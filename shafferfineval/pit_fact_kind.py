"""FLOW / STATE / MARKET -- the fact-kind taxonomy and `fact_kind_staleness_v2`.

A CANDIDATE-LINEAGE policy (`pit_factor_contract.CANDIDATE_MODEL_VERSION`,
`candidate_equity_shaffer_v2`). It is written BESIDE `pit_policy`'s v1
staleness constants and never edits them: `pit_policy.MAX_AGE_ANNUAL_MONTHS`,
`MAX_AGE_QUARTERLY_MONTHS`, `CONCEPT_MAX_AGE_MONTHS`, `max_age_months` and
`is_stale` are imported and read, and every v1 row ever stamped keeps meaning
exactly what it meant. Nothing here writes to the store.

==========================================================================
THE DEFECT THIS POLICY EXISTS TO REMOVE
==========================================================================

`pit_policy` gives every fact an age budget keyed on the length of the period
it covers: 15 months for an annual fact, 6 for a quarterly one, and -- for
`shares_outstanding` alone -- an override of 12 / 4. The override's reasoning
is sound for the failure it was written against (a 15-month-old cover count
means a missed filing, and a wrong share count mis-scales the whole market cap
rather than degrading one factor). Its CONSEQUENCE is not.

A 4-month bound on a fact refreshed on a QUARTERLY cadence, published with a
filing lag of 30-45 days, leaves a window that closes before the next
observation opens. A December-fiscal-year filer's Q3 balance-sheet count is
dated 30 September; under the quarterly override it is usable through 30
January and refused from 31 January; the 10-K carrying the next count is filed
in late February. Every December-FY filer
in the cross-section is therefore UNCOUNTABLE for most of January and much of
February, and again -- for the June-FY cohort -- around October. That is not a
fact about companies. It is a CALENDAR INSIDE THE VALUATION FACTOR, and a model
fitted on it learns the calendar: measured on the EV/EBITDA cohort census,
P(N>=3) is 40.6% in April, 19.1% in October and 14.5% in January
(`pit_factor_contract`, `seasonality_pct_ge_3`).

The census also located the leak precisely. Of 174,674 refused priced
entity-dates, 52.1% are `stale_beyond_max_age` and only 2.2% are the entire
share-class guard; and leave-one-out on the five-leaf EV/EBITDA conjunction
moves P(N>=3) from 34.2% to 61.6% when the defensible share count is the leaf
removed. The binding leaf is the share count, and the share count's problem is
staleness, not tagging.

==========================================================================
THE TAXONOMY
==========================================================================

The owner's insight, and the whole content of this module: SHARES OUTSTANDING
IS A STATE VARIABLE, NOT A FLOW. "Revenue from 15 months ago is stale. A share
count from five months ago may still be exactly the last known share count."

    FLOW    a quantity measured OVER a period: revenue, EBITDA's two
            components, net income, operating cash flow, capex, interest
            expense. A flow describes a window that has closed. Last year's
            revenue is not this year's revenue and never becomes it, so an old
            flow is GENUINELY WRONG about the question being asked, and the
            strict age bound is the correct instrument. v2 inherits v1's flow
            bounds unchanged -- see `FLOW_INHERITS_V1`.

    STATE   a level measured AT an instant: cash, total debt, equity, total
            assets, shares outstanding. A state fact is the issuer's last
            PUBLISHED position, and it remains the last published position
            until the issuer publishes another one. Five months after the
            balance-sheet date it is not a decayed number -- it is still
            literally the most recent thing the filer said. So a state fact
            PERSISTS UNTIL SUPERSEDED, subject to a sanity ceiling.

    MARKET  a price. There is a new one every session and the previous one is
            not evidence about today, so the requirement is the current
            session. This is the strictest rung and it is the one v2 does NOT
            currently enforce in production -- see `MARKET_RULE_VS_PRODUCTION`.

==========================================================================
THE POINT-IN-TIME PROOF -- why "persist until superseded" is not look-ahead
==========================================================================

This is the argument that separates this policy from a leak, and it rests on
one structural fact: V2 CHANGES ONLY THE AGE TEST APPLIED AFTER SELECTION. IT
DOES NOT CHANGE SELECTION AT ALL.

Selection is `pit_store.latest_period_as_of` (and its mirrors
`pit_shares._select_one`, `pit_sharecoverage.select_share_pit`). Its scope is

        period_end     <= as_of        the fact must be OF a date that happened
        available_date <= as_of        the filing carrying it must have landed

ordered by `period_end DESC, available_date DESC`. Both v1 and v2 hand this
selector the same arguments and receive the same row. v1 then asks "is that row
older than N months?"; v2 asks "is that row older than the sanity ceiling?".
Neither question can reach a row the selector did not return.

Now the supersession claim. Observation B supersedes observation A at `as_of`
if and only if B outranks A in that ORDER BY -- which requires B to be in
SCOPE, which requires `B.available_date <= as_of`. So:

    SUPERSEDING REQUIRES A NEW OBSERVATION WHOSE available_date <= as_of.
    A LATER-FILED OBSERVATION IS NOT MERELY "NOT CHOSEN" AT AN EARLIER AS-OF.
    IT IS NEVER ENUMERATED, NEVER COMPARED, AND NEVER SEEN.

Critically, v2 never asks the forbidden question. A rule of the form "keep
using A until B arrives" could be implemented by testing whether a successor
EXISTS -- and the existence of a future filing is future knowledge. v2 tests no
such thing. It asks only "what is the newest in-scope observation", which is
the question v1's selector already asked, and then applies a weaker age bound
to the answer. A policy that only RELAXES a post-selection filter cannot
introduce a look-ahead that the selector did not already have.

The concrete case, from `pit_shares`' docstring: Apple's instant 2019-09-28
carries 4,443,236,000 in four filings and 17,772,945,000 in a fifth filed
2020-10-30, after the 4:1 split. At a 2020-01-31 as-of the restatement's
available_date is nine months in the future, so it is out of scope under v1 and
out of scope under v2, identically. `check_supersession_is_not_look_ahead` in
`test_pit_fact_kind.py` is that case as an executable test, and it is the
difference between this policy and a leak.

==========================================================================
WHAT THIS POLICY DOES NOT FIX
==========================================================================

28.0% of the 174,674 refusals are `only_period_average_available`: the issuer's
only share-shaped tag is `WeightedAverageNumberOfDilutedSharesOutstanding`, a
DURATION AVERAGE built for earnings-per-share arithmetic. It is not a count on
a date at ANY staleness policy, because staleness is a question about WHEN a
measurement was taken and this is a defect in WHAT was measured. No age rule
reaches it. See `UNFIXED_BY_STALENESS`.

Stdlib only. Read-only on the store. No scoring, no replay, no writes.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_policy

__all__ = [
    "STALENESS_POLICY_V1", "STALENESS_POLICY_V2", "CANDIDATE_MODEL_VERSION",
    "FACT_KIND_FLOW", "FACT_KIND_STATE", "FACT_KIND_MARKET", "FACT_KINDS",
    "FactKindSpec", "FACT_KIND_BY_PRIMITIVE", "fact_kind", "kind_spec",
    "primitives_of_kind", "taxonomy_spec", "validate_against_pit_derive",
    "STATE_SANITY_CEILING_MONTHS", "MARKET_MAX_AGE_SESSIONS",
    "FLOW_INHERITS_V1", "MARKET_RULE_VS_PRODUCTION", "UNFIXED_BY_STALENESS",
    "MEASURED",
    "max_age_months_v2", "is_stale_v2", "staleness_decision",
    "staleness_policy_v2", "REASON_STALE_FLOW", "REASON_BEYOND_STATE_CEILING",
    "REASON_STALE_MARKET", "supersedes",
]

#: The policy this module is a candidate successor to. NAMED here, not defined
#: here: the behaviour is `pit_policy.is_stale` and it is unchanged.
STALENESS_POLICY_V1 = "staleness_policy_v1_fixed_month_bounds"

#: The policy this module defines. A row produced under it must carry this id.
STALENESS_POLICY_V2 = "fact_kind_staleness_v2"

#: The lineage this candidate belongs to. Restated rather than imported so that
#: importing this module cannot drag in the factor-contract graph; the test
#: asserts the two agree.
CANDIDATE_MODEL_VERSION = "candidate_equity_shaffer_v2"


# ==========================================================================
# (1) THE TAXONOMY
# ==========================================================================

FACT_KIND_FLOW = "flow"
FACT_KIND_STATE = "state"
FACT_KIND_MARKET = "market"
FACT_KINDS = (FACT_KIND_FLOW, FACT_KIND_STATE, FACT_KIND_MARKET)


@dataclass(frozen=True)
class FactKindSpec:
    """One primitive's kind, and the one-line argument for the assignment.

    `why` is required and is not decoration. The assignment is what decides
    whether a fact may persist for two years or expires in four months, so an
    unargued assignment is an unargued staleness bound.

    `obtainable` is False for the four `pit_derive` GENUINELY_UNAVAILABLE
    primitives. Their kind is recorded because the taxonomy must be total over
    the graph, and it is marked moot because no source supplies them: an age
    rule for a fact that has no observations is a rule about nothing.
    """

    key: str
    kind: str
    why: str
    obtainable: bool = True
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        out = {"key": self.key, "fact_kind": self.kind, "why": self.why,
               "obtainable": self.obtainable}
        if self.note:
            out["note"] = self.note
        return out


def _k(key: str, kind: str, why: str, **kw: Any) -> FactKindSpec:
    return FactKindSpec(key=key, kind=kind, why=why, **kw)


#: EVERY primitive registered in `pit_derive`, with its kind and its argument.
#: `validate_against_pit_derive()` fails loudly if the two ever drift apart --
#: a primitive with no kind would silently fall back to v1's bound, which is
#: the defect this module exists to remove.
FACT_KIND_BY_PRIMITIVE: dict[str, FactKindSpec] = {
    spec.key: spec for spec in (
        # -- FLOW: measured over a window that has closed ------------------
        _k("revenue", FACT_KIND_FLOW,
           "Sales BOOKED IN A PERIOD; last year's figure is a different "
           "question's answer, never a decayed version of this year's."),
        _k("operating_income", FACT_KIND_FLOW,
           "Earnings EARNED IN A PERIOD, and the numerator of a margin whose "
           "denominator is a same-period flow; mismatching the windows is the "
           "error, not the age as such."),
        _k("depreciation_amortisation", FACT_KIND_FLOW,
           "A charge AGAINST a period's income, and EBITDA's scarcer half; it "
           "must share period_end and qtrs with operating income, which a "
           "persistence rule would quietly break."),
        _k("net_income", FACT_KIND_FLOW,
           "What the period left the owners. A stale one is the P/E numerator "
           "of a year that has ended, which is wrong rather than old."),
        _k("operating_cash_flow", FACT_KIND_FLOW,
           "Cash PRODUCED OVER a period; carrying it forward would restate a "
           "closed cash cycle as the current one."),
        _k("capex", FACT_KIND_FLOW,
           "Cash SPENT OVER a period, and free cash flow subtracts it from a "
           "same-period flow; both halves must age together."),
        _k("interest_expense", FACT_KIND_FLOW,
           "The cost of debt CHARGED IN a period. Interest coverage divides a "
           "period's operating income by it, so it inherits the flow rule."),
        # -- STATE: a level that stands until the filer publishes another ---
        _k("cash", FACT_KIND_STATE,
           "A balance ON the balance-sheet date. Five months later it is still "
           "the last balance the issuer published, which is exactly what net "
           "debt and enterprise value ask for."),
        _k("total_debt", FACT_KIND_STATE,
           "Debt OUTSTANDING at an instant. It changes at issuance and "
           "maturity, not continuously, and between filings the last reported "
           "balance is the only balance there is."),
        _k("equity", FACT_KIND_STATE,
           "Book value AT an instant; the ROE denominator is a level, and a "
           "level has no window to fall out of."),
        _k("total_assets", FACT_KIND_STATE,
           "The balance sheet's size AT an instant, and the base universe's "
           "own definition -- a level, superseded only by the next one filed."),
        _k("shares_outstanding", FACT_KIND_STATE,
           "THE CASE THIS MODULE WAS WRITTEN FOR. A count of claims existing "
           "on a date; it moves at issuance, buyback and split, all of which "
           "are EVENTS, and between them the last filed count is still the "
           "count. The 4-month override treats it as a flow and manufactures a "
           "January/October hole."),
        _k("sic", FACT_KIND_STATE,
           "An industry CLASSIFICATION, which persists by construction: "
           "pit_entity_sic already stores it as intervals and "
           "pit_store.sic_as_of already reads it as persist-until-superseded. "
           "v2 names what that code already does.",
           note="Already state-shaped in the store; listed for totality."),
        _k("cpi", FACT_KIND_STATE,
           "An index LEVEL for a month that has been published. pit_macro_obs "
           "selects it by vintage -- persist-until-superseded with an explicit "
           "revision trail -- so the macro path already implements this rung.",
           note=("The DERIVED node `inflation` is a flow built from two CPI "
                 "levels; the level is the state fact, the change is not.")),
        # -- MARKET: a new observation every session ------------------------
        _k("price_raw", FACT_KIND_MARKET,
           "The as-printed close of ONE session. Yesterday's close is not "
           "evidence about today's value; there is a new observation every "
           "session and the requirement is the current one."),
        _k("price_adjusted", FACT_KIND_MARKET,
           "price_raw times sourced adjustment factors: same session, same "
           "rule. The factors themselves are state facts of the listing, but "
           "the quantity is a market observation."),
        _k("treasury_yield", FACT_KIND_MARKET,
           "A daily constant-maturity quote. Market-observed, dated to a "
           "session, and superseded every session it trades.",
           note=("No equity node in the pit_derive graph consumes it today and "
                 "its coverage is unsurveyed; the kind is assigned, the "
                 "coverage is not claimed.")),
        # -- the four GENUINELY_UNAVAILABLE primitives ----------------------
        _k("price_dead_company", FACT_KIND_MARKET,
           "A price, so market-kind; moot, because for the specific (listing, "
           "window) in question no vendor carries a bar at all.",
           obtainable=False),
        _k("option_quote_pre_archive", FACT_KIND_MARKET,
           "A quote is a market observation; moot before 2026-09-20, when the "
           "archive begins, because a snapshot nobody recorded cannot be aged.",
           obtainable=False),
        _k("analyst_gpi_historical", FACT_KIND_STATE,
           "A standing assessment rather than a period measurement, so "
           "state-SHAPED; moot, and deliberately so -- it was never generated, "
           "and back-filling it would date an opinion with hindsight.",
           obtainable=False),
        _k("never_tagged_fact", FACT_KIND_STATE,
           "Kind-neutral by construction: it is the ABSENCE of whatever fact "
           "was asked for. Recorded as state only so the mapping is total; no "
           "age rule applies to a row that does not exist.",
           obtainable=False,
           note=("pit_policy.absence_limits(): 159,522 empty-value rows were "
                 "counted and discarded at ingest, so never_tagged, "
                 "tagged_zero and company_had_none are one absence today.")),
    )
}


def fact_kind(primitive: str) -> str:
    """The kind of one primitive. Raises on an unknown key rather than guessing.

    Guessing is the failure mode worth refusing: an unmapped primitive silently
    defaulting to `state` would grant a two-year persistence to a flow.
    """
    try:
        return FACT_KIND_BY_PRIMITIVE[primitive].kind
    except KeyError:
        raise ValueError(
            f"no fact_kind for primitive {primitive!r}; known: "
            f"{', '.join(sorted(FACT_KIND_BY_PRIMITIVE))}") from None


def kind_spec(primitive: str) -> FactKindSpec:
    """The whole assignment record, argument included."""
    try:
        return FACT_KIND_BY_PRIMITIVE[primitive]
    except KeyError:
        raise ValueError(f"no fact_kind for primitive {primitive!r}") from None


def primitives_of_kind(kind: str) -> tuple[str, ...]:
    """Every primitive of one kind, in a stable order."""
    if kind not in FACT_KINDS:
        raise ValueError(f"unknown fact kind {kind!r}; known: {FACT_KINDS}")
    return tuple(sorted(k for k, v in FACT_KIND_BY_PRIMITIVE.items()
                        if v.kind == kind))


def validate_against_pit_derive() -> list[str]:
    """Every `pit_derive` primitive has a kind, and no kind is an orphan.

    Imported lazily and tolerantly: this module must stay usable on a bare
    interpreter, and a census run should not fail because the derivation graph
    moved. A missing graph is reported as a skip, never as a pass.
    """
    try:
        import pit_derive
    except Exception as exc:                          # pragma: no cover
        return [f"SKIP: pit_derive unavailable ({exc})"]
    problems: list[str] = []
    graph = {k for k, n in pit_derive.NODES.items()
             if n.kind in (pit_derive.KIND_SOURCE, pit_derive.KIND_UNAVAILABLE)}
    for key in sorted(graph - set(FACT_KIND_BY_PRIMITIVE)):
        problems.append(f"pit_derive primitive {key!r} has no fact_kind")
    for key in sorted(set(FACT_KIND_BY_PRIMITIVE) - graph):
        problems.append(f"fact_kind {key!r} is not a pit_derive primitive")
    for key, spec in sorted(FACT_KIND_BY_PRIMITIVE.items()):
        if spec.kind not in FACT_KINDS:
            problems.append(f"{key!r} has unknown kind {spec.kind!r}")
        if not spec.why.strip():
            problems.append(f"{key!r} has no argument for its assignment")
        node = pit_derive.NODES.get(key)
        if node is not None:
            expect = node.kind != pit_derive.KIND_UNAVAILABLE
            if spec.obtainable != expect:
                problems.append(
                    f"{key!r} obtainable={spec.obtainable} but pit_derive says "
                    f"{node.kind}")
    return problems


# ==========================================================================
# (2) THE V2 STALENESS RULE
# ==========================================================================

#: FLOW facts keep v1's bounds EXACTLY: 15 months annual, 6 months quarterly.
#:
#: Deliberate, and the reason is measurement hygiene rather than laziness. The
#: taxonomy's claim about flows is that they may NOT be granted the state rule,
#: not that their numbers are wrong. Nothing measured says 15/6 is the wrong
#: pair -- 15 months is one annual cycle plus a filing lag, 6 months is two
#: missed 10-Qs -- and moving them would change coverage for a reason no
#: evidence supports while making the before/after comparison below unreadable:
#: two changes, one number. If a later measurement argues a flow bound, it is a
#: v3, and it will be able to point at this run as its baseline.
FLOW_INHERITS_V1 = True

#: THE SANITY CEILING ON STATE FACTS, in months from `period_end`.
#:
#: "Persists until superseded" must not mean a 2011 share count answering a
#: 2024 query for a company that stopped filing. The ceiling is what stops it,
#: and 24 months is chosen for three reasons:
#:
#: 1. CADENCE. A state fact is refreshed with every periodic filing. 24 months
#:    is EIGHT consecutive missed quarterly refreshes and TWO missed annual
#:    ones. A filer that has published no balance sheet in two years has not
#:    slowed down; it has stopped, and the correct answer is unavailable.
#: 2. IT CLEARS THE ARTEFACT BY A WIDE MARGIN. The hole this policy closes is
#:    at most one quarterly cadence plus a filing lag -- roughly 4 to 7 months
#:    of age. Any ceiling above ~9 months removes it. 24 gives the delinquent-
#:    but-alive filer (a late 10-K, a restatement pause, an auditor change)
#:    room that a 12-month ceiling would not, without approaching the multi-year
#:    case the ceiling exists to refuse.
#: 3. IT IS MEASURED, NOT ASSERTED. `measure_dates()` reports how often it
#:    binds: the share of priced entity-dates whose newest in-scope count is
#:    older than 24 months, and the age distribution around it.
#:
#: The ceiling is an AGE test on the fact, deliberately NOT an entity-level
#: "is this issuer still filing" test. The two answer different questions and
#: conflating them would hide the second: `is_reporting_at` (400 days of ANY
#: share-shaped fact) is measured alongside as a complement, never folded in.
STATE_SANITY_CEILING_MONTHS = 24

#: MARKET facts: the current session, full stop. Zero sessions of slack.
MARKET_MAX_AGE_SESSIONS = 0

#: The market rung is the one place v2 is STRICTER than what runs today, and
#: saying so is the point of recording it.
MARKET_RULE_VS_PRODUCTION = {
    "v2_rule": "current session; MARKET_MAX_AGE_SESSIONS = 0",
    "production_today": (
        "pit_identity.scored_universe_as_of admits an entity on a real bar "
        "with volume > 0 within DEFAULT_PRICE_MAX_AGE_DAYS (10 calendar days) "
        "of the as-of date, so a halted line can be priced from a close up to "
        "ten days old."),
    "direction": ("v2's market rule would SHRINK the priced universe, not grow "
                  "it. It is declared here and NOT enforced by this module; "
                  "enforcing it is a separate decision with its own "
                  "measurement, and `measure_dates()` reports the gap (the "
                  "share of priced entities whose newest bar is the as-of "
                  "session itself) rather than assuming it is small."),
    "why_not_folded_in": (
        "This run measures ONE change. Tightening the price rule in the same "
        "pass would make the share-policy before/after uninterpretable."),
}

#: What no staleness policy can repair. Carried as data so it travels with the
#: coverage numbers it qualifies.
UNFIXED_BY_STALENESS = {
    "reason": "only_period_average_available",
    "share_of_refusals": 0.280,
    "measured_on": "174,674 refused priced entity-dates (cohort census, 2026-09-21)",
    "why_no_age_rule_reaches_it": (
        "WeightedAverageNumberOfDilutedSharesOutstanding is a DURATION AVERAGE "
        "over a reporting window, built for per-share arithmetic. Staleness "
        "asks WHEN a measurement was taken; this is a defect in WHAT was "
        "measured. A fresh period average is still not a point-in-time count, "
        "so relaxing, tightening or re-kinding the age bound moves it not at "
        "all. It is a FETCH problem (the dei cover-page count, which DERA's "
        "compact num.txt drops: 606 rows in the whole archive against 589,989 "
        "balance-sheet counts) or a DIMENSIONAL problem (per-class tagging, "
        "which pit_fact correctly discards)."),
    "second_thing_not_fixed": (
        "never_filed_a_share_count. An issuer that tagged nothing share-shaped "
        "has no observation to persist, and persistence of nothing is nothing."),
}

#: EVERYTHING BELOW WAS MEASURED ON 2026-09-21 AGAINST THE LOADED STORE by
#: `python pit_fact_kind.py --grid` and `python pit_cohort_staleness.py`, over
#: the same 165-date grid, the same 39,038 peer sets and the same real
#: functions the published census used. It is kept as data rather than left in
#: a transcript, and an unmeasured quantity is None -- never 0.
#:
#: THE BASELINE REPRODUCES EXACTLY, which is why the deltas can be believed:
#: 174,674 refused priced entity-dates, 52.1% stale_beyond_max_age, 28.0%
#: only_period_average_available; EV/EBITDA median 1, P(N>=3) 34.2%,
#: P(N>=12) 7.0%; EBITDA-only median 13, P(N>=3) 95.2%, P(N>=12) 56.0%;
#: and January 14.46% / April 40.57% / October 19.15% against the published
#: 14.5 / 40.6 / 19.1. Same numbers, to the decimal.
MEASURED: dict[str, Any] = {
    "measured_on": "2026-09-21",
    "sample_scope": "SURVIVOR_ONLY_DIAGNOSTIC (pit_listing: 2,576 lines, all alive in 2026)",
    "grid": "165 month-end as-of dates, 2013-01-31 .. 2026-09-18",
    "ceiling_months": 24,
    "share_count_coverage": {
        "basis": "priced entity-dates (pit_identity.scored_universe_as_of), "
                 "defensible = pit_rawprice.class_decision under the strict policy",
        "pooled": {"priced_entity_dates": 377304,
                   "v1_defensible": 202630, "v1_pct": 53.70,
                   "v2_defensible": 272587, "v2_pct": 72.25,
                   "gain_entity_dates": 69957, "gain_points": 18.54,
                   "ratio": 1.345},
        "at_census_dates": {
            "2015-06-30": {"priced": 2034, "v1": 1185, "v1_pct": 58.26,
                           "v2": 1432, "v2_pct": 70.40, "points": 12.14},
            "2019-06-28": {"priced": 2423, "v1": 1528, "v1_pct": 63.06,
                           "v2": 1759, "v2_pct": 72.60, "points": 9.54},
            "2024-06-28": {"priced": 2443, "v1": 1566, "v1_pct": 64.10,
                           "v2": 1802, "v2_pct": 73.76, "points": 9.66},
        },
        "why_the_june_dates_understate_it": (
            "All three census dates are JUNE, the middle of the reporting cycle "
            "and nowhere near the hole. The pooled figure is 2x their gain "
            "because January and October are where the artefact lives."),
    },
    "seasonality": {
        "metric": "pct of priced entity-dates with a defensible count, pooled by month",
        "v1": {"january": 23.02, "february": 39.28, "march": 60.88,
               "april": 67.19, "may": 61.96, "june": 61.47, "july": 63.49,
               "august": 55.30, "september": 58.10, "october": 27.47,
               "november": 61.97, "december": 63.48,
               "range_points": 44.17, "cv": 0.2681},
        "v2": {"january": 71.75, "february": 72.06, "march": 72.33,
               "april": 72.19, "may": 72.41, "june": 72.37, "july": 72.26,
               "august": 72.38, "september": 72.42, "october": 72.14,
               "november": 72.31, "december": 72.33,
               "range_points": 0.67, "cv": 0.0025},
        "verdict": ("THE CALENDAR IS GONE. The month-to-month range falls from "
                    "44.17 points to 0.67 -- a ratio of 0.015 -- and the "
                    "coefficient of variation from 0.268 to 0.0025. January "
                    "rises from 23.02% to 71.75% and October from 27.47% to "
                    "72.14%, while April, the best month, rises only 67.19% to "
                    "72.19%. The diagnosis was right: the hole was the bound, "
                    "not the companies."),
    },
    "refusals": {
        "v1_total": 174674,
        "v2_total": 104717,
        "v1": {"stale_beyond_max_age": 91051, "only_period_average_available": 48956,
               "never_filed_a_share_count": 17279, "not_yet_filed": 13201,
               "share_count_implausible_vs_weighted_average": 2349,
               "share_class_ambiguous": 1251, "share_class_uncorroborated": 410,
               "zero_share_count": 177},
        "v2": {"only_period_average_available": 48956,
               "state_beyond_sanity_ceiling": 18328,
               "never_filed_a_share_count": 17279, "not_yet_filed": 13201,
               "share_count_implausible_vs_weighted_average": 3428,
               "share_class_ambiguous": 2231, "zero_share_count": 705,
               "share_class_uncorroborated": 589},
        "reading": (
            "Refusals fall 40.0%. 72,723 of the 91,051 staleness refusals "
            "(79.9%) were the calendar artefact and are gone; 18,328 survive as "
            "the ceiling doing its job. The guard refuses MORE in absolute terms "
            "(4,187 -> 6,953) because v2 hands it 72,723 more counts to judge -- "
            "that is the price and it is stated, not netted away. And "
            "only_period_average_available does not move by one row: unchanged "
            "at 48,956, it goes from 28.0% of refusals to 46.8% and becomes the "
            "LARGEST single refusal. Fixing staleness promotes the problem "
            "staleness cannot fix."),
    },
    "ceiling": {
        "binds_entity_dates": 18328,
        "pct_of_priced_entity_dates": 4.86,
        "pct_of_those_with_any_count": 6.15,
        "sensitivity_entity_dates_admitted": {
            "v1_quarterly_4m": 206817, "6m": 249624, "9m": 259027,
            "12m": 266188, "18m": 272308, "24m": 274439, "36m": 277994,
            "48m": 281069, "unbounded": 297868},
        "argument": (
            "The curve is STEEP where the artefact is and FLAT where the "
            "ceiling sits. 4 -> 12 months buys 59,371 entity-dates; 12 -> 24 "
            "buys 8,251 and 24 -> 36 buys 3,555. So the ceiling is not a "
            "coverage lever, which is exactly what a sanity bound should be: it "
            "costs almost nothing to set it where a delinquent-but-alive filer "
            "survives, and removing it entirely would admit a further 23,429 "
            "entity-dates whose newest count is over two years old. The tail is "
            "real and it is long: at 2024-06-28 the age of the newest in-scope "
            "count has p50 89 days, p90 545, p95 3,102 (8.5 years) and a "
            "maximum of 5,354 days (14.7 years). That p95 IS the '2011 count "
            "answering a 2024 query' the owner named, and 24 months refuses it."),
    },
    "market_rung": {
        "pct_priced_listings_with_a_bar_on_the_as_of_session": 100.0,
        "at": ["2015-06-30", "2019-06-28", "2024-06-28"],
        "reading": ("v2's current-session MARKET rule would cost NOTHING at "
                    "these dates: every priced listing has a bar on the as-of "
                    "session itself, so the 10-day reach-back production allows "
                    "is never exercised at a month-end grid date. Measured at "
                    "three dates only; the other 162 are UNMEASURED."),
    },
    "ev_ebitda_cohort": {
        "method": ("pit_cohort_measure re-run over the same 39,038 peer sets and "
                   "165 dates with ONE input substituted. Not an estimate."),
        "baseline_reproduced": {"median": 1, "pct_ge_3": 34.19, "pct_ge_12": 7.01},
        "v2_shares_only": {"median": 2, "pct_ge_3": 42.85, "pct_ge_12": 9.14,
                           "points_ge_3": 8.66, "ratio_ge_3": 1.253,
                           "points_ge_12": 2.13, "ratio_ge_12": 1.304},
        "v2_fundamentals_only": {"median": 2, "pct_ge_3": 36.31, "pct_ge_12": 7.87},
        "v2_whole_policy": {"median": 2, "pct_ge_3": 46.01, "pct_ge_12": 10.34,
                            "points_ge_3": 11.82, "ratio_ge_3": 1.346,
                            "points_ge_12": 3.33, "ratio_ge_12": 1.475},
        "share_of_the_available_room": 0.316,
        "room_definition": ("leave-one-out put P(N>=3) at 61.6% with the share "
                            "leaf REMOVED entirely, against 34.2% with it. v2 "
                            "takes 8.66 of those 27.41 points -- 31.6% of the "
                            "room -- which is what a staleness fix can take. The "
                            "rest is issuers with no usable count at any age."),
        "cohort_seasonality_pct_ge_3": {
            "v1_shares_v1_fundamentals": {"january": 14.46, "april": 40.57,
                                          "october": 19.15, "range": 26.11},
            "v1_shares_v2_fundamentals": {"january": 15.57, "april": 42.97,
                                          "october": 20.45, "range": 27.40},
            "v2_shares_v1_fundamentals": {"january": 41.48, "april": 42.91,
                                          "october": 42.20, "range": 2.72},
            "v2_shares_v2_fundamentals": {"january": 44.72, "april": 45.68,
                                          "october": 45.74, "range": 1.97},
        },
        "the_control": (
            "v1 shares + v2 fundamentals is the control and it settles the "
            "attribution. Re-kinding cash and total_debt as STATE lifts the "
            "LEVEL (34.19% -> 36.31%) and leaves the SEASONALITY exactly where "
            "it was (range 26.11 -> 27.40 points, January still 15.57%). The "
            "calendar artefact is the SHARE LEAF and nothing else, which is "
            "what the leave-one-out predicted."),
        "what_does_not_change": (
            "The median peer set still holds 2 valid members against a "
            "MIN_EBITDA_COHORT of 3 and a 50-75 band requirement of 12. v2 is a "
            "large relative gain on a small absolute base, and it does not make "
            "EV/EBITDA a broad benchmark -- which is the owner's decision 3 "
            "('only where a meaningful peer cohort actually exists'), now "
            "supported by a number rather than by an impression."),
        "n_priceshares_leaf": {"v1": {"median": 4, "pct_ge_3": 69.7, "pct_ge_12": 19.5},
                               "v2": {"median": 6, "pct_ge_3": 84.0, "pct_ge_12": 24.8}},
        "n_ebitda_leaf_unchanged": {"median": 13, "pct_ge_3": 95.2, "pct_ge_12": 56.0,
                                    "why": "EBITDA's components are FLOW facts; v2 "
                                           "changes nothing about them, and the "
                                           "four runs agree to the decimal."},
    },
    "resources": {
        "census_elapsed_seconds": 384.3,
        "census_wal_peak_bytes": 8911592,
        "mask_scan_v1_seconds": 83.3,
        "mask_scan_v2_seconds": 52.2,
        "cohort_census_seconds": 47.7,
        "min_free_disk_gib_observed": 10.03,
        "db_bytes": 8965951488,
        "rows_written_to_the_store": 0,
        "note": "read-only throughout; pit_feature and pit_score stay at 0 rows",
    },
}

REASON_STALE_FLOW = "stale_flow_beyond_max_age"
REASON_BEYOND_STATE_CEILING = "state_beyond_sanity_ceiling"
REASON_STALE_MARKET = "market_not_current_session"


def max_age_months_v2(primitive: str, qtrs: int) -> Optional[int]:
    """The v2 age budget in months, or None for "no month bound applies".

    None is returned for MARKET facts, whose bound is counted in SESSIONS and
    not in months. It is never returned for a flow or a state fact: both have a
    month bound, and the state one is the ceiling rather than the cadence.
    """
    kind = fact_kind(primitive)
    if kind == FACT_KIND_MARKET:
        return None
    if kind == FACT_KIND_STATE:
        return STATE_SANITY_CEILING_MONTHS
    # FLOW -- v1's bound, read from v1 rather than restated. Passing the
    # primitive as the concept keeps any v1 per-concept override in force;
    # today the only override is shares_outstanding, which is a STATE fact and
    # therefore never reaches this branch.
    return pit_policy.max_age_months(qtrs, primitive)


def supersedes(candidate_available_date: Any, candidate_period_end: Any,
               incumbent_period_end: Any, as_of: Any) -> bool:
    """Does `candidate` supersede `incumbent` AT `as_of`? The PIT gate itself.

    THE ONE FUNCTION THE LOOK-AHEAD ARGUMENT TURNS ON. A candidate supersedes
    an incumbent only if BOTH of the selector's bounds hold for it --

        candidate_period_end     <= as_of
        candidate_available_date <= as_of

    -- and only then is its period end compared with the incumbent's. The
    availability test comes FIRST and is unconditional, which is exactly why
    "persist until superseded" never consults the future: a filing that has not
    landed cannot displace anything, and its mere existence is never asked
    about. Apple's 17,772,945,000 restatement of the 2019-09-28 instant, filed
    2020-10-30, returns False here for every as_of before 2020-10-30 and True
    after it.
    """
    day = _day(as_of)
    avail = _day(candidate_available_date)
    cand_end = _day(candidate_period_end)
    if day is None or avail is None or cand_end is None:
        return False
    if avail > day or cand_end > day:
        return False                       # NOT YET KNOWABLE -- the whole proof
    inc_end = _day(incumbent_period_end)
    if inc_end is None:
        return True
    return cand_end > inc_end


def is_stale_v2(primitive: str, period_end: Any, as_of: Any, qtrs: int = 0, *,
                sessions_since: Optional[int] = None) -> bool:
    """Whether a SELECTED fact is too old to back a feature on `as_of` under v2.

    Applied AFTER selection, to a row the point-in-time selector already
    returned. It does not select, does not look for successors, and cannot see
    a row the selector did not hand it -- which is the whole of the PIT proof
    in the module docstring.

        FLOW    identical to `pit_policy.is_stale`: v1's month arithmetic,
                v1's bounds, v1's inclusive boundary.
        STATE   usable while the fact is the newest in-scope observation AND is
                within STATE_SANITY_CEILING_MONTHS of its period end.
        MARKET  usable only on the observation's own session. `sessions_since`
                is the caller's session count; with none supplied the test
                falls back to same-DAY equality and says so by being strict.

    An unparseable period end is stale, matching v1: unusable data is never
    resolved in favour of the feature.
    """
    kind = fact_kind(primitive)
    end = _day(period_end)
    day = _day(as_of)
    if end is None or day is None:
        return True
    if kind == FACT_KIND_MARKET:
        if sessions_since is None:
            return end != day
        return int(sessions_since) > MARKET_MAX_AGE_SESSIONS
    if kind == FACT_KIND_FLOW:
        # Delegated, not reimplemented. A second copy of the month arithmetic
        # is a second opinion waiting to diverge from v1 by a day.
        return pit_policy.is_stale(end, day, qtrs, primitive)
    return day > _add_months(end, STATE_SANITY_CEILING_MONTHS)


def staleness_decision(primitive: str, period_end: Any, as_of: Any,
                       qtrs: int = 0, *,
                       sessions_since: Optional[int] = None) -> dict[str, Any]:
    """`is_stale_v2` with its reasoning, and with v1's verdict beside it.

    Both verdicts in one record on purpose: every claim this candidate makes is
    a DIFFERENCE from v1, and a record that carried only the new answer would
    make the difference unauditable.
    """
    kind = fact_kind(primitive)
    end = _day(period_end)
    day = _day(as_of)
    v1 = pit_policy.is_stale(period_end, as_of, qtrs, primitive)
    v2 = is_stale_v2(primitive, period_end, as_of, qtrs,
                     sessions_since=sessions_since)
    reason = None
    if v2:
        reason = {FACT_KIND_FLOW: REASON_STALE_FLOW,
                  FACT_KIND_STATE: REASON_BEYOND_STATE_CEILING,
                  FACT_KIND_MARKET: REASON_STALE_MARKET}[kind]
    return {
        "primitive": primitive,
        "fact_kind": kind,
        "period_end": end,
        "as_of": day,
        "qtrs": int(qtrs),
        "age_days": _days_between(end, day) if (end and day) else None,
        "stale_v1": bool(v1),
        "stale_v2": bool(v2),
        "changed": bool(v1) != bool(v2),
        "reason_v2": reason,
        "max_age_months_v1": pit_policy.max_age_months(qtrs, primitive),
        "max_age_months_v2": max_age_months_v2(primitive, qtrs),
        "staleness_policy_version": STALENESS_POLICY_V2,
        "supersedes_v1": STALENESS_POLICY_V1,
    }


def staleness_policy_v2() -> dict[str, Any]:
    """The whole policy as a serialisable record, for a run to store.

    Deliberately shaped like `pit_policy.staleness_policy()` so the two can sit
    side by side in one bundle, and deliberately NOT produced by mutating it:
    v1's record must keep serialising exactly what it always did.
    """
    return {
        "staleness_policy_version": STALENESS_POLICY_V2,
        "supersedes": STALENESS_POLICY_V1,
        "lineage": CANDIDATE_MODEL_VERSION,
        "taxonomy": {
            "flow": {
                "primitives": list(primitives_of_kind(FACT_KIND_FLOW)),
                "rule": ("v1 unchanged: stale strictly after period_end + "
                         f"{pit_policy.MAX_AGE_ANNUAL_MONTHS} months (qtrs >= 4) "
                         f"or + {pit_policy.MAX_AGE_QUARTERLY_MONTHS} months "
                         "(qtrs 0..3), boundary inclusive"),
                "why": ("A closed window's number is not a decayed version of "
                        "the open window's number; it is a different question's "
                        "answer, so age is the right instrument."),
                "inherits_v1": FLOW_INHERITS_V1,
            },
            "state": {
                "primitives": list(primitives_of_kind(FACT_KIND_STATE)),
                "rule": ("persists until superseded, and superseding requires "
                         "a new observation whose available_date <= as_of; "
                         "refused only beyond the sanity ceiling of "
                         f"{STATE_SANITY_CEILING_MONTHS} months from period_end"),
                "why": ("A level is the issuer's last published position and "
                        "remains it until another is published. A 4-month bound "
                        "on a quarterly-cadence fact with a filing lag expires "
                        "before the successor exists, which is a calendar, not "
                        "a measurement."),
                "sanity_ceiling_months": STATE_SANITY_CEILING_MONTHS,
                "ceiling_why": ("eight missed quarterly refreshes or two missed "
                                "annual ones; clears the 4-7 month artefact by "
                                "a wide margin without licensing a multi-year "
                                "count for a filer that has stopped"),
            },
            "market": {
                "primitives": list(primitives_of_kind(FACT_KIND_MARKET)),
                "rule": f"current session; max age {MARKET_MAX_AGE_SESSIONS} sessions",
                "why": "there is a new observation every session and the previous one is not evidence about today",
                "vs_production": MARKET_RULE_VS_PRODUCTION,
            },
        },
        "assignments": [FACT_KIND_BY_PRIMITIVE[k].as_dict()
                        for k in sorted(FACT_KIND_BY_PRIMITIVE)],
        "point_in_time_proof": {
            "claim": "persist-until-superseded is PIT-safe",
            "argument": (
                "v2 changes ONLY the age test applied after selection. "
                "Selection is unchanged (period_end <= as_of AND "
                "available_date <= as_of, ordered period_end DESC, "
                "available_date DESC), so v2 is handed the same row v1 was "
                "handed. Superseding requires outranking in that ORDER BY, "
                "which requires being IN SCOPE, which requires "
                "available_date <= as_of. A later-filed observation is "
                "therefore never enumerated and never compared at an earlier "
                "as-of. v2 never asks whether a successor exists -- that "
                "question would be future knowledge -- it asks only what the "
                "newest in-scope observation is, and then applies a weaker "
                "bound to it. A policy that only relaxes a post-selection "
                "filter cannot introduce a look-ahead the selector did not "
                "already have."),
            "witness": ("Apple instant 2019-09-28: 4,443,236,000 in four "
                        "filings, 17,772,945,000 in a fifth filed 2020-10-30 "
                        "after the 4:1 split. At a 2020-01-31 as-of the "
                        "restatement is out of scope under v1 and under v2, "
                        "identically."),
            "executable_test": "test_pit_fact_kind.check_supersession_is_not_look_ahead",
        },
        "v1_untouched": {
            "module": "pit_policy",
            "constants": {
                "MAX_AGE_ANNUAL_MONTHS": pit_policy.MAX_AGE_ANNUAL_MONTHS,
                "MAX_AGE_QUARTERLY_MONTHS": pit_policy.MAX_AGE_QUARTERLY_MONTHS,
                "CONCEPT_MAX_AGE_MONTHS": {
                    k: dict(v) for k, v in pit_policy.CONCEPT_MAX_AGE_MONTHS.items()},
            },
            "note": ("read, never written. Every row already stamped "
                     f"{STALENESS_POLICY_V1} keeps meaning what it meant."),
        },
        "does_not_fix": UNFIXED_BY_STALENESS,
        "measured": json.loads(json.dumps(MEASURED)),
    }


def taxonomy_spec() -> dict[str, Any]:
    """The taxonomy alone, for a reader who wants the assignments and not the rule."""
    return {
        "fact_kinds": list(FACT_KINDS),
        "by_kind": {kind: [FACT_KIND_BY_PRIMITIVE[k].as_dict()
                           for k in primitives_of_kind(kind)]
                    for kind in FACT_KINDS},
    }


# ==========================================================================
# (3) DATE HELPERS -- local by design
# ==========================================================================
# A shared `_day` that drifts between modules is worse than four identical ones
# that cannot. `_add_months` is v1's arithmetic, restated ONLY because
# pit_policy does not export it; the test asserts the two agree on a sweep.

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


def _add_months(day: str, months: int) -> str:
    d = _dt.date.fromisoformat(day)
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    if month == 12:
        last = 31
    else:
        last = (_dt.date(year, month + 1, 1) - _dt.timedelta(days=1)).day
    return _dt.date(year, month, min(d.day, last)).isoformat()


def _pct(numerator: int, denominator: int) -> float:
    return round(100.0 * numerator / denominator, 2) if denominator else 0.0


def _percentiles(values: Sequence[float],
                 points: Sequence[int] = (5, 25, 50, 75, 90, 95, 99)) -> dict[str, float]:
    """Nearest-rank percentiles. Stdlib, no numpy; these are day counts of real
    filings, and an interpolated 47.5-day age is an age no filing ever had."""
    if not values:
        return {}
    ordered = sorted(values)
    out: dict[str, float] = {"n": float(len(ordered))}
    for point in points:
        rank = max(1, min(len(ordered), -(-point * len(ordered) // 100)))
        out[f"p{point}"] = float(ordered[rank - 1])
    out["mean"] = round(sum(ordered) / len(ordered), 1)
    out["max"] = float(ordered[-1])
    return out


# ==========================================================================
# (4) THE MEASUREMENT -- SURVIVOR_ONLY_DIAGNOSTIC, read-only, no writes
# ==========================================================================
#
# EVERYTHING BELOW THAT TOUCHES A PRICE IS SURVIVOR_ONLY_DIAGNOSTIC. pit_listing
# holds 2,576 company lines, every one alive in 2026, with no series ending
# before 2020, so "the priced universe at 2015-06-30" is a count among
# SURVIVORS and is a different population from the 2015 cross-section. The
# label travels with the number into the output file.
#
# The measurement's one structural economy, and the reason it is affordable:
#
#   V1 AND V2 ARE HANDED THE SAME ROW. Both call the same selector; v1 then
#   refuses at 4 months and v2 at the ceiling. So ONE diagnostic pass with
#   `enforce_staleness=False` yields BOTH answers, and -- because the share-
#   class guard's inputs are (entity, as_of, count value) and the count value is
#   the same row's value -- the guard runs ONCE and its verdict is valid for
#   both policies. No reimplementation of the guard, no second opinion.
#
# The one case where the rows genuinely differ: v1 walks the ladder, so a STALE
# cover-page row can make v1 fall through to a FRESHER balance-sheet row. Each
# rung is therefore selected separately and the winner is chosen per policy.
# ==========================================================================

SAMPLE_SCOPE = "SURVIVOR_ONLY_DIAGNOSTIC"

#: Sampled every date; the run stops rather than starving the checkpointer. A
#: reader that blocks the checkpointer is what once put a 9.3 GB WAL on this
#: volume, and the volume has ~10 GiB free.
DEFAULT_WAL_ABORT_MIB = 1024

#: Free space below which no run starts at all.
MIN_FREE_GIB = 2.0

#: The three cross-sections every earlier phase reported on.
CENSUS_DATES: tuple[str, ...] = ("2015-06-30", "2019-06-28", "2024-06-28")


def disk_check(path: str = ".") -> dict[str, Any]:
    """Free space, as measured. ABORT rather than fill the disk."""
    usage = __import__("shutil").disk_usage(os.path.abspath(path))
    free_gib = usage.free / (1024 ** 3)
    return {
        "total_gib": round(usage.total / (1024 ** 3), 2),
        "free_gib": round(free_gib, 2),
        "pct_used": round(100.0 * usage.used / usage.total, 1),
        "ok": free_gib >= MIN_FREE_GIB,
        "min_free_gib": MIN_FREE_GIB,
    }


def wal_bytes(db_path: str) -> int:
    try:
        return os.path.getsize(db_path + "-wal")
    except OSError:
        return 0


def connect_ro(db_path: str, busy_ms: int = 60_000) -> sqlite3.Connection:
    """Read-only, short transactions, generous busy timeout.

    Another agent holds the write lock intermittently. Python's sqlite3 opens no
    explicit transaction for a bare SELECT, so no read snapshot is held across
    dates and the checkpointer is never starved by this process.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {busy_ms}")
    conn.execute("PRAGMA cache_size = -80000")
    return conn


def measure_date(conn: sqlite3.Connection, day: str, *,
                 ceiling_months: int = STATE_SANITY_CEILING_MONTHS,
                 with_guard: bool = True,
                 with_market_gap: bool = False,
                 keep_sets: bool = False,
                 absence_cache: Optional[dict[int, str]] = None) -> dict[str, Any]:
    """v1 vs v2 defensible share-count coverage at ONE as-of date.

    Calls the REAL functions -- `pit_identity.scored_universe_as_of`,
    `pit_rawprice.archive_shares_as_of`, `pit_rawprice.share_class_audit`,
    `pit_rawprice.class_decision` -- and changes exactly one thing: which age
    bound is applied to the row the selector returned.

    `with_guard=False` reports the pre-guard count only, and labels it. It is
    for a cheap sweep, never for a headline number.

    `keep_sets=True` also returns the ENTITY ID SETS, in exactly the shape
    `pit_cohort_price` writes and `pit_cohort_measure.load_inputs` reads. That
    is what lets the 39,038-peer-set EV/EBITDA census be re-run under the v2
    share policy without re-deriving anything: same masks, same peer sets, one
    substituted leaf.
    """
    import pit_identity
    import pit_rawprice
    import pit_shares

    rows = pit_identity.scored_universe_as_of(conn, day)
    priced: dict[int, int] = {}
    for row in rows:
        priced.setdefault(int(row["entity_id"]), int(row["listing_id"]))

    ladder = pit_shares.SHARES_LADDER_STRICT
    record = {
        "as_of": day,
        "month": int(day[5:7]),
        "sample_scope": SAMPLE_SCOPE,
        "n_priced": len(priced),
        "ceiling_months": ceiling_months,
        "guard_applied": with_guard,
        "v1": {"resolved": 0, "defensible": 0, "reasons": {}},
        "v2": {"resolved": 0, "defensible": 0, "reasons": {}},
        "ceiling_binds": 0,
        "rescued_by_v2": 0,
        "ages_v2_days": [],
        # Every entity-date that HAS a newest in-scope count, whatever either
        # policy then says about it. This is the evidence the ceiling is chosen
        # from, and it is what makes `ceiling_sensitivity` free.
        "ages_all_days": [],
        "only_period_average": 0,
        "never_filed": 0,
        "not_yet_filed": 0,
    }
    sets: dict[str, list[int]] = {"priced": sorted(priced),
                                  "defensible_v1": [], "defensible_v2": []}

    cached_absence = {} if absence_cache is None else absence_cache

    for entity_id in priced:
        # One diagnostic selection PER RUNG, so the ladder fall-through that v1
        # performs on a stale rung is reproduced exactly rather than assumed
        # away. `enforce_staleness=False` makes the selector return the row and
        # leave the age verdict to us -- it never changes which row is in scope.
        picks: list[dict[str, Any]] = []
        rung_reasons: list[str] = []
        for rung in ladder:
            got = pit_rawprice.archive_shares_as_of(
                conn, entity_id, day, ladder=(rung,), enforce_staleness=False)
            if got.get("available"):
                picks.append(got)
            else:
                rung_reasons.append(str(got.get("reason")))

        if not picks:
            # Nothing in scope on ANY rung. `not_yet_filed` (rows exist, none
            # available yet) wins over the entity-level absence reasons, which
            # are date-independent and therefore cacheable across the grid.
            if pit_rawprice.REASON_NOT_YET_FILED in rung_reasons:
                reason = pit_rawprice.REASON_NOT_YET_FILED
            else:
                reason = cached_absence.get(entity_id) or rung_reasons[0]
                cached_absence[entity_id] = reason
            record["v1"]["reasons"][reason] = record["v1"]["reasons"].get(reason, 0) + 1
            record["v2"]["reasons"][reason] = record["v2"]["reasons"].get(reason, 0) + 1
            if reason == pit_rawprice.REASON_ONLY_PERIOD_AVERAGE:
                record["only_period_average"] += 1
            elif reason == pit_rawprice.REASON_NEVER_FILED:
                record["never_filed"] += 1
            elif reason == pit_rawprice.REASON_NOT_YET_FILED:
                record["not_yet_filed"] += 1
            continue

        def winner(bound_months: Optional[int]) -> Optional[dict[str, Any]]:
            """First rung whose row is inside `bound_months`. None means v1's bound.

            Share counts are instantaneous facts, so `qtrs` is 0 by definition
            of the ladder's period_kind and `latest_period_as_of` is queried
            with qtrs = 0; v1's quarterly bucket is therefore the one in force.
            """
            for pick in picks:
                end = _day(pick["measured_at"])
                if end is None:
                    continue
                if bound_months is None:
                    if not pit_policy.is_stale(end, day, 0,
                                               pit_shares.STALENESS_CONCEPT):
                        return pick
                elif day <= _add_months(end, bound_months):
                    return pick
            return None

        v1_pick = winner(None)
        v2_pick = winner(ceiling_months)
        record["ages_all_days"].append(picks[0]["age_days"])

        if v2_pick is None:
            record["ceiling_binds"] += 1
        if v1_pick is None and v2_pick is not None:
            record["rescued_by_v2"] += 1

        # The guard runs at most twice per entity-date and normally once: its
        # inputs are (entity, day, count value) and the two policies usually
        # select the same row.
        cache: dict[float, dict[str, Any]] = {}

        def verdict(pick: Optional[dict[str, Any]], side: str) -> None:
            bucket = record[side]
            if pick is None:
                reason = (pit_rawprice.REASON_STALE if side == "v1"
                          else REASON_BEYOND_STATE_CEILING)
                bucket["reasons"][reason] = bucket["reasons"].get(reason, 0) + 1
                return
            bucket["resolved"] += 1
            if pick.get("zero_count"):
                bucket["reasons"]["zero_share_count"] = (
                    bucket["reasons"].get("zero_share_count", 0) + 1)
                return
            if not with_guard:
                bucket["defensible"] += 1
                return
            value = float(pick["shares"])
            decision = cache.get(value)
            if decision is None:
                audit = pit_rawprice.share_class_audit(conn, entity_id, day,
                                                       shares=pick)
                decision = pit_rawprice.class_decision(
                    audit, pit_rawprice.CLASS_POLICY_STRICT)
                cache[value] = decision
            if decision["ok"]:
                bucket["defensible"] += 1
                sets[f"defensible_{side}"].append(entity_id)
                if side == "v2":
                    record["ages_v2_days"].append(pick["age_days"])
            else:
                key = str(decision["reason"])
                bucket["reasons"][key] = bucket["reasons"].get(key, 0) + 1

        verdict(v1_pick, "v1")
        verdict(v2_pick, "v2")

    for side in ("v1", "v2"):
        bucket = record[side]
        bucket["pct_defensible"] = _pct(bucket["defensible"], record["n_priced"])
        bucket["pct_resolved"] = _pct(bucket["resolved"], record["n_priced"])
        bucket["reasons"] = dict(sorted(bucket["reasons"].items()))
    if with_market_gap:
        # What v2's MARKET rung would cost, measured rather than assumed: how
        # many priced listings actually have a bar ON the as-of session, against
        # the 10-calendar-day reach-back `scored_universe_as_of` allows today.
        on_session = 0
        for listing_id in set(priced.values()):
            if conn.execute(
                    "SELECT 1 FROM pit_price_bar WHERE listing_id = ? AND "
                    "bar_date = ? LIMIT 1", (listing_id, day)).fetchone():
                on_session += 1
        record["market_rung"] = {
            "n_priced_listings": len(set(priced.values())),
            "n_bar_on_as_of_session": on_session,
            "pct_on_session": _pct(on_session, len(set(priced.values()))),
            "note": ("v2's MARKET rule (current session) is STRICTER than the "
                     "10-day reach-back in force today; this is the size of "
                     "that gap, and this module does not enforce it."),
        }
    record["age_percentiles_v2"] = _percentiles(record.pop("ages_v2_days"))
    ages_all = record.pop("ages_all_days")
    record["age_percentiles_all"] = _percentiles(ages_all)
    # The ceiling, priced. Reported for every candidate ceiling at once so the
    # choice of 24 months can be argued against alternatives from measurement
    # rather than re-run for each. `unbounded` is the limit case and is NOT a
    # proposal -- it is the 2011-answering-2024 rule the ceiling exists to stop.
    record["ceiling_sensitivity"] = {
        f"{months}m": sum(1 for a in ages_all if a <= round(months * 30.44))
        for months in (6, 9, 12, 18, 24, 36, 48)
    }
    record["ceiling_sensitivity"]["unbounded"] = len(ages_all)
    record["ceiling_sensitivity"]["v1_quarterly_4m"] = record["v1"]["resolved"]
    record["delta_defensible"] = record["v2"]["defensible"] - record["v1"]["defensible"]
    record["delta_pct_points"] = round(
        record["v2"]["pct_defensible"] - record["v1"]["pct_defensible"], 2)
    if keep_sets:
        record["sets"] = {k: sorted(v) for k, v in sets.items()}
    return record


def by_calendar_month(per_date: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate per-date coverage into calendar months.

    Pooled over entity-dates, NOT averaged over dates: a month with more grid
    dates should weigh more, and a mean of percentages would hide a month whose
    dates have very different universe sizes.
    """
    months: dict[int, dict[str, int]] = {}
    for record in per_date.values():
        bucket = months.setdefault(int(record["month"]),
                                   {"dates": 0, "priced": 0, "v1": 0, "v2": 0,
                                    "ceiling_binds": 0, "rescued": 0})
        bucket["dates"] += 1
        bucket["priced"] += record["n_priced"]
        bucket["v1"] += record["v1"]["defensible"]
        bucket["v2"] += record["v2"]["defensible"]
        bucket["ceiling_binds"] += record.get("ceiling_binds", 0)
        bucket["rescued"] += record.get("rescued_by_v2", 0)
    names = ["", "january", "february", "march", "april", "may", "june", "july",
             "august", "september", "october", "november", "december"]
    out: dict[str, Any] = {}
    for month in sorted(months):
        bucket = months[month]
        out[names[month]] = {
            "month": month,
            "n_grid_dates": bucket["dates"],
            "n_priced_entity_dates": bucket["priced"],
            "v1_defensible": bucket["v1"],
            "v2_defensible": bucket["v2"],
            "v1_pct": _pct(bucket["v1"], bucket["priced"]),
            "v2_pct": _pct(bucket["v2"], bucket["priced"]),
            "delta_pct_points": round(_pct(bucket["v2"], bucket["priced"])
                                      - _pct(bucket["v1"], bucket["priced"]), 2),
            "ceiling_binds": bucket["ceiling_binds"],
            "rescued_by_v2": bucket["rescued"],
        }
    return out


def seasonality_summary(monthly: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Is the calendar artefact gone? Stated as a number, and falsifiable.

    The test is the SPREAD across calendar months. If v2's spread is not much
    smaller than v1's, the diagnosis was wrong, and this function says so rather
    than burying it -- `verdict` is computed from the numbers, not asserted.
    """
    v1 = [m["v1_pct"] for m in monthly.values()]
    v2 = [m["v2_pct"] for m in monthly.values()]
    if not v1:
        return {"verdict": "no data"}

    def spread(values: Sequence[float]) -> dict[str, float]:
        lo, hi = min(values), max(values)
        mean = sum(values) / len(values)
        var = sum((v - mean) ** 2 for v in values) / len(values)
        return {"min": round(lo, 2), "max": round(hi, 2),
                "range_pct_points": round(hi - lo, 2),
                "mean": round(mean, 2), "sd": round(var ** 0.5, 2),
                "cv": round((var ** 0.5) / mean, 4) if mean else 0.0}

    s1, s2 = spread(v1), spread(v2)
    worst_v1 = min(monthly.items(), key=lambda kv: kv[1]["v1_pct"])
    worst_v2 = min(monthly.items(), key=lambda kv: kv[1]["v2_pct"])
    shrunk = s2["range_pct_points"] < s1["range_pct_points"]
    ratio = (s2["range_pct_points"] / s1["range_pct_points"]
             if s1["range_pct_points"] else None)
    return {
        "v1_spread": s1,
        "v2_spread": s2,
        "range_ratio_v2_over_v1": round(ratio, 3) if ratio is not None else None,
        "worst_month_v1": {"month": worst_v1[0], "pct": worst_v1[1]["v1_pct"]},
        "worst_month_v2": {"month": worst_v2[0], "pct": worst_v2[1]["v2_pct"]},
        "verdict": ("the calendar artefact is REMOVED or much reduced"
                    if shrunk and (ratio is not None and ratio < 0.5)
                    else "the calendar artefact SURVIVES -- the diagnosis was "
                         "wrong or incomplete, and this number says so"),
        "how_to_read": ("Spread across calendar months, pooled over entity-dates. "
                        "A staleness rule that removed a calendar artefact must "
                        "flatten the month-to-month range; if the range is "
                        "unchanged the artefact was never staleness."),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the census. Read-only; writes one JSON file to `--out`."""
    argv = list(sys.argv[1:] if argv is None else argv)
    import pit_store
    db_path = pit_store.DEFAULT_PIT_DB_PATH
    out_path = "fact_kind_census.json"
    sets_dir = ""
    wal_abort = DEFAULT_WAL_ABORT_MIB
    ceiling = STATE_SANITY_CEILING_MONTHS
    dates: list[str] = []
    grid = False
    i = 0
    while i < len(argv):
        if argv[i] == "--db" and i + 1 < len(argv):
            db_path = argv[i + 1]; i += 2
        elif argv[i] == "--out" and i + 1 < len(argv):
            out_path = argv[i + 1]; i += 2
        elif argv[i] == "--sets-dir" and i + 1 < len(argv):
            sets_dir = argv[i + 1]; i += 2
        elif argv[i] == "--dates" and i + 1 < len(argv):
            dates = argv[i + 1].split(","); i += 2
        elif argv[i] == "--grid":
            grid = True; i += 1
        elif argv[i] == "--ceiling-months" and i + 1 < len(argv):
            ceiling = int(argv[i + 1]); i += 2
        elif argv[i] == "--wal-abort-mib" and i + 1 < len(argv):
            wal_abort = int(argv[i + 1]); i += 2
        else:
            i += 1

    disk = disk_check(os.path.dirname(os.path.abspath(db_path)) or ".")
    print(f"disk: {disk['free_gib']} GiB free of {disk['total_gib']} "
          f"({disk['pct_used']}% used)", flush=True)
    if not disk["ok"]:
        print(f"ABORT: under {MIN_FREE_GIB} GiB free", flush=True)
        return 2

    problems = validate_against_pit_derive()
    for problem in problems:
        print(f"taxonomy: {problem}", flush=True)

    started = time.time()
    conn = connect_ro(db_path)
    if grid:
        import pit_cohort_scan
        dates = pit_cohort_scan.grid_dates(conn)
    elif not dates:
        dates = list(CENSUS_DATES)

    wal_start = wal_bytes(db_path)
    out: dict[str, Any] = {
        "staleness_policy": staleness_policy_v2(),
        "taxonomy_problems": problems,
        "sample_scope": SAMPLE_SCOPE,
        "db": os.path.abspath(db_path),
        "ceiling_months": ceiling,
        "disk_at_start": disk,
        "n_dates": len(dates),
        "dates": dates,
        "per_date": {},
        "wal_bytes_start": wal_start,
        "wal_peak_bytes": wal_start,
        "disk_free_gib_min": disk["free_gib"],
    }
    print(f"{len(dates)} as-of dates, ceiling {ceiling} months", flush=True)
    absence_cache: dict[int, str] = {}
    for index, day in enumerate(dates):
        attempt = 0
        while True:
            try:
                record = measure_date(conn, day, ceiling_months=ceiling,
                                      with_market_gap=day in CENSUS_DATES,
                                      keep_sets=bool(sets_dir),
                                      absence_cache=absence_cache)
                break
            except sqlite3.OperationalError as exc:
                attempt += 1
                if attempt > 5:
                    raise
                print(f"  locked at {day} ({exc}); retry {attempt}", flush=True)
                time.sleep(5 * attempt)
        out["per_date"][day] = record
        now = wal_bytes(db_path)
        out["wal_peak_bytes"] = max(out["wal_peak_bytes"], now)
        free = disk_check(os.path.dirname(os.path.abspath(db_path)) or ".")["free_gib"]
        out["disk_free_gib_min"] = min(out["disk_free_gib_min"], free)
        if now > wal_abort * 1024 * 1024:
            out["aborted"] = f"WAL reached {now:,} bytes at {day}"
            print(out["aborted"], flush=True)
            break
        if index % 5 == 0 or index == len(dates) - 1:
            print(f"  {day} priced={record['n_priced']:5d} "
                  f"v1={record['v1']['defensible']:5d} "
                  f"({record['v1']['pct_defensible']:5.2f}%) "
                  f"v2={record['v2']['defensible']:5d} "
                  f"({record['v2']['pct_defensible']:5.2f}%) "
                  f"wal={now:,} {time.time() - started:.0f}s", flush=True)
    conn.close()

    if sets_dir:
        # Written in `pit_cohort_price`'s exact shape, one file per policy, so
        # `pit_cohort_measure` can be re-run over the same 39,038 peer sets with
        # only the share leaf substituted. The sets are then dropped from the
        # census record itself -- 165 dates of entity lists would bloat it by
        # two orders of magnitude and say nothing the two files do not.
        os.makedirs(sets_dir, exist_ok=True)
        for side in ("v1", "v2"):
            payload = {
                "sample_scope": SAMPLE_SCOPE,
                "share_policy": (STALENESS_POLICY_V1 if side == "v1"
                                 else STALENESS_POLICY_V2),
                "ceiling_months": ceiling if side == "v2" else None,
                "dates": list(out["per_date"]),
                "per_date": {},
                "wal_peak_bytes": out["wal_peak_bytes"],
            }
            for day, record in out["per_date"].items():
                got = record.get("sets") or {}
                payload["per_date"][day] = {
                    "as_of": day,
                    "n_priced": record["n_priced"],
                    "priced": got.get("priced", []),
                    "n_defensible": record[side]["defensible"],
                    "defensible": got.get(f"defensible_{side}", []),
                    "refusals": record[side]["reasons"],
                }
            path = os.path.join(sets_dir, f"price_shares_{side}.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            print(f"wrote {path} ({os.path.getsize(path):,} bytes)", flush=True)
        for record in out["per_date"].values():
            record.pop("sets", None)

    out["by_calendar_month"] = by_calendar_month(out["per_date"])
    out["seasonality"] = seasonality_summary(out["by_calendar_month"])
    out["elapsed_seconds"] = round(time.time() - started, 1)
    out["wal_bytes_end"] = wal_bytes(db_path)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=1, sort_keys=True, default=str)
    print(f"wrote {out_path} ({os.path.getsize(out_path):,} bytes) in "
          f"{out['elapsed_seconds']}s; WAL peak {out['wal_peak_bytes']:,} bytes; "
          f"min free {out['disk_free_gib_min']} GiB", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
