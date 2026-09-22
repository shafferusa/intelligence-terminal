"""The share-count gate for the point-in-time research store.

    python test_pit_shares.py            # offline fixtures + a live confirmation
    python test_pit_shares.py --offline  # fixtures only, no network at all

This test guards the input to the VALUATION FACTOR, which carries 40% of the
equity Shaffer Score. The failure it exists to catch does not look like a
failure: a share count restated for a later split, multiplied by a
retroactively adjusted price, produces a market capitalisation that is wrong by
the whole split ratio and entirely plausible on the page.

Most of it is OFFLINE on hand-built fixtures, and that is deliberate here --
unlike the survivorship gate in `test_pit_identity.py`, where the defects being
guarded against are properties of the live sources. What is under test in this
file is the SELECTOR: given these five vintages of one instant, which one comes
back. The fixtures are not invented. Every number in them was read from
companyconcept on 2026-09-20 and the live section at the end re-reads the
source and checks the fixtures still match it, so an offline pass can never
become a pass against a stale assumption.

Nothing here touches `shafferfineval.db` or `shafferfineval_pit.db`. The
database is a throwaway file under the system temp directory.
"""

from __future__ import annotations

import datetime as _dt
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_policy
import pit_shares as PS
import pit_store

fails: list[str] = []
skips: list[str] = []


def check(name: str, cond: bool, extra: object = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {extra}")
        fails.append(name)


def skip(name: str, why: object = "") -> None:
    print(f"  SKIP  {name} {why}")
    skips.append(name)


def section(title: str) -> None:
    print(f"\n== {title} ==")


CACHE_DIR = os.path.join(tempfile.gettempdir(), "shafferfineval_pit_shares_cache")

# --------------------------------------------------------------------------
# Fixtures. Read live from companyconcept on 2026-09-20; re-verified by the
# live section below.
# --------------------------------------------------------------------------

#: us-gaap:CommonStockSharesOutstanding, CIK 320193, instant 2019-09-28. Five
#: vintages of ONE instant. The fifth is the 4:1 split of 2020-08-31 applied
#: retroactively, and the whole point of a point-in-time selector is that the
#: first four are what a reader saw before 2020-10-30.
AAPL_2019_INSTANT = (
    # (val, accn, form, filed, accepted_raw)
    (4443236000, "0000320193-19-000119", "10-K", "2019-10-31", "2019-10-31T16:32:38.000Z"),
    (4443236000, "0000320193-20-000010", "10-Q", "2020-01-29", "2020-01-28T16:31:09.000Z"),
    (4443236000, "0000320193-20-000052", "10-Q", "2020-05-01", "2020-04-30T16:32:01.000Z"),
    (4443236000, "0000320193-20-000062", "10-Q", "2020-07-31", "2020-07-30T16:30:32.000Z"),
    (17772945000, "0000320193-20-000096", "10-K", "2020-10-30", "2020-10-29T16:33:08.000Z"),
)

#: dei:EntityCommonStockSharesOutstanding, CIK 320193. Cover-page instants
#: straddling the split: 2020-07-17 is pre-split, 2020-10-16 is post.
AAPL_COVER = (
    (4275634000, "2020-07-17", "0000320193-20-000062", "10-Q", "2020-07-31"),
    (17001802000, "2020-10-16", "0000320193-20-000096", "10-K", "2020-10-30"),
)

#: The three-way test of the identity rules, as entity ids assigned in main().
CIK_AAPL = "320193"
CIK_AVERAGE_ONLY = "9999001"       # synthetic: files only the weighted average
CIK_ZERO = "40730"                 # Motors Liquidation Co: a genuine zero
CIK_PRE_XBRL = "806085"            # Lehman: nothing, ever
CIK_ISSUED_ONLY = "9999002"        # synthetic: only CommonStockSharesIssued


def _row(entity_id: int, concept_key: str, *, val: float, period_end: str,
         accn: str, form: str, filed: str, accepted_raw: object = None,
         period_start: object = None) -> tuple:
    """One pit_share_obs tuple in SHARE_COLUMNS order, availability from policy."""
    concept = PS.concept_for(concept_key)
    detail = pit_policy.available_date_detail(accepted_raw, filed, None)
    qtrs = 0 if period_start is None else 1
    return (
        entity_id, concept.key, concept.taxonomy, concept.tag, concept.unit,
        concept.measure_kind, period_start, period_end, qtrs, float(val), accn,
        form, None, None, filed, accepted_raw, detail["accepted_et"],
        detail["available_date"], detail["rule"], detail["latency_policy_version"],
        PS.PRICE_BASIS_RAW, "fixture", "2026-09-20T00:00:00+00:00",
    )


def load_fixtures(conn: sqlite3.Connection) -> dict[str, int]:
    """Insert the hand-built store. Returns cik -> entity_id."""
    ids = {cik: pit_store.upsert_entity(conn, cik) for cik in
           (CIK_AAPL, CIK_AVERAGE_ONLY, CIK_ZERO, CIK_PRE_XBRL, CIK_ISSUED_ONLY)}
    rows: list[tuple] = []

    for val, accn, form, filed, accepted in AAPL_2019_INSTANT:
        rows.append(_row(ids[CIK_AAPL], PS.CONCEPT_GAAP_OUTSTANDING, val=val,
                         period_end="2019-09-28", accn=accn, form=form,
                         filed=filed, accepted_raw=accepted))
    for val, end, accn, form, filed in AAPL_COVER:
        rows.append(_row(ids[CIK_AAPL], PS.CONCEPT_DEI_COVER, val=val,
                         period_end=end, accn=accn, form=form, filed=filed))

    # An issuer whose ONLY share concept is the period average. The correct
    # answer for this issuer is "unavailable", forever, at every as-of date.
    rows.append(_row(ids[CIK_AVERAGE_ONLY], PS.CONCEPT_WEIGHTED_DILUTED,
                     val=500_000_000, period_start="2019-01-01",
                     period_end="2019-12-31", accn="9999001-20-000001",
                     form="10-K", filed="2020-02-14"))

    # A genuine zero. Motors Liquidation Co really reports this.
    rows.append(_row(ids[CIK_ZERO], PS.CONCEPT_DEI_COVER, val=0,
                     period_end="2015-03-31", accn="0001193125-15-000001",
                     form="10-Q", filed="2015-05-11"))

    # Issued only: an upper bound on outstanding, off the default ladder.
    rows.append(_row(ids[CIK_ISSUED_ONLY], PS.CONCEPT_GAAP_ISSUED,
                     val=120_000_000, period_end="2019-06-30",
                     accn="9999002-19-000001", form="10-Q", filed="2019-08-01"))

    # CIK_PRE_XBRL gets nothing at all, which is the point.
    with pit_store.transaction(conn):
        PS.insert_share_obs(conn, rows)
    return ids


# ==========================================================================
def main() -> int:
    offline = "--offline" in sys.argv
    db_dir = tempfile.mkdtemp(prefix="pit_shares_test_")
    db_path = os.path.join(db_dir, "pit_shares_test.db")
    print(f"throwaway db: {db_path}")
    print(f"http cache  : {CACHE_DIR}")

    conn = pit_store.init_db(db_path)

    section("schema")
    first = PS.ensure_schema(conn)
    second = PS.ensure_schema(conn)
    check("ensure_schema creates on a bare store", first == "created", first)
    check("ensure_schema is idempotent", second == "exists", second)
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(pit_share_obs)")}
    check("`frame` is never a column", "frame" not in columns)
    check("the acceptance audit trail is three-form",
          {"accepted_raw", "accepted_eastern", "available_date"} <= columns)
    check("every row names its concept", "concept_key" in columns)
    check("every row names the price basis it needs", "price_basis" in columns)

    ids = load_fixtures(conn)
    aapl = ids[CIK_AAPL]

    section("the fixtures loaded")
    n_rows = conn.execute("SELECT COUNT(*) AS n FROM pit_share_obs").fetchone()["n"]
    check("all fixture rows stored", n_rows == 10, n_rows)
    again = PS.insert_share_obs(conn, [
        _row(aapl, PS.CONCEPT_GAAP_OUTSTANDING, val=4443236000,
             period_end="2019-09-28", accn="0000320193-19-000119", form="10-K",
             filed="2019-10-31", accepted_raw="2019-10-31T16:32:38.000Z")])
    conn.commit()
    check("re-inserting a published observation writes nothing", again == 0, again)

    section("append-only, enforced by trigger and not by convention")
    try:
        conn.execute("UPDATE pit_share_obs SET val = 1 WHERE share_obs_id = 1")
        conn.commit()
        check("UPDATE is refused", False, "no error raised")
    except sqlite3.DatabaseError as exc:
        check("UPDATE is refused", "append-only" in str(exc), exc)
        conn.rollback()
    try:
        conn.execute("DELETE FROM pit_share_obs WHERE share_obs_id = 1")
        conn.commit()
        check("DELETE is refused", False, "no error raised")
    except sqlite3.DatabaseError as exc:
        check("DELETE is refused", "append-only" in str(exc), exc)
        conn.rollback()
    try:
        conn.execute(
            "INSERT INTO pit_share_obs (" + ", ".join(PS.SHARE_COLUMNS) + ") VALUES ("
            + ", ".join("?" * len(PS.SHARE_COLUMNS)) + ")",
            (aapl, PS.CONCEPT_DEI_COVER, "dei", "EntityCommonStockSharesOutstanding",
             "shares", PS.MEASURE_POINT_IN_TIME, None, "2019-01-01", 0, 1.0,
             "leak-00-000001", "10-Q", None, None, "2019-02-01", None, None,
             "2019-01-15", "same_session", pit_policy.LATENCY_POLICY_VERSION,
             PS.PRICE_BASIS_RAW, "fixture", "2026-09-20T00:00:00+00:00"))
        conn.commit()
        check("available_date < filed is refused", False, "no error raised")
    except sqlite3.DatabaseError as exc:
        check("available_date < filed is refused", "CHECK" in str(exc).upper(), exc)
        conn.rollback()

    # ======================================================================
    section("a period average is NEVER a point-in-time count")
    average = PS.concept_for(PS.CONCEPT_WEIGHTED_DILUTED)
    check("the weighted average declares itself a period average",
          average.measure_kind == PS.MEASURE_PERIOD_AVERAGE
          and average.point_in_time is False)
    check("it is not on the default ladder",
          PS.CONCEPT_WEIGHTED_DILUTED not in PS.SHARES_LADDER_STRICT)
    check("it is not on the opt-in ladder either",
          PS.CONCEPT_WEIGHTED_DILUTED not in PS.SHARES_LADDER_WITH_ISSUED)
    try:
        PS.shares_as_of_detail(conn, aapl, "2021-01-01",
                               ladder=(PS.CONCEPT_WEIGHTED_DILUTED,))
        check("putting it in a ladder raises", False, "no error raised")
    except ValueError as exc:
        check("putting it in a ladder raises", "point-in-time" in str(exc), exc)

    avg_only = ids[CIK_AVERAGE_ONLY]
    got = PS.shares_as_of(conn, avg_only, "2021-06-30")
    detail = PS.shares_as_of_detail(conn, avg_only, "2021-06-30")
    check("an average-only issuer returns no count", got is None, got)
    check("and says why, by name",
          detail["reason"] == PS.REASON_ONLY_PERIOD_AVERAGE, detail["reason"])
    check("no substitute value is offered", detail["shares"] is None)
    rows_exist = conn.execute(
        "SELECT COUNT(*) AS n FROM pit_share_obs WHERE entity_id = ?",
        (avg_only,)).fetchone()["n"]
    check("the average itself IS stored, so the gap is measurable", rows_exist == 1)

    # ======================================================================
    section("the split trap: AAPL instant 2019-09-28")
    pre = PS.shares_as_of_detail(conn, aapl, "2019-12-31",
                                 ladder=(PS.CONCEPT_GAAP_OUTSTANDING,))
    check("a pre-split as-of returns the pre-split count",
          pre["shares"] == 4443236000.0, pre["shares"])
    check("and names the filing it came from",
          pre["accn"] == "0000320193-19-000119", pre["accn"])
    # enforce_staleness off for the vintage walk only. The fixture holds ONE
    # instant, and an instantaneous share count goes stale after 4 months under
    # pit_policy's shares_outstanding override -- so the freshness bound would
    # hide the later vintages of 2019-09-28 before the selector got to choose
    # between them, and choosing between them is what is under test here. The
    # bound itself is tested on its own further down.
    still_pre = PS.shares_as_of_detail(conn, aapl, "2020-08-15",
                                       ladder=(PS.CONCEPT_GAAP_OUTSTANDING,),
                                       enforce_staleness=False)
    check("still pre-split after three further filings restated the same number",
          still_pre["shares"] == 4443236000.0, still_pre["shares"])
    check("and from the newest vintage available by then",
          still_pre["accn"] == "0000320193-20-000062", still_pre["accn"])
    post = PS.shares_as_of_detail(conn, aapl, "2021-01-31",
                                  ladder=(PS.CONCEPT_GAAP_OUTSTANDING,),
                                  enforce_staleness=False)
    check("after the restatement lands, the split-adjusted count is visible",
          post["shares"] == 17772945000.0, post["shares"])
    check("the two differ by the 4:1 split",
          abs(post["shares"] / pre["shares"] - 4.0) < 0.001,
          post["shares"] / pre["shares"])

    basis = PS.price_basis_for(pre)
    check("the helper demands a raw price",
          basis["required_price_basis"] == PS.PRICE_BASIS_RAW)
    check("and refuses the adjusted series by name",
          PS.PRICE_BASIS_TOTAL_RETURN in basis["refused_price_bases"]
          and PS.PRICE_BASIS_SPLIT_ADJUSTED in basis["refused_price_bases"])
    check("and states which era the count is in",
          basis["share_basis_era"] == "2019-09-28", basis["share_basis_era"])

    # ======================================================================
    section("market cap refuses what it cannot justify")
    cap = PS.market_cap_as_of_detail(conn, aapl, "2020-08-15", 460.0)
    check("a raw price and a contemporaneous count give a market cap",
          cap["market_cap"] is not None and cap["market_cap"] > 0)
    check("the answer carries its provenance",
          cap.get("accn") == "0000320193-20-000062"
          and cap.get("concept_key") == PS.CONCEPT_DEI_COVER
          and cap.get("measured_at") == "2020-07-17",
          (cap.get("accn"), cap.get("concept_key"), cap.get("measured_at")))
    check("the fresh cover-page count wins over the older balance-sheet one",
          cap["shares_as_published"] == 4275634000.0, cap["shares_as_published"])
    check("and says the split window was not verified",
          cap["split_check"] == PS.SPLIT_UNCHECKED, cap["split_check"])

    refused = PS.market_cap_as_of_detail(
        conn, aapl, "2020-08-15", 115.0,
        price_basis=PS.PRICE_BASIS_TOTAL_RETURN,
        ladder=(PS.CONCEPT_GAAP_OUTSTANDING,))
    check("an adjusted-basis price is refused outright",
          refused["market_cap"] is None
          and refused["reason"] == PS.REASON_PRICE_BASIS_REFUSED, refused["reason"])
    check("market_cap_as_of returns None for it",
          PS.market_cap_as_of(conn, aapl, "2020-08-15", 115.0,
                              price_basis=PS.PRICE_BASIS_SPLIT_ADJUSTED,
                              ladder=(PS.CONCEPT_GAAP_OUTSTANDING,)) is None)

    # The mirror-image error: count measured 2020-07-17, price on 2020-09-30,
    # 4:1 split in between. Unadjusted the answer is wrong by the split ratio.
    naive = PS.market_cap_as_of_detail(conn, aapl, "2020-09-30", 115.81)
    check("the count used on 2020-09-30 is the pre-split cover count",
          naive["shares_as_published"] == 4275634000.0, naive["shares_as_published"])
    check("unadjusted, the product is the wrong order of magnitude",
          naive["market_cap"] < 600e9, naive["market_cap"])
    corrected = PS.market_cap_as_of_detail(conn, aapl, "2020-09-30", 115.81,
                                           split_factor=4.0)
    check("un-applying the split restores it",
          1.9e12 < corrected["market_cap"] < 2.1e12, corrected["market_cap"])
    check("and the correction is recorded, not silent",
          corrected["split_check"] == PS.SPLIT_CALLER_SUPPLIED
          and corrected["split_factor"] == 4.0)
    strict = PS.market_cap_as_of_detail(conn, aapl, "2020-09-30", 115.81,
                                        require_split_check=True)
    check("strict mode refuses an unverified split window",
          strict["market_cap"] is None
          and strict["reason"] == PS.REASON_SPLIT_UNRESOLVED, strict["reason"])

    # ======================================================================
    section("a missing count is None with a reason, never a guess")
    dead = ids[CIK_PRE_XBRL]
    check("a pre-XBRL issuer has no count",
          PS.shares_as_of(conn, dead, "2008-06-30") is None)
    why = PS.shares_as_of_detail(conn, dead, "2008-06-30")
    check("and the reason is 'never filed'",
          why["reason"] == PS.REASON_NEVER_FILED, why["reason"])
    check("no value is invented", why["shares"] is None)
    no_cap = PS.market_cap_as_of_detail(conn, dead, "2008-06-30", 20.0)
    check("market cap is None, not zero",
          no_cap["market_cap"] is None and no_cap["reason"] == PS.REASON_NEVER_FILED)
    check("and it names the valuation factor as unavailable",
          no_cap["valuation_factor"] == "unavailable")
    weights = no_cap["effective_weights_if_dropped"]
    check("carrying the frozen drop-and-renormalise weights",
          abs(weights["growth"] - 0.25 / 0.60) < 1e-9
          and abs(weights["profitability"] - 0.20 / 0.60) < 1e-9
          and abs(weights["debt"] - 0.25) < 1e-9, weights)
    check("which is 0.4167 / 0.3333 / 0.25 over 0.60",
          PS.valuation_dropped_weights()["rounded"]
          == {"growth": 0.4167, "profitability": 0.3333, "debt": 0.25})

    early = PS.shares_as_of_detail(conn, aapl, "2015-01-01")
    check("before the first filing, the reason is 'not yet filed'",
          early["shares"] is None and early["reason"] == PS.REASON_NOT_YET_FILED,
          early["reason"])
    stale = PS.shares_as_of_detail(conn, aapl, "2024-06-28")
    check("long after the last filing, the reason is staleness",
          stale["shares"] is None and stale["reason"] == PS.REASON_STALE,
          stale["reason"])
    check("the staleness bound is pit_policy's, not a local one",
          pit_policy.max_age_months(0, PS.STALENESS_CONCEPT) == 4
          and pit_policy.max_age_months(4, PS.STALENESS_CONCEPT) == 12)

    # ======================================================================
    section("availability never precedes the as-of")
    grid = ["2019-10-30", "2019-10-31", "2019-11-01", "2020-01-28", "2020-01-29",
            "2020-05-01", "2020-07-31", "2020-10-29", "2020-10-30", "2020-11-30"]
    leaks: list[tuple] = []
    for day in grid:
        for ladder in ((PS.CONCEPT_GAAP_OUTSTANDING,), PS.SHARES_LADDER_STRICT):
            record = PS.shares_as_of_detail(conn, aapl, day, ladder=ladder,
                                            enforce_staleness=False)
            if not record["available"]:
                continue
            if record["available_date"] > day or record["measured_at"] > day:
                leaks.append((day, record["available_date"], record["measured_at"]))
    check("no selected row was available after its as-of date", not leaks, leaks)

    # The canary: a row whose filing had not landed must be invisible even
    # though its measurement date is in the past.
    on_filing_day = PS.shares_as_of_detail(conn, aapl, "2020-10-29",
                                           ladder=(PS.CONCEPT_GAAP_OUTSTANDING,),
                                           enforce_staleness=False)
    check("the day before the restatement filed, it is still the old number",
          on_filing_day["shares"] == 4443236000.0, on_filing_day["shares"])
    day_after = PS.shares_as_of_detail(conn, aapl, "2020-10-30",
                                       ladder=(PS.CONCEPT_GAAP_OUTSTANDING,),
                                       enforce_staleness=False)
    check("the day it filed, the restatement is visible",
          day_after["shares"] == 17772945000.0, day_after["shares"])
    check("accepted 16:33 ET on 2020-10-29, so it lands on the next session -- "
          "which EDGAR had already made its filing date",
          day_after["available_date"] == "2020-10-30"
          and day_after["availability_rule"] == "next_session"
          and day_after["filed"] == "2020-10-30",
          (day_after["available_date"], day_after["availability_rule"]))

    # The contrast that shows why the submissions join is worth one extra call
    # per issuer: the cover-page fixtures carry NO acceptance timestamp, so the
    # policy takes its pessimistic branch -- next session after `filed`, always.
    cover_on_filing_day = PS.shares_as_of_detail(
        conn, aapl, "2020-07-31", ladder=(PS.CONCEPT_DEI_COVER,))
    cover_next_day = PS.shares_as_of_detail(
        conn, aapl, "2020-08-03", ladder=(PS.CONCEPT_DEI_COVER,))
    check("with no acceptance timestamp a fact is NOT available on its filing day",
          cover_on_filing_day["shares"] is None
          and cover_on_filing_day["reason"] == PS.REASON_NOT_YET_FILED,
          cover_on_filing_day["reason"])
    check("it lands the next session, always",
          cover_next_day["shares"] == 4275634000.0
          and cover_next_day["available_date"] == "2020-08-01",
          (cover_next_day["shares"], cover_next_day["available_date"]))

    # ======================================================================
    section("a genuine zero is not a market cap of zero")
    zero = PS.shares_as_of_detail(conn, ids[CIK_ZERO], "2015-06-30")
    check("the zero is returned, because it is true",
          zero["available"] and zero["shares"] == 0.0, zero["shares"])
    check("and flagged", zero["zero_count"] is True
          and zero.get("warning") == PS.REASON_ZERO_SHARE_COUNT)
    zero_cap = PS.market_cap_as_of_detail(conn, ids[CIK_ZERO], "2015-06-30", 12.0)
    check("market cap refuses it rather than returning $0",
          zero_cap["market_cap"] is None
          and zero_cap["reason"] == PS.REASON_ZERO_SHARE_COUNT, zero_cap["reason"])
    allowed = PS.market_cap_as_of_detail(conn, ids[CIK_ZERO], "2015-06-30", 12.0,
                                         allow_zero_shares=True)
    check("unless the caller explicitly asks for it",
          allowed["market_cap"] == 0.0, allowed["market_cap"])

    # ======================================================================
    section("issued is an upper bound, not a silent fallback")
    issued = ids[CIK_ISSUED_ONLY]
    check("the strict ladder will not use it",
          PS.shares_as_of(conn, issued, "2019-09-30") is None)
    opted = PS.shares_as_of_detail(conn, issued, "2019-09-30",
                                   ladder=PS.SHARES_LADDER_WITH_ISSUED)
    check("the opt-in ladder does", opted["available"] and opted["shares"] == 120e6)
    check("and marks the answer an upper bound", opted["upper_bound"] is True)
    check("the concept itself declares it",
          PS.concept_for(PS.CONCEPT_GAAP_ISSUED).upper_bound is True)

    # ======================================================================
    section("coverage measurement")
    report = PS.coverage_report(conn, list(ids.values()),
                                ["2015-06-30", "2019-12-31", "2024-06-28"])
    check("coverage counts the whole sample, survivors and dead alike",
          all(report["as_of"][d]["n"] == len(ids) for d in report["as_of"]))
    check("the average-only issuer shows as unavailable, with its own reason",
          report["reasons"].get(PS.REASON_ONLY_PERIOD_AVERAGE, 0) >= 1,
          report["reasons"])
    check("2019-12-31 finds the AAPL count",
          report["as_of"]["2019-12-31"]["available"] >= 1)

    # ======================================================================
    section("nothing here touched a live database")
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    check("the throwaway store holds no production tables",
          not ({"assets", "current_scores", "score_history", "positions"} & tables))
    check("the test database is under the temp directory",
          os.path.abspath(db_path).startswith(os.path.abspath(tempfile.gettempdir())))
    check("it is not the production PIT store",
          os.path.abspath(db_path)
          != os.path.abspath(pit_store.DEFAULT_PIT_DB_PATH))

    # ======================================================================
    section("live confirmation: are the fixtures still what the SEC publishes?")
    if offline:
        skip("live fixture re-verification", "(--offline)")
    else:
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            fetched = PS.fetch_share_concept(
                CIK_AAPL, PS.concept_for(PS.CONCEPT_GAAP_OUTSTANDING), CACHE_DIR)
            live = sorted(
                ((r["val"], r["accn"]) for r in fetched["rows"]
                 if r.get("end") == "2019-09-28"),
                key=lambda pair: pair[1])
            expected = sorted(((v, a) for v, a, _f, _d, _t in AAPL_2019_INSTANT),
                              key=lambda pair: pair[1])
            check("companyconcept reachable", fetched["status"] == 200, fetched["status"])
            check("the five vintages of 2019-09-28 still match the fixture",
                  live == expected, (live, expected))
            check("`frame` is stripped before anything sees the rows",
                  all("frame" not in r for r in fetched["rows"]))

            lehman = PS.fetch_share_concept(
                CIK_PRE_XBRL, PS.concept_for(PS.CONCEPT_DEI_COVER), CACHE_DIR)
            check("Lehman still 404s on the cover count (pre-XBRL)",
                  lehman["status"] == 404, lehman["status"])

            alphabet_cover = PS.fetch_share_concept(
                "1652044", PS.concept_for(PS.CONCEPT_DEI_COVER), CACHE_DIR)
            alphabet_bs = PS.fetch_share_concept(
                "1652044", PS.concept_for(PS.CONCEPT_GAAP_OUTSTANDING), CACHE_DIR)
            check("a multi-class cover page is invisible to companyconcept",
                  alphabet_cover["status"] == 404, alphabet_cover["status"])
            check("but its balance-sheet count survives",
                  alphabet_bs["status"] == 200 and len(alphabet_bs["rows"]) > 0)
        except Exception as exc:                    # network down, DNS, proxy
            skip("live fixture re-verification", f"({exc})")

    print()
    print(f"CHECKS: {len(fails)} failed, {len(skips)} skipped")
    if fails:
        print("FAILURES:", fails)
        print("\nSTOP CONDITION: the point-in-time share count is not trustworthy. "
              "The valuation factor carries 40% of the equity score and must not "
              "be computed from this.")
    else:
        print("\nGATE PASSED: point-in-time share counts are pre-split by "
              "construction, period averages are never substituted for them, and "
              "a missing count is an honest None.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
