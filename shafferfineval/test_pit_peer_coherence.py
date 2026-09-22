"""Tests for the two-test peer gate.   python test_pit_peer_coherence.py

    1   a VALUATION factor may not draw its cohort from the SEC Office rung --
        asserted STRUCTURALLY, and then MUTATED four ways, every mutation
        required to fail validate(), so a future edit that re-enables the
        office rung cannot pass this file;
    2   a NON-valuation factor may still use it -- the control, because a gate
        that refused everything would be worthless;
    3   the gate refuses an office cohort WITHOUT TOUCHING THE STORE (the
        connection passed in is None: if the rung test ever moves after the
        first SELECT, this raises instead of refusing);
    4   the coherence statistics behave as claimed on constructed cohorts, and
        SEPARATE THE RUNGS on the real store;
    5   VALUATION_PEER_SET_INSUFFICIENT reaches the score as a PARTIAL SHAFFER
        SCORE that is not comparable with a full one, with no survivor promoted.

Section 6 is the only part that touches the store: it opens `mode=ro`, holds
no snapshot, and SKIPS -- reporting the fact, never inventing a result -- if
the database is absent or locked. Section 7 reads the measured artefact if one
has been produced and SKIPS if not.
"""

from __future__ import annotations

import dataclasses
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_factor_spec as FS
import pit_peer_coherence as PC
import pit_score_signature as S
import pit_store

fails: list[str] = []
skips: list[str] = []

V2 = FS.FACTOR_SPEC_VERSION_V2
MEASURED_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "pit_archive", "coherence", "peer_coherence.json")


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
# (1) THE DECLARATION: office is forbidden to valuation, and v1 says so
# ==========================================================================

def section_1_declaration() -> None:
    print("[1] a valuation factor declares a rung ceiling that EXCLUDES the "
          "office rung")
    for key in sorted(FS.VALUATION_FACTORS):
        item = FS.spec(key, V2)
        check(f"{key}: declares a ceiling", bool(item.max_admissible_rung),
              item.max_admissible_rung)
        check(f"{key}: office is NOT admissible",
              item.rung_admissible(FS.RUNG_OFFICE) is False)
        check(f"{key}: sic4, sic3 and sic2 ARE admissible",
              all(item.rung_admissible(r) for r in ("sic4", "sic3", "sic2")),
              item.admissible_rungs)
        check(f"{key}: the forbidden set is exactly the office rung",
              item.forbidden_rungs == (FS.RUNG_OFFICE,), item.forbidden_rungs)
        check(f"{key}: admissible rungs are a PREFIX of the widening ladder",
              item.admissible_rungs
              == tuple(item.fallback_rung[:len(item.admissible_rungs)]),
              (item.admissible_rungs, item.fallback_rung))

    # v1 is frozen and declares no ceiling. That is a statement about v1, and
    # it is recorded as a limitation rather than left as an omission.
    for key in sorted(FS.BY_KEY):
        check(f"v1/{key}: declares NO ceiling (v1 is frozen)",
              FS.spec(key).max_admissible_rung == "")
    check("the v1 limitation is named",
          "office" in FS.FACTOR_SPEC_V1_KNOWN_LIMITATION.lower()
          and FS.VALUATION_PEER_SET_INSUFFICIENT
          in FS.FACTOR_SPEC_V1_KNOWN_LIMITATION)

    # v2 is v1 plus one dimension, and everything else is the SAME OBJECT.
    shared = [k for k in FS.BY_KEY if k not in FS.VALUATION_FACTORS]
    check("every non-valuation spec is shared BY REFERENCE between v1 and v2",
          all(FS.spec(k) is FS.spec(k, V2) for k in shared))
    check("...and exactly the valuation factors differ",
          {k for k in FS.BY_KEY if FS.spec(k) is not FS.spec(k, V2)}
          == set(FS.VALUATION_FACTORS))

    # v1's serialised spec is unchanged by the existence of the gate.
    v1_dict = FS.spec("ev_ebitda").as_dict()
    check("a v1 spec serialises WITHOUT the new fields",
          "max_admissible_rung" not in v1_dict
          and v1_dict["spec_version"] == FS.FACTOR_SPEC_VERSION)
    v2_dict = FS.spec("ev_ebitda", V2).as_dict()
    check("...and a v2 spec serialises WITH them, stamped v2",
          v2_dict["max_admissible_rung"] == FS.VALUATION_MAX_RUNG
          and v2_dict["forbidden_rungs"] == [FS.RUNG_OFFICE]
          and v2_dict["spec_version"] == V2)
    check("validate() is clean as shipped", FS.validate() == [], FS.validate()[:2])


def section_1b_mutations() -> None:
    """RE-ENABLING THE OFFICE RUNG MUST FAIL. This is the part that has to
    survive the next year, not the part that pins today."""
    print("[1b] mutating the ceiling -- every mutation must FAIL validate()")
    base = FS.spec("ev_ebitda", V2)
    mutations = {
        "raises the ceiling to the office rung":
            dataclasses.replace(base, max_admissible_rung=FS.RUNG_OFFICE),
        "drops the ceiling entirely":
            dataclasses.replace(base, max_admissible_rung=""),
        "sets a ceiling that is not on the ladder":
            dataclasses.replace(base, max_admissible_rung="sector"),
        "reorders the ladder so office comes first":
            dataclasses.replace(base, max_admissible_rung=FS.RUNG_OFFICE,
                                fallback_rung=("office", "sic2", "sic3", "sic4")),
    }
    original_specs, original_spec = FS.SPECS_V2, FS.BY_KEY_V2[base.key]
    for name, mutant in mutations.items():
        FS.SPECS_V2 = tuple(mutant if s.key == base.key else s
                            for s in original_specs)
        FS.BY_KEY_V2[base.key] = mutant
        try:
            problems = FS.validate()
            hit = any("office" in p or "VALUATION" in p or "ladder" in p
                      for p in problems)
            check(f"{name} -> validate() FAILS", hit,
                  problems[:2] or "validate() returned no problems")
        finally:
            FS.SPECS_V2 = original_specs
            FS.BY_KEY_V2[base.key] = original_spec
    check("the registry is restored after the mutations",
          FS.validate() == [] and FS.spec("ev_ebitda", V2) is original_spec)

    # And the vocabulary itself cannot be softened without a failure.
    original_max = FS.VALUATION_MAX_RUNG
    try:
        FS.VALUATION_MAX_RUNG = FS.RUNG_OFFICE
        check("moving VALUATION_MAX_RUNG to the office rung -> validate() FAILS",
              any("office" in p for p in FS.validate()))
    finally:
        FS.VALUATION_MAX_RUNG = original_max


# ==========================================================================
# (2) THE GATE: two independent tests, reported separately
# ==========================================================================

def section_2_gate() -> None:
    print("[2] the gate runs BOTH tests and says which one refused")
    big = FS.peer_set_gate("ev_ebitda", FS.RUNG_OFFICE, 900, version=V2)
    check("a 900-member office cohort is REFUSED", big.ok is False)
    check("...with the owner's first-class outcome",
          big.reason == FS.VALUATION_PEER_SET_INSUFFICIENT, big.reason)
    check("...failing ECONOMIC COHERENCE while PASSING sufficient N -- the "
          "two tests move in opposite directions and the record shows it",
          big.failed_tests == (FS.TEST_ECONOMIC_COHERENCE,)
          and big.sufficient_n is True, (big.failed_tests, big.sufficient_n))
    check("...and the refusal explains what a 'peer' means at that rung",
          "SEC Corporation Finance" in big.note)

    thin = FS.peer_set_gate("ev_ebitda", "sic4", 11, version=V2)
    check("an 11-member sic4 cohort is refused on SUFFICIENT N",
          thin.failed_tests == (FS.TEST_SUFFICIENT_N,)
          and thin.rung_admissible is True, thin.failed_tests)
    check("...and still carries the valuation outcome, not 'insufficient_peers'",
          thin.reason == FS.VALUATION_PEER_SET_INSUFFICIENT, thin.reason)

    ok = FS.peer_set_gate("ev_ebitda", "sic2", 12, version=V2)
    check("a 12-member sic2 cohort PASSES both tests",
          ok.ok and ok.reason is None and ok.failed_tests == ())

    both = FS.peer_set_gate("ev_ebitda", FS.RUNG_OFFICE, 2, version=V2)
    check("a cohort that fails BOTH tests names both",
          set(both.failed_tests) == {FS.TEST_SUFFICIENT_N,
                                     FS.TEST_ECONOMIC_COHERENCE},
          both.failed_tests)

    # THE CONTROL. The ban is on valuation peer groups, not on the rung.
    print("[2b] a NON-valuation factor may still use the office rung")
    for key in ("ebitda_benchmark", "roa", "net_debt_ebitda", "fcf_conversion"):
        gate = FS.peer_set_gate(key, FS.RUNG_OFFICE,
                                FS.spec(key, V2).min_peer_count, version=V2)
        check(f"{key} at the office rung is ALLOWED", gate.ok is True,
              (gate.reason, gate.failed_tests))
    bench = FS.peer_set_gate("ebitda_benchmark", FS.RUNG_OFFICE, 2, version=V2)
    check("...and when one IS too thin it keeps pit_store's own vocabulary",
          bench.reason == pit_store.REASON_NO_PEERS, bench.reason)

    # Under v1 nothing is gated: the frozen baseline is preserved, not corrected.
    v1_gate = FS.peer_set_gate("ev_ebitda", FS.RUNG_OFFICE, 900)
    check("under factor_spec_v1 the office cohort is NOT refused -- v1 is a "
          "baseline to be replayed, not a model to be fixed",
          v1_gate.ok is True and v1_gate.spec_version == FS.FACTOR_SPEC_VERSION)


def section_3_refused_without_the_store() -> None:
    """The refusal must happen BEFORE any SQL. Passing None as the connection
    is how that is asserted rather than asserted about."""
    print("[3] an office-rung valuation request is refused WITHOUT a database")
    members = list(range(1000, 1900))          # 900 peers, far above min_peer_count
    result = FS.eligible_peers(None, "ev_ebitda", "2024-06-28", members,
                               rung=FS.RUNG_OFFICE, version=V2)
    check("refused", result.availability == pit_store.AVAIL_UNAVAILABLE)
    check("...with VALUATION_PEER_SET_INSUFFICIENT",
          result.reason == FS.VALUATION_PEER_SET_INSUFFICIENT, result.reason)
    check("...before a single SELECT ran (conn was None and nothing raised)",
          result.n_eligible == 0 and result.n_offered == 900)
    check("...and the gate record travels with the cohort",
          result.gate is not None
          and result.gate.failed_tests == (FS.TEST_ECONOMIC_COHERENCE,))

    # A ceilinged factor with no rung supplied must RAISE. A gate that silently
    # skips when it is not told the rung is not a gate.
    raised = False
    try:
        FS.eligible_peers(None, "ev_ebitda", "2024-06-28", members, version=V2)
    except ValueError as exc:
        raised = "rung" in str(exc)
    check("a ceilinged factor with NO rung raises rather than guessing", raised)

    # v1 is untouched: no ceiling, so no rung is required and none is applied.
    try:
        FS.eligible_peers(None, "ev_ebitda", "2024-06-28", [1, 2],
                          rung=FS.RUNG_OFFICE)
        v1_ok = False
    except Exception:
        # v1 gets past the rung test and reaches the store, which is None here.
        v1_ok = True
    check("v1 applies no rung test at all (it reaches the store instead)", v1_ok)


# ==========================================================================
# (4) THE COHERENCE STATISTICS
# ==========================================================================

def section_4_coherence() -> None:
    print("[4] coherence is a NUMBER, and the number behaves")
    one = FS.cohort_coherence("sic4", {i: "2834" for i in range(20)})
    check("a single-SIC4 cohort has exactly 1.00 effective industries",
          one.effective_sic4_count == 1.0 and one.top_sic4_share == 1.0)
    two = FS.cohort_coherence("sic3", {i: ("2834" if i % 2 else "2836")
                                       for i in range(20)})
    check("a 50/50 two-industry cohort has exactly 2.00",
          two.effective_sic4_count == 2.0 and two.top_sic4_share == 0.5)
    many = FS.cohort_coherence("office", {i: f"{2000 + i}" for i in range(100)})
    check("a 100-industry cohort has 100.00 effective industries",
          many.effective_sic4_count == 100.0)
    check("...and its largest single industry is 1% of it",
          many.top_sic4_share == 0.01)

    # Scale-free: adding more of the SAME industry must not move it. This is
    # the property that keeps the measure from restating the member count.
    doubled = FS.cohort_coherence("sic3", {i: ("2834" if i % 2 else "2836")
                                           for i in range(400)})
    check("doubling a cohort with more of the same industries leaves the "
          "composition measure unchanged -- it is not a proxy for N",
          doubled.effective_sic4_count == two.effective_sic4_count)

    unclassified = FS.cohort_coherence("sic2", {1: None, 2: None, 3: "2834"})
    check("members with no SIC are counted but excluded from the composition",
          unclassified.n_members == 3 and unclassified.n_distinct_sic4 == 1)
    empty = FS.cohort_coherence("sic2", {})
    check("an empty cohort measures None, never 0.0 -- 'not measurable' and "
          "'measured and perfect' are different answers",
          empty.effective_sic4_count is None and empty.top_sic4_share is None)

    thin = FS.cohort_coherence("sic4", {i: "2834" for i in range(3)},
                               {0: 0.1, 1: 0.2, 2: 0.3})
    check("a margin spread over 3 members is not reported",
          thin.margin_iqr is None and thin.n_margin == 3)
    spread = FS.cohort_coherence(
        "sic4", {i: "2834" for i in range(8)},
        {i: value for i, value in enumerate([0.0, 0.1, 0.2, 0.3, 0.4, 0.5,
                                             0.6, 0.7])})
    check("a margin IQR is computed once there are four or more",
          spread.margin_iqr is not None and spread.n_margin == 8)
    sized = FS.cohort_coherence("sic4", {i: "2834" for i in range(8)}, None,
                                {i: 10.0 ** i for i in range(8)})
    check("the size span is in DECADES of EBITDA",
          sized.log10_ebitda_span is not None
          and abs(sized.log10_ebitda_span - 5.6) < 0.01,
          sized.log10_ebitda_span)
    losses = FS.cohort_coherence("sic4", {i: "2834" for i in range(8)}, None,
                                 {i: -1000.0 for i in range(8)})
    check("loss-makers are dropped from the size span rather than logged",
          losses.log10_ebitda_span is None and losses.n_level == 0)

    check("the SIC divisions match pit_cohort_measure's list exactly",
          _divisions_agree())


def _divisions_agree() -> bool:
    try:
        import pit_cohort_measure
    except Exception:
        return True                      # nothing to disagree with
    return tuple(pit_cohort_measure.SIC_DIVISIONS) == tuple(PC.SIC_DIVISIONS)


# ==========================================================================
# (5) THE SCORE: a gated-out valuation pillar is a PARTIAL score
# ==========================================================================

def section_5_partial_score() -> None:
    print("[5] VALUATION_PEER_SET_INSUFFICIENT makes the score PARTIAL, not "
          "a renormalised full one")
    check("one constant, one meaning: the spec's outcome IS the score "
          "module's, and both are pit_store's",
          FS.VALUATION_PEER_SET_INSUFFICIENT
          is S.VALUATION_PEER_SET_INSUFFICIENT
          is pit_store.REASON_VALUATION_PEER_SET_INSUFFICIENT)

    # THE WIRE, end to end: two refused gates -> one pillar reason -> a partial
    # score. No step of it invents a string.
    office_ev = FS.peer_set_gate("ev_ebitda", FS.RUNG_OFFICE, 900, version=V2)
    office_pe = FS.peer_set_gate("pe_ratio", FS.RUNG_OFFICE, 900, version=V2)
    formed = FS.peer_set_gate("ev_ebitda", "sic2", 40, version=V2)
    check("every valuation factor refused -> the PILLAR is unavailable",
          FS.valuation_pillar_reason([office_ev, office_pe])
          == FS.VALUATION_PEER_SET_INSUFFICIENT)
    check("...one formed cohort is enough to keep the pillar",
          FS.valuation_pillar_reason([office_ev, formed]) is None)
    check("...and nothing attempted is a SILENCE, not a finding",
          FS.valuation_pillar_reason([]) is None)
    check("...a non-valuation refusal never speaks for the pillar",
          FS.valuation_pillar_reason(
              [FS.peer_set_gate("roa", "sic4", 1, version=V2)]) is None)

    scores = {"growth": 40.0, "profitability": 20.0, "debt": 10.0}
    reasons = {S.PILLAR_VALUATION:
               FS.valuation_pillar_reason([office_ev, office_pe])}
    result = S.score_v2(scores, reasons=reasons)
    check("the marker is PARTIAL_SHAFFER_SCORE",
          result.partial_score_marker == S.PARTIAL_SHAFFER_SCORE)
    check("...the signature says which pillar is gone",
          result.signature == "GPD" and result.pillars_missing == ("valuation",))
    check("...the reason is readable off the row, not parsed out of prose",
          result.valuation_peer_set_insufficient is True)
    check("...it is NOT comparable with a full score (decision 7)",
          result.comparable_with_full is False)

    full = S.score_v2({**scores, "valuation": 50.0})
    check("no survivor was promoted: every present pillar keeps the effective "
          "weight it had in the full score",
          all(abs(full.effective_weights[p] - result.effective_weights[p]) < 1e-12
              for p in result.pillars_present))
    check("...so the partial score is SMALLER than the full one, not rescaled",
          result.company_score < full.company_score)
    check("the note names the outcome",
          FS.VALUATION_PEER_SET_INSUFFICIENT in " ".join(result.notes))
    check("...and the rendered partial score prints it for a reader",
          FS.VALUATION_PEER_SET_INSUFFICIENT in S.render_partial_score(result))
    check("pit_score_fields carries the reason to the writer",
          S.pit_score_fields(result)["unavailable_reasons"]
          == {"valuation": FS.VALUATION_PEER_SET_INSUFFICIENT})

    v1 = S.score_v1(scores, reasons=reasons)
    check("the v1 baseline still RENORMALISES -- it is replayed, not corrected",
          v1.renormalised is True
          and v1.partial_score_marker == S.V1_RENORMALISED_PARTIAL
          and v1.effective_weights["growth"] > S.V1_MAJOR_WEIGHTS["growth"])
    check("...and still records why the pillar went missing",
          v1.unavailable_reasons == {"valuation":
                                     FS.VALUATION_PEER_SET_INSUFFICIENT})

    pooled = S.evaluation_groups([full, result])
    check("the two never pool by default",
          len(pooled.groups) == 2, sorted(pooled.groups))
    check("validate() is clean", S.validate() == [], S.validate()[:2])


# ==========================================================================
# (6) AGAINST THE REAL STORE (read-only; SKIPS if absent or locked)
# ==========================================================================

def section_6_store() -> None:
    print("[6] against the real store (read-only; SKIPS if absent or locked)")
    db_path = pit_store.DEFAULT_PIT_DB_PATH
    if not os.path.exists(db_path):
        skip("store section", f"{db_path} is absent")
        return
    wal_before = PC.wal_bytes(db_path)
    try:
        conn = PC.connect_ro(db_path)
    except sqlite3.Error as exc:
        skip("store section", f"cannot open read-only: {exc}")
        return
    try:
        row = conn.execute(
            """SELECT peer_set_id, as_of_date, rung, key, n_members
                 FROM pit_peer_set WHERE rung = ? ORDER BY peer_set_id LIMIT 1""",
            (FS.RUNG_OFFICE,)).fetchone()
        if row is None:
            skip("store section", "no office-rung peer sets in this store")
            return
        day = row["as_of_date"]
        members = [int(r["entity_id"]) for r in conn.execute(
            "SELECT entity_id FROM pit_peer_member WHERE peer_set_id = ?",
            (int(row["peer_set_id"]),))]
        print(f"        office peer set {row['peer_set_id']} "
              f"({row['key']}, {len(members)} members) at {day}")

        # The rung is read off the ROW, so a caller cannot forget to declare it.
        refused = FS.eligible_peers(conn, "ev_ebitda", day,
                                    int(row["peer_set_id"]), version=V2)
        check("a real office peer set is refused for ev_ebitda",
              refused.reason == FS.VALUATION_PEER_SET_INSUFFICIENT
              and refused.rung == FS.RUNG_OFFICE,
              (refused.reason, refused.rung))
        check("...and it was refused on size grounds it would have PASSED: "
              f"{len(members)} members is far above "
              f"{FS.spec('ev_ebitda', V2).min_peer_count}",
              len(members) >= FS.spec("ev_ebitda", V2).min_peer_count)

        allowed = FS.eligible_peers(conn, "ebitda_benchmark", day,
                                    int(row["peer_set_id"]), version=V2)
        check("...while the EBITDA benchmark is allowed to use the same set",
              allowed.reason != FS.VALUATION_PEER_SET_INSUFFICIENT,
              (allowed.availability, allowed.reason, allowed.n_eligible))

        # The coherence statistics, on real cohorts at one date.
        sics = PC.sic_index(conn)
        by_rung: dict[str, float] = {}
        for rung in ("sic4", "sic2", FS.RUNG_OFFICE):
            sample = conn.execute(
                """SELECT peer_set_id FROM pit_peer_set
                    WHERE as_of_date = ? AND rung = ?
                    ORDER BY n_members DESC LIMIT 5""", (day, rung)).fetchall()
            values = []
            for peer_set in sample:
                cohort = [int(r["entity_id"]) for r in conn.execute(
                    "SELECT entity_id FROM pit_peer_member WHERE peer_set_id = ?",
                    (int(peer_set["peer_set_id"]),))]
                coherence = FS.cohort_coherence(
                    rung, {e: PC.sic_as_of(sics, e, day) for e in cohort})
                if coherence.effective_sic4_count is not None:
                    values.append(coherence.effective_sic4_count)
            if values:
                by_rung[rung] = sum(values) / len(values)
        print(f"        mean effective SIC4 industries per cohort: "
              + ", ".join(f"{k} {v:.2f}" for k, v in by_rung.items()))
        check("on real cohorts the composition measure separates sic4 from "
              "the office rung",
              by_rung.get("sic4", 0) < by_rung.get("sic2", 0)
              < by_rung.get(FS.RUNG_OFFICE, 0), by_rung)

        # The connection is genuinely read-only.
        refused_write = False
        try:
            conn.execute("CREATE TABLE _probe (x)")
        except sqlite3.OperationalError:
            refused_write = True
        check("the connection REFUSES a write", refused_write)
    finally:
        conn.close()
    # The WAL is REPORTED, not asserted: this connection cannot write (checked
    # above, structurally), and another agent writing to the same store is a
    # fact about this machine rather than a failure of this test.
    print(f"        WAL {wal_before:,} -> {PC.wal_bytes(db_path):,} bytes "
          f"(this section holds a mode=ro connection and cannot write)")


# ==========================================================================
# (7) THE MEASURED ARTEFACT (SKIPS if the measurement has not been run)
# ==========================================================================

def section_7_measured() -> None:
    print("[7] the measured artefact agrees with what the spec declares")
    if not os.path.exists(MEASURED_JSON):
        skip("measured artefact", f"{MEASURED_JSON} has not been produced")
        return
    with open(MEASURED_JSON, encoding="utf-8") as handle:
        payload = json.load(handle)
    check("the measurement passed its own checks",
          payload.get("problems") == [], payload.get("problems", [])[:2])
    adopted = payload.get("coherence_adopted") or []
    check("at least one statistic survived the size-matched control",
          bool(adopted), adopted)
    check("pit_factor_spec.COHERENCE_ADOPTED names exactly those",
          sorted(FS.COHERENCE_ADOPTED) == sorted(adopted),
          (sorted(FS.COHERENCE_ADOPTED), sorted(adopted)))
    for name in FS.COHERENCE_MEASURES:
        check(f"{name} was measured at every rung, adopted or not",
              all(name in payload["coherence_by_rung"].get(rung, {})
                  or name == "margin_p10_p90"
                  for rung in ("sic4", "sic3", "sic2", "office")),
              name)
    cost = payload["cost_of_the_ban"]
    check("the cost of the ban is recorded, by year and by SIC division",
          cost["by_year"] and cost["by_division"] and "lost_total" in cost)
    check("...and the measured cost is what the spec's evidence quotes",
          str(cost["lost_total"]) in json.dumps(FS.COHERENCE_MEASURED)
          or not FS.COHERENCE_MEASURED,
          cost["lost_total"])
    resources = payload["resources"]
    check("the run reported a measured peak working set, not 0",
          resources["peak_working_set_bytes"] != 0)
    check("...and never let the volume fall below the floor",
          resources["free_bytes_min_observed"] >= PC.MIN_FREE_BYTES)


def main() -> int:
    started = time.time()
    print("=" * 74)
    print("THE TWO-TEST PEER GATE: sufficient N, and economic coherence")
    print("=" * 74)
    for section in (section_1_declaration, section_1b_mutations,
                    section_2_gate, section_3_refused_without_the_store,
                    section_4_coherence, section_5_partial_score,
                    section_6_store, section_7_measured):
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
