"""Tests for the Shaffer Equity v2 specification freeze and the debt gate.

Two things are asserted here that nothing else can assert:

  1. the freeze DETECTS ITS OWN VIOLATION -- a freeze that cannot is a frozen
     name over moving content;
  2. the debt eligibility gate is HARD, and its three statuses stay three.

Run: python test_pit_frozen_spec.py
"""

from __future__ import annotations

import os
import sys

import pit_frozen_spec as F
import pit_valuation_spec as V

_PASS = 0
_FAIL: list[str] = []


def check(name: str, ok: bool) -> None:
    global _PASS
    if ok:
        _PASS += 1
        print("  PASS  %s" % name)
    else:
        _FAIL.append(name)
        print("  FAIL  %s" % name)


def _raises_value(thunk) -> bool:
    try:
        thunk()
    except ValueError:
        return True
    except Exception:
        return False
    return False


def raises(thunk, exc) -> bool:
    try:
        thunk()
    except exc:
        return True
    except Exception:
        return False
    return False


# --------------------------------------------------------------------------

def test_the_freeze_is_intact() -> None:
    print("\n1. the freeze, at rest")

    v = F.verify()
    check("the specification is INTACT against its recorded digest",
          v["intact"])
    check("the digest was actually recorded, not left PENDING",
          v["recorded"] and F.FROZEN_DIGEST != "PENDING")
    check("every component resolved -- nothing is MISSING",
          not v["missing_components"])
    check("61 components make up spec_freeze_v5 -- v4's 47 plus fourteen: "
          "FACTOR_SPEC_VERSION_V4 and SPECS_V4, FORMULAS_VERSION_V1, "
          "FORMULAS_V1 and its golden witnesses, the D3 base and domain "
          "policies, the benchmark band and floor, V2_BLOCKS, the transform "
          "parameters, PINNED_CURVE, PAIR_PATHS and the engine coupling",
          v["n_components"] == len(F.COMPONENTS) == 61)

    check("the superseded freeze is preserved VERBATIM, never repaired",
          F.FREEZE_V1["digest"]
          == "7a844dca245cfb37ea6722d8e34a9d9407f82e2210ec17f62605aa8ab40f7843"
          and F.FREEZE_V1["n_components"] == 27)
    check("...and carries its own defect in its status",
          F.FREEZE_V1["status"] == F.STATUS_FROZEN_NONEXECUTABLE
          and F.FREEZE_V1["status_reason"]
          == F.REASON_PILLAR_WEIGHT_SEMANTIC_CONFLICT)
    check("...and records that it produced no rows, ever",
          F.FREEZE_V1["rows_it_ever_produced"] == 0)
    check("the successor has a DIFFERENT digest -- the correction reached the "
          "components",
          F.FREEZE_V1["digest"] != F.FROZEN_DIGEST)

    check("spec_freeze_v2 is ALSO preserved verbatim, sealed rather than "
          "extended when it blocked us",
          F.FREEZE_V2["digest"]
          == "5219bdd5902a220bbce6fc4b3bb68aeac17ee34928d340103be24ef85fd31462"
          and F.FREEZE_V2["n_components"] == 32)
    check("...with its own, narrower reason: MISSING_WITHIN_BLOCK_WEIGHTS",
          F.FREEZE_V2["status"] == F.STATUS_FROZEN_NONEXECUTABLE
          and F.FREEZE_V2["status_reason"]
          == F.REASON_MISSING_WITHIN_BLOCK_WEIGHTS)
    check("...and it too produced no rows",
          F.FREEZE_V2["rows_it_ever_produced"] == 0)
    check("three freezes, three distinct digests",
          len({F.FREEZE_V1["digest"], F.FREEZE_V2["digest"],
               F.FROZEN_DIGEST}) == 3)
    check("the chain is linked in all four sealed records",
          F.FREEZE_V1["superseded_by"] == "spec_freeze_v2"
          and F.FREEZE_V2["superseded_by"] == "spec_freeze_v3"
          and F.FREEZE_V3["superseded_by"] == "spec_freeze_v4"
          and F.FREEZE_V4["superseded_by"] == "spec_freeze_v5"
          and F.SPEC_FREEZE_VERSION == "spec_freeze_v5")

    check("spec_freeze_v3 is preserved VERBATIM -- digest 2f9bba31..., 38 "
          "components, sealed rather than widened",
          F.FREEZE_V3["digest"]
          == "2f9bba3136ac45f4bdc37c5197e646acf645410f1c36ded698fbdd368f86e764"
          and F.FREEZE_V3["n_components"] == 38)
    check("...with a GOVERNANCE reason, not a modelling one: the digest "
          "covered version strings, not bodies",
          F.FREEZE_V3["status"] == F.STATUS_SUPERSEDED
          and F.FREEZE_V3["status_reason"]
          == F.REASON_DIGEST_COVERED_VERSION_STRINGS)
    check("...and names the two content defects that sat under it",
          len(F.FREEZE_V3["content_defects_found_under_it"]) == 2)
    check("...and separates main-store rows (0) from isolated pilot rows "
          "(5,582 scores, EG only, may promote nothing)",
          F.FREEZE_V3["rows_it_ever_produced_in_main_store"] == 0
          and F.FREEZE_V3["rows_in_the_isolated_pilot_db_stamped_with_it"]
          ["pit_score"] == 5582
          and F.FREEZE_V3["rows_in_the_isolated_pilot_db_stamped_with_it"]
          ["may_promote_anything"] is False)
    check("spec_freeze_v4 is preserved VERBATIM -- digest 912268b3..., 47 "
          "components, sealed",
          F.FREEZE_V4["digest"]
          == "912268b3ec11bfc641fcac78078a0fb288cc51ce5432f8c27f931a532d1faae9"
          and F.FREEZE_V4["n_components"] == 47)
    check("...superseded for freezing UNRESOLVED semantic choices, 0 main-store "
          "rows, may not execute the replay",
          F.FREEZE_V4["status"] == F.STATUS_SUPERSEDED
          and F.FREEZE_V4["status_reason"] == F.REASON_FROZE_UNRESOLVED_SEMANTICS
          and F.FREEZE_V4["rows_it_ever_produced_in_main_store"] == 0
          and F.FREEZE_V4["may_execute_replay"] is False)
    check("...and records its incident narrative as REJECTED_BY_MEASUREMENT",
          F.FREEZE_V4["incident_narrative_it_carried"]["status"]
          == "REJECTED_BY_MEASUREMENT")
    check("five freezes, five distinct digests",
          len({F.FREEZE_V1["digest"], F.FREEZE_V2["digest"],
               F.FREEZE_V3["digest"], F.FREEZE_V4["digest"],
               F.FROZEN_DIGEST}) == 5)

    check("the weight-semantic invariant refuses the exact defect",
          _raises_value(lambda: F.assert_weight_semantics(
              (("V", "valuation", 0.35), ("G", "real_growth", 0.15),
               ("E", "ebitda_strength", 0.25),
               ("Q", "financial_quality", 0.10)), "probe")))
    check("...and refuses a v1 pillar name in a v2 table",
          _raises_value(lambda: F.assert_weight_semantics(
              {"valuation": 0.25, "growth": 0.15, "ebitda_strength": 0.35,
               "financial_quality": 0.10}, "probe")))
    check("...and refuses a PARTIAL table",
          _raises_value(lambda: F.assert_weight_semantics(
              {"ebitda_strength": 0.35, "valuation": 0.25}, "probe")))
    check("...and accepts the specification's own triples",
          F.assert_weight_semantics(
              __import__("pit_score_signature").V2_WEIGHT_SEMANTICS,
              "pit_score_signature") is None)
    check("the frozen manifest was recorded beside the digest, so drift can "
          "be LOCATED and not merely detected",
          len(F._FROZEN_MANIFEST) == len(F.COMPONENTS))
    check("the recorded manifest hashes to the recorded digest -- ONE manifest",
          F.digest(F._FROZEN_MANIFEST) == F.FROZEN_DIGEST)


def test_the_freeze_detects_its_own_violation() -> None:
    print("\n2. the freeze detects its own violation")

    # A scalar convention.
    original = V.PE_ABSOLUTE_ANCHOR_V1
    try:
        V.PE_ABSOLUTE_ANCHOR_V1 = 20.5
        v = F.verify()
        check("moving the P/E anchor 20.0 -> 20.5 breaks the freeze",
              not v["intact"])
        check("...and the drift is LOCATED to the exact component",
              len(v["drifted"]) == 1
              and v["drifted"][0]["component"]
              == "pit_valuation_spec:PE_ABSOLUTE_ANCHOR_V1")
        check("...and reports the frozen value alongside the current one",
              v["drifted"][0]["frozen"] == "20.0"
              and v["drifted"][0]["now"] == "20.5")
    finally:
        V.PE_ABSOLUTE_ANCHOR_V1 = original
    check("restoring it restores the freeze", F.verify()["intact"])

    # A structural value -- the case a version-string-only freeze would miss.
    try:
        V.DEBT_RUNG_EVEBITDA_V1["LongTermDebtNoncurrent"] = V.DEBT_ELIGIBLE
        v = F.verify()
        check("PROMOTING A REJECTED DEBT RUNG breaks the freeze, even though "
              "no version STRING changed",
              not v["intact"]
              and v["drifted"][0]["component"]
              == "pit_valuation_spec:DEBT_RUNG_EVEBITDA_V1")
    finally:
        V.DEBT_RUNG_EVEBITDA_V1["LongTermDebtNoncurrent"] = \
            V.DEBT_REJECTED_MEASURED
    check("restoring it restores the freeze", F.verify()["intact"])

    check("the digest depends on EVERY component, proved by perturbation",
          not [p for p in F.validate() if "does not depend on" in p])

    # THE REASON v4 EXISTS: a BODY moves, no version string moves, and the
    # freeze sees it. Under v3 all three of these would have read intact=True.
    import dataclasses
    import pit_factor_spec as FS
    import pit_factor_blocks as B
    import pit_normalization as N
    orig = FS.SPECS_V3
    try:
        FS.SPECS_V3 = tuple(
            dataclasses.replace(s, required_primitives=(
                "operating_income", "depreciation_amortisation"))
            if s.key == "ebitda_benchmark" else s for s in orig)
        v = F.verify()
        check("removing revenue from ebitda_benchmark's BODY breaks the "
              "freeze -- FACTOR_SPEC_VERSION_V3 untouched",
              not v["intact"]
              and [d["component"] for d in v["drifted"]]
              == ["pit_factor_spec:SPECS_V3"])
    finally:
        FS.SPECS_V3 = orig
    try:
        B.FACTOR_DIRECTION_V1["net_debt_ebitda"] = B.HIGHER_IS_BETTER
        v = F.verify()
        check("flipping ONE sign in the direction table breaks the freeze",
              not v["intact"]
              and v["drifted"][0]["component"]
              == "pit_factor_blocks:FACTOR_DIRECTION_V1")
    finally:
        B.FACTOR_DIRECTION_V1["net_debt_ebitda"] = B.LOWER_IS_BETTER
    real = N.candidate_v2_registry

    def _tampered():
        r = dict(real())
        r["EBITDAGrowth"] = dataclasses.replace(
            r["EBITDAGrowth"], higher_is_better=not r["EBITDAGrowth"].higher_is_better)
        return r
    try:
        N.candidate_v2_registry = _tampered
        v = F.verify()
        check("tampering the normalization REGISTRY body breaks the freeze -- "
              "NORMALIZATION_POLICY_VERSION untouched",
              not v["intact"]
              and v["drifted"][0]["component"]
              == "pit_normalization:candidate_v2_registry()")
    finally:
        N.candidate_v2_registry = real
    check("all three restored", F.verify()["intact"])

    # THE REASON v5 EXISTS: the executable definitions, the D3 policies and the
    # constants the v4 audit found outside the digest are BODIES now.
    orig4 = FS.SPECS_V4
    try:
        FS.SPECS_V4 = tuple(
            dataclasses.replace(s, peer_eligibility=FS.OPERATING_INCOME_AND_INTEREST)
            if s.key == "interest_coverage" else s for s in orig4)
        v = F.verify()
        check("reverting interest_coverage's v4 eligibility BODY breaks the "
              "freeze -- FACTOR_SPEC_VERSION_V4 untouched",
              not v["intact"]
              and [d["component"] for d in v["drifted"]]
              == ["pit_factor_spec:SPECS_V4"])
    finally:
        FS.SPECS_V4 = orig4
    saved_formula = FS.FORMULAS_V1["ebitda_growth"]
    try:
        FS.FORMULAS_V1["ebitda_growth"] = "ebitda_growth = ebitda_t / ebitda_t-1"
        v = F.verify()
        check("editing ONE executable definition breaks the freeze, located",
              not v["intact"]
              and [d["component"] for d in v["drifted"]]
              == ["pit_factor_spec:FORMULAS_V1"])
    finally:
        FS.FORMULAS_V1["ebitda_growth"] = saved_formula
    saved_rate = FS.EBITDA_GROWTH_BASE_POLICY_V1["max_abs_rate"]
    try:
        FS.EBITDA_GROWTH_BASE_POLICY_V1["max_abs_rate"] = saved_rate * 2
        v = F.verify()
        check("moving the base policy's band breaks the freeze, located",
              not v["intact"]
              and [d["component"] for d in v["drifted"]]
              == ["pit_factor_spec:EBITDA_GROWTH_BASE_POLICY_V1"])
    finally:
        FS.EBITDA_GROWTH_BASE_POLICY_V1["max_abs_rate"] = saved_rate
    band = N.BENCHMARK_BAND_V1
    try:
        N.BENCHMARK_BAND_V1 = (0.40, 0.80)
        v = F.verify()
        check("widening the benchmark band breaks the freeze -- the v4 hole is closed",
              not v["intact"]
              and v["drifted"][0]["component"] == "pit_normalization:BENCHMARK_BAND_V1")
    finally:
        N.BENCHMARK_BAND_V1 = band
    saved_b = V.TRANSFORM_PARAMS_V1["b_solved"]
    try:
        V.TRANSFORM_PARAMS_V1["b_solved"] = V.solve_b
        check("a function smuggled into a digested body is NAMED by the callable "
              "scan itself, not only by the drift message",
              any(p.startswith("a body component contains a callable")
                  and "pit_valuation_spec:TRANSFORM_PARAMS_V1" in p
                  for p in F.validate()))
    finally:
        V.TRANSFORM_PARAMS_V1["b_solved"] = saved_b
    check("all v5 probes restored", F.verify()["intact"])


def test_what_the_freeze_claims() -> None:
    print("\n3. what the freeze claims, and what it refuses to claim")

    check("the survivorship limitation is IN the model version string",
          F.SAMPLE_SCOPE in F.FROZEN_MODEL_VERSION
          and F.FROZEN_MODEL_VERSION
          == "equity_shaffer_v2_pit_SURVIVOR_ONLY_DIAGNOSTIC")
    check("it claims the specification is sufficient to CALCULATE a "
          "diagnostic replay",
          any("DIAGNOSTIC replay" in s for s in F.WHAT_FREEZING_MEANS))
    check("it explicitly denies historical validation",
          any("NOT that the model is historically validated" in s
              for s in F.WHAT_IT_DOES_NOT_MEAN))
    check("...and denies that survivor-only results may promote anything",
          any("may promote" in s for s in F.WHAT_IT_DOES_NOT_MEAN))
    check("...and distinguishes FIXED from FINISHED",
          any("FIXED, which is a different" in s
              for s in F.WHAT_IT_DOES_NOT_MEAN))

    check("all five gates are recorded as closed",
          len(F.GATES) == 5 and all(
              g["verdict"].startswith("CLOSED") for g in F.GATES))
    gate5 = [g for g in F.GATES if g["gate"] == 5][0]
    check("gate 5 is recorded as a RESTRICTIVE decision, not a clean result",
          "RESTRICTIVE" in gate5["verdict"].upper()
          and "NOT A CLEAN BILL OF HEALTH" in gate5["verdict"].upper())

    check("the validation baseline is recorded as BLOCKED on dead-company "
          "prices",
          "DEAD-COMPANY PRICES"
          in F.STILL_BLOCKED["the_validation_baseline"].upper())
    check("the rate-regime limitation travels with the freeze",
          "OBSERVED_REGIME_STABLE"
          in F.STILL_BLOCKED["rate_regime_generalization"])


def test_debt_eligibility_is_a_hard_gate() -> None:
    print("\n4. DEBT_RUNG_EVEBITDA_V1 -- a hard gate, not a multiplier")

    check("exactly TWO rungs are eligible to score",
          sum(1 for k in V.DEBT_RUNG_EVEBITDA_V1 if V.debt_rung_eligible(k))
          == 2)
    check("the exact three-way total is eligible by construction",
          V.debt_rung_eligible(
              "SUM(LongTermDebtNoncurrent+LongTermDebtCurrent+"
              "ShortTermBorrowings)"))
    check("the current+noncurrent SUM is eligible",
          V.debt_rung_eligible("SUM(LongTermDebtNoncurrent+LongTermDebtCurrent)"))
    check("LongTermDebtNoncurrent is REJECTED_MEASURED",
          V.debt_rung_status("LongTermDebtNoncurrent")
          == V.DEBT_REJECTED_MEASURED)
    check("the other three are UNDETERMINED",
          all(V.debt_rung_status(k) == V.DEBT_UNDETERMINED
              for k in ("LongTermDebt",
                        "LongTermDebtAndCapitalLeaseObligations",
                        "DebtLongtermAndShorttermCombinedAmount")))

    check("REJECTED and UNDETERMINED are DIFFERENT statuses -- 'we measured "
          "it and it failed' is not 'we have no evidence'",
          V.DEBT_REJECTED_MEASURED != V.DEBT_UNDETERMINED
          and len(set(V.DEBT_STATUSES)) == 3)
    check("...and they earn DIFFERENT refusal codes, so a later reader can "
          "tell which rungs were judged",
          V.debt_gate("LongTermDebtNoncurrent")
          == V.REASON_DEBT_RUNG_REJECTED
          and V.debt_gate("LongTermDebt")
          == V.REASON_DEBT_RUNG_UNDETERMINED)

    check("an eligible rung passes the gate with no reason",
          V.debt_gate("SUM(LongTermDebtNoncurrent+LongTermDebtCurrent)") is None)
    check("MISSING PROVENANCE is UNDETERMINED, never eligible -- a debt "
          "figure whose rung was not recorded cannot have been validated",
          V.debt_gate(None) == V.REASON_DEBT_RUNG_UNDETERMINED)
    check("an unknown rung RAISES rather than defaulting to a status",
          raises(lambda: V.debt_rung_status("SomeNewTag"), KeyError))

    check("an EXACT-bound rung can still be UNDETERMINED -- eligibility is "
          "about validated ordering, not nominal tag completeness",
          V.debt_rung_evidence(
              "DebtLongtermAndShorttermCombinedAmount")["bound"] == "exact"
          and V.debt_rung_status(
              "DebtLongtermAndShorttermCombinedAmount") == V.DEBT_UNDETERMINED)


def test_the_continuous_q_was_refused_on_the_record() -> None:
    print("\n5. why the continuous Q was refused, kept on the record")

    w = V.WHY_NOT_CONTINUOUS_Q
    check("the rejected proposal is named",
          "0.983" in w["rejected_proposal"] and "0.977" in w["rejected_proposal"])
    check("the reason is that it COMPRESSES the distinction the test exposed",
          "COMPRESS" in w["why"].upper()
          and "14.13%" in w["why"] and "22.55%" in w["why"])
    check("...namely that it describes rank agreement while the factor's use "
          "is tail-sensitive",
          "TAIL-SENSITIVE" in w["why"].upper())
    check("Q = 1 - churn was ALSO refused, as one arbitrary mapping for "
          "another",
          "1 - decile_churn" in w["also_rejected"])
    check("what is preserved instead is named: rung, provenance, statistics, "
          "status",
          len(w["what_is_preserved_instead"]) == 4
          and "the eligibility status" in w["what_is_preserved_instead"])
    check("a continuous shrinkage remains a CHALLENGER -- deferred, not "
          "refuted",
          "CHALLENGER" in w["remains_a_challenger"].upper()
          and "deferred, not refuted" in w["remains_a_challenger"])

    check("the evidence behind each status is retrievable, not just the status",
          V.debt_rung_evidence(
              "LongTermDebtNoncurrent")["cheap_decile_churn"] == 0.2255)
    check("...and an undetermined rung reports UNKNOWN, never 0",
          V.debt_rung_evidence(
              "DebtLongtermAndShorttermCombinedAmount")[
                  "near_ordering_agreement"] is None)


def test_threshold_history_is_permanent_lineage() -> None:
    print("\n6. threshold_history as permanent research lineage")

    hist = V.debt_threshold_history()
    check("the threshold history survived into the frozen spec",
          len(hist) >= 2)
    check("it records that thresholds were registered BEFORE the measurement",
          any("before the first run" in str(h.get("when", "")) for h in hist))

    withdrawal = [h for h in hist if "MIN_POOLED_PAIRS" in str(h.get("change", ""))]
    check("it records the withdrawal: the first rule would have PROMOTED "
          "LongTermDebt",
          bool(withdrawal)
          and "PROMOTE" in str(withdrawal[0].get("effect", "")))
    check("...and that the corrected floor moved NOTHING ELSE",
          bool(withdrawal) and withdrawal[0].get("nothing_else_moved") is True)
    check("...and that a second floor can only WITHDRAW a verdict, never "
          "grant one",
          bool(withdrawal)
          and "never to a promotion" in str(withdrawal[0].get("change", "")))


def test_factor_block_map() -> None:
    print('\n7. the factor-to-block map')
    import pit_factor_blocks as B
    import pit_factor_spec as FS

    check("one factor, one block: no key is mapped twice",
          len(B.FACTOR_BLOCK) == len(set(B.FACTOR_BLOCK)))
    check("every factor spec is either mapped or explicitly REFUSED",
          {x.key for x in FS.SPECS}
          == set(B.FACTOR_BLOCK) | set(B.UNMAPPED))
    check("12 mapped, 2 refused",
          len(B.FACTOR_BLOCK) == 12 and len(B.UNMAPPED) == 2)

    check("EBITDA growth and acceleration are owned by E ONLY -- the 15.6% "
          "double count is gone",
          B.block_of("ebitda_growth") == B.BLOCK_E
          and B.block_of("ebitda_acceleration") == B.BLOCK_E)
    check("no EBITDA factor sits in G; G is the NON-EBITDA growth block",
          all(not k.startswith("ebitda") for k in B.factors_in(B.BLOCK_G)))
    check("ownership follows the economic concept, not the noun in the name: "
          "ebitda_growth is E, real_revenue_growth is G",
          B.block_of("real_revenue_growth") == B.BLOCK_G)
    check("net_debt_ebitda is Q, not E -- it measures the BURDEN, not the "
          "engine, despite the EBITDA denominator",
          B.block_of("net_debt_ebitda") == B.BLOCK_Q)
    check("ev_ebitda is V, not E -- it asks about price, despite the EBITDA "
          "ingredient",
          B.block_of("ev_ebitda") == B.BLOCK_V)

    check("roa and roe are REFUSED, and block_of() raises rather than guessing",
          all(_raises_key(lambda k=k: B.block_of(k)) for k in ("roa", "roe")))
    check("...each saying why it fits NEITHER candidate block",
          all("why_not_E" in B.UNMAPPED[k] and "why_not_Q" in B.UNMAPPED[k]
              for k in B.UNMAPPED))
    check("...and the ROE objection names the SIGN INVERSION against Q's "
          "leverage factors",
          "SIGN INVERSION" in B.UNMAPPED["roe"]["why_not_Q"].upper())
    check("the sector is excluded from EVGQ by name",
          "overlay" in B.SECTOR_IS_NOT_A_BLOCK)

    print('\n8. MIN_BLOCK_WEIGHT = 0.40')
    check("the floor exceeds the largest single block, so nothing scores alone",
          B.MIN_BLOCK_WEIGHT > max(
              __import__("pit_score_signature").V2_MAJOR_WEIGHTS.values()))
    check("E alone (0.35) is REFUSED despite being the largest block",
          not B.meets_min_block_weight([B.BLOCK_E]))
    check("two blocks is NECESSARY, not sufficient: V+Q = 0.35 refused",
          not B.meets_min_block_weight([B.BLOCK_V, B.BLOCK_Q]))
    check("E+Q = 0.45 scores", B.meets_min_block_weight([B.BLOCK_E, B.BLOCK_Q]))
    check("exactly 9 of the 16 combinations may score; 7 are refused",
          sum(1 for m in range(16)
              if B.meets_min_block_weight(
                  [b for i, b in enumerate(B.BLOCKS) if m & (1 << (3 - i))]))
          == 9)

    below = B.assemble({B.BLOCK_E: None, B.BLOCK_V: 50.0, B.BLOCK_G: None,
                        B.BLOCK_Q: 50.0})
    check("below the floor: NO score, and a named refusal -- an absence is "
          "never a zero",
          below["company_score"] is None
          and below["refused"] == B.INSUFFICIENT_BLOCK_COVERAGE)

    no_v = B.assemble({B.BLOCK_E: 10.0, B.BLOCK_V: None, B.BLOCK_G: 10.0,
                       B.BLOCK_Q: 10.0})
    check("missing V: the score is the plain 0.35E + 0.15G + 0.10Q, NOT that "
          "over 0.60",
          _close(no_v["company_score"], 0.35 * 10 + 0.15 * 10 + 0.10 * 10))
    check("...coverage is 0.60/0.85 = 70.59%",
          _close(no_v["block_coverage"], 0.60 / 0.85))
    check("...and the block mask travels with the row",
          no_v["block_mask"] == "E=1,V=0,G=1,Q=1")
    full = B.assemble({b: 10.0 for b in B.BLOCKS})
    check("losing a block does NOT promote the survivors",
          all(_close(full["effective_weights"][b],
                     no_v["effective_weights"][b])
              for b in (B.BLOCK_E, B.BLOCK_G, B.BLOCK_Q)))
    check("the map's own validate() is clean", not B.validate())

    print('\n7b. FACTOR_DIRECTION_V1')
    check("the five owner-approved directions are pinned",
          B.FACTOR_DIRECTION_V1["ev_ebitda"] == B.LOWER_IS_BETTER
          and B.FACTOR_DIRECTION_V1["fcf_conversion"] == B.HIGHER_IS_BETTER
          and B.FACTOR_DIRECTION_V1["interest_coverage"] == B.HIGHER_IS_BETTER
          and B.FACTOR_DIRECTION_V1["net_debt_ebitda"] == B.LOWER_IS_BETTER
          and B.FACTOR_DIRECTION_V1["debt_market_cap"] == B.LOWER_IS_BETTER)
    check("every scoring factor has a direction and none is UNDECLARED",
          all(B.FACTOR_DIRECTION_V1.get(k, B.UNDECLARED) != B.UNDECLARED
              for k in B.FACTOR_BLOCK))
    check("the P/E legs carry their sign INSIDE the transform, so they are "
          "not oriented twice",
          B.FACTOR_DIRECTION_V1["pe_ratio"] == B.EMBEDDED_IN_TRANSFORM
          and B.SUBFACTOR_DIRECTION_V1["pe_absolute"] == B.EMBEDDED_IN_TRANSFORM)
    check("direction_of() RAISES on an undeclared factor -- no default sign",
          _raises_key(lambda: B.direction_of("roa")))
    check("direction is ORIENTATION ONLY, after validity checks -- the "
          "owner's rule is on the record",
          "AFTER domain-validity" in B.DIRECTION_IS_ORIENTATION_ONLY)
    check("every direction records where it came from",
          all(k in B.DIRECTION_SOURCE for k in B.FACTOR_BLOCK))

    print('\n7c. factor_spec_v3 -- margin benchmark, corrected EPS')
    b2 = FS.BY_KEY_V2["ebitda_benchmark"]
    b3 = FS.BY_KEY_V3["ebitda_benchmark"]
    check("v2's DOLLAR reading is preserved verbatim (EBITDA_ONLY, no revenue)",
          "revenue" not in b2.required_primitives
          and b2.peer_eligibility is FS.EBITDA_ONLY)
    check("v3 scores the MARGIN: revenue required, EBITDA_AND_REVENUE eligibility",
          "revenue" in b3.required_primitives
          and b3.peer_eligibility is FS.EBITDA_AND_REVENUE)
    check("...but the COHORT is still selected by EBITDA dollars -- that is "
          "Shaffer's identity",
          b3.member_quantity == "ebitda")
    check("v2's stale EPS declaration is preserved; v3's is corrected",
          FS.BY_KEY_V2["pe_ratio"].peer_eligibility.unavailable_primitives
          == ("earnings_per_share_pit",)
          and FS.BY_KEY_V3["pe_ratio"].peer_eligibility.unavailable_primitives
          == ())
    check("12 of 14 v3 specs are the v2 objects BY REFERENCE -- only the two "
          "decided keys moved",
          sum(1 for a, b in zip(FS.SPECS_V2, FS.SPECS_V3) if a is b) == 12)
    check("11 of 14 v4 specs are the v3 objects BY REFERENCE -- only the three "
          "D3 keys moved",
          sum(1 for a, b in zip(FS.SPECS_V3, FS.SPECS_V4) if a is b) == 11)
    d3 = F.SCORE_ASSEMBLY_GAP["decision_D3_2026_09_22"]
    check("D3 is DECIDED on the record: growth A, acceleration A, OI over "
          "interest, with the base and domain policies named",
          d3["status"].startswith("DECIDED")
          and "abs(EBITDA_t-1)" in d3["ebitda_growth"]
          and "NOT a second difference" in d3["ebitda_acceleration"]
          and "operating_income / interest_expense" in d3["interest_coverage"]
          and "open_decision_D3" not in F.SCORE_ASSEMBLY_GAP)
    check("the UNDECIDED record is kept verbatim inside the decision, not erased",
          "assets_lag1" in d3["superseded_record"]["ebitda_growth_denominator"]
          ["engine_currently_uses"])
    pending = F.SCORE_ASSEMBLY_GAP["owner_confirmation_pending_v5"]
    check("the six items the owner has not yet confirmed are named on the record",
          all(s in pending for s in ("max_abs_rate", "MATCHED_V2", "vocabulary",
                                     "PERCENTILE_RANK", "NEGATIVE-OI", "E + Q")))
    check("both proposed policies carry their pending status INSIDE the digested body",
          "AWAITING_OWNER_CONFIRMATION" in FS.EBITDA_GROWTH_BASE_POLICY_V1["status"]
          and "AWAITING_OWNER_CONFIRMATION" in FS.INTEREST_COVERAGE_DOMAIN_V1["status"])
    import pit_replay as _engine
    check("the ENGINE derives from the version the freeze digests",
          _engine.FACTOR_SPEC_VERSION == F.ENGINE_MUST_DERIVE_FROM
          == FS.FACTOR_SPEC_VERSION_V4)
    check("the live freeze carries the name the last sealed record names, never a "
          "sealed name",
          F.SPEC_FREEZE_VERSION == F.FREEZE_V4["superseded_by"]
          and F.SPEC_FREEZE_VERSION not in {f["freeze_version"] for f in
                                            (F.FREEZE_V1, F.FREEZE_V2,
                                             F.FREEZE_V3, F.FREEZE_V4)})
    check("the pagefile-incident cause is recorded as REJECTED_BY_MEASUREMENT",
          "REJECTED_BY_MEASUREMENT" in F.STILL_BLOCKED["the_2026_09_21_pagefile_incident"])


def _close(a, b, tol=1e-9):
    return a is not None and b is not None and abs(a - b) <= tol


def _raises_key(thunk) -> bool:
    try:
        thunk()
    except KeyError:
        return True
    except Exception:
        return False
    return False


def test_within_block_weights() -> None:
    print('\n9. within-block weights -- POLICY_WEIGHTS_V1')
    import pit_factor_blocks as B
    import pit_score_signature as S

    check("the weights are tagged POLICY, not a fitted result",
          B.POLICY_WEIGHTS_VERSION == "POLICY_WEIGHTS_V1"
          and B.POLICY_WEIGHTS_STATUS == "NOT_EMPIRICALLY_OPTIMIZED")

    e = B.within_block_weights(B.BLOCK_E)
    check("E = .35 benchmark + .25 growth + .20 efficiency + .20 acceleration",
          _close(e["ebitda_benchmark"], 0.35)
          and _close(e["ebitda_growth"], 0.25)
          and _close(e["ebitda_efficiency"], 0.20)
          and _close(e["ebitda_acceleration"], 0.20))
    check("...and it reproduces the PUBLISHED effective weights exactly, so "
          "adopting it keeps the calibration and the model in agreement",
          _close(B.effective_company_weight("ebitda_benchmark"), 0.1225)
          and _close(B.effective_company_weight("ebitda_growth"), 0.0875)
          and _close(B.effective_company_weight("ebitda_efficiency"), 0.0700)
          and _close(B.effective_company_weight("ebitda_acceleration"), 0.0700))

    q = B.within_block_weights(B.BLOCK_Q)
    check("Q = .35 fcf + .30 coverage + .25 net-debt/EBITDA + .10 debt/mcap",
          _close(q["fcf_conversion"], 0.35)
          and _close(q["interest_coverage"], 0.30)
          and _close(q["net_debt_ebitda"], 0.25)
          and _close(q["debt_market_cap"], 0.10))
    check("fcf_conversion LEADS Q -- the only factor asking whether earnings "
          "become cash",
          q["fcf_conversion"] == max(q.values()))
    check("debt_market_cap is SMALLEST -- it imports price movement into a "
          "balance-sheet block",
          q["debt_market_cap"] == min(q.values()))
    check("the two leverage factors together stay under half of Q, so Q is "
          "not a disguised leverage block",
          q["net_debt_ebitda"] + q["debt_market_cap"] < 0.50)

    check("every block's scoring weights sum to 1.00",
          all(_close(sum(B.within_block_weights(b).values()), 1.0)
              for b in B.BLOCKS))
    total = sum(B.block_weight(b) * w
                for b in B.BLOCKS
                for w in B.within_block_weights(b).values())
    check("and the whole model closes to COMPANY_SCALE_V2 = 0.85",
          _close(total, S.COMPANY_SCALE_V2))

    print('\n10. the zero-weight challenger')
    check("ebitda_scale is a CHALLENGER, not a refusal -- it keeps its block",
          B.FACTOR_ROLE["ebitda_scale"] == B.ROLE_ZERO_WEIGHT_CHALLENGER
          and B.block_of("ebitda_scale") == B.BLOCK_E)
    check("...carries zero effective weight",
          B.effective_company_weight("ebitda_scale") == 0.0)
    check("...never reaches the within-block denominator",
          "ebitda_scale" not in B.within_block_weights(B.BLOCK_E))
    alone = B.block_score(B.BLOCK_E, {"ebitda_scale": 90.0})
    check("...cannot make E available by itself",
          not alone["available"] and alone["score"] is None)
    check("...and its value stays OBSERVABLE for research",
          alone["challengers_observed"]["ebitda_scale"] == 90.0)
    check("...with the reason recorded: size can masquerade as signal",
          "masquerade" in B.ZERO_WEIGHT_WHY["ebitda_scale"])

    three_of_four = B.block_score(B.BLOCK_E, {
        "ebitda_benchmark": 20.0, "ebitda_growth": 40.0,
        "ebitda_efficiency": None, "ebitda_acceleration": 10.0})
    denom = 0.35 + 0.25 + 0.20
    check("WITHIN-block renormalisation is allowed: siblings measure one thing",
          _close(three_of_four["score"],
                 (0.35 * 20.0 + 0.25 * 40.0 + 0.20 * 10.0) / denom))
    check("...and the within-block coverage travels, so a thin block is "
          "distinguishable from a full one",
          _close(three_of_four["within_block_coverage"], denom))

    check("V's two granularities reconcile: pe_ratio covers BOTH legs",
          B.V_FACTOR_SUBFACTORS["pe_ratio"] == ("pe_absolute", "pe_relative")
          and _close(B.effective_company_weight("pe_ratio"), 0.20)
          and _close(B.effective_company_weight("ev_ebitda"), 0.05))

    check("G carries exactly one factor, declared as a single-point dependency",
          len(B.within_block_weights(B.BLOCK_G)) == 1
          and "single-point" in B.WITHIN_BLOCK_REASON[B.BLOCK_G].lower())
    check("...so G is unavailable when real_revenue_growth is",
          not B.block_score(B.BLOCK_G,
                            {"real_revenue_growth": None})["available"])

    print('\n11. is the replay calculable?')
    ok, why = F.can_execute_replay()
    check("the economic specification is complete enough to CALCULATE the "
          "first survivor-only diagnostic replay",
          ok and not why)
    check("...and the gap record is CLOSED, with the closed items on record",
          F.SCORE_ASSEMBLY_GAP["status"].startswith("CLOSED")
          and len(F.SCORE_ASSEMBLY_GAP["resolved_2026_09_21"]) == 6)
    check("...while still denying that the weights are optimal",
          "POLICY, not results"
          in F.SCORE_ASSEMBLY_GAP["what_this_does_not_make_true"])
    check("...and still denying historical validation",
          any("NOT that the model is historically validated" in s
              for s in F.WHAT_IT_DOES_NOT_MEAN))


def test_self() -> None:
    print("\n7. self-check")

    problems = F.validate()
    check("pit_frozen_spec.validate() reports 0 problems", not problems)
    for p in problems:
        print("        - %s" % p)

    here = os.path.dirname(os.path.abspath(__file__))
    src = open(os.path.join(here, "pit_frozen_spec.py"), "rb").read()
    check("the module is pure ASCII", all(b < 128 for b in src))
    check("the module touches no database",
          b"sqlite3" not in src)
    check("the report renders without raising",
          isinstance(F.report(), str) and len(F.report()) > 500)


def main() -> int:
    print("test_pit_frozen_spec -- the v2 specification freeze, 2026-09-21")
    test_the_freeze_is_intact()
    test_the_freeze_detects_its_own_violation()
    test_what_the_freeze_claims()
    test_debt_eligibility_is_a_hard_gate()
    test_the_continuous_q_was_refused_on_the_record()
    test_threshold_history_is_permanent_lineage()
    test_factor_block_map()
    test_within_block_weights()
    test_self()
    print("\n%d PASS, %d FAIL" % (_PASS, len(_FAIL)))
    print("FAILURES: %d %s" % (len(_FAIL), _FAIL if _FAIL else ""))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
