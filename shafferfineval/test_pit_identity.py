"""The survivorship gate for the point-in-time research store.

    python test_pit_identity.py

THIS TEST GATES THE BUILD. If it fails, bulk historical scoring must not begin,
because a store that cannot represent a company which no longer exists cannot
measure anything about failure -- and a backtest that only ever sees survivors
is not merely optimistic, it is measuring a different question.

These are LIVE NETWORK tests against the real SEC and Yahoo endpoints, and that
is deliberate: the defects being guarded against are properties of the sources,
not of the code. BBBY's ticker really was reassigned to a different company;
Berkshire really has six Form 25 filings; First Republic really has no 10-K. A
mocked fixture would be a copy of the author's assumptions, and the assumptions
are exactly what needs testing.

Requests are kept under the SEC's 10/second ceiling by `pit_identity` and every
response is cached under the system temp directory, so a second run costs
almost nothing. The database is a throwaway file: nothing here touches the live
`shafferfineval.db`, and nothing here touches `shafferfineval_pit.db` either.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_identity as PI
import pit_store

fails: list[str] = []


def check(name: str, cond: bool, extra: object = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {extra}")
        fails.append(name)


#: A stable cache directory, so a re-run of the gate is fast and polite.
CACHE_DIR = os.path.join(tempfile.gettempdir(), "shafferfineval_pit_identity_cache")

LEH = "806085"
SIVB = "719739"
SHLD = "1310067"
BBBY = "886158"
ENRON = "1024401"
GM_OLD = "40730"
GM_NEW = "1467858"
BRK = "1067983"
FRC = "1132979"

#: Lehman's 2008-10-21 Form 25 description: a wall of structured notes that
#: happens to contain the phrase "Common Stock of General Electric Company".
LEHMAN_TRAP = (
    "Opta Exchange-Traded Notes due February 25, 2038 Linked to the Lehman "
    "Brothers Commodity Index Pure Beta Agricultural Total Return 0.00% Medium "
    "Term Notes, Series I, Due May 15, 2010 Performance Linked to the Common "
    "Stock of General Electric Company (GE) Dow Jones Global Titans 50 Index"
)


def section(title: str) -> None:
    print(f"\n== {title} ==")


# ==========================================================================
def main() -> int:
    db_dir = tempfile.mkdtemp(prefix="pit_identity_test_")
    db_path = os.path.join(db_dir, "pit_test.db")
    os.makedirs(CACHE_DIR, exist_ok=True)
    print(f"throwaway db: {db_path}")
    print(f"http cache  : {CACHE_DIR}")

    # ------------------------------------------------------------------
    section("0. the Form 25 security-class filter (offline)")
    # ------------------------------------------------------------------
    check("exact 'Common Stock' is common",
          PI.is_common_stock_class("Common Stock"))
    check("lower-case 'Common stock' is common",
          PI.is_common_stock_class("Common stock"))
    check("'Common Stock, $0.01 par value' is common",
          PI.is_common_stock_class("Common Stock, $0.01 par value"))
    check("SVB's 'Common stock and preferred stock' is common",
          PI.is_common_stock_class("Common stock and preferred stock"))
    check("Berkshire's senior note is NOT common",
          not PI.is_common_stock_class("0.000% Senior Notes due 2025"))
    check("new GM's Class B Warrants are NOT common",
          not PI.is_common_stock_class("Class B Warrants (Expiring July 10, 2019)"))
    check("depositary shares over preferred are NOT common",
          not PI.is_common_stock_class(
              "Depositary Shares, (Each Representing One-Tenth Interest in a "
              "Share of 5.94% Cumulative Preferred Stock, Series C)"))
    check("Lehman's notes-wall mentioning GE common stock is NOT common",
          not PI.is_common_stock_class(LEHMAN_TRAP))
    check("empty description is NOT common", not PI.is_common_stock_class(""))
    check("None description is NOT common", not PI.is_common_stock_class(None))
    check("CIK normalises to one spelling",
          PI.cik10("40730") == PI.cik10("0000040730") == "0000040730")

    # ------------------------------------------------------------------
    section("0b. a first trade before 1970 is a date, not a crash (offline)")
    # ------------------------------------------------------------------
    # DEFECT #24. `_exchange_date` used to call `datetime.fromtimestamp`, and
    # on Windows that raises OSError [Errno 22] for a sufficiently negative
    # timestamp. Yahoo dates the pre-CRSP NYSE roll at epoch -252322200, so the
    # oldest listings in the store -- Boeing, GE, IBM, Coca-Cola, Exxon, 3M --
    # each crashed whatever called this. The epochs below were read from live
    # Yahoo `chart.meta` on 2026-09-21; -236260800 is the value recorded off the
    # ingest that actually died, kept because a regression test for a crash
    # should carry the input that caused it.
    NY = -14400          # America/New_York, as Yahoo reports it in `gmtoffset`
    for epoch, offset, expect, who in (
            (-252322200, NY, "1962-01-02", "Boeing / GE / IBM / KO / XOM / MMM"),
            (-236260800, NY, "1962-07-07", "the epoch the failed ingest saw"),
            (-1, 0, "1969-12-31", "one second before the epoch"),
            (0, 0, "1970-01-01", "the epoch itself"),
            (76253400, NY, "1972-06-01", "Ford, the post-1970 control"),
    ):
        got = PI._exchange_date(epoch, offset)
        check(f"epoch {epoch} dates to {expect}  ({who})", got == expect, got)

    check("a pre-1970 epoch survives a missing gmtoffset",
          PI._exchange_date(-252322200, None) == "1962-01-02",
          PI._exchange_date(-252322200, None))
    check("a pre-1970 epoch survives an unparseable gmtoffset",
          PI._exchange_date(-252322200, "eastern") == "1962-01-02",
          PI._exchange_date(-252322200, "eastern"))

    # The exchange's own calendar, not London's: 1962-01-01T02:00Z is still
    # 1961-12-31 in New York, and the offset is what decides that.
    check("the exchange timezone still moves the day across midnight",
          PI._exchange_date(-252453600, NY) == "1961-12-31"
          and PI._exchange_date(-252453600, 0) == "1962-01-01",
          (PI._exchange_date(-252453600, NY), PI._exchange_date(-252453600, 0)))
    check("a positive epoch is still moved across midnight by the offset",
          PI._exchange_date(1719540000, NY) == "2024-06-27"
          and PI._exchange_date(1719540000, 0) == "2024-06-28",
          (PI._exchange_date(1719540000, NY), PI._exchange_date(1719540000, 0)))

    # The fix must not have changed a single answer in the range the old
    # implementation could handle. This IS the old implementation, inline.
    def _old_exchange_date(epoch, gmtoffset):
        seconds = int(epoch)
        try:
            offset = int(gmtoffset or 0)
        except (TypeError, ValueError):
            offset = 0
        return (_dt.datetime.fromtimestamp(seconds, _dt.timezone.utc)
                + _dt.timedelta(seconds=offset)).date().isoformat()

    drift = []
    for epoch in (0, 1, 86399, 86400, 76253400, 1435622400, 1561939200,
                  1719532800, 1767225600, 2147483647):
        for offset in (0, NY, -18000, 3600, 32400):
            if _old_exchange_date(epoch, offset) != PI._exchange_date(epoch, offset):
                drift.append((epoch, offset))
    check("every positive epoch is unchanged from the old implementation",
          not drift, drift)

    # The measured boundary of the old call on this machine: fromtimestamp
    # converted -43200 and raised OSError on -43201. Both are ordinary dates.
    check("the exact epoch the old call choked on is now a date",
          PI._exchange_date(-43201, 0) == "1969-12-31"
          and PI._exchange_date(-43200, 0) == "1969-12-31",
          (PI._exchange_date(-43201, 0), PI._exchange_date(-43200, 0)))

    check("a non-numeric epoch is still None, not a guess",
          PI._exchange_date(None, 0) is None
          and PI._exchange_date("", 0) is None
          and PI._exchange_date("1962-01-02", 0) is None)

    # ------------------------------------------------------------------
    section("1. (a) the cohort is ingested end to end")
    # ------------------------------------------------------------------
    conn = pit_store.init_db(db_path)
    cohort = {"LEH": LEH, "SIVB": SIVB, "SHLD": SHLD, "BBBY": BBBY,
              "ENRON": ENRON, "GM_OLD": GM_OLD, "GM_NEW": GM_NEW,
              "BRK": BRK, "FRC": FRC}
    reports: dict[str, dict] = {}
    for label, cik in cohort.items():
        reports[label] = PI.ingest_entity(conn, cik, cache_dir=CACHE_DIR)

    for label in ("LEH", "SIVB", "SHLD", "BBBY", "ENRON", "GM_OLD", "GM_NEW"):
        report = reports[label]
        entity_id = pit_store.entity_id_for_cik(conn, cohort[label])
        names = conn.execute(
            "SELECT COUNT(*) AS n FROM pit_entity_name WHERE entity_id = ?",
            (entity_id,)).fetchone()["n"]
        check(f"{label} has an entity_id", entity_id is not None)
        check(f"{label} reports filings", report.get("n_filings", 0) > 0,
              report.get("n_filings"))
        check(f"{label} has a dated name", names > 0, names)

    check("none of the cohort is in today's live universe by ticker",
          all(not (reports[label].get("current_snapshot_hint") or {}).get("tickers")
              for label in ("LEH", "SIVB", "SHLD", "BBBY", "ENRON", "GM_OLD")),
          {label: reports[label]["current_snapshot_hint"]["tickers"]
           for label in ("LEH", "SIVB", "SHLD", "BBBY", "ENRON", "GM_OLD")})

    listing_count = conn.execute(
        "SELECT COUNT(*) AS n FROM pit_listing").fetchone()["n"]
    check("ingest writes NO listing rows from the current ticker snapshot",
          listing_count == 0, listing_count)

    # ------------------------------------------------------------------
    section("2. (b) old GM and new GM are two entities, not one ticker")
    # ------------------------------------------------------------------
    gm_old = pit_store.entity_id_for_cik(conn, GM_OLD)
    gm_new = pit_store.entity_id_for_cik(conn, GM_NEW)
    check("old GM resolved", gm_old is not None)
    check("new GM resolved", gm_new is not None)
    check("old GM and new GM are DISTINCT entity_ids", gm_old != gm_new,
          (gm_old, gm_new))
    check("their CIKs differ", PI.cik10(GM_OLD) != PI.cik10(GM_NEW))
    check("old GM was named GENERAL MOTORS CORP in 2008",
          (PI.name_as_of(conn, gm_old, "2008-06-30") or "").upper()
          == "GENERAL MOTORS CORP",
          PI.name_as_of(conn, gm_old, "2008-06-30"))
    check("old GM is Motors Liquidation today",
          "MOTORS LIQUIDATION" in (PI.name_as_of(conn, gm_old, "2024-06-28") or "").upper(),
          PI.name_as_of(conn, gm_old, "2024-06-28"))
    check("new GM has no 2008 name -- it did not exist",
          PI.name_as_of(conn, gm_new, "2008-06-30") is None,
          PI.name_as_of(conn, gm_new, "2008-06-30"))

    # ------------------------------------------------------------------
    section("3. (c) BBBY survives a renamed, tickerless CIK")
    # ------------------------------------------------------------------
    bbby = pit_store.entity_id_for_cik(conn, BBBY)
    hint = reports["BBBY"]["current_snapshot_hint"]
    check("BBBY's entity exists", bbby is not None)
    check("its CIK is now named 20230930-DK-Butterfly-1",
          "BUTTERFLY" in (hint.get("name") or "").upper(), hint.get("name"))
    check("its current tickers array is empty -- ticker lookup finds nothing",
          hint.get("tickers") == [], hint.get("tickers"))
    check("name-as-of 2015 is still BED BATH & BEYOND",
          "BED BATH" in (PI.name_as_of(conn, bbby, "2015-06-30") or "").upper(),
          PI.name_as_of(conn, bbby, "2015-06-30"))
    check("the current name is stored too, dated from the rename",
          conn.execute(
              """SELECT COUNT(*) AS n FROM pit_entity_name
                  WHERE entity_id = ? AND valid_to IS NULL""",
              (bbby,)).fetchone()["n"] == 1)
    check("only the CIK connects the two names",
          PI.name_as_of(conn, bbby, "2015-06-30")
          != PI.name_as_of(conn, bbby, "2024-06-28"))

    # ------------------------------------------------------------------
    section("4. (g) SIC is read per filing, not from the current record")
    # ------------------------------------------------------------------
    enron = pit_store.entity_id_for_cik(conn, ENRON)
    enron_1999 = pit_store.sic_as_of(conn, enron, "1999-12-31")
    enron_now = reports["ENRON"]["current_snapshot_hint"]["sic"]
    check("Enron has a historical SIC at 1999-12-31", enron_1999 is not None,
          enron_1999)
    check("and it DIFFERS from the current record",
          enron_1999 is not None and str(enron_1999) != str(enron_now),
          f"historical {enron_1999} vs current {enron_now}")
    check("Enron's 1999 SIC is the wholesale-petroleum code 5172",
          enron_1999 == "5172", enron_1999)
    check("no SIC before the first filing",
          pit_store.sic_as_of(conn, enron, "1990-01-01") is None)
    check("SVB's SIC is stable at 6022 across its life",
          pit_store.sic_as_of(conn, pit_store.entity_id_for_cik(conn, SIVB),
                              "2010-06-30") == "6022",
          pit_store.sic_as_of(conn, pit_store.entity_id_for_cik(conn, SIVB),
                              "2010-06-30"))

    # ------------------------------------------------------------------
    section("5. (d)(e) exit dating: earliest defensible, or quarantined")
    # ------------------------------------------------------------------
    exits = {label: PI.exit_date_for(conn, cik, cache_dir=CACHE_DIR)
             for label, cik in cohort.items()}

    sears = exits["SHLD"]
    check("Sears has an exit date", sears["exit_date"] is not None, sears["exit_date"])
    check("Sears' exit is in the bankruptcy window",
          sears["exit_date"] is not None
          and "2018-01-01" <= sears["exit_date"] <= "2019-06-30",
          sears["exit_date"])
    check("Sears records WHICH source won",
          sears["exit_date_source"] in (PI.EXIT_SOURCE_8K, PI.EXIT_SOURCE_FORM_25,
                                        PI.EXIT_SOURCE_LAST_BAR),
          sears["exit_date_source"])
    check("Sears' exit is EARLIER than its Form 25 -- the form lags the trade",
          sears["exit_date"] <= sears["candidates"].get(PI.EXIT_SOURCE_FORM_25, "9999"),
          sears["candidates"])

    bb = exits["BBBY"]
    check("BBBY has an exit date", bb["exit_date"] is not None, bb["exit_date"])
    check("BBBY's exit is in 2023",
          bb["exit_date"] is not None and bb["exit_date"].startswith("2023"),
          bb["exit_date"])
    check("BBBY records WHICH source won", bb["exit_date_source"] is not None,
          bb["exit_date_source"])
    check("BBBY's exit precedes its 2023-07-10 Form 25 by weeks",
          bb["candidates"].get(PI.EXIT_SOURCE_FORM_25) == "2023-07-10"
          and bb["exit_date"] < "2023-07-10", bb["candidates"])

    en = exits["ENRON"]
    check("Enron gets NO fabricated exit date", en["exit_date"] is None,
          en["exit_date"])
    check("Enron is quarantined with no_exit_record = 1",
          en["no_exit_record"] == 1, en)
    check("Enron's quarantine is persisted",
          (conn.execute("SELECT no_exit_record FROM pit_entity_exit WHERE entity_id = ?",
                        (enron,)).fetchone() or {"no_exit_record": None})["no_exit_record"] == 1)
    check("Enron filed neither a Form 25 nor a Form 15",
          en["dereg_date"] is None and not en["candidates"], en)

    brk = exits["BRK"]
    brk_id = pit_store.entity_id_for_cik(conn, BRK)
    brk_row = conn.execute(
        "SELECT * FROM pit_entity_exit WHERE entity_id = ?", (brk_id,)).fetchone()
    check("Berkshire is NOT marked delisted", brk["exit_date"] is None, brk)
    check("Berkshire is NOT quarantined either", brk["no_exit_record"] == 0, brk)
    check("Berkshire has no persisted exit row at all",
          brk_row is None or (brk_row["exit_date"] is None
                              and brk_row["no_exit_record"] == 0),
          dict(brk_row) if brk_row else None)
    check("Berkshire's Form 25 filings were examined and all rejected",
          brk["form_25_examined"] >= 1
          and PI.EXIT_SOURCE_FORM_25 not in brk["candidates"],
          (brk["form_25_examined"], brk["candidates"]))
    check("Berkshire's Item 3.01 notices did NOT create an exit",
          PI.EXIT_SOURCE_8K not in brk["candidates"], brk["candidates"])

    gmn = exits["GM_NEW"]
    check("new GM is NOT delisted by its Class B Warrants Form 25",
          gmn["exit_date"] is None and gmn["no_exit_record"] == 0, gmn)

    lehman = exits["LEH"]
    check("Lehman exits in 2008, not 2002",
          lehman["exit_date"] is not None and lehman["exit_date"].startswith("2008"),
          lehman["exit_date"])
    check("Lehman's Form 15 (2012) is recorded but is NOT the exit date",
          lehman["dereg_date"] is not None
          and lehman["dereg_date"] > (lehman["exit_date"] or ""),
          (lehman["dereg_date"], lehman["exit_date"]))

    gmo = exits["GM_OLD"]
    check("old GM exits in 2009 despite an estate that filed to 2021",
          gmo["exit_date"] is not None and gmo["exit_date"].startswith("2009"),
          (gmo["exit_date"], gmo["last_periodic_filing"]))

    svb = exits["SIVB"]
    check("SVB exits in 2023",
          svb["exit_date"] is not None and svb["exit_date"].startswith("2023"),
          svb["exit_date"])

    for label in ("LEH", "SIVB", "SHLD", "BBBY", "GM_OLD"):
        row = conn.execute(
            "SELECT * FROM pit_entity_exit WHERE entity_id = ?",
            (pit_store.entity_id_for_cik(conn, cohort[label]),)).fetchone()
        check(f"{label}'s exit_date_source is persisted",
              row is not None and row["exit_date_source"] is not None,
              dict(row) if row else None)

    # ------------------------------------------------------------------
    section("6. (f) First Republic is a KNOWN HOLE, recorded not skipped")
    # ------------------------------------------------------------------
    frc_report = reports["FRC"]
    frc_id = pit_store.entity_id_for_cik(conn, FRC)
    check("First Republic still gets an entity -- it is not skipped",
          frc_id is not None)
    check("it is recorded as having no periodic filings",
          "no_periodic_filings" in frc_report["known_holes"],
          frc_report["known_holes"])
    check("its 10-K/10-Q count really is zero",
          frc_report["n_periodic_filings"] == 0,
          frc_report["n_periodic_filings"])
    check("companyfacts was probed and 404s",
          frc_report.get("companyfacts_status") == 404,
          frc_report.get("companyfacts_status"))
    check("the 404 is recorded as a hole",
          "companyfacts_404" in frc_report["known_holes"],
          frc_report["known_holes"])
    ingest_row = conn.execute(
        "SELECT * FROM pit_ingest_run WHERE ingest_id = ?",
        (frc_report["ingest_id"],)).fetchone()
    persisted = pit_store.loads(ingest_row["summary_json"]) if ingest_row else None
    check("the hole is PERSISTED in pit_ingest_run, not just returned",
          persisted is not None and "no_periodic_filings" in persisted["known_holes"],
          persisted["known_holes"] if persisted else None)
    frc_exit = exits["FRC"]
    check("First Republic is quarantined rather than dated",
          frc_exit["no_exit_record"] == 1 and frc_exit["exit_date"] is None,
          frc_exit)
    check("its quarantine note names the reason",
          "KNOWN HOLE" in (frc_exit["notes"] or ""), frc_exit["notes"])
    check("a control entity has NO known holes",
          reports["BBBY"]["known_holes"] == [], reports["BBBY"]["known_holes"])

    # ------------------------------------------------------------------
    section("7. (h) the listing gate rejects the reused BBBY ticker")
    # ------------------------------------------------------------------
    reused = PI.resolve_listing(conn, "BBBY", bbby, "2015-06-30", cache_dir=CACHE_DIR)
    check("Yahoo answers HTTP 200 for BBBY -- the trap looks healthy",
          reused["http_status"] == 200, reused["http_status"])
    check("and the name still matches the dead retailer",
          "BED BATH" in (reused["long_name"] or "").upper(), reused["long_name"])
    check("and the exchange matches too",
          (reused["exchange"] or "").upper().startswith("NYSE"), reused["exchange"])
    check("but the gate REJECTS it for a 2015 as-of", not reused["accepted"], reused)
    check("rejected specifically on firstTradeDate",
          reused["reason"] == PI.REJECT_FIRST_TRADE_AFTER, reused["reason"])
    check("its first trade date is in 2026",
          (reused["first_trade_date"] or "") >= "2026-01-01",
          reused["first_trade_date"])
    check("no listing row was written for the rejected symbol",
          conn.execute("SELECT COUNT(*) AS n FROM pit_listing WHERE symbol = 'BBBY'"
                       ).fetchone()["n"] == 0)

    sears_id = pit_store.entity_id_for_cik(conn, SHLD)
    reused_etf = PI.resolve_listing(conn, "SHLD", sears_id, "2024-06-28",
                                    cache_dir=CACHE_DIR, persist=False)
    check("SHLD today is an ETF, and the gate says so",
          not reused_etf["accepted"]
          and reused_etf["reason"] == PI.REJECT_INSTRUMENT_TYPE,
          (reused_etf["reason"], reused_etf["instrument_type"],
           reused_etf["long_name"]))

    gone = PI.resolve_listing(conn, "LEHMQ", pit_store.entity_id_for_cik(conn, LEH),
                              "2008-06-30", cache_dir=CACHE_DIR, persist=False)
    check("a truly dead symbol is 'unverified', never invented",
          not gone["accepted"] and gone["confidence"] == PI.CONFIDENCE_UNVERIFIED
          and gone["reason"] == PI.REJECT_NO_YAHOO_DATA, gone)

    good = PI.resolve_listing(conn, "GM", gm_new, "2015-06-30", cache_dir=CACHE_DIR)
    check("a genuine listing passes the gate", good["accepted"], good)
    check("GM's first trade date is the 2010 IPO",
          good["first_trade_date"] == "2010-11-18", good["first_trade_date"])
    check("confidence is 'inferred', never 'proved', without a dated map",
          good["confidence"] == PI.CONFIDENCE_INFERRED, good["confidence"])
    check("the accepted listing is persisted", good["listing_id"] is not None)
    check("daily granularity was asserted", good["granularity"] == "1d",
          good["granularity"])
    too_early = PI.resolve_listing(conn, "GM", gm_new, "2009-06-30",
                                   cache_dir=CACHE_DIR, persist=False)
    check("the same symbol is rejected BEFORE its first trade",
          not too_early["accepted"]
          and too_early["reason"] == PI.REJECT_FIRST_TRADE_AFTER, too_early)
    proved = PI.resolve_listing(conn, "GM", gm_new, "2015-06-30",
                                cache_dir=CACHE_DIR, persist=False,
                                proof="dei:TradingSymbol on the filed cover page")
    check("only an explicit dated proof raises confidence to 'proved'",
          proved["confidence"] == PI.CONFIDENCE_PROVED, proved["confidence"])

    # ------------------------------------------------------------------
    section("8. the two universes, CIK-keyed and survivorship-free")
    # ------------------------------------------------------------------
    peers_2007 = peer_ciks(conn, "2007-12-31")
    check("Lehman is a peer at 2007-12-31 -- a company that no longer exists",
          PI.cik10(LEH) in peers_2007, len(peers_2007))
    check("new GM is NOT a peer in 2007 -- it had not been formed",
          PI.cik10(GM_NEW) not in peers_2007)
    check("old GM IS a peer in 2007", PI.cik10(GM_OLD) in peers_2007)

    peers_2015 = peer_ciks(conn, "2015-06-30")
    check("BBBY and Sears are peers in 2015",
          PI.cik10(BBBY) in peers_2015 and PI.cik10(SHLD) in peers_2015)
    check("Lehman has aged out of the 2015 peer set",
          PI.cik10(LEH) not in peers_2015)
    check("both GMs can coexist in the store without colliding",
          PI.cik10(GM_NEW) in peers_2015)

    peers_2001 = peer_ciks(conn, "2001-06-30")
    check("Enron IS a 2001 peer despite being quarantined -- dropping it "
          "would BE survivorship bias", PI.cik10(ENRON) in peers_2001)
    check("Enron is not a 2015 peer -- the staleness bound retires it",
          PI.cik10(ENRON) not in peer_ciks(conn, "2015-06-30"))

    peers_2024 = peer_ciks(conn, "2024-06-28")
    check("BBBY has left the peer set after its 2023 exit",
          PI.cik10(BBBY) not in peers_2024)
    check("Sears has left the peer set after its 2018 exit",
          PI.cik10(SHLD) not in peers_2024)
    check("Berkshire and new GM are still peers in 2024",
          PI.cik10(BRK) in peers_2024 and PI.cik10(GM_NEW) in peers_2024)

    rows = PI.peer_universe_as_of(conn, "2015-06-30")
    check("peer membership needs no ticker at all",
          all(row["cik"] and row["sic"] for row in rows),
          [(r["cik"], r["sic"]) for r in rows])
    drift = [(row["cik"], row["sic"],
              pit_store.sic_as_of(conn, row["entity_id"], "2015-06-30"))
             for row in rows
             if row["sic"] != pit_store.sic_as_of(conn, row["entity_id"], "2015-06-30")]
    check("the peer query's SIC agrees with pit_store.sic_as_of, row for row",
          not drift, drift)

    check("nothing is scoreable before prices are loaded",
          PI.scored_universe_as_of(conn, "2015-06-30") == [])

    # A synthetic bar, so the priced half of the gate is exercised now rather
    # than discovered broken in the price phase. Throwaway DB only.
    gm_listing = good["listing_id"]
    with pit_store.transaction(conn):
        conn.executemany(
            """INSERT OR IGNORE INTO pit_price_bar
                   (listing_id, bar_date, close, adjclose, volume,
                    source_symbol, ingest_id)
               VALUES (?, ?, 33.0, 33.0, 1000000, 'GM', 1)""",
            [(gm_listing, "2015-06-25"), (gm_listing, "2015-06-26"),
             (gm_listing, "2015-06-29"), (gm_listing, "2015-06-30")])
    scored = PI.scored_universe_as_of(conn, "2015-06-30")
    check("a priced, gated listing reaches the scored universe",
          [row["cik"] for row in scored] == [PI.cik10(GM_NEW)],
          [(r["cik"], r["symbol"]) for r in scored])
    check("the scored row carries the listing, not just the issuer",
          scored and scored[0]["listing_id"] == gm_listing)
    check("stale prices do not qualify",
          PI.scored_universe_as_of(conn, "2015-12-31") == [],
          [dict(r) for r in PI.scored_universe_as_of(conn, "2015-12-31")])

    # A quarantined entity with a listing AND prices must still be excluded.
    enron_listing = pit_store.upsert_listing(
        conn, "ENE-TEST", "1990-01-01", entity_id=enron, exchange="NYSE",
        instrument_type="EQUITY", currency="USD", valid_from="1990-01-01",
        confidence=PI.CONFIDENCE_INFERRED, source="test:fixture")
    with pit_store.transaction(conn):
        conn.execute(
            """INSERT OR IGNORE INTO pit_price_bar
                   (listing_id, bar_date, close, adjclose, volume,
                    source_symbol, ingest_id)
               VALUES (?, '2001-06-29', 49.0, 49.0, 5000000, 'ENE-TEST', 1)""",
            (enron_listing,))
    scored_2001 = [row["cik"] for row in PI.scored_universe_as_of(conn, "2001-06-30")]
    check("a quarantined issuer is a PEER but never SCORED",
          PI.cik10(ENRON) in peer_ciks(conn, "2001-06-30")
          and PI.cik10(ENRON) not in scored_2001, scored_2001)

    # ------------------------------------------------------------------
    section("9. idempotency and production isolation")
    # ------------------------------------------------------------------
    before = store_counts(conn)
    again = PI.ingest_entity(conn, BBBY, cache_dir=CACHE_DIR)
    after = store_counts(conn)
    check("re-ingest returns the same entity_id",
          again["entity_id"] == bbby, (again["entity_id"], bbby))
    check("re-ingest adds no name rows", before["pit_entity_name"] == after["pit_entity_name"],
          (before["pit_entity_name"], after["pit_entity_name"]))
    check("re-ingest adds no SIC rows", before["pit_entity_sic"] == after["pit_entity_sic"],
          (before["pit_entity_sic"], after["pit_entity_sic"]))
    check("re-ingest adds no entity rows", before["pit_entity"] == after["pit_entity"])
    again_exit = PI.exit_date_for(conn, BBBY, cache_dir=CACHE_DIR)
    check("re-dating an exit is stable", again_exit["exit_date"] == bb["exit_date"])

    tables = {row["name"] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    check("the PIT store holds no production tables",
          not ({"assets", "current_scores", "score_history", "positions"} & tables))
    check("research and production are separate files",
          os.path.abspath(pit_store.DEFAULT_PIT_DB_PATH)
          != os.path.abspath(db_path)
          and "pit" in os.path.basename(pit_store.DEFAULT_PIT_DB_PATH))
    check("this test wrote to neither live database",
          os.path.abspath(db_path).startswith(os.path.abspath(tempfile.gettempdir())))

    print()
    print(f"CHECKS: {len(fails)} failed")
    print("FAILURES:", len(fails), fails if fails else "")
    if fails:
        print("\nSTOP CONDITION: the survivorship gate did not pass. "
              "Bulk historical scoring must not begin.")
    else:
        print("\nGATE PASSED: the store can represent companies that no longer exist.")
    return 1 if fails else 0


def peer_ciks(conn: sqlite3.Connection, as_of: str) -> set[str]:
    return {row["cik"] for row in PI.peer_universe_as_of(conn, as_of)}


def store_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {table: conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            for table in ("pit_entity", "pit_entity_name", "pit_entity_sic",
                          "pit_listing", "pit_entity_exit")}


if __name__ == "__main__":
    sys.exit(main())
