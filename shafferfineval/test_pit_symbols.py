"""The look-ahead gate for historical trading symbols.

    python test_pit_symbols.py

OFFLINE. Every row below is a synthetic fixture built from values MEASURED in
the 2026-09-20 pilot against the SEC Financial Statement and Notes data sets,
then frozen. That split is deliberate: `test_pit_identity.py` goes to the live
network because the defects it guards are properties of the SOURCES, while the
defects guarded here are properties of the SELECTOR -- a date comparison, an
ORDER BY, a table a replay must not read. Those need a fixture that cannot move
under the test, so that a failure means the code changed and nothing else.

The fixture's awkward values are all real and all cost something to get wrong:

    Berkshire's 2023-03-01 8-K reports THIRTEEN symbols in one accession --
    BRK.A, BRK.B and eleven senior notes tagged BRK23 ... BRK59, every one of
    them on the NYSE. Eleven of the thirteen are debt.
    Microsoft's 2023-01-24 8-K reports the symbol MSFT three times: once as
    common stock and twice as listed notes.
    Old GM (Motors Liquidation Co) reports `CK0000040730` in 2013 and `MTLQQ`
    from 2015 -- an EDGAR placeholder and a pink-sheet husk.
    Bed Bath & Beyond filed its symbol as the literal lower-case `bbby`.
    Berkshire filed `BRKA` through 2019 and `BRK.A` from 2022; Yahoo's symbol
    is `BRK-A`. No two of those three strings are equal.

Nothing here touches `shafferfineval.db` or `shafferfineval_pit.db`. The
database is a throwaway file under the system temp directory and the test
asserts that before it finishes.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_identity
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
# Fixture
# --------------------------------------------------------------------------

#: A tiny session calendar so the latency policy resolves to real sessions.
SESSIONS = [d for d in (
    "2013-11-07", "2013-11-08", "2015-11-12", "2015-11-13",
    "2019-05-06", "2019-05-07", "2022-01-25", "2022-01-26",
    "2023-01-24", "2023-01-25", "2023-02-27", "2023-02-28",
    "2023-03-01", "2023-03-02", "2023-03-30", "2023-03-31",
    "2023-06-29", "2023-06-30",
)]


def next_session(day: str):
    for s in SESSIONS:
        if s >= day:
            return s
    return None


def build(conn: sqlite3.Connection) -> dict[str, int]:
    """Entities, one SIC filing each, and the symbol observations."""
    ids = {}
    for key, cik in (("brk", "1067983"), ("msft", "789019"), ("oldgm", "40730"),
                     ("bbby", "886158"), ("sears", "1310067"), ("meta", "1326801")):
        ids[key] = pit_store.upsert_entity(conn, cik)
        # An annual filing inside the 400-day peer window for a 2023-06-30 as-of.
        pit_store.add_entity_sic(conn, ids[key], "6331" if key == "brk" else "7372",
                                 "2023-02-27", f"acc-sic-{key}", "fixture")
    return ids


def seed_observations(conn: sqlite3.Connection, ids: dict[str, int]) -> None:
    def obs(entity, raw, accn, filed, accepted, title=None, exch=None,
            dimh="0x00000000", form="8-K", period=None):
        return PS.record_symbol_obs(
            conn, ids[entity], raw, accn, filed, accepted=accepted, form=form,
            reported_period=period or filed, segments=dimh,
            security_title=title, exchange_name=exch,
            source=f"{PS.SOURCE_DERA_NOTES}:fixture", next_session=next_session)

    # Berkshire's 2023-03-01 8-K: two share classes and eleven senior notes.
    for sym, title, dim in [
        ("BRK.A", "ClassA Common Stock", "0x4fe5"),
        ("BRK.B", "ClassB Common Stock", "0xb907"),
        ("BRK23", "0.750% Senior Notes due 2023", "0x707d"),
        ("BRK24", "1.300% Senior Notes due 2024", "0xf898"),
        ("BRK25", "0.000% Senior Notes due 2025", "0xdaa6"),
        ("BRK27", "1.125% Senior Notes due 2027", "0x967d"),
        ("BRK28", "2.150% Senior Notes due 2028", "0x545d"),
        ("BRK30", "1.500% Senior Notes due 2030", "0xc9a2"),
        ("BRK34", "2.000% Senior Notes due 2034", "0x379a"),
        ("BRK35", "1.625% Senior Notes due 2035", "0xd26f"),
        ("BRK39", "2.375% Senior Notes due 2039", "0x57b2"),
        ("BRK41", "0.500% Senior Notes due 2041", "0xaf61"),
        ("BRK59", "2.625% Senior Notes due 2059", "0xdbde"),
    ]:
        obs("brk", sym, "0001193125-23-056259", "2023-03-01",
            "2023-03-01 09:12:00.0", title, "NYSE", dim)
    # ... and the pre-rule form of the SAME issuer: no title tag existed.
    obs("brk", "BRKA", "0001193125-19-137433", "2019-05-06",
        "2019-05-06 16:41:00.0", None, None, "0x00000000", "10-Q")

    # Microsoft reports MSFT three times, twice as notes.
    obs("msft", "MSFT", "0001193125-23-014230", "2023-01-24",
        "2023-01-24 08:49:00.0", "Common stock, $0.00000625 par value per share",
        "NASDAQ", "0x5982")
    obs("msft", "MSFT", "0001193125-23-014230", "2023-01-24",
        "2023-01-24 08:49:00.0", "2.625% Notes due 2033", "NASDAQ", "0x0263")
    obs("msft", "MSFT", "0001193125-23-014230", "2023-01-24",
        "2023-01-24 08:49:00.0", "3.125% Notes due 2028", "NASDAQ", "0xfe8e")

    # Old GM: an EDGAR placeholder, then a pink-sheet husk. Both filed, neither
    # a listed common line.
    obs("oldgm", "CK0000040730", "0001193125-13-433150", "2013-11-07",
        "2013-11-07 16:05:00.0", None, None, "0x00000000", "10-Q")
    obs("oldgm", "MTLQQ", "0001193125-15-374638", "2015-11-12",
        "2015-11-12 14:02:00.0", None, None, "0x00000000", "10-Q")

    # Bed Bath & Beyond, filed lower case.
    obs("bbby", "bbby", "0001193125-23-084725", "2023-03-30",
        "2023-03-30 16:31:00.0", "Common stock, $.01 par value", "NASDAQ")

    # A genuine ticker CHANGE, for the backwards-leak canary.
    obs("meta", "FB", "0001326801-22-000018", "2022-01-25",
        "2022-01-25 07:30:00.0", "Common stock, $0.000006 par value", "NASDAQ")
    obs("meta", "META", "0001326801-23-000013", "2023-02-27",
        "2023-02-27 07:30:00.0", "Common stock, $0.000006 par value", "NASDAQ")


def seed_tradeable(conn: sqlite3.Connection, entity_id: int, symbol: str) -> int:
    """A resolved listing plus a live bar, so the scored universe can see it."""
    listing_id = pit_store.upsert_listing(
        conn, symbol, "2000-01-03", entity_id=entity_id, exchange="NYSE",
        instrument_type="EQUITY", currency="USD", valid_from="2000-01-03",
        confidence=pit_identity.CONFIDENCE_INFERRED, source="fixture")
    conn.execute(
        """INSERT OR IGNORE INTO pit_price_bar
               (listing_id, bar_date, close, adjclose, volume, source_symbol, ingest_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (listing_id, "2023-06-29", 100.0, 100.0, 1_000_000.0, symbol, 1))
    conn.commit()
    return listing_id


# --------------------------------------------------------------------------

def main() -> int:
    handle, db_path = tempfile.mkstemp(prefix="pit_symbols_test_", suffix=".db")
    os.close(handle)
    os.unlink(db_path)
    conn = pit_store.init_db(db_path)

    print("SCHEMA")
    first = PS.ensure_schema(conn)
    second = PS.ensure_schema(conn)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    check("ensure_schema creates the three tables",
          {"pit_symbol_obs", "pit_symbol_interval", "pit_listing_status"} <= tables,
          sorted(tables))
    check("ensure_schema is idempotent", second == "already present",
          (first, second))

    ids = build(conn)
    seed_observations(conn, ids)

    print()
    print("1. ACCESSION-LEVEL OBSERVATIONS ROUND-TRIP")
    n = conn.execute("SELECT COUNT(*) AS n FROM pit_symbol_obs").fetchone()["n"]
    # 13 Berkshire securities + 1 pre-rule BRKA + 3 Microsoft + 2 old GM
    # + 1 BBBY + 2 Meta.
    check("every seeded observation stored", n == 22, n)
    brk = PS.symbol_observations(conn, ids["brk"])
    accns = {r["source_accn"] for r in brk}
    check("an observation carries its own accession, not a collapsed ticker",
          accns == {"0001193125-23-056259", "0001193125-19-137433"}, accns)
    check("thirteen symbols share ONE accession",
          sum(1 for r in brk if r["source_accn"] == "0001193125-23-056259") == 13)
    bb = PS.symbol_observations(conn, ids["bbby"])[0]
    check("lower-case `bbby` normalises to BBBY", bb["symbol"] == "BBBY", bb["symbol"])
    check("the raw filed string is preserved verbatim",
          bb["symbol_raw"] == "bbby", bb["symbol_raw"])
    check("no valid_to column exists on the raw table",
          "valid_to" not in {r[1] for r in conn.execute(
              "PRAGMA table_info(pit_symbol_obs)")})
    dup = PS.record_symbol_obs(
        conn, ids["bbby"], "bbby", "0001193125-23-084725", "2023-03-30",
        accepted="2023-03-30 16:31:00.0", segments="0x00000000",
        security_title="Common stock, $.01 par value", next_session=next_session)
    check("re-recording the same filing is a no-op", dup == "duplicate", dup)
    # The same accession under a DIFFERENT dimension is a different observation.
    # That is what lets one Berkshire 8-K carry thirteen securities.
    other_dim = PS.record_symbol_obs(
        conn, ids["bbby"], "bbby", "0001193125-23-084725", "2023-03-30",
        accepted="2023-03-30 16:31:00.0", segments="0xdeadbeef",
        security_title="6.000% Senior Notes due 2044", next_session=next_session)
    check("a different dimension on the same accession is NOT a duplicate",
          other_dim == f"inserted:{PS.OBS_OK}", other_dim)
    check("...and the note it describes is excluded from the common-stock answer",
          (PS.symbol_as_of(conn, ids["bbby"], "2023-03-31") or {})["security_title"]
          == "Common stock, $.01 par value")

    print()
    print("2. IMMUTABILITY")
    check("UPDATE on pit_symbol_obs is refused",
          raises(lambda: conn.execute(
              "UPDATE pit_symbol_obs SET symbol = 'X' WHERE obs_id = ?",
              (bb["obs_id"],))))
    conn.rollback()
    check("DELETE on pit_symbol_obs is refused",
          raises(lambda: conn.execute(
              "DELETE FROM pit_symbol_obs WHERE obs_id = ?", (bb["obs_id"],))))
    conn.rollback()

    print()
    print("3. AVAILABILITY COMES FROM pit_policy, NOT FROM `filed`")
    check("accepted 16:31 ET -> the NEXT session, not the filing day",
          bb["available_date"] == "2023-03-31",
          (bb["filed"], bb["accepted_raw"], bb["available_date"]))
    msft = PS.symbol_observations(conn, ids["msft"])[0]
    check("accepted 08:49 ET -> the SAME session",
          msft["available_date"] == "2023-01-24", msft["available_date"])
    check("the latency policy version is recorded on every row",
          {r["latency_policy_version"] for r in PS.symbol_observations(conn, ids["brk"])}
          == {pit_store.LATENCY_POLICY_VERSION})

    print()
    print("4. THE SELECTOR NEVER RETURNS THE FUTURE")
    leaks = conn.execute(
        "SELECT COUNT(*) AS n FROM pit_symbol_obs WHERE available_date < filed"
    ).fetchone()["n"]
    check("no stored row is available before it was filed", leaks == 0, leaks)
    for entity in ids:
        for as_of in ("2013-01-01", "2015-01-01", "2019-06-30", "2022-06-30",
                      "2023-03-30", "2023-06-30"):
            row = PS.symbol_as_of(conn, ids[entity], as_of)
            if row is not None and row["available_date"] > as_of:
                check(f"LEAK {entity} @ {as_of}", False, dict(row))
    check("no (entity, as-of) pair returns an observation from the future", True)
    check("BBBY is invisible on its own filing day (accepted 16:31)",
          PS.symbol_as_of(conn, ids["bbby"], "2023-03-30") is None)
    check("BBBY is visible the next session",
          (PS.symbol_as_of(conn, ids["bbby"], "2023-03-31") or {})["symbol"] == "BBBY")
    check("nothing is returned before the first filing",
          PS.symbol_as_of(conn, ids["brk"], "2019-05-05") is None)

    print()
    print("5. A LATER FILING'S SYMBOL DOES NOT LEAK BACKWARDS")
    before = PS.symbol_as_of(conn, ids["meta"], "2023-02-26")
    after = PS.symbol_as_of(conn, ids["meta"], "2023-02-27")
    check("before the rename the answer is FB",
          before is not None and before["symbol"] == "FB",
          before["symbol"] if before else None)
    check("on the rename's availability date the answer is META",
          after is not None and after["symbol"] == "META",
          after["symbol"] if after else None)
    check("the FB answer cites the FB filing",
          before["source_accn"] == "0001326801-22-000018", before["source_accn"])
    check("Berkshire at 2022-06-30 is still the pre-rule BRKA, not BRK.A",
          (PS.symbol_as_of(conn, ids["brk"], "2022-06-30") or {})["symbol"] == "BRKA")

    print()
    print("6. SHARE CLASSES: ELEVEN OF BERKSHIRE'S THIRTEEN ARE DEBT")
    brk_now = PS.symbols_as_of(conn, ids["brk"], "2023-06-30")
    got = sorted(r["symbol"] for r in brk_now)
    check("only the two common classes survive the class filter",
          got == ["BRK.A", "BRK.B"], got)
    check("symbol_as_of picks a common class, never a note",
          PS.symbol_as_of(conn, ids["brk"], "2023-06-30")["symbol"] in ("BRK.A", "BRK.B"))
    every = sorted(r["symbol"] for r in PS.symbols_as_of(
        conn, ids["brk"], "2023-06-30", require_common=False))
    check("require_common=False still returns all thirteen", len(every) == 13, len(every))
    msft_now = PS.symbols_as_of(conn, ids["msft"], "2023-06-30")
    check("Microsoft's one common row is kept and its two note rows dropped",
          len(msft_now) == 1
          and msft_now[0]["security_title"].lower().startswith("common stock"),
          [(r["symbol"], r["security_title"]) for r in msft_now])
    check("a pre-2019 row with NO title is still eligible (the tag did not exist)",
          (PS.symbol_as_of(conn, ids["brk"], "2019-05-07") or {})["symbol"] == "BRKA")
    check("an unknown class can be refused explicitly",
          PS.symbol_as_of(conn, ids["brk"], "2019-05-07",
                          allow_unknown_class=False) is None)

    print()
    print("7. FILED BUT NOT A TICKER")
    gm13 = PS.symbol_as_of(conn, ids["oldgm"], "2014-01-01")
    check("old GM's `CK0000040730` is never selected", gm13 is None, gm13)
    stored = PS.symbol_observations(conn, ids["oldgm"], include_rejected=True)
    check("...but it IS stored, with a named status",
          any(r["symbol"] == "CK0000040730"
              and r["status"] == PS.OBS_CIK_PLACEHOLDER for r in stored),
          [(r["symbol"], r["status"]) for r in stored])
    check("MTLQQ, a real filed symbol, is selected",
          (PS.symbol_as_of(conn, ids["oldgm"], "2016-01-01") or {})["symbol"] == "MTLQQ")
    check("a preferred series is flagged not_ticker_shaped",
          PS.normalise_symbol("SNV - PrE")[1] == PS.OBS_NOT_TICKER_SHAPED,
          PS.normalise_symbol("SNV - PrE"))
    check("an empty filed value is flagged empty",
          PS.normalise_symbol("")[1] == PS.OBS_EMPTY)
    check("a real ticker is ok", PS.normalise_symbol(" brk.b ") == ("BRK.B", PS.OBS_OK))

    print()
    print("8. THE DERIVED INTERVAL TABLE IS LABELLED AND IS NOT A REPLAY SOURCE")
    built = PS.build_symbol_intervals(conn)
    check("build_symbol_intervals reports a status", built["status"] == "built", built)
    flags = {r[0] for r in conn.execute(
        "SELECT DISTINCT is_derived FROM pit_symbol_interval")}
    check("every interval row is marked derived", flags == {1}, flags)
    derivs = {r[0] for r in conn.execute(
        "SELECT DISTINCT derivation FROM pit_symbol_interval")}
    check("every interval row names its derivation",
          derivs == {PS.INTERVAL_DERIVATION}, derivs)
    through = {r[0] for r in conn.execute(
        "SELECT DISTINCT derived_through FROM pit_symbol_interval")}
    check("every interval row records how far into the future it saw",
          through == {"2023-03-31"}, through)
    check("a row claiming NOT to be derived is refused by the CHECK",
          raises(lambda: conn.execute(
              """INSERT INTO pit_symbol_interval
                     (entity_id, symbol, valid_from, valid_to, first_obs_accn,
                      last_obs_accn, n_obs, is_derived, derivation,
                      derived_through, derived_at)
                 VALUES (?, 'X', '2020-01-01', NULL, 'a', 'a', 1, 0, 'forged',
                         '2020-01-01', '2020-01-01')""", (ids["brk"],))))
    conn.rollback()
    fb = [r for r in PS.symbol_intervals(conn, ids["meta"]) if r["symbol"] == "FB"]
    check("the FB interval was CLOSED by the later META filing",
          len(fb) == 1 and fb[0]["valid_to"] == "2023-02-27",
          [dict(r) for r in fb])
    # The canary: that valid_to is 2023 information sitting on a 2022 row.
    sql = PS.symbol_as_of.__doc__ or ""
    check("symbol_as_of's implementation never touches pit_symbol_interval",
          "pit_symbol_interval" not in (PS.symbols_as_of.__code__.co_consts.__str__()
                                        + str(PS.symbol_as_of.__code__.co_consts)))
    conn.execute("DELETE FROM pit_symbol_interval")
    conn.commit()
    check("...proved by deleting every interval and re-asking",
          (PS.symbol_as_of(conn, ids["meta"], "2023-02-26") or {})["symbol"] == "FB")
    PS.build_symbol_intervals(conn)

    print()
    print("9. LISTING_UNRESOLVED: OUT OF THE SCORED SET, IN THE PEER SET")
    seed_tradeable(conn, ids["brk"], "BRK-A")
    seed_tradeable(conn, ids["msft"], "MSFT")
    seed_tradeable(conn, ids["sears"], "SHLD")
    as_of = "2023-06-30"
    peers0 = {r["entity_id"] for r in PS.peer_universe_as_of(conn, as_of)}
    scored0 = {r["entity_id"] for r in PS.scored_universe_as_of(conn, as_of)}
    check("all six entities are peers before any status is set",
          peers0 == set(ids.values()), len(peers0))
    check("three of them are scoreable before any status is set",
          scored0 == {ids["brk"], ids["msft"], ids["sears"]}, len(scored0))

    PS.set_listing_status(conn, ids["sears"], PS.LISTING_UNRESOLVED,
                          reason=PS.UNRESOLVED_PRICE_GATE_FAILED,
                          evidence={"note": "SHLD is an ETF today"})
    peers1 = {r["entity_id"] for r in PS.peer_universe_as_of(conn, as_of)}
    scored1 = {r["entity_id"] for r in PS.scored_universe_as_of(conn, as_of)}
    check("an unresolved entity STAYS in the peer universe",
          ids["sears"] in peers1)
    check("the peer universe did not shrink at all", peers1 == peers0)
    check("an unresolved entity is REMOVED from the scored universe",
          ids["sears"] not in scored1)
    check("nothing else was removed", scored1 == scored0 - {ids["sears"]}, scored1)
    check("an entity with NO status row is not treated as unresolved",
          ids["brk"] in scored1 and PS.listing_status(conn, ids["brk"]) is None)
    check("unresolved_entities reports it with its reason",
          [(r["cik"], r["reason"]) for r in PS.unresolved_entities(conn)]
          == [("0001310067", PS.UNRESOLVED_PRICE_GATE_FAILED)],
          [(r["cik"], r["reason"]) for r in PS.unresolved_entities(conn)])
    PS.set_listing_status(conn, ids["sears"], PS.LISTING_RESOLVED,
                          resolved_symbol="SHLD")
    check("a status is a revisable conclusion, not a published fact",
          ids["sears"] in {r["entity_id"] for r in PS.scored_universe_as_of(conn, as_of)})
    PS.set_listing_status(conn, ids["sears"], PS.LISTING_UNRESOLVED,
                          reason=PS.UNRESOLVED_PRICE_GATE_FAILED)
    check("an unknown status string is refused",
          raises(lambda: PS.set_listing_status(conn, ids["brk"], "MAYBE")))

    print()
    print("10. A FILED SYMBOL IS A PROOF STRING, NEVER A BYPASS")
    calls: list[dict] = []

    def refusing(conn_, symbol, entity_id, as_of_, **kw):
        """Stands in for Yahoo answering 200 for a REASSIGNED ticker."""
        calls.append({"symbol": symbol, "proof": kw.get("proof")})
        return {"symbol": symbol, "entity_id": entity_id, "as_of": as_of_,
                "accepted": False, "listing_id": None,
                "confidence": pit_identity.CONFIDENCE_UNVERIFIED,
                "reason": pit_identity.REJECT_FIRST_TRADE_AFTER,
                "first_trade_date": "2026-07-17"}

    meta_listing = seed_tradeable(conn, ids["meta"], "META")

    def accepting(conn_, symbol, entity_id, as_of_, **kw):
        calls.append({"symbol": symbol, "proof": kw.get("proof")})
        return {"symbol": symbol, "entity_id": entity_id, "as_of": as_of_,
                "accepted": True, "listing_id": meta_listing,
                "confidence": (pit_identity.CONFIDENCE_PROVED if kw.get("proof")
                               else pit_identity.CONFIDENCE_INFERRED),
                "reason": pit_identity.ACCEPT_REASON,
                "first_trade_date": "1992-06-05"}

    listings_before = conn.execute(
        "SELECT COUNT(*) AS n FROM pit_listing").fetchone()["n"]
    out = PS.resolve_listing_with_filed_symbol(
        conn, ids["bbby"], "2023-03-31", resolver=refusing, persist=False)
    check("the filed symbol was handed to the price gate",
          calls and calls[-1]["symbol"] == "BBBY", calls[-1] if calls else None)
    check("...as a PROOF, carrying its accession",
          "0001193125-23-084725" in (calls[-1]["proof"] or ""), calls[-1]["proof"])
    check("a refused gate does NOT accept the listing", out["accepted"] is False)
    check("a refused gate marks the entity LISTING_UNRESOLVED",
          out["listing_status"] == PS.LISTING_UNRESOLVED
          and PS.listing_status(conn, ids["bbby"])["status"] == PS.LISTING_UNRESOLVED)
    check("a refused gate writes no pit_listing row",
          conn.execute("SELECT COUNT(*) AS n FROM pit_listing").fetchone()["n"]
          == listings_before)
    check("the reassigned-BBBY case is therefore NOT scoreable",
          ids["bbby"] not in {r["entity_id"]
                              for r in PS.scored_universe_as_of(conn, "2023-06-30")})

    out2 = PS.resolve_listing_with_filed_symbol(
        conn, ids["meta"], "2023-06-30", resolver=accepting, persist=False)
    check("a passing gate resolves the listing",
          out2["accepted"] and out2["listing_status"] == PS.LISTING_RESOLVED)
    check("a filed attribution raises confidence to `proved`",
          out2["confidence"] == pit_identity.CONFIDENCE_PROVED, out2["confidence"])
    check("the result names the filed symbol and its accession",
          out2["filed_symbol"] == "META"
          and out2["filed_symbol_accn"] == "0001326801-23-000013")

    out3 = PS.resolve_listing_with_filed_symbol(
        conn, ids["oldgm"], "2014-01-01", resolver=accepting, persist=False)
    check("old GM at 2014 has only a placeholder, so the gate is never called",
          out3["accepted"] is False
          and out3["reason"] == PS.UNRESOLVED_NO_OBSERVATION, out3["reason"])
    check("...and old GM is marked unresolved rather than given CK0000040730",
          PS.listing_status(conn, ids["oldgm"])["status"] == PS.LISTING_UNRESOLVED)

    check("the filed string is not assumed to be the price key: BRKA != BRK-A",
          PS.symbol_as_of(conn, ids["brk"], "2019-05-07")["symbol"] == "BRKA"
          and conn.execute(
              "SELECT symbol FROM pit_listing WHERE entity_id = ?",
              (ids["brk"],)).fetchone()["symbol"] == "BRK-A")

    print()
    print("11. SAFETY")
    check("the test database is a throwaway under the temp directory",
          os.path.abspath(db_path).startswith(os.path.abspath(tempfile.gettempdir())),
          db_path)
    check("the production PIT store was never opened",
          os.path.abspath(db_path) != os.path.abspath(pit_store.DEFAULT_PIT_DB_PATH))

    conn.close()
    try:
        os.unlink(db_path)
        for suffix in ("-wal", "-shm"):
            if os.path.exists(db_path + suffix):
                os.unlink(db_path + suffix)
    except OSError:
        pass

    print()
    print(f"CHECKS: {len(fails)} failed")
    print("FAILURES:", len(fails), fails if fails else "")
    if fails:
        print("\nSTOP CONDITION: the symbol selector is not point-in-time safe.")
    else:
        print("\nGATE PASSED: a symbol is an observation, availability decides, "
              "and identity that cannot be proved is excluded from scoring only.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
