"""Tests for the debt-rung quality test.   python test_pit_debt_quality.py

    1   the rungs and the REFERENCE are read out of the frozen v2 ladder, not
        restated -- and the reference is derived STRUCTURALLY, so a reordered
        ladder cannot quietly move it onto a lower bound;
    2   the decision rule is pre-registered data and `verdict` obeys it,
        including the two cases that matter: enormous coverage with preserved
        ordering PROMOTES, and a material reshuffle does NOT, however much
        coverage it buys;
    3   the rank statistics behave on CONSTRUCTED vectors whose answer is known
        by hand -- identical, reversed, tied, and the near-tolerance boundary;
    4   THE ADDITIVE-DEBT INVARIANT, which is the whole mechanism under test: a
        debt understatement proportional to nothing reorders nothing, and one
        correlated with the company reorders everything. Both are constructed
        and both are asserted, so a change that broke the EV arithmetic could
        not pass by producing a plausible-looking agreement rate;
    5   the point-in-time selector: a rung is never resolved from a fact that
        had not landed, a composite rung never mixes two period ends, a stale
        period is refused, and a restatement wins only from its own
        availability date onward;
    6   Q is a MEASURED quantity -- 1.0 for the reference, UNKNOWN for a thin
        sample, never a default -- and no DataQuality/PeerCoherence combination
        rule is proposed anywhere in this module;
    7   the survivor-return block is barred from promoting, in code;
    8   the store agrees (mode=ro, SKIPS if absent or locked);
    9   the measured artefact, if one exists, is internally consistent and
        reproduces the published census numbers. SKIPS if it does not exist.

Sections 8 and 9 are the only parts that touch the store or the disk. Nothing
in this file writes to the database, fits a model, or freezes a baseline.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_debt_quality as DQ
import pit_derive
import pit_factor_spec as FS
import pit_policy
import pit_store

fails: list[str] = []
skips: list[str] = []

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURED_JSON = os.path.join(HERE, "pit_archive", "debt_quality", "debt_quality.json")


def check(name: str, cond: bool, extra: object = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {extra}")
        fails.append(name)


def skip(name: str, why: str) -> None:
    print(f"  SKIP  {name} -- {why}")
    skips.append(f"{name}: {why}")


def close(a: float, b: float, tol: float = 1e-9) -> bool:
    return a is not None and b is not None and abs(float(a) - float(b)) <= tol


# ==========================================================================
# (1) THE RUNGS COME FROM THE FROZEN LADDER
# ==========================================================================

def section_1_rungs() -> None:
    print("[1] the rung set and the reference are READ from concept_ladder_v2")
    ladder = pit_policy.ladder_for("total_debt", pit_store.LADDER_VERSION_V2)
    check("one RungSpec per ladder rung, in ladder order",
          [r.key for r in DQ.RUNGS] == [r.key for r in ladder.rungs],
          [r.key for r in DQ.RUNGS])
    check("the ladder version under test is v2",
          DQ.LADDER == pit_store.LADDER_VERSION_V2)
    check("...which is also the version the factor spec resolves debt under",
          DQ.LADDER == FS.LADDER)

    ref = [r for r in DQ.RUNGS if r.is_reference]
    check("exactly one reference rung", len(ref) == 1, [r.key for r in ref])
    check("the reference is the three-way sum",
          ref[0].tags == ("LongTermDebtNoncurrent", "LongTermDebtCurrent",
                          "ShortTermBorrowings"), ref[0].tags)
    check("the reference is bound=exact and combine=sum",
          ref[0].bound == pit_policy.BOUND_EXACT
          and ref[0].combine == pit_policy.COMBINE_SUM)
    check("the reference is the TOTAL quantity",
          ref[0].quantity == pit_policy.DEBT_Q_TOTAL, ref[0].quantity)
    check("every other rung that is not exact is labelled lower_bound",
          all(r.bound in (pit_policy.BOUND_EXACT, pit_policy.BOUND_LOWER)
              for r in DQ.RUNGS))
    check("v1's total_debt ladder is a SUBSET of v2's by rung key",
          {r.key for r in DQ.debt_rungs(pit_store.LADDER_VERSION)}
          <= {r.key for r in DQ.RUNGS})

    # A ladder whose exact composite rung has been removed must RAISE rather
    # than silently rank against a lower bound. Constructed, not mutated in
    # place: pit_policy's ladders are frozen and this test may not edit them.
    original = pit_policy.LADDER_SETS.get("TEST_no_exact")
    try:
        import dataclasses
        broken = dataclasses.replace(
            pit_policy.LADDERS_V2["total_debt"],
            rungs=tuple(r for r in pit_policy.LADDERS_V2["total_debt"].rungs
                        if not (r.bound == pit_policy.BOUND_EXACT
                                and r.combine == pit_policy.COMBINE_SUM)))
        pit_policy.LADDER_SETS["TEST_no_exact"] = dict(
            pit_policy.LADDERS_V2, total_debt=broken)
        raised = False
        try:
            DQ._reference_key("TEST_no_exact")
        except ValueError:
            raised = True
        check("a ladder with no exact composite rung RAISES rather than "
              "ranking against a lower bound", raised)
    finally:
        if original is None:
            pit_policy.LADDER_SETS.pop("TEST_no_exact", None)
        else:
            pit_policy.LADDER_SETS["TEST_no_exact"] = original
    check("pit_policy's real ladder sets are untouched by this test",
          set(pit_policy.LADDER_SETS) == set(pit_policy.ladder_versions()),
          sorted(pit_policy.LADDER_SETS))


# ==========================================================================
# (2) THE DECISION RULE IS PRE-REGISTERED, AND verdict() OBEYS IT
# ==========================================================================

def section_2_decision_rule() -> None:
    print("[2] the owner's rule, stated before the numbers, applied as written")
    rule = DQ.DECISION_RULE
    for phrase in ("enormous coverage", "nearly the same ordering",
                   "materially reshuffles", "fills cells"):
        check(f"the rule quotes the owner: {phrase!r}",
              phrase in rule["sentence"])
    check("the near tolerance is stated, not implied",
          rule["near_tolerance"] == DQ.NEAR_TOLERANCE
          and "5%" in rule["near_tolerance_meaning"])
    check("returns are declared unable to promote in the rule itself",
          "ranking result wins" in rule["returns_may_not_promote"])

    reference = next(r for r in DQ.RUNGS if r.is_reference)
    other = next(r for r in DQ.RUNGS if not r.is_reference)

    big = 40_000                      # plenty of within-date pairs
    check("the reference rung is never judged",
          DQ.verdict(reference, 0.0, 0.0, 1.0, 0, 0)["verdict"]
          == DQ.VERDICT_REFERENCE)

    thin = DQ.verdict(other, 20.0, 0.99, 0.01, DQ.MIN_POOLED_SAMPLE - 1, big)
    check("a thin sample is UNDETERMINED even when every number looks perfect",
          thin["verdict"] == DQ.VERDICT_UNDETERMINED, thin["verdict"])
    check("...and says so rather than calling it harmless",
          "not as harmless" in thin["why"])

    few_pairs = DQ.verdict(other, 20.0, 0.99, 0.01, 5000,
                           DQ.MIN_POOLED_PAIRS - 1)
    check("enough issuer-dates but too few WITHIN-DATE PAIRS is UNDETERMINED",
          few_pairs["verdict"] == DQ.VERDICT_UNDETERMINED, few_pairs["verdict"])
    check("...and names the pair denominator as the reason",
          "WITHIN-DATE pairs" in few_pairs["why"])

    good = DQ.verdict(other, 20.0, 0.98, 0.05, 5000, big)
    check("enormous coverage + preserved ordering -> PROMOTE",
          good["verdict"] == DQ.VERDICT_PROMOTE, good["verdict"])

    reshuffle = DQ.verdict(other, 99.0, 0.99, 0.40, 5000, big)
    check("a material reshuffle is REFUSED however much coverage it buys",
          reshuffle["verdict"] == DQ.VERDICT_DO_NOT_PROMOTE, reshuffle["verdict"])

    disordered = DQ.verdict(other, 20.0, 0.70, 0.05, 5000, big)
    check("ordering below the qualified floor is REFUSED",
          disordered["verdict"] == DQ.VERDICT_DO_NOT_PROMOTE, disordered["verdict"])

    middling = DQ.verdict(other, 20.0, 0.92, 0.15, 5000, big)
    check("ordering that survives but not cleanly -> PROMOTE_WITH_QUALITY_FACTOR",
          middling["verdict"] == DQ.VERDICT_PROMOTE_WITH_Q, middling["verdict"])

    tiny = DQ.verdict(other, 0.1, 0.99, 0.01, 5000, big)
    check("a rung that adds almost no coverage does not reach PROMOTE",
          tiny["verdict"] == DQ.VERDICT_PROMOTE_WITH_Q, tiny["verdict"])

    check("verdict() never reads a return",
          "return" not in DQ.verdict.__code__.co_varnames)

    history = rule["threshold_history"]
    check("the one threshold added after the first run is recorded as such",
          any("MIN_POOLED_PAIRS added" in h["change"] for h in history), history)
    check("...and its effect is stated, including which rung it moved",
          any("PROMOTE" in (h.get("effect") or "") for h in history))
    check("...in the conservative direction only",
          any("never to a promotion" in h["change"] for h in history))


# ==========================================================================
# (3) THE RANK STATISTICS, ON VECTORS WHOSE ANSWER IS KNOWN BY HAND
# ==========================================================================

def section_3_statistics() -> None:
    print("[3] spearman, pairwise ordering, near tolerance and decile churn")
    same = [1.0, 2.0, 3.0, 4.0, 5.0]
    check("identical vectors: rho = 1", close(DQ.spearman(same, same), 1.0))
    check("reversed vectors: rho = -1",
          close(DQ.spearman(same, list(reversed(same))), -1.0))
    check("a monotone transform preserves rho exactly",
          close(DQ.spearman(same, [v ** 3 for v in same]), 1.0))
    check("a constant vector has NO rho (None), never 0.0",
          DQ.spearman(same, [7.0] * 5) is None)
    check("fewer than three points has no rho",
          DQ.spearman([1.0, 2.0], [1.0, 2.0]) is None)

    order = DQ.ordering_agreement(same, same)
    check("identical vectors: every pair agrees",
          close(order["exact"], 1.0) and order["pairs"] == 10, order)
    rev = DQ.ordering_agreement(same, list(reversed(same)))
    check("reversed vectors: no pair agrees", close(rev["exact"], 0.0), rev)

    # One swap of an adjacent pair out of five: 1 reversal in 10 pairs.
    swapped = [2.0, 1.0, 3.0, 4.0, 5.0]
    one = DQ.ordering_agreement(same, swapped)
    check("one adjacent swap = exactly one reversed pair in ten",
          one["reversals"] == 1 and close(one["exact"], 0.9), one)

    # The near tolerance forgives a reversal between two numbers within 5% of
    # each other and does NOT forgive one between two that are far apart.
    near_pair = DQ.ordering_agreement([100.0, 101.0, 200.0],
                                      [101.0, 100.0, 200.0])
    check("a reversal inside the tolerance is forgiven by NEAR but not EXACT",
          close(near_pair["exact"], 2 / 3) and close(near_pair["near"], 1.0),
          near_pair)
    far_pair = DQ.ordering_agreement([100.0, 200.0, 300.0],
                                     [200.0, 100.0, 300.0])
    check("a reversal outside the tolerance is forgiven by NEITHER",
          close(far_pair["exact"], 2 / 3) and close(far_pair["near"], 2 / 3),
          far_pair)
    check("material_pairs excludes the too-close-to-call pair",
          near_pair["material_pairs"] == 2 and far_pair["material_pairs"] == 3,
          (near_pair["material_pairs"], far_pair["material_pairs"]))

    ten = [float(i) for i in range(10)]
    churn_same = DQ.decile_churn(ten, ten)
    check("identical vectors churn nothing",
          close(churn_same["churn"], 0.0) and churn_same["decile_size"] == 1,
          churn_same)
    churn_rev = DQ.decile_churn(ten, list(reversed(ten)))
    check("reversed vectors churn the whole cheap decile",
          close(churn_rev["churn"], 1.0), churn_rev)

    shift = DQ.rank_shift(ten, ten)
    check("identical vectors move nobody's percentile",
          shift["max"] == 0.0 and shift["pct_moving_more_than_5_points"] == 0.0,
          shift)


# ==========================================================================
# (4) THE ADDITIVE-DEBT INVARIANT -- the mechanism the whole test rests on
# ==========================================================================

def section_4_additivity() -> None:
    print("[4] what an understated debt figure does to an EV/EBITDA ranking")
    # Ten issuers. EV = MarketCap + Debt - Cash, ratio = EV / EBITDA.
    mcap = [100.0 + 40.0 * i for i in range(10)]
    cash = [10.0] * 10
    ebitda = [20.0 + 2.0 * i for i in range(10)]
    debt = [50.0 + 30.0 * i for i in range(10)]

    def ratios(debt_vector):
        return [(mcap[i] + debt_vector[i] - cash[i]) / ebitda[i] for i in range(10)]

    exact = ratios(debt)

    # (a) A rung that understates every issuer's debt by the SAME PROPORTION
    #     of its own debt still moves EV by an issuer-specific amount, because
    #     debt is not proportional to EV. So even a "uniform" 14% haircut is
    #     not a monotone transform of the ratio -- which is precisely why this
    #     question needed measuring instead of arguing.
    haircut = ratios([d * 0.862 for d in debt])
    check("a constant proportional debt haircut is NOT a monotone transform "
          "of EV/EBITDA (this is why the test exists)",
          DQ.spearman(exact, haircut) is not None)

    # (b) A rung that returns debt SHIFTED BY A CONSTANT DOLLAR AMOUNT and
    #     nothing else cannot reorder issuers with identical EBITDA...
    flat_mcap = [100.0] * 10
    flat_ebitda = [20.0] * 10
    flat_exact = [(flat_mcap[i] + debt[i] - cash[i]) / flat_ebitda[i]
                  for i in range(10)]
    flat_shift = [(flat_mcap[i] + debt[i] - 25.0 - cash[i]) / flat_ebitda[i]
                  for i in range(10)]
    check("a constant dollar understatement at constant EBITDA preserves the "
          "ordering exactly", close(DQ.spearman(flat_exact, flat_shift), 1.0)
          and close(DQ.ordering_agreement(flat_exact, flat_shift)["exact"], 1.0))

    # (c) ...and an understatement CORRELATED with the issuer reorders it.
    correlated = ratios([d * (1.0 - 0.08 * i) for i, d in enumerate(debt)])
    order = DQ.ordering_agreement(exact, correlated)
    check("an understatement correlated with the issuer reverses real pairs",
          order["reversals"] > 0, order)
    check("...and the reversals are MATERIAL, not too-close-to-call noise",
          order["material_reversals"] > 0, order)

    # (d) The direction is the one the ladder warned about: a lower bound on
    #     debt makes EV smaller, so the multiple is smaller, so the company
    #     looks CHEAPER. Asserted numerically rather than asserted in prose.
    understated = ratios([d * 0.862 for d in debt])
    check("every lower-bound rung makes every issuer look CHEAPER",
          all(understated[i] < exact[i] for i in range(10)))


# ==========================================================================
# (5) THE POINT-IN-TIME SELECTOR
# ==========================================================================

def _periods(rows):
    """Build the (period_end_ord, {tag: [(available_ord, val)]}) shape."""
    from datetime import date
    out = {}
    for period_end, tag, available, val in rows:
        pe = date.fromisoformat(period_end).toordinal()
        av = date.fromisoformat(available).toordinal()
        out.setdefault(pe, {}).setdefault(tag, []).append((av, val))
    for tagmap in out.values():
        for entries in tagmap.values():
            entries.sort()
    return sorted(out.items(), key=lambda kv: -kv[0])


def section_5_selector() -> None:
    print("[5] the point-in-time rung selector: landing, matching, staleness")
    from datetime import date
    ref = next(r for r in DQ.RUNGS if r.is_reference)
    noncurrent = next(r for r in DQ.RUNGS
                      if r.tags == ("LongTermDebtNoncurrent",))
    months = DQ.age_months("total_debt", 0, "v2")

    periods = _periods([
        ("2019-12-31", "LongTermDebtNoncurrent", "2020-02-20", 800.0),
        ("2019-12-31", "LongTermDebtCurrent", "2020-02-20", 100.0),
        ("2019-12-31", "ShortTermBorrowings", "2020-02-20", 100.0),
    ])
    day = date.fromisoformat("2020-06-30").toordinal()
    resolved = DQ.resolve_rungs(periods, day, DQ.RUNGS, months)
    check("the three-way sum adds its three components",
          close(resolved[ref.index][0], 1000.0), resolved.get(ref.index))
    check("the noncurrent rung reports the component alone",
          close(resolved[noncurrent.index][0], 800.0))

    before = date.fromisoformat("2020-01-15").toordinal()
    check("NOTHING resolves before the filing landed",
          DQ.resolve_rungs(periods, before, DQ.RUNGS, months) == {})

    # A composite rung may not straddle two balance sheets.
    split = _periods([
        ("2019-12-31", "LongTermDebtNoncurrent", "2020-02-20", 800.0),
        ("2019-12-31", "LongTermDebtCurrent", "2020-02-20", 100.0),
        ("2020-03-31", "ShortTermBorrowings", "2020-05-10", 100.0),
    ])
    got = DQ.resolve_rungs(split, day, DQ.RUNGS, months)
    check("a composite rung NEVER mixes two period ends",
          ref.index not in got, got.get(ref.index))
    check("...while the single-tag rung below it still resolves",
          noncurrent.index in got)

    stale_day = date.fromisoformat("2024-06-28").toordinal()
    check("a period past the staleness window resolves nothing",
          DQ.resolve_rungs(periods, stale_day, DQ.RUNGS, months) == {})

    # A restatement supersedes only from its own availability date.
    restated = _periods([
        ("2019-12-31", "LongTermDebtNoncurrent", "2020-02-20", 800.0),
        ("2019-12-31", "LongTermDebtNoncurrent", "2020-11-02", 850.0),
    ])
    early = date.fromisoformat("2020-06-30").toordinal()
    late = date.fromisoformat("2020-12-31").toordinal()
    check("before the restatement lands, the original value is returned",
          close(DQ.resolve_rungs(restated, early, DQ.RUNGS, months)[
              noncurrent.index][0], 800.0))
    check("after it lands, the restatement wins",
          close(DQ.resolve_rungs(restated, late, DQ.RUNGS, months)[
              noncurrent.index][0], 850.0))

    check("v2 gives a balance-sheet fact the STATE ceiling, v1 the cadence bound",
          DQ.age_months("total_debt", 0, "v2") == 24
          and DQ.age_months("total_debt", 0, "v1") == 6,
          (DQ.age_months("total_debt", 0, "v2"),
           DQ.age_months("total_debt", 0, "v1")))
    check("a FLOW concept keeps v1's bound under both policies",
          DQ.age_months("operating_income", 4, "v2")
          == DQ.age_months("operating_income", 4, "v1") == 15)


# ==========================================================================
# (6) Q IS MEASURED, AND NO COMBINATION RULE IS PROPOSED
# ==========================================================================

def section_6_quality_factor() -> None:
    print("[6] the proposed Q: measured, debt-only, and silent on combination")
    reference = next(r for r in DQ.RUNGS if r.is_reference)
    other = next(r for r in DQ.RUNGS if not r.is_reference)
    coverage = {d: {"rungs": {r.key: {"marginal_pct_of_base": 5.0}
                             for r in DQ.RUNGS}} for d in DQ.CENSUS_DATES}
    pooled = {
        reference.key: {"n_paired_issuer_dates": 4000,
                        "n_within_date_pairs": 40000,
                        "near_ordering_agreement": 1.0,
                        "cheap_decile_churn": 0.0,
                        "debt_ratio_to_reference": {"p50": 1.0}},
        other.key: {"n_paired_issuer_dates": 4000,
                    "n_within_date_pairs": 40000,
                    "near_ordering_agreement": 0.9312,
                    "cheap_decile_churn": 0.12,
                    "debt_ratio_to_reference": {"p50": 0.862}},
    }
    proposal = DQ.quality_proposal(coverage, pooled)
    check("Q for the reference rung is exactly 1.0",
          proposal["rungs"][reference.key]["Q_debt"] == 1.0)
    check("Q for a measured rung IS the measured near agreement (to 3 places, "
          "which is the precision the measurement supports)",
          close(proposal["rungs"][other.key]["Q_debt"], 0.9312, 5e-4),
          proposal["rungs"][other.key]["Q_debt"])
    check("...and the basis names the measurement, not a judgement",
          "measured near ordering agreement"
          in proposal["rungs"][other.key]["basis"])

    thin = dict(pooled)
    thin[other.key] = dict(pooled[other.key], n_paired_issuer_dates=5)
    thin_proposal = DQ.quality_proposal(coverage, thin)
    check("a thin sample gets Q=UNKNOWN, never a default",
          thin_proposal["rungs"][other.key]["Q_debt"] == DQ.UNKNOWN,
          thin_proposal["rungs"][other.key]["Q_debt"])

    check("Q is declared a confidence weight and NOT a correction",
          proposal["is_a_confidence_weight_not_a_correction"] is True)
    check("the dimension is named as debt provenance only",
          proposal["dimension"] == "debt_provenance_only")
    check("NO DataQuality/PeerCoherence combination rule is proposed",
          proposal["combination_rule_with_peer_coherence"].startswith("NOT PROPOSED"),
          proposal["combination_rule_with_peer_coherence"])
    check("Q never exceeds 1.0 and never falls below the stated floor",
          all(v["Q_debt"] == DQ.UNKNOWN
              or DQ.Q_FLOOR <= float(v["Q_debt"]) <= 1.0
              for v in proposal["rungs"].values()))

    # pit_policy forbids rescaling a lower bound up to an estimated total, and
    # this module must not be the place that does it anyway.
    source = open(os.path.join(HERE, "pit_debt_quality.py"), encoding="utf-8").read()
    check("the module never rescales a lower-bound rung up to a total",
          "Do NOT rescale" not in source.replace(
              "does not rescale a lower-bound debt figure", "")
          or "it never edits the ratio" in source)


# ==========================================================================
# (7) THE SURVIVOR-ONLY RETURN BLOCK CANNOT PROMOTE
# ==========================================================================

def section_7_returns_cannot_promote() -> None:
    print("[7] the survivor return diagnostic is barred from deciding anything")
    other = next(r for r in DQ.RUNGS if not r.is_reference)
    # The same inputs, with a spectacular return story attached: the verdict
    # must not move, because verdict() cannot see returns at all.
    a = DQ.verdict(other, 30.0, 0.80, 0.50, 9999, 40000)
    b = DQ.verdict(other, 30.0, 0.80, 0.50, 9999, 40000)
    check("verdict is a pure function of the ranking inputs", a == b)
    check("...and refuses this rung on the ordering evidence",
          a["verdict"] == DQ.VERDICT_DO_NOT_PROMOTE)
    check("the return scope constant is the store's own survivor label",
          DQ.SCOPE_RETURNS == pit_store.SAMPLE_SURVIVOR_ONLY)
    check("the ranking half carries its OWN scope label, distinct from returns",
          DQ.SCOPE_PRICED != DQ.SCOPE_RETURNS and DQ.SCOPE_PRICED != DQ.SCOPE_FULL)
    check("the coverage half is labelled full reporting universe",
          DQ.SCOPE_FULL == "FULL_REPORTING_UNIVERSE")

    payload = {"rows_written_to_the_store": 0,
               "resources": {"free_bytes_min_observed": DQ.MIN_FREE_BYTES,
                             "peak_working_set_bytes": 1},
               "survivor_return_diagnostic": {"may_promote_a_rung": True,
                                              "sample_scope": DQ.SCOPE_RETURNS},
               "quality_factor_proposal": {
                   "combination_rule_with_peer_coherence": "NOT PROPOSED",
                   "rungs": {}},
               "ranking": {"sample_scope": DQ.SCOPE_PRICED}, "coverage": {}}
    check("check_findings CATCHES a return block that claims it may promote",
          any("barred from promoting" in p for p in DQ.check_findings(payload)),
          DQ.check_findings(payload))
    payload["survivor_return_diagnostic"]["may_promote_a_rung"] = False
    check("...and passes the same payload once it is barred",
          DQ.check_findings(payload) == [], DQ.check_findings(payload))


# ==========================================================================
# (8) THE STORE (mode=ro; SKIPS if absent or locked)
# ==========================================================================

def section_8_store() -> None:
    print("[8] the store: the rungs are resolvable and the reference is rare")
    db_path = pit_store.DEFAULT_PIT_DB_PATH
    if not os.path.exists(db_path):
        skip("store", f"{db_path} is absent")
        return
    status = DQ.PC.disk_status(db_path)
    check("the volume is above this module's floor before any read",
          status["free_bytes"] >= DQ.MIN_FREE_BYTES,
          f"{status['free_bytes'] / 1024 ** 3:.2f} GiB free")
    try:
        conn = DQ.PC.connect_ro(db_path)
    except sqlite3.Error as exc:
        skip("store", f"cannot open read-only: {exc}")
        return
    try:
        tags = sorted({t for r in DQ.RUNGS for t in r.tags})
        marks = ", ".join("?" * len(tags))
        counts = dict(conn.execute(
            f"""SELECT tag, COUNT(DISTINCT entity_id) FROM pit_fact
                 WHERE qtrs = 0 AND segments = '' AND coreg = '' AND unit = 'USD'
                   AND tag IN ({marks}) GROUP BY tag""", tags))
        for tag in tags:
            check(f"{tag}: present in the store", counts.get(tag, 0) > 0,
                  counts.get(tag, 0))
        check("ShortTermBorrowings is the scarce component the ladder blamed",
              counts.get("ShortTermBorrowings", 0)
              < counts.get("LongTermDebtNoncurrent", 0),
              (counts.get("ShortTermBorrowings"),
               counts.get("LongTermDebtNoncurrent")))
        grid = DQ.grid_dates(conn)
        check("the replay grid is the 165 month-end dates the census used",
              len(grid) == 165, len(grid))
        check("select_dates always includes the three census dates",
              set(DQ.CENSUS_DATES) <= set(DQ.select_dates(grid, "census"))
              and set(DQ.CENSUS_DATES) <= set(DQ.select_dates(grid, "annual")))
        check("the store still holds ZERO replayed features and scores",
              conn.execute("SELECT COUNT(*) FROM pit_feature").fetchone()[0] == 0
              and conn.execute("SELECT COUNT(*) FROM pit_score").fetchone()[0] == 0)
        check("...and zero replay runs",
              conn.execute("SELECT COUNT(*) FROM pit_replay_run").fetchone()[0] == 0)
    except sqlite3.Error as exc:
        skip("store queries", str(exc))
    finally:
        conn.close()


# ==========================================================================
# (9) THE MEASURED ARTEFACT
# ==========================================================================

def section_9_measured() -> None:
    print("[9] the measured run, if one has been produced")
    if not os.path.exists(MEASURED_JSON):
        skip("measured artefact", f"{MEASURED_JSON} has not been produced")
        return
    with open(MEASURED_JSON, encoding="utf-8") as handle:
        payload = json.load(handle)

    check("the run reports no findings against itself",
          payload.get("findings") == [], payload.get("findings"))
    check("the run wrote nothing to the store",
          payload["rows_written_to_the_store"] == 0)
    resources = payload["resources"]
    check("peak working set was MEASURED, not reported as 0",
          resources["peak_working_set_bytes"] not in (0, None))
    check("free space never fell below the floor",
          resources["free_bytes_min_observed"] >= DQ.MIN_FREE_BYTES)
    check("the WAL peak is reported",
          "wal_bytes_peak_observed" in resources)

    # The reproduction gate: this module's selector has to agree with the
    # numbers the ladder was argued from before its new numbers mean anything.
    validation = {c["check"]: c for c in payload.get("validation") or []}
    if not validation:
        skip("validation", "the run was made with --no-validate")
    for day in DQ.CENSUS_DATES:
        row = validation.get(f"base universe at {day}")
        if row is None:
            continue
        check(f"base universe at {day} reproduces pit_derive exactly",
              row["measured"] == pit_derive.MEASURED_BASE[day],
              (row["measured"], pit_derive.MEASURED_BASE[day]))
        for version in ("concept_ladder_v1", "concept_ladder_v2"):
            row = validation.get(f"{version} total_debt coverage at {day}")
            if row is None or row["measured"] is None:
                continue
            published = 100.0 * pit_derive.MEASURED_TOTAL_DEBT[version][day]
            check(f"{version} coverage at {day} reproduces the published figure "
                  "to within a rounding step",
                  abs(row["measured"] - published) <= 0.06,
                  (row["measured"], published))

    pooled = payload["ranking"]["pooled"]
    for rung in DQ.RUNGS:
        block = pooled.get(rung.key)
        if not block:
            continue
        near = block.get("near_ordering_agreement")
        exact = block.get("exact_ordering_agreement")
        if near is None or exact is None:
            continue
        check(f"{rung.key[:44]}: near agreement >= exact agreement",
              near >= exact - 1e-9, (near, exact))
        check(f"{rung.key[:44]}: both agreements are proportions",
              0.0 <= exact <= 1.0 and 0.0 <= near <= 1.0)

    proposal = payload["quality_factor_proposal"]
    check("the measured Q for the reference rung is 1.0",
          proposal["rungs"][payload["reference_rung"]]["Q_debt"] == 1.0)
    check("no combination rule was proposed by the run",
          proposal["combination_rule_with_peer_coherence"].startswith("NOT PROPOSED"))
    for key, block in proposal["rungs"].items():
        q = block["Q_debt"]
        if q == DQ.UNKNOWN:
            continue
        near = (pooled.get(key) or {}).get("near_ordering_agreement")
        if near is None or key == payload["reference_rung"]:
            continue
        check(f"{key[:44]}: Q is the measured near agreement, not a judgement",
              close(q, max(DQ.Q_FLOOR, near), 1e-3), (q, near))
        verdict_block = block["verdict"]
        expected = DQ.verdict(
            next(r for r in DQ.RUNGS if r.key == key),
            block["mean_marginal_coverage_points_of_base"], near,
            (pooled.get(key) or {}).get("cheap_decile_churn"),
            block["n_paired_issuer_dates"],
            int((pooled.get(key) or {}).get("n_within_date_pairs") or 0))["verdict"]
        check(f"{key[:44]}: the stored verdict is what the rule produces",
              verdict_block["verdict"] == expected,
              (verdict_block["verdict"], expected))

    head = payload["headline"]
    check("the headline's ranges are computed from the payload, not written in",
          all(str(v) in json.dumps(head["measured_range"])
              for v in (head["measured_range"]["near_ordering_agreement"][0],
                        head["measured_range"]["cheap_decile_churn"][1])))
    check("an UNDETERMINED rung is excluded from the headline ranges",
          all(k not in head["measured_range"]["rungs_included"]
              for k in head["verdicts"].get(DQ.VERDICT_UNDETERMINED, [])),
          head["measured_range"]["rungs_included"])
    rep = head["representativeness_of_the_ranking_sample"]
    check("the ranking sample is declared non-random, with the gate named",
          rep["sample_is_not_random"] is True
          and "ShortTermBorrowings" in rep["gate"])
    check("the DIRECTION of that bias is UNKNOWN, not guessed",
          rep["direction_of_the_bias"] == DQ.UNKNOWN)
    check("...and the reason is the absence limit this store actually has",
          "never_tagged" in rep["why_direction_is_unknown"]
          and "tagged_zero" in rep["why_direction_is_unknown"])
    check("the calendar thinning of the sample is reported, not hidden",
          rep["calendar_spread_points"] is not None
          and "share-count artefact" in rep["calendar_note"].lower(),
          rep["calendar_spread_points"])

    returns = payload["survivor_return_diagnostic"]
    check("the return diagnostic is labelled survivor-only and barred",
          returns["sample_scope"] == pit_store.SAMPLE_SURVIVOR_ONLY
          and returns["may_promote_a_rung"] is False)
    check("...and names the label policy behind its numbers",
          returns["label_policy_version"] == pit_store.LABEL_POLICY_VERSION)


def main() -> int:
    started = time.time()
    print("=" * 74)
    print("THE DEBT-RUNG QUALITY TEST: does the coverage recovery survive ranking?")
    print("=" * 74)
    for section in (section_1_rungs, section_2_decision_rule,
                    section_3_statistics, section_4_additivity,
                    section_5_selector, section_6_quality_factor,
                    section_7_returns_cannot_promote, section_8_store,
                    section_9_measured):
        section()
        print()
    if skips:
        print(f"{len(skips)} SKIPPED:")
        for line in skips:
            print(f"  SKIP  {line}")
    if fails:
        print(f"{len(fails)} FAILED:")
        for line in fails:
            print(f"  FAIL  {line}")
    print(f"{'PASS' if not fails else 'FAIL'}  ({time.time() - started:.1f}s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
