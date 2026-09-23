"""One declared CONTRACT per derived factor, each with its OWN peer universe.

THE CORRECTION THIS MODULE EXISTS TO LOCK IN. `build_ebitda_peer_cohort` in the
frozen production core filters peers to those carrying a USABLE EV/EBITDA and
only then takes percentiles. The consequence is not a rounding error:

  * a FUNDAMENTAL OPERATING BENCHMARK is made conditional on price, shares,
    debt and cash -- for about thirty other companies, not for the one being
    scored;
  * `sector_ebitda_p50` and `sector_ebitda_p75` are therefore the PRICED
    sector's percentiles, not the sector's. They are a size statistic silently
    selected on price and liquidity, and nothing in the stored row says so.

The owner's rule: nothing about price, shares, debt or cash belongs in the
EBITDA benchmark's eligibility filter. The EBITDA cohort needs EBITDA.
Valuation separately asks which peers have the primitives for EV/EBITDA. TWO
DIFFERENT COHORTS, and every Shaffer factor gets its own.

MEASURED COST OF THE CURRENT RULE, over all 39,038 peer sets in the store
(`pit_cohort_measure.py`, 2026-09-21, 3,069,260 memberships, 165 month-end
dates):

    EBITDA benchmark cohort, as the core builds it   median  1  >=3 34.2%  >=12  7.0%
    EBITDA benchmark cohort, as the rule requires    median 13  >=3 95.2%  >=12 56.0%

    49.0% of peer sets clear the 12-member band under the corrected rule and
    do not under the current one; 61.0% clear the 3-member floor and do not.
    The median peer set keeps 10.0% of its EBITDA cohort after the price
    filter, and a quarter of peer sets keep NONE.

THE AVAILABILITY RULE, stated once and enforced by `validate()`:

    Availability(DerivedFeature) = JointAvailability(UNIQUE primitive leaves)

never a product of intermediate feature coverages. `ebitda_acceleration` needs
E_t, E_t-1 and E_t-2 -- THREE observations of two primitives, not four, because
the two EBITDA differences share the middle one. A coverage model that
multiplied two EBITDA-growth coverages would charge for the shared leaf twice.

STATUS. `equity_shaffer_v1` and `equity_shaffer_v1_pit` are FROZEN. Nothing here
edits `company_scoring.py`, `sector_scoring.py`, `asset_models.py`,
`prediction.py` or `hedging.py`; the finding against them is recorded as
SHAFFER_V1_KNOWN_LIMITATION and the contracts below belong to the CANDIDATE
lineage only. This module is declarative: it reads nothing, writes nothing and
computes no score.

    python pit_factor_contract.py          # prints the contracts; PASS/FAIL
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

CANDIDATE_MODEL_VERSION = "candidate_equity_shaffer_v2"
CONTRACT_VERSION = "factor_contract_v1"

#: The production core is frozen. A defect found in it is RECORDED here and
#: fixed in the candidate lineage, never in place.
SHAFFER_V1_KNOWN_LIMITATION = "SHAFFER_V1_KNOWN_LIMITATION"

#: Where every measured number in this module comes from.
EVIDENCE = ("pit_cohort_scan.py + pit_cohort_price.py + pit_cohort_measure.py, "
            "run 2026-09-21 against shafferfineval_pit.db: 39,038 peer sets, "
            "3,069,260 memberships, 165 month-end as-of dates, ladder "
            "concept_ladder_v2, duration facts at annual cadence. Validated by "
            "pit_cohort_validate.py: 6,000 assertions against the real "
            "selectors with zero disagreements, and the three census marginals "
            "reproduced to 0.0 pp.")

#: Results that depend on `pit_listing` are survivor-only and say so.
SURVIVOR_ONLY = "SURVIVOR_ONLY_DIAGNOSTIC"
FULL_UNIVERSE = "FULL_REPORTING_UNIVERSE"

#: Normalisation vocabulary. Named rather than described, so two contracts
#: cannot mean different things by "percentile".
NORM_COHORT_PERCENTILE = "cohort_percentile"
NORM_COHORT_RATIO = "ratio_to_cohort_location"
NORM_WINSORISED_MEAN = "winsorised_cohort_mean"
NORM_NONE = "none_company_level"

#: The SIC fallback ladder every cohort widens along. `pit_peers.RUNG_LADDER`
#: is the authority; restated as a tuple of strings so this module imports
#: nothing and can be read as a specification.
RUNG_LADDER = ("sic4", "sic3", "sic2", "office")

#: What a factor does when its cohort cannot be formed. The frozen core's
#: answer for valuation is `pit_shares.VALUATION_DROPPED_WEIGHTS`: drop the
#: factor and renormalise 0.25/0.20/0.15 over 0.60. Naming the behaviour on the
#: contract means a reader never has to infer it from a NULL.
MISSING_DROP_RENORMALISE = "drop_factor_and_renormalise_major_weights"
MISSING_UNAVAILABLE = "report_unavailable_with_named_binding_leaf"


@dataclass(frozen=True)
class FactorContract:
    """Everything a derived factor must declare before it may be computed.

    Six fields, because six is what the owner enumerated and because each one
    is a place the current core gets something wrong somewhere:

      required_primitives   the UNIQUE leaves, which is what joint availability
                            is taken over. Listing a derived intermediate here
                            would double-charge its leaves.
      peer_eligibility      the rule for THIS factor's own cohort. The bug
                            being corrected is entirely a bug in this field.
      normalization         how a member's value becomes a score.
      min_peers             below this the cohort is refused, not caveated.
      fallback_rung         the ladder widened along when it is refused.
      missingness           what the score does when the cohort never forms.
    """

    key: str
    question: str
    required_primitives: tuple[str, ...]
    peer_eligibility: str
    normalization: str
    min_peers: int
    min_peers_why: str
    fallback_rung: tuple[str, ...]
    missingness: str
    sample_scope: str
    measured: dict[str, Any] = field(default_factory=dict)
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "question": self.question,
            "required_primitives": list(self.required_primitives),
            "peer_eligibility": self.peer_eligibility,
            "normalization": self.normalization,
            "min_peers": self.min_peers, "min_peers_why": self.min_peers_why,
            "fallback_rung": list(self.fallback_rung),
            "missingness": self.missingness,
            "sample_scope": self.sample_scope,
            "measured": dict(self.measured), "note": self.note,
        }


#: Primitives that say something about a company's PRICE or its capital
#: structure as the market sees it. A fundamental cohort's eligibility rule may
#: not mention one; `validate()` enforces exactly that.
PRICE_CONDITIONAL = frozenset({"price_adjusted", "price_raw", "shares_outstanding",
                               "total_debt", "cash", "market_cap", "enterprise_value"})

#: Contracts whose cohort is a FUNDAMENTAL one and must therefore be free of
#: every primitive in PRICE_CONDITIONAL.
FUNDAMENTAL_COHORTS = frozenset({"ebitda_size_benchmark", "ebitda_efficiency",
                                 "real_revenue_growth"})


CONTRACTS: tuple[FactorContract, ...] = (
    FactorContract(
        key="ebitda_size_benchmark",
        question="What does a right-sized peer in this sector earn?",
        required_primitives=("operating_income", "depreciation_amortisation"),
        peer_eligibility=(
            "members of the peer set whose EBITDA resolves: operating_income "
            "AND depreciation_amortisation at a MATCHED (period_end, qtrs), "
            "each non-stale under pit_policy.is_stale. NOTHING ELSE. No price, "
            "no share count, no debt, no cash -- this is the sector's "
            "distribution of earning power and conditioning it on tradeability "
            "would make it the PRICED sector's distribution."),
        normalization=NORM_COHORT_PERCENTILE,
        min_peers=12,
        min_peers_why=(
            "the benchmark is the mean of the 50th-75th percentile BAND, which "
            "is a quarter of the vector by construction, so three members "
            "inside the band needs twelve in the vector. 3 is the floor for "
            "the percentiles themselves (company_scoring.MIN_EBITDA_COHORT)."),
        fallback_rung=RUNG_LADDER,
        missingness=MISSING_UNAVAILABLE,
        sample_scope=FULL_UNIVERSE,
        measured={
            "median": 13, "p10": 5, "p25": 8, "p75": 23, "p90": 70, "max": 718,
            "pct_ge_3": 95.2, "pct_ge_12": 56.0,
            "sic4_rung_median": 11, "sic4_pct_ge_3": 92.6, "sic4_pct_ge_12": 46.7,
            "under_the_current_price_conditional_rule": {
                "median": 1, "pct_ge_3": 34.2, "pct_ge_12": 7.0},
        },
        note=(SHAFFER_V1_KNOWN_LIMITATION + ": company_scoring."
              "build_ebitda_peer_cohort applies the EV/EBITDA eligibility rule "
              "here. Measured, that costs 12 members of median cohort size and "
              "turns a 95.2% formation rate at the 3-peer floor into 34.2%."),
    ),
    FactorContract(
        key="ebitda_efficiency",
        question="How profitable is a typical right-sized peer, per dollar of sales?",
        required_primitives=("operating_income", "depreciation_amortisation",
                             "revenue"),
        peer_eligibility=(
            "the EBITDA cohort above, further restricted to members whose "
            "revenue resolves AT THE SAME (period_end, qtrs) as the EBITDA. A "
            "margin is a ratio of one period's numbers; an annual EBITDA over "
            "a quarterly revenue is not a margin."),
        normalization=NORM_COHORT_RATIO,
        min_peers=3,
        min_peers_why=("a cohort MEAN, not a percentile: three is the floor the "
                       "frozen core uses for a cohort statistic."),
        fallback_rung=RUNG_LADDER,
        missingness=MISSING_UNAVAILABLE,
        sample_scope=FULL_UNIVERSE,
        measured={
            "median": 12, "p10": 4, "p25": 8, "p75": 22, "p90": 66, "max": 699,
            "pct_ge_3": 94.5, "pct_ge_12": 52.8,
            "revenue_selected_independently": {"pct_ge_3": 94.5, "note":
                "the matched-period rule costs almost nothing at annual cadence"},
        },
    ),
    FactorContract(
        key="ev_ebitda",
        question="What multiple is the market paying for a peer's earnings?",
        required_primitives=("price_adjusted", "shares_outstanding", "total_debt",
                             "cash", "operating_income", "depreciation_amortisation"),
        peer_eligibility=(
            "members with a resolvable point-in-time price (pit_identity."
            "scored_universe_as_of: a listing valid on the date, a bar with "
            "volume > 0 within 10 days, no quarantine), a DEFENSIBLE share "
            "count (pit_rawprice.class_decision under the strict policy), "
            "total_debt on concept_ladder_v2, cash, and EBITDA. Six primitives, "
            "five of them conjunctive with the sixth."),
        normalization=NORM_WINSORISED_MEAN,
        min_peers=12,
        min_peers_why="the same 50-75 band arithmetic as the benchmark above.",
        fallback_rung=RUNG_LADDER,
        missingness=MISSING_DROP_RENORMALISE,
        sample_scope=SURVIVOR_ONLY,
        measured={
            "median": 1, "p10": 0, "p25": 0, "p75": 3, "p90": 8, "max": 95,
            "pct_ge_3": 34.2, "pct_ge_5": 18.6, "pct_ge_8": 11.5, "pct_ge_12": 7.0,
            "sic4_rung_median": 1, "sic4_pct_ge_3": 22.9, "sic4_pct_ge_12": 2.4,
            "office_rung_pct_ge_12": 95.0,
            "leave_one_out_pct_ge_3": {
                "all_five": 34.2, "without_defensible_shares": 61.6,
                "without_total_debt": 51.2, "without_ebitda": 46.8,
                "without_cash": 35.0, "without_price_and_shares": 83.1},
            "seasonality_pct_ge_3": {"january": 14.5, "april": 40.6,
                                     "october": 19.1},
        },
        note=("The binding leaf is the DEFENSIBLE SHARE COUNT, then total_debt. "
              "Cash is free. And the cohort is SEASONAL: shares_outstanding "
              "carries a 4-month staleness bound (pit_policy."
              "CONCEPT_MAX_AGE_MONTHS), so a December-FY filer's count expires "
              "at the end of January and the next one has not been filed -- "
              "52.1% of all 174,674 refused entity-dates are "
              "stale_beyond_max_age, against 2.2% for the share-class guard."),
    ),
    FactorContract(
        key="pe_ratio",
        question="What is the market paying for a dollar of this company's earnings?",
        required_primitives=("price_adjusted", "earnings_per_share_pit"),
        peer_eligibility=(
            "members with a resolvable point-in-time price and a point-in-time "
            "TTM EPS. NO SHARE COUNT: the per-share division is inside the "
            "filed EPS figure, which is why this chain is two primitives where "
            "EV/EBITDA is six."),
        normalization=NORM_COHORT_RATIO,
        min_peers=3,
        min_peers_why="a cohort mean, as `sector_pe` is in the frozen core.",
        fallback_rung=RUNG_LADDER,
        missingness=MISSING_DROP_RENORMALISE,
        sample_scope=SURVIVOR_ONLY,
        measured={
            "eps_in_store": 0,
            "why": ("pit_dera.TAG_FILTER is built from the concept ladders and "
                    "no ladder names an EPS tag, so every EarningsPerShare* row "
                    "in 72 DERA quarters was read and dropped at ingest. This "
                    "factor is GENUINELY_UNAVAILABLE today and RECOVERABLE; see "
                    "pit_cohort_eps.py for the scope and cost."),
            "proxy_median": 9, "proxy_pct_ge_3": 92.0, "proxy_pct_ge_12": 36.0,
            "proxy_note": ("measured with the net_income ladder standing in for "
                           "EPS -- same filers, same periods, same filings. It "
                           "is an upper bound on P/E because P/E is undefined "
                           "for a loss-making filer."),
            "pct_positive_earnings_sampled": {"2015-06-30": 72.94,
                                              "2019-06-28": 70.18,
                                              "2024-06-28": 68.29,
                                              "n_per_date": 400,
                                              "note": "seeded sample, not a census"},
            "recovery_scope_priced_universe": {
                "entities": 2533, "requests": 5066, "minutes_at_10_rps": 8.4,
                "download_gib": 0.112, "distinct_cik_period_pairs": 216540,
                "retained_rows": 1595180, "retained_gib": 0.529,
                "bytes_per_row_measured": 356.1},
            "recovery_scope_full_peer_universe": {
                "entities": 16148, "requests": 32296, "minutes_at_10_rps": 53.8,
                "download_gib": 0.711, "distinct_cik_period_pairs": 633113,
                "retained_rows": 4173284, "retained_gib": 1.384},
        },
        note=("2.7x the 3-peer formation rate of EV/EBITDA and 5.1x at the "
              "12-peer bar, and FLAT ACROSS THE CALENDAR (91.6%-92.3% in every "
              "month) because net income carries the 15-month annual staleness "
              "bound rather than the share count's 4-month one."),
    ),
    FactorContract(
        key="real_revenue_growth",
        question="Did this company grow faster than the price level?",
        required_primitives=("revenue", "cpi"),
        peer_eligibility=(
            "members with revenue at TWO periods a year apart, each non-stale, "
            "plus the CPI vintage available on the as-of date. Company-level, "
            "so the 'cohort' is only the percentile denominator."),
        normalization=NORM_COHORT_PERCENTILE,
        min_peers=3,
        min_peers_why="a percentile rank needs a denominator; 3 is the core's floor.",
        fallback_rung=RUNG_LADDER,
        missingness=MISSING_UNAVAILABLE,
        sample_scope=FULL_UNIVERSE,
        measured={
            "revenue_cohort_median": 19, "revenue_pct_ge_3": 98.8,
            "revenue_pct_ge_12": 82.0,
            "leaves": 2,
            "note": ("the revenue figure here is ONE-PERIOD availability; the "
                     "two-period joint has NOT been measured and is reported "
                     "as UNKNOWN rather than assumed to be the square of it. "
                     "CPI is 100% available on every grid date."),
        },
    ),
)

BY_KEY = {c.key: c for c in CONTRACTS}


#: Unique-leaf arithmetic, stated as data so it can be checked rather than
#: believed. The key is the derived feature; the value is the number of
#: distinct primitive OBSERVATIONS it needs, and the reason.
LEAF_ARITHMETIC: dict[str, dict[str, Any]] = {
    "ebitda": {"observations": 2, "why": "operating_income and D&A at one period"},
    "ebitda_growth": {"observations": 4,
                      "why": ("E_t and E_t-1, each two primitives; the "
                              "denominator is abs(E_t-1), so no asset "
                              "observation is charged (D3, 2026-09-22)")},
    "ebitda_acceleration": {
        "observations": 6,
        "distinct_ebitda_observations": 3,
        "why": ("E_t, E_t-1 and E_t-2 -- THREE EBITDA observations, not four. "
                "The two growth rates share E_t-1, and a coverage model built "
                "as growth_t x growth_t-1 charges for it twice."),
    },
    "ev_ebitda": {"observations": 6,
                  "why": "price, shares, debt, cash, operating_income, D&A"},
    "pe_ratio": {"observations": 2, "why": "price and one EPS figure"},
}


def validate() -> list[str]:
    """Every contract complete, and no fundamental cohort priced. Returns problems."""
    problems: list[str] = []
    for contract in CONTRACTS:
        if not contract.required_primitives:
            problems.append(f"{contract.key}: no required primitives declared")
        if contract.min_peers < 1:
            problems.append(f"{contract.key}: min_peers must be at least 1")
        if not contract.fallback_rung:
            problems.append(f"{contract.key}: no fallback rung ladder declared")
        if contract.sample_scope not in (SURVIVOR_ONLY, FULL_UNIVERSE):
            problems.append(f"{contract.key}: sample scope {contract.sample_scope!r}"
                            " is not a declared vocabulary term")
        if contract.key in FUNDAMENTAL_COHORTS:
            leaked = sorted(PRICE_CONDITIONAL & set(contract.required_primitives))
            if leaked:
                problems.append(
                    f"{contract.key}: a FUNDAMENTAL cohort requires price-"
                    f"conditional primitives {leaked} -- this is exactly the "
                    "defect the contract exists to prevent")
            lowered = contract.peer_eligibility.lower()
            for word in ("share count", "market cap", "ev/ebitda", "enterprise"):
                if word in lowered and "no " + word not in lowered:
                    problems.append(
                        f"{contract.key}: eligibility rule mentions {word!r} "
                        "without refusing it")
        if contract.sample_scope == SURVIVOR_ONLY:
            priced = PRICE_CONDITIONAL & set(contract.required_primitives)
            if not priced:
                problems.append(f"{contract.key}: labelled survivor-only but "
                                "requires no price-conditional primitive")
    accel = LEAF_ARITHMETIC["ebitda_acceleration"]
    if accel["distinct_ebitda_observations"] != 3:
        problems.append("ebitda_acceleration must need THREE EBITDA observations")
    return problems


def render() -> str:
    lines = [f"FACTOR CONTRACTS -- {CONTRACT_VERSION} for {CANDIDATE_MODEL_VERSION}",
             f"evidence: {EVIDENCE}", ""]
    for contract in CONTRACTS:
        lines.append(f"{contract.key}  [{contract.sample_scope}]")
        lines.append(f"  question      {contract.question}")
        lines.append(f"  primitives    {', '.join(contract.required_primitives)}"
                     f"   ({len(contract.required_primitives)} unique leaves)")
        lines.append(f"  eligibility   {contract.peer_eligibility}")
        lines.append(f"  normalization {contract.normalization}")
        lines.append(f"  min peers     {contract.min_peers} -- {contract.min_peers_why}")
        lines.append(f"  fallback      {' -> '.join(contract.fallback_rung)}")
        lines.append(f"  missing       {contract.missingness}")
        measured = contract.measured
        if measured:
            lines.append("  measured      " + json.dumps(measured, sort_keys=True))
        if contract.note:
            lines.append(f"  note          {contract.note}")
        lines.append("")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    print(render())
    problems = validate()
    for problem in problems:
        print("  FAIL", problem)
    print(f"{len(CONTRACTS)} contracts, {len(problems)} problems")
    print("PASS" if not problems else "FAIL")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
