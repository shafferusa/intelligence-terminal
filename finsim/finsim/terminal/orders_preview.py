"""Order validation and the preview (TERMINAL_DESIGN.md §10.2, §10.3; sentences STALE, SESSION, SWEEP, AVAILABLE).
``validate()`` raises the §10.2 codes (``RejectError`` 400 / ``ConflictError`` 409) and returns a ``ValidatedOrder`` that
carries the §10.3 preview it was computed from; ``preview()`` turns the rejections into ``blockers``; ``PreviewStore``
keeps the last 200 previews for 120 s so ``order.submit`` can detect a stale preview (PREVIEW_STALE).  The conventions
are stated in ``orders.py`` (which re-exports this module) and in docs/TERMINAL_EXECUTION.md.

Build 2 (2026-10-21): FX pairs, bonds, private companies, swaps and OTC contracts are refused HERE with the pointer to their own
ticket (C15, ``USE_OWN_TICKET``); a FOREIGN listing trades in its venue session read from the quote record (``session_for``); a
non-USD need the account and Treasury cannot cover in that currency carries ``funding.fx_conversion`` (``services.fx.conversion_plan``)
and, with ``fx_convert: true``, the fill converts USD first; a picked hedge's legs are in the funding need (USX-17) and a hedge booked
on the OTC desk is refused here (PO-10); the trade / settle dates are those of the first session the order can trade in (EXEC-8);
stops and trailing stops read ``orders_rules.trigger_price`` (EXEC-1); the confirm names the rule that fired (USX-18) and the sweep
rule is off unless the owner set it (SR-12); a never-observed quote is NO_QUOTE 'no quote yet - fetching' (USX-08); GTC expires at
the close of the 90th day's session (USX-31); one shared reader for the whole validation (ORC-5).

FUNDS_WAIT (hotfix 1.0.3.1, 2026-10-21, ported to 1.0.4): with ``queue_unfunded`` (the preview and ``order.submit``) funding is never
a blocker - the preview stays ok with the warning "will queue: waiting for funds (needs X, available Y); it executes automatically when
cash frees up" and ``ValidatedOrder.funds_wait`` tells the submit to queue the order; ``expected_price``, ``cash_need_c``,
``funding_fit`` and ``resting_need`` are the preview's own rules, shared with the automatic resume (``orders.plan_funds_resume``).
SHORT_COLLATERAL (same hotfix): a SELL_SHORT needs only the 2 % excess + fee - the 102 % collateral is posted at the fill and funded
first by the sale's own proceeds (``cash_need_c``).
"""
from __future__ import annotations

import threading
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import config, execution, tables
from .books import account_kind_for, account_row, lifetime_row
from .clock import epoch_of, ny_of, trade_date as _trade_date
from .commands import ConflictError, RejectError
from .db import get_setting
from .instruments import Instrument
from .ledger import position as _position
from .marks import shared_reader
from .orders_rules import (BUY_SIDES, CASH_SIDES, FUNDS_WAIT, FUTURES_SIDES, NEEDS_QUOTE_TYPES, OPENING_SIDES, OPTION_LAST_OPEN_ET, OPTION_SIDES,
                           ORDER_TYPES, SELL_SIDES, TIFS, OrderRow, evaluate, exec_price, fill_cap, funds_wait_reason, on_tick, trail_stop, trigger_price)
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
    """§10.5 step 5 for equities: when a fresh chain for the underlying is already cached (``services.chains.underlying_quote``,
    EXEC-7: the Cboe underlying bid / ask of a snapshot fetched within the chain interval) its bid/ask ride on a copy of the record,
    so a buy fills at the observed ask and a sell at the bid, as long as that observation is not older than the record's own print;
    nothing is fetched for this."""
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
    # the NBBO rides only when it is not OLDER than the print it would replace: a chain fetched before the last trade the record
    # observed (the market moved since) never prices a fill - the newer observation wins (the 2026-10-21 P05 UI walk: a stop-limit
    # triggered by a 758.50 print would otherwise have filled at the chain's earlier bid 768.61)
    if rec.quote_time is not None and (u.quote_time is None or int(u.quote_time) < int(rec.quote_time)):
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


def _futures_effect(inst: Instrument, side: str, qty: int, pos_qty: int, expected: Optional[Decimal], fee_c: int, working_sells: int, working_buys: int,
                    pv: dict, warnings: List[str]):
    """FUT_MARGIN for one futures order, judged against the account's CURRENT position exactly as ``futures.apply_fill``
    books the fill: the part opposite to the position closes first (its initial margin is RELEASED), the rest opens (its
    initial margin is POSTED); the fill nets the fee with the margin move, so a closing order needs no funding for its fee
    and never sweeps.  Only the opening contracts count toward the large-notional confirm (closing adds no exposure).
    Writes ``pv["margin"]``: ``initial`` = the margin posted for the opening contracts, ``released``, ``im_change`` (signed),
    ``initial_after`` / ``maintenance`` = after the trade, the position before / after, the closing / opening split and the
    effect (OPEN / INCREASE / REDUCE / CLOSE / FLIP).  Returns ``(need_c, need_formula, confirm_notional_c)``."""
    im = int((inst.spec or {}).get("im_c") or 0)
    mm = int((inst.spec or {}).get("mm_c") or 0)
    signed = qty if side in BUY_SIDES else -qty
    pos_after = pos_qty + signed
    closing = min(qty, abs(pos_qty)) if pos_qty and (pos_qty > 0) != (signed > 0) else 0
    opening = qty - closing
    im_delta = im * (abs(pos_after) - abs(pos_qty))
    need_c = max(0, im_delta + fee_c)
    held = "long" if pos_qty > 0 else "short"
    new_dir = "long" if signed > 0 else "short"
    move = f"{abs(pos_qty):,} -> {abs(pos_after):,} contracts x {fmt_money(im)}"
    if opening == 0:
        effect = "CLOSE" if pos_after == 0 else "REDUCE"
        detail = (f"CLOSING {closing:,} of the {held} {abs(pos_qty):,} {inst.symbol} -> position after {pos_after:,}: initial margin {fmt_money(im * closing)} RELEASED"
                  f" ({move}); nothing new to post")
        formula = (f"closing: fee {fmt_money(fee_c)} - initial margin released {fmt_money(im * closing)} (the fill nets them; FUT_MARGIN)"
                   + (": nothing to fund" if need_c == 0 else ""))
    elif closing == 0:
        effect = "OPEN" if pos_qty == 0 else "INCREASE"
        detail = f"OPENING {opening:,} {new_dir} {inst.symbol} -> position after {pos_after:,}: initial margin {fmt_money(im * opening)} posted ({move})"
        formula = "initial margin x contracts + fee (FUT_MARGIN)"
    else:
        effect = "FLIP"
        detail = (f"closes the {held} {closing:,} {inst.symbol} (initial margin {fmt_money(im * closing)} RELEASED) and opens {opening:,} {new_dir}"
                  f" (initial margin {fmt_money(im * opening)} posted) -> position after {pos_after:,}: net margin {'+' if im_delta >= 0 else ''}{fmt_money(im_delta)} ({move})")
        formula = "initial margin posted for the opening contracts - margin released by the closing ones + fee (the fill nets them; FUT_MARGIN)"
    same = working_sells if signed < 0 else working_buys
    if closing and same:
        warnings.append(f"a working {'SELL' if signed < 0 else 'BUY'} of {same:,} {inst.symbol} also reduces the {held} {abs(pos_qty):,}: whichever fills first closes it;"
                        f" a later fill beyond the position opens a {new_dir} and posts its initial margin then (cancel the working order if this one replaces it)")
    pv["margin"] = {"initial": _m(im * opening), "released": _m(im * closing), "im_change": _m(im_delta), "initial_after": _m(im * abs(pos_after)),
                    "maintenance": _m(mm * abs(pos_after)), "per_contract": _m(im), "effect": effect, "position_before": pos_qty, "position_after": pos_after,
                    "closing": closing, "opening": opening, "detail": detail, "note": tables.CONVENTIONS.get("FUT_MARGIN", "")}
    confirm_notional_c = 0 if (opening == 0 or expected is None) else inst.contract_value_c(expected, opening)
    return need_c, formula, confirm_notional_c


# ---- Build 2 helpers: own tickets, sessions, dates, fees, hedges, FX conversion, confirm rules ---------------------------------------
#: C15 - kinds that never go through the order ticket: the pointer sentence to their own ticket (trader wording, shown as is)
OWN_TICKET = {
    "FX": "a currency pair: deal it on the FX desk of the Foreign Markets & FX account (spot, forwards and crosses), not with the order ticket",
    "BOND": "a bond: trade it with the bond ticket in Debt Instruments (clean price, yield and accrued interest), not with the order ticket",
    "PRIVATE": "a private company: trade it with the private-markets ticket (negotiated price, placement fee, settlement lag and lock-up), not with the order ticket",
    "SWAP": "an OTC contract: book it on the OTC desk of a prime-broker account, not with the order ticket",
}
OTC_TICKET_KINDS = ("OTC", "IRS", "TRS", "XCCY", "EQ_OPTION", "FX_OPTION", "EQ_FORWARD", "FX_FORWARD", "OTC_OPTION", "OTC_FORWARD")
NO_QUOTE_NOTE = "no quote yet - fetching"
OLD_SEED_SWEEP_THRESHOLD_C = 2_000_000_00          # the seed of databases created before decision s7.11 was applied (SR-12)


def is_foreign_group(group) -> bool:
    g = str(group or "")
    return g == "FOREIGN" or g.startswith("FOREIGN:")


def session_for(sessions, group: str, rec: Optional[QuoteRecord], now: int):
    """C15: the session an order trades in - the session table for US / CME / FX groups; a FOREIGN listing's venue session from
    its quote record's provider session (Yahoo ``currentTradingPeriod``, as ``QuoteCache.session_for`` does).  None when the
    group is unknown."""
    if sessions is None:
        return None
    try:
        if is_foreign_group(group):
            ps = getattr(rec, "provider_session", None) if rec is not None else None
            return sessions.state(group, now, foreign={"open_ts": int(ps[0]), "close_ts": int(ps[1]), "tz": getattr(rec, "exchange_tz", None)} if ps else None)
        return sessions.state(group, now)
    except KeyError:
        return None


def foreign_next_session(rec: Optional[QuoteRecord], now: int) -> Optional[Tuple[int, int]]:
    """A FOREIGN venue's next ``(open_ts, close_ts)``: today's provider session while it has not ended, else the same hours on the
    next weekday - an estimate (Yahoo publishes only the current trading period; venue holidays are not modelled)."""
    ps = getattr(rec, "provider_session", None) if rec is not None else None
    if not ps:
        return None
    o, c = int(ps[0]), int(ps[1])
    if c > int(now):
        return o, c
    k = 1
    while k < 10:
        o2, c2 = o + 86400 * k, c + 86400 * k
        if c2 > int(now) and ny_of(o2).weekday() < 5:
            return o2, c2
        k += 1
    return None


def first_trade_date(services, inst: Instrument, session, rec: Optional[QuoteRecord], now: int) -> date:
    """EXEC-8: the trade date of the first session the order can trade in - the open session's own trade date, else the next
    session's (Thanksgiving 10:00 -> Fri 11-27; Wed 17:00 -> Thu; a CME evening session -> the next business day)."""
    if session is not None and session.open and session.trade_date:
        return date.fromisoformat(str(session.trade_date))
    group = inst.session_group
    if is_foreign_group(group):
        nxt = foreign_next_session(rec, now)
        if nxt is not None:
            return ny_of(nxt[0]).date()
    else:
        try:
            nxt = services.sessions.next_session(group, now)
        except (KeyError, ValueError):
            nxt = None
        if nxt is not None:
            return nxt.trade_date
    return _trade_date(ny_of(now), group, services.cal)


def next_open(services, inst: Instrument, session, rec: Optional[QuoteRecord], now: int) -> Optional[int]:
    if session is not None and session.next_open:
        return int(session.next_open)
    if is_foreign_group(inst.session_group):
        nxt = foreign_next_session(rec, now)
        return None if nxt is None else (nxt[0] if nxt[0] > now else None)
    try:
        nxt = services.sessions.next_session(inst.session_group, now)
    except (KeyError, ValueError):
        return None
    return None if nxt is None or nxt.open_ts <= now else int(nxt.open_ts)


def gtc_expiry(services, inst: Instrument, rec: Optional[QuoteRecord], now: int) -> int:
    """USX-31: a GTC order expires at the close of the session of the 90th calendar day (``config.GTC_DAYS``) - the last session
    closing on or before that day when it has none (weekend, holiday) - in New York wall time, so a DST change never shifts it.
    A FOREIGN listing: the venue's closing time (from its provider session) on that day."""
    day = ny_of(now).date() + timedelta(days=int(config.GTC_DAYS))
    if not is_foreign_group(inst.session_group):
        for back in range(0, 10):
            try:
                close = services.sessions.session_close(inst.session_group, day - timedelta(days=back))
            except KeyError:
                close = None
            if close is not None:
                return int(close)
    ps = getattr(rec, "provider_session", None) if rec is not None else None
    hhmm = ny_of(int(ps[1])).strftime("%H:%M") if ps else "16:00"
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return epoch_of(day, hhmm)


def order_expiry(services, inst: Instrument, tif: str, session, rec: Optional[QuoteRecord], now: int) -> int:
    """DAY: the close of the session the order can first trade in; GTC: ``gtc_expiry``.  ValueError when no session is known."""
    if tif != "DAY":
        return gtc_expiry(services, inst, rec, now)
    if is_foreign_group(inst.session_group):
        if session is not None and session.open and session.closes_at:
            return int(session.closes_at)
        nxt = foreign_next_session(rec, now)
        if nxt is None:
            raise ValueError(f"no session known for {inst.session_group} (no quote with the venue's trading period yet)")
        return int(nxt[1])
    return int(services.sessions.day_order_expiry(inst.session_group, now))


def _fees(services, acct: dict, inst: Instrument, side: str, qty: int, gross_c: Optional[int]) -> Tuple[int, str, list]:
    """The fee of one order and its lines (C25): ``execution.fee_breakdown(services, acct, inst, side, qty, gross_c) -> (total_c,
    basis, lines)`` when the money package provides it (a foreign venue's fee is its own line), else ``execution.fee_for`` (one
    commission line).  The same functions price the fill, so the preview's fee is the fill's."""
    fb = getattr(execution, "fee_breakdown", None)
    if callable(fb):
        got = fb(services, acct, inst, side, qty, gross_c)
        total, basis = int(got[0]), str(got[1])
        lines = [dict(l) for l in (got[2] if len(got) > 2 else [])] or [{"label": "commission", "amount": _m(total), "basis": basis}]
        for l in lines:
            if "amount_c" in l and "amount" not in l:
                l["amount"] = _m(int(l.pop("amount_c")))
        return total, basis, lines
    got = execution.fee_for(services, acct, inst, qty, gross_c)
    total, basis = int(got[0]), str(got[1])
    lines = [dict(l) for l in got[2]] if len(got) > 2 and got[2] else [{"label": "commission", "amount": _m(total), "basis": basis}]
    return total, basis, lines


def _hedge_need(services, block: dict, hedge: Optional[dict], primary_qty: int, warnings: List[str]) -> Tuple[int, Optional[dict]]:
    """USX-17 / PO-10: the cash the picked hedge's legs need - option premium paid at the leg's observed ask, fees, futures initial
    margin; sells add nothing (their proceeds settle later) - sized like ``hedges.package`` sizes them (a ``ratio`` makes every leg
    floor(primary qty x ratio)); and the refusal of a candidate that is not an order leg (booked on the OTC desk) or is not
    available - here at the preview, not at submit.  Explicit ``legs`` are priced from the cached quotes."""
    if not hedge:
        return 0, None
    cid = str(hedge.get("candidate_id") or "")
    ratio = None
    if hedge.get("ratio") not in (None, ""):
        try:
            ratio = Decimal(str(hedge.get("ratio")))
        except Exception:   # noqa: BLE001 - hedges.package refuses a bad ratio at submit; the preview prices the default legs
            ratio = None
    legs: List[dict] = []
    if isinstance(hedge.get("legs"), list) and hedge.get("legs"):
        for l in hedge["legs"]:
            if not isinstance(l, dict):
                continue
            iid = str(l.get("instrument_id") or "")
            inst = services.instruments.get(iid) if iid else None
            rec = quote_of(services, iid) if iid else None
            side = str(l.get("side") or "").upper()
            px = None
            if rec is not None:
                px = rec.ask if side in BUY_SIDES and rec.ask is not None and rec.ask > 0 else rec.last
            legs.append({"instrument_id": iid, "side": side, "qty": int(l.get("qty") or 0), "kind": getattr(inst, "kind", None) or "OPTION",
                         "est_price": None if px is None else str(px), "fee": None, "margin": None,
                         "multiplier": str(getattr(inst, "multiplier", None) or (100 if parse_occ_like(iid) else 1))})
    else:
        cands = (block or {}).get("candidates") or []
        cand = next((c for c in cands if isinstance(c, dict) and c.get("id") == cid), None)
        if cand is None:
            if (block or {}).get("status") not in (None, "ready"):
                warnings.append(f"hedge {cid}: its legs are priced when the option chain is cached ({(block or {}).get('note') or (block or {}).get('status')});"
                                " their cash is not in this funding plan yet")
                return 0, {"candidate_id": cid, "need": None, "note": "legs not priced yet"}
            raise RejectError("NOT_AVAILABLE", f"hedge {cid!r} is not offered for this order", {"candidate_id": cid})
        if (cand.get("submit_via") or "order") != "order":
            raise RejectError("NOT_AVAILABLE", f"{cand.get('label') or cid} is booked on the OTC desk of the prime-broker account, not as an order leg: place this"
                              " order without it, then book the swap on the OTC desk", {"candidate_id": cid, "submit_via": cand.get("submit_via")})
        if cand.get("available") is False:
            raise RejectError("NOT_AVAILABLE", f"{cand.get('label') or cid}: {cand.get('reason') or 'not available'}", {"candidate_id": cid})
        legs = [dict(l) for l in (cand.get("legs") or []) if isinstance(l, dict)]
    need = Decimal(0)
    premium = fees = im = Decimal(0)
    unpriced = []
    for l in legs:
        q0 = int(l.get("qty") or 0)
        q = int((Decimal(int(primary_qty)) * ratio).to_integral_value(rounding="ROUND_FLOOR")) if ratio is not None else q0
        if q <= 0:
            continue
        kind = str(l.get("kind") or "OPTION")
        side = str(l.get("side") or "").upper()
        m = l.get("margin") if isinstance(l.get("margin"), dict) else None
        if kind == "FUTURE":
            per = (m or {}).get("per_contract")
            if per not in (None, ""):
                im += Decimal(str(per)) * q
        elif side in BUY_SIDES:
            if l.get("est_price") in (None, ""):
                unpriced.append(l.get("instrument_id") or l.get("label") or "?")
                continue
            mult = Decimal(str(l.get("multiplier") or (100 if kind == "OPTION" else 1)))
            premium += Decimal(str(l["est_price"])) * q * mult
        if l.get("fee") not in (None, "") and q0 > 0:
            fees += Decimal(str(l["fee"])) * q / q0
    need = premium + fees + im
    need_c = to_c(need)
    if unpriced:
        warnings.append(f"hedge legs without an observed price ({', '.join(str(u) for u in unpriced)}): their cash is not in this funding plan")
    return need_c, {"candidate_id": cid or None, "need": _m(need_c), "premium": _m(to_c(premium)), "fees": _m(to_c(fees)),
                    "initial_margin": _m(to_c(im)) if im else None, "unpriced": unpriced or None,
                    "note": "estimated from the hedge legs' observed prices; the legs fill after the primary"}


def parse_occ_like(iid: str) -> bool:
    """An OCC option symbol (ROOT + yymmdd + C/P + strike x 1000): its multiplier is 100."""
    import re as _re
    return bool(_re.match(r"^[A-Z][A-Z0-9.\-]{0,5}\d{6}[CP]\d{8}$", str(iid or "")))


def _conversion_plan(services, snapshot, account_id: str, ccy: str, need_c: int, settle_date: date, conn) -> Optional[dict]:
    fn = getattr(getattr(services, "fx", None), "conversion_plan", None)
    if not callable(fn):
        return None
    try:
        plan = fn(services, snapshot, account_id, ccy, int(need_c), settle_date, conn=conn)
    except Exception as e:   # noqa: BLE001 - a failing plan is disclosed, never a crash of the preview
        return {"executable": False, "reason": f"conversion unavailable: {type(e).__name__}: {e}"[:200]}
    return None if plan is None else _jsonable(dict(plan))


def _plan_usd_c(plan: Optional[dict]) -> Optional[int]:
    """The USD cents a conversion plan needs (C17 keys: usd_c / usd_amount_c / usd_amount / usd)."""
    if not plan:
        return None
    for k in ("usd_c", "usd_amount_c"):
        if plan.get(k) is not None:
            try:
                return int(plan[k])
            except (TypeError, ValueError):
                pass
    for k in ("usd_amount", "usd"):
        if plan.get(k) not in (None, ""):
            try:
                return to_c(Decimal(str(plan[k])))
            except Exception:   # noqa: BLE001
                pass
    return None


def sweep_confirm_threshold_c(conn) -> int:
    """SR-12 (decision s7.11): Treasury sweep-ins are automatic at any size, so the optional fat-finger confirm on a large sweep is
    OFF (0) unless the owner set ``settings.sweep_confirm_threshold_c`` himself (a SETTINGS_CHANGED event names the key): databases
    seeded before the decision carry the old seed 2,000,000.00 that nobody chose.  The 25 % of NAV notional confirm is separate."""
    try:
        v = int(get_setting(conn, "sweep_confirm_threshold_c", 0) or 0)
    except (TypeError, ValueError):
        return 0
    if v <= 0:
        return 0
    if v == OLD_SEED_SWEEP_THRESHOLD_C:
        row = conn.execute("SELECT 1 FROM events WHERE type='SETTINGS_CHANGED' AND (',' || COALESCE(ref_id, '') || ',') LIKE '%,sweep_confirm_threshold_c,%' LIMIT 1").fetchone()
        if row is None:
            return 0
    return v


def _quote_note(rec: Optional[QuoteRecord], reason: str) -> str:
    """The plain reason a quote does not execute (the ticket's blocker text)."""
    if rec is None:
        return NO_QUOTE_NOTE
    if reason == "NO_QUOTE":                   # a record without a price: the last attempt failed (an unknown symbol, a provider error)
        return f"no quote: the last fetch failed ({rec.error})" if rec.error else NO_QUOTE_NOTE
    if getattr(rec, "restored", False) and reason == "STALE":
        return "the last print was observed before the restart; executions wait for a fresh observation"
    if rec.error and reason == "STALE":
        good = ny_of(int(rec.fetched_at)).strftime("%H:%M:%S ET") if rec.fetched_at else "never"
        return f"the last fetch failed ({rec.error}); last good observation {good}; executions pause until a fresh one"
    return {"STALE": "the last observed print is too old to execute against", "SESSION_CLOSED": "the session is closed",
            "QUOTE_FROM_PREVIOUS_SESSION": "the last print is from the previous session", "NO_NBBO": "no usable Cboe bid / ask",
            "COMPUTED_NOT_EXECUTABLE": "a computed mark never executes", "PROVIDER_SESSION_ENDED": "the venue's session has ended"}.get(reason, "")


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
    funds_wait: Optional[dict] = None       # FUNDS_WAIT: {code, message, reason, details, need_c, available_c} when funding is the only blocker


def validate(services, req: dict, *, snapshot=None, conn=None, now: Optional[int] = None, rest_ok: bool = False,
             partial: Optional[dict] = None, queue_unfunded: bool = False) -> ValidatedOrder:
    """§10.2: every rejection is a ``RejectError`` (400) or ``ConflictError`` (409) with the code of the table; a closed
    session is never a rejection.  ``rest_ok`` lifts the QUOTE_STALE rule (package legs rest until a fresh observation).
    ``partial`` (a dict) receives the preview built so far, so a blocked preview still shows the instrument, quote,
    session and funding figures.  Reads go through ``marks.shared_reader`` (ORC-5): ONE connection for the whole
    validation - accounting, compliance, risk, hedges and prime nested inside reuse it instead of taking more pooled readers.
    ``queue_unfunded`` (the preview and ``order.submit``; FUNDS_WAIT): an order whose only blocker is funding is not refused -
    ``funds_wait`` carries the refusal (code, message, the reason "waiting for funds: needs X, available Y") and the preview
    warns that it will queue; every other blocker still raises."""
    with shared_reader(services.db, conn) as c:
        return _validate(services, req, snapshot, c, now, rest_ok, partial, queue_unfunded)


def expected_price(ok: bool, px_exec: Optional[Decimal], basis: str, order_type: str, limit_px: Optional[Decimal], stop_px: Optional[Decimal],
                   ref_last: Optional[Decimal]):
    """The preview's expected price and its basis (sentence SWEEP): the executable price when the quote is executable,
    else the limit (LIMIT / STOP_LIMIT), the stop (STOP / TRAILING_STOP) or the last / mark."""
    if ok and px_exec is not None:
        return px_exec, basis
    if order_type in ("LIMIT", "STOP_LIMIT"):
        return limit_px, "limit"
    if order_type in ("STOP", "TRAILING_STOP"):
        return stop_px, "stop"
    return ref_last, ("last_mark" if ref_last is not None else "unavailable")


def cash_need_c(side: str, gross_c: Optional[int], fee_c: int) -> int:
    """The worst-case cash a non-futures fill takes (sentence SWEEP): a buy, gross + fee; SELL_SHORT, the 102 % collateral
    less the sale's own proceeds (gross - fee) = the 2 % excess + fee (SHORT_COLLATERAL); any other sale, nothing."""
    if side in BUY_SIDES:
        return (gross_c or 0) + fee_c
    if side == "SELL_SHORT":
        coll_c = to_c(from_c(gross_c or 0) * config.COLLATERAL_PCT)
        return coll_c - max(0, min((gross_c or 0) - fee_c, coll_c))
    return 0


def resting_need(services, conn, snapshot, now: int, acct: dict, inst: Instrument, orow) -> Optional[dict]:
    """The preview's need for the REMAINING quantity of a resting order at the current observation (FUNDS_WAIT re-check):
    the same session (``session_for``: a FOREIGN venue's from its quote record), expected price (``expected_price``), fee
    (``_fees``: a foreign venue's fee line included), settle date (``first_trade_date``) and need (``cash_need_c`` /
    ``_futures_effect``) the ticket shows.  ``{need_c, currency, settle_date, expected, price_basis}``; None when nothing
    prices it (no quote, mark, limit or stop): such an order keeps waiting."""
    qty = int(orow.qty) - int(orow.filled_qty)
    if qty <= 0:
        return None
    iid = inst.instrument_id
    raw = quote_of(services, iid, snapshot)
    session = session_for(services.sessions, inst.session_group, raw, now)
    rec = with_nbbo(services, inst, raw, session, now)
    ok, reason = is_executable(rec, session, now)
    last = qprice(rec.last) if rec is not None and rec.last is not None else None
    ref_last = last if last is not None else _mark_price(services, snapshot, iid)
    probe = type("_P", (), {})()
    probe.side, probe.kind, probe.instrument_id = orow.side, inst.kind, iid
    px_exec, basis = exec_price(probe, rec) if ok else (None, reason)
    expected, price_basis = expected_price(ok, px_exec, basis, orow.order_type, orow.limit_px, orow.stop_px, ref_last)
    if expected is None:
        return None
    gross_c = inst.contract_value_c(expected, qty)
    fee_c = _fees(services, acct, inst, orow.side, qty, gross_c)[0]
    tdate = first_trade_date(services, inst, session, rec, now)
    settle_date = services.cal.settlement_date(tdate, inst.settle_days)
    if inst.kind == "FUTURE":
        pos_qty, _cost = _position(conn, orow.account_id, iid)
        need_c = _futures_effect(inst, orow.side, qty, pos_qty, expected, fee_c, 0, 0, {}, [])[0]
    else:
        need_c = cash_need_c(orow.side, gross_c, fee_c)
    return {"need_c": int(need_c), "currency": inst.currency, "settle_date": settle_date, "expected": expected, "price_basis": price_basis}


def funding_fit(acct: dict, need_c: int, own_c: int, treasury_c: int, pb_c: int) -> Dict[str, int]:
    """§10.8 order of funding, the preview's rule: own available first, then the Treasury sweep, then PB financing (a PB
    account with financing on); ``unfunded`` > 0 means the order cannot be paid for now."""
    shortfall = max(0, int(need_c) - int(own_c))
    sweep_in = min(shortfall, max(0, int(treasury_c)))
    pb_fin = min(shortfall - sweep_in, max(0, int(pb_c))) if (acct.get("kind") == "PB" and int(acct.get("pb_financing") or 0)) else 0
    return {"shortfall": shortfall, "sweep_in": sweep_in, "pb_fin": pb_fin, "unfunded": shortfall - sweep_in - pb_fin}


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


def _lent_shares(services, conn, account_id: str, instrument_id: str) -> int:
    """C27: shares of the position out on loan (``services.lending.lent_qty(conn, account_id, instrument_id)``, P08's
    securities lending); 0 when the module does not expose it yet."""
    fn = getattr(getattr(services, "lending", None), "lent_qty", None)
    if not callable(fn):
        return 0
    try:
        return max(0, int(fn(conn, account_id, instrument_id) or 0))
    except Exception:   # noqa: BLE001 - a failing lookup never blocks a preview by error (the lending job reconciles)
        return 0


def bar_usage(conn) -> Callable[[str, int], int]:
    """EXEC-6: ``fn(instrument_id, bar_t)`` -> the quantity every fill (any order, account or book) already took from that
    1-minute bar; ``orders_rules.fill_cap`` offers only the rest of the participation cap."""
    def used(instrument_id: str, bar_t: int) -> int:
        row = conn.execute("SELECT COALESCE(SUM(qty), 0) FROM fills WHERE instrument_id=? AND bar_ts=?", (instrument_id, int(bar_t))).fetchone()
        return int(row[0] or 0)
    return used


def _own_ticket(inst: Instrument) -> Optional[tuple]:
    """C15: kinds that never go through the order ticket -> (ticket kind, the pointer sentence)."""
    if inst.kind in OWN_TICKET:
        return inst.kind, OWN_TICKET[inst.kind]
    if inst.kind in OTC_TICKET_KINDS:
        return "OTC", OWN_TICKET["SWAP"]
    return None


def _validate(services, req: dict, snapshot, conn, now: Optional[int], rest_ok: bool, partial: Optional[dict], queue_unfunded: bool = False) -> ValidatedOrder:
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
    own = _own_ticket(inst)
    if own is not None:                         # C15: refused at PREVIEW with the pointer to the product's own ticket
        tkind, sentence = own
        raise RejectError("USE_OWN_TICKET", f"{inst.name or inst.symbol} is {sentence}", {"kind": inst.kind, "ticket_kind": tkind, "instrument_id": inst.instrument_id})
    if not services.instruments.tradeable_in(inst, akind):
        raise RejectError("NOT_TRADEABLE_HERE", f"{inst.symbol} ({inst.kind}) does not trade in a {akind} account", {"account_kinds": list(inst.account_kinds)})
    if inst.support in ("UNSUPPORTED", "COMPUTED"):
        raise RejectError("UNSUPPORTED", f"{inst.symbol} is {inst.support.lower()} in this build: model marks never execute" if inst.support == "COMPUTED"
                          else f"{inst.symbol} is unsupported in this build")
    if inst.support == "LIMITED":
        warnings.append(f"{inst.symbol} is LIMITED in this build: {inst.spec.get('note') or 'check the instrument page before trading'}")
    if inst.status != "ACTIVE":
        raise RejectError("INSTRUMENT_EXPIRED", f"{inst.symbol} is {inst.status}")
    if inst.kind in ("OPTION", "FX"):           # capped by the displayed size / no volume concept: the bar-participation sentence does not apply
        conv.remove("PARTICIPATION")
    ccy = inst.currency
    shortfall_row = conn.execute("SELECT id FROM attention WHERE code='FUNDING_SHORTFALL' AND account_id=? AND resolved_ts IS NULL AND details_json LIKE ?",
                                 (account_id, f'%"currency":"{ccy}"%')).fetchone()
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
    # session and quote: a FOREIGN listing trades in its venue's session, taken from the quote record (C15)
    sessions = services.sessions
    raw = quote_of(services, iid, snapshot)
    session = session_for(sessions, inst.session_group, raw, now)
    rec = with_nbbo(services, inst, raw, session, now)
    ok, reason = is_executable(rec, session, now)
    quality = freshness(rec, session, now)
    if rec is None:
        pv["quote"] = {"last": None, "bid": None, "ask": None, "quality": "unavailable", "source": None, "quote_time": None, "age_s": None, "delay_label": None,
                       "method": None, "executable": False, "reason": "NO_QUOTE", "note": NO_QUOTE_NOTE}
    else:
        pv["quote"] = {
            "last": _s(rec.last), "bid": _s(rec.bid), "ask": _s(rec.ask), "quality": quality, "source": rec.source, "quote_time": rec.quote_time,
            "age_s": None if rec.quote_time is None else max(0, now - int(rec.quote_time)), "delay_label": rec.delay_label, "method": rec.method,
            "executable": ok, "reason": reason, "note": _quote_note(rec, reason)}
    if not ok:
        conv.append("STALE") if reason in ("STALE", "NO_QUOTE") else None
    last = qprice(rec.last) if rec is not None and rec.last is not None else None
    mark = _mark_price(services, snapshot, iid)
    probe = type("_P", (), {})()
    probe.side, probe.kind, probe.instrument_id = side, inst.kind, iid
    px_exec, basis = exec_price(probe, rec) if ok else (None, reason)
    # the price stops trigger on and trailing stops trail (EXEC-1): an option's executable bid / ask from the Cboe chain, never its
    # own last trade; everything else the observed last (the mark when no last was ever observed)
    trig, trig_basis = trigger_price(probe, rec)
    if trig is None and inst.kind != "OPTION" and mark is not None:
        trig, trig_basis = mark, "mark"
    if order_type in ("STOP", "STOP_LIMIT") and trig is not None:
        if (side in BUY_SIDES and stop_px <= trig) or (side in SELL_SIDES and stop_px >= trig):
            raise RejectError("STOP_SIDE", f"a {'buy' if side in BUY_SIDES else 'sell'} stop at {stop_px} would trigger at once against the observed {trig_basis}"
                              f" {trig}: use a limit", {"trigger_price": str(trig), "basis": trig_basis, "last": _s(last), "stop_px": str(stop_px)})
    trail_ref_px = None
    if order_type == "TRAILING_STOP":
        if trig is None:
            what = "a fresh Cboe bid / ask (an option trails its executable side, never its own last trade)" if inst.kind == "OPTION" else "an observed last price"
            code = "NO_QUOTE" if reason == "NO_QUOTE" else "QUOTE_STALE"
            raise ConflictError(code, f"a trailing stop needs {what} to set its reference; none is available", {"reason": reason})
        trail_ref_px = trig
        tmp = OrderRow("", account_id, iid, side, qty, 0, order_type, None, None, trail_amt, trail_pct, None, "SEQUENTIAL", tif, "WORKING", False, False,
                       None, None, inst.session_group, 0, config.PARTICIPATION_RATE, kind=inst.kind, lot=lot, tick=inst.tick)
        stop_px = trail_stop(tmp, trail_ref_px)
        pv["trail"].update({"trail_ref_px": str(trail_ref_px), "stop_px": str(stop_px), "ref_basis": trig_basis})
    if order_type in NEEDS_QUOTE_TYPES and session is not None and session.open and not ok and not rest_ok:
        if reason == "NO_QUOTE":                 # USX-08 / OA-26: no print was ever observed - not "too old"
            raise ConflictError("NO_QUOTE", f"{inst.symbol}: {_quote_note(rec, reason)}; a {order_type} order needs a fresh print while the session is open",
                                {"reason": reason, "quality": quality, "quote_time": None, "error": None if rec is None else rec.error})
        raise ConflictError("QUOTE_STALE", f"{inst.symbol}: the quote is not executable ({reason}); a {order_type} order needs a fresh print while the session is open",
                            {"reason": reason, "quality": quality, "quote_time": None if rec is None else rec.quote_time,
                             "note": "" if rec is None else _quote_note(rec, reason)})
    # positions (OVERSELL / CLOSE_ONLY_MISMATCH)
    pos_qty, _cost = _position(conn, account_id, iid)
    working_sells = working_covers = working_buys = 0
    for r in conn.execute("SELECT side, qty, filled_qty FROM orders WHERE account_id=? AND instrument_id=? AND status IN ('WORKING','PARTIAL','SUSPENDED','WAITING_PARENT')",
                          (account_id, iid)).fetchall():
        rem = int(r["qty"]) - int(r["filled_qty"])
        if r["side"] in ("SELL", "STC"):
            working_sells += rem
        elif r["side"] in ("BUY_TO_COVER", "BTC"):
            working_covers += rem
        elif r["side"] == "BUY":
            working_buys += rem         # futures: a working BUY also reduces a short (the futures effect below warns about it)
    if inst.kind == "FUTURE":
        pass        # no borrow in futures: SELL from flat opens a short, SELL beyond the long flips (futures.apply_fill books the signed quantity)
    elif side in ("SELL", "STC"):
        reserved = _reserved_shares(services, account_id, iid) if side == "SELL" else 0
        lent = _lent_shares(services, conn, account_id, iid) if side == "SELL" else 0
        free = pos_qty - working_sells - reserved - lent
        if qty > free:
            code = "OVERSELL" if side == "SELL" else "CLOSE_ONLY_MISMATCH"
            if lent and qty <= free + lent:     # C27: the shares are there but out on loan
                raise RejectError("SHARES_ON_LOAN", f"SELL {qty:,} {inst.symbol}: {lent:,} of the position are lent out: recall {min(lent, qty - max(free, 0)):,} lent"
                                  f" shares first", {"position": pos_qty, "working_sells": working_sells, "reserved_shares": reserved, "lent": lent, "qty": qty})
            hint = f": sell {max(free, 0):,} and SELL_SHORT {qty - max(free, 0):,} with a borrow" if side == "SELL" else ""
            if reserved:
                hint += f"; {reserved:,} shares are reserved by covered calls (SHARES_RESERVED)"
            if lent:
                hint += f"; {lent:,} shares are lent out (recall them first)"
            raise RejectError(code, f"{side} {qty:,} {inst.symbol} but the long position net of working sells{' and reserved shares' if reserved else ''}"
                              f"{' and lent shares' if lent else ''} is {max(free, 0):,}{hint}",
                              {"position": pos_qty, "working_sells": working_sells, "reserved_shares": reserved, "lent": lent, "qty": qty})
    elif side in ("BUY_TO_COVER", "BTC"):
        free = -pos_qty - working_covers
        if qty > free:
            raise RejectError("CLOSE_ONLY_MISMATCH", f"{side} {qty:,} {inst.symbol} but the short position net of working covers is {max(free, 0):,}",
                              {"position": pos_qty, "working_covers": working_covers, "qty": qty})
    if shortfall_row is not None:
        # an order that only reduces a position (SELL / STC of a long, BUY_TO_COVER / BTC of a short, a futures trade toward flat)
        # is allowed while the account's shortfall is open; every other order waits for the account to be funded
        if inst.kind == "FUTURE":
            reduce_only = (side == "SELL" and 0 < qty <= pos_qty) or (side == "BUY" and 0 < qty <= -pos_qty)
        else:
            reduce_only = side in ("SELL", "STC", "BUY_TO_COVER", "BTC")
        if not reduce_only:
            raise ConflictError("FUNDING_SHORTFALL", f"{account_id} has an open funding shortfall in {ccy} (attention #{shortfall_row['id']}): fund the account"
                                " before placing new orders (orders that only reduce a position are allowed)",
                                {"attention_id": int(shortfall_row["id"]), "currency": ccy})
        warnings.append(f"{account_id} has an open funding shortfall in {ccy}: this order only reduces a position, so it is allowed")
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
    ref_last = last if last is not None else mark
    expected, price_basis = expected_price(ok, px_exec, basis, order_type, limit_px, stop_px, ref_last)
    gross_c = None if expected is None else inst.contract_value_c(expected, qty)
    fee_c, fee_basis, fee_lines = _fees(services, acct, inst, side, qty, gross_c)
    tdate = first_trade_date(services, inst, session, rec, now)          # EXEC-8: the date of the first session the order can trade in
    settle_date = services.cal.settlement_date(tdate, inst.settle_days)
    buy = side in BUY_SIDES
    net_cash = None if gross_c is None else (-(gross_c + fee_c) if buy else gross_c - fee_c)
    if inst.kind == "FUTURE":
        net_cash = -fee_c       # no premium and no notional change hands on a future: the fee is the cash; the margin move is in margin / funding
    pv["economics"] = {"gross": _m(gross_c), "fee": _m(fee_c), "fee_basis": fee_basis, "fee_lines": fee_lines, "net_cash": _m(net_cash), "currency": ccy,
                       "trade_date": tdate.isoformat(), "settle_date": settle_date.isoformat(), "accrued": None}
    confirm_notional_c = gross_c or 0
    if inst.kind == "FUTURE":
        need_c, need_formula, confirm_notional_c = _futures_effect(inst, side, qty, pos_qty, expected, fee_c, working_sells, working_buys, pv, warnings)
        conv.append("FUT_MARGIN")
    elif buy:
        need_c, need_formula = cash_need_c(side, gross_c, fee_c), "gross at the expected price + fee"
        pv["margin"] = None if inst.kind != "OPTION" else {"requirement": None, "formula": tables.CONVENTIONS.get("OPT_MARGIN", ""), "detail": "recomputed after the fill"}
    elif side == "SELL_SHORT":
        # SHORT_COLLATERAL: the lender's 102 % is posted at the fill and funded first by the sale's own proceeds (gross - fee);
        # the cash the fill takes is the rest: the 2 % excess + fee
        need_c = cash_need_c(side, gross_c, fee_c)
        need_formula = "102 % cash collateral posted at the fill, funded first by the sale's own proceeds (gross - fee): need = 2 % excess + fee"
        pv["margin"] = None
    else:
        need_c, need_formula = 0, "a sale: proceeds settle at T+1" + ("; short-option margin is recomputed after the fill" if inst.kind == "OPTION" else "")
        pv["margin"] = None if inst.kind != "OPTION" else {"requirement": None, "formula": tables.CONVENTIONS.get("OPT_MARGIN", ""), "detail": "recomputed after the fill"}
        if inst.kind == "OPTION":
            conv.append("OPT_MARGIN")
    if inst.kind == "OPTION" and buy:
        conv.append("OPT_MARGIN")
    # hedge candidates (absent-safe) before funding: a picked hedge's legs need cash too (USX-17), and a leg that is booked
    # elsewhere is refused here, not at submit (PO-10)
    pv["hedges"] = _hedges_block(services, account_id, inst, side, qty)
    hedge = req.get("hedge") if isinstance(req.get("hedge"), dict) else None
    hedge_need_c, hedge_info = _hedge_need(services, pv["hedges"], hedge, qty, warnings)
    # funding (§6.1, §10.8)
    bp = acc.buying_power(account_id, ccy, settle_date, snapshot=snapshot)
    cash = acc.cash(account_id, snapshot=snapshot).get(ccy)
    own_c = int(bp.immediate_c) if inst.kind == "FUTURE" else int(bp.available_c)     # USX-17: IM moves at once (execution.fund immediate)
    tre = int(bp.treasury_available_c)
    pbh = int(bp.pb_headroom_c)
    total_need_c = need_c + hedge_need_c
    fit = funding_fit(acct, total_need_c, own_c, tre, pbh)
    shortfall, sweep_in, pb_fin, unfunded = fit["shortfall"], fit["sweep_in"], fit["pb_fin"], fit["unfunded"]
    # C15: a non-USD need the account and Treasury cannot cover in that currency -> the FX conversion plan (services.fx.conversion_plan);
    # with fx_convert: true the fill converts USD into the currency first (execution.fund(..., fx_convert=True))
    fx_conversion = None
    fx_convert = bool(req.get("fx_convert"))
    if ccy != "USD" and unfunded > 0:
        fx_conversion = _conversion_plan(services, snapshot, account_id, ccy, unfunded, settle_date, conn)
        if fx_conversion is not None:
            fx_conversion["requested"] = fx_convert
            usd_c = _plan_usd_c(fx_conversion)
            if fx_conversion.get("executable") and usd_c is not None:
                usd_bp = acc.buying_power(account_id, "USD", settle_date, snapshot=snapshot)
                usd_room = int(usd_bp.available_c) + max(0, int(usd_bp.treasury_available_c)) + max(0, int(usd_bp.pb_headroom_c))
                fx_conversion["usd_fundable"] = usd_c <= usd_room
                fx_conversion["usd_available"] = _m(usd_room)
                if fx_convert and usd_c <= usd_room:
                    unfunded = 0
            if fx_convert and unfunded > 0:
                warnings.append("automatic conversion asked for, but " + (fx_conversion.get("reason") or "the USD side cannot be funded"))
    fundable = unfunded <= 0
    threshold = sweep_confirm_threshold_c(conn)
    nav_c = int(acc.nav(f"{lid}:ALL", snapshot=snapshot).nav_c)
    notional = confirm_notional_c          # futures: only the opening contracts (a closing trade adds no exposure)
    big_sweep = threshold > 0 and sweep_in > threshold
    big_notional = nav_c > 0 and notional * 4 > nav_c
    confirm_required = big_sweep or big_notional
    rules = []
    if big_notional:                       # USX-18: the rule that fired, with its own threshold
        rules.append({"rule": "NOTIONAL_25PCT_NAV", "value": _m(notional), "threshold": _m(-(-nav_c // 4)),
                      "text": f"notional {fmt_money(notional)} > 25 % of NAV = {fmt_money(-(-nav_c // 4))}"})
    if big_sweep:
        rules.append({"rule": "SWEEP_THRESHOLD", "value": _m(sweep_in), "threshold": _m(threshold),
                      "text": f"sweep-in {fmt_money(sweep_in)} > the confirm threshold {fmt_money(threshold)}"})
    confirm_reason = "; ".join(r["text"] for r in rules) or None
    fx_leg = None
    if ccy != "USD" and shortfall > 0:
        fx_leg = {"buy_ccy": ccy, "sell_ccy": "USD", "amount": _m(shortfall), "rate": _s(_mark_price(services, snapshot, f"{ccy}USD")), "settle_date": settle_date.isoformat(),
                  "note": "a foreign need is met from the account's then Treasury's cash in that currency; beyond that the order needs a USD conversion"
                          " (opt in to convert automatically at the fill, or deal it on the FX desk first)"}
    if shortfall > 0:
        conv.append("SWEEP")
    pv["funding"] = {"currency": ccy, "settled": _m(cash.settled_c if cash else 0), "pending": _m(cash.pending_c if cash else 0), "reserved": _m(cash.reserved_c if cash else 0),
                     "available": _m(own_c), "need": _m(total_need_c), "need_formula": need_formula + (" + the hedge legs" if hedge_need_c else ""),
                     "order_need": _m(need_c), "hedge_legs": hedge_info, "shortfall": _m(shortfall), "sweep_in": _m(sweep_in),
                     "sweep_source": "TREASURY" if sweep_in > 0 else ("PB" if pb_fin > 0 else "NONE"), "treasury_available": _m(tre), "pb_financing": _m(pb_fin),
                     "fx_leg": fx_leg, "fx_conversion": fx_conversion, "fx_convert": bool(fx_convert and fx_conversion is not None and fundable), "ok": fundable,
                     "confirm_required": confirm_required, "confirm_reason": confirm_reason, "confirm_rules": rules, "threshold": _m(threshold) if big_sweep else None,
                     "nav": _m(nav_c), "formula": "own settled + receivables due by settlement - payables - reserved"}
    funds_wait = None
    if not fundable:
        code = "INSUFFICIENT_FUNDS_CCY" if ccy != "USD" else "INSUFFICIENT_FUNDS"
        conv_txt = ""
        if fx_conversion is not None and fx_conversion.get("executable") and not fx_convert:
            conv_txt = f"; a USD conversion of about {fx_conversion.get('usd_amount') or _m(_plan_usd_c(fx_conversion))} USD would cover it (convert automatically to proceed)"
        msg = (f"{account_id} needs {ccy} {fmt_money(total_need_c)}: own available {fmt_money(own_c)}, Treasury available {fmt_money(tre)},"
               f" PB financing {fmt_money(pbh)}{conv_txt}")
        details = {"need_c": total_need_c, "own_available_c": own_c, "treasury_available_c": tre, "pb_headroom_c": pbh, "currency": ccy, "fx_leg": fx_leg,
                   "fx_conversion": fx_conversion}
        if not queue_unfunded:
            raise ConflictError(code, msg, details)
        # FUNDS_WAIT: funding is not a refusal; the order queues SUSPENDED (no reservation) and resumes when cash frees up
        avail = own_c + max(0, tre) + (max(0, pbh) if (acct.get("kind") == "PB" and int(acct.get("pb_financing") or 0)) else 0)
        wait = funds_wait_reason(ccy, total_need_c, avail, fx_first=fx_leg is not None)
        funds_wait = {"code": code, "message": msg, "reason": wait, "details": details, "need_c": total_need_c, "available_c": avail}
        pv["funding"].update({"queued": True, "wait": wait})
        warnings.append(f"will queue: {FUNDS_WAIT} (needs {ccy} {fmt_money(total_need_c)}, available {ccy} {fmt_money(avail)}); it executes automatically when cash"
                        " frees up" + ("; deal the FX leg first (never automatic)" if fx_leg is not None else ""))
    # execution block
    try:
        expires_ts = order_expiry(services, inst, tif, session, rec, now)
    except ValueError as e:
        raise RejectError("SESSION", f"no session for {inst.session_group} within 16 days") from e
    if inst.expiry:
        exp = date.fromisoformat(inst.expiry)
        close = sessions.session_close(inst.session_group, exp)
        if close is not None:
            expires_ts = min(expires_ts, close)
    tmp = OrderRow("", account_id, iid, side, qty, 0, order_type, limit_px, stop_px, trail_amt, trail_pct, trail_ref_px, "SEQUENTIAL", tif, "WORKING", False, False,
                   None, None, inst.session_group, expires_ts, config.PARTICIPATION_RATE, kind=inst.kind, lot=lot, tick=inst.tick, multiplier=inst.multiplier,
                   price_scale=inst.price_scale, expiry=inst.expiry)
    participation = Decimal(str(get_setting(conn, "participation_rate", str(config.PARTICIPATION_RATE)) or config.PARTICIPATION_RATE))
    tmp.participation = participation
    used = bar_usage(conn)
    dec = evaluate(tmp, rec, session, now, participation, inst, bar_used=used)
    cap, cap_note = fill_cap(tmp, rec, inst, participation, used) if ok else (0, "not executable now")
    if rec is not None and (rec.bar is None or rec.bar.v is None) and inst.kind not in ("OPTION", "FX"):
        conv.append("NO_VOLUME")
    if inst.kind == "OPTION" and rec is not None and rec.method != "chain":
        assumption = "options execute only at the Cboe delayed NBBO; this record is not a chain quote"
    elif inst.kind == "OPTION":
        assumption = "fills at the Cboe delayed ask (buys) / bid (sells); capped by the displayed size; stops and limits are judged on that bid / ask"
    elif basis in ("ask", "bid"):
        assumption = "fills at the Cboe underlying ask (buys) / bid (sells) from the cached chain"
    elif not ok:
        assumption = "fills at the observed price once a fresh print is observed (the last trade; the Cboe ask / bid while a fresh chain is cached)"
    else:
        assumption = "fills at the last observed trade price; no bid/ask is available from the free feed"     # no fresh Cboe chain is cached (EXEC-7)
    rests_until = None if (session is not None and session.open) else next_open(services, inst, session, rec, now)
    sess_view = None if session is None else {"group": session.group, "open": session.open, "phase": session.phase, "closes_at": session.closes_at,
                                              "next_open": session.next_open if session.next_open else rests_until, "note": session.note}
    pv["execution"] = {"price_basis": price_basis, "expected_price": _s(expected), "fill_now": dec.action == "FILL", "fill_qty_now": dec.qty if dec.action == "FILL" else 0,
                       "decision": dec.reason, "decision_note": dec.note, "cap_note": cap_note, "session": sess_view, "rests_until": rests_until,
                       "expires_at": expires_ts, "assumption": assumption, "participation": None if inst.kind == "FX" else str(participation)}
    if funds_wait is not None:
        pv["execution"].update({"fill_now": False, "fill_qty_now": 0, "queued": True, "queued_reason": funds_wait["reason"]})
    if session is None or not session.open:
        phase = (session.phase if session is not None else "CLOSED").lower()
        warnings.append(f"the {inst.session_group} session is {phase}: the order rests until it opens"
                        + (f" ({ny_of(rests_until).strftime('%a %H:%M ET')})" if rests_until else ""))
    # compliance, risk (other packages; absent-safe)
    pv["compliance"] = _notices(services, "compliance", account_id, inst, side, qty, pv, warnings)
    pv["risk"] = _notices(services, "risk", account_id, inst, side, qty, pv, warnings)
    seen = set()
    pv["conventions"] = [c for c in conv if not (c in seen or seen.add(c))]
    package = req.get("package") if isinstance(req.get("package"), dict) else None
    return ValidatedOrder(account_id, acct, lid, akind, inst, side, qty, order_type, limit_px, stop_px, trail_amt, trail_pct, trail_ref_px, tif, session, rec, ok, reason,
                          expected, price_basis, participation, expires_ts, rests_until, settle_date, gross_c, fee_c, fee_basis, need_c, pv["funding"], locate, warnings, pv,
                          hedge, package, bool(req.get("confirm_large")), req.get("preview_id"), funds_wait=funds_wait)


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
        vo = validate(services, req, snapshot=snapshot, now=now, partial=pv, queue_unfunded=True)     # funding alone queues (FUNDS_WAIT)
        pv = vo.preview
        if not vo.executable:
            _ask_for_quote(services, vo.inst.instrument_id)
    except RejectError as e:
        if e.code in ("NO_QUOTE", "QUOTE_STALE"):
            _ask_for_quote(services, str(req.get("instrument_id") or ""))
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


def _ask_for_quote(services, instrument_id: str) -> None:
    """USX-08: the ticket's instrument without an executable quote is subscribed PAGE (in memory only), so the worker fetches it at
    its next tick, ahead of the list rows (never-observed names go first: ``QuoteCache.due``)."""
    cache = getattr(services, "cache", None)
    if not instrument_id or cache is None or not hasattr(cache, "subscribe"):
        return
    inst = services.instruments.get(instrument_id) if getattr(services, "instruments", None) is not None else None
    if inst is None or inst.kind in tuple(OWN_TICKET) + OTC_TICKET_KINDS:
        return
    try:
        cache.subscribe([instrument_id], 90, "PAGE")
    except Exception:   # noqa: BLE001 - a subscription is a hint; the preview never fails on it
        pass


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
