"""The pure matching rules of the order book (TERMINAL_DESIGN.md §10.5, §10.6, §10.9): ``OrderRow``, ``Decision``,
``exec_price``, ``fill_cap``, ``evaluate`` and ``evaluate_package``.  No I/O anywhere in this module: a row, one observed
``QuoteRecord``, one ``SessionState`` and the clock in, a decision out.  The conventions are stated in ``orders.py``
(the package's front door, which re-exports everything here) and in docs/TERMINAL_EXECUTION.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_EVEN
from typing import Dict, List, Optional, Tuple

from . import config, execution, tables
from .clock import ny_of
from .instruments import Instrument
from .quotes import QuoteRecord, is_executable
from .units import qprice

ORDER_TYPES = ("MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAILING_STOP")
TIFS = ("DAY", "GTC")
BUY_SIDES = execution.BUY_SIDES
SELL_SIDES = execution.SELL_SIDES
OPENING_SIDES = execution.OPENING_SIDES
CASH_SIDES = ("BUY", "SELL", "SELL_SHORT", "BUY_TO_COVER")
OPTION_SIDES = ("BTO", "STO", "BTC", "STC")
FUTURES_SIDES = ("BUY", "SELL")
OPEN_STATUSES = ("WORKING", "PARTIAL")
LIVE_STATUSES = ("WORKING", "PARTIAL", "SUSPENDED", "WAITING_PARENT")
STOP_TYPES = ("STOP", "STOP_LIMIT", "TRAILING_STOP")
NEEDS_QUOTE_TYPES = ("MARKET", "STOP", "STOP_LIMIT", "TRAILING_STOP")
OPTION_LAST_OPEN_ET = "15:30"


# ---- rows and decisions -------------------------------------------------------------------------------------------------
@dataclass
class OrderRow:
    """The columns ``evaluate()`` reads plus the instrument facts it needs (kind, lot, tick); built by ``from_row``."""
    order_id: str
    account_id: str
    instrument_id: str
    side: str
    qty: int
    filled_qty: int
    order_type: str
    limit_px: Optional[Decimal]
    stop_px: Optional[Decimal]
    trail_amt: Optional[Decimal]
    trail_pct: Optional[Decimal]
    trail_ref_px: Optional[Decimal]
    fill_mode: str
    tif: str
    status: str
    triggered: bool
    first_eval_done: bool
    last_bar_ts: Optional[int]
    last_quote_time: Optional[int]
    session_group: str
    expires_ts: int
    participation: Decimal
    borrow_id: Optional[str] = None
    package_id: Optional[str] = None
    parent_order_id: Optional[str] = None
    ratio: Optional[Decimal] = None
    kind: str = "EQUITY"
    lot: int = 1
    tick: Decimal = Decimal("0.01")
    multiplier: Decimal = Decimal(1)
    price_scale: Decimal = Decimal(1)

    @property
    def remaining(self) -> int:
        return max(0, int(self.qty) - int(self.filled_qty))

    @classmethod
    def from_row(cls, r, inst: Optional[Instrument] = None) -> "OrderRow":
        def d(v):
            return None if v is None else Decimal(str(v))
        o = cls(r["order_id"], r["account_id"], r["instrument_id"], r["side"], int(r["qty"]), int(r["filled_qty"]), r["order_type"],
                d(r["limit_px"]), d(r["stop_px"]), d(r["trail_amt"]), d(r["trail_pct"]), d(r["trail_ref_px"]), r["fill_mode"], r["tif"], r["status"],
                bool(r["triggered"]), bool(r["first_eval_done"]), r["last_bar_ts"], r["last_quote_time"], r["session_group"], int(r["expires_ts"]),
                Decimal(str(r["participation"] or config.PARTICIPATION_RATE)), r["borrow_id"], r["package_id"], r["parent_order_id"], d(r["ratio"]))
        if inst is not None:
            o.kind, o.lot, o.tick, o.multiplier, o.price_scale = inst.kind, int(inst.lot), inst.tick, inst.multiplier, inst.price_scale
        return o


@dataclass
class Decision:
    """What one observation does to one order.  ``action``: NONE | FILL | TRIGGER | RATCHET.  A FILL carries the price,
    the capped quantity and the price basis; TRIGGER = the stop was reached but nothing is fillable this pass (qty 0);
    RATCHET = a trailing stop moved (``stop_px`` / ``trail_ref_px`` are the new values).  ``first_look`` marks the order's
    first executable look (persisted as ``first_eval_done``); ``triggered`` says this pass triggered the stop."""
    action: str
    price: Optional[Decimal]
    qty: int
    reason: str
    bar_ts: Optional[int]
    quote_time: Optional[int] = None
    price_basis: str = ""
    triggered: bool = False
    stop_px: Optional[Decimal] = None
    trail_ref_px: Optional[Decimal] = None
    cap_note: str = ""
    first_look: bool = False
    note: str = ""

    @property
    def wants_command(self) -> bool:
        return self.action in ("FILL", "TRIGGER", "RATCHET") or self.first_look

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        for k in ("price", "stop_px", "trail_ref_px"):
            d[k] = None if d[k] is None else str(d[k])
        return d


@dataclass
class LegDecision:
    order_id: str
    instrument_id: str
    side: str
    qty: int
    price: Decimal
    price_basis: str


@dataclass
class PackageDecision:
    """``evaluate_package``: FILL with every leg's price and quantity from ONE chain observation, or NONE with the
    reason (AON_SIZE / AON_STALE / AON_OBSERVATION / NOT_OPEN / EXPIRED_PENDING / AWAITING_SESSION) and the leg."""
    action: str
    reason: str
    quote_time: Optional[int]
    legs: List[LegDecision] = field(default_factory=list)
    leg: Optional[str] = None
    note: str = ""

    def to_dict(self) -> dict:
        return {"action": self.action, "reason": self.reason, "quote_time": self.quote_time, "leg": self.leg, "note": self.note,
                "legs": [{"order_id": l.order_id, "instrument_id": l.instrument_id, "side": l.side, "qty": l.qty, "price": str(l.price), "price_basis": l.price_basis}
                         for l in self.legs]}


# ---- pure rules (§10.5, §10.6, §10.9) -------------------------------------------------------------------------------------
def _q_tick(px: Decimal, tick: Decimal) -> Decimal:
    tick = tick if tick and tick > 0 else Decimal("0.01")
    return qprice((px / tick).to_integral_value(rounding=ROUND_HALF_EVEN) * tick)


def on_tick(px: Decimal, tick: Decimal) -> bool:
    if tick is None or tick <= 0:
        return True
    r = px / tick
    return r == r.to_integral_value()


def fx_pip(instrument_id: str) -> Decimal:
    return Decimal("0.01") if "JPY" in (instrument_id or "") else Decimal("0.0001")


def exec_price(order: OrderRow, rec: QuoteRecord) -> Tuple[Optional[Decimal], str]:
    """§10.5 step 5: ``(px_exec, basis)``.  Options: Cboe ask (buys) / bid (sells), needing ``bid > 0 and ask >= bid``;
    equities / futures: the NBBO carried on the record when a fresh cached chain supplied one (never fetched for
    this), else the observed last; FX: mid +/- 0.2 pip against you.  ``(None, reason)`` when no price exists."""
    buy = order.side in BUY_SIDES
    if order.kind == "OPTION":
        if rec.bid is None or rec.ask is None or rec.bid <= 0 or rec.ask < rec.bid:
            return None, "NO_NBBO"
        return (qprice(rec.ask), "ask") if buy else (qprice(rec.bid), "bid")
    if order.kind == "FX":
        mid = rec.mid if rec.mid is not None else rec.last
        if mid is None:
            return None, "NO_QUOTE"
        spread = tables.FEES["DEFAULT"]["FX"]["spread_pips"] * fx_pip(order.instrument_id)
        return (qprice(mid + spread), "mid+0.2pip") if buy else (qprice(mid - spread), "mid-0.2pip")
    if rec.bid is not None and rec.ask is not None and rec.bid > 0 and rec.ask >= rec.bid and order.kind != "FUTURE":
        return (qprice(rec.ask), "ask") if buy else (qprice(rec.bid), "bid")
    if rec.last is None:
        return None, "NO_QUOTE"
    return qprice(rec.last), "last"


def trail_amount(order: OrderRow, ref: Decimal) -> Decimal:
    """The trail in price units: ``trail_amt`` as given, or ``ref x trail_pct / 100`` re-derived from the reference."""
    if order.trail_amt is not None:
        return Decimal(order.trail_amt)
    return ref * Decimal(order.trail_pct or 0) / Decimal(100)


def trail_stop(order: OrderRow, ref: Decimal) -> Decimal:
    """stop = ref - trail (sell) / ref + trail (buy), quantised to the tick."""
    t = trail_amount(order, ref)
    return _q_tick(ref - t if order.side in SELL_SIDES else ref + t, order.tick)


def fill_cap(order: OrderRow, rec: Optional[QuoteRecord], inst: Optional[Instrument], participation: Decimal) -> Tuple[int, str]:
    """§10.6: ``(qty cap this pass, disclosure)``.  Equities / futures: at most ``participation`` x the latest completed
    1-minute bar's volume, in lots, and never the same bar twice (NO_NEW_BAR); no bar or no volume -> the remaining
    quantity (NO_VOLUME); a completed bar with zero volume -> 0 (NO_VOLUME_THIS_BAR).  Options: the displayed ask
    (buys) / bid (sells) size (None -> remaining, disclosed; 0 -> 0).  FX: remaining (no volume concept)."""
    kind = inst.kind if inst is not None else order.kind
    lot = max(1, int(inst.lot if inst is not None else order.lot))
    remaining = order.remaining
    pct = f"{(participation * 100).normalize():f}"
    if kind in ("OPTION", "FX") and rec is not None and order.last_quote_time is not None and rec.quote_time is not None and int(rec.quote_time) <= int(order.last_quote_time):
        return 0, "this observation already filled the order (no newer chain time): SAME_OBSERVATION"
    if kind == "OPTION":
        size = (rec.ask_size if order.side in BUY_SIDES else rec.bid_size) if rec is not None else None
        side_word = "ask" if order.side in BUY_SIDES else "bid"
        if size is None:
            return remaining, f"no displayed {side_word} size on the chain: fills in full (disclosed)"
        size = int(size)
        if size <= 0:
            return 0, f"displayed {side_word} size is 0: nothing fillable this observation (NO_SIZE)"
        return min(remaining, size), f"displayed {side_word} size {size:,} contracts caps this pass"
    if kind == "FX":
        return remaining, "FX has no volume concept: fills in full (disclosed)"
    bar = rec.bar if rec is not None else None
    if order.last_bar_ts is not None and bar is not None and int(bar.t) <= int(order.last_bar_ts):
        return 0, f"no new 1-minute bar since the last fill (bar {ny_of(int(bar.t)).strftime('%H:%M')} already used): NO_NEW_BAR"
    if bar is None or bar.v is None:
        return remaining, "no 1-minute bar volume observed: fills in full (NO_VOLUME)"
    v = int(bar.v)
    if v == 0:
        return 0, f"the completed 1-minute bar at {ny_of(int(bar.t)).strftime('%H:%M')} traded no volume: NO_VOLUME_THIS_BAR"
    raw = (participation * Decimal(v) / Decimal(lot)).to_integral_value(rounding=ROUND_FLOOR) * lot
    cap = max(lot, int(raw))
    unit = "contracts" if kind == "FUTURE" else "shares"
    return min(remaining, cap), f"latest 1-minute bar volume {v:,}; {pct} % participation caps a pass at {cap:,} {unit}"


def evaluate(order: OrderRow, rec: Optional[QuoteRecord], session, now: int, participation: Optional[Decimal] = None,
             inst: Optional[Instrument] = None) -> Decision:
    """§10.5, exactly and purely (no I/O).  See the module docstring for the sentences this implements."""
    participation = Decimal(participation if participation is not None else order.participation)
    if order.status not in OPEN_STATUSES:
        return Decision("NONE", None, 0, "NOT_OPEN", None)
    if now > int(order.expires_ts):
        return Decision("NONE", None, 0, "EXPIRED_PENDING", None, note="past expires_ts: the lifecycle expires it; never fills after expiry")
    if session is None or not session.open:
        return Decision("NONE", None, 0, "AWAITING_SESSION", None, note=getattr(session, "phase", "CLOSED"))
    ok, reason = is_executable(rec, session, now)
    if not ok:
        return Decision("NONE", None, 0, reason, None)
    bar_ts = int(rec.bar.t) if rec.bar is not None else None
    qt = rec.quote_time
    px_exec, basis = exec_price(order, rec)
    if px_exec is None:
        return Decision("NONE", None, 0, basis, bar_ts, qt)
    first_look = not order.first_eval_done
    last = qprice(rec.last) if rec.last is not None else px_exec
    buy = order.side in BUY_SIDES
    triggered_now = False
    otype = order.order_type
    stop = order.stop_px
    if otype == "TRAILING_STOP":
        ref = order.trail_ref_px if order.trail_ref_px is not None else last
        if order.trail_ref_px is None or order.stop_px is None:
            return Decision("RATCHET", None, 0, "TRAIL_INIT", bar_ts, qt, stop_px=trail_stop(order, ref), trail_ref_px=ref, first_look=first_look,
                            note=f"trailing reference set from the observed last {last}")
        if not order.triggered:
            better = (last > ref) if not buy else (last < ref)
            if better:
                new_stop = trail_stop(order, last)
                return Decision("RATCHET", None, 0, "RATCHET", bar_ts, qt, stop_px=new_stop, trail_ref_px=last, first_look=first_look,
                                note=f"ratchet: reference {ref} -> {last}, stop {stop} -> {new_stop}")
            hit = (last <= stop) if not buy else (last >= stop)
            if not hit:
                return Decision("NONE", None, 0, "STOP_NOT_REACHED", bar_ts, qt, first_look=first_look, stop_px=stop, trail_ref_px=ref)
            triggered_now = True
    elif otype in ("STOP", "STOP_LIMIT") and not order.triggered:
        hit = (last >= stop) if buy else (last <= stop)
        if not hit:
            return Decision("NONE", None, 0, "STOP_NOT_REACHED", bar_ts, qt, first_look=first_look)
        triggered_now = True
    price, fill_basis, why = px_exec, basis, "MARKET"
    if otype == "STOP":
        fill_basis, why = "stop_print", "STOP_TRIGGERED"
    elif otype == "TRAILING_STOP":
        fill_basis, why = "stop_print", "TRAIL_TRIGGERED"
    elif otype in ("LIMIT", "STOP_LIMIT"):
        limit = order.limit_px
        marketable = (px_exec <= limit) if buy else (px_exec >= limit)
        crossed = (last <= limit) if buy else (last >= limit)
        if (first_look or triggered_now) and marketable:
            price, why = px_exec, "LIMIT_MARKETABLE"
        elif (not first_look) and crossed:
            price, fill_basis, why = qprice(limit), "limit", "LIMIT_CROSSED"
        else:
            return Decision("NONE", None, 0, "NOT_MARKETABLE", bar_ts, qt, triggered=triggered_now, first_look=first_look)
    cap, cap_note = fill_cap(order, rec, inst, participation)
    qty = min(order.remaining, cap)
    note = ""
    if triggered_now and otype in ("STOP", "TRAILING_STOP") and stop is not None:
        if (buy and price > stop) or ((not buy) and price < stop):
            note = f"gap through the stop {stop}: filled at the print {price} (STOP_RULE)"
    if qty <= 0:
        reason = next((r for r in ("NO_NEW_BAR", "NO_VOLUME_THIS_BAR", "SAME_OBSERVATION") if r in cap_note), "NO_SIZE")
        return Decision("TRIGGER" if triggered_now else "NONE", None, 0, reason, bar_ts, qt, fill_basis, triggered_now, cap_note=cap_note, first_look=first_look, note=note)
    return Decision("FILL", price, qty, why, bar_ts, qt, fill_basis, triggered_now, cap_note=cap_note, first_look=first_look, note=note)


def evaluate_package(pkg, legs: List[OrderRow], recs: Dict[str, Optional[QuoteRecord]], session, now: int) -> PackageDecision:
    """§10.9 all-or-none (pure): every leg WORKING/PARTIAL and unexpired, the session open, every leg's contract record
    executable (fresh chain NBBO) with displayed size >= its remaining quantity, all from ONE chain observation."""
    if not legs:
        return PackageDecision("NONE", "NOT_OPEN", None, note="no legs")
    if session is None or not session.open:
        return PackageDecision("NONE", "AWAITING_SESSION", None)
    for leg in legs:
        if leg.status not in OPEN_STATUSES:
            return PackageDecision("NONE", "NOT_OPEN", None, leg=leg.order_id)
        if now > int(leg.expires_ts):
            return PackageDecision("NONE", "EXPIRED_PENDING", None, leg=leg.order_id)
    out: List[LegDecision] = []
    times = set()
    for leg in legs:
        rec = recs.get(leg.instrument_id)
        ok, reason = is_executable(rec, session, now)
        if not ok:
            return PackageDecision("NONE", "AON_STALE", None, leg=leg.order_id, note=f"{leg.instrument_id}: {reason}")
        if rec.method != "chain" or rec.bid is None or rec.ask is None or rec.bid <= 0 or rec.ask < rec.bid:
            return PackageDecision("NONE", "AON_STALE", None, leg=leg.order_id, note=f"{leg.instrument_id}: no usable Cboe NBBO")
        buy = leg.side in BUY_SIDES
        size = rec.ask_size if buy else rec.bid_size
        if size is None or int(size) < leg.remaining:
            return PackageDecision("NONE", "AON_SIZE", rec.quote_time, leg=leg.order_id,
                                   note=f"{leg.instrument_id}: displayed {'ask' if buy else 'bid'} size {size} < {leg.remaining:,} remaining")
        times.add(rec.quote_time)
        out.append(LegDecision(leg.order_id, leg.instrument_id, leg.side, leg.remaining, qprice(rec.ask if buy else rec.bid), "ask" if buy else "bid"))
    if len(times) != 1:
        return PackageDecision("NONE", "AON_OBSERVATION", None, note=f"legs come from {len(times)} different chain observations")
    return PackageDecision("FILL", "AON_OK", times.pop(), out)


def match_idem(order_id: str, quote_time: Optional[int], bar_ts: Optional[int]) -> str:
    """The matcher's request id and the fill's idem_key for one observation of one order."""
    return f"match:{order_id}:{quote_time if quote_time is not None else 'none'}:{bar_ts if bar_ts is not None else 'none'}"
