"""SignalEngine: turn raw features into comparable, point-in-time standardised signals.

z_t = (x_t − mean of x up to t) / (std of x up to t), after `MIN_HISTORY` observations, capped at ±CAP so one outlier
cannot dominate a combined score. Only past values enter each z (an expanding window), so there is no future
normalisation.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

Series = List[Optional[float]]
CAP = 3.0
MIN_HISTORY = 252


def standardize(x: Series, min_history: int = MIN_HISTORY, cap: float = CAP) -> Series:
    out: Series = [None] * len(x)
    n = 0
    mean = 0.0
    m2 = 0.0
    for i, v in enumerate(x):
        if v is None:
            continue
        if n >= min_history and m2 > 0:                 # statistics of the values BEFORE today
            sd = math.sqrt(m2 / (n - 1))
            z = (v - mean) / sd if sd > 0 else 0.0
            out[i] = max(-cap, min(cap, z))
        n += 1
        d = v - mean
        mean += d / n
        m2 += d * (v - mean)
    return out


def standardize_all(features: Dict[str, Series], min_history: int = MIN_HISTORY) -> Dict[str, Series]:
    return {k: standardize(v, min_history) for k, v in features.items() if not k.startswith("_")}


def strength_label(z: Optional[float], direction: int = 1) -> Optional[str]:
    """Bullish ... Bearish from a standardised signal and the direction its history says it points."""
    if z is None or direction == 0:
        return None
    s = z * direction
    if s >= 1.5:
        return "Bullish"
    if s >= 0.5:
        return "Slightly Bullish"
    if s <= -1.5:
        return "Bearish"
    if s <= -0.5:
        return "Slightly Bearish"
    return "Neutral"


def score_label(score: Optional[float]) -> Optional[str]:
    """Label for a −100..100 score."""
    if score is None:
        return None
    if score >= 50:
        return "Bullish"
    if score >= 15:
        return "Slightly Bullish"
    if score <= -50:
        return "Bearish"
    if score <= -15:
        return "Slightly Bearish"
    return "Neutral"


def trend(values: Series, lookback: int = 21, tol: float = 0.05) -> Optional[str]:
    """Increasing / Decreasing / Stable over the last `lookback` observations (relative to the series' spread)."""
    vals = [v for v in values if v is not None]
    if len(vals) < lookback + 2:
        return None
    a, b = vals[-lookback - 1], vals[-1]
    tail = vals[-252:]
    mu = sum(tail) / len(tail)
    sd = math.sqrt(sum((v - mu) ** 2 for v in tail) / max(1, len(tail) - 1)) or abs(mu) or 1.0
    ch = (b - a) / sd
    if ch > tol * 4:
        return "Increasing"
    if ch < -tol * 4:
        return "Decreasing"
    return "Stable"


def percentile_of_last(values: Series, window: Optional[int] = None) -> Optional[float]:
    vals = [v for v in values if v is not None]
    if window:
        vals = vals[-window:]
    if len(vals) < 20:
        return None
    x = vals[-1]
    below = sum(1 for v in vals if v < x)
    eq = sum(1 for v in vals if v == x)
    return (below + 0.5 * eq) / len(vals)
