"""Offline tests for the block-level coverage census.

Pure stdlib, no network:   python test_pit_coverage.py

Everything here runs against fixtures except two checks that open the real
store READ-ONLY to assert it was not written to. That is deliberate: the
module's headline promise is "READ-ONLY", and a promise a test cannot fail is
not a promise.

The classification and distress definitions are tested at their BOUNDARIES,
because every one of them is a judgement call the owner must be able to
disagree with precisely: SIC 67 is finance and 70 is services; a company with
no equity fact is distress-UNKNOWN and never distress-none; a revenue tag of
zero is not a missing revenue tag.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_coverage as V
import pit_policy
import pit_store

fails: list[str] = []


def check(name: str, cond: bool, extra: object = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {extra}")
        fails.append(name)


def _row(**fields):
    """A census row with sane defaults, so a test names only what it means."""
    base = {"entity_id": 1, "sic": "2834", "division": "D", "is_financial": False,
            "size_bucket": "Q3", "total_assets": 1e8,
            "net_income_sign": "net_income_positive",
            "ebitda_sign": "ebitda_positive",
            "operating_income_sign": "oi_positive",
            "distress": V.DISTRESS_NONE, "distress_flags": [],
            "n_distress_flags": 0, "revenue_state": V.REVENUE_NORMAL,
            "age_bucket": "10-20y", "shares_ok": True, "price_ok": True,
            "valuation_ok": True,
            "features": {key: True for key in V.FEATURE_KEYS}}
    base.update(fields)
    if "is_financial" not in fields:
        base["is_financial"] = base["division"] == V.DIVISION_FINANCE
    return base


def main() -> int:
    print("== 1. SIC divisions, at the boundaries ==")
    for sic, want in (("0100", "A"), ("0900", "A"), ("1000", "B"), ("1731", "C"),
                      ("2834", "D"), ("3999", "D"), ("4813", "E"), ("5065", "F"),
                      ("5812", "G"), ("6022", "H"), ("6798", "H"), ("7372", "I"),
                      ("8742", "I"), ("9995", "J")):
        check(f"SIC {sic} -> division {want}", V.sic_division(sic) == want,
              V.sic_division(sic))
    check("an unfiled SIC is '?', not the largest division",
          V.sic_division(None) == V.DIVISION_UNKNOWN
          and V.sic_division("") == V.DIVISION_UNKNOWN
          and V.sic_division("ABCD") == V.DIVISION_UNKNOWN)
    check("finance is 60-67 and nothing else",
          V.is_financial("6022") and V.is_financial("6770")
          and not V.is_financial("5812") and not V.is_financial("7372"))
    check("every division letter names itself",
          V.division_name("H").startswith("Finance")
          and V.division_name("?") == "Unclassified")

    print("== 2. distress is defined, and unknown is not healthy ==")
    none_known = V.distress_state(None, None, None)
    check("no inputs at all is UNKNOWN, never 'none'",
          none_known["state"] == V.DISTRESS_UNKNOWN
          and none_known["n_inputs_known"] == 0, none_known)
    check("negative equity alone is distress",
          V.distress_state(-1.0, None, None)["flags"]
          == (V.DISTRESS_NEGATIVE_EQUITY,))
    check("negative operating income alone is distress",
          V.distress_state(None, -1.0, None)["flags"]
          == (V.DISTRESS_NEGATIVE_OPERATING_INCOME,))
    check("interest above operating income is distress",
          V.DISTRESS_INTEREST_EXCEEDS_OPERATING_INCOME
          in V.distress_state(None, 10.0, 11.0)["flags"])
    check("...and needs BOTH inputs, so one alone cannot imply it",
          V.distress_state(None, None, 11.0)["state"] == V.DISTRESS_UNKNOWN,
          V.distress_state(None, None, 11.0))
    healthy = V.distress_state(5.0, 5.0, 1.0)
    check("a solvent, profitable, covered company is 'none' with 3 inputs",
          healthy["state"] == V.DISTRESS_NONE and healthy["n_inputs_known"] == 3)
    check("zero equity is not negative equity",
          V.distress_state(0.0, None, None)["state"] == V.DISTRESS_NONE)
    check("all three flags can fire at once",
          V.distress_state(-1.0, -1.0, 0.0)["n_flags"] == 3,
          V.distress_state(-1.0, -1.0, 0.0))

    print("== 3. revenue status and age ==")
    check("no tag, a tagged zero and a small number are three states",
          (V.revenue_state(None), V.revenue_state(0.0), V.revenue_state(5e5),
           V.revenue_state(5e6))
          == (V.REVENUE_NO_TAG, V.REVENUE_ZERO, V.REVENUE_NEAR_ZERO,
              V.REVENUE_NORMAL))
    check("a negative revenue is not 'normal'",
          V.revenue_state(-10.0) == V.REVENUE_ZERO)
    check("age buckets are contiguous and bounded",
          (V.age_bucket("2014-01-01", "2015-06-30"),
           V.age_bucket("2010-01-01", "2015-06-30"),
           V.age_bucket("1994-01-01", "2015-06-30"))
          == ("0-2y", "5-10y", "20y+"))
    check("an unknown or impossible first filing is 'unknown', not 0-2y",
          V.age_bucket(None, "2015-06-30") == "unknown"
          and V.age_bucket("2016-01-01", "2015-06-30") == "unknown"
          and V.age_bucket("not-a-date", "2015-06-30") == "unknown")

    print("== 4. size buckets: equal counts, and None is its own bucket ==")
    values = [float(v) for v in range(1, 101)]
    cuts = V.quantile_cuts(values, 5)
    labelled = [V.bucket_of(v, cuts, V.SIZE_LABELS) for v in values]
    counts = {label: labelled.count(label) for label in V.SIZE_LABELS}
    check("five buckets of twenty", set(counts.values()) == {20}, counts)
    check("the buckets are ordered by value",
          V.bucket_of(1.0, cuts, V.SIZE_LABELS) == "Q1_smallest"
          and V.bucket_of(100.0, cuts, V.SIZE_LABELS) == "Q5_largest")
    check("a missing size is 'unknown', not the smallest bucket",
          V.bucket_of(None, cuts, V.SIZE_LABELS) == "unknown")
    check("an empty sample yields no cuts rather than a crash",
          V.quantile_cuts([], 5) == [])
    check("a sign that is missing is a third state",
          V.sign_label(None, "p", "n") == "unknown"
          and V.sign_label(0.0, "p", "n") == "p"
          and V.sign_label(-1e-9, "p", "n") == "n")

    print("== 5. period matching: a year earlier means a year earlier ==")
    periods = ["2014-06-30", "2013-06-30", "2012-12-31", "2014-12-31"]
    check("one year back finds the matching fiscal period",
          V.nearest_period(periods, "2015-06-30", 1) == "2014-06-30")
    check("two years back finds the right one too",
          V.nearest_period(periods, "2015-06-30", 2) == "2013-06-30")
    check("a 52/53-week drift inside tolerance still matches",
          V.nearest_period(["2014-06-28"], "2015-06-30", 1) == "2014-06-28")
    check("outside tolerance it returns NOTHING rather than the nearest",
          V.nearest_period(["2014-03-31"], "2015-06-30", 1) is None)
    check("a changed year-end is a missing comparison, not a 9-month one",
          V.nearest_period(["2014-09-30", "2014-12-31"], "2015-06-30", 1) is None)

    print("== 6. concept resolution honours ladder order, units and staleness ==")
    ladders = pit_policy.ladder_set(pit_store.LADDER_VERSION_V2)
    index = {
        (1, "Assets", 0): {"2015-03-31": 1000.0, "2014-03-31": 900.0},
        (1, "Revenues", 4): {"2014-12-31": 500.0},
        (1, "SalesRevenueNet", 4): {"2014-12-31": 777.0},
        (2, "Assets", 0): {"2013-12-31": 50.0},
        (3, "LongTermDebtNoncurrent", 0): {"2015-03-31": 10.0},
        (3, "LongTermDebtCurrent", 0): {"2015-03-31": 2.0},
        (3, "ShortTermBorrowings", 0): {"2015-03-31": 3.0},
        (4, "LongTermDebtNoncurrent", 0): {"2015-03-31": 10.0},
        (4, "LongTermDebtCurrent", 0): {"2014-03-31": 2.0},
    }
    rung, period, value = V.resolve_concept(index, 1, "total_assets",
                                            "2015-06-30", ladders)
    check("the newest non-stale period wins",
          (rung, period, value) == ("Assets", "2015-03-31", 1000.0),
          (rung, period, value))
    check("a stale balance sheet resolves to NOTHING, not to an old number",
          V.resolve_concept(index, 2, "total_assets", "2015-06-30", ladders)
          == (None, None, None))
    check("the ladder's FIRST usable rung wins, not the biggest number",
          V.resolve_concept(index, 1, "revenue", "2015-06-30", ladders)[2] == 500.0)
    check("a composite rung sums every component",
          V.resolve_concept(index, 3, "total_debt", "2015-06-30", ladders)[2] == 15.0)
    check("...and refuses when the components do not share a period",
          V.resolve_concept(index, 4, "total_debt", "2015-06-30",
                            ladders)[0] != "SUM(LongTermDebtNoncurrent+"
                                            "LongTermDebtCurrent+ShortTermBorrowings)")
    check("every census tag carries the unit its ladder declares",
          V.tag_units()["Assets"] == "USD"
          and V.tag_units()["CommonStockSharesOutstanding"] == "shares")
    # A LEVEL merges the ladder per period; a GROWTH RATE must not.
    rungs = ["Revenues", "SalesRevenueNet"]
    switched = {(9, "Revenues", 4): {"2014-12-31": 500.0},
                (9, "SalesRevenueNet", 4): {"2013-12-31": 450.0}}
    check("per-period resolution finds a revenue in BOTH years",
          V.concept_periods(switched, 9, rungs, 4)
          == {"2014-12-31": 500.0, "2013-12-31": 450.0})
    check("...while same-rung resolution refuses to difference two tags",
          V.rung_periods(switched, 9, "Revenues", 4) == {"2014-12-31": 500.0})
    check("a composite rung is never differenced into a growth rate",
          V.rung_periods(switched, 9, "SUM(A+B)", 4) == {}
          and V.rung_periods(switched, 9, None, 4) == {})
    check("the first rung still wins where both tags cover a period",
          V.concept_periods({(9, "Revenues", 4): {"2014-12-31": 500.0},
                             (9, "SalesRevenueNet", 4): {"2014-12-31": 777.0}},
                            9, rungs, 4) == {"2014-12-31": 500.0})

    print("== 7. EBITDA assembles only from a shared period ==")
    ebitda_index = {
        (1, "OperatingIncomeLoss", 4): {"2014-12-31": 100.0, "2013-12-31": 80.0},
        (1, "DepreciationDepletionAndAmortization", 4): {"2014-12-31": 20.0},
        (1, "DepreciationAndAmortization", 4): {"2013-12-31": 15.0},
        (2, "OperatingIncomeLoss", 4): {"2014-12-31": 100.0},
        (2, "DepreciationDepletionAndAmortization", 4): {"2013-12-31": 20.0},
    }
    assembled = V._ebitda_periods(ebitda_index, 1, ladders)
    check("both components at the same period end assemble",
          assembled["2014-12-31"] == 120.0, assembled)
    check("a later D&A rung still assembles its own year",
          assembled["2013-12-31"] == 95.0, assembled)
    check("components in DIFFERENT years never assemble",
          V._ebitda_periods(ebitda_index, 2, ladders) == {})
    check("no operating income means no EBITDA, with no substitute",
          V._ebitda_periods({}, 1, ladders) == {})

    print("== 8. tabulation reports rates WITH their denominators ==")
    rows = ([_row(division="D", features={**{k: True for k in V.FEATURE_KEYS}})
             for _ in range(40)]
            + [_row(division="H", features={**{k: True for k in V.FEATURE_KEYS},
                                            "ebitda_level": False})
               for _ in range(40)]
            + [_row(division="B", features={**{k: True for k in V.FEATURE_KEYS},
                                            "ebitda_level": False})
               for _ in range(4)])
    table = V.tabulate(rows, "division", "ebitda_level")
    check("each group carries n as well as a percentage",
          table["groups"]["D"] == {"n_available": 40, "n": 40, "pct": 100.0},
          table["groups"]["D"])
    check("the overall rate is over the WHOLE denominator",
          abs(table["overall_pct"] - 100.0 * 40 / 84) < 1e-9, table["overall_pct"])
    check("the spread ignores groups below MIN_GROUP_N",
          abs(table["spread_pct"] - 100.0) < 1e-9, table["spread_pct"])
    check("a tiny group is still REPORTED, just not compared",
          table["groups"]["B"]["n"] == 4)
    summary = V.missingness_summary(rows, ["ebitda_level"], ["division"])
    check("the missingness summary carries the widest spread per feature",
          abs(summary["ebitda_level"]["widest_spread_pct"] - 100.0) < 1e-9)

    print("== 9. the headline states its denominator ==")
    head = V.headline(rows)
    check("financials are excluded from the operating denominator",
          head["n_operating"] == 44 and head["n_financial_excluded"] == 40,
          (head["n_operating"], head["n_financial_excluded"]))
    check("the denominator is written down, not implied",
          "SIC 60-67" in head["denominator"])
    check("the full score is counted over the OPERATING denominator only",
          abs(head["pct_full_score_now"] - 100.0 * 40 / 44) < 1e-9,
          head["pct_full_score_now"])
    no_shares = [_row(shares_ok=False, valuation_ok=False) for _ in range(10)]
    head2 = V.headline(no_shares)
    check("without a share count the full score is impossible...",
          head2["pct_full_score_now"] == 0.0)
    check("...and closing the share-count gap is what the projection changes",
          head2["pct_full_score_shares_closed"] == 100.0
          and head2["pct_three_blocks_EGQ"] == 100.0)
    check("a blank cheque is excluded from the operating universe too",
          V.headline([_row(division=V.SIC_BLANK_CHECK)])["n_operating"] == 0)

    print("== 10. every return-linked row is labelled SURVIVOR_ONLY ==")
    linked = V.availability_vs_returns(
        [_row(entity_id=1), _row(entity_id=2,
                                 features={**{k: True for k in V.FEATURE_KEYS},
                                           "ebitda_level": False})],
        {1: 0.25, 2: -0.10})
    check("the report is labelled at the top level",
          linked["sample_scope"] == pit_store.SAMPLE_SURVIVOR_ONLY
          and linked["may_promote_a_model"] is False)
    check("...and on EVERY per-feature row, where a reader will actually look",
          all(row["sample_scope"] == pit_store.SAMPLE_SURVIVOR_ONLY
              for row in linked["features"].values()))
    check("the measured gap is reported with both group sizes",
          linked["features"]["ebitda_level"]["n_available"] == 1
          and linked["features"]["ebitda_level"]["n_unavailable"] == 1
          and abs(linked["features"]["ebitda_level"]["mean_gap"] - 0.35) < 1e-12)
    check("effective N deflates a correlated cross-section, hard",
          abs(V.effective_n(2100) - 13.4) < 0.1
          and V.effective_n(100, icc=0.0) == 100.0, V.effective_n(2100))
    check("effective N is never larger than the nominal count",
          all(V.effective_n(n) <= n for n in (1, 2, 10, 1000)))

    print("== 11. the census agrees with the modules it mirrors ==")
    import pit_peers
    check("the peer rung ladder and cohort floor match pit_peers",
          V.RUNG_LADDER == tuple(pit_peers.RUNG_LADDER)
          and V.MIN_COHORT_N == pit_peers.MIN_COHORT_N,
          (V.RUNG_LADDER, pit_peers.RUNG_LADDER))
    check("every feature declares a block and a note",
          set(V.FEATURE_KEYS) == set(V.FEATURE_BLOCK)
          and set(V.FEATURE_KEYS) == set(V.FEATURE_NOTE))
    check("the three measured dates are the project's three",
          V.MEASURED_DATES == ("2015-06-30", "2019-06-28", "2024-06-28"))
    check("the survivor label comes from pit_store, never redeclared",
          V.SURVIVOR_ONLY is pit_store.SAMPLE_SURVIVOR_ONLY)

    print("== 12. READ-ONLY is enforced, not promised ==")
    handle, path = tempfile.mkstemp(suffix=".db")
    os.close(handle)
    try:
        seed = sqlite3.connect(path)
        seed.execute("CREATE TABLE t (x INTEGER)")
        seed.commit()
        seed.close()
        conn = V.open_read_only(path)
        wrote = True
        try:
            conn.execute("INSERT INTO t VALUES (1)")
            conn.commit()
        except sqlite3.Error:
            wrote = False
        conn.close()
        check("a census connection cannot INSERT", not wrote)
    finally:
        os.unlink(path)

    store = pit_store.DEFAULT_PIT_DB_PATH
    if os.path.exists(store):
        conn = V.open_read_only(store)
        try:
            empty = {name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                     for name in ("pit_feature", "pit_score", "pit_replay_run")}
        finally:
            conn.close()
        check("the replay tables are STILL empty after the census ran",
              empty == {"pit_feature": 0, "pit_score": 0, "pit_replay_run": 0},
              empty)
    else:
        check("the real store is absent, so its emptiness is UNKNOWN", True,
              "skipped deliberately -- an absent store is not a passing one")

    print()
    print("FAILURES:", len(fails), fails if fails else "")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
