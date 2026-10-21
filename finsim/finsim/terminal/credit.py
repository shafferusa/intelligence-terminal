"""Currency loans and prime-broker financing draws (TERMINAL_DESIGN.md §2.12 credit.py, §5.4, §11.3, §12.2, §13.6,
§13.7, §19.6, §19.23; WP-05).

Conventions stated here (the §1.3 sentences that bite in this module)
- A cash loan (``loans.kind = CASH_LOAN``) is drawn in the Credit Instruments account from the lender "Money market
  (paper)": ``Dr 1010:ccy / Cr 2750:ccy`` (idem ``loan:{loan_id}:draw``).  The drawn currency is SETTLED cash at once
  and can be spot-converted or transferred the same day; the loan is a liability from the draw date and accrues from
  the draw night (the night of the accounting day it was drawn in, if open at 17:00 ET).
- Rate: the currency's policy index + 0.75 % (``tables.POLICY_INDEX`` / ``LOAN_SPREAD``), FLOATING: every night uses the
  latest published observation on or before that night (RATE_LAG, ``RateStore.as_of_le``); a currency without a policy
  series is refused ``CURRENCY_UNSUPPORTED``; a series without an observation is 409 ``RATE_UNAVAILABLE`` — a rate is
  never defaulted.  Monthly proxies (``POLICY_INDEX_MONTHLY``) are labelled in the memo.
- ACCRUAL (§11.3): ``Dr 5150:ccy / Cr 2755:ccy`` per calendar night (idem ``accr:loan:{loan_id}:{D}``, base at that
  command's FX rate, ACT/360, GBP ACT/365) through WP-08's cumulative-rounding helper ``lifecycle.accrue_segment(ctx,
  kind, ref, base_c, rate, basis, night) -> int`` (consumed as ``services.lifecycle.accrue_segment``; tests inject a
  local copy).  The base of night D is the principal STANDING AT THE 17:00 ET CLOSE OF D (``principal_at_close_c``: the
  loan's own 2750 lines, draw and repayments, whose entry ``business_date`` ≤ D — an entry after 17:00 belongs to the
  next accounting day), never the row's current ``principal_c`` read when the job happens to run.  The segment restarts
  on a new rate observation or a principal change because the helper compares the stored base / rate, so the sum of the
  nightly postings equals ``to_c(base × rate × n / basis)`` per segment.  The memo carries the index, its observation
  date, the night and the base ("ECBDFR 2.25 % (2026-10-22) + 0.75 % = 3.00 %, ACT/360, night of 2026-10-22 on
  principal 1,000,000.00").  Interest is Financing in P&L; it is paid at repayment, not monthly.
- NIGHTS OWED AT REPAYMENT (review MONEY-1): a repayment or a maturity FIRST posts every night the loan owes that the
  00:30 ACCRUE_DAILY run has not posted yet (``catch_up_accruals``: the nights D with ``opened_date ≤ D < the command's
  accounting day`` after ``accrued_through``, in date order, under the job's own idem keys, so the job later finds them
  done).  A loan open at a 17:00 close therefore pays that night even when it is repaid between 17:00 and 00:30, after a
  restart or after laptop sleep, and that night is charged on the principal of that close, not on the reduced one; a
  draw and a repayment inside one accounting day cross no close and owe no night.  A night that cannot accrue (no rate
  observation, no FX rate, no accrual helper) refuses the whole command 409 ``ACCRUAL_PENDING`` naming the night and the
  cause — nothing is repaid around a missing night, nothing is defaulted.
- Repayment: ``credit.loan_repay {loan_id, amount | full: true}`` pays accrued interest first (after the catch-up above),
  then principal (``Dr 2755 / Dr 2750 / Cr 1010:ccy``, idem ``loan:{loan_id}:repay:{request_id}``); ``full: true``
  repays principal + accrued as they stand after the catch-up (the payoff, which no client can know between 17:00 and
  00:30); refused 409 ``INSUFFICIENT_CASH_CCY`` with the hint "buy <ccy> <shortfall> with Convert" (USD: "transfer USD
  <shortfall> from Treasury") when the account's SETTLED cash in that currency is short — no automatic FX; 400
  ``AMOUNT_EXCEEDS`` above principal + accrued (both refusals carry ``total_due_c`` after the catch-up).  The result
  carries ``accrued_nights`` (the nights the catch-up posted) and ``paid_interest_c`` / ``paid_principal_c``.  The loan
  CLOSES when its principal reaches zero (L14: Σ OPEN principal = balance(2750) per account and currency).
- Maturity (``term_days``): ``credit.loan_mature`` attempts the full repayment (catch-up first) on the maturity date;
  when cash is short the loan ROLLS to the next business day with one open Attention WARN ``LOAN_ROLLED`` — no invented
  default; the nights posted by the catch-up stay posted (they are owed either way).
- PB financing (§13.7): ``credit.pb_draw`` / ``credit.pb_repay`` move ``Dr 1010:USD / Cr 2700:USD`` and back (idem
  ``pb:{account}:draw:{request_id}`` / ``pb:{account}:repay:{request_id}``) in a PB account whose ``pb_financing``
  switch is on, within ``accounting.pb_headroom_c`` (50 % of long market value − 2700 − OTC margin, capped by
  ``pb_limit_c``); one ``loans`` row of kind ``PB_MARGIN`` per PB account carries the drawn principal (L14).  The hook
  ``pb_draw_for_funding`` (called by ``execution.fund`` after Treasury) draws ``min(shortfall, headroom)``.  Its
  interest (SOFR + the PB schedule's ``financing_spread``, nightly to 2300, paid monthly) is WP-07's accrual line.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import List, Optional, Tuple

from . import tables
from .books import account_row, lifetime_row
from .commands import ConflictError, RejectError, TxContext, require_amount_c
from .db import next_id
from .ledger import Entry, LedgerError, Line, balance, cr, dr
from .units import fmt_money, qprice

LENDER = "Money market (paper)"
LOAN_KIND = "CASH_LOAN"
PB_KIND = "PB_MARGIN"
ACCRUAL_KIND = "loan"


# ---- small helpers -----------------------------------------------------------------------------------------------------------
def _ccy(x) -> str:
    c = str(x or "").upper()
    if len(c) != 3 or not c.isalpha():
        raise RejectError("CURRENCY", f"currency must be a 3-letter code, got {x!r}")
    return c


def _pct(x: Decimal) -> str:
    """'2.00' / '2.75' / '4.6250' — a label; the exact rate lives in accrual_state.rate."""
    q2 = x.quantize(Decimal("0.01"))
    return f"{q2:.2f}" if q2 == x else f"{x.quantize(Decimal('0.0001')):.4f}"


def _as_date(d) -> date:
    return d if isinstance(d, date) else date.fromisoformat(str(d))


def _open_account(conn, account_id: str):
    acct = account_row(conn, account_id)
    if acct is None:
        raise RejectError("ACCOUNT_UNKNOWN", f"account {account_id} does not exist")
    lrow = lifetime_row(conn, acct["lifetime_id"])
    if lrow is None or lrow["status"] != "OPEN":
        raise RejectError("LIFETIME_CLOSED", f"lifetime {acct['lifetime_id']} is closed (History is read-only)")
    if acct["status"] != "OPEN":
        raise RejectError("ACCOUNT_CLOSED", f"account {account_id} is closed")
    return acct


def _services(ctx: TxContext, services=None):
    s = services if services is not None else ctx.services
    if s is None:
        raise RejectError("NO_SERVICES", "the command context carries no services bundle")
    return s


def _fx_rate(ctx: TxContext, ccy: str) -> Decimal:
    try:
        return ctx.fx.rate(ccy)
    except LedgerError as e:
        raise ConflictError("FX_RATE_UNAVAILABLE", f"no USD rate for {ccy} in the mark snapshot: the loan cannot be valued in base", {"currency": ccy}) from e


# ---- reads ---------------------------------------------------------------------------------------------------------------------
def loan_row(conn, loan_id: str):
    return conn.execute("SELECT * FROM loans WHERE loan_id=?", (loan_id,)).fetchone()


def accrued_c(conn, loan_id: str) -> int:
    """Accrued, unpaid interest of one loan (natural cents): the 2755 lines of the entries referenced to the loan."""
    row = conn.execute("SELECT COALESCE(SUM(l.amount_c), 0) FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id"
                       " WHERE e.ref_type='loan' AND e.ref_id=? AND l.gl='2755'", (loan_id,)).fetchone()
    return -int(row[0] or 0)


def principal_at_close_c(conn, loan_id: str, d) -> int:
    """Principal of one cash loan standing at the 17:00 ET close of accounting day ``d`` (natural cents): the 2750 lines of
    the entries referenced to the loan (its draw and its repayments) whose ``business_date`` ≤ ``d``.  A repayment posted
    after 17:00 carries the next accounting day, so it does not reduce the base of the night it was still open at."""
    row = conn.execute("SELECT COALESCE(SUM(l.amount_c), 0) FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id"
                       " WHERE e.ref_type='loan' AND e.ref_id=? AND l.gl='2750' AND e.business_date<=?", (loan_id, _as_date(d).isoformat())).fetchone()
    return -int(row[0] or 0)


def accrual_state(conn, loan_id: str):
    return conn.execute("SELECT * FROM accrual_state WHERE kind=? AND ref_id=?", (ACCRUAL_KIND, loan_id)).fetchone()


def loan_view(conn, loan_id: str) -> dict:
    r = loan_row(conn, loan_id)
    if r is None:
        raise RejectError("LOAN_UNKNOWN", f"loan {loan_id} does not exist")
    acc = accrued_c(conn, loan_id) if r["kind"] == LOAN_KIND else 0
    st = accrual_state(conn, loan_id)
    return {"loan_id": r["loan_id"], "account_id": r["account_id"], "kind": r["kind"], "currency": r["currency"], "principal_c": int(r["principal_c"]),
            "accrued_c": acc, "total_due_c": int(r["principal_c"]) + acc, "index_name": r["index_name"], "spread": r["spread"], "day_basis": int(r["day_basis"]),
            "opened_date": r["opened_date"], "maturity_date": r["maturity_date"], "lender": r["lender"], "status": r["status"], "closed_ts": r["closed_ts"],
            "accrued_through": st["accrued_through"] if st else None, "segment_start": st["segment_start"] if st else None,
            "segment_rate": st["rate"] if st else None, "monthly_proxy": r["index_name"] in tables.POLICY_INDEX_MONTHLY}


def loans_of(conn, account_id: Optional[str] = None, status: Optional[str] = "OPEN") -> List[dict]:
    sql, params = "SELECT loan_id FROM loans WHERE 1=1", []
    if account_id:
        sql += " AND account_id=?"
        params.append(account_id)
    if status:
        sql += " AND status=?"
        params.append(status)
    sql += " ORDER BY opened_date, loan_id"
    return [loan_view(conn, r["loan_id"]) for r in conn.execute(sql, params).fetchall()]


def nights_due(conn, loan, today) -> List[date]:
    """Calendar nights D of an OPEN cash loan with ``opened_date ≤ D < today`` not yet accrued (for WP-08's ACCRUE_DAILY
    keys ``loan:{loan_id}:{D}``)."""
    today = _as_date(today)
    start = _as_date(loan["opened_date"])
    st = accrual_state(conn, loan["loan_id"])
    if st is not None and st["accrued_through"]:
        start = max(start, _as_date(st["accrued_through"]) + timedelta(days=1))
    out = []
    d = start
    while d < today:
        out.append(d)
        d += timedelta(days=1)
    return out


# ---- rates ---------------------------------------------------------------------------------------------------------------------
def policy_series(ccy: str) -> str:
    sid = tables.POLICY_INDEX.get(_ccy(ccy))
    if not sid:
        raise RejectError("CURRENCY_UNSUPPORTED", f"{ccy}: no policy-rate series; loans are available in {', '.join(sorted(tables.POLICY_INDEX))}",
                          {"currency": ccy, "supported": sorted(tables.POLICY_INDEX)})
    return sid


def index_observation(services, ccy: str, on) -> Tuple[str, date, Decimal]:
    """``(series, observation date, percent)``: the latest published observation on or before ``on`` (RATE_LAG).
    409 ``RATE_UNAVAILABLE`` when the store has none — never a defaulted number."""
    sid = policy_series(ccy)
    rates = getattr(services, "rates", None)
    got = rates.as_of_le(sid, _as_date(on)) if rates is not None else None
    if got is None:
        raise ConflictError("RATE_UNAVAILABLE", f"no {sid} observation on or before {_as_date(on).isoformat()} (FRED not loaded): the {ccy} rate is unknown, not defaulted",
                            {"series": sid, "currency": ccy, "on": _as_date(on).isoformat()})
    obs_date, pct = got
    return sid, obs_date, qprice(pct)


def loan_rate(index_pct: Decimal, spread) -> Decimal:
    """Decimal fraction: ``index % / 100 + spread`` (2.00 % + 0.0075 -> 0.0275)."""
    return qprice(Decimal(index_pct) / Decimal(100) + Decimal(str(spread)))


def _accrue_fn(services):
    lc = getattr(services, "lifecycle", None)
    fn = getattr(lc, "accrue_segment", None)
    if fn is None:
        raise RejectError("NOT_IN_THIS_BUILD", "loan accruals need the lifecycle accrual helper (WP-08 lifecycle.accrue_segment), which is not loaded")
    return fn


# ---- accrual (§11.3) -----------------------------------------------------------------------------------------------------------
def accrual_lines_loan(ctx: TxContext, loan, d, index, fx=None) -> List[Line]:
    """The night-``d`` lines of one cash loan: ``Dr 5150:ccy / Cr 2755:ccy`` for the amount the cumulative-rounding
    segment yields (empty when it rounds to zero).  The base is the principal standing at the 17:00 ET close of ``d``
    (``principal_at_close_c``), not the row's current principal.  ``index`` is ``(obs_date, percent)`` from
    ``index_observation`` (or a bare percent); ``fx`` is unused — base is the command's rate (§5.4).  Mutates
    ``accrual_state`` through ``accrue_segment``; the caller posts the entry (idem ``accr:loan:{loan_id}:{d}``)."""
    services = _services(ctx)
    accrue = _accrue_fn(services)
    night = _as_date(d)
    obs_date, pct = (index if isinstance(index, (tuple, list)) else (None, index))
    rate = loan_rate(Decimal(str(pct)), loan["spread"])
    basis = int(loan["day_basis"])
    base_c = principal_at_close_c(ctx.conn, loan["loan_id"], night)
    amount = int(accrue(ctx, ACCRUAL_KIND, loan["loan_id"], base_c, rate, basis, night))
    if amount == 0:
        return []
    label = f"{loan['index_name']} {_pct(Decimal(str(pct)))} %" + (f" ({obs_date.isoformat() if isinstance(obs_date, date) else obs_date})" if obs_date else "")
    label += f" + {_pct(Decimal(str(loan['spread'])) * 100)} % = {_pct(rate * 100)} %, ACT/{basis}, night of {night.isoformat()} on principal {fmt_money(base_c)}"
    if loan["index_name"] in tables.POLICY_INDEX_MONTHLY:
        label += " (monthly proxy)"
    aid, ccy = loan["account_id"], loan["currency"]
    return [dr(aid, "5150", ccy, amount, memo=label), cr(aid, "2755", ccy, amount, memo=label)]


def post_loan_accrual(ctx: TxContext, services, loan, night, force: bool = False) -> dict:
    """One night of one cash loan as an entry (idempotent: an existing ``accr:loan:{id}:{D}`` entry is skipped before
    the segment moves).  Returns ``{loan_id, night, amount_c, entry_id, rate, index, cum_c}``."""
    night = _as_date(night)
    loan_id = loan["loan_id"]
    idem = f"accr:loan:{loan_id}:{night.isoformat()}"
    existing = ctx.conn.execute("SELECT entry_id FROM journal_entries WHERE idem_key=?", (idem,)).fetchone()
    if existing is not None:
        return {"loan_id": loan_id, "night": night.isoformat(), "amount_c": 0, "entry_id": int(existing["entry_id"]), "skipped": True}
    if loan["status"] != "OPEN":
        raise ConflictError("LOAN_CLOSED", f"loan {loan_id} is closed")
    if night < _as_date(loan["opened_date"]):
        raise RejectError("NIGHT_BEFORE_DRAW", f"night {night.isoformat()} is before the loan's draw day {loan['opened_date']}")
    if night.isoformat() >= ctx.today and not force:
        raise RejectError("NIGHT_NOT_ENDED", f"the night of {night.isoformat()} has not ended (accounting day {ctx.today}); accruals post from 00:30 the next day")
    sid, obs_date, pct = index_observation(services, loan["currency"], night)
    _fx_rate(ctx, loan["currency"])
    lines = accrual_lines_loan(ctx, loan, night, (obs_date, pct))
    st = accrual_state(ctx.conn, loan_id)
    out = {"loan_id": loan_id, "night": night.isoformat(), "amount_c": 0, "entry_id": None, "rate": str(loan_rate(pct, loan["spread"])),
           "index": {"series": sid, "value": str(pct), "obs_date": obs_date.isoformat()}, "cum_c": int(st["cum_c"]) if st else 0,
           "segment_start": st["segment_start"] if st else None, "skipped": False}
    if not lines:
        return out
    amount = lines[0].amount_c
    entry_id = ctx.post(Entry("ACCRUAL", f"loan interest {loan_id}: {loan['currency']} {fmt_money(amount)} — {lines[0].memo}", lines, idem,
                              ref_type="loan", ref_id=loan_id))
    ctx.event("ACCRUED", account_id=loan["account_id"], ref_type="loan", ref_id=loan_id, kind=ACCRUAL_KIND, night=night.isoformat(), amount_c=amount,
              currency=loan["currency"], rate=out["rate"], index=out["index"], entry_id=entry_id)
    out.update(amount_c=amount, entry_id=entry_id)
    return out


def catch_up_accruals(ctx: TxContext, loan, services=None) -> List[dict]:
    """Module docstring NIGHTS OWED AT REPAYMENT: post every night the OPEN cash loan owes and the 00:30 run has not
    posted yet (``nights_due`` up to the command's accounting day, exclusive), in date order, under the job's idem keys.
    Returns the per-night results (``post_loan_accrual``).  A night that cannot accrue -> 409 ``ACCRUAL_PENDING``."""
    nights = nights_due(ctx.conn, loan, ctx.today)
    if not nights:
        return []
    services = _services(ctx, services)
    done: List[dict] = []
    for night in nights:
        try:
            done.append(post_loan_accrual(ctx, services, loan, night))
        except RejectError as e:
            raise ConflictError("ACCRUAL_PENDING",
                                f"loan {loan['loan_id']} owes the night of {night.isoformat()} but its interest cannot be computed ({e.code}: {e.message});"
                                " nothing is repaid around a missing night — retry once it can post",
                                dict(e.details, loan_id=loan["loan_id"], night=night.isoformat(), cause=e.code)) from e
    return done


# ---- cash loans ----------------------------------------------------------------------------------------------------------------
def _credit_account(ctx: TxContext, payload: dict):
    aid = str(payload.get("account_id") or payload.get("account") or "")
    if not aid:
        raise RejectError("ACCOUNT_REQUIRED", "account_id is required")
    acct = _open_account(ctx.conn, aid)
    if acct["kind"] != "TRADING" or acct["role"] != "CREDIT":
        raise RejectError("NOT_CREDIT_ACCOUNT", f"{aid} is not the Credit Instruments account: cash loans are drawn there (PB financing uses credit.pb_draw)",
                          {"account_id": aid, "role": acct["role"], "kind": acct["kind"]})
    return acct


def h_loan_draw(ctx: TxContext, payload: dict) -> dict:
    """``credit.loan_draw {account_id, currency, amount_c | amount, term_days | null, memo?}``."""
    services = _services(ctx)
    acct = _credit_account(ctx, payload)
    aid = acct["account_id"]
    ccy = _ccy(payload.get("currency"))
    amount_c = require_amount_c(payload)
    term = payload.get("term_days")
    if term is not None:
        try:
            term = int(term)
        except (TypeError, ValueError) as e:
            raise RejectError("TERM", f"term_days must be an integer number of days, got {term!r}") from e
        if term <= 0:
            raise RejectError("TERM", "term_days must be positive (or null for an open-ended loan)")
    sid, obs_date, pct = index_observation(services, ccy, ctx.today)
    rate = loan_rate(pct, tables.LOAN_SPREAD)
    fx_rate = _fx_rate(ctx, ccy)
    basis = tables.daycount(ccy)
    opened = date.fromisoformat(ctx.today)
    maturity = ctx.cal.roll_following(opened + timedelta(days=term)).isoformat() if term else None
    loan_id = next_id(ctx.conn, "loan")
    note = str(payload.get("memo") or "")
    label = f"{sid} {_pct(pct)} % ({obs_date.isoformat()}) + {_pct(tables.LOAN_SPREAD * 100)} % = {_pct(rate * 100)} %, ACT/{basis}"
    if sid in tables.POLICY_INDEX_MONTHLY:
        label += " (monthly proxy)"
    memo = f"loan draw {loan_id}: {ccy} {fmt_money(amount_c)} from {LENDER} at {label}" + (f", matures {maturity}" if maturity else ", open-ended") + (f": {note}" if note else "")
    entry_id = ctx.post(Entry("LOAN", memo, [dr(aid, "1010", ccy, amount_c, memo="drawn: settled at once"), cr(aid, "2750", ccy, amount_c, memo=f"principal, {LENDER}")],
                              f"loan:{loan_id}:draw", ref_type="loan", ref_id=loan_id))
    ctx.conn.execute("INSERT INTO loans(loan_id, command_id, account_id, kind, currency, principal_c, index_name, spread, day_basis, opened_date, maturity_date, lender,"
                     " status, closed_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'OPEN',NULL)",
                     (loan_id, ctx.command_id, aid, LOAN_KIND, ccy, amount_c, sid, str(tables.LOAN_SPREAD), basis, opened.isoformat(), maturity, LENDER))
    ctx.event("LOAN_DRAWN", account_id=aid, ref_type="loan", ref_id=loan_id, kind=LOAN_KIND, currency=ccy, amount_c=amount_c, rate=str(rate),
              index={"series": sid, "value": str(pct), "obs_date": obs_date.isoformat()}, maturity_date=maturity, lender=LENDER, entry_id=entry_id)
    view = loan_view(ctx.conn, loan_id)
    view.update(rate=str(rate), index={"series": sid, "value": str(pct), "obs_date": obs_date.isoformat()}, fx_rate=str(fx_rate))
    return {"loan": view, "loan_id": loan_id, "entry_id": entry_id, "settled_c": balance(ctx.conn, aid, "1010", ccy), "memo": memo}


def _repayment(ctx: TxContext, loan, amount_c: Optional[int], idem: str, memo_prefix: str) -> dict:
    """Post every night owed first (``catch_up_accruals``), then ``Dr 2755 accrued / Dr 2750 principal / Cr 1010`` for
    ``amount_c`` (accrued first; None = the whole amount due after the catch-up) and update the loan row."""
    aid, ccy, loan_id = loan["account_id"], loan["currency"], loan["loan_id"]
    caught = [x for x in catch_up_accruals(ctx, loan) if not x.get("skipped")]
    caught_c = sum(int(x.get("amount_c") or 0) for x in caught)
    loan = loan_row(ctx.conn, loan_id)
    accrued = accrued_c(ctx.conn, loan_id)
    principal = int(loan["principal_c"])
    total = accrued + principal
    if amount_c is None:
        amount_c = total
    if amount_c > total:
        raise RejectError("AMOUNT_EXCEEDS", f"loan {loan_id} is due {ccy} {fmt_money(total)} (principal {fmt_money(principal)} + accrued {fmt_money(accrued)}); "
                          f"the repayment {fmt_money(amount_c)} exceeds it", {"principal_c": principal, "accrued_c": accrued, "total_due_c": total, "amount_c": amount_c,
                                                                               "accrued_at_repayment_c": caught_c})
    settled = balance(ctx.conn, aid, "1010", ccy)
    if settled < amount_c:
        short = amount_c - settled
        hint = _cash_hint(ccy, short)
        raise ConflictError("INSUFFICIENT_CASH_CCY", f"{aid} holds {ccy} {fmt_money(settled)} settled; the repayment needs {fmt_money(amount_c)}: {hint}",
                            {"settled_c": settled, "amount_c": amount_c, "shortfall_c": short, "currency": ccy, "account_id": aid,
                             "hint": hint, "total_due_c": total, "accrued_at_repayment_c": caught_c})
    pay_accrued = min(amount_c, accrued)
    pay_principal = amount_c - pay_accrued
    _fx_rate(ctx, ccy)
    lines = []
    if pay_accrued:
        lines.append(dr(aid, "2755", ccy, pay_accrued, memo="accrued interest paid"))
    if pay_principal:
        lines.append(dr(aid, "2750", ccy, pay_principal, memo="principal repaid"))
    lines.append(cr(aid, "1010", ccy, amount_c, memo=f"repayment to {loan['lender']}"))
    left = principal - pay_principal
    closed = left == 0
    memo = f"{memo_prefix} {loan_id}: {ccy} {fmt_money(amount_c)} (interest {fmt_money(pay_accrued)}, principal {fmt_money(pay_principal)}); principal left {fmt_money(left)}" + (
        "; loan CLOSED" if closed else "") + _caught_note(caught)
    entry_id = ctx.post(Entry("LOAN", memo, lines, idem, ref_type="loan", ref_id=loan_id))
    ctx.conn.execute("UPDATE loans SET principal_c=?, status=?, closed_ts=? WHERE loan_id=?", (left, "CLOSED" if closed else "OPEN", ctx.ts if closed else None, loan_id))
    if closed:
        ctx.conn.execute("UPDATE attention SET resolved_ts=? WHERE code='LOAN_ROLLED' AND ref_type='loan' AND ref_id=? AND resolved_ts IS NULL", (ctx.ts, loan_id))
    nights = [{"night": x["night"], "amount_c": int(x.get("amount_c") or 0), "entry_id": x.get("entry_id")} for x in caught]
    ctx.event("LOAN_REPAID", account_id=aid, ref_type="loan", ref_id=loan_id, kind=loan["kind"], currency=ccy, amount_c=amount_c, interest_c=pay_accrued,
              principal_c=pay_principal, principal_left_c=left, closed=closed, entry_id=entry_id, accrued_nights=nights, accrued_at_repayment_c=caught_c)
    # paid_interest_c / paid_principal_c: the names the Cash & financing page's repay toast and its API fixture read
    return {"loan_id": loan_id, "entry_id": entry_id, "amount_c": amount_c, "interest_c": pay_accrued, "principal_c": pay_principal, "principal_left_c": left,
            "paid_interest_c": pay_accrued, "paid_principal_c": pay_principal,
            "closed": closed, "settled_c": balance(ctx.conn, aid, "1010", ccy), "loan": loan_view(ctx.conn, loan_id), "memo": memo,
            "accrued_nights": nights, "accrued_at_repayment_c": caught_c}


def _cash_hint(ccy: str, short_c: Optional[int] = None) -> str:
    """What the owner does when the repayment currency is short: a foreign currency is bought with Convert (§13.6, no
    automatic FX); USD comes from the book's Treasury by an internal transfer."""
    amt = f" {fmt_money(short_c)}" if short_c is not None else ""
    return f"transfer USD{amt} from Treasury" if ccy == "USD" else f"buy {ccy}{amt} with Convert"


def _caught_note(caught: List[dict]) -> str:
    """The repayment memo's disclosure of the nights the catch-up posted in the same command ('' when none)."""
    if not caught:
        return ""
    amt = fmt_money(sum(int(x.get("amount_c") or 0) for x in caught))
    if len(caught) == 1:
        return f"; accrued first at repayment: night of {caught[0]['night']} {amt}"
    return f"; accrued first at repayment: {len(caught)} nights {caught[0]['night']} .. {caught[-1]['night']} {amt}"


def _open_cash_loan(ctx: TxContext, payload: dict):
    loan_id = str(payload.get("loan_id") or "")
    loan = loan_row(ctx.conn, loan_id) if loan_id else None
    if loan is None:
        raise RejectError("LOAN_UNKNOWN", f"loan {loan_id!r} does not exist")
    if loan["kind"] != LOAN_KIND:
        raise RejectError("NOT_CASH_LOAN", f"{loan_id} is PB financing: use credit.pb_repay")
    if loan["status"] != "OPEN":
        raise ConflictError("LOAN_CLOSED", f"loan {loan_id} is closed")
    _open_account(ctx.conn, loan["account_id"])
    return loan


def h_loan_repay(ctx: TxContext, payload: dict) -> dict:
    """``credit.loan_repay {loan_id, amount_c | amount | full: true}``: every night owed is posted first, then accrued
    first, then principal; ``full`` repays the whole amount due after that catch-up (never together with an amount)."""
    loan = _open_cash_loan(ctx, payload)
    if payload.get("full"):
        if payload.get("amount_c") is not None or payload.get("amount") is not None:
            raise RejectError("AMOUNT", "give an amount or full: true, not both")
        amount_c = None
    else:
        amount_c = require_amount_c(payload)
    return _repayment(ctx, loan, amount_c, f"loan:{loan['loan_id']}:repay:{ctx.request_id}", "loan repayment")


def h_loan_accrue(ctx: TxContext, payload: dict) -> dict:
    """``credit.loan_accrue {loan_id, night, force?}`` (actor lifecycle; the ACCRUE_DAILY unit for one loan and one night)."""
    loan = _open_cash_loan(ctx, payload)
    if not payload.get("night"):
        raise RejectError("NIGHT_REQUIRED", "night (YYYY-MM-DD) is required")
    return post_loan_accrual(ctx, _services(ctx), loan, payload["night"], bool(payload.get("force")))


def h_loan_mature(ctx: TxContext, payload: dict) -> dict:
    """``credit.loan_mature {loan_id}``: on or after the maturity date post every night owed, then repay principal +
    accrued from settled cash; when the cash is short the loan rolls to the next business day with one open WARN
    ``LOAN_ROLLED`` (no invented default) and the nights just posted stay posted (they are owed either way)."""
    loan = _open_cash_loan(ctx, payload)
    loan_id = loan["loan_id"]
    if not loan["maturity_date"]:
        raise RejectError("OPEN_ENDED", f"loan {loan_id} has no maturity date")
    if loan["maturity_date"] > ctx.today and not payload.get("force"):
        raise RejectError("NOT_DUE", f"loan {loan_id} matures {loan['maturity_date']} (today {ctx.today})", {"maturity_date": loan["maturity_date"]})
    try:
        out = _repayment(ctx, loan, None, f"loan:{loan_id}:repay:{ctx.request_id}", "loan maturity repayment")
        out["rolled"] = False
        return out
    except ConflictError as e:
        if e.code != "INSUFFICIENT_CASH_CCY":
            raise
    # the catch-up inside _repayment ran before the cash refusal: the amount due below includes every night owed
    loan = loan_row(ctx.conn, loan_id)
    total = int(loan["principal_c"]) + accrued_c(ctx.conn, loan_id)
    new_date = ctx.cal.next_business_day(date.fromisoformat(ctx.today)).isoformat()
    ctx.conn.execute("UPDATE loans SET maturity_date=? WHERE loan_id=?", (new_date, loan_id))
    if ctx.conn.execute("SELECT id FROM attention WHERE code='LOAN_ROLLED' AND ref_type='loan' AND ref_id=? AND resolved_ts IS NULL", (loan_id,)).fetchone() is None:
        ctx.attention("WARN", "credit", "LOAN_ROLLED",
                      f"loan {loan_id} matured {loan['maturity_date']} but {loan['account_id']} holds {loan['currency']} {fmt_money(balance(ctx.conn, loan['account_id'], '1010', loan['currency']))}"
                      f" settled against {fmt_money(total)} due: it rolls day by day until repaid ({_cash_hint(loan['currency'])}, then credit.loan_repay)",
                      {"account_id": loan["account_id"], "ref_type": "loan", "ref_id": loan_id, "total_due_c": total, "currency": loan["currency"], "auto_resolve": 1})
    ctx.event("LOAN_ROLLED", account_id=loan["account_id"], ref_type="loan", ref_id=loan_id, matured=loan["maturity_date"], new_maturity=new_date, total_due_c=total)
    return {"loan_id": loan_id, "rolled": True, "maturity_date": new_date, "total_due_c": total, "loan": loan_view(ctx.conn, loan_id)}


# ---- prime-broker financing (§13.7) ------------------------------------------------------------------------------------------
def pb_loan_id(account_id: str) -> str:
    return f"PB-{account_id}"


def pb_schedule(services, acct) -> dict:
    """The PB account's live schedule through ``services.prime.schedule`` (WP-07), else the seed row of
    ``tables.PB_SCHEDULES`` for its broker."""
    prime = getattr(services, "prime", None)
    if prime is not None and hasattr(prime, "schedule"):
        s = prime.schedule(acct)
        if s is not None:
            return s if isinstance(s, dict) else (s.to_dict() if hasattr(s, "to_dict") else dict(getattr(s, "__dict__", {})))
    broker = acct["broker"] or (acct["role"][3:] if str(acct["role"]).startswith("PB_") else "")
    return dict(tables.PB_SCHEDULES.get(broker) or {"name": broker or "prime broker", "financing_spread": Decimal("0.0125")})


def _pb_account(conn, account_id: str):
    acct = _open_account(conn, account_id)
    if acct["kind"] != "PB":
        raise RejectError("NOT_PB", f"{account_id} is not a prime-broker account")
    if not int(acct["pb_financing"] or 0):
        raise RejectError("PB_FINANCING_OFF", f"{account_id}: PB financing is switched off (Settings → pb_financing)")
    return acct


def headroom_c(ctx: TxContext, services, acct) -> int:
    """``accounting.pb_headroom_c`` on the command's connection and mark snapshot (WP-07 may replace the formula)."""
    return int(services.accounting.pb_headroom_c(ctx.conn, dict(acct), ctx.marks))


def pb_draw(ctx: TxContext, services, account_id: str, amount_c: int, link_type: str, link_id: str, idem_suffix: str) -> dict:
    """``Dr 1010:USD / Cr 2700:USD`` within headroom; the account's single PB_MARGIN loans row carries the principal."""
    amount_c = int(amount_c)
    if amount_c <= 0:
        raise RejectError("AMOUNT", "draw amount must be positive")
    acct = _pb_account(ctx.conn, account_id)
    head = headroom_c(ctx, services, acct)
    if amount_c > head:
        raise ConflictError("PB_HEADROOM", f"{account_id}: PB financing headroom is USD {fmt_money(max(head, 0))}; the draw of {fmt_money(amount_c)} exceeds it",
                            {"headroom_c": head, "amount_c": amount_c, "account_id": account_id, "formula": "50 % of long market value − 2700 − OTC margin, capped by pb_limit_c"})
    sched = pb_schedule(services, acct)
    spread = Decimal(str(sched.get("financing_spread", "0.0125")))
    lender = str(sched.get("name") or acct["broker"] or "prime broker")
    lid = pb_loan_id(account_id)
    memo = f"PB financing draw {lid}: USD {fmt_money(amount_c)} from {lender} for {link_type} {link_id} (SOFR + {_pct(spread * 100)} %, ACT/360)"
    entry_id = ctx.post(Entry("LOAN", memo, [dr(account_id, "1010", "USD", amount_c, memo="drawn: settled at once"), cr(account_id, "2700", "USD", amount_c, memo=lender)],
                              f"pb:{account_id}:draw:{idem_suffix}", ref_type="loan", ref_id=lid))
    row = loan_row(ctx.conn, lid)
    if row is None:
        ctx.conn.execute("INSERT INTO loans(loan_id, command_id, account_id, kind, currency, principal_c, index_name, spread, day_basis, opened_date, maturity_date, lender,"
                         " status, closed_ts) VALUES (?,?,?,?,?,?,?,?,?,?,NULL,?,'OPEN',NULL)",
                         (lid, ctx.command_id, account_id, PB_KIND, "USD", amount_c, "SOFR", str(spread), 360, ctx.today, lender))
    else:
        ctx.conn.execute("UPDATE loans SET principal_c=principal_c+?, status='OPEN', closed_ts=NULL, spread=?, lender=? WHERE loan_id=?", (amount_c, str(spread), lender, lid))
    ctx.event("LOAN_DRAWN", account_id=account_id, ref_type="loan", ref_id=lid, kind=PB_KIND, currency="USD", amount_c=amount_c, link_type=link_type, link_id=link_id,
              headroom_before_c=head, spread=str(spread), lender=lender, entry_id=entry_id)
    return {"loan_id": lid, "entry_id": entry_id, "amount_c": amount_c, "headroom_before_c": head, "headroom_after_c": head - amount_c,
            "financed_c": balance(ctx.conn, account_id, "2700", "USD"), "spread": str(spread), "lender": lender, "memo": memo}


def pb_draw_for_funding(ctx: TxContext, services, account_id: str, shortfall_c: int, link_type: str, link_id: str) -> int:
    """The ``execution.fund`` hook: draw ``min(shortfall, headroom)`` when the account is a PB account with financing on;
    returns the cents drawn (0 when nothing can be drawn)."""
    acct = account_row(ctx.conn, account_id)
    if acct is None or acct["kind"] != "PB" or not int(acct["pb_financing"] or 0) or acct["status"] != "OPEN":
        return 0
    head = headroom_c(ctx, services, acct)
    draw = min(int(shortfall_c), head)
    if draw <= 0:
        return 0
    pb_draw(ctx, services, account_id, draw, link_type, link_id, f"{ctx.request_id}:{link_id}")
    return draw


def h_pb_draw(ctx: TxContext, payload: dict) -> dict:
    """``credit.pb_draw {account_id, amount_c | amount}``."""
    aid = str(payload.get("account_id") or "")
    if not aid:
        raise RejectError("ACCOUNT_REQUIRED", "account_id is required")
    return pb_draw(ctx, _services(ctx), aid, require_amount_c(payload), "request", ctx.request_id, ctx.request_id)


def h_pb_repay(ctx: TxContext, payload: dict) -> dict:
    """``credit.pb_repay {account_id, amount_c | amount}``: ``Dr 2700 / Cr 1010`` from settled USD (interest is paid monthly by WP-07's line)."""
    aid = str(payload.get("account_id") or "")
    if not aid:
        raise RejectError("ACCOUNT_REQUIRED", "account_id is required")
    acct = _open_account(ctx.conn, aid)
    if acct["kind"] != "PB":
        raise RejectError("NOT_PB", f"{aid} is not a prime-broker account")
    amount_c = require_amount_c(payload)
    financed = balance(ctx.conn, aid, "2700", "USD")
    if amount_c > financed:
        raise RejectError("AMOUNT_EXCEEDS", f"{aid} has USD {fmt_money(financed)} of PB financing; the repayment {fmt_money(amount_c)} exceeds it",
                          {"financed_c": financed, "amount_c": amount_c})
    settled = balance(ctx.conn, aid, "1010", "USD")
    if settled < amount_c:
        short = amount_c - settled
        raise ConflictError("INSUFFICIENT_CASH_CCY", f"{aid} holds USD {fmt_money(settled)} settled; the repayment needs {fmt_money(amount_c)} (short {fmt_money(short)})",
                            {"settled_c": settled, "amount_c": amount_c, "shortfall_c": short, "currency": "USD", "account_id": aid})
    lid = pb_loan_id(aid)
    row = loan_row(ctx.conn, lid)
    lender = row["lender"] if row else "prime broker"
    memo = f"PB financing repayment {lid}: USD {fmt_money(amount_c)} to {lender}; financed left {fmt_money(financed - amount_c)}"
    entry_id = ctx.post(Entry("LOAN", memo, [dr(aid, "2700", "USD", amount_c, memo="financing repaid"), cr(aid, "1010", "USD", amount_c, memo=lender)],
                              f"pb:{aid}:repay:{ctx.request_id}", ref_type="loan", ref_id=lid))
    left = financed - amount_c
    if row is not None:
        ctx.conn.execute("UPDATE loans SET principal_c=?, status=?, closed_ts=? WHERE loan_id=?", (left, "OPEN" if left else "CLOSED", None if left else ctx.ts, lid))
    ctx.event("LOAN_REPAID", account_id=aid, ref_type="loan", ref_id=lid, kind=PB_KIND, currency="USD", amount_c=amount_c, principal_left_c=left, closed=left == 0, entry_id=entry_id)
    return {"loan_id": lid, "entry_id": entry_id, "amount_c": amount_c, "financed_left_c": left, "closed": left == 0, "settled_c": balance(ctx.conn, aid, "1010", "USD"), "memo": memo}


def register(bus) -> None:
    bus.register("credit.loan_draw", h_loan_draw)
    bus.register("credit.loan_repay", h_loan_repay)
    bus.register("credit.loan_accrue", h_loan_accrue)
    bus.register("credit.loan_mature", h_loan_mature)
    bus.register("credit.pb_draw", h_pb_draw)
    bus.register("credit.pb_repay", h_pb_repay)


__all__ = ["LENDER", "LOAN_KIND", "PB_KIND", "ACCRUAL_KIND", "loan_row", "loan_view", "loans_of", "accrued_c", "principal_at_close_c", "accrual_state", "nights_due",
           "policy_series", "index_observation", "loan_rate", "accrual_lines_loan", "post_loan_accrual", "catch_up_accruals", "pb_loan_id", "pb_schedule", "headroom_c",
           "pb_draw", "pb_draw_for_funding",
           "h_loan_draw", "h_loan_repay", "h_loan_accrue", "h_loan_mature", "h_pb_draw", "h_pb_repay", "register"]
