"""pit_price_basis -- the price/share-basis policy, and the EPS pair hierarchy.

OWNER DECISIONS OF 2026-09-21. This module exists because a measurement showed
that ShafferFinEval could have produced a completely clean, perfectly
reproducible, economically FALSE P/E and EPS-growth history. Nothing here is a
bug fix: no wrong number was ever written, because `pit_feature` and `pit_score`
are still empty. This is the fence that keeps them from being written.

==============================================================================
DECISION 2 -- TWO PRICE CONCEPTS, NEVER INTERCHANGEABLE
==============================================================================

    raw / as-traded price        ->  VALUATION
    split/action-adjusted price  ->  RETURNS and LABELS

They are different primitives with different names and this module refuses to
let one stand in for the other. The usual "fully adjusted historical price
series" is DANGEROUS for valuation precisely because it is complete: it encodes
corporate actions that were not yet effective, and not yet knowable, at the
historical score date. A back-adjusted price is a statement about today
projected backwards; a Shaffer score is a statement about what was knowable
then.

The measurement, live from the store (`pit_eps_obs`, entity 224 = CIK 320193):

    Apple at 2020-01-31, before the 4:1 split of 2020-08-31
      as traded that day      $309.51        / 11.89  =  26.03
      the SAME day restated   $309.51 / 4    / 11.89  =   6.51

A mega cap wearing a deep-value multiple, wrong by EXACTLY the split factor,
with clean provenance and no error raised anywhere. Note both lines are the
same day's trade: the second is what a back-adjusted series reports for
2020-01-31 once the August split has happened. Nothing about the price moved.

THE REFINEMENT THAT MAKES IT CORRECT ON BOTH SIDES OF A SPLIT. Raw price alone
is not enough, because after the split the most recently KNOWABLE EPS is still
the pre-split figure. EPS must be carried to the SCORE-DATE share basis:

    PE(t) = RawPrice(t) / EPS_pit normalised to the share basis at t

      before   309.51 / 11.89            = 26.03
      after     77.00 / (11.89 / 4)      = 25.90

Two dates either side of a 4:1 split, two readings of the same company, 0.5%
apart -- which is the price move, not the split.

THE POINT-IN-TIME GATE. Only corporate actions with

    event_date <= as_of

may participate. A future split must NEVER back-adjust a historical P/E. An
action needed but not yet effective is not "close enough"; it is the future.

==============================================================================
DECISION 3 -- THE EPS PAIR HIERARCHY, AND CLASSIFY ONLY AFTER NORMALISING
==============================================================================

An earlier report said the corporate-action table blocks every two-period EPS
factor. That was too strong. A filing that presents current and prior-period
per-share amounts presents them on a CONSISTENT basis -- the issuer has already
done the restatement. So:

    1. same-filing comparative pair      PREFERRED
    2. different-filing pair             harmonise both to a common share basis
                                         using PIT-safe corporate actions
    3. basis unresolved                  REFUSED_SHARE_BASIS_UNKNOWN

Rung 1 is not a special case. Measured over the whole store:

    (entity, accn, qtrs) groups            180,599
      carrying >= 2 distinct period_ends   179,130   99.19%
    annual (qtrs=4) filings                 28,734
      carrying a comparative period         28,364   98.71%
    entities with any annual EPS             2,411
      with >= 1 same-filing pair             2,395   99.34%

THE PREMISE IS PROVEN, NOT ASSUMED. Apple's own filings, read from the store:

    filed 2019-10-31  10-K   FY2019 (2019-09-28)  diluted  11.89
    filed 2020-10-30  10-K   FY2019 (2019-09-28)  diluted   2.97   <- restated
    filed 2020-10-30  10-K   FY2020 (2020-09-26)  diluted   3.28

The FY2020 10-K restated its own comparative across the split. The three paths
therefore give:

    rung 1  same-filing comparative     +10.4377%
    rung 2  cross-filing, harmonised    +10.3448%
    rung 3  naive cross-filing pair     -72.4138%   <-- FABRICATED

A company that grew diluted EPS 10% reads as a 72% collapse. Note what does NOT
save us: both figures are strictly positive, so the pair classifies as
`both_positive` and the sign-cross logic never fires. THIS IS WHY THE
CLASSIFIER MUST RUN AFTER NORMALISATION. A split can manufacture a fake
profit-to-loss, fake growth or a giant collapse before any sign-cross rule sees
the values.

Rung 1 is preferred over rung 2 even where both are available, and the Apple
case shows why in one number: they differ by 9.29 basis points, because the
issuer rounded 11.89/4 = 2.9725 to the reported 2.97. Rung 1 is the issuer's
own restatement at the issuer's own precision. Rung 2 is our arithmetic on
their pre-split figure.

FOR TTM CONSTRUCTIONS ASSEMBLED ACROSS FILINGS the corporate-action table
remains directly binding, and this module says so rather than implying rung 1
rescues everything.

==============================================================================
WHAT IS CONVENTION HERE, NAMED RATHER THAN HIDDEN IN CODE
==============================================================================

`EPS_GROWTH_MIN_POSITIVE_BASE_V1` is a threshold, therefore a convention,
therefore it gets a version string and a rationale. Its rationale is REPORTING
PRECISION, not a claim that one cent has economic meaning: US per-share amounts
are reported to the cent, so a base at or below one cent sits at the quantum of
the disclosure itself and its relative change measures rounding.

Stdlib only. Read-only against the store.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Optional

import pit_eps
import pit_shares

__all__ = [
    "PRICE_BASIS_POLICY_VERSION", "EPS_PAIR_POLICY_VERSION",
    "PRICE_RAW_AS_TRADED", "PRICE_ACTION_ADJUSTED", "PRICE_CONCEPTS",
    "PRICE_ROLE_VALUATION", "PRICE_ROLE_RETURN", "PRICE_ROLE_REQUIRED_BASIS",
    "PRICE_ROLE_WHY", "PriceBasisError", "assert_price_basis",
    "price_concept_for_role",
    "EPS_GROWTH_MIN_POSITIVE_BASE_V1", "MIN_POSITIVE_BASE_RATIONALE",
    "PAIR_SAME_FILING", "PAIR_CROSS_FILING_HARMONISED", "PAIR_REFUSED",
    "REFUSED_SHARE_BASIS_UNKNOWN", "PAIR_PATHS", "PAIR_PATH_COVERAGE",
    "APPLE_SPLIT_CASE", "ACTION_GATE_VERSION", "ACTION_GATE_SEMANTICS_V2",
    "GATE_OK", "GATE_NO_EVENTS", "GATE_UNCHECKED", "GATE_FUTURE_ACTION_REFUSED",
    "GATE_INCOMPLETE_RATIO", "COVERAGE_ESTABLISHED", "COVERAGE_UNKNOWN",
    "ActionGate", "pit_safe_split_factor",
    "PRICE_BASIS_TRANSLATION_V1", "translate_price_basis",
    "VALUATION_PRICE_POLICY_V1",
    "EpsPair", "same_filing_pair", "harmonise_pair", "resolve_eps_pair",
    "NormalisedPE", "normalised_pe", "policy_record", "validate",
]

PRICE_BASIS_POLICY_VERSION = "price_basis_policy_v1"
EPS_PAIR_POLICY_VERSION = "eps_pair_basis_policy_v1"


# ==========================================================================
# DECISION 2 -- the two price concepts
# ==========================================================================

#: What a VALUATION multiple is taken against. The price actually printed on
#: the tape that day, unadjusted by anything that happened afterwards.
PRICE_RAW_AS_TRADED = "price_raw_as_traded"

#: What a RETURN or a LABEL is computed from. Back-adjusted for splits (and,
#: for a total-return label, distributions) so that a ratio of two dates is a
#: holding-period return rather than a corporate-action artefact.
PRICE_ACTION_ADJUSTED = "price_action_adjusted"

PRICE_CONCEPTS: tuple[str, ...] = (PRICE_RAW_AS_TRADED, PRICE_ACTION_ADJUSTED)

PRICE_ROLE_VALUATION = "valuation"
PRICE_ROLE_RETURN = "return_or_label"

#: The whole of decision 2 in one mapping. A role does not have a DEFAULT price
#: basis it can be talked out of; it has a REQUIRED one.
PRICE_ROLE_REQUIRED_BASIS: dict[str, str] = {
    PRICE_ROLE_VALUATION: PRICE_RAW_AS_TRADED,
    PRICE_ROLE_RETURN: PRICE_ACTION_ADJUSTED,
}

#: Why each role requires what it requires -- kept beside the mapping so the
#: reason travels with the rule.
PRICE_ROLE_WHY: dict[str, str] = {
    PRICE_ROLE_VALUATION: (
        "A point-in-time EPS, book value or share count is in the share basis "
        "of its own era. An adjusted price is in TODAY's basis. Dividing one "
        "by the other is wrong by every split since the period end, with no "
        "error raised anywhere -- Apple at 2020-01-31 reads 26.0 as printed "
        "and 6.5 adjusted. A fully adjusted series is dangerous here BECAUSE "
        "it is complete: it encodes actions that were not yet effective, and "
        "not yet knowable, at the historical score date."),
    PRICE_ROLE_RETURN: (
        "A holding-period return must not book a 4:1 split as a 75% loss. "
        "Here the back-adjustment is the correct treatment and the as-traded "
        "price is the wrong one -- which is exactly why the two concepts must "
        "not share a name."),
}


#: GAP 7 CLOSED (owner ruling R20, 2026-09-23): ONE digested translation from
#: the two other spellings of "the as-traded price" to the valuation role's
#: required basis, consumed by ONE resolver (pit_replay.valuation_price).
#:   'price_adjusted'              the pit_factor_spec / pit_derive primitive
#:                                 vocabulary, whose name predates decision 2
#:                                 and means the price a valuation is taken on;
#:   'raw_as_printed_unadjusted'   pit_shares / pit_rawprice's spelling on the
#:                                 record raw_close_as_of returns.
#: Both mean PRICE_RAW_AS_TRADED for a valuation and nothing else; an
#: 'price_action_adjusted' record has NO entry here, so translating it raises
#: through assert_price_basis rather than being waved through.
PRICE_BASIS_TRANSLATION_V1: dict[str, str] = {
    "price_adjusted": PRICE_RAW_AS_TRADED,
    pit_shares.PRICE_BASIS_RAW: PRICE_RAW_AS_TRADED,
    PRICE_RAW_AS_TRADED: PRICE_RAW_AS_TRADED,
}


#: Owner ruling R19 (2026-09-23): the price every valuation leg uses. Exact
#: score-date raw close, else the latest PRIOR valid raw close within ten
#: CALENDAR days, on the SAME listing, a real trade (volume > 0), with the
#: age of the price written on the row. No share-class switching: an entity
#: with more than one scored listing on the date is REFUSED, never guessed.
VALUATION_PRICE_POLICY_V1: dict[str, Any] = {
    "policy_version": "valuation_price_policy_v1",
    "status": "OWNER_RULING_2026_09_23 (R19)",
    "basis": PRICE_RAW_AS_TRADED,
    "translation": "PRICE_BASIS_TRANSLATION_V1",
    "selection": "the exact score-date raw close, else the latest prior valid raw close",
    "roll_back_days": 10,
    "roll_back_unit": "calendar days",
    "require_traded": True,
    "same_listing": True,
    "listing_rule": ("exactly ONE scored listing for the entity on the date "
                     "(pit_identity.scored_universe_as_of); zero -> no_scored_listing; "
                     "more than one -> listing_ambiguous -- no share-class switching"),
    "record": "price_age_days (bar_date to score date) and bar_date on the row",
    "invalid_bar_rule": ("a NULL, non-positive or zero-volume print inside the window is "
                         "SKIPPED and the next earlier bar is tried, newest first; the "
                         "ten-day floor is never widened; the skipped bars travel on the "
                         "row ('sk')"),
    "straddle_rule": ("a rolled-back price is REFUSED (price_straddles_corporate_action) "
                      "when a share-applicable corporate action falls in (bar_date, "
                      "score date]: the price and the score-date share basis would "
                      "straddle the split"),
    "shared_by": ("pe_absolute", "pe_relative", "ev_ebitda_supplement", "debt_market_cap"),
}


def translate_price_basis(basis: str) -> str:
    """The valuation-role basis a record's own spelling stands for.

    Raises PriceBasisError for a spelling with no entry: an adjusted price
    has no translation to a valuation basis, and neither does a typo.
    """
    try:
        return PRICE_BASIS_TRANSLATION_V1[str(basis)]
    except KeyError:
        raise PriceBasisError(
            "price basis %r has no translation to a valuation basis; "
            "PRICE_BASIS_TRANSLATION_V1 admits %s. %s"
            % (basis, sorted(PRICE_BASIS_TRANSLATION_V1),
               PRICE_ROLE_WHY[PRICE_ROLE_VALUATION])) from None


class PriceBasisError(TypeError):
    """Raised when a price basis is used for a role that forbids it.

    A TypeError rather than a ValueError on purpose: substituting an adjusted
    price for a raw one is not a bad value, it is the wrong KIND of thing.
    """


def assert_price_basis(role: str, basis: str) -> str:
    """Refuse the substitution decision 2 forbids. Returns `basis` when legal."""
    if role not in PRICE_ROLE_REQUIRED_BASIS:
        raise PriceBasisError(
            "unknown price role %r; known roles are %s"
            % (role, ", ".join(sorted(PRICE_ROLE_REQUIRED_BASIS))))
    required = PRICE_ROLE_REQUIRED_BASIS[role]
    if basis != required:
        raise PriceBasisError(
            "role %r requires %s and was handed %s. These are NOT "
            "interchangeable primitives. %s"
            % (role, required, basis, PRICE_ROLE_WHY[role]))
    return basis


def price_concept_for_role(role: str) -> str:
    """The primitive name a factor must declare for this role."""
    if role not in PRICE_ROLE_REQUIRED_BASIS:
        raise PriceBasisError("unknown price role %r" % (role,))
    return PRICE_ROLE_REQUIRED_BASIS[role]


# ==========================================================================
# THE NAMED CONVENTION -- decision 3's threshold
# ==========================================================================

#: The smallest EPS that may serve as the BASE of a percentage growth rate.
#:
#: A CONVENTION, versioned for that reason. Equal in value to
#: `pit_eps.EPS_NEAR_ZERO_ABS`, and deliberately NOT an alias of it: that
#: constant classifies a LEVEL as an extreme-valuation case, this one gates a
#: DENOMINATOR. Two rules that happen to agree on a number today are still two
#: rules, and folding them together would mean a later change to either one
#: silently moved the other.
EPS_GROWTH_MIN_POSITIVE_BASE_V1 = 0.01

MIN_POSITIVE_BASE_RATIONALE = (
    "REPORTING PRECISION, not economic significance. US per-share amounts are "
    "reported to the cent, so an EPS at or below $0.01 sits at the quantum of "
    "the disclosure itself: the difference between $0.005 and $0.01 is one "
    "rounding step, and a growth rate computed on it measures that step. "
    "$0.005 -> $0.50 prints +9,900%, and one further cent of prior earnings "
    "halves it to +4,850% -- a 5,050-point swing produced by rounding. The "
    "threshold does NOT assert that a penny of earnings is economically "
    "meaningless; it asserts that a RATIO whose denominator is one reporting "
    "quantum is a statement about the quantum."
)


# ==========================================================================
# DECISION 3 -- the pair hierarchy
# ==========================================================================

PAIR_SAME_FILING = "same_filing_comparative"
PAIR_CROSS_FILING_HARMONISED = "cross_filing_harmonised"
PAIR_REFUSED = "refused"

#: The refusal reason, spelled as the owner named it.
REFUSED_SHARE_BASIS_UNKNOWN = "REFUSED_SHARE_BASIS_UNKNOWN"

#: Ordered best-first. `resolve_eps_pair` walks this and records which rung
#: answered, so a factor built on rung 2 is distinguishable from rung 1 in the
#: stored feature rather than only in a log.
PAIR_PATHS: tuple[str, ...] = (
    PAIR_SAME_FILING, PAIR_CROSS_FILING_HARMONISED, PAIR_REFUSED,
)

#: Measured 2026-09-21, read-only over the whole store. Quote with the name.
PAIR_PATH_COVERAGE: dict[str, Any] = {
    "measured_on": "2026-09-21",
    "scope": "pit_eps_obs, whole store, SURVIVOR_ONLY price universe",
    "filing_groups_entity_accn_qtrs": 180599,
    "filing_groups_with_comparative": 179130,
    "filing_groups_with_comparative_pct": 99.19,
    "annual_filings_qtrs4": 28734,
    "annual_filings_with_comparative": 28364,
    "annual_filings_with_comparative_pct": 98.71,
    "entities_with_annual_eps": 2411,
    "entities_with_same_filing_pair": 2395,
    "entities_with_same_filing_pair_pct": 99.34,
    "caveat": (
        "Availability of a comparative period is NOT the same as a factor "
        "being computable: the pair must still clear the sign and base gates, "
        "and a TTM assembled across filings is not a same-filing pair at all."),
}

#: The worked proof that rung 1's premise holds, and what rung 3 would have
#: produced. All four figures read from the store or computed from figures read
#: from it; none is illustrative.
APPLE_SPLIT_CASE: dict[str, Any] = {
    "entity_id": 224,
    "cik": "0000320193",
    "split": {"event_date": "2020-08-31", "ratio": "4:1", "confidence": "inferred"},
    "rows_as_stored": [
        {"filed": "2019-10-31", "accn": "0000320193-19-000119",
         "period_end": "2019-09-28", "concept": "eps_diluted", "val": 11.89},
        {"filed": "2020-10-30", "accn": "0000320193-20-000096",
         "period_end": "2019-09-28", "concept": "eps_diluted", "val": 2.97},
        {"filed": "2020-10-30", "accn": "0000320193-20-000096",
         "period_end": "2020-09-26", "concept": "eps_diluted", "val": 3.28},
    ],
    "rung_1_same_filing_pct": 10.4377,
    "rung_2_harmonised_pct": 10.3448,
    "rung_3_naive_cross_filing_pct": -72.4138,
    "rung_1_minus_rung_2_bp": 9.2883,
    "why_they_differ": (
        "The issuer rounded 11.89 / 4 = 2.9725 to the reported 2.97. Rung 1 is "
        "the issuer's own restatement at the issuer's own precision; rung 2 is "
        "our arithmetic on their pre-split figure."),
    "why_the_sign_logic_does_not_save_us": (
        "3.28 and 11.89 are both strictly positive, so the naive pair "
        "classifies as both_positive and PRINTS -72.4138%. No sign cross, no "
        "zero, no near-zero base. The classifier must run AFTER normalisation."),
}


# ==========================================================================
# THE POINT-IN-TIME ACTION GATE
# ==========================================================================

#: v2, owner ruling R18 (2026-09-23): LISTING-LEVEL COMPLETENESS SEMANTICS.
#: v1 read an empty window as `no_events_recorded` and refused it, because an
#: empty window was ambiguous between "no split happened" and "splits were
#: never ingested for this listing". v2 asks the listing, not the window:
#:   coverage ESTABLISHED  the listing carries ANY corporate-action row, so
#:                         an empty window means no action -> factor 1.0,
#:                         GATE_OK (the identity is a resolved answer);
#:   coverage UNKNOWN      the listing carries NO row at all (673 of 2,576
#:                         listings) -> GATE_NO_EVENTS, NOT usable, as before.
#: The version string moves because a version string is what the freeze
#: digests for this gate; the semantics body below is digested beside it.
ACTION_GATE_VERSION = "pit_safe_corporate_action_gate_v2"

GATE_OK = "resolved"
GATE_NO_EVENTS = "no_events_recorded"
GATE_UNCHECKED = "unchecked"

COVERAGE_ESTABLISHED = "coverage_established"
COVERAGE_UNKNOWN = "coverage_unknown"

ACTION_GATE_SEMANTICS_V2: dict[str, str] = {
    "gate_version": ACTION_GATE_VERSION,
    "supersedes": "pit_safe_corporate_action_gate_v1",
    "status": "OWNER_RULING_2026_09_23 (R18)",
    "window": "events with after < event_date <= through, through <= as_of (a later window RAISES)",
    "coverage_established": ("the listing carries at least one corporate-action row dated "
                             "on or before as_of (the probe is POINT-IN-TIME bounded): an "
                             "empty window is 'no action happened' -> factor 1.0, status "
                             "resolved, usable"),
    "coverage_unknown": ("the listing carries no corporate-action row dated on or before "
                         "as_of: an empty window is ambiguous -> status "
                         "no_events_recorded, NOT usable"),
    "future_action": "an event needed by the window but dated after as_of -> future_action_refused",
    "incomplete_ratio": "a share-applicable row without a usable ratio -> incomplete_ratio",
    "price_only_events": "rows with applies_to_shares = 0 (spin-offs) are ignored as share divisors",
    "v1_difference": ("v1 refused every empty window; v2 refuses only the empty window of a "
                      "listing with no coverage"),
}

#: An action needed by the window is not yet effective at `as_of`.
#:
#: HONEST NOTE ON REACHABILITY: this status is UNREACHABLE today by
#: construction, and that is the point rather than a defect. Point-in-time
#: safety is enforced twice over -- `pit_safe_split_factor` raises when
#: `through > as_of`, and the SQL bounds `event_date <= through`. So no
#: selected event can postdate `as_of`. The branch stays as defence in depth:
#: if a later caller ever relaxes the window rule, the events become a refusal
#: rather than silently applying a future split to a historical figure.
GATE_FUTURE_ACTION_REFUSED = "future_action_refused"

#: A row that applies to shares but carries no usable ratio. A DIFFERENT
#: condition from a future action and therefore a different status: measured
#: 2026-09-21, zero such rows exist (1,270 splits all carry a ratio and all
#: 226 spin-offs are flagged price-only), but folding it into the future-action
#: status would mislabel it the day one appears.
GATE_INCOMPLETE_RATIO = "incomplete_ratio"


@dataclass(frozen=True)
class ActionGate:
    """The outcome of asking for a share-basis factor at a point in time."""

    factor: Optional[float]
    status: str
    as_of: str
    window_after: str
    window_through: str
    events_applied: tuple[dict[str, Any], ...] = ()
    events_refused_as_future: tuple[dict[str, Any], ...] = ()
    events_unusable: tuple[dict[str, Any], ...] = ()
    #: v2: whether the LISTING has any corporate-action coverage at all. An
    #: empty window is a resolved identity only when this is established.
    coverage: str = ""

    @property
    def usable(self) -> bool:
        """True only when a factor may be applied to a per-share figure.

        `no_events_recorded` is NOT usable. Under v2 it is reached only for a
        listing with NO corporate-action row at all, where an empty window is
        genuinely ambiguous between "no splits happened" and "splits were
        never ingested" -- 673 of 2,576 listings. A listing WITH coverage and
        an empty window resolves to the identity factor 1.0 (owner ruling
        R18, 2026-09-23).
        """
        return self.status == GATE_OK and self.factor is not None

    def to_record(self) -> dict[str, Any]:
        return {
            "gate_version": ACTION_GATE_VERSION,
            "factor": self.factor,
            "status": self.status,
            "usable": self.usable,
            "coverage": self.coverage,
            "as_of": self.as_of,
            "window": [self.window_after, self.window_through],
            "events_applied": list(self.events_applied),
            "events_refused_as_future": list(self.events_refused_as_future),
            "events_unusable": list(self.events_unusable),
        }


def pit_safe_split_factor(conn: sqlite3.Connection, listing_id: Optional[int],
                          after: str, through: str, as_of: str) -> ActionGate:
    """Cumulative share-basis multiplier for splits in (after, through], PIT-safe.

    The one thing this adds to `pit_shares.split_factor_between` is the gate the
    owner named: an event with `event_date > as_of` is REFUSED, not applied. A
    future split must never back-adjust a historical per-share figure, and the
    refusal is reported rather than silently dropping the event -- because a
    needed-but-future action means the pair is unresolvable at this as-of date,
    which is a different answer from "no action was needed".
    """
    if listing_id is None:
        return ActionGate(factor=None, status=GATE_UNCHECKED, as_of=as_of,
                          window_after=after, window_through=through)
    if through > as_of:
        # Asking about a window that has not closed yet is a caller error, not
        # a data condition: the window is the thing being made point-in-time.
        raise ValueError(
            "window end %s is after as_of %s; a share-basis window cannot "
            "extend past the date the score is being computed for"
            % (through, as_of))

    rows = conn.execute(
        """SELECT event_date, event_type, ratio_num, ratio_den, applies_to_shares
             FROM pit_corporate_action
            WHERE listing_id = ? AND event_date > ? AND event_date <= ?
            ORDER BY event_date""",
        (listing_id, after, through)).fetchall()
    # v2 (R18): coverage is a property of the LISTING, asked once, and asked
    # POINT-IN-TIME: only rows dated on or before as_of are evidence at as_of.
    covered = conn.execute(
        "SELECT 1 FROM pit_corporate_action WHERE listing_id = ? "
        "AND event_date <= ? LIMIT 1",
        (listing_id, as_of)).fetchone() is not None
    coverage = COVERAGE_ESTABLISHED if covered else COVERAGE_UNKNOWN

    applied: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    unusable: list[dict[str, Any]] = []
    factor = 1.0
    for event_date, event_type, num, den, applies_to_shares in rows:
        rec = {"event_date": event_date, "event_type": event_type,
               "ratio_num": num, "ratio_den": den}
        if event_date > as_of:
            refused.append(rec)
            continue
        if not applies_to_shares:
            # AT&T's "1324:1000" on 2022-04-11 is a spin-off: a valid price
            # adjustment and wrong by 32.4% as a share-count divisor.
            continue
        if not num or not den:
            unusable.append(rec)
            continue
        factor *= float(num) / float(den)
        applied.append(rec)

    if refused:
        return ActionGate(factor=None, status=GATE_FUTURE_ACTION_REFUSED,
                          as_of=as_of, window_after=after,
                          window_through=through,
                          events_applied=tuple(applied),
                          events_refused_as_future=tuple(refused),
                          coverage=coverage)
    if unusable:
        return ActionGate(factor=None, status=GATE_INCOMPLETE_RATIO,
                          as_of=as_of, window_after=after,
                          window_through=through,
                          events_applied=tuple(applied),
                          events_unusable=tuple(unusable),
                          coverage=coverage)
    if not rows and not covered:
        return ActionGate(factor=None, status=GATE_NO_EVENTS, as_of=as_of,
                          window_after=after, window_through=through,
                          coverage=coverage)
    # Coverage established and nothing in the window (or only price-only
    # rows): the identity is a RESOLVED answer under v2.
    return ActionGate(factor=factor, status=GATE_OK,
                      as_of=as_of, window_after=after, window_through=through,
                      events_applied=tuple(applied), coverage=coverage)


# ==========================================================================
# THE PAIR ITSELF
# ==========================================================================

@dataclass(frozen=True)
class EpsPair:
    """A two-period EPS pair brought onto one share basis, or refused.

    `classifiable` is the gate decision 3 turns on: the transition classifier
    may only see `prev_on_basis` / `curr`, and only when this is True.
    """

    path: str
    prev_on_basis: Optional[float]
    curr: Optional[float]
    prev_period_end: Optional[str] = None
    curr_period_end: Optional[str] = None
    prev_as_reported: Optional[float] = None
    basis_factor: Optional[float] = None
    accn: Optional[str] = None
    reason: Optional[str] = None
    gate: Optional[ActionGate] = None
    notes: tuple[str, ...] = ()

    @property
    def classifiable(self) -> bool:
        return (self.path != PAIR_REFUSED
                and self.prev_on_basis is not None
                and self.curr is not None)

    @property
    def same_share_basis(self) -> Optional[bool]:
        """What to hand `pit_valuation_spec.eps_transition`.

        True only on a resolved rung. On refusal this is None = UNKNOWN, never
        False -- we do not know the bases DIFFER, we know we cannot show they
        agree, and an unmeasured quantity is UNKNOWN.
        """
        return True if self.classifiable else None

    def to_record(self) -> dict[str, Any]:
        return {
            "policy_version": EPS_PAIR_POLICY_VERSION,
            "path": self.path,
            "classifiable": self.classifiable,
            "same_share_basis": self.same_share_basis,
            "prev_period_end": self.prev_period_end,
            "curr_period_end": self.curr_period_end,
            "prev_as_reported": self.prev_as_reported,
            "prev_on_basis": self.prev_on_basis,
            "curr": self.curr,
            "basis_factor": self.basis_factor,
            "accn": self.accn,
            "reason": self.reason,
            "gate": None if self.gate is None else self.gate.to_record(),
            "notes": list(self.notes),
        }


def same_filing_pair(conn: sqlite3.Connection, entity_id: int, *,
                     as_of: str, qtrs: int = 4,
                     concept_key: str = "eps_diluted",
                     curr_period_end: Optional[str] = None) -> Optional[EpsPair]:
    """Rung 1: two period_ends presented by ONE filing, available by `as_of`.

    Returns None when no such pair exists -- the caller then falls to rung 2.
    Selection is point-in-time twice over: the filing must be available by
    `as_of`, and among available filings the LATEST vintage wins, because a
    restatement is a new row and the newest knowable one is what a reader had.
    """
    params: list[Any] = [entity_id, qtrs, concept_key, as_of]
    extra = ""
    if curr_period_end is not None:
        extra = " AND period_end <= ?"
        params.append(curr_period_end)

    rows = conn.execute(
        """SELECT accn, filed, available_date, period_end, val
             FROM pit_eps_obs
            WHERE entity_id = ? AND qtrs = ? AND concept_key = ?
              AND available_date <= ?""" + extra + """
            ORDER BY available_date DESC, filed DESC, accn DESC, period_end DESC""",
        params).fetchall()
    if not rows:
        return None

    by_accn: dict[str, list[tuple[str, float]]] = {}
    order: list[str] = []
    for accn, _filed, _avail, period_end, val in rows:
        if accn not in by_accn:
            by_accn[accn] = []
            order.append(accn)
        by_accn[accn].append((period_end, float(val)))

    for accn in order:
        periods = sorted(set(by_accn[accn]), key=lambda pv: pv[0])
        # set() de-duplicates identical (period_end, val). A duplicated
        # period_end carrying DIFFERENT values inside one accession is a data
        # condition we must not average away, so it is skipped rather than
        # resolved.
        ends = [p for p, _ in periods]
        if len(set(ends)) < 2 or len(ends) != len(set(ends)):
            continue
        (prev_end, prev_val), (curr_end, curr_val) = periods[-2], periods[-1]
        if curr_period_end is not None and curr_end != curr_period_end:
            continue
        return EpsPair(
            path=PAIR_SAME_FILING,
            prev_on_basis=prev_val, curr=curr_val,
            prev_period_end=prev_end, curr_period_end=curr_end,
            prev_as_reported=prev_val, basis_factor=1.0, accn=accn,
            notes=("both figures are the issuer's own, presented together on "
                   "one basis; no arithmetic of ours touched either",),
        )
    return None


def harmonise_pair(conn: sqlite3.Connection, *,
                   prev_eps: Optional[float], curr_eps: Optional[float],
                   prev_period_end: str, curr_period_end: str,
                   listing_id: Optional[int], as_of: str) -> EpsPair:
    """Rung 2: two figures from different filings, brought to one basis.

    The prior figure is divided by the cumulative split factor over
    (prev_period_end, curr_period_end], so it lands in the CURRENT figure's
    basis. Only PIT-safe actions participate. Anything unresolved is rung 3.
    """
    if prev_eps is None or curr_eps is None:
        return EpsPair(path=PAIR_REFUSED, prev_on_basis=None, curr=curr_eps,
                       prev_period_end=prev_period_end,
                       curr_period_end=curr_period_end,
                       prev_as_reported=prev_eps,
                       reason=REFUSED_SHARE_BASIS_UNKNOWN,
                       notes=("a missing figure has no share basis to resolve",))

    gate = pit_safe_split_factor(conn, listing_id, prev_period_end,
                                 curr_period_end, as_of)
    if not gate.usable:
        return EpsPair(path=PAIR_REFUSED, prev_on_basis=None, curr=curr_eps,
                       prev_period_end=prev_period_end,
                       curr_period_end=curr_period_end,
                       prev_as_reported=prev_eps, gate=gate,
                       reason=REFUSED_SHARE_BASIS_UNKNOWN,
                       notes=("gate status %s is not a resolved basis"
                              % gate.status,))

    factor = float(gate.factor or 1.0)
    return EpsPair(
        path=PAIR_CROSS_FILING_HARMONISED,
        prev_on_basis=prev_eps / factor, curr=curr_eps,
        prev_period_end=prev_period_end, curr_period_end=curr_period_end,
        prev_as_reported=prev_eps, basis_factor=factor, gate=gate,
        notes=("prior figure divided by the cumulative PIT-safe split factor "
               "to land in the current figure's basis",
               "our arithmetic, at our precision -- rung 1 is preferred where "
               "it exists because the issuer's own restatement is not rounded "
               "by us"),
    )


def resolve_eps_pair(conn: sqlite3.Connection, entity_id: int, *,
                     as_of: str, qtrs: int = 4,
                     concept_key: str = "eps_diluted",
                     listing_id: Optional[int] = None,
                     curr_period_end: Optional[str] = None,
                     prev_eps: Optional[float] = None,
                     prev_period_end: Optional[str] = None,
                     curr_eps: Optional[float] = None) -> EpsPair:
    """Walk the hierarchy best-first and report which rung answered."""
    pair = same_filing_pair(conn, entity_id, as_of=as_of, qtrs=qtrs,
                            concept_key=concept_key,
                            curr_period_end=curr_period_end)
    if pair is not None:
        return pair
    if (prev_eps is None or curr_eps is None
            or prev_period_end is None or curr_period_end is None):
        return EpsPair(path=PAIR_REFUSED, prev_on_basis=None, curr=curr_eps,
                       prev_period_end=prev_period_end,
                       curr_period_end=curr_period_end,
                       prev_as_reported=prev_eps,
                       reason=REFUSED_SHARE_BASIS_UNKNOWN,
                       notes=("no same-filing comparative, and no cross-filing "
                              "pair was supplied to harmonise",))
    return harmonise_pair(conn, prev_eps=prev_eps, curr_eps=curr_eps,
                          prev_period_end=prev_period_end,
                          curr_period_end=curr_period_end,
                          listing_id=listing_id, as_of=as_of)


# ==========================================================================
# THE VALUATION SIDE OF DECISION 2
# ==========================================================================

@dataclass(frozen=True)
class NormalisedPE:
    """A P/E taken on one consistent basis, or refused."""

    pe: Optional[float]
    price_raw: Optional[float]
    eps_as_reported: Optional[float]
    eps_on_score_basis: Optional[float]
    basis_factor: Optional[float]
    price_basis: str = PRICE_RAW_AS_TRADED
    reason: Optional[str] = None
    gate: Optional[ActionGate] = None

    def to_record(self) -> dict[str, Any]:
        return {
            "policy_version": PRICE_BASIS_POLICY_VERSION,
            "pe": self.pe,
            "price_raw": self.price_raw,
            "price_basis": self.price_basis,
            "eps_as_reported": self.eps_as_reported,
            "eps_on_score_basis": self.eps_on_score_basis,
            "basis_factor": self.basis_factor,
            "reason": self.reason,
            "gate": None if self.gate is None else self.gate.to_record(),
        }


def normalised_pe(conn: sqlite3.Connection, *, price_raw: Optional[float],
                  price_basis: str, eps_pit: Optional[float],
                  eps_period_end: str, as_of: str,
                  listing_id: Optional[int]) -> NormalisedPE:
    """P/E on the score-date share basis, from a raw as-traded price.

    Refuses an adjusted price outright (decision 2), then carries the
    point-in-time EPS forward through PIT-safe splits in
    (eps_period_end, as_of] so numerator and denominator share one basis.
    """
    assert_price_basis(PRICE_ROLE_VALUATION, price_basis)
    if price_raw is None or eps_pit is None:
        return NormalisedPE(pe=None, price_raw=price_raw,
                            eps_as_reported=eps_pit, eps_on_score_basis=None,
                            basis_factor=None, reason="missing_input")

    gate = pit_safe_split_factor(conn, listing_id, eps_period_end, as_of, as_of)
    if not gate.usable:
        # `no_events_recorded` lands here too, and deliberately: it is
        # ambiguous between "no splits happened" and "splits were never
        # ingested", and `pit_shares` already refuses to round that down.
        return NormalisedPE(pe=None, price_raw=price_raw,
                            eps_as_reported=eps_pit, eps_on_score_basis=None,
                            basis_factor=None, gate=gate,
                            reason=REFUSED_SHARE_BASIS_UNKNOWN)

    factor = float(gate.factor or 1.0)
    eps_on_basis = eps_pit / factor
    if eps_on_basis == 0.0:
        return NormalisedPE(pe=None, price_raw=price_raw,
                            eps_as_reported=eps_pit,
                            eps_on_score_basis=eps_on_basis,
                            basis_factor=factor, gate=gate,
                            reason="zero_denominator")
    return NormalisedPE(pe=price_raw / eps_on_basis, price_raw=price_raw,
                        eps_as_reported=eps_pit,
                        eps_on_score_basis=eps_on_basis,
                        basis_factor=factor, gate=gate)


# ==========================================================================
# THE POLICY AS A RECORD
# ==========================================================================

def policy_record() -> dict[str, Any]:
    """Everything a replay run should store about these two decisions."""
    return {
        "price_basis_policy_version": PRICE_BASIS_POLICY_VERSION,
        "eps_pair_policy_version": EPS_PAIR_POLICY_VERSION,
        "action_gate_version": ACTION_GATE_VERSION,
        "action_gate_semantics": dict(ACTION_GATE_SEMANTICS_V2),
        "price_basis_translation": dict(PRICE_BASIS_TRANSLATION_V1),
        "valuation_price_policy": json.loads(json.dumps(VALUATION_PRICE_POLICY_V1)),
        "price_roles": dict(PRICE_ROLE_REQUIRED_BASIS),
        "price_role_why": dict(PRICE_ROLE_WHY),
        "pair_paths": list(PAIR_PATHS),
        "pair_path_coverage": json.loads(json.dumps(PAIR_PATH_COVERAGE)),
        "apple_split_case": json.loads(json.dumps(APPLE_SPLIT_CASE)),
        "eps_growth_min_positive_base": EPS_GROWTH_MIN_POSITIVE_BASE_V1,
        "eps_growth_min_positive_base_rationale": MIN_POSITIVE_BASE_RATIONALE,
        "classifier_ordering_rule": (
            "NORMALISE FIRST, CLASSIFY SECOND. A split can manufacture a fake "
            "profit_to_loss, fake growth or a giant collapse before any "
            "sign-cross rule sees the values -- Apple's naive cross-filing "
            "pair reads -72.4138% with both figures strictly positive, so it "
            "classifies as both_positive and prints that number."),
        "known_limitation": (
            "pit_corporate_action holds 1,270 splits and 226 spin-offs across "
            "1,903 of 2,576 listings, all Yahoo-sourced and all confidence "
            "'inferred', over a SURVIVOR_ONLY universe. Rung 2 is therefore "
            "only as good as that table, and 673 listings have no action rows "
            "at all -- which reads as no_events_recorded and is REFUSED, not "
            "assumed clean."),
    }


def validate() -> list[str]:
    """Self-check. Returns a list of problems; empty means PASS."""
    problems: list[str] = []

    if set(PRICE_ROLE_REQUIRED_BASIS) != {PRICE_ROLE_VALUATION, PRICE_ROLE_RETURN}:
        problems.append("the two price roles are the whole of decision 2")
    if PRICE_ROLE_REQUIRED_BASIS[PRICE_ROLE_VALUATION] != PRICE_RAW_AS_TRADED:
        problems.append("valuation must require the raw as-traded price")
    if PRICE_ROLE_REQUIRED_BASIS[PRICE_ROLE_RETURN] != PRICE_ACTION_ADJUSTED:
        problems.append("returns must require the action-adjusted price")
    if len(set(PRICE_CONCEPTS)) != 2:
        problems.append("the two price concepts must not collapse to one name")

    for role, wrong in ((PRICE_ROLE_VALUATION, PRICE_ACTION_ADJUSTED),
                        (PRICE_ROLE_RETURN, PRICE_RAW_AS_TRADED)):
        try:
            assert_price_basis(role, wrong)
        except PriceBasisError:
            pass
        else:
            problems.append("role %s accepted %s; the substitution decision 2 "
                            "forbids must raise" % (role, wrong))

    if EPS_GROWTH_MIN_POSITIVE_BASE_V1 != pit_eps.EPS_NEAR_ZERO_ABS:
        problems.append(
            "the growth base threshold and pit_eps.EPS_NEAR_ZERO_ABS disagree "
            "in value (%r vs %r); that is allowed, but it must be a decision "
            "rather than a drift, so update this check when it becomes one"
            % (EPS_GROWTH_MIN_POSITIVE_BASE_V1, pit_eps.EPS_NEAR_ZERO_ABS))
    if "precision" not in MIN_POSITIVE_BASE_RATIONALE.lower():
        problems.append("the threshold's rationale must reference reporting "
                        "precision, not economic significance")

    # The Apple arithmetic, recomputed rather than trusted.
    rung1 = 100.0 * (3.28 / 2.97 - 1.0)
    rung2 = 100.0 * (3.28 / (11.89 / 4.0) - 1.0)
    rung3 = 100.0 * (3.28 / 11.89 - 1.0)
    for name, got, want in (("rung_1_same_filing_pct", rung1,
                             APPLE_SPLIT_CASE["rung_1_same_filing_pct"]),
                            ("rung_2_harmonised_pct", rung2,
                             APPLE_SPLIT_CASE["rung_2_harmonised_pct"]),
                            ("rung_3_naive_cross_filing_pct", rung3,
                             APPLE_SPLIT_CASE["rung_3_naive_cross_filing_pct"])):
        if abs(got - want) > 5e-4:
            problems.append("%s drifted: recomputed %.4f, published %.4f"
                            % (name, got, want))
    if not (rung3 < -50.0 < 0.0 < rung1):
        problems.append("the Apple case must show a fabricated collapse against "
                        "real growth, or it is not making its point")
    if abs((rung1 - rung2) * 100.0
           - APPLE_SPLIT_CASE["rung_1_minus_rung_2_bp"]) > 0.05:
        problems.append("the rung1-rung2 basis-point gap drifted")

    # The two-sided P/E check: raw price with an era-basis EPS is wrong, and
    # normalising the EPS is what makes both sides of a split agree.
    before = 309.51 / 11.89
    same_date_adjusted = (309.51 / 4.0) / 11.89
    after_normalised = 77.00 / (11.89 / 4.0)
    if abs(before / same_date_adjusted - 4.0) > 1e-9:
        problems.append("the one-date two-basis error must be EXACTLY the "
                        "split factor, or the demonstration is not making its "
                        "point")
    if not (abs(before - 26.03) < 0.02 and abs(same_date_adjusted - 6.51) < 0.02):
        problems.append("the Apple P/E measurement drifted")
    if abs(after_normalised - before) / before > 0.02:
        problems.append("normalising EPS to the score-date basis must make the "
                        "two sides of the split agree to within the price move")

    # The refusal must be UNKNOWN, never False.
    refused = EpsPair(path=PAIR_REFUSED, prev_on_basis=None, curr=1.0,
                      reason=REFUSED_SHARE_BASIS_UNKNOWN)
    if refused.classifiable:
        problems.append("a refused pair must not be classifiable")
    if refused.same_share_basis is not None:
        problems.append("a refused pair must report UNKNOWN, never False")

    if PAIR_PATHS[0] != PAIR_SAME_FILING:
        problems.append("the hierarchy must prefer the same-filing comparative")
    if PAIR_PATHS[-1] != PAIR_REFUSED:
        problems.append("refusal must be the last rung, not the first answer")

    if pit_shares.PRICE_BASIS_RAW not in (PRICE_RAW_AS_TRADED,
                                          "raw_as_printed_unadjusted"):
        problems.append("pit_shares' raw-price spelling moved; reconcile rather "
                        "than adding a third name")

    # GAP 7 (R20): every admitted spelling translates to the valuation basis,
    # and the adjusted price has NO way in.
    if set(PRICE_BASIS_TRANSLATION_V1.values()) != {PRICE_RAW_AS_TRADED}:
        problems.append("PRICE_BASIS_TRANSLATION_V1 may only ever land on the "
                        "valuation role's required basis")
    if pit_shares.PRICE_BASIS_RAW not in PRICE_BASIS_TRANSLATION_V1 or \
            "price_adjusted" not in PRICE_BASIS_TRANSLATION_V1:
        problems.append("the translation must admit pit_shares' spelling and the "
                        "spec vocabulary's 'price_adjusted'")
    if PRICE_ACTION_ADJUSTED in PRICE_BASIS_TRANSLATION_V1:
        problems.append("an action-adjusted price must have NO translation to a "
                        "valuation basis")
    try:
        translate_price_basis(PRICE_ACTION_ADJUSTED)
    except PriceBasisError:
        pass
    else:
        problems.append("translate_price_basis must REFUSE the adjusted price")
    if assert_price_basis(PRICE_ROLE_VALUATION,
                          translate_price_basis(pit_shares.PRICE_BASIS_RAW)) \
            != PRICE_RAW_AS_TRADED:
        problems.append("a translated raw record must pass the valuation role")

    # R19: the valuation price policy is what the engine's one resolver reads.
    if (VALUATION_PRICE_POLICY_V1["roll_back_days"] != 10
            or VALUATION_PRICE_POLICY_V1["require_traded"] is not True
            or VALUATION_PRICE_POLICY_V1["basis"] != PRICE_RAW_AS_TRADED
            or not VALUATION_PRICE_POLICY_V1["same_listing"]):
        problems.append("VALUATION_PRICE_POLICY_V1 must read: 10 calendar days back, "
                        "traded, raw basis, same listing (owner ruling R19)")

    # R18: gate v2 -- the version moved with the semantics, and the semantics
    # body names both coverage states.
    if not ACTION_GATE_VERSION.endswith("_v2"):
        problems.append("the action gate's version must move with its semantics")
    if (ACTION_GATE_SEMANTICS_V2["gate_version"] != ACTION_GATE_VERSION
            or "coverage_established" not in ACTION_GATE_SEMANTICS_V2
            or "coverage_unknown" not in ACTION_GATE_SEMANTICS_V2):
        problems.append("ACTION_GATE_SEMANTICS_V2 must name the version and both "
                        "coverage states")
    if ActionGate(factor=1.0, status=GATE_OK, as_of="2020-01-01",
                  window_after="2019-01-01", window_through="2020-01-01",
                  coverage=COVERAGE_ESTABLISHED).usable is not True:
        problems.append("an established-coverage identity gate must be usable")
    if ActionGate(factor=None, status=GATE_NO_EVENTS, as_of="2020-01-01",
                  window_after="2019-01-01", window_through="2020-01-01",
                  coverage=COVERAGE_UNKNOWN).usable:
        problems.append("an unknown-coverage empty window must stay unusable")
    return problems


def main() -> int:
    problems = validate()
    print("pit_price_basis -- %s / %s" % (PRICE_BASIS_POLICY_VERSION,
                                          EPS_PAIR_POLICY_VERSION))
    print()
    print("DECISION 2 -- two price concepts, never interchangeable")
    for role in (PRICE_ROLE_VALUATION, PRICE_ROLE_RETURN):
        print("   %-16s -> %s" % (role, PRICE_ROLE_REQUIRED_BASIS[role]))
    print()
    print("   (a) ONE DATE, TWO PRICE BASES -- 2020-01-31:")
    print("       as traded       309.51     / 11.89       = %6.2f"
          % (309.51 / 11.89,))
    print("       back-adjusted   309.51 / 4 / 11.89       = %6.2f   "
          "<- same day, %.1fx apart"
          % ((309.51 / 4.0) / 11.89, (309.51 / 11.89) / ((309.51 / 4.0) / 11.89)))
    print()
    print("   (b) TWO DATES EITHER SIDE OF THE SPLIT, both raw:")
    print("       2020-01-31       309.51 / 11.89          = %6.2f"
          % (309.51 / 11.89,))
    print("       post-split        77.00 / 11.89          = %6.2f   "
          "<- EPS left in its own era" % (77.00 / 11.89,))
    print("       post-split        77.00 / (11.89 / 4)    = %6.2f   "
          "<- EPS carried to the score-date basis"
          % (77.00 / (11.89 / 4.0),))
    print()
    print("DECISION 3 -- the EPS pair hierarchy")
    for i, path in enumerate(PAIR_PATHS, 1):
        print("   %d. %s" % (i, path))
    print()
    print("   Apple, across the same split:")
    print("     rung 1  same-filing comparative   %+9.4f%%"
          % APPLE_SPLIT_CASE["rung_1_same_filing_pct"])
    print("     rung 2  cross-filing, harmonised  %+9.4f%%"
          % APPLE_SPLIT_CASE["rung_2_harmonised_pct"])
    print("     rung 3  naive cross-filing pair   %+9.4f%%   FABRICATED"
          % APPLE_SPLIT_CASE["rung_3_naive_cross_filing_pct"])
    print()
    print("   rung 1 availability: %.2f%% of annual filings carry a comparative"
          % PAIR_PATH_COVERAGE["annual_filings_with_comparative_pct"])
    print("   EPS_GROWTH_MIN_POSITIVE_BASE_V1 = %.2f"
          % EPS_GROWTH_MIN_POSITIVE_BASE_V1)
    print()
    print("%d problems / %s" % (len(problems), "FAIL" if problems else "PASS"))
    for p in problems:
        print("   - %s" % p)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
