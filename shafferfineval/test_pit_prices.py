"""Tests for pit_prices: the gate, the split/spin-off split, and the disk guard.

Plain script, no pytest. Two halves:

  OFFLINE  a throwaway database and synthetic chart payloads. Every branch of
           the gate, the classifier and the bar writer, deterministic.
  LIVE     real Yahoo payloads for the named cohort, because the point of the
           gate is what it does to real reassigned tickers. Skipped with
           --offline; a network failure is reported as SKIP, never as PASS.

Run:  python test_pit_prices.py [--offline]
"""

from __future__ import annotations

import datetime as _dt
import os
import sqlite3
import sys
import tempfile

import pit_identity
import pit_prices
import pit_store
import pit_symbols

FAILURES: list[str] = []
SKIPS: list[str] = []
CHECKS = [0]


def check(name: str, cond: bool) -> bool:
    CHECKS[0] += 1
    print(("PASS  " if cond else "FAIL  ") + name)
    if not cond:
        FAILURES.append(name)
    return bool(cond)


def skip(name: str, why: str) -> None:
    print(f"SKIP  {name}  ({why})")
    SKIPS.append(f"{name}: {why}")


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

def _epoch(day: str) -> int:
    date = _dt.date.fromisoformat(day)
    return int(_dt.datetime(date.year, date.month, date.day, 14, 30,
                            tzinfo=_dt.timezone.utc).timestamp())


def fake_chart(*, first_trade: str, instrument: str = "EQUITY",
               exchange: str = "NasdaqGS", granularity: str = "1d",
               days: tuple[str, ...] = ("2015-06-29", "2015-06-30"),
               volumes: tuple = (1000, 0),
               splits: tuple = (), dividends: tuple = ()) -> dict:
    """A chart payload shaped exactly like Yahoo's, with nothing fetched."""
    stamps = [_epoch(d) for d in days]
    n = len(days)
    return {
        "status": 200,
        "meta": {"dataGranularity": granularity, "instrumentType": instrument,
                 "fullExchangeName": exchange, "currency": "USD",
                 "longName": "Test Co", "gmtoffset": -14400,
                 "firstTradeDate": _epoch(first_trade)},
        "granularity": granularity,
        "timestamps": stamps,
        "quote": {"open": [10.0] * n, "high": [11.0] * n, "low": [9.0] * n,
                  "close": [10.5] * n, "volume": list(volumes)[:n]},
        "adjclose": [9.75] * n,
        "events": {
            "splits": {str(_epoch(d)): {"date": _epoch(d), "numerator": num,
                                        "denominator": den,
                                        "splitRatio": f"{num}:{den}"}
                       for d, num, den in splits},
            "dividends": {str(_epoch(d)): {"date": _epoch(d), "amount": amt}
                          for d, amt in dividends},
        },
        "error": None,
    }


def temp_store() -> tuple[sqlite3.Connection, str]:
    path = os.path.join(tempfile.mkdtemp(prefix="pitprices_"), "t.db")
    conn = pit_store.init_db(path)
    pit_prices.ensure_schema(conn)
    return conn, path


def seed_entity(conn: sqlite3.Connection, cik: str) -> int:
    return pit_store.upsert_entity(conn, cik)


# --------------------------------------------------------------------------
# Offline: the gate
# --------------------------------------------------------------------------

def test_pre_1970_first_trade() -> None:
    """DEFECT #24: an issuer older than the Unix epoch must gate, not crash.

    `exchange_day` used to carry its own copy of the conversion because
    `pit_identity._exchange_date` died on Windows for a negative timestamp.
    The fix now lives in `pit_identity` and this is a delegation, so the two
    things worth asserting are that they agree everywhere and that a real
    pre-1970 payload survives the whole gate.
    """
    print("\n-- a first trade before 1970 (defect #24) --")

    #: The epoch Yahoo actually serves for the pre-CRSP NYSE roll -- Boeing,
    #: GE, IBM, Coca-Cola, Exxon, 3M, Caterpillar, J&J all report it -- read
    #: live from `chart.meta` on 2026-09-21. It is 1962-01-02 in New York.
    BA_FIRST_TRADE = -252322200
    NY = -14400

    check("Boeing's first trade dates to 1962-01-02, it does not raise",
          pit_prices.exchange_day(BA_FIRST_TRADE, NY) == "1962-01-02")
    check("the epoch recorded off the ingest that died also dates",
          pit_prices.exchange_day(-236260800, NY) == "1962-07-07")

    drift = [(e, o)
             for e in (BA_FIRST_TRADE, -236260800, -1, 0, 1, 76253400,
                       1435622400, 1719532800, "nonsense", None)
             for o in (0, NY, -18000, 3600, None, "eastern")
             if pit_prices.exchange_day(e, o) != pit_identity._exchange_date(e, o)]
    if drift:
        print("    drift:", drift)
    check("exchange_day is pit_identity._exchange_date, not a second copy",
          not drift)
    check("no private epoch constant is left behind in this module",
          not hasattr(pit_prices, "_EPOCH"))

    conn, path = temp_store()
    eid = seed_entity(conn, "12927")          # Boeing's CIK
    aged = pit_prices.gate_chart(conn, "BA", eid, "2015-06-30",
                                 fake_chart(first_trade="1962-01-02",
                                            exchange="NYQ"))
    if not aged["accepted"]:
        print("    gate result:", aged)
    check("a 1962 listing is accepted by the gate", aged["accepted"])
    check("and is stored as 1962-01-02, not as an error",
          aged.get("first_trade_date") == "1962-01-02")
    row = conn.execute(
        "SELECT first_trade_date FROM pit_listing WHERE symbol = 'BA'").fetchone()
    check("the pit_listing row carries the pre-1970 date",
          row is not None and row["first_trade_date"] == "1962-01-02")
    conn.close()


def test_gate_offline() -> None:
    print("\n-- gate, synthetic payloads --")
    conn, path = temp_store()
    eid = seed_entity(conn, "1")

    ok = pit_prices.gate_chart(conn, "OK", eid, "2015-06-30",
                               fake_chart(first_trade="2000-01-03"))
    check("a symbol first traded before the as-of is accepted", ok["accepted"])
    check("acceptance without proof is 'inferred', never 'proved'",
          ok["confidence"] == pit_identity.CONFIDENCE_INFERRED)
    check("a persisted acceptance writes a pit_listing row",
          ok["listing_id"] is not None)

    proved = pit_prices.gate_chart(conn, "OK2", eid, "2015-06-30",
                                   fake_chart(first_trade="2000-01-03"),
                                   proof='{"tag":"dei:TradingSymbol"}')
    check("a filed dei:TradingSymbol raises confidence to 'proved'",
          proved["confidence"] == pit_identity.CONFIDENCE_PROVED)

    late = pit_prices.gate_chart(conn, "REUSED", eid, "2015-06-30",
                                 fake_chart(first_trade="2026-07-17"))
    check("first trade AFTER the as-of is rejected (the reused-ticker gate)",
          not late["accepted"] and late["reason"] == pit_identity.REJECT_FIRST_TRADE_AFTER)
    check("a rejection writes no listing", late["listing_id"] is None)

    etf = pit_prices.gate_chart(conn, "FUND", eid, "2015-06-30",
                                fake_chart(first_trade="2000-01-03", instrument="ETF"))
    check("a fund where an operating company is expected is rejected",
          not etf["accepted"] and etf["reason"] == pit_identity.REJECT_INSTRUMENT_TYPE)

    monthly = pit_prices.gate_chart(conn, "COARSE", eid, "2015-06-30",
                                    fake_chart(first_trade="2000-01-03",
                                               granularity="1mo"))
    check("a coarsened series is rejected outright",
          not monthly["accepted"] and monthly["reason"] == pit_identity.REJECT_NOT_DAILY)

    otc = pit_prices.gate_chart(conn, "OTCNAME", eid, "2015-06-30",
                                fake_chart(first_trade="2000-01-03",
                                           exchange="OTC Markets OTCPK"))
    check("an OTC venue is FLAGGED and still accepted by default",
          otc["accepted"] and otc["otc_flag"] == 1)
    strict = pit_prices.gate_chart(conn, "OTCNAME2", eid, "2015-06-30",
                                   fake_chart(first_trade="2000-01-03",
                                              exchange="OTC Markets OTCPK"),
                                   expect_national_exchange=True)
    check("...and rejected when the caller demands a national exchange",
          not strict["accepted"] and strict["reason"] == pit_identity.REJECT_OTC)

    dead = pit_prices.gate_chart(conn, "GONE", eid, "2015-06-30",
                                 {"status": 404, "meta": None, "granularity": None})
    check("HTTP 404 is verdict 'no_data', NOT 'rejected'",
          dead["verdict"] == pit_prices.VERDICT_NO_DATA)
    broke = pit_prices.gate_chart(conn, "NET", eid, "2015-06-30",
                                  {"status": 0, "meta": None, "granularity": None})
    check("a failed request is verdict 'error', distinct from both",
          broke["verdict"] == pit_prices.VERDICT_ERROR)

    other = seed_entity(conn, "2")
    stolen = pit_prices.gate_chart(conn, "OK", other, "2015-06-30",
                                   fake_chart(first_trade="2000-01-03"))
    check("a listing already claimed by another entity is not reattributed",
          not stolen["accepted"]
          and stolen["reason"] == pit_prices.REJECT_CLAIMED_BY_OTHER_ENTITY)
    owner = conn.execute(
        "SELECT entity_id FROM pit_listing WHERE symbol = 'OK'").fetchone()
    check("...and the original owner still holds it", int(owner["entity_id"]) == eid)

    conn.close()


# --------------------------------------------------------------------------
# Offline: splits, spin-offs, bars
# --------------------------------------------------------------------------

def test_split_classification() -> None:
    print("\n-- splits versus spin-offs --")
    att = pit_prices.classify_split(1324, 1000)
    check("AT&T's 1324:1000 is classified a spin-off",
          att["event_type"] == pit_prices.EVENT_SPINOFF)
    check("...it adjusts the price", att["applies_to_price"] == 1)
    check("...and NOT the share count (the 32.4% error)",
          att["applies_to_shares"] == 0)
    check("...and the row says how it was decided",
          "331:250" in att["note"] and "spin_off" in att["note"])

    for num, den, label in ((4, 1, "4:1 forward"), (1, 8, "1:8 reverse"),
                            (3, 2, "3:2"), (20, 1, "20:1"), (21, 20, "21:20 dividend")):
        verdict = pit_prices.classify_split(num, den)
        check(f"a {label} split is a genuine share split",
              verdict["event_type"] == pit_prices.EVENT_SPLIT
              and verdict["applies_to_shares"] == 1)

    # The false positive a bound alone produces. 1:100 and 1:200 reverse splits
    # are routine for a distressed name and a bare bound would file them as
    # spin-offs -- which would leave the share count unchanged through a 100x
    # consolidation.
    for num, den in ((1, 100), (1, 200), (100, 1)):
        verdict = pit_prices.classify_split(num, den)
        check(f"a {num}:{den} reverse/forward split is a split, not a spin-off",
              verdict["event_type"] == pit_prices.EVENT_SPLIT
              and verdict["applies_to_shares"] == 1)

    for num, den, label in ((2376, 1000, "eBay/PayPal"), (1128, 1000, "Danaher/Veralto"),
                            (10000, 8939, "Darden/Four Corners")):
        verdict = pit_prices.classify_split(num, den)
        check(f"a real {label} separation ratio is a spin-off",
              verdict["event_type"] == pit_prices.EVENT_SPINOFF)

    junk = pit_prices.classify_split(None, None)
    check("an unparseable ratio never touches the share count",
          junk["applies_to_shares"] == 0)


def test_bars_offline() -> None:
    print("\n-- bars --")
    chart = fake_chart(first_trade="2000-01-03",
                       days=("2010-12-31", "2011-01-03", "2011-01-04"),
                       volumes=(500, 0, 900),
                       splits=(("2011-01-04", 1324, 1000),),
                       dividends=(("2011-01-03", 0.22),))
    bars, stats = pit_prices.bars_from_chart(chart, 7, "X", 1)
    check("bars before the price window are dropped", stats["n_bars"] == 2)
    check("the window boundary is the first stored bar",
          stats["bar_first"] == "2011-01-03")
    check("a zero-volume print is KEPT and counted",
          stats["n_zero_volume_bars"] == 1)
    check("close and adjclose are both stored and differ",
          bars[0][5] == 10.5 and bars[0][6] == 9.75)

    actions, counts = pit_prices.actions_from_chart(chart, 7)
    check("the spin-off is counted as a spin-off, not a split",
          counts["n_spinoffs"] == 1 and counts["n_splits"] == 0)
    check("the dividend is stored", counts["n_dividends"] == 1)
    dividend = [a for a in actions if a[2] == pit_prices.EVENT_DIVIDEND][0]
    check("a dividend adjusts price and never the share count",
          dividend[6] == 1 and dividend[7] == 0)

    conn, _ = temp_store()
    eid = seed_entity(conn, "9")
    listing = pit_store.upsert_listing(conn, "X", "2000-01-03", entity_id=eid)
    bars, _ = pit_prices.bars_from_chart(chart, listing, "X", 1)
    actions, _ = pit_prices.actions_from_chart(chart, listing)
    n_bars, n_actions = pit_prices.write_bars(conn, bars, actions)
    check("bars are written", n_bars == 2 and n_actions == 2)
    again = pit_prices.write_bars(conn, bars, actions)
    check("a repeated write under the same ingest is idempotent", again == (0, 0))
    check("listing_has_bars sees them", pit_prices.listing_has_bars(conn, listing))

    # A row stored under an older rule must not keep a verdict the classifier
    # no longer stands behind: the difference is a share count.
    conn.execute(
        """UPDATE pit_corporate_action SET event_type = ?, applies_to_shares = 1
            WHERE event_type = ?""",
        (pit_prices.EVENT_SPLIT, pit_prices.EVENT_SPINOFF))
    conn.commit()
    report = pit_prices.reclassify_splits(conn)
    check("reclassify_splits corrects a stale verdict",
          report["reclassified"] == 1 and report["to_spinoff"] == 1)
    fixed = conn.execute(
        "SELECT * FROM pit_corporate_action WHERE event_type = ?",
        (pit_prices.EVENT_SPINOFF,)).fetchone()
    check("...restoring applies_to_shares = 0", fixed["applies_to_shares"] == 0)
    check("...and saying on the row that it was reclassified",
          "reclassified_from=split" in fixed["source"])
    check("reclassify_splits is a no-op the second time",
          pit_prices.reclassify_splits(conn)["reclassified"] == 0)
    conn.close()


def test_disk_and_wal() -> None:
    print("\n-- disk guard and WAL checkpoint --")
    conn, path = temp_store()
    free = pit_prices.disk_free_gib(path)
    check("free space is measured, not assumed", free > 0)
    report = pit_prices.checkpoint_wal(conn, path)
    check("the checkpoint's return value is READ, not discarded",
          "busy" in report and "ok" in report)
    check("a checkpoint on an idle store is not busy", report["busy"] == 0)
    try:
        pit_prices.require_disk(path, min_free_gib=free + 1000.0)
        check("require_disk aborts below its floor", False)
    except RuntimeError as exc:
        check("require_disk aborts below its floor, with the number in the message",
              "ABORT" in str(exc) and "GiB free" in str(exc))
    check("wal_bytes reports a size for an existing store",
          pit_prices.wal_bytes(path) >= 0)
    conn.close()


def test_gate_ledger() -> None:
    print("\n-- the rejection ledger --")
    conn, _ = temp_store()
    eid = seed_entity(conn, "3")
    gate = pit_prices.gate_chart(conn, "REUSED", eid, "2015-06-30",
                                 fake_chart(first_trade="2026-07-17"))
    pit_prices.record_gate(conn, gate, priority_tier=0, ingest_id=1)
    row = conn.execute("SELECT * FROM pit_listing_gate WHERE symbol = 'REUSED'").fetchone()
    check("every rejection is recorded with its reason",
          row is not None and row["reason"] == pit_identity.REJECT_FIRST_TRADE_AFTER)
    check("the rejection carries the evidence that produced it",
          row["first_trade_date"] == "2026-07-17")
    pit_prices.record_gate(conn, gate, priority_tier=0, ingest_id=2)
    n = conn.execute("SELECT COUNT(*) AS n FROM pit_listing_gate").fetchone()["n"]
    check("re-gating updates the verdict rather than duplicating it", n == 1)

    # A first trade date shared by many unrelated issuers is a truncated feed,
    # not a reused ticker, and the ledger has to be able to say so.
    for n, cik in enumerate(("40", "41", "42", "43", "44", "45")):
        other = seed_entity(conn, cik)
        shared = pit_prices.gate_chart(conn, f"SYM{n}", other, "2015-06-30",
                                       fake_chart(first_trade="2026-07-17"))
        pit_prices.record_gate(conn, shared, priority_tier=0, ingest_id=1)
    # A gate that passed without its proof is recorded weaker than the evidence
    # already in the store supports.
    proved_eid = seed_entity(conn, "77")
    good = pit_prices.gate_chart(conn, "PROVE", proved_eid, "2015-06-30",
                                 fake_chart(first_trade="2000-01-03"))
    pit_prices.record_gate(conn, good, priority_tier=0, ingest_id=1)
    check("an acceptance with no proof passed through is 'inferred'",
          good["confidence"] == pit_identity.CONFIDENCE_INFERRED)
    before = pit_prices.attach_filed_proof(conn)
    check("...and stays inferred while no filed symbol backs it",
          before["upgraded_to_proved"] == 0
          and before["left_inferred_no_filed_symbol"] >= 1)
    conn.execute(
        """INSERT INTO pit_symbol_obs
               (entity_id, symbol, symbol_raw, source_accn, form, qtrs, filed,
                available_date, latency_policy_version, status, source,
                source_version, created_at)
           VALUES (?, 'PROVE', 'PROVE', '0000-1', '10-K', 0, '2014-02-01',
                   '2014-02-02', 'v1', 'ok', 'dera_notes', 'v1', '2014-02-02')""",
        (proved_eid,))
    conn.commit()
    after = pit_prices.attach_filed_proof(conn)
    check("a dated dei:TradingSymbol raises the listing to 'proved'",
          after["upgraded_to_proved"] == 1)
    listing = conn.execute(
        "SELECT confidence FROM pit_listing WHERE symbol = 'PROVE'").fetchone()
    check("...on the listing row itself",
          listing["confidence"] == pit_identity.CONFIDENCE_PROVED)
    check("attach_filed_proof is idempotent",
          pit_prices.attach_filed_proof(conn)["upgraded_to_proved"] == 0)

    suspects = pit_prices.suspect_first_trade_dates(conn, min_entities=5)
    check("a first trade date shared by 5+ unrelated issuers is flagged",
          any(s["first_trade_date"] == "2026-07-17" and s["n_entities"] >= 6
              for s in suspects))

    pit_prices._roll_up_listing_status(conn, {eid: {"resolved": False, "symbol": None,
                                                    "listing_id": None,
                                                    "reason": row["reason"],
                                                    "as_of": "2015-06-30"}})
    status = pit_symbols.listing_status(conn, eid)
    check("the per-entity roll-up lands in pit_listing_status",
          status is not None and status["status"] == pit_symbols.LISTING_UNRESOLVED)
    conn.close()


# --------------------------------------------------------------------------
# Live: the cohort that made the rules
# --------------------------------------------------------------------------

def test_live_cohort() -> None:
    print("\n-- live Yahoo cohort --")
    conn, _ = temp_store()
    charts: dict[str, dict] = {}
    for symbol in ("AAPL", "MSFT", "BRK-A", "BBBY", "SHLD", "FRCB", "LEHMQ", "T"):
        try:
            charts[symbol] = pit_prices.fetch_chart(symbol)
        except pit_prices.ThrottleStop as exc:
            skip("live cohort", f"throttled: {exc}")
            conn.close()
            return
        except Exception as exc:                      # network, not logic
            skip("live cohort", f"{symbol}: {exc}")
            conn.close()
            return

    def gate(symbol: str, as_of: str, entity: int) -> dict:
        eid = seed_entity(conn, str(1000 + entity))
        return pit_prices.gate_chart(conn, symbol, eid, as_of, charts[symbol])

    bbby_2015 = gate("BBBY", "2015-06-30", 1)
    check("BBBY is REJECTED for a 2015 as-of",
          not bbby_2015["accepted"]
          and bbby_2015["reason"] == pit_identity.REJECT_FIRST_TRADE_AFTER)
    print(f"      BBBY 2015: {bbby_2015['reason']} "
          f"first_trade={bbby_2015['first_trade_date']} "
          f"name={bbby_2015['long_name']!r} exch={bbby_2015['exchange']}")
    bbby_2026 = gate("BBBY", "2026-09-01", 2)
    check("BBBY is ACCEPTED for a 2026 as-of (the younger instrument)",
          bbby_2026["accepted"])

    # SHLD is rejected TWICE OVER, and which check fires depends on the as-of.
    # The ETF that now holds the ticker first traded 2023-09-14, so at a 2015
    # as-of the first-trade-date gate catches it before the instrument-type
    # check is ever reached. Both are rejections; asserting only the second one
    # would be asserting the order of the checks, not their effect. The
    # instrument-type check is verified at an as-of where the date gate passes.
    shld = gate("SHLD", "2015-06-30", 3)
    check("SHLD is rejected for a 2015 as-of", not shld["accepted"])
    check("...by the first-trade-date gate, the ETF being younger than the as-of",
          shld["reason"] == pit_identity.REJECT_FIRST_TRADE_AFTER)
    shld_now = gate("SHLD", "2026-09-01", 33)
    check("SHLD is rejected as a fund once the date gate no longer bites",
          not shld_now["accepted"]
          and shld_now["reason"] == pit_identity.REJECT_INSTRUMENT_TYPE)
    print(f"      SHLD: {shld['instrument_type']} {shld['long_name']!r} "
          f"first_trade={shld['first_trade_date']} "
          f"2015={shld['reason']} 2026={shld_now['reason']}")

    for symbol, entity in (("AAPL", 4), ("MSFT", 5), ("BRK-A", 6)):
        result = gate(symbol, "2015-06-30", entity)
        check(f"{symbol} is accepted", result["accepted"])

    frcb = gate("FRCB", "2015-06-30", 7)
    check("FRCB -- dead, still serving data -- is accepted", frcb["accepted"])
    check("...and flagged OTC rather than silently kept", frcb["otc_flag"] == 1)
    print(f"      FRCB: exch={frcb['exchange']} otc_flag={frcb['otc_flag']}")

    lehm = pit_prices.gate_chart(conn, "LEHMQ", seed_entity(conn, "1008"),
                                 "2008-06-30", charts["LEHMQ"])
    check("a 404 dead ticker is 'no_data', not a rejection",
          lehm["verdict"] == pit_prices.VERDICT_NO_DATA
          and charts["LEHMQ"]["status"] == 404)

    att = gate("T", "2004-06-30", 9)
    check("the known blind spot is honest: T passes for a 2004 as-of",
          att["accepted"])
    check("...and is recorded 'inferred', never 'proved', on ticker alone",
          att["confidence"] == pit_identity.CONFIDENCE_INFERRED)

    aapl = charts["AAPL"]
    check("the one call returns daily granularity", aapl["granularity"] == "1d")
    check("the one call returns bars as well as meta", len(aapl["timestamps"]) > 3000)
    bars, stats = pit_prices.bars_from_chart(aapl, 1, "AAPL", 1)
    check("AAPL bars start inside the price window",
          stats["bar_first"] >= pit_prices.PRICE_START)
    row = [b for b in bars if b[1] == "2015-06-30"]
    if row:
        check("AAPL's 2015-06-30 close is the split-adjusted 31.3575, not as-traded",
              abs(row[0][5] - 31.3575) < 0.01)
        check("...and adjclose differs from close (dividends too)",
              row[0][6] is not None and abs(row[0][6] - row[0][5]) > 0.01)
    actions, counts = pit_prices.actions_from_chart(aapl, 1)
    check("AAPL's 2020 4:1 split is stored as a genuine split",
          any(a[1] == "2020-08-31" and a[2] == pit_prices.EVENT_SPLIT
              and a[7] == 1 for a in actions))
    print(f"      AAPL actions: {counts}")

    att_actions, att_counts = pit_prices.actions_from_chart(charts["T"], 1)
    spin = [a for a in att_actions if a[1] == "2022-04-11"]
    if spin:
        check("AT&T's 2022-04-11 event is stored as a spin-off, shares untouched",
              spin[0][2] == pit_prices.EVENT_SPINOFF and spin[0][7] == 0)
        print(f"      T 2022-04-11: {spin[0][2]} ratio={spin[0][3]}:{spin[0][4]} "
              f"applies_to_shares={spin[0][7]}")
    else:
        skip("AT&T spin-off row", "no 2022-04-11 split event in this payload")
    conn.close()


def main() -> int:
    offline = "--offline" in sys.argv
    test_pre_1970_first_trade()
    test_gate_offline()
    test_split_classification()
    test_bars_offline()
    test_disk_and_wal()
    test_gate_ledger()
    if offline:
        skip("live cohort", "--offline")
    else:
        test_live_cohort()

    print(f"\n{CHECKS[0]} checks, {len(FAILURES)} failed, {len(SKIPS)} skipped")
    for name in FAILURES:
        print("  FAILED: " + name)
    for note in SKIPS:
        print("  SKIPPED: " + note)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
