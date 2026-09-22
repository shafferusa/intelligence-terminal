"""Benchmark instruments, and the excess returns that were NULL without them.

`benchmark_forward_return`, `excess_return` and `net_forward_return` were NULL
on all 2,334,978 label rows, and the reason was structural rather than an
oversight: `pit_listing` is populated by a gate fed from issuers that file
`dei:TradingSymbol`, and an index ETF trust does not file one. There was no
path by which a benchmark could enter the store, so every excess return in the
research chain was missing and no amount of re-running the labeller would have
produced one.

WHERE A BENCHMARK LIVES, and why. `pit_price_bar.listing_id` is a foreign key
into `pit_listing`, so a benchmark that is not a listing cannot have bars
without a second bar table -- and a second bar table means a second copy of the
dedup-by-ingest rule, the stale-print rule, the adjusted-close basis and the
disk floor, which is how two modules end up with two answers to one question.
So a benchmark IS a `pit_listing` row, with:

    entity_id        NULL          -- it is not an issuer and has no CIK
    instrument_kind  'benchmark'   -- what keeps it out of "the companies"

and `pit_benchmark` is the registry that says which benchmark it is and what
kind of question it answers. NOTHING here touches company CIK logic:
`pit_prices.gate_chart` is not called and cannot be, because it requires an
entity_id and refuses anything whose `instrumentType` is not an operating
company's. `gate_benchmark_chart` below is a separate, explicit gate with its
own checks, and it is stricter in the one place that matters -- it requires the
payload's own symbol to echo the one requested, which is the check that catches
a reassigned ticker.

BENCHMARK KIND IS EXPLICIT. `broad_equity_market`, `sector` and `asset_class`
are three different questions and an excess return against the wrong one is not
a small error, it is a different result wearing the same column name. Only
broad-equity instruments are seeded here; sector and asset-class benchmarks are
declared in the vocabulary and deliberately left unseeded rather than faked
from an equity proxy.

WHY VTI IS THE DEFAULT. `ml_lab` already benchmarks the live path against VTI,
and research disagreeing with production about what "the market" means is a
difference that would show up as alpha. Beyond consistency: the labelled
universe is a total-market cross-section of 2,574 listings across every size
bucket, and VTI's CRSP US Total Market basket is the matching population where
the S&P 500 is a large-cap subset -- benchmarking small caps against SPY prices
the size factor into every excess return. SPY is seeded beside it as a
registered, ingested cross-check so the size effect can be MEASURED rather than
argued about, but it is not the default and is not written into any label.

THE SURVIVORSHIP TRAP THIS CREATES, stated first because it is the most
important thing in this module. VTI's own return series is NOT
survivorship-biased: the fund's NAV absorbed every constituent that failed. The
stock leg is survivor-only: 2,574 listings, every one alive in 2026. Subtracting
a clean benchmark from a survivors-only stock return therefore produces an
excess return biased UPWARD by the entire survivorship gap -- excess is WORSE
than gross here, not better. Every row this module writes is part of a label set
stamped SURVIVOR_ONLY_DIAGNOSTIC and may not be used to promote a model.

Tables written: `pit_listing` (two benchmark rows), `pit_price_bar` and
`pit_corporate_action` (their bars and dividends), `pit_benchmark`,
`pit_ingest_run`, `pit_label_run`, and four columns of `pit_label`. The frozen
production core is not imported.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
import sys
import time
from typing import Any, Mapping, Optional, Sequence

import pit_labels
import pit_labelset
import pit_prices
import pit_store
from pit_prices import checkpoint_wal, disk_free_gib, require_disk, wal_bytes
from pit_prices import _with_lock_retry
from pit_store import transaction

# --------------------------------------------------------------------------
# Versions and vocabulary
# --------------------------------------------------------------------------

#: Stamped on the registry rows and on the ingest run.
BENCHMARK_SOURCE_VERSION = "pit_benchmark_v1_yahoo_adjclose"

#: What a benchmark's return is computed from. Identical to the label module's
#: basis on purpose: a benchmark measured on a different convention from the
#: names measured against it is an excess return with a bias built into it.
PRICE_SOURCE = pit_labels.PRICE_SOURCE

#: The window. Identical to `pit_prices.PRICE_START`, so the benchmark covers
#: every session any label in this store can anchor on.
PRICE_START = pit_prices.PRICE_START

#: Gate verdicts, in this module's own vocabulary.
GATE_ACCEPTED = "accepted"
GATE_REJECTED = "rejected"
REJECT_NO_META = "no_meta"
REJECT_NOT_DAILY = "granularity_not_daily"
REJECT_SYMBOL_MISMATCH = "payload_symbol_is_not_the_one_requested"
REJECT_INSTRUMENT_TYPE = "instrument_type_not_expected"
REJECT_CURRENCY = "currency_not_expected"
REJECT_FIRST_TRADE_TOO_LATE = "first_trade_after_required_coverage"
REJECT_CLAIMED_BY_ISSUER = "symbol_already_claimed_by_an_issuer_listing"

#: Free-space floor. The same one, for the same reason.
DEFAULT_MIN_FREE_GIB = pit_prices.DEFAULT_MIN_FREE_GIB
DEFAULT_BATCH_LISTINGS = pit_labelset.DEFAULT_BATCH_LISTINGS
DEFAULT_CHECKPOINT_EVERY = pit_labelset.DEFAULT_CHECKPOINT_EVERY

#: The instruments. `expected_*` fields are GATE CONDITIONS, not stored facts:
#: what is stored is what the payload actually reported. `must_cover` is the
#: latest first-trade date that would still let this instrument benchmark the
#: whole label window -- an instrument that started after it is refused rather
#: than used for the part of the window it does cover.
BENCHMARK_SPECS: tuple[dict[str, Any], ...] = (
    {
        "benchmark_key": "us_total_market",
        "benchmark_kind": pit_store.BENCHMARK_BROAD_EQUITY,
        "symbol": "VTI",
        "instrument_name": "Vanguard Total Stock Market ETF",
        "tracks": "CRSP US Total Market Index",
        "asset_class": "equity",
        "sector_key": None,
        "expected_instrument_type": "ETF",
        "expected_currency": "USD",
        "must_cover": PRICE_START,
        "is_default_for_kind": 1,
        "selection_note": (
            "Default broad-market benchmark. Chosen over SPY on two grounds. "
            "(1) Consistency: ml_lab's live path already benchmarks against "
            "VTI, and a research chain that disagrees with production about "
            "what 'the market' is manufactures alpha out of the disagreement. "
            "(2) Population match: the labelled universe is a total-market "
            "cross-section across every size bucket, and CRSP US Total Market "
            "is that population, where the S&P 500 is a large-cap subset -- "
            "benchmarking small caps against SPY prices the size factor into "
            "every excess return. SPY is seeded as a registered cross-check."
        ),
    },
    {
        "benchmark_key": "us_large_cap_500",
        "benchmark_kind": pit_store.BENCHMARK_BROAD_EQUITY,
        "symbol": "SPY",
        "instrument_name": "SPDR S&P 500 ETF Trust",
        "tracks": "S&P 500 Index",
        "asset_class": "equity",
        "sector_key": None,
        "expected_instrument_type": "ETF",
        "expected_currency": "USD",
        "must_cover": PRICE_START,
        "is_default_for_kind": 0,
        "selection_note": (
            "Registered and ingested as a CROSS-CHECK, not as the default and "
            "not written into any label. Its purpose is to make the large-cap "
            "versus total-market difference a measurable quantity in this "
            "store rather than an argument: VTI minus SPY over the same grid is "
            "the size tilt that a benchmark choice silently imposes."
        ),
    },
)

#: Survivorship statement attached to every registry row. An index ETF is the
#: one instrument in this store that is NOT survivorship-biased, which is
#: precisely what makes the excess return dangerous.
SURVIVORSHIP_NOTE = (
    "The benchmark's own series is NOT survivorship-biased: the fund's NAV "
    "absorbed every constituent that failed. The stock leg it is subtracted "
    "from IS survivor-only. Excess return on this store is therefore biased "
    "UPWARD by the whole survivorship gap and is WORSE evidence than the gross "
    "return, not better. SURVIVOR_ONLY_DIAGNOSTIC."
)

SCHEMA = """
-- `pit_benchmark` is declared by `pit_store`. This module adds one index: the
-- backfill and every report ask "which listings are benchmarks", and on a
-- table that is 99.9% companies that question should not be a scan.
CREATE INDEX IF NOT EXISTS idx_pit_listing_kind
    ON pit_listing (instrument_kind);
"""


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def ensure_schema(conn: sqlite3.Connection) -> str:
    """Create this module's schema plus the label-set schema it depends on."""
    base = pit_labelset.ensure_schema(conn)
    before = set(r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index'"))
    conn.executescript(SCHEMA)
    conn.commit()
    created = sorted(set(r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index'")) - before)
    return f"{base}; indexes {created}" if created else base


def connect(db_path: str = pit_store.DEFAULT_PIT_DB_PATH) -> sqlite3.Connection:
    return pit_prices.connect(db_path)


# --------------------------------------------------------------------------
# 1. The gate -- explicit, and not the company gate
# --------------------------------------------------------------------------

def gate_benchmark_chart(conn: sqlite3.Connection, spec: Mapping[str, Any],
                         chart: Mapping[str, Any]) -> dict[str, Any]:
    """Decide whether this payload really is the benchmark we asked for.

    `pit_prices.gate_chart` is NOT reused and could not be: it takes an
    entity_id, refuses anything whose `instrumentType` is not an operating
    company's, and writes a `pit_listing_gate` row keyed to an issuer. Forcing
    a broad market index through issuer identity is exactly the mistake this
    module exists to avoid.

    The checks, in order, each one a way a benchmark ingest can silently ingest
    the wrong series:

      1. a payload and a daily granularity, or every date below is meaningless;
      2. the payload's OWN symbol echoes the request -- Yahoo resolves and
         redirects, and a reassigned ticker answers 200 with a clean series;
      3. `instrumentType` is what we expect an ETF to be, which is the check
         that separates an index fund from an operating company that happens to
         share a ticker;
      4. currency, because a return in the wrong numeraire is still a number;
      5. the first trade is early enough to cover the WHOLE label window. A
         benchmark that starts halfway through is refused, not used for half --
         a benchmark that exists for only part of the grid puts a structural
         break in the middle of every excess return;
      6. the symbol is not already an ISSUER's listing. If it is, the store
         holds a company under this ticker and the benchmark must not upsert
         over it.
    """
    symbol = str(spec["symbol"]).strip().upper()
    result: dict[str, Any] = {
        "symbol": symbol, "accepted": False, "verdict": GATE_REJECTED,
        "reason": REJECT_NO_META, "first_trade_date": None,
        "instrument_type": None, "exchange": None, "currency": None,
        "long_name": None, "http_status": chart.get("status"),
        "granularity": chart.get("granularity"),
    }
    meta = chart.get("meta")
    if not meta:
        return result
    if chart.get("granularity") != "1d":
        result["reason"] = REJECT_NOT_DAILY
        return result

    payload_symbol = str(meta.get("symbol") or "").strip().upper()
    first_trade = pit_prices.exchange_day(meta.get("firstTradeDate"),
                                          meta.get("gmtoffset"))
    result.update({
        "payload_symbol": payload_symbol,
        "first_trade_date": first_trade,
        "instrument_type": meta.get("instrumentType"),
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
        "currency": meta.get("currency"),
        "long_name": meta.get("longName") or meta.get("shortName"),
    })

    if payload_symbol and payload_symbol != symbol:
        result["reason"] = REJECT_SYMBOL_MISMATCH
        return result
    expected_type = spec.get("expected_instrument_type")
    if expected_type and str(result["instrument_type"] or "").upper() != expected_type.upper():
        result["reason"] = REJECT_INSTRUMENT_TYPE
        return result
    expected_ccy = spec.get("expected_currency")
    if expected_ccy and str(result["currency"] or "").upper() != expected_ccy.upper():
        result["reason"] = REJECT_CURRENCY
        return result
    if first_trade is None or first_trade > str(spec["must_cover"]):
        result["reason"] = REJECT_FIRST_TRADE_TOO_LATE
        return result

    claimed = conn.execute(
        """SELECT listing_id, entity_id, instrument_kind FROM pit_listing
            WHERE symbol = ? AND entity_id IS NOT NULL""", (symbol,)).fetchone()
    if claimed is not None:
        result["reason"] = REJECT_CLAIMED_BY_ISSUER
        result["claimed_by_entity_id"] = int(claimed["entity_id"])
        return result

    result["accepted"] = True
    result["verdict"] = GATE_ACCEPTED
    result["reason"] = "accepted"
    return result


# --------------------------------------------------------------------------
# 2. Seeding and ingest
# --------------------------------------------------------------------------

def register_benchmark(conn: sqlite3.Connection, spec: Mapping[str, Any],
                       listing_id: int, gate: Mapping[str, Any]) -> str:
    """Write or refresh one `pit_benchmark` row. Returns a status string."""
    def _write() -> str:
        with transaction(conn):
            conn.execute(
                """INSERT INTO pit_benchmark
                       (benchmark_key, benchmark_kind, symbol, listing_id,
                        instrument_name, tracks, asset_class, sector_key,
                        currency, inception_date, return_basis,
                        is_default_for_kind, price_source, survivorship_note,
                        selection_note, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (benchmark_key) DO UPDATE SET
                        listing_id = excluded.listing_id,
                        currency = excluded.currency,
                        inception_date = excluded.inception_date""",
                (spec["benchmark_key"], spec["benchmark_kind"], spec["symbol"],
                 listing_id, spec["instrument_name"], spec["tracks"],
                 spec["asset_class"], spec["sector_key"], gate.get("currency"),
                 gate.get("first_trade_date"), "adjclose",
                 int(spec["is_default_for_kind"]), PRICE_SOURCE,
                 SURVIVORSHIP_NOTE, spec["selection_note"], _now()))
        return "registered"
    return _with_lock_retry(_write)


def seed_benchmark(conn: sqlite3.Connection, spec: Mapping[str, Any], *,
                   db_path: str = pit_store.DEFAULT_PIT_DB_PATH,
                   start: str = PRICE_START,
                   min_free_gib: float = DEFAULT_MIN_FREE_GIB,
                   write: bool = True) -> dict[str, Any]:
    """Fetch, gate, register and ingest one benchmark instrument.

    One chart call, exactly as the company path takes one -- the gate reads
    `meta`, the ingest reads the same payload's bars, and a rejected symbol
    writes NOTHING: no listing, no registry row, no bars. A benchmark attributed
    to the wrong series is worse than a NULL excess return, because a NULL
    stops a consumer and a wrong benchmark does not.
    """
    symbol = str(spec["symbol"]).strip().upper()
    out: dict[str, Any] = {"symbol": symbol, "benchmark_key": spec["benchmark_key"]}

    existing = benchmark_listing_id(conn, spec["benchmark_key"])
    if existing is not None and pit_prices.listing_has_bars(conn, existing):
        out.update({"status": "already_seeded", "listing_id": existing,
                    "n_bars": 0})
        return out

    require_disk(db_path, min_free_gib)
    # Bounded at the store's data horizon, not at "now". A bar on a session the
    # store's calendar does not hold is a bar no horizon can address, and it
    # would quietly move `pit_labels.data_horizon` for every company too.
    chart = pit_prices.fetch_chart(symbol, start=start,
                                   end=pit_labels.data_horizon(conn))
    gate = gate_benchmark_chart(conn, spec, chart)
    out["gate"] = gate
    if not gate["accepted"]:
        out["status"] = f"refused:{gate['reason']}"
        return out
    if not write:
        out["status"] = "gate_only"
        return out

    listing_id = _with_lock_retry(
        pit_store.upsert_listing, conn, symbol, gate["first_trade_date"],
        entity_id=None, exchange=gate["exchange"],
        instrument_type=gate["instrument_type"], currency=gate["currency"],
        valid_from=gate["first_trade_date"], valid_to=None,
        confidence="proved", source=BENCHMARK_SOURCE_VERSION,
        instrument_kind=pit_store.INSTRUMENT_BENCHMARK)

    ingest_id = pit_store.start_ingest(
        conn, BENCHMARK_SOURCE_VERSION,
        json.dumps({"symbol": symbol, "benchmark_key": spec["benchmark_key"],
                    "start": start}, sort_keys=True))
    bars, stats = pit_prices.bars_from_chart(chart, listing_id, symbol,
                                             ingest_id, start)
    actions, counts = pit_prices.actions_from_chart(chart, listing_id, start)
    n_bars, n_actions = pit_prices.write_bars(conn, bars, actions)
    pit_store.finish_ingest(conn, ingest_id, "complete", rows_read=len(bars),
                            rows_kept=n_bars,
                            summary={**stats, **counts, "symbol": symbol})
    out.update({
        "status": "seeded", "listing_id": listing_id, "ingest_id": ingest_id,
        "n_bars": n_bars, "n_actions": n_actions,
        "registry": register_benchmark(conn, spec, listing_id, gate),
        **stats, **counts,
    })
    return out


def seed_all(conn: sqlite3.Connection, specs: Sequence[Mapping[str, Any]] = BENCHMARK_SPECS,
             **kwargs) -> list[dict[str, Any]]:
    """Seed every declared benchmark, in order. Idempotent."""
    return [seed_benchmark(conn, spec, **kwargs) for spec in specs]


# --------------------------------------------------------------------------
# 3. Lookup
# --------------------------------------------------------------------------

def benchmark_listing_id(conn: sqlite3.Connection, key: str) -> Optional[int]:
    row = conn.execute(
        "SELECT listing_id FROM pit_benchmark WHERE benchmark_key = ?",
        (key,)).fetchone()
    return int(row[0]) if row and row[0] is not None else None


def default_benchmark(conn: sqlite3.Connection,
                      kind: str = pit_store.BENCHMARK_BROAD_EQUITY
                      ) -> Optional[sqlite3.Row]:
    """The registered default instrument for one benchmark kind, or None.

    Returns None rather than guessing when no default is registered. A silent
    fallback to "whichever benchmark exists" is how an excess return ends up
    measured against something nobody chose.
    """
    return conn.execute(
        """SELECT * FROM pit_benchmark
            WHERE benchmark_kind = ? AND is_default_for_kind = 1
              AND listing_id IS NOT NULL
            ORDER BY benchmark_id LIMIT 1""", (kind,)).fetchone()


def benchmark_listings(conn: sqlite3.Connection) -> set[int]:
    """Every listing_id that is a benchmark rather than a company."""
    return {int(r[0]) for r in conn.execute(
        "SELECT listing_id FROM pit_listing WHERE instrument_kind = ?",
        (pit_store.INSTRUMENT_BENCHMARK,))}


# --------------------------------------------------------------------------
# 4. The benchmark's own forward returns
# --------------------------------------------------------------------------

def benchmark_forward_returns(conn: sqlite3.Connection, listing_id: int,
                              as_of_dates: Optional[Sequence[str]] = None,
                              horizons: Sequence[str] = pit_labels.HORIZON_ORDER,
                              ) -> dict[tuple[str, str], Optional[float]]:
    """(as_of, horizon) -> the benchmark's forward return, or None if censored.

    Computed by `pit_labels.compute_listing_labels` -- the SAME pure core that
    produced every company label in this store, over the same calendar, with the
    same anchor roll, the same real-trade requirement and the same censoring
    rule. That is not a convenience: a benchmark measured by a second
    implementation would differ from the names measured against it in ways that
    look exactly like alpha.

    The grid defaults to the as-of dates `pit_label` actually holds rather than
    to the peer-set grid, so a benchmark return can never exist for a date no
    label uses, nor be missing for one that does.

    A censored benchmark maps to None and is kept in the map. Dropping it would
    make a censored benchmark indistinguishable from a date the benchmark was
    never asked about.
    """
    if as_of_dates is None:
        as_of_dates = [r[0] for r in conn.execute(
            "SELECT DISTINCT as_of_date FROM pit_label ORDER BY as_of_date")]
    sessions, index = pit_labels.load_sessions(conn)
    horizon_end = pit_labels.data_horizon(conn)
    bars = pit_labels.load_bars(conn, listing_id)
    if not bars or horizon_end is None:
        return {}
    labels, _paths, _counters = pit_labels.compute_listing_labels(
        bars, as_of_dates, sessions, index, horizon_end,
        horizons=horizons, path_horizons=())
    return {(row["as_of_date"], row["horizon"]): row["forward_return"]
            for row in labels}


# --------------------------------------------------------------------------
# 5. The backfill
# --------------------------------------------------------------------------

_TEMP_SCHEMA = """
DROP TABLE IF EXISTS temp.bench_ret;
CREATE TABLE temp.bench_ret (
    as_of_date TEXT NOT NULL,
    horizon    TEXT NOT NULL,
    ret        REAL,
    PRIMARY KEY (as_of_date, horizon)
) WITHOUT ROWID;
"""

_BACKFILL_SQL = """
UPDATE pit_label
   SET benchmark_listing_id = :bench,
       benchmark_forward_return = (
           SELECT b.ret FROM temp.bench_ret b
            WHERE b.as_of_date = pit_label.as_of_date
              AND b.horizon = pit_label.horizon),
       excess_return = CASE WHEN forward_return IS NULL THEN NULL ELSE
           forward_return - (
               SELECT b.ret FROM temp.bench_ret b
                WHERE b.as_of_date = pit_label.as_of_date
                  AND b.horizon = pit_label.horizon) END,
       net_forward_return = CASE WHEN forward_return IS NULL THEN NULL ELSE
           (1.0 + forward_return) * (1.0 - :round_trip) - 1.0 END,
       cost_assumption_set_id = :cost_set
 WHERE listing_id BETWEEN :low AND :high
   AND listing_id <> :bench
   AND (benchmark_listing_id IS NULL OR benchmark_listing_id = :bench)
"""


def backfill_benchmark_returns(conn: sqlite3.Connection, *,
                               db_path: str = pit_store.DEFAULT_PIT_DB_PATH,
                               benchmark_key: Optional[str] = None,
                               cost_set_label: str = pit_labelset.DEFAULT_COST_SET,
                               label_run_id: Optional[int] = None,
                               batch_listings: int = DEFAULT_BATCH_LISTINGS,
                               checkpoint_every: int = DEFAULT_CHECKPOINT_EVERY,
                               min_free_gib: float = DEFAULT_MIN_FREE_GIB,
                               progress: bool = True) -> dict[str, Any]:
    """Write benchmark, excess and net returns onto every label row.

    THREE THINGS THIS REFUSES TO DO, each of which would be silent:

      * It refuses to run at all if any row already names a DIFFERENT benchmark.
        Overwriting one benchmark's excess returns with another's leaves a table
        where every number is defined and none of them is comparable.
      * It never fills a NULL. `benchmark_forward_return` stays NULL where the
        benchmark's own window is unfinished, and `excess_return` stays NULL
        whenever either leg is NULL -- including the real case where a company
        resolved early (a bankruptcy in month three of a twelve-month window)
        against a benchmark window that has not finished. An excess return with
        one leg missing is not a small error, it is a number with no meaning.
      * It does not touch `computed_at` or `label_run_id`. Those record when and
        by what the GROSS label was computed, and a derived column arriving
        later does not change that history.

    Batched by listing-id RANGE because `pit_label` is physically ordered by
    listing: the build wrote all of one listing's rows before moving on. Disk is
    re-checked before every batch and every checkpoint's busy flag is read.
    """
    if label_run_id is None:
        raise ValueError("label_run_id is required: a derived column must name "
                         "the run that derived it")
    row = (conn.execute("SELECT * FROM pit_benchmark WHERE benchmark_key = ?",
                        (benchmark_key,)).fetchone() if benchmark_key
           else default_benchmark(conn))
    if row is None or row["listing_id"] is None:
        return {"status": "refused:no_benchmark_seeded",
                "note": "seed a benchmark before asking for an excess return"}
    bench = int(row["listing_id"])

    costs = pit_labelset.cost_set(conn, cost_set_label)
    if costs is None:
        return {"status": f"refused:no_cost_set:{cost_set_label}",
                "note": "run pit_labelset.seed_cost_assumptions first, or the "
                        "net return has no stated assumption behind it"}
    round_trip = pit_labelset.round_trip_fraction(costs)

    foreign = int(conn.execute(
        """SELECT COUNT(*) FROM pit_label
            WHERE benchmark_listing_id IS NOT NULL AND benchmark_listing_id <> ?""",
        (bench,)).fetchone()[0])
    if foreign:
        return {"status": "refused:rows_hold_a_different_benchmark",
                "n_rows_other_benchmark": foreign,
                "note": "two benchmarks in one column would make every excess "
                        "return in the table incomparable with the next"}

    returns = benchmark_forward_returns(conn, bench)
    if not returns:
        return {"status": "refused:benchmark_has_no_bars", "listing_id": bench}

    conn.executescript(_TEMP_SCHEMA)
    with transaction(conn):
        conn.executemany(
            "INSERT INTO temp.bench_ret (as_of_date, horizon, ret) VALUES (?, ?, ?)",
            [(k[0], k[1], v) for k, v in returns.items()])

    started = time.time()
    ranges = pit_labelset._listing_ranges(conn, batch_listings)
    file_before = os.path.getsize(db_path) if os.path.exists(db_path) else 0
    summary: dict[str, Any] = {
        "status": "completed",
        "benchmark_key": row["benchmark_key"],
        "benchmark_symbol": row["symbol"],
        "benchmark_listing_id": bench,
        "cost_assumption_set_id": int(costs["assumption_set_id"]),
        "cost_assumption_label": costs["label"],
        "round_trip_fraction": round_trip,
        "n_benchmark_points": len(returns),
        "n_benchmark_points_censored": sum(1 for v in returns.values() if v is None),
        "n_batches": len(ranges),
        "rows_touched": 0,
        "checkpoints": 0,
        "checkpoints_busy": 0,
        "wal_peak_bytes": wal_bytes(db_path),
        "free_gib_min": disk_free_gib(db_path),
        "db_bytes_before": file_before,
    }

    for i, (low, high) in enumerate(ranges, start=1):
        try:
            free = require_disk(db_path, min_free_gib)
        except RuntimeError as exc:
            summary["status"] = f"stopped:disk_floor: {exc}"
            break
        summary["free_gib_min"] = min(summary["free_gib_min"], free)

        def _write(low=low, high=high) -> int:
            with transaction(conn):
                cursor = conn.execute(_BACKFILL_SQL, {
                    "bench": bench, "round_trip": round_trip,
                    "cost_set": int(costs["assumption_set_id"]),
                    "low": low, "high": high})
                return int(cursor.rowcount or 0)

        summary["rows_touched"] += _with_lock_retry(_write)
        summary["wal_peak_bytes"] = max(summary["wal_peak_bytes"], wal_bytes(db_path))

        if i % checkpoint_every == 0 or i == len(ranges):
            state = checkpoint_wal(conn, db_path)
            summary["checkpoints"] += 1
            summary["checkpoints_busy"] += int(state["busy"])
            if progress:
                print(f"  batch {i}/{len(ranges)} listings {low}-{high}: "
                      f"{summary['rows_touched']:,} rows, "
                      f"wal {state['wal_after'] / 2 ** 20:.1f} MiB "
                      f"(busy={state['busy']}), free {free:.2f} GiB")

    conn.executescript("DROP TABLE IF EXISTS temp.bench_ret;")
    summary["db_bytes_after"] = os.path.getsize(db_path) if os.path.exists(db_path) else 0
    summary["db_growth_mib"] = round(
        (summary["db_bytes_after"] - file_before) / 2 ** 20, 1)
    summary["wal_peak_mib"] = round(summary["wal_peak_bytes"] / 2 ** 20, 1)
    summary["elapsed_seconds"] = round(time.time() - started, 1)
    return summary


# --------------------------------------------------------------------------
# 6. Reporting
# --------------------------------------------------------------------------

def coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    """What the benchmark columns now hold, and what they still do not."""
    row = conn.execute(
        """SELECT COUNT(*) AS n,
                  SUM(benchmark_listing_id IS NOT NULL) AS n_bench_named,
                  SUM(benchmark_forward_return IS NOT NULL) AS n_bench_return,
                  SUM(excess_return IS NOT NULL) AS n_excess,
                  SUM(net_forward_return IS NOT NULL) AS n_net,
                  SUM(forward_return IS NOT NULL) AS n_gross
             FROM pit_label""").fetchone()
    by_horizon = [
        {"horizon": r["horizon"], "n_rows": int(r["n"]),
         "n_gross": int(r["n_gross"] or 0), "n_excess": int(r["n_excess"] or 0),
         "mean_gross": None if r["mean_gross"] is None else round(r["mean_gross"], 6),
         "mean_excess": None if r["mean_excess"] is None else round(r["mean_excess"], 6),
         "mean_benchmark": None if r["mean_bench"] is None else round(r["mean_bench"], 6)}
        for r in conn.execute(
            """SELECT horizon, COUNT(*) AS n,
                      SUM(forward_return IS NOT NULL) AS n_gross,
                      SUM(excess_return IS NOT NULL) AS n_excess,
                      AVG(forward_return) AS mean_gross,
                      AVG(excess_return) AS mean_excess,
                      AVG(benchmark_forward_return) AS mean_bench
                 FROM pit_label GROUP BY horizon ORDER BY horizon""")]
    return {
        "n_rows": int(row["n"]),
        "n_benchmark_named": int(row["n_bench_named"] or 0),
        "n_benchmark_return": int(row["n_bench_return"] or 0),
        "n_excess_return": int(row["n_excess"] or 0),
        "n_net_return": int(row["n_net"] or 0),
        "n_gross_return": int(row["n_gross"] or 0),
        "gross_but_no_excess": int((row["n_gross"] or 0) - (row["n_excess"] or 0)),
        "by_horizon": by_horizon,
        "benchmarks": [dict(r) for r in conn.execute(
            """SELECT benchmark_key, benchmark_kind, symbol, listing_id,
                      inception_date, is_default_for_kind FROM pit_benchmark
                ORDER BY benchmark_id""")],
        "sample_scope": pit_store.SAMPLE_SURVIVOR_ONLY,
        "sample_scope_note": SURVIVORSHIP_NOTE,
    }


def benchmark_spread(conn: sqlite3.Connection,
                     horizon: str = "12M") -> list[dict[str, Any]]:
    """VTI minus SPY over the shared grid: the size tilt a benchmark imposes.

    The reason SPY was ingested. Returns one row per as-of date where both
    instruments have a finished window, so the difference is a measurement
    rather than an argument about which broad benchmark to prefer.
    """
    out: list[dict[str, Any]] = []
    ids = {r["benchmark_key"]: r["listing_id"] for r in conn.execute(
        "SELECT benchmark_key, listing_id FROM pit_benchmark")}
    if len(ids) < 2 or any(v is None for v in ids.values()):
        return out
    series = {k: benchmark_forward_returns(conn, int(v), horizons=(horizon,))
              for k, v in ids.items()}
    keys = sorted(series)
    for as_of, _h in sorted(series[keys[0]]):
        values = {k: series[k].get((as_of, horizon)) for k in keys}
        if any(v is None for v in values.values()):
            continue
        out.append({"as_of_date": as_of, **{k: round(v, 6) for k, v in values.items()},
                    "spread": round(values[keys[0]] - values[keys[1]], 6)})
    return out


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    """Seed the benchmarks, backfill the derived columns, then report."""
    args = list(sys.argv[1:] if argv is None else argv)

    def opt(name: str, default: Any) -> Any:
        return args[args.index(name) + 1] if name in args else default

    db_path = str(opt("--db", pit_store.DEFAULT_PIT_DB_PATH))
    batch = int(opt("--batch-listings", DEFAULT_BATCH_LISTINGS))
    min_free = float(opt("--min-free-gib", DEFAULT_MIN_FREE_GIB))
    cost_label = str(opt("--cost-set", pit_labelset.DEFAULT_COST_SET))
    do_seed = "--seed" in args
    do_backfill = "--backfill" in args

    conn = connect(db_path)
    print(ensure_schema(conn))
    print(f"disk: {disk_free_gib(db_path):.2f} GiB free, floor {min_free:.2f} GiB, "
          f"wal {wal_bytes(db_path) / 2 ** 20:.1f} MiB")

    if do_seed:
        for result in seed_all(conn, db_path=db_path, min_free_gib=min_free):
            print(json.dumps(result, indent=2, sort_keys=True, default=str))

    if do_backfill:
        pit_labelset.assert_policy_matches_code(conn)
        bench = default_benchmark(conn)
        run_id = pit_labelset.start_label_run(
            conn, pit_labelset.RUN_BACKFILL_BENCHMARK,
            code_module="pit_benchmark.py",
            benchmark_listing_id=None if bench is None else bench["listing_id"],
            note="benchmark, excess and net returns onto existing labels")
        summary = backfill_benchmark_returns(
            conn, db_path=db_path, cost_set_label=cost_label,
            label_run_id=run_id, batch_listings=batch, min_free_gib=min_free)
        pit_labelset.finish_label_run(
            conn, run_id,
            status="complete" if summary.get("status") == "completed" else summary.get("status", "error"),
            n_rows_written=summary.get("rows_touched"),
            benchmark_listing_id=summary.get("benchmark_listing_id"),
            cost_assumption_set_id=summary.get("cost_assumption_set_id"),
            evidence=summary)
        print(json.dumps(summary, indent=2, sort_keys=True, default=str))

    print("\ncoverage:")
    print(json.dumps(coverage(conn), indent=2, sort_keys=True, default=str))
    spread = benchmark_spread(conn, "12M")
    if spread:
        values = [r["spread"] for r in spread]
        print(f"\nVTI-vs-SPY 12M spread over {len(spread)} shared as-of dates: "
              f"mean {sum(values) / len(values):+.4f}, "
              f"min {min(values):+.4f}, max {max(values):+.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
