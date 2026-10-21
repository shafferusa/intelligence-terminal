"""Dividends and payments in lieu from Yahoo chart events; split notices (TERMINAL_DESIGN.md §2.12 products/dividends.py,
§5.4 posting table, §11.2 DIVIDEND / DIVPAY, §13.15, §19.19; TERMINAL_DECISIONS.md §8 item 3; TERMINAL_WORKPLAN.md WP-18).

Conventions stated here (each is a §1.3 sentence or a rule the tests pin)
- SOURCE OF TRUTH: a dividend exists for this terminal only as a ``dividends`` row, and those rows are written only by the
  quote worker's ``dividends.upsert`` light command from ``YahooChart.events`` (``quotes/worker.py``; the refresher's
  ``dividends`` slot, daily after 18:00 ET and at startup, for every name held long or short in an OPEN lifetime).
  Yahoo gives the ex-date and the amount only — never a pay date — so ``pay_date`` stays NULL and the convention below
  applies.  A handler asked to post without a row answers 400 ``NO_DIVIDEND_ROW``: nothing is invented, nothing posts.
- DIV_EX (``div.ex``, key ``{account}:{instrument}:{ex_date}``, idem ``div:{key}``, business date = the ex-date):
  the entitled position is the position at the PRIOR CLOSE — ``v_positions.qty`` less the signed fills whose
  ``trade_date`` is on or after the ex-date (BUY +, SELL −; a short sale is a SELL with a SHORT lot) — so shares bought
  on the ex-date earn nothing and shares sold on it still do.  Amount ``to_c(|qty| × amount)`` (HALF_EVEN cents).
  Long: ``Dr 1210 Dividends receivable / Cr 4200 Dividend income``; short: ``Dr 5400 Payments in lieu / Cr 2320
  Payments in lieu payable`` (a financing cost, §5.1 bucket financing_cost).  A zero position at the prior close is a
  terminal DONE no-op (nothing posts).  The handler refuses to run before the ex-date (400 ``EX_DATE_NOT_REACHED``).
- DIV_PAY (``div.pay``, same key, idem ``divpay:{key}``, business date = the pay date): cash on ``pay_date`` when Yahoo
  recorded one, else the ex-date + 15 business days of the NYSE calendar (sentence DIVIDEND; ``pay_date_for``).  The
  amount paid is the amount the ex-date entry accrued — read back from that entry, never recomputed from a position
  that may have changed since.  Long: ``Dr 1010 / Cr 1210``; short: the payment is funded first (``execution.fund``,
  immediate, §11.6 overdraft rule: an unfundable payment is still posted under an open ERROR ``FUNDING_SHORTFALL``),
  then ``Dr 2320 / Cr 1010``.  Before the pay date: 400 ``PAY_DATE_NOT_REACHED``.  Without the ex-date entry: SKIPPED
  ``EX_NOT_POSTED`` (retried by the scheduler; one WARN attention per key) unless the ex-date key completed as a no-op,
  which makes the pay key a no-op too.
- KEYS (``ex_keys`` / ``pay_keys(conn, now_ts)``, the WP-08 ProductJob helpers, field order ``(account_id,
  instrument_id, ex_date)``): derived from state, so a catch-up after downtime is automatic — every ``dividends`` row
  with ``ex_date <= today`` (on the day itself from 00:40 ET; ``DUE_ET``) × every OPEN account of an OPEN lifetime with
  a non-zero position at the prior close, minus the keys already DONE in ``jobs_done``; pay keys exist from the pay
  date (00:40 ET) for the keys whose ex-date entry was posted and whose DIVPAY is not DONE.  SKIPPED keys come back.
- JOBS: the handlers write their own ``jobs_done`` row in the same transaction (DONE for a posting and for terminal
  no-ops, SKIPPED for a pending outcome); a rerun of a DONE key answers ``already=True`` and posts nothing; the bus
  replays an identical request id, so a retry needs a new one (``job:{JOB}:{key}:r{n}`` from the scheduler).
- SPLIT (``div.split_notice``, light): a Yahoo split event raises one open WARN ``SPLIT_MANUAL_REVIEW`` per account
  holding the name and does nothing else — lots are NOT adjusted in this build (§13.9, "manual review"); the worker's
  ``dividends.upsert`` raises the same item inline, so the two never duplicate an open notice.
- LABELS: every journal memo, ``jobs_done`` note and position note names the convention (``tables.CONVENTIONS
  ["DIVIDEND"]``) and says whether the pay date is Yahoo's or the convention's; ``position_notes`` lists the business
  days of the convention count and the weekday holidays it skipped (§19.19: "11-26 is Thanksgiving Day").
- POSITION ROW: ``income_pending_c`` = natural balance of 1210 − natural balance of 2320 for the instrument (exactly
  what ``accounting`` reports on the position row); ``position_notes`` gives the row's note strings for the API.
- NO NETWORK in handlers: ``events_for`` (the §2.12 helper) asks the bound Yahoo provider and is for the worker /
  scheduler side only; handlers read the ``dividends`` table.
- Money is integer cents / Decimal; ``amount`` is the per-share Decimal as Yahoo quoted it (``qprice``).
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, List, Optional, Tuple

from .. import execution
from ..books import account_row
from ..clock import epoch_of, ny_of
from ..commands import RejectError, TxContext
from ..ledger import Entry, balance, cr, dr
from ..tables import CONVENTIONS, EQUITY_KINDS
from ..units import fmt_money, qprice, to_c

JOB_EX = "DIVIDEND"
JOB_PAY = "DIVPAY"
SOURCE = "dividends"
CONVENTION_KEY = "DIVIDEND"
CONVENTION = CONVENTIONS[CONVENTION_KEY]
PAY_LAG_BDAYS = 15              # the convention: cash 15 NYSE business days after the ex-date when Yahoo gives no pay date
DUE_ET = "00:40"                # DIVIDEND runs at 00:40 ET on the ex-date; DIVPAY at 00:40 ET on the pay date (§11.2)
SPLIT_CODE = "SPLIT_MANUAL_REVIEW"
CODE_EX_NOT_POSTED = "EX_NOT_POSTED"
DIVIDEND_KINDS = EQUITY_KINDS   # the kinds the worker fetches events for (EQUITY, ETF, ADR, REIT, PREFERRED)
NOTE_LOOKBACK_DAYS = 60
NOTE_AHEAD_DAYS = 120

_services = None                # the runtime bound by register(bus, services): its calendar serves the key helpers
_fallback_cal = None


# ---- pure helpers ---------------------------------------------------------------------------------------------------------
def entitled_c(qty: int, amount) -> int:
    """``to_c(|qty| × amount)``: 1,000 × 0.26 = 260.00 -> 26000; 3 × 0.125 = 0.375 -> 0.38 (HALF_EVEN); 1 × 0.125 -> 0.12."""
    return to_c(Decimal(abs(int(qty))) * qprice(amount))


def pay_date_for(cal, ex_date: date, pay_date: Optional[str]) -> Tuple[date, bool]:
    """``(pay date, is_convention)``: Yahoo's recorded pay date when the row carries one, else ex + 15 business days."""
    if pay_date:
        return date.fromisoformat(str(pay_date)), False
    return cal.add_business_days(ex_date, PAY_LAG_BDAYS), True


def holiday_name(cal, d: date) -> str:
    """The calendar file's name for a holiday (``TradingCalendar`` keeps the file spec but has no public name lookup;
    a missing name reads "a holiday", never a guess)."""
    spec = getattr(cal, "_file_years", {}).get(d.year) if hasattr(cal, "_file_years") else None
    if isinstance(spec, dict):
        for h in spec.get("holidays") or []:
            if str(h.get("date")) == d.isoformat():
                return str(h.get("name") or "a holiday")
    return "a holiday"


def business_day_listing(cal, ex_date: date, n: int = PAY_LAG_BDAYS) -> Tuple[date, str, str]:
    """``(pay date, listing, skipped)`` of the ``n`` business days after ``ex_date``: a run of three or more consecutive
    calendar days is written as a range, shorter runs day by day (§19.19: ``11-10…11-13, 11-16…11-20, 11-23…11-25,
    11-27, 11-30, 12-01``); weekday holidays inside the count are named (``11-26 is Thanksgiving Day``)."""
    days: List[date] = []
    skipped: List[str] = []
    d = ex_date
    while len(days) < n:
        d += timedelta(days=1)
        if cal.is_business_day(d):
            days.append(d)
        elif d.weekday() < 5:
            skipped.append(f"{d.strftime('%m-%d')} is {holiday_name(cal, d)}")
    groups: List[List[date]] = []
    for x in days:
        if groups and (x - groups[-1][-1]).days == 1:
            groups[-1].append(x)
        else:
            groups.append([x])
    parts: List[str] = []
    for g in groups:
        if len(g) >= 3:
            parts.append(f"{g[0].strftime('%m-%d')}…{g[-1].strftime('%m-%d')}")
        else:
            parts.extend(x.strftime("%m-%d") for x in g)
    return days[-1], ", ".join(parts), "; ".join(skipped)


def pay_label(cal, ex_date: date, pay_date: Optional[str]) -> Tuple[date, bool, str]:
    """``(pay date, is_convention, label)`` — "pays 2026-12-01 (pay date from Yahoo)" or "pays 2026-12-01 (convention:
    ex-date + 15 business days)"."""
    pd, conv = pay_date_for(cal, ex_date, pay_date)
    label = f"pays {pd.isoformat()} (" + (f"convention: ex-date + {PAY_LAG_BDAYS} business days" if conv else "pay date from Yahoo") + ")"
    return pd, conv, label


def signed_fill(side: str, qty: int) -> int:
    return int(qty) if side == "BUY" else -int(qty)


def position_at_prior_close(conn: sqlite3.Connection, account_id: str, instrument_id: str, ex_date: str) -> Tuple[int, int]:
    """``(qty at the prior close, signed fills on or after the ex-date)``: the current ``v_positions`` quantity less the
    fills dated on or after ``ex_date`` (fills.trade_date is the New York trade date; BUY +, SELL −)."""
    row = conn.execute("SELECT qty FROM v_positions WHERE account_id=? AND instrument_id=?", (account_id, instrument_id)).fetchone()
    now_qty = int(row["qty"] or 0) if row is not None else 0
    since = 0
    for f in conn.execute("SELECT side, qty FROM fills WHERE account_id=? AND instrument_id=? AND trade_date >= ?", (account_id, instrument_id, str(ex_date))).fetchall():
        since += signed_fill(f["side"], int(f["qty"]))
    return now_qty - since, since


# ---- services / rows -----------------------------------------------------------------------------------------------------------
def _svc(ctx: TxContext, services=None):
    s = services if services is not None else ctx.services
    if s is None:
        s = _services
    if s is None:
        raise RejectError("NO_SERVICES", "the command context carries no services bundle")
    return s


def _cal(services=None):
    """The calendar of the bound runtime (``register``), else the repo's NYSE calendar built once (key helpers run with
    a bare connection)."""
    global _fallback_cal
    s = services if services is not None else _services
    cal = getattr(s, "cal", None) if s is not None else None
    if cal is not None:
        return cal
    if _fallback_cal is None:
        from .. import config
        from ..calendar import TradingCalendar
        _fallback_cal = TradingCalendar(config.holidays_path())
    return _fallback_cal


def _open_account(conn: sqlite3.Connection, account_id: str):
    acct = account_row(conn, account_id)
    if acct is None:
        raise RejectError("ACCOUNT_UNKNOWN", f"account {account_id} does not exist")
    if acct["status"] != "OPEN":
        raise RejectError("ACCOUNT_CLOSED", f"account {account_id} is closed")
    return acct


def _instrument(services, conn: sqlite3.Connection, iid: str):
    inst = services.instruments.get(iid, conn)
    if inst is None:
        raise RejectError("INSTRUMENT_UNKNOWN", f"instrument {iid} is not in the master")
    if inst.kind not in DIVIDEND_KINDS:
        raise RejectError("NOT_A_DIVIDEND_KIND", f"{iid} is a {inst.kind}; dividends and payments in lieu apply to {', '.join(DIVIDEND_KINDS)} only")
    return inst


def dividend_row(conn: sqlite3.Connection, instrument_id: str, ex_date: str):
    return conn.execute("SELECT instrument_id, ex_date, amount, pay_date, source, fetched_ts FROM dividends WHERE instrument_id=? AND ex_date=?",
                        (instrument_id, str(ex_date))).fetchone()


def _require_row(conn: sqlite3.Connection, iid: str, ex_date: str):
    row = dividend_row(conn, iid, ex_date)
    if row is None:
        raise RejectError("NO_DIVIDEND_ROW", f"no dividends row for {iid} ex {ex_date}: rows come only from Yahoo chart events (the worker's dividends.upsert); "
                          "nothing posts without one")
    return row


def _key(payload: dict) -> Tuple[str, str, str]:
    try:
        account, iid, ex = str(payload["account_id"]), str(payload["instrument_id"]), str(payload["ex_date"])
    except KeyError as e:
        raise RejectError("BAD_PAYLOAD", f"missing {e.args[0]}: the payload needs account_id, instrument_id and ex_date") from e
    try:
        date.fromisoformat(ex)
    except ValueError as e:
        raise RejectError("BAD_PAYLOAD", f"ex_date {ex!r} is not an ISO date") from e
    return account, iid, ex


# ---- jobs_done / attention helpers (the futures module's shape) --------------------------------------------------------------------
def _job_row(conn: sqlite3.Connection, job: str, key: str):
    return conn.execute("SELECT status, note, run_ts FROM jobs_done WHERE job=? AND key=?", (job, key)).fetchone()


def _job_done(ctx: TxContext, job: str, key: str, status: str, note: str) -> None:
    ctx.conn.execute("INSERT INTO jobs_done(job, key, run_ts, command_id, status, note) VALUES (?,?,?,?,?,?)"
                     " ON CONFLICT(job, key) DO UPDATE SET run_ts=excluded.run_ts, command_id=excluded.command_id, status=excluded.status, note=excluded.note",
                     (job, key, ctx.ts, ctx.command_id, status, note[:500]))


def _resolve_attention(ctx: TxContext, codes: Tuple[str, ...], ref_id: str) -> int:
    n = 0
    for code in codes:
        cur = ctx.conn.execute("UPDATE attention SET resolved_ts=? WHERE code=? AND ref_id=? AND resolved_ts IS NULL", (ctx.ts, code, ref_id))
        n += cur.rowcount or 0
    return n


def _skip(ctx: TxContext, job: str, key: str, code: str, message: str, account_id: str, iid: str, details: Optional[dict] = None) -> dict:
    """A PENDING outcome: jobs_done SKIPPED (the key stays due), one open WARN attention per (code, key), an event."""
    _job_done(ctx, job, key, "SKIPPED", f"{code}: {message}")
    existing = ctx.conn.execute("SELECT id FROM attention WHERE code=? AND ref_id=? AND resolved_ts IS NULL", (code, key)).fetchone()
    attention_id = int(existing["id"]) if existing else ctx.attention("WARN", SOURCE, code, message,
                                                                          dict(details or {}, account_id=account_id, ref_type="dividend", ref_id=key, auto_resolve=1))
    ctx.event("JOB_SKIPPED", account_id=account_id, instrument_id=iid, ref_type="job", ref_id=key, job=job, code=code, message=message)
    return {"status": "SKIPPED", "skipped": True, "retry": True, "code": code, "message": message, "key": key, "attention_id": attention_id}


def _done_noop(ctx: TxContext, job: str, key: str, code: str, note: str) -> dict:
    """A TERMINAL no-op: DONE with the reason, nothing posted."""
    _job_done(ctx, job, key, "DONE", f"{code}: {note}")
    return {"status": "DONE", "skipped": True, "posted": False, "code": code, "note": note, "key": key}


def _already(row, key: str) -> dict:
    return {"status": "DONE", "already": True, "skipped": True, "posted": False, "key": key, "note": row["note"], "run_ts": int(row["run_ts"])}


# ---- the ex-date entry ------------------------------------------------------------------------------------------------------------
def ex_entry(conn: sqlite3.Connection, key: str):
    """The ex-date journal entry of ``key`` (idem ``div:{key}``) with its accrued amount, side and currency, else None."""
    e = conn.execute("SELECT entry_id, business_date, memo FROM journal_entries WHERE idem_key=?", (f"div:{key}",)).fetchone()
    if e is None:
        return None
    lines = conn.execute("SELECT gl, currency, amount_c, instrument_id FROM journal_lines WHERE entry_id=? ORDER BY line_no", (int(e["entry_id"]),)).fetchall()
    out = {"entry_id": int(e["entry_id"]), "business_date": e["business_date"], "memo": e["memo"], "side": None, "amount_c": 0, "currency": None, "instrument_id": None}
    for ln in lines:
        if ln["gl"] == "1210" and int(ln["amount_c"]) > 0:
            out.update(side="LONG", amount_c=int(ln["amount_c"]), currency=ln["currency"], instrument_id=ln["instrument_id"])
        elif ln["gl"] == "2320" and int(ln["amount_c"]) < 0:
            out.update(side="SHORT", amount_c=-int(ln["amount_c"]), currency=ln["currency"], instrument_id=ln["instrument_id"])
    return out


# ---- handlers ---------------------------------------------------------------------------------------------------------------------
def h_ex(ctx: TxContext, payload: dict) -> dict:
    """DIVIDEND for one ``(account, instrument, ex_date)`` — module docstring DIV_EX.  Idempotent per key."""
    services = _svc(ctx)
    conn = ctx.conn
    account, iid, ex = _key(payload)
    key = f"{account}:{iid}:{ex}"
    idem = f"div:{key}"
    _open_account(conn, account)
    row = _require_row(conn, iid, ex)
    inst = _instrument(services, conn, iid)
    done = _job_row(conn, JOB_EX, key)
    if done is not None and done["status"] == "DONE":
        return _already(done, key)
    ex_d = date.fromisoformat(ex)
    today = ny_of(ctx.ts).date()
    if today < ex_d:
        raise RejectError("EX_DATE_NOT_REACHED", f"{iid}: the ex-date {ex} is after today ({today.isoformat()}); the income is posted on the ex-date, never before")
    qty_prior, since = position_at_prior_close(conn, account, iid, ex)
    if qty_prior == 0:
        return _done_noop(ctx, JOB_EX, key, "NO_POSITION_AT_PRIOR_CLOSE",
                          f"{account} held no {iid} at the close before {ex}" + (f" (fills on or after the ex-date: {since:+d})" if since else ""))
    amount = qprice(row["amount"])
    if amount <= 0:
        return _done_noop(ctx, JOB_EX, key, "ZERO_AMOUNT", f"{iid} ex {ex}: Yahoo's amount is {amount}")
    cents = entitled_c(qty_prior, amount)
    if cents == 0:
        return _done_noop(ctx, JOB_EX, key, "ROUNDS_TO_ZERO", f"{iid} ex {ex}: {abs(qty_prior)} × {amount} rounds to 0.00")
    cal = ctx.cal if ctx.cal is not None else _cal(services)
    pay_d, conv, label = pay_label(cal, ex_d, row["pay_date"])
    prior_close = cal.prev_business_day(ex_d).isoformat()
    ccy = inst.currency
    if qty_prior > 0:
        kind_txt = "dividend"
        memo = (f"{inst.symbol} dividend {amount} × {qty_prior:,} held at the prior close ({prior_close}) = {fmt_money(cents)} receivable, ex {ex}; {label}; "
                f"{CONVENTION_KEY}: {CONVENTION}")
        lines = [dr(account, "1210", ccy, cents, instrument_id=iid, memo=f"dividend receivable ex {ex}"),
                 cr(account, "4200", ccy, cents, instrument_id=iid, memo=f"dividend income {amount}/share × {qty_prior:,}")]
    else:
        kind_txt = "payment in lieu"
        memo = (f"{inst.symbol} payment in lieu {amount} × {abs(qty_prior):,} short at the prior close ({prior_close}) = {fmt_money(cents)} payable, ex {ex}; "
                f"{label}; {CONVENTION_KEY}: {CONVENTION}")
        lines = [dr(account, "5400", ccy, cents, instrument_id=iid, memo=f"payment in lieu {amount}/share × {abs(qty_prior):,} short"),
                 cr(account, "2320", ccy, cents, instrument_id=iid, memo=f"payment in lieu payable ex {ex}")]
    entry_id = ctx.post(Entry("DIVIDEND", memo, lines, idem, ref_type="dividend", ref_id=key, business_date=ex))
    _job_done(ctx, JOB_EX, key, "DONE", f"{kind_txt} {fmt_money(cents)} on {abs(qty_prior):,} {inst.symbol} at {amount} (ex {ex}; {label}; source {row['source']})")
    ctx.event("DIVIDEND_ACCRUED", account_id=account, instrument_id=iid, ref_type="dividend", ref_id=key, kind=kind_txt, amount_c=cents, qty=qty_prior,
              amount=str(amount), ex_date=ex, pay_date=pay_d.isoformat(), pay_date_convention=conv, source=row["source"])
    ctx.touched_instruments.add((account, iid))
    return {"status": "DONE", "posted": True, "key": key, "entry_id": entry_id, "kind": kind_txt, "side": "LONG" if qty_prior > 0 else "SHORT", "qty": qty_prior,
            "fills_since_ex": since, "amount": str(amount), "amount_c": cents, "currency": ccy, "ex_date": ex, "pay_date": pay_d.isoformat(),
            "pay_date_convention": conv, "pay_label": label, "source": row["source"], "convention": CONVENTION, "idem_key": idem}


def h_pay(ctx: TxContext, payload: dict) -> dict:
    """DIVPAY for one key — module docstring DIV_PAY.  Idempotent per key."""
    services = _svc(ctx)
    conn = ctx.conn
    account, iid, ex = _key(payload)
    key = f"{account}:{iid}:{ex}"
    idem = f"divpay:{key}"
    _open_account(conn, account)
    row = _require_row(conn, iid, ex)
    inst = _instrument(services, conn, iid)
    done = _job_row(conn, JOB_PAY, key)
    if done is not None and done["status"] == "DONE":
        return _already(done, key)
    cal = ctx.cal if ctx.cal is not None else _cal(services)
    ex_d = date.fromisoformat(ex)
    pay_d, conv, label = pay_label(cal, ex_d, row["pay_date"])
    today = ny_of(ctx.ts).date()
    if today < pay_d:
        raise RejectError("PAY_DATE_NOT_REACHED", f"{iid} ex {ex}: {label}; today is {today.isoformat()} — cash moves on the pay date, never before")
    accrued = ex_entry(conn, key)
    if accrued is None or not accrued["side"]:
        ex_done = _job_row(conn, JOB_EX, key)
        if ex_done is not None and ex_done["status"] == "DONE":
            return _done_noop(ctx, JOB_PAY, key, "NOTHING_ACCRUED", f"the ex-date key completed without a posting ({ex_done['note']})")
        return _skip(ctx, JOB_PAY, key, CODE_EX_NOT_POSTED, f"{inst.symbol} ex {ex}: the ex-date entry div:{key} has not been posted yet; the pay waits for it",
                     account, iid, {"ex_date": ex, "pay_date": pay_d.isoformat()})
    cents, ccy = int(accrued["amount_c"]), str(accrued["currency"] or inst.currency)
    pay_s = pay_d.isoformat()
    funding = None
    if accrued["side"] == "LONG":
        memo = f"{inst.symbol} dividend {fmt_money(cents)} received (ex {ex}; {label}; {CONVENTION_KEY}: {CONVENTION})"
        lines = [dr(account, "1010", ccy, cents, memo=f"dividend received ex {ex}"), cr(account, "1210", ccy, cents, instrument_id=iid, memo="dividend receivable collected")]
        entry_id = ctx.post(Entry("DIVIDEND", memo, lines, idem, ref_type="dividend", ref_id=key, business_date=pay_s))
        execution.auto_resolve_shortfall(ctx, account, ccy)
    else:
        funding = execution.fund(ctx, account, ccy, cents, "pay", idem, None, immediate=True, overdraft=True, services=services)
        memo = f"{inst.symbol} payment in lieu {fmt_money(cents)} paid (ex {ex}; {label}; {CONVENTION_KEY}: {CONVENTION})"
        lines = [dr(account, "2320", ccy, cents, instrument_id=iid, memo="payment in lieu payable settled"), cr(account, "1010", ccy, cents, memo=f"payment in lieu paid ex {ex}")]
        entry_id = ctx.post(Entry("DIVIDEND", memo, lines, idem, ref_type="dividend", ref_id=key, business_date=pay_s))
    kind_txt = "dividend" if accrued["side"] == "LONG" else "payment in lieu"
    unfunded = funding is not None and not funding.funded
    _job_done(ctx, JOB_PAY, key, "DONE", f"{kind_txt} {fmt_money(cents)} {'received' if accrued['side'] == 'LONG' else 'paid'} on {pay_s} ({label})"
              + ("; UNFUNDED (overdraft rule)" if unfunded else ""))
    _resolve_attention(ctx, (CODE_EX_NOT_POSTED,), key)
    ctx.event("DIVIDEND_PAID", account_id=account, instrument_id=iid, ref_type="dividend", ref_id=key, kind=kind_txt, amount_c=cents, pay_date=pay_s,
              pay_date_convention=conv, ex_entry_id=accrued["entry_id"])
    ctx.touched_instruments.add((account, iid))
    return {"status": "DONE", "posted": True, "key": key, "entry_id": entry_id, "ex_entry_id": accrued["entry_id"], "kind": kind_txt, "side": accrued["side"],
            "amount_c": cents, "currency": ccy, "ex_date": ex, "pay_date": pay_s, "pay_date_convention": conv, "pay_label": label, "convention": CONVENTION,
            "idem_key": idem, "funded": None if funding is None else funding.funded, "unfunded_c": 0 if funding is None else int(funding.unfunded_c)}


def holders(conn: sqlite3.Connection, instrument_id: str) -> List[str]:
    """OPEN accounts of OPEN lifetimes with a non-zero position in the name."""
    return [r["account_id"] for r in conn.execute(
        "SELECT v.account_id FROM v_positions v JOIN accounts a ON a.account_id=v.account_id JOIN lifetimes l ON l.lifetime_id=a.lifetime_id"
        " WHERE v.instrument_id=? AND v.qty <> 0 AND a.status='OPEN' AND l.status='OPEN' ORDER BY v.account_id", (instrument_id,)).fetchall()]


def h_split_notice(ctx: TxContext, payload: dict) -> dict:
    """``{instrument_id, date, ratio, held_accounts?}`` -> one open WARN ``SPLIT_MANUAL_REVIEW`` per holding account; no
    posting, lots unchanged (module docstring SPLIT).  Light: it touches no ledger table."""
    iid = str(payload.get("instrument_id") or "")
    d = str(payload.get("date") or "")
    ratio = str(payload.get("ratio") or "")
    if not iid or not d or not ratio:
        raise RejectError("BAD_PAYLOAD", "the split notice needs instrument_id, date and ratio")
    ref = f"{iid}:{d}"
    accounts = list(payload.get("held_accounts") or holders(ctx.conn, iid))
    opened: List[int] = []
    for aid in accounts:
        row = ctx.conn.execute("SELECT id FROM attention WHERE code=? AND ref_id=? AND account_id=? AND resolved_ts IS NULL", (SPLIT_CODE, ref, aid)).fetchone()
        if row is None:
            opened.append(ctx.attention("WARN", SOURCE, SPLIT_CODE, f"{iid}: split {ratio} on {d} — lots are not adjusted in this build; review the position manually",
                                        {"account_id": aid, "ref_type": "split", "ref_id": ref, "ratio": ratio, "date": d}))
    return {"status": "DONE", "posted": False, "instrument_id": iid, "date": d, "ratio": ratio, "accounts": accounts, "notices": len(opened), "attention_ids": opened,
            "note": "lots are not adjusted in this build (manual review)"}


# ---- key helpers for the WP-08 ProductJobs ------------------------------------------------------------------------------------------
def _today_and_gate(now_ts: int) -> Tuple[date, bool]:
    now = int(now_ts)
    today = ny_of(now).date()
    return today, now >= epoch_of(today, DUE_ET)


def _candidate_accounts(conn: sqlite3.Connection, iid: str, ex_date: str) -> List[str]:
    """OPEN accounts of OPEN lifetimes with any fill in the name before the ex-date (the only way to hold it at the prior close)."""
    return [r["account_id"] for r in conn.execute(
        "SELECT DISTINCT f.account_id FROM fills f JOIN accounts a ON a.account_id=f.account_id JOIN lifetimes l ON l.lifetime_id=a.lifetime_id"
        " WHERE f.instrument_id=? AND f.trade_date < ? AND a.status='OPEN' AND l.status='OPEN' ORDER BY f.account_id", (iid, str(ex_date))).fetchall()]


def ex_keys(conn: sqlite3.Connection, now_ts: int) -> List[Tuple[str, str, str]]:
    """``(account_id, instrument_id, ex_date)`` triples whose DIVIDEND is due (module docstring KEYS)."""
    today, after_due = _today_and_gate(now_ts)
    out: List[Tuple[str, str, str]] = []
    for r in conn.execute("SELECT instrument_id, ex_date FROM dividends WHERE ex_date <= ? ORDER BY ex_date, instrument_id", (today.isoformat(),)).fetchall():
        if r["ex_date"] == today.isoformat() and not after_due:
            continue
        for account in _candidate_accounts(conn, r["instrument_id"], r["ex_date"]):
            key = f"{account}:{r['instrument_id']}:{r['ex_date']}"
            done = _job_row(conn, JOB_EX, key)
            if done is not None and done["status"] == "DONE":
                continue
            qty_prior, _ = position_at_prior_close(conn, account, r["instrument_id"], r["ex_date"])
            if qty_prior != 0:
                out.append((account, r["instrument_id"], r["ex_date"]))
    return out


def pay_keys(conn: sqlite3.Connection, now_ts: int, cal=None) -> List[Tuple[str, str, str]]:
    """``(account_id, instrument_id, ex_date)`` triples whose DIVPAY is due: the pay date (Yahoo's or the convention's)
    is today from 00:40 ET or past, the ex-date entry was posted, DIVPAY not DONE (module docstring KEYS)."""
    today, after_due = _today_and_gate(now_ts)
    cal = cal if cal is not None else _cal()
    out: List[Tuple[str, str, str]] = []
    for r in conn.execute("SELECT instrument_id, ex_date, pay_date FROM dividends WHERE ex_date <= ? ORDER BY ex_date, instrument_id", (today.isoformat(),)).fetchall():
        pd, _ = pay_date_for(cal, date.fromisoformat(r["ex_date"]), r["pay_date"])
        if pd > today or (pd == today and not after_due):
            continue
        for account in _candidate_accounts(conn, r["instrument_id"], r["ex_date"]):
            key = f"{account}:{r['instrument_id']}:{r['ex_date']}"
            done = _job_row(conn, JOB_PAY, key)
            if done is not None and done["status"] == "DONE":
                continue
            acc = ex_entry(conn, key)
            if acc is None or not acc["side"]:
                continue
            out.append((account, r["instrument_id"], r["ex_date"]))
    return out


# ---- position-row notes / views -------------------------------------------------------------------------------------------------------
def income_pending_c(conn: sqlite3.Connection, account_id: str, instrument_id: str) -> int:
    """Dividends receivable (1210) less payments in lieu payable (2320) for the instrument — the position row's
    ``income_pending_c`` (negative for a short with an unpaid payment in lieu)."""
    return balance(conn, account_id, "1210", None, instrument_id) - balance(conn, account_id, "2320", None, instrument_id)


def row_note(cal, row, qty: Optional[int] = None, state: Optional[str] = None) -> str:
    """One row's note: "dividend 0.26 ex 2026-11-09, pays 2026-12-01 (convention: ex + 15 business days: 11-10…11-13, …;
    11-26 is Thanksgiving Day)" — "payment in lieu" when ``qty`` is short; ``state`` ("accrued 260.00 receivable",
    "paid", "upcoming") appended when given."""
    ex_d = date.fromisoformat(row["ex_date"])
    amount = qprice(row["amount"])
    kind_txt = "payment in lieu" if (qty is not None and qty < 0) else "dividend"
    if row["pay_date"]:
        tail = f"pays {row['pay_date']} (pay date from Yahoo)"
    else:
        pd, listing, skipped = business_day_listing(cal, ex_d, PAY_LAG_BDAYS)
        tail = f"pays {pd.isoformat()} (convention: ex + {PAY_LAG_BDAYS} business days: {listing}" + (f"; {skipped}" if skipped else "") + ")"
    txt = f"{kind_txt} {amount} ex {row['ex_date']}, {tail}"
    return txt + (f" — {state}" if state else "")


def position_notes(conn: sqlite3.Connection, cal, account_id: str, instrument_id: str, today: date) -> List[str]:
    """The position row's dividend notes (module docstring POSITION ROW): one per ``dividends`` row of the name within
    −60 / +120 days of ``today`` with its state from ``jobs_done`` / the ledger, plus open split notices."""
    lo, hi = (today - timedelta(days=NOTE_LOOKBACK_DAYS)).isoformat(), (today + timedelta(days=NOTE_AHEAD_DAYS)).isoformat()
    pos = conn.execute("SELECT qty FROM v_positions WHERE account_id=? AND instrument_id=?", (account_id, instrument_id)).fetchone()
    qty_now = int(pos["qty"] or 0) if pos is not None else 0
    out: List[str] = []
    for r in conn.execute("SELECT instrument_id, ex_date, amount, pay_date, source FROM dividends WHERE instrument_id=? AND ex_date BETWEEN ? AND ? ORDER BY ex_date",
                          (instrument_id, lo, hi)).fetchall():
        key = f"{account_id}:{instrument_id}:{r['ex_date']}"
        acc = ex_entry(conn, key)
        pay_done = _job_row(conn, JOB_PAY, key)
        ex_done = _job_row(conn, JOB_EX, key)
        if pay_done is not None and pay_done["status"] == "DONE" and acc is not None and acc["side"]:
            qty, state = (1 if acc["side"] == "LONG" else -1), f"{fmt_money(acc['amount_c'])} {'received' if acc['side'] == 'LONG' else 'paid'}"
        elif acc is not None and acc["side"]:
            qty = 1 if acc["side"] == "LONG" else -1
            state = f"accrued {fmt_money(acc['amount_c'])} {'receivable' if acc['side'] == 'LONG' else 'payable'}"
        elif ex_done is not None and ex_done["status"] == "DONE":
            qty, state = qty_now, "no entitlement (nothing held at the prior close)"
        else:
            qty, state = qty_now, ("upcoming" if r["ex_date"] > today.isoformat() else "pending")
        out.append(row_note(cal, r, qty, state) + f" [{r['source']}]")
    for a in conn.execute("SELECT message FROM attention WHERE code=? AND account_id=? AND ref_id LIKE ? AND resolved_ts IS NULL ORDER BY id",
                          (SPLIT_CODE, account_id, f"{instrument_id}:%")).fetchall():
        out.append(f"split notice: {a['message']}")
    return out


def events_for(services, inst) -> List[Any]:
    """§2.12: the Yahoo chart dividend events of ``inst`` (``[DivEvent(ex_date, amount)]``) from the bound provider
    (``services.providers.yahoo``, else ``services.fp`` in tests).  Worker / scheduler side only — it may do network
    I/O; handlers never call it.  An instrument without a Yahoo symbol or outside ``DIVIDEND_KINDS`` has no events."""
    if inst is None or inst.kind not in DIVIDEND_KINDS or not getattr(inst, "yahoo_symbol", None):
        return []
    ps = getattr(services, "providers", None)
    yahoo = getattr(ps, "yahoo", None) if ps is not None else None
    if yahoo is None or not hasattr(yahoo, "events"):
        fp = getattr(services, "fp", None)
        yahoo = fp if fp is not None and hasattr(fp, "events") else None
    if yahoo is None:
        return []
    return list(yahoo.events(inst.yahoo_symbol).dividends)


def register(bus, services=None) -> None:
    """Register ``div.ex`` / ``div.pay`` (ledger commands) and ``div.split_notice`` (light); remember the runtime so the
    key helpers (called with a bare connection by the scheduler) share its calendar.  The integrator binds this module
    as ``services.dividends`` (the WP-08 ProductJobs look up ``ex_keys`` / ``pay_keys`` there)."""
    global _services
    s = services if services is not None else getattr(bus, "services", None)
    if s is not None:
        _services = s
    bus.register("div.ex", h_ex)
    bus.register("div.pay", h_pay)
    bus.register("div.split_notice", h_split_notice, light=True)


__all__ = ["JOB_EX", "JOB_PAY", "SOURCE", "CONVENTION", "CONVENTION_KEY", "PAY_LAG_BDAYS", "DUE_ET", "SPLIT_CODE", "CODE_EX_NOT_POSTED", "DIVIDEND_KINDS",
           "entitled_c", "pay_date_for", "pay_label", "holiday_name", "business_day_listing", "position_at_prior_close", "dividend_row", "ex_entry", "holders",
           "h_ex", "h_pay", "h_split_notice", "ex_keys", "pay_keys", "income_pending_c", "row_note", "position_notes", "events_for", "register"]
