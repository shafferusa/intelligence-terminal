"""THE SCOPED EPS INGEST. The only writer in this phase, and it guards the disk.

APPROVED SCOPE, measured before it was approved and not to be exceeded:

    the PRICED universe ONLY -- 2,533 price-resolvable entities
    ~5,066 requests (two concepts each), ~8.4 minutes at the SEC's 10 req/s
    ~0.529 GiB retained

The 16,148-entity version was explicitly REFUSED: "1.384 GiB on a 96%-full
volume to price peers that have no price is not a trade worth making." This
runner enumerates the priced universe the same way the scope measurement did --
`pit_identity.scored_universe_as_of` over the first, middle and last grid dates
plus the three census dates -- so the set it fetches is the set that was costed.

DISK IS THE BINDING CONSTRAINT AND IT IS CHECKED, NOT ASSUMED.

    * `shutil.disk_usage` before the run and before every batch. Below
      `--free-floor-gib` the run STOPS CLEANLY and reports how far it got.
      It never tries to finish on a volume that cannot hold the result.
    * `PRAGMA wal_checkpoint(TRUNCATE)` after every batch, AND ITS RETURN VALUE
      IS READ. The pragma returns (busy, log_pages, checkpointed_pages) and a
      busy of 1 means IT DID NOTHING, silently. That is not hypothetical here:
      a checkpoint that quietly did nothing is what once let this store's WAL
      reach 9.3 GB and fill this volume. A busy checkpoint is counted, and a
      run of them aborts.
    * Peak disk consumption and peak WAL are MEASURED and reported. An
      unmeasured quantity is reported as UNKNOWN, never as 0.

RESUMABLE. `pit_eps_ingest` records one row per (entity, concept, source)
including the 404s, so a re-run skips any entity whose primary concepts are
already recorded. A run that aborts on disk is therefore a run that can be
finished later rather than one that has to be started again.

    python pit_eps_run.py [--limit N] [--out DIR] [--free-floor-gib 3.0]
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import time
from typing import Any, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_eps
import pit_identity
import pit_store

#: The census dates the coverage deliverable is reported at, and the same three
#: the scope measurement used.
CENSUS_DATES = ("2015-06-30", "2019-06-28", "2024-06-28")

#: Stop rather than fill the volume. 3 GiB is not a round number chosen for
#: comfort: the expected retained size is ~0.529 GiB and SQLite needs room for a
#: WAL and a checkpoint of the pages it is rewriting, so the floor has to sit
#: well above the result's own size.
DEFAULT_FREE_FLOOR_GIB = 3.0

#: A WAL this large means the checkpointer is not keeping up. Abort.
DEFAULT_WAL_ABORT_MIB = 512

#: Entities per batch. Small enough that an abort loses at most this much work,
#: large enough that the checkpoint is not paid per issuer.
DEFAULT_BATCH = 25


def priced_universe(conn: sqlite3.Connection) -> dict[int, str]:
    """entity_id -> CIK for the priced universe, enumerated as the scope was.

    The union of `scored_universe_as_of` over the first, middle and last grid
    dates plus the three census dates. Reproduced here rather than read from a
    file so that the set fetched is provably the set costed; it comes to 2,533
    entities, which is the number the owner approved.
    """
    import pit_cohort_scan
    dates = pit_cohort_scan.grid_dates(conn)
    probe = (dates[0], dates[len(dates) // 2], dates[-1]) + CENSUS_DATES
    out: dict[int, str] = {}
    for day in probe:
        for row in pit_identity.scored_universe_as_of(conn, day):
            out.setdefault(int(row["entity_id"]), str(row["cik"]))
    return out


def already_done(conn: sqlite3.Connection) -> set[int]:
    """Entities whose PRIMARY concepts are already recorded, 404s included."""
    wanted = len(pit_eps.EPS_LADDER_PRIMARY)
    placeholders = ", ".join("?" * wanted)
    return {int(r[0]) for r in conn.execute(
        f"""SELECT entity_id FROM pit_eps_ingest
             WHERE concept_key IN ({placeholders}) AND source = ?
             GROUP BY entity_id HAVING COUNT(DISTINCT concept_key) >= ?""",
        list(pit_eps.EPS_LADDER_PRIMARY) + [pit_eps.SOURCE_COMPANYCONCEPT, wanted])}


def checkpoint(conn: sqlite3.Connection) -> dict[str, Any]:
    """`PRAGMA wal_checkpoint(TRUNCATE)` WITH ITS RETURN VALUE READ.

    The pragma returns one row: (busy, log_pages, checkpointed_pages). A `busy`
    of 1 means the checkpoint DID NOTHING -- another connection held a read
    snapshot -- and it returns that silently, with no exception and no warning.
    Treating the call as fire-and-forget is what once produced a 9.3 GB WAL on
    this volume. So the row is read, returned, and counted by the caller.
    """
    try:
        row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    except sqlite3.Error as exc:
        return {"busy": None, "log_pages": None, "checkpointed_pages": None,
                "error": str(exc), "did_nothing": True}
    if row is None:
        return {"busy": None, "log_pages": None, "checkpointed_pages": None,
                "error": "pragma returned no row", "did_nothing": True}
    busy, log_pages, done_pages = (int(row[0]), int(row[1]), int(row[2]))
    return {"busy": busy, "log_pages": log_pages, "checkpointed_pages": done_pages,
            "did_nothing": busy != 0}


def wal_bytes(db_path: str) -> int:
    try:
        return os.path.getsize(db_path + "-wal")
    except OSError:
        return 0


def dir_bytes(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def rederive(db_path: str, out_dir: str, batch_size: int = 200,
             free_floor: float = DEFAULT_FREE_FLOOR_GIB) -> int:
    """Rebuild `pit_eps_ttm` from the stored primitives. No network.

    The DERIVED table is a deterministic function of `pit_eps_obs` and the
    versioned TTM rule, so rebuilding it under a changed rule is a
    recomputation, not a loss of published history -- the primitives, which ARE
    the published history, are not touched and their append-only triggers stay
    armed. That is the whole reason primitives and derived quantities live in
    different tables.

    Used once, to apply the re-affirmation rule: a derived row per CHANGE in the
    derived value, not per re-filing of an unchanged number.
    """
    volume = os.path.dirname(os.path.abspath(db_path))
    started = time.time()
    usage0 = shutil.disk_usage(volume)
    db0 = os.path.getsize(db_path)
    if usage0.free / 2**30 < free_floor:
        print(f"ABORT: free {usage0.free / 2**30:.2f} GiB is below the floor")
        return 2
    conn = pit_store.connect(db_path)
    conn.execute("PRAGMA busy_timeout = 60000")
    entities = [int(r[0]) for r in conn.execute(
        "SELECT DISTINCT entity_id FROM pit_eps_obs ORDER BY entity_id")]
    before = int(conn.execute("SELECT COUNT(*) FROM pit_eps_ttm").fetchone()[0])
    print(f"rederiving TTM for {len(entities):,} entities "
          f"(dropping {before:,} existing derived rows)", flush=True)
    conn.execute("DROP TABLE IF EXISTS pit_eps_ttm")
    conn.execute("DROP TRIGGER IF EXISTS trg_pit_eps_ttm_no_update")
    conn.commit()
    pit_eps.ensure_schema(conn)
    checkpoint(conn)

    written = reaffirmations = 0
    min_free = usage0.free
    peak_wal = wal_bytes(db_path)
    for index, entity_id in enumerate(entities):
        result = pit_eps.derive_ttm_for_entity(conn, entity_id)
        written += result["rows_written"]
        reaffirmations += result["reaffirmations"]
        if (index + 1) % batch_size == 0 or index == len(entities) - 1:
            conn.commit()
            checkpoint(conn)
            usage = shutil.disk_usage(volume)
            min_free = min(min_free, usage.free)
            peak_wal = max(peak_wal, wal_bytes(db_path))
            if usage.free / 2**30 < free_floor:
                print("ABORT on the disk floor", flush=True)
                break
            print(f"  {index + 1:5d}/{len(entities)}  rows={written:,}  "
                  f"reaffirmations skipped={reaffirmations:,}  "
                  f"free={usage.free / 2**30:.2f}GiB  {time.time() - started:.0f}s",
                  flush=True)
    conn.commit()
    final = checkpoint(conn)
    conn.close()
    usage1 = shutil.disk_usage(volume)
    db1 = os.path.getsize(db_path)
    payload = {
        "mode": "rederive",
        "entities": len(entities),
        "ttm_rows_before": before,
        "ttm_rows_after": written,
        "reaffirmations_skipped": reaffirmations,
        "ttm_rule_version": pit_eps.TTM_RULE_VERSION,
        "final_checkpoint": final,
        "resources": {
            "free_gib_before": round(usage0.free / 2**30, 3),
            "free_gib_after": round(usage1.free / 2**30, 3),
            "min_free_gib_observed": round(min_free / 2**30, 3),
            "db_gib_before": round(db0 / 2**30, 4),
            "db_gib_after": round(db1 / 2**30, 4),
            "peak_wal_bytes": peak_wal,
        },
        "elapsed_seconds": round(time.time() - started, 1),
    }
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "eps_rederive.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1, default=str)
    print(f"\n{before:,} -> {written:,} derived rows "
          f"({reaffirmations:,} re-affirmations skipped); db "
          f"{db0 / 2**30:.3f} -> {db1 / 2**30:.3f} GiB; peak WAL {peak_wal:,}")
    print(f"wrote {path} in {payload['elapsed_seconds']}s")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    db_path = pit_store.DEFAULT_PIT_DB_PATH
    out_dir = os.getcwd()
    cache_dir = ""
    accepted_path = ""
    limit = 0
    batch_size = DEFAULT_BATCH
    free_floor = DEFAULT_FREE_FLOOR_GIB
    wal_abort = DEFAULT_WAL_ABORT_MIB
    do_rederive = "--rederive" in argv
    i = 0
    while i < len(argv):
        if argv[i] == "--db" and i + 1 < len(argv):
            db_path = argv[i + 1]; i += 2
        elif argv[i] == "--out" and i + 1 < len(argv):
            out_dir = argv[i + 1]; i += 2
        elif argv[i] == "--cache" and i + 1 < len(argv):
            cache_dir = argv[i + 1]; i += 2
        elif argv[i] == "--accepted" and i + 1 < len(argv):
            accepted_path = argv[i + 1]; i += 2
        elif argv[i] == "--limit" and i + 1 < len(argv):
            limit = int(argv[i + 1]); i += 2
        elif argv[i] == "--batch" and i + 1 < len(argv):
            batch_size = int(argv[i + 1]); i += 2
        elif argv[i] == "--free-floor-gib" and i + 1 < len(argv):
            free_floor = float(argv[i + 1]); i += 2
        elif argv[i] == "--wal-abort-mib" and i + 1 < len(argv):
            wal_abort = int(argv[i + 1]); i += 2
        else:
            i += 1

    if do_rederive:
        return rederive(db_path, out_dir, free_floor=free_floor)

    volume = os.path.dirname(os.path.abspath(db_path))
    started = time.time()
    usage0 = shutil.disk_usage(volume)
    db_bytes0 = os.path.getsize(db_path)
    print(f"disk before: free {usage0.free / 2**30:.2f} GiB of "
          f"{usage0.total / 2**30:.2f} ({100.0 * usage0.used / usage0.total:.1f}% used); "
          f"db {db_bytes0 / 2**30:.3f} GiB", flush=True)
    if usage0.free / 2**30 < free_floor:
        print(f"ABORT before any write: free {usage0.free / 2**30:.2f} GiB is "
              f"below the {free_floor} GiB floor", flush=True)
        return 2

    accepted_map: dict[str, Any] = {}
    if accepted_path and os.path.exists(accepted_path):
        with open(accepted_path, encoding="utf-8") as handle:
            accepted_map = json.load(handle)
        print(f"acceptance map: {len(accepted_map):,} accessions from the store",
              flush=True)

    conn = pit_store.connect(db_path)
    conn.execute("PRAGMA busy_timeout = 60000")
    schema_status = pit_eps.ensure_schema(conn)
    print(f"schema: {schema_status}", flush=True)

    universe = priced_universe(conn)
    done = already_done(conn)
    todo = [(e, c) for e, c in sorted(universe.items()) if e not in done]
    if limit:
        todo = todo[:limit]
    print(f"priced universe {len(universe):,}; already ingested {len(done):,}; "
          f"to fetch {len(todo):,} entities x {len(pit_eps.EPS_LADDER_PRIMARY)} "
          f"concepts = {len(todo) * len(pit_eps.EPS_LADDER_PRIMARY):,} requests",
          flush=True)

    try:
        import pit_dera
        next_session = pit_dera.session_resolver(conn)
    except Exception as exc:                            # pragma: no cover
        print(f"session resolver unavailable: {exc}", file=sys.stderr)
        next_session = None

    report: dict[str, Any] = {
        "db": os.path.abspath(db_path),
        "user_agent": pit_identity.SEC_USER_AGENT,
        "spec": pit_eps.eps_ladder_spec(),
        "universe_entities": len(universe),
        "already_ingested": len(done),
        "attempted": 0,
        "requests": 0,
        "fallback_entities": 0,
        "obs_rows_written": 0,
        "ttm_rows_written": 0,
        "entities_with_rows": 0,
        "http_404": 0,
        "http_other": 0,
        "empty_200": 0,
        "sign_cases_primitive": {},
        "checkpoints": [],
        "checkpoint_busy": 0,
        "aborted": None,
        "session_resolver": next_session is not None,
        "accession_map_size": len(accepted_map),
    }

    min_free = usage0.free
    peak_wal = wal_bytes(db_path)
    peak_db = db_bytes0
    peak_cache = 0
    aborted = False

    for index, (entity_id, cik) in enumerate(todo):
        try:
            result = pit_eps.ingest_entity_eps(
                conn, entity_id, cik, cache_dir=cache_dir or None,
                next_session=next_session, accepted_map=accepted_map,
                commit=False)
        except Exception as exc:                        # one bad issuer never kills a run
            report.setdefault("errors", []).append(
                {"entity_id": entity_id, "cik": cik, "error": repr(exc)})
            conn.rollback()
            continue
        report["attempted"] += 1
        report["requests"] += result["requests"]
        report["obs_rows_written"] += result["rows_written"]
        report["ttm_rows_written"] += result["ttm_rows_written"]
        report["empty_200"] += len(result["empty_200"])
        if result["fallback_used"]:
            report["fallback_entities"] += 1
        if result["rows_written"]:
            report["entities_with_rows"] += 1
        for concept in result["concepts"].values():
            if concept["http_status"] == 404:
                report["http_404"] += 1
            elif concept["http_status"] != 200:
                report["http_other"] += 1
            for case, n in concept.get("sign_cases", {}).items():
                report["sign_cases_primitive"][case] = (
                    report["sign_cases_primitive"].get(case, 0) + n)

        if (index + 1) % batch_size == 0 or index == len(todo) - 1:
            conn.commit()
            check = checkpoint(conn)
            check["after_entities"] = index + 1
            if check["did_nothing"]:
                report["checkpoint_busy"] += 1
                report["checkpoints"].append(check)
            usage = shutil.disk_usage(volume)
            wal_now = wal_bytes(db_path)
            db_now = os.path.getsize(db_path)
            min_free = min(min_free, usage.free)
            peak_wal = max(peak_wal, wal_now)
            peak_db = max(peak_db, db_now)
            if cache_dir and os.path.isdir(cache_dir):
                peak_cache = max(peak_cache, dir_bytes(cache_dir))
            if usage.free / 2**30 < free_floor:
                report["aborted"] = (
                    f"free disk {usage.free / 2**30:.2f} GiB fell below the "
                    f"{free_floor} GiB floor after {index + 1} entities")
                aborted = True
            elif wal_now > wal_abort * 1024 * 1024:
                report["aborted"] = (
                    f"WAL reached {wal_now:,} bytes after {index + 1} entities")
                aborted = True
            elif report["checkpoint_busy"] >= 5:
                report["aborted"] = (
                    "wal_checkpoint(TRUNCATE) returned busy 5 times: it is doing "
                    "nothing and the WAL will grow unbounded")
                aborted = True
            if (index + 1) % (batch_size * 8) == 0 or aborted or index == len(todo) - 1:
                print(f"  {index + 1:5d}/{len(todo)}  obs={report['obs_rows_written']:,} "
                      f"ttm={report['ttm_rows_written']:,}  "
                      f"free={usage.free / 2**30:.2f}GiB  wal={wal_now:,}  "
                      f"{time.time() - started:.0f}s", flush=True)
            if aborted:
                print("ABORT: " + report["aborted"], flush=True)
                break

    conn.commit()
    final_check = checkpoint(conn)
    report["final_checkpoint"] = final_check
    if final_check["did_nothing"]:
        report["checkpoint_busy"] += 1
    conn.close()

    usage1 = shutil.disk_usage(volume)
    db_bytes1 = os.path.getsize(db_path)
    min_free = min(min_free, usage1.free)
    peak_wal = max(peak_wal, wal_bytes(db_path))
    peak_db = max(peak_db, db_bytes1)
    if cache_dir and os.path.isdir(cache_dir):
        peak_cache = max(peak_cache, dir_bytes(cache_dir))

    report["resources"] = {
        "free_gib_before": round(usage0.free / 2**30, 3),
        "free_gib_after": round(usage1.free / 2**30, 3),
        "min_free_gib_observed": round(min_free / 2**30, 3),
        "db_gib_before": round(db_bytes0 / 2**30, 4),
        "db_gib_after": round(db_bytes1 / 2**30, 4),
        "db_growth_gib": round((db_bytes1 - db_bytes0) / 2**30, 4),
        "peak_db_gib": round(peak_db / 2**30, 4),
        "peak_wal_bytes": peak_wal,
        "peak_wal_mib": round(peak_wal / 2**20, 2),
        "peak_response_cache_bytes": peak_cache,
        "peak_response_cache_mib": round(peak_cache / 2**20, 2) if peak_cache else 0,
        "peak_disk_consumed_gib": round((usage0.free - min_free) / 2**30, 4),
        "sampling": ("free disk, WAL and db size are sampled at every batch "
                     f"boundary ({batch_size} entities) and at the end; the peak "
                     "is the max over those samples, not a continuous maximum"),
    }
    report["elapsed_seconds"] = round(time.time() - started, 1)

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "eps_ingest_run.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=1, default=str)
    print(f"\nattempted {report['attempted']:,} entities, "
          f"{report['requests']:,} requests, "
          f"{report['obs_rows_written']:,} primitive rows, "
          f"{report['ttm_rows_written']:,} derived TTM rows")
    print(f"404s {report['http_404']:,}  empty-200s {report['empty_200']:,}  "
          f"fallback entities {report['fallback_entities']:,}")
    print(f"disk: free {usage0.free / 2**30:.2f} -> {usage1.free / 2**30:.2f} GiB, "
          f"min observed {min_free / 2**30:.2f}; db grew "
          f"{(db_bytes1 - db_bytes0) / 2**30:.3f} GiB; peak WAL "
          f"{peak_wal:,} bytes; busy checkpoints {report['checkpoint_busy']}")
    print(f"wrote {path} in {report['elapsed_seconds']}s")
    return 1 if aborted else 0


if __name__ == "__main__":
    raise SystemExit(main())
