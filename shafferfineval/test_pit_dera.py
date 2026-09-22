"""Offline tests for the DERA ingest estimator and bulk path.

Pure stdlib, no network, ~40 KB of synthetic fixture:

    python test_pit_dera.py

The fixture is a hand-built tab-delimited DERA quarter -- a real 2021q2 ZIP is
86 MB and a test that downloads one is a test nobody runs. Every filter stage,
the latency policy, the month-end period key and the append-only restatement
path are exercised against it.

The checks that matter most are the ones a silent bug would otherwise hide:

* a DIMENSIONAL row reaching pit_fact. 47% of DERA rows are segment or
  co-registrant breakdowns, and one segment's revenue standing in for the
  company's corrupts a score with no error anywhere.
* an IFRS fact answering a us-gaap ladder rung. `ProfitLoss`, `Assets`,
  `GrossProfit` and `InterestExpense` are local names shared by both taxonomies
  and `pit_store.fact_as_of` does not filter on taxonomy.
* a RESTATEMENT overwriting its original instead of landing beside it, which
  would destroy the only thing this store exists for.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import zipfile
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_dera as D
import pit_policy as P
import pit_store

fails: list[str] = []


def check(name: str, cond: bool, extra: object = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {extra}")
        fails.append(name)


# --------------------------------------------------------------------------
# A synthetic DERA quarter. Real column headers, hand-written rows.
# --------------------------------------------------------------------------

SUB_COLUMNS = ("adsh cik name sic countryba stprba cityba zipba bas1 bas2 baph "
               "countryma stprma cityma zipma mas1 mas2 countryinc stprinc ein "
               "former changed afs wksi fye form period fy fp filed accepted "
               "prevrpt detail instance nciks aciks").split()

NUM_COLUMNS = ["adsh", "tag", "version", "ddate", "qtrs", "uom", "segments",
               "coreg", "value", "footnote"]

APPLE_CIK = "320193"
MSFT_CIK = "789019"
SHELL_CIK = "222222"

ADSH_10K = "0000320193-21-000105"
ADSH_10Q = "0000789019-21-000031"
ADSH_S1 = "0001111111-21-000001"
ADSH_10KA = "0000320193-22-000002"
ADSH_NOTIME = "0002222222-21-000009"

#: (adsh, cik, name, sic, fye, form, period, filed, accepted, prevrpt)
SUBMISSIONS = [
    # Accepted 17:00 ET on a FRIDAY -> past the 15:30 cutoff, and the next
    # calendar day is a Saturday, so the session calendar must carry it to
    # Monday 2021-11-01.
    (ADSH_10K, APPLE_CIK, "APPLE INC", "3571", "0930", "10-K", "20210930",
     "20211029", "2021-10-29 17:00:00.0", "1"),
    # Accepted 09:00 ET -> same session.
    (ADSH_10Q, MSFT_CIK, "MICROSOFT CORP", "7372", "0630", "10-Q", "20210331",
     "20210427", "2021-04-27 09:00:00.0", "0"),
    # A registration statement: excluded by PERIODIC_FORMS.
    (ADSH_S1, "1111111", "NEWCO INC", "7372", "1231", "S-1", "20210331",
     "20210501", "2021-05-01 10:00:00.0", "0"),
    # The amendment that restates Apple's revenue. A NEW observation.
    (ADSH_10KA, APPLE_CIK, "APPLE INC", "3571", "0930", "10-K/A", "20210930",
     "20220125", "2022-01-25 12:00:00.0", "0"),
    # No acceptance timestamp at all -> the pessimistic branch, next session
    # AFTER `filed`.
    (ADSH_NOTIME, SHELL_CIK, "QUIET CORP", "6770", "1231", "10-K", "20201231",
     "20210301", "", "0"),
]

#: (adsh, tag, version, ddate, qtrs, uom, segments, coreg, value)
NUM_ROWS = [
    # --- kept ---
    (ADSH_10K, "Revenues", "us-gaap/2021", "20210930", "4", "USD", "", "",
     "365817000000.0000"),
    (ADSH_10K, "Assets", "us-gaap/2021", "20210930", "0", "USD", "", "",
     "351002000000.0000"),
    # DERA rounds Apple's FY2009, which really ended 2009-09-26, to 20090930.
    (ADSH_10K, "OperatingIncomeLoss", "us-gaap/2021", "20090930", "4", "USD",
     "", "", "7658000000.0000"),
    (ADSH_10Q, "NetIncomeLoss", "us-gaap/2021", "20210331", "1", "USD", "", "",
     "15457000000.0000"),
    (ADSH_10KA, "Revenues", "us-gaap/2021", "20210930", "4", "USD", "", "",
     "365000000000.0000"),
    (ADSH_NOTIME, "Assets", "us-gaap/2020", "20201231", "0", "USD", "", "",
     "500.0000"),
    # A period ending 2021-04-2x rounds forward to 20210430, three days past a
    # 2021-04-27 filing. That overhang is DERA's rounding, not a bad period,
    # and MAX_PERIOD_AHEAD_OF_FILED_DAYS must let it through.
    (ADSH_10Q, "Assets", "us-gaap/2021", "20210430", "0", "USD", "", "",
     "333779000000.0000"),
    # --- dropped: a balance sheet dated eight months after the 10-Q ---
    (ADSH_10Q, "StockholdersEquity", "us-gaap/2021", "20211231", "0", "USD",
     "", "", "141988000000.0000"),
    # --- dropped: dimensional ---
    (ADSH_10K, "Revenues", "us-gaap/2021", "20210930", "4", "USD",
     "ProductOrService=IPhone;", "", "191973000000.0000"),
    (ADSH_10K, "Assets", "us-gaap/2021", "20210930", "0", "USD", "", "SUBCO",
     "1000.0000"),
    # --- dropped: a company extension, version = the filer's own accession ---
    (ADSH_10K, "AppleSpecialThing", ADSH_10K, "20210930", "0", "USD", "", "",
     "42.0000"),
    # --- dropped: IFRS sharing a us-gaap local name ---
    (ADSH_10Q, "ProfitLoss", "ifrs/2021", "20210331", "1", "USD", "", "",
     "99.0000"),
    # --- dropped: a cumulative period pit_fact's CHECK refuses ---
    (ADSH_10K, "Revenues", "us-gaap/2021", "20210930", "8", "USD", "", "",
     "700000000000.0000"),
    # --- dropped: a tagged null. Absent is not zero. ---
    (ADSH_10K, "NetIncomeLoss", "us-gaap/2021", "20210930", "4", "USD", "", "",
     ""),
    # --- dropped: its submission is an S-1 ---
    (ADSH_S1, "Revenues", "us-gaap/2021", "20210331", "1", "USD", "", "",
     "5.0000"),
]

EXPECTED_KEPT = 7
EXPECTED_STATS = {
    "rows_read": 15, "dimensional": 2, "tag_rejected": 1,
    "nonstandard_taxonomy": 1, "qtrs_rejected": 1, "empty_value": 1,
    "submission_not_kept": 1, "unmapped_cik": 0, "bad_date": 0,
    "period_after_filing": 1, "kept": EXPECTED_KEPT,
}


def _tsv(columns, rows) -> str:
    out = ["\t".join(columns)]
    out.extend("\t".join(str(c) for c in row) for row in rows)
    return "\n".join(out) + "\n"


def build_fixture_zip(path: str) -> str:
    """Write a synthetic {quarter}.zip with real DERA column headers."""
    sub_rows = []
    for adsh, cik, name, sic, fye, form, period, filed, accepted, prevrpt in SUBMISSIONS:
        row = {c: "" for c in SUB_COLUMNS}
        row.update({"adsh": adsh, "cik": cik, "name": name, "sic": sic,
                    "fye": fye, "form": form, "period": period, "filed": filed,
                    "accepted": accepted, "prevrpt": prevrpt, "detail": "1",
                    "nciks": "1", "fy": period[:4], "fp": "FY"})
        sub_rows.append([row[c] for c in SUB_COLUMNS])

    num_rows = [list(r) + [""] for r in NUM_ROWS]     # + footnote
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("sub.txt", _tsv(SUB_COLUMNS, sub_rows))
        zf.writestr("num.txt", _tsv(NUM_COLUMNS, num_rows))
        # Present and deliberately never read by the ingest.
        zf.writestr("pre.txt", "adsh\treport\tline\n")
        zf.writestr("tag.txt", "tag\tversion\tcustom\n")
    return path


def weekday_sessions(first: str, last: str) -> list[str]:
    day = dt.date.fromisoformat(first)
    end = dt.date.fromisoformat(last)
    out = []
    while day <= end:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += dt.timedelta(days=1)
    return out


def _raises(thunk, exc) -> bool:
    try:
        thunk()
    except exc:
        return True
    except Exception:
        return False
    return False


# --------------------------------------------------------------------------

def main() -> int:
    tmp = tempfile.mkdtemp()
    zip_path = build_fixture_zip(os.path.join(tmp, "2021q2.zip"))
    db_path = os.path.join(tmp, "t_pit.db")

    print("== 1. TAG_FILTER comes from pit_policy, not a retyped list ==")
    ladder_tags = set()
    ladder_dei = set()
    for concept in P.concepts():
        for rung in P.ladder_for(concept).rungs:
            (ladder_dei if rung.taxonomy == P.TAXONOMY_DEI else ladder_tags).update(rung.tags)
    check("every ladder tag is retained", ladder_tags | ladder_dei <= D.TAG_FILTER,
          sorted((ladder_tags | ladder_dei) - D.TAG_FILTER))
    check("nothing extra beyond the ladders and the dei cover tags",
          D.TAG_FILTER - (ladder_tags | ladder_dei) <= D.DEI_COVER_TAGS,
          sorted(D.TAG_FILTER - (ladder_tags | ladder_dei) - D.DEI_COVER_TAGS))
    check("a composite rung contributes all three components",
          {"LongTermDebtNoncurrent", "LongTermDebtCurrent", "ShortTermBorrowings"}
          <= D.TAG_FILTER)
    check("the ASC 606 rung is in the filter even though it is date-gated",
          "RevenueFromContractWithCustomerExcludingAssessedTax" in D.TAG_FILTER)
    check("38 tags, the affordability of the whole ingest", len(D.TAG_FILTER) == 38,
          len(D.TAG_FILTER))
    check("every retained tag has a taxonomy",
          set(D.TAG_TAXONOMY) == set(D.TAG_FILTER))
    check("dei tags are tagged dei",
          D.TAG_TAXONOMY["EntityCommonStockSharesOutstanding"] == "dei"
          and D.TAG_TAXONOMY["EntityPublicFloat"] == "dei")
    check("us-gaap tags are tagged us-gaap",
          D.TAG_TAXONOMY["Revenues"] == "us-gaap"
          and D.TAG_TAXONOMY["Assets"] == "us-gaap")
    saving = D.tag_filter_saving()
    check("the saving is quantified from a measured quarter",
          saving["tags_in_tag_txt"] == 97000 and saving["company_extensions"] == 86781)
    check("89.5% of tag.txt is company extensions",
          abs(saving["company_extension_share"] - 0.895) < 0.001,
          saving["company_extension_share"])
    check("the filter keeps under a twentieth of a percent of all tags",
          saving["share_of_all_tags"] < 0.0005, saving["share_of_all_tags"])

    print("== 2. quarter arithmetic ==")
    check("parse_quarter", D.parse_quarter("2021q2") == (2021, 2))
    check("a bad quarter id raises", _raises(lambda: D.parse_quarter("2021Q5"), ValueError))
    check("quarters_between wraps the year",
          D.quarters_between("2011q3", "2012q2") == ["2011q3", "2011q4", "2012q1", "2012q2"])
    check("the archive is 69 quarters", len(D.archive_quarters()) == 69,
          len(D.archive_quarters()))
    check("every archive quarter has a measured size",
          all(q in D.QUARTER_BYTES for q in D.archive_quarters()))
    check("quarter_url", D.quarter_url("2021q2").endswith(
        "/financial-statement-data-sets/2021q2.zip"))
    check("the .gov user agent identifies the project, never a browser",
          "ShafferFinEval" in D.SEC_USER_AGENT and "Mozilla" not in D.SEC_USER_AGENT)
    check("the SEC rate limit is respected by construction",
          1.0 / D.SEC_MIN_INTERVAL <= 10.0, 1.0 / D.SEC_MIN_INTERVAL)

    print("== 3. DERA's month-end rounding, and the companyfacts reconciliation ==")
    check("Apple's FY2009 ended 2009-09-26 and keys to 2009-09-30",
          D.nearest_month_end("2009-09-26") == "2009-09-30")
    check("a 52/53-week year ending 2013-01-02 rounds BACK to 2012-12-31",
          D.nearest_month_end("2013-01-02") == "2012-12-31")
    check("a true month end is its own key",
          D.nearest_month_end("2021-06-30") == "2021-06-30")
    check("February is not special", D.nearest_month_end("2021-02-28") == "2021-02-28")
    check("a leap day is its own key", D.nearest_month_end("2020-02-29") == "2020-02-29")
    check("mid-month ties resolve into the month the date falls in",
          D.nearest_month_end("2021-06-15") == "2021-06-30")
    check("late-December rounds forward, not into the next year",
          D.nearest_month_end("2020-12-28") == "2020-12-31")
    check("DERA ddate is already a month end, so the key is a no-op on it",
          D.nearest_month_end(D.dera_date("20090930")) == "2009-09-30")
    check("dera_date converts", D.dera_date("20210506") == "2021-05-06")
    check("dera_date refuses a non-date", _raises(lambda: D.dera_date("20210230"), ValueError))
    check("dera_date refuses an ISO string", _raises(lambda: D.dera_date("2021-05-06"), ValueError))
    check("dera_accepted normalises DERA's stamp",
          D.dera_accepted("2021-05-06 16:28:00.0") == "2021-05-06T16:28:00")
    check("dera_accepted leaves it NAIVE, so pit_policy reads it as Eastern",
          "+" not in (D.dera_accepted("2021-05-06 16:28:00.0") or "")
          and "Z" not in (D.dera_accepted("2021-05-06 16:28:00.0") or ""))
    check("a missing stamp is None", D.dera_accepted("") is None
          and D.dera_accepted(None) is None)

    print("== 4. sub.txt is read WITHOUT the hindsight columns ==")
    with zipfile.ZipFile(zip_path) as zf:
        subs_all = D.read_submissions(zf, forms=None)
        subs = D.read_submissions(zf)
    check("all forms reads every submission", len(subs_all) == 5, len(subs_all))
    check("periodic forms drop the S-1", len(subs) == 4 and ADSH_S1 not in subs,
          sorted(subs))
    check("prevrpt is unreachable, not merely unused",
          all("prevrpt" not in s for s in subs.values()))
    check("fy and fp are unreachable too -- they describe the FILING",
          all("fy" not in s and "fp" not in s for s in subs.values()))
    check("prevrpt IS in the fixture file, so the test is real",
          "prevrpt" in SUB_COLUMNS)
    check("the dropped list names them", set(D.DROPPED_SUB_COLUMNS) == {"prevrpt", "fy", "fp"})
    check("prevrpt=1 rows are KEPT -- an amended original is the vintage we want",
          ADSH_10K in subs and SUBMISSIONS[0][9] == "1")

    print("== 5. the filter chain, stage by stage ==")
    conn = pit_store.init_db(db_path)
    pit_store.insert_calendar(conn, "XNYS",
                              weekday_sessions("2009-01-01", "2022-12-31"), "test")
    resolver = D.session_resolver(conn)
    check("a populated calendar yields a resolver", resolver is not None)
    check("the resolver carries Saturday to Monday",
          resolver("2021-10-30") == "2021-11-01", resolver("2021-10-30"))

    with zipfile.ZipFile(zip_path) as zf:
        subs = D.read_submissions(zf)
        ids = D.register_entities(conn, subs)
        stats: dict[str, int] = {}
        rows = list(D.iter_quarter_facts(zf, "2021q2", ids, subs,
                                         next_session=resolver, stats=stats))
    for key, want in EXPECTED_STATS.items():
        check(f"stat {key} == {want}", stats.get(key) == want, stats.get(key))
    check("seven rows survive everything", len(rows) == EXPECTED_KEPT, len(rows))
    check("the 15-day tolerance keeps DERA's rounding overhang",
          any(r[5] == "2021-04-30" and r[12] == "2021-04-27" for r in rows))
    check("a balance sheet dated 8 months after its 10-Q is refused",
          not any(r[5] == "2021-12-31" for r in rows))
    check("the tolerance is the maximum honest rounding distance",
          D.MAX_PERIOD_AHEAD_OF_FILED_DAYS == 15)

    print("== 6. the row mapping into pit_store.FACT_COLUMNS ==")
    by = {(r[2], r[5], r[6], r[10]): r for r in rows}   # tag, period_end, qtrs, accn
    cols = {name: i for i, name in enumerate(pit_store.FACT_COLUMNS)}
    check("the tuple is exactly FACT_COLUMNS wide",
          all(len(r) == len(pit_store.FACT_COLUMNS) for r in rows))
    rev = by[("Revenues", "2021-09-30", 4, ADSH_10K)]
    check("entity_id resolves through the CIK, never the ticker",
          rev[cols["entity_id"]] == pit_store.entity_id_for_cik(conn, APPLE_CIK))
    check("taxonomy", rev[cols["taxonomy"]] == "us-gaap")
    check("unit comes from uom", rev[cols["unit"]] == "USD")
    check("period_start is NULL -- DERA publishes no start date",
          rev[cols["period_start"]] is None)
    check("period_end is DERA's month-end ddate", rev[cols["period_end"]] == "2021-09-30")
    check("qtrs is an int", rev[cols["qtrs"]] == 4 and isinstance(rev[cols["qtrs"]], int))
    check("segments and coreg are empty by construction",
          rev[cols["segments"]] == "" and rev[cols["coreg"]] == "")
    check("val is a float", rev[cols["val"]] == 365817000000.0)
    check("accn is the accession", rev[cols["accn"]] == ADSH_10K)
    check("form comes from sub.txt", rev[cols["form"]] == "10-K")
    check("filed is ISO", rev[cols["filed"]] == "2021-10-29")
    check("accepted_eastern is the Eastern wall clock",
          rev[cols["accepted_eastern"]] == "2021-10-29T17:00:00")
    check("source names the quarter", rev[cols["source"]] == "dera:2021q2")
    check("the latency policy version is stamped on every row",
          all(r[cols["latency_policy_version"]] == P.LATENCY_POLICY_VERSION for r in rows))
    apple_2009 = by[("OperatingIncomeLoss", "2009-09-30", 4, ADSH_10K)]
    check("the FY2009 comparative keeps DERA's rounded period end",
          apple_2009[cols["period_end"]] == "2009-09-30")

    print("== 7. the information latency policy, applied per submission ==")
    check("accepted 17:00 Friday -> the next SESSION, Monday",
          rev[cols["available_date"]] == "2021-11-01", rev[cols["available_date"]])
    ni = by[("NetIncomeLoss", "2021-03-31", 1, ADSH_10Q)]
    check("accepted 09:00 -> the same session",
          ni[cols["available_date"]] == "2021-04-27", ni[cols["available_date"]])
    amend = by[("Revenues", "2021-09-30", 4, ADSH_10KA)]
    check("accepted 12:00 -> the same session",
          amend[cols["available_date"]] == "2022-01-25", amend[cols["available_date"]])
    quiet = by[("Assets", "2020-12-31", 0, ADSH_NOTIME)]
    check("no acceptance time -> the next session AFTER filed, the pessimistic branch",
          quiet[cols["available_date"]] == "2021-03-02", quiet[cols["available_date"]])
    check("available_date is never earlier than filed -- pit_fact's CHECK",
          all(r[cols["available_date"]] >= r[cols["filed"]] for r in rows))
    check("no row is available before it was accepted",
          all(r[cols["accepted_eastern"]] is None
              or r[cols["available_date"]] >= r[cols["accepted_eastern"]][:10] for r in rows))

    print("== 8. what was DROPPED is genuinely absent ==")
    kept_tags = {r[cols["tag"]] for r in rows}
    check("no company extension survived", "AppleSpecialThing" not in kept_tags)
    check("no IFRS row survived", "ProfitLoss" not in kept_tags)
    check("no dimensional revenue survived",
          not any(r[cols["val"]] == 191973000000.0 for r in rows))
    check("no co-registrant row survived",
          not any(r[cols["val"]] == 1000.0 for r in rows))
    check("no qtrs=8 cumulative row survived",
          all(r[cols["qtrs"]] in D.ACCEPTED_QTRS for r in rows))
    check("the tagged null did not become a zero",
          not any(r[cols["tag"]] == "NetIncomeLoss" and r[cols["period_end"]] == "2021-09-30"
                  for r in rows))
    check("the S-1's revenue did not survive",
          not any(r[cols["accn"]] == ADSH_S1 for r in rows))

    print("== 9. ingest_quarter writes, and a second run is idempotent ==")
    conn2 = pit_store.init_db(os.path.join(tmp, "t_ingest.db"))
    pit_store.insert_calendar(conn2, "XNYS",
                              weekday_sessions("2009-01-01", "2022-12-31"), "test")
    resolver2 = D.session_resolver(conn2)
    first = D.ingest_quarter(conn2, "2021q2", zip_path=zip_path, next_session=resolver2)
    check("first run is 'written'", first["status"] == "written", first)
    check("it kept six rows", first["rows_kept"] == EXPECTED_KEPT, first["rows_kept"])
    check("it inserted six rows", first["rows_inserted"] == EXPECTED_KEPT,
          first["rows_inserted"])
    check("it read every num.txt row", first["rows_read"] == 15, first["rows_read"])
    n_fact = conn2.execute("SELECT COUNT(*) AS n FROM pit_fact").fetchone()["n"]
    check("pit_fact holds six", n_fact == EXPECTED_KEPT, n_fact)
    check("three entities registered, keyed on CIK",
          conn2.execute("SELECT COUNT(*) AS n FROM pit_entity").fetchone()["n"] == 3)
    check("the CIK is stored zero-padded to ten",
          conn2.execute("SELECT cik FROM pit_entity ORDER BY cik").fetchall()[0]["cik"]
          == "0000222222")
    check("the point-in-time SIC came from the filing header",
          conn2.execute(
              "SELECT COUNT(*) AS n FROM pit_entity_sic").fetchone()["n"] == 4)
    check("sic_as_of reads the filing, not a current field",
          pit_store.sic_as_of(conn2, pit_store.entity_id_for_cik(conn2, APPLE_CIK),
                              "2021-12-31") == "3571")
    check("entity names are deliberately NOT written here",
          conn2.execute("SELECT COUNT(*) AS n FROM pit_entity_name").fetchone()["n"] == 0)

    second = D.ingest_quarter(conn2, "2021q2", zip_path=zip_path, next_session=resolver2)
    check("second run is 'exists', not a re-parse", second["status"] == "exists", second)
    check("row count unchanged after the second run",
          conn2.execute("SELECT COUNT(*) AS n FROM pit_fact").fetchone()["n"]
          == EXPECTED_KEPT)
    forced = D.ingest_quarter(conn2, "2021q2", zip_path=zip_path,
                              next_session=resolver2, force=True)
    check("a forced re-run re-reads", forced["status"] == "written")
    check("but inserts nothing -- INSERT OR IGNORE on the accession key",
          forced["rows_inserted"] == 0, forced["rows_inserted"])
    check("and every row it saw was a duplicate",
          forced["duplicates_ignored"] == EXPECTED_KEPT, forced["duplicates_ignored"])
    check("row count STILL unchanged",
          conn2.execute("SELECT COUNT(*) AS n FROM pit_fact").fetchone()["n"]
          == EXPECTED_KEPT)
    runs = conn2.execute(
        "SELECT * FROM pit_ingest_run ORDER BY ingest_id").fetchall()
    check("every attempt is recorded in pit_ingest_run", len(runs) == 2, len(runs))
    check("the run records what it read and kept",
          runs[0]["rows_read"] == 15 and runs[0]["rows_kept"] == EXPECTED_KEPT)
    check("the run records the source bytes",
          runs[0]["bytes_downloaded"] == os.path.getsize(zip_path))
    summary = pit_store.loads(runs[0]["summary_json"])
    check("the summary carries both policy versions",
          summary["ladder_version"] == P.LADDER_VERSION
          and summary["latency_policy_version"] == P.LATENCY_POLICY_VERSION)
    check("the summary records that sessions were resolved",
          summary["session_resolved"] is True)
    check("the summary carries the per-stage filter counts",
          summary["filters"]["dimensional"] == 2)

    print("== 10. a restatement lands BESIDE its original, never over it ==")
    apple = pit_store.entity_id_for_cik(conn2, APPLE_CIK)
    both = conn2.execute(
        """SELECT * FROM pit_fact WHERE entity_id = ? AND tag = 'Revenues'
               AND period_end = '2021-09-30' AND qtrs = 4
            ORDER BY available_date""", (apple,)).fetchall()
    check("two observations of the same period", len(both) == 2, len(both))
    check("they differ by accession", both[0]["accn"] != both[1]["accn"])
    before = pit_store.fact_as_of(conn2, apple, "Revenues", "USD", "2021-09-30", 4,
                                  "2022-01-24")
    after = pit_store.fact_as_of(conn2, apple, "Revenues", "USD", "2021-09-30", 4,
                                 "2022-01-25")
    check("the day before the amendment, the ORIGINAL is returned",
          before is not None and before["val"] == 365817000000.0,
          before["val"] if before else None)
    check("the day of the amendment, the RESTATED value is returned",
          after is not None and after["val"] == 365000000000.0,
          after["val"] if after else None)
    check("nothing is knowable before the 10-K was available",
          pit_store.fact_as_of(conn2, apple, "Revenues", "USD", "2021-09-30", 4,
                               "2021-10-29") is None)
    check("it IS knowable on the Monday the calendar carried it to",
          pit_store.fact_as_of(conn2, apple, "Revenues", "USD", "2021-09-30", 4,
                               "2021-11-01") is not None)
    check("pit_fact is still append-only after a bulk load",
          _raises(lambda: conn2.execute(
              "UPDATE pit_fact SET val = 1 WHERE entity_id = ?", (apple,)),
              sqlite3.IntegrityError))
    check("bulk_load restored foreign keys on the way out",
          conn2.execute("PRAGMA foreign_keys").fetchone()[0] == 1)
    check("bulk_load restored synchronous on the way out",
          conn2.execute("PRAGMA synchronous").fetchone()[0] == 2)

    print("== 11. a limit and an all-forms run ==")
    conn3 = pit_store.init_db(os.path.join(tmp, "t_limit.db"))
    limited = D.ingest_quarter(conn3, "2021q2", zip_path=zip_path, limit=2)
    check("limit stops the stream", limited["rows_kept"] == 2, limited["rows_kept"])
    check("a limited run is recorded 'partial', never 'complete'",
          conn3.execute("SELECT status FROM pit_ingest_run").fetchone()["status"]
          == "partial")
    check("so it cannot block the real ingest of that quarter",
          D.ingest_quarter(conn3, "2021q2", zip_path=zip_path)["status"] == "written")
    check("and the full run then holds every row",
          conn3.execute("SELECT COUNT(*) AS n FROM pit_fact").fetchone()["n"]
          == EXPECTED_KEPT)
    conn4 = pit_store.init_db(os.path.join(tmp, "t_allforms.db"))
    allf = D.ingest_quarter(conn4, "2021q2", zip_path=zip_path, forms=None)
    check("forms=None keeps the S-1's revenue too",
          allf["rows_kept"] == EXPECTED_KEPT + 1, allf["rows_kept"])
    check("and registers the extra filer",
          conn4.execute("SELECT COUNT(*) AS n FROM pit_entity").fetchone()["n"] == 4)
    conn5 = pit_store.init_db(os.path.join(tmp, "t_nosic.db"))
    D.ingest_quarter(conn5, "2021q2", zip_path=zip_path, capture_sic=False)
    check("capture_sic=False writes no SIC rows",
          conn5.execute("SELECT COUNT(*) AS n FROM pit_entity_sic").fetchone()["n"] == 0)
    conn6 = pit_store.init_db(os.path.join(tmp, "t_nocal.db"))
    check("an empty calendar yields no resolver", D.session_resolver(conn6) is None)
    nocal = D.ingest_quarter(conn6, "2021q2", zip_path=zip_path)
    check("the ingest still runs without a calendar",
          nocal["rows_kept"] == EXPECTED_KEPT)
    unresolved = conn6.execute(
        """SELECT available_date FROM pit_fact WHERE accn = ? LIMIT 1""",
        (ADSH_10K,)).fetchone()["available_date"]
    check("and falls back to the calendar day, flagged rather than hidden",
          unresolved == "2021-10-30", unresolved)
    check("the run says sessions were NOT resolved",
          pit_store.loads(conn6.execute(
              "SELECT summary_json FROM pit_ingest_run").fetchone()["summary_json"]
          )["session_resolved"] is False)

    print("== 12. the estimate is offline, structured and traceable ==")
    est = D.estimate_ingest(D.quarters_between("2013q1", "2013q4"))
    check("it names its quarters", est["n_quarters"] == 4)
    check("download bytes are the sum of the measured sizes",
          est["download"]["total_bytes"]
          == sum(D.QUARTER_BYTES[q] for q in ("2013q1", "2013q2", "2013q3", "2013q4")))
    check("nothing is missing a size", est["quarters_without_a_measured_size"] == [])
    check("it projects retained rows", est["rows"]["retained_rows"] > 0)
    check("the reduction ratio matches the measured quarter",
          abs(est["rows"]["reduction_ratio"]
              - D.MEASURED_QUARTER["num_total_rows"]
              / D.MEASURED_QUARTER["retained_periodic"]) < 0.1,
          est["rows"]["reduction_ratio"])
    check("bytes per row come from a real insert, not a guess",
          abs(est["database"]["bytes_per_row_total"]
              - D.MEASURED_INSERT["bytes_per_row_total"]) < 1.0,
          est["database"]["bytes_per_row_total"])
    check("the footprint is table plus index",
          est["database"]["total_bytes"]
          == est["database"]["pit_fact_bytes"] + est["database"]["index_bytes"])
    check("runtime has all three terms",
          {"download_seconds", "parse_seconds", "insert_seconds", "total_seconds"}
          <= set(est["runtime"]))
    check("the total is the sum of the three",
          abs(est["runtime"]["total_seconds"]
              - (est["runtime"]["download_seconds"] + est["runtime"]["parse_seconds"]
                 + est["runtime"]["insert_seconds"])) < 0.3)
    check("the basis says how every number was obtained",
          {"quarter_sizes", "row_counts", "bytes_per_row", "rates",
           "scaling_assumption"} <= set(est["basis"]))
    check("the known holes travel with the estimate",
          len(est["basis"]["known_holes"]) == 3)
    check("the dei hole is one of them",
          any("EntityPublicFloat" in n for n in est["basis"]["known_holes"]))
    big = D.estimate_ingest(D.quarters_between("2013q1", "2014q4"))
    check("twice the quarters, more bytes",
          big["download"]["total_bytes"] > est["download"]["total_bytes"])
    check("and more rows", big["rows"]["retained_rows"] > est["rows"]["retained_rows"])
    check("forms=None projects MORE rows than periodic-only",
          D.estimate_ingest(["2013q1"], forms=None)["rows"]["retained_rows"]
          > D.estimate_ingest(["2013q1"])["rows"]["retained_rows"])
    check("an unmeasured quarter is reported, not silently zero",
          D.estimate_ingest(["2099q1"])["quarters_without_a_measured_size"] == ["2099q1"])

    print("== 13. the warm-up decision is stated with its cost ==")
    w = D.warmup_window()
    check("the first scored date is the approved one", w["scoring_start"] == "2013-01-01")
    check("the binding minimum is 2010q1", w["minimum_first_quarter"] == "2010q1")
    check("2010q1..2012q4 is twelve quarters",
          len(D.quarters_between("2010q1", "2012q4")) == 12)
    check("the recommendation is the whole archive", w["first_quarter"] == "2009q2")
    check("fifteen warm-up quarters", w["n_warmup_quarters"] == 15, w["n_warmup_quarters"])
    check("no warm-up quarter reaches the first scored quarter",
          all(q < "2013q1" for q in w["warmup_quarters"]))
    check("the window runs to the end of the archive",
          w["window_quarters"][-1] == D.ARCHIVE_LAST_QUARTER)
    check("the extra over the minimum is the three 2009 quarters",
          w["extra_bytes_over_minimum"]
          == sum(D.QUARTER_BYTES[q] for q in ("2009q2", "2009q3", "2009q4")),
          w["extra_bytes_over_minimum"])
    check("and it is under a fifth of a percent of the window",
          w["extra_bytes_over_minimum"] / w["window_bytes"] < 0.002,
          w["extra_bytes_over_minimum"] / w["window_bytes"])
    check("the phase-in bias travels with the decision",
          "484" in w["phase_in_warning"])
    check("both reasons are recorded",
          "prior-prior" in w["why_minimum"] and "2009-09-26" in w["why_recommended"])

    print("== 14. measure_quarter reproduces the stage table ==")
    mq = D.measure_quarter(zip_path, "2021q2")
    check("it counts every num.txt row", mq["num_total_rows"] == EXPECTED_STATS["rows_read"],
          mq["num_total_rows"])
    check("consolidated stage", mq["after_consolidated"] == 13, mq["after_consolidated"])
    check("tag stage keeps 11 -- 13 consolidated, less the extension, less IFRS",
          mq["after_tag"] == 11, mq["after_tag"])
    check("qtrs stage drops the cumulative row", mq["after_qtrs"] == 10,
          mq["after_qtrs"])
    check("value stage drops the tagged null", mq["retained"] == 9,
          mq["retained"])
    check("periodic stage drops the S-1 row",
          mq["rows_before_period_guard"] == EXPECTED_KEPT + 1,
          mq["rows_before_period_guard"])
    check("it ends where the ingest ends", mq["retained_periodic"] == EXPECTED_KEPT,
          mq["retained_periodic"])
    check("it counts the future-dated period it refused",
          mq["period_after_filing"] == 1, mq["period_after_filing"])
    check("it counts the IFRS row", mq["ifrs_rows_excluded"] == 1)
    check("it reads tag.txt, which the ingest never opens",
          "tag_txt_rows" in mq and "tag_txt_bytes" in mq)
    check("it records the unit mix without filtering it",
          mq["units"].get("USD", 0) == EXPECTED_KEPT)
    check("the measured constants in the module match what it produces on the "
          "real 2021q2 (spot-check the reduction identity)",
          D.MEASURED_QUARTER["rows_before_period_guard"]
          - D.MEASURED_QUARTER["period_after_filing"]
          == D.MEASURED_QUARTER["retained_periodic"])

    print("== 15. the report renders ==")
    text = D.report(D.quarters_between("2013q1", "2013q4"))
    check("it is long enough to be a report", len(text) > 2000, len(text))
    check("it names the tag filter", "TAG_FILTER retains" in text)
    check("it names the warm-up decision", "WARM-UP DECISION" in text)
    check("it names the known holes", "KNOWN HOLES" in text)
    check("it says it does not run the archive", "does NOT run the archive" in text)
    check("main() returns 0", D.main([]) == 0)

    print()
    print("FAILURES:", len(fails), fails if fails else "")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
