"""pit_replay_meter -- the measurement harness for the replay sizing pilot.

WHAT THIS IS
============
A stdlib-only instrument that wraps a write workload and reports, in bytes and
seconds, what that workload actually cost the volume. It exists because the
replay budget must be MEASUREMENT-DERIVED. This project has already put a
9.3 GB WAL on this volume once; the margin today (~9.7 GiB free, 96% used) is
too small to discover a second transient multiplier experimentally.

It measures, around any workload:

  * PER-TABLE BYTES     -- exact via the `dbstat` virtual table when this
                           Python's sqlite3 was compiled with it, otherwise by
                           page_count deltas taken around a scope in which
                           exactly one table is written. The method actually
                           used is reported in every report; the fallback also
                           reports whether attribution was exclusive.
  * DB FILE GROWTH      -- os.path.getsize before/after, AND page_count *
                           page_size, AND freelist_count, because those three
                           disagree in informative ways (WAL not yet folded in,
                           pages freed but not returned to the filesystem).
  * WAL PEAK / FINAL    -- sampled by a background thread at a stated interval.
  * WAL AFTER CHECKPOINT-- with the (busy, log_pages, checkpointed) return
                           value READ and recorded, never assumed. busy=1 with
                           log_pages>0 and checkpointed=0 is the busy-refusal
                           signature and is reported as such.
  * FREE-SPACE LOW-WATER-- shutil.disk_usage on the DB's volume, same thread.
  * TEMP / JOURNAL      -- `<db>-journal`, `<db>-shm`, and `etilqs_*` files in
                           the temp directory, peak observed.
  * ELAPSED + WORK      -- wall seconds and entity-dates processed.

HONESTY RULE (non-negotiable)
=============================
Every field is either a real measurement or None. A quantity that could not be
measured is None and its reason is in ``report["unknown"][field]``. Nothing is
ever reported as 0 to stand in for "not measured". 0 appears only where 0 was
observed (an absent -wal file really is 0 bytes of WAL).

SAFETY RULE (non-negotiable)
============================
This module never opens the main store writable. Any path whose basename is in
``PROTECTED_DB_BASENAMES`` is refused for a writable connection, for ATTACH,
for checkpointing and for schema creation, and the refusal is an exception, not
a warning. There is deliberately no override flag. The meter's own reads use
``mode=ro`` + ``PRAGMA query_only=1``.

USAGE
=====
    from pit_replay_meter import Meter

    with Meter(pilot_db_path, sample_hz=5) as m:
        for as_of in dates:
            ok, free, why = m.free_space_ok()
            if not ok:
                break
            with m.table_scope("pit_feature"):
                ...write only pit_feature, then commit...
            m.checkpoint()                    # reads (busy, log_pages, checkpointed)
            m.mark(as_of, entity_dates=n)
    report = m.report()

Stdlib only. No network. Nothing here fits a model, optimises a weight, or
promotes anything.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "METER_VERSION",
    "GIB",
    "FREE_FLOOR_DEFAULT",
    "FULL_GRID_ENTITY_DATES",
    "PILOT_DATES",
    "MODEL_VERSION",
    "PROTECTED_DB_BASENAMES",
    "ProtectedStoreError",
    "FreeSpaceFloorBreach",
    "Meter",
    "dbstat_probe",
    "free_space",
    "pilot_ddl_plan",
    "pilot_fk_requirements",
    "compare_schema",
    "preflight_manifest",
    "store_identity",
    "REPLAY_TABLES",
    "build_pilot_db",
    "project_full_replay",
    "validate",
]

METER_VERSION = "pit_replay_meter/1.0"

GIB = 1024 ** 3
MIB = 1024 ** 2

#: The run aborts below this. Measured context: the volume holds 237.4 GiB and
#: had 9.74 GiB free when this module was written, with an 8.971 GiB store on it.
FREE_FLOOR_DEFAULT = int(7.0 * GIB)

#: Measured, not assumed: 1,238,663 distinct (as_of_date, entity_id) pairs over
#: the 165 month-end as-of dates 2013-01-31..2026-09-18.
FULL_GRID_ENTITY_DATES = 1_238_663
FULL_GRID_DATES = 165

#: The pilot is four COMPLETE cross-sections, not a sample of companies.
PILOT_DATES: Tuple[Tuple[str, int], ...] = (
    ("2014-06-30", 8132),
    ("2019-06-28", 7102),
    ("2022-06-30", 8033),
    ("2026-06-30", 7041),
)
PILOT_ENTITY_DATES = sum(n for _, n in PILOT_DATES)  # 30,308 == 2.45% of the grid

MODEL_VERSION = "equity_shaffer_v2_pit_SURVIVOR_ONLY_DIAGNOSTIC"

#: Never opened writable by anything in this module. No override flag exists.
PROTECTED_DB_BASENAMES = frozenset({"shafferfineval_pit.db"})

#: Per-table byte method labels. These strings go into the report verbatim so a
#: reader never has to guess which one produced a number.
PER_TABLE_DBSTAT = "DBSTAT_EXACT"
PER_TABLE_PAGE_DELTA = "PAGE_COUNT_DELTA_SERIALIZED"
PER_TABLE_UNMEASURED = "UNMEASURED"
LOGICAL_PAYLOAD = "LOGICAL_PAYLOAD_NOT_STORAGE"


class ProtectedStoreError(RuntimeError):
    """Raised on any attempt to open a protected store writable."""


class FreeSpaceFloorBreach(RuntimeError):
    """Raised by ``Meter.require_free_space()`` when the floor is breached."""


# --------------------------------------------------------------------------
# Small primitives. Each returns (value, reason) so "absent" and "unmeasurable"
# never collapse into the same number.
# --------------------------------------------------------------------------

def _utc() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _file_size(path: str) -> Tuple[Optional[int], Optional[str]]:
    """Bytes on disk, or (None, reason).

    A missing file is 0 and NOT unknown: an absent ``-wal`` really is zero
    bytes of write-ahead log. Any other OSError is unknown, with the errno.
    """
    try:
        return os.path.getsize(path), None
    except FileNotFoundError:
        return 0, None
    except OSError as exc:
        return None, f"getsize({os.path.basename(path)}) failed: {exc.__class__.__name__}: {exc}"


def free_space(path: str) -> Tuple[Optional[int], Optional[str]]:
    """Free bytes on the volume holding ``path``, or (None, reason)."""
    probe = path if os.path.isdir(path) else (os.path.dirname(os.path.abspath(path)) or ".")
    try:
        return shutil.disk_usage(probe).free, None
    except OSError as exc:
        return None, f"disk_usage({probe}) failed: {exc.__class__.__name__}: {exc}"


def _volume_of(path: str) -> str:
    drive, _ = os.path.splitdrive(os.path.abspath(path))
    return drive or os.path.sep


def is_protected(path: str) -> bool:
    """True when ``path`` is a store this module refuses to open writable."""
    return os.path.basename(os.path.abspath(path)).lower() in {
        n.lower() for n in PROTECTED_DB_BASENAMES
    }


def assert_write_allowed(path: str) -> None:
    if is_protected(path):
        raise ProtectedStoreError(
            f"refusing a writable connection to {path!r}: its basename is in "
            f"PROTECTED_DB_BASENAMES. The main store is READ-ONLY, always. "
            f"Open it as sqlite3.connect('file:...?mode=ro', uri=True) with "
            f"PRAGMA query_only=1."
        )


def connect_readonly(path: str) -> sqlite3.Connection:
    """A read-only, query_only connection. The only way this module reads."""
    conn = sqlite3.connect(f"file:{_uri_path(path)}?mode=ro", uri=True,
                           check_same_thread=False)
    conn.execute("PRAGMA query_only=1")
    return conn


def _uri_path(path: str) -> str:
    p = os.path.abspath(path).replace("\\", "/")
    if len(p) > 1 and p[1] == ":":          # Windows drive letter
        p = "/" + p
    return p.replace("?", "%3f").replace("#", "%23")


# --------------------------------------------------------------------------
# dbstat: investigated, not assumed
# --------------------------------------------------------------------------

def dbstat_probe(conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """Does THIS Python's sqlite3 have the `dbstat` virtual table?

    Answered by running a query, not by reading a version number. The compile
    options are read too, as corroboration -- ``ENABLE_DBSTAT_VTAB`` is the
    flag that decides it, and its absence from ``PRAGMA compile_options`` is
    the reason the query fails.
    """
    result: Dict[str, Any] = {
        "available": False,
        "reason": None,
        "probed_on": "caller connection" if conn is not None else "in-memory database",
        "sqlite_version": sqlite3.sqlite_version,
        "python_version": sys.version.split()[0],
        "compile_option_present": None,
    }
    owned = conn is None
    probe = conn if conn is not None else sqlite3.connect(":memory:")
    try:
        try:
            opts = [r[0] for r in probe.execute("PRAGMA compile_options").fetchall()]
            result["compile_option_present"] = any("DBSTAT" in o.upper() for o in opts)
        except sqlite3.Error as exc:
            result["compile_option_present"] = None
            result["compile_options_reason"] = f"{exc.__class__.__name__}: {exc}"
        try:
            if owned:
                probe.execute("CREATE TABLE IF NOT EXISTS _dbstat_probe(x)")
                probe.execute("INSERT INTO _dbstat_probe(x) VALUES ('x')")
            probe.execute("SELECT name, pgsize FROM dbstat LIMIT 1").fetchall()
            result["available"] = True
        except sqlite3.Error as exc:
            result["available"] = False
            result["reason"] = f"{exc.__class__.__name__}: {exc}"
    finally:
        if owned:
            probe.close()
    if result["available"]:
        result["reason"] = None
    return result


# --------------------------------------------------------------------------
# The meter
# --------------------------------------------------------------------------

class _Sample:
    __slots__ = ("t", "wal", "shm", "journal", "free", "temp", "mark", "source")

    def __init__(self, t, wal, shm, journal, free, temp, mark, source):
        self.t = t
        self.wal = wal
        self.shm = shm
        self.journal = journal
        self.free = free
        self.temp = temp
        self.mark = mark
        self.source = source


class Meter:
    """Measures a write workload. Never opens a protected store writable.

    Parameters
    ----------
    db_path
        The database being written. For the pilot this is
        ``shafferfineval_pilot.db``. A protected basename is accepted for
        READ-ONLY measurement but every writable operation (checkpoint,
        schema build) will refuse.
    sample_hz
        Background sampling rate for WAL / free space / temp files. 0 disables
        sampling, in which case every sampled field reports UNKNOWN with that
        as its reason rather than 0.
    free_floor_bytes
        The abort floor. ``free_space_ok()`` and ``require_free_space()`` read
        it; the meter itself never kills the workload, it reports.
    conn
        An existing WRITER connection to reuse for PRAGMA reads and
        checkpoints. Optional; without it the meter opens its own read-only
        connection for reads and its own writable one only when
        ``checkpoint()`` is called on a non-protected path.
    temp_scan_every
        Scan the temp directory on every Nth sample. A full scandir at 5 Hz is
        wasteful; the default samples temp space at sample_hz/4.
    """

    def __init__(
        self,
        db_path: str,
        *,
        sample_hz: float = 5.0,
        free_floor_bytes: int = FREE_FLOOR_DEFAULT,
        conn: Optional[sqlite3.Connection] = None,
        temp_dir: Optional[str] = None,
        temp_scan_every: int = 4,
        keep_samples: bool = True,
        max_samples: int = 400_000,
        exclusivity_tables: Optional[Sequence[str]] = None,
        label: Optional[str] = None,
    ) -> None:
        self.db_path = os.path.abspath(db_path)
        self.db_dir = os.path.dirname(self.db_path) or "."
        self.wal_path = self.db_path + "-wal"
        self.shm_path = self.db_path + "-shm"
        self.journal_path = self.db_path + "-journal"
        self.label = label
        self.sample_hz = float(sample_hz)
        self.interval = (1.0 / self.sample_hz) if self.sample_hz > 0 else None
        self.free_floor_bytes = int(free_floor_bytes)
        self.temp_dir = os.path.abspath(temp_dir or tempfile.gettempdir())
        self.temp_scan_every = max(1, int(temp_scan_every))
        self.keep_samples = bool(keep_samples)
        self.max_samples = int(max_samples)
        # Which tables the exclusivity check counts. None = every table, which
        # is correct but costs a full count(*) per table per scope boundary.
        # Name the replay tables here once pit_feature is large.
        self.exclusivity_tables = (
            list(exclusivity_tables) if exclusivity_tables is not None else None)

        self._external_conn = conn
        self._ro: Optional[sqlite3.Connection] = None
        self._ro_reason: Optional[str] = None
        self._rw: Optional[sqlite3.Connection] = None
        self._closed = False
        self._dbstat_cache: Optional[Dict[str, Any]] = None

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._samples: List[_Sample] = []
        self._n_samples = 0
        self._sample_errors: List[str] = []

        self._current_mark: Optional[str] = None
        self._marks: List[Dict[str, Any]] = []
        self._checkpoints: List[Dict[str, Any]] = []
        self._table_scopes: List[Dict[str, Any]] = []
        self._notes: List[str] = []
        self._rebaselines: List[Dict[str, Any]] = []
        self._unknown: Dict[str, str] = {}
        self._entity_dates = 0
        self._rows_written: Dict[str, int] = {}

        # peaks / low-water marks, each with the time and mark it occurred under
        self._wal_peak: Optional[int] = None
        self._wal_peak_at: Optional[float] = None
        self._wal_peak_mark: Optional[str] = None
        self._wal_peak_source: Optional[str] = None
        self._wal_peak_sampler_only: Optional[int] = None
        self._shm_peak: Optional[int] = None
        self._journal_peak: Optional[int] = None
        self._temp_peak: Optional[int] = None
        self._temp_peak_files: Optional[List[Tuple[str, int]]] = None
        self._temp_scanned = False
        self._free_low: Optional[int] = None
        self._free_low_at: Optional[float] = None
        self._free_low_mark: Optional[str] = None
        self._floor_breached = False
        self._floor_breach_at: Optional[str] = None

        self._shm_final: Optional[int] = None
        self._t0: Optional[float] = None
        self._t1: Optional[float] = None
        self._started_at: Optional[str] = None
        self._ended_at: Optional[str] = None

        self.dbstat = dbstat_probe()
        self.per_table_method = (
            PER_TABLE_DBSTAT if self.dbstat["available"] else PER_TABLE_PAGE_DELTA
        )

        self._page_size: Optional[int] = None
        self._before: Dict[str, Any] = {}
        self._after: Dict[str, Any] = {}

    # -- connections -------------------------------------------------------

    def _read_conn(self) -> Optional[sqlite3.Connection]:
        """A connection for PRAGMA/SELECT. Read-only unless the caller gave us one."""
        if self._external_conn is not None:
            return self._external_conn
        if self._ro is not None:
            return self._ro
        if self._closed:
            # Do NOT reopen. close() means closed, and a connection silently
            # reopened here outlives the meter and holds the file: on Windows
            # that leaves an undeletable -wal behind, and on a live store a
            # lingering reader blocks the WAL checkpointer.
            return None
        if self._ro_reason is not None:
            return None
        try:
            self._ro = connect_readonly(self.db_path)
            return self._ro
        except sqlite3.Error as exc:
            # A WAL database with no live writer and no -shm cannot always be
            # opened read-only. That is a real limitation, reported as one.
            self._ro_reason = (
                f"read-only connect to {os.path.basename(self.db_path)} failed: "
                f"{exc.__class__.__name__}: {exc}"
            )
            return None

    def _write_conn(self) -> sqlite3.Connection:
        """A writable connection. REFUSES a protected store, with no override."""
        assert_write_allowed(self.db_path)
        if self._external_conn is not None:
            return self._external_conn
        if self._rw is None:
            self._rw = sqlite3.connect(self.db_path, check_same_thread=False)
        return self._rw

    # -- primitive reads ---------------------------------------------------

    def _pragma(self, name: str) -> Tuple[Optional[int], Optional[str]]:
        conn = self._read_conn()
        if conn is None:
            return None, self._ro_reason or "no readable connection"
        try:
            row = conn.execute(f"PRAGMA {name}").fetchone()
        except sqlite3.Error as exc:
            return None, f"PRAGMA {name} failed: {exc.__class__.__name__}: {exc}"
        if row is None:
            return None, f"PRAGMA {name} returned no row"
        return int(row[0]), None

    def page_size(self) -> Optional[int]:
        if self._page_size is None:
            value, reason = self._pragma("page_size")
            if value is None:
                self._unknown.setdefault("db.page_size", reason or "unknown")
            self._page_size = value
        return self._page_size

    def db_snapshot(self) -> Dict[str, Any]:
        """File size, page_count, page bytes, freelist -- all four, right now."""
        size, size_reason = _file_size(self.db_path)
        wal, wal_reason = _file_size(self.wal_path)
        pages, pages_reason = self._pragma("page_count")
        free_pages, free_reason = self._pragma("freelist_count")
        psize = self.page_size()
        snap: Dict[str, Any] = {
            "at": _utc(),
            "file_bytes": size,
            "file_bytes_reason": size_reason,
            "wal_bytes": wal,
            "wal_bytes_reason": wal_reason,
            "page_size": psize,
            "page_count": pages,
            "page_count_reason": pages_reason,
            "page_bytes": (pages * psize) if (pages is not None and psize) else None,
            "freelist_count": free_pages,
            "freelist_reason": free_reason,
            "freelist_bytes": (free_pages * psize)
            if (free_pages is not None and psize) else None,
        }
        fb, fb_reason = free_space(self.db_path)
        snap["free_bytes"] = fb
        snap["free_bytes_reason"] = fb_reason
        return snap

    def row_counts(self, tables: Optional[Iterable[str]] = None) -> Dict[str, Optional[int]]:
        """count(*) per table. Cheap on a fresh pilot DB; never run on the store."""
        conn = self._read_conn()
        if conn is None:
            return {}
        if tables is None:
            try:
                tables = [
                    r[0] for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                    ).fetchall()
                ]
            except sqlite3.Error as exc:
                self._unknown.setdefault(
                    "work.row_counts", f"sqlite_master read failed: {exc}")
                return {}
        counts: Dict[str, Optional[int]] = {}
        for t in tables:
            try:
                counts[t] = int(conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0])
            except sqlite3.Error as exc:
                counts[t] = None
                self._unknown.setdefault(
                    f"work.row_counts.{t}", f"count(*) failed: {exc}")
        return counts

    def _exclusivity_counts(self) -> Dict[str, Optional[int]]:
        """Row counts used only to prove a table_scope was exclusive.

        Narrow this with ``exclusivity_tables=`` once a table is large: a
        ``count(*)`` is a full scan of its btree, and the scope takes one per
        table on entry and on exit.
        """
        return self.row_counts(self.exclusivity_tables)

    # -- sampling ----------------------------------------------------------

    def _scan_temp(self) -> Tuple[Optional[int], Optional[List[Tuple[str, int]]], Optional[str]]:
        """Bytes of SQLite scratch in the temp directory (``etilqs_*``)."""
        total = 0
        files: List[Tuple[str, int]] = []
        try:
            with os.scandir(self.temp_dir) as it:
                for entry in it:
                    name = entry.name
                    if not name.startswith("etilqs_"):
                        continue
                    try:
                        size = entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
                    total += size
                    files.append((name, size))
        except OSError as exc:
            return None, None, f"scandir({self.temp_dir}) failed: {exc}"
        return total, files, None

    def _take_sample(self, force_temp: bool = False, source: str = "sampler") -> None:
        """One observation of every sampled quantity, from ANY caller.

        The background thread is not the only observer, and it must not be:
        at 5 Hz a 0.1 s write burst is invisible, and a WAL peak that the
        checkpoint path saw directly at 1.19 MB would otherwise be reported as
        the 33 KB the thread happened to catch. Every boundary the meter knows
        about -- scope entry and exit, checkpoint, mark, start, stop -- takes a
        sample too, tagged with where it came from, and the peak is the maximum
        over ALL of them. It remains a LOWER BOUND on the true peak: nothing is
        observed between observations, and that caveat ships with the number.
        """
        now = time.monotonic()
        wal, wal_reason = _file_size(self.wal_path)
        shm, shm_reason = _file_size(self.shm_path)
        journal, journal_reason = _file_size(self.journal_path)
        fb, fb_reason = free_space(self.db_path)

        temp: Optional[int] = None
        temp_files: Optional[List[Tuple[str, int]]] = None
        want_temp = force_temp or (self._n_samples % self.temp_scan_every == 0)
        if want_temp:
            temp, temp_files, temp_reason = self._scan_temp()
            if temp is None and temp_reason:
                if len(self._sample_errors) < 20:
                    self._sample_errors.append(temp_reason)
            else:
                self._temp_scanned = True

        with self._lock:
            self._n_samples += 1
            for reason in (wal_reason, shm_reason, journal_reason, fb_reason):
                if reason and len(self._sample_errors) < 20:
                    self._sample_errors.append(reason)
            if wal is not None and (self._wal_peak is None or wal > self._wal_peak):
                self._wal_peak = wal
                self._wal_peak_at = now
                self._wal_peak_mark = self._current_mark
                self._wal_peak_source = source
            if wal is not None and source == "sampler" and (
                    self._wal_peak_sampler_only is None
                    or wal > self._wal_peak_sampler_only):
                self._wal_peak_sampler_only = wal
            if shm is not None and (self._shm_peak is None or shm > self._shm_peak):
                self._shm_peak = shm
            if journal is not None and (self._journal_peak is None or journal > self._journal_peak):
                self._journal_peak = journal
            if temp is not None and (self._temp_peak is None or temp > self._temp_peak):
                self._temp_peak = temp
                self._temp_peak_files = temp_files
            if fb is not None:
                if self._free_low is None or fb < self._free_low:
                    self._free_low = fb
                    self._free_low_at = now
                    self._free_low_mark = self._current_mark
                if fb < self.free_floor_bytes and not self._floor_breached:
                    self._floor_breached = True
                    self._floor_breach_at = _utc()
            if self.keep_samples and len(self._samples) < self.max_samples:
                self._samples.append(
                    _Sample(now, wal, shm, journal, fb, temp,
                            self._current_mark, source))

    def _sample_loop(self) -> None:
        assert self.interval is not None
        while not self._stop.is_set():
            start = time.monotonic()
            try:
                self._take_sample()
            except Exception as exc:                       # never kill the workload
                if len(self._sample_errors) < 20:
                    self._sample_errors.append(f"sampler: {exc.__class__.__name__}: {exc}")
            wait = self.interval - (time.monotonic() - start)
            self._stop.wait(wait if wait > 0 else 0)

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> "Meter":
        self._t0 = time.monotonic()
        self._started_at = _utc()
        self._before = self.db_snapshot()
        self._take_sample(force_temp=True, source="start")
        if self.interval is None:
            self._unknown["wal.peak_bytes"] = (
                "sampling disabled (sample_hz=0): WAL peak was never observed")
            self._unknown["free_space.low_water_bytes"] = (
                "sampling disabled (sample_hz=0): free space was not sampled")
            self._unknown["temp.peak_bytes"] = (
                "sampling disabled (sample_hz=0): temp space was not sampled")
        else:
            self._thread = threading.Thread(
                target=self._sample_loop, name="pit-replay-meter", daemon=True)
            self._thread.start()
        return self

    def stop(self) -> "Meter":
        if self._thread is not None:
            self._stop.set()
            self._thread.join(timeout=5.0)
            if self._thread.is_alive():
                self._notes.append("sampler thread did not join within 5s")
            self._thread = None
        self._take_sample(force_temp=True, source="stop")
        self._t1 = time.monotonic()
        self._ended_at = _utc()
        self._after = self.db_snapshot()
        self._shm_final = _file_size(self.shm_path)[0]
        # Cache the exact per-table numbers BEFORE letting the connection go,
        # so report() is a pure function of what was already measured.
        if self.dbstat["available"]:
            self._dbstat_cache = self.table_bytes_dbstat(None)
        self._close_own()
        return self

    def __enter__(self) -> "Meter":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.stop()
        if exc_type is not None:
            self._notes.append(
                f"workload raised {exc_type.__name__}: {exc} -- the report covers "
                f"only what completed before it")
        return False

    def _close_own(self) -> None:
        """Close the connections the METER opened. Never the caller's."""
        for conn in (self._ro, self._rw):
            if conn is not None:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
        self._ro = None
        self._rw = None
        self._closed = True

    def close(self) -> None:
        """Idempotent. Safe to call inside or after the ``with`` block."""
        self._close_own()

    # -- workload annotation ----------------------------------------------

    def begin(self, label: str) -> None:
        """Name the phase now in force, so a peak can be attributed to it.

        Without this, a peak reached during the FIRST date is attributed to no
        mark at all, because ``mark()`` only runs after the work is done.
        """
        with self._lock:
            self._current_mark = label

    @contextmanager
    def phase(self, label: str, *, entity_dates: Optional[int] = None):
        """``begin(label)`` ... work ... ``mark(label)``, as one block."""
        self.begin(label)
        try:
            yield self
        finally:
            self.mark(label, entity_dates=entity_dates)

    def mark(self, label: str, *, entity_dates: Optional[int] = None,
             rows: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
        """Record a phase boundary -- typically one as-of date -- and snapshot."""
        if entity_dates is not None:
            self._entity_dates += int(entity_dates)
        if rows:
            for table, n in rows.items():
                self._rows_written[table] = self._rows_written.get(table, 0) + int(n)
        self._take_sample(force_temp=True, source=f"mark:{label}")
        snap = self.db_snapshot()
        with self._lock:
            entry = {
                "label": label,
                "at": _utc(),
                "elapsed_s": round(time.monotonic() - self._t0, 3) if self._t0 else None,
                "entity_dates_this_mark": entity_dates,
                "entity_dates_cumulative": self._entity_dates,
                "db_file_bytes": snap["file_bytes"],
                "db_page_bytes": snap["page_bytes"],
                "wal_bytes": snap["wal_bytes"],
                "free_bytes": snap["free_bytes"],
                "wal_peak_bytes_so_far": self._wal_peak,
                "free_low_water_so_far": self._free_low,
            }
            self._marks.append(entry)
            self._current_mark = label
        return entry

    def rebaseline(self, reason: str) -> Dict[str, Any]:
        """Move the "before" snapshot to NOW, and say why in the report.

        THE SEED IS NOT THE REPLAY. A fresh pilot DB has foreign keys pointing
        at pit_entity, pit_listing, pit_peer_set and pit_replay_run, so the
        parent rows must be seeded before the first pit_feature insert -- and
        those bytes are a one-time cost, not part of BytesPerEntityDate.
        Seed, call this, then replay: growth is then measured from the seeded
        state and the seed cost is reported separately as ``seed_bytes``.
        """
        old = dict(self._before)
        self._take_sample(force_temp=True, source="rebaseline")
        self._before = self.db_snapshot()
        seed_bytes = None
        if (old.get("file_bytes") is not None
                and self._before.get("file_bytes") is not None):
            seed_bytes = self._before["file_bytes"] - old["file_bytes"]
        entry = {
            "reason": reason,
            "at": _utc(),
            "previous_file_bytes": old.get("file_bytes"),
            "new_baseline_file_bytes": self._before.get("file_bytes"),
            "seed_bytes": seed_bytes,
            "seed_bytes_reason": (
                None if seed_bytes is not None
                else "a file size was unknown at one of the two points"),
        }
        self._rebaselines.append(entry)
        self._notes.append(
            f"rebaselined at {entry['at']}: {reason} (seed cost {seed_bytes} B); "
            f"growth and bytes-per-entity-date are measured from HERE, so the "
            f"seed is excluded from the replay's per-row cost")
        return entry

    def add_entity_dates(self, n: int) -> int:
        self._entity_dates += int(n)
        return self._entity_dates

    def note(self, text: str) -> None:
        self._notes.append(text)

    # -- the free-space guard ---------------------------------------------

    def free_space_ok(self) -> Tuple[bool, Optional[int], Optional[str]]:
        """(ok, free_bytes, reason). ok is False when free space is UNKNOWN.

        An unmeasurable free-space reading is treated as a failure, not as
        room. Call this before every date.
        """
        fb, reason = free_space(self.db_path)
        if fb is None:
            return False, None, reason or "free space unknown"
        if fb < self.free_floor_bytes:
            self._floor_breached = True
            self._floor_breach_at = self._floor_breach_at or _utc()
            return False, fb, (
                f"free {fb / GIB:.3f} GiB is below the "
                f"{self.free_floor_bytes / GIB:.3f} GiB floor")
        return True, fb, None

    def require_free_space(self) -> int:
        ok, fb, reason = self.free_space_ok()
        if not ok:
            raise FreeSpaceFloorBreach(reason or "free space below floor")
        return int(fb)  # type: ignore[arg-type]

    # -- checkpointing -----------------------------------------------------

    def checkpoint(self, mode: str = "TRUNCATE") -> Dict[str, Any]:
        """``PRAGMA wal_checkpoint(mode)`` with the RETURN VALUE READ.

        Records (busy, log_pages, checkpointed) exactly as SQLite returned
        them, plus the WAL size before and after and the DB file size after.
        ``busy=1`` with ``log_pages>0`` and ``checkpointed=0`` is flagged as
        the busy-refusal signature -- a reader is holding the checkpointer off,
        which is how a WAL grows without bound.
        """
        mode = mode.upper()
        if mode not in ("PASSIVE", "FULL", "RESTART", "TRUNCATE"):
            raise ValueError(f"unknown checkpoint mode {mode!r}")
        # Observe BEFORE the checkpoint: this is usually the largest the WAL
        # ever gets, and it is exactly the sample a 5 Hz thread misses.
        self._take_sample(source="checkpoint:pre")
        wal_before, wal_before_reason = _file_size(self.wal_path)
        t0 = time.monotonic()
        entry: Dict[str, Any] = {
            "mode": mode,
            "at": _utc(),
            "mark": self._current_mark,
            "wal_bytes_before": wal_before,
            "wal_bytes_before_reason": wal_before_reason,
            "busy": None,
            "log_pages": None,
            "checkpointed": None,
            "return_read": False,
            "busy_refusal": None,
            "error": None,
        }
        try:
            conn = self._write_conn()
        except ProtectedStoreError as exc:
            entry["error"] = str(exc)
            entry["reason"] = "protected store: checkpoint refused"
            self._checkpoints.append(entry)
            return entry
        try:
            row = conn.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
        except sqlite3.Error as exc:
            entry["error"] = f"{exc.__class__.__name__}: {exc}"
            self._checkpoints.append(entry)
            return entry
        if row is None or len(row) < 3:
            entry["error"] = f"wal_checkpoint returned {row!r}, not a 3-tuple"
        else:
            busy, log_pages, checkpointed = int(row[0]), int(row[1]), int(row[2])
            entry.update({
                "busy": busy,
                "log_pages": log_pages,
                "checkpointed": checkpointed,
                "return_read": True,
                "busy_refusal": bool(busy == 1 and log_pages > 0 and checkpointed == 0),
                "fully_checkpointed": bool(
                    busy == 0 and log_pages >= 0 and checkpointed == log_pages),
            })
        entry["elapsed_s"] = round(time.monotonic() - t0, 4)
        wal_after, wal_after_reason = _file_size(self.wal_path)
        entry["wal_bytes_after"] = wal_after
        entry["wal_bytes_after_reason"] = wal_after_reason
        db_after, db_after_reason = _file_size(self.db_path)
        entry["db_file_bytes_after"] = db_after
        entry["db_file_bytes_after_reason"] = db_after_reason
        pages, pages_reason = self._pragma("page_count")
        psize = self.page_size()
        entry["page_count_after"] = pages
        entry["page_bytes_after"] = (pages * psize) if (pages is not None and psize) else None
        if pages is None and pages_reason:
            entry["page_count_after_reason"] = pages_reason
        entry["return_semantics"] = (
            "SQLite returns (busy, log_pages, checkpointed). In TRUNCATE and "
            "RESTART modes a SUCCESSFUL checkpoint resets the log, so (0, 0, 0) "
            "means 'done and the WAL was reset', NOT 'nothing happened' -- read "
            "wal_bytes_before/after to see the work. busy=1 with log_pages>0 and "
            "checkpointed=0 is the busy-refusal signature: a reader is holding "
            "the checkpointer off and the WAL will keep growing.")
        self._checkpoints.append(entry)
        if entry.get("busy_refusal"):
            self._notes.append(
                f"BUSY-REFUSAL at {entry['at']} under mark {entry['mark']!r}: "
                f"busy=1 log_pages={entry['log_pages']} checkpointed=0 -- a reader "
                f"is blocking the checkpointer and the WAL will keep growing")
        self._take_sample(force_temp=True, source="checkpoint:post")
        return entry

    # -- per-table bytes ---------------------------------------------------

    @contextmanager
    def table_scope(self, table: str, *, expect_exclusive: bool = True):
        """Attribute the page_count delta across this block to one table.

        THE FALLBACK METHOD, used when `dbstat` is absent. It is only honest
        if exactly one table grows inside the block, so the scope measures
        every table's row count before and after and reports whether
        attribution was in fact exclusive. It also requires the writes to be
        COMMITTED before the block exits -- an open transaction's pages are in
        the WAL, not yet in page_count as this read-only connection sees it.

        With `dbstat` available the scope still runs (the delta is a useful
        cross-check) and exact per-btree bytes are read on exit as well.
        """
        before_pages, before_reason = self._pragma("page_count")
        before_free, _ = self._pragma("freelist_count")
        before_counts = self._exclusivity_counts() if expect_exclusive else {}
        self._take_sample(source=f"scope:{table}:enter")
        before_wal, _ = _file_size(self.wal_path)
        t0 = time.monotonic()
        entry: Dict[str, Any] = {
            "table": table,
            "method": self.per_table_method,
            "mark": self._current_mark,
            "page_count_before": before_pages,
            "page_count_before_reason": before_reason,
        }
        try:
            yield entry
        finally:
            after_pages, after_reason = self._pragma("page_count")
            after_free, _ = self._pragma("freelist_count")
            after_counts = self._exclusivity_counts() if expect_exclusive else {}
            self._take_sample(source=f"scope:{table}:exit")
            after_wal, _ = _file_size(self.wal_path)
            psize = self.page_size()
            entry["page_count_after"] = after_pages
            entry["page_count_after_reason"] = after_reason
            entry["elapsed_s"] = round(time.monotonic() - t0, 4)
            entry["wal_bytes_delta"] = (
                after_wal - before_wal
                if (after_wal is not None and before_wal is not None) else None)
            if before_pages is None or after_pages is None or not psize:
                entry["bytes"] = None
                entry["bytes_reason"] = (
                    before_reason or after_reason
                    or "page_size unavailable; page-delta attribution impossible")
            else:
                entry["pages_delta"] = after_pages - before_pages
                entry["bytes"] = (after_pages - before_pages) * psize
                entry["freelist_delta"] = (
                    after_free - before_free
                    if (after_free is not None and before_free is not None) else None)
            grew: Dict[str, int] = {}
            for name, after_n in after_counts.items():
                before_n = before_counts.get(name)
                if after_n is None or before_n is None:
                    continue
                if after_n != before_n:
                    grew[name] = after_n - before_n
            if expect_exclusive and after_counts:
                entry["rows_delta"] = grew.get(table)
                entry["other_tables_changed"] = {
                    k: v for k, v in grew.items() if k != table}
                entry["exclusive"] = not entry["other_tables_changed"]
                if not entry["exclusive"]:
                    self._notes.append(
                        f"table_scope({table!r}) was NOT exclusive: also changed "
                        f"{entry['other_tables_changed']} -- its byte attribution "
                        f"is an upper bound for {table}, not a measurement of it")
            else:
                entry["exclusive"] = None
                entry["exclusive_reason"] = (
                    "exclusivity not checked (expect_exclusive=False)"
                    if not expect_exclusive else "row counts unavailable")
            rows = entry.get("rows_delta")
            if entry.get("bytes") is not None and rows:
                entry["bytes_per_row"] = entry["bytes"] / rows
            else:
                entry["bytes_per_row"] = None
                entry["bytes_per_row_reason"] = (
                    "no committed row delta observed inside the scope "
                    "(uncommitted writes are not visible to the read-only meter)"
                ) if not rows else "byte delta unavailable"
            if self.dbstat["available"]:
                exact = self.table_bytes_dbstat(table)
                entry["dbstat_bytes"] = exact.get("bytes")
                entry["dbstat_detail"] = exact.get("btrees")
            self._table_scopes.append(entry)
            if rows:
                self._rows_written[table] = self._rows_written.get(table, 0) + int(rows)

    def table_bytes_dbstat(self, table: Optional[str] = None) -> Dict[str, Any]:
        """Exact per-btree bytes via `dbstat`, or a reason why not.

        Returns bytes for the table's own btree AND for every index on it,
        because an index is storage the budget has to carry too.
        """
        if not self.dbstat["available"]:
            return {
                "bytes": None,
                "method": PER_TABLE_UNMEASURED,
                "reason": f"dbstat unavailable: {self.dbstat['reason']}",
            }
        conn = self._read_conn()
        if conn is None:
            return {"bytes": None, "method": PER_TABLE_UNMEASURED,
                    "reason": self._ro_reason or "no readable connection"}
        try:
            owners = {
                r[0]: (r[1] or r[0])
                for r in conn.execute(
                    "SELECT name, COALESCE(tbl_name, name) FROM sqlite_master "
                    "WHERE type IN ('table','index')").fetchall()
            }
            rows = conn.execute(
                "SELECT name, SUM(pgsize), COUNT(*) FROM dbstat GROUP BY name"
            ).fetchall()
        except sqlite3.Error as exc:
            return {"bytes": None, "method": PER_TABLE_UNMEASURED,
                    "reason": f"dbstat query failed: {exc}"}
        per_owner: Dict[str, Dict[str, Any]] = {}
        for name, nbytes, npages in rows:
            owner = owners.get(name, name)
            slot = per_owner.setdefault(
                owner, {"bytes": 0, "pages": 0, "btrees": {}, "method": PER_TABLE_DBSTAT})
            slot["bytes"] += int(nbytes or 0)
            slot["pages"] += int(npages or 0)
            slot["btrees"][name] = int(nbytes or 0)
        if table is None:
            return {"method": PER_TABLE_DBSTAT, "by_table": per_owner}
        return per_owner.get(
            table,
            {"bytes": None, "method": PER_TABLE_UNMEASURED,
             "reason": f"{table!r} has no pages in dbstat (empty or absent)"})

    def logical_payload_bytes(self, table: str) -> Dict[str, Any]:
        """SUM(octet_length(col)) over a table. A CROSS-CHECK, NOT STORAGE.

        This is the payload the rows carry, excluding SQLite's per-row header,
        per-page overhead, index copies and freelist slack. It is always
        SMALLER than the storage the table occupies. Labelled so, always.
        """
        conn = self._read_conn()
        if conn is None:
            return {"bytes": None, "method": LOGICAL_PAYLOAD,
                    "reason": self._ro_reason or "no readable connection"}
        try:
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]
            if not cols:
                return {"bytes": None, "method": LOGICAL_PAYLOAD,
                        "reason": f"{table!r} has no columns (absent table)"}
            expr = " + ".join(f'COALESCE(octet_length("{c}"), 0)' for c in cols)
            total, nrows = conn.execute(
                f'SELECT COALESCE(SUM({expr}), 0), COUNT(*) FROM "{table}"').fetchone()
        except sqlite3.Error as exc:
            return {"bytes": None, "method": LOGICAL_PAYLOAD,
                    "reason": f"octet_length scan failed: {exc}"}
        return {
            "bytes": int(total),
            "rows": int(nrows),
            "bytes_per_row": (int(total) / int(nrows)) if nrows else None,
            "method": LOGICAL_PAYLOAD,
            "caveat": "row payload only: excludes row headers, page overhead, "
                      "indexes and freelist slack. ALWAYS an underestimate of storage.",
        }

    # -- the report --------------------------------------------------------

    def per_table_bytes(self) -> Dict[str, Any]:
        """Per-table bytes by whichever method this interpreter can support."""
        out: Dict[str, Any] = {
            "method": self.per_table_method,
            "dbstat_available": self.dbstat["available"],
            "dbstat_reason": self.dbstat["reason"],
            "tables": {},
        }
        if self.dbstat["available"]:
            exact = self._dbstat_cache or self.table_bytes_dbstat(None)
            for table, slot in exact.get("by_table", {}).items():
                out["tables"][table] = {
                    "bytes": slot["bytes"],
                    "pages": slot["pages"],
                    "btrees": slot["btrees"],
                    "method": PER_TABLE_DBSTAT,
                }
        for entry in self._table_scopes:
            slot = out["tables"].setdefault(entry["table"], {})
            slot.setdefault("method", PER_TABLE_PAGE_DELTA)
            prev = slot.get("page_delta_bytes")
            delta = entry.get("bytes")
            slot["page_delta_bytes"] = (
                (prev or 0) + delta if delta is not None else prev)
            if delta is None:
                slot["page_delta_bytes_reason"] = entry.get("bytes_reason")
            slot["scopes"] = slot.get("scopes", 0) + 1
            slot["exclusive"] = entry.get("exclusive")
            slot["rows"] = (slot.get("rows") or 0) + (entry.get("rows_delta") or 0)
            if not self.dbstat["available"]:
                slot["bytes"] = slot["page_delta_bytes"]
                if slot["bytes"] is None:
                    slot["bytes_reason"] = entry.get("bytes_reason")
            if slot.get("bytes") is not None and slot.get("rows"):
                slot["bytes_per_row"] = slot["bytes"] / slot["rows"]
        for table in self._rows_written:
            slot = out["tables"].setdefault(table, {})
            if "bytes" not in slot:
                slot["bytes"] = None
                slot["method"] = PER_TABLE_UNMEASURED
                slot["bytes_reason"] = (
                    "no dbstat on this interpreter and no table_scope was opened "
                    "around this table's writes: its share of the growth is UNKNOWN, "
                    "not zero")
        if not out["tables"]:
            out["reason"] = (
                "no table scopes were opened and dbstat is unavailable: per-table "
                "bytes are UNKNOWN for this workload")
        return out

    def report(self) -> Dict[str, Any]:
        """Everything measured, with a reason for everything that was not."""
        unknown = dict(self._unknown)
        before = self._before or {}
        after = self._after or self.db_snapshot()

        def _delta(key: str, field: str) -> Optional[int]:
            a, b = after.get(key), before.get(key)
            if a is None or b is None:
                unknown[field] = (
                    f"{key} unknown at "
                    f"{'start' if b is None else 'end'}: "
                    f"{before.get(key + '_reason') or after.get(key + '_reason') or 'no reason recorded'}")
                return None
            return a - b

        elapsed = None
        if self._t0 is not None:
            end = self._t1 if self._t1 is not None else time.monotonic()
            elapsed = round(end - self._t0, 3)
        else:
            unknown["timing.elapsed_s"] = "meter was never started"

        # retained size: the DB file AFTER the last successful TRUNCATE
        # checkpoint, which is the only size that means "on disk for good".
        retained_after: Optional[int] = None
        retained_reason: Optional[str] = None
        good = [c for c in self._checkpoints
                if c.get("return_read") and not c.get("busy_refusal")
                and c.get("db_file_bytes_after") is not None]
        if good:
            retained_after = good[-1]["db_file_bytes_after"]
        else:
            retained_reason = (
                "no checkpoint completed with its return value read, so no DB size "
                "is known with the WAL folded in; db.file_bytes_after still includes "
                "whatever the WAL had not yet written back")
            unknown["db.retained_bytes_after_checkpoint"] = retained_reason

        retained_growth: Optional[int] = None
        if retained_after is not None and before.get("file_bytes") is not None:
            retained_growth = retained_after - before["file_bytes"]
        else:
            unknown.setdefault(
                "db.retained_growth_bytes",
                retained_reason or "start file size unknown")

        wal_peak = self._wal_peak
        if wal_peak is None and "wal.peak_bytes" not in unknown:
            unknown["wal.peak_bytes"] = "no WAL size sample succeeded"
        temp_peak = self._temp_peak
        if temp_peak is None and "temp.peak_bytes" not in unknown:
            unknown["temp.peak_bytes"] = (
                "temp directory was never scanned successfully" if not self._temp_scanned
                else "no temp sample succeeded")
        free_low = self._free_low
        if free_low is None and "free_space.low_water_bytes" not in unknown:
            unknown["free_space.low_water_bytes"] = "no free-space sample succeeded"

        bpe: Optional[float] = None
        if retained_growth is not None and self._entity_dates > 0:
            bpe = retained_growth / self._entity_dates
        elif self._entity_dates <= 0:
            unknown["derived.bytes_per_entity_date"] = (
                "no entity-dates were recorded (call mark(..., entity_dates=n))")
        else:
            unknown["derived.bytes_per_entity_date"] = (
                retained_reason or "retained growth unknown")

        sample_stats: Dict[str, Any] = {
            "requested_hz": self.sample_hz,
            "interval_s": self.interval,
            "n_samples": self._n_samples,
            "errors": self._sample_errors or None,
        }
        thread_samples = [s for s in self._samples if s.source == "sampler"]
        sample_stats["n_thread_samples"] = len(thread_samples)
        sample_stats["n_boundary_samples"] = self._n_samples - len(thread_samples)
        if len(thread_samples) > 1:
            ts = [s.t for s in thread_samples]
            spans = [b - a for a, b in zip(ts, ts[1:])]
            sample_stats["achieved_mean_interval_s"] = round(sum(spans) / len(spans), 4)
            sample_stats["max_gap_s"] = round(max(spans), 4)
            if self.interval:
                sample_stats["gaps_over_2x_interval"] = sum(
                    1 for s in spans if s > 2 * self.interval)
        else:
            sample_stats["achieved_mean_interval_s"] = None
            sample_stats["max_gap_s"] = None
            unknown["samples.achieved_mean_interval_s"] = (
                "fewer than two background samples were taken"
                + ("; sampling was disabled (sample_hz=0)" if self.interval is None
                   else "; the workload finished inside one sampling interval"))

        return {
            "meter_version": METER_VERSION,
            "label": self.label,
            "generated_at": _utc(),
            "db_path": self.db_path,
            "volume": _volume_of(self.db_path),
            "protected_store": is_protected(self.db_path),
            "model_version": MODEL_VERSION,
            "method": {
                "per_table_bytes": self.per_table_method,
                "dbstat_available": self.dbstat["available"],
                "dbstat_reason": self.dbstat["reason"],
                "dbstat_compile_option_present": self.dbstat["compile_option_present"],
                "sqlite_version": self.dbstat["sqlite_version"],
                "python_version": self.dbstat["python_version"],
                "fallback_note": (
                    None if self.dbstat["available"] else
                    "dbstat is NOT compiled into this sqlite3. Per-table bytes come "
                    "from page_count deltas taken around a scope in which one table "
                    "is written, and are only as exact as that serialisation. Each "
                    "scope reports whether attribution was exclusive."),
            },
            "db": {
                "page_size": self.page_size(),
                "file_bytes_before": before.get("file_bytes"),
                "file_bytes_after": after.get("file_bytes"),
                "file_growth_bytes": _delta("file_bytes", "db.file_growth_bytes"),
                "page_count_before": before.get("page_count"),
                "page_count_after": after.get("page_count"),
                "page_bytes_before": before.get("page_bytes"),
                "page_bytes_after": after.get("page_bytes"),
                "page_growth_bytes": _delta("page_bytes", "db.page_growth_bytes"),
                "freelist_count_before": before.get("freelist_count"),
                "freelist_count_after": after.get("freelist_count"),
                "freelist_bytes_after": after.get("freelist_bytes"),
                "retained_bytes_after_checkpoint": retained_after,
                "retained_growth_bytes": retained_growth,
                "retained_reason": retained_reason,
                "rebaselines": self._rebaselines or None,
                "seed_bytes_excluded": (
                    sum(r["seed_bytes"] for r in self._rebaselines
                        if r.get("seed_bytes") is not None)
                    if self._rebaselines else None),
                "growth_basis": (
                    "measured from the last rebaseline, so seeded parent rows are "
                    "NOT counted as replay growth"
                    if self._rebaselines else
                    "measured from meter start; if parent rows were seeded inside "
                    "the meter without calling rebaseline(), their bytes ARE "
                    "included here and bytes-per-entity-date is overstated"),
            },
            "wal": {
                "peak_bytes": wal_peak,
                "peak_is_lower_bound": True,
                "peak_caveat":
                    "the maximum over every observation the meter made -- background "
                    "samples plus scope, checkpoint and mark boundaries. Nothing is "
                    "observed BETWEEN observations, so the true peak is >= this. "
                    "Budget with the safety reserve, not with this number alone.",
                "peak_source": self._wal_peak_source,
                "peak_bytes_background_sampler_only": self._wal_peak_sampler_only,
                "peak_at_elapsed_s": (round(self._wal_peak_at - self._t0, 3)
                                      if (self._wal_peak_at and self._t0) else None),
                "peak_under_mark": self._wal_peak_mark,
                "final_bytes": after.get("wal_bytes"),
                "bytes_after_last_checkpoint": (
                    self._checkpoints[-1].get("wal_bytes_after")
                    if self._checkpoints else None),
                "n_checkpoints": len(self._checkpoints),
                "busy_refusals": sum(1 for c in self._checkpoints if c.get("busy_refusal")),
                "checkpoints": self._checkpoints,
            },
            "shm": {"peak_bytes": self._shm_peak,
                    "final_bytes": (self._shm_final if self._closed
                                    else _file_size(self.shm_path)[0])},
            "temp": {
                "dir": self.temp_dir,
                "same_volume_as_db": _volume_of(self.temp_dir) == _volume_of(self.db_path),
                "scanned": self._temp_scanned,
                "etilqs_peak_bytes": temp_peak,
                "etilqs_peak_files": self._temp_peak_files,
                "rollback_journal_peak_bytes": self._journal_peak,
                "peak_bytes": (
                    None if temp_peak is None and self._journal_peak is None
                    else (temp_peak or 0) + (self._journal_peak or 0)),
                "scan_every_n_samples": self.temp_scan_every,
                "peak_is_lower_bound": True,
                "peak_caveat":
                    "etilqs_* scratch files are created and unlinked between scans; "
                    "a short-lived sorter spill can be invisible at this scan rate. "
                    "Treat the number as a floor, and note that this temp directory "
                    "is on the SAME volume as the database when same_volume_as_db "
                    "is true -- temp space and retained growth then compete.",
            },
            "free_space": {
                "floor_bytes": self.free_floor_bytes,
                "before_bytes": before.get("free_bytes"),
                "after_bytes": after.get("free_bytes"),
                "low_water_bytes": free_low,
                "low_water_at_elapsed_s": (round(self._free_low_at - self._t0, 3)
                                           if (self._free_low_at and self._t0) else None),
                "low_water_under_mark": self._free_low_mark,
                "consumed_bytes": (
                    before["free_bytes"] - after["free_bytes"]
                    if (before.get("free_bytes") is not None
                        and after.get("free_bytes") is not None) else None),
                "floor_breached": self._floor_breached,
                "floor_breached_at": self._floor_breach_at,
            },
            "timing": {
                "started_at": self._started_at,
                "ended_at": self._ended_at,
                "elapsed_s": elapsed,
            },
            "work": {
                "entity_dates": self._entity_dates if self._entity_dates else None,
                "entity_dates_reason": (
                    None if self._entity_dates else
                    "no entity-dates recorded; the workload never called "
                    "mark(entity_dates=...) or add_entity_dates()"),
                "rows_written_by_table": self._rows_written or None,
                "marks": self._marks,
                "entity_dates_per_second": (
                    round(self._entity_dates / elapsed, 3)
                    if (elapsed and self._entity_dates) else None),
            },
            "per_table_bytes": self.per_table_bytes(),
            "table_scopes": self._table_scopes,
            "derived": {
                "bytes_per_entity_date": bpe,
                "bytes_per_entity_date_basis": (
                    "retained DB growth after the last clean TRUNCATE checkpoint, "
                    "divided by entity-dates processed" if bpe is not None else None),
            },
            "samples": sample_stats,
            "notes": self._notes,
            "unknown": unknown,
        }


# --------------------------------------------------------------------------
# What a fresh pilot database needs
# --------------------------------------------------------------------------

def pilot_ddl_plan() -> List[Dict[str, str]]:
    """The DDL sources a fresh pilot DB needs, IN ORDER, with why.

    ORDER IS THE INVARIANT. Every CREATE in this project is
    ``CREATE TABLE IF NOT EXISTS``, so a second, differing declaration of the
    same table is not an error -- it is a SILENT NO-OP. ``pit_cohort_stat``
    and ``pit_company_intermediate`` are declared by ``pit_derive`` and must
    be created from there and nowhere else; ``pit_intermediates`` extends them
    by ALTER, and an ALTER against a table that does not exist yet is an error.
    """
    return [
        {"step": "1", "source": "pit_store.SCHEMA",
         "applies": "the 46 base tables: pit_entity, pit_fact, pit_feature, "
                    "pit_score, pit_replay_run, pit_peer_set, ...",
         "why": "the store's own schema; everything else is an extension of it"},
        {"step": "2", "source": "pit_store.MIGRATIONS (via pit_store.apply_migrations)",
         "applies": "pit_listing.instrument_kind, pit_cost_assumption.*, pit_label.*",
         "why": "ADD COLUMN only; guarded by PRAGMA table_info, idempotent"},
        {"step": "3", "source": "pit_derive.PIT_INTERMEDIATE_DDL",
         "applies": "pit_cohort_stat, pit_company_intermediate",
         "why": "these two are declared THERE. Redeclaring them elsewhere would "
                "silently no-op under IF NOT EXISTS and leave a drifted schema"},
        {"step": "4", "source": "pit_intermediates.PIT_INTERMEDIATE_DDL_V2",
         "applies": "pit_pillar_score (+2 indexes +immutability trigger), "
                    "pit_hedge_snapshot (+2 indexes +trigger)",
         "why": "pit_pillar_score DOES NOT EXIST in the main store. The pilot DB "
                "must create it; this is its only declaration"},
        {"step": "5", "source": "pit_intermediates.INTERMEDIATE_MIGRATIONS",
         "applies": "pit_cohort_stat.{n_eligible_json,n_eligible_min,binding_factor,"
                    "eligibility_rule,eligibility_rule_version}, "
                    "pit_feature.resolved_inputs_json, "
                    "pit_score.{score_signature,pillar_mask,original_weight_coverage,"
                    "subfactor_mask}",
         "why": "ADD COLUMN only, guarded by table_info"},
        {"step": "6", "source": "pit_score_signature.PIT_SCORE_SIGNATURE_MIGRATIONS "
                                "(via pit_score_signature.ensure_columns)",
         "applies": "pit_score.{score_signature,original_weight_coverage,"
                    "partial_score_marker} + idx_pit_score_signature",
         "why": "overlaps step 5 by design; each ALTER is table_info-guarded, so the "
                "union is applied and nothing is applied twice. partial_score_marker "
                "is unique to this step and carries "
                "PARTIAL_SCORE_INSUFFICIENT_BLOCK_COVERAGE"},
        {"step": "7", "source": "pit_valuation_spec.PIT_SCORE_SUBFACTOR_MIGRATIONS",
         "applies": "pit_score.{subfactor_mask,valuation_presence_state,"
                    "valuation_data_quality,valuation_quality_rule,"
                    "valuation_subfactors_json,earnings_discontinuity_state} "
                    "+ idx_pit_score_subfactor_mask",
         "why": "the subfactor half of the signature; same ADD-COLUMN convention"},
        {"step": "8 (NOT applied by default)",
         "source": "pit_score_signature.PIT_SCORE_GUARD_DDL "
                   "(ensure_columns(include_guard=True))",
         "applies": "BEFORE INSERT triggers refusing a NULL score_signature",
         "why": "install ONLY once the writer stamps the columns on every row, per "
                "that module's own instruction. Off by default here"},
    ]


def pilot_fk_requirements(pilot_path: str) -> Dict[str, Any]:
    """Which parent rows a fresh pilot DB needs before the first write.

    ``pit_store.connect()`` sets ``PRAGMA foreign_keys = ON``, and every table
    the replay writes points at pit_entity, pit_listing, pit_peer_set and
    pit_replay_run. On an EMPTY pilot DB the first pit_feature insert fails
    with FOREIGN KEY constraint failed. There are exactly two honest options
    and the pilot must pick one deliberately:

      SEED   -- copy the parent rows from the (read-only) store into the pilot,
                then call ``Meter.rebaseline()`` so their bytes are reported as
                a one-time seed cost and not as replay growth. Keeps the
                constraint checks real.
      FK OFF -- run the pilot with foreign_keys=OFF. Cheaper and smaller, but
                the pilot then proves nothing about referential integrity, and
                that has to be said out loud in the pilot's report.
    """
    out: Dict[str, Any] = {"pilot_path": os.path.abspath(pilot_path)}
    try:
        conn = connect_readonly(pilot_path)
    except sqlite3.Error as exc:
        return {"error": f"read-only connect failed: {exc}"}
    try:
        write_tables = ["pit_feature", "pit_score", "pit_pillar_score",
                        "pit_cohort_stat", "pit_company_intermediate",
                        "pit_hedge_snapshot"]
        needs: Dict[str, List[str]] = {}
        parents: Dict[str, Optional[int]] = {}
        for table in write_tables:
            try:
                fks = conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
            except sqlite3.Error:
                continue
            if not fks:
                continue
            needs[table] = sorted({row[2] for row in fks})
            for row in fks:
                parents.setdefault(row[2], None)
        for parent in list(parents):
            try:
                parents[parent] = int(conn.execute(
                    f'SELECT count(*) FROM "{parent}"').fetchone()[0])
            except sqlite3.Error:
                parents[parent] = None
        try:
            fk_on = bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])
        except sqlite3.Error:
            fk_on = None
    finally:
        conn.close()
    out["writes_need_parents"] = needs
    out["parent_row_counts"] = parents
    out["empty_parents"] = sorted(k for k, v in parents.items() if v == 0)
    out["foreign_keys_on_this_connection"] = fk_on
    out["blocks_first_insert"] = bool(out["empty_parents"])
    out["options"] = ["SEED_PARENTS_THEN_REBASELINE", "RUN_WITH_FOREIGN_KEYS_OFF"]
    return out


def compare_schema(pilot_path: str, store_path: str) -> Dict[str, Any]:
    """Tables the pilot has and the store lacks, and vice versa. READ-ONLY."""
    def tables(path: str) -> Tuple[Optional[set], Optional[str]]:
        try:
            conn = connect_readonly(path)
        except sqlite3.Error as exc:
            return None, f"read-only connect to {os.path.basename(path)} failed: {exc}"
        try:
            return {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'").fetchall()}, None
        except sqlite3.Error as exc:
            return None, f"sqlite_master read failed: {exc}"
        finally:
            conn.close()

    pilot, pilot_reason = tables(pilot_path)
    store, store_reason = tables(store_path)
    if pilot is None or store is None:
        return {"error": pilot_reason or store_reason,
                "in_store_not_in_pilot": None, "in_pilot_not_in_store": None}
    return {
        "in_store_not_in_pilot": sorted(store - pilot),
        "in_pilot_not_in_store": sorted(pilot - store),
        "shared": len(store & pilot),
        "reading":
            "in_store_not_in_pilot is only a problem if the replay WRITES one of "
            "them; the pilot reads from the store, so read-only tables need not "
            "exist in the pilot. in_pilot_not_in_store is expected: those are the "
            "intermediate tables the store never created.",
    }


def build_pilot_db(path: str, *, include_guard: bool = False,
                   overwrite: bool = False) -> Dict[str, Any]:
    """Create a fresh pilot database and return exactly what it contains.

    REFUSES a protected basename. Calls the real declaring modules rather than
    copying their DDL, so the pilot cannot drift from the store it emulates.
    """
    assert_write_allowed(path)
    path = os.path.abspath(path)
    if os.path.exists(path):
        if not overwrite:
            raise FileExistsError(
                f"{path} already exists; pass overwrite=True to replace it")
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                os.remove(path + suffix)
            except FileNotFoundError:
                pass

    import pit_store
    import pit_derive
    import pit_intermediates
    import pit_score_signature
    import pit_valuation_spec

    applied: List[str] = []
    conn = pit_store.init_db(path)                                   # steps 1-2
    applied.append("pit_store.SCHEMA + pit_store.apply_migrations")
    try:
        status = pit_intermediates.ensure_schema(conn)               # steps 3-5
        applied.append(
            "pit_derive.PIT_INTERMEDIATE_DDL + pit_intermediates."
            "PIT_INTERMEDIATE_DDL_V2 + INTERMEDIATE_MIGRATIONS "
            f"(columns added: {status.get('columns_added')})")

        added = pit_score_signature.ensure_columns(conn, include_guard=include_guard)
        applied.append(f"pit_score_signature.ensure_columns -> {added}")  # step 6

        have = {r[1] for r in conn.execute("PRAGMA table_info(pit_score)").fetchall()}
        sub_added = []
        for name, sql_type in pit_valuation_spec.PIT_SCORE_SUBFACTOR_MIGRATIONS:
            if name not in have:
                conn.execute(f"ALTER TABLE pit_score ADD COLUMN {name} {sql_type}")
                sub_added.append(f"pit_score.{name}")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pit_score_subfactor_mask "
            "ON pit_score (as_of_date, model_version, score_signature, subfactor_mask)")
        conn.commit()
        applied.append(
            f"pit_valuation_spec.PIT_SCORE_SUBFACTOR_MIGRATIONS -> {sub_added}")  # 7

        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]
        indexes = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]
        triggers = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name"
        ).fetchall()]
        score_cols = [r[1] for r in conn.execute("PRAGMA table_info(pit_score)").fetchall()]
        feature_cols = [r[1] for r in conn.execute(
            "PRAGMA table_info(pit_feature)").fetchall()]
        pillar_cols = [r[1] for r in conn.execute(
            "PRAGMA table_info(pit_pillar_score)").fetchall()]
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        page_count = conn.execute("PRAGMA page_count").fetchone()[0]
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()

    size, size_reason = _file_size(path)
    return {
        "path": path,
        "applied": applied,
        "tables": tables,
        "n_tables": len(tables),
        "indexes": indexes,
        "triggers": triggers,
        "has_pit_pillar_score": "pit_pillar_score" in tables,
        "pit_pillar_score_columns": pillar_cols,
        "pit_score_columns": score_cols,
        "pit_feature_columns": feature_cols,
        "page_size": page_size,
        "page_count": page_count,
        "journal_mode": journal,
        "file_bytes": size,
        "file_bytes_reason": size_reason,
        "guard_triggers_installed": include_guard,
    }


# --------------------------------------------------------------------------
# The pre-pilot manifest: snapshot the pristine state BEFORE the first row
# --------------------------------------------------------------------------

#: The tables the replay writes. Every one of them must read ZERO in the
#: manifest, or the pilot is not starting from a pristine store.
REPLAY_TABLES = ("pit_feature", "pit_score", "pit_replay_run",
                 "pit_pillar_score", "pit_cohort_stat",
                 "pit_company_intermediate", "pit_hedge_snapshot")

#: ZERO-BECAUSE-EMPTY AND ZERO-BECAUSE-ABSENT ARE DIFFERENT FACTS, and the gate
#: must not merge them. These four are DECLARED (by pit_derive and
#: pit_intermediates) but were never created in the main store, so their
#: absence there is the expected pristine state and the pilot DB is where they
#: come into existence. pit_feature, pit_score and pit_replay_run DO exist and
#: must read 0; if one of THOSE goes missing, something is wrong with the store
#: and the pilot refuses rather than calling it zero.
EXPECTED_ABSENT_IN_STORE = frozenset({
    "pit_pillar_score", "pit_cohort_stat",
    "pit_company_intermediate", "pit_hedge_snapshot",
})


def store_identity(path: str, *, full_sha256: bool = False,
                   sha_chunk: int = 8 * MIB) -> Dict[str, Any]:
    """An immutable identity for the source store. READ-ONLY, always.

    A full SHA-256 of 8.971 GiB is minutes of I/O on this volume and is OFF by
    default; what is on by default is an identity that is cheap and still
    sufficient for the purpose at hand: size, mtime, page geometry, the
    SQLite header's own counters, and a SHA-256 of the complete schema text.
    Two stores that agree on all of it are the same store for replay purposes.
    Set ``full_sha256=True`` when the cost is acceptable -- the manifest
    records WHICH identity was taken either way, in ``identity_method``.
    """
    ident: Dict[str, Any] = {"path": os.path.abspath(path)}
    size, size_reason = _file_size(path)
    ident["file_bytes"] = size
    ident["file_bytes_reason"] = size_reason
    for suffix in ("-wal", "-shm"):
        sidecar, reason = _file_size(path + suffix)
        ident["sidecar" + suffix + "_bytes"] = sidecar
        if reason:
            ident["sidecar" + suffix + "_reason"] = reason
    try:
        ident["mtime_utc"] = _dt.datetime.fromtimestamp(
            os.path.getmtime(path), _dt.timezone.utc).isoformat(timespec="seconds")
    except OSError as exc:
        ident["mtime_utc"] = None
        ident["mtime_reason"] = f"{exc.__class__.__name__}: {exc}"
    try:
        conn = connect_readonly(path)
    except sqlite3.Error as exc:
        ident["schema_sha256"] = None
        ident["schema_reason"] = f"read-only connect failed: {exc}"
        ident["identity_method"] = PER_TABLE_UNMEASURED
        return ident
    try:
        import hashlib
        for pragma in ("page_size", "page_count", "freelist_count",
                       "schema_version", "user_version", "application_id",
                       "journal_mode"):
            try:
                ident[pragma] = conn.execute(f"PRAGMA {pragma}").fetchone()[0]
            except sqlite3.Error as exc:
                ident[pragma] = None
                ident[f"{pragma}_reason"] = f"{exc.__class__.__name__}: {exc}"
        if ident.get("page_size") and ident.get("page_count"):
            ident["page_bytes"] = ident["page_size"] * ident["page_count"]
        try:
            rows = conn.execute(
                "SELECT type, name, tbl_name, COALESCE(sql, '') FROM sqlite_master "
                "ORDER BY type, name").fetchall()
            digest = hashlib.sha256()
            for row in rows:
                digest.update(("\x1f".join(str(c) for c in row) + "\x1e").encode("utf-8"))
            ident["schema_sha256"] = digest.hexdigest()
            ident["n_schema_objects"] = len(rows)
        except sqlite3.Error as exc:
            ident["schema_sha256"] = None
            ident["schema_reason"] = f"sqlite_master read failed: {exc}"
        counts: Dict[str, Any] = {}
        reasons: Dict[str, str] = {}
        for table in REPLAY_TABLES:
            try:
                counts[table] = int(conn.execute(
                    f'SELECT count(*) FROM "{table}"').fetchone()[0])
            except sqlite3.Error as exc:
                counts[table] = None
                reasons[table] = f"{exc.__class__.__name__}: {exc}"
        ident["replay_table_counts"] = counts
        ident["replay_table_count_reasons"] = reasons or None
        absent = [k for k, v in counts.items() if v is None]
        present = {k: v for k, v in counts.items() if v is not None}
        ident["replay_tables_absent"] = absent
        ident["replay_tables_expected_absent"] = sorted(
            k for k in absent if k in EXPECTED_ABSENT_IN_STORE)
        ident["replay_tables_unexpectedly_absent"] = sorted(
            k for k in absent if k not in EXPECTED_ABSENT_IN_STORE)
        ident["replay_tables_nonzero"] = {k: v for k, v in present.items() if v != 0}
        ident["all_present_replay_tables_zero"] = (
            bool(present) and all(v == 0 for v in present.values()))
        # Pristine means: every table that EXISTS reads 0, and every table that
        # does not exist is one we expected not to exist.
        ident["pristine"] = bool(
            ident["all_present_replay_tables_zero"]
            and not ident["replay_tables_unexpectedly_absent"])
        ident["pristine_reading"] = (
            "counted zero: " + ", ".join(sorted(present)) + " | absent as expected: "
            + (", ".join(ident["replay_tables_expected_absent"]) or "none")
            + " | absent UNEXPECTEDLY: "
            + (", ".join(ident["replay_tables_unexpectedly_absent"]) or "none"))
    finally:
        # Close at once. A lingering reader blocks the WAL checkpointer, which
        # is how this project once put a 9.3 GB WAL on this volume.
        conn.close()
    if full_sha256:
        import hashlib
        digest = hashlib.sha256()
        t0 = time.monotonic()
        try:
            with open(path, "rb") as fh:
                while True:
                    block = fh.read(sha_chunk)
                    if not block:
                        break
                    digest.update(block)
            ident["file_sha256"] = digest.hexdigest()
            ident["file_sha256_elapsed_s"] = round(time.monotonic() - t0, 2)
        except OSError as exc:
            ident["file_sha256"] = None
            ident["file_sha256_reason"] = f"{exc.__class__.__name__}: {exc}"
    else:
        ident["file_sha256"] = None
        ident["file_sha256_reason"] = (
            "not taken: hashing the whole store is minutes of I/O. Pass "
            "full_sha256=True to take it. Identity here rests on size + mtime "
            "+ page geometry + schema_sha256.")
    ident["identity_method"] = (
        "FULL_SHA256" if ident.get("file_sha256") else "GEOMETRY_PLUS_SCHEMA_SHA256")
    return ident


def _git_revision(start: str) -> Dict[str, Any]:
    """The code revision, read from .git directly. No subprocess, no network."""
    here = os.path.abspath(start)
    for _ in range(6):
        git_dir = os.path.join(here, ".git")
        if os.path.isdir(git_dir):
            try:
                with open(os.path.join(git_dir, "HEAD"), encoding="utf-8") as fh:
                    head = fh.read().strip()
            except OSError as exc:
                return {"revision": None, "reason": f"HEAD unreadable: {exc}"}
            if head.startswith("ref: "):
                ref = head[5:].strip()
                try:
                    with open(os.path.join(git_dir, ref), encoding="utf-8") as fh:
                        return {"revision": fh.read().strip(), "branch": ref,
                                "git_dir": git_dir}
                except OSError:
                    try:
                        with open(os.path.join(git_dir, "packed-refs"),
                                  encoding="utf-8") as fh:
                            for line in fh:
                                if line.rstrip().endswith(" " + ref):
                                    return {"revision": line.split()[0],
                                            "branch": ref, "git_dir": git_dir}
                    except OSError:
                        pass
                    return {"revision": None, "branch": ref,
                            "reason": "ref found neither loose nor packed"}
            return {"revision": head, "branch": "DETACHED", "git_dir": git_dir}
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent
    return {"revision": None, "reason": f"no .git found at or above {start}"}


def preflight_manifest(
    store_path: str,
    pilot_db_path: str,
    *,
    pilot_dates: Sequence[Tuple[str, int]] = PILOT_DATES,
    full_sha256: bool = False,
    free_floor_bytes: int = FREE_FLOOR_DEFAULT,
) -> Dict[str, Any]:
    """The pristine-state snapshot, taken BEFORE the pilot writes its first row.

    Every generated score must be traceable not merely to "v3" but to the exact
    frozen specification and the exact source store it came from. This returns
    that record, and it also returns the GATE:

        ``may_start`` is True only when ``pit_frozen_spec.verify()['intact']``
        is True, ``can_execute_replay()`` returns (True, []), every replay
        table in the source store reads zero, free space is above the floor,
        and the pilot target is not the main store.

    If verification fails the caller must refuse BEFORE opening a write
    transaction. Nothing in this function writes anything, anywhere.
    """
    refusals: List[str] = []
    manifest: Dict[str, Any] = {
        "manifest_version": "replay_manifest/1",
        "meter_version": METER_VERSION,
        "generated_at": _utc(),
        "model_version": MODEL_VERSION,
        "sample_scope": "SURVIVOR_ONLY_DIAGNOSTIC",
        "promotes_anything": False,
    }

    try:
        import pit_frozen_spec
        verification = pit_frozen_spec.verify()
        can, blockers = pit_frozen_spec.can_execute_replay()
        manifest["frozen_spec"] = {
            "freeze_version": verification.get("freeze_version"),
            "frozen_digest": verification.get("frozen_digest"),
            "current_digest": verification.get("current_digest"),
            "intact": verification.get("intact"),
            "n_components": verification.get("n_components"),
            "frozen_at": verification.get("frozen_at"),
            "can_execute_replay": can,
            "blockers": list(blockers),
        }
        if not verification.get("intact"):
            refusals.append(
                "spec verification FAILED: digest is "
                f"{verification.get('current_digest')} but the freeze recorded "
                f"{verification.get('frozen_digest')}")
        if not can:
            refusals.append(f"can_execute_replay() refused: {list(blockers)}")
    except Exception as exc:
        manifest["frozen_spec"] = {"error": f"{exc.__class__.__name__}: {exc}"}
        refusals.append(f"frozen spec could not be verified: {exc}")

    manifest["code_revision"] = _git_revision(os.path.dirname(os.path.abspath(__file__)))
    manifest["source_store"] = store_identity(store_path, full_sha256=full_sha256)
    src = manifest["source_store"]
    if src.get("replay_tables_nonzero"):
        refusals.append(
            "the source store's replay tables are NOT all zero: "
            f"{src['replay_tables_nonzero']}. The pilot must start from a "
            "pristine store, not on top of existing replay rows")
    if src.get("replay_tables_unexpectedly_absent"):
        refusals.append(
            "replay tables are missing from the source store that should exist: "
            f"{src['replay_tables_unexpectedly_absent']}. Absent is NOT zero -- "
            "something is wrong with the store")
    if src.get("all_present_replay_tables_zero") is None:
        refusals.append("the source store's replay tables could not be counted")

    free_bytes, free_reason = free_space(store_path)
    manifest["volume"] = {
        "path": _volume_of(store_path),
        "free_bytes": free_bytes,
        "free_bytes_reason": free_reason,
        "free_gib": None if free_bytes is None else round(free_bytes / GIB, 4),
        "floor_bytes": free_floor_bytes,
        "floor_gib": round(free_floor_bytes / GIB, 4),
    }
    if free_bytes is None:
        refusals.append(f"free space could not be measured: {free_reason}")
    elif free_bytes < free_floor_bytes:
        refusals.append(
            f"free space {free_bytes / GIB:.3f} GiB is already below the "
            f"{free_floor_bytes / GIB:.3f} GiB floor")

    manifest["pilot"] = {
        "pilot_db_path": os.path.abspath(pilot_db_path),
        "pilot_db_exists_before_run": os.path.exists(pilot_db_path),
        "dates": [{"as_of_date": d, "entities": n} for d, n in pilot_dates],
        "entity_dates": sum(n for _, n in pilot_dates),
        "grid_entity_dates": FULL_GRID_ENTITY_DATES,
        "grid_dates": FULL_GRID_DATES,
        "share_of_grid": round(
            sum(n for _, n in pilot_dates) / FULL_GRID_ENTITY_DATES, 6),
        "complete_cross_sections": True,
        "ddl_plan": [step["source"] for step in pilot_ddl_plan()],
    }
    if is_protected(pilot_db_path):
        refusals.append(
            f"the pilot DB path {pilot_db_path!r} has a PROTECTED basename")

    manifest["refusals"] = refusals
    manifest["may_start"] = not refusals
    manifest["reading"] = (
        "may_start=True means: the frozen spec verified intact, every replay "
        "table in the source store reads zero, free space is above the floor, "
        "and the pilot target is not the main store. may_start=False means the "
        "pilot must refuse BEFORE opening a write transaction."
    )
    return manifest


# --------------------------------------------------------------------------
# Extrapolation -- components always reported separately
# --------------------------------------------------------------------------

def project_full_replay(
    report: Dict[str, Any],
    *,
    grid_entity_dates: int = FULL_GRID_ENTITY_DATES,
    reserve_bytes: int = 5 * GIB,
    retained_multiplier: float = 2.0,
    free_space_bytes: Optional[int] = None,
) -> Dict[str, Any]:
    """Size the full replay FROM the pilot, never from a round number.

        RequiredSpace = ProjectedRetainedGrowth + PeakWAL + PeakTemp + SafetyReserve

    and the start gate

        FreeSpace_start > k * ProjectedRetainedGrowth + PeakWAL + PeakTemp + Reserve

    with k = ``retained_multiplier``. Every component is returned separately;
    any component that the pilot could not measure makes the TOTAL None with a
    reason, because a budget assembled from a guess is not a budget.
    """
    unknown: Dict[str, str] = {}
    bpe = report.get("derived", {}).get("bytes_per_entity_date")
    pilot_n = report.get("work", {}).get("entity_dates")
    retained = report.get("db", {}).get("retained_growth_bytes")
    peak_wal = report.get("wal", {}).get("peak_bytes")
    peak_temp = report.get("temp", {}).get("peak_bytes")

    if bpe is None:
        unknown["bytes_per_entity_date"] = (
            report.get("unknown", {}).get("derived.bytes_per_entity_date")
            or "not measured by the pilot")
    if peak_wal is None:
        unknown["measured_peak_wal_bytes"] = (
            report.get("unknown", {}).get("wal.peak_bytes") or "not measured")
    if peak_temp is None:
        unknown["measured_peak_temp_bytes"] = (
            report.get("unknown", {}).get("temp.peak_bytes") or "not measured")

    projected = bpe * grid_entity_dates if bpe is not None else None
    if projected is None:
        unknown["projected_retained_growth_bytes"] = (
            "bytes-per-entity-date unknown, so the retained growth cannot be projected")

    components = {
        "projected_retained_growth_bytes": projected,
        "measured_peak_wal_bytes": peak_wal,
        "measured_peak_temp_bytes": peak_temp,
        "safety_reserve_bytes": int(reserve_bytes),
    }
    required = None
    if all(v is not None for v in components.values()):
        required = sum(components.values())  # type: ignore[arg-type]
    else:
        unknown["required_space_bytes"] = (
            "at least one component is UNKNOWN: "
            + ", ".join(k for k, v in components.items() if v is None))

    gate_threshold = None
    if projected is not None and peak_wal is not None and peak_temp is not None:
        gate_threshold = (retained_multiplier * projected + peak_wal
                          + peak_temp + reserve_bytes)
    else:
        unknown["gate_threshold_bytes"] = unknown.get(
            "required_space_bytes", "components missing")

    gate_pass = None
    if gate_threshold is not None and free_space_bytes is not None:
        gate_pass = free_space_bytes > gate_threshold
    elif gate_threshold is None:
        unknown["gate_pass"] = "threshold unknown"
    else:
        unknown["gate_pass"] = "free space at start not supplied"

    return {
        "basis": {
            "pilot_entity_dates": pilot_n,
            "pilot_retained_growth_bytes": retained,
            "bytes_per_entity_date": bpe,
            "grid_entity_dates": grid_entity_dates,
            "grid_dates": FULL_GRID_DATES,
            "pilot_share_of_grid": (pilot_n / grid_entity_dates) if pilot_n else None,
            "extrapolation_note":
                "scaled by ENTITY-DATES, not by 'pilot dates x 41'. Cross-sections "
                "differ in size (8,132 vs 7,041) and in coverage regime, so a "
                "per-date multiplier would be the wrong ruler.",
        },
        "components": components,
        "required_space_bytes": required,
        "required_space_formula":
            "ProjectedRetainedGrowth + PeakWAL + PeakTemp + SafetyReserve",
        "gate": {
            "formula": f"FreeSpace_start > {retained_multiplier} x "
                       f"ProjectedRetainedGrowth + PeakWAL + PeakTemp + Reserve",
            "retained_multiplier": retained_multiplier,
            "threshold_bytes": gate_threshold,
            "free_space_bytes_at_check": free_space_bytes,
            "pass": gate_pass,
            "multiplier_note":
                "the multiplier is provisional and must be revised from the pilot's "
                "own write amplification (WAL peak / retained growth), not kept "
                "because it was written down first.",
        },
        "unknown": unknown,
    }


def gib(n: Optional[float]) -> Optional[str]:
    """Bytes as GiB for human reading. None stays None -- never 0."""
    return None if n is None else f"{n / GIB:.4f} GiB"


# --------------------------------------------------------------------------
# validate()
# --------------------------------------------------------------------------

def validate(verbose: bool = False) -> Dict[str, Any]:
    """Self-check the meter on a throwaway database. Touches no real store."""
    checks: List[Dict[str, Any]] = []

    def check(name: str, ok: bool, detail: Any = None) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    probe = dbstat_probe()
    check("dbstat probed (result either way is acceptable)",
          isinstance(probe["available"], bool), probe)

    # The guard must refuse the main store, with no override path.
    try:
        assert_write_allowed(os.path.join("C:\\anywhere", "shafferfineval_pit.db"))
        check("protected store refused for writable open", False,
              "assert_write_allowed did NOT raise")
    except ProtectedStoreError:
        check("protected store refused for writable open", True, None)
    try:
        build_pilot_db("shafferfineval_pit.db")
        check("build_pilot_db refuses the protected basename", False, "no raise")
    except ProtectedStoreError:
        check("build_pilot_db refuses the protected basename", True, None)
    except Exception as exc:  # pragma: no cover
        check("build_pilot_db refuses the protected basename", False, repr(exc))

    tmpdir = tempfile.mkdtemp(prefix="pit_meter_validate_")
    db = os.path.join(tmpdir, "meter_selftest.db")
    report: Dict[str, Any] = {}
    cleanup_error: Optional[str] = None
    try:
        conn = sqlite3.connect(db)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(
            "CREATE TABLE demo_feature(id INTEGER PRIMARY KEY, as_of_date TEXT,"
            " entity_id INTEGER, name TEXT, value REAL, payload TEXT);"
            "CREATE TABLE demo_score(id INTEGER PRIMARY KEY, as_of_date TEXT,"
            " entity_id INTEGER, score REAL, signature TEXT);"
        )
        conn.commit()

        with Meter(db, sample_hz=20, free_floor_bytes=0, label="selftest") as m:
            m.begin("2019-06-28")
            with m.table_scope("demo_feature"):
                conn.executemany(
                    "INSERT INTO demo_feature(as_of_date, entity_id, name, value, payload)"
                    " VALUES (?,?,?,?,?)",
                    [("2019-06-28", i, "ebitda_strength", i * 1.5, "x" * 120)
                     for i in range(8000)])
                conn.commit()
            with m.table_scope("demo_score"):
                conn.executemany(
                    "INSERT INTO demo_score(as_of_date, entity_id, score, signature)"
                    " VALUES (?,?,?,?)",
                    [("2019-06-28", i, (i % 170) - 85, "EVGQ") for i in range(2000)])
                conn.commit()
            cp = m.checkpoint("TRUNCATE")
            m.mark("2019-06-28", entity_dates=2000)
            payload = m.logical_payload_bytes("demo_feature")
            report = m.report()
        conn.close()

        check("meter started and stopped", report.get("timing", {}).get("elapsed_s") is not None,
              report.get("timing"))
        check("sampler produced samples", report["samples"]["n_samples"] > 2,
              report["samples"])
        check("checkpoint return value READ (busy, log_pages, checkpointed)",
              cp.get("return_read") is True,
              {k: cp.get(k) for k in ("busy", "log_pages", "checkpointed", "busy_refusal")})
        check("WAL peak observed > 0", (report["wal"]["peak_bytes"] or 0) > 0,
              report["wal"]["peak_bytes"])
        # REGRESSION: the first cut of this meter sampled the WAL only from the
        # background thread and reported a 33 KB peak for a workload the
        # checkpoint path had just seen at 1.19 MB. A budget built on an
        # understated peak is the exact failure this harness exists to prevent.
        biggest_seen = max(
            [c["wal_bytes_before"] for c in report["wal"]["checkpoints"]
             if c.get("wal_bytes_before") is not None] or [0])
        check("WAL peak is not understated vs. what the checkpoints saw",
              (report["wal"]["peak_bytes"] or 0) >= biggest_seen,
              {"peak": report["wal"]["peak_bytes"],
               "largest wal seen at a checkpoint": biggest_seen,
               "peak_source": report["wal"]["peak_source"]})
        check("WAL peak is labelled a lower bound",
              report["wal"]["peak_is_lower_bound"] is True, None)
        check("peak attributed to the phase in force",
              report["wal"]["peak_under_mark"] == "2019-06-28",
              report["wal"]["peak_under_mark"])
        check("WAL after TRUNCATE checkpoint recorded",
              cp.get("wal_bytes_after") is not None, cp.get("wal_bytes_after"))
        check("DB growth measured", (report["db"]["file_growth_bytes"] or 0) > 0,
              report["db"])
        check("retained growth measured after checkpoint",
              report["db"]["retained_growth_bytes"] is not None,
              report["db"]["retained_growth_bytes"])
        check("free-space low-water measured",
              report["free_space"]["low_water_bytes"] is not None,
              gib(report["free_space"]["low_water_bytes"]))
        check("per-table bytes attributed for both tables",
              all(report["per_table_bytes"]["tables"].get(t, {}).get("bytes") is not None
                  for t in ("demo_feature", "demo_score")),
              report["per_table_bytes"]["tables"])
        check("table_scope attribution was exclusive",
              all(s.get("exclusive") for s in report["table_scopes"]),
              [(s["table"], s.get("exclusive"), s.get("other_tables_changed"))
               for s in report["table_scopes"]])
        check("bytes_per_entity_date derived",
              report["derived"]["bytes_per_entity_date"] is not None,
              report["derived"])
        check("logical payload cross-check is smaller than storage",
              (payload["bytes"] is not None
               and report["per_table_bytes"]["tables"]["demo_feature"]["bytes"]
               > payload["bytes"]),
              {"payload": payload.get("bytes"),
               "storage": report["per_table_bytes"]["tables"]["demo_feature"].get("bytes")})
        no_fake_zero = True
        offenders = []
        for field, reason in report["unknown"].items():
            if not isinstance(reason, str) or not reason.strip():
                no_fake_zero = False
                offenders.append(field)
        check("every UNKNOWN field carries a reason string", no_fake_zero, offenders or None)

        projection = project_full_replay(report, free_space_bytes=free_space(db)[0])
        check("projection reports every component separately",
              set(projection["components"]) == {
                  "projected_retained_growth_bytes", "measured_peak_wal_bytes",
                  "measured_peak_temp_bytes", "safety_reserve_bytes"},
              projection["components"])

        # A fresh pilot DB, actually built, in the temp directory.
        pilot = build_pilot_db(os.path.join(tmpdir, "shafferfineval_pilot.db"))
        check("pilot DB builds and declares pit_pillar_score",
              pilot["has_pit_pillar_score"], pilot["n_tables"])
        check("pilot pit_score carries the signature + subfactor columns",
              {"score_signature", "pillar_mask", "original_weight_coverage",
               "partial_score_marker", "subfactor_mask", "valuation_presence_state",
               "valuation_subfactors_json", "earnings_discontinuity_state"}
              <= set(pilot["pit_score_columns"]), pilot["pit_score_columns"])
        check("pilot pit_feature carries resolved_inputs_json",
              "resolved_inputs_json" in pilot["pit_feature_columns"],
              pilot["pit_feature_columns"])
        check("pilot DB is WAL", pilot["journal_mode"] == "wal", pilot["journal_mode"])

        # The pristine gate must BITE, not merely exist. Zero-because-empty and
        # zero-because-absent are different facts and neither may be faked.
        ident = store_identity(pilot["path"])
        check("a fresh pilot DB reads pristine", ident["pristine"] is True,
              ident.get("pristine_reading"))
        pc = sqlite3.connect(pilot["path"])
        pc.execute(
            "INSERT INTO pit_replay_run (started_at, as_of_grid, model_version,"
            " latency_policy_version, ladder_version, peer_set_version, status)"
            " VALUES (?,?,?,?,?,?,?)",
            (_utc(), "165 month-ends 2013-01-31..2026-09-18", MODEL_VERSION,
             "selftest", "selftest", "selftest", "selftest"))
        pc.commit()
        pc.close()
        dirty = store_identity(pilot["path"])
        check("a store with replay rows is NOT pristine",
              dirty["pristine"] is False and dirty["replay_tables_nonzero"],
              dirty.get("replay_tables_nonzero"))
        man = preflight_manifest(pilot["path"], pilot["path"])
        check("preflight refuses a store that is not pristine",
              man["may_start"] is False and man["refusals"], man["refusals"])
        man2 = preflight_manifest(pilot["path"], "shafferfineval_pit.db")
        check("preflight refuses a PROTECTED pilot target",
              any("PROTECTED" in r for r in man2["refusals"]), man2["refusals"])
        fk = pilot_fk_requirements(pilot["path"])
        check("FK requirements found: an empty pilot blocks the first insert",
              fk["blocks_first_insert"] is True and "pit_entity" in fk["empty_parents"],
              {"empty_parents": fk["empty_parents"],
               "pit_feature needs": fk["writes_need_parents"].get("pit_feature")})

        # rebaseline must EXCLUDE seed bytes from replay growth, or
        # bytes-per-entity-date is overstated by the seed.
        db2 = os.path.join(tmpdir, "rebaseline.db")
        c2 = sqlite3.connect(db2)
        c2.execute("PRAGMA journal_mode=WAL")
        c2.execute("CREATE TABLE seed(id INTEGER PRIMARY KEY, blob TEXT)")
        c2.execute("CREATE TABLE replay(id INTEGER PRIMARY KEY, blob TEXT)")
        c2.commit()
        with Meter(db2, sample_hz=0, free_floor_bytes=0, label="rebaseline") as m2:
            c2.executemany("INSERT INTO seed(blob) VALUES (?)",
                           [("s" * 400,) for _ in range(4000)])
            c2.commit()
            m2.checkpoint("TRUNCATE")
            rb = m2.rebaseline("seeded FK parents; the seed is not the replay")
            c2.executemany("INSERT INTO replay(blob) VALUES (?)",
                           [("r" * 100,) for _ in range(1000)])
            c2.commit()
            m2.checkpoint("TRUNCATE")
            m2.mark("2019-06-28", entity_dates=1000)
            r2 = m2.report()
        c2.close()
        check("rebaseline recorded the seed cost separately",
              (rb["seed_bytes"] or 0) > 0, rb["seed_bytes"])
        check("replay growth EXCLUDES the seed after rebaseline",
              (r2["db"]["retained_growth_bytes"] is not None
               and rb["seed_bytes"] is not None
               and r2["db"]["retained_growth_bytes"] < rb["seed_bytes"]),
              {"seed_bytes": rb["seed_bytes"],
               "retained_growth_after_rebaseline":
                   r2["db"]["retained_growth_bytes"],
               "bytes_per_entity_date": r2["derived"]["bytes_per_entity_date"]})
        check("growth_basis says the seed was excluded",
              "rebaseline" in (r2["db"]["growth_basis"] or ""),
              r2["db"]["growth_basis"])

        check("preflight records the frozen spec digest",
              (man["frozen_spec"].get("current_digest") or "")
              == pit_frozen_spec.FROZEN_DIGEST,
              {k: man["frozen_spec"].get(k)
               for k in ("freeze_version", "intact", "n_components")})
    finally:
        try:
            shutil.rmtree(tmpdir)
        except OSError as exc:
            cleanup_error = (
                f"{exc.__class__.__name__}: {exc} -- a sqlite connection is still "
                f"open on one of these files, so the temp directory cannot be "
                f"removed. On Windows that is how a handle leak shows itself.")
            shutil.rmtree(tmpdir, ignore_errors=True)

    check("no database handle leaked (temp directory deletes cleanly)",
          cleanup_error is None and not os.path.exists(tmpdir),
          cleanup_error or tmpdir)

    ok = all(c["ok"] for c in checks)
    out = {"ok": ok, "n_checks": len(checks),
           "n_failed": sum(1 for c in checks if not c["ok"]), "checks": checks}
    if verbose:
        out["report"] = report
    return out


# --------------------------------------------------------------------------
# __main__ -- demonstrate on a throwaway database, then delete it
# --------------------------------------------------------------------------

def _demo() -> Dict[str, Any]:
    tmpdir = tempfile.mkdtemp(prefix="pit_meter_demo_")
    db = os.path.join(tmpdir, "demo_replay.db")
    try:
        conn = sqlite3.connect(db)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(
            "CREATE TABLE pit_feature_demo(id INTEGER PRIMARY KEY, as_of_date TEXT,"
            " entity_id INTEGER, feature_name TEXT, value REAL, sources_json TEXT,"
            " resolved_inputs_json TEXT);"
            "CREATE INDEX ix_f ON pit_feature_demo(as_of_date, entity_id);"
            "CREATE TABLE pit_pillar_score_demo(id INTEGER PRIMARY KEY, as_of_date TEXT,"
            " entity_id INTEGER, pillar TEXT, pillar_score REAL, subfactors_json TEXT);"
            "CREATE TABLE pit_score_demo(id INTEGER PRIMARY KEY, as_of_date TEXT,"
            " entity_id INTEGER, score REAL, score_signature TEXT,"
            " valuation_subfactors_json TEXT);"
        )
        conn.commit()

        with Meter(db, sample_hz=10, free_floor_bytes=0, label="demo") as m:
            for as_of, n in (("2019-06-28", 1500), ("2022-06-30", 1500)):
                ok, free, why = m.free_space_ok()
                if not ok:
                    m.note(f"aborting before {as_of}: {why}")
                    break
                m.begin(as_of)
                with m.table_scope("pit_feature_demo"):
                    conn.executemany(
                        "INSERT INTO pit_feature_demo(as_of_date, entity_id,"
                        " feature_name, value, sources_json, resolved_inputs_json)"
                        " VALUES (?,?,?,?,?,?)",
                        [(as_of, i, f, i * 0.37, '{"acc":["0001"]}', '{"ebitda":1}')
                         for i in range(n)
                         for f in ("ebitda_strength", "valuation", "real_growth",
                                   "financial_quality", "ebitda_scale")])
                    conn.commit()
                with m.table_scope("pit_pillar_score_demo"):
                    conn.executemany(
                        "INSERT INTO pit_pillar_score_demo(as_of_date, entity_id,"
                        " pillar, pillar_score, subfactors_json) VALUES (?,?,?,?,?)",
                        [(as_of, i, p, (i % 170) - 85, '{"pe":{"n":0.5}}')
                         for i in range(n)
                         for p in ("ebitda_strength", "valuation", "real_growth",
                                   "financial_quality")])
                    conn.commit()
                with m.table_scope("pit_score_demo"):
                    conn.executemany(
                        "INSERT INTO pit_score_demo(as_of_date, entity_id, score,"
                        " score_signature, valuation_subfactors_json) VALUES (?,?,?,?,?)",
                        [(as_of, i, (i % 170) - 85, "EVGQ", '{"pe":1}')
                         for i in range(n)])
                    conn.commit()
                cp = m.checkpoint("TRUNCATE")
                m.mark(as_of, entity_dates=n)
                if cp.get("busy_refusal"):
                    m.note(f"busy refusal after {as_of}; stopping")
                    break
            report = m.report()
        conn.close()
        report["projection_demo"] = project_full_replay(
            report, free_space_bytes=free_space(db)[0])
        return report
    finally:
        try:
            shutil.rmtree(tmpdir)
        except OSError as exc:
            report_cleanup_warning = (
                f"the demo's temp directory could not be removed: {exc}")
            shutil.rmtree(tmpdir, ignore_errors=True)
            print(f"  WARNING: {report_cleanup_warning}")


def _print_human(report: Dict[str, Any]) -> None:
    def line(k: str, v: Any) -> None:
        print(f"  {k:<44} {v}")

    print("\n-- METHOD ------------------------------------------------------")
    line("per-table byte method", report["method"]["per_table_bytes"])
    line("dbstat available", report["method"]["dbstat_available"])
    if not report["method"]["dbstat_available"]:
        line("dbstat reason", report["method"]["dbstat_reason"])
    line("sqlite", report["method"]["sqlite_version"])
    print("\n-- DB ----------------------------------------------------------")
    d = report["db"]
    line("page_size", d["page_size"])
    line("file bytes before/after", f'{d["file_bytes_before"]} -> {d["file_bytes_after"]}')
    line("file growth", f'{d["file_growth_bytes"]} B ({gib(d["file_growth_bytes"])})')
    line("page_count before/after", f'{d["page_count_before"]} -> {d["page_count_after"]}')
    line("retained after checkpoint", d["retained_bytes_after_checkpoint"])
    line("retained growth", d["retained_growth_bytes"])
    line("freelist pages after", d["freelist_count_after"])
    print("\n-- WAL ---------------------------------------------------------")
    w = report["wal"]
    line("peak (lower bound)",
         f'{w["peak_bytes"]} B ({gib(w["peak_bytes"])}) under {w["peak_under_mark"]!r} '
         f'seen by {w["peak_source"]!r}')
    line("peak seen by background sampler alone", w["peak_bytes_background_sampler_only"])
    line("final", w["final_bytes"])
    line("after last checkpoint", w["bytes_after_last_checkpoint"])
    line("checkpoints / busy refusals", f'{w["n_checkpoints"]} / {w["busy_refusals"]}')
    for c in w["checkpoints"]:
        line(f'  {c["mode"]} @ {c["mark"]}',
             f'(busy={c["busy"]}, log_pages={c["log_pages"]}, '
             f'checkpointed={c["checkpointed"]}) read={c["return_read"]} '
             f'wal {c["wal_bytes_before"]} -> {c["wal_bytes_after"]}')
    print("\n-- TEMP / FREE SPACE -------------------------------------------")
    t, f = report["temp"], report["free_space"]
    line("temp dir", t["dir"])
    line("temp on same volume as DB", t["same_volume_as_db"])
    line("etilqs peak", t["etilqs_peak_bytes"])
    line("rollback journal peak", t["rollback_journal_peak_bytes"])
    line("free before/after", f'{gib(f["before_bytes"])} -> {gib(f["after_bytes"])}')
    line("free low-water", f'{gib(f["low_water_bytes"])} under {f["low_water_under_mark"]!r}')
    line("floor / breached", f'{gib(f["floor_bytes"])} / {f["floor_breached"]}')
    print("\n-- WORK --------------------------------------------------------")
    line("elapsed s", report["timing"]["elapsed_s"])
    line("entity-dates", report["work"]["entity_dates"])
    line("entity-dates / s", report["work"]["entity_dates_per_second"])
    line("bytes per entity-date", report["derived"]["bytes_per_entity_date"])
    print("\n-- PER-TABLE BYTES ---------------------------------------------")
    for name, slot in report["per_table_bytes"]["tables"].items():
        line(name, f'{slot.get("bytes")} B rows={slot.get("rows")} '
                   f'exclusive={slot.get("exclusive")} method={slot.get("method")}')
    if report["unknown"]:
        print("\n-- UNKNOWN (never 0) -------------------------------------------")
        for k, v in report["unknown"].items():
            line(k, v)
    if report["notes"]:
        print("\n-- NOTES -------------------------------------------------------")
        for n in report["notes"]:
            print(f"  * {n}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    as_json = "--json" in argv
    want_manifest = "--manifest" in argv

    print(f"{METER_VERSION}   python {sys.version.split()[0]}   "
          f"sqlite {sqlite3.sqlite_version}")
    probe = dbstat_probe()
    print(f"dbstat virtual table: "
          f"{'AVAILABLE -- exact per-table page counts' if probe['available'] else 'NOT AVAILABLE'}")
    if not probe["available"]:
        print(f"  reason: {probe['reason']}")
        print(f"  ENABLE_DBSTAT_VTAB in compile_options: {probe['compile_option_present']}")
        print(f"  => per-table bytes fall back to {PER_TABLE_PAGE_DELTA}")

    print("\n=== validate() ===")
    v = validate()
    for c in v["checks"]:
        print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['check']}")
        if not c["ok"]:
            print(f"         detail: {c['detail']}")
    print(f"  {v['n_checks'] - v['n_failed']}/{v['n_checks']} passed")

    print("\n=== DDL a fresh pilot DB needs ===")
    for step in pilot_ddl_plan():
        print(f"  [{step['step']}] {step['source']}")
        print(f"        applies: {step['applies']}")

    print("\n=== building a pilot DB in the system temp directory ===")
    tmpdir = tempfile.mkdtemp(prefix="pit_pilot_ddl_")
    try:
        built = build_pilot_db(os.path.join(tmpdir, "shafferfineval_pilot.db"))
        print(f"  path        : {built['path']}")
        print(f"  tables      : {built['n_tables']}")
        print(f"  page_size   : {built['page_size']}  "
              f"page_count: {built['page_count']}  bytes: {built['file_bytes']}")
        print(f"  journal_mode: {built['journal_mode']}")
        print(f"  pit_pillar_score present: {built['has_pit_pillar_score']}")
        for i in range(0, len(built["tables"]), 4):
            print("    " + "  ".join(f"{t:<28}" for t in built["tables"][i:i + 4]))
        print(f"  pit_pillar_score columns: {built['pit_pillar_score_columns']}")
        fk = pilot_fk_requirements(built["path"])
        print(f"\n  FOREIGN KEYS: a fresh pilot DB has EMPTY parents "
              f"{fk['empty_parents']}")
        print(f"  pit_feature needs rows in "
              f"{fk['writes_need_parents'].get('pit_feature')} before its first "
              f"insert, and pit_store.connect() sets foreign_keys=ON.")
        print(f"  => the pilot must choose: {fk['options'][0]} "
              f"(then Meter.rebaseline so the seed is not charged to the replay), "
              f"or {fk['options'][1]} (and say so in its report).")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        print("  (deleted)")

    if want_manifest:
        print("\n=== pre-pilot manifest (READ-ONLY against the real store) ===")
        store = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "shafferfineval_pit.db")
        pilot = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "shafferfineval_pilot.db")
        man = preflight_manifest(store, pilot)
        if as_json:
            print(json.dumps(man, indent=2, default=str))
        else:
            fs = man["frozen_spec"]
            src = man["source_store"]
            print(f"  spec          : {fs.get('freeze_version')} intact="
                  f"{fs.get('intact')} components={fs.get('n_components')} "
                  f"can_execute={fs.get('can_execute_replay')}")
            print(f"  digest        : {fs.get('current_digest')}")
            print(f"  code revision : {man['code_revision'].get('revision')} "
                  f"({man['code_revision'].get('branch')})")
            print(f"  store         : {src.get('file_bytes')} B, "
                  f"{src.get('page_count')} pages of {src.get('page_size')}, "
                  f"freelist {src.get('freelist_count')}, wal "
                  f"{src.get('sidecar-wal_bytes')}")
            print(f"  identity      : {src.get('identity_method')} "
                  f"schema_sha256={str(src.get('schema_sha256'))[:32]}...")
            print(f"  pristine      : {src.get('pristine')} -- "
                  f"{src.get('pristine_reading')}")
            print(f"  free space    : {man['volume'].get('free_gib')} GiB "
                  f"(floor {man['volume'].get('floor_gib')} GiB)")
            print(f"  pilot         : {man['pilot']['entity_dates']} entity-dates "
                  f"over {len(man['pilot']['dates'])} complete cross-sections "
                  f"= {man['pilot']['share_of_grid'] * 100:.2f}% of the grid")
            print(f"  MAY START     : {man['may_start']}  refusals={man['refusals']}")

    print("\n=== demo workload on a throwaway database ===")
    report = _demo()
    if as_json:
        print(json.dumps(report, indent=2, default=str))
    else:
        _print_human(report)
        proj = report["projection_demo"]
        print("\n-- PROJECTION (demo numbers, NOT the pilot) --------------------")
        for k, val in proj["components"].items():
            print(f"  {k:<44} {val} ({gib(val)})")
        print(f"  {'required_space_bytes':<44} {proj['required_space_bytes']} "
              f"({gib(proj['required_space_bytes'])})")
        print(f"  {'gate threshold':<44} {proj['gate']['threshold_bytes']} "
              f"({gib(proj['gate']['threshold_bytes'])})")
    return 0 if v["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
