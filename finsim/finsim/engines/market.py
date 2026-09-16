"""Market engine.

Generates a fictional but economically coherent market:

* Equities follow a *factor model* — market factor + sector factor + idiosyncratic
  noise — so they are correlated, not independent random walks.
* A yield curve (Nelson–Siegel level/slope/curvature) and credit-spread indices
  are shocked with loadings on the same market factor, so in a recession
  regime stocks fall, yields fall, and spreads widen together.
* Market regimes (Markov chain) change drift, volatility, liquidity, and the
  sign of the stock/rates correlation.
* Prices drop by the dividend on ex-date.

Every random draw is seeded from (world_seed, date), so the engine is a pure
function of the seed: replays and pre-history regeneration are exact.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from ..calendar import BusinessCalendar
from ..domain.models import Bar, Security, YieldCurve
from ..money import D, money, price as qprice
from .commodities import SPECS as COMMODITY_SPECS, SPEC_BY_CODE, CommodityModel
from .lending_market import LendingMarket
from .fx_market import FXModel
from .counterparties import DealerModel
from .corporate_events import CorporateEventModel
from .macro import MacroModel
from .vol import VolSurfaceModel, optionable_underlyings, structural_vol, INDEX_ID as OPT_INDEX_ID, INDEX_SOURCE as OPT_INDEX_SOURCE

TENORS = [0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 20.0, 30.0]
TRADING_DAYS = 252.0


# ----------------------------------------------------------------------------
# Regimes
# ----------------------------------------------------------------------------

@dataclass(frozen=True)
class Regime:
    name: str
    label: str
    mkt_drift: float          # annual expected market return
    mkt_vol: float            # annual market factor vol
    idio_mult: float          # multiplier on idiosyncratic vol
    spread_mult: float        # multiplier on bid/ask spreads
    depth_mult: float         # multiplier on ADV (liquidity)
    rate_level_drift_bp: float   # annual drift of curve level (bp/yr)
    rate_vol_bp: float        # daily vol of level (bp)
    stock_rate_corr: float    # correlation of level shocks with market factor
    slope_drift_bp: float
    ig_spread_target: float   # bps
    hy_spread_target: float
    spread_vol: float         # daily bp vol of IG spread
    description: str
    growth: float = 0.0        # macro growth proxy used by commodity demand (-1.5 .. +0.5)


REGIMES: Dict[str, Regime] = {
    "NORMAL_GROWTH": Regime("NORMAL_GROWTH", "Normal growth", 0.08, 0.14, 1.0, 1.0, 1.0, 0.0, 4.0, 0.25, 0.0, 105, 360, 1.2,
                            "Steady expansion; moderate volatility; rates range-bound.", 0.3),
    "RATE_HIKING": Regime("RATE_HIKING", "Rate-hiking cycle", 0.02, 0.18, 1.1, 1.2, 0.9, 180.0, 6.0, -0.35, -60.0, 130, 430, 1.8,
                          "Central bank tightening; curve bear-flattens; growth stocks under pressure.", 0.1),
    "RECESSION": Regime("RECESSION", "Recession", -0.18, 0.26, 1.25, 1.8, 0.6, -220.0, 8.0, 0.45, 80.0, 190, 650, 3.5,
                        "Contraction; earnings fall; flight to quality lowers yields; spreads widen.", -1.0),
    "LIQUIDITY_STRESS": Regime("LIQUIDITY_STRESS", "Liquidity crisis", -0.45, 0.48, 2.0, 4.0, 0.3, -300.0, 14.0, 0.15, 40.0, 280, 950, 7.0,
                               "Funding stress; depth disappears; correlations go to one; margin rises.", -1.5),
    "RATE_CUTTING": Regime("RATE_CUTTING", "Rate-cutting cycle", 0.12, 0.16, 1.0, 1.0, 1.1, -150.0, 6.0, 0.30, 60.0, 115, 390, 1.5,
                           "Easing cycle; curve bull-steepens; risk assets recover.", 0.5),
}

# Monthly transition probabilities (row = from).
TRANSITIONS: Dict[str, List[Tuple[str, float]]] = {
    "NORMAL_GROWTH": [("NORMAL_GROWTH", 0.90), ("RATE_HIKING", 0.06), ("RECESSION", 0.03), ("LIQUIDITY_STRESS", 0.01)],
    "RATE_HIKING": [("RATE_HIKING", 0.80), ("NORMAL_GROWTH", 0.10), ("RECESSION", 0.10)],
    "RECESSION": [("RECESSION", 0.75), ("LIQUIDITY_STRESS", 0.10), ("RATE_CUTTING", 0.15)],
    "LIQUIDITY_STRESS": [("LIQUIDITY_STRESS", 0.55), ("RECESSION", 0.30), ("RATE_CUTTING", 0.15)],
    "RATE_CUTTING": [("RATE_CUTTING", 0.80), ("NORMAL_GROWTH", 0.20)],
}


# ----------------------------------------------------------------------------
# Universe
# ----------------------------------------------------------------------------

SECTOR_VOL = {
    "Technology": 0.010, "Financials": 0.008, "Healthcare": 0.007, "Energy": 0.012,
    "Consumer": 0.006, "Industrials": 0.007, "Utilities": 0.005, "Real Estate": 0.008,
    "Materials": 0.009, "Communications": 0.006, "Index": 0.0, "Government": 0.0,
}

# ticker, name, asset_class, sector, country, ccy, price, beta, idio vol, ADV, spread bps, tier, div yield, shares (mm)
EQUITY_SEED = [
    ("NVRA", "Novara Systems", "EQUITY", "Technology", "US", "USD", 412.50, 1.35, 0.30, 4_200_000, 2, "LARGE", 0.004, 2450),
    ("QNTM", "Quantum Lattice", "EQUITY", "Technology", "US", "USD", 88.20, 1.55, 0.42, 6_500_000, 4, "LARGE", 0.0, 1320),
    ("CLDX", "Cloudex Infrastructure", "EQUITY", "Technology", "US", "USD", 167.40, 1.25, 0.28, 2_900_000, 3, "LARGE", 0.006, 980),
    ("MRDN", "Meridian Bancorp", "EQUITY", "Financials", "US", "USD", 54.10, 1.10, 0.22, 8_100_000, 3, "LARGE", 0.032, 3100),
    ("ATLS", "Atlas Capital Group", "EQUITY", "Financials", "US", "USD", 236.80, 1.20, 0.24, 1_700_000, 4, "LARGE", 0.018, 410),
    ("HLXB", "Helix Biosciences", "EQUITY", "Healthcare", "US", "USD", 142.30, 0.75, 0.20, 3_300_000, 3, "LARGE", 0.028, 1750),
    ("VTRA", "Vitra Health", "EQUITY", "Healthcare", "US", "USD", 61.75, 0.85, 0.23, 4_800_000, 4, "LARGE", 0.022, 2200),
    ("PTRX", "Petrox Energy", "EQUITY", "Energy", "US", "USD", 98.60, 0.95, 0.27, 5_600_000, 3, "LARGE", 0.038, 1600),
    ("NGSL", "Northgate Solar", "EQUITY", "Energy", "US", "USD", 27.35, 1.60, 0.48, 7_200_000, 8, "MID", 0.0, 540),
    ("ORCH", "Orchard Retail", "EQUITY", "Consumer", "US", "USD", 176.90, 0.90, 0.19, 2_400_000, 3, "LARGE", 0.016, 1100),
    ("BRWN", "Brewton Foods", "EQUITY", "Consumer", "US", "USD", 68.40, 0.55, 0.15, 3_900_000, 3, "LARGE", 0.034, 1900),
    ("LUXA", "Luxa Brands", "EQUITY", "Consumer", "US", "USD", 312.20, 1.05, 0.26, 900_000, 6, "MID", 0.012, 190),
    ("TITN", "Titan Machinery", "EQUITY", "Industrials", "US", "USD", 219.50, 1.15, 0.24, 2_100_000, 4, "LARGE", 0.020, 480),
    ("AERO", "Aerion Dynamics", "EQUITY", "Industrials", "US", "USD", 384.10, 0.80, 0.21, 1_300_000, 4, "LARGE", 0.024, 260),
    ("GRDP", "Grid Power Holdings", "EQUITY", "Utilities", "US", "USD", 74.85, 0.45, 0.14, 2_600_000, 4, "LARGE", 0.042, 1250),
    ("URBN", "Urban Core REIT", "REIT", "Real Estate", "US", "USD", 41.20, 0.85, 0.22, 3_100_000, 6, "MID", 0.056, 670),
    ("CPRX", "Cuprex Mining", "EQUITY", "Materials", "US", "USD", 33.70, 1.30, 0.34, 6_900_000, 6, "MID", 0.026, 1450),
    ("TLNK", "Telelink Communications", "EQUITY", "Communications", "US", "USD", 22.90, 0.60, 0.17, 12_000_000, 4, "LARGE", 0.061, 4300),
    ("ZYNQ", "Zynqa Robotics", "EQUITY", "Technology", "US", "USD", 14.60, 1.70, 0.62, 380_000, 45, "SMALL", 0.0, 62),
    ("PLSR", "Pulsar Biotech", "EQUITY", "Healthcare", "US", "USD", 8.95, 1.40, 0.75, 650_000, 60, "SMALL", 0.0, 140),
    ("KOBE", "Kobayashi Motors ADR", "ADR", "Consumer", "JP", "USD", 19.40, 0.90, 0.24, 1_900_000, 8, "MID", 0.030, 3000),
    ("ATLS-P", "Atlas Capital 6.25% Pfd", "PREFERRED", "Financials", "US", "USD", 24.60, 0.35, 0.09, 220_000, 20, "SMALL", 0.0635, 40),
    ("SPXE", "Broad Market Equity ETF", "ETF", "Index", "US", "USD", 512.30, 1.00, 0.005, 9_500_000, 1, "LARGE", 0.013, 900),
    ("SHRT", "Short-Term Treasury ETF", "ETF", "Government", "US", "USD", 100.15, 0.00, 0.003, 4_000_000, 1, "LARGE", 0.043, 500),
]

# id, name, asset_class, issuer, coupon, years to maturity from start, rating, spread bps, ADV face, sector
BOND_SEED = [
    ("UST-2Y", "US Treasury 4.125% 2Y", "GOVT_BOND", "United States Treasury", 0.04125, 2.0, "AAA", 0.0, 400_000_000, "Government"),
    ("UST-5Y", "US Treasury 4.00% 5Y", "GOVT_BOND", "United States Treasury", 0.04, 5.0, "AAA", 0.0, 300_000_000, "Government"),
    ("UST-10Y", "US Treasury 4.25% 10Y", "GOVT_BOND", "United States Treasury", 0.0425, 10.0, "AAA", 0.0, 250_000_000, "Government"),
    ("UST-30Y", "US Treasury 4.50% 30Y", "GOVT_BOND", "United States Treasury", 0.045, 30.0, "AAA", 0.0, 120_000_000, "Government"),
    ("MRDN-29", "Meridian Bancorp 5.40% 2029", "CORP_BOND", "Meridian Bancorp", 0.054, 3.0, "A-", 95.0, 25_000_000, "Financials"),
    ("PTRX-32", "Petrox Energy 5.85% 2032", "CORP_BOND", "Petrox Energy", 0.0585, 6.0, "BBB", 140.0, 18_000_000, "Energy"),
    ("NGSL-30", "Northgate Solar 8.25% 2030", "CORP_BOND", "Northgate Solar", 0.0825, 4.0, "B+", 420.0, 8_000_000, "Energy"),
]

BOND_ISSUER_TICKER = {"MRDN-29": "MRDN", "PTRX-32": "PTRX", "NGSL-30": "NGSL"}


def _add_months(d: date, months: int) -> date:
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    return date(y, m, min(d.day, 28))


def _isin(seq: int) -> str:
    return f"US{seq:09d}X"


def _cusip(seq: int) -> str:
    return f"{seq:08d}X"[-9:]


def build_universe(start: date, seed: int) -> Dict[str, Security]:
    rng = random.Random(f"{seed}|universe")
    secs: Dict[str, Security] = {}
    n = 100
    for (t, name, ac, sector, country, ccy, px, beta, sig, adv, spr, tier, dy, shares) in EQUITY_SEED:
        n += 1
        dps = money(D(px) * D(dy) / 4) if dy > 0 else D("0")
        # Simple fundamentals so the security page can show real ratios.
        rev_per_share = px / rng.uniform(1.5, 6.0)
        margin = rng.uniform(0.05, 0.28)
        eps = rev_per_share * margin
        fundamentals = {
            "revenue": round(rev_per_share * shares * 1e6, 0),
            "ebitda": round(rev_per_share * shares * 1e6 * (margin + 0.10), 0),
            "net_income": round(eps * shares * 1e6, 0),
            "eps": round(eps, 2),
            "total_debt": round(rev_per_share * shares * 1e6 * rng.uniform(0.1, 0.8), 0),
            "cash": round(rev_per_share * shares * 1e6 * rng.uniform(0.05, 0.3), 0),
            "book_value_per_share": round(px / rng.uniform(1.2, 8.0), 2),
            "free_cash_flow": round(eps * shares * 1e6 * rng.uniform(0.6, 1.3), 0),
        }
        if ac in ("ETF",):
            fundamentals = {}
        secs[t] = Security(
            id=t, name=name, asset_class=ac, market="US_EQUITY", currency=ccy, country=country, sector=sector,
            isin=_isin(n), cusip=_cusip(n * 7919), shares_outstanding=int(shares * 1e6), dividend_yield=dy,
            dividend_per_share=dps, div_anchor_month=rng.randint(0, 2), div_day_bd=rng.randint(3, 15), beta=beta,
            sigma_annual=sig, adv=adv, spread_bps=spr, liquidity_tier=tier, fundamentals=fundamentals,
        )
    for (bid, name, ac, issuer, cpn, yrs, rating, spr, adv, sector) in BOND_SEED:
        n += 1
        # Seasoned issues: issued four months before the world starts (on the 15th), so accrued
        # interest exists on day one and the first coupon arrives about two months in.
        issue = _add_months(date(start.year, start.month, 15), -4)
        mat = date(issue.year + int(round(yrs)), issue.month, issue.day)
        secs[bid] = Security(
            id=bid, name=name, asset_class=ac, market="US_TREASURY" if ac == "GOVT_BOND" else "US_CORP_BOND",
            currency="USD", country="US", sector=sector, isin=_isin(n), cusip=_cusip(n * 7919),
            coupon=cpn, maturity=mat.isoformat(), issue_date=issue.isoformat(), freq=2, rating=rating, issuer=issuer,
            spread_bps_credit=spr, adv=adv, spread_bps=(1.0 if ac == "GOVT_BOND" else 12.0), liquidity_tier="LARGE",
            lot_size=1000, beta=0.0, sigma_annual=0.0,
        )
    return secs


# ----------------------------------------------------------------------------
# Curve
# ----------------------------------------------------------------------------

def nelson_siegel(level: float, slope: float, curv: float, tau: float, t: float) -> float:
    x = t / tau
    f1 = (1 - math.exp(-x)) / x if x > 1e-9 else 1.0
    f2 = f1 - math.exp(-x)
    return level + slope * f1 + curv * f2


@dataclass
class MarketState:
    """Deterministic per-day market state (recomputed, never persisted)."""
    date: str
    regime: str
    level: float
    slope: float
    curv: float
    ig_spread: float
    hy_spread: float
    mkt_factor: float
    sector_factors: Dict[str, float]


class MarketEngine:
    def __init__(self, seed: int, start: date, calendar: BusinessCalendar, securities: Dict[str, Security],
                 prehistory_days: int = 260, initial_regime: str = "NORMAL_GROWTH"):
        self.seed = seed
        self.start = start
        self.cal = calendar
        self.securities = securities
        self.prehistory_days = prehistory_days
        self.initial_regime = initial_regime
        self.history: Dict[str, List[Bar]] = {s: [] for s in securities}
        self.curves: List[YieldCurve] = []
        self.regime_history: List[Tuple[str, str]] = []   # (date, regime)
        self.state: Optional[MarketState] = None
        self._dividend_cache: Dict[Tuple[str, int], List[date]] = {}
        self.commodities = CommodityModel(seed, calendar)
        self.lending = LendingMarket(seed)
        for sec in securities.values():
            self.lending.init_security(sec)
        self.fx = FXModel(seed)
        self.vol = VolSurfaceModel(seed)
        for under in optionable_underlyings(securities):
            self.vol.init_underlying(under, structural_vol(securities, under), is_index=(under == OPT_INDEX_ID))
        self._vol_payload: Dict[str, Dict] = {}
        self.dealers = DealerModel(seed)
        self._dealer_payload: Dict[str, Dict] = {}
        self.macro = MacroModel(seed, securities, calendar)
        self._macro_payload: Dict = {}
        self.cevents = CorporateEventModel(seed, calendar)
        self._cevent_payload: List[Dict] = []
        self._forced_returns: Dict[str, float] = {}
        self._forced_rate_bp: float = 0.0
        self.bar_provider = None      # set by the world: synthetic bars for instruments priced off others (listed options)
        self.player_on_loan: Dict[str, int] = {}
        self.vol_index_history: List[Tuple[str, float]] = []
        self._contract_seq = 1000
        self.day_news: List[Dict] = []

    # ---------------- dividends (pure function of seed) ----------------
    def dividend_ex_dates(self, sec: Security, year: int) -> List[date]:
        """Quarterly ex-dates for a security in a year: business day `div_day_bd`
        of months anchor, anchor+3, anchor+6, anchor+9."""
        key = (sec.id, year)
        if key in self._dividend_cache:
            return self._dividend_cache[key]
        out: List[date] = []
        if sec.dividend_per_share > 0:
            for q in range(4):
                m = sec.div_anchor_month + 1 + 3 * q
                first = date(year, m, 1)
                d = self.cal.roll(first)
                d = self.cal.add_business_days(d, sec.div_day_bd - 1)
                out.append(d)
        self._dividend_cache[key] = out
        return out

    def dividend_schedule(self, sec: Security, ex: date) -> Dict[str, date]:
        return {
            "declared": self.cal.add_business_days(ex, -20),
            "ex": ex,
            "record": ex,                                   # T+1 settlement: record = ex
            "pay": self.cal.add_business_days(ex, 10),
        }

    def dividend_on(self, sec: Security, d: date) -> Decimal:
        if sec.dividend_per_share > 0 and d in self.dividend_ex_dates(sec, d.year):
            return sec.dividend_per_share
        return D("0")

    # ---------------- regime ----------------
    def _regime_for_month(self, prev: str, year: int, month: int) -> str:
        rng = random.Random(f"{self.seed}|regime|{year}-{month:02d}")
        u = rng.random()
        acc = 0.0
        for name, p in TRANSITIONS[prev]:
            acc += p
            if u < acc:
                return name
        return prev

    # ---------------- futures listings ----------------
    def ensure_listings(self, d: date) -> List[Security]:
        """List new contract months and flag expired ones. Deterministic, safe to call in replay."""
        added = []
        for spec in COMMODITY_SPECS:
            for (y, m) in self.commodities.listed_months(spec, d):
                from .commodities import contract_id
                cid = contract_id(spec.code, y, m)
                if cid not in self.securities:
                    self._contract_seq += 1
                    sec = self.commodities.make_contract(spec, y, m, self._contract_seq)
                    self.securities[cid] = sec
                    self.history[cid] = []
                    added.append(sec)
        for sec in self.securities.values():
            if sec.is_future and not sec.expired and sec.expiry and date.fromisoformat(sec.expiry) < d:
                sec.expired = True
        return added

    def front_contract(self, code: str) -> Optional[Security]:
        cands = [s for s in self.securities.values() if s.is_future and s.underlying == code and not s.expired]
        return min(cands, key=lambda s: s.contract_month) if cands else None

    def spot(self, code: str) -> float:
        h = self.commodities.spot_history.get(code)
        return h[-1][1] if h else 0.0

    def curve_for(self, code: str) -> Dict[str, float]:
        h = self.commodities.curve_history.get(code)
        return h[-1][1] if h else {}

    def vol_index(self) -> float:
        return self.vol_index_history[-1][1] if self.vol_index_history else 0.0

    # ---------------- generation ----------------
    def _initial_state(self) -> MarketState:
        d0 = self.cal.add_business_days(self.start, -self.prehistory_days - 1)
        return MarketState(d0.isoformat(), "NORMAL_GROWTH", 0.0435, -0.006, 0.004, 105.0, 360.0, 0.0, {})

    def bootstrap(self) -> None:
        """Generate pre-history up to (but not including) the start date."""
        st = self._initial_state()
        d = date.fromisoformat(st.date)
        prev_close: Dict[str, Decimal] = {}
        for t, sec in self.securities.items():
            if not sec.is_bond and not sec.is_future:
                prev_close[t] = D([e for e in EQUITY_SEED if e[0] == t][0][6])
        # Bonds start at the price implied by the initial curve.
        self.state = st
        self._prev_close = prev_close
        while True:
            d = self.cal.next_business_day(d)
            if d >= self.start:
                break
            self.generate_day(d)

    def generate_day(self, d: date) -> Tuple[Dict[str, Bar], YieldCurve, Optional[str]]:
        """Generate one business day's market. Returns (bars, curve, regime_change)."""
        assert self.state is not None
        prev = self.state
        rng = random.Random(f"{self.seed}|day|{d.isoformat()}")
        regime_change = None
        regime = prev.regime
        pd = date.fromisoformat(prev.date)
        if d == self.start:
            # The chosen starting regime takes effect on the start date, whatever pre-history did.
            if self.initial_regime != regime:
                regime_change = self.initial_regime
                regime = self.initial_regime
        elif (d.year, d.month) != (pd.year, pd.month):
            new = self._regime_for_month(regime, d.year, d.month)
            if new != regime:
                regime_change = new
                regime = new
        R = REGIMES[regime]
        dt = 1.0 / TRADING_DAYS
        prev_bd = date.fromisoformat(prev.date)
        # macro world: releases, meetings, earnings, credit; corporate events (only once the world is live)
        oil = self.commodities.spot_history.get("CL") or []
        oil20 = math.log(oil[-1][1] / oil[-21][1]) if len(oil) > 21 and oil[-21][1] else 0.0
        macro_out = self.macro.step(d, prev_bd, regime, oil20, prev.hy_spread, 0.0, allow_defaults=(d >= self.start), live=(d >= self.start),
                                    short_rate=nelson_siegel(prev.level, prev.slope, prev.curv, 0.25, 0.08))
        self._macro_payload = macro_out
        extra_ret: Dict[str, float] = {}
        if d >= self.start:
            prices_now = {t: float(c) for t, c in self._prev_close.items()}
            cev, cnews_corp, cshocks = self.cevents.step(d, self.securities, regime, prices_now, prev.level)
            extra_ret.update(cshocks)
        else:
            cev, cnews_corp = [], []
        self._cevent_payload = cev
        for e in macro_out["earnings"]:
            extra_ret[e["security_id"]] = extra_ret.get(e["security_id"], 0.0) + e["jump"]
        for dflt in macro_out["defaults"]:
            tick = BOND_ISSUER_TICKER.get(dflt["reference"])
            if tick:
                extra_ret[tick] = extra_ret.get(tick, 0.0) - 1.5
        for sid, fr in self._forced_returns.items():
            extra_ret[sid] = extra_ret.get(sid, 0.0) + fr
        macro_out["forced_returns"] = dict(self._forced_returns)
        macro_out["forced_rate_bp"] = self._forced_rate_bp
        macro_out["rate_shock_bp"] += self._forced_rate_bp
        self._forced_returns = {}
        self._forced_rate_bp = 0.0

        # Market factor and sector factors
        z_m = rng.gauss(0, 1)
        mkt = (R.mkt_drift - 0.5 * R.mkt_vol ** 2) * dt + R.mkt_vol * math.sqrt(dt) * z_m + macro_out["equity_shock"]
        sectors = {s: rng.gauss(0, v) * R.idio_mult for s, v in SECTOR_VOL.items()}

        # Rates: level shock correlated with the market factor
        z_r = R.stock_rate_corr * z_m + math.sqrt(max(0.0, 1 - R.stock_rate_corr ** 2)) * rng.gauss(0, 1)
        level = prev.level + (R.rate_level_drift_bp / 1e4) * dt + (R.rate_vol_bp / 1e4) * z_r
        level += macro_out["rate_shock_bp"] / 1e4
        if d >= self.start:   # the short end is pulled toward the policy rate once the central bank is live
            level += 0.05 * (self.macro.state.policy_rate - nelson_siegel(prev.level, prev.slope, prev.curv, 0.25, 0.08))
        level = min(max(level, 0.001), 0.15)
        slope = prev.slope + (R.slope_drift_bp / 1e4) * dt + (R.rate_vol_bp * 0.5 / 1e4) * rng.gauss(0, 1)
        slope = min(max(slope, -0.03), 0.03)
        curv = prev.curv + 0.0002 * rng.gauss(0, 1)
        curv = min(max(curv, -0.02), 0.02)
        # Spreads mean-revert to the regime target, widen when the market falls
        ig = prev.ig_spread + 0.05 * (R.ig_spread_target - prev.ig_spread) - 25.0 * mkt + R.spread_vol * rng.gauss(0, 1)
        ig = max(40.0, ig)
        hy = prev.hy_spread + 0.05 * (R.hy_spread_target - prev.hy_spread) - 90.0 * mkt + R.spread_vol * 3 * rng.gauss(0, 1)
        hy = max(ig + 120.0, hy)

        curve = YieldCurve(d.isoformat(), TENORS, [nelson_siegel(level, slope, curv, 2.0, t) for t in TENORS],
                           ig_spread_bps=round(ig, 2), hy_spread_bps=round(hy, 2),
                           policy_rate=round(nelson_siegel(level, slope, curv, 2.0, 0.08), 5))

        bars: Dict[str, Bar] = {}
        from .pricing import BondPricer  # local import to avoid cycle
        for t, sec in list(self.securities.items()):
            if sec.is_future or sec.is_option or sec.delisted or sec.asset_class == "PHYSICAL":
                continue
            if sec.is_bond and sec.defaulted:
                rp = qprice(D(repr(sec.recovery_rate * 100)))
                bars[t] = Bar(d.isoformat(), rp, rp, rp, rp, int(sec.adv * 0.1), rp, rp)
                self._prev_close[t] = rp
                continue
            if sec.is_bond:
                clean = BondPricer.clean_price_from_curve(sec, curve, d)
                spread_bps = sec.spread_bps * R.spread_mult
                half = clean * D(spread_bps / 2 / 1e4)
                pc = self._prev_close.get(t, clean)
                bars[t] = Bar(d.isoformat(), qprice(pc), qprice(max(pc, clean)), qprice(min(pc, clean)), qprice(clean),
                              int(sec.adv * R.depth_mult * rng.lognormvariate(0, 0.3)),
                              qprice(clean - half), qprice(clean + half))
                self._prev_close[t] = qprice(clean)
                continue
            pc = self._prev_close[t]
            idio = sec.sigma_annual * R.idio_mult * math.sqrt(dt) * rng.gauss(0, 1)
            sect = sectors.get(sec.sector, 0.0)
            r = sec.beta * mkt + sect + idio
            if sec.asset_class == "ETF" and sec.sector == "Government":
                # Short treasury ETF: tiny duration, carries at the short rate.
                r = curve.rates[1] * dt - 0.4 * (level - prev.level) + idio
            r = max(min(r, 0.35), -0.45) + extra_ret.get(t, 0.0)      # exogenous jumps (earnings, deals, defaults) are not clamped
            close_f = float(pc) * math.exp(r)
            div = self.dividend_on(sec, d)
            close_f = max(0.05, close_f - float(div))
            gap = rng.gauss(0, 0.25) * abs(r) + 0.15 * r
            open_f = float(pc) * math.exp(gap)
            rng_range = abs(r) * rng.uniform(0.3, 0.9) + sec.sigma_annual * R.idio_mult * math.sqrt(dt) * 0.4
            high_f = max(open_f, close_f) * (1 + rng_range * rng.uniform(0.2, 1.0))
            low_f = min(open_f, close_f) * (1 - rng_range * rng.uniform(0.2, 1.0))
            vol_mult = R.depth_mult * (1 + 2.5 * abs(r) / max(1e-6, sec.sigma_annual * math.sqrt(dt))) * rng.lognormvariate(0, 0.35)
            volume = int(sec.adv * vol_mult)
            spread = sec.spread_bps * R.spread_mult / 1e4
            half = close_f * spread / 2
            close = qprice(close_f)
            bars[t] = Bar(d.isoformat(), qprice(open_f), qprice(high_f), qprice(low_f), close, volume,
                          qprice(max(0.01, close_f - half)), qprice(close_f + half))
            self._prev_close[t] = close

        # commodities & financial futures
        self.ensure_listings(d)
        prev_bd = date.fromisoformat(prev.date)
        spx = bars["SPXE"]
        ust = bars["UST-10Y"]
        cpayload, cnews = self.commodities.step(d, prev_bd, R.growth, mkt, level, level - prev.level, float(spx.close),
                                                self.securities["SPXE"].dividend_yield, float(ust.close), self.securities["UST-10Y"].coupon,
                                                R.depth_mult, R.spread_mult)
        spot_shapes = {}
        for code in cpayload:
            rr = random.Random(f"{self.seed}|shape|{code}|{d.isoformat()}")
            ret = cpayload[code].get("ret", 0.0)
            gap = rr.gauss(0, 0.3) * abs(ret) + 0.2 * ret
            rng_range = abs(ret) * rr.uniform(0.3, 0.9) + 0.004
            spot_shapes[code] = (math.exp(gap), 1 + rng_range * rr.uniform(0.2, 1.0), 1 - rng_range * rr.uniform(0.2, 1.0))
        fbars = self.commodities.bars_for_contracts(d, cpayload, self.securities, self._prev_close, spot_shapes, R.depth_mult, R.spread_mult)
        # physical inventory (phase 9) is priced at the commodity's spot with a dealing spread
        for t, sec in self.securities.items():
            if sec.asset_class == "PHYSICAL" and sec.underlying in self.commodities.state:
                sp = qprice(D(repr(self.commodities.state[sec.underlying].spot)))
                pc = self._prev_close.get(t, sp)
                half = sp * D(str(sec.spread_bps / 2 / 1e4))
                fbars[t] = Bar(d.isoformat(), qprice(pc), qprice(max(pc, sp)), qprice(min(pc, sp)), sp, int(sec.adv * R.depth_mult), qprice(sp - half), qprice(sp + half))
                self._prev_close[t] = sp
        for cid, b in fbars.items():
            bars[cid] = b
            self._prev_close[cid] = b.close
        lpayload, lnews = self.lending.step(d, self.securities, regime, self.realized_vol, self.player_on_loan, max(1, (d - prev_bd).days))
        self._lending_payload = lpayload
        self._fx_payload = self.fx.step(d, mkt, curve.policy_rate, level - prev.level)
        self._dealer_payload, dnews = self.dealers.step(d, regime, hy, mkt)
        self.day_news = cnews + lnews + dnews + macro_out["news"] + cnews_corp
        self._commodity_payload = cpayload
        self.state = MarketState(d.isoformat(), regime, level, slope, curv, ig, hy, mkt, sectors)
        for t, b in bars.items():
            self.history[t].append(b)
        self.curves.append(curve)
        prev_vix = self.vol_index()
        vix = 100 * (0.5 * R.mkt_vol + 0.5 * self.realized_vol("SPXE")) * (1 + macro_out["vol_bump"])
        self.vol_index_history.append((d.isoformat(), round(vix, 2)))
        vchg = (vix / prev_vix - 1) if prev_vix else 0.0
        self._vol_payload = {}
        for under in list(self.vol.state):
            src = OPT_INDEX_SOURCE if under == OPT_INDEX_ID else under
            self._vol_payload[under] = self.vol.step(d, under, structural_vol(self.securities, under), self.realized_vol(src), vix, vchg, regime,
                                                     is_index=(under == OPT_INDEX_ID))
        if regime_change or not self.regime_history:
            self.regime_history.append((d.isoformat(), regime))
        return bars, curve, regime_change

    # ---------------- ingest (replay) ----------------
    def ingest_close(self, d: date, bars: Dict[str, Bar], curve: YieldCurve, state: Dict, commodities: Optional[Dict] = None,
                     lending: Optional[Dict] = None, fx: Optional[Dict] = None, vol: Optional[Dict] = None, dealers: Optional[Dict] = None,
                     macro: Optional[Dict] = None, cevents: Optional[List[Dict]] = None) -> None:
        """Used on replay: adopt stored bars instead of regenerating them."""
        self._forced_returns, self._forced_rate_bp = {}, 0.0   # queued shocks were consumed by the stored close
        self.ensure_listings(d)
        for t, b in bars.items():
            self.history.setdefault(t, []).append(b)
            self._prev_close[t] = b.close
        self.curves.append(curve)
        self.state = MarketState(d.isoformat(), state["regime"], state["level"], state["slope"], state["curv"],
                                 state["ig"], state["hy"], state.get("mkt", 0.0), {})
        if commodities:
            self.commodities.ingest(d, commodities)
            self._commodity_payload = commodities
        if lending:
            self.lending.ingest(d, lending)
            self._lending_payload = lending
        if fx:
            self.fx.ingest(d, fx)
            self._fx_payload = fx
        if vol:
            self.vol.ingest(d, vol)
            self._vol_payload = vol
        if dealers:
            self.dealers.ingest(d, dealers)
            self._dealer_payload = dealers
        if macro:
            self.macro.ingest(d, macro)
            self._macro_payload = macro
        if cevents is not None:
            self.cevents.ingest(cevents)
            self._cevent_payload = cevents
        self.vol_index_history.append((d.isoformat(), state.get("vol_index", 0.0)))
        if not self.regime_history or self.regime_history[-1][1] != state["regime"]:
            self.regime_history.append((d.isoformat(), state["regime"]))

    # ---------------- queries ----------------
    def last_bar(self, security_id: str) -> Bar:
        h = self.history.get(security_id)
        if not h:
            if self.bar_provider is not None and security_id in self.securities and self.securities[security_id].is_option:
                return self.bar_provider(security_id)
            raise KeyError(f"no market data for {security_id}")
        return h[-1]

    def curve(self) -> YieldCurve:
        return self.curves[-1]

    def regime(self) -> Regime:
        return REGIMES[self.state.regime] if self.state else REGIMES[self.initial_regime]

    def realized_vol(self, security_id: str, window: int = 20) -> float:
        h = self.history[security_id]
        if len(h) < window + 1:
            return self.securities[security_id].sigma_annual
        rets = [math.log(float(h[i].close) / float(h[i - 1].close)) for i in range(len(h) - window, len(h))]
        m = sum(rets) / len(rets)
        var = sum((x - m) ** 2 for x in rets) / max(1, len(rets) - 1)
        return math.sqrt(var * TRADING_DAYS)

    def state_dict(self) -> Dict:
        s = self.state
        return {"regime": s.regime, "level": s.level, "slope": s.slope, "curv": s.curv, "ig": s.ig_spread, "hy": s.hy_spread, "mkt": s.mkt_factor,
                "vol_index": self.vol_index()}

    def commodity_payload(self) -> Dict:
        return getattr(self, "_commodity_payload", {})

    def lending_payload(self) -> Dict:
        return getattr(self, "_lending_payload", {})

    def fx_payload(self) -> Dict:
        return getattr(self, "_fx_payload", {})

    def vol_payload(self) -> Dict:
        return getattr(self, "_vol_payload", {})

    def dealer_payload(self) -> Dict:
        return getattr(self, "_dealer_payload", {})

    def macro_payload(self) -> Dict:
        return getattr(self, "_macro_payload", {})

    def corporate_events_payload(self) -> List[Dict]:
        return getattr(self, "_cevent_payload", [])

    def corporate_announcements(self) -> List[Dict]:
        return list(self.corporate_events_payload())

    def force_return(self, security_id: str, log_return: float) -> None:
        """Queue an exogenous log return for the next session (forced corporate events / defaults in sandbox worlds)."""
        self._forced_returns[security_id] = self._forced_returns.get(security_id, 0.0) + log_return

    def force_rate_shock(self, bp: float) -> None:
        """Queue an exogenous parallel shift of the curve (basis points) for the next session."""
        self._forced_rate_bp += float(bp)

    def list_security(self, sec: Security, price: Decimal, day: str) -> None:
        """A new listing (spin-off): starts trading at `price` from the next session."""
        self.securities[sec.id] = sec
        self.history.setdefault(sec.id, [])
        self._prev_close[sec.id] = qprice(price)
        if not self.history[sec.id]:
            p = qprice(price)
            half = p * D(str(sec.spread_bps / 2 / 1e4))
            self.history[sec.id].append(Bar(day, p, p, p, p, 0, qprice(p - half), qprice(p + half)))
        self.lending.init_security(sec)
        if sec.asset_class in ("EQUITY", "ETF", "REIT", "ADR") and sec.liquidity_tier in ("LARGE", "MID") and sec.shares_outstanding:
            self.vol.init_underlying(sec.id, structural_vol(self.securities, sec.id))
