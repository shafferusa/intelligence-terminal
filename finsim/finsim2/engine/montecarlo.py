"""Monte Carlo for a portfolio (or one asset): future paths from the past, not promises about the future.

`bootstrap`: stationary block bootstrap of the portfolio's historical daily returns at today's weights (keeps fat
tails, volatility clustering within blocks and the assets' co-movement). `gbm`: geometric Brownian motion with the
same mean and volatility (thin tails, for comparison). Reports percentiles of the ending value, probabilities of a
loss, of a gain above 10% and of a drawdown beyond a threshold, and a percentile cone over time.
"""
from __future__ import annotations

import math
import random
from typing import Dict, List, Optional

HORIZON_DAYS = {"1M": 21, "3M": 63, "6M": 126, "1Y": 252, "5Y": 1260, "10Y": 2520}


def _pct(sorted_vals: List[float], q: float) -> float:
    if not sorted_vals:
        return float("nan")
    k = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[k]


def simulate(daily: List[Optional[float]], days: int, paths: int = 2000, method: str = "bootstrap", block: int = 10,
             start_value: float = 1.0, dd_threshold: float = 0.2, seed: int = 0) -> dict:
    hist = [x for x in daily if x is not None]
    if len(hist) < 60:
        raise ValueError("not enough history to simulate (need at least 60 daily returns)")
    rng = random.Random(seed)
    paths = max(200, min(paths, 5000 if days <= 252 else 1500))
    n = len(hist)
    mean = sum(hist) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in hist) / (n - 1))
    mu_log = math.log(1 + mean) - 0.5 * sd * sd
    checkpoints = sorted(set([max(1, round(days * k / 30)) for k in range(1, 31)]))
    cone: Dict[int, List[float]] = {c: [] for c in checkpoints}
    ends, dds = [], []
    p_restart = 1.0 / max(1, block)
    for _ in range(paths):
        v, peak, mdd = 1.0, 1.0, 0.0
        j = rng.randrange(n)
        ci = 0
        for t in range(1, days + 1):
            if method == "gbm":
                r = math.exp(mu_log + sd * rng.gauss(0.0, 1.0)) - 1
            else:
                if rng.random() < p_restart:
                    j = rng.randrange(n)
                else:
                    j = (j + 1) % n
                r = hist[j]
            v *= 1 + r
            if v > peak:
                peak = v
            dd = v / peak - 1
            if dd < mdd:
                mdd = dd
            if ci < len(checkpoints) and t == checkpoints[ci]:
                cone[t].append(v)
                ci += 1
        ends.append(v)
        dds.append(mdd)
    ends.sort()
    pc = {q: _pct(ends, q) * start_value for q in (0.05, 0.25, 0.5, 0.75, 0.95)}
    band = []
    for c in checkpoints:
        vals = sorted(cone[c])
        band.append({"day": c, **{f"p{int(q * 100)}": _pct(vals, q) * start_value for q in (0.05, 0.25, 0.5, 0.75, 0.95)}})
    return {"days": days, "paths": paths, "method": method, "start_value": start_value,
            "mean": sum(ends) / len(ends) * start_value, "median": pc[0.5], "p5": pc[0.05], "p25": pc[0.25], "p75": pc[0.75], "p95": pc[0.95],
            "prob_loss": sum(1 for x in ends if x < 1) / len(ends), "prob_gain_10": sum(1 for x in ends if x > 1.10) / len(ends),
            "prob_drawdown": sum(1 for d in dds if d <= -dd_threshold) / len(dds), "dd_threshold": dd_threshold,
            "hist_days": n, "hist_mean_daily": mean, "hist_vol_ann": sd * math.sqrt(252), "cone": band,
            "note": "Simulated from history: a range of plausible outcomes, not a forecast or a guarantee."}
