"""Offline tests for the candidate v2 normalisation layer.

Pure stdlib, no network, no database:   python test_pit_normalization.py

THE ACCEPTANCE TEST is section 9: the redundancy guard must catch all three
collapses this project actually shipped --

    (a) RevenueGrowth vs RevenueGrowth - pi      the inert deflator
    (b) EBITDAMargin  vs EBITDAMargin - M_s      the inert cohort benchmark
    (c) EBITDA        vs log1p(EBITDA)           the decorative log

-- and must NOT fire on two genuinely different factors, or it is a guard that
refuses everything and will be switched off within a week.

Section 11 is the module's reason to exist, stated as one test: the SAME values
through PERCENTILE_RANK are identical for x and x - c, and through
BENCHMARK_ANCHORED they are not.
"""
from __future__ import annotations

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_normalization as N
import pit_store
import statlib

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
# Realistic fixtures.
#
# SECTOR A is a software-shaped cohort (high margin, wide spread); SECTOR B is
# a grocery-shaped one (thin margin). Two cohorts, not one, because the whole
# point of case (b) is that the collapse is a WITHIN-cohort identity that a
# pooled correlation hides.
# --------------------------------------------------------------------------

A_EBITDA = [40.0, 120.0, 260.0, 500.0, 900.0, 1500.0, 3200.0, 8000.0]      # $m
A_MARGIN = [0.21, 0.35, 0.28, 0.33, 0.24, 0.38, 0.31, 0.26]
A_LEVERAGE = [0.55, 0.12, 0.61, 0.20, 0.48, 0.31, 0.05, 0.44]              # debt/assets

B_EBITDA = [60.0, 150.0, 300.0, 700.0, 1100.0, 2000.0, 4000.0, 9000.0]
B_MARGIN = [0.06, 0.11, 0.08, 0.13, 0.05, 0.09, 0.12, 0.07]

#: The June-2022 CPI peak, the figure used in the original measurement.
PI = 0.0906
#: The seven-company growth cross-section from the measured case.
# Twelve names, not seven. Exact rank equivalence is a claim that a
# relationship is STRUCTURAL, and MIN_EXACT_N = 8 exists because two
# independent series collide by chance 2/n! of the time -- 8.25% at n=4,
# 1.72% at n=5. The original seven-company fixture demonstrated a real
# collapse on a sample too small to prove one, and the guard said so.
NOMINAL_GROWTH = [0.32, 0.18, 0.12, 0.09, 0.04, -0.03, -0.11,
                  0.27, 0.21, 0.15, 0.01, -0.07]

# Sixteen-row columns, one per registered factor, for the registration panel in
# section 12. The registry's comparison set is now derived FROM THE REGISTRY, so
# a candidate must be offered a column for every factor already in it; these are
# the other five. Deliberately unrelated to each other and to LEVERAGE, so the
# only findings the panel can produce are the ones the test is asserting.
B_LEVERAGE = [0.33, 0.07, 0.52, 0.15, 0.41, 0.26, 0.60, 0.11]
LEVERAGE = A_LEVERAGE + B_LEVERAGE
GROWTH_COL = [0.11, -0.04, 0.27, 0.02, 0.15, -0.09, 0.33, 0.06,
              0.19, 0.01, -0.12, 0.24, 0.08, -0.02, 0.30, 0.13]
ACCEL_COL = [-0.05, 0.12, 0.03, -0.18, 0.21, 0.07, -0.01, 0.16,
             0.09, -0.11, 0.25, 0.04, -0.07, 0.14, 0.02, -0.15]
REAL_COL = [0.04, 0.22, -0.06, 0.17, -0.13, 0.09, 0.28, -0.02,
            0.12, 0.31, 0.05, -0.09, 0.20, 0.07, -0.04, 0.25]
COVERAGE_COL = [8.5, 2.1, -1.4, 12.0, 4.4, 0.6, 21.0, 3.3,
                6.8, -0.2, 9.9, 1.7, 15.5, 5.1, 2.9, 7.4]


def main() -> int:
    print("== 1. the taxonomy is named, versioned and complete ==")
    check("five normalization types", len(N.NORMALIZATION_TYPES) == 5,
          N.NORMALIZATION_TYPES)
    check("every type carries the economic question it answers",
          all(t in N.NORMALIZATION_QUESTION for t in N.NORMALIZATION_TYPES))
    check("the types are distinct strings",
          len(set(N.NORMALIZATION_TYPES)) == 5)
    check("the policy is versioned",
          N.NORMALIZATION_POLICY_VERSION == "normalization_policy_v1")
    check("the candidate model is named and is NOT v1",
          N.CANDIDATE_MODEL_VERSION == "candidate_equity_shaffer_v2"
          and N.CANDIDATE_MODEL_VERSION != pit_store.EQUITY_PIT_MODEL_VERSION)
    check("availability states come from pit_store, never redeclared",
          N.AVAIL_COMPLETE is pit_store.AVAIL_COMPLETE
          and N.AVAIL_UNAVAILABLE is pit_store.AVAIL_UNAVAILABLE
          and N.REASON_NO_PEERS is pit_store.REASON_NO_PEERS)
    row = N.zero_anchored_score(0.05, scale=0.10,
                                feature_key="RealRevenueGrowth").as_feature_row()
    check("norm_method written to pit_feature is a taxonomy constant",
          row["norm_method"] in N.NORMALIZATION_TYPES, row["norm_method"])
    check("the feature row carries availability and the flag together",
          row["availability"] == pit_store.AVAIL_COMPLETE and row["available_flag"] == 1)
    check("every unavailable reason is distinct",
          len({N.REASON_NO_VALUE, N.REASON_COHORT_TOO_SMALL_RANK,
               N.REASON_COHORT_TOO_SMALL_SCALE, N.REASON_ZERO_DISPERSION,
               N.REASON_NON_FINITE_SCALE, N.REASON_NO_BENCHMARK,
               N.REASON_HISTORY_TOO_SHORT, N.REASON_ZERO_HISTORY_DISPERSION,
               N.REASON_NOT_CALIBRATED}) == 9)

    print("== 2. ZERO_ANCHORED: zero means zero ==")
    zero = N.zero_anchored_score(0.0, scale=0.10)
    check("0% real growth scores EXACTLY 0.0", zero.score == 0.0, zero.score)
    check("...and it is available, not a missing value",
          zero.available and zero.availability == pit_store.AVAIL_COMPLETE)
    check("the anchor is recorded as zero",
          zero.anchor == 0.0 and zero.anchor_source == N.ANCHOR_ZERO)
    check("the scale actually used is recorded",
          zero.scale == 0.10 and zero.scale_source == N.SCALE_FIXED)
    up = N.zero_anchored_score(0.10, scale=0.10).score
    down = N.zero_anchored_score(-0.10, scale=0.10).score
    check("+10% real growth is genuinely bullish", up > 70.0, up)
    check("-10% real growth is genuinely bearish", down < -70.0, down)
    check("the curve is odd: S(-x) == -S(x)", abs(up + down) < 1e-12, (up, down))
    check("it never leaves [-100, +100]",
          all(-100.0 <= N.zero_anchored_score(v, scale=0.10).score <= 100.0
              for v in (-100.0, -1.0, 0.0, 1.0, 100.0)))
    check("it is strictly increasing",
          all(N.zero_anchored_score(a, scale=0.10).score
              < N.zero_anchored_score(b, scale=0.10).score
              for a, b in zip([-0.3, -0.1, 0.0, 0.05], [-0.1, 0.0, 0.05, 0.3])))
    # The docstring's worked table is a promise to the reader. Verify it.
    g_table = [(-0.30, -99.51), (-0.20, -96.40), (-0.10, -76.16), (-0.05, -46.21),
               (-0.02, -19.74), (0.0, 0.0), (0.02, 19.74), (0.05, 46.21),
               (0.10, 76.16), (0.20, 96.40), (0.30, 99.51)]
    check("the g = 0.10 table in the docstring is the real curve",
          all(abs(N.zero_anchored_score(x, scale=0.10).score - s) < 0.01
              for x, s in g_table))
    # The economic claim the type exists for.
    nominal, inflation = 0.08, 0.06
    real = N.zero_anchored_score(nominal - inflation, scale=0.10).score
    check("8% growth at 6% inflation is barely positive, not middling",
          0.0 < real < 25.0, real)

    print("== 3. BENCHMARK_ANCHORED: the bar means zero ==")
    m_s, n_band, note = N.benchmark_margin_50_75(A_EBITDA, A_MARGIN)
    check("M_s is the mean margin of the 50-75 EBITDA band",
          abs(m_s - 0.31) < 1e-12, m_s)
    check("the band is a real subset, not the whole cohort",
          n_band == 2 and n_band < len(A_EBITDA), n_band)
    check("the benchmark explains itself", "50-75" in note and "M_s" in note)
    parity = N.benchmark_anchored_score(m_s, m_s, scale=0.06)
    check("at parity with the bar the score is EXACTLY 0.0", parity.score == 0.0,
          parity.score)
    above = N.benchmark_anchored_score(m_s + 0.04, m_s, scale=0.06).score
    below = N.benchmark_anchored_score(m_s - 0.04, m_s, scale=0.06).score
    check("above the bar is positive, below is negative", above > 0.0 > below,
          (above, below))
    check("equal distances either side are equal and opposite",
          abs(above + below) < 1e-12, (above, below))
    check("the anchor and its source are recorded on the result",
          parity.anchor == m_s and parity.anchor_source == N.ANCHOR_CALLER_SUPPLIED)
    k_table = [(-0.18, -99.51), (-0.12, -96.40), (-0.06, -76.16), (-0.03, -46.21),
               (0.0, 0.0), (0.03, 46.21), (0.06, 76.16), (0.12, 96.40),
               (0.18, 99.51)]
    check("the k = 0.06 table in the docstring is the real curve",
          all(abs(N.benchmark_anchored_score(0.20 + d, 0.20, scale=0.06).score - s)
              < 0.01 for d, s in k_table))
    missing = N.benchmark_anchored_score(0.25, None, scale=0.06)
    check("an unavailable bar is unavailable, never a zero excess",
          not missing.available and missing.reason == N.REASON_NO_BENCHMARK)
    thin, _, _ = N.benchmark_margin_50_75([1.0, 2.0], [0.1, 0.2])
    check("a band too thin gives no benchmark rather than a made-up one",
          thin is None)
    orig_band = N.BENCHMARK_BAND_V1
    try:
        N.BENCHMARK_BAND_V1 = (0.0, 1.0)
        _, n_all, _ = N.benchmark_margin_50_75(A_EBITDA, A_MARGIN)
        check("the band is READ from the digested constant, not from a literal",
              n_all == len(A_EBITDA))
    finally:
        N.BENCHMARK_BAND_V1 = orig_band
    check("the band and floor values did not move when they were lifted out",
          N.BENCHMARK_BAND_V1 == (0.50, 0.75) and N.BENCHMARK_MIN_COHORT_V1 == 3)

    print("== 4. the scale parameters, and every degenerate case ==")
    fixed = N.resolve_scale(N.fixed_scale(0.06))
    check("a fixed scale resolves to itself", fixed.ok and fixed.value == 0.06)
    check("a zero fixed scale is refused, not divided by",
          N.resolve_scale(N.fixed_scale(0.0)).reason == N.REASON_NON_FINITE_SCALE)
    check("a negative fixed scale is refused",
          N.resolve_scale(N.fixed_scale(-1.0)).reason == N.REASON_NON_FINITE_SCALE)
    check("a NaN fixed scale is refused",
          N.resolve_scale(N.fixed_scale(float("nan"))).reason == N.REASON_NON_FINITE_SCALE)
    spread = N.resolve_scale(N.cohort_scale(), A_MARGIN + B_MARGIN)
    check("a cohort IQR resolves", spread.ok and spread.value > 0.0, spread.value)
    check("...and says it was the IQR over n peers",
          spread.source == N.SCALE_COHORT_IQR and spread.n_cohort == 16)
    check("the IQR matches statlib on a sorted copy",
          abs(spread.value - (statlib.percentile(sorted(A_MARGIN + B_MARGIN), 0.75)
                              - statlib.percentile(sorted(A_MARGIN + B_MARGIN), 0.25)))
          < 1e-12)
    # statlib.percentile assumes an ALREADY SORTED sequence and returns garbage
    # silently otherwise. The module must sort for itself.
    shuffled = [A_MARGIN[i] for i in (5, 0, 7, 2, 4, 1, 6, 3)]
    check("an unsorted cohort gives the same IQR as a sorted one -- the module sorts",
          abs(N.iqr(shuffled) - N.iqr(sorted(A_MARGIN))) < 1e-15,
          (N.iqr(shuffled), N.iqr(sorted(A_MARGIN))))
    flat = N.resolve_scale(N.cohort_scale(), [0.2] * 10)
    check("a cohort with zero dispersion is REFUSED, not divided by",
          flat.value is None and flat.reason == N.REASON_ZERO_DISPERSION, flat)
    small = N.resolve_scale(N.cohort_scale(), A_MARGIN[:5])
    check("a cohort too small to estimate dispersion is refused, distinctly",
          small.value is None and small.reason == N.REASON_COHORT_TOO_SMALL_SCALE)
    check("the two refusals are different reasons, not one vague one",
          flat.reason != small.reason)
    # The MAD rung. Selectable directly, and put on the same footing as an IQR
    # by the normal-consistency factor, so swapping the rung does not silently
    # rescale the curve.
    mad_scale = N.resolve_scale(N.cohort_scale(source=N.SCALE_COHORT_MAD),
                                A_MARGIN + B_MARGIN)
    check("a MAD scale resolves and says it was the MAD",
          mad_scale.ok and mad_scale.source == N.SCALE_COHORT_MAD, mad_scale)
    check("the MAD is converted to IQR units, so the two rungs are comparable",
          abs(mad_scale.value - N.MAD_TO_IQR * N.mad(A_MARGIN + B_MARGIN)) < 1e-15)
    check("...and lands within a factor of 2 of the IQR it stands in for",
          0.5 < mad_scale.value / spread.value < 2.0,
          (mad_scale.value, spread.value))
    # The recorded finding: the IQR -> MAD rung cannot fire, because an
    # interpolated IQR of zero needs half the cohort on one value, which zeroes
    # the MAD as well. Documented in resolve_scale rather than papered over.
    rigged = [1.0] + [2.0] * 8 + [9.0]
    check("a cohort with a zero IQR has a zero MAD too -- the measured finding",
          N.iqr(rigged) == 0.0 and N.mad(rigged) == 0.0)
    check("so a flat cohort refuses rather than falling through to a MAD",
          N.resolve_scale(N.cohort_scale(min_cohort=6), rigged).reason
          == N.REASON_ZERO_DISPERSION)
    check("a declared fixed fallback is used before giving up",
          N.resolve_scale(N.cohort_scale(fallback_fixed=0.06), [0.2] * 10).value == 0.06)
    check("a cohort-sourced scale with no cohort is a declaration bug, and raises",
          _raises(lambda: N.resolve_scale(N.cohort_scale()), N.NormalizationError))
    check("an unknown scale source raises",
          _raises(lambda: N.resolve_scale(N.ScaleSpec(source="vibes"), A_MARGIN),
                  N.NormalizationError))
    check("the multiplier widens the linear region",
          N.resolve_scale(N.cohort_scale(multiplier=2.0), A_MARGIN).value
          == 2.0 * N.resolve_scale(N.cohort_scale(), A_MARGIN).value)

    print("== 5. point-in-time: a scale may only see the cross-section as of the day ==")
    dates = ["2015-06-30"] * 8
    check("an as-of cohort passes the screen",
          len(N.cohort_as_of(A_MARGIN, dates, "2015-06-30")) == 8)
    check("one observation from the future raises, it is not quietly dropped",
          _raises(lambda: N.cohort_as_of(A_MARGIN, dates[:7] + ["2015-09-30"],
                                         "2015-06-30"), N.PointInTimeError))
    check("a full-history cohort (many dates, one as-of) raises",
          _raises(lambda: N.cohort_as_of(
              A_MARGIN, ["2013-06-30", "2014-06-30", "2015-06-30", "2016-06-30",
                         "2017-06-30", "2018-06-30", "2019-06-30", "2020-06-30"],
              "2015-06-30"), N.PointInTimeError))
    check("misaligned dates raise rather than zipping short",
          _raises(lambda: N.cohort_as_of(A_MARGIN, dates[:4], "2015-06-30"),
                  N.PointInTimeError))
    check("resolve_scale screens when it is given dates",
          _raises(lambda: N.resolve_scale(N.cohort_scale(), A_MARGIN,
                                          as_of_date="2015-06-30",
                                          value_dates=dates[:7] + ["2099-01-01"]),
                  N.PointInTimeError))
    check("dates without an as-of raise -- a screen needs a day to screen against",
          _raises(lambda: N.resolve_scale(N.cohort_scale(), A_MARGIN,
                                          value_dates=dates), N.NormalizationError))
    check("resolve_scale takes no history parameter at all",
          "history" not in N.resolve_scale.__code__.co_varnames)

    print("== 6. PERCENTILE_RANK, and the cohorts it refuses ==")
    ranked = N.percentile_rank_score(A_MARGIN[3], [m for i, m in enumerate(A_MARGIN)
                                                   if i != 3])
    check("a real cohort ranks", ranked.available and -100.0 <= ranked.score <= 100.0,
          ranked.score)
    check("the population size is recorded", ranked.n_cohort == 8)
    check("the best in the cohort scores +100",
          N.percentile_rank_score(max(A_MARGIN),
                                  [m for m in A_MARGIN if m != max(A_MARGIN)]).score
          == 100.0)
    check("lower-is-better inverts the score",
          N.percentile_rank_score(max(A_MARGIN),
                                  [m for m in A_MARGIN if m != max(A_MARGIN)],
                                  higher_is_better=False).score == -100.0)
    two = N.percentile_rank_score(0.30, [0.10])
    check("a 2-company cohort is REFUSED, not scored +/-100",
          two.score is None and two.reason == N.REASON_COHORT_TOO_SMALL_RANK, two)
    check("...and the refusal says how coarse the grid would have been",
          "points apart" in " ".join(two.notes))
    forced = N.percentile_rank_score(0.30, [0.10], min_peers=1)
    check("the refusal is not theoretical: without it the answer is +100",
          forced.score == 100.0, forced.score)
    check("three companies would still only produce -100/0/+100",
          {N.percentile_rank_score(v, [x for x in (0.1, 0.2, 0.3) if x != v],
                                   min_peers=1).score for v in (0.1, 0.2, 0.3)}
          == {-100.0, 0.0, 100.0})
    check("no peers at all is the pit_store reason",
          N.percentile_rank_score(0.3, []).reason is pit_store.REASON_NO_PEERS)
    check("a non-finite value is unavailable, never zero",
          N.percentile_rank_score(None, A_MARGIN).reason == N.REASON_NO_VALUE
          and N.percentile_rank_score(float("nan"), A_MARGIN).reason == N.REASON_NO_VALUE)

    print("== 7. HISTORICAL_Z needs a history, and says so when it has none ==")
    history = [0.28, 0.30, 0.27, 0.31, 0.29, 0.26, 0.30, 0.32, 0.28, 0.27, 0.31, 0.29]
    z = N.historical_z_score(0.40, history)
    check("a spike versus its own history scores strongly positive", z.score > 50.0,
          z.score)
    check("its own median is the anchor",
          abs(z.anchor - statlib.median(history)) < 1e-12)
    check("a value at its own median scores ~0",
          abs(N.historical_z_score(statlib.median(history), history).score) < 1e-9)
    short = N.historical_z_score(0.40, history[:8])
    check("too short a history is UNAVAILABLE with its own reason",
          short.score is None and short.reason == N.REASON_HISTORY_TOO_SHORT, short)
    dead = N.historical_z_score(0.40, [0.30] * 12)
    check("a history with no spread is refused, not infinitely unusual",
          dead.score is None and dead.reason == N.REASON_ZERO_HISTORY_DISPERSION)
    check("history dates are screened point-in-time too",
          _raises(lambda: N.historical_z_score(
              0.40, history, as_of_date="2015-06-30",
              history_dates=["2015-06-30"] * 11 + ["2016-06-30"]), N.PointInTimeError))

    print("== 8. ML_CALIBRATED refuses to run ==")
    ml = N.ml_calibrated_score(0.5)
    check("it returns no score", ml.score is None)
    check("it is unavailable", ml.availability == pit_store.AVAIL_UNAVAILABLE)
    check("and it says exactly why", ml.reason == N.REASON_NOT_CALIBRATED)
    check("the label is still reported, so the refusal is attributable",
          ml.normalization_type == N.ML_CALIBRATED)
    check("it refuses through the registry dispatch as well",
          N.normalize(N.FactorSpec(feature_key="X", normalization_type=N.ML_CALIBRATED,
                                   economic_question="q", rationale="r"),
                      0.5).reason == N.REASON_NOT_CALIBRATED)
    reg_ml = {}
    result = N.register_factor(reg_ml, N.FactorSpec(
        feature_key="MLThing", normalization_type=N.ML_CALIBRATED,
        economic_question="what does the model say?", rationale="reserved",
        feature_role=N.ROLE_PRODUCTION))
    check("an ML_CALIBRATED factor cannot be registered as production",
          reg_ml["MLThing"].feature_role == N.ROLE_RESEARCH, result.note)

    print("== 9. THE ACCEPTANCE TEST: the guard catches all three collapses ==")

    # (a) The inert deflator. RealRevenueGrowth = RevenueGrowth - pi.
    real_growth = [g - PI for g in NOMINAL_GROWTH]
    report_a = N.redundancy_check(real_growth, {"RevenueGrowth": NOMINAL_GROWTH})
    check("(a) growth - pi is REDUNDANT against nominal growth",
          report_a.verdict == N.VERDICT_REDUNDANT, report_a.verdict)
    check("(a) the match is named", report_a.matched_key == "RevenueGrowth")
    check("(a) it is reported as EXACT rank equivalence, not just a high rho",
          report_a.pooled[0].exact == N.EXACT_SAME)
    check("(a) rho is exactly 1.0", abs(report_a.rho - 1.0) < 1e-12, report_a.rho)
    check("(a) the cause is named as a cohort-constant shift",
          report_a.cause == N.CAUSE_COHORT_CONSTANT, report_a.cause)
    check("(a) the explanation is readable and mentions the shift",
          "constant" in report_a.explanation and "rank" in report_a.explanation,
          report_a.explanation)
    # And the measured consequence that started all of this.
    scored_nominal = [N.percentile_rank_score(
        g, [x for j, x in enumerate(NOMINAL_GROWTH) if j != i]).score
        for i, g in enumerate(NOMINAL_GROWTH)]
    scored_real = [N.percentile_rank_score(
        g, [x for j, x in enumerate(real_growth) if j != i]).score
        for i, g in enumerate(real_growth)]
    check("(a) the measured fact: the scores are identical to the digit",
          scored_nominal == scored_real, list(zip(scored_nominal, scored_real)))
    # The claim is IDENTITY, not a particular value: -33.333 was an artefact of
    # the original seven-company cohort. A company destroying real value (+4%
    # nominal into 9% inflation) must score the same either way and must not be
    # rescued by the deflator -- that is what makes the adjustment inert.
    idx = NOMINAL_GROWTH.index(0.04)
    check("(a) +4% growth into 9% inflation scores identically either way",
          abs(scored_real[idx] - scored_nominal[idx]) < 1e-9,
          (scored_nominal[idx], scored_real[idx]))
    check("(a) ...and real growth of -5.06% is still scored as merely mid-pack",
          scored_real[idx] > -100.0, scored_real[idx])

    # (b) The inert cohort benchmark, computed from a real 50-75 band per sector.
    m_a, _, _ = N.benchmark_margin_50_75(A_EBITDA, A_MARGIN)
    m_b, _, _ = N.benchmark_margin_50_75(B_EBITDA, B_MARGIN)
    check("(b) the two sectors have genuinely different bars",
          abs(m_a - m_b) > 0.2, (m_a, m_b))
    margins = A_MARGIN + B_MARGIN
    excess = [m - m_a for m in A_MARGIN] + [m - m_b for m in B_MARGIN]
    sectors = ["7372"] * 8 + ["5411"] * 8
    check("(b) excess == margin - M_s exactly",
          all(abs(e - (m - (m_a if i < 8 else m_b))) < 1e-15
              for i, (e, m) in enumerate(zip(excess, margins))))
    report_b = N.redundancy_check(excess, {"EBITDAMarginRank": margins}, sectors)
    check("(b) EBITDAExcess is REDUNDANT against EBITDAMargin",
          report_b.verdict == N.VERDICT_REDUNDANT, report_b.verdict)
    check("(b) the finding is WITHIN COHORT, which is where it lives",
          report_b.scope == N.SCOPE_COHORT, report_b.scope)
    check("(b) it is exact inside every sector",
          all(f.exact == N.EXACT_SAME for f in report_b.within_cohort),
          [f.exact for f in report_b.within_cohort])
    check("(b) both sectors were checked separately",
          {f.cohort_id for f in report_b.within_cohort} == {"7372", "5411"})
    pooled_b = report_b.pooled[0]
    check("(b) THE POINT: pooled, it is NOT exact and would have been missed",
          pooled_b.exact is None and pooled_b.verdict != N.VERDICT_REDUNDANT,
          (pooled_b.exact, pooled_b.rho))
    check("(b) and the report says so in words",
          "pooled view would have missed" in report_b.explanation,
          report_b.explanation)
    scored_margin = [N.percentile_rank_score(
        m, [x for j, x in enumerate(A_MARGIN) if j != i]).score
        for i, m in enumerate(A_MARGIN)]
    a_excess = [m - m_a for m in A_MARGIN]
    scored_excess = [N.percentile_rank_score(
        e, [x for j, x in enumerate(a_excess) if j != i]).score
        for i, e in enumerate(a_excess)]
    check("(b) a .30 Excess + .20 Margin block really is 0.50 on one quantity",
          scored_margin == scored_excess)

    # (c) The decorative log.
    log_scale = [math.log1p(e) for e in A_EBITDA + B_EBITDA]
    report_c = N.redundancy_check(log_scale, {"EBITDAScale": A_EBITDA + B_EBITDA})
    check("(c) log1p(EBITDA) is REDUNDANT against EBITDA",
          report_c.verdict == N.VERDICT_REDUNDANT, report_c.verdict)
    check("(c) the cause is named as a monotone transform",
          report_c.cause == N.CAUSE_MONOTONE, report_c.cause)
    check("(c) not mistaken for a constant shift",
          report_c.cause != N.CAUSE_COHORT_CONSTANT)
    check("(c) the measured fact: S(EBITDA) == S(log1p(EBITDA))",
          [N.percentile_rank_score(e, [x for j, x in enumerate(A_EBITDA) if j != i]).score
           for i, e in enumerate(A_EBITDA)]
          == [N.percentile_rank_score(math.log1p(e),
                                      [math.log1p(x) for j, x in enumerate(A_EBITDA)
                                       if j != i]).score
              for i, e in enumerate(A_EBITDA)])
    check("(c) and log1p is undefined for the 37.8% with EBITDA <= 0",
          _raises(lambda: math.log1p(-1.5), ValueError))

    print("== 10. the guard does NOT fire on genuinely different factors ==")
    report_d = N.redundancy_check(A_LEVERAGE, {"EBITDAMarginRank": A_MARGIN})
    check("margin vs leverage is INDEPENDENT",
          report_d.verdict == N.VERDICT_INDEPENDENT, report_d.verdict)
    check("...and rho is reported anyway, so the reader can judge",
          report_d.rho is not None and abs(report_d.rho) < N.RHO_HIGH, report_d.rho)
    # One adjacent pair swapped: rho = 0.976, and the ranking is NOT identical.
    near = list(A_MARGIN)
    near[0], near[4] = near[4], near[0]
    near_report = N.redundancy_check(near, {"EBITDAMarginRank": A_MARGIN})
    check("a merely-correlated pair is HIGHLY_CORRELATED, not REDUNDANT",
          near_report.verdict == N.VERDICT_CORRELATED, near_report.verdict)
    check("...because high rho is statistical and exact equivalence is structural",
          near_report.pooled[0].exact is None and near_report.rho > N.RHO_HIGH,
          near_report.rho)
    check("...and the explanation says which of the two it is",
          "Statistical, not structural" in near_report.explanation)
    check("an exactly reversed ranking is redundant too, and named as such",
          N.redundancy_check([-m for m in A_MARGIN],
                             {"EBITDAMarginRank": A_MARGIN}).cause
          == N.CAUSE_MONOTONE_REVERSED)
    check("a cohort too small to judge is SKIPPED and named, never folded in",
          N.redundancy_check(A_MARGIN, {"L": A_LEVERAGE},
                             ["x", "x", "x", "y", "y", "y", "z", "z"]).skipped != ())
    check("misaligned series raise rather than zipping short",
          _raises(lambda: N.redundancy_check(A_MARGIN, {"L": A_LEVERAGE[:5]}),
                  N.NormalizationError))
    check("spearman is Pearson-on-ranks and handles ties",
          abs(N.spearman_rho([1.0, 2.0, 2.0, 3.0], [10.0, 20.0, 20.0, 30.0]) - 1.0)
          < 1e-12)
    check("a constant series has no correlation to report",
          N.spearman_rho([1.0] * 6, A_MARGIN[:6]) is None)
    check("exact equivalence is a tolerance test, not float ==",
          N.rank_equivalence([1.0, 2.0, 3.0], [1.0 + 1e-18, 2.0, 3.0]) == N.EXACT_SAME)

    print("== 11. THE CENTRAL CLAIM, stated as one test ==")
    # Same values, same cohort, one shifted by a cohort-wide constant.
    shifted = [m - m_a for m in A_MARGIN]
    rank_plain = [N.percentile_rank_score(
        v, [x for j, x in enumerate(A_MARGIN) if j != i]).score
        for i, v in enumerate(A_MARGIN)]
    rank_shifted = [N.percentile_rank_score(
        v, [x for j, x in enumerate(shifted) if j != i]).score
        for i, v in enumerate(shifted)]
    anchored_plain = [N.benchmark_anchored_score(v, m_a, scale=0.06).score
                      for v in A_MARGIN]
    anchored_shifted = [N.benchmark_anchored_score(v, m_a, scale=0.06).score
                        for v in shifted]
    check("PERCENTILE_RANK: x and x - c score IDENTICALLY, to the digit",
          rank_plain == rank_shifted, list(zip(rank_plain, rank_shifted)))
    check("BENCHMARK_ANCHORED: x and x - c do NOT",
          anchored_plain != anchored_shifted)
    check("...and the difference is large, not a rounding artefact",
          max(abs(a - b) for a, b in zip(anchored_plain, anchored_shifted)) > 20.0,
          max(abs(a - b) for a, b in zip(anchored_plain, anchored_shifted)))
    check("the anchored score knows where the bar is: above it is positive",
          all((v > m_a) == (s > 0.0)
              for v, s in zip(A_MARGIN, anchored_plain) if v != m_a))
    check("the ranked score cannot know: the cohort's worst scores -100 "
          "however profitable it is",
          min(rank_plain) == -100.0 and min(A_MARGIN) > 0.0)

    print("== 12. the registry refuses what it should refuse ==")
    registry = N.candidate_v2_registry()
    check("the v2 EBITDA family is seeded",
          set(registry) == {"EBITDAMarginRank", "EBITDAExcessLevel", "EBITDAGrowth",
                            "EBITDAAcceleration", "EBITDAScale", "RealRevenueGrowth",
                            "InterestCoverageRank"},
          sorted(registry))
    check("InterestCoverageRank is a rank, higher is better, and states its "
          "domain policy",
          registry["InterestCoverageRank"].normalization_type == N.PERCENTILE_RANK
          and registry["InterestCoverageRank"].higher_is_better is True
          and "interest_expense_zero" in " ".join(registry["InterestCoverageRank"].notes))
    check("a negative coverage ranks below every positive peer",
          N.percentile_rank_score(-4.0, [1.5, 3.0, 8.0, 12.0]).score == -100.0)
    check("growth and acceleration are RATES now: no asset base in their text, "
          "and both name the base policy",
          all("Assets" not in (r.rationale + " ".join(r.notes))
              for r in (registry["EBITDAGrowth"], registry["EBITDAAcceleration"]))
          and all("EBITDA_GROWTH_BASE_POLICY_V1" in " ".join(registry[k].notes)
                  for k in ("EBITDAGrowth", "EBITDAAcceleration")))
    check("EBITDAMarginRank is a rank", registry["EBITDAMarginRank"].normalization_type
          == N.PERCENTILE_RANK)
    check("EBITDAExcessLevel is benchmark-anchored on M_s",
          registry["EBITDAExcessLevel"].normalization_type == N.BENCHMARK_ANCHORED
          and registry["EBITDAExcessLevel"].anchor_source
          == N.ANCHOR_COHORT_50_75_MEAN_MARGIN)
    check("RealRevenueGrowth is zero-anchored",
          registry["RealRevenueGrowth"].normalization_type == N.ZERO_ANCHORED
          and registry["RealRevenueGrowth"].anchor_source == N.ANCHOR_ZERO)
    check("EBITDAGrowth and EBITDAAcceleration are zero-anchored, and justified",
          all(registry[k].normalization_type == N.ZERO_ANCHORED
              and len(registry[k].rationale) > 40
              for k in ("EBITDAGrowth", "EBITDAAcceleration")))
    check("EBITDAAcceleration derives its scale from the cohort, not a fixed g",
          registry["EBITDAAcceleration"].scale_source in N.COHORT_SCALE_SOURCES)
    check("EBITDAScale is ranked, and the docs say the log is inert",
          registry["EBITDAScale"].normalization_type == N.PERCENTILE_RANK
          and "inert" in registry["EBITDAScale"].notes[0])
    check("every seeded factor states its economic question and rationale",
          all(s.economic_question and s.rationale for s in registry.values()))
    check("every anchored factor carries a scale source",
          all(s.scale is not None for s in registry.values()
              if s.normalization_type in (N.ZERO_ANCHORED, N.BENCHMARK_ANCHORED)))
    check("nothing is registered as production",
          all(s.feature_role == N.ROLE_RESEARCH for s in registry.values()))
    check("the seed is a fresh copy, so a caller cannot mutate the policy",
          (registry.clear() or True) and len(N.candidate_v2_registry()) == 7)

    registry = N.candidate_v2_registry()
    check("a factor with NO normalization_type is refused",
          _raises(lambda: N.register_factor(registry, {
              "feature_key": "MysteryFactor", "economic_question": "q",
              "rationale": "r"}), N.NormalizationError))
    check("an unknown normalization_type is refused",
          _raises(lambda: N.register_factor(registry, {
              "feature_key": "MysteryFactor", "normalization_type": "just_scale_it",
              "economic_question": "q", "rationale": "r"}), N.NormalizationError))
    check("a factor with no economic question is refused",
          _raises(lambda: N.register_factor(registry, N.FactorSpec(
              feature_key="Q", normalization_type=N.PERCENTILE_RANK, rationale="r")),
              N.NormalizationError))
    check("BENCHMARK_ANCHORED with no bar is refused",
          _raises(lambda: N.register_factor(registry, N.FactorSpec(
              feature_key="NoBar", normalization_type=N.BENCHMARK_ANCHORED,
              economic_question="q", rationale="r", scale=N.fixed_scale(0.06))),
              N.NormalizationError))
    check("BENCHMARK_ANCHORED with no scale is refused",
          _raises(lambda: N.register_factor(registry, N.FactorSpec(
              feature_key="NoK", normalization_type=N.BENCHMARK_ANCHORED,
              economic_question="q", rationale="r",
              anchor_source=N.ANCHOR_COHORT_50_75_MEAN_MARGIN)),
              N.NormalizationError))
    check("a PERCENTILE_RANK factor claiming an anchor is refused",
          _raises(lambda: N.register_factor(registry, N.FactorSpec(
              feature_key="FalseAnchor", normalization_type=N.PERCENTILE_RANK,
              economic_question="q", rationale="r",
              anchor_source=N.ANCHOR_COHORT_50_75_MEAN_MARGIN)),
              N.NormalizationError))
    check("re-registering a key silently is refused",
          _raises(lambda: N.register_factor(registry, N.FactorSpec(
              feature_key="EBITDAScale", normalization_type=N.PERCENTILE_RANK,
              economic_question="q", rationale="r")), N.NormalizationError))
    # THE COMPARISON PANEL. One column per REGISTERED factor, 16 rows, aligned
    # by position. register_factor derives the comparison set from the registry
    # and refuses a panel that covers only the factor the caller happens to
    # worry about -- so the panel below is the contract, not a convenience.
    panel = {
        "EBITDAMarginRank": margins,
        "EBITDAExcessLevel": excess,
        "EBITDAGrowth": GROWTH_COL,
        "EBITDAAcceleration": ACCEL_COL,
        "EBITDAScale": A_EBITDA + B_EBITDA,
        "RealRevenueGrowth": REAL_COL,
        "InterestCoverageRank": COVERAGE_COL,
    }
    check("registering with NO samples is now refused, not silently skipped",
          _raises(lambda: N.register_factor(registry, N.FactorSpec(
              feature_key="Unchecked", normalization_type=N.PERCENTILE_RANK,
              economic_question="q", rationale="r")), N.NormalizationError))
    check("a PARTIAL panel is refused and names what is missing",
          _raises(lambda: N.register_factor(registry, N.FactorSpec(
              feature_key="Partial", normalization_type=N.PERCENTILE_RANK,
              economic_question="q", rationale="r"),
              sample_values=LEVERAGE, samples={"EBITDAMarginRank": margins},
              cohort_ids=sectors), N.NormalizationError))
    check("a cohort-scoped factor with NO cohort_ids is refused",
          _raises(lambda: N.register_factor(registry, N.FactorSpec(
              feature_key="NoCohorts", normalization_type=N.PERCENTILE_RANK,
              economic_question="q", rationale="r"),
              sample_values=LEVERAGE, samples=panel), N.NormalizationError))
    ok = N.register_factor(registry, N.FactorSpec(
        feature_key="LeverageRank", normalization_type=N.PERCENTILE_RANK,
        economic_question="how levered versus peers?", rationale="ordering is the point",
        higher_is_better=False), sample_values=LEVERAGE,
        samples=panel, cohort_ids=sectors)
    check("a genuinely new factor is accepted",
          ok.accepted and "LeverageRank" in registry)
    check("...with its redundancy evidence attached",
          ok.redundancy is not None
          and ok.redundancy.verdict == N.VERDICT_INDEPENDENT, ok.redundancy.verdict)
    check("...and the Registration says what it was checked against, and where",
          ok.checked and set(ok.compared_keys) == set(panel)
          and ok.scopes_run == (N.SCOPE_POOLED, N.SCOPE_COHORT), ok.note)
    panel["LeverageRank"] = LEVERAGE
    check("a redundant factor is REFUSED at registration",
          _raises(lambda: N.register_factor(registry, N.FactorSpec(
              feature_key="EBITDAExcessRank", normalization_type=N.PERCENTILE_RANK,
              economic_question="how far above the bar versus peers?",
              rationale="the excess, ranked"),
              sample_values=excess, samples=panel,
              cohort_ids=sectors), N.RedundancyError))
    forced_reg = N.register_factor(registry, N.FactorSpec(
        feature_key="EBITDAExcessRank", normalization_type=N.PERCENTILE_RANK,
        economic_question="how far above the bar versus peers?",
        rationale="kept deliberately, with the finding recorded"),
        sample_values=excess, samples=panel,
        cohort_ids=sectors, allow_redundant=True)
    check("the escape hatch records the finding instead of deleting the check",
          forced_reg.accepted and forced_reg.redundancy.is_redundant
          and "DESPITE" in forced_reg.note, forced_reg.note)

    print("== 13. the registry drives the transform, not the caller ==")
    seeded = N.candidate_v2_registry()
    excess_result = N.normalize(seeded["EBITDAExcessLevel"], A_MARGIN[5],
                                benchmark=m_a, cohort_values=A_MARGIN,
                                as_of_date="2015-06-30",
                                value_dates=["2015-06-30"] * 8)
    check("EBITDAExcessLevel normalises against its declared bar",
          excess_result.available and excess_result.anchor == m_a
          and excess_result.normalization_type == N.BENCHMARK_ANCHORED)
    check("...with a cohort-derived k, recorded on the row",
          excess_result.scale_source == N.SCALE_COHORT_IQR
          and excess_result.scale > 0.0, excess_result.scale)
    check("...and the best margin in the cohort clears the bar",
          excess_result.score > 0.0, excess_result.score)
    at_bar = N.normalize(seeded["EBITDAExcessLevel"], m_a, benchmark=m_a,
                         cohort_values=A_MARGIN)
    check("...while a company exactly at the bar scores 0.0", at_bar.score == 0.0)
    check("a point-in-time violation stops the dispatch too",
          _raises(lambda: N.normalize(seeded["EBITDAExcessLevel"], A_MARGIN[5],
                                      benchmark=m_a, cohort_values=A_MARGIN,
                                      as_of_date="2015-06-30",
                                      value_dates=["2015-06-30"] * 7 + ["2016-06-30"]),
                  N.PointInTimeError))
    growth_result = N.normalize(seeded["RealRevenueGrowth"], 0.04 - PI)
    check("RealRevenueGrowth normalises at its declared fixed g",
          growth_result.scale == N.DEFAULT_REAL_GROWTH_SCALE_G
          and growth_result.score < 0.0, growth_result.score)
    check("...and +4% into 9% inflation is now NEGATIVE, which was the point",
          growth_result.score < -40.0, growth_result.score)
    scale_result = N.normalize(seeded["EBITDAScale"], A_EBITDA[7],
                               peer_values=A_EBITDA[:7])
    check("EBITDAScale ranks the raw dollars", scale_result.score == 100.0)
    check("...and the registry's claim is true: the log changes nothing",
          N.normalize(seeded["EBITDAScale"], math.log1p(A_EBITDA[7]),
                      peer_values=[math.log1p(v) for v in A_EBITDA[:7]]).score
          == scale_result.score)
    check("a lower-is-better factor inverts through the dispatch",
          N.normalize(registry["LeverageRank"], max(A_LEVERAGE),
                      peer_values=[v for v in A_LEVERAGE if v != max(A_LEVERAGE)]).score
          == -100.0)

    print("== 14. the registry serialises for a lineage row ==")
    spec = N.registry_spec(N.candidate_v2_registry())
    encoded = json.dumps(spec, sort_keys=True)
    check("it round-trips through json",
          json.dumps(json.loads(encoded), sort_keys=True) == encoded)
    check("it is substantial enough to be a specification", len(encoded) > 2000,
          len(encoded))
    check("every factor carries its normalization_type in the dump",
          all(f["normalization_type"] in N.NORMALIZATION_TYPES
              for f in json.loads(encoded)["factors"].values()))
    check("the dump names the model and the policy version",
          json.loads(encoded)["model_version"] == N.CANDIDATE_MODEL_VERSION
          and json.loads(encoded)["policy_version"] == N.NORMALIZATION_POLICY_VERSION)
    check("a redundancy report serialises too",
          json.loads(json.dumps(report_b.as_dict(), sort_keys=True))["verdict"]
          == N.VERDICT_REDUNDANT)
    check("a NormResult serialises too",
          json.loads(json.dumps(zero.as_dict(), sort_keys=True))["anchor"] == 0.0)

    print("== 15. production is untouched ==")
    check("importing it pulls in no production scoring module",
          not {"company_scoring", "sector_scoring", "asset_models", "prediction",
               "hedging", "streamlit"} & set(sys.modules),
          sorted({"company_scoring", "sector_scoring", "asset_models", "prediction",
                  "hedging", "streamlit"} & set(sys.modules)))
    check("the candidate's factors never claim the v1 model version",
          all(s.model_version == N.CANDIDATE_MODEL_VERSION
              for s in N.candidate_v2_registry().values()))
    check("it imports statlib rather than reimplementing it",
          N.percentile is statlib.percentile
          and N.average_ranks is statlib.average_ranks
          and N.percentile_rank_within is statlib.percentile_rank_within
          and N.percentile_to_score is statlib.percentile_to_score
          and N.median is statlib.median and N.clamp is statlib.clamp
          and N.is_finite is statlib.is_finite and N.winsorize is statlib.winsorize)

    print()
    print("FAILURES:", len(fails), fails if fails else "")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
