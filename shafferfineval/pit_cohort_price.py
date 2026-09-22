"""Pass 2 of the cohort-formation census: PRICE and DEFENSIBLE SHARES per date.

READ-ONLY, and deliberately the SLOW half. Where pass 1 vectorises the fact
selector into intervals, this one calls the real functions once per
(entity, as-of) pair and does not approximate anything:

    pit_identity.scored_universe_as_of   the price test the peer sets were
                                         built with -- `price_test_scored_
                                         universe_v1`, a real bar with volume
                                         > 0 within 10 days, a listing whose
                                         validity window covers the date, and
                                         no quarantine.
    pit_rawprice.shares_as_of_any        the point-in-time share ladder,
                                         answered from pit_fact because
                                         pit_share_obs is empty in this store.
    pit_rawprice.share_class_audit       symbols + the weighted-average scale
    pit_rawprice.class_decision          check, strict policy.

EVERY RESULT HERE IS SURVIVOR_ONLY_DIAGNOSTIC. `pit_listing` holds 2,574
company lines, every one of them alive in 2026, with no series ending before
2020. A count of price-resolvable peers is therefore a count among survivors and
is not what the 2015 cross-section looked like; it is an UPPER bound on nothing
and a lower bound on nothing, it is a different population. The label travels
with the number in the output file.

Short transactions by construction: every call is one or a few indexed SELECTs
and Python's sqlite3 opens no explicit transaction for them, so no read snapshot
is held across dates. The WAL is sampled every date and the run ABORTS if it
grows past `--wal-abort-mib`, because a reader that blocks the checkpointer is
what once put a 9.3 GB WAL on this volume.

Stdlib only.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from typing import Any, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_cohort_scan
import pit_identity
import pit_rawprice
import pit_store

SAMPLE_SCOPE = pit_store.SAMPLE_SURVIVOR_ONLY

#: Sampled every date; the run stops rather than starving the checkpointer.
DEFAULT_WAL_ABORT_MIB = 1024


def measure_date(conn: sqlite3.Connection, day: str) -> dict[str, Any]:
    """Price-resolvable and defensible-share entity sets at one as-of date."""
    rows = pit_identity.scored_universe_as_of(conn, day)
    priced: dict[int, int] = {}
    for row in rows:
        entity_id = int(row["entity_id"])
        priced.setdefault(entity_id, int(row["listing_id"]))
    defensible: list[int] = []
    reasons: dict[str, int] = {}
    for entity_id in priced:
        shares = pit_rawprice.shares_as_of_any(conn, entity_id, day)
        if not shares.get("available"):
            key = str(shares.get("reason"))
            reasons[key] = reasons.get(key, 0) + 1
            continue
        if shares.get("zero_count"):
            reasons["zero_share_count"] = reasons.get("zero_share_count", 0) + 1
            continue
        audit = pit_rawprice.share_class_audit(conn, entity_id, day, shares=shares)
        decision = pit_rawprice.class_decision(audit, pit_rawprice.CLASS_POLICY_STRICT)
        if decision["ok"]:
            defensible.append(entity_id)
        else:
            key = str(decision["reason"])
            reasons[key] = reasons.get(key, 0) + 1
    return {"as_of": day, "n_priced": len(priced), "priced": sorted(priced),
            "n_defensible": len(defensible), "defensible": sorted(defensible),
            "refusals": reasons}


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    db_path = pit_store.DEFAULT_PIT_DB_PATH
    out_dir = os.getcwd()
    wal_abort = DEFAULT_WAL_ABORT_MIB
    i = 0
    while i < len(argv):
        if argv[i] == "--db" and i + 1 < len(argv):
            db_path = argv[i + 1]; i += 2
        elif argv[i] == "--out" and i + 1 < len(argv):
            out_dir = argv[i + 1]; i += 2
        elif argv[i] == "--wal-abort-mib" and i + 1 < len(argv):
            wal_abort = int(argv[i + 1]); i += 2
        else:
            i += 1

    started = time.time()
    conn = pit_cohort_scan.connect_ro(db_path)
    dates = pit_cohort_scan.grid_dates(conn)
    wal_before = pit_cohort_scan.wal_bytes(db_path)
    out: dict[str, Any] = {"sample_scope": SAMPLE_SCOPE, "dates": dates,
                           "per_date": {}, "wal_bytes_before": wal_before,
                           "wal_peak_bytes": wal_before, "blocked_retries": 0}
    for index, day in enumerate(dates):
        attempt = 0
        while True:
            try:
                record = measure_date(conn, day)
                break
            except sqlite3.OperationalError as exc:
                attempt += 1
                out["blocked_retries"] += 1
                if attempt > 5:
                    raise
                print(f"  locked at {day} ({exc}); retry {attempt}", flush=True)
                time.sleep(5 * attempt)
        out["per_date"][day] = record
        wal_now = pit_cohort_scan.wal_bytes(db_path)
        out["wal_peak_bytes"] = max(out["wal_peak_bytes"], wal_now)
        if wal_now > wal_abort * 1024 * 1024:
            out["aborted"] = f"WAL reached {wal_now:,} bytes at {day}"
            print(out["aborted"], flush=True)
            break
        if index % 10 == 0 or index == len(dates) - 1:
            print(f"  {day}  priced={record['n_priced']:5d}  "
                  f"defensible={record['n_defensible']:5d}  "
                  f"wal={wal_now:,}  {time.time() - started:.0f}s", flush=True)
    conn.close()
    out["elapsed_seconds"] = round(time.time() - started, 1)
    out["wal_bytes_after"] = pit_cohort_scan.wal_bytes(db_path)
    path = os.path.join(out_dir, "price_shares.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(out, handle)
    print(f"wrote {path} ({os.path.getsize(path):,} bytes) in "
          f"{out['elapsed_seconds']}s; WAL peak {out['wal_peak_bytes']:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
