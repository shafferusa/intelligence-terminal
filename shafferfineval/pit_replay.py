"""
==============================================================================
pit_replay -- THE REPLAY ENGINE for the Shaffer v2 survivor-only diagnostic
==============================================================================

WHAT THIS IS

    The executable half of `spec_freeze_v3`. `pit_frozen_spec` says what the
    model is; `pit_replay_manifest` says whether it may run; `pit_replay_meter`
    says what it cost. This module is the thing that actually walks a cross-
    section, resolves every factor, normalises it, assembles the blocks, and
    writes `pit_feature`, `pit_pillar_score` and `pit_score`.

THE THREE RULES IT WAS BUILT AROUND

    1. THE MAIN STORE IS READ-ONLY, ALWAYS. `open_store()` is the only door
       and it opens `file:...?mode=ro` plus `PRAGMA query_only=1`. There is no
       second door. The store is 8.971 GiB on a volume that has already been
       filled once by a 9.3 GB WAL, and a writable handle on it is the single
       mistake this module must make impossible rather than merely avoid.

    2. AN ABSENCE IS DATA. Every factor gets a `pit_feature` row on every
       entity-date, including the ones that did not resolve, carrying the
       reason they did not. A factor that is skipped leaves no trace and is
       indistinguishable from a factor nobody thought of; a factor written
       UNAVAILABLE with `no_ebitda_period` is a measurement.

    3. NOTHING HERE MAY INVENT A NUMBER. Where the codebase does not declare
       how a factor is computed, this engine writes the refusal and names the
       missing declaration. It does not pick a reading, and it does not write
       0.0 for "unknown".

THE FACTOR PLAN, AND WHY FIVE OF THIRTEEN ARE COMPUTED

    Thirteen feature keys are written per entity-date: the ten company factors
    of `pit_factor_blocks.FACTOR_BLOCK` that are not V, plus V's three
    subfactors from `pit_valuation_spec.SUBFACTOR_WEIGHTS` -- because V
    declares its weights at subfactor granularity and a feature row at factor
    granularity could not carry a weight.

    FIVE are COMPUTABLE, and they are exactly the five for which
    `pit_normalization.candidate_v2_registry()` declares a transform:

        ebitda_growth        EBITDAGrowth        ZERO_ANCHORED, cohort scale
        ebitda_efficiency    EBITDAMarginRank    PERCENTILE_RANK
        ebitda_acceleration  EBITDAAcceleration  ZERO_ANCHORED, cohort scale
        ebitda_scale         EBITDAScale         PERCENTILE_RANK   (challenger)
        real_revenue_growth  RealRevenueGrowth   ZERO_ANCHORED, fixed g = 0.10

    ONE is REFUSED for ambiguity: `ebitda_benchmark`. The codebase holds two
    incompatible definitions of its benchmark -- `pit_factor_spec.
    ebitda_benchmark_cohort` gives the mean EBITDA in DOLLARS of the 50-75
    band, `pit_normalization`'s registered EBITDAExcessLevel anchors a MARGIN
    against `benchmark_margin_50_75`'s mean margin of the same band -- and no
    code picks between them. Measured on CTSH at 2019-06-28 the two are
    12,378,765 dollars against 0.1036 margin; under the dollar reading every
    large company saturates at the clamp and the factor becomes
    indistinguishable from `ebitda_scale`, the factor deliberately given zero
    weight. This is 0.1225 of the company score, the largest single factor
    weight in the model. Picking one silently is the one thing that must not
    happen, so the engine writes UNAVAILABLE with
    `benchmark_definition_ambiguous` on every row until an owner decides.

    SEVEN are NOT_COMPUTABLE, each with the missing declaration named:

        pe_absolute, pe_relative        earnings_per_share_pit is
                                        GENUINELY_UNAVAILABLE in this store --
                                        `pit_factor_spec` says so itself, in
                                        pe_ratio's `unavailable_primitives`.
        ev_ebitda_supplement,           no v2 normalisation is declared:
        fcf_conversion,                 `candidate_v2_registry()` holds six
        interest_coverage,              specs and none of them is these. The
        net_debt_ebitda,                ratio is arithmetic; the TRANSFORM is a
        debt_market_cap                 modelling decision nobody has recorded.

    The consequence is stated rather than hidden: with V and Q unavailable the
    reachable weight is E 0.35 + G 0.15 = 0.50, which clears
    MIN_BLOCK_WEIGHT = 0.40, so companies that resolve both blocks get a score
    and companies that resolve only one get the
    PARTIAL_SCORE_INSUFFICIENT_BLOCK_COVERAGE refusal. No block is ever
    renormalised against another.

THE ZERO-WEIGHT CHALLENGER

    `ebitda_scale` is FACTOR_ROLE SCORING_WEIGHT_ZERO_CHALLENGER. It is
    computed, normalised and written to `pit_feature` with
    `feature_role = 'SCORING_WEIGHT_ZERO_CHALLENGER'`, and it reaches nothing
    else: `pit_factor_blocks.within_block_weights()` omits it, so it cannot
    enter a numerator, a denominator, or an availability decision.
    `test_pit_replay.py` asserts that adding or removing it changes no block
    score, no coverage and no company score, to the digit.

POINT-IN-TIME DISCIPLINE

    Every fact comes from `pit_coverage.load_fact_index`, whose SQL carries
    `available_date <= as_of`, so every value in a cohort was knowable on the
    day. That is the assertion `pit_normalization.resolve_scale` allows a
    caller to make by passing no `value_dates`, and it is made here
    deliberately rather than by omission.

WHAT THIS ENGINE DOES NOT DO

    No ML fitting. No weight optimisation. No promotion of anything. It does
    not compute a sector overlay, so `pit_score.final_score` is NULL rather
    than a company score wearing the name of a Shaffer Score. It does not
    detect source-tag changes across dates, so `source_tag_changed` is written
    0 as a declared default and is listed in KNOWN_LIMITATIONS.

Stdlib only. No network.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import pit_coverage
import pit_factor_blocks
import pit_factor_spec
import pit_frozen_spec
import pit_intermediates
import pit_normalization
import pit_policy
import pit_replay_manifest
import pit_store
import pit_valuation_spec

__all__ = [
    "ENGINE_VERSION", "MODEL_VERSION", "SAMPLE_SCOPE", "AS_OF_GRID",
    "ReplayRefused", "FreeSpaceAbort",
    "VERDICT_COMPUTABLE", "VERDICT_REFUSED_AMBIGUOUS", "VERDICT_NOT_COMPUTABLE",
    "FactorPlan", "FACTOR_PLAN", "REPLAY_FEATURE_KEYS", "BLOCK_KEYS",
    "REASON_WHY", "ENGINE_REASONS", "KNOWN_LIMITATIONS", "plan_record",
    "Primitives", "resolve_primitives", "CohortCache", "normalise",
    "open_store", "require_gate", "ensure_pilot_db", "open_pilot",
    "seed_parents", "run_date", "run_pilot", "validate", "render", "main",
]

# ==========================================================================
# IDENTITY
# ==========================================================================

ENGINE_VERSION = "pit_replay/1.0"

#: On EVERY row this engine writes. Not a default, not a parameter with a
#: default -- a constant, because a row whose model version came from an
#: argument is a row whose model version can be wrong.
MODEL_VERSION = "equity_shaffer_v2_pit_SURVIVOR_ONLY_DIAGNOSTIC"

SAMPLE_SCOPE = pit_store.SAMPLE_SURVIVOR_ONLY          # 'SURVIVOR_ONLY_DIAGNOSTIC'

AS_OF_GRID = "month_end_2013_01_31..2026_09_18_165_dates"

#: The peer sets in the store were built under v1's model version; that is the
#: PEER SET's lineage, not the score's, and reading them under v2 is correct --
#: a cohort is a classification fact, not a model output. Named here so the
#: join is visible rather than buried in a default argument.
PEER_SET_MODEL_VERSION = pit_store.EQUITY_PIT_MODEL_VERSION
PEER_SET_VERSION = pit_store.PEER_SET_VERSION
LADDER_VERSION = pit_store.LADDER_VERSION_V2
LATENCY_POLICY_VERSION = pit_store.LATENCY_POLICY_VERSION
FACTOR_SPEC_VERSION = "factor_spec_v2"

GIB = 1024 ** 3

#: The per-date abort floor. Checked with `shutil.disk_usage` BEFORE every
#: date, never after. CONVENTION, not measurement.
FREE_FLOOR_BYTES = int(7.0 * GIB)

#: Writer pragmas, recorded on the run so a byte measurement can be read
#: alongside the settings that produced it.
PILOT_PRAGMAS: Tuple[Tuple[str, Any], ...] = (
    ("cache_size", -64000),        # 64 MiB of page cache; negative means KiB
    ("synchronous", "NORMAL"),     # the standard WAL durability setting
    ("temp_store", "FILE"),        # so a spill is VISIBLE to the temp meter
)


class ReplayRefused(RuntimeError):
    """The gate said no. Raised BEFORE any write connection is opened."""


class FreeSpaceAbort(RuntimeError):
    """Free space fell below the floor. The run stops and reports its position."""


# ==========================================================================
# (1) THE FACTOR PLAN -- declared, not inferred
# ==========================================================================

VERDICT_COMPUTABLE = "COMPUTABLE"
VERDICT_REFUSED_AMBIGUOUS = "REFUSED_AMBIGUOUS"
VERDICT_NOT_COMPUTABLE = "NOT_COMPUTABLE"

# -- the short-code vocabulary written into pit_feature.unavailable_reason ---
#
# SHORT ON PURPOSE. The long sentence lives once per run in
# pit_replay_run.summary_json; repeating it on ~100,000 rows per date would put
# the explanation into the storage budget the pilot exists to measure.

R_BENCHMARK_AMBIGUOUS = "benchmark_definition_ambiguous"
R_NO_V2_NORMALIZATION = "no_v2_normalization_declared"
R_PRIMITIVE_UNAVAILABLE = "primitive_genuinely_unavailable"
R_NO_PEER_SET = "no_peer_set"
R_NO_EBITDA = "no_ebitda_period"
R_NO_LAG1 = "no_ebitda_lag1_period"
R_NO_LAG2 = "no_ebitda_lag2_period"
R_NO_ASSETS_LAG1 = "no_assets_at_lag1"
R_NO_ASSETS_LAG2 = "no_assets_at_lag2"
R_NO_REVENUE_AT_P = "no_revenue_at_ebitda_period"
R_NO_REVENUE = "no_revenue_period"
R_NO_REVENUE_LAG1 = "no_revenue_lag1_period"
R_NO_CPI = "no_cpi_vintage"
R_NON_POSITIVE_BASE = "non_positive_real_revenue_base"

#: Reasons this engine itself can emit. Cohort refusals add
#: `pit_factor_spec`'s vocabulary and normalisation refusals add
#: `pit_normalization`'s; both are recorded VERBATIM rather than remapped,
#: because a reason that has been translated has been edited.
ENGINE_REASONS: Tuple[str, ...] = (
    R_BENCHMARK_AMBIGUOUS, R_NO_V2_NORMALIZATION, R_PRIMITIVE_UNAVAILABLE,
    R_NO_PEER_SET, R_NO_EBITDA, R_NO_LAG1, R_NO_LAG2, R_NO_ASSETS_LAG1,
    R_NO_ASSETS_LAG2, R_NO_REVENUE_AT_P, R_NO_REVENUE, R_NO_REVENUE_LAG1,
    R_NO_CPI, R_NON_POSITIVE_BASE,
)

REASON_WHY: Dict[str, str] = {
    R_BENCHMARK_AMBIGUOUS: (
        "TWO INCOMPATIBLE DEFINITIONS, NOT MISSING DATA. "
        "pit_factor_spec.ebitda_benchmark_cohort -> benchmark_band returns the "
        "MEAN EBITDA IN DOLLARS of the 50-75 band, which is consistent with "
        "ebitda_benchmark's required_primitives (operating_income, "
        "depreciation_amortisation) and with its EBITDA_ONLY eligibility rule. "
        "pit_normalization's registered EBITDAExcessLevel declares "
        "anchor_source=ANCHOR_COHORT_50_75_MEAN_MARGIN and pairs with "
        "benchmark_margin_50_75, which returns the MEAN MARGIN of the same "
        "band and needs revenue for the target AND for every band member -- "
        "revenue is in neither required_primitives nor the eligibility rule. "
        "Measured on CTSH at 2019-06-28: 12,378,764.67 dollars against an own "
        "EBITDA of 3,299,000,000, versus M_s = 0.10360 against an own margin "
        "of 0.2046. Under the dollar reading any cohort-derived scale "
        "saturates every large company at the clamp and the factor becomes "
        "indistinguishable from ebitda_scale -- the factor deliberately given "
        "ZERO weight. This factor carries 0.1225 of the company score, the "
        "largest single factor weight in the model. The engine REFUSES rather "
        "than choosing; an owner decides, and the decision is a spec change."),
    R_NO_V2_NORMALIZATION: (
        "pit_normalization.candidate_v2_registry() declares six specs -- "
        "EBITDAMarginRank, EBITDAExcessLevel, EBITDAGrowth, "
        "EBITDAAcceleration, EBITDAScale, RealRevenueGrowth -- and this key is "
        "not among them. pit_factor_spec declares the factor's PRIMITIVES and "
        "its PEER ELIGIBILITY but sets `normalization` to None on every spec, "
        "so there is no transform to apply. The ratio is arithmetic; the "
        "transform is a modelling decision, and inventing one at run time is "
        "exactly the undefended assignment this project refuses elsewhere."),
    R_PRIMITIVE_UNAVAILABLE: (
        "pit_factor_spec.spec('pe_ratio').peer_eligibility names "
        "earnings_per_share_pit in `unavailable_primitives`: EPS IS NOT IN "
        "THIS STORE. pit_dera.TAG_FILTER is built from the concept ladders and "
        "no ladder names EarningsPerShareDiluted or EarningsPerShareBasic, so "
        "every EPS row in 72 DERA quarters was read and dropped at ingest. "
        "The store's own declaration, not an inference from an empty query."),
    R_NO_PEER_SET: (
        "pit_coverage.stored_cohorts found no stored peer set containing this "
        "entity at this date with at least pit_coverage.MIN_COHORT_N = 12 "
        "members on any rung of the sic4/sic3/sic2/office ladder."),
    R_NO_EBITDA: (
        "No period yields EBITDA = OperatingIncomeLoss + D&A at a matched "
        "(period_end, qtrs=4), or the newest such period is stale under "
        "pit_policy.is_stale(period, as_of, 4, 'operating_income')."),
    R_NO_LAG1: "pit_coverage.nearest_period found no EBITDA period one year before P.",
    R_NO_LAG2: "pit_coverage.nearest_period found no EBITDA period two years before P.",
    R_NO_ASSETS_LAG1: (
        "No non-zero Assets instant (qtrs=0) within tolerance of the lag-1 "
        "period. EBITDAGrowth's declared input divides by Assets_{t-4q}, so "
        "without it there is no input, only a numerator."),
    R_NO_ASSETS_LAG2: (
        "No non-zero Assets instant within tolerance of the lag-2 period. "
        "EBITDAAcceleration divides by Assets_{t-8q} -- ONE common base, "
        "because two different bases give any asset-growing company a "
        "systematic negative acceleration."),
    R_NO_REVENUE_AT_P: (
        "The revenue ladder resolves no non-zero value AT the EBITDA period. "
        "A margin is a ratio of one period's numbers; revenue from a "
        "neighbouring year is a different company-year."),
    R_NO_REVENUE: (
        "pit_coverage.resolve_concept walked the whole revenue ladder and no "
        "rung carried a period that survives pit_policy.is_stale(period, "
        "as_of, 4, 'revenue'). An older period of the same tag is the same "
        "stale number, so there is no fallback to take."),
    R_NO_REVENUE_LAG1: (
        "No revenue period one year before the resolved revenue period ON THE "
        "SAME RUNG. A growth rate differenced across two tags is a measurement "
        "discontinuity wearing the shape of a business change."),
    R_NO_CPI: (
        "pit_coverage.cpi_index_as_of returned no CPIAUCSL vintage knowable on "
        "the as-of date for one or both ends of the growth window. The "
        "deflator is taken OVER THE WINDOW, not as trailing CPI at the as-of."),
    R_NON_POSITIVE_BASE: (
        "Real revenue at the lag-1 end of the window is not positive, so the "
        "growth rate has no denominator. A rate on a non-positive base is not "
        "a large number, it is an undefined one."),
}


@dataclass(frozen=True)
class FactorPlan:
    """What the engine will do with one feature key, decided in advance."""

    key: str
    block: str
    role: str
    verdict: str
    #: The `pit_normalization.candidate_v2_registry()` key, whose vocabulary is
    #: deliberately NOT pit_factor_blocks'. The mapping is declared here
    #: because two modules naming one factor differently is exactly where a
    #: wrong transform would hide.
    norm_key: Optional[str] = None
    #: The `pit_factor_spec` key whose eligibility rule governs the cohort.
    spec_key: Optional[str] = None
    needs_cohort: bool = False
    #: Whether the cohort enters the transform as a RANK population or as a
    #: DISPERSION sample. They are not the same set: a rank excludes the target
    #: (percentile_rank_within counts it back in), a dispersion does not.
    cohort_use: str = ""
    refusal_reason: Optional[str] = None
    mask_code: str = ""
    note: str = ""

    @property
    def computable(self) -> bool:
        return self.verdict == VERDICT_COMPUTABLE

    def as_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key, "block": self.block, "role": self.role,
            "verdict": self.verdict, "norm_key": self.norm_key,
            "spec_key": self.spec_key, "needs_cohort": self.needs_cohort,
            "cohort_use": self.cohort_use,
            "refusal_reason": self.refusal_reason,
            "refusal_why": (REASON_WHY.get(self.refusal_reason)
                            if self.refusal_reason else None),
            "mask_code": self.mask_code, "note": self.note,
        }


COHORT_RANK = "rank_population_excludes_target"
COHORT_DISPERSION = "dispersion_sample_includes_target"

_B = pit_factor_blocks

FACTOR_PLAN: Dict[str, FactorPlan] = {
    # ---- E ---------------------------------------------------------------
    "ebitda_benchmark": FactorPlan(
        key="ebitda_benchmark", block=_B.BLOCK_E, role=_B.ROLE_SCORING,
        verdict=VERDICT_REFUSED_AMBIGUOUS, norm_key="EBITDAExcessLevel",
        spec_key="ebitda_benchmark", needs_cohort=True,
        refusal_reason=R_BENCHMARK_AMBIGUOUS, mask_code="BENCH",
        note=("0.35 within E = 0.1225 of the company score, the largest single "
              "factor weight. Refused, not skipped: the row is written so the "
              "refusal is in the data.")),
    "ebitda_growth": FactorPlan(
        key="ebitda_growth", block=_B.BLOCK_E, role=_B.ROLE_SCORING,
        verdict=VERDICT_COMPUTABLE, norm_key="EBITDAGrowth",
        spec_key="ebitda_growth", needs_cohort=True,
        cohort_use=COHORT_DISPERSION, mask_code="GROW",
        note="input (E_t - E_{t-4q}) / Assets_{t-4q}, declared by the registry"),
    "ebitda_efficiency": FactorPlan(
        key="ebitda_efficiency", block=_B.BLOCK_E, role=_B.ROLE_SCORING,
        verdict=VERDICT_COMPUTABLE, norm_key="EBITDAMarginRank",
        spec_key="ebitda_efficiency", needs_cohort=True,
        cohort_use=COHORT_RANK, mask_code="EFF",
        note=("input EBITDA / revenue AT THE SAME period. The registry's key is "
              "'EBITDAMarginRank' and the block map's is 'ebitda_efficiency'; "
              "the correspondence is declared here and nowhere else.")),
    "ebitda_acceleration": FactorPlan(
        key="ebitda_acceleration", block=_B.BLOCK_E, role=_B.ROLE_SCORING,
        verdict=VERDICT_COMPUTABLE, norm_key="EBITDAAcceleration",
        spec_key="ebitda_acceleration", needs_cohort=True,
        cohort_use=COHORT_DISPERSION, mask_code="ACC",
        note="input (E_t - 2E_{t-4q} + E_{t-8q}) / Assets_{t-8q}, ONE base"),
    "ebitda_scale": FactorPlan(
        key="ebitda_scale", block=_B.BLOCK_E,
        role=_B.ROLE_ZERO_WEIGHT_CHALLENGER,
        verdict=VERDICT_COMPUTABLE, norm_key="EBITDAScale",
        spec_key="ebitda_scale", needs_cohort=True,
        cohort_use=COHORT_RANK, mask_code="SCALE",
        note=("OBSERVED, NEVER SCORED. Written to pit_feature with "
              "feature_role=SCORING_WEIGHT_ZERO_CHALLENGER and excluded from "
              "every numerator, denominator and availability test by "
              "pit_factor_blocks.within_block_weights().")),

    # ---- V, at SUBFACTOR granularity because that is where V's weights live -
    "pe_absolute": FactorPlan(
        key="pe_absolute", block=_B.BLOCK_V, role=_B.ROLE_SCORING,
        verdict=VERDICT_NOT_COMPUTABLE, spec_key="pe_ratio",
        refusal_reason=R_PRIMITIVE_UNAVAILABLE, mask_code="PE"),
    "pe_relative": FactorPlan(
        key="pe_relative", block=_B.BLOCK_V, role=_B.ROLE_SCORING,
        verdict=VERDICT_NOT_COMPUTABLE, spec_key="pe_ratio",
        refusal_reason=R_PRIMITIVE_UNAVAILABLE, mask_code="PEPeer"),
    "ev_ebitda_supplement": FactorPlan(
        key="ev_ebitda_supplement", block=_B.BLOCK_V, role=_B.ROLE_SCORING,
        verdict=VERDICT_NOT_COMPUTABLE, spec_key="ev_ebitda",
        refusal_reason=R_NO_V2_NORMALIZATION, mask_code="EVEBITDA",
        note=("Six primitives DO resolve in principle -- price, defensible "
              "shares, total_debt, cash and EBITDA -- but no v2 transform is "
              "declared for the resulting multiple, so there is nothing to "
              "apply. The primitive chain is the second obstacle, not the "
              "first.")),

    # ---- G ---------------------------------------------------------------
    "real_revenue_growth": FactorPlan(
        key="real_revenue_growth", block=_B.BLOCK_G, role=_B.ROLE_SCORING,
        verdict=VERDICT_COMPUTABLE, norm_key="RealRevenueGrowth",
        spec_key="real_revenue_growth", needs_cohort=False,
        cohort_use="", mask_code="RRG",
        note=("ZERO_ANCHORED at a FIXED g = 0.10, so no cohort enters the "
              "transform and no peer gate applies. G has a genuine "
              "single-point dependency: without this factor there is no G.")),

    # ---- Q ---------------------------------------------------------------
    "fcf_conversion": FactorPlan(
        key="fcf_conversion", block=_B.BLOCK_Q, role=_B.ROLE_SCORING,
        verdict=VERDICT_NOT_COMPUTABLE, spec_key="fcf_conversion",
        refusal_reason=R_NO_V2_NORMALIZATION, mask_code="FCF"),
    "interest_coverage": FactorPlan(
        key="interest_coverage", block=_B.BLOCK_Q, role=_B.ROLE_SCORING,
        verdict=VERDICT_NOT_COMPUTABLE, spec_key="interest_coverage",
        refusal_reason=R_NO_V2_NORMALIZATION, mask_code="ICOV"),
    "net_debt_ebitda": FactorPlan(
        key="net_debt_ebitda", block=_B.BLOCK_Q, role=_B.ROLE_SCORING,
        verdict=VERDICT_NOT_COMPUTABLE, spec_key="net_debt_ebitda",
        refusal_reason=R_NO_V2_NORMALIZATION, mask_code="NDE"),
    "debt_market_cap": FactorPlan(
        key="debt_market_cap", block=_B.BLOCK_Q, role=_B.ROLE_SCORING,
        verdict=VERDICT_NOT_COMPUTABLE, spec_key="debt_market_cap",
        refusal_reason=R_NO_V2_NORMALIZATION, mask_code="DMC"),
}

#: Canonical write order. Load-bearing the same way a signature's order is: the
#: row set for one entity-date must have exactly one spelling.
REPLAY_FEATURE_KEYS: Tuple[str, ...] = (
    "ebitda_benchmark", "ebitda_growth", "ebitda_efficiency",
    "ebitda_acceleration", "ebitda_scale",
    "pe_absolute", "pe_relative", "ev_ebitda_supplement",
    "real_revenue_growth",
    "fcf_conversion", "interest_coverage", "net_debt_ebitda",
    "debt_market_cap",
)

#: block -> the keys this engine writes for it, in canonical order.
BLOCK_KEYS: Dict[str, Tuple[str, ...]] = {
    b: tuple(k for k in REPLAY_FEATURE_KEYS if FACTOR_PLAN[k].block == b)
    for b in pit_factor_blocks.BLOCKS
}

KNOWN_LIMITATIONS: Tuple[str, ...] = (
    "feature_available_date is written as the as-of date. That is the LATEST "
    "legal value under the schema's CHECK (feature_available_date <= "
    "as_of_date) and is therefore conservative, but it is NOT the earliest "
    "date the feature was knowable. pit_coverage.load_fact_index discards "
    "available_date when it flattens vintages, so a tighter bound needs a "
    "second pass over pit_fact. Not measured, and not claimed.",
    "source_tag_changed is written 0 on every row. The column is NOT NULL and "
    "this engine does not compare a date's resolved rung against the previous "
    "date's, so 0 here means NOT DETECTED, not KNOWN UNCHANGED.",
    "pit_score.final_score is NULL. ShafferScore = CompanyScore + SectorScore "
    "and no sector overlay is computed here, so writing the company score into "
    "final_score would be a company score wearing the name of a final one.",
    "listing_id is NULL on every row. No price-dependent factor is computed, "
    "so no listing is resolved, and a listing id guessed from the entity would "
    "be an invention.",
    "fact_max_age_days is (as_of - newest source period_end) in days, which is "
    "the age of the FISCAL PERIOD, not of the filing.",
    "Eight of thirteen feature keys are UNAVAILABLE by construction on every "
    "row: one refused for ambiguity, seven for a missing declaration. The "
    "reachable block weight is therefore E 0.35 + G 0.15 = 0.50.",
    "The peer sets read from pit_peer_set carry model_version "
    "'equity_shaffer_v1_pit'. That is the COHORT's lineage, not the score's; "
    "no v2 peer set exists and building one would be a model change.",
)


def plan_record() -> Dict[str, Any]:
    """The whole declared plan, for the run row to store. Reads nothing."""
    counts: Dict[str, int] = {}
    for plan in FACTOR_PLAN.values():
        counts[plan.verdict] = counts.get(plan.verdict, 0) + 1
    reachable = 0.0
    for block in pit_factor_blocks.BLOCKS:
        if any(FACTOR_PLAN[k].computable
               and FACTOR_PLAN[k].role == pit_factor_blocks.ROLE_SCORING
               for k in BLOCK_KEYS[block]):
            reachable += pit_factor_blocks.block_weight(block)
    return {
        "engine_version": ENGINE_VERSION,
        "model_version": MODEL_VERSION,
        "sample_scope": SAMPLE_SCOPE,
        "feature_keys": list(REPLAY_FEATURE_KEYS),
        "n_feature_keys": len(REPLAY_FEATURE_KEYS),
        "verdict_counts": counts,
        "factors": {k: FACTOR_PLAN[k].as_dict() for k in REPLAY_FEATURE_KEYS},
        "reason_vocabulary": {r: REASON_WHY.get(r, "") for r in ENGINE_REASONS},
        "block_policy": pit_factor_blocks.policy_record(),
        "reachable_block_weight": reachable,
        "min_block_weight": pit_factor_blocks.MIN_BLOCK_WEIGHT,
        "known_limitations": list(KNOWN_LIMITATIONS),
        "pilot_pragmas": {k: v for k, v in PILOT_PRAGMAS},
    }


# ==========================================================================
# (2) CONNECTIONS -- one door in, one door out
# ==========================================================================

def open_store(db_path: str = pit_store.DEFAULT_PIT_DB_PATH) -> sqlite3.Connection:
    """The ONLY way this module touches the main store. Read-only, twice over.

    `mode=ro` makes the file handle read-only and `query_only=1` makes the
    connection refuse a write even if something later ATTACHes. Belt and
    braces on a volume that has already been filled once.
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(db_path)
    safe = db_path.replace("\\", "/").replace("?", "%3f").replace("#", "%23")
    conn = sqlite3.connect("file:" + safe + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=1")
    return conn


def require_gate(snap: Optional[Dict[str, Any]] = None) -> List[str]:
    """`pit_replay_manifest.gate()` or a refusal. CALLED BEFORE ANY WRITER.

    A rolled-back transaction has already allocated a WAL, and a WAL is the
    failure mode this volume has suffered. So the gate is evaluated while the
    only open handle is read-only, and a refusal raises before a writer exists.
    """
    ok, reasons = pit_replay_manifest.gate(snap)
    if not ok:
        raise ReplayRefused(
            "REPLAY REFUSED -- no write transaction was opened:\n  - "
            + "\n  - ".join(reasons))
    return list(reasons)


def ensure_pilot_db(path: str, *, overwrite: bool = True) -> Dict[str, Any]:
    """Create the isolated pilot database from the declaring modules' own DDL."""
    import pit_replay_meter
    pit_replay_meter.assert_write_allowed(path)
    return pit_replay_meter.build_pilot_db(path, overwrite=overwrite)


def open_pilot(path: str) -> sqlite3.Connection:
    """A writer on the PILOT database only. Refuses a protected basename."""
    import pit_replay_meter
    pit_replay_meter.assert_write_allowed(path)
    conn = pit_store.connect(path)
    for name, value in PILOT_PRAGMAS:
        conn.execute("PRAGMA %s = %s" % (name, value))
    return conn


def seed_parents(pilot_conn: sqlite3.Connection, store_conn: sqlite3.Connection,
                 dates: Sequence[str]) -> Dict[str, Any]:
    """Copy the FK parent rows the replay needs. SEED_PARENTS_THEN_REBASELINE.

    `pit_store.connect()` sets `PRAGMA foreign_keys = ON`, and on a fresh pilot
    DB the first `pit_feature` insert fails against empty parents.
    `pit_replay_meter.pilot_fk_requirements` names the two honest options; this
    is the first, chosen so the pilot's referential integrity checks are REAL.
    Running with foreign keys off would make the pilot silent about exactly the
    constraint the full run must satisfy.

    Only two parents are seeded, because only two are reached: `pit_entity`
    (every written table points at it) and `pit_peer_set` (pit_feature and
    pit_pillar_score carry a peer_set_id). `pit_listing` is not seeded because
    listing_id is NULL on every row this engine writes, and a NULL child column
    is not FK-checked.

    Call `Meter.rebaseline()` after this: the seed is a one-time cost and is
    not part of BytesPerEntityDate.
    """
    t0 = time.monotonic()
    entities = store_conn.execute(
        "SELECT entity_id, cik, first_filing_date, last_filing_date, "
        "entity_kind, created_at FROM pit_entity").fetchall()
    pilot_conn.executemany(
        "INSERT OR IGNORE INTO pit_entity (entity_id, cik, first_filing_date, "
        "last_filing_date, entity_kind, created_at) VALUES (?,?,?,?,?,?)",
        [tuple(r) for r in entities])

    days = sorted({str(d)[:10] for d in dates})
    marks = ",".join("?" * len(days))
    peer_sets = store_conn.execute(
        "SELECT peer_set_id, as_of_date, rung, key, n_members, "
        "n_price_resolvable, peer_set_version, crosswalk_hash, model_version, "
        "created_at FROM pit_peer_set WHERE as_of_date IN (%s)" % marks,
        days).fetchall()
    pilot_conn.executemany(
        "INSERT OR IGNORE INTO pit_peer_set (peer_set_id, as_of_date, rung, "
        "key, n_members, n_price_resolvable, peer_set_version, crosswalk_hash, "
        "model_version, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [tuple(r) for r in peer_sets])
    pilot_conn.commit()
    return {
        "option": "SEED_PARENTS_THEN_REBASELINE",
        "pit_entity_rows": len(entities),
        "pit_peer_set_rows": len(peer_sets),
        "pit_peer_set_dates": days,
        "pit_listing_rows": 0,
        "pit_listing_why": ("listing_id is NULL on every written row, and a "
                            "NULL child column is not FK-checked"),
        "foreign_keys": "ON",
        "elapsed_s": round(time.monotonic() - t0, 3),
    }


# ==========================================================================
# (3) PRIMITIVES -- one entity's point-in-time facts
# ==========================================================================

@dataclass
class Primitives:
    """Everything the five computable factors need, resolved once per entity."""

    entity_id: int
    ebitda_period: Optional[str] = None
    ebitda: Optional[float] = None
    lag1_period: Optional[str] = None
    ebitda_lag1: Optional[float] = None
    lag2_period: Optional[str] = None
    ebitda_lag2: Optional[float] = None
    assets_lag1_period: Optional[str] = None
    assets_lag1: Optional[float] = None
    assets_lag2_period: Optional[str] = None
    assets_lag2: Optional[float] = None
    revenue_at_ebitda_period: Optional[float] = None
    revenue_rung: Optional[str] = None
    revenue_period: Optional[str] = None
    revenue: Optional[float] = None
    revenue_lag1_period: Optional[str] = None
    revenue_lag1: Optional[float] = None
    cpi_t: Optional[float] = None
    cpi_lag1: Optional[float] = None

    # derived factor inputs, None where an ingredient is missing
    margin: Optional[float] = None
    growth: Optional[float] = None
    acceleration: Optional[float] = None
    real_revenue_growth: Optional[float] = None

    #: derived key -> the short code saying which ingredient failed. Never a
    #: zero: "no value" and "a value of zero" are different facts.
    why: Dict[str, str] = field(default_factory=dict)


def _finite(value: Any) -> bool:
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def resolve_primitives(index: Mapping[Tuple[int, str, int], Mapping[str, float]],
                       entity_id: int, as_of: str,
                       ladders: Mapping[str, Any],
                       cpi: Any) -> Primitives:
    """One entity's facts and the four derived inputs.

    NO DATABASE ACCESS except the CPI lookup, which `cpi` memoises by reference
    date. Everything else is answered from the in-memory fact index.

    The resolution rules are `pit_coverage`'s, reached through its own
    functions rather than restated: `_ebitda_periods` for the assembly,
    `is_stale` for the gate, `nearest_period` for the lags, `concept_periods`
    for a LEVEL (revenue at the EBITDA period) and `rung_periods` for a GROWTH
    RATE (revenue on ONE rung at two dates). The level/growth distinction is
    not a nicety: differencing two rungs is a measurement discontinuity wearing
    the shape of a business change.
    """
    out = Primitives(entity_id=int(entity_id))
    day = str(as_of)[:10]

    periods = pit_coverage._ebitda_periods(index, entity_id, ladders)
    current: Optional[str] = None
    if periods:
        newest = max(periods)
        if not pit_policy.is_stale(newest, day, 4, "operating_income"):
            current = newest
    out.ebitda_period = current
    out.ebitda = periods.get(current) if current else None
    if current is None:
        out.why["ebitda"] = R_NO_EBITDA

    if current:
        out.lag1_period = pit_coverage.nearest_period(periods, current, 1)
        out.ebitda_lag1 = (periods.get(out.lag1_period)
                           if out.lag1_period else None)
        out.lag2_period = pit_coverage.nearest_period(periods, current, 2)
        out.ebitda_lag2 = (periods.get(out.lag2_period)
                           if out.lag2_period else None)

        assets_map = index.get((entity_id, "Assets", 0)) or {}
        if out.lag1_period:
            out.assets_lag1_period = pit_coverage.nearest_period(
                assets_map, out.lag1_period, 0)
            out.assets_lag1 = (assets_map.get(out.assets_lag1_period)
                               if out.assets_lag1_period else None)
        if out.lag2_period:
            out.assets_lag2_period = pit_coverage.nearest_period(
                assets_map, out.lag2_period, 0)
            out.assets_lag2 = (assets_map.get(out.assets_lag2_period)
                               if out.assets_lag2_period else None)

        # A LEVEL: the best rung available for THAT year.
        revenue_tags = [r.tags[0] for r in ladders["revenue"].rungs
                        if r.eligible(day)]
        out.revenue_at_ebitda_period = pit_coverage.concept_periods(
            index, entity_id, revenue_tags, 4).get(current)

    rung, period, value = pit_coverage.resolve_concept(
        index, entity_id, "revenue", day, ladders)
    out.revenue_rung, out.revenue_period, out.revenue = rung, period, value
    if period:
        # A GROWTH RATE: the SAME rung in both years.
        revenue_map = pit_coverage.rung_periods(index, entity_id, rung, 4)
        out.revenue_lag1_period = pit_coverage.nearest_period(revenue_map, period, 1)
        out.revenue_lag1 = (revenue_map.get(out.revenue_lag1_period)
                            if out.revenue_lag1_period else None)

    # ---- the four derived inputs, each with its own refusal ---------------

    # margin = EBITDA / revenue AT THE SAME PERIOD
    if out.ebitda is None:
        out.why["margin"] = R_NO_EBITDA
    elif not out.revenue_at_ebitda_period:
        out.why["margin"] = R_NO_REVENUE_AT_P
    else:
        out.margin = float(out.ebitda) / float(out.revenue_at_ebitda_period)

    # growth = (E_t - E_{t-4q}) / Assets_{t-4q}
    if out.ebitda is None:
        out.why["growth"] = R_NO_EBITDA
    elif out.ebitda_lag1 is None:
        out.why["growth"] = R_NO_LAG1
    elif not out.assets_lag1:
        out.why["growth"] = R_NO_ASSETS_LAG1
    else:
        out.growth = ((float(out.ebitda) - float(out.ebitda_lag1))
                      / float(out.assets_lag1))

    # acceleration = (E_t - 2E_{t-4q} + E_{t-8q}) / Assets_{t-8q}
    if out.ebitda is None:
        out.why["acceleration"] = R_NO_EBITDA
    elif out.ebitda_lag1 is None:
        out.why["acceleration"] = R_NO_LAG1
    elif out.ebitda_lag2 is None:
        out.why["acceleration"] = R_NO_LAG2
    elif not out.assets_lag2:
        out.why["acceleration"] = R_NO_ASSETS_LAG2
    else:
        out.acceleration = ((float(out.ebitda) - 2.0 * float(out.ebitda_lag1)
                             + float(out.ebitda_lag2)) / float(out.assets_lag2))

    # real revenue growth, deflated OVER THE WINDOW
    if out.revenue_period is None:
        out.why["real_revenue_growth"] = R_NO_REVENUE
    elif out.revenue_lag1 is None:
        out.why["real_revenue_growth"] = R_NO_REVENUE_LAG1
    else:
        out.cpi_t = cpi(out.revenue_period)
        out.cpi_lag1 = cpi(out.revenue_lag1_period)
        if not out.cpi_t or not out.cpi_lag1:
            out.why["real_revenue_growth"] = R_NO_CPI
        else:
            base = float(out.revenue_lag1) / float(out.cpi_lag1)
            if base <= 0.0:
                out.why["real_revenue_growth"] = R_NON_POSITIVE_BASE
            else:
                now = float(out.revenue) / float(out.cpi_t)
                out.real_revenue_growth = now / base - 1.0
    return out


#: factor key -> (the Primitives attribute holding its raw value, the `why` key
#: holding its refusal). Declared rather than derived from the name, so a
#: rename cannot silently disconnect a value from its reason.
RAW_FIELD: Dict[str, Tuple[str, str]] = {
    "ebitda_benchmark": ("ebitda", "ebitda"),
    "ebitda_scale": ("ebitda", "ebitda"),
    "ebitda_efficiency": ("margin", "margin"),
    "ebitda_growth": ("growth", "growth"),
    "ebitda_acceleration": ("acceleration", "acceleration"),
    "real_revenue_growth": ("real_revenue_growth", "real_revenue_growth"),
}

#: factor key -> the primitives it consumed, for `sources_json`. COMPACT on
#: purpose: this string is written on every row and its bytes land in the
#: budget the pilot exists to measure.
SOURCE_CONCEPTS: Dict[str, Tuple[str, ...]] = {
    "ebitda_scale": ("oi", "da"),
    "ebitda_benchmark": ("oi", "da"),
    "ebitda_efficiency": ("oi", "da", "rev@P"),
    "ebitda_growth": ("oi", "da", "assets"),
    "ebitda_acceleration": ("oi", "da", "assets"),
    "real_revenue_growth": ("rev", "cpi"),
}


# ==========================================================================
# (4) COHORTS -- resolved once per (peer set, factor), not once per company
# ==========================================================================

class CohortCache:
    """Per-date cohort vectors, memoised by (peer_set_id, factor key).

    Two entities in one peer set share one cohort, and `eligible_peers` costs a
    few SELECTs per member. Memoising turns 8,000 x 4 resolutions into roughly
    200 x 4, and the result is identical by construction because the inputs are.
    """

    def __init__(self, conn: sqlite3.Connection, as_of: str,
                 primitives: Mapping[int, Primitives]) -> None:
        self.conn = conn
        self.as_of = str(as_of)[:10]
        self.primitives = primitives
        self._cache: Dict[Tuple[int, str], Dict[str, Any]] = {}
        self.n_eligible_calls = 0

    def get(self, peer_set_id: int, rung: str, members: Sequence[int],
            factor_key: str) -> Dict[str, Any]:
        cache_key = (int(peer_set_id), factor_key)
        hit = self._cache.get(cache_key)
        if hit is not None:
            return hit
        plan = FACTOR_PLAN[factor_key]
        spec_key = plan.spec_key or factor_key
        self.n_eligible_calls += 1
        eligible = pit_factor_spec.eligible_peers(
            self.conn, spec_key, self.as_of, list(members), rung=rung,
            version=FACTOR_SPEC_VERSION)
        attr = RAW_FIELD[factor_key][0]
        values: Dict[int, float] = {}
        for member in eligible.members:
            prim = self.primitives.get(int(member))
            if prim is None:
                continue
            value = getattr(prim, attr)
            if _finite(value):
                values[int(member)] = float(value)
        record = {
            "peer_set_id": int(peer_set_id),
            "rung": rung,
            "availability": eligible.availability,
            "reason": eligible.reason,
            "n_offered": eligible.n_offered,
            "n_eligible": eligible.n_eligible,
            "values": values,
            "n_values": len(values),
        }
        self._cache[cache_key] = record
        return record


# ==========================================================================
# (5) NORMALISATION -- the transform the FACTOR declared, not the caller
# ==========================================================================

_REGISTRY = pit_normalization.candidate_v2_registry()


def normalise(factor_key: str, value: Any, cohort_values: Mapping[int, float],
              entity_id: int, as_of: str) -> pit_normalization.NormResult:
    """Apply the registered v2 transform for one factor.

    The rank population EXCLUDES the target, because `percentile_rank_within`
    counts it back in; the dispersion sample INCLUDES it, because a cohort's
    spread is a property of the cohort, target and all. The two sets are
    different and the difference is declared in `FactorPlan.cohort_use` rather
    than left to be inferred from a slice.

    No `value_dates` are passed: every value in `cohort_values` came from
    `pit_coverage.load_fact_index`, whose SQL carries `available_date <=
    as_of`, so the cohort IS as-of by construction. That is the assertion
    `pit_normalization.resolve_scale` documents, made deliberately.
    """
    plan = FACTOR_PLAN[factor_key]
    spec = _REGISTRY[plan.norm_key]
    if plan.cohort_use == COHORT_RANK:
        peers = [v for e, v in cohort_values.items() if int(e) != int(entity_id)]
        return pit_normalization.normalize(spec, value, peer_values=peers,
                                           as_of_date=as_of)
    if plan.cohort_use == COHORT_DISPERSION:
        return pit_normalization.normalize(
            spec, value, cohort_values=list(cohort_values.values()),
            as_of_date=as_of)
    return pit_normalization.normalize(spec, value, as_of_date=as_of)


# ==========================================================================
# (6) ROW BUILDING
# ==========================================================================

FEATURE_COLUMNS = (
    "entity_id", "listing_id", "as_of_date", "feature_key", "feature_role",
    "sources_json", "source_tag", "ladder_version", "source_tag_changed",
    "fact_max_age_days", "feature_available_date", "latency_policy_version",
    "raw_value", "normalized_value", "norm_method", "peer_set_id",
    "availability", "available_flag", "unavailable_reason", "model_version",
    "replay_run_id", "created_at", "resolved_inputs_json",
)

PILLAR_COLUMNS = (
    "entity_id", "as_of_date", "pillar", "model_version", "replay_run_id",
    "pillar_score", "original_weight", "effective_weight", "availability",
    "unavailable_reason", "n_subfactors_declared", "n_subfactors_resolved",
    "subfactors_json", "subfactor_mask", "peer_set_id", "benchmark_level",
    "n_peers", "sample_scope", "model_state_version", "created_at",
)

SCORE_COLUMNS = (
    "entity_id", "listing_id", "as_of_date", "model_version", "replay_run_id",
    "latency_policy_version", "peer_set_version", "ladder_version",
    "company_score", "sector_overlay", "political_overlay", "final_score",
    "classification", "benchmark_level", "valuation_benchmark_level",
    "n_industry_peers", "n_valid_peers", "n_ebitda_cohort",
    "effective_weights_json", "factor_scores_json", "raw_inputs_json",
    "missing_factors_json", "notes_json", "provenance_json", "coverage",
    "confidence", "created_at", "score_signature", "pillar_mask",
    "original_weight_coverage", "subfactor_mask", "partial_score_marker",
    "valuation_presence_state", "valuation_data_quality",
    "valuation_quality_rule", "valuation_subfactors_json",
    "earnings_discontinuity_state",
)


def _insert_sql(table: str, columns: Sequence[str]) -> str:
    return ("INSERT INTO %s (%s) VALUES (%s)"
            % (table, ", ".join(columns), ", ".join("?" * len(columns))))


FEATURE_SQL = _insert_sql("pit_feature", FEATURE_COLUMNS)
PILLAR_SQL = _insert_sql("pit_pillar_score", PILLAR_COLUMNS)
SCORE_SQL = _insert_sql("pit_score", SCORE_COLUMNS)


def _age_days(period: Optional[str], as_of: str) -> Optional[int]:
    if not period:
        return None
    try:
        a = _dt.date.fromisoformat(str(period)[:10])
        b = _dt.date.fromisoformat(str(as_of)[:10])
    except (TypeError, ValueError):
        return None
    return (b - a).days


def _mask(block: str, present: Mapping[str, Any]) -> str:
    """'BENCH=0,GROW=1,EFF=1,ACC=1'. V uses its own declared spelling."""
    if block == pit_factor_blocks.BLOCK_V:
        return pit_valuation_spec.subfactor_mask(
            {k: present.get(k) is not None
             for k in pit_valuation_spec.SUBFACTOR_KEYS})
    scoring = [k for k in BLOCK_KEYS[block]
               if FACTOR_PLAN[k].role == pit_factor_blocks.ROLE_SCORING]
    return ",".join("%s=%d" % (FACTOR_PLAN[k].mask_code,
                               1 if present.get(k) is not None else 0)
                    for k in scoring)


def _db_dir(conn: sqlite3.Connection) -> str:
    """The directory of the connection's main database file."""
    for row in conn.execute("PRAGMA database_list").fetchall():
        if (row[1] if not isinstance(row, sqlite3.Row) else row["name"]) == "main":
            path = row[2] if not isinstance(row, sqlite3.Row) else row["file"]
            if path:
                return os.path.dirname(os.path.abspath(path)) or "."
    return "."


# ==========================================================================
# (7) ONE DATE
# ==========================================================================

def run_date(main_conn_ro: sqlite3.Connection, pilot_conn: sqlite3.Connection,
             as_of: str, run_id: int, *,
             entity_limit: Optional[int] = None,
             entity_ids: Optional[Sequence[int]] = None,
             meter: Any = None,
             per_table_scopes: bool = False,
             freeze_digest: str = "",
             progress: Optional[Any] = None) -> Dict[str, Any]:
    """One complete cross-section: resolve, normalise, assemble, write.

    `main_conn_ro` MUST be a read-only handle on the store -- pass
    `open_store()`. `pilot_conn` is a writer on the isolated pilot DB. The two
    are never the same file, and this function opens neither.

    Returns per-date statistics: row counts by table, refusal and signature
    counts, elapsed seconds, the checkpoint return value, and the availability
    tally per feature key.
    """
    day = str(as_of)[:10]
    t0 = time.monotonic()
    created = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")

    def tick(stage: str, **extra: Any) -> None:
        if progress is not None:
            progress(dict({"as_of": day, "stage": stage,
                           "elapsed_s": round(time.monotonic() - t0, 1)},
                          **extra))

    # ---- the scoreable universe -----------------------------------------
    import pit_identity
    tick("universe")
    universe = pit_identity.peer_universe_as_of(main_conn_ro, day)
    all_ids = [int(r["entity_id"]) for r in universe]
    if entity_ids is not None:
        wanted = set(int(e) for e in entity_ids)
        targets = [e for e in all_ids if e in wanted]
    else:
        targets = list(all_ids)
    if entity_limit is not None:
        targets = targets[:int(entity_limit)]

    # ---- the cohorts, and therefore the entities whose facts are needed --
    tick("cohorts", n_targets=len(targets))
    cohorts = pit_coverage.stored_cohorts(main_conn_ro, day,
                                          PEER_SET_MODEL_VERSION)
    needed = set(targets)
    for entity_id in targets:
        record = cohorts.get(entity_id)
        if record:
            needed.update(int(m) for m in record["members"])

    # ---- one fact index for everyone -------------------------------------
    tick("fact_index", n_entities=len(needed))
    ladders = pit_policy.ladder_set(LADDER_VERSION)
    index = pit_coverage.load_fact_index(main_conn_ro, day, needed,
                                         ladder_version=LADDER_VERSION)

    cpi_cache: Dict[str, Optional[float]] = {}

    def cpi(reference: Optional[str]) -> Optional[float]:
        if not reference:
            return None
        if reference not in cpi_cache:
            cpi_cache[reference] = pit_coverage.cpi_index_as_of(
                main_conn_ro, reference, day)
        return cpi_cache[reference]

    tick("primitives", n_entities=len(needed))
    primitives: Dict[int, Primitives] = {
        e: resolve_primitives(index, e, day, ladders, cpi) for e in needed}
    index.clear()                      # the largest structure; let it go early

    cohort_cache = CohortCache(main_conn_ro, day, primitives)

    # ---- per-entity assembly ---------------------------------------------
    tick("assemble", n_targets=len(targets))
    feature_rows: List[Tuple[Any, ...]] = []
    pillar_rows: List[Tuple[Any, ...]] = []
    score_rows: List[Tuple[Any, ...]] = []

    availability_tally: Dict[str, Dict[str, int]] = {
        k: {"complete": 0, "unavailable": 0} for k in REPLAY_FEATURE_KEYS}
    reason_tally: Dict[str, int] = {}
    signature_tally: Dict[str, int] = {}
    block_tally: Dict[str, int] = {b: 0 for b in pit_factor_blocks.BLOCKS}
    n_refused = 0
    n_scored = 0
    n_no_peer_set = 0
    challenger_rows = 0

    for entity_id in targets:
        prim = primitives[entity_id]
        cohort = cohorts.get(entity_id)
        peer_set_id = cohort["peer_set_id"] if cohort else None
        rung = cohort["rung"] if cohort else None
        members = cohort["members"] if cohort else []
        n_members = cohort["n_members"] if cohort else 0
        if cohort is None:
            n_no_peer_set += 1

        normalized: Dict[str, Optional[float]] = {}
        n_ebitda_cohort: Optional[int] = None

        for key in REPLAY_FEATURE_KEYS:
            plan = FACTOR_PLAN[key]
            raw: Optional[float] = None
            score: Optional[float] = None
            norm_method: Optional[str] = None
            reason: Optional[str] = None
            resolved: Dict[str, Any] = {}
            row_peer_set = peer_set_id if plan.needs_cohort else None

            if plan.verdict != VERDICT_COMPUTABLE:
                # DECLARED REFUSAL. The row exists; the value does not.
                reason = plan.refusal_reason
            else:
                attr, why_key = RAW_FIELD[key]
                value = getattr(prim, attr)
                if not _finite(value):
                    reason = prim.why.get(why_key, R_NO_EBITDA)
                elif plan.needs_cohort and cohort is None:
                    raw = float(value)
                    reason = R_NO_PEER_SET
                else:
                    raw = float(value)
                    if plan.needs_cohort:
                        record = cohort_cache.get(peer_set_id, rung, members, key)
                        resolved["ne"] = record["n_eligible"]
                        resolved["nv"] = record["n_values"]
                        if key == "ebitda_scale":
                            n_ebitda_cohort = record["n_values"]
                        if record["availability"] != pit_store.AVAIL_COMPLETE:
                            reason = record["reason"]
                        else:
                            result = normalise(key, raw, record["values"],
                                               entity_id, day)
                            score = result.score
                            norm_method = result.normalization_type
                            reason = result.reason
                            if result.n_cohort is not None:
                                resolved["nc"] = result.n_cohort
                            if result.scale is not None:
                                resolved["k"] = result.scale
                                resolved["ks"] = result.scale_source
                    else:
                        result = normalise(key, raw, {}, entity_id, day)
                        score = result.score
                        norm_method = result.normalization_type
                        reason = result.reason
                        if result.scale is not None:
                            resolved["k"] = result.scale
                            resolved["ks"] = result.scale_source
                    if score is not None:
                        reason = None

            availability = (pit_store.AVAIL_COMPLETE if score is not None
                            else pit_store.AVAIL_UNAVAILABLE)
            normalized[key] = score
            availability_tally[key][
                "complete" if score is not None else "unavailable"] += 1
            if reason:
                reason_tally[reason] = reason_tally.get(reason, 0) + 1
            if plan.role == pit_factor_blocks.ROLE_ZERO_WEIGHT_CHALLENGER:
                challenger_rows += 1

            source_period = (prim.revenue_period if key == "real_revenue_growth"
                             else prim.ebitda_period)
            source_tag = (prim.revenue_rung if key == "real_revenue_growth"
                          else None)
            resolved["scope"] = SAMPLE_SCOPE
            resolved["fd"] = freeze_digest[:12]
            resolved["ev"] = ENGINE_VERSION
            if plan.computable:
                resolved["p"] = source_period

            feature_rows.append((
                entity_id, None, day, key, plan.role,
                json.dumps(list(SOURCE_CONCEPTS.get(key, ())),
                           separators=(",", ":")),
                source_tag, LADDER_VERSION, 0,
                _age_days(source_period, day), day, LATENCY_POLICY_VERSION,
                raw, score, norm_method, row_peer_set,
                availability, 1 if score is not None else 0, reason,
                MODEL_VERSION, run_id, created,
                json.dumps(resolved, separators=(",", ":")),
            ))

        # ---- blocks -------------------------------------------------------
        block_results: Dict[str, Dict[str, Any]] = {}
        block_scores: Dict[str, Optional[float]] = {}
        for block in pit_factor_blocks.BLOCKS:
            result = pit_factor_blocks.block_score(
                block, {k: normalized[k] for k in BLOCK_KEYS[block]})
            block_results[block] = result
            block_scores[block] = result["score"]
            if result["score"] is not None:
                block_tally[block] += 1

        assembled = pit_factor_blocks.assemble(block_scores)
        scored = assembled["company_score"] is not None
        if scored:
            n_scored += 1
        else:
            n_refused += 1
        signature = assembled.get("signature") or "NONE"
        signature_tally[signature] = signature_tally.get(signature, 0) + 1

        # ---- pillar rows, written whether or not a score was emitted ------
        for block in pit_factor_blocks.BLOCKS:
            result = block_results[block]
            keys = BLOCK_KEYS[block]
            weights = pit_factor_blocks.within_block_weights(block)
            present = {k: normalized[k] for k in keys
                       if normalized[k] is not None}
            subfactors = {
                k: {"n": normalized[k],
                    "w": weights.get(k, 0.0),
                    "role": FACTOR_PLAN[k].role,
                    "a": (pit_store.AVAIL_COMPLETE if normalized[k] is not None
                          else pit_store.AVAIL_UNAVAILABLE)}
                for k in keys}
            resolved_block = result["score"] is not None
            pillar_rows.append((
                entity_id, day, block, MODEL_VERSION, run_id,
                result["score"],
                pit_factor_blocks.block_weight(block),
                (pit_factor_blocks.block_weight(block)
                 if (resolved_block and scored) else 0.0),
                "resolved" if resolved_block else "unavailable",
                None if resolved_block else result.get("unavailable_reason"),
                len(weights), result["n_scoring_present"],
                json.dumps(subfactors, separators=(",", ":")),
                _mask(block, present),
                peer_set_id, rung, n_members or None,
                SAMPLE_SCOPE, pit_intermediates.INTERMEDIATE_SCHEMA_VERSION,
                created,
            ))

        # ---- the score row, or none at all --------------------------------
        if scored:
            missing = [k for k in REPLAY_FEATURE_KEYS if normalized[k] is None]
            notes = {
                "block_mask": assembled["block_mask"],
                "renormalised": assembled.get("renormalised"),
                "within_block_coverage": {
                    b: block_results[b]["within_block_coverage"]
                    for b in pit_factor_blocks.BLOCKS},
            }
            provenance = {
                "engine": ENGINE_VERSION,
                "spec_freeze_version": pit_frozen_spec.SPEC_FREEZE_VERSION,
                "freeze_digest": freeze_digest,
                "sample_scope": SAMPLE_SCOPE,
                "block_map_version": pit_factor_blocks.BLOCK_MAP_VERSION,
                "policy_weights_version": pit_factor_blocks.POLICY_WEIGHTS_VERSION,
                "policy_weights_status": pit_factor_blocks.POLICY_WEIGHTS_STATUS,
                "normalization_policy": pit_normalization.NORMALIZATION_POLICY_VERSION,
                "factor_spec_version": FACTOR_SPEC_VERSION,
                "peer_set_model_version": PEER_SET_MODEL_VERSION,
            }
            score_rows.append((
                entity_id, None, day, MODEL_VERSION, run_id,
                LATENCY_POLICY_VERSION, PEER_SET_VERSION, LADDER_VERSION,
                assembled["company_score"], None, None, None,
                None, rung, None,
                n_members or None, None, n_ebitda_cohort,
                json.dumps(assembled.get("effective_weights") or {},
                           separators=(",", ":")),
                json.dumps({b: block_scores[b]
                            for b in pit_factor_blocks.BLOCKS},
                           separators=(",", ":")),
                None,
                json.dumps(missing, separators=(",", ":")),
                json.dumps(notes, separators=(",", ":")),
                json.dumps(provenance, separators=(",", ":")),
                assembled["block_coverage"], None, created,
                assembled["signature"], assembled["block_mask"],
                assembled["block_coverage"],
                _mask(pit_factor_blocks.BLOCK_V,
                      {k: normalized[k]
                       for k in BLOCK_KEYS[pit_factor_blocks.BLOCK_V]}),
                assembled.get("partial_score_marker"),
                None, None, None, None, None,
            ))

    # ---- write ------------------------------------------------------------
    tick("write", n_features=len(feature_rows), n_pillars=len(pillar_rows),
         n_scores=len(score_rows))
    written: Dict[str, int] = {}
    batches = (("pit_feature", FEATURE_SQL, feature_rows),
               ("pit_pillar_score", PILLAR_SQL, pillar_rows),
               ("pit_score", SCORE_SQL, score_rows))
    for table, sql, rows in batches:
        if per_table_scopes and meter is not None:
            with meter.table_scope(table):
                pilot_conn.executemany(sql, rows)
                pilot_conn.commit()
        else:
            pilot_conn.executemany(sql, rows)
            pilot_conn.commit()
        written[table] = len(rows)

    # ---- checkpoint, WITH THE RETURN VALUE READ ---------------------------
    tick("checkpoint")
    if meter is not None:
        checkpoint = meter.checkpoint("TRUNCATE")
    else:
        row = pilot_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        busy, log_pages, checkpointed = int(row[0]), int(row[1]), int(row[2])
        checkpoint = {
            "mode": "TRUNCATE", "busy": busy, "log_pages": log_pages,
            "checkpointed": checkpointed, "return_read": True,
            "busy_refusal": bool(busy == 1 and log_pages > 0
                                 and checkpointed == 0),
            "return_semantics": (
                "a SUCCESSFUL TRUNCATE returns (0, 0, 0) because the log is "
                "reset; that is not a no-op. busy=1 with log_pages>0 and "
                "checkpointed=0 is the busy-refusal signature."),
        }

    free_after = shutil.disk_usage(_db_dir(pilot_conn)).free

    return {
        "as_of": day,
        "run_id": run_id,
        "n_universe": len(all_ids),
        "n_targets": len(targets),
        "n_entities_indexed": len(needed),
        "entity_dates": len(targets),
        "rows": written,
        "n_scored": n_scored,
        "n_refused_insufficient_block_coverage": n_refused,
        "n_no_peer_set": n_no_peer_set,
        "n_challenger_rows": challenger_rows,
        "blocks_resolved": block_tally,
        "signatures": signature_tally,
        "availability": availability_tally,
        "reasons": reason_tally,
        "n_eligible_peer_calls": cohort_cache.n_eligible_calls,
        "checkpoint": checkpoint,
        "free_bytes_after": free_after,
        "elapsed_s": round(time.monotonic() - t0, 2),
    }


# ==========================================================================
# (8) THE RUN
# ==========================================================================

def run_pilot(dates: Sequence[str], pilot_db_path: str, *,
              store_path: str = pit_store.DEFAULT_PIT_DB_PATH,
              entity_limit: Optional[int] = None,
              entity_ids: Optional[Sequence[int]] = None,
              overwrite: bool = True,
              free_floor_bytes: int = FREE_FLOOR_BYTES,
              measure: bool = True,
              per_table_scopes: bool = True,
              sample_hz: float = 5.0,
              progress: Optional[Any] = None) -> Dict[str, Any]:
    """The whole pilot: gate, build, seed, replay each date, report.

    ORDER IS THE CONTRACT. The gate is evaluated while the only open handle is
    read-only; only after it passes does a writable connection exist at all.
    Free space is checked BEFORE every date, and a breach stops the run and
    reports how far it got rather than discovering the floor experimentally.
    """
    started = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    t0 = time.monotonic()
    days = [str(d)[:10] for d in dates]

    # ---- 1. THE GATE, before any writer ----------------------------------
    require_gate()
    freeze = pit_frozen_spec.verify()
    digest = str(freeze.get("current_digest") or "")

    # ---- 2. free space, before anything is created -----------------------
    pilot_dir = os.path.dirname(os.path.abspath(pilot_db_path)) or "."
    usage = shutil.disk_usage(pilot_dir)
    if usage.free < free_floor_bytes:
        raise FreeSpaceAbort(
            "free %.3f GiB is below the %.3f GiB floor before the run started; "
            "nothing was created" % (usage.free / GIB, free_floor_bytes / GIB))

    report: Dict[str, Any] = {
        "engine_version": ENGINE_VERSION,
        "model_version": MODEL_VERSION,
        "sample_scope": SAMPLE_SCOPE,
        "spec_freeze_version": pit_frozen_spec.SPEC_FREEZE_VERSION,
        "freeze_digest": digest,
        "freeze_intact_before": bool(freeze.get("intact")),
        "gate": "PASSED",
        "started_at": started,
        "dates_requested": days,
        "entity_limit": entity_limit,
        "pilot_db_path": os.path.abspath(pilot_db_path),
        "store_path": os.path.abspath(store_path),
        "free_floor_bytes": free_floor_bytes,
        "free_bytes_at_start": usage.free,
        "plan": plan_record(),
        "dates": [],
        "aborted": None,
    }

    build = ensure_pilot_db(pilot_db_path, overwrite=overwrite)
    report["pilot_build"] = {k: build[k] for k in
                             ("path", "n_tables", "page_size", "journal_mode",
                              "file_bytes", "has_pit_pillar_score")}

    store_conn = open_store(store_path)
    pilot_conn = open_pilot(pilot_db_path)
    meter = None
    try:
        if measure:
            import pit_replay_meter
            meter = pit_replay_meter.Meter(
                pilot_db_path, conn=pilot_conn, sample_hz=sample_hz,
                free_floor_bytes=free_floor_bytes,
                exclusivity_tables=("pit_feature", "pit_pillar_score",
                                    "pit_score", "pit_replay_run",
                                    "pit_entity", "pit_peer_set"),
                label="pit_replay pilot")
            meter.start()
            meter.begin("seed")

        seed = seed_parents(pilot_conn, store_conn, days)
        report["seed"] = seed
        if meter is not None:
            # CHECKPOINT BEFORE THE BASELINE, or the seed costs nothing.
            # `Meter.db_snapshot` reads the size of the MAIN database file,
            # and a committed-but-unckeckpointed write lives in the -wal. The
            # first cut of this engine rebaselined straight after the seed and
            # measured seed_bytes = 0 for 16,890 entity rows -- not a small
            # cost, an unmeasured one. TRUNCATE first, then baseline.
            seed["checkpoint"] = meter.checkpoint("TRUNCATE")
            report["rebaseline"] = meter.rebaseline(
                "parents seeded (%d pit_entity, %d pit_peer_set) and "
                "checkpointed; the seed is a one-time cost and is excluded "
                "from BytesPerEntityDate"
                % (seed["pit_entity_rows"], seed["pit_peer_set_rows"]))

        # ---- the run row --------------------------------------------------
        cur = pilot_conn.execute(
            "INSERT INTO pit_replay_run (started_at, as_of_from, as_of_to, "
            "as_of_grid, model_version, latency_policy_version, ladder_version, "
            "peer_set_version, universe_def_json, source_versions_json, "
            "code_revision, status, n_dates, summary_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (started, days[0], days[-1], AS_OF_GRID, MODEL_VERSION,
             LATENCY_POLICY_VERSION, LADDER_VERSION, PEER_SET_VERSION,
             json.dumps({"universe": "pit_identity.peer_universe_as_of",
                         "entity_limit": entity_limit,
                         "sample_scope": SAMPLE_SCOPE}),
             json.dumps({"spec_freeze_version": pit_frozen_spec.SPEC_FREEZE_VERSION,
                         "freeze_digest": digest,
                         "engine": ENGINE_VERSION}),
             str(pit_replay_manifest.git_revision().get("revision") or ""),
             "running", len(days),
             json.dumps(plan_record(), separators=(",", ":"))))
        run_id = int(cur.lastrowid)
        pilot_conn.commit()
        report["run_id"] = run_id

        totals: Dict[str, int] = {"pit_feature": 0, "pit_pillar_score": 0,
                                  "pit_score": 0}
        entity_dates = 0
        for day in days:
            # ---- FREE-SPACE GUARD, before the date, every date ------------
            usage = shutil.disk_usage(pilot_dir)
            if usage.free < free_floor_bytes:
                report["aborted"] = {
                    "at_date": day,
                    "reason": "FREE_SPACE_FLOOR",
                    "free_bytes": usage.free,
                    "floor_bytes": free_floor_bytes,
                    "dates_completed": [d["as_of"] for d in report["dates"]],
                    "entity_dates_completed": entity_dates,
                }
                break
            if meter is not None:
                meter.begin(day)
            stats = run_date(store_conn, pilot_conn, day, run_id,
                             entity_limit=entity_limit, entity_ids=entity_ids,
                             meter=meter, per_table_scopes=per_table_scopes,
                             freeze_digest=digest, progress=progress)
            report["dates"].append(stats)
            for table, n in stats["rows"].items():
                totals[table] = totals.get(table, 0) + n
            entity_dates += stats["entity_dates"]
            if meter is not None:
                meter.mark(day, entity_dates=stats["entity_dates"],
                           rows=stats["rows"])

        report["totals"] = totals
        report["entity_dates"] = entity_dates
        pilot_conn.execute(
            "UPDATE pit_replay_run SET finished_at = ?, status = ?, "
            "n_entities = ?, summary_json = ? WHERE run_id = ?",
            (_dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
             "aborted_free_space" if report["aborted"] else "complete",
             entity_dates,
             json.dumps({"plan": plan_record(), "totals": totals,
                         "entity_dates": entity_dates,
                         "aborted": report["aborted"]},
                        separators=(",", ":")),
             run_id))
        pilot_conn.commit()
    finally:
        if meter is not None:
            try:
                meter.stop()
                report["meter"] = meter.report()
            except Exception as exc:                  # measurement only
                report["meter_error"] = "%s: %s" % (exc.__class__.__name__, exc)
            finally:
                meter.close()
        pilot_conn.close()
        store_conn.close()

    after = pit_frozen_spec.verify()
    report["freeze_digest_after"] = str(after.get("current_digest") or "")
    report["freeze_intact_after"] = bool(after.get("intact"))
    report["freeze_unchanged"] = (report["freeze_digest_after"] == digest)
    report["elapsed_s"] = round(time.monotonic() - t0, 2)
    report["finished_at"] = _dt.datetime.now(
        _dt.timezone.utc).isoformat(timespec="seconds")
    return report


# ==========================================================================
# (9) SELF-CHECK
# ==========================================================================

def validate() -> List[str]:
    """Structural self-check. Returns problems; empty means PASS. READS NOTHING."""
    problems: List[str] = []

    # the plan covers the model, exactly once
    mapped = set(pit_factor_blocks.FACTOR_BLOCK)
    planned_spec_keys = {p.spec_key for p in FACTOR_PLAN.values() if p.spec_key}
    missing = mapped - planned_spec_keys
    if missing:
        problems.append("factors with no plan: %s" % sorted(missing))
    if set(FACTOR_PLAN) != set(REPLAY_FEATURE_KEYS):
        problems.append("FACTOR_PLAN and REPLAY_FEATURE_KEYS disagree")
    if len(REPLAY_FEATURE_KEYS) != len(set(REPLAY_FEATURE_KEYS)):
        problems.append("REPLAY_FEATURE_KEYS has a duplicate")

    # every block's scoring keys are exactly within_block_weights' keys
    for block in pit_factor_blocks.BLOCKS:
        declared = set(pit_factor_blocks.within_block_weights(block))
        ours = {k for k in BLOCK_KEYS[block]
                if FACTOR_PLAN[k].role == pit_factor_blocks.ROLE_SCORING}
        if declared != ours:
            problems.append(
                "%s: engine writes %s, within_block_weights declares %s"
                % (block, sorted(ours), sorted(declared)))

    # the challenger is a challenger everywhere
    if (FACTOR_PLAN["ebitda_scale"].role
            != pit_factor_blocks.ROLE_ZERO_WEIGHT_CHALLENGER):
        problems.append("ebitda_scale is not marked a challenger in the plan")
    if pit_factor_blocks.effective_company_weight("ebitda_scale") != 0.0:
        problems.append("ebitda_scale has non-zero effective company weight")
    if "ebitda_scale" in pit_factor_blocks.within_block_weights(
            pit_factor_blocks.BLOCK_E):
        problems.append("ebitda_scale reached within_block_weights")

    # every computable factor has a registered transform under its declared key
    for key, plan in FACTOR_PLAN.items():
        if plan.computable:
            if plan.norm_key not in _REGISTRY:
                problems.append(
                    "%s: norm_key %r is not in candidate_v2_registry()"
                    % (key, plan.norm_key))
            if key not in RAW_FIELD:
                problems.append("%s is computable but declares no raw field" % key)
        else:
            if plan.refusal_reason not in REASON_WHY:
                problems.append("%s: refusal reason %r has no recorded why"
                                % (key, plan.refusal_reason))
            if key in RAW_FIELD and plan.verdict == VERDICT_NOT_COMPUTABLE:
                problems.append(
                    "%s is NOT_COMPUTABLE but declares a raw field" % key)

    # the model version string is the frozen one
    if MODEL_VERSION != pit_frozen_spec.FROZEN_MODEL_VERSION:
        problems.append(
            "MODEL_VERSION %r != pit_frozen_spec.FROZEN_MODEL_VERSION %r"
            % (MODEL_VERSION, pit_frozen_spec.FROZEN_MODEL_VERSION))
    if SAMPLE_SCOPE != "SURVIVOR_ONLY_DIAGNOSTIC":
        problems.append("SAMPLE_SCOPE is not SURVIVOR_ONLY_DIAGNOSTIC")

    # column tuples have no duplicates
    for table, columns in (("pit_feature", FEATURE_COLUMNS),
                           ("pit_pillar_score", PILLAR_COLUMNS),
                           ("pit_score", SCORE_COLUMNS)):
        if len(columns) != len(set(columns)):
            problems.append("%s column tuple has a duplicate" % table)

    # the reachable weight statement must be true, not aspirational
    reachable = plan_record()["reachable_block_weight"]
    if reachable < pit_factor_blocks.MIN_BLOCK_WEIGHT:
        problems.append(
            "reachable block weight %.2f is below MIN_BLOCK_WEIGHT %.2f: this "
            "engine could never emit a score"
            % (reachable, pit_factor_blocks.MIN_BLOCK_WEIGHT))
    return problems


def render(report: Mapping[str, Any]) -> str:
    """A human-readable pilot report. Never invents a number it was not given."""
    lines: List[str] = []
    add = lines.append
    add("=" * 78)
    add("PIT REPLAY -- %s" % report.get("engine_version"))
    add("=" * 78)
    add("model_version      %s" % report.get("model_version"))
    add("spec freeze        %s  %s" % (report.get("spec_freeze_version"),
                                       report.get("freeze_digest")))
    add("freeze unchanged   %s" % report.get("freeze_unchanged"))
    add("gate               %s" % report.get("gate"))
    add("pilot db           %s" % report.get("pilot_db_path"))
    add("")
    for stats in report.get("dates", []):
        add("-- %s -------------------------------------------------"
            % stats["as_of"])
        add("   universe %d  targets %d  indexed %d  %.1fs"
            % (stats["n_universe"], stats["n_targets"],
               stats["n_entities_indexed"], stats["elapsed_s"]))
        add("   rows     %s" % stats["rows"])
        add("   scored %d   refused(min block weight) %d   no peer set %d"
            % (stats["n_scored"],
               stats["n_refused_insufficient_block_coverage"],
               stats["n_no_peer_set"]))
        add("   signatures %s" % stats["signatures"])
        cp = stats["checkpoint"]
        add("   checkpoint busy=%s log_pages=%s checkpointed=%s busy_refusal=%s"
            % (cp.get("busy"), cp.get("log_pages"), cp.get("checkpointed"),
               cp.get("busy_refusal")))
    if report.get("aborted"):
        add("")
        add("ABORTED: %s" % report["aborted"])
    add("")
    add("totals %s over %s entity-dates"
        % (report.get("totals"), report.get("entity_dates")))
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="the Shaffer v2 replay engine")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--smoke", metavar="DATE",
                        help="run one date into a throwaway pilot DB")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--pilot", default=pit_replay_manifest.PILOT_DB_PATH)
    args = parser.parse_args(argv)

    if args.validate or not any((args.plan, args.gate, args.smoke)):
        problems = validate()
        print("validate: %s" % ("PASS" if not problems else "FAIL"))
        for problem in problems:
            print("  -", problem)
        if problems:
            return 1
    if args.plan:
        print(json.dumps(plan_record(), indent=2))
    if args.gate:
        ok, reasons = pit_replay_manifest.gate()
        print("gate: %s" % ok)
        for reason in reasons:
            print("  -", reason)
    if args.smoke:
        report = run_pilot([args.smoke], args.pilot, entity_limit=args.limit)
        print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
