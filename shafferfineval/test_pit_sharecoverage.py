"""Tests for pit_sharecoverage. Plain script: check(), main(), PASS/FAIL, exit.

Two halves, on purpose:

  SYNTHETIC   the point-in-time selector and the title classifier, on fixtures
              built here, so a logic regression fails even with no store.
  LIVE        assertions against the real 8.13 GiB store, READ-ONLY, that pin
              the numbers this study reported.

The live half exists because the study's whole claim is about THIS store. A
test that only exercised fixtures would pass happily on the day the archive
changed underneath the conclusions.

COST, because a test nobody runs is not a test. `pit_fact` has no index on
`tag`, so every assertion about a tag costs a full covering-index scan --
measured at 34s each, and proving a tag's ABSENCE cannot be cheaper, because
absence is only provable by looking everywhere. Those five checks are behind
`--full` (about 200s); the default run is metadata plus `pit_symbol_obs` and
finishes in seconds. CI should run `--full`.
"""

from __future__ import annotations

import os
import sqlite3
import sys

import pit_sharecoverage as psc

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                  "shafferfineval_pit.db")

_PASS = 0
_FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print("PASS  %s" % name)
    else:
        _FAIL += 1
        print("FAIL  %s%s" % (name, ("  -- " + detail) if detail else ""))


# --------------------------------------------------------------------------
# SYNTHETIC: the selector
# --------------------------------------------------------------------------

def _row(period_end, available_date, filed, val, qtrs=0):
    # (period_end, available_date, filed, form, qtrs, val, unit, accn)
    return (period_end, available_date, filed, "10-Q", qtrs, val, "shares",
            "acc-%s-%s" % (period_end, filed))


def test_selector() -> None:
    apple = [
        _row("2019-09-28", "2019-11-01", "2019-10-31", 4_443_236_000),
        _row("2019-09-28", "2020-01-30", "2020-01-29", 4_443_236_000),
        _row("2019-09-28", "2020-10-31", "2020-10-30", 17_772_945_000),
    ]
    sel = psc.select_share_pit(apple, "2020-01-31")
    check("split restatement is invisible before it is filed",
          sel is not None and sel[5] == 4_443_236_000,
          "got %r" % (sel and sel[5],))
    sel = psc.select_share_pit(apple, "2020-12-31")
    check("restated count wins once it is available",
          sel is not None and sel[5] == 17_772_945_000,
          "got %r" % (sel and sel[5],))
    check("nothing is readable before its availability date",
          psc.select_share_pit(apple, "2019-10-31") is None)
    future_period = [_row("2025-12-31", "2020-01-01", "2020-01-01", 1.0)]
    check("a period that has not ended is refused even if published",
          psc.select_share_pit(future_period, "2020-06-30") is None)
    newer = [
        _row("2020-03-31", "2020-05-01", "2020-04-30", 200.0),
        _row("2019-12-31", "2020-05-02", "2020-05-01", 100.0),
    ]
    check("the newest PERIOD wins, not the newest filing",
          psc.select_share_pit(newer, "2020-06-30")[5] == 200.0)
    check("empty input is None, not an exception",
          psc.select_share_pit([], "2020-01-01") is None)
    check("a genuine zero is selected, not skipped",
          psc.select_share_pit([_row("2013-03-31", "2013-05-01", "2013-04-30", 0.0)],
                               "2013-06-30")[5] == 0.0)


# --------------------------------------------------------------------------
# SYNTHETIC: the title classifier
# --------------------------------------------------------------------------

def test_classifier() -> None:
    cases = {
        "Common Stock": "unclassed",
        "Common Stock, par value $0.01 per share": "unclassed",
        "Class A common stock, par value $0.0001 per share": "class_A",
        "Class B Common Stock": "class_B",
        "Class A ordinary shares, par value $0.0001 per share": "class_A",
        "Common Shares of Beneficial Interest": "unclassed",
        "Warrants": None,
        "Warrants to purchase Common Stock": None,
        "Units, each consisting of one share of Class A common stock": None,
        "7.25% Series B Cumulative Preferred Stock": None,
        "Depositary Shares, each representing 1/1000th": None,
        "4.500% Senior Notes due 2029": None,
        "Rights to purchase Series A Junior Participating Preferred": None,
        None: None,
        "": None,
    }
    bad = [(t, psc.classify_security_title(t), want)
           for t, want in cases.items() if psc.classify_security_title(t) != want]
    check("security title classifier: %d cases" % len(cases), not bad,
          "mismatches: %r" % (bad[:4],))
    check("a warrant on common stock is not common stock",
          psc.classify_security_title("Warrants to purchase Common Stock") is None)


# --------------------------------------------------------------------------
# SYNTHETIC: the candidate verdicts and the cost model
# --------------------------------------------------------------------------

def test_candidates() -> None:
    keys = {c.key for c in psc.SHARE_CANDIDATES}
    check("six candidates registered", len(psc.SHARE_CANDIDATES) == 6,
          repr(sorted(keys)))
    correct = {c.key for c in psc.SHARE_CANDIDATES if c.economically_correct}
    check("the two direct counts plus the issued-minus-treasury identity",
          correct == {"dei_cover_outstanding", "gaap_balance_sheet_outstanding",
                      "gaap_issued_minus_treasury"},
          repr(sorted(correct)))
    ident = psc.candidate_for("gaap_issued_minus_treasury")
    check("the identity rung is correct, absent, and names its tag change",
          ident.economically_correct and ident.rows_in_store == 0
          and "TreasuryStockCommonShares" in ident.distortions)
    wad = psc.candidate_for("gaap_weighted_average_diluted")
    check("weighted-average diluted is never point-in-time",
          not wad.point_in_time and not wad.reconstructs_market_cap)
    flt = psc.candidate_for("dei_public_float")
    check("public float is point-in-time AND still refused",
          flt.point_in_time and not flt.economically_correct)
    issued = psc.candidate_for("gaap_balance_sheet_issued")
    check("issued is registered as absent from the store",
          issued.rows_in_store == 0 and not issued.economically_correct)
    try:
        psc.candidate_for("shares_outstanding_today")
        check("unknown candidate raises", False)
    except ValueError:
        check("unknown candidate raises", True)
    spec = psc.candidate_spec()
    check("staleness budget for a share count is 4 months, not 6",
          spec["max_age_months"]["quarterly"] == 4,
          repr(spec["max_age_months"]))


def test_cost_model() -> None:
    cost = psc.fetch_cost(6815)
    check("cost scales from a measured per-request mean",
          cost["download_bytes_streamed"] == 6815 * 148230)
    check("peak temporary space is ONE payload, not the whole fetch",
          cost["peak_temporary_bytes"] == 5164868
          and cost["peak_temporary_bytes"] < cost["download_bytes_streamed"])
    check("retained bytes are far smaller than the download",
          0 < cost["retained_bytes"] < cost["download_bytes_streamed"])
    free = psc.MEASURED["volume_free_bytes"]
    check("the whole fetch retains under 5%% of free space",
          cost["retained_bytes"] < 0.05 * free,
          "%d vs %d" % (cost["retained_bytes"], free))
    check("VACUUM is declared impossible, with its arithmetic",
          cost["vacuum_possible"] is False and "8.35" in cost["vacuum_note"])
    check("an unmeasured WAL bound under contention is None, never 0",
          psc.MEASURED["wal_peak_bytes_under_concurrent_reader"] is None)
    check("10 req/s ceiling is honoured in the schedule",
          cost["seconds_at_10_per_second"] == 681.5)


# --------------------------------------------------------------------------
# LIVE: the store this study is about
# --------------------------------------------------------------------------

def test_live(full: bool = False) -> None:
    if not os.path.exists(DB):
        check("live store present", False, DB)
        return
    conn = psc.connect_readonly(DB)
    try:
        try:
            conn.execute("CREATE TABLE _probe_should_fail (x)")
            check("read-only handle refuses writes", False, "a write succeeded")
        except sqlite3.OperationalError:
            check("read-only handle refuses writes", True)

        for table in ("pit_feature", "pit_score", "pit_replay_run"):
            n = conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
            check("%s is still empty (no replay was run)" % table, n == 0,
                  "found %d rows" % n)

        if full:
            n_tags = conn.execute(
                "SELECT COUNT(*) FROM (SELECT DISTINCT tag FROM pit_fact)").fetchone()[0]
            check("pit_fact is a 37-tag curated ingest, not an XBRL mirror",
                  n_tags == 37, "found %d" % n_tags)

            for tag in (psc.TAG_GAAP_ISSUED, "Security12bTitle",
                        "CommonStockSharesAuthorized"):
                n = conn.execute(
                    "SELECT COUNT(*) FROM pit_fact WHERE tag = ?", (tag,)).fetchone()[0]
                check("%s was never ingested" % tag, n == 0, "found %d" % n)

            rows = conn.execute(
                "SELECT COUNT(*) FROM pit_fact WHERE tag = ?",
                (psc.TAG_COVER,)).fetchone()[0]
            check("the designed lead rung has 606 rows in 14.07M", rows == 606,
                  "found %d" % rows)
        else:
            print("SKIP  5 pit_fact tag scans (~200s) -- pass --full to run them")

        for as_of, expect in zip(psc.STUDY_DATES, (7950, 7102, 7311)):
            n = len(psc.peer_universe(conn, as_of))
            check("peer universe at %s is %d" % (as_of, expect), n == expect,
                  "found %d" % n)

        # The era effect, which is the study's sharpest single number.
        pre = conn.execute(
            """SELECT COUNT(*) FROM pit_symbol_obs
                WHERE filed < '2019-01-01'
                  AND security_title IS NOT NULL AND security_title <> ''""").fetchone()[0]
        check("dei:Security12bTitle has ZERO pre-2019 rows", pre == 0,
              "found %d" % pre)
        post = conn.execute(
            """SELECT COUNT(*) FROM pit_symbol_obs
                WHERE filed >= '2020-01-01'
                  AND security_title IS NOT NULL AND security_title <> ''""").fetchone()[0]
        check("and it is near-universal from 2020", post > 500000, "found %d" % post)

        blind = psc.multiclass_at(conn, "2015-06-30")
        check("2015 multi-class detection is structurally blind",
              blind["blind"] and blind["proved_multi_class"] == 0,
              repr(blind))
        seeing = psc.multiclass_at(conn, "2024-06-28")
        check("2024 multi-class detection works and finds issuers",
              not seeing["blind"] and seeing["proved_multi_class"] > 300,
              repr(seeing))
        check("a blind era reports None for the rate, not 0%%",
              blind["multi_class_pct_of_detectable"] is None)

        # coverage_at is the module's main API. Exercised on a small slice --
        # it is one indexed query per entity, so the whole universe is 157s and
        # a smoke test does not need it.
        slice_ids = sorted(psc.peer_universe(conn, "2024-06-28"))[:120]
        cov = psc.coverage_at(conn, "2024-06-28", entity_ids=slice_ids)
        check("coverage_at runs and counts the slice it was given",
              cov["n_peers"] == 120 and cov["counts"]["gaap_fresh"] > 0,
              repr(cov["counts"]))
        check("the ladder never resolves fewer than its best rung",
              cov["counts"]["ladder_fresh"] >= cov["counts"]["gaap_fresh"]
              and cov["counts"]["ladder_any"] >= cov["counts"]["ladder_fresh"])
        check("age at use respects the 4-month staleness budget",
              cov["age_at_use_days"]["max"] is not None
              and cov["age_at_use_days"]["max"] <= 124,
              repr(cov["age_at_use_days"]))
        # The four outcome buckets must PARTITION the universe exactly: a count,
        # only a period average, only a public float, or nothing. An entity that
        # filed only EntityPublicFloat is the case that breaks a three-bucket
        # version of this check, which is why it has its own bucket.
        check("a period average alone is never counted as a count",
              cov["counts"]["only_period_average"] > 0)
        check("the outcome buckets partition the universe exactly",
              cov["counts"]["ladder_any"] + cov["counts"]["only_period_average"]
              + cov["counts"]["only_public_float"]
              + cov["counts"]["no_share_fact_at_all"] == cov["n_peers"],
              repr(cov["counts"]))

        scope = psc.fetch_scope(conn, include_fact_scan=full)
        check("fetch scope is a count of entities",
              scope["peer_cohort_entities"] == 16148
              and scope["pit_entity_total"] == 16890, repr(scope))
        check("an unscanned scope field is None, never 0",
              (scope["entities_with_a_gaap_count_anywhere"] == 12689) if full
              else (scope["entities_with_a_gaap_count_anywhere"] is None),
              repr(scope))
    finally:
        conn.close()


def main() -> int:
    full = "--full" in sys.argv
    print("=" * 70)
    print("pit_sharecoverage%s" % ("  [--full]" if full else ""))
    print("=" * 70)
    test_selector()
    test_classifier()
    test_candidates()
    test_cost_model()
    test_live(full=full)
    print("-" * 70)
    print("%d checks: %d passed, %d failed" % (_PASS + _FAIL, _PASS, _FAIL))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
