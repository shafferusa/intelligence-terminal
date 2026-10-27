"""Orders: validation, preview, the pure matching rules, package (all-or-none) matching, handlers and the Matcher
(TERMINAL_DESIGN.md §2.12 orders.py, §10.1-10.9, §19.9, §19.22; TERMINAL_EXECUTION.md).

Conventions stated here (the §1.3 sentences that bite in this module)
- FILL_PRICE: fills happen at the last observed trade (Yahoo, up to 15 min delayed) or, for options, at the Cboe
  delayed bid (sells) / ask (buys); nothing here invents a price.  ``evaluate()`` is pure: it reads an order row,
  one observed ``QuoteRecord``, one ``SessionState`` and the clock and returns a ``Decision``; it never touches the
  database, the network or the cache.
- LIMIT_RULE: a limit fills at the observed price whenever that price is at or better than the limit; a limit that was
  RESTING (not marketable at its previous executable look, ``orders.limit_rested``) and is then crossed fills at the limit
  price (EXEC-5).  ``first_eval_done`` is persisted after the order's first EXECUTABLE look (a stale or closed look does not
  count).  Listed options are judged on the Cboe ask (buys) / bid (sells), never their own last trade (EXEC-1).
- STOP_RULE: stops trigger on the observed last trade and fill at that print (basis ``stop_print``); a gap through
  the stop fills at the gap price, disclosed in the decision.  A STOP_LIMIT whose stop is reached while its limit is not
  marketable is persisted as triggered (EXEC-4: ORDER_TRIGGERED) and rests as a limit.  Options trigger on the bid / ask.
- TRAILING: a trailing stop ratchets with each observed print in your favour (sell: highest last minus the trail;
  buy: lowest last plus the trail) and never moves back; the ratchet is persisted through ``order.match`` with action
  RATCHET (one order_events row per change) so a restart keeps it; it triggers on the observed last and fills at
  that print.  ``trail_pct`` re-derives the trail from the ratcheted reference; the stop is quantised to the tick.
- PARTICIPATION / NO_VOLUME: each new completed 1-minute bar lets at most ``participation`` (default 10 %) of its
  volume fill across EVERY order, account and book (EXEC-6: the fills already taken from that bar count); the same bar
  never fills one order twice (``last_bar_ts``); with no volume information the order fills in full; options are capped by
  the displayed size, FX has no volume concept.
- SESSION: orders entered outside the session rest until it opens (never a rejection); DAY orders expire at that
  session's close; GTC at the close of the session of the 90th calendar day (or at the instrument's expiry).  No OPENING
  option trade fills after 15:30 ET on its expiry day (EXEC-9, ``LAST_SESSION``).  A print from a previous session never fills
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
  fee; futures: IM + fee; SELL_SHORT: the 102 % collateral less the sale's own proceeds = 2 % excess + fee; plus a picked
  hedge's legs); the shortfall against the account's available cash is the sweep-in the fill will make - automatic at any
  size (decision s7.11, SR-12); an order whose opening notional is above 25 % of the lifetime's NAV (or whose sweep-in is above
  a threshold the owner set himself) needs ``confirm_large`` (409 CONFIRM_REQUIRED naming the rule that fired with its own
  threshold, USX-18); unfundable from own + Treasury + PB headroom -> FUNDS_WAIT (below).
- FUNDS_WAIT (hotfix 1.0.3.1, 2026-10-21, ported to 1.0.4; the owner: "so that they queue"): a NEW order (single, hedge package or
  AON package) whose only blocker is funding is accepted and stored SUSPENDED with the reason "waiting for funds: needs X,
  available Y" (one ORDER_SUBMITTED event, one INFO attention item ``ORDER_WAITING_FUNDS``); a SUSPENDED order reserves nothing
  (``accounting.reserved_c`` counts WORKING / PARTIAL and the WAITING_PARENT legs of a primary that is not SUSPENDED), so it
  blocks no other order.  Every other blocker (quote, session, borrow, limits, confirm) still refuses.  ``plan_funds_resume``
  (the matcher's sweep, command ``order.resume_funded``) walks the waiting orders oldest first and re-checks each exactly like
  the preview (own available - settled-now cash for futures, USX-17 - -> Treasury sweep -> PB headroom, the need at the current
  observation) against a running budget per account / lifetime, so two orders never claim the same cash; those that fit go
  SUSPENDED -> WORKING ("resumed: funds available") and the matcher fills them by the normal rules.  A resting or resumed order
  whose fill cannot be funded goes back to SUSPENDED (waiting for funds) - one attention item per order while it waits, never a
  second - and is re-tried after a back-off (60 s doubling to 30 min) so a fill-time measure stricter than the preview cannot
  churn.  An order suspended for any other refusal (TX-3) is never resumed automatically.  DAY orders still expire at the close,
  GTC at the end of their 90 days.
- Idempotency: the matcher's command id is ``match:<order_id>:<quote_time>:<bar_ts|none>`` and the fill's idem_key
  the same string, so a second evaluation of the same observation is a replay (no second fill); ``order.match``
  re-reads status / filled_qty / expires_ts inside the transaction and re-checks ``is_executable`` with the
  payload's quote and the command clock (a cancel that committed in between wins).
- Reservation at fill time: before a fill is applied the order's ``filled_qty`` is advanced by the fill quantity
  (inside a SAVEPOINT) so that ``accounting.reserved_c`` no longer reserves the slice being paid for and the sweep-in
  is exactly the shortfall; a refusal of a resting order's fill - funding or any other (OVERSELL after an exercise
  delivered the shares ...) - rolls the savepoint back and SUSPENDS it with the reason and ONE attention per order (TX-3); a
  funding refusal suspends it as a FUNDS_WAIT, and a NEW order whose immediate fill cannot be funded is kept the same way
  (queued, never refused for funding alone) while any other refusal of a new order's fill still refuses it.  ``fx_convert``
  stored on the order's preview passes to the fill.
- Rejection audit (TX-7): every refused submission is recorded once per attempt (``rej:<request_id>:<sha8(code+message)>``);
  a failed audit write is logged.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Dict, Iterable, List, Optional

from . import config, execution
from .books import account_row, lifetime_row
from .commands import Command, ConflictError, RejectError, TxContext
from .db import dumps, next_id
from .clock import ny_of
from .instruments import Instrument
from .matcher import Matcher                                                                        # noqa: F401 (re-export)
from .orders_preview import (PreviewStore, ValidatedOrder, _as_dict, _jsonable, _s, bar_usage, funding_fit, preview, preview_store, quote_of,   # noqa: F401
                             resting_need, session_for, validate, with_nbbo)
from .orders_rules import (AUTO_RESUME_NOTE, BUY_SIDES, CASH_SIDES, FUNDS_CODES, FUNDS_WAIT, FUNDS_WAIT_ITEM, FUTURES_SIDES, LIVE_STATUSES,   # noqa: F401
                           NEEDS_QUOTE_TYPES, OPEN_STATUSES, OPENING_SIDES, OPTION_SIDES, ORDER_TYPES, SELL_SIDES, STOP_TYPES, TIFS, Decision, LegDecision,
                           OrderRow, PackageDecision, evaluate, evaluate_package, exec_price, fill_cap, funds_wait_from_error, funds_wait_reason, is_funds_wait,
                           match_idem, on_tick, trail_amount, trail_stop)
from .quotes import QuoteRecord
from .units import fmt_money, qprice, to_c


class NotBuilt(RejectError):
    """A package request that needs a module this build does not have yet -> HTTP 501."""
    http = 501


FUNDING_CODES = FUNDS_CODES                     # ("INSUFFICIENT_FUNDS", "INSUFFICIENT_FUNDS_CCY")
FUNDS_ITEM_CODES = (FUNDS_WAIT_ITEM, "ORDER_SUSPENDED")
FUNDS_RESUME_BACKOFF_S = 60          # a resumed order that could not be funded at its fill waits 60 s, doubling per retry ...
FUNDS_RESUME_BACKOFF_MAX_S = 1800    # ... up to 30 min, before the next automatic resume
#: C15 / C12b: the fill passes ``fx_convert`` once execution.FillSpec carries the field (P04); before that the order needs its FX leg
FILLSPEC_FX_CONVERT = "fx_convert" in getattr(execution.FillSpec, "__dataclass_fields__", {})
#: Build 2 schema additions of the order book (C4 ``Database.ensure_schema_ext``, run by ``bind``): the resting state of a limit
#: (EXEC-5) and the per-bar index the participation cap reads (EXEC-6)
SCHEMA_EXT = ["ALTER TABLE orders ADD COLUMN limit_rested INTEGER", "CREATE INDEX IF NOT EXISTS ix_fills_bar ON fills(instrument_id, bar_ts)"]


def bind(rt) -> None:
    """Called by the server when it binds the orders module: the additive schema of SCHEMA_EXT (idempotent)."""
    fn = getattr(getattr(rt, "db", None), "ensure_schema_ext", None)
    if callable(fn):
        fn("orders", SCHEMA_EXT)


def _trigger_text(orow: OrderRow, rec: Optional[QuoteRecord]) -> str:
    """'the observed last 769.10' - or, for a listed option, its executable side: 'the observed bid 65.01' (EXEC-1)."""
    if rec is None:
        return "the observed price ?"
    if orow.kind == "OPTION":
        if orow.side in BUY_SIDES:
            return f"the observed ask {rec.ask}"
        return f"the observed bid {rec.bid}"
    return f"the observed last {rec.last}"


def _look_text(orow: OrderRow, rec: Optional[QuoteRecord]) -> str:
    if rec is None:
        return "no quote"
    if orow.kind == "OPTION":
        return f"bid {rec.bid} / ask {rec.ask}"
    return f"last {rec.last}"


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
                  parent_order_id: Optional[str] = None, ratio: Optional[Decimal] = None, reason: Optional[str] = None) -> str:
    oid = next_id(ctx.conn, "order")
    ctx.conn.execute(
        "INSERT INTO orders(order_id, request_id, account_id, instrument_id, side, qty, filled_qty, avg_px, order_type, limit_px, stop_px, trail_amt, trail_pct, trail_ref_px,"
        " fill_mode, tif, status, triggered, first_eval_done, last_bar_ts, last_quote_time, session_group, created_ts, updated_ts, expires_ts, participation, borrow_id,"
        " package_id, parent_order_id, ratio, fees_c, reason, preview_json) VALUES (?,?,?,?,?,?,0,NULL,?,?,?,?,?,?,?,?,?,0,0,NULL,NULL,?,?,?,?,?,NULL,?,?,?,0,?,?)",
        (oid, request_id, vo.account_id, vo.inst.instrument_id, vo.side, vo.qty, vo.order_type, _s(vo.limit_px), _s(vo.stop_px), _s(vo.trail_amt), _s(vo.trail_pct),
         _s(vo.trail_ref_px), fill_mode, vo.tif, status, vo.inst.session_group, ctx.ts, ctx.ts, vo.expires_ts, str(vo.participation), package_id, parent_order_id,
         None if ratio is None else str(ratio), reason, dumps(vo.preview)))
    return oid


def _summary(vo: ValidatedOrder) -> str:
    px = f" @ {vo.limit_px}" if vo.limit_px is not None else ""
    st = f" stop {vo.stop_px}" if vo.stop_px is not None else ""
    return f"{vo.side} {vo.qty:,} {vo.inst.symbol} {vo.order_type}{px}{st} {vo.tif}"


# ---- the shared fill path (§10.7) --------------------------------------------------------------------------------------------

def _act(ctx: TxContext, services, orow: OrderRow, rec: Optional[QuoteRecord], dec: Decision, new_order: bool):
    """Persist a Decision: RATCHET / TRIGGER / first look / a limit's resting state update the row; FILL applies the fill.  A
    decision that reached the stop is persisted as triggered whatever its action (EXEC-4: a STOP_LIMIT hit while its limit is
    not marketable rests as a triggered limit).  Returns the FillResult or None."""
    conn = ctx.conn
    oid = orow.order_id
    if dec.persist_rested and orow.rested_col and dec.rested is not None and dec.action != "FILL":
        conn.execute("UPDATE orders SET limit_rested=?, updated_ts=? WHERE order_id=?", (1 if dec.rested else 0, ctx.ts, oid))
    if dec.action == "RATCHET":
        conn.execute("UPDATE orders SET stop_px=?, trail_ref_px=?, first_eval_done=1, last_quote_time=?, updated_ts=? WHERE order_id=?",
                     (_s(dec.stop_px), _s(dec.trail_ref_px), dec.quote_time, ctx.ts, oid))
        _order_event(conn, oid, ctx.ts, orow.status, orow.status, f"RATCHET: {dec.note}", rec)
        ctx.event("ORDER_RATCHETED", account_id=orow.account_id, instrument_id=orow.instrument_id, ref_type="order", ref_id=oid, stop_px=_s(dec.stop_px),
                  trail_ref_px=_s(dec.trail_ref_px), last=_s(rec.last if rec else None), observed=_trigger_text(orow, rec))
        return None
    if dec.triggered and not orow.triggered:
        conn.execute("UPDATE orders SET triggered=1, first_eval_done=1, last_quote_time=?, updated_ts=? WHERE order_id=?", (dec.quote_time, ctx.ts, oid))
        rests = f"; rests as a limit at {orow.limit_px} (not marketable now)" if dec.action == "NONE" and orow.limit_px is not None else ""
        _order_event(conn, oid, ctx.ts, orow.status, orow.status, f"triggered on {_trigger_text(orow, rec)} ({dec.reason}){rests}"
                     + (f"; {dec.cap_note}" if dec.cap_note else ""), rec)
        ctx.event("ORDER_TRIGGERED", account_id=orow.account_id, instrument_id=orow.instrument_id, ref_type="order", ref_id=oid, last=_s(rec.last if rec else None),
                  observed=_trigger_text(orow, rec), stop_px=_s(orow.stop_px), limit_px=_s(orow.limit_px), reason=dec.reason)
        if dec.action != "FILL":
            return None
    if dec.action == "FILL":
        fr = _fill_order(ctx, services, orow, rec, dec, new_order)
        if fr is not None and orow.rested_col and dec.rested is False:
            conn.execute("UPDATE orders SET limit_rested=0 WHERE order_id=?", (oid,))
        return fr
    if dec.first_look:
        conn.execute("UPDATE orders SET first_eval_done=1, last_quote_time=?, updated_ts=? WHERE order_id=?", (dec.quote_time, ctx.ts, oid))
        _order_event(conn, oid, ctx.ts, orow.status, orow.status, f"first executable look: {dec.reason} ({_look_text(orow, rec)})", rec)
    return None


def _fx_convert_flag(conn, order_id: str) -> bool:
    """C15: the order's stored preview asked for the automatic USD conversion (funding.fx_convert)."""
    row = conn.execute("SELECT preview_json FROM orders WHERE order_id=?", (order_id,)).fetchone()
    if row is None or not row["preview_json"]:
        return False
    try:
        return bool(((json.loads(row["preview_json"]) or {}).get("funding") or {}).get("fx_convert"))
    except (ValueError, TypeError, AttributeError):
        return False


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
        if FILLSPEC_FX_CONVERT and _fx_convert_flag(conn, oid):
            spec = dataclasses.replace(spec, fx_convert=True)         # C15: the fill converts USD into the order's currency first (C12b)
        fr = execution.apply_fill(ctx, spec, services)
        conn.execute("RELEASE SAVEPOINT fill_order")
    except RejectError as e:                   # ConflictError included
        if new_order and e.code not in FUNDING_CODES:
            raise
        # TX-3: a resting order whose fill is refused - unfunded, or any other refusal (OVERSELL after an exercise delivered the
        # shares ...) - is SUSPENDED with the reason and one attention, instead of being retried on every matcher pass; FUNDS_WAIT: a
        # funding refusal (a new order's too) keeps the order, queued for funds, resumed automatically when cash frees up
        conn.execute("ROLLBACK TO SAVEPOINT fill_order")
        conn.execute("RELEASE SAVEPOINT fill_order")
        _suspend(ctx, orow, e, rec, new_order=new_order)
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
    _resolve_funds_items(ctx, oid)                       # FUNDS_WAIT: the wait is over once the order fills
    sweep = fr.funding.sweep_id if fr.funding is not None else None
    ctx.event("ORDER_FILLED" if status == "FILLED" else "ORDER_PARTIAL", account_id=orow.account_id, instrument_id=orow.instrument_id, ref_type="order", ref_id=oid,
              fill_id=fr.fill_id, qty=fr.qty, price=str(fr.price), price_basis=dec.price_basis, gross_c=fr.gross_c, fee_c=fr.fee_c, filled_qty=filled, sweep_id=sweep)
    if orow.parent_order_id is None and orow.package_id and orow.fill_mode != "AON":
        execution.resize_hedge_children(ctx, oid, filled, primary_ended=(status == "FILLED"))
    return status


def _funds_item(ctx: TxContext, account_id: str, oid: str, severity: str, message: str, details: dict) -> Optional[int]:
    """FUNDS_WAIT: at most ONE open attention item per waiting order (INFO ``ORDER_WAITING_FUNDS`` for an order queued at
    submit, WARN ``ORDER_SUSPENDED`` for a resting order its fill could not fund); a re-suspension after an automatic
    resume adds nothing while the order's item is still open.  Returns the new item's id, None when one was open."""
    q = ",".join("?" for _ in FUNDS_ITEM_CODES)
    if ctx.conn.execute(f"SELECT 1 FROM attention WHERE ref_type='order' AND ref_id=? AND code IN ({q}) AND resolved_ts IS NULL",
                        (oid, *FUNDS_ITEM_CODES)).fetchone() is not None:
        return None
    return ctx.attention(severity, "orders", FUNDS_WAIT_ITEM if severity == "INFO" else "ORDER_SUSPENDED", message,
                         dict(details, account_id=account_id, ref_type="order", ref_id=oid))


def _resolve_funds_items(ctx: TxContext, oid: str) -> None:
    """The order's open waiting / suspension items resolve: it filled, ended or was resumed."""
    q = ",".join("?" for _ in FUNDS_ITEM_CODES)
    ctx.conn.execute(f"UPDATE attention SET resolved_ts=? WHERE ref_type='order' AND ref_id=? AND code IN ({q}) AND resolved_ts IS NULL", (ctx.ts, oid, *FUNDS_ITEM_CODES))


def _suspend(ctx: TxContext, orow: OrderRow, e: RejectError, rec: Optional[QuoteRecord], new_order: bool = False, item: bool = True) -> None:
    """SUSPEND an order whose fill was refused, with the reason, one order event and ONE open attention per order.  A
    funding refusal is a FUNDS_WAIT ("waiting for funds: needs X, available Y"): it reserves nothing and is resumed
    automatically (``plan_funds_resume``); a NEW order is queued with an INFO item, a resting one raises a WARN item - one
    open item per order, never a second while it waits (``item`` False: the package's first leg carries it).  Any other
    refusal (TX-3) keeps its reason and ONE open WARN item (an open one is updated, never duplicated); ``order.resume``
    brings it back once the cause is fixed."""
    conn = ctx.conn
    oid = orow.order_id
    funding = e.code in FUNDING_CODES
    reason = funds_wait_from_error(e) if funding else f"{e.code}: {e.message}"
    conn.execute("UPDATE orders SET status='SUSPENDED', reason=?, first_eval_done=1, last_quote_time=?, updated_ts=? WHERE order_id=?",
                 (reason, rec.quote_time if rec else None, ctx.ts, oid))
    _order_event(conn, oid, ctx.ts, orow.status, "SUSPENDED", (f"fill could not be funded: {e.message}; {reason}: it resumes automatically when cash frees up"
                                                              if funding else f"fill refused: {reason}"), rec)
    ctx.event("ORDER_SUSPENDED", account_id=orow.account_id, instrument_id=orow.instrument_id, ref_type="order", ref_id=oid, reason=reason, code=e.code,
              details=_jsonable(e.details))
    if not item:
        return
    what = f"{oid} {orow.side} {orow.remaining:,} {orow.instrument_id}"
    if funding:
        if new_order:
            _funds_item(ctx, orow.account_id, oid, "INFO", f"{what} queued: {reason}; it executes automatically when cash frees up (cancel it to drop it)",
                        {"code": e.code, "details": _jsonable(e.details)})
        else:
            _funds_item(ctx, orow.account_id, oid, "WARN", f"{what}: {e.message}; {reason}: it resumes automatically when cash frees up (or resume / cancel it)",
                        {"code": e.code, "details": _jsonable(e.details)})
        return
    msg = f"{what} suspended: {e.message}; fix the cause and resume it, or cancel it"
    details = {"account_id": orow.account_id, "ref_type": "order", "ref_id": oid, "code": e.code, "details": _jsonable(e.details)}
    q = ",".join("?" for _ in FUNDS_ITEM_CODES)
    row = conn.execute(f"SELECT id FROM attention WHERE code IN ({q}) AND ref_type='order' AND ref_id=? AND resolved_ts IS NULL ORDER BY id DESC LIMIT 1",
                       (*FUNDS_ITEM_CODES, oid)).fetchone()
    if row is not None:            # the order's one open item (a waiting-for-funds one included) now tells this refusal
        conn.execute("UPDATE attention SET ts=?, severity='WARN', code='ORDER_SUSPENDED', message=?, details_json=? WHERE id=?",
                     (ctx.ts, msg, dumps(details), int(row["id"])))
    else:
        ctx.attention("WARN", "orders", "ORDER_SUSPENDED", msg, details)


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
        # USX-18: the message names the rule that fired with its own threshold; the sweep threshold only when that rule fired
        reason = f.get("confirm_reason") or "large order"
        sweep_c = to_c(f.get("sweep_in") or "0")
        raise ConflictError("CONFIRM_REQUIRED", f"confirm the large order: {reason}",
                            {"sweep_in": f.get("sweep_in") if sweep_c > 0 else None, "sweep_in_c": sweep_c, "threshold": f.get("threshold"),
                             "notional": vo.preview["economics"]["gross"], "nav": f.get("nav"), "reason": reason, "rules": f.get("confirm_rules") or [],
                             "preview": vo.preview})


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
        # a leg waits for its primary anyway: funding is checked at its fill (FUNDS_WAIT there), never a refusal here
        vo = validate(services, req, snapshot=ctx.marks, conn=conn, now=ctx.ts, rest_ok=True, queue_unfunded=True)
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
    wait = vo.funds_wait
    order_id = _insert_order(ctx, vo, ctx.request_id, status="SUSPENDED" if wait else "WORKING", reason=wait["reason"] if wait else None)
    borrow = _open_borrow(ctx, services, vo, order_id)
    package_id = None
    if hedge_legs:
        package_id = create_hedge_children(ctx, services, order_id, hedge_legs, candidate_id or "STRATEGY", candidate_id)
    elif vo.hedge:
        package_id = _attach_hedge(ctx, services, order_id, vo)
    if wait:
        # FUNDS_WAIT: accepted and queued - SUSPENDED, no reservation, one event, one INFO item; resumed when cash frees up
        _order_event(ctx.conn, order_id, ctx.ts, None, "SUSPENDED", f"submitted: {_summary(vo)}; queued, {wait['reason']}: it executes automatically when cash"
                     " frees up", vo.rec)
        ctx.event("ORDER_SUBMITTED", account_id=vo.account_id, instrument_id=vo.inst.instrument_id, ref_type="order", ref_id=order_id, side=vo.side, qty=vo.qty,
                  order_type=vo.order_type, tif=vo.tif, expires_ts=vo.expires_ts, package_id=package_id, limit_px=_s(vo.limit_px), stop_px=_s(vo.stop_px),
                  status="SUSPENDED", reason=wait["reason"])
        _funds_item(ctx, vo.account_id, order_id, "INFO", f"{order_id} {_summary(vo)} queued: {wait['reason']}; it executes automatically when cash frees up"
                    " (cancel it to drop it)", {"code": wait["code"], "details": _jsonable(wait["details"])})
        return {"order": order_view(ctx.conn, order_id), "fills": [], "sweeps": [], "borrow": borrow, "hedge_package": package_id, "warnings": vo.warnings,
                "decision": {"action": "NONE", "reason": "FUNDS_WAIT", "note": wait["reason"]}, "preview": vo.preview, "queued": True}
    _order_event(ctx.conn, order_id, ctx.ts, None, "WORKING", f"submitted: {_summary(vo)}; {vo.exec_reason if not vo.executable else 'quote executable'}", vo.rec)
    ctx.event("ORDER_SUBMITTED", account_id=vo.account_id, instrument_id=vo.inst.instrument_id, ref_type="order", ref_id=order_id, side=vo.side, qty=vo.qty,
              order_type=vo.order_type, tif=vo.tif, expires_ts=vo.expires_ts, package_id=package_id, limit_px=_s(vo.limit_px), stop_px=_s(vo.stop_px))
    orow = load_order(services, ctx.conn, order_id)
    dec = evaluate(orow, vo.rec, vo.session, ctx.ts, orow.participation, vo.inst, bar_used=bar_usage(ctx.conn))
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
    if hasattr(strategies, "ensure_instruments_tx"):
        strategies.ensure_instruments_tx(ctx, services, spec)
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
        vo = validate(services, req, snapshot=ctx.marks, conn=conn, now=ctx.ts, queue_unfunded=True)
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
        vos.append(validate(services, req, snapshot=ctx.marks, conn=conn, now=ctx.ts, rest_ok=True, queue_unfunded=True))
    if any(v.funding.get("confirm_required") for v in vos) and not payload.get("confirm_large"):
        reasons = [f"{v.inst.symbol}: {v.funding.get('confirm_reason')}" for v in vos if v.funding.get("confirm_required")]
        raise ConflictError("CONFIRM_REQUIRED", "confirm the large package: " + "; ".join(reasons),
                            {"legs": [{"instrument_id": v.inst.instrument_id, "funding": v.funding} for v in vos], "reason": "; ".join(reasons),
                             "rules": [r for v in vos for r in (v.funding.get("confirm_rules") or [])]})
    # FUNDS_WAIT: a leg the account cannot fund queues the whole package (every leg SUSPENDED, no reservation, one INFO item)
    waiting = [v for v in vos if v.funds_wait]
    wait = funds_wait_reason(vos[0].inst.currency, sum(int(v.need_c) for v in vos), int(waiting[0].funds_wait["available_c"])) if waiting else None
    status = "SUSPENDED" if wait else "WORKING"
    pid = next_id(conn, "package")
    conn.execute("INSERT INTO hedge_packages(package_id, command_id, account_id, primary_order_id, candidate_id, strategy_key, legs_json, fill_mode, status, created_ts)"
                 " VALUES (?,?,?,NULL,?,?,?,?,?,?)", (pid, ctx.command_id, account_id, key, key, dumps(_jsonable(legs)), "AON", status, ctx.ts))
    oids = []
    for i, vo in enumerate(vos):
        oid = _insert_order(ctx, vo, f"{ctx.request_id}:L{i + 1}", status=status, fill_mode="AON", package_id=pid, reason=wait)
        _order_event(conn, oid, ctx.ts, None, status, f"AON package {pid} ({key}) leg {i + 1}: {_summary(vo)}" + (f"; queued, {wait}" if wait else ""), vo.rec)
        ctx.event("ORDER_SUBMITTED", account_id=account_id, instrument_id=vo.inst.instrument_id, ref_type="order", ref_id=oid, side=vo.side, qty=vo.qty, order_type="MARKET",
                  tif=tif, package_id=pid, fill_mode="AON", **({"status": "SUSPENDED", "reason": wait} if wait else {}))
        oids.append(oid)
    if wait:
        _funds_item(ctx, account_id, oids[0], "INFO", f"AON package {pid} ({key}, {len(oids)} legs) queued: {wait}; it executes automatically when cash frees up"
                    " (cancel it to drop it)", {"code": waiting[0].funds_wait["code"], "package_id": pid, "orders": oids})
        return {"package": {"package_id": pid, "fill_mode": "AON", "strategy_key": key, "status": "SUSPENDED", "legs": legs},
                "orders": [order_view(conn, oid) for oid in oids], "decision": {"action": "NONE", "reason": "FUNDS_WAIT", "note": wait}, "fills": [], "margin": None,
                "sweeps": [], "warnings": [w for v in vos for w in v.warnings], "previews": [v.preview for v in vos], "queued": True}
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
    the option margin once; a funding refusal rolls everything back and suspends the package as a FUNDS_WAIT (a new one
    is queued the same way, with one INFO item)."""
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
        if e.code not in FUNDING_CODES:
            raise
        conn.execute("ROLLBACK TO SAVEPOINT fill_pkg")
        conn.execute("RELEASE SAVEPOINT fill_pkg")
        for i, leg in enumerate(legs):
            _suspend(ctx, leg, e, recs.get(leg.instrument_id), new_order=new_package, item=i == 0)     # one item for the package
        conn.execute("UPDATE hedge_packages SET status='SUSPENDED' WHERE package_id=?", (pid,))
        return {"status": "SUSPENDED", "package_id": pid, "fills": [], "margin": None, "sweeps": [], "reason": funds_wait_from_error(e)}
    conn.execute("UPDATE hedge_packages SET status='FILLED' WHERE package_id=?", (pid,))
    ctx.event("PACKAGE_FILLED", account_id=legs[0].account_id, ref_type="package", ref_id=pid, fills=[f.fill_id for f in fills], quote_time=qt,
              margin_required_c=None if margin is None else margin.get("required_c"))
    return {"status": "FILLED", "package_id": pid, "fills": [f.to_dict() for f in fills], "margin": margin, "sweeps": _sweeps_for(conn, [f.fill_id for f in fills]),
            "quote_time": qt}


def h_submit(ctx: TxContext, payload: dict) -> dict:
    services = _services(ctx)
    if isinstance(payload.get("package"), dict):
        return _submit_package(ctx, services, payload)
    vo = validate(services, payload, snapshot=ctx.marks, conn=ctx.conn, now=ctx.ts, queue_unfunded=True)     # funding alone queues (FUNDS_WAIT); PRIVATE / BOND / FX / OTC: USE_OWN_TICKET there
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
    session = session_for(ctx.sessions, orow.session_group, rec, ctx.ts)          # C15: a FOREIGN venue's session from the record
    dec = evaluate(orow, rec, session, ctx.ts, orow.participation, inst, bar_used=bar_usage(ctx.conn))
    fr = _act(ctx, services, orow, rec, dec, new_order=False)
    out = {"order_id": oid, "action": dec.action, "reason": dec.reason, "decision": dec.to_dict(), "fill": None if fr is None else fr.to_dict(),
           "sweeps": _sweeps_for(ctx.conn, [fr.fill_id] if fr else []), "order": order_view(ctx.conn, oid)}
    if dec.action == "FILL" and fr is None:
        if out["order"]["status"] == "SUSPENDED":
            out["action"], out["reason"] = "SUSPENDED", (out["order"].get("reason") or "SUSPENDED")
        else:
            out["action"], out["reason"] = "NONE", "ALREADY_APPLIED"
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
    _resolve_funds_items(ctx, oid)                       # a cancelled / expired order waits for nothing any more
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
    _resolve_funds_items(ctx, oid)
    return {"order": order_view(ctx.conn, oid)}


# ---- FUNDS_WAIT: the automatic resume (order.resume_funded, actor matcher) -------------------------------------------------------------
def _resume_backoff_left(conn, order_id: str, now: int) -> int:
    """Seconds before a waiting order may be resumed automatically again: 0 when it never was; after n automatic resumes
    that its fill could not fund, 60 s x 2^(n-1) capped at 30 min from its last suspension (a fill-time measure stricter
    than the preview - settled cash for collateral and margin - cannot make it churn)."""
    n = int(conn.execute("SELECT COUNT(*) FROM order_events WHERE order_id=? AND note LIKE ?", (order_id, AUTO_RESUME_NOTE + "%")).fetchone()[0] or 0)
    if n <= 0:
        return 0
    last = int(conn.execute("SELECT COALESCE(MAX(ts), 0) FROM order_events WHERE order_id=? AND to_status='SUSPENDED'", (order_id,)).fetchone()[0] or 0)
    wait = min(FUNDS_RESUME_BACKOFF_S << min(n - 1, 10), FUNDS_RESUME_BACKOFF_MAX_S)
    return max(0, last + wait - int(now))


def plan_funds_resume(services, conn, snapshot, now: int) -> List[dict]:
    """FUNDS_WAIT: which waiting orders fit now.  Every SUSPENDED order whose reason is a funding wait, oldest first (an
    AON package as one unit, a primary with its WAITING_PARENT hedge legs), is re-checked exactly like the preview - its
    need at the current observation (``orders_preview.resting_need``), then own available (settled-now cash for a
    futures order, USX-17) -> Treasury sweep -> PB headroom (``funding_fit`` on ``accounting.buying_power`` at its settle
    date) - against a RUNNING budget: own cash per (account, currency), Treasury per (lifetime, currency), PB headroom per
    account, each reduced by what the orders planned before it claim, so two orders never claim the same cash.  Read-only
    (the matcher plans on a reader; ``order.resume_funded`` plans again inside its transaction and applies).  An order that
    cannot be priced, is in its back-off, belongs to a closed / held lifetime or an account with an open FUNDING_SHORTFALL
    keeps waiting.  Returns ``[{order_ids, account_id, package_id, currency, need_c, available_c}]``."""
    rows = conn.execute("SELECT * FROM orders WHERE status='SUSPENDED' AND reason LIKE ? ORDER BY created_ts, order_id", (FUNDS_WAIT + "%",)).fetchall()
    if not rows:
        return []
    acc = services.accounting
    units, seen = [], set()
    for r in rows:
        if r["package_id"] and r["fill_mode"] == "AON":
            if r["package_id"] in seen:
                continue
            seen.add(r["package_id"])
            legs = conn.execute("SELECT * FROM orders WHERE package_id=? AND fill_mode='AON' ORDER BY order_id", (r["package_id"],)).fetchall()
            if all(l["status"] == "SUSPENDED" and is_funds_wait(l["reason"]) for l in legs):
                units.append((r, list(legs), r["package_id"]))
            continue
        kids = conn.execute("SELECT * FROM orders WHERE parent_order_id=? AND status='WAITING_PARENT' ORDER BY order_id", (r["order_id"],)).fetchall()
        units.append((r, [r] + list(kids), None))
    budgets: Dict[tuple, object] = {}
    own_used: Dict[tuple, int] = {}
    tre_used: Dict[tuple, int] = {}
    pb_used: Dict[str, int] = {}
    out: List[dict] = []
    for first, members, pkg in units:
        aid = first["account_id"]
        acct = account_row(conn, aid)
        if acct is None or acct["status"] != "OPEN":
            continue
        acct = dict(acct)
        lid = acct["lifetime_id"]
        lrow = lifetime_row(conn, lid)
        if lrow is None or lrow["status"] != "OPEN" or (hasattr(acc, "is_held") and acc.is_held(lid)):
            continue
        if _resume_backoff_left(conn, first["order_id"], now) > 0:
            continue
        need, ccy, sdate, priced, fut = 0, None, None, True, False
        for m in members:
            inst = services.instruments.get(m["instrument_id"], conn)
            if inst is None or inst.status != "ACTIVE":
                priced = False
                break
            n = resting_need(services, conn, snapshot, now, acct, inst, OrderRow.from_row(m, inst))
            if n is None:
                if m["order_id"] == first["order_id"] or pkg:
                    priced = False
                    break
                continue                                    # an unpriced hedge leg is checked at its own fill
            if ccy is None:
                ccy, sdate, fut = n["currency"], n["settle_date"], inst.kind == "FUTURE"
            if n["currency"] == ccy:
                need += n["need_c"]
        if not priced or ccy is None:
            continue
        if conn.execute("SELECT 1 FROM attention WHERE code='FUNDING_SHORTFALL' AND account_id=? AND resolved_ts IS NULL AND details_json LIKE ?",
                        (aid, f'%"currency":"{ccy}"%')).fetchone() is not None:
            continue
        key = (aid, ccy, sdate)
        if key not in budgets:
            budgets[key] = acc.buying_power(aid, ccy, sdate, snapshot=snapshot)
        bp = budgets[key]
        own_now = int(getattr(bp, "immediate_c", 0) or 0) if fut else int(bp.available_c)       # the preview's own figure (USX-17)
        own = max(0, own_now - own_used.get((aid, ccy), 0))
        tre = max(0, int(bp.treasury_available_c) - tre_used.get((lid, ccy), 0))
        pbh = max(0, int(bp.pb_headroom_c) - pb_used.get(aid, 0))
        fit = funding_fit(acct, need, own, tre, pbh)
        if fit["unfunded"] > 0:
            continue
        own_used[(aid, ccy)] = own_used.get((aid, ccy), 0) + (need - fit["shortfall"])
        tre_used[(lid, ccy)] = tre_used.get((lid, ccy), 0) + fit["sweep_in"]
        pb_used[aid] = pb_used.get(aid, 0) + fit["pb_fin"]
        ids = [m["order_id"] for m in members if m["status"] == "SUSPENDED"]
        out.append({"order_ids": ids, "account_id": aid, "package_id": pkg, "currency": ccy, "need_c": need,
                    "available_c": own + tre + (pbh if (acct.get("kind") == "PB" and int(acct.get("pb_financing") or 0)) else 0)})
    return out


def h_resume_funded(ctx: TxContext, payload: dict) -> dict:
    """The matcher's FUNDS_WAIT command: plan again inside the transaction (``plan_funds_resume``) and resume what fits -
    SUSPENDED -> WORKING (PARTIAL when part-filled), reason cleared, order event "resumed: funds available (needs X,
    available Y)", ORDER_RESUMED (auto); an AON package's legs together.  The matcher fills them by the normal rules."""
    services = _services(ctx)
    plan = plan_funds_resume(services, ctx.conn, ctx.marks, ctx.ts)
    resumed = []
    for p in plan:
        note = f"{AUTO_RESUME_NOTE} (needs {p['currency']} {fmt_money(p['need_c'])}, available {p['currency']} {fmt_money(p['available_c'])})"
        for oid in p["order_ids"]:
            raw = _order_raw(ctx.conn, oid)
            if raw is None or raw["status"] != "SUSPENDED":
                continue
            to = "PARTIAL" if int(raw["filled_qty"]) > 0 else "WORKING"
            ctx.conn.execute("UPDATE orders SET status=?, reason=NULL, updated_ts=? WHERE order_id=?", (to, ctx.ts, oid))
            _order_event(ctx.conn, oid, ctx.ts, "SUSPENDED", to, note)
            ctx.event("ORDER_RESUMED", account_id=raw["account_id"], instrument_id=raw["instrument_id"], ref_type="order", ref_id=oid, auto=True,
                      need_c=p["need_c"], available_c=p["available_c"], currency=p["currency"])
            resumed.append({"order_id": oid, "status": to, "need_c": p["need_c"], "available_c": p["available_c"]})
        if p.get("package_id"):
            ctx.conn.execute("UPDATE hedge_packages SET status='WORKING' WHERE package_id=? AND status='SUSPENDED'", (p["package_id"],))
    return {"resumed": resumed, "planned": [p["order_ids"] for p in plan]}


def h_record_rejection(ctx: TxContext, payload: dict) -> dict:
    """Light: the audit row of a refused submission (handoff §7: rejected orders are preserved)."""
    req = payload.get("request") or {}
    cur = ctx.conn.execute("INSERT INTO order_rejections(ts, request_id, account_id, instrument_id, side, qty, order_type, code, message, payload_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (ctx.ts, str(payload.get("request_id") or ctx.request_id), req.get("account_id"), req.get("instrument_id"), req.get("side"),
                            req.get("qty") if isinstance(req.get("qty"), int) else None, req.get("order_type"), str(payload.get("code") or "REJECTED"),
                            str(payload.get("message") or ""), dumps(_jsonable(req))))
    return {"id": int(cur.lastrowid)}


def rejection_rid(request_id: str, code: str, message: str) -> str:
    """TX-7: the audit command's id is per ATTEMPT - ``rej:<request_id>:<sha8(code|message)>`` - so a retry of the same request id
    refused for another reason is recorded too (the same refusal again is a replay, not a second row); a request id too long
    for the 80-character rule is hashed: ``rej:h<sha24(request_id)>:<sha8>``."""
    tail = hashlib.sha256(f"{code}|{message}".encode("utf-8")).hexdigest()[:8]
    base = f"rej:{request_id}"
    if len(base) + 9 > 80:
        base = "rej:h" + hashlib.sha256(str(request_id).encode("utf-8")).hexdigest()[:24]
    return f"{base}:{tail}"


def record_rejection(services, request_id: str, req: dict, code: str, message: str):
    """Record a refused submission through the bus (the API calls this after a 4xx from ``order.submit``); a failed audit write
    is logged (TX-7), never raised over the refusal itself."""
    rid = rejection_rid(request_id, code, message)
    res = services.bus.execute(Command("order.record_rejection", {"request_id": request_id, "request": _jsonable(req), "code": code, "message": message}, rid, "ui"))
    if not res.ok:
        log = getattr(services, "log", None) or getattr(services.bus, "log", None)
        if log is not None:
            log.warning("the rejection audit of %s (%s) was not recorded: %s %s", request_id, code, res.code, res.message)
    return res


def register(bus) -> None:
    bus.register("order.submit", h_submit)
    bus.register("order.cancel", h_cancel)
    bus.register("order.match", h_match)
    bus.register("order.match_package", h_match_package)
    bus.register("order.expire", h_expire)
    bus.register("order.resume", h_resume)
    bus.register("order.resume_funded", h_resume_funded)
    bus.register("order.record_rejection", h_record_rejection, light=True)
