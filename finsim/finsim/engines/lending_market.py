"""The securities-lending market for each stock: lendable supply, utilization,
borrow rates, and 'special' episodes. A pure function of (seed, date, regime,
volatility) plus a persisted state so replay can ingest it.

Rate = base + utilization curve + volatility premium + stress premium + special
premium. Utilization moves with a random walk of 'other' borrowers' demand, and
specials (short squeezes, event-driven demand) arrive as seeded episodes that
push rates into the tens of percent for a few weeks.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Tuple

from ..domain.models import Security

LENDERS = ["State Street Agency Lending", "BNY Mellon Securities Lending", "J.P. Morgan Agency Lending"]


@dataclass
class LendingState:
    supply: int                 # lendable shares
    on_loan_other: int          # borrowed by other market participants
    rate: float = 0.005         # annual borrow fee
    special_days: int = 0       # remaining days of a special episode
    special_premium: float = 0.0
    utilization: float = 0.0
    category: str = "GC"        # GC | HTB | SPECIAL
    demand_z: float = 0.0


class LendingMarket:
    def __init__(self, seed: int):
        self.seed = seed
        self.state: Dict[str, LendingState] = {}
        self.history: Dict[str, List[Tuple[str, float, float]]] = {}   # sec -> [(date, rate, utilization)]

    def init_security(self, sec: Security) -> None:
        if sec.shares_outstanding and sec.id not in self.state:
            rng = random.Random(f"{self.seed}|lend0|{sec.id}")
            supply = max(1_000, int(sec.shares_outstanding * rng.uniform(0.04, 0.15)))
            util = {"LARGE": rng.uniform(0.05, 0.3), "MID": rng.uniform(0.15, 0.5), "SMALL": rng.uniform(0.3, 0.8)}.get(sec.liquidity_tier, 0.3)
            self.state[sec.id] = LendingState(supply=supply, on_loan_other=int(supply * util))
            self.history[sec.id] = []

    def step(self, d: date, securities: Dict[str, Security], regime: str, realized_vol, player_on_loan: Dict[str, int], weekend_days: int) -> Tuple[Dict[str, Dict], List[Dict]]:
        stress = {"NORMAL_GROWTH": 0.0, "RATE_CUTTING": 0.0, "RATE_HIKING": 0.003, "RECESSION": 0.015, "LIQUIDITY_STRESS": 0.05}.get(regime, 0.0)
        out: Dict[str, Dict] = {}
        news: List[Dict] = []
        for sid, st in self.state.items():
            sec = securities[sid]
            rng = random.Random(f"{self.seed}|lend|{sid}|{d.isoformat()}")
            # other borrowers' demand random walk, mean-reverting
            st.demand_z = 0.97 * st.demand_z + 0.12 * rng.gauss(0, 1)
            base_util = min(0.98, max(0.01, (st.on_loan_other / st.supply) if st.supply else 0.0))
            if not st.supply:
                continue
            target = min(0.98, max(0.02, base_util + 0.05 * st.demand_z))
            st.on_loan_other = int(st.supply * target)
            # specials
            if st.special_days > 0:
                st.special_days -= 1
                st.special_premium *= 0.94
                if st.special_days == 0:
                    st.special_premium = 0.0
            else:
                p = {"LARGE": 0.002, "MID": 0.006, "SMALL": 0.015}.get(sec.liquidity_tier, 0.005) * weekend_days
                if rng.random() < p:
                    st.special_days = rng.randint(8, 30)
                    st.special_premium = rng.uniform(0.10, 0.60)
                    st.on_loan_other = int(min(st.supply * 0.97, st.on_loan_other + st.supply * rng.uniform(0.2, 0.5)))
                    news.append({"code": sid, "headline": f"{sec.name} borrow goes special: {sec.id} shares hard to borrow as short demand surges",
                                 "category": "SEC_LENDING", "body": f"Lending desks report utilization above {min(0.97, st.on_loan_other / st.supply):.0%} in {sec.id}; "
                                 f"borrow fees are being re-rated sharply higher and recalls are more likely while the episode lasts."})
            total = st.on_loan_other + player_on_loan.get(sid, 0)
            util = min(1.0, total / st.supply) if st.supply else 1.0
            vol = realized_vol(sid)
            rate = 0.0025 + 0.02 * util ** 3 + (0.15 * util ** 6) + 0.01 * max(0.0, vol - 0.3) + stress + st.special_premium
            rate *= rng.uniform(0.95, 1.05)
            st.rate = round(min(1.5, rate), 5)
            st.utilization = util
            st.category = "SPECIAL" if st.rate >= 0.10 else "HTB" if st.rate >= 0.02 else "GC"
            self.history[sid].append((d.isoformat(), st.rate, util))
            out[sid] = {"supply": st.supply, "on_loan_other": st.on_loan_other, "rate": st.rate, "special_days": st.special_days,
                        "special_premium": st.special_premium, "utilization": util, "category": st.category, "demand_z": st.demand_z}
        return out, news

    def ingest(self, d: date, payload: Dict[str, Dict]) -> None:
        for sid, p in payload.items():
            st = self.state.get(sid)
            if st is None:
                st = LendingState(supply=int(p["supply"]), on_loan_other=int(p["on_loan_other"]))
                self.state[sid] = st
                self.history[sid] = []
            st.supply, st.on_loan_other, st.rate = int(p["supply"]), int(p["on_loan_other"]), float(p["rate"])
            st.special_days, st.special_premium = int(p["special_days"]), float(p["special_premium"])
            st.utilization, st.category, st.demand_z = float(p["utilization"]), p["category"], float(p.get("demand_z", 0.0))
            self.history[sid].append((d.isoformat(), st.rate, st.utilization))

    def available(self, sid: str, player_on_loan: int) -> int:
        st = self.state.get(sid)
        if st is None:
            return 0
        return max(0, st.supply - st.on_loan_other - player_on_loan)

    def lender_for(self, sid: str, d: date) -> str:
        return LENDERS[random.Random(f"{self.seed}|lender|{sid}|{d.isoformat()}").randrange(len(LENDERS))]
