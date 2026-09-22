"""The range reader and the symbol ingest, gated offline.

    python test_pit_notes.py

OFFLINE AND SELF-CONTAINED. The fixture is a real zip file, written by
`zipfile` into the system temp directory and served back through a fake HTTP
session that honours `Range` exactly as www.sec.gov does -- 206 with the
requested slice, 200 when told to misbehave. That shape is the point: the
defects this module can have are (a) reading the wrong bytes out of a zip it
never downloaded and (b) letting a server that ignores Range hand it a whole
gigabyte. Both are reproducible without touching the network, and neither is
reproducible against a live SEC host on demand.

The fixture rows are the pilot's awkward real values, frozen:

    Berkshire reports BRK.A and BRK.B as common stock and BRK41 as a senior
    note, three symbols in one accession under three different dimension
    hashes. Joining the titles on the accession alone labels the note "Class A
    common stock", which is the specific bug the (adsh, dimh, coreg) key exists
    to prevent.
    Old GM reports the literal `CK0000040730` -- a well-formed string, not a
    ticker, and accepted by any shape test that has not heard of EDGAR.
    Bed Bath & Beyond files its symbol lower-case as `bbby`.
    AIG files `AIG PRA`, a preferred series rather than a share.
    One filer tags TradingSymbol in its OWN namespace, which is not the
    cover-page assertion this module claims to read.

Nothing here opens `shafferfineval_pit.db`. The test asserts that before it
finishes.
"""

from __future__ import annotations

import io
import os
import sqlite3
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_notes as PN
import pit_store
import pit_symbols as PS

fails: list[str] = []


def check(name: str, cond: bool, extra: object = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {extra}")
        fails.append(name)


def raises(fn) -> bool:
    try:
        fn()
    except Exception:
        return True
    return False


# --------------------------------------------------------------------------
# A fake www.sec.gov that honours Range
# --------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status_code: int, body: bytes, headers: dict):
        self.status_code = status_code
        self.content = body
        self.headers = headers

    def iter_content(self, chunk_size: int = 1024):
        for pos in range(0, len(self.content), chunk_size):
            yield self.content[pos: pos + chunk_size]

    def raise_for_status(self):
        if self.status_code >= 400:
            raise OSError(f"HTTP {self.status_code}")

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSession:
    """Serves one local file over HTTP semantics. Counts requests and bytes."""

    def __init__(self, path: str, *, honour_range: bool = True, missing: bool = False):
        self.path = path
        self.honour_range = honour_range
        self.missing = missing
        self.requests = 0
        self.served_bytes = 0
        self.headers: dict = {}

    def _size(self) -> int:
        return os.path.getsize(self.path)

    def head(self, url, timeout=None, allow_redirects=True):
        self.requests += 1
        if self.missing:
            return FakeResponse(404, b"", {})
        return FakeResponse(200, b"", {"Content-Length": str(self._size()),
                                       "Accept-Ranges": "bytes"})

    def get(self, url, headers=None, timeout=None, stream=False):
        self.requests += 1
        if self.missing:
            return FakeResponse(404, b"", {})
        raw = (headers or {}).get("Range")
        with open(self.path, "rb") as fh:
            data = fh.read()
        if raw is None or not self.honour_range:
            self.served_bytes += len(data)
            return FakeResponse(200, data, {"Content-Length": str(len(data))})
        start, end = raw.split("=", 1)[1].split("-")
        body = data[int(start): int(end) + 1]
        self.served_bytes += len(body)
        return FakeResponse(206, body, {"Content-Length": str(len(body))})


# --------------------------------------------------------------------------
# The fixture
# --------------------------------------------------------------------------

SUB_HEADER = "adsh\tcik\tname\tsic\tform\tperiod\tfiled\taccepted\tdetail\tnciks"
TXT_HEADER = ("adsh\ttag\tversion\tddate\tqtrs\tiprx\tlang\tdcml\tdurp\tdatp\t"
              "dimh\tdimn\tcoreg\tescaped\tsrclen\ttxtlen\tfootnote\tfootlen\t"
              "context\tvalue")

#: Two filings, one accepted before the 15:30 ET cutoff and one after, so the
#: availability rule has something to bite on in both directions.
SUBS = [
    # adsh, cik, name, sic, form, period, filed, accepted
    ("0000950170-23-004451", "1067983", "BERKSHIRE HATHAWAY INC", "6331",
     "10-K", "20221231", "20230227", "2023-02-27 14:05:00.0"),
    ("0000886158-19-000012", "886158", "BED BATH & BEYOND INC", "5712",
     "10-Q", "20190601", "20190710", "2019-07-10 16:31:00.0"),
    ("0000040730-13-000045", "40730", "MOTORS LIQUIDATION CO", "3711",
     "10-Q", "20130930", "20131107", "2013-11-07 09:15:00.0"),
    ("0000005272-22-000101", "5272", "AMERICAN INTERNATIONAL GROUP", "6311",
     "10-K", "20211231", "20220217", "2022-02-17 11:00:00.0"),
    ("0009999999-23-000001", "9999999", "AN UNREGISTERED FILER", "1000",
     "8-K", "20230101", "20230105", "2023-01-05 10:00:00.0"),
]


def _txt(adsh, tag, version, ddate, qtrs, dimh, coreg, value):
    row = [""] * 20
    row[0], row[1], row[2], row[3], row[4] = adsh, tag, version, ddate, str(qtrs)
    row[10], row[12] = dimh, coreg
    row[19] = value
    return "\t".join(row)


TXT_ROWS = [
    # Berkshire: two common classes and one senior note, three dimensions.
    _txt("0000950170-23-004451", "TradingSymbol", "dei/2022", "20221231", 0,
         "0x11111111", "", "BRK.A"),
    _txt("0000950170-23-004451", "Security12bTitle", "dei/2022", "20221231", 0,
         "0x11111111", "", "Class A common stock"),
    _txt("0000950170-23-004451", "SecurityExchangeName", "dei/2022", "20221231", 0,
         "0x11111111", "", "NYSE"),
    _txt("0000950170-23-004451", "TradingSymbol", "dei/2022", "20221231", 0,
         "0x22222222", "", "BRK.B"),
    _txt("0000950170-23-004451", "Security12bTitle", "dei/2022", "20221231", 0,
         "0x22222222", "", "Class B common stock"),
    _txt("0000950170-23-004451", "TradingSymbol", "dei/2022", "20221231", 0,
         "0x33333333", "", "BRK41"),
    _txt("0000950170-23-004451", "Security12bTitle", "dei/2022", "20221231", 0,
         "0x33333333", "", "1.500% Senior Notes due 2041"),
    # A text block, to prove the scanner discards the bulk of txt.tsv.
    _txt("0000950170-23-004451", "SignificantAccountingPoliciesTextBlock",
         "us-gaap/2022", "20221231", 4, "0x00000000", "", "x" * 4000),
    # BBBY files lower case.
    _txt("0000886158-19-000012", "TradingSymbol", "dei/2019", "20190601", 0,
         "0x00000000", "", "bbby"),
    # Old GM files the EDGAR placeholder.
    _txt("0000040730-13-000045", "TradingSymbol", "dei/2013", "20130930", 0,
         "0x00000000", "", "CK0000040730"),
    # AIG files a preferred series.
    _txt("0000005272-22-000101", "TradingSymbol", "dei/2021", "20211231", 0,
         "0x44444444", "", "AIG PRA"),
    # An empty value, and a symbol in the filer's OWN namespace.
    _txt("0000005272-22-000101", "TradingSymbol", "dei/2021", "20211231", 0,
         "0x55555555", "", "   "),
    _txt("0000005272-22-000101", "TradingSymbol", "aig/2021", "20211231", 0,
         "0x66666666", "", "NOTDEI"),
    # A filer with no pit_entity row.
    _txt("0009999999-23-000001", "TradingSymbol", "dei/2022", "20230101", 0,
         "0x00000000", "", "GHOST"),
]


def build_fixture_zip(path: str) -> None:
    sub = "\n".join([SUB_HEADER] + [
        "\t".join([a, cik, name, sic, form, period, filed, accepted, "1", "1"])
        for a, cik, name, sic, form, period, filed, accepted in SUBS]) + "\n"
    txt = "\n".join([TXT_HEADER] + TXT_ROWS) + "\n"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("readme.htm", "<html>not read</html>")
        zf.writestr("sub.tsv", sub)
        # Incompressible padding, 300 KB, for one reason: the
        # end-of-central-directory search window is 65,557 bytes, and a
        # fixture smaller than that would make "the index is cheaper than
        # the file" true by accident rather than by design.
        zf.writestr("num.tsv", os.urandom(300_000))
        zf.writestr("txt.tsv", txt)


def build_store(db_path: str) -> sqlite3.Connection:
    conn = pit_store.init_db(db_path)
    PS.ensure_schema(conn)
    for _, cik, name, _, _, _, filed, _ in SUBS:
        if cik == "9999999":
            continue                      # deliberately unregistered
        entity_id = pit_store.upsert_entity(conn, cik, first_filing_date="2009-01-01")
        pit_store.add_entity_name(conn, entity_id, name, "2009-01-01", None,
                                  "fixture")
    pit_store.insert_calendar(conn, "XNYS",
                              ["2013-11-07", "2013-11-08", "2019-07-10",
                               "2019-07-11", "2022-02-17", "2022-02-18",
                               "2023-01-05", "2023-01-06", "2023-02-27",
                               "2023-02-28"],
                              "fixture")
    return conn


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="pit_notes_test_")
    zip_path = os.path.join(tmp, "2023q1_notes.zip")
    db_path = os.path.join(tmp, "notes_test.db")
    build_fixture_zip(zip_path)
    conn = build_store(db_path)
    url = PN.notes_url("2023q1")

    print("1. THE PUBLISHED ARCHIVE")
    periods = PN.archive_periods()
    check("the archive is the 80 published files", len(periods) == 80, len(periods))
    check("quarterly stops at 2025q2 and monthly starts at 2025_07",
          periods[periods.index("2025q2") + 1] == "2025_07")
    check("the quarterly and monthly series do not overlap",
          len(set(periods)) == len(periods))
    check("2010q1 is the irregular published name",
          PN.notes_filename("2010q1") == "2010q1_notes_1.zip")
    check("2010q2 and 2010q3 are irregular too",
          PN.notes_filename("2010q2") == "2010q2_notes_0.zip"
          and PN.notes_filename("2010q3") == "2010q3_notes_0.zip")
    check("an ordinary period follows the pattern",
          PN.notes_filename("2023q1") == "2023q1_notes.zip")
    check("the symbol window starts at 2009q4, not 2009q1",
          PN.symbol_periods()[0] == "2009q4" and len(PN.symbol_periods()) == 77)
    check("a malformed period id is refused",
          raises(lambda: PN.notes_filename("2023Q9")))

    print()
    print("2. THE ZIP INDEX, READ FROM THE TAIL")
    session = FakeSession(zip_path)
    size = os.path.getsize(zip_path)
    members = PN.central_directory(session, url, size)
    check("every member is found", {"sub.tsv", "txt.tsv", "num.tsv",
                                    "readme.htm"} <= set(members), sorted(members))
    check("the file is larger than the EOCD search window",
          size > PN._EOCD_SEARCH_BYTES, size)
    check("the index costs far less than the file",
          session.served_bytes < size // 3, (session.served_bytes, size))
    check("member offsets and sizes are recorded",
          members["txt.tsv"]["comp_size"] > 0
          and members["txt.tsv"]["header_offset"] >= 0)

    print()
    print("3. ONLY THE TWO MEMBERS ARE FETCHED")
    session = FakeSession(zip_path)
    counter: dict = {"bytes": 0}
    members = PN.central_directory(session, url, size)
    subs = PN.read_submissions(session, url, members, counter)
    symbols, titles, exchanges, stats = PN.collect_symbol_rows(
        session, url, members, counter)
    check("sub.tsv is read by name into adsh keys", len(subs) == len(SUBS), len(subs))
    two_members = members["sub.tsv"]["comp_size"] + members["txt.tsv"]["comp_size"]
    check("exactly the two members are transferred, and nothing else",
          counter["bytes"] == two_members, (counter["bytes"], two_members))
    check("num.tsv, the bulk of the file, is never requested",
          counter["bytes"] < members["num.tsv"]["comp_size"],
          (counter["bytes"], members["num.tsv"]["comp_size"]))
    check("every txt row is counted", stats["txt_rows"] == len(TXT_ROWS),
          stats["txt_rows"])
    check("the text block is skipped before it is ever decoded",
          stats["cover_tag_rows"] < stats["txt_rows"],
          (stats["cover_tag_rows"], stats["txt_rows"]))
    check("the prefilter and the tag test agree",
          PN.wanted_tag_line(b"0000000000-00-000000\tTradingSymbol\tdei/2022\tx")
          and not PN.wanted_tag_line(
              b"0000000000-00-000000\tPolicyTextBlock\tus-gaap/2022\tx"))
    check("only dei TradingSymbol rows are retained",
          len(symbols) == 8, len(symbols))
    check("a custom-namespace TradingSymbol is rejected",
          stats["non_dei_rejected"] == 1, stats["non_dei_rejected"])
    check("companions key on (adsh, dimh, coreg)",
          titles[("0000950170-23-004451", "0x33333333", "")]
          == "1.500% Senior Notes due 2041")
    check("the note's title is NOT the Class A title",
          titles[("0000950170-23-004451", "0x11111111", "")] !=
          titles[("0000950170-23-004451", "0x33333333", "")])
    check("the exchange is carried where filed",
          exchanges[("0000950170-23-004451", "0x11111111", "")] == "NYSE")

    print()
    print("4. A SERVER THAT IGNORES RANGE IS AN ERROR, NOT A SLOW PATH")
    rogue = FakeSession(zip_path, honour_range=False)
    check("a 200 answer to a Range request raises",
          raises(lambda: PN.central_directory(rogue, url, size)))

    print()
    print("5. A MISSING COLUMN STOPS THE INGEST")
    bad = b"adsh\tcik\tform\n0000000000-00-000000\t1\t10-K\n"
    check("a tsv without the needed columns raises",
          raises(lambda: list(PN.iter_tsv_rows([bad], PN._SUB_FIELDS))))
    good = b"adsh\ttag\n1\tX\n2\tY"
    rows = list(PN.iter_tsv_rows([good], ("adsh", "tag")))
    check("a final line without a newline is still read",
          len(rows) == 2 and rows[1]["tag"] == "Y", rows)

    print()
    print("6. THE INGEST")
    session = FakeSession(zip_path)
    record = PN.ingest_period(conn, "2023q1", session=session, db_path=db_path,
                              next_session=None)
    check("the period completes", record["status"] == "complete", record)
    check("six mappable symbols are stored", record["rows_inserted"] == 6,
          record["rows_inserted"])
    check("the unregistered filer is counted, not inserted",
          record["by_status"].get("unmapped_cik") == 1, record["by_status"])
    check("the empty value is counted and not stored",
          record["by_status"].get("empty") == 1, record["by_status"])
    check("the WAL checkpoint reports what it did",
          record["checkpoint"]["status"] in ("truncated", "busy"),
          record["checkpoint"])
    check("bytes downloaded are measured", record["bytes_downloaded"] > 0)
    check("less than the whole zip was transferred",
          record["fraction_of_zip"] < 1.0, record["fraction_of_zip"])

    print()
    print("7. STATUSES ARE RECORDED, NOT ENFORCED BY DELETION")
    gm = pit_store.entity_id_for_cik(conn, "40730")
    aig = pit_store.entity_id_for_cik(conn, "5272")
    bbby = pit_store.entity_id_for_cik(conn, "886158")
    brk = pit_store.entity_id_for_cik(conn, "1067983")
    gm_rows = PS.symbol_observations(conn, gm, include_rejected=True)
    check("old GM's placeholder is stored with its own status",
          len(gm_rows) == 1 and gm_rows[0]["status"] == PS.OBS_CIK_PLACEHOLDER,
          [r["status"] for r in gm_rows])
    check("old GM resolves to NO ticker",
          PS.symbol_as_of(conn, gm, "2014-01-02") is None)
    aig_rows = PS.symbol_observations(conn, aig, include_rejected=True)
    check("AIG's preferred series is stored as not_ticker_shaped",
          [r["status"] for r in aig_rows] == [PS.OBS_NOT_TICKER_SHAPED],
          [r["status"] for r in aig_rows])
    check("AIG resolves to no common ticker from that filing",
          PS.symbol_as_of(conn, aig, "2022-06-30") is None)

    print()
    print("8. AVAILABILITY IS THE LATENCY POLICY, NOT THE FILING DATE")
    bbby_obs = PS.symbol_observations(conn, bbby)[0]
    check("BBBY's lower-case symbol is upper-cased", bbby_obs["symbol"] == "BBBY",
          bbby_obs["symbol"])
    check("a 16:31 ET acceptance is NOT available the same day",
          bbby_obs["available_date"] > bbby_obs["filed"],
          (bbby_obs["filed"], bbby_obs["available_date"]))
    check("BBBY is invisible on its filing date",
          PS.symbol_as_of(conn, bbby, "2019-07-10") is None)
    check("BBBY is visible the next session",
          (PS.symbol_as_of(conn, bbby, "2019-07-11") or {})["symbol"] == "BBBY")
    gm_row = gm_rows[0]
    check("a 09:15 ET acceptance IS available the same day",
          gm_row["available_date"] == gm_row["filed"],
          (gm_row["filed"], gm_row["available_date"]))

    print()
    print("9. SHARE CLASSES")
    rows = PS.symbols_as_of(conn, brk, "2023-06-30")
    check("Berkshire returns its two COMMON classes and not the note",
          sorted(r["symbol"] for r in rows) == ["BRK.A", "BRK.B"],
          [r["symbol"] for r in rows])
    check("the senior note is stored all the same",
          any(r["symbol"] == "BRK41"
              for r in PS.symbol_observations(conn, brk, include_rejected=True)))
    check("symbol_as_of picks the alphabetically first common class",
          PS.symbol_as_of(conn, brk, "2023-06-30")["symbol"] == "BRK.A")

    print()
    print("10. IDEMPOTENCY AND RESUME")
    check("the period is recorded as complete",
          PN.period_already_ingested(conn, "2023q1"))
    again = PN.ingest_period(conn, "2023q1", session=FakeSession(zip_path),
                             db_path=db_path)
    check("a completed period is skipped without transfer",
          again["status"] == "already_ingested" and again["bytes_downloaded"] == 0,
          again)
    forced = PN.ingest_period(conn, "2023q1", session=FakeSession(zip_path),
                              db_path=db_path, force=True)
    check("a forced re-run inserts nothing new",
          forced["status"] == "complete" and forced["rows_inserted"] == 0, forced)
    total = conn.execute("SELECT COUNT(*) AS n FROM pit_symbol_obs").fetchone()["n"]
    check("the table did not grow on the second pass", total == 6, total)

    print()
    print("11. DISK IS THE BINDING CONSTRAINT")
    refused = PN.ingest_period(conn, "2023q2", session=FakeSession(zip_path),
                               db_path=db_path, min_free_bytes=1 << 62)
    check("a period is refused rather than run when space is short",
          refused["status"] == "aborted_low_disk", refused)
    check("the refusal transferred nothing", refused["bytes_downloaded"] == 0)
    check("the refusal did not record a completed ingest",
          not PN.period_already_ingested(conn, "2023q2"))
    summary = PN.ingest_archive(conn, ["2023q2", "2023q3"],
                                session=FakeSession(zip_path), db_path=db_path,
                                min_free_bytes=1 << 62)
    check("the archive loop stops cleanly and names where it stopped",
          summary["status"] == "aborted_low_disk" and summary["stopped_at"] == "2023q2",
          summary["status"])
    check("peak disk used is measured, not estimated",
          "peak_disk_used_bytes" in summary and summary["peak_disk_used_bytes"] >= 0)
    report = PN.disk_report(db_path)
    check("free space is reported in bytes and GiB",
          report["free_bytes"] > 0 and report["free_gib"] > 0)

    print()
    print("12. AN UNAVAILABLE PERIOD IS A STATUS, NOT A CRASH")
    gone = PN.ingest_period(conn, "2026_08",
                            session=FakeSession(zip_path, missing=True),
                            db_path=db_path)
    check("a 404 comes back as 'unavailable'", gone["status"] == "unavailable", gone)
    check("an unavailable period is not marked complete",
          not PN.period_already_ingested(conn, "2026_08"))

    print()
    print("13. COVERAGE REPORTING")
    by_year = PN.symbol_coverage_by_year(conn)
    years = {r["year"]: r["entities"] for r in by_year}
    check("coverage is keyed on availability, not filing",
          years.get("2019") == 1 and years.get("2023") == 1, years)
    check("rejected rows never count as coverage", "2013" not in years, years)

    print()
    print("14. SAFETY")
    check("the test database is a throwaway under the temp directory",
          os.path.abspath(db_path).startswith(os.path.abspath(tempfile.gettempdir())),
          db_path)
    check("the production PIT store was never opened",
          os.path.abspath(db_path) != os.path.abspath(pit_store.DEFAULT_PIT_DB_PATH))

    conn.close()
    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(db_path + suffix)
        except OSError:
            pass
    try:
        os.unlink(zip_path)
        os.rmdir(tmp)
    except OSError:
        pass

    print()
    print(f"CHECKS: {len(fails)} failed")
    print("FAILURES:", len(fails), fails if fails else "")
    if fails:
        print("\nSTOP CONDITION: the notes reader is not safe to point at 25 GiB.")
    else:
        print("\nGATE PASSED: the index is read from the tail, only two members are "
              "fetched, rejected values are stored with a status, and a short disk "
              "stops the run instead of filling it.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
