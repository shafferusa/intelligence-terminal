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

import json
import math
import os
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
# The universe is a snapshot of real listed companies, ETFs and representative bonds (finsim/data/universe.json, refreshed
# with tools/refresh_universe.py from Yahoo Finance prices and SEC EDGAR fundamentals). The game starts from that snapshot
# and simulates from there; nothing here is a live feed.
_UNIVERSE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "universe.json")
with open(_UNIVERSE_PATH) as _f:
    UNIVERSE = json.load(_f)
UNIVERSE_AS_OF = UNIVERSE.get("as_of", "")


# Shares that EDGAR's dei tag does not give directly (multi-class issuers report per class; funds and foreign filers
# do not file it): approximate share counts, in millions, as of the snapshot. Used only for market-cap tiers and
# per-share fundamentals.
SHARES_FALLBACK_M = {"GOOGL": 12_100.0, "META": 2_520.0, "RIVN": 1_210.0, "BRK-B": 2_160.0, "TSM": 5_190.0, "TM": 1_300.0,
                     "SPY": 900.0, "QQQ": 600.0, "TLT": 400.0, "HYG": 250.0, "SHV": 300.0,
                     # expansion: foreign filers (no XBRL company facts) and ETFs, approximate units outstanding
                     "ASML": 393.0, "NVO": 4_460.0, "BABA": 2_390.0, "SAP": 1_170.0, "SHEL": 3_000.0, "SHOP": 1_300.0, "SPOT": 200.0, "ETN": 395.0, "CB": 400.0,
                     "IWM": 300.0, "DIA": 200.0, "XLF": 1_500.0, "XLE": 300.0, "XLK": 400.0, "GLD": 300.0, "SLV": 500.0, "USO": 80.0, "VNQ": 400.0,
                     "EEM": 600.0, "EFA": 2_000.0, "LQD": 1_000.0, "IEF": 300.0, "BND": 2_000.0}


def _adjusted_beta(raw: float) -> float:
    """One-year realized beta, shrunk toward 1 (Blume): the factor model wants a forward-looking loading, and a
    defensive name's negative twelve-month beta is noise, not a sign that it falls when the market rises."""
    return round(max(0.1, 0.67 * raw + 0.33), 3)


def _tier(rec: Dict) -> str:
    cap = float(rec.get("price", 0)) * float(rec.get("shares_outstanding", 0) or 0)
    if rec["asset_class"] == "ETF":
        return "LARGE"
    if rec["asset_class"] == "PREFERRED":
        return "SMALL"
    if cap >= 30e9:
        return "LARGE"
    if cap >= 5e9:
        return "MID"
    return "SMALL"


def _spread_bps(rec: Dict, tier: str) -> float:
    if rec["asset_class"] == "ETF":
        return 1.0
    if rec["asset_class"] == "PREFERRED":
        return 20.0
    return {"LARGE": 2.0, "MID": 6.0, "SMALL": 30.0}[tier]


# (ticker, name, asset_class, sector, country, ccy, price, beta, sigma, adv, spread_bps, tier, dividend_yield, shares in millions)
EQUITY_SEED = []
for _r in UNIVERSE["equities"]:
    _shares = SHARES_FALLBACK_M.get(_r["ticker"]) or float(_r.get("shares_outstanding") or 0) / 1e6 or 300.0
    _r = dict(_r, shares_outstanding=_shares * 1e6)
    if _r["country"] != "US":            # foreign filers report in their home currency: keep prices, drop EDGAR statement lines
        _r = {k: v for k, v in _r.items() if k not in ("revenue", "net_income", "eps", "total_debt", "cash", "equity", "cfo", "capex")}
    _t = _tier(_r)
    EQUITY_SEED.append((_r["ticker"], _r["name"], _r["asset_class"], _r["sector"], _r["country"], "USD", float(_r["price"]), _adjusted_beta(float(_r["beta"])),
                        max(0.03, float(_r["sigma_annual"])), int(_r["adv"]), _spread_bps(_r, _t), _t, float(_r.get("dividend_yield", 0.0)), _shares))
REAL_FUNDAMENTALS = {r["ticker"]: r for r in UNIVERSE["equities"] if r["country"] == "US"}
from .commodities import apply_snapshot as _apply_commodity_snapshot      # noqa: E402
from .fx_market import apply_snapshot as _apply_fx_snapshot               # noqa: E402
_apply_commodity_snapshot(UNIVERSE.get("commodities", {}))
_apply_fx_snapshot(UNIVERSE.get("fx", {}))
_RATES = UNIVERSE.get("rates", {})
INITIAL_POLICY_RATE = float(_RATES.get("bill_13w", 0.0435))
INITIAL_LONG_RATE = float(_RATES.get("y30", 0.0475))
INITIAL_10Y = float(_RATES.get("y10", 0.0425))


def _coupon(y: float) -> float:
    """On-the-run coupons: the yield rounded to the nearest eighth of a percent so new Treasuries start near par."""
    return round(y * 800) / 800 if y else 0.0


# id, name, asset_class, issuer, coupon, years to maturity from start, rating, spread bps, ADV face, sector
BOND_SEED = [
    ("UST-2Y", f"US Treasury {_coupon(float(_RATES.get('y5', 0.04)) - 0.00125) * 100:.3f}% 2Y", "GOVT_BOND", "United States Treasury", _coupon(float(_RATES.get("y5", 0.04)) - 0.00125), 2.0, "AAA", 0.0, 4_000_000_000, "Government"),
    ("UST-5Y", f"US Treasury {_coupon(float(_RATES.get('y5', 0.04))) * 100:.3f}% 5Y", "GOVT_BOND", "United States Treasury", _coupon(float(_RATES.get("y5", 0.04))), 5.0, "AAA", 0.0, 3_000_000_000, "Government"),
    ("UST-10Y", f"US Treasury {_coupon(INITIAL_10Y) * 100:.3f}% 10Y", "GOVT_BOND", "United States Treasury", _coupon(INITIAL_10Y), 10.0, "AAA", 0.0, 2_500_000_000, "Government"),
    ("UST-30Y", f"US Treasury {_coupon(INITIAL_LONG_RATE) * 100:.3f}% 30Y", "GOVT_BOND", "United States Treasury", _coupon(INITIAL_LONG_RATE), 30.0, "AAA", 0.0, 1_200_000_000, "Government"),
] + [(b["id"], b["name"], "CORP_BOND", b["issuer"], float(b["coupon"]), float(b["years"]), b["rating"], float(b["spread_bps"]), int(b["adv"]), b["sector"]) for b in UNIVERSE["bonds"]]

BOND_ISSUER_TICKER = {b["id"]: b["issuer_ticker"] for b in UNIVERSE["bonds"]}


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
        real = REAL_FUNDAMENTALS.get(t, {})
        if real.get("revenue") and shares:
            so = shares * 1e6
            fundamentals = {
                "revenue": float(real["revenue"]), "net_income": float(real.get("net_income", 0.0) or 0.0),
                "eps": float(real.get("eps") if real.get("eps") is not None else (float(real.get("net_income", 0.0) or 0.0) / so)),
                "ebitda": round(float(real.get("net_income", 0.0) or 0.0) * 1.6 + float(real["revenue"]) * 0.04, 0),
                "total_debt": float(real.get("total_debt", 0.0) or 0.0), "cash": float(real.get("cash", 0.0) or 0.0),
                "book_value_per_share": round(float(real.get("equity", 0.0) or 0.0) / so, 2) if real.get("equity") else round(px / 3.0, 2),
                "free_cash_flow": float(real.get("cfo", 0.0) or 0.0) - float(real.get("capex", 0.0) or 0.0) if real.get("cfo") is not None else round(eps * so, 0),
                "fiscal_year_end": real.get("fiscal_year_end"), "as_of": UNIVERSE_AS_OF, "source": "SEC EDGAR company facts",
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
        self.real_history: Optional[Dict] = None       # a world that tracks the market: stored real closes (prehistory + sessions seen)
        self.real_feed = None                            # live RealFeed for sessions not yet in real_history
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
        # the initial curve is the real one at the snapshot date: long end from the 30-year, slope from bills to bonds
        return MarketState(d0.isoformat(), "NORMAL_GROWTH", INITIAL_LONG_RATE, min(0.03, max(-0.03, INITIAL_POLICY_RATE - INITIAL_LONG_RATE)), 0.004, 105.0, 360.0, 0.0, {})

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
        self._anchor_to_snapshot()

    def _rescale_history(self, sid: str, factor: Decimal) -> None:
        if factor == 1 or not self.history.get(sid):
            return
        self.history[sid] = [Bar(b.date, qprice(b.open * factor), qprice(b.high * factor), qprice(b.low * factor), qprice(b.close * factor), b.volume,
                                 qprice(b.bid * factor), qprice(b.ask * factor)) for b in self.history[sid]]
        if sid in self._prev_close:
            self._prev_close[sid] = qprice(self._prev_close[sid] * factor)

    def real_targets(self, d: date) -> Optional[Dict]:
        """Real closes for `d` from the stored history, else from the live feed (which the day's close then records)."""
        from .realfeed import RealFeed
        t = RealFeed.targets_from_history(self.real_history or {}, d)
        if t is None and self.real_feed is not None:
            t = self.real_feed.targets_for(d)
        return t

    @staticmethod
    def _snapshot_targets() -> Dict:
        return {"equities": {t: px for (t, _n, _ac, _s, _c, _ccy, px, *_rest) in EQUITY_SEED}, "commodities": UNIVERSE.get("commodities", {}),
                "fx": UNIVERSE.get("fx", {}), "rates": UNIVERSE.get("rates", {})}

    def _anchor_start_day(self, d: date, bars: Dict[str, Bar], curve: YieldCurve, cpayload: Dict, level: float, slope: float, curv: float,
                          ig: float, hy: float, R) -> Tuple[Dict[str, Bar], YieldCurve, float, float, float]:
        """Pin the start date's closes to the snapshot (the prehistory already ends there, so the first session is flat)."""
        return self._pin_day(d, bars, curve, cpayload, level, slope, curv, ig, hy, R, self._snapshot_targets())

    def _pin_day(self, d: date, bars: Dict[str, Bar], curve: YieldCurve, cpayload: Dict, level: float, slope: float, curv: float,
                 ig: float, hy: float, R, targets: Dict) -> Tuple[Dict[str, Bar], YieldCurve, float, float, float]:
        """Pin one session's closes to `targets` ({equities: {id: px}, commodities: {code: spot}, fx: {ccy: usd}, rates: {...}}):
        the snapshot on the start date, or a real session's closes in a world that tracks the market. Everything priced
        off those closes (bonds off the curve, futures off their sources, contracts off spot) moves with them."""
        from .pricing import BondPricer
        def scaled(b: Bar, f: Decimal) -> Bar:
            return Bar(b.date, qprice(b.open * f), qprice(b.high * f), qprice(b.low * f), qprice(b.close * f), b.volume, qprice(b.bid * f), qprice(b.ask * f))
        pin: Dict[str, Decimal] = {}
        for t, px in (targets.get("equities") or {}).items():
            b = bars.get(t)
            if b and b.close and px:
                pin[t] = D(repr(float(px))) / b.close
                bars[t] = scaled(b, pin[t])
                self._prev_close[t] = bars[t].close
        for code, target in (targets.get("commodities") or {}).items():
            st = self.commodities.state.get(code)
            if st is None or not st.spot or not target or code not in cpayload:
                continue
            f = float(target) / st.spot
            st.spot = float(target)
            cpayload[code]["spot"] = float(target)
            if isinstance(cpayload[code].get("curve"), dict):
                cpayload[code]["curve"] = {k: v * f for k, v in cpayload[code]["curve"].items()}
            if self.commodities.spot_history[code]:
                self.commodities.spot_history[code][-1] = (d.isoformat(), float(target))
            if self.commodities.curve_history[code]:
                dd, cv = self.commodities.curve_history[code][-1]
                self.commodities.curve_history[code][-1] = (dd, {k: v * f for k, v in cv.items()})
            fD = D(repr(f))
            for sid, sec in self.securities.items():
                if (sec.is_future and sec.underlying == code or sec.asset_class == "PHYSICAL" and sec.underlying == code) and sid in bars:
                    bars[sid] = scaled(bars[sid], fD)
                    self._prev_close[sid] = bars[sid].close
        for ccy, target in (targets.get("fx") or {}).items():
            if self.fx.spot.get(ccy) and target:
                self.fx.spot[ccy] = float(target)
                if ccy in self._fx_payload:
                    self._fx_payload[ccy]["spot"] = float(target)
                if self.fx.history.get(ccy):
                    dd, _s, r_ = self.fx.history[ccy][-1]
                    self.fx.history[ccy][-1] = (dd, float(target), r_)
        rates = targets.get("rates") or {}
        if rates.get("y30") and rates.get("bill_13w"):
            level, slope, curv = self._curve_params(rates, curv)
            curve = YieldCurve(d.isoformat(), TENORS, [nelson_siegel(level, slope, curv, 2.0, t) for t in TENORS], ig_spread_bps=round(ig, 2),
                               hy_spread_bps=round(hy, 2), policy_rate=round(nelson_siegel(level, slope, curv, 2.0, 0.08), 5))
            for sid, sec in self.securities.items():
                if sec.is_bond and sid in bars and not sec.defaulted:
                    clean = qprice(BondPricer.clean_price_from_curve(sec, curve, d))
                    half = clean * D(str(sec.spread_bps * R.spread_mult / 2 / 1e4))
                    pc = self.history[sid][-1].close if self.history.get(sid) else clean
                    if bars[sid].close:
                        pin[sid] = clean / bars[sid].close
                    bars[sid] = Bar(d.isoformat(), qprice(pc), qprice(max(pc, clean)), qprice(min(pc, clean)), clean, bars[sid].volume, qprice(clean - half), qprice(clean + half))
                    self._prev_close[sid] = clean
        # financial futures were stepped off the unpinned SPY close and 10-year price: move them with their source
        for code, src in (("ES", "SPY"), ("ZN", "UST-10Y")):
            fD = pin.get(src)
            if fD is None or fD == 1 or code not in cpayload:
                continue
            f = float(fD)
            cpayload[code]["spot"] = cpayload[code]["spot"] * f
            if isinstance(cpayload[code].get("curve"), dict):
                cpayload[code]["curve"] = {k: v * f for k, v in cpayload[code]["curve"].items()}
            if self.commodities.spot_history.get(code):
                dd, v = self.commodities.spot_history[code][-1]
                self.commodities.spot_history[code][-1] = (dd, v * f)
            if self.commodities.curve_history.get(code):
                dd, cv = self.commodities.curve_history[code][-1]
                self.commodities.curve_history[code][-1] = (dd, {k: v * f for k, v in cv.items()})
            for sid, sec in self.securities.items():
                if sec.is_future and sec.underlying == code and sid in bars:
                    bars[sid] = scaled(bars[sid], fD)
                    self._prev_close[sid] = bars[sid].close
        return bars, curve, level, slope, curv

    @staticmethod
    def _curve_params(rates: Dict, curv: float) -> Tuple[float, float, float]:
        """Nelson-Siegel level/slope/curvature that reproduce the 13-week bill, the 30-year and (when given) the 10-year
        yield: a 3x3 solve for the three factors, slope and curvature clamped to the model's range and the level then
        re-set so the 30-year lands exactly."""
        y30 = float(rates["y30"]); bill = float(rates["bill_13w"]); y10 = rates.get("y10")
        f1 = lambda t: nelson_siegel(0.0, 1.0, 0.0, 2.0, t)
        f2 = lambda t: nelson_siegel(0.0, 0.0, 1.0, 2.0, t)
        t_bill = 0.25
        if y10:
            a = [[1.0, f1(t_bill), f2(t_bill)], [1.0, f1(10.0), f2(10.0)], [1.0, f1(30.0), f2(30.0)]]
            b = [bill, float(y10), y30]
            det = lambda m: (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0]) + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))
            d0 = det(a)
            if abs(d0) > 1e-12:
                col = lambda j: det([[b[i] if k == j else a[i][k] for k in range(3)] for i in range(3)]) / d0
                slope = min(0.03, max(-0.03, col(1)))
                curv = min(0.03, max(-0.03, col(2)))
            else:
                slope = min(0.03, max(-0.03, bill - y30))
        else:
            slope = min(0.03, max(-0.03, bill - y30))
        level = y30 - slope * f1(30.0) - curv * f2(30.0)
        return level, slope, curv

    def apply_real_history(self, h: Dict) -> None:
        """Overlay real closes on the generated prehistory (a world that tracks the market): each session's equity, ETF,
        commodity, FX and curve values become the real ones, bonds and futures are re-priced off them, and the state
        ends at the last real session. Called once after bootstrap, from the stored REAL_HISTORY_LOADED event."""
        from .pricing import BondPricer
        eq_h = h.get("equities") or {}
        def carry(series: Dict[str, float], iso: str) -> Optional[float]:
            if iso in series:
                return series[iso]
            earlier = [k for k in series if k < iso]
            if earlier:
                return series[max(earlier)]
            later = [k for k in series if k > iso]
            return series[min(later)] if later else None
        factors: Dict[str, Dict[str, Decimal]] = {}          # sid -> {date: factor}
        for sid, series in eq_h.items():
            bars = self.history.get(sid)
            if not bars or not series:
                continue
            fmap: Dict[str, Decimal] = {}
            new = []
            for b in bars:
                v = carry(series, b.date)
                if v is None or not b.close:
                    new.append(b)
                    continue
                f = D(repr(float(v))) / b.close
                fmap[b.date] = f
                new.append(Bar(b.date, qprice(b.open * f), qprice(b.high * f), qprice(b.low * f), qprice(b.close * f), b.volume, qprice(b.bid * f), qprice(b.ask * f)))
            self.history[sid] = new
            factors[sid] = fmap
            self._prev_close[sid] = new[-1].close
        # commodities: spot history, curve history, every listed contract's bars
        for code, series in (h.get("commodities") or {}).items():
            if code not in self.commodities.spot_history or not series:
                continue
            fmap = {}
            sh = self.commodities.spot_history[code]
            new_sh = []
            for dd, v0 in sh:
                v = carry(series, dd)
                if v is None or not v0:
                    new_sh.append((dd, v0))
                    continue
                fmap[dd] = float(v) / v0
                new_sh.append((dd, float(v)))
            self.commodities.spot_history[code] = new_sh
            self.commodities.curve_history[code] = [(dd, {k: val * fmap.get(dd, 1.0) for k, val in cv.items()}) for dd, cv in self.commodities.curve_history[code]]
            st = self.commodities.state.get(code)
            if st is not None and new_sh:
                st.spot = new_sh[-1][1]
            for sid, sec in self.securities.items():
                if (sec.is_future or sec.asset_class == "PHYSICAL") and sec.underlying == code and self.history.get(sid):
                    self.history[sid] = [Bar(b.date, *(qprice(x * D(repr(fmap.get(b.date, 1.0)))) for x in (b.open, b.high, b.low, b.close)), b.volume,
                                             qprice(b.bid * D(repr(fmap.get(b.date, 1.0)))), qprice(b.ask * D(repr(fmap.get(b.date, 1.0))))) for b in self.history[sid]]
                    self._prev_close[sid] = self.history[sid][-1].close
        # FX
        for ccy, series in (h.get("fx") or {}).items():
            if ccy not in self.fx.history or not series:
                continue
            self.fx.history[ccy] = [(dd, (carry(series, dd) or s_), r_) for dd, s_, r_ in self.fx.history[ccy]]
            self.fx.spot[ccy] = self.fx.history[ccy][-1][1]
        # the curve on every prehistory date, then bonds and the rates futures off it
        rates = h.get("rates") or {}
        if rates.get("y30") and rates.get("bill_13w"):
            new_curves = []
            bond_factors: Dict[str, Dict[str, Decimal]] = {}
            for c in self.curves:
                rr = {k: carry(v, c.date) for k, v in rates.items()}
                if rr.get("y30") is None or rr.get("bill_13w") is None:
                    new_curves.append(c)
                    continue
                level, slope, curv = self._curve_params(rr, 0.0)
                nc = YieldCurve(c.date, TENORS, [nelson_siegel(level, slope, curv, 2.0, t) for t in TENORS], ig_spread_bps=c.ig_spread_bps, hy_spread_bps=c.hy_spread_bps,
                                policy_rate=round(nelson_siegel(level, slope, curv, 2.0, 0.08), 5))
                new_curves.append(nc)
                asof = date.fromisoformat(c.date)
                for sid, sec in self.securities.items():
                    if sec.is_bond:
                        bond_factors.setdefault(sid, {})[c.date] = qprice(BondPricer.clean_price_from_curve(sec, nc, asof))
            self.curves = new_curves
            for sid, closes in bond_factors.items():
                bars = self.history.get(sid)
                if not bars:
                    continue
                fmap = {}
                new = []
                for b in bars:
                    clean = closes.get(b.date)
                    if clean is None or not b.close:
                        new.append(b)
                        continue
                    f = clean / b.close
                    fmap[b.date] = f
                    new.append(Bar(b.date, qprice(b.open * f), qprice(b.high * f), qprice(b.low * f), clean, b.volume, qprice(b.bid * f), qprice(b.ask * f)))
                self.history[sid] = new
                factors[sid] = fmap
                self._prev_close[sid] = new[-1].close
            last = self.curves[-1]
            s = self.state
            level, slope, curv = self._curve_params({k: carry(v, last.date) for k, v in rates.items()}, s.curv)
            self.state = MarketState(s.date, s.regime, level, slope, curv, s.ig_spread, s.hy_spread, s.mkt_factor, s.sector_factors)
        # financial futures ride their sources, date by date
        for code, src in (("ES", "SPY"), ("ZN", "UST-10Y")):
            fmap = factors.get(src)
            if not fmap or code not in self.commodities.spot_history:
                continue
            self.commodities.spot_history[code] = [(dd, v * float(fmap.get(dd, 1))) for dd, v in self.commodities.spot_history[code]]
            self.commodities.curve_history[code] = [(dd, {k: val * float(fmap.get(dd, 1)) for k, val in cv.items()}) for dd, cv in self.commodities.curve_history[code]]
            for sid, sec in self.securities.items():
                if sec.is_future and sec.underlying == code and self.history.get(sid):
                    self.history[sid] = [Bar(b.date, *(qprice(x * fmap.get(b.date, D(1))) for x in (b.open, b.high, b.low, b.close)), b.volume,
                                             qprice(b.bid * fmap.get(b.date, D(1))), qprice(b.ask * fmap.get(b.date, D(1)))) for b in self.history[sid]]
                    self._prev_close[sid] = self.history[sid][-1].close
        self._bar_cache.clear() if hasattr(self, "_bar_cache") else None

    def _anchor_to_snapshot(self) -> None:
        """The seeded prehistory is a random path; the world must nevertheless START at the snapshot: today's prices,
        commodity spots, FX rates and yield curve. Every history is rescaled by a constant so its last close equals
        the snapshot value (returns and shapes are untouched), the curve state is set to the snapshot yields and bonds
        are rescaled to the price that curve implies. Deterministic, so replay rebuilds the same prehistory."""
        if not self.history:
            return
        # equities, ETFs, preferred, ADRs: back to the snapshot price
        factors: Dict[str, Decimal] = {}
        for (t, _n, _ac, _s, _c, _ccy, px, *_rest) in EQUITY_SEED:
            h = self.history.get(t)
            if h and h[-1].close:
                factors[t] = D(repr(px)) / h[-1].close
                self._rescale_history(t, factors[t])
        # commodities: spot, curves and every listed contract
        for code, target in UNIVERSE.get("commodities", {}).items():
            st = self.commodities.state.get(code)
            if st is None or not st.spot or not target:
                continue
            f = float(target) / st.spot
            st.spot = float(target)
            self.commodities.spot_history[code] = [(d_, v * f) for d_, v in self.commodities.spot_history[code]]
            self.commodities.curve_history[code] = [(d_, {k: v * f for k, v in cv.items()}) for d_, cv in self.commodities.curve_history[code]]
            fD = D(repr(f))
            for sid, sec in self.securities.items():
                if sec.is_future and sec.underlying == code:
                    self._rescale_history(sid, fD)
        # FX: spot in USD per unit of foreign currency
        for ccy, target in UNIVERSE.get("fx", {}).items():
            cur = self.fx.spot.get(ccy)
            if cur and target:
                f = float(target) / cur
                self.fx.spot[ccy] = float(target)
                self.fx.history[ccy] = [(d_, s_ * f, r_) for d_, s_, r_ in self.fx.history[ccy]]
        # the curve: long end and short end from the snapshot; bonds to the price that curve implies
        rates = UNIVERSE.get("rates", {})
        if rates.get("y30") and rates.get("bill_13w") and self.curves:
            from .pricing import BondPricer  # local import to avoid a cycle
            s = self.state
            level = float(rates["y30"])
            slope = min(0.03, max(-0.03, float(rates["bill_13w"]) - level))
            self.state = MarketState(s.date, s.regime, level, slope, s.curv, s.ig_spread, s.hy_spread, s.mkt_factor, s.sector_factors)
            old = self.curves[-1]
            curve = YieldCurve(old.date, TENORS, [nelson_siegel(level, slope, s.curv, 2.0, t) for t in TENORS], ig_spread_bps=old.ig_spread_bps,
                               hy_spread_bps=old.hy_spread_bps, policy_rate=round(nelson_siegel(level, slope, s.curv, 2.0, 0.08), 5))
            self.curves[-1] = curve
            asof = date.fromisoformat(old.date)
            # issuers: the prehistory's credit migration is a random path; the world starts at the snapshot's rating and spread
            seed_spread = {bid: spr for (bid, _n, _ac, _i, _c, _y, _r, spr, _a, _s) in BOND_SEED}
            for ref, ist in self.macro.issuers.items():
                sec = self.securities.get(ref)
                if sec is None or ist.defaulted:
                    continue
                ist.rating, ist.watch = ist.base_rating, "STABLE"
                ist.spread_bps = float(seed_spread.get(ref, ist.spread_bps))
                sec.rating, sec.spread_bps_credit = ist.rating, ist.spread_bps
            for sid, sec in self.securities.items():
                if sec.is_bond and self.history.get(sid) and self.history[sid][-1].close:
                    clean = BondPricer.clean_price_from_curve(sec, curve, asof)
                    factors[sid] = qprice(clean) / self.history[sid][-1].close
                    self._rescale_history(sid, factors[sid])
        # financial futures ride their source: ES on the SPY level, ZN on the 10-year note's clean price
        for code, src in (("ES", "SPY"), ("ZN", "UST-10Y")):
            fD = factors.get(src)
            if fD is None or fD == 1 or code not in self.commodities.spot_history:
                continue
            f = float(fD)
            self.commodities.spot_history[code] = [(d_, v * f) for d_, v in self.commodities.spot_history[code]]
            self.commodities.curve_history[code] = [(d_, {k: v * f for k, v in cv.items()}) for d_, cv in self.commodities.curve_history[code]]
            for sid, sec in self.securities.items():
                if sec.is_future and sec.underlying == code:
                    self._rescale_history(sid, fD)

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
        spx = bars["SPY"]
        ust = bars["UST-10Y"]
        cpayload, cnews = self.commodities.step(d, prev_bd, R.growth, mkt, level, level - prev.level, float(spx.close),
                                                self.securities["SPY"].dividend_yield, float(ust.close), self.securities["UST-10Y"].coupon,
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
        real = self.real_targets(d) if self.real_history is not None else None
        if real is not None:
            # a world that tracks the market: this session closes at the real closes
            bars, curve, level, slope, curv = self._pin_day(d, bars, curve, cpayload, level, slope, curv, ig, hy, R, real)
        elif d == self.start:
            # the first session closes exactly at the snapshot: today's prices, spots, FX and curve are the starting point
            bars, curve, level, slope, curv = self._anchor_start_day(d, bars, curve, cpayload, level, slope, curv, ig, hy, R)
        self._commodity_payload = cpayload
        self.state = MarketState(d.isoformat(), regime, level, slope, curv, ig, hy, mkt, sectors)
        for t, b in bars.items():
            self.history[t].append(b)
        self.curves.append(curve)
        prev_vix = self.vol_index()
        vix = 100 * (0.5 * R.mkt_vol + 0.5 * self.realized_vol("SPY")) * (1 + macro_out["vol_bump"])
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
