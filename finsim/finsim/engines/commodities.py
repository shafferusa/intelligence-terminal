"""Commodities: fundamentals, spot prices, futures curves, contract listings.

Each commodity carries a small supply/demand state:

  demand_z       demand relative to normal, pulled by the macro regime (copper
                 and oil are cyclical, gold is counter-cyclical, grains barely)
  supply_shock   production disruptions / cuts / bumper harvests, arriving as
                 seeded events (with news) and decaying over weeks
  inventory_z    stocks relative to normal; accumulates (supply - demand) and
                 jumps on scheduled inventory reports (EIA, USDA, LME ...)

Spot prices respond to *changes* in the balance (a supply cut or an inventory
draw pushes price up), to the market factor, and to seasonality. The futures
curve is cost-of-carry: F(T) = S * exp((r + storage - convenience) * T) with a
convenience yield that rises when inventories are tight — so shortages put the
curve in backwardation and gluts push it into contango. Financial futures
(equity index, 10Y note) use their own carry.

Every draw is seeded by (world_seed, commodity, date); replay ingests the
stored state instead of regenerating.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from ..calendar import BusinessCalendar
from ..domain.models import Bar, Security
from ..money import D, price as qprice

MONTH_CODES = "FGHJKMNQUVXZ"      # Jan..Dec
CODE_TO_MONTH = {c: i + 1 for i, c in enumerate(MONTH_CODES)}


@dataclass(frozen=True)
class CommoditySpec:
    code: str
    name: str
    group: str                 # COMMODITY_ENERGY | COMMODITY_METAL | COMMODITY_AG | COMMODITY_LIVESTOCK | EQUITY_INDEX | RATES
    unit: str
    spot0: float
    vol: float                 # annual
    storage: float             # annual storage cost as fraction of price
    conv_base: float           # base convenience yield
    inv_sens: float            # convenience-yield sensitivity to inventory tightness
    demand_beta: float         # sensitivity of demand to the macro cycle
    mkt_beta: float            # loading on the equity market factor
    season_amp: float          # seasonal amplitude of the curve (fraction)
    season_peak_month: int     # month where seasonal premium peaks
    months: str                # listed contract month codes
    n_listed: int
    multiplier: float
    tick: float
    margin_pct: float
    adv: int                   # front-month ADV in contracts
    expiry_rule: str           # PRIOR_MONTH_END | THIRD_FRIDAY | PRIOR_MONTH_25 | THIRD_WEDNESDAY | LAST_FRIDAY
    event_p: float             # daily probability of a supply/demand event
    report: Optional[str]      # WEEKLY_WED | MONTHLY_10 | None
    report_name: str
    prec: int                  # price decimals


SPECS: List[CommoditySpec] = [
    # energy
    CommoditySpec("CL", "WTI Crude Oil", "COMMODITY_ENERGY", "bbl", 74.0, 0.34, 0.04, 0.05, 0.10, 1.0, 0.35, 0.0, 6, "FGHJKMNQUVXZ", 12, 1000, 0.01, 0.08, 350_000, "PRIOR_MONTH_25", 0.035, "WEEKLY_WED", "EIA Weekly Petroleum Status Report", 2),
    CommoditySpec("BRN", "Brent Crude Oil", "COMMODITY_ENERGY", "bbl", 78.0, 0.32, 0.04, 0.05, 0.10, 1.0, 0.35, 0.0, 6, "FGHJKMNQUVXZ", 12, 1000, 0.01, 0.08, 250_000, "PRIOR_MONTH_END", 0.03, None, "", 2),
    CommoditySpec("NG", "Henry Hub Natural Gas", "COMMODITY_ENERGY", "MMBtu", 3.10, 0.55, 0.12, 0.06, 0.20, 0.5, 0.10, 0.18, 1, "FGHJKMNQUVXZ", 12, 10_000, 0.001, 0.12, 150_000, "PRIOR_MONTH_END", 0.04, "WEEKLY_WED", "EIA Weekly Natural Gas Storage Report", 3),
    CommoditySpec("RB", "RBOB Gasoline", "COMMODITY_ENERGY", "gal", 2.25, 0.36, 0.05, 0.05, 0.10, 0.9, 0.30, 0.08, 7, "FGHJKMNQUVXZ", 12, 42_000, 0.0001, 0.09, 60_000, "PRIOR_MONTH_END", 0.03, None, "", 4),
    CommoditySpec("HO", "ULSD Heating Oil", "COMMODITY_ENERGY", "gal", 2.45, 0.34, 0.05, 0.05, 0.10, 0.9, 0.30, 0.06, 1, "FGHJKMNQUVXZ", 12, 42_000, 0.0001, 0.09, 55_000, "PRIOR_MONTH_END", 0.03, None, "", 4),
    # metals
    CommoditySpec("GC", "Gold", "COMMODITY_METAL", "oz", 2350.0, 0.15, 0.005, 0.0, 0.0, -0.3, -0.10, 0.0, 1, "GJMQVZ", 6, 100, 0.10, 0.05, 200_000, "PRIOR_MONTH_END", 0.01, None, "", 2),
    CommoditySpec("SI", "Silver", "COMMODITY_METAL", "oz", 28.5, 0.28, 0.006, 0.0, 0.02, 0.3, 0.25, 0.0, 1, "HKNUZ", 5, 5000, 0.005, 0.08, 60_000, "PRIOR_MONTH_END", 0.012, None, "", 3),
    CommoditySpec("HG", "Copper", "COMMODITY_METAL", "lb", 4.35, 0.24, 0.01, 0.02, 0.10, 1.4, 0.40, 0.0, 1, "HKNUZ", 5, 25_000, 0.0005, 0.08, 90_000, "PRIOR_MONTH_END", 0.02, "MONTHLY_10", "LME/COMEX Warehouse Stocks Report", 4),
    CommoditySpec("PL", "Platinum", "COMMODITY_METAL", "oz", 980.0, 0.26, 0.006, 0.0, 0.03, 0.8, 0.30, 0.0, 1, "FJNV", 4, 50, 0.10, 0.08, 25_000, "PRIOR_MONTH_END", 0.015, None, "", 2),
    CommoditySpec("PA", "Palladium", "COMMODITY_METAL", "oz", 1050.0, 0.38, 0.006, 0.0, 0.04, 1.0, 0.30, 0.0, 1, "HMUZ", 4, 100, 0.10, 0.12, 8_000, "PRIOR_MONTH_END", 0.02, None, "", 2),
    CommoditySpec("ALI", "Aluminum", "COMMODITY_METAL", "MT", 2450.0, 0.22, 0.015, 0.02, 0.08, 1.2, 0.35, 0.0, 1, "FGHJKMNQUVXZ", 6, 25, 0.25, 0.08, 15_000, "PRIOR_MONTH_END", 0.015, "MONTHLY_10", "LME Aluminum Stocks Report", 2),
    # agriculture
    CommoditySpec("ZC", "Corn", "COMMODITY_AG", "bu", 4.55, 0.24, 0.06, 0.03, 0.10, 0.2, 0.10, 0.10, 6, "HKNUZ", 5, 5000, 0.0025, 0.07, 300_000, "PRIOR_MONTH_END", 0.025, "MONTHLY_10", "USDA WASDE Report", 4),
    CommoditySpec("ZW", "Wheat", "COMMODITY_AG", "bu", 5.85, 0.30, 0.06, 0.03, 0.10, 0.2, 0.10, 0.08, 5, "HKNUZ", 5, 5000, 0.0025, 0.08, 120_000, "PRIOR_MONTH_END", 0.03, "MONTHLY_10", "USDA WASDE Report", 4),
    CommoditySpec("ZS", "Soybeans", "COMMODITY_AG", "bu", 11.20, 0.22, 0.05, 0.03, 0.10, 0.4, 0.15, 0.08, 6, "FHKNQUX", 7, 5000, 0.0025, 0.07, 200_000, "PRIOR_MONTH_END", 0.025, "MONTHLY_10", "USDA WASDE Report", 4),
    CommoditySpec("KC", "Coffee", "COMMODITY_AG", "lb", 2.35, 0.36, 0.05, 0.02, 0.10, 0.3, 0.10, 0.0, 1, "HKNUZ", 5, 37_500, 0.0005, 0.10, 30_000, "PRIOR_MONTH_END", 0.03, None, "", 4),
    CommoditySpec("SB", "Sugar", "COMMODITY_AG", "lb", 0.205, 0.32, 0.05, 0.02, 0.10, 0.3, 0.10, 0.0, 1, "HKNV", 4, 112_000, 0.0001, 0.10, 100_000, "PRIOR_MONTH_END", 0.025, None, "", 4),
    CommoditySpec("CT", "Cotton", "COMMODITY_AG", "lb", 0.78, 0.28, 0.05, 0.02, 0.10, 0.5, 0.20, 0.06, 4, "HKNVZ", 5, 50_000, 0.0001, 0.08, 25_000, "PRIOR_MONTH_END", 0.025, None, "", 4),
    CommoditySpec("CC", "Cocoa", "COMMODITY_AG", "MT", 6800.0, 0.45, 0.05, 0.03, 0.12, 0.2, 0.10, 0.0, 1, "HKNUZ", 5, 10, 1.0, 0.12, 20_000, "PRIOR_MONTH_END", 0.035, None, "", 0),
    # livestock
    CommoditySpec("LE", "Live Cattle", "COMMODITY_LIVESTOCK", "lb", 1.85, 0.16, 0.08, 0.04, 0.08, 0.4, 0.10, 0.05, 4, "GJMQVZ", 6, 40_000, 0.00025, 0.06, 60_000, "PRIOR_MONTH_END", 0.02, None, "", 4),
    CommoditySpec("GF", "Feeder Cattle", "COMMODITY_LIVESTOCK", "lb", 2.55, 0.18, 0.08, 0.04, 0.08, 0.4, 0.10, 0.05, 4, "FHJKQUVX", 6, 50_000, 0.00025, 0.07, 15_000, "PRIOR_MONTH_END", 0.02, None, "", 4),
    CommoditySpec("HE", "Lean Hogs", "COMMODITY_LIVESTOCK", "lb", 0.92, 0.28, 0.10, 0.05, 0.10, 0.4, 0.10, 0.10, 6, "GJKMNQVZ", 6, 40_000, 0.00025, 0.08, 40_000, "PRIOR_MONTH_END", 0.025, None, "", 4),
    # financial
    # financial futures: priced off a source (an ETF, a Treasury, a coin, an index) with cost of carry; no supply/demand model
    CommoditySpec("ES", "E-mini S&P 500 Future", "EQUITY_INDEX", "index pt", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 1, "HMUZ", 4, 50, 0.25, 0.06, 1_500_000, "THIRD_FRIDAY", 0.0, None, "", 2),
    CommoditySpec("NQ", "E-mini Nasdaq-100 Future", "EQUITY_INDEX", "index pt", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.2, 0.0, 1, "HMUZ", 4, 20, 0.25, 0.07, 600_000, "THIRD_FRIDAY", 0.0, None, "", 2),
    CommoditySpec("RTY", "E-mini Russell 2000 Future", "EQUITY_INDEX", "index pt", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.1, 0.0, 1, "HMUZ", 4, 50, 0.10, 0.07, 200_000, "THIRD_FRIDAY", 0.0, None, "", 2),
    CommoditySpec("YM", "E-mini Dow Future", "EQUITY_INDEX", "index pt", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.9, 0.0, 1, "HMUZ", 4, 5, 1.0, 0.06, 150_000, "THIRD_FRIDAY", 0.0, None, "", 0),
    CommoditySpec("ZT", "2-Year Treasury Note Future", "RATES", "% of par", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1, "HMUZ", 3, 2000, 0.0078125, 0.012, 600_000, "PRIOR_MONTH_END", 0.0, None, "", 4),
    CommoditySpec("ZF", "5-Year Treasury Note Future", "RATES", "% of par", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1, "HMUZ", 3, 1000, 0.0078125, 0.015, 1_200_000, "PRIOR_MONTH_END", 0.0, None, "", 4),
    CommoditySpec("ZN", "10-Year Treasury Note Future", "RATES", "% of par", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1, "HMUZ", 3, 1000, 0.015625, 0.02, 1_800_000, "PRIOR_MONTH_END", 0.0, None, "", 4),
    CommoditySpec("ZB", "30-Year Treasury Bond Future", "RATES", "% of par", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1, "HMUZ", 3, 1000, 0.03125, 0.04, 400_000, "PRIOR_MONTH_END", 0.0, None, "", 4),
    CommoditySpec("SR3", "3-Month SOFR Future", "RATES", "100 − rate", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1, "HMUZ", 8, 2500, 0.0025, 0.003, 2_500_000, "THIRD_WEDNESDAY", 0.0, None, "", 4),
    CommoditySpec("BTC", "Bitcoin Future (CME)", "CRYPTO", "BTC", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.5, 0.0, 1, "FGHJKMNQUVXZ", 6, 5, 5.0, 0.35, 12_000, "LAST_FRIDAY", 0.0, None, "", 0),
    CommoditySpec("ETH", "Ether Future (CME)", "CRYPTO", "ETH", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.6, 0.0, 1, "FGHJKMNQUVXZ", 6, 50, 0.25, 0.40, 8_000, "LAST_FRIDAY", 0.0, None, "", 2),
    CommoditySpec("DX", "U.S. Dollar Index Future", "FX_INDEX", "index pt", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, -0.2, 0.0, 1, "HMUZ", 4, 1000, 0.005, 0.03, 30_000, "THIRD_WEDNESDAY", 0.0, None, "", 3),
    CommoditySpec("VX", "Cboe VIX Future", "VOLATILITY", "vol pt", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, -3.0, 0.0, 1, "FGHJKMNQUVXZ", 8, 1000, 0.05, 0.25, 250_000, "THIRD_WEDNESDAY", 0.0, None, "", 2),
]
SPEC_BY_CODE = {s.code: s for s in SPECS}


def apply_snapshot(spots: Dict[str, float]) -> None:
    """Re-anchor each commodity's marginal-cost level to the snapshot spot so the long-run pull matches today's market."""
    import dataclasses
    new = []
    for s in SPECS:
        v = spots.get(s.code)
        new.append(dataclasses.replace(s, spot0=float(v)) if v else s)
    SPECS[:] = new
    SPEC_BY_CODE.clear()
    SPEC_BY_CODE.update({s.code: s for s in SPECS})

EVENT_TEMPLATES = {
    "COMMODITY_ENERGY": {
        "CL": [(-1, "OPEC+ agrees to extend production cuts by {n} kb/d"), (-1, "Pipeline outage halts {n} kb/d of Permian crude flows"), (-1, "Hurricane forces evacuation of Gulf of Mexico platforms"),
               (1, "OPEC+ signals it will unwind voluntary cuts faster than expected"), (1, "US shale output hits record as rig counts rise"), (1, "Strategic reserve release of {n} million barrels announced")],
        "BRN": [(-1, "Tanker traffic disrupted in key shipping lane; insurers raise premiums"), (-1, "North Sea field shut for unplanned maintenance"), (1, "Ceasefire talks ease supply-risk premium"), (1, "Non-OPEC producers guide output higher")],
        "NG": [(-1, "Cold snap forecast lifts heating demand across the Northeast"), (-1, "LNG export terminal returns to service, tightening domestic balance"), (1, "Mild weather outlook cuts heating-demand expectations"), (1, "Producers ramp associated-gas output; storage builds above normal")],
        "RB": [(-1, "Refinery fire knocks out {n} kb/d of gasoline capacity"), (1, "Refiners return from maintenance, product supply rises")],
        "HO": [(-1, "Diesel inventories at multi-year lows ahead of harvest season"), (1, "Distillate exports fall as overseas demand softens")],
    },
    "COMMODITY_METAL": {
        "GC": [(-1, "Central banks add to gold reserves for a {n}th straight month"), (1, "ETF outflows continue as real yields rise")],
        "SI": [(-1, "Solar manufacturers' silver demand forecast raised"), (1, "Mine restart adds supply in Mexico")],
        "HG": [(-1, "Strike at major Chilean copper mine cuts output"), (-1, "Grid-buildout demand estimates revised up"), (1, "Chinese smelter output rises; exchange inventories build"), (1, "Manufacturing PMI disappoints; metals demand seen slowing")],
        "PL": [(-1, "Power outages curtail South African PGM production"), (1, "Auto-catalyst substitution reduces platinum loadings")],
        "PA": [(-1, "Sanctions threat on a key palladium exporter"), (1, "Hybrid vehicle mix shift trims palladium demand")],
        "ALI": [(-1, "Smelter curtailments in Europe on high power prices"), (1, "New smelting capacity comes online in Asia")],
    },
    "COMMODITY_AG": {
        "ZC": [(-1, "Drought expands across the Corn Belt during pollination"), (-1, "Export sales surge on strong overseas buying"), (1, "Beneficial rains improve crop condition ratings"), (1, "Record planted acreage reported")],
        "ZW": [(-1, "Black Sea export corridor disruption"), (-1, "Heat stress hits winter wheat yields"), (1, "Bumper harvest in exporting regions")],
        "ZS": [(-1, "South American harvest delayed by flooding"), (1, "Trade-flow shift reduces export demand")],
        "KC": [(-1, "Frost damages arabica crop in Brazil"), (1, "Favorable weather boosts next season's harvest outlook")],
        "SB": [(-1, "Dry weather cuts cane crush estimates"), (1, "Mills favor sugar over ethanol; supply rises")],
        "CT": [(-1, "Storm damages cotton fields in the Delta"), (1, "Textile demand weakens; mill buying slows")],
        "CC": [(-1, "Disease outbreak damages West African cocoa pods"), (1, "Mid-crop arrivals run ahead of last year")],
    },
    "COMMODITY_LIVESTOCK": {
        "LE": [(-1, "Cattle herd shrinks to multi-decade low"), (1, "Packer margins collapse; slaughter slows")],
        "GF": [(-1, "Pasture conditions deteriorate; placements fall"), (1, "Feed costs ease, encouraging heavier placements")],
        "HE": [(-1, "Disease scare tightens hog supplies"), (1, "Export demand softens after tariff announcement")],
    },
}


@dataclass
class CommodityState:
    spot: float
    demand_z: float = 0.0
    supply_shock: float = 0.0
    inventory_z: float = 0.0
    conv_yield: float = 0.0


def contract_id(code: str, year: int, month: int) -> str:
    return f"{code}{MONTH_CODES[month - 1]}{year % 100:02d}"


def _third_friday(year: int, month: int) -> date:
    d = date(year, month, 15)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d


def _third_wednesday(year: int, month: int) -> date:
    d = date(year, month, 15)
    while d.weekday() != 2:
        d += timedelta(days=1)
    return d


def _last_friday(year: int, month: int) -> date:
    d = date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)
    while d.weekday() != 4:
        d -= timedelta(days=1)
    return d


FINANCIAL_GROUPS = ("EQUITY_INDEX", "RATES", "CRYPTO", "FX_INDEX", "VOLATILITY")
# financial futures ride a source: (security id whose close is the spot, how the curve is built)
#   carry: F = S·e^{(r−q)T}; vol: mean-reverting term structure; rate: 100 − forward rate
FINANCIAL_SOURCES: Dict[str, Tuple[Optional[str], str]] = {
    "ES": ("SPY", "carry"), "NQ": ("QQQ", "carry"), "RTY": ("IWM", "carry"), "YM": ("DIA", "carry"),
    "ZT": ("UST-2Y", "carry"), "ZF": ("UST-5Y", "carry"), "ZN": ("UST-10Y", "carry"), "ZB": ("UST-30Y", "carry"),
    "SR3": (None, "rate"), "BTC": ("BTC", "carry"), "ETH": ("ETH", "carry"), "DX": ("DXY", "carry"), "VX": ("VIX", "vol"),
}
VIX_LONG_RUN, VIX_KAPPA = 19.5, 3.0


def expiry_for(spec: CommoditySpec, cal: BusinessCalendar, year: int, month: int) -> date:
    """Last trade date; positions are auto-closed on this date to avoid delivery."""
    first = date(year, month, 1)
    if spec.expiry_rule == "THIRD_FRIDAY":
        return cal.roll_back(_third_friday(year, month))
    if spec.expiry_rule == "THIRD_WEDNESDAY":
        return cal.roll_back(_third_wednesday(year, month))
    if spec.expiry_rule == "LAST_FRIDAY":
        return cal.roll_back(_last_friday(year, month))
    if spec.expiry_rule == "PRIOR_MONTH_25":
        d = date(year if month > 1 else year - 1, month - 1 if month > 1 else 12, 25)
        return cal.add_business_days(cal.roll_back(d), -3)
    # PRIOR_MONTH_END: last business day of the month before the contract month
    return cal.roll_back(first - timedelta(days=1))


class CommodityModel:
    def __init__(self, seed: int, cal: BusinessCalendar):
        self.seed = seed
        self.cal = cal
        self.state: Dict[str, CommodityState] = {s.code: CommodityState(s.spot0) for s in SPECS if s.spot0 > 0}
        self.spot_history: Dict[str, List[Tuple[str, float]]] = {s.code: [] for s in SPECS}
        self.curve_history: Dict[str, List[Tuple[str, Dict[str, float]]]] = {s.code: [] for s in SPECS}

    # ------------------------------------------------------------------ listings
    def listed_months(self, spec: CommoditySpec, d: date) -> List[Tuple[int, int]]:
        out = []
        y, m = d.year, d.month
        while len(out) < spec.n_listed:
            if MONTH_CODES[m - 1] in spec.months and expiry_for(spec, self.cal, y, m) >= d:
                out.append((y, m))
            m += 1
            if m > 12:
                m, y = 1, y + 1
        return out

    def make_contract(self, spec: CommoditySpec, y: int, m: int, seq: int) -> Security:
        cid = contract_id(spec.code, y, m)
        exp = expiry_for(spec, self.cal, y, m)
        return Security(id=cid, name=f"{spec.name} {MONTH_CODES[m-1]}{y%100:02d} ({date(y, m, 1).strftime('%b %Y')})", asset_class="FUTURE",
                        market="FUTURES", currency="USD", country="US", sector=spec.group, isin=f"XF{seq:09d}F", cusip=f"F{seq:08d}",
                        adv=spec.adv, spread_bps=0.0, liquidity_tier="LARGE", underlying=spec.code, underlying_class=spec.group,
                        contract_month=f"{y}-{m:02d}", multiplier=spec.multiplier, tick_size=spec.tick, expiry=exp.isoformat(),
                        margin_pct=spec.margin_pct, unit=spec.unit, sigma_annual=spec.vol, beta=spec.mkt_beta, lot_size=1)

    # ------------------------------------------------------------------ fundamentals
    def _seasonal(self, spec: CommoditySpec, month: int) -> float:
        if spec.season_amp == 0:
            return 0.0
        return spec.season_amp * math.cos(2 * math.pi * (month - spec.season_peak_month) / 12.0)

    def step(self, d: date, prev_bd: date, regime_growth: float, mkt_factor: float, rate_level: float, rate_change: float,
             financials: Dict[str, Dict], depth_mult: float, spread_mult: float) -> Tuple[Dict[str, Dict], List[Dict]]:
        """Advance all commodities one business day. Returns (per-commodity dict for the event payload, news items).
        `financials` carries the financial futures' sources: {code: {"S": spot level, "q": yield, "fwd": callable(T1, T2) for rate futures}}."""
        dt = 1.0 / 252.0
        weekend_days = (d - prev_bd).days
        out: Dict[str, Dict] = {}
        news: List[Dict] = []
        for spec in SPECS:
            if spec.group in FINANCIAL_GROUPS:
                continue
            st = self.state[spec.code]
            rng = random.Random(f"{self.seed}|cmd|{spec.code}|{d.isoformat()}")
            prev_inv, prev_shock = st.inventory_z, st.supply_shock
            # demand follows the cycle
            st.demand_z = 0.96 * st.demand_z + 0.04 * spec.demand_beta * regime_growth + 0.02 * rng.gauss(0, 1)
            # events (weekends make Monday's draw cover three days)
            if rng.random() < spec.event_p * weekend_days:
                sign, text = rng.choice(EVENT_TEMPLATES[spec.group][spec.code])
                mag = rng.uniform(0.3, 1.2) * (1.6 if spec.group == "COMMODITY_ENERGY" else 1.0)
                st.supply_shock += -sign * mag          # sign -1 = bullish (supply lost / demand up)
                news.append({"code": spec.code, "headline": text.format(n=rng.choice([150, 250, 400, 600, 900])), "category": "COMMODITY",
                             "body": f"{spec.name} fundamentals shift: {'supply tightens / demand firms' if sign < 0 else 'supply loosens / demand softens'}. "
                                     f"Traders reassess the balance; the effect fades over the coming weeks unless confirmed by inventory data."})
            st.supply_shock *= 0.97
            # scheduled reports move inventories with a surprise component
            if spec.report == "WEEKLY_WED" and d.weekday() == 2 or spec.report == "MONTHLY_10" and 8 <= d.day <= 12 and d.weekday() == 3:
                surprise = rng.gauss(0, 0.35)
                st.inventory_z += surprise
                direction = "draw" if surprise < 0 else "build"
                news.append({"code": spec.code, "headline": f"{spec.report_name}: {spec.name} stocks {direction} {'larger' if abs(surprise) > 0.3 else 'roughly in line'} vs expectations",
                             "category": "COMMODITY", "body": f"Reported inventory change: {'-' if surprise < 0 else '+'}{abs(surprise) * 2.8:.1f}% vs seasonal norm. "
                             f"Inventory index now {st.inventory_z:+.2f} (0 = normal; negative = tight)."})
            # inventories accumulate the balance and mean-revert slowly
            st.inventory_z = 0.995 * st.inventory_z + 0.02 * (-st.supply_shock - st.demand_z) + 0.04 * rng.gauss(0, 1)
            st.inventory_z = max(-3.0, min(3.0, st.inventory_z))
            # price responds to changes in the balance
            d_inv = st.inventory_z - prev_inv
            d_shock = st.supply_shock - prev_shock
            # supply lost (d_shock > 0) and inventory draws (d_inv < 0) are bullish
            r = (-0.06 * d_inv + 0.04 * d_shock + 0.002 * st.demand_z
                 + 0.003 * math.log(spec.spot0 / st.spot)          # slow pull toward marginal cost (half-life ~ 1 year)
                 + spec.mkt_beta * mkt_factor + spec.vol * math.sqrt(dt) * rng.gauss(0, 1))
            if spec.code == "GC":
                r += -2.5 * rate_change            # gold dislikes rising real rates
            r = max(-0.25, min(0.25, r))
            st.spot = max(0.01, st.spot * math.exp(r))
            st.conv_yield = max(-0.05, min(0.6, spec.conv_base - spec.inv_sens * st.inventory_z))
            curve = self.curve(spec, st, d, rate_level)
            self.spot_history[spec.code].append((d.isoformat(), st.spot))
            self.curve_history[spec.code].append((d.isoformat(), curve))
            out[spec.code] = {"spot": st.spot, "demand_z": st.demand_z, "supply_shock": st.supply_shock, "inventory_z": st.inventory_z,
                              "conv_yield": st.conv_yield, "curve": curve, "ret": r}
        # financial futures
        for code, (src, kind) in FINANCIAL_SOURCES.items():
            fin = financials.get(code)
            if fin is None or code not in SPEC_BY_CODE:
                continue
            spec = SPEC_BY_CODE[code]
            S, q = float(fin.get("S", 0.0)), float(fin.get("q", 0.0))
            curve = {}
            for (y, m) in self.listed_months(spec, d):
                exp = expiry_for(spec, self.cal, y, m)
                T = max(0.0, (exp - d).days / 365.25)
                if kind == "rate":                        # 100 − the 3-month rate starting at expiry
                    curve[f"{y}-{m:02d}"] = round(100.0 - 100.0 * float(fin["fwd"](T, T + 0.25)), spec.prec)
                elif kind == "vol":                       # VIX futures: the level pulled toward its long run over the contract's life
                    curve[f"{y}-{m:02d}"] = round(S + (VIX_LONG_RUN - S) * (1 - math.exp(-VIX_KAPPA * T)), spec.prec)
                else:
                    curve[f"{y}-{m:02d}"] = round(S * math.exp((rate_level - q) * T), spec.prec)
            self.spot_history[code].append((d.isoformat(), S))
            self.curve_history[code].append((d.isoformat(), curve))
            out[code] = {"spot": S, "curve": curve}
        return out, news

    def curve(self, spec: CommoditySpec, st: CommodityState, d: date, r: float) -> Dict[str, float]:
        out = {}
        for (y, m) in self.listed_months(spec, d):
            exp = expiry_for(spec, self.cal, y, m)
            T = max(0.0, (exp - d).days / 365.25)
            carry = (r + spec.storage - st.conv_yield) * T
            season = self._seasonal(spec, m) - self._seasonal(spec, d.month)
            out[f"{y}-{m:02d}"] = round(st.spot * math.exp(carry + season), spec.prec)
        return out

    def ingest(self, d: date, payload: Dict[str, Dict]) -> None:
        for code, p in payload.items():
            if code in self.state:
                st = self.state[code]
                st.spot, st.demand_z, st.supply_shock = p["spot"], p.get("demand_z", 0.0), p.get("supply_shock", 0.0)
                st.inventory_z, st.conv_yield = p.get("inventory_z", 0.0), p.get("conv_yield", 0.0)
            self.spot_history[code].append((d.isoformat(), p["spot"]))
            self.curve_history[code].append((d.isoformat(), p["curve"]))

    def bars_for_contracts(self, d: date, payload: Dict[str, Dict], securities: Dict[str, Security], prev_close: Dict[str, Decimal],
                           spot_bars: Dict[str, Tuple[float, float, float]], depth_mult: float, spread_mult: float) -> Dict[str, Bar]:
        """Contract bars derived from the commodity's spot path shape and the day's curve."""
        bars = {}
        for sec in securities.values():
            if not sec.is_future or sec.expired:
                continue
            code = sec.underlying
            spec = SPEC_BY_CODE[code]
            px = payload.get(code, {}).get("curve", {}).get(sec.contract_month)
            if px is None:
                continue
            rng = random.Random(f"{self.seed}|fbar|{sec.id}|{d.isoformat()}")
            pc = float(prev_close.get(sec.id, D(px)))
            o_ratio, h_ratio, l_ratio = spot_bars.get(code, (1.0, 1.0, 1.0))
            open_f = pc * o_ratio if pc else px
            hi = max(open_f, px) * h_ratio
            lo = min(open_f, px) * l_ratio
            months = list(payload[code]["curve"].keys())
            idx = months.index(sec.contract_month) if sec.contract_month in months else 0
            liq = 1.0 / (1.0 + 0.9 * idx)
            volume = max(1, int(spec.adv * liq * depth_mult * rng.lognormvariate(0, 0.3)))
            tick = spec.tick
            half = max(tick / 2, px * 0.0002 * spread_mult * (1 + idx * 0.5))
            bars[sec.id] = Bar(d.isoformat(), qprice(open_f), qprice(hi), qprice(lo), qprice(px), volume, qprice(px - half), qprice(px + half))
        return bars
