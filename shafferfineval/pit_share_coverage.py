"""ONE CANONICAL SHARE-COVERAGE NUMBER, and the four that disagreed with it.

READ-ONLY and declarative. Writes nothing anywhere: `pit_feature`, `pit_score`
and `pit_replay_run` stay at zero rows, no baseline is frozen, nothing is
fitted. The live half is three indexed SELECTs behind a `mode=ro` handle.

==========================================================================
THE PROBLEM
==========================================================================

Four share-coverage figures are in circulation in this project and they
disagree by a factor of nearly three:

    ~62%                 pit_derive / pit_coverage, "the ladder"
                         SHARE_COV_A_LADDER_BASE_V1
    ~52-55%              pit_sharecoverage, "usable"
                         SHARE_COV_B_USABLE_PEERS_V1
    25.35/35.20/41.35%   the defensible series, after the share-class guard
                         SHARE_COV_C_DEFENSIBLE_PEERS_V1
    72.25%               pit_fact_kind, the v2 state-variable rule, pooled
                         SHARE_COV_D2_STATE_PRICED_V2_POOLED
                         (its v1 twin, 53.70%, is
                         SHARE_COV_D1_DEFENSIBLE_PRICED_V1_POOLED)

Those five ids are not decoration. THIS DOCSTRING OBEYS ITS OWN RULE: every
percentage above that is a registered share-coverage figure appears in a text
that names the definition it belongs to, and `test_pit_share_coverage` runs
`assert_quotable` over this docstring to keep it that way.

None of them is wrong. Every one of them answers a different question, and
none of them carried its question in its name, so each was quoted as though it
were the share coverage of the store. The owner's instruction is the whole
specification for this module:

    "I don't care which is highest; I care that each numerator/denominator
     and eligibility rule is explicit so the final number means one thing."

So this module does three things and refuses to do a fourth.

  (1) RECONCILES. Each figure is traced to the code that produced it and
      restated as one evaluation of the same function -- see `DEFINITIONS`,
      and `reconciliation()` for the step ladder in which each choice moves
      the number by a named amount.
  (2) PUBLISHES ONE. `PIT_SHARE_STATE_COVERAGE_V2` with `CANONICAL`, its full
      definition, and `canonical_block()` in the shape the owner asked for. A
      later quote imports the CONSTANT and names the DEFINITION; it does not
      retype the number.
  (3) ENFORCES THE RULE. `LEGACY_FIGURES` maps every figure in circulation to
      its definition, `lookup("52%")` answers what it meant, and
      `assert_quotable()` REFUSES a text that quotes one of them without
      naming the definition it belongs to. The rule is executable, so it can
      be run over a docstring instead of remembered.

  It does NOT re-run the census. The per-date and pooled counts below were
  measured by `pit_cohort_price`, `pit_fact_kind --grid` and `pit_coverage`
  and are carried here as data with their provenance; re-deriving them would
  cost hours and would produce the same numbers, and a fifth measurement of
  the same quantity is exactly what this module exists to stop.

==========================================================================
WHAT THE RECONCILIATION FOUND -- three defects, measured here
==========================================================================

1. TWO OF THE FOUR DIFFER ONLY BY DENOMINATOR. Measured live at 2015-06-30
   (`verify_selectors`, 131s, read-only): the pit_coverage selector and the
   pit_sharecoverage selector return the SAME 4,410 peers with a fresh
   point-in-time count -- not a similar count, the same set, `both = 4410`.
   62% and 55% are that one numerator over two denominators (the base
   universe, 7,073, and the peer universe, 7,950), with 88 entities dropped
   from the numerator by the base restriction itself. Nothing about share
   data differs between them. The `~62%` figure is a coverage of companies
   that already have a usable balance sheet.

1b. AND THE LOWEST FIGURE IS NOT A SHARE FIGURE EITHER. The fall from 4,410
   to the defensible 2,015 at 2015-06-30 is 2,356 entities lost to the SYMBOL
   gate and 39 to the share-class guard -- 98.4% and 1.6%. The series quoted
   as evidence that share counts are scarce is mostly evidence that
   dei:TradingSymbol tagging was thin before 2019.

2. A ROW COUNT WAS BEING READ AS AN ENTITY COUNT. `pit_derive.PRICE_NOTE`
   quotes the scored universe as 2,065 / 2,455 / 2,475; `pit_fact_kind`
   quotes priced entities as 2,034 / 2,423 / 2,443. Both are right:
   `pit_identity.scored_universe_as_of` returns one row PER LISTING, and an
   issuer with two lines contributes two rows. Verified at all three dates by
   `verify_denominators`. 31 / 32 / 32 entity-dates of difference is small,
   and a denominator that silently changes unit is not.

3. THE LADDER'S THIRD RUNG CANNOT FIRE. `pit_policy`'s `shares_outstanding`
   ladder names WeightedAverageNumberOfDilutedSharesOutstanding as a last
   resort, and `pit_coverage.resolve_concept` queries an INSTANT ladder at
   `qtrs = 0`. The weighted average is a duration fact: in a 400-entity
   sample 51,682 of its rows carry qtrs 1-4 and 16 carry qtrs 0 (0.03%), and
   at 2015-06-30 it rescued ZERO of 7,950 peers. So all four figures share
   the same two effective concepts, and the "the ladder allows a period
   average" objection does not separate them. It separates nothing, because
   the rung is unreachable where it is nominally allowed.

==========================================================================
WHY THE CANONICAL NUMBER IS THE ONE IT IS
==========================================================================

`PIT_SHARE_STATE_COVERAGE_V2` is the v2 state-variable figure over priced
entity-dates, pooled across the 165-date grid, with the share-class guard on.
Five reasons, each of them a measured one:

  * THE DENOMINATOR IS WHAT THE REPLAY ITERATES. A valuation factor needs a
    price and a count. An entity-date with no price cannot produce one at any
    share coverage, so counting it in the denominator measures the price
    loader. 377,304 priced entity-dates is the set a replay would actually
    ask the share question about.
  * THE GRID IS THE REPLAY'S GRID. The three census dates are all JUNE, and
    the measurement shows June is the wrong month to ask in: the v1->v2 gain
    is 9.54-12.14 points at those dates and 18.54 pooled, because the hole
    the state rule closes lives in January and October.
  * THE GUARD IS ON. A per-class count taken for the whole company understates
    market cap -- measured 10th percentile of outstanding/diluted for proved
    multi-class issuers is 0.18, a 5.5x error. A number that counted those
    rows would be counting rows the replay must refuse.
  * THE CALENDAR IS GONE. Under v1 the same quantity reads 23.02% in January
    and 67.19% in April. A coverage number with a 44.17-point month-to-month
    spread is not a coverage number; under v2 the spread is 0.67 points.
  * IT IS THE UNIT THE BINDING LEAF WAS MEASURED IN. The EV/EBITDA cohort
    census was re-run on exactly these entity sets, which is how P(N>=3)
    34.2% -> 46.0% is known.

And what it is NOT, stated in the block itself rather than in a footnote:
SURVIVOR_ONLY_DIAGNOSTIC (the priced universe comes from 2,574 listings, all
alive in 2026, so true coverage is LOWER), CANDIDATE lineage and not
production, and silent about the ~4,900 peers per date that have no price at
all and therefore never enter the denominator.

==========================================================================
SENSITIVITY, BECAUSE A NUMBER NOBODY CAN ARGUE WITH IS WORSE
==========================================================================

`sensitivity()` reports the three levers, all measured:

    SANITY CEILING   4m -> unbounded moves the admitted entity-dates from
                     206,817 to 297,868. The curve is steep where the
                     artefact is (4->12m buys 59,371) and flat where the
                     ceiling sits (24->36m buys 3,555).
    DENOMINATOR      the SAME v2 numerator of 272,587 reads 72.25% over
                     priced entity-dates, 91.51% over entity-dates that have
                     any in-scope count, and 22.01% over peer entity-dates.
                     This lever is worth more than every other choice
                     combined, which is the owner's point in one line.
    GUARD            on 72.25%, off 74.09%. 1.84 points, and it buys the
                     refusal of counts that are demonstrably one class.

Stdlib only. Python 3.9+.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

__all__ = [
    "PIT_SHARE_STATE_COVERAGE_V2", "CANONICAL_DEFINITION_ID", "CANONICAL",
    "CoverageDefinition", "DEFINITIONS", "definition", "canonical_block",
    "LEGACY_FIGURES", "UndefinedQuote", "lookup", "quote", "assert_quotable",
    "reconciliation", "sensitivity", "ceiling_sensitivity",
    "denominator_sensitivity", "guard_sensitivity", "CEILING_CURVE_METHOD_GAP",
    "MEASURED", "SAMPLE_SCOPE", "connect_readonly", "verify_denominators",
    "verify_rung_reachability", "verify_selectors", "resources",
    "audit_sources", "AUDIT_EXEMPT",
]

MEASURED_ON = "2026-09-21"

#: Every figure in this module whose numerator or denominator touches a price
#: inherits this scope, and that is all five of them: the priced universe is
#: drawn from `pit_listing`'s 2,574 company lines, every one alive in 2026.
SAMPLE_SCOPE = "SURVIVOR_ONLY_DIAGNOSTIC"

SAMPLE_SCOPE_NOTE = (
    "The priced universe comes from 2,574 listings, all alive in 2026, whose "
    "median survivor underperformed the total market by -7.45% at 12M. Dead "
    "issuers are exactly the ones whose share counts are hardest to resolve, "
    "so every coverage rate here is an OVERSTATEMENT of the true one by an "
    "unmeasured amount. Diagnostic only; may not promote a model.")

UNKNOWN = None   # written as None and never as 0. An unmeasured quantity is
                 # not a zero, and reporting it as one is how a disk fills.


# ==========================================================================
# (1) WHAT A COVERAGE NUMBER IS. Every field is a choice someone made.
# ==========================================================================

@dataclass(frozen=True)
class CoverageDefinition:
    """One share-coverage figure, with every choice behind it named.

    The fields are not documentation. They are the DEFINITION: two figures
    that agree on all of them are the same number, and two that differ on any
    one of them are not comparable, whatever their labels say. `pct` is
    computed from `numerator` and `denominator` rather than stored, so a
    published percentage that does not follow from its own counts fails
    `consistent()` instead of standing.

    `derived_numerator` marks a count that was BACK-DERIVED from a published
    percentage rather than counted. Those carry rounding of a few entities and
    may not be used as evidence of anything smaller than that.
    """

    definition_id: str
    label: str
    published: str                   # the figure exactly as it circulates
    #: The single percentage the stored counts must reproduce. Separate from
    #: `published` because three of these five circulate as a SERIES or a BAND
    #: ('25.35 / 35.20 / 41.35%', '~52-55%') and a series cannot be checked
    #: against one pair of counts. This is the member of the series that the
    #: stored counts belong to -- the anchor date for a per-date figure, the
    #: pooled value for a pooled one.
    anchor_pct: Optional[float]
    numerator: Optional[int]
    denominator: Optional[int]
    numerator_rule: str
    denominator_name: str
    denominator_unit: str            # entities | entity_dates | listing_rows
    allowed_concepts: tuple[str, ...]
    effective_concepts: tuple[str, ...]
    selector: str
    staleness_rule: str
    guard: str
    listing_gate: str
    as_of_grid: str
    produced_by: str                 # module:function that computes it
    recorded_at: str                 # module:constant where the number lives
    sample_scope: str
    derived_numerator: bool = False
    notes: tuple[str, ...] = ()

    @property
    def pct(self) -> Optional[float]:
        if not self.numerator or not self.denominator:
            return UNKNOWN
        return round(100.0 * self.numerator / self.denominator, 2)

    def consistent(self, tolerance: float = 0.05) -> bool:
        """Does the published figure follow from this definition's counts?

        `tolerance` is in percentage POINTS and defaults to half a basis point
        of a percentage -- enough for the rounding in a published figure, not
        enough for a different numerator.
        """
        published = (self.anchor_pct if self.anchor_pct is not None
                     else _first_pct(self.published))
        if published is None or self.pct is None:
            return True              # nothing to contradict
        return abs(self.pct - published) <= tolerance

    def as_dict(self) -> dict[str, Any]:
        out = dict(self.__dict__)
        out["pct_from_counts"] = self.pct
        out["consistent"] = self.consistent()
        return out


def _first_pct(text: str) -> Optional[float]:
    """The first percentage in a string, or None. '25.35/35.20%' -> 25.35."""
    match = re.search(r"(\d{1,3}(?:\.\d{1,2})?)\s*(?:/|%)", text)
    return float(match.group(1)) if match else None


# --------------------------------------------------------------------------
# The concepts. Two of them, and the third is a ghost -- see `verify_rung_
# reachability` and the module docstring's defect 3.
# --------------------------------------------------------------------------

CONCEPT_COVER = "dei:EntityCommonStockSharesOutstanding"
CONCEPT_GAAP = "us-gaap:CommonStockSharesOutstanding"
CONCEPT_WAVG = "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding"

STRICT_CONCEPTS = (CONCEPT_COVER, CONCEPT_GAAP)

#: The v1 staleness rule, spelled out once so five definitions can point at it.
V1_STALENESS = ("pit_policy.is_stale(period_end, as_of, qtrs, "
                "'shares_outstanding'): 4 months quarterly / 12 annual, exact "
                "calendar-month arithmetic from period_end, boundary inclusive")

#: The v2 rule. The ONLY change is the age test applied AFTER selection.
V2_STALENESS = ("pit_fact_kind.fact_kind_staleness_v2 STATE rung: a state fact "
                "PERSISTS UNTIL SUPERSEDED, subject to a 24-month sanity "
                "ceiling from period_end (STATE_SANITY_CEILING_MONTHS). "
                "Selection is unchanged -- same selector, same rows, same "
                "order; only the age question differs")

GUARD_ON = ("pit_rawprice.class_decision under CLASS_POLICY_STRICT "
            "(share_class_guard_v1): multi-class without a consistent "
            "weighted-average scale check is REFUSED; single class below a "
            "0.70 ratio is REFUSED as a probable hidden second class; any "
            "ratio outside [0.10, 10] is REFUSED as a units bug")
GUARD_OFF = "none -- a resolved count is counted, whatever class it describes"


# ==========================================================================
# (2) THE FOUR, RESTATED AS FOUR EVALUATIONS OF ONE FUNCTION
# ==========================================================================

DEF_LADDER_BASE_V1 = "SHARE_COV_A_LADDER_BASE_V1"
DEF_USABLE_PEERS_V1 = "SHARE_COV_B_USABLE_PEERS_V1"
DEF_DEFENSIBLE_PEERS_V1 = "SHARE_COV_C_DEFENSIBLE_PEERS_V1"
DEF_DEFENSIBLE_PRICED_V1 = "SHARE_COV_D1_DEFENSIBLE_PRICED_V1_POOLED"
DEF_STATE_PRICED_V2 = "SHARE_COV_D2_STATE_PRICED_V2_POOLED"

#: The three census dates every earlier phase quoted at. All three are June,
#: which the pooled measurement shows is the wrong month to ask in.
CENSUS_DATES = ("2015-06-30", "2019-06-28", "2024-06-28")

#: Denominators, measured. Peer universe and base are per-date entity counts;
#: the pooled figures are entity-DATES over the 165-date grid.
PEER_UNIVERSE = {"2015-06-30": 7950, "2019-06-28": 7102, "2024-06-28": 7311}
BASE_UNIVERSE = {"2015-06-30": 7073, "2019-06-28": 5959, "2024-06-28": 5944}
PRICED_ENTITIES = {"2015-06-30": 2034, "2019-06-28": 2423, "2024-06-28": 2443}
SCORED_ROWS = {"2015-06-30": 2065, "2019-06-28": 2455, "2024-06-28": 2475}

PEER_ENTITY_DATES = 1238663
PRICED_ENTITY_DATES = 377304
IN_SCOPE_COUNT_ENTITY_DATES = 297868   # priced entity-dates with any in-scope
                                       # count on any rung, before any age test

#: The per-date series behind definitions B and C, MEASURED and recorded in
#: `pit_rawprice`'s module docstring (2026-09-21, full peer universe). They are
#: carried here because two of the four figures were circulating as derived
#: percentages with no counts attached, which is how a band like "~52-55%"
#: survives next to a precise-looking "25.35%".
COUNT_RESOLVABLE = {"2015-06-30": 4410, "2019-06-28": 3912, "2024-06-28": 3795}
COUNT_AND_SYMBOL = {"2015-06-30": 2054, "2019-06-28": 2559, "2024-06-28": 3163}
DEFENSIBLE_PEERS = {"2015-06-30": 2015, "2019-06-28": 2500, "2024-06-28": 3023}

GRID = "165 month-end as-of dates, 2013-01-31 .. 2026-09-18"


DEFINITIONS: dict[str, CoverageDefinition] = {

    DEF_LADDER_BASE_V1: CoverageDefinition(
        definition_id=DEF_LADDER_BASE_V1,
        label="raw ladder coverage of the fact base",
        published="61.1 / 64.9 / 63.5%  (quoted as '~62%')",
        anchor_pct=61.1,
        numerator=4322,              # 0.611 x 7,073, +/- 4 from rounding
        denominator=7073,
        numerator_rule=(
            "base entities for which pit_coverage.resolve_concept returns a "
            "non-stale value for concept 'shares_outstanding'"),
        denominator_name=(
            "BASE UNIVERSE: peers with a usable non-stale total-assets fact "
            "(7,073 / 5,959 / 5,944). A company with no balance sheet cannot "
            "be scored by any model, so it is out of the denominator"),
        denominator_unit="entities",
        allowed_concepts=(CONCEPT_COVER, CONCEPT_GAAP, CONCEPT_WAVG),
        effective_concepts=STRICT_CONCEPTS,
        selector=(
            "pit_coverage.load_fact_index + resolve_concept: qtrs = 0 (the "
            "ladder is PERIOD_INSTANT), unit = 'shares', segments = '' and "
            "coreg = '', period_end within 1,170 days of as_of, "
            "available_date <= as_of; latest vintage per period, newest period "
            "wins, first non-stale rung wins"),
        staleness_rule=V1_STALENESS,
        guard=GUARD_OFF,
        listing_gate="none -- no symbol, no listing and no price required",
        as_of_grid="three census dates: " + ", ".join(CENSUS_DATES),
        produced_by="pit_coverage.measure_as_of -> row['shares_ok']",
        recorded_at="pit_derive.SHARES_NOTE / pit_derive.py:382",
        sample_scope="FULL_UNIVERSE (no price touches this figure)",
        derived_numerator=True,
        notes=(
            "The nominal third rung is unreachable: an INSTANT ladder is "
            "queried at qtrs = 0 and the weighted average is a duration fact. "
            "Measured: 0 of 7,950 peers rescued at 2015-06-30.",
            "This is the HIGHEST of the four and it is the one with the "
            "fewest requirements: no guard, no symbol, no price. It is a "
            "statement about the fact archive, not about computable market "
            "caps.",
        ),
    ),

    DEF_USABLE_PEERS_V1: CoverageDefinition(
        definition_id=DEF_USABLE_PEERS_V1,
        label="usable point-in-time count, peer universe",
        published="55.47 / 55.08 / 51.91%  (circulates as '~52-55%')",
        anchor_pct=55.47,
        numerator=4410,              # MEASURED live, not derived
        denominator=7950,
        numerator_rule=(
            "peers whose newest in-scope observation on the cover-page rung OR "
            "the balance-sheet rung is not stale ('ladder_fresh')"),
        denominator_name=(
            "PEER UNIVERSE: every issuer that was a live reporting company on "
            "the date (7,950 / 7,102 / 7,311). Survivorship-free; no ticker, "
            "no price, no listing required"),
        denominator_unit="entities",
        allowed_concepts=STRICT_CONCEPTS,
        effective_concepts=STRICT_CONCEPTS,
        selector=(
            "pit_sharecoverage.select_share_pit: eligible iff BOTH "
            "available_date <= as_of AND period_end <= as_of; newest period "
            "wins, ties to the later availability then the later filing. No "
            "qtrs, unit, segments or period-window filter"),
        staleness_rule=V1_STALENESS,
        guard=GUARD_OFF,
        listing_gate="none",
        as_of_grid="three census dates: " + ", ".join(CENSUS_DATES),
        produced_by=("pit_sharecoverage.coverage_at -> counts['ladder_fresh']; "
                     "same quantity as pit_rawprice.market_cap_coverage -> "
                     "tally['count_resolved']"),
        recorded_at=("pit_rawprice module docstring, 'point-in-time count "
                     "resolvable, of the peer universe' (4,410 / 3,912 / 3,795); "
                     "circulated as the band in pit_sharecoverage.coverage_at"),
        sample_scope="FULL_UNIVERSE",
        notes=(
            "Same numerator as the '~62%' figure. Measured at 2015-06-30 the "
            "two selectors return the same 4,410 peers -- not a similar count, "
            "the same set (verify_selectors: both = 4410). The figures differ "
            "because one divides by the base universe and the other by the "
            "peer universe, and because the base restriction drops 88 of those "
            "4,410 entities from its own numerator.",
            "4,410 is corroborated three ways: this module measured it live "
            "(verify_selectors), pit_rawprice's docstring records 4,410 / "
            "7,950 (55.47%) from market_cap_coverage, and pit_rawprice."
            "weighted_average_crosscheck records '2,140 of 4,410 resolvable "
            "counts at 2015-06-30' have no usable cross-check.",
            "The band '~52-55%' understates its own series: the measured "
            "members are 55.47 / 55.08 / 51.91%. A band is not a figure, and "
            "a band quoted as a figure is how '52%' entered circulation.",
        ),
    ),

    DEF_DEFENSIBLE_PEERS_V1: CoverageDefinition(
        definition_id=DEF_DEFENSIBLE_PEERS_V1,
        label="defensible count AND a symbol, peer universe",
        published="25.35 / 35.20 / 41.35%",
        anchor_pct=25.35,
        numerator=2015,              # 0.2535 x 7,950, +/- 4 from rounding
        denominator=7950,
        numerator_rule=(
            "peers with ALL THREE of: a non-stale count on the strict ladder; "
            "a point-in-time symbol observation (a price needs one); and a "
            "PASS from the share-class guard"),
        denominator_name="PEER UNIVERSE (7,950 / 7,102 / 7,311)",
        denominator_unit="entities",
        allowed_concepts=STRICT_CONCEPTS,
        effective_concepts=STRICT_CONCEPTS,
        selector=(
            "pit_rawprice.archive_shares_as_of -> pit_store.latest_period_as_of: "
            "qtrs = 0, unit matched to the concept, segments = '' and coreg = "
            "'', available_date <= as_of, ORDER BY period_end DESC. NOTE it "
            "does not test period_end <= as_of, which the pit_sharecoverage "
            "selector does; for instantaneous share facts the two agree"),
        staleness_rule=V1_STALENESS,
        guard=GUARD_ON,
        listing_gate=(
            "a point-in-time SYMBOL observation from pit_symbol_obs -- NOT a "
            "priced listing. The symbol ceiling is 43.61 / 63.18 / 68.10% of "
            "peers and is an early-adopter artefact of dei:TradingSymbol "
            "tagging before 2019, not a property of the market"),
        as_of_grid="three census dates: " + ", ".join(CENSUS_DATES),
        produced_by=("pit_rawprice.market_cap_coverage -> "
                     "pct_defensible_of_peers"),
        recorded_at=("pit_rawprice module docstring, 'DEFENSIBLE after the "
                     "share-class guard' (2,015 / 2,500 / 3,023); quoted "
                     "without its definition at pit_coverage.py:32 and "
                     "pit_derive.py:383"),
        sample_scope="FULL_UNIVERSE (symbol-gated, not price-gated)",
        notes=(
            "The lowest of the four, and the drop from 55.47% is almost "
            "entirely NOT a share problem. Measured at 2015-06-30: 4,410 "
            "peers have a usable count, 2,054 of those also have a symbol "
            "(the symbol costs 2,356), and the guard then refuses 39. The "
            "symbol gate is 98.4% of the fall.",
            "pit_sharecoverage records this series as UNRESOLVED against its "
            "own re-measurement (26.04 / 36.16 / 48.45%): 2015 and 2019 "
            "reproduce, 2024 does not, and the 'a later symbol ingest added "
            "rows' hypothesis was tested and rejected. The 2024 member of this "
            "series is therefore gated on something this project cannot "
            "currently name, and it must not be read as a trend with the "
            "other two.",
        ),
    ),

    DEF_DEFENSIBLE_PRICED_V1: CoverageDefinition(
        definition_id=DEF_DEFENSIBLE_PRICED_V1,
        label="defensible count, priced entity-dates, v1 staleness, pooled",
        published="53.70%",
        anchor_pct=53.7,
        numerator=202630,
        denominator=PRICED_ENTITY_DATES,
        numerator_rule=(
            "priced entity-dates with a non-stale count on the strict ladder "
            "that PASSES the share-class guard"),
        denominator_name=(
            "PRICED ENTITY-DATES: pit_identity.scored_universe_as_of, "
            "deduplicated to ENTITIES per date, summed over the grid "
            "(377,304 = the sum of 165 per-date priced entity counts)"),
        denominator_unit="entity_dates",
        allowed_concepts=STRICT_CONCEPTS,
        effective_concepts=STRICT_CONCEPTS,
        selector="pit_rawprice.archive_shares_as_of, per rung, fall-through",
        staleness_rule=V1_STALENESS,
        guard=GUARD_ON,
        listing_gate=(
            "a pit_listing whose validity window covers the date, a real bar "
            "with volume > 0 within 10 calendar days, and no quarantine "
            "(pit_identity.scored_universe_as_of)"),
        as_of_grid=GRID,
        produced_by="pit_fact_kind.measure_date -> record['v1']",
        recorded_at="pit_fact_kind.MEASURED['share_coverage']['pooled']",
        sample_scope=SAMPLE_SCOPE,
        notes=(
            "Carried here because it is the canonical figure's DIRECT "
            "comparator -- identical in every field but the staleness rule -- "
            "and because '53.70% -> 72.25%' is itself quoted.",
            "Month-to-month spread 44.17 points: 23.02% in January, 67.19% in "
            "April. The calendar, not the companies.",
        ),
    ),

    DEF_STATE_PRICED_V2: CoverageDefinition(
        definition_id=DEF_STATE_PRICED_V2,
        label="THE CANONICAL NUMBER: state-variable rule, priced, pooled",
        published="72.25%",
        anchor_pct=72.25,
        numerator=272587,
        denominator=PRICED_ENTITY_DATES,
        numerator_rule=(
            "priced entity-dates whose newest in-scope count on the strict "
            "ladder is within the 24-month state ceiling, is non-zero, and "
            "PASSES the share-class guard"),
        denominator_name=(
            "PRICED ENTITY-DATES: pit_identity.scored_universe_as_of, "
            "deduplicated to ENTITIES per date, summed over the grid (377,304)"),
        denominator_unit="entity_dates",
        allowed_concepts=STRICT_CONCEPTS,
        effective_concepts=STRICT_CONCEPTS,
        selector=(
            "pit_rawprice.archive_shares_as_of with enforce_staleness=False, "
            "once per rung, ladder order; UNCHANGED from v1 -- v2 alters only "
            "the age test applied to the row the selector returned"),
        staleness_rule=V2_STALENESS,
        guard=GUARD_ON,
        listing_gate=(
            "a pit_listing whose validity window covers the date, a real bar "
            "with volume > 0 within 10 calendar days, and no quarantine"),
        as_of_grid=GRID,
        produced_by="pit_fact_kind.measure_date -> record['v2']",
        recorded_at="pit_fact_kind.MEASURED['share_coverage']['pooled']",
        sample_scope=SAMPLE_SCOPE,
        notes=(
            "CANDIDATE lineage (candidate_equity_shaffer_v2). The production "
            "v1 policy is frozen and unedited; this is a second policy beside "
            "it, not a replacement of it.",
            "Month-to-month spread 0.67 points against v1's 44.17. The "
            "seasonal artefact is gone, which is what makes a single pooled "
            "number meaningful at all -- a figure that reads 23% in January "
            "and 67% in April cannot be quoted as one number honestly.",
            "It does not fix only_period_average_available: 48,956 refused "
            "entity-dates, unchanged to the row, now 46.8% of all refusals and "
            "the largest single one. That is a FETCH problem, not an age one.",
        ),
    ),
}

CANONICAL_DEFINITION_ID = DEF_STATE_PRICED_V2
CANONICAL = DEFINITIONS[CANONICAL_DEFINITION_ID]

#: THE NUMBER. Import this; do not retype it. It is a percentage of the priced
#: entity-dates on the 165-date grid -- see `CANONICAL` for what that means and
#: `canonical_block()` for the statement a quote should carry with it.
PIT_SHARE_STATE_COVERAGE_V2 = 72.25


def definition(definition_id: str) -> CoverageDefinition:
    try:
        return DEFINITIONS[definition_id]
    except KeyError:
        raise UndefinedQuote(
            "unknown definition %r; known: %s"
            % (definition_id, ", ".join(sorted(DEFINITIONS)))) from None


# ==========================================================================
# (3) THE PUBLISHED BLOCK, in the shape the owner asked for
# ==========================================================================

_LABEL_WIDTH = 21


def _wrap(label: str, value: str, width: int = 96) -> list[str]:
    """One block line, continuation indented to the value column."""
    pad = " " * (_LABEL_WIDTH + 1)
    words, lines, current = value.split(), [], (label + ":").ljust(_LABEL_WIDTH) + " "
    room = width - len(current)
    for word in words:
        if room - len(word) - 1 < 0 and current.strip() != (label + ":"):
            lines.append(current.rstrip())
            current, room = pad, width - len(pad)
        current += word + " "
        room -= len(word) + 1
    lines.append(current.rstrip())
    return lines


def canonical_block(width: int = 96) -> str:
    """The canonical number and its definition, as one quotable block.

    This is what a report pastes. It is generated from `CANONICAL` rather than
    typed, so the block and the constant cannot drift: change the definition
    and the block changes, or the test fails.
    """
    rows = [
        ("Universe", CANONICAL.denominator_name + ". Numerator: "
                     + CANONICAL.numerator_rule
                     + ". {:,} of {:,} entity-dates.".format(
                         CANONICAL.numerator or 0, CANONICAL.denominator or 0)),
        ("As-of grid", CANONICAL.as_of_grid
                       + ", pooled (not averaged across dates): the numerator "
                         "and denominator are summed over the grid, so a date "
                         "with more priced names weighs more, which is what a "
                         "replay would experience."),
        ("Allowed concepts", " then ".join(CANONICAL.effective_concepts)
                             + ". The period-average tag "
                             + CONCEPT_WAVG
                             + " is NOT admitted at any age: it is a duration "
                               "average built for per-share arithmetic, never "
                               "a count on a date."),
        ("Staleness/state rule", CANONICAL.staleness_rule + "."),
        ("Exclusions", EXCLUSIONS_LINE),
    ]
    lines = ["%s = %s%%" % ("PIT_SHARE_STATE_COVERAGE_V2",
                            PIT_SHARE_STATE_COVERAGE_V2)]
    for label, value in rows:
        lines.extend(_wrap(label, value, width))
    return "\n".join(lines)


EXCLUSIONS_LINE = (
    "(a) entity-dates with no price are never in the denominator -- roughly "
    "4,900 peers per date, so this number says NOTHING about them; "
    "(b) refused and counted as uncovered: only a period average 48,956, "
    "newest count beyond the 24-month ceiling 18,328, never filed a share "
    "count 17,279, not yet filed 13,201, guard refusals 6,248, zero count 705; "
    "(c) per-class (dimensional) XBRL rows were dropped at ingest and cannot be "
    "summed here, so a multi-class issuer tagging only per class is uncovered "
    "rather than wrong; "
    "(d) SURVIVOR_ONLY_DIAGNOSTIC -- the priced universe is drawn from 2,574 "
    "listings all alive in 2026, so the true rate is LOWER by an unmeasured "
    "amount; "
    "(e) CANDIDATE lineage (candidate_equity_shaffer_v2), not production; the "
    "frozen v1 policy is unchanged and its own figure is "
    + DEF_DEFENSIBLE_PRICED_V1 + " = 53.70%.")


# ==========================================================================
# (4) THE RULE. A legacy figure may not be quoted without its definition.
# ==========================================================================

class UndefinedQuote(ValueError):
    """A share-coverage figure was quoted without naming what it measures."""


#: Every spelling in circulation, mapped to the definition it belongs to. A
#: reader who meets '52%' looks it up here rather than guessing, and
#: `assert_quotable` refuses a text that uses one of these without naming its
#: definition. Spellings are normalised by `_normalise_figure`, so '62%',
#: '62.0 %' and '~62%' are one key.
LEGACY_FIGURES: dict[str, str] = {
    "62": DEF_LADDER_BASE_V1,
    "61.1": DEF_LADDER_BASE_V1,
    "64.9": DEF_LADDER_BASE_V1,
    "63.5": DEF_LADDER_BASE_V1,
    "52": DEF_USABLE_PEERS_V1,
    "53": DEF_USABLE_PEERS_V1,
    "54": DEF_USABLE_PEERS_V1,
    "55": DEF_USABLE_PEERS_V1,
    "55.47": DEF_USABLE_PEERS_V1,
    "25.35": DEF_DEFENSIBLE_PEERS_V1,
    "35.2": DEF_DEFENSIBLE_PEERS_V1,
    "41.35": DEF_DEFENSIBLE_PEERS_V1,
    "26.04": DEF_DEFENSIBLE_PEERS_V1,
    "36.16": DEF_DEFENSIBLE_PEERS_V1,
    "48.45": DEF_DEFENSIBLE_PEERS_V1,
    "53.7": DEF_DEFENSIBLE_PRICED_V1,
    "72.25": DEF_STATE_PRICED_V2,
    "74.09": DEF_STATE_PRICED_V2,   # the guard-off variant; see guard_sensitivity
}

#: Naming the constant is as good as naming the definition id, for the one
#: figure that has a constant.
_CONSTANT_ALIAS = {DEF_STATE_PRICED_V2: "PIT_SHARE_STATE_COVERAGE_V2"}

_PCT_RE = re.compile(r"(\d{1,3}(?:\.\d{1,2})?)\s*%")


def _normalise_figure(figure: Any) -> str:
    """'~62%', '62.0 %', 62.0 -> '62'. Trailing zeros are not a definition."""
    text = str(figure).strip().lstrip("~").rstrip("%").strip()
    try:
        value = float(text)
    except ValueError:
        raise UndefinedQuote("not a percentage: %r" % (figure,)) from None
    text = ("%.2f" % value).rstrip("0").rstrip(".")
    return text or "0"


def lookup(figure: Any) -> CoverageDefinition:
    """What did this figure mean? Raises if it is not one this project used.

    The point of the registry: a reader who encounters '52%' in an old
    docstring can ask here instead of assuming it is comparable with 72.25%.
    """
    key = _normalise_figure(figure)
    if key not in LEGACY_FIGURES:
        raise UndefinedQuote(
            "%s%% is not a registered share-coverage figure. Registered: %s"
            % (key, ", ".join(sorted(LEGACY_FIGURES, key=float))))
    return DEFINITIONS[LEGACY_FIGURES[key]]


def quote(figure: Any, definition_id: Optional[str] = None) -> str:
    """Render a quotable citation, or refuse.

    A figure is quotable only WITH its definition. Passing the wrong
    definition id raises rather than relabelling the number, because a
    mislabelled coverage figure is worse than an unlabelled one: it invites a
    comparison that cannot be made.
    """
    spec = lookup(figure)
    if definition_id is not None and definition_id != spec.definition_id:
        raise UndefinedQuote(
            "%s%% belongs to %s, not %s -- these measure different "
            "numerators over different denominators and may not be compared"
            % (_normalise_figure(figure), spec.definition_id, definition_id))
    # The anchor counts are the definition's OWN counts and are not always the
    # counts behind the spelling being quoted -- '52%' is the bottom of a
    # three-date band whose anchor date reads 55.47%. Saying "anchor" rather
    # than "numerator" is the difference between a citation and a claim.
    return ("%s%% [%s] %s -- published as %s; anchor counts %s / %s %s; "
            "denominator: %s" % (
                _normalise_figure(figure), spec.definition_id, spec.label,
                spec.published,
                spec.numerator if spec.numerator is not None else "UNKNOWN",
                spec.denominator if spec.denominator is not None else "UNKNOWN",
                spec.denominator_unit,
                spec.denominator_name.split(":")[0]))


def assert_quotable(text: str) -> None:
    """Refuse a text that quotes a registered figure without naming it.

    The enforceable form of the owner's rule. Run it over a docstring, a
    report body or a commit message: every percentage that matches a figure in
    `LEGACY_FIGURES` must appear alongside its definition id (or, for the
    canonical one, the constant name). Percentages that are not registered
    share-coverage figures are ignored -- this guard is not a general ban on
    percentages, it is a ban on quoting THESE five numbers namelessly.
    """
    offenders: list[str] = []
    for raw in _PCT_RE.findall(text):
        key = _normalise_figure(raw)
        spec_id = LEGACY_FIGURES.get(key)
        if spec_id is None:
            continue
        alias = _CONSTANT_ALIAS.get(spec_id)
        if spec_id in text or (alias and alias in text):
            continue
        offenders.append("%s%% (%s)" % (key, spec_id))
    if offenders:
        raise UndefinedQuote(
            "share-coverage figures quoted without their definitions: "
            + "; ".join(sorted(set(offenders)))
            + ". Name the definition id, or use pit_share_coverage.quote().")


# ==========================================================================
# (5) RECONCILIATION -- the four as one ladder, each step named
# ==========================================================================

@dataclass(frozen=True)
class Step:
    """One move along the reconciliation, and what it cost or bought."""

    step: str
    numerator: Optional[int]
    denominator: Optional[int]
    moved_by: str                    # denominator | eligibility | rule | grid
    what_changed: str
    #: The definition this step IS, set only when the step lands exactly on a
    #: published figure. None for an intermediate: the per-date members of the
    #: two POOLED series are not those series, and labelling them so would
    #: recreate the confusion this module exists to end.
    #: `related_definition_id` keeps the link without claiming the identity.
    definition_id: Optional[str] = None
    related_definition_id: Optional[str] = None
    measured: str = "measured"

    @property
    def pct(self) -> Optional[float]:
        if not self.numerator or not self.denominator:
            return UNKNOWN
        return round(100.0 * self.numerator / self.denominator, 2)

    def as_dict(self) -> dict[str, Any]:
        out = dict(self.__dict__)
        out["pct"] = self.pct
        return out


def reconciliation(as_of: str = "2015-06-30") -> dict[str, Any]:
    """The four figures as one walk, at one date and then pooled.

    2015-06-30 is the anchor because it is the only date at which every one of
    the four has a published member AND the numerator identity was measured
    live. The last two steps leave the date behind, because the canonical
    number is pooled over the grid and that is itself one of the choices.
    """
    if as_of != "2015-06-30":
        raise ValueError(
            "only 2015-06-30 is reconciled step by step: it is the date at "
            "which verify_selectors measured the shared numerator. The other "
            "census dates have published figures but no measured bridge.")
    peers, base = PEER_UNIVERSE[as_of], BASE_UNIVERSE[as_of]
    steps = [
        Step("fresh count, peer universe", 4410, peers, "start",
             "A non-stale point-in-time count on the cover-page or "
             "balance-sheet rung. No guard, no symbol, no price. This is the "
             "quantity all four figures are built from.",
             DEF_USABLE_PEERS_V1),
        Step("... restricted to the fact base", 4322, base, "denominator",
             "Two changes at once, and they pull the same way: the "
             "denominator falls from 7,950 peers to 7,073 base companies "
             "(-877) and the numerator loses the 88 entities that have a "
             "fresh count but no usable total-assets fact. NOTHING ABOUT "
             "SHARE DATA CHANGED. +5.63 points.",
             DEF_LADDER_BASE_V1, measured="numerator back-derived from 61.1%"),
        Step("... require a SYMBOL too", 2054, peers, "eligibility",
             "Back to the peer denominator, with one new requirement: a "
             "point-in-time symbol observation, which a price needs. It costs "
             "2,356 of the 4,410 -- an artefact of pre-2019 dei:TradingSymbol "
             "tagging rather than a property of the market. -29.63 points, "
             "and not one of them is a share-count problem.",
             None, DEF_DEFENSIBLE_PEERS_V1),
        Step("... and pass the share-class guard", 2015, peers, "eligibility",
             "The guard refuses 39 more: a count that is one class of a "
             "multi-class issuer, or off-scale against the weighted average. "
             "-0.49 points. The guard is 1.6% of the fall from 55.47% and the "
             "symbol gate is 98.4% of it.",
             DEF_DEFENSIBLE_PEERS_V1),
        Step("... require a PRICE instead of a symbol", 1185,
             PRICED_ENTITIES[as_of], "denominator+eligibility",
             "The replay's actual gate: a listing whose window covers the "
             "date, a real bar with volume > 0 within 10 days, no quarantine. "
             "The denominator collapses from 7,950 to 2,034 and the figure "
             "RISES to 58.26%, because priced survivors are far more likely to "
             "have a resolvable count than the peer universe at large. Same "
             "share policy, same guard, +32.91 points. PER-DATE: not the "
             "pooled 53.70% figure, which is the same rule over 165 dates.",
             None, DEF_DEFENSIBLE_PRICED_V1),
        Step("... apply the v2 state rule at this date", 1432,
             PRICED_ENTITIES[as_of], "rule",
             "Shares outstanding is a STATE variable: it persists until "
             "superseded, subject to a 24-month sanity ceiling, instead of "
             "expiring at 4 months. Selection is untouched. +12.14 points at "
             "this date. PER-DATE: not the canonical 72.25%, which is the "
             "same rule over 165 dates.",
             None, DEF_STATE_PRICED_V2),
        Step("... pool over the 165-date grid", 202630, PRICED_ENTITY_DATES,
             "grid", "v1, pooled: 53.70%. Lower than v1 at this June date "
             "(58.26%) because June is a good month and January (23.02%) and "
             "October (27.47%) are not.",
             DEF_DEFENSIBLE_PRICED_V1),
        Step("... v2, pooled: THE CANONICAL NUMBER", 272587,
             PRICED_ENTITY_DATES, "rule+grid",
             "+18.54 points pooled against +12.14 at this date, because the "
             "hole the state rule closes lives in the months the census dates "
             "avoid. Month-to-month spread 44.17 -> 0.67 points.",
             DEF_STATE_PRICED_V2),
    ]
    return {
        "anchor_date": as_of,
        "claim": ("Four figures, one quantity, four definitions. Each step "
                  "changes exactly what it names."),
        "steps": [s.as_dict() for s in steps],
        "differ_only_by_denominator": [DEF_LADDER_BASE_V1, DEF_USABLE_PEERS_V1],
        "differ_by_eligibility": [DEF_DEFENSIBLE_PEERS_V1, DEF_DEFENSIBLE_PRICED_V1],
        "differ_by_rule": [DEF_DEFENSIBLE_PRICED_V1, DEF_STATE_PRICED_V2],
        "sample_scope": ("the last four steps are " + SAMPLE_SCOPE
                         + "; the first three are not price-gated"),
    }


# ==========================================================================
# (6) SENSITIVITY
# ==========================================================================

#: Entity-dates ADMITTED by each candidate sanity ceiling, measured over the
#: grid. NOT the same quantity as the canonical numerator -- see
#: CEILING_CURVE_METHOD_GAP, which is why this is reported as its own series.
CEILING_SENSITIVITY_ADMITTED: dict[str, int] = {
    "4m (v1 quarterly)": 206817,
    "6m": 249624,
    "9m": 259027,
    "12m": 266188,
    "18m": 272308,
    "24m (v2, chosen)": 274439,
    "36m": 277994,
    "48m": 281069,
    "unbounded": 297868,
}

#: The guard's measured attrition, used to translate the pre-guard ceiling
#: curve into the canonical's units. It is an ASSUMPTION of uniformity across
#: ceilings, stated rather than hidden, and it is exact at the two ends.
GUARD_ATTRITION_V2 = 6953          # guard refusals + zero counts, v2
V2_RESOLVED_ENTITY_DATES = 279540  # 272,587 defensible + 6,953 refused
V1_RESOLVED_ENTITY_DATES = 206817
V1_DEFENSIBLE_ENTITY_DATES = 202630

CEILING_CURVE_METHOD_GAP = {
    "curve_at_24m": 274439,
    "v2_resolved_at_24m": V2_RESOLVED_ENTITY_DATES,
    "gap_entity_dates": V2_RESOLVED_ENTITY_DATES - 274439,
    "gap_pct_of_curve": round(
        100.0 * (V2_RESOLVED_ENTITY_DATES - 274439) / 274439, 2),
    "why": (
        "The two are computed differently and neither is wrong. The CURVE "
        "counts entity-dates whose LEADING in-scope rung is younger than "
        "months x 30.44 days (pit_fact_kind stores picks[0]['age_days'] and "
        "thresholds it arithmetically). The POLICY walks the ladder -- if the "
        "cover-page rung is beyond the ceiling it falls through to the "
        "balance-sheet rung -- and uses exact calendar-month arithmetic. "
        "Ladder fall-through plus the 30.44-vs-calendar difference admits "
        "5,101 more entity-dates than the curve shows at 24 months."),
    "consequence": (
        "Read the curve for SHAPE, not for level. Its 4m point is exact (it "
        "is v1's own resolved count by construction) and its 24m point "
        "understates the chosen policy by 1.86%."),
}


def ceiling_sensitivity() -> dict[str, Any]:
    """How the canonical number moves with the sanity ceiling.

    Two columns, and the difference between them is the honesty: `admitted`
    is measured, `defensible_est` applies the measured guard attrition to it
    and is an ESTIMATE everywhere except the 4m end, where it is the measured
    v1 figure.
    """
    keep = 1.0 - GUARD_ATTRITION_V2 / float(V2_RESOLVED_ENTITY_DATES)
    rows = []
    for label, admitted in CEILING_SENSITIVITY_ADMITTED.items():
        exact = label.startswith("4m")
        defensible = V1_DEFENSIBLE_ENTITY_DATES if exact else int(round(admitted * keep))
        chosen = label.startswith("24m")
        rows.append({
            "ceiling": label,
            "admitted_entity_dates": admitted,
            "admitted_pct_of_priced": round(100.0 * admitted / PRICED_ENTITY_DATES, 2),
            "defensible_entity_dates": defensible,
            "defensible_pct": round(100.0 * defensible / PRICED_ENTITY_DATES, 2),
            "basis": ("measured" if exact else
                      # The chosen ceiling is the one row a reader will compare
                      # with the headline, so it says outright that the curve
                      # is not the headline and by how much.
                      "estimated; MEASURED under this ceiling is 272,587 "
                      "(72.25%) -- the curve understates it by 1.86%, see "
                      "method_gap" if chosen else
                      "estimated (uniform guard attrition)"),
        })
    return {
        "lever": "sanity ceiling on a STATE fact",
        "definition_id": CANONICAL_DEFINITION_ID,
        "chosen": "24 months",
        "rows": rows,
        "guard_keep_rate": round(keep, 5),
        "shape": ("Steep where the artefact is, flat where the ceiling sits: "
                  "4 -> 12 months buys 59,371 entity-dates, 12 -> 24 buys "
                  "8,251, 24 -> 36 buys 3,555. So the ceiling is not a "
                  "coverage lever, which is what a sanity bound should be."),
        "if_removed": ("Unbounded would admit 23,429 more entity-dates whose "
                       "newest count is over two years old. At 2024-06-28 the "
                       "age of the newest in-scope count has p95 3,102 days "
                       "(8.5 years) and a maximum of 5,354 days (14.7 years). "
                       "That p95 is the '2011 count answering a 2024 query'."),
        "method_gap": CEILING_CURVE_METHOD_GAP,
    }


def denominator_sensitivity() -> dict[str, Any]:
    """THE SAME v2 numerator over every denominator anyone has used.

    This is the lever that matters. 272,587 entity-dates is one measured
    quantity; which denominator it is divided by moves the published figure
    from 22% to 92%, and no share-data question is asked anywhere in that
    range. It is the arithmetic form of the owner's instruction.
    """
    numerator = CANONICAL.numerator or 0
    options = [
        ("priced entity-dates (CANONICAL)", PRICED_ENTITY_DATES,
         "what a replay iterates: an entity-date with a listing, a fresh bar "
         "and no quarantine"),
        ("entity-dates with any in-scope count", IN_SCOPE_COUNT_ENTITY_DATES,
         "priced entity-dates minus never-filed, not-yet-filed and "
         "period-average-only. Answers 'of the counts we could have used, how "
         "many did we', which flatters the policy by excluding the issuers it "
         "cannot help"),
        ("peer entity-dates", PEER_ENTITY_DATES,
         "every live reporting company on every grid date, priced or not. "
         "Honest about the whole store and useless for the replay: four "
         "fifths of it can never produce a market cap for want of a price"),
        ("base entity-dates (pooled)", UNKNOWN,
         "peers with a usable non-stale total-assets fact, pooled over the "
         "grid. Measured at three dates only (7,073 / 5,959 / 5,944) and "
         "UNKNOWN pooled -- reported as UNKNOWN, never as zero"),
        ("scored-universe ROWS", UNKNOWN,
         "the listing-level count (2,065 / 2,455 / 2,475 at the census "
         "dates). NOT an entity count and never a valid denominator for an "
         "entity-keyed numerator: an issuer with two lines would be counted "
         "twice on one side of the ratio and once on the other"),
    ]
    rows = []
    for name, denominator, why in options:
        rows.append({
            "denominator": name,
            "n": denominator,
            "pct": (round(100.0 * numerator / denominator, 2)
                    if denominator else UNKNOWN),
            "why": why,
        })
    return {
        "lever": "denominator",
        "definition_id": CANONICAL_DEFINITION_ID,
        "numerator_held_fixed": numerator,
        "rows": rows,
        "spread_points": round(
            100.0 * numerator / IN_SCOPE_COUNT_ENTITY_DATES
            - 100.0 * numerator / PEER_ENTITY_DATES, 2),
        "reading": ("One numerator, 69.5 points of spread. The denominator is "
                    "worth more than the staleness rule (18.54 points), the "
                    "guard (1.84) and the whole sanity ceiling (24.14 from 4m "
                    "to unbounded) put together, which is why it is the first "
                    "line of the published block."),
    }


def guard_sensitivity() -> dict[str, Any]:
    """The share-class guard, on and off, under both rules. All measured."""
    def row(label: str, resolved: int, defensible: int) -> dict[str, Any]:
        return {
            "policy": label,
            "guard_off_entity_dates": resolved,
            "guard_off_pct": round(100.0 * resolved / PRICED_ENTITY_DATES, 2),
            "guard_on_entity_dates": defensible,
            "guard_on_pct": round(100.0 * defensible / PRICED_ENTITY_DATES, 2),
            "cost_points": round(
                100.0 * (resolved - defensible) / PRICED_ENTITY_DATES, 2),
        }
    return {
        "lever": "share-class guard (pit_rawprice.class_decision, strict)",
        "definition_id": CANONICAL_DEFINITION_ID,
        "rows": [row("v1 staleness", V1_RESOLVED_ENTITY_DATES,
                     V1_DEFENSIBLE_ENTITY_DATES),
                 row("v2 state rule", V2_RESOLVED_ENTITY_DATES,
                     CANONICAL.numerator or 0)],
        "why_it_costs_more_under_v2": (
            "The guard refuses MORE in absolute terms under v2 (4,187 -> "
            "6,953) because v2 hands it 72,723 more counts to judge. That is "
            "the price of the rescue and it is stated, not netted away."),
        "why_it_stays_on": (
            "The error it prevents is silent and large: the measured 10th "
            "percentile of outstanding/diluted for proved multi-class issuers "
            "is 0.18, a 5.5x understatement of market cap, and the store "
            "cannot say which of the 46% of multi-class issuers with an "
            "undimensioned count are affected."),
    }


def sensitivity() -> dict[str, Any]:
    """All three levers, plus the ones that cannot be quantified."""
    return {
        "canonical": PIT_SHARE_STATE_COVERAGE_V2,
        "canonical_definition_id": CANONICAL_DEFINITION_ID,
        "ceiling": ceiling_sensitivity(),
        "denominator": denominator_sensitivity(),
        "guard": guard_sensitivity(),
        "unquantified": {
            "survivorship": (
                "UNKNOWN and one-directional. The priced universe is 2,574 "
                "listings all alive in 2026; the issuers missing from it are "
                "the ones whose counts are hardest, so the true rate is LOWER. "
                "No bound is available without a dead-issuer price sample."),
            "per_class_ingest": (
                "UNKNOWN. 17,851 dimensional CommonStockSharesOutstanding rows "
                "in 2024q2 alone were dropped at ingest against 11,203 "
                "undimensioned, and 1,094 accessions in that quarter have no "
                "undimensioned row at all. Recovering them is a re-read of the "
                "DERA archive, not a policy change, and it would move this "
                "number by an amount nobody has measured."),
            "cover_page_tag": (
                "UNKNOWN. DERA's compact num.txt drops cover-page facts: 606 "
                "rows of dei:EntityCommonStockSharesOutstanding in the whole "
                "archive against 589,989 balance-sheet counts. A targeted "
                "companyfacts fetch recovered a fresh count for 48.3% of a "
                "60-issuer probe that had none."),
        },
    }


# ==========================================================================
# (7) MEASURED. Everything above, with its provenance, as one record.
# ==========================================================================

MEASURED: dict[str, Any] = {
    "measured_on": MEASURED_ON,
    "sample_scope": SAMPLE_SCOPE,
    "sample_scope_note": SAMPLE_SCOPE_NOTE,
    "per_date_series": {
        "source": ("pit_rawprice module docstring, measured 2026-09-21 by "
                   "market_cap_coverage over the full peer universe"),
        "count_resolvable": COUNT_RESOLVABLE,
        "count_and_symbol": COUNT_AND_SYMBOL,
        "defensible_after_guard": DEFENSIBLE_PEERS,
        "at_2015_06_30": ("4,410 have a count; 2,054 also have a symbol (the "
                          "symbol costs 2,356); 2,015 survive the guard (the "
                          "guard costs 39). The symbol gate is 98.4% of the "
                          "fall and the guard 1.6%."),
    },
    "denominators": {
        "peer_universe": PEER_UNIVERSE,
        "base_universe": BASE_UNIVERSE,
        "priced_entities": PRICED_ENTITIES,
        "scored_universe_rows": SCORED_ROWS,
        "peer_entity_dates": PEER_ENTITY_DATES,
        "priced_entity_dates": PRICED_ENTITY_DATES,
        "in_scope_count_entity_dates": IN_SCOPE_COUNT_ENTITY_DATES,
        "rows_are_not_entities": (
            "pit_identity.scored_universe_as_of returns one row PER LISTING. "
            "2,065 / 2,455 / 2,475 rows are 2,034 / 2,423 / 2,443 entities. "
            "Verified live at all three dates by verify_denominators."),
    },
    #: Measured live by `verify_selectors` on 2026-09-21, 131.1s, read-only,
    #: WAL 0 bytes throughout. The finding that collapses two of the four.
    "selector_identity_2015_06_30": {
        "n_peers": 7950,
        "fresh_pit_coverage_selector": 4410,
        "fresh_pit_sharecoverage_selector": 4410,
        "in_both": 4410,
        "weighted_average_rung_rescues": 0,
        "seconds": 131.1,
        "reading": ("The two selectors differ in four filters -- qtrs, unit, "
                    "segments/coreg and a 1,170-day period window -- and "
                    "return the same set. The '~62%' and '~52-55%' figures "
                    "are therefore one numerator over two denominators."),
    },
    "rung_reachability_sample": {
        "n_entities": 400,
        "method": "entity-keyed index seeks; no full scan of pit_fact",
        "rows_by_qtrs": {
            CONCEPT_GAAP: {"0": 21503},
            CONCEPT_COVER: {"0": 38},
            CONCEPT_WAVG: {"0": 16, "1": 25528, "2": 7710, "3": 7675, "4": 10753},
        },
        "reading": ("0.03% of weighted-average rows carry qtrs = 0, and an "
                    "INSTANT ladder is queried at qtrs = 0, so the third rung "
                    "of pit_policy's shares_outstanding ladder is unreachable "
                    "in pit_coverage's census. It rescued 0 of 7,950 peers at "
                    "2015-06-30."),
    },
    "guard": {
        "v1_refusals": 4187, "v2_refusals": GUARD_ATTRITION_V2,
        "policy_version": "share_class_guard_v1",
    },
    "seasonality_points": {"v1_range": 44.17, "v2_range": 0.67},
    "downstream": {
        "ev_ebitda_p_ge_3": {"v1": 34.19, "v2_whole_policy": 46.01},
        "note": ("Quoted for context only. These are cohort-size "
                 "probabilities, not share coverage, and they are not "
                 "registered in LEGACY_FIGURES."),
    },
}


# ==========================================================================
# (8) LIVE VERIFICATION. Cheap, read-only, and it checks the DENOMINATORS --
# which is where the confusion was -- rather than re-running the census.
# ==========================================================================

def connect_readonly(db_path: Optional[str] = None) -> sqlite3.Connection:
    """`mode=ro`, so the no-write rule is held by the file handle."""
    if db_path is None:
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "shafferfineval_pit.db")
    uri = "file:" + os.path.abspath(db_path).replace("\\", "/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def verify_denominators(conn: sqlite3.Connection,
                        dates: Sequence[str] = CENSUS_DATES) -> dict[str, Any]:
    """Re-count the published denominators. ~3s per date, three SELECTs each.

    Deliberately the DENOMINATORS and not the numerators: a numerator costs
    minutes per date and has been measured three times already, while the
    denominators are cheap, are where two of the four figures disagreed, and
    are the half of a ratio nobody checks.
    """
    import pit_identity

    out: dict[str, Any] = {"grid_dates": int(conn.execute(
        "SELECT COUNT(DISTINCT as_of_date) FROM pit_peer_set").fetchone()[0]),
        "per_date": {}}
    for day in dates:
        peers = pit_identity.peer_universe_as_of(conn, day)
        scored = pit_identity.scored_universe_as_of(conn, day)
        entities = {int(r["entity_id"]) for r in scored}
        members = int(conn.execute(
            """SELECT COUNT(DISTINCT m.entity_id)
                 FROM pit_peer_member m
                 JOIN pit_peer_set s ON s.peer_set_id = m.peer_set_id
                WHERE s.as_of_date = ?""", (day,)).fetchone()[0])
        out["per_date"][day] = {
            "peer_universe": len(peers),
            "peer_set_membership": members,
            "scored_rows": len(scored),
            "scored_entities": len(entities),
            "rows_minus_entities": len(scored) - len(entities),
            "published_peer": PEER_UNIVERSE.get(day),
            "published_priced_entities": PRICED_ENTITIES.get(day),
            "published_scored_rows": SCORED_ROWS.get(day),
        }
    return out


def verify_rung_reachability(conn: sqlite3.Connection,
                             n_entities: int = 400) -> dict[str, Any]:
    """Is the weighted-average rung reachable at qtrs = 0? Sampled, ~10s.

    Entity-keyed index seeks on purpose: `pit_fact` has no index on `tag`, so
    a census-wide answer costs a 14M-row scan per tag, and the question --
    "does this tag ever carry qtrs = 0" -- is answered by a sample with its
    size stated.
    """
    ids = [int(r[0]) for r in conn.execute(
        "SELECT entity_id FROM pit_entity ORDER BY entity_id LIMIT ?",
        (int(n_entities),))]
    tags = ("WeightedAverageNumberOfDilutedSharesOutstanding",
            "CommonStockSharesOutstanding", "EntityCommonStockSharesOutstanding")
    tally: dict[str, dict[str, int]] = {t: {} for t in tags}
    for entity_id in ids:
        for tag, qtrs, n in conn.execute(
                "SELECT tag, qtrs, COUNT(*) FROM pit_fact "
                "WHERE entity_id = ? AND tag IN (?, ?, ?) GROUP BY tag, qtrs",
                (entity_id, *tags)):
            bucket = tally[tag]
            bucket[str(qtrs)] = bucket.get(str(qtrs), 0) + int(n)
    wavg = tally[tags[0]]
    total = sum(wavg.values())
    return {
        "n_entities": len(ids),
        "rows_by_qtrs": tally,
        "weighted_average_rows": total,
        "weighted_average_qtrs0": wavg.get("0", 0),
        "weighted_average_qtrs0_pct": (
            round(100.0 * wavg.get("0", 0) / total, 3) if total else UNKNOWN),
        "reachable_at_qtrs_0": wavg.get("0", 0) > 0,
        "materially_reachable": (
            total > 0 and (wavg.get("0", 0) / total) > 0.01),
    }


def verify_selectors(conn: sqlite3.Connection, as_of: str = "2015-06-30"
                     ) -> dict[str, Any]:
    """Do the two v1 selectors return the same peers? EXPENSIVE: ~131s.

    The measurement behind `MEASURED['selector_identity_2015_06_30']`, kept
    runnable rather than only recorded, because the claim it supports -- that
    two of the four figures differ by denominator alone -- is the load-bearing
    one. One indexed query per peer; no full scan; no transaction held.
    """
    import pit_policy

    peers = [int(r["entity_id"]) for r in _peer_ids(conn, as_of)]
    low = (_dt.date.fromisoformat(as_of) - _dt.timedelta(days=1170)).isoformat()
    cover, gaap, wavg = ("EntityCommonStockSharesOutstanding",
                         "CommonStockSharesOutstanding",
                         "WeightedAverageNumberOfDilutedSharesOutstanding")
    sql = ("SELECT tag, period_end, available_date, qtrs, unit, segments, coreg "
           "FROM pit_fact WHERE entity_id = ? AND tag IN (?, ?, ?)")
    n_b = n_a = n_both = n_rung3 = 0
    for entity_id in peers:
        rows_b: dict[str, list] = {cover: [], gaap: [], wavg: []}
        rows_a: dict[str, list] = {cover: [], gaap: [], wavg: []}
        for tag, period_end, avail, qtrs, unit, seg, coreg in conn.execute(
                sql, (entity_id, cover, gaap, wavg)):
            if avail <= as_of and period_end <= as_of:
                rows_b[tag].append((period_end, qtrs))
            if (qtrs == 0 and unit == "shares" and seg == "" and coreg == ""
                    and avail <= as_of and low <= period_end <= as_of):
                rows_a[tag].append((period_end, 0))
        fresh_b = _first_fresh(rows_b, (cover, gaap), as_of, pit_policy)
        fresh_a = _first_fresh(rows_a, (cover, gaap), as_of, pit_policy)
        n_b += fresh_b
        n_a += fresh_a
        n_both += fresh_a and fresh_b
        if not fresh_a and _first_fresh(rows_a, (wavg,), as_of, pit_policy):
            n_rung3 += 1
    return {
        "as_of": as_of, "n_peers": len(peers),
        "fresh_pit_sharecoverage_selector": n_b,
        "fresh_pit_coverage_selector": n_a,
        "in_both": n_both,
        "weighted_average_rung_rescues": n_rung3,
        "identical": n_a == n_b == n_both,
    }


def _peer_ids(conn: sqlite3.Connection, as_of: str) -> list[sqlite3.Row]:
    import pit_identity
    return pit_identity.peer_universe_as_of(conn, as_of)


def _first_fresh(rows: Mapping[str, Sequence[tuple]], tags: Sequence[str],
                 as_of: str, policy: Any) -> bool:
    for tag in tags:
        found = rows.get(tag) or []
        if not found:
            continue
        period_end, qtrs = max(found)
        if not policy.is_stale(period_end, as_of, qtrs, "shares_outstanding"):
            return True
    return False


# --------------------------------------------------------------------------
# THE RULE, MADE INTO A LINT. Section (4) defines it for one text; this scans
# the whole project for texts that break it. Kept beside the live half rather
# than beside the rule because it reads files, which is the same kind of
# side effect the verifiers have and the pure definitions do not.
# --------------------------------------------------------------------------

#: Files that are allowed to carry an unnamed legacy figure because they are
#: not making a coverage claim with it. Empty on purpose: there is no such
#: file today, and an exemption list that starts populated never empties.
AUDIT_EXEMPT: tuple[str, ...] = ()


def audit_sources(paths: Optional[Iterable[str]] = None) -> dict[str, Any]:
    """Which files in this project quote a legacy figure without naming it.

    The rule made into a LINT rather than a wish. A quote is traceable if the
    file containing it names the definition somewhere -- per FILE, not per
    line, because a module that states its definition in its docstring has
    done what the rule asks and should not have to repeat the id beside every
    number.

    This is deliberately a REPORT and not a failure. The production core is
    frozen and several research modules are stamped into a store's provenance;
    rewriting their docstrings to satisfy a lint written afterwards would edit
    modules for a reason that is not a defect in them. What the backlog is for
    is the next edit: a file in `offenders` that is touched for any other
    reason should leave with its figures named.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    if paths is None:
        paths = sorted(
            os.path.join(here, name) for name in os.listdir(here)
            if name.endswith(".py") and not name.startswith("test_"))
    offenders: dict[str, list[dict[str, Any]]] = {}
    scanned = 0
    for path in paths:
        name = os.path.basename(path)
        if name in AUDIT_EXEMPT:
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                text = handle.read()
        except (OSError, UnicodeDecodeError):
            continue
        scanned += 1
        named = {spec_id for spec_id in DEFINITIONS if spec_id in text}
        named |= {spec_id for spec_id, alias in _CONSTANT_ALIAS.items()
                  if alias in text}
        found: list[dict[str, Any]] = []
        for number, line in enumerate(text.splitlines(), 1):
            for raw in _PCT_RE.findall(line):
                key = _normalise_figure(raw)
                spec_id = LEGACY_FIGURES.get(key)
                if spec_id is None or spec_id in named:
                    continue
                found.append({"line": number, "figure": key + "%",
                              "definition_id": spec_id,
                              "text": line.strip()[:110]})
        if found:
            offenders[name] = found
    return {
        "scanned_files": scanned,
        "offenders": offenders,
        "n_offending_files": len(offenders),
        "n_unnamed_quotes": sum(len(v) for v in offenders.values()),
        "rule": ("A registered share-coverage figure may not be quoted in a "
                 "file that never names the definition it belongs to. See "
                 "LEGACY_FIGURES, lookup() and quote()."),
        "status": ("REPORT, not a gate. The backlog is what the next edit to "
                   "each file should clear."),
        "false_positives_expected": (
            "This is a STRING matcher and it cannot know what a number "
            "counts. '54.0%' as a cohort P(N>=12) in pit_factor_spec, "
            "'(35.2%)' as a tag-liveness share by quarter in pit_symbols and "
            "'54% of proved multi-class issuers' in pit_sharecoverage all "
            "match a registered figure and none of them is a share-coverage "
            "claim. Every hit is a CANDIDATE for a human read, never a "
            "verdict, and a matcher that suppressed them by guessing at "
            "context would be making the same mistake this module exists to "
            "end -- deciding what a number means without being told."),
        "known_true_positives": (
            "pit_coverage.py:32, pit_derive.py:382-383 and 1149-1150 and "
            "pit_sharecoverage.py:716-717 quote the defensible series and the "
            "usable band as share coverage with no definition attached. Those "
            "are the ones this module was written for."),
    }


def resources(db_path: Optional[str] = None) -> dict[str, Any]:
    """Disk and WAL, read before anything and reported after.

    The WAL number is READ, not assumed: a busy checkpoint does nothing
    silently and once produced a 9.3 GB WAL on this volume. This module never
    checkpoints -- it holds a read-only handle and could not -- so the figure
    it reports is whatever a writer elsewhere has left.
    """
    if db_path is None:
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "shafferfineval_pit.db")
    usage = shutil.disk_usage(os.path.dirname(os.path.abspath(db_path)))
    wal = db_path + "-wal"
    return {
        "db_bytes": os.path.getsize(db_path) if os.path.exists(db_path) else UNKNOWN,
        "wal_bytes": os.path.getsize(wal) if os.path.exists(wal) else 0,
        "volume_free_bytes": usage.free,
        "volume_free_gib": round(usage.free / 2 ** 30, 2),
        "volume_used_pct": round(100.0 * usage.used / usage.total, 1),
        "writes_performed": 0,
    }


# ==========================================================================
# (9) REPORT
# ==========================================================================

def _table(rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> str:
    widths = [max(len(c), *(len(str(r.get(c, ""))) for r in rows)) if rows
              else len(c) for c in columns]
    out = ["  ".join(c.ljust(w) for c, w in zip(columns, widths)),
           "  ".join("-" * w for w in widths)]
    for row in rows:
        out.append("  ".join(str(row.get(c, "")).ljust(w)
                             for c, w in zip(columns, widths)))
    return "\n".join(out)


def report(conn: Optional[sqlite3.Connection] = None) -> str:
    lines = ["=" * 96, "PIT SHARE COVERAGE -- ONE NUMBER, FOUR DEFINITIONS",
             "=" * 96, "", canonical_block(), "",
             "-" * 96, "THE FOUR, RECONCILED AT 2015-06-30", "-" * 96, ""]
    walk = reconciliation()
    rows = []
    for step in walk["steps"]:
        rows.append({
            "step": step["step"],
            "n": "%s / %s" % (step["numerator"], step["denominator"]),
            "pct": "%.2f%%" % step["pct"] if step["pct"] else "UNKNOWN",
            "moved by": step["moved_by"],
            "definition": step["definition_id"] or "",
        })
    lines.append(_table(rows, ["step", "n", "pct", "moved by", "definition"]))
    lines += ["", "-" * 96, "DEFINITIONS", "-" * 96, ""]
    for spec in DEFINITIONS.values():
        lines.append("%s  %s" % (spec.definition_id, spec.published))
        lines.append("    numerator    %s" % spec.numerator_rule)
        lines.append("    denominator  %s" % spec.denominator_name)
        lines.append("    concepts     %s" % " then ".join(spec.effective_concepts))
        lines.append("    staleness    %s" % spec.staleness_rule)
        lines.append("    guard        %s" % spec.guard)
        lines.append("    listing      %s" % spec.listing_gate)
        lines.append("    grid         %s" % spec.as_of_grid)
        lines.append("    produced by  %s" % spec.produced_by)
        lines.append("    scope        %s" % spec.sample_scope)
        lines.append("")
    lines += ["-" * 96, "SENSITIVITY", "-" * 96, ""]
    lines.append("CEILING (chosen: 24 months)")
    lines.append(_table(ceiling_sensitivity()["rows"],
                        ["ceiling", "admitted_entity_dates",
                         "admitted_pct_of_priced", "defensible_pct", "basis"]))
    lines += ["", "DENOMINATOR (numerator held at %d)" % (CANONICAL.numerator or 0)]
    lines.append(_table(
        [{k: ("UNKNOWN" if r[k] is None else r[k]) for k in ("denominator", "n", "pct")}
         for r in denominator_sensitivity()["rows"]],
        ["denominator", "n", "pct"]))
    lines += ["", "GUARD"]
    lines.append(_table(guard_sensitivity()["rows"],
                        ["policy", "guard_off_pct", "guard_on_pct", "cost_points"]))
    if conn is not None:
        lines += ["", "-" * 96, "LIVE VERIFICATION (read-only)", "-" * 96, ""]
        live = verify_denominators(conn)
        lines.append("grid dates: %d" % live["grid_dates"])
        lines.append(_table(
            [dict(date=d, **{k: v for k, v in rec.items()
                             if k in ("peer_universe", "scored_rows",
                                      "scored_entities", "rows_minus_entities")})
             for d, rec in live["per_date"].items()],
            ["date", "peer_universe", "scored_rows", "scored_entities",
             "rows_minus_entities"]))
    lines += ["", "-" * 96, "RESOURCES", "-" * 96, "", json.dumps(resources(), indent=1)]
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    db_path = None
    as_json = "--json" in argv
    audit = "--audit" in argv
    live = "--verify" in argv
    slow = "--verify-selectors" in argv
    if "--db" in argv:
        db_path = argv[argv.index("--db") + 1]
    conn = None
    try:
        if live or slow:
            conn = connect_readonly(db_path)
        if as_json:
            out = {
                "canonical": PIT_SHARE_STATE_COVERAGE_V2,
                "canonical_definition_id": CANONICAL_DEFINITION_ID,
                "canonical_block": canonical_block(),
                "definitions": {k: v.as_dict() for k, v in DEFINITIONS.items()},
                "legacy_figures": LEGACY_FIGURES,
                "reconciliation": reconciliation(),
                "sensitivity": sensitivity(),
                "measured": MEASURED,
                "audit": audit_sources(),
                "resources": resources(db_path) if db_path else resources(),
            }
            if conn is not None:
                out["live"] = {"denominators": verify_denominators(conn),
                               "rungs": verify_rung_reachability(conn)}
                if slow:
                    out["live"]["selectors"] = verify_selectors(conn)
            print(json.dumps(out, indent=1, sort_keys=True, default=str))
        elif audit:
            found = audit_sources()
            print("UNNAMED SHARE-COVERAGE QUOTES: %d in %d of %d files"
                  % (found["n_unnamed_quotes"], found["n_offending_files"],
                     found["scanned_files"]))
            for name, hits in sorted(found["offenders"].items()):
                for hit in hits:
                    print("  %s:%d  %s  -> %s" % (
                        name, hit["line"], hit["figure"], hit["definition_id"]))
                    print("      %s" % hit["text"])
            print("")
            print(found["false_positives_expected"])
        else:
            print(report(conn))
            if slow and conn is not None:
                print("\nSELECTOR IDENTITY (slow):")
                print(json.dumps(verify_selectors(conn), indent=1))
    finally:
        if conn is not None:
            conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
