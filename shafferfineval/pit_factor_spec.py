"""EVERY SHAFFER FACTOR GETS ITS OWN PEER UNIVERSE.

THE CORRECTION THIS MODULE IMPLEMENTS. `company_scoring.build_ebitda_peer_cohort`
filters peers to those carrying a USABLE EV/EBITDA and only then takes the EBITDA
percentiles. So a FUNDAMENTAL OPERATING BENCHMARK is made conditional on price,
shares, debt and cash -- for about thirty OTHER companies, not for the one being
scored -- and `sector_ebitda_p50` / `sector_ebitda_p75` are the PRICED sector's
percentiles, not the sector's: a size statistic silently selected on price and
liquidity, with nothing on the row saying so.

The owner's rule, stated once and enforced by `validate()`:

    Nothing about price, shares, debt or cash belongs in the EBITDA benchmark's
    eligibility filter. The EBITDA cohort needs EBITDA. Valuation SEPARATELY
    asks which peers have the primitives for EV/EBITDA. Two different cohorts.

`pit_factor_contract.py` states that as prose a person argues with. This module
is the executable half: the eligibility rule is a DATA STRUCTURE with a
primitive list and three boolean flags, `eligible_peers()` evaluates it against
the store, and `validate()` fails if a fundamental cohort's rule names a
price-conditional primitive. A future edit that reintroduces the price filter
does not degrade a number quietly -- it fails a test.

WHAT THE CORRECTION IS WORTH, measured here over all 39,038 peer sets, 165
month-end as-of dates and 3,069,260 memberships (`--measure`, 2026-09-21). The
50-75 band is defined by PERCENTILES OF EBITDA VALUES, so this is measured from
point-in-time values and not from an availability mask:

    the benchmark VECTOR (peers whose EBITDA enters the percentiles)
      price-conditional, as the frozen core builds it   median   1   >=12   5.7%
      EBITDA-only, as the owner's rule requires         median  13   >=12  56.0%

    the benchmark BAND (the 50-75 members whose mean IS the benchmark),
    counted with the vector gate lifted so the two thresholds are separable
      price-conditional                                 median   1   >=3    7.6%
      EBITDA-only                                       median   3   >=3   68.3%

    cohort_mean_ebitda -- vector >= 12 AND band >= 3, which is what the product
    actually needs -- is computable for 56.0% of peer sets under the owner's
    rule and 5.7% under the current one. NEARLY TEN TIMES. 19,632 peer sets
    (50.3%) get a benchmark they do not have today, the median peer set keeps
    8.5% of its EBITDA vector after the price filter, and 29.9% keep NONE.

    The reconstruction of the current rule is cross-checked against the
    independent mask census: the same five-primitive conjunction without the
    EBITDA>0 screen reproduces N_EVEBITDA to 0.01 pp (median 1, >=3 34.19% vs
    34.2%, >=12 7.01% vs 7.0%), so the A/B compares two RULES and not two
    implementations.

AND THE UNIQUE-LEAF RULE IS WORTH 8 POINTS, also measured here. Acceleration
needs THREE consecutive annual EBITDA observations, not four: its two
differences share E_t-1. Over the same 39,038 peer sets, cohorts carrying k
consecutive observations run

    1 observation  (a level)         median 13   >=3 95.2%   >=12 56.0%
    2 observations (growth)          median 12   >=3 94.0%   >=12 54.0%
    3 observations (acceleration)    median 11   >=3 86.0%   >=12 47.4%
    4 observations (THE MISTAKE)     median  9   >=3 78.0%   >=12 39.2%

so charging for the shared observation twice would have thrown away two members
of median cohort size and 8.1 points of formation rate -- by an arithmetic error
in the coverage model, not by anything missing from the store. The two-period
rate is also MEASURED and is not the square of the one-period rate: the square
says 90.6% where the truth is 94.0%, because a filer that reports EBITDA once
mostly reports it every year.

THE UNIQUE-LEAF RULE, enforced rather than asserted:

    Availability(DerivedFeature) = JointAvailability(UNIQUE primitive leaves)

never a product of intermediate feature coverages. `ebitda_acceleration` needs
E_t, E_t-1 and E_t-2 -- THREE observations of two primitives, not four, because
its two differences share the middle one. `pit_derive.leaves()` already
de-duplicates; this module imports it rather than reimplementing it, and
`observation_lags()` reads the answer off the flattened leaf set.

THE SECOND TEST (2026-09-21, `factor_spec_v2`). SUFFICIENT N IS NOT ENOUGH.
Peer selection now has to pass two INDEPENDENT tests -- enough members to
compute a percentile, and members worth comparing -- because measured, those
two move in OPPOSITE directions along the SIC ladder:

    rung     share of peer sets   clears 12 members   median cohort
    sic4                 57.4%                2.4%              23
    office                1.7%               94.5%           908.5

A "peer" at the office rung is any issuer supervised by the same SEC
Corporation Finance office, and those cohorts run 542 to 1,310 members. So the
owner's ruling: THE OFFICE FALLBACK MAY NOT BE A COMPANY VALUATION PEER GROUP.
It may serve broad descriptive statistics; for valuation the honest answer is
VALUATION_PEER_SET_INSUFFICIENT, which makes the score PARTIAL and not
comparable with a full one. A factor declares BOTH thresholds --
`min_peer_count` and `max_admissible_rung` -- and `peer_set_gate` evaluates
both, refusing an office cohort before a single SELECT runs.

COHERENCE IS A NUMBER, not the name of a rung. Four candidate statistics were
measured per rung over all 39,038 peer sets (`pit_peer_coherence.py`), and two
were THROWN OUT by their own size-matched control:

    effective_sic4_count   sic4 1.00  sic3 1.88  sic2 3.37  office 18.01  ADOPTED
    top_sic4_share         sic4 1.00  sic3 0.67  sic2 0.44  office  0.16  ADOPTED
    margin_iqr             sic4 0.35  sic3 0.21  sic2 0.16  office  0.34  REJECTED
    log10_ebitda_span      sic4 2.07  sic3 2.29  sic2 2.25  office  2.47  REJECTED

Both dispersion measures separate the rungs until the MEMBER COUNT IS HELD
FIXED and then stop: among cohorts of 542+ members the office margin IQR is
0.342 against sic2's 1.427 -- the office cohort is TIGHTER, because pooling
twenty industries pulls the quartiles toward the aggregate centre. Composition
survives the control (18.01 against 3.29 at equal size) because a cohort's
industry mix cannot be made homogeneous by adding members. THE COST OF THE
BAN, measured before it was locked: 57,278 of 1,238,663 entity-dates (4.62%)
lose a valuation peer set, 10,156 of them because no narrower rung forms a peer
set at all. Agriculture pays 66.3% of its entity-dates; Finance and Wholesale
pay nothing.

SAMPLE SCOPE. Anything touching `pit_listing` is SURVIVOR_ONLY_DIAGNOSTIC:
2,574 listings, every one alive in 2026, no series ending before 2020. Every
spec carries its scope and every measured table is labelled.

STATUS. `equity_shaffer_v1` and `equity_shaffer_v1_pit` are FROZEN. Nothing here
edits `company_scoring.py`, `sector_scoring.py`, `asset_models.py`,
`prediction.py` or `hedging.py`. The defect is recorded as
SHAFFER_V1_KNOWN_LIMITATION and the specs belong to the candidate lineage only.
The module reads the store `mode=ro` and writes nothing to it.

    python pit_factor_spec.py                         # specs + coverage; PASS/FAIL
    python pit_factor_spec.py --measure --masks DIR   # the A/B against the store
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import os
import sqlite3
import sys
import time
from dataclasses import dataclass, field, replace as _dc_replace
from typing import Any, Iterable, Mapping, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_derive
import pit_normalization
import pit_policy
import pit_store
import statlib

__all__ = [
    "FACTOR_SPEC_VERSION", "FACTOR_SPEC_VERSION_V2", "DEFAULT_SPEC_VERSION",
    "SPECS_V2", "BY_KEY_V2", "SPEC_SETS", "spec_versions", "spec_set",
    "FACTOR_SPEC_VERSION_V3", "SPECS_V3", "BY_KEY_V3", "PRICED_EPS_V2",
    "FACTOR_SPEC_V2_KNOWN_LIMITATION",
    "FACTOR_SPEC_V1_KNOWN_LIMITATION",
    "RUNG_RANK", "RUNG_OFFICE", "VALUATION_FACTORS", "VALUATION_MAX_RUNG",
    "VALUATION_PEER_SET_INSUFFICIENT", "TEST_SUFFICIENT_N",
    "TEST_ECONOMIC_COHERENCE", "GATE_TESTS", "COHERENCE_MEASURES",
    "COHERENCE_MEASURED", "CohortCoherence", "cohort_coherence",
    "PeerSetGate", "peer_set_gate", "is_valuation_factor", "refusal_reason",
    "valuation_pillar_reason",
    "CANDIDATE_MODEL_VERSION",
    "SHAFFER_V1_KNOWN_LIMITATION", "SURVIVOR_ONLY", "FULL_UNIVERSE",
    "MARKET_CONDITIONAL", "CAPITAL_STRUCTURE", "PRICE_CONDITIONAL",
    "FUNDAMENTAL_OPERATING_COHORTS", "RUNG_LADDER", "REQUIRED_FIELDS",
    "MISSING_UNAVAILABLE", "MISSING_DROP_RENORMALISE", "MISSINGNESS_VOCABULARY",
    "PeerEligibility", "FactorSpec", "SPECS", "BY_KEY", "spec",
    "ELIGIBILITY_RULES",
    "EligibleCohort", "eligible_peers", "matched_period_observations",
    "BandCohort", "benchmark_band",
    "ebitda_benchmark_cohort", "price_conditional_cohort_UNCORRECTED",
    "unique_leaves", "leaf_keys", "spec_leaves", "graph_per_member_leaves",
    "observation_lags", "ebitda_observations",
    "leaf_availability", "factor_coverage", "factor_coverage_report",
    "ebitda_benchmark_before_after", "MEASURED_COHORT", "MEASURED_BAND",
    "OBSERVATION_DEPTH", "ANNUAL_SPACING_DAYS",
    "MEASURED_OBSERVATION_JOINT", "MEASURED_OBSERVATION_BASE",
    "validate", "render", "render_gate", "main",
]

#: This module's own policy id. Versioned for the same reason every other
#: policy in the project is: a stored row that says which spec produced it must
#: mean exactly one thing forever. A changed eligibility rule means a v2
#: constant beside this one, never an edit in place.
FACTOR_SPEC_VERSION = "factor_spec_v1"

#: THE TWO-TEST GATE, as its own spec version. v2 is v1 with one thing added --
#: a MAXIMUM ADMISSIBLE RUNG on the valuation factors -- and it is a new
#: constant beside v1 rather than an edit to it for exactly the reason stated
#: above: peer selection is what this module versions, and a rung ceiling
#: changes which companies are compared with which. Every non-valuation spec in
#: v2 is THE SAME OBJECT as in v1 (see SPEC_SETS), so the two sets cannot drift
#: apart by accident and a diff of the two shows exactly the factors the
#: owner's ruling touched.
FACTOR_SPEC_VERSION_V2 = "factor_spec_v2"

#: factor_spec_v3, 2026-09-22 -- two corrections, both OWNER DECISIONS, both
#: applied as a SUCCESSOR set. See `SPECS_V3` for what changed and why.
FACTOR_SPEC_VERSION_V3 = "factor_spec_v3"

#: What a caller that passes no version gets. Deliberately v1: adding v2 must
#: not silently change what an un-updated caller resolves -- the same rule
#: `pit_policy.DEFAULT_LADDER_VERSION` follows.
DEFAULT_SPEC_VERSION = FACTOR_SPEC_VERSION

#: The lineage these specs belong to. `equity_shaffer_v1` is frozen and gets a
#: recorded limitation, not a patch.
CANDIDATE_MODEL_VERSION = pit_normalization.CANDIDATE_MODEL_VERSION
SHAFFER_V1_KNOWN_LIMITATION = "SHAFFER_V1_KNOWN_LIMITATION"

SURVIVOR_ONLY = pit_store.SAMPLE_SURVIVOR_ONLY          # SURVIVOR_ONLY_DIAGNOSTIC
FULL_UNIVERSE = "FULL_REPORTING_UNIVERSE"

#: Where every measured number in this module comes from.
EVIDENCE = (
    "pit_factor_spec.py --measure, run 2026-09-21 against shafferfineval_pit.db "
    "(8.35 GiB, 14,072,934 pit_fact rows): 39,038 peer sets, 3,069,260 "
    "memberships, 165 month-end as-of dates, ladder concept_ladder_v2, duration "
    "facts at annual cadence, unit='USD'. EBITDA VALUES are point-in-time "
    "selected (latest period whose two components are both available and "
    "non-stale, latest vintage known on the date). Price and defensible-share "
    "membership is reused from pit_cohort_price.py's per-date sets, which call "
    "pit_identity.scored_universe_as_of and pit_rawprice.class_decision "
    "directly. pit_cohort_validate.py has already checked the fundamental "
    "selectors against the real ladder walk: 6,000 assertions, zero "
    "disagreements.")


# ==========================================================================
# (1) THE VOCABULARY THE GUARD IS WRITTEN IN
# ==========================================================================

#: Primitives that say what the MARKET thinks a company is worth. A fundamental
#: operating cohort may not name one.
MARKET_CONDITIONAL = frozenset({
    "price_raw", "price_adjusted", "shares_outstanding",
    "market_cap", "enterprise_value", "earnings_per_share_pit",
})

#: Primitives that describe the CAPITAL STRUCTURE. Real fundamentals, and still
#: forbidden in an operating benchmark's filter -- the owner's sentence names
#: all four: "nothing about price, shares, debt or cash".
CAPITAL_STRUCTURE = frozenset({"total_debt", "cash"})

#: The union. `validate()` refuses any of these inside a fundamental cohort's
#: eligibility rule, and that refusal IS the correction.
PRICE_CONDITIONAL = MARKET_CONDITIONAL | CAPITAL_STRUCTURE

#: The factors whose cohort is a FUNDAMENTAL OPERATING one. Membership here is
#: an assertion about economics: these ask what the SECTOR does, so conditioning
#: them on tradeability would answer a different question. Every one of them
#: must be free of PRICE_CONDITIONAL.
FUNDAMENTAL_OPERATING_COHORTS = frozenset({
    "ebitda_benchmark", "ebitda_scale", "ebitda_efficiency",
    "ebitda_growth", "ebitda_acceleration", "real_revenue_growth",
    "roa", "roe", "interest_coverage", "fcf_conversion",
})

#: The SIC fallback ladder every cohort widens along. `pit_peers.RUNG_LADDER` is
#: the authority; restated as plain strings so this module imports no peer code
#: to be read as a specification. `test_pit_factor_spec` asserts they are equal.
RUNG_LADDER: tuple[str, ...] = ("sic4", "sic3", "sic2", "office")

#: Position on that ladder. 0 is narrowest. A "maximum admissible rung" is a
#: rank comparison and not a membership test, so widening the ladder later
#: cannot silently let a factor through a rung it never declared.
RUNG_RANK: dict[str, int] = {rung: i for i, rung in enumerate(RUNG_LADDER)}

#: The widest rung, and the one the owner's ruling is about. A "peer" here is
#: any issuer supervised by the same SEC Corporation Finance office; the
#: cohorts run 542-1,310 members and clear 12 members 94.5% of the time.
RUNG_OFFICE = "office"

#: The factors that ANSWER THE VALUATION QUESTION -- what the market is paying.
#: These are the ones the office rung is forbidden to: the honest answer for a
#: company whose only sufficient cohort is an SEC office is
#: VALUATION_PEER_SET_INSUFFICIENT, not a comparison of Microsoft against
#: hundreds of firms that merely share an administrative reviewer.
#:
#: `debt_market_cap` is deliberately NOT here. It uses a market value and is a
#: DEBT-pillar factor, and the owner's ruling was about the valuation peer
#: group; giving it a ceiling on my own authority would be a modelling decision
#: nobody made. Its ceiling is therefore unchanged and that is a declared
#: choice rather than an oversight.
VALUATION_FACTORS: frozenset[str] = frozenset({"ev_ebitda", "pe_ratio"})

#: The ceiling those factors carry in `factor_spec_v2`: sic2 and no wider.
VALUATION_MAX_RUNG = "sic2"

#: What a factor does when its cohort cannot be formed.
MISSING_UNAVAILABLE = "report_unavailable_with_named_binding_leaf"
MISSING_DROP_RENORMALISE = "drop_factor_and_renormalise_major_weights"
MISSINGNESS_VOCABULARY = (MISSING_UNAVAILABLE, MISSING_DROP_RENORMALISE)

#: The six fields the owner requires every derived factor to declare. Held as
#: data so the "declares all six" test reads the requirement rather than
#: restating it, and so adding a seventh is a one-line change here.
REQUIRED_FIELDS: tuple[str, ...] = (
    "required_primitives", "peer_eligibility", "normalization_type",
    "min_peer_count", "fallback_rung", "missingness_behaviour",
)

#: Why a cohort was refused. Reuses pit_store's vocabulary where one exists --
#: a second spelling of "too few peers" would be a second meaning.
REASON_TOO_FEW = pit_store.REASON_NO_PEERS              # insufficient_peers
REASON_PRIMITIVE_ABSENT = pit_store.REASON_NEVER_TAGGED  # never_tagged
REASON_NO_BAND = "band_too_thin"

#: THE FIRST-CLASS OUTCOME the owner named: a valuation factor had no
#: ADMISSIBLE peer set. Defined in `pit_store` beside `insufficient_peers`
#: because the same string has to be written by this module, read by
#: `pit_score_signature` and stored in `pit_feature.unavailable_reason`, and
#: three spellings of one outcome would be three outcomes.
VALUATION_PEER_SET_INSUFFICIENT = pit_store.REASON_VALUATION_PEER_SET_INSUFFICIENT

#: WHICH of the two independent tests refused. The outcome above says valuation
#: is unavailable; these say why, and they are not interchangeable -- "too few
#: peers to compute a percentile" is a data problem that more coverage fixes,
#: and "the only cohort that is big enough is not a peer group" is not.
TEST_SUFFICIENT_N = "sufficient_n"
TEST_ECONOMIC_COHERENCE = "economic_coherence"
GATE_TESTS: tuple[str, ...] = (TEST_SUFFICIENT_N, TEST_ECONOMIC_COHERENCE)


# ==========================================================================
# (2) THE ELIGIBILITY RULE, AS DATA
# ==========================================================================

@dataclass(frozen=True)
class PeerEligibility:
    """The rule deciding which peers may enter ONE factor's cohort.

    The whole defect being corrected lives in this object. Stated as prose it
    is an opinion; stated as a primitive list plus three flags it is something
    `validate()` can refuse and a test can pin.

      primitives          what each PEER must have for that peer to count. Not
                          what the TARGET company needs -- the two are
                          different, and conflating them is how a benchmark
                          acquires a price filter.
      matched_period      the subset that must resolve at ONE shared
                          (period_end, qtrs). A quarterly operating income plus
                          an annual D&A is not EBITDA.
      lags                the observation lags, in years, at which `primitives`
                          must resolve. (0,) for a level, (0,1) for a growth,
                          (0,1,2) for an acceleration -- THREE observations,
                          which is the unique-leaf rule made visible in the
                          eligibility rule itself.
      requires_price      a resolvable point-in-time price
                          (pit_identity.scored_universe_as_of).
      requires_defensible_shares
                          a share count that survives the strict share-class
                          guard (pit_rawprice.class_decision).
      positive_screen     a value-level screen, e.g. 'ebitda > 0'. Empty for a
                          rule that screens on availability alone.
      value_band          a validity band on a computed ratio, e.g.
                          ('ev_ebitda', 0.1, 300). Price-conditional by
                          construction when the ratio is.
      unavailable_primitives
                          primitives no source in this store supplies. Named on
                          the rule so `eligible_peers` refuses loudly instead
                          of returning an empty cohort that looks like scarcity.
    """

    rule_id: str
    primitives: tuple[str, ...]
    why: str
    matched_period: tuple[str, ...] = ()
    lags: tuple[int, ...] = (0,)
    requires_price: bool = False
    requires_defensible_shares: bool = False
    positive_screen: str = ""
    value_band: Optional[tuple[str, float, float]] = None
    unavailable_primitives: tuple[str, ...] = ()

    @property
    def price_conditional_terms(self) -> tuple[str, ...]:
        """EVERY price-, share-, debt- or cash-dependent thing this rule names.

        The guard's whole surface, in one tuple, so a test asserts emptiness
        rather than enumerating the ways a price could sneak back in. It covers
        the primitive list, both flags and the value band -- a validity band on
        EV/EBITDA is a price condition even though it names no primitive.
        """
        terms = set(self.primitives) & PRICE_CONDITIONAL
        if self.requires_price:
            terms.add("requires_price")
        if self.requires_defensible_shares:
            terms.add("requires_defensible_shares")
        if self.value_band is not None:
            terms.add(f"value_band[{self.value_band[0]}]")
        return tuple(sorted(terms))

    @property
    def is_price_free(self) -> bool:
        return not self.price_conditional_terms

    @property
    def n_observations(self) -> int:
        """Distinct observation dates this rule requires. 3 for acceleration."""
        return len(set(self.lags))

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "primitives": list(self.primitives),
            "matched_period": list(self.matched_period),
            "lags": list(self.lags),
            "requires_price": self.requires_price,
            "requires_defensible_shares": self.requires_defensible_shares,
            "positive_screen": self.positive_screen,
            "value_band": list(self.value_band) if self.value_band else None,
            "unavailable_primitives": list(self.unavailable_primitives),
            "price_conditional_terms": list(self.price_conditional_terms),
            "why": self.why,
        }


# -- the rules themselves --------------------------------------------------
# Shared BY REFERENCE where two factors genuinely use one cohort, exactly as
# pit_policy shares a ladder object between v1 and v2: two specs that must mean
# the same cohort cannot drift apart if they are the same object.

EBITDA_ONLY = PeerEligibility(
    rule_id="ebitda_only_v1",
    primitives=("operating_income", "depreciation_amortisation"),
    matched_period=("operating_income", "depreciation_amortisation"),
    why=("Members of the peer set whose EBITDA resolves: operating_income AND "
         "depreciation_amortisation at a MATCHED (period_end, qtrs), each "
         "non-stale under pit_policy.is_stale, each unit='USD'. NOTHING ELSE. "
         "No price, no share count, no debt, no cash. This is the sector's "
         "distribution of earning power, and conditioning it on tradeability "
         "would make it the PRICED sector's distribution -- which is what the "
         "frozen core does, and what costs it 12 members of median cohort "
         "size."),
)

EBITDA_AND_REVENUE = PeerEligibility(
    rule_id="ebitda_and_revenue_matched_v1",
    primitives=("operating_income", "depreciation_amortisation", "revenue"),
    matched_period=("operating_income", "depreciation_amortisation", "revenue"),
    why=("The EBITDA cohort, further restricted to members whose revenue "
         "resolves at the SAME (period_end, qtrs). A margin is a ratio of one "
         "period's numbers. MEASURED: the matched-period rule costs 0.1 pp "
         "against selecting revenue independently, so it is free -- enforce "
         "it."),
)

EBITDA_TWO_OBSERVATIONS = PeerEligibility(
    rule_id="ebitda_t_and_t1_v1",
    primitives=("operating_income", "depreciation_amortisation"),
    matched_period=("operating_income", "depreciation_amortisation"),
    lags=(0, 1),
    why=("EBITDA at TWO observation dates a year apart. Two unique leaves at "
         "two lags = four observations, and the cohort is the peers that have "
         "all four."),
)

EBITDA_THREE_OBSERVATIONS = PeerEligibility(
    rule_id="ebitda_t_t1_t2_v1",
    primitives=("operating_income", "depreciation_amortisation"),
    matched_period=("operating_income", "depreciation_amortisation"),
    lags=(0, 1, 2),
    why=("EBITDA at THREE observation dates: E_t, E_t-1, E_t-2. Acceleration "
         "is growth(t) minus growth(t-1) and the two growths SHARE E_t-1, so "
         "the requirement is three observations and not four. A coverage model "
         "that multiplied two growth coverages would charge for the shared "
         "middle observation twice."),
)

REVENUE_HISTORY_AND_CPI = PeerEligibility(
    rule_id="revenue_two_periods_plus_cpi_v1",
    primitives=("revenue", "cpi"),
    lags=(0, 1),
    why=("Revenue at two observation dates a year apart, each non-stale, plus "
         "the CPI VINTAGE available on the as-of date -- the deflator is taken "
         "over the growth window, not as trailing CPI at the as-of, because "
         "that error is correlated with the fiscal calendar and mis-scores the "
         "28% of the universe that is not a December filer. CPI is 100% "
         "available on every grid date."),
)

EV_EBITDA_SIX = PeerEligibility(
    rule_id="ev_ebitda_six_primitives_v1",
    primitives=("price_adjusted", "shares_outstanding", "total_debt", "cash",
                "operating_income", "depreciation_amortisation"),
    matched_period=("operating_income", "depreciation_amortisation"),
    requires_price=True,
    requires_defensible_shares=True,
    positive_screen="ebitda > 0 and enterprise_value > 0",
    value_band=("ev_ebitda", 0.1, 300.0),
    why=("A resolvable point-in-time price (pit_identity.scored_universe_as_of: "
         "a listing valid on the date, a bar with volume > 0 within 10 days, no "
         "quarantine), a DEFENSIBLE share count (pit_rawprice.class_decision "
         "under the strict policy), total_debt on concept_ladder_v2, cash, and "
         "EBITDA -- then the [0.1, 300] validity band on the resulting "
         "multiple. Six primitives and two value screens. This rule is CORRECT "
         "for a valuation factor and is exactly what does not belong anywhere "
         "near the EBITDA benchmark."),
)

PRICED_EPS = PeerEligibility(
    rule_id="price_and_pit_eps_v1",
    primitives=("price_adjusted", "earnings_per_share_pit"),
    requires_price=True,
    positive_screen="earnings_per_share_pit > 0",
    unavailable_primitives=("earnings_per_share_pit",),
    why=("A resolvable point-in-time price and a point-in-time EPS. NO SHARE "
         "COUNT: the per-share division is already inside the filed EPS figure, "
         "which is why this chain is two primitives where EV/EBITDA is six. "
         "EPS IS NOT IN THIS STORE -- pit_dera.TAG_FILTER is built from the "
         "concept ladders and no ladder names EarningsPerShareDiluted or "
         "*Basic, so every EPS row in 72 DERA quarters was read and dropped at "
         "ingest. The primitive is named GENUINELY_UNAVAILABLE here rather "
         "than silently producing an empty cohort."),
)

#: factor_spec_v3 -- THE CORRECTION OF A DECLARATION THAT BECAME FALSE.
#:
#: `PRICED_EPS` above says "EPS IS NOT IN THIS STORE". That was true when it
#: was written and it is false now: pit_eps_obs holds 888,486 rows and
#: pit_eps_ttm 147,914 (measured 2026-09-21). The consequence under v2 was not
#: subtle -- eligible_peers('pe_ratio') returned n_eligible=0 in 0.00 seconds
#: WITHOUT TOUCHING THE DATABASE, so the declaration, not the data, was
#: refusing the entire P/E backbone (0.20 of the company score) on every row.
#:
#: The v1/v2 object is preserved VERBATIM. No row was ever produced under it in
#: the main store, and a frozen declaration is corrected by a successor, never
#: by an edit -- otherwise the record of what v2 said would be gone.
#:
#: Owner decision 2026-09-22: "Do not make the P/E eligibility bypass part of
#: the executable model ... fix that declaration properly in v4." This is that
#: fix. Production replay derives eligibility from THIS object under
#: factor_spec_v3; an engine-side bypass is permitted only for diagnostics and
#: only when labelled as such.
PRICED_EPS_V2 = _dc_replace(
    PRICED_EPS,
    rule_id="price_and_pit_eps_v2",
    unavailable_primitives=(),
    why=("A resolvable point-in-time price and a point-in-time EPS. NO SHARE "
         "COUNT: the per-share division is already inside the filed EPS figure, "
         "which is why this chain is two primitives where EV/EBITDA is six. "
         "EPS IS IN THIS STORE as of the pit_eps ingest: pit_eps_obs 888,486 "
         "rows, pit_eps_ttm 147,914, 1,962 of the 7,102 peers on 2019-06-28 "
         "with a TTM knowable on the date. The v1/v2 rule declared it "
         "GENUINELY_UNAVAILABLE and that declaration is now false; it is "
         "corrected here rather than bypassed in an engine."),
)

DEBT_CASH_EBITDA = PeerEligibility(
    rule_id="net_debt_over_ebitda_v1",
    primitives=("total_debt", "cash", "operating_income",
                "depreciation_amortisation"),
    matched_period=("operating_income", "depreciation_amortisation"),
    why=("Leverage against earning power. Debt and cash are REQUIRED here "
         "because they are what the factor measures -- which is the whole "
         "distinction: a primitive belongs in a cohort's filter when the "
         "factor is ABOUT it, never because a neighbouring factor needed it. "
         "Price-free: this leverage ratio needs no market value at all."),
)

DEBT_AND_MARKET_CAP = PeerEligibility(
    rule_id="debt_over_market_cap_v1",
    primitives=("total_debt", "price_adjusted", "shares_outstanding"),
    requires_price=True,
    requires_defensible_shares=True,
    positive_screen="market_cap > 0",
    why=("Leverage against MARKET value, so a price and a defensible share "
         "count are the factor's own subject matter. SURVIVOR_ONLY by "
         "construction."),
)

NET_INCOME_AND_ASSETS = PeerEligibility(
    rule_id="net_income_and_total_assets_v1",
    primitives=("net_income", "total_assets"),
    why=("Return on assets. Both are near-universal in the store (95.1-97.8% "
         "and 100% of base at the three census dates), which is why the "
         "quality legs survive where valuation does not."),
)

NET_INCOME_AND_EQUITY = PeerEligibility(
    rule_id="net_income_and_equity_v1",
    primitives=("net_income", "equity"),
    why=("Return on shareholder capital. StockholdersEquity leads the ladder; "
         "the NCI-inclusive tag is a DIFFERENT denominator and is a fallback."),
)

OPERATING_INCOME_AND_INTEREST = PeerEligibility(
    rule_id="operating_income_and_interest_v1",
    primitives=("operating_income", "interest_expense"),
    why=("Interest cover. The interest_expense ladder is AUTHORED, NOT "
         "MEASURED (pit_policy marks it measured=False), so no coverage claim "
         "may be quoted for this cohort from the ladder -- only the measured "
         "marginal, 63.8/60.7/55.8% of base."),
)

FCF_AND_EBITDA = PeerEligibility(
    rule_id="fcf_over_ebitda_v1",
    primitives=("operating_cash_flow", "capex", "operating_income",
                "depreciation_amortisation"),
    matched_period=("operating_income", "depreciation_amortisation"),
    why=("How much of the accounting earnings turns into spendable cash. "
         "Price-free. capex is the binding leaf of the pair at 67.5/71.1/69.4% "
         "of base against operating cash flow's 94.2/96.1/97.3%."),
)

ELIGIBILITY_RULES: tuple[PeerEligibility, ...] = (
    EBITDA_ONLY, EBITDA_AND_REVENUE, EBITDA_TWO_OBSERVATIONS,
    EBITDA_THREE_OBSERVATIONS, REVENUE_HISTORY_AND_CPI, EV_EBITDA_SIX,
    PRICED_EPS, DEBT_CASH_EBITDA, DEBT_AND_MARKET_CAP, NET_INCOME_AND_ASSETS,
    NET_INCOME_AND_EQUITY, OPERATING_INCOME_AND_INTEREST, FCF_AND_EBITDA,
)


# ==========================================================================
# (3) THE FACTOR SPECIFICATION
# ==========================================================================

@dataclass(frozen=True)
class FactorSpec:
    """Everything a derived factor must declare before it may be computed.

    The six required fields are `REQUIRED_FIELDS`; the rest are provenance.

    `member_quantity` is the `pit_derive` node EACH ELIGIBLE PEER contributes --
    an EBITDA for the size benchmark, an EV/EBITDA for the valuation benchmark.
    It is the node the unique-leaf set is read off, and a spec whose declared
    primitives disagree with that node's de-duplicated leaves is a `validate()`
    failure. That is how a hand-written primitive list stays honest.

    `graph_node` is the cohort statistic itself, kept so the before/after can be
    run against `pit_derive.coverage`. The two differ in exactly the way the
    owner's correction is about: `cohort_mean_ebitda`'s per-member leaf in the
    graph is `ev_ebitda`, not `ebitda`, because the graph is FAITHFUL to the
    frozen core. `validate()` compares the graph's per-member leaf against
    `member_quantity` and demands that any difference be DECLARED in
    `graph_divergence` -- so the contamination is a tested, named fact rather
    than a silent one, and a future graph that stopped diverging would fail the
    stale claim.

    `max_admissible_rung` is the SECOND of the two tests peer selection now has
    to pass. `min_peer_count` asks whether a percentile can be COMPUTED;
    `max_admissible_rung` asks whether the members are worth comparing, and the
    two move in OPPOSITE directions along the SIC ladder -- the office rung
    clears 12 members 94.5% of the time on cohorts of 542-1,310 issuers who
    share nothing but an SEC reviewer. Empty means "no ceiling", which is v1's
    behaviour and is what every non-valuation factor carries; `as_dict` omits
    the field when it is empty, so `factor_spec_v1`'s serialised spec is
    byte-identical to what it was before the gate existed.
    """

    key: str
    question: str
    required_primitives: tuple[str, ...]
    peer_eligibility: PeerEligibility
    normalization_type: str
    min_peer_count: int
    fallback_rung: tuple[str, ...]
    missingness_behaviour: str
    graph_node: str
    member_quantity: str
    sample_scope: str
    min_peer_why: str
    min_band_members: int = 0        # >0 only for a BAND statistic
    graph_divergence: str = ""
    measured: dict[str, Any] = field(default_factory=dict)
    note: str = ""
    max_admissible_rung: str = ""    # "" = no ceiling (v1's behaviour)

    @property
    def admissible_rungs(self) -> tuple[str, ...]:
        """The rungs this factor's cohort may actually be drawn from.

        `fallback_rung` truncated at `max_admissible_rung`. With no ceiling it
        IS `fallback_rung`, so a factor that declares nothing behaves exactly
        as it did before the gate was written.
        """
        if not self.max_admissible_rung:
            return tuple(self.fallback_rung)
        ceiling = RUNG_RANK.get(self.max_admissible_rung)
        if ceiling is None:
            # An unrecognised ceiling FAILS CLOSED. `validate()` reports it as
            # a problem, and until someone fixes it nothing is admissible --
            # the alternative is a typo that silently admits every rung.
            return ()
        return tuple(r for r in self.fallback_rung
                     if RUNG_RANK.get(r, len(RUNG_RANK)) <= ceiling)

    @property
    def forbidden_rungs(self) -> tuple[str, ...]:
        """The rungs this factor may NOT use. The price of the ceiling, named."""
        allowed = set(self.admissible_rungs)
        return tuple(r for r in self.fallback_rung if r not in allowed)

    def rung_admissible(self, rung: str) -> bool:
        """Whether a cohort drawn at `rung` may serve THIS factor.

        A rank comparison against the declared ceiling, never a name test: a
        rung that is not on the ladder at all is inadmissible rather than
        silently accepted.
        """
        if not self.max_admissible_rung:
            return str(rung) in RUNG_RANK
        ceiling = RUNG_RANK.get(self.max_admissible_rung)
        rank = RUNG_RANK.get(str(rung))
        if ceiling is None or rank is None:
            return False                 # fail closed; see `admissible_rungs`
        return rank <= ceiling

    def as_dict(self, version: Optional[str] = None) -> dict[str, Any]:
        """The spec as JSON.

        `max_admissible_rung` and its two derived tuples appear ONLY when the
        spec declares a ceiling, so a v1 spec serialises exactly as it did
        before the two-test gate was written -- the same mechanism
        `pit_policy.Rung.as_dict` uses to keep `concept_ladder_v1` frozen while
        v2 says more. `version` stamps which SET this dict was read out of;
        with none, a spec that declares a ceiling stamps v2 and one that does
        not stamps v1.
        """
        out = {
            "key": self.key, "question": self.question,
            "required_primitives": list(self.required_primitives),
            "peer_eligibility": self.peer_eligibility.as_dict(),
            "normalization_type": self.normalization_type,
            "min_peer_count": self.min_peer_count,
            "min_peer_why": self.min_peer_why,
            "min_band_members": self.min_band_members,
            "fallback_rung": list(self.fallback_rung),
            "missingness_behaviour": self.missingness_behaviour,
            "graph_node": self.graph_node,
            "member_quantity": self.member_quantity,
            "graph_divergence": self.graph_divergence,
            "sample_scope": self.sample_scope,
            "measured": dict(self.measured), "note": self.note,
            "spec_version": version or (FACTOR_SPEC_VERSION_V2
                                        if self.max_admissible_rung
                                        else FACTOR_SPEC_VERSION),
            "model_version": CANDIDATE_MODEL_VERSION,
        }
        if self.max_admissible_rung:
            out["max_admissible_rung"] = self.max_admissible_rung
            out["admissible_rungs"] = list(self.admissible_rungs)
            out["forbidden_rungs"] = list(self.forbidden_rungs)
        return out


_BAND_WHY = ("the benchmark is the MEAN OF THE 50-75 BAND, and the band is a "
             "quarter of the vector by construction, so three members inside "
             "the band needs twelve in the vector. 3 is the floor for the "
             "percentiles themselves (company_scoring.MIN_EBITDA_COHORT).")
_RANK_WHY = ("a percentile rank needs a denominator; 3 is the frozen core's "
             "floor for any cohort statistic.")
_MEAN_WHY = "a cohort MEAN, not a percentile: the core's floor of 3 applies."

SPECS: tuple[FactorSpec, ...] = (
    FactorSpec(
        key="ebitda_benchmark",
        question="What does a right-sized peer in this sector earn?",
        required_primitives=("operating_income", "depreciation_amortisation"),
        peer_eligibility=EBITDA_ONLY,
        normalization_type=pit_normalization.BENCHMARK_ANCHORED,
        min_peer_count=12,
        min_peer_why=_BAND_WHY,
        min_band_members=3,
        fallback_rung=RUNG_LADDER,
        missingness_behaviour=MISSING_UNAVAILABLE,
        graph_node="cohort_mean_ebitda",
        member_quantity="ebitda",
        graph_divergence=(
            "pit_derive's cohort_mean_ebitda takes ev_ebitda PER PEER, not "
            "ebitda -- faithfully, because that is what "
            "company_scoring.build_ebitda_peer_cohort does. THAT IS THE "
            "DEFECT. The spec's member quantity is ebitda and its "
            "eligibility rule is EBITDA_ONLY. " + SHAFFER_V1_KNOWN_LIMITATION),
        sample_scope=FULL_UNIVERSE,
        measured={
            "vector": {"median": 13, "p10": 5, "p25": 8, "p75": 23, "p90": 70,
                       "max": 718, "mean": 36.04, "pct_ge_3": 95.20,
                       "pct_ge_12": 56.00},
            "band_gate_lifted": {"median": 3, "p10": 1, "p25": 2, "p75": 6,
                                 "p90": 18, "max": 179, "mean": 9.18,
                                 "pct_ge_3": 68.34},
            "benchmark_formed_pct": 56.00,
            "under_the_price_conditional_rule": {
                "vector_median": 1, "vector_pct_ge_12": 5.71,
                "band_gate_lifted_median": 1, "band_pct_ge_3": 7.56,
                "benchmark_formed_pct": 5.71},
        },
        note=(SHAFFER_V1_KNOWN_LIMITATION + ": company_scoring."
              "build_ebitda_peer_cohort applies EV_EBITDA_SIX here. Measured, "
              "that turns a 56.0% benchmark-formation rate into 5.7% -- the "
              "benchmark is unavailable for 94% of peer sets because of a "
              "filter that has nothing to do with the quantity it measures."),
    ),
    FactorSpec(
        key="ebitda_scale",
        question="How many median peers would it take to make this company?",
        required_primitives=("operating_income", "depreciation_amortisation"),
        peer_eligibility=EBITDA_ONLY,          # THE SAME OBJECT, by reference
        normalization_type=pit_normalization.PERCENTILE_RANK,
        min_peer_count=3,
        min_peer_why=_RANK_WHY,
        fallback_rung=RUNG_LADDER,
        missingness_behaviour=MISSING_UNAVAILABLE,
        graph_node="ebitda_scale",
        member_quantity="ebitda",
        graph_divergence=(
            "ebitda_scale divides by sector_ebitda_p50, which the graph "
            "builds from sector_ebitda_vector -- the PRICED sector's "
            "percentiles. The same contamination reached through a "
            "percentile instead of a band mean. " + SHAFFER_V1_KNOWN_LIMITATION),
        sample_scope=FULL_UNIVERSE,
        measured={"vector": {"median": 13, "pct_ge_3": 95.2}},
        note=("Shares EBITDA_ONLY with ebitda_benchmark BY REFERENCE, not by "
              "copy: two factors that must mean the same cohort cannot drift "
              "apart if they are the same object. Only the threshold differs "
              "-- a rank needs 3, a band needs 12."),
    ),
    FactorSpec(
        key="ebitda_efficiency",
        question="How profitable is a typical right-sized peer, per dollar of sales?",
        required_primitives=("operating_income", "depreciation_amortisation",
                             "revenue"),
        peer_eligibility=EBITDA_AND_REVENUE,
        normalization_type=pit_normalization.PERCENTILE_RANK,
        min_peer_count=3,
        min_peer_why=_MEAN_WHY,
        fallback_rung=RUNG_LADDER,
        missingness_behaviour=MISSING_UNAVAILABLE,
        graph_node="ebitda_margin_rank",
        member_quantity="ebitda_margin",
        sample_scope=FULL_UNIVERSE,
        measured={"vector": {"median": 12, "p10": 4, "p25": 8, "p75": 22,
                             "p90": 66, "max": 699, "pct_ge_3": 94.5,
                             "pct_ge_12": 52.8},
                  "matched_period_cost_pp": 0.1},
    ),
    FactorSpec(
        key="ebitda_growth",
        question="Is the earnings power larger than it was a year ago?",
        required_primitives=("operating_income", "depreciation_amortisation"),
        peer_eligibility=EBITDA_TWO_OBSERVATIONS,
        normalization_type=pit_normalization.ZERO_ANCHORED,
        min_peer_count=3,
        min_peer_why=_RANK_WHY,
        fallback_rung=RUNG_LADDER,
        missingness_behaviour=MISSING_UNAVAILABLE,
        graph_node="ebitda_growth",
        member_quantity="ebitda_growth",
        sample_scope=FULL_UNIVERSE,
        measured={"vector": {"median": 12, "p25": 8, "p75": 23, "max": 651,
                             "mean": 34.74, "pct_ge_3": 94.01,
                             "pct_ge_12": 54.00},
                  "level_cohort_for_comparison": {"median": 13,
                                                  "pct_ge_3": 95.20},
                  "company_joint_2024": 0.5096,
                  "independence_product_2024": 0.2632,
                  "note": ("MEASURED over two CONSECUTIVE annual observations, "
                           "not assumed to be the square of the one-period "
                           "rate -- the square would have said 90.6% where the "
                           "truth is 94.0%, because a filer that reports "
                           "EBITDA once mostly reports it every year.")},
        note=("ZERO_ANCHORED, not ranked: growth has a true zero and a rank "
              "hands out +100s in a cohort where everyone shrank."),
    ),
    FactorSpec(
        key="ebitda_acceleration",
        question="Is the earnings power growing FASTER than it was?",
        required_primitives=("operating_income", "depreciation_amortisation"),
        peer_eligibility=EBITDA_THREE_OBSERVATIONS,
        normalization_type=pit_normalization.ZERO_ANCHORED,
        min_peer_count=3,
        min_peer_why=_RANK_WHY,
        fallback_rung=RUNG_LADDER,
        missingness_behaviour=MISSING_UNAVAILABLE,
        graph_node="ebitda_acceleration",
        member_quantity="ebitda_acceleration",
        sample_scope=FULL_UNIVERSE,
        measured={"distinct_ebitda_observations": 3,
                  "vector": {"median": 11, "p25": 6, "p75": 20, "max": 568,
                             "mean": 30.27, "pct_ge_3": 86.03,
                             "pct_ge_12": 47.44},
                  "if_the_shared_observation_were_charged_twice": {
                      "median": 9, "pct_ge_3": 77.97, "pct_ge_12": 39.22},
                  "company_joint_2024": 0.4832,
                  "independence_product_2024": 0.1350,
                  "note": ("the second row is the COST OF THE MISTAKE, "
                           "measured: 2 members of median cohort size and 8.1 "
                           "points of formation rate. See OBSERVATION_DEPTH.")},
        note=("THREE EBITDA observations, not four. The eligibility rule says "
              "so in `lags`, pit_derive.leaves() says so in the flattened leaf "
              "set, and observation_lags() reads it off that set."),
    ),
    FactorSpec(
        key="real_revenue_growth",
        question="Did the business sell MORE, or just charge more?",
        required_primitives=("revenue", "cpi"),
        peer_eligibility=REVENUE_HISTORY_AND_CPI,
        normalization_type=pit_normalization.ZERO_ANCHORED,
        min_peer_count=3,
        min_peer_why=_RANK_WHY,
        fallback_rung=RUNG_LADDER,
        missingness_behaviour=MISSING_UNAVAILABLE,
        graph_node="real_revenue_growth",
        member_quantity="real_revenue_growth",
        sample_scope=FULL_UNIVERSE,
        measured={"revenue_vector": {"median": 19, "pct_ge_3": 98.8,
                                     "pct_ge_12": 82.0},
                  "two_period_joint": "UNKNOWN -- one-period availability only"},
    ),
    FactorSpec(
        key="ev_ebitda",
        question="What multiple is the market paying for a right-sized peer's earnings?",
        required_primitives=("price_adjusted", "shares_outstanding", "total_debt",
                             "cash", "operating_income", "depreciation_amortisation"),
        peer_eligibility=EV_EBITDA_SIX,
        normalization_type=pit_normalization.BENCHMARK_ANCHORED,
        min_peer_count=12,
        min_peer_why=_BAND_WHY,
        min_band_members=3,
        fallback_rung=RUNG_LADDER,
        missingness_behaviour=MISSING_DROP_RENORMALISE,
        graph_node="cohort_mean_ev_ebitda",
        member_quantity="ev_ebitda",
        sample_scope=SURVIVOR_ONLY,
        measured={
            "vector": {"median": 1, "p25": 0, "p75": 3, "p90": 8, "max": 95,
                       "mean": 4.0, "pct_ge_3": 34.2, "pct_ge_5": 18.6,
                       "pct_ge_8": 11.5, "pct_ge_12": 7.0},
            "sic4_rung": {"median": 1, "pct_ge_3": 22.9, "pct_ge_12": 2.4},
            "binding_leaf": "shares_outstanding (defensible)",
            "leave_one_out_pct_ge_3": {
                "all_five": 34.2, "without_defensible_shares": 61.6,
                "without_total_debt": 51.2, "without_ebitda": 46.8,
                "without_cash": 35.0},
            "seasonality_pct_ge_3": {"january": 14.5, "april": 40.6,
                                     "october": 19.1},
        },
        note=("Infeasible for most of the cross-section, and the 40% weight "
              "drops out with it -- so the Shaffer equity model IS a "
              "0.25G+0.20P+0.15D model (renormalised 0.4167/0.3333/0.25) for "
              "the majority of company-dates. Known BEFORE the replay."),
    ),
    FactorSpec(
        key="pe_ratio",
        question="How many years of current earnings is the market paying?",
        required_primitives=("price_adjusted", "earnings_per_share_pit"),
        peer_eligibility=PRICED_EPS,
        normalization_type=pit_normalization.PERCENTILE_RANK,
        min_peer_count=3,
        min_peer_why=_MEAN_WHY,
        fallback_rung=RUNG_LADDER,
        missingness_behaviour=MISSING_DROP_RENORMALISE,
        graph_node="pe_ratio",
        member_quantity="pe_ratio",
        graph_divergence=(
            "the graph computes pe_ratio as market_cap / net_income, so its "
            "leaves are price, SHARES and net income -- three. A filed EPS "
            "already carries the per-share division, so the spec needs price "
            "and EPS and NO share count. That is the whole reason P/E is "
            "feasible where EV/EBITDA is not: the binding leaf of the "
            "valuation chain is the defensible share count, and this factor "
            "does not touch it."),
        sample_scope=SURVIVOR_ONLY,
        measured={
            "eps_rows_in_store": 0,
            "proxy_vector": {"median": 9, "pct_ge_3": 92.0, "pct_ge_12": 36.0},
            "proxy_note": ("measured with the net_income ladder standing in "
                           "for EPS -- same filers, same periods, same "
                           "filings. An UPPER bound: P/E is undefined for a "
                           "loss-maker and ~70% of resolvable earnings are "
                           "positive."),
            "recovery_priced_universe": {"requests": 5066, "minutes": 8.4,
                                         "retained_gib": 0.529},
        },
        note=("2.7x EV/EBITDA's 3-peer formation rate, 5.1x at the 12-peer "
              "bar, and FLAT across the calendar (91.6-92.3% every month) "
              "because net income carries the 15-month annual staleness bound "
              "rather than the share count's 4-month one."),
    ),
    FactorSpec(
        key="net_debt_ebitda",
        question="How many years of earnings would clear the debt?",
        required_primitives=("total_debt", "cash", "operating_income",
                             "depreciation_amortisation"),
        peer_eligibility=DEBT_CASH_EBITDA,
        normalization_type=pit_normalization.PERCENTILE_RANK,
        min_peer_count=3,
        min_peer_why=_RANK_WHY,
        fallback_rung=RUNG_LADDER,
        missingness_behaviour=MISSING_DROP_RENORMALISE,
        graph_node="net_debt_ebitda",
        member_quantity="net_debt_ebitda",
        sample_scope=FULL_UNIVERSE,
        measured={"debt_vector": {"median": 11, "pct_ge_3": 95.0,
                                  "pct_ge_12": 45.2},
                  "cash_vector": {"median": 20, "pct_ge_3": 99.9}},
        note=("Lower-is-better, and 94% of resolved total_debt sits on a "
              "concept_ladder_v2 LOWER-BOUND rung -- which FLATTERS the score. "
              "A percentile over that mixture must report the rung "
              "distribution beside it (pit_policy's v2 consumer rule)."),
    ),
    FactorSpec(
        key="debt_market_cap",
        question="How much leverage sits under each dollar of equity value?",
        required_primitives=("total_debt", "price_adjusted", "shares_outstanding"),
        peer_eligibility=DEBT_AND_MARKET_CAP,
        normalization_type=pit_normalization.PERCENTILE_RANK,
        min_peer_count=3,
        min_peer_why=_RANK_WHY,
        fallback_rung=RUNG_LADDER,
        missingness_behaviour=MISSING_DROP_RENORMALISE,
        graph_node="debt_market_cap",
        member_quantity="debt_market_cap",
        sample_scope=SURVIVOR_ONLY,
        measured={"price_shares_vector": {"median": 4, "pct_ge_3": 69.7,
                                          "pct_ge_12": 19.5}},
    ),
    FactorSpec(
        key="roa",
        question="How much profit does each dollar of assets generate?",
        required_primitives=("net_income", "total_assets"),
        peer_eligibility=NET_INCOME_AND_ASSETS,
        normalization_type=pit_normalization.PERCENTILE_RANK,
        min_peer_count=3,
        min_peer_why=_RANK_WHY,
        fallback_rung=RUNG_LADDER,
        missingness_behaviour=MISSING_UNAVAILABLE,
        graph_node="roa",
        member_quantity="roa",
        sample_scope=FULL_UNIVERSE,
        measured={"net_income_marginal": [0.951, 0.966, 0.978],
                  "total_assets_marginal": [1.0, 1.0, 1.0]},
    ),
    FactorSpec(
        key="roe",
        question="How much profit does each dollar of shareholder capital generate?",
        required_primitives=("net_income", "equity"),
        peer_eligibility=NET_INCOME_AND_EQUITY,
        normalization_type=pit_normalization.PERCENTILE_RANK,
        min_peer_count=3,
        min_peer_why=_RANK_WHY,
        fallback_rung=RUNG_LADDER,
        missingness_behaviour=MISSING_UNAVAILABLE,
        graph_node="roe",
        member_quantity="roe",
        sample_scope=FULL_UNIVERSE,
        measured={"equity_marginal": [0.947, 0.963, 0.975]},
    ),
    FactorSpec(
        key="interest_coverage",
        question="How many times over can the business pay its interest bill?",
        required_primitives=("operating_income", "interest_expense"),
        peer_eligibility=OPERATING_INCOME_AND_INTEREST,
        normalization_type=pit_normalization.PERCENTILE_RANK,
        min_peer_count=3,
        min_peer_why=_RANK_WHY,
        fallback_rung=RUNG_LADDER,
        missingness_behaviour=MISSING_UNAVAILABLE,
        graph_node="interest_coverage",
        member_quantity="interest_coverage",
        sample_scope=FULL_UNIVERSE,
        measured={"interest_expense_marginal": [0.638, 0.607, 0.558],
                  "ladder_measured": False},
    ),
    FactorSpec(
        key="fcf_conversion",
        question="How much of the accounting earnings turns into spendable cash?",
        required_primitives=("operating_cash_flow", "capex", "operating_income",
                             "depreciation_amortisation"),
        peer_eligibility=FCF_AND_EBITDA,
        normalization_type=pit_normalization.PERCENTILE_RANK,
        min_peer_count=3,
        min_peer_why=_RANK_WHY,
        fallback_rung=RUNG_LADDER,
        missingness_behaviour=MISSING_UNAVAILABLE,
        graph_node="fcf_conversion",
        member_quantity="fcf_conversion",
        sample_scope=FULL_UNIVERSE,
        measured={"capex_marginal": [0.675, 0.711, 0.694],
                  "ocf_marginal": [0.942, 0.961, 0.973]},
    ),
)

BY_KEY: dict[str, FactorSpec] = {s.key: s for s in SPECS}


# -- factor_spec_v2: THE TWO-TEST GATE -------------------------------------
#
# v1 above is FROZEN and is read, never edited. It declares no rung ceiling,
# which is a STATEMENT ABOUT V1 rather than an omission: under v1 a valuation
# percentile may be taken against an SEC Corporation Finance office, and that
# is the behaviour the owner's ruling corrects. It is recorded here as a known
# limitation of the v1 spec, in the same way the price-conditional EBITDA
# benchmark is recorded as SHAFFER_V1_KNOWN_LIMITATION.
#
# v2 differs in ONE dimension, for TWO factors. Everything else is the same
# object by reference, so `SPECS_V2[i] is SPECS[i]` for every non-valuation
# factor and the two sets cannot drift apart.

#: The v1 behaviour this version corrects, as a named limitation rather than a
#: comment, so a reader who finds an office-rung valuation row in an old replay
#: can look up what produced it.
FACTOR_SPEC_V1_KNOWN_LIMITATION = (
    "FACTOR_SPEC_V1_ALLOWS_OFFICE_RUNG_VALUATION: factor_spec_v1 declares no "
    "maximum admissible rung, so a valuation percentile may be taken against "
    "an SEC Corporation Finance office -- 542 to 1,310 issuers whose only "
    "shared property is their reviewer. factor_spec_v2 forbids it and answers "
    + VALUATION_PEER_SET_INSUFFICIENT + " instead.")

_V2_CEILING_NOTE = (
    "factor_spec_v2: the office rung is FORBIDDEN for this factor. Sufficient "
    "N and economic coherence are two INDEPENDENT tests and they move in "
    "opposite directions along the SIC ladder -- measured, the office rung "
    "clears 12 members 94.5% of the time and does it with cohorts of 542-1,310 "
    "issuers who share an SEC reviewer and nothing else, while sic4 clears 12 "
    "for 2.4% of peer sets. More observations do not help when the economic "
    "relationship has become meaningless, so the honest answer when no "
    "admissible rung forms a cohort is " + VALUATION_PEER_SET_INSUFFICIENT
    + ", which makes the score PARTIAL and not comparable with a full one "
      "(pit_score_signature.PARTIAL_SHAFFER_SCORE). The office rung remains "
      "available for BROAD DESCRIPTIVE STATISTICS; what it may not be is a "
      "company valuation peer group.")

SPECS_V2: tuple[FactorSpec, ...] = tuple(
    _dc_replace(s, max_admissible_rung=VALUATION_MAX_RUNG,
                note=(s.note + " " if s.note else "") + _V2_CEILING_NOTE)
    if s.key in VALUATION_FACTORS else s
    for s in SPECS)

BY_KEY_V2: dict[str, FactorSpec] = {s.key: s for s in SPECS_V2}

# ==========================================================================
# factor_spec_v3 -- OWNER DECISIONS OF 2026-09-22, as a successor set
#
# v3 differs from v2 in TWO factors. Everything else is the same object by
# reference, so the sets cannot drift apart.
#
# (a) ebitda_benchmark: the MARGIN reading, not the DOLLAR reading.
#
#     The v1/v2 spec compares the company's EBITDA IN DOLLARS against the mean
#     EBITDA in dollars of the 50th-75th percentile cohort. That was measured
#     to be rank-identical to raw EBITDA dollars (docs/SHAFFER-EQUITY-V2-
#     CANDIDATE.md, Spearman table at 2015-06-30; "no monotone transform
#     fixes this") -- i.e. it is a SIZE factor, which is precisely what
#     ebitda_scale is, and ebitda_scale was deliberately given ZERO scoring
#     weight for being one. Measured on CTSH 2019-06-28: a $12.4M benchmark
#     against $3.3B of own EBITDA saturates the tanh at the clamp. Under the
#     dollar reading, the largest single factor weight in the model (0.1225)
#     degenerates into a second copy of a zero-weight challenger.
#
#     THE COHORT IS UNCHANGED. Selection still uses the 50th-75th percentile
#     of peer EBITDA DOLLARS -- that is Shaffer's identity and it is retained.
#     What changes is the QUANTITY COMPARED once the cohort is selected:
#
#         EBITDA_i / Revenue_i  -  mean(EBITDA_j / Revenue_j  for j in cohort)
#
#     with the existing BENCHMARK_ANCHORED transform, S = 100 tanh(x / k),
#     k = pit_normalization.DEFAULT_EXCESS_MARGIN_SCALE_K. The registry
#     already declared exactly this (EBITDAExcessLevel, ANCHOR_COHORT_50_75_
#     MEAN_MARGIN, benchmark_margin_50_75); the spec body never caught up.
#
#     Cost: revenue joins required_primitives and eligibility becomes
#     EBITDA_AND_REVENUE. The design document measured that as a further
#     13.2% coverage hole on top of EBITDA's own. Accepted with eyes open.
#
# (b) pe_ratio: eligibility is PRICED_EPS_V2, which no longer declares EPS
#     unavailable. See PRICED_EPS_V2.
# ==========================================================================

FACTOR_SPEC_V2_KNOWN_LIMITATION = (
    "FACTOR_SPEC_V2_DOLLAR_BENCHMARK_AND_STALE_EPS_DECLARATION: factor_spec_v2 "
    "compares EBITDA in DOLLARS against the 50-75 cohort (a size factor the "
    "design record had already rejected) and declares point-in-time EPS "
    "unavailable although pit_eps_obs holds 888,486 rows. factor_spec_v3 "
    "corrects both. No main-store row was ever produced under v2.")

_V3_BENCHMARK_NOTE = (
    "factor_spec_v3: SCORED QUANTITY IS THE EBITDA MARGIN, cohort selection is "
    "unchanged. Select the 50th-75th percentile cohort by peer EBITDA dollars "
    "(member_quantity='ebitda', as before); then compare EBITDA_i/Revenue_i "
    "against the cohort's mean margin, BENCHMARK_ANCHORED. Owner decision "
    "2026-09-22. The dollar reading is FACTOR_SPEC_V2_KNOWN_LIMITATION.")

_V3_PE_NOTE = (
    "factor_spec_v3: eligibility PRICED_EPS_V2 -- EPS is in the store and is "
    "no longer declared GENUINELY_UNAVAILABLE.")


def _to_v3(s: FactorSpec) -> FactorSpec:
    if s.key == "ebitda_benchmark":
        return _dc_replace(
            s,
            required_primitives=("operating_income", "depreciation_amortisation",
                                 "revenue"),
            peer_eligibility=EBITDA_AND_REVENUE,
            graph_divergence=(
                "SCORED QUANTITY DIFFERS FROM pit_derive's cohort_mean_ebitda "
                "node: the graph node is a DOLLAR mean; this spec scores the "
                "MARGIN excess against the cohort's mean margin "
                "(pit_normalization.benchmark_margin_50_75). The cohort "
                "selection quantity is still EBITDA dollars. "
                + FACTOR_SPEC_V2_KNOWN_LIMITATION),
            note=(s.note + " " if s.note else "") + _V3_BENCHMARK_NOTE)
    if s.key == "pe_ratio":
        return _dc_replace(
            s,
            peer_eligibility=PRICED_EPS_V2,
            note=(s.note + " " if s.note else "") + _V3_PE_NOTE)
    return s


#: Which keys each successor version LEGITIMATELY replaces. Everything not
#: listed must be the v1 object by reference, and validate() enforces that.
REPLACED_BY_VERSION: dict[str, frozenset] = {
    FACTOR_SPEC_VERSION_V3: frozenset({"ebitda_benchmark", "pe_ratio"}),
}

SPECS_V3: tuple[FactorSpec, ...] = tuple(_to_v3(s) for s in SPECS_V2)

BY_KEY_V3: dict[str, FactorSpec] = {s.key: s for s in SPECS_V3}

#: Every spec set that has ever been named, keyed by the id a row would carry.
#: v1 and v2 are FROZEN: they are read, never edited.
SPEC_SETS: dict[str, dict[str, FactorSpec]] = {
    FACTOR_SPEC_VERSION: BY_KEY,
    FACTOR_SPEC_VERSION_V2: BY_KEY_V2,
    FACTOR_SPEC_VERSION_V3: BY_KEY_V3,
}


def spec_versions() -> tuple[str, ...]:
    """Every selectable spec version, oldest first."""
    return (FACTOR_SPEC_VERSION, FACTOR_SPEC_VERSION_V2, FACTOR_SPEC_VERSION_V3)


def spec_set(version: Optional[str] = None) -> dict[str, FactorSpec]:
    """The specs for one version. Raises on an unknown version id.

    Resolved through `SPEC_SETS` rather than through a module global so that a
    test which monkey-patches one registry patches exactly one version.
    """
    name = version or DEFAULT_SPEC_VERSION
    try:
        return SPEC_SETS[name]
    except KeyError:
        raise ValueError(f"unknown factor spec version {name!r}; known: "
                         f"{', '.join(spec_versions())}") from None


def spec(factor_key: str, version: Optional[str] = None) -> FactorSpec:
    """One factor's spec. Raises on an unknown key, loudly.

    `version` DEFAULTS TO v1, so every caller written before the two-test gate
    existed resolves exactly what it always resolved.
    """
    registry = spec_set(version)
    try:
        return registry[factor_key]
    except KeyError:
        raise ValueError(f"unknown factor {factor_key!r}; known: "
                         f"{', '.join(sorted(registry))}") from None


# ==========================================================================
# (4) THE UNIQUE-LEAF RULE -- imported from pit_derive, never reimplemented
# ==========================================================================

def unique_leaves(factor_key: str) -> tuple[pit_derive.LeafRef, ...]:
    """The de-duplicated primitive leaves ONE PEER contributes to this factor.

    Delegates to `pit_derive.leaves` on the spec's `member_quantity`, and that
    function already de-duplicates by (key, lag, per_member, owner). The
    de-duplication IS the rule:

        Availability(DerivedFeature) = JointAvailability(UNIQUE primitive leaves)

    Reimplementing it here would create a second answer to the one question the
    owner's correction turns on, so this function is one line on purpose.

    `member_quantity` and not `graph_node`: a cohort statistic's graph node
    flattens to a PER-MEMBER edge (`ev_ebitda[>= 12 peers]`), which is a count
    threshold and not a conjunction -- and for the EBITDA benchmark it is the
    contaminated edge itself.
    """
    return pit_derive.leaves(spec(factor_key).member_quantity)


def leaf_keys(factor_key: str) -> tuple[str, ...]:
    """The distinct PRIMITIVE keys one peer needs, lags collapsed.

    What `required_primitives` must equal: a spec that listed `ebitda` or
    `market_cap` here would be naming an intermediate and double-charging its
    leaves.
    """
    out: list[str] = []
    for ref in unique_leaves(factor_key):
        if ref.key not in out:
            out.append(ref.key)
    return tuple(out)


def spec_leaves(factor_key: str) -> tuple[tuple[str, int], ...]:
    """The (primitive, lag) pairs the SPEC declares, de-duplicated.

    The spec's own statement of the unique-leaf set: `required_primitives`
    crossed with the eligibility rule's observation `lags`. `validate()` checks
    it against `unique_leaves`, so a declaration and the derivation graph
    cannot disagree without a test failing.
    """
    item = spec(factor_key)
    out: list[tuple[str, int]] = []
    for lag in item.peer_eligibility.lags:
        for primitive in item.required_primitives:
            pair = (primitive, int(lag))
            if pair not in out:
                out.append(pair)
    return tuple(out)


def graph_per_member_leaves(graph_node: str) -> tuple[str, ...]:
    """The quantities `pit_derive`'s node consumes ONCE PER COHORT MEMBER.

    Empty for a company-level node. `sic` is excluded: it is what BUILDS the
    peer set (`peer_set` owns that edge), not something a factor asks of a
    member. What is left is the factor's real per-member requirement -- and for
    `cohort_mean_ebitda` it comes back as `('ev_ebitda',)`, which is the defect,
    stated by the graph itself rather than by a comment.
    """
    return tuple(dict.fromkeys(
        ref.key for ref in pit_derive.leaves(graph_node)
        if ref.per_member and ref.owner != "peer_set"))


def observation_lags(factor_key: str, primitive: str = "") -> tuple[int, ...]:
    """The distinct lags at which a primitive is required, ascending.

    This is where "three observations, not four" is READ rather than asserted:
    `observation_lags('ebitda_acceleration', 'operating_income')` returns
    (0, 1, 2) because `pit_derive.leaves` collapsed the E_t-1 that the two
    growth terms share. A model built as growth_t x growth_t-1 would charge for
    that shared middle observation twice.
    """
    lags = {ref.lag for ref in unique_leaves(factor_key)
            if not ref.per_member and (not primitive or ref.key == primitive)}
    return tuple(sorted(lags))


def ebitda_observations(factor_key: str) -> int:
    """How many distinct EBITDA OBSERVATIONS this factor needs.

    3 for acceleration, 2 for growth, 1 for a level, 0 for a factor with no
    EBITDA in it. Counted over the lags at which BOTH assembly components are
    required, because one component on its own is not an EBITDA observation.
    """
    components = ("operating_income", "depreciation_amortisation")
    lags = [set(observation_lags(factor_key, c)) for c in components]
    return len(lags[0] & lags[1])


def leaf_availability(factor_key: str,
                      marginals: Mapping[str, float]) -> dict[str, Any]:
    """Joint availability over the DE-DUPLICATED leaf set of ONE PEER.

    Never a product of intermediate feature coverages: the leaf set comes from
    `spec_leaves`, which `validate()` pins to `pit_derive.leaves` -- so EBITDA
    acceleration is priced over six leaves at THREE observation dates, and not
    over two growth coverages multiplied together.

    Lagged leaves inherit the unlagged marginal and are FLAGGED as an
    assumption, because a company that filed last year and not this one is a
    different population from one that filed both. Raises rather than guesses
    when a primitive has never been surveyed.
    """
    rows: list[dict[str, Any]] = []
    for key, lag in spec_leaves(factor_key):
        exact = f"{key}@t-{lag}y" if lag else key
        if exact in marginals:
            value, basis = float(marginals[exact]), "measured"
        elif key in marginals:
            value, basis = float(marginals[key]), "lag_persistence"
        else:
            raise ValueError(
                f"{factor_key}: no availability supplied for {exact!r}. It has "
                "not been surveyed; supply a measured figure or accept that "
                "this factor's coverage is UNKNOWN -- do not guess one.")
        rows.append({"leaf": exact, "key": key, "lag": lag,
                     "availability": value, "basis": basis})
    points = [r["availability"] for r in rows]
    low, high = pit_derive.frechet_bounds(points)
    ranked = sorted(rows, key=lambda r: r["availability"])
    return {
        "factor": factor_key,
        "leaves": rows,
        "n_leaves": len(rows),
        "n_unique_primitives": len({r["key"] for r in rows}),
        "n_observations": len({r["lag"] for r in rows}),
        "joint_independent": pit_derive.independence_joint(points),
        "frechet_lower": low, "frechet_upper": high,
        "binding_leaf": ranked[0]["leaf"] if ranked else None,
        "binding_availability": ranked[0]["availability"] if ranked else None,
    }


# ==========================================================================
# (5) THE FACTOR-SPECIFIC PEER UNIVERSE
# ==========================================================================

#: The ladder version every selector here resolves under. v2, because v1's
#: total_debt ladder hides its best-covered tag inside a composite rung and
#: resolves 2.4x less often (pit_policy section 2b).
LADDER = pit_policy.LADDER_VERSION_V2

#: Duration facts are read at ANNUAL cadence. The Shaffer model's revenue,
#: EBITDA and margin are annual quantities.
ANNUAL_QTRS = 4


@dataclass(frozen=True)
class EligibleCohort:
    """The subset of ONE peer set eligible for ONE factor, with the count.

    `availability` and `reason` exist so a thin cohort is REFUSED with a named
    reason rather than scored against four companies. That is the difference
    between "unavailable" and "unavailable because insufficient_peers: 2 of a
    required 12 members of a 31-member peer set carried EBITDA".
    """

    factor_key: str
    as_of: str
    rule_id: str
    members: tuple[int, ...]
    n_eligible: int
    n_offered: int
    availability: str
    reason: Optional[str]
    sample_scope: str
    note: str = ""
    rung: str = ""
    gate: Optional["PeerSetGate"] = None

    @property
    def ok(self) -> bool:
        return self.availability == pit_store.AVAIL_COMPLETE

    def as_dict(self) -> dict[str, Any]:
        out = {"factor_key": self.factor_key, "as_of": self.as_of,
               "rule_id": self.rule_id, "n_eligible": self.n_eligible,
               "n_offered": self.n_offered, "availability": self.availability,
               "reason": self.reason, "sample_scope": self.sample_scope,
               "note": self.note, "members": list(self.members)}
        if self.rung:
            out["rung"] = self.rung
        if self.gate is not None:
            out["gate"] = self.gate.as_dict()
        return out


def _ladder_unit_and_qtrs(concept: str) -> tuple[str, int]:
    """The unit and period length a concept's facts are filed under.

    Read off the ladder rather than hard-coded: `pit_policy` is the authority
    on whether a concept is an instant or a duration, and a table here would be
    a second one. unit='USD' is load-bearing -- entity 2544 is a JPY-reporting
    20-F filer with a complete D&A history, and a JPY EBITDA in a USD sector
    percentile is a 150x error with clean provenance.
    """
    ladder = pit_policy.ladder_for(concept, LADDER)
    qtrs = 0 if ladder.period_kind == pit_policy.PERIOD_INSTANT else ANNUAL_QTRS
    return ladder.unit, qtrs


def concept_available(conn: sqlite3.Connection, entity_id: int, as_of: str,
                      concept: str) -> bool:
    """Does `concept` resolve for this entity on this date, under the ladder?

    `pit_policy.resolve` walks the rungs in order with the date gates applied,
    `pit_store.latest_period_as_of` does the point-in-time selection, and
    `pit_policy.is_stale` retires a dead company's last filing. A composite
    (`combine == 'sum'`) rung needs EVERY component at a MATCHED period end.

    The same chain as `pit_cohort_validate.real_concept_available`, generalised
    to any ladder concept instead of that module's five. The test asserts the
    two agree on the five it covers, which is how this generalisation earns the
    6,000 assertions that were already run against it.
    """
    unit, qtrs = _ladder_unit_and_qtrs(concept)
    for rung in pit_policy.resolve(concept, as_of, LADDER):
        if rung.combine == pit_policy.COMBINE_SUM:
            rows = [pit_store.latest_period_as_of(conn, entity_id, tag, unit,
                                                  qtrs, as_of)
                    for tag in rung.tags]
            if any(r is None for r in rows):
                continue
            if len({r["period_end"] for r in rows}) != 1:
                continue
            row = rows[0]
        else:
            row = pit_store.latest_period_as_of(conn, entity_id, rung.tag, unit,
                                                qtrs, as_of)
            if row is None:
                continue
        if pit_policy.is_stale(row["period_end"], as_of, int(row["qtrs"]), concept):
            continue
        return True
    return False


def matched_period_available(conn: sqlite3.Connection, entity_id: int,
                             as_of: str, concepts: Sequence[str]) -> bool:
    """Do ALL of `concepts` resolve at ONE shared (period_end, qtrs)?

    For the two EBITDA components this delegates to
    `pit_cohort_validate.real_ebitda_matched`, which is the validated
    implementation of `pit_policy.ebitda_assembly_spec()["period_rule"]` and
    has already been checked against the vectorised masks over 165 dates. Any
    other concept set is walked the same way, period by period.
    """
    wanted = tuple(concepts)
    if set(wanted) == {"operating_income", "depreciation_amortisation"}:
        import pit_cohort_validate                      # lazy: touches nothing
        return pit_cohort_validate.real_ebitda_matched(conn, entity_id, as_of)

    tag_sets = []
    for concept in wanted:
        unit, qtrs = _ladder_unit_and_qtrs(concept)
        tags = [t for rung in pit_policy.resolve(concept, as_of, LADDER)
                for t in rung.tags]
        tag_sets.append((concept, unit, qtrs, tags))
    head_concept, head_unit, head_qtrs, head_tags = tag_sets[0]
    marks = ", ".join("?" * len(head_tags))
    periods = conn.execute(
        f"""SELECT DISTINCT period_end FROM pit_fact
             WHERE entity_id = ? AND qtrs = ? AND segments = '' AND coreg = ''
               AND unit = ? AND available_date <= ? AND tag IN ({marks})""",
        [entity_id, head_qtrs, head_unit, as_of] + head_tags).fetchall()
    for row in periods:
        period_end = row["period_end"]
        if pit_policy.is_stale(period_end, as_of, head_qtrs, head_concept):
            continue
        if all(conn.execute(
                f"""SELECT 1 FROM pit_fact
                     WHERE entity_id = ? AND qtrs = ? AND period_end = ?
                       AND segments = '' AND coreg = '' AND unit = ?
                       AND available_date <= ?
                       AND tag IN ({", ".join("?" * len(tags))}) LIMIT 1""",
                [entity_id, qtrs, period_end, unit, as_of] + tags).fetchone()
               for _c, unit, qtrs, tags in tag_sets[1:]):
            return True
    return False


#: How far apart two ANNUAL period ends may be and still count as consecutive
#: observations. A 52/53-week retailer's year ends on a moving Saturday and a
#: filer that changes its fiscal year end shifts by months, so the window is
#: deliberately wider than 365 +/- a few days -- and it is a WINDOW rather than
#: a tolerance because a gap outside it is a MISSING YEAR, and a growth rate
#: computed across a missing year is a two-year growth rate wearing a one-year
#: label.
ANNUAL_SPACING_DAYS = (300, 430)


def matched_period_observations(conn: sqlite3.Connection, entity_id: int,
                                as_of: str, concepts: Sequence[str],
                                n_required: int) -> bool:
    """Are there `n_required` CONSECUTIVE annual observations of `concepts`?

    Every concept must resolve at ONE shared (period_end, qtrs) for each
    observation, every observation must have been available on `as_of`, the
    LATEST observation must be non-stale, and consecutive period ends must sit
    `ANNUAL_SPACING_DAYS` apart.

    The staleness rule applies to the LATEST observation only, and that is a
    decision, not an oversight. `pit_policy.is_stale` asks "is this fact still
    a description of the company today", which is the right question for a
    level and the wrong one for the t-1 term of a growth rate: last year's
    revenue is SUPPOSED to be a year old. Applying the bound to the history
    would make every growth factor unavailable by construction.

    This is the executable form of `PeerEligibility.lags`, and it is why
    acceleration costs three observations rather than four: the middle one is
    shared between the two differences, and it is counted once here for the
    same reason `pit_derive.leaves` de-duplicates it.
    """
    n_required = max(1, int(n_required))
    per_concept: list[tuple[str, str, int, list[str]]] = []
    for concept in concepts:
        unit, qtrs = _ladder_unit_and_qtrs(concept)
        tags = [t for rung in pit_policy.resolve(concept, as_of, LADDER)
                for t in rung.tags]
        if not tags:
            return False
        per_concept.append((concept, unit, qtrs, tags))

    head_concept, head_unit, head_qtrs, head_tags = per_concept[0]
    marks = ", ".join("?" * len(head_tags))
    periods = [r["period_end"] for r in conn.execute(
        f"""SELECT DISTINCT period_end FROM pit_fact
             WHERE entity_id = ? AND qtrs = ? AND segments = '' AND coreg = ''
               AND unit = ? AND available_date <= ? AND tag IN ({marks})
             ORDER BY period_end DESC""",
        [entity_id, head_qtrs, head_unit, as_of] + head_tags)]

    usable: list[str] = []
    for period_end in periods:
        if all(conn.execute(
                f"""SELECT 1 FROM pit_fact
                     WHERE entity_id = ? AND qtrs = ? AND period_end = ?
                       AND segments = '' AND coreg = '' AND unit = ?
                       AND available_date <= ?
                       AND tag IN ({", ".join("?" * len(tags))}) LIMIT 1""",
                [entity_id, qtrs, period_end, unit, as_of] + tags).fetchone()
               for _c, unit, qtrs, tags in per_concept[1:]):
            usable.append(period_end)

    low, high = ANNUAL_SPACING_DAYS
    for start in range(len(usable)):
        if pit_policy.is_stale(usable[start], as_of, head_qtrs, head_concept):
            continue                      # the LATEST observation must be live
        run = 1
        previous = _ord(usable[start])
        for period_end in usable[start + 1:]:
            gap = previous - _ord(period_end)
            if gap < low:
                continue                  # a restated or duplicated period end
            if gap > high:
                break                     # a MISSING YEAR: the run is over
            run += 1
            previous = _ord(period_end)
            if run >= n_required:
                return True
        if run >= n_required:
            return True
        break            # the newest non-stale period is the only valid start
    return False


# ==========================================================================
# (5b) THE TWO-TEST GATE: sufficient N is not enough
#
# Peer selection now has to pass TWO INDEPENDENT tests.
#
#     SUFFICIENT N          enough members to compute a percentile at all.
#                           Declared per factor as `min_peer_count`.
#     ECONOMIC COHERENCE    the members are actually comparable. Declared per
#                           factor as `max_admissible_rung`, and MEASURED by
#                           the statistics below so that "coherence" is a
#                           number and not the name of a rung.
#
# The measurement that forces the second test: the office rung clears 12
# members 94.5% of the time and those cohorts hold 542-1,310 members, while
# sic4 -- 57.4% of all peer sets -- clears 12 for 2.4% of them. Feasibility and
# meaningfulness move in OPPOSITE directions along the ladder, so a single
# threshold on N can only ever trade one for the other silently.
# ==========================================================================

#: The coherence statistics this module computes, and what each one answers.
#: Held as data so a report can print the definitions beside the numbers and a
#: reader never has to guess which way is better.
COHERENCE_MEASURES: dict[str, dict[str, Any]] = {
    "effective_sic4_count": {
        "definition": ("1 / SUM(share_i^2) over the distinct SIC4 codes inside "
                       "the cohort -- the inverse Herfindahl of its industry "
                       "composition, i.e. the effective number of DIFFERENT "
                       "four-digit industries the cohort is made of."),
        "unit": "industries",
        "better": "lower",
        "degenerate_at": ("sic4, where it is 1.00 by construction -- every "
                          "member shares the code. That is a definitional "
                          "value and is reported as such; the informative "
                          "comparison is sic2 against office, where neither "
                          "value is fixed."),
        "verdict": "ADOPTED",
        "why": ("A peer group built of 140 effective industries is not a peer "
                "group. This is a COMPOSITION statistic: it is scale-free and "
                "does not move with the member count on its own -- doubling a "
                "cohort by adding more of the same industry leaves it "
                "unchanged."),
    },
    "top_sic4_share": {
        "definition": ("the share of the cohort's classified members "
                       "contributed by its single largest SIC4 subgroup."),
        "unit": "share of members",
        "better": "higher",
        "degenerate_at": "sic4, where it is 1.00 by construction",
        "verdict": "ADOPTED",
        "why": ("The same composition fact read the other way round, and the "
                "one a person can check by eye: 'the biggest single industry "
                "in this 800-member peer group is 4% of it' is an argument "
                "nobody needs a statistic to follow."),
    },
    "margin_iqr": {
        "definition": ("p75 - p25 of the EBITDA margin (EBITDA / revenue) "
                       "across cohort members whose EBITDA and revenue both "
                       "resolve at a MATCHED period."),
        "unit": "margin (ratio, not percent)",
        "better": "lower",
        "degenerate_at": "nothing -- it is a sample quantile spread at every rung",
        "verdict": ("REJECTED -- it separates the rungs over all peer sets "
                    "and STOPS separating them at equal member count. At 542+ "
                    "members the office cohorts' median IQR is 0.342 against "
                    "sic2's 1.427: the office cohort is TIGHTER, because "
                    "pooling twenty industries pulls the quartiles toward the "
                    "aggregate centre. A dispersion statistic measures a "
                    "population's variance, not whether it is one population."),
        "why": ("The owner's own test: 'a cohort whose EBITDA margins span "
                "three orders of magnitude is not a peer group'. An "
                "interquartile spread is a consistent estimator of the "
                "population spread, so unlike a range it does not grow with "
                "the member count. Whether it SEPARATES the rungs is an "
                "empirical question, and `pit_peer_coherence.py` answers it."),
    },
    "log10_ebitda_span": {
        "definition": ("p90 - p10 of log10(EBITDA) over cohort members with a "
                       "positive EBITDA -- the cohort's size spread, in "
                       "DECADES."),
        "unit": "orders of magnitude",
        "better": "lower",
        "degenerate_at": "nothing",
        "verdict": ("REJECTED for the same reason, and by a narrower margin: "
                    "2.471 decades at office against 2.672 at sic2 among "
                    "cohorts of the same size. Size dispersion inside ONE "
                    "four-digit industry is already two decades, so there is "
                    "little room left for a wider rung to add."),
        "why": ("Dispersion of the quantity actually being benchmarked. "
                "`ebitda_benchmark` asks what a RIGHT-SIZED peer earns, and a "
                "cohort spanning five decades of EBITDA has no right-sized "
                "peer to speak of. Logs because EBITDA is heavy-tailed across "
                "four orders of magnitude even inside one industry."),
    },
}

#: MEASURED per rung against the real store by `pit_peer_coherence.py`. An
#: unmeasured quantity is absent rather than zero.
#:
#: THE TWO REJECTIONS ARE THE INTERESTING HALF. Both dispersion candidates --
#: the margin IQR the owner's own sentence suggests, and the size span of the
#: quantity being benchmarked -- separate the rungs until the member count is
#: held fixed, and then STOP. At 542+ members the office cohorts' margin IQR is
#: 0.342 against sic2's 1.427: the office cohort is TIGHTER. The mechanism is
#: not mysterious. Pooling twenty industries pulls the quartiles toward the
#: aggregate centre, so a big mixed pool can look calmer than one volatile
#: four-digit industry, and a dispersion statistic therefore measures the
#: population's variance and NOT whether it is one population. What survives is
#: composition: a cohort's industry mix cannot be made to look homogeneous by
#: adding members.
COHERENCE_MEASURED: dict[str, Any] = {
    "evidence": ("pit_peer_coherence.py, run 2026-09-21 against "
                 "shafferfineval_pit.db: 39,038 peer sets over 165 month-end "
                 "as-of dates 2013-01-31..2026-09-18, 1,238,663 entity-dates, "
                 "ladder concept_ladder_v2, duration facts at annual cadence, "
                 "unit='USD'. FULL_REPORTING_UNIVERSE -- no price, no share "
                 "count and no pit_listing anywhere in it. The EBITDA levels "
                 "were checked against pit_factor_spec.ebitda_value_index on "
                 "6,477 values with ZERO disagreements, so the two modules "
                 "select the same numbers."),
    "median_by_rung": {
        "effective_sic4_count": {"sic4": 1.0, "sic3": 1.882, "sic2": 3.365,
                                 "office": 18.006},
        "top_sic4_share": {"sic4": 1.0, "sic3": 0.667, "sic2": 0.436,
                           "office": 0.157},
        "margin_iqr": {"sic4": 0.351, "sic3": 0.207, "sic2": 0.163,
                       "office": 0.342},
        "log10_ebitda_span": {"sic4": 2.068, "sic3": 2.286, "sic2": 2.247,
                              "office": 2.471},
    },
    "size_matched_control": {
        "bucket": "542+ members, the office rung's own range (542-1,310); "
                  "sic2 cohorts reach 1,227, which is why the comparison is "
                  "possible at all",
        "effective_sic4_count": {"office": 18.006, "sic2": 3.285,
                                 "office_worse": True},
        "top_sic4_share": {"office": 0.157, "sic2": 0.438, "office_worse": True},
        "margin_iqr": {"office": 0.342, "sic2": 1.427, "office_worse": False},
        "log10_ebitda_span": {"office": 2.471, "sic2": 2.672,
                              "office_worse": False},
    },
    "cohort_sizes": {
        "office": {"n_sets": 676, "min": 542, "median": 908.5, "max": 1310},
        "sic4": {"n_sets": 22393, "median": 23},
        "sic3": {"n_sets": 8652, "median": 25},
        "sic2": {"n_sets": 7317, "median": 49},
    },
    "cost_of_the_ban": {
        "n_entity_dates": 1238663,
        "kept_at_sic4_sic3_sic2": 1014548,
        "lost_no_admissible_peer_set": 10156,
        "lost_admissible_set_too_thin": 47122,
        "lost_total": 57278,
        "lost_pct_of_entity_dates": 4.62,
        "lost_pct_of_those_the_office_rung_would_have_served": 9.31,
        "already_unavailable_everywhere": 166837,
        "worst_divisions_pct": {"A Agriculture, Forestry & Fishing": 66.32,
                                "J Public Administration": 25.95,
                                "C Construction": 21.12,
                                "G Retail Trade": 20.43,
                                "B Mining": 17.81},
        "unaffected_divisions_pct": {"F Wholesale Trade": 0.0,
                                     "H Finance, Insurance & Real Estate": 0.0},
        "by_year_pct": {"2013": 4.31, "2019": 5.32, "2024": 5.89,
                        "2026": 7.25},
        "eligibility_proxy": (
            "N is counted as the cohort members whose EBITDA resolves -- the "
            "EBITDA-ONLY rule the owner locked in decision 4 -- against "
            "ev_ebitda's threshold of 12. That is an UPPER BOUND on the "
            "EV/EBITDA cohort, whose six-primitive rule is a strict superset, "
            "so an entity-date counted as KEPT here may still lose valuation "
            "for a reason that has nothing to do with the rung. The ban's cost "
            "is therefore measured at its most generous, and the price-free "
            "half of it -- 10,156 entity-dates with no admissible peer set at "
            "ALL -- is exact."),
    },
}

#: Which candidates SURVIVED the size-matched control and may therefore be
#: quoted as coherence. Set from the measurement, not from taste: a statistic
#: that stops separating office from sic2 once the member count is held fixed
#: is measuring N, and quoting it would make the second test a disguised copy
#: of the first. The rejected candidates keep their measured numbers in
#: COHERENCE_MEASURED so the rejection stays evidence rather than an opinion.
COHERENCE_ADOPTED: tuple[str, ...] = ("effective_sic4_count", "top_sic4_share")


@dataclass(frozen=True)
class CohortCoherence:
    """How coherent ONE cohort is, as numbers rather than as a rung name.

    Every field is Optional because a cohort can be big and still carry no
    usable values -- and "not measurable" is a different answer from "measured
    and bad". A gate that treated the two the same would refuse a cohort for
    having no revenue coverage, which is a coverage fact and not an incoherence.
    """

    rung: str
    n_members: int
    n_distinct_sic4: int
    effective_sic4_count: Optional[float]
    top_sic4_share: Optional[float]
    n_margin: int
    margin_iqr: Optional[float]
    margin_p10_p90: Optional[float]
    n_level: int = 0
    log10_ebitda_span: Optional[float] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "rung": self.rung, "n_members": self.n_members,
            "n_distinct_sic4": self.n_distinct_sic4,
            "effective_sic4_count": self.effective_sic4_count,
            "top_sic4_share": self.top_sic4_share,
            "n_margin": self.n_margin, "margin_iqr": self.margin_iqr,
            "margin_p10_p90": self.margin_p10_p90,
            "n_level": self.n_level,
            "log10_ebitda_span": self.log10_ebitda_span,
        }


def cohort_coherence(rung: str, sic_by_member: Mapping[int, Optional[str]],
                     margins: Optional[Mapping[int, float]] = None,
                     levels: Optional[Mapping[int, float]] = None
                     ) -> CohortCoherence:
    """The coherence statistics for one cohort. Pure: no store, no network.

    `sic_by_member` is every member's point-in-time SIC (None where the issuer
    carried none -- counted in `n_members` and excluded from the composition,
    because an unclassified member is not evidence about industry mix).
    `margins` is the EBITDA margin of the members that have one; `levels` is
    the EBITDA itself, from which the size span in decades is taken over the
    members with a POSITIVE one (log10 of a loss is not a number, and dropping
    the loss-makers is stated rather than hidden in `n_level`).
    """
    members = list(sic_by_member)
    codes = [str(s)[:4] for s in sic_by_member.values() if s]
    effective: Optional[float] = None
    top: Optional[float] = None
    distinct = 0
    if codes:
        counts: dict[str, int] = {}
        for code in codes:
            counts[code] = counts.get(code, 0) + 1
        distinct = len(counts)
        total = float(len(codes))
        shares = [n / total for n in counts.values()]
        hhi = sum(s * s for s in shares)
        effective = round(1.0 / hhi, 3) if hhi > 0 else None
        top = round(max(shares), 4)

    values = sorted(float(v) for v in (margins or {}).values()
                    if v is not None and v == v and abs(v) != float("inf"))
    iqr = span = None
    if len(values) >= 4:
        p25 = statlib.percentile(values, 0.25)
        p75 = statlib.percentile(values, 0.75)
        p10 = statlib.percentile(values, 0.10)
        p90 = statlib.percentile(values, 0.90)
        if p25 is not None and p75 is not None:
            iqr = round(p75 - p25, 6)
        if p10 is not None and p90 is not None:
            span = round(p90 - p10, 6)

    sizes = sorted(math.log10(float(v)) for v in (levels or {}).values()
                   if v is not None and v == v and float(v) > 0.0)
    decades = None
    if len(sizes) >= 4:
        low = statlib.percentile(sizes, 0.10)
        high = statlib.percentile(sizes, 0.90)
        if low is not None and high is not None:
            decades = round(high - low, 4)

    return CohortCoherence(
        rung=str(rung), n_members=len(members), n_distinct_sic4=distinct,
        effective_sic4_count=effective, top_sic4_share=top,
        n_margin=len(values), margin_iqr=iqr, margin_p10_p90=span,
        n_level=len(sizes), log10_ebitda_span=decades)


@dataclass(frozen=True)
class PeerSetGate:
    """The decision, with BOTH tests reported whether or not either refused.

    `ok` is the conjunction. `failed_tests` names which of the two refused, in
    ladder order, because "too few peers carried the primitives" is a coverage
    problem that more data fixes and "the only cohort big enough is an SEC
    office" is not, and a consumer that cannot tell them apart will spend
    months on the wrong one.

    `n_eligible` is the count the gate was HANDED: the eligible count when
    eligibility was evaluated, and the OFFERED count when the rung test
    refused first and no eligibility was computed. In that second case it is
    an upper bound, which is all the rung test needs -- a cohort refused for
    being an SEC office is not refused for its size.
    """

    factor_key: str
    as_of: str
    rung: str
    n_eligible: int
    min_peer_count: int
    max_admissible_rung: str
    admissible_rungs: tuple[str, ...]
    sufficient_n: bool
    rung_admissible: bool
    coherence: Optional[CohortCoherence]
    ok: bool
    availability: str
    reason: Optional[str]
    failed_tests: tuple[str, ...]
    spec_version: str
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "factor_key": self.factor_key, "as_of": self.as_of,
            "rung": self.rung, "n_eligible": self.n_eligible,
            "min_peer_count": self.min_peer_count,
            "max_admissible_rung": self.max_admissible_rung,
            "admissible_rungs": list(self.admissible_rungs),
            "sufficient_n": self.sufficient_n,
            "rung_admissible": self.rung_admissible,
            "coherence": self.coherence.as_dict() if self.coherence else None,
            "ok": self.ok, "availability": self.availability,
            "reason": self.reason, "failed_tests": list(self.failed_tests),
            "spec_version": self.spec_version, "note": self.note,
        }


def is_valuation_factor(factor_key: str) -> bool:
    """Whether this factor answers the VALUATION question.

    Read off `VALUATION_FACTORS` rather than off the pillar weights, so adding
    a valuation factor means adding it to one frozenset and inheriting the ban.
    """
    return str(factor_key) in VALUATION_FACTORS


def refusal_reason(factor_key: str, version: Optional[str] = None) -> str:
    """The `unavailable_reason` a refused cohort carries for this factor.

    Under `factor_spec_v2` a valuation factor gets the owner's first-class
    outcome; everything else keeps pit_store's existing `insufficient_peers`.
    One decision, in one place, so the two vocabularies cannot be mixed by a
    caller in a hurry.

    Under v1 the answer is ALWAYS `insufficient_peers`, because v1 is frozen
    and a replay of it must emit what it always emitted. The new outcome is a
    v2 statement -- it is the answer given by a gate v1 does not have.
    """
    name = version or DEFAULT_SPEC_VERSION
    if name != FACTOR_SPEC_VERSION and is_valuation_factor(factor_key):
        return VALUATION_PEER_SET_INSUFFICIENT
    return REASON_TOO_FEW


def peer_set_gate(factor_key: str, rung: str, n_eligible: int, *,
                  as_of: str = "", coherence: Optional[CohortCoherence] = None,
                  version: Optional[str] = None) -> PeerSetGate:
    """BOTH tests, evaluated together. The gate the owner's ruling describes.

    SUFFICIENT N is `n_eligible >= min_peer_count`. ECONOMIC COHERENCE is, in
    production, the declared rung ceiling -- a coarse but AUDITABLE proxy for
    the measured statistics, and the one the owner locked. The measured
    statistics ride along on the record when the caller has them, so a refusal
    and an acceptance both carry the number that justifies the rule.

    The rung test is evaluated FIRST and needs no database at all: an office
    cohort with 900 eligible members is refused without a single SELECT,
    because its size is exactly what is not the point.
    """
    item = spec(factor_key, version)
    name = version or DEFAULT_SPEC_VERSION
    admissible = item.rung_admissible(rung)
    enough = int(n_eligible) >= int(item.min_peer_count)
    failed: list[str] = []
    if not admissible:
        failed.append(TEST_ECONOMIC_COHERENCE)
    if not enough:
        failed.append(TEST_SUFFICIENT_N)

    if not failed:
        return PeerSetGate(
            factor_key=factor_key, as_of=str(as_of)[:10], rung=str(rung),
            n_eligible=int(n_eligible), min_peer_count=item.min_peer_count,
            max_admissible_rung=item.max_admissible_rung,
            admissible_rungs=item.admissible_rungs, sufficient_n=True,
            rung_admissible=True, coherence=coherence, ok=True,
            availability=pit_store.AVAIL_COMPLETE, reason=None,
            failed_tests=(), spec_version=name)

    if not admissible:
        note = (f"rung {rung!r} is wider than {factor_key}'s declared ceiling "
                f"{item.max_admissible_rung!r}; admissible rungs are "
                f"{list(item.admissible_rungs)}. {n_eligible} members is not "
                f"the question -- at the office rung a 'peer' is any issuer "
                f"supervised by the same SEC Corporation Finance office. "
                f"{_V2_CEILING_NOTE}")
    else:
        note = (f"{n_eligible} of a required {item.min_peer_count} eligible "
                f"peers at rung {rung!r} ({item.min_peer_why}) -> "
                f"{item.missingness_behaviour}.")

    return PeerSetGate(
        factor_key=factor_key, as_of=str(as_of)[:10], rung=str(rung),
        n_eligible=int(n_eligible), min_peer_count=item.min_peer_count,
        max_admissible_rung=item.max_admissible_rung,
        admissible_rungs=item.admissible_rungs, sufficient_n=enough,
        rung_admissible=admissible, coherence=coherence, ok=False,
        availability=pit_store.AVAIL_UNAVAILABLE,
        reason=refusal_reason(factor_key, name), failed_tests=tuple(failed),
        spec_version=name, note=note)


def valuation_pillar_reason(
        results: Iterable[Any]) -> Optional[str]:
    """The reason the VALUATION PILLAR is absent, or None if it is not.

    Takes the per-factor outcomes for the valuation factors -- `PeerSetGate`s,
    `EligibleCohort`s, or anything else carrying `.factor_key` and `.ok` -- and
    answers the one question the score row needs: was a valuation cohort formed
    at all? The pillar survives if ANY valuation factor did; it is absent, with
    the owner's first-class outcome, only when none did.

    THIS IS THE WIRE. `peer_set_gate` produces the outcome, this reduces the
    factor-level outcomes to a PILLAR-level one, and
    `pit_score_signature.score_v2(scores, reasons={'valuation': <this>})` turns
    that into a PARTIAL SHAFFER SCORE that is not comparable with a full one.
    Three modules, one string, defined once in `pit_store`.

    Returns None when nothing was offered: "no valuation factor was even
    attempted" is not the same statement as "none could be formed", and
    inventing a reason for it would put a finding where there is a silence.
    """
    seen = False
    for result in results:
        key = getattr(result, "factor_key", None)
        if key not in VALUATION_FACTORS:
            continue
        seen = True
        if getattr(result, "ok", False):
            return None
    return VALUATION_PEER_SET_INSUFFICIENT if seen else None


def _priced_and_defensible(conn: sqlite3.Connection, as_of: str,
                           wanted: Sequence[int]) -> tuple[set[int], set[int]]:
    """(price-resolvable, defensible-share-count) among `wanted`, at `as_of`.

    SURVIVOR_ONLY_DIAGNOSTIC by construction: `pit_identity.scored_universe_as_of`
    reads `pit_listing`, which holds 2,574 lines, every one alive in 2026.
    Imported lazily so this module stays importable on a bare interpreter and so
    a price-free factor never pays for the price machinery.
    """
    import pit_identity
    import pit_rawprice
    universe = {int(r["entity_id"]) for r in
                pit_identity.scored_universe_as_of(conn, as_of)}
    priced = {e for e in wanted if e in universe}
    defensible: set[int] = set()
    for entity_id in sorted(priced):
        shares = pit_rawprice.shares_as_of_any(conn, entity_id, as_of)
        if not shares.get("available") or shares.get("zero_count"):
            continue
        audit = pit_rawprice.share_class_audit(conn, entity_id, as_of,
                                               shares=shares)
        if pit_rawprice.class_decision(
                audit, pit_rawprice.CLASS_POLICY_STRICT)["ok"]:
            defensible.add(entity_id)
    return priced, defensible


def eligible_peers(conn: sqlite3.Connection, factor_key: str, as_of: str,
                   cohort: Sequence[int], *, rung: Optional[str] = None,
                   version: Optional[str] = None) -> EligibleCohort:
    """The subset of `cohort` eligible for THIS factor, with the count.

    THIS IS THE FIX. The same peer set, handed to two factors, yields two
    different cohorts, because each factor's `peer_eligibility` rule is
    evaluated on its own terms:

        eligible_peers(conn, 'ebitda_benchmark', d, peers)  -> EBITDA only
        eligible_peers(conn, 'ev_ebitda',        d, peers)  -> six primitives

    The EBITDA benchmark stops being conditional on price. Measured over the
    whole store, that is the difference between a median cohort of 13 and a
    median cohort of 1.

    `cohort` is a sequence of entity ids -- a point-in-time peer set, from
    `pit_peers.peer_members`. An int is accepted as a peer_set_id and resolved.
    A factor whose rule names an UNAVAILABLE primitive is refused by name
    rather than returning an empty cohort that would read as scarcity.

    THE SECOND TEST. `rung` is the SIC rung the peer set was drawn at, and a
    factor that declares a `max_admissible_rung` is REFUSED on a wider one
    before a single SELECT runs -- the size of an office cohort is exactly what
    is not the point. Passing a `peer_set_id` resolves the rung from the row;
    passing a bare member list requires the caller to say which rung it came
    from, and a ceilinged factor RAISES rather than guessing, because a gate
    that silently skips when it is not told the rung is not a gate.

    Read-only, short transactions: every call is indexed SELECTs, and a reader
    that held a snapshot would block the WAL checkpointer.
    """
    item = spec(factor_key, version)
    rule = item.peer_eligibility
    spec_version = version or DEFAULT_SPEC_VERSION

    if isinstance(cohort, int):
        import pit_peers
        offered = tuple(pit_peers.peer_members(conn, cohort))
        if rung is None:
            row = conn.execute(
                "SELECT rung FROM pit_peer_set WHERE peer_set_id = ?",
                (int(cohort),)).fetchone()
            if row is not None:
                rung = row["rung"] if not isinstance(row, tuple) else row[0]
    else:
        offered = tuple(int(e) for e in cohort)

    def refuse(reason: str, note: str, members: tuple[int, ...] = (),
               gate: Optional[PeerSetGate] = None) -> EligibleCohort:
        return EligibleCohort(factor_key, str(as_of)[:10], rule.rule_id, members,
                              len(members), len(offered),
                              pit_store.AVAIL_UNAVAILABLE, reason,
                              item.sample_scope, note, str(rung or ""), gate)

    # ---- TEST 2, ECONOMIC COHERENCE. Costs nothing and runs first. --------
    if item.max_admissible_rung:
        if rung is None:
            raise ValueError(
                f"{factor_key} declares max_admissible_rung="
                f"{item.max_admissible_rung!r} under {spec_version}, so the "
                "rung the peer set was drawn at must be supplied: pass "
                "rung='sic4'/'sic3'/'sic2'/'office', or pass a peer_set_id and "
                "let it be read from the row. A cohort whose rung is unknown "
                "cannot be certified admissible.")
        gate = peer_set_gate(factor_key, rung, len(offered), as_of=as_of,
                             version=spec_version)
        if not gate.rung_admissible:
            extra = ""
            if rule.unavailable_primitives:
                extra = (f" (this factor is separately uncomputable in this "
                         f"store: {', '.join(rule.unavailable_primitives)} is "
                         f"not ingested)")
            return refuse(gate.reason or VALUATION_PEER_SET_INSUFFICIENT,
                          gate.note + extra, (), gate)

    if rule.unavailable_primitives:
        return refuse(REASON_PRIMITIVE_ABSENT,
                      f"{', '.join(rule.unavailable_primitives)} is not in this "
                      f"store and no cohort can be formed for {factor_key}. "
                      f"{rule.why}")

    # `cpi` is a MACRO leaf: one value per as-of date, shared by everything, and
    # 100% available on every grid date. Charging each peer for it would make a
    # cohort condition out of a constant.
    fundamentals = [p for p in rule.primitives
                    if p not in MARKET_CONDITIONAL and p != "cpi"]
    matched = tuple(rule.matched_period)
    singles = tuple(p for p in fundamentals if p not in matched)
    n_obs = rule.n_observations

    if n_obs > 1 and matched and singles:
        return refuse(
            "mixed_group_multi_observation_rule",
            f"{factor_key} asks for {n_obs} observations of a matched group "
            f"{matched} AND of {singles} separately. Whether those have to be "
            f"the SAME periods is a modelling decision nobody has made, so the "
            f"cohort is refused rather than guessed.")

    priced: set[int] = set()
    defensible: set[int] = set()
    if rule.requires_price or rule.requires_defensible_shares:
        priced, defensible = _priced_and_defensible(conn, as_of, offered)

    group = matched or singles
    members: list[int] = []
    for entity_id in offered:
        if rule.requires_price and entity_id not in priced:
            continue
        if rule.requires_defensible_shares and entity_id not in defensible:
            continue
        if n_obs > 1:
            if not matched_period_observations(conn, entity_id, as_of, group,
                                               n_obs):
                continue
        else:
            if matched and not matched_period_available(conn, entity_id, as_of,
                                                        matched):
                continue
            if any(not concept_available(conn, entity_id, as_of, c)
                   for c in singles):
                continue
        members.append(entity_id)

    kept = tuple(members)
    note = ""
    if rule.positive_screen or rule.value_band:
        note = (f"AVAILABILITY only. This rule also carries value screens "
                f"({rule.positive_screen or ''}"
                f"{'; band ' + str(rule.value_band) if rule.value_band else ''})"
                f" which need the VALUES, not the primitives -- apply them in "
                f"the cohort builder. This count is an UPPER BOUND on the "
                f"screened cohort.")
    gate = (peer_set_gate(factor_key, rung, len(kept), as_of=as_of,
                          version=spec_version) if rung is not None else None)
    if len(kept) < item.min_peer_count:
        return refuse(refusal_reason(factor_key, spec_version),
                      f"{len(kept)} of {len(offered)} peers satisfy "
                      f"{rule.rule_id}; {factor_key} requires "
                      f"{item.min_peer_count} ({item.min_peer_why}) "
                      f"-> {item.missingness_behaviour}. " + note, kept, gate)
    return EligibleCohort(factor_key, str(as_of)[:10], rule.rule_id, kept,
                          len(kept), len(offered), pit_store.AVAIL_COMPLETE,
                          None, item.sample_scope, note, str(rung or ""), gate)


# ==========================================================================
# (6) THE 50-75 COHORT, REBUILT CORRECTLY
# ==========================================================================

@dataclass(frozen=True)
class BandCohort:
    """The 50-75 band and the benchmark computed from it.

    Both counts are reported, because they answer different questions: the
    VECTOR is who entered the percentiles and the BAND is whose mean IS the
    benchmark. The frozen core reports only the second, which is why "the
    benchmark is unavailable" has never named which of the two failed.
    """

    n_vector: int
    p50: Optional[float]
    p75: Optional[float]
    band: tuple[int, ...]
    n_band: int
    mean_ebitda: Optional[float]
    availability: str
    reason: Optional[str]
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.availability == pit_store.AVAIL_COMPLETE

    def as_dict(self) -> dict[str, Any]:
        return {"n_vector": self.n_vector, "p50": self.p50, "p75": self.p75,
                "n_band": self.n_band, "mean_ebitda": self.mean_ebitda,
                "availability": self.availability, "reason": self.reason,
                "note": self.note, "band": list(self.band)}


def benchmark_band(values: Mapping[int, float], *, min_vector: int = 12,
                   min_band: int = 3) -> BandCohort:
    """PIT sector peers -> peers with valid EBITDA -> P50, P75 -> the 50-75
    band -> the mean EBITDA of that band. NOTHING ELSE IN THE FILTER.

    `values` is already the factor's own eligible cohort: entity -> EBITDA. The
    filtering happened in `eligible_peers` under EBITDA_ONLY, so by the time a
    value reaches this function there is no price condition left to remove.

    The arithmetic is deliberately the frozen core's: `statlib.percentile` is
    the same interpolated percentile `company_scoring` calls, and the band is
    the same inclusive `p50 <= e <= p75`. ONLY the eligibility rule changed, so
    the measured delta is attributable to the correction and to nothing else.
    """
    if not values:
        return BandCohort(0, None, None, (), 0, None,
                          pit_store.AVAIL_UNAVAILABLE, REASON_TOO_FEW,
                          "no peer carried a resolvable EBITDA")
    ordered = sorted(float(v) for v in values.values())
    n = len(ordered)
    if n < min_vector:
        return BandCohort(n, None, None, (), 0, None,
                          pit_store.AVAIL_UNAVAILABLE, REASON_TOO_FEW,
                          f"{n} peers with EBITDA; the 50-75 band needs "
                          f"{min_vector} in the vector for {min_band} in the band")
    p50 = statlib.percentile(ordered, 0.50)
    p75 = statlib.percentile(ordered, 0.75)
    band = tuple(sorted(e for e, v in values.items()
                        if p50 <= float(v) <= p75))
    if len(band) < min_band:
        return BandCohort(n, p50, p75, band, len(band), None,
                          pit_store.AVAIL_UNAVAILABLE, REASON_NO_BAND,
                          f"{len(band)} members inside [{p50:,.0f}, {p75:,.0f}]"
                          f" of a {n}-member vector; {min_band} required")
    mean = sum(float(values[e]) for e in band) / len(band)
    return BandCohort(n, p50, p75, band, len(band), mean,
                      pit_store.AVAIL_COMPLETE, None,
                      f"50-75 band of {n} valid peers "
                      f"({p50:,.0f} to {p75:,.0f}); {len(band)} qualify")


def ebitda_benchmark_cohort(conn: sqlite3.Connection, as_of: str,
                            cohort: Sequence[int],
                            ebitda_values: Mapping[int, float]) -> BandCohort:
    """The corrected benchmark, end to end, for one peer set on one date.

    Chains `eligible_peers('ebitda_benchmark', ...)` -- EBITDA only -- into
    `benchmark_band`. `ebitda_values` supplies the point-in-time EBITDA per
    entity; this function will not invent one, because an imputed EBITDA inside
    a benchmark is the same class of error as a price inside its filter.
    """
    item = spec("ebitda_benchmark")
    eligible = eligible_peers(conn, "ebitda_benchmark", as_of, cohort)
    priced_free = {e: ebitda_values[e] for e in eligible.members
                   if e in ebitda_values}
    if len(priced_free) < len(eligible.members):
        missing = len(eligible.members) - len(priced_free)
        return BandCohort(len(priced_free), None, None, (), 0, None,
                          pit_store.AVAIL_UNAVAILABLE, REASON_PRIMITIVE_ABSENT,
                          f"{missing} eligible peers had no EBITDA VALUE "
                          f"supplied; the cohort is refused rather than built "
                          f"on a partial vector")
    return benchmark_band(priced_free, min_vector=item.min_peer_count,
                          min_band=item.min_band_members)


def price_conditional_cohort_UNCORRECTED(
        values: Mapping[int, float], eligible_ev: Sequence[int], *,
        min_vector: int = 12, min_band: int = 3) -> BandCohort:
    """The CURRENT rule, reconstructed for measurement ONLY. Do not call this
    to produce a benchmark.

    `company_scoring.build_ebitda_peer_cohort` is frozen and is not edited or
    imported; this is a faithful reconstruction of its eligibility step --
    percentiles taken over the peers with a usable EV/EBITDA -- so the A/B in
    `--measure` compares two rules and not two implementations. The band
    arithmetic below is byte-identical to `benchmark_band`'s because both call
    `statlib.percentile`.

    It is an UPPER BOUND on the core's cohort: the core also requires EV > 0
    and a multiple inside [0.1, 300], and neither can be evaluated without
    prices and share counts as VALUES, which this store's survivor-only listing
    table could only supply for a survivor subset anyway.
    """
    keep = {e: v for e, v in values.items() if e in set(eligible_ev)}
    result = benchmark_band(keep, min_vector=min_vector, min_band=min_band)
    return BandCohort(result.n_vector, result.p50, result.p75, result.band,
                      result.n_band, result.mean_ebitda, result.availability,
                      result.reason,
                      "UNCORRECTED price-conditional reconstruction; "
                      + SURVIVOR_ONLY + "; upper bound on the frozen core's "
                      "cohort (EV>0 and the [0.1,300] band are not applied). "
                      + result.note)


# ==========================================================================
# (7) MEASURED COHORT DISTRIBUTIONS
#
# Every number below was produced by `--measure` against the loaded store. A
# figure that has not been measured is the string "UNKNOWN", never 0 and never
# a plausible-looking estimate.
# ==========================================================================

#: P(cohort forms) per eligibility rule, over all 39,038 peer sets.
#: `pct_ge_k` is the share of peer sets with at least k eligible members.
MEASURED_COHORT: dict[str, dict[str, Any]] = {
    "ebitda_only_v1": {
        "metric": "N_EBITDA", "scope": FULL_UNIVERSE, "n_sets": 39038,
        "min": 0, "p10": 5, "p25": 8, "median": 13, "p75": 23, "p90": 70,
        "max": 718, "mean": 36.0,
        "pct_ge_3": 95.2, "pct_ge_5": 91.7, "pct_ge_8": 80.2, "pct_ge_12": 56.0,
        "by_rung": {"sic4": {"median": 11, "pct_ge_3": 92.6, "pct_ge_12": 46.7},
                    "sic3": {"median": 13, "pct_ge_3": 97.5, "pct_ge_12": 58.6},
                    "sic2": {"median": 21, "pct_ge_3": 100.0, "pct_ge_12": 77.2},
                    "office": {"median": 471, "pct_ge_3": 100.0, "pct_ge_12": 100.0}},
    },
    "ebitda_and_revenue_matched_v1": {
        "metric": "N_RevEBITDA", "scope": FULL_UNIVERSE, "n_sets": 39038,
        "min": 0, "p10": 4, "p25": 8, "median": 12, "p75": 22, "p90": 66,
        "max": 699, "mean": 33.1,
        "pct_ge_3": 94.5, "pct_ge_5": 89.9, "pct_ge_8": 76.5, "pct_ge_12": 52.8,
    },
    "ev_ebitda_six_primitives_v1": {
        "metric": "N_EVEBITDA", "scope": SURVIVOR_ONLY, "n_sets": 39038,
        "min": 0, "p10": 0, "p25": 0, "median": 1, "p75": 3, "p90": 8,
        "max": 95, "mean": 4.0,
        "pct_ge_3": 34.2, "pct_ge_5": 18.6, "pct_ge_8": 11.5, "pct_ge_12": 7.0,
        "by_rung": {"sic4": {"median": 1, "pct_ge_3": 22.9, "pct_ge_12": 2.4},
                    "sic3": {"median": 2, "pct_ge_3": 39.2, "pct_ge_12": 6.2},
                    "sic2": {"median": 3, "pct_ge_3": 57.0, "pct_ge_12": 14.0},
                    "office": {"median": 61, "pct_ge_3": 96.6, "pct_ge_12": 94.5}},
    },
    "debt_over_market_cap_v1": {
        "metric": "N_PriceShares", "scope": SURVIVOR_ONLY, "n_sets": 39038,
        "min": 0, "p10": 1, "p25": 2, "median": 4, "p75": 9, "p90": 27,
        "max": 281, "mean": 13.3,
        "pct_ge_3": 69.7, "pct_ge_5": 48.5, "pct_ge_8": 29.1, "pct_ge_12": 19.5,
    },
    "price_and_pit_eps_v1": {
        "metric": "N_PE_proxy", "scope": SURVIVOR_ONLY, "n_sets": 39038,
        "min": 0, "p10": 3, "p25": 5, "median": 9, "p75": 16, "p90": 54,
        "max": 449, "mean": 23.9,
        "pct_ge_3": 92.0, "pct_ge_5": 81.9, "pct_ge_8": 57.5, "pct_ge_12": 36.0,
        "proxy": ("net_income stands in for EPS: EPS IS NOT IN THE STORE. An "
                  "upper bound -- P/E is undefined for a loss-maker."),
    },
    "net_debt_over_ebitda_v1": {
        "metric": "N_Debt (binding leg)", "scope": FULL_UNIVERSE,
        "n_sets": 39038, "min": 0, "p10": 4, "p25": 6, "median": 11, "p75": 20,
        "p90": 53, "max": 537, "mean": 25.8,
        "pct_ge_3": 95.0, "pct_ge_5": 86.5, "pct_ge_8": 68.2, "pct_ge_12": 45.2,
        "note": ("the debt leg alone; the debt-AND-EBITDA joint per peer set "
                 "is UNKNOWN -- not measured, and not assumed to be the "
                 "product."),
    },
    "revenue_two_periods_plus_cpi_v1": {
        "metric": "N_Revenue (one period)", "scope": FULL_UNIVERSE,
        "n_sets": 39038, "min": 0, "p10": 10, "p25": 13, "median": 19,
        "p75": 37, "p90": 104, "max": 1119, "mean": 52.8,
        "pct_ge_3": 98.8, "pct_ge_5": 98.3, "pct_ge_8": 95.5, "pct_ge_12": 82.0,
        "note": "TWO-period joint UNKNOWN; this is the one-period rate.",
    },
    "ebitda_t_and_t1_v1": {
        "metric": "peers with TWO consecutive annual EBITDA observations",
        "scope": FULL_UNIVERSE, "n_sets": 39038,
        "min": 0, "p10": 5, "p25": 8, "median": 12, "p75": 23, "p90": 68,
        "max": 651, "mean": 34.74,
        "pct_ge_3": 94.01, "pct_ge_5": 90.06, "pct_ge_8": 77.93,
        "pct_ge_12": 54.00,
    },
    "ebitda_t_t1_t2_v1": {
        "metric": "peers with THREE consecutive annual EBITDA observations",
        "scope": FULL_UNIVERSE, "n_sets": 39038,
        "min": 0, "p10": 0, "p25": 6, "median": 11, "p75": 20, "p90": 59,
        "max": 568, "mean": 30.27,
        "pct_ge_3": 86.03, "pct_ge_5": 82.00, "pct_ge_8": 69.25,
        "pct_ge_12": 47.44,
        "note": ("THREE, not four. A coverage model built as growth_t x "
                 "growth_t-1 would have demanded four observations and "
                 "reported this cohort at median 9 / 78.0% / 39.2% -- see "
                 "OBSERVATION_DEPTH."),
    },
    "net_income_and_total_assets_v1": {"measured": "UNKNOWN",
                                       "scope": FULL_UNIVERSE},
    "net_income_and_equity_v1": {"measured": "UNKNOWN", "scope": FULL_UNIVERSE},
    "operating_income_and_interest_v1": {"measured": "UNKNOWN",
                                         "scope": FULL_UNIVERSE},
    "fcf_over_ebitda_v1": {"measured": "UNKNOWN", "scope": FULL_UNIVERSE},
}

#: THE MULTI-OBSERVATION JOINT AT COMPANY LEVEL, MEASURED -- and the single
#: most wrong number this project has been carrying.
#:
#: `pit_derive` prices a growth factor as EBITDA squared and an acceleration as
#: EBITDA cubed, flagging each lagged leaf `lag_persistence` because no survey
#: existed. It does now. Share of base (the peer universe with a usable
#: non-stale Assets fact) carrying k CONSECUTIVE annual EBITDA observations:
#:
#:     date          >=1 obs   >=2 obs   >=3 obs   >=4 obs   base
#:     2015-06-30    48.14%    47.56%    45.50%    34.40%    7,073
#:     2019-06-28    50.73%    49.77%    47.36%    43.82%    5,959
#:     2024-06-28    51.31%    50.96%    48.32%    44.70%    5,944
#:
#: The >=1 column reproduces pit_derive.MEASURED_JOINTS['ebitda'] exactly at
#: all three dates (48.1 / 50.7 / 51.3), which is what says this is the same
#: measurement and not a different one.
#:
#: THE INDEPENDENCE PRODUCT IS OUT BY 3.6x. At 2024 it says 0.513^2 = 26.3% for
#: growth and 0.513^3 = 13.5% for acceleration, against a measured 50.96% and
#: 48.32%. So `pit_derive`'s note -- "this is why acceleration is the scarcest
#: growth component and why its scarcity is structural" -- is WRONG, and wrong
#: in an actionable direction: acceleration costs THREE POINTS of company
#: coverage against a level (51.31% -> 48.32%), not the 74% the product
#: implies. A filer that reports EBITDA once reports it every year; depth is
#: nearly free and the growth block is the cheapest part of the model.
#:
#: The >=4 column is again the cost of the mistake: 44.70% against 48.32%, so
#: even at company level, charging for the shared observation twice would have
#: thrown away 3.6 points of coverage.
MEASURED_OBSERVATION_JOINT: dict[int, dict[str, float]] = {
    1: {"2015-06-30": 0.4814, "2019-06-28": 0.5073, "2024-06-28": 0.5131},
    2: {"2015-06-30": 0.4756, "2019-06-28": 0.4977, "2024-06-28": 0.5096},
    3: {"2015-06-30": 0.4550, "2019-06-28": 0.4736, "2024-06-28": 0.4832},
    4: {"2015-06-30": 0.3440, "2019-06-28": 0.4382, "2024-06-28": 0.4470},
}

#: The base each of those fractions is a share of, restated so a reader never
#: has to guess the denominator.
MEASURED_OBSERVATION_BASE = {"2015-06-30": 7073, "2019-06-28": 5959,
                             "2024-06-28": 5944}


#: THE UNIQUE-LEAF RULE, PRICED. How many peers per peer set carry k
#: CONSECUTIVE annual EBITDA observations, over all 39,038 peer sets. A growth
#: factor needs two, an acceleration needs THREE, and the whole point of
#: de-duplicating the leaf set is that acceleration does NOT need four: its two
#: differences share E_t-1.
#:
#: The fourth row is therefore not a factor anybody builds -- it is THE COST OF
#: THE MISTAKE, measured. Charging for the shared observation twice would have
#: reported the acceleration cohort at median 9 and P(N>=3) 78.0% instead of
#: median 11 and 86.0%: two members of median cohort size and 8.1 points of
#: formation rate, thrown away by an arithmetic error in the coverage model
#: rather than by anything missing from the store.
OBSERVATION_DEPTH: dict[str, Any] = {
    "scope": FULL_UNIVERSE, "n_sets": 39038,
    "spacing_window_days": ANNUAL_SPACING_DAYS,
    "staleness_rule": ("the LATEST observation must be non-stale; the history "
                       "need only have been available on the date. Last year's "
                       "revenue is SUPPOSED to be a year old, and applying the "
                       "level's staleness bound to it would make every growth "
                       "factor unavailable by construction."),
    "1_observation_level": {"p10": 5, "p25": 8, "median": 13, "p75": 23,
                            "p90": 70, "max": 718, "mean": 36.04,
                            "pct_ge_3": 95.20, "pct_ge_12": 56.00},
    "2_observations_growth": {"p10": 5, "p25": 8, "median": 12, "p75": 23,
                              "p90": 68, "max": 651, "mean": 34.74,
                              "pct_ge_3": 94.01, "pct_ge_12": 54.00},
    "3_observations_acceleration": {"p10": 0, "p25": 6, "median": 11, "p75": 20,
                                    "p90": 59, "max": 568, "mean": 30.27,
                                    "pct_ge_3": 86.03, "pct_ge_12": 47.44},
    "4_observations_the_mistake": {"p10": 0, "p25": 4, "median": 9, "p75": 17,
                                   "p90": 49, "max": 492, "mean": 25.15,
                                   "pct_ge_3": 77.97, "pct_ge_12": 39.22},
    "cost_of_double_charging_the_shared_observation": {
        "median_members": "11 -> 9",
        "pct_ge_3": "86.03% -> 77.97%  (-8.06 pp)",
        "pct_ge_12": "47.44% -> 39.22%  (-8.22 pp)",
        "why": ("acceleration = growth(t) - growth(t-1), and the two growths "
                "share E_t-1. pit_derive.leaves() de-duplicates it; this is "
                "what the de-duplication is worth."),
    },
    "and_the_depth_is_cheap": (
        "going from a level to an acceleration costs 9.2 points of formation "
        "rate at the 3-peer bar (95.2% -> 86.0%), not the collapse the "
        "valuation chain suffers. Filers that report EBITDA at all mostly "
        "report it every year -- which is why growth survives where valuation "
        "does not, and why the 0.25G weight is the one that stays."),
}


#: THE CORRECTION'S PAYLOAD, measured at the BAND -- the statistic that
#: actually IS the benchmark. Produced by `--measure`; see `EVIDENCE`.
#:
#: Two band figures are kept apart on purpose. `band` is what the product sees:
#: `benchmark_band` refuses a vector below 12 and reports a band of 0, so that
#: number conflates "the vector was too thin" with "the band was too thin".
#: `band_gate_lifted` counts the band whenever a percentile is meaningful at
#: all, which is the diagnostic -- it says the 12-in-the-vector rule, not
#: coverage, is what fails for the 12.3 points between 68.3% and 56.0%.
MEASURED_BAND: dict[str, Any] = {
    "n_sets": 39038,
    "min_vector": 12, "min_band": 3,
    "measured_on": "2026-09-21",
    "value_index": {"rows_scanned": 387971, "matched_periods": 54436,
                    "entities": 7697, "seconds": 58.5},
    "corrected_ebitda_only": {
        "scope": FULL_UNIVERSE,
        "vector": {"min": 0, "p10": 5, "p25": 8, "median": 13, "p75": 23,
                   "p90": 70, "max": 718, "mean": 36.04, "pct_ge_3": 95.20,
                   "pct_ge_5": 91.71, "pct_ge_8": 80.16, "pct_ge_12": 56.00},
        "band_gate_lifted": {"min": 0, "p10": 1, "p25": 2, "median": 3,
                             "p75": 6, "p90": 18, "max": 179, "mean": 9.18,
                             "pct_ge_3": 68.34, "pct_ge_5": 35.25,
                             "pct_ge_12": 14.37},
        "band_as_the_product_sees_it": {"median": 3, "mean": 8.29,
                                        "pct_ge_3": 56.00},
        "benchmark_formed_pct": 56.00,
    },
    "uncorrected_price_conditional": {
        "scope": SURVIVOR_ONLY,
        "vector": {"min": 0, "p10": 0, "p25": 0, "median": 1, "p75": 3,
                   "p90": 7, "max": 88, "mean": 3.40, "pct_ge_3": 30.16,
                   "pct_ge_5": 16.02, "pct_ge_8": 9.60, "pct_ge_12": 5.71},
        "band_gate_lifted": {"min": 0, "p25": 0, "median": 1, "p75": 2,
                             "p90": 2, "max": 22, "mean": 1.30,
                             "pct_ge_3": 7.56, "pct_ge_12": 1.10},
        "band_as_the_product_sees_it": {"median": 0, "mean": 0.42,
                                        "pct_ge_3": 5.71},
        "benchmark_formed_pct": 5.71,
        "reconstruction_note": (
            "an UPPER BOUND on the frozen core's cohort. EBITDA > 0 IS applied "
            "(values are in hand); EV > 0 and the [0.1, 300] multiple band are "
            "NOT, because they need prices and share counts as values. The "
            "core's real cohort is therefore no larger than this."),
    },
    "crosscheck_against_the_mask_census": {
        "arm": "the same five primitives WITHOUT the EBITDA>0 screen",
        "measured_here": {"median": 1, "mean": 4.02, "pct_ge_3": 34.19,
                          "pct_ge_12": 7.01},
        "mask_census_N_EVEBITDA": {"median": 1, "mean": 4.0, "pct_ge_3": 34.2,
                                   "pct_ge_12": 7.0},
        "agreement": "0.01 pp on both thresholds and 0.02 on the mean",
        "why_it_matters": ("the two arms of the A/B are two RULES evaluated by "
                          "one function, and the function reproduces an "
                          "independently built census. The delta is therefore "
                          "attributable to the eligibility rule and to nothing "
                          "else."),
    },
    "cost_of_the_positivity_screen": {
        "vector_pct_ge_3": "34.19% -> 30.16%", "vector_pct_ge_12": "7.01% -> 5.71%",
        "note": ("EBITDA > 0 is the core's, not the owner's: the size benchmark "
                 "does not need it, and 37.8% of the universe has EBITDA <= 0."),
    },
    "delta": {
        "vector_median": "1 -> 13",
        "band_gate_lifted_median": "1 -> 3",
        "band_pct_ge_3": "7.56% -> 68.34%",
        "benchmark_formed_pct": "5.71% -> 56.00%  (9.8x)",
        "peer_sets_rescued": 19632,
        "peer_sets_rescued_share_pct": 50.29,
        "share_of_vector_surviving_the_price_filter": {
            "p10": 0.0, "p25": 0.0, "median": 8.5, "p75": 16.7, "p90": 27.3,
            "pct_keeping_none": 29.93},
    },
}


# ==========================================================================
# (8) PER-FACTOR COVERAGE -- pit_derive's report, with the CORRECT eligibility
# ==========================================================================

def factor_coverage(factor_key: str, as_of: str, *,
                    ladder_version: str = LADDER,
                    measured_cohort: Optional[Mapping[str, Any]] = None
                    ) -> dict[str, Any]:
    """Coverage for ONE factor under ITS OWN eligibility rule.

    Two halves, kept apart because they are different kinds of quantity:

      COMPANY   the joint availability of the factor's de-duplicated primitive
                leaves, from `leaf_availability` -- which is
                `pit_derive.leaves` + `pit_derive.independence_joint`, so the
                unique-leaf rule is obeyed by construction.
      COHORT    P(the factor's own cohort reaches min_peer_count). MEASURED,
                from `MEASURED_COHORT`, not modelled: the binomial in
                `pit_derive.cohort_formation_probability` cannot see that
                priced peers CLUSTER, and with the measured per-peer rate it
                still says P(N>=12) = 0.00% where the truth is 4.8-10.3%.

    The difference from `pit_derive.coverage` is the second half. That function
    prices every cohort edge through `_band_estimate()`, which is `ev_ebitda`
    -- faithful to the frozen core, and exactly the contamination. Here the
    gate comes from the factor's OWN rule.
    """
    item = spec(factor_key)
    marginals = pit_derive.primitive_availability(as_of, ladder_version)
    try:
        company = leaf_availability(factor_key, marginals)
    except ValueError as exc:
        company = {"error": str(exc), "joint_independent": None,
                   "leaves": [], "n_leaves": 0, "binding_leaf": None,
                   "frechet_lower": None, "frechet_upper": None}

    # Where a MEASURED joint exists for exactly this leaf set, use it. EBITDA
    # is the one measured joint in the model and the product understates it by
    # 5 points at 2015 -- quoting the model beside a measurement would be a
    # choice to be wrong on purpose.
    day = str(as_of)[:10]
    n_obs = item.peer_eligibility.n_observations
    ebitda_leaves = {("operating_income", lag)
                     for lag in range(n_obs)} | {
                         ("depreciation_amortisation", lag)
                         for lag in range(n_obs)}
    if set(spec_leaves(factor_key)) == ebitda_leaves:
        if n_obs == 1 and day in pit_derive.MEASURED_JOINTS["ebitda"]:
            company["measured_joint"] = pit_derive.MEASURED_JOINTS["ebitda"][day]
        elif day in MEASURED_OBSERVATION_JOINT.get(n_obs, {}):
            company["measured_joint"] = MEASURED_OBSERVATION_JOINT[n_obs][day]
            company["measured_joint_note"] = (
                f"{n_obs} CONSECUTIVE annual EBITDA observations, measured. "
                f"The independence product across lags says "
                f"{100 * pit_derive.MEASURED_JOINTS['ebitda'][day] ** n_obs:.1f}%"
                f" -- it is out by "
                f"{MEASURED_OBSERVATION_JOINT[n_obs][day] / pit_derive.MEASURED_JOINTS['ebitda'][day] ** n_obs:.1f}x "
                f"because a filer that reports EBITDA once reports it yearly.")
    company_point = company.get("measured_joint")
    if company_point is None:
        company_point = company.get("joint_independent")

    table = dict((measured_cohort or MEASURED_COHORT).get(
        item.peer_eligibility.rule_id, {}))
    gate_key = f"pct_ge_{item.min_peer_count}"
    gate = table.get(gate_key)
    gate_fraction = None if gate is None else float(gate) / 100.0
    overall = None
    if gate_fraction is not None and company_point is not None:
        overall = company_point * gate_fraction

    return {
        "factor": factor_key,
        "as_of": str(as_of)[:10],
        "rule_id": item.peer_eligibility.rule_id,
        "price_conditional_terms": list(item.peer_eligibility.price_conditional_terms),
        "sample_scope": item.sample_scope,
        "normalization_type": item.normalization_type,
        "min_peer_count": item.min_peer_count,
        "company": company,
        "company_point": company_point,
        "company_point_basis": ("MEASURED joint" if company.get("measured_joint")
                                is not None else "independence product"),
        "cohort_gate_measured": gate_fraction,
        "cohort_gate_basis": (f"MEASURED {gate_key} over {table.get('n_sets')} "
                              f"peer sets ({table.get('metric')})"
                              if gate_fraction is not None else
                              "UNKNOWN -- this cohort has not been measured"),
        "joint_with_cohort": overall,
        "missingness_behaviour": item.missingness_behaviour,
        "spec_version": FACTOR_SPEC_VERSION,
    }


def _pct(value: Optional[float]) -> str:
    return "  UNKNOWN" if value is None else f"{100.0 * value:7.2f}%"


def factor_coverage_report(as_of: str, *, width: int = 78) -> str:
    """`pit_derive`'s coverage report, REGENERATED per factor with each
    factor's own eligibility rule."""
    out: list[str] = []
    rule = "=" * width
    out.append(rule)
    out.append(f"PER-FACTOR DERIVATION COVERAGE -- {FACTOR_SPEC_VERSION} "
               f"at {str(as_of)[:10]}")
    out.append(rule)
    out.append("Each factor is priced under ITS OWN peer-eligibility rule. The")
    out.append("company half is the joint over the DE-DUPLICATED primitive leaves")
    out.append("(pit_derive.leaves); the cohort half is a MEASURED formation rate,")
    out.append("not a binomial -- the binomial cannot see that priced peers cluster.")
    out.append("A quantity that has not been measured prints UNKNOWN, never 0.")
    out.append("")
    for item in SPECS:
        report = factor_coverage(item.key, as_of)
        terms = report["price_conditional_terms"]
        out.append(f"{item.key} -- {item.question}")
        out.append(f"  rule {report['rule_id']}   [{item.sample_scope}]"
                   + ("   PRICE-CONDITIONAL: " + ", ".join(terms) if terms
                      else "   price-free"))
        if report["company"].get("error"):
            out.append(f"    company leaves: UNKNOWN -- "
                       f"{report['company']['error'].split(': ', 1)[-1][:90]}")
        else:
            for row in report["company"]["leaves"]:
                flag = "   (lag assumed = t marginal)" if row["basis"] != "measured" else ""
                out.append(f"    {row['leaf']:<30}{_pct(row['availability'])}{flag}")
            out.append(f"    {'unique leaves jointly':<30}"
                       f"{_pct(report['company']['joint_independent'])}"
                       f"   [{_pct(report['company']['frechet_lower']).strip()} .. "
                       f"{_pct(report['company']['frechet_upper']).strip()}]"
                       + ("   MEASURED "
                          + _pct(report['company']['measured_joint']).strip()
                          if report['company'].get('measured_joint') is not None
                          else ""))
            if report["company"].get("measured_joint_note"):
                for chunk in _wrap(report["company"]["measured_joint_note"], 68):
                    out.append("      " + chunk)
            out.append(f"    BINDING LEAF: {report['company']['binding_leaf']} "
                       f"({_pct(report['company']['binding_availability']).strip()})")
        out.append(f"    cohort gate  N >= {item.min_peer_count:<3}"
                   f"          {_pct(report['cohort_gate_measured'])}"
                   f"   {report['cohort_gate_basis']}")
        out.append(f"    {'COMPANY x COHORT':<30}"
                   f"{_pct(report['joint_with_cohort'])}"
                   f"   -> {item.missingness_behaviour} when it fails")
        out.append("")
    return "\n".join(out)


def ebitda_benchmark_before_after() -> str:
    """The one table the owner asked for: the EBITDA benchmark, before/after.

    BEFORE is `pit_derive.coverage('cohort_mean_ebitda', ...)`, which prices
    the cohort edge through `ev_ebitda` because that is what the frozen core
    does. AFTER is the same node under EBITDA_ONLY. Both halves are MEASURED
    where a measurement exists.
    """
    out: list[str] = []
    out.append("=" * 78)
    out.append("THE EBITDA BENCHMARK, BEFORE AND AFTER THE CORRECTION")
    out.append("=" * 78)
    out.append("BEFORE: pit_derive.coverage('cohort_mean_ebitda'), whose cohort edge")
    out.append("        is priced through ev_ebitda -- faithful to the frozen core.")
    out.append("AFTER : the same benchmark under EBITDA_ONLY, cohort gate MEASURED.")
    out.append("")
    for day in pit_derive.MEASURED_DATES:
        marginals = pit_derive.primitive_availability(day, LADDER)
        before = pit_derive.coverage(
            "cohort_mean_ebitda", marginals,
            measured_joints={"ebitda": pit_derive.MEASURED_JOINTS["ebitda"][day]},
            lift=pit_derive.co_occurrence_lift(day))
        after = factor_coverage("ebitda_benchmark", day)
        out.append(f"  {day}")
        out.append(f"    BEFORE  modelled joint {_pct(before['joint_independent'])}"
                   f"   binding leaf: {before['binding_leaf']}")
        out.append(f"    AFTER   company joint  "
                   f"{_pct(after['company_point'])}"
                   f"   ({after['company_point_basis']}; binding leaf "
                   f"{after['company']['binding_leaf']})")
        out.append(f"            cohort gate    "
                   f"{_pct(after['cohort_gate_measured'])}   "
                   f"{after['cohort_gate_basis']}")
        out.append(f"            COMPANY x COHORT "
                   f"{_pct(after['joint_with_cohort'])}")
    out.append("")
    out.append("  MEASURED COHORT FORMATION, all 39,038 peer sets:")
    corrected = MEASURED_COHORT["ebitda_only_v1"]
    current = MEASURED_COHORT["ev_ebitda_six_primitives_v1"]
    out.append(f"    {'rule':<34}{'median':>8}{'>=3':>9}{'>=12':>9}")
    out.append(f"    {'ev_ebitda_six (what the core uses)':<34}"
               f"{current['median']:>8}{current['pct_ge_3']:>8.1f}%"
               f"{current['pct_ge_12']:>8.1f}%")
    out.append(f"    {'ebitda_only (the owners rule)':<34}"
               f"{corrected['median']:>8}{corrected['pct_ge_3']:>8.1f}%"
               f"{corrected['pct_ge_12']:>8.1f}%")
    out.append("")
    band = MEASURED_BAND
    unc, cor = (band["uncorrected_price_conditional"],
                band["corrected_ebitda_only"])
    out.append("  MEASURED AT THE BAND -- the statistic that IS the benchmark.")
    out.append("  'band' is counted with the VECTOR GATE LIFTED, so the two")
    out.append("  thresholds are separable; 'benchmark' is vector >= 12 AND")
    out.append("  band >= 3, which is what the product actually needs.")
    out.append(f"    {'rule':<34}{'vector':>8}{'band':>8}{'band>=3':>10}"
               f"{'benchmark':>11}")
    out.append(f"    {'price-conditional (UNCORRECTED)':<34}"
               f"{unc['vector']['median']:>8}"
               f"{unc['band_gate_lifted']['median']:>8}"
               f"{unc['band_gate_lifted']['pct_ge_3']:>9.1f}%"
               f"{unc['benchmark_formed_pct']:>10.2f}%")
    out.append(f"    {'EBITDA-only (CORRECTED)':<34}"
               f"{cor['vector']['median']:>8}"
               f"{cor['band_gate_lifted']['median']:>8}"
               f"{cor['band_gate_lifted']['pct_ge_3']:>9.1f}%"
               f"{cor['benchmark_formed_pct']:>10.2f}%")
    delta = band["delta"]
    out.append(f"    peer sets that gain a benchmark: "
               f"{delta['peer_sets_rescued']:,} of {band['n_sets']:,} "
               f"({delta['peer_sets_rescued_share_pct']}%)")
    survive = delta["share_of_vector_surviving_the_price_filter"]
    out.append(f"    share of the EBITDA vector surviving the price filter: "
               f"p25 {survive['p25']}%  median {survive['median']}%  "
               f"p75 {survive['p75']}%; {survive['pct_keeping_none']}% keep NONE")
    check = band["crosscheck_against_the_mask_census"]
    out.append(f"    reconstruction cross-check vs the independent mask census: "
               f"{check['agreement']}")
    out.append(f"    {unc['reconstruction_note']}")
    out.append("")
    return "\n".join(out)


# ==========================================================================
# (9) VALIDATION -- the guard that makes the correction permanent
# ==========================================================================

def validate() -> list[str]:
    """Every problem with the seeded specs, as plain sentences.

    Run by the tests and by `main`, never at import: a module that refuses to
    import cannot say why, and this list is the diagnostic.
    """
    problems: list[str] = []
    seen_rules: dict[str, PeerEligibility] = {}

    for item in SPECS:
        # (a) all six declared fields present and non-empty.
        for name in REQUIRED_FIELDS:
            value = getattr(item, name, None)
            if value is None or (hasattr(value, "__len__") and not len(value)):
                problems.append(f"{item.key}: required field {name!r} is not declared")
        if item.min_peer_count < 1:
            problems.append(f"{item.key}: min_peer_count must be at least 1")
        if item.normalization_type not in pit_normalization.NORMALIZATION_TYPES:
            problems.append(f"{item.key}: normalization_type "
                            f"{item.normalization_type!r} is not in "
                            f"pit_normalization.NORMALIZATION_TYPES")
        if item.missingness_behaviour not in MISSINGNESS_VOCABULARY:
            problems.append(f"{item.key}: missingness_behaviour "
                            f"{item.missingness_behaviour!r} is not a declared "
                            "vocabulary term")
        if tuple(item.fallback_rung) != RUNG_LADDER:
            unknown = [r for r in item.fallback_rung if r not in RUNG_LADDER]
            if unknown:
                problems.append(f"{item.key}: fallback rung(s) {unknown} are not "
                                f"on the SIC ladder {RUNG_LADDER}")
        if item.sample_scope not in (SURVIVOR_ONLY, FULL_UNIVERSE):
            problems.append(f"{item.key}: sample scope {item.sample_scope!r} is "
                            "not a declared vocabulary term")
        if item.min_band_members and item.min_peer_count < 4 * item.min_band_members:
            problems.append(
                f"{item.key}: a 50-75 BAND of {item.min_band_members} needs "
                f"{4 * item.min_band_members} in the vector; min_peer_count is "
                f"{item.min_peer_count}")

        # (b) THE CORRECTION. A fundamental operating cohort may not name a
        # price-, share-, debt- or cash-dependent term anywhere in its rule.
        if item.key in FUNDAMENTAL_OPERATING_COHORTS:
            terms = item.peer_eligibility.price_conditional_terms
            if terms:
                problems.append(
                    f"{item.key}: a FUNDAMENTAL OPERATING cohort's eligibility "
                    f"rule names price-conditional term(s) {list(terms)} -- this "
                    "is exactly the defect the spec exists to prevent "
                    "(SHAFFER_V1_KNOWN_LIMITATION)")
            leaked = sorted(PRICE_CONDITIONAL & set(item.required_primitives))
            if leaked:
                problems.append(
                    f"{item.key}: a FUNDAMENTAL OPERATING factor requires "
                    f"price-conditional primitives {leaked}")

        # (c) a survivor-only label must be earned by an actual market primitive.
        market = MARKET_CONDITIONAL & set(item.peer_eligibility.primitives)
        needs_market = bool(market) or item.peer_eligibility.requires_price
        if needs_market and item.sample_scope != SURVIVOR_ONLY:
            problems.append(f"{item.key}: needs {sorted(market) or 'a price'} but "
                            f"is not labelled {SURVIVOR_ONLY}")
        if item.sample_scope == SURVIVOR_ONLY and not needs_market:
            problems.append(f"{item.key}: labelled {SURVIVOR_ONLY} but needs no "
                            "market primitive")

        # (d) the eligibility rule may not ask for what the factor does not need.
        extra = sorted(set(item.peer_eligibility.primitives)
                       - set(item.required_primitives))
        if extra:
            problems.append(f"{item.key}: eligibility requires {extra}, which the "
                            "factor does not declare as a required primitive")

        # (e) THE UNIQUE-LEAF RULE. The spec's declared leaf set must BE the
        # derivation graph's de-duplicated leaf set for the quantity each peer
        # contributes -- so a hand-written primitive list cannot name an
        # intermediate, miss an observation, or invent one.
        if item.member_quantity not in pit_derive.NODES:
            problems.append(f"{item.key}: member_quantity "
                            f"{item.member_quantity!r} is not a registered "
                            "pit_derive node")
        elif item.graph_node not in pit_derive.NODES:
            problems.append(f"{item.key}: graph_node {item.graph_node!r} is not "
                            "a registered pit_derive node")
        else:
            graph_set = {(ref.key, ref.lag) for ref in unique_leaves(item.key)
                         if not ref.per_member}
            declared = set(spec_leaves(item.key))
            if declared != graph_set and not item.graph_divergence:
                problems.append(
                    f"{item.key}: declared leaves {sorted(declared)} are not "
                    f"the de-duplicated leaves of {item.member_quantity!r} "
                    f"{sorted(graph_set)}, and no graph_divergence is declared")
            if declared == graph_set and item.graph_divergence and                     "per PEER" not in item.graph_divergence and                     "PER PEER" not in item.graph_divergence and                     "sector_ebitda_p50" not in item.graph_divergence:
                problems.append(
                    f"{item.key}: declares a graph_divergence but its leaves "
                    "agree with the graph -- a stale claim is worse than none")

            # (e2) THE CORRECTION, checked against the graph itself. A cohort
            # statistic's per-member leaf in pit_derive is what the frozen core
            # actually consumes. Where that differs from the spec's member
            # quantity, the difference must be DECLARED.
            per_member_all = graph_per_member_leaves(item.graph_node)
            per_member = per_member_all[0] if per_member_all else None
            if per_member is not None and per_member != item.member_quantity:
                if not item.graph_divergence:
                    problems.append(
                        f"{item.key}: pit_derive's {item.graph_node!r} consumes "
                        f"{per_member!r} per cohort member while this spec's "
                        f"member quantity is {item.member_quantity!r}, and the "
                        "divergence is not declared")
                elif (item.key in FUNDAMENTAL_OPERATING_COHORTS
                      and per_member in ("ev_ebitda", "market_cap",
                                         "enterprise_value")):
                    if SHAFFER_V1_KNOWN_LIMITATION not in item.graph_divergence:
                        problems.append(
                            f"{item.key}: a fundamental cohort whose graph edge "
                            f"is {per_member!r} must record "
                            f"{SHAFFER_V1_KNOWN_LIMITATION}")

        # (f) one rule id, one rule.
        prior = seen_rules.setdefault(item.peer_eligibility.rule_id,
                                      item.peer_eligibility)
        if prior is not item.peer_eligibility and prior != item.peer_eligibility:
            problems.append(f"{item.key}: rule id "
                            f"{item.peer_eligibility.rule_id!r} is used for two "
                            "different rules")

    # (g) THE UNIQUE-LEAF RULE, at its hardest case.
    n_obs = ebitda_observations("ebitda_acceleration")
    if n_obs != 3:
        problems.append(f"ebitda_acceleration resolves to {n_obs} EBITDA "
                        "observations; it must be THREE (E_t, E_t-1, E_t-2) -- "
                        "the two growth terms share the middle one")
    if spec("ebitda_acceleration").peer_eligibility.n_observations != 3:
        problems.append("the acceleration eligibility rule must declare three "
                        "observation lags")

    # (h2) a multi-observation rule must name ONE coherent group of concepts.
    for item in SPECS:
        rule = item.peer_eligibility
        if rule.n_observations > 1:
            fundamentals = {p for p in rule.primitives
                            if p not in MARKET_CONDITIONAL and p != "cpi"}
            matched = set(rule.matched_period)
            if matched and fundamentals - matched:
                problems.append(
                    f"{item.key}: a {rule.n_observations}-observation rule "
                    f"mixes a matched group {sorted(matched)} with "
                    f"{sorted(fundamentals - matched)}; whether those must "
                    "share periods is undecided, so the rule is not evaluable")

    # (i) two factors over one peer set must be able to differ.
    if (spec("ebitda_benchmark").peer_eligibility
            is spec("ev_ebitda").peer_eligibility):
        problems.append("ebitda_benchmark and ev_ebitda share an eligibility "
                        "rule; they must not")

    # (j) THE TWO-TEST GATE. Checked on EVERY spec version, because the whole
    # point of the second test is that it cannot be skipped: a future edit that
    # lets a valuation factor reach the office rung fails HERE, not in a review.
    unknown = sorted(VALUATION_FACTORS - set(BY_KEY))
    if unknown:
        problems.append(f"VALUATION_FACTORS names unknown factor(s) {unknown}")
    if VALUATION_MAX_RUNG not in RUNG_RANK:
        problems.append(f"VALUATION_MAX_RUNG {VALUATION_MAX_RUNG!r} is not on "
                        f"the ladder {RUNG_LADDER}")
    if RUNG_RANK.get(VALUATION_MAX_RUNG, -1) >= RUNG_RANK[RUNG_OFFICE]:
        problems.append(
            f"VALUATION_MAX_RUNG {VALUATION_MAX_RUNG!r} does not exclude the "
            f"office rung; the owner's ruling is that the Office-level "
            f"fallback may NOT be a company valuation peer group")

    for version in spec_versions():
        registry = spec_set(version)
        if set(registry) != set(BY_KEY):
            problems.append(f"{version}: declares a different factor set from "
                            f"{FACTOR_SPEC_VERSION}")
        for key in sorted(registry):
            item = registry[key]
            ceiling = item.max_admissible_rung
            if ceiling and ceiling not in RUNG_RANK:
                problems.append(f"{version}/{key}: max_admissible_rung "
                                f"{ceiling!r} is not on the ladder {RUNG_LADDER}")
                continue
            if ceiling and not item.admissible_rungs:
                problems.append(f"{version}/{key}: the rung ceiling {ceiling!r} "
                                "leaves no admissible rung at all")
            if item.admissible_rungs != tuple(
                    item.fallback_rung[:len(item.admissible_rungs)]):
                problems.append(
                    f"{version}/{key}: admissible rungs "
                    f"{list(item.admissible_rungs)} are not a PREFIX of the "
                    f"fallback ladder {list(item.fallback_rung)} -- a ceiling "
                    "may only truncate the widening order, never reorder it")

            if version == FACTOR_SPEC_VERSION and ceiling:
                problems.append(
                    f"{FACTOR_SPEC_VERSION}/{key}: declares a rung ceiling. v1 "
                    "is FROZEN and declares none by construction; the two-test "
                    f"gate belongs to {FACTOR_SPEC_VERSION_V2}. "
                    + FACTOR_SPEC_V1_KNOWN_LIMITATION)

            if version != FACTOR_SPEC_VERSION and key in VALUATION_FACTORS:
                if item.rung_admissible(RUNG_OFFICE):
                    problems.append(
                        f"{version}/{key}: a VALUATION factor admits the "
                        f"{RUNG_OFFICE!r} rung. The office fallback may serve "
                        "broad descriptive statistics and may NOT be a company "
                        "valuation peer group -- the honest answer is "
                        + VALUATION_PEER_SET_INSUFFICIENT)
                if not ceiling:
                    problems.append(
                        f"{version}/{key}: a VALUATION factor declares no "
                        "max_admissible_rung; both tests are required, not one")
                if refusal_reason(key, version) != VALUATION_PEER_SET_INSUFFICIENT:
                    problems.append(
                        f"{version}/{key}: a refused valuation cohort must "
                        f"carry {VALUATION_PEER_SET_INSUFFICIENT}")

            if version != FACTOR_SPEC_VERSION and key not in VALUATION_FACTORS:
                # The control. A gate that refused everything would be worthless.
                if not item.rung_admissible(RUNG_OFFICE):
                    problems.append(
                        f"{version}/{key}: a NON-valuation factor lost the "
                        f"{RUNG_OFFICE!r} rung. The ban is on valuation peer "
                        "groups, not on the rung")
                if (registry[key] is not BY_KEY[key]
                        and key not in REPLACED_BY_VERSION.get(version, ())):
                    problems.append(
                        f"{version}/{key}: is a COPY of the v1 spec rather than "
                        "the same object; the two versions would drift apart")

    # (i2) A DECLARED REPLACEMENT MUST ACTUALLY DIFFER, in the direction the
    # owner decided. Otherwise the exemption above is a loophole.
    v3 = BY_KEY_V3["ebitda_benchmark"]
    if "revenue" not in v3.required_primitives:
        problems.append("factor_spec_v3/ebitda_benchmark must require revenue: "
                        "the scored quantity is a MARGIN")
    if v3.peer_eligibility is not EBITDA_AND_REVENUE:
        problems.append("factor_spec_v3/ebitda_benchmark eligibility must be "
                        "EBITDA_AND_REVENUE by reference")
    if v3.member_quantity != "ebitda":
        problems.append("factor_spec_v3/ebitda_benchmark cohort SELECTION must "
                        "still be by EBITDA dollars -- that is Shaffer's identity")
    if BY_KEY_V2["ebitda_benchmark"].peer_eligibility is not EBITDA_ONLY:
        problems.append("factor_spec_v2/ebitda_benchmark must be PRESERVED "
                        "verbatim -- a frozen set is never edited")
    if BY_KEY_V3["pe_ratio"].peer_eligibility.unavailable_primitives != ():
        problems.append("factor_spec_v3/pe_ratio must not declare EPS unavailable")
    if BY_KEY_V2["pe_ratio"].peer_eligibility.unavailable_primitives != (
            "earnings_per_share_pit",):
        problems.append("factor_spec_v2/pe_ratio's stale declaration must be "
                        "PRESERVED, not repaired in place")

    # (j1) COHERENCE IS A NUMBER, and the number must be one the data kept.
    # A statistic may be quoted as coherence only if it survived the
    # size-matched control, and every statistic that did must be quoted --
    # otherwise the declaration and the evidence drift apart silently.
    for name, record in COHERENCE_MEASURES.items():
        verdict = str(record.get("verdict", ""))
        if not verdict:
            problems.append(f"coherence candidate {name!r} carries no verdict")
        adopted = verdict.startswith("ADOPTED")
        if adopted and name not in COHERENCE_ADOPTED:
            problems.append(f"{name!r} is marked ADOPTED but is missing from "
                            "COHERENCE_ADOPTED")
        if not adopted and name in COHERENCE_ADOPTED:
            problems.append(f"{name!r} is in COHERENCE_ADOPTED while its "
                            f"verdict says {verdict[:40]!r}")
        if name not in COHERENCE_MEASURED.get("median_by_rung", {}):
            problems.append(f"coherence candidate {name!r} has no measured "
                            "value per rung; a rejection needs evidence too")
    for name in COHERENCE_ADOPTED:
        if name not in COHERENCE_MEASURES:
            problems.append(f"COHERENCE_ADOPTED names {name!r}, which is not a "
                            "declared candidate")
    if not COHERENCE_ADOPTED:
        problems.append("no coherence statistic is adopted, so the second test "
                        "has no number behind it")

    # (j2) the gate must actually refuse, on both tests, for a valuation factor.
    office = peer_set_gate("ev_ebitda", RUNG_OFFICE, 900,
                           version=FACTOR_SPEC_VERSION_V2)
    if office.ok or office.reason != VALUATION_PEER_SET_INSUFFICIENT:
        problems.append("a 900-member office cohort is not refused for "
                        "ev_ebitda under " + FACTOR_SPEC_VERSION_V2)
    if TEST_ECONOMIC_COHERENCE not in office.failed_tests or not office.sufficient_n:
        problems.append("the office refusal must fail ECONOMIC COHERENCE while "
                        "PASSING sufficient N -- that separation is the finding")
    narrow = peer_set_gate("ev_ebitda", "sic4", 12,
                           version=FACTOR_SPEC_VERSION_V2)
    if not narrow.ok:
        problems.append("a 12-member sic4 cohort must PASS both tests for "
                        "ev_ebitda; the gate refuses too much")
    return problems


def render(version: Optional[str] = None) -> str:
    """The specs as a table a person reads and argues with.

    `version` DEFAULTS TO v1, like every other selector here; pass
    `FACTOR_SPEC_VERSION_V2` to see the rung ceilings.
    """
    name = version or DEFAULT_SPEC_VERSION
    registry = spec_set(name)
    lines = [f"FACTOR SPECS -- {name} for {CANDIDATE_MODEL_VERSION}",
             f"evidence: {EVIDENCE}", ""]
    for item in [registry[k.key] for k in SPECS]:
        terms = item.peer_eligibility.price_conditional_terms
        lines.append(f"{item.key}  [{item.sample_scope}]"
                     + ("  PRICE-CONDITIONAL" if terms else "  price-free"))
        lines.append(f"  question       {item.question}")
        lines.append(f"  primitives     {', '.join(item.required_primitives)}"
                     f"   ({len(item.required_primitives)} unique leaves x "
                     f"{item.peer_eligibility.n_observations} observation"
                     f"{'s' if item.peer_eligibility.n_observations > 1 else ''}"
                     f" = {len(spec_leaves(item.key))} leaves)")
        lines.append(f"  eligibility    {item.peer_eligibility.rule_id}")
        for chunk in _wrap(item.peer_eligibility.why, 62):
            lines.append(f"                 {chunk}")
        lines.append(f"  normalization  {item.normalization_type}")
        lines.append(f"  min peers      {item.min_peer_count}"
                     + (f" (band {item.min_band_members})"
                        if item.min_band_members else ""))
        lines.append(f"  fallback rung  {' -> '.join(item.fallback_rung)}")
        if item.max_admissible_rung:
            lines.append(f"  RUNG CEILING   {item.max_admissible_rung}"
                         f"   (admissible {', '.join(item.admissible_rungs)};"
                         f" FORBIDDEN {', '.join(item.forbidden_rungs)})")
        lines.append(f"  missingness    {item.missingness_behaviour}")
        if item.graph_divergence:
            for index, chunk in enumerate(_wrap(item.graph_divergence, 62)):
                lines.append(("  graph diverges " if index == 0
                              else "                 ") + chunk)
        if item.note:
            for index, chunk in enumerate(_wrap(item.note, 62)):
                lines.append(("  note           " if index == 0
                              else "                 ") + chunk)
        lines.append("")
    return "\n".join(lines)


def render_gate() -> str:
    """THE TWO-TEST GATE as a table: what each factor needs, and what it may
    not use. The line a reader should leave with is the last one -- feasibility
    and meaningfulness move in opposite directions along the ladder."""
    lines = [f"THE TWO-TEST PEER GATE -- {FACTOR_SPEC_VERSION_V2}",
             "",
             f"  {'factor':<22s}{'min N':>7s}{'ceiling':>10s}"
             f"{'forbidden':>12s}   refusal",
             "  " + "-" * 74]
    for item in SPECS_V2:
        gate_reason = refusal_reason(item.key, FACTOR_SPEC_VERSION_V2)
        lines.append(
            f"  {item.key:<22s}{item.min_peer_count:>7d}"
            f"{(item.max_admissible_rung or '-'):>10s}"
            f"{(', '.join(item.forbidden_rungs) or '-'):>12s}   {gate_reason}")
    lines.append("")
    lines.append("  COHERENCE, MEASURED (pit_peer_coherence.py, 39,038 peer sets)")
    lines.append(f"  {'statistic':<24s}{'better':>8s}{'sic4':>9s}{'sic3':>9s}"
                 f"{'sic2':>9s}{'office':>9s}   verdict")
    medians = COHERENCE_MEASURED.get("median_by_rung", {})
    for name, record in COHERENCE_MEASURES.items():
        row = medians.get(name, {})
        lines.append(
            f"  {name:<24s}{record['better']:>8s}"
            + "".join(f"{row.get(r, float('nan')):>9.2f}"
                      for r in ("sic4", "sic3", "sic2", "office"))
            + f"   {str(record.get('verdict', ''))[:8]}")
    control = COHERENCE_MEASURED.get("size_matched_control", {})
    lines.append("")
    lines.append(f"  SIZE-MATCHED CONTROL ({control.get('bucket', '')})")
    for name in COHERENCE_MEASURES:
        row = control.get(name)
        if not row:
            continue
        lines.append(f"    {name:<24s} office {row['office']:>8.3f}   "
                     f"sic2 {row['sic2']:>8.3f}   office worse: "
                     f"{row['office_worse']}")
    cost = COHERENCE_MEASURED.get("cost_of_the_ban", {})
    if cost:
        lines.append("")
        lines.append("  THE COST OF THE BAN")
        lines.append(f"    entity-dates                 "
                     f"{cost['n_entity_dates']:>10,}")
        lines.append(f"    lost, no admissible peer set {cost['lost_no_admissible_peer_set']:>10,}")
        lines.append(f"    lost, admissible set too thin{cost['lost_admissible_set_too_thin']:>10,}")
        lines.append(f"    LOST TOTAL                   {cost['lost_total']:>10,}"
                     f"   ({cost['lost_pct_of_entity_dates']}%)")
    return "\n".join(lines)


def _wrap(text: str, width: int) -> list[str]:
    out: list[str] = []
    line = ""
    for word in str(text).split():
        if line and len(line) + 1 + len(word) > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out


# ==========================================================================
# (10) THE MEASUREMENT: what the correction does to cohort sizes
# ==========================================================================

_EPOCH = _dt.date(2000, 1, 1).toordinal()
THRESHOLDS = (3, 5, 8, 12)
PERCENTILE_POINTS = (10, 25, 50, 75, 90)


def _ord(day: str) -> int:
    return _dt.date.fromisoformat(str(day)[:10]).toordinal()


def _distribution(values: Sequence[int]) -> dict[str, Any]:
    """Nearest-rank percentiles of a COUNT distribution. Nearest rank and not
    interpolation: a median of 23.5 peers is a cohort nobody ever formed."""
    if not values:
        return {}
    ordered = sorted(values)
    n = len(ordered)
    out: dict[str, Any] = {"n_sets": n, "min": ordered[0], "max": ordered[-1]}
    for point in PERCENTILE_POINTS:
        rank = max(1, min(n, -(-point * n // 100)))
        out[f"p{point}"] = ordered[rank - 1]
    out["median"] = out["p50"]
    out["mean"] = round(sum(ordered) / n, 2)
    for k in THRESHOLDS:
        out[f"pct_ge_{k}"] = round(100.0 * sum(1 for v in ordered if v >= k) / n, 2)
    return out


def _band_size(values: Mapping[int, float]) -> int:
    """Members of the 50-75 band with the VECTOR gate lifted.

    `benchmark_band` refuses below `min_vector` and reports a band of 0, which
    is the right production behaviour and the wrong diagnostic: it conflates
    "the vector was too thin" with "the band was too thin". This counts the
    band whenever a percentile is meaningful at all, so the two gates can be
    attributed separately. Same arithmetic -- `statlib.percentile` -- so the
    two counts are directly comparable.
    """
    if len(values) < 3:
        return len(values)
    ordered = sorted(float(v) for v in values.values())
    p50 = statlib.percentile(ordered, 0.50)
    p75 = statlib.percentile(ordered, 0.75)
    return sum(1 for v in values.values() if p50 <= float(v) <= p75)


def ebitda_value_index(conn: sqlite3.Connection, grid: Sequence[str],
                       progress: bool = True) -> dict[str, Any]:
    """Point-in-time EBITDA VALUES per (entity, as-of date).

    Counts are not enough for this measurement: the 50-75 band is defined by
    PERCENTILES OF THE VALUES, so the band cannot be measured from an
    availability mask. One pass over `pit_fact` for the two assembly concepts
    builds, per (entity, period_end), the vintages of each component; each grid
    date then selects the latest period whose two components are both available
    and non-stale, at the latest vintage knowable on that date -- which is
    exactly what `pit_store.fact_as_of` does, done once instead of 1.2 million
    times.

    D&A follows its ladder ORDER: the first rung with a vintage available on
    the date wins, so the value is the one the replay would have read.

    unit='USD' is enforced. 1,381,061 of 14,072,934 fact rows are non-USD or
    'shares', and a JPY EBITDA inside a USD sector percentile is a 150x error.
    """
    started = time.time()
    oi_tags = [t for rung in pit_policy.ladder_for("operating_income", LADDER).rungs
               for t in rung.tags]
    da_rungs = pit_policy.ladder_for("depreciation_amortisation", LADDER).rungs
    da_rank = {t: i for i, rung in enumerate(da_rungs) for t in rung.tags}
    tags = list(dict.fromkeys(oi_tags + list(da_rank)))
    oi_set = set(oi_tags)

    first_ord, last_ord = _ord(grid[0]), _ord(grid[-1])
    # (entity, period_ord) -> {'oi': [(avail,val)], 'da': [(rank,avail,val)]}
    keyed: dict[tuple[int, int], dict[str, list]] = {}
    marks = ", ".join("?" * len(tags))
    n_rows = 0
    for entity_id, tag, period_end, available, val in conn.execute(
            f"""SELECT entity_id, tag, period_end, available_date, val
                  FROM pit_fact
                 WHERE segments = '' AND coreg = '' AND unit = 'USD'
                   AND qtrs = ? AND tag IN ({marks})""",
            [ANNUAL_QTRS] + tags):
        n_rows += 1
        try:
            avail_o, pe_o = _ord(available), _ord(period_end)
        except (ValueError, TypeError):
            continue
        if avail_o > last_ord:
            continue
        key = (int(entity_id), pe_o)
        slot = keyed.get(key)
        if slot is None:
            slot = keyed[key] = {"oi": [], "da": []}
        if tag in oi_set:
            slot["oi"].append((avail_o, float(val)))
        else:
            slot["da"].append((da_rank[tag], avail_o, float(val)))
        if progress and n_rows % 1_000_000 == 0:
            print(f"    ... {n_rows:,} rows  {time.time() - started:.0f}s", flush=True)

    # Keep only matched keys, and index them per entity, newest period first.
    per_entity: dict[int, list[tuple[int, int, list, list]]] = {}
    n_matched = 0
    for (entity_id, pe_o), slot in keyed.items():
        if not slot["oi"] or not slot["da"]:
            continue
        deadline = _deadline_ord(pe_o, ANNUAL_QTRS, "operating_income")
        if deadline < first_ord:
            continue
        n_matched += 1
        per_entity.setdefault(entity_id, []).append(
            (pe_o, deadline, sorted(slot["oi"]), sorted(slot["da"])))
    for rows in per_entity.values():
        rows.sort(key=lambda r: -r[0])
    keyed.clear()

    low, high = ANNUAL_SPACING_DAYS
    values: dict[str, dict[int, float]] = {}
    observations: dict[str, dict[int, int]] = {}
    for position, day in enumerate(grid):
        day_ord = _ord(day)
        table: dict[int, float] = {}
        counts: dict[int, int] = {}
        for entity_id, rows in per_entity.items():
            # Periods whose BOTH components had landed by this date, newest
            # first. `rows` is already sorted by period end descending.
            landed: list[int] = []
            level: Optional[float] = None
            for pe_o, deadline, oi, da in rows:
                if pe_o > day_ord:
                    continue
                oi_val = None
                for avail_o, v in oi:
                    if avail_o <= day_ord:
                        oi_val = v
                    else:
                        break
                if oi_val is None:
                    continue
                best_rank, da_val = None, None
                for rank, avail_o, v in da:
                    if avail_o > day_ord:
                        continue
                    if best_rank is None or rank < best_rank:
                        best_rank, da_val = rank, v
                    elif rank == best_rank:
                        da_val = v                 # later vintage of same rung
                if da_val is None:
                    continue
                if level is None and deadline >= day_ord:
                    level = oi_val + da_val        # the latest NON-STALE level
                landed.append(pe_o)
            if level is None:
                continue                           # no usable level today
            table[entity_id] = level
            # Consecutive annual observations, counted from the newest
            # non-stale period backwards. A gap outside the spacing window is
            # a MISSING YEAR and ends the run -- a growth rate computed across
            # it would be a two-year rate wearing a one-year label.
            run, previous = 0, None
            for pe_o in landed:
                if previous is None:
                    run, previous = 1, pe_o
                    continue
                gap = previous - pe_o
                if gap < low:
                    continue
                if gap > high:
                    break
                run += 1
                previous = pe_o
            counts[entity_id] = run
        values[day] = table
        observations[day] = counts
        if progress and (position % 40 == 0 or position == len(grid) - 1):
            print(f"    {day}: {len(table):,} entities with EBITDA, "
                  f"{sum(1 for v in counts.values() if v >= 3):,} with three "
                  f"observations  {time.time() - started:.0f}s", flush=True)
    return {"values": values, "observations": observations,
            "n_rows_scanned": n_rows, "n_matched_periods": n_matched,
            "n_entities": len(per_entity),
            "seconds": round(time.time() - started, 1)}


def _deadline_ord(period_end_ord: int, qtrs: int, concept: str) -> int:
    """`period_end + max_age_months`, the last as-of date `is_stale` allows."""
    end = _dt.date.fromordinal(period_end_ord)
    total = end.month - 1 + pit_policy.max_age_months(qtrs, concept)
    year, month = end.year + total // 12, total % 12 + 1
    last = 31 if month == 12 else (
        _dt.date(year, month + 1, 1) - _dt.timedelta(days=1)).day
    return _dt.date(year, month, min(end.day, last)).toordinal()


def measure_band_delta(conn: sqlite3.Connection, masks_dir: str,
                       progress: bool = True) -> dict[str, Any]:
    """THE A/B: what the correction does to cohort sizes and to benchmark
    availability, over every peer set in the store.

    Both arms call `benchmark_band`, so the percentile arithmetic is identical
    and the measured delta is attributable to the ELIGIBILITY RULE and nothing
    else. The uncorrected arm is an UPPER BOUND on the frozen core's cohort:
    EV > 0 and the [0.1, 300] multiple band need prices as values and are not
    applied. EBITDA > 0 IS applied to it, because EBITDA values are in hand.
    """
    with open(os.path.join(masks_dir, "fund_masks_annual.json"),
              encoding="utf-8") as handle:
        masks_payload = json.load(handle)
    masks = {k: {int(e): int(v, 16) for e, v in t.items()}
             for k, t in masks_payload["masks"].items()}
    dates = masks_payload["dates"]
    with open(os.path.join(masks_dir, "price_shares.json"),
              encoding="utf-8") as handle:
        price_payload = json.load(handle)

    index = ebitda_value_index(conn, dates, progress=progress)
    values = index["values"]
    observations = index["observations"]
    item = spec("ebitda_benchmark")
    min_vector, min_band = item.min_peer_count, item.min_band_members

    sets = conn.execute(
        """SELECT peer_set_id, as_of_date, rung, key FROM pit_peer_set
            WHERE model_version = ? AND peer_set_version = ?
            ORDER BY as_of_date, peer_set_id""",
        (pit_store.EQUITY_PIT_MODEL_VERSION, pit_store.PEER_SET_VERSION)).fetchall()
    by_date: dict[str, list[Any]] = {}
    for row in sets:
        by_date.setdefault(row["as_of_date"], []).append(row)

    records: list[dict[str, Any]] = []
    kept_share: list[float] = []
    #: COMPANY-LEVEL multi-observation marginals, accumulated in the main loop.
    #: The independence product across lags is a floor and a bad one -- a filer
    #: that reported EBITDA this year almost certainly reported it last year --
    #: so the multi-observation joint is MEASURED rather than multiplied. Base
    #: is the peer universe with a usable non-stale Assets fact: the same
    #: denominator every other marginal in this project uses.
    census: dict[str, dict[str, int]] = {}

    for day, rows in sorted(by_date.items()):
        bit = dates.index(day)
        day_values = values.get(day, {})
        day_obs = observations.get(day, {})
        record = price_payload["per_date"].get(day) or {}
        defensible = set(record.get("defensible", ()))
        ids = [int(r["peer_set_id"]) for r in rows]
        lo, hi = min(ids), max(ids)
        wanted = set(ids)
        members: dict[int, list[int]] = {i: [] for i in ids}
        for peer_set_id, entity_id in conn.execute(
                "SELECT peer_set_id, entity_id FROM pit_peer_member "
                "WHERE peer_set_id BETWEEN ? AND ?", (lo, hi)):
            if peer_set_id in wanted:
                members[peer_set_id].append(entity_id)

        def has(mask: str, entity_id: int) -> bool:
            return bool((masks[mask].get(entity_id, 0) >> bit) & 1)

        if day in pit_derive.MEASURED_DATES:
            universe = {e for ids in members.values() for e in ids}
            base = {e for e in universe if has("total_assets", e)}
            row = {"base": len(base), "peer_universe": len(universe)}
            for k in (1, 2, 3, 4):
                row[f"ge_{k}_observations"] = sum(
                    1 for e in base if day_obs.get(e, 0) >= k)
            census[day] = row

        for row in rows:
            cohort = members[int(row["peer_set_id"])]
            corrected = {e: day_values[e] for e in cohort if e in day_values}
            # The price-conditional arm, reconstructed. AVAILABILITY first, so
            # it can be cross-checked against the independent mask census, and
            # then the one value screen that IS computable here, EBITDA > 0.
            priced_avail = {e: v for e, v in corrected.items()
                            if e in defensible and has("cash", e)
                            and has("total_debt", e)}
            uncorrected = {e: v for e, v in priced_avail.items() if v > 0}
            band_c = benchmark_band(corrected, min_vector=min_vector,
                                    min_band=min_band)
            band_u = benchmark_band(uncorrected, min_vector=min_vector,
                                    min_band=min_band)
            if corrected:
                kept_share.append(100.0 * len(uncorrected) / len(corrected))
            records.append({
                "peer_set_id": int(row["peer_set_id"]), "as_of": day,
                "rung": row["rung"], "n_members": len(cohort),
                "vector_corrected": band_c.n_vector,
                "band_corrected": band_c.n_band,
                "ok_corrected": int(band_c.ok),
                "vector_uncorrected": band_u.n_vector,
                "band_uncorrected": band_u.n_band,
                "ok_uncorrected": int(band_u.ok),
                # The BAND with the vector gate LIFTED, so the two thresholds
                # can be told apart: how often the 12-in-the-vector rule binds
                # is a different question from how often 3-in-the-band does.
                "band_free_corrected": _band_size(corrected),
                "band_free_uncorrected": _band_size(uncorrected),
                # The availability-only reconstruction, for the cross-check
                # against N_EVEBITDA from the independent mask census.
                "vector_uncorrected_no_screen": len(priced_avail),
                # The multi-observation cohorts. THE UNIQUE-LEAF RULE, measured:
                # acceleration needs THREE EBITDA observations, and a model
                # built as growth x growth would have charged for four.
                "vector_2obs": sum(1 for e in cohort
                                   if day_obs.get(e, 0) >= 2),
                "vector_3obs": sum(1 for e in cohort
                                   if day_obs.get(e, 0) >= 3),
                "vector_4obs": sum(1 for e in cohort
                                   if day_obs.get(e, 0) >= 4),
            })
        if progress and (bit % 40 == 0 or bit == len(dates) - 1):
            print(f"    {day}: {len(rows)} peer sets", flush=True)

    def dist(field_name: str) -> dict[str, Any]:
        return _distribution([r[field_name] for r in records])

    n = max(1, len(records))
    kept_share.sort()

    def q(share: Sequence[float], point: int) -> float:
        if not share:
            return float("nan")
        return round(share[max(0, min(len(share) - 1,
                                      -(-point * len(share) // 100) - 1))], 1)

    return {
        "n_peer_sets": len(records),
        "min_vector": min_vector, "min_band": min_band,
        "value_index": {k: v for k, v in index.items() if k != "values"},
        "corrected_ebitda_only": {
            "scope": FULL_UNIVERSE,
            "vector": dist("vector_corrected"), "band": dist("band_corrected"),
            "band_gate_lifted": dist("band_free_corrected"),
            "benchmark_formed_pct": round(
                100.0 * sum(r["ok_corrected"] for r in records) / n, 2),
        },
        "uncorrected_price_conditional": {
            "scope": SURVIVOR_ONLY,
            "vector": dist("vector_uncorrected"), "band": dist("band_uncorrected"),
            "band_gate_lifted": dist("band_free_uncorrected"),
            "benchmark_formed_pct": round(
                100.0 * sum(r["ok_uncorrected"] for r in records) / n, 2),
        },
        "company_observation_marginals": {
            "scope": FULL_UNIVERSE,
            "base": ("the peer universe with a usable non-stale Assets fact -- "
                     "pit_derive.MEASURED_BASE's denominator"),
            "per_date": {day: dict(
                row, **{f"pct_ge_{k}": round(
                    100.0 * row[f"ge_{k}_observations"] / max(1, row["base"]), 2)
                    for k in (1, 2, 3, 4)})
                for day, row in census.items()},
            "why": ("the multi-observation joint at COMPANY level, measured. "
                    "The independence product across lags says 0.513^3 = 13.5% "
                    "for acceleration; this says what it actually is, and the "
                    "gap is the co-occurrence a product cannot see."),
        },
        "observation_depth": {
            "scope": FULL_UNIVERSE,
            "one_observation": dist("vector_corrected"),
            "two_observations": dist("vector_2obs"),
            "three_observations": dist("vector_3obs"),
            "four_observations": dist("vector_4obs"),
            "why": ("what a growth and an acceleration cohort actually "
                    "contain. THE UNIQUE-LEAF RULE, priced: acceleration needs "
                    "THREE consecutive annual EBITDA observations -- the two "
                    "differences share the middle one -- and a coverage model "
                    "built as growth_t x growth_t-1 would have demanded four. "
                    "The gap between the three- and four-observation rows is "
                    "what that mistake would have cost."),
        },
        "uncorrected_availability_only_crosscheck": {
            "scope": SURVIVOR_ONLY,
            "vector": dist("vector_uncorrected_no_screen"),
            "why": ("the same five-primitive conjunction WITHOUT the EBITDA>0 "
                    "screen, so it compares against N_EVEBITDA from the "
                    "independent mask census (median 1, >=3 34.2%, >=12 7.0%). "
                    "Agreement is evidence that this value-level reconstruction "
                    "and the mask-level one are the same measurement."),
        },
        "rescued_at_the_band": sum(
            1 for r in records
            if r["band_corrected"] >= min_band and r["band_uncorrected"] < min_band),
        "rescued_benchmark": sum(1 for r in records
                                 if r["ok_corrected"] and not r["ok_uncorrected"]),
        "kept_share_pct": {"p10": q(kept_share, 10), "p25": q(kept_share, 25),
                           "median": q(kept_share, 50), "p75": q(kept_share, 75),
                           "p90": q(kept_share, 90),
                           "pct_keeping_none": round(
                               100.0 * sum(1 for s in kept_share if s == 0.0)
                               / max(1, len(kept_share)), 2)},
        "records": records,
    }


def _connect_ro(db_path: str, busy_ms: int = 60_000) -> sqlite3.Connection:
    """Read-only, with a busy timeout and no long snapshot. A reader blocks the
    WAL checkpointer, and this volume was filled once by a 9.3 GB WAL."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {busy_ms}")
    conn.execute("PRAGMA cache_size = -80000")
    return conn


def _wal_bytes(db_path: str) -> int:
    try:
        return os.path.getsize(db_path + "-wal")
    except OSError:
        return 0


# ==========================================================================
# CLI
# ==========================================================================

def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    db_path = pit_store.DEFAULT_PIT_DB_PATH
    masks_dir = os.getcwd()
    out_dir = None
    do_measure = False
    i = 0
    while i < len(argv):
        if argv[i] == "--db" and i + 1 < len(argv):
            db_path = argv[i + 1]; i += 2
        elif argv[i] == "--masks" and i + 1 < len(argv):
            masks_dir = argv[i + 1]; i += 2
        elif argv[i] == "--out" and i + 1 < len(argv):
            out_dir = argv[i + 1]; i += 2
        elif argv[i] == "--measure":
            do_measure = True; i += 1
        else:
            i += 1

    if do_measure:
        out_dir = out_dir or masks_dir
        wal_before = _wal_bytes(db_path)
        started = time.time()
        conn = _connect_ro(db_path)
        result = measure_band_delta(conn, masks_dir)
        conn.close()
        wal_after = _wal_bytes(db_path)
        result["wal_bytes_before"] = wal_before
        result["wal_bytes_after"] = wal_after
        result["elapsed_seconds"] = round(time.time() - started, 1)
        path = os.path.join(out_dir, "band_delta.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(result, handle)
        cor = result["corrected_ebitda_only"]
        unc = result["uncorrected_price_conditional"]
        print(f"{result['n_peer_sets']:,} peer sets, "
              f"{result['elapsed_seconds']}s; WAL {wal_before:,} -> {wal_after:,}")
        for name, block in (("CORRECTED  (EBITDA only)", cor),
                            ("UNCORRECTED(price-cond)", unc)):
            v, b, f = block["vector"], block["band"], block["band_gate_lifted"]
            print(f"  {name}  vector med={v['median']:4d} p25={v['p25']:4d} "
                  f"p75={v['p75']:4d} >=12 {v['pct_ge_12']:5.1f}%  |  band "
                  f"(gate lifted) med={f['median']:4d} >=3 {f['pct_ge_3']:5.1f}%"
                  f"  |  band med={b['median']:4d} >=3 {b['pct_ge_3']:5.1f}%"
                  f"  |  benchmark {block['benchmark_formed_pct']:5.1f}%")
        for day, row in sorted(
                result["company_observation_marginals"]["per_date"].items()):
            print(f"  COMPANY {day} base={row['base']:5d}  "
                  + "  ".join(f">={k}obs {row[f'pct_ge_{k}']:5.2f}%"
                              for k in (1, 2, 3, 4)))
        depth = result["observation_depth"]
        for label, key in (("1 obs (level)      ", "one_observation"),
                           ("2 obs (growth)     ", "two_observations"),
                           ("3 obs (acceleration)", "three_observations"),
                           ("4 obs (the mistake)", "four_observations")):
            d = depth[key]
            print(f"  DEPTH {label} med={d['median']:4d} >=3 "
                  f"{d['pct_ge_3']:5.1f}%  >=12 {d['pct_ge_12']:5.1f}%")
        chk = result["uncorrected_availability_only_crosscheck"]["vector"]
        print(f"  CROSS-CHECK vs the mask census N_EVEBITDA (med 1, >=3 34.2%, "
              f">=12 7.0%): med={chk['median']} >=3 {chk['pct_ge_3']:.1f}% "
              f">=12 {chk['pct_ge_12']:.1f}%")
        print(f"  rescued at the band: {result['rescued_at_the_band']:,}; "
              f"benchmark rescued: {result['rescued_benchmark']:,}")
        print(f"  share of the EBITDA vector surviving the price filter: "
              f"{result['kept_share_pct']}")
        print(f"wrote {path} ({os.path.getsize(path):,} bytes)")
        return 0

    print(render())
    print(factor_coverage_report(pit_derive.MEASURED_DATES[-1]))
    print(ebitda_benchmark_before_after())
    print()
    print(render_gate())
    print()
    problems = validate()
    for problem in problems:
        print("  FAIL", problem)
    print(f"{len(SPECS)} factor specs, {len(ELIGIBILITY_RULES)} eligibility "
          f"rules, {len(problems)} problems")
    print("PASS" if not problems else "FAIL")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
