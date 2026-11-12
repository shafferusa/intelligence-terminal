"""Volatility models (equations 44-50).

Variance recursions return n + 1 values: entry t is the conditional variance of r_t and the
final entry is the one-step-ahead forecast.
"""
import math
import random as _random


def _init_var(returns, h0):
    if h0 is not None:
        return h0
    n = len(returns)
    if n == 0:
        return 0.0
    return math.fsum(r * r for r in returns) / n


def ewma_variance(returns, lam=0.94, h0=None):
    """Eq 44: EWMA (RiskMetrics) variance σ²_t = λσ²_{t−1} + (1 − λ) r²_{t−1}."""
    h = _init_var(returns, h0)
    out = [h]
    for r in returns:
        h = lam * h + (1.0 - lam) * r * r
        out.append(h)
    return out


def arch_variance(returns, omega, alphas, h0=None):
    """Eq 45: ARCH(q) variance σ²_t = ω + Σ α_i r²_{t−i} (pre-sample r² = unconditional level)."""
    fill = _init_var(returns, h0)
    q = len(alphas)
    r2 = [fill] * q + [r * r for r in returns]
    return [omega + sum(alphas[i] * r2[t + q - 1 - i] for i in range(q)) for t in range(len(returns) + 1)]


def garch_variance(returns, omega, alpha, beta, h0=None):
    """Eq 46: GARCH(1,1) variance σ²_t = ω + α r²_{t−1} + β σ²_{t−1}."""
    h = _init_var(returns, h0)
    out = [h]
    for r in returns:
        h = omega + alpha * r * r + beta * h
        out.append(h)
    return out


def _garch_nll(r2, var, a, b):
    """Negative Gaussian log-likelihood (constants dropped) under variance targeting."""
    w = var * (1.0 - a - b)
    h = var
    s = 0.0
    log = math.log
    for x in r2:
        s += log(h) + x / h
        h = w + a * x + b * h
    return 0.5 * s


def garch_loglik(returns, omega, alpha, beta, h0=None):
    """Gaussian log-likelihood −½ Σ [ln 2π + ln σ²_t + r²_t / σ²_t] of a GARCH(1,1)."""
    h = garch_variance(returns, omega, alpha, beta, h0)
    return -0.5 * math.fsum(math.log(2 * math.pi) + math.log(ht) + r * r / ht for r, ht in zip(returns, h))


def garch_fit(returns, fast=True, demean=True):
    """Eq 46: fit GARCH(1,1) by maximum likelihood with variance targeting (ω = σ̄²(1 − α − β)).

    Searches a coarse grid over (α, α + β) then refines locally; fast=True uses about 20
    likelihood evaluations, fast=False about 60.
    Returns {"omega", "alpha", "beta", "persistence", "loglik", "next_var", "uncond_var"}.
    """
    n = len(returns)
    if n < 10:
        raise ValueError("need at least 10 returns")
    m = math.fsum(returns) / n if demean else 0.0
    r = [x - m for x in returns]
    r2 = [x * x for x in r]
    var = math.fsum(r2) / n
    if var <= 0:
        raise ValueError("zero variance")
    if fast:
        alphas, pers, budget = (0.04, 0.10), (0.90, 0.97), 20
    else:
        alphas, pers, budget = (0.02, 0.05, 0.10, 0.18), (0.80, 0.90, 0.95, 0.98, 0.995), 60
    cache = {}

    def f(a, p):
        a = min(max(a, 0.002), 0.6)
        p = min(max(p, a), 0.999)
        key = (round(a, 6), round(p, 6))
        if key not in cache:
            cache[key] = _garch_nll(r2, var, a, p - a)
        return cache[key], a, p

    best = None
    for a in alphas:
        for p in pers:
            if a <= p:
                cand = f(a, p)
                if best is None or cand[0] < best[0]:
                    best = cand
    sa, sp = 0.03, 0.03
    while len(cache) < budget and (sa > 1e-4 or sp > 1e-4):
        _, a0, p0 = best
        improved = False
        for da, dp in ((sa, 0.0), (-sa, 0.0), (0.0, sp), (0.0, -sp)):
            if len(cache) >= budget:
                break
            cand = f(a0 + da, p0 + dp)
            if cand[0] < best[0] - 1e-12:
                best, improved = cand, True
        if not improved:
            sa, sp = sa / 2.0, sp / 2.0
    nll, a, p = best
    b = p - a
    w = var * (1.0 - p)
    h = var
    for x in r2:
        h = w + a * x + b * h
    return {"omega": w, "alpha": a, "beta": b, "persistence": p,
            "loglik": -nll - 0.5 * n * math.log(2 * math.pi), "next_var": h, "uncond_var": var}


def simulate_garch(n, omega, alpha, beta, seed=None):
    """Simulate returns from a Gaussian GARCH(1,1)."""
    rng = seed if isinstance(seed, _random.Random) else _random.Random(seed)
    h = omega / max(1e-12, 1.0 - alpha - beta)
    out = []
    for _ in range(n):
        x = math.sqrt(h) * rng.gauss(0.0, 1.0)
        out.append(x)
        h = omega + alpha * x * x + beta * h
    return out


def gjr_garch_variance(returns, omega, alpha, gamma, beta, h0=None):
    """Eq 47: GJR-GARCH σ²_t = ω + (α + γ·1[r_{t−1} < 0]) r²_{t−1} + β σ²_{t−1}."""
    h = _init_var(returns, h0)
    out = [h]
    for r in returns:
        h = omega + (alpha + (gamma if r < 0 else 0.0)) * r * r + beta * h
        out.append(h)
    return out


def realized_variance(returns):
    """Eq 48: realised variance Σ r²."""
    return math.fsum(r * r for r in returns)


def realized_vol_annualized(returns, periods_per_year=252):
    """Eq 49: annualised realised volatility √(A · RV / n) using the average squared return."""
    return math.sqrt(periods_per_year * realized_variance(returns) / len(returns))


def rolling_beta(asset_returns, market_returns, window=60):
    """Eq 50: rolling beta Cov(R_i, R_m) / Var(R_m) over a trailing window. One value per window end."""
    n = min(len(asset_returns), len(market_returns))
    a, m = asset_returns[-n:], market_returns[-n:]
    out = []
    for end in range(window, n + 1):
        xa, xm = a[end - window:end], m[end - window:end]
        ma, mm = sum(xa) / window, sum(xm) / window
        vm = sum((v - mm) ** 2 for v in xm)
        out.append(sum((u - ma) * (v - mm) for u, v in zip(xa, xm)) / vm if vm > 0 else math.nan)
    return out
