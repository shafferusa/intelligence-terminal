"""Drive the SURVIVOR_ONLY_DIAGNOSTIC replay sizing pilot and measure it per date.

This is a DRIVER. It edits nothing, fits nothing, and promotes nothing. It calls
pit_replay.run_pilot() -- the tested path -- and adds the one thing that function
does not itself return: PER-DATE attribution of every measured quantity.

Per-date attribution is possible because pit_replay calls Meter.begin(day) before
each date, so every sample, table_scope and checkpoint the meter records carries
`mark == day`. The driver subclasses Meter only to keep a reference to the live
instance (run_pilot closes it and returns report(), which does not carry the raw
samples), and to capture the post-seed rebaseline snapshot.

NOTHING HERE OPENS THE MAIN STORE FOR WRITING. pit_replay.open_store() is the
only path to it and it is mode=ro + query_only=1.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
import traceback
import datetime as _dt

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pit_replay
import pit_replay_meter
import pit_replay_manifest
import pit_frozen_spec

GIB = 1024 ** 3
FLOOR = int(7.0 * GIB)
DATES = ["2014-06-30", "2019-06-28", "2022-06-30", "2026-06-30"]
PILOT_DB = os.path.join(HERE, "shafferfineval_pilot.db")
LOG_PATH = os.path.join(HERE, "pilot_sizing_run.log")
OUT_PATH = os.path.join(HERE, "pilot_sizing_report.json")

_LOG = open(LOG_PATH, "a", encoding="utf-8", buffering=1)


def log(msg):
    line = "%s  %s" % (_dt.datetime.now().strftime("%H:%M:%S"), msg)
    _LOG.write(line + "\n")
    print(line, flush=True)


class MidDateFreeSpaceAbort(RuntimeError):
    pass


_METERS = []


class CapturingMeter(pit_replay_meter.Meter):
    """Identical behaviour; keeps a reference and the rebaseline snapshot."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.post_rebaseline_snapshot = None
        _METERS.append(self)

    def rebaseline(self, reason):
        entry = super().rebaseline(reason)
        self.post_rebaseline_snapshot = dict(self._before)
        return entry


def make_progress():
    state = {"last": 0.0}

    def progress(info):
        # run_pilot checks free space BEFORE every date. This is the additional
        # in-date guard: never fill the volume to find out what happens.
        free = shutil.disk_usage(HERE).free
        if free < FLOOR:
            raise MidDateFreeSpaceAbort(
                "free %.3f GiB fell below the %.3f GiB floor mid-date at stage %r "
                "on %s" % (free / GIB, FLOOR / GIB, info.get("stage"),
                           info.get("as_of")))
        now = time.monotonic()
        if now - state["last"] > 5 or info.get("stage") in (
                "universe", "cohorts", "fact_index", "primitives",
                "assemble", "write", "checkpoint"):
            state["last"] = now
            extra = dict((k, v) for k, v in info.items()
                         if k not in ("as_of", "stage"))
            log("    [%s] %-11s %s free=%.3fGiB"
                % (info.get("as_of"), info.get("stage"), extra, free / GIB))

    return progress


# -------------------------------------------------------------------------
# per-date attribution out of the meter's own records
# -------------------------------------------------------------------------

def _maxi(values):
    vals = [v for v in values if v is not None]
    return max(vals) if vals else None


def _mini(values):
    vals = [v for v in values if v is not None]
    return min(vals) if vals else None


def per_date_measurements(meter, dates):
    """Group every raw observation by the mark it was taken under."""
    out = {}
    samples_by_mark = {}
    for s in meter._samples:                      # read-only post-processing
        samples_by_mark.setdefault(s.mark, []).append(s)

    scopes_by_mark = {}
    for sc in meter._table_scopes:
        scopes_by_mark.setdefault(sc.get("mark"), []).append(sc)

    cps_by_mark = {}
    for cp in meter._checkpoints:
        cps_by_mark.setdefault(cp.get("mark"), []).append(cp)

    marks_by_label = {}
    for mk in meter._marks:
        marks_by_label.setdefault(mk["label"], []).append(mk)

    page_size = meter.page_size()
    base = meter.post_rebaseline_snapshot or {}
    prev_file = base.get("file_bytes")
    prev_pages = base.get("page_count")

    for day in dates:
        samples = samples_by_mark.get(day, [])
        scopes = scopes_by_mark.get(day, [])
        cps = cps_by_mark.get(day, [])
        marks = marks_by_label.get(day, [])
        if not (samples or scopes or cps or marks):
            continue

        last_cp = cps[-1] if cps else None
        mark = marks[-1] if marks else None

        file_after = (last_cp or {}).get("db_file_bytes_after")
        if file_after is None and mark is not None:
            file_after = mark.get("db_file_bytes")
        pages_after = (last_cp or {}).get("page_count_after")

        per_table = {}
        for sc in scopes:
            t = sc["table"]
            entry = per_table.setdefault(t, {
                "bytes_page_delta": 0, "pages_delta": 0, "n_scopes": 0,
                "rows_delta": 0, "exclusive": True, "wal_bytes_delta": 0,
                "elapsed_s": 0.0, "bytes_unknown_reason": None,
                "dbstat_bytes_cumulative_after": None})
            entry["n_scopes"] += 1
            if sc.get("bytes") is None:
                entry["bytes_page_delta"] = None
                entry["bytes_unknown_reason"] = sc.get("bytes_reason")
            elif entry["bytes_page_delta"] is not None:
                entry["bytes_page_delta"] += sc["bytes"]
                entry["pages_delta"] += sc.get("pages_delta") or 0
            if sc.get("rows_delta") is not None:
                entry["rows_delta"] += sc["rows_delta"]
            if sc.get("exclusive") is False:
                entry["exclusive"] = False
            elif sc.get("exclusive") is None and entry["exclusive"] is not False:
                entry["exclusive"] = None
            if sc.get("wal_bytes_delta") is not None:
                entry["wal_bytes_delta"] += sc["wal_bytes_delta"]
            entry["elapsed_s"] = round(
                entry["elapsed_s"] + (sc.get("elapsed_s") or 0.0), 3)
            if sc.get("dbstat_bytes") is not None:
                entry["dbstat_bytes_cumulative_after"] = sc["dbstat_bytes"]

        out[day] = {
            "n_samples": len(samples),
            "wal_peak_bytes": _maxi(s.wal for s in samples),
            "wal_peak_is_lower_bound": True,
            "wal_bytes_before_checkpoint": (last_cp or {}).get("wal_bytes_before"),
            "wal_bytes_after_checkpoint": (last_cp or {}).get("wal_bytes_after"),
            "shm_peak_bytes": _maxi(s.shm for s in samples),
            "temp_etilqs_peak_bytes": _maxi(s.temp for s in samples),
            "rollback_journal_peak_bytes": _maxi(s.journal for s in samples),
            "free_low_water_bytes": _mini(s.free for s in samples),
            "free_bytes_at_mark": (mark or {}).get("free_bytes"),
            "db_file_bytes_before": prev_file,
            "db_file_bytes_after": file_after,
            "db_file_growth_bytes": (file_after - prev_file
                                     if (file_after is not None
                                         and prev_file is not None) else None),
            "page_count_before": prev_pages,
            "page_count_after": pages_after,
            "page_count_delta": (pages_after - prev_pages
                                 if (pages_after is not None
                                     and prev_pages is not None) else None),
            "page_size": page_size,
            "page_growth_bytes": ((pages_after - prev_pages) * page_size
                                  if (pages_after is not None
                                      and prev_pages is not None
                                      and page_size) else None),
            "checkpoints": cps,
            "per_table": per_table,
        }
        if file_after is not None:
            prev_file = file_after
        if pages_after is not None:
            prev_pages = pages_after
    return out


def main():
    log("=" * 78)
    log("SIZING PILOT -- %s" % _dt.datetime.now().isoformat(timespec="seconds"))
    log("dates: %s" % ", ".join(DATES))

    # ---- the gate, stated explicitly before anything at all --------------
    freeze = pit_frozen_spec.verify()
    can, why = pit_frozen_spec.can_execute_replay()
    log("freeze intact=%s digest=%s n_components=%s can_execute=%s %s"
        % (freeze["intact"], freeze["current_digest"], freeze["n_components"],
           can, why))
    if not (freeze["intact"] and can):
        log("REFUSED: freeze not intact / cannot execute. No writer opened.")
        return 2

    free0 = shutil.disk_usage(HERE).free
    log("free before: %.3f GiB (%d bytes)" % (free0 / GIB, free0))

    pit_replay_meter.Meter = CapturingMeter        # runtime only; no file edited

    report = None
    error = None
    t0 = time.monotonic()
    try:
        report = pit_replay.run_pilot(
            DATES, PILOT_DB,
            entity_limit=None,            # COMPLETE cross-sections
            overwrite=True,
            free_floor_bytes=FLOOR,
            measure=True,
            per_table_scopes=True,
            sample_hz=5.0,
            progress=make_progress())
    except BaseException as exc:                   # measurement must survive
        error = {"type": exc.__class__.__name__, "message": str(exc),
                 "traceback": traceback.format_exc()}
        log("ABORTED: %s: %s" % (exc.__class__.__name__, exc))

    elapsed = round(time.monotonic() - t0, 2)
    meter = _METERS[-1] if _METERS else None

    out = {
        "driver": "run_sizing_pilot.py",
        "generated_at": _dt.datetime.now(
            _dt.timezone.utc).isoformat(timespec="seconds"),
        "dates_requested": DATES,
        "free_floor_bytes": FLOOR,
        "free_bytes_before_run": free0,
        "elapsed_s": elapsed,
        "error": error,
        "replay_report": report,
        "per_date_measurements": (per_date_measurements(meter, DATES)
                                  if meter is not None else None),
        "meter_report_after_abort": (meter.report()
                                     if (meter is not None and report is None)
                                     else None),
    }

    # final on-disk truth, read back from the pilot DB itself
    try:
        import sqlite3
        conn = sqlite3.connect("file:%s?mode=ro" % PILOT_DB.replace("\\", "/"),
                               uri=True)
        counts = {}
        for t in ("pit_feature", "pit_pillar_score", "pit_score",
                  "pit_replay_run", "pit_entity", "pit_peer_set"):
            try:
                counts[t] = conn.execute(
                    "SELECT COUNT(*) FROM %s" % t).fetchone()[0]
            except Exception as exc:
                counts[t] = "UNKNOWN: %s" % exc
        by_date = {}
        for table in ("pit_feature", "pit_pillar_score", "pit_score"):
            try:
                by_date[table] = dict(conn.execute(
                    "SELECT as_of_date, COUNT(*) FROM %s GROUP BY as_of_date"
                    % table).fetchall())
            except Exception as exc:
                by_date[table] = "UNKNOWN: %s" % exc
        conn.close()
        out["pilot_db_final"] = {
            "path": PILOT_DB,
            "file_bytes": os.path.getsize(PILOT_DB),
            "wal_bytes": (os.path.getsize(PILOT_DB + "-wal")
                          if os.path.exists(PILOT_DB + "-wal") else 0),
            "shm_bytes": (os.path.getsize(PILOT_DB + "-shm")
                          if os.path.exists(PILOT_DB + "-shm") else 0),
            "row_counts": counts,
            "rows_by_as_of_date": by_date,
        }
    except Exception as exc:
        out["pilot_db_final"] = {"error": "%s: %s" % (exc.__class__.__name__, exc)}

    out["free_bytes_after_run"] = shutil.disk_usage(HERE).free
    after = pit_frozen_spec.verify()
    out["freeze_after"] = {
        "intact": after["intact"],
        "digest": after["current_digest"],
        "unchanged": after["current_digest"] == freeze["current_digest"]}
    store = pit_replay_manifest.PIT_DB_PATH
    out["main_store"] = {
        "path": store,
        "file_bytes": os.path.getsize(store),
        "wal_bytes": (os.path.getsize(store + "-wal")
                      if os.path.exists(store + "-wal") else 0),
    }

    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, default=str)
    log("wrote %s (%d bytes)" % (OUT_PATH, os.path.getsize(OUT_PATH)))
    log("pilot db rows: %s"
        % json.dumps(out.get("pilot_db_final", {}).get("row_counts")))
    log("DONE in %.1f s" % elapsed)
    return 0 if error is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
