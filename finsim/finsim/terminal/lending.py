"""Securities lending: locate, borrow, 102 % collateral, nightly fee / rebate accruals, returns, cleanup, recall notices
(TERMINAL_DESIGN.md §2.12 lending.py, §5.4, §11.2 COLLATERAL_MARK / BORROW_CLEANUP, §11.3, §12.2, §13.1, §19.5;
TERMINAL_WORKPLAN.md WP-06).

Conventions stated here (the §1.3 sentences that bite in this module)
- SHORT_COLLATERAL: a borrow posts 102 % cash collateral from the account (``Dr 1400 / Cr 1010``, idem
  ``collat:{borrow}:open``, funded first by ``execution.fund`` so a shortfall arrives as ONE Treasury sweep linked to
  the borrow), marked daily both ways (``cmark:{borrow}:{date}``: a call is funded the same way, an excess returns to
  1010); sale proceeds settle T+1 as ordinary cash (no restricted-cash account); the collateral stays at the lender
  until the borrow is returned.  ``borrows.collateral_c`` always equals the 1400 balance of the borrow (L13).
- BORROW_AVAIL: availability and the fee tier are a heuristic from the last FINRA daily short-volume ratio and the
  master's ADV (``tables.borrow_tier``, §12.2); a real locate would come from a lender.  ``lender`` is a modelled
  label ("street lender (modelled)", or the PB's code for a prime-broker account), never a counterparty invented
  as fact.  A name that is not in the master, has no ADV or is flagged ``shortable: false`` gives NO_BORROW.
- ACCRUAL / RATE_LAG: for every calendar night the borrow is open at 17:00 ET the fee accrues on
  ``qty_outstanding × mark_D`` (the CLOSE of D, else the last CLOSE before D — weekends use Friday's) at the tier
  rate, and the rebate on the cash collateral at ``max(0, SOFR_D − 0.50 %)`` (0 for tier T3), ACT/360, with
  cumulative rounding per segment (§11.3, ``accrue_segment`` on ``accrual_state``): a segment restarts when the
  base or the rate changes (a collateral mark, a partial return, a new SOFR print).  SOFR_D is the latest published
  observation on or before D; a missing mark or rate RAISES (MARK_UNAVAILABLE / RATE_UNAVAILABLE) — nothing is
  defaulted; the lifecycle job records the night as skipped and retries.
- Rates in rows and signatures are decimal FRACTIONS (``0.003`` = 0.30 %).  The one exception is ``sofr``: the
  published PERCENT (``3.87`` = 3.87 %) exactly as FRED's SOFR series and ``quotes.rates.RateStore`` carry it.
- Accrued fees (2310) and rebates (1410) are per instrument in the chart, so the instrument's borrows in an account
  share them: a return settles the returned share (``qty / Σ outstanding of the instrument``) and the last share
  settles the whole balance, so nothing lingers; a late accrual posted on a CLOSED borrow settles itself in the
  same command (``pay:bfee:{borrow}:late:{night}``).
- The ``reason`` 'pending short sale' is set at submit and cleared when the order is fully filled (``consume_for_short``);
  after a PARTIAL fill it stays until the order fills or ends and the short backs the rest (L12 wants a short OR a reason).
- Returns close a borrow in exactly three ways: a cover returns the now-unneeded quantity inside the fill's
  transaction (``return_for_cover``: borrows whose order has ended first, oldest first); ``credit.return_stock``
  (manual); BORROW_CLEANUP (``cleanup_due`` / ``cleanup_borrow``) for the unfilled remainder of a cancelled,
  expired or partly filled short order (``release_for_cancel`` handles the unfilled-cancel case at once).  A return
  that would leave the short under-borrowed is refused with 409 BACKS_SHORT (L12).  Manual borrows (no order,
  reason "manual borrow (no order)") stay outside the automatic cleanup until consumed or returned by hand.
- Recalls are never generated and 5700 (buy-in penalties) is never posted by this module: three consecutive FINRA
  sessions with a short-volume ratio ≥ 0.80 on a shorted name only open an INFO attention item ``HTB_TIGHTENING``
  ("hard-to-borrow tightening: a lender could recall; consider covering"), resolved when the ratio falls back.
- Idempotency keys (§5.4): ``collat:{borrow}:open``, ``cmark:{borrow}:{date}``, ``accr:bfee:{borrow}:{night}``,
  ``accr:rebate:{borrow}:{night}``, ``collat:{borrow}:return:{fill_id | cancel:{order} | cleanup:{date} |
  manual:{request_id}}`` and ``pay:bfee:{borrow}:return:{same suffix}`` / ``pay:bfee:{borrow}:late:{night}``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from . import config, execution, tables
from .books import account_kind_for, account_row
from .clock import accounting_day, ny_of
from .commands import ConflictError, RejectError, TxContext
from .db import next_id
from .ledger import Entry, Line, balance, cr, dr, position
from .lending_accruals import (BASIS_360, RATE_LAG_MAX_DAYS, _borrow, _dec, _inst_of, _outstanding, _pct, accrual_amounts, accrual_lines,   # noqa: F401
                               accrue_night, accrue_segment, base_state, close_mark_for, nights_due, rebate_rate, segment_amount, settle_accrued,
                               sofr_for)
from .units import fmt_money, from_c, qprice, to_c

EQUITY_KINDS = tables.EQUITY_KINDS
LOCATE_TTL_S = 120
REASON_PENDING = "pending short sale"
REASON_MANUAL = "manual borrow (no order)"
LIVE_ORDER_STATUSES = ("WORKING", "PARTIAL", "SUSPENDED", "WAITING_PARENT")
MODELLED_LENDER = "street lender (modelled)"
HTB_CODE = "HTB_TIGHTENING"
HTB_MESSAGE = "hard-to-borrow tightening: a lender could recall; consider covering"


@dataclass
class LocateQuote:
    """§2.12 shape plus the inputs the ticket shows.  Rates are fractions; ``sofr`` is the published percent."""
    instrument_id: str
    qty: int
    available: bool
    qty_available: int
    fee_rate: Optional[Decimal]
    rebate_rate: Optional[Decimal]
    tier: str
    lender: str
    collateral_c: int
    mark: Optional[Decimal]
    mark_quality: str
    valid_until: int
    basis: str
    adv: Optional[int] = None
    finra_ratio: Optional[Decimal] = None
    finra_day: Optional[str] = None
    finra_rows_last3: int = 0
    sofr: Optional[Decimal] = None
    sofr_obs: Optional[str] = None
    mark_source: str = ""

    def to_dict(self) -> dict:
        return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in self.__dict__.items()}


# ---- small helpers -----------------------------------------------------------------------------------------------------
def _services(ctx: TxContext, services=None):
    return execution._services(ctx, services)


def _adv(inst) -> Optional[int]:
    spec = getattr(inst, "spec", None) or {}
    v = spec.get("adv")
    if v in (None, "", 0, "0"):
        return None
    try:
        return int(_dec(v))
    except Exception:   # noqa: BLE001 — a malformed master value is "no ADV", disclosed by the basis text
        return None


def _mark_view(services, inst):
    marks = getattr(services, "marks", None)
    if marks is None:
        return None
    if hasattr(marks, "mark_for"):
        return marks.mark_for(inst)
    if hasattr(marks, "mark"):
        return marks.mark(inst.instrument_id)
    return None


def _open_borrows(conn, account_id: str, instrument_id: str) -> list:
    """OPEN borrows of (account, instrument) with their order's status, oldest first."""
    return conn.execute(
        "SELECT b.*, o.status AS order_status, o.qty AS order_qty, o.filled_qty AS order_filled FROM borrows b LEFT JOIN orders o ON o.order_id=b.order_id"
        " WHERE b.account_id=? AND b.instrument_id=? AND b.status='OPEN' ORDER BY b.opened_ts, b.borrow_id", (account_id, instrument_id)).fetchall()


def _group(conn, account_id: str, instrument_id: str) -> dict:
    """What backs what for one (account, instrument): the short, every open borrow, the quantity live short orders
    still need, and the excess that backs nothing (manual pre-borrows excluded from the automatic arithmetic)."""
    rows = _open_borrows(conn, account_id, instrument_id)
    pos_qty, _ = position(conn, account_id, instrument_id)
    short = max(0, -pos_qty)
    auto = [r for r in rows if r["reason"] != REASON_MANUAL]
    total_all = sum(_outstanding(r) for r in rows)
    total_auto = sum(_outstanding(r) for r in auto)
    pending = sum(max(0, int(r["order_qty"] or 0) - int(r["order_filled"] or 0)) for r in auto if r["order_status"] in LIVE_ORDER_STATUSES)
    return {"rows": rows, "auto": auto, "short": short, "total": total_all, "total_auto": total_auto, "pending_live": pending,
            "excess": total_auto - short - pending}


def pro_rata_c(total_c: int, qty: int, outstanding: int) -> int:
    """The share of ``total_c`` that ``qty`` of ``outstanding`` units carries: all of it for the last unit, else
    ``to_c(total × qty / outstanding)`` HALF_EVEN (the residual cent stays with the last share)."""
    qty, outstanding = int(qty), int(outstanding)
    if outstanding <= 0 or qty >= outstanding:
        return int(total_c)
    return to_c(from_c(int(total_c)) * Decimal(qty) / Decimal(outstanding))


def collateral_required_c(qty: int, mark, inst=None) -> int:
    """``to_c(1.02 × qty × mark × multiplier × price_scale)`` — one HALF_EVEN quantisation of the exact product."""
    mult = _dec(getattr(inst, "multiplier", 1) or 1)
    scale = _dec(getattr(inst, "price_scale", 1) or 1)
    return to_c(Decimal(int(qty)) * _dec(mark) * mult * scale * config.COLLATERAL_PCT)


# ---- rates (§12.2) --------------------------------------------------------------------------------------------------------
def fee_rate(tier: str, schedule: Optional[dict] = None) -> Optional[Decimal]:
    """Tier fee as a fraction; a PB schedule adds its ``htb_add`` (GC uses the schedule's ``gc_rate``)."""
    base = tables.RATES["borrow_fee"].get(tier)
    if base is None:
        return None
    if schedule:
        if tier == "GC" and schedule.get("gc_rate") is not None:
            return qprice(_dec(schedule["gc_rate"]))
        return qprice(base + _dec(schedule.get("htb_add") or 0))
    return qprice(base)


# ---- FINRA view and the locate (§12.2, §13.1.1) --------------------------------------------------------------------------
def finra_view(conn, symbol: str, today: date, cal) -> dict:
    """The last three NYSE sessions up to ``today`` and the FINRA rows found for them: ``ratio`` / ``ratio_day`` of the
    latest row in the window (short_volume / total_volume, 4 dp), ``rows_last3`` = rows in the window."""
    d0 = today if cal.is_business_day(today) else cal.prev_business_day(today)
    d1 = cal.prev_business_day(d0)
    d2 = cal.prev_business_day(d1)
    window = [d2.isoformat(), d1.isoformat(), d0.isoformat()]
    rows = conn.execute("SELECT day, short_volume, total_volume FROM finra_short WHERE symbol=? AND day>=? AND day<=? ORDER BY day DESC",
                        (symbol, window[0], window[-1])).fetchall()
    ratio = ratio_day = None
    for r in rows:
        tv = int(r["total_volume"] or 0)
        if tv > 0:
            ratio = qprice((Decimal(int(r["short_volume"])) / Decimal(tv)).quantize(Decimal("0.0001")))
            ratio_day = r["day"]
            break
    return {"ratio": ratio, "ratio_day": ratio_day, "rows_last3": len(rows), "window": window}


def locate(services, account_id: str, inst, qty: int, conn=None) -> LocateQuote:
    """§13.1.1: tier and fee from the master + the last FINRA file (``tables.borrow_tier``), availability per tier,
    ``collateral_c = to_c(1.02 × qty_available × mark)``, ``valid_until = now + 120 s``, ``basis`` for the ticket.
    Read-only; ``conn`` (optional) lets a command reuse its own connection."""
    now = int(services.clock.epoch())
    valid_until = now + LOCATE_TTL_S
    qty = int(qty)
    iid = getattr(inst, "instrument_id", None) if inst is not None else None

    def no(basis: str, **kw) -> LocateQuote:
        return LocateQuote(iid or "", qty, False, 0, None, None, "NONE", MODELLED_LENDER, 0, None, "unavailable", valid_until, basis, **kw)

    if inst is None or not iid:
        return no("not in the master: no borrow (BORROW_AVAIL)")
    if inst.kind not in EQUITY_KINDS:
        return no(f"{inst.symbol}: {inst.kind} cannot be borrowed in this build (equities, ETFs, ADRs, REITs and preferreds only)")
    if inst.status != "ACTIVE":
        return no(f"{inst.symbol} is {inst.status}: no borrow")
    adv = _adv(inst)
    shortable = bool((inst.spec or {}).get("shortable", True))
    today = services.clock.today()
    if conn is not None:
        fv = finra_view(conn, inst.symbol, today, services.cal)
        acct = account_row(conn, account_id)
    else:
        with services.db.reader() as c:
            fv = finra_view(c, inst.symbol, today, services.cal)
            acct = account_row(c, account_id)
    extras = {"adv": adv, "finra_ratio": fv["ratio"], "finra_day": fv["ratio_day"], "finra_rows_last3": fv["rows_last3"]}
    tier, cap = tables.borrow_tier(True, adv, fv["ratio"], fv["rows_last3"], inst.kind, shortable)
    if tier == "NONE":
        why = "flagged not shortable" if not shortable else "no average daily volume in the master"
        return no(f"{inst.symbol}: {why}; no borrow (BORROW_AVAIL)", **extras)
    qty_av = qty if cap is None else min(qty, int(cap))
    mv = _mark_view(services, inst)
    mark = None if mv is None else mv.mark
    quality = "unavailable" if mv is None else mv.quality
    if mark is None or quality == "unavailable":
        return no(f"{inst.symbol}: no mark to size the 102 % collateral; no borrow", **extras)
    if qty_av <= 0:
        return no(f"{inst.symbol}: tier {tier} availability {tables.HTB[tier]['availability']} with ADV {adv:,} leaves nothing to borrow", **extras)
    schedule = execution._schedule_for(services, acct) if acct is not None else None
    lender = f"{acct['broker']} (prime broker, modelled)" if acct is not None and acct["kind"] == "PB" and acct["broker"] else MODELLED_LENDER
    fee = fee_rate(tier, schedule)
    sofr = sofr_for(services)
    sofr_pct = None if sofr is None else sofr[1]
    sofr_obs = None if sofr is None else sofr[0].isoformat()
    reb = rebate_rate(sofr_pct, tier)
    coll = collateral_required_c(qty_av, mark, inst)
    ratio_txt = f"FINRA short-volume ratio {fv['ratio']} ({fv['ratio_day']})" if fv["ratio"] is not None else "no FINRA row in the last 3 sessions"
    if tier == "T3":
        reb_txt = "rebate 0 (tier T3)"
    elif reb is None:
        reb_txt = "rebate unavailable until SOFR is published (never defaulted)"
    else:
        reb_txt = f"rebate max(0, SOFR {sofr_pct} % ({sofr_obs}) - 0.50 %) = {_pct(reb)}"
    avail_txt = "full" if cap is None else f"{tables.HTB[tier]['availability']} = {int(cap):,}"
    basis = (f"tier {tier} ({tables.HTB[tier]['rule']}): {ratio_txt}, ADV {adv:,}; availability {avail_txt}; fee {_pct(fee)} ACT/360 on qty x mark; "
             f"{reb_txt}; collateral 102 % of {qty_av:,} x {mark} = {fmt_money(coll)} from {lender} (BORROW_AVAIL, SHORT_COLLATERAL)")
    return LocateQuote(iid, qty, True, qty_av, fee, reb, tier, lender, coll, qprice(mark), quality, valid_until, basis, sofr=sofr_pct, sofr_obs=sofr_obs,
                       mark_source=getattr(mv, "source", "") or "", **extras)


# ---- borrow open / consume (§13.1.2-3) ---------------------------------------------------------------------------------------
def open_borrow(ctx: TxContext, services, account_id: str, inst, qty: int, locate_q: Optional[LocateQuote], order_id: Optional[str]) -> str:
    """Open a borrow in the caller's transaction: ``fund()`` the 102 % collateral (a shortfall is one Treasury sweep
    linked to the borrow), post ``Dr 1400 / Cr 1010`` (``collat:{borrow}:open``), insert the row (reason
    'pending short sale' for an order, 'manual borrow (no order)' otherwise).  Returns the borrow id."""
    services = _services(ctx, services)
    conn = ctx.conn
    acct = account_row(conn, account_id)
    if acct is None:
        raise RejectError("ACCOUNT_UNKNOWN", f"account {account_id} does not exist")
    if acct["status"] != "OPEN":
        raise RejectError("ACCOUNT_CLOSED", f"account {account_id} is closed")
    if acct["kind"] == "TREASURY":
        raise RejectError("NOT_TRADEABLE_HERE", "Treasury never borrows stock")
    qty = int(qty)
    if qty <= 0:
        raise RejectError("QTY_INVALID", "borrow quantity must be positive")
    if locate_q is None or int(locate_q.valid_until) < ctx.ts or locate_q.instrument_id != inst.instrument_id:
        locate_q = locate(services, account_id, inst, qty, conn=conn)
    if not locate_q.available or int(locate_q.qty_available) <= 0:
        raise ConflictError("NO_BORROW", f"no borrow available for {inst.symbol}: {locate_q.basis}", {"locate": locate_q.to_dict()})
    if qty > int(locate_q.qty_available):
        raise ConflictError("NO_BORROW", f"only {int(locate_q.qty_available):,} {inst.symbol} can be borrowed, asked {qty:,}: {locate_q.basis}",
                            {"locate": locate_q.to_dict(), "qty": qty})
    mark = qprice(locate_q.mark)
    ccy = inst.currency
    iid = inst.instrument_id
    coll = collateral_required_c(qty, mark, inst)
    borrow_id = next_id(conn, "borrow")
    funding = execution.fund(ctx, account_id, ccy, coll, "borrow", borrow_id, None, immediate=True, services=services)
    fee = locate_q.fee_rate
    memo = (f"borrow {qty:,} {inst.symbol} from {locate_q.lender}, tier {locate_q.tier} fee {_pct(fee)}; collateral 102 % of {qty:,} x {mark} = {fmt_money(coll)}"
            f" (SHORT_COLLATERAL)")
    entry_id = ctx.post(Entry("COLLATERAL", memo,
                              [dr(account_id, "1400", ccy, coll, instrument_id=iid, memo=borrow_id),
                               cr(account_id, "1010", ccy, coll, memo=f"cash collateral to the lender for {borrow_id}")],
                              f"collat:{borrow_id}:open", ref_type="borrow", ref_id=borrow_id))
    reason = REASON_PENDING if order_id else REASON_MANUAL
    reb_txt = "unavailable" if locate_q.rebate_rate is None else str(qprice(locate_q.rebate_rate))
    conn.execute(
        "INSERT INTO borrows(borrow_id, command_id, account_id, instrument_id, qty, qty_returned, fee_rate, rebate_rate, tier, lender, collateral_c, currency,"
        " opened_ts, opened_date, status, closed_ts, order_id, reason, last_mark_date, recall_notice) VALUES (?,?,?,?,?,0,?,?,?,?,?,?,?,?,'OPEN',NULL,?,?,NULL,NULL)",
        (borrow_id, ctx.command_id, account_id, iid, qty, str(qprice(fee)) if fee is not None else "0", reb_txt, locate_q.tier, locate_q.lender, coll, ccy,
         ctx.ts, ctx.today, order_id, reason))
    ctx.touched_accounts.add(account_id)
    ctx.touched_instruments.add((account_id, iid))
    ctx.event("BORROW_OPENED", account_id=account_id, instrument_id=iid, ref_type="borrow", ref_id=borrow_id, qty=qty, tier=locate_q.tier,
              fee_rate=str(fee) if fee is not None else None, rebate_rate=reb_txt, collateral_c=coll, mark=str(mark), order_id=order_id, lender=locate_q.lender,
              entry_id=entry_id, sweep_id=funding.sweep_id, reason=reason)
    return borrow_id


def consume_for_short(ctx: TxContext, borrow_id: Optional[str], qty: int, fill_id: str) -> None:
    """A short fill backed by ``borrow_id`` (§13.1.3): the borrow must be OPEN with enough outstanding quantity for the
    fill and belong to the fill's (account, instrument).  The ``reason`` ('pending short sale') is cleared once the
    order is fully filled — the short position now backs the whole borrow; after a PARTIAL fill the unfilled remainder
    still pends, so the reason stays (L12 accepts a short position or a reason; without it a cover of the filled part
    while the rest is working would leave an open borrow that backs nothing and roll the cover back).  The order's
    ``filled_qty`` already includes this fill (``orders`` advances it before ``execution.apply_fill``)."""
    if not borrow_id:
        raise RejectError("NOT_IN_THIS_BUILD", "a short fill needs the borrow opened at submit: no borrow backs this fill")
    conn = ctx.conn
    b = _borrow(conn, borrow_id)
    if b["status"] != "OPEN":
        raise ConflictError("BORROW_CLOSED", f"borrow {borrow_id} is closed; it cannot back a new short fill")
    qty = int(qty)
    out = _outstanding(b)
    if qty <= 0 or qty > out:
        raise ConflictError("BORROW_SHORTFALL", f"borrow {borrow_id} holds {out:,} but the short fill is {qty:,}", {"outstanding": out, "qty": qty})
    f = conn.execute("SELECT account_id, instrument_id FROM fills WHERE fill_id=?", (fill_id,)).fetchone()
    if f is not None and (f["account_id"] != b["account_id"] or f["instrument_id"] != b["instrument_id"]):
        raise RejectError("BORROW_MISMATCH", f"borrow {borrow_id} is {b['account_id']} {b['instrument_id']}, the fill is {f['account_id']} {f['instrument_id']}")
    fully_filled = True
    if b["order_id"]:
        o = conn.execute("SELECT qty, filled_qty FROM orders WHERE order_id=?", (b["order_id"],)).fetchone()
        fully_filled = o is None or int(o["filled_qty"]) >= int(o["qty"])
    if fully_filled:
        conn.execute("UPDATE borrows SET reason=NULL WHERE borrow_id=?", (borrow_id,))
    ctx.touched_accounts.add(b["account_id"])
    ctx.touched_instruments.add((b["account_id"], b["instrument_id"]))


# ---- returns (§13.1.5-6) ----------------------------------------------------------------------------------------------------
def return_borrow(ctx: TxContext, services, borrow_id: str, qty: int, fill_id: Optional[str], reason: str, idem_suffix: Optional[str] = None) -> dict:
    """Return ``qty`` of a borrow: collateral back pro rata (``Dr 1010 / Cr 1400``), the returned share of the
    instrument's accrued fees and rebate settled, the row updated (CLOSED when nothing is outstanding).  Refused with
    409 BACKS_SHORT when the (account, instrument) would be left with less borrowed than sold short (L12)."""
    services = _services(ctx, services)
    conn = ctx.conn
    b = _borrow(conn, borrow_id)
    if b["status"] != "OPEN":
        raise ConflictError("BORROW_CLOSED", f"borrow {borrow_id} is already closed")
    out = _outstanding(b)
    qty = int(qty)
    if qty <= 0 or qty > out:
        raise RejectError("QTY_INVALID", f"borrow {borrow_id} has {out:,} outstanding; cannot return {qty:,}", {"outstanding": out, "qty": qty})
    aid, iid, ccy = b["account_id"], b["instrument_id"], b["currency"]
    g = _group(conn, aid, iid)
    if g["total"] - qty < g["short"]:
        raise ConflictError("BACKS_SHORT", f"borrow {borrow_id} still backs a short of {g['short']:,} {iid}: {g['total']:,} borrowed, returning {qty:,} would leave"
                            f" {g['total'] - qty:,}; cover first", {"short": g["short"], "borrowed": g["total"], "qty": qty, "borrow_id": borrow_id})
    full = qty == out
    coll_c = int(b["collateral_c"])
    back = pro_rata_c(coll_c, qty, out)
    suffix = idem_suffix or (fill_id if fill_id else f"{reason}:{ctx.request_id}")
    inst = _inst_of(services, conn, iid)
    sym = inst.symbol if inst is not None else iid
    entry_id = None
    if back > 0:
        entry_id = ctx.post(Entry("COLLATERAL", f"return {qty:,} {sym} to the lender ({reason}): collateral {fmt_money(back)} back from {borrow_id}",
                                  [dr(aid, "1010", ccy, back, memo=f"collateral returned by the lender for {borrow_id}"),
                                   cr(aid, "1400", ccy, back, instrument_id=iid, memo=borrow_id)],
                                  f"collat:{borrow_id}:return:{suffix}", ref_type="borrow", ref_id=borrow_id))
    fee_bal = balance(conn, aid, "2310", ccy, iid)
    reb_bal = balance(conn, aid, "1410", ccy, iid)
    if g["total"] - qty == 0:
        fee_c, reb_c = fee_bal, reb_bal
    else:
        fee_c, reb_c = pro_rata_c(fee_bal, qty, g["total"]), pro_rata_c(reb_bal, qty, g["total"])
    pay_id = settle_accrued(ctx, services, b, fee_c, reb_c, f"return:{suffix}")
    conn.execute("UPDATE borrows SET qty_returned=qty_returned+?, collateral_c=collateral_c-? WHERE borrow_id=?", (qty, back, borrow_id))
    if full:
        conn.execute("UPDATE borrows SET status='CLOSED', closed_ts=?, reason=NULL WHERE borrow_id=?", (ctx.ts, borrow_id))
    elif b["reason"] == REASON_PENDING and b["order_id"] and g["short"] > 0:
        # the order has ended (a live order never has free quantity to return) and the short backs what stays borrowed
        o = conn.execute("SELECT status FROM orders WHERE order_id=?", (b["order_id"],)).fetchone()
        if o is None or o["status"] not in LIVE_ORDER_STATUSES:
            conn.execute("UPDATE borrows SET reason=NULL WHERE borrow_id=?", (borrow_id,))
    ctx.touched_accounts.add(aid)
    ctx.touched_instruments.add((aid, iid))
    ctx.event("BORROW_RETURNED", account_id=aid, instrument_id=iid, ref_type="borrow", ref_id=borrow_id, qty=qty, reason=reason, collateral_returned_c=back,
              fees_paid_c=max(fee_c, 0), rebate_received_c=max(reb_c, 0), closed=full, fill_id=fill_id, entry_id=entry_id, pay_entry_id=pay_id)
    return {"borrow_id": borrow_id, "qty": qty, "reason": reason, "collateral_returned_c": back, "fees_paid_c": max(fee_c, 0), "rebate_received_c": max(reb_c, 0),
            "closed": full, "outstanding": out - qty, "entry_id": entry_id, "pay_entry_id": pay_id}


def return_for_cover(ctx: TxContext, services, account_id: str, instrument_id: str, qty: int, fill_id: str) -> List[dict]:
    """§10.7 step 5 / §13.1.5, called by ``execution.apply_fill`` after a BUY_TO_COVER relieved the SHORT lots: return the
    borrowed quantity that now backs nothing (borrowed − short − what live short orders still need), from borrows
    whose order has ended first, oldest first.  Returns the ``return_borrow`` results (empty when nothing is free)."""
    services = _services(ctx, services)
    g = _group(ctx.conn, account_id, instrument_id)
    left = g["excess"]
    out: List[dict] = []
    if left <= 0:
        return out
    ordered = sorted(g["auto"], key=lambda r: (1 if r["order_status"] in LIVE_ORDER_STATUSES else 0, int(r["opened_ts"]), r["borrow_id"]))
    for r in ordered:
        if left <= 0:
            break
        take = min(left, _outstanding(r))
        if take <= 0:
            continue
        out.append(return_borrow(ctx, services, r["borrow_id"], take, fill_id, "cover"))
        left -= take
    return out


def release_for_cancel(ctx: TxContext, services, borrow_id: str, order_id: str, reason: str) -> Optional[dict]:
    """``order.cancel`` / ``order.expire`` of a short order that filled nothing: the whole borrow goes back at once
    (``collat:{borrow}:return:cancel:{order}``)."""
    b = _borrow(ctx.conn, borrow_id)
    if b["status"] != "OPEN" or _outstanding(b) <= 0:
        return None
    return return_borrow(ctx, services, borrow_id, _outstanding(b), None, f"cancel: {reason}", idem_suffix=f"cancel:{order_id}")


# ---- BORROW_CLEANUP (§11.2) --------------------------------------------------------------------------------------------------
def cleanup_due(conn, lifetime_ids: Optional[List[str]] = None) -> List[dict]:
    """The job's keys: borrowed quantity that backs nothing (the order ended cancelled / expired / partly filled), per
    borrow, oldest first; only borrows whose order is not live and that are not manual pre-borrows; OPEN lifetimes only."""
    sql = ("SELECT DISTINCT b.account_id, b.instrument_id FROM borrows b JOIN accounts a ON a.account_id=b.account_id JOIN lifetimes l ON l.lifetime_id=a.lifetime_id"
           " WHERE b.status='OPEN' AND l.status='OPEN'")
    params: list = []
    if lifetime_ids is not None:
        if not lifetime_ids:
            return []
        sql += f" AND a.lifetime_id IN ({','.join('?' for _ in lifetime_ids)})"
        params = list(lifetime_ids)
    out: List[dict] = []
    for grp in conn.execute(sql + " ORDER BY b.account_id, b.instrument_id", params).fetchall():
        g = _group(conn, grp["account_id"], grp["instrument_id"])
        left = g["excess"]
        if left <= 0:
            continue
        for r in g["auto"]:
            if left <= 0:
                break
            if r["order_status"] in LIVE_ORDER_STATUSES:
                continue
            take = min(left, _outstanding(r))
            if take <= 0:
                continue
            out.append({"borrow_id": r["borrow_id"], "account_id": r["account_id"], "instrument_id": r["instrument_id"], "qty": take,
                        "order_id": r["order_id"], "order_status": r["order_status"]})
            left -= take
    return out


def cleanup_borrow(ctx: TxContext, services, borrow_id: str, d: str) -> dict:
    """Return one borrow's unused quantity (recomputed now; ``collat:{borrow}:return:cleanup:{date}``)."""
    b = _borrow(ctx.conn, borrow_id)
    lid = account_row(ctx.conn, b["account_id"])["lifetime_id"]
    due = [c for c in cleanup_due(ctx.conn, [lid]) if c["borrow_id"] == borrow_id]
    if not due:
        return {"borrow_id": borrow_id, "qty": 0, "skipped": "nothing to return: every borrowed share backs a short or a live order"}
    return return_borrow(ctx, services, borrow_id, int(due[0]["qty"]), None, "cleanup", idem_suffix=f"cleanup:{d}")


def release_unused(ctx: TxContext, services, lifetime_id: str, d: str) -> List[dict]:
    """``book.reset_lifetime`` (WP-23): return every borrow quantity of the lifetime that backs only cancelled orders."""
    return [cleanup_borrow(ctx, services, c["borrow_id"], d) for c in cleanup_due(ctx.conn, [lifetime_id])]


# ---- collateral marks (§11.2 COLLATERAL_MARK, §13.1.4) -------------------------------------------------------------------------
def collateral_mark_lines(ctx, borrow, mark, mark_date: str) -> Tuple[List[Line], int]:
    """One movement to 102 % of ``qty_outstanding × mark``: ``(lines, delta_c)``; a call is ``Dr 1400 / Cr 1010``, an
    excess ``Dr 1010 / Cr 1400``; ``([], 0)`` when the collateral already matches.  Reads nothing but the row."""
    services = getattr(ctx, "services", None)
    inst = _inst_of(services, getattr(ctx, "conn", None), borrow["instrument_id"]) if services is not None else None
    required = collateral_required_c(_outstanding(borrow), mark, inst)
    delta = required - int(borrow["collateral_c"])
    aid, iid, ccy, bid = borrow["account_id"], borrow["instrument_id"], borrow["currency"], borrow["borrow_id"]
    if delta > 0:
        return [dr(aid, "1400", ccy, delta, instrument_id=iid, memo=bid), cr(aid, "1010", ccy, delta, memo=f"collateral call {mark_date}")], delta
    if delta < 0:
        return [dr(aid, "1010", ccy, -delta, memo=f"collateral excess returned {mark_date}"), cr(aid, "1400", ccy, -delta, instrument_id=iid, memo=bid)], delta
    return [], 0


def collateral_mark(ctx: TxContext, services, borrow_id: str, mark_date: str, mark=None, mark_note: Optional[str] = None) -> dict:
    """``required = to_c(1.02 × qty_outstanding × close)``, ``delta = required − collateral_c``; a call is funded
    (``fund(immediate=True, overdraft=True)``: a Treasury sweep linked to the borrow, else posted anyway with the
    FUNDING_SHORTFALL item of §11.6); one entry ``cmark:{borrow}:{date}``; ``collateral_c`` / ``last_mark_date`` updated.
    Without ``mark`` the CLOSE of ``mark_date`` must exist in ``marks`` (MARK_UNAVAILABLE otherwise; the job retries)."""
    services = _services(ctx, services)
    conn = ctx.conn
    b = _borrow(conn, borrow_id)
    if b["status"] != "OPEN":
        raise ConflictError("BORROW_CLOSED", f"borrow {borrow_id} is closed; nothing to mark")
    if b["last_mark_date"] and b["last_mark_date"] > mark_date:
        raise RejectError("MARK_ORDER", f"borrow {borrow_id} was last marked {b['last_mark_date']}; {mark_date} is earlier")
    if b["last_mark_date"] == mark_date:
        return {"borrow_id": borrow_id, "mark_date": mark_date, "delta_c": 0, "required_c": int(b["collateral_c"]), "skipped": "already marked for this date"}
    if mark is None:
        got = close_mark_for(conn, b["instrument_id"], mark_date, exact=True)
        if got is None:
            raise RejectError("MARK_UNAVAILABLE", f"no CLOSE mark recorded for {b['instrument_id']} on {mark_date}: the collateral mark waits for MARKS_RECORD (never defaulted)")
        mark, mark_note = got
    mark = qprice(mark)
    lines, delta = collateral_mark_lines(ctx, b, mark, mark_date)
    required = int(b["collateral_c"]) + delta
    aid, iid, ccy = b["account_id"], b["instrument_id"], b["currency"]
    funding = None
    entry_id = None
    if delta > 0:
        funding = execution.fund(ctx, aid, ccy, delta, "borrow", borrow_id, None, immediate=True, overdraft=True, services=services)
    if delta != 0:
        what = "call" if delta > 0 else "excess returned"
        memo = (f"collateral mark {mark_date} on {borrow_id}: 102 % of {_outstanding(b):,} x {mark} = {fmt_money(required)} ({mark_note or 'mark given'}); "
                f"{what} {fmt_money(abs(delta))} (SHORT_COLLATERAL)")
        entry_id = ctx.post(Entry("COLLATERAL", memo, lines, f"cmark:{borrow_id}:{mark_date}", ref_type="borrow", ref_id=borrow_id))
    conn.execute("UPDATE borrows SET collateral_c=?, last_mark_date=? WHERE borrow_id=?", (required, mark_date, borrow_id))
    ctx.touched_accounts.add(aid)
    ctx.touched_instruments.add((aid, iid))
    ctx.event("COLLATERAL_MARKED", account_id=aid, instrument_id=iid, ref_type="borrow", ref_id=borrow_id, mark=str(mark), mark_date=mark_date, required_c=required,
              delta_c=delta, entry_id=entry_id, sweep_id=None if funding is None else funding.sweep_id, funded=None if funding is None else funding.funded)
    return {"borrow_id": borrow_id, "mark_date": mark_date, "mark": str(mark), "mark_note": mark_note, "required_c": required, "delta_c": delta, "entry_id": entry_id,
            "funding": None if funding is None else funding.to_dict()}


# ---- recall notices (§12.2; never a recall, never a buy-in) ---------------------------------------------------------------------
def _ratio(r) -> Optional[Decimal]:
    tv = int(r["total_volume"] or 0)
    return None if tv <= 0 else qprice((Decimal(int(r["short_volume"])) / Decimal(tv)).quantize(Decimal("0.0001")))


def recall_notices(ctx: TxContext, services, today: Optional[date] = None) -> List[dict]:
    """For every (account, instrument) with an OPEN borrow: when the three most recent FINRA rows up to ``today`` are
    consecutive sessions with ratio ≥ 0.80, open ONE INFO attention ``HTB_TIGHTENING`` (auto-resolve) and note it on
    the borrows; when the condition no longer holds, resolve it.  Nothing else happens: no recall, no buy-in."""
    conn = ctx.conn
    cal = ctx.cal if getattr(ctx, "cal", None) is not None else services.cal
    today = today or ny_of(ctx.ts).date()
    out: List[dict] = []
    groups = conn.execute("SELECT DISTINCT b.account_id, b.instrument_id, i.symbol FROM borrows b JOIN instruments i ON i.instrument_id=b.instrument_id"
                          " WHERE b.status='OPEN' ORDER BY b.account_id, b.instrument_id").fetchall()
    for g in groups:
        aid, iid, sym = g["account_id"], g["instrument_id"], g["symbol"]
        rows = conn.execute("SELECT day, short_volume, total_volume FROM finra_short WHERE symbol=? AND day<=? ORDER BY day DESC LIMIT 3", (sym, today.isoformat())).fetchall()
        tight = False
        sessions: List[dict] = []
        if len(rows) == 3:
            days = [date.fromisoformat(r["day"]) for r in rows]
            consecutive = days[1] == cal.prev_business_day(days[0]) and days[2] == cal.prev_business_day(days[1])
            ratios = [_ratio(r) for r in rows]
            sessions = [{"day": r["day"], "ratio": None if x is None else str(x)} for r, x in zip(rows, ratios)]
            tight = consecutive and all(x is not None and x >= tables.HTB_RECALL_RATIO for x in ratios)
        item = conn.execute("SELECT id FROM attention WHERE code=? AND account_id=? AND ref_id=? AND resolved_ts IS NULL", (HTB_CODE, aid, iid)).fetchone()
        if tight and item is None:
            txt = ", ".join(f"{s['day']} {s['ratio']}" for s in sessions)
            msg = f"{sym}: {HTB_MESSAGE} (FINRA short-volume ratio >= 0.80 for three consecutive sessions: {txt}); no recall or buy-in is generated"
            att = ctx.attention("INFO", "lending", HTB_CODE, msg, {"account_id": aid, "ref_type": "instrument", "ref_id": iid, "sessions": sessions, "auto_resolve": 1})
            conn.execute("UPDATE borrows SET recall_notice=? WHERE account_id=? AND instrument_id=? AND status='OPEN'", (f"{today.isoformat()}: {HTB_MESSAGE} (INFO)", aid, iid))
            ctx.event("ATTENTION", account_id=aid, instrument_id=iid, ref_type="instrument", ref_id=iid, code=HTB_CODE, severity="INFO", sessions=sessions)
            out.append({"account_id": aid, "instrument_id": iid, "opened": True, "attention_id": att, "sessions": sessions})
        elif not tight and item is not None:
            conn.execute("UPDATE attention SET resolved_ts=? WHERE id=?", (ctx.ts, int(item["id"])))
            conn.execute("UPDATE borrows SET recall_notice=NULL WHERE account_id=? AND instrument_id=? AND status='OPEN'", (aid, iid))
            out.append({"account_id": aid, "instrument_id": iid, "resolved": True, "attention_id": int(item["id"]), "sessions": sessions})
    return out


# ---- views ---------------------------------------------------------------------------------------------------------------------
def borrow_view(conn, borrow_id: str) -> dict:
    """The row plus ``outstanding``, the instrument's accrued fee / rebate balances and the order's status (API shape)."""
    b = _borrow(conn, borrow_id)
    d = dict(b)
    d["outstanding"] = _outstanding(b)
    d["accrued_fees_c"] = balance(conn, b["account_id"], "2310", b["currency"], b["instrument_id"])
    d["accrued_rebate_c"] = balance(conn, b["account_id"], "1410", b["currency"], b["instrument_id"])
    d["collateral_posted_c"] = balance(conn, b["account_id"], "1400", b["currency"], b["instrument_id"])
    o = conn.execute("SELECT status, qty, filled_qty FROM orders WHERE order_id=?", (b["order_id"],)).fetchone() if b["order_id"] else None
    d["order_status"] = None if o is None else o["status"]
    return d


# ---- handlers (§4.1 credit.*) ---------------------------------------------------------------------------------------------------
def _sweep_for(conn, borrow_id: str) -> Optional[dict]:
    r = conn.execute("SELECT transfer_id, from_account, to_account, currency, amount_c, linked_type, linked_id, shortfall_c FROM transfers"
                     " WHERE kind='SWEEP_IN' AND linked_type='borrow' AND linked_id=? ORDER BY transfer_id DESC LIMIT 1", (borrow_id,)).fetchone()
    return None if r is None else dict(r)


def _qty(payload: dict, key: str = "qty") -> int:
    try:
        q = int(payload.get(key))
    except (TypeError, ValueError) as e:
        raise RejectError("QTY_INVALID", f"{key} must be a whole number, got {payload.get(key)!r}") from e
    if q <= 0:
        raise RejectError("QTY_INVALID", f"{key} must be positive")
    return q


def h_borrow_stock(ctx: TxContext, payload: dict) -> dict:
    """``credit.borrow_stock {account_id, instrument_id, qty}`` (POST /api/credit/borrows): a locate, then a manual borrow
    scaled to the availability; 409 NO_BORROW / INSUFFICIENT_FUNDS."""
    services = _services(ctx)
    aid = str(payload.get("account_id") or "")
    iid = str(payload.get("instrument_id") or "")
    acct = account_row(ctx.conn, aid)
    if acct is None:
        raise RejectError("ACCOUNT_UNKNOWN", f"account {aid} does not exist")
    inst = services.instruments.get(iid, ctx.conn)
    if inst is None:
        raise RejectError("INSTRUMENT_UNKNOWN", f"instrument {iid} is not in the master")
    if not services.instruments.tradeable_in(inst, account_kind_for(acct)):
        raise RejectError("NOT_TRADEABLE_HERE", f"{inst.symbol} does not trade in {aid}")
    qty = _qty(payload)
    loc = locate(services, aid, inst, qty, conn=ctx.conn)
    if not loc.available or int(loc.qty_available) <= 0:
        raise ConflictError("NO_BORROW", f"no borrow available for {inst.symbol}: {loc.basis}", {"locate": loc.to_dict()})
    q = min(qty, int(loc.qty_available))
    bid = open_borrow(ctx, services, aid, inst, q, loc, None)
    return {"borrow": borrow_view(ctx.conn, bid), "collateral_transfer": _sweep_for(ctx.conn, bid), "locate": loc.to_dict(),
            "scaled_from": qty if q < qty else None}


def h_return_stock(ctx: TxContext, payload: dict) -> dict:
    """``credit.return_stock {borrow_id, qty?}`` (POST /api/credit/borrows/{id}/return): a manual return of ``qty``
    (default: everything outstanding); 409 BACKS_SHORT while the quantity still backs a short."""
    services = _services(ctx)
    bid = str(payload.get("borrow_id") or "")
    b = _borrow(ctx.conn, bid)
    qty = _qty(payload) if payload.get("qty") is not None else _outstanding(b)
    res = return_borrow(ctx, services, bid, qty, None, "manual", idem_suffix=f"manual:{ctx.request_id}")
    return {"borrow": borrow_view(ctx.conn, bid), "return": res}


def h_collateral_mark(ctx: TxContext, payload: dict) -> dict:
    """``credit.collateral_mark {borrow_id, mark_date, mark?, mark_note?}`` — the COLLATERAL_MARK job's command (key
    ``{borrow_id}:{date}``); without ``mark`` the recorded CLOSE of ``mark_date`` is used."""
    services = _services(ctx)
    bid = str(payload.get("borrow_id") or "")
    md = str(payload.get("mark_date") or "")
    try:
        date.fromisoformat(md)
    except ValueError as e:
        raise RejectError("DATE_INVALID", f"mark_date must be YYYY-MM-DD, got {md!r}") from e
    mark = payload.get("mark")
    if mark is not None:
        try:
            mark = qprice(str(mark))
        except Exception as e:   # noqa: BLE001
            raise RejectError("PRICE_INVALID", f"mark is not a price: {payload.get('mark')!r}") from e
        if mark <= 0:
            raise RejectError("PRICE_INVALID", "mark must be positive")
    return collateral_mark(ctx, services, bid, md, mark, payload.get("mark_note"))


def h_accrue_borrow(ctx: TxContext, payload: dict) -> dict:
    """``credit.accrue_borrow {borrow_id, night, mark?, mark_note?, sofr?, sofr_obs?}`` — one night of one borrow (the
    ACCRUE_DAILY job runs it per key ``bfee:{borrow}:{D}``; both the fee and the rebate post here)."""
    services = _services(ctx)
    bid = str(payload.get("borrow_id") or "")
    night = str(payload.get("night") or "")
    try:
        date.fromisoformat(night)
    except ValueError as e:
        raise RejectError("DATE_INVALID", f"night must be YYYY-MM-DD, got {night!r}") from e
    mark = payload.get("mark")
    sofr = payload.get("sofr")
    return accrue_night(ctx, services, bid, night, None if mark is None else qprice(str(mark)), payload.get("mark_note"),
                        None if sofr is None else _dec(sofr), payload.get("sofr_obs"))


def h_borrow_cleanup(ctx: TxContext, payload: dict) -> dict:
    """``credit.borrow_cleanup {date?, borrow_id?}`` — BORROW_CLEANUP: one borrow (the job's key) or every due borrow."""
    services = _services(ctx)
    d = str(payload.get("date") or ny_of(ctx.ts).date().isoformat())
    bid = payload.get("borrow_id")
    if bid:
        return {"date": d, "returns": [cleanup_borrow(ctx, services, str(bid), d)]}
    due = cleanup_due(ctx.conn)
    return {"date": d, "returns": [cleanup_borrow(ctx, services, c["borrow_id"], d) for c in due]}


def h_recall_check(ctx: TxContext, payload: dict) -> dict:
    """``credit.recall_check {today?}`` (light): the INFO notice rule of §12.2 over every open borrow."""
    services = _services(ctx)
    today = date.fromisoformat(str(payload["today"])) if payload.get("today") else None
    return {"notices": recall_notices(ctx, services, today)}


def register(bus) -> None:
    bus.register("credit.borrow_stock", h_borrow_stock)
    bus.register("credit.return_stock", h_return_stock)
    bus.register("credit.collateral_mark", h_collateral_mark)
    bus.register("credit.accrue_borrow", h_accrue_borrow)
    bus.register("credit.borrow_cleanup", h_borrow_cleanup)
    bus.register("credit.recall_check", h_recall_check, light=True)


def bind(services) -> None:
    """Expose this module as ``services.lending`` (what ``orders`` / ``execution`` look up at run time)."""
    import sys
    services.lending = sys.modules[__name__]
