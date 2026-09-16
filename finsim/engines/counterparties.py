"""Dealers, their credit state and ISDA/CSA terms.

Six fictional dealers quote OTC products by request-for-quote. Each has a
credit rating, a CDS spread that drifts with the credit cycle and its own
shocks (stored in the market close so replay is exact), a capital base, and a
quoting style: how wide it quotes each product and how much it skews to win
business. Under stress every dealer widens; weaker dealers widen more and
occasionally decline to quote.

An ISDA master with a CSA governs every trade with a dealer: threshold,
minimum transfer amount, independent amounts (initial margin) by product and
eligible collateral. One CSA = one netting set.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Tuple

from ..money import D


@dataclass(frozen=True)
class DealerSpec:
    key: str
    name: str
    rating: str
    cds0: float             # 5y CDS, bps
    beta_credit: float      # sensitivity to the HY index (per 100bp of HY move -> bps)
    capital_bn: float
    products: Tuple[str, ...]
    width: float            # 1.0 = standard bid/offer, <1 tighter
    skew: float             # -1..1 systematic lean (negative = pays up for the player's flow)
    style: str
    product_width: Tuple[Tuple[str, float], ...] = ()   # per-product overrides of `width`

    def width_for(self, product: str) -> float:
        return dict(self.product_width).get(product, self.width)


DEALERS: Dict[str, DealerSpec] = {
    "ATLAS": DealerSpec("ATLAS", "Atlas Capital Markets", "A+", 55, 0.10, 24.0, ("IRS", "FRA", "CAP", "FLOOR", "SWAPTION", "XCCY", "TRS", "CDS", "COMMODITY_SWAP"), 0.9, 0.0,
                        "Bulge bracket: tight in rates and credit, shows everything.", (("TRS", 1.15), ("COMMODITY_SWAP", 1.3), ("XCCY", 1.1))),
    "MERIDIAN": DealerSpec("MERIDIAN", "Meridian Bank Derivatives", "A", 70, 0.12, 16.0, ("IRS", "FRA", "CAP", "FLOOR", "SWAPTION", "XCCY", "TRS", "CDS"), 1.0, -0.1,
                           "Relationship bank: slightly wider, leans to win.", (("XCCY", 0.8), ("CAP", 0.85), ("FLOOR", 0.85))),
    "HARBOR": DealerSpec("HARBOR", "Harbor Securities Swaps", "A-", 85, 0.15, 9.0, ("IRS", "FRA", "SWAPTION", "TRS", "CDS", "COMMODITY_SWAP"), 1.1, 0.1,
                         "Broker-dealer: competitive in equity swaps and commodities, wider in rates.", (("TRS", 0.7), ("COMMODITY_SWAP", 0.85))),
    "KESTREL": DealerSpec("KESTREL", "Kestrel Global Markets", "BBB+", 120, 0.25, 5.0, ("IRS", "CAP", "FLOOR", "XCCY", "TRS", "COMMODITY_SWAP"), 1.25, -0.2,
                          "Aggressive mid-tier: cheapest when calm, first to widen and to decline in stress.", (("XCCY", 0.7), ("IRS", 0.95))),
    "NORDBANK": DealerSpec("NORDBANK", "Nordbank International", "AA-", 40, 0.08, 30.0, ("IRS", "FRA", "CAP", "FLOOR", "SWAPTION", "XCCY", "CDS"), 1.05, 0.15,
                           "Conservative universal bank: never the tightest, always there."),
    "VANTAGE": DealerSpec("VANTAGE", "Vantage Commodities & Credit", "BBB", 160, 0.35, 3.0, ("COMMODITY_SWAP", "CDS", "TRS"), 1.15, -0.15,
                          "Specialist: sharp in commodities and credit, fragile balance sheet."),
}
RATING_ORDER = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-", "BB+", "BB", "BB-", "B+", "B", "B-", "CCC", "D"]

# standard bid/offer half-widths per product (in the product's quote unit) in a normal regime
HALF_WIDTH = {"IRS": 0.75, "FRA": 1.0, "CAP": 0.03, "FLOOR": 0.03, "SWAPTION": 0.03, "XCCY": 2.0, "TRS": 7.5, "CDS": 3.0, "COMMODITY_SWAP": 0.004}
UNIT = {"IRS": "bp of fixed rate", "FRA": "bp of rate", "CAP": "fraction of premium", "FLOOR": "fraction of premium", "SWAPTION": "fraction of premium",
        "XCCY": "bp of basis", "TRS": "bp of financing spread", "CDS": "bp of spread", "COMMODITY_SWAP": "fraction of fixed price"}
REGIME_WIDTH = {"NORMAL_GROWTH": 1.0, "RATE_CUTTING": 1.1, "RATE_HIKING": 1.3, "RECESSION": 1.8, "LIQUIDITY_STRESS": 3.0}

# CSA terms by dealer: threshold and MTA in base currency, IM by product (fraction of notional)
CSA_TERMS = {
    "ATLAS": {"threshold": 250_000, "mta": 100_000, "im": {"IRS": 0.010, "FRA": 0.003, "CAP": 0.0, "FLOOR": 0.0, "SWAPTION": 0.0, "XCCY": 0.030, "TRS": 0.100, "CDS": 0.040, "COMMODITY_SWAP": 0.080}},
    "MERIDIAN": {"threshold": 500_000, "mta": 100_000, "im": {"IRS": 0.012, "FRA": 0.004, "CAP": 0.0, "FLOOR": 0.0, "SWAPTION": 0.0, "XCCY": 0.035, "TRS": 0.120, "CDS": 0.050, "COMMODITY_SWAP": 0.100}},
    "HARBOR": {"threshold": 0, "mta": 50_000, "im": {"IRS": 0.015, "FRA": 0.005, "CAP": 0.0, "FLOOR": 0.0, "SWAPTION": 0.0, "XCCY": 0.040, "TRS": 0.150, "CDS": 0.060, "COMMODITY_SWAP": 0.120}},
    "KESTREL": {"threshold": 0, "mta": 50_000, "im": {"IRS": 0.020, "FRA": 0.006, "CAP": 0.0, "FLOOR": 0.0, "SWAPTION": 0.0, "XCCY": 0.050, "TRS": 0.200, "CDS": 0.080, "COMMODITY_SWAP": 0.150}},
    "NORDBANK": {"threshold": 1_000_000, "mta": 250_000, "im": {"IRS": 0.008, "FRA": 0.003, "CAP": 0.0, "FLOOR": 0.0, "SWAPTION": 0.0, "XCCY": 0.025, "TRS": 0.100, "CDS": 0.040, "COMMODITY_SWAP": 0.080}},
    "VANTAGE": {"threshold": 0, "mta": 25_000, "im": {"IRS": 0.020, "FRA": 0.006, "CAP": 0.0, "FLOOR": 0.0, "SWAPTION": 0.0, "XCCY": 0.050, "TRS": 0.200, "CDS": 0.100, "COMMODITY_SWAP": 0.150}},
}


@dataclass
class DealerState:
    cds: float
    rating: str
    stress: float = 0.0        # 0..1 idiosyncratic funding stress
    defaulted: bool = False
    default_date: Optional[str] = None


class DealerModel:
    """Daily credit state of the dealers (pure function of seed and the credit cycle)."""

    def __init__(self, seed: int):
        self.seed = seed
        self.state: Dict[str, DealerState] = {k: DealerState(s.cds0, s.rating) for k, s in DEALERS.items()}
        self.history: Dict[str, List[Tuple[str, float]]] = {k: [] for k in DEALERS}
        self._hy_prev: Optional[float] = None

    def step(self, d: date, regime: str, hy_bps: float, mkt: float) -> Tuple[Dict[str, Dict], List[Dict]]:
        news = []
        d_hy = 0.0 if self._hy_prev is None else hy_bps - self._hy_prev
        self._hy_prev = hy_bps
        out = {}
        for k, spec in DEALERS.items():
            st = self.state[k]
            if st.defaulted:
                out[k] = {"cds": st.cds, "rating": st.rating, "stress": st.stress, "defaulted": True}
                continue
            rng = random.Random(f"{self.seed}|dealer|{k}|{d.isoformat()}")
            # idiosyncratic stress: rare shocks that decay
            st.stress *= 0.93
            shock_p = {"NORMAL_GROWTH": 0.002, "RATE_CUTTING": 0.002, "RATE_HIKING": 0.004, "RECESSION": 0.012, "LIQUIDITY_STRESS": 0.03}[regime] * (1.0 + (spec.cds0 - 40) / 60)
            if rng.random() < shock_p:
                st.stress = min(1.0, st.stress + rng.uniform(0.3, 0.8))
                news.append({"code": k, "headline": f"{spec.name} CDS gaps wider on funding concerns", "category": "COUNTERPARTY",
                             "body": f"Protection on {spec.name} ({spec.rating}) repriced sharply as market participants questioned the dealer's funding position. "
                                     "Counterparties are reviewing exposure and collateral terms."})
            target = spec.cds0 * (1 + 0.6 * max(0.0, hy_bps - 360) / 360) * (1 + 2.5 * st.stress)
            st.cds = max(15.0, st.cds + 0.12 * (target - st.cds) + spec.beta_credit * d_hy + 0.01 * st.cds * rng.gauss(0, 1) - 4.0 * spec.beta_credit * mkt * 10)
            # rating migration: sustained wide spreads notch down; recovery notches up slowly
            idx = RATING_ORDER.index(st.rating)
            if st.cds > 3.0 * spec.cds0 and idx < len(RATING_ORDER) - 2 and rng.random() < 0.05:
                st.rating = RATING_ORDER[idx + 1]
                news.append({"code": k, "headline": f"{spec.name} downgraded to {st.rating}", "category": "COUNTERPARTY",
                             "body": f"Rating agencies cut {spec.name} to {st.rating}, citing wider credit spreads and funding stress."})
            elif st.cds < 1.2 * spec.cds0 and st.rating != spec.rating and idx > 0 and rng.random() < 0.02:
                st.rating = RATING_ORDER[idx - 1]
            self.history[k].append((d.isoformat(), st.cds))
            # a dealer under sustained funding stress can fail (rare; the world closes its netting sets out)
            default_now = st.stress > 0.75 and st.cds > 4 * spec.cds0 and rng.random() < 0.04
            out[k] = {"cds": st.cds, "rating": st.rating, "stress": st.stress, "defaulted": False, "default_now": default_now}
        return out, news

    def ingest(self, d: date, payload: Dict[str, Dict]) -> None:
        for k, p in payload.items():
            st = self.state.setdefault(k, DealerState(float(p["cds"]), p["rating"]))
            st.cds, st.rating, st.stress, st.defaulted = float(p["cds"]), p["rating"], float(p.get("stress", 0.0)), bool(p.get("defaulted", False))
            self.history.setdefault(k, []).append((d.isoformat(), st.cds))

    def default_probability_1y(self, key: str, recovery: float = 0.4) -> float:
        st = self.state[key]
        return min(0.99, (st.cds / 1e4) / (1 - recovery))

    def mark_defaulted(self, key: str, d: date) -> None:
        st = self.state[key]
        st.defaulted, st.default_date = True, d.isoformat()


def quote_half_width(product: str, dealer: str, regime: str, stress: float) -> float:
    spec = DEALERS[dealer]
    return HALF_WIDTH[product] * spec.width_for(product) * REGIME_WIDTH.get(regime, 1.0) * (1 + 2.0 * stress)


def dealers_for(product: str) -> List[str]:
    return [k for k, s in DEALERS.items() if product in s.products]


# ---------------------------------------------------------------- clients (the desk's customers when the player is the dealer)
CLIENT_SPECS: Dict[str, Dict] = {
    "PENSION_A": {"name": "Lakeshore Teachers' Pension", "rating": "AA", "cds": 35.0},
    "INSURER_B": {"name": "Orchard Life Insurance", "rating": "A+", "cds": 60.0},
    "HF_C": {"name": "Kestrel Macro Fund", "rating": "BBB", "cds": 220.0},
    "CORP_D": {"name": "Titan Machinery Treasury", "rating": "A-", "cds": 95.0},
    "AM_E": {"name": "Meridian Asset Management", "rating": "A", "cds": 70.0},
    "SOV_F": {"name": "Nordic Sovereign Reserve", "rating": "AAA", "cds": 20.0},
}
CLIENT_CSA = {"threshold": 1_000_000, "mta": 250_000, "im": {p: 0.0 for p in HALF_WIDTH}}


def counterparty_name(key: str) -> str:
    if key in DEALERS:
        return DEALERS[key].name
    if key.startswith("CLIENT:"):
        return CLIENT_SPECS.get(key[7:], {}).get("name", key[7:])
    return key


def csa_terms_for(key: str) -> Dict:
    return CSA_TERMS[key] if key in CSA_TERMS else CLIENT_CSA
