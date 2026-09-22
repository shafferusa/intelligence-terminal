"""Offline tests for the point-in-time peer-set builder.

Pure stdlib, no network, a temporary store:   python test_pit_peers.py

THE ACCEPTANCE TESTS are sections 3 and 5. Section 3 is the one that catches the
mistake this module exists to prevent: an entity whose SIC changed must be
cohorted by the SIC IT CARRIED ON THE AS-OF DATE, not by the one it carries now.
The fixture entity files as a pharmaceutical in 2014 and as a software company
in 2019; a builder that reads the current record puts it in software at BOTH
dates and every percentile it touches is silently wrong. Section 5 is the floor:
a cohort of eleven is refused outright, never quietly scored.

The fixture is built so each rung of the ladder is exercised by a real cohort:

    2834 (15) 7372 (15)          -> resolve at sic4
    2842  (3) + 2844 (10)        -> 2842 falls through to sic3 '284'
    3011  (3) + 3080 (10)        -> 3011 falls through to sic2 '30'
    8880  (2) + 8888 (1) + 9721 (10)
                                 -> all fall through to the SEC office rung
    6189  (1)                    -> nothing works; unscoreable, and reported
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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

EARLY = "2015-06-30"
LATE = "2019-06-28"

#: (cik suffix, sic, filed) rows. Every entity files once inside the 400-day
#: window before EACH as-of date, because peer membership is "filed recently",
#: not "exists".
COHORTS = [
    ("2834", 15), ("7372", 15),
    ("2842", 3), ("2844", 10),
    ("3011", 3), ("3080", 10),
    ("8880", 2), ("8888", 1), ("9721", 10),
    ("6189", 1),
]

#: The entity whose classification changes: pharma in 2014, software in 2019.
MOVER_CIK = "9900001"


def build_fixture(db_path: str) -> tuple[sqlite3.Connection, dict[str, int]]:
    conn = P.prepare_connection(pit_store.init_db(db_path))
    ids: dict[str, int] = {}
    counter = 0
    for sic, n in COHORTS:
        for i in range(n):
            counter += 1
            cik = f"10{counter:05d}"
            entity_id = pit_store.upsert_entity(conn, cik)
            ids[f"{sic}:{i}"] = entity_id
            for filed, accn in (("2014-06-01", f"{cik}-14"), ("2019-06-01", f"{cik}-19")):
                pit_store.add_entity_sic(conn, entity_id, sic, filed, accn, "test")
    # The mover: pharmaceutical preparations in 2014, prepackaged software in 2019.
    mover = pit_store.upsert_entity(conn, MOVER_CIK)
    ids["mover"] = mover
    pit_store.add_entity_sic(conn, mover, "2834", "2014-06-01", "mover-14", "test")
    pit_store.add_entity_sic(conn, mover, "7372", "2019-06-01", "mover-19", "test")

    # A company that files in 2014 and then goes dark: a peer at the 2015 date
    # and, 400 days of staleness later, not a peer at the 2019 one -- while its
    # cohort carries on existing without it.
    dark = pit_store.upsert_entity(conn, "9900002")
    ids["dark"] = dark
    pit_store.add_entity_sic(conn, dark, "7372", "2014-06-01", "dark-14", "test")

    # A quarantined entity: its end cannot be dated, and it stays a peer anyway.
    quarantined = ids["2834:0"]
    pit_store.set_entity_exit(conn, quarantined, no_exit_record=1,
                              notes="test: undatable exit")

    # Exactly one entity is price-resolvable, so n_members and
    # n_price_resolvable are forced to differ on a real cohort row.
    priced = ids["2834:1"]
    listing_id = pit_store.upsert_listing(
        conn, "PRICED", "2010-01-04", entity_id=priced, exchange="NYSE",
        instrument_type="EQUITY", currency="USD", valid_from="2010-01-04",
        confidence="proved", source="test")
    with pit_store.transaction(conn):
        conn.executemany(
            """INSERT OR IGNORE INTO pit_price_bar
                   (listing_id, bar_date, open, high, low, close, adjclose,
                    volume, source_symbol, ingest_id)
               VALUES (?, ?, 10, 10, 10, 10, 10, 1000, 'PRICED', 1)""",
            [(listing_id, EARLY), (listing_id, LATE)])
    return conn, ids


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

def main() -> int:
    tmp = tempfile.mkdtemp(prefix="pit_peers_test_")
    db_path = os.path.join(tmp, "peers.db")
    try:
        conn, ids = build_fixture(db_path)

        print("== 1. the crosswalk itself ==")
        check("every SIC maps to exactly one SEC office",
              len(P.SIC_OFFICE) == sum(len(set(v)) for v in P.OFFICE_SIC_CODES.values()),
              (len(P.SIC_OFFICE), sum(len(set(v)) for v in P.OFFICE_SIC_CODES.values())))
        check("every crosswalk code is a 4-character SIC",
              all(len(c) == 4 and c.isdigit() for c in P.SIC_OFFICE))
        check("a duplicated code is refused rather than resolved by dict order",
              _raises(lambda: P._invert_offices({"A": ("1311",), "B": ("1311",)}),
                      ValueError))
        check("the keys differ in shape by rung, so they cannot be confused",
              (P.cohort_key("sic4", "2834"), P.cohort_key("sic3", "2834"),
               P.cohort_key("sic2", "2834"), P.cohort_key("office", "2834"))
              == ("2834", "283", "28", "Office of Life Sciences"))
        check("a zero-stripped SIC normalises to the stored 4-character spelling",
              P.normalize_sic(100) == "0100" and P.normalize_sic("0100") == "0100")

        print()
        print("== 2. the crosswalk hash is a real hash of the real mapping ==")
        base = P.crosswalk_hash()
        moved = dict(P.SIC_OFFICE)
        moved["2834"] = "Office of Technology"
        check("moving one SIC to another office changes the hash",
              P.crosswalk_hash(moved) != base, (base, P.crosswalk_hash(moved)))
        check("reordering the rung ladder changes the hash",
              P.crosswalk_hash(rungs=("sic2", "sic3", "sic4", "office")) != base)
        check("moving MIN_COHORT_N changes the hash",
              P.crosswalk_hash(min_cohort_n=13) != base)
        check("moving LOW_CONFIDENCE_N changes the hash",
              P.crosswalk_hash(low_confidence_n=30) != base)
        check("the same mapping hashes the same twice (no dict-order dependence)",
              P.crosswalk_hash(dict(reversed(list(P.SIC_OFFICE.items())))) == base)
        check("it is a sha256, not a placeholder",
              base.startswith("sha256:") and len(base) == 23, base)

        print()
        print("== 3. ACCEPTANCE: classification is point-in-time ==")
        early = P.build_peer_sets(conn, EARLY)
        late = P.build_peer_sets(conn, LATE)
        check("both dates built", early["status"] == P.STATUS_WRITTEN
              and late["status"] == P.STATUS_WRITTEN, (early["status"], late["status"]))
        check("sic_as_of returns the 2014 filing at the 2015 as-of",
              P.classification_as_of(conn, ids["mover"], EARLY) == "2834")
        check("sic_as_of returns the 2019 filing at the 2019 as-of",
              P.classification_as_of(conn, ids["mover"], LATE) == "7372")
        mover_early = P.peer_set_for_entity(conn, ids["mover"], EARLY)
        mover_late = P.peer_set_for_entity(conn, ids["mover"], LATE)
        check("the mover is cohorted as pharma at the 2015 date",
              (mover_early["rung"], mover_early["key"]) == ("sic4", "2834"),
              (mover_early["rung"], mover_early["key"]))
        check("THE CURRENT SIC IS NOT USED: it is not in 7372 at the 2015 date",
              mover_early["key"] != "7372")
        check("the mover is cohorted as software at the 2019 date",
              (mover_late["rung"], mover_late["key"]) == ("sic4", "7372"),
              (mover_late["rung"], mover_late["key"]))
        check("its two cohorts are different peer sets, not one relabelled",
              mover_early["peer_set_id"] != mover_late["peer_set_id"])

        print()
        print("== 4. the rung ladder widens, and stops the moment it is usable ==")
        at_sic4 = P.peer_set_for_entity(conn, ids["2834:2"], EARLY)
        at_sic3 = P.peer_set_for_entity(conn, ids["2842:0"], EARLY)
        at_sic2 = P.peer_set_for_entity(conn, ids["3011:0"], EARLY)
        at_office = P.peer_set_for_entity(conn, ids["8880:0"], EARLY)
        check("a 16-member 4-digit cohort resolves at sic4",
              (at_sic4["rung"], at_sic4["n_members"]) == ("sic4", 16),
              (at_sic4["rung"], at_sic4["n_members"]))
        check("a 3-member 4-digit cohort widens to sic3 and finds 13",
              (at_sic3["rung"], at_sic3["key"], at_sic3["n_members"])
              == ("sic3", "284", 13), at_sic3)
        check("a 3-member 3-digit cohort widens to sic2 and finds 13",
              (at_sic2["rung"], at_sic2["key"], at_sic2["n_members"])
              == ("sic2", "30", 13), at_sic2)
        check("a thin 2-digit cohort falls all the way to the SEC office",
              (at_office["rung"], at_office["key"], at_office["n_members"])
              == ("office", "Office of International Corp Fin", 13), at_office)
        check("the wider cohort contains the companies that resolved at sic4 too",
              ids["2844:0"] in P.peer_members(conn, at_sic3["peer_set_id"]))
        check("the rung actually used is recorded on the row, per cohort",
              {r["rung"] for r in conn.execute(
                  "SELECT DISTINCT rung FROM pit_peer_set WHERE as_of_date = ?",
                  (EARLY,))} == {"sic4", "sic3", "sic2", "office"})
        check("confidence is derived from size: 12-24 is low, 25+ is normal",
              (P.cohort_confidence(11), P.cohort_confidence(12),
               P.cohort_confidence(24), P.cohort_confidence(25))
              == ("unavailable", "low", "low", "normal"))

        print()
        print("== 5. ACCEPTANCE: a cohort below 12 is refused, not scored ==")
        lonely = P.peer_set_for_entity(conn, ids["6189:0"], EARLY)
        check("the unscoreable entity gets no peer set at all",
              lonely["peer_set_id"] is None, lonely)
        check("and it is UNAVAILABLE with reason 'insufficient_peers'",
              (lonely["availability"], lonely["reason"])
              == (pit_store.AVAIL_UNAVAILABLE, "insufficient_peers"), lonely)
        check("no persisted cohort is below the floor",
              conn.execute("SELECT COUNT(*) FROM pit_peer_set WHERE n_members < ?",
                           (P.MIN_COHORT_N,)).fetchone()[0] == 0)
        check("the build reports it as a measured number, not an exception",
              early["n_unusable"] == 1 and early["unusable_pct"] is not None,
              (early["n_unusable"], early["unusable_pct"]))
        check("an 11-member group is refused where a 12-member one is not",
              P.assign_rung({"sic4": {"1311": list(range(11))}}, "1311",
                            ("sic4",))[0] is None
              and P.assign_rung({"sic4": {"1311": list(range(12))}}, "1311",
                                ("sic4",))[0] == "sic4")
        dark_early = P.peer_set_for_entity(conn, ids["dark"], EARLY)
        dark_late = P.peer_set_for_entity(conn, ids["dark"], LATE)
        check("a company that went dark is a peer while it was still filing",
              dark_early["peer_set_id"] is not None and dark_early["key"] == "7372",
              dark_early)
        check("and afterwards is 'not_in_peer_universe', NOT 'insufficient_peers'",
              (dark_late["peer_set_id"], dark_late["reason"])
              == (None, P.REASON_NOT_A_PEER), dark_late)
        check("its cohort carries on existing without it",
              P.peer_set_for_entity(conn, ids["7372:0"], LATE)["n_members"] >= 12)

        print()
        print("== 6. a peer needs no listing and no price ==")
        cohort_2834 = P.peer_set_for_entity(conn, ids["2834:2"], EARLY)
        members = P.peer_members(conn, cohort_2834["peer_set_id"])
        check("an entity with no listing at all is still a member",
              ids["2834:3"] in members
              and conn.execute("SELECT COUNT(*) FROM pit_listing WHERE entity_id = ?",
                               (ids["2834:3"],)).fetchone()[0] == 0)
        check("a QUARANTINED entity is still a member (dropping it would be bias)",
              ids["2834:0"] in members)
        check("n_members counts the cohort that shapes the percentile",
              cohort_2834["n_members"] == 16, cohort_2834["n_members"])
        check("n_price_resolvable is recorded SEPARATELY and is smaller",
              cohort_2834["n_price_resolvable"] == 1, cohort_2834)
        check("the quarantined name is a peer but is not price-resolvable",
              ids["2834:0"] not in {
                  int(r["entity_id"]) for r in conn.execute(
                      "SELECT entity_id FROM pit_listing WHERE entity_id IS NOT NULL")})

        print()
        print("== 7. re-running a date is idempotent ==")
        before = conn.execute("SELECT COUNT(*) FROM pit_peer_member").fetchone()[0]
        n_sets_before = conn.execute("SELECT COUNT(*) FROM pit_peer_set").fetchone()[0]
        again = P.build_peer_sets(conn, EARLY)
        after = conn.execute("SELECT COUNT(*) FROM pit_peer_member").fetchone()[0]
        n_sets_after = conn.execute("SELECT COUNT(*) FROM pit_peer_set").fetchone()[0]
        check("the second run reports 'exists'", again["status"] == P.STATUS_EXISTS,
              again["status"])
        check("it writes no membership rows", before == after, (before, after))
        check("it writes no peer-set rows", n_sets_before == n_sets_after,
              (n_sets_before, n_sets_after))
        check("it does not even open the universe (no work done)",
              "n_universe" not in again)
        measured = P.build_peer_sets(conn, EARLY, persist=False)
        check("a measurement run re-measures a built date (the skip is for writes)",
              measured["status"] == "measured"
              and measured["n_classified"] == early["n_classified"], measured["status"])
        check("and still writes nothing",
              conn.execute("SELECT COUNT(*) FROM pit_peer_member").fetchone()[0] == after)
        check("one crosswalk hash across the whole store",
              conn.execute("SELECT COUNT(DISTINCT crosswalk_hash) FROM pit_peer_set"
                           ).fetchone()[0] == 1)

        print()
        print("== 8. a changed mapping refuses rather than mixing ==")
        conflict = P.build_peer_sets(conn, EARLY, crosswalk=moved)
        check("a build under a different crosswalk is refused on a built date",
              conflict["status"] == P.STATUS_CROSSWALK_CONFLICT, conflict["status"])
        check("and it names both hashes so the operator can decide",
              conflict["crosswalk_hash"] != base and conflict["stored_hashes"] == [base],
              conflict)
        check("nothing was written by the refusal",
              conn.execute("SELECT COUNT(*) FROM pit_peer_member").fetchone()[0] == after)

        print()
        print("== 9. rebuild is possible, but never under a live feature ==")
        run_id = pit_store.start_replay_run(conn, "test_model", pit_store.GRID_MONTH_END)
        set_id = cohort_2834["peer_set_id"]
        pit_store.insert_features(conn, [(
            ids["2834:2"], None, EARLY, "test_key", "production", "[]", None, None, 0,
            None, EARLY, pit_store.LATENCY_POLICY_VERSION, 1.0, 0.5, "percentile",
            set_id, pit_store.AVAIL_COMPLETE, 1, None, "test_model", run_id,
            pit_store._now())])
        conn.commit()
        refused = P.delete_peer_sets(conn, EARLY, pit_store.EQUITY_PIT_MODEL_VERSION)
        check("deleting a cohort a feature was normalised against is refused",
              refused["status"] == "refused", refused)
        check("the cohort is still there",
              P.peer_set_for_entity(conn, ids["2834:2"], EARLY)["peer_set_id"] == set_id)

        print()
        print("== 10. the disk and WAL guards are real ==")
        status = P.disk_status(db_path)
        check("disk_status reports free bytes and a WAL size",
              status["free_bytes"] > 0 and status["wal_bytes"] >= 0, status)
        check("require_free_space aborts loudly when the volume is too full",
              _raises(lambda: P.require_free_space(db_path, 10 ** 18), RuntimeError))
        check("and the message says how much was free and how much was needed",
              "REFUSING TO WRITE" in _message(
                  lambda: P.require_free_space(db_path, 10 ** 18)))
        checkpoint = P.checkpoint_wal(conn, db_path)
        check("checkpoint_wal READS the return row rather than discarding it",
              set(checkpoint) >= {"busy", "log_frames", "checkpointed_frames",
                                  "effective"}, checkpoint)
        check("busy is reported as an integer, which is how a no-op is visible",
              checkpoint["busy"] in (0, 1), checkpoint)
        check("a build refuses to start when the free-space floor is not met",
              _raises(lambda: P.build_peer_sets(conn, "2016-06-30",
                                                need_free_bytes=10 ** 18),
                      RuntimeError))

        print()
        print("== 11. measurement is produced, not estimated ==")
        check("every rung carries a size distribution",
              all(k in early["per_rung"]["sic4"]
                  for k in ("n_groups", "median", "usable_at_rung_pct",
                            "assigned_at_rung")), early["per_rung"]["sic4"])
        assigned = sum(early["per_rung"][r]["assigned_at_rung"] for r in P.RUNG_LADDER)
        check("assignments plus unusables account for the whole universe",
              assigned + early["n_unusable"] == early["n_classified"],
              (assigned, early["n_unusable"], early["n_classified"]))
        check("the priced subset is reported beside the peer universe",
              early["n_price_resolvable_universe"] == 1, early)
        check("the two SIC resolvers agree on the sampled rows",
              early["sic_as_of_disagreements"] == 0, early)
        coverage = P.peer_set_coverage(conn)
        check("coverage reports two dates and one crosswalk",
              coverage["n_dates"] == 2 and coverage["n_distinct_crosswalk_hashes"] == 1,
              coverage)

        print()
        print("== 12. the frozen production core is untouched ==")
        check("importing pit_peers pulls in no production scoring module",
              not {"company_scoring", "sector_scoring", "asset_models", "prediction",
                   "hedging", "streamlit"} & set(sys.modules),
              sorted({"company_scoring", "sector_scoring", "asset_models",
                      "prediction", "hedging", "streamlit"} & set(sys.modules)))
        check("no numpy, no pandas",
              not {"numpy", "pandas"} & set(sys.modules))
        check("the peer-set version is the store's, not a second spelling",
              P.PEER_SET_VERSION is pit_store.PEER_SET_VERSION)
        check("the refusal reason is the store's constant",
              P.REASON_NO_PEERS == pit_store.REASON_NO_PEERS == "insufficient_peers")

        conn.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("FAILURES:", len(fails), fails if fails else "")
    return 1 if fails else 0


def _message(thunk) -> str:
    try:
        thunk()
    except Exception as exc:
        return str(exc)
    return ""


if __name__ == "__main__":
    sys.exit(main())
