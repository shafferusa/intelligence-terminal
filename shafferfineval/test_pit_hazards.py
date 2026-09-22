"""Regression tests for the two point-in-time leakage fences.

Pure stdlib, no network, throwaway database:   python test_pit_hazards.py

These tests exist to FAIL if either fence is removed. Both hazards are of the
same shape -- a path that produces a result which looks well-formed and is
quietly a lie -- so a test that merely exercises the happy path would not
notice their removal at all.

HAZARD A, the relabel trap. `daily_job.py --date 2020-01-01` used to succeed
and write a `score_history` row stamped 2020-01-01 holding data fetched TODAY.
`refresh.py` only ever used `snapshot_date` as a label; nothing on the table
distinguished that row from a genuine close. Fenced by
`refresh.resolve_snapshot_date` plus the `snapshot_provenance` column.

HAZARD B, the unpurged splitter. `ml_lab.walk_forward_splits` is honestly
chronological but has no purge and no embargo, so a 12M label attached to the
last training date is realised 365 days inside the test window. Fenced by
refusing point-in-time data outright.

The macro refresh is driven here with a SYNTHETIC `MacroSnapshot`, which is
what makes an end-to-end test of the write path possible with no network: the
row that lands in `score_history` is written by the real `refresh_macro_scores`
through the real `storage.save_score_snapshot`, not by the test.
"""
from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
import tempfile
import warnings
from dataclasses import dataclass
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import macro_data
import ml_lab
import refresh as R
import storage

HERE = os.path.dirname(os.path.abspath(__file__))

fails: list[str] = []


def check(name: str, cond: bool, extra: object = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {extra}")
        fails.append(name)


def _raises(thunk, exc) -> bool:
    try:
        thunk()
    except exc:
        return True
    except Exception:
        return False
    return False


def _message(thunk) -> str:
    """The text of whatever a refusing call raises, for inspecting wording."""
    try:
        thunk()
    except Exception as exc:
        return str(exc)
    return ""


# --------------------------------------------------------------------------
# A minimal asset and a synthetic macro snapshot, so the write path can be
# exercised end to end without a single HTTP request.
# --------------------------------------------------------------------------

@dataclass
class _Asset:
    symbol: str
    yahoo_symbol: Optional[str]
    name: str
    asset_class: str
    subclass: str
    sector: Optional[str] = None
    industry: Optional[str] = None
    currency: str = "USD"
    country: str = "US"
    sheet: str = "test"


_MACRO_KEYS = ("ust_10y", "ust_2y", "ust_3m", "real_10y", "breakeven_10y",
               "fed_funds", "cpi", "unemployment", "dollar_index",
               "hy_oas", "ig_oas")


def _series(key: str, n: int = 400) -> macro_data.Series:
    series = macro_data.Series(key=key, source="synthetic", identifier=key)
    base = dt.date(2020, 1, 1)
    series.dates = [base + dt.timedelta(days=i) for i in range(n)]
    series.values = [1.0 + 0.01 * i for i in range(n)]
    return series


def _fake_macro() -> macro_data.MacroSnapshot:
    return macro_data.MacroSnapshot(
        series={key: _series(key) for key in _MACRO_KEYS},
        fetched_at="2026-01-01T00:00:00+00:00")


def _provenances(conn, snapshot_date: str) -> list[str]:
    return [r["snapshot_provenance"] for r in conn.execute(
        "SELECT snapshot_provenance FROM score_history WHERE snapshot_date=?",
        (snapshot_date,))]


def main() -> int:
    db_dir = tempfile.mkdtemp()
    db = os.path.join(db_dir, "hazards.db")
    today = dt.date.today()
    today_s = today.isoformat()
    past = (today - dt.timedelta(days=2000)).isoformat()
    future = (today + dt.timedelta(days=30)).isoformat()

    # ------------------------------------------------------------------
    print("== 1. the snapshot_provenance column exists and defaults honestly ==")
    conn = storage.init_db(db)
    for table in ("score_history", "current_scores"):
        columns = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        check(f"{table} carries snapshot_provenance",
              "snapshot_provenance" in columns)
    check("migration is idempotent", storage.init_db(db) is not None)
    check("three provenance values are named",
          set(storage.SNAPSHOT_PROVENANCE) ==
          {storage.LIVE_CLOSE, storage.REPLAY, storage.RELABELLED},
          storage.SNAPSHOT_PROVENANCE)
    check("a relabelled row is distinguishable from a close",
          storage.RELABELLED != storage.LIVE_CLOSE)

    ids = storage.upsert_assets(conn, [
        _Asset("UST-10Y", None, "US 10 Year", "Bond", "US Treasury"),
        _Asset("TESTCO", "TESTCO", "Test Co", "Equity", "Stock",
               sector="Technology", industry="Software"),
    ])
    bond_id = ids[("UST-10Y", "Bond")]
    equity_id = ids[("TESTCO", "Equity")]

    # ------------------------------------------------------------------
    print("== 2. storage stamps, validates and never lets a fake displace a real ==")
    storage.save_score_snapshot(conn, equity_id, "2026-01-05", price=10.0,
                                shaffer_score=1.0)
    check("default provenance is live_close",
          _provenances(conn, "2026-01-05") == [storage.LIVE_CLOSE],
          _provenances(conn, "2026-01-05"))

    storage.save_score_snapshot(conn, equity_id, "2026-01-06", price=10.0,
                                shaffer_score=2.0,
                                provenance=storage.RELABELLED)
    check("an explicit relabel is stamped relabelled",
          _provenances(conn, "2026-01-06") == [storage.RELABELLED],
          _provenances(conn, "2026-01-06"))

    check("an unknown provenance is refused",
          _raises(lambda: storage.save_score_snapshot(
              conn, equity_id, "2026-01-07", provenance="probably_fine"),
              ValueError))
    check("the refusal names the allowed values",
          "live_close" in _message(lambda: storage.save_score_snapshot(
              conn, equity_id, "2026-01-07", provenance="probably_fine")))

    # The real close wins: an existing close is never rewritten, so a later
    # relabel attempt on the same date writes nothing at all.
    again = storage.save_score_snapshot(conn, equity_id, "2026-01-05",
                                        price=999.0, shaffer_score=99.0,
                                        provenance=storage.RELABELLED)
    check("a relabel cannot displace a stored close", again == "exists", again)
    check("the stored close keeps its provenance",
          _provenances(conn, "2026-01-05") == [storage.LIVE_CLOSE],
          _provenances(conn, "2026-01-05"))

    storage.save_current_score(conn, equity_id, price=10.0, shaffer_score=1.0,
                               snapshot_provenance=storage.RELABELLED)
    current = storage.get_current_score(conn, equity_id)
    check("current_scores records the kind of run that wrote it",
          current["snapshot_provenance"] == storage.RELABELLED,
          current["snapshot_provenance"])
    check("current_scores refuses an unknown provenance",
          _raises(lambda: storage.save_current_score(
              conn, equity_id, snapshot_provenance="nope"), ValueError))
    storage.save_current_score(conn, equity_id, price=10.0, shaffer_score=1.0)
    check("a normal refresh resets it to live_close",
          storage.get_current_score(conn, equity_id)["snapshot_provenance"]
          == storage.LIVE_CLOSE)

    # ------------------------------------------------------------------
    print("== 3. HAZARD A: the date fence itself ==")
    check("no date -> today, live_close",
          R.resolve_snapshot_date(None, False) == (today_s, storage.LIVE_CLOSE),
          R.resolve_snapshot_date(None, False))
    check("today's own date is allowed",
          R.resolve_snapshot_date(today_s, False) == (today_s, storage.LIVE_CLOSE))
    check("a PAST date is REFUSED without the override",
          _raises(lambda: R.resolve_snapshot_date(past, False),
                  R.SnapshotDateError))
    check("a FUTURE date is REFUSED without the override",
          _raises(lambda: R.resolve_snapshot_date(future, False),
                  R.SnapshotDateError))
    check("a FUTURE date is REFUSED EVEN WITH the override",
          _raises(lambda: R.resolve_snapshot_date(future, True),
                  R.SnapshotDateError))
    check("a malformed date is refused",
          _raises(lambda: R.resolve_snapshot_date("last Tuesday", True),
                  R.SnapshotDateError))
    check("the override yields the past date stamped relabelled",
          R.resolve_snapshot_date(past, True) == (past, storage.RELABELLED),
          R.resolve_snapshot_date(past, True))

    past_message = _message(lambda: R.resolve_snapshot_date(past, False))
    check("the refusal says it does NOT fetch historical data",
          "does NOT fetch historical data" in past_message, past_message[:120])
    check("the refusal names the override", "allow_relabel" in past_message)
    check("the refusal points at the point-in-time replay",
          "pit_score" in past_message or "pit_store" in past_message)
    future_message = _message(lambda: R.resolve_snapshot_date(future, True))
    check("the future refusal says there is no override",
          "no override" in future_message, future_message[:120])

    # ------------------------------------------------------------------
    print("== 4. HAZARD A: the fence is wired into both refresh entry points ==")
    # These must raise BEFORE any fetch, so they are safe to call offline and
    # must leave no refresh_runs row behind.
    check("refresh_daily_scores refuses a past date",
          _raises(lambda: R.refresh_daily_scores(conn, snapshot_date=past),
                  R.SnapshotDateError))
    check("refresh_daily_scores refuses a future date",
          _raises(lambda: R.refresh_daily_scores(conn, snapshot_date=future),
                  R.SnapshotDateError))
    check("refresh_daily_scores refuses a future date even with the override",
          _raises(lambda: R.refresh_daily_scores(conn, snapshot_date=future,
                                                 allow_relabel=True),
                  R.SnapshotDateError))
    runs = conn.execute("SELECT COUNT(*) AS n FROM refresh_runs").fetchone()["n"]
    check("a refused run costs nothing -- no refresh_runs row, no fetch",
          runs == 0, runs)

    check("refresh_macro_scores refuses a past date",
          _raises(lambda: R.refresh_macro_scores(
              conn, snapshot_date=past, macro=_fake_macro()),
              R.SnapshotDateError))
    check("refresh_macro_scores refuses a future date",
          _raises(lambda: R.refresh_macro_scores(
              conn, snapshot_date=future, macro=_fake_macro()),
              R.SnapshotDateError))

    # ------------------------------------------------------------------
    print("== 5. HAZARD A: what the wired path actually writes ==")
    normal = R.refresh_macro_scores(conn, macro=_fake_macro())
    check("a normal macro refresh scores the treasury", normal["scored"] == 1,
          normal)
    check("today's row is stamped live_close",
          _provenances(conn, today_s) == [storage.LIVE_CLOSE],
          _provenances(conn, today_s))

    explicit = R.refresh_macro_scores(conn, snapshot_date=today_s,
                                      macro=_fake_macro())
    check("passing today's date explicitly still works",
          explicit["scored"] == 1, explicit)

    relabelled = R.refresh_macro_scores(conn, snapshot_date=past,
                                        allow_relabel=True,
                                        macro=_fake_macro())
    check("the override lets the relabelled run through",
          relabelled["scored"] == 1, relabelled)
    check("and the row is stamped relabelled, permanently",
          _provenances(conn, past) == [storage.RELABELLED],
          _provenances(conn, past))
    check("today's genuine close is untouched by the relabel run",
          _provenances(conn, today_s) == [storage.LIVE_CLOSE],
          _provenances(conn, today_s))

    # ------------------------------------------------------------------
    print("== 6. HAZARD A: a relabelled row never reaches a training set ==")
    for day, forward in ((today_s, 0.11), (past, 0.22)):
        storage.save_outcome_label(conn, bond_id, day, "12M", base_price=100.0,
                                   future_date="2027-01-01", future_price=110.0,
                                   forward_return=forward)
    rows = storage.dataset_rows(conn, "12M")
    dates = sorted({r["snapshot_date"] for r in rows})
    check("dataset_rows returns the genuine close", today_s in dates, dates)
    check("dataset_rows EXCLUDES the relabelled row", past not in dates, dates)
    check("every returned row is a live close",
          all(r["snapshot_provenance"] == storage.LIVE_CLOSE for r in rows))
    labelled = conn.execute(
        "SELECT COUNT(*) AS n FROM outcome_labels").fetchone()["n"]
    check("the relabelled row still HAS a label -- it is excluded, not absent",
          labelled == 2, labelled)

    stats = storage.snapshot_stats(conn)
    check("snapshot_stats does not backdate history to the relabelled row",
          stats["earliest"] != past, stats)
    check("snapshot_stats counts the relabelled rows separately",
          stats["relabelled_excluded"] >= 1, stats)
    check("ml_lab.data_status inherits the same honest counts",
          ml_lab.data_status(conn)["relabelled_excluded"]
          == stats["relabelled_excluded"])

    # ------------------------------------------------------------------
    print("== 7. HAZARD A: daily_job.py refuses at the command line ==")
    job_db = os.path.join(db_dir, "job.db")
    refused = subprocess.run(
        [sys.executable, "daily_job.py", "--date", past, "--db", job_db],
        cwd=HERE, capture_output=True, text=True, timeout=180)
    check("--date in the past exits nonzero", refused.returncode == 2,
          refused.returncode)
    check("and explains itself on stderr", "REFUSED" in refused.stderr,
          refused.stderr[-200:])
    check("and says plainly that it does not fetch history",
          "does NOT fetch historical data" in refused.stderr)

    refused_future = subprocess.run(
        [sys.executable, "daily_job.py", "--date", future,
         "--allow-relabel", "--db", job_db],
        cwd=HERE, capture_output=True, text=True, timeout=180)
    check("--date in the future is refused even with --allow-relabel",
          refused_future.returncode == 2 and "REFUSED" in refused_future.stderr,
          refused_future.stderr[-200:])

    helped = subprocess.run(
        [sys.executable, "daily_job.py", "--help"],
        cwd=HERE, capture_output=True, text=True, timeout=180)
    # argparse re-wraps help text, so compare on collapsed whitespace.
    help_text = " ".join(helped.stdout.split())
    check("--allow-relabel is documented", "--allow-relabel" in help_text)
    check("--date help states it does NOT fetch historical data",
          "does NOT fetch historical data" in help_text,
          help_text[:160])
    check("--allow-relabel help says the row is stamped relabelled",
          "relabelled" in help_text)

    # ------------------------------------------------------------------
    print("== 8. HAZARD B: the live path still splits, exactly as before ==")
    dates = sorted([f"2026-{m:02d}-01" for m in range(1, 13)] * 5)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        splits = ml_lab.walk_forward_splits(dates, n_folds=3, min_train=5)
    check("the live-snapshot path still produces splits", len(splits) >= 2,
          len(splits))
    check("every train period precedes its test period",
          all(max(dates[i] for i in tr) < min(dates[i] for i in te)
              for tr, te in splits))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        explicit_live = ml_lab.walk_forward_splits(
            dates, n_folds=3, min_train=5,
            source=ml_lab.SOURCE_LIVE_SNAPSHOT)
    check("naming the live source explicitly changes nothing",
          explicit_live == splits)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        ml_lab.walk_forward_splits(dates, n_folds=3, min_train=5)
    check("it emits a DeprecationWarning",
          any(issubclass(w.category, DeprecationWarning) for w in caught),
          [str(w.category) for w in caught])
    check("the warning points at the replacement",
          any("purge" in str(w.message) for w in caught),
          [str(w.message)[:80] for w in caught])

    # ------------------------------------------------------------------
    print("== 9. HAZARD B: point-in-time data is refused outright ==")
    check("a PIT dataset is refused",
          _raises(lambda: ml_lab.walk_forward_splits(
              dates, source=ml_lab.SOURCE_POINT_IN_TIME),
              ml_lab.LeakageGuardError))
    pit_message = _message(lambda: ml_lab.walk_forward_splits(
        dates, source=ml_lab.SOURCE_POINT_IN_TIME))
    check("the refusal names the missing mechanism",
          "PURGE" in pit_message and "EMBARGO" in pit_message,
          pit_message[:140])
    check("the refusal points at the replacement",
          "purge_days" in pit_message and "ml_fold" in pit_message,
          pit_message[:140])
    check("a declared purge requirement is refused, never ignored",
          _raises(lambda: ml_lab.walk_forward_splits(dates, purge_days=252),
                  ml_lab.LeakageGuardError))
    check("a declared embargo requirement is refused, never ignored",
          _raises(lambda: ml_lab.walk_forward_splits(dates, embargo_days=90),
                  ml_lab.LeakageGuardError))
    check("an unrecognised source is refused rather than assumed live",
          _raises(lambda: ml_lab.walk_forward_splits(dates, source="pit"),
                  ml_lab.LeakageGuardError))
    check("the refusal is not a plain ValueError a caller might swallow",
          not issubclass(ml_lab.LeakageGuardError, ValueError))

    # ------------------------------------------------------------------
    print("== 10. HAZARD B: the refusal survives the whole evaluation path ==")
    live_set = ml_lab.Dataset(horizon="12M")
    check("a dataset is live-snapshot unless it says otherwise",
          live_set.source == ml_lab.SOURCE_LIVE_SNAPSHOT)

    pit_set = ml_lab.Dataset(horizon="12M", source=ml_lab.SOURCE_POINT_IN_TIME)
    pit_set.feature_names = ["shaffer_score"]
    pit_set.dates = [d for d in sorted({f"2020-{m:02d}-01" for m in range(1, 13)})
                     for _ in range(6)]
    pit_set.X = [[float(i)] for i in range(len(pit_set.dates))]
    pit_set.y = [0.01 * i for i in range(len(pit_set.dates))]
    pit_set.shaffer_scores = [float(i) for i in range(len(pit_set.dates))]
    check("evaluate_model refuses a PIT dataset rather than scoring it",
          _raises(lambda: ml_lab.evaluate_model(
              pit_set, ml_lab.ShafferBaseline(), "baseline", "baseline"),
              ml_lab.LeakageGuardError))

    live_equivalent = ml_lab.Dataset(horizon="12M")
    live_equivalent.feature_names = list(pit_set.feature_names)
    live_equivalent.dates = list(pit_set.dates)
    live_equivalent.X = list(pit_set.X)
    live_equivalent.y = list(pit_set.y)
    live_equivalent.shaffer_scores = list(pit_set.shaffer_scores)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        report = ml_lab.evaluate_model(live_equivalent, ml_lab.ShafferBaseline(),
                                       "baseline", "baseline")
    check("the identical data on the live path is still evaluated",
          len(report.folds) >= 1, report.note)

    built = ml_lab.build_dataset(conn, "12M")
    check("build_dataset stamps the live source",
          built.source == ml_lab.SOURCE_LIVE_SNAPSHOT, built.source)

    print()
    print("FAILURES:", len(fails), fails if fails else "")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
