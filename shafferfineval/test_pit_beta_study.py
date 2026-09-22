"""Tests for pit_beta_study. Plain script: check(), main(), PASS/FAIL, exit code.

Three kinds of test live here, and the second kind is the one that matters.

1. ARITHMETIC. The OLS slope, its classical and HC1 standard errors and the
   Student-t quantile are all closed forms written out by hand because scipy is
   not a dependency. Each is checked against a value computed independently --
   a five-point regression worked through on paper, and published t tables.

2. INVARIANTS OF THE SAMPLE. The study's claim is that its cross-section is the
   one the replay would see. So the tests build a real (tiny) PIT store and
   assert the selectors behave: a restatement filed after the as-of date is not
   used, a D&A filed for a different fiscal year does not assemble an EBITDA,
   and a fact past its staleness budget drops out.

3. PROVENANCE, ENFORCED. The module claims it reads no prices and writes
   nothing. A SQLite authorizer records every table the study actually touches
   and every write action it attempts, so the claim is measured rather than
   asserted -- a grep over the source would be fooled by this very docstring.
"""

from __future__ import annotations

import math
import os
import re
import sqlite3
import sys
import tempfile

import pit_beta_study as bs
import pit_policy
import pit_store

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    status = "PASS" if cond else "FAIL"
    line = f"[{status}] {name}"
    if detail:
        line += f"  -- {detail}"
    print(line)
    if not cond:
        FAILURES.append(name)
    return bool(cond)


def close(a, b, tol=1e-9) -> bool:
    return a is not None and b is not None and abs(float(a) - float(b)) <= tol


# --------------------------------------------------------------------------
# 1. Arithmetic
# --------------------------------------------------------------------------

#: The hand-worked example, computed on paper before the code was written:
#:   x = 1..5, y = 2,4,5,4,5.  xbar = 3, ybar = 4.
#:   Sxx = 4+1+0+1+4 = 10.   Sxy = 4+0+0+0+2 = 6.
#:   beta = 0.6,  alpha = 4 - 1.8 = 2.2.
#:   fitted 2.8, 3.4, 4.0, 4.6, 5.2  ->  residuals -0.8, .6, 1.0, -.6, -.2
#:   SSE = .64+.36+1+.36+.04 = 2.4,  s^2 = 2.4/3 = 0.8
#:   SE(beta) = sqrt(0.8/10) = sqrt(0.08) = 0.28284271247461906
#:   HC1 meat = 4(.64)+1(.36)+0(1)+1(.36)+4(.04) = 3.44
#:   Var_HC1 = (5/3)(3.44)/100 = 0.05733333...,  SE = 0.23944380...
HAND_X = [1.0, 2.0, 3.0, 4.0, 5.0]
HAND_Y = [2.0, 4.0, 5.0, 4.0, 5.0]


def test_ols_against_hand_computation() -> None:
    fit = bs.ols_slope_intercept(HAND_X, HAND_Y)
    check("ols: returns a fit", fit is not None)
    if fit is None:
        return
    check("ols: beta = 0.6 exactly", close(fit["beta"], 0.6, 1e-12),
          f"got {fit['beta']!r}")
    check("ols: alpha = 2.2 exactly", close(fit["alpha"], 2.2, 1e-12),
          f"got {fit['alpha']!r}")
    check("ols: Sxx = 10", close(fit["sxx"], 10.0, 1e-12))
    check("ols: SSE = 2.4", close(fit["sse"], 2.4, 1e-12))
    check("ols: df = n - 2 = 3", fit["df"] == 3)
    check("ols: sigma = sqrt(0.8)", close(fit["sigma"], math.sqrt(0.8), 1e-12))
    check("ols: classical SE(beta) = sqrt(0.08)",
          close(fit["se_beta"], math.sqrt(0.08), 1e-12), f"got {fit['se_beta']!r}")
    check("ols: HC1 SE(beta) = sqrt((5/3)*3.44/100)",
          close(fit["se_beta_hc1"], math.sqrt((5.0 / 3.0) * 3.44 / 100.0), 1e-12),
          f"got {fit['se_beta_hc1']!r}")
    # R^2: Syy = 4+0+1+0+1 = 6 about ybar=4 -> 1 - 2.4/6 = 0.6
    check("ols: R2 = 0.6", close(fit["r2"], 0.6, 1e-12))
    # t against ONE, not zero: (0.6 - 1)/0.28284271 = -1.41421356
    check("ols: t_vs_one = -sqrt(2)", close(fit["t_vs_one"], -math.sqrt(2), 1e-9),
          f"got {fit['t_vs_one']!r}")
    check("ols: residuals returned in input order",
          all(close(a, b, 1e-12) for a, b in
              zip(fit["residuals"], [-0.8, 0.6, 1.0, -0.6, -0.2])))


def test_hc1_matches_the_full_sandwich() -> None:
    """The module computes HC1 for the slope with a one-line shortcut.

    That shortcut is the (2,2) element of (X'X)^-1 X' diag(e^2) X (X'X)^-1 when
    X is [1, x] -- true, but not obviously true, so it is checked against the
    full 2x2 sandwich built here from first principles on deliberately
    heteroskedastic data, where a wrong formula could not hide.
    """
    import random
    random.seed(7)
    xs = [random.uniform(0.0, 10.0) for _ in range(50)]
    ys = [2.0 + 0.7 * x + random.gauss(0.0, 0.2 * (1.0 + x)) for x in xs]
    fit = bs.ols_slope_intercept(xs, ys)
    if fit is None:
        check("hc1: fit exists", False)
        return
    n = len(xs)
    X = [[1.0, x] for x in xs]
    xtx = [[sum(X[i][a] * X[i][b] for i in range(n)) for b in (0, 1)]
           for a in (0, 1)]
    det = xtx[0][0] * xtx[1][1] - xtx[0][1] * xtx[1][0]
    inv = [[xtx[1][1] / det, -xtx[0][1] / det],
           [-xtx[1][0] / det, xtx[0][0] / det]]
    e = fit["residuals"]
    meat = [[sum(X[i][a] * X[i][b] * e[i] * e[i] for i in range(n))
             for b in (0, 1)] for a in (0, 1)]
    tmp = [[sum(inv[a][k] * meat[k][b] for k in (0, 1)) for b in (0, 1)]
           for a in (0, 1)]
    sand = [[sum(tmp[a][k] * inv[k][b] for k in (0, 1)) for b in (0, 1)]
            for a in (0, 1)]
    want = math.sqrt(sand[1][1] * n / (n - 2))
    check("hc1: the shortcut equals the full 2x2 sandwich on heteroskedastic data",
          close(fit["se_beta_hc1"], want, 1e-12),
          f"module {fit['se_beta_hc1']!r} vs sandwich {want!r}")
    check("hc1: and it differs from the classical SE when errors fan out",
          abs(fit["se_beta_hc1"] - fit["se_beta"]) > 1e-4,
          f"classical {fit['se_beta']:.6f} HC1 {fit['se_beta_hc1']:.6f}")


def test_ols_refuses_what_it_cannot_identify() -> None:
    check("ols: refuses n < 3", bs.ols_slope_intercept([1.0, 2.0], [1.0, 2.0]) is None)
    check("ols: refuses zero spread in x",
          bs.ols_slope_intercept([2.0, 2.0, 2.0], [1.0, 5.0, 3.0]) is None)
    check("ols: refuses mismatched lengths",
          bs.ols_slope_intercept([1.0, 2.0, 3.0], [1.0, 2.0]) is None)


#: Published two-sided 97.5% Student-t quantiles.
T_TABLE = {1: 12.70620474, 2: 4.30265273, 5: 2.57058184, 10: 2.22813885,
           20: 2.08596345, 30: 2.04227246, 100: 1.98397152}


def test_student_t() -> None:
    check("t: sf(0, df) = 0.5", close(bs.student_t_sf(0.0, 10), 0.5, 1e-12))
    check("t: sf is symmetric",
          close(bs.student_t_sf(-1.3, 7), 1.0 - bs.student_t_sf(1.3, 7), 1e-12))
    ok = True
    for df, want in T_TABLE.items():
        got = bs.student_t_ppf(0.975, df)
        if not close(got, want, 1e-6):
            ok = False
            print(f"        df={df} want {want} got {got}")
    check("t: ppf(0.975, df) matches published table for 7 df values", ok)
    check("t: ppf(0.95, 10) = 1.812461",
          close(bs.student_t_ppf(0.95, 10), 1.81246112, 1e-6))
    check("t: ppf -> normal 1.959964 as df grows",
          close(bs.student_t_ppf(0.975, 10_000_000), 1.95996398, 1e-5),
          f"got {bs.student_t_ppf(0.975, 10_000_000)!r}")
    check("t: two-sided p at the 97.5 quantile is 0.05",
          close(bs.student_t_two_sided_p(T_TABLE[10], 10), 0.05, 1e-7))
    check("t: two-sided p at 0 is 1.0",
          close(bs.student_t_two_sided_p(0.0, 10), 1.0, 1e-12))
    check("t: incomplete beta endpoints",
          bs.regularized_incomplete_beta(2.0, 3.0, 0.0) == 0.0
          and bs.regularized_incomplete_beta(2.0, 3.0, 1.0) == 1.0)
    # I_x(1,1) = x for every x: the uniform case, an independent identity.
    check("t: I_x(1,1) = x",
          all(close(bs.regularized_incomplete_beta(1.0, 1.0, x), x, 1e-12)
              for x in (0.1, 0.25, 0.5, 0.75, 0.9)))


def test_ci_excludes_one_flag() -> None:
    # beta = 0.6, SE = 0.2828, t_crit(3) = 3.1824 -> CI half width 0.9001
    # -> [-0.300, 1.500], which CONTAINS 1. The flag must say so.
    fit = bs.ols_slope_intercept(HAND_X, HAND_Y)
    check("ci: hand example's interval contains 1",
          fit is not None and fit["ci_excludes_one"] is False,
          f"ci = [{fit['ci_lo']:.4f}, {fit['ci_hi']:.4f}]" if fit else "")
    # A tight, clearly-not-one slope: y = 0.5x with a whisper of noise.
    xs = [float(i) for i in range(40)]
    ys = [0.5 * x + (0.01 if i % 2 else -0.01) for i, x in enumerate(xs)]
    tight = bs.ols_slope_intercept(xs, ys)
    check("ci: a clean beta=0.5 excludes 1 on both SEs",
          tight is not None and tight["ci_excludes_one"]
          and tight["ci_excludes_one_hc1"],
          f"beta={tight['beta']:.4f}" if tight else "")


# --------------------------------------------------------------------------
# The identity the whole study turns on
# --------------------------------------------------------------------------

#: log-revenue deviations (x - xbar) for x = 0,1,2,3 are -1.5,-0.5,0.5,1.5.
#: log-margin deviations chosen so that sum(dx*dm) = 0 EXACTLY:
#:   -1.5(0.3) - 0.5(-0.7) + 0.5(0.5) + 1.5(-0.1)
#:    = -0.45 + 0.35 + 0.25 - 0.15 = 0
#: so the fitted beta of log(EBITDA) = log(Rev) + log(margin) on log(Rev) is
#: exactly 1, and the residual must be a strictly increasing function of margin.
_X = [0.0, 1.0, 2.0, 3.0]
_M = [-1.0 + d for d in (0.3, -0.7, 0.5, -0.1)]


def _synthetic_records(sic: str = "2834"):
    out = []
    for i, (x, m) in enumerate(zip(_X, _M)):
        out.append({
            "entity_id": 1000 + i, "disposition": bs.IN_SAMPLE, "sic": sic,
            "log_revenue": x, "log_ebitda": x + m, "margin": math.exp(m),
            "revenue": math.exp(x), "ebitda": math.exp(x + m), "in_base": True,
        })
    return out


def test_beta_one_makes_the_residual_margin() -> None:
    recs = _synthetic_records()
    fits = bs.fit_sectors(recs, pit_policy_rung(), min_n=4, min_sxx=0.5)
    check("identity: one sector reported", fits["n_sectors_reported"] == 1)
    if fits["n_sectors_reported"] != 1:
        return
    sec = fits["sectors"][0]
    check("identity: constructed beta is exactly 1", close(sec["beta"], 1.0, 1e-12),
          f"got {sec['beta']!r}")
    check("identity: Spearman(residual, margin) = 1.000000 when beta = 1",
          close(sec["rho_residual_margin"], 1.0, 1e-12),
          f"got {sec['rho_residual_margin']!r}")
    check("identity: Spearman(residual, log margin) = 1.000000 too",
          close(sec["rho_residual_log_margin"], 1.0, 1e-12))


def pit_policy_rung() -> str:
    import pit_peers
    return pit_peers.RUNG_SIC2


def test_fit_sectors_refuses_thin_sectors() -> None:
    recs = _synthetic_records()
    fits = bs.fit_sectors(recs, pit_policy_rung(), min_n=bs.MIN_SECTOR_N)
    check("refusal: a 4-firm sector is refused at the default floor of 30",
          fits["n_sectors_reported"] == 0 and fits["n_sectors_refused"] == 1)
    check("refusal: the refusal names the size and the reason",
          fits["refused"] and fits["refused"][0]["n"] == 4
          and "30" in fits["refused"][0]["reason"],
          str(fits["refused"][:1]))
    check("refusal: refused firms are counted, not hidden",
          fits["n_firms_refused"] == 4 and fits["n_firms_reported"] == 0)
    check("refusal: the common within-sector beta still uses the thin sector",
          fits["common_beta"] is not None and fits["common_beta"]["n"] == 4)


def test_fixed_effects_recovers_a_common_slope() -> None:
    groups = {
        "A": [(x, 10.0 + 0.5 * x) for x in (1.0, 2.0, 3.0, 4.0)],
        "B": [(x, -3.0 + 0.5 * x) for x in (5.0, 6.0, 7.0, 8.0)],
    }
    fe = bs.fixed_effects_slope(groups)
    check("fe: recovers the common slope 0.5 across shifted intercepts",
          fe is not None and close(fe["beta"], 0.5, 1e-12),
          f"got {fe['beta']!r}" if fe else "")
    check("fe: df = N - S - 1", fe is not None and fe["df"] == 8 - 2 - 1)
    check("fe: a single-point group cannot contribute",
          bs.fixed_effects_slope({"A": [(1.0, 1.0)]}) is None)


def test_intraclass_correlation() -> None:
    # Every firm perfectly repeats itself across 4 dates; firms differ.
    # ICC = 1, design effect = mean group size = 4, effective N = 3 firms.
    groups = {1: [1.0] * 4, 2: [5.0] * 4, 3: [9.0] * 4}
    icc = bs.intraclass_correlation(groups)
    check("icc: perfect within-firm repetition gives ICC = 1",
          icc is not None and close(icc["icc"], 1.0, 1e-12))
    check("icc: design effect = mean group size",
          icc is not None and close(icc["design_effect"], 4.0, 1e-12))
    check("icc: effective N collapses to the number of firms",
          icc is not None and close(icc["n_effective"], 3.0, 1e-9),
          f"got {icc['n_effective']!r}" if icc else "")
    flat = bs.intraclass_correlation({1: [2.0, 2.0], 2: [2.0, 2.0]})
    check("icc: no variance anywhere is ICC 0, not a crash",
          flat is not None and close(flat["icc"], 0.0, 1e-12))
    check("icc: nothing to measure returns None, never 0",
          bs.intraclass_correlation({1: [1.0], 2: [2.0]}) is None)


def test_describe_and_quantile() -> None:
    d = bs.describe([1.0, 2.0, 3.0, 4.0, 5.0])
    check("describe: median of 1..5 is 3", close(d["median"], 3.0))
    check("describe: mean of 1..5 is 3", close(d["mean"], 3.0))
    check("describe: p10 of 1..5 is 1.4", close(d["p10"], 1.4, 1e-12))
    check("describe: empty reports n=0 with None, not zeros",
          bs.describe([])["n"] == 0 and bs.describe([])["median"] is None)


# --------------------------------------------------------------------------
# 2. Invariants of the sample, against a real (tiny) PIT store
# --------------------------------------------------------------------------

AS_OF = "2020-06-30"
PERIOD = "2019-12-31"
PRIOR = "2018-12-31"


def _fact(entity_id: int, tag: str, period_end: str, qtrs: int, val: float,
          accn: str, filed: str, available: str, unit: str = "USD"):
    """One pit_fact tuple in FACT_COLUMNS order."""
    return (entity_id, "us-gaap", tag, unit, None, period_end, qtrs, "", "",
            val, accn, "10-K", filed, None, None, available,
            pit_policy.LATENCY_POLICY_VERSION, "test")


def _build_fixture(path: str) -> tuple[sqlite3.Connection, dict[str, int]]:
    conn = pit_store.init_db(path)
    ids: dict[str, int] = {}

    # A: complete and profitable. Revenue 1000, op income 80, D&A 20 -> EBITDA 100.
    ids["A"] = pit_store.upsert_entity(conn, "0000000001")
    # B: D&A filed for the PRIOR year only -> the assembly must refuse.
    ids["B"] = pit_store.upsert_entity(conn, "0000000002")
    # C: EBITDA negative -> assembled, then excluded by log().
    ids["C"] = pit_store.upsert_entity(conn, "0000000003")
    # D: everything stale (period ends 2017) -> not even an operating income.
    ids["D"] = pit_store.upsert_entity(conn, "0000000004")

    rows = []
    for key, sic in (("A", "2834"), ("B", "2834"), ("C", "3674"), ("D", "3674")):
        pit_store.add_entity_sic(conn, ids[key], sic, "2020-02-15",
                                 f"accn-{key}", "test")
    rows += [
        _fact(ids["A"], "Revenues", PERIOD, 4, 1000.0, "a1", "2020-02-15", "2020-02-18"),
        _fact(ids["A"], "OperatingIncomeLoss", PERIOD, 4, 80.0, "a1", "2020-02-15", "2020-02-18"),
        _fact(ids["A"], "DepreciationDepletionAndAmortization", PERIOD, 4, 20.0, "a1", "2020-02-15", "2020-02-18"),
        _fact(ids["A"], "Assets", PERIOD, 0, 5000.0, "a1", "2020-02-15", "2020-02-18"),
        # THE FUTURE-DATA CANARY: a restatement of revenue to 1500, knowable
        # only from 2020-08-01. At AS_OF the study must still see 1000.
        _fact(ids["A"], "Revenues", PERIOD, 4, 1500.0, "a2", "2020-07-30", "2020-08-01"),

        _fact(ids["B"], "Revenues", PERIOD, 4, 900.0, "b1", "2020-02-15", "2020-02-18"),
        _fact(ids["B"], "OperatingIncomeLoss", PERIOD, 4, 60.0, "b1", "2020-02-15", "2020-02-18"),
        # D&A only for the PRIOR fiscal year: same company, wrong period.
        _fact(ids["B"], "DepreciationDepletionAndAmortization", PRIOR, 4, 15.0, "b0", "2019-02-15", "2019-02-18"),
        _fact(ids["B"], "Assets", PERIOD, 0, 4000.0, "b1", "2020-02-15", "2020-02-18"),

        _fact(ids["C"], "Revenues", PERIOD, 4, 500.0, "c1", "2020-02-15", "2020-02-18"),
        _fact(ids["C"], "OperatingIncomeLoss", PERIOD, 4, -90.0, "c1", "2020-02-15", "2020-02-18"),
        _fact(ids["C"], "DepreciationDepletionAndAmortization", PERIOD, 4, 10.0, "c1", "2020-02-15", "2020-02-18"),
        _fact(ids["C"], "Assets", PERIOD, 0, 800.0, "c1", "2020-02-15", "2020-02-18"),

        # D last reported for FY2017: PIT-correct, and eight quarters too old.
        _fact(ids["D"], "Revenues", "2017-12-31", 4, 200.0, "d1", "2018-02-15", "2018-02-18"),
        _fact(ids["D"], "OperatingIncomeLoss", "2017-12-31", 4, 30.0, "d1", "2018-02-15", "2018-02-18"),
        _fact(ids["D"], "DepreciationDepletionAndAmortization", "2017-12-31", 4, 5.0, "d1", "2018-02-15", "2018-02-18"),
    ]
    with pit_store.transaction(conn):
        pit_store.insert_facts(conn, rows)
    return conn, ids


def test_sample_invariants(conn: sqlite3.Connection, ids: dict[str, int]) -> None:
    a = bs.firm_observation(conn, ids["A"], "2834", AS_OF)
    check("sample: a complete profitable firm is IN_SAMPLE",
          a["disposition"] == bs.IN_SAMPLE, a["disposition"])
    check("sample: EBITDA = operating income + D&A = 100",
          close(a["ebitda"], 100.0), str(a["ebitda"]))
    check("sample: FUTURE-DATA CANARY -- the 2020-08-01 restatement is invisible "
          "at a 2020-06-30 as-of (revenue is 1000, not 1500)",
          close(a["revenue"], 1000.0), str(a["revenue"]))
    check("sample: margin = 0.10", close(a["margin"], 0.10, 1e-12))
    check("sample: log variables are the logs of the levels",
          close(a["log_ebitda"], math.log(100.0), 1e-12)
          and close(a["log_revenue"], math.log(1000.0), 1e-12))
    check("sample: total assets put the firm in the base universe", a["in_base"])
    check("sample: both components shared one accession -> not mixed",
          a["mixed_accession"] is False)

    b = bs.firm_observation(conn, ids["B"], "2834", AS_OF)
    check("sample: D&A from a DIFFERENT fiscal year does not assemble an EBITDA",
          b["disposition"] == bs.NO_DNA_AT_PERIOD and b["ebitda"] is None,
          b["disposition"])

    c = bs.firm_observation(conn, ids["C"], "3674", AS_OF)
    check("sample: a negative EBITDA assembles, then log() excludes it",
          c["disposition"] == bs.EXCL_NONPOSITIVE_EBITDA
          and close(c["ebitda"], -80.0), f"{c['disposition']} {c['ebitda']}")
    check("sample: the excluded firm keeps its revenue, so size can be reported",
          close(c["revenue"], 500.0))

    d = bs.firm_observation(conn, ids["D"], "3674", AS_OF)
    check("sample: a FY2017 filer is stale at 2020-06-30, not usable",
          d["disposition"] == bs.NO_OPERATING_INCOME
          and d.get("reason") == pit_store.REASON_STALE,
          f"{d['disposition']} {d.get('reason')}")

    nos = bs.firm_observation(conn, ids["A"], None, AS_OF)
    check("sample: no SIC as-of is its own disposition, never a default sector",
          nos["disposition"] == bs.NO_SIC)


def test_cross_section_and_census(conn: sqlite3.Connection) -> None:
    cross = bs.build_cross_section(conn, AS_OF)
    check("cross-section: all four fixture peers are in the universe",
          cross["n_peers"] == 4, str(cross["n_peers"]))
    census = cross["census"]
    check("cross-section: the census partitions the universe",
          sum(census.values()) == cross["n_peers"], str(census))
    check("cross-section: exactly one firm reaches the log sample",
          census.get(bs.IN_SAMPLE) == 1, str(census))
    check("cross-section: base universe counts the three with total assets",
          cross["n_base"] == 3, str(cross["n_base"]))
    sel = bs.selection_effect(cross["records"], "sic2")
    check("selection: 2 assembled, 1 kept, 1 dropped for EBITDA <= 0",
          sel["n_assembled"] == 2 and sel["n_in_sample"] == 1
          and sel["n_dropped_nonpositive_ebitda"] == 1, str(sel["n_assembled"]))
    check("selection: the drop rate of assembled firms is 50%",
          close(sel["drop_rate_of_assembled"], 0.5, 1e-12))
    check("selection: all three denominators are reported and differ",
          close(sel["drop_rate_of_base"], 2.0 / 3.0, 1e-12)
          and close(sel["drop_rate_of_peers"], 0.75, 1e-12),
          f"base {sel['drop_rate_of_base']} peers {sel['drop_rate_of_peers']}")
    check("selection: the dropped firm is the smaller one by log revenue",
          sel["log_revenue_dropped"]["median"] < sel["log_revenue_kept"]["median"])


# --------------------------------------------------------------------------
# 3. Provenance, enforced by a SQLite authorizer
# --------------------------------------------------------------------------

#: Every table the study is permitted to read. Prices, listings and labels are
#: absent ON PURPOSE: the study's claim that its result is not survivor-only
#: rests on never touching the 2,574-listing price sample.
ALLOWED_TABLES = {"pit_entity", "pit_entity_sic", "pit_entity_exit", "pit_fact"}

FORBIDDEN_TABLES = {"pit_price_bar", "pit_listing", "pit_label", "pit_label_set",
                    "pit_feature", "pit_score", "pit_replay_run",
                    "pit_corporate_action", "pit_benchmark"}

#: sqlite3 authorizer action codes for every write verb.
_WRITE_ACTIONS = {
    1: "CREATE_INDEX", 2: "CREATE_TABLE", 3: "CREATE_TEMP_INDEX",
    4: "CREATE_TEMP_TABLE", 5: "CREATE_TEMP_TRIGGER", 6: "CREATE_TEMP_VIEW",
    7: "CREATE_TRIGGER", 8: "CREATE_VIEW", 9: "DELETE", 10: "DROP_INDEX",
    11: "DROP_TABLE", 12: "DROP_TEMP_INDEX", 13: "DROP_TEMP_TABLE",
    14: "DROP_TEMP_TRIGGER", 15: "DROP_TEMP_VIEW", 16: "DROP_TRIGGER",
    17: "DROP_VIEW", 18: "INSERT", 23: "UPDATE", 27: "ALTER_TABLE",
}
_SQLITE_READ = 20


def test_authorizer_proves_what_is_touched(path: str) -> None:
    """Run a whole cross-section under an authorizer and read the ledger back."""
    seen_tables: set[str] = set()
    writes: list[str] = []

    def authorizer(action, arg1, arg2, dbname, source):
        if action == _SQLITE_READ and arg1:
            seen_tables.add(str(arg1))
        elif action in _WRITE_ACTIONS:
            writes.append(f"{_WRITE_ACTIONS[action]} {arg1}")
        return sqlite3.SQLITE_OK

    conn = bs.open_readonly(path)
    try:
        conn.set_authorizer(authorizer)
        bs.build_cross_section(conn, AS_OF)
    finally:
        conn.set_authorizer(None)
        conn.close()

    check("provenance: the study attempted NO write of any kind",
          not writes, "; ".join(writes[:5]))
    check("provenance: every table read is on the allowlist",
          seen_tables <= ALLOWED_TABLES,
          f"unexpected: {sorted(seen_tables - ALLOWED_TABLES)}")
    check("provenance: NO price, listing, label, feature or score table was read",
          not (seen_tables & FORBIDDEN_TABLES),
          f"touched: {sorted(seen_tables & FORBIDDEN_TABLES)}")
    check("provenance: pit_fact really was read (the test is not vacuous)",
          "pit_fact" in seen_tables, str(sorted(seen_tables)))


def test_connection_is_read_only(path: str) -> None:
    conn = bs.open_readonly(path)
    refused = False
    try:
        conn.execute("INSERT INTO pit_entity (cik, created_at) VALUES ('x', 'y')")
    except sqlite3.OperationalError as exc:
        refused = "readonly" in str(exc).lower()
    except sqlite3.Error:
        refused = True
    finally:
        conn.close()
    check("read-only: SQLite itself refuses a write through open_readonly()",
          refused)


def test_source_contains_no_write_sql() -> None:
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "pit_beta_study.py"), encoding="utf-8").read()
    patterns = {
        "INSERT INTO": r"\bINSERT\s+INTO\b",
        "UPDATE ... SET": r"\bUPDATE\s+\w+\s+SET\b",
        "DELETE FROM": r"\bDELETE\s+FROM\b",
        "CREATE TABLE": r"\bCREATE\s+TABLE\b",
        "DROP": r"\bDROP\s+(TABLE|INDEX|VIEW|TRIGGER)\b",
    }
    hits = [name for name, pat in patterns.items()
            if re.search(pat, src, re.IGNORECASE)]
    check("read-only: the module's source contains no write SQL",
          not hits, ", ".join(hits))


def test_scope_label_is_not_survivor_only() -> None:
    check("scope: the study is labelled as a non-price, non-survivor sample",
          bs.SAMPLE_SCOPE == "PEER_UNIVERSE_PIT_NO_PRICES"
          and bs.SAMPLE_SCOPE != pit_store.SAMPLE_SURVIVOR_ONLY)
    plan = bs.oos_comparison_plan()
    check("plan: the out-of-sample comparison is a PLAN, not a result",
          plan["status"] == "PLAN_ONLY_NOT_EXECUTED")
    check("plan: it refuses the survivor sample for the eventual labels",
          "survivor" in plan["sample_required"]["labels"].lower())


def test_report_renders(conn_path: str) -> None:
    report = bs.run_study(conn_path, dates=(AS_OF,), rungs=("sic2",), min_n=1,
                          progress=False)
    text = bs.render_report(report)
    check("report: renders without raising and mentions the headline",
          "BETA-SCALE HINGE TEST" in text and "EFFECTIVE N" in text)
    check("report: resource block reports zero bytes written",
          report["resources"]["bytes_written_to_store"] == 0
          and report["resources"]["bytes_downloaded"] == 0)
    check("report: the WAL did not grow",
          report["resources"]["wal_bytes_after"]
          <= max(report["resources"]["wal_bytes_before"], 0) + 0,
          f"{report['resources']['wal_bytes_before']} -> "
          f"{report['resources']['wal_bytes_after']}")
    check("report: peak memory is MEASURED, not assumed",
          isinstance(report["resources"]["peak_python_alloc_bytes"], int)
          and report["resources"]["peak_python_alloc_bytes"] > 0)


def main() -> int:
    print("=" * 74)
    print("test_pit_beta_study")
    print("=" * 74)

    print("\n-- arithmetic --")
    test_ols_against_hand_computation()
    test_hc1_matches_the_full_sandwich()
    test_ols_refuses_what_it_cannot_identify()
    test_student_t()
    test_ci_excludes_one_flag()

    print("\n-- the identity the study turns on --")
    test_beta_one_makes_the_residual_margin()
    test_fit_sectors_refuses_thin_sectors()
    test_fixed_effects_recovers_a_common_slope()
    test_intraclass_correlation()
    test_describe_and_quantile()

    tmpdir = tempfile.mkdtemp(prefix="pit_beta_study_")
    path = os.path.join(tmpdir, "fixture_pit.db")
    conn, ids = _build_fixture(path)
    try:
        print("\n-- sample invariants, on a real PIT store --")
        test_sample_invariants(conn, ids)
        test_cross_section_and_census(conn)
    finally:
        conn.close()

    print("\n-- provenance and read-only posture --")
    test_authorizer_proves_what_is_touched(path)
    test_connection_is_read_only(path)
    test_source_contains_no_write_sql()
    test_scope_label_is_not_survivor_only()
    test_report_renders(path)

    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass
    try:
        os.rmdir(tmpdir)
    except OSError:
        pass

    print("\n" + "=" * 74)
    if FAILURES:
        print(f"FAILED {len(FAILURES)}: " + "; ".join(FAILURES))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
