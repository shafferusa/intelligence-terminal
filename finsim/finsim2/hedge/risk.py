"""The universal risk vector.

Every exposure is a dollar P&L per unit move of a factor, so a position's linear P&L is Σ_f R_f·Δf and its
variance is Rᵀ Σ_F R. Factor moves and their units:

    MKT                 S&P 500 (SPY) daily return                     R = beta-dollars (market value × β)
    SEC:<ETF>           sector ETF minus SPY return (spread)           R = dollars × sector beta
    IND:SMH             semiconductors minus technology (SMH − XLK)
    STY:SIZE/VALUE      IWM − SPY, IWD − IWF
    RATE:2Y/5Y/10Y/30Y  change in the constant-maturity Treasury yield, basis points   R = −DV01 by key rate
    REAL:10Y            change in the 10-year TIPS real yield, bp                       R = −real DV01
    CREDIT:IG/HY        change in the ICE BofA option-adjusted spread, bp               R = −CS01
    FX:<CCY>            return of the currency against the dollar                       R = dollars of currency
    CMD:OIL/GAS/GOLD/SILVER/COPPER/AGRI/BROAD   commodity return                        R = dollars of commodity
    CRYPTO              bitcoin return
    VOL                 change in the VIX, index points                                 R = dollars per VIX point
    IDIO:<asset>        the asset's own residual return                                 R = dollars (single-name risk)

Structural exposures come from the instrument (a bond's duration, a currency's notional, a commodity ETF's
underlying). Empirical exposures of equity-like assets come from a ridge regression of the asset's daily USD
returns over the trailing window on the equity factors, shrunk toward an economic prior (market 1, own sector 1,
others 0), followed by a screen of macro factors on the residual (kept only when |t| ≥ 3). Everything is estimated
with data up to session i only."""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

WINDOW = 252
RIDGE = 0.15                     # shrinkage toward the prior, as a fraction of the regressors' sum of squares
MACRO_T = 3.0
KRD = [("RATE:2Y", 2.0, 1.9, "DGS2"), ("RATE:5Y", 5.0, 4.5, "DGS5"), ("RATE:10Y", 10.0, 8.5, "DGS10"), ("RATE:30Y", 30.0, 17.5, "DGS30")]
SECTOR_ETF = {"Information Technology": "XLK", "Financials": "XLF", "Energy": "XLE", "Health Care": "XLV",
              "Consumer Discretionary": "XLY", "Consumer Staples": "XLP", "Industrials": "XLI", "Materials": "XLB",
              "Utilities": "XLU", "Real Estate": "XLRE", "Communication Services": "XLC"}
SEMIS = {"NVDA", "AMD", "AVGO", "INTC", "QCOM", "TXN", "SMH"}
FX_DIRECT = {"EUR": "EURUSD", "GBP": "GBPUSD", "AUD": "AUDUSD", "NZD": "NZDUSD"}
FX_INVERSE = {"JPY": "USDJPY", "CAD": "USDCAD", "CHF": "USDCHF", "CNY": "USDCNY", "MXN": "USDMXN", "INR": "USDINR"}
COMMODITY = {"WTI": "CMD:OIL", "BRENT": "CMD:OIL", "USO": "CMD:OIL", "NATGAS": "CMD:GAS", "UNG": "CMD:GAS", "GOLD": "CMD:GOLD",
             "GLD": "CMD:GOLD", "IAU": "CMD:GOLD", "SILVER": "CMD:SILVER", "SLV": "CMD:SILVER", "COPPER": "CMD:COPPER",
             "CPER": "CMD:COPPER", "CORN": "CMD:AGRI", "WHEAT": "CMD:AGRI", "SOYBEANS": "CMD:AGRI", "COFFEE": "CMD:AGRI",
             "DBA": "CMD:AGRI", "DBC": "CMD:BROAD", "PLATINUM": "CMD:BROAD"}
CRYPTO_SPOT = {"BTC", "ETH", "SOL"}
COUNTRY_FX = {"EWJ": "JPY", "EWG": "EUR", "EWU": "GBP", "INDA": "INR", "FXE": "EUR", "FXY": "JPY", "FXB": "GBP"}
# credit-bearing bond funds: (spread bucket, share of the duration that is spread duration)
CREDIT = {"LQD": ("CREDIT:IG", 1.0), "VCIT": ("CREDIT:IG", 1.0), "CORP_BAA": ("CREDIT:IG", 1.0), "HYG": ("CREDIT:HY", 1.0),
          "JNK": ("CREDIT:HY", 1.0), "EMB": ("CREDIT:HY", 1.0), "AGG": ("CREDIT:IG", 0.3), "BND": ("CREDIT:IG", 0.3),
          "PFF": ("CREDIT:HY", 1.0), "BKLN": ("CREDIT:HY", 1.0), "FLOT": ("CREDIT:IG", 1.0),
          "JAAA": ("CREDIT:IG", 1.0)}
# floating-rate funds: rate duration is near zero but spread duration is not (years)
SPREAD_DURATION = {"BKLN": 2.0, "FLOT": 2.0, "JAAA": 2.5, "PFF": 5.0, "CWB": 2.5}
FLOATING = {"BKLN", "FLOT", "JAAA"}          # coupons reset with short rates: rate duration ≈ 0.2 years
REAL_RATE = {"TIP", "SCHP"}
EQUITY_FACTORS = ["MKT"] + [f"SEC:{e}" for e in SECTOR_ETF.values()] + ["IND:SMH", "STY:SIZE", "STY:VALUE"]
STYLE = ["STY:SIZE", "STY:VALUE"]
# the 11 sector spreads together nearly reproduce the market, so no asset is regressed on all of them: each asset uses
# the market, its own sector (and semiconductors for chip makers) and the two style spreads
MACRO_FACTORS = ["RATE:10Y", "CREDIT:HY", "CMD:OIL", "CMD:GOLD", "CMD:COPPER", "FX:EUR", "FX:JPY", "CRYPTO", "VOL"]
UNITS = {"MKT": "$ per 100% S&P 500 move (beta-dollars)", "RATE": "$ per +1bp", "REAL": "$ per +1bp", "CREDIT": "$ per +1bp spread",
         "FX": "$ per 100% currency move (currency dollars)", "CMD": "$ per 100% commodity move", "CRYPTO": "$ per 100% bitcoin move",
         "VOL": "$ per +1 VIX point", "IDIO": "$ per 100% residual move", "SEC": "$ per 100% sector-spread move",
         "IND": "$ per 100% industry-spread move", "STY": "$ per 100% style-spread move"}
LABELS = {"MKT": "Equity market (beta $)", "IND:SMH": "Semiconductors", "STY:SIZE": "Size (small − large)", "STY:VALUE": "Value (value − growth)",
          "REAL:10Y": "10Y real yield", "CREDIT:IG": "Investment-grade spread", "CREDIT:HY": "High-yield spread", "CRYPTO": "Crypto (bitcoin)",
          "VOL": "Volatility (VIX)", "CMD:OIL": "Oil", "CMD:GAS": "Natural gas", "CMD:GOLD": "Gold", "CMD:SILVER": "Silver",
          "CMD:COPPER": "Copper", "CMD:AGRI": "Agriculture", "CMD:BROAD": "Broad commodities"}


def label(f: str) -> str:
    if f in LABELS:
        return LABELS[f]
    k, _, v = f.partition(":")
    if k == "SEC":
        return {e: s for s, e in SECTOR_ETF.items()}.get(v, v) + " sector"
    if k == "RATE":
        return f"{v} Treasury yield"
    if k == "FX":
        return f"{v} currency"
    if k == "IDIO":
        return f"{v}-specific"
    return f


def unit(f: str) -> str:
    return UNITS.get(f.split(":")[0], "")


# ------------------------------------------------------------------ small linear algebra
def solve(A: List[List[float]], b: List[float]) -> Optional[List[float]]:
    n = len(A)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[p][c]) < 1e-14:
            return None
        M[c], M[p] = M[p], M[c]
        piv = M[c][c]
        for k in range(c, n + 1):
            M[c][k] /= piv
        for r in range(n):
            if r != c and M[r][c]:
                f = M[r][c]
                for k in range(c, n + 1):
                    M[r][k] -= f * M[c][k]
    return [M[r][n] for r in range(n)]


# ------------------------------------------------------------------ factor series
def factor_series(research) -> Dict[str, List[Optional[float]]]:
    """Daily factor moves on the calendar (returns, basis-point changes or VIX points). Memoised per data version."""
    def build():
        from .market import Market
        m = Market(research)
        r = m.usd_returns
        n = len(m.cal)
        out: Dict[str, List[Optional[float]]] = {}

        def spread(a, b):
            ra, rb = r(a), r(b)
            return [(x - y) if x is not None and y is not None else None for x, y in zip(ra, rb)]

        def diff_bp(sid, scale=100.0):
            # same-day alignment (lag 0): a risk model relates the day's yield move to the day's price move; the
            # close-of-day Treasury/ICE/Cboe values are known by the close (forecasting code keeps FRED's lag)
            s = research.panel().macro(sid, lag_days=0, max_age_days=10)
            return [None] + [((s[k] - s[k - 1]) * scale) if s[k] is not None and s[k - 1] is not None else None for k in range(1, n)]
        out["MKT"] = r("SPY")
        for etf in SECTOR_ETF.values():
            out[f"SEC:{etf}"] = spread(etf, "SPY")
        out["IND:SMH"] = spread("SMH", "XLK")
        out["STY:SIZE"] = spread("IWM", "SPY")
        out["STY:VALUE"] = spread("IWD", "IWF")
        for f, _, _, sid in KRD:
            out[f] = diff_bp(sid)
        out["REAL:10Y"] = diff_bp("DFII10")
        hy, ig = diff_bp("BAMLH0A0HYM2"), diff_bp("BAMLC0A0CM")
        baa = diff_bp("BAA10Y")
        out["CREDIT:HY"] = [a if a is not None else c for a, c in zip(hy, baa)]      # Baa−10Y before ICE OAS history (2023-09)
        out["CREDIT:IG"] = [a if a is not None else c for a, c in zip(ig, baa)]
        for ccy, pair in FX_DIRECT.items():
            out[f"FX:{ccy}"] = r(pair)
        for ccy, pair in FX_INVERSE.items():
            s = research.panel().series(pair, "close")
            out[f"FX:{ccy}"] = [None] + [(s[k - 1] / s[k] - 1) if s[k] and s[k - 1] else None for k in range(1, n)]
        for f, a in (("CMD:OIL", "WTI"), ("CMD:GAS", "NATGAS"), ("CMD:GOLD", "GOLD"), ("CMD:SILVER", "SILVER"), ("CMD:COPPER", "COPPER"),
                     ("CMD:BROAD", "DBC")):
            out[f] = r(a)
        ag = [r(a) for a in ("CORN", "WHEAT", "SOYBEANS")]
        out["CMD:AGRI"] = [(sum(v for v in vs if v is not None) / sum(1 for v in vs if v is not None)) if any(v is not None for v in vs) else None
                           for vs in zip(*ag)]
        out["CRYPTO"] = r("BTC")
        vix = research.panel().macro("VIXCLS", lag_days=0, max_age_days=10)
        out["VOL"] = [None] + [(vix[k] - vix[k - 1]) if vix[k] is not None and vix[k - 1] is not None else None for k in range(1, n)]
        return out
    return research._memo("hedge:factors", build)


def _window(xs: List[Optional[float]], i: int, w: int) -> List[Optional[float]]:
    return xs[max(0, i - w + 1):i + 1]


def cov(a: List[Optional[float]], b: List[Optional[float]], min_n: int = 40) -> Optional[float]:
    pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    if len(pairs) < min_n:
        return None
    mx = sum(x for x, _ in pairs) / len(pairs)
    my = sum(y for _, y in pairs) / len(pairs)
    return sum((x - mx) * (y - my) for x, y in pairs) / (len(pairs) - 1)


class RiskModel:
    """Exposures of any spot asset (per dollar of market value) and the factor covariance, at session i."""

    def __init__(self, market, window: int = WINDOW):
        self.m = market
        self.research = market.research
        self.i = market.i
        self.w = window
        self.F = factor_series(self.research)
        self._beta: Dict[str, dict] = {}
        self._idio: Dict[str, List[Optional[float]]] = {}

    def fwin(self, f: str) -> List[Optional[float]]:
        if f.startswith("IDIO:"):
            return _window(self._idio.get(f[5:], []), self.i, self.w)
        return _window(self.F.get(f) or [], self.i, self.w)

    # ------------------------------------------------------------------ per-asset exposures (per $1 of value)
    def unit_exposures(self, asset_id: str) -> dict:
        """{"exposures": {factor: $ per $1 of market value}, "kind": {factor: "structural"|"empirical"}, "stats": …}."""
        if asset_id in self._beta:
            return self._beta[asset_id]
        a = self.research.store.asset(asset_id) or {"id": asset_id, "asset_class": "EQUITY"}
        cls, meta = a.get("asset_class"), a.get("meta") or {}
        exp: Dict[str, float] = {}
        kind: Dict[str, str] = {}
        stats: dict = {}

        def put(f, v, k):
            if v:
                exp[f] = exp.get(f, 0.0) + v
                kind[f] = k
        dur = a.get("duration")
        ccy = a.get("currency") or "USD"
        if cls in ("TREASURY", "CORP_BOND") or (dur and a.get("sector") == "Fixed Income") or asset_id in CREDIT or asset_id in REAL_RATE:
            d = float(dur or meta.get("duration") or 0.0)
            rate_d = d
            if asset_id in FLOATING:
                rate_d = float(meta.get("rate_duration", 0.2))
            if asset_id in REAL_RATE:
                put("REAL:10Y", -d * 1e-4, "structural")
            elif rate_d:
                for f, w in krd_weights(rate_d).items():
                    put(f, -rate_d * w * 1e-4, "structural")
            if asset_id in CREDIT:
                bucket, share = CREDIT[asset_id]
                sd = SPREAD_DURATION.get(asset_id, d * share)
                put(bucket, -sd * 1e-4, "structural")
            stats["model"] = "structural (duration; spread duration)"
            self._idio[asset_id] = self._residual(asset_id, exp)
            put(f"IDIO:{asset_id}", 1.0, "structural")
        elif cls == "FX":
            base, quote = (meta.get("base_currency") or asset_id[:3]), ccy
            if asset_id == "DXY":
                for c, wgt in (("EUR", -0.576), ("JPY", -0.136), ("GBP", -0.119), ("CAD", -0.091), ("CHF", -0.036)):
                    put(f"FX:{c}", wgt, "structural")
            elif base == "USD":
                put(f"FX:{quote}", -1.0, "structural")            # long dollars = short the quote currency
            else:
                put(f"FX:{base}", 1.0, "structural")
            stats["model"] = "structural (currency notional)"
        elif asset_id in COMMODITY and cls in ("COMMODITY", "ETF"):
            put(COMMODITY[asset_id], 1.0, "structural")
            self._idio[asset_id] = self._residual(asset_id, exp)
            put(f"IDIO:{asset_id}", 1.0, "structural")
            stats["model"] = "structural (commodity notional)"
        elif asset_id in CRYPTO_SPOT or cls == "CRYPTO":
            if asset_id == "BTC":
                put("CRYPTO", 1.0, "structural")
            else:
                b = self._simple_beta(asset_id, "CRYPTO")
                put("CRYPTO", b if b is not None else 1.0, "empirical")
                self._idio[asset_id] = self._residual(asset_id, exp)
                put(f"IDIO:{asset_id}", 1.0, "empirical")
            stats["model"] = "structural (bitcoin) / regression on bitcoin"
        else:
            # equity-like: ridge on the equity factors toward the prior, then a macro screen on the residual
            fx_ccy = COUNTRY_FX.get(asset_id) or (ccy if ccy != "USD" else None)
            y = list(self.m.usd_returns(asset_id))
            if fx_ccy and self.F.get(f"FX:{fx_ccy}"):
                put(f"FX:{fx_ccy}", 1.0, "structural")
                fxr = self.F[f"FX:{fx_ccy}"]
                y = [(v - x) if v is not None and x is not None else None for v, x in zip(y, fxr)]
            lev = meta.get("leverage")
            prior = {"MKT": float(lev) if lev else 1.0}
            se = SECTOR_ETF.get(a.get("sector") or "")
            regs = ["MKT"] + STYLE
            if se:
                prior[f"SEC:{se}"] = 1.0
                regs.append(f"SEC:{se}")
            if asset_id in SEMIS:
                prior["IND:SMH"] = 1.0
                regs.append("IND:SMH")
            if asset_id == "SPY":
                betas, st = {"MKT": 1.0}, {"r2": 1.0, "n": self.w}
            else:
                betas, st = self._ridge(y, regs, prior)
            stats.update(st)
            for f, b in betas.items():
                put(f, b, "empirical")
            res = self._resid_series(y, betas)
            for f in MACRO_FACTORS:
                if f in exp or (f.startswith("FX:") and fx_ccy):
                    continue
                b, t = self._screen(res, f)
                if b is not None and abs(t) >= MACRO_T:
                    put(f, b, "empirical")
                    res = [(v - b * x) if v is not None and x is not None else v for v, x in zip(res, self.F[f])]
                    stats.setdefault("macro", {})[f] = {"beta": b, "t": t}
            self._idio[asset_id] = res
            if asset_id != "SPY":
                put(f"IDIO:{asset_id}", 1.0, "empirical")
            stats["model"] = "ridge regression toward the economic prior + macro screen (|t| ≥ 3)"
        out = {"exposures": exp, "kind": kind, "stats": stats}
        self._beta[asset_id] = out
        return out

    def _residual(self, asset_id: str, exp: Dict[str, float]) -> List[Optional[float]]:
        y = self.m.usd_returns(asset_id)
        out = []
        for k in range(len(y)):
            v = y[k]
            if v is None:
                out.append(None)
                continue
            for f, b in exp.items():
                x = (self.F.get(f) or [None] * len(y))[k]
                if x is None:
                    v = None
                    break
                v -= b * x
            out.append(v)
        return out

    def _resid_series(self, y, betas):
        n = len(y)
        cols = {f: self.F.get(f) or [None] * n for f in betas}
        out = []
        for k in range(n):
            v = y[k]
            if v is None:
                out.append(None)
                continue
            for f, b in betas.items():
                x = cols[f][k]
                if x is None:
                    if abs(b) > 0:
                        v = None
                        break
                    continue
                v -= b * x
            out.append(v)
        return out

    def _simple_beta(self, asset_id, f):
        y = _window(self.m.usd_returns(asset_id), self.i, self.w)
        x = self.fwin(f)
        c, vx = cov(y, x), cov(x, x)
        return (c / vx) if c is not None and vx else None

    def _screen(self, res, f) -> Tuple[Optional[float], float]:
        y = _window(res, self.i, self.w)
        x = self.fwin(f)
        pairs = [(a, b) for a, b in zip(x, y) if a is not None and b is not None]
        if len(pairs) < 120:
            return None, 0.0
        n = len(pairs)
        mx = sum(a for a, _ in pairs) / n
        my = sum(b for _, b in pairs) / n
        sxx = sum((a - mx) ** 2 for a, _ in pairs)
        sxy = sum((a - mx) * (b - my) for a, b in pairs)
        if sxx <= 0:
            return None, 0.0
        beta = sxy / sxx
        syy = sum((b - my) ** 2 for _, b in pairs)
        if syy <= 1e-12 * n or (sxy * sxy) / (sxx * syy) < 0.02:     # nothing left to explain, or < 2% of the residual
            return None, 0.0
        sse = sum((b - my - beta * (a - mx)) ** 2 for a, b in pairs)
        se = math.sqrt(sse / (n - 2) / sxx) if n > 2 and sse > 0 else None
        return beta, (beta / se if se else 0.0)

    def _ridge(self, y, factors, prior) -> Tuple[Dict[str, float], dict]:
        yw = _window(y, self.i, self.w)
        cols = {f: self.fwin(f) for f in factors}
        use = [f for f in factors if sum(1 for v in cols[f] if v is not None) >= 0.8 * len(yw) or f in prior]
        rows = [k for k in range(len(yw)) if yw[k] is not None and all(cols[f][k] is not None for f in use)]
        if len(rows) < 60:
            use = [f for f in use if f in prior]
            rows = [k for k in range(len(yw)) if yw[k] is not None and all(cols[f][k] is not None for f in use)]
        if len(rows) < 30 or not use:
            return dict(prior), {"n": len(rows), "note": "too few observations: the prior is used"}
        n, p = len(rows), len(use)
        mu = {f: sum(cols[f][k] for k in rows) / n for f in use}
        my = sum(yw[k] for k in rows) / n
        X = [[cols[f][k] - mu[f] for f in use] for k in rows]
        Y = [yw[k] - my for k in rows]
        XtX = [[sum(X[r][a] * X[r][b] for r in range(n)) for b in range(p)] for a in range(p)]
        XtY = [sum(X[r][a] * Y[r] for r in range(n)) for a in range(p)]
        pri = [prior.get(f, 0.0) for f in use]
        for a in range(p):
            lam = RIDGE * XtX[a][a]
            XtX[a][a] += lam
            XtY[a] += lam * pri[a]
        b = solve(XtX, XtY)
        if b is None:
            return dict(prior), {"n": n, "note": "singular: the prior is used"}
        fit = [sum(b[a] * X[r][a] for a in range(p)) for r in range(n)]
        sst = sum(v * v for v in Y)
        sse = sum((Y[r] - fit[r]) ** 2 for r in range(n))
        return {f: b[a] for a, f in enumerate(use)}, {"n": n, "r2": 1 - sse / sst if sst > 0 else None, "prior": prior}

    # ------------------------------------------------------------------ covariance
    def covariance(self, factors: List[str]) -> Dict[Tuple[str, str], float]:
        """Daily covariance of factor moves over the window (pairwise complete; 0 where there is too little data)."""
        out = {}
        wins = {f: self.fwin(f) for f in factors}
        for a_i, a in enumerate(factors):
            for b in factors[a_i:]:
                c = cov(wins[a], wins[b])
                if c is None and a == b:
                    c = 0.0
                out[(a, b)] = out[(b, a)] = c or 0.0
        return out


def krd_weights(duration: float) -> Dict[str, float]:
    """Split a duration across the 2/5/10/30-year key rates by where it falls between their durations."""
    pts = [(f, d) for f, _, d, _ in KRD]
    if duration <= pts[0][1]:
        return {pts[0][0]: 1.0}
    for (f0, d0), (f1, d1) in zip(pts, pts[1:]):
        if duration <= d1:
            w = (duration - d0) / (d1 - d0)
            return {f0: 1 - w, f1: w}
    return {pts[-1][0]: 1.0}


def variance(R: Dict[str, float], C: Dict[Tuple[str, str], float]) -> float:
    fs = list(R)
    return sum(R[a] * R[b] * C.get((a, b), 0.0) for a in fs for b in fs)
