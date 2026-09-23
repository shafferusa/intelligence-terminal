"""pit_replay_manifest -- the PRISTINE-STATE SNAPSHOT, taken before row one.

    the LIVE freeze -> [THIS MODULE] -> replay sizing pilot -> capacity decision

WHY A MANIFEST AND NOT A COMMENT IN A LOG
==============================================================================

A score row that says `equity_shaffer_v2_pit_SURVIVOR_ONLY_DIAGNOSTIC` names a
LINEAGE. It does not name the specification that produced it, and it does not
name the store the inputs came from. Those two facts are exactly what a reader
in six months needs and exactly what nobody writes down, because at the moment
of writing they feel too obvious to record.

So this module records them BEFORE the first row exists:

    the frozen model version, the freeze digest, and the whole freeze LINEAGE
    (v1 and v2 with the defects that made them non-executable, not just the
    survivor v3 -- a lineage that keeps only its winners is a marketing
    document);

    the code revision, AND whether the working tree was dirty, because a clean
    `rev-parse HEAD` over eleven modified files describes code that was never
    committed and therefore cannot be recovered;

    the PIT store's immutable identity -- size, page_count, page_size, a
    sha256 OF THE SCHEMA SQL, and a per-table content FINGERPRINT;

    proof that every replay table was at zero, which is a claim that expires
    the instant the pilot writes, and can therefore only be made now;

    the pilot dates with their entity counts MEASURED, not quoted.

THE FINGERPRINT IS NOT A HASH OF THE CONTENTS
==============================================================================

Hashing 8.97 GiB to identify the store would take tens of minutes, read every
page through a cache the pilot is about to need, and produce a number nobody
would ever recompute. Instead each major source table contributes

    (row count, MIN(rowid), MAX(rowid))

which is cheap, is stable under re-reading, and CHANGES IF ROWS ARE ADDED OR
DELETED. What it does NOT detect is an in-place UPDATE of an existing row that
leaves the count and the rowid range alone. That is a real hole and it is
stated here rather than papered over: this is a FINGERPRINT, and the sentence
"the source store was unchanged" is only ever supported to fingerprint
strength.

THE GATE -- THE CONTRACT THE REPLAY ENGINE MUST HONOUR
==============================================================================

    ok, reasons = pit_replay_manifest.gate()
    if not ok:
        refuse(reasons)        # BEFORE opening a write transaction
    conn = sqlite3.connect(PILOT_DB_PATH)   # only now

`gate()` returns (False, reasons) unless ALL of:

    1. pit_frozen_spec.verify()["intact"] is True
    2. pit_frozen_spec.can_execute_replay() == (True, [])
    3. every replay table is at 0 rows IN THE MAIN STORE
    4. free space exceeds FREE_SPACE_GATE_FLOOR_BYTES

The ordering is the point. A refusal after a write transaction is open has
already cost a WAL, and a WAL on this volume is the specific failure that put
9.3 GB of journal on a disk with 13.4 GiB free. The engine calls the gate
while it still owns nothing.

A zero it cannot PROVE is a refusal, not a pass. If a replay table's count
cannot be read, the gate refuses; it never reports UNKNOWN as 0. An ABSENT
table is different and is treated as proven-empty: a table that does not exist
holds no rows, and `pit_pillar_score` genuinely does not exist in the store
yet (its DDL lives in `pit_intermediates.PIT_INTERMEDIATE_DDL_V2` and the
PILOT database is where it gets created).

READ-ONLY, AND MEANT LITERALLY
==============================================================================

This module opens the main store as

    sqlite3.connect("file:...?mode=ro", uri=True)  +  PRAGMA query_only=1

and nothing else. It never attaches it, never indexes it, never vacuums it,
and writes NO file of its own unless you explicitly ask:

    python pit_replay_manifest.py                  # print manifest + gate
    python pit_replay_manifest.py --cheap          # skip the heavy row counts
    python pit_replay_manifest.py --json           # machine-readable
    python pit_replay_manifest.py --save PATH.json # the only write it can do

Stdlib only. No network.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pit_frozen_spec
import pit_store

__all__ = [
    "MANIFEST_VERSION", "UNKNOWN", "PIT_DB_PATH", "PILOT_DB_PATH",
    "FREE_SPACE_GATE_FLOOR_BYTES", "PER_DATE_ABORT_FLOOR_BYTES",
    "REPLAY_TABLES", "ADJACENT_TABLES", "SOURCE_TABLES", "PILOT_DATES",
    "AS_OF_GRID", "FINGERPRINT_CAVEAT", "snapshot", "render", "gate",
    "full_replay_gate", "validate", "save", "main",
]

HERE = os.path.dirname(os.path.abspath(__file__))

MANIFEST_VERSION = "replay_manifest_v1"

#: The sentinel for an UNMEASURED quantity. Never 0, never None-that-renders-
#: as-blank, never an empty string. If this string reaches a report, something
#: was not measured and the report says so in the reader's face.
UNKNOWN = "UNKNOWN"

GIB = 1024 ** 3

#: READ-ONLY, ALWAYS. 8.97 GiB on a volume that has filled once already.
PIT_DB_PATH = pit_store.DEFAULT_PIT_DB_PATH

#: Every byte the pilot writes goes here. Disposable, created from
#: pit_store.SCHEMA plus pit_intermediates.PIT_INTERMEDIATE_DDL_V2.
PILOT_DB_PATH = os.path.join(HERE, "shafferfineval_pilot.db")

#: The per-date abort floor the pilot runner enforces: check `shutil.disk_usage`
#: BEFORE each date, stop cleanly below this.
PER_DATE_ABORT_FLOOR_BYTES = int(7.0 * GIB)

#: The gate floor, deliberately ABOVE the abort floor. Starting a run at its own
#: abort line guarantees the abort; half a GiB of headroom means the first date
#: gets to finish. Both numbers are conventions and are labelled as such -- the
#: pilot exists to replace them with measurements.
FREE_SPACE_GATE_FLOOR_BYTES = int(7.5 * GIB)

FREE_SPACE_FLOOR_BASIS = (
    "CONVENTION, not a measurement. 7.5 GiB gate / 7.0 GiB per-date abort. "
    "The measurement that should replace them -- bytes per entity-date, peak "
    "WAL, peak temp -- is what the sizing pilot is for."
)


# ==========================================================================
# THE TABLES
# ==========================================================================

#: The tables the SURVIVOR_ONLY_DIAGNOSTIC replay writes. Every one must be
#: provably empty in the MAIN store before the pilot starts, because "these
#: rows came from the pilot" is only true if there were no other rows.
REPLAY_TABLES: tuple[tuple[str, str], ...] = (
    ("pit_replay_run", "the run header: one row per replay"),
    ("pit_feature", "one row per entity-date-factor, with availability"),
    ("pit_pillar_score", "one row per entity-date-block (E/V/G/Q)"),
    ("pit_score", "one row per entity-date: the Shaffer Score"),
    ("pit_company_intermediate", "one row per entity-date of per-company state"),
    ("pit_cohort_stat", "one row per peer set per as-of"),
    ("pit_hedge_snapshot", "hedge recommendation snapshots"),
    ("pit_score_prediction", "the separately-versioned calibration"),
)

#: Observed and REPORTED, but not gated. These are written by other pipelines;
#: blocking a replay because an unrelated pipeline has rows would be a gate
#: that fails for reasons the replay cannot fix or even explain.
ADJACENT_TABLES: tuple[str, ...] = (
    "pit_label", "pit_label_run", "pit_label_set", "pit_prediction",
    "pit_sector_score", "pit_promotion", "pit_model_lineage", "pit_proposal",
    "pit_benchmark", "pit_regime", "pit_path",
)

#: (table, heavy). `heavy` tables carry millions of rows, so their COUNT(*) is
#: a full scan -- 15s for pit_fact, 7s for pit_price_bar on this volume.
#: --cheap reports those counts as UNKNOWN rather than guessing them.
SOURCE_TABLES: tuple[tuple[str, bool], ...] = (
    ("pit_entity", False),
    ("pit_entity_name", False),
    ("pit_entity_sic", False),
    ("pit_entity_exit", False),
    ("pit_listing", False),
    ("pit_listing_status", False),
    ("pit_fact", True),
    ("pit_price_bar", True),
    ("pit_corporate_action", False),
    ("pit_eps_obs", False),
    ("pit_eps_ttm", False),
    ("pit_peer_set", False),
    ("pit_peer_member", True),
    ("pit_macro_obs", False),
    ("pit_curve_obs", False),
    ("pit_calendar", False),
)

FINGERPRINT_CAVEAT = (
    "FINGERPRINT, NOT A HASH OF CONTENTS. (row count, MIN(rowid), MAX(rowid)) "
    "per table detects inserts and deletes. It does NOT detect an in-place "
    "UPDATE that leaves the count and the rowid range unchanged. Any claim "
    "that the source store was unchanged is supported only to this strength."
)


# ==========================================================================
# THE PILOT
# ==========================================================================

#: 4 COMPLETE CROSS-SECTIONS, not a sample of companies. The point is
#: operational sizing, so each date must generate the real number of entities,
#: missing factors, refusals, signatures, subfactor rows and scores that a
#: normal replay date generates. The dates span four coverage regimes.
#: `stated` is what the task carried in; `measured` is read from the store and
#: the two are compared rather than assumed equal.
PILOT_DATES: tuple[tuple[str, int, str], ...] = (
    ("2014-06-30", 8132, "early regime, pre-2015 coverage"),
    ("2019-06-28", 7102, "mid regime, the coverage trough"),
    ("2022-06-30", 8033, "post-2020 regime"),
    ("2026-06-30", 7041, "current regime, nearest the grid end"),
)

#: The as-of grid, as MEASURED from pit_calendar (XNYS month-ends) and from the
#: distinct (as_of_date, entity_id) pairs behind the peer sets. The entity-date
#: total is the denominator every extrapolation divides into: a pilot covering
#: 30,308 entity-dates is 2.45% of it, and "3 dates x 55" is not the same
#: arithmetic.
AS_OF_GRID: dict[str, Any] = {
    "n_dates": 165,
    "first": "2013-01-31",
    "last": "2026-09-18",
    "entity_dates": 1238663,
    "market": "XNYS",
    "definition": (
        "month-end trading sessions from pit_calendar (is_month_end=1); "
        "entity-dates = COUNT(DISTINCT pit_peer_set.as_of_date, "
        "pit_peer_member.entity_id)"),
    "source": "measured 2026-09-21 against shafferfineval_pit.db",
}


# ==========================================================================
# READ-ONLY ACCESS -- the only door to the main store in this module
# ==========================================================================

def _connect_ro(db_path: str = PIT_DB_PATH) -> sqlite3.Connection:
    """The ONLY way this module opens the main store.

    mode=ro refuses a write at the VFS layer; query_only=1 refuses it at the
    statement layer. Two locks on the same door, because the cost of being
    wrong here is another 9.3 GB WAL on a volume with 9.7 GiB free.
    """
    conn = sqlite3.connect("file:%s?mode=ro" % db_path.replace("\\", "/"),
                           uri=True, timeout=30.0)
    conn.execute("PRAGMA query_only=1")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _scalar(conn: sqlite3.Connection, sql: str, args: Sequence[Any] = ()) -> Any:
    row = conn.execute(sql, tuple(args)).fetchone()
    return None if row is None else row[0]


# ==========================================================================
# THE FREEZE: version, digest and the WHOLE lineage
# ==========================================================================

def freeze_state() -> dict[str, Any]:
    """The live freeze verification plus the permanent lineage records."""
    try:
        verified = pit_frozen_spec.verify()
    except Exception as exc:                      # pragma: no cover - defensive
        return {
            "verify_failed": "%s: %s" % (type(exc).__name__, exc),
            "intact": False,
            "freeze_version": UNKNOWN,
            "frozen_digest": UNKNOWN,
            "current_digest": UNKNOWN,
            "n_components": UNKNOWN,
            "can_execute_replay": False,
            "can_execute_reasons": ["pit_frozen_spec.verify() raised"],
            "lineage": [],
        }

    try:
        can_exec, exec_reasons = pit_frozen_spec.can_execute_replay()
    except Exception as exc:                      # pragma: no cover - defensive
        can_exec, exec_reasons = False, ["can_execute_replay() raised: %s" % exc]

    lineage: list[dict[str, Any]] = []
    # Every SEALED record, in order. A record's row-count key differs by
    # generation (v3 separates main-store rows from isolated-pilot rows),
    # so the main-store figure is read under either spelling.
    sealed = [getattr(pit_frozen_spec, name)
              for name in ("FREEZE_V1", "FREEZE_V2", "FREEZE_V3", "FREEZE_V4",
                           "FREEZE_V5")
              if hasattr(pit_frozen_spec, name)]
    for record in sealed:
        rows = record.get("rows_it_ever_produced",
                          record.get("rows_it_ever_produced_in_main_store"))
        lineage.append({
            "freeze_version": record["freeze_version"],
            "frozen_at": record["frozen_at"],
            "digest": record["digest"],
            "n_components": record["n_components"],
            "status": record["status"],
            "status_reason": record["status_reason"],
            "rows_it_ever_produced": rows,
            "superseded_by": record["superseded_by"],
        })
    lineage.append({
        "freeze_version": pit_frozen_spec.SPEC_FREEZE_VERSION,
        "frozen_at": pit_frozen_spec.FROZEN_AT,
        "digest": pit_frozen_spec.FROZEN_DIGEST,
        "n_components": len(pit_frozen_spec.COMPONENTS),
        "status": (pit_frozen_spec.STATUS_EXECUTABLE if can_exec
                   else pit_frozen_spec.STATUS_FROZEN_NONEXECUTABLE),
        "status_reason": ("" if can_exec else "; ".join(exec_reasons)),
        "rows_it_ever_produced": UNKNOWN,   # this manifest measures it below
        "superseded_by": None,
    })

    return {
        "freeze_version": verified["freeze_version"],
        "model_version": verified["model_version"],
        "frozen_at": verified["frozen_at"],
        "frozen_digest": verified["frozen_digest"],
        "current_digest": verified["current_digest"],
        "intact": bool(verified["intact"]),
        "n_components": int(verified["n_components"]),
        "missing_components": list(verified["missing_components"]),
        "drifted": list(verified["drifted"]),
        "can_execute_replay": bool(can_exec),
        "can_execute_reasons": list(exec_reasons),
        "sample_scope": pit_frozen_spec.SAMPLE_SCOPE,
        "lineage": lineage,
        "lineage_note": (
            "A lineage that keeps only its survivors is a marketing document. "
            "FREEZE_V1 and FREEZE_V2 are kept with the defects that made them "
            "non-executable, and both produced 0 rows."),
    }


def model_shape() -> dict[str, Any]:
    """The weight table and coverage floor, recorded beside the digest.

    The digest already covers these. They are written out in plain sight
    anyway, because (key, semantic id, weight) TOGETHER is the model, and the
    2026-09-21 defect was two weight tables agreeing on the numeric vector
    while describing different models.
    """
    out: dict[str, Any] = {}
    try:
        import pit_score_signature as sig
        import pit_factor_blocks as blocks
        out["blocks"] = [
            {"block": b, "letter": sig.V2_BLOCK_LETTER[b],
             "weight": sig.V2_MAJOR_WEIGHTS[b]}
            for b in sig.V2_BLOCKS
        ]
        # The SCALE and the CLAMP are two different numbers and get two names:
        # 0.85 is the multiplier (= sum of the block weights), +/-85 is the
        # bound. Calling either one "the clamp" is how 0.85 ends up rendered as
        # a score limit.
        out["scale"] = sig.COMPANY_SCALE_V2
        out["clamp_min"] = sig.V2_COMPANY_MIN
        out["clamp_max"] = sig.V2_COMPANY_MAX
        out["signature_version"] = sig.SIGNATURE_VERSION
        out["min_block_weight"] = blocks.MIN_BLOCK_WEIGHT
        out["block_map_version"] = blocks.BLOCK_MAP_VERSION
        out["policy_weights_version"] = blocks.POLICY_WEIGHTS_VERSION
        out["policy_weights_status"] = blocks.POLICY_WEIGHTS_STATUS
        out["zero_weight_challengers"] = sorted(
            k for k, v in blocks.FACTOR_ROLE.items()
            if v == "SCORING_WEIGHT_ZERO_CHALLENGER")
        out["renormalisation"] = (
            "within-block: ALLOWED. cross-block: FORBIDDEN. Below "
            "MIN_BLOCK_WEIGHT emit NO score and record "
            "PARTIAL_SCORE_INSUFFICIENT_BLOCK_COVERAGE.")
    except Exception as exc:                      # pragma: no cover - defensive
        out["error"] = "%s: %s" % (type(exc).__name__, exc)
    return out


# ==========================================================================
# CODE REVISION -- and whether it is the code that actually ran
# ==========================================================================

def git_revision(cwd: str = HERE) -> dict[str, Any]:
    """HEAD, branch and DIRTINESS. UNKNOWN if this is not a repo.

    The dirty flag is not decoration. A revision recorded over a modified
    working tree names code that was never committed and cannot be recovered
    from the revision, so a clean-looking hash would be a false provenance
    claim rather than a missing one.
    """
    def run(args: list[str]) -> Optional[str]:
        try:
            proc = subprocess.run(["git"] + args, cwd=cwd, timeout=20,
                                  capture_output=True, text=True)
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0:
            return None
        return proc.stdout.strip()

    head = run(["rev-parse", "HEAD"])
    if head is None:
        return {"revision": UNKNOWN, "short": UNKNOWN, "branch": UNKNOWN,
                "dirty": UNKNOWN, "n_dirty_paths": UNKNOWN,
                "why_unknown": "git unavailable, or this is not a repository"}

    status = run(["status", "--porcelain"])
    if status is None:
        dirty: Any = UNKNOWN
        n_dirty: Any = UNKNOWN
        dirty_sample: list[str] = []
    else:
        paths = [ln for ln in status.splitlines() if ln.strip()]
        dirty = bool(paths)
        n_dirty = len(paths)
        dirty_sample = paths[:12]

    return {
        "revision": head,
        "short": head[:12],
        "branch": run(["rev-parse", "--abbrev-ref", "HEAD"]) or UNKNOWN,
        "committed_at": run(["show", "-s", "--format=%cI", "HEAD"]) or UNKNOWN,
        "dirty": dirty,
        "n_dirty_paths": n_dirty,
        "dirty_sample": dirty_sample,
        "reading": (
            "dirty=True means HEAD does NOT describe the code that ran. The "
            "revision is recorded as a pointer, not as a reproduction "
            "guarantee."),
    }


def replay_engine_version() -> dict[str, Any]:
    """The replay engine's version, or UNKNOWN because it does not exist yet."""
    for module_name in ("pit_replay_engine", "pit_replay", "pit_replay_run",
                        "pit_replay_pilot"):
        try:
            module = __import__(module_name)
        except ImportError:
            continue
        for attr in ("REPLAY_ENGINE_VERSION", "ENGINE_VERSION",
                     "REPLAY_VERSION", "VERSION"):
            value = getattr(module, attr, None)
            if value is not None:
                return {"module": module_name, "attribute": attr,
                        "version": str(value)}
        return {"module": module_name, "attribute": UNKNOWN,
                "version": UNKNOWN,
                "why_unknown": "module imported but declares no version attribute"}
    return {"module": UNKNOWN, "attribute": UNKNOWN, "version": UNKNOWN,
            "why_unknown": ("no replay engine module is importable. At the "
                            "moment of this manifest the engine does not "
                            "exist, which is consistent with every replay "
                            "table being at zero.")}


# ==========================================================================
# THE STORE'S IMMUTABLE IDENTITY
# ==========================================================================

def _file_size(path: str) -> Any:
    try:
        return os.path.getsize(path)
    except OSError:
        return UNKNOWN


def db_identity(conn: sqlite3.Connection, db_path: str = PIT_DB_PATH) -> dict[str, Any]:
    """Size, page geometry, schema hash and the data_version counter."""
    size = _file_size(db_path)
    page_count = _scalar(conn, "PRAGMA page_count")
    page_size = _scalar(conn, "PRAGMA page_size")
    freelist = _scalar(conn, "PRAGMA freelist_count")

    rows = conn.execute(
        "SELECT type, name, tbl_name, COALESCE(sql, '') FROM sqlite_master "
        "ORDER BY type, name, tbl_name").fetchall()
    blob = "\n".join("%s|%s|%s|%s" % tuple(r) for r in rows)
    schema_sha256 = hashlib.sha256(blob.encode("utf-8")).hexdigest()

    pages_bytes = (page_count * page_size
                   if isinstance(page_count, int) and isinstance(page_size, int)
                   else UNKNOWN)

    return {
        "path": db_path,
        "exists": os.path.exists(db_path),
        "size_bytes": size,
        "size_gib": (round(size / GIB, 4) if isinstance(size, int) else UNKNOWN),
        "page_count": page_count if page_count is not None else UNKNOWN,
        "page_size": page_size if page_size is not None else UNKNOWN,
        "pages_bytes": pages_bytes,
        "freelist_count": freelist if freelist is not None else UNKNOWN,
        "freelist_bytes": (freelist * page_size
                           if isinstance(freelist, int) and isinstance(page_size, int)
                           else UNKNOWN),
        "journal_mode": _scalar(conn, "PRAGMA journal_mode") or UNKNOWN,
        "wal_file_bytes": _file_size(db_path + "-wal"),
        "shm_file_bytes": _file_size(db_path + "-shm"),
        "n_schema_objects": len(rows),
        "schema_sha256": schema_sha256,
        "schema_sha256_covers": (
            "sqlite_master (type, name, tbl_name, sql) for every table, index, "
            "trigger and view -- the SCHEMA, not one byte of the data"),
        "data_version": _scalar(conn, "PRAGMA data_version"),
        "data_version_reading": (
            "PRAGMA data_version is a per-CONNECTION change counter, not a "
            "content identity. It is comparable only against a later read on "
            "THE SAME connection, where a change means another process "
            "committed to the store. Two different connections may report "
            "different values over identical content; do not compare across "
            "processes."),
        "pit_pillar_score_present": any(r[1] == "pit_pillar_score" for r in rows),
    }


def content_fingerprint(conn: sqlite3.Connection, cheap: bool = False) -> dict[str, Any]:
    """(row count, MIN(rowid), MAX(rowid)) per major source table.

    NOT a hash of 8.97 GiB of content -- see FINGERPRINT_CAVEAT, which is
    returned alongside so a consumer cannot read the numbers without the
    limitation attached to them.
    """
    tables: dict[str, Any] = {}
    t_start = time.time()
    for table, heavy in SOURCE_TABLES:
        entry: dict[str, Any] = {"heavy": heavy}
        try:
            lo, hi = conn.execute(
                "SELECT MIN(rowid), MAX(rowid) FROM %s" % table).fetchone()
            entry["min_rowid"] = lo if lo is not None else None
            entry["max_rowid"] = hi if hi is not None else None
        except sqlite3.Error as exc:
            entry["min_rowid"] = UNKNOWN
            entry["max_rowid"] = UNKNOWN
            entry["error"] = str(exc)
            tables[table] = entry
            continue

        if heavy and cheap:
            entry["n_rows"] = UNKNOWN
            entry["why_unknown"] = ("--cheap: COUNT(*) on this table is a full "
                                    "scan and was deliberately not run")
        else:
            t0 = time.time()
            try:
                entry["n_rows"] = int(conn.execute(
                    "SELECT COUNT(*) FROM %s" % table).fetchone()[0])
                entry["count_seconds"] = round(time.time() - t0, 2)
            except sqlite3.Error as exc:
                entry["n_rows"] = UNKNOWN
                entry["error"] = str(exc)
        tables[table] = entry

    return {
        "tables": tables,
        "cheap_mode": cheap,
        "elapsed_seconds": round(time.time() - t_start, 2),
        "caveat": FINGERPRINT_CAVEAT,
        "is_a_hash_of_contents": False,
    }


# ==========================================================================
# PROOF OF ZERO
# ==========================================================================

def _table_state(conn: sqlite3.Connection, table: str) -> dict[str, Any]:
    try:
        n = int(conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0])
    except sqlite3.Error as exc:
        message = str(exc)
        if "no such table" in message:
            return {"state": "ABSENT", "n_rows": 0, "proven_empty": True,
                    "note": "the table does not exist, so it holds no rows"}
        return {"state": "UNREADABLE", "n_rows": UNKNOWN, "proven_empty": False,
                "error": message}
    return {"state": "EMPTY" if n == 0 else "NONEMPTY", "n_rows": n,
            "proven_empty": n == 0}


def replay_table_state(conn: sqlite3.Connection) -> dict[str, Any]:
    """Every replay table's row count, with ABSENT told apart from EMPTY."""
    tables: dict[str, Any] = {}
    for table, role in REPLAY_TABLES:
        entry = _table_state(conn, table)
        entry["role"] = role
        entry["gated"] = True
        tables[table] = entry

    adjacent: dict[str, Any] = {}
    for table in ADJACENT_TABLES:
        entry = _table_state(conn, table)
        entry["gated"] = False
        adjacent[table] = entry

    all_zero = all(t["proven_empty"] for t in tables.values())
    unproven = sorted(k for k, t in tables.items() if not t["proven_empty"])
    return {
        "tables": tables,
        "adjacent_tables": adjacent,
        "all_replay_tables_at_zero": all_zero,
        "not_proven_empty": unproven,
        "claim_expires": (
            "This is the ONLY moment this claim can be made. It stops being "
            "true the instant the pilot writes its first row, which is why it "
            "is recorded before the engine exists rather than inferred "
            "afterwards."),
        "adjacent_note": (
            "Adjacent tables are REPORTED, never gated. Blocking a replay "
            "because an unrelated pipeline holds rows would be a refusal the "
            "replay could neither fix nor explain."),
    }


# ==========================================================================
# DISK AND GRID
# ==========================================================================

def disk_state(path: str = HERE) -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(path)
    except OSError as exc:
        return {"free_bytes": UNKNOWN, "total_bytes": UNKNOWN,
                "used_bytes": UNKNOWN, "free_gib": UNKNOWN,
                "error": str(exc)}
    return {
        "volume": os.path.splitdrive(os.path.abspath(path))[0] or path,
        "free_bytes": usage.free,
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_gib": round(usage.free / GIB, 3),
        "total_gib": round(usage.total / GIB, 3),
        "used_pct": round(100.0 * usage.used / usage.total, 1) if usage.total else UNKNOWN,
        "gate_floor_bytes": FREE_SPACE_GATE_FLOOR_BYTES,
        "gate_floor_gib": round(FREE_SPACE_GATE_FLOOR_BYTES / GIB, 3),
        "per_date_abort_floor_bytes": PER_DATE_ABORT_FLOOR_BYTES,
        "per_date_abort_floor_gib": round(PER_DATE_ABORT_FLOOR_BYTES / GIB, 3),
        "floor_basis": FREE_SPACE_FLOOR_BASIS,
    }


def grid_state(conn: sqlite3.Connection, measure_entity_dates: bool = True) -> dict[str, Any]:
    """The as-of grid, measured, and compared against the recorded constants."""
    out: dict[str, Any] = {"recorded": dict(AS_OF_GRID)}
    conn.row_factory = sqlite3.Row   # month_end_sessions reads by column name
    try:
        dates = pit_store.month_end_sessions(
            conn, "2013-01-01", "2026-12-31", AS_OF_GRID["market"])
    except sqlite3.Error as exc:
        out["measured"] = {"n_dates": UNKNOWN, "first": UNKNOWN, "last": UNKNOWN,
                           "entity_dates": UNKNOWN, "error": str(exc)}
        out["agrees_with_record"] = False
        return out

    measured: dict[str, Any] = {
        "n_dates": len(dates),
        "first": dates[0] if dates else UNKNOWN,
        "last": dates[-1] if dates else UNKNOWN,
    }
    if measure_entity_dates:
        t0 = time.time()
        try:
            measured["entity_dates"] = int(_scalar(conn, """
                SELECT COUNT(*) FROM (
                    SELECT DISTINCT s.as_of_date, m.entity_id
                      FROM pit_peer_member m
                      JOIN pit_peer_set s ON s.peer_set_id = m.peer_set_id)"""))
            measured["entity_dates_seconds"] = round(time.time() - t0, 2)
        except sqlite3.Error as exc:
            measured["entity_dates"] = UNKNOWN
            measured["error"] = str(exc)
    else:
        measured["entity_dates"] = UNKNOWN
        measured["why_unknown"] = ("--cheap: the DISTINCT scan over 3.07M peer "
                                   "memberships was deliberately not run")

    out["measured"] = measured
    out["agrees_with_record"] = (
        measured.get("n_dates") == AS_OF_GRID["n_dates"]
        and measured.get("first") == AS_OF_GRID["first"]
        and measured.get("last") == AS_OF_GRID["last"]
        and measured.get("entity_dates") in (AS_OF_GRID["entity_dates"], UNKNOWN))
    return out


def pilot_date_state(conn: sqlite3.Connection, verify: bool = True) -> dict[str, Any]:
    """The pilot dates, with entity counts MEASURED and compared to the stated.

    Each is a COMPLETE CROSS-SECTION. The measured count is the number of
    entity-dates that date will actually generate, which is what an
    extrapolation needs; a stated number that nobody re-read is how a sizing
    estimate turns out to be 3x wrong.
    """
    dates: list[dict[str, Any]] = []
    stated_total = 0
    measured_total: Any = 0
    for as_of, stated, regime in PILOT_DATES:
        stated_total += stated
        entry: dict[str, Any] = {"as_of_date": as_of, "stated_entities": stated,
                                 "regime": regime}
        if verify:
            t0 = time.time()
            try:
                n = int(_scalar(conn, """
                    SELECT COUNT(DISTINCT m.entity_id)
                      FROM pit_peer_member m
                      JOIN pit_peer_set s ON s.peer_set_id = m.peer_set_id
                     WHERE s.as_of_date = ?""", (as_of,)))
                entry["measured_entities"] = n
                entry["agrees"] = (n == stated)
                entry["seconds"] = round(time.time() - t0, 2)
                if isinstance(measured_total, int):
                    measured_total += n
            except sqlite3.Error as exc:
                entry["measured_entities"] = UNKNOWN
                entry["agrees"] = UNKNOWN
                entry["error"] = str(exc)
                measured_total = UNKNOWN
            try:
                in_grid = _scalar(
                    conn,
                    "SELECT COUNT(*) FROM pit_calendar WHERE market = ? "
                    "AND is_month_end = 1 AND session_date = ?",
                    (AS_OF_GRID["market"], as_of))
                entry["on_the_grid"] = bool(in_grid)
            except sqlite3.Error:
                entry["on_the_grid"] = UNKNOWN
        else:
            entry["measured_entities"] = UNKNOWN
            entry["agrees"] = UNKNOWN
            entry["on_the_grid"] = UNKNOWN
            measured_total = UNKNOWN
        dates.append(entry)

    total = measured_total if isinstance(measured_total, int) else stated_total
    pct = (100.0 * total / AS_OF_GRID["entity_dates"]) if AS_OF_GRID["entity_dates"] else UNKNOWN
    return {
        "dates": dates,
        "n_dates": len(dates),
        "stated_entity_dates": stated_total,
        "measured_entity_dates": measured_total,
        "pilot_share_of_grid_pct": round(pct, 3) if pct != UNKNOWN else UNKNOWN,
        "share_basis": ("measured" if isinstance(measured_total, int) else "stated"),
        "extrapolation_rule": (
            "BytesPerEntityDate = DeltaDB / N_entity_dates, extrapolated over "
            "the %d entity-date grid. NOT 'n dates x 55' -- the cross-sections "
            "differ in size by 15%% and the grid is not a multiple of any one "
            "of them." % AS_OF_GRID["entity_dates"]),
    }


# ==========================================================================
# THE SNAPSHOT
# ==========================================================================

def snapshot(cheap: bool = False, db_path: str = PIT_DB_PATH) -> dict[str, Any]:
    """The whole pristine-state record. READ-ONLY; writes nothing anywhere.

    `cheap=True` skips the full-scan COUNT(*)s and the DISTINCT entity-date
    scan, reporting those numbers as UNKNOWN. It never substitutes 0.
    """
    started = time.time()
    snap: dict[str, Any] = {
        "manifest_version": MANIFEST_VERSION,
        "taken_at": _now(),
        "taken_by": "pit_replay_manifest",
        "purpose": ("pristine-state snapshot for the SURVIVOR_ONLY_DIAGNOSTIC "
                    "replay sizing pilot -- taken BEFORE the first replay row "
                    "exists"),
        "sample_scope": pit_frozen_spec.SAMPLE_SCOPE,
        "frozen_model_version": pit_frozen_spec.FROZEN_MODEL_VERSION,
        "cheap_mode": cheap,
        "freeze": freeze_state(),
        "model_shape": model_shape(),
        "code": git_revision(),
        "replay_engine": replay_engine_version(),
        "pilot_db_path": PILOT_DB_PATH,
        "pilot_db_exists": os.path.exists(PILOT_DB_PATH),
        "disk": disk_state(os.path.dirname(os.path.abspath(db_path))),
        "read_only_promise": (
            "the main store was opened mode=ro with PRAGMA query_only=1 and "
            "was never attached, indexed or vacuumed"),
    }

    if not os.path.exists(db_path):
        snap["db"] = {"path": db_path, "exists": False, "size_bytes": UNKNOWN}
        snap["fingerprint"] = {"tables": {}, "caveat": FINGERPRINT_CAVEAT,
                               "error": "store not found"}
        snap["replay_tables"] = {
            "tables": {}, "adjacent_tables": {},
            "all_replay_tables_at_zero": False,
            "not_proven_empty": [t for t, _ in REPLAY_TABLES],
            "error": "store not found -- emptiness is UNKNOWN, not proven"}
        snap["grid"] = {"recorded": dict(AS_OF_GRID),
                        "measured": {"n_dates": UNKNOWN}, "agrees_with_record": False}
        snap["pilot"] = {
            "dates": [{"as_of_date": d, "stated_entities": n, "regime": r,
                       "measured_entities": UNKNOWN, "agrees": UNKNOWN,
                       "on_the_grid": UNKNOWN} for d, n, r in PILOT_DATES],
            "n_dates": len(PILOT_DATES),
            "stated_entity_dates": sum(n for _d, n, _r in PILOT_DATES),
            "measured_entity_dates": UNKNOWN,
            "pilot_share_of_grid_pct": UNKNOWN,
            "share_basis": UNKNOWN,
            "error": "store not found"}
        snap["elapsed_seconds"] = round(time.time() - started, 2)
        return snap

    conn = _connect_ro(db_path)
    try:
        conn.row_factory = sqlite3.Row
        snap["db"] = db_identity(conn, db_path)
        snap["replay_tables"] = replay_table_state(conn)
        snap["grid"] = grid_state(conn, measure_entity_dates=not cheap)
        snap["pilot"] = pilot_date_state(conn, verify=True)
        snap["fingerprint"] = content_fingerprint(conn, cheap=cheap)
    finally:
        conn.close()

    # The live freeze's "rows it ever produced" is left UNKNOWN by
    # freeze_state(), which reads no database. Here it has just been MEASURED,
    # so the measurement replaces the sentinel -- and only if every gated table
    # was provably empty. An unproven zero stays UNKNOWN.
    if snap["replay_tables"].get("all_replay_tables_at_zero"):
        for record in snap["freeze"].get("lineage", []):
            if record["freeze_version"] == pit_frozen_spec.SPEC_FREEZE_VERSION:
                record["rows_it_ever_produced"] = 0
                record["rows_measured_at"] = snap["taken_at"]

    snap["elapsed_seconds"] = round(time.time() - started, 2)
    ok, reasons = gate(snap)
    snap["gate"] = {"passed": ok, "reasons": reasons,
                    "contract": GATE_CONTRACT}
    snap["full_replay_gate"] = dict(zip(("passed", "reasons"),
                                        full_replay_gate(snap)))
    return snap


# ==========================================================================
# THE GATE
# ==========================================================================

GATE_CONTRACT = (
    "THE REPLAY ENGINE MUST CALL gate() AND REFUSE BEFORE OPENING A WRITE "
    "TRANSACTION IF IT RETURNS False. Not after opening one and rolling back: "
    "a rolled-back transaction has already allocated a WAL, and a WAL is the "
    "failure mode this volume has already suffered once."
)


def gate(snap: Optional[dict[str, Any]] = None) -> tuple:
    """May the pilot open a write transaction? (ok, reasons).

    THE CONTRACT
    ------------
        ok, reasons = pit_replay_manifest.gate()
        if not ok:
            refuse(reasons)          # <-- BEFORE any connection is opened
                                     #     for writing, to the pilot DB or
                                     #     anywhere else
        ...only now may a write transaction begin...

    Returns (True, []) only when ALL FOUR hold:

      1. `pit_frozen_spec.verify()["intact"]` is True -- the specification the
         scores will claim to come from is the one that is actually loaded.
      2. `pit_frozen_spec.can_execute_replay() == (True, [])` -- the
         specification reaches far enough to assemble a company score.
      3. every table in REPLAY_TABLES is PROVABLY at 0 rows in the MAIN store.
         ABSENT counts as proven (no table, no rows). UNREADABLE does NOT: an
         unprovable zero is a refusal, never a pass.
      4. free space > FREE_SPACE_GATE_FLOOR_BYTES.

    Pass `snap` to reuse a snapshot already taken; otherwise a light snapshot
    is built here, which reads only what the four conditions need.
    """
    reasons: list[str] = []

    if snap is None:
        freeze = freeze_state()
        disk = disk_state()
        if not os.path.exists(PIT_DB_PATH):
            tables = {"error": "store not found",
                      "all_replay_tables_at_zero": False,
                      "not_proven_empty": [t for t, _ in REPLAY_TABLES],
                      "tables": {}}
        else:
            conn = _connect_ro()
            try:
                tables = replay_table_state(conn)
            finally:
                conn.close()
    else:
        freeze = snap.get("freeze", {})
        disk = snap.get("disk", {})
        tables = snap.get("replay_tables", {})

    # 1 -- the freeze is intact
    if not freeze.get("intact"):
        drifted = freeze.get("drifted") or []
        names = ", ".join(d.get("component", "?") for d in drifted[:6]) or "unknown"
        reasons.append(
            "FREEZE NOT INTACT: %s is not %s. %d component(s) moved (%s). The "
            "scores this replay would write must not be labelled %s."
            % (freeze.get("current_digest", UNKNOWN),
               freeze.get("frozen_digest", UNKNOWN), len(drifted), names,
               pit_frozen_spec.FROZEN_MODEL_VERSION))
    missing = freeze.get("missing_components") or []
    if missing:
        reasons.append("FREEZE COMPONENTS MISSING: %s" % ", ".join(missing))

    # 2 -- the specification can actually be executed
    if not freeze.get("can_execute_replay"):
        why = freeze.get("can_execute_reasons") or ["no reason recorded"]
        reasons.append("SPECIFICATION NOT EXECUTABLE: %s" % "; ".join(why))

    # 3 -- every replay table provably at zero
    if not tables.get("all_replay_tables_at_zero"):
        detail = []
        for name in tables.get("not_proven_empty", []):
            entry = tables.get("tables", {}).get(name, {})
            detail.append("%s=%s(%s)" % (name, entry.get("n_rows", UNKNOWN),
                                         entry.get("state", UNKNOWN)))
        if not detail and tables.get("error"):
            detail.append(tables["error"])
        reasons.append(
            "REPLAY TABLES NOT PROVABLY EMPTY: %s. A zero that cannot be read "
            "is not a zero." % ("; ".join(detail) or "unknown"))

    # 4 -- free space above the stated floor
    free = disk.get("free_bytes", UNKNOWN)
    if not isinstance(free, int):
        reasons.append("FREE SPACE UNKNOWN: refusing rather than assuming. %s"
                       % disk.get("error", ""))
    elif free <= FREE_SPACE_GATE_FLOOR_BYTES:
        reasons.append(
            "FREE SPACE %.3f GiB IS NOT ABOVE THE %.3f GiB FLOOR (per-date "
            "abort floor %.3f GiB). %s"
            % (free / GIB, FREE_SPACE_GATE_FLOOR_BYTES / GIB,
               PER_DATE_ABORT_FLOOR_BYTES / GIB, FREE_SPACE_FLOOR_BASIS))

    return (not reasons), reasons


def full_replay_gate(snap: Optional[dict[str, Any]] = None,
                     projected_retained_growth_bytes: Any = UNKNOWN,
                     measured_peak_wal_bytes: Any = UNKNOWN,
                     measured_peak_temp_bytes: Any = UNKNOWN,
                     retained_multiplier: float = 2.0,
                     reserve_bytes: int = 5 * GIB) -> tuple:
    """THE 165-DATE GATE -- a DIFFERENT and much stricter gate than gate().

        FreeSpace_start > retained_multiplier * ProjectedRetainedGrowth
                          + MeasuredPeakWAL + MeasuredPeakTemp + 5 GiB reserve

    Every component is reported separately; no total is ever formed from a
    component that was not measured. Before the pilot runs, all three
    measurements are UNKNOWN, so this returns (False, ...) -- which is the
    correct answer, not a limitation. The multiplier is a convention until the
    pilot replaces it with a measured write-amplification factor.

    gate() governs the PILOT. This governs the FULL RUN. Passing the first
    says nothing about the second.
    """
    disk = (snap or {}).get("disk") or disk_state()
    free = disk.get("free_bytes", UNKNOWN)
    reasons: list[str] = []
    components = {
        "projected_retained_growth_bytes": projected_retained_growth_bytes,
        "retained_multiplier": retained_multiplier,
        "measured_peak_wal_bytes": measured_peak_wal_bytes,
        "measured_peak_temp_bytes": measured_peak_temp_bytes,
        "safety_reserve_bytes": reserve_bytes,
    }
    for key in ("projected_retained_growth_bytes", "measured_peak_wal_bytes",
                "measured_peak_temp_bytes"):
        if not isinstance(components[key], int):
            reasons.append("%s is UNKNOWN -- the sizing pilot has not measured "
                           "it yet, so no required-space total may be formed" % key)
    if not isinstance(free, int):
        reasons.append("FreeSpace_start is UNKNOWN")
    if reasons:
        return False, reasons

    required = (retained_multiplier * projected_retained_growth_bytes
                + measured_peak_wal_bytes + measured_peak_temp_bytes
                + reserve_bytes)
    if free <= required:
        return False, [
            "RequiredSpace %.3f GiB exceeds free %.3f GiB (%.1fx retained "
            "%.3f + WAL %.3f + temp %.3f + reserve %.3f, all GiB)"
            % (required / GIB, free / GIB, retained_multiplier,
               projected_retained_growth_bytes / GIB,
               measured_peak_wal_bytes / GIB, measured_peak_temp_bytes / GIB,
               reserve_bytes / GIB)]
    return True, []


# ==========================================================================
# RENDER
# ==========================================================================

def _fmt_bytes(value: Any) -> str:
    if not isinstance(value, int):
        return str(value)
    if value >= GIB:
        return "%d (%.3f GiB)" % (value, value / GIB)
    if value >= 1024 * 1024:
        return "%d (%.1f MiB)" % (value, value / (1024.0 * 1024.0))
    return "%d" % value


def render(snap: Optional[dict[str, Any]] = None, cheap: bool = False) -> str:
    """The manifest as plain text."""
    snap = snap if snap is not None else snapshot(cheap=cheap)
    lines: list[str] = []
    add = lines.append

    add("=" * 78)
    add("REPLAY MANIFEST -- PRISTINE STATE BEFORE THE FIRST REPLAY ROW")
    add("=" * 78)
    add("  manifest_version   %s" % snap["manifest_version"])
    add("  taken_at           %s" % snap["taken_at"])
    add("  sample_scope       %s" % snap["sample_scope"])
    add("  model_version      %s" % snap["frozen_model_version"])
    if snap.get("cheap_mode"):
        add("  MODE               CHEAP -- heavy counts reported UNKNOWN, never 0")
    add("")

    fr = snap.get("freeze", {})
    add("FROZEN SPECIFICATION")
    add("  freeze_version     %s" % fr.get("freeze_version", UNKNOWN))
    add("  frozen_at          %s" % fr.get("frozen_at", UNKNOWN))
    add("  frozen_digest      %s" % fr.get("frozen_digest", UNKNOWN))
    add("  current_digest     %s" % fr.get("current_digest", UNKNOWN))
    add("  intact             %s" % fr.get("intact", UNKNOWN))
    add("  components         %s" % fr.get("n_components", UNKNOWN))
    add("  executable         %s %s" % (fr.get("can_execute_replay", UNKNOWN),
                                        fr.get("can_execute_reasons") or ""))
    add("")
    add("FREEZE LINEAGE  (defects kept, not deleted)")
    for record in fr.get("lineage", []):
        add("  %-15s %s  %s" % (record["freeze_version"],
                                str(record["digest"])[:16] + "...",
                                record["status"]))
        add("                  %d components, %s rows ever produced%s"
            % (record["n_components"], record["rows_it_ever_produced"],
               ", superseded by %s" % record["superseded_by"]
               if record["superseded_by"] else ""))
        if record.get("status_reason"):
            add("                  reason: %s" % record["status_reason"])
    add("")

    shape = snap.get("model_shape", {})
    if shape.get("blocks"):
        add("MODEL SHAPE  (key + semantic id + weight, together)")
        for block in shape["blocks"]:
            add("  %s  %-18s %.2f" % (block["letter"], block["block"],
                                      block["weight"]))
        add("  scale x%s   clamp [%s, %s]   MIN_BLOCK_WEIGHT %s   %s / %s"
            % (shape.get("scale"), shape.get("clamp_min"), shape.get("clamp_max"),
               shape.get("min_block_weight"),
               shape.get("policy_weights_version"),
               shape.get("policy_weights_status")))
        add("  zero-weight challengers: %s"
            % (", ".join(shape.get("zero_weight_challengers", [])) or "none"))
        add("  %s" % shape.get("renormalisation", ""))
        add("")

    code = snap.get("code", {})
    add("CODE REVISION")
    add("  revision           %s" % code.get("revision", UNKNOWN))
    add("  branch             %s" % code.get("branch", UNKNOWN))
    add("  dirty              %s (%s modified path(s))"
        % (code.get("dirty", UNKNOWN), code.get("n_dirty_paths", UNKNOWN)))
    if code.get("dirty") is True:
        add("  WARNING            HEAD does not describe the code that ran")
    engine = snap.get("replay_engine", {})
    add("  replay engine      %s (%s)" % (engine.get("version", UNKNOWN),
                                          engine.get("module", UNKNOWN)))
    if engine.get("why_unknown"):
        add("                     %s" % engine["why_unknown"])
    add("")

    db = snap.get("db", {})
    add("PIT STORE IDENTITY  (opened READ-ONLY)")
    add("  path               %s" % db.get("path", UNKNOWN))
    add("  size               %s" % _fmt_bytes(db.get("size_bytes", UNKNOWN)))
    add("  page_count         %s" % db.get("page_count", UNKNOWN))
    add("  page_size          %s" % db.get("page_size", UNKNOWN))
    add("  freelist           %s pages (%s)"
        % (db.get("freelist_count", UNKNOWN),
           _fmt_bytes(db.get("freelist_bytes", UNKNOWN))))
    add("  journal_mode       %s" % db.get("journal_mode", UNKNOWN))
    add("  -wal / -shm        %s / %s" % (_fmt_bytes(db.get("wal_file_bytes", UNKNOWN)),
                                          _fmt_bytes(db.get("shm_file_bytes", UNKNOWN))))
    add("  schema objects     %s" % db.get("n_schema_objects", UNKNOWN))
    add("  schema_sha256      %s" % db.get("schema_sha256", UNKNOWN))
    add("  data_version       %s  (per-connection counter, NOT an identity)"
        % db.get("data_version", UNKNOWN))
    add("  pit_pillar_score   %s"
        % ("PRESENT" if db.get("pit_pillar_score_present")
           else "ABSENT -- the PILOT database creates it from "
                "pit_intermediates.PIT_INTERMEDIATE_DDL_V2"))
    add("")

    disk = snap.get("disk", {})
    add("DISK AT SNAPSHOT TIME")
    add("  volume             %s" % disk.get("volume", UNKNOWN))
    add("  free               %s" % _fmt_bytes(disk.get("free_bytes", UNKNOWN)))
    add("  total              %s" % _fmt_bytes(disk.get("total_bytes", UNKNOWN)))
    add("  used               %s%%" % disk.get("used_pct", UNKNOWN))
    add("  gate floor         %.3f GiB   per-date abort floor %.3f GiB"
        % (FREE_SPACE_GATE_FLOOR_BYTES / GIB, PER_DATE_ABORT_FLOOR_BYTES / GIB))
    add("  floor basis        %s" % FREE_SPACE_FLOOR_BASIS)
    add("")

    rt = snap.get("replay_tables", {})
    add("PROOF OF ZERO  (the claim that expires when the pilot writes)")
    for name, entry in sorted(rt.get("tables", {}).items()):
        add("  %-26s %-10s %-12s %s"
            % (name, entry.get("state", UNKNOWN), entry.get("n_rows", UNKNOWN),
               entry.get("role", "")))
    add("  ALL REPLAY TABLES AT ZERO: %s"
        % rt.get("all_replay_tables_at_zero", UNKNOWN))
    if rt.get("adjacent_tables"):
        add("  adjacent (reported, NOT gated):")
        for name, entry in sorted(rt["adjacent_tables"].items()):
            add("    %-24s %-10s %s" % (name, entry.get("state", UNKNOWN),
                                        entry.get("n_rows", UNKNOWN)))
    add("")

    grid = snap.get("grid", {})
    measured = grid.get("measured", {})
    rec = grid.get("recorded", {})
    add("AS-OF GRID")
    add("  dates              %s measured / %s recorded"
        % (measured.get("n_dates", UNKNOWN), rec.get("n_dates", UNKNOWN)))
    add("  range              %s .. %s" % (measured.get("first", UNKNOWN),
                                           measured.get("last", UNKNOWN)))
    add("  entity-dates       %s measured / %s recorded"
        % (measured.get("entity_dates", UNKNOWN), rec.get("entity_dates", UNKNOWN)))
    add("  agrees with record %s" % grid.get("agrees_with_record", UNKNOWN))
    add("")

    pilot = snap.get("pilot", {})
    add("PILOT DATES  (complete cross-sections)")
    for entry in pilot.get("dates", []):
        add("  %s  stated %-6s measured %-8s agrees %-6s on-grid %-6s  %s"
            % (entry["as_of_date"], entry["stated_entities"],
               entry.get("measured_entities", UNKNOWN),
               entry.get("agrees", UNKNOWN), entry.get("on_the_grid", UNKNOWN),
               entry.get("regime", "")))
    add("  pilot entity-dates %s (%s%% of the grid, %s)"
        % (pilot.get("measured_entity_dates", UNKNOWN),
           pilot.get("pilot_share_of_grid_pct", UNKNOWN),
           pilot.get("share_basis", UNKNOWN)))
    add("  %s" % pilot.get("extrapolation_rule", ""))
    add("  pilot DB           %s (%s)"
        % (snap.get("pilot_db_path", UNKNOWN),
           "EXISTS" if snap.get("pilot_db_exists") else "not yet created"))
    add("")

    fp = snap.get("fingerprint", {})
    add("SOURCE-TABLE FINGERPRINT")
    add("  %s" % FINGERPRINT_CAVEAT)
    for name, entry in sorted(fp.get("tables", {}).items()):
        add("  %-24s n=%-12s rowid %s..%s"
            % (name, entry.get("n_rows", UNKNOWN), entry.get("min_rowid", UNKNOWN),
               entry.get("max_rowid", UNKNOWN)))
    add("")

    ok, reasons = gate(snap)
    add("=" * 78)
    add("GATE: %s" % ("PASS -- the pilot may open a write transaction"
                      if ok else "REFUSE"))
    for reason in reasons:
        add("  - %s" % reason)
    add("  %s" % GATE_CONTRACT)
    full_ok, full_reasons = full_replay_gate(snap)
    add("")
    add("FULL 165-DATE GATE: %s" % ("PASS" if full_ok else "REFUSE"))
    for reason in full_reasons:
        add("  - %s" % reason)
    add("=" * 78)
    return "\n".join(lines)


# ==========================================================================
# SELF-CHECK
# ==========================================================================

def validate() -> list[str]:
    """Declarative self-check. Reads no database. Returns problems."""
    problems: list[str] = []

    if not MANIFEST_VERSION:
        problems.append("MANIFEST_VERSION is empty")
    if UNKNOWN != "UNKNOWN" or not isinstance(UNKNOWN, str):
        problems.append("UNKNOWN must be the string 'UNKNOWN', never a falsey value")
    if UNKNOWN == 0 or UNKNOWN is None:
        problems.append("UNKNOWN must not compare equal to 0 or None")

    if FREE_SPACE_GATE_FLOOR_BYTES <= PER_DATE_ABORT_FLOOR_BYTES:
        problems.append(
            "the gate floor must sit ABOVE the per-date abort floor, or the "
            "pilot begins at its own abort line")

    names = [t for t, _ in REPLAY_TABLES]
    if len(names) != len(set(names)):
        problems.append("REPLAY_TABLES contains a duplicate")
    if "pit_pillar_score" not in names:
        problems.append("pit_pillar_score must be gated: it is a replay output")
    overlap = set(names) & {t for t, _ in SOURCE_TABLES}
    if overlap:
        problems.append("a table is both a replay output and a source: %s"
                        % sorted(overlap))
    adjacent_overlap = set(names) & set(ADJACENT_TABLES)
    if adjacent_overlap:
        problems.append("a table is both gated and adjacent: %s"
                        % sorted(adjacent_overlap))

    if not 3 <= len(PILOT_DATES) <= 5:
        problems.append("the pilot is sized at 3-5 complete cross-sections, "
                        "not %d" % len(PILOT_DATES))
    seen: set[str] = set()
    for as_of, n, _regime in PILOT_DATES:
        if as_of in seen:
            problems.append("duplicate pilot date %s" % as_of)
        seen.add(as_of)
        if len(as_of) != 10 or as_of[4] != "-" or as_of[7] != "-":
            problems.append("pilot date %r is not YYYY-MM-DD" % as_of)
        if not as_of[:4].isdigit():
            problems.append("pilot date %r has no year" % as_of)
        if n <= 0:
            problems.append("pilot date %s claims %d entities" % (as_of, n))
        if not (AS_OF_GRID["first"] <= as_of <= AS_OF_GRID["last"]):
            problems.append("pilot date %s is outside the as-of grid" % as_of)

    total = sum(n for _d, n, _r in PILOT_DATES)
    if total >= AS_OF_GRID["entity_dates"]:
        problems.append("the pilot cannot be larger than the grid it samples")
    if len({as_of[:4] for as_of, _n, _r in PILOT_DATES}) < 3:
        problems.append("the pilot dates must span at least three coverage "
                        "regimes, not %d years"
                        % len({d[:4] for d, _n, _r in PILOT_DATES}))

    if AS_OF_GRID["n_dates"] != 165:
        problems.append("the as-of grid is 165 month-end dates")
    if AS_OF_GRID["entity_dates"] != 1238663:
        problems.append("the measured entity-date total is 1,238,663")

    try:
        if pit_frozen_spec.FROZEN_MODEL_VERSION != \
                "equity_shaffer_v2_pit_SURVIVOR_ONLY_DIAGNOSTIC":
            problems.append("the frozen model version string moved")
        # The manifest follows the LIVE freeze; it pins the lineage's SHAPE,
        # never a particular version literal, so a successor freeze does not
        # silently invalidate every manifest.
        chain = [getattr(pit_frozen_spec, n)
                 for n in ("FREEZE_V1", "FREEZE_V2", "FREEZE_V3", "FREEZE_V4",
                           "FREEZE_V5")
                 if hasattr(pit_frozen_spec, n)]
        for a, b in zip(chain, chain[1:]):
            if a["superseded_by"] != b["freeze_version"]:
                problems.append("freeze lineage %s -> %s is broken"
                                % (a["freeze_version"], b["freeze_version"]))
        if chain and chain[-1]["superseded_by"] != pit_frozen_spec.SPEC_FREEZE_VERSION:
            problems.append("the last sealed record must name the LIVE freeze "
                            "(%s) as its successor"
                            % pit_frozen_spec.SPEC_FREEZE_VERSION)
        for record in chain:
            rows = record.get("rows_it_ever_produced",
                              record.get("rows_it_ever_produced_in_main_store"))
            if rows != 0:
                problems.append("%s must record 0 main-store rows"
                                % record["freeze_version"])
        if pit_frozen_spec.SAMPLE_SCOPE not in pit_frozen_spec.FROZEN_MODEL_VERSION:
            problems.append("the sample scope must be IN the model version string")
    except Exception as exc:                      # pragma: no cover - defensive
        problems.append("pit_frozen_spec unreadable: %s" % exc)

    if PILOT_DB_PATH == PIT_DB_PATH:
        problems.append("the pilot database must not be the main store")
    if not PILOT_DB_PATH.endswith("shafferfineval_pilot.db"):
        problems.append("the pilot database path moved")

    if "FINGERPRINT" not in FINGERPRINT_CAVEAT.upper():
        problems.append("the fingerprint caveat must say it is a fingerprint")
    if "BEFORE OPENING A WRITE TRANSACTION" not in GATE_CONTRACT.upper():
        problems.append("the gate contract must state when the engine refuses")

    return problems


def save(path: str, snap: Optional[dict[str, Any]] = None,
         cheap: bool = False) -> str:
    """Write the manifest as JSON. THE ONLY WRITE THIS MODULE CAN PERFORM.

    Never called on import, never called by `snapshot()`, and never aimed at
    the main store or the pilot database -- a manifest that lived inside the
    database it describes would vanish with it.
    """
    if os.path.abspath(path) in (os.path.abspath(PIT_DB_PATH),
                                 os.path.abspath(PILOT_DB_PATH)):
        raise ValueError("the manifest may not be written over a database")
    snap = snap if snap is not None else snapshot(cheap=cheap)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(snap, handle, indent=2, sort_keys=True, default=str)
    return path


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cheap = "--cheap" in args
    as_json = "--json" in args

    problems = validate()
    snap = snapshot(cheap=cheap)

    if as_json:
        print(json.dumps(snap, indent=2, sort_keys=True, default=str))
    else:
        print(render(snap))
        print()
        if problems:
            print("SELF-CHECK FAILURES: %d" % len(problems))
            for problem in problems:
                print("  - %s" % problem)
        else:
            print("SELF-CHECK: PASS")

    if "--save" in args:
        index = args.index("--save")
        if index + 1 >= len(args):
            print("--save needs a path", file=sys.stderr)
            return 2
        print("manifest written to %s" % save(args[index + 1], snap))

    ok, _reasons = gate(snap)
    return 0 if (ok and not problems) else 1


if __name__ == "__main__":
    sys.exit(main())
