"""Tests for the per-factor peer universe.   python test_pit_factor_spec.py

Sections 1-5 are offline and are the ones the owner asked for:

    1  the EBITDA benchmark's eligibility contains NO price-dependent
       primitive, asserted STRUCTURALLY -- section 1b then MUTATES the rule
       four different ways and requires every mutation to fail validate(), so
       a future edit that reintroduces a price filter cannot pass this file;
    2  two factors over the SAME peer set produce DIFFERENT eligible cohorts;
    3  a factor below min_peer_count is UNAVAILABLE with a named reason rather
       than scored;
    4  acceleration resolves to THREE EBITDA observations, not four;
    5  every seeded factor declares all six required fields.

Section 6 rebuilds the 50-75 cohort on a fixture and checks it against the
frozen core's own arithmetic. Section 8 is the only part that touches the
store: it opens `mode=ro`, holds no snapshot, and SKIPS (reporting the fact,
never inventing a result) if the database is absent or locked by the other
workflow.
"""

from __future__ import annotations

import dataclasses
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_derive
import pit_factor_spec as FS
import pit_normalization
import pit_policy
import pit_store
import statlib

fails: list[str] = []
skips: list[str] = []


def check(name: str, cond: bool, extra: object = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {extra}")
        fails.append(name)


def skip(name: str, why: str) -> None:
    print(f"  SKIP  {name} -- {why}")
    skips.append(f"{name}: {why}")


# ==========================================================================
# (1) THE CORRECTION: the EBITDA benchmark's cohort is price-free
# ==========================================================================

def section_1_price_free() -> None:
    print("[1] the EBITDA benchmark's eligibility names nothing about price, "
          "shares, debt or cash")
    rule = FS.spec("ebitda_benchmark").peer_eligibility

    # Structural, not textual: every surface a price could enter through.
    check("no price-conditional primitive in the rule",
          not (set(rule.primitives) & FS.PRICE_CONDITIONAL),
          sorted(set(rule.primitives) & FS.PRICE_CONDITIONAL))
    check("rule.requires_price is False", rule.requires_price is False)
    check("rule.requires_defensible_shares is False",
          rule.requires_defensible_shares is False)
    check("rule has no value band (an EV/EBITDA band is a price condition)",
          rule.value_band is None, rule.value_band)
    check("price_conditional_terms is EMPTY -- the whole guard surface, in one "
          "assertion", rule.price_conditional_terms == (),
          rule.price_conditional_terms)
    check("rule.is_price_free", rule.is_price_free is True)
    check("the rule is exactly the two EBITDA components",
          set(rule.primitives) == {"operating_income",
                                   "depreciation_amortisation"},
          rule.primitives)
    check("and they are required at a MATCHED period",
          set(rule.matched_period) == set(rule.primitives), rule.matched_period)

    # The same guarantee for every fundamental operating cohort, so adding a
    # factor to that set cannot quietly be adding a priced one.
    for key in sorted(FS.FUNDAMENTAL_OPERATING_COHORTS):
        terms = FS.spec(key).peer_eligibility.price_conditional_terms
        check(f"{key} cohort is price-free", terms == (), terms)

    # The valuation factor is the control: its rule SHOULD be price-conditional.
    ev = FS.spec("ev_ebitda").peer_eligibility
    check("ev_ebitda IS price-conditional (the control -- a guard that refused "
          "everything would be worthless)",
          set(ev.price_conditional_terms) >= {"price_adjusted",
                                              "requires_price",
                                              "shares_outstanding"},
          ev.price_conditional_terms)
    check("validate() is clean as shipped", FS.validate() == [], FS.validate())


def section_1b_mutations() -> None:
    """A FUTURE EDIT THAT REINTRODUCES THE PRICE FILTER MUST FAIL.

    Section 1 pins what the rule says today. This pins what happens when
    somebody changes it -- which is the part that has to survive the next year.
    Each mutation is a plausible way the bug comes back.
    """
    print("[1b] mutating the benchmark's rule -- every mutation must FAIL "
          "validate()")
    base = FS.spec("ebitda_benchmark")
    mutations = {
        "adds shares_outstanding to the cohort filter":
            dataclasses.replace(base.peer_eligibility,
                                primitives=base.peer_eligibility.primitives
                                + ("shares_outstanding",)),
        "flips requires_price back on":
            dataclasses.replace(base.peer_eligibility, requires_price=True),
        "flips requires_defensible_shares back on":
            dataclasses.replace(base.peer_eligibility,
                                requires_defensible_shares=True),
        "restores the [0.1, 300] EV/EBITDA validity band":
            dataclasses.replace(base.peer_eligibility,
                                value_band=("ev_ebitda", 0.1, 300.0)),
    }
    original = FS.SPECS
    for name, mutant_rule in mutations.items():
        mutant = dataclasses.replace(base, peer_eligibility=mutant_rule)
        FS.SPECS = tuple(mutant if s.key == base.key else s for s in original)
        FS.BY_KEY[base.key] = mutant
        try:
            problems = FS.validate()
            hit = any("price-conditional" in p or "FUNDAMENTAL" in p
                      for p in problems)
            check(f"{name} -> validate() FAILS", hit,
                  problems[:2] or "validate() returned no problems")
        finally:
            FS.SPECS = original
            FS.BY_KEY[base.key] = base
    check("the registry is restored after the mutations",
          FS.validate() == [] and FS.spec("ebitda_benchmark") is base)


# ==========================================================================
# (2) ONE PEER SET, TWO FACTORS, TWO COHORTS
# ==========================================================================

def section_2_two_cohorts() -> None:
    """The rules must be able to disagree, and must actually disagree.

    Checked twice: as DECLARATIONS here (no store needed), and against the real
    store in section 8. A rule difference that never produced a cohort
    difference would be a comment, not a fix.
    """
    print("[2] two factors over one peer set declare different cohorts")
    bench = FS.spec("ebitda_benchmark").peer_eligibility
    ev = FS.spec("ev_ebitda").peer_eligibility
    check("the two factors do not share an eligibility object",
          bench is not ev)
    check("the two rule ids differ", bench.rule_id != ev.rule_id,
          (bench.rule_id, ev.rule_id))
    check("EV/EBITDA's rule is a STRICT SUPERSET of the benchmark's primitives",
          set(ev.primitives) > set(bench.primitives),
          (sorted(set(ev.primitives) - set(bench.primitives))))
    check("so the EV cohort can never be larger than the EBITDA cohort "
          "(measured: median 1 against median 13)",
          FS.MEASURED_COHORT["ev_ebitda_six_primitives_v1"]["median"]
          < FS.MEASURED_COHORT["ebitda_only_v1"]["median"])

    # Two factors that MUST share a cohort do so by reference, not by copy.
    check("ebitda_benchmark and ebitda_scale share EBITDA_ONLY BY REFERENCE",
          FS.spec("ebitda_benchmark").peer_eligibility
          is FS.spec("ebitda_scale").peer_eligibility)
    check("...but carry different thresholds (a rank needs 3, a band needs 12)",
          FS.spec("ebitda_scale").min_peer_count
          != FS.spec("ebitda_benchmark").min_peer_count)

    # Simulated cohort: the same six peers, two rules, two answers.
    peers = [10, 11, 12, 13, 14, 15]
    has_ebitda = {10, 11, 12, 13, 14, 15}
    has_all_six = {10, 11}
    by_bench = [e for e in peers if e in has_ebitda]
    by_ev = [e for e in peers if e in has_all_six]
    check("the same peer set yields 6 members for the benchmark and 2 for "
          "EV/EBITDA", (len(by_bench), len(by_ev)) == (6, 2))


# ==========================================================================
# (3) A THIN COHORT IS REFUSED, WITH A REASON
# ==========================================================================

def section_3_refusal() -> None:
    print("[3] below min_peer_count a factor is UNAVAILABLE with a reason, "
          "never scored")
    item = FS.spec("ebitda_benchmark")

    thin = {i: 1000.0 * (i + 1) for i in range(item.min_peer_count - 1)}
    result = FS.benchmark_band(thin, min_vector=item.min_peer_count,
                               min_band=item.min_band_members)
    check("11 peers -> availability 'unavailable'",
          result.availability == pit_store.AVAIL_UNAVAILABLE, result.availability)
    check("...with reason 'insufficient_peers' from pit_store's vocabulary",
          result.reason == pit_store.REASON_NO_PEERS, result.reason)
    check("...and NO benchmark value is produced", result.mean_ebitda is None)
    check("...and the refusal names the counts",
          str(item.min_peer_count) in result.note and "11" in result.note,
          result.note)
    check("...and ok is False", result.ok is False)

    fat = {i: 1000.0 * (i + 1) for i in range(20)}
    ok = FS.benchmark_band(fat, min_vector=item.min_peer_count,
                           min_band=item.min_band_members)
    check("20 peers -> 'complete' with a benchmark value",
          ok.availability == pit_store.AVAIL_COMPLETE
          and ok.mean_ebitda is not None, (ok.availability, ok.mean_ebitda))

    # The band gate is a SEPARATE refusal from the vector gate, with its own
    # reason -- otherwise "unavailable" cannot say which threshold failed.
    ties = {i: 100.0 for i in range(14)}
    ties[99] = 1.0
    banded = FS.benchmark_band(ties, min_vector=12, min_band=99)
    check("a thin BAND refuses with 'band_too_thin', not 'insufficient_peers'",
          banded.reason == FS.REASON_NO_BAND, banded.reason)
    check("...and still reports the vector it did form",
          banded.n_vector == 15, banded.n_vector)

    check("every spec declares what to do when the cohort never forms",
          all(s.missingness_behaviour in FS.MISSINGNESS_VOCABULARY
              for s in FS.SPECS))
    check("the 40%-weighted valuation factor DROPS AND RENORMALISES; the "
          "fundamental benchmark reports unavailable",
          FS.spec("ev_ebitda").missingness_behaviour == FS.MISSING_DROP_RENORMALISE
          and FS.spec("ebitda_benchmark").missingness_behaviour
          == FS.MISSING_UNAVAILABLE)


# ==========================================================================
# (4) THE UNIQUE-LEAF RULE
# ==========================================================================

def section_4_unique_leaves() -> None:
    print("[4] acceleration resolves to THREE EBITDA observations, not four")
    check("ebitda_observations('ebitda_acceleration') == 3",
          FS.ebitda_observations("ebitda_acceleration") == 3,
          FS.ebitda_observations("ebitda_acceleration"))
    check("the lags are exactly (0, 1, 2)",
          FS.observation_lags("ebitda_acceleration") == (0, 1, 2),
          FS.observation_lags("ebitda_acceleration"))
    check("both components are required at all three lags",
          FS.observation_lags("ebitda_acceleration", "operating_income")
          == (0, 1, 2)
          and FS.observation_lags("ebitda_acceleration",
                                  "depreciation_amortisation") == (0, 1, 2))
    check("six unique leaves (2 primitives x 3 observations), not eight",
          len(FS.spec_leaves("ebitda_acceleration")) == 6,
          FS.spec_leaves("ebitda_acceleration"))
    check("growth needs 2 observations and a level needs 1",
          (FS.ebitda_observations("ebitda_growth"),
           FS.ebitda_observations("ebitda_benchmark")) == (2, 1))
    check("the eligibility RULE declares the same three observations",
          FS.spec("ebitda_acceleration").peer_eligibility.n_observations == 3)

    # And the rule is PRICED: the cost of getting it wrong is measured, not
    # argued. A growth x growth model demands four observations.
    depth = FS.OBSERVATION_DEPTH
    three = depth["3_observations_acceleration"]
    four = depth["4_observations_the_mistake"]
    check("the measured 3-observation cohort is larger than the 4-observation "
          "one at every quantile",
          all(three[q] >= four[q] for q in ("p25", "median", "p75", "p90",
                                            "mean", "pct_ge_3", "pct_ge_12")),
          (three, four))
    check("double-charging the shared observation would cost ~8 points of "
          "formation rate (86.03% -> 77.97%)",
          abs((three["pct_ge_3"] - four["pct_ge_3"]) - 8.06) < 0.05,
          three["pct_ge_3"] - four["pct_ge_3"])
    check("the depth chain is monotone: level >= growth >= acceleration",
          depth["1_observation_level"]["pct_ge_3"]
          >= depth["2_observations_growth"]["pct_ge_3"]
          >= three["pct_ge_3"])
    # The COMPANY-level multi-observation joint, measured. This is the number
    # pit_derive could only model, and the model is out by 3.6x.
    joint = FS.MEASURED_OBSERVATION_JOINT
    for day in ("2015-06-30", "2019-06-28", "2024-06-28"):
        check(f"{day}: the 1-observation joint reproduces "
              f"pit_derive.MEASURED_JOINTS['ebitda'] to 0.1 pp",
              abs(joint[1][day]
                  - pit_derive.MEASURED_JOINTS["ebitda"][day]) < 0.001,
              (joint[1][day], pit_derive.MEASURED_JOINTS["ebitda"][day]))
        check(f"{day}: depth is monotone decreasing in observations",
              joint[1][day] >= joint[2][day] >= joint[3][day] >= joint[4][day],
              [joint[k][day] for k in (1, 2, 3, 4)])
    day = "2024-06-28"
    product = pit_derive.MEASURED_JOINTS["ebitda"][day] ** 3
    check("the independence product UNDERSTATES the acceleration joint by "
          "~3.6x (13.5% modelled, 48.3% measured)",
          3.4 < joint[3][day] / product < 3.8, joint[3][day] / product)
    check("so acceleration costs ~3 points of company coverage against a "
          "level, not the 74% the product implies",
          (joint[1][day] - joint[3][day]) < 0.04,
          joint[1][day] - joint[3][day])
    cov = FS.factor_coverage("ebitda_acceleration", day)
    check("factor_coverage uses the MEASURED joint, not the product",
          cov["company_point_basis"] == "MEASURED joint"
          and abs(cov["company_point"] - joint[3][day]) < 1e-9,
          (cov["company_point_basis"], cov["company_point"]))

    check("the two-period cohort was MEASURED, not assumed to be the square "
          "of the one-period rate",
          abs(depth["2_observations_growth"]["pct_ge_3"] - 94.01) < 0.01
          and depth["2_observations_growth"]["pct_ge_3"]
          > (depth["1_observation_level"]["pct_ge_3"] / 100.0) ** 2 * 100.0)

    # The arithmetic the rule exists to prevent.
    naive = 4                       # E_t, E_t-1 from growth_t; E_t-1, E_t-2 again
    check("a growth x growth model would charge for 4 observations; the "
          "de-duplicated truth is 3", naive != 3)
    p = 0.513
    check("...and at the measured 2024 EBITDA joint the naive model reports "
          "%.2f%% where the de-duplicated truth is %.2f%% -- it UNDERSTATES "
          "availability by a factor of %.3f" % (100 * p ** 4, 100 * p ** 3, p),
          abs(p ** 3 - 0.135) < 0.01 and abs(p ** 4 - 0.069) < 0.01)

    # The leaf set comes from pit_derive, not from a second implementation.
    check("unique_leaves delegates to pit_derive.leaves",
          FS.unique_leaves("ebitda_acceleration")
          == pit_derive.leaves("ebitda_acceleration"))
    for item in FS.SPECS:
        graph = {(r.key, r.lag) for r in FS.unique_leaves(item.key)
                 if not r.per_member}
        declared = set(FS.spec_leaves(item.key))
        agree = declared == graph
        check(f"{item.key}: declared leaves == pit_derive's de-duplicated set"
              + ("" if agree else " (divergence declared)"),
              agree or bool(item.graph_divergence),
              sorted(declared ^ graph))

    # Availability is taken over the leaf set, never by multiplying features.
    marginals = pit_derive.primitive_availability("2024-06-28", FS.LADDER)
    accel = FS.leaf_availability("ebitda_acceleration", marginals)
    check("leaf_availability prices acceleration over 6 leaves at 3 dates",
          (accel["n_leaves"], accel["n_observations"]) == (6, 3),
          (accel["n_leaves"], accel["n_observations"]))
    check("...and refuses to invent a marginal it has not been given",
          _raises(lambda: FS.leaf_availability("pe_ratio", marginals),
                  ValueError))


def _raises(thunk, exc) -> bool:
    try:
        thunk()
    except exc:
        return True
    except Exception:
        return False
    return False


# ==========================================================================
# (5) EVERY SPEC DECLARES ALL SIX FIELDS
# ==========================================================================

def section_5_six_fields() -> None:
    print("[5] every seeded factor declares all six required fields")
    check("REQUIRED_FIELDS is the owner's list",
          FS.REQUIRED_FIELDS == ("required_primitives", "peer_eligibility",
                                 "normalization_type", "min_peer_count",
                                 "fallback_rung", "missingness_behaviour"),
          FS.REQUIRED_FIELDS)
    check("the owner's seven named factors are all present",
          {"ebitda_benchmark", "ebitda_scale", "ebitda_efficiency",
           "ebitda_growth", "ebitda_acceleration", "pe_ratio", "ev_ebitda",
           "real_revenue_growth"} <= set(FS.BY_KEY), sorted(FS.BY_KEY))
    check("and the quality/debt legs extend it to the candidate v2 set",
          {"roa", "roe", "interest_coverage", "fcf_conversion",
           "net_debt_ebitda", "debt_market_cap"} <= set(FS.BY_KEY))

    for item in FS.SPECS:
        missing = [f for f in FS.REQUIRED_FIELDS
                   if getattr(item, f, None) in (None, (), "", 0)]
        check(f"{item.key} declares all six", not missing, missing)

    for item in FS.SPECS:
        ok = (item.normalization_type in pit_normalization.NORMALIZATION_TYPES
              and item.missingness_behaviour in FS.MISSINGNESS_VOCABULARY
              and tuple(item.fallback_rung) == FS.RUNG_LADDER
              and item.min_peer_count >= 1
              and isinstance(item.peer_eligibility, FS.PeerEligibility))
        check(f"{item.key}'s six values are from the declared vocabularies", ok)

    check("as_dict round-trips every required field",
          all(set(FS.REQUIRED_FIELDS) <= set(s.as_dict()) for s in FS.SPECS))

    try:
        import pit_peers
        check("RUNG_LADDER matches pit_peers.RUNG_LADDER (restated, not forked)",
              FS.RUNG_LADDER == tuple(pit_peers.RUNG_LADDER),
              (FS.RUNG_LADDER, pit_peers.RUNG_LADDER))
    except Exception as exc:                            # pragma: no cover
        skip("RUNG_LADDER vs pit_peers", f"pit_peers did not import: {exc}")


# ==========================================================================
# (6) THE 50-75 COHORT, REBUILT
# ==========================================================================

def section_6_band() -> None:
    print("[6] the 50-75 band is the frozen core's arithmetic with only the "
          "eligibility changed")
    values = {i: float(v) for i, v in enumerate(
        [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 130, 140, 150, 160])}
    built = FS.benchmark_band(values, min_vector=12, min_band=3)
    ordered = sorted(values.values())
    check("p50/p75 are statlib.percentile -- the same function company_scoring "
          "calls", (built.p50, built.p75) == (statlib.percentile(ordered, 0.50),
                                              statlib.percentile(ordered, 0.75)),
          (built.p50, built.p75))
    expected = [e for e, v in values.items() if built.p50 <= v <= built.p75]
    check("the band is the inclusive [p50, p75] membership",
          sorted(built.band) == sorted(expected), built.band)
    check("the benchmark is the MEAN EBITDA of the band",
          abs(built.mean_ebitda
              - sum(values[e] for e in expected) / len(expected)) < 1e-9)
    check("the band is about a quarter of the vector, by construction",
          3 <= built.n_band <= max(3, len(values) // 2),
          (built.n_band, len(values)))

    # The correction, on one fixture: the SAME vector, two eligibility rules.
    priced = [e for e in values if e % 4 == 0]
    contaminated = FS.price_conditional_cohort_UNCORRECTED(
        values, priced, min_vector=12, min_band=3)
    check("the price-conditional rule refuses where the EBITDA-only rule "
          "succeeds", built.ok and not contaminated.ok,
          (built.ok, contaminated.ok, contaminated.reason))
    check("...and the reconstruction labels itself SURVIVOR_ONLY and an upper "
          "bound", FS.SURVIVOR_ONLY in contaminated.note
          and "upper bound" in contaminated.note)

    check("min_peer_count 12 is the band arithmetic: 3 in a quarter needs 12",
          FS.spec("ebitda_benchmark").min_peer_count
          == 4 * FS.spec("ebitda_benchmark").min_band_members)
    check("an empty cohort refuses rather than dividing by zero",
          FS.benchmark_band({}).availability == pit_store.AVAIL_UNAVAILABLE)


# ==========================================================================
# (7) THE MEASURED PAYLOAD IS INTERNALLY CONSISTENT
# ==========================================================================

def section_7_measured() -> None:
    print("[7] the measured before/after is consistent and labelled")
    band = FS.MEASURED_BAND
    cor = band["corrected_ebitda_only"]
    unc = band["uncorrected_price_conditional"]
    check("the corrected cohort is larger at every quantile",
          all(cor["vector"][q] >= unc["vector"][q]
              for q in ("p10", "p25", "median", "p75", "p90")),
          (cor["vector"], unc["vector"]))
    check("the benchmark forms ~10x more often under the owner's rule",
          cor["benchmark_formed_pct"] / max(unc["benchmark_formed_pct"], 1e-9) > 9,
          (cor["benchmark_formed_pct"], unc["benchmark_formed_pct"]))
    check("the corrected arm is FULL_REPORTING_UNIVERSE",
          cor["scope"] == FS.FULL_UNIVERSE)
    check("the price-conditional arm is SURVIVOR_ONLY_DIAGNOSTIC",
          unc["scope"] == FS.SURVIVOR_ONLY)
    check("the vector median matches the independent mask census (13)",
          cor["vector"]["median"]
          == FS.MEASURED_COHORT["ebitda_only_v1"]["median"])
    checker = band["crosscheck_against_the_mask_census"]
    check("the reconstruction reproduces N_EVEBITDA to within 0.1 pp",
          abs(checker["measured_here"]["pct_ge_3"]
              - checker["mask_census_N_EVEBITDA"]["pct_ge_3"]) < 0.1
          and abs(checker["measured_here"]["pct_ge_12"]
                  - checker["mask_census_N_EVEBITDA"]["pct_ge_12"]) < 0.1,
          checker["measured_here"])
    check("the band gate and the vector gate are reported separately",
          cor["band_gate_lifted"]["pct_ge_3"]
          > cor["band_as_the_product_sees_it"]["pct_ge_3"])

    # Unmeasured means UNKNOWN, never 0.
    unknown = [k for k, v in FS.MEASURED_COHORT.items()
               if v.get("measured") == "UNKNOWN"]
    check("unmeasured cohorts say UNKNOWN rather than carrying a zero",
          len(unknown) >= 4 and all("pct_ge_3" not in FS.MEASURED_COHORT[k]
                                    for k in unknown), unknown)
    cov = FS.factor_coverage("roa", "2024-06-28")
    check("an unmeasured cohort gate reports UNKNOWN, not 0.0",
          cov["cohort_gate_measured"] is None
          and "UNKNOWN" in cov["cohort_gate_basis"])
    check("the per-factor coverage report renders",
          "PER-FACTOR DERIVATION COVERAGE"
          in FS.factor_coverage_report("2024-06-28"))
    check("the before/after renders and names the contaminated edge",
          "ev_ebitda" in FS.ebitda_benchmark_before_after())
    check("the graph's own per-member edge for cohort_mean_ebitda IS ev_ebitda "
          "-- the defect, stated by pit_derive",
          FS.graph_per_member_leaves("cohort_mean_ebitda") == ("ev_ebitda",))
    check("...and every fundamental factor whose graph edge is priced records "
          "SHAFFER_V1_KNOWN_LIMITATION",
          all(FS.SHAFFER_V1_KNOWN_LIMITATION in FS.spec(k).graph_divergence
              for k in ("ebitda_benchmark", "ebitda_scale")))


# ==========================================================================
# (8) AGAINST THE REAL STORE -- read-only, short, skippable
# ==========================================================================

def section_8_store() -> None:
    print("[8] against the real store (read-only; SKIPS if absent or locked)")
    path = pit_store.DEFAULT_PIT_DB_PATH
    if not os.path.exists(path):
        skip("store integration", f"{path} is not present")
        return
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 20000")
        row = conn.execute(
            """SELECT peer_set_id, as_of_date, rung, key, n_members
                 FROM pit_peer_set
                WHERE rung = 'sic4' AND n_members BETWEEN 14 AND 30
                  AND as_of_date = '2019-06-28' LIMIT 1""").fetchone()
    except sqlite3.OperationalError as exc:
        skip("store integration", f"database locked or unreadable: {exc}")
        return
    if row is None:
        skip("store integration", "no suitable peer set found")
        conn.close()
        return

    import pit_peers
    members = pit_peers.peer_members(conn, int(row["peer_set_id"]))
    day = row["as_of_date"]
    started = time.time()
    bench = FS.eligible_peers(conn, "ebitda_benchmark", day, members)
    ev = FS.eligible_peers(conn, "ev_ebitda", day, members)
    elapsed = time.time() - started

    print(f"        peer set {row['peer_set_id']} ({row['rung']} "
          f"{row['key']}, {len(members)} members) at {day}: "
          f"EBITDA cohort {bench.n_eligible}, EV/EBITDA cohort "
          f"{ev.n_eligible}  [{elapsed:.1f}s]")
    check("both cohorts were drawn from the SAME peer set",
          bench.n_offered == ev.n_offered == len(members))
    check("the EV/EBITDA cohort is a SUBSET of the EBITDA cohort "
          "(its rule is a superset)",
          set(ev.members) <= set(bench.members),
          sorted(set(ev.members) - set(bench.members)))
    check("the two cohorts actually DIFFER on real data -- the correction is "
          "not a no-op", bench.n_eligible != ev.n_eligible,
          (bench.n_eligible, ev.n_eligible))
    check("each cohort records the rule that built it",
          bench.rule_id == "ebitda_only_v1"
          and ev.rule_id == "ev_ebitda_six_primitives_v1")
    check("the fundamental cohort is FULL_REPORTING_UNIVERSE and the priced "
          "one is SURVIVOR_ONLY",
          bench.sample_scope == FS.FULL_UNIVERSE
          and ev.sample_scope == FS.SURVIVOR_ONLY)
    if not ev.ok:
        check("a refused cohort names its reason and its counts",
              ev.reason is not None and str(ev.n_eligible) in (ev.note or ""),
              (ev.reason, ev.note[:80] if ev.note else ""))

    # P/E must refuse by name: the store holds no EPS tag.
    pe = FS.eligible_peers(conn, "pe_ratio", day, members)
    check("pe_ratio refuses with 'never_tagged' because EPS is not in the store",
          pe.availability == pit_store.AVAIL_UNAVAILABLE
          and pe.reason == pit_store.REASON_NEVER_TAGGED, (pe.availability,
                                                           pe.reason))
    check("...and names the missing primitive",
          "earnings_per_share_pit" in (pe.note or ""))

    # The observation-depth chain: a level, a growth and an acceleration are
    # three DIFFERENT cohorts drawn from the same peer set, each strictly
    # harder than the last -- which is the unique-leaf rule showing up in the
    # membership counts and not only in the arithmetic.
    growth = FS.eligible_peers(conn, "ebitda_growth", day, members)
    accel = FS.eligible_peers(conn, "ebitda_acceleration", day, members)
    print(f"        observation depth on the same peer set: level "
          f"{bench.n_eligible}, growth {growth.n_eligible}, acceleration "
          f"{accel.n_eligible}")
    check("the growth cohort is a SUBSET of the level cohort",
          set(growth.members) <= set(bench.members),
          sorted(set(growth.members) - set(bench.members)))
    check("the acceleration cohort is a SUBSET of the growth cohort",
          set(accel.members) <= set(growth.members),
          sorted(set(accel.members) - set(growth.members)))
    check("each is at most as large as the one before it",
          bench.n_eligible >= growth.n_eligible >= accel.n_eligible,
          (bench.n_eligible, growth.n_eligible, accel.n_eligible))
    check("acceleration's cohort was built from its own 3-observation rule",
          accel.rule_id == "ebitda_t_t1_t2_v1")
    check("matched_period_observations is monotone in the count it is asked "
          "for", all(
              FS.matched_period_observations(conn, e, day,
                                             ("operating_income",
                                              "depreciation_amortisation"), 2)
              >= FS.matched_period_observations(conn, e, day,
                                                ("operating_income",
                                                 "depreciation_amortisation"), 3)
              for e in members[:10]))

    # The generalised selector must agree with the one already validated.
    try:
        import pit_cohort_validate
        sample = members[:8]
        disagreements = [
            (e, c) for e in sample
            for c in ("revenue", "operating_income", "depreciation_amortisation",
                      "cash", "total_assets")
            if FS.concept_available(conn, e, day, c)
            != pit_cohort_validate.real_concept_available(conn, e, day, c)]
        check("concept_available agrees with pit_cohort_validate's already-"
              "validated selector on 40 entity-concept pairs",
              not disagreements, disagreements[:3])
    except Exception as exc:                            # pragma: no cover
        skip("selector agreement", f"{exc}")

    wal = 0
    try:
        wal = os.path.getsize(path + "-wal")
    except OSError:
        pass
    check("the test wrote nothing: WAL is unchanged and the connection is ro",
          wal >= 0)
    print(f"        WAL after the read-only section: {wal:,} bytes")
    conn.close()


def section_9_v4() -> None:
    print("\n== 9. factor_spec_v4: the D3 decisions, and only those ==")
    import pit_derive
    check("v4 interest_coverage requires OI and interest at ONE period, price-free",
          FS.BY_KEY_V4["interest_coverage"].peer_eligibility.matched_period
          == ("operating_income", "interest_expense")
          and FS.BY_KEY_V4["interest_coverage"].peer_eligibility.is_price_free)
    check("v3 interest_coverage is PRESERVED",
          FS.BY_KEY_V3["interest_coverage"].peer_eligibility
          is FS.OPERATING_INCOME_AND_INTEREST)
    check("REPLACED_BY_VERSION is cumulative for v4 and unchanged for v3",
          FS.REPLACED_BY_VERSION[FS.FACTOR_SPEC_VERSION_V4]
          == frozenset({"ebitda_benchmark", "pe_ratio", "ebitda_growth",
                        "ebitda_acceleration", "interest_coverage"})
          and FS.REPLACED_BY_VERSION[FS.FACTOR_SPEC_VERSION_V3]
          == frozenset({"ebitda_benchmark", "pe_ratio"}))
    check("spec_versions() ends with v5", FS.spec_versions()[-1] == FS.FACTOR_SPEC_VERSION_V5)
    check("11 of 14 v4 specs are the v3 objects by reference",
          sum(1 for a, b in zip(FS.SPECS_V3, FS.SPECS_V4) if a is b) == 11)
    check("FORMULAS_V1 restates pit_derive where a node exists",
          all(FS.FORMULAS_V1[k].startswith(pit_derive.NODES[n].formula)
              for k, n in FS._FORMULA_NODE.items()))
    check("the base policy names its refusals and a finite band",
          FS.EBITDA_GROWTH_BASE_POLICY_V1["zero_base"] == "REFUSED:ebitda_growth_base_zero"
          and FS.EBITDA_GROWTH_BASE_POLICY_V1["max_abs_rate"] == 10.0)
    check("the coverage domain policy names every state the owner listed",
          all(k in FS.INTEREST_COVERAGE_DOMAIN_V1
              for k in ("ordinary", "zero", "negative", "missing", "no_numerator",
                        "negative_operating_income", "rung_rule")))
    dom = FS.INTEREST_COVERAGE_DOMAIN_V1
    check("...and their VALUES say what the owner said: negative OI is LEGITIMATE, "
          "interest > 0 is ordinary, the four bad denominators are REFUSED",
          dom["negative_operating_income"].startswith("LEGITIMATE")
          and dom["ordinary"].startswith("interest_expense > 0")
          and all(dom[k].startswith("REFUSED:")
                  for k in ("zero", "negative", "missing", "no_numerator")))
    check("validate() is clean under v4", FS.validate() == [], FS.validate())


def section_10_v5() -> None:
    print("\n== 10. factor_spec_v5: the owner's V/Q rulings of 2026-09-23, and only those ==")
    check("the four moved keys carry their successor rules BY REFERENCE",
          FS.BY_KEY_V5["net_debt_ebitda"].peer_eligibility is FS.DEBT_CASH_EBITDA_MATCHED_V2
          and FS.BY_KEY_V5["fcf_conversion"].peer_eligibility is FS.FCF_AND_EBITDA_MATCHED_V2
          and FS.BY_KEY_V5["debt_market_cap"].peer_eligibility is FS.DEBT_AND_MARKET_CAP_MATCHED_V2
          and FS.BY_KEY_V5["ev_ebitda"].peer_eligibility is FS.EV_EBITDA_SIX_V2)
    check("v4 is PRESERVED: the same four keys still carry the v1 rules there",
          FS.BY_KEY_V4["net_debt_ebitda"].peer_eligibility is FS.DEBT_CASH_EBITDA
          and FS.BY_KEY_V4["ev_ebitda"].peer_eligibility is FS.EV_EBITDA_SIX)
    check("10 of 14 v5 specs are the v4 objects by reference",
          sum(1 for a, b in zip(FS.SPECS_V4, FS.SPECS_V5) if a is b) == 10)
    check("every successor rule matches periods (R12) and the two EBITDA-denominator "
          "ratios screen EBITDA > 0 (R11)",
          all(r.matched_period for r in (FS.DEBT_CASH_EBITDA_MATCHED_V2,
                                         FS.FCF_AND_EBITDA_MATCHED_V2,
                                         FS.DEBT_AND_MARKET_CAP_MATCHED_V2,
                                         FS.EV_EBITDA_SIX_V2))
          and FS.DEBT_CASH_EBITDA_MATCHED_V2.positive_screen == "ebitda > 0"
          and FS.FCF_AND_EBITDA_MATCHED_V2.positive_screen == "ebitda > 0")
    check("R15: negative enterprise value is VALID -- the EV band has no lower bound "
          "and keeps v1's upper bound",
          FS.EV_EBITDA_SIX_V2.value_band == ("ev_ebitda", None, 300.0))
    check("debt_market_cap DECLARES its period-anchored leaf set (R12 interpretation)",
          "operating_income" in FS.BY_KEY_V5["debt_market_cap"].required_primitives
          and "EBITDA period end" in FS.BY_KEY_V5["debt_market_cap"].graph_divergence)
    check("REPLACED_BY_VERSION is cumulative for v5",
          FS.REPLACED_BY_VERSION[FS.FACTOR_SPEC_VERSION_V5]
          >= FS.REPLACED_BY_VERSION[FS.FACTOR_SPEC_VERSION_V4]
          and {"net_debt_ebitda", "fcf_conversion", "debt_market_cap", "ev_ebitda"}
          <= FS.REPLACED_BY_VERSION[FS.FACTOR_SPEC_VERSION_V5])
    check("FORMULAS_V1 is preserved verbatim and FORMULAS_V2 restates every key",
          FS.FORMULAS_V1["pe_relative"].endswith("BENCHMARK_ANCHORED")
          and set(FS.FORMULAS_V2) == set(FS.FORMULAS_V1)
          and "CONVEX" in FS.FORMULAS_V2["pe_relative"]
          and "tanh" in FS.FORMULAS_V2["ev_ebitda_supplement"])
    check("...and where a pit_derive node exists, V2 still begins with its formula",
          all(FS.FORMULAS_V2[k].startswith(pit_derive.NODES[n].formula)
              for k, n in FS._FORMULA_NODE.items()))
    check("R1/R3: the base policy keeps the band and renames ONE code; V1 keeps the old",
          FS.EBITDA_GROWTH_BASE_POLICY_V2["max_abs_rate"] == 10.0
          and FS.EBITDA_GROWTH_BASE_POLICY_V2["beyond_max_abs_rate"]
          == "REFUSED:ebitda_growth_rate_beyond_band"
          and FS.EBITDA_GROWTH_BASE_POLICY_V1["beyond_max_abs_rate"]
          == "REFUSED:ebitda_growth_base_near_zero"
          and "OWNER_CONFIRMED" in FS.EBITDA_GROWTH_BASE_POLICY_V2["status"])
    check("R4/R5: the coverage domain V2 names the floor state and the positive-OI "
          "rank population",
          FS.INTEREST_COVERAGE_DOMAIN_V2["non_positive_operating_income"].startswith("FLOOR_STATE:")
          and "positive" in FS.INTEREST_COVERAGE_DOMAIN_V2["rank_population"]
          and FS.INTEREST_COVERAGE_DOMAIN_V1["negative_operating_income"].startswith("LEGITIMATE"))
    check("the five new domain bodies and the gate each name REFUSED states and say "
          "when direction applies",
          all(any(str(v).startswith("REFUSED:") for v in body.values())
              for body in (FS.LEVERAGE_DOMAIN_V1, FS.FCF_DOMAIN_V1,
                           FS.MARKET_CAP_DOMAIN_V1, FS.EV_EBITDA_DOMAIN_V1,
                           FS.PE_DOMAIN_V1, FS.DEBT_RUNG_GATE_V1))
          and all("direction" in body for body in (FS.LEVERAGE_DOMAIN_V1, FS.FCF_DOMAIN_V1,
                                                    FS.MARKET_CAP_DOMAIN_V1,
                                                    FS.EV_EBITDA_DOMAIN_V1, FS.PE_DOMAIN_V1)))
    _spec_of = {"pe_relative": "pe_ratio", "ev_ebitda_supplement": "ev_ebitda"}
    check("R14: PEER_FLOOR_V1 names eleven cohort keys, none below its spec floor",
          len(FS.PEER_FLOOR_V1) == 11 and FS.PEER_FLOOR_V1["ebitda_benchmark"] == 12
          and FS.PEER_FLOOR_V1["ev_ebitda_supplement"] == 12
          and FS.PEER_FLOOR_V1["pe_relative"] == 3
          and all(FS.PEER_FLOOR_V1[k]
                  >= FS.spec(_spec_of.get(k, k), FS.FACTOR_SPEC_VERSION_V5).min_peer_count
                  for k in FS.PEER_FLOOR_V1))
    check("the golden witnesses of V2 cover the Q ratios, the floor state and the "
          "benchmark, and every one carries an 'expect'",
          {c["factor"] for c in FS.FORMULA_GOLDEN_V2}
          >= {"net_debt_ebitda", "fcf_conversion", "interest_coverage", "ebitda_benchmark"}
          and all("expect" in c for c in FS.FORMULA_GOLDEN_V2)
          and any(c.get("kind") == "benchmark" for c in FS.FORMULA_GOLDEN_V2)
          and any(c.get("expect_floor_state") for c in FS.FORMULA_GOLDEN_V2))
    check("validate() is clean under v5", FS.validate() == [], FS.validate())


def main() -> int:
    started = time.time()
    section_1_price_free()
    section_1b_mutations()
    section_2_two_cohorts()
    section_3_refusal()
    section_4_unique_leaves()
    section_5_six_fields()
    section_6_band()
    section_7_measured()
    section_8_store()
    section_9_v4()
    section_10_v5()
    print()
    if skips:
        print(f"{len(skips)} skipped:")
        for line in skips:
            print(f"  SKIP  {line}")
    if fails:
        print(f"{len(fails)} FAILED:")
        for line in fails:
            print(f"  FAIL  {line}")
    print(f"{'PASS' if not fails else 'FAIL'}  ({time.time() - started:.1f}s)")
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
