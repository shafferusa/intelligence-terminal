"""IS THE DEBT-RUNG COVERAGE RECOVERY REAL, OR COSMETIC? Read-only, stdlib only.

`concept_ladder_v2` took `total_debt` from 15.9% / 17.8% / 14.5% of base to
37.9% / 42.3% / 37.7% -- 2.4x. It did it by admitting rungs that are NOT total
debt, and the price was stated when the ladder was written rather than
discovered later:

    94.4% / 94.5% / 93.4% of the resolved cross-section sits on a `lower_bound`
    rung, and only 2.1% / 2.3% / 2.5% of base lands on a rung that is
    genuinely total debt.

That matters because debt is ADDITIVE in enterprise value --

    EV = MarketCap + Debt - Cash

-- so an understated debt figure makes a company look CHEAPER than it is, in
the same direction for every filer on that rung. The staleness fix
(`fact_kind_staleness_v2`) has just made debt the binding leaf of the EV/EBITDA
conjunction again (EV/EBITDA P(N>=3) 34.2% -> 46.0%), so whether that recovery
is real is now the question in front of the valuation factor.

==========================================================================
THE TEST: RANK AGREEMENT ACROSS DEBT PROVENANCE
==========================================================================

A coverage number cannot answer the question, because filling a cell with a
wrong number fills the cell. The evidence has to be about ORDERING, because
ordering is what the model actually consumes: `ev_ebitda` is scored as a peer
percentile, so a debt definition that shifts every EV by the same proportion
costs nothing, and one that shifts EV by an amount correlated with size,
leverage or sector reorders the cross-section and changes the score.

So, for each rung, on the subset where BOTH that rung AND the strongest
definition resolve for the same issuer on the same date:

    EV_ref  = MarketCap + debt(reference rung) - Cash
    EV_rung = MarketCap + debt(this rung)      - Cash

    ratio_ref  = EV_ref  / EBITDA          ratio_rung = EV_rung / EBITDA

and then the two RANKINGS are compared -- Spearman on average ranks, exact
pairwise ordering agreement, near ordering agreement at a stated tolerance,
and the churn of the cheapest decile, which is the part a portfolio would feel.

THE REFERENCE is `SUM(LongTermDebtNoncurrent+LongTermDebtCurrent+
ShortTermBorrowings)`: the only rung in v2 that is total debt AND is attested
often enough to rank against. `DebtLongtermAndShorttermCombinedAmount` is also
labelled exact and is NOT the reference -- 98 issuers carry it in the whole
store and `pit_policy` says its scope could not be verified from data.

THE DECISION RULE IS STATED BEFORE THE NUMBERS, in section (2), as the owner
gave it: "If a lower rung adds enormous coverage while preserving nearly the
same ordering, that's useful. If it materially reshuffles valuation, don't
promote it simply because it fills cells." The thresholds that turn that
sentence into a verdict are module constants, pre-registered, and the test file
asserts the verdict function against them on constructed cases.

==========================================================================
SAMPLE SCOPE -- two different scopes, and they are labelled separately
==========================================================================

COVERAGE, PROVENANCE RATIOS AND THE SECTOR BREAKDOWN are
FULL_REPORTING_UNIVERSE. They read `pit_fact`, `pit_entity_sic` and
`pit_identity.peer_universe_as_of`, all CIK-keyed, and include issuers that are
long dead. Nothing in that half touches a price.

THE RANK-AGREEMENT TEST NEEDS A MARKET CAP, and a market cap needs a price and
a defensible share count, so that half is SURVIVOR_PRICED_DIAGNOSTIC: it is
drawn from `pit_listing`'s 2,574 lines, every one of them alive in 2026. That
label is NOT a footnote -- but it is also not the same objection as the return
objection, and the difference is the reason the two are named apart:

  * the comparison is PAIRED WITHIN AN ISSUER. The same company, the same
    market cap, the same cash, the same EBITDA; only the debt term differs. So
    survivorship decides WHICH issuers are measured, not which direction the
    measurement points.
  * a RETURN measured on survivors is a different object altogether: the median
    survivor in this store underperforms the total market by -7.45% at 12M, so
    a return is a statement about a population that did not exist.

Hence rule 5 of this module, enforced in code and not merely written down:
`survivor_return_diagnostic()` is computed, reported, labelled
SURVIVOR_ONLY_DIAGNOSTIC, and MAY NOT PROMOTE A RUNG. `verdict()` never reads
it. If the ranking and the returns disagree, the ranking wins.

==========================================================================
WHAT THIS MODULE DOES NOT DO
==========================================================================

It writes NOTHING to the store. It opens `mode=ro`, holds no snapshot (every
statement is a short indexed SELECT or one streaming scan), fits no model,
freezes no baseline, and leaves `pit_feature`, `pit_score` and
`pit_replay_run` at zero rows. It does not run `PRAGMA wal_checkpoint`: a
read-only connection cannot, and `pit_rawprice.wal_checkpoint` -- which READS
the (busy, log_pages, checkpointed) triple rather than firing and hoping -- is
the one place that should. WAL size is SAMPLED throughout instead, and a
quantity nobody sampled is reported as UNKNOWN, never as 0.

    python pit_debt_quality.py [--dates census|annual|quarterly|grid]
                               [--rank-dates census|annual|quarterly|grid]
                               [--out DIR] [--staleness v1|v2] [--no-validate]
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_derive
import pit_fact_kind
import pit_factor_spec as FS
import pit_identity
import pit_peer_coherence as PC
import pit_policy
import pit_rawprice
import pit_store
import statlib

UNKNOWN = PC.UNKNOWN

#: Two DIFFERENT scopes, named apart on purpose. See the module docstring.
SCOPE_FULL = "FULL_REPORTING_UNIVERSE"
SCOPE_PRICED = "SURVIVOR_PRICED_DIAGNOSTIC"
SCOPE_RETURNS = pit_store.SAMPLE_SURVIVOR_ONLY

#: Reused rather than re-implemented: a second copy of the disk floor, the
#: read-only connection or the resource sampler would be a second answer to
#: "what did this cost".
MIN_FREE_BYTES = PC.MIN_FREE_BYTES
BUSY_TIMEOUT_MS = PC.BUSY_TIMEOUT_MS

#: The ladder under test. v2 is where the debt rungs are labelled at all.
LADDER = pit_store.LADDER_VERSION_V2
DEBT_CONCEPT = "total_debt"
CASH_CONCEPT = "cash"
ANNUAL_QTRS = FS.ANNUAL_QTRS
INSTANT_QTRS = 0

#: The forward-return horizon for the SURVIVOR-ONLY diagnostic.
RETURN_HORIZON = "12M"

#: `pit_derive`'s three census dates, so the coverage half of this module is
#: comparable with the numbers the ladder was written against.
CENSUS_DATES = pit_derive.MEASURED_DATES


# ==========================================================================
# (1) THE RUNGS -- read from the frozen ladder, never restated here
# ==========================================================================

@dataclass(frozen=True)
class RungSpec:
    """One debt rung, flattened out of `pit_policy` for the measurement.

    `index` is LADDER ORDER, which is also order of economic completeness: a
    lower index is a more complete quantity. `is_reference` marks the rung
    every other one is measured against.
    """

    index: int
    key: str
    tags: tuple[str, ...]
    combine: str
    quantity: str
    bound: str
    note: str

    @property
    def is_reference(self) -> bool:
        return self.key == REFERENCE_RUNG_KEY

    @property
    def is_exact(self) -> bool:
        return self.bound == pit_policy.BOUND_EXACT

    def as_dict(self) -> dict[str, Any]:
        return {"index": self.index, "key": self.key, "tags": list(self.tags),
                "combine": self.combine, "quantity": self.quantity,
                "bound": self.bound, "is_reference": self.is_reference}


def debt_rungs(version: str = LADDER) -> tuple[RungSpec, ...]:
    """Every `total_debt` rung of one ladder version, in ladder order."""
    return tuple(
        RungSpec(index=i, key=r.key, tags=tuple(r.tags), combine=r.combine,
                 quantity=r.quantity, bound=r.bound, note=r.note)
        for i, r in enumerate(pit_policy.ladder_for(DEBT_CONCEPT, version).rungs))


def _reference_key(version: str = LADDER) -> str:
    """The rung every other is ranked against: the three-way sum.

    Chosen STRUCTURALLY rather than by name -- the first rung whose bound is
    `exact` and whose combine is `sum`, which is the only rung in v2 that is
    total debt and is attested on more than 98 issuers. Deriving it means a
    reordered ladder cannot silently move the reference onto a lower bound.
    """
    for rung in pit_policy.ladder_for(DEBT_CONCEPT, version).rungs:
        if rung.bound == pit_policy.BOUND_EXACT and rung.combine == pit_policy.COMBINE_SUM:
            return rung.key
    raise ValueError(f"{version} has no exact composite total_debt rung to rank against")


REFERENCE_RUNG_KEY = _reference_key()
RUNGS: tuple[RungSpec, ...] = debt_rungs()
REFERENCE_INDEX = next(r.index for r in RUNGS if r.key == REFERENCE_RUNG_KEY)


# ==========================================================================
# (2) THE DECISION RULE -- STATED BEFORE ANY NUMBER IS SEEN
#
# The owner's sentence:
#
#   "If a lower rung adds enormous coverage while preserving nearly the same
#    ordering, that's useful. If it materially reshuffles valuation, don't
#    promote it simply because it fills cells."
#
# Everything below turns that into arithmetic. These constants are registered
# here, in the source, BEFORE the measurement was run; `test_pit_debt_quality`
# exercises `verdict` against constructed cases so a later edit that loosens a
# threshold to admit a rung has to be a visible edit.
# ==========================================================================

#: Two companies whose reference EV/EBITDA differ by less than this, relative
#: to their mean, are TOO CLOSE TO CALL: a reversal between them is not a
#: reshuffle of the valuation, it is noise in the third significant figure.
#: 5% of an EV/EBITDA multiple -- e.g. 10.0x against 10.5x. Stated here so the
#: "near" column can never be read as an unqualified agreement rate.
NEAR_TOLERANCE = 0.05

#: The decile a portfolio would actually act on.
CHEAP_FRACTION = 0.10

#: Below this many paired issuers at a date, that date contributes to the
#: pooled sample and gets NO date-level verdict: a rank correlation on twelve
#: names is a statement about twelve names.
MIN_RANK_SAMPLE = 30

#: The smallest cross-section on which a per-date Spearman rho is reported at
#: all. A rank correlation on four names is a statement about four names.
#: Pairwise agreement and decile churn have no such floor because they are
#: pooled as COUNTS: a date contributing three pairs contributes three pairs.
MIN_SPEARMAN_N = 10

#: Below this many pooled paired observations, the rung's verdict is
#: UNDETERMINED and its Q is UNKNOWN. Not 1.0, not 0.5, not "probably fine".
MIN_POOLED_SAMPLE = 200

#: AND below this many WITHIN-DATE PAIRS. Added after the first full run, and
#: recorded as an addition rather than quietly folded in.
#:
#: The first run exposed a SPECIFICATION ERROR, not an inconvenient result:
#: `MIN_POOLED_SAMPLE` counts issuer-dates, and the statistics it gates are
#: computed over PAIRS. `LongTermDebt` cleared 200 issuer-dates (224) and
#: produced 149 pairs, because its 224 observations are spread one and two at a
#: time across 140 dates -- and on a date with two comparable issuers the
#: "cheapest decile" is one name out of two, which is a coin flip wearing the
#: name of a statistic. A gate on the wrong denominator is not a gate.
#:
#: The direction matters and is the reason this is admissible after the fact:
#: adding a second floor can only ever move a rung from a verdict to
#: UNDETERMINED. It cannot promote anything, and it did not -- it moved
#: `LongTermDebt` from PROMOTE to UNDETERMINED, which is the conservative way
#: round. Both verdicts are reported in `threshold_history`.
MIN_POOLED_PAIRS = 1000

#: "Enormous coverage", in points of the base universe. A rung that adds less
#: than this is not worth an argument either way.
PROMOTE_MIN_COVERAGE_POINTS = 1.0

#: "Nearly the same ordering".
PROMOTE_MIN_NEAR_AGREEMENT = 0.95
QUALIFIED_MIN_NEAR_AGREEMENT = 0.90

#: "Materially reshuffles valuation" -- the cheapest decile is the part of the
#: ranking a decision is taken on, so its churn is the material test.
PROMOTE_MAX_DECILE_CHURN = 0.10
REFUSE_MAX_DECILE_CHURN = 0.25

VERDICT_REFERENCE = "REFERENCE"
VERDICT_PROMOTE = "PROMOTE"
VERDICT_PROMOTE_WITH_Q = "PROMOTE_WITH_QUALITY_FACTOR"
VERDICT_DO_NOT_PROMOTE = "DO_NOT_PROMOTE"
VERDICT_UNDETERMINED = "UNDETERMINED"

DECISION_RULE = {
    "stated_by": "the owner, before the measurement was run",
    "sentence": ("If a lower rung adds enormous coverage while preserving "
                 "nearly the same ordering, that's useful. If it materially "
                 "reshuffles valuation, don't promote it simply because it "
                 "fills cells."),
    "near_tolerance": NEAR_TOLERANCE,
    "near_tolerance_meaning": (
        "a pair of issuers whose REFERENCE EV/EBITDA differ by less than 5% of "
        "their mean is counted as agreeing whichever way the rung orders them; "
        "exact agreement counts no such indulgence"),
    "cheap_fraction": CHEAP_FRACTION,
    "thresholds": {
        "min_rank_sample_per_date_verdict": MIN_RANK_SAMPLE,
        "min_cross_section_for_a_per_date_spearman": MIN_SPEARMAN_N,
        "min_pooled_issuer_dates": MIN_POOLED_SAMPLE,
        "min_pooled_within_date_pairs": MIN_POOLED_PAIRS,
        "promote_min_coverage_points_of_base": PROMOTE_MIN_COVERAGE_POINTS,
        "promote_min_near_agreement": PROMOTE_MIN_NEAR_AGREEMENT,
        "qualified_min_near_agreement": QUALIFIED_MIN_NEAR_AGREEMENT,
        "promote_max_decile_churn": PROMOTE_MAX_DECILE_CHURN,
        "refuse_max_decile_churn": REFUSE_MAX_DECILE_CHURN,
    },
    "threshold_history": [
        {"when": "before the first run", "change": (
            "every threshold in this block was registered in the source before "
            "the measurement was executed")},
        {"when": "after the first full run, 2026-09-21", "change": (
            "MIN_POOLED_PAIRS added. MIN_POOLED_SAMPLE gates issuer-dates while "
            "the agreement rate and the decile churn are computed over pairs; "
            "LongTermDebt cleared 224 issuer-dates and produced 149 pairs, 1-2 "
            "per date, where a cheap decile is one name in two. A second floor "
            "can only move a rung TO UNDETERMINED, never to a promotion."),
         "effect": ("LongTermDebt: PROMOTE (+6.7 points of base, 98.66% near "
                    "agreement, 5.95% churn, 149 pairs) -> UNDETERMINED"),
         "nothing_else_moved": True},
    ],
    "returns_may_not_promote": (
        "survivor_return_diagnostic() is never read by verdict(). It is a "
        "SURVIVOR_ONLY_DIAGNOSTIC measured on 2,574 listings all alive in 2026, "
        "whose median member underperforms the total market by -7.45% at 12M. "
        "If the ranking result and the return result disagree, the ranking "
        "result wins, and the disagreement is reported as a disagreement."),
}


def verdict(rung: RungSpec, coverage_points: Optional[float],
            near_agreement: Optional[float], decile_churn: Optional[float],
            n_pooled: int, n_pairs: int) -> dict[str, Any]:
    """Apply the pre-registered rule to one rung. Reads no return data.

    Returns {'verdict', 'why', 'inputs'}. UNDETERMINED is a real answer and is
    produced whenever the pooled sample is too thin -- a rung nobody could
    measure is not a rung that passed.
    """
    inputs = {"coverage_points_of_base": coverage_points,
              "near_ordering_agreement": near_agreement,
              "cheap_decile_churn": decile_churn,
              "n_pooled_issuer_dates": n_pooled,
              "n_within_date_pairs": n_pairs}
    if rung.is_reference:
        return {"verdict": VERDICT_REFERENCE, "inputs": inputs,
                "why": ("the definition every other rung is measured against; "
                        "it ranks against itself by construction")}
    if n_pooled < MIN_POOLED_SAMPLE or near_agreement is None or decile_churn is None:
        return {"verdict": VERDICT_UNDETERMINED, "inputs": inputs,
                "why": (f"only {n_pooled} paired issuer-dates carry both this "
                        f"rung and the reference with a market cap, EBITDA and "
                        f"cash; {MIN_POOLED_SAMPLE} is the floor for a verdict. "
                        "Reported as undetermined, not as harmless.")}
    if n_pairs < MIN_POOLED_PAIRS:
        return {"verdict": VERDICT_UNDETERMINED, "inputs": inputs,
                "why": (f"{n_pooled} issuer-dates yielded only {n_pairs:,} "
                        f"WITHIN-DATE pairs ({MIN_POOLED_PAIRS:,} is the floor), "
                        "so this rung meets the reference one or two names at a "
                        "time and its agreement rate and decile churn have no "
                        "cross-section behind them. Undetermined, not harmless.")}
    if decile_churn > REFUSE_MAX_DECILE_CHURN:
        return {"verdict": VERDICT_DO_NOT_PROMOTE, "inputs": inputs,
                "why": (f"the cheapest decile churns {decile_churn:.1%} against "
                        f"the reference ranking, over the "
                        f"{REFUSE_MAX_DECILE_CHURN:.0%} ceiling: this rung "
                        "materially reshuffles the valuation and may not be "
                        "promoted on coverage")}
    if near_agreement < QUALIFIED_MIN_NEAR_AGREEMENT:
        return {"verdict": VERDICT_DO_NOT_PROMOTE, "inputs": inputs,
                "why": (f"near ordering agreement {near_agreement:.1%} is below "
                        f"{QUALIFIED_MIN_NEAR_AGREEMENT:.0%} even after the "
                        f"{NEAR_TOLERANCE:.0%} too-close-to-call indulgence")}
    if (near_agreement >= PROMOTE_MIN_NEAR_AGREEMENT
            and decile_churn <= PROMOTE_MAX_DECILE_CHURN
            and (coverage_points or 0.0) >= PROMOTE_MIN_COVERAGE_POINTS):
        return {"verdict": VERDICT_PROMOTE, "inputs": inputs,
                "why": (f"+{coverage_points:.1f} points of base while preserving "
                        f"{near_agreement:.1%} of pairwise ordering and churning "
                        f"{decile_churn:.1%} of the cheapest decile")}
    return {"verdict": VERDICT_PROMOTE_WITH_Q, "inputs": inputs,
            "why": (f"ordering survives ({near_agreement:.1%} near agreement, "
                    f"{decile_churn:.1%} decile churn) but not cleanly enough to "
                    "be pooled with the exact rung as an equal: admissible only "
                    "with a data-quality factor that says which rung it is")}


# ==========================================================================
# (3) Point-in-time selection of a rung's value
# ==========================================================================

def _ord(day: Any) -> int:
    return _dt.date.fromisoformat(str(day)[:10]).toordinal()


def _add_months_ord(day_ord: int, months: int) -> int:
    """Calendar month addition on an ordinal, clamping into the target month."""
    end = _dt.date.fromordinal(day_ord)
    total = end.month - 1 + int(months)
    year, month = end.year + total // 12, total % 12 + 1
    last = 31 if month == 12 else (
        _dt.date(year, month + 1, 1) - _dt.timedelta(days=1)).day
    return _dt.date(year, month, min(end.day, last)).toordinal()


def age_months(concept: str, qtrs: int, staleness: str) -> int:
    """The freshness budget for one concept under v1 or v2 staleness.

    v1 is `pit_policy.max_age_months` -- the CADENCE rule, six months for an
    instantaneous fact. v2 is `pit_fact_kind.max_age_months_v2`, which calls
    `cash`, `total_debt` and `total_assets` STATE facts and gives them the
    24-month sanity ceiling instead, because a balance-sheet level persists
    until it is superseded and does not expire on a filing calendar.

    BOTH are computed by this module and BOTH are reported. v1 is what the
    published 15.9% / 37.9% coverage numbers were measured under, so it is the
    reproduction check; v2 is the locked correction and is the default, and it
    is why debt is the binding leaf again.
    """
    if staleness == "v1":
        return pit_policy.max_age_months(qtrs, concept)
    months = pit_fact_kind.max_age_months_v2(concept, qtrs)
    if months is None:                       # a MARKET fact -- not reachable here
        return pit_policy.max_age_months(qtrs, concept)
    return int(months)


def _pick(entries: Sequence[tuple[int, float]], day_ord: int) -> Optional[float]:
    """The latest vintage of one tag at one period that had LANDED by `day_ord`.

    A restatement supersedes its original the day it becomes available and not
    a day sooner; `entries` is sorted ascending by `available_date`, so the
    last entry at or before the as-of date is the value the system knew.
    """
    value: Optional[float] = None
    for avail_ord, val in entries:
        if avail_ord > day_ord:
            break
        value = val
    return value


def resolve_rungs(periods: Sequence[tuple[int, dict[str, list[tuple[int, float]]]]],
                  day_ord: int, rungs: Sequence[RungSpec],
                  months: int) -> dict[int, tuple[float, int]]:
    """Every rung's value for ONE issuer on ONE date. {rung index: (value, period)}.

    `periods` is that issuer's (period_end_ord, {tag: [(available_ord, val)]}),
    sorted DESCENDING. Each rung independently takes the NEWEST period that is

      * already ended     (period_end <= as_of),
      * not stale         (period_end + months >= as_of),
      * fully landed      (every component tag has a vintage available by as_of),

    and a composite rung takes its components from ONE period end, which is the
    whole reason this cannot be done tag by tag: long-term and short-term debt
    added across two balance-sheet dates is not a balance sheet.

    The descending walk BREAKS at the first stale period rather than
    continuing, which is correct because `period_end + months` is monotone in
    `period_end`: once one period is too old, every older one is too.
    """
    out: dict[int, tuple[float, int]] = {}
    for pe_ord, tagmap in periods:
        if pe_ord > day_ord:
            continue                        # the period had not ended yet
        if _add_months_ord(pe_ord, months) < day_ord:
            break                           # stale, and so is everything older
        values: dict[str, float] = {}
        for tag, entries in tagmap.items():
            val = _pick(entries, day_ord)
            if val is not None:
                values[tag] = val
        if not values:
            continue
        for rung in rungs:
            if rung.index in out:
                continue
            if all(tag in values for tag in rung.tags):
                out[rung.index] = (sum(values[t] for t in rung.tags), pe_ord)
        if len(out) == len(rungs):
            break
    return out


def resolve_ladder_value(periods: Sequence[tuple[int, dict[str, list[tuple[int, float]]]]],
                         day_ord: int, ranked_tags: Sequence[tuple[str, int]],
                         months: int) -> Optional[tuple[float, int]]:
    """A single-ladder concept (cash) at one date: (value, period_end_ord).

    Newest non-stale period that yields anything; within it, the lowest-ranked
    tag wins, which is `pit_policy.resolve`'s own order.
    """
    for pe_ord, tagmap in periods:
        if pe_ord > day_ord:
            continue
        if _add_months_ord(pe_ord, months) < day_ord:
            break
        best: Optional[tuple[int, float]] = None
        for tag, rank in ranked_tags:
            entries = tagmap.get(tag)
            if not entries:
                continue
            val = _pick(entries, day_ord)
            if val is None:
                continue
            if best is None or rank < best[0]:
                best = (rank, val)
        if best is not None:
            return best[1], pe_ord
    return None


def resolve_ebitda(periods: Sequence[tuple[int, dict[str, list[tuple[int, float]]]]],
                   day_ord: int, oi_ranked: Sequence[tuple[str, int]],
                   da_ranked: Sequence[tuple[str, int]],
                   months: int) -> Optional[tuple[float, int]]:
    """EBITDA = operating income + D&A at a MATCHED period end.

    `pit_policy.ebitda_assembly_spec`'s period rule, applied: both components
    from one (period_end, qtrs). A quarterly operating income added to an
    annual D&A is not EBITDA, and neither is this year's operating income added
    to last year's depreciation.
    """
    for pe_ord, tagmap in periods:
        if pe_ord > day_ord:
            continue
        if _add_months_ord(pe_ord, months) < day_ord:
            break
        oi = _best_ranked(tagmap, oi_ranked, day_ord)
        da = _best_ranked(tagmap, da_ranked, day_ord)
        if oi is not None and da is not None:
            return oi + da, pe_ord
    return None


def _best_ranked(tagmap: Mapping[str, list[tuple[int, float]]],
                 ranked: Sequence[tuple[str, int]], day_ord: int) -> Optional[float]:
    best: Optional[tuple[int, float]] = None
    for tag, rank in ranked:
        entries = tagmap.get(tag)
        if not entries:
            continue
        val = _pick(entries, day_ord)
        if val is None:
            continue
        if best is None or rank < best[0]:
            best = (rank, val)
    return best[1] if best else None


def ranked_tags(concept: str, version: str = LADDER) -> tuple[tuple[str, int], ...]:
    """(tag, ladder rank) for one concept, lowest rank first."""
    out: dict[str, int] = {}
    for rank, rung in enumerate(pit_policy.ladder_for(concept, version).rungs):
        for tag in rung.tags:
            out.setdefault(tag, rank)
    return tuple(sorted(out.items(), key=lambda kv: kv[1]))


# ==========================================================================
# (4) Fact indexes. One streaming scan for the census, targeted reads for the
#     candidate issuers -- the store is 9.0 GiB and the volume is at 96%.
# ==========================================================================

def _period_index(rows: Iterable[tuple[int, str, str, str, float]],
                  ) -> dict[int, dict[int, dict[str, list[tuple[int, float]]]]]:
    """(entity, period_end) -> {tag: [(available_ord, value)]} from raw rows."""
    index: dict[int, dict[int, dict[str, list[tuple[int, float]]]]] = {}
    for entity_id, tag, period_end, available, val in rows:
        try:
            pe_ord, avail_ord = _ord(period_end), _ord(available)
        except (TypeError, ValueError):
            continue
        by_period = index.setdefault(int(entity_id), {})
        by_period.setdefault(pe_ord, {}).setdefault(str(tag), []).append(
            (avail_ord, float(val)))
    return index


def _sorted_periods(index: Mapping[int, Mapping[int, dict[str, list[tuple[int, float]]]]],
                    ) -> dict[int, list[tuple[int, dict[str, list[tuple[int, float]]]]]]:
    """Periods newest-first, vintages oldest-first. The shape `resolve_*` want."""
    out: dict[int, list[tuple[int, dict[str, list[tuple[int, float]]]]]] = {}
    for entity_id, by_period in index.items():
        for tagmap in by_period.values():
            for entries in tagmap.values():
                entries.sort()
        out[int(entity_id)] = sorted(by_period.items(), key=lambda kv: -kv[0])
    return out


def debt_index(conn: sqlite3.Connection, progress: bool = True) -> dict[str, Any]:
    """ONE scan of `pit_fact` for every tag any `total_debt` rung names.

    Consolidated only (`segments = '' AND coreg = ''`) and `unit = 'USD'`.
    The unit filter is not decoration: entity 2544 is a JPY-reporting 20-F
    filer, and a JPY debt level added to a USD market cap is a 150x error with
    clean provenance.
    """
    tags = sorted({t for rung in RUNGS for t in rung.tags})
    marks = ", ".join("?" * len(tags))
    started = time.time()
    rows = conn.execute(
        f"""SELECT entity_id, tag, period_end, available_date, val
              FROM pit_fact
             WHERE qtrs = ? AND segments = '' AND coreg = '' AND unit = 'USD'
               AND tag IN ({marks})""", [INSTANT_QTRS] + tags).fetchall()
    index = _period_index(rows)
    if progress:
        print(f"    debt: {len(rows):,} rows, {len(index):,} issuers, "
              f"{time.time() - started:.0f}s", flush=True)
    return {"periods": _sorted_periods(index), "n_rows": len(rows),
            "tags": tags, "seconds": round(time.time() - started, 1)}


def assets_mask_index(conn: sqlite3.Connection, grid: Sequence[str],
                      staleness: str, progress: bool = True) -> dict[int, int]:
    """entity -> bitmask over `grid` of "a usable non-stale Assets fact exists".

    A BITMASK and not a value index, because base membership is the only thing
    `total_assets` is needed for here and one integer per issuer is three
    orders of magnitude smaller than the rows behind it. Bit i is set when some
    Assets fact has period_end <= grid[i], available_date <= grid[i] and
    period_end + max_age >= grid[i].
    """
    import bisect
    started = time.time()
    ords = [_ord(d) for d in grid]
    months = age_months("total_assets", INSTANT_QTRS, staleness)
    masks: dict[int, int] = {}
    n_rows = 0
    deadline_cache: dict[int, int] = {}
    for entity_id, period_end, available in conn.execute(
            """SELECT entity_id, period_end, available_date
                 FROM pit_fact
                WHERE qtrs = ? AND segments = '' AND coreg = '' AND unit = 'USD'
                  AND tag = 'Assets'""", (INSTANT_QTRS,)):
        n_rows += 1
        try:
            pe_ord, avail_ord = _ord(period_end), _ord(available)
        except (TypeError, ValueError):
            continue
        low = pe_ord if pe_ord > avail_ord else avail_ord
        high = deadline_cache.get(pe_ord)
        if high is None:
            high = deadline_cache[pe_ord] = _add_months_ord(pe_ord, months)
        if high < ords[0] or low > ords[-1]:
            continue
        # The usable grid positions are a CONTIGUOUS range because the grid is
        # sorted, so the mask is one shifted run of ones rather than 165 tests.
        lo_i = bisect.bisect_left(ords, low)
        hi_i = bisect.bisect_right(ords, high) - 1
        if hi_i < lo_i:
            continue
        mask = ((1 << (hi_i - lo_i + 1)) - 1) << lo_i
        masks[int(entity_id)] = masks.get(int(entity_id), 0) | mask
    if progress:
        print(f"    assets: {n_rows:,} rows, {len(masks):,} issuers with a "
              f"usable window, {time.time() - started:.0f}s", flush=True)
    return masks


def targeted_index(conn: sqlite3.Connection, entity_ids: Sequence[int],
                   tags: Sequence[str], qtrs: int, chunk: int = 400,
                   ) -> dict[int, list[tuple[int, dict[str, list[tuple[int, float]]]]]]:
    """The same period index, but only for the issuers that can be compared.

    `idx_pit_fact_select` is (entity_id, tag, period_end, available_date), so an
    `entity_id IN (...) AND tag IN (...)` read is an index seek per pair rather
    than a scan. Cash alone is 1.59 million rows in this store and the
    comparison needs a few hundred issuers of it.
    """
    ids = sorted({int(e) for e in entity_ids})
    tag_list = sorted(set(tags))
    marks_t = ", ".join("?" * len(tag_list))
    merged: dict[int, dict[int, dict[str, list[tuple[int, float]]]]] = {}
    for start in range(0, len(ids), chunk):
        block = ids[start:start + chunk]
        marks_e = ", ".join("?" * len(block))
        rows = conn.execute(
            f"""SELECT entity_id, tag, period_end, available_date, val
                  FROM pit_fact
                 WHERE entity_id IN ({marks_e}) AND tag IN ({marks_t})
                   AND qtrs = ? AND segments = '' AND coreg = '' AND unit = 'USD'""",
            block + tag_list + [qtrs]).fetchall()
        for entity_id, by_period in _period_index(rows).items():
            target = merged.setdefault(entity_id, {})
            for pe_ord, tagmap in by_period.items():
                slot = target.setdefault(pe_ord, {})
                for tag, entries in tagmap.items():
                    slot.setdefault(tag, []).extend(entries)
    return _sorted_periods(merged)


# ==========================================================================
# (5) Rank statistics
# ==========================================================================

def spearman(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    """Spearman's rho: Pearson on AVERAGE ranks, so ties are handled.

    `statlib.average_ranks` is the project's one definition of a rank with
    ties; re-deriving it here would be a second answer to what a tie is.
    Returns None for fewer than three points or a constant vector, which are
    the two cases where a correlation is not defined -- never 0.0, which would
    read as "measured, and no agreement".
    """
    if len(a) != len(b) or len(a) < 3:
        return None
    ra, rb = statlib.average_ranks(list(a)), statlib.average_ranks(list(b))
    n = len(ra)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((ra[i] - ma) * (rb[i] - mb) for i in range(n))
    da = sum((ra[i] - ma) ** 2 for i in range(n))
    db = sum((rb[i] - mb) ** 2 for i in range(n))
    if da <= 0 or db <= 0:
        return None
    return num / (da * db) ** 0.5


def ordering_agreement(ref: Sequence[float], alt: Sequence[float],
                       tolerance: float = NEAR_TOLERANCE) -> dict[str, Any]:
    """How often the pairwise ordering of two issuers survives the rung swap.

    EXACT counts a pair as agreeing when `sign(ref_i - ref_j)` equals
    `sign(alt_i - alt_j)`; pairs tied under the reference are excluded from the
    denominator, because there is no ordering to preserve.

    NEAR additionally forgives a reversal between two issuers whose REFERENCE
    multiples differ by less than `tolerance` of their mean -- a pair that was
    too close to call. `material_pairs` is the denominator of that forgiveness,
    so a near-agreement rate can always be read beside the number of pairs it
    actually decided.

    O(n^2) on purpose. n here is a few hundred paired issuers at a date; a
    sampled estimate would add a sampling error to a question that is small
    enough to answer exactly.
    """
    n = len(ref)
    if n != len(alt) or n < 2:
        return {"n": n, "pairs": 0, "exact": None, "near": None,
                "material_pairs": 0, "reversals": 0, "material_reversals": 0}
    pairs = exact = reversals = material = material_rev = 0
    for i in range(n - 1):
        ri, ai = ref[i], alt[i]
        for j in range(i + 1, n):
            rj = ref[j]
            if ri == rj:
                continue                    # no reference ordering to preserve
            pairs += 1
            aj = alt[j]
            agree = (ri < rj) == (ai < aj) and ai != aj
            scale = (abs(ri) + abs(rj)) / 2.0
            close = scale > 0 and abs(ri - rj) / scale < tolerance
            if not close:
                material += 1
            if agree:
                exact += 1
            else:
                reversals += 1
                if not close:
                    material_rev += 1
    if not pairs:
        return {"n": n, "pairs": 0, "exact": None, "near": None,
                "material_pairs": 0, "reversals": 0, "material_reversals": 0}
    return {
        "n": n, "pairs": pairs,
        "n_exact": exact, "n_near": pairs - material_rev,
        "exact": exact / pairs,
        "near": (pairs - material_rev) / pairs,
        "material_pairs": material,
        "material_agreement": ((material - material_rev) / material
                               if material else None),
        "reversals": reversals, "material_reversals": material_rev,
    }


def decile_churn(ref: Sequence[float], alt: Sequence[float],
                 fraction: float = CHEAP_FRACTION) -> dict[str, Any]:
    """How much of the CHEAPEST decile the rung swap replaces.

    Cheapest = lowest EV/EBITDA, which is what the valuation factor rewards.
    Churn is the share of the reference's cheap decile that the rung's cheap
    decile does not contain. This is the material test in the decision rule:
    a ranking can shuffle harmlessly in the middle and still hand a portfolio a
    different basket at the top.
    """
    n = len(ref)
    size = max(1, int(round(n * fraction))) if n else 0
    if n < 2 or n != len(alt):
        return {"n": n, "decile_size": size, "churn": None, "kept": None}
    ref_top = {i for i in sorted(range(n), key=lambda k: ref[k])[:size]}
    alt_top = {i for i in sorted(range(n), key=lambda k: alt[k])[:size]}
    kept = len(ref_top & alt_top)
    return {"n": n, "decile_size": size, "kept": kept,
            "churn": (size - kept) / size if size else None}


def rank_shift(ref: Sequence[float], alt: Sequence[float]) -> dict[str, Any]:
    """Percentile-rank movement per issuer, as a distribution.

    Expressed in PERCENTILE POINTS so it is comparable across dates with
    different sample sizes -- the factor consumes a percentile, not a rank.
    """
    n = len(ref)
    if n < 2 or n != len(alt):
        return {"n": n}
    ra, rb = statlib.average_ranks(list(ref)), statlib.average_ranks(list(alt))
    shifts = [abs(ra[i] - rb[i]) / (n - 1) * 100.0 for i in range(n)]
    out = PC.distribution(shifts, places=2)
    out["pct_moving_more_than_5_points"] = round(
        100.0 * sum(1 for s in shifts if s > 5.0) / n, 2)
    return out


# ==========================================================================
# (6) THE MEASUREMENT
# ==========================================================================

def grid_dates(conn: sqlite3.Connection) -> list[str]:
    """The replay grid: every month-end a peer set was built on."""
    return [r[0] for r in conn.execute(
        """SELECT DISTINCT as_of_date FROM pit_peer_set
            WHERE model_version = ? AND peer_set_version = ?
            ORDER BY as_of_date""",
        (pit_store.EQUITY_PIT_MODEL_VERSION, pit_store.PEER_SET_VERSION))]


def select_dates(grid: Sequence[str], mode: str) -> list[str]:
    """The as-of dates to measure, always including the three census dates.

    The census dates are forced in under every mode because they are the only
    dates whose base universe has a PUBLISHED size, and reproducing that size
    is how this module proves its selector agrees with the one that produced
    the coverage numbers it is arguing with.
    """
    grid = list(grid)
    if mode == "census":
        chosen = [d for d in grid if d in CENSUS_DATES]
    elif mode == "annual":
        chosen = [d for d in grid if d[5:7] == "06"]
    elif mode == "quarterly":
        chosen = [d for d in grid if d[5:7] in ("03", "06", "09", "12")]
    elif mode == "grid":
        chosen = list(grid)
    else:
        raise ValueError(f"unknown date mode {mode!r}")
    forced = [d for d in grid if d in CENSUS_DATES and d not in chosen]
    return sorted(set(chosen) | set(forced))


def reference_capable_entities(
        periods: Mapping[int, Sequence[tuple[int, dict[str, list[tuple[int, float]]]]]],
        ) -> set[int]:
    """Every issuer that could EVER resolve the reference rung, at any date.

    Derived from the fact index alone -- an issuer qualifies when some ONE
    period end carries all three components of the three-way sum. Cheap, and
    date-free on purpose: the ranking half must not have to walk the base
    universe (which needs `peer_universe_as_of` and the Assets window) on 165
    dates to find out which few hundred issuers it can compare at all.
    """
    reference = next(r for r in RUNGS if r.is_reference)
    out: set[int] = set()
    for entity_id, rows in periods.items():
        for _pe_ord, tagmap in rows:
            if all(tag in tagmap for tag in reference.tags):
                out.add(int(entity_id))
                break
    return out


def measure(conn: sqlite3.Connection, db_path: str, *, date_mode: str = "census",
            rank_date_mode: str = "grid", staleness: str = "v2",
            progress: bool = True) -> dict[str, Any]:
    """The whole measurement. Reads; writes nothing.

    Two halves with two different sample scopes AND two different date grids,
    kept apart in the payload because they answer different questions:

      coverage   FULL_REPORTING_UNIVERSE, on the CENSUS dates by default.
                 Which rungs resolve, for whom, in which SIC division, and
                 what each rung's value is as a ratio of the reference total on
                 the issuers that carry both. The census dates are the default
                 because they are the only dates whose base universe has a
                 published size -- this half exists to be comparable with the
                 numbers the ladder was argued from, and its denominator costs
                 a `peer_universe_as_of` and an Assets window per date.

      ranking    SURVIVOR_PRICED_DIAGNOSTIC, on the WHOLE 165-date grid by
                 default. Only ~24 issuers per date carry the reference rung
                 AND a price AND a defensible share count AND EBITDA, so three
                 dates cannot decide anything; the full grid is what makes the
                 pooled pairwise sample large enough to have a verdict at all.
                 It needs no base universe, so it costs a fraction per date.
    """
    watch = PC.ResourceWatch(db_path)
    started = time.time()
    grid = grid_dates(conn)
    dates = select_dates(grid, date_mode)
    rank_dates = select_dates(grid, rank_date_mode)
    if progress:
        print(f"  grid: {len(grid)} month-end dates {grid[0]}..{grid[-1]}; "
              f"coverage on {len(dates)} ({date_mode}), ranking on "
              f"{len(rank_dates)} ({rank_date_mode})", flush=True)

    debt = debt_index(conn, progress=progress)
    debt_periods = debt["periods"]
    watch.sample()

    # Base universe, reproduced rather than assumed. Both staleness policies,
    # because the published base sizes were measured under v1.
    masks_v1 = assets_mask_index(conn, grid, "v1", progress=progress)
    masks_v2 = (assets_mask_index(conn, grid, "v2", progress=progress)
                if staleness != "v1" else masks_v1)
    watch.sample()
    grid_pos = {day: i for i, day in enumerate(grid)}

    sics = PC.sic_index(conn)
    debt_months = age_months(DEBT_CONCEPT, INSTANT_QTRS, staleness)
    debt_months_v1 = age_months(DEBT_CONCEPT, INSTANT_QTRS, "v1")

    # ---- COVERAGE HALF -------------------------------------------------
    coverage: dict[str, Any] = {}
    reference_capable = reference_capable_entities(debt_periods)
    for day in dates:
        peers = {int(r["entity_id"]) for r in pit_identity.peer_universe_as_of(conn, day)}
        bit = 1 << grid_pos[day]
        base_v1 = {e for e in peers if masks_v1.get(e, 0) & bit}
        base = {e for e in peers if masks_v2.get(e, 0) & bit} if staleness != "v1" else base_v1
        day_ord = _ord(day)
        per_rung = {r.index: {"resolves": 0, "marginal": 0,
                              "divisions": {}, "marginal_divisions": {}}
                    for r in RUNGS}
        per_rung_v1 = {r.index: 0 for r in RUNGS}
        ratios: dict[int, list[float]] = {r.index: [] for r in RUNGS}
        ratio_divisions: dict[int, dict[str, list[float]]] = {r.index: {} for r in RUNGS}
        mismatched_period = {r.index: 0 for r in RUNGS}
        any_resolved = any_resolved_v1 = any_v1_ladder = 0
        v1_indexes = {r.index for r in RUNGS
                      if r.key in {x.key for x in debt_rungs(pit_store.LADDER_VERSION)}}
        for entity_id in base:
            periods = debt_periods.get(entity_id)
            if not periods:
                continue
            resolved = resolve_rungs(periods, day_ord, RUNGS, debt_months)
            resolved_v1 = resolve_rungs(periods, day_ord, RUNGS, debt_months_v1)
            if resolved_v1:
                any_resolved_v1 += 1
                for index in resolved_v1:
                    per_rung_v1[index] += 1
                if v1_indexes & set(resolved_v1):
                    any_v1_ladder += 1
            if not resolved:
                continue
            any_resolved += 1
            division = PC.division_of_sic(PC.sic_as_of(sics, entity_id, day))
            winner = min(resolved)
            for index, (value, pe_ord) in resolved.items():
                slot = per_rung[index]
                slot["resolves"] += 1
                slot["divisions"][division] = slot["divisions"].get(division, 0) + 1
                if index == winner:
                    slot["marginal"] += 1
                    slot["marginal_divisions"][division] = \
                        slot["marginal_divisions"].get(division, 0) + 1
            ref = resolved.get(REFERENCE_INDEX)
            if ref is not None and ref[0] > 0:
                for index, (value, pe_ord) in resolved.items():
                    if index == REFERENCE_INDEX:
                        continue
                    if pe_ord != ref[1]:
                        mismatched_period[index] += 1
                        continue            # a ratio across two balance sheets
                    ratios[index].append(value / ref[0])
                    ratio_divisions[index].setdefault(division, []).append(value / ref[0])
        coverage[day] = {
            "sample_scope": SCOPE_FULL,
            "n_peer_universe": len(peers),
            "n_base": len(base), "n_base_v1_staleness": len(base_v1),
            "n_any_rung": any_resolved, "n_any_rung_v1_staleness": any_resolved_v1,
            "pct_any_rung_of_base": _pct(any_resolved, len(base)),
            "pct_any_rung_v1_of_base_v1": _pct(any_resolved_v1, len(base_v1)),
            "n_v1_ladder_v1_staleness": any_v1_ladder,
            "pct_v1_ladder_of_base_v1": _pct(any_v1_ladder, len(base_v1)),
            "rungs": {
                RUNGS[i].key: {
                    "quantity": RUNGS[i].quantity, "bound": RUNGS[i].bound,
                    "n_resolves": per_rung[i]["resolves"],
                    "pct_of_base": _pct(per_rung[i]["resolves"], len(base)),
                    "n_marginal": per_rung[i]["marginal"],
                    "marginal_pct_of_base": _pct(per_rung[i]["marginal"], len(base)),
                    "n_resolves_v1_staleness": per_rung_v1[i],
                    "pct_of_base_v1_staleness": _pct(per_rung_v1[i], len(base_v1)),
                    "provenance_ratio_to_reference": PC.distribution(ratios[i]),
                    "provenance_ratio_by_division": {
                        d: PC.distribution(v) for d, v in
                        sorted(ratio_divisions[i].items()) if len(v) >= 5},
                    "n_period_mismatch_excluded": mismatched_period[i],
                    "marginal_divisions": dict(sorted(
                        per_rung[i]["marginal_divisions"].items())),
                } for i in sorted(per_rung)},
        }
        watch.sample()
        if progress:
            print(f"    {day}: base {len(base):,}, any rung {any_resolved:,} "
                  f"({_pct(any_resolved, len(base))}%)", flush=True)

    # ---- RANKING HALF ---------------------------------------------------
    if progress:
        print(f"  ranking: {len(reference_capable):,} issuers can ever carry the "
              f"reference rung; pulling cash and EBITDA for them", flush=True)
    cash_ranked = ranked_tags(CASH_CONCEPT)
    cash_tags = [t for t, _ in cash_ranked]
    oi_ranked = ranked_tags("operating_income")
    da_ranked = ranked_tags("depreciation_amortisation")
    cash_periods = targeted_index(conn, sorted(reference_capable), cash_tags, INSTANT_QTRS)
    flow_periods = targeted_index(
        conn, sorted(reference_capable),
        [t for t, _ in oi_ranked] + [t for t, _ in da_ranked], ANNUAL_QTRS)
    watch.sample()
    cash_months = age_months(CASH_CONCEPT, INSTANT_QTRS, staleness)
    flow_months = age_months("operating_income", ANNUAL_QTRS, staleness)
    _band, band_lo, band_hi = FS.EV_EBITDA_SIX.value_band

    paired: dict[int, list[dict[str, Any]]] = {r.index: [] for r in RUNGS}
    per_date: dict[str, Any] = {}
    screens = {"no_price_or_shares": 0, "no_ebitda": 0, "ebitda_not_positive": 0,
               "no_cash": 0, "ev_not_positive_reference": 0,
               "ev_not_positive_rung": 0, "outside_band_reference": 0,
               "outside_band_rung": 0}
    for day in rank_dates:
        day_ord = _ord(day)
        priced = {}
        for row in pit_identity.scored_universe_as_of(conn, day):
            priced.setdefault(int(row["entity_id"]), int(row["listing_id"]))
        rows: list[dict[str, Any]] = []
        for entity_id in sorted(reference_capable & set(priced)):
            periods = debt_periods.get(entity_id)
            if not periods:
                continue
            resolved = resolve_rungs(periods, day_ord, RUNGS, debt_months)
            ref = resolved.get(REFERENCE_INDEX)
            if ref is None:
                continue
            ebitda = resolve_ebitda(flow_periods.get(entity_id, []), day_ord,
                                    oi_ranked, da_ranked, flow_months)
            if ebitda is None:
                screens["no_ebitda"] += 1
                continue
            if ebitda[0] <= 0:
                screens["ebitda_not_positive"] += 1
                continue
            cash = resolve_ladder_value(cash_periods.get(entity_id, []), day_ord,
                                        cash_ranked, cash_months)
            if cash is None:
                screens["no_cash"] += 1
                continue
            record = pit_rawprice.market_cap_as_of_detail(
                conn, entity_id, day, listing_id=priced[entity_id])
            mcap = record.get("market_cap")
            if mcap is None or mcap <= 0:
                screens["no_price_or_shares"] += 1
                continue
            ev_ref = mcap + ref[0] - cash[0]
            if ev_ref <= 0:
                screens["ev_not_positive_reference"] += 1
                continue
            ratio_ref = ev_ref / ebitda[0]
            if not (band_lo <= ratio_ref <= band_hi):
                screens["outside_band_reference"] += 1
                continue
            entry = {"entity_id": entity_id, "as_of": day,
                     "division": PC.division_of_sic(PC.sic_as_of(sics, entity_id, day)),
                     "listing_id": priced[entity_id], "market_cap": mcap,
                     "cash": cash[0], "ebitda": ebitda[0],
                     "debt_reference": ref[0], "ratio_reference": ratio_ref,
                     "rungs": {}}
            for index, (value, _pe) in resolved.items():
                if index == REFERENCE_INDEX:
                    continue
                ev = mcap + value - cash[0]
                if ev <= 0:
                    screens["ev_not_positive_rung"] += 1
                    continue
                ratio = ev / ebitda[0]
                if not (band_lo <= ratio <= band_hi):
                    screens["outside_band_rung"] += 1
                    continue
                entry["rungs"][index] = {"debt": value, "ratio": ratio}
            rows.append(entry)
        per_date[day] = {"n_with_reference_and_price": len(rows), "rungs": {}}
        for rung in RUNGS:
            if rung.index == REFERENCE_INDEX:
                continue
            subset = [r for r in rows if rung.index in r["rungs"]]
            for r in subset:
                paired[rung.index].append({
                    "as_of": day, "entity_id": r["entity_id"],
                    "division": r["division"], "listing_id": r["listing_id"],
                    "ratio_reference": r["ratio_reference"],
                    "ratio_rung": r["rungs"][rung.index]["ratio"],
                    "debt_reference": r["debt_reference"],
                    "debt_rung": r["rungs"][rung.index]["debt"]})
            per_date[day]["rungs"][rung.key] = _agreement_block(subset, rung.index)
        watch.sample()
        if progress:
            print(f"    {day}: {len(rows):,} issuers priced with the reference rung",
                  flush=True)

    pooled = {rung.key: _pooled_block(paired[rung.index], rung) for rung in RUNGS}
    returns = survivor_return_diagnostic(conn, paired)

    payload: dict[str, Any] = {
        "module": "pit_debt_quality",
        "measured_on": _dt.date.today().isoformat(),
        "ladder_version": LADDER,
        "staleness_policy": ("fact_kind_staleness_v2" if staleness == "v2"
                             else "concept_ladder_v1 cadence bounds"),
        "reference_rung": REFERENCE_RUNG_KEY,
        "rungs": [r.as_dict() for r in RUNGS],
        "decision_rule": DECISION_RULE,
        "date_mode": date_mode, "dates": dates,
        "rank_date_mode": rank_date_mode, "rank_dates": rank_dates,
        "n_reference_capable_issuers": len(reference_capable),
        "coverage": coverage,
        "ranking": {"sample_scope": SCOPE_PRICED, "by_date": per_date,
                    "pooled": pooled, "screens": screens,
                    "screen_source": {
                        "positive_screen": FS.EV_EBITDA_SIX.positive_screen,
                        "value_band": list(FS.EV_EBITDA_SIX.value_band),
                        "rule_id": FS.EV_EBITDA_SIX.rule_id}},
        "survivor_return_diagnostic": returns,
        "quality_factor_proposal": quality_proposal(coverage, pooled),
        "resources": watch.report(),
        "elapsed_seconds": round(time.time() - started, 1),
        "rows_written_to_the_store": 0,
    }
    return payload


def _pct(num: int, den: int, places: int = 2) -> Optional[float]:
    return round(100.0 * num / den, places) if den else None


def _agreement_block(subset: Sequence[Mapping[str, Any]], index: int) -> dict[str, Any]:
    """Spearman, ordering agreement, churn and rank shift for one date+rung."""
    ref = [float(r["ratio_reference"]) for r in subset]
    alt = [float(r["rungs"][index]["ratio"]) for r in subset]
    block: dict[str, Any] = {"n": len(subset)}
    if len(subset) < MIN_RANK_SAMPLE:
        block["verdict_eligible"] = False
        block["note"] = (f"below the {MIN_RANK_SAMPLE}-issuer floor; contributes "
                         "to the pooled sample and gets no date-level verdict")
        return block
    block["verdict_eligible"] = True
    block["spearman"] = _round(spearman(ref, alt), 4)
    block["ordering"] = _round_map(ordering_agreement(ref, alt))
    block["cheap_decile"] = _round_map(decile_churn(ref, alt))
    block["rank_shift_percentile_points"] = rank_shift(ref, alt)
    return block


def _pooled_block(rows: Sequence[Mapping[str, Any]], rung: RungSpec) -> dict[str, Any]:
    """The rung's pooled result. THE PAIR IS THE UNIT, AND IT NEVER CROSSES A DATE.

    Pooling the RATIOS across dates would rank a 2015 multiple against a 2024
    one and call the difference a provenance effect, so every pair is formed
    inside one as-of date. What is pooled is the COUNTS: how many within-date
    pairs there were, and how many of them this rung ordered the same way the
    exact total does. That is a real rate over a real denominator, and it is
    what makes ~24 comparable issuers a date add up to a decidable sample over
    165 dates -- where an unweighted mean of 165 noisy per-date rates would not.

    `cheap_decile_churn` is pooled the same way: the denominator is the number
    of reference-cheap-decile SLOTS across dates, and the numerator is how many
    of those slots the rung's own cheap decile did not contain.

    Spearman is the exception and is reported as a size-weighted mean of
    per-date rho over dates clearing `MIN_SPEARMAN_N`, because a correlation
    coefficient has no counts to add. It is the weakest of the three figures
    and is listed beside the two that do have denominators.
    """
    by_date: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        by_date.setdefault(str(row["as_of"]), []).append(row)
    totals = {"pairs": 0, "exact": 0, "near": 0, "material_pairs": 0,
              "material_agree": 0, "decile_slots": 0, "decile_kept": 0}
    rho_weighted: list[tuple[float, int]] = []
    n_dates_used = n_dates_above_floor = 0
    debt_ratio: list[float] = []
    for _day, block in sorted(by_date.items()):
        ref = [float(r["ratio_reference"]) for r in block]
        alt = [float(r["ratio_rung"]) for r in block]
        for r in block:
            if r["debt_reference"]:
                debt_ratio.append(float(r["debt_rung"]) / float(r["debt_reference"]))
        if len(block) < 2:
            continue
        n_dates_used += 1
        if len(block) >= MIN_RANK_SAMPLE:
            n_dates_above_floor += 1
        order = ordering_agreement(ref, alt)
        if order["pairs"]:
            totals["pairs"] += order["pairs"]
            totals["exact"] += order["n_exact"]
            totals["near"] += order["n_near"]
            totals["material_pairs"] += order["material_pairs"]
            totals["material_agree"] += (order["material_pairs"]
                                         - order["material_reversals"])
        churn = decile_churn(ref, alt)
        if churn.get("kept") is not None:
            totals["decile_slots"] += churn["decile_size"]
            totals["decile_kept"] += churn["kept"]
        if len(block) >= MIN_SPEARMAN_N:
            rho = spearman(ref, alt)
            if rho is not None:
                rho_weighted.append((rho, len(block)))

    def rate(num: str, den: str) -> Optional[float]:
        return totals[num] / totals[den] if totals[den] else None

    rho_total = sum(w for _v, w in rho_weighted)
    out: dict[str, Any] = {
        "quantity": rung.quantity, "bound": rung.bound,
        "n_paired_issuer_dates": len(rows),
        "n_dates": len(by_date), "n_dates_above_floor": n_dates_above_floor,
        "n_dates_contributing_pairs": n_dates_used,
        "n_within_date_pairs": totals["pairs"],
        "n_material_pairs": totals["material_pairs"],
        "n_cheap_decile_slots": totals["decile_slots"],
        "spearman_weighted": _round(
            (sum(v * w for v, w in rho_weighted) / rho_total) if rho_total else None, 4),
        "n_dates_in_spearman": len(rho_weighted),
        "exact_ordering_agreement": _round(rate("exact", "pairs"), 4),
        "near_ordering_agreement": _round(rate("near", "pairs"), 4),
        "material_pair_agreement": _round(rate("material_agree", "material_pairs"), 4),
        "cheap_decile_churn": _round(
            (1.0 - totals["decile_kept"] / totals["decile_slots"])
            if totals["decile_slots"] else None, 4),
        "debt_ratio_to_reference": PC.distribution(debt_ratio),
        "by_division": {},
    }
    divisions: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        divisions.setdefault(str(row["division"]), []).append(row)
    for division, block in sorted(divisions.items()):
        ratios = [float(r["debt_rung"]) / float(r["debt_reference"])
                  for r in block if r["debt_reference"]]
        out["by_division"][division] = {
            "n": len(block), "debt_ratio": PC.distribution(ratios)}
    return out


def _round(value: Optional[float], places: int) -> Optional[float]:
    return None if value is None else round(float(value), places)


def _round_map(block: Mapping[str, Any], places: int = 4) -> dict[str, Any]:
    return {k: (round(v, places) if isinstance(v, float) else v)
            for k, v in block.items()}


# ==========================================================================
# (7) THE SURVIVOR-ONLY RETURN DIAGNOSTIC -- reported, and barred from deciding
# ==========================================================================

def survivor_return_diagnostic(conn: sqlite3.Connection,
                               paired: Mapping[int, Sequence[Mapping[str, Any]]],
                               horizon: str = RETURN_HORIZON) -> dict[str, Any]:
    """Forward returns of the issuers each rung MOVES. Diagnostic only.

    The question it answers: when the rung swap pushes an issuer into or out of
    the cheapest decile, did that issuer go on to earn a different return? It is
    a real question and this is not a sample that can answer it. Every label
    here comes from `pit_label`, whose policy version says so in its own name --
    `label_v1_adjclose_sessions_survivor_only` -- over 2,574 listings every one
    of which is alive in 2026, whose median member underperforms the total
    market by -7.45% at 12M.

    So this block carries `may_promote_a_rung = False` and `verdict()` does not
    read it. It is here because the owner asked what the returns do, and the
    honest answer includes the number AND the reason it cannot decide.
    """
    out: dict[str, Any] = {
        "sample_scope": SCOPE_RETURNS,
        "label_policy_version": pit_store.LABEL_POLICY_VERSION,
        "horizon": horizon,
        "may_promote_a_rung": False,
        "why_it_may_not_promote": (
            "2,574 listings, all alive in 2026; the median survivor "
            "underperforms the total market by -7.45% at 12M. A return measured "
            "here describes a population that did not exist at the as-of date. "
            "If this block and the ranking block disagree, the ranking wins."),
        "rungs": {},
    }
    wanted: set[tuple[int, str]] = set()
    for rows in paired.values():
        for row in rows:
            wanted.add((int(row["listing_id"]), str(row["as_of"])))
    labels: dict[tuple[int, str], float] = {}
    listings = sorted({l for l, _d in wanted})
    days = sorted({d for _l, d in wanted})
    marks_d = ", ".join("?" * len(days))
    for start in range(0, len(listings), 300):
        block = listings[start:start + 300]
        marks_l = ", ".join("?" * len(block))
        for listing_id, as_of, forward in conn.execute(
                f"""SELECT listing_id, as_of_date, forward_return
                      FROM pit_label
                     WHERE horizon = ? AND listing_id IN ({marks_l})
                       AND as_of_date IN ({marks_d})""",
                [horizon] + block + days):
            key = (int(listing_id), str(as_of))
            if forward is not None and key in wanted:
                labels[key] = float(forward)
    out["n_labels_matched"] = len(labels)
    out["n_pairs_wanted"] = len(wanted)

    for rung in RUNGS:
        rows = paired.get(rung.index) or []
        if not rows:
            continue
        by_date: dict[str, list[Mapping[str, Any]]] = {}
        for row in rows:
            by_date.setdefault(str(row["as_of"]), []).append(row)
        entered: list[float] = []
        left: list[float] = []
        stayed: list[float] = []
        universe: list[float] = []
        for day, block in by_date.items():
            if len(block) < MIN_SPEARMAN_N:
                continue        # a "decile" of two names is not a decile
            ref = [float(r["ratio_reference"]) for r in block]
            alt = [float(r["ratio_rung"]) for r in block]
            size = max(1, int(round(len(block) * CHEAP_FRACTION)))
            ref_top = set(sorted(range(len(block)), key=lambda k: ref[k])[:size])
            alt_top = set(sorted(range(len(block)), key=lambda k: alt[k])[:size])
            for i, row in enumerate(block):
                ret = labels.get((int(row["listing_id"]), day))
                if ret is None:
                    continue
                universe.append(ret)
                if i in alt_top and i not in ref_top:
                    entered.append(ret)
                elif i in ref_top and i not in alt_top:
                    left.append(ret)
                elif i in ref_top and i in alt_top:
                    stayed.append(ret)
        out["rungs"][rung.key] = {
            "n_labelled": len(universe),
            "all_paired_issuers": PC.distribution(universe),
            "cheap_under_both": PC.distribution(stayed),
            "pushed_INTO_the_cheap_decile_by_this_rung": PC.distribution(entered),
            "pushed_OUT_of_the_cheap_decile_by_this_rung": PC.distribution(left),
            "reading": ("if the issuers this rung pushes INTO the cheap decile "
                        "earn less than the ones it pushes OUT, the rung is "
                        "degrading the selection -- among survivors, which is "
                        "not the market"),
        }
    return out


# ==========================================================================
# (8) THE QUALITY FACTOR -- proposed FROM THE MEASUREMENT, for DEBT ALONE
# ==========================================================================

#: The floor a Q may take. Not 0: a rung whose ordering is 60% preserved still
#: carries information, and a zero would silently delete the row rather than
#: discount it, which is a different decision and not one this measures.
Q_FLOOR = 0.25


def quality_proposal(coverage: Mapping[str, Any],
                     pooled: Mapping[str, Any]) -> dict[str, Any]:
    """Q per debt rung, read off the measurement. Debt dimension ONLY.

    THE DEFINITION, chosen so that Q is a measured quantity with a unit rather
    than a taste:

        Q(rung) = the measured NEAR ORDERING AGREEMENT of that rung's EV/EBITDA
                  ranking against the exact-total ranking, pooled within dates.

    In words: the share of pairwise valuation comparisons that this rung's debt
    figure would have decided the same way the true total decides them, once
    pairs too close to call are forgiven. Q = 1.0 for the reference rung by
    construction. Q is UNKNOWN -- never a default, never 1.0 -- for a rung with
    fewer than MIN_POOLED_SAMPLE paired observations.

    WHAT Q IS NOT. It is not a correction: it does not rescale a lower-bound
    debt figure up to an estimated total, which `pit_policy`'s v2 consumer rule
    forbids in as many words -- the measured ratios are a distribution, not a
    factor. Q discounts CONFIDENCE in a ratio; it never edits the ratio.

    SCOPE. This proposes Q for the DEBT dimension of EV/EBITDA and nothing
    else. The owner has NOT chosen how DataQuality and PeerCoherence combine,
    and this module deliberately proposes no combination rule: a Q multiplied
    into a coherence weight by an author who was not asked to decide it would
    be a decision taken by accident.
    """
    census = [d for d in CENSUS_DATES if d in coverage]
    out: dict[str, Any] = {
        "dimension": "debt_provenance_only",
        "applies_to": "ev_ebitda (and any factor whose input is total_debt)",
        "definition": ("Q(rung) = pooled near ordering agreement of that rung's "
                       "EV/EBITDA ranking against the exact-total ranking"),
        "q_floor": Q_FLOOR,
        "is_a_confidence_weight_not_a_correction": True,
        "combination_rule_with_peer_coherence": "NOT PROPOSED -- the owner has "
            "not chosen it, and this module measures one dimension",
        "rungs": {},
    }
    for rung in RUNGS:
        block = pooled.get(rung.key) or {}
        n = int(block.get("n_paired_issuer_dates") or 0)
        near = block.get("near_ordering_agreement")
        cov = [coverage[d]["rungs"][rung.key]["marginal_pct_of_base"] for d in census
               if rung.key in coverage[d]["rungs"]]
        cov_points = (sum(c for c in cov if c is not None) / len(cov)) if cov else None
        if rung.is_reference:
            q: Any = 1.0
            basis = "the reference definition: it ranks against itself"
        elif (n < MIN_POOLED_SAMPLE or near is None
              or int(block.get("n_within_date_pairs") or 0) < MIN_POOLED_PAIRS):
            q = UNKNOWN
            basis = (f"{n} paired issuer-dates and "
                     f"{int(block.get('n_within_date_pairs') or 0):,} within-date "
                     f"pairs is under the {MIN_POOLED_SAMPLE} / "
                     f"{MIN_POOLED_PAIRS:,} floors; reported UNKNOWN rather "
                     "than defaulted")
        else:
            q = round(max(Q_FLOOR, float(near)), 3)
            basis = (f"measured near ordering agreement {near:.4f} over "
                     f"{n:,} paired issuer-dates")
        out["rungs"][rung.key] = {
            "quantity": rung.quantity, "bound": rung.bound, "Q_debt": q,
            "basis": basis, "n_paired_issuer_dates": n,
            "mean_marginal_coverage_points_of_base": _round(cov_points, 2),
            "median_debt_ratio_to_reference":
                (block.get("debt_ratio_to_reference") or {}).get("p50"),
            "verdict": verdict(rung, cov_points, near,
                               block.get("cheap_decile_churn"), n,
                               int(block.get("n_within_date_pairs") or 0)),
        }
    return out


# ==========================================================================
# (8b) THE ANSWER, in one block, computed from the payload
# ==========================================================================

def headline(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The question this module was asked, answered from its own numbers.

    A COMPUTED block, not a paragraph: every figure in the sentence is read out
    of `payload`, so a re-run on different data produces a different headline
    rather than the same claim over new numbers. Rungs that came out
    UNDETERMINED are excluded from the ranges and named separately -- averaging
    a rung nobody could measure into a headline is how an unmeasured thing
    becomes a measured one.
    """
    pooled = (payload.get("ranking") or {}).get("pooled") or {}
    proposal = (payload.get("quality_factor_proposal") or {}).get("rungs") or {}
    coverage = payload.get("coverage") or {}
    census = [d for d in CENSUS_DATES if d in coverage]

    shares: list[float] = []
    for day in census:
        block = coverage[day]
        resolved = block["n_any_rung"]
        if not resolved:
            continue
        lower = sum(block["rungs"][r.key]["n_marginal"] for r in RUNGS
                    if r.bound == pit_policy.BOUND_LOWER
                    and r.key in block["rungs"])
        shares.append(100.0 * lower / resolved)
    lower_bound_share = round(sum(shares) / len(shares), 2) if shares else None

    decided: dict[str, list[str]] = {}
    for rung in RUNGS:
        block = proposal.get(rung.key) or {}
        decided.setdefault(
            (block.get("verdict") or {}).get("verdict", VERDICT_UNDETERMINED),
            []).append(rung.key)

    measurable = [r for r in RUNGS if not r.is_reference
                  and (proposal.get(r.key, {}).get("verdict", {}).get("verdict")
                       not in (None, VERDICT_UNDETERMINED))]
    nears = [pooled[r.key]["near_ordering_agreement"] for r in measurable
             if (pooled.get(r.key) or {}).get("near_ordering_agreement") is not None]
    churns = [pooled[r.key]["cheap_decile_churn"] for r in measurable
              if (pooled.get(r.key) or {}).get("cheap_decile_churn") is not None]
    marginal = []
    for rung in measurable:
        for day in census:
            row = coverage[day]["rungs"].get(rung.key)
            if row and row["marginal_pct_of_base"] is not None:
                marginal.append(row["marginal_pct_of_base"])

    if nears and churns:
        answer = (
            f"the ordering survives nearly intact -- every rung with enough "
            f"co-tagged priced issuers to measure keeps "
            f"{min(nears):.1%} to {max(nears):.1%} of pairwise EV/EBITDA "
            f"ordering against the exact total -- but the CHEAPEST DECILE, "
            f"which is the part a decision is taken on, churns "
            f"{min(churns):.1%} to {max(churns):.1%}. The recovery is real "
            f"enough to keep and not clean enough to pool: EV/EBITDA needs a "
            f"debt data-quality factor, not a bigger denominator.")
    else:
        answer = ("no rung cleared the sample floors; the question is "
                  "UNDETERMINED on this store rather than answered either way")

    return {
        "question": ("does concept_ladder_v2's total_debt coverage recovery "
                     "survive a ranking test, or does it only fill cells?"),
        "answer_in_one_line": answer,
        "measured_range": {
            "near_ordering_agreement": [_round(min(nears), 4) if nears else None,
                                        _round(max(nears), 4) if nears else None],
            "cheap_decile_churn": [_round(min(churns), 4) if churns else None,
                                   _round(max(churns), 4) if churns else None],
            "marginal_coverage_points_of_base": [
                _round(min(marginal), 2) if marginal else None,
                _round(max(marginal), 2) if marginal else None],
            "rungs_included": [r.key for r in measurable],
        },
        "share_of_resolved_cross_section_on_a_lower_bound_rung": lower_bound_share,
        "verdicts": decided,
        "what_it_implies_for_the_provenance_multiplier": (
            "rung quality varies materially and measurably -- near ordering "
            "agreement and cheap-decile churn differ across rungs by more than "
            "their sampling error -- so an EV/EBITDA computed on a lower-bound "
            "rung is NOT the same measurement as one computed on the exact "
            "total, and presenting them in one percentile without a quality "
            "factor asserts that they are. Q is proposed per rung, for the DEBT "
            "dimension alone; no combination rule with PeerCoherence is "
            "proposed, because the owner has not chosen one."),
        "representativeness_of_the_ranking_sample": _representativeness(payload),
        "what_is_still_unmeasurable": (
            "a rung listed under UNDETERMINED is not cleared and not condemned. "
            "Too few co-tagged, priced issuers carry it beside the exact total "
            "to rank it, and that is reported as the answer rather than filled "
            "in with the nearest plausible number."),
    }


def _representativeness(payload: Mapping[str, Any]) -> dict[str, Any]:
    """WHO the rank-agreement result was measured on, and who it was not.

    The rank test can only run where the EXACT rung resolves, and the exact
    rung requires `ShortTermBorrowings` -- a tag fewer than one issuer in
    eleven files. So the comparison sample is not a random slice of the
    cross-section, and saying which way that biases the answer would be
    inventing a direction:

      * an issuer that does not tag short-term borrowings may genuinely have
        none, in which case the lower-bound rung EQUALS the total for it and
        there is no reshuffle at all -- the measured churn would be an upper
        bound;
      * or it may have short-term debt and not tag it, in which case the
        understatement is there and unmeasured -- the measured churn would be
        a lower bound.

    `pit_policy.absence_limits()` is explicit that this store cannot tell the
    two apart: 159,522 empty-valued DERA rows were counted and discarded at
    ingest, so `never_tagged` and `tagged_zero` are the same absence here. The
    direction is therefore reported as UNKNOWN rather than guessed.
    """
    pooled = (payload.get("ranking") or {}).get("pooled") or {}
    by_date = (payload.get("ranking") or {}).get("by_date") or {}
    widest = max(
        (b for b in pooled.values() if b.get("by_division")),
        key=lambda b: b.get("n_paired_issuer_dates", 0), default=None)
    divisions: dict[str, Any] = {}
    if widest:
        total = sum(v["n"] for v in widest["by_division"].values()) or 1
        divisions = {k: {"n": v["n"], "share_pct": round(100.0 * v["n"] / total, 2)}
                     for k, v in sorted(widest["by_division"].items(),
                                        key=lambda kv: -kv[1]["n"])}
    by_month: dict[str, list[int]] = {}
    for day, block in by_date.items():
        by_month.setdefault(day[5:7], []).append(
            int(block.get("n_with_reference_and_price") or 0))
    month_medians = {m: statlib.median(v) for m, v in sorted(by_month.items())}
    usable = {m: v for m, v in month_medians.items() if v is not None}
    return {
        "sample_is_not_random": True,
        "gate": ("the exact rung requires ShortTermBorrowings, filed by fewer "
                 "than one issuer in eleven"),
        "division_mix_of_the_paired_sample": divisions,
        "median_comparable_issuers_by_calendar_month": month_medians,
        "calendar_spread_points": (
            round(max(usable.values()) - min(usable.values()), 2) if usable else None),
        "calendar_note": (
            "the thin months are a SHARE-COUNT artefact, not a debt one: "
            "`pit_rawprice.market_cap_as_of` resolves the cover-page count "
            "under the v1 twelve-month bound, which is the calendar artefact "
            "`fact_kind_staleness_v2` was written to remove. This module does "
            "not patch the frozen price path to get a bigger sample."),
        "direction_of_the_bias": UNKNOWN,
        "why_direction_is_unknown": (
            "an untagged ShortTermBorrowings may be a real zero or an untagged "
            "amount, and pit_policy.absence_limits() records that this store "
            "cannot separate never_tagged from tagged_zero -- 159,522 "
            "empty-valued rows were counted and discarded at ingest. So the "
            "measured churn is not declared an upper or a lower bound."),
    }


# ==========================================================================
# (9) Validation: reproduce what is already published before claiming anything
# ==========================================================================

def validate(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Check this module's selector against the PUBLISHED census numbers.

    Three reproductions, and each one is a different part of the selector:

      base size            `pit_derive.MEASURED_BASE` -- the peer universe
                           gate and the Assets staleness window.
      v1 ladder coverage   `pit_derive.MEASURED_TOTAL_DEBT['concept_ladder_v1']`
                           -- the composite-rung rule and the vintage pick.
      v2 ladder coverage   the same, for v2 -- the whole rung ladder.

    A mismatch is REPORTED, not raised: this module's staleness default is v2
    and the published numbers are v1, so a small gap is expected and its size
    is the interesting part. The v1 columns are computed side by side precisely
    so the comparison is like for like.
    """
    checks: list[dict[str, Any]] = []
    coverage = payload.get("coverage") or {}
    for day in CENSUS_DATES:
        block = coverage.get(day)
        if not block:
            continue
        published = pit_derive.MEASURED_BASE.get(day)
        checks.append({
            "check": f"base universe at {day}", "published": published,
            "measured": block["n_base_v1_staleness"],
            "delta": (block["n_base_v1_staleness"] - published
                      if published is not None else None),
            "basis": "v1 Assets staleness, which is what the census used"})
        pub1 = pit_derive.MEASURED_TOTAL_DEBT["concept_ladder_v1"].get(day)
        checks.append({
            "check": f"concept_ladder_v1 total_debt coverage at {day}",
            "published": (round(100.0 * pub1, 2) if pub1 is not None else None),
            "measured": v1_ladder_coverage(payload, day),
            "basis": "v1 rung set, v1 staleness"})
        pub2 = pit_derive.MEASURED_TOTAL_DEBT["concept_ladder_v2"].get(day)
        checks.append({
            "check": f"concept_ladder_v2 total_debt coverage at {day}",
            "published": (round(100.0 * pub2, 2) if pub2 is not None else None),
            "measured": block["pct_any_rung_v1_of_base_v1"],
            "basis": "v2 rung set, v1 staleness (the census's own basis)"})
    return checks


def v1_ladder_coverage(payload: Mapping[str, Any], day: str) -> Optional[float]:
    """What the v1 ladder alone resolves -- the UNION over v1's four rungs.

    v1's rungs are a SUBSET of v2's by key, because v2 introduces no new tag
    and renames nothing. A per-rung count cannot be summed into a union (an
    issuer carrying two rungs would be counted twice), so the union is
    accumulated during the census pass and stored as
    `n_v1_ladder_v1_staleness`. This reads it rather than re-deriving it.
    """
    block = (payload.get("coverage") or {}).get(day)
    if not block:
        return None
    return block.get("pct_v1_ladder_of_base_v1")


# ==========================================================================
# (10) Report
# ==========================================================================

def render(payload: Mapping[str, Any], width: int = 78) -> str:
    lines: list[str] = []
    add = lines.append
    add("=" * width)
    add("DEBT-RUNG QUALITY: is the EV/EBITDA coverage recovery real?")
    add("=" * width)
    for line in _wrap((payload.get("headline") or {}).get(
            "answer_in_one_line", ""), width - 2):
        add("  " + line)
    add("")
    add(f"ladder {payload['ladder_version']}   staleness {payload['staleness_policy']}")
    add(f"reference rung: {payload['reference_rung']}")
    add(f"coverage on {len(payload['dates'])} dates ({payload['date_mode']}), "
        f"ranking on {len(payload.get('rank_dates') or [])} dates "
        f"({payload.get('rank_date_mode')})")
    add(f"{payload['elapsed_seconds']}s, "
        f"{payload['rows_written_to_the_store']} rows written to the store")
    add("")
    add("-" * width)
    add(f"COVERAGE AND PROVENANCE  [{SCOPE_FULL}]")
    add("-" * width)
    for day in CENSUS_DATES:
        block = (payload.get("coverage") or {}).get(day)
        if not block:
            continue
        add(f"{day}   base {block['n_base']:,}   any rung "
            f"{block['n_any_rung']:,} ({block['pct_any_rung_of_base']}%)")
        add(f"  {'rung':46s} {'%base':>7s} {'%marg':>7s} {'val/ref':>8s} "
            f"{'(n)':>6s}")
        for rung in RUNGS:
            row = block["rungs"].get(rung.key)
            if not row:
                continue
            ratio = row["provenance_ratio_to_reference"]
            p50 = ratio.get("p50")
            shown = ("   ref" if rung.is_reference
                     else f"{p50:.3f}" if p50 is not None else "     -")
            add(f"  {rung.key[:46]:46s} {_n(row['pct_of_base']):>7s} "
                f"{_n(row['marginal_pct_of_base']):>7s} {shown:>8s} "
                f"{ratio.get('n', 0):>6,}  {rung.bound}")
        add("")
    add("-" * width)
    add(f"RANK AGREEMENT AGAINST THE EXACT TOTAL  [{SCOPE_PRICED}]")
    add("-" * width)
    add(f"  {'rung':38s} {'n':>5s} {'pairs':>8s} {'rho':>7s} {'exact':>7s} "
        f"{'near':>7s} {'churn':>7s}")
    pooled = (payload.get("ranking") or {}).get("pooled") or {}
    for rung in RUNGS:
        block = pooled.get(rung.key) or {}
        if rung.is_reference:
            add(f"  {rung.key[:38]:38s} {'  (the reference: it ranks against itself)':>44s}")
            continue
        add(f"  {rung.key[:38]:38s} {block.get('n_paired_issuer_dates', 0):>5,} "
            f"{block.get('n_within_date_pairs', 0):>8,} "
            f"{_n(block.get('spearman_weighted'), 4):>7s} "
            f"{_n(block.get('exact_ordering_agreement'), 4):>7s} "
            f"{_n(block.get('near_ordering_agreement'), 4):>7s} "
            f"{_n(block.get('cheap_decile_churn'), 4):>7s}")
    add("")
    add(f"  near tolerance: pairs within {NEAR_TOLERANCE:.0%} of each other under "
        f"the reference are forgiven")
    rep = (payload.get("headline") or {}).get(
        "representativeness_of_the_ranking_sample") or {}
    mix = rep.get("division_mix_of_the_paired_sample") or {}
    if mix:
        top = "; ".join(f"{k.split(' ', 1)[0]} {v['share_pct']}%"
                        for k, v in list(mix.items())[:5])
        add(f"  sample mix (the exact rung needs ShortTermBorrowings): {top}")
        add(f"  direction of that bias: {rep.get('direction_of_the_bias')}")
    add("")
    add("-" * width)
    add("VERDICT AND PROPOSED Q  (debt dimension only)")
    add("-" * width)
    proposal = payload.get("quality_factor_proposal") or {}
    for rung in RUNGS:
        block = (proposal.get("rungs") or {}).get(rung.key)
        if not block:
            continue
        add(f"  {rung.key[:52]:52s}  Q={block['Q_debt']}")
        add(f"      {block['verdict']['verdict']}: {block['verdict']['why']}")
    add("")
    add("-" * width)
    add(f"SURVIVOR RETURN DIAGNOSTIC  [{SCOPE_RETURNS}]  MAY NOT PROMOTE A RUNG")
    add("-" * width)
    returns = payload.get("survivor_return_diagnostic") or {}
    add(f"  labels matched: {returns.get('n_labels_matched', 0):,} of "
        f"{returns.get('n_pairs_wanted', 0):,} issuer-dates, horizon "
        f"{returns.get('horizon')}")
    for rung in RUNGS:
        block = (returns.get("rungs") or {}).get(rung.key)
        if not block:
            continue
        into = block["pushed_INTO_the_cheap_decile_by_this_rung"]
        out_ = block["pushed_OUT_of_the_cheap_decile_by_this_rung"]
        add(f"  {rung.key[:40]:40s} in n={into.get('n', 0):<4} "
            f"med={_n(into.get('p50'), 4):>8s}   out n={out_.get('n', 0):<4} "
            f"med={_n(out_.get('p50'), 4):>8s}")
    add("")
    resources = payload.get("resources") or {}
    add("-" * width)
    add("RESOURCES")
    add("-" * width)
    add(f"  free space min observed {resources.get('free_gib_min_observed')} GiB "
        f"(floor {MIN_FREE_BYTES / 1024 ** 3:.0f} GiB)")
    add(f"  WAL peak observed {resources.get('wal_bytes_peak_observed')} bytes "
        f"(start {resources.get('wal_bytes_start')})")
    add(f"  peak working set {resources.get('peak_working_set_mib')} MiB")
    add("  PRAGMA wal_checkpoint was NOT run: this connection is mode=ro and "
        "cannot checkpoint.")
    return "\n".join(lines)


def _wrap(text: str, width: int) -> list[str]:
    """Greedy wrap. A report that needs a terminal wider than 80 columns is a
    report nobody reads on the machine that produced it."""
    words, lines, current = str(text).split(), [], ""
    for word in words:
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


def _n(value: Any, places: int = 2) -> str:
    if value is None:
        return "-"
    if isinstance(value, str):
        return value
    return f"{float(value):.{places}f}"


# ==========================================================================
# (11) Findings the run asserts about itself
# ==========================================================================

def check_findings(payload: Mapping[str, Any]) -> list[str]:
    """Internal consistency of one measured payload. Empty list means clean."""
    problems: list[str] = []
    if payload.get("rows_written_to_the_store") != 0:
        problems.append("this module wrote to the store")
    resources = payload.get("resources") or {}
    if resources.get("free_bytes_min_observed", 0) < MIN_FREE_BYTES:
        problems.append("free space fell below the floor during the run")
    if resources.get("peak_working_set_bytes") == 0:
        problems.append("peak working set reported as 0 rather than UNKNOWN")
    returns = payload.get("survivor_return_diagnostic") or {}
    if returns.get("may_promote_a_rung") is not False:
        problems.append("the survivor return block is not barred from promoting")
    if returns.get("sample_scope") != SCOPE_RETURNS:
        problems.append("the survivor return block is not labelled survivor-only")
    proposal = payload.get("quality_factor_proposal") or {}
    if proposal.get("combination_rule_with_peer_coherence", "").find("NOT PROPOSED") != 0:
        problems.append("a DataQuality/PeerCoherence combination rule was proposed")
    for key, block in (proposal.get("rungs") or {}).items():
        q = block.get("Q_debt")
        if q == UNKNOWN:
            continue
        if not isinstance(q, (int, float)) or not (Q_FLOOR <= float(q) <= 1.0):
            problems.append(f"{key}: Q_debt {q!r} outside [{Q_FLOOR}, 1.0]")
        if key == REFERENCE_RUNG_KEY and q != 1.0:
            problems.append("the reference rung's Q is not 1.0")
    ranking = payload.get("ranking") or {}
    if ranking.get("sample_scope") != SCOPE_PRICED:
        problems.append("the ranking block is not labelled survivor-priced")
    for day, block in (payload.get("coverage") or {}).items():
        if block.get("sample_scope") != SCOPE_FULL:
            problems.append(f"coverage at {day} is not labelled full-universe")
    return problems


# ==========================================================================
# (12) CLI
# ==========================================================================

def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    db_path = pit_store.DEFAULT_PIT_DB_PATH
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "pit_archive", "debt_quality")
    date_mode = "census"
    rank_date_mode = "grid"
    staleness = "v2"
    do_validate = True
    i = 0
    while i < len(argv):
        if argv[i] == "--db" and i + 1 < len(argv):
            db_path = argv[i + 1]; i += 2
        elif argv[i] == "--out" and i + 1 < len(argv):
            out_dir = argv[i + 1]; i += 2
        elif argv[i] == "--dates" and i + 1 < len(argv):
            date_mode = argv[i + 1]; i += 2
        elif argv[i] == "--rank-dates" and i + 1 < len(argv):
            rank_date_mode = argv[i + 1]; i += 2
        elif argv[i] == "--staleness" and i + 1 < len(argv):
            staleness = argv[i + 1]; i += 2
        elif argv[i] == "--no-validate":
            do_validate = False; i += 1
        else:
            i += 1

    status = PC.disk_status(db_path)
    print(f"disk: {status['free_bytes'] / 1024 ** 3:.2f} GiB free "
          f"({status['used_pct']}% used), WAL {status['wal_bytes']:,} bytes",
          flush=True)
    if status["free_bytes"] < MIN_FREE_BYTES:
        print(f"ABORT: under {MIN_FREE_BYTES / 1024 ** 3:.0f} GiB free", flush=True)
        return 2

    conn = PC.connect_ro(db_path)
    try:
        payload = measure(conn, db_path, date_mode=date_mode,
                          rank_date_mode=rank_date_mode, staleness=staleness)
    finally:
        conn.close()
    payload["headline"] = headline(payload)
    if do_validate:
        payload["validation"] = validate(payload)
    payload["findings"] = check_findings(payload)

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "debt_quality.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1, sort_keys=True, default=str)
    report = render(payload)
    with open(os.path.join(out_dir, "debt_quality.txt"), "w", encoding="utf-8") as handle:
        handle.write(report + "\n")
    print(report, flush=True)
    print(f"\nwrote {path} ({os.path.getsize(path):,} bytes)", flush=True)
    for line in payload["findings"]:
        print(f"  FINDING: {line}", flush=True)
    for check in payload.get("validation") or []:
        print(f"  validate: {check['check']}: published={check['published']} "
              f"measured={check['measured']}", flush=True)
    return 1 if payload["findings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
