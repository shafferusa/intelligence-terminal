"""Order validation and the preview (TERMINAL_DESIGN.md §10.2, §10.3; sentences STALE, SESSION, SWEEP, AVAILABLE).
``validate()`` raises the §10.2 codes (``RejectError`` 400 / ``ConflictError`` 409) and returns a ``ValidatedOrder`` that
carries the §10.3 preview it was computed from; ``preview()`` turns the rejections into ``blockers``; ``PreviewStore``
keeps the last 200 previews for 120 s so ``order.submit`` can detect a stale preview (PREVIEW_STALE).  The conventions
are stated in ``orders.py`` (which re-exports this module) and in docs/TERMINAL_EXECUTION.md.
"""
from __future__ import annotations

import threading
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any, Dict, List, Optional

from . import config, execution, tables
from .books import account_kind_for, account_row, lifetime_row
from .clock import ny_of, trade_date as _trade_date
from .commands import ConflictError, RejectError
from .db import get_setting
from .instruments import Instrument
from .ledger import position as _position
from .orders_rules import (BUY_SIDES, CASH_SIDES, FUTURES_SIDES, NEEDS_QUOTE_TYPES, OPENING_SIDES, OPTION_LAST_OPEN_ET, OPTION_SIDES, ORDER_TYPES,
                           SELL_SIDES, TIFS, OrderRow, evaluate, exec_price, fill_cap, on_tick, trail_stop)
from .quotes import QuoteRecord, freshness, is_executable
from .units import fmt_money, from_c, qprice, to_c


# ---- the preview store (§2.12) -------------------------------------------------------------------------------------------
class PreviewStore:
    """In-memory ring of the last 200 previews, each valid ``config.PREVIEW_TTL_S`` (120 s) from its ``as_of``."""

    def __init__(self, size: int = 200, ttl_s: int = config.PREVIEW_TTL_S):
        self._ring: deque = deque(maxlen=size)
        self._by_id: Dict[str, dict] = {}
        self._ttl = int(ttl_s)
        self._lock = threading.Lock()

    def put(self, preview: dict) -> str:
        pid = preview.get("preview_id") or f"pv:{uuid.uuid4().hex[:16]}"
        preview["preview_id"] = pid
        preview["expires_at"] = int(preview.get("as_of") or 0) + self._ttl
        with self._lock:
            if len(self._ring) == self._ring.maxlen:
                self._by_id.pop(self._ring[0], None)
            self._ring.append(pid)
            self._by_id[pid] = preview
        return pid

    def get(self, preview_id: str, now: Optional[int] = None) -> Optional[dict]:
        with self._lock:
            p = self._by_id.get(preview_id)
        if p is None:
            return None
        if now is not None and int(now) > int(p.get("expires_at") or 0):
            return None
        return p

    def __len__(self) -> int:
        return len(self._by_id)


def preview_store(services) -> PreviewStore:
    store = getattr(services, "previews", None)
    if store is None:
        store = PreviewStore()
        try:
            services.previews = store
        except Exception:   # noqa: BLE001 - a read-only services bundle keeps a fresh store per call (previews then never match)
            pass
    return store


# ---- quotes for validation / matching -------------------------------------------------------------------------------------
def quote_of(services, instrument_id: str, snapshot=None) -> Optional[QuoteRecord]:
    """The cached observation for an instrument: the command's mark snapshot first (immutable for the command), then
    ``services.cache.get`` (the QuoteCache of WP-01), then the mark provider's ``quote``.  Never fetches."""
    if snapshot is not None and hasattr(snapshot, "quote"):
        rec = snapshot.quote(instrument_id)
        if rec is not None:
            return rec
    cache = getattr(services, "cache", None)
    if cache is not None and hasattr(cache, "get"):
        rec = cache.get(instrument_id)
        if rec is not None:
            return rec
    marks = getattr(services, "marks", None)
    if marks is not None and hasattr(marks, "quote"):
        try:
            return marks.quote(instrument_id)
        except Exception:   # noqa: BLE001 - a provider without quotes is the same as no quote
            return None
    return None


def with_nbbo(services, inst: Instrument, rec: Optional[QuoteRecord], session, now: int) -> Optional[QuoteRecord]:
    """§10.5 step 5 for equities: when a fresh chain for the underlying is already cached (``services.chains.underlying_quote``)
    its bid/ask ride on a copy of the record; nothing is fetched for this."""
    if rec is None or inst.kind not in execution.EQUITY_KINDS:
        return rec
    chains = getattr(services, "chains", None)
    if chains is None or not hasattr(chains, "underlying_quote"):
        return rec
    try:
        u = chains.underlying_quote(inst.instrument_id)
    except Exception:   # noqa: BLE001 - the chain cache is an optional source; the Yahoo last stays the basis
        return rec
    if u is None or u.bid is None or u.ask is None or u.bid <= 0 or u.ask < u.bid:
        return rec
    ok, _ = is_executable(u, session, now)
    if not ok:
        return rec
    import copy
    out = copy.copy(rec)
    out.bid, out.ask, out.bid_size, out.ask_size = u.bid, u.ask, u.bid_size, u.ask_size
    return out


def _mark_price(services, snapshot, instrument_id: str) -> Optional[Decimal]:
    src = snapshot if snapshot is not None else getattr(services, "marks", None)
    if src is None or not hasattr(src, "mark"):
        return None
    try:
        v = src.mark(instrument_id)
    except Exception:   # noqa: BLE001
        return None
    if v is None or v.mark is None or v.quality == "unavailable":
        return None
    return qprice(v.mark)


def _dec(v, code: str, what: str) -> Decimal:
    try:
        d = Decimal(str(v).replace(",", "").strip())
    except Exception as e:   # noqa: BLE001
        raise RejectError(code, f"{what} is not a number: {v!r}") from e
    if not d.is_finite():
        raise RejectError(code, f"{what} is not a finite number: {v!r}")
    return d


def _s(x) -> Optional[str]:
    return None if x is None else str(x)


def _m(c: Optional[int]) -> Optional[str]:
    return None if c is None else str(from_c(int(c)))


@dataclass
class ValidatedOrder:
    """Everything ``order.submit`` needs after validation, plus the §10.3 preview it was computed from."""
    account_id: str
    acct: dict
    lifetime_id: str
    account_kind: str
    inst: Instrument
    side: str
    qty: int
    order_type: str
    limit_px: Optional[Decimal]
    stop_px: Optional[Decimal]
    trail_amt: Optional[Decimal]
    trail_pct: Optional[Decimal]
    trail_ref_px: Optional[Decimal]
    tif: str
    session: Any
    rec: Optional[QuoteRecord]
    executable: bool
    exec_reason: str
    expected_price: Optional[Decimal]
    price_basis: str
    participation: Decimal
    expires_ts: int
    rests_until: Optional[int]
    settle_date: date
    gross_c: Optional[int]
    fee_c: int
    fee_basis: str
    need_c: int
    funding: dict
    locate: Any
    warnings: List[str]
    preview: dict
    hedge: Optional[dict] = None
    package: Optional[dict] = None
    confirm_large: bool = False
    preview_id: Optional[str] = None
    fill_mode: str = "SEQUENTIAL"


def validate(services, req: dict, *, snapshot=None, conn=None, now: Optional[int] = None, rest_ok: bool = False,
             partial: Optional[dict] = None) -> ValidatedOrder:
    """§10.2: every rejection is a ``RejectError`` (400) or ``ConflictError`` (409) with the code of the table; a closed
    session is never a rejection.  ``rest_ok`` lifts the QUOTE_STALE rule (package legs rest until a fresh observation).
    ``partial`` (a dict) receives the preview built so far, so a blocked preview still shows the instrument, quote,
    session and funding figures."""
    if conn is None:
        with services.db.reader() as c:
            return _validate(services, req, snapshot, c, now, rest_ok, partial)
    return _validate(services, req, snapshot, conn, now, rest_ok, partial)


def _reserved_shares(services, account_id: str, instrument_id: str) -> int:
    """Shares reserved by covered calls (``options.reserved_shares``, WP-03 SHARES_RESERVED); 0 when the options module is
    absent, exposes no such function or the lookup fails (a reservation never blocks a preview by error: the margin job
    re-prices a call left naked)."""
    fn = getattr(getattr(services, "options", None), "reserved_shares", None)
    if not callable(fn):
        return 0
    try:
        got = fn(services, account_id, instrument_id)
        return int(got) if isinstance(got, int) else int(str(got)) if str(got).isdigit() else 0
    except Exception:   # noqa: BLE001
        return 0


def _validate(services, req: dict, snapshot, conn, now: Optional[int], rest_ok: bool, partial: Optional[dict]) -> ValidatedOrder:
    clock = services.clock
    now = int(now if now is not None else clock.epoch())
    pv: dict = partial if partial is not None else {}
    pv.update({"as_of": now, "ok": True, "blockers": [], "warnings": [], "conventions": ["FILL_PRICE", "PARTICIPATION", "SESSION", "COST_BASIS"]})
    warnings: List[str] = pv["warnings"]
    conv: List[str] = pv["conventions"]
    # account, lifetime, hold
    account_id = str(req.get("account_id") or "")
    if not account_id:
        raise RejectError("ACCOUNT_UNKNOWN", "account_id is required")
    acct = account_row(conn, account_id)
    if acct is None:
        raise RejectError("ACCOUNT_UNKNOWN", f"account {account_id} does not exist")
    acct = dict(acct)
    pv["account_id"] = account_id
    lid = acct["lifetime_id"]
    lrow = lifetime_row(conn, lid)
    if lrow is None or lrow["status"] != "OPEN":
        raise RejectError("LIFETIME_CLOSED", f"lifetime {lid} is closed (History is read-only)", {"lifetime_id": lid})
    if acct["status"] != "OPEN":
        raise RejectError("ACCOUNT_CLOSED", f"account {account_id} is closed")
    acc = services.accounting
    if hasattr(acc, "is_held") and acc.is_held(lid):
        raise ConflictError("INTEGRITY_HOLD", f"lifetime {lid} is on an integrity hold: the whole-ledger check failed and is unresolved (see Requires your attention)",
                            {"lifetime_id": lid})
    akind = account_kind_for(acct)
    # instrument
    iid = str(req.get("instrument_id") or "")
    inst = services.instruments.get(iid, conn) if iid else None
    if inst is None:
        raise RejectError("INSTRUMENT_UNKNOWN", f"instrument {iid!r} is not in the master (resolve it first)")
    pv["instrument"] = {"id": inst.instrument_id, "symbol": inst.symbol, "name": inst.name, "kind": inst.kind, "currency": inst.currency,
                        "multiplier": str(inst.multiplier), "support": inst.support, "lot": inst.lot, "tick": str(inst.tick), "settle_days": inst.settle_days,
                        "session_group": inst.session_group, "expiry": inst.expiry}
    if akind == "TREASURY":
        raise RejectError("NOT_TRADEABLE_HERE", "Treasury never trades: it only funds the accounts (and converts FX)")
    if not services.instruments.tradeable_in(inst, akind):
        raise RejectError("NOT_TRADEABLE_HERE", f"{inst.symbol} ({inst.kind}) does not trade in a {akind} account", {"account_kinds": list(inst.account_kinds)})
    if inst.support in ("UNSUPPORTED", "COMPUTED"):
        raise RejectError("UNSUPPORTED", f"{inst.symbol} is {inst.support.lower()} in this build: model marks never execute" if inst.support == "COMPUTED"
                          else f"{inst.symbol} is unsupported in this build")
    if inst.support == "LIMITED":
        warnings.append(f"{inst.symbol} is LIMITED in this build: {inst.spec.get('note') or 'check the instrument page before trading'}")
    if inst.status != "ACTIVE":
        raise RejectError("INSTRUMENT_EXPIRED", f"{inst.symbol} is {inst.status}")
    if inst.kind == "SWAP":
        from . import books as _books
        _books.require_pb_otc(services, account_id)
        raise RejectError("NOT_TRADEABLE_HERE", "swaps are booked through the OTC desk (otc.book), not the order ticket")
    if inst.kind == "PRIVATE":
        from . import books as _books
        _books.require_private(services, lid)
        if req.get("price") in (None, ""):
            raise RejectError("PRICE_REQUIRED", "a private secondary needs the negotiated price (sentence PRIVATE_TRADE)")
    ccy = inst.currency
    row = conn.execute("SELECT id FROM attention WHERE code='FUNDING_SHORTFALL' AND account_id=? AND resolved_ts IS NULL AND details_json LIKE ?",
                       (account_id, f'%"currency":"{ccy}"%')).fetchone()
    if row is not None:
        raise ConflictError("FUNDING_SHORTFALL", f"{account_id} has an open funding shortfall in {ccy} (attention #{row['id']}): fund the account before placing new orders",
                            {"attention_id": int(row["id"]), "currency": ccy})
    ny_now = ny_of(now)
    today = ny_now.date()
    if inst.expiry:
        exp = date.fromisoformat(inst.expiry)
        if exp < today:
            raise RejectError("INSTRUMENT_EXPIRED", f"{inst.symbol} expired on {inst.expiry}")
    # side, qty, type, prices
    side = str(req.get("side") or "").upper()
    allowed = OPTION_SIDES if inst.kind == "OPTION" else (FUTURES_SIDES if inst.kind == "FUTURE" else CASH_SIDES)
    if side not in allowed:
        raise RejectError("SIDE_INVALID", f"side {side!r} is not valid for {inst.kind}: use one of {', '.join(allowed)}")
    if inst.kind == "OPTION" and inst.expiry == today.isoformat() and side in OPENING_SIDES and ny_now.strftime("%H:%M") >= OPTION_LAST_OPEN_ET:
        raise RejectError("LAST_SESSION", f"{inst.symbol} expires today: no new opening trades after {OPTION_LAST_OPEN_ET} ET on the expiry day")
    try:
        qty = int(req.get("qty"))
    except (TypeError, ValueError) as e:
        raise RejectError("QTY_INVALID", f"qty must be a whole number, got {req.get('qty')!r}") from e
    lot = max(1, int(inst.lot))
    if qty <= 0 or qty % lot:
        raise RejectError("QTY_INVALID", f"qty must be a positive multiple of {lot:,}", {"lot": lot})
    order_type = str(req.get("order_type") or "MARKET").upper()
    if order_type not in ORDER_TYPES:
        raise RejectError("ORDER_TYPE_INVALID", f"order_type {order_type!r}: use one of {', '.join(ORDER_TYPES)}")
    tif = str(req.get("tif") or "DAY").upper()
    if tif not in TIFS:
        raise RejectError("TIF_INVALID", f"tif {tif!r}: use DAY or GTC")
    limit_px = stop_px = trail_amt = trail_pct = None
    if order_type in ("LIMIT", "STOP_LIMIT"):
        if req.get("limit_px") in (None, ""):
            raise RejectError("PRICE_REQUIRED", f"{order_type} needs limit_px")
        limit_px = _dec(req["limit_px"], "PRICE_TICK", "limit_px")
        if limit_px <= 0 or not on_tick(limit_px, inst.tick):
            raise RejectError("PRICE_TICK", f"limit_px {limit_px} is not on the {inst.tick} tick grid")
        limit_px = qprice(limit_px)
    if order_type in ("STOP", "STOP_LIMIT"):
        if req.get("stop_px") in (None, ""):
            raise RejectError("PRICE_REQUIRED", f"{order_type} needs stop_px")
        stop_px = _dec(req["stop_px"], "PRICE_TICK", "stop_px")
        if stop_px <= 0 or not on_tick(stop_px, inst.tick):
            raise RejectError("PRICE_TICK", f"stop_px {stop_px} is not on the {inst.tick} tick grid")
        stop_px = qprice(stop_px)
    if order_type == "TRAILING_STOP":
        has_amt, has_pct = req.get("trail_amt") not in (None, ""), req.get("trail_pct") not in (None, "")
        if has_amt == has_pct:
            raise RejectError("TRAIL_INVALID", "a trailing stop takes exactly one of trail_amt (price units) or trail_pct (percent)")
        if has_amt:
            trail_amt = _dec(req["trail_amt"], "TRAIL_INVALID", "trail_amt")
            if trail_amt <= 0:
                raise RejectError("TRAIL_INVALID", "trail_amt must be positive")
            trail_amt = qprice(trail_amt)
        else:
            trail_pct = _dec(req["trail_pct"], "TRAIL_INVALID", "trail_pct").quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
            if trail_pct <= 0 or trail_pct >= 100:
                raise RejectError("TRAIL_INVALID", "trail_pct must be above 0 and below 100")
        conv.append("TRAILING")
    if order_type in ("LIMIT", "STOP_LIMIT"):
        conv.append("LIMIT_RULE")
    if order_type in ("STOP", "STOP_LIMIT"):
        conv.append("STOP_RULE")
    pv.update({"side": side, "qty": qty, "order_type": order_type, "tif": tif, "limit_px": _s(limit_px), "stop_px": _s(stop_px),
               "trail": None if order_type != "TRAILING_STOP" else {"trail_amt": _s(trail_amt), "trail_pct": _s(trail_pct)}})
    # session and quote
    sessions = services.sessions
    session = sessions.state(inst.session_group, now)
    rec = with_nbbo(services, inst, quote_of(services, iid, snapshot), session, now)
    ok, reason = is_executable(rec, session, now)
    quality = freshness(rec, session, now)
    pv["quote"] = None if rec is None else {
        "last": _s(rec.last), "bid": _s(rec.bid), "ask": _s(rec.ask), "quality": quality, "source": rec.source, "quote_time": rec.quote_time,
        "age_s": None if rec.quote_time is None else max(0, now - int(rec.quote_time)), "delay_label": rec.delay_label, "method": rec.method,
        "executable": ok, "reason": reason}
    if not ok:
        conv.append("STALE") if reason in ("STALE", "NO_QUOTE") else None
    last = qprice(rec.last) if rec is not None and rec.last is not None else None
    mark = _mark_price(services, snapshot, iid)
    ref_last = last if last is not None else mark
    probe = type("_P", (), {})()
    probe.side, probe.kind, probe.instrument_id = side, inst.kind, iid
    px_exec, basis = exec_price(probe, rec) if ok else (None, reason)
    if order_type in ("STOP", "STOP_LIMIT") and ref_last is not None:
        if (side in BUY_SIDES and stop_px <= ref_last) or (side in SELL_SIDES and stop_px >= ref_last):
            raise RejectError("STOP_SIDE", f"a {'buy' if side in BUY_SIDES else 'sell'} stop at {stop_px} would trigger at once against the last {ref_last}: use a limit",
                              {"last": str(ref_last), "stop_px": str(stop_px)})
    trail_ref_px = None
    if order_type == "TRAILING_STOP":
        if ref_last is None:
            raise ConflictError("QUOTE_STALE", "a trailing stop needs an observed last price to set its reference; none is available", {"reason": reason})
        trail_ref_px = ref_last
        tmp = OrderRow("", account_id, iid, side, qty, 0, order_type, None, None, trail_amt, trail_pct, None, "SEQUENTIAL", tif, "WORKING", False, False,
                       None, None, inst.session_group, 0, config.PARTICIPATION_RATE, kind=inst.kind, lot=lot, tick=inst.tick)
        stop_px = trail_stop(tmp, trail_ref_px)
        pv["trail"].update({"trail_ref_px": str(trail_ref_px), "stop_px": str(stop_px)})
    if order_type in NEEDS_QUOTE_TYPES and session.open and not ok and not rest_ok:
        raise ConflictError("QUOTE_STALE", f"{inst.symbol}: the quote is not executable ({reason}); a {order_type} order needs a fresh print while the session is open",
                            {"reason": reason, "quality": quality, "quote_time": None if rec is None else rec.quote_time})
    # positions (OVERSELL / CLOSE_ONLY_MISMATCH)
    pos_qty, _cost = _position(conn, account_id, iid)
    working_sells = working_covers = 0
    for r in conn.execute("SELECT side, qty, filled_qty FROM orders WHERE account_id=? AND instrument_id=? AND status IN ('WORKING','PARTIAL','SUSPENDED','WAITING_PARENT')",
                          (account_id, iid)).fetchall():
        rem = int(r["qty"]) - int(r["filled_qty"])
        if r["side"] in ("SELL", "STC"):
            working_sells += rem
        elif r["side"] in ("BUY_TO_COVER", "BTC"):
            working_covers += rem
    if inst.kind == "FUTURE":
        pass        # no borrow in futures: SELL from flat opens a short, SELL beyond the long flips (futures.apply_fill books the signed quantity)
    elif side in ("SELL", "STC"):
        reserved = _reserved_shares(services, account_id, iid) if side == "SELL" else 0
        free = pos_qty - working_sells - reserved
        if qty > free:
            code = "OVERSELL" if side == "SELL" else "CLOSE_ONLY_MISMATCH"
            hint = f": sell {max(free, 0):,} and SELL_SHORT {qty - max(free, 0):,} with a borrow" if side == "SELL" else ""
            if reserved:
                hint += f"; {reserved:,} shares are reserved by covered calls (SHARES_RESERVED)"
            raise RejectError(code, f"{side} {qty:,} {inst.symbol} but the long position net of working sells{' and reserved shares' if reserved else ''} is {max(free, 0):,}{hint}",
                              {"position": pos_qty, "working_sells": working_sells, "reserved_shares": reserved, "qty": qty})
    elif side in ("BUY_TO_COVER", "BTC"):
        free = -pos_qty - working_covers
        if qty > free:
            raise RejectError("CLOSE_ONLY_MISMATCH", f"{side} {qty:,} {inst.symbol} but the short position net of working covers is {max(free, 0):,}",
                              {"position": pos_qty, "working_covers": working_covers, "qty": qty})
    # borrow (SELL_SHORT)
    locate = None
    if side == "SELL_SHORT":
        lending = getattr(services, "lending", None)
        if lending is None or not hasattr(lending, "locate"):
            raise RejectError("NOT_IN_THIS_BUILD", "short sales need the securities-lending package (WP-06): no locate is possible yet")
        locate = lending.locate(services, account_id, inst, qty)
        if not locate.available or int(locate.qty_available) <= 0:
            raise ConflictError("NO_BORROW", f"no borrow available for {inst.symbol}: {getattr(locate, 'basis', '')}", {"locate": _as_dict(locate)})
        if int(locate.qty_available) < qty:
            warnings.append(f"borrow availability {int(locate.qty_available):,} < {qty:,}: the order is scaled down to {int(locate.qty_available):,}")
            qty = int(locate.qty_available)
            pv["qty"] = qty
        conv.append("SHORT_COLLATERAL")
        pv["borrow"] = dict(_as_dict(locate), needed=True)
    else:
        pv["borrow"] = None
    # economics
    if ok and px_exec is not None:
        expected, price_basis = px_exec, basis
    elif order_type in ("LIMIT", "STOP_LIMIT"):
        expected, price_basis = limit_px, "limit"
    elif order_type in ("STOP", "TRAILING_STOP"):
        expected, price_basis = stop_px, "stop"
    else:
        expected, price_basis = ref_last, "last_mark" if ref_last is not None else "unavailable"
    gross_c = None if expected is None else inst.contract_value_c(expected, qty)
    fee_c, fee_basis = execution.fee_for(services, acct, inst, qty, gross_c)
    tdate = _trade_date(ny_now, inst.session_group, services.cal)
    settle_date = services.cal.settlement_date(tdate, inst.settle_days)
    buy = side in BUY_SIDES
    net_cash = None if gross_c is None else (-(gross_c + fee_c) if buy else gross_c - fee_c)
    pv["economics"] = {"gross": _m(gross_c), "fee": _m(fee_c), "fee_basis": fee_basis, "net_cash": _m(net_cash), "currency": ccy,
                       "settle_date": settle_date.isoformat(), "accrued": None}
    if inst.kind == "FUTURE":
        im = int((inst.spec or {}).get("im_c") or 0)
        mm = int((inst.spec or {}).get("mm_c") or 0)
        need_c, need_formula = im * qty + fee_c, "initial margin x contracts + fee (FUT_MARGIN)"
        pv["margin"] = {"initial": _m(im * qty), "maintenance": _m(mm * qty), "per_contract": _m(im), "note": tables.CONVENTIONS.get("FUT_MARGIN", "")}
        conv.append("FUT_MARGIN")
    elif buy:
        need_c, need_formula = (gross_c or 0) + fee_c, "gross at the expected price + fee"
        pv["margin"] = None if inst.kind != "OPTION" else {"requirement": None, "formula": tables.CONVENTIONS.get("OPT_MARGIN", ""), "detail": "recomputed after the fill"}
    elif side == "SELL_SHORT":
        need_c, need_formula = to_c(from_c(gross_c or 0) * config.COLLATERAL_PCT), "102 % cash collateral posted at the borrow (sale proceeds settle T+1)"
        pv["margin"] = None
    else:
        need_c, need_formula = 0, "a sale: proceeds settle at T+1" + ("; short-option margin is recomputed after the fill" if inst.kind == "OPTION" else "")
        pv["margin"] = None if inst.kind != "OPTION" else {"requirement": None, "formula": tables.CONVENTIONS.get("OPT_MARGIN", ""), "detail": "recomputed after the fill"}
        if inst.kind == "OPTION":
            conv.append("OPT_MARGIN")
    if inst.kind == "OPTION" and buy:
        conv.append("OPT_MARGIN")
    # funding (§6.1, §10.8)
    bp = acc.buying_power(account_id, ccy, settle_date, snapshot=snapshot)
    cash = acc.cash(account_id, snapshot=snapshot).get(ccy)
    own = int(bp.available_c)
    tre = int(bp.treasury_available_c)
    pbh = int(bp.pb_headroom_c)
    shortfall = max(0, need_c - own)
    sweep_in = min(shortfall, max(0, tre))
    pb_fin = min(shortfall - sweep_in, max(0, pbh)) if (acct.get("kind") == "PB" and int(acct.get("pb_financing") or 0)) else 0
    fundable = (shortfall - sweep_in - pb_fin) <= 0
    threshold = int(get_setting(conn, "sweep_confirm_threshold_c", config.SWEEP_CONFIRM_THRESHOLD_C) or 0)
    nav_c = int(acc.nav(f"{lid}:ALL", snapshot=snapshot).nav_c)
    notional = gross_c or 0
    big_sweep = sweep_in > threshold
    big_notional = nav_c > 0 and notional * 4 > nav_c
    confirm_required = big_sweep or big_notional
    confirm_reason = ("sweep-in above the confirm threshold" if big_sweep else "") + (" and " if big_sweep and big_notional else "") + \
                     ("notional above 25 % of the lifetime's NAV" if big_notional else "")
    fx_leg = None
    if ccy != "USD" and shortfall > 0:
        fx_leg = {"buy_ccy": ccy, "sell_ccy": "USD", "amount": _m(shortfall), "rate": _s(_mark_price(services, snapshot, f"{ccy}USD")), "settle_date": settle_date.isoformat(),
                  "note": "a foreign need is met from the account's then Treasury's cash in that currency; submit the FX leg first (never automatic FX)"}
    if shortfall > 0:
        conv.append("SWEEP")
    pv["funding"] = {"currency": ccy, "settled": _m(cash.settled_c if cash else 0), "pending": _m(cash.pending_c if cash else 0), "reserved": _m(cash.reserved_c if cash else 0),
                     "available": _m(own), "need": _m(need_c), "need_formula": need_formula, "shortfall": _m(shortfall), "sweep_in": _m(sweep_in),
                     "sweep_source": "TREASURY" if sweep_in > 0 else ("PB" if pb_fin > 0 else "NONE"), "treasury_available": _m(tre), "pb_financing": _m(pb_fin),
                     "fx_leg": fx_leg, "ok": fundable, "confirm_required": confirm_required, "confirm_reason": confirm_reason or None, "threshold": _m(threshold),
                     "nav": _m(nav_c), "formula": "own settled + receivables due by settlement - payables - reserved"}
    if not fundable:
        code = "INSUFFICIENT_FUNDS_CCY" if ccy != "USD" else "INSUFFICIENT_FUNDS"
        raise ConflictError(code, f"{account_id} needs {ccy} {fmt_money(need_c)}: own available {fmt_money(own)}, Treasury available {fmt_money(tre)},"
                            f" PB financing {fmt_money(pbh)}", {"need_c": need_c, "own_available_c": own, "treasury_available_c": tre, "pb_headroom_c": pbh,
                                                                 "currency": ccy, "fx_leg": fx_leg})
    # execution block
    try:
        expires_ts = sessions.day_order_expiry(inst.session_group, now) if tif == "DAY" else now + config.GTC_DAYS * 86400
    except ValueError as e:
        raise RejectError("SESSION", f"no session for {inst.session_group} within 16 days") from e
    if inst.expiry:
        exp = date.fromisoformat(inst.expiry)
        close = sessions.session_close(inst.session_group, exp)
        if close is not None:
            expires_ts = min(expires_ts, close)
    tmp = OrderRow("", account_id, iid, side, qty, 0, order_type, limit_px, stop_px, trail_amt, trail_pct, trail_ref_px, "SEQUENTIAL", tif, "WORKING", False, False,
                   None, None, inst.session_group, expires_ts, config.PARTICIPATION_RATE, kind=inst.kind, lot=lot, tick=inst.tick, multiplier=inst.multiplier,
                   price_scale=inst.price_scale)
    participation = Decimal(str(get_setting(conn, "participation_rate", str(config.PARTICIPATION_RATE)) or config.PARTICIPATION_RATE))
    tmp.participation = participation
    dec = evaluate(tmp, rec, session, now, participation, inst)
    cap, cap_note = fill_cap(tmp, rec, inst, participation) if ok else (0, "not executable now")
    if rec is not None and (rec.bar is None or rec.bar.v is None) and inst.kind not in ("OPTION", "FX"):
        conv.append("NO_VOLUME")
    if inst.kind == "OPTION" and rec is not None and rec.method != "chain":
        assumption = "options execute only at the Cboe delayed NBBO; this record is not a chain quote"
    elif inst.kind == "OPTION":
        assumption = "fills at the Cboe delayed ask (buys) / bid (sells); capped by the displayed size"
    elif inst.kind == "FX":
        assumption = "deals at the Yahoo mid plus or minus 0.2 pip against you (FX_SPOT)"
    elif basis in ("ask", "bid"):
        assumption = "fills at the Cboe underlying ask (buys) / bid (sells) from the cached chain"
    else:
        assumption = "fills at the last observed trade price; no bid/ask is available from the free feed"
    rests_until = None if session.open else session.next_open
    pv["execution"] = {"price_basis": price_basis, "expected_price": _s(expected), "fill_now": dec.action == "FILL", "fill_qty_now": dec.qty if dec.action == "FILL" else 0,
                       "decision": dec.reason, "cap_note": cap_note, "session": {"group": session.group, "open": session.open, "phase": session.phase,
                                                                                 "closes_at": session.closes_at, "next_open": session.next_open, "note": session.note},
                       "rests_until": rests_until, "expires_at": expires_ts, "assumption": assumption, "participation": str(participation)}
    if not session.open:
        warnings.append(f"the {inst.session_group} session is {session.phase.lower()}: the order rests until it opens" + (f" ({ny_of(session.next_open).strftime('%a %H:%M ET')})" if session.next_open else ""))
    # hedge candidates, compliance, risk (other packages; absent-safe)
    pv["hedges"] = _hedges_block(services, account_id, inst, side, qty)
    pv["compliance"] = _notices(services, "compliance", account_id, inst, side, qty, pv, warnings)
    pv["risk"] = _notices(services, "risk", account_id, inst, side, qty, pv, warnings)
    seen = set()
    pv["conventions"] = [c for c in conv if not (c in seen or seen.add(c))]
    hedge = req.get("hedge") if isinstance(req.get("hedge"), dict) else None
    package = req.get("package") if isinstance(req.get("package"), dict) else None
    return ValidatedOrder(account_id, acct, lid, akind, inst, side, qty, order_type, limit_px, stop_px, trail_amt, trail_pct, trail_ref_px, tif, session, rec, ok, reason,
                          expected, price_basis, participation, expires_ts, rests_until, settle_date, gross_c, fee_c, fee_basis, need_c, pv["funding"], locate, warnings, pv,
                          hedge, package, bool(req.get("confirm_large")), req.get("preview_id"))


def _as_dict(obj) -> dict:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return dict(obj)
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in getattr(obj, "__dict__", {}).items()}


def _hedges_block(services, account_id, inst, side, qty) -> dict:
    hedges = getattr(services, "hedges", None)
    if hedges is None or not hasattr(hedges, "candidates"):
        return {"candidates": [], "status": "unavailable", "note": "hedge candidates: not yet built (WP-03)"}
    try:
        cands = hedges.candidates(services, account_id, inst, side, qty, None)
    except Exception as e:   # noqa: BLE001 - the panel reports, never blocks the ticket
        return {"candidates": [], "status": "error", "note": f"{type(e).__name__}: {e}"}
    if isinstance(cands, dict) and cands.get("status"):
        return cands
    return {"candidates": [_as_dict(c) for c in (cands or [])], "status": "ready"}


def _notices(services, module: str, account_id, inst, side, qty, pv, warnings) -> list:
    mod = getattr(services, module, None)
    if mod is None or not hasattr(mod, "pretrade"):
        return []
    try:
        return [_as_dict(n) for n in (mod.pretrade(services, account_id, inst, side, qty, pv) or [])]
    except Exception as e:   # noqa: BLE001 - notices inform only; a failure is disclosed, never a block
        warnings.append(f"{module} notices unavailable: {type(e).__name__}: {e}")
        return []


def preview(services, req: dict, *, snapshot=None, now: Optional[int] = None) -> dict:
    """§10.3: pure (no writes); validation failures become ``blockers`` (code, message, details, http), the rest of the
    preview still carries what could be computed; the result is stored in the ``PreviewStore`` under ``preview_id``."""
    pv: dict = {}
    try:
        vo = validate(services, req, snapshot=snapshot, now=now, partial=pv)
        pv = vo.preview
    except RejectError as e:
        pv.setdefault("as_of", int(now if now is not None else services.clock.epoch()))
        pv["ok"] = False
        pv["blockers"] = [{"code": e.code, "message": e.message, "details": _jsonable(e.details), "http": e.http}]
        for k in ("instrument", "quote", "execution", "economics", "funding", "margin", "borrow", "hedges", "compliance", "risk"):
            pv.setdefault(k, None if k not in ("compliance", "risk") else [])
        pv.setdefault("warnings", [])
        pv.setdefault("conventions", ["FILL_PRICE", "PARTICIPATION", "SESSION", "COST_BASIS"])
        for k in ("account_id", "side", "qty", "order_type", "tif"):
            pv.setdefault(k, req.get(k))
    preview_store(services).put(pv)
    return pv


def _jsonable(x):
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, Decimal):
        return str(x)
    if hasattr(x, "to_dict"):
        return x.to_dict()
    return x
