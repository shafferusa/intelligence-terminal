"""Government bond markets outside the dollar: Bunds, Bobls, Schatz and the Buxl (EUR), Gilts (GBP), JGBs (JPY),
OATs (EUR, France) and BTPs (EUR, Italy), each priced off its own currency's curve and settling in its own currency.

The local curves are derived, not simulated separately: a currency's zero curve is the dollar curve plus the policy-rate
differential (the FX model carries every currency's policy rate) weighted toward the front end, plus a term adjustment
that gives each market its shape (JGBs low and flat, Gilts a touch above Bunds), plus a country spread over the euro
curve for France and Italy. Everything that moves the dollar curve or a currency's policy rate moves these curves, so a
save pinned to the real Treasury curve moves its Bunds with it.
"""
from __future__ import annotations

import math
from typing import Dict, List, Tuple

from ..domain.models import YieldCurve

# currency -> (term adjustment at the long end, as a fraction; label)
CURVE_ADJ: Dict[str, Tuple[float, str]] = {"EUR": (-0.0020, "euro area"), "GBP": (0.0010, "sterling"), "JPY": (-0.0060, "yen")}
# country -> (currency, spread over that currency's curve at 10 years, in fraction; name)
COUNTRY_SPREAD: Dict[str, Tuple[str, float, str]] = {"DE": ("EUR", 0.0, "Germany"), "FR": ("EUR", 0.0060, "France"), "IT": ("EUR", 0.0120, "Italy"),
                                                    "GB": ("GBP", 0.0, "United Kingdom"), "JP": ("JPY", 0.0, "Japan")}

# id, name, country, currency, years, coupon frequency, ADV face, market code, nickname
SOVEREIGN_SPECS: List[Tuple[str, str, str, str, float, int, int, str, str]] = [
    ("DE-2Y", "German Schatz 2Y", "DE", "EUR", 2.0, 1, 3_000_000_000, "EU_GOVT", "Schatz"),
    ("DE-5Y", "German Bobl 5Y", "DE", "EUR", 5.0, 1, 2_500_000_000, "EU_GOVT", "Bobl"),
    ("DE-10Y", "German Bund 10Y", "DE", "EUR", 10.0, 1, 2_500_000_000, "EU_GOVT", "Bund"),
    ("DE-30Y", "German Buxl 30Y", "DE", "EUR", 30.0, 1, 800_000_000, "EU_GOVT", "Buxl"),
    ("FR-10Y", "French OAT 10Y", "FR", "EUR", 10.0, 1, 1_500_000_000, "EU_GOVT", "OAT"),
    ("FR-30Y", "French OAT 30Y", "FR", "EUR", 30.0, 1, 500_000_000, "EU_GOVT", "OAT"),
    ("IT-2Y", "Italian BTP 2Y", "IT", "EUR", 2.0, 2, 1_500_000_000, "EU_GOVT", "BTP"),
    ("IT-10Y", "Italian BTP 10Y", "IT", "EUR", 10.0, 2, 1_500_000_000, "EU_GOVT", "BTP"),
    ("IT-30Y", "Italian BTP 30Y", "IT", "EUR", 30.0, 2, 400_000_000, "EU_GOVT", "BTP"),
    ("GB-2Y", "UK Gilt 2Y", "GB", "GBP", 2.0, 2, 2_000_000_000, "UK_GOVT", "Gilt"),
    ("GB-5Y", "UK Gilt 5Y", "GB", "GBP", 5.0, 2, 1_500_000_000, "UK_GOVT", "Gilt"),
    ("GB-10Y", "UK Gilt 10Y", "GB", "GBP", 10.0, 2, 1_500_000_000, "UK_GOVT", "Gilt"),
    ("GB-30Y", "UK Gilt 30Y", "GB", "GBP", 30.0, 2, 600_000_000, "UK_GOVT", "Gilt"),
    ("JP-2Y", "Japanese JGB 2Y", "JP", "JPY", 2.0, 2, 300_000_000_000, "JP_GOVT", "JGB"),
    ("JP-5Y", "Japanese JGB 5Y", "JP", "JPY", 5.0, 2, 300_000_000_000, "JP_GOVT", "JGB"),
    ("JP-10Y", "Japanese JGB 10Y", "JP", "JPY", 10.0, 2, 400_000_000_000, "JP_GOVT", "JGB"),
    ("JP-30Y", "Japanese JGB 30Y", "JP", "JPY", 30.0, 2, 100_000_000_000, "JP_GOVT", "JGB"),
]
SOVEREIGN_BY_ID = {s[0]: s for s in SOVEREIGN_SPECS}
CURVE_CURRENCIES = ("USD", "EUR", "GBP", "JPY")


def weight(t: float) -> float:
    """How much of the policy-rate differential reaches tenor t: all of it at the front, about two thirds at the long end."""
    return 0.62 + 0.38 * math.exp(-t / 4.0)


def foreign_curve(ccy: str, usd: YieldCurve, local_policy: float, usd_policy: float, country: str = "") -> YieldCurve:
    """A currency's zero curve from the dollar curve, its policy rate and the market's term adjustment; a country spread on top."""
    if ccy == "USD" and not country:
        return usd
    adj, _ = CURVE_ADJ.get(ccy, (0.0, ccy))
    spread = COUNTRY_SPREAD.get(country, (ccy, 0.0, ""))[1] if country else 0.0
    diff = local_policy - usd_policy
    rates = []
    for t, r in zip(usd.tenors, usd.rates):
        z = r + diff * weight(t) + adj * min(1.0, t / 10.0) + spread * min(1.0, 0.4 + 0.6 * t / 10.0)
        rates.append(max(-0.01, z))
    policy = max(-0.01, local_policy)
    return YieldCurve(usd.date, list(usd.tenors), rates, ig_spread_bps=usd.ig_spread_bps, hy_spread_bps=usd.hy_spread_bps, policy_rate=round(policy, 5))


def par_coupon(curve: YieldCurve, years: float, freq: int) -> float:
    """The coupon that prices a bullet at par on `curve`, rounded to an eighth of a percent (new sovereign issues start near par)."""
    from .pricing import interp_rate
    f = max(1, freq)
    n = int(round(years * f))
    dfs = [1.0 / (1.0 + interp_rate(curve, k / f) / f) ** k for k in range(1, n + 1)]
    ann = sum(dfs)
    c = f * (1.0 - dfs[-1]) / ann if ann > 0 else 0.0
    return max(0.0, round(c * 800) / 800)
