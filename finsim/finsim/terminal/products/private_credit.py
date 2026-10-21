"""Private credit (direct loans): commit, nightly coupon + OID accretion, quarterly payments, amendment fees, the computed
mark and repayment (TERMINAL_DESIGN.md §2.12 products/private_credit.py, §5.4, §6.2, §8.6, §11.2 PC_PAY, §11.3, §13.14,
§19.18; TERMINAL_WORKPLAN.md WP-21).

Conventions stated here (each is a §1.3 sentence or a rule the tests pin)
- COMMIT (``pc.commit``): the owner lends ``principal`` to a named borrower from the lifetime's CREDIT account at
  ``issue_price`` (98.00 = 2 % original-issue discount).  Cash out = ``to_c(principal × issue_price / 100)`` leaves settled
  cash at once (``execution.fund(immediate=True)``: own settled USD, else one Treasury SWEEP_IN of exactly the shortfall,
  else 409 INSUFFICIENT_FUNDS) and is posted ``Dr 1130 / Cr 1010`` (idem ``pc:{loan_id}:commit``).  The loan gets the id
  ``PC-000001`` (one sequence for every book), a ``private_loans`` row and an instrument row of the same id (kind PRIVATE
  with ``spec.loan_id`` — the kind the MarkProvider dispatches to ``pc_mark``; the design's "kind BOND" would route the
  loan to the par-curve bond model — support COMPUTED, session group PRIVATE_MARKETS).  ``oas_issue`` is the latest FRED
  ``BAMLH0A0HYM2`` print at commitment (null when the rate store has none; the mark is then the amortised cost).
- CURRENCY / INDEX: this build commits USD loans floating off SOFR only — the coupon resets nightly from the USD policy
  series and the mark's spread input is the US high-yield OAS, which is a USD spread; any other currency is refused
  400 CURRENCY_UNSUPPORTED and any other index 400 INDEX_UNSUPPORTED (nothing is approximated).  ``spread`` is a decimal
  FRACTION ("0.06" = 6.00 %; a trailing "%" is accepted as percent); a fraction above 0.50 is refused SPREAD.
- NIGHTS (ACCRUE_DAILY kinds ``pccpn`` / ``pcoid``, idem ``accr:{kind}:{loan_id}:{D}``): every calendar night from the
  start date (the accounting day of the commit) up to the night BEFORE the maturity date; nights on or after maturity
  are terminal no-ops.  Coupon ``principal × (SOFR_D / 100 + spread) × n / 360`` with cumulative rounding per segment
  (``lifecycle.accrue_segment``: a new SOFR observation restarts the segment at that night, so 1,370.83 / 1,370.84
  alternate instead of drifting) posts ``Dr 1220 / Cr 4300``; SOFR_D is the latest print on or before D (RATE_LAG) and
  must be at most 10 days old, else the night is SKIPPED with a WARN attention and retried (never defaulted, never
  lumped).  OID accretes straight-line over the calendar nights start → maturity, ``(principal − cash_out) × n /
  total_nights`` with the same cumulative rounding (kind ``pcoid``, rate 1, basis = total nights) and posts
  ``Dr 1130 / Cr 4300``, so the 1130 balance is always principal × issue price + the ``pcoid`` segment's ``cum_c`` (L24).
- PAYMENTS (PC_PAY, key ``{loan_id}:{date}``, idem ``pcpay:{loan_id}:{date}``): payment dates are the start date rolled by
  ``payment_months`` (modified following on the NYSE calendar; the anchors stay unadjusted, the last one is the maturity
  date).  A payment is due from 00:40 ET on its date once the coupon has accrued through the night before it, and pays
  exactly the coupon accrued for the nights up to that night (``Dr 1010 / Cr 1220``), never a night after it — so a
  catch-up that accrued past the date still pays the right amount; the row's ``next_payment_date`` rolls to the next
  schedule date.  On the final date the face is repaid in the same entry: ``Dr 1010 principal / Cr 1130 amortised cost /
  Cr(Dr) 4000 difference``; the loan is REPAID.  The handler writes its own ``jobs_done`` row (DONE; SKIPPED with a WARN
  attention when the accrual is not yet complete).
- FEES (``pc.amend_fee``, idem ``pcfee:{loan_id}:{request_id}``): an amendment / arrangement fee the owner RECORDS when
  received, ``Dr 1010 / Cr 4320``; nothing is generated.
- REPAYMENT (``pc.repay {loan_id, amount}``, idem ``pc:{loan_id}:repay:{request_id}``): the amount received pays the
  accrued coupon first (``Cr 1220``), the rest retires the WHOLE amortised cost (``Cr 1130``) and the difference is
  realised on 4000 (a par repayment before full accretion realises the unaccreted OID as a gain; a repayment below the
  amortised cost a loss); the loan is REPAID.  A partial prepayment is not in this build.  Nights not yet accrued before
  the repayment day are accrued first (same idem keys as the job); a missing SOFR for them is 409 ACCRUAL_PENDING.
- MARK (``pc_mark``, method ``pc_model``, quality computed, never executable): ``amortised_cost × (1 − ΔOAS × T)`` with
  ``ΔOAS = (OAS_now − OAS_issue) / 100`` from FRED ``BAMLH0A0HYM2`` (the latest print on or before today) and ``T`` = days
  to maturity / 365, ``to_c`` HALF_EVEN, floored at zero; equals the amortised cost when the spread is unchanged or
  either observation is missing (note "amortised cost (no spread observation)").  The inputs carry both OAS prints with
  their dates, the days remaining and the formula.  ``pc_mark − amortised cost`` is unrealised; coupons and OID are
  income (4300), fees income (4320).
- NEVER: no default, rating, review, covenant or recovery event is generated or simulated; the module registers exactly
  ``pc.commit``, ``pc.amend_fee``, ``pc.repay`` and ``pc.pay``.
- Money is integer cents / Decimal (HALF_EVEN through ``units.to_c``); dates are ISO strings; no floats.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, getcontext
from typing import Any, Dict, List, Optional, Tuple

from .. import execution, lifecycle_jobs
from ..books import account_kind_for, account_row, lifetime_row, split_account_id
from ..clock import epoch_of, ny_of
from ..commands import ConflictError, RejectError, TxContext, require_amount_c
from ..ledger import Entry, Line, balance, cr, dr
from ..marks import add_months
from ..units import fmt_money, from_c, qprice, to_c

getcontext().prec = 34

SOURCE = "private_credit"
JOB = "PC_PAY"
KIND_CPN = "pccpn"
KIND_OID = "pcoid"
OAS_SERIES = "BAMLH0A0HYM2"
ID_PREFIX = "PC-"
SUPPORTED = {"USD": "SOFR"}                 # currency -> the policy index this build floats off (module docstring CURRENCY / INDEX)
RATE_LAG_MAX_DAYS = 10
PAY_FROM_ET = "00:40"
PAYMENT_MONTHS = (1, 2, 3, 4, 6, 12)
MAX_TERM_MONTHS = 360
MAX_SPREAD = Decimal("0.5")
MIN_ISSUE_PRICE = Decimal("1")
PAR = Decimal("100")
ONE_DAY = timedelta(days=1)
DAYS_PER_YEAR = Decimal(365)
HANDLER_KINDS = ("pc.commit", "pc.amend_fee", "pc.repay", "pc.pay")


# ---- small helpers ------------------------------------------------------------------------------------------------------------
def _services(ctx: TxContext):
    if ctx.services is None:
        raise RejectError("NO_SERVICES", "the command context carries no services bundle")
    return ctx.services


def _dec(x: Any, code: str, what: str) -> Decimal:
    if isinstance(x, bool) or isinstance(x, float):
        raise RejectError(code, f"{what} must be a decimal string or an integer, not {type(x).__name__}")
    try:
        d = Decimal(str(x).replace(",", "").strip())
    except (InvalidOperation, ValueError, TypeError) as e:
        raise RejectError(code, f"{what} is not a number: {x!r}") from e
    if not d.is_finite():
        raise RejectError(code, f"{what} is not a finite number: {x!r}")
    return d


def _int(x: Any, code: str, what: str) -> int:
    if isinstance(x, bool):
        raise RejectError(code, f"{what} must be an integer")
    try:
        d = Decimal(str(x).strip())
    except (InvalidOperation, ValueError, TypeError, AttributeError) as e:
        raise RejectError(code, f"{what} is not an integer: {x!r}") from e
    if d != d.to_integral_value():
        raise RejectError(code, f"{what} must be a whole number, got {x!r}")
    return int(d)


def _date(x) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x))


def _pct(fraction) -> str:
    """A decimal fraction -> "9.87 %" (at least 2 decimals, up to 4)."""
    s = f"{(Decimal(fraction) * 100).quantize(Decimal('0.0001')):f}"
    whole, frac = s.split(".")
    frac = frac.rstrip("0")
    if len(frac) < 2:
        frac = (frac + "00")[:2]
    return f"{whole}.{frac} %"


def _plain2(x) -> str:
    """A decimal as plain text with at least two decimals and no trailing zeros beyond them ("3.00", "98.50", "-0.50", "98.125")."""
    d = Decimal(str(x))
    s = f"{d.normalize():f}" if d != d.to_integral_value() else f"{d.quantize(Decimal(1)):f}"
    whole, _dot, frac = s.partition(".")
    return f"{whole}.{(frac + '00')[:2] if len(frac) < 2 else frac}"


def _spread(x: Any) -> Decimal:
    """``"0.06"`` -> 0.06; ``"6.00%"`` -> 0.06; a fraction above MAX_SPREAD (or negative) is refused SPREAD."""
    if x is None or str(x).strip() == "":
        raise RejectError("SPREAD", "spread is required: a decimal fraction such as \"0.06\" for 6.00 % (a trailing % is accepted as percent)")
    s = str(x).strip()
    pct = s.endswith("%")
    d = _dec(s.rstrip("%").strip(), "SPREAD", "spread")
    if pct:
        d = d / PAR
    if d < 0 or d > MAX_SPREAD:
        raise RejectError("SPREAD", f"spread {s!r} is outside 0 .. {_pct(MAX_SPREAD)}: give a decimal fraction (\"0.06\" = 6.00 %) or a percent with a % sign")
    return qprice(d)


def loan_row(conn: sqlite3.Connection, loan_id: str):
    return conn.execute("SELECT * FROM private_loans WHERE loan_id=?", (loan_id,)).fetchone()


def next_loan_id(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT loan_id FROM private_loans WHERE loan_id LIKE ? ORDER BY loan_id DESC LIMIT 1", (ID_PREFIX + "%",)).fetchone()
    n = 1
    if row is not None:
        try:
            n = int(str(row["loan_id"])[len(ID_PREFIX):]) + 1
        except ValueError:
            n = int(conn.execute("SELECT COUNT(*) FROM private_loans").fetchone()[0] or 0) + 1
    return f"{ID_PREFIX}{n:06d}"


def cash_out_c(loan) -> int:
    """``to_c(principal × issue_price / 100)``: what left cash at commitment (5,000,000 × 98 / 100 = 4,900,000.00)."""
    return to_c(from_c(int(loan["principal_c"])) * Decimal(str(loan["issue_price"])) / PAR)


def oid_total_c(loan) -> int:
    return int(loan["principal_c"]) - cash_out_c(loan)


def total_nights(loan) -> int:
    """Calendar nights start → maturity (2026-10-20 → 2029-10-20 = 1,096)."""
    return (_date(loan["maturity_date"]) - _date(loan["start_date"])).days


def coupon_rate(index_pct, spread) -> Decimal:
    """Decimal fraction ``index % / 100 + spread`` (3.87 % + 0.06 -> 0.0987)."""
    return qprice(Decimal(str(index_pct)) / PAR + Decimal(str(spread)))


# ---- the schedule (modified following) -----------------------------------------------------------------------------------------
def modified_following(cal, d: date) -> date:
    """The next business day on or after ``d`` unless that crosses into the next month, in which case the last business
    day before ``d``."""
    adj = cal.roll_following(d)
    if adj.month != d.month or adj.year != d.year:
        adj = cal.roll_back(d)
    return adj


def payment_schedule(cal, start: date, maturity: date, months: int) -> List[Tuple[date, date]]:
    """``[(anchor, payment date)]``: anchors are ``start + n × months`` (unadjusted), the last is the maturity date (a
    stub period when the term is not a multiple); payment dates are modified following."""
    out: List[Tuple[date, date]] = []
    n = 1
    while n <= MAX_TERM_MONTHS + 1:
        anchor = add_months(start, n * int(months))
        if anchor >= maturity:
            out.append((maturity, modified_following(cal, maturity)))
            return out
        out.append((anchor, modified_following(cal, anchor)))
        n += 1
    raise RejectError("TERM", "the payment schedule does not reach the maturity date")


def schedule_of(cal, loan) -> List[Tuple[date, date]]:
    return payment_schedule(cal, _date(loan["start_date"]), _date(loan["maturity_date"]), int(loan["payment_months"]))


def next_payment_after(cal, loan, d: date) -> Optional[date]:
    for _anchor, adj in schedule_of(cal, loan):
        if adj > d:
            return adj
    return None


def final_payment_date(cal, loan) -> date:
    return schedule_of(cal, loan)[-1][1]


# ---- rates ----------------------------------------------------------------------------------------------------------------------
def index_for_night(services, loan, night: date) -> Tuple[date, Decimal]:
    """``(observation date, percent)`` of the loan's index for night ``night`` (RATE_LAG: the latest print on or before the
    night, at most 10 days old).  Raises RejectError RATE_UNAVAILABLE / RATE_STORE_MISSING — a missing rate is never a number."""
    rates = getattr(services, "rates", None)
    series = str(loan["index_name"])
    if rates is None or not hasattr(rates, "as_of_le"):
        raise RejectError("RATE_STORE_MISSING", f"no rate store is bound (services.rates): {series} is unknown and is never defaulted",
                          {"series": series, "night": night.isoformat()})
    got = rates.as_of_le(series, night)
    if got is None or (night - got[0]).days > RATE_LAG_MAX_DAYS:
        raise RejectError("RATE_UNAVAILABLE", f"no {series} observation within {RATE_LAG_MAX_DAYS} days on or before {night.isoformat()}: the night waits for FRED"
                          " (RATE_LAG; never defaulted)", {"series": series, "night": night.isoformat(), "loan_id": loan["loan_id"]})
    return got[0], qprice(got[1])


def _accrue_fn(services):
    lc = getattr(services, "lifecycle", None)
    fn = getattr(lc, "accrue_segment", None)
    return fn if fn is not None else lifecycle_jobs.accrue_segment


# ---- accrual lines (§11.3) --------------------------------------------------------------------------------------------------------
def accrual_lines_pc(ctx: TxContext, loan, d, index, kind: str = "both") -> List[Line]:
    """The night-``d`` lines of one loan: coupon ``Dr 1220 / Cr 4300`` (kind ``pccpn``; ``index`` = ``(obs_date, percent)``
    or a bare percent) and / or OID accretion ``Dr 1130 / Cr 4300`` (kind ``pcoid``; no index needed).  Each call moves the
    kind's ``accrual_state`` segment through ``accrue_segment``; a night that rounds to zero yields no lines.  The caller
    posts the entry under ``accr:{kind}:{loan_id}:{d}``."""
    services = _services(ctx)
    accrue = _accrue_fn(services)
    night = _date(d)
    aid, iid, ccy, loan_id = loan["account_id"], loan["instrument_id"], loan["currency"], loan["loan_id"]
    lines: List[Line] = []
    if kind in (KIND_CPN, "both"):
        if index is None:
            raise RejectError("INDEX_REQUIRED", "the coupon accrual needs the index observation (obs_date, percent)")
        obs_date, pct = (index if isinstance(index, (tuple, list)) else (None, index))
        pct = qprice(Decimal(str(pct)))
        rate = coupon_rate(pct, loan["spread"])
        basis = int(loan["day_basis"])
        amount = int(accrue(ctx, KIND_CPN, loan_id, int(loan["principal_c"]), rate, basis, night))
        if amount:
            memo = (f"{loan['index_name']} {pct.normalize():f} %" + (f" ({_date(obs_date).isoformat()})" if obs_date else "")
                    + f" + {_pct(loan['spread'])} = {_pct(rate)}, ACT/{basis} on {fmt_money(int(loan['principal_c']))}, night of {night.isoformat()} (ACCRUAL, RATE_LAG)")
            lines += [dr(aid, "1220", ccy, amount, instrument_id=iid, memo=memo), cr(aid, "4300", ccy, amount, instrument_id=iid, memo=memo)]
    if kind in (KIND_OID, "both"):
        nights = total_nights(loan)
        if nights <= 0:
            raise RejectError("TERM", f"loan {loan_id} has no accrual nights (start {loan['start_date']}, maturity {loan['maturity_date']})")
        amount = int(accrue(ctx, KIND_OID, loan_id, oid_total_c(loan), Decimal(1), nights, night))
        if amount:
            n = (night - _date(loan["start_date"])).days + 1
            memo = (f"OID {fmt_money(oid_total_c(loan))} straight-line over {nights:,} nights ({loan['start_date']} to {loan['maturity_date']}),"
                    f" night {n:,} of {nights:,} ({night.isoformat()})")
            lines += [dr(aid, "1130", ccy, amount, instrument_id=iid, memo=memo), cr(aid, "4300", ccy, amount, instrument_id=iid, memo=memo)]
    if kind not in (KIND_CPN, KIND_OID, "both"):
        raise RejectError("KIND_UNKNOWN", f"accrual kind {kind!r} is not pccpn / pcoid")
    return lines


def accrue_night(ctx: TxContext, services, kind: str, ref: str, night) -> dict:
    """WP-08's ACCRUE_DAILY contract for kinds ``pccpn`` / ``pcoid`` (``services.private_credit.accrue_night``): one
    night of one loan as one entry (idem ``accr:{kind}:{loan_id}:{night}``).  Returns the job body (``status`` DONE; a
    missing SOFR raises RejectError RATE_UNAVAILABLE, which the lifecycle handler records as SKIPPED + WARN)."""
    if kind not in (KIND_CPN, KIND_OID):
        raise RejectError("KIND_UNKNOWN", f"accrual kind {kind!r} is not pccpn / pcoid")
    loan = loan_row(ctx.conn, ref)
    if loan is None:
        raise RejectError("NOT_FOUND", f"private loan {ref} does not exist")
    night_d = _date(night)
    ds = night_d.isoformat()
    loan_id, aid, iid = loan["loan_id"], loan["account_id"], loan["instrument_id"]
    idem = f"accr:{kind}:{loan_id}:{ds}"
    base = {"status": "DONE", "loan_id": loan_id, "kind": kind, "night": ds, "amount_c": 0, "posted_c": 0, "entry_id": None, "posted": False}
    if loan["status"] != "OPEN":
        return dict(base, note=f"loan {loan_id} is {loan['status']}: nothing accrues")
    if night_d >= _date(loan["maturity_date"]):
        return dict(base, note=f"night of {ds} is on or after the maturity date {loan['maturity_date']}: nothing accrues")
    if night_d < _date(loan["start_date"]):
        return dict(base, note=f"night of {ds} is before the start date {loan['start_date']}: nothing accrues")
    if ds >= ctx.today:
        raise RejectError("NIGHT_NOT_ENDED", f"the night of {ds} has not ended (accounting day {ctx.today}); accruals post from 00:30 the next day")
    existing = ctx.conn.execute("SELECT entry_id FROM journal_entries WHERE idem_key=?", (idem,)).fetchone()
    if existing is not None:
        return dict(base, entry_id=int(existing["entry_id"]), note=f"{idem} already posted")
    index = None
    if kind == KIND_CPN:
        index = index_for_night(services, loan, night_d)
    lines = accrual_lines_pc(ctx, loan, night_d, index, kind)
    st = lifecycle_jobs.accrual_state_row(ctx.conn, kind, loan_id)
    out = dict(base, cum_c=int(st["cum_c"]) if st else 0, segment_start=st["segment_start"] if st else None, rate=str(st["rate"]) if st else None)
    if index is not None:
        out["index"] = {"series": loan["index_name"], "value": str(index[1]), "obs_date": index[0].isoformat()}
    if not lines:
        out["note"] = f"{kind} {loan_id}: 0.00 for the night of {ds}"
        return out
    amount = lines[0].amount_c
    what = "coupon" if kind == KIND_CPN else "OID accretion"
    entry_id = ctx.post(Entry("ACCRUAL", f"private credit {what} {loan_id} ({loan['borrower']}): {fmt_money(amount)} — {lines[0].memo}", lines, idem,
                              ref_type="private_loan", ref_id=loan_id))
    ctx.event("ACCRUED", account_id=aid, instrument_id=iid, ref_type="private_loan", ref_id=loan_id, kind=kind, night=ds, amount_c=amount, entry_id=entry_id,
              index=out.get("index"), cum_c=out["cum_c"])
    out.update(amount_c=amount, posted_c=amount, entry_id=entry_id, posted=True, note=f"{what} {fmt_money(amount)} night of {ds}")
    return out


def _catch_up_accruals(ctx: TxContext, services, loan) -> List[dict]:
    """Nights before today (and before maturity) not yet accrued, posted with the job's own idem keys (module docstring
    REPAYMENT).  A missing rate becomes 409 ACCRUAL_PENDING."""
    today = _date(ctx.today)
    last = min(today - ONE_DAY, _date(loan["maturity_date"]) - ONE_DAY)
    done: List[dict] = []
    for kind in (KIND_CPN, KIND_OID):
        for night in lifecycle_jobs.nights_from_state(ctx.conn, kind, loan["loan_id"], _date(loan["start_date"]), last):
            try:
                done.append(accrue_night(ctx, services, kind, loan["loan_id"], night))
            except RejectError as e:
                raise ConflictError("ACCRUAL_PENDING", f"the {kind} accrual for the night of {night.isoformat()} is pending ({e.code}: {e.message}); repay after it posts",
                                    dict(e.details, kind=kind, night=night.isoformat())) from e
    return done


# ---- balances ---------------------------------------------------------------------------------------------------------------------
def amortised_cost_c(conn: sqlite3.Connection, loan) -> int:
    return balance(conn, loan["account_id"], "1130", loan["currency"], loan["instrument_id"])


def accrued_c(conn: sqlite3.Connection, loan) -> int:
    return balance(conn, loan["account_id"], "1220", loan["currency"], loan["instrument_id"])


def coupon_accrued_through_c(conn: sqlite3.Connection, loan, through: date) -> int:
    """Σ coupon accruals for the nights ≤ ``through`` (the ``accr:pccpn:{loan}:{night}`` entries), by idem key."""
    row = conn.execute("SELECT COALESCE(SUM(l.amount_c), 0) FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id"
                       " WHERE l.account_id=? AND l.gl='1220' AND l.instrument_id=? AND e.idem_key LIKE ? AND substr(e.idem_key, -10) <= ?",
                       (loan["account_id"], loan["instrument_id"], f"accr:{KIND_CPN}:{loan['loan_id']}:%", through.isoformat())).fetchone()
    return int(row[0] or 0)


def coupon_paid_c(conn: sqlite3.Connection, loan) -> int:
    """Σ coupon payments so far (the 1220 credits of the ``pcpay:{loan}:{date}`` entries)."""
    row = conn.execute("SELECT COALESCE(SUM(-l.amount_c), 0) FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id"
                       " WHERE l.account_id=? AND l.gl='1220' AND l.instrument_id=? AND e.idem_key LIKE ?",
                       (loan["account_id"], loan["instrument_id"], f"pcpay:{loan['loan_id']}:%")).fetchone()
    return int(row[0] or 0)


def coupon_due_c(conn: sqlite3.Connection, loan, through: date) -> int:
    """The coupon a payment on the day after ``through`` pays: accrued for the nights ≤ ``through`` minus paid so far."""
    return coupon_accrued_through_c(conn, loan, through) - coupon_paid_c(conn, loan)


# ---- the mark (§8.6 pc_model) ---------------------------------------------------------------------------------------------------------
def pc_mark(services, loan, conn: Optional[sqlite3.Connection] = None, now: Optional[int] = None) -> Tuple[int, dict]:
    """``(mark_c, inputs)`` — module docstring MARK.  ``loan`` is a ``private_loans`` row (sqlite3.Row or dict).  Reads the
    1130 balance on ``conn`` (a handler's transaction) or a fresh reader."""
    now_ts = int(now if now is not None else services.clock.epoch())
    if conn is not None:
        cost = amortised_cost_c(conn, loan)
    else:
        with services.db.reader() as c:
            cost = amortised_cost_c(c, loan)
    today = ny_of(now_ts).date()
    maturity = _date(loan["maturity_date"])
    days = max(0, (maturity - today).days)
    t_years = Decimal(days) / DAYS_PER_YEAR
    master = getattr(services, "instruments", None)
    inst = master.get(loan["instrument_id"], conn) if master is not None else None
    spec = dict(getattr(inst, "spec", None) or {})
    rates = getattr(services, "rates", None)
    now_obs = rates.as_of_le(OAS_SERIES, today) if rates is not None and hasattr(rates, "as_of_le") else None
    issue_pct = None if loan["oas_issue"] in (None, "") else Decimal(str(loan["oas_issue"]))
    inputs: Dict[str, Any] = {"method": "pc_model", "series": OAS_SERIES, "formula": "amortised_cost x (1 - dOAS x T), T = days remaining / 365",
                              "amortised_cost_c": cost, "days_remaining": days, "t_years": str(t_years.quantize(Decimal("0.0000001"))), "as_of": now_ts,
                              "oas_issue_pct": None if issue_pct is None else _plain2(issue_pct), "oas_issue_as_of": spec.get("oas_issue_as_of"),
                              "oas_now_pct": None if now_obs is None else _plain2(now_obs[1]), "oas_now_as_of": None if now_obs is None else now_obs[0].isoformat(),
                              "delta_oas_pct": None, "qualities": ["computed"], "status": loan["status"]}
    if loan["status"] != "OPEN":
        inputs["note"] = f"loan {loan['status']}: nothing to mark"
        return 0, inputs
    if issue_pct is None or now_obs is None:
        inputs["note"] = "amortised cost (no spread observation)"
        return cost, inputs
    delta_pct = (Decimal(str(now_obs[1])) - issue_pct)
    inputs["delta_oas_pct"] = _plain2(delta_pct)
    if delta_pct == 0:
        inputs["note"] = f"spread unchanged ({OAS_SERIES} {_plain2(issue_pct)} % at issue and {now_obs[0].isoformat()})"
        return cost, inputs
    mark = to_c(from_c(cost) * (Decimal(1) - delta_pct / PAR * t_years))
    sign = "+" if delta_pct > 0 else ""
    note = (f"{OAS_SERIES} {_plain2(issue_pct)} % at issue -> {_plain2(now_obs[1])} % ({now_obs[0].isoformat()}): {sign}{_plain2(delta_pct)} %,"
            f" {days:,} days remaining")
    if mark < 0:
        mark, note = 0, note + "; first-order adjustment floored at zero"
    inputs["note"] = note
    return mark, inputs


def _mark_dict(services, loan, conn=None, now=None) -> dict:
    mark_c, inputs = pc_mark(services, loan, conn, now)
    return {"instrument_id": loan["instrument_id"], "value": str(from_c(mark_c)), "mark_c": mark_c, "quality": "computed", "source": "private credit model",
            "method": "pc_model", "as_of": inputs.get("as_of"), "delay_label": "model", "inputs": inputs, "executable": False, "note": inputs.get("note") or ""}


# ---- views ----------------------------------------------------------------------------------------------------------------------------
def _index_now(services, loan, today: date) -> Optional[Tuple[date, Decimal]]:
    rates = getattr(services, "rates", None)
    if rates is None or not hasattr(rates, "as_of_le"):
        return None
    got = rates.as_of_le(str(loan["index_name"]), today)
    return None if got is None else (got[0], qprice(got[1]))


def projected_schedule(services, loan, conn: Optional[sqlite3.Connection] = None, today: Optional[date] = None) -> List[dict]:
    """The remaining payment dates with the coupon PROJECTED at the latest index print (labelled; the actual coupon
    accrues nightly from each night's print).  Dates only when no print is loaded."""
    cal = services.cal
    today = today or ny_of(int(services.clock.epoch())).date()
    obs = _index_now(services, loan, today)
    rate = None if obs is None else coupon_rate(obs[1], loan["spread"])
    out: List[dict] = []
    prev = _date(loan["start_date"])
    for anchor, adj in schedule_of(cal, loan):
        nights = (adj - prev).days
        row = {"date": adj.isoformat(), "anchor": anchor.isoformat(), "nights": nights, "final": adj == final_payment_date(cal, loan),
               "projected_coupon_c": None if rate is None else to_c(from_c(int(loan["principal_c"])) * rate * Decimal(nights) / Decimal(int(loan["day_basis"]))),
               "basis": None if obs is None else f"projected at {loan['index_name']} {obs[1].normalize():f} % ({obs[0].isoformat()}) + {_pct(loan['spread'])}; the coupon floats nightly"}
        if loan["status"] == "OPEN" and adj >= _date(loan["next_payment_date"]):
            out.append(row)                                           # the remaining dates of an open loan; a repaid loan has none
        prev = adj
    return out


def loan_view(services, loan, conn: Optional[sqlite3.Connection] = None, now: Optional[int] = None) -> dict:
    """``PrivateLoanView`` (§15 GET /api/credit/private): the row, the ledger balances (amortised cost, accrued, paid,
    fees, income), the accrual state, the mark with its inputs and the projected schedule.  Cents are ints (the API
    codec renders decimal strings)."""
    def body(c: sqlite3.Connection) -> dict:
        now_ts = int(now if now is not None else services.clock.epoch())
        today = ny_of(now_ts).date()
        st_cpn = lifecycle_jobs.accrual_state_row(c, KIND_CPN, loan["loan_id"])
        st_oid = lifecycle_jobs.accrual_state_row(c, KIND_OID, loan["loan_id"])
        obs = _index_now(services, loan, today)
        cost = amortised_cost_c(c, loan)
        mark = _mark_dict(services, loan, c, now_ts)
        lid, _role = split_account_id(loan["account_id"])
        inst = services.instruments.get(loan["instrument_id"], c) if getattr(services, "instruments", None) is not None else None
        spec = dict(getattr(inst, "spec", None) or {})
        maturity = _date(loan["maturity_date"])
        return {"loan_id": loan["loan_id"], "account_id": loan["account_id"], "lifetime_id": lid, "instrument_id": loan["instrument_id"], "company_id": loan["company_id"],
                "borrower": loan["borrower"], "currency": loan["currency"], "principal_c": int(loan["principal_c"]), "issue_price": str(loan["issue_price"]),
                "cash_out_c": cash_out_c(loan), "oid_total_c": oid_total_c(loan), "index": loan["index_name"], "spread": str(loan["spread"]),
                "index_now": None if obs is None else {"series": loan["index_name"], "value": str(obs[1]), "as_of": obs[0].isoformat()},
                "coupon_rate": None if obs is None else str(coupon_rate(obs[1], loan["spread"])), "coupon_rate_text": None if obs is None else _pct(coupon_rate(obs[1], loan["spread"])),
                "day_basis": int(loan["day_basis"]), "payment_months": int(loan["payment_months"]), "start_date": loan["start_date"], "maturity_date": loan["maturity_date"],
                "next_payment_date": loan["next_payment_date"], "status": loan["status"], "closed_ts": loan["closed_ts"], "nights_total": total_nights(loan),
                "nights_accrued": 0 if st_oid is None or not st_oid["accrued_through"] else (_date(st_oid["accrued_through"]) - _date(loan["start_date"])).days + 1,
                "accrued_through": None if st_cpn is None else st_cpn["accrued_through"], "segment_start": None if st_cpn is None else st_cpn["segment_start"],
                "segment_rate": None if st_cpn is None else str(st_cpn["rate"]), "oid_accreted_c": 0 if st_oid is None else int(st_oid["cum_c"]),
                "amortised_cost_c": cost, "accrued_c": accrued_c(c, loan), "coupon_paid_c": coupon_paid_c(c, loan),
                "fees_c": balance(c, loan["account_id"], "4320", loan["currency"], loan["instrument_id"]),
                "income_c": balance(c, loan["account_id"], "4300", loan["currency"], loan["instrument_id"]),
                "realised_c": balance(c, loan["account_id"], "4000", loan["currency"], loan["instrument_id"]),
                "mark_c": mark["mark_c"], "unrealised_c": (mark["mark_c"] - cost) if loan["status"] == "OPEN" else 0, "mark": mark,
                "days_remaining": max(0, (maturity - today).days), "oas_issue": loan["oas_issue"], "oas_issue_as_of": spec.get("oas_issue_as_of"),
                "schedule": projected_schedule(services, loan, c, today), "convention": "PRIVATE_CREDIT"}
    if conn is not None:
        return body(conn)
    with services.db.reader() as c:
        return body(c)


def _accounts_of_scope(conn: sqlite3.Connection, scope: Optional[str]) -> Optional[List[str]]:
    """None = every account; else the account ids of ``{lifetime}:ALL`` or the single account id."""
    s = (scope or "ALL").strip()
    if s in ("ALL", "ALL_BOOKS", ""):
        return None
    if s.endswith(":ALL"):
        lid = s[:-4]
        return [r["account_id"] for r in conn.execute("SELECT account_id FROM accounts WHERE lifetime_id=?", (lid,)).fetchall()]
    return [s]


def loans_of(conn: sqlite3.Connection, scope: Optional[str] = None, status: Optional[str] = None) -> List[sqlite3.Row]:
    accts = _accounts_of_scope(conn, scope)
    sql, params = "SELECT * FROM private_loans WHERE 1=1", []
    if accts is not None:
        if not accts:
            return []
        sql += f" AND account_id IN ({','.join('?' for _ in accts)})"
        params += accts
    if status:
        sql += " AND status=?"
        params.append(status)
    return conn.execute(sql + " ORDER BY loan_id", params).fetchall()


def loans_view(services, scope: Optional[str] = "ALL") -> List[dict]:
    """§15 ``GET /api/credit/private?scope=`` -> ``[PrivateLoanView]`` (OPEN first, then REPAID; account / lifetime / all)."""
    with services.db.reader() as conn:
        rows = loans_of(conn, scope)
        views = [loan_view(services, r, conn) for r in rows]
    views.sort(key=lambda v: (0 if v["status"] == "OPEN" else 1, v["loan_id"]))
    return views


# ---- PC_PAY keys (WP-08 ProductJob contract) ------------------------------------------------------------------------------------------
def pay_keys(conn: sqlite3.Connection, now_ts: int) -> List[Tuple[str, str]]:
    """``[(loan_id, date)]`` due now: OPEN loans of OPEN lifetimes whose ``next_payment_date`` has come (from 00:40 ET on
    that date) and whose coupon has accrued through the night before it (``min(date − 1, maturity − 1)``), excluding
    keys already DONE.  A SKIPPED key comes back."""
    today = ny_of(int(now_ts)).date()
    rows = conn.execute("SELECT l.* FROM private_loans l JOIN accounts a ON a.account_id=l.account_id JOIN lifetimes t ON t.lifetime_id=a.lifetime_id"
                        " WHERE l.status='OPEN' AND a.status='OPEN' AND t.status='OPEN' ORDER BY l.loan_id").fetchall()
    out: List[Tuple[str, str]] = []
    for r in rows:
        d = _date(r["next_payment_date"])
        if d > today or (d == today and int(now_ts) < epoch_of(d, PAY_FROM_ET)):
            continue
        need = min(d - ONE_DAY, _date(r["maturity_date"]) - ONE_DAY)
        st = lifecycle_jobs.accrual_state_row(conn, KIND_CPN, r["loan_id"])
        if need >= _date(r["start_date"]) and (st is None or not st["accrued_through"] or _date(st["accrued_through"]) < need):
            continue                                                  # the coupon through the night before the date comes first
        key = f"{r['loan_id']}:{d.isoformat()}"
        done = lifecycle_jobs.job_row(conn, JOB, key)
        if done is not None and done["status"] == "DONE":
            continue
        out.append((r["loan_id"], d.isoformat()))
    return out


def _skip(ctx: TxContext, key: str, code: str, message: str, loan, details: Optional[dict] = None) -> dict:
    """A PENDING outcome of PC_PAY: jobs_done SKIPPED (the key stays due), one open WARN attention per (code, key), an event."""
    lifecycle_jobs.write_job_done(ctx, JOB, key, "SKIPPED", f"{code}: {message}")
    existing = ctx.conn.execute("SELECT id FROM attention WHERE source=? AND code=? AND ref_id=? AND resolved_ts IS NULL", (SOURCE, code, key)).fetchone()
    attention_id = int(existing["id"]) if existing else ctx.attention("WARN", SOURCE, code, f"{JOB} {key}: {message}; retried",
                                                                      dict(details or {}, account_id=loan["account_id"], ref_type="private_loan", ref_id=key, auto_resolve=1))
    ctx.event("JOB_SKIPPED", account_id=loan["account_id"], instrument_id=loan["instrument_id"], ref_type="job", ref_id=f"{JOB}:{key}", job=JOB, key=key, code=code, message=message)
    return {"status": "SKIPPED", "skipped": True, "retry": True, "code": code, "message": message, "note": f"{code}: {message}", "key": key, "attention_id": attention_id,
            "surfaced": True}


def _done_noop(ctx: TxContext, key: str, code: str, note: str) -> dict:
    lifecycle_jobs.write_job_done(ctx, JOB, key, "DONE", f"{code}: {note}")
    return {"status": "DONE", "skipped": True, "posted": False, "code": code, "note": note, "key": key}


def _resolve_skips(ctx: TxContext, key: str) -> None:
    ctx.conn.execute("UPDATE attention SET resolved_ts=? WHERE source=? AND ref_id=? AND resolved_ts IS NULL", (ctx.ts, SOURCE, key))


# ---- handlers --------------------------------------------------------------------------------------------------------------------------
def _open_loan(conn: sqlite3.Connection, payload: dict):
    loan_id = str(payload.get("loan_id") or "").strip()
    if not loan_id:
        raise RejectError("LOAN_REQUIRED", "loan_id is required")
    loan = loan_row(conn, loan_id)
    if loan is None:
        raise RejectError("NOT_FOUND", f"private loan {loan_id} does not exist")
    if loan["status"] != "OPEN":
        raise ConflictError("LOAN_CLOSED", f"private loan {loan_id} is {loan['status']}")
    return loan


def h_commit(ctx: TxContext, payload: dict) -> dict:
    """``pc.commit {account_id, borrower | company_id, currency?, principal_c | principal, issue_price, index?, spread, term_months,
    payment_months?}`` — module docstring COMMIT."""
    services = _services(ctx)
    conn = ctx.conn
    aid = str(payload.get("account_id") or "").strip()
    if not aid:
        raise RejectError("ACCOUNT_REQUIRED", "account_id (the lifetime's CREDIT account) is required")
    acct = account_row(conn, aid)
    if acct is None:
        raise RejectError("ACCOUNT_UNKNOWN", f"account {aid} does not exist")
    if acct["status"] != "OPEN":
        raise ConflictError("ACCOUNT_CLOSED", f"account {aid} is {acct['status']}")
    if account_kind_for(acct) != "CREDIT":
        raise RejectError("ACCOUNT_KIND", f"{aid} is a {account_kind_for(acct)} account: private loans are committed from the lifetime's CREDIT account")
    lid, _role = split_account_id(aid)
    lrow = lifetime_row(conn, lid)
    if lrow is None or lrow["status"] != "OPEN":
        raise ConflictError("LIFETIME_CLOSED", f"lifetime {lid} is not open")
    acc = getattr(services, "accounting", None)
    if acc is not None and hasattr(acc, "is_held") and acc.is_held(lid):
        raise ConflictError("INTEGRITY_HOLD", f"lifetime {lid} is under an integrity hold; no new commitments until it clears")
    currency = str(payload.get("currency") or "USD").strip().upper()
    if currency not in SUPPORTED:
        raise RejectError("CURRENCY_UNSUPPORTED", f"{currency}: private loans float off SOFR and are marked with the US high-yield OAS in this build; "
                          f"supported: {', '.join(sorted(SUPPORTED))}", {"currency": currency, "supported": sorted(SUPPORTED)})
    index = str(payload.get("index") or SUPPORTED[currency]).strip().upper()
    if index != SUPPORTED[currency]:
        raise RejectError("INDEX_UNSUPPORTED", f"index {index}: a {currency} loan floats off {SUPPORTED[currency]} (the policy series) in this build",
                          {"index": index, "expected": SUPPORTED[currency]})
    principal_c = require_amount_c(payload, "principal_c", "principal")
    issue_price = _dec(payload.get("issue_price") if payload.get("issue_price") not in (None, "") else "100", "ISSUE_PRICE", "issue_price").quantize(Decimal("0.0001"))
    if issue_price < MIN_ISSUE_PRICE or issue_price > PAR:
        raise RejectError("ISSUE_PRICE", f"issue_price {issue_price} must be between {MIN_ISSUE_PRICE} and {PAR} (98.00 = 2 % OID; premiums are not in this build)")
    spread = _spread(payload.get("spread"))
    term_months = _int(payload.get("term_months"), "TERM", "term_months")
    if term_months < 1 or term_months > MAX_TERM_MONTHS:
        raise RejectError("TERM", f"term_months {term_months} must be 1 .. {MAX_TERM_MONTHS}")
    payment_months = _int(payload.get("payment_months") if payload.get("payment_months") not in (None, "") else 3, "PAYMENT_MONTHS", "payment_months")
    if payment_months not in PAYMENT_MONTHS or payment_months > term_months:
        raise RejectError("PAYMENT_MONTHS", f"payment_months {payment_months} must be one of {PAYMENT_MONTHS} and at most the term")
    company_id = payload.get("company_id")
    borrower = str(payload.get("borrower") or "").strip()
    if company_id:
        crow = conn.execute("SELECT company_id, name FROM private_companies WHERE company_id=?", (str(company_id).strip(),)).fetchone()
        if crow is None:
            raise RejectError("COMPANY_UNKNOWN", f"private company {company_id} is not in the master; give borrower as free text instead")
        company_id = crow["company_id"]
        borrower = borrower or str(crow["name"])
    if not borrower:
        raise RejectError("BORROWER_REQUIRED", "borrower (a name) or company_id is required")
    if len(borrower) > 120:
        raise RejectError("BORROWER_REQUIRED", "borrower is too long (120 characters at most)")
    start = _date(ctx.today)
    maturity = add_months(start, term_months)
    schedule = payment_schedule(ctx.cal, start, maturity, payment_months)
    next_payment = schedule[0][1]
    loan_id = next_loan_id(conn)
    iid = loan_id
    master = getattr(services, "instruments", None)
    if master is None:
        raise RejectError("NO_SERVICES", "services.instruments is not bound")
    if master.get(iid, conn) is not None:
        raise ConflictError("INSTRUMENT_EXISTS", f"instrument {iid} already exists")
    cash_out = to_c(from_c(principal_c) * issue_price / PAR)
    if cash_out <= 0:
        raise RejectError("AMOUNT", "the cash out rounds to zero")
    rates = getattr(services, "rates", None)
    oas = rates.latest(OAS_SERIES) if rates is not None and hasattr(rates, "latest") else None
    oas_issue = None if oas is None else _plain2(oas[1])
    oas_as_of = None if oas is None else oas[0].isoformat()
    funding = execution.fund(ctx, aid, currency, cash_out, "private_loan", loan_id, None, immediate=True, services=services)
    master._insert(conn, ctx.ts, instrument_id=iid, kind="PRIVATE", symbol=iid, name=f"{borrower} direct loan {maturity.isoformat()}", currency=currency,
                   session_group="PRIVATE_MARKETS", settle_days=0, account_kinds=["CREDIT"],
                   spec={"loan_id": loan_id, "borrower": borrower, "company_id": company_id, "maturity": maturity.isoformat(), "oas_issue_as_of": oas_as_of,
                         "oas_series": OAS_SERIES, "product": "private_credit"}, support="COMPUTED")
    memo = (f"private credit commit {loan_id}: {borrower}, {currency} {fmt_money(principal_c)} at {_plain2(issue_price)} ({_pct((PAR - issue_price) / PAR)} OID),"
            f" {index} + {_pct(spread)}, {term_months} months to {maturity.isoformat()}, paid every {payment_months} months; cash out {fmt_money(cash_out)}"
            f" (PRIVATE_CREDIT)")
    entry_id = ctx.post(Entry("PRIVATE_LOAN", memo, [dr(aid, "1130", currency, cash_out, instrument_id=iid, memo="loan at amortised cost (principal x issue price)"),
                                                     cr(aid, "1010", currency, cash_out, memo=f"cash out to {borrower}")],
                              f"pc:{loan_id}:commit", ref_type="private_loan", ref_id=loan_id))
    conn.execute("INSERT INTO private_loans(loan_id, command_id, account_id, instrument_id, company_id, borrower, currency, principal_c, issue_price, index_name, spread,"
                 " day_basis, payment_months, start_date, maturity_date, next_payment_date, oas_issue, status, closed_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'OPEN',NULL)",
                 (loan_id, ctx.command_id, aid, iid, company_id, borrower, currency, principal_c, _plain2(issue_price), index, str(spread), 360, payment_months,
                  start.isoformat(), maturity.isoformat(), next_payment.isoformat(), oas_issue))
    loan = loan_row(conn, loan_id)
    ctx.event("PC_COMMITTED", account_id=aid, instrument_id=iid, ref_type="private_loan", ref_id=loan_id, borrower=borrower, principal_c=principal_c,
              issue_price=_plain2(issue_price), cash_out_c=cash_out, index=index, spread=str(spread), term_months=term_months, payment_months=payment_months,
              maturity_date=maturity.isoformat(), next_payment_date=next_payment.isoformat(), oas_issue=oas_issue, entry_id=entry_id, funding=funding.to_dict())
    view = loan_view(services, loan, conn, ctx.ts)
    return {"loan_id": loan_id, "loan": view, "mark": view["mark"], "schedule": view["schedule"], "cash_out_c": cash_out, "entry_id": entry_id, "entries": [entry_id],
            "funding": funding.to_dict(), "convention": "PRIVATE_CREDIT"}


def h_amend_fee(ctx: TxContext, payload: dict) -> dict:
    """``pc.amend_fee {loan_id, amount_c | amount, memo?}`` — module docstring FEES."""
    services = _services(ctx)
    loan = _open_loan(ctx.conn, payload)
    amount = require_amount_c(payload)
    memo = str(payload.get("memo") or "amendment fee").strip()[:200] or "amendment fee"
    aid, iid, ccy, loan_id = loan["account_id"], loan["instrument_id"], loan["currency"], loan["loan_id"]
    text = f"private credit fee {loan_id} ({loan['borrower']}): {ccy} {fmt_money(amount)} — {memo} (owner-recorded)"
    entry_id = ctx.post(Entry("PRIVATE_LOAN", text, [dr(aid, "1010", ccy, amount, memo=memo), cr(aid, "4320", ccy, amount, instrument_id=iid, memo=memo)],
                              f"pcfee:{loan_id}:{ctx.request_id}", ref_type="private_loan", ref_id=loan_id))
    ctx.event("PC_FEE", account_id=aid, instrument_id=iid, ref_type="private_loan", ref_id=loan_id, amount_c=amount, memo=memo, entry_id=entry_id)
    return {"loan_id": loan_id, "loan": loan_view(services, loan, ctx.conn, ctx.ts), "entries": [entry_id], "entry_id": entry_id, "amount_c": amount, "memo": memo}


def _close_loan(ctx: TxContext, loan) -> None:
    ctx.conn.execute("UPDATE private_loans SET status='REPAID', closed_ts=? WHERE loan_id=?", (ctx.ts, loan["loan_id"]))


def _principal_lines(loan, received_c: int, cost_c: int, memo: str) -> Tuple[List[Line], int]:
    """``Dr 1010 received / Cr 1130 amortised cost / Cr(Dr) 4000 difference`` (zero lines omitted); returns the realised cents."""
    aid, iid, ccy = loan["account_id"], loan["instrument_id"], loan["currency"]
    lines: List[Line] = []
    if received_c:
        lines.append(dr(aid, "1010", ccy, received_c, memo=memo))
    if cost_c:
        lines.append(cr(aid, "1130", ccy, cost_c, instrument_id=iid, memo="amortised cost retired"))
    realised = received_c - cost_c
    if realised > 0:
        lines.append(cr(aid, "4000", ccy, realised, instrument_id=iid, memo="received above amortised cost (unaccreted OID / premium realised)"))
    elif realised < 0:
        lines.append(dr(aid, "4000", ccy, -realised, instrument_id=iid, memo="received below amortised cost (loss realised)"))
    return lines, realised


def h_repay(ctx: TxContext, payload: dict) -> dict:
    """``pc.repay {loan_id, amount_c | amount}`` — module docstring REPAYMENT."""
    services = _services(ctx)
    conn = ctx.conn
    loan = _open_loan(conn, payload)
    amount = require_amount_c(payload)
    caught_up = _catch_up_accruals(ctx, services, loan)
    aid, iid, ccy, loan_id = loan["account_id"], loan["instrument_id"], loan["currency"], loan["loan_id"]
    accrued = accrued_c(conn, loan)
    if amount < accrued:
        raise RejectError("AMOUNT_BELOW_ACCRUED", f"{fmt_money(amount)} is below the accrued coupon {fmt_money(accrued)}: a repayment pays the accrued coupon first and retires "
                          "the whole loan (a partial prepayment is not in this build)", {"accrued_c": accrued, "amortised_cost_c": amortised_cost_c(conn, loan)})
    cost = amortised_cost_c(conn, loan)
    principal_part = amount - accrued
    lines: List[Line] = []
    if accrued:
        lines += [dr(aid, "1010", ccy, accrued, memo="accrued coupon received"), cr(aid, "1220", ccy, accrued, instrument_id=iid, memo="accrued coupon paid")]
    plines, realised = _principal_lines(loan, principal_part, cost, f"repayment of {loan_id} received")
    lines += plines
    memo = (f"private credit repayment {loan_id} ({loan['borrower']}): received {fmt_money(amount)} = accrued coupon {fmt_money(accrued)} + {fmt_money(principal_part)} against"
            f" amortised cost {fmt_money(cost)} (face {fmt_money(int(loan['principal_c']))}); realised {fmt_money(realised)}")
    entry_id = ctx.post(Entry("PRIVATE_LOAN", memo, lines, f"pc:{loan_id}:repay:{ctx.request_id}", ref_type="private_loan", ref_id=loan_id)) if lines else None
    _close_loan(ctx, loan)
    _resolve_skips(ctx, f"{loan_id}:{loan['next_payment_date']}")
    ctx.event("PC_REPAID", account_id=aid, instrument_id=iid, ref_type="private_loan", ref_id=loan_id, amount_c=amount, accrued_paid_c=accrued, principal_received_c=principal_part,
              amortised_cost_c=cost, realised_c=realised, entry_id=entry_id, nights_caught_up=len([x for x in caught_up if x.get("posted")]))
    loan = loan_row(conn, loan_id)
    return {"loan_id": loan_id, "loan": loan_view(services, loan, conn, ctx.ts), "entries": [] if entry_id is None else [entry_id], "entry_id": entry_id,
            "accrued_paid_c": accrued, "principal_received_c": principal_part, "amortised_cost_c": cost, "realised_c": realised, "status": "REPAID"}


def h_pay(ctx: TxContext, payload: dict) -> dict:
    """``pc.pay {loan_id, date, key?}`` (the PC_PAY job) — module docstring PAYMENTS.  Writes its own ``jobs_done`` row."""
    services = _services(ctx)
    conn = ctx.conn
    loan_id = str(payload.get("loan_id") or "").strip()
    d = _date(str(payload.get("date") or "")) if payload.get("date") else None
    if not loan_id or d is None:
        raise RejectError("KEY_REQUIRED", "pc.pay needs loan_id and date")
    key = str(payload.get("key") or f"{loan_id}:{d.isoformat()}")
    existing = lifecycle_jobs.job_row(conn, JOB, key)
    if existing is not None and existing["status"] == "DONE":
        return {"status": "DONE", "key": key, "note": "already done: " + (existing["note"] or ""), "posted": False}
    loan = loan_row(conn, loan_id)
    if loan is None:
        raise RejectError("NOT_FOUND", f"private loan {loan_id} does not exist")
    if loan["status"] != "OPEN":
        return _done_noop(ctx, key, "LOAN_CLOSED", f"loan {loan_id} is {loan['status']}: nothing to pay")
    nxt = _date(loan["next_payment_date"])
    if d < nxt:
        return _done_noop(ctx, key, "SUPERSEDED", f"{d.isoformat()} is before the next payment date {nxt.isoformat()} (already paid or rolled)")
    if d > nxt:
        return _skip(ctx, key, "PAYMENT_ORDER", f"the payment of {nxt.isoformat()} comes first", loan, {"next_payment_date": nxt.isoformat()})
    if ctx.ts < epoch_of(d, PAY_FROM_ET):
        return _skip(ctx, key, "NOT_YET_DUE", f"payment {d.isoformat()} is due from {PAY_FROM_ET} ET", loan)
    through = min(d - ONE_DAY, _date(loan["maturity_date"]) - ONE_DAY)
    st = lifecycle_jobs.accrual_state_row(conn, KIND_CPN, loan_id)
    if through >= _date(loan["start_date"]) and (st is None or not st["accrued_through"] or _date(st["accrued_through"]) < through):
        have = None if st is None else st["accrued_through"]
        return _skip(ctx, key, "ACCRUAL_PENDING", f"the coupon has accrued through {have or 'no night'} but the payment needs the night of {through.isoformat()}", loan,
                     {"accrued_through": have, "needs": through.isoformat()})
    cal = ctx.cal
    final = d == final_payment_date(cal, loan)
    aid, iid, ccy = loan["account_id"], loan["instrument_id"], loan["currency"]
    coupon = coupon_due_c(conn, loan, through)
    if coupon < 0:
        raise RejectError("COUPON_NEGATIVE", f"coupon due {fmt_money(coupon)} is negative: payments exceed accruals for {loan_id}")
    lines: List[Line] = []
    if coupon:
        lines += [dr(aid, "1010", ccy, coupon, memo=f"coupon received for the nights through {through.isoformat()}"),
                  cr(aid, "1220", ccy, coupon, instrument_id=iid, memo="accrued coupon paid")]
    realised, cost = 0, 0
    if final:
        cost = amortised_cost_c(conn, loan)
        plines, realised = _principal_lines(loan, int(loan["principal_c"]), cost, f"face repaid at maturity {loan['maturity_date']}")
        lines += plines
    memo = (f"private credit payment {loan_id} ({loan['borrower']}) {d.isoformat()}: coupon {fmt_money(coupon)} for the nights through {through.isoformat()}"
            + (f"; face {fmt_money(int(loan['principal_c']))} repaid against amortised cost {fmt_money(cost)}, realised {fmt_money(realised)}" if final else ""))
    entry_id = ctx.post(Entry("PRIVATE_LOAN", memo, lines, f"pcpay:{loan_id}:{d.isoformat()}", ref_type="private_loan", ref_id=loan_id)) if lines else None
    if final:
        _close_loan(ctx, loan)
        next_date = None
    else:
        nd = next_payment_after(cal, loan, d)
        if nd is None:
            raise RejectError("SCHEDULE", f"no payment date after {d.isoformat()} for {loan_id}")
        next_date = nd.isoformat()
        conn.execute("UPDATE private_loans SET next_payment_date=? WHERE loan_id=?", (next_date, loan_id))
    ctx.touched_accounts.add(aid)
    ctx.event("PC_PAID", account_id=aid, instrument_id=iid, ref_type="private_loan", ref_id=loan_id, date=d.isoformat(), coupon_c=coupon, through=through.isoformat(),
              final=final, face_c=int(loan["principal_c"]) if final else 0, amortised_cost_c=cost, realised_c=realised, next_payment_date=next_date, entry_id=entry_id)
    note = f"coupon {fmt_money(coupon)} through {through.isoformat()}" + (f"; face {fmt_money(int(loan['principal_c']))} repaid, realised {fmt_money(realised)}" if final else
                                                                           f"; next payment {next_date}")
    lifecycle_jobs.write_job_done(ctx, JOB, key, "DONE", note)
    _resolve_skips(ctx, key)
    return {"status": "DONE", "key": key, "loan_id": loan_id, "date": d.isoformat(), "coupon_c": coupon, "posted_c": coupon + (int(loan["principal_c"]) if final else 0),
            "entry_id": entry_id, "final": final, "realised_c": realised, "next_payment_date": next_date, "posted": bool(lines), "note": note}


def register(bus, services=None) -> None:
    bus.register("pc.commit", h_commit)
    bus.register("pc.amend_fee", h_amend_fee)
    bus.register("pc.repay", h_repay)
    bus.register("pc.pay", h_pay)


__all__ = ["register", "h_commit", "h_amend_fee", "h_repay", "h_pay", "accrue_night", "accrual_lines_pc", "pc_mark", "loan_view", "loans_view", "loans_of",
           "pay_keys", "payment_schedule", "modified_following", "coupon_rate", "coupon_due_c", "index_for_night", "cash_out_c", "oid_total_c", "total_nights",
           "HANDLER_KINDS", "OAS_SERIES", "KIND_CPN", "KIND_OID", "JOB"]
