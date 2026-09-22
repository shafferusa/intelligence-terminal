#!/usr/bin/env python3
"""
ShafferFinEval -- scheduled daily refresh entry point.

Writes the OFFICIAL immutable close snapshot. Run from cron or launchd after
the relevant market closes; for US equities that is 16:30 America/New_York.

    # crontab -e   (16:30 ET weekdays; set CRON_TZ so DST is handled)
    CRON_TZ=America/New_York
    30 16 * * 1-5 cd /path/to/shafferfineval && /usr/bin/python3 daily_job.py

Options:
    --intraday          write a replaceable intraday snapshot instead
    --symbols A,B,C     limit the run
    --date YYYY-MM-DD   RELABEL the row. This does NOT fetch historical data.
    --allow-relabel     permit a past --date, stamped 'relabelled' forever

`--date` is a LABEL, not a time machine. The refresh always fetches today's
prices, fundamentals and macro; the date only decides what the resulting row is
called. A past date is therefore refused unless --allow-relabel is given, and
the row is then stamped 'relabelled' so no dataset can mistake it for history.
For genuine historical scores, use the point-in-time replay (pit_store).
"""

import argparse
import sys

import storage
from refresh import (SnapshotDateError, refresh_daily_scores,
                     refresh_macro_scores)


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh Shaffer Scores.")
    parser.add_argument("--intraday", action="store_true",
                        help="write a replaceable intraday snapshot")
    parser.add_argument("--symbols", help="comma-separated symbols to limit the run")
    parser.add_argument(
        "--date",
        help="snapshot date LABEL (YYYY-MM-DD). This does NOT fetch historical "
             "data: the run still fetches today's prices, fundamentals and "
             "macro, and only stamps the row with this date. A past date is "
             "refused unless --allow-relabel is also given; a future date is "
             "always refused. For real history use the point-in-time replay.")
    parser.add_argument(
        "--allow-relabel", action="store_true",
        help="permit a past --date. The row is written with "
             "snapshot_provenance='relabelled' permanently, and is excluded "
             "from every training dataset. Still fetches TODAY'S data.")
    parser.add_argument("--db", default=storage.DEFAULT_DB_PATH)
    parser.add_argument("--equities-only", action="store_true",
                        help="skip the non-equity macro pass")
    args = parser.parse_args()

    conn = storage.init_db(args.db)
    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None
    kind = storage.INTRADAY if args.intraday else storage.CLOSE

    def progress(symbol, index, total):
        print(f"  [{index}/{total}] {symbol}", flush=True)

    print(f"ShafferFinEval refresh: {kind} snapshot", flush=True)
    if args.date and args.allow_relabel:
        print(f"  RELABELLING: rows will be stamped {args.date} with data "
              f"fetched TODAY, and marked '{storage.RELABELLED}'. They are "
              f"excluded from every training dataset.", flush=True)
    try:
        summary = refresh_daily_scores(
            conn, symbols=symbols, snapshot_kind=kind,
            snapshot_date=args.date, progress=progress,
            allow_relabel=args.allow_relabel,
        )
    except SnapshotDateError as exc:
        # A usage mistake, not a crash: say exactly what is wrong and stop
        # before a single HTTP request is made.
        print(f"\n{exc}", file=sys.stderr)
        return 2

    if not args.equities_only and not symbols:
        print("\nScoring non-equity classes with wired data...", flush=True)
        macro = refresh_macro_scores(conn, snapshot_kind=kind,
                                     snapshot_date=args.date,
                                     allow_relabel=args.allow_relabel)
        print(f"  macro coverage {macro.get('macro_coverage', 0):.0%} | "
              f"scored {macro['scored']} | skipped {macro['skipped']}")
        for engine, count in sorted(macro.get("engines", {}).items()):
            print(f"    {engine}: {count}")
        for note in macro.get("notes", [])[:4]:
            print(f"    note: {note[:110]}")
        blocked = macro.get("blocked_engines") or {}
        if blocked:
            print(f"  {len(blocked)} engines have arithmetic but no wired feed "
                  f"(see README)")

    print(
        f"\nattempted {summary.attempted} | scored {summary.scored} | "
        f"failed {summary.failed} | snapshots written {summary.snapshots_written} | "
        f"already existed {summary.snapshots_existing} | "
        f"fundamental changes {summary.fundamental_changes}"
    )
    for error in summary.errors[:20]:
        print(f"  ERROR {error}")
    return 1 if summary.failed and not summary.scored else 0


if __name__ == "__main__":
    sys.exit(main())
