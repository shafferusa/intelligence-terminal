"""FeatureEngine: point-in-time features for one asset on the common calendar.

Every value at index t uses only data dated on or before calendar date t (macro series by their publication date,
fundamentals by their SEC filing date). Rolling statistics use prefix sums so a 30-year daily history costs a
second or two in pure Python. Names and families are the contract in ARCHITECTURE.md.
"""
from __future__ import annotations

import bisect
import math
from collections import deque
from typing import Callable, Dict, List, Optional, Sequence

from .align import Panel, growth_26w, sahm_gap, yoy_log

Series = List[Optional[float]]
ANN = 252

# name -> (family, label, description)
FEATURES: Dict[str, tuple] = {
    "ret_1d": ("Returns", "1-day return", "log return over the last session"),
    "ret_1w": ("Returns", "1-week return", "log return over 5 sessions"),
    "ret_1m": ("Returns", "1-month return", "log return over 21 sessions"),
    "ret_3m": ("Returns", "3-month return", "log return over 63 sessions"),
    "ret_6m": ("Returns", "6-month return", "log return over 126 sessions"),
    "ret_12m": ("Returns", "12-month return", "log return over 252 sessions"),
    "excess_3m": ("Returns", "3-month excess return vs SPY", "3-month log return minus the S&P 500 ETF's"),
    "mom_12_1": ("Momentum", "12-1 momentum", "return from 12 months ago to 1 month ago"),
    "rel_strength_6m": ("Momentum", "6-month relative strength", "6-month return minus SPY's"),
    "ma_cross": ("Momentum", "50/200-day trend", "50-day average over 200-day average, minus 1"),
    "z_20": ("Statistics", "20-day z-score", "price versus its 20-day mean, in standard deviations"),
    "z_50": ("Statistics", "50-day z-score", "price versus its 50-day mean, in standard deviations"),
    "dist_ma200": ("Statistics", "Distance from 200-day average", "log price minus log 200-day average"),
    "skew_60": ("Statistics", "60-day skewness", "skewness of daily returns"),
    "kurt_60": ("Statistics", "60-day excess kurtosis", "fat-tailedness of daily returns"),
    "pctile_252": ("Statistics", "Position in 1-year range", "0 = at the 1-year low, 1 = at the high"),
    "vol_20": ("Risk", "20-day volatility", "annualised standard deviation of daily returns"),
    "vol_60": ("Risk", "60-day volatility", "annualised standard deviation of daily returns"),
    "downside_vol_60": ("Risk", "60-day downside volatility", "annualised root mean square of negative returns"),
    "sharpe_252": ("Risk", "1-year Sharpe ratio", "excess return over the 3-month bill per unit of volatility"),
    "sortino_252": ("Risk", "1-year Sortino ratio", "excess return per unit of downside volatility"),
    "drawdown_252": ("Risk", "Drawdown from 1-year high", "price over its 1-year high, minus 1"),
    "beta_252": ("Risk", "1-year beta to SPY", "slope of daily returns on the S&P 500 ETF"),
    "corr_252": ("Risk", "1-year correlation to SPY", "correlation of daily returns"),
    "alpha_252": ("Risk", "1-year alpha", "annualised intercept of the market regression"),
    "idio_vol_252": ("Risk", "1-year idiosyncratic volatility", "volatility not explained by the market"),
    "ewma_vol": ("Volatility", "EWMA volatility", "RiskMetrics λ = 0.94, annualised"),
    "garch_vol": ("Volatility", "GARCH(1,1) volatility", "next-day forecast, parameters refitted yearly on 3 years"),
    "vol_ratio": ("Volatility", "Volatility ratio", "20-day volatility over 1-year volatility"),
    "vol_of_vol": ("Volatility", "Volatility of volatility", "standard deviation of 20-day volatility over 6 months"),
    "vol_pctile": ("Volatility", "Volatility percentile", "20-day volatility's percentile over 3 years"),
    "rsi_14": ("Technical", "RSI (14)", "Wilder relative strength index"),
    "macd": ("Technical", "MACD", "(12-day EMA − 26-day EMA) / price"),
    "bb_pctb": ("Technical", "Bollinger %b", "position within the 20-day ±2σ band"),
    "volume_z": ("Technical", "Volume z-score", "log volume versus its 60-day average"),
    "ar1_63": ("TimeSeries", "AR(1) of returns, 3 months", "first-order autoregressive coefficient of daily returns"),
    "acf1_252": ("TimeSeries", "Lag-1 autocorrelation, 1 year", "autocorrelation of daily returns"),
    "half_life": ("TimeSeries", "Mean-reversion half-life", "Ornstein-Uhlenbeck half-life of log price over 1 year, days"),
    "adf_t": ("TimeSeries", "ADF statistic", "augmented Dickey-Fuller t on log price over 1 year (month-end)"),
    "value_5y": ("Valuation", "5-year price value", "minus the z-score of log price versus its 5-year mean"),
    "earnings_yield": ("Valuation", "Earnings yield", "trailing 12-month EPS / price"),
    "pe_rel_5y": ("Valuation", "P/E versus own history", "z-score of P/E versus its 5-year history"),
    "eps_growth_yoy": ("Fundamentals", "EPS growth, year on year", "trailing 12-month EPS growth"),
    "rev_growth_yoy": ("Fundamentals", "Revenue growth, year on year", "trailing 12-month revenue growth"),
    "net_margin": ("Fundamentals", "Net margin", "trailing 12-month net income / revenue"),
    "y10": ("Rates", "10-year Treasury yield", "FRED DGS10"),
    "d_y10_3m": ("Rates", "10-year yield change, 3 months", "change in the 10-year yield over 63 sessions"),
    "slope_10y3m": ("Rates", "Yield curve (10Y − 3M)", "FRED T10Y3M"),
    "d_slope_3m": ("Rates", "Curve change, 3 months", "change in 10Y − 3M over 63 sessions"),
    "real_y10": ("Rates", "10-year real yield", "FRED DFII10 (TIPS)"),
    "breakeven_10y": ("Rates", "Breakeven inflation (5-year)", "FRED T5YIE"),
    "rate_beta_252": ("Rates", "Rate sensitivity", "beta of daily returns to daily 10-year yield changes"),
    "credit_spread": ("Credit", "Credit spread (Baa − 10Y)", "FRED BAA10Y"),
    "d_credit_3m": ("Credit", "Credit spread change, 3 months", "change over 63 sessions"),
    "vix": ("Macro", "VIX", "implied volatility of the S&P 500"),
    "d_vix_1m": ("Macro", "VIX change, 1 month", "change over 21 sessions"),
    "dollar_mom_3m": ("Macro", "Dollar momentum, 3 months", "log change of the dollar index"),
    "oil_mom_3m": ("Macro", "Oil momentum, 3 months", "log change of WTI"),
    "gold_mom_3m": ("Macro", "Gold momentum, 3 months", "log change of gold"),
    "cpi_yoy": ("Macro", "Inflation (CPI, y/y)", "as published (45-day lag)"),
    "unemp_gap": ("Macro", "Unemployment gap (Sahm)", "3-month average unemployment minus its 12-month low"),
    "nfci": ("Macro", "Financial conditions (NFCI)", "Chicago Fed; above zero = tighter than average"),
    "fed_bs_growth": ("Macro", "Fed balance sheet growth, 26 weeks", "liquidity"),
    "dollar_beta_252": ("Macro", "Dollar sensitivity", "beta of daily returns to the dollar index"),
}
FAMILIES = sorted({v[0] for v in FEATURES.values()})
MACRO_FEATURES = [k for k, v in FEATURES.items() if v[0] in ("Rates", "Credit", "Macro") and k not in ("rate_beta_252", "dollar_beta_252")]


def family(name: str) -> str:
    return FEATURES.get(name, ("Other",))[0]


def label(name: str) -> str:
    return FEATURES.get(name, (None, name))[1]


# ------------------------------------------------------------------ rolling primitives (None-aware)
def log_returns(p: Series) -> Series:
    out: Series = [None] * len(p)
    for i in range(1, len(p)):
        a, b = p[i - 1], p[i]
        if a is not None and b is not None and a > 0 and b > 0:
            out[i] = math.log(b / a)
    return out


def lag_ret(p: Series, k: int) -> Series:
    out: Series = [None] * len(p)
    for i in range(k, len(p)):
        a, b = p[i - k], p[i]
        if a is not None and b is not None and a > 0 and b > 0:
            out[i] = math.log(b / a)
    return out


def diff(x: Series, k: int) -> Series:
    out: Series = [None] * len(x)
    for i in range(k, len(x)):
        if x[i] is not None and x[i - k] is not None:
            out[i] = x[i] - x[i - k]
    return out


def _prefix(xs: Series, power: int = 1):
    s = [0.0] * (len(xs) + 1)
    c = [0] * (len(xs) + 1)
    for i, v in enumerate(xs):
        s[i + 1] = s[i] + (v ** power if v is not None else 0.0)
        c[i + 1] = c[i] + (1 if v is not None else 0)
    return s, c


def roll_mean(xs: Series, w: int, minp: Optional[int] = None) -> Series:
    minp = minp or max(2, int(w * 0.8))
    s, c = _prefix(xs)
    out: Series = [None] * len(xs)
    for i in range(len(xs)):
        lo = max(0, i + 1 - w)
        n = c[i + 1] - c[lo]
        if n >= minp and xs[i] is not None:
            out[i] = (s[i + 1] - s[lo]) / n
    return out


def roll_std(xs: Series, w: int, minp: Optional[int] = None, ddof: int = 1) -> Series:
    minp = minp or max(3, int(w * 0.8))
    s1, c = _prefix(xs)
    s2, _ = _prefix(xs, 2)
    out: Series = [None] * len(xs)
    for i in range(len(xs)):
        lo = max(0, i + 1 - w)
        n = c[i + 1] - c[lo]
        if n >= minp and xs[i] is not None:
            m = (s1[i + 1] - s1[lo]) / n
            v = ((s2[i + 1] - s2[lo]) - n * m * m) / max(1, n - ddof)
            out[i] = math.sqrt(v) if v > 0 else 0.0
    return out


def roll_moments(xs: Series, w: int) -> tuple:
    """(skew, excess kurtosis) over a trailing window."""
    minp = max(10, int(w * 0.8))
    p1, c = _prefix(xs)
    p2, _ = _prefix(xs, 2)
    p3, _ = _prefix(xs, 3)
    p4, _ = _prefix(xs, 4)
    sk: Series = [None] * len(xs)
    ku: Series = [None] * len(xs)
    for i in range(len(xs)):
        lo = max(0, i + 1 - w)
        n = c[i + 1] - c[lo]
        if n < minp or xs[i] is None:
            continue
        e1 = (p1[i + 1] - p1[lo]) / n
        e2 = (p2[i + 1] - p2[lo]) / n
        e3 = (p3[i + 1] - p3[lo]) / n
        e4 = (p4[i + 1] - p4[lo]) / n
        m2 = e2 - e1 * e1
        if m2 <= 1e-18:
            continue
        m3 = e3 - 3 * e1 * e2 + 2 * e1 ** 3
        m4 = e4 - 4 * e1 * e3 + 6 * e1 * e1 * e2 - 3 * e1 ** 4
        sk[i] = m3 / m2 ** 1.5
        ku[i] = m4 / (m2 * m2) - 3.0
    return sk, ku


def roll_regress(y: Series, x: Series, w: int, minp: Optional[int] = None) -> Dict[str, Series]:
    """Trailing OLS y = a + b x: slope, intercept, correlation, residual variance (per observation)."""
    minp = minp or max(10, int(w * 0.8))
    n = len(y)
    sx = [0.0] * (n + 1); sy = [0.0] * (n + 1); sxx = [0.0] * (n + 1); syy = [0.0] * (n + 1); sxy = [0.0] * (n + 1); c = [0] * (n + 1)
    for i in range(n):
        a, b = y[i], x[i]
        ok = a is not None and b is not None
        sx[i + 1] = sx[i] + (b if ok else 0.0)
        sy[i + 1] = sy[i] + (a if ok else 0.0)
        sxx[i + 1] = sxx[i] + (b * b if ok else 0.0)
        syy[i + 1] = syy[i] + (a * a if ok else 0.0)
        sxy[i + 1] = sxy[i] + (a * b if ok else 0.0)
        c[i + 1] = c[i] + (1 if ok else 0)
    slope: Series = [None] * n; icpt: Series = [None] * n; corr: Series = [None] * n; resv: Series = [None] * n; vary: Series = [None] * n
    for i in range(n):
        lo = max(0, i + 1 - w)
        k = c[i + 1] - c[lo]
        if k < minp:
            continue
        mx = (sx[i + 1] - sx[lo]) / k
        my = (sy[i + 1] - sy[lo]) / k
        vx = (sxx[i + 1] - sxx[lo]) / k - mx * mx
        vy = (syy[i + 1] - syy[lo]) / k - my * my
        cxy = (sxy[i + 1] - sxy[lo]) / k - mx * my
        if vx <= 1e-18:
            continue
        b = cxy / vx
        slope[i] = b
        icpt[i] = my - b * mx
        vary[i] = vy
        corr[i] = cxy / math.sqrt(vx * vy) if vy > 1e-18 else None
        resv[i] = max(0.0, vy - b * b * vx)
    return {"slope": slope, "intercept": icpt, "corr": corr, "resvar": resv, "vary": vary}


def roll_max(xs: Series, w: int) -> Series:
    out: Series = [None] * len(xs)
    dq: deque = deque()
    for i, v in enumerate(xs):
        while dq and dq[0] <= i - w:
            dq.popleft()
        if v is not None:
            while dq and xs[dq[-1]] <= v:
                dq.pop()
            dq.append(i)
        if dq and v is not None:
            out[i] = xs[dq[0]]
    return out


def roll_min(xs: Series, w: int) -> Series:
    neg = [(-v if v is not None else None) for v in xs]
    return [(-v if v is not None else None) for v in roll_max(neg, w)]


def roll_pctile(xs: Series, w: int, minp: Optional[int] = None) -> Series:
    """Percentile (0..1) of the current value within the trailing window."""
    minp = minp or max(20, w // 4)
    out: Series = [None] * len(xs)
    window: List[float] = []
    q: deque = deque()
    for i, v in enumerate(xs):
        if v is not None:
            bisect.insort(window, v)
        q.append(v)
        if len(q) > w:
            old = q.popleft()
            if old is not None:
                del window[bisect.bisect_left(window, old)]
        if v is not None and len(window) >= minp:
            lo = bisect.bisect_left(window, v)
            hi = bisect.bisect_right(window, v)
            out[i] = ((lo + hi) / 2) / len(window)
    return out


def ema(xs: Series, span: int) -> Series:
    a = 2.0 / (span + 1)
    out: Series = [None] * len(xs)
    m = None
    for i, v in enumerate(xs):
        if v is None:
            out[i] = m
            continue
        m = v if m is None else a * v + (1 - a) * m
        out[i] = m
    return out


def rsi(p: Series, n: int = 14) -> Series:
    out: Series = [None] * len(p)
    ag = al = None
    k = 0
    for i in range(1, len(p)):
        if p[i] is None or p[i - 1] is None:
            continue
        ch = p[i] - p[i - 1]
        g, l = max(ch, 0.0), max(-ch, 0.0)
        k += 1
        if ag is None:
            ag, al = g, l
        else:
            ag = (ag * (n - 1) + g) / n
            al = (al * (n - 1) + l) / n
        if k >= n:
            out[i] = 100.0 if al == 0 else 100.0 - 100.0 / (1 + ag / al)
    return out


def combine(a: Series, b: Series, fn: Callable) -> Series:
    return [fn(x, y) if x is not None and y is not None else None for x, y in zip(a, b)]


def sub(a: Series, b: Series) -> Series:
    return combine(a, b, lambda x, y: x - y)


# ------------------------------------------------------------------ slower, periodically refitted features
def garch_vol(r: Series, fit_window: int = 756, refit: int = 252) -> Series:
    """Next-day GARCH(1,1) volatility (annualised). Parameters are refitted every `refit` sessions on the trailing
    `fit_window` returns only, then the variance recursion runs forward with them: no future data."""
    from finsim.quant.volatility import garch_fit
    n = len(r)
    out: Series = [None] * n
    valid = [i for i, v in enumerate(r) if v is not None]
    if len(valid) < fit_window + 5:
        return out
    start = valid[fit_window]
    params = None
    h = None
    mean = 0.0
    for i in range(start, n):
        if params is None or (i - start) % refit == 0:
            hist = [v for v in r[max(0, i - int(fit_window * 1.1)):i] if v is not None][-fit_window:]
            try:
                f = garch_fit(hist, fast=True)
                params = (f["omega"], f["alpha"], f["beta"])
                mean = sum(hist) / len(hist)
                if h is None:
                    h = f["uncond_var"]
            except Exception:
                continue
        x = r[i]
        if x is None or h is None:
            continue
        w_, a_, b_ = params
        h = w_ + a_ * (x - mean) ** 2 + b_ * h
        out[i] = math.sqrt(max(h, 0.0) * ANN)
    return out


def adf_monthly(logp: Series, window: int = 252, every: int = 21) -> Series:
    from finsim.quant.timeseries import adf_test
    n = len(logp)
    out: Series = [None] * n
    cur = None
    for i in range(n):
        if i >= window and (i % every == 0):
            seg = [v for v in logp[i - window + 1:i + 1] if v is not None]
            if len(seg) >= int(window * 0.8):
                try:
                    cur = adf_test(seg, lags=1, trend="c")["t_stat"]
                except Exception:
                    cur = None
        out[i] = cur if logp[i] is not None else None
    return out


def half_life(logp: Series, w: int = 252) -> Series:
    lag = [None] + logp[:-1]
    d = sub(logp, lag)
    reg = roll_regress(d, lag, w)
    out: Series = [None] * len(logp)
    for i, b in enumerate(reg["slope"]):
        if b is not None and -1.0 < b < 0.0:
            k = -math.log(1.0 + b)
            if k > 0:
                out[i] = min(2520.0, math.log(2) / k)
    return out


# ------------------------------------------------------------------ the engine
_MACRO_CACHE: Dict[int, Dict[str, Series]] = {}


def macro_features(panel: Panel) -> Dict[str, Series]:
    """Features shared by every asset (rates, credit, macro), computed once per panel."""
    key = id(panel)
    if key in _MACRO_CACHE:
        return _MACRO_CACHE[key]
    pct = lambda s: [v / 100.0 if v is not None else None for v in s]
    y10 = pct(panel.macro("DGS10"))
    slope = pct(panel.macro("T10Y3M"))
    credit = pct(panel.macro("BAA10Y"))
    vix = panel.macro("VIXCLS")
    if all(v is None for v in vix):
        vix = panel.series("VIX") if _has(panel, "VIX") else vix
    dollar = panel.series("DXY") if _has(panel, "DXY") else panel.macro("DTWEXBGS")
    oil = panel.series("WTI") if _has(panel, "WTI") else [None] * len(y10)
    gold = panel.series("GOLD") if _has(panel, "GOLD") else [None] * len(y10)
    out = {
        "y10": y10, "d_y10_3m": diff(y10, 63), "slope_10y3m": slope, "d_slope_3m": diff(slope, 63),
        "real_y10": pct(panel.macro("DFII10")), "breakeven_10y": pct(panel.macro("T5YIE")),
        "credit_spread": credit, "d_credit_3m": diff(credit, 63),
        "vix": vix, "d_vix_1m": diff(vix, 21),
        "dollar_mom_3m": lag_ret(dollar, 63), "oil_mom_3m": lag_ret(oil, 63), "gold_mom_3m": lag_ret(gold, 63),
        "cpi_yoy": panel.macro("CPIAUCSL", transform=yoy_log),
        "unemp_gap": panel.macro("UNRATE", transform=sahm_gap),
        "nfci": panel.macro("NFCI"),
        "fed_bs_growth": panel.macro("WALCL", transform=growth_26w),
        "_rf": pct(panel.macro("DGS3MO")), "_dy10": diff(y10, 1), "_dollar_ret": log_returns(dollar),
    }
    _MACRO_CACHE.clear()          # one panel at a time is plenty
    _MACRO_CACHE[key] = out
    return out


def _has(panel: Panel, asset_id: str) -> bool:
    try:
        return bool(panel._price_rows(asset_id))
    except Exception:
        return False


def compute_features(panel: Panel, asset_id: str, include_macro: bool = True) -> Dict[str, Series]:
    """All features for `asset_id` on the panel's calendar."""
    p = panel.series(asset_id, "adj_close")
    vol_raw = panel.series(asset_id, "volume")
    mkt = panel.series(panel.benchmark, "adj_close")
    n = len(p)
    r = log_returns(p)
    rm = log_returns(mkt)
    logp = [math.log(v) if v is not None and v > 0 else None for v in p]
    f: Dict[str, Series] = {}
    f["ret_1d"] = r
    for name, k in (("ret_1w", 5), ("ret_1m", 21), ("ret_3m", 63), ("ret_6m", 126), ("ret_12m", 252)):
        f[name] = lag_ret(p, k)
    m3 = lag_ret(mkt, 63)
    m6 = lag_ret(mkt, 126)
    f["excess_3m"] = sub(f["ret_3m"], m3)
    f["rel_strength_6m"] = sub(f["ret_6m"], m6)
    f["mom_12_1"] = [(logp[i - 21] - logp[i - 252]) if i >= 252 and logp[i - 21] is not None and logp[i - 252] is not None and logp[i] is not None else None for i in range(n)]
    ma20, ma50, ma200 = roll_mean(p, 20), roll_mean(p, 50), roll_mean(p, 200)
    sd20, sd50 = roll_std(p, 20), roll_std(p, 50)
    f["ma_cross"] = combine(ma50, ma200, lambda a, b: a / b - 1 if b else None)
    f["z_20"] = [((p[i] - ma20[i]) / sd20[i]) if ma20[i] is not None and sd20[i] else None for i in range(n)]
    f["z_50"] = [((p[i] - ma50[i]) / sd50[i]) if ma50[i] is not None and sd50[i] else None for i in range(n)]
    f["dist_ma200"] = [(math.log(p[i] / ma200[i])) if ma200[i] and p[i] else None for i in range(n)]
    f["skew_60"], f["kurt_60"] = roll_moments(r, 60)
    hi, lo = roll_max(p, 252), roll_min(p, 252)
    f["pctile_252"] = [((p[i] - lo[i]) / (hi[i] - lo[i])) if hi[i] is not None and lo[i] is not None and hi[i] > lo[i] else None for i in range(n)]
    sdr20, sdr60, sdr252 = roll_std(r, 20), roll_std(r, 60), roll_std(r, 252)
    ann = math.sqrt(ANN)
    f["vol_20"] = [v * ann if v is not None else None for v in sdr20]
    f["vol_60"] = [v * ann if v is not None else None for v in sdr60]
    neg2 = [(min(v, 0.0) ** 2) if v is not None else None for v in r]
    dn60 = roll_mean(neg2, 60)
    f["downside_vol_60"] = [math.sqrt(v * ANN) if v is not None else None for v in dn60]
    mac = macro_features(panel) if include_macro else {}
    rf = mac.get("_rf") or [None] * n
    mr252 = roll_mean(r, 252)
    dn252 = roll_mean(neg2, 252)
    f["sharpe_252"] = [((mr252[i] * ANN - (rf[i] or 0.0)) / (sdr252[i] * ann)) if mr252[i] is not None and sdr252[i] else None for i in range(n)]
    f["sortino_252"] = [((mr252[i] * ANN - (rf[i] or 0.0)) / math.sqrt(dn252[i] * ANN)) if mr252[i] is not None and dn252[i] else None for i in range(n)]
    f["drawdown_252"] = [(p[i] / hi[i] - 1.0) if hi[i] else None for i in range(n)]
    reg = roll_regress(r, rm, 252)
    f["beta_252"] = reg["slope"]
    f["corr_252"] = reg["corr"]
    f["alpha_252"] = [v * ANN if v is not None else None for v in reg["intercept"]]
    f["idio_vol_252"] = [math.sqrt(v * ANN) if v is not None else None for v in reg["resvar"]]
    from finsim.quant.volatility import ewma_variance
    ew: Series = [None] * n
    h = None
    for i, x in enumerate(r):
        if x is None:
            ew[i] = math.sqrt(h * ANN) if h is not None else None
            continue
        h = x * x if h is None else 0.94 * h + 0.06 * x * x
        ew[i] = math.sqrt(h * ANN) if i > 30 else None
    f["ewma_vol"] = ew
    f["garch_vol"] = garch_vol(r)
    v252 = [v * ann if v is not None else None for v in sdr252]
    f["vol_ratio"] = combine(f["vol_20"], v252, lambda a, b: a / b if b else None)
    f["vol_of_vol"] = roll_std(f["vol_20"], 126)
    f["vol_pctile"] = roll_pctile(f["vol_20"], 756, minp=126)
    f["rsi_14"] = rsi(p)
    e12, e26 = ema(p, 12), ema(p, 26)
    f["macd"] = [((e12[i] - e26[i]) / p[i]) if i >= 26 and p[i] and e12[i] is not None and e26[i] is not None else None for i in range(n)]
    f["bb_pctb"] = [((p[i] - (ma20[i] - 2 * sd20[i])) / (4 * sd20[i])) if ma20[i] is not None and sd20[i] else None for i in range(n)]
    lv = [math.log(v) if v is not None and v > 0 else None for v in vol_raw]
    lvm, lvs = roll_mean(lv, 60), roll_std(lv, 60)
    f["volume_z"] = [((lv[i] - lvm[i]) / lvs[i]) if lv[i] is not None and lvm[i] is not None and lvs[i] else None for i in range(n)]
    rlag = [None] + r[:-1]
    f["ar1_63"] = roll_regress(r, rlag, 63)["slope"]
    f["acf1_252"] = roll_regress(r, rlag, 252)["corr"]
    f["half_life"] = half_life(logp)
    f["adf_t"] = adf_monthly(logp)
    lm, ls = roll_mean(logp, 1260, minp=756), roll_std(logp, 1260, minp=756)
    f["value_5y"] = [(-(logp[i] - lm[i]) / ls[i]) if lm[i] is not None and ls[i] else None for i in range(n)]
    f.update(fundamental_features(panel, asset_id, p))
    if include_macro:
        for k in MACRO_FEATURES:
            f[k] = mac.get(k) or [None] * n
        f["rate_beta_252"] = roll_regress(r, mac["_dy10"], 252)["slope"]
        f["dollar_beta_252"] = roll_regress(r, mac["_dollar_ret"], 252)["slope"]
    return f


def fundamental_features(panel: Panel, asset_id: str, p: Series) -> Dict[str, Series]:
    """Valuation and growth from SEC filings, each value usable only from its filing date."""
    n = len(p)
    none = {k: [None] * n for k in ("earnings_yield", "pe_rel_5y", "eps_growth_yoy", "rev_growth_yoy", "net_margin")}
    try:
        rows = panel.store.fundamentals(asset_id)
    except Exception:
        return none
    if not rows:
        return none
    try:
        from ..data.sec import ttm_series
    except Exception:
        return none
    cal = panel.calendar()
    try:
        eps = ttm_series(rows, "eps", cal)
        rev = ttm_series(rows, "revenue", cal)
        ni = ttm_series(rows, "net_income", cal)
    except Exception:
        return none
    e = [eps.get(d) for d in cal]
    rv = [rev.get(d) for d in cal]
    nn = [ni.get(d) for d in cal]
    out = dict(none)
    out["earnings_yield"] = [(e[i] / p[i]) if e[i] is not None and p[i] else None for i in range(n)]
    pe = [(p[i] / e[i]) if e[i] and e[i] > 0 and p[i] else None for i in range(n)]
    pm, ps = roll_mean(pe, 1260, minp=504), roll_std(pe, 1260, minp=504)
    out["pe_rel_5y"] = [((pe[i] - pm[i]) / ps[i]) if pe[i] is not None and pm[i] is not None and ps[i] else None for i in range(n)]
    out["eps_growth_yoy"] = [((e[i] - e[i - 252]) / abs(e[i - 252])) if i >= 252 and e[i] is not None and e[i - 252] else None for i in range(n)]
    out["rev_growth_yoy"] = [(rv[i] / rv[i - 252] - 1) if i >= 252 and rv[i] and rv[i - 252] else None for i in range(n)]
    out["net_margin"] = [(nn[i] / rv[i]) if nn[i] is not None and rv[i] else None for i in range(n)]
    for k, s in out.items():                 # prices exist only where the asset traded
        out[k] = [v if p[i] is not None else None for i, v in enumerate(s)]
    return out


def targets(p: Series, h: int) -> Series:
    """Forward log return over h sessions: ln(P[t+h] / P[t])."""
    n = len(p)
    out: Series = [None] * n
    for i in range(n - h):
        a, b = p[i], p[i + h]
        if a is not None and b is not None and a > 0 and b > 0:
            out[i] = math.log(b / a)
    return out
