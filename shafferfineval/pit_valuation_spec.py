"""VALUATION AS THREE SUBFACTORS -- and quality that lives where the truth is.

THE OWNER'S CHANGE, and why `V = 1` was the wrong object
========================================================

A valuation pillar can legitimately be

    P/E                  available, high quality
    P/E peer context     available, medium quality
    EV/EBITDA            UNAVAILABLE

and that is STILL A REAL VALUATION VIEW. The company's own multiple is known,
it is anchored against a declared level, and the only thing missing is a
second, supplementary reading of the same question. Refusing the whole pillar
for it -- which is what an all-or-nothing `V` does -- throws away the answer
because one of three ways of asking was unavailable.

So `V = 1` must NOT mean "every valuation subfactor existed." The DETAILED MASK
carries the truth,

    V[PE=1, PEPeer=1, EVEBITDA=0]

while the pillar stays PRESENT because it has enough legitimate valuation
information to function. That is the difference between v1 and the candidate:
v1 is a conjunction, v2 is a function of its subfactors.

`pit_score_signature` already forbids CROSS-pillar renormalisation and says so
in its own docstring -- "renormalisation WITHIN a pillar stays allowed ... that
is a statement about three measurements of the same thing." This module is that
sentence made executable, for the one pillar where it decides most of the
cross-section.

THE THREE SUBFACTORS
====================

  P/E ABSOLUTE VALUATION  price + TTM EPS. **NO PEER COHORT REQUIRED.** The
                          multiple is scored against a DECLARED FIXED ANCHOR
                          through the convex extreme-valuation transform, so a
                          100x P/E is penalised whether or not anybody comparable
                          could be found. This is the ANCHOR subfactor: it can
                          stand alone, and where it stands the pillar stands.
  P/E RELATIVE CONTEXT    the same multiple against a COHERENT peer cohort.
                          OPTIONAL, and structurally DEPENDENT: it cannot exist
                          without the absolute leg, because it ranks the very
                          number the absolute leg computes.
  EV/EBITDA SUPPLEMENT    the five-primitive chain. OPTIONAL and STANDALONE: a
                          loss-maker with positive EBITDA has no P/E and a real
                          EV/EBITDA, and 12.7% of the FY2014 cross-section is
                          exactly that population (NI <= 0 for 49.2%, EBITDA
                          <= 0 for 37.8%, BOTH for 36.5%).

THE CONSEQUENCE THAT MATTERS MOST. Under a peer-relative-only valuation, a
Tesla-like 100x multiple in a thin SIC4 band scores NOTHING -- no cohort, no
percentile, no penalty, and the 0.35 of weight quietly vanishes. Under this
spec the absolute leg fires anyway and charges 53.99 points. `render_worked_
example()` shows both, side by side, at a thin-cohort as-of date.

THE PILLAR PRESENCE RULE -- 'valuation_presence_rule_v1'
========================================================

Stated once, and `presence()` is the only implementation:

    0. DEPENDENCY FIRST. A subfactor whose `depends_on` is not present is
       itself refused, whatever the caller offered. P/E peer context without a
       P/E is not a valuation reading, it is a ranking of nothing.
    1. PRESENT       iff at least one PRESENT subfactor has can_stand_alone.
    2. PRESENT_FULL  iff every declared subfactor is present.
    3. PRESENT_DEGRADED  iff PRESENT and not FULL.
    4. ABSENT        otherwise -- and the mask still says which optional
                     subfactors were there, because "EV/EBITDA alone" and
                     "nothing at all" are different findings.

The three states are NOT new vocabulary. They are `pit_store.AVAIL_COMPLETE`,
`AVAIL_PARTIAL` and `AVAIL_UNAVAILABLE`, which have existed since the schema
was written and already mean exactly this.

The pillar score renormalises over the PRESENT SUBFACTOR WEIGHTS. That is the
within-pillar renormalisation v2 permits, and it has a consequence to state
plainly rather than discover later: A DEGRADED PILLAR LANDS ON THE SAME SCALE
AS A FULL ONE. Two rows can both read `VGPD` and both be scaled identically
while one was built from three valuation readings and the other from one. That
is not a bug being tolerated -- it is precisely the situation
`docs/CONFIDENCE-FRAMEWORK.md` section 2 designed `data_quality` for, and it is why
the mask and Q are STORED COLUMNS rather than prose in a note.

SUBFACTOR-LEVEL QUALITY, AND WHY PILLAR-LEVEL QUALITY IS A TYPE ERROR
=====================================================================

`Q = f(SourceQuality, Freshness, PeerCoherence, PrimitiveCoverage)`. Move that
to the pillar and one of its four terms becomes undefined: THE ABSOLUTE LEG HAS
NO PEERS, so it has no peer coherence, and a pillar-level Q would have to
average a peer-coherence number into a reading that never consulted a peer.
Each subfactor therefore declares WHICH components apply to it, and a component
that does not apply is absent rather than imputed at 1.0.

The combination rule is NOT adopted here, because `docs/CONFIDENCE-FRAMEWORK.md`
section 3 has not adopted it: a weak debt rung and a thin peer set are plausibly one
latent cause (small, obscure, thinly-covered issuers) and multiplying two
correlated penalties charges twice for one weakness. `data_quality()` returns
EVERY candidate rule's value and adopts none unless the caller names one; the
record says `UNDECIDED` either way, and the components are stored so the choice
stays learnable. `F_raw`, `Q` and `F_effective` are all three stored, never the
product alone (section 2).

EARNINGS_SIGN_CROSS -- A STATE, NEVER A PERCENTAGE
==================================================

61.35% of entities cross zero at some point. -$0.10 -> +$0.10 is one of the
most important things that can happen to a company, and

    (0.10 - (-0.10)) / -0.10  =  -200%

is a real number, correctly computed, that points the WRONG WAY. Note what this
is: not a division by zero, not a NaN, not a guard failing. It is arithmetic
that succeeds and lies. So the refusal here is DELIBERATE rather than defensive
-- `eps_transition()` computes the state and refuses the percentage.

The rule is wider than the sign cross alone, because the sign cross is not the
only place the denominator breaks:

    A PERCENTAGE GROWTH RATE EXISTS ONLY WHERE THE BASE IS STRICTLY POSITIVE.

A loss narrowing from -$1.00 to -$0.10 is a 90% improvement and the formula
returns -90%. Both endpoints negative, no crossing, no zero, and the sign is
still inverted. `both_negative` is therefore its own state too, with a
LOSS_NARROWING / LOSS_DEEPENING direction taken from the LEVEL difference.

What replaces the percentage where one is not allowed: the CHANGE IN SIGNED
EARNINGS YIELD, `(eps_t - eps_t-1) / price`, which is defined and monotone
straight through zero. One price -- the as-of price -- deliberately, so the
quantity measures earnings and not a price return. It is a research candidate
for the ML layer, it is price-conditional and therefore
SURVIVOR_ONLY_DIAGNOSTIC, and it is never a valuation leg.

And negative EPS remains a LOSS-MAKING STATE, not a cheap P/E. This module
never calls abs() on an earnings figure; the refusal lives in
`pit_eps.price_earnings`, which is IMPORTED rather than restated.

THE SHARE-BASIS TRAP ON A TWO-PERIOD EPS PAIR (a finding, not a footnote)
========================================================================

`pit_eps` establishes that a point-in-time EPS is in the share basis of its own
era: Apple's FY2019 diluted EPS is 11.89 as printed and 2.97 in the next 10-K's
comparative, four times apart, with a 4:1 split between them. A PIT selector at
two different period ends can therefore return two figures in two different
share bases, and their difference -- percentage OR level OR yield -- is wrong by
the split factor. `eps_transition` takes `same_share_basis` and DEFAULTS TO
None = UNKNOWN, which refuses all pair arithmetic. An unmeasured quantity is
UNKNOWN, never assumed true.

WIRING -- FOUR CANONICAL NAMES, NO FIFTH
========================================

`docs/CONFIDENCE-FRAMEWORK.md` names the four, and `VOCABULARY_MAP` maps each to
the OBJECT that already implements it. Nothing here is a parallel system:

    data_quality        NEW at subfactor granularity -- `data_quality()` below
    score_completeness  `pit_score_signature` -- signature, markers, coverage.
                        EXTENDED by `subfactor_mask`, never replaced.
    forecast_evidence   `pit_invariants.evidence_status` -- called, not restated.
                        The mask joins the STRATIFICATION KEY.
    hedge_evidence      `pit_intermediates.hedge_research_status` -- UNCHANGED
                        and untouched: it is temporal and has nothing to do with
                        valuation, and giving it a valuation flavour would be
                        inventing a fifth name wearing a fourth name's clothes.

STATUS AND RESTRAINT
====================

The production core is FROZEN. Nothing here edits `company_scoring.py`,
`sector_scoring.py`, `asset_models.py`, `prediction.py`, `hedging.py` or any v1
policy; everything below is `candidate_equity_shaffer_v2`. This module is
DECLARATIVE: it opens no connection, issues no SQL, fetches nothing, fits
nothing, and leaves `pit_feature`, `pit_score` and `pit_replay_run` exactly as
empty as it found them. Its storage section is SPECIFIED, NOT APPLIED.

Stdlib only.

    python pit_valuation_spec.py           # the worked example; PASS/FAIL
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

import pit_eps
import pit_factor_spec
import pit_intermediates
import pit_invariants
import pit_normalization
import pit_price_basis
import pit_score_signature
import pit_shares
import pit_store

__all__ = [
    "VALUATION_SPEC_VERSION", "PRESENCE_RULE_VERSION", "ANCHOR_POLICY_VERSION",
    "TRANSFORM_VERSION", "CANDIDATE_MODEL_VERSION",
    # subfactors
    "SUBFACTOR_PE_ABSOLUTE", "SUBFACTOR_PE_RELATIVE", "SUBFACTOR_EV_EBITDA",
    "SUBFACTORS", "SUBFACTOR_KEYS", "SUBFACTOR_CODE", "subfactor",
    "ROLE_ANCHOR", "ROLE_CONTEXT", "ROLE_SUPPLEMENT", "ROLES", "Subfactor",
    "SUBFACTOR_WEIGHTS", "SURVIVOR_ONLY", "FULL_UNIVERSE", "PRICE_BASIS_RAW",
    # presence
    "PRESENT_FULL", "PRESENT_DEGRADED", "ABSENT", "PRESENCE_STATES",
    "PRESENCE_AVAILABILITY", "Presence", "presence", "subfactor_mask",
    "parse_mask", "MASK_FULL", "MASK_NONE",
    # the absolute anchor
    "PE_ABSOLUTE_ANCHOR", "ANCHOR_FIXED_MARKET_PE", "ANCHOR_COHORT_MEDIAN",
    "ANCHOR_SOURCES", "anchor_policy", "WHAT_IS_ARBITRARY",
    "DEBT_RUNG_POLICY_VERSION", "DEBT_ELIGIBLE", "DEBT_REJECTED_MEASURED",
    "DEBT_UNDETERMINED", "DEBT_STATUSES", "DEBT_RUNG_EVEBITDA_V1",
    "DEBT_RUNG_REASONS", "WHY_NOT_CONTINUOUS_Q", "debt_rung_status",
    "debt_rung_eligible", "debt_rung_evidence", "debt_policy", "debt_gate",
    "REASON_DEBT_RUNG_REJECTED", "REASON_DEBT_RUNG_UNDETERMINED",
    "debt_threshold_history",
    "PE_ABSOLUTE_ANCHOR_V1", "ANCHOR_NEUTRALITY_SEMANTICS", "ANCHOR_SWEEP_SPEC",
    "PRICE_RAW_AS_TRADED", "PRICE_ACTION_ADJUSTED",
    # the convex transform
    "TRANSFORM_A", "TRANSFORM_THETA", "TRANSFORM_C", "TRANSFORM_B",
    "TRANSFORM_FLOOR_MULTIPLE", "TRANSFORM_FLOOR", "TRANSFORM_PARAMS_V1",
    "solve_b", "valuation_score",
    "valuation_score_detail", "PINNED_CURVE",
    # earnings sign crossing
    "EARNINGS_SIGN_CROSS", "TRANSITION_BOTH_POSITIVE",
    "TRANSITION_BOTH_NEGATIVE", "TRANSITION_LOSS_TO_PROFIT",
    "TRANSITION_PROFIT_TO_LOSS", "TRANSITION_FROM_ZERO", "TRANSITION_TO_ZERO",
    "TRANSITION_NEAR_ZERO_BASE",
    "TRANSITION_STATES", "CROSSING_STATES", "DIR_LOSS_NARROWING",
    "DIR_LOSS_DEEPENING", "DIR_UNCHANGED", "EpsTransition", "eps_transition",
    "eps_transition_from_pair", "TRANSITION_ENDPOINT_MISSING",
    "TRANSITION_SHARE_BASIS_UNRESOLVED",
    "earnings_yield_change", "SHARE_BASIS_UNKNOWN",
    # subfactor quality
    "COMPONENT_SOURCE_QUALITY", "COMPONENT_FRESHNESS",
    "COMPONENT_PEER_COHERENCE", "COMPONENT_PRIMITIVE_COVERAGE",
    "QUALITY_COMPONENTS", "QUALITY_RULE_PRODUCT", "QUALITY_RULE_MIN",
    "QUALITY_RULE_WEIGHTED", "QUALITY_RULE_LEARNED", "QUALITY_RULE_UNDECIDED",
    "QUALITY_RULES", "QUALITY_RULE_STATUS", "DataQuality", "data_quality",
    "effective_value",
    # assembly
    "SubfactorResult", "ValuationPillar", "valuation_pillar",
    "pillar_reason", "stratify_key",
    # wiring
    "VOCABULARY_MAP", "forecast_evidence", "hedge_evidence",
    "confidence_view", "render_confidence_view", "QUALITY_BANDS",
    "quality_band",
    # storage -- specified, NOT applied
    "PIT_SCORE_SUBFACTOR_MIGRATIONS", "PIT_SCORE_SUBFACTOR_COLUMN_DDL",
    "migration_patch", "migration_sql", "storage_note",
    # evidence and demonstration
    "MEASURED", "KNOWN_LIMITATIONS", "FIXTURE_AS_OF", "FIXTURE_NOTE",
    "FIXTURE_THIN", "FIXTURE_VERY_THIN", "FIXTURE_OTHER_PILLARS",
    "render", "render_curve",
    "render_worked_example", "render_transitions", "validate", "main",
]


# ==========================================================================
# (0) VERSIONS AND LINEAGE
#
# One id per decision, because a row that says how it was scored must mean
# exactly one thing forever. None of these is ever edited in place.
# ==========================================================================

VALUATION_SPEC_VERSION = "valuation_subfactor_spec_v1"
PRESENCE_RULE_VERSION = "valuation_presence_rule_v1"
ANCHOR_POLICY_VERSION = "valuation_anchor_v1_fixed_pe_20"
TRANSFORM_VERSION = "extreme_valuation_transform_v1"

#: The candidate lineage. Imported rather than restated -- three spellings of
#: one model id would be three models.
CANDIDATE_MODEL_VERSION = pit_factor_spec.CANDIDATE_MODEL_VERSION

#: Results that touch a price or a realized return. 2,574 listings, all alive
#: in 2026, median survivor underperforming the total market by -7.45% at 12M.
SURVIVOR_ONLY = pit_factor_spec.SURVIVOR_ONLY
FULL_UNIVERSE = pit_factor_spec.FULL_UNIVERSE

#: A P/E is taken against a RAW, AS-TRADED, UNADJUSTED price, because a
#: point-in-time EPS is in the share basis of its own era.
#:
#: OWNER DECISION 2, 2026-09-21, RESOLVED IN FAVOUR OF THE RAW PRICE -- with
#: the refinement that makes it correct on BOTH sides of a split: the EPS is
#: then carried to the SCORE-DATE share basis, using only corporate actions
#: with `effective_date <= as_of`. `pit_price_basis` owns that machinery and
#: refuses the substitution outright rather than defaulting around it.
PRICE_RAW_AS_TRADED = pit_price_basis.PRICE_RAW_AS_TRADED

#: The returns/labels concept, named here only so that the two are visibly
#: DIFFERENT primitives rather than two spellings of one. They are never
#: interchangeable: valuation takes the first, a realized return takes the
#: second, and `pit_price_basis.assert_price_basis` raises on a swap.
PRICE_ACTION_ADJUSTED = pit_price_basis.PRICE_ACTION_ADJUSTED

#: Retained: `pit_shares`' spelling of the same raw concept.
PRICE_BASIS_RAW = pit_shares.PRICE_BASIS_RAW


# ==========================================================================
# (1) THE THREE SUBFACTORS
# ==========================================================================

#: The subfactor that can carry the pillar by itself and needs nothing from a
#: cohort. Exactly one subfactor holds this role, on purpose.
ROLE_ANCHOR = "anchor"

#: Adds a reading; cannot exist alone. Declares `depends_on`.
ROLE_CONTEXT = "context"

#: A SECOND standalone reading of the valuation question, from different
#: primitives. It can carry the pillar where the anchor cannot -- a loss-maker
#: with positive EBITDA -- which is why it is not merely context.
ROLE_SUPPLEMENT = "supplement"

ROLES: tuple[str, ...] = (ROLE_ANCHOR, ROLE_CONTEXT, ROLE_SUPPLEMENT)

SUBFACTOR_PE_ABSOLUTE = "pe_absolute"
SUBFACTOR_PE_RELATIVE = "pe_relative"
SUBFACTOR_EV_EBITDA = "ev_ebitda_supplement"


@dataclass(frozen=True)
class Subfactor:
    """One reading of the valuation question, declared as data.

    `can_stand_alone` is the field the presence rule turns on, and it is NOT
    the same as "has no dependency": the supplement has no dependency AND
    stands alone, while the context has a dependency AND cannot. Keeping them
    separate is what lets a fourth subfactor be added later without rewriting
    the rule.

    `quality_components` is the subset of the four confidence components that
    MEAN something for this subfactor. The absolute leg omits peer coherence
    because it consults no peer, and that omission is the argument for putting
    quality at subfactor level in the first place.
    """

    key: str
    code: str
    label: str
    role: str
    weight: float
    required_primitives: tuple[str, ...]
    requires_cohort: bool
    normalization_type: str
    anchor_source: str
    quality_components: tuple[str, ...]
    depends_on: Optional[str]
    can_stand_alone: bool
    sample_scope: str
    why: str
    factor_spec_key: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "code": self.code,
            "label": self.label,
            "role": self.role,
            "weight": self.weight,
            "required_primitives": list(self.required_primitives),
            "requires_cohort": self.requires_cohort,
            "normalization_type": self.normalization_type,
            "anchor_source": self.anchor_source,
            "quality_components": list(self.quality_components),
            "depends_on": self.depends_on,
            "can_stand_alone": self.can_stand_alone,
            "sample_scope": self.sample_scope,
            "factor_spec_key": self.factor_spec_key,
            "why": self.why,
        }


COMPONENT_SOURCE_QUALITY = "source_quality"
COMPONENT_FRESHNESS = "freshness"
COMPONENT_PEER_COHERENCE = "peer_coherence"
COMPONENT_PRIMITIVE_COVERAGE = "primitive_coverage"

#: The four of `docs/CONFIDENCE-FRAMEWORK.md` section 2, in its order.
QUALITY_COMPONENTS: tuple[str, ...] = (
    COMPONENT_SOURCE_QUALITY, COMPONENT_FRESHNESS,
    COMPONENT_PEER_COHERENCE, COMPONENT_PRIMITIVE_COVERAGE,
)

#: NO PEER COHERENCE. The absolute leg never forms a cohort, so the component
#: is undefined for it rather than perfect.
_ABSOLUTE_COMPONENTS = (COMPONENT_SOURCE_QUALITY, COMPONENT_FRESHNESS,
                        COMPONENT_PRIMITIVE_COVERAGE)

#: Where the value that scores ZERO comes from. `pit_normalization`'s anchor
#: vocabulary is reused where it fits; the fixed market anchor is new and is
#: declared below beside the number it names.
ANCHOR_FIXED_MARKET_PE = "fixed_market_pe_anchor"
ANCHOR_COHORT_MEDIAN = "cohort_median_multiple"
ANCHOR_SOURCES: tuple[str, ...] = (
    ANCHOR_FIXED_MARKET_PE, ANCHOR_COHORT_MEDIAN, pit_normalization.ANCHOR_NONE,
)

SUBFACTORS: dict[str, Subfactor] = {
    SUBFACTOR_PE_ABSOLUTE: Subfactor(
        key=SUBFACTOR_PE_ABSOLUTE,
        code="PE",
        label="P/E absolute valuation",
        role=ROLE_ANCHOR,
        weight=0.50,
        required_primitives=(PRICE_RAW_AS_TRADED, "earnings_per_share_pit"),
        requires_cohort=False,
        normalization_type=pit_normalization.BENCHMARK_ANCHORED,
        anchor_source=ANCHOR_FIXED_MARKET_PE,
        quality_components=_ABSOLUTE_COMPONENTS,
        depends_on=None,
        can_stand_alone=True,
        sample_scope=SURVIVOR_ONLY,
        factor_spec_key="pe_ratio",
        why=("TWO LEAVES, NO COHORT. The share division is already inside the "
             "filed EPS, so this leg does not touch the defensible share count "
             "-- the binding leaf of the whole valuation chain (leave-one-out: "
             "EV/EBITDA P(N>=3) 34.2% -> 61.6% when it is dropped). It is the "
             "ANCHOR because an extreme multiple must be penalised on its own "
             "terms: a 100x P/E is expensive whether or not thirty comparable "
             "issuers could be found, and waiting for peers is how a thin SIC4 "
             "band becomes a free pass."),
    ),
    SUBFACTOR_PE_RELATIVE: Subfactor(
        key=SUBFACTOR_PE_RELATIVE,
        code="PEPeer",
        label="P/E relative context",
        role=ROLE_CONTEXT,
        weight=0.30,
        required_primitives=(PRICE_RAW_AS_TRADED, "earnings_per_share_pit",
                             "coherent_peer_cohort"),
        requires_cohort=True,
        normalization_type=pit_normalization.BENCHMARK_ANCHORED,
        anchor_source=ANCHOR_COHORT_MEDIAN,
        quality_components=QUALITY_COMPONENTS,
        depends_on=SUBFACTOR_PE_ABSOLUTE,
        can_stand_alone=False,
        sample_scope=SURVIVOR_ONLY,
        factor_spec_key="pe_ratio",
        why=("The SAME multiple, asked a DIFFERENT question: not 'is this "
             "expensive' but 'is this expensive FOR ITS INDUSTRY'. Structurally "
             "dependent -- it ranks the number the absolute leg computes, so it "
             "cannot exist without it, and `presence()` enforces that rather "
             "than trusting a caller. Its cohort must clear BOTH of "
             "`pit_factor_spec`'s gates (sufficient N and economic coherence, "
             "ceiling sic2): the SEC Office rung clears twelve members 94.5% of "
             "the time with 542-1,310 issuers whose only shared property is "
             "their filing reviewer, and that is not a valuation peer group."),
    ),
    SUBFACTOR_EV_EBITDA: Subfactor(
        key=SUBFACTOR_EV_EBITDA,
        code="EVEBITDA",
        label="EV/EBITDA supplement",
        role=ROLE_SUPPLEMENT,
        weight=0.20,
        required_primitives=(PRICE_RAW_AS_TRADED, "shares_outstanding",
                             "total_debt", "cash", "operating_income",
                             "depreciation_amortisation"),
        requires_cohort=True,
        normalization_type=pit_normalization.BENCHMARK_ANCHORED,
        anchor_source=ANCHOR_COHORT_MEDIAN,
        quality_components=QUALITY_COMPONENTS,
        depends_on=None,
        can_stand_alone=True,
        sample_scope=SURVIVOR_ONLY,
        factor_spec_key="ev_ebitda",
        why=("Capital-structure-neutral, and the ONE leg that survives a loss. "
             "FY2014, n=2,756: NI <= 0 for 49.2%, EBITDA <= 0 for 37.8%, both "
             "for 36.5% -- so 12.7% of the cross-section has an EV/EBITDA and "
             "NO P/E, and refusing those companies a valuation pillar would be "
             "refusing a reading we actually have. STANDALONE for that reason, "
             "LAST in weight because it is the rarest: median cohort of 1, "
             "P(N>=3) 34.2%, P(N>=12) 7.0%, and 2.4% at sic4. Its debt leaf "
             "also carries the ladder-v2 lower-bound mixture -- 94% of the v2 "
             "recovery understates debt by a median 14%, which FLATTERS an "
             "enterprise value."),
    ),
}

#: Canonical order: anchor, then context, then supplement. Load-bearing, like
#: `pit_score_signature.PILLARS` -- the mask is a string compared for equality,
#: so two orderings would be two masks for one state.
SUBFACTOR_KEYS: tuple[str, ...] = (
    SUBFACTOR_PE_ABSOLUTE, SUBFACTOR_PE_RELATIVE, SUBFACTOR_EV_EBITDA,
)

SUBFACTOR_CODE: dict[str, str] = {k: SUBFACTORS[k].code for k in SUBFACTOR_KEYS}
_CODE_SUBFACTOR: dict[str, str] = {v: k for k, v in SUBFACTOR_CODE.items()}

SUBFACTOR_WEIGHTS: dict[str, float] = {
    k: SUBFACTORS[k].weight for k in SUBFACTOR_KEYS
}


def subfactor(key: str) -> Subfactor:
    """One subfactor by key. Raises on an unknown key rather than returning None."""
    try:
        return SUBFACTORS[key]
    except KeyError:
        raise ValueError(
            f"unknown valuation subfactor {key!r}; known: "
            f"{', '.join(SUBFACTOR_KEYS)}") from None


# ==========================================================================
# (2) THE PILLAR PRESENCE RULE -- 'valuation_presence_rule_v1'
#
# The whole of the owner's change is in `presence()`. Everything above it is
# declaration and everything below it is arithmetic.
# ==========================================================================

PRESENT_FULL = "PRESENT_FULL"
PRESENT_DEGRADED = "PRESENT_DEGRADED"
ABSENT = "ABSENT"

PRESENCE_STATES: tuple[str, ...] = (PRESENT_FULL, PRESENT_DEGRADED, ABSENT)

#: The states are `pit_store`'s availability vocabulary, not a parallel one.
#: `AVAIL_PARTIAL` has meant "present but not whole" since the schema was
#: written; present-but-degraded is what it was for.
PRESENCE_AVAILABILITY: dict[str, str] = {
    PRESENT_FULL: pit_store.AVAIL_COMPLETE,
    PRESENT_DEGRADED: pit_store.AVAIL_PARTIAL,
    ABSENT: pit_store.AVAIL_UNAVAILABLE,
}

MASK_FULL = "V[PE=1,PEPeer=1,EVEBITDA=1]"
MASK_NONE = "V[PE=0,PEPeer=0,EVEBITDA=0]"


def subfactor_mask(present: Mapping[str, Any]) -> str:
    """The detailed mask the owner wrote: 'V[PE=1,PEPeer=1,EVEBITDA=0]'.

    Deliberately a STRING and not a bitfield. It is read by a human off a row
    months later, grouped on in SQL, and printed in a report; three digits in
    an integer column would need a decoder ring, and the decoder ring is where
    the meaning goes to die.
    """
    return "V[" + ",".join(
        f"{SUBFACTOR_CODE[k]}={1 if present.get(k) else 0}"
        for k in SUBFACTOR_KEYS) + "]"


def parse_mask(mask: str) -> dict[str, bool]:
    """The inverse of `subfactor_mask`. Raises on anything it cannot read."""
    text = (mask or "").strip()
    if not (text.startswith("V[") and text.endswith("]")):
        raise ValueError(f"unreadable subfactor mask {mask!r}: expected V[...]")
    out: dict[str, bool] = {}
    for part in text[2:-1].split(","):
        if "=" not in part:
            raise ValueError(f"unreadable subfactor mask {mask!r}: {part!r}")
        code, _, flag = part.partition("=")
        code, flag = code.strip(), flag.strip()
        if code not in _CODE_SUBFACTOR:
            raise ValueError(f"unknown subfactor code {code!r} in {mask!r}")
        if flag not in ("0", "1"):
            raise ValueError(f"subfactor flag must be 0 or 1, got {flag!r}")
        out[_CODE_SUBFACTOR[code]] = flag == "1"
    if len(out) != len(SUBFACTOR_KEYS):
        raise ValueError(f"subfactor mask {mask!r} does not name every subfactor")
    return {k: out[k] for k in SUBFACTOR_KEYS}


@dataclass(frozen=True)
class Presence:
    """The presence decision, with the reasoning kept beside the answer."""

    state: str
    availability: str
    mask: str
    present: tuple[str, ...]
    absent: tuple[str, ...]
    refused_for_dependency: tuple[str, ...]
    standing_alone: tuple[str, ...]
    present_weight: float
    rule_version: str = PRESENCE_RULE_VERSION

    @property
    def is_present(self) -> bool:
        return self.state in (PRESENT_FULL, PRESENT_DEGRADED)

    @property
    def degraded(self) -> bool:
        return self.state == PRESENT_DEGRADED

    def as_dict(self) -> dict[str, Any]:
        return {
            "presence_state": self.state,
            "availability": self.availability,
            "subfactor_mask": self.mask,
            "subfactors_present": list(self.present),
            "subfactors_absent": list(self.absent),
            "refused_for_dependency": list(self.refused_for_dependency),
            "standing_alone": list(self.standing_alone),
            "present_subfactor_weight": self.present_weight,
            "presence_rule_version": self.rule_version,
        }


def presence(offered: Mapping[str, Any]) -> Presence:
    """PRESENT_FULL / PRESENT_DEGRADED / ABSENT, from what each subfactor offered.

    `offered` maps a subfactor key to anything truthy-for-present: a bool, a
    `SubfactorResult`, a score. Unknown keys raise -- a typo'd subfactor name
    that silently meant "absent" would turn a coding error into a data finding.

    THE RULE, in the order it is applied:

      0. DEPENDENCY. A subfactor whose `depends_on` is not present is refused,
         and is reported in `refused_for_dependency` so the row can tell "the
         cohort failed" from "the leg it hangs off failed."
      1. PRESENT iff some surviving subfactor has `can_stand_alone`.
      2. FULL iff every declared subfactor survived.

    Worth stating once: the rule is an OR over standalone subfactors, never a
    conjunction and never a weight threshold. A weight threshold would have
    been tunable, and a tunable presence rule is a knob that decides how much
    of the cross-section has a valuation pillar -- which is a modelling
    decision, not a parameter.
    """
    unknown = [k for k in offered if k not in SUBFACTORS]
    if unknown:
        raise ValueError(
            f"unknown valuation subfactor(s) {sorted(unknown)!r}; known: "
            f"{', '.join(SUBFACTOR_KEYS)}")

    raw = {k: bool(offered.get(k)) for k in SUBFACTOR_KEYS}
    refused: list[str] = []
    survived: dict[str, bool] = {}
    for key in SUBFACTOR_KEYS:                      # anchor first, by order
        item = SUBFACTORS[key]
        if not raw[key]:
            survived[key] = False
            continue
        if item.depends_on is not None and not survived.get(item.depends_on, False):
            survived[key] = False
            refused.append(key)
            continue
        survived[key] = True

    present = tuple(k for k in SUBFACTOR_KEYS if survived[k])
    absent = tuple(k for k in SUBFACTOR_KEYS if not survived[k])
    standing = tuple(k for k in present if SUBFACTORS[k].can_stand_alone)
    weight = sum(SUBFACTORS[k].weight for k in present)

    if not standing:
        state = ABSENT
    elif len(present) == len(SUBFACTOR_KEYS):
        state = PRESENT_FULL
    else:
        state = PRESENT_DEGRADED

    return Presence(
        state=state,
        availability=PRESENCE_AVAILABILITY[state],
        mask=subfactor_mask(survived),
        present=present,
        absent=absent,
        refused_for_dependency=tuple(refused),
        standing_alone=standing,
        present_weight=weight,
    )


# ==========================================================================
# (3) THE ABSOLUTE ANCHOR -- and what is arbitrary about it
#
# The relative leg's anchor is obvious: the peer median. The absolute leg has
# no peers, so the anchor is a CHOICE, and the choice is stated here rather
# than buried in a default argument.
# ==========================================================================

#: The value of P/E that scores exactly ZERO on the absolute leg.
#:
#: FIXED, and that is the whole point. The three rejected alternatives and why:
#:
#:   THE CROSS-SECTIONAL MEDIAN OF THE PRICED UNIVERSE AT THE AS-OF DATE is not
#:   an absolute anchor at all -- it is a peer cohort widened to "everyone", and
#:   it makes a market-wide re-rating INVISIBLE by construction. If every
#:   multiple doubles, every score is unchanged. Catching exactly that is what
#:   the absolute leg is for.
#:
#:   THE SECTOR'S OWN HISTORY grades on a curve: a sector that has always been
#:   expensive scores neutral while it is expensive, which again hides the thing
#:   being measured. It also cannot be computed honestly from this store -- the
#:   price history starts 2013, contains no pre-GFC period, and its priced
#:   universe is SURVIVOR_ONLY (2,574 listings all alive in 2026), so a
#:   "historical sector median P/E" here is a survivor median with an unknown
#:   bias and a 13-year window.
#:
#:   A MEASURED LONG-RUN MARKET MEDIAN is the right idea and is NOT AVAILABLE
#:   HERE for the same reason: this store cannot produce one that is not
#:   survivor-contaminated and calendar-truncated. Importing one from outside
#:   would be importing an unversioned number into a versioned policy.
#:
#: So: a declared constant, at 20.0. Only a fixed anchor makes a market-wide
#: re-rating show up in the score, and only a fixed anchor makes the absolute
#: leg comparable ACROSS AS-OF DATES -- which the relative leg can never be,
#: because its anchor moves underneath it every month.
#:
#: OWNER DECISION 1, 2026-09-21. KEPT AT 20.0 FOR THE FIRST FROZEN CANDIDATE,
#: and described as a POLICY CONVENTION justified by continuity with the
#: already-published calibration -- NOT as an empirically discovered fair
#: multiple. The version suffix is part of the decision: a later anchor is a
#: v2 of this policy beside it, never an edit of it.
PE_ABSOLUTE_ANCHOR_V1 = 20.0

#: Retained spelling so nothing downstream silently reads a different number
#: than it did yesterday. The versioned name is the one to quote.
PE_ABSOLUTE_ANCHOR = PE_ABSOLUTE_ANCHOR_V1

#: THE SEMANTIC STATEMENT THE ANCHOR MAKES, and the one it does NOT.
#:
#:     P/E = 20x  =>  PE_absolute = 0
#:
#: is a statement about WHERE NEUTRAL SITS on this scale. It is NOT a claim
#: that 20x is fair value in every rate regime, and it must never be rendered
#: as one. The distinction is load-bearing rather than pedantic, because the
#: anchor is deliberately RATE-BLIND: it will interpret broad market multiple
#: expansion as increasing expensiveness, including expansion that a falling
#: discount rate fully justifies. That behaviour is chosen, not overlooked --
#: catching a market-wide re-rating is the absolute leg's only job, and an
#: anchor that moved with rates could not do it. What the choice costs is
#: stated here so a reader of a -54 score knows which statement it is making.
ANCHOR_NEUTRALITY_SEMANTICS = (
    "P/E = 20x => PE_absolute = 0 is a semantic statement about NEUTRALITY on "
    "this scale, not a statement that 20x is fair value in every rate regime. "
    "The anchor is rate-blind by design, so it will deliberately read broad "
    "market multiple expansion as increasing expensiveness -- including "
    "expansion a falling discount rate justifies. Carry "
    "RATE_REGIME_GENERALIZATION_UNPROVEN with any claim built on it."
)

#: Read this before quoting anything above. The honest account of the choice.
WHAT_IS_ARBITRARY: dict[str, Any] = {
    "policy_version": ANCHOR_POLICY_VERSION,
    "the_kind_is_defensible": (
        "FIXED rather than cross-sectional or historical. That part is argued, "
        "not assumed: a moving anchor cannot see a market-wide re-rating, and "
        "seeing one is the absolute leg's only job."),
    "the_level_is_arbitrary": (
        "20.0 IS A CONVENTION AND NOT A MEASUREMENT. It was not estimated from "
        "this store, and it could not be: the priced universe is "
        "SURVIVOR_ONLY_DIAGNOSTIC and the price history starts in 2013. Long-run "
        "US market trailing P/E is usually quoted somewhere between 15 and 20 "
        "depending entirely on the window, and picking a point in that range is "
        "a judgement. 16 would be as defensible as 20 and would score every "
        "company more harshly; `render_curve()` prints both so the sensitivity "
        "is visible rather than implied."),
    "why_20_and_not_16": (
        "One reason only, and it is a reproducibility reason rather than an "
        "economic one: the candidate spec's published calibration -- 20->30 "
        "costs 10.14 points, 100->150 costs 25.00, ratio 2.47x -- was struck at "
        "parity = 20. Setting the absolute anchor there makes those three "
        "numbers THE ABSOLUTE LEG'S OWN NUMBERS rather than a demonstration on "
        "a different anchor, so `PINNED_CURVE` is a pin and not an analogy. "
        "That is a good reason to prefer 20 and a bad reason to believe 20."),
    "what_the_level_decides": (
        "Everything downstream of it. theta = ln 2 puts the quadratic knot at "
        "2 x anchor = 40x, and the -100 floor at 10 x anchor = 200x. Moving the "
        "anchor to 16 moves the knot to 32x and the floor to 160x, and charges "
        "a 100x multiple 67.08 points instead of 53.99."),
    "not_rate_or_inflation_adjusted": (
        "A 20x anchor at a 1% ten-year yield and at a 6% ten-year yield are "
        "DIFFERENT ECONOMIC STATEMENTS, and this policy makes the same one at "
        "both. The consequence is deliberate and is stated rather than fudged: "
        "a rate-driven re-rating of the whole market scores as expensive. "
        "Whether that improves prediction is an empirical question for the ML "
        "layer, exactly as docs/CONFIDENCE-FRAMEWORK.md section 4 says of "
        "quality-adjusting the headline score. A discount-rate-linked anchor "
        "would be a v2 of THIS policy, beside it, never an edit of it."),
    "what_would_replace_it": (
        "A measured long-run anchor from a full, dead-company-inclusive priced "
        "universe -- which is the same precondition every realized-return claim "
        "in this project is waiting on. Until then the constant is a constant "
        "and says so."),
    "the_arbitrariness_is_bounded": (
        "The anchor sets WHERE zero is; it does not set the ORDERING. Within "
        "one as-of date, ranking companies by the absolute leg is invariant to "
        "the anchor, because ln(m/a) is ln(m) minus a constant and the transform "
        "is monotone in it. The anchor changes the LEVEL, the SIGN and therefore "
        "the cross-date comparability -- which is precisely the content a "
        "peer-rank leg does not have."),
}


#: OWNER DECISION 1, 2026-09-21 -- the anchor sweep is a SENSITIVITY
#: DIAGNOSTIC, NOT AN OPTIMISER.
#:
#: THE PROHIBITION IS THE IMPORTANT HALF. The anchor CANNOT change within-date
#: ordering -- ln(m/a) is ln(m) minus a constant and the transform is monotone
#: in it -- so any attempt to "fit" the anchor against realized returns is not
#: choosing a better ranking. It is choosing WHERE ZERO SITS, and the only
#: returns available to choose it with come from 2,574 listings all alive in
#: 2026, whose median underperforms the total market by -7.45% at 12M. That
#: would be selecting the neutral point of the valuation scale using
#: contaminated evidence, and the contamination points in a known direction.
#:
#: So the sweep REPORTS and does not DECIDE. The anchor becomes an explicit
#: early ML CHALLENGER later, against a dead-company-inclusive priced universe
#: -- which is the same precondition every realized-return claim in this
#: project is waiting on.
ANCHOR_SWEEP_SPEC: dict[str, Any] = {
    "status": "SPECIFIED, NOT RUN",
    "purpose": "SENSITIVITY_DIAGNOSTIC",
    "explicitly_not": "ANCHOR_OPTIMISATION",
    "grid": (14.0, 16.0, 18.0, 20.0, 22.0),
    "held_fixed": (
        "the transform itself. theta = ln 2 and the -100 floor at 10x the "
        "anchor are stated as MULTIPLES of the anchor, so B is invariant to "
        "the level and the sweep moves one thing only."),
    "reports": (
        "median-issuer sign of the absolute leg, at each anchor, at each "
        "census date",
        "percentage of scored observations whose absolute-leg SIGN changes "
        "between adjacent anchors",
        "score-level displacement: the distribution of the change in the "
        "absolute leg, and of the change in the company score once the "
        "pillar's weight is applied",
    ),
    "must_not_report": (
        "any ranking of anchors by realized return, Sharpe, hit rate or "
        "information coefficient. Not as a headline, not as a footnote, not "
        "as a 'for interest' column.",
    ),
    "label": pit_factor_spec.SURVIVOR_ONLY,
    "how_to_read_the_result": (
        "If the median priced issuer's sign FLIPS inside 14-22, the level is "
        "load-bearing and needs a real decision rather than a convention. If "
        "it does not, the convention is safe to keep -- and saying so is then "
        "a measured statement rather than an assumption."),
    "future_challenger": (
        "The anchor is an explicit early ML CHALLENGER, not a frozen truth. "
        "Its challenge must run against a full, dead-company-inclusive priced "
        "universe; until that exists the challenge cannot be scored, and this "
        "spec says so instead of running it early on the data at hand."),
}


def anchor_policy() -> dict[str, Any]:
    """The anchor policy as a serialisable record, for a replay run to store."""
    return {
        "version": ANCHOR_POLICY_VERSION,
        "absolute_anchor_pe": PE_ABSOLUTE_ANCHOR,
        "anchor_source": ANCHOR_FIXED_MARKET_PE,
        "relative_anchor_source": ANCHOR_COHORT_MEDIAN,
        "knot_multiple": math.exp(TRANSFORM_THETA) * PE_ABSOLUTE_ANCHOR,
        "floor_multiple": TRANSFORM_FLOOR_MULTIPLE * PE_ABSOLUTE_ANCHOR,
        "price_basis": PRICE_RAW_AS_TRADED,
        "neutrality_semantics": ANCHOR_NEUTRALITY_SEMANTICS,
        "sweep": json.loads(json.dumps(ANCHOR_SWEEP_SPEC, default=list)),
        "price_basis_why": (
            "A point-in-time EPS is in the share basis of its own era. Apple "
            "2020-01-31: $309.51 / 11.89 = 26.0 as printed; the same 11.89 "
            "against today's split-adjusted ~$77 is 6.5 -- a mega cap wearing a "
            "deep-value multiple, wrong by exactly the 4:1 split."),
        "arbitrary": json.loads(json.dumps(WHAT_IS_ARBITRARY)),
    }


# ==========================================================================
# (4) THE CONVEX EXTREME-VALUATION TRANSFORM -- 'extreme_valuation_transform_v1'
#
# Restated from docs/SHAFFER-EQUITY-V2-CANDIDATE.md section 3, with B SOLVED here
# rather than pasted, so the constant cannot drift away from the condition
# that defines it.
# ==========================================================================

#: Points per log-unit near parity. ln(30/20) = ln(150/100) = 0.405465, so a
#: purely linear-in-log penalty charges the SAME 10.14 points for both moves --
#: ratio exactly 1.00 against a requirement of more than 1. That identity is
#: the log trap, and A alone cannot escape it.
TRANSFORM_A = 25.0

#: The knot. Quadratic acceleration begins at TWICE the anchor.
TRANSFORM_THETA = math.log(2.0)

#: The cheap-side saturation ceiling. Extreme cheapness is more often distress
#: than opportunity, so the cheap side saturates at +60 while the rich side
#: runs to -100. The asymmetry is the economics, not a normalisation slip.
TRANSFORM_C = 60.0

#: The -100 floor lands at TEN TIMES the anchor.
TRANSFORM_FLOOR_MULTIPLE = 10.0

TRANSFORM_FLOOR = -100.0


def solve_b(a: float = TRANSFORM_A, theta: float = TRANSFORM_THETA,
            floor_multiple: float = TRANSFORM_FLOOR_MULTIPLE,
            floor: float = TRANSFORM_FLOOR) -> float:
    """B, from the condition that defines it: S = -100 exactly at 10x the anchor.

    B IS NOT A FREE PARAMETER, and computing it here rather than pasting
    16.3825 is the difference between a constant and a derivation. Set
    u* = ln(floor_multiple); then

        A*u* + B*(u* - theta)^2 = |floor|   =>   B = (|floor| - A*u*) / (u*-theta)^2

    Independent of the anchor's LEVEL, because the condition is stated as a
    multiple of it.
    """
    u_star = math.log(float(floor_multiple))
    gap = u_star - float(theta)
    if gap <= 0:
        raise ValueError("the floor multiple must sit beyond the knot")
    return (abs(float(floor)) - float(a) * u_star) / (gap * gap)


TRANSFORM_B = solve_b()

#: The transform's parameters as ONE body for the freeze (v4 governance audit,
#: R4). PINNED_CURVE pins what the curve PRODUCES; this pins what it is MADE OF
#: -- C, the cheap-side ceiling, is not recoverable from PINNED_CURVE at all.
#: theta and B are computed floats, recorded to 12 dp so a last-ulp libm
#: difference can never read as drift. solve_b itself is NOT digested: a
#: function renders as its name, and a name is not a body.
TRANSFORM_PARAMS_V1: dict[str, float] = {
    "a": TRANSFORM_A,
    "theta_ln2": round(TRANSFORM_THETA, 12),
    "c": TRANSFORM_C,
    "floor_multiple": TRANSFORM_FLOOR_MULTIPLE,
    "floor": TRANSFORM_FLOOR,
    "b_solved": round(TRANSFORM_B, 12),
}


def valuation_score_detail(multiple: Optional[float],
                           anchor: float = PE_ABSOLUTE_ANCHOR) -> dict[str, Any]:
    """The transform, with every intermediate kept.

        u = ln(m / anchor)
        cheap (u <= 0):  S = +C * tanh((A/C) * (-u))            saturating
        rich  (u >  0):  S = -min(100, A*u + B*max(0, u-theta)^2)  accelerating

    REFUSES a non-positive multiple and returns `score=None` for it. That is
    the same refusal `pit_eps.price_earnings` makes and for the same reason: a
    negative multiple is a loss-making state, and abs() maps (-inf, 0) onto
    (0, +inf) REVERSED, so the worst loss-makers sort as the cheapest
    securities in the market. There is no flag to turn it off.
    """
    record: dict[str, Any] = {
        "score": None, "multiple": multiple, "anchor": anchor,
        "u": None, "branch": None, "linear_term": None, "convex_term": None,
        "clamped_at_floor": False, "reason": None,
        "transform_version": TRANSFORM_VERSION,
    }
    if multiple is None:
        record["reason"] = "no_multiple"
        return record
    m = float(multiple)
    if not math.isfinite(m) or m <= 0:
        record["reason"] = pit_eps.PE_UNAVAILABLE_LOSS if m <= 0 else "not_finite"
        return record
    a = float(anchor)
    if not math.isfinite(a) or a <= 0:
        raise ValueError(f"the valuation anchor must be positive, got {anchor!r}")

    u = math.log(m / a)
    record["u"] = u
    if u <= 0:
        record["branch"] = "cheap"
        record["score"] = TRANSFORM_C * math.tanh(
            (TRANSFORM_A / TRANSFORM_C) * (-u))
        return record

    record["branch"] = "rich"
    linear = TRANSFORM_A * u
    convex = TRANSFORM_B * max(0.0, u - TRANSFORM_THETA) ** 2
    penalty = linear + convex
    record["linear_term"] = linear
    record["convex_term"] = convex
    record["clamped_at_floor"] = penalty >= abs(TRANSFORM_FLOOR)
    record["score"] = -min(abs(TRANSFORM_FLOOR), penalty)
    return record


def valuation_score(multiple: Optional[float],
                    anchor: float = PE_ABSOLUTE_ANCHOR) -> Optional[float]:
    """Just the number. See `valuation_score_detail`."""
    return valuation_score_detail(multiple, anchor)["score"]


#: The published calibration, pinned. `validate()` recomputes every one of
#: these from the functions above; if a constant drifts, the module fails to
#: start rather than quietly rescoring the cross-section.
PINNED_CURVE: dict[str, Any] = {
    "anchor": 20.0,
    "b_published": 16.3825,
    "b_solved_to": 4,
    "cost_20_to_30": 10.1366,
    "cost_100_to_150": 25.0029,
    "ratio": 2.4666,
    "score_at_100x": -53.9905,
    "floor_at": 200.0,
    "tolerance": 1e-3,
    "why_the_ratio_matters": (
        "ln(30/20) = ln(150/100) exactly, so a linear-in-log penalty charges "
        "the same points for both moves -- ratio 1.00 against a requirement of "
        "more than 1. v1's own transform is WORSE than flat: 100*tanh(2*gap) "
        "charges 58.28 points for 20->30 and 1.78 for 100->150, so the second "
        "move costs one-thirty-third of the first. The requirement is not "
        "'penalise extremes', it is 'penalise them MORE', and only a convex "
        "branch does that."),
}


# ==========================================================================
# (5) EARNINGS_SIGN_CROSS -- the discontinuity as a first-class state
#
# 61.35% of entities cross zero at some point, so this is not an edge case
# being handled; it is most of the universe.
# ==========================================================================

#: The umbrella state name the score row carries. The two directed states
#: below are its members, and `CROSSING_STATES` is the membership test, so a
#: consumer never has to string-match a prefix.
EARNINGS_SIGN_CROSS = "EARNINGS_SIGN_CROSS"

TRANSITION_BOTH_POSITIVE = "both_positive"
TRANSITION_BOTH_NEGATIVE = "both_negative_loss_persisting"
TRANSITION_LOSS_TO_PROFIT = "earnings_sign_cross_loss_to_profit"
TRANSITION_PROFIT_TO_LOSS = "earnings_sign_cross_profit_to_loss"
#: NEITHER a zero nor a crossing: one endpoint simply is not there.
#: "Unavailable" and "zero" are different answers, and before 2026-09-21 this
#: case borrowed the zero states' labels -- so a missing figure read as a
#: reported zero in the state column.
TRANSITION_ENDPOINT_MISSING = "endpoint_missing"

#: The pair exists on both ends and CANNOT BE COMPARED, because the two figures
#: are not shown to be in the same share basis. Distinct from every value state
#: above: those describe earnings, this describes our evidence about earnings.
TRANSITION_SHARE_BASIS_UNRESOLVED = "share_basis_unresolved"

TRANSITION_FROM_ZERO = "from_reported_zero"
TRANSITION_TO_ZERO = "to_reported_zero"

#: Both endpoints positive, and the BASE is at or under one cent. The sign is
#: fine and the percentage is still meaningless: EPS is filed to the cent, so a
#: base of $0.005 is one rounding step from zero and $0.005 -> $0.50 prints
#: +9,900% that becomes +4,850% on the next cent of PRIOR earnings. `pit_eps`
#: already refuses to fold `near_zero_positive` silently into `positive` for
#: the valuation multiple, on exactly this argument; a growth rate off the same
#: base is the same quantisation artefact wearing a different unit.
TRANSITION_NEAR_ZERO_BASE = "near_zero_positive_base"

TRANSITION_STATES: tuple[str, ...] = (
    TRANSITION_BOTH_POSITIVE, TRANSITION_BOTH_NEGATIVE,
    TRANSITION_LOSS_TO_PROFIT, TRANSITION_PROFIT_TO_LOSS,
    TRANSITION_FROM_ZERO, TRANSITION_TO_ZERO, TRANSITION_NEAR_ZERO_BASE,
)

#: The two states that ARE `EARNINGS_SIGN_CROSS`. `pit_eps.SIGN_CROSSING` is
#: the FLAG the ingest already writes on `pit_eps_ttm`; this is the DIRECTED
#: reading of it, and the two must never drift -- the flag says a crossing
#: happened, these say which way.
CROSSING_STATES: frozenset[str] = frozenset(
    {TRANSITION_LOSS_TO_PROFIT, TRANSITION_PROFIT_TO_LOSS})

#: Direction for a both-negative pair. Taken from the LEVEL difference, never
#: from a ratio: a loss narrowing from -1.00 to -0.10 is a 90% improvement and
#: the percentage formula returns -90%.
DIR_LOSS_NARROWING = "loss_narrowing"
DIR_LOSS_DEEPENING = "loss_deepening"
DIR_UNCHANGED = "unchanged"

#: What `same_share_basis=None` means, and it is not "probably fine".
SHARE_BASIS_UNKNOWN = "share_basis_unknown_pair_arithmetic_refused"


@dataclass(frozen=True)
class EpsTransition:
    """A PAIR of TTM EPS figures, read as a state rather than as a ratio.

    `growth_pct` is None on every state but `both_positive`, and that is the
    entire contract. The other fields exist so a consumer that wanted a growth
    rate gets something usable instead of a silence.
    """

    state: str
    prev_eps: Optional[float]
    curr_eps: Optional[float]
    crossing: bool
    direction: Optional[str]
    growth_pct: Optional[float]
    level_change: Optional[float]
    permits_percentage_growth: bool
    refused_because: Optional[str]
    same_share_basis: Optional[bool]
    prev_sign_case: Optional[str]
    curr_sign_case: Optional[str]
    pe_leg: Optional[str]
    note: str = ""

    @property
    def is_sign_cross(self) -> bool:
        return self.state in CROSSING_STATES

    @property
    def discontinuity_state(self) -> Optional[str]:
        """`EARNINGS_SIGN_CROSS` when this pair is one, else None.

        The value a replay writes into the score row's discontinuity column,
        beside `pit_feature.source_tag_changed` -- the same kind of object for
        the same kind of reason.
        """
        return EARNINGS_SIGN_CROSS if self.is_sign_cross else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "transition_state": self.state,
            "discontinuity_state": self.discontinuity_state,
            "prev_eps": self.prev_eps,
            "curr_eps": self.curr_eps,
            "earnings_sign_cross": self.crossing,
            "direction": self.direction,
            "growth_pct": self.growth_pct,
            "level_change": self.level_change,
            "permits_percentage_growth": self.permits_percentage_growth,
            "refused_because": self.refused_because,
            "same_share_basis": self.same_share_basis,
            "prev_sign_case": self.prev_sign_case,
            "curr_sign_case": self.curr_sign_case,
            "pe_leg": self.pe_leg,
            "note": self.note,
        }


_REFUSAL_SIGN_CROSS = (
    "a sign crossing is a STATE, not a growth rate. (0.10 - (-0.10)) / -0.10 "
    "= -200%: a real number, correctly computed, pointing the wrong way about "
    "one of the most important things that can happen to a company")
_REFUSAL_NEGATIVE_BASE = (
    "a percentage growth rate exists only where the BASE is strictly positive. "
    "A loss narrowing from -1.00 to -0.10 is a 90% improvement and the formula "
    "returns -90% -- no crossing, no zero, and the sign still inverted")
_REFUSAL_NEAR_ZERO_BASE = (
    "the BASE is at or under one cent, the granularity EPS is filed at. "
    "$0.005 -> $0.50 is +9,900% and the next cent of prior earnings makes it "
    "+4,850%: the percentage is a quantisation artefact, not a growth rate")
_REFUSAL_ZERO = (
    "a reported zero is a REPORTED ZERO, not a small number. There is no "
    "percentage against it and there is no imputation of one")
_REFUSAL_BASIS = (
    "the two figures may not be in the same share basis. Apple FY2019 diluted "
    "EPS is 11.89 as printed and 2.97 in the next 10-K's comparative, four "
    "times apart across a 4:1 split, and a point-in-time selector at two period "
    "ends can legitimately return one of each")


def eps_transition_from_pair(pair: "pit_price_basis.EpsPair"):
    """THE ORDERING RULE, as a function rather than a docstring.

    OWNER DECISION 3, 2026-09-21: normalise the share basis FIRST, classify
    SECOND. This is the only supported way to classify a two-period EPS pair,
    and it exists because the sign-cross logic cannot catch a split.

    Apple across the 2020-08-31 4:1 split: the naive cross-filing pair is
    3.28 against 11.89. BOTH ARE STRICTLY POSITIVE, so `eps_transition` quite
    correctly classifies it `both_positive` and returns -72.4138% -- a real
    number, computed without error, describing a company that actually grew
    diluted EPS 10.44%. No sign cross, no zero, no near-zero base. Nothing
    downstream of the classifier can detect it, which is why the fence has to
    sit upstream of the classifier.

    A refused pair is passed through with `same_share_basis=None`, so the
    refusal arrives as UNKNOWN rather than as a percentage.
    """
    if not pair.classifiable:
        # Report WHY, specifically. A basis refusal is not a missing endpoint
        # and not a reported zero, and collapsing the three would hide the one
        # condition this whole decision exists to make visible.
        return EpsTransition(
            state=TRANSITION_SHARE_BASIS_UNRESOLVED,
            prev_eps=pair.prev_as_reported, curr_eps=pair.curr,
            crossing=False, direction=None, growth_pct=None,
            level_change=None, permits_percentage_growth=False,
            refused_because=(pair.reason
                             or pit_price_basis.REFUSED_SHARE_BASIS_UNKNOWN),
            same_share_basis=None,
            prev_sign_case=None, curr_sign_case=None, pe_leg=None,
            note=("pair path %s -- the two figures are not shown to share a "
                  "share basis, so NO arithmetic on them is permitted: not a "
                  "percentage, not a level difference, not a yield change"
                  % pair.path))
    return eps_transition(pair.prev_on_basis, pair.curr,
                          same_share_basis=pair.same_share_basis)


def eps_transition(prev_eps: Optional[float], curr_eps: Optional[float], *,
                   same_share_basis: Optional[bool] = None,
                   near_zero: float = pit_eps.EPS_NEAR_ZERO_ABS
                   ) -> EpsTransition:
    """The state of a two-period TTM EPS pair. NEVER a percentage across a sign.

    `same_share_basis` DEFAULTS TO None = UNKNOWN and refuses all pair
    arithmetic, because an unmeasured quantity is UNKNOWN and never assumed.
    Pass True only where a corporate-action table actually says so.

    The four-way value case of each endpoint comes from `pit_eps.sign_case`,
    imported rather than restated, so this module and the EPS ingest cannot
    disagree about what `near_zero_positive` means.
    """
    prev_case = None if prev_eps is None else pit_eps.sign_case(prev_eps, near_zero)
    curr_case = None if curr_eps is None else pit_eps.sign_case(curr_eps, near_zero)
    leg = pit_eps.pe_leg_for(curr_case) if curr_case else None

    if prev_eps is None or curr_eps is None:
        # The state used to be reported as TRANSITION_FROM_ZERO / _TO_ZERO
        # here, which contradicted this function's OWN refusal text: an absent
        # EPS was being labelled a reported zero in the one column a reader
        # scans. Corrected 2026-09-21 alongside owner decision 3.
        return EpsTransition(
            state=TRANSITION_ENDPOINT_MISSING,
            prev_eps=prev_eps, curr_eps=curr_eps, crossing=False, direction=None,
            growth_pct=None, level_change=None, permits_percentage_growth=False,
            refused_because=("an absent EPS is not a state of the pair; "
                             "'unavailable' and 'zero' are different answers"),
            same_share_basis=same_share_basis,
            prev_sign_case=prev_case, curr_sign_case=curr_case, pe_leg=leg,
            note=("one endpoint is missing -- no transition, no arithmetic. "
                  "Which endpoint: %s"
                  % ("prev" if prev_eps is None else "curr")))

    p, c = float(prev_eps), float(curr_eps)
    level = c - p

    if prev_case == pit_eps.SIGN_ZERO:
        state, direction, refusal = TRANSITION_FROM_ZERO, None, _REFUSAL_ZERO
    elif curr_case == pit_eps.SIGN_ZERO:
        state, direction, refusal = TRANSITION_TO_ZERO, None, _REFUSAL_ZERO
    elif p < 0 and c > 0:
        state, direction, refusal = (TRANSITION_LOSS_TO_PROFIT,
                                     TRANSITION_LOSS_TO_PROFIT,
                                     _REFUSAL_SIGN_CROSS)
    elif p > 0 and c < 0:
        state, direction, refusal = (TRANSITION_PROFIT_TO_LOSS,
                                     TRANSITION_PROFIT_TO_LOSS,
                                     _REFUSAL_SIGN_CROSS)
    elif p < 0 and c < 0:
        if level > 0:
            direction = DIR_LOSS_NARROWING
        elif level < 0:
            direction = DIR_LOSS_DEEPENING
        else:
            direction = DIR_UNCHANGED
        state, refusal = TRANSITION_BOTH_NEGATIVE, _REFUSAL_NEGATIVE_BASE
    elif prev_case == pit_eps.SIGN_NEAR_ZERO_POSITIVE:
        state, direction, refusal = (TRANSITION_NEAR_ZERO_BASE, None,
                                     _REFUSAL_NEAR_ZERO_BASE)
    else:
        state, direction, refusal = TRANSITION_BOTH_POSITIVE, None, None

    crossing = state in CROSSING_STATES
    permits = state == TRANSITION_BOTH_POSITIVE and same_share_basis is True
    if state == TRANSITION_BOTH_POSITIVE and same_share_basis is not True:
        refusal = _REFUSAL_BASIS
    basis_ok = same_share_basis is True

    return EpsTransition(
        state=state,
        prev_eps=p, curr_eps=c,
        crossing=crossing,
        direction=direction,
        growth_pct=((c - p) / p * 100.0) if permits else None,
        level_change=level if basis_ok else None,
        permits_percentage_growth=permits,
        refused_because=refusal,
        same_share_basis=same_share_basis,
        prev_sign_case=prev_case, curr_sign_case=curr_case, pe_leg=leg,
        note=("" if permits else
              (SHARE_BASIS_UNKNOWN if same_share_basis is None
               and state == TRANSITION_BOTH_POSITIVE else state)),
    )


def earnings_yield_change(prev_eps: Optional[float], curr_eps: Optional[float],
                          price: Optional[float], *,
                          same_share_basis: Optional[bool] = None
                          ) -> dict[str, Any]:
    """(eps_t - eps_t-1) / price -- defined and MONOTONE STRAIGHT THROUGH ZERO.

    What replaces the percentage where a percentage is not allowed. ONE price,
    the as-of price, deliberately: two prices would fold a price return into a
    quantity that is supposed to measure earnings.

    It is a RESEARCH CANDIDATE for the ML layer, never a valuation leg, and it
    is price-conditional and therefore SURVIVOR_ONLY_DIAGNOSTIC. The per-share
    LEVEL difference is offered beside it and is NOT cross-sectionally
    comparable -- a dollar of EPS means different things at $10 and $400 --
    which is exactly why the yield form is the one that goes forward.
    """
    transition = eps_transition(prev_eps, curr_eps,
                                same_share_basis=same_share_basis)
    record: dict[str, Any] = {
        "yield_change": None,
        "level_change": transition.level_change,
        "transition_state": transition.state,
        "earnings_sign_cross": transition.crossing,
        "sample_scope": SURVIVOR_ONLY,
        "price_basis": PRICE_RAW_AS_TRADED,
        "neutrality_semantics": ANCHOR_NEUTRALITY_SEMANTICS,
        "sweep": json.loads(json.dumps(ANCHOR_SWEEP_SPEC, default=list)),
        "role": pit_normalization.ROLE_RESEARCH,
        "monotone_through_zero": True,
        "reason": None,
        "why": ("the percentage is refused for this pair; the yield change is "
                "not, because dividing by a POSITIVE price cannot invert a "
                "sign the way dividing by a signed base does"),
    }
    if same_share_basis is not True:
        record["reason"] = SHARE_BASIS_UNKNOWN
        return record
    if prev_eps is None or curr_eps is None:
        record["reason"] = pit_eps.REASON_NO_TTM
        return record
    if price is None or float(price) <= 0:
        record["reason"] = "no_price"
        return record
    record["yield_change"] = (float(curr_eps) - float(prev_eps)) / float(price)
    return record


# ==========================================================================
# (6) SUBFACTOR-LEVEL DATA QUALITY
#
# docs/CONFIDENCE-FRAMEWORK.md section 2 and section 3, implemented at the granularity section 2
# asks for ("per factor") and stopping exactly where section 3 stops.
# ==========================================================================

QUALITY_RULE_PRODUCT = "product"
QUALITY_RULE_MIN = "min"
QUALITY_RULE_WEIGHTED = "weighted"
QUALITY_RULE_LEARNED = "learned"
QUALITY_RULE_UNDECIDED = "UNDECIDED"

QUALITY_RULES: tuple[str, ...] = (QUALITY_RULE_PRODUCT, QUALITY_RULE_MIN,
                                  QUALITY_RULE_WEIGHTED, QUALITY_RULE_LEARNED)

#: section 3, restated so the code carries the reason and not just the state.
QUALITY_RULE_STATUS: dict[str, Any] = {
    "status": QUALITY_RULE_UNDECIDED,
    "why": ("Q = D x C is NOT adopted pending measurement. A weak debt rung "
            "and a thin, incoherent peer set are plausibly symptoms of ONE "
            "latent cause -- small, obscure, thinly-covered companies -- and "
            "multiplying two correlated penalties charges that population "
            "twice for one weakness. 0.6 x 0.6 = 0.36 is an enormous penalty "
            "to levy on what may be a single underlying fact."),
    "what_decides_it": ("Pearson and Spearman correlation of the components, "
                        "conditional by sector, market-cap bucket, year and "
                        "distress state. Then product (if independent), min "
                        "(the binding-constraint reading), a weighted blend, "
                        "or learned."),
    "until_then": ("`data_quality()` computes EVERY candidate and adopts NONE "
                   "unless the caller names one. A caller that names `min` is "
                   "making an INTERIM choice and the record says so; the "
                   "components are stored, so the choice stays re-derivable "
                   "and the shrinkage stays learnable."),
    "learned_is_not_implemented": ("QUALITY_RULE_LEARNED is reserved and "
                                   "refuses to run. NO ML FITTING happens in "
                                   "this module."),
}


@dataclass(frozen=True)
class DataQuality:
    """`data_quality` for ONE subfactor. Q in [0,1], components kept.

    Stores what section 2 requires and nothing it forbids: the components, every
    candidate combination, the adopted rule (usually None) and the resulting Q
    (usually None). `F_effective` is computed by `effective_value`, which
    returns all THREE of F_raw, Q and F_effective -- never the product alone,
    because the shrinkage is itself a hypothesis and a stored product is a
    hypothesis that can no longer be tested.
    """

    subfactor_key: str
    components: dict[str, float]
    declared: tuple[str, ...]
    missing: tuple[str, ...]
    candidates: dict[str, Optional[float]]
    adopted_rule: Optional[str]
    quality: Optional[float]
    rule_status: str = QUALITY_RULE_UNDECIDED

    @property
    def complete(self) -> bool:
        return not self.missing

    def as_dict(self) -> dict[str, Any]:
        return {
            "subfactor": self.subfactor_key,
            "components": dict(self.components),
            "declared_components": list(self.declared),
            "missing_components": list(self.missing),
            "candidate_rules": dict(self.candidates),
            "adopted_rule": self.adopted_rule,
            "data_quality": self.quality,
            "rule_status": self.rule_status,
        }


def data_quality(subfactor_key: str,
                 components: Mapping[str, Any],
                 rule: Optional[str] = None,
                 weights: Optional[Mapping[str, float]] = None) -> DataQuality:
    """`data_quality` for one subfactor, with the combination rule left open.

    Only the components the subfactor DECLARES are read; a component that does
    not apply to this subfactor is dropped rather than defaulted to 1.0, and a
    declared component the caller did not supply is reported in `missing`
    rather than imputed. That is the whole reason quality sits at subfactor
    level: the absolute leg has no peers, so `peer_coherence` is not a perfect
    score for it, it is NOT A QUESTION ABOUT IT.

    `rule=None` returns every candidate and adopts none -- which is the honest
    state of `docs/CONFIDENCE-FRAMEWORK.md` section 3 today. Naming a rule is a
    deliberate interim act and lands on the record.
    """
    item = subfactor(subfactor_key)
    declared = item.quality_components
    supplied: dict[str, float] = {}
    for name in declared:
        if name not in components:
            continue
        value = float(components[name])
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError(
                f"{subfactor_key}.{name} must be a finite value in [0,1], got "
                f"{components[name]!r} -- a quality component outside the unit "
                f"interval is a scaling error, not a strong opinion")
        supplied[name] = value
    extra = [k for k in components if k not in declared]
    if extra:
        raise ValueError(
            f"{subfactor_key} does not declare quality component(s) "
            f"{sorted(extra)!r}; it declares {list(declared)}. "
            f"{'peer_coherence is not a question about a leg that consults no peers. ' if COMPONENT_PEER_COHERENCE in extra else ''}"
            f"Supplying one anyway would put a number where there is no question.")
    missing = tuple(n for n in declared if n not in supplied)

    values = [supplied[n] for n in declared if n in supplied]
    candidates: dict[str, Optional[float]] = {r: None for r in QUALITY_RULES}
    if values and not missing:
        product = 1.0
        for v in values:
            product *= v
        candidates[QUALITY_RULE_PRODUCT] = product
        candidates[QUALITY_RULE_MIN] = min(values)
        table = {n: float((weights or {}).get(n, 1.0)) for n in declared}
        total = sum(table.values())
        candidates[QUALITY_RULE_WEIGHTED] = (
            sum(table[n] * supplied[n] for n in declared) / total
            if total > 0 else None)

    adopted: Optional[str] = None
    quality: Optional[float] = None
    if rule is not None:
        key = str(rule)
        if key == QUALITY_RULE_LEARNED:
            raise ValueError(
                "QUALITY_RULE_LEARNED is reserved and refuses to run: nothing "
                "in this lineage has been fitted, and shipping an uncalibrated "
                "model under a label that claims calibration is the one thing "
                "the normalization vocabulary exists to prevent")
        if key not in QUALITY_RULES:
            raise ValueError(
                f"unknown quality rule {rule!r}; candidates are "
                f"{', '.join(QUALITY_RULES)}")
        adopted = key
        quality = candidates.get(key)

    return DataQuality(
        subfactor_key=subfactor_key, components=supplied, declared=declared,
        missing=missing, candidates=candidates, adopted_rule=adopted,
        quality=quality)


def effective_value(raw: Optional[float],
                    quality: Optional[DataQuality]) -> dict[str, Any]:
    """F_raw, Q and F_effective -- ALL THREE, never the product alone.

    section 2: "Store all three, never only the damped number ... because the
    shrinkage itself is a hypothesis. Q = 0.70 might belong at 0.85 or 0.40,
    and that must be learnable -- which is impossible if only the product was
    stored."

    Value shrinkage, not weight shrinkage: a damped factor becomes less bullish
    AND less bearish, which moves a noisy signal toward neutral rather than
    toward a verdict. With no adopted rule, F_effective is None and F_raw
    stands -- degrading a factor by a number nobody has chosen would be worse
    than not degrading it.
    """
    q = quality.quality if quality is not None else None
    return {
        "f_raw": raw,
        "data_quality": q,
        "quality_rule": quality.adopted_rule if quality is not None else None,
        "quality_components": dict(quality.components) if quality else {},
        "f_effective": (None if raw is None or q is None else float(raw) * q),
        "shrinkage": "value_shrinkage_toward_neutral",
        "rule_status": QUALITY_RULE_UNDECIDED,
    }


#: The display bands of `docs/CONFIDENCE-FRAMEWORK.md`'s example panel. Bands,
#: not a grade: the number is what is stored and this is what is shown.
QUALITY_BANDS: tuple[tuple[float, str], ...] = (
    (0.85, "High"), (0.65, "Medium"), (0.40, "Low"), (0.0, "Very low"),
)


def quality_band(quality: Optional[float]) -> str:
    """'High' / 'Medium' / 'Low' / 'Very low', or 'Undecided' with no rule."""
    if quality is None:
        return "Undecided (no combination rule adopted)"
    for floor, label in QUALITY_BANDS:
        if float(quality) >= floor:
            return label
    return QUALITY_BANDS[-1][1]


# ==========================================================================
# (7) ASSEMBLY -- the pillar as a FUNCTION of its subfactors
# ==========================================================================

@dataclass(frozen=True)
class SubfactorResult:
    """One subfactor's outcome. Falsy when absent, so `presence()` reads it."""

    key: str
    available: bool
    raw_score: Optional[float] = None
    multiple: Optional[float] = None
    anchor: Optional[float] = None
    anchor_source: str = ""
    quality: Optional[DataQuality] = None
    unavailable_reason: Optional[str] = None
    cohort_n: Optional[int] = None
    cohort_rung: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.available and self.raw_score is not None
                    and math.isfinite(float(self.raw_score)))

    def as_dict(self) -> dict[str, Any]:
        out = {
            "subfactor": self.key,
            "available": bool(self),
            "raw_score": self.raw_score,
            "multiple": self.multiple,
            "anchor": self.anchor,
            "anchor_source": self.anchor_source,
            "unavailable_reason": self.unavailable_reason,
            "cohort_n": self.cohort_n,
            "cohort_rung": self.cohort_rung,
        }
        out.update(effective_value(self.raw_score, self.quality))
        if self.quality is not None:
            out["quality_detail"] = self.quality.as_dict()
        return out


@dataclass(frozen=True)
class ValuationPillar:
    """The valuation pillar, assembled from whichever subfactors existed."""

    presence: Presence
    results: dict[str, SubfactorResult]
    effective_weights: dict[str, float]
    contributions: dict[str, float]
    pillar_score: Optional[float]
    pillar_quality: Optional[float]
    reason: Optional[str]
    spec_version: str = VALUATION_SPEC_VERSION

    @property
    def available(self) -> bool:
        return self.presence.is_present and self.pillar_score is not None

    @property
    def mask(self) -> str:
        return self.presence.mask

    def as_dict(self) -> dict[str, Any]:
        out = dict(self.presence.as_dict())
        out.update({
            "valuation_spec_version": self.spec_version,
            "subfactor_effective_weights": dict(self.effective_weights),
            "subfactor_contributions": dict(self.contributions),
            "valuation_pillar_score": self.pillar_score,
            "valuation_pillar_data_quality": self.pillar_quality,
            "unavailable_reason": self.reason,
            "subfactors": [self.results[k].as_dict() for k in SUBFACTOR_KEYS
                           if k in self.results],
        })
        return out

    def for_score_signature(self) -> dict[str, Any]:
        """What to hand `pit_score_signature.score_v2`.

        Returns {'factor_score', 'reason'} for the valuation pillar only. The
        caller merges it into the four-pillar mapping; this module does not
        own growth, profitability or debt and does not pretend to.
        """
        return {
            "factor_score": self.pillar_score if self.available else None,
            "reason": self.reason,
        }


def pillar_reason(results: Mapping[str, SubfactorResult],
                  pres: Presence) -> Optional[str]:
    """The `unavailable_reason` for an ABSENT valuation pillar, or None.

    `pit_store`'s vocabulary, never a new spelling. The precedence is
    deliberate: the owner's first-class outcome wins where any subfactor
    reported it, because "no admissible peer set" is a structural finding and
    "a primitive was missing" is a coverage one, and the structural finding is
    the one a reader must not have to dig for.
    """
    if pres.is_present:
        return None
    offered = [results[k].unavailable_reason for k in SUBFACTOR_KEYS
               if k in results and results[k].unavailable_reason]
    if not offered:
        return None
    if pit_store.REASON_VALUATION_PEER_SET_INSUFFICIENT in offered:
        return pit_store.REASON_VALUATION_PEER_SET_INSUFFICIENT
    return offered[0]


def valuation_pillar(results: Mapping[str, SubfactorResult],
                     quality_rule: Optional[str] = None) -> ValuationPillar:
    """The pillar as a FUNCTION of its subfactors. The owner's change, executed.

    The score renormalises over the PRESENT SUBFACTOR WEIGHTS -- the
    within-pillar renormalisation `pit_score_signature.score_v2` explicitly
    permits, because three readings of the valuation question are three
    measurements of one thing. Losing one of them must not make the pillar
    vanish, and it must not make the survivors mean less than they say.

    `pillar_quality` is the effective-weight-weighted mean of the present
    subfactors' adopted Q, and is None unless EVERY present subfactor adopted
    one -- a pillar quality assembled from a mixture of "0.72" and "we have not
    decided" would be a number with a hole in it.
    """
    unknown = [k for k in results if k not in SUBFACTORS]
    if unknown:
        raise ValueError(f"unknown valuation subfactor(s) {sorted(unknown)!r}")

    pres = presence(results)
    if not pres.is_present:
        return ValuationPillar(
            presence=pres, results=dict(results), effective_weights={},
            contributions={}, pillar_score=None, pillar_quality=None,
            reason=pillar_reason(results, pres))

    total = sum(SUBFACTORS[k].weight for k in pres.present)
    used = {k: SUBFACTORS[k].weight / total for k in pres.present}
    contributions = {k: used[k] * float(results[k].raw_score)
                     for k in pres.present}
    score = max(-100.0, min(100.0, sum(contributions.values())))

    qualities = [(used[k], results[k].quality.quality)
                 for k in pres.present
                 if results[k].quality is not None
                 and results[k].quality.quality is not None]
    pillar_q: Optional[float] = None
    if len(qualities) == len(pres.present) and qualities:
        pillar_q = sum(w * q for w, q in qualities)

    return ValuationPillar(
        presence=pres, results=dict(results), effective_weights=used,
        contributions=contributions, pillar_score=score, pillar_quality=pillar_q,
        reason=None)


def stratify_key(pillar: ValuationPillar,
                 score_result: Any) -> tuple[str, str, str]:
    """(model_version, score_signature, subfactor_mask) -- the evaluation unit.

    `pit_score_signature.pool` already refuses to average across signatures.
    The mask EXTENDS that key rather than replacing it, and the reason is the
    consequence stated in the module docstring: two rows can both read `VGPD`
    and be built from three valuation readings and one. Pooling them averages
    two different models exactly as pooling VGPD with GPD does, and the mask is
    the column that makes the difference visible to a GROUP BY.
    """
    return (getattr(score_result, "model_version", ""),
            getattr(score_result, "signature", ""),
            pillar.mask)


# ==========================================================================
# (8) WIRING -- the four canonical names, mapped to the objects that exist
# ==========================================================================

VOCABULARY_MAP: dict[str, Any] = {
    "data_quality": {
        "status": "NEW -- this module, at SUBFACTOR granularity",
        "object": "pit_valuation_spec.data_quality -> DataQuality",
        "type": "continuous [0,1] per subfactor; combination rule UNDECIDED",
        "stored": ["f_raw", "data_quality", "f_effective", "quality_components",
                   "quality_rule"],
        "why_subfactor_not_pillar": (
            "PeerCoherence is undefined for the absolute leg, which consults no "
            "peers. A pillar-level Q would have to average a peer-coherence "
            "number into a reading that never formed a cohort."),
        "spec": "docs/CONFIDENCE-FRAMEWORK.md sections 2 and 3",
    },
    "score_completeness": {
        "status": "BUILT -- pit_score_signature. EXTENDED here, not replaced.",
        "object": "pit_score_signature.score_v2 -> PillarScore",
        "type": "structural/categorical: signature, marker, weight coverage",
        "extension": ("`subfactor_mask` -- V[PE=1,PEPeer=1,EVEBITDA=0] -- joins "
                      "the row and the pooling key. The SIGNATURE STAYS STABLE "
                      "at VGPD for a degraded valuation pillar, which is the "
                      "design: a continuous quality measure must not trigger a "
                      "discrete signature change every time a cohort thins."),
        "call": ("pit_score_signature.score_v2(scores, reasons={'valuation': "
                 "pillar.reason}) with scores['valuation'] = "
                 "pillar.for_score_signature()['factor_score']"),
    },
    "forecast_evidence": {
        "status": "BUILT -- pit_invariants.evidence_status. Called, not restated.",
        "object": "pit_invariants.EvidenceVerdict",
        "type": "per-horizon verdict: SUFFICIENT / INSUFFICIENT_EVIDENCE",
        "extension": ("the subfactor mask joins the STRATIFICATION KEY. A "
                      "verdict pooled across masks is a verdict about two "
                      "populations."),
        "note": ("12M is brutal here: 160.7 independent observations detect "
                 "nothing below rho ~ 0.155 while equity factor ICs live at "
                 "0.02-0.06, so a 12M rank IC from this store would be noise "
                 "with a decimal point on it."),
    },
    "hedge_evidence": {
        "status": "BUILT -- pit_intermediates.hedge_research_status. UNCHANGED.",
        "object": "LIVE_RECOMMENDATION_ONLY | HISTORICALLY_VALIDATED",
        "type": "temporal: prospective-only before the option archive start",
        "extension": "NONE, deliberately.",
        "why_none": ("Hedge evidence is a statement about when the option "
                     "archive begins (2026-09-20). It has nothing to do with "
                     "which valuation subfactors resolved, and giving it a "
                     "valuation flavour would be inventing a fifth name wearing "
                     "a fourth name's clothes."),
    },
}


def forecast_evidence(mask: str, n_effective: float, n_folds: int,
                      horizon: str = "12M",
                      target_effect: float = 0.05) -> dict[str, Any]:
    """`pit_invariants.evidence_status`, stratified by the subfactor mask.

    Calls the existing verdict function and attaches the mask. It does not
    reimplement the protocol, and it does not soften it: a cell that cannot
    distinguish the effect of interest from zero returns INSUFFICIENT_EVIDENCE
    whatever the mask says.
    """
    parse_mask(mask)                                  # raises on a bad mask
    verdict = pit_invariants.evidence_status(
        n_effective, n_folds, target_effect=target_effect)
    return {
        "horizon": horizon,
        "subfactor_mask": mask,
        "forecast_evidence": verdict.status,
        "n_effective": verdict.n_effective,
        "n_folds": verdict.n_folds,
        "detectable_rho": verdict.detectable,
        "reason": verdict.reason,
        "stratified_by": ["model_version", "score_signature", "subfactor_mask"],
    }


def hedge_evidence(as_of: str) -> dict[str, Any]:
    """`pit_intermediates.hedge_research_status`, passed through unchanged."""
    status = pit_intermediates.hedge_research_status(as_of)
    return {
        "as_of": str(as_of)[:10],
        "hedge_evidence": status["research_status"],
        "valuation_subfactors_are_irrelevant_here": True,
        "detail": status,
    }


def confidence_view(pillar: ValuationPillar, score_result: Any,
                    as_of: str,
                    evidence: Optional[Mapping[str, Any]] = None
                    ) -> dict[str, Any]:
    """The four dimensions, side by side and NEVER multiplied together.

    section 4: `ShafferScore x OverallConfidence` is forbidden by default. The score
    and its quality are DIFFERENT PIECES OF INFORMATION, and collapsing them
    destroys the distinction between "we are confident this is mediocre" and
    "we are unsure whether this is excellent." Note the deliberate asymmetry
    with section 2, which DOES shrink an input factor: shrinking an input is a
    statement about that input's reliability; shrinking the final score is a
    statement about the whole belief.

    There is no function in this module that multiplies a company score by a
    confidence, and `validate()` checks that the view carries no such product.
    """
    return {
        "as_of": str(as_of)[:10],
        "shaffer_score": getattr(score_result, "company_score", None),
        "score_completeness": {
            "score_signature": getattr(score_result, "signature", None),
            "subfactor_mask": pillar.mask,
            "presence_state": pillar.presence.state,
            "availability": pillar.presence.availability,
            "partial_score_marker": getattr(score_result,
                                            "partial_score_marker", None),
            "original_weight_coverage": getattr(
                score_result, "original_weight_coverage", None),
        },
        "data_quality": {
            "valuation_pillar": pillar.pillar_quality,
            "band": quality_band(pillar.pillar_quality),
            "rule_status": QUALITY_RULE_UNDECIDED,
            "per_subfactor": {
                k: (pillar.results[k].quality.as_dict()
                    if pillar.results[k].quality is not None else None)
                for k in pillar.presence.present},
        },
        "forecast_evidence": dict(evidence) if evidence else None,
        "hedge_evidence": hedge_evidence(as_of),
        "never_multiplied": (
            "score and quality are reported separately; this view contains no "
            "score x confidence product (docs/CONFIDENCE-FRAMEWORK.md section 4)"),
    }


def render_confidence_view(view: Mapping[str, Any]) -> str:
    """The panel from `docs/CONFIDENCE-FRAMEWORK.md` section 1, filled from real objects."""
    score = view.get("shaffer_score")
    completeness = view["score_completeness"]
    quality = view["data_quality"]
    evidence = view.get("forecast_evidence") or {}
    lines = [
        f"  {'Shaffer Score:':24s}{'' if score is None else f'{score:+.2f}'}",
        f"  {'Data quality:':24s}{quality['band']}",
        f"  {'Score completeness:':24s}"
        f"{completeness['score_signature']}  {completeness['subfactor_mask']}"
        f"  ({completeness['presence_state']})",
    ]
    if evidence:
        lines.append(f"  {evidence.get('horizon', '?') + ' forecast evidence:':24s}"
                     f"{evidence.get('forecast_evidence')}")
    lines.append(f"  {'Hedge evidence:':24s}"
                 f"{view['hedge_evidence']['hedge_evidence']}")
    return "\n".join(lines)


# ==========================================================================
# (9) STORAGE -- SPECIFIED, NOT APPLIED
#
# Written for ANOTHER process to run later. Nothing in this module opens a
# connection, and `pit_score` stays empty.
# ==========================================================================

#: (column, type) pairs -- the shape `pit_store.MIGRATIONS` takes and the shape
#: `pit_score_signature.PIT_SCORE_SIGNATURE_MIGRATIONS` already uses, so the two
#: patches MERGE into one list under the "pit_score" key instead of needing new
#: plumbing. NO DEFAULT and no NOT NULL on any of them, for that module's
#: reason: a default lets a writer that forgot the column produce a row that
#: looks deliberate.
PIT_SCORE_SUBFACTOR_MIGRATIONS: list[tuple[str, str]] = [
    ("subfactor_mask", "TEXT"),
    ("valuation_presence_state", "TEXT"),
    ("valuation_data_quality", "REAL"),
    ("valuation_quality_rule", "TEXT"),
    ("valuation_subfactors_json", "TEXT"),
    ("earnings_discontinuity_state", "TEXT"),
]

#: The columns with the reason each one exists, plus the one index an
#: evaluation path that separates by MASK as well as by signature will actually
#: use. Free to apply: `pit_score` holds 0 rows, so each ALTER is a header
#: rewrite and the index is an empty b-tree. NOT APPLIED HERE.
PIT_SCORE_SUBFACTOR_COLUMN_DDL = """
-- subfactor_mask: V[PE=1,PEPeer=1,EVEBITDA=0]. The DETAILED truth behind one
--   letter of score_signature. `V` says the pillar FUNCTIONED, and this
--   says what it functioned ON. Two different facts about the same row.
-- valuation_presence_state: PRESENT_FULL | PRESENT_DEGRADED | ABSENT, mapping
--   onto pit_store's own complete / partial / unavailable vocabulary.
-- valuation_data_quality: Q in [0,1] for the pillar; NULL while the
--   combination rule is UNDECIDED. That NULL is a true statement -- "no rule
--   adopted" -- and not a missing measurement.
-- valuation_quality_rule: WHICH candidate rule produced it, so the shrinkage
--   stays re-derivable from the stored components and therefore learnable.
-- valuation_subfactors_json: one entry per subfactor with its raw score, its
--   anchor, its quality components and its unavailable_reason.
-- earnings_discontinuity_state: EARNINGS_SIGN_CROSS or NULL. Read exactly as
--   pit_feature.source_tag_changed is read -- a change-based factor MUST drop
--   the step, never treat it as zero.
ALTER TABLE pit_score ADD COLUMN subfactor_mask TEXT;
ALTER TABLE pit_score ADD COLUMN valuation_presence_state TEXT;
ALTER TABLE pit_score ADD COLUMN valuation_data_quality REAL;
ALTER TABLE pit_score ADD COLUMN valuation_quality_rule TEXT;
ALTER TABLE pit_score ADD COLUMN valuation_subfactors_json TEXT;
ALTER TABLE pit_score ADD COLUMN earnings_discontinuity_state TEXT;

-- The evaluation path reads one (signature, mask) cell at a time.
CREATE INDEX IF NOT EXISTS idx_pit_score_subfactor_mask
    ON pit_score (as_of_date, model_version, score_signature, subfactor_mask);
"""


def migration_patch() -> dict[str, list[tuple[str, str]]]:
    """The exact entry to MERGE into `pit_store.MIGRATIONS` under "pit_score".

    The signature module's pairs come FIRST and these after, in the order they
    must be applied: `subfactor_mask` is only meaningful beside a
    `score_signature`, and the index spans both. `pit_store.apply_migrations`
    is already idempotent and already runs from `init_db`, so no new plumbing
    is needed anywhere. Applies nothing.
    """
    return {"pit_score": (
        [tuple(pair) for pair in
         pit_score_signature.PIT_SCORE_SIGNATURE_MIGRATIONS]
        + [tuple(pair) for pair in PIT_SCORE_SUBFACTOR_MIGRATIONS])}


def migration_sql() -> tuple[str, ...]:
    """The statements, one per element, commentary stripped. Executes nothing.

    The DDL above is written to be READ -- the comments are the reason the
    columns exist -- so this strips them rather than leaving a caller to paste
    a prose block into `execute`.
    """
    body = "\n".join(line for line in PIT_SCORE_SUBFACTOR_COLUMN_DDL.splitlines()
                     if line.strip() and not line.strip().startswith("--"))
    return tuple(s.strip() for s in body.split(";") if s.strip())


def storage_note() -> dict[str, Any]:
    return {
        "applied": False,
        "why_not": ("This module is declarative and the store is 8.97 GiB on a "
                    "volume with 9.67 GiB free at 96% used. A migration is a "
                    "write, a write is a WAL, and this project has already "
                    "filled the volume once with a 9.3 GB WAL. Nothing here "
                    "opens a connection."),
        "columns": [name for name, _ in PIT_SCORE_SUBFACTOR_MIGRATIONS],
        "beside_not_instead_of": [
            name for name, _ in pit_score_signature.PIT_SCORE_SIGNATURE_MIGRATIONS],
        "statements": list(migration_sql()),
        "grouping_key": ["model_version", "score_signature", "subfactor_mask"],
        "json_column_why": ("valuation_subfactors_json carries one entry per "
                            "subfactor with its raw score, its anchor, its "
                            "quality components and its unavailable_reason -- "
                            "the same argument that makes pit_feature."
                            "sources_json an ARRAY: one row of flat columns "
                            "could never answer 'what exactly was this pillar "
                            "built from'."),
    }


# ==========================================================================
# (10) MEASURED CONTEXT AND KNOWN LIMITATIONS
# ==========================================================================

MEASURED: dict[str, Any] = {
    "measured_on": "2026-09-21",
    "sample_scope": {
        "cohorts_and_coverage": FULL_UNIVERSE,
        "anything_touching_price_or_return": SURVIVOR_ONLY,
        "survivor_caveat": ("2,574 listings, all alive in 2026; the median "
                            "survivor underperforms the total market by -7.45% "
                            "at 12M"),
    },
    "peer_sets": {"n": 39038, "memberships": 3069260,
                  "entity_dates": 1238663, "month_ends": 165,
                  "span": "2013-01-31..2026-09-18"},
    "ev_ebitda_cohort": {"median": 1, "pct_ge_3": 34.2, "pct_ge_12": 7.0,
                         "sic4_pct_ge_12": 2.4},
    "pe_cohort": {"median": 5, "pct_ge_3": 78.3, "pct_ge_12": 24.2,
                  "sic4_pct_ge_12": 13.4,
                  "usable_pe_share_of_priced_universe": [62.4, 57.0, 59.7]},
    "ebitda_only_cohort": {"median": 13, "pct_ge_3": 95.2, "pct_ge_12": 56.0,
                           "note": "the locked correction: the EBITDA benchmark "
                                   "cohort needs EBITDA, not a price"},
    "staleness_v2": {"monthly_spread_points": [44.17, 0.67],
                     "pooled_share_coverage_pct": [53.70, 72.25],
                     "ev_ebitda_pct_ge_3": [34.2, 46.0],
                     "consequence": "debt is binding again"},
    "total_debt_ladder": {"v1_pct_of_base": [15.9, 17.8, 14.5],
                          "v2_pct_of_base": [37.9, 42.3, 37.7],
                          "on_lower_bound_rung_pct": 94,
                          "lower_bound_understates_by_median_pct": 14,
                          "exact_rung_pct_of_base": [2.1, 2.5]},
    "eps_sign_crossing": {"entities_touched_pct": 61.35,
                          "why_it_matters": "the discontinuity is most of the "
                                            "universe, not an edge case"},
    "earnings_negativity_fy2014": {"n": 2756, "ni_le_zero_pct": 49.2,
                                   "ebitda_le_zero_pct": 37.8,
                                   "both_pct": 36.5,
                                   "ebitda_but_no_earnings_pct": 12.7},
    "peer_coherence": {"effective_sic4_count_median":
                       {"sic4": 1.00, "sic3": 1.88, "sic2": 3.37,
                        "office": 18.01},
                       "office_ban_cost_pct_of_entity_dates": 4.62},
}

# ==========================================================================
# (4b) DEBT-RUNG ELIGIBILITY FOR EV/EBITDA -- 'DEBT_RUNG_EVEBITDA_V1'
#
# OWNER DECISION, 2026-09-21, closing freeze gate 5. A HARD ELIGIBILITY GATE,
# NOT A CONTINUOUS QUALITY MULTIPLIER.
# ==========================================================================

DEBT_RUNG_POLICY_VERSION = "DEBT_RUNG_EVEBITDA_V1"

#: The refusal a debt rung earns when it is not admitted to the scored factor.
#: One code for both non-eligible statuses is NOT enough -- "we measured it and
#: it failed" and "we could not measure it" are different answers and get
#: different codes, so a later reader can tell which rungs were judged.
REASON_DEBT_RUNG_REJECTED = "debt_rung_rejected_measured"
REASON_DEBT_RUNG_UNDETERMINED = "debt_rung_undetermined"

#: Validated as ordering-preserving where the decision is actually taken, and
#: admitted to the scored EV/EBITDA factor.
DEBT_ELIGIBLE = "ELIGIBLE"

#: MEASURED, and the measurement was unfavourable. This is a verdict.
DEBT_REJECTED_MEASURED = "REJECTED_MEASURED"

#: Not enough validation pairs exist in this store to reach any verdict. This
#: is the ABSENCE of a verdict, and the distinction from the line above is the
#: reason there are three statuses rather than two: "rejected because the
#: evidence was unfavourable" and "we have no evidence" must never collapse
#: into one bucket, because only one of them is informative about the rung.
DEBT_UNDETERMINED = "UNDETERMINED"

DEBT_STATUSES: tuple[str, ...] = (
    DEBT_ELIGIBLE, DEBT_REJECTED_MEASURED, DEBT_UNDETERMINED,
)

#: The rule itself. Keys are `pit_policy` ladder rung keys, quoted exactly.
DEBT_RUNG_EVEBITDA_V1: dict[str, str] = {
    "SUM(LongTermDebtNoncurrent+LongTermDebtCurrent+ShortTermBorrowings)":
        DEBT_ELIGIBLE,
    "SUM(LongTermDebtNoncurrent+LongTermDebtCurrent)": DEBT_ELIGIBLE,
    "LongTermDebtNoncurrent": DEBT_REJECTED_MEASURED,
    "LongTermDebt": DEBT_UNDETERMINED,
    "LongTermDebtAndCapitalLeaseObligations": DEBT_UNDETERMINED,
    "DebtLongtermAndShorttermCombinedAmount": DEBT_UNDETERMINED,
}

#: Why each rung sits where it sits. Every number here is READ FROM the Gate 2
#: archive run by `debt_rung_evidence()`; none is re-typed as a literal that
#: could drift away from the measurement that produced it.
DEBT_RUNG_REASONS: dict[str, str] = {
    "SUM(LongTermDebtNoncurrent+LongTermDebtCurrent+ShortTermBorrowings)": (
        "THE REFERENCE. Exact three-way total, eligible by construction -- it "
        "is the quantity every other rung is measured against. Rare: it lands "
        "on 2.41 / 2.53 / 2.49% of base and only 585 issuers in the entire "
        "store can ever resolve it."),
    "SUM(LongTermDebtNoncurrent+LongTermDebtCurrent)": (
        "ELIGIBLE. Conceptually DIFFERENT from the lower-bound tags, not "
        "merely better-scoring: current debt plus noncurrent debt is an "
        "economically defensible RECONSTRUCTION of total debt, rather than "
        "whatever debt-like number happened to be available. The measurement "
        "supports admitting it while acknowledging its tail disagreement -- "
        "14.13% cheap-decile churn is a real cost, accepted with its eyes "
        "open, in exchange for +14.5 to +16.6 points of base."),
    "LongTermDebtNoncurrent": (
        "REJECTED, AND THE REJECTION IS A MEASUREMENT. Not because Spearman "
        "0.9662 is bad -- it is not -- but because the DECISION-RELEVANT part "
        "of the distribution disagrees materially: 22.55% of the cheapest "
        "decile is a different set of companies. A valuation factor exists "
        "partly to identify cheap and expensive tails, so the churn matters "
        "more here than the headline rho. And the understatement is SECTORAL "
        "rather than random -- median ratio 0.642 in Finance/Insurance/Real "
        "Estate against 0.932 in Services -- which makes it structural. Debt "
        "understated => EV understated => EV/EBITDA understated: it "
        "MANUFACTURES CHEAPNESS UNEVENLY ACROSS THE CROSS-SECTION, which is "
        "precisely the error a valuation factor cannot absorb."),
    "LongTermDebt": (
        "UNDETERMINED. 224 issuer-dates but only 149 within-date pairs, below "
        "the pair floor. Note what this rung would have bought: 61.8% of the "
        "entities it uniquely rescues at 2019-06-28 are Finance/Insurance/"
        "Real Estate -- the one division where the understatement is worst -- "
        "and this store cannot tell you whether that coverage is trustworthy. "
        "That is a finding, not a gap to paper over."),
    "LongTermDebtAndCapitalLeaseObligations": (
        "UNDETERMINED. 113 within-date pairs, below the floor."),
    "DebtLongtermAndShorttermCombinedAmount": (
        "UNDETERMINED, AND NOTE THE SHAPE OF IT: this rung's bound is EXACT, "
        "and it is still undetermined. 23 issuer-dates produce ZERO "
        "within-date pairs, so nothing can be ranked. Eligibility here is "
        "about VALIDATED ORDERING BEHAVIOUR, not about a tag's nominal "
        "completeness -- an exact-bound rung has to earn its place the same "
        "way a lower-bound one does."),
}

#: The owner's reasoning for refusing the continuous mapping, recorded because
#: it will be proposed again.
WHY_NOT_CONTINUOUS_Q: dict[str, Any] = {
    "rejected_proposal": "Q = {1.000, 0.983, 0.977, UNKNOWN x3}",
    "why": (
        "Those numbers COMPRESS EXACTLY THE DISTINCTION THE TEST EXPOSED. "
        "0.983 against 0.977 makes the SUM rung and LongTermDebtNoncurrent "
        "look almost interchangeable, while their cheap-decile churn is "
        "14.13% against 22.55%. The proposed Q principally describes BROAD "
        "RANK AGREEMENT, while the factor's economic use is TAIL-SENSITIVE. "
        "A quality weight that is near-blind to the dimension the factor is "
        "actually used on is worse than no weight, because it looks like it "
        "priced the risk."),
    "also_rejected": (
        "Q = 1 - decile_churn. That would replace one arbitrary mapping with "
        "another: there is no defensible calibration for what a given amount "
        "of tail instability should be worth in FACTOR POINTS, and inventing "
        "one here would be a modelling decision wearing a measurement's "
        "clothes."),
    "what_is_preserved_instead": (
        "the raw debt rung", "its provenance", "the validation statistics",
        "the eligibility status",
    ),
    "the_hard_gate_makes_the_decision": (
        "A binary admission is honest about what was actually established: "
        "these rungs preserve the tail well enough to score on, those do not, "
        "and for these three we do not know. Nothing is smuggled into a "
        "decimal."),
    "remains_a_challenger": (
        "A continuous debt-provenance shrinkage stays available as a LATER "
        "CHALLENGER, if a defensible calibration is developed for what a "
        "particular amount of tail instability should mean in factor points. "
        "It is deferred, not refuted."),
}

#: PERMANENT RESEARCH LINEAGE, by owner instruction. Adapted from the Gate 2
#: run rather than re-typed: the first threshold would have PROMOTED
#: LongTermDebt, and a correctly specified pair-count floor withdrew that
#: verdict without touching anything else. That history is the answer to
#: "why was this rung excluded?" asked two years from now.
def debt_threshold_history() -> list[dict[str, Any]]:
    """The Gate 2 threshold history, from the archive, or UNKNOWN."""
    archive = _debt_archive()
    if archive is None:
        return []
    return list(archive.get("decision_rule", {}).get("threshold_history", []))


_DEBT_ARCHIVE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "pit_archive", "debt_quality", "debt_quality.json")

_DEBT_ARCHIVE_CACHE: dict[str, Any] = {}


def _debt_archive() -> Optional[dict[str, Any]]:
    """The Gate 2 measurement, read once. None if the run is not on disk.

    Absent is reported as absent. The eligibility RULE is a decision and lives
    in code; the STATISTICS behind it are a measurement and live in the run
    that produced them, so a rule quoted without its evidence is visible.
    """
    if "loaded" not in _DEBT_ARCHIVE_CACHE:
        try:
            with open(_DEBT_ARCHIVE_PATH, "r", encoding="utf-8") as fh:
                _DEBT_ARCHIVE_CACHE["loaded"] = json.load(fh)
        except (OSError, ValueError):
            _DEBT_ARCHIVE_CACHE["loaded"] = None
    return _DEBT_ARCHIVE_CACHE["loaded"]


def debt_rung_status(rung_key: str) -> str:
    """The eligibility status of a debt rung. Raises on an unknown rung."""
    if rung_key not in DEBT_RUNG_EVEBITDA_V1:
        raise KeyError(
            "unknown debt rung %r. %s admits exactly %d rungs and an unlisted "
            "one has no status -- add it to the policy deliberately rather "
            "than defaulting it."
            % (rung_key, DEBT_RUNG_POLICY_VERSION, len(DEBT_RUNG_EVEBITDA_V1)))
    return DEBT_RUNG_EVEBITDA_V1[rung_key]


def debt_rung_eligible(rung_key: str) -> bool:
    """May this rung's debt figure enter the SCORED EV/EBITDA factor?"""
    return debt_rung_status(rung_key) == DEBT_ELIGIBLE


def debt_rung_evidence(rung_key: str) -> dict[str, Any]:
    """Status, reason and the measured statistics behind it.

    Statistics are UNKNOWN (None) rather than 0 when the archive is absent or
    the rung produced no pairs.
    """
    status = debt_rung_status(rung_key)
    archive = _debt_archive()
    pooled = ((archive or {}).get("ranking", {}).get("pooled", {})
              .get(rung_key, {}))
    return {
        "policy_version": DEBT_RUNG_POLICY_VERSION,
        "rung": rung_key,
        "status": status,
        "eligible_for_scoring": status == DEBT_ELIGIBLE,
        "why": DEBT_RUNG_REASONS.get(rung_key),
        "bound": pooled.get("bound"),
        "n_within_date_pairs": pooled.get("n_within_date_pairs"),
        "spearman_weighted": pooled.get("spearman_weighted"),
        "near_ordering_agreement": pooled.get("near_ordering_agreement"),
        "cheap_decile_churn": pooled.get("cheap_decile_churn"),
        "debt_ratio_median_to_reference": (
            (pooled.get("debt_ratio_to_reference") or {}).get("median")),
        "evidence_available": bool(pooled),
        "sample_scope": pit_factor_spec.SURVIVOR_ONLY,
        "measured_by": "pit_debt_quality (gate 2, 2026-09-21)",
    }


def debt_gate(rung_key: Optional[str]) -> Optional[str]:
    """The refusal reason for this debt rung, or None if it may be scored.

    THE GATE THAT BITES. `EV/EBITDA` calls this before it forms a multiple, so
    an ineligible rung produces an UNAVAILABLE subfactor rather than a number
    nobody can trace back to its provenance.

    A missing rung key is UNDETERMINED, never eligible: a debt figure whose
    provenance was not recorded cannot have been validated.
    """
    if rung_key is None:
        return REASON_DEBT_RUNG_UNDETERMINED
    status = debt_rung_status(rung_key)
    if status == DEBT_ELIGIBLE:
        return None
    if status == DEBT_REJECTED_MEASURED:
        return REASON_DEBT_RUNG_REJECTED
    return REASON_DEBT_RUNG_UNDETERMINED


def debt_policy() -> dict[str, Any]:
    """The whole eligibility decision, for a replay run to store."""
    return {
        "version": DEBT_RUNG_POLICY_VERSION,
        "kind": "HARD_ELIGIBILITY_GATE",
        "explicitly_not": "CONTINUOUS_QUALITY_MULTIPLIER",
        "statuses": list(DEBT_STATUSES),
        "rungs": {k: debt_rung_evidence(k) for k in DEBT_RUNG_EVEBITDA_V1},
        "why_not_continuous_q": json.loads(
            json.dumps(WHY_NOT_CONTINUOUS_Q, default=list)),
        "threshold_history": debt_threshold_history(),
        "cost_of_the_gate": (
            "Excluding LongTermDebtNoncurrent costs 7.8 to 9.7 points of base "
            "and roughly halves the tail distortion, 22.55% -> 14.13% "
            "cheap-decile churn. A real trade with real numbers on both "
            "sides, taken deliberately."),
        "what_undetermined_does_not_mean": (
            "It does not mean 'probably fine' and it does not mean 'bad'. "
            "Defaulting the three to eligible would admit the finance-heavy "
            "LongTermDebt rung on no evidence; defaulting them to rejected "
            "would silently delete it. They are held open, and the way to "
            "close them is the absence sidecar pit_policy.absence_limits() "
            "specifies -- because what makes the exact rung rare is that "
            "ShortTermBorrowings is untagged, and this store cannot tell an "
            "untagged zero from an untagged amount."),
    }


KNOWN_LIMITATIONS: dict[str, Any] = {
    "pe_ratio_price_basis_conflict": {
        "status": pit_factor_spec.SHAFFER_V1_KNOWN_LIMITATION,
        "where": "pit_factor_spec.SPECS['pe_ratio'].required_primitives",
        "finding": (
            "The pe_ratio factor spec names `price_adjusted`, and `pit_eps` "
            "establishes that a point-in-time EPS must be paired with a RAW, "
            "AS-PRINTED price -- pit_shares.PRICE_BASIS_RAW. The two cannot "
            "both be right. Apple at 2020-01-31 is the measurement: $309.51 / "
            "11.89 = 26.0 as printed, and the same 11.89 against a "
            "split-adjusted ~$77 is 6.5. A mega cap wearing a deep-value "
            "multiple, wrong by exactly the 4:1 split, with clean provenance "
            "and no error anywhere."),
        "resolution_here": (
            "OWNER DECISION 2 of 2026-09-21 SETTLED THIS in favour of the raw "
            "as-traded price, with the refinement that the EPS is carried to "
            "the SCORE-DATE share basis using only actions effective by the "
            "as-of date. This module's three subfactors therefore declare "
            "`price_raw_as_traded`, and `pit_price_basis` enforces it: the "
            "substitution raises rather than defaulting. Apple then reads "
            "26.03 before the split and 25.90 after -- 0.5% apart, which is "
            "the price move rather than the split. The FROZEN pit_factor_spec "
            "is still NOT edited; the divergence is recorded, which is what "
            "this project does with a defect in a frozen object."),
        "decided_on": "2026-09-21",
    },
    "ev_ebitda_debt_rung_eligibility": {
        "status": "CLOSED BY MEASUREMENT, 2026-09-21 (freeze gate 5)",
        "finding": (
            "The debt-rung coverage recovery is REAL IN ORDERING and NOT "
            "CLEAN ENOUGH TO POOL. SUM(LTDNoncurrent+LTDCurrent) keeps 98.27% "
            "of within-date pairwise EV/EBITDA ordering and churns 14.13% of "
            "the cheapest decile; LongTermDebtNoncurrent keeps 97.71% and "
            "churns 22.55%. Three of six rungs have too few within-date pairs "
            "to judge at all."),
        "resolution": (
            "A HARD ELIGIBILITY GATE, %s -- not the continuous quality "
            "multiplier the measurement proposed. See WHY_NOT_CONTINUOUS_Q: "
            "Q = {1.000, 0.983, 0.977} compresses 14.13%% against 22.55%%, so "
            "it describes broad rank agreement while the factor's use is "
            "tail-sensitive." % DEBT_RUNG_POLICY_VERSION),
        "cost_accepted": (
            "7.8 to 9.7 points of base, in exchange for roughly halving the "
            "tail distortion."),
    },
    "anchor_level_is_a_convention": {
        "status": "DECLARED, NOT MEASURED",
        "finding": WHAT_IS_ARBITRARY["the_level_is_arbitrary"],
        "resolution_here": "stated in WHAT_IS_ARBITRARY and exposed as ONE "
                           "constant, so a sensitivity sweep is a one-line "
                           "change and the choice is auditable.",
    },
    "degraded_pillar_shares_the_full_scale": {
        "status": "DELIBERATE CONSEQUENCE",
        "finding": (
            "Within-pillar renormalisation means a V built from one subfactor "
            "lands on the same scale as a V built from three, and both rows "
            "read `VGPD`. Information content differs; the scale does not."),
        "resolution_here": (
            "the mask and `data_quality` are STORED COLUMNS and join the "
            "pooling key. This is the exact situation "
            "docs/CONFIDENCE-FRAMEWORK.md section 2 designed Q for -- a continuous "
            "measure must not trigger a discrete signature change -- so the "
            "fix is a column, not a different signature."),
    },
    "losing_a_weak_leg_raises_the_pillar_quality": {
        "status": "DELIBERATE, AND THE OBVIOUS FIX IS THE WRONG ONE",
        "finding": (
            "`pillar_quality` is a weighted mean over the PRESENT subfactors, "
            "so dropping the lower-quality relative leg RAISES it: the worked "
            "example goes from 0.8075 (Medium) at V[PE=1,PEPeer=1,EVEBITDA=0] "
            "to 0.9200 (High) at V[PE=1,PEPeer=0,EVEBITDA=0], on strictly LESS "
            "information."),
        "why_it_is_correct": (
            "Q answers 'how good is what we have', and the MASK answers 'how "
            "much do we have'. Those are different types -- exactly the type "
            "error docs/CONFIDENCE-FRAMEWORK.md section 1 refuses to commit one "
            "level up -- and the surviving reading really is high quality. "
            "Completeness is not quality with a different name."),
        "the_tempting_wrong_fix": (
            "Multiplying Q by the present-subfactor weight would fold "
            "completeness into quality and lose the distinction, one level "
            "below the place section 4 forbids the same move. Both numbers are "
            "STORED and joined in the pooling key, so an evaluation that wants "
            "them combined can combine them AFTER measuring whether it helps -- "
            "which is the only order in which that question is answerable."),
    },
    "quality_combination_rule_unresolved": {
        "status": QUALITY_RULE_UNDECIDED,
        "finding": QUALITY_RULE_STATUS["why"],
        "resolution_here": QUALITY_RULE_STATUS["until_then"],
    },
    "no_replay_no_baseline": {
        "status": "OUT OF SCOPE BY INSTRUCTION",
        "finding": ("No official replay has been run and no baseline is frozen. "
                    "pit_feature, pit_score and pit_replay_run remain at 0 "
                    "rows, and none of the five freeze criteria in "
                    "docs/CONFIDENCE-FRAMEWORK.md section 6 is closed by this module."),
        "resolution_here": ("this module computes a SPEC and a worked example "
                            "from fixtures; it fits nothing and reads nothing."),
    },
}


# ==========================================================================
# (11) THE WORKED EXAMPLE
#
# FIXTURES. Every number below was authored for the demonstration and then
# scored with the functions above. No database was opened to produce them and
# none of them is a claim about a real issuer.
# ==========================================================================

FIXTURE_AS_OF = "2024-06-28"

FIXTURE_NOTE = (
    "A HYPOTHETICAL high-multiple manufacturer, SIC 3711 (motor vehicles), at "
    f"{FIXTURE_AS_OF} -- one of the three census dates. Raw as-printed price "
    "$244.00, TTM EPS $2.44, so P/E = 100.0 exactly. These are FIXTURE values "
    "chosen to be legible, NOT store reads and NOT a claim about any real "
    "company. The thin cohort is the realistic part: at sic4 the EV/EBITDA "
    "cohort clears twelve members 2.4% of the time.")

#: The thin case: a P/E cohort forms at sic3, the EV/EBITDA band does not.
FIXTURE_THIN: dict[str, Any] = {
    "price_raw": 244.00,
    "ttm_eps": 2.44,
    "pe_cohort_n": 9,
    "pe_cohort_rung": "sic3",
    "pe_cohort_median": 28.0,
    "ev_ebitda_cohort_n": 2,
    "ev_ebitda_cohort_rung": "sic4",
}

#: The VERY thin case: nothing comparable forms at all, at any admissible rung.
#: This is the case the whole design exists for.
FIXTURE_VERY_THIN: dict[str, Any] = {
    "price_raw": 244.00,
    "ttm_eps": 2.44,
    "pe_cohort_n": 1,
    "pe_cohort_rung": "sic4",
    "pe_cohort_median": None,
    "ev_ebitda_cohort_n": 0,
    "ev_ebitda_cohort_rung": "sic4",
}

#: The other three pillars, so the example produces a real company score.
FIXTURE_OTHER_PILLARS: dict[str, float] = {
    pit_score_signature.V2_BLOCK_EBITDA: 62.0,
    pit_score_signature.V2_BLOCK_GROWTH: 18.0,
    pit_score_signature.V2_BLOCK_QUALITY: -24.0,
}

#: Authored quality components, in [0,1]. The absolute leg is high quality --
#: two leaves, a cover-page-adjacent filed number, fresh. The relative leg is
#: medium -- the same inputs plus a nine-member sic3 cohort whose effective
#: SIC4 count is 1.88.
_Q_ABSOLUTE = {COMPONENT_SOURCE_QUALITY: 0.95,
               COMPONENT_FRESHNESS: 0.92,
               COMPONENT_PRIMITIVE_COVERAGE: 0.98}
_Q_RELATIVE = {COMPONENT_SOURCE_QUALITY: 0.95,
               COMPONENT_FRESHNESS: 0.92,
               COMPONENT_PEER_COHERENCE: 0.62,
               COMPONENT_PRIMITIVE_COVERAGE: 0.71}


def _build_pillar(fixture: Mapping[str, Any],
                  quality_rule: Optional[str] = QUALITY_RULE_MIN
                  ) -> ValuationPillar:
    """The three subfactors for one fixture, scored through the real functions."""
    price = float(fixture["price_raw"])
    eps = float(fixture["ttm_eps"])

    # -- P/E ABSOLUTE. No cohort is consulted anywhere in this block. --------
    pe = pit_eps.price_earnings(price, eps)
    absolute_detail = valuation_score_detail(pe["pe"], PE_ABSOLUTE_ANCHOR)
    absolute = SubfactorResult(
        key=SUBFACTOR_PE_ABSOLUTE,
        available=pe["pe"] is not None,
        raw_score=absolute_detail["score"],
        multiple=pe["pe"],
        anchor=PE_ABSOLUTE_ANCHOR,
        anchor_source=ANCHOR_FIXED_MARKET_PE,
        quality=data_quality(SUBFACTOR_PE_ABSOLUTE, _Q_ABSOLUTE, quality_rule),
        unavailable_reason=pe["reason"],
        detail=absolute_detail)

    # -- P/E RELATIVE CONTEXT. Both gates, through the real gate function. ---
    gate = pit_factor_spec.peer_set_gate(
        "pe_ratio", str(fixture["pe_cohort_rung"]),
        int(fixture["pe_cohort_n"]), as_of=FIXTURE_AS_OF,
        version=pit_factor_spec.FACTOR_SPEC_VERSION_V2)
    median = fixture.get("pe_cohort_median")
    relative_detail = (valuation_score_detail(pe["pe"], float(median))
                       if gate.ok and median else
                       {"score": None, "reason": gate.reason,
                        "failed_tests": list(gate.failed_tests),
                        "note": gate.note})
    relative = SubfactorResult(
        key=SUBFACTOR_PE_RELATIVE,
        available=bool(gate.ok and median and relative_detail["score"] is not None),
        raw_score=relative_detail.get("score"),
        multiple=pe["pe"],
        anchor=float(median) if median else None,
        anchor_source=ANCHOR_COHORT_MEDIAN,
        quality=data_quality(SUBFACTOR_PE_RELATIVE, _Q_RELATIVE, quality_rule),
        unavailable_reason=None if gate.ok else gate.reason,
        cohort_n=int(fixture["pe_cohort_n"]),
        cohort_rung=str(fixture["pe_cohort_rung"]),
        detail=relative_detail)

    # -- EV/EBITDA SUPPLEMENT. Absent in both fixtures, and the gate says so. -
    ev_gate = pit_factor_spec.peer_set_gate(
        "ev_ebitda", str(fixture["ev_ebitda_cohort_rung"]),
        int(fixture["ev_ebitda_cohort_n"]), as_of=FIXTURE_AS_OF,
        version=pit_factor_spec.FACTOR_SPEC_VERSION_V2)
    supplement = SubfactorResult(
        key=SUBFACTOR_EV_EBITDA,
        available=False,
        raw_score=None,
        unavailable_reason=ev_gate.reason,
        cohort_n=int(fixture["ev_ebitda_cohort_n"]),
        cohort_rung=str(fixture["ev_ebitda_cohort_rung"]),
        detail={"failed_tests": list(ev_gate.failed_tests), "note": ev_gate.note})

    return valuation_pillar(
        {SUBFACTOR_PE_ABSOLUTE: absolute,
         SUBFACTOR_PE_RELATIVE: relative,
         SUBFACTOR_EV_EBITDA: supplement},
        quality_rule=quality_rule)


def render_curve() -> str:
    """The transform, tabulated, at both candidate anchors."""
    lines = [f"THE CONVEX EXTREME-VALUATION TRANSFORM -- {TRANSFORM_VERSION}", ""]
    lines.append(f"  u = ln(m / anchor)")
    lines.append(f"  cheap (u<=0):  S = +{TRANSFORM_C:.0f} * "
                 f"tanh(({TRANSFORM_A:.0f}/{TRANSFORM_C:.0f}) * (-u))")
    lines.append(f"  rich  (u> 0):  S = -min(100, {TRANSFORM_A:.0f}*u + "
                 f"{TRANSFORM_B:.4f}*max(0, u-ln2)^2)")
    lines.append(f"  B is SOLVED, not chosen: S(10 x anchor) = -100 exactly "
                 f"-> B = {TRANSFORM_B:.10f}")
    lines.append("")
    lines.append("  P/E      S @ anchor 20      S @ anchor 16     (sensitivity)")
    lines.append("  " + "-" * 60)
    for m in (5, 10, 15, 20, 25, 30, 40, 50, 100, 150, 200, 400):
        s20 = valuation_score(float(m), 20.0)
        s16 = valuation_score(float(m), 16.0)
        lines.append(f"  {m:<9}{s20:>+12.4f}{s16:>+18.4f}")
    lines.append("")
    c1 = valuation_score(20.0, 20.0) - valuation_score(30.0, 20.0)
    c2 = valuation_score(100.0, 20.0) - valuation_score(150.0, 20.0)
    lines.append(f"  20 -> 30   costs {c1:7.4f} points")
    lines.append(f"  100 -> 150 costs {c2:7.4f} points")
    lines.append(f"  ratio            {c2 / c1:7.4f}x   "
                 f"(a linear-in-log penalty would be exactly 1.0000x, and v1's "
                 f"tanh is 0.0305x)")
    return "\n".join(lines)


def render_transitions() -> str:
    """Every sign case of an EPS pair, and what each is allowed to produce."""
    pairs = [
        (-0.10, +0.10, "the owner's example: -200% would be a real number "
                       "pointing the wrong way"),
        (+0.10, -0.10, "the mirror image"),
        (-1.00, -0.10, "a 90% improvement; the formula returns -90%"),
        (-0.10, -1.00, "a loss deepening"),
        (+1.00, +1.20, "the only state that gets a percentage"),
        (0.00, +0.50, "a REPORTED zero, not a small number"),
        (+0.50, 0.00, "to a reported zero"),
        (+0.005, +0.50, "base under one cent: +9,900% is a quantisation artefact"),
        (+1.00, +0.005, "collapse TO near zero: the BASE is fine, so this one counts"),
    ]
    lines = ["EARNINGS_SIGN_CROSS AND THE NEIGHBOURING STATES", ""]
    lines.append("  prev     curr    state                             growth%   note")
    lines.append("  " + "-" * 92)
    for prev, curr, note in pairs:
        t = eps_transition(prev, curr, same_share_basis=True)
        growth = "REFUSED" if t.growth_pct is None else f"{t.growth_pct:+.2f}%"
        lines.append(f"  {prev:+7.3f} {curr:+7.3f}  {t.state:<33} {growth:>9}   {note}")
    lines.append("")
    lines.append("  Not one row produces a percentage across a sign, and the "
                 "refusal is DELIBERATE:")
    lines.append("  every refused value is computable. That is the point -- the "
                 "arithmetic succeeds and lies.")
    unknown = eps_transition(1.00, 1.20)
    lines.append("")
    lines.append(f"  share basis UNKNOWN on a both-positive pair -> "
                 f"growth {unknown.growth_pct!r}, "
                 f"refused_because = {unknown.refused_because[:48]}...")
    return "\n".join(lines)


def render_worked_example() -> str:
    """The three-subfactor pillar producing a real valuation view, EV/EBITDA absent."""
    out: list[str] = []
    out.append("=" * 78)
    out.append("A REAL VALUATION VIEW WITH EV/EBITDA ABSENT")
    out.append("=" * 78)
    out.append("")
    out.append("  " + FIXTURE_NOTE.replace(". ", ".\n  "))
    out.append("")

    for title, fixture in (("THIN COHORT -- a sic3 P/E cohort forms, the "
                            "EV/EBITDA band does not", FIXTURE_THIN),
                           ("VERY THIN -- nothing comparable forms at any "
                            "admissible rung", FIXTURE_VERY_THIN)):
        pillar = _build_pillar(fixture)
        out.append("-" * 78)
        out.append(title)
        out.append("-" * 78)
        out.append("")
        out.append(f"  mask              {pillar.mask}")
        out.append(f"  presence          {pillar.presence.state}"
                   f"   -> pit_store availability "
                   f"{pillar.presence.availability!r}")
        out.append(f"  standing alone    "
                   f"{', '.join(pillar.presence.standing_alone) or '(none)'}")
        if pillar.presence.refused_for_dependency:
            out.append(f"  refused for dep.  "
                       f"{', '.join(pillar.presence.refused_for_dependency)}")
        out.append("")
        out.append("  subfactor              w_eff   multiple   anchor      raw"
                   "        Q   F_eff")
        out.append("  " + "-" * 74)
        for key in SUBFACTOR_KEYS:
            r = pillar.results[key]
            item = SUBFACTORS[key]
            if not r:
                out.append(f"  {item.label:<22} {'--':>6} "
                           f"{'UNAVAILABLE':>36}")
                out.append(f"  {'':22} {'':>6}   reason: {r.unavailable_reason}")
                if r.cohort_n is not None:
                    out.append(f"  {'':22} {'':>6}   cohort n={r.cohort_n} at "
                               f"rung {r.cohort_rung!r}; failed "
                               f"{r.detail.get('failed_tests')}")
                continue
            eff = effective_value(r.raw_score, r.quality)
            q = eff["data_quality"]
            fe = eff["f_effective"]
            out.append(
                f"  {item.label:<22} "
                f"{pillar.effective_weights[key]:>6.4f} "
                f"{r.multiple:>10.2f} {r.anchor:>8.2f} "
                f"{r.raw_score:>+9.4f} "
                f"{('--' if q is None else f'{q:.4f}'):>8} "
                f"{('--' if fe is None else f'{fe:+.4f}'):>8}")
        out.append("")
        out.append(f"  VALUATION PILLAR  {pillar.pillar_score:+.4f}"
                   f"   (data_quality {pillar.pillar_quality:.4f}"
                   f" = {quality_band(pillar.pillar_quality)}, rule "
                   f"{QUALITY_RULE_MIN!r}, status {QUALITY_RULE_UNDECIDED})")
        out.append("")

        scores = dict(FIXTURE_OTHER_PILLARS)
        scores[pit_score_signature.V2_BLOCK_VALUATION] = (
            pillar.for_score_signature()["factor_score"])
        result = pit_score_signature.score_v2(
            scores, reasons={pit_score_signature.V2_BLOCK_VALUATION:
                             pillar.reason} if pillar.reason else None)
        evidence = forecast_evidence(pillar.mask, 4820.0, 5, horizon="3M")
        view = confidence_view(pillar, result, FIXTURE_AS_OF, evidence)
        out.append(render_confidence_view(view))
        out.append("")
        out.append(f"  pooling key       {stratify_key(pillar, result)}")
        out.append("")

    thin = _build_pillar(FIXTURE_THIN)
    very = _build_pillar(FIXTURE_VERY_THIN)
    absolute_only = very.results[SUBFACTOR_PE_ABSOLUTE].raw_score
    out.append("=" * 78)
    out.append("THE POINT, IN TWO NUMBERS")
    out.append("=" * 78)
    out.append("")
    out.append(f"  A 100.0x P/E at a thin-cohort as-of date.")
    out.append("")
    failed = (very.results[SUBFACTOR_PE_RELATIVE].detail.get("failed_tests")
              or [])
    out.append(f"    peer-relative valuation ONLY (v1's shape):  the cohort "
               f"fails {failed},")
    out.append(f"      the pillar is absent, 0.35 of weight is not spent, and "
               f"the 100x is")
    out.append(f"      NOT PENALISED AT ALL.")
    out.append(f"    three-subfactor valuation (this spec):      "
               f"{absolute_only:+.4f} points from the")
    out.append(f"      absolute leg alone, with no cohort anywhere in the "
               f"computation.")
    out.append("")
    out.append(f"  Note what pillar data_quality does between the two cases: "
               f"{thin.pillar_quality:.4f} -> {very.pillar_quality:.4f}.")
    out.append(f"  Losing the WEAKER leg RAISES it, on strictly less "
               f"information. That is correct and")
    out.append(f"  deliberate -- Q says how good what we have is, the MASK "
               f"says how much we have, and")
    out.append(f"  folding one into the other is the type error the confidence "
               f"framework refuses.")
    out.append("")
    out.append(f"  And with a cohort, the two legs disagree usefully rather "
               f"than redundantly:")
    out.append(f"    absolute vs a fixed 20.0x anchor  "
               f"{thin.results[SUBFACTOR_PE_ABSOLUTE].raw_score:+9.4f}")
    out.append(f"    relative vs a 28.0x peer median   "
               f"{thin.results[SUBFACTOR_PE_RELATIVE].raw_score:+9.4f}"
               f"   (expensive, but its industry is expensive too)")
    out.append(f"    blended pillar                    "
               f"{thin.pillar_score:+9.4f}")
    return "\n".join(out)


def render() -> str:
    lines = [f"VALUATION SUBFACTORS -- {VALUATION_SPEC_VERSION}", ""]
    lines.append(f"  lineage      {CANDIDATE_MODEL_VERSION}")
    lines.append(f"  presence     {PRESENCE_RULE_VERSION}")
    lines.append(f"  anchor       {ANCHOR_POLICY_VERSION}  "
                 f"(P/E {PE_ABSOLUTE_ANCHOR:.1f}, knot "
                 f"{2 * PE_ABSOLUTE_ANCHOR:.0f}x, floor "
                 f"{TRANSFORM_FLOOR_MULTIPLE * PE_ABSOLUTE_ANCHOR:.0f}x)")
    lines.append(f"  transform    {TRANSFORM_VERSION}")
    lines.append("")
    lines.append("  subfactor                 code        w  role        "
                 "cohort  stands alone  depends on")
    lines.append("  " + "-" * 92)
    for key in SUBFACTOR_KEYS:
        s = SUBFACTORS[key]
        lines.append(f"  {s.label:<25} {s.code:<9} {s.weight:.2f}  "
                     f"{s.role:<11} {('yes' if s.requires_cohort else 'NO'):<7} "
                     f"{('yes' if s.can_stand_alone else 'no'):<13} "
                     f"{s.depends_on or '--'}")
    lines.append("")
    lines.append("  PRESENCE RULE -- every reachable offer, exhaustively")
    lines.append("")
    lines.append("  offered            mask                          state"
                 "              availability  refused for dependency")
    lines.append("  " + "-" * 104)
    for bits in range(8):
        offered = {k: bool(bits & (1 << i)) for i, k in enumerate(SUBFACTOR_KEYS)}
        p = presence(offered)
        shown = "".join("1" if offered[k] else "0" for k in SUBFACTOR_KEYS)
        lines.append(f"  {shown:<18} {p.mask:<29} {p.state:<18} "
                     f"{p.availability:<13} "
                     f"{', '.join(p.refused_for_dependency) or '--'}")
    lines.append("")
    lines.append("  Row 010 is the dependency rule: P/E peer context was "
                 "OFFERED and is REFUSED,")
    lines.append("  because it ranks a multiple the absolute leg did not "
                 "produce.")
    return "\n".join(lines)


# ==========================================================================
# (12) SELF-CHECK
# ==========================================================================

def validate() -> list[str]:
    """Every invariant this module claims. Returns a list of problems."""
    problems: list[str] = []

    # -- the declarations ---------------------------------------------------
    if tuple(sorted(SUBFACTORS)) != tuple(sorted(SUBFACTOR_KEYS)):
        problems.append("SUBFACTOR_KEYS and SUBFACTORS disagree")
    if abs(sum(SUBFACTOR_WEIGHTS.values()) - 1.0) > 1e-12:
        problems.append("the subfactor weights must sum to 1.00")
    anchors = [k for k in SUBFACTOR_KEYS if SUBFACTORS[k].role == ROLE_ANCHOR]
    if anchors != [SUBFACTOR_PE_ABSOLUTE]:
        problems.append("there must be exactly one ANCHOR subfactor")
    if SUBFACTORS[SUBFACTOR_PE_ABSOLUTE].requires_cohort:
        problems.append("the absolute leg must NOT require a cohort")
    if COMPONENT_PEER_COHERENCE in SUBFACTORS[SUBFACTOR_PE_ABSOLUTE].quality_components:
        problems.append("the absolute leg must not declare peer_coherence: it "
                        "consults no peers")
    for key in SUBFACTOR_KEYS:
        item = SUBFACTORS[key]
        if item.role not in ROLES:
            problems.append(f"{key} has an unknown role {item.role!r}")
        if item.depends_on is not None and item.can_stand_alone:
            problems.append(f"{key} both depends on something and stands alone")
        if item.anchor_source not in ANCHOR_SOURCES:
            problems.append(f"{key} has an unknown anchor source")

    # -- the presence rule --------------------------------------------------
    for bits in range(8):
        offered = {k: bool(bits & (1 << i)) for i, k in enumerate(SUBFACTOR_KEYS)}
        p = presence(offered)
        if p.availability != PRESENCE_AVAILABILITY[p.state]:
            problems.append(f"{p.mask}: availability does not match the state")
        if p.state not in PRESENCE_STATES:
            problems.append(f"{p.mask}: unknown presence state")
        if parse_mask(p.mask) != {k: k in p.present for k in SUBFACTOR_KEYS}:
            problems.append(f"{p.mask}: mask does not round-trip")

    owner = presence({SUBFACTOR_PE_ABSOLUTE: True, SUBFACTOR_PE_RELATIVE: True,
                      SUBFACTOR_EV_EBITDA: False})
    if owner.state != PRESENT_DEGRADED or owner.mask != "V[PE=1,PEPeer=1,EVEBITDA=0]":
        problems.append("the owner's case must be PRESENT_DEGRADED with the "
                        "mask V[PE=1,PEPeer=1,EVEBITDA=0]")
    if owner.availability != pit_store.AVAIL_PARTIAL:
        problems.append("PRESENT_DEGRADED must map to pit_store.AVAIL_PARTIAL")

    alone = presence({SUBFACTOR_PE_ABSOLUTE: True})
    if not alone.is_present:
        problems.append("the anchor must carry the pillar alone")
    supp = presence({SUBFACTOR_EV_EBITDA: True})
    if not supp.is_present:
        problems.append("the supplement must carry the pillar alone -- 12.7% of "
                        "the cross-section has EBITDA and no earnings")
    orphan = presence({SUBFACTOR_PE_RELATIVE: True})
    if orphan.is_present or orphan.refused_for_dependency != (SUBFACTOR_PE_RELATIVE,):
        problems.append("peer context without an absolute leg must be REFUSED "
                        "for dependency, not merely absent")
    if presence({}).state != ABSENT:
        problems.append("no subfactors must be ABSENT")

    # -- the transform ------------------------------------------------------
    tol = PINNED_CURVE["tolerance"]
    if round(TRANSFORM_B, PINNED_CURVE["b_solved_to"]) != PINNED_CURVE["b_published"]:
        problems.append(f"B solved to {TRANSFORM_B!r}, published "
                        f"{PINNED_CURVE['b_published']}")
    a = PINNED_CURVE["anchor"]
    floor_at = PINNED_CURVE["floor_at"]
    if abs(valuation_score(floor_at, a) + 100.0) > 1e-9:
        problems.append("the -100 floor must land exactly at 10x the anchor")
    if abs(valuation_score(a, a)) > 1e-12:
        problems.append("the anchor itself must score exactly zero")
    c1 = valuation_score(20.0, a) - valuation_score(30.0, a)
    c2 = valuation_score(100.0, a) - valuation_score(150.0, a)
    for name, got, want in (("cost_20_to_30", c1, PINNED_CURVE["cost_20_to_30"]),
                            ("cost_100_to_150", c2, PINNED_CURVE["cost_100_to_150"]),
                            ("ratio", c2 / c1, PINNED_CURVE["ratio"]),
                            ("score_at_100x", valuation_score(100.0, a),
                             PINNED_CURVE["score_at_100x"])):
        if abs(got - want) > tol:
            problems.append(f"{name}: got {got!r}, pinned {want!r}")
    if c2 <= c1:
        problems.append("the convex branch must charge MORE for the far move")
    if valuation_score(-5.0) is not None or valuation_score(0.0) is not None:
        problems.append("a non-positive multiple must score None, never abs()")
    if valuation_score_detail(-5.0)["reason"] != pit_eps.PE_UNAVAILABLE_LOSS:
        problems.append("a negative multiple must reuse pit_eps's loss reason")
    prev = None
    for m in (1.0, 5.0, 20.0, 50.0, 200.0, 1000.0):
        s = valuation_score(m)
        if prev is not None and s > prev + 1e-12:
            problems.append("the transform must be non-increasing in the multiple")
        prev = s
    if valuation_score(5.0) > TRANSFORM_C or valuation_score(0.001) > TRANSFORM_C:
        problems.append("the cheap branch must saturate at +C")

    # -- earnings sign crossing ---------------------------------------------
    cross = eps_transition(-0.10, 0.10, same_share_basis=True)
    if cross.growth_pct is not None or not cross.crossing:
        problems.append("a loss-to-profit pair must be a crossing with NO growth%")
    if cross.discontinuity_state != EARNINGS_SIGN_CROSS:
        problems.append("a crossing must carry the EARNINGS_SIGN_CROSS state")
    if eps_transition(0.10, -0.10, same_share_basis=True).growth_pct is not None:
        problems.append("a profit-to-loss pair must produce NO growth%")
    narrowing = eps_transition(-1.00, -0.10, same_share_basis=True)
    if narrowing.growth_pct is not None or narrowing.direction != DIR_LOSS_NARROWING:
        problems.append("a narrowing loss must be a direction, never -90%")
    ok = eps_transition(1.00, 1.20, same_share_basis=True)
    if ok.growth_pct is None or abs(ok.growth_pct - 20.0) > 1e-9:
        problems.append("a both-positive pair in one share basis must grow 20%")
    if eps_transition(1.00, 1.20).growth_pct is not None:
        problems.append("an UNKNOWN share basis must refuse the percentage")
    tiny = eps_transition(0.005, 0.50, same_share_basis=True)
    if tiny.state != TRANSITION_NEAR_ZERO_BASE or tiny.growth_pct is not None:
        problems.append("a base at or under one cent must refuse the "
                        "percentage: +9,900% is a quantisation artefact")
    if eps_transition(1.00, 0.005, same_share_basis=True).growth_pct is None:
        problems.append("a collapse TO near zero has a sound BASE and must "
                        "still produce a percentage")
    for prev_v in (-2.0, -0.01, 0.0, 0.005, 1.0, 50.0):
        for curr_v in (-2.0, -0.01, 0.0, 0.005, 1.0, 50.0):
            t = eps_transition(prev_v, curr_v, same_share_basis=True)
            if t.state not in TRANSITION_STATES:
                problems.append(f"({prev_v}, {curr_v}) produced an unknown state")
            if t.is_sign_cross and t.growth_pct is not None:
                problems.append(f"({prev_v}, {curr_v}) produced a growth% across "
                                f"a sign crossing")
            if t.growth_pct is not None and prev_v <= pit_eps.EPS_NEAR_ZERO_ABS:
                problems.append(f"({prev_v}, {curr_v}) produced a growth% on a "
                                f"non-positive or sub-cent base")
    yc = earnings_yield_change(-0.10, 0.10, 20.0, same_share_basis=True)
    if yc["yield_change"] is None or yc["yield_change"] <= 0:
        problems.append("the yield change must be defined and POSITIVE across a "
                        "loss-to-profit crossing")
    if earnings_yield_change(-0.10, 0.10, 20.0)["yield_change"] is not None:
        problems.append("an UNKNOWN share basis must refuse the yield change too")

    # -- quality ------------------------------------------------------------
    q = data_quality(SUBFACTOR_PE_ABSOLUTE, _Q_ABSOLUTE)
    if q.quality is not None or q.adopted_rule is not None:
        problems.append("with no rule named, Q must be None and adopt nothing")
    if q.rule_status != QUALITY_RULE_UNDECIDED:
        problems.append("the combination rule status must remain UNDECIDED")
    if q.candidates[QUALITY_RULE_MIN] is None:
        problems.append("every candidate rule must still be computed")
    named = data_quality(SUBFACTOR_PE_ABSOLUTE, _Q_ABSOLUTE, QUALITY_RULE_MIN)
    if named.quality is None or abs(named.quality - 0.92) > 1e-12:
        problems.append("min over the absolute leg's components must be 0.92")
    try:
        data_quality(SUBFACTOR_PE_ABSOLUTE,
                     dict(_Q_ABSOLUTE, **{COMPONENT_PEER_COHERENCE: 1.0}))
        problems.append("peer_coherence must be REFUSED on the absolute leg")
    except ValueError:
        pass
    try:
        data_quality(SUBFACTOR_PE_ABSOLUTE, _Q_ABSOLUTE, QUALITY_RULE_LEARNED)
        problems.append("QUALITY_RULE_LEARNED must refuse to run")
    except ValueError:
        pass
    ev = effective_value(-53.99, named)
    if ev["f_raw"] is None or ev["data_quality"] is None or ev["f_effective"] is None:
        problems.append("effective_value must return all three of raw, Q, effective")
    if abs(ev["f_effective"] - (-53.99 * 0.92)) > 1e-9:
        problems.append("F_effective must be F_raw x Q")
    if abs(ev["f_effective"]) >= abs(ev["f_raw"]):
        problems.append("shrinkage must move a factor TOWARD neutral")

    # -- the assembly and the worked example --------------------------------
    thin = _build_pillar(FIXTURE_THIN)
    very = _build_pillar(FIXTURE_VERY_THIN)
    if thin.mask != "V[PE=1,PEPeer=1,EVEBITDA=0]":
        problems.append(f"the thin fixture must produce the owner's mask, got "
                        f"{thin.mask}")
    if not thin.available or thin.presence.state != PRESENT_DEGRADED:
        problems.append("the thin fixture must be a PRESENT_DEGRADED real view")
    if thin.results[SUBFACTOR_EV_EBITDA].unavailable_reason != \
            pit_store.REASON_VALUATION_PEER_SET_INSUFFICIENT:
        problems.append("the absent supplement must carry the owner's "
                        "first-class outcome as its reason")
    if very.mask != "V[PE=1,PEPeer=0,EVEBITDA=0]" or not very.available:
        problems.append("the very-thin fixture must still be a PRESENT pillar "
                        "carried by the absolute leg alone")
    solo = very.results[SUBFACTOR_PE_ABSOLUTE].raw_score
    if solo is None or solo > -50.0:
        problems.append("a 100x P/E must be heavily penalised with NO cohort")
    if abs(solo - PINNED_CURVE["score_at_100x"]) > tol:
        problems.append("the no-cohort penalty must be the pinned curve value")
    if abs(sum(thin.effective_weights.values()) - 1.0) > 1e-12:
        problems.append("the present subfactor weights must renormalise to 1")
    if not (very.pillar_quality > thin.pillar_quality):
        problems.append("the documented consequence must hold: dropping the "
                        "weaker leg RAISES pillar quality, because Q is about "
                        "what is there and the mask is about how much")
    if very.results[SUBFACTOR_PE_RELATIVE].detail.get("failed_tests") != \
            [pit_factor_spec.TEST_SUFFICIENT_N]:
        problems.append("a refused relative leg must carry its gate's failed "
                        "tests, not merely its reason")
    if thin.results[SUBFACTOR_PE_RELATIVE].cohort_rung != "sic3":
        problems.append("the thin fixture's P/E cohort must form at sic3")

    # -- the wiring ---------------------------------------------------------
    scores = dict(FIXTURE_OTHER_PILLARS)
    scores[pit_score_signature.V2_BLOCK_VALUATION] = thin.pillar_score
    result = pit_score_signature.score_v2(scores)
    if result.signature != pit_score_signature.V2_SIGNATURE_FULL:
        problems.append("a degraded-but-present valuation pillar must still "
                        "produce a FULL signature -- that is the design")
    if result.partial_score_marker != pit_score_signature.FULL_SHAFFER_SCORE:
        problems.append("the marker must be FULL: the pillar is present")
    key = stratify_key(thin, result)
    if key[2] != thin.mask or len(key) != 3:
        problems.append("the pooling key must carry the mask")
    if key == stratify_key(very, result):
        problems.append("two different masks must not share a pooling key")
    for name in ("data_quality", "score_completeness", "forecast_evidence",
                 "hedge_evidence"):
        if name not in VOCABULARY_MAP:
            problems.append(f"{name} is missing from VOCABULARY_MAP")
    if len(VOCABULARY_MAP) != 4:
        problems.append("VOCABULARY_MAP must name FOUR dimensions and no fifth")
    fe = forecast_evidence(thin.mask, 4820.0, 5, horizon="3M")
    if fe["forecast_evidence"] not in (pit_invariants.STATUS_SUFFICIENT,
                                       pit_invariants.STATUS_INSUFFICIENT):
        problems.append("forecast evidence must reuse pit_invariants' verdicts")
    thin_12m = forecast_evidence(thin.mask, 160.7, 3, horizon="12M")
    if thin_12m["forecast_evidence"] != pit_invariants.STATUS_INSUFFICIENT:
        problems.append("160.7 independent 12M observations must be INSUFFICIENT")
    he = hedge_evidence("2019-06-28")
    if he["hedge_evidence"] != pit_intermediates.HEDGE_LIVE_ONLY:
        problems.append("a pre-archive as-of must be prospective-only")
    view = confidence_view(thin, result, FIXTURE_AS_OF, fe)
    flat = json.dumps(view, default=str)
    if "overall_confidence" in flat or "confidence_adjusted_score" in flat:
        problems.append("the view must not carry a score x confidence product")
    if view["shaffer_score"] != result.company_score:
        problems.append("the view must report the UNADJUSTED company score")

    # -- storage restraint --------------------------------------------------
    if storage_note()["applied"]:
        problems.append("this module must not apply a migration")
    for name, coltype in PIT_SCORE_SUBFACTOR_MIGRATIONS:
        if coltype not in ("TEXT", "REAL", "INTEGER"):
            problems.append(f"{name} has an unexpected column type {coltype!r}")
        if "DEFAULT" in coltype.upper() or "NOT NULL" in coltype.upper():
            problems.append(f"{name} must carry no DEFAULT and no NOT NULL")
        if name in dict(pit_score_signature.PIT_SCORE_SIGNATURE_MIGRATIONS):
            problems.append(f"{name} duplicates a pit_score_signature column")
        if f"ADD COLUMN {name} {coltype};" not in PIT_SCORE_SUBFACTOR_COLUMN_DDL:
            problems.append(f"{name} {coltype} is in MIGRATIONS but not the DDL")
    merged = migration_patch()["pit_score"]
    if len(merged) != len(set(n for n, _ in merged)):
        problems.append("the merged pit_score patch repeats a column")
    return problems


def main(argv: Optional[Sequence[str]] = None) -> int:
    print(render())
    print()
    print(render_curve())
    print()
    print(render_transitions())
    print()
    print(render_worked_example())
    print()
    print("=" * 78)
    print("THE ANCHOR, AND WHAT IS ARBITRARY ABOUT IT")
    print("=" * 78)
    print()
    for key in ("the_kind_is_defensible", "the_level_is_arbitrary",
                "why_20_and_not_16", "what_the_level_decides",
                "not_rate_or_inflation_adjusted", "the_arbitrariness_is_bounded",
                "what_would_replace_it"):
        print(f"  {key}")
        text = WHAT_IS_ARBITRARY[key]
        for i in range(0, len(text), 74):
            print(f"      {text[i:i + 74]}")
        print()
    print("=" * 78)
    print("STORAGE -- SPECIFIED, NOT APPLIED")
    print("=" * 78)
    print()
    print(json.dumps(storage_note(), indent=1, sort_keys=True))
    print()
    problems = validate()
    for problem in problems:
        print("  FAIL", problem)
    print(f"{len(problems)} problems")
    print("PASS" if not problems else "FAIL")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
