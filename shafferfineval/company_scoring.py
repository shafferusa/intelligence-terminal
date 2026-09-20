"""
ShafferFinEval -- individual equity scoring engine.

Pure stdlib. No Streamlit, no network, no market-data imports: structured
values in, structured results out.

Model
-----
    CompanyRawScore = 0.40V + 0.25G + 0.20P + 0.15D

        V  Valuation      40%   EV/EBITDA vs an industry EBITDA cohort
        G  Growth         25%   0.45RG + 0.35RA + 0.20EG
        P  Profitability  20%   0.65EM + 0.35ROA
        D  Debt strength  15%   0.60ND + 0.40DM

    CompanyScore     = 0.75 x CompanyRawScore          clamped [-75, +75]
    FinalEquityScore = CompanyScore + SectorOverlay     clamped [-100, +100]

Every component except valuation is scored by percentile rank against companies
in the SAME INDUSTRY, falling back to the sector when the industry is too thin.
The fallback is always labelled, never silent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

from statlib import (
    clamp,
    is_finite,
    median,
    percentile,
    percentile_rank_within,
    percentile_to_score,
    winsorized_mean,
)

# --------------------------------------------------------------------------
# Weights
# --------------------------------------------------------------------------

MAJOR_WEIGHTS: dict[str, float] = {
    "valuation": 0.40,
    "growth": 0.25,
    "profitability": 0.20,
    "debt": 0.15,
}

GROWTH_WEIGHTS: dict[str, float] = {
    "revenue_growth": 0.45,
    "revenue_acceleration": 0.35,
    "ebitda_growth": 0.20,
}

PROFITABILITY_WEIGHTS: dict[str, float] = {
    "ebitda_margin": 0.65,
    "roa": 0.35,
}

DEBT_WEIGHTS: dict[str, float] = {
    "net_debt_ebitda": 0.60,
    "debt_market_cap": 0.40,
}

#: True where a HIGHER raw value deserves a higher score.
COMPONENT_HIGHER_IS_BETTER: dict[str, bool] = {
    "revenue_growth": True,
    "revenue_acceleration": True,
    "ebitda_growth": True,
    "ebitda_margin": True,
    "roa": True,
    "net_debt_ebitda": False,     # lower leverage is better
    "debt_market_cap": False,     # lower leverage is better
}

COMPONENT_LABELS: dict[str, str] = {
    "revenue_growth": "Revenue growth",
    "revenue_acceleration": "Revenue acceleration",
    "ebitda_growth": "EBITDA growth",
    "ebitda_margin": "EBITDA margin",
    "roa": "ROA",
    "net_debt_ebitda": "Net Debt / EBITDA",
    "debt_market_cap": "Debt / Market Cap",
}

FACTOR_LABELS: dict[str, str] = {
    "valuation": "Valuation",
    "growth": "Growth",
    "profitability": "Profitability",
    "debt": "Debt / Financial Strength",
}

SCORE_MIN, SCORE_MAX = -100.0, 100.0
COMPANY_SCALE = 0.75
COMPANY_SCORE_MIN, COMPANY_SCORE_MAX = -75.0, 75.0
FINAL_MIN, FINAL_MAX = -100.0, 100.0

#: tanh steepness in V = 100 * tanh(k * gap).
VALUATION_TANH_K = 2.0

#: Minimum same-industry peers before the industry is used as the benchmark.
MIN_INDUSTRY_PEERS = 5

#: Minimum companies in the 50th-75th percentile EBITDA cohort.
MIN_EBITDA_COHORT = 3

#: An EV/EBITDA outside this band is not an economically usable multiple.
MIN_VALID_EV_EBITDA = 0.1
MAX_VALID_EV_EBITDA = 300.0

INDUSTRY = "Industry"
SECTOR_FALLBACK = "Sector Fallback"

OK = "ok"
UNAVAILABLE = "unavailable"


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------

@dataclass
class CompanyFinancials:
    """Raw per-company inputs. Built by the data adapter; math happens here.

    Annual series are ordered oldest -> newest.
    """

    ticker: str
    name: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    price: Optional[float] = None
    shares_outstanding: Optional[float] = None
    market_cap: Optional[float] = None
    total_debt: Optional[float] = None
    cash: Optional[float] = None
    ebitda: Optional[float] = None                        # trailing twelve months
    revenue: Optional[float] = None                       # trailing twelve months
    net_income: Optional[float] = None
    total_assets: Optional[float] = None
    roa_supplied: Optional[float] = None
    revenue_annual: list[float] = field(default_factory=list)
    ebitda_annual: list[float] = field(default_factory=list)


# --------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------

@dataclass
class Component:
    """One normalised sub-metric (RG, RA, EG, EM, ROA, ND, DM)."""

    name: str
    raw_value: Optional[float] = None
    peer_median: Optional[float] = None
    percentile: Optional[float] = None
    score: Optional[float] = None
    status: str = UNAVAILABLE
    n_peers: int = 0
    benchmark_level: Optional[str] = None
    notes: list[str] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.status == OK and self.score is not None


@dataclass
class FactorResult:
    """One major factor (V, G, P, D)."""

    name: str
    score: Optional[float] = None
    status: str = UNAVAILABLE
    components: dict[str, Component] = field(default_factory=dict)
    weights_used: dict[str, float] = field(default_factory=dict)
    contributions: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.status == OK and self.score is not None


@dataclass
class PeerRow:
    ticker: str
    name: Optional[str]
    ebitda: Optional[float]
    enterprise_value: Optional[float]
    ev_ebitda: Optional[float]
    market_cap: Optional[float] = None
    total_debt: Optional[float] = None
    cash: Optional[float] = None


@dataclass
class ValuationDetail:
    """Everything behind the valuation factor, for display."""

    current_price: Optional[float] = None
    implied_price: Optional[float] = None
    valuation_gap: Optional[float] = None
    benchmark_ev_ebitda: Optional[float] = None
    company_ebitda: Optional[float] = None
    company_debt: Optional[float] = None
    company_cash: Optional[float] = None
    shares_outstanding: Optional[float] = None
    implied_ev: Optional[float] = None
    implied_equity_value: Optional[float] = None
    benchmark_level: Optional[str] = None
    cohort: list[PeerRow] = field(default_factory=list)
    n_industry_peers: int = 0
    n_valid_peers: int = 0
    notes: list[str] = field(default_factory=list)


@dataclass
class CompanyScoreResult:
    ticker: str
    raw_score: Optional[float] = None
    company_score: Optional[float] = None
    sector_overlay: Optional[float] = None
    final_score: Optional[float] = None
    label: Optional[str] = None
    factors: dict[str, FactorResult] = field(default_factory=dict)
    weights_used: dict[str, float] = field(default_factory=dict)
    contributions: dict[str, float] = field(default_factory=dict)
    valuation: ValuationDetail = field(default_factory=ValuationDetail)
    benchmark_level: Optional[str] = None
    notes: list[str] = field(default_factory=list)

    @property
    def scored(self) -> bool:
        return self.company_score is not None


# --------------------------------------------------------------------------
# Shared blending: drop unavailable parts, renormalise the rest
# --------------------------------------------------------------------------

def _blend(
    parts: dict[str, Component],
    weights: dict[str, float],
    label_map: dict[str, str],
) -> tuple[Optional[float], dict[str, float], dict[str, float], list[str]]:
    """Weighted blend that renormalises over whatever is available.

    A missing component never contributes a silent zero.
    """
    notes: list[str] = []
    available = {k: p for k, p in parts.items() if p.available}
    missing = sorted(k for k in weights if k not in available)

    if not available:
        notes.append("No component could be calculated.")
        return None, {}, {}, notes

    total = sum(weights[k] for k in available)
    if total <= 0:
        return None, {}, {}, ["No weighted component available."]

    used = {k: weights[k] / total for k in available}
    contributions = {k: used[k] * available[k].score for k in available}

    if missing:
        notes.append(
            "Unavailable and excluded: "
            + ", ".join(label_map.get(m, m) for m in missing)
            + f". Remaining weights renormalised over {total:.2f}."
        )
    return clamp(sum(contributions.values()), SCORE_MIN, SCORE_MAX), used, contributions, notes


def _normalise_component(
    name: str,
    raw_value: Optional[float],
    peer_values: Sequence[float],
    benchmark_level: Optional[str],
) -> Component:
    """Percentile-rank one company metric against its peer distribution."""
    higher_is_better = COMPONENT_HIGHER_IS_BETTER[name]
    component = Component(
        name=name, raw_value=raw_value, benchmark_level=benchmark_level
    )

    usable = [float(v) for v in peer_values if is_finite(v)]
    component.n_peers = len(usable)
    component.peer_median = median(usable)

    if not is_finite(raw_value):
        component.notes.append(f"{COMPONENT_LABELS[name]} unavailable for this company.")
        return component
    if len(usable) < MIN_EBITDA_COHORT:
        component.notes.append(
            f"Only {len(usable)} peers had a usable {COMPONENT_LABELS[name]} "
            f"-- not enough to rank against."
        )
        return component

    p = percentile_rank_within(raw_value, usable)
    score = percentile_to_score(p, higher_is_better)
    if score is None:
        component.notes.append("Percentile rank could not be computed.")
        return component

    component.percentile = p
    component.score = score
    component.status = OK
    return component


# --------------------------------------------------------------------------
# 1. VALUATION
# --------------------------------------------------------------------------

def enterprise_value(company: CompanyFinancials) -> Optional[float]:
    """EV = MarketCap + TotalDebt - Cash.

    Market cap uses Yahoo's figure when valid, otherwise price x shares --
    the conceptual relationship is kept explicit either way.
    """
    cap = company_market_cap(company)
    if cap is None:
        return None
    debt = float(company.total_debt) if is_finite(company.total_debt) else 0.0
    cash = float(company.cash) if is_finite(company.cash) else 0.0
    return cap + debt - cash


def company_market_cap(company: CompanyFinancials) -> Optional[float]:
    """MarketCap = Price x SharesOutstanding, or Yahoo's figure when valid."""
    if is_finite(company.market_cap) and float(company.market_cap) > 0:
        return float(company.market_cap)
    if (
        is_finite(company.price)
        and is_finite(company.shares_outstanding)
        and float(company.price) > 0
        and float(company.shares_outstanding) > 0
    ):
        return float(company.price) * float(company.shares_outstanding)
    return None


def _valid_ev_ebitda(company: CompanyFinancials) -> Optional[float]:
    """EV/EBITDA, or None when the observation is not economically usable."""
    if not is_finite(company.ebitda) or float(company.ebitda) <= 0:
        return None
    ev = enterprise_value(company)
    if ev is None or ev <= 0:
        return None
    multiple = ev / float(company.ebitda)
    if not is_finite(multiple):
        return None
    if not (MIN_VALID_EV_EBITDA <= multiple <= MAX_VALID_EV_EBITDA):
        return None
    return multiple


def build_industry_peer_set(
    target: CompanyFinancials,
    universe: Sequence[CompanyFinancials],
    min_peers: int = MIN_INDUSTRY_PEERS,
) -> tuple[list[CompanyFinancials], str, list[str]]:
    """Same-industry peers, excluding the target itself.

    Falls back to the whole sector when the industry is too thin, and says so.
    """
    notes: list[str] = []
    others = [c for c in universe if c.ticker != target.ticker]

    industry_peers = [
        c for c in others if target.industry and c.industry == target.industry
    ]
    if len(industry_peers) >= min_peers:
        return industry_peers, INDUSTRY, notes

    sector_peers = [c for c in others if target.sector and c.sector == target.sector]
    notes.append(
        f"Only {len(industry_peers)} '{target.industry or 'unknown'}' industry peers "
        f"(need {min_peers}) -- benchmarking against the {target.sector or 'unknown'} "
        f"sector instead ({len(sector_peers)} peers)."
    )
    return sector_peers, SECTOR_FALLBACK, notes


def build_ebitda_peer_cohort(
    peers: Sequence[CompanyFinancials],
) -> tuple[list[CompanyFinancials], list[str]]:
    """The ThirdQuartilePeerSet: peers whose EBITDA sits in the 50th-75th percentile.

    Deliberately the 50-75 band, not the top quartile. Only peers with a
    usable EV/EBITDA are considered in the first place.
    """
    notes: list[str] = []
    valid = [c for c in peers if _valid_ev_ebitda(c) is not None]
    if len(valid) < MIN_EBITDA_COHORT:
        notes.append(
            f"Only {len(valid)} peers had usable EBITDA and enterprise-value "
            f"inputs -- cannot build an EBITDA cohort."
        )
        return [], notes

    ebitdas = sorted(float(c.ebitda) for c in valid)
    p50 = percentile(ebitdas, 0.50)
    p75 = percentile(ebitdas, 0.75)
    cohort = [c for c in valid if p50 <= float(c.ebitda) <= p75]

    notes.append(
        f"EBITDA cohort = 50th-75th percentile of {len(valid)} valid peers "
        f"({p50:,.0f} to {p75:,.0f}); {len(cohort)} companies qualify."
    )
    return cohort, notes


def calculate_peer_ev_ebitda(
    cohort: Sequence[CompanyFinancials],
) -> tuple[Optional[float], list[PeerRow], int]:
    """Winsorized mean EV/EBITDA of the benchmark cohort."""
    rows: list[PeerRow] = []
    multiples: list[float] = []
    for peer in cohort:
        multiple = _valid_ev_ebitda(peer)
        rows.append(
            PeerRow(
                ticker=peer.ticker,
                name=peer.name,
                ebitda=peer.ebitda,
                enterprise_value=enterprise_value(peer),
                ev_ebitda=multiple,
                market_cap=company_market_cap(peer),
                total_debt=peer.total_debt,
                cash=peer.cash,
            )
        )
        if multiple is not None:
            multiples.append(multiple)

    rows.sort(key=lambda r: r.ticker)
    if len(multiples) < MIN_EBITDA_COHORT:
        return None, rows, len(multiples)

    benchmark, n_used = winsorized_mean(multiples)
    return benchmark, rows, n_used


def calculate_implied_equity_value(
    ebitda: Optional[float],
    benchmark_ev_ebitda: Optional[float],
    total_debt: Optional[float],
    cash: Optional[float],
) -> tuple[Optional[float], Optional[float]]:
    """(ImpliedEV, ImpliedEquityValue).

        ImpliedEV          = CompanyEBITDA x BenchmarkEVEBITDA
        ImpliedEquityValue = ImpliedEV - CompanyDebt + CompanyCash
    """
    if not is_finite(ebitda) or not is_finite(benchmark_ev_ebitda):
        return None, None
    implied_ev = float(ebitda) * float(benchmark_ev_ebitda)
    debt = float(total_debt) if is_finite(total_debt) else 0.0
    held_cash = float(cash) if is_finite(cash) else 0.0
    return implied_ev, implied_ev - debt + held_cash


def calculate_implied_share_price(
    implied_equity_value: Optional[float], shares_outstanding: Optional[float]
) -> Optional[float]:
    if not is_finite(implied_equity_value) or not is_finite(shares_outstanding):
        return None
    if float(shares_outstanding) <= 0:
        return None
    return float(implied_equity_value) / float(shares_outstanding)


def calculate_valuation_gap(
    implied_price: Optional[float], current_price: Optional[float]
) -> Optional[float]:
    """(Implied - Current) / Current. Positive = undervalued."""
    if not is_finite(implied_price) or not is_finite(current_price):
        return None
    if float(current_price) <= 0:
        return None
    return (float(implied_price) - float(current_price)) / float(current_price)


def calculate_valuation_score(valuation_gap: Optional[float]) -> Optional[float]:
    """V = 100 * tanh(2 * ValuationGap), clamped to [-100, +100].

    tanh keeps an extreme gap from dominating: +50% gap scores about +76,
    +25% about +46, 0% exactly 0, and the curve saturates rather than blowing up.
    """
    if not is_finite(valuation_gap):
        return None
    return clamp(
        100.0 * math.tanh(VALUATION_TANH_K * float(valuation_gap)), SCORE_MIN, SCORE_MAX
    )


def build_valuation_factor(
    target: CompanyFinancials, universe: Sequence[CompanyFinancials]
) -> tuple[FactorResult, ValuationDetail]:
    """Full valuation pipeline: peers -> cohort -> benchmark -> implied price -> V."""
    factor = FactorResult(name="valuation")
    detail = ValuationDetail(
        current_price=target.price,
        company_ebitda=target.ebitda,
        company_debt=target.total_debt,
        company_cash=target.cash,
        shares_outstanding=target.shares_outstanding,
    )

    peers, level, peer_notes = build_industry_peer_set(target, universe)
    detail.benchmark_level = level
    detail.n_industry_peers = len(peers)
    detail.notes.extend(peer_notes)

    cohort, cohort_notes = build_ebitda_peer_cohort(peers)
    detail.notes.extend(cohort_notes)

    # If the industry cohort is too thin, widen to the sector and say so.
    if len(cohort) < MIN_EBITDA_COHORT and level == INDUSTRY:
        sector_peers = [
            c for c in universe
            if c.ticker != target.ticker and target.sector and c.sector == target.sector
        ]
        wider, wider_notes = build_ebitda_peer_cohort(sector_peers)
        if len(wider) >= MIN_EBITDA_COHORT:
            detail.notes.append(
                f"Industry EBITDA cohort had only {len(cohort)} companies "
                f"(need {MIN_EBITDA_COHORT}) -- widened to the "
                f"{target.sector} sector."
            )
            detail.notes.extend(wider_notes)
            cohort, peers, level = wider, sector_peers, SECTOR_FALLBACK
            detail.benchmark_level = level
            detail.n_industry_peers = len(sector_peers)

    benchmark, rows, n_valid = calculate_peer_ev_ebitda(cohort)
    detail.cohort = rows
    detail.n_valid_peers = n_valid
    detail.benchmark_ev_ebitda = benchmark

    if benchmark is None:
        factor.notes.append(
            f"No usable EV/EBITDA benchmark could be built "
            f"({n_valid} valid cohort observations, need {MIN_EBITDA_COHORT})."
        )
        return factor, detail

    implied_ev, implied_equity = calculate_implied_equity_value(
        target.ebitda, benchmark, target.total_debt, target.cash
    )
    detail.implied_ev = implied_ev
    detail.implied_equity_value = implied_equity

    shares = target.shares_outstanding
    if not is_finite(shares) and is_finite(target.market_cap) and is_finite(target.price) \
            and float(target.price) > 0:
        shares = float(target.market_cap) / float(target.price)
        detail.shares_outstanding = shares
        detail.notes.append(
            "Shares outstanding derived as market cap / price (not reported directly)."
        )

    implied_price = calculate_implied_share_price(implied_equity, shares)
    detail.implied_price = implied_price

    gap = calculate_valuation_gap(implied_price, target.price)
    detail.valuation_gap = gap

    score = calculate_valuation_score(gap)
    if score is None:
        factor.notes.append(
            "Valuation gap could not be computed (missing EBITDA, shares or price)."
        )
        return factor, detail

    factor.score = score
    factor.status = OK
    return factor, detail


# --------------------------------------------------------------------------
# 2. GROWTH
# --------------------------------------------------------------------------

def _growth(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    """(current - previous) / |previous|.

    The absolute denominator keeps the sign meaningful when the base period is
    negative -- a loss shrinking from -100 to -50 is an improvement, and a
    signed denominator would score it as a decline.
    """
    if not is_finite(current) or not is_finite(previous):
        return None
    if float(previous) == 0:
        return None
    return (float(current) - float(previous)) / abs(float(previous))


def calculate_revenue_growth(revenue_annual: Sequence[float]) -> Optional[float]:
    """(Revenue_t - Revenue_t-1) / Revenue_t-1, from an oldest-first series."""
    if not revenue_annual or len(revenue_annual) < 2:
        return None
    return _growth(revenue_annual[-1], revenue_annual[-2])


def calculate_revenue_acceleration(revenue_annual: Sequence[float]) -> Optional[float]:
    """GrowthCurrent - GrowthPrevious. Needs three comparable periods.

    Growth falling from 30% to 15% is negative acceleration even though the
    company is still growing.
    """
    if not revenue_annual or len(revenue_annual) < 3:
        return None
    current = _growth(revenue_annual[-1], revenue_annual[-2])
    previous = _growth(revenue_annual[-2], revenue_annual[-3])
    if current is None or previous is None:
        return None
    return current - previous


def calculate_ebitda_growth(ebitda_annual: Sequence[float]) -> Optional[float]:
    if not ebitda_annual or len(ebitda_annual) < 2:
        return None
    return _growth(ebitda_annual[-1], ebitda_annual[-2])


def calculate_growth_score(
    target: CompanyFinancials, peers: Sequence[CompanyFinancials], level: str
) -> FactorResult:
    """G = 0.45RG + 0.35RA + 0.20EG, each percentile-ranked within the peer set."""
    components = {
        "revenue_growth": _normalise_component(
            "revenue_growth",
            calculate_revenue_growth(target.revenue_annual),
            [calculate_revenue_growth(p.revenue_annual) for p in peers],
            level,
        ),
        "revenue_acceleration": _normalise_component(
            "revenue_acceleration",
            calculate_revenue_acceleration(target.revenue_annual),
            [calculate_revenue_acceleration(p.revenue_annual) for p in peers],
            level,
        ),
        "ebitda_growth": _normalise_component(
            "ebitda_growth",
            calculate_ebitda_growth(target.ebitda_annual),
            [calculate_ebitda_growth(p.ebitda_annual) for p in peers],
            level,
        ),
    }
    score, weights, contributions, notes = _blend(
        components, GROWTH_WEIGHTS, COMPONENT_LABELS
    )
    return FactorResult(
        name="growth",
        score=score,
        status=OK if score is not None else UNAVAILABLE,
        components=components,
        weights_used=weights,
        contributions=contributions,
        notes=notes,
    )


# --------------------------------------------------------------------------
# 3. PROFITABILITY
# --------------------------------------------------------------------------

def calculate_ebitda_margin(
    ebitda: Optional[float], revenue: Optional[float]
) -> Optional[float]:
    if not is_finite(ebitda) or not is_finite(revenue):
        return None
    if float(revenue) <= 0:
        return None
    return float(ebitda) / float(revenue)


def calculate_roa(company: CompanyFinancials) -> Optional[float]:
    """Net income / total assets. Prefers a supplied ROA when one is valid."""
    if is_finite(company.roa_supplied):
        return float(company.roa_supplied)
    if not is_finite(company.net_income) or not is_finite(company.total_assets):
        return None
    if float(company.total_assets) <= 0:
        return None
    return float(company.net_income) / float(company.total_assets)


def calculate_profitability_score(
    target: CompanyFinancials, peers: Sequence[CompanyFinancials], level: str
) -> FactorResult:
    """P = 0.65EM + 0.35ROA, each percentile-ranked within the peer set."""
    components = {
        "ebitda_margin": _normalise_component(
            "ebitda_margin",
            calculate_ebitda_margin(target.ebitda, target.revenue),
            [calculate_ebitda_margin(p.ebitda, p.revenue) for p in peers],
            level,
        ),
        "roa": _normalise_component(
            "roa",
            calculate_roa(target),
            [calculate_roa(p) for p in peers],
            level,
        ),
    }
    score, weights, contributions, notes = _blend(
        components, PROFITABILITY_WEIGHTS, COMPONENT_LABELS
    )
    return FactorResult(
        name="profitability",
        score=score,
        status=OK if score is not None else UNAVAILABLE,
        components=components,
        weights_used=weights,
        contributions=contributions,
        notes=notes,
    )


# --------------------------------------------------------------------------
# 4. DEBT / FINANCIAL STRENGTH
# --------------------------------------------------------------------------

def calculate_net_debt(
    total_debt: Optional[float], cash: Optional[float]
) -> Optional[float]:
    if not is_finite(total_debt):
        return None
    return float(total_debt) - (float(cash) if is_finite(cash) else 0.0)


def calculate_net_debt_ebitda(
    total_debt: Optional[float], cash: Optional[float], ebitda: Optional[float]
) -> Optional[float]:
    """NetDebt / EBITDA. Negative is possible and is financially strong.

    Requires positive EBITDA: dividing by a negative or zero EBITDA inverts the
    meaning of the ratio, so it is reported unavailable instead.
    """
    net_debt = calculate_net_debt(total_debt, cash)
    if net_debt is None:
        return None
    if not is_finite(ebitda) or float(ebitda) <= 0:
        return None
    return net_debt / float(ebitda)


def calculate_debt_market_cap(company: CompanyFinancials) -> Optional[float]:
    """TotalDebt / MarketCap, where MarketCap = Price x SharesOutstanding."""
    cap = company_market_cap(company)
    if cap is None or cap <= 0:
        return None
    if not is_finite(company.total_debt) or float(company.total_debt) < 0:
        return None
    return float(company.total_debt) / cap


def calculate_debt_score(
    target: CompanyFinancials, peers: Sequence[CompanyFinancials], level: str
) -> FactorResult:
    """D = 0.60ND + 0.40DM. Both components are LOWER-IS-BETTER."""
    components = {
        "net_debt_ebitda": _normalise_component(
            "net_debt_ebitda",
            calculate_net_debt_ebitda(target.total_debt, target.cash, target.ebitda),
            [calculate_net_debt_ebitda(p.total_debt, p.cash, p.ebitda) for p in peers],
            level,
        ),
        "debt_market_cap": _normalise_component(
            "debt_market_cap",
            calculate_debt_market_cap(target),
            [calculate_debt_market_cap(p) for p in peers],
            level,
        ),
    }
    score, weights, contributions, notes = _blend(
        components, DEBT_WEIGHTS, COMPONENT_LABELS
    )
    return FactorResult(
        name="debt",
        score=score,
        status=OK if score is not None else UNAVAILABLE,
        components=components,
        weights_used=weights,
        contributions=contributions,
        notes=notes,
    )


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------

def calculate_company_raw_score(
    factors: dict[str, FactorResult]
) -> tuple[Optional[float], dict[str, float], dict[str, float], list[str]]:
    """0.40V + 0.25G + 0.20P + 0.15D, renormalised over available factors."""
    notes: list[str] = []
    available = {k: f for k, f in factors.items() if f.available}
    missing = sorted(k for k in MAJOR_WEIGHTS if k not in available)

    if not available:
        notes.append("No major factor could be calculated -- no company score.")
        return None, {}, {}, notes

    total = sum(MAJOR_WEIGHTS[k] for k in available)
    if total <= 0:
        return None, {}, {}, ["No weighted factor available."]

    used = {k: MAJOR_WEIGHTS[k] / total for k in available}
    contributions = {k: used[k] * available[k].score for k in available}

    if missing:
        notes.append(
            "Unavailable major factors excluded: "
            + ", ".join(FACTOR_LABELS[m] for m in missing)
            + f". Remaining weights renormalised over {total:.2f}."
        )
    raw = clamp(sum(contributions.values()), SCORE_MIN, SCORE_MAX)
    return raw, used, contributions, notes


def calculate_company_score(raw_score: Optional[float]) -> Optional[float]:
    """CompanyScore = 0.75 x CompanyRawScore, clamped to [-75, +75]."""
    if not is_finite(raw_score):
        return None
    return clamp(COMPANY_SCALE * float(raw_score), COMPANY_SCORE_MIN, COMPANY_SCORE_MAX)


def calculate_final_equity_score(
    company_score: Optional[float], sector_overlay: Optional[float]
) -> Optional[float]:
    """FinalEquityScore = CompanyScore + SectorOverlay, clamped to [-100, +100].

    A missing overlay contributes nothing rather than blocking the score; the
    caller is expected to say the sector environment was unavailable.
    """
    if not is_finite(company_score):
        return None
    overlay = float(sector_overlay) if is_finite(sector_overlay) else 0.0
    return clamp(float(company_score) + overlay, FINAL_MIN, FINAL_MAX)


def build_company_score(
    target: CompanyFinancials,
    universe: Sequence[CompanyFinancials],
    sector_overlay: Optional[float] = None,
) -> CompanyScoreResult:
    """Score one company end to end against its industry peers."""
    result = CompanyScoreResult(ticker=target.ticker, sector_overlay=sector_overlay)

    peers, level, peer_notes = build_industry_peer_set(target, universe)
    result.benchmark_level = level
    result.notes.extend(peer_notes)

    valuation, detail = build_valuation_factor(target, universe)
    result.valuation = detail

    result.factors = {
        "valuation": valuation,
        "growth": calculate_growth_score(target, peers, level),
        "profitability": calculate_profitability_score(target, peers, level),
        "debt": calculate_debt_score(target, peers, level),
    }

    raw, weights, contributions, notes = calculate_company_raw_score(result.factors)
    result.raw_score = raw
    result.weights_used = weights
    result.contributions = contributions
    result.notes.extend(notes)

    result.company_score = calculate_company_score(raw)
    result.final_score = calculate_final_equity_score(result.company_score, sector_overlay)

    if result.company_score is not None and sector_overlay is None:
        result.notes.append(
            "No sector overlay was available; the final score equals the "
            "company score alone."
        )
    return result


COMPANY_MODEL_DETAILS = """\
**Master equation**

    CompanyRawScore = 0.40V + 0.25G + 0.20P + 0.15D

    G = 0.45RG + 0.35RA + 0.20EG
    P = 0.65EM + 0.35ROA
    D = 0.60ND + 0.40DM      (both components LOWER is better)

    CompanyScore     = 0.75 x CompanyRawScore       clamped [-75, +75]
    FinalEquityScore = CompanyScore + SectorOverlay  clamped [-100, +100]

Fully expanded:

    CompanyRawScore = 0.40V + 0.1125RG + 0.0875RA + 0.05EG
                    + 0.13EM + 0.07ROA + 0.09ND + 0.06DM

**Valuation (40%)**

    peers        = same Yahoo industry (sector fallback if too thin)
    cohort       = peers whose EBITDA sits in the 50th-75th percentile
    EV_i         = MarketCap_i + TotalDebt_i - Cash_i
    Benchmark    = winsorized mean of cohort EV_i / EBITDA_i

    ImpliedEV          = CompanyEBITDA x Benchmark
    ImpliedEquityValue = ImpliedEV - CompanyDebt + CompanyCash
    ImpliedSharePrice  = ImpliedEquityValue / SharesOutstanding
    ValuationGap       = (ImpliedSharePrice - CurrentPrice) / CurrentPrice

    V = 100 x tanh(2 x ValuationGap)

Positive gap means the company looks undervalued against the cohort.

**Growth, Profitability, Debt**

Every component is percentile-ranked against companies in the SAME INDUSTRY
(average ranks for ties), then

    higher-is-better:  Score = 200p - 100
    lower-is-better:   Score = 100 - 200p

**Missing data**

Never a silent zero. A missing component is dropped and the remaining weights
inside that category are renormalised; a missing major factor is dropped and
V/G/P/D are renormalised across what is left. Everything dropped is named
on screen.
"""
