"""
ShafferFinEval -- sector scoring engine.

Pure-stdlib math. No Streamlit, no network, no market-data imports: every
function takes structured data in and returns structured data out, so the whole
engine can be unit-tested offline and lifted into another project.

Model
-----
Four factors, each normalised to [-100, +100] by PERCENTILE RANK ACROSS
SECTORS (never by raw magnitude, so one extreme observation cannot dominate):

    G    Growth acceleration vs VTI   40%   higher is better
    ROE  Winsorized mean ROE          21%   higher is better
    ROA  Winsorized mean ROA          14%   higher is better
    D    Winsorized mean Debt/MktCap  25%   LOWER is better

    SectorRawScore = 0.40G + 0.21ROE + 0.14ROA + 0.25D    clamped [-100, +100]
    SectorOverlay  = SectorRawScore / 4                   clamped [ -25,  +25]

`SectorOverlay` is the only number the company model will consume. The company
formula itself is NOT touched here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

# Shared primitives. Re-exported so this module's public surface is unchanged.
from statlib import (  # noqa: F401
    MIN_WINSOR_TRIM,
    WINSOR_LOWER,
    WINSOR_UPPER,
    average_ranks,
    clamp,
    is_finite as _finite,
    percentile,
    winsorize,
    winsorized_mean,
)

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

RAW_SCORE_MIN, RAW_SCORE_MAX = -100.0, 100.0
OVERLAY_DIVISOR = 4.0
OVERLAY_MIN, OVERLAY_MAX = -25.0, 25.0

SECTOR_FACTOR_WEIGHTS: dict[str, float] = {
    "growth": 0.40,
    "roe": 0.21,
    "roa": 0.14,
    "debt_market_cap": 0.25,
}

#: True where a HIGHER raw sector value should score higher.
FACTOR_HIGHER_IS_BETTER: dict[str, bool] = {
    "growth": True,
    "roe": True,
    "roa": True,
    "debt_market_cap": False,   # lower leverage is better
}

FACTOR_LABELS: dict[str, str] = {
    "growth": "Growth acceleration vs VTI",
    "roe": "Mean ROE",
    "roa": "Mean ROA",
    "debt_market_cap": "Debt / Market Cap",
}

#: ~3 trading months, and the total history needed for two of them.
RECENT_WINDOW = 63
LOOKBACK_WINDOW = 126
MIN_PRICE_HISTORY = LOOKBACK_WINDOW + 1      # need t, t-63 and t-126

#: Winsorization bounds and tail-trim policy live in statlib (re-exported
#: above) so the sector and company models clip identically.

#: Minimum usable companies before a factor is trustworthy.
MIN_COMPANIES_PER_FACTOR = 5

#: Below this, the whole sector is reported as unscoreable.
MIN_ELIGIBLE_COMPANIES = 3

#: Fewer available factors than this and the score is withheld.
MIN_FACTORS_FOR_SCORE = 2

HIGH, MEDIUM, LOW = "HIGH", "MEDIUM", "LOW"

OK = "ok"
UNAVAILABLE = "unavailable"


# --------------------------------------------------------------------------
# Inputs / outputs
# --------------------------------------------------------------------------

@dataclass
class CompanyObservation:
    """Raw per-company inputs. Built by the data adapter; math happens here."""

    ticker: str
    name: Optional[str] = None
    sector: Optional[str] = None
    prices: Optional[list[float]] = None          # adjusted closes, oldest first
    market_cap: Optional[float] = None
    total_debt: Optional[float] = None
    net_income: Optional[float] = None            # trailing twelve months
    shareholders_equity: Optional[float] = None   # latest balance sheet
    total_assets: Optional[float] = None          # latest balance sheet
    price: Optional[float] = None
    shares_outstanding: Optional[float] = None
    roe_supplied: Optional[float] = None          # Yahoo's own ROE, if ever present
    roa_supplied: Optional[float] = None


@dataclass
class SectorFactor:
    """One factor for one sector: its raw value, its cross-sector score."""

    name: str
    raw_value: Optional[float] = None
    score: Optional[float] = None
    status: str = UNAVAILABLE
    n_used: int = 0
    n_eligible: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def coverage(self) -> Optional[float]:
        if not self.n_eligible:
            return None
        return self.n_used / self.n_eligible

    @property
    def available(self) -> bool:
        return self.status == OK and self.score is not None


@dataclass
class SectorScore:
    """Everything known about one sector."""

    sector: str
    raw_score: Optional[float] = None
    overlay: Optional[float] = None
    factors: dict[str, SectorFactor] = field(default_factory=dict)
    weights_used: dict[str, float] = field(default_factory=dict)
    contributions: dict[str, float] = field(default_factory=dict)
    n_eligible: int = 0
    confidence: str = LOW
    notes: list[str] = field(default_factory=list)
    # Growth-factor detail, surfaced in the UI.
    sector_recent_return: Optional[float] = None
    sector_previous_return: Optional[float] = None
    vti_recent_return: Optional[float] = None
    vti_previous_return: Optional[float] = None
    sector_acceleration: Optional[float] = None
    vti_acceleration: Optional[float] = None

    @property
    def scored(self) -> bool:
        return self.raw_score is not None


# --------------------------------------------------------------------------
# Cross-sector normalisation
# --------------------------------------------------------------------------

def percentile_factor_scores(
    values: dict[str, float], higher_is_better: bool = True
) -> dict[str, float]:
    """Map raw sector values onto [-100, +100] by cross-sector percentile rank.

        p = (rank - 1) / (N - 1)        ascending rank, average ranks for ties

        higher_is_better  ->  200p - 100     (lowest -100, highest +100)
        lower is better   ->  100 - 200p     (lowest +100, highest -100)

    With a single sector there is no spread to rank against, so it scores 0.
    """
    keys = [k for k, v in values.items() if _finite(v)]
    if not keys:
        return {}
    if len(keys) == 1:
        return {keys[0]: 0.0}

    ordered = [float(values[k]) for k in keys]
    ranks = average_ranks(ordered)
    n = len(keys)

    out: dict[str, float] = {}
    for key, rank in zip(keys, ranks):
        p = (rank - 1) / (n - 1)
        score = (200 * p - 100) if higher_is_better else (100 - 200 * p)
        out[key] = clamp(score, RAW_SCORE_MIN, RAW_SCORE_MAX)
    return out


# --------------------------------------------------------------------------
# Factor 1 -- growth acceleration vs VTI
# --------------------------------------------------------------------------

def calculate_returns(
    prices: Optional[Sequence[float]],
    recent_window: int = RECENT_WINDOW,
    lookback_window: int = LOOKBACK_WINDOW,
) -> tuple[Optional[float], Optional[float]]:
    """Recent and previous ~3-month returns from an adjusted-close series.

        recent   = P_t      / P_(t-63)  - 1
        previous = P_(t-63) / P_(t-126) - 1

    Returns (None, None) when history is too short -- never a substituted value.
    """
    if not prices:
        return None, None
    series = [float(p) for p in prices if _finite(p) and float(p) > 0]
    if len(series) < lookback_window + 1:
        return None, None

    t = len(series) - 1
    p_now = series[t]
    p_mid = series[t - recent_window]
    p_old = series[t - lookback_window]
    if p_mid <= 0 or p_old <= 0:
        return None, None

    return (p_now / p_mid) - 1.0, (p_mid / p_old) - 1.0


def calculate_sector_returns(
    companies: Sequence[CompanyObservation],
) -> tuple[Optional[float], Optional[float], int]:
    """Market-cap-weighted sector returns.

    Companies without enough price history, or without a positive market cap,
    are excluded and the weights are renormalised across the remainder.
    Returns (recent, previous, n_used).
    """
    usable: list[tuple[float, float, float]] = []    # (mcap, recent, previous)
    for company in companies:
        if not _finite(company.market_cap) or float(company.market_cap) <= 0:
            continue
        recent, previous = calculate_returns(company.prices)
        if recent is None or previous is None:
            continue
        usable.append((float(company.market_cap), recent, previous))

    if not usable:
        return None, None, 0

    total_cap = sum(row[0] for row in usable)
    if total_cap <= 0:
        return None, None, 0

    recent = sum((cap / total_cap) * r for cap, r, _ in usable)
    previous = sum((cap / total_cap) * p for cap, _, p in usable)
    return recent, previous, len(usable)


def calculate_growth_acceleration(
    sector_recent: Optional[float],
    sector_previous: Optional[float],
    vti_recent: Optional[float],
    vti_previous: Optional[float],
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Sector acceleration minus VTI acceleration.

    Returns (growth_acceleration_raw, sector_acceleration, vti_acceleration).
    Positive means the sector is accelerating relative to the total US market.
    """
    if sector_recent is None or sector_previous is None:
        return None, None, None
    sector_acceleration = sector_recent - sector_previous

    if vti_recent is None or vti_previous is None:
        return None, sector_acceleration, None
    vti_acceleration = vti_recent - vti_previous

    return sector_acceleration - vti_acceleration, sector_acceleration, vti_acceleration


# --------------------------------------------------------------------------
# Factors 2-4 -- accounting ratios
# --------------------------------------------------------------------------

def company_roe(company: CompanyObservation) -> Optional[float]:
    """Net income / shareholders' equity.

    Prefers a valid ROE supplied by the data source; falls back to the
    statement fields. Negative or zero equity makes the ratio meaningless
    (a negative denominator flips the sign), so it is rejected outright.
    """
    if _finite(company.roe_supplied):
        return float(company.roe_supplied)
    if not _finite(company.net_income) or not _finite(company.shareholders_equity):
        return None
    if float(company.shareholders_equity) <= 0:
        return None
    return float(company.net_income) / float(company.shareholders_equity)


def company_roa(company: CompanyObservation) -> Optional[float]:
    """Net income / total assets."""
    if _finite(company.roa_supplied):
        return float(company.roa_supplied)
    if not _finite(company.net_income) or not _finite(company.total_assets):
        return None
    if float(company.total_assets) <= 0:
        return None
    return float(company.net_income) / float(company.total_assets)


def company_market_cap(company: CompanyObservation) -> Optional[float]:
    """Market cap: Yahoo's figure when valid, else price x shares outstanding."""
    if _finite(company.market_cap) and float(company.market_cap) > 0:
        return float(company.market_cap)
    if (
        _finite(company.price)
        and _finite(company.shares_outstanding)
        and float(company.price) > 0
        and float(company.shares_outstanding) > 0
    ):
        return float(company.price) * float(company.shares_outstanding)
    return None


def company_debt_market_cap(company: CompanyObservation) -> Optional[float]:
    """Total debt / market cap. Rejects negative debt and non-positive caps."""
    cap = company_market_cap(company)
    if cap is None or cap <= 0:
        return None
    if not _finite(company.total_debt) or float(company.total_debt) < 0:
        return None
    return float(company.total_debt) / cap


def _ratio_factor(
    name: str,
    companies: Sequence[CompanyObservation],
    extractor,
    n_eligible: int,
) -> SectorFactor:
    """Shared pipeline: collect -> winsorize -> mean, with coverage tracking."""
    observations = [v for v in (extractor(c) for c in companies) if _finite(v)]
    factor = SectorFactor(name=name, n_eligible=n_eligible)

    if len(observations) < MIN_COMPANIES_PER_FACTOR:
        factor.notes.append(
            f"Only {len(observations)} usable observations "
            f"(need {MIN_COMPANIES_PER_FACTOR}) -- factor unavailable."
        )
        factor.n_used = len(observations)
        return factor

    mean, n_used = winsorized_mean(observations)
    factor.raw_value = mean
    factor.n_used = n_used
    factor.status = OK if mean is not None else UNAVAILABLE
    factor.notes.append(
        f"Winsorized at the {int(WINSOR_LOWER * 100)}th/{int(WINSOR_UPPER * 100)}th "
        f"percentile across {n_used} companies."
    )
    return factor


def calculate_sector_roe(companies, n_eligible: int) -> SectorFactor:
    return _ratio_factor("roe", companies, company_roe, n_eligible)


def calculate_sector_roa(companies, n_eligible: int) -> SectorFactor:
    return _ratio_factor("roa", companies, company_roa, n_eligible)


def calculate_sector_debt_market_cap(companies, n_eligible: int) -> SectorFactor:
    return _ratio_factor(
        "debt_market_cap", companies, company_debt_market_cap, n_eligible
    )


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------

def calculate_sector_raw_score(
    factors: dict[str, SectorFactor]
) -> tuple[Optional[float], dict[str, float], dict[str, float], list[str]]:
    """Weighted blend of the available factor scores.

    Unavailable factors are dropped and the remaining weights are renormalised
    proportionally -- a missing factor never contributes a silent zero.
    Returns (raw_score, weights_used, contributions, notes).
    """
    notes: list[str] = []
    available = {k: f for k, f in factors.items() if f.available}
    missing = sorted(k for k in factors if k not in available)

    if len(available) < MIN_FACTORS_FOR_SCORE:
        notes.append(
            f"Only {len(available)} of {len(SECTOR_FACTOR_WEIGHTS)} factors could be "
            f"calculated (need {MIN_FACTORS_FOR_SCORE}) -- no sector score."
        )
        return None, {}, {}, notes

    weight_total = sum(SECTOR_FACTOR_WEIGHTS[k] for k in available)
    if weight_total <= 0:
        notes.append("No weighted factor available -- no sector score.")
        return None, {}, {}, notes

    weights_used = {k: SECTOR_FACTOR_WEIGHTS[k] / weight_total for k in available}
    contributions = {k: weights_used[k] * available[k].score for k in available}

    if missing:
        notes.append(
            "Unavailable and excluded: "
            + ", ".join(FACTOR_LABELS.get(m, m) for m in missing)
            + f". Remaining weights renormalised over {weight_total:.2f}."
        )

    raw = clamp(sum(contributions.values()), RAW_SCORE_MIN, RAW_SCORE_MAX)
    return raw, weights_used, contributions, notes


def calculate_sector_overlay(raw_score: Optional[float]) -> Optional[float]:
    """SectorRawScore / 4, clamped to [-25, +25]."""
    if not _finite(raw_score):
        return None
    return clamp(float(raw_score) / OVERLAY_DIVISOR, OVERLAY_MIN, OVERLAY_MAX)


def classify_confidence(score: SectorScore) -> str:
    """Informational only -- never modifies the directional score."""
    if not score.scored:
        return LOW

    coverages = [
        f.coverage for f in score.factors.values() if f.available and f.coverage is not None
    ]
    if not coverages:
        return LOW
    worst = min(coverages)
    n_available = sum(1 for f in score.factors.values() if f.available)

    if n_available == len(SECTOR_FACTOR_WEIGHTS) and worst >= 0.80 and score.n_eligible >= 10:
        return HIGH
    if n_available >= 3 and worst >= 0.50 and score.n_eligible >= 5:
        return MEDIUM
    return LOW


# --------------------------------------------------------------------------
# Whole-market build
# --------------------------------------------------------------------------

def build_all_sector_scores(
    companies_by_sector: dict[str, Sequence[CompanyObservation]],
    vti_prices: Optional[Sequence[float]] = None,
) -> dict[str, SectorScore]:
    """Score every sector. Percentile ranks are cross-sector, so this is the
    entry point -- a single sector cannot be scored in isolation.
    """
    vti_recent, vti_previous = calculate_returns(vti_prices)
    scores: dict[str, SectorScore] = {}

    # Pass 1: raw per-sector values.
    for sector, companies in companies_by_sector.items():
        n_eligible = len(companies)
        score = SectorScore(sector=sector, n_eligible=n_eligible)
        score.vti_recent_return = vti_recent
        score.vti_previous_return = vti_previous

        if n_eligible < MIN_ELIGIBLE_COMPANIES:
            score.notes.append(
                f"Only {n_eligible} eligible companies "
                f"(need {MIN_ELIGIBLE_COMPANIES}) -- sector not scored."
            )
            score.factors = {
                k: SectorFactor(name=k, n_eligible=n_eligible)
                for k in SECTOR_FACTOR_WEIGHTS
            }
            scores[sector] = score
            continue

        recent, previous, n_growth = calculate_sector_returns(companies)
        raw_growth, sector_accel, vti_accel = calculate_growth_acceleration(
            recent, previous, vti_recent, vti_previous
        )
        score.sector_recent_return = recent
        score.sector_previous_return = previous
        score.sector_acceleration = sector_accel
        score.vti_acceleration = vti_accel

        growth = SectorFactor(name="growth", n_eligible=n_eligible, n_used=n_growth)
        if raw_growth is None:
            if vti_recent is None:
                growth.notes.append(
                    "VTI history unavailable -- growth acceleration cannot be "
                    "measured against the market."
                )
            else:
                growth.notes.append(
                    f"Only {n_growth} companies had {MIN_PRICE_HISTORY} days of "
                    f"price history -- growth unavailable."
                )
        elif n_growth < MIN_COMPANIES_PER_FACTOR:
            growth.notes.append(
                f"Only {n_growth} companies had sufficient price history "
                f"(need {MIN_COMPANIES_PER_FACTOR}) -- growth unavailable."
            )
        else:
            growth.raw_value = raw_growth
            growth.status = OK
            growth.notes.append(
                f"Market-cap weighted across {n_growth} companies with "
                f"{MIN_PRICE_HISTORY}+ days of adjusted history."
            )

        score.factors = {
            "growth": growth,
            "roe": calculate_sector_roe(companies, n_eligible),
            "roa": calculate_sector_roa(companies, n_eligible),
            "debt_market_cap": calculate_sector_debt_market_cap(companies, n_eligible),
        }
        scores[sector] = score

    # Pass 2: normalise each factor across sectors.
    for factor_name, higher_is_better in FACTOR_HIGHER_IS_BETTER.items():
        raw_values = {
            sector: s.factors[factor_name].raw_value
            for sector, s in scores.items()
            if factor_name in s.factors and s.factors[factor_name].raw_value is not None
        }
        normalised = percentile_factor_scores(raw_values, higher_is_better)
        for sector, value in normalised.items():
            scores[sector].factors[factor_name].score = value
        # A raw value that could not be ranked is not a usable factor.
        for sector, s in scores.items():
            factor = s.factors.get(factor_name)
            if factor is not None and factor.score is None:
                factor.status = UNAVAILABLE

    # Pass 3: blend, overlay, confidence.
    for score in scores.values():
        raw, weights, contributions, notes = calculate_sector_raw_score(score.factors)
        score.raw_score = raw
        score.weights_used = weights
        score.contributions = contributions
        score.notes.extend(notes)
        score.overlay = calculate_sector_overlay(raw)
        score.confidence = classify_confidence(score)

    return scores


def get_sector_score_for_ticker(
    ticker: str,
    sector: Optional[str],
    all_scores: dict[str, SectorScore],
) -> Optional[SectorScore]:
    """The sector score a given company would inherit. None if unknown."""
    if not sector:
        return None
    return all_scores.get(sector)


def sector_table_rows(all_scores: dict[str, SectorScore]) -> list[dict]:
    """Flattened all-sector view, sorted by SectorRawScore descending."""
    rows = []
    for score in all_scores.values():
        rows.append(
            {
                "sector": score.sector,
                "growth_raw": score.factors["growth"].raw_value
                if "growth" in score.factors else None,
                "growth_score": score.factors["growth"].score
                if "growth" in score.factors else None,
                "roe_raw": score.factors["roe"].raw_value if "roe" in score.factors else None,
                "roe_score": score.factors["roe"].score if "roe" in score.factors else None,
                "roa_raw": score.factors["roa"].raw_value if "roa" in score.factors else None,
                "roa_score": score.factors["roa"].score if "roa" in score.factors else None,
                "debt_raw": score.factors["debt_market_cap"].raw_value
                if "debt_market_cap" in score.factors else None,
                "debt_score": score.factors["debt_market_cap"].score
                if "debt_market_cap" in score.factors else None,
                "raw_score": score.raw_score,
                "overlay": score.overlay,
                "confidence": score.confidence,
                "n_eligible": score.n_eligible,
            }
        )
    rows.sort(key=lambda r: (r["raw_score"] is not None, r["raw_score"] or 0), reverse=True)
    return rows


SECTOR_MODEL_DETAILS = """\
**Factor normalisation (percentile rank across sectors)**

    ascending rank, average ranks for ties
    p = (rank - 1) / (N - 1)

    higher-is-better factors:   FactorScore = 200p - 100
    Debt / Market Cap (lower is better):  FactorScore = 100 - 200p

So the weakest sector scores -100, the strongest +100, and the median ~0.
Ranking is used instead of raw magnitudes so one extreme observation cannot
dominate the model.

**Factors**

    G    Growth acceleration vs VTI    40%
         sector acceleration - VTI acceleration, where acceleration is
         (recent 63-day return) - (previous 63-day return), market-cap weighted

    ROE  Winsorized mean net income / shareholders' equity    21%
    ROA  Winsorized mean net income / total assets            14%
    D    Winsorized mean total debt / market cap              25%  (lower better)

Company observations are winsorized at the 5th/95th percentile before the
sector mean is taken.

**Final**

    SectorRawScore = 0.40G + 0.21ROE + 0.14ROA + 0.25D     clamped [-100, +100]
    SectorOverlay  = SectorRawScore / 4                    clamped [ -25,  +25]

An unavailable factor is dropped and the remaining weights are renormalised
proportionally. Confidence (HIGH / MEDIUM / LOW) is informational and never
changes the directional score.
"""
