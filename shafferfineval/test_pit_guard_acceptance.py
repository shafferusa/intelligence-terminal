"""THE FINAL ACCEPTANCE TEST for the candidate v2 redundancy guard.

Pure stdlib, no network, no database:  python test_pit_guard_acceptance.py

`test_pit_normalization.py` proves the transforms are what they claim to be.
THIS file is about one question only: can the guard be trusted to run, and can
it be trusted not to cry wolf? Those are one question, not two. A guard that
refuses everything is switched off within a week -- and a guard that is
switched off by `allow_redundant=True` becoming a habit is worse than no guard,
because the registry still carries its evidence field and the field is a lie.

Every comparison is reported with one of the four statuses the owner asked for:

    EXACT_RANK_EQUIVALENT   identical ranks -- structural, not statistical
    EXACT_RANK_INVERSE      exactly reversed ranks -- the same information
    NEAR_REDUNDANT          |Spearman rho| >= 0.95 -- a warning, not a proof
    DISTINCT                nothing strong enough to name

and beside the ADMITTED column, because the whole repair is that those two are
different things. Section 3 is the one that matters most: a level-anchored
factor whose RAW ORDERING matches a ranked one is EXACT_RANK_EQUIVALENT and is
ADMITTED, because its scores are not equivalent. If the guard refused that pair
it would be refusing its own remedy, and the only route past it would be the
escape hatch.

Sections:
    1  the four statuses, each produced by the pair designed to produce it
    2  the three collapses this project actually shipped are still caught
    3  the disarm-proof: different normalisations, same raw ranks, ADMITTED
    4  the opt-in defect is closed: the check is compulsory
    5  the comparison set comes from the REGISTRY, not from the caller
    6  the sample-size floor, the degenerate factor, and the escape hatches
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_normalization as N

fails: list[str] = []


def check(name: str, cond: bool, extra: object = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {extra}")
        fails.append(name)


def _raises(thunk, exc) -> bool:
    try:
        thunk()
    except exc:
        return True
    except Exception:
        return False
    return False


# --------------------------------------------------------------------------
# Two cohorts, sixteen companies. Two, because every collapse this guard was
# written for is a WITHIN-cohort identity that a pooled correlation hides.
# --------------------------------------------------------------------------

SOFTWARE = "7372"
GROCERY = "5411"

A_EBITDA = [40.0, 120.0, 260.0, 500.0, 900.0, 1500.0, 3200.0, 8000.0]
A_MARGIN = [0.21, 0.35, 0.28, 0.33, 0.24, 0.38, 0.31, 0.26]
B_EBITDA = [60.0, 150.0, 300.0, 700.0, 1100.0, 2000.0, 4000.0, 9000.0]
B_MARGIN = [0.06, 0.11, 0.08, 0.13, 0.05, 0.09, 0.12, 0.07]
LEVERAGE = [0.55, 0.12, 0.61, 0.20, 0.48, 0.31, 0.05, 0.44,
            0.33, 0.07, 0.52, 0.15, 0.41, 0.26, 0.60, 0.11]

SECTORS = [SOFTWARE] * 8 + [GROCERY] * 8
MARGINS = A_MARGIN + B_MARGIN
EBITDA = A_EBITDA + B_EBITDA

#: The June-2022 CPI peak, the figure the original measurement used.
PI = 0.0906
NOMINAL_GROWTH = [0.32, 0.18, 0.12, 0.09, 0.04, -0.03, -0.11, 0.27,
                  0.21, 0.15, 0.01, -0.07, 0.35, 0.23, -0.05, 0.16]

M_A, _, _ = N.benchmark_margin_50_75(A_EBITDA, A_MARGIN)
M_B, _, _ = N.benchmark_margin_50_75(B_EBITDA, B_MARGIN)
EXCESS = [m - M_A for m in A_MARGIN] + [m - M_B for m in B_MARGIN]

#: A noisy copy of margin: correlated enough to warn about, and NOT identical.
#: The noise has to be large enough to reorder a few near-neighbours, or the
#: copy is rank-equivalent and the fixture is testing the wrong status --
#: measured, rho = +0.983 with the ranks genuinely different.
NOISY_MARGIN = [m + d for m, d in zip(
    MARGINS, [0.03, -0.01, 0.02, -0.03, 0.02, -0.02, 0.01, -0.02,
              0.01, -0.005, 0.008, -0.012, 0.006, -0.004, 0.009, -0.006])]

ROWS: list[tuple[str, str, str, int, str]] = []


def report_row(name: str, report: "N.RedundancyReport", admitted: str) -> str:
    """One line of the acceptance table: status, verdict, n, admitted."""
    status = N.acceptance_status(report)
    ROWS.append((name, status, report.verdict, report.n, admitted))
    return status


def main() -> int:
    print("== 1. the four statuses, each from the pair built to produce it ==")

    same = N.redundancy_check([m - M_A for m in A_MARGIN],
                              {"EBITDAMarginRank": A_MARGIN},
                              candidate_type=N.PERCENTILE_RANK,
                              existing_types={"EBITDAMarginRank": N.PERCENTILE_RANK})
    check("EXACT_RANK_EQUIVALENT: margin - M_s under a rank",
          report_row("margin-M_s vs margin (both ranked)", same, "no")
          == N.STATUS_EXACT_RANK_EQUIVALENT, same.verdict)

    inverse = N.redundancy_check([-m for m in A_MARGIN],
                                 {"EBITDAMarginRank": A_MARGIN},
                                 candidate_type=N.PERCENTILE_RANK,
                                 existing_types={"EBITDAMarginRank": N.PERCENTILE_RANK})
    check("EXACT_RANK_INVERSE: the sign-flipped copy",
          report_row("-margin vs margin", inverse, "no")
          == N.STATUS_EXACT_RANK_INVERSE, inverse.verdict)
    check("...and the inverse is still REDUNDANT: a sign is not information",
          inverse.verdict == N.VERDICT_REDUNDANT, inverse.verdict)

    near = N.redundancy_check(NOISY_MARGIN, {"EBITDAMarginRank": MARGINS},
                              candidate_type=N.PERCENTILE_RANK,
                              existing_types={"EBITDAMarginRank": N.PERCENTILE_RANK})
    check("NEAR_REDUNDANT: a noisy copy correlates without being identical",
          report_row("noisy margin vs margin", near, "yes")
          == N.STATUS_NEAR_REDUNDANT, (near.verdict, near.rho))
    check("...and NEAR_REDUNDANT is a warning, not a refusal",
          not near.carries_no_information, near.verdict)

    distinct = N.redundancy_check(LEVERAGE, {"EBITDAMarginRank": MARGINS},
                                  candidate_type=N.PERCENTILE_RANK,
                                  existing_types={"EBITDAMarginRank": N.PERCENTILE_RANK})
    check("DISTINCT: leverage against margin",
          report_row("leverage vs margin", distinct, "yes")
          == N.STATUS_DISTINCT, (distinct.verdict, distinct.rho))
    check("the four statuses are exactly the four the owner asked for",
          set(N.ACCEPTANCE_STATUSES) == {"EXACT_RANK_EQUIVALENT",
                                         "EXACT_RANK_INVERSE",
                                         "NEAR_REDUNDANT", "DISTINCT"})
    check("all four were exercised above",
          {row[1] for row in ROWS} == set(N.ACCEPTANCE_STATUSES),
          sorted({row[1] for row in ROWS}))

    print("== 2. the three shipped collapses are still caught ==")
    deflated = N.redundancy_check([g - PI for g in NOMINAL_GROWTH],
                                  {"RevenueGrowth": NOMINAL_GROWTH},
                                  candidate_type=N.PERCENTILE_RANK,
                                  existing_types={"RevenueGrowth": N.PERCENTILE_RANK})
    check("(a) the inert deflator is EXACT_RANK_EQUIVALENT and refused",
          report_row("growth - pi vs growth", deflated, "no")
          == N.STATUS_EXACT_RANK_EQUIVALENT and deflated.carries_no_information)
    cohort = N.redundancy_check(EXCESS, {"EBITDAMarginRank": MARGINS}, SECTORS,
                                candidate_type=N.PERCENTILE_RANK,
                                existing_types={"EBITDAMarginRank": N.PERCENTILE_RANK})
    check("(b) the inert cohort benchmark is caught WITHIN cohort",
          report_row("excess vs margin, ranked, by sector", cohort, "no")
          == N.STATUS_EXACT_RANK_EQUIVALENT and cohort.scope == N.SCOPE_COHORT,
          cohort.scope)
    check("(b) and the pooled scope alone would have missed it",
          cohort.pooled[0].exact is None
          and cohort.pooled[0].verdict != N.VERDICT_REDUNDANT,
          (cohort.pooled[0].exact, cohort.pooled[0].rho))
    logged = N.redundancy_check([math.log1p(v) for v in EBITDA],
                                {"EBITDAScale": EBITDA},
                                candidate_type=N.PERCENTILE_RANK,
                                existing_types={"EBITDAScale": N.PERCENTILE_RANK})
    check("(c) the decorative log is EXACT_RANK_EQUIVALENT and refused",
          report_row("log1p(EBITDA) vs EBITDA", logged, "no")
          == N.STATUS_EXACT_RANK_EQUIVALENT and logged.carries_no_information)

    print("== 3. THE DISARM-PROOF: same raw ranks, different normalisations ==")
    anchored = N.redundancy_check(
        EXCESS, {"EBITDAMarginRank": MARGINS}, SECTORS,
        candidate_type=N.BENCHMARK_ANCHORED,
        existing_types={"EBITDAMarginRank": N.PERCENTILE_RANK})
    status = report_row("excess (ANCHORED) vs margin (ranked)", anchored, "yes")
    check("the relationship is still reported as EXACT_RANK_EQUIVALENT",
          status == N.STATUS_EXACT_RANK_EQUIVALENT, status)
    check("...but the VERDICT is not redundant, so it is not refused",
          not anchored.carries_no_information
          and anchored.verdict == N.VERDICT_CORRELATED, anchored.verdict)
    check("...and the explanation says WHY: the scores are not equivalent",
          "normalised differently" in anchored.explanation
          and "anchored" in anchored.explanation, anchored.explanation)
    # The claim under the verdict, measured rather than asserted.
    ranked_margin = [N.percentile_rank_score(
        m, [x for j, x in enumerate(A_MARGIN) if j != i]).score
        for i, m in enumerate(A_MARGIN)]
    a_excess = [m - M_A for m in A_MARGIN]
    ranked_excess = [N.percentile_rank_score(
        e, [x for j, x in enumerate(a_excess) if j != i]).score
        for i, e in enumerate(a_excess)]
    check("under a RANK the two score identically, to the digit",
          ranked_margin == ranked_excess)
    anchored_excess = [N.benchmark_anchored_score(m, M_A, scale=0.06).score
                       for m in A_MARGIN]
    biggest = max(abs(a - b) for a, b in zip(ranked_margin, anchored_excess))
    check("under the ANCHOR they differ materially, which is the whole point",
          biggest > 10.0, biggest)
    check("the anchored score is zero AT the bar, which a rank can never be",
          abs(N.benchmark_anchored_score(M_A, M_A, scale=0.06).score) < 1e-12)
    # And the registration itself must admit it.
    registry = {"EBITDAMarginRank": N.candidate_v2_registry()["EBITDAMarginRank"]}
    admitted = N.register_factor(
        registry, N.candidate_v2_registry()["EBITDAExcessLevel"],
        sample_values=EXCESS, samples={"EBITDAMarginRank": MARGINS},
        cohort_ids=SECTORS)
    check("REGISTRATION ADMITS the level-anchored factor",
          admitted.accepted and "EBITDAExcessLevel" in registry, admitted.note)
    check("...with the finding recorded rather than suppressed",
          admitted.redundancy is not None
          and N.acceptance_status(admitted.redundancy)
          == N.STATUS_EXACT_RANK_EQUIVALENT, admitted.note)
    check("...and it was NOT admitted through the escape hatch",
          admitted.checked and "DESPITE" not in admitted.note, admitted.note)

    print("== 4. the opt-in defect is closed: the check is compulsory ==")
    seeded = N.candidate_v2_registry()
    check("no samples at all is now a REFUSAL, not a silent skip",
          _raises(lambda: N.register_factor(seeded, N.FactorSpec(
              feature_key="Sneak", normalization_type=N.PERCENTILE_RANK,
              economic_question="q", rationale="r")), N.NormalizationError))
    check("a panel missing one registered factor is refused",
          _raises(lambda: N.register_factor(seeded, N.FactorSpec(
              feature_key="Sneak", normalization_type=N.PERCENTILE_RANK,
              economic_question="q", rationale="r"),
              sample_values=LEVERAGE,
              samples={"EBITDAMarginRank": MARGINS},
              cohort_ids=SECTORS), N.NormalizationError))
    check("a cohort-scoped factor with no cohort_ids is refused",
          _raises(lambda: N.register_factor(seeded, N.FactorSpec(
              feature_key="Sneak", normalization_type=N.PERCENTILE_RANK,
              economic_question="q", rationale="r"),
              sample_values=LEVERAGE,
              samples={key: MARGINS for key in seeded}), N.NormalizationError))
    waived = N.register_factor(
        dict(seeded), N.FactorSpec(
            feature_key="Waived", normalization_type=N.PERCENTILE_RANK,
            economic_question="q", rationale="r"),
        sample_values=LEVERAGE, samples={key: LEVERAGE for key in seeded},
        cohort_scope_not_run_reason="one cohort in this sample",
        allow_redundant=True)
    check("the cohort waiver is recorded LOUDLY on the registration",
          "WITHIN-COHORT SCOPE NOT RUN" in waived.note
          and waived.scopes_run == (N.SCOPE_POOLED,), waived.note)
    unchecked = N.register_factor(
        dict(seeded), N.FactorSpec(
            feature_key="Unmeasured", normalization_type=N.ZERO_ANCHORED,
            economic_question="q", rationale="r",
            anchor_source=N.ANCHOR_ZERO, scale=N.fixed_scale(0.1)),
        unchecked_reason="no cross-section exists for this factor yet")
    check("the deliberate bypass stamps UNCHECKED on the note",
          unchecked.note.startswith("UNCHECKED") and not unchecked.checked,
          unchecked.note)
    first = N.register_factor({}, N.candidate_v2_registry()["EBITDAMarginRank"])
    check("the FIRST factor into an empty registry needs no panel",
          first.accepted and not first.checked
          and "first factor" in (first.unchecked_reason or ""), first.note)
    check("a checked registration says which scopes actually ran",
          admitted.scopes_run == (N.SCOPE_POOLED, N.SCOPE_COHORT),
          admitted.scopes_run)

    print("== 5. the comparison set comes from the REGISTRY, not the caller ==")
    # The candidate is a monotone rescaling of EBITDAScale -- both ranked, so
    # the collapse is real -- and is unrelated to EBITDAMarginRank. A caller
    # who named only the margin would have been told it was independent, and
    # the registry would have gained a second copy of EBITDAScale.
    small = {"EBITDAMarginRank": N.candidate_v2_registry()["EBITDAMarginRank"],
             "EBITDAScale": N.candidate_v2_registry()["EBITDAScale"]}
    rescaled = [v / 1000.0 + 5.0 for v in EBITDA]
    callers_choice = N.redundancy_check(
        rescaled, {"EBITDAMarginRank": MARGINS}, SECTORS,
        candidate_type=N.PERCENTILE_RANK,
        existing_types={"EBITDAMarginRank": N.PERCENTILE_RANK})
    check("the caller's chosen subset says INDEPENDENT -- the old hole",
          not callers_choice.carries_no_information, callers_choice.verdict)
    panel = {"EBITDAMarginRank": MARGINS, "EBITDAScale": EBITDA}
    refused = _raises(lambda: N.register_factor(
        dict(small), N.FactorSpec(
            feature_key="EBITDAScaleThousands",
            normalization_type=N.PERCENTILE_RANK,
            economic_question="how big versus peers, in thousands?",
            rationale="the same dollars, rescaled"),
        sample_values=rescaled, samples=panel, cohort_ids=SECTORS),
        N.RedundancyError)
    check("the registry-derived comparison REFUSES it",
          refused, "registration should have raised RedundancyError")
    both = N.redundancy_check(rescaled, panel, SECTORS,
                              candidate_type=N.PERCENTILE_RANK,
                              existing_types={k: N.PERCENTILE_RANK for k in panel})
    check("...because the collapse is against the factor the caller omitted",
          both.matched_key == "EBITDAScale", both.matched_key)
    check("...and it is named as an affine rescaling, not a vague correlation",
          both.cause in (N.CAUSE_AFFINE, N.CAUSE_COHORT_CONSTANT), both.cause)
    report_row("rescaled EBITDA vs the FULL registry", both, "no")

    print("== 6. the floor, the degenerate factor and the honest hatches ==")
    tiny = N.redundancy_check([1.0, 2.0, 3.0, 4.0], {"X": [10.0, 20.0, 30.0, 40.0]},
                              candidate_type=N.PERCENTILE_RANK,
                              existing_types={"X": N.PERCENTILE_RANK})
    check("four identical ranks are NOT called structural (MIN_EXACT_N = 8)",
          N.acceptance_status(tiny) == N.STATUS_NEAR_REDUNDANT
          and tiny.verdict == N.VERDICT_CORRELATED, tiny.verdict)
    check("...and the report says why, in the arithmetic",
          "2/4!" in tiny.explanation or "MIN_EXACT_N" in tiny.explanation,
          tiny.explanation)
    report_row("4-row exact match (below the floor)", tiny, "yes")
    flat = N.redundancy_check([3.0] * 16, {"EBITDAMarginRank": MARGINS},
                              candidate_type=N.PERCENTILE_RANK,
                              existing_types={"EBITDAMarginRank": N.PERCENTILE_RANK})
    check("a constant candidate is DEGENERATE, not independent",
          flat.verdict == N.VERDICT_DEGENERATE, flat.verdict)
    check("...and carries no information, so registration refuses it",
          flat.carries_no_information
          and _raises(lambda: N.register_factor(
              {"EBITDAMarginRank": N.candidate_v2_registry()["EBITDAMarginRank"]},
              N.FactorSpec(feature_key="Empty",
                           normalization_type=N.PERCENTILE_RANK,
                           economic_question="q", rationale="r"),
              sample_values=[3.0] * 16, samples={"EBITDAMarginRank": MARGINS},
              cohort_ids=SECTORS), N.RedundancyError))
    forced = N.register_factor(
        {"EBITDAMarginRank": N.candidate_v2_registry()["EBITDAMarginRank"]},
        N.FactorSpec(feature_key="ExcessRank",
                     normalization_type=N.PERCENTILE_RANK,
                     economic_question="how far above the bar versus peers?",
                     rationale="kept deliberately, with the finding recorded"),
        sample_values=EXCESS, samples={"EBITDAMarginRank": MARGINS},
        cohort_ids=SECTORS, allow_redundant=True)
    check("the escape hatch still admits, and still says DESPITE",
          forced.accepted and "DESPITE" in forced.note, forced.note)
    check("a Registration serialises whole, evidence included",
          set(forced.as_dict()) >= {"checked", "compared_keys", "scopes_run",
                                    "redundancy"})

    print()
    print("  ACCEPTANCE TABLE")
    print(f"  {'comparison':<44} {'status':<22} {'verdict':<38} {'n':>3}  admitted")
    for name, status, verdict, n, admitted in ROWS:
        print(f"  {name:<44} {status:<22} {verdict:<38} {n:>3}  {admitted}")
    print()
    print("FAILURES:", len(fails), fails if fails else "")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
