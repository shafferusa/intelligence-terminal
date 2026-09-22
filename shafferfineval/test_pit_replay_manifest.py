"""Offline tests for the replay manifest and its gate.

    python test_pit_replay_manifest.py

THE TEST THIS FILE EXISTS FOR is `perturbed freeze`. A gate that only ever
returns True proves nothing: it is indistinguishable from `return True, []`.
So the freeze is deliberately broken -- one frozen component is moved in
memory -- and the gate must REFUSE. Then it is restored and the gate must pass
again, because a test that leaves the specification broken would poison every
test after it and, worse, the next real run in this interpreter.

Nothing on disk is edited. The perturbation is a `setattr` on an already
imported module, undone in a `finally`, so `pit_factor_blocks.py` and friends
are byte-identical before and after and the live freeze digest
is unharmed.

Most checks run against FIXTURES -- in-memory SQLite databases built here. Two
run against the real store READ-ONLY, to check that the module's headline
promises (read-only; every replay table at zero) are true of the actual file
rather than of a convenient mock. They SKIP, loudly, if the store is absent:
an absent store is not a passing one.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pit_factor_blocks
import pit_frozen_spec
import pit_replay_manifest as M

fails: list[str] = []
skips: list[str] = []


def check(name: str, cond: bool, extra: object = "") -> None:
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s %s" % (name, extra))
        fails.append(name)


def skip(name: str, why: str) -> None:
    print("  SKIP  %s -- %s" % (name, why))
    skips.append(name)


# ==========================================================================
# FIXTURES
# ==========================================================================

def empty_store(extra_tables: tuple = (), rows: dict | None = None) -> sqlite3.Connection:
    """An in-memory store holding the replay tables, empty unless told otherwise."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for table, _role in M.REPLAY_TABLES:
        if table in extra_tables:
            continue          # deliberately ABSENT
        conn.execute("CREATE TABLE %s (x INTEGER)" % table)
    for table in M.ADJACENT_TABLES:
        conn.execute("CREATE TABLE %s (x INTEGER)" % table)
    for table, n in (rows or {}).items():
        for _ in range(n):
            conn.execute("INSERT INTO %s (x) VALUES (1)" % table)
    conn.commit()
    return conn


def fake_snap(**overrides):
    """A snapshot shaped like the real one, with everything passing by default."""
    snap = {
        "freeze": {
            "intact": True,
            "can_execute_replay": True,
            "can_execute_reasons": [],
            "missing_components": [],
            "drifted": [],
            "frozen_digest": "f" * 64,
            "current_digest": "f" * 64,
        },
        "disk": {"free_bytes": M.FREE_SPACE_GATE_FLOOR_BYTES + M.GIB},
        "replay_tables": {
            "all_replay_tables_at_zero": True,
            "not_proven_empty": [],
            "tables": {t: {"state": "EMPTY", "n_rows": 0, "proven_empty": True}
                       for t, _r in M.REPLAY_TABLES},
        },
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(snap.get(key), dict):
            snap[key] = {**snap[key], **value}
        else:
            snap[key] = value
    return snap


# ==========================================================================
# THE GATE UNDER A PERTURBED FREEZE  -- the reason this file exists
# ==========================================================================

def test_perturbed_freeze() -> None:
    print("\nGATE REFUSES A PERTURBED FREEZE")

    before = pit_frozen_spec.verify()
    check("the freeze is intact before the perturbation", before["intact"],
          before["current_digest"])
    check("the recorded digest is the LIVE freeze's digest (%s)"
          % pit_frozen_spec.SPEC_FREEZE_VERSION,
          before["frozen_digest"] == pit_frozen_spec.FROZEN_DIGEST,
          before["frozen_digest"])

    original = pit_factor_blocks.MIN_BLOCK_WEIGHT
    perturbed_verify = None
    try:
        # ONE component moves: the block-coverage floor, 0.40 -> 0.55. In
        # memory only. This is the smallest possible lie the specification can
        # tell -- a single float -- and it must still be caught.
        pit_factor_blocks.MIN_BLOCK_WEIGHT = 0.55

        perturbed_verify = pit_frozen_spec.verify()
        check("verify() sees the perturbation", not perturbed_verify["intact"],
              perturbed_verify["current_digest"])
        check("verify() names the component that moved",
              any(d["component"] == "pit_factor_blocks:MIN_BLOCK_WEIGHT"
                  for d in perturbed_verify["drifted"]),
              perturbed_verify["drifted"])

        ok, reasons = M.gate(fake_snap(freeze=M.freeze_state()))
        check("gate REFUSES with the freeze perturbed", not ok, reasons)
        check("the refusal says the freeze is not intact",
              any("FREEZE NOT INTACT" in r for r in reasons), reasons)
        check("the refusal names the model version that must not be claimed",
              any(pit_frozen_spec.FROZEN_MODEL_VERSION in r for r in reasons),
              reasons)

        # And the live gate, reading the real modules, must refuse too --
        # not merely the one handed a doctored dict.
        live_ok, live_reasons = M.gate()
        check("the LIVE gate refuses too", not live_ok, live_reasons)
    finally:
        pit_factor_blocks.MIN_BLOCK_WEIGHT = original

    after = pit_frozen_spec.verify()
    check("the freeze is restored", after["intact"], after["current_digest"])
    check("the restored digest equals the frozen digest",
          after["current_digest"] == after["frozen_digest"])
    check("the restoration is not a coincidence: the perturbed digest differed",
          perturbed_verify is not None
          and perturbed_verify["current_digest"] != after["current_digest"])

    ok, reasons = M.gate(fake_snap(freeze=M.freeze_state()))
    check("gate passes again once the freeze is restored", ok, reasons)


def test_perturbed_freeze_other_component() -> None:
    """A second, different component -- so the test is not floor-specific."""
    print("\nGATE REFUSES A PERTURBED WEIGHT TABLE")
    import pit_score_signature

    original = dict(pit_score_signature.V2_MAJOR_WEIGHTS)
    try:
        # The 2026-09-21 defect, re-enacted: the same numeric vector over
        # different labels. Only the weight TABLE moves.
        pit_score_signature.V2_MAJOR_WEIGHTS = {
            "valuation": 0.35, "ebitda_strength": 0.25,
            "real_growth": 0.15, "financial_quality": 0.10}
        ok, reasons = M.gate(fake_snap(freeze=M.freeze_state()))
        check("gate refuses when E and V swap weights", not ok, reasons)
        check("the numeric vector is unchanged -- so vector equality would "
              "have missed it",
              sorted(pit_score_signature.V2_MAJOR_WEIGHTS.values())
              == sorted(original.values()))
    finally:
        pit_score_signature.V2_MAJOR_WEIGHTS = original
    check("the weight table is restored",
          pit_frozen_spec.verify()["intact"])


# ==========================================================================
# THE OTHER THREE GATE CONDITIONS
# ==========================================================================

def test_gate_conditions() -> None:
    print("\nGATE CONDITIONS")

    ok, reasons = M.gate(fake_snap())
    check("a clean snapshot passes", ok, reasons)

    ok, reasons = M.gate(fake_snap(freeze={"can_execute_replay": False,
                                           "can_execute_reasons": ["a gap"]}))
    check("refuses when the specification is not executable", not ok, reasons)
    check("and says why", any("NOT EXECUTABLE" in r for r in reasons), reasons)

    ok, reasons = M.gate(fake_snap(replay_tables={
        "all_replay_tables_at_zero": False,
        "not_proven_empty": ["pit_score"],
        "tables": {"pit_score": {"state": "NONEMPTY", "n_rows": 17,
                                 "proven_empty": False}}}))
    check("refuses when a replay table already holds rows", not ok, reasons)
    check("and names the table and the count",
          any("pit_score=17" in r for r in reasons), reasons)

    ok, reasons = M.gate(fake_snap(replay_tables={
        "all_replay_tables_at_zero": False,
        "not_proven_empty": ["pit_feature"],
        "tables": {"pit_feature": {"state": "UNREADABLE", "n_rows": M.UNKNOWN,
                                   "proven_empty": False}}}))
    check("an UNREADABLE count is a refusal, not a pass", not ok, reasons)
    check("and says a zero it cannot read is not a zero",
          any("not a zero" in r for r in reasons), reasons)

    ok, reasons = M.gate(fake_snap(
        disk={"free_bytes": M.FREE_SPACE_GATE_FLOOR_BYTES - 1}))
    check("refuses one byte below the free-space floor", not ok, reasons)

    ok, reasons = M.gate(fake_snap(
        disk={"free_bytes": M.FREE_SPACE_GATE_FLOOR_BYTES}))
    check("the floor is exclusive: exactly at the floor is a refusal",
          not ok, reasons)

    ok, reasons = M.gate(fake_snap(disk={"free_bytes": M.UNKNOWN,
                                         "error": "no such volume"}))
    check("refuses when free space is UNKNOWN", not ok, reasons)
    check("and does not treat UNKNOWN as room",
          any("UNKNOWN" in r for r in reasons), reasons)

    ok, reasons = M.gate(fake_snap(freeze={
        "intact": True, "missing_components": ["pit_gone:THING"]}))
    check("refuses when a frozen component has gone missing", not ok, reasons)

    # Several at once: every reason is reported, not just the first.
    ok, reasons = M.gate(fake_snap(
        freeze={"intact": False, "drifted": [{"component": "x"}],
                "can_execute_replay": False, "can_execute_reasons": ["gap"]},
        disk={"free_bytes": 1}))
    check("all independent refusals are reported together",
          not ok and len(reasons) >= 3, reasons)


def test_gate_returns_a_pair() -> None:
    print("\nGATE SHAPE")
    result = M.gate(fake_snap())
    check("gate() returns a 2-tuple", isinstance(result, tuple) and len(result) == 2,
          result)
    ok, reasons = result
    check("first element is a bool", isinstance(ok, bool), type(ok))
    check("second element is a list of strings",
          isinstance(reasons, list) and all(isinstance(r, str) for r in reasons))
    check("gate() takes no required argument", M.gate() is not None)


# ==========================================================================
# REPLAY-TABLE STATE: ABSENT vs EMPTY vs NONEMPTY
# ==========================================================================

def test_table_states() -> None:
    print("\nABSENT IS NOT THE SAME AS EMPTY")

    conn = empty_store()
    state = M.replay_table_state(conn)
    check("all present-and-empty tables prove zero",
          state["all_replay_tables_at_zero"], state["not_proven_empty"])
    check("every one is reported EMPTY, not ABSENT",
          all(t["state"] == "EMPTY" for t in state["tables"].values()))
    conn.close()

    conn = empty_store(extra_tables=("pit_pillar_score",))
    state = M.replay_table_state(conn)
    entry = state["tables"]["pit_pillar_score"]
    check("a missing table reads ABSENT", entry["state"] == "ABSENT", entry)
    check("ABSENT proves zero -- no table, no rows", entry["proven_empty"])
    check("ABSENT reports 0 rows because that is a fact, not a guess",
          entry["n_rows"] == 0, entry)
    check("the gate still passes with pit_pillar_score absent",
          state["all_replay_tables_at_zero"])
    conn.close()

    conn = empty_store(rows={"pit_feature": 3})
    state = M.replay_table_state(conn)
    check("a table with rows blocks the proof",
          not state["all_replay_tables_at_zero"])
    check("and is named", state["not_proven_empty"] == ["pit_feature"],
          state["not_proven_empty"])
    check("the count is reported exactly",
          state["tables"]["pit_feature"]["n_rows"] == 3)
    conn.close()

    conn = empty_store(rows={"pit_label": 5})
    state = M.replay_table_state(conn)
    check("an ADJACENT table with rows does NOT block the replay",
          state["all_replay_tables_at_zero"], state["not_proven_empty"])
    check("but it is still reported",
          state["adjacent_tables"]["pit_label"]["n_rows"] == 5)
    check("and marked ungated",
          state["adjacent_tables"]["pit_label"]["gated"] is False)
    conn.close()


# ==========================================================================
# THE UNKNOWN SENTINEL
# ==========================================================================

def test_unknown_is_never_zero() -> None:
    print("\nUNMEASURED IS UNKNOWN, NEVER 0")
    check("UNKNOWN is the string", M.UNKNOWN == "UNKNOWN")
    check("UNKNOWN != 0", M.UNKNOWN != 0)
    check("UNKNOWN is truthy, so `or 0` cannot silently swallow it",
          bool(M.UNKNOWN))

    engine = M.replay_engine_version()
    try:
        import pit_replay as _engine_mod
        _engine_present = True
    except ImportError:
        _engine_present = False
    if _engine_present:
        check("a PRESENT replay engine reports its real version, never 0",
              engine["version"] == _engine_mod.ENGINE_VERSION
              and engine["version"] != 0, engine)
    else:
        check("an absent replay engine is UNKNOWN, not version 0",
              engine["version"] == M.UNKNOWN, engine)
        check("and says why it is unknown", bool(engine.get("why_unknown")),
              engine)

    rev = M.git_revision(cwd=tempfile.gettempdir())
    check("git revision outside a repo is UNKNOWN",
          rev["revision"] == M.UNKNOWN, rev)
    check("and explains itself", bool(rev.get("why_unknown")), rev)

    missing = os.path.join(tempfile.gettempdir(), "no_such_store_zzz.db")
    snap = M.snapshot(db_path=missing, cheap=True)
    check("a missing store does not report size 0",
          snap["db"]["size_bytes"] == M.UNKNOWN, snap["db"])
    check("and its replay tables are NOT declared empty",
          not snap["replay_tables"]["all_replay_tables_at_zero"])
    check("a snapshot of a missing store still renders",
          "REPLAY MANIFEST" in M.render(snap))


# ==========================================================================
# THE FINGERPRINT SAYS WHAT IT IS
# ==========================================================================

def test_fingerprint_is_not_a_hash() -> None:
    print("\nFINGERPRINT, NOT A CONTENT HASH")
    check("the caveat names itself a fingerprint",
          "FINGERPRINT" in M.FINGERPRINT_CAVEAT.upper())
    check("the caveat admits the in-place UPDATE hole",
          "UPDATE" in M.FINGERPRINT_CAVEAT.upper(), M.FINGERPRINT_CAVEAT)

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for table, _heavy in M.SOURCE_TABLES:
        conn.execute("CREATE TABLE %s (x INTEGER)" % table)
    conn.execute("INSERT INTO pit_entity (x) VALUES (1)")
    conn.execute("INSERT INTO pit_entity (x) VALUES (2)")
    conn.commit()

    fp = M.content_fingerprint(conn)
    check("it declares it is not a hash of contents",
          fp["is_a_hash_of_contents"] is False)
    check("counts and rowid bounds are both present",
          fp["tables"]["pit_entity"]["n_rows"] == 2
          and fp["tables"]["pit_entity"]["max_rowid"] == 2,
          fp["tables"]["pit_entity"])
    check("an empty table reports NULL bounds, not 0",
          fp["tables"]["pit_calendar"]["min_rowid"] is None,
          fp["tables"]["pit_calendar"])

    cheap = M.content_fingerprint(conn, cheap=True)
    heavy = [t for t, h in M.SOURCE_TABLES if h]
    check("cheap mode reports heavy counts as UNKNOWN, never 0",
          all(cheap["tables"][t]["n_rows"] == M.UNKNOWN for t in heavy),
          {t: cheap["tables"][t]["n_rows"] for t in heavy})
    check("cheap mode still reports the rowid bounds",
          all("min_rowid" in cheap["tables"][t] for t in heavy))
    conn.close()


# ==========================================================================
# THE FULL-REPLAY GATE IS A DIFFERENT, STRICTER GATE
# ==========================================================================

def test_full_replay_gate() -> None:
    print("\nFULL 165-DATE GATE")
    ok, reasons = M.full_replay_gate(fake_snap())
    check("refuses before the pilot has measured anything", not ok, reasons)
    check("it refuses once per unmeasured component", len(reasons) == 3, reasons)
    check("no total is formed from an UNKNOWN",
          not any("RequiredSpace" in r for r in reasons), reasons)

    # With measurements that do not fit: 3 GiB retained, 2 GiB WAL, 1 GiB temp
    # -> 2*3 + 2 + 1 + 5 = 14 GiB required against 9.8 GiB free.
    ok, reasons = M.full_replay_gate(
        {"disk": {"free_bytes": int(9.8 * M.GIB)}},
        projected_retained_growth_bytes=3 * M.GIB,
        measured_peak_wal_bytes=2 * M.GIB,
        measured_peak_temp_bytes=1 * M.GIB)
    check("refuses when the measured requirement exceeds free space",
          not ok, reasons)
    check("and shows every component of the total separately",
          all(word in reasons[0] for word in ("retained", "WAL", "temp", "reserve")),
          reasons)

    ok, reasons = M.full_replay_gate(
        {"disk": {"free_bytes": int(200 * M.GIB)}},
        projected_retained_growth_bytes=3 * M.GIB,
        measured_peak_wal_bytes=2 * M.GIB,
        measured_peak_temp_bytes=1 * M.GIB)
    check("passes only with room for 2x retained + WAL + temp + 5 GiB",
          ok, reasons)


# ==========================================================================
# DECLARATIONS
# ==========================================================================

def test_declarations() -> None:
    print("\nDECLARATIONS AND SELF-CHECK")
    problems = M.validate()
    check("validate() finds no problems", not problems, problems)

    check("the gate floor is above the per-date abort floor",
          M.FREE_SPACE_GATE_FLOOR_BYTES > M.PER_DATE_ABORT_FLOOR_BYTES)
    check("the per-date abort floor is the stated 7.0 GiB",
          M.PER_DATE_ABORT_FLOOR_BYTES == int(7.0 * M.GIB))
    check("the floor is labelled a convention, not a measurement",
          "CONVENTION" in M.FREE_SPACE_FLOOR_BASIS.upper())

    check("the pilot database is not the main store",
          M.PILOT_DB_PATH != M.PIT_DB_PATH)
    check("the pilot database is the one the task named",
          M.PILOT_DB_PATH.endswith("shafferfineval_pilot.db"))

    check("four pilot dates", len(M.PILOT_DATES) == 4)
    check("they span four distinct years",
          len({d[:4] for d, _n, _r in M.PILOT_DATES}) == 4)
    check("the stated pilot total is 30,308 entity-dates",
          sum(n for _d, n, _r in M.PILOT_DATES) == 30308,
          sum(n for _d, n, _r in M.PILOT_DATES))
    check("the grid denominator is the measured 1,238,663",
          M.AS_OF_GRID["entity_dates"] == 1238663)
    check("165 dates, 2013-01-31 .. 2026-09-18",
          M.AS_OF_GRID["n_dates"] == 165
          and M.AS_OF_GRID["first"] == "2013-01-31"
          and M.AS_OF_GRID["last"] == "2026-09-18")

    check("pit_pillar_score is one of the gated replay tables",
          "pit_pillar_score" in [t for t, _r in M.REPLAY_TABLES])
    check("the gate contract states WHEN the engine must refuse",
          "BEFORE OPENING A WRITE TRANSACTION" in M.GATE_CONTRACT.upper())
    check("the module docstring states the contract too",
          "BEFORE OPENING A WRITE TRANSACTION" in (M.__doc__ or "").upper())

    shape = M.model_shape()
    weights = {b["block"]: b["weight"] for b in shape["blocks"]}
    check("E carries 0.35, not V", abs(weights["ebitda_strength"] - 0.35) < 1e-12,
          weights)
    check("V carries 0.25", abs(weights["valuation"] - 0.25) < 1e-12, weights)
    check("ebitda_scale is recorded as a zero-weight challenger",
          shape["zero_weight_challengers"] == ["ebitda_scale"], shape)
    check("the scale and the clamp are two different recorded numbers",
          shape["scale"] == 0.85 and shape["clamp_max"] == 85.0, shape)


def test_validate_catches_a_broken_declaration() -> None:
    """validate() must be able to fail, or it proves nothing either."""
    print("\nVALIDATE CAN FAIL")
    original = M.FREE_SPACE_GATE_FLOOR_BYTES
    try:
        M.FREE_SPACE_GATE_FLOOR_BYTES = M.PER_DATE_ABORT_FLOOR_BYTES - 1
        problems = M.validate()
        check("a gate floor below the abort floor is caught",
              any("abort line" in p for p in problems), problems)
    finally:
        M.FREE_SPACE_GATE_FLOOR_BYTES = original

    original_dates = M.PILOT_DATES
    try:
        M.PILOT_DATES = (("2014-06-30", 8132, "only one"),)
        problems = M.validate()
        check("a one-date pilot is caught",
              any("3-5 complete cross-sections" in p for p in problems), problems)
    finally:
        M.PILOT_DATES = original_dates
    check("declarations restored", not M.validate())


# ==========================================================================
# AGAINST THE REAL STORE, READ-ONLY
# ==========================================================================

def test_real_store_read_only() -> None:
    print("\nTHE REAL STORE (READ-ONLY)")
    if not os.path.exists(M.PIT_DB_PATH):
        skip("the real store", "absent -- an absent store is not a passing one")
        skip("the real store's emptiness", "unverifiable without the store")
        return

    size_before = os.path.getsize(M.PIT_DB_PATH)
    conn = M._connect_ro()
    try:
        check("query_only is set on the connection",
              conn.execute("PRAGMA query_only").fetchone()[0] == 1)
        wrote = None
        try:
            conn.execute("CREATE TABLE zzz_should_never_exist (x INTEGER)")
            wrote = True
        except sqlite3.Error:
            wrote = False
        check("a write against the read-only connection is REFUSED",
              wrote is False)

        state = M.replay_table_state(conn)
        check("every replay table in the real store is at zero",
              state["all_replay_tables_at_zero"], state["not_proven_empty"])
        check("pit_feature is present and empty",
              state["tables"]["pit_feature"]["state"] == "EMPTY",
              state["tables"]["pit_feature"])
        check("pit_score is present and empty",
              state["tables"]["pit_score"]["state"] == "EMPTY",
              state["tables"]["pit_score"])
        check("pit_replay_run is present and empty",
              state["tables"]["pit_replay_run"]["state"] == "EMPTY",
              state["tables"]["pit_replay_run"])
        check("pit_pillar_score does not exist in the main store yet",
              state["tables"]["pit_pillar_score"]["state"] == "ABSENT",
              state["tables"]["pit_pillar_score"])

        identity = M.db_identity(conn)
        check("the schema hash is a sha256 hex digest",
              len(identity["schema_sha256"]) == 64)
        check("page_count x page_size accounts for the file",
              identity["pages_bytes"] == identity["size_bytes"],
              (identity["pages_bytes"], identity["size_bytes"]))
        check("the store is in WAL mode, as the incident history implies",
              identity["journal_mode"] == "wal", identity["journal_mode"])
        check("data_version is recorded WITH its caveat",
              "per-CONNECTION" in identity["data_version_reading"])
    finally:
        conn.close()

    check("the store was not resized by this test",
          os.path.getsize(M.PIT_DB_PATH) == size_before)
    check("no -wal file was left behind by the read",
          not os.path.exists(M.PIT_DB_PATH + "-wal")
          or os.path.getsize(M.PIT_DB_PATH + "-wal") == 0,
          M._file_size(M.PIT_DB_PATH + "-wal"))


def test_real_snapshot() -> None:
    print("\nA REAL SNAPSHOT (cheap mode, READ-ONLY)")
    if not os.path.exists(M.PIT_DB_PATH):
        skip("a real snapshot", "the store is absent")
        return

    snap = M.snapshot(cheap=True)
    for key in ("manifest_version", "taken_at", "freeze", "code", "db",
                "replay_tables", "grid", "pilot", "fingerprint", "disk",
                "replay_engine", "model_shape", "gate"):
        check("the snapshot carries %s" % key, key in snap, sorted(snap))

    lineage = snap["freeze"]["lineage"]
    check("the lineage carries every sealed freeze plus the live one -- four "
          "as of spec_freeze_v4, and never fewer than the sealed records",
          len(lineage) == 4
          and lineage[-1]["freeze_version"] == pit_frozen_spec.SPEC_FREEZE_VERSION,
          [r["freeze_version"] for r in lineage])
    check("v1 is on record as non-executable",
          lineage[0]["status"] == pit_frozen_spec.STATUS_FROZEN_NONEXECUTABLE,
          lineage[0])
    check("v1 and v2 are both recorded as having produced 0 rows",
          lineage[0]["rows_it_ever_produced"] == 0
          and lineage[1]["rows_it_ever_produced"] == 0)
    check("v3 is SEALED and superseded, kept with its governance defect",
          lineage[2]["freeze_version"] == "spec_freeze_v3"
          and lineage[2]["status"] == pit_frozen_spec.STATUS_SUPERSEDED
          and lineage[2]["rows_it_ever_produced"] == 0
          and lineage[2]["superseded_by"] == "spec_freeze_v4",
          lineage[2])
    check("the LAST lineage entry is the live, executable freeze",
          lineage[-1]["freeze_version"] == pit_frozen_spec.SPEC_FREEZE_VERSION
          and lineage[-1]["status"] == pit_frozen_spec.STATUS_EXECUTABLE,
          lineage[-1])
    check("the LIVE freeze's row count is MEASURED to 0, not assumed",
          lineage[-1]["rows_it_ever_produced"] == 0
          and "rows_measured_at" in lineage[-1], lineage[-1])

    check("the measured grid agrees with the recorded grid",
          snap["grid"]["agrees_with_record"], snap["grid"])
    check("every pilot date's entity count was measured and agrees",
          all(d["agrees"] is True for d in snap["pilot"]["dates"]),
          snap["pilot"]["dates"])
    check("every pilot date is on the as-of grid",
          all(d["on_the_grid"] is True for d in snap["pilot"]["dates"]))
    check("the pilot's share of the grid is derived from the MEASURED total",
          snap["pilot"]["share_basis"] == "measured",
          snap["pilot"]["share_basis"])

    check("the snapshot records whether the working tree was dirty",
          snap["code"]["dirty"] in (True, False, M.UNKNOWN), snap["code"])
    check("free space is recorded in bytes", isinstance(snap["disk"]["free_bytes"], int))

    text = M.render(snap)
    for heading in ("FROZEN SPECIFICATION", "FREEZE LINEAGE", "CODE REVISION",
                    "PIT STORE IDENTITY", "PROOF OF ZERO", "AS-OF GRID",
                    "PILOT DATES", "SOURCE-TABLE FINGERPRINT", "GATE:"):
        check("render() shows %s" % heading, heading in text)
    check("render() does not print a fabricated total for the full replay",
          "FULL 165-DATE GATE: REFUSE" in text)


def test_save_refuses_a_database() -> None:
    print("\nSAVE")
    raised = False
    try:
        M.save(M.PIT_DB_PATH, fake_snap())
    except ValueError:
        raised = True
    check("save() refuses to write over the main store", raised)

    raised = False
    try:
        M.save(M.PILOT_DB_PATH, fake_snap())
    except ValueError:
        raised = True
    check("save() refuses to write over the pilot database", raised)

    path = os.path.join(tempfile.mkdtemp(), "manifest.json")
    M.save(path, fake_snap())
    check("save() writes JSON where asked", os.path.getsize(path) > 0)
    os.remove(path)


def main() -> int:
    print(__doc__.strip().splitlines()[0])
    test_perturbed_freeze()
    test_perturbed_freeze_other_component()
    test_gate_conditions()
    test_gate_returns_a_pair()
    test_table_states()
    test_unknown_is_never_zero()
    test_fingerprint_is_not_a_hash()
    test_full_replay_gate()
    test_declarations()
    test_validate_catches_a_broken_declaration()
    test_real_store_read_only()
    test_real_snapshot()
    test_save_refuses_a_database()

    print()
    print("SKIPPED:  %d %s" % (len(skips), skips if skips else ""))
    print("FAILURES: %d %s" % (len(fails), fails if fails else ""))

    # A last, paranoid check: this test file must leave the freeze exactly as
    # it found it. A test that breaks the specification and forgets to restore
    # it would hand the next run a silent lie.
    final = pit_frozen_spec.verify()
    if not final["intact"]:
        print("FATAL: the freeze was left perturbed by this test run -- %s"
              % final["current_digest"])
        return 2
    print("freeze intact after the run: %s" % final["current_digest"])
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
