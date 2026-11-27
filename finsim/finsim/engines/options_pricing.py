"""Option pricing: Black-Scholes-Merton for European contracts, a Cox-Ross-Rubinstein
binomial tree for American contracts (early exercise), Greeks by finite difference,
intrinsic/extrinsic split and no-arbitrage bounds. Pure Python, deterministic.

Dividends enter as a continuous yield for valuation; discrete ex-dividend dates are
handled by the assignment logic (early exercise of deep in-the-money calls before
an ex-date), which is where they matter most economically.
"""
from __future__ import annotations

import math
from typing import Dict

SQRT2 = math.sqrt(2.0)


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / SQRT2))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def bsm(option_type: str, S: float, K: float, T: float, r: float, q: float, sigma: float) -> float:
    if T <= 0:
        return max(0.0, S - K) if option_type == "C" else max(0.0, K - S)
    sigma = max(1e-6, sigma)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if option_type == "C":
        return S * math.exp(-q * T) * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
    return K * math.exp(-r * T) * norm_cdf(-d2) - S * math.exp(-q * T) * norm_cdf(-d1)


def crr_tree(option_type: str, S: float, K: float, T: float, r: float, q: float, sigma: float, steps: int = 60) -> Dict[str, float]:
    """CRR binomial with early exercise. Returns price plus delta, gamma and theta read from the tree."""
    if T <= 0:
        v = max(0.0, S - K) if option_type == "C" else max(0.0, K - S)
        return {"price": v, "delta": 1.0 if (option_type == "C" and S > K) else -1.0 if (option_type == "P" and S < K) else 0.0, "gamma": 0.0, "theta": 0.0}
    sigma = max(1e-6, sigma)
    dt = T / steps
    u = math.exp(sigma * math.sqrt(dt))
    d = 1 / u
    disc = math.exp(-r * dt)
    p = (math.exp((r - q) * dt) - d) / (u - d)
    p = min(1.0, max(0.0, p))
    sign = 1.0 if option_type == "C" else -1.0
    values = [max(0.0, sign * (S * (u ** (2 * j - steps)) - K)) for j in range(steps + 1)]
    euro = list(values)
    lvl1 = lvl2 = None
    for i in range(steps - 1, -1, -1):
        for j in range(i + 1):
            cont = disc * (p * values[j + 1] + (1 - p) * values[j])
            spot = S * (u ** (2 * j - i))
            values[j] = max(cont, sign * (spot - K))
            euro[j] = disc * (p * euro[j + 1] + (1 - p) * euro[j])
        if i == 2:
            lvl2 = values[:3]
        if i == 1:
            lvl1 = values[:2]
    # control variate: correct the American tree value by the tree's European error against the closed form
    cv = bsm(option_type, S, K, T, r, q, sigma) - euro[0]
    price0 = max(values[0] + cv, max(0.0, sign * (S - K)))
    if lvl1 is None or lvl2 is None:
        return {"price": price0, "delta": 0.0, "gamma": 0.0, "theta": 0.0}
    s_u, s_d = S * u, S * d
    delta = (lvl1[1] - lvl1[0]) / (s_u - s_d)
    s_uu, s_dd = S * u * u, S * d * d
    d_up = (lvl2[2] - lvl2[1]) / (s_uu - S)
    d_dn = (lvl2[1] - lvl2[0]) / (S - s_dd)
    gamma = (d_up - d_dn) / (0.5 * (s_uu - s_dd))
    theta = (lvl2[1] - price0) / (2 * dt) / 365.0        # per calendar day
    return {"price": price0, "delta": delta, "gamma": gamma, "theta": theta}


def crr_american(option_type: str, S: float, K: float, T: float, r: float, q: float, sigma: float, steps: int = 60) -> float:
    return crr_tree(option_type, S, K, T, r, q, sigma, steps)["price"]


def price(option_type: str, style: str, S: float, K: float, T: float, r: float, q: float, sigma: float) -> float:
    if style == "AMERICAN":
        # an American call on a non-dividend stock is never exercised early; use the closed form
        if option_type == "C" and q <= 1e-9:
            return bsm("C", S, K, T, r, q, sigma)
        return crr_american(option_type, S, K, T, r, q, sigma)
    return bsm(option_type, S, K, T, r, q, sigma)


def bsm_greeks(option_type: str, S: float, K: float, T: float, r: float, q: float, sigma: float) -> Dict[str, float]:
    if T <= 0:
        itm = (S > K) if option_type == "C" else (S < K)
        return {"delta": (1.0 if option_type == "C" else -1.0) if itm else 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0, "rho": 0.0}
    sigma = max(1e-6, sigma)
    sq = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sq)
    d2 = d1 - sigma * sq
    pdf = norm_pdf(d1)
    if option_type == "C":
        delta = math.exp(-q * T) * norm_cdf(d1)
        theta = (-S * math.exp(-q * T) * pdf * sigma / (2 * sq) - r * K * math.exp(-r * T) * norm_cdf(d2) + q * S * math.exp(-q * T) * norm_cdf(d1)) / 365
        rho = K * T * math.exp(-r * T) * norm_cdf(d2) / 10000
    else:
        delta = -math.exp(-q * T) * norm_cdf(-d1)
        theta = (-S * math.exp(-q * T) * pdf * sigma / (2 * sq) + r * K * math.exp(-r * T) * norm_cdf(-d2) - q * S * math.exp(-q * T) * norm_cdf(-d1)) / 365
        rho = -K * T * math.exp(-r * T) * norm_cdf(-d2) / 10000
    gamma = math.exp(-q * T) * pdf / (S * sigma * sq)
    vega = S * math.exp(-q * T) * pdf * sq / 100
    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta, "rho": rho}


def greeks(option_type: str, style: str, S: float, K: float, T: float, r: float, q: float, sigma: float) -> Dict[str, float]:
    """Per-unit (per share) Greeks. European: analytic. American: delta/gamma/theta from the tree, vega/rho by finite difference.
    Vega per 1 vol point, theta per calendar day, rho per 1bp."""
    if style == "AMERICAN" and not (option_type == "C" and q <= 1e-9):
        t = crr_tree(option_type, S, K, T, r, q, sigma)
        p0 = t["price"]
        delta, gamma, theta = t["delta"], t["gamma"], t["theta"]
        dv = 0.01
        vega = (crr_american(option_type, S, K, T, r, q, sigma + dv) - crr_american(option_type, S, K, T, r, q, max(1e-4, sigma - dv))) / 2
        dr = 0.0001
        rho = (crr_american(option_type, S, K, T, r + dr, q, sigma) - crr_american(option_type, S, K, T, r - dr, q, sigma)) / 2
    else:
        p0 = bsm(option_type, S, K, T, r, q, sigma)
        g = bsm_greeks(option_type, S, K, T, r, q, sigma)
        delta, gamma, vega, theta, rho = g["delta"], g["gamma"], g["vega"], g["theta"], g["rho"]
    intrinsic = max(0.0, S - K) if option_type == "C" else max(0.0, K - S)
    return {"price": p0, "delta": delta, "gamma": gamma, "vega": vega, "theta": theta, "rho": rho, "intrinsic": intrinsic,
            "extrinsic": max(0.0, p0 - intrinsic), "iv": sigma}


def bounds_ok(option_type: str, style: str, S: float, K: float, T: float, r: float, q: float, p: float) -> bool:
    """No-arbitrage bounds (with small tolerance)."""
    tol = 1e-6 + 1e-6 * S
    disc_k = K * math.exp(-r * T)
    disc_s = S * math.exp(-q * T)
    if option_type == "C":
        # American call with a dividend yield: worth at least the European call and at least intrinsic (early exercise)
        lower = max(0.0, disc_s - disc_k) if style == "EUROPEAN" else max(0.0, disc_s - disc_k, S - K, S - disc_k if q <= 1e-9 else 0.0)
        upper = S
    else:
        lower = max(0.0, disc_k - disc_s) if style == "EUROPEAN" else max(0.0, K - S, disc_k - disc_s)
        upper = disc_k if style == "EUROPEAN" else K
    return lower - tol <= p <= upper + tol


def implied_vol(option_type: str, style: str, S: float, K: float, T: float, r: float, q: float, target: float) -> float:
    lo, hi = 0.01, 3.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if price(option_type, style, S, K, T, r, q, mid) < target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)
