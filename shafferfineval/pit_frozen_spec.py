"""pit_frozen_spec -- the Shaffer Equity v2 SPECIFICATION freeze, 2026-09-21.

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

`COMPONENTS` names every policy version, calibrated constant AND -- from v4 --
every canonical BODY (factor specs, normalization registry, direction table,
block map, within-block weights, valuation subfactors, block floor, debt-rung
policy, anchor policy) the specification is made of, read live from the module
that owns it. v1-v3 hashed version strings for the spec bodies; v3 was
superseded when that was shown to let economics drift under an intact digest. `digest()`
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
    "FREEZE_V3", "STATUS_SUPERSEDED", "REASON_DIGEST_COVERED_VERSION_STRINGS",
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


SPEC_FREEZE_VERSION = "spec_freeze_v4"

#: The survivorship limitation is IN THE NAME. Not in a footnote, not in a
#: column, not in a README -- in the string every score row will carry.
FROZEN_MODEL_VERSION = "equity_shaffer_v2_pit_SURVIVOR_ONLY_DIAGNOSTIC"

FROZEN_AT = "2026-09-22"

SAMPLE_SCOPE = "SURVIVOR_ONLY_DIAGNOSTIC"


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

#: (module, attribute, what it governs). A value that is not a str/int/float
#: is hashed by its repr, so a structural change is caught too.
COMPONENTS: tuple[tuple[str, str, str], ...] = (
    # the model identity
    ("pit_factor_spec", "CANDIDATE_MODEL_VERSION", "candidate lineage id"),
    ("pit_score_signature", "SIGNATURE_VERSION", "score comparability"),
    ("pit_score_signature", "COMPANY_SCALE_V2", "company score scale, +/-85"),
    ("pit_score_signature", "V2_MAJOR_WEIGHTS", "the block weight TABLE"),
    ("pit_score_signature", "PILLARS", "the block SET"),
    ("pit_factor_blocks", "FACTOR_BLOCK", "the factor-to-block MAP"),
    ("pit_factor_blocks", "BLOCK_MAP_VERSION", "the map policy version"),
    ("pit_factor_blocks", "MIN_BLOCK_WEIGHT", "the block-coverage floor"),
    ("pit_factor_blocks", "WITHIN_BLOCK_WEIGHTS", "within-block weights"),
    ("pit_factor_blocks", "POLICY_WEIGHTS_VERSION", "their policy version"),
    ("pit_factor_blocks", "POLICY_WEIGHTS_STATUS", "not empirically optimised"),
    ("pit_factor_blocks", "FACTOR_ROLE", "scoring vs zero-weight challenger"),
    ("pit_factor_blocks", "V_FACTOR_SUBFACTORS", "V granularity bridge"),
    ("pit_valuation_spec", "SUBFACTOR_WEIGHTS", "V within-block weights"),

    # ---- v4: THE BODIES. A version string cannot detect a change to the
    # thing it versions; these are the things. `name()` means: call it.
    ("pit_factor_spec", "FACTOR_SPEC_VERSION_V3", "factor spec version (v3)"),
    ("pit_factor_spec", "SPECS_V3", "the factor-spec BODIES, all 14"),
    ("pit_factor_spec", "PRICED_EPS_V2", "corrected P/E eligibility body"),
    ("pit_factor_spec", "REPLACED_BY_VERSION", "which keys v3 legitimately replaces"),
    ("pit_normalization", "candidate_v2_registry()", "the normalization registry BODY"),
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
    ("pit_valuation_spec", "DEBT_RUNG_POLICY_VERSION", "EV/EBITDA debt eligibility"),
    ("pit_valuation_spec", "DEBT_RUNG_EVEBITDA_V1", "the eligibility MAP itself"),

    # price and share basis
    ("pit_price_basis", "PRICE_BASIS_POLICY_VERSION", "raw vs adjusted price"),
    ("pit_price_basis", "EPS_PAIR_POLICY_VERSION", "the EPS pair hierarchy"),
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
FROZEN_DIGEST = "912268b3ec11bfc641fcac78078a0fb288cc51ce5432f8c27f931a532d1faae9"


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
    'pit_factor_blocks:WITHIN_BLOCK_WEIGHTS':
        '{"ebitda_strength":{"ebitda_acceleration":0.2,"ebitda_benchmark":0.35,"ebitda_efficiency":0.2,"ebitda_growth":0.25,"ebitda_scale":0.0},"financial_quality":{"debt_market_cap":0.1,"fcf_conversion":0.35,"interest_coverage":0.3,"net_debt_ebitda":0.25},"real_growth":{"real_revenue_growth":1.0},"valuation":{}}',
    'pit_factor_spec:CANDIDATE_MODEL_VERSION':
        "'candidate_equity_shaffer_v2'",
    'pit_factor_spec:FACTOR_SPEC_VERSION_V2':
        "'factor_spec_v2'",
    'pit_factor_spec:FACTOR_SPEC_VERSION_V3':
        "'factor_spec_v3'",
    'pit_factor_spec:PRICED_EPS_V2':
        '{"lags":[0],"matched_period":[],"positive_screen":"earnings_per_share_pit > 0","price_conditional_terms":["earnings_per_share_pit","price_adjusted","requires_price"],"primitives":["price_adjusted","earnings_per_share_pit"],"requires_defensible_shares":false,"requires_price":true,"rule_id":"price_and_pit_eps_v2","unavailable_primitives":[],"value_band":null,"why":"A resolvable point-in-time price and a point-in-time EPS. NO SHARE COUNT: the per-share division is already inside the filed EPS figure, which is why this chain is two primitives where EV/EBITDA is six. EPS IS IN THIS STORE as of the pit_eps ingest: pit_eps_obs 888,486 rows, pit_eps_ttm 147,914, 1,962 of the 7,102 peers on 2019-06-28 with a TTM knowable on the date. The v1/v2 rule declared it GENUINELY_UNAVAILABLE and that declaration is now false; it is corrected here rather than bypassed in an engine."}',
    'pit_factor_spec:REPLACED_BY_VERSION':
        '{"factor_spec_v3":["ebitda_benchmark","pe_ratio"]}',
    'pit_factor_spec:SPECS_V3':
        '[{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"SCORED QUANTITY DIFFERS FROM pit_derive\'s cohort_mean_ebitda node: the graph node is a DOLLAR mean; this spec scores the MARGIN excess against the cohort\'s mean margin (pit_normalization.benchmark_margin_50_75). The cohort selection quantity is still EBITDA dollars. FACTOR_SPEC_V2_DOLLAR_BENCHMARK_AND_STALE_EPS_DECLARATION: factor_spec_v2 compares EBITDA in DOLLARS against the 50-75 cohort (a size factor the design record had already rejected) and declares point-in-time EPS unavailable although pit_eps_obs holds 888,486 rows. factor_spec_v3 corrects both. No main-store row was ever produced under v2.","graph_node":"cohort_mean_ebitda","key":"ebitda_benchmark","measured":{"band_gate_lifted":{"max":179,"mean":9.18,"median":3,"p10":1,"p25":2,"p75":6,"p90":18,"pct_ge_3":68.34},"benchmark_formed_pct":56.0,"under_the_price_conditional_rule":{"band_gate_lifted_median":1,"band_pct_ge_3":7.56,"benchmark_formed_pct":5.71,"vector_median":1,"vector_pct_ge_12":5.71},"vector":{"max":718,"mean":36.04,"median":13,"p10":5,"p25":8,"p75":23,"p90":70,"pct_ge_12":56.0,"pct_ge_3":95.2}},"member_quantity":"ebitda","min_band_members":3,"min_peer_count":12,"min_peer_why":"the benchmark is the MEAN OF THE 50-75 BAND, and the band is a quarter of the vector by construction, so three members inside the band needs twelve in the vector. 3 is the floor for the percentiles themselves (company_scoring.MIN_EBITDA_COHORT).","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"benchmark_anchored_tanh_v1","note":"SHAFFER_V1_KNOWN_LIMITATION: company_scoring.build_ebitda_peer_cohort applies EV_EBITDA_SIX here. Measured, that turns a 56.0% benchmark-formation rate into 5.7% -- the benchmark is unavailable for 94% of peer sets because of a filter that has nothing to do with the quantity it measures. factor_spec_v3: SCORED QUANTITY IS THE EBITDA MARGIN, cohort selection is unchanged. Select the 50th-75th percentile cohort by peer EBITDA dollars (member_quantity=\'ebitda\', as before); then compare EBITDA_i/Revenue_i against the cohort\'s mean margin, BENCHMARK_ANCHORED. Owner decision 2026-09-22. The dollar reading is FACTOR_SPEC_V2_KNOWN_LIMITATION.","peer_eligibility":{"lags":[0],"matched_period":["operating_income","depreciation_amortisation","revenue"],"positive_screen":"","price_conditional_terms":[],"primitives":["operating_income","depreciation_amortisation","revenue"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"ebitda_and_revenue_matched_v1","unavailable_primitives":[],"value_band":null,"why":"The EBITDA cohort, further restricted to members whose revenue resolves at the SAME (period_end, qtrs). A margin is a ratio of one period\'s numbers. MEASURED: the matched-period rule costs 0.1 pp against selecting revenue independently, so it is free -- enforce it."},"question":"What does a right-sized peer in this sector earn?","required_primitives":["operating_income","depreciation_amortisation","revenue"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"ebitda_scale divides by sector_ebitda_p50, which the graph builds from sector_ebitda_vector -- the PRICED sector\'s percentiles. The same contamination reached through a percentile instead of a band mean. SHAFFER_V1_KNOWN_LIMITATION","graph_node":"ebitda_scale","key":"ebitda_scale","measured":{"vector":{"median":13,"pct_ge_3":95.2}},"member_quantity":"ebitda","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"Shares EBITDA_ONLY with ebitda_benchmark BY REFERENCE, not by copy: two factors that must mean the same cohort cannot drift apart if they are the same object. Only the threshold differs -- a rank needs 3, a band needs 12.","peer_eligibility":{"lags":[0],"matched_period":["operating_income","depreciation_amortisation"],"positive_screen":"","price_conditional_terms":[],"primitives":["operating_income","depreciation_amortisation"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"ebitda_only_v1","unavailable_primitives":[],"value_band":null,"why":"Members of the peer set whose EBITDA resolves: operating_income AND depreciation_amortisation at a MATCHED (period_end, qtrs), each non-stale under pit_policy.is_stale, each unit=\'USD\'. NOTHING ELSE. No price, no share count, no debt, no cash. This is the sector\'s distribution of earning power, and conditioning it on tradeability would make it the PRICED sector\'s distribution -- which is what the frozen core does, and what costs it 12 members of median cohort size."},"question":"How many median peers would it take to make this company?","required_primitives":["operating_income","depreciation_amortisation"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"ebitda_margin_rank","key":"ebitda_efficiency","measured":{"matched_period_cost_pp":0.1,"vector":{"max":699,"median":12,"p10":4,"p25":8,"p75":22,"p90":66,"pct_ge_12":52.8,"pct_ge_3":94.5}},"member_quantity":"ebitda_margin","min_band_members":0,"min_peer_count":3,"min_peer_why":"a cohort MEAN, not a percentile: the core\'s floor of 3 applies.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"","peer_eligibility":{"lags":[0],"matched_period":["operating_income","depreciation_amortisation","revenue"],"positive_screen":"","price_conditional_terms":[],"primitives":["operating_income","depreciation_amortisation","revenue"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"ebitda_and_revenue_matched_v1","unavailable_primitives":[],"value_band":null,"why":"The EBITDA cohort, further restricted to members whose revenue resolves at the SAME (period_end, qtrs). A margin is a ratio of one period\'s numbers. MEASURED: the matched-period rule costs 0.1 pp against selecting revenue independently, so it is free -- enforce it."},"question":"How profitable is a typical right-sized peer, per dollar of sales?","required_primitives":["operating_income","depreciation_amortisation","revenue"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"ebitda_growth","key":"ebitda_growth","measured":{"company_joint_2024":0.5096,"independence_product_2024":0.2632,"level_cohort_for_comparison":{"median":13,"pct_ge_3":95.2},"note":"MEASURED over two CONSECUTIVE annual observations, not assumed to be the square of the one-period rate -- the square would have said 90.6% where the truth is 94.0%, because a filer that reports EBITDA once mostly reports it every year.","vector":{"max":651,"mean":34.74,"median":12,"p25":8,"p75":23,"pct_ge_12":54.0,"pct_ge_3":94.01}},"member_quantity":"ebitda_growth","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"zero_anchored_tanh_v1","note":"ZERO_ANCHORED, not ranked: growth has a true zero and a rank hands out +100s in a cohort where everyone shrank.","peer_eligibility":{"lags":[0,1],"matched_period":["operating_income","depreciation_amortisation"],"positive_screen":"","price_conditional_terms":[],"primitives":["operating_income","depreciation_amortisation"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"ebitda_t_and_t1_v1","unavailable_primitives":[],"value_band":null,"why":"EBITDA at TWO observation dates a year apart. Two unique leaves at two lags = four observations, and the cohort is the peers that have all four."},"question":"Is the earnings power larger than it was a year ago?","required_primitives":["operating_income","depreciation_amortisation"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"ebitda_acceleration","key":"ebitda_acceleration","measured":{"company_joint_2024":0.4832,"distinct_ebitda_observations":3,"if_the_shared_observation_were_charged_twice":{"median":9,"pct_ge_12":39.22,"pct_ge_3":77.97},"independence_product_2024":0.135,"note":"the second row is the COST OF THE MISTAKE, measured: 2 members of median cohort size and 8.1 points of formation rate. See OBSERVATION_DEPTH.","vector":{"max":568,"mean":30.27,"median":11,"p25":6,"p75":20,"pct_ge_12":47.44,"pct_ge_3":86.03}},"member_quantity":"ebitda_acceleration","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"zero_anchored_tanh_v1","note":"THREE EBITDA observations, not four. The eligibility rule says so in `lags`, pit_derive.leaves() says so in the flattened leaf set, and observation_lags() reads it off that set.","peer_eligibility":{"lags":[0,1,2],"matched_period":["operating_income","depreciation_amortisation"],"positive_screen":"","price_conditional_terms":[],"primitives":["operating_income","depreciation_amortisation"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"ebitda_t_t1_t2_v1","unavailable_primitives":[],"value_band":null,"why":"EBITDA at THREE observation dates: E_t, E_t-1, E_t-2. Acceleration is growth(t) minus growth(t-1) and the two growths SHARE E_t-1, so the requirement is three observations and not four. A coverage model that multiplied two growth coverages would charge for the shared middle observation twice."},"question":"Is the earnings power growing FASTER than it was?","required_primitives":["operating_income","depreciation_amortisation"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"real_revenue_growth","key":"real_revenue_growth","measured":{"revenue_vector":{"median":19,"pct_ge_12":82.0,"pct_ge_3":98.8},"two_period_joint":"UNKNOWN -- one-period availability only"},"member_quantity":"real_revenue_growth","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"zero_anchored_tanh_v1","note":"","peer_eligibility":{"lags":[0,1],"matched_period":[],"positive_screen":"","price_conditional_terms":[],"primitives":["revenue","cpi"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"revenue_two_periods_plus_cpi_v1","unavailable_primitives":[],"value_band":null,"why":"Revenue at two observation dates a year apart, each non-stale, plus the CPI VINTAGE available on the as-of date -- the deflator is taken over the growth window, not as trailing CPI at the as-of, because that error is correlated with the fiscal calendar and mis-scores the 28% of the universe that is not a December filer. CPI is 100% available on every grid date."},"question":"Did the business sell MORE, or just charge more?","required_primitives":["revenue","cpi"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"admissible_rungs":["sic4","sic3","sic2"],"fallback_rung":["sic4","sic3","sic2","office"],"forbidden_rungs":["office"],"graph_divergence":"","graph_node":"cohort_mean_ev_ebitda","key":"ev_ebitda","max_admissible_rung":"sic2","measured":{"binding_leaf":"shares_outstanding (defensible)","leave_one_out_pct_ge_3":{"all_five":34.2,"without_cash":35.0,"without_defensible_shares":61.6,"without_ebitda":46.8,"without_total_debt":51.2},"seasonality_pct_ge_3":{"april":40.6,"january":14.5,"october":19.1},"sic4_rung":{"median":1,"pct_ge_12":2.4,"pct_ge_3":22.9},"vector":{"max":95,"mean":4.0,"median":1,"p25":0,"p75":3,"p90":8,"pct_ge_12":7.0,"pct_ge_3":34.2,"pct_ge_5":18.6,"pct_ge_8":11.5}},"member_quantity":"ev_ebitda","min_band_members":3,"min_peer_count":12,"min_peer_why":"the benchmark is the MEAN OF THE 50-75 BAND, and the band is a quarter of the vector by construction, so three members inside the band needs twelve in the vector. 3 is the floor for the percentiles themselves (company_scoring.MIN_EBITDA_COHORT).","missingness_behaviour":"drop_factor_and_renormalise_major_weights","model_version":"candidate_equity_shaffer_v2","normalization_type":"benchmark_anchored_tanh_v1","note":"Infeasible for most of the cross-section, and the 40% weight drops out with it -- so the Shaffer equity model IS a 0.25G+0.20P+0.15D model (renormalised 0.4167/0.3333/0.25) for the majority of company-dates. Known BEFORE the replay. factor_spec_v2: the office rung is FORBIDDEN for this factor. Sufficient N and economic coherence are two INDEPENDENT tests and they move in opposite directions along the SIC ladder -- measured, the office rung clears 12 members 94.5% of the time and does it with cohorts of 542-1,310 issuers who share an SEC reviewer and nothing else, while sic4 clears 12 for 2.4% of peer sets. More observations do not help when the economic relationship has become meaningless, so the honest answer when no admissible rung forms a cohort is VALUATION_PEER_SET_INSUFFICIENT, which makes the score PARTIAL and not comparable with a full one (pit_score_signature.PARTIAL_SHAFFER_SCORE). The office rung remains available for BROAD DESCRIPTIVE STATISTICS; what it may not be is a company valuation peer group.","peer_eligibility":{"lags":[0],"matched_period":["operating_income","depreciation_amortisation"],"positive_screen":"ebitda > 0 and enterprise_value > 0","price_conditional_terms":["cash","price_adjusted","requires_defensible_shares","requires_price","shares_outstanding","total_debt","value_band[ev_ebitda]"],"primitives":["price_adjusted","shares_outstanding","total_debt","cash","operating_income","depreciation_amortisation"],"requires_defensible_shares":true,"requires_price":true,"rule_id":"ev_ebitda_six_primitives_v1","unavailable_primitives":[],"value_band":["ev_ebitda",0.1,300.0],"why":"A resolvable point-in-time price (pit_identity.scored_universe_as_of: a listing valid on the date, a bar with volume > 0 within 10 days, no quarantine), a DEFENSIBLE share count (pit_rawprice.class_decision under the strict policy), total_debt on concept_ladder_v2, cash, and EBITDA -- then the [0.1, 300] validity band on the resulting multiple. Six primitives and two value screens. This rule is CORRECT for a valuation factor and is exactly what does not belong anywhere near the EBITDA benchmark."},"question":"What multiple is the market paying for a right-sized peer\'s earnings?","required_primitives":["price_adjusted","shares_outstanding","total_debt","cash","operating_income","depreciation_amortisation"],"sample_scope":"SURVIVOR_ONLY_DIAGNOSTIC","spec_version":"factor_spec_v2"},{"admissible_rungs":["sic4","sic3","sic2"],"fallback_rung":["sic4","sic3","sic2","office"],"forbidden_rungs":["office"],"graph_divergence":"the graph computes pe_ratio as market_cap / net_income, so its leaves are price, SHARES and net income -- three. A filed EPS already carries the per-share division, so the spec needs price and EPS and NO share count. That is the whole reason P/E is feasible where EV/EBITDA is not: the binding leaf of the valuation chain is the defensible share count, and this factor does not touch it.","graph_node":"pe_ratio","key":"pe_ratio","max_admissible_rung":"sic2","measured":{"eps_rows_in_store":0,"proxy_note":"measured with the net_income ladder standing in for EPS -- same filers, same periods, same filings. An UPPER bound: P/E is undefined for a loss-maker and ~70% of resolvable earnings are positive.","proxy_vector":{"median":9,"pct_ge_12":36.0,"pct_ge_3":92.0},"recovery_priced_universe":{"minutes":8.4,"requests":5066,"retained_gib":0.529}},"member_quantity":"pe_ratio","min_band_members":0,"min_peer_count":3,"min_peer_why":"a cohort MEAN, not a percentile: the core\'s floor of 3 applies.","missingness_behaviour":"drop_factor_and_renormalise_major_weights","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"2.7x EV/EBITDA\'s 3-peer formation rate, 5.1x at the 12-peer bar, and FLAT across the calendar (91.6-92.3% every month) because net income carries the 15-month annual staleness bound rather than the share count\'s 4-month one. factor_spec_v2: the office rung is FORBIDDEN for this factor. Sufficient N and economic coherence are two INDEPENDENT tests and they move in opposite directions along the SIC ladder -- measured, the office rung clears 12 members 94.5% of the time and does it with cohorts of 542-1,310 issuers who share an SEC reviewer and nothing else, while sic4 clears 12 for 2.4% of peer sets. More observations do not help when the economic relationship has become meaningless, so the honest answer when no admissible rung forms a cohort is VALUATION_PEER_SET_INSUFFICIENT, which makes the score PARTIAL and not comparable with a full one (pit_score_signature.PARTIAL_SHAFFER_SCORE). The office rung remains available for BROAD DESCRIPTIVE STATISTICS; what it may not be is a company valuation peer group. factor_spec_v3: eligibility PRICED_EPS_V2 -- EPS is in the store and is no longer declared GENUINELY_UNAVAILABLE.","peer_eligibility":{"lags":[0],"matched_period":[],"positive_screen":"earnings_per_share_pit > 0","price_conditional_terms":["earnings_per_share_pit","price_adjusted","requires_price"],"primitives":["price_adjusted","earnings_per_share_pit"],"requires_defensible_shares":false,"requires_price":true,"rule_id":"price_and_pit_eps_v2","unavailable_primitives":[],"value_band":null,"why":"A resolvable point-in-time price and a point-in-time EPS. NO SHARE COUNT: the per-share division is already inside the filed EPS figure, which is why this chain is two primitives where EV/EBITDA is six. EPS IS IN THIS STORE as of the pit_eps ingest: pit_eps_obs 888,486 rows, pit_eps_ttm 147,914, 1,962 of the 7,102 peers on 2019-06-28 with a TTM knowable on the date. The v1/v2 rule declared it GENUINELY_UNAVAILABLE and that declaration is now false; it is corrected here rather than bypassed in an engine."},"question":"How many years of current earnings is the market paying?","required_primitives":["price_adjusted","earnings_per_share_pit"],"sample_scope":"SURVIVOR_ONLY_DIAGNOSTIC","spec_version":"factor_spec_v2"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"net_debt_ebitda","key":"net_debt_ebitda","measured":{"cash_vector":{"median":20,"pct_ge_3":99.9},"debt_vector":{"median":11,"pct_ge_12":45.2,"pct_ge_3":95.0}},"member_quantity":"net_debt_ebitda","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"drop_factor_and_renormalise_major_weights","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"Lower-is-better, and 94% of resolved total_debt sits on a concept_ladder_v2 LOWER-BOUND rung -- which FLATTERS the score. A percentile over that mixture must report the rung distribution beside it (pit_policy\'s v2 consumer rule).","peer_eligibility":{"lags":[0],"matched_period":["operating_income","depreciation_amortisation"],"positive_screen":"","price_conditional_terms":["cash","total_debt"],"primitives":["total_debt","cash","operating_income","depreciation_amortisation"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"net_debt_over_ebitda_v1","unavailable_primitives":[],"value_band":null,"why":"Leverage against earning power. Debt and cash are REQUIRED here because they are what the factor measures -- which is the whole distinction: a primitive belongs in a cohort\'s filter when the factor is ABOUT it, never because a neighbouring factor needed it. Price-free: this leverage ratio needs no market value at all."},"question":"How many years of earnings would clear the debt?","required_primitives":["total_debt","cash","operating_income","depreciation_amortisation"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"debt_market_cap","key":"debt_market_cap","measured":{"price_shares_vector":{"median":4,"pct_ge_12":19.5,"pct_ge_3":69.7}},"member_quantity":"debt_market_cap","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"drop_factor_and_renormalise_major_weights","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"","peer_eligibility":{"lags":[0],"matched_period":[],"positive_screen":"market_cap > 0","price_conditional_terms":["price_adjusted","requires_defensible_shares","requires_price","shares_outstanding","total_debt"],"primitives":["total_debt","price_adjusted","shares_outstanding"],"requires_defensible_shares":true,"requires_price":true,"rule_id":"debt_over_market_cap_v1","unavailable_primitives":[],"value_band":null,"why":"Leverage against MARKET value, so a price and a defensible share count are the factor\'s own subject matter. SURVIVOR_ONLY by construction."},"question":"How much leverage sits under each dollar of equity value?","required_primitives":["total_debt","price_adjusted","shares_outstanding"],"sample_scope":"SURVIVOR_ONLY_DIAGNOSTIC","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"roa","key":"roa","measured":{"net_income_marginal":[0.951,0.966,0.978],"total_assets_marginal":[1.0,1.0,1.0]},"member_quantity":"roa","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"","peer_eligibility":{"lags":[0],"matched_period":[],"positive_screen":"","price_conditional_terms":[],"primitives":["net_income","total_assets"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"net_income_and_total_assets_v1","unavailable_primitives":[],"value_band":null,"why":"Return on assets. Both are near-universal in the store (95.1-97.8% and 100% of base at the three census dates), which is why the quality legs survive where valuation does not."},"question":"How much profit does each dollar of assets generate?","required_primitives":["net_income","total_assets"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"roe","key":"roe","measured":{"equity_marginal":[0.947,0.963,0.975]},"member_quantity":"roe","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"","peer_eligibility":{"lags":[0],"matched_period":[],"positive_screen":"","price_conditional_terms":[],"primitives":["net_income","equity"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"net_income_and_equity_v1","unavailable_primitives":[],"value_band":null,"why":"Return on shareholder capital. StockholdersEquity leads the ladder; the NCI-inclusive tag is a DIFFERENT denominator and is a fallback."},"question":"How much profit does each dollar of shareholder capital generate?","required_primitives":["net_income","equity"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"interest_coverage","key":"interest_coverage","measured":{"interest_expense_marginal":[0.638,0.607,0.558],"ladder_measured":false},"member_quantity":"interest_coverage","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"","peer_eligibility":{"lags":[0],"matched_period":[],"positive_screen":"","price_conditional_terms":[],"primitives":["operating_income","interest_expense"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"operating_income_and_interest_v1","unavailable_primitives":[],"value_band":null,"why":"Interest cover. The interest_expense ladder is AUTHORED, NOT MEASURED (pit_policy marks it measured=False), so no coverage claim may be quoted for this cohort from the ladder -- only the measured marginal, 63.8/60.7/55.8% of base."},"question":"How many times over can the business pay its interest bill?","required_primitives":["operating_income","interest_expense"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"},{"fallback_rung":["sic4","sic3","sic2","office"],"graph_divergence":"","graph_node":"fcf_conversion","key":"fcf_conversion","measured":{"capex_marginal":[0.675,0.711,0.694],"ocf_marginal":[0.942,0.961,0.973]},"member_quantity":"fcf_conversion","min_band_members":0,"min_peer_count":3,"min_peer_why":"a percentile rank needs a denominator; 3 is the frozen core\'s floor for any cohort statistic.","missingness_behaviour":"report_unavailable_with_named_binding_leaf","model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","note":"","peer_eligibility":{"lags":[0],"matched_period":["operating_income","depreciation_amortisation"],"positive_screen":"","price_conditional_terms":[],"primitives":["operating_cash_flow","capex","operating_income","depreciation_amortisation"],"requires_defensible_shares":false,"requires_price":false,"rule_id":"fcf_over_ebitda_v1","unavailable_primitives":[],"value_band":null,"why":"How much of the accounting earnings turns into spendable cash. Price-free. capex is the binding leaf of the pair at 67.5/71.1/69.4% of base against operating cash flow\'s 94.2/96.1/97.3%."},"question":"How much of the accounting earnings turns into spendable cash?","required_primitives":["operating_cash_flow","capex","operating_income","depreciation_amortisation"],"sample_scope":"FULL_REPORTING_UNIVERSE","spec_version":"factor_spec_v1"}]',
    'pit_intermediates:INTERMEDIATE_SCHEMA_VERSION':
        "'pit_intermediate_v1'",
    'pit_invariants:SPEC_VERSION':
        "'economic_spec_v1'",
    'pit_normalization:NORMALIZATION_POLICY_VERSION':
        "'normalization_policy_v1'",
    'pit_normalization:candidate_v2_registry()':
        '{"EBITDAAcceleration":{"anchor_source":"zero","economic_question":"is EBITDA growth speeding up or slowing down?","feature_key":"EBITDAAcceleration","feature_role":"research_candidate","higher_is_better":true,"model_version":"candidate_equity_shaffer_v2","normalization_type":"zero_anchored_tanh_v1","notes":["input: (E_t - 2E_{t-4q} + E_{t-8q}) / Assets_{t-8q}, one common base -- two different bases give any asset-growing company a systematic negative acceleration."],"policy_version":"normalization_policy_v1","rationale":"Zero means growth held steady -- a real state that must score 0. The scale is cohort-derived because the second difference carries 1.73x the sd of a single difference, so a fixed g borrowed from growth would saturate it.","scale":{"fallback_fixed":null,"fixed_value":null,"min_cohort":8,"multiplier":1.0,"source":"cohort_iqr_then_mad","winsorize_cohort":false},"scale_source":"cohort_iqr_then_mad"},"EBITDAExcessLevel":{"anchor_source":"cohort_p50_p75_mean_margin","economic_question":"does it clear the 50-75 cohort\'s profitability bar?","feature_key":"EBITDAExcessLevel","feature_role":"research_candidate","higher_is_better":true,"model_version":"candidate_equity_shaffer_v2","normalization_type":"benchmark_anchored_tanh_v1","notes":["one cohort IQR clear of the bar scores +76.2; two IQRs +96.4.","measured collapse: rank(margin - M_s) == rank(margin), exactly."],"policy_version":"normalization_policy_v1","rationale":"margin - M_s is a cohort-constant shift, so under a rank it scores identically to the margin itself and the benchmark contributes nothing. Anchored, M_s sets where zero is, which is the only way the bar survives into the score.","scale":{"fallback_fixed":0.06,"fixed_value":null,"min_cohort":8,"multiplier":1.0,"source":"cohort_iqr_then_mad","winsorize_cohort":false},"scale_source":"cohort_iqr_then_mad"},"EBITDAGrowth":{"anchor_source":"zero","economic_question":"is the business generating more EBITDA than a year ago?","feature_key":"EBITDAGrowth","feature_role":"research_candidate","higher_is_better":true,"model_version":"candidate_equity_shaffer_v2","normalization_type":"zero_anchored_tanh_v1","notes":["input: (E_t - E_{t-4q}) / Assets_{t-4q}"],"policy_version":"normalization_policy_v1","rationale":"Growth has a true zero and a meaningful sign; a rank would hand out +100s in a cohort where every company shrank. The input is an amount over a common asset base, not a rate, because a rate is meaningless on the 37.8% with EBITDA <= 0.","scale":{"fallback_fixed":null,"fixed_value":null,"min_cohort":8,"multiplier":1.0,"source":"cohort_iqr_then_mad","winsorize_cohort":false},"scale_source":"cohort_iqr_then_mad"},"EBITDAMarginRank":{"anchor_source":"cohort_rank_position","economic_question":"how efficient versus the whole sector?","feature_key":"EBITDAMarginRank","feature_role":"research_candidate","higher_is_better":true,"model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","notes":["v1 scored this by rank too; unchanged on purpose."],"policy_version":"normalization_policy_v1","rationale":"Margin is an intensity, so ranking it against the sector is the right question and the units carry no extra meaning.","scale":null,"scale_source":"none"},"EBITDAScale":{"anchor_source":"cohort_rank_position","economic_question":"genuine economic scale versus the sector","feature_key":"EBITDAScale","feature_role":"research_candidate","higher_is_better":true,"model_version":"candidate_equity_shaffer_v2","normalization_type":"percentile_rank_v1","notes":["the log is inert under a rank; it is not applied."],"policy_version":"normalization_policy_v1","rationale":"Rank the raw dollars. A rank is invariant to any strictly monotone transform, so log1p is decorative here -- measured, S(EBITDA) == S(log1p(EBITDA)) to the digit -- while being undefined for the 37.8% of the universe with EBITDA <= 0.","scale":null,"scale_source":"none"},"RealRevenueGrowth":{"anchor_source":"zero","economic_question":"is real growth positive?","feature_key":"RealRevenueGrowth","feature_role":"research_candidate","higher_is_better":true,"model_version":"candidate_equity_shaffer_v2","normalization_type":"zero_anchored_tanh_v1","notes":["deflate over the GROWTH WINDOW, not by trailing CPI at the as-of: the error is correlated with fiscal calendar and mis-scores the 28% of the universe that is not a December filer."],"policy_version":"normalization_policy_v1","rationale":"Under a rank the deflator is inert: nominal, minus-pi and divided-by-(1+pi) score identically to the digit, so +4% growth into 9% inflation still scores -33.333. Anchored at zero, -5% real scores -46.2 and the subtraction finally means something.","scale":{"fallback_fixed":null,"fixed_value":0.1,"min_cohort":8,"multiplier":1.0,"source":"fixed","winsorize_cohort":false},"scale_source":"fixed"}}',
    'pit_policy:LADDER_VERSION':
        "'concept_ladder_v1'",
    'pit_policy:LADDER_VERSION_V2':
        "'concept_ladder_v2'",
    'pit_policy:LATENCY_POLICY_VERSION':
        "'information_latency_policy_v1'",
    'pit_price_basis:ACTION_GATE_VERSION':
        "'pit_safe_corporate_action_gate_v1'",
    'pit_price_basis:EPS_GROWTH_MIN_POSITIVE_BASE_V1':
        '0.01',
    'pit_price_basis:EPS_PAIR_POLICY_VERSION':
        "'eps_pair_basis_policy_v1'",
    'pit_price_basis:PRICE_BASIS_POLICY_VERSION':
        "'price_basis_policy_v1'",
    'pit_price_basis:PRICE_ROLE_REQUIRED_BASIS':
        '{"return_or_label":"price_action_adjusted","valuation":"price_raw_as_traded"}',
    'pit_score_signature:COMPANY_SCALE_V2':
        '0.85',
    'pit_score_signature:PILLARS':
        '["valuation","growth","profitability","debt"]',
    'pit_score_signature:SIGNATURE_VERSION':
        "'score_signature_v1'",
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
    'pit_valuation_spec:PE_ABSOLUTE_ANCHOR_V1':
        '20.0',
    'pit_valuation_spec:PRESENCE_RULE_VERSION':
        "'valuation_presence_rule_v1'",
    'pit_valuation_spec:SUBFACTORS':
        '{"ev_ebitda_supplement":{"anchor_source":"cohort_median_multiple","can_stand_alone":true,"code":"EVEBITDA","depends_on":null,"factor_spec_key":"ev_ebitda","key":"ev_ebitda_supplement","label":"EV/EBITDA supplement","normalization_type":"benchmark_anchored_tanh_v1","quality_components":["source_quality","freshness","peer_coherence","primitive_coverage"],"required_primitives":["price_raw_as_traded","shares_outstanding","total_debt","cash","operating_income","depreciation_amortisation"],"requires_cohort":true,"role":"supplement","sample_scope":"SURVIVOR_ONLY_DIAGNOSTIC","weight":0.2,"why":"Capital-structure-neutral, and the ONE leg that survives a loss. FY2014, n=2,756: NI <= 0 for 49.2%, EBITDA <= 0 for 37.8%, both for 36.5% -- so 12.7% of the cross-section has an EV/EBITDA and NO P/E, and refusing those companies a valuation pillar would be refusing a reading we actually have. STANDALONE for that reason, LAST in weight because it is the rarest: median cohort of 1, P(N>=3) 34.2%, P(N>=12) 7.0%, and 2.4% at sic4. Its debt leaf also carries the ladder-v2 lower-bound mixture -- 94% of the v2 recovery understates debt by a median 14%, which FLATTERS an enterprise value."},"pe_absolute":{"anchor_source":"fixed_market_pe_anchor","can_stand_alone":true,"code":"PE","depends_on":null,"factor_spec_key":"pe_ratio","key":"pe_absolute","label":"P/E absolute valuation","normalization_type":"benchmark_anchored_tanh_v1","quality_components":["source_quality","freshness","primitive_coverage"],"required_primitives":["price_raw_as_traded","earnings_per_share_pit"],"requires_cohort":false,"role":"anchor","sample_scope":"SURVIVOR_ONLY_DIAGNOSTIC","weight":0.5,"why":"TWO LEAVES, NO COHORT. The share division is already inside the filed EPS, so this leg does not touch the defensible share count -- the binding leaf of the whole valuation chain (leave-one-out: EV/EBITDA P(N>=3) 34.2% -> 61.6% when it is dropped). It is the ANCHOR because an extreme multiple must be penalised on its own terms: a 100x P/E is expensive whether or not thirty comparable issuers could be found, and waiting for peers is how a thin SIC4 band becomes a free pass."},"pe_relative":{"anchor_source":"cohort_median_multiple","can_stand_alone":false,"code":"PEPeer","depends_on":"pe_absolute","factor_spec_key":"pe_ratio","key":"pe_relative","label":"P/E relative context","normalization_type":"benchmark_anchored_tanh_v1","quality_components":["source_quality","freshness","peer_coherence","primitive_coverage"],"required_primitives":["price_raw_as_traded","earnings_per_share_pit","coherent_peer_cohort"],"requires_cohort":true,"role":"context","sample_scope":"SURVIVOR_ONLY_DIAGNOSTIC","weight":0.3,"why":"The SAME multiple, asked a DIFFERENT question: not \'is this expensive\' but \'is this expensive FOR ITS INDUSTRY\'. Structurally dependent -- it ranks the number the absolute leg computes, so it cannot exist without it, and `presence()` enforces that rather than trusting a caller. Its cohort must clear BOTH of `pit_factor_spec`\'s gates (sufficient N and economic coherence, ceiling sic2): the SEC Office rung clears twelve members 94.5% of the time with 542-1,310 issuers whose only shared property is their filing reviewer, and that is not a valuation peer group."}}',
    'pit_valuation_spec:SUBFACTOR_WEIGHTS':
        '{"ev_ebitda_supplement":0.2,"pe_absolute":0.5,"pe_relative":0.3}',
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
        "SPECIFICATION complete as of spec_freeze_v4; ENGINE not. The pilot "
        "engine (a) holds a whole cross-section in memory before its first "
        "insert -- root cause of the 2026-09-21 pagefile incident on a 7.45 "
        "GiB machine; (b) derives eligibility from factor_spec_v2, which "
        "refuses P/E by a stale declaration; (c) computes 5 of 13 factors. "
        "It must derive from factor_spec_v3, stream in bounded batches with a "
        "measured RSS profile, and compute all 13 before the four-date pilot "
        "is rerun. No replay authorisation exists."),
    "the_machine": (
        "4.3 GiB free at 99%% used, 1.0 GiB free RAM, pagefile 11.87 GiB on "
        "disk with 2.47 GiB in use, no reboot since 2026-09-16. Reboot "
        "authorised 2026-09-22; no memory test may run before it."),
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

    "open_decision_D3": {
        "status": "UNDECIDED -- owner instruction 2026-09-22: report, do not infer",
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
    if len({FREEZE_V1["digest"], FREEZE_V2["digest"], FREEZE_V3["digest"],
            digest()}) != 4:
        problems.append("the four freezes must have four distinct digests")

    # v4 digests BODIES. Prove it: the body components are present, and the
    # digest moves when a body moves with no version string touched.
    keys = {"%s:%s" % (m, a) for m, a, _ in COMPONENTS}
    for need in ("pit_factor_spec:SPECS_V3",
                 "pit_normalization:candidate_v2_registry()",
                 "pit_factor_blocks:FACTOR_DIRECTION_V1",
                 "pit_valuation_spec:SUBFACTORS"):
        if need not in keys:
            problems.append("v4 must digest %s by body" % need)
    man = manifest()
    if any(v.startswith("unrenderable:") or "unrenderable:" in v
           for v in man.values()):
        problems.append("a body component contains something the canonical "
                        "serializer could not render deterministically")
    import pit_factor_spec as _fs
    b3 = _fs.BY_KEY_V3["ebitda_benchmark"]
    if "revenue" not in b3.required_primitives:
        problems.append("v4 must carry the MARGIN reading of ebitda_benchmark")
    if _fs.BY_KEY_V3["pe_ratio"].peer_eligibility.unavailable_primitives != ():
        problems.append("v4 must carry the corrected P/E eligibility")
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
