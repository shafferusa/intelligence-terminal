"""Private equity: buying private companies with leverage, running them, and selling them.

Deal flow: every month a handful of opportunities (deterministic from the seed) — founder-owned businesses, corporate
carve-outs, sponsor-to-sponsor secondaries — each described by sector, revenue, EBITDA, growth and the seller's asking
multiple, sold through a banker-run auction or a proprietary conversation. The player bids an enterprise value (as an
EV/EBITDA multiple) and chooses how much debt to put on it; the answer comes five sessions later. A won deal closes at
once: senior term loan (SOFR + spread, mandatory amortisation, excess-cash-flow sweep) and, above the senior cap, a
second-lien tranche; the fund writes the equity cheque for its share (management rolls over the rest); advisory,
financing and diligence fees are expensed — the J-curve is real.

Ownership: every quarter the company reports — revenue grows with its sector, the cycle, the equity market and the
plan the player is running (cost programme, growth plan, add-on acquisition, new CEO), margin drifts toward its
target, free cash flow after interest, tax, capex and working capital pays the term loan down or draws the revolver;
leverage and interest cover are tested against the covenants: a breach must be cured with fresh equity within ten
sessions or the lenders take the keys and the equity is written off. A quality-of-earnings adjustment hidden in the
seller's numbers surfaces at the first report unless diligence was bought before bidding. Marks are quarterly, at a
multiple that starts at entry and drifts toward the public comparables over two years — private marks lag public
markets, as they do. A dividend recapitalisation re-levers to pay the fund a distribution.

Exits: a sale process (strategic or sponsor buyers, a control premium, thirty sessions, and in a recession the
process can fail) or an IPO (a discount to the comparables, a quarter of the stake sold at the offer, the rest marked
daily as a listed stake and sold down after the lock-up). Everything is an event, so replay rebuilds the book exactly.

Accounts: 1180 investments at cost, 1185 valuation adjustment; 4370 mark-to-market and realised gains, 4380 income
(recaps, monitoring fees), 5360 deal costs and losses.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from ..domain.events import E, Event
from ..domain.models import Portfolio
from ..engines.ledger import dr, cr
from ..money import D, money, ZERO

# EV/EBITDA that the public comparables trade at in a normal market (2024-26 levels), entry-margin profile, growth, capex and
# working-capital intensity, and the kind of business behind the label
SECTORS = {
    "Software":               {"mult": 16.0, "margin": (0.22, 0.34), "growth": (0.10, 0.20), "capex": 0.03, "nwc": -0.05, "beta": 1.2, "lev_add": 1.0,
                               "names": ["vertical SaaS provider", "payments software company", "cybersecurity vendor", "compliance software vendor", "ERP for mid-market manufacturers"]},
    "Health Care":            {"mult": 13.0, "margin": (0.15, 0.24), "growth": (0.06, 0.12), "capex": 0.04, "nwc": 0.08, "beta": 0.8, "lev_add": 0.5,
                               "names": ["dental services platform", "physician practice group", "medical device distributor", "behavioural health provider", "specialty pharmacy"]},
    "Business Services":      {"mult": 11.0, "margin": (0.14, 0.22), "growth": (0.05, 0.10), "capex": 0.03, "nwc": 0.10, "beta": 1.0, "lev_add": 0.5,
                               "names": ["insurance brokerage roll-up", "testing & inspection company", "facilities services group", "staffing platform", "wealth management aggregator"]},
    "Industrials":            {"mult": 10.0, "margin": (0.12, 0.19), "growth": (0.03, 0.07), "capex": 0.05, "nwc": 0.15, "beta": 1.1, "lev_add": 0.0,
                               "names": ["aerospace components maker", "industrial distributor", "building products manufacturer", "packaging manufacturer", "specialty chemicals maker"]},
    "Consumer":               {"mult": 9.5, "margin": (0.10, 0.17), "growth": (0.03, 0.08), "capex": 0.04, "nwc": 0.08, "beta": 1.1, "lev_add": 0.0,
                               "names": ["restaurant franchisor", "pet food producer", "consumer brands roll-up", "fitness chain", "auto aftermarket parts supplier"]},
    "Communication & Media":  {"mult": 8.5, "margin": (0.20, 0.32), "growth": (0.02, 0.06), "capex": 0.10, "nwc": 0.03, "beta": 0.9, "lev_add": 0.5,
                               "names": ["fibre broadband operator", "outdoor advertising company", "data-centre operator", "tower portfolio"]},
    "Energy & Infrastructure": {"mult": 7.0, "margin": (0.25, 0.40), "growth": (0.01, 0.05), "capex": 0.12, "nwc": 0.05, "beta": 0.9, "lev_add": 0.0,
                               "names": ["midstream services company", "waste services company", "renewable developer", "logistics terminal operator"]},
}
SOURCES = {   # how the deal comes: competition, the ask relative to fair, how much the seller will move
    "AUCTION":     {"label": "banker-run auction", "ask_prem": 0.08, "compete": 1.0, "give": 0.02},
    "PROPRIETARY": {"label": "proprietary (founder-owned)", "ask_prem": -0.04, "compete": 0.3, "give": 0.06},
    "CARVE_OUT":   {"label": "corporate carve-out", "ask_prem": -0.08, "compete": 0.6, "give": 0.05},
    "SECONDARY":   {"label": "sponsor-to-sponsor secondary", "ask_prem": 0.05, "compete": 0.9, "give": 0.02},
}
REGIME_LEVERAGE = {"NORMAL_GROWTH": 5.5, "RATE_CUTTING": 6.0, "RATE_HIKING": 5.0, "RECESSION": 4.0, "LIQUIDITY_STRESS": 3.0}
REGIME_MULT = {"NORMAL_GROWTH": 1.0, "RATE_CUTTING": 1.05, "RATE_HIKING": 0.95, "RECESSION": 0.82, "LIQUIDITY_STRESS": 0.72}
REGIME_GROWTH = {"NORMAL_GROWTH": 0.0, "RATE_CUTTING": 0.01, "RATE_HIKING": -0.01, "RECESSION": -0.06, "LIQUIDITY_STRESS": -0.09}
SENIOR_CAP = 4.5                    # turns of EBITDA the term loan market funds; the rest is second lien
TLB_SPREAD_BPS = (325.0, 475.0)     # by leverage, in a normal market
SECOND_LIEN_SPREAD_BPS = 850.0
FEES = {"advisory": 0.0125, "financing": 0.025, "legal": 0.004, "diligence": 0.0025, "sale": 0.015, "ipo": 0.06, "recap": 0.02, "monitoring": 0.0}
MIN_EQUITY = 5_000_000
BID_DAYS = 5                        # business days from bid to answer
SALE_DAYS = 30
IPO_DAYS = 45
CURE_DAYS = 10
LOCKUP_DAYS = 180
IPO_MIN_EBITDA = 60_000_000
INITIATIVES = {
    "COST_PROGRAM": {"label": "Cost programme", "cost_pct_ev": 0.010, "quarters": 4, "margin": 0.025, "growth": -0.01, "risk": 0.25,
                     "note": "procurement, footprint and overhead: +250bp of margin over four quarters; one in four slips and delivers half"},
    "GROWTH_PLAN":  {"label": "Growth plan", "cost_pct_ev": 0.015, "quarters": 6, "margin": -0.01, "growth": 0.05, "risk": 0.30,
                     "note": "sales build-out and new sites: +5 points of growth for six quarters at a point of margin; three in ten stall"},
    "NEW_CEO":      {"label": "New CEO", "cost_pct_ev": 0.004, "quarters": 4, "margin": 0.015, "growth": 0.03, "risk": 0.40,
                     "note": "a proven operator: growth and margin both improve, but four in ten transitions disrupt the first two quarters"},
    "ADD_ON":       {"label": "Add-on acquisition", "cost_pct_ev": 0.0, "quarters": 2, "margin": 0.005, "growth": 0.0, "risk": 0.20,
                     "note": "buy a smaller competitor at a lower multiple, funded with incremental debt and equity; synergies over two quarters"},
}


def irr(flows: List[Tuple[date, float]]) -> Optional[float]:
    """Annual IRR of dated cash flows (negative = paid out), by bisection; None when it has no sign change."""
    if not flows or not any(a < 0 for _, a in flows) or not any(a > 0 for _, a in flows):
        return None
    t0 = min(d for d, _ in flows)
    def npv(r: float) -> float:
        return sum(a / (1 + r) ** ((d - t0).days / 365.0) for d, a in flows)
    lo, hi = -0.95, 10.0
    if npv(lo) * npv(hi) > 0:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return round((lo + hi) / 2, 4)


@dataclass
class PortfolioCompany:
    id: str
    portfolio_id: str
    deal_id: str
    name: str
    sector: str
    source: str
    entry_date: str
    entry_ev: float
    entry_multiple: float
    entry_ebitda: float
    revenue: float                      # LTM
    ebitda: float                       # LTM
    margin: float
    target_margin: float
    growth: float                       # underlying annual revenue growth
    capex_pct: float
    nwc_pct: float
    tax_rate: float
    beta: float
    tlb: float                          # senior term loan outstanding
    tlb_spread_bps: float
    second_lien: float
    second_lien_spread_bps: float
    revolver: float = 0.0
    cash: float = 0.0
    ownership: float = 0.9              # the fund's share of the equity (management rolls the rest)
    equity_invested: Decimal = ZERO     # the fund's cost
    fees_paid: Decimal = ZERO
    qoe_adj: float = 0.0                # hidden quality-of-earnings adjustment, applied at the first report
    qoe_revealed: bool = False
    qoe_applied: bool = False
    mark_multiple: float = 0.0
    mark_value: Decimal = ZERO          # the fund's share of the equity value, carried
    distributions: Decimal = ZERO
    realized: Decimal = ZERO
    status: str = "ACTIVE"              # ACTIVE | BREACH | EXIT_PROCESS | IPO_PROCESS | LISTED | SOLD | IPO_COMPLETE | RESTRUCTURED | FAILED
    cov_max_leverage: float = 7.0
    cov_min_icr: float = 1.5
    next_report: str = ""
    quarters_held: int = 0
    initiatives: List[Dict] = field(default_factory=list)
    reports: List[Dict] = field(default_factory=list)
    exit: Optional[Dict] = None
    listed: Optional[Dict] = None       # {shares, price, held, lockup_until, tranches}
    breach: Optional[Dict] = None       # {cure_by, cure_amount, reason}
    flows: List[Dict] = field(default_factory=list)      # dated cash flows for the IRR
    history: List[Dict] = field(default_factory=list)

    @property
    def debt(self) -> float:
        return self.tlb + self.second_lien + self.revolver

    @property
    def net_debt(self) -> float:
        return self.debt - self.cash

    @property
    def leverage(self) -> float:
        return self.net_debt / self.ebitda if self.ebitda > 0 else 99.0


class PrivateEquityEngine:
    def __init__(self, world):
        self.w = world
        self.bids: Dict[str, Dict[str, Dict]] = {}          # deal id -> portfolio id -> bid (pending)
        self.diligence: Dict[str, set] = {}                 # deal id -> portfolio ids that bought diligence
        self.taken: set = set()                             # deal ids already bought (by anyone)

    def register(self) -> None:
        w = self.w
        w.on(E.PE_DILIGENCE, lambda world, ev: self._h_diligence(ev))
        w.on(E.PE_BID, lambda world, ev: self._h_bid(ev))
        w.on(E.PE_DEAL_RESOLVED, lambda world, ev: self._h_resolved(ev))
        w.on(E.PE_REPORT, lambda world, ev: self._h_report(ev))
        w.on(E.PE_MARKED, lambda world, ev: self._h_marked(ev))
        w.on(E.PE_INITIATIVE, lambda world, ev: self._h_initiative(ev))
        w.on(E.PE_RECAP, lambda world, ev: self._h_recap(ev))
        w.on(E.PE_COVENANT, lambda world, ev: self._h_covenant(ev))
        w.on(E.PE_CURED, lambda world, ev: self._h_cured(ev))
        w.on(E.PE_RESTRUCTURED, lambda world, ev: self._h_restructured(ev))
        w.on(E.PE_EXIT_STARTED, lambda world, ev: self._h_exit_started(ev))
        w.on(E.PE_EXITED, lambda world, ev: self._h_exited(ev))
        w.on(E.PE_SELLDOWN, lambda world, ev: self._h_selldown(ev))

    # ------------------------------------------------------------------ market inputs
    def policy_rate(self) -> float:
        return float(self.w.market.curves[-1].policy_rate) if self.w.market.curves else 0.04

    def regime(self) -> str:
        return self.w.market.state.regime if self.w.market.state else "NORMAL_GROWTH"

    def hy_scale(self) -> float:
        curves = self.w.market.curves
        return float(curves[-1].hy_spread_bps) / 360.0 if curves else 1.0

    def market_scale(self) -> float:
        """Where the equity market stands against the save's first close (SPY): public multiples move with it."""
        h = self.w.market.history.get("SPY") or []
        if len(h) < 2:
            return 1.0
        first = next((b for b in h if b.date >= self.w.start_date.isoformat()), h[0])
        return max(0.5, min(1.6, float(h[-1].close) / float(first.close))) if first.close else 1.0

    def public_multiple(self, sector: str) -> float:
        """EV/EBITDA the listed comparables trade at now."""
        base = SECTORS[sector]["mult"]
        return round(base * (0.6 + 0.4 * self.market_scale()) * REGIME_MULT.get(self.regime(), 1.0), 2)

    def max_leverage(self, sector: str) -> float:
        return round(REGIME_LEVERAGE.get(self.regime(), 5.0) + SECTORS[sector]["lev_add"], 1)

    def tlb_spread(self, leverage: float) -> float:
        lo, hi = TLB_SPREAD_BPS
        base = lo + (hi - lo) * max(0.0, min(1.0, (leverage - 3.0) / 3.0))
        return round(base * max(0.8, min(2.0, self.hy_scale())) / 25) * 25

    def market(self) -> Dict:
        return {"policy_rate": self.policy_rate(), "regime": self.regime(), "market_scale": round(self.market_scale(), 3), "hy_scale": round(self.hy_scale(), 3),
                "public_multiples": {s: self.public_multiple(s) for s in SECTORS}, "max_leverage": {s: self.max_leverage(s) for s in SECTORS},
                "tlb_spread_bps": {"at_4x": self.tlb_spread(4.0), "at_6x": self.tlb_spread(6.0)}, "second_lien_spread_bps": SECOND_LIEN_SPREAD_BPS,
                "senior_cap": SENIOR_CAP, "fees": FEES, "ipo_open": self.regime() in ("NORMAL_GROWTH", "RATE_CUTTING", "RATE_HIKING"), "ipo_min_ebitda": IPO_MIN_EBITDA}

    # ------------------------------------------------------------------ deal flow
    def pipeline(self, d: Optional[date] = None, portfolio_id: Optional[str] = None) -> List[Dict]:
        """This month's opportunities. Deterministic: same seed, same month, same deals. Hidden facts stay hidden unless diligence was bought."""
        w = self.w
        d = d or w.current_date
        rng = random.Random(f"{w.seed}|pequity|{d.year}-{d.month:02d}")
        n = 6 + rng.randint(0, 3)
        close = w.calendar.roll(date(d.year + (d.month // 12), d.month % 12 + 1, 1))
        out = []
        for i in range(n):
            sector = rng.choice(list(SECTORS))
            sp = SECTORS[sector]
            source = rng.choices(list(SOURCES), weights=[5, 2, 2, 3])[0]
            # lower-middle market to large-cap: most deals are small, a few are big
            revenue = rng.choices([15, 25, 40, 60, 90, 120, 180, 250, 350, 500, 750, 1000, 1500, 2500], weights=[4, 5, 5, 4, 4, 3, 3, 2, 2, 2, 1, 1, 1, 1])[0] * 1_000_000 * rng.uniform(0.85, 1.15)
            margin = rng.uniform(*sp["margin"])
            ebitda = revenue * margin
            growth = rng.uniform(*sp["growth"])
            quality = rng.uniform(0.9, 1.1)                                        # how the market rates this asset against its peers
            fair = self.public_multiple(sector) * quality * (1 + 0.6 * (growth - sum(sp["growth"]) / 2))
            ask = fair * (1 + SOURCES[source]["ask_prem"])
            qoe = round(rng.choice([0.0, 0.0, 0.0, -0.04, -0.08, -0.12, -0.18, 0.03]) * rng.uniform(0.8, 1.2), 4)
            did = f"PED-{d.year % 100:02d}{d.month:02d}-{i + 1:02d}"
            mgmt = round(rng.uniform(0.05, 0.15), 3)
            row = {"id": did, "name": f"{rng.choice(sp['names'])} ({SOURCES[source]['label']})", "sector": sector, "source": source, "revenue": round(revenue, -5),
                   "ebitda": round(ebitda, -5), "margin": round(margin, 4), "growth": round(growth, 4), "ask_multiple": round(ask, 1), "ask_ev": round(ask * ebitda, -6),
                   "public_multiple": self.public_multiple(sector), "max_leverage": self.max_leverage(sector), "management_rollover": mgmt,
                   "capex_pct": sp["capex"], "nwc_pct": sp["nwc"], "opened": date(d.year, d.month, 1).isoformat(), "closes": close.isoformat(),
                   "status": "TAKEN" if did in self.taken else ("OPEN" if d < close else "CLOSED"), "min_equity": MIN_EQUITY,
                   "_fair": fair, "_qoe": qoe, "_quality": quality}
            if portfolio_id:
                row["diligence"] = portfolio_id in self.diligence.get(did, set())
                row["qoe_adj"] = qoe if row["diligence"] else None
                b = self.bids.get(did, {}).get(portfolio_id)
                row["my_bid"] = b
            out.append(row)
        return out

    def deal(self, deal_id: str, portfolio_id: Optional[str] = None) -> Optional[Dict]:
        return next((x for x in self.pipeline(None, portfolio_id) if x["id"] == deal_id), None)

    def structure(self, deal: Dict, multiple: float, leverage: float) -> Dict:
        """The sources and uses of a bid: EV, debt tranches, fees, the fund's equity cheque."""
        ebitda = deal["ebitda"]
        ev = multiple * ebitda
        lev = max(0.0, min(leverage, deal["max_leverage"]))
        senior = min(lev, SENIOR_CAP + (0.5 if deal["sector"] == "Software" else 0.0)) * ebitda
        second = max(0.0, lev * ebitda - senior)
        debt = senior + second
        fees = ev * (FEES["advisory"] + FEES["legal"]) + debt * FEES["financing"]
        equity_total = ev + fees - debt
        own = 1.0 - deal["management_rollover"]
        cheque = equity_total * own
        tlb_spread = self.tlb_spread(lev)
        interest = senior * (self.policy_rate() + tlb_spread / 1e4) + second * (self.policy_rate() + SECOND_LIEN_SPREAD_BPS / 1e4)
        return {"ev": round(ev), "multiple": multiple, "leverage": round(lev, 2), "senior": round(senior), "second_lien": round(second), "debt": round(debt),
                "tlb_spread_bps": tlb_spread, "second_lien_spread_bps": SECOND_LIEN_SPREAD_BPS if second else None, "fees": round(fees), "equity_total": round(equity_total),
                "ownership": round(own, 3), "equity_cheque": round(cheque), "interest_year1": round(interest), "interest_cover": round(ebitda / interest, 2) if interest else None,
                "equity_pct_of_ev": round(equity_total / ev, 3) if ev else None}

    # ------------------------------------------------------------------ commands
    def _mandate(self, pf: Portfolio) -> None:
        from ..world import CommandError
        job = self.w.careers.job_for(pf)
        if job and job.allowed_classes and "PRIVATE_EQUITY" not in job.allowed_classes:
            raise CommandError("private equity is outside this job's mandate")

    def buy_diligence(self, pf: Portfolio, deal_id: str) -> Dict:
        from ..world import CommandError
        w = self.w
        self._mandate(pf)
        deal = self.deal(deal_id, pf.id)
        if deal is None or deal["status"] != "OPEN":
            raise CommandError(f"{deal_id} is not open")
        if deal["diligence"]:
            raise CommandError("diligence already bought on this deal")
        fee = money(D(str(deal["ask_ev"])) * D(str(FEES["diligence"])))
        reason = w.prime.affordable(pf, fee, None)
        if reason:
            raise CommandError(f"cannot pay the diligence fee {fee:,.0f}: {reason}")
        w.emit(E.PE_DILIGENCE, {"portfolio_id": pf.id, "deal_id": deal_id, "fee": fee, "qoe_adj": deal["_qoe"], "currency": pf.base_currency,
                                "findings": self._findings(deal)}, portfolio_id=pf.id)
        return self.deal(deal_id, pf.id)

    @staticmethod
    def _findings(deal: Dict) -> str:
        q = deal["_qoe"]
        if q >= 0.02:
            return f"quality of earnings: run-rate EBITDA is {q:+.0%} against the marketing case (under-reported one-offs)"
        if q > -0.02:
            return "quality of earnings: the marketing case holds; no material adjustments"
        return f"quality of earnings: adjusted EBITDA is {q:+.0%} against the marketing case (pro-forma add-backs, customer concentration)"

    def bid(self, pf: Portfolio, deal_id: str, multiple: float, leverage: float) -> Dict:
        from ..world import CommandError
        w = self.w
        self._mandate(pf)
        deal = self.deal(deal_id, pf.id)
        if deal is None:
            raise CommandError(f"unknown deal {deal_id}: the pipeline refreshes monthly")
        if deal["status"] != "OPEN":
            raise CommandError(f"{deal_id} is {deal['status'].lower()}")
        multiple = float(multiple); leverage = float(leverage)
        if not (3.0 <= multiple <= 40.0):
            raise CommandError("bid an EV/EBITDA multiple between 3x and 40x")
        if leverage < 0 or leverage > deal["max_leverage"] + 1e-9:
            raise CommandError(f"lenders will fund at most {deal['max_leverage']:.1f}x EBITDA on this deal in this market")
        st = self.structure(deal, multiple, leverage)
        cheque = money(D(str(st["equity_cheque"])))
        if cheque < MIN_EQUITY:
            raise CommandError(f"the equity cheque must be at least {MIN_EQUITY:,.0f}")
        reason = w.prime.affordable(pf, cheque, None)
        if reason:
            raise CommandError(f"cannot fund the equity cheque {cheque:,.0f}: {reason}")
        decides = w.calendar.add_business_days(w.current_date, BID_DAYS).isoformat()
        w.emit(E.PE_BID, {"portfolio_id": pf.id, "deal_id": deal_id, "multiple": multiple, "leverage": st["leverage"], "structure": st, "decides": decides,
                          "deal": {k: v for k, v in deal.items() if not k.startswith("_")}, "hidden": {"fair": deal["_fair"], "qoe": deal["_qoe"], "quality": deal["_quality"]}},
               portfolio_id=pf.id)
        return self.bids[deal_id][pf.id]

    def initiative(self, pf: Portfolio, company_id: str, kind: str, size_pct: Optional[float] = None) -> PortfolioCompany:
        from ..world import CommandError
        w = self.w
        c = pf.pe_companies.get(company_id)
        if c is None or c.status != "ACTIVE":
            raise CommandError("no such active portfolio company")
        kind = str(kind).upper()
        if kind not in INITIATIVES:
            raise CommandError(f"initiative must be one of {', '.join(INITIATIVES)}")
        if any(i["kind"] == kind and i["status"] == "RUNNING" for i in c.initiatives):
            raise CommandError(f"a {INITIATIVES[kind]['label'].lower()} is already running")
        spec = INITIATIVES[kind]
        ev_now = c.ebitda * max(c.mark_multiple, c.entry_multiple)
        cost = money(D(str(ev_now * spec["cost_pct_ev"])) * D(str(c.ownership)))
        payload = {"portfolio_id": pf.id, "company_id": c.id, "kind": kind, "cost": cost, "currency": pf.base_currency, "quarters": spec["quarters"]}
        if kind == "ADD_ON":
            pct = float(size_pct or 0.2)
            if not (0.05 <= pct <= 0.6):
                raise CommandError("an add-on is 5% to 60% of the platform's EBITDA")
            target_ebitda = c.ebitda * pct
            mult = max(4.0, self.public_multiple(c.sector) * 0.65)          # smaller companies sell cheaper: the multiple arbitrage
            ev = target_ebitda * mult
            room = max(0.0, self.max_leverage(c.sector) - c.leverage) * c.ebitda
            debt = min(ev * 0.5, room)
            equity = (ev * (1 + FEES["advisory"] + FEES["legal"]) - debt)
            cost = money(D(str(equity)) * D(str(c.ownership)))
            payload.update({"cost": cost, "add_on": {"ebitda": target_ebitda, "multiple": round(mult, 1), "ev": round(ev), "debt": round(debt), "equity": round(equity),
                                                     "revenue": target_ebitda / max(0.05, c.margin)}})
        reason = w.prime.affordable(pf, cost, None) if cost > 0 else None
        if reason:
            raise CommandError(f"cannot fund {cost:,.0f}: {reason}")
        w.emit(E.PE_INITIATIVE, payload, portfolio_id=pf.id)
        return c

    def recap(self, pf: Portfolio, company_id: str, target_leverage: float) -> PortfolioCompany:
        from ..world import CommandError
        w = self.w
        c = pf.pe_companies.get(company_id)
        if c is None or c.status != "ACTIVE":
            raise CommandError("no such active portfolio company")
        target = float(target_leverage)
        cap = self.max_leverage(c.sector)
        if target > cap + 1e-9:
            raise CommandError(f"lenders will fund at most {cap:.1f}x on this company in this market")
        new_debt = (target - c.leverage) * c.ebitda
        if new_debt < c.ebitda * 0.5:
            raise CommandError("a recap must raise at least half a turn of EBITDA")
        if c.quarters_held < 2:
            raise CommandError("lenders want two quarters of reporting under the sponsor before a recap")
        fee = new_debt * FEES["recap"]
        dist = money(D(str((new_debt - fee))) * D(str(c.ownership)))
        w.emit(E.PE_RECAP, {"portfolio_id": pf.id, "company_id": c.id, "new_debt": round(new_debt), "fee": round(fee), "distribution": dist, "target_leverage": target,
                            "spread_bps": self.tlb_spread(target) + 50, "currency": pf.base_currency}, portfolio_id=pf.id)
        return c

    def cure(self, pf: Portfolio, company_id: str) -> PortfolioCompany:
        from ..world import CommandError
        w = self.w
        c = pf.pe_companies.get(company_id)
        if c is None or c.status != "BREACH" or not c.breach:
            raise CommandError("no covenant breach to cure on that company")
        amt = money(D(str(c.breach["cure_amount"])))
        reason = w.prime.affordable(pf, amt, None)
        if reason:
            raise CommandError(f"cannot fund the equity cure {amt:,.0f}: {reason}")
        w.emit(E.PE_CURED, {"portfolio_id": pf.id, "company_id": c.id, "amount": amt, "currency": pf.base_currency}, portfolio_id=pf.id)
        return c

    def start_exit(self, pf: Portfolio, company_id: str, route: str) -> PortfolioCompany:
        from ..world import CommandError
        w = self.w
        c = pf.pe_companies.get(company_id)
        if c is None or c.status != "ACTIVE":
            raise CommandError("no such active portfolio company (a breach must be cured first)")
        route = str(route).upper()
        if route not in ("SALE", "IPO"):
            raise CommandError("route is SALE or IPO")
        if route == "IPO":
            if c.ebitda < IPO_MIN_EBITDA:
                raise CommandError(f"too small for a listing: EBITDA below {IPO_MIN_EBITDA:,.0f}")
            if self.regime() not in ("NORMAL_GROWTH", "RATE_CUTTING", "RATE_HIKING"):
                raise CommandError("the IPO window is shut in this market")
        days = SALE_DAYS if route == "SALE" else IPO_DAYS
        completes = w.calendar.add_business_days(w.current_date, days).isoformat()
        w.emit(E.PE_EXIT_STARTED, {"portfolio_id": pf.id, "company_id": c.id, "route": route, "completes": completes}, portfolio_id=pf.id)
        return c

    def selldown(self, pf: Portfolio, company_id: str, fraction: float) -> PortfolioCompany:
        from ..world import CommandError
        w = self.w
        c = pf.pe_companies.get(company_id)
        if c is None or c.status != "LISTED" or not c.listed:
            raise CommandError("no listed stake on that company")
        if w.current_date.isoformat() < c.listed["lockup_until"]:
            raise CommandError(f"lock-up runs until {c.listed['lockup_until']}")
        frac = max(0.0, min(1.0, float(fraction)))
        shares = c.listed["held"] * frac
        if shares <= 0:
            raise CommandError("nothing to sell")
        self._emit_selldown(pf, c, shares, None, "block sale after the lock-up")
        return c

    def _emit_selldown(self, pf: Portfolio, c: PortfolioCompany, shares: float, cause: Optional[Event], memo: str) -> None:
        w = self.w
        px = c.listed["price"] * 0.97                                       # a block goes at a discount
        proceeds = money(D(str(shares * px)))
        w.emit(E.PE_SELLDOWN, {"portfolio_id": pf.id, "company_id": c.id, "shares": shares, "price": round(px, 4), "proceeds": proceeds, "memo": memo,
                               "currency": pf.base_currency}, cause_id=cause.id if cause else None, portfolio_id=pf.id)

    # ------------------------------------------------------------------ daily process
    def process_day(self, cause: Event, prev_date: date) -> None:
        w = self.w
        today = w.current_date
        iso = today.isoformat()
        # bids decided
        for did, by_pf in list(self.bids.items()):
            for pid, b in list(by_pf.items()):
                if iso >= b["decides"]:
                    self._decide(w.portfolios[pid], did, b, cause)
        # companies
        for pf in w.portfolios.values():
            for c in list(pf.pe_companies.values()):
                if c.status in ("SOLD", "IPO_COMPLETE", "RESTRUCTURED", "FAILED"):
                    continue
                if c.status == "BREACH" and c.breach and iso >= c.breach["cure_by"]:
                    w.emit(E.PE_RESTRUCTURED, {"portfolio_id": pf.id, "company_id": c.id, "reason": "covenant breach not cured: the lenders took the keys",
                                               "currency": pf.base_currency}, cause_id=cause.id, portfolio_id=pf.id)
                    continue
                if c.status in ("ACTIVE", "BREACH", "EXIT_PROCESS", "IPO_PROCESS") and c.next_report and iso >= c.next_report:
                    self._report(pf, c, cause)
                    if c.status in ("RESTRUCTURED",):
                        continue
                if c.status in ("EXIT_PROCESS", "IPO_PROCESS") and c.exit and iso >= c.exit["completes"]:
                    self._complete_exit(pf, c, cause)
                    continue
                if c.status == "LISTED" and c.listed:
                    self._listed_day(pf, c, cause, prev_date)

    def _decide(self, pf: Portfolio, did: str, b: Dict, cause: Event) -> None:
        w = self.w
        rng = random.Random(f"{w.seed}|pedecide|{did}|{pf.id}")
        deal, hidden = b["deal"], b["hidden"]
        src = SOURCES[deal["source"]]
        fair = hidden["fair"]
        floor = deal["ask_multiple"] * (1 - src["give"])
        edge = (b["multiple"] - floor) / max(0.1, fair)                      # how far above the seller's floor, in multiple terms
        if did in self.taken:
            won, note = False, "sold to another buyer before the answer came"
        elif b["multiple"] < floor:
            won, note = False, f"declined: below the seller's floor of {floor:.1f}x"
        else:
            p = 1 / (1 + math.exp(-(edge * 40 - 1.0 * src["compete"] + 0.8)))
            won = rng.random() < p
            note = (f"won at {b['multiple']:.1f}x (fair {fair:.1f}x, ask {deal['ask_multiple']:.1f}x)" if won
                    else f"lost to a {rng.choice(['strategic buyer', 'rival sponsor', 'higher bid'])} at about {max(b['multiple'] + 0.3, deal['ask_multiple'] * rng.uniform(1.0, 1.06)):.1f}x")
        payload = {"portfolio_id": pf.id, "deal_id": did, "won": won, "note": note, "bid": b, "currency": pf.base_currency}
        if won:
            payload["company_id"] = w.new_id("PEC")
            payload["next_report"] = self._quarter_end_after(w.current_date).isoformat()
        w.emit(E.PE_DEAL_RESOLVED, payload, cause_id=cause.id, portfolio_id=pf.id)

    def _quarter_end_after(self, d: date) -> date:
        m = ((d.month - 1) // 3 + 1) * 3
        qe = date(d.year, m, 28) + timedelta(days=4)
        qe = qe - timedelta(days=qe.day)
        if qe <= d:
            m2 = m + 3
            y = d.year + (m2 - 1) // 12
            m2 = (m2 - 1) % 12 + 1
            qe = date(y, m2, 28) + timedelta(days=4)
            qe = qe - timedelta(days=qe.day)
        return self.w.calendar.roll_back(qe)

    def _report(self, pf: Portfolio, c: PortfolioCompany, cause: Event) -> None:
        """One quarter of operating the company: growth, margin, cash flow, debt service, covenants, the mark."""
        w = self.w
        today = w.current_date
        rng = random.Random(f"{w.seed}|pereport|{c.id}|{today.isoformat()}")
        sp = SECTORS[c.sector]
        # equity market over the quarter
        h = w.market.history.get("SPY") or []
        q_ret = 0.0
        if len(h) > 63:
            q_ret = float(h[-1].close) / float(h[-64].close) - 1.0
        g_add, m_add, notes = 0.0, 0.0, []
        for ini in c.initiatives:
            if ini["status"] != "RUNNING":
                continue
            spec = INITIATIVES[ini["kind"]]
            ini["quarters_done"] += 1
            factor = ini["factor"]
            g_add += spec["growth"] * factor
            m_add += spec["margin"] * factor / spec["quarters"]
            if ini["quarters_done"] >= spec["quarters"]:
                ini["status"] = "DONE"
                notes.append(f"{spec['label'].lower()} complete ({'delivered' if factor >= 1 else 'delivered half'})")
        qoe_note = ""
        if not c.qoe_applied:
            c.qoe_applied = True
            if abs(c.qoe_adj) >= 0.01:
                qoe_note = f"first report under the sponsor: run-rate EBITDA {c.qoe_adj:+.0%} against the marketing case"
        growth_q = (c.growth + REGIME_GROWTH.get(self.regime(), 0.0) + g_add) / 4 + 0.25 * sp["beta"] * q_ret + rng.gauss(0, 0.02)
        rev0 = c.revenue
        revenue = rev0 * (1 + growth_q)
        margin = c.margin + (c.target_margin - c.margin) * 0.2 + m_add + rng.gauss(0, 0.004)
        margin = max(0.02, min(0.6, margin))
        ebitda_q = revenue * margin / 4
        qoe_hit = c.entry_ebitda * c.qoe_adj if qoe_note else 0.0
        ebitda_ltm = revenue * margin + qoe_hit
        interest = (c.tlb * (self.policy_rate() + c.tlb_spread_bps / 1e4) + c.second_lien * (self.policy_rate() + c.second_lien_spread_bps / 1e4)
                    + c.revolver * (self.policy_rate() + 0.03)) / 4
        da = c.capex_pct * revenue * 0.8 / 4
        taxes = max(0.0, (ebitda_q - da - interest)) * c.tax_rate
        capex = c.capex_pct * revenue / 4
        d_nwc = c.nwc_pct * (revenue - rev0) / 4
        fcf = ebitda_q + qoe_hit / 4 - interest - taxes - capex - d_nwc
        mandatory = c.tlb * 0.0025
        paydown = revolver_draw = 0.0
        cash = c.cash + fcf
        if cash >= mandatory:
            sweep = mandatory + max(0.0, cash - mandatory) * 0.5
            paydown = min(c.tlb, sweep)
            cash -= paydown
        else:
            revolver_draw = mandatory - cash
            cash = 0.0
            paydown = min(c.tlb, mandatory)
        tlb = c.tlb - paydown
        revolver = c.revolver + revolver_draw
        if cash > 0 and revolver > 0:
            rep = min(cash, revolver)
            revolver -= rep
            cash -= rep
        net_debt = tlb + c.second_lien + revolver - cash
        leverage = net_debt / ebitda_ltm if ebitda_ltm > 0 else 99.0
        icr = ebitda_ltm / (interest * 4) if interest > 0 else 99.0
        # the mark: entry multiple drifting toward the public comparables over eight quarters, private-market smoothing
        pub = self.public_multiple(c.sector) * c.exit_quality if hasattr(c, "exit_quality") else self.public_multiple(c.sector)
        wgt = min(1.0, (c.quarters_held + 1) / 8.0)
        mark_mult = round(c.entry_multiple * (1 - wgt) + pub * wgt, 2)
        equity_value = max(0.0, ebitda_ltm * mark_mult - net_debt)
        mark_value = money(D(str(equity_value)) * D(str(c.ownership)))
        breach = None
        if c.status in ("ACTIVE", "EXIT_PROCESS", "IPO_PROCESS") and (leverage > c.cov_max_leverage or icr < c.cov_min_icr):
            cure_amt = max(ebitda_ltm * 0.5, (leverage - c.cov_max_leverage) * ebitda_ltm if leverage > c.cov_max_leverage else 0.0)
            breach = {"reason": (f"net leverage {leverage:.1f}x above the {c.cov_max_leverage:.1f}x covenant" if leverage > c.cov_max_leverage
                                 else f"interest cover {icr:.2f}x below the {c.cov_min_icr:.2f}x covenant"),
                      "cure_amount": round(cure_amt), "cure_by": w.calendar.add_business_days(today, CURE_DAYS).isoformat()}
        report = {"date": today.isoformat(), "revenue": round(revenue), "growth_q": round(growth_q, 4), "margin": round(margin, 4), "ebitda_ltm": round(ebitda_ltm),
                  "fcf": round(fcf), "interest": round(interest), "taxes": round(taxes), "capex": round(capex), "paydown": round(paydown), "revolver_draw": round(revolver_draw),
                  "tlb": round(tlb), "revolver": round(revolver), "cash": round(cash), "net_debt": round(net_debt), "leverage": round(leverage, 2), "icr": round(icr, 2),
                  "mark_multiple": mark_mult, "public_multiple": pub, "equity_value": round(equity_value), "mark_value": mark_value, "q_market_return": round(q_ret, 4),
                  "notes": notes + ([qoe_note] if qoe_note else []), "qoe_hit": round(qoe_hit)}
        w.emit(E.PE_REPORT, {"portfolio_id": pf.id, "company_id": c.id, "report": report, "breach": breach,
                             "next_report": self._quarter_end_after(today).isoformat(), "initiatives": c.initiatives, "currency": pf.base_currency},
               cause_id=cause.id, portfolio_id=pf.id)

    def _complete_exit(self, pf: Portfolio, c: PortfolioCompany, cause: Event) -> None:
        w = self.w
        today = w.current_date
        rng = random.Random(f"{w.seed}|peexit|{c.id}|{today.isoformat()}")
        route = c.exit["route"]
        pub = self.public_multiple(c.sector)
        held_years = (today - date.fromisoformat(c.entry_date)).days / 365.0
        if route == "SALE":
            fail_p = {"RECESSION": 0.45, "LIQUIDITY_STRESS": 0.75}.get(self.regime(), 0.08)
            if rng.random() < fail_p:
                w.emit(E.PE_EXITED, {"portfolio_id": pf.id, "company_id": c.id, "route": route, "failed": True, "fee": round(c.ebitda * pub * 0.003),
                                     "note": "process pulled: no bids at an acceptable level; the company stays in the portfolio", "currency": pf.base_currency},
                       cause_id=cause.id, portfolio_id=pf.id)
                return
            strategic = rng.random() < 0.55
            premium = rng.uniform(0.08, 0.22) if strategic else rng.uniform(0.0, 0.10)
            if held_years < 1.0:
                premium -= 0.08                                           # a quick flip: buyers wonder why
            mult = pub * (1 + premium) * rng.uniform(0.96, 1.04)
            ev = c.ebitda * mult
            fee = ev * FEES["sale"]
            proceeds = money(D(str(max(0.0, ev - c.net_debt - fee))) * D(str(c.ownership)))
            w.emit(E.PE_EXITED, {"portfolio_id": pf.id, "company_id": c.id, "route": route, "failed": False, "multiple": round(mult, 2), "ev": round(ev), "fee": round(fee),
                                 "proceeds": proceeds, "buyer": "strategic buyer" if strategic else "another sponsor", "currency": pf.base_currency,
                                 "note": f"sold to a {'strategic buyer' if strategic else 'sponsor'} at {mult:.1f}x EBITDA ({ev:,.0f} EV)"},
                   cause_id=cause.id, portfolio_id=pf.id)
        else:
            if self.regime() in ("RECESSION", "LIQUIDITY_STRESS") or rng.random() < 0.12:
                w.emit(E.PE_EXITED, {"portfolio_id": pf.id, "company_id": c.id, "route": route, "failed": True, "fee": round(c.ebitda * pub * 0.004),
                                     "note": "IPO postponed: the window shut during the roadshow", "currency": pf.base_currency}, cause_id=cause.id, portfolio_id=pf.id)
                return
            mult = pub * 0.90 * rng.uniform(0.95, 1.05)                    # the IPO discount
            ev = c.ebitda * mult
            equity = max(0.0, ev - c.net_debt)
            shares_total = 100_000_000.0
            px = equity / shares_total
            fund_shares = shares_total * c.ownership
            sold = fund_shares * 0.25
            gross = sold * px
            fee = gross * FEES["ipo"]
            proceeds = money(D(str(gross - fee)))
            lock = w.calendar.roll(today + timedelta(days=LOCKUP_DAYS)).isoformat()
            w.emit(E.PE_EXITED, {"portfolio_id": pf.id, "company_id": c.id, "route": route, "failed": False, "multiple": round(mult, 2), "ev": round(ev), "fee": round(fee),
                                 "proceeds": proceeds, "currency": pf.base_currency, "note": f"listed at {mult:.1f}x EBITDA, {px:.2f} a share; a quarter of the stake sold at the offer",
                                 "listed": {"shares": shares_total, "price": round(px, 4), "held": fund_shares - sold, "lockup_until": lock, "sold_at_ipo": sold}},
                   cause_id=cause.id, portfolio_id=pf.id)

    def _listed_day(self, pf: Portfolio, c: PortfolioCompany, cause: Event, prev_date: date) -> None:
        """A listed stake: the share price walks with the market and its own noise; after the lock-up the stake is sold in blocks."""
        w = self.w
        today = w.current_date
        rng = random.Random(f"{w.seed}|pelisted|{c.id}|{today.isoformat()}")
        h = w.market.history.get("SPY") or []
        m_ret = float(h[-1].close) / float(h[-2].close) - 1.0 if len(h) > 1 else 0.0
        r = c.beta * m_ret + rng.gauss(0, 0.022)
        px = max(0.05, c.listed["price"] * math.exp(r))
        w.emit(E.PE_MARKED, {"portfolio_id": pf.id, "company_id": c.id, "listed_price": round(px, 4), "mark_value": money(D(str(px * c.listed["held"])))},
               cause_id=cause.id, portfolio_id=pf.id)
        iso = today.isoformat()
        if iso >= c.listed["lockup_until"] and c.listed["held"] > 0:
            tranche = c.listed.get("tranche", 0)
            due = w.calendar.roll(date.fromisoformat(c.listed["lockup_until"]) + timedelta(days=90 * tranche)).isoformat()
            if iso >= due:
                shares = min(c.listed["held"], c.listed["shares"] * c.ownership * 0.25)
                self._emit_selldown(pf, c, shares, cause, f"lock-up block {tranche + 1}")

    # ------------------------------------------------------------------ views
    def book(self, pf: Portfolio) -> Dict:
        w = self.w
        today = w.current_date
        rows = []
        invested = distributions = value = realized = ZERO
        for c in pf.pe_companies.values():
            row = asdict(c)
            row["debt"], row["net_debt"], row["leverage"] = round(c.debt), round(c.net_debt), round(c.leverage, 2)
            row["public_multiple"] = self.public_multiple(c.sector)
            active = c.status not in ("SOLD", "IPO_COMPLETE", "RESTRUCTURED", "FAILED")
            row["carrying"] = c.mark_value if active else ZERO
            row["moic"] = round(float(c.distributions + (c.mark_value if active else ZERO)) / float(c.equity_invested), 2) if c.equity_invested else None
            flows = [(date.fromisoformat(f["date"]), float(f["amount"])) for f in c.flows]
            if active and c.mark_value > 0:
                flows.append((today, float(c.mark_value)))
            row["irr"] = irr(flows)
            row["held_years"] = round((today - date.fromisoformat(c.entry_date)).days / 365.0, 2)
            row["unrealized"] = (c.mark_value - c.equity_invested + c.distributions) if active else ZERO
            row["max_leverage"] = self.max_leverage(c.sector)
            row["can_recap"] = active and c.status == "ACTIVE" and c.quarters_held >= 2 and c.leverage + 0.5 <= self.max_leverage(c.sector)
            row["can_ipo"] = active and c.status == "ACTIVE" and c.ebitda >= IPO_MIN_EBITDA and self.regime() in ("NORMAL_GROWTH", "RATE_CUTTING", "RATE_HIKING")
            rows.append(row)
            invested += c.equity_invested
            distributions += c.distributions
            realized += c.realized
            if active:
                value += c.mark_value
        pending = [{"deal_id": did, **b} for did, by in self.bids.items() for pid, b in by.items() if pid == pf.id]
        tvpi = float(distributions + value) / float(invested) if invested else None
        return {"pipeline": self.pipeline(None, pf.id), "companies": rows, "pending_bids": pending, "market": self.market(), "initiatives": INITIATIVES,
                "totals": {"invested": invested, "value": value, "distributions": distributions, "realized": realized, "companies_active": sum(1 for r in rows if r["carrying"] > 0 or r["status"] in ("ACTIVE", "BREACH", "EXIT_PROCESS", "IPO_PROCESS", "LISTED")),
                           "dpi": round(float(distributions) / float(invested), 3) if invested else None, "rvpi": round(float(value) / float(invested), 3) if invested else None,
                           "tvpi": round(tvpi, 3) if tvpi is not None else None}}

    # ------------------------------------------------------------------ ledger helpers
    def _cash(self, pf: Portfolio, ccy: str, amount: Decimal, kind: str, memo: str, ev: Event) -> None:
        ca = pf.cash_account(ccy)
        ca.balance += amount
        ca.base_value += amount
        self.w.record_cash_movement(pf, ccy, amount, kind, memo, ev)

    def _remark(self, pf: Portfolio, c: PortfolioCompany, new_value: Decimal, ev: Event, memo: str) -> None:
        delta = money(new_value - c.mark_value)
        c.mark_value = money(new_value)
        if delta:
            lines = [dr("1185", delta, c.id, "mark"), cr("4370", delta, c.id, "mark-to-market")] if delta > 0 else [dr("4370", -delta, c.id, "mark-to-market"), cr("1185", -delta, c.id, "mark")]
            self.w.post(pf.id, f"Private equity mark {c.id}: {memo}", lines, ev, {"company_id": c.id, "kind": "PRIVATE_EQUITY_MTM"})

    def _realise(self, pf: Portfolio, c: PortfolioCompany, cash: Decimal, ev: Event, ccy: str, memo: str, kind: str, cost_out: Optional[Decimal] = None) -> None:
        """Cash against the carrying value: release cost and valuation adjustment for the share leaving, realise the rest."""
        w = self.w
        cost_out = c.equity_invested if cost_out is None else cost_out
        va_out = money(c.mark_value - cost_out)
        if cash:
            self._cash(pf, ccy, cash, kind, f"{c.id}: {memo}", ev)
        lines = [dr(f"1010:{ccy}", cash, c.id, "proceeds"), cr("1180", cost_out, c.id, "cost released")] if cash else [cr("1180", cost_out, c.id, "cost released")]
        if va_out > 0:
            lines.append(cr("1185", va_out, c.id, "valuation adjustment released"))
        elif va_out < 0:
            lines.append(dr("1185", -va_out, c.id, "valuation adjustment released"))
        realised = cash - (cost_out + va_out)
        if realised > 0:
            lines.append(cr("4370", realised, c.id, "realised gain"))
        elif realised < 0:
            lines.append(dr("5360" if cash == 0 else "4370", -realised, c.id, "realised loss"))
        w.post(pf.id, f"Private equity {c.id}: {memo}", lines, ev, {"company_id": c.id, "kind": kind})
        c.realized += money(cash - c.equity_invested) if cost_out == c.equity_invested else ZERO
        c.equity_invested -= cost_out
        c.mark_value -= (cost_out + va_out)

    # ------------------------------------------------------------------ handlers
    def _h_diligence(self, ev: Event) -> None:
        p = ev.payload
        pf = self.w.portfolios[p["portfolio_id"]]
        self.diligence.setdefault(p["deal_id"], set()).add(pf.id)
        fee = D(p["fee"])
        self._cash(pf, p["currency"], -fee, "PRIVATE_EQUITY_FEE", f"{p['deal_id']}: diligence", ev)
        self.w.post(pf.id, f"Private equity diligence {p['deal_id']}: {fee:,.0f}", [dr("5360", fee, p["deal_id"], "diligence"), cr(f"1010:{p['currency']}", fee, p["deal_id"], "paid")], ev,
                    {"deal_id": p["deal_id"], "kind": "PRIVATE_EQUITY_FEE"})

    def _h_bid(self, ev: Event) -> None:
        p = ev.payload
        self.bids.setdefault(p["deal_id"], {})[p["portfolio_id"]] = {"multiple": p["multiple"], "leverage": p["leverage"], "structure": p["structure"], "decides": p["decides"],
                                                                     "deal": p["deal"], "hidden": p["hidden"], "date": ev.sim_date, "status": "PENDING"}

    def _h_resolved(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        b = p["bid"]
        self.bids.get(p["deal_id"], {}).pop(pf.id, None)
        if not p["won"]:
            pf.pe_log.append({"date": ev.sim_date, "deal_id": p["deal_id"], "note": f"{b['deal']['name']}: {p['note']}"})
            return
        deal, st, hidden = b["deal"], b["structure"], b["hidden"]
        self.taken.add(p["deal_id"])
        sp = SECTORS[deal["sector"]]
        cheque = money(D(str(st["equity_cheque"])))
        fees = money(D(str(st["fees"])) * D(str(st["ownership"])))
        cost = cheque - fees
        c = PortfolioCompany(id=p["company_id"], portfolio_id=pf.id, deal_id=p["deal_id"], name=deal["name"], sector=deal["sector"], source=deal["source"], entry_date=ev.sim_date,
                             entry_ev=float(st["ev"]), entry_multiple=float(b["multiple"]), entry_ebitda=float(deal["ebitda"]), revenue=float(deal["revenue"]), ebitda=float(deal["ebitda"]),
                             margin=float(deal["margin"]), target_margin=min(0.6, float(deal["margin"]) + 0.02), growth=float(deal["growth"]), capex_pct=float(deal["capex_pct"]),
                             nwc_pct=float(deal["nwc_pct"]), tax_rate=0.25, beta=sp["beta"], tlb=float(st["senior"]), tlb_spread_bps=float(st["tlb_spread_bps"]),
                             second_lien=float(st["second_lien"]), second_lien_spread_bps=SECOND_LIEN_SPREAD_BPS, ownership=float(st["ownership"]),
                             equity_invested=cost, fees_paid=fees, qoe_adj=float(hidden["qoe"]), qoe_revealed=pf.id in self.diligence.get(p["deal_id"], set()),
                             mark_multiple=float(b["multiple"]), mark_value=cost, cov_max_leverage=round(float(st["leverage"]) + 1.5, 1), next_report=p["next_report"])
        c.exit_quality = float(hidden.get("quality", 1.0))
        c.flows.append({"date": ev.sim_date, "amount": -float(cheque)})
        c.history.append({"date": ev.sim_date, "note": f"{p['note']}; EV {st['ev']:,.0f}, debt {st['debt']:,.0f} ({st['leverage']:.1f}x), equity cheque {cheque:,.0f} for {st['ownership']:.0%}"})
        pf.pe_companies[c.id] = c
        ccy = p["currency"]
        self._cash(pf, ccy, -cheque, "PRIVATE_EQUITY", f"{c.id}: equity cheque for {deal['name']}", ev)
        w.post(pf.id, f"Private equity {c.id}: buyout of {deal['name']}",
               [dr("1180", cost, c.id, "investment at cost"), dr("5360", fees, c.id, "transaction fees"), cr(f"1010:{ccy}", cheque, c.id, "equity cheque")], ev,
               {"company_id": c.id, "kind": "PRIVATE_EQUITY"})

    def _h_report(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        c = pf.pe_companies[p["company_id"]]
        r = p["report"]
        c.revenue, c.margin, c.ebitda = float(r["revenue"]), float(r["margin"]), float(r["ebitda_ltm"])
        c.tlb, c.revolver, c.cash = float(r["tlb"]), float(r["revolver"]), float(r["cash"])
        c.mark_multiple = float(r["mark_multiple"])
        c.quarters_held += 1
        c.next_report = p["next_report"]
        c.qoe_applied = True
        c.initiatives = p.get("initiatives", c.initiatives)
        c.reports.append(r)
        for n in r.get("notes", []):
            c.history.append({"date": ev.sim_date, "note": n})
        c.history.append({"date": ev.sim_date, "note": f"Q report: revenue {r['revenue']:,.0f} ({r['growth_q']:+.1%} q/q), margin {r['margin']:.1%}, LTM EBITDA {r['ebitda_ltm']:,.0f}, "
                                                        f"FCF {r['fcf']:,.0f}, leverage {r['leverage']:.1f}x, ICR {r['icr']:.1f}x, marked at {r['mark_multiple']:.1f}x"})
        if c.status not in ("LISTED",):
            self._remark(pf, c, D(r["mark_value"]), ev, f"{r['mark_multiple']:.1f}x LTM EBITDA less net debt, {c.ownership:.0%} share")
        if p.get("breach") and c.status in ("ACTIVE", "EXIT_PROCESS", "IPO_PROCESS"):
            b = p["breach"]
            c.status = "BREACH"
            c.breach = b
            c.exit = None
            c.history.append({"date": ev.sim_date, "note": f"COVENANT BREACH: {b['reason']}; equity cure of {b['cure_amount']:,.0f} needed by {b['cure_by']}"})

    def _h_marked(self, ev: Event) -> None:
        p = ev.payload
        pf = self.w.portfolios[p["portfolio_id"]]
        c = pf.pe_companies[p["company_id"]]
        if c.listed is not None and "listed_price" in p:
            c.listed["price"] = float(p["listed_price"])
        self._remark(pf, c, D(p["mark_value"]), ev, f"listed stake at {float(p.get('listed_price', 0)):.2f}" if "listed_price" in p else "mark")

    def _h_initiative(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        c = pf.pe_companies[p["company_id"]]
        spec = INITIATIVES[p["kind"]]
        rng = random.Random(f"{w.seed}|peini|{c.id}|{p['kind']}|{ev.seq}")
        factor = 0.5 if rng.random() < spec["risk"] else 1.0
        if p["kind"] == "NEW_CEO" and factor < 1:
            factor = -0.5                                                     # a botched transition hurts before it helps
        cost = D(p["cost"])
        ini = {"kind": p["kind"], "label": spec["label"], "started": ev.sim_date, "quarters": spec["quarters"], "quarters_done": 0, "status": "RUNNING", "factor": factor, "cost": cost}
        if p.get("add_on"):
            a = p["add_on"]
            c.revenue += float(a["revenue"]); c.ebitda += float(a["ebitda"]); c.tlb += float(a["debt"]); c.margin = c.ebitda / c.revenue if c.revenue else c.margin
            c.entry_ebitda += float(a["ebitda"])
            ini["add_on"] = a
            c.history.append({"date": ev.sim_date, "note": f"add-on: {a['ebitda']:,.0f} EBITDA bought at {a['multiple']:.1f}x ({a['ev']:,.0f} EV), {a['debt']:,.0f} incremental debt"})
        else:
            c.history.append({"date": ev.sim_date, "note": f"{spec['label']} started ({cost:,.0f})"})
        c.initiatives.append(ini)
        ccy = p["currency"]
        if cost > 0:
            self._cash(pf, ccy, -cost, "PRIVATE_EQUITY", f"{c.id}: {spec['label'].lower()}", ev)
            if p.get("add_on"):
                c.equity_invested += cost
                c.flows.append({"date": ev.sim_date, "amount": -float(cost)})
                w.post(pf.id, f"Private equity {c.id}: add-on equity", [dr("1180", cost, c.id, "add-on equity"), cr(f"1010:{ccy}", cost, c.id, "paid")], ev, {"company_id": c.id, "kind": "PRIVATE_EQUITY"})
                c.mark_value += cost
            else:
                w.post(pf.id, f"Private equity {c.id}: {spec['label'].lower()}", [dr("5360", cost, c.id, spec["label"]), cr(f"1010:{ccy}", cost, c.id, "paid")], ev, {"company_id": c.id, "kind": "PRIVATE_EQUITY_FEE"})

    def _h_recap(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        c = pf.pe_companies[p["company_id"]]
        new_debt = float(p["new_debt"])
        # the new money is a fresh term loan tranche: blended spread
        c.tlb_spread_bps = round((c.tlb * c.tlb_spread_bps + new_debt * float(p["spread_bps"])) / max(1.0, c.tlb + new_debt), 1)
        c.tlb += new_debt
        dist = D(p["distribution"])
        c.distributions += dist
        c.flows.append({"date": ev.sim_date, "amount": float(dist)})
        c.history.append({"date": ev.sim_date, "note": f"dividend recap: {new_debt:,.0f} new debt to {p['target_leverage']:.1f}x, distribution {dist:,.0f}"})
        ccy = p["currency"]
        self._cash(pf, ccy, dist, "PRIVATE_EQUITY_DISTRIBUTION", f"{c.id}: dividend recap", ev)
        w.post(pf.id, f"Private equity {c.id}: recap distribution {dist:,.0f}", [dr(f"1010:{ccy}", dist, c.id, "distribution"), cr("4380", dist, c.id, "dividend recap")], ev,
               {"company_id": c.id, "kind": "PRIVATE_EQUITY_DISTRIBUTION"})
        # the equity is worth less by the debt added: re-mark
        self._remark(pf, c, max(ZERO, c.mark_value - money(D(str(new_debt)) * D(str(c.ownership)))), ev, "after the recap")

    def _h_covenant(self, ev: Event) -> None:   # reserved: breaches ride on PE_REPORT
        pass

    def _h_cured(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        c = pf.pe_companies[p["company_id"]]
        amt = D(p["amount"])
        c.cash += float(amt) / max(0.01, c.ownership) * c.ownership      # the fund's cure goes in as cash (pays the revolver first)
        rep = min(c.cash, c.revolver)
        c.revolver -= rep; c.cash -= rep
        c.status = "ACTIVE"
        c.breach = None
        c.equity_invested += amt
        c.mark_value += amt
        c.flows.append({"date": ev.sim_date, "amount": -float(amt)})
        c.history.append({"date": ev.sim_date, "note": f"equity cure of {amt:,.0f}: covenant reset"})
        ccy = p["currency"]
        self._cash(pf, ccy, -amt, "PRIVATE_EQUITY", f"{c.id}: equity cure", ev)
        w.post(pf.id, f"Private equity {c.id}: equity cure", [dr("1180", amt, c.id, "equity cure"), cr(f"1010:{ccy}", amt, c.id, "paid")], ev, {"company_id": c.id, "kind": "PRIVATE_EQUITY"})

    def _h_restructured(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        c = pf.pe_companies[p["company_id"]]
        c.history.append({"date": ev.sim_date, "note": f"RESTRUCTURED: {p['reason']}; the equity is written off"})
        self._realise(pf, c, ZERO, ev, p["currency"], "equity written off in the restructuring", "PRIVATE_EQUITY_LOSS")
        c.status = "RESTRUCTURED"
        c.breach = None
        c.mark_value = ZERO

    def _h_exit_started(self, ev: Event) -> None:
        p = ev.payload
        c = self.w.portfolios[p["portfolio_id"]].pe_companies[p["company_id"]]
        c.status = "EXIT_PROCESS" if p["route"] == "SALE" else "IPO_PROCESS"
        c.exit = {"route": p["route"], "started": ev.sim_date, "completes": p["completes"]}
        c.history.append({"date": ev.sim_date, "note": f"{'sale process launched' if p['route'] == 'SALE' else 'IPO filed'}; answer by {p['completes']}"})

    def _h_exited(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        c = pf.pe_companies[p["company_id"]]
        ccy = p["currency"]
        if p.get("failed"):
            fee = money(D(str(p["fee"])) * D(str(c.ownership)))
            c.status = "ACTIVE"
            c.exit = None
            c.history.append({"date": ev.sim_date, "note": p["note"]})
            if fee:
                self._cash(pf, ccy, -fee, "PRIVATE_EQUITY_FEE", f"{c.id}: broken process costs", ev)
                w.post(pf.id, f"Private equity {c.id}: broken process", [dr("5360", fee, c.id, "broken-deal costs"), cr(f"1010:{ccy}", fee, c.id, "paid")], ev, {"company_id": c.id, "kind": "PRIVATE_EQUITY_FEE"})
            return
        proceeds = D(p["proceeds"])
        c.history.append({"date": ev.sim_date, "note": p["note"]})
        if p["route"] == "SALE":
            c.distributions += proceeds
            c.flows.append({"date": ev.sim_date, "amount": float(proceeds)})
            self._realise(pf, c, proceeds, ev, ccy, "sale proceeds", "PRIVATE_EQUITY_EXIT")
            c.status = "SOLD"
            c.exit = {**(c.exit or {}), "completed": ev.sim_date, "multiple": p["multiple"], "ev": p["ev"], "buyer": p.get("buyer")}
            c.mark_value = ZERO
        else:
            li = p["listed"]
            c.listed = {"shares": float(li["shares"]), "price": float(li["price"]), "held": float(li["held"]), "lockup_until": li["lockup_until"], "tranche": 0, "sold_at_ipo": float(li["sold_at_ipo"])}
            # the offer: a quarter of the stake sold; the rest carried at the offer price
            sold_frac = float(li["sold_at_ipo"]) / (float(li["held"]) + float(li["sold_at_ipo"]))
            cost_out = money(c.equity_invested * D(str(sold_frac)))
            c.distributions += proceeds
            c.flows.append({"date": ev.sim_date, "amount": float(proceeds)})
            self._realise(pf, c, proceeds, ev, ccy, "shares sold at the offer", "PRIVATE_EQUITY_EXIT", cost_out=cost_out)
            c.status = "LISTED"
            c.exit = {**(c.exit or {}), "completed": ev.sim_date, "multiple": p["multiple"], "ev": p["ev"]}
            self._remark(pf, c, money(D(str(c.listed["held"] * c.listed["price"]))), ev, "remaining stake at the offer price")

    def _h_selldown(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        c = pf.pe_companies[p["company_id"]]
        shares = float(p["shares"])
        proceeds = D(p["proceeds"])
        frac = shares / c.listed["held"] if c.listed["held"] else 1.0
        cost_out = money(c.equity_invested * D(str(min(1.0, frac))))
        c.distributions += proceeds
        c.flows.append({"date": ev.sim_date, "amount": float(proceeds)})
        self._realise(pf, c, proceeds, ev, p["currency"], f"{p['memo']}: {shares:,.0f} shares at {float(p['price']):.2f}", "PRIVATE_EQUITY_EXIT", cost_out=cost_out)
        c.listed["held"] -= shares
        c.listed["tranche"] = c.listed.get("tranche", 0) + 1
        c.history.append({"date": ev.sim_date, "note": f"{p['memo']}: {shares:,.0f} shares at {float(p['price']):.2f}, {proceeds:,.0f}"})
        if c.listed["held"] <= 1e-6:
            c.status = "IPO_COMPLETE"
            c.mark_value = ZERO
            c.history.append({"date": ev.sim_date, "note": "fully exited"})
