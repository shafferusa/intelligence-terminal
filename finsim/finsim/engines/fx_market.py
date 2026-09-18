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

CURRENCIES = ["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "SEK", "NOK", "MXN", "BRL", "CNH", "HKD", "SGD", "KRW", "INR", "ZAR", "PLN",
              "TRY", "TWD", "IDR", "THB", "CZK", "HUF", "SAR"]


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
    "NZD": CcySpec("NZD", 0.60, 0.035, 0.11, 0.45, 4.0, False),
    "SEK": CcySpec("SEK", 1 / 9.5, 0.020, 0.10, 0.30, 4.0, True),
    "NOK": CcySpec("NOK", 1 / 10.0, 0.045, 0.11, 0.35, 5.0, True),
    "MXN": CcySpec("MXN", 1 / 18.5, 0.080, 0.13, 0.50, 6.0, True),
    "BRL": CcySpec("BRL", 1 / 5.4, 0.140, 0.16, 0.50, 10.0, True),
    "CNH": CcySpec("CNH", 1 / 7.15, 0.015, 0.05, 0.10, 4.0, True),
    "HKD": CcySpec("HKD", 1 / 7.8, 0.040, 0.005, 0.0, 2.0, True),
    "SGD": CcySpec("SGD", 1 / 1.30, 0.025, 0.05, 0.20, 3.0, True),
    "KRW": CcySpec("KRW", 1 / 1390.0, 0.025, 0.09, 0.35, 6.0, True),
    "INR": CcySpec("INR", 1 / 88.0, 0.060, 0.05, 0.10, 8.0, True),
    "ZAR": CcySpec("ZAR", 1 / 17.5, 0.075, 0.15, 0.50, 8.0, True),
    "PLN": CcySpec("PLN", 1 / 3.8, 0.050, 0.10, 0.35, 6.0, True),
    "TRY": CcySpec("TRY", 1 / 41.0, 0.400, 0.22, 0.30, 25.0, True),      # the lira: a 40% policy rate and a steady slide
    "TWD": CcySpec("TWD", 1 / 30.5, 0.020, 0.05, 0.25, 4.0, True),
    "IDR": CcySpec("IDR", 1 / 16400.0, 0.055, 0.07, 0.35, 8.0, True),
    "THB": CcySpec("THB", 1 / 32.5, 0.020, 0.07, 0.30, 5.0, True),
    "CZK": CcySpec("CZK", 1 / 21.0, 0.035, 0.09, 0.30, 5.0, True),
    "HUF": CcySpec("HUF", 1 / 340.0, 0.065, 0.11, 0.35, 8.0, True),
    "SAR": CcySpec("SAR", 1 / 3.75, 0.050, 0.002, 0.0, 2.0, True),        # pegged at 3.75
}
# the ICE dollar index: a geometric basket of six currencies (weights EUR 57.6, JPY 13.6, GBP 11.9, CAD 9.1, SEK 4.2, CHF 3.6)
DXY_WEIGHTS = {"EUR": -0.576, "JPY": 0.136, "GBP": -0.119, "CAD": 0.091, "SEK": 0.042, "CHF": 0.036}
DXY_SCALE = 50.14348112


def dollar_index(spot_usd_per_unit: Dict[str, float]) -> float:
    """DXY from USD-per-unit spots: 50.14348112 × EURUSD^−0.576 × USDJPY^0.136 × GBPUSD^−0.119 × USDCAD^0.091 × USDSEK^0.042 × USDCHF^0.036."""
    import math as _m
    level = DXY_SCALE
    for c, wgt in DXY_WEIGHTS.items():
        s_ = spot_usd_per_unit.get(c)
        if not s_:
            return 0.0
        level *= (1.0 / s_) ** abs(wgt)               # every leg as foreign units per dollar: EURUSD^-w is (1/EURUSD)^w
    return level


def apply_snapshot(spots: Dict[str, float]) -> None:
    """Re-anchor each currency's long-run spot to the snapshot."""
    import dataclasses
    for c, v in spots.items():
        if c in SPECS and v:
            SPECS[c] = dataclasses.replace(SPECS[c], spot0=float(v))


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
            self.rate[c] = max(-0.01, min(0.60, self.rate[c] + 0.5 * usd_rate_change + 0.03 * (s.rate0 - self.rate[c]) * dt * 12 + 0.0003 * rng.gauss(0, 1)))
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
