"""Implied-volatility surfaces.

Each optionable underlying carries a small state: ATM implied volatility (30-day),
a skew slope, a smile curvature and a term-structure slope. The ATM level is
persistent and mean-reverts toward a target blending the underlying's structural
volatility, its 20-day realized volatility, the market's volatility index and the
regime; it also jumps with the market's volatility index. Skew steepens and the
term structure inverts (front above back) in stress.

IV(K, T) = ATM(T) * exp(skew_T * m + curv * m^2),  m = ln(K / F),
ATM(T)   = ATM_30 * (1 + term * (sqrt(T) - sqrt(30/365))),
skew_T   = skew * (30/365 / T)^0.3   (short-dated skew is steeper).

State is stored in the MARKET_CLOSE payload so replay ingests it.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Tuple

REGIME_IV = {"NORMAL_GROWTH": 0.0, "RATE_CUTTING": 0.0, "RATE_HIKING": 0.03, "RECESSION": 0.10, "LIQUIDITY_STRESS": 0.25}
REGIME_SKEW = {"NORMAL_GROWTH": 1.0, "RATE_CUTTING": 1.0, "RATE_HIKING": 1.1, "RECESSION": 1.4, "LIQUIDITY_STRESS": 1.8}
REGIME_TERM = {"NORMAL_GROWTH": 0.08, "RATE_CUTTING": 0.08, "RATE_HIKING": 0.04, "RECESSION": -0.05, "LIQUIDITY_STRESS": -0.15}
BASE_T = 30 / 365


@dataclass
class VolState:
    atm: float          # 30-day ATM implied vol
    skew: float         # slope per unit log-moneyness (negative for equities)
    curv: float         # smile curvature
    term: float         # term slope: positive = upward-sloping ATM term structure


class VolSurfaceModel:
    def __init__(self, seed: int):
        self.seed = seed
        self.state: Dict[str, VolState] = {}
        self.history: Dict[str, List[Tuple[str, float, float, float]]] = {}   # (date, atm, skew, term)

    def init_underlying(self, sid: str, structural_vol: float, is_index: bool = False) -> None:
        if sid in self.state:
            return
        base_skew = -0.35 if is_index else -0.22
        self.state[sid] = VolState(atm=max(0.08, structural_vol * 1.05), skew=base_skew, curv=0.12 if not is_index else 0.06, term=0.08)
        self.history[sid] = []

    def step(self, d: date, sid: str, structural_vol: float, realized_20d: float, vol_index: float, vol_index_change: float, regime: str,
             is_index: bool = False) -> Dict:
        st = self.state[sid]
        rng = random.Random(f"{self.seed}|iv|{sid}|{d.isoformat()}")
        target = 0.45 * structural_vol + 0.30 * realized_20d + 0.25 * (vol_index / 100.0) * (structural_vol / 0.18) + REGIME_IV[regime] * (structural_vol / 0.18) * 0.5
        target = max(0.06, min(1.5, target))
        st.atm = st.atm + 0.08 * (target - st.atm) + 0.35 * st.atm * vol_index_change + 0.012 * st.atm * rng.gauss(0, 1)
        st.atm = max(0.05, min(2.0, st.atm))
        base_skew = -0.35 if is_index else -0.22
        st.skew = st.skew + 0.15 * (base_skew * REGIME_SKEW[regime] - st.skew) + 0.01 * rng.gauss(0, 1)
        st.term = st.term + 0.15 * (REGIME_TERM[regime] - st.term) + 0.004 * rng.gauss(0, 1)
        self.history[sid].append((d.isoformat(), st.atm, st.skew, st.term))
        return {"atm": st.atm, "skew": st.skew, "curv": st.curv, "term": st.term}

    def ingest(self, d: date, payload: Dict[str, Dict]) -> None:
        for sid, p in payload.items():
            st = self.state.get(sid)
            if st is None:
                st = VolState(p["atm"], p["skew"], p["curv"], p["term"])
                self.state[sid] = st
                self.history[sid] = []
            st.atm, st.skew, st.curv, st.term = float(p["atm"]), float(p["skew"]), float(p["curv"]), float(p["term"])
            self.history[sid].append((d.isoformat(), st.atm, st.skew, st.term))

    def atm_for_tenor(self, sid: str, T: float) -> float:
        st = self.state[sid]
        T = max(1 / 365, T)
        return max(0.03, st.atm * (1 + st.term * (math.sqrt(T) - math.sqrt(BASE_T))))

    def iv(self, sid: str, strike: float, forward: float, T: float) -> float:
        st = self.state[sid]
        T = max(1 / 365, T)
        m = math.log(max(1e-9, strike) / max(1e-9, forward))
        skew_T = st.skew * (BASE_T / T) ** 0.3
        v = self.atm_for_tenor(sid, T) * math.exp(skew_T * m + st.curv * m * m)
        return max(0.03, min(3.0, v))

    def iv_rank(self, sid: str, window: int = 252) -> Optional[Dict]:
        h = self.history.get(sid, [])[-window:]
        if len(h) < 20:
            return None
        atms = [x[1] for x in h]
        cur = atms[-1]
        lo, hi = min(atms), max(atms)
        rank = (cur - lo) / (hi - lo) if hi > lo else 0.5
        pct = sum(1 for a in atms if a <= cur) / len(atms)
        return {"iv_rank": rank, "iv_percentile": pct, "low": lo, "high": hi, "current": cur, "days": len(atms)}


OPTIONABLE_CLASSES = ("EQUITY", "ETF", "REIT", "ADR")
INDEX_ID = "SPXI"
INDEX_SOURCE = "SPXE"


def optionable_underlyings(securities: Dict) -> List[str]:
    """Underlyings that carry a listed option chain and a vol surface (large/mid caps plus the cash index)."""
    out = [s.id for s in securities.values() if s.asset_class in OPTIONABLE_CLASSES and s.liquidity_tier in ("LARGE", "MID") and s.shares_outstanding]
    return sorted(out) + [INDEX_ID]


def structural_vol(securities: Dict, under: str) -> float:
    if under == INDEX_ID:
        return 0.16
    sec = securities[under]
    return math.sqrt((sec.beta * 0.16) ** 2 + sec.sigma_annual ** 2)
