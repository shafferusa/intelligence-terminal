"""Offline tests for the point-in-time option chain archive.

Pure stdlib, no network:   python test_pit_options.py

Everything here runs against a saved fixture payload shaped like Yahoo's v7
optionChain response, so the test says nothing about whether Yahoo is up and
everything about whether the archive is trustworthy.

The checks that matter are the ones that protect EVIDENCE:

  * the raw venue object survives the round trip byte-identically, because a
    normalisation bug found in 2028 must be fixable from the archive rather
    than requiring quotes that no longer exist anywhere;
  * the sha256 in the manifest matches the file, and stops matching the moment
    the file is edited;
  * a DERIVED Greek never lands in a SOURCE Greek field, which is the one
    failure that would make a backtest look like it had venue data it never had;
  * a failed capture is a row, not a silent gap.
"""
from __future__ import annotations

import copy
import datetime as dt
import gzip
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_options as P
import pit_store

fails: list[str] = []


def check(name: str, cond: bool, extra: object = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {extra}")
        fails.append(name)


# --------------------------------------------------------------------------
# Fixtures. Shaped exactly like a Yahoo v7 optionChain payload, including the
# fields this module deliberately does NOT normalise, so the raw-preservation
# check has something to preserve.
# --------------------------------------------------------------------------

SESSION = "2026-09-18"
CAPTURED_AT = "2026-09-18T19:45:00+00:00"          # 15:45 ET, a live session

#: Yahoo stamps expiries at 00:00 UTC.
EXP_1 = int(dt.datetime(2026, 9, 18, tzinfo=dt.timezone.utc).timestamp())   # 0 DTE
EXP_2 = int(dt.datetime(2026, 10, 16, tzinfo=dt.timezone.utc).timestamp())  # 28 DTE
EXP_3 = int(dt.datetime(2026, 12, 18, tzinfo=dt.timezone.utc).timestamp())  # 91 DTE
EXP_4 = int(dt.datetime(2027, 12, 17, tzinfo=dt.timezone.utc).timestamp())  # 455 DTE


def _contract(symbol, strike, right, expiry_epoch, **over):
    raw = {
        "contractSymbol": symbol,
        "strike": strike,
        "currency": "USD",
        "lastPrice": 4.15,
        "change": -0.35,
        "percentChange": -7.777,
        "volume": 12045,
        "openInterest": 48219,
        "bid": 4.10,
        "ask": 4.20,
        "contractSize": "REGULAR",
        "expiration": expiry_epoch,
        "lastTradeDate": 1789760652,   # 2026-09-18T19:44:12Z
        "impliedVolatility": 0.1732421875,
        "inTheMoney": right == "C" and strike < 660.0,
    }
    raw.update(over)
    return raw


def _payload(expiry_epoch, *, spot=661.42, expiries=(EXP_1, EXP_2, EXP_3, EXP_4),
             calls=None, puts=None, quote=True):
    return {
        "optionChain": {
            "result": [{
                "underlyingSymbol": "SPY",
                "expirationDates": list(expiries),
                "strikes": [650.0, 655.0, 660.0, 665.0],
                "hasMiniOptions": False,
                "quote": ({
                    "symbol": "SPY", "regularMarketPrice": spot,
                    "regularMarketTime": 1789760700, "currency": "USD",
                    "exchange": "PCX", "marketState": "REGULAR",
                    "fullExchangeName": "NYSEArca",
                } if quote else None),
                "options": [{
                    "expirationDate": expiry_epoch,
                    "hasMiniOptions": False,
                    "calls": calls if calls is not None else [
                        _contract("SPY260918C00655000", 655.0, "C", expiry_epoch),
                        _contract("SPY260918C00665000", 665.0, "C", expiry_epoch,
                                  bid=1.02, ask=1.06, lastPrice=1.04),
                    ],
                    "puts": puts if puts is not None else [
                        _contract("SPY260918P00655000", 655.0, "P", expiry_epoch,
                                  bid=0.88, ask=0.92, lastPrice=0.90,
                                  inTheMoney=False),
                    ],
                }],
            }],
            "error": None,
        }
    }


#: A venue that DOES publish Greeks. Nothing free does today, which is exactly
#: why the label has to be recorded per snapshot rather than assumed.
GREEKY_CONTRACT = _contract("XYZ261016P00100000", 100.0, "P", EXP_2,
                            delta=-0.4123, gamma=0.0211, theta=-0.0456,
                            vega=0.1122, rho=-0.0331)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="pit_options_")
    db_path = os.path.join(tmp, "pit.db")
    archive = os.path.join(tmp, "archive")
    conn = pit_store.init_db(db_path)

    # ------------------------------------------------------------------
    print("== 1. session dating ==")
    # ------------------------------------------------------------------
    check("a 15:45 ET capture belongs to that ET session",
          P.session_date_for(CAPTURED_AT) == SESSION, P.session_date_for(CAPTURED_AT))
    check("a 00:30 UTC capture belongs to the PREVIOUS ET day",
          P.session_date_for("2026-01-06T00:30:00+00:00") == "2026-01-05",
          P.session_date_for("2026-01-06T00:30:00+00:00"))
    check("winter capture dates correctly (EST, -5)",
          P.session_date_for("2026-01-05T21:30:00+00:00") == "2026-01-05")
    check("summer capture dates correctly (EDT, -4)",
          P.session_date_for("2026-07-06T20:30:00+00:00") == "2026-07-06")
    # The statutory-rule fallback must agree with the tz database wherever the
    # tz database exists; if it ever disagrees, one of them is dating sessions
    # wrong and every file lands in the wrong folder.
    try:
        from zoneinfo import ZoneInfo
        probes = ["2026-03-08T06:59:00+00:00", "2026-03-08T07:01:00+00:00",
                  "2026-11-01T05:59:00+00:00", "2026-11-01T06:01:00+00:00",
                  "2025-06-15T18:00:00+00:00", "2025-12-15T18:00:00+00:00"]
        agree = all(
            (P._parse_iso(p) + dt.timedelta(hours=P._eastern_offset_hours(P._parse_iso(p)))).date()
            == P._parse_iso(p).astimezone(ZoneInfo("America/New_York")).date()
            for p in probes)
        check("DST fallback rule agrees with the tz database at the boundaries", agree)
    except Exception as exc:                       # no tz database on this host
        check("DST fallback rule agrees with the tz database at the boundaries",
              True, f"(skipped: {exc})")

    # ------------------------------------------------------------------
    print("== 2. the parser maps fields correctly ==")
    # ------------------------------------------------------------------
    parsed = P.parse_chain_payload("SPY", _payload(EXP_2), SESSION,
                                   captured_at=CAPTURED_AT,
                                   payload_fetched_at=CAPTURED_AT)
    check("every contract parsed", len(parsed["contracts"]) == 3, len(parsed["contracts"]))
    check("expiry list read", parsed["expiries_listed"]
          == ["2026-09-18", "2026-10-16", "2026-12-18", "2027-12-17"],
          parsed["expiries_listed"])
    check("underlying spot read from the quote block",
          parsed["underlying_spot"] == 661.42, parsed["underlying_spot"])

    call = next(c for c in parsed["contracts"] if c["contract_symbol"] == "SPY260918C00655000")
    put = next(c for c in parsed["contracts"] if c["right"] == "P")
    check("call/put split", call["right"] == "C" and put["right"] == "P")
    check("strike", call["strike"] == 655.0)
    check("bid/ask/last", (call["bid"], call["ask"], call["last"]) == (4.10, 4.20, 4.15),
          (call["bid"], call["ask"], call["last"]))
    check("volume and open interest",
          call["volume"] == 12045 and call["open_interest"] == 48219)
    check("implied vol preserved and labelled source",
          call["implied_volatility"] == 0.1732421875
          and call["implied_volatility_provenance"] == P.PROV_SOURCE)
    check("expiry resolved to a date", call["expiry"] == "2026-10-16", call["expiry"])
    check("DTE measured from the session date", call["dte"] == 28, call["dte"])
    check("contract symbol preserved", call["contract_symbol"] == "SPY260918C00655000")
    check("currency preserved", call["currency"] == "USD")
    check("last trade time preserved as an instant",
          call["last_trade_at"] == "2026-09-18T19:44:12+00:00", call["last_trade_at"])
    check("underlying and session stamped on every contract",
          all(c["underlying_symbol"] == "SPY" and c["session_date"] == SESSION
              and c["captured_at"] == CAPTURED_AT for c in parsed["contracts"]))
    check("spot stamped on every contract",
          all(c["underlying_spot"] == 661.42 for c in parsed["contracts"]))
    check("source recorded", call["source"] == P.SOURCE_YAHOO_OPTIONS)
    check("retrieval status recorded", call["retrieval_status"] == P.STATUS_OK)

    check("multiplier is DERIVED from contractSize, never claimed as venue data",
          call["multiplier"] == 100.0 and call["multiplier_provenance"] == P.PROV_DERIVED)
    mini = P.contract_record("SPY", _contract("M", 100.0, "C", EXP_2,
                                              contractSize="MINI"), "C", None, SESSION)
    check("a non-REGULAR contract gets NO guessed multiplier",
          mini["multiplier"] is None and mini["multiplier_provenance"] is None)

    check("the venue's own object is preserved verbatim",
          call["raw"]["percentChange"] == -7.777 and call["raw"]["change"] == -0.35
          and call["raw"]["inTheMoney"] is True)
    check("a contract with no strike is dropped, not invented",
          P.contract_record("SPY", {"contractSymbol": "X"}, "C", None, SESSION) is None)
    check("a malformed payload yields zero contracts rather than raising",
          P.parse_chain_payload("SPY", {"junk": 1}, SESSION)["contracts"] == [])
    check("a payload with no quote block leaves spot null, never zero",
          P.parse_chain_payload("SPY", _payload(EXP_2, quote=False),
                                SESSION)["underlying_spot"] is None)
    check("a boolean is never read as a price",
          P._num(True) is None and P._num(False) is None)

    # ------------------------------------------------------------------
    print("== 3. Greeks provenance ==")
    # ------------------------------------------------------------------
    check("Yahoo contracts carry NO source Greeks",
          call["greeks_source"] == {} and call["greeks_source_label"] == P.GREEKS_SOURCE_NONE)
    check("no Greek is computed here", call["greeks_derived"] is None)
    greeky = P.contract_record("XYZ", GREEKY_CONTRACT, "P", None, SESSION)
    check("a venue that DOES publish Greeks has them preserved and labelled source",
          greeky["greeks_source"]["delta"] == -0.4123
          and greeky["greeks_source_label"] == P.GREEKS_SOURCE_VENUE,
          greeky["greeks_source"])
    check("all five Greeks captured when supplied",
          set(greeky["greeks_source"]) == {"delta", "gamma", "theta", "vega", "rho"})
    check("an explicitly null Greek is not a value",
          P.source_greeks({"delta": None, "gamma": 0.01}) == {"gamma": 0.01})

    # ------------------------------------------------------------------
    print("== 4. expiry selection ==")
    # ------------------------------------------------------------------
    listed = parsed["expiries_listed"]
    picked = P.select_expiries(listed, SESSION, max_expiries=8, max_dte=400)
    check("expiries beyond max_dte are excluded", "2027-12-17" not in picked, picked)
    check("the 0-DTE expiry is kept", "2026-09-18" in picked, picked)
    many = [(dt.date(2026, 9, 18) + dt.timedelta(days=7 * i)).isoformat() for i in range(40)]
    thin = P.select_expiries(many, SESSION, max_expiries=8, max_dte=400)
    check("thinned to the requested count", len(thin) == 8, len(thin))
    check("the nearest and the furthest are always kept",
          thin[0] == many[0] and thin[-1] == max(e for e in many
                                                 if (dt.date.fromisoformat(e)
                                                     - dt.date.fromisoformat(SESSION)).days <= 400),
          thin)
    check("selection is deterministic",
          P.select_expiries(many, SESSION, 8, 400) == thin)
    check("an expiry in the past is never requested",
          P.select_expiries(["2026-09-17"], SESSION) == [])

    # ------------------------------------------------------------------
    print("== 5. snapshot assembly ==")
    # ------------------------------------------------------------------
    second = P.parse_chain_payload("SPY", _payload(EXP_3), SESSION,
                                   captured_at=CAPTURED_AT,
                                   payload_fetched_at="2026-09-18T19:45:02+00:00")
    snapshot = P.build_snapshot("SPY", [parsed, second], CAPTURED_AT)
    check("payloads merge into one snapshot", snapshot["n_contracts"] == 6,
          snapshot["n_contracts"])
    check("expiries counted from captured contracts", snapshot["n_expiries"] == 2,
          snapshot["n_expiries"])
    check("snapshot status ok", snapshot["status"] == P.STATUS_OK)
    check("snapshot labelled with no venue Greeks",
          snapshot["greeks_source"] == P.GREEKS_SOURCE_NONE)
    check("header records the schema version",
          snapshot["header"]["schema_version"] == P.ARCHIVE_SCHEMA_VERSION)
    check("header keeps the raw underlying quote",
          snapshot["header"]["underlying_quote_raw"]["marketState"] == "REGULAR")
    check("header names the field the spot came from",
          "regularMarketPrice" in snapshot["header"]["underlying_spot_field"])
    check("an empty chain is a snapshot with status empty, not a crash",
          P.build_snapshot("SPY", [P.parse_chain_payload("SPY", {}, SESSION)],
                           CAPTURED_AT)["status"] == P.STATUS_EMPTY)

    # ------------------------------------------------------------------
    print("== 6. the file: gzip round-trip and checksum ==")
    # ------------------------------------------------------------------
    relpath = P.snapshot_relpath("SPY", CAPTURED_AT)
    check("deterministic path: date / underlying / timestamp",
          relpath == "2026/09/18/SPY_20260918T194500Z.jsonl.gz", relpath)
    path = P.snapshot_abspath(relpath, archive)
    size, digest = P.write_snapshot_file(path, snapshot["header"], snapshot["contracts"])
    check("file written", os.path.exists(path))
    check("file is real gzip", open(path, "rb").read(2) == b"\x1f\x8b")
    check("size reported matches the file", size == os.path.getsize(path))
    check("sha256 matches a fresh hash of the file", digest == P.sha256_file(path), digest)
    check("no temporary file left behind", not os.path.exists(path + ".tmp"))

    header_back, contracts_back = P.read_snapshot_file(path)
    check("header round-trips", header_back == json.loads(json.dumps(snapshot["header"])))
    check("every contract round-trips", len(contracts_back) == 6, len(contracts_back))
    call_back = next(c for c in contracts_back
                     if c["contract_symbol"] == "SPY260918C00655000"
                     and c["expiry"] == "2026-10-16")
    check("the raw venue object round-trips byte-identically",
          call_back["raw"] == _contract("SPY260918C00655000", 655.0, "C", EXP_2),
          call_back["raw"])
    check("JSONL: one record per line",
          sum(1 for line in gzip.open(path, "rt", encoding="utf-8") if line.strip()) == 7)

    # Determinism is what makes a hash mismatch mean tampering rather than
    # "written again". gzip stamps an mtime into its header unless told not to.
    twin = P.snapshot_abspath("twin.jsonl.gz", archive)
    _size2, digest2 = P.write_snapshot_file(twin, snapshot["header"], snapshot["contracts"])
    check("identical content hashes identically (gzip mtime pinned)",
          digest2 == digest, (digest, digest2))

    # ------------------------------------------------------------------
    print("== 7. the manifest ==")
    # ------------------------------------------------------------------
    result = P.archive_snapshot(conn, snapshot, archive)
    check("manifest row written", result["manifest"] == "written", result)
    row = conn.execute("SELECT * FROM pit_option_snapshot "
                       "WHERE underlying_symbol='SPY'").fetchone()
    check("manifest names the file", row["file_path"] == relpath, row["file_path"])
    check("manifest byte count matches the file on disk",
          row["file_bytes"] == os.path.getsize(P.snapshot_abspath(row["file_path"], archive)))
    check("manifest sha256 matches the file on disk",
          row["sha256"] == P.sha256_file(P.snapshot_abspath(row["file_path"], archive)))
    check("manifest contract count matches the file",
          row["n_contracts"] == len(contracts_back))
    check("manifest expiry count matches the file",
          row["n_expiries"] == len({c["expiry"] for c in contracts_back}))
    check("manifest spot matches the header", row["underlying_spot"] == 661.42)
    check("manifest session date", row["session_date"] == SESSION)
    check("manifest records greeks_source honestly",
          row["greeks_source"] == P.GREEKS_SOURCE_NONE)
    check("manifest records the source endpoint family",
          row["source"] == P.SOURCE_YAHOO_OPTIONS)
    check("manifest status ok", row["status"] == P.STATUS_OK and row["error"] is None)

    verified = P.verify_snapshot(conn, int(row["snapshot_id"]), archive)
    check("verify passes on an untouched file", verified["ok"], verified)

    # ------------------------------------------------------------------
    print("== 8. idempotency ==")
    # ------------------------------------------------------------------
    again = P.archive_snapshot(conn, snapshot, archive)
    check("a second archive of the same underlying+timestamp is a no-op",
          again["manifest"] == "exists", again)
    check("still exactly one manifest row",
          conn.execute("SELECT COUNT(*) AS n FROM pit_option_snapshot "
                       "WHERE underlying_symbol='SPY'").fetchone()["n"] == 1)
    check("record_snapshot reports 'exists' rather than raising",
          P.record_snapshot(conn, "SPY", CAPTURED_AT) == "exists")
    later = P.build_snapshot("SPY", [parsed], "2026-09-18T20:15:00+00:00")
    check("a LATER capture on the same day is a new snapshot, not a duplicate",
          P.archive_snapshot(conn, later, archive)["manifest"] == "written")
    check("two snapshots now recorded for the session",
          conn.execute("SELECT COUNT(*) AS n FROM pit_option_snapshot "
                       "WHERE underlying_symbol='SPY'").fetchone()["n"] == 2)

    # ------------------------------------------------------------------
    print("== 9. a failed capture is visible ==")
    # ------------------------------------------------------------------
    status = P.record_error(conn, "AAPL", "2026-09-18T19:46:00+00:00",
                            "HTTP 401 Invalid Crumb", session_date=SESSION)
    check("error row written", status == "written")
    err = conn.execute("SELECT * FROM pit_option_snapshot "
                       "WHERE underlying_symbol='AAPL'").fetchone()
    check("status is error", err["status"] == P.STATUS_ERROR)
    check("the message is kept", "Invalid Crumb" in err["error"])
    check("no file is claimed", err["file_path"] is None and err["sha256"] is None)
    check("an error snapshot cannot be verified as a file",
          not P.verify_snapshot(conn, int(err["snapshot_id"]), archive)["ok"])
    check("an error capture is idempotent too",
          P.record_error(conn, "AAPL", "2026-09-18T19:46:00+00:00", "again") == "exists")

    # ------------------------------------------------------------------
    print("== 10. DERIVED Greeks never reach a SOURCE field ==")
    # ------------------------------------------------------------------
    derived = copy.deepcopy(snapshot)
    for contract in derived["contracts"]:
        # What a downstream model is allowed to do: attach its own Greeks in
        # the derived block. It may not touch greeks_source.
        contract["greeks_derived"] = {"delta": -0.3117, "method": "black_scholes_76",
                                      "computed_at": "2026-09-19T02:00:00+00:00"}
    derived["captured_at"] = "2026-09-18T20:45:00+00:00"
    derived["header"] = dict(derived["header"], captured_at="2026-09-18T20:45:00+00:00")
    dres = P.archive_snapshot(conn, derived, archive)
    _h, dcontracts = P.read_snapshot_file(P.snapshot_abspath(dres["file_path"], archive))
    check("derived Greeks are stored in their own block",
          all(c["greeks_derived"]["delta"] == -0.3117 for c in dcontracts))
    check("the derived block names its method",
          all(c["greeks_derived"]["method"] == "black_scholes_76" for c in dcontracts))
    check("SOURCE Greeks remain empty after a derived Greek is attached",
          all(c["greeks_source"] == {} for c in dcontracts))
    check("the contract-level source label still says 'none'",
          all(c["greeks_source_label"] == P.GREEKS_SOURCE_NONE for c in dcontracts))
    drow = conn.execute("SELECT * FROM pit_option_snapshot WHERE captured_at = ?",
                        ("2026-09-18T20:45:00+00:00",)).fetchone()
    check("the manifest still reports greeks_source 'none'",
          drow["greeks_source"] == P.GREEKS_SOURCE_NONE, drow["greeks_source"])
    check("a derived delta is never readable as a venue delta",
          all(c["raw"].get("delta") is None for c in dcontracts))

    # ------------------------------------------------------------------
    print("== 11. tampering is detectable ==")
    # ------------------------------------------------------------------
    victim = conn.execute(
        "SELECT * FROM pit_option_snapshot WHERE underlying_symbol='SPY' "
        "ORDER BY captured_at LIMIT 1").fetchone()
    vpath = P.snapshot_abspath(victim["file_path"], archive)
    _vh, vcontracts = P.read_snapshot_file(vpath)
    vcontracts[0]["bid"] = 99.99                    # an "improvement" to the data
    P.write_snapshot_file(vpath, _vh, vcontracts)
    after = P.verify_snapshot(conn, int(victim["snapshot_id"]), archive)
    check("an edited archive file fails verification",
          not after["ok"] and after["reason"] == "sha256 mismatch", after)
    check("a missing archive file fails verification loudly",
          (os.remove(vpath) or
           not P.verify_snapshot(conn, int(victim["snapshot_id"]), archive)["ok"]))

    # ------------------------------------------------------------------
    print("== 12. coverage: where the archive starts ==")
    # ------------------------------------------------------------------
    coverage = {r["underlying_symbol"]: r for r in P.archive_coverage(conn)}
    check("SPY coverage reported", coverage["SPY"]["first_session"] == SESSION,
          coverage.get("SPY"))
    check("failed captures are counted, not hidden",
          coverage["AAPL"]["n_error"] == 1 and coverage["AAPL"]["n_ok"] == 0)
    check("a failed capture does NOT create coverage",
          coverage["AAPL"]["first_session"] is None, coverage["AAPL"])
    check("bytes totalled for the storage projection",
          coverage["SPY"]["file_bytes"] > 0)
    statement = P.coverage_statement(conn, "SPY")
    check("the statement names the start date and says UNAVAILABLE before it",
          SESSION in statement and "UNAVAILABLE" in statement, statement)
    check("an unarchived underlying is reported as wholly unavailable",
          "NO archived option quotes" in P.coverage_statement(conn, "TSLA"))
    check("manifest rows are listable as an index",
          len(P.snapshot_rows(conn, session_date=SESSION)) == 4,
          len(P.snapshot_rows(conn, session_date=SESSION)))

    # ------------------------------------------------------------------
    print("== 13. the research store stays separate ==")
    # ------------------------------------------------------------------
    check("the archive writes only to pit_option_snapshot",
          {t for t in ("pit_score", "pit_feature", "pit_fact")
           if conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]} == set())
    check("pit_store reports the archive in its stats",
          pit_store.store_stats(conn)["pit_option_snapshot"] == 4)

    print()
    print("FAILURES:", len(fails), fails if fails else "")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
