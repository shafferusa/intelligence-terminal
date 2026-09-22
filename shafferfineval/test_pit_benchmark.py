"""Offline tests for benchmark instruments and the excess returns they enable.

Pure stdlib, no network, a temporary store:   python test_pit_benchmark.py

Yahoo is stubbed at `pit_prices.fetch_chart`, so the gate is exercised against
payloads that are shaped exactly like the real ones, including the four ways a
benchmark ingest can silently ingest the wrong series: a coarsened granularity,
a payload whose own symbol is not the one requested, an operating company
wearing the ticker, and an instrument whose history starts halfway through the
label window.

THE ACCEPTANCE TESTS are sections 3 and 4.

  Section 3 is the arithmetic: the benchmark's forward return must come from
  the SAME core that produced the company labels, and excess must be gross
  minus benchmark to the last bit. A benchmark computed by a second
  implementation would differ from the names measured against it in a way that
  looks exactly like alpha.

  Section 4 is the honesty: every NULL that should stay NULL does. A censored
  benchmark leaves `excess_return` NULL even where the company resolved -- the
  bankruptcy-in-month-three case -- and `benchmark_listing_id` is stamped
  anyway, because a row that names its benchmark and reports NULL is readable
  and one that does neither is not.

The fixture also proves the containment that makes it safe to keep benchmark
bars in `pit_price_bar`: the benchmark must not appear in `listings_with_bars`,
must not be labelled, and must not be counted in the survivorship report.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_benchmark as B
import pit_labels as L
import pit_labelset as S
import pit_path
import pit_prices
import pit_store

fails: list[str] = []


def check(name: str, cond: bool, extra: object = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {extra}")
        fails.append(name)


def _raises(thunk, exc=Exception) -> bool:
    try:
        thunk()
    except exc:
        return True
    except Exception:
        return False
    return False


# --------------------------------------------------------------------------
# Fixture: a calendar, two companies, and a stubbed vendor
# --------------------------------------------------------------------------

#: 520 consecutive "sessions" -- enough for a 252-session horizon from the
#: first as-of date and not enough for one from the last, so the fixture holds
#: both a finished and an unfinished benchmark window.
def _sessions(n: int = 520) -> list[str]:
    import datetime as dt
    day = dt.date(2019, 1, 2)
    out: list[str] = []
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += dt.timedelta(days=1)
    return out


SESSIONS = _sessions()
EPOCH_BASE = 1_546_400_000


def _chart(symbol: str, *, n: int = 520, start_at: int = 0, step: float = 1.0,
           instrument_type: str = "ETF", currency: str = "USD",
           granularity: str = "1d", payload_symbol: str | None = None) -> dict:
    """A Yahoo chart payload in the shape `pit_prices.fetch_chart` returns."""
    import datetime as dt
    days = SESSIONS[start_at:start_at + n]
    stamps = [int(dt.datetime.fromisoformat(d + "T00:00:00+00:00").timestamp()) + 50_400
              for d in days]
    prices = [100.0 * (step ** i) for i in range(len(days))]
    return {
        "status": 200,
        "meta": {
            "symbol": payload_symbol or symbol,
            "instrumentType": instrument_type,
            "currency": currency,
            "firstTradeDate": stamps[0] if stamps else None,
            "gmtoffset": -14_400,
            "fullExchangeName": "NYSEArca",
            "longName": f"{symbol} Test Instrument",
        },
        "granularity": granularity,
        "timestamps": stamps,
        "quote": {"open": prices, "high": prices, "low": prices,
                  "close": prices, "volume": [1_000_000.0] * len(prices)},
        "adjclose": prices,
        "events": {},
        "error": None,
    }


CHARTS: dict[str, dict] = {}


def _stub_fetch(symbol: str, start: str = pit_prices.PRICE_START,
                end: str | None = None) -> dict:
    return CHARTS.get(symbol.upper(),
                      {"status": 404, "meta": None, "granularity": None,
                       "timestamps": [], "quote": {}, "adjclose": [],
                       "events": {}, "error": "Not Found"})


#: Company bars: one that compounds faster than the benchmark, one slower.
COMPANY_STEP = {"FAST": 1.0010, "SLOW": 1.0002}
BENCH_STEP = 1.0005


def fixture() -> tuple[sqlite3.Connection, str, str, dict[str, int]]:
    directory = tempfile.mkdtemp(prefix="pit_benchmark_")
    path = os.path.join(directory, "bench.db")
    conn = pit_store.init_db(path)
    conn.execute("PRAGMA foreign_keys = OFF")
    pit_store.insert_calendar(conn, L.MARKET, SESSIONS, "fixture")

    listings: dict[str, int] = {}
    ingest_id = pit_store.start_ingest(conn, "fixture", "companies")
    for symbol, step in COMPANY_STEP.items():
        listing_id = pit_store.upsert_listing(
            conn, symbol, SESSIONS[0], entity_id=None, exchange="NYSE",
            instrument_type="EQUITY", currency="USD", valid_from=SESSIONS[0],
            confidence="proved", source="fixture")
        listings[symbol] = listing_id
        chart = _chart(symbol, step=step, instrument_type="EQUITY")
        bars, _stats = pit_prices.bars_from_chart(chart, listing_id, symbol,
                                                  ingest_id, SESSIONS[0])
        pit_prices.write_bars(conn, bars, [])
    pit_store.finish_ingest(conn, ingest_id)

    CHARTS.clear()
    CHARTS["VTI"] = _chart("VTI", step=BENCH_STEP)
    CHARTS["SPY"] = _chart("SPY", step=BENCH_STEP * 1.00002)
    return conn, path, directory, listings


def cleanup(conn: sqlite3.Connection, directory: str) -> None:
    conn.close()
    shutil.rmtree(directory, ignore_errors=True)


# --------------------------------------------------------------------------
# 1. The gate
# --------------------------------------------------------------------------

def test_gate() -> None:
    print("\n1. the benchmark gate refuses the four ways this goes wrong")
    conn, path, directory, listings = fixture()
    B.ensure_schema(conn)
    spec = dict(B.BENCHMARK_SPECS[0])
    spec["must_cover"] = SESSIONS[0]

    check("a clean ETF payload is accepted",
          B.gate_benchmark_chart(conn, spec, CHARTS["VTI"])["accepted"])

    coarse = _chart("VTI", granularity="1mo")
    check("a coarsened series is refused outright",
          B.gate_benchmark_chart(conn, spec, coarse)["reason"] == B.REJECT_NOT_DAILY)

    redirected = _chart("VTI", payload_symbol="VTIAX")
    check("a payload whose own symbol differs is refused -- the ticker-reuse check",
          B.gate_benchmark_chart(conn, spec, redirected)["reason"]
          == B.REJECT_SYMBOL_MISMATCH)

    company = _chart("VTI", instrument_type="EQUITY")
    check("an operating company wearing the ticker is refused",
          B.gate_benchmark_chart(conn, spec, company)["reason"]
          == B.REJECT_INSTRUMENT_TYPE)

    foreign = _chart("VTI", currency="CAD")
    check("a series in another numeraire is refused",
          B.gate_benchmark_chart(conn, spec, foreign)["reason"] == B.REJECT_CURRENCY)

    late = _chart("VTI", start_at=200)
    check("an instrument that starts halfway through the window is refused, "
          "not used for the half it covers",
          B.gate_benchmark_chart(conn, spec, late)["reason"]
          == B.REJECT_FIRST_TRADE_TOO_LATE)

    issuer_spec = dict(spec)
    issuer_spec["symbol"] = "FAST"
    conn.execute("UPDATE pit_listing SET entity_id = 1 WHERE symbol = 'FAST'")
    conn.commit()
    check("a symbol an issuer already claims is refused",
          B.gate_benchmark_chart(conn, issuer_spec, _chart("FAST"))["reason"]
          == B.REJECT_CLAIMED_BY_ISSUER)
    conn.execute("UPDATE pit_listing SET entity_id = NULL WHERE symbol = 'FAST'")
    conn.commit()
    cleanup(conn, directory)


# --------------------------------------------------------------------------
# 2. Seeding, and the containment that makes it safe
# --------------------------------------------------------------------------

def test_seed_and_containment() -> None:
    print("\n2. a benchmark is a listing, and stays out of 'the companies'")
    conn, path, directory, listings = fixture()
    B.ensure_schema(conn)
    original = pit_prices.fetch_chart
    pit_prices.fetch_chart = _stub_fetch
    try:
        specs = [dict(s, must_cover=SESSIONS[0]) for s in B.BENCHMARK_SPECS]
        results = B.seed_all(conn, specs, db_path=path, start=SESSIONS[0],
                             min_free_gib=0.0)
        check("both declared benchmarks seed",
              [r["status"] for r in results] == ["seeded", "seeded"],
              [r.get("status") for r in results])
        check("and each brought its bars",
              all(r["n_bars"] == len(SESSIONS) for r in results),
              [r.get("n_bars") for r in results])
        check("seeding is idempotent",
              [r["status"] for r in B.seed_all(conn, specs, db_path=path,
                                               start=SESSIONS[0], min_free_gib=0.0)]
              == ["already_seeded", "already_seeded"])

        row = conn.execute(
            "SELECT * FROM pit_listing WHERE symbol = 'VTI'").fetchone()
        check("the benchmark listing has NO entity -- it is not an issuer",
              row["entity_id"] is None)
        check("and is marked as a benchmark, not a company",
              row["instrument_kind"] == pit_store.INSTRUMENT_BENCHMARK)

        registry = conn.execute(
            "SELECT * FROM pit_benchmark WHERE symbol = 'VTI'").fetchone()
        check("the registry records the benchmark KIND explicitly",
              registry["benchmark_kind"] == pit_store.BENCHMARK_BROAD_EQUITY)
        check("and the return basis, which is what makes it a total return",
              registry["return_basis"] == "adjclose")
        check("and says why this instrument was chosen over the alternative",
              "SPY" in registry["selection_note"])
        check("and carries the survivorship trap in the row itself",
              "UPWARD" in registry["survivorship_note"])
        check("VTI is the default for its kind and SPY is not",
              B.default_benchmark(conn)["symbol"] == "VTI"
              and int(conn.execute(
                  "SELECT is_default_for_kind FROM pit_benchmark WHERE symbol='SPY'"
              ).fetchone()[0]) == 0)

        # Containment: the whole reason benchmark bars can live in pit_price_bar.
        labelled = {lid for lid, _e in L.listings_with_bars(conn)}
        check("the benchmark is NOT in the set of listings to label",
              B.benchmark_listings(conn).isdisjoint(labelled),
              (B.benchmark_listings(conn), labelled))
        check("and the companies still are",
              set(listings.values()) <= labelled)
        check("the survivorship report counts companies only",
              L.survivorship_report(conn)["listings_with_bars"] == len(listings))
    finally:
        pit_prices.fetch_chart = original
    cleanup(conn, directory)


# --------------------------------------------------------------------------
# 3. THE ACCEPTANCE TEST: the arithmetic
# --------------------------------------------------------------------------

def _seeded() -> tuple[sqlite3.Connection, str, str, dict[str, int], int]:
    conn, path, directory, listings = fixture()
    B.ensure_schema(conn)
    L.ensure_schema(conn)          # pit_path, which build_labels also writes
    S.register_label_set(conn)
    S.seed_cost_assumptions(conn)
    original = pit_prices.fetch_chart
    pit_prices.fetch_chart = _stub_fetch
    try:
        specs = [dict(s, must_cover=SESSIONS[0]) for s in B.BENCHMARK_SPECS]
        B.seed_all(conn, specs, db_path=path, start=SESSIONS[0], min_free_gib=0.0)
    finally:
        pit_prices.fetch_chart = original
    grid = [SESSIONS[i] for i in range(21, 400, 42)]
    run = S.start_label_run(conn, S.RUN_BUILD, code_module="fixture")
    L.build_labels(conn, db_path=path, as_of_dates=grid,
                   min_free_gib=0.0, path_stride=99, label_run_id=run,
                   progress=False)
    return conn, path, directory, listings, run


def test_arithmetic() -> None:
    print("\n3. the benchmark is measured by the same core, to the last bit")
    conn, path, directory, listings, run = _seeded()
    bench_id = B.benchmark_listing_id(conn, "us_total_market")

    check("the benchmark itself received no labels",
          conn.execute("SELECT COUNT(*) FROM pit_label WHERE listing_id = ?",
                       (bench_id,)).fetchone()[0] == 0)

    returns = B.benchmark_forward_returns(conn, bench_id)
    check("the benchmark has a return for every (as_of, horizon) a label uses",
          set(returns) == {(r[0], r[1]) for r in conn.execute(
              "SELECT DISTINCT as_of_date, horizon FROM pit_label")},
          len(returns))

    # The same core, invoked the way the labeller invokes it.
    sessions, index = L.load_sessions(conn)
    horizon_end = L.data_horizon(conn)
    direct, _p, _c = L.compute_listing_labels(
        L.load_bars(conn, bench_id),
        sorted({r[0] for r in conn.execute("SELECT DISTINCT as_of_date FROM pit_label")}),
        sessions, index, horizon_end, path_horizons=())
    same = all(returns[(d["as_of_date"], d["horizon"])] == d["forward_return"]
               for d in direct)
    check("and they are bit-identical to compute_listing_labels' own output", same)

    summary = B.backfill_benchmark_returns(
        conn, db_path=path, label_run_id=run, batch_listings=1,
        checkpoint_every=1, min_free_gib=0.0, progress=False)
    check("the backfill completes", summary["status"] == "completed", summary.get("status"))
    check("and touches every label row",
          summary["rows_touched"] == conn.execute(
              "SELECT COUNT(*) FROM pit_label").fetchone()[0])

    rows = conn.execute(
        """SELECT forward_return, benchmark_forward_return, excess_return,
                  net_forward_return, benchmark_listing_id, cost_assumption_set_id
             FROM pit_label WHERE excess_return IS NOT NULL""").fetchall()
    check("excess is gross minus benchmark on every resolved row",
          rows and all(abs(r[2] - (r[0] - r[1])) < 1e-15 for r in rows), len(rows))

    flat = S.cost_set(conn, S.DEFAULT_COST_SET)
    rt = S.round_trip_fraction(flat)
    check("net is gross through the stored round-trip fraction, exactly",
          all(abs(r[3] - ((1.0 + r[0]) * (1.0 - rt) - 1.0)) < 1e-15 for r in rows))
    check("and every such row names the cost set it used",
          all(int(r[5]) == int(flat["assumption_set_id"]) for r in rows))

    fast = conn.execute(
        """SELECT AVG(excess_return) FROM pit_label
            WHERE listing_id = ? AND excess_return IS NOT NULL""",
        (listings["FAST"],)).fetchone()[0]
    slow = conn.execute(
        """SELECT AVG(excess_return) FROM pit_label
            WHERE listing_id = ? AND excess_return IS NOT NULL""",
        (listings["SLOW"],)).fetchone()[0]
    check("the name compounding above the benchmark has positive excess",
          fast > 0, fast)
    check("and the one below it has negative excess", slow < 0, slow)
    cleanup(conn, directory)


# --------------------------------------------------------------------------
# 4. THE ACCEPTANCE TEST: every NULL that should stay NULL does
# --------------------------------------------------------------------------

def test_nulls_and_refusals() -> None:
    print("\n4. the NULLs that must survive, and the runs that must be refused")
    conn, path, directory, listings, run = _seeded()
    bench_id = B.benchmark_listing_id(conn, "us_total_market")
    B.backfill_benchmark_returns(conn, db_path=path, label_run_id=run,
                                 batch_listings=1, min_free_gib=0.0, progress=False)

    check("EVERY row names the benchmark it was measured against, censored or not",
          conn.execute("SELECT COUNT(*) FROM pit_label WHERE benchmark_listing_id IS NULL"
                       ).fetchone()[0] == 0)
    check("a censored benchmark window leaves the benchmark return NULL",
          conn.execute(
              """SELECT COUNT(*) FROM pit_label
                  WHERE benchmark_forward_return IS NULL""").fetchone()[0] > 0)
    check("excess is NULL wherever either leg is NULL, and never otherwise",
          conn.execute(
              """SELECT COUNT(*) FROM pit_label
                  WHERE (excess_return IS NULL)
                     <> (forward_return IS NULL OR benchmark_forward_return IS NULL)"""
          ).fetchone()[0] == 0)
    check("net is NULL exactly where the gross return is",
          conn.execute(
              """SELECT COUNT(*) FROM pit_label
                  WHERE (net_forward_return IS NULL) <> (forward_return IS NULL)"""
          ).fetchone()[0] == 0)

    # The bankruptcy-in-month-three case: a company resolves inside a window
    # the benchmark has not finished. Gross is real; excess must stay NULL.
    resolved_but_no_benchmark = conn.execute(
        """SELECT COUNT(*) FROM pit_label
            WHERE forward_return IS NOT NULL
              AND benchmark_forward_return IS NULL""").fetchone()[0]
    conn.execute(
        """UPDATE pit_label SET forward_return = -1.0, censored = 0,
                  terminal_reason = 'bankruptcy'
            WHERE benchmark_forward_return IS NULL AND horizon = '12M'""")
    conn.commit()
    B.backfill_benchmark_returns(conn, db_path=path, label_run_id=run,
                                 batch_listings=1, min_free_gib=0.0, progress=False)
    check("a company that resolved early against an unfinished benchmark window "
          "keeps a real gross return and a NULL excess",
          conn.execute(
              """SELECT COUNT(*) FROM pit_label
                  WHERE terminal_reason = 'bankruptcy' AND forward_return = -1.0
                    AND excess_return IS NOT NULL""").fetchone()[0] == 0
          and conn.execute(
              """SELECT COUNT(*) FROM pit_label
                  WHERE terminal_reason = 'bankruptcy'""").fetchone()[0] > 0,
          resolved_but_no_benchmark)

    check("the backfill refuses to run without a run id",
          _raises(lambda: B.backfill_benchmark_returns(conn, db_path=path),
                  ValueError))

    spy = B.benchmark_listing_id(conn, "us_large_cap_500")
    conn.execute("UPDATE pit_label SET benchmark_listing_id = ? WHERE listing_id = ?",
                 (spy, listings["FAST"]))
    conn.commit()
    refusal = B.backfill_benchmark_returns(
        conn, db_path=path, label_run_id=run, batch_listings=1,
        min_free_gib=0.0, progress=False)
    check("and refuses outright when rows already hold a DIFFERENT benchmark",
          refusal["status"] == "refused:rows_hold_a_different_benchmark",
          refusal.get("status"))
    conn.execute("UPDATE pit_label SET benchmark_listing_id = ?", (bench_id,))
    conn.commit()

    conn.execute("DELETE FROM pit_cost_assumption")
    conn.commit()
    missing = B.backfill_benchmark_returns(
        conn, db_path=path, label_run_id=run, batch_listings=1,
        min_free_gib=0.0, progress=False)
    check("a missing cost set stops the run rather than inventing a cost",
          str(missing["status"]).startswith("refused:no_cost_set"),
          missing.get("status"))
    cleanup(conn, directory)


# --------------------------------------------------------------------------
# 5. Reporting
# --------------------------------------------------------------------------

def test_reporting() -> None:
    print("\n5. what the coverage report has to say out loud")
    conn, path, directory, listings, run = _seeded()
    B.backfill_benchmark_returns(conn, db_path=path, label_run_id=run,
                                 batch_listings=1, min_free_gib=0.0, progress=False)
    report = B.coverage(conn)
    check("it reports the sample scope on the coverage itself",
          report["sample_scope"] == pit_store.SAMPLE_SURVIVOR_ONLY)
    check("and explains which direction the excess return is biased",
          "UPWARD" in report["sample_scope_note"])
    check("every row is accounted for",
          report["n_benchmark_named"] == report["n_rows"])
    check("it separates 'has a gross return' from 'has an excess return'",
          report["gross_but_no_excess"] >= 0
          and report["n_excess_return"] <= report["n_gross_return"])
    check("it lists the registered benchmarks with their kinds",
          {b["symbol"] for b in report["benchmarks"]} == {"VTI", "SPY"})

    spread = B.benchmark_spread(conn, "12M")
    check("the VTI/SPY spread is measurable rather than argued about",
          spread and all("spread" in r for r in spread), len(spread))
    cleanup(conn, directory)


def main() -> int:
    test_gate()
    test_seed_and_containment()
    test_arithmetic()
    test_nulls_and_refusals()
    test_reporting()
    print()
    if fails:
        print(f"{len(fails)} FAILED: {fails}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
