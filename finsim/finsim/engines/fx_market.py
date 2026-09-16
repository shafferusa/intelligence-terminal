"""FX market: spot rates (base-currency units per one unit of foreign currency)
and a short rate per currency, so forwards price by covered interest parity.
Spots load on the equity market factor (AUD, CAD risk-on; JPY, CHF safe havens)
and foreign rates drift with the USD level."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Tuple

CURRENCIES = ["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD"]


@dataclass(frozen=True)
class CcySpec:
    code: str
    spot0: float          # USD per 1 unit
    rate0: float
    vol: float
    mkt_beta: float
    spread_bps: float
    inverse_quote: bool   # display as units per USD (JPY)


SPECS = {
    "EUR": CcySpec("EUR", 1.08, 0.025, 0.08, 0.10, 1.5, False),
    "GBP": CcySpec("GBP", 1.27, 0.045, 0.09, 0.20, 2.0, False),
    "JPY": CcySpec("JPY", 1 / 149.0, 0.005, 0.10, -0.35, 2.0, True),
    "CHF": CcySpec("CHF", 1.12, 0.010, 0.08, -0.25, 2.5, False),
    "CAD": CcySpec("CAD", 0.73, 0.035, 0.07, 0.30, 2.5, False),
    "AUD": CcySpec("AUD", 0.66, 0.040, 0.11, 0.45, 3.0, False),
}


class FXModel:
    def __init__(self, seed: int):
        self.seed = seed
        self.spot: Dict[str, float] = {"USD": 1.0, **{c: s.spot0 for c, s in SPECS.items()}}
        self.rate: Dict[str, float] = {"USD": 0.0425, **{c: s.rate0 for c, s in SPECS.items()}}
        self.history: Dict[str, List[Tuple[str, float, float]]] = {c: [] for c in CURRENCIES}   # (date, spot, rate)

    def step(self, d: date, mkt_factor: float, usd_rate: float, usd_rate_change: float) -> Dict[str, Dict]:
        dt = 1.0 / 252.0
        self.rate["USD"] = usd_rate
        out = {"USD": {"spot": 1.0, "rate": usd_rate}}
        for c, s in SPECS.items():
            rng = random.Random(f"{self.seed}|fx|{c}|{d.isoformat()}")
            # foreign rate follows the USD rate with a lag and its own noise
            self.rate[c] = max(-0.01, min(0.15, self.rate[c] + 0.5 * usd_rate_change + 0.03 * (s.rate0 - self.rate[c]) * dt * 12 + 0.0003 * rng.gauss(0, 1)))
            carry = (usd_rate - self.rate[c]) * dt          # higher USD rates support USD (foreign spot drifts down slightly)
            r = -0.3 * carry + s.mkt_beta * mkt_factor + s.vol * math.sqrt(dt) * rng.gauss(0, 1) + 0.002 * math.log(s.spot0 / self.spot[c]) * dt * 252 / 20
            self.spot[c] = self.spot[c] * math.exp(max(-0.08, min(0.08, r)))
            out[c] = {"spot": self.spot[c], "rate": self.rate[c]}
        for c in CURRENCIES:
            self.history[c].append((d.isoformat(), self.spot[c], self.rate[c]))
        return out

    def ingest(self, d: date, payload: Dict[str, Dict]) -> None:
        for c, p in payload.items():
            self.spot[c], self.rate[c] = float(p["spot"]), float(p["rate"])
            self.history.setdefault(c, []).append((d.isoformat(), self.spot[c], self.rate[c]))

    def cross(self, buy: str, sell: str) -> float:
        """sell-currency units per 1 buy-currency unit."""
        return self.spot[buy] / self.spot[sell]

    def forward(self, buy: str, sell: str, d: date, maturity: date) -> float:
        """Covered interest parity: F = S * (1 + r_sell*T) / (1 + r_buy*T)."""
        T = max(0.0, (maturity - d).days / 365.0)
        return self.cross(buy, sell) * (1 + self.rate[sell] * T) / (1 + self.rate[buy] * T)

    def spread_bps(self, buy: str, sell: str) -> float:
        return max(SPECS.get(buy, CcySpec(buy, 1, 0, 0, 0, 1.0, False)).spread_bps, SPECS.get(sell, CcySpec(sell, 1, 0, 0, 0, 1.0, False)).spread_bps)
