"""Portfolio construction: long-only mean-variance (equation 101) along a frontier, the minimum-variance portfolio
(104), the maximum-Sharpe portfolio, risk parity (equal risk contributions, 105) and the diversification ratio.

Weights are found by projected gradient descent onto {w ≥ 0, Σw = 1, w ≤ cap}; small problems (a few dozen
assets) converge in milliseconds. Expected returns are an input: historical means (shrunk), the quant score's
expected returns, or the ML ensemble's — the caller chooses and the page says which.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional


def project(v: List[float], cap: float = 1.0) -> List[float]:
    """Euclidean projection onto the capped simplex {0 ≤ w ≤ cap, Σw = 1} (bisection on the shift)."""
    n = len(v)
    cap = max(cap, 1.0 / n + 1e-12)
    lo, hi = min(v) - 1.0, max(v)
    for _ in range(100):
        mid = (lo + hi) / 2
        s = sum(min(cap, max(0.0, x - mid)) for x in v)
        if s > 1:
            lo = mid
        else:
            hi = mid
    tau = (lo + hi) / 2
    w = [min(cap, max(0.0, x - tau)) for x in v]
    s = sum(w)
    return [x / s for x in w] if s > 0 else [1.0 / n] * n


def _mv(cov, w):
    return [sum(cov[i][j] * w[j] for j in range(len(w))) for i in range(len(w))]


def port_stats(w, mu, cov, rf=0.0):
    sw = _mv(cov, w)
    var = sum(w[i] * sw[i] for i in range(len(w)))
    ret = sum(w[i] * mu[i] for i in range(len(w)))
    vol = math.sqrt(max(var, 0.0))
    return {"ret": ret, "vol": vol, "sharpe": (ret - rf) / vol if vol > 0 else None}


def mean_variance(mu, cov, lam: float, cap: float = 1.0, iters: int = 400, w0=None) -> List[float]:
    """max wᵀμ − (λ/2) wᵀΣw, long-only, fully invested."""
    n = len(mu)
    w = list(w0) if w0 else [1.0 / n] * n
    L = max(1e-8, lam * max(sum(abs(x) for x in row) for row in cov))    # Lipschitz bound of the gradient
    step = 1.0 / L
    for _ in range(iters):
        g = _mv(cov, w)
        w_new = project([w[i] + step * (mu[i] - lam * g[i]) for i in range(n)], cap)
        if max(abs(a - b) for a, b in zip(w, w_new)) < 1e-9:
            w = w_new
            break
        w = w_new
    return w


def min_variance(cov, cap: float = 1.0) -> List[float]:
    return mean_variance([0.0] * len(cov), cov, 1.0, cap, iters=2000)


def frontier(mu, cov, cap: float = 1.0, rf: float = 0.0, points: int = 25) -> List[dict]:
    out = []
    w = None
    for k in range(points):
        lam = 10 ** (3 - 4.5 * k / (points - 1))        # from very risk-averse to nearly risk-neutral
        w = mean_variance(mu, cov, lam, cap, w0=w)
        st = port_stats(w, mu, cov, rf)
        out.append({"lambda": lam, "weights": w, **st})
    out.sort(key=lambda p: p["vol"])
    return out


def max_sharpe(mu, cov, cap: float = 1.0, rf: float = 0.0) -> dict:
    pts = frontier(mu, cov, cap, rf, points=40)
    best = max((p for p in pts if p["sharpe"] is not None), key=lambda p: p["sharpe"], default=pts[0])
    return best


def risk_parity(cov, iters: int = 500, budget: Optional[List[float]] = None) -> List[float]:
    """Equal (or budgeted) risk contributions: w_i (Σw)_i / wᵀΣw = b_i, long-only."""
    n = len(cov)
    b = budget or [1.0 / n] * n
    w = [1.0 / math.sqrt(cov[i][i]) if cov[i][i] > 0 else 1.0 for i in range(n)]
    s = sum(w); w = [x / s for x in w]
    for _ in range(iters):
        sw = _mv(cov, w)
        var = sum(w[i] * sw[i] for i in range(n))
        if var <= 0:
            break
        rc = [w[i] * sw[i] / var for i in range(n)]
        w = [w[i] * math.sqrt(b[i] / rc[i]) if rc[i] > 0 else w[i] for i in range(n)]
        s = sum(w); w = [x / s for x in w]
        if max(abs(rc[i] - b[i]) for i in range(n)) < 1e-6:
            break
    return w


def risk_contributions(w, cov) -> List[float]:
    sw = _mv(cov, w)
    var = sum(w[i] * sw[i] for i in range(len(w)))
    return [w[i] * sw[i] / var if var > 0 else 0.0 for i in range(len(w))]


def diversification_ratio(w, cov) -> Optional[float]:
    vol = math.sqrt(max(0.0, sum(w[i] * _mv(cov, w)[i] for i in range(len(w)))))
    num = sum(w[i] * math.sqrt(max(cov[i][i], 0.0)) for i in range(len(w)))
    return num / vol if vol > 0 else None


def optimise(assets: List[str], mu: List[float], cov: List[List[float]], current: Optional[Dict[str, float]] = None,
             cap: float = 1.0, rf: float = 0.0) -> dict:
    n = len(assets)
    front = frontier(mu, cov, cap, rf)
    gmv = min_variance(cov, cap)
    ms = max_sharpe(mu, cov, cap, rf)
    rp = risk_parity(cov)
    ew = [1.0 / n] * n

    def pack(name, w):
        return {"name": name, "weights": dict(zip(assets, w)), **port_stats(w, mu, cov, rf), "diversification_ratio": diversification_ratio(w, cov),
                "risk_contributions": dict(zip(assets, risk_contributions(w, cov)))}
    out = {"assets": assets, "frontier": [{"vol": p["vol"], "ret": p["ret"], "sharpe": p["sharpe"], "weights": dict(zip(assets, p["weights"]))} for p in front],
           "portfolios": [pack("Minimum variance", gmv), pack("Maximum Sharpe", ms["weights"]), pack("Risk parity", rp), pack("Equal weight", ew)],
           "assets_points": [{"asset": a, "vol": math.sqrt(max(cov[i][i], 0.0)), "ret": mu[i]} for i, a in enumerate(assets)]}
    if current:
        cw = [current.get(a, 0.0) for a in assets]
        s = sum(cw)
        if s > 0:
            out["portfolios"].insert(0, pack("Current", [x / s for x in cw]))
    return out
