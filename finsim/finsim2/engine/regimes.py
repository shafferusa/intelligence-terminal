"""RegimeEngine: point-in-time regime labels on the calendar.

Each dimension has two states, decided only from data known on the date:
  market     bull / bear          SPY above its 200-day average and less than 20% below its 1-year high
  volatility high / low           VIX above its trailing 5-year median
  rates      rising / falling     10-year yield up over the last 3 months
  inflation  high / low           CPI year on year above 3% (as published)
  growth     recession / expansion  Sahm-style unemployment gap at or above 0.5 point
  dollar     strong / weak        dollar index above its 200-day average
  liquidity  expansion / contraction  Chicago Fed financial conditions looser than average (NFCI < 0)
"""
from __future__ import annotations

import bisect
from collections import deque
from typing import Dict, List, Optional

from .features import roll_max, roll_mean

DIMENSIONS = {
    "market": ("Bull market", "Bear market"),
    "volatility": ("High volatility", "Low volatility"),
    "rates": ("Rising rates", "Falling rates"),
    "inflation": ("High inflation", "Low inflation"),
    "growth": ("Recession", "Expansion"),
    "dollar": ("Strong dollar", "Weak dollar"),
    "liquidity": ("Liquidity expansion", "Liquidity contraction"),
}
STATE_KEYS = {"market": ("bull", "bear"), "volatility": ("high_vol", "low_vol"), "rates": ("rising_rates", "falling_rates"),
              "inflation": ("high_inflation", "low_inflation"), "growth": ("recession", "expansion"),
              "dollar": ("strong_dollar", "weak_dollar"), "liquidity": ("liquidity_expansion", "liquidity_contraction")}
STATE_LABEL = {STATE_KEYS[d][i]: DIMENSIONS[d][i] for d in DIMENSIONS for i in (0, 1)}


def _trailing_median(xs: List[Optional[float]], w: int, minp: int = 126) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(xs)
    window: List[float] = []
    q: deque = deque()
    for i, v in enumerate(xs):
        q.append(v)
        if v is not None:
            bisect.insort(window, v)
        if len(q) > w:
            old = q.popleft()
            if old is not None:
                del window[bisect.bisect_left(window, old)]
        if len(window) >= minp:
            m = len(window)
            out[i] = window[m // 2] if m % 2 else 0.5 * (window[m // 2 - 1] + window[m // 2])
    return out


def compute(panel, macro: Dict[str, list]) -> Dict[str, List[Optional[str]]]:
    """{dimension: [state key or None per calendar date]} using the panel and the shared macro features."""
    spy = panel.series(panel.benchmark)
    n = len(spy)
    ma200 = roll_mean(spy, 200)
    hi = roll_max(spy, 252)
    out: Dict[str, List[Optional[str]]] = {d: [None] * n for d in DIMENSIONS}
    for i in range(n):
        if spy[i] is not None and ma200[i] is not None and hi[i]:
            out["market"][i] = "bull" if spy[i] > ma200[i] and spy[i] / hi[i] - 1 > -0.20 else "bear"
    vix = macro.get("vix") or [None] * n
    vmed = _trailing_median(vix, 1260)
    d10 = macro.get("d_y10_3m") or [None] * n
    cpi = macro.get("cpi_yoy") or [None] * n
    gap = macro.get("unemp_gap") or [None] * n
    nfci = macro.get("nfci") or [None] * n
    try:
        dollar = panel.series("DXY")
    except Exception:
        dollar = [None] * n
    dma = roll_mean(dollar, 200) if any(v is not None for v in dollar) else [None] * n
    for i in range(n):
        if vix[i] is not None and vmed[i] is not None:
            out["volatility"][i] = "high_vol" if vix[i] > vmed[i] else "low_vol"
        if d10[i] is not None:
            out["rates"][i] = "rising_rates" if d10[i] > 0 else "falling_rates"
        if cpi[i] is not None:
            out["inflation"][i] = "high_inflation" if cpi[i] > 0.03 else "low_inflation"
        if gap[i] is not None:
            out["growth"][i] = "recession" if gap[i] >= 0.5 else "expansion"
        if dollar[i] is not None and dma[i] is not None:
            out["dollar"][i] = "strong_dollar" if dollar[i] > dma[i] else "weak_dollar"
        if nfci[i] is not None:
            out["liquidity"][i] = "liquidity_expansion" if nfci[i] < 0 else "liquidity_contraction"
    return out


def current(regimes: Dict[str, List[Optional[str]]], i: Optional[int] = None) -> Dict[str, Optional[str]]:
    """The state of each dimension at index i (default: the last date with a label)."""
    out = {}
    for d, s in regimes.items():
        j = len(s) - 1 if i is None else i
        while j >= 0 and s[j] is None:
            j -= 1
        out[d] = s[j] if j >= 0 else None
    return out


def describe(state: Dict[str, Optional[str]]) -> str:
    """A short name for the current regime, e.g. 'Bull market, low volatility, falling rates'."""
    parts = [STATE_LABEL[state[d]] for d in ("market", "volatility", "rates") if state.get(d)]
    if not parts:
        return "Unknown"
    s = ", ".join(parts).lower()
    return s[0].upper() + s[1:]


def history_share(regimes: Dict[str, List[Optional[str]]], state: Dict[str, Optional[str]]) -> Dict[str, float]:
    """Share of labelled history spent in the current state, per dimension (regime similarity)."""
    out = {}
    for d, s in regimes.items():
        lab = [x for x in s if x is not None]
        out[d] = (sum(1 for x in lab if x == state.get(d)) / len(lab)) if lab and state.get(d) else 0.0
    return out
