"""FinSim2's analytics workspace: the equation library, a scoreboard of every tradeable asset, and one asset in depth.

The scoreboard runs `finsim.quant.asset_metrics` over each asset's closing prices (up to two years), against the S&P 500
ETF as the market and the dollar policy rate as the risk-free rate, and adds the Shaffer Score column from the plug-in.
It is computed once per market day and cached."""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from . import shaffer_score

HISTORY = 520            # closes per asset (about two years)
MARKET = "SPY"

CLASS_LABEL = {"EQUITY": "Stock", "ETF": "ETF", "REIT": "REIT", "ADR": "ADR", "GOVT_BOND": "Government bond", "CORP_BOND": "Corporate bond",
               "MBS_TBA": "Agency MBS", "STRUCTURED": "Structured credit", "CRYPTO": "Crypto", "FX": "Currency", "INDEX": "Index"}


def _f(x) -> Optional[float]:
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


class Analytics:
    def __init__(self, service):
        self.s = service
        self._cache: Dict[str, Tuple[tuple, Dict]] = {}

    # ------------------------------------------------------------------ what is tradeable, and its price history
    @staticmethod
    def tradeable(w) -> List:
        out = []
        for s in w.securities.values():
            if s.is_option or getattr(s, "expired", False) or getattr(s, "delisted", False) or getattr(s, "defaulted", False):
                continue
            if len(w.market.history.get(s.id) or []) < 2:
                continue
            out.append(s)
        return out

    @staticmethod
    def closes(w, sid: str) -> List[float]:
        if sid.startswith("FX:"):
            return [float(h[1]) for h in (w.market.fx.history.get(sid[3:]) or [])][-HISTORY:]
        return [float(b.close) for b in (w.market.history.get(sid) or [])][-HISTORY:]

    @staticmethod
    def dates(w, sid: str) -> List[str]:
        if sid.startswith("FX:"):
            return [h[0] for h in (w.market.fx.history.get(sid[3:]) or [])][-HISTORY:]
        return [b.date for b in (w.market.history.get(sid) or [])][-HISTORY:]

    def asset_info(self, w, sec) -> Dict:
        if isinstance(sec, str):             # a currency
            c = sec[3:]
            return {"id": sec, "name": f"{c} (US dollars per {c})", "asset_class": "FX", "class_label": "Currency", "sector": "Currencies", "country": "",
                    "currency": "USD", "last": _f(w.market.fx.spot.get(c)), "rating": None, "coupon": None, "maturity": None}
        cls = sec.asset_class
        if sec.is_future:
            label = "Future: " + ("commodity" if (sec.underlying_class or "").startswith("COMMODITY") else (sec.underlying_class or "financial").lower().replace("_", " "))
        else:
            label = CLASS_LABEL.get(cls, cls.replace("_", " ").title())
        h = w.market.history.get(sec.id) or []
        return {"id": sec.id, "name": sec.name, "asset_class": ("FUTURE" if sec.is_future else cls), "class_label": label, "sector": sec.sector, "country": sec.country,
                "currency": sec.currency, "last": _f(h[-1].close) if h else None, "rating": sec.rating, "coupon": _f(sec.coupon) if sec.coupon is not None else None,
                "maturity": sec.maturity, "underlying": sec.underlying if sec.is_future else None}

    # ------------------------------------------------------------------ the scoreboard
    def scoreboard(self, wid: str) -> Dict:
        from finsim.quant.asset_metrics import asset_metrics, METRIC_INFO
        w = self.s.world(wid)
        key = (w.current_date.isoformat(), len(w.market.history.get(MARKET) or []), shaffer_score.status()["version"])
        hit = self._cache.get(wid)
        if hit and hit[0] == key:
            return hit[1]
        rf = float(w.market.curve().policy_rate)
        mkt = self.closes(w, MARKET)
        version, fn, source = shaffer_score.load()
        rows = []
        assets = self.tradeable(w) + ["FX:" + c for c in sorted(w.market.fx.history) if c != "USD" and w.market.fx.history.get(c)]
        for sec in assets:
            sid = sec if isinstance(sec, str) else sec.id
            info = self.asset_info(w, sec)
            m = asset_metrics(self.closes(w, sid), mkt, rf)
            info["shaffer"] = shaffer_score.safe_score(fn, m, info) if version is not None else None
            rows.append({**info, **{k: (v if not isinstance(v, float) or math.isfinite(v) else None) for k, v in m.items()}})
        out = {"date": w.current_date.isoformat(), "market": MARKET, "rf": rf, "count": len(rows), "rows": rows, "metric_info": METRIC_INFO,
               "shaffer": {**shaffer_score.status(), "scored": sum(1 for r in rows if r["shaffer"] is not None)}}
        self._cache[wid] = (key, out)
        return out

    # ------------------------------------------------------------------ one asset in depth
    def asset(self, wid: str, sid: str) -> Dict:
        from finsim.quant.asset_metrics import asset_metrics, METRIC_INFO
        from finsim.quant.catalog import catalog
        w = self.s.world(wid)
        if sid.startswith("FX:"):
            if sid[3:] not in w.market.fx.history:
                raise KeyError(sid)
            sec = sid
        else:
            sec = w.securities.get(sid)
            if sec is None:
                raise KeyError(sid)
        rf = float(w.market.curve().policy_rate)
        closes = self.closes(w, sid)
        m = asset_metrics(closes, self.closes(w, MARKET), rf)
        info = self.asset_info(w, sec)
        version, fn, _ = shaffer_score.load()
        info["shaffer"] = shaffer_score.safe_score(fn, m, info) if version is not None else None
        eqs = [e for e in catalog()["equations"] if e.get("metric")]
        applied = [{"id": e["id"], "name": e["name"], "metric": e["metric"], "value": m.get(e["metric"])} for e in eqs]
        return {"asset": info, "metrics": m, "metric_info": METRIC_INFO, "applied": applied, "rf": rf, "market": MARKET,
                "series": {"dates": self.dates(w, sid), "closes": closes}, "shaffer": shaffer_score.status()}
