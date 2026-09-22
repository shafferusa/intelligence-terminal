"""Point-in-time EARNINGS PER SHARE -- the valuation leg that needs no share count.

WHY THIS MODULE EXISTS
======================

The measurement that produced it is not ambiguous. Over 39,038 peer sets the
EV/EBITDA cohort has a MEDIAN OF ONE member: P(N>=3) is 34.2% and P(N>=12) --
what a 50-75 percentile BAND actually requires -- is 7.0%. A leave-one-out over
the five leaves EV/EBITDA needs says where that goes:

    all five leaves required      P(N>=3) = 34.2%
    drop the share-count guard    P(N>=3) = 61.6%     <- the binding leaf
    drop total_debt               P(N>=3) = 51.2%
    drop EBITDA                   P(N>=3) = 46.8%
    drop cash                     P(N>=3) = 35.0%

THE BINDING LEAF IS THE DEFENSIBLE SHARE COUNT, NOT DEBT. And the share problem
is STALENESS rather than share classes: of 174,674 refused priced entity-dates,
52.1% are `stale_beyond_max_age`, 28.0% are `only_period_average_available`, and
the entire multi-class guard is 2.2%. `pit_policy.CONCEPT_MAX_AGE_MONTHS` gives
`shares_outstanding` a FOUR-MONTH quarterly bound on a fact that is refreshed
quarterly and arrives with a filing lag, so the bound expires before the next
filing lands and the factor collapses every January and October. That is a
calendar living inside a valuation factor.

    P/E = Price / TTM EPS ROUTES AROUND THE BINDING LEAF ENTIRELY.

The share division is already inside the filed number. A P/E needs one price and
one filed figure; an EV/EBITDA needs a price, a defensible point-in-time share
count, total debt, cash and a matched-period EBITDA. Five leaves against two.

WHY EPS WAS NOT ALREADY HERE
============================

`pit_dera.TAG_FILTER` is built from the concept ladders and no ladder names an
EPS tag, so every `EarningsPerShare*` row in 72 DERA quarters was read at ingest
and dropped. The store holds 37 distinct tags and not one of them is EPS. P/E
was never thin here; it was ABSENT. This module goes and gets it, per issuer,
from the XBRL companyconcept API -- the same targeted, streamed, filtered route
`pit_shares` uses, and deliberately the same SHAPE, so that the two can be read
by one reader and folded together later by a copy rather than a translation.

==========================================================================
THE SIGN CASES -- read this before computing a single multiple
==========================================================================

EPS is the first quantity in this store that is routinely NEGATIVE, and the
standard mistake is not subtle, it is catastrophic:

    abs(P/E) MAPS (-inf, 0) ONTO (0, +inf) **REVERSED**.

A company losing $5.00 a share at a $10 price has P/E = -2; |P/E| = 2 and it
sorts as the cheapest security in the cross-section. A company earning $0.10 at
$10 has P/E = 100 and sorts as the most expensive. Taking the absolute value
therefore ranks the worst loss-makers as the best value, monotonically, with
clean provenance and no error anywhere. Nothing in this module ever calls abs()
on an earnings figure, `price_earnings()` REFUSES a non-positive denominator,
and `test_pit_eps.py` asserts the pathology is not reproduced.

Five cases, named, stored on the row, and never collapsed into each other:

  EPS > threshold     `positive`            a conventional P/E.
  0 < EPS <= 0.01     `near_zero_positive`  EXTREME VALUATION, not an ordinary
                      large positive number. EPS is filed to the CENT, so one
                      cent is one rounding step from zero and its multiple is a
                      quantisation artefact: a $30 stock at $0.01 prints a P/E
                      of 3,000 that moves to 1,500 on the next cent. It is
                      reported separately from `positive` everywhere, because
                      dropping it into a percentile vector is a decision and
                      not a default.
  EPS == 0            `zero`                UNDEFINED. Not infinity, not a large
                      number, not a missing value to be imputed.
  EPS < 0             `negative`            LOSS-MAKING, AND NOT CHEAP. The P/E
                      leg is UNAVAILABLE. The signed EARNINGS YIELD (EPS/price)
                      is well defined and monotone straight through zero, so it
                      goes to the ML layer as a research candidate -- labelled,
                      never as a valuation leg in the frozen core.
  sign changes        `sign_crossing`       ITS OWN STATE, flagged on the row.
                      A TTM EPS that crosses zero between periods makes every
                      change-based earnings factor a ratio across a sign change,
                      which is not a growth rate; consumers must drop the step
                      exactly as they drop a `source_tag_changed` step.

==========================================================================
EPS IS RESTATED FOR SPLITS. MEASURED, NOT ASSUMED.
==========================================================================

`pit_shares` documents the split trap for share COUNTS. EPS has the mirror
image of it, because EPS is earnings divided by a share count, and this ingest
measured it live on CIK 320193's diluted EPS for the fiscal year ending
2019-09-28:

    11.89   filed 2019-10-31   the 10-K for that year, as printed
     2.97   filed 2020-10-30   the comparative in the NEXT 10-K
     2.97   filed 2021-10-29   and in the one after

One period, one tag, two numbers four times apart, and the only thing between
them is the 4:1 split of 2020-08-31. The point-in-time selector here handles it
BY CONSTRUCTION -- it takes the newest vintage AVAILABLE AT THE AS-OF DATE, so a
2020-01-31 as-of returns 11.89 and never sees the restatement. That is correct,
and it carries the same consequence `pit_shares` spells out:

    A POINT-IN-TIME EPS IS IN THE SHARE BASIS OF ITS OWN ERA.
    ITS P/E MUST BE TAKEN AGAINST A RAW, AS-PRINTED, UN-ADJUSTED PRICE.

Apple on 2020-01-31: as printed, $309.51 / 11.89 = 26.0. Pair that same 11.89
with the split-adjusted price shown today (~$77) and the multiple is 6.5 -- a
mega cap that looks like a deep-value cyclical, wrong by exactly the 4x split,
with clean provenance and no error anywhere. `pit_shares.PRICE_BASIS_RAW` and
`pit_shares.price_basis_for()` state the rule; it binds P/E exactly as it binds
market cap, and a P/E built on Yahoo's `close` or `adjclose` is wrong by every
split since the period end.

==========================================================================
PRIMITIVE vs DERIVED -- both are stored, and they are different tables
==========================================================================

No source hands anybody a TTM EPS. It is CALCULATED, so it is stored as a
derived row that names its inputs rather than as a number with no parents:

  `pit_eps_obs`  THE PRIMITIVES. One immutable row per published EPS
                 observation, exactly as filed, signed, with its accession and
                 its availability date. A restatement is a NEW ROW.
  `pit_eps_ttm`  THE DERIVED QUANTITY. One row per (entity, concept, period,
                 VINTAGE), carrying the method, the component accessions, the
                 count of components and an available_date that is the MAX over
                 them -- a TTM is knowable only once its LAST component is.

The two TTM methods are NOT interchangeable and the row says which it used:

  `annual_figure`   the filed annual EPS at this period end. PREFERRED at a
                    fiscal year end, because it is the audited number.
  `four_quarters`   the sum of four consecutive quarterly EPS figures. This is
                    the only route at a non-year-end period, and it is an
                    APPROXIMATION of the annual figure, not an identity: each
                    quarter's EPS is divided by that quarter's own weighted
                    average share count, so four quarterly EPS figures do not
                    add to the annual EPS for any issuer whose share count
                    moved. Mixing the two methods inside one cross-section is
                    mixing two measurements, which is why `method` is on the row.

==========================================================================
INVARIANTS
==========================================================================

1. APPEND-ONLY, like `pit_fact` and `pit_share_obs`. Enforced by triggers.
2. NOTHING IS KNOWABLE BEFORE IT WAS AVAILABLE. `available_date` comes from
   `pit_policy.available_date` and is never earlier than `filed`.
3. `frame` IS NEVER STORED. It carries the LATEST restated value and has no
   filed date, so it can never be a point-in-time selector.
4. THERE IS NO `CHECK (val >= 0)`. `pit_share_obs` has one because a share
   count cannot be negative; an EPS can, and the negative ones are the whole
   reason the sign vocabulary above exists. A schema that refused them would
   silently delete every loss-maker from the cross-section.
5. THE UNIQUE KEY INCLUDES `qtrs`. `pit_share_obs` omits it because its facts
   are instants; a 10-K carries BOTH a Q4 EPS and a full-year EPS ending on the
   same date in the same accession, and a key without `qtrs` would keep one and
   discard the other.
6. NO EDIT TO A FROZEN POLICY. The ladder below is `eps_concept_ladder_v2`, a
   CANDIDATE-lineage ladder owned by this module. See `EPS_LADDER_VERSION`.

Stdlib plus `requests`, reusing `pit_identity`'s throttled, cached SEC session
so this module and `pit_shares` share ONE budget against the 10 req/s ceiling.
"""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional, Sequence

import pit_policy
import pit_store
from pit_identity import cik10, fetch_sec_json

__all__ = [
    "SCHEMA", "ensure_schema", "EPS_LADDER_VERSION", "TTM_RULE_VERSION",
    "EpsConcept", "EPS_CONCEPTS", "eps_concepts", "concept_for",
    "EPS_LADDER", "EPS_LADDER_PRIMARY", "EPS_LADDER_FALLBACK",
    "CONCEPT_DILUTED", "CONCEPT_BASIC_AND_DILUTED", "CONCEPT_BASIC",
    "SIGN_POSITIVE", "SIGN_NEAR_ZERO_POSITIVE", "SIGN_ZERO", "SIGN_NEGATIVE",
    "SIGN_CROSSING", "EPS_NEAR_ZERO_ABS", "sign_case",
    "PE_AVAILABLE", "PE_AVAILABLE_EXTREME", "PE_UNAVAILABLE_ZERO",
    "PE_UNAVAILABLE_LOSS", "pe_leg_for",
    "price_earnings", "signed_earnings_yield",
    "METHOD_ANNUAL", "METHOD_FOUR_QUARTERS",
    "fetch_eps_concept", "ingest_entity_eps", "insert_eps_obs",
    "derive_ttm_for_entity", "insert_ttm_rows",
    "ttm_eps_as_of", "ttm_eps_as_of_detail", "eps_ladder_spec",
]


# ==========================================================================
# (1) THE LADDER -- candidate lineage, never an edit to v1
# ==========================================================================

#: The lineage id stamped on every row this module writes.
#:
#: NOT folded into `pit_policy.LADDERS_V2`, and the reason is load-bearing
#: rather than stylistic. `pit_policy.ladder_tags(v2)` is what
#: `pit_dera.TAG_FILTER` is built from, and `concept_ladder_v2`'s documented
#: invariant is that it "introduces NO new tag, which is why pit_dera.TAG_FILTER
#: is unchanged and the loaded 14,072,934-row store can serve v2 with no
#: re-ingest". Adding three EPS tags there would falsify that sentence and would
#: make every existing v2 consumer demand tags `pit_fact` does not contain. So
#: the EPS ladder is a SEPARATE versioned ladder in the candidate lineage, owned
#: by the module that fetches it -- which is exactly the arrangement `pit_shares`
#: already uses for the five share concepts. `concept_ladder_v1` is untouched.
EPS_LADDER_VERSION = "eps_concept_ladder_v2"

#: The TTM derivation is a POLICY, not an implementation detail, so it carries
#: its own version on every derived row. Changing the method preference, the
#: quarter-matching tolerance or the near-zero threshold means a v2 of this
#: string beside the v1 that produced the existing rows.
TTM_RULE_VERSION = "ttm_eps_rule_v1"

CONCEPT_DILUTED = "eps_diluted"
CONCEPT_BASIC_AND_DILUTED = "eps_basic_and_diluted"
CONCEPT_BASIC = "eps_basic"

#: XBRL's unit key for a per-share amount. Confirmed live on 2026-09-21 against
#: CIK 320193: `units` carries exactly one key, `USD/shares`, on all three tags.
UNIT_USD_PER_SHARE = "USD/shares"

SOURCE_COMPANYCONCEPT = "sec:companyconcept"

#: How faithfully a rung stands for the denominator a P/E should use. Same
#: vocabulary as `pit_policy.BOUND_EXACT` / `BOUND_LOWER`, with the direction
#: named, because for EPS the bias runs the OTHER way from debt's: an
#: overstated EPS understates the multiple and so FLATTERS the valuation score.
BOUND_DILUTED = "diluted_exact"
BOUND_OVERSTATES = "overstates_eps_understates_pe"


@dataclass(frozen=True)
class EpsConcept:
    """One XBRL tag that carries an earnings-per-share figure.

    `is_diluted` is the field that decides whether the rung delivers the
    CONSERVATIVE denominator. It is a property of the concept, not of the
    caller's intent, which is why it lives here and not at the call site.
    """

    key: str
    taxonomy: str
    tag: str
    unit: str
    is_diluted: bool
    bound: str
    why: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "taxonomy": self.taxonomy, "tag": self.tag,
                "unit": self.unit, "is_diluted": self.is_diluted,
                "bound": self.bound, "why": self.why}


EPS_CONCEPTS: tuple[EpsConcept, ...] = (
    EpsConcept(
        key=CONCEPT_DILUTED,
        taxonomy=pit_policy.TAXONOMY_US_GAAP,
        tag="EarningsPerShareDiluted",
        unit=UNIT_USD_PER_SHARE,
        is_diluted=True,
        bound=BOUND_DILUTED,
        why=("THE CONSERVATIVE DENOMINATOR, and what a P/E should use. Diluted "
             "EPS divides earnings by the share count that would exist if every "
             "dilutive instrument -- options, RSUs, convertibles -- were "
             "exercised, so it is the SMALLER earnings figure and produces the "
             "LARGER, more cautious multiple. It leads for that economic reason "
             "and not because it is better covered."),
    ),
    EpsConcept(
        key=CONCEPT_BASIC_AND_DILUTED,
        taxonomy=pit_policy.TAXONOMY_US_GAAP,
        tag="EarningsPerShareBasicAndDiluted",
        unit=UNIT_USD_PER_SHARE,
        is_diluted=True,
        bound=BOUND_DILUTED,
        why=("The tag a filer uses to ASSERT that basic and diluted are equal -- "
             "it has no dilutive securities outstanding, or they are "
             "anti-dilutive this period. The value IS the diluted figure by the "
             "filer's own declaration, which is why it ranks above basic and not "
             "below it. Typically the only EPS tag a single-class issuer with no "
             "option programme files at all: CIK 320193 404s on this tag while "
             "carrying 338 rows of diluted and 338 of basic."),
    ),
    EpsConcept(
        key=CONCEPT_BASIC,
        taxonomy=pit_policy.TAXONOMY_US_GAAP,
        tag="EarningsPerShareBasic",
        unit=UNIT_USD_PER_SHARE,
        is_diluted=False,
        bound=BOUND_OVERSTATES,
        why=("Basic EPS divides by the weighted average of shares ACTUALLY "
             "outstanding and ignores dilution, so it is the LARGER earnings "
             "figure and yields a SMALLER P/E. The direction of that bias "
             "matters: valuation is lower-is-better, so a basic-EPS rung "
             "FLATTERS the score, and it flatters it most for exactly the "
             "issuers with the heaviest option and convertible programmes. Last "
             "rung, labelled, and a cross-section mixing it with diluted rungs "
             "must report the rung distribution beside the percentile."),
    ),
)

_BY_KEY = {c.key: c for c in EPS_CONCEPTS}

#: The full ladder, in economic order: diluted, then the filer's own assertion
#: that basic equals diluted, then basic.
EPS_LADDER: tuple[str, ...] = (CONCEPT_DILUTED, CONCEPT_BASIC_AND_DILUTED,
                               CONCEPT_BASIC)

#: THE FETCH PLAN, and it is not the same thing as the ladder.
#:
#: The owner approved a measured scope of ~5,066 requests over the 2,533
#: price-resolvable entities, which is TWO concepts per issuer. Three rungs
#: fetched unconditionally would be 7,599 requests -- 50% over an approved
#: budget on a 96%-full volume. So the two rungs that between them cover almost
#: every filer are fetched for everybody, and the middle rung is fetched ONLY
#: for the issuers where those two produced nothing at all. That is the same
#: conditional-fallback idiom `pit_shares.ingest_entity_shares` uses for its
#: companyfacts route, and it is bounded, counted and reported.
#:
#: WHAT THE CONDITIONAL COSTS, stated rather than hidden: for an issuer that
#: resolves on basic, we never learn whether `EarningsPerShareBasicAndDiluted`
#: also existed. The stored VALUE is unaffected -- that tag asserts basic equals
#: diluted, so the number is the same number -- but the recorded `concept_key`
#: would have said `eps_basic_and_diluted` and instead says `eps_basic`. The
#: diluted-vs-basic distinction, which is the one that moves a multiple, is
#: never at risk: the diluted rung is fetched for every issuer.
EPS_LADDER_PRIMARY: tuple[str, ...] = (CONCEPT_DILUTED, CONCEPT_BASIC)
EPS_LADDER_FALLBACK: tuple[str, ...] = (CONCEPT_BASIC_AND_DILUTED,)

COMPANYCONCEPT_URL = (
    "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik10}/{taxonomy}/{tag}.json")


def eps_concepts() -> tuple[EpsConcept, ...]:
    """Every EPS concept this module knows, in ladder order."""
    return EPS_CONCEPTS


def concept_for(key: str) -> EpsConcept:
    """One concept by key. Raises on an unknown key rather than guessing."""
    try:
        return _BY_KEY[key]
    except KeyError:
        raise ValueError(
            f"unknown EPS concept {key!r}; known: {', '.join(sorted(_BY_KEY))}"
        ) from None


# ==========================================================================
# (2) THE SIGN VOCABULARY
# ==========================================================================

SIGN_POSITIVE = "positive"
SIGN_NEAR_ZERO_POSITIVE = "near_zero_positive"
SIGN_ZERO = "zero"
SIGN_NEGATIVE = "negative"

#: A STATE, not a value bucket: it describes a PAIR of periods, so it is a flag
#: on the row and never a member of the four-way case above.
SIGN_CROSSING = "sign_crossing"

#: One cent. EPS is filed to the cent, so an EPS at or under one cent is a
#: single rounding step from zero and the multiple it produces is a quantisation
#: artefact rather than a valuation: a $30 stock at $0.01 prints 3,000 and
#: halves to 1,500 on the next cent of earnings. Not a tuning parameter -- it is
#: the reporting granularity of the underlying figure.
EPS_NEAR_ZERO_ABS = 0.01

PE_AVAILABLE = "available"
PE_AVAILABLE_EXTREME = "available_extreme_valuation"
PE_UNAVAILABLE_ZERO = "unavailable_eps_zero_undefined"
PE_UNAVAILABLE_LOSS = "unavailable_loss_making"

#: Why a TTM EPS could not be produced. These sit beside `pit_store`'s own
#: vocabulary in `pit_feature.unavailable_reason`.
REASON_NEVER_FILED = "never_filed_an_eps"
REASON_NOT_YET_FILED = pit_store.REASON_NOT_YET_FILED
REASON_STALE = pit_store.REASON_STALE
REASON_NO_TTM = "no_ttm_assembly_available"


def sign_case(value: Optional[float],
              near_zero: float = EPS_NEAR_ZERO_ABS) -> str:
    """Which of the four value cases an EPS figure is in.

    Exact equality against 0.0 is deliberate and correct here: a filed EPS of
    exactly zero is a REPORTED zero, not a small number that rounded, and the
    XBRL value is a decimal the filer chose. Everything in (0, near_zero] is
    near-zero-positive; everything below 0 is negative, near-zero or not,
    because a company losing a cent a share is loss-making and the P/E leg is
    unavailable either way.
    """
    if value is None:
        raise ValueError(
            "a missing EPS has no sign case -- 'unavailable' and 'zero' are "
            "different answers and merging them is how an absence becomes an "
            "undefined multiple")
    v = float(value)
    if v != v or v in (float("inf"), float("-inf")):   # NaN / inf
        raise ValueError(f"EPS is not a finite number: {value!r}")
    if v == 0.0:
        return SIGN_ZERO
    if v < 0.0:
        return SIGN_NEGATIVE
    return SIGN_NEAR_ZERO_POSITIVE if v <= near_zero else SIGN_POSITIVE


def pe_leg_for(case: str) -> str:
    """Whether the P/E valuation leg is available for this sign case.

    The one place the rule lives, so it cannot drift between the ingest, the
    coverage measurement and any later candidate feature builder.
    """
    if case == SIGN_POSITIVE:
        return PE_AVAILABLE
    if case == SIGN_NEAR_ZERO_POSITIVE:
        return PE_AVAILABLE_EXTREME
    if case == SIGN_ZERO:
        return PE_UNAVAILABLE_ZERO
    if case == SIGN_NEGATIVE:
        return PE_UNAVAILABLE_LOSS
    raise ValueError(f"unknown sign case {case!r}")


def price_earnings(price: Optional[float], ttm_eps: Optional[float],
                   near_zero: float = EPS_NEAR_ZERO_ABS) -> dict[str, Any]:
    """Price / TTM EPS, with the sign cases enforced. NEVER calls abs().

    Returns a record whose `pe` is None whenever the multiple is not defined,
    with `reason` naming which case it was. The refusal on a negative
    denominator is the whole point of the function existing: taking |P/E| maps
    (-inf, 0) onto (0, +inf) REVERSED, so the deepest loss-makers come out as
    the cheapest securities in a lower-is-better valuation percentile. There is
    no flag to turn that refusal off.
    """
    record: dict[str, Any] = {
        "pe": None, "sign_case": None, "pe_leg": None, "reason": None,
        "price": price, "ttm_eps": ttm_eps,
        "earnings_yield": None, "extreme": False,
        "never_abs": ("abs(P/E) reverses the ordering of loss-makers and is "
                      "never computed here"),
    }
    if price is None or ttm_eps is None:
        record["reason"] = "no_price" if price is None else REASON_NO_TTM
        return record
    price = float(price)
    if price <= 0:
        record["reason"] = "no_price"
        return record
    case = sign_case(ttm_eps, near_zero)
    leg = pe_leg_for(case)
    record["sign_case"] = case
    record["pe_leg"] = leg
    record["earnings_yield"] = signed_earnings_yield(ttm_eps, price)["earnings_yield"]
    if leg in (PE_UNAVAILABLE_ZERO, PE_UNAVAILABLE_LOSS):
        record["reason"] = leg
        return record
    record["pe"] = price / float(ttm_eps)
    record["extreme"] = leg == PE_AVAILABLE_EXTREME
    return record


def signed_earnings_yield(ttm_eps: Optional[float],
                          price: Optional[float]) -> dict[str, Any]:
    """TTM EPS / price -- the RESEARCH quantity, for the ML layer only.

    The reciprocal of P/E is what survives the sign boundary. Earnings yield is
    continuous and MONOTONE straight through zero: -50%, -1%, 0%, +1%, +10% is
    an ordering that means what it looks like, while the P/E of those same five
    firms is -2, -100, undefined, +100, +10. That is why a loss-making issuer's
    earnings information is not simply discarded -- it goes to the ML layer as a
    signed, labelled research candidate.

    It is NOT a valuation leg in the frozen core, and this record says so on
    every call so that nobody can lift the number out of context.
    """
    out: dict[str, Any] = {
        "earnings_yield": None,
        "layer": "ML_RESEARCH_CANDIDATE",
        "not_a_valuation_leg": ("the frozen equity core's valuation factor takes "
                                "P/E and EV/EBITDA; the signed earnings yield is "
                                "a research input and must never be substituted "
                                "for either"),
        "why_it_survives_the_sign": ("monotone through zero, so a loss is ranked "
                                     "as a loss rather than as a bargain"),
    }
    if ttm_eps is None or price is None:
        return out
    price = float(price)
    if price <= 0:
        return out
    out["earnings_yield"] = float(ttm_eps) / price
    return out


# ==========================================================================
# (3) SCHEMA. Owned here; pit_store and pit_shares are not edited.
# ==========================================================================

METHOD_ANNUAL = "annual_figure"
METHOD_FOUR_QUARTERS = "four_quarters"

SCHEMA = """
-- THE PRIMITIVES. One immutable row per published EPS observation, as filed.
--
-- Deliberately a MIRROR of pit_share_obs, which is itself a mirror of pit_fact,
-- so folding the three together later is a copy rather than a translation. Two
-- differences from pit_share_obs, and both are deliberate:
--
--   * NO `CHECK (val >= 0)`. A share count cannot be negative; an EPS can, and
--     the negative ones carry the information that the issuer is loss-making.
--     A non-negative check here would silently delete every loss-maker.
--   * `qtrs` IS IN THE UNIQUE KEY. A 10-K carries a Q4 EPS and a full-year EPS
--     ending on the same date in the same accession. Without qtrs the second
--     would be dropped as a duplicate of the first.
CREATE TABLE IF NOT EXISTS pit_eps_obs (
    eps_obs_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id             INTEGER NOT NULL REFERENCES pit_entity(entity_id),
    concept_key           TEXT NOT NULL,
    taxonomy              TEXT NOT NULL,
    tag                   TEXT NOT NULL,
    unit                  TEXT NOT NULL,
    period_start          TEXT,
    period_end            TEXT NOT NULL,
    qtrs                  INTEGER NOT NULL,
    val                   REAL NOT NULL,
    sign_case             TEXT NOT NULL,
    accn                  TEXT NOT NULL,
    form                  TEXT NOT NULL,
    fy                    INTEGER,
    fp                    TEXT,
    filed                 TEXT NOT NULL,
    accepted_raw          TEXT,
    accepted_eastern      TEXT,
    available_date        TEXT NOT NULL,
    availability_rule     TEXT NOT NULL,
    latency_policy_version TEXT NOT NULL,
    eps_ladder_version    TEXT NOT NULL,
    source                TEXT NOT NULL,
    ingested_at           TEXT NOT NULL,
    UNIQUE (entity_id, tag, unit, period_end, qtrs, accn),
    CHECK (qtrs IN (0, 1, 2, 3, 4)),
    CHECK (available_date >= filed)
);

CREATE INDEX IF NOT EXISTS idx_pit_eps_select
    ON pit_eps_obs (entity_id, concept_key, period_end, available_date);

CREATE TRIGGER IF NOT EXISTS trg_pit_eps_obs_no_update
BEFORE UPDATE ON pit_eps_obs
BEGIN
    SELECT RAISE(ABORT, 'pit_eps_obs is append-only: a restatement is a new row');
END;

CREATE TRIGGER IF NOT EXISTS trg_pit_eps_obs_no_delete
BEFORE DELETE ON pit_eps_obs
BEGIN
    SELECT RAISE(ABORT, 'pit_eps_obs is append-only: published facts are never deleted');
END;

-- THE DERIVED QUANTITY. No source hands anybody a TTM EPS; we calculate it, so
-- it is stored as a row that NAMES ITS INPUTS rather than as a bare number.
--
-- `accn_key` is the vintage: the sorted component accessions joined by '|' the
-- FIRST time this value was published. A restatement that CHANGES the derived
-- figure produces a new row, which is how an append-only derived table stays
-- point-in-time. A re-filing that merely REPEATS the number does not: CIK
-- 320193's FY2023 diluted EPS of 6.13 appears under three accessions in three
-- consecutive 10-Ks, all three are kept in pit_eps_obs, and one row here
-- answers every as-of date identically.
-- `available_date` is the MAX over components -- an assembly is knowable only
-- once its LAST component is.
--
-- `components_json` is [[period_end, qtrs, accn], ...] -- exactly pit_eps_obs's
-- unique key minus the entity, tag and unit this row already carries. It is a
-- lossless FOREIGN KEY into the primitives, not a copy of them: val, form,
-- filed and available_date are one join away. A four-quarter TTM has four
-- components and duplicating each one's whole row here would triple this
-- table for no information.
CREATE TABLE IF NOT EXISTS pit_eps_ttm (
    eps_ttm_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id             INTEGER NOT NULL REFERENCES pit_entity(entity_id),
    concept_key           TEXT NOT NULL,
    period_end            TEXT NOT NULL,
    ttm_eps               REAL NOT NULL,
    method                TEXT NOT NULL,
    n_components          INTEGER NOT NULL,
    components_json       TEXT NOT NULL,
    accn_key              TEXT NOT NULL,
    mixed_accession       INTEGER NOT NULL DEFAULT 0,
    available_date        TEXT NOT NULL,
    sign_case             TEXT NOT NULL,
    sign_crossing         INTEGER NOT NULL DEFAULT 0,
    prev_period_end       TEXT,
    prev_ttm_eps          REAL,
    pe_leg                TEXT NOT NULL,
    eps_ladder_version    TEXT NOT NULL,
    ttm_rule_version      TEXT NOT NULL,
    derived_at            TEXT NOT NULL,
    UNIQUE (entity_id, concept_key, period_end, accn_key),
    CHECK (method IN ('annual_figure', 'four_quarters')),
    CHECK (n_components IN (1, 4))
);

CREATE INDEX IF NOT EXISTS idx_pit_eps_ttm_select
    ON pit_eps_ttm (entity_id, period_end, available_date);

CREATE TRIGGER IF NOT EXISTS trg_pit_eps_ttm_no_update
BEFORE UPDATE ON pit_eps_ttm
BEGIN
    SELECT RAISE(ABORT, 'pit_eps_ttm is append-only: a new vintage is a new row');
END;

-- Per (entity, concept) ingest evidence, INCLUDING THE NEGATIVE RESULTS. A 404
-- on an EPS tag is a fact about the issuer -- CIK 320193 404s on
-- EarningsPerShareBasicAndDiluted because it has dilutive securities and tags
-- the two figures separately -- not a failed fetch. Coverage arithmetic that
-- cannot see the 404s quietly divides by the survivors.
CREATE TABLE IF NOT EXISTS pit_eps_ingest (
    entity_id             INTEGER NOT NULL REFERENCES pit_entity(entity_id),
    cik                   TEXT NOT NULL,
    concept_key           TEXT NOT NULL,
    http_status           INTEGER NOT NULL,
    rows_seen             INTEGER NOT NULL DEFAULT 0,
    rows_kept             INTEGER NOT NULL DEFAULT 0,
    rows_rejected         INTEGER NOT NULL DEFAULT 0,
    first_period_end      TEXT,
    last_period_end       TEXT,
    acceptance_matched    INTEGER NOT NULL DEFAULT 0,
    units_seen            TEXT NOT NULL DEFAULT '',
    source                TEXT NOT NULL,
    checked_at            TEXT NOT NULL,
    UNIQUE (entity_id, concept_key, source)
);
"""


def ensure_schema(conn: sqlite3.Connection) -> str:
    """Create this module's tables if absent. Idempotent; returns a status."""
    existed = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'pit_eps%'")}
    conn.executescript(SCHEMA)
    conn.commit()
    wanted = {"pit_eps_obs", "pit_eps_ttm", "pit_eps_ingest"}
    return "exists" if wanted <= existed else "created"


# --------------------------------------------------------------------------
# Small helpers -- same shapes as pit_shares, on purpose
# --------------------------------------------------------------------------

def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _day(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) < 10:
        return None
    try:
        _dt.date.fromisoformat(text[:10])
    except ValueError:
        return None
    return text[:10]


def _days_between(start: Any, end: Any) -> int:
    a, b = _day(start), _day(end)
    if a is None or b is None:
        return 10 ** 6
    return (_dt.date.fromisoformat(b) - _dt.date.fromisoformat(a)).days


def _qtrs(start: Any, end: Any) -> int:
    """`qtrs` for one observation. 0 means instantaneous -- never, for EPS.

    EPS is always a DURATION: it is earnings over a period divided by a share
    count over that period. A row with no parseable start is therefore not an
    instant, it is unusable, and `_store_concept` rejects it rather than
    storing it as qtrs = 0 where a balance-sheet reader would find it.
    """
    if start is None or _day(start) is None:
        return 0
    days = _days_between(start, end)
    quarters = int(round(days / 91.3125))
    return max(1, min(4, quarters))


# ==========================================================================
# (4) FETCH
# ==========================================================================

def fetch_eps_concept(cik: Any, concept: EpsConcept,
                      cache_dir: Optional[str] = None) -> dict[str, Any]:
    """One companyconcept document, reduced to the rows this module keeps.

    companyconcept IS the targeted filter: one tag for one issuer, tens of
    kilobytes, against a companyfacts payload that runs to megabytes. Nothing
    but the observations survives the call, and `frame` is stripped on the way
    through because it always carries the LATEST restated value and has no
    filed date -- it can never be a point-in-time selector.

    A 404 is a FACT, not a failure. Returns
    {'status', 'rows', 'units_seen', 'unit_kept', 'empty_200'}.
    """
    url = COMPANYCONCEPT_URL.format(
        cik10=cik10(cik), taxonomy=concept.taxonomy, tag=concept.tag)
    status, payload = fetch_sec_json(url, cache_dir)
    if status != 200 or not payload:
        return {"status": status, "rows": [], "units_seen": [],
                "unit_kept": None, "empty_200": False}
    units = payload.get("units") or {}
    block = units.get(concept.unit)
    observations = block if isinstance(block, list) else []
    rows = [{k: v for k, v in row.items() if k != "frame"} for row in observations]
    return {"status": status, "rows": rows, "units_seen": sorted(units),
            "unit_kept": concept.unit if concept.unit in units else None,
            "empty_200": not rows}


# ==========================================================================
# (5) INGEST
# ==========================================================================

EPS_COLUMNS = (
    "entity_id", "concept_key", "taxonomy", "tag", "unit", "period_start",
    "period_end", "qtrs", "val", "sign_case", "accn", "form", "fy", "fp",
    "filed", "accepted_raw", "accepted_eastern", "available_date",
    "availability_rule", "latency_policy_version", "eps_ladder_version",
    "source", "ingested_at",
)

_EPS_INSERT = (
    "INSERT OR IGNORE INTO pit_eps_obs (" + ", ".join(EPS_COLUMNS) + ") VALUES ("
    + ", ".join("?" * len(EPS_COLUMNS)) + ")"
)

TTM_COLUMNS = (
    "entity_id", "concept_key", "period_end", "ttm_eps", "method",
    "n_components", "components_json", "accn_key", "mixed_accession",
    "available_date", "sign_case", "sign_crossing", "prev_period_end",
    "prev_ttm_eps", "pe_leg", "eps_ladder_version", "ttm_rule_version",
    "derived_at",
)

_TTM_INSERT = (
    "INSERT OR IGNORE INTO pit_eps_ttm (" + ", ".join(TTM_COLUMNS) + ") VALUES ("
    + ", ".join("?" * len(TTM_COLUMNS)) + ")"
)


def insert_eps_obs(conn: sqlite3.Connection, rows: Iterable[Sequence[Any]]) -> int:
    """Bulk-insert EPS tuples in EPS_COLUMNS order. Returns rows inserted.

    INSERT OR IGNORE is safe for the same reason it is in `pit_store.insert_facts`:
    the unique key includes `accn`, so a conflict means the identical published
    observation is already stored. It can never overwrite a value -- the
    immutability trigger forbids UPDATE outright.
    """
    cursor = conn.executemany(_EPS_INSERT, rows)
    return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0


def insert_ttm_rows(conn: sqlite3.Connection, rows: Iterable[Sequence[Any]]) -> int:
    """Bulk-insert derived TTM tuples in TTM_COLUMNS order."""
    cursor = conn.executemany(_TTM_INSERT, rows)
    return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0


def _store_concept(conn: sqlite3.Connection, entity_id: int, padded: str,
                   concept: EpsConcept, raw_rows: Sequence[dict[str, Any]],
                   *, status: int, source: str, stamp: str,
                   accepted_map: dict[str, Any],
                   next_session: Optional[Callable[[str], Optional[str]]],
                   units_seen: Sequence[str] = (),
                   commit: bool = True) -> dict[str, Any]:
    """Validate, stamp and store one concept's observations. Returns a report."""
    rows: list[tuple[Any, ...]] = []
    rejected = 0
    matched = 0
    ends: list[str] = []
    cases: dict[str, int] = {}

    for raw in raw_rows:
        period_end = _day(raw.get("end"))
        period_start = _day(raw.get("start"))
        filed = _day(raw.get("filed"))
        accn = raw.get("accn") or ""
        value = raw.get("val")
        if period_end is None or filed is None or not accn or value is None:
            rejected += 1
            continue
        if period_start is None:
            # EPS is a DURATION by construction. A row with no start is not an
            # instant, it is unusable; storing it as qtrs = 0 would put it where
            # a balance-sheet reader looks.
            rejected += 1
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            rejected += 1
            continue
        if value != value or value in (float("inf"), float("-inf")):
            rejected += 1
            continue

        case = sign_case(value)
        cases[case] = cases.get(case, 0) + 1
        accepted_raw = accepted_map.get(accn)
        if accepted_raw:
            matched += 1
        detail = pit_policy.available_date_detail(accepted_raw, filed, next_session)
        ends.append(period_end)
        rows.append((
            entity_id, concept.key, concept.taxonomy, concept.tag, concept.unit,
            period_start, period_end, _qtrs(period_start, period_end), value,
            case, accn, raw.get("form") or "", raw.get("fy"), raw.get("fp"),
            filed, accepted_raw, detail["accepted_et"], detail["available_date"],
            detail["rule"], detail["latency_policy_version"], EPS_LADDER_VERSION,
            source, stamp,
        ))

    written = insert_eps_obs(conn, rows) if rows else 0
    conn.execute(
        """INSERT INTO pit_eps_ingest
               (entity_id, cik, concept_key, http_status, rows_seen, rows_kept,
                rows_rejected, first_period_end, last_period_end,
                acceptance_matched, units_seen, source, checked_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (entity_id, concept_key, source) DO UPDATE SET
                http_status = excluded.http_status,
                rows_seen = excluded.rows_seen,
                rows_kept = excluded.rows_kept,
                rows_rejected = excluded.rows_rejected,
                first_period_end = excluded.first_period_end,
                last_period_end = excluded.last_period_end,
                acceptance_matched = excluded.acceptance_matched,
                units_seen = excluded.units_seen,
                checked_at = excluded.checked_at""",
        (entity_id, padded, concept.key, status, len(raw_rows), len(rows),
         rejected, min(ends) if ends else None, max(ends) if ends else None,
         matched, ",".join(units_seen), source, stamp),
    )
    if commit:
        conn.commit()

    return {"http_status": status, "source": source, "rows_seen": len(raw_rows),
            "rows_valid": len(rows), "rows_written": written,
            "rows_rejected": rejected, "acceptance_matched": matched,
            "units_seen": list(units_seen), "sign_cases": cases,
            "first_period_end": min(ends) if ends else None,
            "last_period_end": max(ends) if ends else None}


def ingest_entity_eps(conn: sqlite3.Connection, entity_id: int, cik: Any, *,
                      cache_dir: Optional[str] = None,
                      next_session: Optional[Callable[[str], Optional[str]]] = None,
                      accepted_map: Optional[dict[str, Any]] = None,
                      primary: Sequence[str] = EPS_LADDER_PRIMARY,
                      fallback: Sequence[str] = EPS_LADDER_FALLBACK,
                      derive_ttm: bool = True,
                      commit: bool = True) -> dict[str, Any]:
    """Fetch and store every EPS observation this issuer ever published.

    Two requests per issuer -- the diluted rung and the basic rung -- which is
    exactly the approved 5,066-request budget over 2,533 entities. The middle
    rung, `EarningsPerShareBasicAndDiluted`, is fetched ONLY when those two
    produced nothing, which is the issuer that files it and nothing else. See
    `EPS_LADDER_PRIMARY` for what that conditional costs and why it is bounded.

    `accepted_map` is accession -> EDGAR acceptanceDateTime, and it is supplied
    by the caller rather than fetched. `pit_shares` pays one submissions request
    per issuer for it; here that would be 2,533 more requests against an
    approved 5,066, and the timestamps are ALREADY IN THE STORE -- every one of
    pit_fact's 14,072,934 rows carries `accepted_raw`, keyed by the same
    accession numbers these EPS rows come from. One local scan replaces 2,533
    network calls. Where an accession is NOT in the map (an EPS filing whose
    accession never carried a ladder tag), `pit_policy` takes the pessimistic
    `next_session_no_acceptance_time` branch, which can only ever DELAY
    availability -- never advance it, which is the only direction that would
    manufacture a profit.

    Returns a report dict. Never raises for a missing issuer.
    """
    padded = cik10(cik)
    stamp = _now()
    accepted_map = accepted_map or {}
    report: dict[str, Any] = {
        "entity_id": entity_id, "cik": padded, "concepts": {},
        "requests": 0, "rows_written": 0, "rows_seen": 0, "rows_rejected": 0,
        "acceptance_matched": 0, "empty_200": [], "fallback_used": False,
        "ttm_rows_written": 0, "notes": [],
    }

    def accumulate(key: str, result: dict[str, Any]) -> None:
        report["concepts"][key] = result
        report["rows_seen"] += result["rows_seen"]
        report["rows_written"] += result["rows_written"]
        report["rows_rejected"] += result["rows_rejected"]
        report["acceptance_matched"] += result["acceptance_matched"]

    for key in primary:
        concept = concept_for(key)
        fetched = fetch_eps_concept(padded, concept, cache_dir)
        report["requests"] += 1
        if fetched["status"] == 200 and fetched["empty_200"]:
            report["empty_200"].append(key)
        accumulate(key, _store_concept(
            conn, entity_id, padded, concept, fetched["rows"],
            status=fetched["status"], source=SOURCE_COMPANYCONCEPT, stamp=stamp,
            accepted_map=accepted_map, next_session=next_session,
            units_seen=fetched["units_seen"], commit=commit))

    def valid_rows() -> int:
        return sum(r.get("rows_valid", 0) for r in report["concepts"].values())

    if valid_rows() == 0 and fallback:
        report["fallback_used"] = True
        for key in fallback:
            concept = concept_for(key)
            fetched = fetch_eps_concept(padded, concept, cache_dir)
            report["requests"] += 1
            if fetched["status"] == 200 and fetched["empty_200"]:
                report["empty_200"].append(key)
            accumulate(key, _store_concept(
                conn, entity_id, padded, concept, fetched["rows"],
                status=fetched["status"], source=SOURCE_COMPANYCONCEPT,
                stamp=stamp, accepted_map=accepted_map,
                next_session=next_session, units_seen=fetched["units_seen"],
                commit=commit))
        if valid_rows() > 0:
            report["notes"].append("recovered_on_basic_and_diluted")

    statuses = [r["http_status"] for r in report["concepts"].values()]
    if statuses and all(s == 404 for s in statuses):
        report["notes"].append("no_xbrl_eps_concepts")
    if report["empty_200"]:
        report["notes"].append("empty_200_from_companyconcept")

    if derive_ttm and valid_rows() > 0:
        ttm = derive_ttm_for_entity(conn, entity_id)
        report["ttm_rows_written"] = ttm["rows_written"]
        report["ttm"] = {k: v for k, v in ttm.items() if k != "rows"}
        if commit:
            conn.commit()
    return report


# ==========================================================================
# (6) THE DERIVED TTM
# ==========================================================================

#: How far a quarter's period end may sit from the ideal 91.31-day step and
#: still be accepted as "the previous quarter". Fiscal quarters are 13-week or
#: calendar-month affairs and a 52/53-week filer's quarter ends move by up to a
#: week a year, so an exact-date join would silently refuse Apple.
QUARTER_STEP_DAYS = 91.3125
QUARTER_TOLERANCE_DAYS = 25

#: TTM rows are DERIVED WEIGHT and the disk they occupy has to earn a reader.
#: The replay grid opens at 2013-01-31 and a TTM is an annual-length quantity
#: that `pit_policy` retires 15 months after its period end, so a TTM period
#: ending before this floor can never back a feature at ANY grid date -- it
#: would be retained weight with no reader, which on a volume at 96% is not a
#: neutral choice. `pit_cohort_eps` applies the same floor to its scope
#: arithmetic for the same reason.
#:
#: The floor bounds the DERIVED row only. Its COMPONENTS may be older: the four
#: quarters behind a 2011-09-30 TTM run back to 2010-12-31, and those primitives
#: are stored exactly as filed. Nothing that was fetched is discarded.
TTM_PERIOD_FLOOR = "2011-09-01"

#: Half a cent. EPS is filed to the cent, so two derived TTM figures closer
#: together than this are the same published number and a float artefact, not a
#: restatement. Used ONLY to decide whether a re-filing re-affirmed a value or
#: changed it; it never rounds a stored number.
_VALUE_EPSILON = 0.005


def _pick_vintage(rows: Sequence[dict[str, Any]], as_of: str
                  ) -> Optional[dict[str, Any]]:
    """The newest vintage of one (period, qtrs) knowable on `as_of`.

    Same ordering as `pit_store.fact_as_of`: available_date, then filed, then
    accn. The accn tie-break is not decoration -- Apple filed a 10-K/A and a
    10-Q on the same day, 2010-01-25.
    """
    best: Optional[dict[str, Any]] = None
    for row in rows:
        if row["available_date"] > as_of:
            continue
        if best is None or (row["available_date"], row["filed"], row["accn"]) > (
                best["available_date"], best["filed"], best["accn"]):
            best = row
    return best


def _entity_eps_rows(conn: sqlite3.Connection, entity_id: int,
                     concept_key: Optional[str] = None) -> list[dict[str, Any]]:
    sql = ("SELECT concept_key, period_start, period_end, qtrs, val, accn, form, "
           "filed, available_date FROM pit_eps_obs WHERE entity_id = ?")
    params: list[Any] = [entity_id]
    if concept_key:
        sql += " AND concept_key = ?"
        params.append(concept_key)
    return [dict(zip(
        ("concept_key", "period_start", "period_end", "qtrs", "val", "accn",
         "form", "filed", "available_date"), row))
        for row in conn.execute(sql, params)]


def derive_ttm_for_entity(conn: sqlite3.Connection, entity_id: int, *,
                          period_floor: str = TTM_PERIOD_FLOOR,
                          commit: bool = False) -> dict[str, Any]:
    """Build every TTM EPS vintage for one issuer and store it. Returns a report.

    THE RULE (`ttm_eps_rule_v1`), applied per (entity, concept, period end):

      1. If a filed ANNUAL figure (qtrs = 4) exists at this period end, that IS
         the TTM. `method = annual_figure`, one component. Preferred because it
         is the audited number, computed by the filer against its own weighted
         average share count.
      2. Otherwise sum FOUR consecutive quarterly figures (qtrs = 1) ending at
         this period end. `method = four_quarters`, four components, every one
         required -- a partial sum is a wrong number, not a partial one, which
         is the same rule `pit_policy`'s composite debt rung states.

    The two are NOT the same measurement. Each quarterly EPS is divided by that
    quarter's own weighted average share count, so their sum differs from the
    annual figure for any issuer whose count moved during the year. `method` is
    on every row so a consumer can group by it rather than average across it.

    VINTAGES. A TTM is rebuilt at every availability frontier its components
    cross, and a row is emitted only when the component ACCESSION SET changes,
    so a restatement produces a new row and a re-filing of an unchanged number
    does not. `available_date` is the MAX over components: an assembly is
    knowable only once its last component is.

    SIGN CROSSING is resolved here because it is the only place that can see
    the sequence: each row records the previous period's TTM as it stood at
    this row's own availability date, and sets `sign_crossing` when the two
    signs differ. No look-ahead -- the comparison uses only vintages available
    on this row's available_date.

    `period_floor` bounds the DERIVED row, never the primitives: a TTM ending
    before the floor cannot back a feature at any grid date, because the grid
    opens at 2013-01-31 and the annual staleness bound is 15 months. Components
    older than the floor are used normally -- the four quarters behind a
    2011-09-30 TTM reach back to 2010-12-31.
    """
    all_rows = _entity_eps_rows(conn, entity_id)
    report: dict[str, Any] = {"entity_id": entity_id, "rows_written": 0,
                              "periods": 0, "by_method": {}, "by_sign": {},
                              "crossings": 0, "reaffirmations": 0, "rows": []}
    if not all_rows:
        return report

    stamp = _now()
    out_rows: list[tuple[Any, ...]] = []

    for concept in EPS_LADDER:
        rows = [r for r in all_rows if r["concept_key"] == concept]
        if not rows:
            continue
        annual: dict[str, list[dict[str, Any]]] = {}
        quarterly: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            if int(row["qtrs"]) == 4:
                annual.setdefault(row["period_end"], []).append(row)
            elif int(row["qtrs"]) == 1:
                quarterly.setdefault(row["period_end"], []).append(row)

        q_ends = sorted(quarterly)
        q_ord = [_dt.date.fromisoformat(d).toordinal() for d in q_ends]

        def prior_quarters(period_end: str) -> Optional[list[str]]:
            """The three quarter ends before `period_end`, by date proximity."""
            base = _dt.date.fromisoformat(period_end).toordinal()
            picked: list[str] = []
            for step in (1, 2, 3):
                target = base - int(round(step * QUARTER_STEP_DAYS))
                best_i, best_d = None, None
                for i, o in enumerate(q_ord):
                    d = abs(o - target)
                    if d <= QUARTER_TOLERANCE_DAYS and (best_d is None or d < best_d):
                        best_i, best_d = i, d
                if best_i is None:
                    return None
                picked.append(q_ends[best_i])
            return picked

        periods = sorted(p for p in (set(annual) | set(quarterly))
                         if p >= period_floor)
        # One (period -> emitted vintages) table per concept, so the sign-crossing
        # lookup can ask "what did the previous period's TTM say on THIS date".
        emitted: dict[str, list[dict[str, Any]]] = {}

        for period_end in periods:
            if period_end in annual:
                candidates = annual[period_end]
                component_sets: list[list[list[dict[str, Any]]]] = [[candidates]]
                method = METHOD_ANNUAL
            else:
                quarters = prior_quarters(period_end)
                if quarters is None:
                    continue
                component_sets = [[quarterly[period_end]]
                                  + [quarterly[q] for q in quarters]]
                method = METHOD_FOUR_QUARTERS
            buckets = component_sets[0]

            frontier = sorted({r["available_date"] for b in buckets for r in b})
            last_key: Optional[str] = None
            last_value: Optional[float] = None
            last_method: Optional[str] = None
            for as_of in frontier:
                picked = [_pick_vintage(b, as_of) for b in buckets]
                if any(p is None for p in picked):
                    continue
                accns = sorted({p["accn"] for p in picked})
                accn_key = "|".join(accns)
                if accn_key == last_key:
                    continue
                last_key = accn_key
                value = sum(float(p["val"]) for p in picked)
                # A RE-AFFIRMATION IS NOT A NEW VINTAGE. Apple's FY2023 diluted
                # EPS of 6.13 appears in the FY2023, FY2024 and FY2025 10-Ks
                # under three accessions and is the same number every time. The
                # PRIMITIVES keep all three rows -- that is what pit_eps_obs is
                # for -- but a DERIVED row exists to answer "what was the TTM
                # knowable on this date", and a repeat of an unchanged number
                # does not change that answer for any date. Emitting it would
                # be disk with no reader. A row is written when the derived
                # VALUE or METHOD changes, which is exactly when the answer
                # changes; Apple's 11.89 -> 2.97 split restatement is such a
                # change and is emitted.
                if last_value is not None and last_method == method and (
                        abs(value - last_value) <= _VALUE_EPSILON):
                    report["reaffirmations"] += 1
                    continue
                last_value, last_method = value, method
                avail = max(p["available_date"] for p in picked)
                case = sign_case(value)
                prev_end, prev_val = _previous_ttm(
                    emitted, periods, period_end, avail)
                crossing = _signs_differ(prev_val, value)
                if crossing:
                    report["crossings"] += 1
                # (period_end, qtrs, accn) is EXACTLY pit_eps_obs's unique key
                # minus the entity, the tag and the unit -- all three of which
                # this row already carries. So the triple is a lossless
                # foreign key: val, form, filed and available_date are one join
                # away and are not copied here. On a volume at 96% a derived
                # table that duplicates its own parents is weight with no
                # information, and the components of a four-quarter TTM are
                # four of them.
                components = [[p["period_end"], int(p["qtrs"]), p["accn"]]
                              for p in picked]
                out_rows.append((
                    entity_id, concept, period_end, value, method, len(picked),
                    json.dumps(components, separators=(",", ":")), accn_key,
                    1 if len(accns) > 1 else 0, avail, case,
                    1 if crossing else 0, prev_end, prev_val,
                    pe_leg_for(case), EPS_LADDER_VERSION, TTM_RULE_VERSION,
                    stamp,
                ))
                emitted.setdefault(period_end, []).append(
                    {"available_date": avail, "ttm_eps": value})
                report["by_method"][method] = report["by_method"].get(method, 0) + 1
                report["by_sign"][case] = report["by_sign"].get(case, 0) + 1
            if period_end in emitted:
                report["periods"] += 1

    if out_rows:
        report["rows_written"] = insert_ttm_rows(conn, out_rows)
        if commit:
            conn.commit()
    return report


def _previous_ttm(emitted: dict[str, list[dict[str, Any]]],
                  periods: Sequence[str], period_end: str, as_of: str
                  ) -> tuple[Optional[str], Optional[float]]:
    """The nearest EARLIER period's TTM as it stood on `as_of`.

    Strictly backward-looking in BOTH senses: an earlier period end, and only
    vintages whose availability date is at or before this row's own. So a
    crossing flag can never be set from a restatement that had not landed yet,
    and it can never be set from a period that had not happened yet.
    """
    try:
        index = periods.index(period_end)
    except ValueError:
        return None, None
    for i in range(index - 1, -1, -1):
        vintages = emitted.get(periods[i])
        if not vintages:
            continue
        usable = [v for v in vintages if v["available_date"] <= as_of]
        if not usable:
            continue
        prev = max(usable, key=lambda v: v["available_date"])
        return periods[i], float(prev["ttm_eps"])
    return None, None


def _signs_differ(a: Optional[float], b: Optional[float]) -> bool:
    """Whether two TTM figures sit on opposite sides of zero. Zero is its own side."""
    if a is None or b is None:
        return False
    sa = 0 if a == 0 else (1 if a > 0 else -1)
    sb = 0 if b == 0 else (1 if b > 0 else -1)
    return sa != sb


# ==========================================================================
# (7) SELECTION -- the point-in-time TTM EPS
# ==========================================================================

#: The concept name the staleness policy is keyed on. No override in
#: `pit_policy.CONCEPT_MAX_AGE_MONTHS`, so a TTM EPS -- an annual-length
#: quantity, qtrs = 4 -- gets the 15-month annual budget. That is deliberately
#: NOT the four-month bound `shares_outstanding` carries, which is the bound
#: that produced the January/October valuation collapse: an EPS is republished
#: every quarter and 15 months is one annual reporting cycle plus the filing lag.
STALENESS_CONCEPT = "eps_ttm"
STALENESS_QTRS = 4


def ttm_eps_as_of_detail(conn: sqlite3.Connection, entity_id: int, as_of: str, *,
                         ladder: Sequence[str] = EPS_LADDER,
                         enforce_staleness: bool = True) -> dict[str, Any]:
    """The TTM EPS knowable on `as_of`, WITH the reasoning. Always a record.

    Walks the ladder in order and returns the first concept with a fresh,
    contemporaneous TTM. Two bounds, and neither implies the other:

        period_end     <= as_of   the earnings must be OF a period that ended
        available_date <= as_of   the filing carrying them must have landed

    The record always carries `available`, `reason`, `ttm_eps`, `sign_case`,
    `pe_leg`, `method`, `concept_key`, `period_end`, `available_date`,
    `age_days` and `sign_crossing`. A NEGATIVE TTM is `available = True` with
    `pe_leg = unavailable_loss_making`: the EARNINGS are available and the
    MULTIPLE is not, and collapsing those two into one boolean is how a loss
    ends up imputed.
    """
    as_of = str(as_of)[:10]
    record: dict[str, Any] = {
        "entity_id": entity_id, "as_of": as_of, "available": False,
        "reason": None, "ttm_eps": None, "sign_case": None, "pe_leg": None,
        "method": None, "concept_key": None, "period_end": None,
        "available_date": None, "age_days": None, "sign_crossing": 0,
        "n_components": None, "accn_key": None, "stale": False,
        "eps_ladder_version": EPS_LADDER_VERSION,
        "ttm_rule_version": TTM_RULE_VERSION,
        "latency_policy_version": pit_policy.LATENCY_POLICY_VERSION,
        "ladder_trace": [],
    }
    stale_seen = False
    future_seen = False

    for key in ladder:
        row = conn.execute(
            """SELECT * FROM pit_eps_ttm
                WHERE entity_id = ? AND concept_key = ?
                  AND period_end <= ? AND available_date <= ?
                ORDER BY period_end DESC, available_date DESC, eps_ttm_id DESC
                LIMIT 1""",
            (entity_id, key, as_of, as_of)).fetchone()
        if row is None:
            any_row = conn.execute(
                "SELECT 1 FROM pit_eps_ttm WHERE entity_id = ? AND concept_key = ? LIMIT 1",
                (entity_id, key)).fetchone()
            future_seen = future_seen or any_row is not None
            record["ladder_trace"].append(
                {"concept_key": key,
                 "outcome": "not_yet_filed" if any_row else "no_rows"})
            continue
        row = dict(row) if not isinstance(row, dict) else row
        stale = pit_policy.is_stale(row["period_end"], as_of, STALENESS_QTRS,
                                    STALENESS_CONCEPT)
        if stale and enforce_staleness:
            stale_seen = True
            record["ladder_trace"].append(
                {"concept_key": key, "outcome": "stale",
                 "period_end": row["period_end"],
                 "age_days": _days_between(row["period_end"], as_of)})
            continue
        record.update({
            "available": True, "reason": None,
            "ttm_eps": float(row["ttm_eps"]), "sign_case": row["sign_case"],
            "pe_leg": row["pe_leg"], "method": row["method"],
            "concept_key": row["concept_key"], "period_end": row["period_end"],
            "available_date": row["available_date"],
            "age_days": _days_between(row["period_end"], as_of),
            "sign_crossing": int(row["sign_crossing"]),
            "n_components": int(row["n_components"]),
            "accn_key": row["accn_key"], "stale": bool(stale),
        })
        record["ladder_trace"].append({"concept_key": key, "outcome": "selected"})
        return record

    if stale_seen:
        record["reason"] = REASON_STALE
    elif future_seen:
        record["reason"] = REASON_NOT_YET_FILED
    else:
        record["reason"] = REASON_NEVER_FILED
    return record


def ttm_eps_as_of(conn: sqlite3.Connection, entity_id: int, as_of: str, **options: Any
                  ) -> Optional[dict[str, Any]]:
    """The point-in-time TTM EPS, or None. See `ttm_eps_as_of_detail`."""
    record = ttm_eps_as_of_detail(conn, entity_id, as_of, **options)
    return record if record["available"] else None


# ==========================================================================
# (8) THE SPEC A REPLAY STORES
# ==========================================================================

def eps_ladder_spec() -> dict[str, Any]:
    """The whole EPS policy as a JSON-serialisable record."""
    return {
        "module": "pit_eps",
        "eps_ladder_version": EPS_LADDER_VERSION,
        "ttm_rule_version": TTM_RULE_VERSION,
        "latency_policy_version": pit_policy.LATENCY_POLICY_VERSION,
        "lineage": ("CANDIDATE. concept_ladder_v1 is not edited and "
                    "concept_ladder_v2 is not extended -- v2's documented "
                    "invariant is that it introduces no new tag so the loaded "
                    "store serves it with no re-ingest, and three EPS tags "
                    "would falsify that."),
        "ladder": [concept_for(k).as_dict() for k in EPS_LADDER],
        "fetch_plan": {
            "primary": list(EPS_LADDER_PRIMARY),
            "fallback_when_primary_empty": list(EPS_LADDER_FALLBACK),
            "why": ("the approved scope is ~5,066 requests over 2,533 priced "
                    "entities = two concepts each; the third rung is fetched "
                    "only where the first two returned nothing"),
        },
        "unit": UNIT_USD_PER_SHARE,
        "staleness": {
            "concept": STALENESS_CONCEPT,
            "qtrs": STALENESS_QTRS,
            "max_age_months": pit_policy.max_age_months(STALENESS_QTRS,
                                                        STALENESS_CONCEPT),
            "why_not_the_shares_bound": (
                "shares_outstanding carries a 4-month quarterly override, which "
                "is the bound that expires before the next filing lands and "
                "collapses the valuation factor every January and October. A TTM "
                "EPS is an annual-length quantity and takes the 15-month annual "
                "budget -- one reporting cycle plus the filing lag."),
        },
        "ttm_rule": {
            "version": TTM_RULE_VERSION,
            "preferred": METHOD_ANNUAL,
            "fallback": METHOD_FOUR_QUARTERS,
            "quarter_tolerance_days": QUARTER_TOLERANCE_DAYS,
            "not_an_identity": ("four quarterly EPS figures do not sum to the "
                                "annual EPS for any issuer whose weighted "
                                "average share count moved during the year; "
                                "`method` is stored so a consumer can group by "
                                "it rather than average across it"),
            "availability_rule": "MAX over component available_dates",
            "period_floor": TTM_PERIOD_FLOOR,
            "vintage_rule": (
                "a derived row per CHANGE in the derived value or method, not "
                "per re-filing. The primitives keep every accession; a repeat "
                "of an unchanged number cannot change what was knowable on any "
                "date, so it is not a new vintage. CIK 320193's FY2019 diluted "
                "EPS moving 11.89 -> 2.97 across the 2020 4:1 split IS such a "
                "change and is emitted."),
            "split_restatement": (
                "EPS is restated retroactively for splits, exactly as share "
                "counts are. A point-in-time EPS is in the share basis of its "
                "own era and its P/E must be taken against a RAW, as-printed "
                "price -- see pit_shares.PRICE_BASIS_RAW. Apple 2020-01-31: "
                "$309.51 / 11.89 = 26.0 as printed; the same EPS against "
                "today's split-adjusted ~$77 prints 6.5."),
            "partial_is_wrong": ("all four quarters required; a three-quarter "
                                 "sum is a wrong number, not a partial one"),
        },
        "sign_cases": {
            SIGN_POSITIVE: "conventional P/E",
            SIGN_NEAR_ZERO_POSITIVE: (
                f"0 < EPS <= {EPS_NEAR_ZERO_ABS}: EXTREME valuation, not an "
                "ordinary large positive number"),
            SIGN_ZERO: "undefined; not infinity and not a missing value",
            SIGN_NEGATIVE: ("loss-making and NOT cheap; the P/E leg is "
                            "unavailable and the signed earnings yield goes to "
                            "the ML layer as a research candidate"),
            SIGN_CROSSING: "a STATE across periods, flagged on the row",
        },
        "never_abs": (
            "abs(P/E) maps (-inf, 0) onto (0, +inf) REVERSED and ranks the worst "
            "loss-makers as the cheapest securities. price_earnings() refuses a "
            "non-positive denominator and there is no flag to turn that off."),
        "never_stored": ["frame"],
        "why_eps_routes_around_the_binding_leaf": (
            "P(N>=3) for the EV/EBITDA cohort is 34.2% with all five leaves and "
            "61.6% with the share guard removed -- the defensible share count is "
            "the binding leaf. P/E needs no share count at all."),
    }
