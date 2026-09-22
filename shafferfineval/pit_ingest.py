#!/usr/bin/env python3
"""Drive the historical point-in-time ingests.

`pit_dera.ingest_quarter` deliberately has no loop over the archive -- running
69 quarters is an operational act, not a library call. This is that act, with
the two things a long ingest needs: it is resumable, and it says what it did.

    python pit_ingest.py calendar          # populate pit_calendar (do this first)
    python pit_ingest.py dera              # the full 69-quarter fundamentals ingest
    python pit_ingest.py dera --from 2015q1 --to 2016q4
    python pit_ingest.py status            # row counts and what is already loaded

THE CALENDAR COMES FIRST. `pit_policy.available_date` needs a `next_session`
callable to turn a late acceptance into the next TRADING session. Without one it
falls back to the next calendar day and flags `session_resolved: False` -- which
happens to select identically on a month-end replay grid, but stops being true
the moment a weekly or daily grid is used. Loading the calendar before the facts
means the availability dates are right the first time, and pit_fact is immutable
so there is no second chance without a full re-ingest.

Resumability is per quarter. pit_fact's UNIQUE key makes a re-run of a completed
quarter insert zero rows (measured: 0 of 202,241), so an interrupted ingest is
restarted by running the same command again -- no bookkeeping, no cleanup.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import json
import shutil
import sys
import time
import urllib.request
from typing import Optional

import pit_dera
import pit_store

#: SPY is the calendar source: it has traded since 1993 and its bars are the
#: US equity session grid. The alternative -- generating weekdays and removing a
#: holiday list -- gets unscheduled closures wrong (Sandy closed 2012-10-29/30),
#: and those are exactly the days a naive calendar silently invents.
CALENDAR_SYMBOL = "SPY"
CALENDAR_MARKET = "XNYS"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def fetch_sessions(symbol: str = CALENDAR_SYMBOL, start_year: int = 1993) -> list[str]:
    """Every trading session from Yahoo daily bars, oldest first.

    Explicit epoch bounds and an assertion on the granularity: `range=max`
    silently coarsens to monthly (AAPL returns 169 points instead of 11,534),
    which would produce a 'calendar' of month-ends that every horizon lookup
    would then quietly agree with.
    """
    period1 = int(_dt.datetime(start_year, 1, 1).timestamp())
    period2 = int(time.time()) + 86400
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?period1={period1}&period2={period2}&interval=1d")
    request = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.load(response)
    result = payload["chart"]["result"][0]
    granularity = result.get("meta", {}).get("dataGranularity")
    if granularity != "1d":
        raise RuntimeError(
            f"{symbol} returned dataGranularity={granularity!r}, not '1d'. "
            "Refusing to build a session calendar from coarsened bars.")
    stamps = result.get("timestamp") or []
    closes = (result.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    sessions = []
    for index, stamp in enumerate(stamps):
        # A bar with no close is not a session that traded.
        if index < len(closes) and closes[index] is None:
            continue
        sessions.append(_dt.datetime.fromtimestamp(stamp, _dt.timezone.utc).date().isoformat())
    return sorted(set(sessions))


def load_calendar(conn) -> dict:
    sessions = fetch_sessions()
    written = pit_store.insert_calendar(conn, CALENDAR_MARKET, sessions,
                                        source=f"yahoo:{CALENDAR_SYMBOL}")
    month_ends = pit_store.month_end_sessions(conn, "2013-01-01", "2026-12-31")
    return {
        "sessions": len(sessions),
        "written": written,
        "first": sessions[0] if sessions else None,
        "last": sessions[-1] if sessions else None,
        "month_ends_2013_on": len(month_ends),
    }


def session_resolver(conn):
    """A `next_session` callable backed by pit_calendar."""
    def next_session(day: str) -> Optional[str]:
        row = conn.execute(
            """SELECT session_date FROM pit_calendar
                WHERE market = ? AND session_date >= ?
                ORDER BY session_date LIMIT 1""",
            (CALENDAR_MARKET, day),
        ).fetchone()
        return row["session_date"] if row else None
    return next_session


def run_dera(conn, quarters: list[str]) -> dict:
    """Ingest each quarter in order, reporting as it goes."""
    resolver = session_resolver(conn)
    if resolver("2020-01-01") is None:
        raise SystemExit(
            "pit_calendar is empty. Run `python pit_ingest.py calendar` first -- "
            "availability dates computed without a session calendar cannot be "
            "corrected later, because pit_fact is immutable.")

    free_gib = shutil.disk_usage(os.path.dirname(os.path.abspath(__file__))).free / 1024 ** 3
    needed_gib = 0.45 * len(quarters) / 10 + 1.0          # ~0.45 GiB per 10 quarters, plus headroom
    print(f"  disk free {free_gib:.1f} GiB, this run needs about {needed_gib:.1f} GiB",
          flush=True)
    if free_gib < needed_gib:
        raise SystemExit(
            f"Refusing to start: {free_gib:.1f} GiB free, about {needed_gib:.1f} GiB needed. "
            "An ingest that runs out of disk half way leaves a partial quarter and a "
            "huge WAL; failing here is cheaper than failing at quarter 29.")

    totals = {"quarters": 0, "rows_kept": 0, "rows_read": 0, "bytes": 0,
              "seconds": 0.0, "failures": []}
    started = time.time()
    for index, quarter in enumerate(quarters, 1):
        t0 = time.time()
        try:
            result = pit_dera.ingest_quarter(conn, quarter, next_session=resolver)
        except Exception as exc:                      # noqa: BLE001 - reported, not swallowed
            totals["failures"].append({"quarter": quarter, "error": repr(exc)[:300]})
            print(f"  [{index}/{len(quarters)}] {quarter}  FAILED  {exc!r}", flush=True)
            continue
        # Checkpoint and drop the source ZIP after EVERY quarter. Both matter:
        #
        # WAL: in WAL mode the log grows until a checkpoint, and a checkpoint
        # cannot run while any reader holds an older snapshot. During the first
        # attempt at this ingest, concurrent read-only queries against the store
        # kept the WAL from ever truncating -- it reached 9.3 GB against a 2.0 GB
        # database and filled the disk at quarter 29 of 69. TRUNCATE here bounds
        # it to one quarter's writes regardless of who else is reading.
        #
        # ZIP: the archive is 5.3 GiB and every quarter is re-downloadable in
        # about two seconds. Keeping 69 of them to save 140 seconds is a bad
        # trade on a machine this close to full.
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        zip_path = os.path.join(pit_dera.DEFAULT_DERA_DIR, f"{quarter}.zip")
        if os.path.exists(zip_path):
            try:
                os.remove(zip_path)
            except OSError:
                pass

        elapsed = time.time() - t0
        kept = int(result.get("rows_kept") or result.get("kept") or 0)
        read = int(result.get("rows_read") or result.get("read") or 0)
        size = int(result.get("bytes_downloaded") or result.get("bytes") or 0)
        totals["quarters"] += 1
        totals["rows_kept"] += kept
        totals["rows_read"] += read
        totals["bytes"] += size
        print(f"  [{index}/{len(quarters)}] {quarter}  kept {kept:>8,}  "
              f"read {read:>10,}  {elapsed:6.1f}s", flush=True)
    totals["seconds"] = time.time() - started
    return totals


def status(conn) -> None:
    stats = pit_store.store_stats(conn)
    for table, count in stats.items():
        if count:
            print(f"  {table:<24} {count:>12,}")
    row = conn.execute(
        """SELECT MIN(period_end), MAX(period_end), COUNT(DISTINCT entity_id),
                  COUNT(DISTINCT accn), MIN(available_date), MAX(available_date)
             FROM pit_fact""").fetchone()
    if row and row[0]:
        print(f"\n  period_end      {row[0]} .. {row[1]}")
        print(f"  available_date  {row[4]} .. {row[5]}")
        print(f"  entities        {row[2]:,}")
        print(f"  accessions      {row[3]:,}")
    quarters = conn.execute(
        """SELECT source, COUNT(*) AS n FROM pit_fact
            GROUP BY source ORDER BY source""").fetchall()
    if quarters:
        print(f"\n  quarters loaded {len(quarters)}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Historical PIT ingests.")
    parser.add_argument("command", choices=("calendar", "dera", "status"))
    parser.add_argument("--from", dest="start", help="first quarter, e.g. 2009q2")
    parser.add_argument("--to", dest="end", help="last quarter, e.g. 2026q2")
    parser.add_argument("--db", default=pit_store.DEFAULT_PIT_DB_PATH)
    args = parser.parse_args(argv)

    conn = pit_store.init_db(args.db)

    if args.command == "calendar":
        print("Loading the US equity session calendar ...", flush=True)
        summary = load_calendar(conn)
        print(f"  sessions      {summary['sessions']:,} "
              f"({summary['first']} .. {summary['last']})")
        print(f"  rows written  {summary['written']:,}")
        print(f"  month-ends from 2013  {summary['month_ends_2013_on']}")
        return 0

    if args.command == "status":
        status(conn)
        return 0

    quarters = pit_dera.archive_quarters()
    if args.start:
        quarters = [q for q in quarters if q >= args.start]
    if args.end:
        quarters = [q for q in quarters if q <= args.end]
    print(f"DERA ingest: {len(quarters)} quarters "
          f"({quarters[0]} .. {quarters[-1]})", flush=True)
    ingest_id = pit_store.start_ingest(conn, "dera:bulk",
                                       f"{quarters[0]}..{quarters[-1]}")
    totals = run_dera(conn, quarters)
    pit_store.finish_ingest(
        conn, ingest_id,
        status="complete" if not totals["failures"] else "partial",
        rows_read=totals["rows_read"], rows_kept=totals["rows_kept"],
        bytes_downloaded=totals["bytes"], summary=totals)

    print(f"\nquarters   {totals['quarters']}/{len(quarters)}")
    print(f"rows kept  {totals['rows_kept']:,} of {totals['rows_read']:,} read")
    print(f"downloaded {totals['bytes'] / 1024 ** 3:.2f} GiB")
    print(f"elapsed    {totals['seconds'] / 60:.1f} min")
    for failure in totals["failures"]:
        print(f"  FAILED {failure['quarter']}: {failure['error']}")
    print()
    status(conn)
    return 1 if totals["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
