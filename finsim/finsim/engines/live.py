"""Live quotes and immediate fills for a save that tracks the real market.

The daily update still closes the day (settlement, marks, the briefing) at the update time. Between updates the
latest real quote (Yahoo Finance, up to 15 minutes delayed on most exchanges) is shown next to the last close, and a
LIVE ticket fills at it immediately through the ordinary fill logic — spread, impact, participation cap, commission —
so the trade, its settlement and its ledger entries are the same as any other. A live ticket that cannot fill at once — a
limit away from the quote, a stop or trailing stop not yet touched — rests against the quote stream: every quote refresh
(a page asking for live quotes, or the server's own minute tick while nobody is looking) sweeps the resting book with
`work()` and fills whatever the latest quote has reached, and the daily update still evaluates it against the session. What is stored is the fill itself
(TRADE_EXECUTED carries the quote, its time and source), never a network result, so replay is exact and offline.

How each instrument is quoted:
  stocks, ETFs, ADRs, REITs, preferreds   directly (the security's own quote)
  listed options                           the model surface repriced at the underlying's live price
  futures                                  the contract's last close moved with its underlying (front-month continuous
                                           quote for commodities, SPY for ES, ZN=F for ZN)
  bonds                                    repriced off the live Treasury curve: Yahoo's 13-week, 5-, 10- and 30-year yields shift the
                                           save's curve tenor by tenor and every bond (Treasuries, corporates, munis, agencies) moves
                                           by what that shift does to its price — its credit spread stays where the last close put it
  indices (DXY, VIX)                       directly (the dollar index and the VIX quote live)
  commodity spot (SPOT:CL …)               the front-month continuous contract's live quote
  FX                                       the pair's live quote (spot deals with execution LIVE)
"""
from __future__ import annotations

import time
from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List, Optional

from ..clock import MARKET_TZ, latest_quote_tick, next_quote_tick, market_now
from ..domain.events import E
from ..domain.models import Bar, Order, Security, Trade
from ..money import D, price as qprice, ZERO
from .trading import ORDER_TYPES

SOURCE = "Yahoo Finance (up to 15 minutes delayed)"
# financial futures: the contract's last close moves with a live reference — the source security's quote where it has one
# (SPY for ES, the coin for BTC, the VIX for VX), else Yahoo's own continuous contract (Treasury futures: their source bonds have no live quote)
YAHOO_FUTURES = {"ZT": "ZT=F", "ZF": "ZF=F", "ZN": "ZN=F", "ZB": "ZB=F"}
EQUITY_CLASSES = ("EQUITY", "ETF", "ADR", "REIT", "PREFERRED", "CRYPTO")


def _financial_ref(code: str, feed):
    """(Yahoo symbol, kind) for a financial future's live reference, or None."""
    from .commodities import FINANCIAL_SOURCES
    if code in YAHOO_FUTURES:
        return YAHOO_FUTURES[code], "yahoo"
    src, kind = FINANCIAL_SOURCES.get(code, (None, None))
    if src and src in feed.equities:
        return feed.equities[src], "security"
    return None


def _ny(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=MARKET_TZ).strftime("%Y-%m-%d %H:%M") if ts else ""


class LiveDesk:
    def __init__(self, world):
        self.w = world
        self.last_tick: Optional[str] = None      # the last quote update this desk worked (memory only)

    # ------------------------------------------------------------------ availability
    def available(self) -> bool:
        m = self.w.market
        return getattr(self.w, "market_source", "SIMULATED") == "REAL" and getattr(m, "real_feed", None) is not None

    def _feed(self):
        from ..world import CommandError
        if not self.available():
            raise CommandError("live quotes need a save that tracks the real market (a career save); this save is simulated")
        return self.w.market.real_feed

    # ------------------------------------------------------------------ quotes
    def _symbols_for(self, sec: Security) -> List[str]:
        """Yahoo symbols a quote for `sec` needs."""
        from .vol import index_source
        from .realfeed import COMMODITY_SYMBOLS, INDEX_SYMBOLS, YIELD_SYMBOLS
        feed = self.w.market.real_feed
        if sec.is_bond:
            return list(YIELD_SYMBOLS.values())
        if sec.asset_class == "INDEX":
            return [INDEX_SYMBOLS[sec.id]] if sec.id in INDEX_SYMBOLS else []
        if sec.is_option:
            src = sec.index_level_source or index_source(sec.underlying)
            return [feed.equities.get(src, src)]
        if sec.is_future:
            code = sec.underlying
            if code in COMMODITY_SYMBOLS:
                return [COMMODITY_SYMBOLS[code]]
            ref = _financial_ref(code, feed)
            return [ref[0]] if ref else []
        if sec.asset_class in EQUITY_CLASSES:
            return [feed.equities.get(sec.id, sec.id)]
        return []

    def quotes(self, ids: List[str], max_age_s: float = 60.0) -> Dict[str, Dict]:
        """Live quotes for several ids in one fetch: {id: quote}. Ids are securities, or SPOT:<code> for a commodity's spot
        (the front-month continuous contract). Anything without a quote is left out."""
        from .realfeed import COMMODITY_SYMBOLS, CENTS_QUOTED
        feed = self._feed()
        w = self.w
        secs = [w.securities[i] for i in ids if i in w.securities]
        spots = [i[5:] for i in ids if i.startswith("SPOT:") and i[5:] in COMMODITY_SYMBOLS]
        syms: List[str] = []
        for sec in secs:
            syms.extend(self._symbols_for(sec))
        syms.extend(COMMODITY_SYMBOLS[c] for c in spots)
        raw = feed.live(list(dict.fromkeys(syms)), max_age_s) if syms else {}
        out = {}
        for sec in secs:
            q = self._quote_from(sec, raw)
            if q is not None:
                out[sec.id] = q
        now = time.time()
        for code in spots:
            q = raw.get(COMMODITY_SYMBOLS[code])
            ref = w.market.spot(code)
            if not q or not ref:
                continue
            live = float(q["price"]) / (100.0 if code in CENTS_QUOTED else 1.0)
            t = int(q.get("time") or 0)
            out[f"SPOT:{code}"] = {"id": f"SPOT:{code}", "price": live, "last_close": ref, "last_close_date": w.current_date.isoformat(), "source": SOURCE, "time": t, "time_ny": _ny(t),
                                   "age_s": max(0, int(now - t)) if t else None, "change_pct": live / ref - 1.0, "method": "front_month", "ref": COMMODITY_SYMBOLS[code], "ref_price": float(q["price"])}
        return out

    def live_curve(self, raw: Dict[str, Dict]):
        """The save's curve shifted tenor by tenor to the live Treasury yields (linear between the quoted tenors)."""
        from .realfeed import YIELD_SYMBOLS
        from .pricing import interp_rate
        from ..domain.models import YieldCurve
        w = self.w
        base = w.market.curve()
        pts = []
        for t, sym in sorted(YIELD_SYMBOLS.items()):
            q = raw.get(sym)
            if q and q.get("price") is not None:
                pts.append((t, float(q["price"]) / 100.0 - interp_rate(base, t)))
        if not pts:
            return None, {}
        def shift(t: float) -> float:
            if t <= pts[0][0]:
                return pts[0][1]
            if t >= pts[-1][0]:
                return pts[-1][1]
            for (t0, s0), (t1, s1) in zip(pts, pts[1:]):
                if t0 <= t <= t1:
                    return s0 + (s1 - s0) * (t - t0) / (t1 - t0)
            return pts[-1][1]
        rates = [r + shift(t) for t, r in zip(base.tenors, base.rates)]
        live = YieldCurve(base.date, list(base.tenors), rates, base.ig_spread_bps, base.hy_spread_bps, base.policy_rate) if hasattr(base, "hy_spread_bps") else YieldCurve(base.date, list(base.tenors), rates)
        return live, {t: s for t, s in pts}

    def quote(self, sec: Security, max_age_s: float = 60.0) -> Optional[Dict]:
        feed = self._feed()
        syms = self._symbols_for(sec)
        raw = feed.live(syms, max_age_s) if syms else {}
        return self._quote_from(sec, raw)

    def _quote_from(self, sec: Security, raw: Dict[str, Dict]) -> Optional[Dict]:
        from .vol import index_source, index_factor
        from .realfeed import COMMODITY_SYMBOLS, CENTS_QUOTED
        from .commodities import FINANCIAL_SOURCES
        w = self.w
        bar = w.trading._bar(sec)
        if bar is None:
            return None
        last = float(bar.close)
        now = time.time()
        base = {"id": sec.id, "last_close": last, "last_close_date": w.current_date.isoformat(), "source": SOURCE}

        def done(price: float, q: Dict, method: str, ref: str, ref_price: float, extra: Optional[Dict] = None) -> Dict:
            t = int(q.get("time") or 0)
            return {**base, "price": price, "time": t, "time_ny": _ny(t), "age_s": max(0, int(now - t)) if t else None,
                    "change_pct": (price / last - 1.0) if last else 0.0, "method": method, "ref": ref, "ref_price": ref_price, **(extra or {})}

        if sec.is_bond:
            from .pricing import BondPricer
            from .realfeed import YIELD_SYMBOLS
            live_curve, shifts = self.live_curve(raw)
            if live_curve is None:
                return {**base, "price": last, "time": None, "time_ny": "", "age_s": None, "change_pct": 0.0, "method": "last_close", "ref": sec.id, "ref_price": last,
                        "note": "no live Treasury yields right now: the last close is used"}
            try:
                if sec.is_mbs or sec.inflation_linked:
                    p_live = float(w.market.bond_clean_price(sec, live_curve, w.current_date))
                    p_base = float(w.market.bond_clean_price(sec, w.market.curve(), w.current_date))
                else:
                    p_live = float(BondPricer.clean_price_from_curve(sec, w.market.curve_for_security(sec, live_curve), w.current_date))
                    p_base = float(BondPricer.clean_price_from_curve(sec, w.market.curve_for_security(sec), w.current_date))
            except Exception:
                return None
            px = round(last + (p_live - p_base), 4)
            yrs = max(0.05, (date.fromisoformat(sec.maturity) - w.current_date).days / 365.0) if sec.maturity else 10.0
            near = min(YIELD_SYMBOLS, key=lambda t: abs(t - yrs))
            q = raw.get(YIELD_SYMBOLS[near]) or next(iter(v for v in raw.values() if v), {})
            return done(px, q, "curve", YIELD_SYMBOLS[near], float(q.get("price", 0.0)),
                        {"shift_bps": round(shifts.get(near, 0.0) * 1e4, 1), "note": f"repriced off the live Treasury curve ({YIELD_SYMBOLS[near]} {float(q.get('price', 0.0)):.2f}%)"})
        if sec.asset_class == "INDEX":
            from .realfeed import INDEX_SYMBOLS
            q = raw.get(INDEX_SYMBOLS.get(sec.id, ""))
            if not q:
                return None
            return done(float(q["price"]), q, "direct", INDEX_SYMBOLS[sec.id], float(q["price"]))
        if sec.is_option:
            src = sec.index_level_source or index_source(sec.underlying)
            sym = w.market.real_feed.equities.get(src, src)
            q = raw.get(sym)
            if not q:
                return None
            S = float(q["price"]) * (sec.index_factor if sec.index_level_source else index_factor(sec.underlying))
            qo = w.options.quote(sec, S, "live")
            return done(float(qo["mid"]), q, "underlying", sym, float(q["price"]),
                        {"bid": qo["bid"], "ask": qo["ask"], "underlying": S, "iv": qo.get("iv") or qo.get("sigma"), "delta": qo.get("delta")})
        if sec.is_future:
            code = sec.underlying
            if code in COMMODITY_SYMBOLS:
                sym = COMMODITY_SYMBOLS[code]
                q = raw.get(sym)
                if not q:
                    return None
                live_spot = float(q["price"]) / (100.0 if code in CENTS_QUOTED else 1.0)
                ref_spot = w.market.spot(code)
                if not ref_spot:
                    return None
                ratio = live_spot / ref_spot
            elif _financial_ref(code, w.market.real_feed):
                sym, kind = _financial_ref(code, w.market.real_feed)
                q = raw.get(sym)
                if not q:
                    return None
                src = FINANCIAL_SOURCES[code][0]
                ref_spot = float(w.market.last_bar(src).close) if kind == "security" and w.market.history.get(src) else float(q.get("prev_close") or 0)
                if not ref_spot:
                    return None
                live_spot = float(q["price"])
                ratio = live_spot / ref_spot
            else:
                return None
            px = last * ratio
            if sec.tick_size:
                px = round(px / sec.tick_size) * sec.tick_size
            return done(float(px), q, "ratio", sym, live_spot, {"ref_close": ref_spot})
        if sec.asset_class in EQUITY_CLASSES:
            sym = w.market.real_feed.equities.get(sec.id, sec.id)
            q = raw.get(sym)
            if not q:
                return None
            return done(float(q["price"]), q, "direct", sym, float(q["price"]), {"day_high": q.get("high"), "day_low": q.get("low"), "day_volume": q.get("volume")})
        return None

    def fx_mid(self, buy: str, sell: str) -> Optional[Dict]:
        """Live mid for a currency pair (sell per one buy) from the dollar quotes; None when a leg has no quote."""
        from .realfeed import FX_SYMBOLS
        feed = self._feed()
        need = {}
        for c in (buy, sell):
            if c != "USD":
                if c not in FX_SYMBOLS:
                    return None
                need[c] = FX_SYMBOLS[c]
        raw = feed.live([s for s, _ in need.values()]) if need else {}
        usd = {"USD": 1.0}
        t = 0
        for c, (sym, invert) in need.items():
            q = raw.get(sym)
            if not q:
                return None
            usd[c] = (1.0 / float(q["price"])) if invert else float(q["price"])
            t = max(t, int(q.get("time") or 0))
        return {"mid": usd[buy] / usd[sell], "time": t, "time_ny": _ny(t), "source": SOURCE, "usd": usd}

    # ------------------------------------------------------------------ fills
    RESTING = ("WORKING", "PARTIALLY_FILLED")

    def resting(self) -> List[Order]:
        """Live orders still working: they are checked against every quote refresh."""
        w = self.w
        out = []
        for pf in w.portfolios.values():
            for o in pf.orders.values():
                if o.status in self.RESTING and o.execution == "LIVE" and not o.condition and o.strategy_tag not in pf.strategies:
                    out.append(o)
        return out

    def instructions(self) -> List[Order]:
        """Instructions (execution NEXT_UPDATE) still working: worked at the 15-minute quote updates."""
        w = self.w
        out = []
        for pf in w.portfolios.values():
            for o in pf.orders.values():
                if o.status in self.RESTING and o.execution != "LIVE" and o.strategy_tag not in pf.strategies:
                    out.append(o)
        return out

    def pending(self) -> bool:
        """Is there anything a sweep could act on (a resting live order, or an instruction once its quote update comes)?"""
        return bool(self.resting()) or (self.w.quote_ticks_active() and bool(self.instructions()))

    def fill(self, order: Order, cause) -> Optional[Trade]:
        """Work a just-entered LIVE order against the latest quote: a market order fills now, anything else fills if the
        quote has reached it and otherwise rests against the quote stream (and the daily update)."""
        from ..world import CommandError
        sec = self.w.securities[order.security_id]
        if order.order_type not in ORDER_TYPES:
            raise CommandError(f"unsupported order type {order.order_type}")
        q = self.quote(sec)
        if q is None:
            raise CommandError(f"no live quote for {sec.id} right now; send it as an instruction for the next update instead")
        return self._work_one(order, q, cause)

    def work(self, max_age_s: float = 60.0, at=None) -> Dict:
        """Sweep the live book against the latest quotes: every resting live order at every call, and — once the next
        15-minute quote update (09:45 to 16:15 New York) has come — every working instruction, each once per update. One
        batched fetch for every security involved; the LIVE_SWEEP event (emitted only when something happens) is the cause
        of the fills, triggers and ratchets, so replay needs no feed. Returns what happened."""
        w = self.w
        out = {"checked": 0, "instructions": 0, "quoted": 0, "filled": [], "triggered": [], "ratcheted": [], "asof": None,
               "tick": None, "next_tick": None}
        if not self.available():
            return out
        orders = self.resting()
        ny = market_now(at)
        tick = latest_quote_tick(w.calendar, ny)
        tick_iso = tick.isoformat()
        out["tick"], out["next_tick"] = tick_iso, next_quote_tick(w.calendar, ny).isoformat()
        due = [o for o in self.instructions()
               if (o.executes_at or "") <= tick_iso and (o.last_tick or "") < tick_iso]      # entered before this update, not yet worked at it
        orders = orders + due
        out["checked"], out["instructions"] = len(orders), len(due)
        if not orders:
            self.last_tick = tick_iso
            return out
        ids = {o.security_id for o in orders}
        ids |= {o.condition["ref"] for o in due if o.condition and o.condition["ref"] in w.securities}
        quotes = self.quotes(sorted(ids), max_age_s)
        out["quoted"] = len(quotes)
        out["asof"] = max((int(q.get("time") or 0) for q in quotes.values()), default=None)
        cause = [None]

        def cause_ev():
            if cause[0] is None:
                cause[0] = w.emit(E.LIVE_SWEEP, {"orders": [o.id for o in orders], "tick": tick_iso, "at": ny.isoformat(),
                                                 "quotes": {i: {"price": q["price"], "time": q.get("time"), "time_ny": q.get("time_ny")} for i, q in quotes.items()}})
            return cause[0]

        for o in orders:
            q = quotes.get(o.security_id)
            if q is None:
                continue
            if o in due:
                o.last_tick = tick_iso
                if o.condition and not o.condition_met_date and not self._condition_met(o, quotes, cause_ev, tick):
                    continue
            before = (o.triggered, o.trail_level, o.filled_quantity)
            trade = self._work_one(o, q, cause_ev, tick if o in due else None)
            if trade is not None:
                out["filled"].append({"order_id": o.id, "trade_id": trade.id, "portfolio_id": o.portfolio_id, "security_id": o.security_id, "side": o.side,
                                      "quantity": trade.quantity, "price": trade.price, "status": o.status, "quote_time_ny": q.get("time_ny")})
            elif o.triggered and not before[0]:
                out["triggered"].append(o.id)
            elif o.trail_level != before[1]:
                out["ratcheted"].append(o.id)
        self.last_tick = tick_iso
        return out

    def _condition_met(self, order: Order, quotes: Dict[str, Dict], cause, tick) -> bool:
        """An instruction's condition at a quote update: a security reference reads its live quote, a curve or spot
        reference the last close; met → recorded (an event), not met → noted and the instruction waits for the next update."""
        w = self.w
        ref = order.condition["ref"]
        q = quotes.get(ref)
        v = float(q["price"]) if q else w.trading._ref_value(ref)
        if v is None:
            return False
        ok = (v <= order.condition["value"]) if order.condition["op"] == "<=" else (v >= order.condition["value"])
        label = tick.strftime("%H:%M")
        if not ok:
            w.trading._note(order, None, f"{label} quote update: condition not met, {ref} = {v:,.4g}; checked again at the next update")
            return False
        w.emit(E.ORDER_STATUS_CHANGED, {"portfolio_id": order.portfolio_id, "order_id": order.id, "status": order.status, "condition_met_date": w.current_date.isoformat(),
                                        "note": f"{label} quote update: condition met, {ref} = {v:,.4g} {order.condition['op']} {order.condition['value']}"},
               cause_id=cause().id, portfolio_id=order.portfolio_id)
        return True

    def _work_one(self, order: Order, q: Dict, cause, tick=None) -> Optional[Trade]:
        """Evaluate one order against quote `q`. `cause` is an Event or a callable that emits one on first use; `tick` is
        the quote update an instruction is being worked at (None for a resting live order, worked at every refresh)."""
        w = self.w
        pf = w.portfolios[order.portfolio_id]
        sec = w.securities[order.security_id]
        ev = (lambda: cause() if callable(cause) else cause)
        lb = w.trading._bar(sec)
        live = qprice(q["price"])
        when = f"{q['time_ny']} New York" if q.get("time") else "the last close"
        if tick is not None:
            when = f"{tick.strftime('%H:%M')} quote update, {when}"
        rests = "checked again at the next quote update" if tick is not None else "rests against the live quote"
        if sec.is_option:
            bid, ask = qprice(q["bid"]), qprice(q["ask"])
            half = (ask - bid) / 2
        else:
            rel = (lb.ask - lb.bid) / 2 / lb.close if lb.close else ZERO
            half = qprice(live * rel)
            bid, ask = live - half, live + half
        volume = lb.volume
        otype, side = order.order_type, order.side
        base = live
        if otype == "TRAILING_STOP":
            level = order.trail_level
            hit = (live <= level) if side == "SELL" else (live >= level)
            if not hit:
                new_level = qprice(live * D(1 - order.trail_pct)) if side == "SELL" else qprice(live * D(1 + order.trail_pct))
                better = (new_level > level) if side == "SELL" else (new_level < level)
                if better:
                    w.emit(E.ORDER_STATUS_CHANGED, {"portfolio_id": pf.id, "order_id": order.id, "status": order.status, "trail_level": new_level,
                                                    "note": f"trailing stop ratchets to {new_level} at the live quote {live} ({when})"}, cause_id=ev().id, portfolio_id=pf.id)
                else:
                    w.trading._note(order, None, f"trailing stop {level} not touched at the live quote {live} ({when}); {rests}")
                return None
            if not order.triggered:
                w.emit(E.ORDER_STATUS_CHANGED, {"portfolio_id": pf.id, "order_id": order.id, "status": order.status, "triggered": True,
                                                "note": f"trailing stop {level} triggered at the live quote {live} ({when})"}, cause_id=ev().id, portfolio_id=pf.id)
        elif otype in ("STOP", "STOP_LIMIT"):
            if not order.triggered:
                hit = (live >= order.stop_price) if side == "BUY" else (live <= order.stop_price)
                if not hit:
                    w.trading._note(order, None, f"stop {order.stop_price} not touched at the live quote {live} ({when}); {rests}")
                    return None
                w.emit(E.ORDER_STATUS_CHANGED, {"portfolio_id": pf.id, "order_id": order.id, "status": order.status, "triggered": True,
                                                "note": f"stop {order.stop_price} triggered at the live quote {live} ({when})"}, cause_id=ev().id, portfolio_id=pf.id)
            if otype == "STOP_LIMIT":
                lim = order.limit_price
                if (live > lim) if side == "BUY" else (live < lim):
                    w.trading._note(order, None, f"stop triggered but limit {lim} not reached at the live quote {live} ({when}); {rests}")
                    return None
        elif otype in ("LIMIT", "TAKE_PROFIT"):
            lim = order.limit_price
            marketable = (live <= lim) if side == "BUY" else (live >= lim)
            if not marketable:
                w.trading._note(order, None, f"limit {lim} not marketable at the live quote {live} ({when}); {rests}")
                return None
        bar = Bar(w.current_date.isoformat(), live, live, live, live, volume, bid, ask)
        detail = {"quote_time": q.get("time"), "quote_time_ny": q.get("time_ny"), "quote_age_s": q.get("age_s"), "quote_source": q["source"],
                  "quote_method": q["method"], "quote_ref": q.get("ref"), "quote_ref_price": q.get("ref_price"), "last_close": q["last_close"]}
        note = f"live fill at {live} ({when}, {q['source']})" if q.get("time") else f"live fill at the last close {live} ({q.get('note', '')})"
        if tick is not None:
            detail["quote_update"] = tick.isoformat()
        trade = w.trading._fill(order, sec, bar, base, half, "LIVE" if tick is None else "QUOTE_UPDATE", ev(), note=note, extra_detail=detail)
        if trade is not None and (sec.is_future or sec.is_option):
            # margined instruments: post (or release) initial margin now rather than at the update, as the clearing broker would
            if sec.is_option:
                w.options.recompute_margin(pf, ev())
            w.futures.sweep_margin(ev())
        return trade
