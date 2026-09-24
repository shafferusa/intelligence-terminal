"""pit_frozen_spec -- the Shaffer Equity v2 SPECIFICATION freeze (v1-v4 2026-09-21/22; v5 2026-09-22; v6 2026-09-23).

    equity_shaffer_v2_pit_SURVIVOR_ONLY_DIAGNOSTIC

==============================================================================
WHAT THIS FREEZE IS, IN THE OWNER'S WORDS
==============================================================================

    "That does not mean the model is historically validated. It means the
     economic definitions, PIT rules, factor eligibility, price/share basis,
     missingness behavior, provenance handling, peer coherence, and coverage
     definitions are sufficiently specified to calculate the first diagnostic
     replay."

So this module freezes a SPECIFICATION, not a result. Nothing here asserts that
any Shaffer Score predicts anything. `pit_feature`, `pit_score` and
`pit_replay_run` are all still at 0 rows at the moment of freezing, and the
proper validation baseline remains blocked on the dead-company price problem.

The survivorship limitation is embedded in the MODEL VERSION STRING itself, so
it cannot be separated from any row that carries it, cannot be lost in a join,
and cannot be dropped from a chart legend by someone who never read this file.

==============================================================================
A FREEZE THAT CANNOT DETECT ITS OWN VIOLATION IS NOT A FREEZE
==============================================================================

`COMPONENTS` names every policy version, calibrated constant AND every
canonical BODY the specification is made of, read live from the module that
owns it. v3 digested the block map, within-block weights, block floor,
debt-rung policy and anchor policy by body; v4 added the factor specs, the
corrected P/E eligibility, the normalization registry, the direction tables
and the valuation subfactor bodies; v5 adds the EXECUTABLE DEFINITIONS
(FORMULAS_V1, with golden witnesses), the D3 policies, and the constants the
v4 governance audit found outside the digest (the benchmark band and floor,
the v2 block order, the transform parameters, the EPS pair hierarchy);
v6 adds the owner's V/Q rulings of 2026-09-23 as BODIES -- factor_spec_v5,
FORMULAS_V2 with its witnesses, the successor base and coverage policies,
the five Q/V domain policies, the strict debt-rung gate, the peer and
within-block floors, the EV transform, corporate-action gate v2's semantics,
the price-basis translation and the valuation price policy. v1-v3 hashed
version strings for the spec bodies; v3 was superseded when that was shown
to let economics drift under an intact digest; v4 was superseded when it
was shown to have frozen two undecided semantic choices; v5 was superseded
because the engine under it could compute six of thirteen keys. `digest()`
hashes them. `verify()` recomputes and reports, per component, anything that
moved. Change a ladder version, an anchor, a threshold or a presence rule, and
the freeze fails loudly rather than quietly meaning something else.

That is the whole mechanism. A frozen specification whose parts can drift is a
frozen NAME over moving content, which is worse than no freeze at all because
it carries authority it has not earned.

==============================================================================
HOW THE FIVE GATES CLOSED
==============================================================================

Note gate 5. It did not produce a clean bill of health; it produced a
RESTRICTIVE DESIGN DECISION. Closing a gate means the question was MEASURED and
ANSWERED, not that the answer was convenient.

Stdlib only. Reads no database.
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib
import json
from typing import Any, Optional

__all__ = [
    "FROZEN_MODEL_VERSION", "FROZEN_AT", "SPEC_FREEZE_VERSION",
    "SAMPLE_SCOPE", "COMPONENTS", "manifest", "digest", "FROZEN_DIGEST",
    "verify", "WHAT_FREEZING_MEANS", "WHAT_IT_DOES_NOT_MEAN", "GATES",
    "STILL_BLOCKED", "THINGS_THE_EMPTY_TABLES_CAUGHT", "report", "validate",
    "SCORE_ASSEMBLY_GAP", "can_execute_replay", "FREEZE_V1", "FREEZE_V2",
    "FREEZE_V3", "FREEZE_V4", "FREEZE_V5", "STATUS_SUPERSEDED",
    "REASON_ENGINE_LACKED_VQ_PATHS",
    "REASON_DIGEST_COVERED_VERSION_STRINGS", "REASON_FROZE_UNRESOLVED_SEMANTICS",
    "INCIDENT_CAUSE_REJECTED", "ENGINE_MUST_DERIVE_FROM",
    "FREEZE_V2_STATUS", "STATUS_EXECUTABLE",
    "REASON_MISSING_WITHIN_BLOCK_WEIGHTS",
    "STATUS_FROZEN_NONEXECUTABLE", "STATUS_FROZEN_PENDING",
    "REASON_PILLAR_WEIGHT_SEMANTIC_CONFLICT", "WEIGHT_SEMANTIC_INVARIANT",
    "assert_weight_semantics",
]


# ==========================================================================
# THE FREEZE LINEAGE
#
# A frozen object is NEVER silently repaired. The first freeze stays exactly
# as it was recorded, with the defect that made it non-executable named in its
# own status, and the correction arrives as a SUCCESSOR beside it. That is the
# same rule the model versions follow, applied to the freeze records
# themselves -- otherwise "frozen" would mean "frozen until inconvenient".
# ==========================================================================

STATUS_FROZEN_NONEXECUTABLE = "FROZEN_BUT_NONEXECUTABLE"
STATUS_FROZEN_PENDING = "FROZEN_PENDING_FACTOR_BLOCK_MAP"
STATUS_EXECUTABLE = "FROZEN_AND_EXECUTABLE"

REASON_MISSING_WITHIN_BLOCK_WEIGHTS = "MISSING_WITHIN_BLOCK_WEIGHTS"

REASON_PILLAR_WEIGHT_SEMANTIC_CONFLICT = "PILLAR_WEIGHT_SEMANTIC_CONFLICT"

#: FREEZE 1 of 2026-09-21. PERMANENT RECORD. Do not edit; do not re-digest.
#:
#: It was a real freeze over 27 real components and it is NOT EXECUTABLE,
#: because the weight table it covered said valuation 0.35 while the approved
#: design says EBITDA 0.35. Both carry the vector (0.35, 0.25, 0.15, 0.10);
#: only the LABELS differ, and the labels are the model. A backtest run from
#: this specification would have completed cleanly and answered a different
#: question.
FREEZE_V1: dict[str, Any] = {
    "freeze_version": "spec_freeze_v1",
    "model_version": "equity_shaffer_v2_pit_SURVIVOR_ONLY_DIAGNOSTIC",
    "frozen_at": "2026-09-21",
    "digest": "7a844dca245cfb37ea6722d8e34a9d9407f82e2210ec17f62605aa8ab40f7843",
    "n_components": 27,
    "status": STATUS_FROZEN_NONEXECUTABLE,
    "status_reason": REASON_PILLAR_WEIGHT_SEMANTIC_CONFLICT,
    "what_was_wrong": (
        "pit_score_signature.V2_MAJOR_WEIGHTS read {valuation .35, growth .25, "
        "profitability .15, debt .10} -- v1's four PILLARS carrying v2's "
        "weight VECTOR. The approved v2 design is {EBITDA .35, valuation .25, "
        "real growth .15, financial quality .10}. Same numbers, different "
        "model."),
    "rows_it_ever_produced": 0,
    "superseded_by": "spec_freeze_v2",
    "never_repair_in_place": (
        "This record is not corrected. Correcting a frozen digest in place "
        "would destroy the only evidence that the defect existed, and would "
        "break the rule the freeze exists to enforce."),
}

#: FREEZE 2 of 2026-09-21. THE SUCCESSOR. Same model version string -- the
#: SURVIVOR_ONLY_DIAGNOSTIC lineage is unchanged; what changed is that the
#: specification now says what it always meant.
#:
#: Still not executable, and for a DIFFERENT and honestly smaller reason: the
#: factor-to-block map does not exist yet and the EBITDA-growth double count
#: is unresolved. Those are additions, not contradictions.
#: FREEZE 2 of 2026-09-21. PERMANENT RECORD. Do not edit; do not re-digest.
#:
#: It corrected the weight SEMANTICS -- EBITDA .35 over a v2 block set with the
#: EVGQ alphabet -- and added the factor-to-block map and the coverage floor.
#: It is STILL NOT EXECUTABLE, for a narrower reason than v1's: the blocks were
#: specified and their factors assigned, but nothing said how five EBITDA
#: factors combine into one E score or four quality factors into one Q.
#:
#: Sealed rather than extended, on the rule that a frozen object is never
#: edited until it becomes convenient: FIXED is not EDITABLE-UNTIL-EXECUTABLE.
FREEZE_V2: dict[str, Any] = {
    "freeze_version": "spec_freeze_v2",
    "model_version": "equity_shaffer_v2_pit_SURVIVOR_ONLY_DIAGNOSTIC",
    "frozen_at": "2026-09-21",
    "digest": "5219bdd5902a220bbce6fc4b3bb68aeac17ee34928d340103be24ef85fd31462",
    "n_components": 32,
    "status": STATUS_FROZEN_NONEXECUTABLE,
    "status_reason": REASON_MISSING_WITHIN_BLOCK_WEIGHTS,
    "what_it_fixed": (
        "the pillar-weight semantic conflict, the factor-to-block map, the "
        "EBITDA-growth double count and MIN_BLOCK_WEIGHT"),
    "what_was_still_missing": (
        "within-block factor weights for E (five factors) and Q (four). V was "
        "specified and G carries one factor."),
    "rows_it_ever_produced": 0,
    "superseded_by": "spec_freeze_v3",
    "never_repair_in_place": (
        "Adding the weights to this record would have made 'frozen' mean "
        "'frozen until it blocked us'."),
}

FREEZE_V2_STATUS = FREEZE_V2["status"]

STATUS_SUPERSEDED = "FROZEN_SUPERSEDED"
REASON_DIGEST_COVERED_VERSION_STRINGS = "DIGEST_COVERED_VERSION_STRINGS_NOT_BODIES"

#: FREEZE 3 of 2026-09-21. PERMANENT RECORD. Do not edit; do not re-digest.
#:
#: It was executable, and it is superseded for a GOVERNANCE defect found on
#: 2026-09-22 rather than a modelling one: its 38 components covered
#: pit_factor_spec and pit_normalization by VERSION STRING only, never by
#: body, so verify() could return intact=True while the factor economics
#: drifted underneath -- exactly the blind spot that let the v1 weight-
#: semantic defect through, one level down. Two such drifts were in fact
#: present under it:
#:   - ebitda_benchmark's spec body implemented the DOLLAR reading the design
#:     record had already rejected as a size factor;
#:   - PRICED_EPS declared point-in-time EPS unavailable while pit_eps_obs
#:     held 888,486 rows, refusing the whole P/E backbone without a DB read.
#: Both are corrected in factor_spec_v3 and digested BY BODY in v4.
FREEZE_V3: dict[str, Any] = {
    "freeze_version": "spec_freeze_v3",
    "model_version": "equity_shaffer_v2_pit_SURVIVOR_ONLY_DIAGNOSTIC",
    "frozen_at": "2026-09-21",
    "digest": "2f9bba3136ac45f4bdc37c5197e646acf645410f1c36ded698fbdd368f86e764",
    "n_components": 38,
    "status": STATUS_SUPERSEDED,
    "status_reason": REASON_DIGEST_COVERED_VERSION_STRINGS,
    "content_defects_found_under_it": (
        "ebitda_benchmark: DOLLAR-mean benchmark in the spec body (a size "
        "factor; rejected by docs/SHAFFER-EQUITY-V2-CANDIDATE.md section S1)",
        "pe_ratio: PRICED_EPS.unavailable_primitives=('earnings_per_share_pit',) "
        "-- false since the pit_eps ingest",
    ),
    "rows_it_ever_produced_in_main_store": 0,
    "rows_in_the_isolated_pilot_db_stamped_with_it": {
        "db": "shafferfineval_pilot.db (disposable, isolated)",
        "pit_feature": 198042, "pit_pillar_score": 60936, "pit_score": 5582,
        "dates": ("2014-06-30", "2019-06-28"),
        "engine_state": "PARTIAL_ENGINE_LOWER_BOUND -- 5 of 13 factors "
                        "computable, every score signature EG",
        "may_promote_anything": False,
    },
    "superseded_by": "spec_freeze_v4",
    "never_repair_in_place": (
        "Widening this digest to cover bodies would have changed the digest, "
        "and a changed digest under the same freeze version is exactly what "
        "the freeze exists to make impossible."),
}


REASON_FROZE_UNRESOLVED_SEMANTICS = "FROZE_UNRESOLVED_SEMANTIC_CHOICES_D3"
INCIDENT_CAUSE_REJECTED = "REJECTED_BY_MEASUREMENT"

#: FREEZE 4 of 2026-09-22. PERMANENT RECORD. Do not edit; do not re-digest.
#:
#: Executable, and superseded because it FROZE two unresolved semantic choices
#: (D3: the growth/acceleration denominator and the coverage numerator) and
#: digested no executable factor definition, so an engine with altered
#: arithmetic passed its gate (v4 governance audit, hole B) while the bar for
#: the model's largest factor weight sat outside the digest (hole A). It also
#: carried an incident cause later REJECTED_BY_MEASUREMENT. v5 arrives beside
#: it; nothing here is repaired.
FREEZE_V4: dict[str, Any] = {
    "freeze_version": "spec_freeze_v4",
    "model_version": "equity_shaffer_v2_pit_SURVIVOR_ONLY_DIAGNOSTIC",
    "frozen_at": "2026-09-22",
    "digest": "912268b3ec11bfc641fcac78078a0fb288cc51ce5432f8c27f931a532d1faae9",
    "n_components": 47,
    "status": STATUS_SUPERSEDED,
    "status_reason": REASON_FROZE_UNRESOLVED_SEMANTICS,
    "what_it_fixed": (
        "digested the spec BODIES (SPECS_V3, the normalization registry, the "
        "direction tables, the valuation subfactors); ebitda_benchmark MARGIN "
        "reading; PRICED_EPS_V2; FACTOR_DIRECTION_V1"),
    "content_defects_found_under_it": (
        "ebitda_growth / ebitda_acceleration: denominator UNDECIDED (A rate vs B "
        "assets); the engine computed B",
        "interest_coverage: numerator UNDECIDED; the engine computed nothing",
        "governance: band/min_cohort literals, V2_BLOCKS, the transform "
        "parameters, PAIR_PATHS and every executable formula outside the digest; "
        "no callable scan; manifest/digest identity unasserted",
    ),
    "incident_narrative_it_carried": {
        "claim": ("whole cross-section in memory before the first insert was the "
                  "root cause of the 2026-09-21 pagefile incident"),
        "status": INCIDENT_CAUSE_REJECTED,
        "ruling": ("owner, 2026-09-22: the machine was operating near its commit "
                   "ceiling because of resident services; the replay was at most "
                   "the marginal trigger"),
        "evidence": ("docs/POST-REBOOT-REPORT-2026-09-22.md sections 1, 5 and 5a: "
                     "the engine's private commit measures ~245 MiB at 4,000 "
                     "targets with the full 7,102-entity index resident, and "
                     "~4.5 GiB of commit sat in three autostarted Python services "
                     "with no replay running"),
    },
    "superseded_definitions_it_digested": {
        "note": ("the v4 registry and feature-note texts, VERBATIM, because the "
                 "registry body was corrected in place for v5 and would otherwise "
                 "survive only in git history"),
        "registry_EBITDAGrowth_notes": "input: (E_t - E_{t-4q}) / Assets_{t-4q}",
        "registry_EBITDAAcceleration_notes": (
            "input: (E_t - 2E_{t-4q} + E_{t-8q}) / Assets_{t-8q}, one common base "
            "-- two different bases give any asset-growing company a systematic "
            "negative acceleration."),
        "feature_note_ebitda_growth": "(E_t - E_t-1y) / Assets_t-1y, one common base",
        "feature_note_ebitda_acceleration": "(E_t - 2E_t-1y + E_t-2y) / Assets_t-2y",
        "feature_note_quality_interest_coverage": "EBITDA / interest expense",
        "engine_acceleration": "(E_t - 2E_{t-4q} + E_{t-8q}) / Assets_{t-8q}",
    },
    "rows_it_ever_produced_in_main_store": 0,
    "rows_in_isolated_scratch_dbs_stamped_with_it": {
        "db": "pit_replay_rss scratch and oracle DBs (disposable, deleted or kept "
              "outside the repository)",
        "per_arm_at_2019_06_28": {
            "50_targets": {"pit_feature": 650, "pit_pillar_score": 200, "pit_score": 25},
            "1000_targets": {"pit_feature": 13000, "pit_pillar_score": 4000, "pit_score": 461},
            "4000_targets": {"pit_feature": 52000, "pit_pillar_score": 16000, "pit_score": 1786},
        },
        "sources": ("rss_report_1k.json", "rss_report_4k_stream.json",
                    "rss_report_4k_legacy.json", "oracle_head_vs_worktree.json"),
        "engine_state": "PARTIAL_ENGINE_LOWER_BOUND -- 5 of 13 factors computable, "
                        "growth under alternative B",
        "may_promote_anything": False,
    },
    "may_execute_replay": False,
    "superseded_by": "spec_freeze_v5",
    "never_repair_in_place": (
        "Its digest stays 912268b3...; the D3 decisions and the audit closures "
        "arrive as spec_freeze_v5 beside it, and no row may be stamped "
        "spec_freeze_v4 again."),
}


REASON_ENGINE_LACKED_VQ_PATHS = "ENGINE_COMPUTED_SIX_OF_THIRTEEN_KEYS"

#: FREEZE 5 of 2026-09-22. PERMANENT RECORD. Do not edit; do not re-digest.
#:
#: Executable and CONSISTENT -- it decided D3, digested the executable
#: definitions and closed the v4 governance holes -- and superseded on
#: 2026-09-23 for a narrower reason than any before it: the engine under it
#: computed six of thirteen keys (no MARGIN benchmark path, no P/E chain, no
#: transform for EV/EBITDA and the three Q ratios), the single-factor Q path
#: it opened let E + one Q factor score a company, and six of its bodies were
#: PROPOSALS awaiting the owner's word. The owner ruled on all of them on
#: 2026-09-23 (R1-R21); v6 arrives beside it. Nothing here is repaired.
FREEZE_V5: dict[str, Any] = {
    "freeze_version": "spec_freeze_v5",
    "model_version": "equity_shaffer_v2_pit_SURVIVOR_ONLY_DIAGNOSTIC",
    "frozen_at": "2026-09-22",
    "digest": "58722f4c00b676fee76bcc10ba341f44ce18c540a2fbc90625570a51786a5b59",
    "n_components": 61,
    "status": STATUS_SUPERSEDED,
    "status_reason": REASON_ENGINE_LACKED_VQ_PATHS,
    "what_it_fixed": (
        "D3 decided (growth A, acceleration A, coverage OI/interest); the "
        "executable definitions FORMULAS_V1 with golden witnesses; the base and "
        "domain policies; the benchmark band and floor, V2_BLOCKS, "
        "TRANSFORM_PARAMS_V1, PINNED_CURVE, PAIR_PATHS and ENGINE_MUST_DERIVE_FROM "
        "digested by body; the callable scan and the manifest identity proof"),
    "content_defects_found_under_it": (
        "the engine computed 6 of 13 keys: ebitda_benchmark refused for an unbuilt "
        "MARGIN path, both P/E legs for an unbuilt chain, EV/EBITDA and the three "
        "Q ratios for missing transforms (reachable weight 0.60)",
        "Q available on interest_coverage ALONE: E + Q = 0.45 cleared "
        "MIN_BLOCK_WEIGHT, so a company was scored on one Q factor",
        "under PERCENTILE_RANK the ordering among negative-OI coverage rows was "
        "inverted relative to interest burden",
        "'ebitda_growth_base_near_zero' named a base, not the RATE band it "
        "refused on",
        "GAP 5 (one peer floor) and GAP 7 (price-basis translation) open; no "
        "within-block completeness rule",
        "six digested bodies carried AWAITING_OWNER_CONFIRMATION",
    ),
    "owner_rulings_that_superseded_it": "R1-R21 of 2026-09-23 (docs/MODEL-LINEAGE.md, spec_freeze_v6)",
    "superseded_definitions_it_digested": {
        "note": ("the v5 texts of the LIVE bodies v6 moved in place (the registry, the "
                 "valuation subfactor shape, the corporate-action gate version, the "
                 "replacement map), VERBATIM, because those bodies are corrected in place "
                 "by design and would otherwise survive only in git history -- the v4 "
                 "precedent"),
        "registry_InterestCoverageRank_last_note": (
            "negative operating income is a NEGATIVE coverage and ranks below every "
            "positively covered peer. KNOWN LIMITATION: among negative-OI rows the rank "
            "ORDERING IS INVERTED relative to interest burden (OI=-100 / interest=1 ranks "
            "below OI=-100 / interest=100); accepted pending owner ruling."),
        "registry_keys": ("EBITDAMarginRank", "EBITDAExcessLevel", "EBITDAGrowth",
                          "EBITDAAcceleration", "EBITDAScale", "RealRevenueGrowth",
                          "InterestCoverageRank"),
        "registry_InterestCoverageRank_domain_reference": "INTEREST_COVERAGE_DOMAIN_V1",
        "registry_EBITDAExcessLevel_scale": ("cohort_scale(multiplier=1.0, fallback_fixed="
                                             "DEFAULT_EXCESS_MARGIN_SCALE_K = 0.06) -- the fixed "
                                             "fallback withdrawn under R8 in v6"),
        "subfactor_fields": ("key", "code", "label", "role", "weight", "required_primitives",
                             "requires_cohort", "normalization_type", "anchor_source",
                             "quality_components", "depends_on", "can_stand_alone",
                             "sample_scope", "factor_spec_key", "why"),
        "action_gate_version": "pit_safe_corporate_action_gate_v1",
        "action_gate_empty_window": "no_events_recorded, NOT usable, for every empty window",
        "replaced_by_version_keys": ("factor_spec_v3", "factor_spec_v4"),
    },
    "rows_it_ever_produced_in_main_store": 0,
    "rows_in_isolated_scratch_dbs_stamped_with_it": {
        "db": "pit_replay_rss scratch and oracle DBs (disposable, outside the repository)",
        "per_arm_at_2019_06_28": {
            "50_targets": {"pit_feature": 650, "pit_pillar_score": 200, "pit_score": 25},
            "1000_targets": {"pit_feature": 13000, "pit_pillar_score": 4000, "pit_score": 461},
            "4000_targets": {"pit_feature": 52000, "pit_pillar_score": 16000, "pit_score": 1851},
        },
        "sources": ("rss_report_paused_1k_stream.json", "rss_report_paused_1k_legacy.json",
                    "rss_report_paused_4k_stream.json", "rss_report_paused_4k_legacy.json",
                    "rss_report_paused_4k_diag_cache2m.json", "oracle_head_1_1.json"),
        "engine_state": "PARTIAL_ENGINE -- 6 of 13 factors computable under pit_replay/1.1",
        "may_promote_anything": False,
    },
    "may_execute_replay": False,
    "superseded_by": "spec_freeze_v6",
    "never_repair_in_place": (
        "Its digest stays 58722f4c...; the V/Q bodies and the owner's rulings "
        "arrive as spec_freeze_v6 beside it, and no row may be stamped "
        "spec_freeze_v5 again."),
}


SPEC_FREEZE_VERSION = "spec_freeze_v6"

#: The survivorship limitation is IN THE NAME. Not in a footnote, not in a
#: column, not in a README -- in the string every score row will carry.
FROZEN_MODEL_VERSION = "equity_shaffer_v2_pit_SURVIVOR_ONLY_DIAGNOSTIC"

FROZEN_AT = "2026-09-23"

SAMPLE_SCOPE = "SURVIVOR_ONLY_DIAGNOSTIC"

#: The factor-spec version the live freeze digests by body and the engine MUST
#: derive eligibility from. pit_replay.validate() and require_gate() refuse
#: while pit_replay.FACTOR_SPEC_VERSION differs (v4 audit, R8). Digested, so
#: moving the engine without a freeze is itself drift. v6: factor_spec_v5,
#: whose Q/V rules match every balance-sheet and cash-flow primitive AT the
#: EBITDA period (owner rulings R10-R12, R15, R16).
ENGINE_MUST_DERIVE_FROM = "factor_spec_v5"


# ==========================================================================
# WHAT THE FREEZE MEANS, AND WHAT IT DOES NOT
# ==========================================================================

WHAT_FREEZING_MEANS: tuple[str, ...] = (
    "the economic definitions are specified",
    "the point-in-time rules are specified and enforced in code",
    "factor eligibility is decided, including which debt rungs may score",
    "the price basis and the share basis are decided and enforced",
    "missingness behaviour is specified -- what is refused and with what reason",
    "provenance handling is specified",
    "peer coherence is quantified as a number rather than a rung name",
    "coverage definitions carry both halves of the coverage contract",
    "therefore: the first DIAGNOSTIC replay can be calculated",
)

WHAT_IT_DOES_NOT_MEAN: tuple[str, ...] = (
    "NOT that the model is historically validated",
    "NOT that any Shaffer Score has been shown to predict anything",
    "NOT that the survivor-only results may promote anything",
    "NOT that the anchor level, the weights or the thresholds are optimal -- "
    "several are conventions, and each says so where it lives",
    "NOT that the specification is finished: it is FIXED, which is a different "
    "property. A successor is a new version beside it, never an edit of it.",
)


# ==========================================================================
# THE COMPONENTS -- read live, never re-typed
# ==========================================================================

#: (module, attribute, what it governs). `what` is a label for humans and is
#: NOT digested. A str/int/float/bool is rendered by repr; anything else as
#: canonical JSON by `_canonical` (never by repr, so no memory address can read
#: as drift).
COMPONENTS: tuple[tuple[str, str, str], ...] = (
    # the model identity
    ("pit_factor_spec", "CANDIDATE_MODEL_VERSION", "candidate lineage id"),
    ("pit_score_signature", "SIGNATURE_VERSION", "score comparability"),
    ("pit_score_signature", "COMPANY_SCALE_V2", "company score scale, +/-85"),
    ("pit_score_signature", "V2_MAJOR_WEIGHTS", "the block weight TABLE"),
    ("pit_score_signature", "PILLARS", "v1 pillar set (LEGACY, VGPD alphabet)"),
    ("pit_score_signature", "V2_BLOCKS", "the v2 block SET in canonical EVGQ order"),
    ("pit_factor_blocks", "FACTOR_BLOCK", "the factor-to-block MAP"),
    ("pit_factor_blocks", "BLOCK_MAP_VERSION", "the map policy version"),
    ("pit_factor_blocks", "MIN_BLOCK_WEIGHT", "the block-coverage floor"),
    ("pit_factor_blocks", "WITHIN_BLOCK_WEIGHTS", "within-block weights"),
    ("pit_factor_blocks", "POLICY_WEIGHTS_VERSION", "their policy version"),
    ("pit_factor_blocks", "POLICY_WEIGHTS_STATUS", "not empirically optimised"),
    ("pit_factor_blocks", "FACTOR_ROLE", "scoring vs zero-weight challenger"),
    ("pit_factor_blocks", "V_FACTOR_SUBFACTORS", "V granularity bridge"),
    ("pit_valuation_spec", "SUBFACTOR_WEIGHTS", "V within-block weights"),

    # ---- v4: THE BODIES -- seven bodies and two version strings. A version
    # string cannot detect a change to the thing it versions. `name()` = call it.
    ("pit_factor_spec", "FACTOR_SPEC_VERSION_V3", "factor spec version (v3)"),
    ("pit_factor_spec", "SPECS_V3", "the factor-spec BODIES, all 14"),
    ("pit_factor_spec", "PRICED_EPS_V2", "corrected P/E eligibility body"),
    ("pit_factor_spec", "REPLACED_BY_VERSION", "which keys v3 legitimately replaces"),
    # ---- v5: the D3 decisions, the executable definitions, the audit closures
    ("pit_factor_spec", "FACTOR_SPEC_VERSION_V4", "factor spec version (v4)"),
    ("pit_factor_spec", "SPECS_V4", "the factor-spec BODIES under v4, all 14"),
    ("pit_factor_spec", "FORMULAS_VERSION_V1", "executable-definition version"),
    ("pit_factor_spec", "FORMULAS_V1", "the executable DEFINITION of each scored key"),
    ("pit_factor_spec", "FORMULA_GOLDEN_V1", "golden witnesses for FORMULAS_V1"),
    ("pit_factor_spec", "EBITDA_GROWTH_BASE_POLICY_V1", "D3 growth denominator base policy"),
    ("pit_factor_spec", "INTEREST_COVERAGE_DOMAIN_V1", "D3 coverage denominator domain policy"),
    # ---- v4, continued: the registry, the direction tables, the subfactor bodies
    ("pit_normalization", "candidate_v2_registry()", "the normalization registry BODY"),
    # ---- v5: the benchmark band and floor the v4 audit found outside the digest
    ("pit_normalization", "BENCHMARK_BAND_V1", "the EBITDA-percentile band of the benchmark cohort"),
    ("pit_normalization", "BENCHMARK_MIN_COHORT_V1", "the smallest band that may carry a benchmark"),
    ("pit_factor_blocks", "DIRECTION_POLICY_VERSION", "factor-direction version"),
    ("pit_factor_blocks", "FACTOR_DIRECTION_V1", "the factor-direction TABLE"),
    ("pit_factor_blocks", "SUBFACTOR_DIRECTION_V1", "valuation subfactor directions"),
    ("pit_valuation_spec", "SUBFACTORS", "the valuation subfactor BODIES"),

    # point-in-time rules
    ("pit_policy", "LATENCY_POLICY_VERSION", "when information becomes actionable"),
    ("pit_policy", "LADDER_VERSION", "concept ladder v1"),
    ("pit_policy", "LADDER_VERSION_V2", "concept ladder v2"),

    # the valuation pillar
    ("pit_valuation_spec", "VALUATION_SPEC_VERSION", "three-subfactor structure"),
    ("pit_valuation_spec", "PRESENCE_RULE_VERSION", "OR over standalone subfactors"),
    ("pit_valuation_spec", "ANCHOR_POLICY_VERSION", "fixed P/E anchor policy"),
    ("pit_valuation_spec", "PE_ABSOLUTE_ANCHOR_V1", "the anchor LEVEL (convention)"),
    ("pit_valuation_spec", "TRANSFORM_VERSION", "convex extreme-valuation transform"),
    ("pit_valuation_spec", "TRANSFORM_PARAMS_V1", "the convex transform's parameters"),
    ("pit_valuation_spec", "PINNED_CURVE", "the published calibration the transform must reproduce"),
    ("pit_valuation_spec", "DEBT_RUNG_POLICY_VERSION", "EV/EBITDA debt eligibility"),
    ("pit_valuation_spec", "DEBT_RUNG_EVEBITDA_V1", "the eligibility MAP itself"),

    # price and share basis
    ("pit_price_basis", "PRICE_BASIS_POLICY_VERSION", "raw vs adjusted price"),
    ("pit_price_basis", "EPS_PAIR_POLICY_VERSION", "the EPS pair hierarchy"),
    ("pit_price_basis", "PAIR_PATHS", "the EPS pair hierarchy BODY, best first, refusal last"),
    ("pit_price_basis", "ACTION_GATE_VERSION", "PIT corporate-action gate"),
    ("pit_price_basis", "EPS_GROWTH_MIN_POSITIVE_BASE_V1", "growth base floor"),
    ("pit_price_basis", "PRICE_ROLE_REQUIRED_BASIS", "the role->basis mapping"),

    # earnings
    ("pit_eps", "EPS_LADDER_VERSION", "EPS concept ladder"),
    ("pit_eps", "TTM_RULE_VERSION", "TTM construction rule"),

    # factors, normalisation, coverage
    ("pit_factor_spec", "FACTOR_SPEC_VERSION_V2", "factor specs v2"),
    ("pit_normalization", "NORMALIZATION_POLICY_VERSION", "normalisation taxonomy"),
    ("pit_coverage_contract", "CONTRACT_VERSION", "the coverage contract"),
    ("pit_share_coverage", "PIT_SHARE_STATE_COVERAGE_V2", "canonical share coverage"),
    ("pit_share_coverage", "CANONICAL_DEFINITION_ID", "its definition id"),

    # the economic specification and the intermediate schema
    ("pit_invariants", "SPEC_VERSION", "executable economic invariants"),
    ("pit_intermediates", "INTERMEDIATE_SCHEMA_VERSION", "intermediate storage"),

    # the engine coupling
    ("pit_frozen_spec", "ENGINE_MUST_DERIVE_FROM",
     "the spec version the engine must derive eligibility from"),

    # ---- v6: the owner's V/Q rulings of 2026-09-23, as BODIES. The sealed v5
    # POLICY bodies above (the V1 policies, FORMULAS_V1, SPECS_V4) are preserved
    # verbatim and stay digested; these are their successors and the new
    # policies. Four LIVE bodies (the registry, SUBFACTORS, ACTION_GATE_VERSION,
    # REPLACED_BY_VERSION) moved in place by design; their v5 texts are recorded
    # verbatim in FREEZE_V5["superseded_definitions_it_digested"].
    ("pit_factor_spec", "FACTOR_SPEC_VERSION_V5", "factor spec version (v5)"),
    ("pit_factor_spec", "SPECS_V5", "the factor-spec BODIES under v5, all 14"),
    ("pit_factor_spec", "FORMULAS_VERSION_V2", "executable-definition version (v2)"),
    ("pit_factor_spec", "FORMULAS_V2", "the executable DEFINITION of each scored key (v2)"),
    ("pit_factor_spec", "FORMULA_GOLDEN_V2", "golden witnesses for FORMULAS_V2"),
    ("pit_factor_spec", "EBITDA_GROWTH_BASE_POLICY_V2", "R1/R3: the confirmed rate band, renamed refusal"),
    ("pit_factor_spec", "INTEREST_COVERAGE_DOMAIN_V2", "R2/R4/R5: confirmed rung rule, positive-OI rank, floor state"),
    ("pit_factor_spec", "LEVERAGE_DOMAIN_V1", "net_debt_ebitda domain policy"),
    ("pit_factor_spec", "FCF_DOMAIN_V1", "fcf_conversion domain policy"),
    ("pit_factor_spec", "MARKET_CAP_DOMAIN_V1", "debt_market_cap and the shared market cap"),
    ("pit_factor_spec", "EV_EBITDA_DOMAIN_V1", "ev_ebitda_supplement domain policy"),
    ("pit_factor_spec", "PE_DOMAIN_V1", "the two P/E legs' domain policy"),
    ("pit_factor_spec", "DEBT_RUNG_GATE_V1", "R10/R16: the strict debt-rung gate"),
    ("pit_factor_spec", "PEER_FLOOR_V1", "R14: one per-factor floor on valued cohort members"),
    ("pit_factor_blocks", "WITHIN_BLOCK_FLOOR_VERSION", "R6: within-block completeness version"),
    ("pit_factor_blocks", "WITHIN_BLOCK_FLOOR_V1", "R6: the within-block floors, V by presence rule"),
    ("pit_valuation_spec", "EV_TRANSFORM_VERSION", "R15: the EV transform version"),
    ("pit_valuation_spec", "EV_TRANSFORM_PARAMS_V1", "R15: the EV transform BODY"),
    ("pit_price_basis", "ACTION_GATE_SEMANTICS_V2", "R18: listing-level gate semantics"),
    ("pit_price_basis", "PRICE_BASIS_TRANSLATION_V1", "R20: the one price-basis translation"),
    ("pit_price_basis", "VALUATION_PRICE_POLICY_V1", "R19: the one valuation price policy"),
)


def _value(module_name: str, attr: str) -> Any:
    mod = importlib.import_module(module_name)
    if attr.endswith("()"):
        return getattr(mod, attr[:-2])()
    return getattr(mod, attr)


def _canonical(value: Any) -> str:
    """A deterministic JSON rendering of a BODY, for hashing.

    Prefers a type's own `as_dict`, then dataclass fields, then `__dict__`.
    Anything it cannot render deterministically is named by its class rather
    than by its repr, because a repr can carry a memory address and an
    address is different in every process -- which would make verify() fail
    for a reason that is not drift.
    """
    def enc(o: Any) -> Any:
        if hasattr(o, "as_dict"):
            try:
                return o.as_dict()
            except TypeError:
                return o.as_dict(None)
        if dataclasses.is_dataclass(o) and not isinstance(o, type):
            return dataclasses.asdict(o)
        if isinstance(o, (set, frozenset)):
            return sorted(str(x) for x in o)
        if callable(o):
            return "callable:%s" % getattr(o, "__qualname__", type(o).__qualname__)
        if hasattr(o, "__dict__"):
            return {k: v for k, v in sorted(vars(o).items())}
        return "unrenderable:%s" % type(o).__qualname__
    return json.dumps(value, sort_keys=True, default=enc, separators=(",", ":"))


def manifest() -> dict[str, str]:
    """Every component's CURRENT value, read live from its owning module."""
    out: dict[str, str] = {}
    for module_name, attr, _what in COMPONENTS:
        try:
            value = _value(module_name, attr)
        except (ImportError, AttributeError) as exc:
            out["%s:%s" % (module_name, attr)] = "MISSING(%s)" % (exc,)
            continue
        if isinstance(value, (str, int, float, bool)):
            rendered = repr(value)
        else:
            rendered = _canonical(value)
        out["%s:%s" % (module_name, attr)] = rendered
    return out


def digest(man: Optional[dict[str, str]] = None) -> str:
    """A content hash over the specification's components."""
    man = man if man is not None else manifest()
    blob = "\n".join("%s=%s" % (k, man[k]) for k in sorted(man))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


#: The digest AT THE MOMENT OF FREEZING. Recorded, not recomputed: the point of
#: writing it down is that a later recomputation can disagree with it.
FROZEN_DIGEST = "1afaca0c17c38657ddb131f03dad59e8d07aa1bceecf9bc9f336d8d09ee6fd85"


def verify() -> dict[str, Any]:
    """Has anything in the frozen specification moved?

    Returns a report rather than raising, because a legitimate successor
    version WILL move things -- the caller decides whether the drift is a new
    version or an accident. What it may not do is happen silently.
    """
    man = manifest()
    now = digest(man)
    missing = sorted(k for k, v in man.items() if v.startswith("MISSING("))
    drifted: list[dict[str, str]] = []
    if FROZEN_DIGEST not in ("PENDING", now):
        for key in sorted(man):
            if _FROZEN_MANIFEST.get(key) != man[key]:
                drifted.append({
                    "component": key,
                    "frozen": _FROZEN_MANIFEST.get(key, "(not in the freeze)"),
                    "now": man[key],
                })
    return {
        "freeze_version": SPEC_FREEZE_VERSION,
        "model_version": FROZEN_MODEL_VERSION,
        "frozen_at": FROZEN_AT,
        "frozen_digest": FROZEN_DIGEST,
        "current_digest": now,
        "intact": FROZEN_DIGEST == now,
        "recorded": FROZEN_DIGEST != "PENDING",
        "n_components": len(COMPONENTS),
        "missing_components": missing,
        "drifted": drifted,
        "reading": (
            "intact=True means every component still holds the value it held "
            "at the freeze. intact=False is NOT automatically an error -- a "
            "successor version legitimately moves things -- but it means this "
            "is no longer %s and must not be labelled as such."
            % FROZEN_MODEL_VERSION),
    }


#: THE RECORD. Written down at the freeze, from the same manifest the digest
#: was taken over. Kept separate from `manifest()` so `verify()` compares a
#: RECORD against a LIVE READ rather than a live read against itself -- the
#: latter would agree with itself forever and detect nothing.
_FROZEN_MANIFEST: dict[str, str] = {
    'pit_coverage_contract:CONTRACT_VERSION':
        "'coverage_contract_v1'",
    'pit_eps:EPS_LADDER_VERSION':
        "'eps_concept_ladder_v2'",
    'pit_eps:TTM_RULE_VERSION':
        "'ttm_eps_rule_v1'",
    'pit_factor_blocks:BLOCK_MAP_VERSION':
        "'factor_block_map_v1'",
    'pit_factor_blocks:DIRECTION_POLICY_VERSION':
        "'FACTOR_DIRECTION_V1'",
    'pit_factor_blocks:FACTOR_BLOCK':
        '{"debt_market_cap":"financial_quality","ebitda_acceleration":"ebitda_strength","ebitda_benchmark":"ebitda_strength","ebitda_efficiency":"ebitda_strength","ebitda_growth":"ebitda_strength","ebitda_scale":"ebitda_strength","ev_ebitda":"valuation","fcf_conversion":"financial_quality","interest_coverage":"financial_quality","net_debt_ebitda":"financial_quality","pe_ratio":"valuation","real_revenue_growth":"real_growth"}',
    'pit_factor_blocks:FACTOR_DIRECTION_V1':
        '{"debt_market_cap":"LOWER_IS_BETTER","ebitda_acceleration":"HIGHER_IS_BETTER","ebitda_benchmark":"HIGHER_IS_BETTER","ebitda_efficiency":"HIGHER_IS_BETTER","ebitda_growth":"HIGHER_IS_BETTER","ebitda_scale":"HIGHER_IS_BETTER","ev_ebitda":"LOWER_IS_BETTER","fcf_conversion":"HIGHER_IS_BETTER","interest_coverage":"HIGHER_IS_BETTER","net_debt_ebitda":"LOWER_IS_BETTER","pe_ratio":"EMBEDDED_IN_TRANSFORM","real_revenue_growth":"HIGHER_IS_BETTER"}',
    'pit_factor_blocks:FACTOR_ROLE':
        '{"debt_market_cap":"SCORING","ebitda_acceleration":"SCORING","ebitda_benchmark":"SCORING","ebitda_efficiency":"SCORING","ebitda_growth":"SCORING","ebitda_scale":"SCORING_WEIGHT_ZERO_CHALLENGER","ev_ebitda":"SCORING","fcf_conversion":"SCORING","interest_coverage":"SCORING","net_debt_ebitda":"SCORING","pe_ratio":"SCORING","real_revenue_growth":"SCORING"}',
    'pit_factor_blocks:MIN_BLOCK_WEIGHT':
        '0.4',
    'pit_factor_blocks:POLICY_WEIGHTS_STATUS':
        "'NOT_EMPIRICALLY_OPTIMIZED'",
    'pit_factor_blocks:POLICY_WEIGHTS_VERSION':
        "'POLICY_WEIGHTS_V1'",
    'pit_factor_blocks:SUBFACTOR_DIRECTION_V1':
        '{"ev_ebitda_supplement":"LOWER_IS_BETTER","pe_absolute":"EMBEDDED_IN_TRANSFORM","pe_relative":"EMBEDDED_IN_TRANSFORM"}',
    'pit_factor_blocks:V_FACTOR_SUBFACTORS':
        '{"ev_ebitda":["ev_ebitda_supplement"],"pe_ratio":["pe_absolute","pe_relative"]}',
    'pit_factor_blocks:WITHIN_BLOCK_FLOOR_V1':
        '{"ebitda_strength":0.5,"financial_quality":0.5,"real_growth":1.0,"valuation":"valuation_presence_rule_v1"}',
    'pit_factor_blocks:WITHIN_BLOCK_FLOOR_VERSION':
        "'WITHIN_BLOCK_FLOOR_V1'",
    'pit_factor_blocks:WITHIN_BLOCK_WEIGHTS':
        '{"ebitda_strength":{"ebitda_acceleration":0.2,"ebitda_benchmark":0.35,"ebitda_efficiency":0.2,"ebitda_growth":0.25,"ebitda_scale":0.0},"financial_quality":{"debt_market_cap":0.1,"fcf_conversion":0.35,"interest_coverage":0.3,"net_debt_ebitda":0.25},"real_growth":{"real_revenue_growth":1.0},"valuation":{}}',
    'pit_factor_spec:CANDIDATE_MODEL_VERSION':
        "'candidate_equity_shaffer_v2'",
    'pit_factor_spec:DEBT_RUNG_GATE_V1':
        '{"applies_to":["net_debt_ebitda","debt_market_cap","ev_ebitda_supplement"],"bound_on_the_row":"the rung key is the row\'s source_tag and its quantity bound (\'exact\' / \'lower_bound\') travels in resolved_inputs_json (\'db\'); an ELIGIBLE lower-bound rung is scored WITH that label","eligible":"pit_valuation_spec.DEBT_ELIGIBLE -> the value enters the factor","ladder":"concept_ladder_v2 total_debt","no_rung_at_period":"REFUSED:no_total_debt_at_ebitda_period","policy_version":"debt_rung_gate_v1","rejected_measured":"REFUSED:debt_rung_rejected_measured","rung_rule":"the FIRST rung carrying a value AT the EBITDA period end decides (every component for a SUM rung); lower rungs are not consulted. \'Non-stale\' is the EBITDA period\'s own bound: an instant anchored at P is as fresh as P (15-month annual budget), and the 6-month quarterly budget for free-floating instants does not apply (INTERPRETATION, docs/MODEL-LINEAGE.md)","same_gate_for_peers":true,"status":"OWNER_RULING_2026_09_23 (R10 strict gate for net_debt_ebitda and debt_market_cap; R16 strict first-rung walk for EV)","undetermined":"REFUSED:debt_rung_undetermined"}',
    'pit_factor_spec:EBITDA_GROWTH_BASE_POLICY_V1':
        '{"applies_to":["ebitda_growth","ebitda_acceleration"],"band_semantics":"the band is on the RATE, not on the base\'s absolute size: an 11x rise from a healthy base is refused as well, under the same name; |rate| == max_abs_rate exactly is COMPUTED (strict >); an acceleration refused because the LAG-1 rate failed carries the same code","beyond_max_abs_rate":"REFUSED:ebitda_growth_base_near_zero","both_negative":"COMPUTED with abs(): a loss shrinking from -100 to -50 is +0.5","denominator":"abs(ebitda_t-1)","max_abs_rate":10.0,"policy_version":"ebitda_growth_base_policy_v1","rationale":"a dollar floor is not scale-free and a revenue/asset floor needs a primitive the cohort rule does not charge for; the rate band refuses only order-of-magnitude base swings, keeps them out of the cohort IQR sample, and the score is tanh-bounded anyway","sign_transition":"COMPUTED: (E_t - E_t-1)/|E_t-1| is positive for loss->profit; base sign recorded on the row (\'bs\')","status":"CONVENTION_PROPOSED_2026_09_22_AWAITING_OWNER_CONFIRMATION","zero_base":"REFUSED:ebitda_growth_base_zero"}',
    'pit_factor_spec:EBITDA_GROWTH_BASE_POLICY_V2':
        '{"applies_to":["ebitda_growth","ebitda_acceleration"],"band_semantics":"the band is on the RATE, not on the base\'s absolute size: an 11x rise from a healthy base is refused as well, under the same name; |rate| == max_abs_rate exactly is COMPUTED (strict >); an acceleration refused because the LAG-1 rate failed carries the same code","beyond_max_abs_rate":"REFUSED:ebitda_growth_rate_beyond_band","both_negative":"COMPUTED with abs(): a loss shrinking from -100 to -50 is +0.5","denominator":"abs(ebitda_t-1)","max_abs_rate":10.0,"policy_version":"ebitda_growth_base_policy_v2","rationale":"a dollar floor is not scale-free and a revenue/asset floor needs a primitive the cohort rule does not charge for; the rate band refuses only order-of-magnitude base swings, keeps them out of the cohort IQR sample, and the score is tanh-bounded anyway","renamed_from":{"ebitda_growth_base_near_zero":"ebitda_growth_rate_beyond_band"},"sign_transition":"COMPUTED: (E_t - E_t-1)/|E_t-1| is positive for loss->profit; base sign recorded on the row (\'bs\')","status":"OWNER_CONFIRMED_2026_09_23 (R1: band approved as a modelling-domain guard, challenger-eligible later; R3: refusal renamed)","supersedes":"ebitda_growth_base_policy_v1","zero_base":"REFUSED:ebitda_growth_base_zero"}',
    'pit_factor_spec:EV_EBITDA_DOMAIN_V1':
        '{"above_band":"REFUSED:ev_ebitda_above_band -- multiple > 300 (v1\'s upper bound stands; the lower bound is withdrawn)","anchor":"the median multiple of the coherent cohort, target INCLUDED (R15)","applies_to":"ev_ebitda_supplement","debt_rung":"DEBT_RUNG_GATE_V1 (R16: first non-stale rung at the period, its status, same gate for peers)","direction":"LOWER_IS_BETTER, carried by the transform\'s sign (pit_factor_blocks: orientation only)","missing_cash":"REFUSED:no_cash_at_ebitda_period","missing_debt":"REFUSED:no_total_debt_at_ebitda_period","negative_enterprise_value":"VALID -- a negative multiple saturates the CHEAP side of the transform (R15)","no_market_cap":"REFUSED:market_cap_unavailable","non_positive_ebitda":"REFUSED:ebitda_non_positive","ordinary":"ebitda > 0; enterprise_value = market_cap + total_debt - cash, debt and cash AT the EBITDA period end","policy_version":"ev_ebitda_domain_v1","status":"OWNER_RULING_2026_09_23 (R15, R16)","transform":"pit_valuation_spec.EV_TRANSFORM_PARAMS_V1: 100 * tanh((median - ev_ebitda) / k), k = cohort IQR over the whole eligible vector; LOWER is better; saturates at +/-100"}',
    'pit_factor_spec:FACTOR_SPEC_VERSION_V2':
        "'factor_spec_v2'",
    'pit_factor_spec:FACTOR_SPEC_VERSION_V3':
        "'factor_spec_v3'",
    'pit_factor_spec:FACTOR_SPEC_VERSION_V4':
        "'factor_spec_v4'",
    'pit_factor_spec:FACTOR_SPEC_VERSION_V5':
        "'factor_spec_v5'",
    'pit_factor_spec:FCF_DOMAIN_V1':
        '{"applies_to":"fcf_conversion","capex_sign":"the filed POSITIVE outflow is subtracted (pit_policy capex ladder)","direction":"HIGHER_IS_BETTER applied AFTER this rule (pit_factor_blocks: orientation only)","missing_capex":"REFUSED:no_capex_at_ebitda_period","missing_operating_cash_flow":"REFUSED:no_operating_cash_flow_at_ebitda_period","negative_free_cash_flow":"COMPUTED and scored: a negative conversion is a real state","non_positive_ebitda":"REFUSED:ebitda_non_positive","ordinary":"ebitda > 0; free_cash_flow = operating_cash_flow - capex, both from the EBITDA flow period","period_rule":"operating cash flow and capex from the SAME flow period as the EBITDA (R12)","policy_version":"fcf_domain_v1","status":"OWNER_RULING_2026_09_23 (R11, R12)"}',
    'pit_factor_spec:FORMULAS_V1':
        '{"debt_market_cap":"debt_market_cap = total_debt / market_cap; market_cap = price * shares in ONE share basis (PRICE_ROLE_REQUIRED_BASIS); PERCENTILE_RANK","ebitda_acceleration":"ebitda_acceleration = ebitda_growth_t - ebitda_growth_t-1; ebitda_growth_t-1 = (ebitda_t-1 - ebitda_t-2) / abs(ebitda_t-2), same base policy; NOT a second difference over assets; ZERO_ANCHORED","ebitda_benchmark":"margin_excess = ebitda_margin - cohort_mean_margin; cohort = peers with p50 <= ebitda_i <= p75 of cohort EBITDA (pit_normalization.BENCHMARK_BAND_V1), |band| >= BENCHMARK_MIN_COHORT_V1 (benchmark_margin_50_75); BENCHMARK_ANCHORED","ebitda_efficiency":"ebitda_margin = ebitda / revenue; revenue AT the EBITDA period (pit_replay.R_NO_REVENUE_AT_P otherwise); PERCENTILE_RANK","ebitda_growth":"ebitda_growth = (ebitda_t - ebitda_t-1) / abs(ebitda_t-1); base policy EBITDA_GROWTH_BASE_POLICY_V1; ZERO_ANCHORED","ebitda_scale":"ebitda_scale = ebitda (raw dollars, PERCENTILE_RANK); DIVERGES from pit_derive\'s ebitda / sector_ebitda_p50 -- rank-identical only when p50 > 0","ev_ebitda_supplement":"ev_ebitda = enterprise_value / ebitda; enterprise_value = market_cap + total_debt - cash; market_cap on price_raw_as_traded (PRICE_ROLE_REQUIRED_BASIS); debt rung DEBT_ELIGIBLE under DEBT_RUNG_EVEBITDA_V1; anchor = cohort median","fcf_conversion":"fcf_conversion = free_cash_flow / ebitda; free_cash_flow = operating_cash_flow - capex; PERCENTILE_RANK","interest_coverage":"interest_coverage = operating_income / interest_expense; both AT ONE (period_end, qtrs=4); domain INTEREST_COVERAGE_DOMAIN_V1; PERCENTILE_RANK","net_debt_ebitda":"net_debt_ebitda = net_debt / ebitda; net_debt = total_debt - cash; PERCENTILE_RANK","pe_absolute":"pe = price_raw_as_traded / earnings_per_share_pit, one share basis (pit_price_basis.PAIR_PATHS), EPS > 0 (PRICED_EPS_V2); S = extreme_valuation_transform_v1(pe, PE_ABSOLUTE_ANCHOR_V1); DIVERGES from pit_derive\'s market_cap / net_income (SPECS_V3[\'pe_ratio\'].graph_divergence)","pe_relative":"pe as pe_absolute; anchor = cohort median pe (ANCHOR_COHORT_MEDIAN); BENCHMARK_ANCHORED","real_revenue_growth":"real_revenue_growth = (1 + revenue_growth) / (1 + inflation) - 1 == (revenue_t / cpi_t) / (revenue_t-1 / cpi_t-1) - 1 for a positive deflated base (pit_replay.R_NON_POSITIVE_BASE otherwise); one revenue rung, CPI over the window; ZERO_ANCHORED"}',
    'pit_factor_spec:FORMULAS_V2':
        '{"debt_market_cap":"debt_market_cap = total_debt / market_cap; market_cap = price * shares in ONE share basis (PRICE_ROLE_REQUIRED_BASIS, PRICE_BASIS_TRANSLATION_V1, R19, R20); total_debt AT the EBITDA period end under DEBT_RUNG_GATE_V1 (MARKET_CAP_DOMAIN_V1, R10, R12); PERCENTILE_RANK, LOWER is better","ebitda_acceleration":"ebitda_acceleration = ebitda_growth_t - ebitda_growth_t-1; ebitda_growth_t-1 = (ebitda_t-1 - ebitda_t-2) / abs(ebitda_t-2), same base policy (EBITDA_GROWTH_BASE_POLICY_V2); NOT a second difference over assets; ZERO_ANCHORED","ebitda_benchmark":"margin_excess = ebitda_margin - cohort_mean_margin; cohort = peers with p50 <= ebitda_i <= p75 of cohort EBITDA (pit_normalization.BENCHMARK_BAND_V1), target INCLUDED in the vector and the band (R7), ONE cohort-constant M_s per peer set; |band| >= BENCHMARK_MIN_COHORT_V1 = 3 or benchmark_band_too_thin, never widened (R9); |vector| >= PEER_FLOOR_V1 or cohort_values_below_floor (R14); BENCHMARK_ANCHORED with k = cohort IQR over the WHOLE eligible margin vector (R8)","ebitda_efficiency":"ebitda_margin = ebitda / revenue; revenue AT the EBITDA period (pit_replay.R_NO_REVENUE_AT_P otherwise); PERCENTILE_RANK","ebitda_growth":"ebitda_growth = (ebitda_t - ebitda_t-1) / abs(ebitda_t-1); base policy EBITDA_GROWTH_BASE_POLICY_V2 (ebitda_growth_base_zero / ebitda_growth_rate_beyond_band); ZERO_ANCHORED","ebitda_scale":"ebitda_scale = ebitda (raw dollars, PERCENTILE_RANK); DIVERGES from pit_derive\'s ebitda / sector_ebitda_p50 -- rank-identical only when p50 > 0","ev_ebitda_supplement":"ev_ebitda = enterprise_value / ebitda; enterprise_value = market_cap + total_debt - cash, debt and cash AT the EBITDA period end (R12); market_cap on price_raw_as_traded (PRICE_ROLE_REQUIRED_BASIS, PRICE_BASIS_TRANSLATION_V1); debt rung DEBT_ELIGIBLE under DEBT_RUNG_GATE_V1 (R16); ebitda > 0 (ebitda_non_positive); negative enterprise_value VALID (R15); ev_ebitda > 300 refused (ev_ebitda_above_band); anchor = cohort median (target INCLUDED); S = 100 * tanh((median - ev_ebitda) / k), k = cohort IQR over the whole eligible vector (EV_TRANSFORM_PARAMS_V1); LOWER is better","fcf_conversion":"fcf_conversion = free_cash_flow / ebitda; free_cash_flow = operating_cash_flow - capex, both AT the EBITDA flow period (FCF_DOMAIN_V1, R12); ebitda > 0 (ebitda_non_positive, R11); PERCENTILE_RANK","interest_coverage":"interest_coverage = operating_income / interest_expense; both AT ONE (period_end, qtrs=4); domain INTEREST_COVERAGE_DOMAIN_V2: operating_income <= 0 is the FLOOR STATE (scored -100, excluded from the rank population, R5); PERCENTILE_RANK over positive-coverage peers (R4)","net_debt_ebitda":"net_debt_ebitda = net_debt / ebitda; net_debt = total_debt - cash, both AT the EBITDA period end (LEVERAGE_DOMAIN_V1, R12); ebitda > 0 (ebitda_non_positive, R11 per the v1 precedent); debt rung under DEBT_RUNG_GATE_V1 (R10); PERCENTILE_RANK, LOWER is better","pe_absolute":"pe = price_raw_as_traded / earnings_per_share_pit, one share basis (pit_price_basis.PAIR_PATHS, pit_safe_corporate_action_gate_v2), EPS > EPS_NEAR_ZERO_ABS (PE_DOMAIN_V1: loss, zero and near-zero-positive EPS are named refusals, R17); price under VALUATION_PRICE_POLICY_V1 (R19) and PRICE_BASIS_TRANSLATION_V1 (R20); S = extreme_valuation_transform_v1(pe, PE_ABSOLUTE_ANCHOR_V1); DIVERGES from pit_derive\'s market_cap / net_income (SPECS_V3[\'pe_ratio\'].graph_divergence)","pe_relative":"pe as pe_absolute; anchor = median pe of the coherent cohort, target INCLUDED, near-zero-positive members EXCLUDED (R17); S = extreme_valuation_transform_v1(pe, median) -- the CONVEX transform around the cohort median, NOT a tanh (owner ruling R17 2026-09-23; supersedes FORMULAS_V1\'s BENCHMARK_ANCHORED wording); requires pe_absolute (pe_relative_needs_absolute otherwise)","real_revenue_growth":"real_revenue_growth = (1 + revenue_growth) / (1 + inflation) - 1 == (revenue_t / cpi_t) / (revenue_t-1 / cpi_t-1) - 1 for a positive deflated base (pit_replay.R_NON_POSITIVE_BASE otherwise); one revenue rung, CPI over the window; ZERO_ANCHORED"}',
    'pit_factor_spec:FORMULAS_VERSION_V1':
        "'formulas_v1'",
    'pit_factor_spec:FORMULAS_VERSION_V2':
        "'formulas_v2'",
    'pit_factor_spec:FORMULA_GOLDEN_V1':
        '[{"ebitda":[1000.0,750.0,600.0],"expect":0.3333333333333333,"factor":"ebitda_growth"},{"ebitda":[-50.0,-100.0,-100.0],"expect":0.5,"factor":"ebitda_growth"},{"ebitda":[1000.0,-250.0,600.0],"expect":5.0,"factor":"ebitda_growth"},{"ebitda":[100.0,0.0,50.0],"expect":"REFUSED:ebitda_growth_base_zero","factor":"ebitda_growth"},{"ebitda":[1000.0,1.0,50.0],"expect":"REFUSED:ebitda_growth_base_near_zero","factor":"ebitda_growth"},{"ebitda":[1000.0,750.0,600.0],"expect":0.08333333333333331,"factor":"ebitda_acceleration"},{"ebitda":[100.0,100.0,100.0],"expect":0.0,"factor":"ebitda_acceleration"},{"ebitda":[-50.0,-100.0,-200.0],"expect":0.0,"factor":"ebitda_acceleration"},{"ebitda":[801300000.0,724500000.0,675000000.0],"expect":0.03267080745341615,"factor":"ebitda_acceleration"},{"ebitda":[1000.0,750.0,0.0],"expect":"REFUSED:ebitda_growth_base_zero","factor":"ebitda_acceleration"},{"ebitda":[1000.0,750.0,1.0],"expect":"REFUSED:ebitda_growth_base_near_zero","factor":"ebitda_acceleration"},{"ebitda":[1100.0,100.0,50.0],"expect":10.0,"factor":"ebitda_growth"},{"ebitda":[-900.0,100.0,50.0],"expect":-10.0,"factor":"ebitda_growth"},{"ebitda":[1200.0,100.0,50.0],"expect":"REFUSED:ebitda_growth_base_near_zero","factor":"ebitda_growth"},{"expect":5.0,"factor":"interest_coverage","interest_expense":100.0,"operating_income":500.0},{"expect":-2.0,"factor":"interest_coverage","interest_expense":100.0,"operating_income":-200.0},{"expect":8.529247910863509,"factor":"interest_coverage","interest_expense":71800000.0,"operating_income":612400000.0},{"expect":"REFUSED:interest_expense_zero","factor":"interest_coverage","interest_expense":0.0,"operating_income":500.0},{"expect":"REFUSED:interest_expense_negative","factor":"interest_coverage","interest_expense":-10.0,"operating_income":500.0},{"expect":"REFUSED:no_interest_expense_at_operating_income_period","factor":"interest_coverage","interest_expense":null,"operating_income":500.0}]',
    'pit_factor_spec:FORMULA_GOLDEN_V2':
        '[{"ebitda":[1000.0,750.0,600.0],"expect":0.3333333333333333,"factor":"ebitda_growth"},{"ebitda":[-50.0,-100.0,-100.0],"expect":0.5,"factor":"ebitda_growth"},{"ebitda":[1000.0,-250.0,600.0],"expect":5.0,"factor":"ebitda_growth"},{"ebitda":[100.0,0.0,50.0],"expect":"REFUSED:ebitda_growth_base_zero","factor":"ebitda_growth"},{"ebitda":[1000.0,1.0,50.0],"expect":"REFUSED:ebitda_growth_rate_beyond_band","factor":"ebitda_growth"},{"ebitda":[1000.0,750.0,600.0],"expect":0.08333333333333331,"factor":"ebitda_acceleration"},{"ebitda":[100.0,100.0,100.0],"expect":0.0,"factor":"ebitda_acceleration"},{"ebitda":[-50.0,-100.0,-200.0],"expect":0.0,"factor":"ebitda_acceleration"},{"ebitda":[801300000.0,724500000.0,675000000.0],"expect":0.03267080745341615,"factor":"ebitda_acceleration"},{"ebitda":[1000.0,750.0,0.0],"expect":"REFUSED:ebitda_growth_base_zero","factor":"ebitda_acceleration"},{"ebitda":[1000.0,750.0,1.0],"expect":"REFUSED:ebitda_growth_rate_beyond_band","factor":"ebitda_acceleration"},{"ebitda":[1100.0,100.0,50.0],"expect":10.0,"factor":"ebitda_growth"},{"ebitda":[-900.0,100.0,50.0],"expect":-10.0,"factor":"ebitda_growth"},{"ebitda":[1200.0,100.0,50.0],"expect":"REFUSED:ebitda_growth_rate_beyond_band","factor":"ebitda_growth"},{"expect":5.0,"factor":"interest_coverage","interest_expense":100.0,"operating_income":500.0},{"expect":-2.0,"factor":"interest_coverage","interest_expense":100.0,"operating_income":-200.0},{"expect":8.529247910863509,"factor":"interest_coverage","interest_expense":71800000.0,"operating_income":612400000.0},{"expect":"REFUSED:interest_expense_zero","factor":"interest_coverage","interest_expense":0.0,"operating_income":500.0},{"expect":"REFUSED:interest_expense_negative","factor":"interest_coverage","interest_expense":-10.0,"operating_income":500.0},{"expect":"REFUSED:no_interest_expense_at_operating_income_period","factor":"interest_coverage","interest_expense":null,"operating_income":500.0},{"expect":-2.0,"expect_floor_state":true,"factor":"interest_coverage","interest_expense":100.0,"operating_income":-200.0},{"expect":0.0,"expect_floor_state":true,"factor":"interest_coverage","interest_expense":100.0,"operating_income":0.0},{"expect":5.0,"expect_floor_state":false,"factor":"interest_coverage","interest_expense":100.0,"operating_income":500.0},{"cash":200.0,"ebitda":[400.0,300.0,200.0],"expect":2.0,"factor":"net_debt_ebitda","total_debt":{"LongTermDebtCurrent":200.0,"LongTermDebtNoncurrent":700.0,"ShortTermBorrowings":100.0}},{"cash":1300.0,"ebitda":[400.0,300.0,200.0],"expect":-0.75,"factor":"net_debt_ebitda","total_debt":{"LongTermDebtCurrent":200.0,"LongTermDebtNoncurrent":700.0,"ShortTermBorrowings":100.0}},{"cash":200.0,"ebitda":[0.0,300.0,200.0],"expect":"REFUSED:ebitda_non_positive","factor":"net_debt_ebitda","total_debt":{"LongTermDebtCurrent":200.0,"LongTermDebtNoncurrent":700.0,"ShortTermBorrowings":100.0}},{"cash":200.0,"ebitda":[400.0,300.0,200.0],"expect":"REFUSED:debt_rung_rejected_measured","factor":"net_debt_ebitda","total_debt":{"LongTermDebtNoncurrent":700.0}},{"cash":200.0,"ebitda":[400.0,300.0,200.0],"expect":"REFUSED:debt_rung_undetermined","factor":"net_debt_ebitda","total_debt":{"LongTermDebt":900.0}},{"cash":200.0,"ebitda":[400.0,300.0,200.0],"expect":"REFUSED:debt_rung_undetermined","factor":"net_debt_ebitda","total_debt":{"DebtLongtermAndShorttermCombinedAmount":950.0,"LongTermDebtCurrent":200.0,"LongTermDebtNoncurrent":700.0}},{"cash":null,"ebitda":[400.0,300.0,200.0],"expect":"REFUSED:no_cash_at_ebitda_period","factor":"net_debt_ebitda","total_debt":{"LongTermDebtCurrent":200.0,"LongTermDebtNoncurrent":700.0,"ShortTermBorrowings":100.0}},{"cash":200.0,"ebitda":[400.0,300.0,200.0],"expect":"REFUSED:no_total_debt_at_ebitda_period","factor":"net_debt_ebitda","total_debt":{}},{"capex":200.0,"ebitda":[400.0,300.0,200.0],"expect":0.75,"factor":"fcf_conversion","operating_cash_flow":500.0},{"capex":300.0,"ebitda":[400.0,300.0,200.0],"expect":-0.5,"factor":"fcf_conversion","operating_cash_flow":100.0},{"capex":200.0,"ebitda":[-400.0,300.0,200.0],"expect":"REFUSED:ebitda_non_positive","factor":"fcf_conversion","operating_cash_flow":500.0},{"capex":null,"ebitda":[400.0,300.0,200.0],"expect":"REFUSED:no_capex_at_ebitda_period","factor":"fcf_conversion","operating_cash_flow":500.0},{"capex":200.0,"ebitda":[400.0,300.0,200.0],"expect":"REFUSED:no_operating_cash_flow_at_ebitda_period","factor":"fcf_conversion","operating_cash_flow":null},{"expect":{"anchor":0.17,"n_band":3,"n_vector":12},"factor":"ebitda_benchmark","kind":"benchmark","pairs":[[100.0,0.1],[200.0,0.11],[300.0,0.12],[400.0,0.13],[500.0,0.14],[600.0,0.15],[700.0,0.16],[800.0,0.17],[900.0,0.18],[1000.0,0.19],[1100.0,0.2],[1200.0,0.21]]},{"expect":"REFUSED:benchmark_band_too_thin","factor":"ebitda_benchmark","kind":"benchmark","pairs":[[100.0,0.1],[200.0,0.2]]}]',
    'pit_factor_spec:INTEREST_COVERAGE_DOMAIN_V1':
        '{"direction":"HIGHER_IS_BETTER applied AFTER this rule (pit_factor_blocks: orientation only)","missing":"REFUSED:no_interest_expense_at_operating_income_period","negative":"REFUSED:interest_expense_negative","negative_operating_income":"LEGITIMATE_NEGATIVE_COVERAGE -- scored, ranks below every positive peer. KNOWN LIMITATION under PERCENTILE_RANK: the ORDERING AMONG NEGATIVE-OI ROWS IS INVERTED relative to interest burden (OI=-100 / interest=1 ranks below OI=-100 / interest=100); accepted pending owner ruling","no_numerator":"REFUSED:no_operating_income_period","ordinary":"interest_expense > 0 at the operating_income period","policy_version":"interest_coverage_domain_v1","rung_rule":"the FIRST interest_expense ladder rung carrying a value AT the operating-income period is used; a tagged zero on that rung is interest_expense_zero and lower rungs are not consulted","status":"OWNER_RULE_2026_09_22; vocabulary, rung rule and transform PROPOSED, AWAITING_OWNER_CONFIRMATION","zero":"REFUSED:interest_expense_zero"}',
    'pit_factor_spec:INTEREST_COVERAGE_DOMAIN_V2':
        '{"direction":"HIGHER_IS_BETTER applied AFTER this rule (pit_factor_blocks: orientation only)","missing":"REFUSED:no_interest_expense_at_operating_income_period","negative":"REFUSED:interest_expense_negative","no_numerator":"REFUSED:no_operating_income_period","non_positive_operating_income":"FLOOR_STATE:interest_coverage_floor_state -- operating_income <= 0 with interest_expense > 0 is SCORED at the PERCENTILE_RANK floor (-100), the raw coverage (<= 0) is written on the row, and the row is EXCLUDED from the rank population (R5)","ordinary":"interest_expense > 0 AND operating_income > 0 at the operating_income period","policy_version":"interest_coverage_domain_v2","rank_population":"positive-coverage observations only: operating_income > 0 and interest_expense > 0 (R4); the target ranks among positive peers and floor-state peers are not in the denominator","rung_rule":"the FIRST interest_expense ladder rung carrying a value AT the operating-income period is used; a tagged zero on that rung is interest_expense_zero and lower rungs are not consulted","status":"OWNER_CONFIRMED_2026_09_23 (R2 rung rule; R4 rank population; R5 floor state)","supersedes":"interest_coverage_domain_v1","zero":"REFUSED:interest_expense_zero"}',
    'pit_factor_spec:LEVERAGE_DOMAIN_V1':
        '{"applies_to":"net_debt_ebitda","debt_rung":"DEBT_RUNG_GATE_V1","direction":"LOWER_IS_BETTER applied AFTER this rule (pit_factor_blocks: orientation only)","missing_cash":"REFUSED:no_cash_at_ebitda_period","missing_debt":"REFUSED:no_total_debt_at_ebitda_period","negative_net_debt":"COMPUTED and scored: net cash is financially strong (v1 precedent)","non_positive_ebitda":"REFUSED:ebitda_non_positive","ordinary":"ebitda > 0; net_debt = total_debt - cash, both AT the EBITDA period end","period_rule":"debt and cash AT the EBITDA period end (R12); no period mixing","policy_version":"leverage_domain_v1","status":"OWNER_RULING_2026_09_23 (R10, R11 per v1 precedent, R12)"}',
    'pit_factor_spec:MARKET_CAP_DOMAIN_V1':
        '{"applies_to":"debt_market_cap; market_cap is shared with ev_ebitda_supplement","debt_rung":"DEBT_RUNG_GATE_V1","direction":"LOWER_IS_BETTER applied AFTER this rule (pit_factor_blocks: orientation only)","listing":"REFUSED:no_scored_listing | REFUSED:listing_ambiguous -- no share-class switching (R19)","missing_debt":"REFUSED:no_total_debt_at_ebitda_period","no_market_cap":"REFUSED:market_cap_unavailable (the pit_rawprice reason travels in \'mr\')","no_price":"REFUSED:no_valuation_price_within_window","non_positive_market_cap":"REFUSED:market_cap_non_positive","ordinary":"market_cap = raw as-traded price x defensible share count > 0; total_debt AT the EBITDA period end","period_rule":"debt AT the EBITDA period end (R12, INTERPRETATION: one balance-sheet date per row)","policy_version":"market_cap_domain_v1","price":"pit_price_basis.VALUATION_PRICE_POLICY_V1: exact score-date raw close, else the latest prior valid raw close within 10 calendar days, valid trade required, same listing, price_age_days on the row (R19)","price_basis":"PRICE_ROLE_REQUIRED_BASIS[valuation] through PRICE_BASIS_TRANSLATION_V1 (R20)","shares":"pit_rawprice strict share-class policy; a count that cannot be shown to cover the issuer is REFUSED","status":"OWNER_RULING_2026_09_23 (R10, R12, R19, R20)"}',
    'pit_factor_spec:PEER_FLOOR_V1':
        '{"debt_market_cap":5,"ebitda_acceleration":8,"ebitda_benchmark":12,"ebitda_efficiency":5,"ebitda_growth":8,"ebitda_scale":5,"ev_ebitda_supplement":12,"fcf_conversion":5,"interest_coverage":5,"net_debt_ebitda":5,"pe_relative":3}',
    'pit_factor_spec:PE_DOMAIN_V1':
        '{"absolute_transform":"extreme_valuation_transform_v1 around PE_ABSOLUTE_ANCHOR_V1 (fixed 20.0)","applies_to":"pe_absolute, pe_relative","dependency":"REFUSED:pe_relative_needs_absolute -- the context leg cannot exist without the anchor leg","direction":"EMBEDDED_IN_TRANSFORM (a signed transform is not oriented again)","eps_source":"pit_eps_ttm through pit_eps_cohort.EntityTtm.select (mirrors pit_eps.ttm_eps_as_of_detail); concept_key and method travel on the row (\'ek\', \'em\') because cohorts may mix TTM methods and EPS rungs (R21)","loss":"REFUSED:unavailable_loss_making","near_zero_positive":"REFUSED:eps_near_zero_positive -- 0 < EPS <= EPS_NEAR_ZERO_ABS is excluded from BOTH legs and from the pe_relative median population (R17)","no_eps":"REFUSED:no_ttm_eps (the pit_eps reason travels in \'er\')","ordinary":"pe = raw as-traded price / TTM EPS carried to the score-date share basis; EPS > EPS_NEAR_ZERO_ABS","policy_version":"pe_domain_v1","price":"as MARKET_CAP_DOMAIN_V1 (one valuation price resolver, R19, R20)","relative_anchor":"the median pe of the coherent cohort, target INCLUDED, near-zero members excluded (R17)","relative_transform":"extreme_valuation_transform_v1 around the cohort median -- the CONVEX transform, not a tanh (R17)","share_basis":"pit_price_basis.normalised_pe under pit_safe_corporate_action_gate_v2: REFUSED:share_basis_unresolved (gate status in \'gs\')","status":"OWNER_RULING_2026_09_23 (R17, R19, R21)","zero":"REFUSED:unavailable_eps_zero_undefined"}',
    'pit_factor_spec:PRICED_EPS_V2':
        '{"lags":[0],"matched_period":[],"positive_screen":"earnings_per_share_pit > 0","price_conditional_terms":["earnings_per_share_pit","price_adjusted","requires_price"],"primitives":["price_adjusted","earnings_per_share_pit"],"requires_defensible_shares":false,"requires_price":true,"rule_id":"price_and_pit_eps_v2","unavailable_primitives":[],"value_band":null,"why":"A resolvable point-in-time price and a point-in-time EPS. NO SHARE COUNT: the per-share division is already inside the filed EPS figure, which is why this chain is two primitives where EV/EBITDA is six. EPS IS IN THIS STORE as of the pit_eps ingest: pit_eps_obs 888,486 rows, pit_eps_ttm 147,914, 1,962 of the 7,102 peers on 2019-06-28 with a TTM knowable on the date. The v1/v2 rule declared it GENUINELY_UNAVAILABLE and that declaration is now false; it is corrected here rather than bypassed in an engine."}',
    'pit_factor_spec:REPLACED_BY_VERSION':
        '{"factor_spec_v3":["ebitda_benchmark","pe_ratio"],"factor_spec_v4":["ebitda_acceleration","ebitda_benchmark","ebitda_growth","interest_coverage","pe_ratio"],"factor_spec_v5":["debt_market_cap","ebitda_acceleration","ebitda_benchmark","ebitda_growth","ev_ebitda","fcf_conversion","interest_coverage","net_debt_ebitda","pe_ratio"]}',
    'pit_factor_spec:SPECS_V3':
        '[{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"SCORED QUANTITY DIFFERS FROM pit_derive\'s cohort_mean_ebitda node: the graph node is a DOLLAR mean; this spec scores the MARGIN excess against the cohort\'s mean margin (pit_normalization.benchmark_margin_50_75). The cohort selection quantity is still EBITDA dollars. FACTOR_SPEC_V2_DOLLAR_BENCHMARK_AND_STALE_EPS_DECLARATION: factor_spec_v2 compares EBITDA in DOLLARS against the 50-75 cohort (a size factor the design record had already rejected) and declares point-in-time EPS unavailable although pit_eps_obs holds 888,486 rows. factor_spec_v3 corrects both. No main-store row was ever produced under v2.","graph_node":"cohort_mean_ebitda","key":"ebitda_benchmark","measured":{"band_gate_lifted":{"max":179,"mean":9.18,"median":3,"p10":1,"p25":2,"p75":6,"p90":18,"pct_ge_3":68.34},"benchmark_formed_pct":56.0,"under_the_price_conditional_rule":{"band_gate_lifted_median":1,"band_pct_ge_3":7.56,"benchmark_formed_pct":5.71,"vector_median":1,"vector_pct_ge_12":5.71},"vector":{"max":718,"mean":36.04,"median":13,"p10":5,"p25":8,"p75":23,"p90":70,"pct_ge_12":56.0,"pct_ge_3":95.2}},"member_quantity":"ebitda","min_band_members":3,"min_peer_count":12,"min_peer_why":"the benchmark is the MEAN OF THE 50-75 BAND, and the band is a quarter of the vector by construction, so three members inside the band needs twelve in the vector. 3 is the floor for the percentiles themselves (company_scoring.MIN_EBITDA_COHORT).","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"benchmark_anchored_tanh_v1","note":"SHAFFER_V1_KNOWN_LIMITATION: company_scoring.build_ebitda_peer_cohort applies EV_EBITDA_SIX here. Measured, that turns a 56.0% benchmark-formation rate into 5.7% -- the benchmark is unavailable for 94% of peer sets because of a filter that has nothing to do with the quantity it measures. factor_spec_v3: SCORED QUANTITY IS THE EBITDA MARGIN, cohort selection is unchanged. Select the 50th-75th percentile cohort by peer EBITDA dollars (member_quantity=\'ebitda\', as before); then compare EBITDA_i/Revenue_i against the cohort\'s mean margin, BENCHMARK_ANCHORED. Owner decision 2026-09-22. The dollar reading is FACTOR_SPEC_V2_KNOWN_LIMITATION.","peer_eligibility":{"lags":[0],"matched_period":["operating_income","depreciation_amortisation","revenue"],"positive_screen":"","price_conditional_terms":[],"primitives":["operating_income","depreciation_amortisation","revenue"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"ebitda_and_revenue_matched_v1","unavailable_primitives":[],"value_band":null,"why":"The EBITDA cohort, further restricted to members whose revenue resolves at the SAME (period_end, qtrs). A margin is a ratio of one period\'s numbers. MEASURED: the matched-period rule costs 0.1 pp against selecting revenue independently, so it is free -- enforce it."},"question":"What does a right-sized peer in this sector earn?","required_primitives":["operating_income","depreciation_amortisation","revenue"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"ebitda_scale divides by sector_ebitda_p50, which the graph builds from sector_ebitda_vector -- the PRICED sector\'s percentiles. The same contamination reached through a percentile instead of a band mean. SHAFFER_V1_KNOWN_LIMITATION","graph_node":"ebitda_scale","key":"ebitda_scale","measured":{"vector":{"median":13,"pct_ge_3":95.2}},"member_quantity":"ebitda","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"Shares EBITDA_ONLY with ebitda_benchmark BY REFERENCE, not by copy: two factors that must mean the same cohort cannot drift apart if they are the same object. Only the threshold differs -- a rank needs 3, a band needs 12.","peer_eligibility":{"lags":[0],"matched_period":["operating_income","depreciation_amortisation"],"positive_screen":"","price_conditional_terms":[],"primitives":["operating_income","depreciation_amortisation"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"ebitda_only_v1","unavailable_primitives":[],"value_band":null,"why":"Members of the peer set whose EBITDA resolves: operating_income AND depreciation_amortisation at a MATCHED (period_end, qtrs), each non-stale under pit_policy.is_stale, each unit=\'USD\'. NOTHING ELSE. No price, no share count, no debt, no cash. This is the sector\'s distribution of earning power, and conditioning it on tradeability would make it the PRICED sector\'s distribution -- which is what the frozen core does, and what costs it 12 members of median cohort size."},"question":"How many median peers would it take to make this company?","required_primitives":["operating_income","depreciation_amortisation"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"ebitda_margin_rank","key":"ebitda_efficiency","measured":{"matched_period_cost_pp":0.1,"vector":{"max":699,"median":12,"p10":4,"p25":8,"p75":22,"p90":66,"pct_ge_12":52.8,"pct_ge_3":94.5}},"member_quantity":"ebitda_margin","min_band_members":0,"min_peer_count":3,"min_peer_why":"a cohort MEAN, not a percentile: the core\'s floor of 3 applies.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"","peer_eligibility":{"lags":[0],"matched_period":["operating_income","depreciation_amortisation","revenue"],"positive_screen":"","price_conditional_terms":[],"primitives":["operating_income","depreciation_amortisation","revenue"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"ebitda_and_revenue_matched_v1","unavailable_primitives":[],"value_band":null,"why":"The EBITDA cohort, further restricted to members whose revenue resolves at the SAME (period_end, qtrs). A margin is a ratio of one period\'s numbers. MEASURED: the matched-period rule costs 0.1 pp against selecting revenue independently, so it is free -- enforce it."},"question":"How profitable is a typical right-sized peer, per dollar of sales?","required_primitives":["operating_income","depreciation_amortisation","revenue"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"ebitda_growth","key":"ebitda_growth","measured":{"company_joint_2024":0.5096,"independence_product_2024":0.2632,"level_cohort_for_comparison":{"median":13,"pct_ge_3":95.2},"note":"MEASURED over two CONSECUTIVE annual observations, not assumed to be the square of the one-period rate -- the square would have said 90.6% where the truth is 94.0%, because a filer that reports EBITDA once mostly reports it every year.","vector":{"max":651,"mean":34.74,"median":12,"p25":8,"p75":23,"pct_ge_12":54.0,"pct_ge_3":94.01}},"member_quantity":"ebitda_growth","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"zero_anchored_tanh_v1","note":"ZERO_ANCHORED, not ranked: growth has a true zero and a rank hands out +100s in a cohort where everyone shrank.","peer_eligibility":{"lags":[0,1],"matched_period":["operating_income","depreciation_amortisation"],"positive_screen":"","price_conditional_terms":[],"primitives":["operating_income","depreciation_amortisation"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"ebitda_t_and_t1_v1","unavailable_primitives":[],"value_band":null,"why":"EBITDA at TWO observation dates a year apart. Two unique leaves at two lags = four observations, and the cohort is the peers that have all four."},"question":"Is the earnings power larger than it was a year ago?","required_primitives":["operating_income","depreciation_amortisation"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"ebitda_acceleration","key":"ebitda_acceleration","measured":{"company_joint_2024":0.4832,"distinct_ebitda_observations":3,"if_the_shared_observation_were_charged_twice":{"median":9,"pct_ge_12":39.22,"pct_ge_3":77.97},"independence_product_2024":0.135,"note":"the second row is the COST OF THE MISTAKE, measured: 2 members of median cohort size and 8.1 points of formation rate. See OBSERVATION_DEPTH.","vector":{"max":568,"mean":30.27,"median":11,"p25":6,"p75":20,"pct_ge_12":47.44,"pct_ge_3":86.03}},"member_quantity":"ebitda_acceleration","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"zero_anchored_tanh_v1","note":"THREE EBITDA observations, not four. The eligibility rule says so in `lags`, pit_derive.leaves() says so in the flattened leaf set, and observation_lags() reads it off that set.","peer_eligibility":{"lags":[0,1,2],"matched_period":["operating_income","depreciation_amortisation"],"positive_screen":"","price_conditional_terms":[],"primitives":["operating_income","depreciation_amortisation"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"ebitda_t_t1_t2_v1","unavailable_primitives":[],"value_band":null,"why":"EBITDA at THREE observation dates: E_t, E_t-1, E_t-2. Acceleration is growth(t) minus growth(t-1) and the two growths SHARE E_t-1, so the requirement is three observations and not four. A coverage model that multiplied two growth coverages would charge for the shared middle observation twice."},"question":"Is the earnings power growing FASTER than it was?","required_primitives":["operating_income","depreciation_amortisation"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"real_revenue_growth","key":"real_revenue_growth","measured":{"revenue_vector":{"median":19,"pct_ge_12":82.0,"pct_ge_3":98.8},"two_period_joint":"UNKNOWN -- one-period availability only"},"member_quantity":"real_revenue_growth","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"zero_anchored_tanh_v1","note":"","peer_eligibility":{"lags":[0,1],"matched_period":[],"positive_screen":"","price_conditional_terms":[],"primitives":["revenue","cpi"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"revenue_two_periods_plus_cpi_v1","unavailable_primitives":[],"value_band":null,"why":"Revenue at two observation dates a year apart, each non-stale, plus the CPI VINTAGE available on the as-of date -- the deflator is taken over the growth window, not as trailing CPI at the as-of, because that error is correlated with the fiscal calendar and mis-scores the 28% of the universe that is not a December filer. CPI is 100% available on every grid date."},"question":"Did the business sell MORE, or just charge more?","required_primitives":["revenue","cpi"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"admissible_rungs":["sic4","sic3","sic2"],"fallback_rung":["sic4","sic3","sic2","office"],"forbidden_rungs":["office"],"graph_divergence":"","graph_node":"cohort_mean_ev_ebitda","key":"ev_ebitda","max_admissible_rung":"sic2","measured":{"binding_leaf":"shares_outstanding (defensible)","leave_one_out_pct_ge_3":{"all_five":34.2,"without_cash":35.0,"without_defensible_shares":61.6,"without_ebitda":46.8,"without_total_debt":51.2},"seasonality_pct_ge_3":{"april":40.6,"january":14.5,"october":19.1},"sic4_rung":{"median":1,"pct_ge_12":2.4,"pct_ge_3":22.9},"vector":{"max":95,"mean":4.0,"median":1,"p25":0,"p75":3,"p90":8,"pct_ge_12":7.0,"pct_ge_3":34.2,"pct_ge_5":18.6,"pct_ge_8":11.5}},"member_quantity":"ev_ebitda","min_band_members":3,"min_peer_count":12,"min_peer_why":"the benchmark is the MEAN OF THE 50-75 BAND, and the band is a quarter of the vector by construction, so three members inside the band needs twelve in the vector. 3 is the floor for the percentiles themselves (company_scoring.MIN_EBITDA_COHORT).","missingness_behaviour":"drop_factor_and_renormalise_major_weights","model_version":"candidate_equity_shaffer_v2","normalization_type":"benchmark_anchored_tanh_v1","note":"Infeasible for most of the cross-section, and the 40% weight drops out with it -- so the Shaffer equity model IS a 0.25G+0.20P+0.15D model (renormalised 0.4167/0.3333/0.25) for the majority of company-dates. Known BEFORE the replay. factor_spec_v2: the office rung is FORBIDDEN for this factor. Sufficient N and economic coherence are two INDEPENDENT tests and they move in opposite directions along the SIC ladder -- measured, the office rung clears 12 members 94.5% of the time and does it with cohorts of 542-1,310 issuers who share an SEC reviewer and nothing else, while sic4 clears 12 for 2.4% of peer sets. More observations do not help when the economic relationship has become meaningless, so the honest answer when no admissible rung forms a cohort is VALUATION_PEER_SET_INSUFFICIENT, which makes the score PARTIAL and not comparable with a full one (pit_score_signature.PARTIAL_SHAFFER_SCORE). The office rung remains available for BROAD DESCRIPTIVE STATISTICS; what it may not be is a company valuation peer group.","peer_eligibility":{"lags":[0],"matched_period":["operating_income","depreciation_amortisation"],"positive_screen":"ebitda > 0 and enterprise_value > 0","price_conditional_terms":["cash","price_adjusted","requires_defensible_shares","requires_price","shares_outstanding","total_debt","value_band[ev_ebitda]"],"primitives":["price_adjusted","shares_outstanding","total_debt","cash","operating_income","depreciation_amortisation"],"requires_defensible_shares":true,"requires_price":true,"rule_id":"ev_ebitda_six_primitives_v1","unavailable_primitives":[],"value_band":["ev_ebitda",0.1,300.0],"why":"A resolvable point-in-time price (pit_identity.scored_universe_as_of: a listing valid on the date, a bar with volume > 0 within 10 days, no quarantine), a DEFENSIBLE share count (pit_rawprice.class_decision under the strict policy), total_debt on concept_ladder_v2, cash, and EBITDA -- then the [0.1, 300] validity band on the resulting multiple. Six primitives and two value screens. This rule is CORRECT for a valuation factor and is exactly what does not belong anywhere near the EBITDA benchmark."},"question":"What multiple is the market paying for a right-sized peer\'s earnings?","required_primitives":["price_adjusted","shares_outstanding","total_debt","cash","operating_income","depreciation_amortisation"],"sample_scope":"SURVIVOR_ONLY_DIAGNOSTIC","spec_version":"factor_spec_v2"},{"admissible_rungs":["sic4","sic3","sic2"],"fallback_rung":["sic4","sic3","sic2","office"],"forbidden_rungs":["office"],"graph_divergence":"the graph computes pe_ratio as market_cap / net_income, so its leaves are price, SHARES and net income -- three. A filed EPS already carries the per-share division, so the spec needs price and EPS and NO share count. That is the whole reason P/E is feasible where EV/EBITDA is not: the binding leaf of the valuation chain is the defensible share count, and this factor does not touch it.","graph_node":"pe_ratio","key":"pe_ratio","max_admissible_rung":"sic2","measured":{"eps_rows_in_store":0,"proxy_note":"measured with the net_income ladder standing in for EPS -- same filers, same periods, same filings. An UPPER bound: P/E is undefined for a loss-maker and ~70% of resolvable earnings are positive.","proxy_vector":{"median":9,"pct_ge_12":36.0,"pct_ge_3":92.0},"recovery_priced_universe":{"minutes":8.4,"requests":5066,"retained_gib":0.529}},"member_quantity":"pe_ratio","min_band_members":0,"min_peer_count":3,"min_peer_why":"a cohort MEAN, not a percentile: the core\'s floor of 3 applies.","missingness_behaviour":"drop_factor_and_renormalise_major_weights","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"2.7x EV/EBITDA\'s 3-peer formation rate, 5.1x at the 12-peer bar, and FLAT across the calendar (91.6-92.3% every month) because net income carries the 15-month annual staleness bound rather than the share count\'s 4-month one. factor_spec_v2: the office rung is FORBIDDEN for this factor. Sufficient N and economic coherence are two INDEPENDENT tests and they move in opposite directions along the SIC ladder -- measured, the office rung clears 12 members 94.5% of the time and does it with cohorts of 542-1,310 issuers who share an SEC reviewer and nothing else, while sic4 clears 12 for 2.4% of peer sets. More observations do not help when the economic relationship has become meaningless, so the honest answer when no admissible rung forms a cohort is VALUATION_PEER_SET_INSUFFICIENT, which makes the score PARTIAL and not comparable with a full one (pit_score_signature.PARTIAL_SHAFFER_SCORE). The office rung remains available for BROAD DESCRIPTIVE STATISTICS; what it may not be is a company valuation peer group. factor_spec_v3: eligibility PRICED_EPS_V2 -- EPS is in the store and is no longer declared GENUINELY_UNAVAILABLE.","peer_eligibility":{"lags":[0],"matched_period":[],"positive_screen":"earnings_per_share_pit > 0","price_conditional_terms":["earnings_per_share_pit","price_adjusted","requires_price"],"primitives":["price_adjusted","earnings_per_share_pit"],"requires_defensible_shares":false,"requires_price":true,"rule_id":"price_and_pit_eps_v2","unavailable_primitives":[],"value_band":null,"why":"A resolvable point-in-time price and a point-in-time EPS. NO SHARE COUNT: the per-share division is already inside the filed EPS figure, which is why this chain is two primitives where EV/EBITDA is six. EPS IS IN THIS STORE as of the pit_eps ingest: pit_eps_obs 888,486 rows, pit_eps_ttm 147,914, 1,962 of the 7,102 peers on 2019-06-28 with a TTM knowable on the date. The v1/v2 rule declared it GENUINELY_UNAVAILABLE and that declaration is now false; it is corrected here rather than bypassed in an engine."},"question":"How many years of current earnings is the market paying?","required_primitives":["price_adjusted","earnings_per_share_pit"],"sample_scope":"SURVIVOR_ONLY_DIAGNOSTIC","spec_version":"factor_spec_v2"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"net_debt_ebitda","key":"net_debt_ebitda","measured":{"cash_vector":{"median":20,"pct_ge_3":99.9},"debt_vector":{"median":11,"pct_ge_12":45.2,"pct_ge_3":95.0}},"member_quantity":"net_debt_ebitda","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"drop_factor_and_renormalise_major_weights","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"Lower-is-better, and 94% of resolved total_debt sits on a concept_ladder_v2 LOWER-BOUND rung -- which FLATTERS the score. A percentile over that mixture must report the rung distribution beside it (pit_policy\'s v2 consumer rule).","peer_eligibility":{"lags":[0],"matched_period":["operating_income","depreciation_amortisation"],"positive_screen":"","price_conditional_terms":["cash","total_debt"],"primitives":["total_debt","cash","operating_income","depreciation_amortisation"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"net_debt_over_ebitda_v1","unavailable_primitives":[],"value_band":null,"why":"Leverage against earning power. Debt and cash are REQUIRED here because they are what the factor measures -- which is the whole distinction: a primitive belongs in a cohort\'s filter when the factor is ABOUT it, never because a neighbouring factor needed it. Price-free: this leverage ratio needs no market value at all."},"question":"How many years of earnings would clear the debt?","required_primitives":["total_debt","cash","operating_income","depreciation_amortisation"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"debt_market_cap","key":"debt_market_cap","measured":{"price_shares_vector":{"median":4,"pct_ge_12":19.5,"pct_ge_3":69.7}},"member_quantity":"debt_market_cap","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"drop_factor_and_renormalise_major_weights","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"","peer_eligibility":{"lags":[0],"matched_period":[],"positive_screen":"market_cap > 0","price_conditional_terms":["price_adjusted","requires_defensible_shares","requires_price","shares_outstanding","total_debt"],"primitives":["total_debt","price_adjusted","shares_outstanding"],"requires_defensible_shares":true,"requires_price":true,"rule_id":"debt_over_market_cap_v1","unavailable_primitives":[],"value_band":null,"why":"Leverage against MARKET value, so a price and a defensible share count are the factor\'s own subject matter. SURVIVOR_ONLY by construction."},"question":"How much leverage sits under each dollar of equity value?","required_primitives":["total_debt","price_adjusted","shares_outstanding"],"sample_scope":"SURVIVOR_ONLY_DIAGNOSTIC","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"roa","key":"roa","measured":{"net_income_marginal":[0.951,0.966,0.978],"total_assets_marginal":[1.0,1.0,1.0]},"member_quantity":"roa","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"","peer_eligibility":{"lags":[0],"matched_period":[],"positive_screen":"","price_conditional_terms":[],"primitives":["net_income","total_assets"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"net_income_and_total_assets_v1","unavailable_primitives":[],"value_band":null,"why":"Return on assets. Both are near-universal in the store (95.1-97.8% and 100% of base at the three census dates), which is why the quality legs survive where valuation does not."},"question":"How much profit does each dollar of assets generate?","required_primitives":["net_income","total_assets"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"roe","key":"roe","measured":{"equity_marginal":[0.947,0.963,0.975]},"member_quantity":"roe","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"","peer_eligibility":{"lags":[0],"matched_period":[],"positive_screen":"","price_conditional_terms":[],"primitives":["net_income","equity"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"net_income_and_equity_v1","unavailable_primitives":[],"value_band":null,"why":"Return on shareholder capital. StockholdersEquity leads the ladder; the NCI-inclusive tag is a DIFFERENT denominator and is a fallback."},"question":"How much profit does each dollar of shareholder capital generate?","required_primitives":["net_income","equity"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"interest_coverage","key":"interest_coverage","measured":{"interest_expense_marginal":[0.638,0.607,0.558],"ladder_measured":false},"member_quantity":"interest_coverage","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"","peer_eligibility":{"lags":[0],"matched_period":[],"positive_screen":"","price_conditional_terms":[],"primitives":["operating_income","interest_expense"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"operating_income_and_interest_v1","unavailable_primitives":[],"value_band":null,"why":"Interest cover. The interest_expense ladder is AUTHORED, NOT MEASURED (pit_policy marks it measured=False), so no coverage claim may be quoted for this cohort from the ladder -- only the measured marginal, 63.8/60.7/55.8% of base."},"question":"How many times over can the business pay its interest bill?","required_primitives":["operating_income","interest_expense"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"fcf_conversion","key":"fcf_conversion","measured":{"capex_marginal":[0.675,0.711,0.694],"ocf_marginal":[0.942,0.961,0.973]},"member_quantity":"fcf_conversion","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"","peer_eligibility":{"lags":[0],"matched_period":["operating_income","depreciation_amortisation"],"positive_screen":"","price_conditional_terms":[],"primitives":["operating_cash_flow","capex","operating_income","depreciation_amortisation"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"fcf_over_ebitda_v1","unavailable_primitives":[],"value_band":null,"why":"How much of the accounting earnings turns into spendable cash. Price-free. capex is the binding leaf of the pair at 67.5/71.1/69.4% of base against operating cash flow\'s 94.2/96.1/97.3%."},"question":"How much of the accounting earnings turns into spendable cash?","required_primitives":["operating_cash_flow","capex","operating_income","depreciation_amortisation"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"}]',
    'pit_factor_spec:SPECS_V4':
        '[{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"SCORED QUANTITY DIFFERS FROM pit_derive\'s cohort_mean_ebitda node: the graph node is a DOLLAR mean; this spec scores the MARGIN excess against the cohort\'s mean margin (pit_normalization.benchmark_margin_50_75). The cohort selection quantity is still EBITDA dollars. FACTOR_SPEC_V2_DOLLAR_BENCHMARK_AND_STALE_EPS_DECLARATION: factor_spec_v2 compares EBITDA in DOLLARS against the 50-75 cohort (a size factor the design record had already rejected) and declares point-in-time EPS unavailable although pit_eps_obs holds 888,486 rows. factor_spec_v3 corrects both. No main-store row was ever produced under v2.","graph_node":"cohort_mean_ebitda","key":"ebitda_benchmark","measured":{"band_gate_lifted":{"max":179,"mean":9.18,"median":3,"p10":1,"p25":2,"p75":6,"p90":18,"pct_ge_3":68.34},"benchmark_formed_pct":56.0,"under_the_price_conditional_rule":{"band_gate_lifted_median":1,"band_pct_ge_3":7.56,"benchmark_formed_pct":5.71,"vector_median":1,"vector_pct_ge_12":5.71},"vector":{"max":718,"mean":36.04,"median":13,"p10":5,"p25":8,"p75":23,"p90":70,"pct_ge_12":56.0,"pct_ge_3":95.2}},"member_quantity":"ebitda","min_band_members":3,"min_peer_count":12,"min_peer_why":"the benchmark is the MEAN OF THE 50-75 BAND, and the band is a quarter of the vector by construction, so three members inside the band needs twelve in the vector. 3 is the floor for the percentiles themselves (company_scoring.MIN_EBITDA_COHORT).","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"benchmark_anchored_tanh_v1","note":"SHAFFER_V1_KNOWN_LIMITATION: company_scoring.build_ebitda_peer_cohort applies EV_EBITDA_SIX here. Measured, that turns a 56.0% benchmark-formation rate into 5.7% -- the benchmark is unavailable for 94% of peer sets because of a filter that has nothing to do with the quantity it measures. factor_spec_v3: SCORED QUANTITY IS THE EBITDA MARGIN, cohort selection is unchanged. Select the 50th-75th percentile cohort by peer EBITDA dollars (member_quantity=\'ebitda\', as before); then compare EBITDA_i/Revenue_i against the cohort\'s mean margin, BENCHMARK_ANCHORED. Owner decision 2026-09-22. The dollar reading is FACTOR_SPEC_V2_KNOWN_LIMITATION.","peer_eligibility":{"lags":[0],"matched_period":["operating_income","depreciation_amortisation","revenue"],"positive_screen":"","price_conditional_terms":[],"primitives":["operating_income","depreciation_amortisation","revenue"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"ebitda_and_revenue_matched_v1","unavailable_primitives":[],"value_band":null,"why":"The EBITDA cohort, further restricted to members whose revenue resolves at the SAME (period_end, qtrs). A margin is a ratio of one period\'s numbers. MEASURED: the matched-period rule costs 0.1 pp against selecting revenue independently, so it is free -- enforce it."},"question":"What does a right-sized peer in this sector earn?","required_primitives":["operating_income","depreciation_amortisation","revenue"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"ebitda_scale divides by sector_ebitda_p50, which the graph builds from sector_ebitda_vector -- the PRICED sector\'s percentiles. The same contamination reached through a percentile instead of a band mean. SHAFFER_V1_KNOWN_LIMITATION","graph_node":"ebitda_scale","key":"ebitda_scale","measured":{"vector":{"median":13,"pct_ge_3":95.2}},"member_quantity":"ebitda","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"Shares EBITDA_ONLY with ebitda_benchmark BY REFERENCE, not by copy: two factors that must mean the same cohort cannot drift apart if they are the same object. Only the threshold differs -- a rank needs 3, a band needs 12.","peer_eligibility":{"lags":[0],"matched_period":["operating_income","depreciation_amortisation"],"positive_screen":"","price_conditional_terms":[],"primitives":["operating_income","depreciation_amortisation"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"ebitda_only_v1","unavailable_primitives":[],"value_band":null,"why":"Members of the peer set whose EBITDA resolves: operating_income AND depreciation_amortisation at a MATCHED (period_end, qtrs), each non-stale under pit_policy.is_stale, each unit=\'USD\'. NOTHING ELSE. No price, no share count, no debt, no cash. This is the sector\'s distribution of earning power, and conditioning it on tradeability would make it the PRICED sector\'s distribution -- which is what the frozen core does, and what costs it 12 members of median cohort size."},"question":"How many median peers would it take to make this company?","required_primitives":["operating_income","depreciation_amortisation"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"ebitda_margin_rank","key":"ebitda_efficiency","measured":{"matched_period_cost_pp":0.1,"vector":{"max":699,"median":12,"p10":4,"p25":8,"p75":22,"p90":66,"pct_ge_12":52.8,"pct_ge_3":94.5}},"member_quantity":"ebitda_margin","min_band_members":0,"min_peer_count":3,"min_peer_why":"a cohort MEAN, not a percentile: the core\'s floor of 3 applies.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"","peer_eligibility":{"lags":[0],"matched_period":["operating_income","depreciation_amortisation","revenue"],"positive_screen":"","price_conditional_terms":[],"primitives":["operating_income","depreciation_amortisation","revenue"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"ebitda_and_revenue_matched_v1","unavailable_primitives":[],"value_band":null,"why":"The EBITDA cohort, further restricted to members whose revenue resolves at the SAME (period_end, qtrs). A margin is a ratio of one period\'s numbers. MEASURED: the matched-period rule costs 0.1 pp against selecting revenue independently, so it is free -- enforce it."},"question":"How profitable is a typical right-sized peer, per dollar of sales?","required_primitives":["operating_income","depreciation_amortisation","revenue"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"ebitda_growth","key":"ebitda_growth","measured":{"company_joint_2024":0.5096,"independence_product_2024":0.2632,"level_cohort_for_comparison":{"median":13,"pct_ge_3":95.2},"note":"MEASURED over two CONSECUTIVE annual observations, not assumed to be the square of the one-period rate -- the square would have said 90.6% where the truth is 94.0%, because a filer that reports EBITDA once mostly reports it every year.","vector":{"max":651,"mean":34.74,"median":12,"p25":8,"p75":23,"pct_ge_12":54.0,"pct_ge_3":94.01}},"member_quantity":"ebitda_growth","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"zero_anchored_tanh_v1","note":"ZERO_ANCHORED, not ranked: growth has a true zero and a rank hands out +100s in a cohort where everyone shrank. factor_spec_v4: EXECUTABLE DEFINITION (E_t - E_t-1) / abs(E_t-1) under EBITDA_GROWTH_BASE_POLICY_V1; no asset base. Owner decision 2026-09-22 (D3, A).","peer_eligibility":{"lags":[0,1],"matched_period":["operating_income","depreciation_amortisation"],"positive_screen":"","price_conditional_terms":[],"primitives":["operating_income","depreciation_amortisation"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"ebitda_t_and_t1_v1","unavailable_primitives":[],"value_band":null,"why":"EBITDA at TWO observation dates a year apart. Two unique leaves at two lags = four observations, and the cohort is the peers that have all four."},"question":"Is the earnings power larger than it was a year ago?","required_primitives":["operating_income","depreciation_amortisation"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"ebitda_acceleration","key":"ebitda_acceleration","measured":{"company_joint_2024":0.4832,"distinct_ebitda_observations":3,"if_the_shared_observation_were_charged_twice":{"median":9,"pct_ge_12":39.22,"pct_ge_3":77.97},"independence_product_2024":0.135,"note":"the second row is the COST OF THE MISTAKE, measured: 2 members of median cohort size and 8.1 points of formation rate. See OBSERVATION_DEPTH.","vector":{"max":568,"mean":30.27,"median":11,"p25":6,"p75":20,"pct_ge_12":47.44,"pct_ge_3":86.03}},"member_quantity":"ebitda_acceleration","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"zero_anchored_tanh_v1","note":"THREE EBITDA observations, not four. The eligibility rule says so in `lags`, pit_derive.leaves() says so in the flattened leaf set, and observation_lags() reads it off that set. factor_spec_v4: EXECUTABLE DEFINITION growth_t - growth_t-1, each an A-rate under EBITDA_GROWTH_BASE_POLICY_V1; three EBITDA observations, no asset base. Owner decision 2026-09-22.","peer_eligibility":{"lags":[0,1,2],"matched_period":["operating_income","depreciation_amortisation"],"positive_screen":"","price_conditional_terms":[],"primitives":["operating_income","depreciation_amortisation"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"ebitda_t_t1_t2_v1","unavailable_primitives":[],"value_band":null,"why":"EBITDA at THREE observation dates: E_t, E_t-1, E_t-2. Acceleration is growth(t) minus growth(t-1) and the two growths SHARE E_t-1, so the requirement is three observations and not four. A coverage model that multiplied two growth coverages would charge for the shared middle observation twice."},"question":"Is the earnings power growing FASTER than it was?","required_primitives":["operating_income","depreciation_amortisation"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"real_revenue_growth","key":"real_revenue_growth","measured":{"revenue_vector":{"median":19,"pct_ge_12":82.0,"pct_ge_3":98.8},"two_period_joint":"UNKNOWN -- one-period availability only"},"member_quantity":"real_revenue_growth","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"zero_anchored_tanh_v1","note":"","peer_eligibility":{"lags":[0,1],"matched_period":[],"positive_screen":"","price_conditional_terms":[],"primitives":["revenue","cpi"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"revenue_two_periods_plus_cpi_v1","unavailable_primitives":[],"value_band":null,"why":"Revenue at two observation dates a year apart, each non-stale, plus the CPI VINTAGE available on the as-of date -- the deflator is taken over the growth window, not as trailing CPI at the as-of, because that error is correlated with the fiscal calendar and mis-scores the 28% of the universe that is not a December filer. CPI is 100% available on every grid date."},"question":"Did the business sell MORE, or just charge more?","required_primitives":["revenue","cpi"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"admissible_rungs":["sic4","sic3","sic2"],"fallback_rung":["sic4","sic3","sic2","office"],"forbidden_rungs":["office"],"graph_divergence":"","graph_node":"cohort_mean_ev_ebitda","key":"ev_ebitda","max_admissible_rung":"sic2","measured":{"binding_leaf":"shares_outstanding (defensible)","leave_one_out_pct_ge_3":{"all_five":34.2,"without_cash":35.0,"without_defensible_shares":61.6,"without_ebitda":46.8,"without_total_debt":51.2},"seasonality_pct_ge_3":{"april":40.6,"january":14.5,"october":19.1},"sic4_rung":{"median":1,"pct_ge_12":2.4,"pct_ge_3":22.9},"vector":{"max":95,"mean":4.0,"median":1,"p25":0,"p75":3,"p90":8,"pct_ge_12":7.0,"pct_ge_3":34.2,"pct_ge_5":18.6,"pct_ge_8":11.5}},"member_quantity":"ev_ebitda","min_band_members":3,"min_peer_count":12,"min_peer_why":"the benchmark is the MEAN OF THE 50-75 BAND, and the band is a quarter of the vector by construction, so three members inside the band needs twelve in the vector. 3 is the floor for the percentiles themselves (company_scoring.MIN_EBITDA_COHORT).","missingness_behaviour":"drop_factor_and_renormalise_major_weights","model_version":"candidate_equity_shaffer_v2","normalization_type":"benchmark_anchored_tanh_v1","note":"Infeasible for most of the cross-section, and the 40% weight drops out with it -- so the Shaffer equity model IS a 0.25G+0.20P+0.15D model (renormalised 0.4167/0.3333/0.25) for the majority of company-dates. Known BEFORE the replay. factor_spec_v2: the office rung is FORBIDDEN for this factor. Sufficient N and economic coherence are two INDEPENDENT tests and they move in opposite directions along the SIC ladder -- measured, the office rung clears 12 members 94.5% of the time and does it with cohorts of 542-1,310 issuers who share an SEC reviewer and nothing else, while sic4 clears 12 for 2.4% of peer sets. More observations do not help when the economic relationship has become meaningless, so the honest answer when no admissible rung forms a cohort is VALUATION_PEER_SET_INSUFFICIENT, which makes the score PARTIAL and not comparable with a full one (pit_score_signature.PARTIAL_SHAFFER_SCORE). The office rung remains available for BROAD DESCRIPTIVE STATISTICS; what it may not be is a company valuation peer group.","peer_eligibility":{"lags":[0],"matched_period":["operating_income","depreciation_amortisation"],"positive_screen":"ebitda > 0 and enterprise_value > 0","price_conditional_terms":["cash","price_adjusted","requires_defensible_shares","requires_price","shares_outstanding","total_debt","value_band[ev_ebitda]"],"primitives":["price_adjusted","shares_outstanding","total_debt","cash","operating_income","depreciation_amortisation"],"requires_defensible_shares":true,"requires_price":true,"rule_id":"ev_ebitda_six_primitives_v1","unavailable_primitives":[],"value_band":["ev_ebitda",0.1,300.0],"why":"A resolvable point-in-time price (pit_identity.scored_universe_as_of: a listing valid on the date, a bar with volume > 0 within 10 days, no quarantine), a DEFENSIBLE share count (pit_rawprice.class_decision under the strict policy), total_debt on concept_ladder_v2, cash, and EBITDA -- then the [0.1, 300] validity band on the resulting multiple. Six primitives and two value screens. This rule is CORRECT for a valuation factor and is exactly what does not belong anywhere near the EBITDA benchmark."},"question":"What multiple is the market paying for a right-sized peer\'s earnings?","required_primitives":["price_adjusted","shares_outstanding","total_debt","cash","operating_income","depreciation_amortisation"],"sample_scope":"SURVIVOR_ONLY_DIAGNOSTIC","spec_version":"factor_spec_v2"},{"admissible_rungs":["sic4","sic3","sic2"],"fallback_rung":["sic4","sic3","sic2","office"],"forbidden_rungs":["office"],"graph_divergence":"the graph computes pe_ratio as market_cap / net_income, so its leaves are price, SHARES and net income -- three. A filed EPS already carries the per-share division, so the spec needs price and EPS and NO share count. That is the whole reason P/E is feasible where EV/EBITDA is not: the binding leaf of the valuation chain is the defensible share count, and this factor does not touch it.","graph_node":"pe_ratio","key":"pe_ratio","max_admissible_rung":"sic2","measured":{"eps_rows_in_store":0,"proxy_note":"measured with the net_income ladder standing in for EPS -- same filers, same periods, same filings. An UPPER bound: P/E is undefined for a loss-maker and ~70% of resolvable earnings are positive.","proxy_vector":{"median":9,"pct_ge_12":36.0,"pct_ge_3":92.0},"recovery_priced_universe":{"minutes":8.4,"requests":5066,"retained_gib":0.529}},"member_quantity":"pe_ratio","min_band_members":0,"min_peer_count":3,"min_peer_why":"a cohort MEAN, not a percentile: the core\'s floor of 3 applies.","missingness_behaviour":"drop_factor_and_renormalise_major_weights","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"2.7x EV/EBITDA\'s 3-peer formation rate, 5.1x at the 12-peer bar, and FLAT across the calendar (91.6-92.3% every month) because net income carries the 15-month annual staleness bound rather than the share count\'s 4-month one. factor_spec_v2: the office rung is FORBIDDEN for this factor. Sufficient N and economic coherence are two INDEPENDENT tests and they move in opposite directions along the SIC ladder -- measured, the office rung clears 12 members 94.5% of the time and does it with cohorts of 542-1,310 issuers who share an SEC reviewer and nothing else, while sic4 clears 12 for 2.4% of peer sets. More observations do not help when the economic relationship has become meaningless, so the honest answer when no admissible rung forms a cohort is VALUATION_PEER_SET_INSUFFICIENT, which makes the score PARTIAL and not comparable with a full one (pit_score_signature.PARTIAL_SHAFFER_SCORE). The office rung remains available for BROAD DESCRIPTIVE STATISTICS; what it may not be is a company valuation peer group. factor_spec_v3: eligibility PRICED_EPS_V2 -- EPS is in the store and is no longer declared GENUINELY_UNAVAILABLE.","peer_eligibility":{"lags":[0],"matched_period":[],"positive_screen":"earnings_per_share_pit > 0","price_conditional_terms":["earnings_per_share_pit","price_adjusted","requires_price"],"primitives":["price_adjusted","earnings_per_share_pit"],"requires_defensible_shares":false,"requires_price":true,"rule_id":"price_and_pit_eps_v2","unavailable_primitives":[],"value_band":null,"why":"A resolvable point-in-time price and a point-in-time EPS. NO SHARE COUNT: the per-share division is already inside the filed EPS figure, which is why this chain is two primitives where EV/EBITDA is six. EPS IS IN THIS STORE as of the pit_eps ingest: pit_eps_obs 888,486 rows, pit_eps_ttm 147,914, 1,962 of the 7,102 peers on 2019-06-28 with a TTM knowable on the date. The v1/v2 rule declared it GENUINELY_UNAVAILABLE and that declaration is now false; it is corrected here rather than bypassed in an engine."},"question":"How many years of current earnings is the market paying?","required_primitives":["price_adjusted","earnings_per_share_pit"],"sample_scope":"SURVIVOR_ONLY_DIAGNOSTIC","spec_version":"factor_spec_v2"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"net_debt_ebitda","key":"net_debt_ebitda","measured":{"cash_vector":{"median":20,"pct_ge_3":99.9},"debt_vector":{"median":11,"pct_ge_12":45.2,"pct_ge_3":95.0}},"member_quantity":"net_debt_ebitda","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"drop_factor_and_renormalise_major_weights","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"Lower-is-better, and 94% of resolved total_debt sits on a concept_ladder_v2 LOWER-BOUND rung -- which FLATTERS the score. A percentile over that mixture must report the rung distribution beside it (pit_policy\'s v2 consumer rule).","peer_eligibility":{"lags":[0],"matched_period":["operating_income","depreciation_amortisation"],"positive_screen":"","price_conditional_terms":["cash","total_debt"],"primitives":["total_debt","cash","operating_income","depreciation_amortisation"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"net_debt_over_ebitda_v1","unavailable_primitives":[],"value_band":null,"why":"Leverage against earning power. Debt and cash are REQUIRED here because they are what the factor measures -- which is the whole distinction: a primitive belongs in a cohort\'s filter when the factor is ABOUT it, never because a neighbouring factor needed it. Price-free: this leverage ratio needs no market value at all."},"question":"How many years of earnings would clear the debt?","required_primitives":["total_debt","cash","operating_income","depreciation_amortisation"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"debt_market_cap","key":"debt_market_cap","measured":{"price_shares_vector":{"median":4,"pct_ge_12":19.5,"pct_ge_3":69.7}},"member_quantity":"debt_market_cap","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"drop_factor_and_renormalise_major_weights","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"","peer_eligibility":{"lags":[0],"matched_period":[],"positive_screen":"market_cap > 0","price_conditional_terms":["price_adjusted","requires_defensible_shares","requires_price","shares_outstanding","total_debt"],"primitives":["total_debt","price_adjusted","shares_outstanding"],"requires_defensible_shares":true,"requires_price":true,"rule_id":"debt_over_market_cap_v1","unavailable_primitives":[],"value_band":null,"why":"Leverage against MARKET value, so a price and a defensible share count are the factor\'s own subject matter. SURVIVOR_ONLY by construction."},"question":"How much leverage sits under each dollar of equity value?","required_primitives":["total_debt","price_adjusted","shares_outstanding"],"sample_scope":"SURVIVOR_ONLY_DIAGNOSTIC","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"roa","key":"roa","measured":{"net_income_marginal":[0.951,0.966,0.978],"total_assets_marginal":[1.0,1.0,1.0]},"member_quantity":"roa","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"","peer_eligibility":{"lags":[0],"matched_period":[],"positive_screen":"","price_conditional_terms":[],"primitives":["net_income","total_assets"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"net_income_and_total_assets_v1","unavailable_primitives":[],"value_band":null,"why":"Return on assets. Both are near-universal in the store (95.1-97.8% and 100% of base at the three census dates), which is why the quality legs survive where valuation does not."},"question":"How much profit does each dollar of assets generate?","required_primitives":["net_income","total_assets"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"roe","key":"roe","measured":{"equity_marginal":[0.947,0.963,0.975]},"member_quantity":"roe","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"","peer_eligibility":{"lags":[0],"matched_period":[],"positive_screen":"","price_conditional_terms":[],"primitives":["net_income","equity"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"net_income_and_equity_v1","unavailable_primitives":[],"value_band":null,"why":"Return on shareholder capital. StockholdersEquity leads the ladder; the NCI-inclusive tag is a DIFFERENT denominator and is a fallback."},"question":"How much profit does each dollar of shareholder capital generate?","required_primitives":["net_income","equity"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"interest_coverage","key":"interest_coverage","measured":{"interest_expense_marginal":[0.638,0.607,0.558],"ladder_measured":false},"member_quantity":"interest_coverage","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"factor_spec_v4: numerator OPERATING INCOME (owner decision 2026-09-22, D3 A); eligibility OPERATING_INCOME_AND_INTEREST_MATCHED_V2; domain INTEREST_COVERAGE_DOMAIN_V1.","peer_eligibility":{"lags":[0],"matched_period":["operating_income","interest_expense"],"positive_screen":"interest_expense > 0","price_conditional_terms":[],"primitives":["operating_income","interest_expense"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"operating_income_and_interest_matched_v2","unavailable_primitives":[],"value_band":null,"why":"Interest cover = operating_income / interest_expense, owner decision 2026-09-22. Both at ONE (period_end, qtrs=4): a ratio of one period\'s numbers. DOMAIN (INTEREST_COVERAGE_DOMAIN_V1, executed in pit_replay.resolve_primitives; this rule counts availability only): interest_expense > 0 is the ordinary case; a tagged zero is interest_expense_zero, a negative value interest_expense_negative, an untagged one no_interest_expense_at_operating_income_period -- never a large \'excellent\' ratio. Negative operating income is a legitimate NEGATIVE coverage and is scored. Ladder still AUTHORED, NOT MEASURED; only the measured marginal 63.8/60.7/55.8% of base may be quoted. UPPER BOUND: this rule accepts any non-stale shared period; the engine values only the newest operating-income period, so n_eligible - n_values on the cohort record measures the gap."},"question":"How many times over can the business pay its interest bill?","required_primitives":["operating_income","interest_expense"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"fcf_conversion","key":"fcf_conversion","measured":{"capex_marginal":[0.675,0.711,0.694],"ocf_marginal":[0.942,0.961,0.973]},"member_quantity":"fcf_conversion","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"","peer_eligibility":{"lags":[0],"matched_period":["operating_income","depreciation_amortisation"],"positive_screen":"","price_conditional_terms":[],"primitives":["operating_cash_flow","capex","operating_income","depreciation_amortisation"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"fcf_over_ebitda_v1","unavailable_primitives":[],"value_band":null,"why":"How much of the accounting earnings turns into spendable cash. Price-free. capex is the binding leaf of the pair at 67.5/71.1/69.4% of base against operating cash flow\'s 94.2/96.1/97.3%."},"question":"How much of the accounting earnings turns into spendable cash?","required_primitives":["operating_cash_flow","capex","operating_income","depreciation_amortisation"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"}]',
    'pit_factor_spec:SPECS_V5':
        '[{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"SCORED QUANTITY DIFFERS FROM pit_derive\'s cohort_mean_ebitda node: the graph node is a DOLLAR mean; this spec scores the MARGIN excess against the cohort\'s mean margin (pit_normalization.benchmark_margin_50_75). The cohort selection quantity is still EBITDA dollars. FACTOR_SPEC_V2_DOLLAR_BENCHMARK_AND_STALE_EPS_DECLARATION: factor_spec_v2 compares EBITDA in DOLLARS against the 50-75 cohort (a size factor the design record had already rejected) and declares point-in-time EPS unavailable although pit_eps_obs holds 888,486 rows. factor_spec_v3 corrects both. No main-store row was ever produced under v2.","graph_node":"cohort_mean_ebitda","key":"ebitda_benchmark","measured":{"band_gate_lifted":{"max":179,"mean":9.18,"median":3,"p10":1,"p25":2,"p75":6,"p90":18,"pct_ge_3":68.34},"benchmark_formed_pct":56.0,"under_the_price_conditional_rule":{"band_gate_lifted_median":1,"band_pct_ge_3":7.56,"benchmark_formed_pct":5.71,"vector_median":1,"vector_pct_ge_12":5.71},"vector":{"max":718,"mean":36.04,"median":13,"p10":5,"p25":8,"p75":23,"p90":70,"pct_ge_12":56.0,"pct_ge_3":95.2}},"member_quantity":"ebitda","min_band_members":3,"min_peer_count":12,"min_peer_why":"the benchmark is the MEAN OF THE 50-75 BAND, and the band is a quarter of the vector by construction, so three members inside the band needs twelve in the vector. 3 is the floor for the percentiles themselves (company_scoring.MIN_EBITDA_COHORT).","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"benchmark_anchored_tanh_v1","note":"SHAFFER_V1_KNOWN_LIMITATION: company_scoring.build_ebitda_peer_cohort applies EV_EBITDA_SIX here. Measured, that turns a 56.0% benchmark-formation rate into 5.7% -- the benchmark is unavailable for 94% of peer sets because of a filter that has nothing to do with the quantity it measures. factor_spec_v3: SCORED QUANTITY IS THE EBITDA MARGIN, cohort selection is unchanged. Select the 50th-75th percentile cohort by peer EBITDA dollars (member_quantity=\'ebitda\', as before); then compare EBITDA_i/Revenue_i against the cohort\'s mean margin, BENCHMARK_ANCHORED. Owner decision 2026-09-22. The dollar reading is FACTOR_SPEC_V2_KNOWN_LIMITATION.","peer_eligibility":{"lags":[0],"matched_period":["operating_income","depreciation_amortisation","revenue"],"positive_screen":"","price_conditional_terms":[],"primitives":["operating_income","depreciation_amortisation","revenue"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"ebitda_and_revenue_matched_v1","unavailable_primitives":[],"value_band":null,"why":"The EBITDA cohort, further restricted to members whose revenue resolves at the SAME (period_end, qtrs). A margin is a ratio of one period\'s numbers. MEASURED: the matched-period rule costs 0.1 pp against selecting revenue independently, so it is free -- enforce it."},"question":"What does a right-sized peer in this sector earn?","required_primitives":["operating_income","depreciation_amortisation","revenue"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"ebitda_scale divides by sector_ebitda_p50, which the graph builds from sector_ebitda_vector -- the PRICED sector\'s percentiles. The same contamination reached through a percentile instead of a band mean. SHAFFER_V1_KNOWN_LIMITATION","graph_node":"ebitda_scale","key":"ebitda_scale","measured":{"vector":{"median":13,"pct_ge_3":95.2}},"member_quantity":"ebitda","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"Shares EBITDA_ONLY with ebitda_benchmark BY REFERENCE, not by copy: two factors that must mean the same cohort cannot drift apart if they are the same object. Only the threshold differs -- a rank needs 3, a band needs 12.","peer_eligibility":{"lags":[0],"matched_period":["operating_income","depreciation_amortisation"],"positive_screen":"","price_conditional_terms":[],"primitives":["operating_income","depreciation_amortisation"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"ebitda_only_v1","unavailable_primitives":[],"value_band":null,"why":"Members of the peer set whose EBITDA resolves: operating_income AND depreciation_amortisation at a MATCHED (period_end, qtrs), each non-stale under pit_policy.is_stale, each unit=\'USD\'. NOTHING ELSE. No price, no share count, no debt, no cash. This is the sector\'s distribution of earning power, and conditioning it on tradeability would make it the PRICED sector\'s distribution -- which is what the frozen core does, and what costs it 12 members of median cohort size."},"question":"How many median peers would it take to make this company?","required_primitives":["operating_income","depreciation_amortisation"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"ebitda_margin_rank","key":"ebitda_efficiency","measured":{"matched_period_cost_pp":0.1,"vector":{"max":699,"median":12,"p10":4,"p25":8,"p75":22,"p90":66,"pct_ge_12":52.8,"pct_ge_3":94.5}},"member_quantity":"ebitda_margin","min_band_members":0,"min_peer_count":3,"min_peer_why":"a cohort MEAN, not a percentile: the core\'s floor of 3 applies.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"","peer_eligibility":{"lags":[0],"matched_period":["operating_income","depreciation_amortisation","revenue"],"positive_screen":"","price_conditional_terms":[],"primitives":["operating_income","depreciation_amortisation","revenue"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"ebitda_and_revenue_matched_v1","unavailable_primitives":[],"value_band":null,"why":"The EBITDA cohort, further restricted to members whose revenue resolves at the SAME (period_end, qtrs). A margin is a ratio of one period\'s numbers. MEASURED: the matched-period rule costs 0.1 pp against selecting revenue independently, so it is free -- enforce it."},"question":"How profitable is a typical right-sized peer, per dollar of sales?","required_primitives":["operating_income","depreciation_amortisation","revenue"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"ebitda_growth","key":"ebitda_growth","measured":{"company_joint_2024":0.5096,"independence_product_2024":0.2632,"level_cohort_for_comparison":{"median":13,"pct_ge_3":95.2},"note":"MEASURED over two CONSECUTIVE annual observations, not assumed to be the square of the one-period rate -- the square would have said 90.6% where the truth is 94.0%, because a filer that reports EBITDA once mostly reports it every year.","vector":{"max":651,"mean":34.74,"median":12,"p25":8,"p75":23,"pct_ge_12":54.0,"pct_ge_3":94.01}},"member_quantity":"ebitda_growth","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"zero_anchored_tanh_v1","note":"ZERO_ANCHORED, not ranked: growth has a true zero and a rank hands out +100s in a cohort where everyone shrank. factor_spec_v4: EXECUTABLE DEFINITION (E_t - E_t-1) / abs(E_t-1) under EBITDA_GROWTH_BASE_POLICY_V1; no asset base. Owner decision 2026-09-22 (D3, A).","peer_eligibility":{"lags":[0,1],"matched_period":["operating_income","depreciation_amortisation"],"positive_screen":"","price_conditional_terms":[],"primitives":["operating_income","depreciation_amortisation"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"ebitda_t_and_t1_v1","unavailable_primitives":[],"value_band":null,"why":"EBITDA at TWO observation dates a year apart. Two unique leaves at two lags = four observations, and the cohort is the peers that have all four."},"question":"Is the earnings power larger than it was a year ago?","required_primitives":["operating_income","depreciation_amortisation"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"ebitda_acceleration","key":"ebitda_acceleration","measured":{"company_joint_2024":0.4832,"distinct_ebitda_observations":3,"if_the_shared_observation_were_charged_twice":{"median":9,"pct_ge_12":39.22,"pct_ge_3":77.97},"independence_product_2024":0.135,"note":"the second row is the COST OF THE MISTAKE, measured: 2 members of median cohort size and 8.1 points of formation rate. See OBSERVATION_DEPTH.","vector":{"max":568,"mean":30.27,"median":11,"p25":6,"p75":20,"pct_ge_12":47.44,"pct_ge_3":86.03}},"member_quantity":"ebitda_acceleration","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"zero_anchored_tanh_v1","note":"THREE EBITDA observations, not four. The eligibility rule says so in `lags`, pit_derive.leaves() says so in the flattened leaf set, and observation_lags() reads it off that set. factor_spec_v4: EXECUTABLE DEFINITION growth_t - growth_t-1, each an A-rate under EBITDA_GROWTH_BASE_POLICY_V1; three EBITDA observations, no asset base. Owner decision 2026-09-22.","peer_eligibility":{"lags":[0,1,2],"matched_period":["operating_income","depreciation_amortisation"],"positive_screen":"","price_conditional_terms":[],"primitives":["operating_income","depreciation_amortisation"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"ebitda_t_t1_t2_v1","unavailable_primitives":[],"value_band":null,"why":"EBITDA at THREE observation dates: E_t, E_t-1, E_t-2. Acceleration is growth(t) minus growth(t-1) and the two growths SHARE E_t-1, so the requirement is three observations and not four. A coverage model that multiplied two growth coverages would charge for the shared middle observation twice."},"question":"Is the earnings power growing FASTER than it was?","required_primitives":["operating_income","depreciation_amortisation"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"real_revenue_growth","key":"real_revenue_growth","measured":{"revenue_vector":{"median":19,"pct_ge_12":82.0,"pct_ge_3":98.8},"two_period_joint":"UNKNOWN -- one-period availability only"},"member_quantity":"real_revenue_growth","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"zero_anchored_tanh_v1","note":"","peer_eligibility":{"lags":[0,1],"matched_period":[],"positive_screen":"","price_conditional_terms":[],"primitives":["revenue","cpi"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"revenue_two_periods_plus_cpi_v1","unavailable_primitives":[],"value_band":null,"why":"Revenue at two observation dates a year apart, each non-stale, plus the CPI VINTAGE available on the as-of date -- the deflator is taken over the growth window, not as trailing CPI at the as-of, because that error is correlated with the fiscal calendar and mis-scores the 28% of the universe that is not a December filer. CPI is 100% available on every grid date."},"question":"Did the business sell MORE, or just charge more?","required_primitives":["revenue","cpi"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"admissible_rungs":["sic4","sic3","sic2"],"fallback_rung":["sic4","sic3","sic2","office"],"forbidden_rungs":["office"],"graph_divergence":"","graph_node":"cohort_mean_ev_ebitda","key":"ev_ebitda","max_admissible_rung":"sic2","measured":{"binding_leaf":"shares_outstanding (defensible)","leave_one_out_pct_ge_3":{"all_five":34.2,"without_cash":35.0,"without_defensible_shares":61.6,"without_ebitda":46.8,"without_total_debt":51.2},"seasonality_pct_ge_3":{"april":40.6,"january":14.5,"october":19.1},"sic4_rung":{"median":1,"pct_ge_12":2.4,"pct_ge_3":22.9},"vector":{"max":95,"mean":4.0,"median":1,"p25":0,"p75":3,"p90":8,"pct_ge_12":7.0,"pct_ge_3":34.2,"pct_ge_5":18.6,"pct_ge_8":11.5}},"member_quantity":"ev_ebitda","min_band_members":3,"min_peer_count":12,"min_peer_why":"the benchmark is the MEAN OF THE 50-75 BAND, and the band is a quarter of the vector by construction, so three members inside the band needs twelve in the vector. 3 is the floor for the percentiles themselves (company_scoring.MIN_EBITDA_COHORT).","missingness_behaviour":"drop_factor_and_renormalise_major_weights","model_version":"candidate_equity_shaffer_v2","normalization_type":"benchmark_anchored_tanh_v1","note":"Infeasible for most of the cross-section, and the 40% weight drops out with it -- so the Shaffer equity model IS a 0.25G+0.20P+0.15D model (renormalised 0.4167/0.3333/0.25) for the majority of company-dates. Known BEFORE the replay. factor_spec_v2: the office rung is FORBIDDEN for this factor. Sufficient N and economic coherence are two INDEPENDENT tests and they move in opposite directions along the SIC ladder -- measured, the office rung clears 12 members 94.5% of the time and does it with cohorts of 542-1,310 issuers who share an SEC reviewer and nothing else, while sic4 clears 12 for 2.4% of peer sets. More observations do not help when the economic relationship has become meaningless, so the honest answer when no admissible rung forms a cohort is VALUATION_PEER_SET_INSUFFICIENT, which makes the score PARTIAL and not comparable with a full one (pit_score_signature.PARTIAL_SHAFFER_SCORE). The office rung remains available for BROAD DESCRIPTIVE STATISTICS; what it may not be is a company valuation peer group. factor_spec_v5: eligibility EV_EBITDA_SIX_V2 -- debt, cash and EBITDA AT one period, EBITDA > 0, negative enterprise value VALID, strict first-rung debt walk (R15, R16); the transform is a benchmark-anchored tanh around the coherent cohort median, lower is better (pit_valuation_spec.EV_TRANSFORM_PARAMS_V1); domain EV_EBITDA_DOMAIN_V1.","peer_eligibility":{"lags":[0],"matched_period":["total_debt","cash","operating_income","depreciation_amortisation"],"positive_screen":"ebitda > 0","price_conditional_terms":["cash","price_adjusted","requires_defensible_shares","requires_price","shares_outstanding","total_debt","value_band[ev_ebitda]"],"primitives":["price_adjusted","shares_outstanding","total_debt","cash","operating_income","depreciation_amortisation"],"requires_defensible_shares":true,"requires_price":true,"rule_id":"ev_ebitda_six_primitives_matched_v2","unavailable_primitives":[],"value_band":["ev_ebitda",null,300.0],"why":"A resolvable point-in-time price, a DEFENSIBLE share count, and total_debt, cash and EBITDA all AT the EBITDA period end (R12, R16). EBITDA > 0 is required (R15). Enterprise value may be NEGATIVE and is VALID (owner ruling R15, 2026-09-23): a negative multiple saturates the cheap side of the anchored transform, so v1\'s lower bound of 0.1 is withdrawn; the upper bound of 300 stands where the ruling did not reach (INTERPRETATION recorded in docs/MODEL-LINEAGE.md). The debt walk is STRICT: the first rung carrying a value at the period decides through DEBT_RUNG_EVEBITDA_V1 and lower rungs are not consulted, for peers and target alike (R16)."},"question":"What multiple is the market paying for a right-sized peer\'s earnings?","required_primitives":["price_adjusted","shares_outstanding","total_debt","cash","operating_income","depreciation_amortisation"],"sample_scope":"SURVIVOR_ONLY_DIAGNOSTIC","spec_version":"factor_spec_v2"},{"admissible_rungs":["sic4","sic3","sic2"],"fallback_rung":["sic4","sic3","sic2","office"],"forbidden_rungs":["office"],"graph_divergence":"the graph computes pe_ratio as market_cap / net_income, so its leaves are price, SHARES and net income -- three. A filed EPS already carries the per-share division, so the spec needs price and EPS and NO share count. That is the whole reason P/E is feasible where EV/EBITDA is not: the binding leaf of the valuation chain is the defensible share count, and this factor does not touch it.","graph_node":"pe_ratio","key":"pe_ratio","max_admissible_rung":"sic2","measured":{"eps_rows_in_store":0,"proxy_note":"measured with the net_income ladder standing in for EPS -- same filers, same periods, same filings. An UPPER bound: P/E is undefined for a loss-maker and ~70% of resolvable earnings are positive.","proxy_vector":{"median":9,"pct_ge_12":36.0,"pct_ge_3":92.0},"recovery_priced_universe":{"minutes":8.4,"requests":5066,"retained_gib":0.529}},"member_quantity":"pe_ratio","min_band_members":0,"min_peer_count":3,"min_peer_why":"a cohort MEAN, not a percentile: the core\'s floor of 3 applies.","missingness_behaviour":"drop_factor_and_renormalise_major_weights","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"2.7x EV/EBITDA\'s 3-peer formation rate, 5.1x at the 12-peer bar, and FLAT across the calendar (91.6-92.3% every month) because net income carries the 15-month annual staleness bound rather than the share count\'s 4-month one. factor_spec_v2: the office rung is FORBIDDEN for this factor. Sufficient N and economic coherence are two INDEPENDENT tests and they move in opposite directions along the SIC ladder -- measured, the office rung clears 12 members 94.5% of the time and does it with cohorts of 542-1,310 issuers who share an SEC reviewer and nothing else, while sic4 clears 12 for 2.4% of peer sets. More observations do not help when the economic relationship has become meaningless, so the honest answer when no admissible rung forms a cohort is VALUATION_PEER_SET_INSUFFICIENT, which makes the score PARTIAL and not comparable with a full one (pit_score_signature.PARTIAL_SHAFFER_SCORE). The office rung remains available for BROAD DESCRIPTIVE STATISTICS; what it may not be is a company valuation peer group. factor_spec_v3: eligibility PRICED_EPS_V2 -- EPS is in the store and is no longer declared GENUINELY_UNAVAILABLE.","peer_eligibility":{"lags":[0],"matched_period":[],"positive_screen":"earnings_per_share_pit > 0","price_conditional_terms":["earnings_per_share_pit","price_adjusted","requires_price"],"primitives":["price_adjusted","earnings_per_share_pit"],"requires_defensible_shares":false,"requires_price":true,"rule_id":"price_and_pit_eps_v2","unavailable_primitives":[],"value_band":null,"why":"A resolvable point-in-time price and a point-in-time EPS. NO SHARE COUNT: the per-share division is already inside the filed EPS figure, which is why this chain is two primitives where EV/EBITDA is six. EPS IS IN THIS STORE as of the pit_eps ingest: pit_eps_obs 888,486 rows, pit_eps_ttm 147,914, 1,962 of the 7,102 peers on 2019-06-28 with a TTM knowable on the date. The v1/v2 rule declared it GENUINELY_UNAVAILABLE and that declaration is now false; it is corrected here rather than bypassed in an engine."},"question":"How many years of current earnings is the market paying?","required_primitives":["price_adjusted","earnings_per_share_pit"],"sample_scope":"SURVIVOR_ONLY_DIAGNOSTIC","spec_version":"factor_spec_v2"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"net_debt_ebitda","key":"net_debt_ebitda","measured":{"cash_vector":{"median":20,"pct_ge_3":99.9},"debt_vector":{"median":11,"pct_ge_12":45.2,"pct_ge_3":95.0}},"member_quantity":"net_debt_ebitda","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"drop_factor_and_renormalise_major_weights","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"Lower-is-better, and 94% of resolved total_debt sits on a concept_ladder_v2 LOWER-BOUND rung -- which FLATTERS the score. A percentile over that mixture must report the rung distribution beside it (pit_policy\'s v2 consumer rule). factor_spec_v5: eligibility DEBT_CASH_EBITDA_MATCHED_V2 -- debt and cash AT the EBITDA period end, EBITDA > 0, debt rung gated by DEBT_RUNG_EVEBITDA_V1 (owner rulings R10, R11, R12, 2026-09-23); domain LEVERAGE_DOMAIN_V1.","peer_eligibility":{"lags":[0],"matched_period":["total_debt","cash","operating_income","depreciation_amortisation"],"positive_screen":"ebitda > 0","price_conditional_terms":["cash","total_debt"],"primitives":["total_debt","cash","operating_income","depreciation_amortisation"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"net_debt_over_ebitda_matched_v2","unavailable_primitives":[],"value_band":null,"why":"Leverage against earning power, with debt and cash taken AS OF the EBITDA period end (owner ruling R12, 2026-09-23) rather than at whatever balance sheet happened to be newest. EBITDA > 0 is required BY THE v1 PRECEDENT (R11 reached fcf_conversion; applying it here is an INTERPRETATION recorded in docs/MODEL-LINEAGE.md): a debt multiple of a loss is not a large number, it is an undefined one. The total_debt rung is gated by DEBT_RUNG_EVEBITDA_V1 (R10, DEBT_RUNG_GATE_V1) -- a lower-bound rung the measurement rejected may not enter a scored leverage ratio, for the same reason it may not enter an enterprise value. Price-free."},"question":"How many years of earnings would clear the debt?","required_primitives":["total_debt","cash","operating_income","depreciation_amortisation"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"pit_derive\'s debt_market_cap node reads total_debt at its own newest period; this spec anchors the debt instant AT the EBITDA period end (owner ruling R12, 2026-09-23, no period mixing), so operating_income and depreciation_amortisation are required primitives of a ratio that has no EBITDA in it. Declared, not inferred: the graph\'s leaf set is three, the spec\'s is five.","graph_node":"debt_market_cap","key":"debt_market_cap","measured":{"price_shares_vector":{"median":4,"pct_ge_12":19.5,"pct_ge_3":69.7}},"member_quantity":"debt_market_cap","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"drop_factor_and_renormalise_major_weights","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"factor_spec_v5: eligibility DEBT_AND_MARKET_CAP_MATCHED_V2 -- debt AT the EBITDA period end (R12, INTERPRETATION: one balance-sheet date per row), rung gated (R10), market cap on the raw as-traded price (R19, R20); domain MARKET_CAP_DOMAIN_V1.","peer_eligibility":{"lags":[0],"matched_period":["total_debt","operating_income","depreciation_amortisation"],"positive_screen":"market_cap > 0","price_conditional_terms":["price_adjusted","requires_defensible_shares","requires_price","shares_outstanding","total_debt"],"primitives":["total_debt","operating_income","depreciation_amortisation","price_adjusted","shares_outstanding"],"requires_defensible_shares":true,"requires_price":true,"rule_id":"debt_over_market_cap_matched_v2","unavailable_primitives":[],"value_band":null,"why":"Leverage against MARKET value. The market cap is at the as-of date; the debt is the balance-sheet instant AT the EBITDA period end, so one row carries ONE balance-sheet date across every Q factor (R12, no period mixing -- the EBITDA period is the anchor even though this ratio has no EBITDA in it; INTERPRETATION recorded in docs/MODEL-LINEAGE.md). The debt rung is gated by DEBT_RUNG_EVEBITDA_V1 (R10). SURVIVOR_ONLY by construction; the price basis is PRICE_ROLE_REQUIRED_BASIS[valuation] through pit_price_basis.PRICE_BASIS_TRANSLATION_V1 (R20)."},"question":"How much leverage sits under each dollar of equity value?","required_primitives":["total_debt","operating_income","depreciation_amortisation","price_adjusted","shares_outstanding"],"sample_scope":"SURVIVOR_ONLY_DIAGNOSTIC","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"roa","key":"roa","measured":{"net_income_marginal":[0.951,0.966,0.978],"total_assets_marginal":[1.0,1.0,1.0]},"member_quantity":"roa","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"","peer_eligibility":{"lags":[0],"matched_period":[],"positive_screen":"","price_conditional_terms":[],"primitives":["net_income","total_assets"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"net_income_and_total_assets_v1","unavailable_primitives":[],"value_band":null,"why":"Return on assets. Both are near-universal in the store (95.1-97.8% and 100% of base at the three census dates), which is why the quality legs survive where valuation does not."},"question":"How much profit does each dollar of assets generate?","required_primitives":["net_income","total_assets"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"roe","key":"roe","measured":{"equity_marginal":[0.947,0.963,0.975]},"member_quantity":"roe","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"","peer_eligibility":{"lags":[0],"matched_period":[],"positive_screen":"","price_conditional_terms":[],"primitives":["net_income","equity"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"net_income_and_equity_v1","unavailable_primitives":[],"value_band":null,"why":"Return on shareholder capital. StockholdersEquity leads the ladder; the NCI-inclusive tag is a DIFFERENT denominator and is a fallback."},"question":"How much profit does each dollar of shareholder capital generate?","required_primitives":["net_income","equity"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"interest_coverage","key":"interest_coverage","measured":{"interest_expense_marginal":[0.638,0.607,0.558],"ladder_measured":false},"member_quantity":"interest_coverage","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"factor_spec_v4: numerator OPERATING INCOME (owner decision 2026-09-22, D3 A); eligibility OPERATING_INCOME_AND_INTEREST_MATCHED_V2; domain INTEREST_COVERAGE_DOMAIN_V1.","peer_eligibility":{"lags":[0],"matched_period":["operating_income","interest_expense"],"positive_screen":"interest_expense > 0","price_conditional_terms":[],"primitives":["operating_income","interest_expense"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"operating_income_and_interest_matched_v2","unavailable_primitives":[],"value_band":null,"why":"Interest cover = operating_income / interest_expense, owner decision 2026-09-22. Both at ONE (period_end, qtrs=4): a ratio of one period\'s numbers. DOMAIN (INTEREST_COVERAGE_DOMAIN_V1, executed in pit_replay.resolve_primitives; this rule counts availability only): interest_expense > 0 is the ordinary case; a tagged zero is interest_expense_zero, a negative value interest_expense_negative, an untagged one no_interest_expense_at_operating_income_period -- never a large \'excellent\' ratio. Negative operating income is a legitimate NEGATIVE coverage and is scored. Ladder still AUTHORED, NOT MEASURED; only the measured marginal 63.8/60.7/55.8% of base may be quoted. UPPER BOUND: this rule accepts any non-stale shared period; the engine values only the newest operating-income period, so n_eligible - n_values on the cohort record measures the gap."},"question":"How many times over can the business pay its interest bill?","required_primitives":["operating_income","interest_expense"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"fcf_conversion","key":"fcf_conversion","measured":{"capex_marginal":[0.675,0.711,0.694],"ocf_marginal":[0.942,0.961,0.973]},"member_quantity":"fcf_conversion","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"factor_spec_v5: eligibility FCF_AND_EBITDA_MATCHED_V2 -- OCF and capex from the EBITDA flow period, EBITDA > 0 (R11, R12); domain FCF_DOMAIN_V1.","peer_eligibility":{"lags":[0],"matched_period":["operating_cash_flow","capex","operating_income","depreciation_amortisation"],"positive_screen":"ebitda > 0","price_conditional_terms":[],"primitives":["operating_cash_flow","capex","operating_income","depreciation_amortisation"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"fcf_over_ebitda_matched_v2","unavailable_primitives":[],"value_band":null,"why":"How much of the accounting earnings turns into spendable cash, with operating cash flow and capex taken from the SAME flow period as the EBITDA (R12). EBITDA > 0 is required (R11): free cash flow over a loss is not a conversion rate. Price-free. capex enters as the filed positive outflow (pit_policy)."},"question":"How much of the accounting earnings turns into spendable cash?","required_primitives":["operating_cash_flow","capex","operating_income","depreciation_amortisation"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"}]',
    'pit_frozen_spec:ENGINE_MUST_DERIVE_FROM':
        "'factor_spec_v5'",
    'pit_intermediates:INTERMEDIATE_SCHEMA_VERSION':
        "'pit_intermediate_v1'",
    'pit_invariants:SPEC_VERSION':
        "'economic_spec_v1'",
    'pit_normalization:BENCHMARK_BAND_V1':
        '[0.5,0.75]',
    'pit_normalization:BENCHMARK_MIN_COHORT_V1':
        '3',
    'pit_normalization:NORMALIZATION_POLICY_VERSION':
        "'normalization_policy_v1'",
    'pit_normalization:candidate_v2_registry()':
        '{"DebtMarketCapRank":{"anchor_source":"cohort_rank_position","economic_question":"how much leverage sits under each dollar of equity value, versus peers?","feature_key":"DebtMarketCapRank","feature_role":"research_candidate","higher_is_better":false,"model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","notes":["DOMAIN POLICY pit_factor_spec.MARKET_CAP_DOMAIN_V1: market cap on the raw as-traded price (VALUATION_PRICE_POLICY_V1, PRICE_BASIS_TRANSLATION_V1) times a DEFENSIBLE share count; debt AT the EBITDA period end under DEBT_RUNG_GATE_V1. SURVIVOR_ONLY by construction."],"policy_version":"normalization_policy_v1","rationale":"total_debt / market_cap; the v1 core\'s direction, LOWER_IS_BETTER, pinned by FACTOR_DIRECTION_V1 (owner 2026-09-22). It imports market price into a balance-sheet block by construction, which is why it carries the smallest Q weight; the rank asks only where the burden sits versus peers. Registered under owner ruling R13 (2026-09-23).","scale":null,"scale_source":"none"},"EBITDAAcceleration":{"anchor_source":"zero","economic_question":"is EBITDA growth speeding up or slowing down?","feature_key":"EBITDAAcceleration","feature_role":"research_candidate","higher_is_better":true,"model_version":"candidate_equity_shaffer_v2","normalization_type":"zero_anchored_tanh_v1","notes":["input: growth_t - growth_t-1 = (E_t - E_t-1)/|E_t-1| - (E_t-1 - E_t-2)/|E_t-2|, base policy EBITDA_GROWTH_BASE_POLICY_V1 on both rates","either rate refused under the base policy refuses the acceleration"],"policy_version":"normalization_policy_v1","rationale":"Zero means growth held steady -- a real state that must score 0. The input is the difference of two consecutive A-rates, each under EBITDA_GROWTH_BASE_POLICY_V1, so it needs three EBITDA observations and no asset base. The scale is cohort-derived because a rate difference has its own dispersion and a g borrowed from growth would be a guess. Owner decision 2026-09-22 (D3, acceleration = A).","scale":{"fallback_fixed":null,"fixed_value":null,"min_cohort":8,"multiplier":1.0,"source":"cohort_iqr_then_mad","winsorize_cohort":false},"scale_source":"cohort_iqr_then_mad"},"EBITDAExcessLevel":{"anchor_source":"cohort_p50_p75_mean_margin","economic_question":"does it clear the 50-75 cohort\'s profitability bar?","feature_key":"EBITDAExcessLevel","feature_role":"research_candidate","higher_is_better":true,"model_version":"candidate_equity_shaffer_v2","normalization_type":"benchmark_anchored_tanh_v1","notes":["one cohort IQR clear of the bar scores +76.2; two IQRs +96.4.","measured collapse: rank(margin - M_s) == rank(margin), exactly.","NO fixed fallback scale (owner ruling R8, 2026-09-23: the scale statistic is over the whole eligible vector): a vector too small or too flat to carry a dispersion refuses by name rather than scoring against 6 margin points nobody measured. The v5 entry\'s fallback_fixed = 0.06 is recorded in pit_frozen_spec.FREEZE_V5[\'superseded_definitions_it_digested\']."],"policy_version":"normalization_policy_v1","rationale":"margin - M_s is a cohort-constant shift, so under a rank it scores identically to the margin itself and the benchmark contributes nothing. Anchored, M_s sets where zero is, which is the only way the bar survives into the score.","scale":{"fallback_fixed":null,"fixed_value":null,"min_cohort":8,"multiplier":1.0,"source":"cohort_iqr_then_mad","winsorize_cohort":false},"scale_source":"cohort_iqr_then_mad"},"EBITDAGrowth":{"anchor_source":"zero","economic_question":"is the business generating more EBITDA than a year ago?","feature_key":"EBITDAGrowth","feature_role":"research_candidate","higher_is_better":true,"model_version":"candidate_equity_shaffer_v2","normalization_type":"zero_anchored_tanh_v1","notes":["input: (E_t - E_t-1) / |E_t-1|, base policy EBITDA_GROWTH_BASE_POLICY_V1","the cohort IQR is a dispersion of RATES: one IQR of spread scores +76.2, two +96.4"],"policy_version":"normalization_policy_v1","rationale":"Growth has a true zero and a meaningful sign; a rank would hand out +100s in a cohort where every company shrank. The input is a RATE over the absolute prior EBITDA under pit_factor_spec.EBITDA_GROWTH_BASE_POLICY_V1: a loss shrinking is positive, a zero base is refused by name, and a base too small to carry a rate is refused rather than allowed to explode into the cohort scale. Owner decision 2026-09-22 (D3, growth = A).","scale":{"fallback_fixed":null,"fixed_value":null,"min_cohort":8,"multiplier":1.0,"source":"cohort_iqr_then_mad","winsorize_cohort":false},"scale_source":"cohort_iqr_then_mad"},"EBITDAMarginRank":{"anchor_source":"cohort_rank_position","economic_question":"how efficient versus the whole sector?","feature_key":"EBITDAMarginRank","feature_role":"research_candidate","higher_is_better":true,"model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","notes":["v1 scored this by rank too; unchanged on purpose."],"policy_version":"normalization_policy_v1","rationale":"Margin is an intensity, so ranking it against the sector is the right question and the units carry no extra meaning.","scale":null,"scale_source":"none"},"EBITDAScale":{"anchor_source":"cohort_rank_position","economic_question":"genuine economic scale versus the sector","feature_key":"EBITDAScale","feature_role":"research_candidate","higher_is_better":true,"model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","notes":["the log is inert under a rank; it is not applied."],"policy_version":"normalization_policy_v1","rationale":"Rank the raw dollars. A rank is invariant to any strictly monotone transform, so log1p is decorative here -- measured, S(EBITDA) == S(log1p(EBITDA)) to the digit -- while being undefined for the 37.8% of the universe with EBITDA <= 0.","scale":null,"scale_source":"none"},"FCFConversionRank":{"anchor_source":"cohort_rank_position","economic_question":"how much of the accounting earnings becomes spendable cash, versus peers?","feature_key":"FCFConversionRank","feature_role":"research_candidate","higher_is_better":true,"model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","notes":["DOMAIN POLICY pit_factor_spec.FCF_DOMAIN_V1, executed upstream in pit_replay.resolve_primitives: EBITDA > 0 (ebitda_non_positive otherwise); operating cash flow and capex from the EBITDA flow period; a negative conversion is computed and ranks below every positive peer."],"policy_version":"normalization_policy_v1","rationale":"free_cash_flow / ebitda is a conversion RATE whose ORDERING within the sector is the information: capital intensity differs so much across industries that a fixed bar would mean a different thing in software than in steel. Direction HIGHER_IS_BETTER per FACTOR_DIRECTION_V1 (owner 2026-09-22); registered under owner ruling R13 (2026-09-23) as a DIGESTED body, not an engine choice.","scale":null,"scale_source":"none"},"InterestCoverageRank":{"anchor_source":"cohort_rank_position","economic_question":"how many times over is the interest bill covered, versus peers?","feature_key":"InterestCoverageRank","feature_role":"research_candidate","higher_is_better":true,"model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","notes":["DOMAIN POLICY pit_factor_spec.INTEREST_COVERAGE_DOMAIN_V1, executed upstream in pit_replay.resolve_primitives: only interest_expense > 0 produces a value; a tagged zero (interest_expense_zero), a negative value (interest_expense_negative) and an untagged one (no_interest_expense_at_operating_income_period) never reach this rank, so a debt-free firm is UNAVAILABLE here and Q renormalises.","operating_income <= 0 with interest_expense > 0 is the FLOOR STATE (INTEREST_COVERAGE_DOMAIN_V2, owner ruling R5 2026-09-23): the row is scored at the rank floor (-100) with its raw coverage written, and it is EXCLUDED from the rank population, so the rank runs over positive-coverage peers only (R4). The v5 KNOWN LIMITATION -- an inverted ordering among negative-OI rows -- is closed by that state: no two negative-OI rows are ordered against each other."],"policy_version":"normalization_policy_v1","rationale":"operating_income / interest_expense (owner decision 2026-09-22) is a times-covered multiple whose ORDERING within the sector is the information: 3x against a cohort at 8x means something, 3x alone does not. A rank is also indifferent to the long right tail a tanh scale would have to clamp. Direction HIGHER_IS_BETTER per FACTOR_DIRECTION_V1.","scale":null,"scale_source":"none"},"NetDebtEBITDARank":{"anchor_source":"cohort_rank_position","economic_question":"how many years of earnings would clear the debt, versus peers?","feature_key":"NetDebtEBITDARank","feature_role":"research_candidate","higher_is_better":false,"model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","notes":["DOMAIN POLICY pit_factor_spec.LEVERAGE_DOMAIN_V1 and DEBT_RUNG_GATE_V1, executed upstream: EBITDA > 0; debt and cash AT the EBITDA period end; the total_debt rung is the row\'s source_tag and its bound travels in \'db\' (pit_policy v2 consumer rule); a REJECTED or UNDETERMINED rung refuses by name. Net cash (negative net debt) is scored and ranks best."],"policy_version":"normalization_policy_v1","rationale":"net_debt / ebitda in YEARS; the frozen v1 core ranked it exactly this way and FACTOR_DIRECTION_V1 pins LOWER_IS_BETTER (owner 2026-09-22). A rank is indifferent to the long right tail of leverage that a tanh scale would have to clamp. Registered under owner ruling R13 (2026-09-23).","scale":null,"scale_source":"none"},"RealRevenueGrowth":{"anchor_source":"zero","economic_question":"is real growth positive?","feature_key":"RealRevenueGrowth","feature_role":"research_candidate","higher_is_better":true,"model_version":"candidate_equity_shaffer_v2","normalization_type":"zero_anchored_tanh_v1","notes":["deflate over the GROWTH WINDOW, not by trailing CPI at the as-of: the error is correlated with fiscal calendar and mis-scores the 28% of the universe that is not a December filer."],"policy_version":"normalization_policy_v1","rationale":"Under a rank the deflator is inert: nominal, minus-pi and divided-by-(1+pi) score identically to the digit, so +4% growth into 9% inflation still scores -33.333. Anchored at zero, -5% real scores -46.2 and the subtraction finally means something.","scale":{"fallback_fixed":null,"fixed_value":0.1,"min_cohort":8,"multiplier":1.0,"source":"fixed","winsorize_cohort":false},"scale_source":"fixed"}}',
    'pit_policy:LADDER_VERSION':
        "'concept_ladder_v1'",
    'pit_policy:LADDER_VERSION_V2':
        "'concept_ladder_v2'",
    'pit_policy:LATENCY_POLICY_VERSION':
        "'information_latency_policy_v1'",
    'pit_price_basis:ACTION_GATE_SEMANTICS_V2':
        '{"coverage_established":"the listing carries at least one corporate-action row dated on or before as_of (the probe is POINT-IN-TIME bounded): an empty window is \'no action happened\' -> factor 1.0, status resolved, usable","coverage_unknown":"the listing carries no corporate-action row dated on or before as_of: an empty window is ambiguous -> status no_events_recorded, NOT usable","future_action":"an event needed by the window but dated after as_of -> future_action_refused","gate_version":"pit_safe_corporate_action_gate_v2","incomplete_ratio":"a share-applicable row without a usable ratio -> incomplete_ratio","price_only_events":"rows with applies_to_shares = 0 (spin-offs) are ignored as share divisors","status":"OWNER_RULING_2026_09_23 (R18)","supersedes":"pit_safe_corporate_action_gate_v1","v1_difference":"v1 refused every empty window; v2 refuses only the empty window of a listing with no coverage","window":"events with after < event_date <= through, through <= as_of (a later window RAISES)"}',
    'pit_price_basis:ACTION_GATE_VERSION':
        "'pit_safe_corporate_action_gate_v2'",
    'pit_price_basis:EPS_GROWTH_MIN_POSITIVE_BASE_V1':
        '0.01',
    'pit_price_basis:EPS_PAIR_POLICY_VERSION':
        "'eps_pair_basis_policy_v1'",
    'pit_price_basis:PAIR_PATHS':
        '["same_filing_comparative","cross_filing_harmonised","refused"]',
    'pit_price_basis:PRICE_BASIS_POLICY_VERSION':
        "'price_basis_policy_v1'",
    'pit_price_basis:PRICE_BASIS_TRANSLATION_V1':
        '{"price_adjusted":"price_raw_as_traded","price_raw_as_traded":"price_raw_as_traded","raw_as_printed_unadjusted":"price_raw_as_traded"}',
    'pit_price_basis:PRICE_ROLE_REQUIRED_BASIS':
        '{"return_or_label":"price_action_adjusted","valuation":"price_raw_as_traded"}',
    'pit_price_basis:VALUATION_PRICE_POLICY_V1':
        '{"basis":"price_raw_as_traded","invalid_bar_rule":"a NULL, non-positive or zero-volume print inside the window is SKIPPED and the next earlier bar is tried, newest first; the ten-day floor is never widened; the skipped bars travel on the row (\'sk\')","listing_rule":"exactly ONE scored listing for the entity on the date (pit_identity.scored_universe_as_of); zero -> no_scored_listing; more than one -> listing_ambiguous -- no share-class switching","policy_version":"valuation_price_policy_v1","record":"price_age_days (bar_date to score date) and bar_date on the row","require_traded":true,"roll_back_days":10,"roll_back_unit":"calendar days","same_listing":true,"selection":"the exact score-date raw close, else the latest prior valid raw close","shared_by":["pe_absolute","pe_relative","ev_ebitda_supplement","debt_market_cap"],"status":"OWNER_RULING_2026_09_23 (R19)","straddle_rule":"a rolled-back price is REFUSED (price_straddles_corporate_action) when a share-applicable corporate action falls in (bar_date, score date]: the price and the score-date share basis would straddle the split","translation":"PRICE_BASIS_TRANSLATION_V1"}',
    'pit_score_signature:COMPANY_SCALE_V2':
        '0.85',
    'pit_score_signature:PILLARS':
        '["valuation","growth","profitability","debt"]',
    'pit_score_signature:SIGNATURE_VERSION':
        "'score_signature_v1'",
    'pit_score_signature:V2_BLOCKS':
        '["ebitda_strength","valuation","real_growth","financial_quality"]',
    'pit_score_signature:V2_MAJOR_WEIGHTS':
        '{"ebitda_strength":0.35,"financial_quality":0.1,"real_growth":0.15,"valuation":0.25}',
    'pit_share_coverage:CANONICAL_DEFINITION_ID':
        "'SHARE_COV_D2_STATE_PRICED_V2_POOLED'",
    'pit_share_coverage:PIT_SHARE_STATE_COVERAGE_V2':
        '72.25',
    'pit_valuation_spec:ANCHOR_POLICY_VERSION':
        "'valuation_anchor_v1_fixed_pe_20'",
    'pit_valuation_spec:DEBT_RUNG_EVEBITDA_V1':
        '{"DebtLongtermAndShorttermCombinedAmount":"UNDETERMINED","LongTermDebt":"UNDETERMINED","LongTermDebtAndCapitalLeaseObligations":"UNDETERMINED","LongTermDebtNoncurrent":"REJECTED_MEASURED","SUM(LongTermDebtNoncurrent+LongTermDebtCurrent)":"ELIGIBLE","SUM(LongTermDebtNoncurrent+LongTermDebtCurrent+ShortTermBorrowings)":"ELIGIBLE"}',
    'pit_valuation_spec:DEBT_RUNG_POLICY_VERSION':
        "'DEBT_RUNG_EVEBITDA_V1'",
    'pit_valuation_spec:EV_TRANSFORM_PARAMS_V1':
        '{"anchor_population":"the coherent cohort\'s EV/EBITDA multiples, target INCLUDED","anchor_source":"cohort_median_multiple","direction":"LOWER_IS_BETTER, carried by the sign inside the formula","fixed_fallback":null,"formula":"S = 100 * tanh((median - ev_ebitda) / k)","negative_multiple":"VALID (negative enterprise value): saturates toward +100","normalization_type":"benchmark_anchored_tanh_v1","saturation":"+/-100","scale_min_cohort":8,"scale_population":"the whole eligible vector of multiples, target INCLUDED","scale_source":"cohort_iqr","transform_version":"ev_ebitda_benchmark_anchored_tanh_v1"}',
    'pit_valuation_spec:EV_TRANSFORM_VERSION':
        "'ev_ebitda_benchmark_anchored_tanh_v1'",
    'pit_valuation_spec:PE_ABSOLUTE_ANCHOR_V1':
        '20.0',
    'pit_valuation_spec:PINNED_CURVE':
        '{"anchor":20.0,"b_published":16.3825,"b_solved_to":4,"cost_100_to_150":25.0029,"cost_20_to_30":10.1366,"floor_at":200.0,"ratio":2.4666,"score_at_100x":-53.9905,"tolerance":0.001,"why_the_ratio_matters":"ln(30/20) = ln(150/100) exactly, so a linear-in-log penalty charges the same points for both moves -- ratio 1.00 against a requirement of more than 1. v1\'s own transform is WORSE than flat: 100*tanh(2*gap) charges 58.28 points for 20->30 and 1.78 for 100->150, so the second move costs one-thirty-third of the first. The requirement is not \'penalise extremes\', it is \'penalise them MORE\', and only a convex branch does that."}',
    'pit_valuation_spec:PRESENCE_RULE_VERSION':
        "'valuation_presence_rule_v1'",
    'pit_valuation_spec:SUBFACTORS':
        '{"ev_ebitda_supplement":{"anchor_source":"cohort_median_multiple","can_stand_alone":true,"code":"EVEBITDA","depends_on":null,"factor_spec_key":"ev_ebitda","key":"ev_ebitda_supplement","label":"EV/EBITDA supplement","normalization_type":"benchmark_anchored_tanh_v1","quality_components":["source_quality","freshness","peer_coherence","primitive_coverage"],"required_primitives":["price_raw_as_traded","shares_outstanding","total_debt","cash","operating_income","depreciation_amortisation"],"requires_cohort":true,"role":"supplement","sample_scope":"SURVIVOR_ONLY_DIAGNOSTIC","transform":"benchmark_anchored_tanh_v1","weight":0.2,"why":"Capital-structure-neutral, and the ONE leg that survives a loss. FY2014, n=2,756: NI <= 0 for 49.2%, EBITDA <= 0 for 37.8%, both for 36.5% -- so 12.7% of the cross-section has an EV/EBITDA and NO P/E, and refusing those companies a valuation pillar would be refusing a reading we actually have. STANDALONE for that reason, LAST in weight because it is the rarest: median cohort of 1, P(N>=3) 34.2%, P(N>=12) 7.0%, and 2.4% at sic4. Its debt leaf also carries the ladder-v2 lower-bound mixture -- 94% of the v2 recovery understates debt by a median 14%, which FLATTERS an enterprise value."},"pe_absolute":{"anchor_source":"fixed_market_pe_anchor","can_stand_alone":true,"code":"PE","depends_on":null,"factor_spec_key":"pe_ratio","key":"pe_absolute","label":"P/E absolute valuation","normalization_type":"benchmark_anchored_tanh_v1","quality_components":["source_quality","freshness","primitive_coverage"],"required_primitives":["price_raw_as_traded","earnings_per_share_pit"],"requires_cohort":false,"role":"anchor","sample_scope":"SURVIVOR_ONLY_DIAGNOSTIC","transform":"extreme_valuation_transform_v1","weight":0.5,"why":"TWO LEAVES, NO COHORT. The share division is already inside the filed EPS, so this leg does not touch the defensible share count -- the binding leaf of the whole valuation chain (leave-one-out: EV/EBITDA P(N>=3) 34.2% -> 61.6% when it is dropped). It is the ANCHOR because an extreme multiple must be penalised on its own terms: a 100x P/E is expensive whether or not thirty comparable issuers could be found, and waiting for peers is how a thin SIC4 band becomes a free pass."},"pe_relative":{"anchor_source":"cohort_median_multiple","can_stand_alone":false,"code":"PEPeer","depends_on":"pe_absolute","factor_spec_key":"pe_ratio","key":"pe_relative","label":"P/E relative context","normalization_type":"benchmark_anchored_tanh_v1","quality_components":["source_quality","freshness","peer_coherence","primitive_coverage"],"required_primitives":["price_raw_as_traded","earnings_per_share_pit","coherent_peer_cohort"],"requires_cohort":true,"role":"context","sample_scope":"SURVIVOR_ONLY_DIAGNOSTIC","transform":"extreme_valuation_transform_v1","weight":0.3,"why":"The SAME multiple, asked a DIFFERENT question: not \'is this expensive\' but \'is this expensive FOR ITS INDUSTRY\'. Structurally dependent -- it ranks the number the absolute leg computes, so it cannot exist without it, and `presence()` enforces that rather than trusting a caller. Its cohort must clear BOTH of `pit_factor_spec`\'s gates (sufficient N and economic coherence, ceiling sic2): the SEC Office rung clears twelve members 94.5% of the time with 542-1,310 issuers whose only shared property is their filing reviewer, and that is not a valuation peer group."}}',
    'pit_valuation_spec:SUBFACTOR_WEIGHTS':
        '{"ev_ebitda_supplement":0.2,"pe_absolute":0.5,"pe_relative":0.3}',
    'pit_valuation_spec:TRANSFORM_PARAMS_V1':
        '{"a":25.0,"b_solved":16.382476950757,"c":60.0,"floor":-100.0,"floor_multiple":10.0,"theta_ln2":0.69314718056}',
    'pit_valuation_spec:TRANSFORM_VERSION':
        "'extreme_valuation_transform_v1'",
    'pit_valuation_spec:VALUATION_SPEC_VERSION':
        "'valuation_subfactor_spec_v1'",
}


# ==========================================================================
# THE FIVE GATES, AND HOW EACH CLOSED
# ==========================================================================

GATES: tuple[dict[str, Any], ...] = (
    {
        "gate": 1,
        "question": "Is the share-state policy corrected, and is the seasonal "
                    "artefact gone?",
        "closed_by": "pit_shares / pit_fact_kind staleness v2",
        "result": "Monthly spread 44.17 -> 0.67 points. A figure that read 23% "
                  "in January and 67% in April could not have been quoted as "
                  "one number honestly; now it can.",
        "verdict": "CLOSED",
    },
    {
        "gate": 2,
        "question": "Is EPS / P-E coverage measured?",
        "closed_by": "pit_eps",
        "result": "Measured, with the sign cases separated: a percentage "
                  "growth rate exists only where the base is strictly "
                  "positive, and six transition states carry the rest.",
        "verdict": "CLOSED",
    },
    {
        "gate": 3,
        "question": "Are the circulating share-coverage figures reconciled "
                    "into ONE published definition?",
        "closed_by": "pit_share_coverage",
        "result": "PIT_SHARE_STATE_COVERAGE_V2 = 72.25%, with both halves of "
                  "the coverage contract. The two legacy figures turned out "
                  "to share the SAME measured numerator (4,410 peers at "
                  "2015-06-30) over two different denominators, and the "
                  "25.35% series was 98.4% a dei:TradingSymbol gate -- a "
                  "metadata artefact wearing a share-count label.",
        "verdict": "CLOSED",
    },
    {
        "gate": 4,
        "question": "Is peer coherence a number rather than a rung name?",
        "closed_by": "pit_factor_spec / pit_valuation_spec",
        "result": "Quantified, with the SEC Office rung banned: it clears "
                  "twelve members 94.5% of the time with 542-1,310 issuers "
                  "whose only shared property is their filing reviewer.",
        "verdict": "CLOSED",
    },
    {
        "gate": 5,
        "question": "What is the debt-rung effect on EV/EBITDA?",
        "closed_by": "pit_debt_quality -> DEBT_RUNG_EVEBITDA_V1",
        "result": "MEASURED, AND THE ANSWER WAS RESTRICTIVE. Real in ordering "
                  "(near-agreement 98.27% / 97.71%), not clean enough to pool "
                  "(cheap-decile churn 14.13% / 22.55%), and the "
                  "understatement is SECTORAL rather than random -- median "
                  "ratio 0.642 in Finance/Insurance/RE against 0.932 in "
                  "Services. Closed with a HARD ELIGIBILITY GATE admitting "
                  "two rungs, rejecting one on measurement and holding three "
                  "UNDETERMINED.",
        "verdict": "CLOSED BY A RESTRICTIVE DECISION, NOT A CLEAN BILL OF HEALTH",
    },
)


# ==========================================================================
# WHAT REMAINS BLOCKED
# ==========================================================================

STILL_BLOCKED: dict[str, Any] = {
    "the_diagnostic_replay_itself": (
        "SPECIFICATION complete as of spec_freeze_v6; ENGINE (pit_replay/1.2) "
        "computes 13 of 13 keys under FORMULAS_V2, derives eligibility from "
        "factor_spec_v5 (ENGINE_MUST_DERIVE_FROM, enforced by pit_replay.validate "
        "and require_gate), scores the V legs through pit_valuation_spec's frozen "
        "scorers and applies PEER_FLOOR_V1 and WITHIN_BLOCK_FLOOR_V1. The "
        "MEASUREMENT is now DONE (2026-09-23, "
        "docs/POST-REBOOT-REPORT-2026-09-22.md section 5c): pit_replay_rss at 1k "
        "and 4k, streamed and unbatched, one child at a time with the three local "
        "services paused and the reaper left enabled. Peak commit 257.8 MiB at 1k "
        "and 307.0 MiB at 4k, fixed cost ~243 MiB, streamed WAL 13.5 MB against "
        "56.7 MB unbatched with both arms writing identical rows, scores and "
        "signatures, and a fitted per-date cost of 548 s + 0.076 s per target "
        "that projects a four-date pilot of 75-90 minutes and ~340 MB. The "
        "pagefile peak since boot did not move. 8k was not run, by the owner's "
        "instruction. What remains is an AUTHORISATION and nothing else: the "
        "four-date pilot is rerun on a disposable DB only on the owner's word; "
        "the 165-date replay runs only on the owner's word. No replay "
        "authorisation exists."),
    "the_2026_09_21_pagefile_incident": (
        "CAUSE REJECTED_BY_MEASUREMENT. The v4 narrative -- that the engine "
        "holding a whole cross-section in memory before its first insert was "
        "the root cause -- is withdrawn: the engine's private commit measures "
        "~245 MiB at 4,000 targets with the full 7,102-entity index resident "
        "(pit_replay/1.1; re-measured under 1.2 on 2026-09-23 at 243.6 MiB fixed "
        "and a 307.0 MiB peak once the writer's page cache is counted, report "
        "section 5c), "
        "and ~4.5 GiB of commit sat in three autostarted Python services with "
        "no replay running (docs/POST-REBOOT-REPORT-2026-09-22.md sections 1 "
        "and 5; owner ruling section 5a). The machine was near its commit "
        "ceiling because of resident services; the replay was at most the "
        "marginal trigger. What consumed the 5.9 GB is NOT IDENTIFIED by a "
        "per-process measurement. The artefacts that carry the old explanation "
        "(pilot_sizing_derived.json, full_run_capacity_budget.json) are left as "
        "written; FREEZE_V4 records the claim and its status."),
    "the_machine": (
        "COMMIT-BOUND BY RESIDENT SERVICES, not by the replay. After the "
        "2026-09-22 reboot: 13.85 GiB free on C:, pagefile 7.45 GiB "
        "system-managed, commit charge 13.1-13.4 GB of a 14.9 GB limit at idle "
        "with three autostarted Python services holding ~4.5 GiB "
        "(docs/POST-REBOOT-REPORT-2026-09-22.md section 1). Controlled "
        "measurements run only with those services paused, one child at a "
        "time, with per-process commit recorded before and after."),
    "the_validation_baseline": (
        "BLOCKED ON DEAD-COMPANY PRICES. The priced universe is 2,574 "
        "listings, every one alive in 2026, whose median member underperforms "
        "the total market by -7.45% at 12M. No realized-return claim from "
        "this store may promote anything, and the freeze does not change "
        "that by one row."),
    "hedge_validation": (
        "BLOCKED BEFORE 2026-09-20. Real option quotes begin at the archive "
        "start; a hedge recommendation is computable live and its historical "
        "validation is not, at any price of effort."),
    "three_debt_rungs": (
        "UNDETERMINED, held open. The way to close them is the absence "
        "sidecar pit_policy.absence_limits() specifies -- this store cannot "
        "tell an untagged zero from an untagged amount (159,522 empty-valued "
        "rows discarded at ingest)."),
    "the_anchor_level": (
        "A CONVENTION. The sweep {14, 16, 18, 20, 22} is specified and not "
        "run, and may never be scored against survivor-only returns."),
    "earnings_growth_across_filings": (
        "A TTM assembled across filings is not a same-filing pair, so the "
        "corporate-action table remains directly binding there -- and that "
        "table is Yahoo-sourced, 'inferred', and covers 1,903 of 2,576 "
        "listings."),
    "rate_regime_generalization": (
        "UNPROVEN. The scoreable period is 2013-2026: one full modern rate "
        "cycle, no 2008-style systemic credit crisis, no prolonged "
        "high-inflation era. OBSERVED_REGIME_STABLE must never be read as "
        "STRUCTURALLY_ROBUST_ACROSS_MARKET_HISTORY."),
}


#: Recorded because it is the argument for the process, and the argument will
#: be tested the next time someone wants to skip a gate.
THINGS_THE_EMPTY_TABLES_CAUGHT: tuple[str, ...] = (
    "the denominator bug -- two figures, one numerator, ten points apart",
    "calendar seasonality in the share state, 44.17 points of monthly spread",
    "Office-peer contamination -- a 'peer group' sharing only a filing reviewer",
    "negative-P/E pathology, and the four-state sign-cross taxonomy it forced",
    "split-adjusted P/E corruption -- Apple at 26.03 or 6.51, exactly 4x apart",
    "cross-split EPS fabrication -- +10.44% real growth reading as -72.41%",
    "debt-tail distortion -- 22.55% cheap-decile churn behind a 0.9662 rho",
)



# ==========================================================================
# THE SCORE-ASSEMBLY GAP -- found 2026-09-21 opening the execution phase,
# BEFORE any row was written. The freeze stands; its claim of sufficiency
# does not reach this far, and this module now says so.
# ==========================================================================

SCORE_ASSEMBLY_GAP: dict[str, Any] = {
    "status": "CLOSED 2026-09-21 in spec_freeze_v3",
    "found": "2026-09-21, opening the execution phase",
    "rows_written_before_it_was_found": 0,

    "resolved_2026_09_21": (
        "THE WEIGHT SEMANTICS (spec_freeze_v2). EBITDA .35 / valuation .25 / "
        "real growth .15 / financial quality .10, over a v2 BLOCK set with the "
        "EVGQ alphabet. FREEZE_V1 keeps the defect on record.",
        "THE FACTOR-TO-BLOCK MAP (spec_freeze_v2). 12 of 14 specs assigned to "
        "exactly one block; roa and roe REFUSED_PENDING_REVIEW.",
        "THE DOUBLE COUNT (spec_freeze_v2). ebitda_growth and "
        "ebitda_acceleration owned by E ONLY.",
        "MIN_BLOCK_WEIGHT = 0.40 (spec_freeze_v2), executable, structural: no "
        "single block can score alone.",
        "WITHIN-BLOCK WEIGHTS (spec_freeze_v3). POLICY_WEIGHTS_V1, tagged "
        "NOT_EMPIRICALLY_OPTIMIZED. E = .35 benchmark + .25 growth + .20 "
        "efficiency + .20 acceleration, reproducing the published effective "
        "weights exactly. Q = .35 fcf + .30 coverage + .25 net-debt/EBITDA + "
        ".10 debt/market-cap. V imported from pit_valuation_spec. G = 1.00.",
        "ebitda_scale is a SCORING_WEIGHT_ZERO_CHALLENGER: owned by E, zero "
        "weight, and contributing nothing to E's availability, its quality "
        "aggregation or its within-block denominator.",
    ),

    "what_still_blocks": (),

    "one_more_thing_to_look_at": (
        "G CARRIES A SINGLE FACTOR, and that is a deliberate choice rather "
        "than an oversight. real_revenue_growth alone holds 0.15 of the "
        "company score, so G's availability IS that one factor's "
        "availability. A second growth factor was not manufactured to make "
        "the architecture look symmetric; EPS growth may become a challenger "
        "later now that its share-basis machinery is correct."),

    "owner_confirmation_pending_v5": (
        "Six items inside spec_freeze_v5 were PROPOSED by the 2026-09-22 session "
        "and await the owner's word (a change to any digested one is a v6): "
        "(1) EBITDA_GROWTH_BASE_POLICY_V1.max_abs_rate = 10.0, a band on the RATE "
        "(an 11x rise from a healthy base is refused too; |rate| == 10 computed); "
        "(2) the matched-period coverage eligibility "
        "OPERATING_INCOME_AND_INTEREST_MATCHED_V2 and the first-rung-at-period "
        "rule; (3) the refusal vocabulary (ebitda_growth_base_zero, "
        "ebitda_growth_base_near_zero -- a misnomer for a rate band, rename "
        "candidate ebitda_growth_rate_beyond_band --, no_operating_income_period, "
        "no_interest_expense_at_operating_income_period, interest_expense_zero, "
        "interest_expense_negative); (4) the PERCENTILE_RANK transform for "
        "InterestCoverageRank; (5) under that rank the ORDERING AMONG NEGATIVE-OI "
        "ROWS IS INVERTED relative to interest burden -- remedies: tie all "
        "negative-OI rows at the cohort floor, or rank by sign then by coverage; "
        "(6) Q is now available on interest_coverage ALONE and E + Q = 0.45 "
        "clears MIN_BLOCK_WEIGHT, so companies without G are scored -- should a "
        "block need a within-block coverage floor before it counts? The "
        "formulas, the OI numerator and the domain rule are the owner's own."),
    "owner_rulings_2026_09_23": {
        "status": "DECIDED by the owner 2026-09-23 (R1-R21); digested in spec_freeze_v6",
        "pending_v5_dispositions": {
            "1_max_abs_rate": ("CONFIRMED (R1): 10.0 inclusive, a modelling-domain guard, "
                               "challenger-eligible later -- EBITDA_GROWTH_BASE_POLICY_V2"),
            "2_matched_period_rule": ("CONFIRMED (R2): matched period, first valid concept "
                                      "rung for the period, never mixing rungs -- "
                                      "INTEREST_COVERAGE_DOMAIN_V2 and factor_spec_v5"),
            "3_refusal_vocabulary": ("CONFIRMED with ONE rename (R3): ebitda_growth_base_near_zero "
                                     "-> ebitda_growth_rate_beyond_band; the old name stays in "
                                     "pit_replay.ENGINE_REASONS as history and in RETIRED_REASONS"),
            "4_percentile_rank_for_coverage": ("CONFIRMED (R4) over POSITIVE-operating-income "
                                               "observations only"),
            "5_negative_oi_inversion": ("RESOLVED (R5): operating income <= 0 is the factor's "
                                        "FLOOR STATE -- scored at the rank floor, excluded from "
                                        "the rank population; zero / negative / missing interest "
                                        "keep their named refusals"),
            "6_single_factor_q": ("ADOPTED (R6): WITHIN_BLOCK_FLOOR_V1 -- E >= 0.50 and Q >= 0.50 "
                                  "of scoring weight, G 1.00, V by its presence rule; "
                                  "MIN_BLOCK_WEIGHT = 0.40 unchanged"),
        },
        "vq_rulings": ("R7 benchmark INCLUDES the target in vector and band, one cohort-constant "
                       "M_s per peer set; R8 scale over the whole eligible vector; R9 band floor 3, "
                       "never widened; R10 strict DEBT_RUNG_EVEBITDA_V1 gate for net_debt_ebitda "
                       "and debt_market_cap; R11 fcf_conversion requires EBITDA > 0 (net_debt_ebitda "
                       "the same per v1 precedent, flagged as interpretation); R12 debt/cash AT the "
                       "EBITDA period end, cash flow/capex the same flow period, no period mixing; "
                       "R13 Q normalisation in the digested registry, V via pit_valuation_spec's "
                       "frozen scorers; R14 one frozen per-factor peer floor, one distinct refusal; "
                       "R15 EV/EBITDA benchmark-anchored tanh around the coherent cohort median, "
                       "lower better, positive EBITDA, negative EV valid; R16 strict EV debt walk; "
                       "R17 P/E relative = the convex transform around the peer median, target "
                       "INCLUDED, near-zero-positive EPS excluded from both legs; R18 corporate-"
                       "action gate with listing-level completeness semantics; R19 the valuation "
                       "price (exact date else <= 10 calendar days prior, valid trade, same "
                       "listing, price_age_days on the row, ambiguous listing refused); R20 one "
                       "digested price-basis translation constant, one resolver; R21 historical "
                       "refusal codes kept, cohort-aware golden witnesses, cohorts may mix TTM/EPS "
                       "rungs with provenance on the row"),
        "interpretations_reported_not_chosen": (
            "debt_market_cap's debt is anchored AT the EBITDA period end like every other "
            "balance-sheet instant (R12 read literally: one balance-sheet date per row); "
            "the alternative (its own newest balance sheet) is recorded in docs/MODEL-LINEAGE.md",
            "EV/EBITDA keeps v1's UPPER bound of 300 while the lower bound is withdrawn under R15",
            "the EV transform has NO fixed fallback scale (a constant in multiple units would be "
            "invented); thin or flat cohorts refuse by pit_normalization's own reason",
            "earnings_discontinuity_state is EARNINGS_SIGN_CROSS only from the selected TTM "
            "row's own sign_crossing flag; no pair classification runs in the engine",
            "market_cap_as_of_detail runs without require_split_check (the split status travels "
            "on the row in 'sc')",
            "a floor-state coverage row is scored -100 only against an ELIGIBLE cohort that clears "
            "the peer floor on positive-coverage members; otherwise it carries the cohort refusal "
            "with the state still marked ('fs')",
            "net_debt_ebitda requires EBITDA > 0 by the v1 precedent -- R11 reached fcf_conversion "
            "only",
            "'non-stale rung at the period' (R16) means the EBITDA period's own 15-month bound; the "
            "6-month quarterly budget for free-floating instants does not apply to debt and cash "
            "anchored at P",
            "the valuation price steps back past NULL, non-positive and zero-volume prints to the "
            "latest prior valid trade inside the ten-day floor (R19 read literally), and a "
            "rolled-back price is refused when a share-applicable corporate action falls between "
            "its bar and the score date",
            "the corporate-action coverage probe of gate v2 is point-in-time bounded (rows dated "
            "on or before as_of)",
            "V feature rows carry the TRANSFORM version in norm_method (extreme_valuation_transform_v1 "
            "/ ev_ebitda_benchmark_anchored_tanh_v1) rather than a pit_normalization taxonomy "
            "constant, because the taxonomy's tanh label would misdescribe the convex legs",
        ),
        "record": "docs/MODEL-LINEAGE.md, spec_freeze_v6 section",
    },
    "decision_D3_2026_09_22": {
        "status": ("DECIDED by the owner 2026-09-22; digested in spec_freeze_v5 as "
                   "pit_factor_spec.FORMULAS_V1, EBITDA_GROWTH_BASE_POLICY_V1, "
                   "INTEREST_COVERAGE_DOMAIN_V1"),
        "ebitda_growth": "A: (EBITDA_t - EBITDA_t-1) / abs(EBITDA_t-1) -- a RATE",
        "ebitda_acceleration": ("A: ebitda_growth_t - ebitda_growth_t-1 -- NOT a "
                                "second difference over assets"),
        "interest_coverage": ("operating_income / interest_expense; interest_expense "
                              "> 0 or a NAMED refusal (zero / negative / missing); "
                              "negative operating income is a legitimate negative "
                              "value"),
        "base_policy": ("EBITDA_GROWTH_BASE_POLICY_V1: zero base refused; |rate| > "
                        "max_abs_rate refused; sign transition and both-negative "
                        "computed with abs(), base sign on the row"),
        "rejected": ("B: (E_t - E_t-4q) / Assets_t-4q (registry, pit_coverage, engine "
                     "as of 52b9853)",
                     "B acceleration: (E_t - 2E_t-4q + E_t-8q) / Assets_t-8q (registry, "
                     "pit_coverage, engine as of 52b9853)",
                     "EBITDA / interest_expense (pit_coverage FEATURE_NOTE only)"),
        "engine_uses": "A / A / OI-over-interest from pit_replay/1.1 under factor_spec_v4",
        "evidence_note": ("37.8% EBITDA <= 0 is the FY2014 n=2,756 valuation-leg "
                          "measurement at t (pit_valuation_spec.MEASURED); the design "
                          "record's 34% is unsourced; the -$50k -> +$10m example is "
                          "illustrative, not observed. Neither form was measured "
                          "against the other. The v4 record was silent on "
                          "acceleration."),
        "record_of_the_open_question": "FREEZE_V4 and docs/D3-EVIDENCE-2026-09-22.md",
        "superseded_record": {
        "status": "UNDECIDED -- owner instruction 2026-09-22: report, do not infer (SUPERSEDED BY THE DECISION ABOVE; kept verbatim)",
        "ebitda_growth_denominator": {
            "alternative_A": "(EBITDA_t - EBITDA_t-1) / abs(EBITDA_t-1) -- a RATE",
            "A_origin": "pit_derive.py line ~481, a formula STRING; pit_derive "
                        "computes nothing",
            "alternative_B": "(EBITDA_t - EBITDA_t-4q) / Assets_t-4q -- an AMOUNT "
                             "over a common asset base",
            "B_origin": "pit_normalization registry EBITDAGrowth (rationale: a "
                        "rate is meaningless on the 37.8% with EBITDA <= 0) and "
                        "pit_coverage FEATURE_NOTE '(E_t - E_t-1y) / Assets_t-1y'",
            "engine_currently_uses": "B -- pit_replay carries assets_lag1 / "
                                     "assets_lag2 and refuses with "
                                     "R_NO_ASSETS_LAG1 / R_NO_ASSETS_LAG2",
            "historical_evidence": "docs/SHAFFER-EQUITY-V2-CANDIDATE.md open "
                                   "question 6: 34% of the universe has EBITDA "
                                   "<= 0 and a rate turns -$50k -> +$10m into "
                                   "+20,100% while a real $50m -> $100m doubling "
                                   "reads +100%. No evidence supports A.",
        },
        "interest_coverage_numerator": {
            "alternative_A": "operating_income / interest_expense",
            "A_origin": "pit_derive.py line ~555; pit_factor_spec "
                        "required_primitives=(operating_income, interest_expense); "
                        "pit_coverage's own arithmetic (line ~217 compares "
                        "interest_expense > operating_income)",
            "alternative_B": "EBITDA / interest_expense",
            "B_origin": "pit_coverage FEATURE_NOTE only ('quality_interest_"
                        "coverage': 'EBITDA / interest expense')",
            "engine_currently_uses": "NEITHER -- interest_coverage is "
                                     "NOT_COMPUTABLE in the pilot engine; no "
                                     "choice has been made",
            "historical_evidence": "none measured for either",
        },
        },
    },
    "what_this_does_not_make_true": (
        "The weights are POLICY, not results. POLICY_WEIGHTS_V1 is a "
        "transparent starting hypothesis for a SURVIVOR_ONLY_DIAGNOSTIC "
        "replay; nothing here claims 35/30/25/10 is optimal, and the status "
        "string says so on every row that carries it."),
}


def can_execute_replay() -> tuple:
    """May the diagnostic replay be calculated from what is specified today?

    Returns (False, reasons) while the score-assembly gap stands. The freeze
    is real and intact; it simply does not reach far enough to assemble a
    company score, and saying so HERE means a runner cannot proceed by
    quietly assuming that it does.
    """
    reasons = list(SCORE_ASSEMBLY_GAP["what_still_blocks"])
    if SCORE_ASSEMBLY_GAP["status"].startswith("BLOCKING") and not reasons:
        reasons.append("the gap is marked BLOCKING with no reason recorded")
    return (not reasons), reasons




# ==========================================================================
# THE WEIGHT-SEMANTIC INVARIANT
#
#     weight key  +  pillar semantic id  +  weight
#
# must match across code, documentation, replay metadata and frozen
# components. Comparing the numeric vector alone is INSUFFICIENT, and this
# module exists partly to prove it: [.35, .25, .15, .10] == [.35, .25, .15,
# .10] held true for a week while the two sides described different models.
# ==========================================================================

WEIGHT_SEMANTIC_INVARIANT = (
    "A weight table is (key, semantic id, weight) TOGETHER. Any source that "
    "claims to carry the v2 weights -- code, documentation, replay metadata, "
    "a frozen component -- must agree on all three. Numeric-vector equality "
    "is not evidence of agreement; it is what the 2026-09-21 defect looked "
    "like from the inside."
)


def assert_weight_semantics(claimed, source: str = "caller") -> None:
    """Refuse a weight table that agrees on numbers and differs on meaning.

    `claimed` is an iterable of (letter, semantic_id, weight) triples, or a
    mapping of semantic_id -> weight. Raises ValueError naming the first
    disagreement.
    """
    import pit_score_signature as _sig
    truth = {b: (_sig.V2_BLOCK_LETTER[b], _sig.V2_MAJOR_WEIGHTS[b])
             for b in _sig.V2_BLOCKS}

    if hasattr(claimed, "items"):
        triples = [(truth.get(k, (None, None))[0], k, v)
                   for k, v in claimed.items()]
    else:
        triples = list(claimed)

    seen = {}
    for letter, block, weight in triples:
        if block not in truth:
            raise ValueError(
                "%s names %r, which is not a v2 block. The v2 blocks are %s. "
                "%s" % (source, block, ", ".join(_sig.V2_BLOCKS),
                        WEIGHT_SEMANTIC_INVARIANT))
        want_letter, want_weight = truth[block]
        if letter is not None and letter != want_letter:
            raise ValueError(
                "%s gives %r the letter %r; the frozen specification gives it "
                "%r. %s" % (source, block, letter, want_letter,
                            WEIGHT_SEMANTIC_INVARIANT))
        if abs(float(weight) - want_weight) > 1e-12:
            raise ValueError(
                "%s gives %r the weight %r; the frozen specification gives it "
                "%r. %s" % (source, block, weight, want_weight,
                            WEIGHT_SEMANTIC_INVARIANT))
        seen[block] = True
    missing = [b for b in _sig.V2_BLOCKS if b not in seen]
    if missing:
        raise ValueError(
            "%s omits %s. A partial weight table is not a weight table. %s"
            % (source, ", ".join(missing), WEIGHT_SEMANTIC_INVARIANT))

# ==========================================================================
# REPORT AND SELF-CHECK
# ==========================================================================

def report() -> str:
    v = verify()
    lines = [
        "SHAFFER EQUITY v2 -- SPECIFICATION FREEZE",
        "",
        "  model_version   %s" % FROZEN_MODEL_VERSION,
        "  frozen_at       %s" % FROZEN_AT,
        "  components      %d" % len(COMPONENTS),
        "  digest          %s" % v["current_digest"],
        "  intact          %s" % v["intact"],
        "",
        "WHAT THIS FREEZE MEANS", "",
    ]
    for item in WHAT_FREEZING_MEANS:
        lines.append("  + %s" % item)
    lines += ["", "WHAT IT DOES NOT MEAN", ""]
    for item in WHAT_IT_DOES_NOT_MEAN:
        lines.append("  - %s" % item)
    lines += ["", "THE FIVE GATES", ""]
    for g in GATES:
        lines.append("  %d. %s" % (g["gate"], g["question"]))
        lines.append("     %s" % g["verdict"])
    lines += ["", "STILL BLOCKED", ""]
    for key, why in STILL_BLOCKED.items():
        lines.append("  %-32s %s" % (key, why.split(".")[0] + "."))
    lines += ["", "WHAT THE EMPTY TABLES CAUGHT BEFORE THEY WERE FILLED", ""]
    for item in THINGS_THE_EMPTY_TABLES_CAUGHT:
        lines.append("  * %s" % item)
    return "\n".join(lines)


def validate() -> list[str]:
    problems: list[str] = []

    if SAMPLE_SCOPE not in FROZEN_MODEL_VERSION:
        problems.append("the survivorship limitation must be IN the model "
                        "version string, not beside it")
    man = manifest()
    missing = [k for k, v in man.items() if v.startswith("MISSING(")]
    if missing:
        problems.append("components could not be read: %s" % ", ".join(missing))
    if len(man) != len(COMPONENTS):
        problems.append("a component key collided; each (module, attr) must be "
                        "unique")

    # The digest must actually depend on every component.
    base = digest(man)
    for key in sorted(man):
        perturbed = dict(man)
        perturbed[key] = perturbed[key] + "_PERTURBED"
        if digest(perturbed) == base:
            problems.append("the digest does not depend on %s" % key)

    if FROZEN_DIGEST == "PENDING":
        problems.append("FROZEN_DIGEST is still PENDING -- the freeze has not "
                        "been recorded, so verify() cannot detect drift")
    elif not _FROZEN_MANIFEST:
        problems.append("the frozen manifest was not recorded alongside the "
                        "digest, so drift could be detected but not located")
    elif digest(_FROZEN_MANIFEST) != FROZEN_DIGEST:
        problems.append("the recorded manifest does not hash to FROZEN_DIGEST: "
                        "record and digest were taken from different manifests, "
                        "so verify() would locate drift against the wrong baseline")
    elif set(_FROZEN_MANIFEST) != {"%s:%s" % (m, a) for m, a, _ in COMPONENTS}:
        problems.append("the recorded manifest and COMPONENTS name different keys")

    v = verify()
    if FROZEN_DIGEST != "PENDING" and not v["intact"]:
        problems.append("the specification has DRIFTED from its freeze: %s"
                        % json.dumps(v["drifted"])[:400])

    if len(GATES) != 5:
        problems.append("there are five freeze gates")
    gate5 = [g for g in GATES if g["gate"] == 5][0]
    if "RESTRICTIVE" not in gate5["verdict"].upper():
        problems.append("gate 5 closed with a restrictive decision and the "
                        "record must say so, not imply a clean result")
    if not STILL_BLOCKED.get("the_validation_baseline"):
        problems.append("the validation baseline must be recorded as blocked")

    ok, reasons = can_execute_replay()
    if ok and SCORE_ASSEMBLY_GAP["status"].startswith("BLOCKING"):
        problems.append("can_execute_replay() says yes while the score "
                        "assembly gap is still BLOCKING")
    if not ok and not STILL_BLOCKED.get("the_diagnostic_replay_itself"):
        problems.append("the replay is blocked and STILL_BLOCKED does not "
                        "say so; an unrecorded blocker is not a blocker")
    if FREEZE_V1["digest"] != "7a844dca245cfb37ea6722d8e34a9d9407f82e2210ec17f62605aa8ab40f7843":
        problems.append("FREEZE_V1's digest was edited; a frozen record is "
                        "never repaired in place")
    if FREEZE_V1["status"] != STATUS_FROZEN_NONEXECUTABLE:
        problems.append("FREEZE_V1 must stay recorded as non-executable")
    if FREEZE_V1["superseded_by"] != "spec_freeze_v2":
        problems.append("FREEZE_V1 must name its successor")
    if FREEZE_V2["digest"] != "5219bdd5902a220bbce6fc4b3bb68aeac17ee34928d340103be24ef85fd31462":
        problems.append("FREEZE_V2's digest was edited; a frozen record is "
                        "never repaired in place")
    if FREEZE_V2["status"] != STATUS_FROZEN_NONEXECUTABLE:
        problems.append("FREEZE_V2 must stay recorded as non-executable")
    if FREEZE_V2["status_reason"] != REASON_MISSING_WITHIN_BLOCK_WEIGHTS:
        problems.append("FREEZE_V2 must name the weights it lacked")
    if FREEZE_V2["superseded_by"] != "spec_freeze_v3":
        problems.append("FREEZE_V2 must name its successor")
    if FREEZE_V3["digest"] != "2f9bba3136ac45f4bdc37c5197e646acf645410f1c36ded698fbdd368f86e764":
        problems.append("FREEZE_V3's digest was edited; a frozen record is "
                        "never repaired in place")
    if FREEZE_V3["status"] != STATUS_SUPERSEDED:
        problems.append("FREEZE_V3 must be recorded as superseded")
    if FREEZE_V3["status_reason"] != REASON_DIGEST_COVERED_VERSION_STRINGS:
        problems.append("FREEZE_V3 must name the governance defect")
    if FREEZE_V3["rows_it_ever_produced_in_main_store"] != 0:
        problems.append("no main-store row was ever produced under v3")
    if FREEZE_V3["superseded_by"] != "spec_freeze_v4":
        problems.append("FREEZE_V3 must name its successor")
    if FREEZE_V4["digest"] != "912268b3ec11bfc641fcac78078a0fb288cc51ce5432f8c27f931a532d1faae9":
        problems.append("FREEZE_V4's digest was edited; a frozen record is "
                        "never repaired in place")
    if FREEZE_V4["n_components"] != 47:
        problems.append("FREEZE_V4 had 47 components")
    if (FREEZE_V4["status"] != STATUS_SUPERSEDED
            or FREEZE_V4["status_reason"] != REASON_FROZE_UNRESOLVED_SEMANTICS):
        problems.append("FREEZE_V4 must be recorded as superseded for freezing "
                        "unresolved semantics")
    if (FREEZE_V4["rows_it_ever_produced_in_main_store"] != 0
            or FREEZE_V4["may_execute_replay"] is not False):
        problems.append("FREEZE_V4 produced no main-store row and may not execute "
                        "the replay")
    if FREEZE_V4["incident_narrative_it_carried"]["status"] != INCIDENT_CAUSE_REJECTED:
        problems.append("FREEZE_V4 must record its incident narrative as "
                        "REJECTED_BY_MEASUREMENT")
    if FREEZE_V4["superseded_by"] != "spec_freeze_v5":
        problems.append("FREEZE_V4 must name its successor")
    if FREEZE_V5["digest"] != "58722f4c00b676fee76bcc10ba341f44ce18c540a2fbc90625570a51786a5b59":
        problems.append("FREEZE_V5's digest was edited; a frozen record is "
                        "never repaired in place")
    if FREEZE_V5["n_components"] != 61:
        problems.append("FREEZE_V5 had 61 components")
    if (FREEZE_V5["status"] != STATUS_SUPERSEDED
            or FREEZE_V5["status_reason"] != REASON_ENGINE_LACKED_VQ_PATHS):
        problems.append("FREEZE_V5 must be recorded as superseded because its engine "
                        "computed six of thirteen keys")
    if (FREEZE_V5["rows_it_ever_produced_in_main_store"] != 0
            or FREEZE_V5["may_execute_replay"] is not False):
        problems.append("FREEZE_V5 produced no main-store row and may not execute "
                        "the replay")
    if FREEZE_V5["superseded_by"] != "spec_freeze_v6":
        problems.append("FREEZE_V5 must name its successor")
    if (SPEC_FREEZE_VERSION != FREEZE_V5["superseded_by"]
            or SPEC_FREEZE_VERSION in {f["freeze_version"] for f in
                                       (FREEZE_V1, FREEZE_V2, FREEZE_V3, FREEZE_V4,
                                        FREEZE_V5)}):
        problems.append("the live freeze must carry the name the last sealed record "
                        "names as its successor, never a sealed name")
    if len({FREEZE_V1["digest"], FREEZE_V2["digest"], FREEZE_V3["digest"],
            FREEZE_V4["digest"], FREEZE_V5["digest"], digest()}) != 6:
        problems.append("the six freezes must have six distinct digests")

    # v5 digests BODIES and the executable definitions. Prove it: the body
    # components are present, no body carries a function or something the
    # serializer could not render, and the digest moves when a body moves
    # with no version string touched (test_pit_frozen_spec).
    keys = {"%s:%s" % (m, a) for m, a, _ in COMPONENTS}
    for need in ("pit_factor_spec:SPECS_V3", "pit_factor_spec:SPECS_V4",
                 "pit_factor_spec:FORMULAS_V1", "pit_factor_spec:FORMULA_GOLDEN_V1",
                 "pit_factor_spec:EBITDA_GROWTH_BASE_POLICY_V1",
                 "pit_factor_spec:INTEREST_COVERAGE_DOMAIN_V1",
                 "pit_normalization:candidate_v2_registry()",
                 "pit_normalization:BENCHMARK_BAND_V1",
                 "pit_normalization:BENCHMARK_MIN_COHORT_V1",
                 "pit_factor_blocks:FACTOR_DIRECTION_V1",
                 "pit_valuation_spec:SUBFACTORS",
                 "pit_valuation_spec:TRANSFORM_PARAMS_V1",
                 "pit_score_signature:V2_BLOCKS", "pit_price_basis:PAIR_PATHS",
                 "pit_frozen_spec:ENGINE_MUST_DERIVE_FROM",
                 # v6: the owner's V/Q rulings as bodies
                 "pit_factor_spec:SPECS_V5", "pit_factor_spec:FORMULAS_V2",
                 "pit_factor_spec:FORMULA_GOLDEN_V2",
                 "pit_factor_spec:EBITDA_GROWTH_BASE_POLICY_V2",
                 "pit_factor_spec:INTEREST_COVERAGE_DOMAIN_V2",
                 "pit_factor_spec:LEVERAGE_DOMAIN_V1", "pit_factor_spec:FCF_DOMAIN_V1",
                 "pit_factor_spec:MARKET_CAP_DOMAIN_V1", "pit_factor_spec:EV_EBITDA_DOMAIN_V1",
                 "pit_factor_spec:PE_DOMAIN_V1", "pit_factor_spec:DEBT_RUNG_GATE_V1",
                 "pit_factor_spec:PEER_FLOOR_V1", "pit_factor_blocks:WITHIN_BLOCK_FLOOR_V1",
                 "pit_valuation_spec:EV_TRANSFORM_PARAMS_V1",
                 "pit_price_basis:ACTION_GATE_SEMANTICS_V2",
                 "pit_price_basis:PRICE_BASIS_TRANSLATION_V1",
                 "pit_price_basis:VALUATION_PRICE_POLICY_V1"):
        if need not in keys:
            problems.append("v6 must digest %s by body" % need)
    man = manifest()
    for marker, why in (
            ("unrenderable:", "something the canonical serializer could not "
                              "render deterministically"),
            ("callable:", "a callable -- a function's CONTENT is outside the "
                          "digest; a body carries data, never code")):
        hits = sorted(k for k, v in man.items() if marker in v)
        if hits:
            problems.append("a body component contains %s: %s"
                            % (why, ", ".join(hits)))
    import pit_factor_spec as _fs
    b4 = _fs.BY_KEY_V4["ebitda_benchmark"]
    if "revenue" not in b4.required_primitives:
        problems.append("v5 must carry the MARGIN reading of ebitda_benchmark")
    if _fs.BY_KEY_V4["pe_ratio"].peer_eligibility.unavailable_primitives != ():
        problems.append("v5 must carry the corrected P/E eligibility")
    if ENGINE_MUST_DERIVE_FROM != _fs.FACTOR_SPEC_VERSION_V5:
        problems.append("ENGINE_MUST_DERIVE_FROM must be the spec version v6 "
                        "digests by body (factor_spec_v5)")
    if not (_fs.FORMULAS_V2["pe_relative"].count("CONVEX")
            and "tanh" in _fs.FORMULAS_V2["ev_ebitda_supplement"]
            and _fs.EBITDA_GROWTH_BASE_POLICY_V2["beyond_max_abs_rate"]
            == "REFUSED:ebitda_growth_rate_beyond_band"
            and _fs.INTEREST_COVERAGE_DOMAIN_V2["non_positive_operating_income"]
            .startswith("FLOOR_STATE:")):
        problems.append("the v6 bodies must carry R17 (convex pe_relative), R15 (tanh EV), "
                        "R3 (the rename) and R5 (the floor state)")
    rulings = SCORE_ASSEMBLY_GAP.get("owner_rulings_2026_09_23", {})
    if (not str(rulings.get("status", "")).startswith("DECIDED")
            or len(rulings.get("pending_v5_dispositions", {})) != 6):
        problems.append("the six pending-v5 items must each carry the owner's disposition")
    if "AWAITING_OWNER_CONFIRMATION" in _fs.EBITDA_GROWTH_BASE_POLICY_V2["status"] or \
            "AWAITING_OWNER_CONFIRMATION" in _fs.INTEREST_COVERAGE_DOMAIN_V2["status"]:
        problems.append("the successor policies must carry the owner's confirmation")
    import pit_factor_blocks as _fbf
    if _fbf.WITHIN_BLOCK_FLOOR_V1.get(_fbf.BLOCK_Q) != 0.50 or _fbf.MIN_BLOCK_WEIGHT != 0.40:
        problems.append("R6: Q's within-block floor is 0.50 and MIN_BLOCK_WEIGHT stays 0.40")
    f = _fs.FORMULAS_V1
    if not ("abs(ebitda_t-1)" in f["ebitda_growth"]
            and "ebitda_growth_t - ebitda_growth_t-1" in f["ebitda_acceleration"]
            and f["interest_coverage"].startswith(
                "interest_coverage = operating_income / interest_expense")):
        problems.append("FORMULAS_V1 must state growth A, acceleration A and "
                        "OI-over-interest")
    d3 = SCORE_ASSEMBLY_GAP.get("decision_D3_2026_09_22", {})
    if (not str(d3.get("status", "")).startswith("DECIDED")
            or "open_decision_D3" in SCORE_ASSEMBLY_GAP):
        problems.append("D3 must be recorded as DECIDED and the UNDECIDED record "
                        "retired")
    if "REJECTED_BY_MEASUREMENT" not in STILL_BLOCKED.get(
            "the_2026_09_21_pagefile_incident", ""):
        problems.append("the pagefile-incident narrative must be recorded as "
                        "REJECTED_BY_MEASUREMENT")
    import pit_factor_blocks as _fbd
    if any(_fbd.FACTOR_DIRECTION_V1.get(k, _fbd.UNDECLARED) == _fbd.UNDECLARED
           for k in _fbd.FACTOR_BLOCK):
        problems.append("every scoring factor must have a declared direction")

    import pit_factor_blocks as _fb2
    if _fb2.POLICY_WEIGHTS_STATUS != "NOT_EMPIRICALLY_OPTIMIZED":
        problems.append("the within-block weights must be tagged as policy, "
                        "not as a fitted result")
    if _fb2.FACTOR_ROLE.get("ebitda_scale") != _fb2.ROLE_ZERO_WEIGHT_CHALLENGER:
        problems.append("ebitda_scale must stay a zero-weight challenger")
    if _fb2.validate():
        problems.append("pit_factor_blocks.validate() is not clean")

    if FREEZE_V1["digest"] == digest():
        problems.append("the successor digest equals the superseded one; the "
                        "correction did not reach the components")

    import pit_score_signature as _sig
    if _sig.V2_MAJOR_WEIGHTS.get(_sig.V2_BLOCK_EBITDA) != 0.35:
        problems.append("the successor must be EBITDA-centred")
    try:
        assert_weight_semantics(_sig.V2_WEIGHT_SEMANTICS, "pit_score_signature")
    except ValueError as exc:
        problems.append("the weight-semantic invariant failed: %s" % (exc,))
    for bad, why in (
            ((("V", "valuation", 0.35), ("G", "real_growth", 0.15),
              ("E", "ebitda_strength", 0.25), ("Q", "financial_quality", 0.10)),
             "the exact defect: the vector is right and valuation carries .35"),
            ({"valuation": 0.25, "growth": 0.15, "ebitda_strength": 0.35,
              "financial_quality": 0.10},
             "a v1 pillar name smuggled into a v2 table")):
        try:
            assert_weight_semantics(bad, "probe")
        except ValueError:
            pass
        else:
            problems.append("the invariant accepted %s" % (why,))

    import pit_factor_blocks as _fb
    if _fb.FACTOR_BLOCK.get("ebitda_growth") != _fb.BLOCK_E:
        problems.append("ebitda_growth must be owned by E only")
    if _fb.MIN_BLOCK_WEIGHT <= max(_sig.V2_MAJOR_WEIGHTS.values()):
        problems.append("the block floor must exceed the largest single block")
    if not SCORE_ASSEMBLY_GAP.get("resolved_2026_09_21"):
        problems.append("the closed blockers must stay on record")

    if SCORE_ASSEMBLY_GAP["rows_written_before_it_was_found"] != 0:
        problems.append("the gap record must state it was found before any "
                        "row was written, or it is a different story")
    return problems


def main() -> int:
    print(report())
    print()
    problems = validate()
    print("%d problems / %s" % (len(problems), "FAIL" if problems else "PASS"))
    for p in problems:
        print("   - %s" % p)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
