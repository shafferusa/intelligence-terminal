"""Per-asset analytics: evaluates the equation library on one asset's daily price history."""
import math

from .portfolio import expected_shortfall, parametric_var
from .timeseries import adf_test, fit_ar1
from .volatility import ewma_variance, garch_fit

MIN_OBS = 30  # minimum number of returns for statistical metrics

METRIC_INFO = {
    "ret_1d": {"label": "1-day return", "equations": ["1"], "fmt": "pct"},
    "log_ret_1d": {"label": "1-day log return", "equations": ["2"], "fmt": "pct"},
    "ret_1m": {"label": "1-month return (21 sessions)", "equations": ["3"], "fmt": "pct"},
    "ret_3m": {"label": "3-month return (63 sessions)", "equations": ["3"], "fmt": "pct"},
    "ret_1y": {"label": "1-year return (252 sessions)", "equations": ["3"], "fmt": "pct"},
    "cum_log_1y": {"label": "1-year cumulative log return", "equations": ["4"], "fmt": "pct"},
    "ann_return": {"label": "Annualised return", "equations": ["5"], "fmt": "pct"},
    "geo_mean": {"label": "Geometric mean daily return", "equations": ["7"], "fmt": "pct"},
    "mean_ret": {"label": "Arithmetic mean daily return", "equations": ["6"], "fmt": "pct"},
    "vol_ann": {"label": "Annualised volatility", "equations": ["8", "9", "49"], "fmt": "pct"},
    "stderr_mean": {"label": "Standard error of mean daily return", "equations": ["18"], "fmt": "pct"},
    "t_mean": {"label": "t-statistic of mean return", "equations": ["31"], "fmt": "num"},
    "skew": {"label": "Skewness of daily returns", "equations": ["16"], "fmt": "num"},
    "kurt": {"label": "Excess kurtosis of daily returns", "equations": ["17"], "fmt": "num"},
    "sharpe": {"label": "Sharpe ratio (annualised)", "equations": ["19"], "fmt": "ratio"},
    "sortino": {"label": "Sortino ratio (annualised)", "equations": ["20"], "fmt": "ratio"},
    "beta": {"label": "Beta to market", "equations": ["22", "50", "106"], "fmt": "ratio"},
    "corr_mkt": {"label": "Correlation with market", "equations": ["10", "11"], "fmt": "ratio"},
    "r2_mkt": {"label": "R² of market regression", "equations": ["29"], "fmt": "ratio"},
    "alpha_ann": {"label": "Jensen's alpha (annualised)", "equations": ["108"], "fmt": "pct"},
    "capm_er": {"label": "CAPM expected return", "equations": ["107"], "fmt": "pct"},
    "ewma_vol": {"label": "EWMA volatility (λ = 0.94, annualised)", "equations": ["44"], "fmt": "pct"},
    "garch_vol": {"label": "GARCH(1,1) next-day volatility (annualised)", "equations": ["46"], "fmt": "pct"},
    "garch_persistence": {"label": "GARCH persistence α + β", "equations": ["46"], "fmt": "ratio"},
    "ar1_phi": {"label": "AR(1) coefficient φ of log price", "equations": ["35"], "fmt": "ratio"},
    "half_life": {"label": "Mean-reversion half-life", "equations": ["58"], "fmt": "days"},
    "adf_t": {"label": "ADF t-statistic (log price, constant + trend)", "equations": ["43"], "fmt": "num"},
    "adf_stationary": {"label": "Stationary at 5% (ADF)", "equations": ["43"], "fmt": "bool"},
    "acf1": {"label": "Lag-1 autocorrelation of returns", "equations": ["41"], "fmt": "ratio"},
    "zscore_50": {"label": "Price z-score vs 50-session mean", "equations": ["12"], "fmt": "num"},
    "mom_12_1": {"label": "12-1 month momentum", "equations": [], "fmt": "pct"},
    "max_drawdown": {"label": "Maximum drawdown", "equations": [], "fmt": "pct"},
    "var_95": {"label": "1-day 95% VaR (normal)", "equations": ["109"], "fmt": "pct"},
    "es_95": {"label": "1-day 95% expected shortfall (normal)", "equations": ["110"], "fmt": "pct"},
    "n": {"label": "Price observations used", "equations": [], "fmt": "num"},
}


def _clean_series(values):
    """Float prices after the last missing, non-finite or non-positive value."""
    out = []
    for v in values or ():
        try:
            f = float(v)
        except (TypeError, ValueError):
            f = math.nan
        if f > 0 and math.isfinite(f):
            out.append(f)
        else:
            out = []
    return out


def _mean_sd(x):
    n = len(x)
    m = sum(x) / n
    v = sum((a - m) * (a - m) for a in x) / (n - 1)
    return m, math.sqrt(v) if v > 0 else 0.0


def _compound(returns):
    g = 1.0
    for r in returns:
        g *= 1.0 + r
    return g - 1.0


def _finite(v):
    if isinstance(v, (bool, int)) or v is None:
        return v
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def asset_metrics(closes, market_closes=None, rf=0.04, dividends=None, periods_per_year=252, fast=True):
    """Evaluate the applicable equations on one asset's closes (oldest first).

    market_closes are aligned with closes from the end. rf is an annual rate. Metrics that
    cannot be computed are None; the function never raises. Keys are those of METRIC_INFO.
    """
    out = dict.fromkeys(METRIC_INFO)
    try:
        _fill(out, closes, market_closes, rf, dividends, periods_per_year, fast)
    except Exception:  # defensive: a bad series must never break a page
        pass
    return {k: _finite(v) for k, v in out.items()}


def _block(fn):
    try:
        fn()
    except (ArithmeticError, ValueError, TypeError, IndexError, OverflowError):
        pass


def _fill(out, closes, market_closes, rf, dividends, A, fast):
    p = _clean_series(closes)
    n = len(p)
    out["n"] = n
    if n < 2:
        return
    divs = None
    if dividends is not None:
        d = [float(x or 0.0) for x in dividends]
        d = d[-n:]
        divs = [0.0] * (n - len(d)) + d
    if divs:
        R = [(p[i] - p[i - 1] + divs[i]) / p[i - 1] for i in range(1, n)]
    else:
        R = [p[i] / p[i - 1] - 1.0 for i in range(1, n)]
    log = math.log
    lr = [log(1.0 + x) for x in R]
    nr = len(R)
    rf_p = rf / A

    out["ret_1d"] = R[-1]
    out["log_ret_1d"] = lr[-1]
    if nr >= 21:
        out["ret_1m"] = _compound(R[-21:])
    if nr >= 63:
        out["ret_3m"] = _compound(R[-63:])
    if nr >= 252:
        out["ret_1y"] = _compound(R[-252:])
        out["cum_log_1y"] = sum(lr[-252:])
        out["mom_12_1"] = _compound(R[-252:-21])

    def drawdown():
        peak, mdd = p[0], 0.0
        for v in p:
            if v > peak:
                peak = v
            dd = v / peak - 1.0
            if dd < mdd:
                mdd = dd
        out["max_drawdown"] = mdd
    _block(drawdown)

    def zscore():
        if n >= 50:
            m, sd = _mean_sd(p[-50:])
            if sd > 0:
                out["zscore_50"] = (p[-1] - m) / sd
    _block(zscore)

    if nr < MIN_OBS:
        return
    m, sd = _mean_sd(R)
    if not sd > 0:
        return

    def moments():
        g = _compound(R) + 1.0
        if g > 0:
            out["ann_return"] = g ** (A / nr) - 1.0
            out["geo_mean"] = g ** (1.0 / nr) - 1.0
        out["mean_ret"] = m
        out["vol_ann"] = sd * math.sqrt(A)
        se = sd / math.sqrt(nr)
        out["stderr_mean"] = se
        out["t_mean"] = m / se
        d = [x - m for x in R]
        m2 = sum(x * x for x in d) / nr
        out["skew"] = sum(x * x * x for x in d) / nr / m2 ** 1.5
        out["kurt"] = sum((x * x) ** 2 for x in d) / nr / (m2 * m2) - 3.0
        out["sharpe"] = (m - rf_p) / sd * math.sqrt(A)
        dd = math.sqrt(sum(min(0.0, x - rf_p) ** 2 for x in R) / nr)
        if dd > 0:
            out["sortino"] = (m - rf_p) / dd * math.sqrt(A)
        g0 = sum(x * x for x in d)
        out["acf1"] = sum(d[t] * d[t - 1] for t in range(1, nr)) / g0
        out["var_95"] = parametric_var(-m, sd, 0.95)
        out["es_95"] = expected_shortfall(-m, sd, 0.95)
    _block(moments)

    def ewma():
        out["ewma_vol"] = math.sqrt(ewma_variance(R, 0.94)[-1] * A)
    _block(ewma)

    def garch():
        g = garch_fit(R, fast=fast)
        out["garch_vol"] = math.sqrt(g["next_var"] * A)
        out["garch_persistence"] = g["persistence"]
    _block(garch)

    lp = [log(v) for v in p]

    def ar1():
        _, phi, _ = fit_ar1(lp)
        out["ar1_phi"] = phi
        if 0.0 < phi < 1.0:
            out["half_life"] = log(2.0) / -log(phi)
    _block(ar1)

    def adf():
        res = adf_test(lp, lags=1, trend="ct")
        out["adf_t"] = res["t_stat"]
        out["adf_stationary"] = bool(res["stationary"])
    _block(adf)

    mk = _clean_series(market_closes) if market_closes is not None else []
    k = min(len(mk), n)
    if k - 1 < MIN_OBS:
        return

    def market():
        mp = mk[-k:]
        Rm = [mp[i] / mp[i - 1] - 1.0 for i in range(1, k)]
        Ra = R[-(k - 1):]
        L = len(Rm)
        ma, mm = sum(Ra) / L, sum(Rm) / L
        sxy = sum((a - ma) * (b - mm) for a, b in zip(Ra, Rm))
        sxx = sum((b - mm) ** 2 for b in Rm)
        syy = sum((a - ma) ** 2 for a in Ra)
        if sxx <= 0:
            return
        beta = sxy / sxx
        out["beta"] = beta
        if syy > 0:
            corr = sxy / math.sqrt(sxx * syy)
            out["corr_mkt"] = corr
            out["r2_mkt"] = corr * corr
        out["alpha_ann"] = ((ma - rf_p) - beta * (mm - rf_p)) * A
        out["capm_er"] = rf + beta * (mm * A - rf)
    _block(market)


def score_table(histories, market=None, rf=0.04, **kwargs):
    """asset_metrics for many assets: {symbol: closes} → {symbol: metrics}."""
    return {sym: asset_metrics(closes, market, rf, **kwargs) for sym, closes in (histories or {}).items()}
