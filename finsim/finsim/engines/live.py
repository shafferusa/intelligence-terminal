"""Live quotes and immediate fills for a save that tracks the real market.

The daily update still closes the day (settlement, marks, the briefing) at the update time. Between updates the
latest real quote (Yahoo Finance, up to 15 minutes delayed on most exchanges) is shown next to the last close, and a
LIVE ticket fills at it immediately through the ordinary fill logic — spread, impact, participation cap, commission —
so the trade, its settlement and its ledger entries are the same as any other. What is stored is the fill itself
(TRADE_EXECUTED carries the quote, its time and source), never a network result, so replay is exact and offline.

How each instrument is quoted:
  stocks, ETFs, ADRs, REITs, preferreds   directly (the security's own quote)
  listed options                           the model surface repriced at the underlying's live price
  futures                                  the contract's last close moved with its underlying (front-month continuous
                                           quote for commodities, SPY for ES, ZN=F for ZN)
  bonds                                    the last close — cash bonds have no public live quote — labelled as such
  FX                                       the pair's live quote (spot deals with execution LIVE)
"""
from __future__ import annotations

import time
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

from ..clock import MARKET_TZ
from ..domain.models import Bar, Order, Security, Trade
from ..money import D, price as qprice, ZERO

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
        from .realfeed import COMMODITY_SYMBOLS
        feed = self.w.market.real_feed
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
        """Live quotes for several securities in one fetch: {id: quote}. Securities without one are left out."""
        feed = self._feed()
        w = self.w
        secs = [w.securities[i] for i in ids if i in w.securities]
        syms: List[str] = []
        for sec in secs:
            syms.extend(self._symbols_for(sec))
        raw = feed.live(syms, max_age_s) if syms else {}
        out = {}
        for sec in secs:
            q = self._quote_from(sec, raw)
            if q is not None:
                out[sec.id] = q
        return out

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
            return {**base, "price": last, "time": None, "time_ny": "", "age_s": None, "change_pct": 0.0, "method": "last_close", "ref": sec.id, "ref_price": last,
                    "note": "cash bonds have no public live quote: the last close is used"}
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
    def fill(self, order: Order, cause) -> Optional[Trade]:
        """Fill a just-entered LIVE order at the latest quote. A limit that is not marketable keeps working for the next update."""
        from ..world import CommandError
        w = self.w
        pf = w.portfolios[order.portfolio_id]
        sec = w.securities[order.security_id]
        if order.order_type not in ("MARKET", "LIMIT"):
            raise CommandError("a live ticket is MARKET or LIMIT; stops, trailing stops and conditions are instructions for the daily update")
        q = self.quote(sec)
        if q is None:
            raise CommandError(f"no live quote for {sec.id} right now; send it as an instruction for the next update instead")
        lb = w.trading._bar(sec)
        live = qprice(q["price"])
        if sec.is_option:
            bid, ask = qprice(q["bid"]), qprice(q["ask"])
            half = (ask - bid) / 2
            volume = lb.volume
        else:
            rel = (lb.ask - lb.bid) / 2 / lb.close if lb.close else ZERO
            half = qprice(live * rel)
            bid, ask = live - half, live + half
            volume = lb.volume
        base = live
        if order.order_type == "LIMIT":
            lim = order.limit_price
            marketable = (base <= lim) if order.side == "BUY" else (base >= lim)
            if not marketable:
                w.trading._note(order, cause, f"limit {lim} not marketable at the live quote {live} ({q['time_ny']} New York); works at the next update")
                return None
        bar = Bar(w.current_date.isoformat(), live, live, live, live, volume, bid, ask)
        detail = {"quote_time": q.get("time"), "quote_time_ny": q.get("time_ny"), "quote_age_s": q.get("age_s"), "quote_source": q["source"],
                  "quote_method": q["method"], "quote_ref": q.get("ref"), "quote_ref_price": q.get("ref_price"), "last_close": q["last_close"]}
        note = f"live fill at {live} ({q['time_ny']} New York, {q['source']})" if q.get("time") else f"live fill at the last close {live} ({q.get('note', '')})"
        trade = w.trading._fill(order, sec, bar, base, half, "LIVE", cause, note=note, extra_detail=detail)
        if trade is not None and (sec.is_future or sec.is_option):
            # margined instruments: post (or release) initial margin now rather than at the update, as the clearing broker would
            if sec.is_option:
                w.options.recompute_margin(pf, cause)
            w.futures.sweep_margin(cause)
        return trade
