"""Market inputs for pricing and hedging at one date of the research calendar (point in time).

`Market(research, i)` reads only what was known at session i: closes, FRED series as published (Panel.macro), the
dividends paid in the year before i, highs and lows for the spread estimate, volumes for liquidity. The live
hedge uses i = the latest session; the historical evaluation uses earlier i. Every input reports its source and
the date it is from, and anything older than its staleness limit is returned as None (never guessed)."""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from ..engine.portfolio import fx_series
from . import pricing as px

# Treasury constant-maturity tenors (years) and their FRED series
CMT = [(1 / 12, "DGS1MO"), (0.25, "DGS3MO"), (1.0, "DGS1"), (2.0, "DGS2"), (5.0, "DGS5"), (7.0, "DGS7"), (10.0, "DGS10"),
       (20.0, "DGS20"), (30.0, "DGS30")]
# Cboe implied-volatility indices (FRED) by underlying: [(maturity in years, series)]
VOL_INDEX = {
    "SPX": [(30 / 365, "VIXCLS"), (93 / 365, "VXVCLS")], "SPY": [(30 / 365, "VIXCLS"), (93 / 365, "VXVCLS")],
    "VTI": [(30 / 365, "VIXCLS"), (93 / 365, "VXVCLS")],
    "NDX": [(30 / 365, "VXNCLS")], "QQQ": [(30 / 365, "VXNCLS")],
    "RUT": [(30 / 365, "RVXCLS")], "IWM": [(30 / 365, "RVXCLS")],
    "DJI": [(30 / 365, "VXDCLS")], "DIA": [(30 / 365, "VXDCLS")],
    "GLD": [(30 / 365, "GVZCLS")], "USO": [(30 / 365, "OVXCLS")],
    "EEM": [(30 / 365, "VXEEMCLS")], "EWZ": [(30 / 365, "VXEWZCLS")],
    "AAPL": [(30 / 365, "VXAPLCLS")], "AMZN": [(30 / 365, "VXAZNCLS")], "GOOGL": [(30 / 365, "VXGOGCLS")],
    "GS": [(30 / 365, "VXGSCLS")], "IBM": [(30 / 365, "VXIBMCLS")],
}
# overnight / short policy rates by currency (FRED)
SHORT_RATE = {"USD": "DGS3MO", "EUR": "ECBDFR", "GBP": "IUDSOIA", "JPY": "IRSTCI01JPM156N", "CAD": "IRSTCI01CAM156N",
              "CHF": "IRSTCI01CHM156N", "AUD": "IRSTCI01AUM156N"}
DIVIDEND_PROXY = {"SPX": "SPY", "NDX": "QQQ", "RUT": "IWM", "DJI": "DIA"}
STALE = {"price": 5, "daily_macro": 10, "monthly_macro": 100}   # sessions for prices; calendar days of observation age for FRED


class Market:
    def __init__(self, research, i: Optional[int] = None):
        self.research = research
        self.panel = research.panel()
        self.store = research.store
        cal = self.panel.calendar()
        self.cal = cal
        self.i = (len(cal) - 1) if i is None else max(0, min(i, len(cal) - 1))
        self.asof = cal[self.i]
        self._memo: Dict[tuple, object] = {}

    # ------------------------------------------------------------------ helpers
    def _m(self, key, fn):
        if key not in self._memo:
            self._memo[key] = fn()
        return self._memo[key]

    def _last(self, series: List[Optional[float]], limit: int) -> Tuple[Optional[float], Optional[str]]:
        for k in range(self.i, max(-1, self.i - limit - 1), -1):
            if series[k] is not None:
                return series[k], self.cal[k]
        return None, None

    def macro(self, series_id: str, limit: Optional[int] = None) -> Tuple[Optional[float], Optional[str]]:
        """The value of a FRED series as known at session i, and the session it became known; None when older than
        the staleness limit."""
        from ..data.universe import FRED_SERIES
        freq = (FRED_SERIES.get(series_id) or {}).get("freq", "daily")
        age = limit if limit is not None else (STALE["monthly_macro"] if freq == "monthly" else STALE["daily_macro"])
        # Panel.macro drops observations older than `age` calendar days (by observation date), so a series that has
        # stopped updating becomes None instead of being carried forward
        s = self._m(("macro", series_id, age), lambda: self.panel.macro(series_id, max_age_days=age))
        return self._last(s, 0)

    # ------------------------------------------------------------------ prices
    def close(self, asset_id: str) -> Tuple[Optional[float], Optional[str]]:
        s = self._m(("close", asset_id), lambda: self.panel.series(asset_id, "close", max_fill=0))
        return self._last(s, STALE["price"])

    def price(self, asset_id: str) -> Optional[float]:
        return self.close(asset_id)[0]

    def fx(self, ccy: str) -> Optional[float]:
        """USD per unit of ccy at i."""
        if not ccy or ccy == "USD":
            return 1.0
        s = self._m(("fx", ccy), lambda: fx_series(self.panel, ccy))
        return self._last(s, 10)[0]

    def usd_returns(self, asset_id: str) -> List[Optional[float]]:
        """Daily simple returns in USD (adjusted close × rate) over the whole calendar (use only indices <= i)."""
        def build():
            p = self.panel.series(asset_id, "adj_close")
            ccy = (self.store.asset(asset_id) or {}).get("currency") or "USD"
            fx = fx_series(self.panel, ccy) if ccy != "USD" else None
            usd = [(p[k] * fx[k]) if (fx is not None and p[k] is not None and fx[k] is not None) else (p[k] if fx is None else None) for k in range(len(p))]
            return [None] + [(usd[k] / usd[k - 1] - 1) if usd[k] is not None and usd[k - 1] else None for k in range(1, len(usd))]
        return self.research._memo(f"usdret:{asset_id}", build) if hasattr(self.research, "_memo") else self._m(("r", asset_id), build)

    # ------------------------------------------------------------------ rates
    def curve(self) -> Optional[px.Curve]:
        def build():
            pts = {}
            for t, sid in CMT:
                v, _ = self.macro(sid)
                if v is not None:
                    pts[t] = v / 100.0
            return px.Curve(pts) if len(pts) >= 3 else None
        return self._m(("curve",), build)

    def short_rate(self, ccy: str = "USD") -> Tuple[Optional[float], Optional[str], str]:
        sid = SHORT_RATE.get(ccy)
        if not sid:
            return None, None, "no short-rate series for " + ccy
        v, d = self.macro(sid)
        return (v / 100.0 if v is not None else None), d, sid

    def option_chain(self, underlying: str) -> list:
        """The latest option-chain snapshot of `underlying` on or before this date (empty without a chain source)."""
        return self._m(("chain", underlying), lambda: self.store.option_chain(underlying, self.asof) if hasattr(self.store, "option_chain") else [])

    def cash_rates(self, portfolio_id: str = "main") -> Tuple[float, float]:
        """(idle-cash rate, short-proceeds rate) under the portfolio's cash settings, at this snapshot's bill rate."""
        from ..engine import cash as cashmod
        r, _, _ = self.short_rate("USD")
        return cashmod.rates(cashmod.load(self.store, portfolio_id), r)

    # ------------------------------------------------------------------ dividends, volatility, liquidity
    def div_yield(self, asset_id: str) -> float:
        """Trailing 12-month cash dividends / price (0 when none are recorded) — the same series the ledger marks with."""
        from .series import _div_yield
        return _div_yield(self.panel, self.store, asset_id)[self.i]

    def implied_vol(self, underlying: str, T: float) -> Tuple[Optional[float], Optional[str], Optional[str]]:
        """(σ, source, date) from the Cboe volatility index(es) for this underlying, interpolated in total variance
        across the index maturities; (None, reason, None) when there is no current index."""
        spec = VOL_INDEX.get(underlying)
        if not spec:
            return None, f"no implied-volatility data for {underlying} (option chains are not available to FinSim2)", None
        pts, dates, srcs = [], [], []
        for mat, sid in spec:
            v, d = self.macro(sid)
            if v is not None:
                pts.append((mat, v / 100.0)); dates.append(d); srcs.append(sid)
        if not pts:
            return None, f"no current implied-volatility index for {underlying} ({'/'.join(s for _, s in spec)} missing or stale)", None
        return px.interp_var_time(max(T, 1 / 365), pts), "+".join(srcs), min(dates)

    def vol_index_series(self, underlying: str) -> Optional[List[Optional[float]]]:
        """The 30-day implied-vol index (decimal) on the calendar, as known at each date."""
        spec = VOL_INDEX.get(underlying)
        if not spec:
            return None
        s = self._m(("vi", spec[0][1]), lambda: self.panel.macro(spec[0][1], lag_days=0, max_age_days=10))
        return [(v / 100.0) if v is not None else None for v in s]

    def realized_vol(self, asset_id: str, days: int = 63) -> Optional[float]:
        r = self.usd_returns(asset_id)[max(1, self.i - days + 1):self.i + 1]
        r = [x for x in r if x is not None]
        if len(r) < max(10, days // 2):
            return None
        m = sum(r) / len(r)
        return math.sqrt(sum((x - m) ** 2 for x in r) / (len(r) - 1) * 252)

    def vol_forecast(self, asset_id: str) -> Tuple[Optional[float], str]:
        """Point-in-time volatility forecast: the GARCH(1,1) feature (refitted yearly on 3 years), else EWMA, else
        63-day realised."""
        try:
            f = self.research.features(asset_id)
        except Exception:
            f = {}
        for k, lab in (("garch_vol", "GARCH(1,1)"), ("ewma_vol", "EWMA λ=0.94")):
            s = f.get(k)
            if s:
                v, _ = self._last(s, 5)
                if v:
                    return v, lab
        return self.realized_vol(asset_id), "63-day realised"

    def spread(self, asset_id: str) -> Tuple[Optional[float], str]:
        """Proportional bid/ask spread estimated from daily highs and lows (Corwin & Schultz 2012), median of the last
        21 two-day estimates, floored at 1bp. None for synthetic series without highs and lows."""
        def build():
            hi = self.panel.series(asset_id, "high", max_fill=0)
            lo = self.panel.series(asset_id, "low", max_fill=0)
            ests = []
            for k in range(max(1, self.i - 21), self.i + 1):
                h0, l0, h1, l1 = hi[k - 1], lo[k - 1], hi[k], lo[k]
                if not all(v and v > 0 for v in (h0, l0, h1, l1)) or h0 < l0 or h1 < l1:
                    continue
                beta = math.log(h0 / l0) ** 2 + math.log(h1 / l1) ** 2
                gamma = math.log(max(h0, h1) / min(l0, l1)) ** 2
                den = 3 - 2 * math.sqrt(2)
                alpha = (math.sqrt(2 * beta) - math.sqrt(beta)) / den - math.sqrt(gamma / den)
                s = 2 * (math.exp(alpha) - 1) / (1 + math.exp(alpha))
                ests.append(max(0.0, s))
            if len(ests) < 5:
                return None, "no high/low data"
            ests.sort()
            return max(0.0001, ests[len(ests) // 2]), "Corwin–Schultz high/low estimate (21 days)"
        return self._m(("spread", asset_id), build)

    def adv_usd(self, asset_id: str, days: int = 20) -> Optional[float]:
        """Average daily traded value (volume × close, USD) over the last `days` sessions; None without volume."""
        def build():
            v = self.panel.series(asset_id, "volume", max_fill=0)
            c = self.panel.series(asset_id, "close", max_fill=0)
            fx = self.fx((self.store.asset(asset_id) or {}).get("currency") or "USD") or 0.0
            xs = [v[k] * c[k] * fx for k in range(max(0, self.i - days + 1), self.i + 1) if v[k] and c[k]]
            return sum(xs) / len(xs) if len(xs) >= days // 2 else None
        return self._m(("adv", asset_id), build)

    def regime(self) -> Dict[str, Optional[str]]:
        rg = self.research.regimes()
        return {d: (s[self.i] if s and self.i < len(s) else None) for d, s in rg.items()}
