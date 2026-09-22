"""Offline tests for label-policy versioning, the guards, and cost assumptions.

Pure stdlib, no network, a temporary store:   python test_pit_labelset.py

WHAT THESE TESTS ARE FOR. `pit_label`'s natural key is (listing_id,
as_of_date, horizon) and carried no version at all, so a second labelling
policy would have upserted over the first and nothing would have recorded that
it had. The version could NOT be added to that key -- it is an inline UNIQUE,
SQLite cannot drop the automatic index behind one, and the rebuild that would
replace it is unaffordable on this volume. So the protection is a pair of
triggers, and a trigger nobody tests is a comment.

The acceptance tests are sections 2 and 3:

  * section 2 fires the actual failure -- a second policy writing over a first
    row -- through BOTH writers (`pit_store.save_label` and the bulk
    `pit_labels.insert_labels` upsert) and requires both to raise. A guard that
    only holds on the slow path is not a guard, because the bulk path is the
    one that writes 2.3 million rows;
  * section 3 requires the backfill to be resumable and to stamp only what is
    unstamped, because the real backfill ran against a live 8 GiB store where
    stopping halfway is a normal outcome, not an exceptional one.

The fixture is a real store built by `pit_store.init_db`, with labels written
through the same functions the production build uses.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_labels as L
import pit_labelset as S
import pit_store

fails: list[str] = []


def check(name: str, cond: bool, extra: object = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {extra}")
        fails.append(name)


def _raises(thunk, exc=Exception) -> bool:
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

DATES = ("2020-01-31", "2020-02-28", "2020-03-31")
HORIZONS = ("1M", "12M")
N_LISTINGS = 7


def fixture() -> tuple[sqlite3.Connection, str, str]:
    """A store with unversioned labels, exactly as the real one was found."""
    directory = tempfile.mkdtemp(prefix="pit_labelset_")
    path = os.path.join(directory, "labels.db")
    conn = pit_store.init_db(path)
    conn.execute("PRAGMA foreign_keys = OFF")
    rows = []
    for listing_id in range(1, N_LISTINGS + 1):
        for as_of in DATES:
            for horizon in HORIZONS:
                rows.append((listing_id, None, as_of, horizon, as_of, 100.0, 0,
                             as_of, 110.0, 0, 0.10, None, None, None, None, 0,
                             L.REASON_COMPLETE, L.PRICE_SOURCE, 1, "now",
                             None, None, None))
    # The INSERT trigger refuses a NULL policy, which is the point -- so the
    # unversioned starting state is created by writing versioned rows and then
    # clearing the column, which is what the real store's rows look like: they
    # were written before the column existed at all.
    stamped = [r[:20] + (pit_store.LABEL_POLICY_VERSION, None, None) for r in rows]
    L.insert_labels(conn, stamped)
    conn.execute("UPDATE pit_label SET label_policy_version = NULL")
    conn.commit()
    return conn, path, directory


def cleanup(conn: sqlite3.Connection, directory: str) -> None:
    conn.close()
    shutil.rmtree(directory, ignore_errors=True)


# --------------------------------------------------------------------------
# 1. Registration and the code/registry agreement
# --------------------------------------------------------------------------

def test_registration() -> None:
    print("\n1. the policy registry")
    conn, path, directory = fixture()
    print("   " + S.ensure_schema(conn))

    check("a label set is not registered until it is registered",
          S.label_set(conn, S.LABEL_POLICY_V1) is None)
    check("assert_policy_matches_code refuses an unregistered policy",
          _raises(lambda: S.assert_policy_matches_code(conn), RuntimeError))

    check("registering returns 'registered'",
          S.register_label_set(conn) == "registered")
    check("and is idempotent",
          S.register_label_set(conn) == "already_registered")

    row = S.label_set(conn, S.LABEL_POLICY_V1)
    check("the set records SURVIVOR_ONLY_DIAGNOSTIC",
          row["sample_scope"] == pit_store.SAMPLE_SURVIVOR_ONLY,
          row["sample_scope"])
    check("and refuses to let a model be promoted from it",
          int(row["may_promote_a_model"]) == 0)
    check("the survivorship note names the direction of the excess-return bias",
          "UPWARD" in row["sample_scope_note"])
    check("the version string itself carries the sample scope",
          "survivor_only" in row["label_policy_version"])

    check("the anchor rule states the measured roll cap",
          str(L.MAX_ANCHOR_ROLL_SESSIONS) in row["anchor_rule"], row["anchor_rule"])
    check("the terminal rule states its own, larger cap",
          str(L.MAX_TERMINAL_ROLL_SESSIONS) in row["terminal_rule"])
    check("the censoring rule states the invariant it rests on",
          "forward_return IS NULL if and only if censored = 1" in row["censoring_rule"])
    check("OUTCOME_UNKNOWN is described as a result, not an absence",
          pit_store.OUTCOME_UNKNOWN in row["outcome_unknown_rule"]
          and "-100" in row["outcome_unknown_rule"])
    check("horizons are stored in sessions, with the session counts",
          row["horizon_unit"] == "trading_sessions"
          and json.loads(row["horizons_json"])["12M"] == L.HORIZON_SESSIONS["12M"])
    check("the price source and its version are both recorded",
          row["price_source"] == L.PRICE_SOURCE
          and row["price_source_version"] != "")
    check("the corporate-action policy names every terminal event type",
          all(t in row["corporate_action_policy"] for t in L.TERMINAL_EVENT_TYPES))

    check("a registered policy that matches the code passes the assert",
          S.assert_policy_matches_code(conn)["drift"] == [])

    cleanup(conn, directory)


def test_registry_is_immutable() -> None:
    print("\n2. the registry cannot be edited into agreement")
    conn, path, directory = fixture()
    S.ensure_schema(conn)
    S.register_label_set(conn)

    check("UPDATE on pit_label_set is refused outright",
          _raises(lambda: conn.execute(
              "UPDATE pit_label_set SET anchor_rule = 'whatever'"),
              sqlite3.IntegrityError))
    conn.rollback()
    check("DELETE on pit_label_set is refused outright",
          _raises(lambda: conn.execute("DELETE FROM pit_label_set"),
                  sqlite3.IntegrityError))
    conn.rollback()

    # Registering a DIFFERENT description under the same version must not be
    # silently accepted: it is a conflict, reported by the column that differs.
    spec = S.describe_policy_v1()
    spec["anchor_rule"] = "a completely different anchor rule"
    check("re-registering a changed policy under the same version conflicts",
          S.register_label_set(conn, spec) == "conflict:anchor_rule")

    stored = S.label_set(conn, S.LABEL_POLICY_V1)
    check("and the stored row is untouched by the attempt",
          str(L.MAX_ANCHOR_ROLL_SESSIONS) in stored["anchor_rule"])
    cleanup(conn, directory)


# --------------------------------------------------------------------------
# 2. THE ACCEPTANCE TEST: a second policy cannot overwrite the first
# --------------------------------------------------------------------------

def test_second_policy_is_refused() -> None:
    print("\n3. a second policy is REFUSED, not silently written over the first")
    conn, path, directory = fixture()
    S.ensure_schema(conn)
    S.register_label_set(conn)
    conn.execute("UPDATE pit_label SET label_policy_version = ?",
                 (S.LABEL_POLICY_V1,))
    conn.commit()

    before = conn.execute(
        "SELECT forward_return FROM pit_label WHERE listing_id = 1 "
        "AND as_of_date = ? AND horizon = '1M'", (DATES[0],)).fetchone()[0]

    def via_store() -> None:
        pit_store.save_label(
            conn, 1, DATES[0], "1M", forward_return=0.99, censored=0,
            price_source=L.PRICE_SOURCE,
            label_policy_version="label_v2_something_else")

    check("pit_store.save_label refuses to relabel under a second policy",
          _raises(via_store, sqlite3.IntegrityError))

    def via_bulk() -> None:
        L.insert_labels(conn, [(
            1, None, DATES[0], "1M", DATES[0], 100.0, 0, DATES[0], 199.0, 0,
            0.99, None, None, None, None, 0, L.REASON_COMPLETE, L.PRICE_SOURCE,
            1, "now", "label_v2_something_else", None, None)])

    check("and the BULK writer refuses it too -- the path that writes millions",
          _raises(via_bulk, sqlite3.IntegrityError))

    after = conn.execute(
        "SELECT forward_return, label_policy_version FROM pit_label "
        "WHERE listing_id = 1 AND as_of_date = ? AND horizon = '1M'",
        (DATES[0],)).fetchone()
    check("the original row survives both attempts unchanged",
          after[0] == before and after[1] == S.LABEL_POLICY_V1,
          (before, tuple(after)))

    check("an INSERT with no policy version at all is refused",
          _raises(lambda: conn.execute(
              "INSERT INTO pit_label (listing_id, as_of_date, horizon, "
              "price_source, computed_at) VALUES (99, '2021-01-29', '1M', 'x', 'n')"),
              sqlite3.IntegrityError))
    conn.rollback()

    check("re-running the SAME policy still refines the row, as a label must",
          _raises(lambda: pit_store.save_label(
              conn, 1, DATES[0], "1M", forward_return=0.11, censored=0,
              price_source=L.PRICE_SOURCE)) is False)
    check("and the refinement landed",
          abs(conn.execute(
              "SELECT forward_return FROM pit_label WHERE listing_id = 1 "
              "AND as_of_date = ? AND horizon = '1M'",
              (DATES[0],)).fetchone()[0] - 0.11) < 1e-12)
    cleanup(conn, directory)


# --------------------------------------------------------------------------
# 3. THE ACCEPTANCE TEST: the backfill stamps only what is unstamped
# --------------------------------------------------------------------------

def test_backfill() -> None:
    print("\n4. the backfill is resumable and touches nothing it should not")
    conn, path, directory = fixture()
    S.ensure_schema(conn)
    S.register_label_set(conn)

    total = N_LISTINGS * len(DATES) * len(HORIZONS)
    check("every row starts unstamped", S.unstamped_rows(conn) == total,
          S.unstamped_rows(conn))

    recon = S.reconstruct_original_run(conn)
    check("the original build gets a run row marked RECONSTRUCTED",
          recon["status"] == "reconstructed")
    run_row = conn.execute("SELECT * FROM pit_label_run WHERE label_run_id = ?",
                           (recon["label_run_id"],)).fetchone()
    check("and it says so in its kind, not only in prose",
          run_row["run_kind"] == S.RUN_RECONSTRUCTED)
    check("its evidence is derived from the rows themselves",
          recon["evidence"]["n_rows_at_reconstruction"] == total
          and recon["evidence"]["n_dates"] == len(DATES))
    check("reconstructing twice does not make a second run",
          S.reconstruct_original_run(conn)["status"] == "already_reconstructed"
          and conn.execute("SELECT COUNT(*) FROM pit_label_run").fetchone()[0] == 1)

    check("the backfill refuses to run without a run id",
          _raises(lambda: S.backfill_label_policy(conn, db_path=path), ValueError))

    # Stamp half the rows by hand, then let the backfill finish: this is the
    # resume case, and it is the normal one on an 8 GiB store.
    conn.execute("UPDATE pit_label SET label_policy_version = ?, label_run_id = ? "
                 "WHERE listing_id <= 3", (S.LABEL_POLICY_V1, recon["label_run_id"]))
    conn.commit()
    partial = S.unstamped_rows(conn)

    summary = S.backfill_label_policy(
        conn, db_path=path, label_run_id=recon["label_run_id"],
        batch_listings=2, checkpoint_every=1, min_free_gib=0.0, progress=False)
    check("the backfill stamps exactly the rows that were unstamped",
          summary["rows_stamped"] == partial, (summary["rows_stamped"], partial))
    check("and leaves none behind", S.unstamped_rows(conn) == 0)
    check("it batched rather than writing in one transaction",
          summary["n_batches"] >= 3, summary["n_batches"])
    check("every checkpoint's busy flag was read and counted",
          summary["checkpoints"] >= 1 and "checkpoints_busy" in summary)
    check("it reports the file growth it caused",
          "db_growth_mib" in summary and "wal_peak_mib" in summary)

    check("every row now names the run that stamped it",
          conn.execute("SELECT COUNT(*) FROM pit_label WHERE label_run_id IS NULL"
                       ).fetchone()[0] == 0)
    check("re-running the backfill stamps nothing",
          S.backfill_label_policy(
              conn, db_path=path, label_run_id=recon["label_run_id"],
              batch_listings=2, min_free_gib=0.0,
              progress=False)["rows_stamped"] == 0)

    coverage = S.policy_coverage(conn)
    check("coverage reports one policy over every row",
          len(coverage["by_policy"]) == 1
          and coverage["by_policy"][0]["n_rows"] == total)
    check("and no policy is in use without being registered",
          coverage["policies_used_but_unregistered"] == [])
    cleanup(conn, directory)


# --------------------------------------------------------------------------
# 4. Cost assumptions
# --------------------------------------------------------------------------

def test_costs() -> None:
    print("\n5. cost assumptions are documented, flagged, and reversible")
    conn, path, directory = fixture()
    S.ensure_schema(conn)

    status = S.seed_cost_assumptions(conn)
    check("both sets are seeded", set(status.values()) == {"seeded"}, status)
    check("and seeding is idempotent",
          set(S.seed_cost_assumptions(conn).values()) == {"already_seeded"})

    zero = S.cost_set(conn, S.COST_ZERO)
    flat = S.cost_set(conn, S.COST_FLAT_10BPS)
    check("the zero set costs exactly nothing",
          S.round_trip_fraction(zero) == 0.0)
    check("the flat set is 10 bps per side, so 20 bps round trip",
          abs(S.round_trip_fraction(flat) - 0.0020) < 1e-12,
          S.round_trip_fraction(flat))
    check("both are flagged as assumptions, not measurements",
          int(zero["is_assumption"]) == 1 and int(flat["is_assumption"]) == 1)
    check("the per-side convention is recorded, not left to be guessed",
          json.loads(flat["cost_convention_json"])["bps_are"] == "per_side")
    check("what is NOT priced is listed explicitly",
          "market_impact" in json.loads(flat["cost_convention_json"])["not_priced"])
    check("the note says which way the flat assumption is wrong",
          "small" in flat["note"] and "mega-cap" in flat["note"])
    check("and warns against annualising a per-window cost",
          "Do not annualise" in json.loads(flat["cost_convention_json"])
          ["per_window_not_per_annum"])

    gross = 0.25
    net = (1.0 + gross) * (1.0 - S.round_trip_fraction(flat)) - 1.0
    check("net is exactly reversible from gross and the stored fraction",
          abs(((1.0 + net) / (1.0 - S.round_trip_fraction(flat)) - 1.0) - gross) < 1e-12)
    check("and a 25% gross year survives the round trip as ~24.75%",
          abs(net - 0.2475) < 1e-9, net)
    cleanup(conn, directory)


# --------------------------------------------------------------------------
# 5. The key-rebuild price tag
# --------------------------------------------------------------------------

def test_rebuild_cost() -> None:
    print("\n6. the key change is priced rather than asserted")
    conn, path, directory = fixture()
    S.ensure_schema(conn)
    cost = S.label_key_rebuild_cost(conn, path, sample=50)

    check("it counts the real rows", cost["n_rows"] == N_LISTINGS * len(DATES) * len(HORIZONS))
    check("it reports bytes per row from a sample, not a constant",
          cost["bytes_per_row_estimated"] > 0 and cost["sampled_rows"] > 0)
    check("it reports the peak extra space a rebuild would hold at once",
          cost["peak_extra_mib"] >= cost["table_copy_mib"])
    check("it names the WAL exposure of a single-transaction rebuild",
          cost["wal_exposure_bytes"] > 0 and "checkpoint" in cost["wal_note"])
    check("it works out whether VACUUM could ever reclaim the leak",
          isinstance(cost["vacuum_possible"], bool)
          and cost["vacuum_needs_gib"] >= 2 * cost["db_gib"] - 0.01)
    check("and says the dropped pages go to the freelist, not the volume",
          "freelist" in cost["leak_note"])

    # The claim the whole design rests on: a wider UNIQUE INDEX does not help,
    # because the narrow autoindex still refuses the second policy's row.
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_try ON pit_label "
                 "(listing_id, as_of_date, horizon, label_policy_version)")
    conn.commit()
    conn.execute("UPDATE pit_label SET label_policy_version = ?", (S.LABEL_POLICY_V1,))
    conn.commit()
    conn.execute("DROP TRIGGER trg_pit_label_policy_immutable")
    conn.commit()

    def second_policy() -> None:
        with pit_store.transaction(conn):
            conn.execute(
                "INSERT INTO pit_label (listing_id, entity_id, as_of_date, horizon, "
                "price_source, computed_at, label_policy_version) "
                "VALUES (1, NULL, ?, '1M', 'x', 'n', 'label_v2')", (DATES[0],))

    check("a wider UNIQUE INDEX does NOT let two policies coexist -- "
          "the inline key's autoindex still refuses the row",
          _raises(second_policy, sqlite3.IntegrityError))
    cleanup(conn, directory)


def main() -> int:
    test_registration()
    test_registry_is_immutable()
    test_second_policy_is_refused()
    test_backfill()
    test_costs()
    test_rebuild_cost()
    print()
    if fails:
        print(f"{len(fails)} FAILED: {fails}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
