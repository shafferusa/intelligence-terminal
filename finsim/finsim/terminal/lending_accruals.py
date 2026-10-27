"""Securities-lending accruals and shared primitives (TERMINAL_DESIGN.md §11.3, §12.2, §5.4; the companion of ``lending.py``,
split out to keep that module under the ~900-line guideline — every name here is re-exported by ``lending``).

Conventions stated here
- ACCRUAL (§11.3, cumulative rounding): for ``(kind, ref)`` the ``accrual_state`` row holds the segment (``segment_start``,
  ``base_c``, ``rate``, ``day_basis``, ``accrued_through``, ``cum_c``); for night D ``n = D − segment_start + 1``,
  ``cum_new = to_c(base × rate × n / basis)``, posted = ``cum_new − cum_c``; a changed base or rate restarts the segment
  at D with ``cum 0``; a night already accrued posts 0; a night out of date order raises ACCRUAL_GAP (never lumped).
  The sum of the nightly postings therefore always equals ``to_c(base × rate × n / basis)`` for the segment.
- Borrow fee base = ``qty_outstanding × mark_D`` (the CLOSE of D, else the last CLOSE before D) at the row's tier rate;
  rebate base = the cash collateral at ``max(0, SOFR_D − 0.50 %)`` (0 for T3), both ACT/360.  For an OPEN borrow the
  bases are the row's current quantity and collateral (after the day's collateral mark, as §19.5 works it); for a
  CLOSED borrow (a late accrual) they are the state just before the closing return, taken from its BORROW_RETURNED
  event, and the amounts settle at once (``pay:bfee:{borrow}:late:{night}``).
- RATE_LAG: SOFR_D is the latest published print on or before D and must be at most 10 days old; a missing mark or
  rate RAISES (MARK_UNAVAILABLE / RATE_UNAVAILABLE) — nothing is defaulted; the job skips the night and retries.
- Rates are fractions (``0.003``); ``sofr`` is the published PERCENT (``3.87``), as FRED and ``RateStore`` carry it.
- Accrued fees (2310) and rebates (1410) are per instrument; ``settle_accrued`` pays a given share in one entry
  ``pay:bfee:{borrow}:{suffix}`` (``Dr 2310 / Cr 1010`` and ``Dr 1010 / Cr 1410``), funding a net cash need first
  with the §11.6 overdraft rule.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from typing import List, Optional, Tuple

from . import config, execution, tables
from .clock import accounting_day
from .commands import ConflictError, RejectError, TxContext
from .ledger import Entry, Line, balance, cr, dr
from .units import fmt_money, from_c, qprice, to_c

BASIS_360 = 360
RATE_LAG_MAX_DAYS = 10


def _services(ctx: TxContext, services=None):
    """The services bundle: the explicit argument, else the command context's (``execution._services`` raises
    NO_SERVICES when neither exists — never a silent default)."""
    return execution._services(ctx, services)


def _dec(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


def _pct(rate: Optional[Decimal]) -> str:
    """``0.003 -> '0.30 %'`` for memos and the ticket."""
    if rate is None:
        return "n/a"
    return f"{(_dec(rate) * 100).quantize(Decimal('0.01'))} %"


def _outstanding(b) -> int:
    return int(b["qty"]) - int(b["qty_returned"])


def _borrow(conn, borrow_id: str):
    row = conn.execute("SELECT * FROM borrows WHERE borrow_id=?", (str(borrow_id),)).fetchone()
    if row is None:
        raise RejectError("NOT_FOUND", f"borrow {borrow_id} does not exist")
    return row


def _inst_of(services, conn, instrument_id: str):
    master = getattr(services, "instruments", None) if services is not None else None
    return master.get(instrument_id, conn) if master is not None else None


def rebate_rate(sofr_pct: Optional[Decimal], tier: str) -> Optional[Decimal]:
    """``max(0, SOFR − 0.50 %)`` as a fraction from the published percent; 0 for T3; None when SOFR is unknown."""
    if tier == "T3":
        return Decimal(0)
    if sofr_pct is None:
        return None
    r = _dec(sofr_pct) / Decimal(100) + tables.RATES["rebate"]["spread"]
    return qprice(max(r, tables.RATES["rebate"]["floor"]))


def sofr_for(services, d: Optional[date] = None) -> Optional[Tuple[date, Decimal]]:
    """``(observation date, percent)`` — the latest print (``d`` None) or the RATE_LAG print on or before ``d``."""
    rates = getattr(services, "rates", None)
    if rates is None:
        return None
    if d is None:
        return rates.latest("SOFR") if hasattr(rates, "latest") else None
    return rates.as_of_le("SOFR", d) if hasattr(rates, "as_of_le") else None


def settle_accrued(ctx: TxContext, services, b, fee_c: int, rebate_c: int, suffix: str) -> Optional[int]:
    """Pay accrued borrow fees (``Dr 2310 / Cr 1010``) and receive the accrued rebate (``Dr 1010 / Cr 1410``) in one
    entry ``pay:bfee:{borrow}:{suffix}``; a net cash need is funded first (§11.6 overdraft rule).  None when both are 0."""
    fee_c, rebate_c = int(fee_c), int(rebate_c)
    if fee_c <= 0 and rebate_c <= 0:
        return None
    aid, iid, ccy = b["account_id"], b["instrument_id"], b["currency"]
    need = fee_c - max(rebate_c, 0)
    if need > 0:
        execution.fund(ctx, aid, ccy, need, "borrow", b["borrow_id"], None, immediate=True, overdraft=True, services=services)
    lines: List[Line] = []
    if fee_c > 0:
        lines += [dr(aid, "2310", ccy, fee_c, instrument_id=iid, memo="accrued borrow fees paid"), cr(aid, "1010", ccy, fee_c, memo="borrow fees paid to the lender")]
    if rebate_c > 0:
        lines += [dr(aid, "1010", ccy, rebate_c, memo="rebate received from the lender"), cr(aid, "1410", ccy, rebate_c, instrument_id=iid, memo="accrued rebate received")]
    return ctx.post(Entry("PAY", f"settle accrued borrow fees {fmt_money(max(fee_c, 0))} and rebate {fmt_money(max(rebate_c, 0))} on {iid} ({b['borrow_id']})",
                          lines, f"pay:bfee:{b['borrow_id']}:{suffix}", ref_type="borrow", ref_id=b["borrow_id"]))


def close_mark_for(conn, instrument_id: str, d: str, exact: bool = False) -> Optional[Tuple[Decimal, str]]:
    """The observed CLOSE mark for night ``d``: the CLOSE of ``d``, else (unless ``exact``) the last CLOSE before it
    (weekends use Friday's).  ``(price, note)`` or None — never a default."""
    r = conn.execute("SELECT price, mark_date, source FROM marks WHERE instrument_id=? AND kind='CLOSE' AND mark_date<=? ORDER BY mark_date DESC LIMIT 1",
                     (instrument_id, d)).fetchone()
    if r is None or (exact and r["mark_date"] != d):
        return None
    note = f"CLOSE {r['mark_date']}" + ("" if r["mark_date"] == d else f" carried to {d}") + f" ({r['source']})"
    return qprice(r["price"]), note


def segment_amount(row, base_c: int, rate, basis: int, night: str) -> Tuple[int, str, int]:
    """Pure §11.3 step for one night: ``(posted_c, segment_start, cum_new_c)`` given the current ``accrual_state`` row
    (or None).  A night already accrued posts 0; a night out of date order raises ACCRUAL_GAP; a changed base or rate
    restarts the segment at ``night`` with ``cum 0``."""
    base_c, basis = int(base_c), int(basis)
    rate_q = qprice(rate)
    if row is not None and row["accrued_through"]:
        if row["accrued_through"] >= night:
            return 0, row["segment_start"], int(row["cum_c"])
        nxt = (date.fromisoformat(row["accrued_through"]) + timedelta(days=1)).isoformat()
        if night != nxt:
            raise RejectError("ACCRUAL_GAP", f"night {night} requested but {nxt} is the next unaccrued night (accruals post in date order, never lumped)")
    same = (row is not None and row["accrued_through"] and int(row["base_c"]) == base_c and str(row["rate"]) == str(rate_q) and int(row["day_basis"]) == basis)
    if same:
        start, cum_prev = row["segment_start"], int(row["cum_c"])
    else:
        start, cum_prev = night, 0
    n = (date.fromisoformat(night) - date.fromisoformat(start)).days + 1
    cum_new = to_c(from_c(base_c) * rate_q * Decimal(n) / Decimal(basis))
    return cum_new - cum_prev, start, cum_new


def accrue_segment(ctx: TxContext, kind: str, ref: str, base_c: int, rate, basis: int, night: str) -> int:
    """§11.3 with persistence: compute the night's posting for ``(kind, ref)`` and write the ``accrual_state`` segment.
    Returns the cents to post (0 when the night is already accrued)."""
    conn = ctx.conn
    row = conn.execute("SELECT * FROM accrual_state WHERE kind=? AND ref_id=?", (kind, ref)).fetchone()
    posted, start, cum_new = segment_amount(row, base_c, rate, basis, night)
    if row is not None and row["accrued_through"] and row["accrued_through"] >= night:
        return 0
    conn.execute("INSERT INTO accrual_state(kind, ref_id, segment_start, base_c, rate, day_basis, accrued_through, cum_c) VALUES (?,?,?,?,?,?,?,?)"
                 " ON CONFLICT(kind, ref_id) DO UPDATE SET segment_start=excluded.segment_start, base_c=excluded.base_c, rate=excluded.rate,"
                 " day_basis=excluded.day_basis, accrued_through=excluded.accrued_through, cum_c=excluded.cum_c",
                 (kind, ref, start, int(base_c), str(qprice(rate)), int(basis), night, cum_new))
    return posted


def _fee_base_c(outstanding: int, mark, inst) -> int:
    mult = _dec(getattr(inst, "multiplier", 1) or 1)
    scale = _dec(getattr(inst, "price_scale", 1) or 1)
    return to_c(Decimal(int(outstanding)) * _dec(mark) * mult * scale)


def base_state(conn, b) -> Tuple[int, int, str]:
    """``(qty_outstanding, collateral_c, note)`` the accrual bases come from: the row itself for an OPEN borrow (after
    the day's collateral mark, as §19.5 works it); for a CLOSED borrow (a late accrual) the state just before the
    closing return, read from its BORROW_RETURNED event (the quantity and collateral that return gave back) — the
    ledger's own record, never the zeroed row."""
    if b["status"] == "CLOSED":
        ev = conn.execute("SELECT payload_json FROM events WHERE type='BORROW_RETURNED' AND ref_type='borrow' AND ref_id=? ORDER BY id DESC LIMIT 1",
                          (b["borrow_id"],)).fetchone()
        if ev is not None:
            p = json.loads(ev["payload_json"] or "{}")
            return int(p.get("qty") or 0), int(p.get("collateral_returned_c") or 0), "state before the closing return"
    return _outstanding(b), int(b["collateral_c"]), "current"


def accrual_amounts(conn, b, d: str, mark, sofr_pct, inst=None) -> dict:
    """What night ``d`` would post for a borrow (pure read of ``accrual_state``): fee on ``qty_outstanding × mark`` at
    the row's fee rate, rebate on the cash collateral at ``rebate_rate(sofr, tier)``; the bases come from
    ``base_state``; ``rebate_c`` is None when SOFR is unknown (and the tier is not T3)."""
    outstanding, coll_c, note = base_state(conn, b)
    fee_r = _dec(b["fee_rate"])
    fee_base = _fee_base_c(outstanding, mark, inst)
    reb_r = rebate_rate(sofr_pct, b["tier"])
    fee_row = conn.execute("SELECT * FROM accrual_state WHERE kind='bfee' AND ref_id=?", (b["borrow_id"],)).fetchone()
    reb_row = conn.execute("SELECT * FROM accrual_state WHERE kind='rebate' AND ref_id=?", (b["borrow_id"],)).fetchone()
    fee_c = segment_amount(fee_row, fee_base, fee_r, BASIS_360, d)[0]
    reb_c = None if reb_r is None else segment_amount(reb_row, coll_c, reb_r, BASIS_360, d)[0]
    return {"outstanding": outstanding, "fee_base_c": fee_base, "fee_rate": fee_r, "fee_c": fee_c, "rebate_base_c": coll_c,
            "rebate_rate": reb_r, "rebate_c": reb_c, "base_note": note}


def accrual_lines(ctx, borrow, d: str, mark, sofr) -> List[Line]:
    """§2.12: the fee (``Dr 5300 / Cr 2310``) and rebate (``Dr 1410 / Cr 4350``) lines for night ``d`` — computed, not
    posted and not persisted (``accrue_night`` does both with the §5.4 keys).  ``sofr`` is the published percent."""
    services = getattr(ctx, "services", None)
    inst = _inst_of(services, ctx.conn, borrow["instrument_id"]) if services is not None else None
    a = accrual_amounts(ctx.conn, borrow, d, mark, sofr, inst)
    aid, iid, ccy = borrow["account_id"], borrow["instrument_id"], borrow["currency"]
    lines: List[Line] = []
    if a["fee_c"] > 0:
        lines += [dr(aid, "5300", ccy, a["fee_c"], instrument_id=iid, memo=f"borrow fee night of {d}"), cr(aid, "2310", ccy, a["fee_c"], instrument_id=iid, memo="accrued fee payable")]
    if a["rebate_c"]:
        lines += [dr(aid, "1410", ccy, a["rebate_c"], instrument_id=iid, memo="accrued rebate receivable"), cr(aid, "4350", ccy, a["rebate_c"], instrument_id=iid, memo=f"rebate night of {d}")]
    return lines


def nights_due(conn, b, through: date) -> List[str]:
    """The calendar nights ≤ ``through`` the borrow still has to accrue: from its first night (the accounting day it
    was opened, i.e. open at 17:00 ET of that day) or the night after ``accrued_through``, up to the last night it was
    open (a CLOSED borrow's last night is the day before the accounting day of its close)."""
    first = date.fromisoformat(b["opened_date"])
    last = through
    if b["status"] == "CLOSED" and b["closed_ts"]:
        last = min(last, accounting_day(int(b["closed_ts"])) - timedelta(days=1))
    start = first
    nexts = []
    for kind in ("bfee", "rebate"):
        r = conn.execute("SELECT accrued_through FROM accrual_state WHERE kind=? AND ref_id=?", (kind, b["borrow_id"])).fetchone()
        if r is not None and r["accrued_through"]:
            nexts.append(date.fromisoformat(r["accrued_through"]) + timedelta(days=1))
    if nexts:
        start = max(first, min(nexts))
    out: List[str] = []
    d = start
    while d <= last:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def accrue_night(ctx: TxContext, services, borrow, d: str, mark=None, mark_note: Optional[str] = None, sofr=None, sofr_obs: Optional[str] = None) -> dict:
    """Post night ``d`` for one borrow: ``accr:bfee:{borrow}:{d}`` (``Dr 5300 / Cr 2310``) and ``accr:rebate:{borrow}:{d}``
    (``Dr 1410 / Cr 4350``), each only when its amount is non-zero, after ``accrue_segment`` wrote the segments.
    ``mark`` defaults to the CLOSE of ``d`` or the last CLOSE before it; ``sofr`` (percent) to the RATE_LAG print, which
    must be at most 10 days old.  A CLOSED borrow (late accrual) settles the amounts at once.  Idempotent per night."""
    services = _services(ctx, services)
    conn = ctx.conn
    b = borrow if not isinstance(borrow, str) else _borrow(conn, borrow)
    bid = b["borrow_id"]
    d = str(d)
    first = b["opened_date"]
    last = None
    if b["status"] == "CLOSED" and b["closed_ts"]:
        last = (accounting_day(int(b["closed_ts"])) - timedelta(days=1)).isoformat()
    if d < first or (last is not None and d > last):
        raise RejectError("NIGHT_INVALID", f"borrow {bid} was not open at 17:00 ET on {d} (first night {first}{', last ' + last if last else ''})")
    inst = _inst_of(services, conn, b["instrument_id"])
    if mark is None:
        got = close_mark_for(conn, b["instrument_id"], d)
        if got is None:
            raise RejectError("MARK_UNAVAILABLE", f"no CLOSE mark for {b['instrument_id']} on or before {d}: the accrual for that night waits for MARKS_RECORD (never defaulted)")
        mark, mark_note = got
    mark = qprice(mark)
    if b["tier"] != "T3" and sofr is None:
        got = sofr_for(services, date.fromisoformat(d))
        if got is None or (date.fromisoformat(d) - got[0]).days > RATE_LAG_MAX_DAYS:
            raise RejectError("RATE_UNAVAILABLE", f"no SOFR observation within {RATE_LAG_MAX_DAYS} days on or before {d}: the rebate for that night waits for FRED (never defaulted)")
        sofr, sofr_obs = got[1], got[0].isoformat()
    a = accrual_amounts(conn, b, d, mark, sofr, inst)
    fee_c = accrue_segment(ctx, "bfee", bid, a["fee_base_c"], a["fee_rate"], BASIS_360, d)
    reb_c = accrue_segment(ctx, "rebate", bid, a["rebate_base_c"], a["rebate_rate"], BASIS_360, d)
    aid, iid, ccy = b["account_id"], b["instrument_id"], b["currency"]
    sym = inst.symbol if inst is not None else iid
    fee_entry = reb_entry = None
    if fee_c > 0:
        memo = (f"borrow fee {sym}: tier {b['tier']} {_pct(a['fee_rate'])}, ACT/360 on {a['outstanding']:,} x {mark} = {fmt_money(a['fee_base_c'])}"
                f" ({mark_note or 'mark given'}; {a['base_note']}), night of {d} (ACCRUAL)")
        fee_entry = ctx.post(Entry("ACCRUAL", memo, [dr(aid, "5300", ccy, fee_c, instrument_id=iid, memo=f"night of {d}"),
                                                     cr(aid, "2310", ccy, fee_c, instrument_id=iid, memo="accrued fee payable")],
                                   f"accr:bfee:{bid}:{d}", ref_type="borrow", ref_id=bid))
    if reb_c > 0:
        memo = (f"rebate on cash collateral {fmt_money(a['rebate_base_c'])} ({bid}): SOFR {sofr} % ({sofr_obs or 'given'}) - 0.50 % = {_pct(a['rebate_rate'])},"
                f" ACT/360, night of {d} (ACCRUAL, RATE_LAG)")
        reb_entry = ctx.post(Entry("ACCRUAL", memo, [dr(aid, "1410", ccy, reb_c, instrument_id=iid, memo="accrued rebate receivable"),
                                                     cr(aid, "4350", ccy, reb_c, instrument_id=iid, memo=f"night of {d}")],
                                   f"accr:rebate:{bid}:{d}", ref_type="borrow", ref_id=bid))
    pay_id = None
    if b["status"] == "CLOSED" and (fee_c > 0 or reb_c > 0):
        pay_id = settle_accrued(ctx, services, b, fee_c, reb_c, f"late:{d}")
    ctx.touched_accounts.add(aid)
    ctx.touched_instruments.add((aid, iid))
    ctx.event("ACCRUED", account_id=aid, instrument_id=iid, ref_type="borrow", ref_id=bid, night=d, fee_c=fee_c, rebate_c=reb_c, mark=str(mark), sofr=None if sofr is None else str(sofr),
              fee_entry_id=fee_entry, rebate_entry_id=reb_entry, late_settlement_entry_id=pay_id)
    return {"borrow_id": bid, "night": d, "fee_c": fee_c, "rebate_c": reb_c, "fee_base_c": a["fee_base_c"], "rebate_base_c": a["rebate_base_c"], "mark": str(mark),
            "mark_note": mark_note, "sofr": None if sofr is None else str(sofr), "sofr_obs": sofr_obs, "fee_entry_id": fee_entry, "rebate_entry_id": reb_entry,
            "late_settlement_entry_id": pay_id}


# =====================================================================================================================================
# Securities lending OUT of inventory through an agent lender (Build 2 P08; OA-18; contracts C13 / C20 / C27)
# =====================================================================================================================================
# Conventions stated here (each is a sentence the screen shows or a rule the tests pin)
# - WHAT: long US equities / ETFs held in a US Exchange or prime-broker account are lent through an AGENT LENDER of the
#   counterparty directory (BNY, State Street, JPMorgan, Northern Trust, Citi, eSecLending; ASSUMED, editable terms).  The
#   shares stay the book's position (price risk, dividends - manufactured by the borrower - and P&L) but are ON LOAN: a sale
#   of lent shares is refused "recall N lent shares first" (C27 ``lent_qty``).  Shares committed to working sells or
#   reserved by covered calls cannot be lent.
# - COLLATERAL: the borrower posts 102 % CASH collateral, received into the lending account and owed back: ``Dr 1010 /
#   Cr 2520`` (``seclend:{id}:open``).  It is marked on each trading day from 17:10 ET to 102 % of the shares at the
#   recorded CLOSE, both ways: more collateral in (``Dr 1010 / Cr 2520``) or the excess returned (``Dr 2520 / Cr 1010``,
#   funded by the sweep) (``slmark:{id}:{date}``); a missing CLOSE waits (never defaulted).
# - FEE AND REBATE: the borrower's lending fee comes from the same FINRA / volume tier table as stock borrows (GC 0.30 %,
#   T1 1 %, T2 5 %, T3 25 %, ACT/360 on qty x the night's CLOSE); the book earns its share of it (the agent keeps the rest:
#   ``owner_split``, e.g. 85 %) ``Dr 1420 / Cr 4360`` (``accr:slfee:{id}:{D}``), and pays the borrower a rebate on the
#   cash collateral it holds, max(0, SOFR + the agent's rebate spread (-0.50 %)) (0 for tier T3) ``Dr 5350 / Cr 2420``
#   (``accr:slreb:{id}:{D}``), on the collateral standing at the 17:00 ET close of each night (swept idle cash earns the
#   Treasury rate, which offsets the rebate).  Accrued fee and rebate are settled when shares come back.
# - RECALL: the owner recalls any time: every night owed is posted first, then the recalled share of the collateral goes
#   back (``Dr 2520 / Cr 1010``, funded by the sweep; ``seclend:{id}:recall:{request}``) with the matching share of the
#   accrued fee and rebate (``slpay:{id}:recall:{request}``); the recall settles at once in this model (a real one settles
#   on the standard cycle).
# - LEDGER: the codes 1420 / 2420 / 2520 / 4360 / 5350 are the ledger's (requested from the accounting package); until the
#   accounting module carries them in its balance sheet and P&L, lending out is refused with that reason (never booked to
#   a mislabelled account).
LEND_GLS = {
    "1420": ("Accrued securities-lending fee receivable", "ASSET", 1, "accrued_income"),
    "2420": ("Accrued rebate payable — securities lent", "LIABILITY", 1, "accrued_expense"),
    "2520": ("Cash collateral received — securities lent", "LIABILITY", 1, "collateral_received"),
    "4360": ("Securities-lending fee income", "INCOME", 1, "income"),
    "5350": ("Rebate paid on cash collateral received", "EXPENSE", 1, "financing_cost"),
}
SECLEND_SCHEMA = [
    "CREATE TABLE IF NOT EXISTS sec_loans (loan_id TEXT PRIMARY KEY, command_id INTEGER NOT NULL, account_id TEXT NOT NULL, instrument_id TEXT NOT NULL,"
    " qty INTEGER NOT NULL CHECK (qty > 0), qty_recalled INTEGER NOT NULL DEFAULT 0, counterparty TEXT NOT NULL, agent TEXT NOT NULL, tier TEXT NOT NULL,"
    " fee_rate TEXT NOT NULL, owner_split TEXT NOT NULL, rebate_spread TEXT NOT NULL, collateral_c INTEGER NOT NULL, currency TEXT NOT NULL, opened_ts INTEGER NOT NULL,"
    " opened_date TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'OPEN', closed_ts INTEGER, last_mark_date TEXT, terms_json TEXT)",
    "CREATE INDEX IF NOT EXISTS ix_sec_loans_acct ON sec_loans(account_id, instrument_id, status)",
    "ALTER TABLE borrows ADD COLUMN counterparty TEXT",
]
SECLEND_ACCOUNT_KINDS = ("US_EXCHANGE", "PB")
SECLEND_MARK_ET = "17:10"
SL_FEE, SL_REB = "slfee", "slreb"


def seclend_tables_ready(conn) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sec_loans'").fetchone() is not None


def ensure_seclend_schema(db, conn=None) -> None:
    """C4: ``sec_loans`` and ``borrows.counterparty`` (idempotent; inside a handler's own transaction when ``conn`` is given)."""
    if db is None:
        return
    fn = getattr(db, "ensure_schema_ext", None)
    if not callable(fn):
        return
    if conn is not None:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(borrows)").fetchall()}
        if not seclend_tables_ready(conn) or "counterparty" not in cols:
            fn("lending", SECLEND_SCHEMA)
        return
    fn("lending", SECLEND_SCHEMA)


def ledger_ready() -> Tuple[bool, str]:
    """Whether the ledger and the accounting read models carry the securities-lending codes (module section LEDGER)."""
    from . import accounting, ledger
    missing = [c for c in LEND_GLS if c not in ledger.CHART]
    a = set(getattr(accounting, "NAV_ASSET_GLS", ())) | set(getattr(accounting, "NAV_LIAB_GLS", ()))
    missing += [c for c in ("1420", "2420", "2520") if c not in a and c not in missing]
    if "4360" not in getattr(ledger, "INCOME_GLS", ()) and "4360" not in missing:
        missing.append("4360")
    if "5350" not in getattr(ledger, "FINANCING_GLS", ()) and "5350" not in missing:
        missing.append("5350")
    if missing:
        return False, ("lending shares out needs its own ledger accounts (cash collateral received, lending fees, rebates paid: "
                       f"{', '.join(sorted(missing))}), which this version of the accounting does not carry yet - nothing is booked to a mislabelled account")
    return True, ""


def ensure_chart_rows(conn) -> int:
    """INSERT OR IGNORE the chart rows of the lending codes the ledger defines (an existing database was seeded before them)."""
    from . import ledger
    n = 0
    for code in LEND_GLS:
        spec = ledger.CHART.get(code)
        if spec is None:
            continue
        cur = conn.execute("INSERT OR IGNORE INTO chart(gl, name, kind, per_instrument, bucket) VALUES (?,?,?,?,?)",
                           (code, spec.name, spec.kind, 1 if spec.per_instrument else 0, spec.bucket))
        n += int(cur.rowcount or 0)
    return n


def _sl_row(conn, loan_id: str):
    r = conn.execute("SELECT * FROM sec_loans WHERE loan_id=?", (str(loan_id),)).fetchone() if seclend_tables_ready(conn) else None
    if r is None:
        raise RejectError("NOT_FOUND", f"securities loan {loan_id} does not exist")
    return r


def sl_outstanding(r) -> int:
    return int(r["qty"]) - int(r["qty_recalled"])


def lent_qty(conn, account_id: str, instrument_id: str) -> int:
    """C27: shares of (account, instrument) out on loan (P05's preview refuses a SELL beyond position - lent)."""
    if not seclend_tables_ready(conn):
        return 0
    row = conn.execute("SELECT COALESCE(SUM(qty - qty_recalled), 0) FROM sec_loans WHERE account_id=? AND instrument_id=? AND status='OPEN'",
                       (account_id, instrument_id)).fetchone()
    return int(row[0] or 0)


def _working_sells(conn, account_id: str, instrument_id: str) -> int:
    row = conn.execute("SELECT COALESCE(SUM(qty - filled_qty), 0) FROM orders WHERE account_id=? AND instrument_id=? AND side='SELL'"
                       " AND status IN ('WORKING','PARTIAL','SUSPENDED','WAITING_PARENT')", (account_id, instrument_id)).fetchone()
    return int(row[0] or 0)


def _reserved(services, account_id: str, instrument_id: str) -> int:
    fn = getattr(getattr(services, "options", None), "reserved_shares", None)
    if not callable(fn):
        return 0
    try:
        return max(0, int(fn(services, account_id, instrument_id) or 0))
    except Exception:   # noqa: BLE001 - a failing lookup reserves nothing here; the order path re-checks
        return 0


def free_to_lend(services, conn, account_id: str, instrument_id: str) -> dict:
    """What can be lent now: the position less shares on loan, shares committed to working sells, shares reserved by
    covered calls and shares bought but not yet settled (a lender delivers shares it has received)."""
    from .ledger import position
    pos, _cost = position(conn, account_id, instrument_id)
    lent = lent_qty(conn, account_id, instrument_id)
    ws = _working_sells(conn, account_id, instrument_id)
    res = _reserved(services, account_id, instrument_id)
    r = conn.execute("SELECT COALESCE(SUM(f.qty), 0) q, MAX(s.due_date) d FROM fills f JOIN settlements s ON s.fill_id=f.fill_id WHERE f.account_id=? AND f.instrument_id=?"
                     " AND f.side='BUY' AND s.status='PENDING'", (account_id, instrument_id)).fetchone()
    pending = int(r["q"] or 0)
    return {"position": int(pos), "lent": lent, "working_sells": ws, "reserved": res, "unsettled": pending, "settles": r["d"],
            "free": max(0, int(pos) - lent - ws - res - pending)}


def _tier_for(services, conn, inst) -> Tuple[str, str]:
    """The lending tier of a name from the borrow heuristic (FINRA short-volume ratio and ADV): the demand side of a loan."""
    from . import tables
    from .lending import _adv, finra_view
    adv = _adv(inst)
    fv = finra_view(conn, inst.symbol, services.clock.today(), services.cal)
    tier, _cap = tables.borrow_tier(True, adv, fv["ratio"], fv["rows_last3"], inst.kind, bool((inst.spec or {}).get("shortable", True)))
    basis = (f"FINRA short-volume ratio {fv['ratio']} ({fv['ratio_day']})" if fv["ratio"] is not None else "no FINRA row in the last 3 sessions") + (
        f", ADV {adv:,}" if adv else ", no ADV")
    return tier, basis


def sl_rebate_rate(sofr_pct, tier: str, spread) -> Optional[Decimal]:
    """max(0, SOFR / 100 + the agent's rebate spread) as a fraction; 0 for T3; None when SOFR is unknown."""
    if tier == "T3":
        return Decimal(0)
    if sofr_pct is None:
        return None
    return qprice(max(Decimal(0), _dec(sofr_pct) / Decimal(100) + _dec(spread)))


def lend_preview(services, payload: dict, conn=None) -> dict:
    """What ``credit.lend_securities`` would book (pure): free shares, the agent, the tier fee and your share of it, the
    collateral, the rebate - or the reason it cannot."""
    from .books import account_kind_for, account_row
    from .marks import shared_reader
    with shared_reader(services.db, conn) as c:
        aid = str(payload.get("account_id") or "")
        acct = account_row(c, aid)
        if acct is None:
            raise RejectError("ACCOUNT_UNKNOWN", f"account {aid} does not exist")
        if acct["status"] != "OPEN":
            raise RejectError("ACCOUNT_CLOSED", f"account {aid} is closed")
        if account_kind_for(acct) not in SECLEND_ACCOUNT_KINDS:
            raise RejectError("NOT_LENDABLE_HERE", f"{aid}: shares are lent from the US Exchange or a prime-broker account that holds them")
        iid = str(payload.get("instrument_id") or "").strip().upper()
        inst = services.instruments.get(iid, c) if iid else None
        if inst is None or inst.kind not in tables.EQUITY_KINDS or inst.currency != "USD":
            raise RejectError("NOT_LENDABLE", f"{iid or 'the instrument'}: US equities and ETFs only are lent in this build")
        try:
            qty = int(payload.get("qty"))
        except (TypeError, ValueError) as e:
            raise RejectError("QTY_INVALID", f"qty must be a whole number, got {payload.get('qty')!r}") from e
        if qty <= 0:
            raise RejectError("QTY_INVALID", "qty must be positive")
        f = free_to_lend(services, c, aid, iid)
        if qty > f["free"]:
            raise ConflictError("NOT_ENOUGH_FREE", f"{aid} holds {f['position']:,} {inst.symbol}; {f['lent']:,} on loan, {f['working_sells']:,} in working sells,"
                                f" {f['reserved']:,} reserved by covered calls, {f['unsettled']:,} bought and not yet settled"
                                + (f" (settles {f['settles']})" if f["unsettled"] else "") + f": {f['free']:,} can be lent", f)
        cps = getattr(services, "counterparties", None)
        if cps is None or not hasattr(cps, "lending_terms"):
            from . import counterparties as cps
        agent = cps.lending_terms(services, str(payload.get("counterparty_id") or ""), c)
        tier, basis = _tier_for(services, c, inst)
        if tier == "NONE":
            raise ConflictError("NO_LENDING_DEMAND", f"{inst.symbol}: no lending estimate ({basis}); nothing is lent without one")
        fee = qprice(tables.RATES["borrow_fee"][tier])
        own_fee = qprice(fee * agent["owner_split"])
    mv = services.marks.mark_for(inst) if hasattr(services.marks, "mark_for") else services.marks.mark(iid)
    if mv is None or mv.mark is None or mv.quality == "unavailable":
        raise ConflictError("MARK_UNAVAILABLE", f"{inst.symbol}: no price to size the 102 % collateral; nothing is lent")
    mark = qprice(mv.mark)
    coll = to_c(Decimal(qty) * mark * config.COLLATERAL_PCT)
    sofr = sofr_for(services)
    reb = sl_rebate_rate(None if sofr is None else sofr[1], tier, agent["rebate_spread"])
    return {"ok": True, "account_id": aid, "instrument_id": iid, "symbol": inst.symbol, "qty": qty, "free": f, "counterparty_id": agent["counterparty_id"],
            "agent": agent["label"], "agreement": agent.get("agreement"), "tier": tier, "tier_basis": basis, "fee_rate": str(fee), "owner_split": str(agent["owner_split"]),
            "own_fee_rate": str(own_fee), "mark": str(mark), "mark_quality": mv.quality, "mark_source": getattr(mv, "source", "") or "", "collateral_c": coll,
            "rebate_rate": None if reb is None else str(reb), "rebate_spread": str(agent["rebate_spread"]),
            "sofr": None if sofr is None else {"value_pct": str(sofr[1]), "as_of": sofr[0].isoformat()},
            "fee_per_day_c": to_c(Decimal(qty) * mark * own_fee / Decimal(BASIS_360)), "rebate_per_day_c": None if reb is None else to_c(from_c(coll) * reb / Decimal(BASIS_360)),
            "ledger_ready": ledger_ready()[0], "ledger_note": ledger_ready()[1],
            "text": (f"lend {qty:,} {inst.symbol} through {agent['label']}: tier {tier} fee {_pct(fee)}, your share {_pct(own_fee)} of qty x CLOSE (ACT/360); "
                     f"cash collateral 102 % = {fmt_money(coll)} received and owed back; rebate {'n/a until SOFR loads' if reb is None else _pct(reb)} paid on it")}


def h_lend(ctx: TxContext, payload: dict) -> dict:
    """``credit.lend_securities {account_id, instrument_id, qty, counterparty_id}``."""
    services = _services(ctx)
    ensure_seclend_schema(getattr(services, "db", None), ctx.conn)
    ok, why = ledger_ready()
    if not ok:
        raise ConflictError("LENDING_LEDGER_PENDING", why)
    ensure_chart_rows(ctx.conn)
    from .db import dumps, next_id
    pv = lend_preview(services, payload, conn=ctx.conn)
    if pv["rebate_rate"] is None:
        raise ConflictError("RATE_UNAVAILABLE", "no SOFR print loaded: the rebate on the cash collateral is unknown and never defaulted; lend once SOFR loads")
    conn = ctx.conn
    lid = next_id(conn, "sl")
    aid, iid, coll = pv["account_id"], pv["instrument_id"], int(pv["collateral_c"])
    memo = (f"lend {pv['qty']:,} {pv['symbol']} through {pv['agent']} ({lid}): tier {pv['tier']} fee {_pct(pv['fee_rate'])}, your share {_pct(pv['own_fee_rate'])};"
            f" cash collateral 102 % of {pv['qty']:,} x {pv['mark']} = {fmt_money(coll)} received, rebate {_pct(pv['rebate_rate'])}")
    eid = ctx.post(Entry("SECLEND", memo, [dr(aid, "1010", "USD", coll, memo=f"cash collateral received for {lid}"),
                                           cr(aid, "2520", "USD", coll, instrument_id=iid, memo=f"owed back to the borrower ({lid})")],
                         f"seclend:{lid}:open", ref_type="sec_loan", ref_id=lid))
    terms = {"tier_basis": pv["tier_basis"], "mark": pv["mark"], "mark_quality": pv["mark_quality"], "agreement": pv.get("agreement"), "sofr": pv["sofr"]}
    conn.execute("INSERT INTO sec_loans(loan_id, command_id, account_id, instrument_id, qty, qty_recalled, counterparty, agent, tier, fee_rate, owner_split, rebate_spread,"
                 " collateral_c, currency, opened_ts, opened_date, status, closed_ts, last_mark_date, terms_json) VALUES (?,?,?,?,?,0,?,?,?,?,?,?,?,'USD',?,?,'OPEN',NULL,NULL,?)",
                 (lid, ctx.command_id, aid, iid, int(pv["qty"]), pv["counterparty_id"], pv["agent"], pv["tier"], pv["fee_rate"], pv["owner_split"], pv["rebate_spread"],
                  coll, ctx.ts, ctx.today, dumps(terms)))
    ctx.touched_accounts.add(aid)
    ctx.touched_instruments.add((aid, iid))
    ctx.event("SECURITIES_LENT", account_id=aid, instrument_id=iid, ref_type="sec_loan", ref_id=lid, qty=int(pv["qty"]), counterparty_id=pv["counterparty_id"],
              tier=pv["tier"], fee_rate=pv["fee_rate"], owner_split=pv["owner_split"], collateral_c=coll, entry_id=eid)
    return {"loan_id": lid, "entry_id": eid, "preview": pv, "loan": sec_loan_view(services, conn, _sl_row(conn, lid)), "memo": memo}


def sl_collateral_at_close_c(conn, loan_id: str, d: str) -> int:
    """The cash collateral of a securities loan standing at the 17:00 ET close of accounting day ``d`` (its SECLEND entries on 2520)."""
    row = conn.execute("SELECT COALESCE(SUM(l.amount_c), 0) FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id WHERE e.ref_type='sec_loan' AND e.ref_id=?"
                       " AND e.kind='SECLEND' AND l.gl='2520' AND e.business_date<=?", (loan_id, d)).fetchone()
    return -int(row[0] or 0)


def sl_nights_due(conn, r, through: date) -> List[str]:
    first = date.fromisoformat(r["opened_date"])
    last = through
    if r["status"] != "OPEN" and r["closed_ts"]:
        last = min(last, accounting_day(int(r["closed_ts"])) - timedelta(days=1))
    starts = []
    for kind in (SL_FEE, SL_REB):
        st = conn.execute("SELECT accrued_through FROM accrual_state WHERE kind=? AND ref_id=?", (kind, r["loan_id"])).fetchone()
        if st is not None and st["accrued_through"]:
            starts.append(date.fromisoformat(st["accrued_through"]) + timedelta(days=1))
    start = max(first, min(starts)) if starts else first
    out, d = [], start
    while d <= last:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def sl_accrue_night(ctx: TxContext, services, r, d: str) -> dict:
    """Night ``d`` of one securities loan: your share of the fee on qty x CLOSE(d) and the rebate on the collateral at the close."""
    lid, aid, iid = r["loan_id"], r["account_id"], r["instrument_id"]
    if d >= ctx.today:
        raise RejectError("NIGHT_NOT_ENDED", f"the night of {d} has not ended (accounting day {ctx.today})")
    got = close_mark_for(ctx.conn, iid, d)
    if got is None:
        raise RejectError("MARK_UNAVAILABLE", f"no CLOSE mark for {iid} on or before {d}: the lending fee for that night waits for MARKS_RECORD (never defaulted)")
    mark, mark_note = got
    tier = r["tier"]
    sofr = None
    if tier != "T3":
        s = sofr_for(services, date.fromisoformat(d))
        if s is None or (date.fromisoformat(d) - s[0]).days > RATE_LAG_MAX_DAYS:
            raise RejectError("RATE_UNAVAILABLE", f"no SOFR observation within {RATE_LAG_MAX_DAYS} days on or before {d}: the rebate waits (never defaulted)")
        sofr = s
    inst = _inst_of(services, ctx.conn, iid)
    qty = sl_outstanding(r)
    fee_base = _fee_base_c(qty, mark, inst)
    own_fee = qprice(_dec(r["fee_rate"]) * _dec(r["owner_split"]))
    reb_rate = sl_rebate_rate(None if sofr is None else sofr[1], tier, r["rebate_spread"])
    coll = sl_collateral_at_close_c(ctx.conn, lid, d)
    fee_c = accrue_segment(ctx, SL_FEE, lid, fee_base, own_fee, BASIS_360, d)
    reb_c = accrue_segment(ctx, SL_REB, lid, coll, reb_rate, BASIS_360, d)
    sym = inst.symbol if inst is not None else iid
    fe = re = None
    if fee_c > 0:
        fe = ctx.post(Entry("ACCRUAL", f"lending fee {sym} ({lid}, {r['agent']}): tier {tier} {_pct(r['fee_rate'])} x your share {_dec(r['owner_split'])} = {_pct(own_fee)},"
                                       f" ACT/360 on {qty:,} x {mark} = {fmt_money(fee_base)} ({mark_note}), night of {d}",
                            [dr(aid, "1420", "USD", fee_c, instrument_id=iid, memo=f"night of {d}"), cr(aid, "4360", "USD", fee_c, instrument_id=iid, memo="lending fee earned")],
                            f"accr:{SL_FEE}:{lid}:{d}", ref_type="sec_loan", ref_id=lid))
    if reb_c > 0:
        re = ctx.post(Entry("ACCRUAL", f"rebate on cash collateral {fmt_money(coll)} ({lid}): SOFR {sofr[1]} % ({sofr[0].isoformat()}) {r['rebate_spread']} = {_pct(reb_rate)},"
                                       f" ACT/360, night of {d}",
                            [dr(aid, "5350", "USD", reb_c, instrument_id=iid, memo=f"night of {d}"), cr(aid, "2420", "USD", reb_c, instrument_id=iid, memo="rebate owed to the borrower")],
                            f"accr:{SL_REB}:{lid}:{d}", ref_type="sec_loan", ref_id=lid))
    ctx.touched_accounts.add(aid)
    ctx.touched_instruments.add((aid, iid))
    ctx.event("ACCRUED", account_id=aid, instrument_id=iid, ref_type="sec_loan", ref_id=lid, night=d, fee_c=fee_c, rebate_c=reb_c, mark=str(mark), fee_entry_id=fe, rebate_entry_id=re)
    return {"loan_id": lid, "night": d, "fee_c": fee_c, "rebate_c": reb_c, "fee_base_c": fee_base, "collateral_c": coll, "own_fee_rate": str(own_fee),
            "rebate_rate": str(reb_rate), "mark": str(mark), "fee_entry_id": fe, "rebate_entry_id": re}


def sl_catch_up(ctx: TxContext, services, r) -> List[dict]:
    through = date.fromisoformat(ctx.today) - timedelta(days=1)
    out = []
    for d in sl_nights_due(ctx.conn, r, through):
        try:
            out.append(sl_accrue_night(ctx, services, r, d))
        except RejectError as e:
            raise ConflictError("ACCRUAL_PENDING", f"securities loan {r['loan_id']} owes the night of {d} but it cannot be computed ({e.code}: {e.message}); recall once it posts",
                                {"loan_id": r["loan_id"], "night": d, "cause": e.code}) from e
    return out


def sl_accrued(conn, r) -> Tuple[int, int]:
    """``(accrued fee receivable 1420, accrued rebate payable 2420)`` of a loan's instrument in its account (natural sign)."""
    fee = balance(conn, r["account_id"], "1420", "USD", r["instrument_id"])
    reb = balance(conn, r["account_id"], "2420", "USD", r["instrument_id"])
    return int(fee), int(reb)


def h_recall(ctx: TxContext, payload: dict) -> dict:
    """``credit.recall_securities {loan_id, qty?}``: nights owed first, then the recalled shares come back with their share of
    the collateral (returned to the borrower) and of the accrued fee and rebate (settled)."""
    services = _services(ctx)
    conn = ctx.conn
    r = _sl_row(conn, str(payload.get("loan_id") or ""))
    if r["status"] != "OPEN":
        raise ConflictError("LOAN_CLOSED", f"securities loan {r['loan_id']} is closed")
    out_qty = sl_outstanding(r)
    qty = out_qty if payload.get("qty") in (None, "") else int(payload.get("qty"))
    if qty <= 0 or qty > out_qty:
        raise RejectError("QTY_INVALID", f"{r['loan_id']} has {out_qty:,} on loan; cannot recall {qty:,}")
    caught = sl_catch_up(ctx, services, r)
    r = _sl_row(conn, r["loan_id"])
    lid, aid, iid = r["loan_id"], r["account_id"], r["instrument_id"]
    group_total = int(conn.execute("SELECT COALESCE(SUM(qty - qty_recalled), 0) FROM sec_loans WHERE account_id=? AND instrument_id=? AND status='OPEN'",
                                   (aid, iid)).fetchone()[0] or 0)
    back = pro_rata_c_(int(r["collateral_c"]), qty, out_qty)
    funding = execution.fund(ctx, aid, "USD", back, "sec_loan", lid, None, immediate=True, services=services) if back > 0 else None
    sym = (_inst_of(services, conn, iid) or type("I", (), {"symbol": iid})).symbol
    eid = ctx.post(Entry("SECLEND", f"recall {qty:,} {sym} from {r['agent']} ({lid}): collateral {fmt_money(back)} returned to the borrower",
                         [dr(aid, "2520", "USD", back, instrument_id=iid, memo=f"collateral returned ({lid})"), cr(aid, "1010", "USD", back, memo=f"collateral back to the borrower ({lid})")],
                         f"seclend:{lid}:recall:{ctx.request_id}", ref_type="sec_loan", ref_id=lid)) if back > 0 else None
    fee_bal, reb_bal = sl_accrued(conn, r)
    if group_total - qty == 0:
        fee_c, reb_c = fee_bal, reb_bal
    else:
        fee_c, reb_c = pro_rata_c_(fee_bal, qty, group_total), pro_rata_c_(reb_bal, qty, group_total)
    pid = None
    if fee_c > 0 or reb_c > 0:
        need = reb_c - max(fee_c, 0)
        if need > 0:
            execution.fund(ctx, aid, "USD", need, "sec_loan", lid, None, immediate=True, overdraft=True, services=services)
        lines = []
        if fee_c > 0:
            lines += [dr(aid, "1010", "USD", fee_c, memo="lending fee received from the agent"), cr(aid, "1420", "USD", fee_c, instrument_id=iid, memo="accrued lending fee settled")]
        if reb_c > 0:
            lines += [dr(aid, "2420", "USD", reb_c, instrument_id=iid, memo="accrued rebate settled"), cr(aid, "1010", "USD", reb_c, memo="rebate paid to the borrower")]
        pid = ctx.post(Entry("PAY", f"settle lending fee {fmt_money(max(fee_c, 0))} and rebate {fmt_money(max(reb_c, 0))} on {sym} ({lid}, recall)", lines,
                             f"slpay:{lid}:recall:{ctx.request_id}", ref_type="sec_loan", ref_id=lid))
    full = qty == out_qty
    conn.execute("UPDATE sec_loans SET qty_recalled=qty_recalled+?, collateral_c=collateral_c-? WHERE loan_id=?", (qty, back, lid))
    if full:
        conn.execute("UPDATE sec_loans SET status='CLOSED', closed_ts=? WHERE loan_id=?", (ctx.ts, lid))
    ctx.touched_accounts.add(aid)
    ctx.touched_instruments.add((aid, iid))
    ctx.event("SECURITIES_RECALLED", account_id=aid, instrument_id=iid, ref_type="sec_loan", ref_id=lid, qty=qty, collateral_returned_c=back, fee_received_c=max(fee_c, 0),
              rebate_paid_c=max(reb_c, 0), closed=full, entry_id=eid, pay_entry_id=pid, nights=[c["night"] for c in caught])
    return {"loan_id": lid, "qty": qty, "closed": full, "collateral_returned_c": back, "fee_received_c": max(fee_c, 0), "rebate_paid_c": max(reb_c, 0),
            "entry_id": eid, "pay_entry_id": pid, "accrued_nights": [{"night": c["night"], "fee_c": c["fee_c"], "rebate_c": c["rebate_c"]} for c in caught],
            "funding": None if funding is None else funding.to_dict(), "loan": sec_loan_view(services, conn, _sl_row(conn, lid))}


def pro_rata_c_(total_c: int, qty: int, outstanding: int) -> int:
    qty, outstanding = int(qty), int(outstanding)
    if outstanding <= 0 or qty >= outstanding:
        return int(total_c)
    return to_c(from_c(int(total_c)) * Decimal(qty) / Decimal(outstanding))


def sl_collateral_mark(ctx: TxContext, services, r, mark_date: str) -> dict:
    """The daily 102 % collateral mark of a securities loan from the recorded CLOSE of ``mark_date`` (missing -> RejectError, retried)."""
    lid, aid, iid = r["loan_id"], r["account_id"], r["instrument_id"]
    if r["status"] != "OPEN":
        return {"loan_id": lid, "skipped": "closed", "delta_c": 0}
    if (r["last_mark_date"] or "") >= mark_date:
        return {"loan_id": lid, "skipped": f"already marked for {mark_date}", "delta_c": 0}
    got = close_mark_for(ctx.conn, iid, mark_date, exact=True)
    if got is None:
        raise RejectError("MARK_UNAVAILABLE", f"no CLOSE mark recorded for {iid} on {mark_date}: the collateral mark waits for MARKS_RECORD (never defaulted)")
    mark, note = got
    inst = _inst_of(services, ctx.conn, iid)
    required = to_c(Decimal(sl_outstanding(r)) * _dec(mark) * _dec(getattr(inst, "multiplier", 1) or 1) * _dec(getattr(inst, "price_scale", 1) or 1) * config.COLLATERAL_PCT)
    delta = required - int(r["collateral_c"])
    eid = None
    if delta > 0:
        lines = [dr(aid, "1010", "USD", delta, memo=f"more collateral in ({lid})"), cr(aid, "2520", "USD", delta, instrument_id=iid, memo=lid)]
    elif delta < 0:
        execution.fund(ctx, aid, "USD", -delta, "sec_loan", lid, None, immediate=True, overdraft=True, services=services)
        lines = [dr(aid, "2520", "USD", -delta, instrument_id=iid, memo=lid), cr(aid, "1010", "USD", -delta, memo=f"excess collateral returned ({lid})")]
    if delta:
        eid = ctx.post(Entry("SECLEND", f"collateral mark {mark_date} on {lid}: 102 % of {sl_outstanding(r):,} x {mark} = {fmt_money(required)} ({note});"
                                        f" {'more collateral in' if delta > 0 else 'excess returned'} {fmt_money(abs(delta))}", lines, f"slmark:{lid}:{mark_date}",
                             ref_type="sec_loan", ref_id=lid))
    ctx.conn.execute("UPDATE sec_loans SET collateral_c=?, last_mark_date=? WHERE loan_id=?", (required, mark_date, lid))
    ctx.touched_accounts.add(aid)
    ctx.touched_instruments.add((aid, iid))
    ctx.event("COLLATERAL_MARKED", account_id=aid, instrument_id=iid, ref_type="sec_loan", ref_id=lid, mark=str(mark), mark_date=mark_date, required_c=required, delta_c=delta, entry_id=eid)
    return {"loan_id": lid, "mark_date": mark_date, "mark": str(mark), "required_c": required, "delta_c": delta, "entry_id": eid}


def market_value_c(services, iid: str, qty: int, snap=None):
    """(qty x the instrument's mark, the MarkView) from a frozen snapshot of the mark provider, or (None, None) when there is
    no usable mark.  services.marks is the MarkProvider: marks are read through snapshot(ids).mark(iid), never invented."""
    try:
        s = snap if snap is not None else services.marks.snapshot([iid])
        v = s.mark(iid)
    except Exception:   # noqa: BLE001 - a view never fails on a missing mark; the value shows n/a with no number
        return None, None
    if v is None or v.mark is None or getattr(v, "quality", None) == "unavailable":
        return None, None
    return to_c(Decimal(int(qty)) * _dec(v.mark)), v


def sec_loan_view(services, conn, r, snap=None) -> dict:
    fee, reb = sl_accrued(conn, r) if r["status"] == "OPEN" else (0, 0)
    inst = _inst_of(services, conn, r["instrument_id"])
    mv, mview = market_value_c(services, r["instrument_id"], sl_outstanding(r), snap) if getattr(services, "marks", None) is not None else (None, None)
    try:
        terms = json.loads(r["terms_json"] or "{}")
    except ValueError:
        terms = {}
    return {"loan_id": r["loan_id"], "account_id": r["account_id"], "instrument_id": r["instrument_id"], "symbol": inst.symbol if inst is not None else r["instrument_id"],
            "qty": int(r["qty"]), "qty_recalled": int(r["qty_recalled"]), "outstanding": sl_outstanding(r), "counterparty_id": r["counterparty"], "agent": r["agent"],
            "tier": r["tier"], "fee_rate": r["fee_rate"], "owner_split": r["owner_split"], "own_fee_rate": str(qprice(_dec(r["fee_rate"]) * _dec(r["owner_split"]))),
            "rebate_spread": r["rebate_spread"], "collateral_c": int(r["collateral_c"]), "market_value_c": mv, "accrued_fee_c": fee, "accrued_rebate_c": reb,
            "mark": None if mview is None else str(mview.mark), "mark_quality": None if mview is None else mview.quality,
            "opened_date": r["opened_date"], "status": r["status"], "last_mark_date": r["last_mark_date"], "terms": terms, "assumed": True}
