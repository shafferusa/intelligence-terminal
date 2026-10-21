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

from . import execution, tables
from .clock import accounting_day
from .commands import RejectError, TxContext
from .ledger import Entry, Line, cr, dr
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
