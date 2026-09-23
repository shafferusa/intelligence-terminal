"""
==============================================================================
test_pit_replay -- the replay engine, checked where it can actually be wrong
==============================================================================

    python test_pit_replay.py            fast: no main-store read, no writes
                                         outside the system temp directory
    python test_pit_replay.py --slow     adds ONE real cross-section slice
                                         (read-only on the store, 5 entities)

WHAT IS ACTUALLY BEING TESTED, and why each one exists

  THE GATE REFUSES A PERTURBED FREEZE, AND REFUSES BEFORE THE FIRST WRITE.
    Two independent in-memory perturbations of the frozen specification, each
    restored in a `finally`. The test asserts not merely that `run_pilot`
    raised, but that NO pilot database file exists afterwards -- because a
    refusal that happens after a connection is opened has already allocated a
    WAL, and a WAL is the failure mode this volume has suffered once.
    It also asserts the perturbed digest DIFFERED from the restored one, so
    "restored" is not a coincidence.

  THE ZERO-WEIGHT CHALLENGER CHANGES NOTHING.
    `ebitda_scale` is fed to `block_score` on every entity-date. The test
    computes the block score, the within-block coverage, the availability
    decision, the subfactor count and the whole company score WITH the
    challenger at +100, WITH it at -100, and WITHOUT it, and asserts all three
    agree to the digit. A weight of zero that is merely believed is not a
    weight of zero.

  THE ARITHMETIC RECONCILES BY HAND.
    One company's E block and company score are recomputed from the literal
    weights, with the division written out, and compared to the engine's own
    assembly path. This is the check that would catch a renormalisation
    creeping across blocks.

  A REFUSAL IS A ROW, NOT A GAP.
    Every NOT_COMPUTABLE and REFUSED_AMBIGUOUS factor must reach `pit_feature`
    with `availability='unavailable'`, `normalized_value IS NULL`,
    `available_flag=0` and a reason from the declared vocabulary.

  THE STORE IS READ-ONLY.
    `open_store()` is opened and then asked to write. The write must fail.

  THE SCHEMA ACCEPTS WHAT THE ENGINE WRITES.
    The three column tuples are checked against a real pilot database built by
    the declaring modules, and the immutability triggers, the uniqueness
    constraints and the foreign keys are each provoked deliberately.
"""

from __future__ import annotations

import inspect
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import traceback
from typing import Any, Callable, Dict, List, Optional

import pit_factor_blocks
import pit_frozen_spec
import pit_normalization
import pit_policy
import pit_replay
import pit_replay_manifest
import pit_score_signature
import pit_store
import pit_valuation_spec

PASS = 0
FAIL = 0
SKIP = 0
FAILURES: List[str] = []
TMPDIR: Optional[str] = None


def check(name: str, ok: bool, detail: Any = "") -> bool:
    global PASS, FAIL
    # `% (detail,)` and not `% detail`: a tuple detail -- and
    # `PRAGMA wal_checkpoint` returns one -- would otherwise be unpacked as
    # format arguments and raise inside the reporter, turning a PASS into a
    # traceback about string formatting.
    suffix = (" -- %s" % (detail,)) if detail else ""
    if ok:
        PASS += 1
        print("  PASS  %s%s" % (name, suffix))
    else:
        FAIL += 1
        FAILURES.append(name)
        print("  FAIL  %s%s" % (name, suffix))
    return ok


def skip(name: str, why: str) -> None:
    global SKIP
    SKIP += 1
    print("  SKIP  %s -- %s" % (name, why))


def close(a: Any, b: Any, tol: float = 1e-9) -> bool:
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


def section(title: str) -> None:
    print()
    print("-" * 78)
    print(title)
    print("-" * 78)


def run(fn: Callable[[], None]) -> None:
    try:
        fn()
    except Exception:
        global FAIL
        FAIL += 1
        FAILURES.append(fn.__name__)
        print("  FAIL  %s raised:" % fn.__name__)
        for line in traceback.format_exc().splitlines():
            print("        " + line)


# ==========================================================================
# (1) THE PLAN
# ==========================================================================

def test_validate() -> None:
    section("(1) THE PLAN -- structural self-check")
    problems = pit_replay.validate()
    check("pit_replay.validate() is clean", not problems, problems or "")

    check("13 feature keys are written per entity-date",
          len(pit_replay.REPLAY_FEATURE_KEYS) == 13,
          len(pit_replay.REPLAY_FEATURE_KEYS))

    # every company factor in the model reaches the plan, directly or via V's
    # subfactors
    mapped = set(pit_factor_blocks.FACTOR_BLOCK)
    spec_keys = {p.spec_key for p in pit_replay.FACTOR_PLAN.values() if p.spec_key}
    check("every FACTOR_BLOCK factor has a plan", mapped <= spec_keys,
          sorted(mapped - spec_keys))

    # V is at subfactor granularity and matches pit_valuation_spec exactly
    v_keys = set(pit_replay.BLOCK_KEYS[pit_factor_blocks.BLOCK_V])
    check("V's engine keys are pit_valuation_spec's subfactor keys",
          v_keys == set(pit_valuation_spec.SUBFACTOR_KEYS), sorted(v_keys))

    # the verdict census is stated, not assumed
    counts: Dict[str, int] = {}
    for plan in pit_replay.FACTOR_PLAN.values():
        counts[plan.verdict] = counts.get(plan.verdict, 0) + 1
    check("verdicts are 5 COMPUTABLE / 1 REFUSED_AMBIGUOUS / 7 NOT_COMPUTABLE",
          counts.get(pit_replay.VERDICT_COMPUTABLE) == 5
          and counts.get(pit_replay.VERDICT_REFUSED_AMBIGUOUS) == 1
          and counts.get(pit_replay.VERDICT_NOT_COMPUTABLE) == 7, counts)

    # every computable factor's declared transform really is registered
    registry = pit_normalization.candidate_v2_registry()
    bad = [k for k, p in pit_replay.FACTOR_PLAN.items()
           if p.computable and p.norm_key not in registry]
    check("every COMPUTABLE factor's norm_key is in candidate_v2_registry()",
          not bad, bad)

    # every refusal reason has a recorded sentence, and the sentence is long
    # enough to be an explanation rather than a label
    thin = [r for r in pit_replay.ENGINE_REASONS
            if len(pit_replay.REASON_WHY.get(r, "")) < 60]
    check("every engine reason carries a recorded why", not thin, thin)

    reachable = pit_replay.plan_record()["reachable_block_weight"]
    check("reachable block weight is E+G = 0.50", close(reachable, 0.50),
          reachable)
    check("0.50 clears MIN_BLOCK_WEIGHT 0.40",
          reachable >= pit_factor_blocks.MIN_BLOCK_WEIGHT,
          pit_factor_blocks.MIN_BLOCK_WEIGHT)

    check("MODEL_VERSION is the frozen one",
          pit_replay.MODEL_VERSION == pit_frozen_spec.FROZEN_MODEL_VERSION,
          pit_replay.MODEL_VERSION)
    check("SAMPLE_SCOPE is SURVIVOR_ONLY_DIAGNOSTIC",
          pit_replay.SAMPLE_SCOPE == "SURVIVOR_ONLY_DIAGNOSTIC")


def test_plan_record_is_serialisable() -> None:
    section("(1b) the plan survives the round trip into summary_json")
    blob = json.dumps(pit_replay.plan_record(), separators=(",", ":"))
    back = json.loads(blob)
    check("plan_record() is JSON round-trippable",
          back["n_feature_keys"] == 13
          and back["model_version"] == pit_replay.MODEL_VERSION,
          "%d bytes" % len(blob))
    check("the ambiguity is recorded in the plan, not only in a docstring",
          "indistinguishable from ebitda_scale"
          in back["factors"]["ebitda_benchmark"]["refusal_why"])
    check("known limitations travel with the run",
          len(back["known_limitations"]) >= 6,
          len(back["known_limitations"]))


# ==========================================================================
# (2) THE GATE -- the headline test
# ==========================================================================

def _pilot_path(name: str) -> str:
    assert TMPDIR is not None
    return os.path.join(TMPDIR, name)


def _no_db_files(path: str) -> bool:
    return not any(os.path.exists(path + s) for s in ("", "-wal", "-shm",
                                                      "-journal"))


def test_gate_refuses_perturbed_freeze() -> None:
    section("(2) THE GATE refuses a perturbed freeze BEFORE any write")

    before = pit_frozen_spec.verify()["current_digest"]
    check("freeze is intact before the test",
          before == pit_frozen_spec.FROZEN_DIGEST
          if hasattr(pit_frozen_spec, "FROZEN_DIGEST") else True, before[:12])

    # ---- perturbation (a): the coverage floor moves --------------------
    original = pit_factor_blocks.MIN_BLOCK_WEIGHT
    path = _pilot_path("gate_a.db")
    try:
        pit_factor_blocks.MIN_BLOCK_WEIGHT = 0.55
        perturbed = pit_frozen_spec.verify()
        check("(a) verify() SEES MIN_BLOCK_WEIGHT move",
              not perturbed["intact"],
              [d.get("component") for d in perturbed.get("drifted", [])][:3])
        check("(a) the perturbed digest DIFFERS from the frozen one",
              perturbed["current_digest"] != before)
        ok, reasons = pit_replay_manifest.gate()
        check("(a) gate() REFUSES", not ok,
              (reasons[0][:70] + "...") if reasons else "")
        raised = None
        try:
            pit_replay.run_pilot(["2019-06-28"], path, entity_limit=1,
                                 measure=False)
        except pit_replay.ReplayRefused as exc:
            raised = exc
        check("(a) run_pilot raises ReplayRefused", raised is not None)
        check("(a) NO pilot database was created -- the refusal preceded the "
              "first write connection", _no_db_files(path))
    finally:
        pit_factor_blocks.MIN_BLOCK_WEIGHT = original

    after_a = pit_frozen_spec.verify()["current_digest"]
    check("(a) the freeze is restored byte-identically", after_a == before,
          after_a[:12])

    # ---- perturbation (b): E and V swapped -- the 2026-09-21 defect ------
    original_weights = dict(pit_score_signature.V2_MAJOR_WEIGHTS)
    path = _pilot_path("gate_b.db")
    try:
        swapped = dict(original_weights)
        swapped[pit_score_signature.V2_BLOCK_EBITDA] = original_weights[
            pit_score_signature.V2_BLOCK_VALUATION]
        swapped[pit_score_signature.V2_BLOCK_VALUATION] = original_weights[
            pit_score_signature.V2_BLOCK_EBITDA]
        pit_score_signature.V2_MAJOR_WEIGHTS.clear()
        pit_score_signature.V2_MAJOR_WEIGHTS.update(swapped)
        perturbed = pit_frozen_spec.verify()
        check("(b) verify() SEES E and V swapped -- the same numbers in a "
              "different order is still a different model",
              not perturbed["intact"],
              [d.get("component") for d in perturbed.get("drifted", [])][:3])
        ok, _ = pit_replay_manifest.gate()
        check("(b) gate() REFUSES", not ok)
        raised = None
        try:
            pit_replay.run_pilot(["2019-06-28"], path, entity_limit=1,
                                 measure=False)
        except pit_replay.ReplayRefused as exc:
            raised = exc
        check("(b) run_pilot raises ReplayRefused", raised is not None)
        check("(b) NO pilot database was created", _no_db_files(path))
    finally:
        pit_score_signature.V2_MAJOR_WEIGHTS.clear()
        pit_score_signature.V2_MAJOR_WEIGHTS.update(original_weights)

    after_b = pit_frozen_spec.verify()["current_digest"]
    check("(b) the freeze is restored byte-identically", after_b == before,
          after_b[:12])

    ok, reasons = pit_replay_manifest.gate()
    check("the LIVE gate passes again after both restorations", ok,
          reasons[:1])


def test_free_space_guard() -> None:
    section("(2b) THE FREE-SPACE GUARD aborts before it creates anything")
    path = _pilot_path("floor.db")
    usage = shutil.disk_usage(TMPDIR or ".")
    raised = None
    try:
        pit_replay.run_pilot(["2019-06-28"], path, entity_limit=1,
                             free_floor_bytes=usage.total * 2, measure=False)
    except pit_replay.FreeSpaceAbort as exc:
        raised = exc
    check("run_pilot raises FreeSpaceAbort when free < floor at the start",
          raised is not None, str(raised)[:70] if raised else "")
    check("nothing was created", _no_db_files(path))
    check("the floor is the stated 7.0 GiB convention",
          pit_replay.FREE_FLOOR_BYTES == int(7.0 * 1024 ** 3),
          pit_replay.FREE_FLOOR_BYTES)


def test_per_date_free_space_abort() -> None:
    section("(2c) THE PER-DATE GUARD -- the floor is checked before EVERY date")
    if not os.path.exists(pit_store.DEFAULT_PIT_DB_PATH):
        skip("per-date abort", "store not present")
        return
    path = _pilot_path("abort.db")
    real = shutil.disk_usage
    calls = {"n": 0}

    class _Usage:
        def __init__(self, total: int, used: int, free: int) -> None:
            self.total, self.used, self.free = total, used, free

    pilot_dir = os.path.abspath(TMPDIR or ".")

    def fake(p: str) -> Any:
        # Only the PILOT VOLUME is faked, and only after the first reading.
        # The gate takes its own disk reading against the project directory
        # and must be left alone, or the run refuses at the gate and the
        # per-date guard is never reached -- which is how the first cut of
        # this test passed for the wrong reason.
        actual = real(p)
        if os.path.abspath(p) != pilot_dir:
            return actual
        calls["n"] += 1
        if calls["n"] == 1:
            return actual                       # the pre-run check must PASS
        return _Usage(actual.total, actual.total - 1024, 1024)

    try:
        shutil.disk_usage = fake                                    # type: ignore
        report = pit_replay.run_pilot(
            ["2019-06-28", "2022-06-30"], path, entity_limit=1, measure=False)
    finally:
        shutil.disk_usage = real                                    # type: ignore

    aborted = report.get("aborted")
    check("the run ABORTED rather than filling the volume", aborted is not None,
          (aborted or {}).get("reason"))
    check("it aborted on the FIRST date, before any entity-date was replayed",
          aborted and aborted["at_date"] == "2019-06-28"
          and aborted["entity_dates_completed"] == 0, aborted)
    check("it reports exactly how far it got",
          aborted and aborted["dates_completed"] == []
          and "free_bytes" in aborted and "floor_bytes" in aborted)
    check("no pit_feature row was written", report["totals"]["pit_feature"] == 0,
          report["totals"])
    conn = sqlite3.connect(path)
    try:
        status = conn.execute(
            "SELECT status FROM pit_replay_run").fetchone()[0]
        check("the run row records the abort rather than claiming completion",
              status == "aborted_free_space", status)
    finally:
        conn.close()


# ==========================================================================
# (3) THE ZERO-WEIGHT CHALLENGER
# ==========================================================================

def test_challenger_is_inert() -> None:
    section("(3) THE ZERO-WEIGHT CHALLENGER changes nothing, to the digit")

    E = pit_factor_blocks.BLOCK_E
    weights = pit_factor_blocks.within_block_weights(E)
    check("within_block_weights(E) OMITS ebitda_scale",
          "ebitda_scale" not in weights, sorted(weights))
    check("effective_company_weight('ebitda_scale') is exactly 0.0",
          pit_factor_blocks.effective_company_weight("ebitda_scale") == 0.0)
    check("the engine marks it a challenger on the row",
          pit_replay.FACTOR_PLAN["ebitda_scale"].role
          == pit_factor_blocks.ROLE_ZERO_WEIGHT_CHALLENGER,
          pit_replay.FACTOR_PLAN["ebitda_scale"].role)

    base = {"ebitda_benchmark": None, "ebitda_growth": 8.990807339019927,
            "ebitda_efficiency": -21.951219512195124,
            "ebitda_acceleration": -83.22171680256658}

    without = pit_factor_blocks.block_score(E, dict(base, ebitda_scale=None))
    plus = pit_factor_blocks.block_score(E, dict(base, ebitda_scale=+100.0))
    minus = pit_factor_blocks.block_score(E, dict(base, ebitda_scale=-100.0))

    check("block score is identical with the challenger at +100, -100 and absent",
          close(without["score"], plus["score"], 0.0)
          and close(without["score"], minus["score"], 0.0),
          "%r / %r / %r" % (without["score"], plus["score"], minus["score"]))
    check("the DENOMINATOR is identical",
          without["within_block_coverage"] == plus["within_block_coverage"]
          == minus["within_block_coverage"], without["within_block_coverage"])
    check("the SUBFACTOR COUNT is identical",
          without["n_scoring_present"] == plus["n_scoring_present"]
          == minus["n_scoring_present"] == 3, plus["n_scoring_present"])
    check("n_scoring_declared counts 4, not 5",
          plus["n_scoring_declared"] == 4, plus["n_scoring_declared"])
    check("the challenger is still OBSERVED, not discarded",
          plus["challengers_observed"].get("ebitda_scale") == 100.0,
          plus["challengers_observed"])

    # and the whole company score, end to end
    def company(scale: Optional[float]) -> Optional[float]:
        blocks = {
            E: pit_factor_blocks.block_score(
                E, dict(base, ebitda_scale=scale))["score"],
            pit_factor_blocks.BLOCK_V: None,
            pit_factor_blocks.BLOCK_G: 94.9569899525263,
            pit_factor_blocks.BLOCK_Q: None,
        }
        return pit_factor_blocks.assemble(blocks)["company_score"]

    check("the COMPANY SCORE is identical at +100, -100 and absent",
          company(None) == company(100.0) == company(-100.0), company(None))

    # AVAILABILITY: a block whose only present factor is the challenger is
    # UNAVAILABLE, not a block scoring +100.
    only = pit_factor_blocks.block_score(
        E, {"ebitda_benchmark": None, "ebitda_growth": None,
            "ebitda_efficiency": None, "ebitda_acceleration": None,
            "ebitda_scale": 100.0})
    check("a block carrying ONLY the challenger is UNAVAILABLE",
          only["score"] is None and only["available"] is False,
          only["unavailable_reason"])


# ==========================================================================
# (4) THE ARITHMETIC, BY HAND
# ==========================================================================

def test_arithmetic_reconciles() -> None:
    section("(4) THE ARITHMETIC -- one company recomputed by hand")

    E = pit_factor_blocks.BLOCK_E
    g, eff, acc = 8.990807339019927, -21.951219512195124, -83.22171680256658

    # E, WITHIN-block renormalisation over the three that resolved
    numerator = 0.25 * g + 0.20 * eff + 0.20 * acc
    denominator = 0.25 + 0.20 + 0.20                       # 0.65, benchmark gone
    by_hand_e = numerator / denominator
    engine_e = pit_factor_blocks.block_score(
        E, {"ebitda_benchmark": None, "ebitda_growth": g,
            "ebitda_efficiency": eff, "ebitda_acceleration": acc,
            "ebitda_scale": 77.52808988764045})["score"]
    check("E = (0.25g + 0.20eff + 0.20acc) / 0.65", close(by_hand_e, engine_e),
          "hand %.12f  engine %.12f" % (by_hand_e, engine_e))
    check("E is -28.902900658765 for the smoke-test company",
          close(engine_e, -28.902900658765166, 1e-9), engine_e)

    # the company score: NO cross-block renormalisation
    rg = 94.9569899525263
    raw_by_hand = (0.35 / 0.85) * by_hand_e + (0.15 / 0.85) * rg
    company_by_hand = 0.85 * raw_by_hand
    assembled = pit_factor_blocks.assemble({
        E: engine_e, pit_factor_blocks.BLOCK_V: None,
        pit_factor_blocks.BLOCK_G: rg, pit_factor_blocks.BLOCK_Q: None})
    check("company = 0.85 * [(0.35/0.85)E + (0.15/0.85)G] = 0.35E + 0.15G",
          close(company_by_hand, assembled["company_score"]),
          "hand %.12f  engine %.12f" % (company_by_hand,
                                        assembled["company_score"]))
    check("company score is 4.127533262311 for the smoke-test company",
          close(assembled["company_score"], 4.127533262311139, 1e-9),
          assembled["company_score"])
    check("signature is EG", assembled["signature"] == "EG",
          assembled["signature"])
    check("block mask is E=1,V=0,G=1,Q=0",
          assembled["block_mask"] == "E=1,V=0,G=1,Q=0",
          assembled["block_mask"])
    check("coverage is 0.50/0.85", close(assembled["block_coverage"],
                                         0.50 / 0.85),
          assembled["block_coverage"])
    check("NO cross-block renormalisation happened",
          assembled["renormalised"] is False)
    check("the absent blocks did NOT promote the survivors: E's effective "
          "weight is 0.35/0.85, not 0.35/0.50",
          close(assembled["effective_weights"][E], 0.35 / 0.85),
          assembled["effective_weights"])

    # and the normalisations themselves
    import math
    growth_raw, growth_k = 0.01622124717457785, 0.1799331695578683
    check("zero-anchored growth = 100*tanh(x/k)",
          close(100.0 * math.tanh(growth_raw / growth_k), g, 1e-9))
    check("real growth 0.18273879 at fixed g=0.10 scores 94.957",
          close(100.0 * math.tanh(0.1827387917218395 / 0.10), rg, 1e-9))


def test_min_block_weight_refusal() -> None:
    section("(4b) BELOW THE FLOOR: no score, and the evidence still survives")
    E = pit_factor_blocks.BLOCK_E
    G = pit_factor_blocks.BLOCK_G

    only_e = pit_factor_blocks.assemble({E: -28.9, pit_factor_blocks.BLOCK_V: None,
                                         G: None, pit_factor_blocks.BLOCK_Q: None})
    check("E alone (0.35) is BELOW the 0.40 floor -> no score",
          only_e["company_score"] is None, only_e["refused"])
    check("the refusal is named PARTIAL_SCORE_INSUFFICIENT_BLOCK_COVERAGE",
          only_e["refused"] == pit_factor_blocks.INSUFFICIENT_BLOCK_COVERAGE)
    check("the signature still records which block DID resolve",
          only_e["signature"] == "E", only_e["signature"])

    only_g = pit_factor_blocks.assemble({E: None, pit_factor_blocks.BLOCK_V: None,
                                         G: 94.9, pit_factor_blocks.BLOCK_Q: None})
    check("G alone (0.15) is below the floor -> no score",
          only_g["company_score"] is None)

    both = pit_factor_blocks.assemble({E: -28.9, pit_factor_blocks.BLOCK_V: None,
                                       G: 94.9, pit_factor_blocks.BLOCK_Q: None})
    check("E+G (0.50) clears the floor -> a score", both["company_score"] is not None,
          both["company_score"])
    check("no block can clear the floor alone -- the largest is E at 0.35",
          max(pit_score_signature.V2_MAJOR_WEIGHTS.values())
          < pit_factor_blocks.MIN_BLOCK_WEIGHT,
          "%.2f < %.2f" % (max(pit_score_signature.V2_MAJOR_WEIGHTS.values()),
                           pit_factor_blocks.MIN_BLOCK_WEIGHT))


# ==========================================================================
# (5) PRIMITIVES -- the four derived inputs, on a synthetic index
# ==========================================================================

def test_primitives_arithmetic() -> None:
    section("(5) PRIMITIVES -- the declared inputs, computed on a fixture")
    as_of = "2019-06-28"
    ladders = pit_policy.ladder_set(pit_replay.LADDER_VERSION)
    eid = 1

    index = {
        (eid, "OperatingIncomeLoss", 4): {"2018-12-31": 800.0,
                                          "2017-12-31": 600.0,
                                          "2016-12-31": 500.0},
        (eid, "DepreciationDepletionAndAmortization", 4): {"2018-12-31": 200.0,
                                                           "2017-12-31": 150.0,
                                                           "2016-12-31": 100.0},
        (eid, "Assets", 0): {"2017-12-31": 5000.0, "2016-12-31": 4000.0},
        (eid, "Revenues", 4): {"2018-12-31": 4000.0, "2017-12-31": 3600.0},
    }
    cpi_values = {"2018-12-31": 110.0, "2017-12-31": 100.0}
    prim = pit_replay.resolve_primitives(index, eid, as_of, ladders,
                                         lambda d: cpi_values.get(d))

    check("EBITDA_t = 800 + 200 = 1000", close(prim.ebitda, 1000.0), prim.ebitda)
    check("EBITDA_{t-1} = 600 + 150 = 750", close(prim.ebitda_lag1, 750.0),
          prim.ebitda_lag1)
    check("EBITDA_{t-2} = 500 + 100 = 600", close(prim.ebitda_lag2, 600.0),
          prim.ebitda_lag2)
    check("growth = (1000 - 750) / 5000 = 0.05", close(prim.growth, 0.05),
          prim.growth)
    check("acceleration = (1000 - 2*750 + 600) / 4000 = 0.025",
          close(prim.acceleration, 0.025), prim.acceleration)
    check("margin = 1000 / 4000 = 0.25", close(prim.margin, 0.25), prim.margin)
    real = (4000.0 / 110.0) / (3600.0 / 100.0) - 1.0
    check("real revenue growth deflates OVER THE WINDOW, not by trailing CPI",
          close(prim.real_revenue_growth, real),
          "%.9f (nominal was %.9f)" % (prim.real_revenue_growth,
                                       4000.0 / 3600.0 - 1.0))
    check("the deflator actually bit: real < nominal",
          prim.real_revenue_growth < (4000.0 / 3600.0 - 1.0))
    check("acceleration uses ONE base (Assets_{t-8q}), not two",
          close(prim.acceleration,
                (1000.0 - 2 * 750.0 + 600.0) / 4000.0))

    # ---- and the refusals, each with its own reason ----------------------
    thin = dict(index)
    del thin[(eid, "Assets", 0)]
    prim2 = pit_replay.resolve_primitives(thin, eid, as_of, ladders,
                                          lambda d: cpi_values.get(d))
    check("no assets -> growth refused with no_assets_at_lag1",
          prim2.growth is None
          and prim2.why.get("growth") == pit_replay.R_NO_ASSETS_LAG1,
          prim2.why.get("growth"))
    check("no assets -> acceleration refused with no_assets_at_lag2",
          prim2.acceleration is None
          and prim2.why.get("acceleration") == pit_replay.R_NO_ASSETS_LAG2,
          prim2.why.get("acceleration"))
    check("a missing ingredient NEVER becomes 0.0",
          prim2.growth is None and prim2.acceleration is None)

    no_da = {k: v for k, v in index.items()
             if k[1] != "DepreciationDepletionAndAmortization"}
    prim3 = pit_replay.resolve_primitives(no_da, eid, as_of, ladders,
                                          lambda d: cpi_values.get(d))
    check("no D&A -> no EBITDA at all, with no_ebitda_period",
          prim3.ebitda is None
          and prim3.why.get("ebitda") == pit_replay.R_NO_EBITDA,
          prim3.why.get("ebitda"))

    no_cpi = pit_replay.resolve_primitives(index, eid, as_of, ladders,
                                           lambda d: None)
    check("no CPI vintage -> real growth refused, NOT silently nominal",
          no_cpi.real_revenue_growth is None
          and no_cpi.why.get("real_revenue_growth") == pit_replay.R_NO_CPI,
          no_cpi.why.get("real_revenue_growth"))

    # a stale period must not resolve
    stale = {
        (eid, "OperatingIncomeLoss", 4): {"2014-12-31": 800.0},
        (eid, "DepreciationDepletionAndAmortization", 4): {"2014-12-31": 200.0},
    }
    prim4 = pit_replay.resolve_primitives(stale, eid, as_of, ladders,
                                          lambda d: None)
    check("a stale EBITDA period is refused by pit_policy.is_stale",
          prim4.ebitda is None, prim4.why.get("ebitda"))


def test_normalise_uses_the_declared_transform() -> None:
    section("(5b) NORMALISATION -- the factor's transform, not the caller's")
    registry = pit_normalization.candidate_v2_registry()
    check("ebitda_efficiency maps to EBITDAMarginRank (PERCENTILE_RANK)",
          registry[pit_replay.FACTOR_PLAN["ebitda_efficiency"].norm_key]
          .normalization_type == pit_normalization.PERCENTILE_RANK)
    check("ebitda_growth maps to EBITDAGrowth (ZERO_ANCHORED)",
          registry[pit_replay.FACTOR_PLAN["ebitda_growth"].norm_key]
          .normalization_type == pit_normalization.ZERO_ANCHORED)
    check("ebitda_scale ranks the RAW DOLLARS",
          registry["EBITDAScale"].normalization_type
          == pit_normalization.PERCENTILE_RANK)

    # the rank population excludes the target; the dispersion sample does not
    cohort = {1: 10.0, 2: 20.0, 3: 30.0, 4: 40.0, 5: 50.0}
    ranked = pit_replay.normalise("ebitda_scale", 30.0, cohort, 3, "2019-06-28")
    check("a rank cohort of 5 counts the target once, not twice",
          ranked.n_cohort == 5, ranked.n_cohort)
    thin_rank = pit_replay.normalise("ebitda_scale", 30.0, {1: 10.0, 2: 20.0},
                                     3, "2019-06-28")
    check("below MIN_RANK_PEERS the rank REFUSES rather than coin-flipping",
          thin_rank.score is None, thin_rank.reason)

    disp = pit_replay.normalise("ebitda_growth", 0.05,
                                {i: i / 100.0 for i in range(1, 21)},
                                1, "2019-06-28")
    check("a dispersion-scaled factor reports the scale it divided by",
          disp.score is not None and disp.scale is not None,
          "scale=%r source=%r" % (disp.scale, disp.scale_source))
    thin_disp = pit_replay.normalise("ebitda_growth", 0.05, {1: 0.01, 2: 0.02},
                                     1, "2019-06-28")
    check("below the dispersion floor it REFUSES rather than inventing a k",
          thin_disp.score is None, thin_disp.reason)

    fixed = pit_replay.normalise("real_revenue_growth", 0.0, {}, 1, "2019-06-28")
    check("zero real growth scores EXACTLY zero -- the property a rank cannot have",
          fixed.score == 0.0, fixed.score)
    check("the fixed scale is the declared g = 0.10", close(fixed.scale, 0.10),
          fixed.scale)


def test_masks() -> None:
    section("(5c) MASKS -- one state, one spelling")
    e_mask = pit_replay._mask(
        pit_factor_blocks.BLOCK_E,
        {"ebitda_growth": 1.0, "ebitda_efficiency": 1.0,
         "ebitda_acceleration": 1.0})
    check("E's mask names the FOUR scoring factors and omits the challenger",
          e_mask == "BENCH=0,GROW=1,EFF=1,ACC=1", e_mask)
    loud = pit_replay._mask(
        pit_factor_blocks.BLOCK_E,
        {"ebitda_growth": 1.0, "ebitda_efficiency": 1.0,
         "ebitda_acceleration": 1.0, "ebitda_scale": 100.0})
    check("the challenger cannot appear in the mask even when it resolved",
          loud == e_mask and "SCALE" not in loud, loud)
    v_mask = pit_replay._mask(pit_factor_blocks.BLOCK_V, {})
    check("V uses pit_valuation_spec's own spelling",
          v_mask == pit_valuation_spec.MASK_NONE, v_mask)
    q_mask = pit_replay._mask(pit_factor_blocks.BLOCK_Q, {})
    check("Q names all four", q_mask == "FCF=0,ICOV=0,NDE=0,DMC=0", q_mask)


# ==========================================================================
# (6) THE STORE IS READ-ONLY
# ==========================================================================

def test_store_is_read_only() -> None:
    section("(6) THE MAIN STORE -- read-only, twice over")
    if not os.path.exists(pit_store.DEFAULT_PIT_DB_PATH):
        skip("open_store read-only", "store not present")
        return
    conn = pit_replay.open_store()
    try:
        flag = conn.execute("PRAGMA query_only").fetchone()[0]
        check("query_only is set on the connection", int(flag) == 1, flag)
        raised = None
        try:
            conn.execute("CREATE TABLE _probe_should_never_exist (x INTEGER)")
        except sqlite3.Error as exc:
            raised = exc
        check("a CREATE TABLE on the store RAISES", raised is not None,
              str(raised)[:60] if raised else "IT DID NOT RAISE")
        raised = None
        try:
            conn.execute("INSERT INTO pit_feature (entity_id) VALUES (1)")
        except sqlite3.Error as exc:
            raised = exc
        check("an INSERT into pit_feature RAISES", raised is not None,
              str(raised)[:60] if raised else "IT DID NOT RAISE")
        n = conn.execute("SELECT count(*) FROM pit_feature").fetchone()[0]
        check("pit_feature in the MAIN store is still empty", n == 0, n)
        n = conn.execute("SELECT count(*) FROM pit_score").fetchone()[0]
        check("pit_score in the MAIN store is still empty", n == 0, n)
    finally:
        conn.close()

    raised = None
    try:
        pit_replay.open_pilot(pit_store.DEFAULT_PIT_DB_PATH)
    except Exception as exc:
        raised = exc
    check("open_pilot REFUSES the protected store basename", raised is not None,
          type(raised).__name__ if raised else "IT DID NOT RAISE")


# ==========================================================================
# (7) THE SCHEMA ACCEPTS WHAT THE ENGINE WRITES
# ==========================================================================

_SCHEMA_DB: Optional[str] = None


def _schema_db() -> str:
    global _SCHEMA_DB
    if _SCHEMA_DB is None:
        path = _pilot_path("schema.db")
        pit_replay.ensure_pilot_db(path, overwrite=True)
        _SCHEMA_DB = path
    return _SCHEMA_DB


def test_columns_match_the_schema() -> None:
    section("(7) THE COLUMN TUPLES against a real pilot database")
    conn = sqlite3.connect(_schema_db())
    try:
        for table, declared in (("pit_feature", pit_replay.FEATURE_COLUMNS),
                                ("pit_pillar_score", pit_replay.PILLAR_COLUMNS),
                                ("pit_score", pit_replay.SCORE_COLUMNS)):
            info = conn.execute("PRAGMA table_info(%s)" % table).fetchall()
            actual = {r[1] for r in info}
            unknown = set(declared) - actual
            check("%s: every declared column exists" % table, not unknown,
                  sorted(unknown))
            required = {r[1] for r in info
                        if r[3] and r[4] is None and not r[5]}   # NOT NULL, no default, not PK
            uncovered = required - set(declared)
            check("%s: every NOT NULL column without a default is written"
                  % table, not uncovered, sorted(uncovered))
        check("pit_pillar_score EXISTS in the pilot (it does not in the store)",
              conn.execute("SELECT count(*) FROM sqlite_master WHERE "
                           "name='pit_pillar_score'").fetchone()[0] == 1)
    finally:
        conn.close()


def _run_row(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "INSERT INTO pit_replay_run (started_at, as_of_grid, model_version, "
        "latency_policy_version, ladder_version, peer_set_version, status) "
        "VALUES (?,?,?,?,?,?,?)",
        ("2026-09-21T00:00:00+00:00", pit_replay.AS_OF_GRID,
         pit_replay.MODEL_VERSION, pit_replay.LATENCY_POLICY_VERSION,
         pit_replay.LADDER_VERSION, pit_replay.PEER_SET_VERSION, "test"))
    return int(cur.lastrowid)


def _feature_tuple(entity_id: int, key: str, run_id: int,
                   score: Optional[float] = None) -> tuple:
    plan = pit_replay.FACTOR_PLAN[key]
    return (entity_id, None, "2019-06-28", key, plan.role, "[]", None,
            pit_replay.LADDER_VERSION, 0, 180, "2019-06-28",
            pit_replay.LATENCY_POLICY_VERSION, None, score, None, None,
            pit_store.AVAIL_COMPLETE if score is not None
            else pit_store.AVAIL_UNAVAILABLE,
            1 if score is not None else 0,
            None if score is not None else plan.refusal_reason,
            pit_replay.MODEL_VERSION, run_id, "2026-09-21T00:00:00+00:00", "{}")


def test_write_constraints_bite() -> None:
    section("(7b) THE CONSTRAINTS -- provoked on purpose, one at a time")
    path = _pilot_path("constraints.db")
    pit_replay.ensure_pilot_db(path, overwrite=True)
    conn = pit_replay.open_pilot(path)
    try:
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        check("foreign keys are ON for the pilot writer", int(fk) == 1, fk)

        run_id = _run_row(conn)
        conn.commit()

        # FK: an unseeded entity must be refused
        raised = None
        try:
            conn.execute(pit_replay.FEATURE_SQL,
                         _feature_tuple(999999, "ebitda_growth", run_id, 1.0))
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raised = exc
            conn.rollback()
        check("a pit_feature row for an UNSEEDED entity is REFUSED by the FK",
              raised is not None,
              str(raised)[:50] if raised else "IT WAS ACCEPTED")

        conn.execute("INSERT INTO pit_entity (entity_id, cik, entity_kind, "
                     "created_at) VALUES (?,?,?,?)",
                     (999999, "0000999999", "issuer", "2026-09-21"))
        conn.commit()
        conn.execute(pit_replay.FEATURE_SQL,
                     _feature_tuple(999999, "ebitda_growth", run_id, 1.0))
        conn.commit()
        check("the same row is ACCEPTED once the parent is seeded",
              conn.execute("SELECT count(*) FROM pit_feature").fetchone()[0] == 1)

        # UNIQUE
        raised = None
        try:
            conn.execute(pit_replay.FEATURE_SQL,
                         _feature_tuple(999999, "ebitda_growth", run_id, 2.0))
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raised = exc
            conn.rollback()
        check("a DUPLICATE (entity, date, key, model, run) is REFUSED",
              raised is not None,
              str(raised)[:50] if raised else "IT WAS ACCEPTED")

        # immutability trigger
        raised = None
        try:
            conn.execute("UPDATE pit_feature SET normalized_value = 99")
            conn.commit()
        except sqlite3.Error as exc:
            raised = exc
            conn.rollback()
        check("pit_feature is IMMUTABLE -- an UPDATE raises", raised is not None,
              str(raised)[:60] if raised else "IT WAS ACCEPTED")

        # feature_available_date <= as_of_date
        bad = list(_feature_tuple(999999, "ebitda_efficiency", run_id, 1.0))
        bad[10] = "2030-01-01"
        raised = None
        try:
            conn.execute(pit_replay.FEATURE_SQL, tuple(bad))
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raised = exc
            conn.rollback()
        check("a feature knowable AFTER the as-of is REFUSED by the CHECK",
              raised is not None,
              str(raised)[:50] if raised else "IT WAS ACCEPTED")

        # pit_pillar_score: an unavailable pillar may not carry weight
        base = (999999, "2019-06-28", pit_factor_blocks.BLOCK_V,
                pit_replay.MODEL_VERSION, run_id, None, 0.25, 0.25,
                "unavailable", "no scoring factor available", 3, 0, "{}",
                pit_valuation_spec.MASK_NONE, None, None, None,
                pit_replay.SAMPLE_SCOPE, "pit_intermediate_v1", "2026-09-21")
        raised = None
        try:
            conn.execute(pit_replay.PILLAR_SQL, base)
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raised = exc
            conn.rollback()
        check("an UNAVAILABLE pillar carrying effective weight is REFUSED",
              raised is not None,
              str(raised)[:50] if raised else "IT WAS ACCEPTED")
        good = list(base)
        good[7] = 0.0
        conn.execute(pit_replay.PILLAR_SQL, tuple(good))
        conn.commit()
        check("the same pillar at effective_weight 0.0 is ACCEPTED",
              conn.execute("SELECT count(*) FROM pit_pillar_score"
                           ).fetchone()[0] == 1)

        # sample_scope vocabulary
        wrong = list(good)
        wrong[2] = pit_factor_blocks.BLOCK_G
        wrong[17] = "WHATEVER"
        raised = None
        try:
            conn.execute(pit_replay.PILLAR_SQL, tuple(wrong))
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raised = exc
            conn.rollback()
        check("a sample_scope outside the declared vocabulary is REFUSED",
              raised is not None,
              str(raised)[:50] if raised else "IT WAS ACCEPTED")
    finally:
        conn.close()


def test_unavailable_rows_are_rows() -> None:
    section("(7c) A REFUSAL IS A ROW -- written, not skipped")
    path = _pilot_path("refusals.db")
    pit_replay.ensure_pilot_db(path, overwrite=True)
    conn = pit_replay.open_pilot(path)
    try:
        run_id = _run_row(conn)
        conn.execute("INSERT INTO pit_entity (entity_id, cik, entity_kind, "
                     "created_at) VALUES (?,?,?,?)",
                     (7, "0000000007", "issuer", "2026-09-21"))
        conn.commit()
        refused = [k for k, p in pit_replay.FACTOR_PLAN.items()
                   if not p.computable]
        conn.executemany(pit_replay.FEATURE_SQL,
                         [_feature_tuple(7, k, run_id) for k in refused])
        conn.commit()
        rows = conn.execute(
            "SELECT feature_key, raw_value, normalized_value, availability, "
            "available_flag, unavailable_reason FROM pit_feature").fetchall()
        check("every NOT_COMPUTABLE / REFUSED factor produced a row",
              len(rows) == len(refused) == 8, len(rows))
        check("every one is availability='unavailable'",
              all(r[3] == pit_store.AVAIL_UNAVAILABLE for r in rows))
        check("every one has normalized_value NULL -- never 0.0",
              all(r[2] is None for r in rows))
        check("every one has available_flag 0",
              all(r[4] == 0 for r in rows))
        vocabulary = set(pit_replay.ENGINE_REASONS)
        check("every reason is in the declared vocabulary",
              all(r[5] in vocabulary for r in rows),
              sorted({r[5] for r in rows}))
        by_key = {r[0]: r[5] for r in rows}
        check("ebitda_benchmark is refused for AMBIGUITY, not for missing data",
              by_key["ebitda_benchmark"] == pit_replay.R_BENCHMARK_AMBIGUOUS,
              by_key["ebitda_benchmark"])
        check("pe_absolute and pe_relative name the genuinely absent primitive",
              by_key["pe_absolute"] == by_key["pe_relative"]
              == pit_replay.R_PRIMITIVE_UNAVAILABLE)
        check("the four Q factors and EV/EBITDA name the missing transform",
              all(by_key[k] == pit_replay.R_NO_V2_NORMALIZATION
                  for k in ("fcf_conversion", "interest_coverage",
                            "net_debt_ebitda", "debt_market_cap",
                            "ev_ebitda_supplement")))
    finally:
        conn.close()


def test_checkpoint_return_is_read() -> None:
    section("(8) THE CHECKPOINT -- the return value is READ, not assumed")
    path = _pilot_path("checkpoint.db")
    pit_replay.ensure_pilot_db(path, overwrite=True)
    conn = pit_replay.open_pilot(path)
    try:
        run_id = _run_row(conn)
        conn.execute("INSERT INTO pit_entity (entity_id, cik, entity_kind, "
                     "created_at) VALUES (?,?,?,?)",
                     (7, "0000000007", "issuer", "2026-09-21"))
        conn.commit()
        wal = path + "-wal"
        wal_before = os.path.getsize(wal) if os.path.exists(wal) else 0
        row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        check("wal_checkpoint returns a 3-tuple", row is not None and len(row) == 3,
              tuple(row) if row else None)
        busy, log_pages, checkpointed = int(row[0]), int(row[1]), int(row[2])
        wal_after = os.path.getsize(wal) if os.path.exists(wal) else 0
        check("a SUCCESSFUL TRUNCATE returns (0, 0, 0) -- that is not a no-op",
              (busy, log_pages, checkpointed) == (0, 0, 0),
              "(%d, %d, %d), wal %d -> %d" % (busy, log_pages, checkpointed,
                                              wal_before, wal_after))
        check("the WAL is what shows the work", wal_after <= wal_before,
              "%d -> %d bytes" % (wal_before, wal_after))
    finally:
        conn.close()

    # the busy-refusal signature, evaluated on the tuple the engine would see
    def is_busy_refusal(b: int, lp: int, cp: int) -> bool:
        return bool(b == 1 and lp > 0 and cp == 0)

    check("(1, 12, 0) IS the busy-refusal signature", is_busy_refusal(1, 12, 0))
    check("(0, 0, 0) is NOT a busy refusal", not is_busy_refusal(0, 0, 0))
    check("(1, 0, 0) is NOT a busy refusal -- nothing was there to checkpoint",
          not is_busy_refusal(1, 0, 0))
    check("(0, 12, 12) is NOT a busy refusal", not is_busy_refusal(0, 12, 12))


# ==========================================================================
# (9) THE SLOW ONE -- a real slice, read-only on the store
# ==========================================================================

def test_real_slice() -> None:
    section("(9) A REAL SLICE -- one date, 5 entities, read-only on the store")
    if not os.path.exists(pit_store.DEFAULT_PIT_DB_PATH):
        skip("real slice", "store not present")
        return
    path = _pilot_path("slice.db")
    report = pit_replay.run_pilot(["2019-06-28"], path, entity_limit=5,
                                  measure=False)
    stats = report["dates"][0]
    check("the gate passed and the run completed", report["gate"] == "PASSED"
          and not report["aborted"])
    check("the freeze is unchanged by the run", report["freeze_unchanged"],
          report["freeze_digest_after"][:12])
    check("pit_feature got 13 rows per entity-date",
          stats["rows"]["pit_feature"] == 13 * stats["n_targets"],
          stats["rows"])
    check("pit_pillar_score got 4 rows per entity-date",
          stats["rows"]["pit_pillar_score"] == 4 * stats["n_targets"])
    check("pit_score got at most one row per entity-date",
          stats["rows"]["pit_score"] <= stats["n_targets"])
    check("scored + refused == entity-dates",
          stats["n_scored"] + stats["n_refused_insufficient_block_coverage"]
          == stats["n_targets"],
          "%d + %d vs %d" % (stats["n_scored"],
                             stats["n_refused_insufficient_block_coverage"],
                             stats["n_targets"]))
    check("the checkpoint return value was read",
          stats["checkpoint"].get("return_read") is True, stats["checkpoint"])

    conn = sqlite3.connect(path)
    try:
        versions = {r[0] for r in conn.execute(
            "SELECT DISTINCT model_version FROM pit_feature "
            "UNION SELECT DISTINCT model_version FROM pit_pillar_score "
            "UNION SELECT DISTINCT model_version FROM pit_score")}
        check("every row carries the frozen model version",
              versions == {pit_replay.MODEL_VERSION}, versions)
        scopes = {r[0] for r in conn.execute(
            "SELECT DISTINCT sample_scope FROM pit_pillar_score")}
        check("every pillar row carries SURVIVOR_ONLY_DIAGNOSTIC",
              scopes == {pit_replay.SAMPLE_SCOPE}, scopes)
        n_challenger = conn.execute(
            "SELECT count(*) FROM pit_feature WHERE feature_role = ?",
            (pit_factor_blocks.ROLE_ZERO_WEIGHT_CHALLENGER,)).fetchone()[0]
        check("one challenger row per entity-date",
              n_challenger == stats["n_targets"], n_challenger)
        leaked = conn.execute(
            "SELECT count(*) FROM pit_pillar_score WHERE "
            "subfactors_json LIKE '%ebitda_scale%' AND n_subfactors_declared != 4"
        ).fetchone()[0]
        check("the challenger never entered a declared-subfactor count",
              leaked == 0, leaked)
        digests = {json.loads(r[0]).get("freeze_digest") for r in conn.execute(
            "SELECT provenance_json FROM pit_score")} or {"(no score rows)"}
        check("every score row carries the freeze digest",
              digests in ({report["freeze_digest"]}, {"(no score rows)"}),
              digests)
    finally:
        conn.close()


# ==========================================================================
# (10) THE STREAMED WRITE SIDE
# ==========================================================================

def test_batching_is_declared() -> None:
    section("(10a) STREAMING -- declared, bounded, and switchable off")
    check("DEFAULT_BATCH_SIZE is a positive integer",
          isinstance(pit_replay.DEFAULT_BATCH_SIZE, int)
          and pit_replay.DEFAULT_BATCH_SIZE > 0, pit_replay.DEFAULT_BATCH_SIZE)
    params = inspect.signature(pit_replay.run_date).parameters
    check("run_date takes batch_size, defaulting to DEFAULT_BATCH_SIZE",
          "batch_size" in params
          and params["batch_size"].default == pit_replay.DEFAULT_BATCH_SIZE)
    check("run_date takes free_floor_bytes for the per-batch guard",
          "free_floor_bytes" in params)
    run_params = inspect.signature(pit_replay.run_pilot).parameters
    check("run_pilot takes batch_size and passes it down",
          "batch_size" in run_params
          and run_params["batch_size"].default == pit_replay.DEFAULT_BATCH_SIZE)
    check("FreeSpaceAbort is the engine's own abort",
          issubclass(pit_replay.FreeSpaceAbort, RuntimeError))


def _table_rows(path: str, table: str, order_by: str) -> List[tuple]:
    """Every column except the autoincrement id and created_at, in key order."""
    conn = sqlite3.connect(path)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)
                if not (r[5] == 1 and str(r[2]).upper() == "INTEGER")
                and r[1] != "created_at"]
        return [tuple(r) for r in conn.execute(
            "SELECT %s FROM %s ORDER BY %s"
            % (", ".join(cols), table, order_by))]
    finally:
        conn.close()


def test_streaming_equals_legacy() -> None:
    section("(10b) THE EQUALITY ORACLE -- same slice, batched vs one write")
    if not os.path.exists(pit_store.DEFAULT_PIT_DB_PATH):
        skip("streaming oracle", "store not present")
        return
    legacy_path = _pilot_path("oracle_legacy.db")
    stream_path = _pilot_path("oracle_stream.db")
    n, batch = 50, 7                  # 50 targets in 8 batches, the last short
    legacy = pit_replay.run_pilot(["2019-06-28"], legacy_path, entity_limit=n,
                                  measure=False, batch_size=None)
    stream = pit_replay.run_pilot(["2019-06-28"], stream_path, entity_limit=n,
                                  measure=False, batch_size=batch)
    check("both runs completed without an abort",
          not legacy["aborted"] and not stream["aborted"],
          (legacy["aborted"], stream["aborted"]))
    ls, ss = legacy["dates"][0], stream["dates"][0]
    check("the unbatched run wrote in ONE batch", ls["n_batches"] == 1,
          ls["n_batches"])
    check("the streamed run wrote in ceil(50/7) = 8 batches",
          ss["n_batches"] == 8, ss["n_batches"])
    for key in ("rows", "n_targets", "n_entities_indexed", "n_scored",
                "n_refused_insufficient_block_coverage", "n_no_peer_set",
                "n_challenger_rows", "blocks_resolved", "signatures",
                "availability", "reasons", "n_eligible_peer_calls"):
        check("per-date statistic %r is identical" % key, ls[key] == ss[key],
              "" if ls[key] == ss[key] else (ls[key], ss[key]))
    for table, order in (("pit_feature", "entity_id, as_of_date, feature_key"),
                         ("pit_pillar_score", "entity_id, as_of_date, pillar"),
                         ("pit_score", "entity_id, as_of_date")):
        a = _table_rows(legacy_path, table, order)
        b = _table_rows(stream_path, table, order)
        same = a == b
        detail: Any = "%d rows" % len(a)
        if not same:
            detail = next(
                ("row %d differs: %r vs %r" % (i, x, y)
                 for i, (x, y) in enumerate(zip(a, b)) if x != y),
                "row counts differ: %d vs %d" % (len(a), len(b)))
        check("%s rows are IDENTICAL (ids and created_at excluded)" % table,
              same, detail)
    check("both checkpoints read their return value",
          ls["checkpoint"].get("return_read") is True
          and ss["checkpoint"].get("return_read") is True)
    check("the freeze is unchanged by both runs",
          legacy["freeze_unchanged"] and stream["freeze_unchanged"])
    for path in (legacy_path, stream_path):
        conn = sqlite3.connect(path)
        try:
            status = conn.execute(
                "SELECT status FROM pit_replay_run").fetchone()[0]
            check("%s: the run row says complete" % os.path.basename(path),
                  status == "complete", status)
        finally:
            conn.close()


# ==========================================================================
# MAIN
# ==========================================================================

def main(argv: Optional[List[str]] = None) -> int:
    global TMPDIR
    argv = list(sys.argv[1:] if argv is None else argv)
    slow = "--slow" in argv
    oracle = "--oracle" in argv

    print("=" * 78)
    print("test_pit_replay -- %s" % pit_replay.ENGINE_VERSION)
    print("=" * 78)
    print("frozen spec  %s" % pit_frozen_spec.SPEC_FREEZE_VERSION)
    print("digest       %s" % pit_frozen_spec.verify()["current_digest"])
    print("mode         %s" % ("FAST + SLOW" if slow else "FAST"))

    TMPDIR = tempfile.mkdtemp(prefix="pit_replay_test_")
    print("scratch      %s" % TMPDIR)
    try:
        run(test_validate)
        run(test_plan_record_is_serialisable)
        run(test_gate_refuses_perturbed_freeze)
        run(test_free_space_guard)
        run(test_per_date_free_space_abort)
        run(test_challenger_is_inert)
        run(test_arithmetic_reconciles)
        run(test_min_block_weight_refusal)
        run(test_primitives_arithmetic)
        run(test_normalise_uses_the_declared_transform)
        run(test_masks)
        run(test_store_is_read_only)
        run(test_columns_match_the_schema)
        run(test_write_constraints_bite)
        run(test_unavailable_rows_are_rows)
        run(test_checkpoint_return_is_read)
        run(test_batching_is_declared)
        if slow:
            run(test_real_slice)
        else:
            skip("(9) a real slice", "pass --slow to run it (~2 minutes)")
        if slow or oracle:
            run(test_streaming_equals_legacy)
        else:
            skip("(10b) the equality oracle",
                 "pass --slow or --oracle to run it (~4 minutes)")
    finally:
        if TMPDIR and os.path.isdir(TMPDIR):
            shutil.rmtree(TMPDIR, ignore_errors=True)
            print()
            print("scratch removed: %s" % TMPDIR)

    print()
    print("=" * 78)
    print("%d PASS, %d FAIL, %d SKIP" % (PASS, FAIL, SKIP))
    for name in FAILURES:
        print("  FAILED: %s" % name)
    final = pit_frozen_spec.verify()
    print("freeze intact after the run: %s" % final["current_digest"])
    if not final["intact"]:
        print("THE FREEZE IS NOT INTACT AFTER THE RUN -- investigate")
        return 1
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
