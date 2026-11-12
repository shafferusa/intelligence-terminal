"""Time-series models (equations 34-43)."""
import math
import random as _random

from .linalg import gram, inverse, nelder_mead, solve, xty


def _rng(seed_or_rng):
    return seed_or_rng if isinstance(seed_or_rng, _random.Random) else _random.Random(seed_or_rng)


# ---------------------------------------------------------------- 34-35 autoregressions

def fit_ar(x, p):
    """Eq 34: fit AR(p) x_t = c + Σ φ_i x_{t−i} + ε_t by OLS.

    Returns {"const", "phi": [φ_1..φ_p], "sigma2", "residuals"}.
    """
    n = len(x)
    if n <= 2 * p + 1:
        raise ValueError("series too short for AR(%d)" % p)
    X = [[1.0] + [x[t - i] for i in range(1, p + 1)] for t in range(p, n)]
    y = x[p:]
    beta = solve(gram(X), xty(X, y))
    res = [yi - sum(b * v for b, v in zip(beta, row)) for row, yi in zip(X, y)]
    dof = max(1, len(y) - (p + 1))
    return {"const": beta[0], "phi": beta[1:], "sigma2": math.fsum(e * e for e in res) / dof,
            "residuals": res}


def fit_ar1(x):
    """Eq 35: AR(1) x_t = c + φ x_{t−1} + ε_t by closed-form OLS. Returns (c, φ, σ²)."""
    n = len(x) - 1
    if n < 3:
        raise ValueError("series too short")
    xs, ys = x[:-1], x[1:]
    mx, my = math.fsum(xs) / n, math.fsum(ys) / n
    sxx = math.fsum((a - mx) ** 2 for a in xs)
    if sxx <= 0:
        raise ValueError("constant series")
    phi = math.fsum((a - mx) * (b - my) for a, b in zip(xs, ys)) / sxx
    c = my - phi * mx
    s2 = math.fsum((b - c - phi * a) ** 2 for a, b in zip(xs, ys)) / max(1, n - 2)
    return c, phi, s2


def ar1_forecast(x_last, c, phi, steps=1):
    """h-step AR(1) forecast c(1 + φ + … + φ^{h−1}) + φ^h x_t."""
    f = x_last
    for _ in range(steps):
        f = c + phi * f
    return f


# ---------------------------------------------------------------- 36-37 moving-average and ARMA

def arma_residuals(x, const, phi=(), theta=()):
    """Eq 37 residual recursion ε_t = x_t − c − Σφ_i x_{t−i} − Σθ_j ε_{t−j} (pre-sample ε = 0)."""
    p, q = len(phi), len(theta)
    e = [0.0] * len(x)
    for t in range(p, len(x)):
        v = x[t] - const
        for i in range(p):
            v -= phi[i] * x[t - 1 - i]
        for j in range(q):
            if t - 1 - j >= 0:
                v -= theta[j] * e[t - 1 - j]
        if abs(v) > 1e100:
            raise OverflowError("non-invertible parameters")
        e[t] = v
    return e[p:]


def ma_filter(x, mu, theta):
    """Eq 36: recover MA(q) shocks ε_t = x_t − μ − Σθ_j ε_{t−j}."""
    return arma_residuals(x, mu, (), theta)


def simulate_arma(n, const=0.0, phi=(), theta=(), sigma=1.0, seed=None, burn=100):
    """Simulate an ARMA(p,q) series x_t = c + Σφ_i x_{t−i} + ε_t + Σθ_j ε_{t−j}."""
    rng = _rng(seed)
    p, q = len(phi), len(theta)
    x, e = [], []
    for t in range(n + burn):
        eps = rng.gauss(0.0, sigma)
        v = const + eps
        for i in range(p):
            if t - 1 - i >= 0:
                v += phi[i] * x[t - 1 - i]
        for j in range(q):
            if t - 1 - j >= 0:
                v += theta[j] * e[t - 1 - j]
        x.append(v)
        e.append(eps)
    return x[burn:]


def simulate_ma(n, mu=0.0, theta=(0.5,), sigma=1.0, seed=None):
    """Eq 36: simulate MA(q) x_t = μ + ε_t + Σθ_j ε_{t−j}."""
    return simulate_arma(n, mu, (), theta, sigma, seed, burn=len(theta))


def _css(x, p, q):
    def obj(params):
        try:
            e = arma_residuals(x, params[0], params[1:1 + p], params[1 + p:])
        except OverflowError:
            return 1e300
        if any(abs(t) >= 0.999 for t in params[1 + p:]) and q == 1:
            return 1e300
        return math.fsum(v * v for v in e)
    return obj


def fit_arma(x, p, q):
    """Eq 37: fit ARMA(p,q) by conditional sum of squares.

    Starts from a Hannan-Rissanen two-stage regression and refines with Nelder-Mead.
    Returns {"const", "phi", "theta", "sigma2", "residuals", "css"}.
    """
    n = len(x)
    m = max(p + q, min(10, n // 10))
    init = [math.fsum(x) / n] + [0.0] * (p + q)
    if q > 0 and n > 3 * m + 10:
        try:
            long_ar = fit_ar(x, m)
            eh = [0.0] * m + long_ar["residuals"]
            s = max(p, q) + m
            X = [[1.0] + [x[t - i] for i in range(1, p + 1)] + [eh[t - j] for j in range(1, q + 1)]
                 for t in range(s, n)]
            init = solve(gram(X), xty(X, x[s:]))
            init = init[:1 + p] + [max(-0.95, min(0.95, v)) for v in init[1 + p:]]
        except (ValueError, ZeroDivisionError):
            pass
    elif q == 0 and p > 0:
        ar = fit_ar(x, p)
        init = [ar["const"]] + ar["phi"]
    obj = _css(x, p, q)
    best, val = nelder_mead(obj, init, step=0.05, max_iter=200 * (p + q + 1), tol=1e-12)
    if obj(init) < val:
        best, val = init, obj(init)
    res = arma_residuals(x, best[0], best[1:1 + p], best[1 + p:])
    k = 1 + p + q
    return {"const": best[0], "phi": best[1:1 + p], "theta": best[1 + p:],
            "sigma2": val / max(1, len(res) - k), "residuals": res, "css": val}


def fit_ma(x, q):
    """Eq 36: fit MA(q) x_t = μ + ε_t + Σθ_j ε_{t−j} by conditional sum of squares."""
    r = fit_arma(x, 0, q)
    return {"mu": r["const"], "theta": r["theta"], "sigma2": r["sigma2"], "residuals": r["residuals"]}


# ---------------------------------------------------------------- 38-40 differencing and ARIMA

def diff(x):
    """Eq 38: first difference Δx_t = x_t − x_{t−1}."""
    return [x[i] - x[i - 1] for i in range(1, len(x))]


def diff_n(x, d):
    """Eq 39: d-th difference Δ^d x_t (repeated first differencing)."""
    for _ in range(d):
        x = diff(x)
    return x


def fit_arima(x, p, d, q):
    """Eq 40: ARIMA(p,d,q): difference d times, then fit ARMA(p,q) by CSS."""
    r = fit_arma(diff_n(list(x), d), p, q)
    r["d"] = d
    return r


# ---------------------------------------------------------------- 41-43 diagnostics

def autocorrelation(x, k):
    """Eq 41: lag-k autocorrelation ρ_k = γ_k / γ_0."""
    n = len(x)
    if k >= n:
        raise ValueError("lag too large")
    m = math.fsum(x) / n
    d = [v - m for v in x]
    g0 = math.fsum(v * v for v in d)
    if g0 == 0:
        raise ValueError("constant series")
    return math.fsum(d[t] * d[t - k] for t in range(k, n)) / g0


def acf(x, nlags=10):
    """Autocorrelations ρ_1..ρ_nlags."""
    return [autocorrelation(x, k) for k in range(1, nlags + 1)]


def _moments(x):
    n = len(x)
    m = math.fsum(x) / n
    v = math.fsum((a - m) ** 2 for a in x) / (n - 1)
    c1 = math.fsum((x[t] - m) * (x[t - 1] - m) for t in range(1, n)) / n
    return m, v, c1


def stationarity_check(x, mean_z=2.0, var_ratio_max=2.0, acov_tol=0.3):
    """Eq 42: rough weak-stationarity check comparing mean, variance and lag-1 autocovariance
    across the two halves of the sample. Heuristic, not a formal test."""
    n = len(x)
    if n < 20:
        raise ValueError("need at least 20 observations")
    a, b = x[: n // 2], x[n // 2:]
    m1, v1, c1 = _moments(a)
    m2, v2, c2 = _moments(b)
    se = math.sqrt(v1 / len(a) + v2 / len(b)) or 1e-300
    z = (m2 - m1) / se
    ratio = max(v1, v2) / max(1e-300, min(v1, v2))
    rho1, rho2 = c1 / v1 if v1 else 0.0, c2 / v2 if v2 else 0.0
    ok = abs(z) < mean_z and ratio < var_ratio_max and abs(rho1 - rho2) < acov_tol
    return {"mean_1": m1, "mean_2": m2, "var_1": v1, "var_2": v2, "autocov1_1": c1, "autocov1_2": c2,
            "mean_shift_z": z, "var_ratio": ratio, "stationary": ok}


ADF_CRIT_5 = {"ct": -3.41, "c": -2.86}


def adf_test(x, lags=1, trend="ct"):
    """Eq 43: augmented Dickey-Fuller regression
    Δy_t = α + βt + γ y_{t−1} + Σ δ_i Δy_{t−i} + ε_t.

    trend "ct" includes constant and trend, "c" constant only. Returns
    {"t_stat", "gamma", "crit_5", "stationary"}; stationary when t_stat < crit_5.
    """
    n = len(x)
    dy = [x[i] - x[i - 1] for i in range(1, n)]
    rows, ys = [], []
    for t in range(lags, len(dy)):
        row = [1.0]
        if trend == "ct":
            row.append(float(t))
        row.append(x[t])  # y_{t-1} relative to dy[t] = x[t+1] - x[t]
        for i in range(1, lags + 1):
            row.append(dy[t - i])
        rows.append(row)
        ys.append(dy[t])
    k = len(rows[0]) if rows else 0
    if len(rows) <= k + 2:
        raise ValueError("series too short")
    G = gram(rows)
    inv = inverse(G)
    xy = xty(rows, ys)
    beta = [sum(inv[i][j] * xy[j] for j in range(k)) for i in range(k)]
    s = 0.0
    for row, yi in zip(rows, ys):
        e = yi - sum(b * v for b, v in zip(beta, row))
        s += e * e
    s2 = s / (len(rows) - k)
    gi = 2 if trend == "ct" else 1
    se = math.sqrt(max(1e-300, s2 * inv[gi][gi]))
    t = beta[gi] / se
    crit = ADF_CRIT_5["ct" if trend == "ct" else "c"]
    return {"t_stat": t, "gamma": beta[gi], "crit_5": crit, "stationary": t < crit}
