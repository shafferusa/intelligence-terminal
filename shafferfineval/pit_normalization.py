"""Typed normalisation layer for `candidate_equity_shaffer_v2`.

Production is untouched by this module. `equity_shaffer_v1` and its v1 constants
are frozen; nothing here is imported by the live scoring path. This is the
research half: where a factor's *mapping into [-100, +100]* is declared, typed,
and checked.

WHY THIS MODULE EXISTS
----------------------
A percentile normalisation erases economically meaningful structure. If S() is a
pure within-cohort rank transform, then for any strictly monotone f and any
cohort-wide constant c:

        S(x) == S(f(x))          and          S(x) == S(x - c)

That is not a tuning problem; it is an algebraic identity, and it silently
deletes work. Three cases measured in this project, each a factor somebody
designed on purpose:

(a) RealRevenueGrowth = RevenueGrowth - pi. Run through this project's own
    statlib, nominal, minus-pi and divided-by-(1+pi) produce IDENTICAL scores to
    the digit. A company growing +4% into 9% inflation -- destroying real value
    -- scores -33.333 on all three. THE DEFLATOR IS INERT.

(b) EBITDAExcess = EBITDAMargin - M_s, where M_s is the mean margin of the
    sector's 50th-75th EBITDA percentile cohort. Measured: the excess is exactly
    margin - M_s and the rank-normalised scores are identical to the digit. A
    block weighting .30 Excess + .20 Margin therefore puts 0.50 on ONE quantity,
    and the cohort benchmark -- the expensive part -- contributes nothing.

(c) EBITDAScale = PercentileRank(log1p(EBITDA)). Measured: S(EBITDA) ==
    S(log1p(EBITDA)). The log is decorative under a rank. It is also undefined
    for the 37.8% of the universe with EBITDA <= 0.

The conclusion this module is the spec for: **the way economic information is
mapped into [-100, +100] may matter almost as much as which financial variables
are chosen.** So the mapping stops being an implementation detail buried in a
scoring function and becomes first-class, declared metadata -- a
`normalization_type` on every factor, a registry that refuses a factor without
one, and a redundancy guard that would have caught all three collapses above.

WHAT IS HERE
------------
1. THE TAXONOMY. Five normalization types, each a named constant plus one
   transform function. The constant is what gets written to
   `pit_feature.norm_method`, so a stored row says how its number was made.
2. THE SCALE PARAMETERS. g and k decide where saturation begins and are
   therefore the entire behaviour of an anchored transform. Fixed, or derived
   from cohort dispersion AT THE AS-OF DATE -- never from the full history.
3. THE REDUNDANCY GUARD. Spearman rho plus a separate test for EXACT rank
   equivalence, computed pooled AND within cohort, with a named likely cause.
4. THE FACTOR REGISTRY. Declarative, seeded with the candidate v2 EBITDA family.

Every transform returns a structured `NormResult` -- score, type, anchor, scale,
availability, reason -- never a bare float. A bare float cannot tell a reader
whether -100 was a genuine worst-in-cohort or a two-company cohort with nothing
to say, and that distinction is the difference between a signal and an artefact.

Pure stdlib. Reuses statlib rather than reimplementing it, including its two
documented traps: `statlib.percentile` assumes an ALREADY SORTED sequence and
returns garbage silently otherwise (every call here sorts first), and
`percentile_rank_within` includes the target in its own population, so a
two-company cohort yields only {0.0, 1.0} -> {-100, +100} (refused here, see
MIN_RANK_PEERS).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace as _dc_replace
from typing import Any, Iterable, Mapping, Optional, Sequence

from pit_store import AVAIL_COMPLETE, AVAIL_UNAVAILABLE, REASON_NO_PEERS
from statlib import (
    average_ranks,
    clamp,
    is_finite,
    median,
    percentile,
    percentile_rank_within,
    percentile_to_score,
    winsorize,
)

__all__ = [
    "NORMALIZATION_POLICY_VERSION", "CANDIDATE_MODEL_VERSION",
    "PERCENTILE_RANK", "ZERO_ANCHORED", "BENCHMARK_ANCHORED",
    "HISTORICAL_Z", "ML_CALIBRATED", "NORMALIZATION_TYPES",
    "NormResult", "ScaleSpec", "ScaleResolution", "FactorSpec",
    "PairFinding", "RedundancyReport", "Registration",
    "NormalizationError", "RedundancyError", "PointInTimeError",
    "percentile_rank_score", "zero_anchored_score", "benchmark_anchored_score",
    "historical_z_score", "ml_calibrated_score", "normalize",
    "resolve_scale", "fixed_scale", "cohort_scale", "iqr", "mad",
    "cohort_as_of", "benchmark_margin_50_75",
    "BENCHMARK_BAND_V1", "BENCHMARK_MIN_COHORT_V1",
    "spearman_rho", "rank_equivalence", "redundancy_check",
    "candidate_v2_registry", "register_factor", "registry_spec",
    "VERDICT_REDUNDANT", "VERDICT_CORRELATED", "VERDICT_INDEPENDENT",
    "VERDICT_DEGENERATE", "MIN_EXACT_N",
]

#: This module's own policy id. It is versioned for the same reason the latency
#: policy is: a row that says how it was normalised must mean exactly one thing
#: forever. A changed curve, cutoff or default scale means a v2 constant beside
#: this one, never an edit in place.
NORMALIZATION_POLICY_VERSION = "normalization_policy_v1"

#: The model these factors belong to. Declared here rather than in pit_store
#: because the candidate is not promoted: it gets a lineage row on promotion,
#: not before. `equity_shaffer_v1` is frozen and is not touched by this module.
CANDIDATE_MODEL_VERSION = "candidate_equity_shaffer_v2"


# ==========================================================================
# (1) THE TAXONOMY
#
# The question each type answers is the point. Choosing a type is choosing
# which question the factor is allowed to ask, and that choice is economic,
# not statistical.
# ==========================================================================

#: "Where does this sit versus peers?" Rank within cohort.
#: Correct when only the ORDERING is meaningful and the units are not -- and
#: therefore WRONG wherever a level, a sign or a bar carries the information.
PERCENTILE_RANK = "percentile_rank_v1"

#: "Is this economically positive or negative?" Zero maps to zero.
#: S = 100 * tanh(x / g). Correct for real growth: 0% real growth must score 0,
#: +10% must be genuinely bullish and -10% genuinely bearish, in a cohort where
#: everyone shrank as much as in one where everyone grew.
ZERO_ANCHORED = "zero_anchored_tanh_v1"

#: "Does it clear a meaningful bar, and by how much?" Benchmark maps to zero.
#: S = 100 * tanh((x - benchmark) / k). Correct for EBITDAExcessLevel against
#: the 50-75 cohort mean margin M_s: this is the ONE construction in which the
#: benchmark survives into the score instead of cancelling out of the ranking.
BENCHMARK_ANCHORED = "benchmark_anchored_tanh_v1"

#: "Unusual versus its OWN history?" Needs a per-entity history supplied by the
#: caller, screened point-in-time. Returns UNAVAILABLE when the history is too
#: short -- a z-score from four observations is a random number with a sign.
HISTORICAL_Z = "historical_z_v1"

#: Reserved. A stub that refuses to run and says so, so that nobody can ship an
#: uncalibrated model under a label that claims calibration.
ML_CALIBRATED = "ml_calibrated_v1"

NORMALIZATION_TYPES = (
    PERCENTILE_RANK, ZERO_ANCHORED, BENCHMARK_ANCHORED, HISTORICAL_Z,
    ML_CALIBRATED,
)

#: One line per type, for a registry dump a human reads.
NORMALIZATION_QUESTION = {
    PERCENTILE_RANK: "where does this sit versus peers?",
    ZERO_ANCHORED: "is this economically positive or negative?",
    BENCHMARK_ANCHORED: "does it clear a meaningful bar, and by how much?",
    HISTORICAL_Z: "is this unusual versus its own history?",
    ML_CALIBRATED: "what does a calibrated model say? (reserved, refuses to run)",
}

#: Where an anchor -- the value that maps to a score of zero -- comes from.
ANCHOR_NONE = "none"
ANCHOR_ZERO = "zero"
ANCHOR_COHORT_RANK = "cohort_rank_position"
ANCHOR_COHORT_50_75_MEAN_MARGIN = "cohort_p50_p75_mean_margin"
ANCHOR_ENTITY_HISTORY_MEDIAN = "entity_history_median"
ANCHOR_CALLER_SUPPLIED = "caller_supplied"

#: Where a scale -- the denominator that decides where saturation begins --
#: comes from.
SCALE_FIXED = "fixed"
SCALE_COHORT_IQR = "cohort_iqr"
SCALE_COHORT_MAD = "cohort_mad"
SCALE_COHORT_IQR_THEN_MAD = "cohort_iqr_then_mad"
SCALE_NONE = "none"
COHORT_SCALE_SOURCES = (SCALE_COHORT_IQR, SCALE_COHORT_MAD, SCALE_COHORT_IQR_THEN_MAD)

#: Feature roles, matching pit_feature.feature_role. Everything in this module
#: is a research candidate until a human promotion says otherwise.
ROLE_RESEARCH = "research_candidate"
ROLE_PRODUCTION = "production"

#: Unavailability reasons. Each is DISTINCT on purpose: "the cohort was too
#: small to estimate a scale" and "the cohort was identical so the scale was
#: zero" are different facts about the data and lead to different fixes.
#: REASON_NO_PEERS comes from pit_store and is never re-declared here.
REASON_NO_VALUE = "value_not_finite"
REASON_COHORT_TOO_SMALL_RANK = "cohort_too_small_to_rank"
REASON_COHORT_TOO_SMALL_SCALE = "cohort_too_small_for_dispersion"
REASON_ZERO_DISPERSION = "zero_cohort_dispersion"
REASON_NON_FINITE_SCALE = "scale_not_finite_or_not_positive"
REASON_NO_BENCHMARK = "benchmark_unavailable"
REASON_HISTORY_TOO_SHORT = "history_too_short"
REASON_ZERO_HISTORY_DISPERSION = "zero_history_dispersion"
REASON_NOT_CALIBRATED = "no_calibrated_model_registered"


class NormalizationError(ValueError):
    """A factor or transform was declared incoherently. Raised, never returned.

    Missing DATA is a NormResult with availability 'unavailable'; a missing
    DECLARATION is a bug in the model spec and must stop the run.
    """


class RedundancyError(ValueError):
    """A factor was registered that carries no new information."""


class PointInTimeError(ValueError):
    """A scale or history was estimated from data that post-dates the as-of."""


# --------------------------------------------------------------------------
# The structured result
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class NormResult:
    """One normalised number and everything needed to defend it.

    Never a bare float. The score alone cannot distinguish a genuine
    worst-in-cohort -100 from a two-company cohort that had nothing to say, and
    `anchor`/`scale` are the two parameters that decide the entire shape of an
    anchored curve -- storing the output without them makes the row
    irreproducible.

    Invariant: `score is None` if and only if `availability == 'unavailable'`,
    and an unavailable result always carries a `reason`.
    """

    score: Optional[float]
    normalization_type: str
    availability: str = AVAIL_COMPLETE
    reason: Optional[str] = None
    raw_value: Optional[float] = None
    anchor: Optional[float] = None
    anchor_source: str = ANCHOR_NONE
    scale: Optional[float] = None
    scale_source: str = SCALE_NONE
    n_cohort: Optional[int] = None
    feature_key: Optional[str] = None
    policy_version: str = NORMALIZATION_POLICY_VERSION
    notes: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return self.availability == AVAIL_COMPLETE and self.score is not None

    def as_feature_row(self) -> dict[str, Any]:
        """The subset of pit_feature columns this layer owns.

        `norm_method` carries the taxonomy constant, which is the whole point:
        a stored feature says which question it was asked.
        """
        return {
            "feature_key": self.feature_key,
            "raw_value": self.raw_value,
            "normalized_value": self.score,
            "norm_method": self.normalization_type,
            "availability": self.availability,
            "available_flag": 1 if self.available else 0,
            "unavailable_reason": self.reason,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "normalization_type": self.normalization_type,
            "availability": self.availability,
            "reason": self.reason,
            "raw_value": self.raw_value,
            "anchor": self.anchor,
            "anchor_source": self.anchor_source,
            "scale": self.scale,
            "scale_source": self.scale_source,
            "n_cohort": self.n_cohort,
            "feature_key": self.feature_key,
            "policy_version": self.policy_version,
            "notes": list(self.notes),
        }


def _unavailable(norm_type: str, reason: str, **fields) -> NormResult:
    fields.pop("score", None)
    fields.pop("availability", None)
    return NormResult(score=None, normalization_type=norm_type,
                      availability=AVAIL_UNAVAILABLE, reason=reason, **fields)


# ==========================================================================
# (2) THE SCALE PARAMETERS
# ==========================================================================

#: Default g for a real-growth rate, in growth units (0.10 = 10 percentage
#: points of real growth). See `zero_anchored_score` for the worked curve.
DEFAULT_REAL_GROWTH_SCALE_G = 0.10

#: Default k for an excess MARGIN, in margin units (0.06 = 6 percentage points
#: of margin above the bar). See `benchmark_anchored_score` for the worked curve.
#: It is a FALLBACK: the registry prefers a cohort-derived k, because a fixed
#: margin scale that fits software does not fit a grocer.
DEFAULT_EXCESS_MARGIN_SCALE_K = 0.06

#: Below this many usable cohort observations, no dispersion is estimated.
#: An IQR interpolated between three or four points is set by whichever peer
#: happens to be extreme that quarter, and the scale IS the behaviour of the
#: transform -- a noisy k makes the factor's saturation point wander.
MIN_DISPERSION_COHORT = 8

#: Minimum PEERS (excluding the target) for a percentile rank. With n members
#: the percentile grid has spacing 1/(n-1), so the achievable scores are
#: 200/(n-1) points apart: at n=2 the only scores are -100 and +100, at n=3 they
#: are -100, 0, +100. `percentile_rank_within` counts the target as a member, so
#: 4 peers is a 5-member population and a 50-point grid -- still coarse, and
#: honestly the floor rather than a recommendation.
MIN_RANK_PEERS = 4

#: A scale at or below this is treated as zero dispersion, not as a divisor.
#: Dividing by 1e-13 does not fail; it returns +/-100 for everyone, which looks
#: like a decisive signal and is noise amplified 1e13 times.
DISPERSION_FLOOR = 1e-12

#: MAD -> IQR conversion, so a MAD fallback is on the same footing as the IQR
#: it replaces. For a normal, IQR = 1.34898*sigma and sigma = 1.4826*MAD, so
#: IQR = 1.34898 * 1.4826 * MAD = 2.0000*MAD to five figures.
MAD_TO_IQR = 2.0

#: MAD -> sigma, the standard normal-consistency constant, for HISTORICAL_Z.
MAD_TO_SIGMA = 1.4826

#: Minimum history observations for a per-entity z. Twelve quarters is three
#: years: below that the dispersion estimate is dominated by a single cycle and
#: "unusual versus its own history" means "unusual versus one business cycle".
MIN_HISTORY_OBS = 12

#: Robust z units that reach ~76 points. z = 2 (two robust sigmas from its own
#: median) scores +76.2, z = 4 scores +96.4.
HISTORICAL_Z_SATURATION = 2.0


@dataclass(frozen=True)
class ScaleSpec:
    """How the scale (g or k) is to be obtained. Declarative, not a number.

    `multiplier` scales the dispersion estimate: with source=cohort_iqr and
    multiplier=1.0, one cohort IQR above the anchor scores +76.2 and two IQRs
    +96.4. Raising the multiplier widens the linear region and delays
    saturation; it does not change the sign of anything.
    """

    source: str = SCALE_NONE
    fixed_value: Optional[float] = None
    multiplier: float = 1.0
    min_cohort: int = MIN_DISPERSION_COHORT
    winsorize_cohort: bool = False
    fallback_fixed: Optional[float] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "fixed_value": self.fixed_value,
            "multiplier": self.multiplier,
            "min_cohort": self.min_cohort,
            "winsorize_cohort": self.winsorize_cohort,
            "fallback_fixed": self.fallback_fixed,
        }


@dataclass(frozen=True)
class ScaleResolution:
    """The scale actually used, where it came from, or why there is none."""

    value: Optional[float]
    source: str
    reason: Optional[str] = None
    n_cohort: int = 0
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.value is not None and self.reason is None


def fixed_scale(value: float) -> ScaleSpec:
    """A hand-set scale. Legitimate, and the honest name for a judgement call."""
    return ScaleSpec(source=SCALE_FIXED, fixed_value=float(value))


def cohort_scale(multiplier: float = 1.0, min_cohort: int = MIN_DISPERSION_COHORT,
                 fallback_fixed: Optional[float] = None,
                 source: str = SCALE_COHORT_IQR_THEN_MAD) -> ScaleSpec:
    """A robust dispersion scale estimated from the cohort at the as-of date."""
    return ScaleSpec(source=source, multiplier=float(multiplier),
                     min_cohort=int(min_cohort), fallback_fixed=fallback_fixed)


def _finite(values: Iterable[Any]) -> list[float]:
    return [float(v) for v in values if is_finite(v)]


def _sorted_finite(values: Iterable[Any]) -> list[float]:
    """Sorted finite copy.

    THE statlib.percentile TRAP: it assumes an already-sorted sequence and
    returns a plausible wrong number, silently, if given anything else. Every
    percentile call in this module goes through a list built here.
    """
    return sorted(_finite(values))


def iqr(values: Iterable[Any]) -> Optional[float]:
    """Interquartile range of a cross-section. None when it cannot be formed."""
    ordered = _sorted_finite(values)
    if len(ordered) < 2:
        return None
    q1 = percentile(ordered, 0.25)
    q3 = percentile(ordered, 0.75)
    if q1 is None or q3 is None:
        return None
    spread = float(q3) - float(q1)
    return spread if math.isfinite(spread) else None


def mad(values: Iterable[Any]) -> Optional[float]:
    """Median absolute deviation from the median. None when it cannot be formed.

    The fallback when the middle half of a cohort is identical -- common in a
    cohort of loss-makers whose margins all round to the same figure -- but the
    tails still differ.
    """
    ordered = _sorted_finite(values)
    if len(ordered) < 2:
        return None
    centre = median(ordered)
    if centre is None:
        return None
    deviations = sorted(abs(v - float(centre)) for v in ordered)
    spread = percentile(deviations, 0.5)
    if spread is None or not math.isfinite(spread):
        return None
    return float(spread)


def cohort_as_of(values: Sequence[Any], value_dates: Sequence[str],
                 as_of_date: str) -> list[float]:
    """Screen a cohort sample for point-in-time legitimacy, loudly.

    A dispersion estimated from the cohort AT THE AS-OF DATE is point-in-time
    legitimate: every member's value was knowable that day, so the scale is
    something the strategy could have computed. A dispersion estimated from the
    FULL HISTORY is not, and it is the subtler leak of the two -- it never puts
    a future number in the numerator, it just sets the saturation point of the
    curve using the next ten years of cross-sections, so the factor knows in
    2009 how wide 2009 was going to look in hindsight.

    Raises PointInTimeError rather than dropping the offending rows, because a
    caller who passed a full history meant to pass a full history and needs to
    hear about it, not get a quietly smaller cohort.
    """
    if len(values) != len(value_dates):
        raise PointInTimeError(
            f"cohort has {len(values)} values and {len(value_dates)} dates; "
            "they must be aligned one to one")
    ahead = [d for d in value_dates if d is not None and str(d)[:10] > str(as_of_date)[:10]]
    if ahead:
        raise PointInTimeError(
            f"{len(ahead)} cohort observation(s) post-date the as-of {as_of_date} "
            f"(first: {sorted(ahead)[0]}). A scale may only be estimated from the "
            "cross-section as it stood on the day.")
    return _finite(values)


def resolve_scale(spec: ScaleSpec, cohort_values: Optional[Sequence[Any]] = None,
                  as_of_date: Optional[str] = None,
                  value_dates: Optional[Sequence[str]] = None) -> ScaleResolution:
    """Turn a ScaleSpec into the number the transform will divide by.

    Takes a CROSS-SECTION, never a time series. There is deliberately no
    `history` parameter: a dispersion estimated across dates is the leak
    described in `cohort_as_of`, and the way to make that impossible is to give
    the function nowhere to put one. Pass `value_dates` with `as_of_date` and
    the cohort is screened; pass neither and the caller is asserting the cohort
    is as-of.

    Each degenerate case gets its OWN reason rather than a division:

      - too few peers to estimate dispersion  -> cohort_too_small_for_dispersion
      - every peer identical (spread 0)       -> zero_cohort_dispersion
      - the result is non-finite or <= 0      -> scale_not_finite_or_not_positive

    Dividing by a near-zero spread does not raise; it returns +/-100 for every
    member of a cohort that had nothing to distinguish, which reads as a
    decisive signal and is noise with a large multiplier.

    A MEASURED NOTE ON THE MAD RUNG, recorded rather than papered over: with
    `statlib.percentile`'s interpolated quartiles, an IQR of exactly zero
    requires at least half the cohort to sit on one value, and that same
    majority drives the MAD to zero too. Searched over random duplicate-heavy
    cohorts of size 4-12, there is NO case where IQR == 0 and MAD > 0, so the
    IQR -> MAD rung of the ladder does not fire in practice and a flat cohort
    resolves to `zero_cohort_dispersion` or to its declared fixed fallback. The
    rung stays because that is a property of this percentile definition rather
    than of dispersion, and `SCALE_COHORT_MAD` remains directly selectable for
    a caller who wants a spread that ignores the tails entirely.
    """
    if spec.source == SCALE_FIXED:
        value = spec.fixed_value
        if not is_finite(value) or float(value) <= 0.0:
            return ScaleResolution(None, SCALE_FIXED, REASON_NON_FINITE_SCALE, 0,
                                   f"fixed scale {value!r} is not a positive finite number")
        return ScaleResolution(float(value), SCALE_FIXED, None, 0,
                               f"fixed scale {float(value):.6g}")

    if spec.source not in COHORT_SCALE_SOURCES:
        raise NormalizationError(
            f"unknown scale source {spec.source!r}; expected one of "
            f"{(SCALE_FIXED,) + COHORT_SCALE_SOURCES}")

    if cohort_values is None:
        raise NormalizationError(
            f"scale source {spec.source!r} needs a cohort; none was supplied")

    if value_dates is not None:
        if as_of_date is None:
            raise NormalizationError(
                "value_dates were supplied without an as_of_date; a point-in-time "
                "screen needs the day it is screening against")
        usable = cohort_as_of(cohort_values, value_dates, as_of_date)
    else:
        usable = _finite(cohort_values)

    n = len(usable)
    if n < spec.min_cohort:
        return ScaleResolution(
            None, spec.source, REASON_COHORT_TOO_SMALL_SCALE, n,
            f"{n} usable peers, need {spec.min_cohort} to estimate a dispersion")

    sample = winsorize(usable) if spec.winsorize_cohort else usable

    estimate: Optional[float] = None
    used = spec.source
    if spec.source in (SCALE_COHORT_IQR, SCALE_COHORT_IQR_THEN_MAD):
        estimate = iqr(sample)
        used = SCALE_COHORT_IQR
    if (estimate is None or estimate <= DISPERSION_FLOOR) and \
            spec.source in (SCALE_COHORT_MAD, SCALE_COHORT_IQR_THEN_MAD):
        robust = mad(sample)
        if robust is not None and robust > DISPERSION_FLOOR:
            estimate = robust * MAD_TO_IQR
            used = SCALE_COHORT_MAD

    if estimate is None or estimate <= DISPERSION_FLOOR:
        if spec.fallback_fixed is not None and is_finite(spec.fallback_fixed) \
                and float(spec.fallback_fixed) > 0.0:
            # A declared fallback is a judgement the registry made in advance,
            # which is different from inventing a divisor at run time.
            return ScaleResolution(
                float(spec.fallback_fixed), SCALE_FIXED, None, n,
                f"cohort dispersion was zero over {n} peers; fell back to the "
                f"declared fixed scale {float(spec.fallback_fixed):.6g}")
        return ScaleResolution(
            None, used, REASON_ZERO_DISPERSION, n,
            f"all {n} peers are effectively identical; there is no spread to scale by")

    value = float(estimate) * float(spec.multiplier)
    if not math.isfinite(value) or value <= 0.0:
        return ScaleResolution(None, used, REASON_NON_FINITE_SCALE, n,
                               f"scale resolved to {value!r}")
    return ScaleResolution(value, used, None, n,
                           f"{used} over {n} peers x {spec.multiplier:g} = {value:.6g}")


# ==========================================================================
# THE TRANSFORMS. One per taxonomy entry.
# ==========================================================================

def percentile_rank_score(value: Any, peer_values: Sequence[Any], *,
                          higher_is_better: bool = True,
                          min_peers: int = MIN_RANK_PEERS,
                          feature_key: Optional[str] = None) -> NormResult:
    """PERCENTILE_RANK -- "where does this sit versus peers?"

    Rank within cohort via statlib (`percentile_rank_within` ->
    `percentile_to_score`), which is v1's mapping exactly. Correct for
    EBITDAMarginRank, ROARank, DebtRank: ordering is the information and the
    units genuinely are not.

    WHAT IT CANNOT SEE, and this is the module's founding fact: for any
    cohort-wide constant c and any strictly monotone f,

        percentile_rank_score(x)  ==  percentile_rank_score(x - c)
        percentile_rank_score(x)  ==  percentile_rank_score(f(x))

    so a deflator, a benchmark subtraction, and a log are all invisible here.
    If the thing you are trying to express is a level, a sign or a bar, this is
    the wrong type and the work you did to compute it will be erased.

    Refuses a cohort too small to rank: `percentile_rank_within` counts the
    target as a member, so with one peer the only outputs are 0.0 and 1.0 --
    -100 and +100, a coin flip wearing a decisive number.
    """
    if not is_finite(value):
        return _unavailable(PERCENTILE_RANK, REASON_NO_VALUE, feature_key=feature_key,
                            anchor_source=ANCHOR_COHORT_RANK)
    peers = _finite(peer_values)
    if not peers:
        return _unavailable(PERCENTILE_RANK, REASON_NO_PEERS, raw_value=float(value),
                            feature_key=feature_key, n_cohort=0,
                            anchor_source=ANCHOR_COHORT_RANK)
    if len(peers) < min_peers:
        return _unavailable(
            PERCENTILE_RANK, REASON_COHORT_TOO_SMALL_RANK, raw_value=float(value),
            feature_key=feature_key, n_cohort=len(peers) + 1,
            anchor_source=ANCHOR_COHORT_RANK,
            notes=(f"{len(peers)} peers, need {min_peers}: a {len(peers) + 1}-member "
                   f"population can only produce scores {200 / len(peers):.0f} points "
                   "apart.",))
    p = percentile_rank_within(value, peers)
    score = percentile_to_score(p, higher_is_better=higher_is_better)
    if score is None:
        return _unavailable(PERCENTILE_RANK, REASON_NO_PEERS, raw_value=float(value),
                            feature_key=feature_key, n_cohort=len(peers) + 1,
                            anchor_source=ANCHOR_COHORT_RANK)
    return NormResult(
        score=float(score), normalization_type=PERCENTILE_RANK,
        raw_value=float(value), anchor=None, anchor_source=ANCHOR_COHORT_RANK,
        scale=None, scale_source=SCALE_NONE, n_cohort=len(peers) + 1,
        feature_key=feature_key,
        notes=(f"percentile {float(p):.4f} of a {len(peers) + 1}-member population; "
               f"{'higher' if higher_is_better else 'lower'} is better.",))


def zero_anchored_score(value: Any, *, scale: Optional[float] = None,
                        scale_spec: Optional[ScaleSpec] = None,
                        cohort_values: Optional[Sequence[Any]] = None,
                        as_of_date: Optional[str] = None,
                        value_dates: Optional[Sequence[str]] = None,
                        feature_key: Optional[str] = None) -> NormResult:
    """ZERO_ANCHORED -- "is this economically positive or negative?"

        S = 100 * tanh(x / g)

    Zero maps to exactly zero, the sign of the score is the sign of x, and the
    curve is odd, so -x scores exactly -S(x). That is the property a rank cannot
    have: in a cohort where every company shrank, the rank transform still hands
    out +100s.

    Correct for RealRevenueGrowth. It is the only construction in which the
    deflator survives: 8% nominal growth at 6% inflation is +2% real and scores
    +19.7 with g = 0.10, where a rank would score it against whoever else
    happened to be in the cohort.

    THE CURVE, g = 0.10 (ten percentage points of real growth):

        real growth   x/g     score
        -----------------------------
          -30%        -3.0    -99.51
          -20%        -2.0    -96.40
          -10%        -1.0    -76.16
           -5%        -0.5    -46.21
           -2%        -0.2    -19.74
            0%         0.0      0.00    <- exact, by construction
           +2%        +0.2    +19.74
           +5%        +0.5    +46.21
          +10%        +1.0    +76.16
          +20%        +2.0    +96.40
          +30%        +3.0    +99.51

    Read the table before trusting the factor: g is the whole behaviour. At
    g = 0.10 the curve is close to linear inside +/-5% and effectively saturated
    beyond +/-30%, which says "past 30% real growth we are not ranking any more".
    If that is wrong for the cohort, change g -- do not change the type.
    """
    if not is_finite(value):
        return _unavailable(ZERO_ANCHORED, REASON_NO_VALUE, feature_key=feature_key,
                            anchor=0.0, anchor_source=ANCHOR_ZERO)
    spec = scale_spec if scale_spec is not None else fixed_scale(
        scale if scale is not None else DEFAULT_REAL_GROWTH_SCALE_G)
    resolution = resolve_scale(spec, cohort_values, as_of_date, value_dates)
    if not resolution.ok:
        return _unavailable(ZERO_ANCHORED, resolution.reason or REASON_NON_FINITE_SCALE,
                            raw_value=float(value), anchor=0.0,
                            anchor_source=ANCHOR_ZERO, scale_source=resolution.source,
                            n_cohort=resolution.n_cohort, feature_key=feature_key,
                            notes=(resolution.detail,))
    g = float(resolution.value)
    score = clamp(100.0 * math.tanh(float(value) / g), -100.0, 100.0)
    return NormResult(
        score=score, normalization_type=ZERO_ANCHORED, raw_value=float(value),
        anchor=0.0, anchor_source=ANCHOR_ZERO, scale=g,
        scale_source=resolution.source, n_cohort=resolution.n_cohort or None,
        feature_key=feature_key, notes=(resolution.detail,))


def benchmark_anchored_score(value: Any, benchmark: Any, *,
                             scale: Optional[float] = None,
                             scale_spec: Optional[ScaleSpec] = None,
                             cohort_values: Optional[Sequence[Any]] = None,
                             as_of_date: Optional[str] = None,
                             value_dates: Optional[Sequence[str]] = None,
                             anchor_source: str = ANCHOR_CALLER_SUPPLIED,
                             feature_key: Optional[str] = None) -> NormResult:
    """BENCHMARK_ANCHORED -- "does it clear a meaningful bar, and by how much?"

        S = 100 * tanh((x - benchmark) / k)

    The benchmark maps to exactly zero, so parity scores 0.0 and the sign of the
    score is "above the bar" or "below it". Correct for EBITDAExcessLevel
    against M_s, the mean margin of the sector's 50-75 EBITDA percentile cohort.

    THIS IS THE TYPE THAT RESCUES A BENCHMARK. Under PERCENTILE_RANK the same
    quantity collapses: rank(margin - M_s) == rank(margin) exactly, because M_s
    is one number for the whole cohort, so the benchmark contributes nothing to
    the ranking and a block weighting .30 Excess + .20 Margin is really 0.50 on
    margin. Anchored, the subtraction is the entire content: it decides where
    zero is.

    THE CURVE, k = 0.06 (six margin points above the cohort bar):

        excess margin   (x-b)/k    score
        ----------------------------------
          -18pp          -3.0      -99.51
          -12pp          -2.0      -96.40
           -6pp          -1.0      -76.16
           -3pp          -0.5      -46.21
            0pp           0.0        0.00   <- exact, by construction
           +3pp          +0.5      +46.21
           +6pp          +1.0      +76.16
          +12pp          +2.0      +96.40
          +18pp          +3.0      +99.51

    With a cohort-derived k the table reads in IQRs instead of margin points:
    one IQR clear of the bar is +76.2, two IQRs +96.4. Preferred, because six
    margin points means something different in software than in groceries.
    """
    if not is_finite(value):
        return _unavailable(BENCHMARK_ANCHORED, REASON_NO_VALUE,
                            feature_key=feature_key, anchor_source=anchor_source)
    if not is_finite(benchmark):
        return _unavailable(BENCHMARK_ANCHORED, REASON_NO_BENCHMARK,
                            raw_value=float(value), feature_key=feature_key,
                            anchor_source=anchor_source,
                            notes=("the bar itself was unavailable; an excess against "
                                   "an unknown bar is not a small number, it is no "
                                   "number.",))
    spec = scale_spec if scale_spec is not None else fixed_scale(
        scale if scale is not None else DEFAULT_EXCESS_MARGIN_SCALE_K)
    resolution = resolve_scale(spec, cohort_values, as_of_date, value_dates)
    if not resolution.ok:
        return _unavailable(BENCHMARK_ANCHORED,
                            resolution.reason or REASON_NON_FINITE_SCALE,
                            raw_value=float(value), anchor=float(benchmark),
                            anchor_source=anchor_source,
                            scale_source=resolution.source,
                            n_cohort=resolution.n_cohort, feature_key=feature_key,
                            notes=(resolution.detail,))
    k = float(resolution.value)
    excess = float(value) - float(benchmark)
    score = clamp(100.0 * math.tanh(excess / k), -100.0, 100.0)
    return NormResult(
        score=score, normalization_type=BENCHMARK_ANCHORED, raw_value=float(value),
        anchor=float(benchmark), anchor_source=anchor_source, scale=k,
        scale_source=resolution.source, n_cohort=resolution.n_cohort or None,
        feature_key=feature_key,
        notes=(f"excess {excess:+.6g} over the bar {float(benchmark):.6g}; "
               + resolution.detail,))


def historical_z_score(value: Any, history: Sequence[Any], *,
                       as_of_date: Optional[str] = None,
                       history_dates: Optional[Sequence[str]] = None,
                       min_history: int = MIN_HISTORY_OBS,
                       z_saturation: float = HISTORICAL_Z_SATURATION,
                       feature_key: Optional[str] = None) -> NormResult:
    """HISTORICAL_Z -- "is this unusual versus its OWN history?"

        z = (x - median(history)) / (1.4826 * MAD(history))
        S = 100 * tanh(z / z_saturation)

    Robust by construction: a mean and a standard deviation over a company's own
    history are dominated by the one restructuring quarter that the factor is
    supposed to flag, so the centre is a median and the spread a MAD.

    The history is the CALLER'S to supply, and the caller owns its point-in-time
    correctness -- pass `history_dates` with `as_of_date` and it is screened
    here. A per-entity history is the easiest place in the whole model to read
    the future, because "its own history" and "its own future" look identical in
    a dataframe.

    Returns UNAVAILABLE with a distinct reason when there is not enough history
    (`history_too_short`) or when the history has no spread
    (`zero_history_dispersion`) -- a company with twelve identical quarters is
    not infinitely unusual on the thirteenth.
    """
    if not is_finite(value):
        return _unavailable(HISTORICAL_Z, REASON_NO_VALUE, feature_key=feature_key,
                            anchor_source=ANCHOR_ENTITY_HISTORY_MEDIAN)
    if history_dates is not None:
        if as_of_date is None:
            raise NormalizationError(
                "history_dates were supplied without an as_of_date")
        observations = cohort_as_of(history, history_dates, as_of_date)
    else:
        observations = _finite(history)
    if len(observations) < min_history:
        return _unavailable(
            HISTORICAL_Z, REASON_HISTORY_TOO_SHORT, raw_value=float(value),
            anchor_source=ANCHOR_ENTITY_HISTORY_MEDIAN, n_cohort=len(observations),
            feature_key=feature_key,
            notes=(f"{len(observations)} usable observations, need {min_history}.",))
    centre = median(observations)
    spread = mad(observations)
    if centre is None or spread is None or spread <= DISPERSION_FLOOR:
        return _unavailable(
            HISTORICAL_Z, REASON_ZERO_HISTORY_DISPERSION, raw_value=float(value),
            anchor=float(centre) if centre is not None else None,
            anchor_source=ANCHOR_ENTITY_HISTORY_MEDIAN, n_cohort=len(observations),
            feature_key=feature_key,
            notes=("its own history has no spread; there is no 'unusual' to measure.",))
    sigma = float(spread) * MAD_TO_SIGMA
    z = (float(value) - float(centre)) / sigma
    score = clamp(100.0 * math.tanh(z / float(z_saturation)), -100.0, 100.0)
    return NormResult(
        score=score, normalization_type=HISTORICAL_Z, raw_value=float(value),
        anchor=float(centre), anchor_source=ANCHOR_ENTITY_HISTORY_MEDIAN,
        scale=sigma, scale_source="entity_history_mad", n_cohort=len(observations),
        feature_key=feature_key,
        notes=(f"robust z {z:+.4f} over {len(observations)} own observations; "
               f"saturation at z = {float(z_saturation):g}.",))


def ml_calibrated_score(*_args: Any, feature_key: Optional[str] = None,
                        **_kwargs: Any) -> NormResult:
    """ML_CALIBRATED -- reserved, and refuses to run.

    The label claims something specific: that the mapping from this quantity
    into [-100, +100] was fitted and validated against realised outcomes on
    purged, embargoed folds. Nothing in this repository has done that yet.

    So the stub refuses rather than quietly falling back to a rank, because a
    silent fallback is how a model ends up carrying a calibration claim it
    cannot support -- the row would say `norm_method = ml_calibrated_v1` and the
    number would be a percentile. It returns an UNAVAILABLE result rather than
    raising, so a replay that hits it records a missing factor with the reason
    `no_calibrated_model_registered` instead of dying halfway through a date.
    """
    return _unavailable(
        ML_CALIBRATED, REASON_NOT_CALIBRATED, feature_key=feature_key,
        notes=("ML_CALIBRATED is reserved. Register a fitted calibration with its "
               "fold set and validation evidence, or choose a type that describes "
               "what the factor actually does.",))


def normalize(spec: "FactorSpec", value: Any, *,
              peer_values: Optional[Sequence[Any]] = None,
              benchmark: Optional[Any] = None,
              cohort_values: Optional[Sequence[Any]] = None,
              history: Optional[Sequence[Any]] = None,
              as_of_date: Optional[str] = None,
              value_dates: Optional[Sequence[str]] = None,
              history_dates: Optional[Sequence[str]] = None) -> NormResult:
    """Apply the normalisation a factor DECLARED, not one the caller chose.

    This is the point of the registry: the transform is a property of the
    factor, recorded once with its rationale, rather than a line of code inside
    whichever scoring function happened to touch the number.
    """
    if spec.normalization_type == PERCENTILE_RANK:
        return percentile_rank_score(value, peer_values or [],
                                     higher_is_better=spec.higher_is_better,
                                     feature_key=spec.feature_key)
    if spec.normalization_type == ZERO_ANCHORED:
        return zero_anchored_score(value, scale_spec=spec.scale,
                                   cohort_values=cohort_values,
                                   as_of_date=as_of_date, value_dates=value_dates,
                                   feature_key=spec.feature_key)
    if spec.normalization_type == BENCHMARK_ANCHORED:
        return benchmark_anchored_score(value, benchmark, scale_spec=spec.scale,
                                        cohort_values=cohort_values,
                                        as_of_date=as_of_date,
                                        value_dates=value_dates,
                                        anchor_source=spec.anchor_source,
                                        feature_key=spec.feature_key)
    if spec.normalization_type == HISTORICAL_Z:
        return historical_z_score(value, history or [], as_of_date=as_of_date,
                                  history_dates=history_dates,
                                  feature_key=spec.feature_key)
    if spec.normalization_type == ML_CALIBRATED:
        return ml_calibrated_score(feature_key=spec.feature_key)
    raise NormalizationError(
        f"{spec.feature_key}: unknown normalization_type "
        f"{spec.normalization_type!r}")


# --------------------------------------------------------------------------
# The one anchor this module computes itself
# --------------------------------------------------------------------------

#: LIFTED OUT of benchmark_margin_50_75 on 2026-09-22 (v4 governance audit,
#: hole A): a literal inside a function body is invisible to pit_frozen_spec,
#: so the bar for the model's largest single factor weight could move with the
#: digest intact. Values UNCHANGED. Equal in value to pit_coverage.
#: MIN_EBITDA_COHORT and deliberately NOT an alias of it: the benchmark's floor
#: is this factor's own policy, digested under its own name.
BENCHMARK_BAND_V1: tuple[float, float] = (0.50, 0.75)
BENCHMARK_MIN_COHORT_V1: int = 3


def benchmark_margin_50_75(ebitda_values: Sequence[Any],
                           margin_values: Sequence[Any],
                           min_cohort: Optional[int] = None
                           ) -> tuple[Optional[float], int, str]:
    """M_s -- the mean margin of the cohort in the 50th-75th EBITDA percentile.

    The owner's benchmark, restated as a free function over plain sequences.
    Deliberately NOT imported from company_scoring: that module is frozen
    production, its version takes CompanyFinancials rows and applies v1's
    EV/EBITDA validity screen, and a research module reaching into it would
    couple the candidate's benchmark to v1's peer plumbing. The BAND is the same
    50-75 band, by design.

    Returns (M_s, n_cohort, note). M_s is None when the band is too thin, which
    the anchored transform turns into `benchmark_unavailable` rather than into a
    zero -- an excess against an unknown bar is not a small number.
    """
    floor = BENCHMARK_MIN_COHORT_V1 if min_cohort is None else int(min_cohort)
    pairs = [(float(e), float(m)) for e, m in zip(ebitda_values, margin_values)
             if is_finite(e) and is_finite(m)]
    if len(pairs) < floor:
        return None, 0, (f"only {len(pairs)} peers carried both an EBITDA and a "
                         f"margin; need {floor}")
    ordered = sorted(e for e, _ in pairs)          # statlib.percentile needs sorted
    lo, hi = BENCHMARK_BAND_V1
    p50 = percentile(ordered, lo)
    p75 = percentile(ordered, hi)
    band = [m for e, m in pairs if p50 <= e <= p75]
    if not band:
        return None, 0, "no peer fell inside the 50-75 EBITDA band"
    m_s = sum(band) / len(band)
    return m_s, len(band), (
        f"M_s = mean margin of {len(band)} peers whose EBITDA sits in the 50-75 "
        f"percentile of {len(pairs)} ({p50:,.0f} to {p75:,.0f}) = {m_s:.4f}")


# ==========================================================================
# (3) THE REDUNDANCY GUARD
# ==========================================================================

VERDICT_REDUNDANT = "REDUNDANT_UNDER_CURRENT_NORMALIZATION"
VERDICT_CORRELATED = "HIGHLY_CORRELATED"
VERDICT_INDEPENDENT = "INDEPENDENT"
VERDICT_DEGENERATE = "DEGENERATE_NO_VARIANCE"

_SEVERITY = {VERDICT_INDEPENDENT: 0, VERDICT_CORRELATED: 1,
             VERDICT_DEGENERATE: 2, VERDICT_REDUNDANT: 3}

SCOPE_POOLED = "pooled"
SCOPE_COHORT = "within_cohort"

#: Ranks equal to within this are the SAME ranking. A tolerance, not `==`:
#: the ranks being compared are floats produced by different arithmetic paths
#: (a log, a subtraction, a division), and an exact-equality test on floats
#: turns a structural identity into a coin flip on the last bit.
EXACT_RANK_TOL = 1e-9

#: |rho| at or above this is reported as HIGHLY_CORRELATED.
RHO_HIGH = 0.95

#: Minimum paired observations before a correlation is reported at all. Below
#: this, rho is a statement about four points.
MIN_RHO_N = 4

#: Exact rank equivalence claims a STRUCTURAL, permanent relationship, so it
#: needs a far larger sample than a reported correlation does. Two independent
#: series collide exactly by chance with probability 2/n!, measured over 30,000
#: trials per n and matching theory:
#:      n=4  8.25%   n=5  1.72%   n=6  0.27%   n=8  0.01%
#: Real SIC cohorts are routinely this small, so at n=4 every independent
#: candidate checked against 20 cohorts was falsely called structural. Below
#: MIN_EXACT_N the finding is downgraded to CORRELATED and says why.
MIN_EXACT_N = 8

#: Relative tolerance for "these two series differ by a constant".
CONSTANT_SHIFT_TOL = 1e-9

EXACT_SAME = "same_ranking"
EXACT_REVERSED = "reversed_ranking"

CAUSE_COHORT_CONSTANT = "cohort_constant_shift"
CAUSE_AFFINE = "affine_rescaling"
CAUSE_MONOTONE = "monotone_transform"
CAUSE_MONOTONE_REVERSED = "sign_reversed_monotone_transform"
CAUSE_SHARED_INPUT = "shared_input"
CAUSE_NONE = "none"

_CAUSE_TEXT = {
    CAUSE_COHORT_CONSTANT: (
        "the two series differ by a constant within the cohort (a deflator, a "
        "benchmark subtraction, a cohort mean), and a rank transform cannot see "
        "a shift"),
    CAUSE_AFFINE: (
        "the two series are an affine rescaling of one another (x -> a*x + b, "
        "a > 0), which preserves order exactly"),
    CAUSE_MONOTONE: (
        "one is a strictly monotone transform of the other (log, log1p, sqrt, "
        "tanh, a winsorised ratio); every one of those is inert under a rank"),
    CAUSE_MONOTONE_REVERSED: (
        "one is a sign-reversed monotone transform of the other, so they carry "
        "the same ordering with the sign flipped"),
    CAUSE_SHARED_INPUT: (
        "they are not an exact transform of one another but move together, which "
        "usually means a shared input (the same numerator, or the same "
        "denominator, in both)"),
    CAUSE_NONE: "no relationship strong enough to name",
}


@dataclass(frozen=True)
class PairFinding:
    """One candidate-vs-existing comparison, in one scope."""

    key: str
    scope: str
    verdict: str
    rho: Optional[float]
    exact: Optional[str] = None
    cause: str = CAUSE_NONE
    n: int = 0
    cohort_id: Optional[str] = None
    explanation: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "scope": self.scope, "verdict": self.verdict,
                "rho": self.rho, "exact": self.exact, "cause": self.cause,
                "n": self.n, "cohort_id": self.cohort_id,
                "explanation": self.explanation}


@dataclass(frozen=True)
class RedundancyReport:
    """The verdict, plus both scopes' evidence for it."""

    verdict: str
    matched_key: Optional[str]
    rho: Optional[float]
    scope: Optional[str]
    cause: str
    explanation: str
    pooled: tuple[PairFinding, ...] = ()
    within_cohort: tuple[PairFinding, ...] = ()
    n: int = 0
    skipped: tuple[str, ...] = ()

    @property
    def is_redundant(self) -> bool:
        return self.verdict == VERDICT_REDUNDANT

    @property
    def carries_no_information(self) -> bool:
        """Redundant OR degenerate -- the two ways a factor adds nothing.

        They are different findings and keep different names: REDUNDANT says
        "this is another factor wearing a hat", DEGENERATE says "this is a
        constant". Registration refuses both, because a zero-variance factor
        passing an information guard is that guard's failure mode inverted.
        """
        return self.verdict in (VERDICT_REDUNDANT, VERDICT_DEGENERATE)

    def as_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "matched_key": self.matched_key,
                "rho": self.rho, "scope": self.scope, "cause": self.cause,
                "explanation": self.explanation, "n": self.n,
                "pooled": [f.as_dict() for f in self.pooled],
                "within_cohort": [f.as_dict() for f in self.within_cohort],
                "skipped": list(self.skipped)}


def _paired(xs: Sequence[Any], ys: Sequence[Any]) -> tuple[list[float], list[float]]:
    """Positions where BOTH series are finite. Pairwise, never listwise.

    Dropping a company from every comparison because one unrelated factor is
    missing would make the guard weakest exactly where coverage is thin.
    """
    a: list[float] = []
    b: list[float] = []
    for x, y in zip(xs, ys):
        if is_finite(x) and is_finite(y):
            a.append(float(x))
            b.append(float(y))
    return a, b


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0.0 or syy <= 0.0:
        return None                      # one series is constant: rho undefined
    rho = sxy / math.sqrt(sxx * syy)
    if not math.isfinite(rho):
        return None
    return clamp(rho, -1.0, 1.0)


def spearman_rho(xs: Sequence[Any], ys: Sequence[Any]) -> Optional[float]:
    """Spearman rank correlation. Pearson on average ranks -- the definition.

    Reuses `statlib.average_ranks`, which already shares the average rank among
    ties; that tie handling is exactly what makes Pearson-on-ranks the correct
    general form rather than the 1 - 6*sum(d^2) shortcut, which is only valid
    with no ties.

    None when fewer than two paired observations survive, or when either series
    is constant (a constant has no ranking, so nothing can correlate with it).
    """
    a, b = _paired(xs, ys)
    if len(a) < 2:
        return None
    return _pearson(average_ranks(a), average_ranks(b))


def rank_equivalence(xs: Sequence[Any], ys: Sequence[Any],
                     tol: float = EXACT_RANK_TOL) -> Optional[str]:
    """EXACT rank equivalence, which is a different claim from high correlation.

    rho = 0.97 is a statistical observation about this sample. Identical ranks
    are a STRUCTURAL fact: the two series are related by a monotone map, so they
    will produce the same rank-normalised score in every cohort, at every date,
    forever -- no amount of new data can separate them. The two deserve
    different words, and different responses: one is a warning, the other is a
    proof that a factor is decorative.

    Returns EXACT_SAME, EXACT_REVERSED, or None. Compared with a tolerance
    rather than `==` because ranks of values computed along different arithmetic
    paths differ in the last bits.
    """
    a, b = _paired(xs, ys)
    if len(a) < 2:
        return None
    ra = average_ranks(a)
    rb = average_ranks(b)
    if all(abs(p - q) <= tol for p, q in zip(ra, rb)):
        return EXACT_SAME
    n = len(ra)
    if all(abs(p - (n + 1 - q)) <= tol for p, q in zip(ra, rb)):
        return EXACT_REVERSED
    return None


def _scale_of(values: Sequence[float]) -> float:
    """A magnitude for relative tolerances. Never zero."""
    biggest = max((abs(v) for v in values), default=0.0)
    return biggest if biggest > 0.0 else 1.0


def _diagnose(xs: Sequence[float], ys: Sequence[float], exact: Optional[str]) -> str:
    """Name the likely CAUSE of an exact rank equivalence.

    Three shapes, distinguishable from the numbers themselves:
    a constant difference (a deflator or a cohort benchmark), an affine
    rescaling (a gap divided by a cohort constant), or a general monotone
    transform (a log). Each one is a different mistake with a different fix.
    """
    if exact == EXACT_REVERSED:
        return CAUSE_MONOTONE_REVERSED
    if exact != EXACT_SAME:
        return CAUSE_SHARED_INPUT
    diffs = [x - y for x, y in zip(xs, ys)]
    tol = CONSTANT_SHIFT_TOL * _scale_of(list(xs) + list(ys))
    if max(diffs) - min(diffs) <= tol:
        return CAUSE_COHORT_CONSTANT
    span_y = max(ys) - min(ys)
    if span_y > 0.0:
        a = (max(xs) - min(xs)) / span_y
        b = min(xs) - a * min(ys)
        if a > 0.0 and all(abs(x - (a * y + b)) <= tol for x, y in zip(xs, ys)):
            return CAUSE_AFFINE
    return CAUSE_MONOTONE


def _rank_regime(candidate_type: Optional[str], existing_type: Optional[str]) -> bool:
    """Is exact rank equivalence a PROOF of redundancy for this pair?

    Only when BOTH sides are rank-normalised. This is the correction that makes
    the verdict name honest: `REDUNDANT_UNDER_CURRENT_NORMALIZATION` is a claim
    about the normalisation, and the first implementation never looked at it.

    Identical raw ranks do NOT imply identical scores once an anchored transform
    is involved. EBITDAExcess = Margin - M_s is exactly rank-equivalent to Margin
    inside every cohort -- and that is precisely why the fix was to normalise it
    BENCHMARK_ANCHORED rather than by rank. Judged on raw ranks alone the guard
    refuses its own remedy, which is how a correct module disarms itself: the
    only way past it is allow_redundant=True, and once that habit exists every
    other check is decorative.

    Unknown types are treated as the rank regime, because the conservative error
    is a false alarm a human resolves, not a silent admission.
    """
    return ((candidate_type or PERCENTILE_RANK) == PERCENTILE_RANK
            and (existing_type or PERCENTILE_RANK) == PERCENTILE_RANK)


def _finding(key: str, scope: str, xs: Sequence[float], ys: Sequence[float],
             cohort_id: Optional[str], high_rho: float,
             candidate_type: Optional[str] = None,
             existing_type: Optional[str] = None,
             min_exact_n: int = MIN_EXACT_N) -> PairFinding:
    rho = _pearson(average_ranks(xs), average_ranks(ys)) if len(xs) >= 2 else None
    exact = rank_equivalence(xs, ys)

    if exact is not None and len(xs) < min_exact_n:
        # Real, but not evidence. At n=4 two independent series collide 8.25% of
        # the time; calling that "structural, forever" is the guard crying wolf.
        return PairFinding(
            key=key, scope=scope, verdict=VERDICT_CORRELATED, rho=rho, exact=None,
            cause=CAUSE_SHARED_INPUT, n=len(xs), cohort_id=cohort_id,
            explanation=(f"ranks match {key} over only {len(xs)} observations, "
                         f"below MIN_EXACT_N={min_exact_n}. Two independent "
                         f"series collide exactly by chance with probability "
                         f"2/{len(xs)}!, so this is not evidence of a structural "
                         f"relationship. Re-check on a larger cohort."))

    if exact is not None and not _rank_regime(candidate_type, existing_type):
        # The ranks agree but the normalisations do not, which is the DESIGNED
        # way to separate two factors built on the same quantity.
        return PairFinding(
            key=key, scope=scope, verdict=VERDICT_CORRELATED, rho=rho, exact=exact,
            cause=_diagnose(xs, ys, exact), n=len(xs), cohort_id=cohort_id,
            explanation=(f"raw ranks are equivalent to {key}, but the two are "
                         f"normalised differently ({candidate_type} vs "
                         f"{existing_type}), so their SCORES are not equivalent. "
                         f"This is the intended construction, not a collapse -- "
                         f"an anchored transform keeps the level information a "
                         f"rank discards. Verify the scores differ materially "
                         f"before admitting it."))

    if exact is not None:
        cause = _diagnose(xs, ys, exact)
        where = "pooled across the sample" if scope == SCOPE_POOLED \
            else f"inside cohort {cohort_id!r}"
        direction = "identical" if exact == EXACT_SAME else "exactly reversed"
        return PairFinding(
            key=key, scope=scope, verdict=VERDICT_REDUNDANT, rho=rho, exact=exact,
            cause=cause, n=len(xs), cohort_id=cohort_id,
            explanation=(f"ranks are {direction} to {key} {where} over {len(xs)} "
                         f"observations: {_CAUSE_TEXT[cause]}. Under a rank "
                         f"normalisation the two produce the same score, to the "
                         f"digit."))
    if rho is None:
        # A constant candidate correlates with nothing -- and carries nothing.
        # Reporting that as INDEPENDENT let a zero-information factor pass the
        # information guard, which is the failure mode inverted.
        xs_finite = [v for v in xs if is_finite(v)]
        candidate_flat = len(set(xs_finite)) <= 1
        if candidate_flat:
            return PairFinding(
                key=key, scope=scope, verdict=VERDICT_DEGENERATE, rho=None,
                n=len(xs), cohort_id=cohort_id,
                explanation=("the candidate has no cross-sectional variance over "
                             f"{len(xs)} observations, so it cannot rank anything. "
                             "It is not independent of the existing factors; it is "
                             "empty."))
        return PairFinding(key=key, scope=scope, verdict=VERDICT_INDEPENDENT,
                           rho=None, n=len(xs), cohort_id=cohort_id,
                           explanation=(f"no usable correlation against {key} "
                                        f"({len(xs)} paired observations, or the "
                                        f"comparison series is constant)"))
    if abs(rho) >= high_rho:
        where = "pooled" if scope == SCOPE_POOLED else f"inside cohort {cohort_id!r}"
        return PairFinding(
            key=key, scope=scope, verdict=VERDICT_CORRELATED, rho=rho,
            cause=CAUSE_SHARED_INPUT, n=len(xs), cohort_id=cohort_id,
            explanation=(f"Spearman rho {rho:+.4f} against {key} {where} over "
                         f"{len(xs)} observations: {_CAUSE_TEXT[CAUSE_SHARED_INPUT]}. "
                         f"Statistical, not structural -- it may separate in "
                         f"another cohort or another date."))
    return PairFinding(key=key, scope=scope, verdict=VERDICT_INDEPENDENT, rho=rho,
                       n=len(xs), cohort_id=cohort_id,
                       explanation=(f"Spearman rho {rho:+.4f} against {key} over "
                                    f"{len(xs)} observations"))


def redundancy_check(candidate_values: Sequence[Any],
                     existing_by_key: Mapping[str, Sequence[Any]],
                     cohort_ids: Optional[Sequence[Any]] = None,
                     *, high_rho: float = RHO_HIGH,
                     min_n: int = MIN_RHO_N,
                     candidate_type: Optional[str] = None,
                     existing_types: Optional[Mapping[str, str]] = None,
                     min_exact_n: int = MIN_EXACT_N) -> RedundancyReport:
    """Does this proposed factor carry new information UNDER ITS NORMALISATION?

    The centrepiece. Every sequence is aligned BY POSITION: element i of
    `candidate_values`, of each series in `existing_by_key`, and of `cohort_ids`
    describe the same company at the same as-of date.

    Two instruments, reported separately:

      EXACT RANK EQUIVALENCE is structural. It says the two series are related
      by a monotone map and will produce identical rank-normalised scores
      forever. Verdict REDUNDANT_UNDER_CURRENT_NORMALIZATION.

      SPEARMAN RHO is statistical. |rho| >= 0.95 says they moved together in
      this sample. Verdict HIGHLY_CORRELATED -- a warning, not a proof.

    And it is computed WITHIN COHORT wherever `cohort_ids` is supplied, because
    these collapses are within-cohort phenomena. EBITDAExcess = margin - M_s is
    exactly rank-equivalent to margin inside every sector, and pooled across
    sectors it is not, because M_s differs by sector: the pooled rho can sit at
    0.9 and look like a merely-correlated pair while the factor is provably
    inert everywhere it is actually used. BOTH scopes are reported, and the
    verdict is the more severe of the two.

    A cohort smaller than `min_n` is skipped and named in `skipped`, never
    silently folded into the pooled number.
    """
    n_total = len(candidate_values)
    for key, series in existing_by_key.items():
        if len(series) != n_total:
            raise NormalizationError(
                f"existing factor {key!r} has {len(series)} values against the "
                f"candidate's {n_total}; series are aligned by position and must "
                f"be the same length")
    if cohort_ids is not None and len(cohort_ids) != n_total:
        raise NormalizationError(
            f"cohort_ids has {len(cohort_ids)} entries against {n_total} values")

    pooled: list[PairFinding] = []
    per_cohort: list[PairFinding] = []
    skipped: list[str] = []

    for key, series in existing_by_key.items():
        a, b = _paired(candidate_values, series)
        if len(a) < min_n:
            skipped.append(f"{key} (pooled): {len(a)} paired observations, need {min_n}")
            continue
        pooled.append(_finding(key, SCOPE_POOLED, a, b, None, high_rho,
                               candidate_type,
                               (existing_types or {}).get(key), min_exact_n))

    if cohort_ids is not None:
        groups: dict[Any, list[int]] = {}
        for index, cohort in enumerate(cohort_ids):
            groups.setdefault(cohort, []).append(index)
        for cohort in sorted(groups, key=lambda c: str(c)):
            members = groups[cohort]
            for key, series in existing_by_key.items():
                a, b = _paired([candidate_values[i] for i in members],
                               [series[i] for i in members])
                if len(a) < min_n:
                    skipped.append(
                        f"{key} (cohort {cohort!r}): {len(a)} paired observations, "
                        f"need {min_n}")
                    continue
                per_cohort.append(_finding(key, SCOPE_COHORT, a, b, cohort,
                                           high_rho, candidate_type,
                                           (existing_types or {}).get(key),
                                           min_exact_n))

    findings = pooled + per_cohort
    if not findings:
        return RedundancyReport(
            verdict=VERDICT_INDEPENDENT, matched_key=None, rho=None, scope=None,
            cause=CAUSE_NONE,
            explanation=("nothing could be compared: no existing factor had "
                         f"{min_n} paired observations against the candidate"),
            pooled=tuple(pooled), within_cohort=tuple(per_cohort), n=n_total,
            skipped=tuple(skipped))

    worst = max(findings, key=lambda f: (_SEVERITY[f.verdict],
                                         abs(f.rho) if f.rho is not None else 0.0,
                                         f.n))
    explanation = worst.explanation
    if worst.scope == SCOPE_COHORT and worst.verdict == VERDICT_REDUNDANT:
        pooled_match = next((f for f in pooled if f.key == worst.key), None)
        if pooled_match is not None and pooled_match.verdict != VERDICT_REDUNDANT:
            explanation += (
                f" Note the pooled view would have missed this: pooled rho is "
                f"{pooled_match.rho:+.4f}, which reads as merely correlated.")
    return RedundancyReport(
        verdict=worst.verdict, matched_key=worst.key, rho=worst.rho,
        scope=worst.scope, cause=worst.cause, explanation=explanation,
        pooled=tuple(pooled), within_cohort=tuple(per_cohort), n=n_total,
        skipped=tuple(skipped))


# --------------------------------------------------------------------------
# The acceptance vocabulary.
#
# `redundancy_check` reports a VERDICT -- what the guard decides. An acceptance
# report needs the other half: WHAT RELATIONSHIP WAS MEASURED, stated in words
# that do not presume the decision. The two are deliberately separate, because
# the whole repair to this module is that a measured relationship and a refusal
# are not the same thing: EXACT_RANK_EQUIVALENT is a fact about two series,
# while REDUNDANT_UNDER_CURRENT_NORMALIZATION is a claim about what happens to
# them once their declared normalisations are applied. A pair can be the first
# without being the second, and that pair is exactly EBITDAExcessLevel.
# --------------------------------------------------------------------------

STATUS_EXACT_RANK_EQUIVALENT = "EXACT_RANK_EQUIVALENT"
STATUS_EXACT_RANK_INVERSE = "EXACT_RANK_INVERSE"
STATUS_NEAR_REDUNDANT = "NEAR_REDUNDANT"
STATUS_DISTINCT = "DISTINCT"

ACCEPTANCE_STATUSES = (STATUS_EXACT_RANK_EQUIVALENT, STATUS_EXACT_RANK_INVERSE,
                       STATUS_NEAR_REDUNDANT, STATUS_DISTINCT)


def acceptance_status(finding: Any) -> str:
    """The measured rank relationship, as one of ACCEPTANCE_STATUSES.

    Takes a PairFinding or a RedundancyReport. It reports the MEASUREMENT and
    never the decision: a pair whose raw ranks coincide is EXACT_RANK_EQUIVALENT
    whether or not the guard admits it, and the acceptance report prints the
    status beside the verdict so the difference between the two is visible on
    every row rather than argued about.

    A candidate with no cross-sectional variance is DISTINCT by this measure --
    it correlates with nothing because it says nothing -- and that is precisely
    why the status is never read alone: its verdict is DEGENERATE_NO_VARIANCE
    and `register_factor` refuses it.

    Given a whole report, the status describes the SAME pair the verdict came
    from -- the most severe finding across both scopes -- and not the pooled
    one, which is the finding that would have missed the flagship case.
    """
    findings = (tuple(getattr(finding, "pooled", ()))
                + tuple(getattr(finding, "within_cohort", ())))
    if findings:
        finding = max(findings, key=lambda f: (_SEVERITY[f.verdict],
                                               abs(f.rho) if f.rho is not None else 0.0,
                                               f.n))
    exact = getattr(finding, "exact", None)
    if exact == EXACT_SAME:
        return STATUS_EXACT_RANK_EQUIVALENT
    if exact == EXACT_REVERSED:
        return STATUS_EXACT_RANK_INVERSE
    rho = getattr(finding, "rho", None)
    if rho is not None and abs(rho) >= RHO_HIGH:
        return STATUS_NEAR_REDUNDANT
    return STATUS_DISTINCT


#: Anchors whose value is read off the cohort. A factor carrying one is scored
#: against its peers, so a redundancy check run only in the pooled scope has
#: not tested it where it is used.
COHORT_ANCHORS = (ANCHOR_COHORT_RANK, ANCHOR_COHORT_50_75_MEAN_MARGIN)


def is_cohort_scoped(spec: "FactorSpec") -> bool:
    """Is this factor's score decided by the cohort it sits in?

    True for a rank (the cohort IS the score), for a cohort-derived anchor, and
    for a cohort-derived scale. It is the test `register_factor` uses to decide
    whether `cohort_ids` is compulsory -- and it exists because the collapse
    this module was written for, margin - M_s, is invisible pooled and exact
    within every cohort. Registering such a factor on the pooled scope alone
    is not a weaker check; it is a check of a different question.
    """
    return (spec.normalization_type == PERCENTILE_RANK
            or spec.anchor_source in COHORT_ANCHORS
            or spec.scale_source in COHORT_SCALE_SOURCES)



# ==========================================================================
# (4) THE FACTOR REGISTRY
# ==========================================================================

@dataclass(frozen=True)
class FactorSpec:
    """A factor's declaration. The normalisation is part of its identity.

    `economic_question` is not decoration: it is the field that makes a wrong
    type visible. "Where does this sit versus peers?" and "is this positive?"
    are different questions, and a factor whose stated question is the second
    cannot be normalised by rank.
    """

    feature_key: str
    normalization_type: str = ""
    economic_question: str = ""
    rationale: str = ""
    anchor_source: str = ANCHOR_NONE
    scale: Optional[ScaleSpec] = None
    higher_is_better: bool = True
    feature_role: str = ROLE_RESEARCH
    model_version: str = CANDIDATE_MODEL_VERSION
    notes: tuple[str, ...] = ()

    @property
    def scale_source(self) -> str:
        return self.scale.source if self.scale is not None else SCALE_NONE

    def as_dict(self) -> dict[str, Any]:
        return {
            "feature_key": self.feature_key,
            "normalization_type": self.normalization_type,
            "economic_question": self.economic_question,
            "rationale": self.rationale,
            "anchor_source": self.anchor_source,
            "scale_source": self.scale_source,
            "scale": self.scale.as_dict() if self.scale is not None else None,
            "higher_is_better": self.higher_is_better,
            "feature_role": self.feature_role,
            "model_version": self.model_version,
            "policy_version": NORMALIZATION_POLICY_VERSION,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class Registration:
    """What happened when a factor was offered to the registry.

    `checked`, `compared_keys` and `scopes_run` exist because the reviewer's
    central defect was that a registration could LOOK successful while no check
    had run at all. A Registration now states what was tested, against what,
    and in which scopes -- so "it passed the guard" is a verifiable claim
    rather than a hope about the caller's arguments.
    """

    spec: FactorSpec
    accepted: bool
    note: str
    redundancy: Optional[RedundancyReport] = None
    checked: bool = False
    compared_keys: tuple[str, ...] = ()
    scopes_run: tuple[str, ...] = ()
    unchecked_reason: Optional[str] = None
    cohort_scope_not_run_reason: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {"feature_key": self.spec.feature_key, "accepted": self.accepted,
                "note": self.note, "checked": self.checked,
                "compared_keys": list(self.compared_keys),
                "scopes_run": list(self.scopes_run),
                "unchecked_reason": self.unchecked_reason,
                "cohort_scope_not_run_reason": self.cohort_scope_not_run_reason,
                "redundancy": (self.redundancy.as_dict()
                               if self.redundancy is not None else None)}


def _validate(spec: FactorSpec) -> FactorSpec:
    """Refuse an incoherent declaration. Raises; never returns a repaired spec.

    The one exception is ML_CALIBRATED, whose role is forced down to
    research_candidate rather than refused: the type exists so the intention can
    be recorded, but it must not be able to arrive in production by being typed.
    """
    if not spec.feature_key:
        raise NormalizationError("a factor needs a feature_key")
    if not spec.normalization_type:
        raise NormalizationError(
            f"{spec.feature_key}: no normalization_type. Every factor must say how "
            f"its value becomes a score -- that mapping is as much a modelling "
            f"choice as the variable itself. Expected one of {NORMALIZATION_TYPES}")
    if spec.normalization_type not in NORMALIZATION_TYPES:
        raise NormalizationError(
            f"{spec.feature_key}: unknown normalization_type "
            f"{spec.normalization_type!r}; expected one of {NORMALIZATION_TYPES}")
    if not spec.economic_question:
        raise NormalizationError(
            f"{spec.feature_key}: no economic_question. A factor that cannot state "
            f"the question it answers cannot have the right normalisation checked.")
    if not spec.rationale:
        raise NormalizationError(f"{spec.feature_key}: no rationale")

    if spec.normalization_type == BENCHMARK_ANCHORED:
        if spec.anchor_source in (ANCHOR_NONE, ANCHOR_ZERO):
            raise NormalizationError(
                f"{spec.feature_key}: BENCHMARK_ANCHORED needs an anchor_source -- "
                f"the bar is the factor")
        if spec.scale is None:
            raise NormalizationError(
                f"{spec.feature_key}: BENCHMARK_ANCHORED needs a scale (k); it "
                f"decides where the curve saturates and is the whole behaviour")
    if spec.normalization_type == ZERO_ANCHORED:
        if spec.anchor_source != ANCHOR_ZERO:
            raise NormalizationError(
                f"{spec.feature_key}: ZERO_ANCHORED anchors at zero; anchor_source "
                f"must be {ANCHOR_ZERO!r}, not {spec.anchor_source!r}")
        if spec.scale is None:
            raise NormalizationError(f"{spec.feature_key}: ZERO_ANCHORED needs a scale (g)")
    if spec.normalization_type == HISTORICAL_Z and \
            spec.anchor_source != ANCHOR_ENTITY_HISTORY_MEDIAN:
        raise NormalizationError(
            f"{spec.feature_key}: HISTORICAL_Z anchors on the entity's own history; "
            f"anchor_source must be {ANCHOR_ENTITY_HISTORY_MEDIAN!r}")
    if spec.normalization_type == PERCENTILE_RANK and \
            spec.anchor_source not in (ANCHOR_NONE, ANCHOR_COHORT_RANK):
        raise NormalizationError(
            f"{spec.feature_key}: PERCENTILE_RANK has no anchor -- a rank cannot see "
            f"one. anchor_source {spec.anchor_source!r} would be a false claim.")
    if spec.normalization_type == ML_CALIBRATED and spec.feature_role != ROLE_RESEARCH:
        return _dc_replace(spec, feature_role=ROLE_RESEARCH)
    return spec


def register_factor(registry: dict[str, FactorSpec],
                    spec: Any,
                    *, sample_values: Optional[Sequence[Any]] = None,
                    samples: Optional[Mapping[str, Sequence[Any]]] = None,
                    existing_samples: Optional[Mapping[str, Sequence[Any]]] = None,
                    cohort_ids: Optional[Sequence[Any]] = None,
                    allow_redundant: bool = False,
                    unchecked_reason: Optional[str] = None,
                    cohort_scope_not_run_reason: Optional[str] = None,
                    replace: bool = False) -> Registration:
    """Add a factor to a registry, or refuse it and say why.

    REFUSES (raises NormalizationError) a factor with no normalization_type, an
    unknown one, or a declaration that contradicts itself -- a
    BENCHMARK_ANCHORED factor with no bar, a ZERO_ANCHORED factor with no scale,
    a PERCENTILE_RANK factor claiming an anchor it cannot see.

    REFUSES (raises RedundancyError) a factor that carries no new information
    against what is already registered -- either because it is rank-equivalent
    to an existing factor under both their normalisations, or because it has no
    cross-sectional variance at all and therefore ranks nothing.

    THE CHECK IS COMPULSORY, AND THE COMPARISON SET COMES FROM THE REGISTRY.
    This is the repair to the defect that made every other repair decorative:
    the guard used to run only when a caller volunteered BOTH `sample_values`
    and `existing_samples`, compared against whatever subset of factors that
    caller chose to name, and silently fell back to the pooled scope when
    `cohort_ids` was omitted -- the scope that would have missed the flagship
    case. A factor could be admitted with no check having run and nothing
    saying so. So:

      * Registering into a NON-EMPTY registry without `sample_values` and
        `samples` raises. The first factor into an empty registry has nothing
        to be redundant against and is recorded as unchecked-because-first.
      * The comparison set is `registry.keys()`, never the caller's list.
        `samples` must carry a column for EVERY registered factor; a missing
        column raises and names it. Choosing what to be compared against is the
        hole, not a convenience.
      * `cohort_ids` is REQUIRED when the candidate or any registered factor is
        cohort-scoped (a rank, a cohort anchor, a cohort-derived scale), because
        for those the pooled scope answers a different question. Omitting them
        raises unless `cohort_scope_not_run_reason` is given, and then the
        Registration says WITHIN-COHORT SCOPE NOT RUN in its note, loudly.
      * Both normalisation types are passed to the check, so exact rank
        equivalence between a ranked factor and an anchored one is reported as
        the intended construction rather than as a collapse. Without this the
        guard refuses its own remedy and the only way past it is
        allow_redundant=True -- the habit that disarms every other check.

    `unchecked_reason` is the one deliberate bypass: it admits the factor with
    no comparison at all and stamps the reason on the Registration, where a
    reader sees UNCHECKED before they see the factor. `allow_redundant=True`
    records a positive finding and admits anyway. Both are honest escape
    hatches; neither is silent.

    `existing_samples` is accepted as the old name for `samples` so existing
    callers keep working -- but under the new contract, not the old one: it is
    still required to cover the whole registry.
    """
    if isinstance(spec, Mapping):
        unknown = set(spec) - {f.name for f in FactorSpec.__dataclass_fields__.values()}
        if unknown:
            raise NormalizationError(f"unknown factor fields: {sorted(unknown)}")
        spec = FactorSpec(**spec)
    if not isinstance(spec, FactorSpec):
        raise NormalizationError(f"expected a FactorSpec or a mapping, got {type(spec)}")

    spec = _validate(spec)
    if spec.feature_key in registry and not replace:
        raise NormalizationError(
            f"{spec.feature_key} is already registered; pass replace=True to "
            f"redefine it (and expect to version the model when you do)")

    if samples is None:
        samples = existing_samples
    elif existing_samples:
        raise NormalizationError(
            "pass samples OR existing_samples, not both -- two comparison "
            "panels cannot both be the one the registry was checked against")

    # THE COMPARISON SET IS DERIVED, NOT SUPPLIED. A factor is compared against
    # every other factor already registered; its own key is excluded so a
    # replace=True redefinition is not judged redundant against the version it
    # replaces.
    compared_keys = tuple(sorted(key for key in registry if key != spec.feature_key))

    report: Optional[RedundancyReport] = None
    checked = False
    scopes_run: tuple[str, ...] = ()
    reason_unchecked: Optional[str] = None
    reason_no_cohort: Optional[str] = None

    if not compared_keys:
        reason_unchecked = ("first factor registered: an empty registry offers "
                            "nothing to be redundant against")
    elif sample_values is None or not samples:
        if not unchecked_reason:
            raise NormalizationError(
                f"{spec.feature_key}: the redundancy check is compulsory. Pass "
                f"sample_values and samples covering {list(compared_keys)} "
                f"(aligned by position), or pass unchecked_reason to admit it "
                f"with the omission recorded. A factor admitted with no check "
                f"and no record is indistinguishable from one that passed.")
        reason_unchecked = unchecked_reason
    else:
        missing = [key for key in compared_keys if key not in samples]
        if missing:
            raise NormalizationError(
                f"{spec.feature_key}: samples is missing a column for "
                f"{missing}. The comparison set is the registry, not the "
                f"caller's choice -- omitting a registered factor is how a "
                f"redundant candidate passes a check that ran.")
        cohort_scoped = [key for key in compared_keys if is_cohort_scoped(registry[key])]
        if is_cohort_scoped(spec):
            cohort_scoped.append(spec.feature_key)
        if cohort_scoped and cohort_ids is None:
            if not cohort_scope_not_run_reason:
                raise NormalizationError(
                    f"{spec.feature_key}: cohort_ids are required because "
                    f"{sorted(set(cohort_scoped))} are scored WITHIN a cohort. "
                    f"Pooled is a different question: margin - M_s is exactly "
                    f"rank-equivalent to margin inside every sector and merely "
                    f"correlated across them, so the pooled scope is where that "
                    f"collapse hides. Pass cohort_ids, or pass "
                    f"cohort_scope_not_run_reason to record that it was not run.")
            reason_no_cohort = cohort_scope_not_run_reason
        report = redundancy_check(
            sample_values, {key: samples[key] for key in compared_keys},
            cohort_ids,
            candidate_type=spec.normalization_type,
            existing_types={key: registry[key].normalization_type
                            for key in compared_keys})
        checked = True
        scopes_run = ((SCOPE_POOLED, SCOPE_COHORT) if cohort_ids is not None
                      else (SCOPE_POOLED,))
        if report.carries_no_information and not allow_redundant:
            raise RedundancyError(
                f"{spec.feature_key} carries no new information "
                f"({report.verdict}) against {report.matched_key}: "
                f"{report.explanation}")

    registry[spec.feature_key] = spec
    note = f"registered {spec.feature_key} as {spec.normalization_type}"
    if reason_unchecked:
        note = f"UNCHECKED ({reason_unchecked}): " + note
    if reason_no_cohort:
        note += (f"; WITHIN-COHORT SCOPE NOT RUN ({reason_no_cohort}) -- the "
                 f"pooled verdict does not cover the scope this factor is "
                 f"scored in")
    if checked:
        note += f"; checked against {list(compared_keys)} in {list(scopes_run)}"
    if report is not None and report.carries_no_information:
        note += f" DESPITE being redundant against {report.matched_key}"
    if spec.normalization_type == ML_CALIBRATED:
        note += "; forced to research_candidate -- the label is reserved"
    return Registration(spec=spec, accepted=True, note=note, redundancy=report,
                        checked=checked, compared_keys=compared_keys,
                        scopes_run=scopes_run,
                        unchecked_reason=reason_unchecked,
                        cohort_scope_not_run_reason=reason_no_cohort)


def registry_spec(registry: Mapping[str, FactorSpec]) -> dict[str, Any]:
    """A JSON-serialisable dump, for pit_model_lineage.transformations_json."""
    return {
        "policy_version": NORMALIZATION_POLICY_VERSION,
        "model_version": CANDIDATE_MODEL_VERSION,
        "normalization_types": {t: NORMALIZATION_QUESTION[t] for t in NORMALIZATION_TYPES},
        "factors": {key: spec.as_dict() for key, spec in sorted(registry.items())},
    }


def candidate_v2_registry() -> dict[str, FactorSpec]:
    """The seed registry for candidate_equity_shaffer_v2's EBITDA family.

    A FRESH dict each call, holding frozen specs: a caller who mutates what they
    get back changes their copy, not the policy.

    The choices, and why:

    EBITDAMarginRank   PERCENTILE_RANK. Efficiency versus the whole sector, where
                       the ordering is the information. Margin is already an
                       intensity, so the rank's blindness to scale is a feature.

    EBITDAExcessLevel  BENCHMARK_ANCHORED at M_s, the 50-75 cohort mean margin.
                       Under a rank this factor is PROVABLY the margin rank
                       again (margin - M_s is a cohort-constant shift), so the
                       anchored form is not a preference -- it is the only form
                       in which the benchmark exists. k comes from the cohort
                       IQR over the WHOLE eligible vector and from nothing else
                       (R8); a flat or thin vector refuses.

    EBITDAGrowth       ZERO_ANCHORED. Growth has a true zero: shrinking and
                       growing are different states, and in a cohort where
                       everyone shrank a rank still hands out +100s. The input
                       is a RATE, (E_t - E_{t-1}) / |E_{t-1}|, under
                       pit_factor_spec.EBITDA_GROWTH_BASE_POLICY_V1 (owner
                       decision 2026-09-22, D3 growth = A): a zero base and a
                       base too small to carry a rate are refused BY NAME, so
                       neither can enter the cohort scale. The earlier
                       amount-over-assets reading is on record in FREEZE_V4.

    EBITDAAcceleration ZERO_ANCHORED, with a COHORT-DERIVED scale rather than a
                       fixed one. Zero means "growth held steady", which is a
                       real economic state and must score 0. The input is the
                       difference of two consecutive A-rates, growth_t -
                       growth_{t-1}, each under the same base policy: three
                       EBITDA observations and no asset base. A rate
                       difference has its own dispersion, so the scale is
                       derived from the cohort rather than borrowed.

    InterestCoverageRank PERCENTILE_RANK. operating_income / interest_expense
                       (owner decision 2026-09-22) is a times-covered multiple
                       whose ORDERING within the sector is the information; a
                       rank is also indifferent to the long right tail a tanh
                       scale would have to clamp. Its domain policy
                       (pit_factor_spec.INTEREST_COVERAGE_DOMAIN_V2) runs
                       upstream: only interest_expense > 0 reaches this rank,
                       and only operating_income > 0 enters the RANK
                       POPULATION -- a non-positive operating income is the
                       factor's FLOOR STATE, scored at -100 outside the rank
                       (owner rulings R4, R5, 2026-09-23).

    FCFConversionRank  PERCENTILE_RANK, higher is better. free_cash_flow /
                       ebitda is a conversion RATE whose ordering within the
                       sector is the information; capital intensity differs
                       so much across sectors that a fixed bar would be a
                       different bar in every industry. Domain FCF_DOMAIN_V1
                       (EBITDA > 0) runs upstream (R11, R13).

    NetDebtEBITDARank  PERCENTILE_RANK, LOWER is better. net_debt / ebitda in
                       years; the frozen v1 core ranked it the same way and
                       the owner's FACTOR_DIRECTION_V1 pins the sign. Domain
                       LEVERAGE_DOMAIN_V1 and the strict debt-rung gate run
                       upstream (R10, R11, R13); a net-cash company ranks best.

    DebtMarketCapRank  PERCENTILE_RANK, LOWER is better. total_debt /
                       market_cap; the v1 core's direction, pinned by the
                       owner. Domain MARKET_CAP_DOMAIN_V1 (strict share-class
                       policy, raw as-traded price, debt AT the EBITDA period)
                       runs upstream (R10, R12, R13, R19, R20).

    EBITDAScale        PERCENTILE_RANK on RAW EBITDA DOLLARS. Genuine economic
                       scale, and the rank is honest about what it can say.
                       Note the log is INERT here: S(EBITDA) == S(log1p(EBITDA))
                       to the digit, so log1p buys nothing -- and it is
                       undefined for the 37.8% with EBITDA <= 0, so it costs
                       coverage for that nothing. Rank the dollars.

    RealRevenueGrowth  ZERO_ANCHORED at g. The founding case: under a rank the
                       deflator is inert, and a company growing +4% into 9%
                       inflation scores -33.333 with or without it. Anchored at
                       zero, +4% nominal less 9% inflation is -5% real and
                       scores -46.2, which is what the subtraction was for.
    """
    specs = (
        FactorSpec(
            feature_key="EBITDAMarginRank",
            normalization_type=PERCENTILE_RANK,
            economic_question="how efficient versus the whole sector?",
            rationale=("Margin is an intensity, so ranking it against the sector is "
                       "the right question and the units carry no extra meaning."),
            anchor_source=ANCHOR_COHORT_RANK,
            notes=("v1 scored this by rank too; unchanged on purpose.",),
        ),
        FactorSpec(
            feature_key="EBITDAExcessLevel",
            normalization_type=BENCHMARK_ANCHORED,
            economic_question="does it clear the 50-75 cohort's profitability bar?",
            rationale=("margin - M_s is a cohort-constant shift, so under a rank it "
                       "scores identically to the margin itself and the benchmark "
                       "contributes nothing. Anchored, M_s sets where zero is, which "
                       "is the only way the bar survives into the score."),
            anchor_source=ANCHOR_COHORT_50_75_MEAN_MARGIN,
            scale=cohort_scale(multiplier=1.0),
            notes=("one cohort IQR clear of the bar scores +76.2; two IQRs +96.4.",
                   "measured collapse: rank(margin - M_s) == rank(margin), exactly.",
                   "NO fixed fallback scale (owner ruling R8, 2026-09-23: the scale "
                   "statistic is over the whole eligible vector): a vector too small or "
                   "too flat to carry a dispersion refuses by name rather than scoring "
                   "against 6 margin points nobody measured. The v5 entry's "
                   "fallback_fixed = 0.06 is recorded in "
                   "pit_frozen_spec.FREEZE_V5['superseded_definitions_it_digested'].",),
        ),
        FactorSpec(
            feature_key="EBITDAGrowth",
            normalization_type=ZERO_ANCHORED,
            economic_question="is the business generating more EBITDA than a year ago?",
            rationale=("Growth has a true zero and a meaningful sign; a rank would "
                       "hand out +100s in a cohort where every company shrank. The "
                       "input is a RATE over the absolute prior EBITDA under "
                       "pit_factor_spec.EBITDA_GROWTH_BASE_POLICY_V1: a loss "
                       "shrinking is positive, a zero base is refused by name, and a "
                       "base too small to carry a rate is refused rather than allowed "
                       "to explode into the cohort scale. Owner decision 2026-09-22 "
                       "(D3, growth = A)."),
            anchor_source=ANCHOR_ZERO,
            scale=cohort_scale(multiplier=1.0),
            notes=("input: (E_t - E_t-1) / |E_t-1|, base policy EBITDA_GROWTH_BASE_POLICY_V1",
                   "the cohort IQR is a dispersion of RATES: one IQR of spread scores "
                   "+76.2, two +96.4",),
        ),
        FactorSpec(
            feature_key="EBITDAAcceleration",
            normalization_type=ZERO_ANCHORED,
            economic_question="is EBITDA growth speeding up or slowing down?",
            rationale=("Zero means growth held steady -- a real state that must score "
                       "0. The input is the difference of two consecutive A-rates, "
                       "each under EBITDA_GROWTH_BASE_POLICY_V1, so it needs three "
                       "EBITDA observations and no asset base. The scale is "
                       "cohort-derived because a rate difference has its own "
                       "dispersion and a g borrowed from growth would be a guess. "
                       "Owner decision 2026-09-22 (D3, acceleration = A)."),
            anchor_source=ANCHOR_ZERO,
            scale=cohort_scale(multiplier=1.0),
            notes=("input: growth_t - growth_t-1 = (E_t - E_t-1)/|E_t-1| - "
                   "(E_t-1 - E_t-2)/|E_t-2|, base policy EBITDA_GROWTH_BASE_POLICY_V1 "
                   "on both rates",
                   "either rate refused under the base policy refuses the "
                   "acceleration",),
        ),
        FactorSpec(
            feature_key="EBITDAScale",
            normalization_type=PERCENTILE_RANK,
            economic_question="genuine economic scale versus the sector",
            rationale=("Rank the raw dollars. A rank is invariant to any strictly "
                       "monotone transform, so log1p is decorative here -- measured, "
                       "S(EBITDA) == S(log1p(EBITDA)) to the digit -- while being "
                       "undefined for the 37.8% of the universe with EBITDA <= 0."),
            anchor_source=ANCHOR_COHORT_RANK,
            notes=("the log is inert under a rank; it is not applied.",),
        ),
        FactorSpec(
            feature_key="RealRevenueGrowth",
            normalization_type=ZERO_ANCHORED,
            economic_question="is real growth positive?",
            rationale=("Under a rank the deflator is inert: nominal, minus-pi and "
                       "divided-by-(1+pi) score identically to the digit, so +4% "
                       "growth into 9% inflation still scores -33.333. Anchored at "
                       "zero, -5% real scores -46.2 and the subtraction finally "
                       "means something."),
            anchor_source=ANCHOR_ZERO,
            scale=fixed_scale(DEFAULT_REAL_GROWTH_SCALE_G),
            notes=("deflate over the GROWTH WINDOW, not by trailing CPI at the "
                   "as-of: the error is correlated with fiscal calendar and "
                   "mis-scores the 28% of the universe that is not a December filer.",),
        ),
        FactorSpec(
            feature_key="InterestCoverageRank",
            normalization_type=PERCENTILE_RANK,
            economic_question="how many times over is the interest bill covered, versus peers?",
            rationale=("operating_income / interest_expense (owner decision 2026-09-22) "
                       "is a times-covered multiple whose ORDERING within the sector "
                       "is the information: 3x against a cohort at 8x means "
                       "something, 3x alone does not. A rank is also indifferent to "
                       "the long right tail a tanh scale would have to clamp. "
                       "Direction HIGHER_IS_BETTER per FACTOR_DIRECTION_V1."),
            anchor_source=ANCHOR_COHORT_RANK,
            higher_is_better=True,
            notes=("DOMAIN POLICY pit_factor_spec.INTEREST_COVERAGE_DOMAIN_V1, executed "
                   "upstream in pit_replay.resolve_primitives: only interest_expense > 0 "
                   "produces a value; a tagged zero (interest_expense_zero), a negative "
                   "value (interest_expense_negative) and an untagged one "
                   "(no_interest_expense_at_operating_income_period) never reach this "
                   "rank, so a debt-free firm is UNAVAILABLE here and Q renormalises.",
                   "operating_income <= 0 with interest_expense > 0 is the FLOOR STATE "
                   "(INTEREST_COVERAGE_DOMAIN_V2, owner ruling R5 2026-09-23): the row is "
                   "scored at the rank floor (-100) with its raw coverage written, and it "
                   "is EXCLUDED from the rank population, so the rank runs over "
                   "positive-coverage peers only (R4). The v5 KNOWN LIMITATION -- an "
                   "inverted ordering among negative-OI rows -- is closed by that state: "
                   "no two negative-OI rows are ordered against each other.",),
        ),
        FactorSpec(
            feature_key="FCFConversionRank",
            normalization_type=PERCENTILE_RANK,
            economic_question="how much of the accounting earnings becomes spendable cash, versus peers?",
            rationale=("free_cash_flow / ebitda is a conversion RATE whose ORDERING within "
                       "the sector is the information: capital intensity differs so much "
                       "across industries that a fixed bar would mean a different thing in "
                       "software than in steel. Direction HIGHER_IS_BETTER per "
                       "FACTOR_DIRECTION_V1 (owner 2026-09-22); registered under owner "
                       "ruling R13 (2026-09-23) as a DIGESTED body, not an engine choice."),
            anchor_source=ANCHOR_COHORT_RANK,
            higher_is_better=True,
            notes=("DOMAIN POLICY pit_factor_spec.FCF_DOMAIN_V1, executed upstream in "
                   "pit_replay.resolve_primitives: EBITDA > 0 (ebitda_non_positive "
                   "otherwise); operating cash flow and capex from the EBITDA flow period; "
                   "a negative conversion is computed and ranks below every positive peer.",),
        ),
        FactorSpec(
            feature_key="NetDebtEBITDARank",
            normalization_type=PERCENTILE_RANK,
            economic_question="how many years of earnings would clear the debt, versus peers?",
            rationale=("net_debt / ebitda in YEARS; the frozen v1 core ranked it exactly "
                       "this way and FACTOR_DIRECTION_V1 pins LOWER_IS_BETTER (owner "
                       "2026-09-22). A rank is indifferent to the long right tail of "
                       "leverage that a tanh scale would have to clamp. Registered under "
                       "owner ruling R13 (2026-09-23)."),
            anchor_source=ANCHOR_COHORT_RANK,
            higher_is_better=False,
            notes=("DOMAIN POLICY pit_factor_spec.LEVERAGE_DOMAIN_V1 and DEBT_RUNG_GATE_V1, "
                   "executed upstream: EBITDA > 0; debt and cash AT the EBITDA period end; "
                   "the total_debt rung is the row's source_tag and its bound travels in "
                   "'db' (pit_policy v2 consumer rule); a REJECTED or UNDETERMINED rung "
                   "refuses by name. Net cash (negative net debt) is scored and ranks best.",),
        ),
        FactorSpec(
            feature_key="DebtMarketCapRank",
            normalization_type=PERCENTILE_RANK,
            economic_question="how much leverage sits under each dollar of equity value, versus peers?",
            rationale=("total_debt / market_cap; the v1 core's direction, LOWER_IS_BETTER, "
                       "pinned by FACTOR_DIRECTION_V1 (owner 2026-09-22). It imports market "
                       "price into a balance-sheet block by construction, which is why it "
                       "carries the smallest Q weight; the rank asks only where the burden "
                       "sits versus peers. Registered under owner ruling R13 (2026-09-23)."),
            anchor_source=ANCHOR_COHORT_RANK,
            higher_is_better=False,
            notes=("DOMAIN POLICY pit_factor_spec.MARKET_CAP_DOMAIN_V1: market cap on the raw "
                   "as-traded price (VALUATION_PRICE_POLICY_V1, PRICE_BASIS_TRANSLATION_V1) "
                   "times a DEFENSIBLE share count; debt AT the EBITDA period end under "
                   "DEBT_RUNG_GATE_V1. SURVIVOR_ONLY by construction.",),
        ),
    )
    return {spec.feature_key: spec for spec in specs}
