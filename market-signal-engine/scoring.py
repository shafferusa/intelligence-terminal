"""
MARKET SIGNAL ENGINE -- scoring engine.

Pure-stdlib quantitative scoring. This module deliberately has NO third-party
dependencies (no pandas / numpy / streamlit / yfinance) so it can be dropped
straight into another project.

V1 equity model
---------------
    L = -50 * ln( (Debt/Revenue)_company / (Debt/Revenue)_sector )
    V = -50 * ln( (Forward P/E)_company / (Forward P/E)_sector )

    EquityScore = 0.55*L + 0.45*V        clamped to [-100, +100]

Interpretation
--------------
    +40 .. +100  BULLISH
      0 .. +40   SEMI-BULLISH
    -40 ..   0   SEMI-BEARISH
   -100 .. -40   BEARISH
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

SCORE_MIN = -100.0
SCORE_MAX = 100.0

#: Scaling constant in the log formulas. A company at 1/e of the sector ratio
#: scores +50; a company at e times the sector ratio scores -50.
LOG_SCALE = 50.0

#: V1 factor weights. Must sum to 1.0.
FACTOR_WEIGHTS: dict[str, float] = {
    "leverage": 0.55,
    "valuation": 0.45,
}

#: The intended V2 shape. Not active yet -- kept here so the expansion path is
#: obvious and so `build_equity_score` never has to be restructured to get
#: there: it already consumes an arbitrary list of FactorResult objects.
FUTURE_FACTOR_WEIGHTS: dict[str, float] = {
    "valuation": 0.25,
    "leverage": 0.20,
    "growth": 0.20,
    "quality": 0.20,
    "momentum": 0.15,
}

#: Score assigned to valuation when forward P/E is unusable (negative or
#: unavailable expected earnings). A deliberate, disclosed penalty -- replace
#: with an EV/Sales or Price/Sales ladder when those inputs are wired up.
VALUATION_FALLBACK_SCORE = -50.0

#: Fewer peers than this and the sector benchmark is flagged low-confidence.
MIN_PEERS_FOR_CONFIDENCE = 5

#: A forward P/E outside this band is treated as junk data, not a valuation.
MIN_VALID_PE = 0.5
MAX_VALID_PE = 500.0

BULLISH = "BULLISH"
SEMI_BULLISH = "SEMI-BULLISH"
SEMI_BEARISH = "SEMI-BEARISH"
BEARISH = "BEARISH"

# Factor status values.
OK = "ok"                    # computed from real data on both sides
FALLBACK = "fallback"        # scored by a disclosed rule, not by the formula
UNAVAILABLE = "unavailable"  # cannot be scored; dropped from the weighted sum


# --------------------------------------------------------------------------
# Result containers
# --------------------------------------------------------------------------

@dataclass
class FactorResult:
    """One scored factor (leverage, valuation, and later growth/quality/...)."""

    name: str
    score: Optional[float]
    status: str
    company_value: Optional[float] = None
    sector_value: Optional[float] = None
    notes: list[str] = field(default_factory=list)

    @property
    def counts_toward_score(self) -> bool:
        return self.status in (OK, FALLBACK) and self.score is not None


@dataclass
class EquityScore:
    """Final model output."""

    score: Optional[float]
    label: Optional[str]
    factors: dict[str, FactorResult]
    weights_used: dict[str, float]
    notes: list[str] = field(default_factory=list)
    calculation_text: str = ""


@dataclass
class SectorBenchmark:
    """Median sector statistics plus the evidence behind them."""

    group_label: str                    # e.g. "Semiconductors" or "Technology"
    grouping: str                       # "industry" or "sector"
    debt_revenue_median: Optional[float]
    forward_pe_median: Optional[float]
    debt_revenue_n: int
    forward_pe_n: int
    peers: list[dict]                   # rows shown in the PEERS USED table
    notes: list[str] = field(default_factory=list)

    @property
    def low_confidence(self) -> bool:
        return (
            self.debt_revenue_n < MIN_PEERS_FOR_CONFIDENCE
            or self.forward_pe_n < MIN_PEERS_FOR_CONFIDENCE
        )


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def clamp(value: float, low: float = SCORE_MIN, high: float = SCORE_MAX) -> float:
    return max(low, min(high, value))


def _is_finite_number(value) -> bool:
    if value is None or isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def is_valid_forward_pe(value) -> bool:
    """A forward P/E worth feeding to a logarithm.

    Rejects None, non-numeric, NaN/inf, zero, negatives (negative expected
    earnings) and absurd outliers that would otherwise poison a median.
    """
    if not _is_finite_number(value):
        return False
    return MIN_VALID_PE <= float(value) <= MAX_VALID_PE


def median_of(values: Iterable) -> tuple[Optional[float], int]:
    """Median of the finite, non-negative values. Returns (median, n_used).

    Median rather than mean, so one extreme company cannot move the benchmark.
    """
    cleaned = [float(v) for v in values if _is_finite_number(v) and float(v) >= 0.0]
    if not cleaned:
        return None, 0
    return statistics.median(cleaned), len(cleaned)


def median_forward_pe(values: Iterable) -> tuple[Optional[float], int]:
    """Median forward P/E, excluding missing, zero, negative and junk values."""
    cleaned = [float(v) for v in values if is_valid_forward_pe(v)]
    if not cleaned:
        return None, 0
    return statistics.median(cleaned), len(cleaned)


# --------------------------------------------------------------------------
# Factor 1 -- leverage
# --------------------------------------------------------------------------

def calculate_debt_revenue(total_debt, revenue) -> tuple[Optional[float], list[str]]:
    """Total Debt / Annual Revenue.

    Returns (ratio, notes). Ratio is None when it cannot be computed honestly;
    it is never guessed at.
    """
    notes: list[str] = []

    if not _is_finite_number(total_debt) or float(total_debt) < 0:
        notes.append("Total debt unavailable.")
        return None, notes

    if not _is_finite_number(revenue) or float(revenue) <= 0:
        notes.append("Revenue unavailable or zero -- Debt/Revenue cannot be computed.")
        return None, notes

    return float(total_debt) / float(revenue), notes


def calculate_leverage_score(company_ratio, sector_ratio) -> FactorResult:
    """L = -50 * ln(company / sector), clamped to [-100, +100].

    Lower leverage than the sector is bullish (positive); higher is bearish.
    Zero company debt is treated as the extreme-low-leverage case and pinned
    to +100 rather than being sent to a logarithm of zero.
    """
    notes: list[str] = []

    if company_ratio is None:
        notes.append("Company Debt/Revenue unavailable -- leverage not scored.")
        return FactorResult("leverage", None, UNAVAILABLE, None, sector_ratio, notes)

    if sector_ratio is None or sector_ratio <= 0:
        notes.append("Sector median Debt/Revenue unavailable -- leverage not scored.")
        return FactorResult("leverage", None, UNAVAILABLE, company_ratio, None, notes)

    if company_ratio == 0:
        notes.append(
            "Company carries no total debt -- treated as extreme low leverage "
            "and capped at +100."
        )
        return FactorResult("leverage", SCORE_MAX, OK, 0.0, sector_ratio, notes)

    raw = -LOG_SCALE * math.log(company_ratio / sector_ratio)
    score = clamp(raw)
    if score != raw:
        notes.append(f"Raw leverage score {raw:+.1f} clamped to {score:+.1f}.")

    return FactorResult("leverage", score, OK, company_ratio, sector_ratio, notes)


# --------------------------------------------------------------------------
# Factor 2 -- valuation
# --------------------------------------------------------------------------

def calculate_valuation_score(company_pe, sector_pe) -> FactorResult:
    """V = -50 * ln(company / sector), clamped to [-100, +100].

    Cheaper than the sector is bullish (positive); richer is bearish.

    When the company's forward P/E is unavailable, zero, negative or otherwise
    meaningless, V1 assigns the disclosed VALUATION_FALLBACK_SCORE instead of
    taking a logarithm. Swap this branch for EV/Sales or Price/Sales when those
    inputs land -- nothing else in the module needs to change.
    """
    notes: list[str] = []

    if not is_valid_forward_pe(company_pe):
        notes.append(
            "Forward P/E unavailable/invalid due to negative or unavailable "
            "expected earnings."
        )
        notes.append(
            f"V1 assigns a fixed valuation score of {VALUATION_FALLBACK_SCORE:+.1f} "
            "in this case (placeholder for a future EV/Sales or Price/Sales test)."
        )
        pe_value = float(company_pe) if _is_finite_number(company_pe) else None
        return FactorResult(
            "valuation", VALUATION_FALLBACK_SCORE, FALLBACK, pe_value, sector_pe, notes
        )

    if sector_pe is None or sector_pe <= 0:
        notes.append("Sector median forward P/E unavailable -- valuation not scored.")
        return FactorResult(
            "valuation", None, UNAVAILABLE, float(company_pe), None, notes
        )

    raw = -LOG_SCALE * math.log(float(company_pe) / float(sector_pe))
    score = clamp(raw)
    if score != raw:
        notes.append(f"Raw valuation score {raw:+.1f} clamped to {score:+.1f}.")

    return FactorResult(
        "valuation", score, OK, float(company_pe), float(sector_pe), notes
    )


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------

def calculate_equity_score(leverage_score, valuation_score) -> Optional[float]:
    """The bare V1 arithmetic: 0.55L + 0.45V, clamped.

    Kept as a standalone function so the formula can be checked in isolation.
    `build_equity_score` is what the app calls.
    """
    if leverage_score is None and valuation_score is None:
        return None
    if leverage_score is None:
        return clamp(float(valuation_score))
    if valuation_score is None:
        return clamp(float(leverage_score))
    combined = (
        FACTOR_WEIGHTS["leverage"] * float(leverage_score)
        + FACTOR_WEIGHTS["valuation"] * float(valuation_score)
    )
    return clamp(combined)


def build_equity_score(factors: Sequence[FactorResult]) -> EquityScore:
    """Combine scored factors into the final EquityScore.

    Factors whose status is UNAVAILABLE are dropped and the remaining weights
    are renormalised, so a missing input degrades the model instead of silently
    contributing a zero. Adding growth/quality/momentum later means appending
    FactorResults here and widening FACTOR_WEIGHTS -- no other change.
    """
    by_name = {f.name: f for f in factors}
    usable = [f for f in factors if f.counts_toward_score]
    notes: list[str] = []

    if not usable:
        notes.append("No factor could be scored -- house score unavailable.")
        return EquityScore(None, None, by_name, {}, notes, "")

    weight_total = sum(FACTOR_WEIGHTS.get(f.name, 0.0) for f in usable)
    if weight_total <= 0:
        notes.append("No weighted factor available -- house score unavailable.")
        return EquityScore(None, None, by_name, {}, notes, "")

    weights_used = {
        f.name: FACTOR_WEIGHTS.get(f.name, 0.0) / weight_total for f in usable
    }

    dropped = [f.name for f in factors if not f.counts_toward_score]
    if dropped:
        notes.append(
            "Dropped from the weighted average (no data): "
            + ", ".join(sorted(dropped))
            + ". Remaining weights renormalised to 100%."
        )

    raw = sum(weights_used[f.name] * f.score for f in usable)
    score = clamp(raw)
    label = classify_score(score)

    terms = " + ".join(
        f"{weights_used[f.name]:.2f}({f.score:+.1f})" for f in usable
    )
    calculation_text = f"{terms}\n\n= {score:+.1f}\n\n{label}"

    return EquityScore(score, label, by_name, weights_used, notes, calculation_text)


def classify_score(score) -> Optional[str]:
    """Map a -100..+100 score onto the four-band label."""
    if not _is_finite_number(score):
        return None
    value = float(score)
    if value >= 40:
        return BULLISH
    if value >= 0:
        return SEMI_BULLISH
    if value >= -40:
        return SEMI_BEARISH
    return BEARISH


# --------------------------------------------------------------------------
# Deterministic explanation (no LLM)
# --------------------------------------------------------------------------

def _magnitude_word(score: float) -> str:
    magnitude = abs(score)
    if magnitude >= 40:
        return "substantially"
    if magnitude >= 15:
        return "moderately"
    if magnitude >= 3:
        return "modestly"
    return "marginally"


def explain_score(
    ticker: str,
    result: EquityScore,
    group_label: Optional[str] = None,
    benchmark: Optional[SectorBenchmark] = None,
) -> str:
    """Build the written verdict straight from the numbers. Fully deterministic."""
    if result.score is None or result.label is None:
        return (
            f"{ticker} could not be scored: neither the leverage nor the "
            f"valuation factor had usable data."
        )

    peer_group = group_label or "sector"
    sentences = [
        f"{ticker} receives a {result.label.title()} score of {result.score:+.1f}."
    ]

    leverage = result.factors.get("leverage")
    if leverage is not None and leverage.status == OK and leverage.score is not None:
        if leverage.company_value == 0:
            sentences.append(
                f"It carries no total debt at all, the strongest possible reading "
                f"against the {peer_group} peer median, contributing "
                f"{leverage.score:+.1f} to the model."
            )
        else:
            direction = "lower" if leverage.score > 0 else "higher"
            effect = "positively" if leverage.score > 0 else "negatively"
            sentences.append(
                f"Its balance-sheet leverage of "
                f"{leverage.company_value * 100:.1f}% debt-to-revenue is "
                f"{_magnitude_word(leverage.score)} {direction} than the "
                f"{peer_group} peer median of "
                f"{leverage.sector_value * 100:.1f}%, contributing "
                f"{effect} to the model ({leverage.score:+.1f})."
            )
    elif leverage is not None:
        sentences.append(
            "Leverage could not be scored, so the house score rests on "
            "valuation alone."
        )

    valuation = result.factors.get("valuation")
    if valuation is not None and valuation.status == OK and valuation.score is not None:
        direction = "below" if valuation.score > 0 else "above"
        effect = "a positive" if valuation.score > 0 else "a negative"
        connector = (
            "Meanwhile,"
            if leverage is not None
            and leverage.score is not None
            and valuation.score is not None
            and (leverage.score > 0) == (valuation.score > 0)
            else "However,"
        )
        sentences.append(
            f"{connector} its forward P/E of {valuation.company_value:.1f}x is "
            f"{_magnitude_word(valuation.score)} {direction} the peer median of "
            f"{valuation.sector_value:.1f}x, creating {effect} valuation "
            f"contribution ({valuation.score:+.1f})."
        )
    elif valuation is not None and valuation.status == FALLBACK:
        sentences.append(
            f"Its forward P/E is unavailable or invalid (negative or missing "
            f"expected earnings), so V1 applies a fixed "
            f"{VALUATION_FALLBACK_SCORE:+.1f} valuation penalty rather than a "
            f"measured comparison -- treat the valuation leg as unmeasured, "
            f"not as evidence."
        )
    elif valuation is not None:
        sentences.append(
            "Valuation could not be scored, so the house score rests on "
            "leverage alone."
        )

    if benchmark is not None and benchmark.low_confidence:
        sentences.append(
            f"Benchmark confidence is LOW: only {benchmark.forward_pe_n} peers "
            f"supplied a usable forward P/E and {benchmark.debt_revenue_n} "
            f"supplied a usable Debt/Revenue."
        )

    return " ".join(sentences)


# --------------------------------------------------------------------------
# Text used by the "How is this calculated?" panel
# --------------------------------------------------------------------------

MODEL_DETAILS = """\
**Leverage Score**

    L = -50 x ln( (Debt/Revenue company) / (Debt/Revenue sector) )

**Valuation Score**

    V = -50 x ln( (Forward P/E company) / (Forward P/E sector) )

**Final**

    Equity Score = 0.55L + 0.45V

**Score interpretation**

    +40 to +100  = Bullish
      0 to +40   = Semi-Bullish
    -40 to 0     = Semi-Bearish
    -100 to -40  = Bearish

**Conventions**

- Both factor scores and the final score are clamped to [-100, +100].
- Sector benchmarks use the MEDIAN of the peer group, never the mean, so a
  single extreme company cannot move the benchmark.
- Peers with missing, zero, negative or absurd forward P/E values are excluded
  from the P/E median.
- Zero company debt is pinned to a +100 leverage score rather than sent to
  ln(0).
- An unusable forward P/E scores a disclosed -50 instead of being guessed at.
- A factor with no data is dropped and the remaining weights are renormalised.
"""
