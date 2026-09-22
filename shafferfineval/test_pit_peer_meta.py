"""Offline tests for the peer-set metadata rebuild and its staleness guard.

Pure stdlib, no network, a temporary store:   python test_pit_peer_meta.py

THE FIXTURE REPRODUCES THE ACTUAL BUG, it does not simulate its symptom. The
cohorts are built while `pit_listing` is EMPTY -- exactly the race that ran in
the real store on 2026-09-21, where 165 cross-sections were written between
13:07 and 13:15 while another process filled the listing table until 13:22. The
listings and their bars are inserted AFTERWARDS, and the tests then assert that
every stale count is detected, corrected, and corrected without touching one row
of membership.

    2834 (20 entities, 12 of them later listed and priced)
    7372 (14 entities,  4 of them later listed and priced)

Both cohorts clear MIN_COHORT_N at rung sic4, so the fixture holds two cohorts
whose priced coverage differs sharply -- 60% against 28.6% -- which is what a
by-rung coverage report has to be able to tell apart.

THE ACCEPTANCE TESTS are sections 4 and 5. Section 4 is the invariant the whole
in-place decision rests on: after the rewrite, SUM(n_members) still equals
COUNT(*) in pit_peer_member EXACTLY and the distinct-entity count is unchanged,
which is what proves no peer was dropped for lack of a price. Section 5 is the
guard: a date whose metadata predates listings that exist now must be refused,
and a date with no provenance at all must be refused too -- `unrecorded` is not
a pass.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_peer_meta as M
import pit_peers as P
import pit_store

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
# Fixture
# --------------------------------------------------------------------------

EARLY = "2020-01-31"
LATE = "2020-02-28"
DATES = (EARLY, LATE)

#: (sic, n_entities, n_of_them_later_priced)
COHORTS = (("2834", 20, 12), ("7372", 14, 4))


def build_cohorts_before_prices(db_path: str) -> tuple[object, dict[str, int]]:
    """Entities, classifications and cohorts -- with pit_listing still EMPTY.

    This is the race, not a stand-in for it: build_peer_sets computes its priced
    subset from a listing table that does not yet have the rows, and records 0.
    """
    conn = P.prepare_connection(pit_store.init_db(db_path))
    ids: dict[str, int] = {}
    counter = 0
    for sic, n, _priced in COHORTS:
        for i in range(n):
            counter += 1
            cik = f"20{counter:05d}"
            entity_id = pit_store.upsert_entity(conn, cik)
            ids[f"{sic}:{i}"] = entity_id
            pit_store.add_entity_sic(conn, entity_id, sic, "2019-06-01",
                                     f"{cik}-19", "test")
    for day in DATES:
        P.build_peer_sets(conn, day)
    return conn, ids


def add_listings_and_bars(conn, ids: dict[str, int]) -> int:
    """The second process, arriving late: listings and daily bars."""
    n = 0
    for sic, _total, n_priced in COHORTS:
        for i in range(n_priced):
            entity_id = ids[f"{sic}:{i}"]
            listing_id = pit_store.upsert_listing(
                conn, f"{sic}L{i}", "2010-01-04", entity_id=entity_id,
                exchange="NYSE", instrument_type="EQUITY", currency="USD",
                valid_from="2010-01-04", confidence="proved", source="test")
            with pit_store.transaction(conn):
                conn.executemany(
                    """INSERT OR IGNORE INTO pit_price_bar
                           (listing_id, bar_date, open, high, low, close,
                            adjclose, volume, source_symbol, ingest_id)
                       VALUES (?, ?, 10, 10, 10, 10, 10, 1000, ?, 1)""",
                    [(listing_id, day, f"{sic}L{i}") for day in DATES])
            n += 1
    return n


def _sum(conn, column: str) -> int:
    return int(conn.execute(f"SELECT COALESCE(SUM({column}), 0) "
                            "FROM pit_peer_set").fetchone()[0])


def _count(conn, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

def main() -> int:
    tmp = tempfile.mkdtemp(prefix="pit_peer_meta_test_")
    db_path = os.path.join(tmp, "meta.db")
    try:
        conn, ids = build_cohorts_before_prices(db_path)

        print("== 1. the race, reproduced ==")
        n_cohorts = _count(conn, "pit_peer_set")
        check("both cohorts were built", n_cohorts == 2 * len(COHORTS), n_cohorts)
        check("every cohort recorded zero price-resolvable members",
              _sum(conn, "n_price_resolvable") == 0,
              _sum(conn, "n_price_resolvable"))
        check("membership itself is complete",
              _sum(conn, "n_members") == _count(conn, "pit_peer_member")
              == 2 * sum(n for _s, n, _p in COHORTS),
              (_sum(conn, "n_members"), _count(conn, "pit_peer_member")))
        check("the build stamped a listing census of zero",
              [tuple(r) for r in conn.execute(
                  "SELECT DISTINCT n_listings_at_build FROM pit_peer_set_build")]
              == [(0,)])

        members_before = _count(conn, "pit_peer_member")
        entities_before = int(conn.execute(
            "SELECT COUNT(DISTINCT entity_id) FROM pit_peer_member").fetchone()[0])

        print("\n== 2. the guard notices that the prices arrived late ==")
        fresh_before = M.peer_set_staleness(conn)
        check("a date built against the same store reads ok",
              fresh_before["fresh"] and fresh_before["n_ok"] == 2, fresh_before)
        n_listed = add_listings_and_bars(conn, ids)
        check("listings arrived after the cohorts", n_listed == 16, n_listed)
        stale = M.peer_set_staleness(conn)
        check("every date is now stale", stale["n_stale"] == 2, stale["n_stale"])
        check("staleness names the listing counts, not just a verdict",
              stale["detail"][0]["n_listings_at_build"] == 0
              and stale["detail"][0]["n_listings_now"] == n_listed,
              stale["detail"][:1])
        check("assert_peer_sets_fresh refuses a stale store",
              _raises(lambda: M.assert_peer_sets_fresh(conn), RuntimeError))

        print("\n== 3. a dry run measures and writes nothing ==")
        dry = M.rebuild_peer_set_metadata(conn, dry_run=True)
        check("dry run reports the correction it would make",
              dry["n_priced_after"] == 2 * sum(p for _s, _n, p in COHORTS),
              dry["n_priced_after"])
        check("dry run wrote nothing", _sum(conn, "n_price_resolvable") == 0
              and _count(conn, "pit_peer_set_meta_history") == 0)

        print("\n== 4. the rebuild, and what it may not disturb ==")
        report = M.rebuild_peer_set_metadata(conn)
        check("status is rebuilt", report["status"] == M.STATUS_REBUILT,
              report["status"])
        check("every cohort was corrected", report["n_changed"] == n_cohorts,
              report["n_changed"])
        check("no checkpoint came back busy", report["n_checkpoints_busy"] == 0,
              report["checkpoints"])
        by_sic = {(r["key"], r["as_of_date"]): (r["n_members"], r["n_price_resolvable"])
                  for r in conn.execute(
                      "SELECT key, as_of_date, n_members, n_price_resolvable "
                      "FROM pit_peer_set")}
        check("the big cohort reads 12 priced of 20", by_sic[("2834", EARLY)] == (20, 12),
              by_sic.get(("2834", EARLY)))
        check("the small cohort reads 4 priced of 14", by_sic[("7372", LATE)] == (14, 4),
              by_sic.get(("7372", LATE)))

        verified = M.verify_metadata(conn, expected_member_rows=members_before,
                                     expected_member_entities=entities_before)
        for entry in verified["checks"]:
            check("invariant: " + entry["check"], entry["ok"], entry)
        check("MEMBERSHIP IS UNCHANGED: not one row added or removed",
              _count(conn, "pit_peer_member") == members_before, members_before)
        check("MEMBERSHIP IS UNCHANGED: no entity dropped for lack of a price",
              int(conn.execute("SELECT COUNT(DISTINCT entity_id) "
                               "FROM pit_peer_member").fetchone()[0]) == entities_before)
        check("no cohort fell below MIN_COHORT_N",
              report["n_below_min_cohort"] == 0 and P.MIN_COHORT_N == 12)
        check("no stored n_members disagreed with its rows",
              report["n_members_disagreed"] == 0)

        print("\n== 5. the record the rewrite would otherwise have destroyed ==")
        history = conn.execute(
            "SELECT * FROM pit_peer_set_meta_history ORDER BY peer_set_id").fetchall()
        check("one history row per rewritten cohort", len(history) == n_cohorts,
              len(history))
        check("the stale value is preserved, not merely overwritten",
              all(r["n_price_resolvable_before"] == 0 for r in history))
        check("the corrected value is recorded beside it",
              sum(r["n_price_resolvable_after"] for r in history)
              == 2 * sum(p for _s, _n, p in COHORTS))
        check("the reason names the race",
              {r["reason"] for r in history} == {M.REASON_LISTING_RACE})
        check("the price test is stamped on every history row",
              {r["price_test"] for r in history} == {M.PRICE_TEST_VERSION})

        print("\n== 6. the guard is satisfied only once the counts are current ==")
        after = M.peer_set_staleness(conn)
        check("no date is stale", after["n_stale"] == 0, after["stale_dates"])
        check("no date is unrecorded", after["n_unrecorded"] == 0,
              after["unrecorded_dates"])
        check("assert_peer_sets_fresh now passes", M.assert_peer_sets_fresh(conn)["fresh"])
        with pit_store.transaction(conn):
            conn.execute("DELETE FROM pit_peer_set_build WHERE as_of_date = ?", (EARLY,))
        missing = M.peer_set_staleness(conn)
        check("a date with NO provenance is unrecorded, never ok",
              missing["n_unrecorded"] == 1 and not missing["fresh"], missing)
        check("assert refuses on absent provenance too",
              _raises(lambda: M.assert_peer_sets_fresh(conn), RuntimeError))
        M.rebuild_peer_set_metadata(conn, dates=[EARLY], reason=M.REASON_REMEASURE)
        check("re-measuring restores the provenance", M.peer_set_staleness(conn)["fresh"])
        check("a re-measure that changes nothing writes no history",
              _count(conn, "pit_peer_set_meta_history") == n_cohorts,
              _count(conn, "pit_peer_set_meta_history"))

        print("\n== 7. priced coverage, the number the stale column hid ==")
        coverage = M.coverage_report(conn)
        check("only the sic4 rung was used", list(coverage["by_rung"]) == ["sic4"],
              list(coverage["by_rung"]))
        sic4 = coverage["by_rung"]["sic4"]
        check("slot-weighted coverage is 32 priced of 68 slots",
              (sic4["n_priced_slots"], sic4["n_member_slots"]) == (32, 68),
              (sic4["n_priced_slots"], sic4["n_member_slots"]))
        check("slot-weighted and cohort-median coverage are reported separately",
              sic4["slot_weighted_pct"] == 47.06
              and sic4["cohort_median_pct"] == 44.29,
              (sic4["slot_weighted_pct"], sic4["cohort_median_pct"]))
        check("the year grouping covers the fixture's single year",
              list(coverage["by_year"]) == ["2020"], list(coverage["by_year"]))
        check("no cohort is reported as having zero priced members",
              sic4["n_cohorts_zero_priced"] == 0)

        print("\n== 8. the refusal that protects a computed percentile ==")
        with pit_store.transaction(conn):
            conn.execute(
                """INSERT INTO pit_replay_run (started_at, as_of_grid, model_version,
                        latency_policy_version, ladder_version, peer_set_version)
                   VALUES ('2026-01-01', 'month_end', 'm', 'l', 'ld', 'ps')""")
            run_id = conn.execute("SELECT MAX(run_id) FROM pit_replay_run").fetchone()[0]
            peer_set_id = conn.execute(
                "SELECT MIN(peer_set_id) FROM pit_peer_set").fetchone()[0]
            entity_id = conn.execute(
                "SELECT MIN(entity_id) FROM pit_entity").fetchone()[0]
            conn.execute(
                """INSERT INTO pit_feature (entity_id, as_of_date, feature_key,
                        sources_json, feature_available_date, latency_policy_version,
                        peer_set_id, availability, model_version, replay_run_id,
                        created_at)
                   VALUES (?, ?, 'revenue', '[]', ?, 'l', ?, 'complete',
                           'equity_shaffer_v1_pit', ?, '2026-01-01')""",
                (entity_id, EARLY, EARLY, peer_set_id, run_id))
        refused = M.rebuild_peer_set_metadata(conn)
        check("a referenced cohort's denominator may not be rewritten",
              refused["status"] == M.STATUS_REFUSED
              and refused["n_features"] == 1, refused)
        check("the refusal matches delete_peer_sets' rule",
              P.delete_peer_sets(conn, EARLY,
                                 pit_store.EQUITY_PIT_MODEL_VERSION)["status"]
              == "refused")

        print("\n== 9. house rules ==")
        check("no production scoring module was imported",
              not {"company_scoring", "sector_scoring", "asset_models", "prediction",
                   "hedging", "streamlit"} & set(sys.modules),
              sorted({"company_scoring", "sector_scoring", "asset_models",
                      "prediction", "hedging", "streamlit"} & set(sys.modules)))
        check("no numpy, no pandas", not {"numpy", "pandas"} & set(sys.modules))
        check("the peer-set version is the store's, not a second spelling",
              M.rebuild_peer_set_metadata.__defaults__ is None
              and P.PEER_SET_VERSION is pit_store.PEER_SET_VERSION)
        check("the priced count is defined by the scored universe, nothing else",
              M.price_test_params()["function"]
              == "pit_identity.scored_universe_as_of")
        check("every priced count is stamped SURVIVOR_ONLY_DIAGNOSTIC",
              M.price_test_params()["price_sample"] == M.SURVIVOR_ONLY_DIAGNOSTIC
              == "SURVIVOR_ONLY_DIAGNOSTIC")
        check("and the label is STORED, not merely reported",
              all(pit_store.loads(r[0])["price_sample"] == M.SURVIVOR_ONLY_DIAGNOSTIC
                  for r in conn.execute(
                      "SELECT price_test_params_json FROM pit_peer_set_build")))

        conn.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("FAILURES:", len(fails), fails if fails else "")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
