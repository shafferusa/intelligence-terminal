"""
==============================================================================
pit_replay -- THE REPLAY ENGINE for the Shaffer v2 survivor-only diagnostic
==============================================================================

WHAT THIS IS

    The executable half of the LIVE freeze (`pit_frozen_spec.SPEC_FREEZE_VERSION`,
    spec_freeze_v6 at this writing). `pit_frozen_spec` says what the
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

THE FACTOR PLAN -- THIRTEEN OF THIRTEEN ARE COMPUTED (pit_replay/1.2)

    Thirteen feature keys are written per entity-date: the ten company factors
    of `pit_factor_blocks.FACTOR_BLOCK` that are not V, plus V's three
    subfactors from `pit_valuation_spec.SUBFACTOR_WEIGHTS` -- because V
    declares its weights at subfactor granularity and a feature row at factor
    granularity could not carry a weight. Under spec_freeze_v6 (owner rulings
    R1-R21 of 2026-09-23) every one of them is COMPUTABLE:

        ebitda_benchmark     EBITDAExcessLevel     BENCHMARK_ANCHORED at M_s,
                                                   the 50-75 band's mean margin
                                                   (target INCLUDED, one M_s
                                                   per peer set, band >= 3)
        ebitda_growth        EBITDAGrowth          ZERO_ANCHORED, cohort scale
        ebitda_efficiency    EBITDAMarginRank      PERCENTILE_RANK
        ebitda_acceleration  EBITDAAcceleration    ZERO_ANCHORED, cohort scale
        ebitda_scale         EBITDAScale           PERCENTILE_RANK   (challenger)
        pe_absolute          (pit_valuation_spec)  convex transform at 20x
        pe_relative          (pit_valuation_spec)  convex transform at the
                                                   cohort median (R17)
        ev_ebitda_supplement (pit_valuation_spec)  tanh around the cohort
                                                   median, lower better (R15)
        real_revenue_growth  RealRevenueGrowth     ZERO_ANCHORED, fixed g = 0.10
        fcf_conversion       FCFConversionRank     PERCENTILE_RANK
        interest_coverage    InterestCoverageRank  PERCENTILE_RANK over
                                                   positive-OI peers; OI <= 0
                                                   is the FLOOR STATE (R4, R5)
        net_debt_ebitda      NetDebtEBITDARank     PERCENTILE_RANK, lower better
        debt_market_cap      DebtMarketCapRank     PERCENTILE_RANK, lower better

    Their arithmetic is DECLARED, not implied: `pit_factor_spec.FORMULAS_V2`
    states each executable definition, `FORMULA_GOLDEN_V2` witnesses it, and
    `validate()` runs every witness through `resolve_primitives` (or, for
    the benchmark, through `benchmark_anchor`). The domain policies run
    BEFORE any direction: EBITDA_GROWTH_BASE_POLICY_V2, INTEREST_COVERAGE_
    DOMAIN_V2, LEVERAGE_DOMAIN_V1, FCF_DOMAIN_V1, MARKET_CAP_DOMAIN_V1,
    EV_EBITDA_DOMAIN_V1, PE_DOMAIN_V1 and the strict debt-rung gate
    DEBT_RUNG_GATE_V1. The engine derives eligibility from
    `pit_frozen_spec.ENGINE_MUST_DERIVE_FROM` (factor_spec_v5, whose Q/V
    rules match every balance-sheet and cash-flow primitive AT the EBITDA
    period) and refuses to run from any other version.

    THE V LEGS ARE SCORED BY pit_valuation_spec'S FROZEN SCORERS (R13): the
    convex extreme-valuation transform for both P/E legs and the
    benchmark-anchored tanh for the supplement. The engine holds no V curve
    of its own. The valuation price is ONE resolver
    (VALUATION_PRICE_POLICY_V1: the score-date raw close, else the latest
    prior valid trade within ten calendar days, same listing, no share-class
    switching), its basis translated ONCE through
    PRICE_BASIS_TRANSLATION_V1, and the P/E carried to the score-date share
    basis under corporate-action gate v2 (an empty window on a listing with
    coverage is the identity).

    THE FLOORS. PEER_FLOOR_V1 is ONE frozen per-factor floor on the number
    of VALUED cohort members, refused once by name
    (cohort_values_below_floor). WITHIN_BLOCK_FLOOR_V1 (R6) says when a block
    counts: E and Q with at least half their scoring weight, G with its one
    factor, V under its presence rule -- so the v5 single-factor Q path is
    CLOSED. MIN_BLOCK_WEIGHT = 0.40 is unchanged. No block is ever
    renormalised against another. The reachable weight is the whole model,
    0.85; what a company actually reaches is on its row.

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
import pit_eps
import pit_eps_cohort
import pit_factor_blocks
import pit_factor_spec
import pit_frozen_spec
import pit_intermediates
import pit_normalization
import pit_policy
import pit_price_basis
import pit_rawprice
import pit_replay_manifest
import pit_store
import pit_valuation_spec

__all__ = [
    "ENGINE_VERSION", "MODEL_VERSION", "SAMPLE_SCOPE", "AS_OF_GRID",
    "ReplayRefused", "FreeSpaceAbort", "DEFAULT_BATCH_SIZE",
    "VERDICT_COMPUTABLE", "VERDICT_REFUSED_AMBIGUOUS", "VERDICT_NOT_COMPUTABLE",
    "FactorPlan", "FACTOR_PLAN", "REPLAY_FEATURE_KEYS", "BLOCK_KEYS",
    "REASON_WHY", "ENGINE_REASONS", "KNOWN_LIMITATIONS", "plan_record",
    "Primitives", "resolve_primitives", "CohortCache", "normalise",
    "attach_market", "benchmark_anchor", "valuation_price", "resolve_listings",
    "COHORT_RANK", "COHORT_DISPERSION", "COHORT_BENCHMARK", "COHORT_MEDIAN_ANCHOR",
    "RETIRED_REASONS", "FACTOR_SPEC_VERSION",
    "open_store", "require_gate", "ensure_pilot_db", "open_pilot",
    "seed_parents", "run_date", "run_pilot", "validate", "render", "main",
]

# ==========================================================================
# IDENTITY
# ==========================================================================

#: 1.1 on 2026-09-22: growth A, acceleration A and interest coverage computed;
#: eligibility from factor_spec_v4. 1.0 rows differ from 1.1 rows BY DESIGN.
#: 1.2 on 2026-09-23 (spec_freeze_v6): all thirteen keys computed -- the
#: MARGIN benchmark, the P/E chain, EV/EBITDA and the three Q ratios -- under
#: the owner's rulings R1-R21; the coverage floor state; the peer and
#: within-block floors; eligibility from factor_spec_v5. 1.1 rows differ from
#: 1.2 rows BY DESIGN (a company scored under 1.1 on E + a single-factor Q is
#: refused under 1.2 by WITHIN_BLOCK_FLOOR_V1).
ENGINE_VERSION = "pit_replay/1.2"

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
#: Must equal pit_frozen_spec.ENGINE_MUST_DERIVE_FROM; validate() and
#: require_gate() refuse otherwise, so the engine cannot stamp a freeze it does
#: not derive from.
FACTOR_SPEC_VERSION = "factor_spec_v5"

GIB = 1024 ** 3

#: The per-date abort floor. Checked with `shutil.disk_usage` BEFORE every
#: date, and -- since the write side was streamed -- after every committed
#: batch inside a date. CONVENTION, not measurement.
FREE_FLOOR_BYTES = int(7.0 * GIB)

#: THE WRITE SIDE IS STREAMED. `run_date` flushes its three row lists to the
#: pilot DB every this-many targets (compute -> write -> commit -> clear), so
#: the write-side memory is bounded by the batch rather than by the
#: cross-section. `None` (or 0) means one batch = the whole cross-section,
#: which is the pre-2026-09-22 behaviour, kept for the equality oracle.
#:
#: This bounds ONLY the write side. The fact index and the primitives are a
#: once-per-date, O(cross-section) cost that this constant does not touch.
#: Whether the bound holds is a MEASUREMENT (pit_replay_rss.py), not a claim.
DEFAULT_BATCH_SIZE = 500

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
R_EBITDA_BASE_ZERO = "ebitda_growth_base_zero"
#: RETIRED NAME (owner ruling R3, 2026-09-23): the band is on the RATE, and
#: the name said "base near zero". Kept in the vocabulary because rows written
#: under pit_replay/1.1 carry it; NO live path emits it (validate() checks).
R_EBITDA_BASE_NEAR_ZERO = "ebitda_growth_base_near_zero"
R_EBITDA_RATE_BEYOND_BAND = "ebitda_growth_rate_beyond_band"
R_NO_OPERATING_INCOME = "no_operating_income_period"
R_NO_INTEREST_AT_P = "no_interest_expense_at_operating_income_period"
R_INTEREST_ZERO = "interest_expense_zero"
R_INTEREST_NEGATIVE = "interest_expense_negative"
R_PE_CHAIN_NOT_BUILT = "pe_chain_not_built_in_engine"

# -- spec_freeze_v6: the Q, V and cohort vocabulary (owner rulings 2026-09-23)
R_COVERAGE_FLOOR_STATE = "interest_coverage_floor_state"      # a STATE, not a refusal
R_EBITDA_NON_POSITIVE = "ebitda_non_positive"
R_NO_TOTAL_DEBT_AT_P = "no_total_debt_at_ebitda_period"
R_NO_CASH_AT_P = "no_cash_at_ebitda_period"
R_NO_OCF_AT_P = "no_operating_cash_flow_at_ebitda_period"
R_NO_CAPEX_AT_P = "no_capex_at_ebitda_period"
R_DEBT_RUNG_REJECTED = pit_valuation_spec.REASON_DEBT_RUNG_REJECTED
R_DEBT_RUNG_UNDETERMINED = pit_valuation_spec.REASON_DEBT_RUNG_UNDETERMINED
R_NO_SCORED_LISTING = "no_scored_listing"
R_LISTING_AMBIGUOUS = "listing_ambiguous"
R_NO_VALUATION_PRICE = "no_valuation_price_within_window"
R_PRICE_STRADDLES_ACTION = "price_straddles_corporate_action"
R_MARKET_CAP_UNAVAILABLE = "market_cap_unavailable"
R_MARKET_CAP_NON_POSITIVE = "market_cap_non_positive"
R_EV_ABOVE_BAND = "ev_ebitda_above_band"
R_NO_TTM_EPS = "no_ttm_eps"
R_EPS_LOSS = pit_eps.PE_UNAVAILABLE_LOSS
R_EPS_ZERO = pit_eps.PE_UNAVAILABLE_ZERO
R_EPS_NEAR_ZERO = "eps_near_zero_positive"
R_SHARE_BASIS_UNRESOLVED = "share_basis_unresolved"
R_PE_RELATIVE_NEEDS_ABSOLUTE = "pe_relative_needs_absolute"
R_COHORT_VALUES_BELOW_FLOOR = "cohort_values_below_floor"
R_BENCHMARK_BAND_TOO_THIN = "benchmark_band_too_thin"
R_NO_ANCHOR_POPULATION = pit_valuation_spec.REASON_NO_ANCHOR_POPULATION

#: RETIRED: the two asset-base codes with the D3 decision of 2026-09-22
#: (growth = A: no asset base), and with spec_freeze_v6 the four codes the
#: v5 engine emitted for keys it could not compute plus the renamed rate-band
#: code (owner ruling R3). Rows in disposable pilot DBs carry them; no live
#: plan or path may emit them, and validate() proves it.
RETIRED_REASONS: Tuple[str, ...] = ("no_assets_at_lag1", "no_assets_at_lag2",
                                    R_EBITDA_BASE_NEAR_ZERO, R_BENCHMARK_AMBIGUOUS,
                                    R_NO_V2_NORMALIZATION, R_PE_CHAIN_NOT_BUILT)
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
    # history: emitted under 1.0 / 1.1, never by a live 1.2 path
    R_BENCHMARK_AMBIGUOUS, R_NO_V2_NORMALIZATION, R_PRIMITIVE_UNAVAILABLE,
    R_PE_CHAIN_NOT_BUILT, R_EBITDA_BASE_NEAR_ZERO,
    # live
    R_NO_PEER_SET, R_NO_EBITDA, R_NO_LAG1, R_NO_LAG2, R_EBITDA_BASE_ZERO,
    R_EBITDA_RATE_BEYOND_BAND, R_NO_REVENUE_AT_P, R_NO_REVENUE, R_NO_REVENUE_LAG1,
    R_NO_CPI, R_NON_POSITIVE_BASE, R_NO_OPERATING_INCOME, R_NO_INTEREST_AT_P,
    R_INTEREST_ZERO, R_INTEREST_NEGATIVE,
    R_EBITDA_NON_POSITIVE, R_NO_TOTAL_DEBT_AT_P, R_NO_CASH_AT_P, R_NO_OCF_AT_P,
    R_NO_CAPEX_AT_P, R_DEBT_RUNG_REJECTED, R_DEBT_RUNG_UNDETERMINED,
    R_NO_SCORED_LISTING, R_LISTING_AMBIGUOUS, R_NO_VALUATION_PRICE,
    R_PRICE_STRADDLES_ACTION,
    R_MARKET_CAP_UNAVAILABLE, R_MARKET_CAP_NON_POSITIVE, R_EV_ABOVE_BAND,
    R_NO_TTM_EPS, R_EPS_LOSS, R_EPS_ZERO, R_EPS_NEAR_ZERO,
    R_SHARE_BASIS_UNRESOLVED, R_PE_RELATIVE_NEEDS_ABSOLUTE,
    R_COHORT_VALUES_BELOW_FLOOR, R_BENCHMARK_BAND_TOO_THIN, R_NO_ANCHOR_POPULATION,
)

REASON_WHY: Dict[str, str] = {
    R_BENCHMARK_AMBIGUOUS: (
        "THE MARGIN PATH IS NOT BUILT IN THIS ENGINE. factor_spec_v2 held two "
        "incompatible definitions and factor_spec_v3 decided the MARGIN reading "
        "(owner, 2026-09-22, D1); this engine has not built the cohort (EBITDA, "
        "margin) pairs through benchmark_margin_50_75 nor the benchmark= anchor "
        "into normalize(), and it does not pick a reading by itself. The code is "
        "kept so rows stay comparable with the pilot; the history it names: "
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
        "pit_normalization.candidate_v2_registry() declares seven specs -- "
        "EBITDAMarginRank, EBITDAExcessLevel, EBITDAGrowth, "
        "EBITDAAcceleration, EBITDAScale, RealRevenueGrowth, "
        "InterestCoverageRank -- and this key is not among them. pit_factor_spec "
        "declares the factor's PRIMITIVES, its PEER ELIGIBILITY and a "
        "normalization_type, but a type is not a registered transform with its "
        "anchor and scale, so there is nothing to apply. The ratio is arithmetic; "
        "the transform is a modelling decision, and inventing one at run time is "
        "exactly the undefended assignment this project refuses elsewhere."),
    R_PRIMITIVE_UNAVAILABLE: (
        "The factor's eligibility rule names a primitive in "
        "`unavailable_primitives`: the store's own declaration that the input "
        "does not exist, not an inference from an empty query. NO live plan "
        "carries this reason under factor_spec_v4 (PRICED_EPS_V2 withdrew the "
        "EPS declaration); it stays in the vocabulary because rows stamped "
        "under v2/v3 carry it, and validate() refuses a plan that uses it "
        "without a declaring spec."),
    R_PE_CHAIN_NOT_BUILT: (
        "PRICED_EPS_V2 (factor_spec_v3 and later) declares point-in-time EPS "
        "AVAILABLE -- pit_eps_obs holds 888,486 rows -- so the older 'primitive "
        "genuinely unavailable' reason is false. This engine has not wired the "
        "pit_eps TTM join, pit_price_basis.PAIR_PATHS or the raw-price basis, "
        "so the P/E legs are refused for an UNBUILT CHAIN, never for an absent "
        "primitive. Engine work, recorded in pit_frozen_spec.STILL_BLOCKED."),
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
    R_EBITDA_BASE_ZERO: (
        "EBITDA at the base end of the growth window is exactly zero, so the rate "
        "has no denominator. A reported zero is a value, not an absence; the rate "
        "on it is undefined, never a large number (company_scoring._growth "
        "precedent). Declared in pit_factor_spec.EBITDA_GROWTH_BASE_POLICY_V1."),
    R_EBITDA_BASE_NEAR_ZERO: (
        "RETIRED NAME under pit_replay/1.1 (owner ruling R3, 2026-09-23): the "
        "same refusal is now ebitda_growth_rate_beyond_band, because the band is "
        "on the RATE and the old name said 'base near zero'. Rows written under "
        "1.1 carry this string; no 1.2 path emits it. EBITDA_GROWTH_BASE_POLICY_V2 "
        "records the rename."),
    R_EBITDA_RATE_BEYOND_BAND: (
        "|(E_now - E_base) / |E_base|| exceeds EBITDA_GROWTH_BASE_POLICY_V2."
        "max_abs_rate = 10.0 (owner-confirmed R1, a modelling-domain guard). The "
        "band is on the RATE, not on the base's size: a base that is a rounding "
        "of the business is the usual cause, but an 11x rise from a healthy base "
        "is refused under this name too. Refused so it cannot enter the cohort "
        "dispersion sample; |rate| == 10.0 exactly is computed."),
    R_EBITDA_NON_POSITIVE: (
        "EBITDA at the period is zero or negative and this ratio divides by it "
        "(fcf_conversion, net_debt_ebitda, ev_ebitda_supplement). Owner ruling "
        "R11 (fcf) and R15 (EV), and the v1 precedent for net_debt_ebitda: a "
        "multiple of a loss is not a large number, it is an undefined one."),
    R_NO_TOTAL_DEBT_AT_P: (
        "No concept_ladder_v2 total_debt rung carries a value AT the EBITDA "
        "period end (all components for a SUM rung). Balance-sheet instants are "
        "anchored at the EBITDA period (owner ruling R12, no period mixing); a "
        "debt figure from another balance sheet is not consulted. UNTAGGED is "
        "not ZERO (pit_policy.absence_limits)."),
    R_NO_CASH_AT_P: (
        "No cash ladder rung carries a value AT the EBITDA period end. Cash is "
        "REQUIRED by the digested formula (net_debt = total_debt - cash; "
        "enterprise_value = market_cap + total_debt - cash) and is never "
        "substituted with 0.0 (owner ruling R12; pit_policy.absence_limits)."),
    R_NO_OCF_AT_P: (
        "No operating_cash_flow ladder rung carries a value for the EBITDA flow "
        "period. Free cash flow is one period's cash flows against the same "
        "period's EBITDA (owner ruling R12); a neighbouring year is not it."),
    R_NO_CAPEX_AT_P: (
        "No capex ladder rung carries a value for the EBITDA flow period. capex "
        "is the binding leaf of the FCF pair (67.5 / 71.1 / 69.4% of base) and "
        "an untagged capex is not a zero outflow (pit_policy.absence_limits)."),
    R_DEBT_RUNG_REJECTED: (
        "The FIRST total_debt rung carrying a value at the EBITDA period end is "
        "LongTermDebtNoncurrent, which DEBT_RUNG_EVEBITDA_V1 REJECTED ON "
        "MEASUREMENT (22.55% cheap-decile churn, sectoral understatement). Under "
        "DEBT_RUNG_GATE_V1 (owner rulings R10, R16) lower rungs are not "
        "consulted and the factor is refused by name, for peers and target alike."),
    R_DEBT_RUNG_UNDETERMINED: (
        "The FIRST total_debt rung carrying a value at the EBITDA period end has "
        "UNDETERMINED status under DEBT_RUNG_EVEBITDA_V1 -- too few validation "
        "pairs to reach a verdict. Not 'probably fine' and not 'bad': refused by "
        "name under DEBT_RUNG_GATE_V1 (owner rulings R10, R16)."),
    R_NO_SCORED_LISTING: (
        "pit_identity.scored_universe_as_of lists no listing for this entity on "
        "the date: no listing valid on the day with a real trade within the "
        "price window, or the entity is quarantined. Every price-dependent "
        "factor is SURVIVOR_ONLY by construction and refuses here by name."),
    R_LISTING_AMBIGUOUS: (
        "More than one scored listing is valid for this entity on the date and "
        "the owner's rule (R19) forbids share-class switching: a price from one "
        "class against a share count for the issuer is a silent multiple. "
        "Refused, never guessed."),
    R_NO_VALUATION_PRICE: (
        "No raw as-traded close on the score date nor any earlier VALID trade "
        "within 10 calendar days on the same listing: every bar in the window "
        "was skipped (NULL, non-positive or zero-volume print) or the listing "
        "has no bar there (VALUATION_PRICE_POLICY_V1, owner ruling R19). The "
        "last pit_rawprice reason travels in resolved_inputs_json ('pr') and "
        "the skipped bars in 'sk'; a zero-volume print is not a trade."),
    R_PRICE_STRADDLES_ACTION: (
        "The valuation price had to roll back to an earlier session and a "
        "share-applicable corporate action is dated in (bar_date, score date]: "
        "the price is on one share basis and the score-date share count on "
        "another, so a market cap or a P/E across them is wrong by the whole "
        "ratio (VALUATION_PRICE_POLICY_V1 straddle rule, R18/R19)."),
    R_MARKET_CAP_UNAVAILABLE: (
        "pit_rawprice.market_cap_as_of_detail returned no market cap under the "
        "STRICT share-class policy: no point-in-time count, a count that cannot "
        "be shown to cover the issuer, or a zero count. The pit_rawprice reason "
        "travels in resolved_inputs_json ('mr'); the answer is UNAVAILABLE "
        "rather than a plausible 2x error."),
    R_MARKET_CAP_NON_POSITIVE: (
        "The market cap resolved but is not positive (a zero or negative "
        "product of price and shares). A $0 market cap would rank as infinitely "
        "cheap and infinitely unlevered; refused by name (MARKET_CAP_DOMAIN_V1)."),
    R_EV_ABOVE_BAND: (
        "enterprise_value / ebitda exceeds 300, the upper bound of the v1 "
        "validity band (EV_EBITDA_SIX). The lower bound is WITHDRAWN under owner "
        "ruling R15 (a negative enterprise value is VALID and saturates the "
        "cheap side); the upper bound stands where the ruling did not reach."),
    R_NO_TTM_EPS: (
        "pit_eps_cohort.EntityTtm.select found no TTM EPS knowable on the date "
        "under the EPS ladder -- never filed, not yet filed, or the newest TTM "
        "period is stale under the 15-month bound. The EARNINGS are absent, so "
        "both P/E legs are absent (PE_DOMAIN_V1)."),
    R_EPS_LOSS: (
        "The TTM EPS is NEGATIVE: the earnings are available and the MULTIPLE is "
        "not (pit_eps.PE_UNAVAILABLE_LOSS, reused verbatim). abs() would map the "
        "deepest loss-makers onto the cheapest securities in the market, so there "
        "is no flag to turn this refusal off."),
    R_EPS_ZERO: (
        "The TTM EPS is a REPORTED ZERO (pit_eps.PE_UNAVAILABLE_ZERO, reused "
        "verbatim): a filed zero is a value, not an absence, and a multiple over "
        "it is undefined rather than large."),
    R_EPS_NEAR_ZERO: (
        "0 < TTM EPS <= pit_eps.EPS_NEAR_ZERO_ABS ($0.01): the EPS sits at the "
        "reporting quantum, so the multiple is a statement about rounding. Owner "
        "ruling R17 (2026-09-23): excluded from BOTH P/E legs and from the "
        "pe_relative median population (PE_DOMAIN_V1)."),
    R_SHARE_BASIS_UNRESOLVED: (
        "pit_price_basis.normalised_pe could not carry the TTM EPS to the "
        "score-date share basis: the corporate-action gate (v2, owner ruling R18) "
        "reported the listing's coverage UNKNOWN, a needed action still in the "
        "future, or an incomplete ratio. The gate status travels in "
        "resolved_inputs_json ('gs'). A P/E across an unresolved split is wrong "
        "by the whole ratio, so it is refused (REFUSED_SHARE_BASIS_UNKNOWN)."),
    R_PE_RELATIVE_NEEDS_ABSOLUTE: (
        "The context leg ranks the multiple the anchor leg computes and cannot "
        "exist without it (pit_valuation_spec.SUBFACTORS: depends_on = "
        "pe_absolute). Refused for DEPENDENCY, distinct from a cohort refusal, so "
        "the row tells 'the cohort failed' from 'the leg it hangs off failed'."),
    R_COHORT_VALUES_BELOW_FLOOR: (
        "Fewer VALUED cohort members than pit_factor_spec.PEER_FLOOR_V1 requires "
        "for this factor (owner ruling R14: one frozen per-factor floor, one "
        "distinct refusal). Distinct from eligible_peers' insufficient_peers, "
        "which counts availability -- an UPPER BOUND -- and from the normaliser's "
        "own floors, which every entry here meets by construction."),
    R_BENCHMARK_BAND_TOO_THIN: (
        "Fewer than pit_normalization.BENCHMARK_MIN_COHORT_V1 = 3 peers fell "
        "inside the 50-75 EBITDA band of an otherwise sufficient vector (owner "
        "ruling R9: the floor is 3 and the band is NEVER widened). An excess "
        "against a bar taken from one or two companies is not a small number, "
        "it is no number."),
    R_NO_ANCHOR_POPULATION: (
        "The median anchor of a V leg (pe_relative, ev_ebitda_supplement) had no "
        "population after the domain screens -- every eligible member's multiple "
        "was refused (loss, zero, near-zero, debt rung, EBITDA <= 0). Reused "
        "verbatim from pit_valuation_spec."),
    R_NO_OPERATING_INCOME: (
        "pit_coverage.resolve_concept found no OperatingIncomeLoss (qtrs=4) period "
        "that survives pit_policy.is_stale(period, as_of, 4, 'operating_income'). "
        "Coverage has no numerator; banks and insurers largely never tag it."),
    R_NO_INTEREST_AT_P: (
        "No interest_expense ladder rung (InterestExpense, InterestExpenseDebt, "
        "InterestAndDebtExpense, InterestExpenseNonoperating; qtrs=4) carries a "
        "value AT the resolved operating-income period. UNTAGGED is not ZERO: "
        "this store cannot tell a debt-free filer from an untagged amount "
        "(pit_policy.absence_limits), so the row is unavailable, never +inf."),
    R_INTEREST_ZERO: (
        "Owner domain policy 2026-09-22 (INTEREST_COVERAGE_DOMAIN_V1): coverage is "
        "defined for interest_expense > 0. A tagged ZERO has no denominator, so "
        "this is a NAMED state rather than an enormous 'excellent' ratio. "
        "Negative operating income is NOT refused."),
    R_INTEREST_NEGATIVE: (
        "Owner domain policy 2026-09-22 (INTEREST_COVERAGE_DOMAIN_V1): a NEGATIVE "
        "interest expense is a net figure (or a sign the ladder did not "
        "anticipate) and would invert the ratio's meaning, so it is this NAMED "
        "state, never a coverage value."),
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
    #: Which FROZEN scorer turns the raw value into a score: "registry" for a
    #: pit_normalization transform under `norm_key`, or the qualified name of
    #: a pit_valuation_spec scorer for the three V legs (R13: no hidden engine
    #: normalisation). validate() resolves the name and refuses an unknown one.
    scorer: str = "registry"

    @property
    def computable(self) -> bool:
        return self.verdict == VERDICT_COMPUTABLE

    @property
    def peer_floor(self) -> Optional[int]:
        """PEER_FLOOR_V1's floor on VALUED cohort members, or None."""
        return pit_factor_spec.PEER_FLOOR_V1.get(self.key)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key, "block": self.block, "role": self.role,
            "verdict": self.verdict, "norm_key": self.norm_key,
            "scorer": self.scorer,
            "spec_key": self.spec_key, "needs_cohort": self.needs_cohort,
            "cohort_use": self.cohort_use, "peer_floor": self.peer_floor,
            "refusal_reason": self.refusal_reason,
            "refusal_why": (REASON_WHY.get(self.refusal_reason)
                            if self.refusal_reason else None),
            "mask_code": self.mask_code, "note": self.note,
        }


COHORT_RANK = "rank_population_excludes_target"
COHORT_DISPERSION = "dispersion_sample_includes_target"
#: The benchmark's cohort: (EBITDA, margin) PAIRS, target INCLUDED in the
#: vector and in the band (owner ruling R7), ONE cohort-constant M_s per peer
#: set, and the scale over the WHOLE eligible margin vector (R8).
COHORT_BENCHMARK = "benchmark_pairs_includes_target"
#: The two V legs anchored at a cohort MEDIAN, target INCLUDED (R15, R17).
COHORT_MEDIAN_ANCHOR = "median_anchor_includes_target"

_B = pit_factor_blocks

FACTOR_PLAN: Dict[str, FactorPlan] = {
    # ---- E ---------------------------------------------------------------
    "ebitda_benchmark": FactorPlan(
        key="ebitda_benchmark", block=_B.BLOCK_E, role=_B.ROLE_SCORING,
        verdict=VERDICT_COMPUTABLE, norm_key="EBITDAExcessLevel",
        spec_key="ebitda_benchmark", needs_cohort=True,
        cohort_use=COHORT_BENCHMARK, mask_code="BENCH",
        note=("input margin = EBITDA / revenue AT P; bar M_s = mean margin of "
              "the 50-75 EBITDA band (benchmark_margin_50_75), target INCLUDED, "
              "one M_s per peer set, band >= 3 or benchmark_band_too_thin, "
              "vector >= 12 or cohort_values_below_floor; BENCHMARK_ANCHORED "
              "with k = cohort IQR over the whole eligible margin vector (D1; "
              "owner rulings R7-R9, R14 of 2026-09-23). 0.1225 of the company "
              "score, the largest single factor weight.")),
    "ebitda_growth": FactorPlan(
        key="ebitda_growth", block=_B.BLOCK_E, role=_B.ROLE_SCORING,
        verdict=VERDICT_COMPUTABLE, norm_key="EBITDAGrowth",
        spec_key="ebitda_growth", needs_cohort=True,
        cohort_use=COHORT_DISPERSION, mask_code="GROW",
        note=("input (E_t - E_t-1) / |E_t-1| under EBITDA_GROWTH_BASE_POLICY_V1, "
              "declared by pit_factor_spec.FORMULAS_V1 and the registry (D3: A)")),
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
        note=("input growth_t - growth_t-1, two A-rates, THREE EBITDA "
              "observations, no asset base (D3: A)")),
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
        verdict=VERDICT_COMPUTABLE, norm_key=None, spec_key="pe_ratio",
        needs_cohort=False, cohort_use="", mask_code="PE",
        scorer="pit_valuation_spec.valuation_score_detail",
        note=("pe = raw as-traded price / TTM EPS on the score-date share basis "
              "(VALUATION_PRICE_POLICY_V1, PRICE_BASIS_TRANSLATION_V1, corporate-"
              "action gate v2); loss / zero / near-zero-positive EPS refused by "
              "name (PE_DOMAIN_V1, R17); the CONVEX transform at the fixed 20x "
              "anchor, sign embedded. No cohort.")),
    "pe_relative": FactorPlan(
        key="pe_relative", block=_B.BLOCK_V, role=_B.ROLE_SCORING,
        verdict=VERDICT_COMPUTABLE, norm_key=None, spec_key="pe_ratio",
        needs_cohort=True, cohort_use=COHORT_MEDIAN_ANCHOR, mask_code="PEPeer",
        scorer="pit_valuation_spec.pe_relative_score_detail",
        note=("the SAME pe, the CONVEX transform around the coherent cohort's "
              "median (target INCLUDED, near-zero members excluded; owner ruling "
              "R17); depends on pe_absolute (pe_relative_needs_absolute); cohort "
              "under PRICED_EPS_V2 with the sic2 ceiling; floor 3 valued members.")),
    "ev_ebitda_supplement": FactorPlan(
        key="ev_ebitda_supplement", block=_B.BLOCK_V, role=_B.ROLE_SCORING,
        verdict=VERDICT_COMPUTABLE, norm_key=None, spec_key="ev_ebitda",
        needs_cohort=True, cohort_use=COHORT_MEDIAN_ANCHOR, mask_code="EVEBITDA",
        scorer="pit_valuation_spec.ev_ebitda_score_detail",
        note=("ev_ebitda = (market_cap + total_debt - cash) / ebitda, debt and "
              "cash AT the EBITDA period end, EBITDA > 0, strict first-rung debt "
              "gate (R16), negative EV VALID; 100 * tanh((median - x) / k) with "
              "k = the cohort IQR over the whole eligible vector, LOWER better "
              "(owner ruling R15, EV_TRANSFORM_PARAMS_V1); floor 12 valued members.")),

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
        verdict=VERDICT_COMPUTABLE, norm_key="FCFConversionRank",
        spec_key="fcf_conversion", needs_cohort=True,
        cohort_use=COHORT_RANK, mask_code="FCF",
        note=("input (operating_cash_flow - capex) / ebitda, both flows from the "
              "EBITDA period, EBITDA > 0 (FCF_DOMAIN_V1; owner rulings R11, R12, "
              "R13); PERCENTILE_RANK, higher better.")),
    "interest_coverage": FactorPlan(
        key="interest_coverage", block=_B.BLOCK_Q, role=_B.ROLE_SCORING,
        verdict=VERDICT_COMPUTABLE, norm_key="InterestCoverageRank",
        spec_key="interest_coverage", needs_cohort=True,
        cohort_use=COHORT_RANK, mask_code="ICOV",
        note=("input operating_income / interest_expense AT the same period; "
              "interest_expense > 0 or a NAMED refusal; operating_income <= 0 is "
              "the FLOOR STATE, scored -100 and excluded from the rank population "
              "(INTEREST_COVERAGE_DOMAIN_V2; owner rulings R2, R4, R5).")),
    "net_debt_ebitda": FactorPlan(
        key="net_debt_ebitda", block=_B.BLOCK_Q, role=_B.ROLE_SCORING,
        verdict=VERDICT_COMPUTABLE, norm_key="NetDebtEBITDARank",
        spec_key="net_debt_ebitda", needs_cohort=True,
        cohort_use=COHORT_RANK, mask_code="NDE",
        note=("input (total_debt - cash) / ebitda, both instants AT the EBITDA "
              "period end, EBITDA > 0, strict debt-rung gate; the rung is the "
              "row's source_tag and its bound travels in 'db' (LEVERAGE_DOMAIN_V1, "
              "DEBT_RUNG_GATE_V1; owner rulings R10-R13); PERCENTILE_RANK, lower "
              "better.")),
    "debt_market_cap": FactorPlan(
        key="debt_market_cap", block=_B.BLOCK_Q, role=_B.ROLE_SCORING,
        verdict=VERDICT_COMPUTABLE, norm_key="DebtMarketCapRank",
        spec_key="debt_market_cap", needs_cohort=True,
        cohort_use=COHORT_RANK, mask_code="DMC",
        note=("input total_debt / market_cap; debt AT the EBITDA period end under "
              "the strict rung gate, market cap = valuation price x defensible "
              "shares (MARKET_CAP_DOMAIN_V1; owner rulings R10, R12, R19, R20); "
              "PERCENTILE_RANK, lower better. SURVIVOR_ONLY by construction.")),
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
    "listing_id is written ONLY where exactly one scored listing resolved for "
    "the entity on the date (pit_identity.scored_universe_as_of); it is NULL "
    "on every row of an unpriced or ambiguously listed entity, and the four "
    "price-dependent keys are then refused by name. The priced universe is "
    "SURVIVOR_ONLY (2,574 listings alive in 2026), so V and debt_market_cap "
    "carry that bias on every row that resolves them.",
    "fact_max_age_days is (as_of - newest source period_end) in days, which is "
    "the age of the FISCAL PERIOD, not of the filing.",
    "All thirteen feature keys are computable under pit_replay/1.2 and the "
    "reachable block weight is the whole model, 0.85; what a company reaches "
    "is on its row. WITHIN_BLOCK_FLOOR_V1 (owner ruling R6) closes the "
    "single-factor Q path that 1.1 opened: a block below its floor is "
    "UNAVAILABLE with within_block_coverage_below_floor, so companies scored "
    "under 1.1 on E plus interest_coverage alone are refused under 1.2 BY "
    "DESIGN.",
    "The total_debt used by net_debt_ebitda, debt_market_cap and EV/EBITDA is "
    "the FIRST concept_ladder_v2 rung carrying a value at the EBITDA period "
    "end, gated by DEBT_RUNG_EVEBITDA_V1. An ELIGIBLE rung may be a "
    "lower_bound quantity (LongTermDebtNoncurrent + LongTermDebtCurrent, "
    "median 0.932 of the three-way total); it is scored WITH that label on "
    "the row ('db'), never silently as total debt.",
    "pit_score.valuation_data_quality is NULL and valuation_quality_rule is "
    "UNDECIDED on every row: no combination rule is adopted "
    "(pit_valuation_spec.QUALITY_RULE_STATUS) and no quality component is "
    "authored for a replay row. earnings_discontinuity_state is "
    "EARNINGS_SIGN_CROSS only where the SELECTED TTM row's own sign_crossing "
    "flag is set -- the store's marker, not a pair classification.",
    "The EV/EBITDA transform has NO fixed fallback scale: a cohort with fewer "
    "than 8 valued multiples or with zero dispersion refuses by "
    "pit_normalization's own reason rather than scoring against an invented "
    "k. The reachable V weight on such cohorts is the P/E legs' 0.80.",
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
    if FACTOR_SPEC_VERSION != pit_frozen_spec.ENGINE_MUST_DERIVE_FROM:
        raise ReplayRefused(
            "REPLAY REFUSED -- the engine derives eligibility from %s but the live "
            "freeze (%s) requires %s; no write transaction was opened"
            % (FACTOR_SPEC_VERSION, pit_frozen_spec.SPEC_FREEZE_VERSION,
               pit_frozen_spec.ENGINE_MUST_DERIVE_FROM))
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

    Three parents are seeded, because three are reached: `pit_entity` (every
    written table points at it), `pit_peer_set` (pit_feature and
    pit_pillar_score carry a peer_set_id) and, since pit_replay/1.2,
    `pit_listing` -- the four price-dependent keys and the score row carry the
    listing_id the valuation price was taken on (owner ruling R19).

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
    listings = store_conn.execute(
        "SELECT listing_id, entity_id, symbol, exchange, instrument_type, "
        "currency, first_trade_date, valid_from, valid_to, confidence, source, "
        "created_at, instrument_kind FROM pit_listing").fetchall()
    pilot_conn.executemany(
        "INSERT OR IGNORE INTO pit_listing (listing_id, entity_id, symbol, "
        "exchange, instrument_type, currency, first_trade_date, valid_from, "
        "valid_to, confidence, source, created_at, instrument_kind) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [tuple(r) for r in listings])
    pilot_conn.commit()
    return {
        "option": "SEED_PARENTS_THEN_REBASELINE",
        "pit_entity_rows": len(entities),
        "pit_peer_set_rows": len(peer_sets),
        "pit_peer_set_dates": days,
        "pit_listing_rows": len(listings),
        "pit_listing_why": ("the price-dependent feature rows and the score row "
                            "carry the listing the valuation price was taken on "
                            "(pit_replay/1.2, owner ruling R19); the FK is REAL"),
        "foreign_keys": "ON",
        "elapsed_s": round(time.monotonic() - t0, 3),
    }


# ==========================================================================
# (3) PRIMITIVES -- one entity's point-in-time facts
# ==========================================================================

@dataclass
class Primitives:
    """Everything the six computable factors need, resolved once per entity."""

    entity_id: int
    ebitda_period: Optional[str] = None
    ebitda: Optional[float] = None
    lag1_period: Optional[str] = None
    ebitda_lag1: Optional[float] = None
    lag2_period: Optional[str] = None
    ebitda_lag2: Optional[float] = None
    growth_lag1: Optional[float] = None       # (E_t-1 - E_t-2) / |E_t-2|
    growth_base_sign: Optional[str] = None    # "neg" when E_t-1 < 0 (sign transition, on the row)
    operating_income_period: Optional[str] = None
    operating_income: Optional[float] = None
    interest_rung: Optional[str] = None
    interest_expense: Optional[float] = None  # a LEVEL at operating_income_period
    revenue_at_ebitda_period: Optional[float] = None
    revenue_rung: Optional[str] = None
    revenue_period: Optional[str] = None
    revenue: Optional[float] = None
    revenue_lag1_period: Optional[str] = None
    revenue_lag1: Optional[float] = None
    cpi_t: Optional[float] = None
    cpi_lag1: Optional[float] = None

    # ---- spec_freeze_v6: the balance sheet and cash flows AT the EBITDA period
    total_debt_rung: Optional[str] = None
    total_debt_bound: Optional[str] = None
    total_debt: Optional[float] = None
    debt_gate_reason: Optional[str] = None    # DEBT_RUNG_GATE_V1's verdict, or None
    cash_rung: Optional[str] = None
    cash: Optional[float] = None
    ocf_rung: Optional[str] = None
    operating_cash_flow: Optional[float] = None
    capex_rung: Optional[str] = None
    capex: Optional[float] = None
    # ---- the market side (SURVIVOR_ONLY), attached by attach_market
    listing_id: Optional[int] = None
    listing_reason: Optional[str] = None
    price_raw: Optional[float] = None
    price_bar_date: Optional[str] = None
    price_age_days: Optional[int] = None
    price_reason: Optional[str] = None
    price_skipped: List[Tuple[str, str]] = field(default_factory=list)
    eps_reason: Optional[str] = None
    market_cap: Optional[float] = None
    market_cap_reason: Optional[str] = None
    shares_used: Optional[float] = None
    split_check: Optional[str] = None
    ttm_eps: Optional[float] = None
    eps_period_end: Optional[str] = None
    eps_concept: Optional[str] = None
    eps_method: Optional[str] = None
    eps_pe_leg: Optional[str] = None
    eps_sign_crossing: int = 0
    pe_basis_factor: Optional[float] = None
    pe_gate_status: Optional[str] = None

    # derived factor inputs, None where an ingredient is missing
    margin: Optional[float] = None
    growth: Optional[float] = None
    acceleration: Optional[float] = None
    real_revenue_growth: Optional[float] = None
    coverage: Optional[float] = None          # operating_income / interest_expense
    coverage_floor_state: bool = False        # R5: operating_income <= 0, interest > 0
    net_debt_ebitda: Optional[float] = None
    fcf_conversion: Optional[float] = None
    debt_market_cap: Optional[float] = None
    enterprise_value: Optional[float] = None
    ev_ebitda: Optional[float] = None
    pe: Optional[float] = None

    #: derived key -> the short code saying which ingredient failed. Never a
    #: zero: "no value" and "a value of zero" are different facts.
    why: Dict[str, str] = field(default_factory=dict)


def _rate(now: float, base: float) -> Tuple[Optional[float], Optional[str]]:
    """EBITDA_GROWTH_BASE_POLICY_V1: (now - base) / |base|, or a NAMED refusal.

    A zero base has no rate. A base so small that the rate exceeds the
    policy's band is a statement about the base, not about growth, and is
    refused rather than allowed into the cohort dispersion sample. A negative
    base is computed: a loss shrinking from -100 to -50 is +0.5.
    """
    if base == 0.0:
        return None, R_EBITDA_BASE_ZERO
    rate = (now - base) / abs(base)
    if abs(rate) > float(pit_factor_spec.EBITDA_GROWTH_BASE_POLICY_V2["max_abs_rate"]):
        return None, R_EBITDA_RATE_BEYOND_BAND
    return rate, None


def _level_at(index: Mapping[Tuple[int, str, int], Mapping[str, float]],
              entity_id: int, ladder: Any, period: str, qtrs: int,
              as_of: str) -> Tuple[Optional[str], Optional[float]]:
    """(rung_key, value) for the FIRST ladder rung carrying a value AT `period`.

    The interest@P pattern generalised (owner ruling R2: the first valid
    concept rung for the period, never mixing rungs), for instants (qtrs=0)
    and durations (qtrs=4) alike. A SUM rung needs EVERY component at the
    period -- two of three is a wrong number, not a partial one. Lower rungs
    are not consulted once a rung answers, which is what makes the debt-rung
    gate (DEBT_RUNG_GATE_V1) a decision about ONE rung.
    """
    for rung in ladder.rungs:
        if not rung.eligible(as_of):
            continue
        maps = [index.get((entity_id, tag, qtrs)) or {} for tag in rung.tags]
        if all(period in m for m in maps):
            return rung.key, sum(float(m[period]) for m in maps)
    return None, None


def _finite(value: Any) -> bool:
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def resolve_primitives(index: Mapping[Tuple[int, str, int], Mapping[str, float]],
                       entity_id: int, as_of: str,
                       ladders: Mapping[str, Any],
                       cpi: Any) -> Primitives:
    """One entity's facts and the five derived inputs.

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

    # ---- the five derived inputs, each with its own refusal ---------------

    # margin = EBITDA / revenue AT THE SAME PERIOD
    if out.ebitda is None:
        out.why["margin"] = R_NO_EBITDA
    elif not out.revenue_at_ebitda_period:
        out.why["margin"] = R_NO_REVENUE_AT_P
    else:
        out.margin = float(out.ebitda) / float(out.revenue_at_ebitda_period)

    # growth = (E_t - E_{t-1}) / |E_{t-1}|            (D3: A, owner 2026-09-22)
    if out.ebitda is None:
        out.why["growth"] = R_NO_EBITDA
    elif out.ebitda_lag1 is None:
        out.why["growth"] = R_NO_LAG1
    else:
        out.growth, why = _rate(float(out.ebitda), float(out.ebitda_lag1))
        if why:
            out.why["growth"] = why
        elif float(out.ebitda_lag1) < 0.0:
            out.growth_base_sign = "neg"

    # acceleration = growth_t - growth_{t-1}   (D3: A; NOT a second difference
    # over assets). Either rate refused under the base policy refuses this.
    if out.ebitda is None:
        out.why["acceleration"] = R_NO_EBITDA
    elif out.ebitda_lag1 is None:
        out.why["acceleration"] = R_NO_LAG1
    elif out.ebitda_lag2 is None:
        out.why["acceleration"] = R_NO_LAG2
    elif out.growth is None:
        out.why["acceleration"] = out.why["growth"]
    else:
        out.growth_lag1, why = _rate(float(out.ebitda_lag1), float(out.ebitda_lag2))
        if why:
            out.why["acceleration"] = why
        else:
            out.acceleration = out.growth - out.growth_lag1

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

    # interest coverage = operating_income / interest_expense AT THE SAME PERIOD
    # (D3: operating income, owner 2026-09-22; INTEREST_COVERAGE_DOMAIN_V1).
    # Operating income resolves like any concept (newest non-stale annual
    # period); interest expense is a LEVEL at that period, walking its ladder
    # in rung order -- the revenue@P pattern, because a ratio is one period's
    # numbers. The domain rule runs BEFORE any direction is applied.
    _oi_rung, oi_period, oi_value = pit_coverage.resolve_concept(
        index, entity_id, "operating_income", day, ladders)
    out.operating_income_period, out.operating_income = oi_period, oi_value
    if oi_period is None or oi_value is None:
        out.why["coverage"] = R_NO_OPERATING_INCOME
    else:
        for rung in ladders["interest_expense"].rungs:
            if not rung.eligible(day):
                continue
            found = index.get((entity_id, rung.tags[0], 4)) or {}
            if oi_period in found:
                out.interest_rung = rung.key
                out.interest_expense = float(found[oi_period])
                break
        if out.interest_expense is None:
            out.why["coverage"] = R_NO_INTEREST_AT_P
        elif out.interest_expense == 0.0:
            out.why["coverage"] = R_INTEREST_ZERO
        elif out.interest_expense < 0.0:
            out.why["coverage"] = R_INTEREST_NEGATIVE
        else:
            out.coverage = float(out.operating_income) / out.interest_expense
            # R5 (INTEREST_COVERAGE_DOMAIN_V2): a non-positive operating income
            # against a real interest bill is the FLOOR STATE -- the raw
            # coverage is kept, the row is scored at the rank floor and it
            # leaves the rank population.
            out.coverage_floor_state = float(out.operating_income) <= 0.0

    # ---- the balance sheet and the cash flows AT THE EBITDA PERIOD ---------
    # (owner ruling R12: no period mixing). Debt walks its ladder STRICTLY:
    # the first rung with a value at P decides through DEBT_RUNG_EVEBITDA_V1
    # (R10, R16) and its bound rides on the row.
    if current:
        out.total_debt_rung, out.total_debt = _level_at(
            index, entity_id, ladders["total_debt"], current, 0, day)
        if out.total_debt_rung is not None:
            out.total_debt_bound = pit_policy.quantity_for(
                "total_debt", out.total_debt_rung, LADDER_VERSION)["bound"] or None
            out.debt_gate_reason = pit_valuation_spec.debt_gate(out.total_debt_rung)
        out.cash_rung, out.cash = _level_at(
            index, entity_id, ladders["cash"], current, 0, day)
        out.ocf_rung, out.operating_cash_flow = _level_at(
            index, entity_id, ladders["operating_cash_flow"], current, 4, day)
        out.capex_rung, out.capex = _level_at(
            index, entity_id, ladders["capex"], current, 4, day)

    # net_debt_ebitda = (total_debt - cash) / ebitda      (LEVERAGE_DOMAIN_V1)
    if out.ebitda is None:
        out.why["net_debt_ebitda"] = R_NO_EBITDA
    elif float(out.ebitda) <= 0.0:
        out.why["net_debt_ebitda"] = R_EBITDA_NON_POSITIVE
    elif out.total_debt_rung is None:
        out.why["net_debt_ebitda"] = R_NO_TOTAL_DEBT_AT_P
    elif out.debt_gate_reason:
        out.why["net_debt_ebitda"] = out.debt_gate_reason
    elif out.cash is None:
        out.why["net_debt_ebitda"] = R_NO_CASH_AT_P
    else:
        out.net_debt_ebitda = (float(out.total_debt) - float(out.cash)) / float(out.ebitda)

    # fcf_conversion = (operating_cash_flow - capex) / ebitda  (FCF_DOMAIN_V1)
    if out.ebitda is None:
        out.why["fcf_conversion"] = R_NO_EBITDA
    elif float(out.ebitda) <= 0.0:
        out.why["fcf_conversion"] = R_EBITDA_NON_POSITIVE
    elif out.operating_cash_flow is None:
        out.why["fcf_conversion"] = R_NO_OCF_AT_P
    elif out.capex is None:
        out.why["fcf_conversion"] = R_NO_CAPEX_AT_P
    else:
        out.fcf_conversion = ((float(out.operating_cash_flow) - float(out.capex))
                              / float(out.ebitda))

    # The price-dependent keys are refused here for a FUNDAMENTAL reason only;
    # attach_market() supplies the market side and finishes them. The context
    # leg's ONE rule: it is refused for DEPENDENCY whenever the anchor leg is,
    # whatever removed the anchor leg (the anchor row carries that cause).
    for key in ("debt_market_cap", "ev_ebitda_supplement", "pe_absolute"):
        out.why[key] = R_NO_SCORED_LISTING
    out.why["pe_relative"] = R_PE_RELATIVE_NEEDS_ABSOLUTE
    return out


# ==========================================================================
# (3b) THE MARKET SIDE -- one listing, one price, one market cap per entity
# ==========================================================================

def resolve_listings(conn: sqlite3.Connection, as_of: str) -> Dict[int, Any]:
    """entity_id -> listing_id, or a refusal code where the rule cannot pick one.

    pit_identity.scored_universe_as_of lists every (entity, listing) that is
    valid, traded and unquarantined on the date. An entity with EXACTLY ONE
    such listing resolves; with more than one it is REFUSED (owner ruling R19:
    no share-class switching); with none it is absent from the map and every
    price-dependent key reads no_scored_listing.
    """
    import pit_identity
    seen: Dict[int, List[int]] = {}
    for row in pit_identity.scored_universe_as_of(conn, str(as_of)[:10]):
        seen.setdefault(int(row["entity_id"]), []).append(int(row["listing_id"]))
    return {e: (ids[0] if len(ids) == 1 else R_LISTING_AMBIGUOUS)
            for e, ids in seen.items()}


def valuation_price(conn: sqlite3.Connection, listing_id: int, as_of: str
                    ) -> Dict[str, Any]:
    """THE ONE valuation price resolver (VALUATION_PRICE_POLICY_V1, R19; the
    basis translated ONCE through PRICE_BASIS_TRANSLATION_V1, R20).

    The exact score-date raw close, else the latest PRIOR valid trade within
    the policy's ten calendar days, same listing. Returns the pit_rawprice
    record with `price_age_days` and `valuation_basis` added, or the record's
    own refusal. An adjusted basis cannot pass: translate_price_basis raises.
    """
    policy = pit_price_basis.VALUATION_PRICE_POLICY_V1
    day = str(as_of)[:10]
    days_back = int(policy["roll_back_days"])
    floor_day = (_dt.date.fromisoformat(day) - _dt.timedelta(days=days_back)).isoformat()
    skipped: List[Tuple[str, str]] = []
    cursor = day
    # NEWEST FIRST, one bar at a time. A NULL, non-positive or zero-volume
    # print is SKIPPED and the next earlier bar tried (invalid_bar_rule); the
    # floor never widens. Anything else that refuses (no bar, an unusable
    # corporate action inside the reconstruction window) is a refusal every
    # earlier bar shares, so it is not stepped over.
    while True:
        window = (_dt.date.fromisoformat(cursor) - _dt.date.fromisoformat(floor_day)).days
        record = pit_rawprice.raw_close_as_of(
            conn, int(listing_id), cursor, roll_back_days=max(0, window),
            require_traded=bool(policy["require_traded"]))
        if record.get("available"):
            break
        reason = record.get("reason")
        bar_date = record.get("bar_date")
        if (reason in (pit_rawprice.REASON_NO_CLOSE, pit_rawprice.REASON_NON_POSITIVE_CLOSE,
                       pit_rawprice.REASON_ZERO_VOLUME_PRINT)
                and bar_date and str(bar_date)[:10] > floor_day):
            skipped.append((str(bar_date)[:10], str(reason)))
            cursor = (_dt.date.fromisoformat(str(bar_date)[:10])
                      - _dt.timedelta(days=1)).isoformat()
            continue
        break
    record["skipped_bars"] = skipped
    if record.get("available"):
        basis = pit_price_basis.translate_price_basis(record["price_basis"])
        pit_price_basis.assert_price_basis(pit_price_basis.PRICE_ROLE_VALUATION, basis)
        record["valuation_basis"] = basis
        bar = str(record["bar_date"])[:10]
        record["price_age_days"] = (_dt.date.fromisoformat(day)
                                    - _dt.date.fromisoformat(bar)).days
        if record["price_age_days"] > 0:
            # straddle_rule: a share-applicable action between the bar and the
            # score date puts the price and the share basis on two sides of it.
            straddled = conn.execute(
                "SELECT 1 FROM pit_corporate_action WHERE listing_id = ? "
                "AND applies_to_shares = 1 AND event_date > ? AND event_date <= ? "
                "LIMIT 1", (int(listing_id), bar, day)).fetchone()
            if straddled is not None:
                record["available"] = False
                record["reason"] = R_PRICE_STRADDLES_ACTION
                record["raw_close"] = None
    return record


def attach_market(prim: Primitives, conn: sqlite3.Connection, as_of: str,
                  listings: Mapping[int, Any],
                  ttm_table: Mapping[int, Any]) -> None:
    """Finish the four price-dependent keys on one Primitives, in place.

    Reads the store for the price, the share count and the split window --
    the only per-entity reads the engine makes after the fact index. Runs
    for cohort members as well as targets, because a member's multiple must
    be formed by the SAME rules as the target's (R16: same gate for peers).
    """
    day = str(as_of)[:10]
    entity_id = int(prim.entity_id)
    market_keys = ("debt_market_cap", "ev_ebitda_supplement", "pe_absolute",
                   "pe_relative")

    listing = listings.get(entity_id)
    if listing is None:
        prim.listing_reason = R_NO_SCORED_LISTING
        return
    if listing == R_LISTING_AMBIGUOUS:
        prim.listing_reason = R_LISTING_AMBIGUOUS
        for key in market_keys:
            prim.why[key] = R_LISTING_AMBIGUOUS
        prim.why["pe_relative"] = R_PE_RELATIVE_NEEDS_ABSOLUTE
        return
    prim.listing_id = int(listing)

    # ---- the price ----------------------------------------------------------
    px = valuation_price(conn, prim.listing_id, day)
    prim.price_skipped = list(px.get("skipped_bars") or [])
    if not px.get("available"):
        prim.price_reason = px.get("reason") or pit_rawprice.REASON_NO_PRICE
        code = (R_PRICE_STRADDLES_ACTION if prim.price_reason == R_PRICE_STRADDLES_ACTION
                else R_NO_VALUATION_PRICE)
        for key in market_keys:
            prim.why[key] = code
        prim.why["pe_relative"] = R_PE_RELATIVE_NEEDS_ABSOLUTE
        return
    prim.price_raw = float(px["raw_close"])
    prim.price_bar_date = px.get("bar_date")
    prim.price_age_days = px.get("price_age_days")

    # ---- the market cap (debt_market_cap, EV) -------------------------------
    mc = pit_rawprice.market_cap_as_of_detail(
        conn, entity_id, day, listing_id=prim.listing_id, raw_price=prim.price_raw,
        class_policy=pit_rawprice.CLASS_POLICY_STRICT)
    if mc.get("market_cap") is None:
        prim.market_cap_reason = mc.get("reason") or R_MARKET_CAP_UNAVAILABLE
        prim.why["debt_market_cap"] = R_MARKET_CAP_UNAVAILABLE
        prim.why["ev_ebitda_supplement"] = R_MARKET_CAP_UNAVAILABLE
    elif float(mc["market_cap"]) <= 0.0:
        prim.market_cap_reason = R_MARKET_CAP_NON_POSITIVE
        prim.why["debt_market_cap"] = R_MARKET_CAP_NON_POSITIVE
        prim.why["ev_ebitda_supplement"] = R_MARKET_CAP_NON_POSITIVE
    else:
        prim.market_cap = float(mc["market_cap"])
        prim.shares_used = mc.get("shares_used")
        prim.split_check = mc.get("split_check")
        # debt_market_cap = total_debt / market_cap        (MARKET_CAP_DOMAIN_V1)
        if prim.ebitda_period is None:
            prim.why["debt_market_cap"] = R_NO_EBITDA
        elif prim.total_debt_rung is None:
            prim.why["debt_market_cap"] = R_NO_TOTAL_DEBT_AT_P
        elif prim.debt_gate_reason:
            prim.why["debt_market_cap"] = prim.debt_gate_reason
        else:
            prim.debt_market_cap = float(prim.total_debt) / prim.market_cap
            prim.why.pop("debt_market_cap", None)
        # ev_ebitda = (market_cap + total_debt - cash) / ebitda  (EV_EBITDA_DOMAIN_V1)
        if prim.ebitda is None:
            prim.why["ev_ebitda_supplement"] = R_NO_EBITDA
        elif float(prim.ebitda) <= 0.0:
            prim.why["ev_ebitda_supplement"] = R_EBITDA_NON_POSITIVE
        elif prim.total_debt_rung is None:
            prim.why["ev_ebitda_supplement"] = R_NO_TOTAL_DEBT_AT_P
        elif prim.debt_gate_reason:
            prim.why["ev_ebitda_supplement"] = prim.debt_gate_reason
        elif prim.cash is None:
            prim.why["ev_ebitda_supplement"] = R_NO_CASH_AT_P
        else:
            ev = prim.market_cap + float(prim.total_debt) - float(prim.cash)
            multiple = ev / float(prim.ebitda)
            prim.enterprise_value = ev
            if multiple > float(pit_factor_spec.EV_EBITDA_SIX_V2.value_band[2]):
                prim.why["ev_ebitda_supplement"] = R_EV_ABOVE_BAND
            else:
                prim.ev_ebitda = multiple
                prim.why.pop("ev_ebitda_supplement", None)

    # ---- the P/E legs (PE_DOMAIN_V1) -----------------------------------------
    entity_ttm = ttm_table.get(entity_id)
    picked = entity_ttm.select(day) if entity_ttm is not None else None
    if picked is None:
        prim.eps_reason = (entity_ttm.reason(day) if entity_ttm is not None
                           else pit_eps.REASON_NEVER_FILED)
        prim.why["pe_absolute"] = R_NO_TTM_EPS
        prim.why["pe_relative"] = R_PE_RELATIVE_NEEDS_ABSOLUTE
        return
    prim.ttm_eps = float(picked["ttm_eps"])
    prim.eps_period_end = picked["period_end"]
    prim.eps_concept = picked["concept_key"]
    prim.eps_method = picked.get("method")
    prim.eps_pe_leg = picked["pe_leg"]
    prim.eps_sign_crossing = int(picked.get("sign_crossing") or 0)
    leg = prim.eps_pe_leg
    if leg == pit_eps.PE_UNAVAILABLE_LOSS:
        why = R_EPS_LOSS
    elif leg == pit_eps.PE_UNAVAILABLE_ZERO:
        why = R_EPS_ZERO
    elif leg == pit_eps.PE_AVAILABLE_EXTREME:
        why = R_EPS_NEAR_ZERO
    else:
        why = None
    if why is None:
        npe = pit_price_basis.normalised_pe(
            conn, price_raw=prim.price_raw,
            price_basis=px["valuation_basis"], eps_pit=prim.ttm_eps,
            eps_period_end=prim.eps_period_end, as_of=day,
            listing_id=prim.listing_id)
        prim.pe_gate_status = npe.gate.status if npe.gate is not None else None
        prim.pe_basis_factor = npe.basis_factor
        if npe.pe is None:
            why = (R_EPS_ZERO if npe.reason == "zero_denominator"
                   else R_SHARE_BASIS_UNRESOLVED)
        elif float(npe.pe) <= 0.0:
            why = R_EPS_LOSS
        elif pit_eps.sign_case(npe.eps_on_score_basis) == pit_eps.SIGN_NEAR_ZERO_POSITIVE:
            # R17 on the EPS the multiple is actually taken over: a split can
            # carry an as-filed 3 cents to sub-cent on the score-date basis.
            why = R_EPS_NEAR_ZERO
        else:
            prim.pe = float(npe.pe)
    if why is None:
        prim.why.pop("pe_absolute", None)
        prim.why.pop("pe_relative", None)
    else:
        prim.why["pe_absolute"] = why
        prim.why["pe_relative"] = R_PE_RELATIVE_NEEDS_ABSOLUTE


#: factor key -> (the Primitives attribute holding its raw value, the `why` key
#: holding its refusal). Declared rather than derived from the name, so a
#: rename cannot silently disconnect a value from its reason.
RAW_FIELD: Dict[str, Tuple[str, str]] = {
    "ebitda_benchmark": ("margin", "margin"),
    "ebitda_scale": ("ebitda", "ebitda"),
    "ebitda_efficiency": ("margin", "margin"),
    "ebitda_growth": ("growth", "growth"),
    "ebitda_acceleration": ("acceleration", "acceleration"),
    "real_revenue_growth": ("real_revenue_growth", "real_revenue_growth"),
    "interest_coverage": ("coverage", "coverage"),
    "fcf_conversion": ("fcf_conversion", "fcf_conversion"),
    "net_debt_ebitda": ("net_debt_ebitda", "net_debt_ebitda"),
    "debt_market_cap": ("debt_market_cap", "debt_market_cap"),
    "ev_ebitda_supplement": ("ev_ebitda", "ev_ebitda_supplement"),
    "pe_absolute": ("pe", "pe_absolute"),
    "pe_relative": ("pe", "pe_relative"),
}

#: factor key -> the primitives it consumed, for `sources_json`. COMPACT on
#: purpose: this string is written on every row and its bytes land in the
#: budget the pilot exists to measure.
SOURCE_CONCEPTS: Dict[str, Tuple[str, ...]] = {
    "ebitda_scale": ("oi", "da"),
    "ebitda_benchmark": ("oi", "da", "rev@P"),
    "ebitda_efficiency": ("oi", "da", "rev@P"),
    "ebitda_growth": ("oi", "da"),
    "ebitda_acceleration": ("oi", "da"),
    "real_revenue_growth": ("rev", "cpi"),
    "interest_coverage": ("oi", "int@P"),
    "fcf_conversion": ("oi", "da", "ocf@P", "capex@P"),
    "net_debt_ebitda": ("oi", "da", "debt@P", "cash@P"),
    "debt_market_cap": ("debt@P", "px", "sh"),
    "ev_ebitda_supplement": ("oi", "da", "debt@P", "cash@P", "px", "sh"),
    "pe_absolute": ("px", "eps_ttm"),
    "pe_relative": ("px", "eps_ttm"),
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
        pairs: Dict[int, Tuple[float, float]] = {}
        n_floor_state = 0
        for member in eligible.members:
            prim = self.primitives.get(int(member))
            if prim is None:
                continue
            if plan.cohort_use == COHORT_BENCHMARK:
                # (EBITDA, margin) PAIRS: the band is cut by EBITDA dollars,
                # the bar and the scale are read off margins (D1, R7, R8).
                if _finite(prim.ebitda) and _finite(prim.margin):
                    pairs[int(member)] = (float(prim.ebitda), float(prim.margin))
                    values[int(member)] = float(prim.margin)
                continue
            if factor_key == "interest_coverage" and prim.coverage_floor_state:
                n_floor_state += 1          # R4: out of the rank population
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
            "n_floor_state": n_floor_state,
        }
        # R14: ONE frozen per-factor floor on VALUED members, refused once.
        floor = plan.peer_floor
        if (record["availability"] == pit_store.AVAIL_COMPLETE
                and floor is not None and len(values) < int(floor)):
            record["availability"] = pit_store.AVAIL_UNAVAILABLE
            record["reason"] = R_COHORT_VALUES_BELOW_FLOOR
        if plan.cohort_use == COHORT_BENCHMARK:
            record["pairs"] = pairs
            if record["availability"] == pit_store.AVAIL_COMPLETE:
                # ONE cohort-constant M_s per peer set (R7), computed once.
                m_s, n_band, n_vector, why = benchmark_anchor(pairs)
                record.update({"benchmark": m_s, "n_band": n_band,
                               "n_vector": n_vector, "benchmark_reason": why})
                if why:
                    record["availability"] = pit_store.AVAIL_UNAVAILABLE
                    record["reason"] = why
        self._cache[cache_key] = record
        return record


def benchmark_anchor(pairs: Mapping[int, Tuple[float, float]]
                     ) -> Tuple[Optional[float], int, int, Optional[str]]:
    """(M_s, n_band, n_vector, refusal) from (EBITDA, margin) pairs, target INCLUDED.

    pit_normalization.benchmark_margin_50_75 cuts the 50-75 band by EBITDA
    dollars and averages the band's margins; the band floor is
    BENCHMARK_MIN_COHORT_V1 = 3 and it is NEVER widened (R9). A band of one
    or two, or an empty band, is benchmark_band_too_thin -- the VECTOR floor
    is PEER_FLOOR_V1's and is applied by the cohort cache before this runs.
    """
    items = list(pairs.values())
    if not items:
        return None, 0, 0, R_BENCHMARK_BAND_TOO_THIN
    m_s, n_band, _note = pit_normalization.benchmark_margin_50_75(
        [e for e, _ in items], [m for _, m in items])
    if m_s is None or n_band < int(pit_normalization.BENCHMARK_MIN_COHORT_V1):
        return None, int(n_band), len(items), R_BENCHMARK_BAND_TOO_THIN
    return float(m_s), int(n_band), len(items), None


# ==========================================================================
# (5) NORMALISATION -- the transform the FACTOR declared, not the caller
# ==========================================================================

_REGISTRY = pit_normalization.candidate_v2_registry()


def normalise(factor_key: str, value: Any, cohort_values: Mapping[int, float],
              entity_id: int, as_of: str, *,
              benchmark: Optional[float] = None) -> pit_normalization.NormResult:
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
    if plan.scorer != "registry":
        return _valuation_result(factor_key, value, cohort_values)
    spec = _REGISTRY[plan.norm_key]
    if plan.cohort_use == COHORT_RANK:
        peers = [v for e, v in cohort_values.items() if int(e) != int(entity_id)]
        return pit_normalization.normalize(spec, value, peer_values=peers,
                                           as_of_date=as_of)
    if plan.cohort_use == COHORT_DISPERSION:
        return pit_normalization.normalize(
            spec, value, cohort_values=list(cohort_values.values()),
            as_of_date=as_of)
    if plan.cohort_use == COHORT_BENCHMARK:
        # The bar is M_s (one per peer set); the scale is the IQR of the WHOLE
        # eligible margin vector, target included (R8).
        return pit_normalization.normalize(
            spec, value, benchmark=benchmark,
            cohort_values=list(cohort_values.values()), as_of_date=as_of)
    return pit_normalization.normalize(spec, value, as_of_date=as_of)


def _scorer(name: str) -> Any:
    """Resolve a FactorPlan.scorer name to the frozen pit_valuation_spec callable."""
    module, _, attr = name.rpartition(".")
    if module != "pit_valuation_spec" or not attr:
        raise ReplayRefused("a V scorer must live in pit_valuation_spec: %r" % (name,))
    fn = getattr(pit_valuation_spec, attr, None)
    if not callable(fn):
        raise ReplayRefused("pit_valuation_spec has no scorer %r" % (attr,))
    return fn


def _valuation_result(factor_key: str, value: Any,
                      cohort_values: Mapping[int, float]
                      ) -> pit_normalization.NormResult:
    """A V leg through its FROZEN pit_valuation_spec scorer, as a NormResult.

    R13: the engine holds no valuation curve. pe_absolute is the convex
    transform at the fixed anchor; pe_relative the same transform around the
    cohort median (R17); the supplement the benchmark-anchored tanh around
    the cohort median with the cohort IQR as scale (R15). The population for
    a median INCLUDES the target (it is in its own stored peer set).
    """
    plan = FACTOR_PLAN[factor_key]
    fn = _scorer(plan.scorer)
    population = list(cohort_values.values())
    if factor_key == "pe_absolute":
        detail = fn(value, pit_valuation_spec.PE_ABSOLUTE_ANCHOR)
        kind = pit_valuation_spec.TRANSFORM_VERSION
        anchor_source = pit_valuation_spec.ANCHOR_FIXED_MARKET_PE
        n_cohort = None
    elif factor_key == "pe_relative":
        detail = fn(value, population)
        kind = pit_valuation_spec.TRANSFORM_VERSION
        anchor_source = pit_valuation_spec.ANCHOR_COHORT_MEDIAN
        n_cohort = detail.get("n_cohort")
    else:
        detail = fn(value, population)
        kind = pit_valuation_spec.EV_TRANSFORM_VERSION
        anchor_source = pit_valuation_spec.ANCHOR_COHORT_MEDIAN
        n_cohort = detail.get("n_cohort")
    notes = tuple("%s=%r" % (k, detail[k]) for k in ("branch", "u")
                  if detail.get(k) is not None)
    if detail.get("score") is None:
        return pit_normalization.NormResult(
            score=None, normalization_type=kind,
            availability=pit_store.AVAIL_UNAVAILABLE,
            reason=detail.get("reason") or "no_multiple",
            raw_value=(float(value) if _finite(value) else None),
            anchor=detail.get("anchor"), anchor_source=anchor_source,
            scale=detail.get("scale"),
            scale_source=detail.get("scale_source") or pit_normalization.SCALE_NONE,
            n_cohort=n_cohort, feature_key=factor_key, notes=notes)
    return pit_normalization.NormResult(
        score=float(detail["score"]), normalization_type=kind,
        raw_value=float(value), anchor=detail.get("anchor"),
        anchor_source=anchor_source, scale=detail.get("scale"),
        scale_source=detail.get("scale_source") or pit_normalization.SCALE_NONE,
        n_cohort=n_cohort, feature_key=factor_key, notes=notes)


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


def _db_file(conn: sqlite3.Connection) -> Optional[str]:
    """The connection's main database file, or None for an in-memory DB."""
    for row in conn.execute("PRAGMA database_list").fetchall():
        if (row[1] if not isinstance(row, sqlite3.Row) else row["name"]) == "main":
            path = row[2] if not isinstance(row, sqlite3.Row) else row["file"]
            if path:
                return os.path.abspath(path)
    return None


def _db_dir(conn: sqlite3.Connection) -> str:
    """The directory of the connection's main database file."""
    path = _db_file(conn)
    return (os.path.dirname(path) or ".") if path else "."


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
             progress: Optional[Any] = None,
             batch_size: Optional[int] = DEFAULT_BATCH_SIZE,
             free_floor_bytes: Optional[int] = None,
             ttm_table: Optional[Mapping[int, Any]] = None) -> Dict[str, Any]:
    """One complete cross-section: resolve, normalise, assemble, write.

    `ttm_table` is pit_eps_cohort.load_ttm's in-memory EPS ladder, loaded
    ONCE per run by run_pilot and handed down; a caller that passes none gets
    it loaded here, once per date.

    `main_conn_ro` MUST be a read-only handle on the store -- pass
    `open_store()`. `pilot_conn` is a writer on the isolated pilot DB. The two
    are never the same file, and this function opens neither.

    The write side is STREAMED: every `batch_size` targets the accumulated
    feature / pillar / score rows are written, committed and cleared, so the
    rows held in memory are bounded by the batch. `batch_size=None` keeps the
    whole cross-section in memory and writes once (the original behaviour).
    The per-entity arithmetic is identical either way; the equality oracle in
    test_pit_replay proves it row for row. When `free_floor_bytes` is given,
    free space is checked after every committed batch and a breach raises
    `FreeSpaceAbort` carrying a `.detail` dict that says exactly what was
    committed before it -- the batches already written are NOT rolled back.

    Returns per-date statistics: row counts by table, refusal and signature
    counts, elapsed seconds, the checkpoint return value, the availability
    tally per feature key, and the batch count.
    """
    day = str(as_of)[:10]
    t0 = time.monotonic()
    created = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    if batch_size is not None and int(batch_size) <= 0:
        batch_size = None

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

    # ---- the market side: one listing, one price, one market cap per entity
    # (pit_replay/1.2). Cohort members get it too, because a member's multiple
    # must be formed by the same rules as the target's (R16).
    tick("market", n_entities=len(needed))
    listings = resolve_listings(main_conn_ro, day)
    if ttm_table is None:
        ttm_table = pit_eps_cohort.load_ttm(main_conn_ro)
    n_priced = 0
    n_listed = 0
    n_ambiguous = 0
    target_set = set(targets)
    for entity_id in needed:
        prim = primitives[entity_id]
        attach_market(prim, main_conn_ro, day, listings, ttm_table)
        if entity_id not in target_set:
            continue
        if prim.listing_id is not None:
            n_listed += 1
        if prim.price_raw is not None:
            n_priced += 1
        elif prim.listing_reason == R_LISTING_AMBIGUOUS:
            n_ambiguous += 1

    cohort_cache = CohortCache(main_conn_ro, day, primitives)

    # ---- per-entity assembly ---------------------------------------------
    tick("assemble", n_targets=len(targets))
    feature_rows: List[Tuple[Any, ...]] = []
    pillar_rows: List[Tuple[Any, ...]] = []
    score_rows: List[Tuple[Any, ...]] = []

    # ---- the write side, STREAMED ----------------------------------------
    # The three lists above are flushed every `batch_size` targets. What is
    # in memory at any moment is one batch of rows, not the cross-section.
    # The `primitives` dict and the cohort cache above are NOT bounded by
    # this: they are the once-per-date fixed cost, measured separately.
    written: Dict[str, int] = {"pit_feature": 0, "pit_pillar_score": 0,
                               "pit_score": 0}
    n_batches_written = 0
    n_in_batch = 0
    n_targets_written = 0
    pilot_dir = _db_dir(pilot_conn)
    pilot_file = _db_file(pilot_conn)
    wal_path = (pilot_file + "-wal") if pilot_file else None
    wal_peak_observed = 0
    wal_peak_batch = 0
    tables = (("pit_feature", FEATURE_SQL, feature_rows),
              ("pit_pillar_score", PILLAR_SQL, pillar_rows),
              ("pit_score", SCORE_SQL, score_rows))

    def _flush(final: bool) -> None:
        """Guard free space, then write, commit and CLEAR the pending rows."""
        nonlocal n_batches_written, n_in_batch, n_targets_written
        nonlocal wal_peak_observed, wal_peak_batch
        pending = bool(feature_rows or pillar_rows or score_rows)
        if not pending and (not final or n_batches_written > 0):
            return                          # nothing to write, nothing to count
        batch_no = n_batches_written + 1
        # The floor is checked BEFORE the write, so it PREVENTS a write below
        # the floor rather than reporting one. It also runs before any tick:
        # a driver-side guard raising inside the progress callback would
        # otherwise pre-empt this path, and its exception carries no record
        # of what was committed.
        if free_floor_bytes is not None:
            free = shutil.disk_usage(pilot_dir).free
            if free < int(free_floor_bytes):
                exc = FreeSpaceAbort(
                    "free %.3f GiB is below the %.3f GiB floor mid-date on %s "
                    "before batch %d; that batch was NOT written; %d targets "
                    "and rows %s from earlier batches are COMMITTED and stay "
                    "in the pilot DB"
                    % (free / GIB, int(free_floor_bytes) / GIB, day, batch_no,
                       n_targets_written, written))
                exc.detail = {                                  # type: ignore
                    "as_of": day, "stage": "batch_full",
                    "batch_refused": batch_no, "batch_size": batch_size,
                    "n_targets_written": n_targets_written,
                    "n_targets_pending_not_written": n_in_batch,
                    "n_targets": len(targets),
                    "rows_written": dict(written),
                    "free_bytes": free, "floor_bytes": int(free_floor_bytes),
                }
                raise exc
        # The batch is at its memory PEAK here -- assembled, not yet written.
        # An observer (the RSS harness) that wants to see the write side at
        # its largest sees it through this tick, before a row leaves.
        tick("batch_full", batch=batch_no, n_targets=n_in_batch,
             n_features=len(feature_rows), n_pillars=len(pillar_rows),
             n_scores=len(score_rows))
        n_batches_written = batch_no
        if per_table_scopes and meter is not None:
            # MEASUREMENT MODE: one commit per table, inside the meter's
            # scope, because a scope attributes only COMMITTED pages.
            # Exclusivity is verified on the first batch of the date only:
            # it is a property of the code path, not of the batch, and each
            # check costs a count(*) on every exclusivity table twice over.
            for table, sql, rows in tables:
                if rows or final:
                    with meter.table_scope(table,
                                           expect_exclusive=(batch_no == 1)):
                        pilot_conn.executemany(sql, rows)
                        pilot_conn.commit()
                written[table] += len(rows)
        else:
            # PRODUCTION MODE: ONE transaction per batch, so a batch is atomic
            # across the three tables -- a target never has its feature rows
            # committed without its pillar rows.
            for table, sql, rows in tables:
                pilot_conn.executemany(sql, rows)
                written[table] += len(rows)
            pilot_conn.commit()
        for _, _, rows in tables:
            rows.clear()                    # the point of the exercise
        n_targets_written += n_in_batch
        n_in_batch = 0
        wal_now = 0
        if wal_path:
            try:
                wal_now = os.path.getsize(wal_path)
            except OSError:
                wal_now = 0
        if wal_now > wal_peak_observed:
            wal_peak_observed, wal_peak_batch = wal_now, batch_no
        tick("write_batch", batch=batch_no,
             n_targets=n_targets_written,
             n_features=written["pit_feature"],
             n_pillars=written["pit_pillar_score"],
             n_scores=written["pit_score"],
             wal_bytes=wal_now)

    availability_tally: Dict[str, Dict[str, int]] = {
        k: {"complete": 0, "unavailable": 0} for k in REPLAY_FEATURE_KEYS}
    reason_tally: Dict[str, int] = {}
    signature_tally: Dict[str, int] = {}
    block_tally: Dict[str, int] = {b: 0 for b in pit_factor_blocks.BLOCKS}
    block_refusal_tally: Dict[str, int] = {}
    presence_tally: Dict[str, int] = {}
    debt_bound_tally: Dict[str, int] = {}
    n_floor_state = 0
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
        row_reasons: Dict[str, Optional[str]] = {}
        v_norm: Dict[str, pit_normalization.NormResult] = {}
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
                floor_state = (key == "interest_coverage"
                               and prim.coverage_floor_state)
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
                        if key == "interest_coverage" and record.get("n_floor_state"):
                            resolved["nf"] = record["n_floor_state"]
                        if floor_state:
                            resolved["fs"] = R_COVERAGE_FLOOR_STATE
                        if plan.cohort_use == COHORT_BENCHMARK:
                            resolved["nb"] = record.get("n_band")
                        if record["availability"] != pit_store.AVAIL_COMPLETE:
                            reason = record["reason"]
                        elif floor_state:
                            # R5: the FLOOR STATE. Scored at the rank floor
                            # against a real cohort, excluded from its
                            # population; the raw coverage stays on the row.
                            score = -100.0
                            norm_method = pit_normalization.PERCENTILE_RANK
                            resolved["nc"] = record["n_values"] + 1
                            n_floor_state += 1
                        else:
                            result = normalise(key, raw, record["values"],
                                               entity_id, day,
                                               benchmark=record.get("benchmark"))
                            score = result.score
                            norm_method = result.normalization_type
                            reason = result.reason
                            if result.n_cohort is not None:
                                resolved["nc"] = result.n_cohort
                            if result.scale is not None:
                                resolved["k"] = result.scale
                                resolved["ks"] = result.scale_source
                            if result.anchor is not None and plan.cohort_use in (
                                    COHORT_BENCHMARK, COHORT_MEDIAN_ANCHOR):
                                resolved["bm"] = result.anchor
                                resolved["as"] = result.anchor_source
                            if result.notes and plan.scorer != "registry":
                                resolved["tr"] = list(result.notes)
                            if plan.scorer != "registry":
                                v_norm[key] = result
                    else:
                        result = normalise(key, raw, {}, entity_id, day)
                        score = result.score
                        norm_method = result.normalization_type
                        reason = result.reason
                        if result.scale is not None:
                            resolved["k"] = result.scale
                            resolved["ks"] = result.scale_source
                        if result.notes and plan.scorer != "registry":
                            resolved["tr"] = list(result.notes)
                        if plan.scorer != "registry":
                            v_norm[key] = result
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

            if key == "real_revenue_growth":
                source_period, source_tag = prim.revenue_period, prim.revenue_rung
            elif key == "interest_coverage":
                source_period, source_tag = prim.operating_income_period, prim.interest_rung
            elif key in ("pe_absolute", "pe_relative"):
                source_period, source_tag = prim.eps_period_end, prim.eps_concept
            elif key in ("net_debt_ebitda", "debt_market_cap", "ev_ebitda_supplement"):
                source_period, source_tag = prim.ebitda_period, prim.total_debt_rung
            elif key == "fcf_conversion":
                source_period, source_tag = prim.ebitda_period, prim.ocf_rung
            else:
                source_period, source_tag = prim.ebitda_period, None
            if key == "ebitda_growth" and prim.growth_base_sign:
                resolved["bs"] = prim.growth_base_sign
            # -- the provenance the v6 rulings put ON THE ROW -----------------
            if key in ("net_debt_ebitda", "debt_market_cap", "ev_ebitda_supplement") \
                    and prim.total_debt_rung is not None:
                resolved["db"] = prim.total_debt_bound          # R10: the bound
                if key == "net_debt_ebitda" and prim.cash_rung:
                    resolved["cr"] = prim.cash_rung
                if key == "ev_ebitda_supplement":
                    if prim.cash_rung:
                        resolved["cr"] = prim.cash_rung
                    if prim.enterprise_value is not None:
                        resolved["evv"] = prim.enterprise_value
            if key == "fcf_conversion" and prim.capex_rung:
                resolved["cx"] = prim.capex_rung
            price_key = key in ("pe_absolute", "pe_relative", "debt_market_cap",
                                "ev_ebitda_supplement")
            if price_key:
                if prim.price_age_days is not None:
                    resolved["pa"] = prim.price_age_days      # R19: price age
                if prim.price_skipped:
                    resolved["sk"] = [list(s) for s in prim.price_skipped]
                if prim.price_reason and prim.price_raw is None:
                    resolved["pr"] = prim.price_reason
                if key == "pe_absolute" and prim.eps_reason:
                    resolved["er"] = prim.eps_reason
                if key != "pe_absolute" and key != "pe_relative":
                    if prim.market_cap_reason and prim.market_cap is None:
                        resolved["mr"] = prim.market_cap_reason
                    if prim.split_check:
                        resolved["sc"] = prim.split_check
            if key in ("pe_absolute", "pe_relative") and prim.eps_concept:
                resolved["ek"] = prim.eps_concept                 # R21
                resolved["em"] = prim.eps_method
                resolved["pl"] = prim.eps_pe_leg
                if prim.pe_gate_status:
                    resolved["gs"] = prim.pe_gate_status
                if prim.pe_basis_factor is not None:
                    resolved["bf"] = prim.pe_basis_factor
            resolved["scope"] = SAMPLE_SCOPE
            resolved["fd"] = freeze_digest[:12]
            resolved["ev"] = ENGINE_VERSION
            if plan.computable:
                resolved["p"] = source_period

            feature_rows.append((
                entity_id, prim.listing_id if price_key else None, day, key,
                plan.role,
                json.dumps(list(SOURCE_CONCEPTS.get(key, ())),
                           separators=(",", ":")),
                source_tag, LADDER_VERSION, 0,
                _age_days(source_period, day), day, LATENCY_POLICY_VERSION,
                raw, score, norm_method, row_peer_set,
                availability, 1 if score is not None else 0, reason,
                MODEL_VERSION, run_id, created,
                json.dumps(resolved, separators=(",", ":")),
            ))
            row_reasons[key] = reason
            if key == "net_debt_ebitda" and score is not None and prim.total_debt_bound:
                debt_bound_tally[prim.total_debt_bound] = (
                    debt_bound_tally.get(prim.total_debt_bound, 0) + 1)

        # ---- blocks -------------------------------------------------------
        # E, G and Q through block_score under WITHIN_BLOCK_FLOOR_V1 (R6); V
        # through pit_valuation_spec.valuation_pillar -- the presence rule,
        # the dependency and the owner's first-class VALUATION_PEER_SET_
        # INSUFFICIENT reason are that module's, not the engine's.
        block_results: Dict[str, Dict[str, Any]] = {}
        block_scores: Dict[str, Optional[float]] = {}
        block_reasons: Dict[str, str] = {}
        v_results = {
            k: pit_valuation_spec.SubfactorResult(
                key=k, available=normalized[k] is not None,
                raw_score=normalized[k],
                multiple=(prim.pe if k in ("pe_absolute", "pe_relative")
                          else prim.ev_ebitda),
                anchor=(v_norm[k].anchor if k in v_norm else None),
                anchor_source=(pit_valuation_spec.ANCHOR_FIXED_MARKET_PE
                               if k == "pe_absolute"
                               else pit_valuation_spec.ANCHOR_COHORT_MEDIAN),
                cohort_n=(v_norm[k].n_cohort if k in v_norm else None),
                unavailable_reason=(None if normalized[k] is not None
                                    else row_reasons.get(k)),
                cohort_rung=(str(rung or "") if k != "pe_absolute" else ""))
            for k in BLOCK_KEYS[pit_factor_blocks.BLOCK_V]}
        pillar = pit_valuation_spec.valuation_pillar(v_results)
        for block in pit_factor_blocks.BLOCKS:
            if block == pit_factor_blocks.BLOCK_V:
                result = {
                    "block": block,
                    "score": pillar.pillar_score if pillar.available else None,
                    "available": pillar.available,
                    "n_scoring_present": len(pillar.presence.present),
                    "n_scoring_declared": len(pit_valuation_spec.SUBFACTOR_KEYS),
                    "within_block_coverage": pillar.presence.present_weight,
                    "unavailable_reason": (None if pillar.available
                                           else (pillar.reason
                                                 or pit_factor_blocks.V_ABSENT)),
                }
            else:
                result = pit_factor_blocks.block_score(
                    block, {k: normalized[k] for k in BLOCK_KEYS[block]})
            block_results[block] = result
            block_scores[block] = result["score"]
            if result["score"] is not None:
                block_tally[block] += 1
            elif result.get("unavailable_reason"):
                block_reasons[block] = str(result["unavailable_reason"])
                block_refusal_tally[block + ":" + str(result["unavailable_reason"])] = (
                    block_refusal_tally.get(
                        block + ":" + str(result["unavailable_reason"]), 0) + 1)
        presence_tally[pillar.presence.state] = presence_tally.get(
            pillar.presence.state, 0) + 1

        assembled = pit_factor_blocks.assemble(block_scores, reasons=block_reasons)
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
                          else pit_store.AVAIL_UNAVAILABLE),
                    "r": (None if normalized[k] is not None
                          else row_reasons.get(k))}
                for k in keys}
            if block == pit_factor_blocks.BLOCK_V:
                mask = pillar.mask
                subfactors["_presence"] = pillar.presence.state
            else:
                mask = _mask(block, present)
                if result.get("floor") is not None:
                    subfactors["_floor"] = result["floor"]
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
                mask,
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
                "block_reasons": assembled.get("block_reasons") or {},
                "within_block_floor": pit_factor_blocks.WITHIN_BLOCK_FLOOR_VERSION,
                "price_age_days": prim.price_age_days,
                "debt_bound": prim.total_debt_bound,
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
                "within_block_floor": pit_factor_blocks.WITHIN_BLOCK_FLOOR_VERSION,
                "valuation_price_policy": pit_price_basis.VALUATION_PRICE_POLICY_V1["policy_version"],
                "action_gate": pit_price_basis.ACTION_GATE_VERSION,
                "debt_rung_policy": pit_valuation_spec.DEBT_RUNG_POLICY_VERSION,
            }
            score_rows.append((
                entity_id, prim.listing_id, day, MODEL_VERSION, run_id,
                LATENCY_POLICY_VERSION, PEER_SET_VERSION, LADDER_VERSION,
                assembled["company_score"], None, None, None,
                None, rung, (rung if pillar.available else None),
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
                pillar.mask,
                assembled.get("partial_score_marker"),
                pillar.presence.state,
                None,                                     # quality rule UNDECIDED
                pit_valuation_spec.QUALITY_RULE_UNDECIDED,
                json.dumps(pillar.as_dict()["subfactors"], separators=(",", ":")),
                (pit_valuation_spec.EARNINGS_SIGN_CROSS
                 if prim.eps_sign_crossing else None),
            ))

        # ---- STREAMING: flush the write side every `batch_size` targets ---
        n_in_batch += 1
        if batch_size is not None and n_in_batch >= int(batch_size):
            _flush(final=False)

    # ---- write: whatever is still pending (everything, when unbatched) ----
    pending_final = bool(feature_rows or pillar_rows or score_rows)
    tick("write", n_features=written["pit_feature"] + len(feature_rows),
         n_pillars=written["pit_pillar_score"] + len(pillar_rows),
         n_scores=written["pit_score"] + len(score_rows),
         n_batches=(n_batches_written
                    + (1 if (pending_final or n_batches_written == 0) else 0)),
         batch_size=batch_size)
    _flush(final=True)

    # ---- the accounting must close, batched or not ------------------------
    # 13 feature rows and 4 pillar rows per target, at most one score row,
    # and every target written. A miscount here is an engine defect, and an
    # engine defect stops the run rather than leaving a plausible table.
    expected = {
        "pit_feature": len(REPLAY_FEATURE_KEYS) * n_targets_written,
        "pit_pillar_score": len(pit_factor_blocks.BLOCKS) * n_targets_written,
    }
    if (n_targets_written != len(targets)
            or written["pit_feature"] != expected["pit_feature"]
            or written["pit_pillar_score"] != expected["pit_pillar_score"]
            or written["pit_score"] > n_targets_written):
        raise RuntimeError(
            "row accounting did not close on %s: targets %d written %d, rows "
            "%s, expected features %d pillars %d scores <= %d"
            % (day, len(targets), n_targets_written, written,
               expected["pit_feature"], expected["pit_pillar_score"],
               n_targets_written))

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
        "block_refusals": block_refusal_tally,
        "valuation_presence": presence_tally,
        "debt_bounds_scored": debt_bound_tally,
        "n_floor_state": n_floor_state,
        "n_priced": n_priced,
        "n_listed": n_listed,
        "n_listing_ambiguous": n_ambiguous,
        "signatures": signature_tally,
        "availability": availability_tally,
        "reasons": reason_tally,
        "n_eligible_peer_calls": cohort_cache.n_eligible_calls,
        "checkpoint": checkpoint,
        "free_bytes_after": free_after,
        "batch_size": batch_size,
        "n_batches": n_batches_written,
        "n_targets_written": n_targets_written,
        "wal_peak_bytes_observed": wal_peak_observed,
        "wal_peak_batch": wal_peak_batch,
        "wal_peak_note": (
            "largest -wal size seen right after a batch commit; a lower "
            "bound (SQLite's auto-checkpoint can run inside a commit), and "
            "with batching the WAL no longer holds a whole date"),
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
              progress: Optional[Any] = None,
              batch_size: Optional[int] = DEFAULT_BATCH_SIZE) -> Dict[str, Any]:
    """The whole pilot: gate, build, seed, replay each date, report.

    ORDER IS THE CONTRACT. The gate is evaluated while the only open handle is
    read-only; only after it passes does a writable connection exist at all.
    Free space is checked BEFORE every date and after every committed batch
    within a date; a breach stops the run and reports how far it got rather
    than discovering the floor experimentally.

    THE RUN ROW TELLS THE TRUTH. Whatever ends the run -- completion, a
    free-space breach before or inside a date, or any other exception -- the
    `pit_replay_run` row is finalised with a status that says so. The first
    sizing pilot was aborted by a driver-side guard and its row read
    'running' for a day; that cannot happen again from this function.
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
        "batch_size": batch_size,
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
        # The in-memory EPS ladder, ONCE per run (pit_eps_cohort.load_ttm):
        # 147,914 TTM rows interned, walked per entity-date without a read.
        ttm_table = pit_eps_cohort.load_ttm(store_conn)
        report["ttm_entities"] = len(ttm_table)
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
        day: Optional[str] = None

        def _utc_now() -> str:
            return _dt.datetime.now(_dt.timezone.utc).isoformat(
                timespec="seconds")

        try:
            for day in days:
                # ---- FREE-SPACE GUARD, before the date, every date --------
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
                                 freeze_digest=digest, progress=progress,
                                 batch_size=batch_size,
                                 free_floor_bytes=free_floor_bytes,
                                 ttm_table=ttm_table)
                report["dates"].append(stats)
                for table, n in stats["rows"].items():
                    totals[table] = totals.get(table, 0) + n
                entity_dates += stats["entity_dates"]
                if meter is not None:
                    # With per-table scopes on, each scope has already added
                    # its committed row delta to the meter; passing the rows
                    # again here double-counted them in the first pilot
                    # (396,084 reported against 198,042 in the DB).
                    meter.mark(day, entity_dates=stats["entity_dates"],
                               rows=(None if per_table_scopes
                                     else stats["rows"]))
        except FreeSpaceAbort as exc:
            # A MID-DATE breach, raised by run_date after a COMMITTED batch.
            # The batches already written stay; the row says exactly which.
            detail = dict(getattr(exc, "detail", None) or {})
            report["aborted"] = {
                "at_date": day,
                "reason": "FREE_SPACE_FLOOR_MID_DATE",
                "free_bytes": detail.get("free_bytes"),
                "floor_bytes": free_floor_bytes,
                "dates_completed": [d["as_of"] for d in report["dates"]],
                "entity_dates_completed": entity_dates,
                "partial_date": detail,
                "message": str(exc),
            }
        except BaseException as exc:
            # ANY other way out -- a driver-side guard, a crash, Ctrl-C --
            # must not leave the run row claiming it is still running.
            try:
                pilot_conn.rollback()
                pilot_conn.execute(
                    "UPDATE pit_replay_run SET finished_at = ?, status = ?, "
                    "n_entities = ?, invalidated_reason = ?, summary_json = ? "
                    "WHERE run_id = ?",
                    (_utc_now(), "aborted_exception", entity_dates,
                     "%s: %s" % (exc.__class__.__name__, exc),
                     json.dumps({"plan": plan_record(), "totals": totals,
                                 "entity_dates": entity_dates,
                                 "dates_completed": [d["as_of"]
                                                     for d in report["dates"]],
                                 # the per-date statistics survive the
                                 # exception here, not only in the caller's
                                 # discarded report
                                 "dates": report["dates"],
                                 "at_date": day, "batch_size": batch_size,
                                 "exception": {
                                     "type": exc.__class__.__name__,
                                     "message": str(exc)}},
                                separators=(",", ":")),
                     run_id))
                pilot_conn.commit()
            except Exception:                 # never mask the original failure
                pass
            raise

        report["totals"] = totals
        report["entity_dates"] = entity_dates
        aborted = report["aborted"]
        pilot_conn.execute(
            "UPDATE pit_replay_run SET finished_at = ?, status = ?, "
            "n_entities = ?, invalidated_reason = ?, summary_json = ? "
            "WHERE run_id = ?",
            (_utc_now(),
             "aborted_free_space" if aborted else "complete",
             entity_dates,
             ("%s at %s: %s" % (aborted["reason"], aborted.get("at_date"),
                                aborted.get("message") or "free space floor")
              if aborted else None),
             json.dumps({"plan": plan_record(), "totals": totals,
                         "entity_dates": entity_dates,
                         "dates_completed": [d["as_of"]
                                             for d in report["dates"]],
                         "dates": report["dates"],
                         "batch_size": batch_size,
                         "aborted": aborted},
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

def _golden_eval(case: Mapping[str, Any], ladders: Mapping[str, Any]
                 ) -> Tuple[Optional[float], Optional[str], Primitives]:
    """One PRIMITIVE golden case through resolve_primitives on a synthetic
    index. READS NOTHING. D&A is 0.0 at every period, so EBITDA == OI.

    Instants (total_debt components, cash) are planted at qtrs=0 and
    durations (OCF, capex) at qtrs=4 under the EXACT tags the case names,
    because the tag decides the debt rung and the rung decides the gate.
    """
    eid, day, p = 1, "2019-06-28", "2018-12-31"
    oi = {"2018-12-31": 0.0, "2017-12-31": 0.0, "2016-12-31": 0.0}
    da = dict(oi)
    index: Dict[Tuple[int, str, int], Dict[str, float]] = {
        (eid, "OperatingIncomeLoss", 4): oi,
        (eid, "DepreciationDepletionAndAmortization", 4): da}
    if "ebitda" in case:
        for period, value in zip(("2018-12-31", "2017-12-31", "2016-12-31"),
                                 case["ebitda"]):
            oi[period] = float(value)
    if "operating_income" in case:
        oi["2018-12-31"] = float(case["operating_income"])
        if case.get("interest_expense") is not None:
            index[(eid, "InterestExpense", 4)] = {p: float(case["interest_expense"])}
    for tag, value in (case.get("total_debt") or {}).items():
        index[(eid, tag, 0)] = {p: float(value)}
    if case.get("cash") is not None:
        index[(eid, "CashAndCashEquivalentsAtCarryingValue", 0)] = {p: float(case["cash"])}
    if case.get("operating_cash_flow") is not None:
        index[(eid, "NetCashProvidedByUsedInOperatingActivities", 4)] = {
            p: float(case["operating_cash_flow"])}
    if case.get("capex") is not None:
        index[(eid, "PaymentsToAcquirePropertyPlantAndEquipment", 4)] = {
            p: float(case["capex"])}
    prim = resolve_primitives(index, eid, day, ladders, lambda d: None)
    attr, why_key = RAW_FIELD[case["factor"]]
    return getattr(prim, attr), prim.why.get(why_key), prim


def _golden_check(case: Mapping[str, Any], ladders: Mapping[str, Any]
                  ) -> Optional[str]:
    """One FORMULA_GOLDEN_V2 witness, dispatched on its shape. None == agrees."""
    want = case["expect"]
    if case.get("kind") == "benchmark":
        pairs = {i + 1: (float(e), float(m)) for i, (e, m) in enumerate(case["pairs"])}
        m_s, n_band, n_vector, why = benchmark_anchor(pairs)
        if isinstance(want, str):
            if why == want.split(":", 1)[1] and m_s is None:
                return None
            return "%s: engine gives %r / %r, witness says %r" % (
                case["factor"], m_s, why, want)
        ok = (m_s is not None and abs(m_s - float(want["anchor"])) < 1e-9
              and n_band == int(want["n_band"]) and n_vector == int(want["n_vector"]))
        return None if ok else "%s: engine gives %r / band %r / vector %r, witness says %r" % (
            case["factor"], m_s, n_band, n_vector, want)
    got, why, prim = _golden_eval(case, ladders)
    if isinstance(want, str):
        ok = got is None and why == want.split(":", 1)[1]
    else:
        ok = got is not None and abs(got - float(want)) < 1e-9
    if ok and "expect_floor_state" in case:
        ok = bool(prim.coverage_floor_state) is bool(case["expect_floor_state"])
    if not ok:
        return "%s: engine gives %r / %r (floor_state=%r), witness says %r" % (
            case["factor"], got, why, prim.coverage_floor_state, want)
    return None


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

    # every computable factor has a registered transform under its declared
    # key -- or, for a V leg, a frozen pit_valuation_spec scorer (R13)
    for key, plan in FACTOR_PLAN.items():
        if plan.computable:
            if plan.scorer == "registry" and plan.norm_key not in _REGISTRY:
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

    # THE ENGINE DERIVES FROM THE VERSION THE FREEZE DIGESTS, or it refuses
    if FACTOR_SPEC_VERSION != pit_frozen_spec.ENGINE_MUST_DERIVE_FROM:
        problems.append(
            "engine derives eligibility from %s but the live freeze digests %s"
            % (FACTOR_SPEC_VERSION, pit_frozen_spec.ENGINE_MUST_DERIVE_FROM))
    if set(pit_factor_spec.FORMULAS_V2) != set(REPLAY_FEATURE_KEYS):
        problems.append("FORMULAS_V2 and REPLAY_FEATURE_KEYS name different factors")
    dom = pit_factor_spec.INTEREST_COVERAGE_DOMAIN_V2
    for k, code in (("zero", R_INTEREST_ZERO), ("negative", R_INTEREST_NEGATIVE),
                    ("missing", R_NO_INTEREST_AT_P),
                    ("no_numerator", R_NO_OPERATING_INCOME)):
        if dom[k] != "REFUSED:" + code:
            problems.append("INTEREST_COVERAGE_DOMAIN_V2[%s] and the engine's "
                            "reason code disagree" % k)
    if not dom["non_positive_operating_income"].startswith(
            "FLOOR_STATE:" + R_COVERAGE_FLOOR_STATE):
        problems.append("the coverage floor state's name disagrees with the domain body")
    bp = pit_factor_spec.EBITDA_GROWTH_BASE_POLICY_V2
    if (bp["zero_base"] != "REFUSED:" + R_EBITDA_BASE_ZERO
            or bp["beyond_max_abs_rate"] != "REFUSED:" + R_EBITDA_RATE_BEYOND_BAND):
        problems.append("EBITDA_GROWTH_BASE_POLICY_V2 and the engine's reason "
                        "codes disagree")
    if _rate(1000.0, 1.0)[1] != R_EBITDA_RATE_BEYOND_BAND:
        problems.append("_rate must emit the RENAMED band code (R3)")
    # every REFUSED:<code> a domain body names is an engine reason
    vocabulary = set(ENGINE_REASONS)
    for name in ("INTEREST_COVERAGE_DOMAIN_V2", "LEVERAGE_DOMAIN_V1", "FCF_DOMAIN_V1",
                 "MARKET_CAP_DOMAIN_V1", "EV_EBITDA_DOMAIN_V1", "PE_DOMAIN_V1",
                 "DEBT_RUNG_GATE_V1", "EBITDA_GROWTH_BASE_POLICY_V2"):
        body = getattr(pit_factor_spec, name)
        for value in body.values():
            text = str(value)
            for part in text.replace("|", " ").split():
                if part.startswith("REFUSED:"):
                    code = part.split(":", 1)[1].rstrip(",.;)")
                    if code not in vocabulary:
                        problems.append("%s names REFUSED:%s, which is not an engine reason"
                                        % (name, code))
    # the retired codes are in the vocabulary (rows carry them) and nowhere live
    for code in RETIRED_REASONS[2:]:
        if code not in vocabulary:
            problems.append("retired code %s must stay readable" % code)
    for key, plan in FACTOR_PLAN.items():
        if plan.refusal_reason in RETIRED_REASONS:
            problems.append("%s uses a RETIRED reason" % key)
    # the V legs are scored by pit_valuation_spec, never by the engine (R13)
    for key, plan in FACTOR_PLAN.items():
        if plan.computable and plan.scorer != "registry":
            try:
                _scorer(plan.scorer)
            except ReplayRefused as exc:
                problems.append(str(exc))
            if plan.norm_key is not None:
                problems.append("%s carries both a registry key and a V scorer" % key)
        if plan.computable and plan.scorer == "registry" and plan.norm_key is None:
            problems.append("%s: a registry-scored plan needs a norm_key" % key)
    # PEER_FLOOR_V1 covers every cohort plan and meets every downstream floor
    for key, plan in FACTOR_PLAN.items():
        if not plan.needs_cohort:
            if plan.peer_floor is not None:
                problems.append("%s needs no cohort but carries a peer floor" % key)
            continue
        floor = plan.peer_floor
        if floor is None:
            problems.append("%s needs a cohort and PEER_FLOOR_V1 names no floor" % key)
            continue
        spec_floor = pit_factor_spec.spec(plan.spec_key or key,
                                          FACTOR_SPEC_VERSION).min_peer_count
        need = spec_floor
        if plan.cohort_use == COHORT_RANK:
            need = max(need, pit_normalization.MIN_RANK_PEERS + 1)
        elif plan.cohort_use in (COHORT_DISPERSION, COHORT_BENCHMARK):
            need = max(need, pit_normalization.MIN_DISPERSION_COHORT)
        if plan.cohort_use == COHORT_BENCHMARK:
            need = max(need, pit_normalization.BENCHMARK_MIN_COHORT_V1)
        if floor < need:
            problems.append("PEER_FLOOR_V1[%s] = %d is below the %d its transform needs"
                            % (key, floor, need))
    # the strict debt gate can name every v2 rung
    debt_keys = {r.key for r in pit_policy.ladder_set(LADDER_VERSION)["total_debt"].rungs}
    if debt_keys != set(pit_valuation_spec.DEBT_RUNG_EVEBITDA_V1):
        problems.append("the v2 total_debt ladder and DEBT_RUNG_EVEBITDA_V1 name "
                        "different rungs; the gate could be bypassed")
    # a plan may claim an UNAVAILABLE primitive only when its spec declares one
    for key, plan in FACTOR_PLAN.items():
        if plan.refusal_reason == R_PRIMITIVE_UNAVAILABLE:
            rule = pit_factor_spec.spec(plan.spec_key or key,
                                        FACTOR_SPEC_VERSION).peer_eligibility
            if not rule.unavailable_primitives:
                problems.append("%s claims an unavailable primitive that %s does "
                                "not declare" % (key, FACTOR_SPEC_VERSION))
        if plan.refusal_reason in RETIRED_REASONS:
            problems.append("%s uses a RETIRED reason" % key)
    # registry direction and the frozen direction table must agree for ranks
    for key, plan in FACTOR_PLAN.items():
        if not plan.computable or plan.norm_key not in _REGISTRY:
            continue
        rspec = _REGISTRY[plan.norm_key]
        if rspec.normalization_type != pit_normalization.PERCENTILE_RANK:
            continue
        want = (pit_factor_blocks.HIGHER_IS_BETTER if rspec.higher_is_better
                else pit_factor_blocks.LOWER_IS_BETTER)
        if pit_factor_blocks.FACTOR_DIRECTION_V1.get(plan.spec_key or key) != want:
            problems.append("%s: registry direction and FACTOR_DIRECTION_V1 "
                            "disagree" % key)
    # the production fact index must carry every tag the coverage path reads
    ladders = pit_policy.ladder_set(LADDER_VERSION)
    census = set(pit_coverage.census_tags(LADDER_VERSION))
    for tag in ["OperatingIncomeLoss"] + [r.tags[0] for r in
                                          ladders["interest_expense"].rungs]:
        if tag not in census:
            problems.append("the fact index does not load %s, which coverage "
                            "reads" % tag)
    for concept in ("cash", "total_debt", "operating_cash_flow", "capex"):
        for rung in ladders[concept].rungs:
            for tag in rung.tags:
                if tag not in census:
                    problems.append("the fact index does not load %s, which %s "
                                    "reads at the EBITDA period" % (tag, concept))
    # the golden witnesses, through the engine's own arithmetic
    for case in pit_factor_spec.FORMULA_GOLDEN_V2:
        problem = _golden_check(case, ladders)
        if problem:
            problems.append(problem + " (FORMULA_GOLDEN_V2)")

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
    if abs(reachable - pit_score_signature_scale()) > 1e-12:
        problems.append("under pit_replay/1.2 every block is reachable; the "
                        "reachable weight must be the whole model")
    # the V presence rule and block_score(V) agree on the three-leg cases
    for present in ({"pe_relative": 10.0}, {"ev_ebitda_supplement": 10.0},
                    {"pe_absolute": 10.0, "pe_relative": 20.0}):
        via_blocks = pit_factor_blocks.block_score(pit_factor_blocks.BLOCK_V, present)
        via_pillar = pit_valuation_spec.valuation_pillar({
            k: pit_valuation_spec.SubfactorResult(key=k, available=True, raw_score=v)
            for k, v in present.items()})
        if via_blocks["available"] != via_pillar.available or (
                via_pillar.available
                and abs(via_blocks["score"] - via_pillar.pillar_score) > 1e-9):
            problems.append("block_score(V) and valuation_pillar disagree on %s"
                            % sorted(present))
    return problems


def pit_score_signature_scale() -> float:
    import pit_score_signature
    return float(pit_score_signature.COMPANY_SCALE_V2)


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
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH_SIZE,
                        help="targets per write batch; 0 = the whole "
                             "cross-section in one write (legacy)")
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
        report = run_pilot([args.smoke], args.pilot, entity_limit=args.limit,
                           batch_size=(args.batch or None))
        print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
