"""THE BETA-SCALE HINGE TEST. Read-only research; owner work order #20.

WHAT THIS DECIDES, AND WHY IT IS CHEAP
--------------------------------------
A proposed feature -- "residual EBITDA productivity" -- would regress

    log(EBITDA_i) = alpha_s + beta_s * log(Revenue_i) + epsilon_i

within sector `s` and rank companies on `epsilon`. Building it means a feature
pipeline: a new pit_feature_definition, per-sector fits inside the replay, a
new normalisation, a new column in the score. That is expensive.

It is also possibly pointless, and one number says which. If beta_s == 1 then

    epsilon_i = log(EBITDA_i) - log(Revenue_i) - alpha_s
              = log(EBITDA_i / Revenue_i) - alpha_s
              = log(margin_i) - a constant within the sector

and because log is strictly increasing on the positives, the ranking of
`epsilon` inside a sector is IDENTICAL to the ranking of EBITDA margin -- a
quantity the Shaffer core already computes. The residual would then be a
renamed margin, exactly as `EBITDAGap` turned out to rank-correlate with margin
at rho = 1.000000.

Only a beta that is materially AND stably away from 1 leaves anything new in
the residual, and what it leaves is a SIZE TILT: epsilon = log(margin) +
(1 - beta) * log(Revenue) - alpha. At beta = 0.9 the residual is margin plus
0.1 of log size. Whether that is information or a small-cap bet is a separate
question this file does not answer.

So the deliverable is: beta, its confidence interval, its effective N, its
stability through time, the fraction of sectors whose CI excludes 1, and the
rank correlation between the residual and margin. Nothing is fitted, promoted,
or written to the store.

WHAT THIS IS NOT
----------------
* It is NOT the official historical Shaffer Score replay. `pit_feature`,
  `pit_score` and `pit_replay_run` are untouched and stay at zero rows.
* It is NOT an ML fit. One ordinary least squares slope per sector, in closed
  form, is a descriptive statistic about the shape of a cross-section.
* It does NOT read prices. The sample is drawn from `pit_fact` through the peer
  universe, which is survivorship-free by construction (`pit_identity.
  peer_universe_as_of`), so no result here carries the SURVIVOR_ONLY_DIAGNOSTIC
  restriction that every price-derived result in this project carries. The
  companion test asserts that this module names no price or label table, so the
  claim is enforced rather than promised.
* It is READ-ONLY on the store. The connection is opened `mode=ro` through a
  URI, so a write is refused by SQLite rather than by discipline, and the WAL
  cannot grow on this module's account.

THE SAMPLE IS THE REPLAY'S SAMPLE
---------------------------------
A convenience query over `pit_fact` would answer a question about the database.
The question asked is about the cross-section the replay would actually see, so
every number here comes through the sanctioned selectors:

    pit_identity.peer_universe_as_of   membership (CIK-keyed, dead names in)
    pit_policy.resolve                 the date-eligible tag ladder
    pit_store.latest_period_as_of      the latest knowable annual period
    pit_store.fact_as_of               a specific period, latest vintage then
    pit_policy.is_stale                the calendar-month freshness bound
    pit_peers.cohort_key               the SIC rung that names a sector

EBITDA is assembled as `pit_policy.ebitda_assembly_spec()` specifies: operating
income plus depreciation and amortisation, BOTH COMPONENTS AT THE SAME
period_end AND qtrs, each independently non-stale, and revenue pinned to that
same period so that the regression's two variables describe one fiscal year of
one company. A quarterly operating income against an annual D&A is not EBITDA,
and an annual EBITDA against a revenue from a different year is not a margin.

THE SELECTION EFFECT IS PART OF THE ANSWER
------------------------------------------
log(EBITDA) needs EBITDA > 0. Every loss-making and thin-margin company leaves
the sample, and they are not a random subset -- they are smaller, and they are
concentrated in particular sectors. A beta estimated on profitable firms is
applied, in production, to firms that are not. This module measures who is
excluded, by sector, by size and by profitability, and reports it beside the
beta rather than in a footnote.

RESOURCE POSTURE
----------------
Read-only, no downloads, no temporary tables, nothing retained. Per as-of date
the working set is one cross-section of small dicts, released before the next
date; only a four-field tuple per firm-date survives, for the effective-N
calculation. Peak allocation is MEASURED with `tracemalloc` and printed, so the
report states a number rather than an assurance.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import os
import sqlite3
import sys
import time
import tracemalloc
from typing import Any, Mapping, Optional, Sequence

import mllib
import pit_identity
import pit_peers
import pit_policy
import pit_store

# --------------------------------------------------------------------------
# What this study is, as labels a stored or printed row must carry
# --------------------------------------------------------------------------

STUDY_VERSION = "beta_scale_hinge_v1"

#: This sample is NOT the price sample. Said in a constant so a row copied out
#: of the JSON report carries the scope with it, the way a label row carries
#: `pit_store.SAMPLE_SURVIVOR_ONLY`.
SAMPLE_SCOPE = "PEER_UNIVERSE_PIT_NO_PRICES"
SAMPLE_SCOPE_NOTE = (
    "Drawn from pit_fact through pit_identity.peer_universe_as_of, which keeps "
    "dead and delisted issuers in. No pit_price_bar, pit_listing or pit_label "
    "row is read, so this result is NOT survivor-only and must not be labelled "
    "SURVIVOR_ONLY_DIAGNOSTIC -- that label belongs to price-derived results.")

#: The Shaffer production core is frozen. A limitation found here is recorded,
#: not fixed, and the fix would live in the candidate lineage.
SHAFFER_V1_KNOWN_LIMITATION = "SHAFFER_V1_KNOWN_LIMITATION"

# --------------------------------------------------------------------------
# Sample policy
# --------------------------------------------------------------------------

#: Annual facts only. qtrs >= 4 is the annual bucket in pit_policy; the store
#: holds 4 for a full year and 0 for an instantaneous balance-sheet fact.
ANNUAL_QTRS = 4
INSTANT_QTRS = 0

#: The minimum sector sample that may back a reported beta.
#:
#: WHY 30. The quantity being tested is whether a CI excludes 1, and the CI
#: half-width is t(0.975, n-2) * sigma / sqrt(Sxx). Two things go wrong below
#: 30. First the t multiplier climbs -- 2.042 at n=32, 2.145 at n=16, 2.365 at
#: n=9 -- so a thin sector's interval is wide for a reason that has nothing to
#: do with its economics. Second, and worse for THIS test, a sector with a
#: dozen members is usually a handful of large filers and a tail, so log
#: Revenue has almost no spread, Sxx collapses and the slope is decided by one
#: firm. A beta reported from such a sector would be noise wearing a number, so
#: it is REFUSED rather than reported with a caveat. Sectors below the floor
#: are still counted, and their share of the universe is reported, because a
#: refusal that hides how much was refused is its own distortion.
MIN_SECTOR_N = 30

#: A second floor on the spread of the regressor. Sxx = sum (x - xbar)^2 over
#: log Revenue; a sector whose members are all the same size cannot identify a
#: slope no matter how many of them there are.
MIN_SECTOR_SXX = 1.0

#: Sector definitions to run. The three rungs answer the same question at three
#: granularities, which is how "the beta is a sector-level fact" is separated
#: from "the beta is an artefact of how coarsely sectors were cut".
DEFAULT_RUNGS: tuple[str, ...] = (
    pit_peers.RUNG_SIC2, pit_peers.RUNG_SIC3, pit_peers.RUNG_OFFICE)

#: The as-of grid.
#:
#: The three the owner named, plus four more June month-ends and one December.
#: The extra June dates measure stability through time; 2019-12-31 measures
#: whether the beta moves WITHIN a year, when the as-of date is late enough
#: that most filers' latest annual period has rolled forward -- a different
#: kind of instability from the year-to-year kind and invisible on a June-only
#: grid.
#:
#: 2011-06-30 is deliberately ABSENT. Measured 2026-09-21: the peer universe
#: there is 1,733 issuers against 8,361 at 2013-06-28, because XBRL was still
#: phasing in and only large accelerated filers had filed. That cross-section
#: is a large-cap sample, and a beta from it would describe the phase-in
#: schedule rather than the decade.
DEFAULT_AS_OF_DATES: tuple[str, ...] = (
    "2013-06-28", "2015-06-30", "2017-06-30", "2019-06-28",
    "2019-12-31", "2021-06-30", "2023-06-30", "2024-06-28",
)

#: Two-sided confidence level for every interval reported.
CI_LEVEL = 0.95

#: Sample dispositions. Each is a different fact about why a firm is or is not
#: in the log sample, and they are never collapsed into "missing".
IN_SAMPLE = "in_sample"
EXCL_NONPOSITIVE_EBITDA = "excluded_ebitda_not_positive"
EXCL_NONPOSITIVE_REVENUE = "excluded_revenue_not_positive"
NO_OPERATING_INCOME = "no_operating_income"
NO_DNA_AT_PERIOD = "no_dna_at_ebitda_period"
NO_REVENUE_AT_PERIOD = "no_revenue_at_ebitda_period"
NO_SIC = "no_sic_as_of"

#: Dispositions where EBITDA and revenue both assembled. The denominator for
#: "what share of assembled firms does log() throw away".
ASSEMBLED = (IN_SAMPLE, EXCL_NONPOSITIVE_EBITDA, EXCL_NONPOSITIVE_REVENUE)


# ==========================================================================
# Statistics. Closed form, stdlib, and validated against a hand computation.
# ==========================================================================

def _betacf(a: float, b: float, x: float, max_iter: int = 300,
            eps: float = 3e-16) -> float:
    """Continued fraction for the incomplete beta, by the modified Lentz method.

    Support for `regularized_incomplete_beta`, which is support for the
    Student-t tail. Nothing here is novel; it is the standard recurrence,
    written out because scipy is not a dependency of this project.
    """
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    """I_x(a, b), the regularized incomplete beta function, on [0, 1]."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + b * math.log1p(-x) + a * math.log(x)) * _betacf(b, a, 1.0 - x) / b


def student_t_sf(t: float, df: float) -> float:
    """P(T > t) for Student's t with `df` degrees of freedom.

    The upper tail rather than the CDF, because every use here is a tail
    probability and computing it directly avoids cancelling two near-ones.
    """
    if df <= 0:
        return float("nan")
    if t < 0:
        return 1.0 - student_t_sf(-t, df)
    x = df / (df + t * t)
    return 0.5 * regularized_incomplete_beta(df / 2.0, 0.5, x)


def student_t_two_sided_p(t: float, df: float) -> float:
    """The two-sided p-value for a t statistic. Clamped into [0, 1]."""
    if df <= 0 or not math.isfinite(t):
        return float("nan")
    return max(0.0, min(1.0, 2.0 * student_t_sf(abs(t), df)))


def student_t_ppf(p: float, df: float) -> float:
    """The inverse CDF of Student's t, by bisection on `student_t_sf`.

    Bisection and not a rational approximation on purpose: the tail function
    above is the authority, the quantile is used a few hundred times in a run,
    and a bisection cannot disagree with its own CDF the way a fitted
    approximation can. Converges to machine precision in well under 200 steps.
    """
    if not 0.0 < p < 1.0 or df <= 0:
        return float("nan")
    lo, hi = -1.0e6, 1.0e6
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if 1.0 - student_t_sf(mid, df) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-12 * max(1.0, abs(mid)):
            break
    return 0.5 * (lo + hi)


def ols_slope_intercept(xs: Sequence[float], ys: Sequence[float],
                        ci_level: float = CI_LEVEL) -> Optional[dict[str, Any]]:
    """y = alpha + beta*x by least squares, with the inference the test needs.

    Returns None -- never a number -- when the fit is not identified: fewer
    than three points (df would be zero or negative, so sigma is undefined) or
    no spread in x (Sxx == 0, so the slope is division by zero, not infinity).
    A caller that got None has a sector it must refuse, which is the point.

    Two standard errors are reported, because they answer the same question
    under different assumptions and this cross-section violates the easier one:

      `se_beta`      classical, assuming constant error variance.
      `se_beta_hc1`  White/Huber heteroskedasticity-consistent, with the n/(n-2)
                     small-sample correction. Firm-level log EBITDA is fanned:
                     small companies scatter far more around the sector line
                     than large ones do, so the classical SE is the optimistic
                     one and HC1 is what the CI-excludes-1 count is built on.

    `t_vs_one` is the statistic for the hypothesis that MATTERS -- beta = 1, not
    beta = 0. Testing a slope against zero here would be testing whether EBITDA
    has anything to do with revenue, which nobody doubts.

    WHY NOT mllib.LinearModel. It was read first, and it does not fit. It
    standardises X before solving and maps the coefficients back by dividing by
    the column standard deviation, which is correct for a point estimate and
    throws away everything needed for inference: it keeps no residuals, no SSE,
    no degrees of freedom and no Gram inverse, so there is no standard error to
    recover and no interval to build -- and an interval is the entire
    deliverable of this study. It is also a ridge solver whose `alpha` defaults
    to 0; a shrunken slope would bias beta TOWARD the null this test is trying
    to reject. `mllib.spearman` IS reused, unchanged, for the rank correlations.
    """
    n = len(xs)
    if n != len(ys) or n < 3:
        return None
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    sxx = sum((x - x_mean) ** 2 for x in xs)
    if sxx <= 0.0:
        return None
    sxy = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    syy = sum((y - y_mean) ** 2 for y in ys)
    beta = sxy / sxx
    alpha = y_mean - beta * x_mean
    residuals = [y - (alpha + beta * x) for x, y in zip(xs, ys)]
    sse = sum(r * r for r in residuals)
    df = n - 2
    sigma2 = sse / df
    se_beta = math.sqrt(sigma2 / sxx) if sigma2 > 0 else 0.0
    # HC1: sum of squared-x-deviation-weighted squared residuals over Sxx^2.
    meat = sum(((x - x_mean) ** 2) * (r * r) for x, r in zip(xs, residuals))
    var_hc1 = (n / df) * meat / (sxx * sxx)
    se_hc1 = math.sqrt(var_hc1) if var_hc1 > 0 else 0.0
    tcrit = student_t_ppf(0.5 + ci_level / 2.0, df)
    out: dict[str, Any] = {
        "n": n, "df": df, "alpha": alpha, "beta": beta,
        "sxx": sxx, "sse": sse, "sigma": math.sqrt(sigma2) if sigma2 > 0 else 0.0,
        "r2": (1.0 - sse / syy) if syy > 0 else None,
        "se_beta": se_beta, "se_beta_hc1": se_hc1,
        "ci_level": ci_level, "t_crit": tcrit,
        "ci_lo": beta - tcrit * se_beta, "ci_hi": beta + tcrit * se_beta,
        "ci_lo_hc1": beta - tcrit * se_hc1, "ci_hi_hc1": beta + tcrit * se_hc1,
        "residuals": residuals,
    }
    out["t_vs_one"] = (beta - 1.0) / se_beta if se_beta > 0 else float("nan")
    out["t_vs_one_hc1"] = (beta - 1.0) / se_hc1 if se_hc1 > 0 else float("nan")
    out["p_vs_one"] = student_t_two_sided_p(out["t_vs_one"], df)
    out["p_vs_one_hc1"] = student_t_two_sided_p(out["t_vs_one_hc1"], df)
    out["ci_excludes_one"] = not (out["ci_lo"] <= 1.0 <= out["ci_hi"])
    out["ci_excludes_one_hc1"] = not (out["ci_lo_hc1"] <= 1.0 <= out["ci_hi_hc1"])
    return out


def fixed_effects_slope(groups: Mapping[str, Sequence[tuple[float, float]]],
                        ci_level: float = CI_LEVEL) -> Optional[dict[str, Any]]:
    """One COMMON beta with a free intercept per sector: the within estimator.

    Each sector's x and y are demeaned inside the sector, which sweeps out
    alpha_s exactly, and the pooled slope is fitted through the origin on the
    demeaned data. df = N - S - 1: one parameter per sector plus the slope.

    This is the headline number. A per-sector beta answers "is THIS sector's
    beta one"; the within estimator answers "is the beta one", using every
    sector including the thin ones -- a sector with four members still
    contributes three degrees of freedom of within-sector variation even though
    its own slope could never be reported.
    """
    sxx = 0.0
    sxy = 0.0
    pairs: list[tuple[float, float]] = []
    n_groups = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        n_groups += 1
        xm = sum(p[0] for p in members) / len(members)
        ym = sum(p[1] for p in members) / len(members)
        for x, y in members:
            dx, dy = x - xm, y - ym
            pairs.append((dx, dy))
            sxx += dx * dx
            sxy += dx * dy
    n = len(pairs)
    df = n - n_groups - 1
    if n_groups == 0 or df <= 0 or sxx <= 0.0:
        return None
    beta = sxy / sxx
    resid = [dy - beta * dx for dx, dy in pairs]
    sse = sum(r * r for r in resid)
    sigma2 = sse / df
    se = math.sqrt(sigma2 / sxx) if sigma2 > 0 else 0.0
    meat = sum((dx * dx) * (r * r) for (dx, _), r in zip(pairs, resid))
    var_hc1 = (n / df) * meat / (sxx * sxx)
    se_hc1 = math.sqrt(var_hc1) if var_hc1 > 0 else 0.0
    tcrit = student_t_ppf(0.5 + ci_level / 2.0, df)
    syy = sum(dy * dy for _, dy in pairs)
    out = {
        "n": n, "n_sectors": n_groups, "df": df, "beta": beta,
        "se_beta": se, "se_beta_hc1": se_hc1, "sxx": sxx,
        "r2_within": (1.0 - sse / syy) if syy > 0 else None,
        "ci_lo": beta - tcrit * se, "ci_hi": beta + tcrit * se,
        "ci_lo_hc1": beta - tcrit * se_hc1, "ci_hi_hc1": beta + tcrit * se_hc1,
        "ci_level": ci_level,
    }
    out["t_vs_one_hc1"] = (beta - 1.0) / se_hc1 if se_hc1 > 0 else float("nan")
    out["p_vs_one_hc1"] = student_t_two_sided_p(out["t_vs_one_hc1"], df)
    out["ci_excludes_one_hc1"] = not (out["ci_lo_hc1"] <= 1.0 <= out["ci_hi_hc1"])
    return out


def intraclass_correlation(groups: Mapping[Any, Sequence[float]]
                           ) -> Optional[dict[str, Any]]:
    """One-way random-effects ICC and the design effect it implies.

    The same decomposition already used in this project to turn 355,727
    overlapping 12-month label rows into 160.7 independent observations. Here
    the grouping variable is the ENTITY and the repeats are as-of dates: the
    same company's residual in 2015 and in 2017 is very nearly the same number,
    so pooling eight cross-sections does NOT multiply the evidence by eight.

    Returns None when there is no repetition to measure (one group, or every
    group of size one), which is an honest "not applicable" rather than a zero.
    """
    sizes = [len(v) for v in groups.values() if v]
    k = len(sizes)
    n = sum(sizes)
    if k < 2 or n <= k:
        return None
    grand = sum(sum(v) for v in groups.values() if v) / n
    ss_between = sum(len(v) * (sum(v) / len(v) - grand) ** 2
                     for v in groups.values() if v)
    ss_within = sum(sum((x - sum(v) / len(v)) ** 2 for x in v)
                    for v in groups.values() if v)
    ms_between = ss_between / (k - 1)
    ms_within = ss_within / (n - k)
    m0 = (n - sum(s * s for s in sizes) / n) / (k - 1)
    denom = ms_between + (m0 - 1.0) * ms_within
    icc = (ms_between - ms_within) / denom if denom > 0 else 0.0
    icc = max(0.0, min(1.0, icc))
    m_bar = n / k
    design_effect = 1.0 + (m_bar - 1.0) * icc
    return {
        "n_rows": n, "n_groups": k, "mean_group_size": m_bar,
        "ms_between": ms_between, "ms_within": ms_within, "m0": m0,
        "icc": icc, "design_effect": design_effect,
        "n_effective": n / design_effect if design_effect > 0 else None,
    }


def _quantile(sorted_values: Sequence[float], q: float) -> Optional[float]:
    """Linear-interpolated quantile of an ALREADY SORTED sequence."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    pos = q * (len(sorted_values) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return float(sorted_values[lo] * (1.0 - frac) + sorted_values[hi] * frac)


def describe(values: Sequence[float]) -> dict[str, Any]:
    """n / mean / p10 / median / p90 for a distribution reported in the text."""
    vals = sorted(float(v) for v in values)
    if not vals:
        return {"n": 0, "mean": None, "p10": None, "median": None, "p90": None}
    return {
        "n": len(vals), "mean": sum(vals) / len(vals),
        "p10": _quantile(vals, 0.10), "median": _quantile(vals, 0.50),
        "p90": _quantile(vals, 0.90),
    }


# ==========================================================================
# Sample construction. Every read goes through a sanctioned selector.
# ==========================================================================

def open_readonly(db_path: str = pit_store.DEFAULT_PIT_DB_PATH) -> sqlite3.Connection:
    """A connection SQLite itself will refuse to write through.

    `mode=ro` is not politeness: this study must be unable to add a page to an
    8.13 GiB file on a 96%-full volume, and unable to extend the WAL. A bug in
    this module therefore fails with an error instead of consuming disk.
    """
    if not os.path.isfile(db_path):
        raise FileNotFoundError(db_path)
    uri = "file:" + db_path.replace("\\", "/").replace("?", "%3f") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _resolve_latest_annual(conn: sqlite3.Connection, entity_id: int, concept: str,
                           as_of: str, ladder_version: Optional[str] = None
                           ) -> tuple[Optional[sqlite3.Row], str]:
    """The latest non-stale ANNUAL fact for `concept`, and why if there is none.

    Ladder order is fallback order: the first rung that yields a usable fact
    wins, and a stale rung does not stop the walk -- a company that dropped its
    preferred tag may still be tagging a later rung freshly, and conflating
    "too old" with "never tagged" would lose that.

    Returns (row, outcome) with outcome one of resolved / stale / ladder_exhausted.
    """
    saw_stale = False
    ladder = pit_policy.ladder_for(concept, ladder_version)
    for rung in pit_policy.resolve(concept, as_of, ladder_version):
        if rung.combine != pit_policy.COMBINE_SINGLE:
            # No concept used here has a composite rung; if one appears, refuse
            # rather than silently read the first tag of a required sum.
            raise ValueError(f"{concept}: composite rung {rung.key} unsupported here")
        row = pit_store.latest_period_as_of(
            conn, entity_id, rung.tag, ladder.unit, ANNUAL_QTRS, as_of)
        if row is None or row["taxonomy"] != rung.taxonomy:
            continue
        if pit_policy.is_stale(row["period_end"], as_of, ANNUAL_QTRS, concept):
            saw_stale = True
            continue
        return row, "resolved"
    return None, (pit_store.REASON_STALE if saw_stale
                  else pit_store.REASON_LADDER_EXHAUSTED)


def _resolve_at_period(conn: sqlite3.Connection, entity_id: int, concept: str,
                       period_end: str, qtrs: int, as_of: str,
                       ladder_version: Optional[str] = None
                       ) -> Optional[sqlite3.Row]:
    """The same ladder, PINNED to one reporting period.

    This is what enforces the assembly spec's period rule. EBITDA's components
    and the revenue it is divided by must describe the same fiscal year of the
    same company; taking each one's own latest period would silently build a
    margin out of two different years for any filer whose D&A lags its income
    statement by a restatement.
    """
    ladder = pit_policy.ladder_for(concept, ladder_version)
    for rung in pit_policy.resolve(concept, as_of, ladder_version):
        row = pit_store.fact_as_of(conn, entity_id, rung.tag, ladder.unit,
                                   period_end, qtrs, as_of)
        if row is None or row["taxonomy"] != rung.taxonomy:
            continue
        return row
    return None


def _resolve_instant(conn: sqlite3.Connection, entity_id: int, concept: str,
                     as_of: str, ladder_version: Optional[str] = None
                     ) -> Optional[sqlite3.Row]:
    """The latest non-stale INSTANTANEOUS fact (qtrs = 0), e.g. total assets."""
    ladder = pit_policy.ladder_for(concept, ladder_version)
    for rung in pit_policy.resolve(concept, as_of, ladder_version):
        row = pit_store.latest_period_as_of(
            conn, entity_id, rung.tag, ladder.unit, INSTANT_QTRS, as_of)
        if row is None or row["taxonomy"] != rung.taxonomy:
            continue
        if pit_policy.is_stale(row["period_end"], as_of, INSTANT_QTRS, concept):
            continue
        return row
    return None


def firm_observation(conn: sqlite3.Connection, entity_id: int, sic: Optional[str],
                     as_of: str, ladder_version: Optional[str] = None
                     ) -> dict[str, Any]:
    """One firm's (Revenue, EBITDA) for one as-of date, or the reason there is none.

    The invariant: `revenue` and `ebitda` in a returned record with disposition
    IN_SAMPLE describe THE SAME period_end of the same entity, both components
    of EBITDA were non-stale at `as_of`, and both were knowable then -- every
    read went through `fact_as_of` or `latest_period_as_of`, which order by
    `available_date <= as_of`.

    `mixed_accession` is carried, not filtered on. Operating income is an income
    statement line and D&A a cash-flow add-back; they usually share a filing,
    and when they do not that is information about the observation rather than a
    reason to drop it.
    """
    rec: dict[str, Any] = {
        "entity_id": entity_id, "as_of": as_of, "sic": sic,
        "disposition": None, "in_base": False,
        "revenue": None, "ebitda": None, "operating_income": None, "dna": None,
        "period_end": None, "revenue_tag": None, "dna_tag": None,
        "mixed_accession": None, "assets": None,
        "revenue_latest_period": None, "period_aligned": None,
    }
    if sic is None:
        rec["disposition"] = NO_SIC
        return rec

    assets_row = _resolve_instant(conn, entity_id, "total_assets", as_of,
                                  ladder_version)
    if assets_row is not None:
        rec["in_base"] = True
        rec["assets"] = float(assets_row["val"])

    rev_latest, _rev_outcome = _resolve_latest_annual(
        conn, entity_id, "revenue", as_of, ladder_version)
    if rev_latest is not None:
        rec["revenue_latest_period"] = rev_latest["period_end"]

    op_row, op_outcome = _resolve_latest_annual(
        conn, entity_id, "operating_income", as_of, ladder_version)
    if op_row is None:
        rec["disposition"] = NO_OPERATING_INCOME
        rec["reason"] = op_outcome
        return rec
    period = op_row["period_end"]
    rec["period_end"] = period
    rec["operating_income"] = float(op_row["val"])

    dna_row = _resolve_at_period(conn, entity_id, "depreciation_amortisation",
                                 period, ANNUAL_QTRS, as_of, ladder_version)
    if dna_row is None or pit_policy.is_stale(period, as_of, ANNUAL_QTRS,
                                              "depreciation_amortisation"):
        rec["disposition"] = NO_DNA_AT_PERIOD
        return rec
    rec["dna"] = float(dna_row["val"])
    rec["dna_tag"] = dna_row["tag"]
    rec["ebitda"] = rec["operating_income"] + rec["dna"]
    rec["mixed_accession"] = pit_policy.mixed_accession(
        (op_row["accn"], dna_row["accn"]))

    if rev_latest is not None and rev_latest["period_end"] == period:
        rev_row = rev_latest
    else:
        rev_row = _resolve_at_period(conn, entity_id, "revenue", period,
                                     ANNUAL_QTRS, as_of, ladder_version)
    rec["period_aligned"] = bool(
        rev_latest is not None and rev_latest["period_end"] == period)
    if rev_row is None:
        rec["disposition"] = NO_REVENUE_AT_PERIOD
        return rec
    rec["revenue"] = float(rev_row["val"])
    rec["revenue_tag"] = rev_row["tag"]

    if rec["revenue"] <= 0.0:
        rec["disposition"] = EXCL_NONPOSITIVE_REVENUE
    elif rec["ebitda"] <= 0.0:
        rec["disposition"] = EXCL_NONPOSITIVE_EBITDA
    else:
        rec["disposition"] = IN_SAMPLE
        rec["log_revenue"] = math.log(rec["revenue"])
        rec["log_ebitda"] = math.log(rec["ebitda"])
        rec["margin"] = rec["ebitda"] / rec["revenue"]
    return rec


def build_cross_section(conn: sqlite3.Connection, as_of: str,
                        ladder_version: Optional[str] = None,
                        limit: Optional[int] = None,
                        progress: bool = False) -> dict[str, Any]:
    """Every peer at `as_of`, resolved. Returns records plus the disposition census.

    The peer universe is the entry point on purpose: it keeps dead and
    delisted issuers in (rule 6), it requires no ticker, and it is the cohort
    the replay's percentiles are computed over. Starting from `pit_listing`
    would have produced a beta estimated on the 2,574 survivors.
    """
    peers = pit_identity.peer_universe_as_of(conn, as_of)
    if limit is not None:
        peers = peers[:limit]
    records: list[dict[str, Any]] = []
    census: dict[str, int] = {}
    started = time.time()
    for i, peer in enumerate(peers):
        rec = firm_observation(conn, int(peer["entity_id"]), peer["sic"], as_of,
                               ladder_version)
        census[rec["disposition"]] = census.get(rec["disposition"], 0) + 1
        records.append(rec)
        if progress and i and i % 2000 == 0:
            print(f"    ... {i}/{len(peers)} ({time.time() - started:.0f}s)",
                  file=sys.stderr)
    return {
        "as_of": as_of, "n_peers": len(peers), "records": records,
        "census": census, "n_base": sum(1 for r in records if r["in_base"]),
        "elapsed_s": time.time() - started,
    }


# ==========================================================================
# The fits
# ==========================================================================

def fit_sectors(records: Sequence[Mapping[str, Any]], rung: str,
                min_n: int = MIN_SECTOR_N, min_sxx: float = MIN_SECTOR_SXX,
                ci_level: float = CI_LEVEL) -> dict[str, Any]:
    """Per-sector betas at one SIC rung, plus the within-sector common beta.

    A sector below `min_n` or below `min_sxx` is REFUSED, not reported: its
    entry appears in `refused` with its size and the reason, so the report can
    state how much of the cross-section carries no reportable beta.

    `rho_residual_margin` is the decisive number. The residual of this
    regression is log(margin) + (1 - beta) * log(Revenue) - alpha; at beta = 1
    it is a strictly increasing function of margin and the Spearman correlation
    is exactly 1, meaning the feature is margin under another name.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        if rec.get("disposition") != IN_SAMPLE:
            continue
        key = pit_peers.cohort_key(rung, rec["sic"])
        if key is None:
            continue
        groups.setdefault(key, []).append(rec)

    sectors: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    pooled_pairs: dict[str, list[tuple[float, float]]] = {}
    residual_index: list[tuple[int, float]] = []

    for key, members in sorted(groups.items()):
        xs = [m["log_revenue"] for m in members]
        ys = [m["log_ebitda"] for m in members]
        pooled_pairs[key] = list(zip(xs, ys))
        if len(members) < min_n:
            refused.append({"sector": key, "n": len(members),
                            "reason": f"n < {min_n}"})
            continue
        fit = ols_slope_intercept(xs, ys, ci_level)
        if fit is None or fit["sxx"] < min_sxx:
            refused.append({"sector": key, "n": len(members),
                            "reason": "no identifying spread in log Revenue"
                                      if fit is not None else "not identified"})
            continue
        resid = fit.pop("residuals")
        margins = [m["margin"] for m in members]
        rho = mllib.spearman(resid, margins)
        rho_size = mllib.spearman(resid, xs)
        rho_logmargin = mllib.spearman(resid, [math.log(v) for v in margins])
        for m, r in zip(members, resid):
            residual_index.append((m["entity_id"], r))
        fit.update({
            "sector": key, "rung": rung,
            "rho_residual_margin": rho,
            "rho_residual_log_margin": rho_logmargin,
            "rho_residual_log_revenue": rho_size,
            "median_log_revenue": _quantile(sorted(xs), 0.5),
        })
        sectors.append(fit)

    common = fixed_effects_slope(pooled_pairs, ci_level)
    reported_n = sum(s["n"] for s in sectors)
    refused_n = sum(r["n"] for r in refused)
    betas = [s["beta"] for s in sectors]
    rhos = [s["rho_residual_margin"] for s in sectors
            if s["rho_residual_margin"] is not None]
    return {
        "rung": rung, "min_n": min_n, "min_sxx": min_sxx,
        "n_sectors_reported": len(sectors), "n_sectors_refused": len(refused),
        "n_firms_reported": reported_n, "n_firms_refused": refused_n,
        "share_firms_reported": (reported_n / (reported_n + refused_n)
                                 if reported_n + refused_n else None),
        "sectors": sectors, "refused": refused,
        "common_beta": common,
        "beta_distribution": describe(betas),
        "rho_distribution": describe(rhos),
        "n_ci_excludes_one": sum(1 for s in sectors if s["ci_excludes_one"]),
        "n_ci_excludes_one_hc1": sum(1 for s in sectors
                                     if s["ci_excludes_one_hc1"]),
        "frac_ci_excludes_one": (sum(1 for s in sectors if s["ci_excludes_one"])
                                 / len(sectors)) if sectors else None,
        "frac_ci_excludes_one_hc1": (
            sum(1 for s in sectors if s["ci_excludes_one_hc1"]) / len(sectors)
        ) if sectors else None,
        "n_rho_at_least_0_99": sum(1 for r in rhos if r >= 0.99),
        "residual_index": residual_index,
    }


def selection_effect(records: Sequence[Mapping[str, Any]], rung: str
                     ) -> dict[str, Any]:
    """WHO log(EBITDA) throws away: by count, by size, by profitability, by sector.

    A beta fitted on EBITDA > 0 is applied in production to every scored name,
    including the ones that were dropped here. If the excluded firms are
    systematically smaller, the fitted slope is estimated over a narrower range
    of log Revenue than it is extrapolated across, and the residual's size tilt
    at beta != 1 is largest exactly where the fit had no data.
    """
    assembled = [r for r in records if r.get("disposition") in ASSEMBLED]
    kept = [r for r in assembled if r["disposition"] == IN_SAMPLE]
    dropped_ebitda = [r for r in assembled
                      if r["disposition"] == EXCL_NONPOSITIVE_EBITDA]
    dropped_rev = [r for r in assembled
                   if r["disposition"] == EXCL_NONPOSITIVE_REVENUE]

    by_sector: dict[str, dict[str, int]] = {}
    for rec in assembled:
        key = pit_peers.cohort_key(rung, rec["sic"])
        if key is None:
            continue
        cell = by_sector.setdefault(key, {"assembled": 0, "kept": 0, "dropped": 0})
        cell["assembled"] += 1
        if rec["disposition"] == IN_SAMPLE:
            cell["kept"] += 1
        else:
            cell["dropped"] += 1
    ranked = sorted(
        ({"sector": k, **v, "drop_rate": v["dropped"] / v["assembled"]}
         for k, v in by_sector.items() if v["assembled"] >= 20),
        key=lambda d: d["drop_rate"], reverse=True)

    def _logrev(rows: Sequence[Mapping[str, Any]]) -> list[float]:
        return [math.log(r["revenue"]) for r in rows
                if r.get("revenue") and r["revenue"] > 0]

    n_base = sum(1 for r in records if r.get("in_base"))
    # THREE denominators, all reported, because "excluded 37.8%" is not one
    # number: it depends entirely on what the 100% was. Of the firms whose
    # EBITDA and revenue both assembled, only the sign test bites. Of the base
    # universe or of all peers, every assembly failure is folded in too, and
    # those are absences of tagging, not absences of profit.
    return {
        "rung": rung,
        "n_peers": len(records),
        "n_base": n_base,
        "n_assembled": len(assembled),
        "n_in_sample": len(kept),
        "n_dropped_nonpositive_ebitda": len(dropped_ebitda),
        "n_dropped_nonpositive_revenue": len(dropped_rev),
        "drop_rate_of_assembled": ((len(dropped_ebitda) + len(dropped_rev))
                                   / len(assembled)) if assembled else None,
        "drop_rate_of_base": (
            (n_base - len(kept)) / n_base) if n_base else None,
        "drop_rate_of_peers": (
            (len(records) - len(kept)) / len(records)) if records else None,
        "log_revenue_kept": describe(_logrev(kept)),
        "log_revenue_dropped": describe(_logrev(dropped_ebitda)),
        "margin_kept": describe([r["ebitda"] / r["revenue"] for r in kept]),
        "margin_dropped": describe([r["ebitda"] / r["revenue"]
                                    for r in dropped_ebitda
                                    if r.get("revenue") and r["revenue"] > 0]),
        "worst_sectors_by_drop_rate": ranked[:12],
        "best_sectors_by_drop_rate": ranked[-8:][::-1],
    }


# ==========================================================================
# The study
# ==========================================================================

def run_study(db_path: str = pit_store.DEFAULT_PIT_DB_PATH,
              dates: Sequence[str] = DEFAULT_AS_OF_DATES,
              rungs: Sequence[str] = DEFAULT_RUNGS,
              min_n: int = MIN_SECTOR_N,
              ladder_version: Optional[str] = None,
              limit: Optional[int] = None,
              progress: bool = False) -> dict[str, Any]:
    """Run every as-of date at every rung and assemble the report.

    Records are held for ONE date at a time and released; only
    (entity_id, as_of, residual) survives across dates, for the effective-N
    calculation. This is what keeps a 60,000-firm-date study inside a few tens
    of megabytes on a machine whose OS has already killed a process for memory.
    """
    tracemalloc.start()
    started = time.time()
    conn = open_readonly(db_path)
    disk_before = pit_peers.disk_status(db_path)
    per_date: list[dict[str, Any]] = []
    # rung -> entity_id -> [residual, ...] across dates.
    residual_panel: dict[str, dict[int, list[float]]] = {r: {} for r in rungs}
    try:
        for as_of in dates:
            if progress:
                print(f"  {as_of} ...", file=sys.stderr)
            cross = build_cross_section(conn, as_of, ladder_version, limit,
                                        progress)
            records = cross.pop("records")
            entry: dict[str, Any] = {
                "as_of": as_of, "n_peers": cross["n_peers"],
                "n_base": cross["n_base"], "census": cross["census"],
                "elapsed_s": cross["elapsed_s"],
                "n_in_sample": cross["census"].get(IN_SAMPLE, 0),
                "ebitda_assembly_share_of_base": (
                    sum(1 for r in records
                        if r["in_base"] and r.get("ebitda") is not None)
                    / cross["n_base"]) if cross["n_base"] else None,
                "mixed_accession_share": None,
                "period_aligned_share": None,
                "fits": {},
                "selection": {},
            }
            assembled = [r for r in records if r.get("ebitda") is not None]
            if assembled:
                entry["mixed_accession_share"] = (
                    sum(1 for r in assembled if r["mixed_accession"])
                    / len(assembled))
                entry["period_aligned_share"] = (
                    sum(1 for r in assembled if r["period_aligned"])
                    / len(assembled))
            for rung in rungs:
                fits = fit_sectors(records, rung, min_n, MIN_SECTOR_SXX)
                for entity_id, resid in fits.pop("residual_index"):
                    residual_panel[rung].setdefault(entity_id, []).append(resid)
                entry["fits"][rung] = fits
            entry["selection"] = selection_effect(records, pit_peers.RUNG_SIC2)
            per_date.append(entry)
            del records
    finally:
        conn.close()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    effective_n = {
        rung: intraclass_correlation(panel)
        for rung, panel in residual_panel.items()
    }
    return {
        "study_version": STUDY_VERSION,
        "sample_scope": SAMPLE_SCOPE,
        "sample_scope_note": SAMPLE_SCOPE_NOTE,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(
            timespec="seconds"),
        "db_path": db_path,
        "ladder_version": ladder_version or pit_policy.DEFAULT_LADDER_VERSION,
        "latency_policy_version": pit_policy.LATENCY_POLICY_VERSION,
        "peer_set_version": pit_store.PEER_SET_VERSION,
        "staleness_policy": pit_policy.staleness_policy(),
        "ebitda_assembly": pit_policy.ebitda_assembly_spec(),
        "min_sector_n": min_n, "min_sector_sxx": MIN_SECTOR_SXX,
        "ci_level": CI_LEVEL,
        "dates": list(dates), "rungs": list(rungs),
        "per_date": per_date,
        "effective_n": effective_n,
        "stability": stability_across_dates(per_date, rungs),
        "resources": {
            "mode": "read_only",
            "peak_python_alloc_bytes": peak,
            "current_python_alloc_bytes": current,
            "bytes_downloaded": 0,
            "bytes_written_to_store": 0,
            "disk_free_bytes_before": disk_before["free_bytes"],
            "disk_free_bytes_after": pit_peers.disk_status(db_path)["free_bytes"],
            "wal_bytes_before": disk_before["wal_bytes"],
            "wal_bytes_after": pit_peers.disk_status(db_path)["wal_bytes"],
            "elapsed_s": time.time() - started,
        },
    }


def stability_across_dates(per_date: Sequence[Mapping[str, Any]],
                           rungs: Sequence[str]) -> dict[str, Any]:
    """Per-sector beta through time: range, and whether the CIs ever disagree.

    "Stable" is not the same as "similar". Two betas of 0.93 and 0.97 are
    similar; if their HC1 intervals do not overlap they are still a measured
    change, and a feature whose definition moves under it is a different
    feature each year. Both are reported.
    """
    out: dict[str, Any] = {}
    for rung in rungs:
        series: dict[str, list[dict[str, Any]]] = {}
        for entry in per_date:
            fits = entry["fits"].get(rung)
            if not fits:
                continue
            for sec in fits["sectors"]:
                series.setdefault(sec["sector"], []).append({
                    "as_of": entry["as_of"], "beta": sec["beta"], "n": sec["n"],
                    "lo": sec["ci_lo_hc1"], "hi": sec["ci_hi_hc1"],
                    "rho": sec["rho_residual_margin"],
                })
        rows = []
        for key, points in sorted(series.items()):
            if len(points) < 2:
                continue
            betas = [p["beta"] for p in points]
            lo = max(p["lo"] for p in points)
            hi = min(p["hi"] for p in points)
            rows.append({
                "sector": key, "n_dates": len(points),
                "beta_min": min(betas), "beta_max": max(betas),
                "beta_range": max(betas) - min(betas),
                "beta_mean": sum(betas) / len(betas),
                "all_below_one": all(b < 1.0 for b in betas),
                "all_above_one": all(b > 1.0 for b in betas),
                "sign_flips": sum(1 for a, b in zip(betas, betas[1:])
                                  if (a < 1.0) != (b < 1.0)),
                "cis_all_overlap": lo <= hi,
                "points": points,
            })
        ranges = [r["beta_range"] for r in rows]
        out[rung] = {
            "n_sectors_with_two_or_more_dates": len(rows),
            "beta_range_distribution": describe(ranges),
            "n_consistently_below_one": sum(1 for r in rows if r["all_below_one"]),
            "n_consistently_above_one": sum(1 for r in rows if r["all_above_one"]),
            "n_crossing_one": sum(1 for r in rows
                                  if not r["all_below_one"] and not r["all_above_one"]),
            "n_cis_all_overlap": sum(1 for r in rows if r["cis_all_overlap"]),
            "sectors": rows,
        }
    return out


# ==========================================================================
# The eventual out-of-sample comparison -- a PLAN, not an execution
# ==========================================================================

def oos_comparison_plan() -> dict[str, Any]:
    """The design of the predictive test this study does NOT run.

    Recorded here so the decision is auditable: the hinge test is what says
    whether this plan is worth executing, and the plan is written before the
    result so it cannot be reshaped to fit one. Executing it is out of scope
    under the owner's standing rules -- it needs the official replay (rule 1)
    and a full-universe return label (rule 3).
    """
    return {
        "status": "PLAN_ONLY_NOT_EXECUTED",
        "gate": (
            "Do not build the feature pipeline unless BOTH hold: (a) the "
            "within-sector common beta's HC1 interval excludes 1 at every "
            "as-of date and the implied size tilt |1 - beta| is at least 0.05, "
            "and (b) the median per-sector Spearman rho between residual and "
            "EBITDA margin is at or below 0.95. A rho above 0.99 is a stop: "
            "the residual is margin, and the honest action is to record a "
            "SHAFFER_V1_KNOWN_LIMITATION about margin's role, not to add a "
            "second copy of margin to the score."),
        "hypothesis": (
            "H1: cross-sectionally, ranking on residual EBITDA productivity "
            "predicts forward excess return better than ranking on EBITDA "
            "margin. The null is that it does not, and the null is what a "
            "beta of 1 already implies."),
        "contenders": [
            "baseline_margin: rank on EBITDA / Revenue within sector",
            "candidate_residual: rank on epsilon from the sector beta fit",
            "control_size: rank on log Revenue, to prove the candidate is not "
            "a size factor wearing a residual's name",
        ],
        "sample_required": {
            "labels": "FULL_UNIVERSE forward returns, NOT the 2,574-listing "
                      "survivor sample. The candidate's whole claim is about "
                      "firms with weak profitability, which is exactly the "
                      "population that fails and leaves a survivor sample.",
            "terminal_returns": "Any unresolved exit stays OUTCOME_UNKNOWN and "
                                "is carried as censored, never as -100%, zero "
                                "or a last price carried forward.",
            "blocker": "This label set does not exist today; building it is a "
                       "separate work order and a prerequisite, not a step.",
        },
        "design": {
            "fit_window": "The sector beta is estimated ONLY on as-of dates "
                          "strictly before the evaluation date, refitted at "
                          "each rebalance, so no firm is ranked by a line "
                          "fitted through its own future.",
            "folds": "Walk-forward on ml_fold with purge >= the label horizon "
                     "and an embargo, because 12-month labels on a month-end "
                     "grid overlap by eleven months.",
            "effective_n": "Inference uses the design-effect-corrected N from "
                           "intraclass_correlation on the residual panel, not "
                           "the row count. The measured ICC in this study is "
                           "the input to that correction.",
            "metrics": "Spearman of signal vs forward excess return, decile "
                       "spread, and hit rate -- each reported per fold, with "
                       "the count of folds improved out of folds total.",
            "decision_rule": "Promotion requires the candidate to beat the "
                             "baseline on the MAJORITY of folds AND on the "
                             "pooled effective-N test, with the number of "
                             "hypotheses tested declared in pit_proposal.",
        },
        "what_would_falsify_the_feature": (
            "If candidate_residual and baseline_margin rank-correlate above "
            "0.95 out of sample, any difference in their metrics is noise and "
            "the comparison is not informative regardless of which wins."),
    }


# ==========================================================================
# Rendering
# ==========================================================================

def _fmt(value: Any, spec: str = ".4f") -> str:
    if value is None:
        return "UNKNOWN"
    if isinstance(value, float) and not math.isfinite(value):
        return "UNKNOWN"
    try:
        return format(value, spec)
    except (TypeError, ValueError):
        return str(value)


def render_report(report: Mapping[str, Any]) -> str:
    """The study as text. Every number carries its N and its method."""
    out: list[str] = []
    w = out.append
    w("=" * 78)
    w("THE BETA-SCALE HINGE TEST  --  log(EBITDA) = alpha_s + beta_s*log(Revenue)")
    w("=" * 78)
    w(f"study_version        {report['study_version']}")
    w(f"generated_at         {report['generated_at']}")
    w(f"sample_scope         {report['sample_scope']}")
    w(f"ladder_version       {report['ladder_version']}")
    w(f"latency_policy       {report['latency_policy_version']}")
    w(f"min_sector_n         {report['min_sector_n']}  "
      f"(min Sxx {report['min_sector_sxx']})")
    w(f"CI level             {report['ci_level']:.0%} (classical and HC1 robust)")
    w("")
    w(SAMPLE_SCOPE_NOTE)
    w("")

    w("-" * 78)
    w("1. THE SAMPLE, BY AS-OF DATE")
    w("-" * 78)
    w(f"{'as_of':<12}{'peers':>7}{'base':>7}{'assembled':>11}{'in sample':>11}"
      f"{'EBITDA<=0':>11}{'asm/base':>10}")
    for d in report["per_date"]:
        cen = d["census"]
        assembled = sum(cen.get(k, 0) for k in ASSEMBLED)
        w(f"{d['as_of']:<12}{d['n_peers']:>7}{d['n_base']:>7}{assembled:>11}"
          f"{cen.get(IN_SAMPLE, 0):>11}"
          f"{cen.get(EXCL_NONPOSITIVE_EBITDA, 0):>11}"
          f"{_fmt(d['ebitda_assembly_share_of_base'], '>10.1%')}")
    w("")
    w("Disposition census (why a peer is not in the log sample):")
    keys = sorted({k for d in report["per_date"] for k in d["census"]})
    w(f"{'as_of':<12}" + "".join(f"{k[:18]:>20}" for k in keys))
    for d in report["per_date"]:
        w(f"{d['as_of']:<12}" + "".join(f"{d['census'].get(k, 0):>20}"
                                        for k in keys))
    w("")

    w("-" * 78)
    w("2. THE HEADLINE BETA  --  within-sector common slope (sector fixed effects)")
    w("-" * 78)
    for rung in report["rungs"]:
        w(f"  rung = {rung}")
        w(f"    {'as_of':<12}{'beta':>9}{'HC1 CI':>22}{'N':>8}{'sectors':>9}"
          f"{'R2w':>8}{'excl 1':>8}")
        for d in report["per_date"]:
            c = d["fits"][rung]["common_beta"]
            if c is None:
                w(f"    {d['as_of']:<12}{'UNKNOWN':>9}")
                continue
            ci = f"[{c['ci_lo_hc1']:.4f}, {c['ci_hi_hc1']:.4f}]"
            w(f"    {d['as_of']:<12}{c['beta']:>9.4f}{ci:>22}{c['n']:>8}"
              f"{c['n_sectors']:>9}{_fmt(c['r2_within'], '>8.3f')}"
              f"{('YES' if c['ci_excludes_one_hc1'] else 'no'):>8}")
        w("")

    w("-" * 78)
    w("3. PER-SECTOR BETAS, AND HOW MANY EXCLUDE 1")
    w("-" * 78)
    for rung in report["rungs"]:
        w(f"  rung = {rung}")
        w(f"    {'as_of':<12}{'reported':>9}{'refused':>9}{'firms kept':>11}"
          f"{'beta p10':>10}{'beta med':>10}{'beta p90':>10}"
          f"{'CI!=1 cls':>11}{'CI!=1 HC1':>11}")
        for d in report["per_date"]:
            f = d["fits"][rung]
            bd = f["beta_distribution"]
            w(f"    {d['as_of']:<12}{f['n_sectors_reported']:>9}"
              f"{f['n_sectors_refused']:>9}"
              f"{_fmt(f['share_firms_reported'], '>11.1%')}"
              f"{_fmt(bd['p10'], '>10.3f')}{_fmt(bd['median'], '>10.3f')}"
              f"{_fmt(bd['p90'], '>10.3f')}"
              f"{_fmt(f['frac_ci_excludes_one'], '>11.1%')}"
              f"{_fmt(f['frac_ci_excludes_one_hc1'], '>11.1%')}")
        w("")

    w("-" * 78)
    w("4. THE DECISIVE NUMBER  --  Spearman(residual, EBITDA margin), per sector")
    w("-" * 78)
    w("   rho = 1.000000 exactly would mean the residual IS margin re-ranked.")
    for rung in report["rungs"]:
        w(f"  rung = {rung}")
        w(f"    {'as_of':<12}{'sectors':>9}{'rho p10':>10}{'rho med':>10}"
          f"{'rho p90':>10}{'rho>=0.99':>11}")
        for d in report["per_date"]:
            f = d["fits"][rung]
            rd = f["rho_distribution"]
            w(f"    {d['as_of']:<12}{rd['n']:>9}{_fmt(rd['p10'], '>10.4f')}"
              f"{_fmt(rd['median'], '>10.4f')}{_fmt(rd['p90'], '>10.4f')}"
              f"{f['n_rho_at_least_0_99']:>11}")
        w("")

    w("-" * 78)
    w("5. STABILITY THROUGH TIME (per sector, across as-of dates)")
    w("-" * 78)
    for rung in report["rungs"]:
        s = report["stability"].get(rung)
        if not s:
            continue
        rd = s["beta_range_distribution"]
        w(f"  rung = {rung}: {s['n_sectors_with_two_or_more_dates']} sectors "
          f"reported at 2+ dates")
        w(f"    beta range across dates: p10 {_fmt(rd['p10'], '.3f')}  "
          f"median {_fmt(rd['median'], '.3f')}  p90 {_fmt(rd['p90'], '.3f')}")
        w(f"    consistently below 1: {s['n_consistently_below_one']}   "
          f"above 1: {s['n_consistently_above_one']}   "
          f"crossing 1: {s['n_crossing_one']}")
        w(f"    sectors whose HC1 intervals ALL overlap: "
          f"{s['n_cis_all_overlap']} / {s['n_sectors_with_two_or_more_dates']}")
        w("")

    w("-" * 78)
    w("6. EFFECTIVE N  --  the same firm appears at every date")
    w("-" * 78)
    for rung, icc in report["effective_n"].items():
        if icc is None:
            w(f"  {rung}: UNKNOWN (no repeated firms to measure)")
            continue
        w(f"  {rung}: {icc['n_rows']} firm-date residuals over "
          f"{icc['n_groups']} firms (mean {icc['mean_group_size']:.2f} dates each)")
        w(f"      ICC {icc['icc']:.4f}  design effect {icc['design_effect']:.2f}  "
          f"-> {icc['n_effective']:.1f} independent observations")
    w("")

    w("-" * 78)
    w("7. THE SELECTION EFFECT  --  who log(EBITDA) throws away")
    w("-" * 78)
    w("  Three denominators, because 'excluded N% of the universe' depends "
      "entirely on what the 100% was:")
    w(f"{'as_of':<12}{'assembled':>11}{'kept':>8}{'of assembled':>14}"
      f"{'of base':>10}{'of peers':>10}{'medlogRev kept':>16}"
      f"{'medlogRev drop':>16}")
    for d in report["per_date"]:
        s = d["selection"]
        w(f"{d['as_of']:<12}{s['n_assembled']:>11}{s['n_in_sample']:>8}"
          f"{_fmt(s['drop_rate_of_assembled'], '>14.1%')}"
          f"{_fmt(s['drop_rate_of_base'], '>10.1%')}"
          f"{_fmt(s['drop_rate_of_peers'], '>10.1%')}"
          f"{_fmt(s['log_revenue_kept']['median'], '>16.2f')}"
          f"{_fmt(s['log_revenue_dropped']['median'], '>16.2f')}")
    w("")
    w("  Profitability of the two groups (EBITDA / Revenue):")
    w(f"{'as_of':<12}{'kept p10':>10}{'kept med':>10}{'kept p90':>10}"
      f"{'drop p10':>10}{'drop med':>10}{'drop p90':>10}")
    for d in report["per_date"]:
        k = d["selection"]["margin_kept"]
        x = d["selection"]["margin_dropped"]
        w(f"{d['as_of']:<12}{_fmt(k['p10'], '>10.3f')}{_fmt(k['median'], '>10.3f')}"
          f"{_fmt(k['p90'], '>10.3f')}{_fmt(x['p10'], '>10.3f')}"
          f"{_fmt(x['median'], '>10.3f')}{_fmt(x['p90'], '>10.3f')}")
    w("")
    if report["per_date"]:
        last = report["per_date"][-1]["selection"]
        w(f"At {report['per_date'][-1]['as_of']}, SIC-2 sectors with the highest "
          f"share dropped by EBITDA <= 0 (>= 20 assembled):")
        for row in last["worst_sectors_by_drop_rate"]:
            w(f"    SIC {row['sector']:<5} {row['dropped']:>5}/{row['assembled']:<5}"
              f"  {row['drop_rate']:.1%}")
        w("  and the lowest:")
        for row in last["best_sectors_by_drop_rate"]:
            w(f"    SIC {row['sector']:<5} {row['dropped']:>5}/{row['assembled']:<5}"
              f"  {row['drop_rate']:.1%}")
    w("")

    w("-" * 78)
    w("8. RESOURCES")
    w("-" * 78)
    r = report["resources"]
    w(f"  mode                     {r['mode']} (SQLite URI mode=ro)")
    w(f"  bytes downloaded         {r['bytes_downloaded']}")
    w(f"  bytes written to store   {r['bytes_written_to_store']}")
    w(f"  WAL before / after       {r['wal_bytes_before']} / {r['wal_bytes_after']}")
    w(f"  disk free before/after   {r['disk_free_bytes_before'] / 2**30:.2f} GiB"
      f" / {r['disk_free_bytes_after'] / 2**30:.2f} GiB")
    w(f"  peak python allocation   {r['peak_python_alloc_bytes'] / 2**20:.1f} MiB"
      f" (tracemalloc)")
    w(f"  elapsed                  {r['elapsed_s']:.0f}s")
    w("")

    w("-" * 78)
    w("9. THE OUT-OF-SAMPLE COMPARISON -- PLAN ONLY, NOT EXECUTED")
    w("-" * 78)
    plan = oos_comparison_plan()
    w(f"  status: {plan['status']}")
    w(f"  gate:   {plan['gate']}")
    w(f"  blocker: {plan['sample_required']['blocker']}")
    w("")
    return "\n".join(out)


# ==========================================================================
# CLI
# ==========================================================================

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="The beta-scale hinge test. READ-ONLY research.")
    parser.add_argument("--db", default=pit_store.DEFAULT_PIT_DB_PATH)
    parser.add_argument("--dates", nargs="*", default=list(DEFAULT_AS_OF_DATES))
    parser.add_argument("--rungs", nargs="*", default=list(DEFAULT_RUNGS))
    parser.add_argument("--min-n", type=int, default=MIN_SECTOR_N)
    parser.add_argument("--ladder-version", default=None)
    parser.add_argument("--limit", type=int, default=None,
                        help="cap peers per date; for a smoke run only")
    parser.add_argument("--json", default=None, help="also write the raw report")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    report = run_study(args.db, args.dates, args.rungs, args.min_n,
                       args.ladder_version, args.limit, progress=not args.quiet)
    text = render_report(report)
    print(text)
    if args.json:
        # `run_study` already popped each fit's residual index -- one float
        # per firm-date, and nothing a reader of the report would use.
        slim = json.loads(json.dumps(report, default=str))
        slim["oos_comparison_plan"] = oos_comparison_plan()
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(slim, handle, indent=2, sort_keys=True)
        print(f"[wrote {args.json}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
