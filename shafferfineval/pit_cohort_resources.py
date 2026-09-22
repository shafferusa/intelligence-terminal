"""Measured resource cost of the cohort census. Nothing here is an estimate.

The volume this store sits on is 96% full and an ingest once let a SQLite WAL
reach 9.3 GB on it, so a measurement that cannot say what it cost is not
finished. This runs each stage under a peak-working-set probe and samples the
WAL and the free space around it.

An unmeasured quantity is reported as UNKNOWN. It is never reported as 0.

    python pit_cohort_resources.py --out <dir>
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from typing import Any, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_cohort_scan
import pit_store

UNKNOWN = "UNKNOWN"


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


def peak_working_set(handle: Optional[int] = None) -> Any:
    """Peak working set of a process in bytes, or UNKNOWN off Windows."""
    try:
        counters = _MEMORYCOUNTERS()
        counters.cb = ctypes.sizeof(_MEMORYCOUNTERS)
        target = handle if handle is not None else \
            ctypes.windll.kernel32.GetCurrentProcess()
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(
            ctypes.c_void_p(target), ctypes.byref(counters), counters.cb)
        return int(counters.PeakWorkingSetSize) if ok else UNKNOWN
    except Exception:
        return UNKNOWN


def run_stage(name: str, argv: Sequence[str], db_path: str) -> dict[str, Any]:
    """Run one stage as a child process and measure what it cost."""
    volume = os.path.dirname(os.path.abspath(db_path))
    before = shutil.disk_usage(volume)
    wal_before = pit_cohort_scan.wal_bytes(db_path)
    started = time.time()
    process = subprocess.Popen([sys.executable] + list(argv),
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               cwd=os.path.dirname(os.path.abspath(__file__)))
    wal_peak = wal_before
    peak_mem: Any = UNKNOWN
    handle = None
    try:
        handle = ctypes.windll.kernel32.OpenProcess(0x1000 | 0x0010, False,
                                                    process.pid)
    except Exception:
        handle = None
    while process.poll() is None:
        wal_peak = max(wal_peak, pit_cohort_scan.wal_bytes(db_path))
        if handle:
            sample = peak_working_set(handle)
            if sample != UNKNOWN:
                peak_mem = sample
        time.sleep(0.25)
    if handle:
        sample = peak_working_set(handle)
        if sample != UNKNOWN:
            peak_mem = sample
        ctypes.windll.kernel32.CloseHandle(handle)
    output = process.stdout.read().decode("utf-8", "replace") if process.stdout else ""
    after = shutil.disk_usage(volume)
    return {
        "stage": name, "argv": list(argv), "returncode": process.returncode,
        "seconds": round(time.time() - started, 1),
        "peak_working_set_bytes": peak_mem,
        "peak_working_set_mib": (round(peak_mem / 1024 ** 2, 1)
                                 if peak_mem != UNKNOWN else UNKNOWN),
        "wal_bytes_before": wal_before, "wal_bytes_peak": wal_peak,
        "wal_bytes_after": pit_cohort_scan.wal_bytes(db_path),
        "free_bytes_before": before.free, "free_bytes_after": after.free,
        "free_delta_bytes": after.free - before.free,
        "tail": output.strip().splitlines()[-3:],
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    out_dir = os.getcwd()
    db_path = pit_store.DEFAULT_PIT_DB_PATH
    i = 0
    while i < len(argv):
        if argv[i] == "--out" and i + 1 < len(argv):
            out_dir = argv[i + 1]; i += 2
        elif argv[i] == "--db" and i + 1 < len(argv):
            db_path = argv[i + 1]; i += 2
        else:
            i += 1

    volume = os.path.dirname(os.path.abspath(db_path))
    usage = shutil.disk_usage(volume)
    stages = [
        run_stage("scan_fundamentals",
                  ["pit_cohort_scan.py", "--out", out_dir, "--qtrs", "annual"],
                  db_path),
        run_stage("measure_cohorts",
                  ["pit_cohort_measure.py", "--masks", out_dir], db_path),
        run_stage("validate", ["pit_cohort_validate.py", "--masks", out_dir,
                               "--sample", "400"], db_path),
    ]
    artefacts = {}
    for name in ("fund_masks_annual.json", "price_shares.json",
                 "cohort_measurement.json", "cohort_report.txt", "eps_scope.json"):
        path = os.path.join(out_dir, name)
        artefacts[name] = os.path.getsize(path) if os.path.exists(path) else UNKNOWN
    payload = {
        "store_bytes": os.path.getsize(db_path),
        "store_gib": round(os.path.getsize(db_path) / 1024 ** 3, 2),
        "volume_total_gib": round(usage.total / 1024 ** 3, 2),
        "volume_free_gib": round(usage.free / 1024 ** 3, 2),
        "volume_pct_used": round(100.0 * usage.used / usage.total, 1),
        "stages": stages,
        "artefact_bytes": artefacts,
        "artefact_total_bytes": sum(v for v in artefacts.values() if v != UNKNOWN),
        "bytes_written_to_store": 0,
        "bytes_written_to_store_note": (
            "measured as zero BY CONSTRUCTION, not by sampling: every connection "
            "is opened mode=ro, which makes a write an error rather than a "
            "promise. The WAL columns are what would move if that were wrong."),
        "price_shares_stage_note": (
            "pit_cohort_price.py is not re-run here -- it takes 315s and is the "
            "one stage whose result is already on disk. Its own run recorded "
            "wal_peak 0 and is reported from price_shares.json."),
    }
    price_path = os.path.join(out_dir, "price_shares.json")
    if os.path.exists(price_path):
        with open(price_path, encoding="utf-8") as handle:
            price = json.load(handle)
        payload["price_shares_recorded"] = {
            "seconds": price.get("elapsed_seconds"),
            "wal_bytes_peak": price.get("wal_peak_bytes"),
            "blocked_retries": price.get("blocked_retries"),
            "peak_working_set_bytes": UNKNOWN,
        }
    path = os.path.join(out_dir, "resources.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1)

    print(f"store {payload['store_gib']} GiB on a volume "
          f"{payload['volume_pct_used']}% used, {payload['volume_free_gib']} GiB free")
    for stage in stages:
        print(f"  {stage['stage']:20s} rc={stage['returncode']} "
              f"{stage['seconds']:7.1f}s  peak RSS "
              f"{stage['peak_working_set_mib']} MiB  WAL peak "
              f"{stage['wal_bytes_peak']:,}  free delta "
              f"{stage['free_delta_bytes']:+,}")
    print(f"  artefacts on disk: {payload['artefact_total_bytes']:,} bytes")
    print(f"wrote {path}")
    return 0 if all(s["returncode"] == 0 for s in stages) else 1


if __name__ == "__main__":
    raise SystemExit(main())
