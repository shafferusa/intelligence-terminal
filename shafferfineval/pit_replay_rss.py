"""Peak-memory harness for the streamed replay. It MEASURES; it asserts nothing.

The question it answers is the one the streaming change was made for:

    is the WRITE-SIDE memory of `pit_replay.run_date` O(batch) rather than
    O(cross-section)?

and it answers it with numbers, not with the fact that the code was changed.

For one (or more) as-of dates and N targets it runs `pit_replay.run_pilot` in
a CHILD process -- streamed (`batch_size`) or unbatched (`batch_size=None`,
the pre-2026-09-22 behaviour) -- and records:

  * child-side, at EVERY engine tick (universe, cohorts, fact_index,
    primitives, assemble, batch_full..., write_batch..., write, checkpoint):
    WorkingSetSize, PeakWorkingSetSize, PagefileUsage (the process's private
    commit -- what the pagefile actually has to back) and PeakPagefileUsage,
    read with GetProcessMemoryInfo on the child's own handle;
  * parent-side: the same counters polled on the child's handle, so a peak
    between ticks is not missed. The child is spawned from the BASE
    interpreter (`sys._base_executable`), because a venv `python.exe` on
    Windows is a launcher stub whose PID is not the engine's; the child
    reports its own PID and the parent records whether the two agree.

COMMIT IS THE PRIMARY METRIC. The first sizing pilot died of pagefile growth,
not of working-set size, and the OS can trim a working set under pressure.
Working set is reported beside it.

The engine has two memory consumers that the owner asked to keep apart:

  1. the fact index + primitives -- loaded once per date over targets AND
     their cohort members, O(cross-section), NOT touched by streaming;
  2. the write-side row lists -- what streaming bounds.

The engine's ticks bracket them exactly: `fact_index` fires before the load,
`primitives` after it, `assemble` after resolve_primitives and index.clear(),
`batch_full` with a whole batch assembled and not yet written, `write_batch`
after the batch is committed and cleared, `write` before the final flush. On
the child's commit series c(tick):

    index_resident      = c(primitives)  - c(fact_index)
    primitives_net      = c(assemble)    - c(primitives)   (sign reported, not assumed)
    fixed_cost          = c(assemble)
    write_side_peak     = max(c(batch_full_i), c(write)) - c(assemble)
    per_batch_growth_i  = c(batch_full_i) - c(write_batch_{i-1})
    peak_reached_at     = first tick where the child's peak commit equals its final value

The invariant under test: write_side_peak is ~flat in N when streamed and
grows ~linearly in N when unbatched; per_batch_growth is ~constant in i.
Whether it holds is what the numbers say. This file never claims it.

Guards: free disk >= the engine floor before every child; during a child,
if the machine's available physical memory falls below 300 MiB or its
available commit below 1 GiB the child is terminated and the run recorded
as `aborted_memory_guard`. System commit is read before and after each child.

The pilot DBs go to a scratch directory and are deleted after each child.
The main store is opened by the child exactly as the engine always opens it:
read-only. One child runs at a time; nothing here is parallel.

    python pit_replay_rss.py --limits 1000 4000 --batch 500 --out rss_report.json
    python pit_replay_rss.py --limits 1000 --modes stream --dates 2019-06-28 2022-06-30
    python pit_replay_rss.py --limits 4000 --modes stream --cache-kib 2000   # DIAGNOSTIC
"""

from __future__ import annotations

import argparse
import ctypes
import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

GIB = 1024 ** 3
MIB = 1024 ** 2
UNKNOWN = "UNKNOWN"
HARNESS_VERSION = "pit_replay_rss/1.1"

#: Parent-side memory guard. CONVENTIONS, not measurements.
GUARD_MIN_AVAIL_PHYS = 300 * MIB
GUARD_MIN_AVAIL_COMMIT = 1 * GIB


# --------------------------------------------------------------------------
# Windows process-memory counters (ctypes; no third-party dependency)
# --------------------------------------------------------------------------

class _MEMORYCOUNTERS(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]


_UNKNOWN_COUNTERS = {"ws": UNKNOWN, "peak_ws": UNKNOWN,
                     "commit": UNKNOWN, "peak_commit": UNKNOWN}


def memory_counters(handle: Optional[int] = None) -> Dict[str, Any]:
    """Working set / peak / private commit / peak commit, or UNKNOWN."""
    try:
        counters = _MEMORYCOUNTERS()
        counters.cb = ctypes.sizeof(_MEMORYCOUNTERS)
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                               ctypes.c_ulong]
        psapi.GetProcessMemoryInfo.restype = ctypes.c_int
        target = handle if handle is not None else kernel32.GetCurrentProcess()
        ok = psapi.GetProcessMemoryInfo(ctypes.c_void_p(target),
                                        ctypes.byref(counters), counters.cb)
        if not ok:
            return dict(_UNKNOWN_COUNTERS)
        return {"ws": int(counters.WorkingSetSize),
                "peak_ws": int(counters.PeakWorkingSetSize),
                "commit": int(counters.PagefileUsage),
                "peak_commit": int(counters.PeakPagefileUsage)}
    except Exception:
        return dict(_UNKNOWN_COUNTERS)


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def machine_memory() -> Dict[str, Any]:
    """Machine-wide physical / commit availability, or UNKNOWN off Windows."""
    try:
        status = _MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError("GlobalMemoryStatusEx failed")
        return {"total_phys": int(status.ullTotalPhys),
                "avail_phys": int(status.ullAvailPhys),
                "total_commit": int(status.ullTotalPageFile),
                "avail_commit": int(status.ullAvailPageFile),
                "used_commit": int(status.ullTotalPageFile
                                   - status.ullAvailPageFile),
                "memory_load_pct": int(status.dwMemoryLoad)}
    except Exception:
        return {"total_phys": UNKNOWN, "avail_phys": UNKNOWN,
                "total_commit": UNKNOWN, "avail_commit": UNKNOWN,
                "used_commit": UNKNOWN}


def _mib(n: Any) -> Any:
    return round(n / MIB, 1) if isinstance(n, int) else n


def _open_process(pid: int) -> Optional[int]:
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int,
                                         ctypes.c_ulong]
        # PROCESS_QUERY_INFORMATION | PROCESS_VM_READ
        handle = kernel32.OpenProcess(0x0400 | 0x0010, False, pid)
        return int(handle) if handle else None
    except Exception:
        return None


def _close_handle(handle: Optional[int]) -> None:
    if handle:
        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle(ctypes.c_void_p(handle))
        except Exception:
            pass


# --------------------------------------------------------------------------
# the CHILD: one run, ticks as JSON lines on stdout
# --------------------------------------------------------------------------

def child_main(dates: List[str], limit: int, batch: Optional[int],
               db_path: str, cache_kib: int = 0, measure: bool = False) -> int:
    import pit_replay
    t0 = time.monotonic()

    def emit(obj: Dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(obj, separators=(",", ":")) + "\n")
        sys.stdout.flush()

    diagnostic: Optional[Dict[str, Any]] = None
    if cache_kib and cache_kib > 0:
        # DIAGNOSTIC ONLY, runtime only, this child only: shrink the pilot
        # writer's page cache so that growth attributable to SQLite's cache
        # (bounded by the engine's cache_size pragma, 64 MiB) can be told
        # apart from growth in Python objects. The engine file is not edited
        # and no row written under this setting is used for anything.
        original = pit_replay.PILOT_PRAGMAS
        pit_replay.PILOT_PRAGMAS = tuple(
            (("cache_size", -int(cache_kib)) if name == "cache_size"
             else (name, value)) for name, value in original)
        diagnostic = {"label": "DIAGNOSTIC_PAGE_CACHE_OVERRIDE",
                      "cache_size_kib": int(cache_kib),
                      "engine_default": dict(original).get("cache_size"),
                      "rows_are_for_measurement_only": True}

    def progress(info: Dict[str, Any]) -> None:
        mem = memory_counters()
        emit(dict({"tick": info.get("stage"), "as_of": info.get("as_of"),
                   "elapsed_s": round(time.monotonic() - t0, 2)},
                  **{k: v for k, v in info.items()
                     if k not in ("stage", "as_of", "elapsed_s")},
                  **mem))

    emit({"child": "start", "pid": os.getpid(), "executable": sys.executable,
          "dates": dates, "limit": limit, "batch_size": batch, "db": db_path,
          "measure": measure, "diagnostic": diagnostic, **memory_counters()})
    error = None
    report: Optional[Dict[str, Any]] = None
    try:
        # measure=True reproduces the sizing pilot's conditions (meter thread,
        # dbstat probe, per-table scopes) so the meter's own footprint is in
        # the measurement; the default leaves it out.
        report = pit_replay.run_pilot(
            dates, db_path, entity_limit=limit, overwrite=True,
            measure=measure, per_table_scopes=measure, progress=progress,
            batch_size=batch)
    except BaseException as exc:                      # measurement survives
        error = {"type": exc.__class__.__name__, "message": str(exc)}
    per_date = []
    for stats in ((report or {}).get("dates") or []):
        per_date.append({k: stats.get(k) for k in (
            "as_of", "rows", "n_targets", "n_targets_written",
            "n_entities_indexed", "n_batches", "batch_size", "n_scored",
            "signatures", "checkpoint", "wal_peak_bytes_observed",
            "wal_peak_batch", "elapsed_s")})
    emit({"child": "done", "elapsed_s": round(time.monotonic() - t0, 2),
          "error": error,
          "aborted": (report or {}).get("aborted"),
          "dates": per_date,
          "freeze_unchanged": (report or {}).get("freeze_unchanged"),
          **memory_counters()})
    return 0 if error is None else 1


# --------------------------------------------------------------------------
# the PARENT: spawn, poll, guard, collect the ticks, attribute
# --------------------------------------------------------------------------

def _series(ticks: List[Dict[str, Any]], as_of: Optional[str],
            metric: str) -> Dict[str, Any]:
    """Attribute one metric ('commit' or 'ws') over one date's ticks."""
    mine = [t for t in ticks if as_of is None or t.get("as_of") == as_of]

    def at(stage: str) -> Any:
        for t in mine:
            if t.get("tick") == stage and isinstance(t.get(metric), int):
                return t[metric]
        return UNKNOWN

    def sub(a: Any, b: Any) -> Any:
        return a - b if isinstance(a, int) and isinstance(b, int) else UNKNOWN

    full = [t for t in mine if t.get("tick") == "batch_full"
            and isinstance(t.get(metric), int)]
    after = [t for t in mine if t.get("tick") == "write_batch"
             and isinstance(t.get(metric), int)]
    fixed = at("assemble")
    candidates = [t[metric] for t in full]
    if isinstance(at("write"), int):
        candidates.append(at("write"))
    write_side_peak = sub(max(candidates), fixed) if candidates else UNKNOWN
    growth: List[Any] = []
    for i, t in enumerate(full):
        prev = after[i - 1][metric] if i >= 1 and i - 1 < len(after) else fixed
        growth.append(sub(t[metric], prev))
    growth_ints = [g for g in growth if isinstance(g, int)]
    return {
        "at_tick_mib": {s: _mib(at(s)) for s in (
            "universe", "cohorts", "fact_index", "primitives", "assemble",
            "write", "checkpoint")},
        "index_resident_mib": _mib(sub(at("primitives"), at("fact_index"))),
        "primitives_net_mib": _mib(sub(at("assemble"), at("primitives"))),
        "fixed_cost_mib": _mib(fixed),
        "fixed_cost_bytes": fixed,
        "write_side_peak_mib": _mib(write_side_peak),
        "write_side_peak_bytes": write_side_peak,
        "batch_full_mib": [_mib(t[metric]) for t in full],
        "write_batch_mib": [_mib(t[metric]) for t in after],
        "per_batch_growth_mib": [_mib(g) for g in growth],
        "per_batch_growth_max_over_median": (
            round(max(growth_ints) / sorted(growth_ints)[len(growth_ints) // 2], 3)
            if growth_ints and sorted(growth_ints)[len(growth_ints) // 2] > 0
            else UNKNOWN),
    }


def _peak_reached_at(ticks: List[Dict[str, Any]], final_peak: Any,
                     key: str) -> Any:
    if not isinstance(final_peak, int):
        return UNKNOWN
    for t in ticks:
        if isinstance(t.get(key), int) and t[key] >= final_peak:
            return "%s@%s" % (t.get("tick"), t.get("as_of"))
    return "after_last_tick"


def run_child(dates: List[str], limit: int, batch: Optional[int],
              db_path: str, poll_s: float = 0.2,
              cache_kib: int = 0, measure: bool = False) -> Dict[str, Any]:
    # THE BASE INTERPRETER, not the venv stub: the engine is stdlib-only and
    # the harness puts its own folder on sys.path, so nothing from the venv
    # is needed, and the PID the parent polls is then the engine's.
    exe = getattr(sys, "_base_executable", None) or sys.executable
    argv = [exe, os.path.abspath(__file__), "--child",
            "--dates", *dates, "--limit", str(limit),
            "--batch", str(batch if batch is not None else 0),
            "--db", db_path, "--cache-kib", str(int(cache_kib or 0))]
    if measure:
        argv.append("--measure")
    env = dict(os.environ)
    env["PYTHONPATH"] = HERE + (os.pathsep + env["PYTHONPATH"]
                                if env.get("PYTHONPATH") else "")
    before = machine_memory()
    started = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    t0 = time.monotonic()
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, cwd=HERE, text=True,
                            encoding="utf-8", errors="replace", bufsize=1,
                            env=env)
    handle = _open_process(proc.pid)
    parent_peak: Dict[str, Any] = {"peak_ws": UNKNOWN, "peak_commit": UNKNOWN}
    machine_low: Dict[str, Any] = {"avail_phys": None, "avail_commit": None}
    ticks: List[Dict[str, Any]] = []
    start_holder: List[Dict[str, Any]] = []
    done_holder: List[Dict[str, Any]] = []
    other_lines: List[str] = []
    guard_abort: Optional[Dict[str, Any]] = None

    def _reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                other_lines.append(line[:300])
                continue
            if obj.get("child") == "start":
                start_holder.append(obj)
            elif obj.get("child") == "done":
                done_holder.append(obj)
            elif "tick" in obj:
                ticks.append(obj)
            else:
                other_lines.append(line[:300])

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()
    while proc.poll() is None:
        if handle:
            sample = memory_counters(handle)
            if isinstance(sample["peak_ws"], int):
                parent_peak = {"peak_ws": sample["peak_ws"],
                               "peak_commit": sample["peak_commit"]}
        mm = machine_memory()
        for k in ("avail_phys", "avail_commit"):
            if isinstance(mm.get(k), int):
                machine_low[k] = (mm[k] if machine_low[k] is None
                                  else min(machine_low[k], mm[k]))
        if (isinstance(mm.get("avail_phys"), int)
                and (mm["avail_phys"] < GUARD_MIN_AVAIL_PHYS
                     or mm["avail_commit"] < GUARD_MIN_AVAIL_COMMIT)):
            guard_abort = {"reason": "aborted_memory_guard",
                           "avail_phys": mm["avail_phys"],
                           "avail_commit": mm["avail_commit"],
                           "at_elapsed_s": round(time.monotonic() - t0, 1),
                           "last_tick": (ticks[-1].get("tick") if ticks
                                         else None)}
            try:
                proc.kill()
            except Exception:
                pass
            break
        time.sleep(poll_s)
    proc.wait()
    if handle:
        sample = memory_counters(handle)
        if isinstance(sample["peak_ws"], int):
            parent_peak = {"peak_ws": sample["peak_ws"],
                           "peak_commit": sample["peak_commit"]}
        _close_handle(handle)
    reader.join(timeout=10)
    stderr = proc.stderr.read() if proc.stderr else ""
    start = start_holder[0] if start_holder else None
    done = done_holder[0] if done_holder else None
    after = machine_memory()

    parent_measures_engine = bool(start and start.get("pid") == proc.pid)
    child_peaks = {
        k: max([t[k] for t in ticks if isinstance(t.get(k), int)]
               + ([done[k]] if done and isinstance(done.get(k), int) else [])
               or [UNKNOWN])
        for k in ("peak_ws", "peak_commit")}

    def best(k: str) -> Any:
        p, c = parent_peak.get(k), child_peaks.get(k)
        if parent_measures_engine and isinstance(p, int) and isinstance(c, int):
            return max(p, c)
        return c if isinstance(c, int) else p

    peak_commit, peak_ws = best("peak_commit"), best("peak_ws")
    date_list = [d for d in dates]
    per_date = {d: {"commit": _series(ticks, d, "commit"),
                    "ws": _series(ticks, d, "ws")} for d in date_list}
    universe_commit = {d: per_date[d]["commit"]["at_tick_mib"]["universe"]
                       for d in date_list}
    first = per_date[date_list[0]]
    return {
        "dates": date_list, "limit": limit, "batch_size": batch,
        "mode": "stream" if batch else "legacy",
        "diagnostic": (start or {}).get("diagnostic"),
        "measure": measure,
        "started_at": started,
        "elapsed_s": round(time.monotonic() - t0, 1),
        "returncode": proc.returncode,
        "child_pid_expected": proc.pid,
        "child_pid_reported": (start or {}).get("pid"),
        "child_executable": (start or {}).get("executable"),
        "parent_measures_engine": parent_measures_engine,
        "guard_abort": guard_abort,
        "peak_commit_bytes": peak_commit, "peak_commit_mib": _mib(peak_commit),
        "peak_ws_bytes": peak_ws, "peak_ws_mib": _mib(peak_ws),
        "peak_commit_parent_polled_mib": _mib(parent_peak.get("peak_commit")),
        "peak_commit_child_reported_mib": _mib(child_peaks.get("peak_commit")),
        "peak_reached_at_commit": _peak_reached_at(
            ticks, child_peaks.get("peak_commit"), "peak_commit"),
        "peak_reached_at_ws": _peak_reached_at(
            ticks, child_peaks.get("peak_ws"), "peak_ws"),
        # first-date headline numbers (commit-based) for the summary table
        "fixed_cost_commit_mib": first["commit"]["fixed_cost_mib"],
        "fixed_cost_commit_bytes": first["commit"]["fixed_cost_bytes"],
        "write_side_peak_commit_mib": first["commit"]["write_side_peak_mib"],
        "write_side_peak_commit_bytes": first["commit"]["write_side_peak_bytes"],
        "write_side_peak_ws_mib": first["ws"]["write_side_peak_mib"],
        "per_batch_growth_commit_mib": first["commit"]["per_batch_growth_mib"],
        "per_batch_growth_max_over_median": first["commit"][
            "per_batch_growth_max_over_median"],
        "per_date": per_date,
        "universe_commit_mib_by_date": universe_commit,
        "cross_date_growth_mib": (
            _mib(per_date[date_list[-1]]["commit"]["at_tick_mib"]["universe"]
                 * MIB - per_date[date_list[0]]["commit"]["at_tick_mib"]["universe"]
                 * MIB)
            if len(date_list) > 1 and all(
                isinstance(per_date[d]["commit"]["at_tick_mib"]["universe"],
                           float) for d in (date_list[0], date_list[-1]))
            else None),
        "attribution_note": (
            "fixed_cost is the process commit at the 'assemble' tick: fact "
            "index loaded, primitives resolved, index cleared, nothing "
            "assembled. write_side_peak = max(commit at batch_full ticks, "
            "commit at 'write') - fixed_cost. The index is cleared before "
            "'assemble' but Python need not return its pages to the OS, so "
            "fixed_cost is an upper bound on the once-per-date cost and "
            "write_side_peak attributes only what assembly and writing "
            "added on top of it."),
        "child_start": start, "child_done": done,
        "n_ticks": len(ticks),
        "ticks": ticks,
        "stderr_tail": stderr.strip().splitlines()[-8:],
        "other_stdout_lines": other_lines[-8:],
        "machine_before": before, "machine_after": after,
        "machine_low_water": machine_low,
        "system_commit_delta_mib": (
            _mib(after["used_commit"] - before["used_commit"])
            if isinstance(after.get("used_commit"), int)
            and isinstance(before.get("used_commit"), int) else UNKNOWN),
    }


def _free_bytes(path: str) -> int:
    return shutil.disk_usage(path).free


def parent_main(args: argparse.Namespace) -> int:
    import pit_replay
    import pit_frozen_spec
    freeze = pit_frozen_spec.verify()
    can, why = pit_frozen_spec.can_execute_replay()
    scratch = tempfile.mkdtemp(prefix="pit_replay_rss_")
    modes = [m for m in args.modes.split(",") if m]
    dates = list(args.dates)
    out: Dict[str, Any] = {
        "harness": HARNESS_VERSION,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(
            timespec="seconds"),
        "engine": pit_replay.ENGINE_VERSION,
        "freeze": {"version": pit_frozen_spec.SPEC_FREEZE_VERSION,
                   "digest": freeze.get("current_digest"),
                   "intact": freeze.get("intact"),
                   "can_execute": can, "why_not": why},
        "dates": dates, "limits": args.limits, "batch_size": args.batch,
        "modes": modes, "free_floor_bytes": pit_replay.FREE_FLOOR_BYTES,
        "guards": {"min_avail_phys_bytes": GUARD_MIN_AVAIL_PHYS,
                   "min_avail_commit_bytes": GUARD_MIN_AVAIL_COMMIT},
        "measure": bool(args.measure),
        "cache_kib_override": (args.cache_kib or None),
        "diagnostic_note": (
            "cache_kib_override, when set, shrinks the pilot writer's SQLite "
            "page cache in the child at runtime for ATTRIBUTION only; the "
            "engine's recorded pragma is untouched and such runs are not "
            "replay evidence." if args.cache_kib else None),
        "scratch": scratch,
        "machine_at_start": machine_memory(),
        "free_bytes_at_start": _free_bytes(HERE),
        "runs": [],
        "verdict": None,
    }
    print("pit_replay_rss %s  freeze=%s intact=%s can_execute=%s"
          % (HARNESS_VERSION, str(freeze.get("current_digest"))[:12],
             freeze.get("intact"), can), flush=True)
    if not (freeze.get("intact") and can):
        out["verdict"] = "REFUSED: freeze not intact / cannot execute"
        _write(args.out, out)
        return 2
    try:
        for limit in args.limits:
            for mode in modes:
                batch = args.batch if mode == "stream" else None
                free = _free_bytes(HERE)
                mm = machine_memory()
                if free < pit_replay.FREE_FLOOR_BYTES:
                    out["runs"].append({"limit": limit, "mode": mode,
                                        "skipped": "free %.3f GiB below floor"
                                                   % (free / GIB)})
                    print("  SKIP limit=%d mode=%s: free %.3f GiB below floor"
                          % (limit, mode, free / GIB), flush=True)
                    continue
                if (isinstance(mm.get("avail_phys"), int)
                        and (mm["avail_phys"] < GUARD_MIN_AVAIL_PHYS
                             or mm["avail_commit"] < GUARD_MIN_AVAIL_COMMIT)):
                    out["runs"].append({"limit": limit, "mode": mode,
                                        "skipped": "machine memory below guard",
                                        "machine": mm})
                    print("  SKIP limit=%d mode=%s: machine memory below guard"
                          % (limit, mode), flush=True)
                    continue
                db_path = os.path.join(scratch, "rss_%s_%d.db" % (mode, limit))
                print("  RUN  limit=%d mode=%s batch=%s dates=%s cache_kib=%s ..."
                      % (limit, mode, batch, ",".join(dates),
                         args.cache_kib or "engine"), flush=True)
                run = run_child(dates, limit, batch, db_path,
                                cache_kib=args.cache_kib,
                                measure=bool(args.measure))
                out["runs"].append(run)
                print("       peak_commit=%s MiB  fixed(assemble)=%s MiB  "
                      "write_side=%s MiB  growth/batch=%s  batches=%s  "
                      "pid_ok=%s  rc=%s  %.0fs"
                      % (run["peak_commit_mib"], run["fixed_cost_commit_mib"],
                         run["write_side_peak_commit_mib"],
                         run["per_batch_growth_commit_mib"],
                         [d.get("n_batches") for d in
                          ((run.get("child_done") or {}).get("dates") or [])],
                         run["parent_measures_engine"],
                         run["returncode"], run["elapsed_s"]), flush=True)
                _write(args.out, out)                      # progress survives
                for suffix in ("", "-wal", "-shm", "-journal"):
                    try:
                        os.remove(db_path + suffix)
                    except OSError:
                        pass
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
        out["machine_at_end"] = machine_memory()
        out["free_bytes_at_end"] = _free_bytes(HERE)
        out["verdict"] = _verdict(out["runs"])
        _write(args.out, out)
    print()
    print(_render(out))
    return 0


def _verdict(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Numbers side by side. The reader draws the conclusion; this does not."""
    by: Dict[str, Dict[int, Dict[str, Any]]] = {}
    for r in runs:
        if "skipped" in r or not isinstance(r.get("write_side_peak_commit_bytes"), int):
            continue
        by.setdefault(r["mode"], {})[r["limit"]] = r
    table: Dict[str, Any] = {}
    for mode, per_limit in by.items():
        limits = sorted(per_limit)
        table[mode] = {
            "limits": limits,
            "peak_commit_mib": [per_limit[n]["peak_commit_mib"] for n in limits],
            "fixed_cost_commit_mib": [per_limit[n]["fixed_cost_commit_mib"]
                                      for n in limits],
            "write_side_peak_commit_mib": [
                per_limit[n]["write_side_peak_commit_mib"] for n in limits],
            "write_side_peak_ws_mib": [per_limit[n]["write_side_peak_ws_mib"]
                                       for n in limits],
            "peak_reached_at": [per_limit[n]["peak_reached_at_commit"]
                                for n in limits],
        }
        if len(limits) >= 2:
            lo, hi = limits[0], limits[-1]
            d_lo = per_limit[lo]["write_side_peak_commit_bytes"]
            d_hi = per_limit[hi]["write_side_peak_commit_bytes"]
            table[mode]["write_side_ratio_hi_over_lo"] = (
                round(d_hi / d_lo, 3) if isinstance(d_lo, int) and d_lo > 0
                else UNKNOWN)
            table[mode]["target_ratio_hi_over_lo"] = round(hi / lo, 3)
    return {"table": table,
            "how_to_read": (
                "For the invariant 'write-side memory = O(batch)' to hold, "
                "stream.write_side_ratio_hi_over_lo should be near 1.0 while "
                "target_ratio_hi_over_lo is the entity ratio (e.g. 4.0), and "
                "stream per_batch_growth should be ~constant across batches. "
                "legacy.write_side_ratio_hi_over_lo near the target ratio "
                "shows the unbatched path really was O(cross-section). "
                "fixed_cost_commit_mib is the once-per-date fact-index / "
                "primitives cost and is NOT expected to be flat. "
                "peak_reached_at says which stage set the process peak; if "
                "that is primitives/assemble in both modes, the peak is the "
                "fact index and streaming cannot lower it.")}


def _render(out: Dict[str, Any]) -> str:
    lines = ["pit_replay_rss -- %s" % out["generated_at"],
             "freeze %s intact=%s" % (str(out["freeze"]["digest"])[:12],
                                      out["freeze"]["intact"]),
             "%-8s %-7s %12s %12s %12s %8s %-28s %s" % (
                 "mode", "limit", "peakC MiB", "fixedC MiB", "writeC MiB",
                 "batches", "peak reached at", "pid_ok")]
    for r in out["runs"]:
        if "skipped" in r:
            lines.append("%-8s %-7s SKIPPED %s" % (r["mode"], r["limit"], r["skipped"]))
            continue
        lines.append("%-8s %-7d %12s %12s %12s %8s %-28s %s" % (
            r["mode"], r["limit"], r["peak_commit_mib"],
            r["fixed_cost_commit_mib"], r["write_side_peak_commit_mib"],
            [d.get("n_batches") for d in
             ((r.get("child_done") or {}).get("dates") or [])],
            r["peak_reached_at_commit"], r["parent_measures_engine"]))
    v = out.get("verdict") or {}
    for mode, t in (v.get("table") or {}).items():
        lines.append("%s: write-side ratio hi/lo = %s  (targets ratio %s)"
                     % (mode, t.get("write_side_ratio_hi_over_lo"),
                        t.get("target_ratio_hi_over_lo")))
    return "\n".join(lines)


def _write(path: str, out: Dict[str, Any]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    os.replace(tmp, path)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--dates", nargs="+", default=["2019-06-28"])
    parser.add_argument("--limit", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--db", default="", help=argparse.SUPPRESS)
    parser.add_argument("--limits", type=int, nargs="+", default=[1000])
    parser.add_argument("--batch", type=int, default=500,
                        help="targets per write batch for the streamed mode")
    parser.add_argument("--modes", default="stream,legacy",
                        help="comma-separated subset of stream,legacy")
    parser.add_argument("--out", default=os.path.join(HERE, "rss_report.json"))
    parser.add_argument("--measure", action="store_true",
                        help="run the child with the engine's meter on "
                             "(measure=True, per_table_scopes=True), as the "
                             "sizing pilot did")
    parser.add_argument("--cache-kib", type=int, default=0,
                        help="DIAGNOSTIC: override the pilot writer's "
                             "cache_size (KiB) in the child to attribute "
                             "memory growth; 0 = the engine's setting")
    args = parser.parse_args(argv)
    if args.child:
        return child_main(list(args.dates), int(args.limit),
                          (args.batch or None), args.db,
                          cache_kib=int(args.cache_kib or 0),
                          measure=bool(args.measure))
    return parent_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
