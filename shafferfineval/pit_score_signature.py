"""Which pillars a Shaffer Score was actually built from -- on every score row.

THE PROBLEM THIS MODULE EXISTS TO STOP. `company_scoring.calculate_company_raw_score`
renormalises over whatever factors are available. That is a deliberate, sound
rule for a single company on a screen: a missing factor never contributes a
silent zero, and the drop is named in the notes. It becomes a different thing
entirely at replay scale, because the notes are prose and the score is a number,
and only the number reaches the backtest.

MEASURED, on the loaded store (`pit_derive.coverage`, reproduced 2026-09-21):

    company_raw_score availability   99.32% / 99.75% / 99.83%
    ev_ebitda, per peer               1.22% /  2.90% /  3.18%
                                     at 2015-06-30 / 2019-06-28 / 2024-06-28

and at COHORT level (`pit_cohort_measure`, 39,038 peer sets) the valuation
cohort has median 1 member, clears 3 in 34.2% of peer sets and 12 in 7.0%. So
the score is available essentially always and its 40%-weighted pillar is
available almost never. Most scored rows would therefore be

    0.25G + 0.20P + 0.15D, renormalised to 0.4167 / 0.3333 / 0.25

wearing the name of a four-pillar model. Pooling those with genuine VGPD scores
in one backtest averages two different models and reports the average as one.

THE OWNER'S RULING, implemented here exactly:

  v1 HISTORICAL BASELINE   PRESERVE the renormalisation. The point of the v1
                           replay is to test the ACTUAL original Shaffer logic,
                           so its arithmetic is not touched -- `score_v1` is a
                           restatement of the frozen core, pinned against it by
                           `test_pit_score_signature.py`. What is ADDED is a
                           label: `score_signature` ('VGPD', 'GPD', ...) and
                           `original_weight_coverage` (1.00, 0.60, ...).
  EVALUATION               "Then never blindly pool those two." `evaluation_
                           groups` is the default path and SEPARATES by
                           signature; `pool` refuses a mixed set unless the
                           caller acknowledges the model difference in writing.
  CANDIDATE v2             No cross-pillar renormalisation masquerading as a
                           full score. A lost pillar's weight is simply not
                           spent, the row is a PARTIAL SHAFFER SCORE, and it
                           carries the missing pillar, the core coverage and an
                           explicit not-directly-comparable marker.

THE ONE PIECE OF ARITHMETIC WORTH READING TWICE.

    v1  company = 0.75 x SUM over PRESENT pillars of (w_p / SUM PRESENT w) x s_p
    v2  company = 0.85 x SUM over PRESENT pillars of (w_p / SUM ALL     w) x s_p

The two differ ONLY in the denominator, and the denominators coincide exactly
when every pillar is present -- which is why a full four-pillar company scores
identically under both paths for a given weight table, and why the distinction
bites only on the rows it is meant to bite on. v1's weights sum to 1.00, so its
renormalisation is the identity at VGPD. v2's weights sum to 0.85, which is
COMPANY_SCALE_V2, so v2's company score reduces to the plain, unrenormalised

    0.35V + 0.25G + 0.15P + 0.10D          terms omitted, never redistributed

and a v2 partial score therefore lands on a SMALLER SCALE than a full one. That
is the design: losing a pillar must not promote the survivors.

STATUS. The production core is FROZEN. Nothing here edits `company_scoring.py`,
`sector_scoring.py`, `asset_models.py`, `prediction.py` or `hedging.py`; the
finding against the first is recorded in KNOWN_LIMITATIONS as
SHAFFER_V1_KNOWN_LIMITATION and the v2 rule belongs to the candidate lineage
only. This module is DECLARATIVE: it opens no connection, runs no SQL, fits
nothing, and leaves `pit_feature` and `pit_score` exactly as empty as it found
them. `ensure_columns` is written for ANOTHER process to run later.

Stdlib only.

    python pit_score_signature.py          # the worked demonstration; PASS/FAIL
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence

from pit_factor_contract import (
    CANDIDATE_MODEL_VERSION,
    SHAFFER_V1_KNOWN_LIMITATION,
)
from pit_store import (
    EQUITY_PIT_MODEL_VERSION,
    REASON_NO_PEERS,
    REASON_VALUATION_PEER_SET_INSUFFICIENT,
)

__all__ = [
    "SIGNATURE_VERSION", "V1_MODEL_VERSION", "CANDIDATE_MODEL_VERSION",
    # pillars and signatures
    "PILLARS", "PILLAR_LETTER", "PILLAR_LABEL", "SIGNATURE_FULL",
    "SIGNATURE_NONE", "signature_of", "pillars_of", "missing_of",
    "is_full_signature",
    # weights
    "V1_MAJOR_WEIGHTS", "V1_COMPANY_SCALE", "V2_MAJOR_WEIGHTS",
    "V2_BLOCK_EBITDA", "V2_BLOCK_VALUATION", "V2_BLOCK_GROWTH",
    "V2_BLOCK_QUALITY", "V2_BLOCKS", "V2_BLOCK_LETTER", "V2_BLOCK_LABEL",
    "V2_SIGNATURE_FULL", "V2_WEIGHT_SEMANTICS", "v2_signature_of",
    "COMPANY_SCALE_V2", "weight_present", "weight_coverage",
    "effective_weights",
    # markers
    "FULL_SHAFFER_SCORE", "V1_RENORMALISED_PARTIAL", "PARTIAL_SHAFFER_SCORE",
    "NOT_SCORED", "NOT_COMPARABLE_PHRASE", "MARKERS", "COMPARABLE_MARKERS",
    # why a pillar is absent -- pit_store's vocabulary, re-exported
    "VALUATION_PEER_SET_INSUFFICIENT", "REASON_LABELS", "reason_label",
    # scoring
    "PillarScore", "score_v1", "score_v2", "score",
    # evaluation
    "SignaturePoolingRefused", "Grouping", "Pool",
    "group_by_signature", "evaluation_groups", "pool",
    # rendering
    "render_partial_score", "render_score", "render_comparison",
    "render_grouping", "render_demonstration", "render_promotion_table",
    "render_pooling_demonstration",
    # storage -- specified, NOT applied
    "PIT_SCORE_SIGNATURE_MIGRATIONS", "PIT_SCORE_COLUMN_DDL",
    "PIT_SCORE_GUARD_DDL", "migration_patch", "migration_sql",
    "ensure_columns", "columns_present", "storage_note",
    # evidence
    "KNOWN_LIMITATIONS", "MEASURED", "DEMO_COMPANIES",
    "DEMO_COMPANIES_V2", "validate",
]

#: This module's own version. It goes nowhere near a row today -- the columns
#: below do -- but a second signature policy (a five-pillar model, say) needs a
#: name to be told apart from this one, and naming it now costs nothing.
SIGNATURE_VERSION = "score_signature_v1"

#: The historical research twin of the promoted human model. Imported rather
#: than restated: a row that says `equity_shaffer_v1_pit` must mean exactly one
#: thing, and `pit_store` is where that string is defined.
V1_MODEL_VERSION = EQUITY_PIT_MODEL_VERSION


# ==========================================================================
# (1) PILLARS AND SIGNATURES
# ==========================================================================

PILLAR_VALUATION = "valuation"
PILLAR_GROWTH = "growth"
PILLAR_PROFITABILITY = "profitability"
PILLAR_DEBT = "debt"

#: CANONICAL ORDER, and it is load-bearing. A signature is a string compared
#: for equality and grouped on, so 'GPD' and 'DPG' must not both be reachable;
#: `signature_of` always emits this order regardless of the input's order.
PILLARS: tuple[str, ...] = (PILLAR_VALUATION, PILLAR_GROWTH,
                            PILLAR_PROFITABILITY, PILLAR_DEBT)

PILLAR_LETTER: dict[str, str] = {
    PILLAR_VALUATION: "V",
    PILLAR_GROWTH: "G",
    PILLAR_PROFITABILITY: "P",
    PILLAR_DEBT: "D",
}

PILLAR_LABEL: dict[str, str] = {
    PILLAR_VALUATION: "Valuation",
    PILLAR_GROWTH: "Growth",
    PILLAR_PROFITABILITY: "Profitability",
    PILLAR_DEBT: "Debt / Financial Strength",
}

_LETTER_PILLAR = {v: k for k, v in PILLAR_LETTER.items()}


def _label_of(key: str) -> str:
    """A display label for a pillar OR a block.

    Rendering spans both lineages, so it needs a lookup that does too. This is
    the ONLY place the two vocabularies are merged, and it is safe precisely
    because it is display-only: nothing downstream of a label is arithmetic.
    v1 'valuation' and v2 'valuation' share a label and are still different
    positions in different models -- which is why the WEIGHT tables stay apart.
    """
    try:
        return V2_BLOCK_LABEL[key] if key in V2_BLOCK_LABEL else PILLAR_LABEL[key]
    except KeyError:
        return str(key)

#: All four present. The ONLY signature that is directly comparable with
#: another model version's full score, and even then only within a version.
SIGNATURE_FULL = "VGPD"

#: No pillar available at all. A real state -- `calculate_company_raw_score`
#: returns None for it -- and it needs a value rather than a NULL, because a
#: NULL signature is indistinguishable from a writer that forgot the column.
SIGNATURE_NONE = "NONE"


def _finite(value: Any) -> bool:
    """A pillar is PRESENT iff its score is a finite real number.

    None, NaN and the infinities are all absence. NaN in particular: it
    compares unequal to everything, propagates through the weighted sum, and
    would turn one missing pillar into a missing score with no signature to
    say which pillar did it.
    """
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def pillars_of(factor_scores: Mapping[str, Any]) -> tuple[str, ...]:
    """The pillars present in a factor-score mapping, in canonical order."""
    return tuple(p for p in PILLARS if _finite(factor_scores.get(p)))


def signature_of(factor_scores: Mapping[str, Any]) -> str:
    """'VGPD' / 'GPD' / ... for a mapping of pillar -> score. 'NONE' if empty.

    Accepts anything mapping-shaped, including a `dict[str, FactorResult]` --
    pass `{k: f.score if f.available else None for ...}`; this module takes
    numbers, so it never has to know the core's dataclasses.
    """
    present = pillars_of(factor_scores)
    if not present:
        return SIGNATURE_NONE
    return "".join(PILLAR_LETTER[p] for p in present)


def pillars_for_signature(signature: str,
                          letters: Optional[Mapping[str, str]] = None
                          ) -> tuple[str, ...]:
    """The inverse of `signature_of`. Raises on an unreadable signature.

    `letters` selects the ALPHABET. It defaults to v1's pillar letters; pass
    `V2_BLOCK_LETTER` to read a v2 signature. The parameter exists because
    'V' names a 0.40 valuation PILLAR in v1 and a 0.25 valuation BLOCK in v2,
    so a signature string is only meaningful together with the model whose
    alphabet it was written in -- the same lesson as the weight vector.
    """
    letter_map = dict(PILLAR_LETTER if letters is None else letters)
    order = tuple(letter_map)
    inverse = {v: k for k, v in letter_map.items()}
    text = (signature or "").strip().upper()
    if text in ("", SIGNATURE_NONE):
        return ()
    try:
        present = {inverse[ch] for ch in text}
    except KeyError as exc:
        raise ValueError(
            f"unreadable score signature {signature!r}: {exc.args[0]!r} is not "
            f"one of {''.join(letter_map[k] for k in order)}") from None
    if len(present) != len(text):
        raise ValueError(f"score signature {signature!r} repeats a pillar")
    return tuple(k for k in order if k in present)


def missing_of(signature: str) -> tuple[str, ...]:
    """Which pillars a signature is MISSING, in canonical order."""
    present = set(pillars_for_signature(signature))
    return tuple(p for p in PILLARS if p not in present)


def is_full_signature(signature: str) -> bool:
    return (signature or "").strip().upper() == SIGNATURE_FULL


# ==========================================================================
# (2) WEIGHTS
#
# v1 is RESTATED, not imported, for the same reason `pit_derive` restates
# MIN_EBITDA_COHORT: the frozen core must not be a runtime dependency of the
# research lineage. A restatement that drifts is worse than an import, so
# `test_pit_score_signature.py` imports `company_scoring` and pins both tables
# AND the arithmetic against it. That test is the freeze.
# ==========================================================================

#: company_scoring.MAJOR_WEIGHTS, restated. Sums to 1.00.
V1_MAJOR_WEIGHTS: dict[str, float] = {
    PILLAR_VALUATION: 0.40,
    PILLAR_GROWTH: 0.25,
    PILLAR_PROFITABILITY: 0.20,
    PILLAR_DEBT: 0.15,
}

#: company_scoring.COMPANY_SCALE, restated. CompanyScore = 0.75 x raw.
V1_COMPANY_SCALE = 0.75

V1_RAW_MIN, V1_RAW_MAX = -100.0, 100.0
V1_COMPANY_MIN, V1_COMPANY_MAX = -75.0, 75.0

# ==========================================================================
# THE v2 BLOCKS -- a DIFFERENT SET from v1's pillars, corrected 2026-09-21
#
# CORRECTION OF RECORD. Until 2026-09-21 this module held
#
#     V2_MAJOR_WEIGHTS = {valuation .35, growth .25, profitability .15,
#                         debt .10}
#
# which is v1's FOUR PILLARS carrying v2's WEIGHT VECTOR -- not the v2 design.
# The approved v2 architecture is EBITDA-CENTRED:
#
#     0.35 E  +  0.25 V  +  0.15 G  +  0.10 Q
#
# The vector (0.35, 0.25, 0.15, 0.10) is identical in both, which is exactly
# why the error survived review: comparing the numbers proves nothing, because
# THE LABELS ARE PART OF THE MODEL. Under the old table valuation carried 0.35
# and there was no EBITDA block at all, inverting the one instruction the v2
# redesign existed to serve. Caught before any feature or score row existed.
#
# v1's PILLARS above are UNTOUCHED: `equity_shaffer_v1_pit` really does have
# valuation / growth / profitability / debt, and VGPD is correct for it. The
# two models have different block sets, which is what a model version is for.
# ==========================================================================

V2_BLOCK_EBITDA = "ebitda_strength"
V2_BLOCK_VALUATION = "valuation"
V2_BLOCK_GROWTH = "real_growth"
V2_BLOCK_QUALITY = "financial_quality"

#: CANONICAL ORDER, load-bearing for the same reason v1's is: a signature is a
#: string compared for equality, so one set of present blocks must have exactly
#: one spelling.
V2_BLOCKS: tuple[str, ...] = (V2_BLOCK_EBITDA, V2_BLOCK_VALUATION,
                              V2_BLOCK_GROWTH, V2_BLOCK_QUALITY)

V2_BLOCK_LETTER: dict[str, str] = {
    V2_BLOCK_EBITDA: "E",
    V2_BLOCK_VALUATION: "V",
    V2_BLOCK_GROWTH: "G",
    V2_BLOCK_QUALITY: "Q",
}

V2_BLOCK_LABEL: dict[str, str] = {
    V2_BLOCK_EBITDA: "EBITDA / operating strength",
    V2_BLOCK_VALUATION: "Valuation",
    V2_BLOCK_GROWTH: "Real growth",
    V2_BLOCK_QUALITY: "Financial quality",
}

#: All four v2 blocks present. NOT 'VGPD' -- a v2 signature and a v1 signature
#: are different alphabets over different models, and the letters happening to
#: overlap ('V', 'G') is precisely the trap this correction closes.
V2_SIGNATURE_FULL = "EVGQ"

#: THE APPROVED v2 WEIGHTS. EBITDA-centred, valuation substantial but
#: secondary, real growth next, quality smallest, with the sector overlay
#: living OUTSIDE this table at +/-15. The four sum to 0.85 = COMPANY_SCALE_V2,
#: and that identity is the whole mechanism -- see the module docstring and
#: `score_v2`.
V2_MAJOR_WEIGHTS: dict[str, float] = {
    V2_BLOCK_EBITDA: 0.35,
    V2_BLOCK_VALUATION: 0.25,
    V2_BLOCK_GROWTH: 0.15,
    V2_BLOCK_QUALITY: 0.10,
}

#: THE SEMANTIC TRIPLE. A weight table is only meaningful as
#: (key, semantic id, weight) together: this bug proved that comparing
#: [0.35, 0.25, 0.15, 0.10] against [0.35, 0.25, 0.15, 0.10] is insufficient,
#: because both were true and the models were still different. Anything that
#: claims to carry the v2 weights -- code, documentation, replay metadata, a
#: frozen component -- must match on all three.
V2_WEIGHT_SEMANTICS: tuple[tuple[str, str, float], ...] = tuple(
    (V2_BLOCK_LETTER[b], b, V2_MAJOR_WEIGHTS[b]) for b in V2_BLOCKS
)

#: CompanyScoreV2 = 0.85 x rawV2. Equal by construction to sum(V2_MAJOR_WEIGHTS),
#: so the scaled score of a FULL company is exactly 0.35V + 0.25G + 0.15P +
#: 0.10D and the scaled score of a partial one is the same expression with the
#: absent terms dropped. Applied ONCE, here, mirroring v1's single 0.75.
COMPANY_SCALE_V2 = 0.85

V2_RAW_MIN, V2_RAW_MAX = -100.0, 100.0
V2_COMPANY_MIN, V2_COMPANY_MAX = -85.0, 85.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def weight_present(signature: str,
                   weights: Optional[Mapping[str, float]] = None) -> float:
    """The RAW original weight the present pillars carry. v1 GPD -> 0.60."""
    table = weights if weights is not None else V1_MAJOR_WEIGHTS
    letters = (V2_BLOCK_LETTER if set(table) == set(V2_BLOCKS)
               else PILLAR_LETTER)
    return sum(table[p] for p in pillars_for_signature(signature, letters))


def v2_signature_of(block_scores: Mapping[str, Any]) -> str:
    """'EVGQ' / 'EVG' / ... for a mapping of v2 BLOCK -> score.

    Separate from `signature_of` on purpose. That one speaks v1's alphabet
    over v1's pillars; this one speaks v2's over v2's blocks. Sharing a
    function would mean sharing an alphabet, and 'V' means a 0.40 pillar in
    one model and a 0.25 block in the other.
    """
    present = [b for b in V2_BLOCKS
               if block_scores.get(b) is not None]
    unknown = set(block_scores) - set(V2_BLOCKS)
    if unknown:
        raise KeyError(
            "not v2 blocks: %s. The v2 block set is %s -- v1's pillar names "
            "are a different model's vocabulary."
            % (sorted(unknown), ", ".join(V2_BLOCKS)))
    return "".join(V2_BLOCK_LETTER[b] for b in present) or "NONE"


def weight_coverage(signature: str,
                    weights: Optional[Mapping[str, float]] = None) -> float:
    """`original_weight_coverage`: present original weight / total original weight.

    Deliberately a FRACTION of the model's own declared total rather than a raw
    sum, so the number means the same thing across weight tables whose totals
    differ. v1 (total 1.00): VGPD -> 1.00, GPD -> 0.60, exactly the owner's
    figures. v2 (total 0.85): VGPD -> 1.00, GPD -> 0.50/0.85 = 0.5882.

    This is NOT `pit_score.coverage`, which is an existing, free-form column
    about SOURCE coverage. Two different questions; two different columns.
    """
    table = weights if weights is not None else V1_MAJOR_WEIGHTS
    total = sum(table.values())
    if total <= 0:
        return 0.0
    return weight_present(signature, table) / total


def effective_weights(signature: str,
                      weights: Mapping[str, float],
                      *,
                      renormalise: bool) -> dict[str, float]:
    """What each surviving pillar's weight ACTUALLY becomes.

    `renormalise=True` divides by the present total (the v1 rule: the survivors
    absorb the missing pillar's weight). `renormalise=False` divides by the
    FULL total (the v2 rule: the missing pillar's weight is not spent, and the
    survivors' weights do not move).

    The two agree exactly when nothing is missing, for ANY weight table. That
    is not a coincidence to be checked later -- it is the reason the v1/v2
    distinction is invisible on a full row and decisive on a partial one.
    """
    # The alphabet follows the WEIGHT TABLE, not a module default: the caller
    # has already told us which model this is by handing us its weights.
    letters = (V2_BLOCK_LETTER if set(weights) == set(V2_BLOCKS)
               else PILLAR_LETTER)
    present = pillars_for_signature(signature, letters)
    if not present:
        return {}
    denominator = (sum(weights[p] for p in present) if renormalise
                   else sum(weights.values()))
    if denominator <= 0:
        return {}
    return {p: weights[p] / denominator for p in present}


# ==========================================================================
# (3) THE MARKER VOCABULARY
#
# A boolean would have been cheaper and would have been wrong. `partial = 1`
# on a row is read months later by someone who has to go and find out what the
# author meant by partial, and a v1 renormalised partial and a v2 unrenormalised
# partial are not the same object. The marker carries its own meaning, and the
# phrase the owner asked for is IN the value, so a row cannot be misread by
# anyone who can read the column.
# ==========================================================================

NOT_COMPARABLE_PHRASE = "not directly comparable with full scores"

#: All four pillars present. Comparable with other full scores of the SAME
#: model version -- never across versions, which is why the pooling key is
#: (model_version, signature) and not the signature alone.
FULL_SHAFFER_SCORE = "FULL_SHAFFER_SCORE"

#: v1, a pillar missing, arithmetic renormalised AND PRESERVED. The score is a
#: real v1 number produced by the real v1 rule; it is simply a different model
#: state, and pooling it with a full score averages two models.
V1_RENORMALISED_PARTIAL = "V1_RENORMALISED_PARTIAL_NOT_DIRECTLY_COMPARABLE"

#: v2, a pillar missing, NO cross-pillar renormalisation. On a smaller scale
#: than a full v2 score by construction.
PARTIAL_SHAFFER_SCORE = "PARTIAL_SHAFFER_SCORE_NOT_DIRECTLY_COMPARABLE"

#: No pillar available. There is no score, and the row says why rather than
#: carrying a zero.
NOT_SCORED = "NOT_SCORED_NO_PILLAR_AVAILABLE"

MARKERS: tuple[str, ...] = (FULL_SHAFFER_SCORE, V1_RENORMALISED_PARTIAL,
                            PARTIAL_SHAFFER_SCORE, NOT_SCORED)

#: The markers a default evaluation path may compare with one another. Exactly
#: one member, on purpose.
COMPARABLE_MARKERS: frozenset[str] = frozenset({FULL_SHAFFER_SCORE})


# ==========================================================================
# (3b) WHY A PILLAR IS ABSENT
#
# The signature says WHICH pillar is missing. It cannot say why, and the two
# questions have different answers and different fixes: "no peer carried the
# primitives" is a coverage problem that a better ingest closes, while "the
# only cohort large enough was an SEC Corporation Finance office" is a problem
# no amount of data fixes, because the comparison itself is meaningless.
#
# The reason strings are NOT defined here. They are `pit_store`'s
# `unavailable_reason` vocabulary, imported, so the string a peer-selection
# gate writes into `pit_feature.unavailable_reason` is the SAME OBJECT the
# score row reports -- there is exactly one vocabulary, not a parallel one for
# scoring.
# ==========================================================================

#: The owner's first-class outcome, re-exported from `pit_store` so a consumer
#: of the score module never has to reach past it for the constant.
VALUATION_PEER_SET_INSUFFICIENT = REASON_VALUATION_PEER_SET_INSUFFICIENT

#: One sentence per reason, for the rendered partial score. A reason with no
#: entry is printed as itself: an unknown reason must still reach the reader.
REASON_LABELS: dict[str, str] = {
    REASON_VALUATION_PEER_SET_INSUFFICIENT: (
        "no ADMISSIBLE valuation peer set: either too few peers carried the "
        "primitives, or the only rung that would have carried enough is one a "
        "valuation comparison may not use (the SEC Office fallback is not a "
        "company valuation peer group)"),
    REASON_NO_PEERS: "too few peers carried the primitives to form a cohort",
}


def reason_label(reason: str) -> str:
    """The sentence for one reason, or the reason itself when it has none."""
    return REASON_LABELS.get(str(reason), str(reason))


# ==========================================================================
# (4) SCORING
# ==========================================================================

@dataclass(frozen=True)
class PillarScore:
    """One company's score, with everything needed to refuse to pool it.

    `raw_score` and `company_score` are the numbers; every other field exists
    so that a reader -- or a backtest -- can tell WHICH MODEL produced them
    without re-deriving anything.
    """

    model_version: str
    signature: str
    pillars_present: tuple[str, ...]
    pillars_missing: tuple[str, ...]
    original_weight_coverage: float
    original_weight_present: float
    original_weight_total: float
    effective_weights: dict[str, float]
    contributions: dict[str, float]
    renormalised: bool
    scale: float
    raw_score: Optional[float]
    company_score: Optional[float]
    partial_score_marker: str
    comparable_with_full: bool
    notes: tuple[str, ...] = ()
    #: pillar -> the `unavailable_reason` that removed it, for the pillars the
    #: caller could explain. Absent pillars with no supplied reason simply do
    #: not appear: "the caller did not say" and "there was no reason" are
    #: different, and inventing a default would erase the difference.
    unavailable_reasons: dict[str, str] = field(default_factory=dict)

    @property
    def is_partial(self) -> bool:
        return self.partial_score_marker in (V1_RENORMALISED_PARTIAL,
                                             PARTIAL_SHAFFER_SCORE)

    @property
    def scored(self) -> bool:
        return self.company_score is not None

    @property
    def pool_key(self) -> tuple[str, str]:
        """(model_version, signature). The unit an evaluation may average over."""
        return (self.model_version, self.signature)

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_version": self.model_version,
            "score_signature": self.signature,
            "pillars_present": list(self.pillars_present),
            "pillars_missing": list(self.pillars_missing),
            "original_weight_coverage": self.original_weight_coverage,
            "original_weight_present": self.original_weight_present,
            "original_weight_total": self.original_weight_total,
            "effective_weights": dict(self.effective_weights),
            "contributions": dict(self.contributions),
            "renormalised": self.renormalised,
            "scale": self.scale,
            "raw_score": self.raw_score,
            "company_score": self.company_score,
            "partial_score_marker": self.partial_score_marker,
            "comparable_with_full": self.comparable_with_full,
            "notes": list(self.notes),
            "unavailable_reasons": dict(self.unavailable_reasons),
        }

    @property
    def valuation_peer_set_insufficient(self) -> bool:
        """Whether the valuation pillar is absent for THE PEER-SET reason.

        The question a reader of a partial score asks first, answered off the
        row rather than by parsing a note: this row is partial because no
        admissible valuation peer set could be formed, not because a number
        was missing.
        """
        return (self.unavailable_reasons.get(PILLAR_VALUATION)
                == REASON_VALUATION_PEER_SET_INSUFFICIENT)


def _score(factor_scores: Mapping[str, Any],
           *,
           model_version: str,
           weights: Mapping[str, float],
           scale: float,
           renormalise: bool,
           raw_bounds: tuple[float, float],
           company_bounds: tuple[float, float],
           partial_marker: str,
           reasons: Optional[Mapping[str, str]] = None) -> PillarScore:
    """The one weighted blend. v1 and v2 differ ONLY in the four arguments.

    `reasons` maps a MISSING pillar to the `unavailable_reason` that removed
    it -- `pit_store`'s vocabulary, which is what a peer-selection gate writes.
    A reason for a pillar that is present is dropped: the row says what
    happened, and a reason beside a number would be a contradiction.
    """
    # THE ALPHABET FOLLOWS THE WEIGHT TABLE. `_score` is the one blend shared
    # by both lineages, so it must not assume v1's pillar names -- handing it
    # v2's blocks and reading them with v1's alphabet is how the 2026-09-21
    # defect would reappear one level lower.
    is_v2 = set(weights) == set(V2_BLOCKS)
    keys = V2_BLOCKS if is_v2 else PILLARS
    letters = V2_BLOCK_LETTER if is_v2 else PILLAR_LETTER
    signature = (v2_signature_of(factor_scores) if is_v2
                 else signature_of(factor_scores))
    present = pillars_for_signature(signature, letters)
    missing = tuple(k for k in keys if k not in set(present))
    total = sum(weights.values())
    carried = sum(weights[p] for p in present)
    coverage = carried / total if total > 0 else 0.0
    notes: list[str] = []
    why = {p: str(r) for p, r in (reasons or {}).items()
           if p in missing and r}

    if not present:
        return PillarScore(
            model_version=model_version, signature=SIGNATURE_NONE,
            pillars_present=(), pillars_missing=keys,
            original_weight_coverage=0.0, original_weight_present=0.0,
            original_weight_total=total, effective_weights={}, contributions={},
            renormalised=renormalise, scale=scale, raw_score=None,
            company_score=None, partial_score_marker=NOT_SCORED,
            comparable_with_full=False,
            notes=("No major pillar could be calculated -- no company score. "
                   "This is an absence, never a zero.",),
            unavailable_reasons={p: str(r) for p, r in (reasons or {}).items()
                                 if r})

    used = effective_weights(signature, weights, renormalise=renormalise)
    contributions = {p: used[p] * float(factor_scores[p]) for p in present}
    raw = _clamp(sum(contributions.values()), *raw_bounds)
    company = _clamp(scale * raw, *company_bounds)

    # "Full" means every block OF THIS MODEL, not every pillar of v1.
    labels = V2_BLOCK_LABEL if is_v2 else PILLAR_LABEL
    full = (signature == (V2_SIGNATURE_FULL if is_v2 else SIGNATURE_FULL))
    marker = FULL_SHAFFER_SCORE if full else partial_marker
    if missing:
        names = ", ".join(
            labels[p] + (f" ({why[p]})" if p in why else "")
            for p in missing)
        if renormalise:
            notes.append(
                f"Unavailable major factors excluded: {names}. Remaining "
                f"weights renormalised over {carried:.2f}. The surviving "
                f"pillars carry more weight here than the model declares, so "
                f"this row is {NOT_COMPARABLE_PHRASE}.")
        else:
            notes.append(
                f"PARTIAL SHAFFER SCORE. Missing: {names}. Its "
                f"{total - carried:.2f} of weight is NOT redistributed -- the "
                f"surviving pillars keep their declared effective weights and "
                f"this score sits on a smaller scale than a full one. "
                f"{NOT_COMPARABLE_PHRASE.capitalize()}.")
        for pillar, reason in sorted(why.items()):
            notes.append(f"{labels[pillar]} unavailable -- {reason}: "
                         f"{reason_label(reason)}.")
    return PillarScore(
        model_version=model_version, signature=signature,
        pillars_present=present, pillars_missing=missing,
        original_weight_coverage=coverage, original_weight_present=carried,
        original_weight_total=total, effective_weights=used,
        contributions=contributions, renormalised=renormalise, scale=scale,
        raw_score=raw, company_score=company, partial_score_marker=marker,
        comparable_with_full=(marker in COMPARABLE_MARKERS),
        notes=tuple(notes), unavailable_reasons=why)


def score_v1(factor_scores: Mapping[str, Any],
             model_version: str = V1_MODEL_VERSION,
             reasons: Optional[Mapping[str, str]] = None) -> PillarScore:
    """The v1 historical baseline. THE ARITHMETIC IS THE FROZEN CORE'S.

        raw     = SUM over present of (w_p / SUM present w) x s_p,  clamped +-100
        company = 0.75 x raw,                                       clamped +-75

    byte-for-byte the rule in `company_scoring.calculate_company_raw_score` and
    `calculate_company_score`, because the v1 replay exists to test the ACTUAL
    original Shaffer logic and a "corrected" baseline tests nothing.

    What is new is entirely ADDITIVE: the signature, the coverage and the
    marker. Nothing about the number changes; what changes is that the number
    can no longer be pooled with a different model's by accident.
    """
    return _score(factor_scores,
                  model_version=model_version,
                  weights=V1_MAJOR_WEIGHTS,
                  scale=V1_COMPANY_SCALE,
                  renormalise=True,
                  raw_bounds=(V1_RAW_MIN, V1_RAW_MAX),
                  company_bounds=(V1_COMPANY_MIN, V1_COMPANY_MAX),
                  partial_marker=V1_RENORMALISED_PARTIAL,
                  reasons=reasons)


def score_v2(factor_scores: Mapping[str, Any],
             model_version: str = CANDIDATE_MODEL_VERSION,
             reasons: Optional[Mapping[str, str]] = None) -> PillarScore:
    """The candidate. NO CROSS-PILLAR RENORMALISATION.

        raw     = SUM over present of (w_p / 0.85) x s_p,  clamped +-100
        company = 0.85 x raw,                              clamped +-85
                = 0.35V + 0.25G + 0.15P + 0.10D, absent terms simply dropped

    The denominator is the FULL weight total, present or not, so a missing
    pillar's weight is not spent and the survivors' effective weights do not
    move. Compare: losing valuation moves growth's effective weight from 0.2500
    to 0.4167 under v1 (x1.667) and from 0.2941 to 0.2941 under v2 (x1.000).

    Renormalisation WITHIN a pillar stays allowed and is untouched by this
    module -- `company_scoring._blend` renormalises 0.45/0.35/0.20 inside G,
    and that is a statement about three measurements of the same thing. Losing
    an entire pillar is a statement about a different thing, and it must not
    silently promote the survivors.
    """
    return _score(factor_scores,
                  model_version=model_version,
                  weights=V2_MAJOR_WEIGHTS,
                  scale=COMPANY_SCALE_V2,
                  renormalise=False,
                  raw_bounds=(V2_RAW_MIN, V2_RAW_MAX),
                  company_bounds=(V2_COMPANY_MIN, V2_COMPANY_MAX),
                  partial_marker=PARTIAL_SHAFFER_SCORE,
                  reasons=reasons)


def score(factor_scores: Mapping[str, Any], lineage: str = "v1",
          reasons: Optional[Mapping[str, str]] = None) -> PillarScore:
    """`score(x, 'v1')` or `score(x, 'v2')`. Raises on anything else.

    There is no default that silently picks a lineage: choosing which model
    computed a number is exactly the decision this module exists to make
    explicit.
    """
    key = str(lineage).strip().lower()
    if key in ("v1", V1_MODEL_VERSION, "equity_shaffer_v1"):
        return score_v1(factor_scores, reasons=reasons)
    if key in ("v2", CANDIDATE_MODEL_VERSION, "candidate"):
        return score_v2(factor_scores, reasons=reasons)
    raise ValueError(f"unknown lineage {lineage!r}; use 'v1' or 'v2'")


def pit_score_fields(result: PillarScore) -> dict[str, Any]:
    """The values a replay writes to `pit_score`, for the columns below.

    Declarative: builds a dict and writes nothing. Handed to
    `pit_store.save_score(**fields)` by a replay that is NOT written yet, so
    that the three new columns are populated from one place rather than from
    whichever caller remembers them.
    """
    return {
        "score_signature": result.signature,
        "original_weight_coverage": result.original_weight_coverage,
        "partial_score_marker": result.partial_score_marker,
        "effective_weights": dict(result.effective_weights),
        "missing_factors": list(result.pillars_missing),
        # WHY each missing pillar is missing, in pit_store's `unavailable_reason`
        # vocabulary. It maps to no pit_score COLUMN on purpose -- the three
        # columns this module specifies are the signature, the coverage and the
        # marker, and the reasons belong inside `notes_json` /
        # `missing_factors_json` beside the pillar they explain rather than in a
        # fourth column that would have to be kept in step with pit_feature's.
        "unavailable_reasons": dict(result.unavailable_reasons),
        "notes": list(result.notes),
        "company_score": result.company_score,
    }


# ==========================================================================
# (5) EVALUATION: the default path separates; pooling is a deliberate act
# ==========================================================================

class SignaturePoolingRefused(RuntimeError):
    """Raised when something tries to average two different models silently."""


def _row_key(row: Any) -> tuple[str, str]:
    """(model_version, signature) for a PillarScore, a dict or a sqlite3.Row.

    A row whose signature is missing or empty is NOT given a benign default.
    It raises, because "the column was not written" and "all four pillars were
    present" are the two readings a blank would have, and one of them is the
    error this whole module exists to prevent.
    """
    if isinstance(row, PillarScore):
        return row.pool_key
    try:
        model = row["model_version"]
        signature = row["score_signature"]
    except (TypeError, KeyError, IndexError):
        model = getattr(row, "model_version", None)
        signature = getattr(row, "score_signature",
                            getattr(row, "signature", None))
    if not model:
        raise SignaturePoolingRefused(
            "a score row carries no model_version; it cannot be grouped, "
            "because a v1 VGPD score and a v2 VGPD score are different models")
    if not signature:
        raise SignaturePoolingRefused(
            "a score row carries no score_signature. Blank is not 'all four "
            "pillars': it is 'nobody wrote the column', and the two readings "
            "differ by the entire valuation factor. Stamp the row or drop it.")
    return (str(model), str(signature).strip().upper())


@dataclass(frozen=True)
class Grouping:
    """Rows split by (model_version, signature). THE DEFAULT RESULT.

    There is no `.all` property and no iteration over the flattened rows, on
    purpose: the only way out of this object with everything in one list is
    `pool`, which refuses unless the caller says in writing that it means to
    average two models.
    """

    groups: dict[tuple[str, str], list[Any]]
    n_rows: int

    @property
    def keys(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self.groups))

    @property
    def n_groups(self) -> int:
        return len(self.groups)

    @property
    def is_mixed(self) -> bool:
        return len(self.groups) > 1

    @property
    def full_key(self) -> tuple[str, str]:
        """The key an evaluation usually wants: full four-pillar rows.

        Raises if the grouping holds more than one model version, because
        "the full scores" is then an ambiguous request.
        """
        models = {model for model, _ in self.groups}
        if len(models) != 1:
            raise SignaturePoolingRefused(
                f"full_key is ambiguous across model versions {sorted(models)}")
        return (models.pop(), SIGNATURE_FULL)

    def counts(self) -> dict[tuple[str, str], int]:
        return {key: len(rows) for key, rows in sorted(self.groups.items())}

    def coverage_by_group(self) -> dict[tuple[str, str], float]:
        """`original_weight_coverage` implied by each group's signature.

        Computed from the signature under the v1 table, which is what makes it
        comparable across groups: it answers "how much of the ORIGINAL model
        does this group represent", and the original model is v1.
        """
        out: dict[tuple[str, str], float] = {}
        for key in self.groups:
            _, signature = key
            try:
                out[key] = weight_coverage(signature)
            except ValueError:
                out[key] = float("nan")
        return out


@dataclass(frozen=True)
class Pool:
    """The RECORD of a deliberate pooling. Not a list -- a list with a reason.

    Returned rather than a bare list so the justification travels with the
    pooled sample. A metric computed on `.rows` can print `.warning` beside it,
    which is the difference between a caveated result and an uncaveated one.
    """

    rows: list[Any]
    keys: tuple[tuple[str, str], ...]
    justification: str
    warning: str

    def __len__(self) -> int:
        return len(self.rows)


def group_by_signature(rows: Iterable[Any]) -> dict[tuple[str, str], list[Any]]:
    """Split rows by (model_version, score_signature). The primitive."""
    groups: dict[tuple[str, str], list[Any]] = {}
    for row in rows:
        groups.setdefault(_row_key(row), []).append(row)
    return groups


def evaluation_groups(rows: Iterable[Any]) -> Grouping:
    """THE DEFAULT EVALUATION PATH. Separates; never pools.

    Every downstream metric -- IC, decile spread, hit rate, the lot -- is
    computed per group and reported per group. If the answer differs across
    groups, that IS the answer: two models were being averaged.
    """
    materialised = list(rows)
    return Grouping(groups=group_by_signature(materialised),
                    n_rows=len(materialised))


def pool(rows: Iterable[Any],
         *,
         acknowledge_different_models: bool = False,
         justification: str = "") -> Pool:
    """Flatten rows into one sample. REFUSES a mixed set by default.

    A single-group call needs no ceremony and none is demanded: there is
    nothing to pool, and the function returns the rows with an empty warning.

    A mixed call raises `SignaturePoolingRefused` unless BOTH
    `acknowledge_different_models=True` and a non-empty `justification` are
    given. Two conditions rather than one because a bare boolean is a keyword
    a caller adds to make an exception go away, and a sentence about why is
    not -- and because the sentence is what a reader of the result needs.
    """
    materialised = list(rows)
    groups = group_by_signature(materialised)
    keys = tuple(sorted(groups))

    if len(groups) <= 1:
        return Pool(rows=materialised, keys=keys,
                    justification=justification.strip(), warning="")

    described = ", ".join(f"{model}/{signature} (n={len(groups[(model, signature)])}, "
                          f"original weight coverage "
                          f"{_coverage_text(signature)})"
                          for model, signature in keys)
    if not acknowledge_different_models or not justification.strip():
        raise SignaturePoolingRefused(
            "refusing to pool " + str(len(groups)) + " score signatures into "
            "one sample: " + described + ". These are different models wearing "
            "the same name -- a renormalised partial has promoted its "
            "surviving pillars and a full score has not, so their mean is the "
            "mean of two models. Use evaluation_groups() to report them "
            "separately, or pass acknowledge_different_models=True together "
            "with a justification saying why the average is meaningful.")
    return Pool(
        rows=materialised, keys=keys, justification=justification.strip(),
        warning=("POOLED ACROSS SIGNATURES -- " + described + ". Every metric "
                 "computed on this sample is an average over more than one "
                 "model state. Justification on record: "
                 + justification.strip()))


def _coverage_text(signature: str) -> str:
    try:
        return f"{weight_coverage(signature):.2f}"
    except ValueError:
        return "unreadable"


# ==========================================================================
# (6) RENDERING
# ==========================================================================

def _fmt(value: Optional[float], places: int = 2) -> str:
    return "unavailable" if value is None else f"{value:.{places}f}"


def render_score(result: PillarScore, *, title: str = "") -> str:
    """One score, full or partial, with everything needed to read it."""
    if result.partial_score_marker == PARTIAL_SHAFFER_SCORE:
        return render_partial_score(result, title=title)
    head = title or ("SHAFFER SCORE" if not result.is_partial
                     else "SHAFFER SCORE (v1 baseline, renormalised)")
    lines = [head, f"  model            {result.model_version}"]
    lines += _score_body(result)
    return "\n".join(lines)


def render_partial_score(result: PillarScore, *, title: str = "") -> str:
    """THE PARTIAL-SCORE OUTPUT THE OWNER DESCRIBED.

    Which pillar is missing, the core coverage, and an explicit
    not-directly-comparable marker -- on the face of the output, not in a
    footnote a reader may skip.
    """
    if not result.is_partial and result.partial_score_marker != NOT_SCORED:
        return render_score(result, title=title)
    head = title or "PARTIAL SHAFFER SCORE"
    lines = [head, f"  model            {result.model_version}"]
    #: The notes are suppressed here and only here: the block below says the
    #: same three things in the form the owner asked for, and a reader who
    #: meets the prose first stops reading before the marker.
    lines += _score_body(result, show_notes=False)
    lines.append("  " + "-" * 62)
    if result.pillars_missing:
        lines.append("  MISSING PILLAR   "
                     + ", ".join(_label_of(p) for p in result.pillars_missing))
    for pillar in result.pillars_missing:
        reason = result.unavailable_reasons.get(pillar)
        if not reason:
            continue
        lines.append(f"  BECAUSE          {reason}")
        for chunk in _wrap(reason_label(reason), 62):
            lines.append(f"                   {chunk}")
    lines.append(f"  CORE COVERAGE    {result.original_weight_coverage:.4f}"
                 f"   ({result.original_weight_present:.2f} of "
                 f"{result.original_weight_total:.2f} declared pillar weight)")
    lines.append("  COMPARABILITY    " + NOT_COMPARABLE_PHRASE.upper())
    lines.append(f"                   marker: {result.partial_score_marker}")
    return "\n".join(lines)


def _score_body(result: PillarScore, *, show_notes: bool = True) -> list[str]:
    lines = [
        f"  signature        {result.signature}"
        f"   ({len(result.pillars_present)} of {len(PILLARS)} pillars)",
        f"  raw score        {_fmt(result.raw_score)}",
        f"  company score    {_fmt(result.company_score)}"
        f"   (= {result.scale:.2f} x raw"
        + (", renormalised over the PRESENT weights)" if result.renormalised
           else ", weights NOT renormalised across pillars)"),
        f"  weight coverage  {result.original_weight_coverage:.4f}"
        f"   ({result.original_weight_present:.2f} of "
        f"{result.original_weight_total:.2f})",
        f"  marker           {result.partial_score_marker}",
    ]
    if result.effective_weights:
        lines.append("  effective weights            weight    contribution")
        for pillar in PILLARS:
            if pillar not in result.effective_weights:
                lines.append(f"    {_label_of(pillar):<26}"
                             f"{'--':>8}{'ABSENT':>16}")
                continue
            lines.append(f"    {_label_of(pillar):<26}"
                         f"{result.effective_weights[pillar]:>8.4f}"
                         f"{result.contributions[pillar]:>16.3f}")
    if show_notes:
        for note in result.notes:
            for line in _wrap(note, 62):
                lines.append("  " + line)
    return lines


def _wrap(text: str, width: int) -> list[str]:
    words, out, line = text.split(), [], ""
    for word in words:
        if line and len(line) + 1 + len(word) > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out or [""]


def render_comparison(v1: PillarScore, v2: PillarScore, *,
                      label: str = "") -> str:
    """The SAME company under both paths, side by side.

    The point of the table is the effective-weight rows, not the score rows: a
    reader can argue about whether 17.69 or 13.80 is the better estimate, and
    cannot argue about growth's weight moving from 0.25 to 0.4167 in a model
    whose published weight for growth is 0.25.
    """
    lines = [label or "SAME COMPANY, BOTH PATHS", ""]
    lines.append(f"  {'':<28}{'v1 (renormalised)':>22}{'v2 (partial)':>22}")
    lines.append("  " + "-" * 70)

    def row(name: str, a: str, b: str) -> None:
        lines.append(f"  {name:<28}{a:>22}{b:>22}")

    row("signature", v1.signature, v2.signature)
    row("raw score", _fmt(v1.raw_score), _fmt(v2.raw_score))
    row("company score", _fmt(v1.company_score), _fmt(v2.company_score))
    row("weight coverage", f"{v1.original_weight_coverage:.4f}",
        f"{v2.original_weight_coverage:.4f}")
    lines.append("  " + "-" * 70)
    for pillar in PILLARS:
        row("eff. weight " + PILLAR_LETTER[pillar],
            f"{v1.effective_weights[pillar]:.4f}"
            if pillar in v1.effective_weights else "--",
            f"{v2.effective_weights[pillar]:.4f}"
            if pillar in v2.effective_weights else "--")
    lines.append("  " + "-" * 70)
    row("marker", _short(v1.partial_score_marker), _short(v2.partial_score_marker))
    row("comparable with full",
        "yes" if v1.comparable_with_full else "NO",
        "yes" if v2.comparable_with_full else "NO")
    return "\n".join(lines)


def _short(marker: str) -> str:
    return {FULL_SHAFFER_SCORE: "FULL",
            V1_RENORMALISED_PARTIAL: "V1_RENORM_PARTIAL",
            PARTIAL_SHAFFER_SCORE: "PARTIAL",
            NOT_SCORED: "NOT_SCORED"}.get(marker, marker)


def render_grouping(grouping: Grouping, *, label: str = "") -> str:
    lines = [label or "EVALUATION GROUPS (the default path: separated)", ""]
    lines.append(f"  {'model':<32}{'sig':>6}{'n':>6}{'weight coverage':>18}")
    lines.append("  " + "-" * 62)
    coverage = grouping.coverage_by_group()
    for key in grouping.keys:
        model, signature = key
        lines.append(f"  {model:<32}{signature:>6}{len(grouping.groups[key]):>6}"
                     f"{coverage[key]:>18.4f}")
    lines.append("  " + "-" * 62)
    lines.append(f"  {grouping.n_rows} rows in {grouping.n_groups} group(s). "
                 + ("MIXED -- pooling requires a deliberate act."
                    if grouping.is_mixed else "One group; nothing to pool."))
    return "\n".join(lines)


# ==========================================================================
# (7) THE WORKED DEMONSTRATION
#
# Realistic pillar scores, chosen so that the failure is a RANKING failure and
# not merely a level shift -- a level shift is arguable, a ranking inversion is
# not. The valuation pillar is absent on the second company for the measured
# reason: over 39,038 peer sets the EV/EBITDA cohort has median 1 member and
# clears the 3-member floor in 34.2% of them.
# ==========================================================================

#: (label, {pillar: score or None}, why it looks like this)
#: v2 DEMONSTRATION INPUTS, IN v2 BLOCK NAMES.
#:
#: Deliberately NOT a translation of `DEMO_COMPANIES`. After the 2026-09-21
#: correction the two lineages have DIFFERENT BLOCK SETS, so there is no
#: row-for-row "same company under both models" comparison to be had: v1's
#: profitability and debt pillars have no v2 counterpart, and v2's EBITDA
#: block has no v1 counterpart. Manufacturing a mapping to keep the old
#: side-by-side report alive would re-assert exactly the equivalence the
#: correction denies. ILLUSTRATIVE, not measured.
DEMO_COMPANIES_V2: tuple[tuple[str, dict[str, Optional[float]], str], ...] = (
    ("A  priced industrial, all four blocks",
     {V2_BLOCK_EBITDA: 30.0, V2_BLOCK_VALUATION: 35.0,
      V2_BLOCK_GROWTH: 12.0, V2_BLOCK_QUALITY: 5.0},
     "EBITDA assembles, the valuation cohort forms, and real growth and "
     "quality both resolve: a genuine EVGQ."),
    ("B  no valuation cohort",
     {V2_BLOCK_EBITDA: 30.0, V2_BLOCK_VALUATION: None,
      V2_BLOCK_GROWTH: 48.0, V2_BLOCK_QUALITY: 22.0},
     "the modal case: the valuation cohort has too few usable members, so V "
     "drops out and its 0.25 is NOT redistributed -- the score shrinks."),
    ("C  FIRE: no EBITDA and no valuation",
     {V2_BLOCK_EBITDA: None, V2_BLOCK_VALUATION: None,
      V2_BLOCK_GROWTH: 30.0, V2_BLOCK_QUALITY: 18.0},
     "the case the v2 architecture is hardest on: with the 0.35 EBITDA block "
     "absent, 0.60 of the declared weight is unspent and the reachable range "
     "is +/-21.25 -- which is the design saying, correctly, that it knows "
     "little about this company."),
)


DEMO_COMPANIES: tuple[tuple[str, dict[str, Optional[float]], str], ...] = (
    ("A  priced industrial, all four pillars",
     {PILLAR_VALUATION: 35.0, PILLAR_GROWTH: 12.0,
      PILLAR_PROFITABILITY: 28.0, PILLAR_DEBT: 5.0},
     "a large filer in a wide sic2 cohort: price, a defensible share count, "
     "total_debt on concept_ladder_v2, cash and EBITDA all resolve, so the "
     "valuation cohort forms and the score is a genuine VGPD."),
    ("B  the median peer set: no EV/EBITDA cohort",
     {PILLAR_VALUATION: None, PILLAR_GROWTH: 48.0,
      PILLAR_PROFITABILITY: 22.0, PILLAR_DEBT: -15.0},
     "the modal case. The valuation cohort has fewer than 3 usable members, "
     "so V drops out; the binding leaf is the defensible share count "
     "(leave-one-out lifts P(N>=3) from 34.2% to 61.6%)."),
    ("C  FIRE: no EBITDA and no EV/EBITDA",
     {PILLAR_VALUATION: None, PILLAR_GROWTH: 30.0,
      PILLAR_PROFITABILITY: None, PILLAR_DEBT: 40.0},
     "banks and insurers largely do not tag OperatingIncomeLoss, so EBITDA "
     "does not assemble and the EBITDA-margin half of profitability goes "
     "with it. N_EBITDA median 6 and P(N>=3) 70% in this division."),
)


def render_demonstration() -> str:
    """The same companies scored both ways, with the ranking inversion named."""
    out: list[str] = []
    out.append("=" * 72)
    out.append("WORKED DEMONSTRATION -- the same companies, both paths")
    out.append("=" * 72)
    out.append("")
    scored: list[tuple[str, PillarScore, PillarScore]] = []
    for line in _wrap(
            "NOTE, from 2026-09-21: v1 and v2 no longer share a block set, so "
            "these are two demonstrations rather than one comparison. v1's "
            "pillars are valuation / growth / profitability / debt; v2's "
            "blocks are EBITDA / valuation / real growth / financial quality. "
            "A row-for-row 'same company, both models' table would assert an "
            "equivalence that does not exist.", 68):
        out.append(line)
    out.append("")
    for (label, factors, why), (v2_label, v2_factors, v2_why) in zip(
            DEMO_COMPANIES, DEMO_COMPANIES_V2):
        a, b = score_v1(factors), score_v2(v2_factors)
        scored.append((label, a, b))
        out.append("v1  " + label)
        for line in _wrap(why, 68):
            out.append("   " + line)
        out.append(render_partial_score(a))
        out.append("")
        out.append("v2  " + v2_label)
        for line in _wrap(v2_why, 68):
            out.append("   " + line)
        out.append(render_partial_score(b))
        out.append("")
        out.append("")

    out.append("-" * 72)
    out.append("THE TABLE THE BACKTEST WOULD SEE")
    out.append("-" * 72)
    out.append(f"  {'company':<44}{'v1':>9}{'v2':>9}{'sig':>6}")
    for label, a, b in scored:
        out.append(f"  {label:<44}{_fmt(a.company_score):>9}"
                   f"{_fmt(b.company_score):>9}{a.signature:>6}")
    out.append("")

    a_full = scored[0][1]
    b_part = scored[1][1]
    a_v2, b_v2 = scored[0][2], scored[1][2]
    gap_v1 = (b_part.company_score or 0.0) - (a_full.company_score or 0.0)
    gap_v2 = (b_v2.company_score or 0.0) - (a_v2.company_score or 0.0)
    out.append("READ THE FIRST TWO ROWS.")
    for line in _wrap(
            f"Under v1, company B scores {b_part.company_score:.4f} against "
            f"company A's {a_full.company_score:.4f} -- B OUTRANKS A by "
            f"{gap_v1:+.4f} on a score that never looked at B's valuation, "
            f"while A's valuation was measured and was good (+35). The "
            f"inversion is produced entirely by renormalisation: B's growth "
            f"pillar carries "
            f"{b_part.effective_weights[PILLAR_GROWTH]:.4f} of the score "
            f"where the model declares "
            f"{V1_MAJOR_WEIGHTS[PILLAR_GROWTH]:.4f}, a factor of "
            f"{b_part.effective_weights[PILLAR_GROWTH] / V1_MAJOR_WEIGHTS[PILLAR_GROWTH]:.3f}. "
            f"Under v2 the ANALOGOUS pair -- full, and missing its "
            f"valuation block; not the same rows, because the block sets "
            f"differ -- ranks "
            f"{a_v2.company_score:.4f} and {b_v2.company_score:.4f} "
            f"({gap_v2:+.4f}), B's real-growth weight is unchanged at "
            f"{b_v2.effective_weights[V2_BLOCK_GROWTH]:.4f}, and B's row is "
            f"stamped {PARTIAL_SHAFFER_SCORE} so it cannot enter the same "
            f"sample as A's without a deliberate act.", 70):
        out.append("  " + line)
    out.append("")
    c_v1, c_v2 = scored[2][1], scored[2][2]
    out.append("NOW READ THE THIRD.")
    for line in _wrap(
            f"Company C is a bank. Neither of its two valuation-and-earnings "
            f"pillars could be computed -- no EV/EBITDA cohort and no EBITDA "
            f"at all -- and under v1 it is the TOP-RANKED COMPANY IN THE "
            f"TABLE at {c_v1.company_score:.4f}, ahead of the one company "
            f"whose valuation was actually measured. Its growth pillar "
            f"carries {c_v1.effective_weights[PILLAR_GROWTH]:.4f} of a score "
            f"whose published growth weight is "
            f"{V1_MAJOR_WEIGHTS[PILLAR_GROWTH]:.2f}: a factor of "
            f"{c_v1.effective_weights[PILLAR_GROWTH] / V1_MAJOR_WEIGHTS[PILLAR_GROWTH]:.1f}. "
            f"That is a growth-and-leverage model sitting at the top of a "
            f"table headed 'Shaffer Score'. Under v2 it scores "
            f"{c_v2.company_score:.4f} and ranks last, with "
            f"original_weight_coverage {c_v2.original_weight_coverage:.4f} "
            f"and a marker on the row. Neither model knows what C's "
            f"valuation is; only one of them pretends the question was "
            f"answered.", 70):
        out.append("  " + line)
    out.append("")
    out.append("-" * 72)
    out.append("AND THE SAME COMPANY WITH AND WITHOUT ITS VALUATION PILLAR")
    out.append("-" * 72)
    with_v = dict(DEMO_COMPANIES[1][1])
    with_v[PILLAR_VALUATION] = -30.0
    with_v2 = dict(DEMO_COMPANIES_V2[1][1])
    with_v2[V2_BLOCK_VALUATION] = -30.0
    b_with_v1, b_with_v2 = score_v1(with_v), score_v2(with_v2)
    out.append(f"  {'':<28}{'V present (-30)':>22}{'V absent':>22}")
    out.append("  " + "-" * 70)
    out.append(f"  {'v1 company score':<28}{b_with_v1.company_score:>22.4f}"
               f"{b_part.company_score:>22.4f}")
    out.append(f"  {'v2 company score':<28}{b_with_v2.company_score:>22.4f}"
               f"{b_v2.company_score:>22.4f}")
    out.append("  " + "-" * 70)
    for line in _wrap(
            f"Losing a valuation pillar worth -30 moves the v1 score from "
            f"{b_with_v1.company_score:.4f} to {b_part.company_score:.4f}, "
            f"{(b_part.company_score - b_with_v1.company_score):+.4f} points, "
            f"and nothing on the row said which of the two numbers it was. "
            f"v2 moves {(b_v2.company_score - b_with_v2.company_score):+.4f} "
            f"-- it rises too, because dropping a negative term raises a sum "
            f"and no honest rule can prevent that without imputing a pillar "
            f"that was not measured. What v2 does prevent is the SURVIVORS "
            f"being promoted, and what it guarantees is that the second "
            f"number is labelled.", 70):
        out.append("  " + line)
    return "\n".join(out)


def render_promotion_table() -> str:
    """How much each rule PROMOTES a surviving pillar. The whole thing, in one table.

    The last column is the point. Under v1 a company whose only computable
    pillar is growth is scored 100% on growth -- a 4.0x promotion of the
    published 0.25 weight -- and the row is called a Shaffer Score. Under v2
    growth's effective weight is 0.2941 in every signature it appears in, and
    the score shrinks instead of the weights growing.
    """
    lines = ["PROMOTION OF THE SURVIVING PILLARS",
             "",
             f"  {'signature':<12}{'v1 eff. G':>12}{'x declared':>13}"
             f"{'v2 eff. G':>12}{'x declared':>13}{'v1 coverage':>14}"]
    lines.append("  " + "-" * 76)
    v2_declared = V2_MAJOR_WEIGHTS[V2_BLOCK_GROWTH] / COMPANY_SCALE_V2
    for signature in ("VGPD", "VGP", "GPD", "GP", "GD", "G"):
        a = effective_weights(signature, V1_MAJOR_WEIGHTS, renormalise=True)
        v2_sig = signature.replace("P", "").replace("D", "") or "NONE"
        v2_sig = v2_sig if "G" in v2_sig else v2_sig
        b = effective_weights("EVGQ", V2_MAJOR_WEIGHTS, renormalise=False)
        lines.append(
            f"  {signature:<12}{a[PILLAR_GROWTH]:>12.4f}"
            f"{a[PILLAR_GROWTH] / V1_MAJOR_WEIGHTS[PILLAR_GROWTH]:>12.3f}x"
            f"{b[V2_BLOCK_GROWTH]:>12.4f}"
            f"{b[V2_BLOCK_GROWTH] / v2_declared:>12.3f}x"
            f"{weight_coverage(signature):>14.4f}")
    lines.append("  " + "-" * 76)
    for line in _wrap(
            "Every v1 row in this table is reported on the same scale, under "
            "the same name, as the VGPD row at the top. The v2 column is flat "
            "at 1.000 by construction: losing a pillar shrinks the score, it "
            "does not enlarge the survivors.", 74):
        lines.append("  " + line)
    return "\n".join(lines)


def render_pooling_demonstration() -> str:
    """What the default evaluation path does with the three demo companies."""
    rows = [score_v1(factors) for _, factors, _ in DEMO_COMPANIES]
    grouping = evaluation_groups(rows)
    out = [render_grouping(grouping), ""]
    try:
        pool(rows)
        out.append("  FAIL: pooling a mixed set was allowed")
    except SignaturePoolingRefused as exc:
        out.append("  pool(rows) ->")
        for line in _wrap(str(exc), 68):
            out.append("    " + line)
    out.append("")
    deliberate = pool(rows, acknowledge_different_models=True,
                      justification=("diagnostic only: measuring how far the "
                                     "renormalised partials move the pooled IC"))
    out.append(f"  pool(rows, acknowledge_different_models=True, "
               f"justification=...) -> {len(deliberate)} rows, with:")
    for line in _wrap(deliberate.warning, 68):
        out.append("    " + line)
    return "\n".join(out)


# ==========================================================================
# (8) STORAGE -- specified, NOT applied
# ==========================================================================

#: The MIGRATIONS fragment, in `pit_store`'s ADD-COLUMN convention.
#:
#: NO DEFAULTS, and that is the same decision `pit_label.label_policy_version`
#: made for the same reason. A constant default would backfill every existing
#: row for free -- there are none, pit_score holds 0 rows -- and would ALSO
#: stamp 'VGPD' or 'not partial' on a row written by a future replay whose
#: author forgot the column. That is the exact silent mislabelling this module
#: exists to stop. NULL means "written before the column existed"; the guard
#: trigger below turns NULL into a refusal from the moment it is installed.
PIT_SCORE_SIGNATURE_MIGRATIONS: list[tuple[str, str]] = [
    ("score_signature", "TEXT"),
    ("original_weight_coverage", "REAL"),
    ("partial_score_marker", "TEXT"),
]


def migration_patch() -> dict[str, list[tuple[str, str]]]:
    """The exact entry to add to `pit_store.MIGRATIONS`. Applies nothing.

    Paste as a new key beside "pit_listing", "pit_cost_assumption" and
    "pit_label"; `pit_store.apply_migrations` is already idempotent and already
    runs from `init_db`, so no new plumbing is needed anywhere.
    """
    return {"pit_score": [tuple(pair) for pair in PIT_SCORE_SIGNATURE_MIGRATIONS]}


#: The columns, plus the one index an evaluation path that separates by
#: signature will actually use. Free to apply: pit_score holds 0 rows, so each
#: ALTER is a header rewrite and the index is an empty b-tree.
PIT_SCORE_COLUMN_DDL = """
-- score_signature: which pillars the score was built from, canonical order
--   'VGPD' all four | 'GPD' valuation absent | ... | 'NONE' nothing scored.
-- original_weight_coverage: the fraction of the model's ORIGINAL declared
--   weight the present pillars carry. v1: VGPD 1.00, GPD 0.60. This is NOT
--   the existing `coverage` column, which is free-form and about SOURCES.
-- partial_score_marker: FULL_SHAFFER_SCORE |
--   V1_RENORMALISED_PARTIAL_NOT_DIRECTLY_COMPARABLE |
--   PARTIAL_SHAFFER_SCORE_NOT_DIRECTLY_COMPARABLE | NOT_SCORED_NO_PILLAR_AVAILABLE
ALTER TABLE pit_score ADD COLUMN score_signature TEXT;
ALTER TABLE pit_score ADD COLUMN original_weight_coverage REAL;
ALTER TABLE pit_score ADD COLUMN partial_score_marker TEXT;

-- The default evaluation path reads one signature at a time. Added now, on an
-- empty table, for the price of an empty b-tree.
CREATE INDEX IF NOT EXISTS idx_pit_score_signature
    ON pit_score (as_of_date, model_version, score_signature);
"""

#: The guard. APPLY ONLY TOGETHER WITH A WRITER THAT STAMPS THE COLUMNS.
#:
#: `pit_store.save_score` does NOT set score_signature today, so installing
#: this trigger first would refuse every insert the sanctioned writer makes.
#: That is stated here rather than discovered during a replay. The trigger is
#: the pit_label pattern exactly: a second policy is REFUSED rather than
#: silently written, which is the failure versioning is for.
PIT_SCORE_GUARD_DDL = """
CREATE TRIGGER IF NOT EXISTS trg_pit_score_signature_required
BEFORE INSERT ON pit_score
WHEN NEW.score_signature IS NULL OR NEW.score_signature = ''
BEGIN
    SELECT RAISE(ABORT, 'pit_score: score_signature is required -- a score whose pillars are unknown cannot be told apart from a full four-pillar score, and pooling the two averages two different models');
END;

CREATE TRIGGER IF NOT EXISTS trg_pit_score_marker_required
BEFORE INSERT ON pit_score
WHEN NEW.partial_score_marker IS NULL OR NEW.partial_score_marker = ''
BEGIN
    SELECT RAISE(ABORT, 'pit_score: partial_score_marker is required -- see pit_score_signature.MARKERS');
END;
"""


def migration_sql(include_guard: bool = False) -> tuple[str, ...]:
    """The statements, one per element, comments stripped. Executes nothing.

    The DDL above is written to be READ -- the comments are the reason the
    columns exist -- so this strips them rather than leaving a caller to paste
    a prose block into `execute`.
    """
    statements = []
    for chunk in PIT_SCORE_COLUMN_DDL.split(";"):
        body = _without_comments(chunk)
        if body:
            statements.append(body)
    if include_guard:
        statements.append(PIT_SCORE_GUARD_DDL.strip())
    return tuple(statements)


def _without_comments(chunk: str) -> str:
    """A DDL chunk with its leading `--` commentary and blank lines removed."""
    kept = [line for line in chunk.splitlines()
            if line.strip() and not line.strip().startswith("--")]
    return "\n".join(kept).strip()


def columns_present(conn: Any) -> dict[str, bool]:
    """Which of the three columns pit_score already has. READ ONLY.

    One PRAGMA and out. Does not begin a transaction, does not hold a snapshot,
    and is safe to call against a store another process is writing -- but it
    still needs a connection, so the CALLER opens it (`mode=ro`, a
    `busy_timeout`) and the caller closes it.
    """
    rows = conn.execute("PRAGMA table_info(pit_score)").fetchall()
    existing = {r[1] if not isinstance(r, Mapping) else r["name"] for r in rows}
    return {name: name in existing for name, _ in PIT_SCORE_SIGNATURE_MIGRATIONS}


def ensure_columns(conn: Any, *, include_guard: bool = False) -> list[str]:
    """Add the columns. FOR ANOTHER PROCESS TO CALL, LATER.

    NOTHING IN THIS MODULE CALLS THIS and nothing here opens a connection to
    pass it. The store is being written to by another agent; a reader that
    lingers blocks the WAL checkpointer, which is how this project once put a
    9.3 GB WAL on a volume with ~11 GiB free.

    Idempotent: each column is added only if `PRAGMA table_info` says it is
    absent, exactly as `pit_store.apply_migrations` does. Returns what it added.

    `include_guard` also installs the BEFORE INSERT triggers. Do NOT pass it
    until `pit_store.save_score` stamps the columns -- see PIT_SCORE_GUARD_DDL.
    """
    have = columns_present(conn)
    added: list[str] = []
    for name, sql_type in PIT_SCORE_SIGNATURE_MIGRATIONS:
        if not have.get(name):
            conn.execute(f"ALTER TABLE pit_score ADD COLUMN {name} {sql_type}")
            added.append(f"pit_score.{name}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pit_score_signature "
                 "ON pit_score (as_of_date, model_version, score_signature)")
    if include_guard:
        conn.executescript(PIT_SCORE_GUARD_DDL)
        added.append("pit_score guard triggers")
    conn.commit()
    return added


#: Measured state of the store at the time this module was written, so the
#: "free now, expensive later" claim can be checked rather than believed.
STORE_STATE = {
    "measured_on": "2026-09-21",
    "pit_score_rows": 0,
    "pit_feature_rows": 0,
    "db_bytes": 8_965_951_488,
    "wal_bytes_at_probe": 0,
    "volume_free_bytes": 11_168_751_616,
    "probe": ("sqlite3 mode=ro, busy_timeout 8000, two COUNT(*) and one "
              "PRAGMA table_info, 0.003 s, connection closed immediately"),
}


def storage_note() -> dict[str, Any]:
    """Why these three columns cost nothing today and a great deal later."""
    return {
        "recommendation": ("apply PIT_SCORE_COLUMN_DDL NOW, while pit_score "
                           "holds 0 rows, and add PIT_SCORE_SIGNATURE_MIGRATIONS "
                           "to pit_store.MIGRATIONS in the same change"),
        "why_now": [
            "pit_score has 0 rows (measured, see STORE_STATE), so each ALTER "
            "TABLE ADD COLUMN rewrites a table header and touches no data "
            "page, and the index is an empty b-tree: milliseconds, no WAL "
            "growth, no risk to the 8.13 GiB store on a 96%-full volume",
            "pit_score carries an immutability trigger (trg_pit_score_no_update) "
            "so a score row can never be UPDATED. A backfill of a missing "
            "signature is therefore not a backfill at all -- it is a whole "
            "new replay under a new replay_run_id",
            "the replay that fills pit_score has not been written yet, so "
            "there is exactly one writer to teach, and it is unwritten",
        ],
        "cost_later": {
            "rows_per_full_replay": 1_240_000,
            "why_expensive": ("165 month-end dates x ~7,500 entities. Once "
                              "those rows exist without a signature, the "
                              "immutability trigger means they cannot be "
                              "stamped -- the only repair is a full re-replay, "
                              "and until it finishes every evaluation over the "
                              "old rows is silently pooling two models"),
        },
        "wal_exposure": ("three ALTER TABLE statements and one CREATE INDEX on "
                         "an empty table: a handful of pages. The guard "
                         "triggers are schema-only. Nothing here writes a row"),
        "not_applied": ("ensure_columns() is written for another process to run "
                        "later. This module executes no SQL and opens no "
                        "connection. A writer may be active on the store: the "
                        "-shm file was modified minutes before the probe, so "
                        "the DDL is SPECIFIED and LEFT FOR LATER"),
        "guard_precondition": ("PIT_SCORE_GUARD_DDL must not be installed until "
                               "pit_store.save_score passes score_signature and "
                               "partial_score_marker -- it does not today, and "
                               "the trigger would refuse every insert it makes"),
    }


# ==========================================================================
# (9) EVIDENCE AND SELF-CHECK
# ==========================================================================

KNOWN_LIMITATIONS: dict[str, Any] = {
    "company_scoring.calculate_company_raw_score": {
        "status": SHAFFER_V1_KNOWN_LIMITATION,
        "finding": ("renormalises the major weights over whatever factors are "
                    "available, so a score missing its 40%-weighted valuation "
                    "pillar is reported on the same scale, under the same "
                    "name, as a full four-pillar score. The drop is named in "
                    "`notes`, which is prose; the backtest reads the number"),
        "measured": ("company_raw_score availability 99.32 / 99.75 / 99.83% "
                     "against an ev_ebitda cohort that clears 3 members in "
                     "34.2% of 39,038 peer sets and 12 in 7.0%"),
        "consequence": ("most scored rows would be 0.25G + 0.20P + 0.15D "
                        "renormalised to 0.4167 / 0.3333 / 0.25 -- growth's "
                        "effective weight rises by a factor of 1.667 and "
                        "nothing on the row says so"),
        "fix_location": ("NOT here. The core is frozen. v1 keeps its "
                         "arithmetic; the signature, the coverage and the "
                         "marker are ADDED, and the v2 rule lives in the "
                         "candidate lineage (" + CANDIDATE_MODEL_VERSION + ")"),
    },
}

MEASURED: dict[str, Any] = {
    "source": ("pit_derive.coverage reproduced 2026-09-21 on the declared "
               "marginals; cohort figures from pit_cohort_measure over 39,038 "
               "peer sets, 3,069,260 memberships, 165 month-end dates"),
    "company_raw_score_availability": {"2015-06-30": 0.9932,
                                       "2019-06-28": 0.9975,
                                       "2024-06-28": 0.9983},
    "ev_ebitda_per_peer_availability": {"2015-06-30": 0.0122,
                                        "2019-06-28": 0.0290,
                                        "2024-06-28": 0.0318},
    "ev_ebitda_cohort": {"median_members": 1, "pct_ge_3": 34.2, "pct_ge_12": 7.0,
                         "sic4_pct_ge_3": 22.9, "sic4_pct_ge_12": 2.4},
    "ebitda_cohort_after_owner_correction": {"median_members": 13,
                                             "pct_ge_3": 95.2, "pct_ge_12": 56.0},
    "sample_scope": ("every figure that touches a price is "
                     "SURVIVOR_ONLY_DIAGNOSTIC: 2,574 listings, all alive in "
                     "2026, zero series ending before 2020. The pillar scores "
                     "in DEMO_COMPANIES are ILLUSTRATIVE and are not measured "
                     "quantities at all"),
    "renormalised_weight_vector_without_valuation": {"growth": 0.25 / 0.60,
                                                     "profitability": 0.20 / 0.60,
                                                     "debt": 0.15 / 0.60},
}


def validate() -> list[str]:
    """Internal consistency. Returns problems; the script exits non-zero on any."""
    problems: list[str] = []

    if abs(sum(V1_MAJOR_WEIGHTS.values()) - 1.0) > 1e-12:
        problems.append("v1 major weights must sum to exactly 1.00")
    if abs(sum(V2_MAJOR_WEIGHTS.values()) - COMPANY_SCALE_V2) > 1e-12:
        problems.append("v2 major weights must sum to exactly COMPANY_SCALE_V2 "
                        "-- that identity is what makes the v2 company score "
                        "the plain unrenormalised 0.35V+0.25G+0.15P+0.10D")
    if set(V1_MAJOR_WEIGHTS) != set(PILLARS):
        problems.append("the v1 weight table must name exactly v1's four pillars")
    if set(V2_MAJOR_WEIGHTS) != set(V2_BLOCKS):
        problems.append("the v2 weight table must name exactly v2's four BLOCKS, "
                        "which are not v1's pillars")
    if set(V1_MAJOR_WEIGHTS) == set(V2_MAJOR_WEIGHTS):
        problems.append("v1's pillars and v2's blocks came out identical; the "
                        "2026-09-21 correction exists because they are not")
    if V2_MAJOR_WEIGHTS.get(V2_BLOCK_EBITDA) != 0.35:
        problems.append("v2 is EBITDA-CENTRED: the EBITDA block carries 0.35. "
                        "Valuation carrying 0.35 is the exact defect corrected "
                        "on 2026-09-21")
    if V2_MAJOR_WEIGHTS.get(V2_BLOCK_VALUATION) != 0.25:
        problems.append("v2 valuation carries 0.25, secondary to EBITDA")
    for letter, block, weight in V2_WEIGHT_SEMANTICS:
        if V2_BLOCK_LETTER.get(block) != letter or V2_MAJOR_WEIGHTS.get(block) != weight:
            problems.append("the semantic triple broke for %r: the label is "
                            "part of the model" % (block,))
    if len({l for l, _b, _w in V2_WEIGHT_SEMANTICS}) != len(V2_BLOCKS):
        problems.append("two v2 blocks share a letter; signatures would collide")

    if abs(weight_coverage(SIGNATURE_FULL) - 1.0) > 1e-12:
        problems.append("VGPD must carry original_weight_coverage 1.00")
    if abs(weight_coverage("GPD") - 0.60) > 1e-12:
        problems.append("GPD must carry original_weight_coverage 0.60")

    #: The property the whole design rests on: renormalising over the present
    #: weights and dividing by the full total are THE SAME OPERATION when
    #: nothing is missing, for any table.
    for name, table, keys, full in (
            ("v1", V1_MAJOR_WEIGHTS, PILLARS, SIGNATURE_FULL),
            ("v2", V2_MAJOR_WEIGHTS, V2_BLOCKS, V2_SIGNATURE_FULL)):
        a = effective_weights(full, table, renormalise=True)
        b = effective_weights(full, table, renormalise=False)
        if any(abs(a[k] - b[k]) > 1e-12 for k in keys):
            problems.append(f"{name}: the two paths must agree at {full}")

    for marker in MARKERS:
        if marker != FULL_SHAFFER_SCORE and "NOT_DIRECTLY_COMPARABLE" not in marker \
                and marker != NOT_SCORED:
            problems.append(f"{marker}: a non-full marker must say so in its value")
    if COMPARABLE_MARKERS != frozenset({FULL_SHAFFER_SCORE}):
        problems.append("only a full four-pillar score may be comparable by default")

    #: v2 must never promote a survivor. NOTE THE TWO FIXTURES: v2 is fed v2
    #: BLOCK names and v1 is fed v1 PILLAR names. Before 2026-09-21 this check
    #: fed v1's pillars to `score_v2` -- the self-test carried the same
    #: assumption as the defect it was meant to catch, which is why it passed.
    v2_full_in = {b: 10.0 for b in V2_BLOCKS}
    v2_partial_in = {b: 10.0 for b in V2_BLOCKS if b != V2_BLOCK_VALUATION}
    v2_full, v2_part = score_v2(v2_full_in), score_v2(v2_partial_in)
    for block in v2_part.pillars_present:
        if abs(v2_full.effective_weights[block]
               - v2_part.effective_weights[block]) > 1e-12:
            problems.append(f"v2 moved {block}'s effective weight when a "
                            "block was lost -- that is renormalisation")
    if v2_full.signature != V2_SIGNATURE_FULL:
        problems.append("a complete v2 score must sign as %s, not %r"
                        % (V2_SIGNATURE_FULL, v2_full.signature))
    expected = sum(V2_MAJOR_WEIGHTS[b] * 10.0 for b in V2_BLOCKS)
    if abs((v2_full.company_score or 0.0) - expected) > 1e-9:
        problems.append("a full v2 company score must be the plain "
                        "0.35E + 0.25V + 0.15G + 0.10Q")

    partial = {p: 10.0 for p in PILLARS if p != PILLAR_VALUATION}
    v1_part = score_v1(partial)
    if v1_part.effective_weights[PILLAR_GROWTH] <= V1_MAJOR_WEIGHTS[PILLAR_GROWTH]:
        problems.append("v1 must still renormalise: the baseline is preserved, "
                        "not corrected")
    if v1_part.partial_score_marker != V1_RENORMALISED_PARTIAL:
        problems.append("a v1 partial must be marked V1_RENORMALISED_PARTIAL")
    if v2_part.partial_score_marker != PARTIAL_SHAFFER_SCORE:
        problems.append("a v2 partial must be marked PARTIAL_SHAFFER_SCORE")

    #: THE PEER-SET REASON REACHES THE ROW. A valuation pillar removed by the
    #: two-test gate must produce a PARTIAL score that names the outcome -- not
    #: a renormalised full one, and not a partial whose reason has to be
    #: reconstructed from prose.
    gated = score_v2(v2_partial_in,
                     reasons={V2_BLOCK_VALUATION: VALUATION_PEER_SET_INSUFFICIENT})
    if gated.partial_score_marker != PARTIAL_SHAFFER_SCORE:
        problems.append(f"{VALUATION_PEER_SET_INSUFFICIENT} must produce a "
                        "PARTIAL score, never a renormalised full one")
    if not gated.valuation_peer_set_insufficient:
        problems.append("the peer-set reason must be readable off the row")
    if gated.comparable_with_full:
        problems.append("a partial score gated out of valuation is NOT "
                        "comparable with a full one (decision 7)")
    for pillar in gated.pillars_present:
        if abs(v2_full.effective_weights[pillar]
               - gated.effective_weights[pillar]) > 1e-12:
            problems.append(f"the gated partial promoted {pillar}")
    if VALUATION_PEER_SET_INSUFFICIENT not in " ".join(gated.notes):
        problems.append("the notes must name the reason the pillar is absent")
    #: A reason for a PRESENT pillar is not carried: a number and an excuse for
    #: its absence cannot both be true.
    noise = score_v2(v2_full_in,
                     reasons={V2_BLOCK_VALUATION: VALUATION_PEER_SET_INSUFFICIENT})
    if noise.unavailable_reasons or noise.partial_score_marker != FULL_SHAFFER_SCORE:
        problems.append("a reason supplied for a PRESENT pillar must be dropped")

    for name, _ in PIT_SCORE_SIGNATURE_MIGRATIONS:
        if name not in PIT_SCORE_COLUMN_DDL:
            problems.append(f"{name} is in MIGRATIONS but not in the DDL")
    if "DEFAULT" in PIT_SCORE_COLUMN_DDL.upper().replace("DEFAULT EVALUATION", ""):
        problems.append("the added columns must carry no DEFAULT")
    return problems


def render() -> str:
    lines = [f"SCORE SIGNATURES -- {SIGNATURE_VERSION}", ""]
    lines.append(f"  v1 baseline  {V1_MODEL_VERSION}")
    lines.append(f"    weights    " + "  ".join(
        f"{PILLAR_LETTER[p]} {V1_MAJOR_WEIGHTS[p]:.2f}" for p in PILLARS)
        + f"   (sum {sum(V1_MAJOR_WEIGHTS.values()):.2f})")
    lines.append(f"    scale      {V1_COMPANY_SCALE:.2f}"
                 f"   renormalises over the PRESENT weights (PRESERVED)")
    lines.append(f"  candidate    {CANDIDATE_MODEL_VERSION}")
    lines.append(f"    weights    " + "  ".join(
        f"{V2_BLOCK_LETTER[b]} {V2_MAJOR_WEIGHTS[b]:.2f}" for b in V2_BLOCKS)
        + f"   (sum {sum(V2_MAJOR_WEIGHTS.values()):.2f})")
    lines.append(f"    scale      {COMPANY_SCALE_V2:.2f}   COMPANY_SCALE_V2, equal "
                 f"to the weight total: no cross-pillar renormalisation")
    lines.append("")
    lines.append("  signature   pillars                     v1 coverage  v2 coverage")
    lines.append("  " + "-" * 68)
    for mask in range(15, -1, -1):
        present = [p for i, p in enumerate(PILLARS) if mask & (1 << (3 - i))]
        if not present:
            continue
        sig = "".join(PILLAR_LETTER[p] for p in present)
        lines.append(f"  {sig:<11} {', '.join(PILLAR_LETTER[p] for p in present):<27}"
                     f"{weight_coverage(sig, V1_MAJOR_WEIGHTS):>11.4f}"
                     f"{weight_coverage(sig, V1_MAJOR_WEIGHTS):>13.4f}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    print(render())
    print()
    print(render_demonstration())
    print()
    print(render_promotion_table())
    print()
    print("=" * 72)
    print("THE DEFAULT EVALUATION PATH")
    print("=" * 72)
    print()
    print(render_pooling_demonstration())
    print()
    print("=" * 72)
    print("A PARTIAL SHAFFER SCORE, AS THE OWNER DESCRIBED IT")
    print("=" * 72)
    print()
    print(render_partial_score(score_v2(DEMO_COMPANIES_V2[1][1])))
    print()
    print("=" * 72)
    print("STORAGE -- SPECIFIED, NOT APPLIED")
    print("=" * 72)
    print()
    print(json.dumps(storage_note(), indent=1, sort_keys=True))
    print()
    print("MIGRATIONS fragment for pit_store.MIGRATIONS:")
    print("  " + json.dumps({k: [list(p) for p in v]
                             for k, v in migration_patch().items()},
                            sort_keys=True))
    print()
    problems = validate()
    for problem in problems:
        print("  FAIL", problem)
    print(f"{len(problems)} problems")
    print("PASS" if not problems else "FAIL")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
