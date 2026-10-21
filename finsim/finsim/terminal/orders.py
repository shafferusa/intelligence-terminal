"""Orders: validation, preview, the pure matching rules, package (all-or-none) matching, handlers and the Matcher
(TERMINAL_DESIGN.md §2.12 orders.py, §10.1-10.9, §19.9, §19.22; TERMINAL_EXECUTION.md).

Conventions stated here (the §1.3 sentences that bite in this module)
- FILL_PRICE: fills happen at the last observed trade (Yahoo, up to 15 min delayed) or, for options, at the Cboe
  delayed bid (sells) / ask (buys); nothing here invents a price.  ``evaluate()`` is pure: it reads an order row,
  one observed ``QuoteRecord``, one ``SessionState`` and the clock and returns a ``Decision``; it never touches the
  database, the network or the cache.
- LIMIT_RULE: a marketable limit fills at the observed price on its first look; afterwards a crossed limit fills at
  the limit price.  ``first_eval_done`` is persisted after the order's first EXECUTABLE look (a stale or closed look
  does not count).
- STOP_RULE: stops trigger on the observed last trade and fill at that print (basis ``stop_print``); a gap through
  the stop fills at the gap price, disclosed in the decision.
- TRAILING: a trailing stop ratchets with each observed print in your favour (sell: highest last minus the trail;
  buy: lowest last plus the trail) and never moves back; the ratchet is persisted through ``order.match`` with action
  RATCHET (one order_events row per change) so a restart keeps it; it triggers on the observed last and fills at
  that print.  ``trail_pct`` re-derives the trail from the ratcheted reference; the stop is quantised to the tick.
- PARTICIPATION / NO_VOLUME: each new completed 1-minute bar lets at most ``participation`` (default 10 %) of its
  volume fill; the same bar never fills twice (``last_bar_ts``); with no volume information the order fills in
  full; options are capped by the displayed size, FX has no volume concept.
- SESSION: orders entered outside the session rest until it opens (never a rejection); DAY orders expire at that
  session's close; GTC after 90 days (or at the instrument's expiry).  A print from a previous session never fills
  (``is_executable`` guards QUOTE_FROM_PREVIOUS_SESSION / PROVIDER_SESSION_ENDED).
- STALE: a quote older than its delay plus 5 minutes blocks executions; a ``computed`` or ``private`` mark never
  executes; a MARKET / STOP / STOP_LIMIT / TRAILING_STOP submitted while the session is open against a
  non-executable quote is refused 409 QUOTE_STALE (a LIMIT may rest).
- AON: an option-only package fills all-or-none: ``evaluate_package()`` needs every leg executable from the same
  chain observation (one ``quote_time``) with ``bid > 0 and ask >= bid`` and displayed size >= the leg's remaining
  quantity, else NONE (AON_SIZE / AON_STALE / AON_OBSERVATION) and nothing fills; the fill is ONE command
  ``order.match_package`` (idem ``matchpkg:{package_id}:{quote_time}``) with ONE option-margin recomputation.
  Packages with a stock or futures leg are SEQUENTIAL: the hedge legs wait for the primary (WAITING_PARENT) and are
  resized to floor(primary filled x ratio) on each fill.
- SWEEP / AVAILABLE: the preview's need is the worst-case cash the fill takes (BUY: gross at the expected price +
  fee; futures: IM + fee; SELL_SHORT: the 102 % collateral); the shortfall against the account's available cash is
  the sweep-in the fill will make; above ``settings.sweep_confirm_threshold_c`` or 25 % of the lifetime's NAV it needs
  ``confirm_large`` (409 CONFIRM_REQUIRED); unfundable from own + Treasury + PB headroom -> 409 INSUFFICIENT_FUNDS.
- Idempotency: the matcher's command id is ``match:<order_id>:<quote_time>:<bar_ts|none>`` and the fill's idem_key
  the same string, so a second evaluation of the same observation is a replay (no second fill); ``order.match``
  re-reads status / filled_qty / expires_ts inside the transaction and re-checks ``is_executable`` with the
  payload's quote and the command clock (a cancel that committed in between wins).
- Reservation at fill time: before a fill is applied the order's ``filled_qty`` is advanced by the fill quantity
  (inside a SAVEPOINT) so that ``accounting.reserved_c`` no longer reserves the slice being paid for and the sweep-in
  is exactly the shortfall; a funding refusal rolls the savepoint back and SUSPENDS a resting order (new orders at
  submit are refused instead).
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_EVEN
from typing import Dict, Iterable, List, Optional

from . import config, execution
from .commands import Command, ConflictError, RejectError, TxContext
from .db import dumps, next_id
from .clock import ny_of
from .instruments import Instrument
from .matcher import Matcher                                                                        # noqa: F401 (re-export)
from .orders_preview import (PreviewStore, ValidatedOrder, _as_dict, _jsonable, _s, preview, preview_store, quote_of, validate,   # noqa: F401
                             with_nbbo)
from .orders_rules import (BUY_SIDES, CASH_SIDES, FUTURES_SIDES, LIVE_STATUSES, NEEDS_QUOTE_TYPES, OPEN_STATUSES, OPENING_SIDES, OPTION_SIDES,   # noqa: F401
                           ORDER_TYPES, SELL_SIDES, STOP_TYPES, TIFS, Decision, LegDecision, OrderRow, PackageDecision, evaluate, evaluate_package,
                           exec_price, fill_cap, match_idem, on_tick, trail_amount, trail_stop)
from .quotes import QuoteRecord
from .units import qprice, to_c


class NotBuilt(RejectError):
    """A package request that needs a module this build does not have yet -> HTTP 501."""
    http = 501


# ---- rows, events, views -----------------------------------------------------------------------------------------------------
def _services(ctx: TxContext):
    if ctx.services is None:
        raise RejectError("NO_SERVICES", "the command context carries no services bundle")
    return ctx.services


def _order_raw(conn, order_id: str):
    return conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()


def load_order(services, conn, order_id: str) -> Optional[OrderRow]:
    r = _order_raw(conn, order_id)
    if r is None:
        return None
    return OrderRow.from_row(r, services.instruments.get(r["instrument_id"], conn))


def _order_event(conn, order_id: str, ts: int, from_status: Optional[str], to_status: str, note: str, rec: Optional[QuoteRecord] = None) -> None:
    conn.execute("INSERT INTO order_events(order_id, ts, from_status, to_status, note, quote_json) VALUES (?,?,?,?,?,?)",
                 (order_id, ts, from_status, to_status, note, None if rec is None else dumps(rec.to_dict())))


def order_view(conn, order_id: str) -> dict:
    """The order row (without the stored preview), its fills and its last event; the API's OrderView."""
    r = _order_raw(conn, order_id)
    if r is None:
        raise RejectError("NOT_FOUND", f"order {order_id} does not exist")
    d = {k: r[k] for k in r.keys() if k != "preview_json"}
    d["remaining"] = int(r["qty"]) - int(r["filled_qty"])
    d["fills"] = [dict(f) for f in conn.execute("SELECT fill_id, side, qty, price, gross_c, fee_c, price_basis, ts, trade_date, settle_date, bar_ts, cap_qty, cap_note"
                                                " FROM fills WHERE order_id=? ORDER BY ts, fill_id", (order_id,)).fetchall()]
    ev = conn.execute("SELECT ts, from_status, to_status, note FROM order_events WHERE order_id=? ORDER BY id DESC LIMIT 1", (order_id,)).fetchone()
    d["last_event"] = dict(ev) if ev else None
    return d


def _sweeps_for(conn, fill_ids: Iterable[str]) -> List[dict]:
    out = []
    for fid in fill_ids:
        for t in conn.execute("SELECT transfer_id, from_account, to_account, currency, amount_c, linked_type, linked_id, shortfall_c FROM transfers"
                              " WHERE kind='SWEEP_IN' AND linked_type='fill' AND linked_id=?", (fid,)).fetchall():
            out.append(dict(t))
    return out


def _insert_order(ctx: TxContext, vo: ValidatedOrder, request_id: str, status: str = "WORKING", fill_mode: str = "SEQUENTIAL", package_id: Optional[str] = None,
                  parent_order_id: Optional[str] = None, ratio: Optional[Decimal] = None) -> str:
    oid = next_id(ctx.conn, "order")
    ctx.conn.execute(
        "INSERT INTO orders(order_id, request_id, account_id, instrument_id, side, qty, filled_qty, avg_px, order_type, limit_px, stop_px, trail_amt, trail_pct, trail_ref_px,"
        " fill_mode, tif, status, triggered, first_eval_done, last_bar_ts, last_quote_time, session_group, created_ts, updated_ts, expires_ts, participation, borrow_id,"
        " package_id, parent_order_id, ratio, fees_c, reason, preview_json) VALUES (?,?,?,?,?,?,0,NULL,?,?,?,?,?,?,?,?,?,0,0,NULL,NULL,?,?,?,?,?,NULL,?,?,?,0,NULL,?)",
        (oid, request_id, vo.account_id, vo.inst.instrument_id, vo.side, vo.qty, vo.order_type, _s(vo.limit_px), _s(vo.stop_px), _s(vo.trail_amt), _s(vo.trail_pct),
         _s(vo.trail_ref_px), fill_mode, vo.tif, status, vo.inst.session_group, ctx.ts, ctx.ts, vo.expires_ts, str(vo.participation), package_id, parent_order_id,
         None if ratio is None else str(ratio), dumps(vo.preview)))
    return oid


def _summary(vo: ValidatedOrder) -> str:
    px = f" @ {vo.limit_px}" if vo.limit_px is not None else ""
    st = f" stop {vo.stop_px}" if vo.stop_px is not None else ""
    return f"{vo.side} {vo.qty:,} {vo.inst.symbol} {vo.order_type}{px}{st} {vo.tif}"


# ---- the shared fill path (§10.7) --------------------------------------------------------------------------------------------

def _act(ctx: TxContext, services, orow: OrderRow, rec: Optional[QuoteRecord], dec: Decision, new_order: bool):
    """Persist a Decision: RATCHET / TRIGGER / first look update the row; FILL applies the fill.  Returns the FillResult or None."""
    conn = ctx.conn
    oid = orow.order_id
    if dec.action == "RATCHET":
        conn.execute("UPDATE orders SET stop_px=?, trail_ref_px=?, first_eval_done=1, last_quote_time=?, updated_ts=? WHERE order_id=?",
                     (_s(dec.stop_px), _s(dec.trail_ref_px), dec.quote_time, ctx.ts, oid))
        _order_event(conn, oid, ctx.ts, orow.status, orow.status, f"RATCHET: {dec.note}", rec)
        ctx.event("ORDER_RATCHETED", account_id=orow.account_id, instrument_id=orow.instrument_id, ref_type="order", ref_id=oid, stop_px=_s(dec.stop_px),
                  trail_ref_px=_s(dec.trail_ref_px), last=_s(rec.last if rec else None))
        return None
    if dec.action == "TRIGGER" or (dec.action == "FILL" and dec.triggered):
        conn.execute("UPDATE orders SET triggered=1, first_eval_done=1, last_quote_time=?, updated_ts=? WHERE order_id=?", (dec.quote_time, ctx.ts, oid))
        _order_event(conn, oid, ctx.ts, orow.status, orow.status, f"triggered on the observed last {rec.last if rec else '?'} ({dec.reason}); {dec.cap_note}", rec)
        ctx.event("ORDER_TRIGGERED", account_id=orow.account_id, instrument_id=orow.instrument_id, ref_type="order", ref_id=oid, last=_s(rec.last if rec else None),
                  stop_px=_s(orow.stop_px), reason=dec.reason)
        if dec.action == "TRIGGER":
            return None
    if dec.action == "FILL":
        return _fill_order(ctx, services, orow, rec, dec, new_order)
    if dec.first_look:
        conn.execute("UPDATE orders SET first_eval_done=1, last_quote_time=?, updated_ts=? WHERE order_id=?", (dec.quote_time, ctx.ts, oid))
        _order_event(conn, oid, ctx.ts, orow.status, orow.status, f"first executable look: {dec.reason} (last {rec.last if rec else '?'})", rec)
    return None


def _fill_order(ctx: TxContext, services, orow: OrderRow, rec: QuoteRecord, dec: Decision, new_order: bool, idem: Optional[str] = None, defer_margin: bool = False):
    conn = ctx.conn
    oid = orow.order_id
    idem = idem or match_idem(oid, dec.quote_time, dec.bar_ts)
    if conn.execute("SELECT 1 FROM fills WHERE idem_key=?", (idem,)).fetchone() is not None:
        return None
    conn.execute("SAVEPOINT fill_order")
    try:
        # advance filled_qty first so the slice being paid for is no longer reserved (sentence AVAILABLE; module docstring)
        conn.execute("UPDATE orders SET filled_qty = filled_qty + ? WHERE order_id=?", (int(dec.qty), oid))
        spec = execution.FillSpec(oid, orow.account_id, orow.instrument_id, orow.side, int(dec.qty), dec.price, "ORDER", rec, idem, dec.bar_ts, dec.price_basis,
                                  cap_qty=int(dec.qty), cap_note=dec.cap_note, borrow_id=orow.borrow_id, defer_margin=defer_margin, note=dec.note)
        fr = execution.apply_fill(ctx, spec, services)
        conn.execute("RELEASE SAVEPOINT fill_order")
    except ConflictError as e:
        if new_order or e.code not in ("INSUFFICIENT_FUNDS", "INSUFFICIENT_FUNDS_CCY"):
            raise
        conn.execute("ROLLBACK TO SAVEPOINT fill_order")
        conn.execute("RELEASE SAVEPOINT fill_order")
        _suspend(ctx, orow, e, rec)
        return None
    _finalize_order(ctx, services, orow, fr, dec, rec)
    return fr


def _finalize_order(ctx: TxContext, services, orow: OrderRow, fr, dec: Decision, rec: Optional[QuoteRecord]) -> str:
    conn = ctx.conn
    oid = orow.order_id
    rows = conn.execute("SELECT qty, price, fee_c FROM fills WHERE order_id=?", (oid,)).fetchall()
    filled = sum(int(r["qty"]) for r in rows)
    fees = sum(int(r["fee_c"]) for r in rows)
    avg = qprice((sum(Decimal(r["price"]) * int(r["qty"]) for r in rows) / Decimal(filled)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)) if filled else None
    status = "FILLED" if filled >= orow.qty else "PARTIAL"
    conn.execute("UPDATE orders SET filled_qty=?, avg_px=?, status=?, last_bar_ts=COALESCE(?, last_bar_ts), last_quote_time=?, first_eval_done=1, fees_c=?, updated_ts=?,"
                 " reason=NULL WHERE order_id=?", (filled, _s(avg), status, dec.bar_ts, dec.quote_time, fees, ctx.ts, oid))
    note = f"filled {fr.qty:,} @ {fr.price} ({dec.price_basis}); {dec.cap_note}" + (f"; {dec.note}" if dec.note else "")
    _order_event(conn, oid, ctx.ts, orow.status, status, note, rec)
    sweep = fr.funding.sweep_id if fr.funding is not None else None
    ctx.event("ORDER_FILLED" if status == "FILLED" else "ORDER_PARTIAL", account_id=orow.account_id, instrument_id=orow.instrument_id, ref_type="order", ref_id=oid,
              fill_id=fr.fill_id, qty=fr.qty, price=str(fr.price), price_basis=dec.price_basis, gross_c=fr.gross_c, fee_c=fr.fee_c, filled_qty=filled, sweep_id=sweep)
    if orow.parent_order_id is None and orow.package_id and orow.fill_mode != "AON":
        execution.resize_hedge_children(ctx, oid, filled, primary_ended=(status == "FILLED"))
    return status


def _suspend(ctx: TxContext, orow: OrderRow, e: ConflictError, rec: Optional[QuoteRecord]) -> None:
    conn = ctx.conn
    oid = orow.order_id
    conn.execute("UPDATE orders SET status='SUSPENDED', reason=?, first_eval_done=1, last_quote_time=?, updated_ts=? WHERE order_id=?",
                 (e.message, rec.quote_time if rec else None, ctx.ts, oid))
    _order_event(conn, oid, ctx.ts, orow.status, "SUSPENDED", f"fill could not be funded: {e.message}", rec)
    ctx.event("ORDER_SUSPENDED", account_id=orow.account_id, instrument_id=orow.instrument_id, ref_type="order", ref_id=oid, reason=e.message, details=_jsonable(e.details))
    ctx.attention("WARN", "orders", "ORDER_SUSPENDED", f"{oid} {orow.side} {orow.remaining:,} {orow.instrument_id}: {e.message}; resume after a deposit or transfer",
                  {"account_id": orow.account_id, "ref_type": "order", "ref_id": oid, "code": e.code, "details": _jsonable(e.details)})


# ---- order.submit ----------------------------------------------------------------------------------------------------------
def _check_preview_and_confirm(ctx: TxContext, services, vo: ValidatedOrder, payload: dict) -> None:
    pid = payload.get("preview_id")
    if pid:
        stored = preview_store(services).get(str(pid), ctx.ts)
        if stored is None:
            raise ConflictError("PREVIEW_STALE", "the preview is unknown or expired (120 s): review the fresh preview and resubmit",
                                {"preview": vo.preview, "diff": {"reason": "unknown_or_expired", "preview_id": pid}})
        old = (stored.get("execution") or {}).get("expected_price")
        new = (vo.preview.get("execution") or {}).get("expected_price")
        marketable_now = bool((vo.preview.get("execution") or {}).get("fill_now"))
        if old and new and (vo.order_type == "MARKET" or marketable_now):
            o, n = Decimal(str(old)), Decimal(str(new))
            moved = (n - o) / o if o else Decimal(0)
            against = moved > config.PRICE_MOVED_PCT if vo.side in BUY_SIDES else moved < -config.PRICE_MOVED_PCT
            if against:
                pct = (moved * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
                raise ConflictError("PREVIEW_STALE", f"the executable price moved {pct} % against you since the preview ({old} -> {new}): review and resubmit",
                                    {"preview": vo.preview, "diff": {"expected_price_old": str(old), "expected_price_new": str(new), "moved_pct": str(pct)}})
    f = vo.funding
    if f.get("confirm_required") and not vo.confirm_large:
        raise ConflictError("CONFIRM_REQUIRED", f"{f.get('confirm_reason')}: sweep-in {f.get('sweep_in')} (threshold {f.get('threshold')}), notional {vo.preview['economics']['gross']}"
                            f" of NAV {f.get('nav')}; resubmit the same request_id with confirm_large: true",
                            {"sweep_in": f.get("sweep_in"), "sweep_in_c": to_c(f.get("sweep_in") or "0"), "threshold": f.get("threshold"), "notional": vo.preview["economics"]["gross"],
                             "nav": f.get("nav"), "reason": f.get("confirm_reason"), "preview": vo.preview})


def _open_borrow(ctx: TxContext, services, vo: ValidatedOrder, order_id: str) -> Optional[dict]:
    if vo.side != "SELL_SHORT":
        return None
    lending = getattr(services, "lending", None)
    if lending is None or not hasattr(lending, "open_borrow"):
        raise RejectError("NOT_IN_THIS_BUILD", "short sales need the securities-lending package (WP-06)")
    borrow_id = lending.open_borrow(ctx, services, vo.account_id, vo.inst, vo.qty, vo.locate, order_id)
    ctx.conn.execute("UPDATE orders SET borrow_id=? WHERE order_id=?", (borrow_id, order_id))
    return {"borrow_id": borrow_id, "locate": _as_dict(vo.locate)}


def create_hedge_children(ctx: TxContext, services, primary_order_id: str, legs: List[dict], candidate_id: str, strategy_key: Optional[str] = None) -> str:
    """§10.9 sequential packages: a ``hedge_packages`` row for the primary and one WAITING_PARENT child order per leg
    (``{instrument_id, side, qty, order_type?, limit_px?}``) with ``ratio = leg qty / primary qty``; the children are sized
    to floor(primary filled x ratio) when the primary fills (``execution.resize_hedge_children``).  Returns the package id.
    ``hedges.package`` (WP-03) resolves a candidate's legs and calls this."""
    conn = ctx.conn
    praw = _order_raw(conn, primary_order_id)
    if praw is None:
        raise RejectError("NOT_FOUND", f"order {primary_order_id} does not exist")
    pqty = int(praw["qty"])
    pid = praw["package_id"]
    if not pid:
        pid = next_id(conn, "package")
        conn.execute("INSERT INTO hedge_packages(package_id, command_id, account_id, primary_order_id, candidate_id, strategy_key, legs_json, fill_mode, status, created_ts)"
                     " VALUES (?,?,?,?,?,?,?,?,?,?)", (pid, ctx.command_id, praw["account_id"], primary_order_id, candidate_id, strategy_key, dumps(_jsonable(legs)), "SEQUENTIAL",
                                                      "WORKING", ctx.ts))
        conn.execute("UPDATE orders SET package_id=? WHERE order_id=?", (pid, primary_order_id))
    for i, leg in enumerate(legs):
        req = {"account_id": praw["account_id"], "instrument_id": leg["instrument_id"], "side": leg["side"], "qty": int(leg["qty"]),
               "order_type": leg.get("order_type") or "MARKET", "limit_px": leg.get("limit_px"), "tif": praw["tif"]}
        vo = validate(services, req, snapshot=ctx.marks, conn=conn, now=ctx.ts, rest_ok=True)
        ratio = (Decimal(int(leg["qty"])) / Decimal(pqty)).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_EVEN).normalize()
        oid = _insert_order(ctx, vo, f"{praw['request_id']}:H{i + 1}", status="WAITING_PARENT", package_id=pid, parent_order_id=primary_order_id, ratio=ratio)
        _order_event(conn, oid, ctx.ts, None, "WAITING_PARENT", f"hedge leg of {primary_order_id} ({candidate_id}): {_summary(vo)}, ratio {ratio}", vo.rec)
        ctx.event("ORDER_SUBMITTED", account_id=vo.account_id, instrument_id=vo.inst.instrument_id, ref_type="order", ref_id=oid, side=vo.side, qty=vo.qty,
                  order_type=vo.order_type, tif=vo.tif, package_id=pid, parent_order_id=primary_order_id, ratio=str(ratio), status="WAITING_PARENT")
    return pid


def _attach_hedge(ctx: TxContext, services, order_id: str, vo: ValidatedOrder) -> Optional[str]:
    hedges = getattr(services, "hedges", None)
    if hedges is None or not hasattr(hedges, "package"):
        raise RejectError("NOT_IN_THIS_BUILD", "hedge packages need the hedges module (WP-03); submit the primary without a hedge")
    h = vo.hedge or {}
    pid = hedges.package(ctx, services, order_id, h.get("candidate_id"), h.get("legs"), h.get("ratio"))
    if pid:
        ctx.conn.execute("UPDATE orders SET package_id=COALESCE(package_id, ?) WHERE order_id=?", (pid, order_id))
    return pid


def _submit_single(ctx: TxContext, services, payload: dict, vo: ValidatedOrder, hedge_legs: Optional[List[dict]] = None, candidate_id: Optional[str] = None) -> dict:
    _check_preview_and_confirm(ctx, services, vo, payload)
    order_id = _insert_order(ctx, vo, ctx.request_id)
    borrow = _open_borrow(ctx, services, vo, order_id)
    package_id = None
    if hedge_legs:
        package_id = create_hedge_children(ctx, services, order_id, hedge_legs, candidate_id or "STRATEGY", candidate_id)
    elif vo.hedge:
        package_id = _attach_hedge(ctx, services, order_id, vo)
    _order_event(ctx.conn, order_id, ctx.ts, None, "WORKING", f"submitted: {_summary(vo)}; {vo.exec_reason if not vo.executable else 'quote executable'}", vo.rec)
    ctx.event("ORDER_SUBMITTED", account_id=vo.account_id, instrument_id=vo.inst.instrument_id, ref_type="order", ref_id=order_id, side=vo.side, qty=vo.qty,
              order_type=vo.order_type, tif=vo.tif, expires_ts=vo.expires_ts, package_id=package_id, limit_px=_s(vo.limit_px), stop_px=_s(vo.stop_px))
    orow = load_order(services, ctx.conn, order_id)
    dec = evaluate(orow, vo.rec, vo.session, ctx.ts, orow.participation, vo.inst)
    fr = _act(ctx, services, orow, vo.rec, dec, new_order=True)
    fills = [fr.to_dict()] if fr is not None else []
    return {"order": order_view(ctx.conn, order_id), "fills": fills, "sweeps": _sweeps_for(ctx.conn, [fr.fill_id] if fr else []), "borrow": borrow,
            "hedge_package": package_id, "warnings": vo.warnings, "decision": dec.to_dict(), "preview": vo.preview}


def _submit_package(ctx: TxContext, services, payload: dict) -> dict:
    strategies = getattr(services, "strategies", None)
    if strategies is None or not hasattr(strategies, "package_legs"):
        raise NotBuilt("NOT_BUILT", "strategy packages need strategies.package_legs (WP-24): not in this build")
    pk = payload["package"]
    key = str(pk.get("strategy_key") or "")
    account_id = str(payload.get("account_id") or "")
    spec = strategies.package_legs(services, account_id, key, pk)
    sd = _as_dict(spec)
    legs = [_as_dict(l) for l in (sd.get("legs") or [])]
    if not legs:
        raise RejectError("NOT_AVAILABLE", f"strategy {key}: no legs could be resolved")
    fill_mode = str(sd.get("fill_mode") or "SEQUENTIAL").upper()
    tif = str(payload.get("tif") or "DAY").upper()
    conn = ctx.conn
    if fill_mode != "AON":
        primary = None
        for l in legs:
            inst = services.instruments.get(l["instrument_id"], conn)
            if inst is not None and inst.kind != "OPTION":
                primary = l
                break
        if primary is None:
            raise RejectError("AON_LEGS", "a SEQUENTIAL package needs a stock or futures primary; an option-only package must be AON")
        req = {"account_id": account_id, "instrument_id": primary["instrument_id"], "side": primary["side"], "qty": int(primary["qty"]),
               "order_type": primary.get("order_type") or "MARKET", "limit_px": primary.get("limit_px"), "tif": tif, "confirm_large": payload.get("confirm_large"),
               "preview_id": payload.get("preview_id")}
        vo = validate(services, req, snapshot=ctx.marks, conn=conn, now=ctx.ts)
        others = [l for l in legs if l is not primary]
        out = _submit_single(ctx, services, req, vo, hedge_legs=others, candidate_id=key)
        out["package"] = {"package_id": out["hedge_package"], "fill_mode": "SEQUENTIAL", "strategy_key": key}
        return out
    insts = [services.instruments.get(l["instrument_id"], conn) for l in legs]
    if any(i is None or i.kind != "OPTION" for i in insts) or len({i.underlying_id for i in insts}) != 1:
        raise RejectError("AON_LEGS", "an all-or-none package must be options on one underlying only", {"legs": [l.get("instrument_id") for l in legs]})
    vos = []
    for l in legs:
        req = {"account_id": account_id, "instrument_id": l["instrument_id"], "side": l["side"], "qty": int(l["qty"]), "order_type": "MARKET", "tif": tif}
        vos.append(validate(services, req, snapshot=ctx.marks, conn=conn, now=ctx.ts, rest_ok=True))
    if any(v.funding.get("confirm_required") for v in vos) and not payload.get("confirm_large"):
        raise ConflictError("CONFIRM_REQUIRED", "a leg of the package needs a sweep-in above the confirm threshold or exceeds 25 % of NAV; resubmit with confirm_large: true",
                            {"legs": [{"instrument_id": v.inst.instrument_id, "funding": v.funding} for v in vos]})
    pid = next_id(conn, "package")
    conn.execute("INSERT INTO hedge_packages(package_id, command_id, account_id, primary_order_id, candidate_id, strategy_key, legs_json, fill_mode, status, created_ts)"
                 " VALUES (?,?,?,NULL,?,?,?,?,?,?)", (pid, ctx.command_id, account_id, key, key, dumps(_jsonable(legs)), "AON", "WORKING", ctx.ts))
    oids = []
    for i, vo in enumerate(vos):
        oid = _insert_order(ctx, vo, f"{ctx.request_id}:L{i + 1}", fill_mode="AON", package_id=pid)
        _order_event(conn, oid, ctx.ts, None, "WORKING", f"AON package {pid} ({key}) leg {i + 1}: {_summary(vo)}", vo.rec)
        ctx.event("ORDER_SUBMITTED", account_id=account_id, instrument_id=vo.inst.instrument_id, ref_type="order", ref_id=oid, side=vo.side, qty=vo.qty, order_type="MARKET",
                  tif=tif, package_id=pid, fill_mode="AON")
        oids.append(oid)
    rows = [load_order(services, conn, oid) for oid in oids]
    recs = {vo.inst.instrument_id: vo.rec for vo in vos}
    pkg = conn.execute("SELECT * FROM hedge_packages WHERE package_id=?", (pid,)).fetchone()
    dec = evaluate_package(pkg, rows, recs, vos[0].session, ctx.ts)
    result = None
    if dec.action == "FILL":
        result = _fill_package(ctx, services, pkg, rows, recs, dec, new_package=True)
    return {"package": {"package_id": pid, "fill_mode": "AON", "strategy_key": key, "status": (result or {}).get("status", "WORKING"), "legs": legs},
            "orders": [order_view(conn, oid) for oid in oids], "decision": dec.to_dict(), "fills": (result or {}).get("fills", []), "margin": (result or {}).get("margin"),
            "sweeps": (result or {}).get("sweeps", []), "warnings": [w for v in vos for w in v.warnings], "previews": [v.preview for v in vos]}


def _fill_package(ctx: TxContext, services, pkg, legs: List[OrderRow], recs: Dict[str, QuoteRecord], dec: PackageDecision, new_package: bool = False) -> dict:
    """Apply every leg of an AON package from one observation inside one SAVEPOINT (sells first, then buys), then recompute
    the option margin once; a funding refusal rolls everything back and suspends the package (or refuses a new one)."""
    conn = ctx.conn
    pid = pkg["package_id"]
    qt = dec.quote_time
    by_id = {l.order_id: l for l in legs}
    ordered = sorted(dec.legs, key=lambda ld: 0 if ld.side in SELL_SIDES else 1)
    fills, statuses = [], {}
    conn.execute("SAVEPOINT fill_pkg")
    try:
        for ld in ordered:
            leg = by_id[ld.order_id]
            rec = recs[leg.instrument_id]
            idem = f"matchpkg:{pid}:{qt}:{leg.order_id}"
            d = Decision("FILL", ld.price, ld.qty, "AON_OK", None, qt, ld.price_basis, cap_note="all-or-none: the displayed size covers the whole leg in this observation")
            conn.execute("UPDATE orders SET filled_qty = filled_qty + ? WHERE order_id=?", (int(ld.qty), leg.order_id))
            spec = execution.FillSpec(leg.order_id, leg.account_id, leg.instrument_id, leg.side, int(ld.qty), ld.price, "ORDER", rec, idem, None, ld.price_basis,
                                      cap_qty=int(ld.qty), cap_note=d.cap_note, defer_margin=True)
            fr = execution.apply_fill(ctx, spec, services)
            statuses[leg.order_id] = _finalize_order(ctx, services, leg, fr, d, rec)
            fills.append(fr)
        margin = None
        if any(by_id[ld.order_id].kind == "OPTION" for ld in ordered):
            margin = execution.recompute_option_margin(ctx, services, legs[0].account_id, f"matchpkg:{pid}:{qt}")
        conn.execute("RELEASE SAVEPOINT fill_pkg")
    except ConflictError as e:
        if new_package or e.code not in ("INSUFFICIENT_FUNDS", "INSUFFICIENT_FUNDS_CCY"):
            raise
        conn.execute("ROLLBACK TO SAVEPOINT fill_pkg")
        conn.execute("RELEASE SAVEPOINT fill_pkg")
        for leg in legs:
            _suspend(ctx, leg, e, recs.get(leg.instrument_id))
        conn.execute("UPDATE hedge_packages SET status='SUSPENDED' WHERE package_id=?", (pid,))
        return {"status": "SUSPENDED", "package_id": pid, "fills": [], "margin": None, "sweeps": [], "reason": e.message}
    conn.execute("UPDATE hedge_packages SET status='FILLED' WHERE package_id=?", (pid,))
    ctx.event("PACKAGE_FILLED", account_id=legs[0].account_id, ref_type="package", ref_id=pid, fills=[f.fill_id for f in fills], quote_time=qt,
              margin_required_c=None if margin is None else margin.get("required_c"))
    return {"status": "FILLED", "package_id": pid, "fills": [f.to_dict() for f in fills], "margin": margin, "sweeps": _sweeps_for(conn, [f.fill_id for f in fills]),
            "quote_time": qt}


def h_submit(ctx: TxContext, payload: dict) -> dict:
    services = _services(ctx)
    if isinstance(payload.get("package"), dict):
        return _submit_package(ctx, services, payload)
    vo = validate(services, payload, snapshot=ctx.marks, conn=ctx.conn, now=ctx.ts)
    if vo.inst.kind == "PRIVATE":
        raise RejectError("NOT_IN_THIS_BUILD", "private secondaries are negotiated through private.trade (WP-19), not the order ticket")
    return _submit_single(ctx, services, payload, vo)


# ---- order.match / order.match_package ------------------------------------------------------------------------------------------
def _rec_from_payload(d) -> Optional[QuoteRecord]:
    if not d:
        return None
    if isinstance(d, QuoteRecord):
        return d
    return QuoteRecord.from_dict(dict(d))


def h_match(ctx: TxContext, payload: dict) -> dict:
    """The matcher's command (actor ``matcher``): re-read the order, re-evaluate against the payload's quote with the
    command clock, and act (fill / trigger / ratchet / first look).  A cancel that committed in between wins (NOT_OPEN)."""
    services = _services(ctx)
    oid = str(payload.get("order_id") or "")
    raw = _order_raw(ctx.conn, oid)
    if raw is None:
        raise RejectError("NOT_FOUND", f"order {oid} does not exist")
    inst = services.instruments.get(raw["instrument_id"], ctx.conn)
    orow = OrderRow.from_row(raw, inst)
    if orow.status not in OPEN_STATUSES:
        return {"order_id": oid, "action": "NONE", "reason": "NOT_OPEN", "status": orow.status}
    rec = _rec_from_payload(payload.get("quote"))
    session = ctx.sessions.state(orow.session_group, ctx.ts)
    dec = evaluate(orow, rec, session, ctx.ts, orow.participation, inst)
    fr = _act(ctx, services, orow, rec, dec, new_order=False)
    out = {"order_id": oid, "action": dec.action, "reason": dec.reason, "decision": dec.to_dict(), "fill": None if fr is None else fr.to_dict(),
           "sweeps": _sweeps_for(ctx.conn, [fr.fill_id] if fr else []), "order": order_view(ctx.conn, oid)}
    if dec.action == "FILL" and fr is None:
        out["action"], out["reason"] = ("NONE", "ALREADY_APPLIED") if out["order"]["status"] != "SUSPENDED" else ("SUSPENDED", "INSUFFICIENT_FUNDS")
    return out


def package_legs_rows(services, conn, package_id: str) -> List[OrderRow]:
    rows = conn.execute("SELECT * FROM orders WHERE package_id=? AND fill_mode='AON' ORDER BY order_id", (package_id,)).fetchall()
    return [OrderRow.from_row(r, services.instruments.get(r["instrument_id"], conn)) for r in rows]


def h_match_package(ctx: TxContext, payload: dict) -> dict:
    """The matcher's AON command (idem ``matchpkg:{package_id}:{quote_time}``): every leg from the payload's quotes in one
    transaction, one margin recomputation; nothing fills unless every leg can."""
    services = _services(ctx)
    pid = str(payload.get("package_id") or "")
    pkg = ctx.conn.execute("SELECT * FROM hedge_packages WHERE package_id=?", (pid,)).fetchone()
    if pkg is None:
        raise RejectError("NOT_FOUND", f"package {pid} does not exist")
    legs = package_legs_rows(services, ctx.conn, pid)
    if not legs or any(l.status not in OPEN_STATUSES for l in legs):
        return {"package_id": pid, "action": "NONE", "reason": "NOT_OPEN", "status": pkg["status"]}
    recs = {iid: _rec_from_payload(d) for iid, d in (payload.get("quotes") or {}).items()}
    session = ctx.sessions.state(legs[0].session_group, ctx.ts)
    dec = evaluate_package(pkg, legs, recs, session, ctx.ts)
    if dec.action != "FILL":
        return {"package_id": pid, "action": "NONE", "reason": dec.reason, "decision": dec.to_dict()}
    if ctx.conn.execute("SELECT 1 FROM fills WHERE idem_key LIKE ?", (f"matchpkg:{pid}:{dec.quote_time}:%",)).fetchone() is not None:
        return {"package_id": pid, "action": "NONE", "reason": "ALREADY_APPLIED", "decision": dec.to_dict()}
    res = _fill_package(ctx, services, pkg, legs, recs, dec)
    res.update({"action": "FILL" if res["status"] == "FILLED" else "SUSPENDED", "decision": dec.to_dict(), "orders": [order_view(ctx.conn, l.order_id) for l in legs]})
    return res


# ---- order.cancel / order.expire / order.resume ---------------------------------------------------------------------------------
def _close_order(ctx: TxContext, services, raw, to_status: str, reason: str, event_type: str) -> dict:
    conn = ctx.conn
    oid = raw["order_id"]
    conn.execute("UPDATE orders SET status=?, reason=?, updated_ts=? WHERE order_id=?", (to_status, reason, ctx.ts, oid))
    _order_event(conn, oid, ctx.ts, raw["status"], to_status, reason)
    ctx.event(event_type, account_id=raw["account_id"], instrument_id=raw["instrument_id"], ref_type="order", ref_id=oid, reason=reason, filled_qty=int(raw["filled_qty"]))
    if raw["borrow_id"] and int(raw["filled_qty"]) == 0:
        lending = getattr(services, "lending", None)
        if lending is not None and hasattr(lending, "release_for_cancel"):
            lending.release_for_cancel(ctx, services, raw["borrow_id"], oid, reason)
    if raw["parent_order_id"] is None and raw["package_id"] and raw["fill_mode"] != "AON":
        execution.resize_hedge_children(ctx, oid, int(raw["filled_qty"]), primary_ended=True)
    return {"order_id": oid, "status": to_status, "filled_qty": int(raw["filled_qty"])}


def _close_package(ctx: TxContext, services, package_id: str, to_status: str, reason: str, event_type: str) -> List[dict]:
    rows = ctx.conn.execute("SELECT * FROM orders WHERE package_id=? AND status IN ('WORKING','PARTIAL','SUSPENDED','WAITING_PARENT') ORDER BY order_id", (package_id,)).fetchall()
    out = [_close_order(ctx, services, r, to_status, reason, event_type) for r in rows]
    ctx.conn.execute("UPDATE hedge_packages SET status=? WHERE package_id=? AND status NOT IN ('FILLED')", (to_status, package_id))
    return out


def h_cancel(ctx: TxContext, payload: dict) -> dict:
    """User cancel: an open order (WORKING / PARTIAL / SUSPENDED / WAITING_PARENT) -> CANCELLED, else 409 NOT_OPEN.  A
    package cancel (``package_id``), or the cancel of any leg of an AON package, cancels every leg; a primary's children
    are cancelled when it ends unfilled."""
    services = _services(ctx)
    reason = str(payload.get("reason") or "cancelled by the user")
    pid = payload.get("package_id")
    if pid:
        if ctx.conn.execute("SELECT 1 FROM hedge_packages WHERE package_id=?", (pid,)).fetchone() is None:
            raise RejectError("NOT_FOUND", f"package {pid} does not exist")
        legs = _close_package(ctx, services, str(pid), "CANCELLED", reason, "ORDER_CANCELLED")
        if not legs:
            raise ConflictError("NOT_OPEN", f"package {pid} has no open legs")
        return {"package_id": pid, "cancelled": legs}
    oid = str(payload.get("order_id") or "")
    raw = _order_raw(ctx.conn, oid)
    if raw is None:
        raise RejectError("NOT_FOUND", f"order {oid} does not exist")
    if raw["status"] not in LIVE_STATUSES:
        raise ConflictError("NOT_OPEN", f"order {oid} is {raw['status']}: only working, partial, suspended or waiting orders can be cancelled", {"status": raw["status"]})
    if raw["package_id"] and raw["fill_mode"] == "AON":
        legs = _close_package(ctx, services, raw["package_id"], "CANCELLED", f"{reason} (all-or-none package: every leg)", "ORDER_CANCELLED")
        return {"order": order_view(ctx.conn, oid), "package_id": raw["package_id"], "cancelled": legs}
    _close_order(ctx, services, raw, "CANCELLED", reason, "ORDER_CANCELLED")
    return {"order": order_view(ctx.conn, oid)}


def h_expire(ctx: TxContext, payload: dict) -> dict:
    """Lifecycle (EXPIRE_ORDERS) or targeted: every open order past ``expires_ts`` (or whose instrument expired) -> EXPIRED;
    ``order_id`` targets one order (``force`` ignores the clock).  Never fills after expiry."""
    services = _services(ctx)
    conn = ctx.conn
    oid = payload.get("order_id")
    if oid:
        rows = [r for r in [_order_raw(conn, str(oid))] if r is not None]
        if not rows:
            raise RejectError("NOT_FOUND", f"order {oid} does not exist")
    else:
        rows = conn.execute("SELECT o.* FROM orders o JOIN instruments i ON i.instrument_id=o.instrument_id WHERE o.status IN ('WORKING','PARTIAL','SUSPENDED','WAITING_PARENT')"
                            " AND (o.expires_ts < ? OR i.status <> 'ACTIVE' OR (i.expiry IS NOT NULL AND i.expiry < ?)) ORDER BY o.order_id",
                            (ctx.ts, ny_of(ctx.ts).date().isoformat())).fetchall()
    out = []
    for r in rows:
        if r["status"] not in LIVE_STATUSES:
            if oid:
                raise ConflictError("NOT_OPEN", f"order {r['order_id']} is {r['status']}", {"status": r["status"]})
            continue
        due = ctx.ts > int(r["expires_ts"])
        if oid and not due and not payload.get("force"):
            raise ConflictError("NOT_EXPIRED", f"order {r['order_id']} expires at {ny_of(int(r['expires_ts'])).strftime('%Y-%m-%d %H:%M ET')}", {"expires_ts": int(r["expires_ts"])})
        why = "DAY order: session closed" if r["tif"] == "DAY" else "GTC order: 90 days or the instrument's expiry reached"
        if r["package_id"] and r["fill_mode"] == "AON":
            out.extend(_close_package(ctx, services, r["package_id"], "EXPIRED", why, "ORDER_EXPIRED"))
        else:
            out.append(_close_order(ctx, services, r, "EXPIRED", why, "ORDER_EXPIRED"))
    return {"expired": out}


def h_resume(ctx: TxContext, payload: dict) -> dict:
    """SUSPENDED -> WORKING after a deposit or transfer (409 NOT_SUSPENDED otherwise); the matcher re-evaluates it next."""
    oid = str(payload.get("order_id") or "")
    raw = _order_raw(ctx.conn, oid)
    if raw is None:
        raise RejectError("NOT_FOUND", f"order {oid} does not exist")
    if raw["status"] != "SUSPENDED":
        raise ConflictError("NOT_SUSPENDED", f"order {oid} is {raw['status']}", {"status": raw["status"]})
    to = "PARTIAL" if int(raw["filled_qty"]) > 0 else "WORKING"
    ctx.conn.execute("UPDATE orders SET status=?, reason=NULL, updated_ts=? WHERE order_id=?", (to, ctx.ts, oid))
    _order_event(ctx.conn, oid, ctx.ts, "SUSPENDED", to, "resumed by the user")
    ctx.event("ORDER_RESUMED", account_id=raw["account_id"], instrument_id=raw["instrument_id"], ref_type="order", ref_id=oid)
    ctx.conn.execute("UPDATE attention SET resolved_ts=? WHERE code='ORDER_SUSPENDED' AND ref_id=? AND resolved_ts IS NULL", (ctx.ts, oid))
    return {"order": order_view(ctx.conn, oid)}


def h_record_rejection(ctx: TxContext, payload: dict) -> dict:
    """Light: the audit row of a refused submission (handoff §7: rejected orders are preserved)."""
    req = payload.get("request") or {}
    cur = ctx.conn.execute("INSERT INTO order_rejections(ts, request_id, account_id, instrument_id, side, qty, order_type, code, message, payload_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (ctx.ts, str(payload.get("request_id") or ctx.request_id), req.get("account_id"), req.get("instrument_id"), req.get("side"),
                            req.get("qty") if isinstance(req.get("qty"), int) else None, req.get("order_type"), str(payload.get("code") or "REJECTED"),
                            str(payload.get("message") or ""), dumps(_jsonable(req))))
    return {"id": int(cur.lastrowid)}


def record_rejection(services, request_id: str, req: dict, code: str, message: str):
    """Record a refused submission through the bus (the API calls this after a 4xx from ``order.submit``)."""
    rid = f"rej:{request_id}"[:80]
    return services.bus.execute(Command("order.record_rejection", {"request_id": request_id, "request": _jsonable(req), "code": code, "message": message}, rid, "ui"))


def register(bus) -> None:
    bus.register("order.submit", h_submit)
    bus.register("order.cancel", h_cancel)
    bus.register("order.match", h_match)
    bus.register("order.match_package", h_match_package)
    bus.register("order.expire", h_expire)
    bus.register("order.resume", h_resume)
    bus.register("order.record_rejection", h_record_rejection, light=True)
