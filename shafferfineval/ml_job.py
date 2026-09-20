#!/usr/bin/env python3
"""
ShafferFinEval -- ML Lab CLI.

Deliberately separate from the daily market refresh. Daily prediction DATA
COLLECTION happens in `daily_job.py`; MODEL TRAINING happens here, on demand.
The daily job never retrains anything.

    python3 ml_job.py --labels              refresh realised outcome labels
    python3 ml_job.py --train               train + evaluate challengers (12M)
    python3 ml_job.py --train --horizon 3M
    python3 ml_job.py --status              what data exists today
    python3 ml_job.py --promote 7           explicit, manual promotion

Promotion is the only path to PRODUCTION and nothing else calls it.
"""

import argparse
import sys

import market_data as md
import ml_lab
import storage


def refresh_labels_with_dates(conn) -> dict:
    """Fill realised forward returns using dated Yahoo price history."""
    return ml_lab.refresh_outcome_labels(
        conn,
        price_history_fn=lambda symbol: md.fetch_price_history_dated(symbol),
        vti_history_fn=lambda: md.fetch_price_history_dated(md.VTI_TICKER),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="ShafferFinEval ML Lab.")
    parser.add_argument("--labels", action="store_true",
                        help="refresh realised outcome labels")
    parser.add_argument("--train", action="store_true",
                        help="train and evaluate challenger models")
    parser.add_argument("--status", action="store_true", help="show data status")
    parser.add_argument("--promote", type=int, metavar="MODEL_ID",
                        help="explicitly promote a model to PRODUCTION")
    parser.add_argument("--horizon", default="12M", choices=["1M", "3M", "6M", "12M"])
    parser.add_argument("--sampling", default=None,
                        choices=["daily", "weekly", "monthly"])
    parser.add_argument("--db", default=storage.DEFAULT_DB_PATH)
    args = parser.parse_args()

    conn = storage.init_db(args.db)

    if args.promote is not None:
        row = storage.get_model(conn, args.promote)
        if row is None:
            print(f"No model #{args.promote}.")
            return 1
        storage.set_model_status(conn, args.promote, storage.PRODUCTION)
        print(f"Promoted #{args.promote} ({row['model_name']}) to PRODUCTION.")
        print("The Shaffer Score itself is unchanged; only the score-to-return "
              "challenger status moved.")
        return 0

    if args.labels:
        print("Refreshing outcome labels...")
        summary = refresh_labels_with_dates(conn)
        print(f"  snapshots scanned : {summary['snapshots']}")
        print(f"  labels written    : {summary['labels_written']}")
        print(f"  still pending     : {summary['pending']} (not yet aged)")
        print(f"  errors            : {summary['errors']}")

    if args.status or not (args.labels or args.train):
        status = ml_lab.data_status(conn)
        print("\nDATA STATUS")
        print(f"  snapshots         : {status.get('snapshots', 0)}")
        print(f"  equities          : {status.get('assets', 0)}")
        print(f"  snapshot dates    : {status.get('dates', 0)}")
        print(f"  earliest / latest : {status.get('earliest')} / {status.get('latest')}")
        for horizon, count in sorted((status.get("labels") or {}).items()):
            print(f"  labelled {horizon:<4}     : {count}")
        print(f"  models registered : {status.get('models_registered', 0)}")
        production = status.get("production_ml_model")
        print("  production ML     : " + (production or
              "none (the human V1 calibration is production)"))

    if args.train:
        print(f"\nTraining challengers for {args.horizon}...")
        dataset, reports, calibration = ml_lab.train_all(
            conn, args.horizon, sampling=args.sampling)
        print(f"  rows={dataset.n} effective={dataset.effective_observations} "
              f"dates={dataset.unique_dates}")
        if dataset.n == 0:
            print("  INSUFFICIENT DATA: no labelled outcomes yet. Nothing was "
                  "trained and no backtest was fabricated.")
            return 0
        print(f"\n  {'MODEL':<26}{'MAE':>9}{'SPEARMAN':>10}{'DIR':>8}  STATUS")
        for report in reports:
            mae = report.metrics.get("mae")
            sp = report.metrics.get("spearman")
            da = report.metrics.get("direction_accuracy")
            print(f"  {report.name[:25]:<26}"
                  f"{'--' if mae is None else format(mae, '.4f'):>9}"
                  f"{'--' if sp is None else format(sp, '.3f'):>10}"
                  f"{'--' if da is None else format(da, '.3f'):>8}  {report.status}")
        if calibration.beta is not None:
            print(f"\n  learned calibration: return% = {calibration.alpha:+.3f} "
                  f"+ {calibration.beta:.4f} x score   (production: 0.20 x score)")
        print("\n  Registered as RESEARCH. Production is unchanged.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
