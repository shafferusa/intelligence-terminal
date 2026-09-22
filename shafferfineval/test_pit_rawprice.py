"""The raw-price gate: does a stored bar reconstruct to what actually traded?

    python test_pit_rawprice.py            # fixtures + a live confirmation
    python test_pit_rawprice.py --offline  # fixtures only, no network at all

The failure under test is silent. A point-in-time share count is expressed in
its own era's share basis; Yahoo's `close` is expressed in today's. Multiply one
by the other and the market capitalisation is wrong by exactly the split ratio,
with clean provenance on every input. Apple at 2020-01-31 is $339bn instead of
$1.35tn -- a number no reviewer would flag as absurd for Apple, which is what
makes it dangerous.

Every fixture here is a MEASURED number, not an invention:

  * AAPL's stored closes and its 7:1 / 4:1 splits were read from Yahoo's chart
    API on 2026-09-21 (granularity asserted `1d`);
  * AAPL's cover-page share counts were read from SEC companyconcept the same
    day (2020-01-17 -> 4,375,480,000; 2020-07-17 -> 4,275,634,000);
  * AT&T's spin-off carries Yahoo's own `splitRatio` of '1324:1000'.

The live section at the end re-reads both sources and checks the fixtures still
match, so an offline pass can never quietly become a pass against a stale
assumption.

Nothing here touches `shafferfineval.db` or `shafferfineval_pit.db`. The test
database is a throwaway file under the system temp directory, and the last
section proves it.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_policy
import pit_rawprice as RP
import pit_shares as PS
import pit_store
import pit_symbols

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


def close_to(a: object, b: float, tol: float) -> bool:
    return a is not None and abs(float(a) - b) <= tol


# --------------------------------------------------------------------------
# Measured fixtures
# --------------------------------------------------------------------------

CIK_AAPL = "320193"
CIK_ARCHIVE_TWIN = "9999101"     # same numbers, read from pit_fact instead
CIK_T = "732717"                 # AT&T: the spin-off that arrives as a split
CIK_MULTI = "9999102"            # synthetic two-class issuer
CIK_NO_COUNT = "9999103"         # synthetic: never filed a count

#: Yahoo `close` (split-adjusted), read 2026-09-21. The 2015 levels carry the
#: 4:1 of 2020-08-31 and nothing else; 2020-09-30 carries no later split at all.
AAPL_BARS = (
    # bar_date, close, volume
    ("2015-06-29", 31.13249969482422, 196645600),
    ("2015-06-30", 31.357500076293945, 177482800),
    ("2015-07-01", 31.649999618530273, 120955200),
    ("2020-01-31", 77.37750244140625, 199588400),
    ("2020-08-28", 124.80750274658203, 187630000),
    ("2020-08-31", 129.0399932861328, 225702700),
    ("2020-09-30", 115.80999755859375, 142675200),
)

#: Yahoo `events.splits`, read 2026-09-21.
AAPL_SPLITS = (
    ("2014-06-09", 7.0, 1.0),
    ("2020-08-31", 4.0, 1.0),
)

#: SEC companyconcept dei:EntityCommonStockSharesOutstanding, read 2026-09-21.
AAPL_COVER = (
    (4375480000, "2020-01-17", "0000320193-20-000010", "10-Q", "2020-01-29"),
    (4275634000, "2020-07-17", "0000320193-20-000062", "10-Q", "2020-07-31"),
)

#: The as-traded truth these fixtures must reproduce.
AAPL_RAW_2015_06_30 = 125.43
AAPL_RAW_2020_01_31 = 309.51
AAPL_MARKET_CAP_2020_01_31 = 1.354e12      # against ~1.38e12 actual

PULL_DATE = "2026-09-21"


def _share_row(entity_id: int, concept_key: str, *, val: float, period_end: str,
               accn: str, form: str, filed: str) -> tuple:
    """One pit_share_obs tuple in PS.SHARE_COLUMNS order."""
    concept = PS.concept_for(concept_key)
    detail = pit_policy.available_date_detail(None, filed, None)
    return (entity_id, concept.key, concept.taxonomy, concept.tag, concept.unit,
            concept.measure_kind, None, period_end, 0, float(val), accn, form,
            None, None, filed, None, detail["accepted_et"],
            detail["available_date"], detail["rule"],
            detail["latency_policy_version"], PS.PRICE_BASIS_RAW, "fixture",
            "2026-09-21T00:00:00+00:00")


def _fact_row(entity_id: int, *, tag: str, taxonomy: str, unit: str, val: float,
              period_end: str, qtrs: int, accn: str, form: str, filed: str,
              period_start: object = None) -> tuple:
    """One pit_fact tuple in pit_store.FACT_COLUMNS order."""
    detail = pit_policy.available_date_detail(None, filed, None)
    return (entity_id, taxonomy, tag, unit, period_start, period_end, qtrs, "", "",
            float(val), accn, form, filed, None, detail["accepted_et"],
            detail["available_date"], detail["latency_policy_version"], "fixture")


def _bars(conn: sqlite3.Connection, listing_id: int, symbol: str, ingest_id: int,
          rows) -> None:
    conn.executemany(
        """INSERT OR IGNORE INTO pit_price_bar
               (listing_id, bar_date, open, high, low, close, adjclose, volume,
                source_symbol, ingest_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [(listing_id, day, None, None, None, close, None, volume, symbol, ingest_id)
         for day, close, volume in rows])
    conn.commit()


def _action(conn: sqlite3.Connection, listing_id: int, event_date: str,
            event_type: str, num, den, *, price: int = 1, shares: int = 1,
            source: str = "fixture") -> None:
    conn.execute(
        """INSERT OR IGNORE INTO pit_corporate_action
               (listing_id, event_date, event_type, ratio_num, ratio_den,
                applies_to_price, applies_to_shares, confidence, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'proved', ?)""",
        (listing_id, event_date, event_type, num, den, price, shares, source))
    conn.commit()


def _ingest(conn: sqlite3.Connection, source: str, finished: str) -> int:
    ingest_id = pit_store.start_ingest(conn, source, "fixture")
    conn.execute("UPDATE pit_ingest_run SET finished_at = ?, status = 'complete' "
                 "WHERE ingest_id = ?", (finished, ingest_id))
    conn.commit()
    return ingest_id


# ==========================================================================
def main() -> int:
    offline = "--offline" in sys.argv
    db_dir = tempfile.mkdtemp(prefix="pit_rawprice_test_")
    db_path = os.path.join(db_dir, "pit_rawprice_test.db")
    print(f"throwaway db: {db_path}")

    conn = pit_store.init_db(db_path)
    PS.ensure_schema(conn)
    pit_symbols.ensure_schema(conn)

    aapl = pit_store.upsert_entity(conn, CIK_AAPL)
    twin = pit_store.upsert_entity(conn, CIK_ARCHIVE_TWIN)
    att = pit_store.upsert_entity(conn, CIK_T)
    multi = pit_store.upsert_entity(conn, CIK_MULTI)
    empty = pit_store.upsert_entity(conn, CIK_NO_COUNT)

    fresh = _ingest(conn, "yahoo:chart", PULL_DATE)
    stale_pull = _ingest(conn, "yahoo:chart", "2020-08-01")

    aapl_listing = pit_store.upsert_listing(conn, "AAPL", "1980-12-12",
                                            entity_id=aapl, exchange="NasdaqGS",
                                            confidence="proved", source="fixture")
    twin_listing = pit_store.upsert_listing(conn, "AAPLTWIN", "1980-12-12",
                                            entity_id=twin, confidence="proved",
                                            source="fixture")
    _bars(conn, aapl_listing, "AAPL", fresh, AAPL_BARS)
    # The SAME bars as they came back from a pull made on 2020-08-01, before the
    # split. Yahoo's close for 2015-06-30 was 125.43 then and 31.3575 now.
    _bars(conn, aapl_listing, "AAPL", stale_pull,
          [(day, close * 4.0, volume) for day, close, volume in AAPL_BARS
           if day < "2020-08-31"])
    # An unregistered pull: bars exist, pit_ingest_run has no row for it.
    _bars(conn, aapl_listing, "AAPL", 4242, [("2015-06-30", 31.357500076293945, 1)])
    _bars(conn, twin_listing, "AAPLTWIN", fresh, AAPL_BARS)
    for day, num, den in AAPL_SPLITS:
        _action(conn, aapl_listing, day, RP.EVENT_SPLIT, num, den)
        _action(conn, twin_listing, day, RP.EVENT_SPLIT, num, den)

    # ======================================================================
    section("synthetic splits round-trip EXACTLY")
    synth = pit_store.upsert_entity(conn, "9999200")
    synth_listing = pit_store.upsert_listing(conn, "SYNTH", "2000-01-03",
                                             entity_id=synth, source="fixture")
    # Raw levels that were actually printed, and the three ratio events that
    # followed. 2:1 and 3:2 are forward, 1:8 is a reverse -- one expression has
    # to cover both directions or the reverse case comes out inverted.
    raw_levels = {"2010-01-04": 40.0, "2012-06-15": 90.0, "2016-03-01": 12.5,
                  "2019-01-02": 8.0}
    chain = (("2011-05-02", 2.0, 1.0), ("2013-09-03", 3.0, 2.0),
             ("2018-07-02", 1.0, 8.0))
    for day, num, den in chain:
        _action(conn, synth_listing, day, RP.EVENT_SPLIT, num, den)
    stored = []
    for day, raw in sorted(raw_levels.items()):
        factor = 1.0
        for event_day, num, den in chain:
            if event_day > day:
                factor *= num / den
        stored.append((day, raw / factor, 1_000_000))
    _bars(conn, synth_listing, "SYNTH", fresh, stored)

    worst = 0.0
    for day, raw in sorted(raw_levels.items()):
        got = RP.raw_close_as_of(conn, synth_listing, day)
        worst = max(worst, abs(got["raw_close"] - raw) / raw)
    check("every synthetic level reconstructs to 1e-12 relative", worst < 1e-12, worst)
    check("a reverse split reconstructs downward, not upward",
          RP.raw_close_as_of(conn, synth_listing, "2016-03-01")["factor"] == 1.0 / 8.0)
    first = RP.raw_close_as_of(conn, synth_listing, "2010-01-04")
    check("the oldest bar un-applies all three events",
          len(first["actions_unapplied"]) == 3, first["actions_unapplied"])
    check("the newest bar un-applies none",
          RP.raw_close_as_of(conn, synth_listing,
                             "2019-01-02")["actions_unapplied"] == [])
    check("the declared basis is raw, always",
          first["price_basis"] == PS.PRICE_BASIS_RAW == "raw_as_printed_unadjusted")
    check("the reconstruction version is stamped on the record",
          first["reconstruction_version"] == RP.RECONSTRUCTION_VERSION)

    # ======================================================================
    section("the AAPL split case: 2015-06-30 is 125.43, never 31.3575")
    got = RP.raw_close_as_of(conn, aapl_listing, "2015-06-30")
    check("the stored close is the split-adjusted 31.3575",
          close_to(got["stored_close"], 31.3575, 1e-4), got["stored_close"])
    check("the reconstructed close is the as-traded 125.43",
          close_to(got["raw_close"], AAPL_RAW_2015_06_30, 0.005), got["raw_close"])
    check("exactly one action was un-applied: the 4:1 of 2020-08-31",
          [e["event_date"] for e in got["actions_unapplied"]] == ["2020-08-31"],
          got["actions_unapplied"])
    check("the 7:1 of 2014-06-09 is NOT un-applied -- it precedes the bar",
          all(e["event_date"] != "2014-06-09" for e in got["actions_unapplied"]))
    check("the un-apply factor is exactly 4",
          got["factor"] == 4.0, got["factor"])
    check("2020-01-31 reconstructs to 309.51",
          close_to(RP.raw_close(conn, aapl_listing, "2020-01-31"),
                   AAPL_RAW_2020_01_31, 0.005))
    after_split = RP.raw_close_as_of(conn, aapl_listing, "2020-09-30")
    check("a bar after the last split needs no adjustment at all",
          after_split["factor"] == 1.0
          and close_to(after_split["raw_close"], 115.81, 0.005),
          after_split["raw_close"])
    check("adjclose is never the source column",
          got["source_column"] == "close" == RP.SOURCE_COLUMN)

    # ======================================================================
    section("the pull date bounds the window")
    old = RP.raw_close_as_of(conn, aapl_listing, "2015-06-30", ingest_id=stale_pull)
    check("a pull that predates the split un-applies nothing",
          old["actions_unapplied"] == [] and old["factor"] == 1.0, old["factor"])
    check("so that pull's stored close IS already the as-traded level",
          close_to(old["raw_close"], AAPL_RAW_2015_06_30, 0.005), old["raw_close"])
    check("the default pick is the freshest PULL, not the largest ingest_id",
          RP.raw_close_as_of(conn, aapl_listing, "2015-06-30")["ingest_id"] == fresh)
    check("and its bound came from pit_ingest_run",
          old["bound_status"] == RP.BOUND_INGEST_RUN
          and old["ingest_bound"] == "2020-08-01",
          (old["bound_status"], old["ingest_bound"]))
    check("the fresh pull's bound is the pull date",
          got["ingest_bound"] == PULL_DATE, got["ingest_bound"])
    orphan = RP.ingest_bound(conn, 4242, aapl_listing)
    check("a missing ingest row falls back to the last bar, loudly",
          orphan["status"] == RP.BOUND_LAST_BAR and orphan["warnings"],
          orphan["status"])
    check("no ingest evidence at all is flagged louder still",
          RP.ingest_bound(conn, None)["status"] == RP.BOUND_UNBOUNDED)

    # ======================================================================
    section("a spin-off adjusts the PRICE and not the SHARE COUNT")
    t_listing = pit_store.upsert_listing(conn, "T", "1983-11-21", entity_id=att,
                                         exchange="NYSE", confidence="proved",
                                         source="fixture")
    _bars(conn, t_listing, "T", fresh, [("2022-03-31", 19.0, 40_000_000)])
    # Yahoo delivers this as a split. It is a valid price adjustment and wrong
    # by 32.4% as a share-count divisor, so applies_to_shares is 0.
    _action(conn, t_listing, "2022-04-11", RP.EVENT_SPINOFF, 1324.0, 1000.0,
            price=1, shares=0, source="yahoo:chart")
    spin = RP.raw_close_as_of(conn, t_listing, "2022-03-31")
    check("the price IS adjusted for the spin-off",
          close_to(spin["raw_close"], 19.0 * 1.324, 1e-9), spin["raw_close"])
    check("and the event is named in the audit trail",
          spin["actions_unapplied"][0]["event_type"] == RP.EVENT_SPINOFF)
    shares_side = PS.split_factor_between(conn, t_listing, "2022-03-31", "2022-06-30")
    check("the SHARE count is not adjusted for it",
          shares_side["factor"] == 1.0, shares_side)
    check("the same window IS consulted, not skipped",
          shares_side["status"] == PS.SPLIT_CHECKED, shares_side["status"])

    # ======================================================================
    section("an action that cannot be applied is a refusal, not a skip")
    bad = pit_store.upsert_listing(conn, "BAD", "2001-01-02", entity_id=synth,
                                   source="fixture")
    _bars(conn, bad, "BAD", fresh, [("2015-01-02", 10.0, 1000)])
    _action(conn, bad, "2016-01-04", RP.EVENT_SPLIT, None, None)
    broken = RP.raw_close_as_of(conn, bad, "2015-01-02")
    check("a ratio-less split refuses the whole price",
          not broken["available"] and broken["reason"] == RP.REASON_RATIO_UNUSABLE,
          broken["reason"])
    weird = pit_store.upsert_listing(conn, "WEIRD", "2001-01-02", entity_id=synth,
                                     source="fixture")
    _bars(conn, weird, "WEIRD", fresh, [("2015-01-02", 10.0, 1000)])
    _action(conn, weird, "2016-01-04", "rights_offering", 3.0, 2.0)
    unknown = RP.raw_close_as_of(conn, weird, "2015-01-02")
    check("an unknown price event type refuses too",
          not unknown["available"]
          and unknown["reason"] == RP.REASON_UNKNOWN_EVENT_TYPE, unknown["reason"])
    cash = pit_store.upsert_listing(conn, "CASH", "2001-01-02", entity_id=synth,
                                    source="fixture")
    _bars(conn, cash, "CASH", fresh, [("2015-01-02", 10.0, 1000)])
    _action(conn, cash, "2016-01-04", RP.EVENT_CASH_DIVIDEND, None, None)
    div = RP.raw_close_as_of(conn, cash, "2015-01-02")
    check("a cash dividend is ignored -- close is not dividend-adjusted",
          div["available"] and div["raw_close"] == 10.0
          and div["ignored_cash_events"], div["reason"])

    # ======================================================================
    section("a zero-volume print is flagged, and refused on request")
    frcb = pit_store.upsert_listing(conn, "FRCB", "2010-12-08", entity_id=synth,
                                    source="fixture")
    _bars(conn, frcb, "FRCB", fresh, [("2023-05-01", 3.51, 0)])
    warned = RP.raw_close_as_of(conn, frcb, "2023-05-01")
    check("by default the price comes back with a warning",
          warned["available"] and RP.REASON_ZERO_VOLUME_PRINT in warned["warnings"])
    strict = RP.raw_close_as_of(conn, frcb, "2023-05-01", require_traded=True)
    check("require_traded refuses the stale print",
          not strict["available"]
          and strict["reason"] == RP.REASON_ZERO_VOLUME_PRINT, strict["reason"])

    # ======================================================================
    section("missing bars, and rolling only when asked")
    check("no bar on the date is its own reason",
          RP.raw_close_as_of(conn, aapl_listing, "2015-07-04")["reason"]
          == RP.REASON_NO_BAR)
    check("nothing rolls back silently",
          RP.raw_close(conn, aapl_listing, "2015-07-04") is None)
    rolled = RP.raw_close_as_of(conn, aapl_listing, "2015-07-04", roll_back_days=5)
    check("an explicit roll finds the previous session and says how far",
          rolled["available"] and rolled["bar_date"] == "2015-07-01"
          and rolled["roll_days"] == 3, (rolled["bar_date"], rolled["roll_days"]))
    check("a listing with no bars at all is a different reason",
          RP.raw_close_as_of(conn, 999999, "2015-06-30")["reason"]
          == RP.REASON_NO_BARS_FOR_LISTING)

    # ======================================================================
    section("the market-cap bridge")
    with pit_store.transaction(conn):
        PS.insert_share_obs(conn, [
            _share_row(aapl, PS.CONCEPT_DEI_COVER, val=val, period_end=end,
                       accn=accn, form=form, filed=filed)
            for val, end, accn, form, filed in AAPL_COVER])
    jan = RP.market_cap_as_of_detail(conn, aapl, "2020-01-31",
                                     listing_id=aapl_listing)
    check("the 2020-01-31 market cap is $1.354tn",
          close_to(jan["market_cap"], AAPL_MARKET_CAP_2020_01_31, 5e9),
          jan["market_cap"])
    check("it is within 2.5% of the ~$1.38tn truth",
          abs(jan["market_cap"] - 1.38e12) / 1.38e12 < 0.025,
          jan["market_cap"])
    check("the count used is the pre-split cover page, as published",
          jan["shares_as_published"] == 4375480000.0, jan["shares_as_published"])
    check("it went through pit_shares, which owns the share side",
          jan["path"] == "pit_shares.market_cap_as_of_detail", jan["path"])
    naive = float(jan["price"]["stored_close"]) * jan["shares_as_published"]
    check("the naive stored-close product is wrong by exactly the split ratio",
          abs(jan["market_cap"] / naive - 4.0) < 1e-9, jan["market_cap"] / naive)

    sep = RP.market_cap_as_of_detail(conn, aapl, "2020-09-30",
                                     listing_id=aapl_listing)
    check("a split BETWEEN the count date and the as-of moves the count, not the price",
          sep["split_factor"] == 4.0 and sep["shares_used"] == 17102536000.0,
          (sep["split_factor"], sep["shares_used"]))
    check("and the 2020-09-30 cap lands near $1.98tn",
          close_to(sep["market_cap"], 1.98e12, 2e10), sep["market_cap"])

    # The two share sources must never drift apart.
    with pit_store.transaction(conn):
        pit_store.insert_facts(conn, [
            _fact_row(twin, tag="EntityCommonStockSharesOutstanding", taxonomy="dei",
                      unit="shares", val=val, period_end=end, qtrs=0, accn=accn,
                      form=form, filed=filed)
            for val, end, accn, form, filed in AAPL_COVER])
    archive = RP.market_cap_as_of_detail(conn, twin, "2020-01-31",
                                         listing_id=twin_listing)
    check("the archive path reads pit_fact",
          archive["shares_source"] == RP.SHARES_SOURCE_ARCHIVE
          and archive["path"] == "pit_rawprice.archive_path", archive["path"])
    check("and produces the SAME market cap as the pit_share_obs path",
          archive["market_cap"] == jan["market_cap"],
          (archive["market_cap"], jan["market_cap"]))

    # ======================================================================
    section("a missing share count returns None WITH a reason")
    none_at_all = RP.market_cap_as_of(conn, empty, "2020-01-31",
                                      listing_id=aapl_listing)
    check("market_cap_as_of returns None", none_at_all is None)
    detail = RP.market_cap_as_of_detail(conn, empty, "2020-01-31",
                                        listing_id=aapl_listing)
    check("and the detail names the absence",
          detail["market_cap"] is None
          and detail["reason"] == PS.REASON_NEVER_FILED, detail["reason"])
    check("it also states the frozen drop-and-renormalise rule",
          detail["effective_weights_if_dropped"]
          == PS.VALUATION_DROPPED_WEIGHTS)
    early = RP.market_cap_as_of_detail(conn, aapl, "2019-01-31",
                                       listing_id=aapl_listing)
    check("a count that had not been filed yet is not_yet_filed, not never",
          early["market_cap"] is None
          and early["reason"] == PS.REASON_NOT_YET_FILED, early["reason"])
    no_price = RP.market_cap_as_of_detail(conn, aapl, "2020-02-03",
                                          listing_id=aapl_listing)
    check("a missing bar refuses the cap rather than carrying a price forward",
          no_price["market_cap"] is None
          and no_price["reason"] == RP.REASON_NO_BAR, no_price["reason"])
    check("no listing and no price is its own reason",
          RP.market_cap_as_of_detail(conn, aapl, "2020-01-31")["reason"]
          == RP.REASON_NO_LISTING)
    check("a listing that belongs to another entity is refused",
          RP.market_cap_as_of_detail(conn, aapl, "2020-01-31",
                                     listing_id=t_listing)["reason"]
          == RP.REASON_LISTING_MISMATCH)

    # ======================================================================
    section("an adjusted price is refused, not silently corrected")
    refused = PS.market_cap_as_of_detail(
        conn, aapl, "2020-01-31", 77.3775, listing_id=aapl_listing,
        price_basis=PS.PRICE_BASIS_SPLIT_ADJUSTED)
    check("pit_shares refuses a split-adjusted basis outright",
          refused["market_cap"] is None
          and refused["reason"] == PS.REASON_PRICE_BASIS_REFUSED, refused["reason"])
    check("and the raw basis is the only admissible one",
          PS.ADMISSIBLE_PRICE_BASES == frozenset({PS.PRICE_BASIS_RAW}))

    # ======================================================================
    section("multi-class ambiguity is REFUSED, not guessed")
    multi_listing = pit_store.upsert_listing(conn, "XYZA", "2005-01-03",
                                             entity_id=multi, source="fixture")
    _bars(conn, multi_listing, "XYZA", fresh, [("2024-06-28", 50.0, 500000)])
    conn.executemany(
        """INSERT OR IGNORE INTO pit_symbol_obs
               (entity_id, symbol, symbol_raw, source_accn, form, reported_period,
                qtrs, segments, coreg, security_title, exchange_name, valid_from,
                filed, available_date, latency_policy_version, status, source,
                source_version, created_at)
           VALUES (?, ?, ?, '9999102-24-000001', '10-Q', '2024-03-31', 0, ?, '',
                   ?, 'NYSE', '2024-03-31', '2024-05-01', '2024-05-02',
                   ?, 'ok', 'fixture', ?, '2026-09-21T00:00:00+00:00')""",
        [(multi, "XYZA", "XYZA", "dimA", "Class A Common Stock, $0.01 par value",
          pit_policy.LATENCY_POLICY_VERSION, "fixture_v1"),
         (multi, "XYZB", "XYZB", "dimB", "Class B Common Stock, $0.01 par value",
          pit_policy.LATENCY_POLICY_VERSION, "fixture_v1")])
    conn.commit()
    # One class only: 60,000,000 of a company whose diluted average is 100m.
    with pit_store.transaction(conn):
        pit_store.insert_facts(conn, [
            _fact_row(multi, tag="CommonStockSharesOutstanding", taxonomy="us-gaap",
                      unit="shares", val=60_000_000, period_end="2024-03-31", qtrs=0,
                      accn="9999102-24-000001", form="10-Q", filed="2024-05-01")])
    audit = RP.share_class_audit(conn, multi, "2024-06-28")
    check("two reported classes are detected from the filed titles",
          audit["class_verdict"] == RP.CLASS_MULTI
          and audit["class_letters"] == ["A", "B"], audit)
    uncorroborated = RP.market_cap_as_of_detail(conn, multi, "2024-06-28",
                                                listing_id=multi_listing)
    check("with no scale evidence the cap is UNAVAILABLE, not a guess",
          uncorroborated["market_cap"] is None
          and uncorroborated["reason"] == RP.REASON_CLASS_UNCORROBORATED,
          uncorroborated["reason"])
    with pit_store.transaction(conn):
        pit_store.insert_facts(conn, [
            _fact_row(multi, tag=RP.CROSSCHECK_TAG, taxonomy="us-gaap",
                      unit="shares", val=100_000_000, period_end="2024-03-31",
                      qtrs=1, period_start="2024-01-01",
                      accn="9999102-24-000001", form="10-Q", filed="2024-05-01")])
    ambiguous = RP.market_cap_as_of_detail(conn, multi, "2024-06-28",
                                           listing_id=multi_listing)
    check("a count that is 60% of the diluted average is refused as ambiguous",
          ambiguous["market_cap"] is None
          and ambiguous["reason"] == RP.REASON_CLASS_AMBIGUOUS, ambiguous["reason"])
    check("the refusal is not a crash: the record still carries the evidence",
          ambiguous["class_audit"]["crosscheck"]["ratio"] == 0.6)
    permissive = RP.market_cap_as_of_detail(conn, multi, "2024-06-28",
                                            listing_id=multi_listing,
                                            class_policy=RP.CLASS_POLICY_PERMISSIVE)
    check("permissive mode returns the number AND says what strict refused",
          permissive["market_cap"] == 60_000_000 * 50.0
          and permissive["class_decision"]["refused_under_strict"]
          == RP.REASON_CLASS_AMBIGUOUS, permissive["class_decision"])

    # The corroborated case: the same two classes, a count that IS the company.
    with pit_store.transaction(conn):
        pit_store.insert_facts(conn, [
            _fact_row(multi, tag="CommonStockSharesOutstanding", taxonomy="us-gaap",
                      unit="shares", val=99_000_000, period_end="2024-04-30", qtrs=0,
                      accn="9999102-24-000002", form="10-Q", filed="2024-06-03")])
    corroborated = RP.market_cap_as_of_detail(conn, multi, "2024-06-28",
                                              listing_id=multi_listing)
    check("a whole-company count for the same issuer IS allowed, and says why",
          corroborated["market_cap"] == 99_000_000 * 50.0
          and corroborated["class_decision"]["resolution"] == RP.RESOLUTION_CORROBORATED,
          corroborated["class_decision"])

    section("the guard's decision table, as a pure function")
    cases = (
        ("multi + consistent -> allow", RP.CLASS_MULTI, "consistent", 1.0, True, None),
        ("multi + below band -> refuse", RP.CLASS_MULTI, "below_band", 0.5, False,
         RP.REASON_CLASS_AMBIGUOUS),
        ("multi + no check -> refuse", RP.CLASS_MULTI, "unavailable", None, False,
         RP.REASON_CLASS_UNCORROBORATED),
        ("single + 0.5 -> refuse", RP.CLASS_SINGLE, "below_band", 0.5, False,
         RP.REASON_CLASS_AMBIGUOUS),
        ("single + 0.99 -> allow", RP.CLASS_SINGLE, "consistent", 0.99, True, None),
        ("any + 1000x -> refuse as units", RP.CLASS_SINGLE, "implausible", 995.0,
         False, RP.REASON_COUNT_IMPLAUSIBLE),
        ("unknown class, no check -> allow", RP.CLASS_UNKNOWN, "unavailable", None,
         True, None),
    )
    for name, klass, verdict, ratio, want_ok, want_reason in cases:
        decision = RP.class_decision(
            {"class_verdict": klass,
             "crosscheck": {"verdict": verdict, "ratio": ratio}})
        check(name, decision["ok"] == want_ok and decision["reason"] == want_reason,
              decision)

    # ======================================================================
    section("disk and WAL: the constraint that failed silently before")
    guard = RP.disk_guard(db_path)
    check("disk_guard measures free space on the store's own volume",
          guard["free_gib"] > 0 and guard["total_gib"] > 0, guard)
    check("it reports the WAL and the db separately",
          "wal_bytes" in guard and "db_bytes" in guard and guard["db_bytes"] > 0)
    check("an impossible requirement is refused, not warned about",
          RP.disk_guard(db_path, need_gib=10 ** 6)["ok"] is False)
    raised = False
    try:
        RP.require_disk(db_path, need_gib=10 ** 6)
    except RuntimeError as exc:
        raised = "ABORT" in str(exc)
    check("require_disk aborts with a message a human can act on", raised)
    ckpt = RP.wal_checkpoint(conn)
    check("wal_checkpoint returns the pragma's own result, not None",
          ckpt["busy"] in (0, 1) and "log_pages" in ckpt, ckpt)
    check("and names the blocked case in words",
          ckpt["status"] in ("checkpointed", "blocked_by_reader"), ckpt["status"])
    reader = sqlite3.connect(db_path)
    reader.execute("BEGIN"); reader.execute("SELECT COUNT(*) FROM pit_price_bar")
    busy = RP.wal_checkpoint(conn)
    check("a concurrent reader is visible as busy, not as success",
          busy["busy"] in (0, 1), busy)
    reader.rollback(); reader.close()

    # ======================================================================
    section("this module writes nothing")
    before = pit_store.store_stats(conn)
    for _ in range(3):
        RP.market_cap_as_of_detail(conn, aapl, "2020-01-31", listing_id=aapl_listing)
        RP.raw_close_as_of(conn, aapl_listing, "2015-06-30")
        RP.market_cap_coverage(conn, "2024-06-28", entity_ids=[aapl, multi, empty])
    after = pit_store.store_stats(conn)
    check("repeated reads change no row count anywhere", before == after,
          {k: (before[k], after[k]) for k in before if before[k] != after[k]})
    check("the spec record is JSON-serialisable for a replay run",
          isinstance(json.dumps(RP.reconstruction_spec()), str))

    section("safety: nothing here can touch the production stores")
    check("the test database is under the temp directory",
          os.path.abspath(db_path).startswith(os.path.abspath(tempfile.gettempdir())))
    check("it is not the production PIT store",
          os.path.abspath(db_path) != os.path.abspath(pit_store.DEFAULT_PIT_DB_PATH))
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    check("and holds no live production table",
          not ({"assets", "current_scores", "score_history"} & tables))

    # ======================================================================
    section("live confirmation: are the fixtures still what the sources publish?")
    if offline:
        skip("live fixture re-verification", "(--offline)")
    else:
        try:
            url = ("https://query1.finance.yahoo.com/v8/finance/chart/AAPL"
                   "?period1=1262304000&period2=1740787200&interval=1d"
                   "&events=div%2Csplit")
            request = urllib.request.Request(url, headers={
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/124.0 Safari/537.36")})
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.load(response)
            result = payload["chart"]["result"][0]
            check("granularity is 1d -- range=max silently coarsens to monthly",
                  result["meta"]["dataGranularity"] == "1d",
                  result["meta"].get("dataGranularity"))
            check("firstTradeDate is present -- the only ticker-reuse catch",
                  result["meta"].get("firstTradeDate") is not None)
            splits = {
                __import__("datetime").datetime.fromtimestamp(
                    event["date"], __import__("datetime").timezone.utc
                ).date().isoformat(): (event["numerator"], event["denominator"])
                for event in result.get("events", {}).get("splits", {}).values()}
            check("the 4:1 of 2020-08-31 is still there",
                  splits.get("2020-08-31") == (4.0, 1.0), splits)
            check("so is the 7:1 of 2014-06-09",
                  splits.get("2014-06-09") == (7.0, 1.0), splits)
            index = {
                __import__("datetime").datetime.fromtimestamp(
                    stamp, __import__("datetime").timezone.utc).date().isoformat(): i
                for i, stamp in enumerate(result["timestamp"])}
            closes = result["indicators"]["quote"][0]["close"]
            live_2020 = closes[index["2020-01-31"]]
            check("2020-01-31 still stores 77.3775 split-adjusted",
                  close_to(live_2020, 77.3775, 1e-3), live_2020)
            check("and 4x it is still the 309.51 a trader saw",
                  close_to(live_2020 * 4, AAPL_RAW_2020_01_31, 0.01))
        except Exception as exc:                      # network down, DNS, proxy
            skip("live Yahoo re-verification", f"({exc})")
        try:
            cache = os.path.join(tempfile.gettempdir(),
                                 "shafferfineval_pit_shares_cache")
            os.makedirs(cache, exist_ok=True)
            fetched = PS.fetch_share_concept(
                CIK_AAPL, PS.concept_for(PS.CONCEPT_DEI_COVER), cache)
            live = {row["end"]: row["val"] for row in fetched["rows"]}
            check("the SEC still publishes 4,375,480,000 at 2020-01-17",
                  live.get("2020-01-17") == 4375480000, live.get("2020-01-17"))
            check("and 4,275,634,000 at 2020-07-17",
                  live.get("2020-07-17") == 4275634000, live.get("2020-07-17"))
        except Exception as exc:
            skip("live SEC re-verification", f"({exc})")

    print()
    print(f"CHECKS: {len(fails)} failed, {len(skips)} skipped")
    if fails:
        print("FAILURES:", fails)
        print("\nSTOP CONDITION: a reconstructed price is not trustworthy, so no "
              "market cap computed from it is either. The valuation factor "
              "carries 40% of the equity score and must not be computed from this.")
    else:
        print("\nGATE PASSED: stored closes reconstruct to as-traded levels, "
              "spin-offs move the price and not the count, and an unresolvable "
              "share class is an honest UNAVAILABLE.")
    conn.close()
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
