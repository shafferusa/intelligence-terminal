#!/usr/bin/env python3
"""ShafferFinEval -- the point-in-time option chain archive.

This module exists because of one asymmetry: every other historical source in
the project can be re-downloaded later, and expired option chains cannot. SEC
facts, DERA quarters, Yahoo daily bars and FRED vintages are all still there
next year. A listed option quote is published while the contract is alive and
is gone from every free source the day after it expires. So the archive is not
a convenience -- it is the only way real hedge research will ever have real
premiums, and every day not captured is permanently lost.

Three rules, in the order of how much damage breaking them does:

1. THE RAW CHAIN IS ARCHIVED BEFORE ANY SHAFFER CALCULATION TOUCHES IT. Each
   contract record carries the venue's own JSON object verbatim under `raw`,
   beside a normalised view. Nothing here scores, ranks, filters for
   "reasonable" quotes or repairs a crossed market. A quote that looked wrong
   on the day is evidence about the day.

2. GREEKS CARRY THEIR PROVENANCE. Yahoo's v7 option endpoint publishes no
   Greeks at all -- `option_data.py:141-143` sets delta to None for that
   reason -- so `greeks_source` on every snapshot this module writes from Yahoo
   is 'none' and the per-contract `greeks_source` block is empty. A Greek
   computed later from a model is DERIVED, belongs in `greeks_derived`, and may
   never be written into a source field. A derived delta stored as if the venue
   published it would make a backtest look like it had data it never had.

3. RAW CHAINS ARE FILES; SQLITE HOLDS ONLY A MANIFEST. Option chains are the
   one source in this project that grows without bound -- a single SPY snapshot
   is thousands of contracts and the archive gains one every trading day
   forever. Forcing those quotes through the transactional store would create
   an avoidable scaling problem in the database the research replay depends on.
   One gzipped JSONL file per snapshot, deterministically named, with a sha256
   in `pit_store.pit_option_snapshot` so a later reader can prove the file has
   not been edited since capture.

The write order is file -> checksum -> manifest row, never the reverse: a
manifest row is a claim that a specific byte sequence exists, and it is only
written once those bytes are on disk and hashed. A capture that fails records
status 'error' with the message. A failed capture must be VISIBLE -- a silent
gap and a documented outage look identical in a coverage report a year later,
and only one of them is honest.

The archive start date is discoverable from the data itself (`archive_coverage`
and `coverage_statement`), so any future hedge backtest can state plainly that
real option quotes before that date are UNAVAILABLE rather than quietly
substituting a model price.

Stdlib plus `requests`. The schema and every policy constant live in
`pit_store`; the crumb handshake lives in `option_data`. This module owns
neither and duplicates neither.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import gzip
import hashlib
import json
import os
import sqlite3
import sys
import threading
import time
from typing import Any, Callable, Iterable, Optional, Sequence

import requests

import option_data
import pit_store
from market_data import HTTP_TIMEOUT, YAHOO_HOSTS

# --------------------------------------------------------------------------
# Provenance and policy
# --------------------------------------------------------------------------

#: Bump this when the on-disk record SHAPE changes. Every file carries it in
#: its header line, so a reader never has to guess which layout it is holding.
ARCHIVE_SCHEMA_VERSION = "option_archive_v1"

#: Row provenance. The endpoint is part of it: Yahoo's v7 option chain and its
#: v8 chart endpoint disagree about the underlying's price during the session,
#: and a snapshot must say which one it recorded.
SOURCE_YAHOO_OPTIONS = "yahoo:v7_options"
OPTIONS_PATH = "v7/finance/options/{symbol}"

#: Snapshot status vocabulary, matching pit_option_snapshot.status.
STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_ERROR = "error"

#: Greek provenance. These two are never mixed. GREEKS_SOURCE_NONE is the
#: truthful answer for Yahoo and is recorded rather than left null, so a reader
#: can tell "the venue published none" from "nobody looked".
GREEKS_SOURCE_VENUE = "source"
GREEKS_SOURCE_NONE = "none"

PROV_SOURCE = "source"
PROV_DERIVED = "derived"

#: Greek names looked for in a source payload. Present-and-numeric only: a key
#: explicitly set to null is the venue saying it has no value, not a value.
SOURCE_GREEK_KEYS = ("delta", "gamma", "theta", "vega", "rho", "lambda")

#: Yahoo is polite-rate-limited by us, not by contract. The option endpoint is
#: crumb-gated and noticeably more fragile than the chart endpoint, and nothing
#: here is latency-sensitive: one call every 0.6s is roughly 1.7/s.
YAHOO_MIN_INTERVAL = 0.6

#: The default daily capture set. Deliberately small: SPY for the index hedge
#: plus five of the most liquid single names. A wider watchlist is a decision
#: about storage and politeness, so it is made on the command line, not here.
DEFAULT_WATCHLIST = ("SPY", "AAPL", "MSFT", "NVDA", "AMZN", "JPM")

#: How many expiries to capture per underlying, and how far out. SPY alone
#: lists 40+ expiries; capturing all of them daily buys term-structure detail
#: nobody has asked for at several times the storage and request cost.
DEFAULT_MAX_EXPIRIES = 8
DEFAULT_MAX_DTE = 400

#: Where the chains live under pit_store.DEFAULT_ARCHIVE_DIR. The DERA source
#: ZIPs sit beside them in `dera/`; these are not interchangeable and must not
#: share a directory.
ARCHIVE_SUBDIR = "options"

_throttle_lock = threading.Lock()
_last_call = [0.0]


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def _now_utc() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(moment: _dt.datetime) -> str:
    """An ISO timestamp to the second, matching pit_store's convention."""
    return moment.astimezone(_dt.timezone.utc).isoformat(timespec="seconds")


def _parse_iso(text: str) -> _dt.datetime:
    moment = _dt.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_dt.timezone.utc)
    return moment.astimezone(_dt.timezone.utc)


def _num(value: Any) -> Optional[float]:
    """Coerce to a finite float or None. Mirrors option_data's coercion.

    A bool is rejected on purpose: `True` is a flag the venue set, never a
    price, and float(True) == 1.0 would silently become a one-dollar bid.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def _throttle() -> None:
    with _throttle_lock:
        wait = YAHOO_MIN_INTERVAL - (time.monotonic() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.monotonic()


def _eastern_offset_hours(utc: _dt.datetime) -> int:
    """US Eastern offset under the statutory post-2007 rule: -4 or -5.

    Used only when the host has no tz database. Daylight time begins at 07:00
    UTC on the second Sunday in March and ends at 06:00 UTC on the first Sunday
    in November; expressing both boundaries as UTC instants avoids the
    ambiguous-local-hour problem entirely.
    """
    year = utc.year
    start = _nth_weekday(year, 3, 6, 2).replace(hour=7, tzinfo=_dt.timezone.utc)
    end = _nth_weekday(year, 11, 6, 1).replace(hour=6, tzinfo=_dt.timezone.utc)
    return -4 if start <= utc < end else -5


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> _dt.datetime:
    """The nth `weekday` (Mon=0..Sun=6) of a month, as a naive midnight."""
    first = _dt.date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return _dt.datetime(year, month, 1 + offset + 7 * (n - 1))


def session_date_for(captured_at: str) -> str:
    """The Eastern calendar date a UTC capture timestamp belongs to.

    A LABEL, not a claim that the market traded: a capture run on a holiday
    still gets that holiday's date, and whether it was a session is answered by
    `pit_calendar`, which is the one canonical calendar. Eastern rather than
    UTC because a 16:30 ET capture is 20:30 UTC in summer and 21:30 in winter,
    and a UTC-dated archive would scatter one session across two folders.
    """
    utc = _parse_iso(captured_at)
    try:
        from zoneinfo import ZoneInfo
        return utc.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        return (utc + _dt.timedelta(hours=_eastern_offset_hours(utc))).date().isoformat()


def _epoch_to_date(epoch: Any) -> Optional[str]:
    """An expiry epoch as a UTC date string. Yahoo stamps expiries at 00:00 UTC."""
    try:
        return _dt.datetime.fromtimestamp(int(epoch), _dt.timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _epoch_to_iso(epoch: Any) -> Optional[str]:
    try:
        return _iso(_dt.datetime.fromtimestamp(int(epoch), _dt.timezone.utc))
    except (TypeError, ValueError, OSError):
        return None


def _days_between(start: str, end: str) -> Optional[int]:
    try:
        return (_dt.date.fromisoformat(end[:10]) - _dt.date.fromisoformat(start[:10])).days
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------
# Parsing. Pure, so the whole record shape is testable without a network.
# --------------------------------------------------------------------------

def source_greeks(raw: dict) -> dict[str, float]:
    """The Greeks the VENUE published for one contract. Usually empty.

    Only keys that are present AND numeric are returned. Yahoo's v7 endpoint
    publishes none of them, so this returns {} for every Yahoo contract and the
    snapshot is honestly labelled GREEKS_SOURCE_NONE. Nothing in this module
    ever computes a Greek; a model-derived one is the caller's responsibility
    and belongs in `greeks_derived`.
    """
    out: dict[str, float] = {}
    for key in SOURCE_GREEK_KEYS:
        if key not in raw:
            continue
        value = _num(raw.get(key))
        if value is not None:
            out[key] = value
    return out


def contract_record(symbol: str, raw: dict, right: str, expiry: Optional[str],
                    session_date: str, **fields) -> Optional[dict]:
    """One archived contract: the normalised view plus the venue's own object.

    `raw` is stored by reference and never modified -- the normalised fields are
    read out of it, not moved out of it, so the file always contains exactly
    what the venue sent even where this module's reading of it is wrong.

    Returns None only when there is no strike, which is the one field without
    which a contract cannot be identified at all.
    """
    strike = _num(raw.get("strike"))
    if strike is None:
        return None

    expiry = expiry or _epoch_to_date(raw.get("expiration"))
    greeks = source_greeks(raw)

    # A multiplier is NOT published by Yahoo. 100 is the OCC standard for a
    # contract Yahoo marks REGULAR, so it is recorded as derived, never as if
    # the venue had stated it. An adjusted contract (post-merger, odd deliverable)
    # is marked MINI or similar and is left null rather than guessed.
    contract_size = raw.get("contractSize")
    multiplier = 100.0 if contract_size == "REGULAR" else None
    return {
        "record": "contract",
        "underlying_symbol": symbol,
        "entity_id": fields.get("entity_id"),
        "listing_id": fields.get("listing_id"),
        "captured_at": fields.get("captured_at"),
        "payload_fetched_at": fields.get("payload_fetched_at"),
        "session_date": session_date,
        "underlying_spot": fields.get("underlying_spot"),
        "contract_symbol": raw.get("contractSymbol"),
        "right": right,
        "expiry": expiry,
        "dte": _days_between(session_date, expiry) if expiry else None,
        "strike": strike,
        "bid": _num(raw.get("bid")),
        "ask": _num(raw.get("ask")),
        "last": _num(raw.get("lastPrice")),
        "volume": _num(raw.get("volume")),
        "open_interest": _num(raw.get("openInterest")),
        "implied_volatility": _num(raw.get("impliedVolatility")),
        "implied_volatility_provenance": (
            PROV_SOURCE if _num(raw.get("impliedVolatility")) is not None else None),
        "contract_size": contract_size,
        "multiplier": multiplier,
        "multiplier_provenance": PROV_DERIVED if multiplier is not None else None,
        "currency": raw.get("currency"),
        "in_the_money": raw.get("inTheMoney"),
        "last_trade_at": _epoch_to_iso(raw.get("lastTradeDate")),
        "greeks_source": greeks,
        "greeks_source_label": GREEKS_SOURCE_VENUE if greeks else GREEKS_SOURCE_NONE,
        "greeks_derived": None,
        "source": fields.get("source", SOURCE_YAHOO_OPTIONS),
        "retrieval_status": fields.get("retrieval_status", STATUS_OK),
        "raw": raw,
    }


def parse_chain_payload(symbol: str, payload: dict, session_date: str,
                        **fields) -> dict:
    """Turn one Yahoo optionChain payload into contract records. Pure.

    Yahoo returns the full expiry list plus the contracts of ONE expiry per
    call, so a whole-chain snapshot is several payloads merged by
    `build_snapshot`. Never raises on a malformed payload: a shape this does not
    recognise becomes zero contracts and an empty expiry list, and the caller
    records that as an empty capture rather than crashing a scheduled run.
    """
    try:
        result = payload["optionChain"]["result"][0]
    except (KeyError, IndexError, TypeError):
        return {"contracts": [], "expiries_listed": [], "quote": None,
                "underlying_spot": None, "strikes": []}

    quote = result.get("quote") if isinstance(result.get("quote"), dict) else None
    spot = _num((quote or {}).get("regularMarketPrice"))
    expiries = [d for d in (_epoch_to_date(e)
                            for e in result.get("expirationDates") or []) if d]

    contracts: list[dict] = []
    for block in result.get("options") or []:
        if not isinstance(block, dict):
            continue
        expiry = _epoch_to_date(block.get("expirationDate"))
        for right, key in (("C", "calls"), ("P", "puts")):
            for raw in block.get(key) or []:
                if not isinstance(raw, dict):
                    continue
                record = contract_record(
                    symbol, raw, right, expiry, session_date,
                    underlying_spot=spot, **fields)
                if record is not None:
                    contracts.append(record)

    return {
        "contracts": contracts,
        "expiries_listed": expiries,
        "quote": quote,
        "underlying_spot": spot,
        "strikes": [s for s in (_num(v) for v in result.get("strikes") or [])
                    if s is not None],
    }


def select_expiries(expiries: Sequence[str], session_date: str,
                    max_expiries: int = DEFAULT_MAX_EXPIRIES,
                    max_dte: int = DEFAULT_MAX_DTE) -> list[str]:
    """Which expiries to capture, spread across the term structure.

    Taking the first N would capture eight weekly expiries and no term
    structure at all, which is the wrong half of the surface for hedge
    research: a 90-day put is the instrument the hedge engine actually targets.
    So: everything within `max_dte`, thinned to an evenly spaced subset that
    always keeps the nearest and the furthest. Deterministic, so two runs on
    the same day capture the same expiries.
    """
    live = []
    for expiry in sorted(set(expiries)):
        if not expiry:
            continue
        dte = _days_between(session_date, expiry)
        # `dte == 0` is the expiring-today chain and is kept: on expiry day the
        # quotes are pure intrinsic and are exactly what a settlement study
        # needs. `if dte` would silently discard it.
        if dte is None or dte < 0 or dte > max_dte:
            continue
        live.append(expiry)
    if max_expiries <= 0 or len(live) <= max_expiries:
        return live
    if max_expiries == 1:
        return live[:1]
    step = (len(live) - 1) / (max_expiries - 1)
    picked = sorted({live[round(i * step)] for i in range(max_expiries)})
    return picked


def build_snapshot(symbol: str, parsed: Sequence[dict], captured_at: str,
                   **fields) -> dict:
    """Merge per-expiry payloads into one snapshot: a header plus contracts.

    The header spot comes from the FIRST payload, and each contract keeps the
    spot and fetch time of the payload it arrived in, because a chain assembled
    over a minute of wall clock is not a single instant and pretending
    otherwise would misprice every moneyness computed from it later.
    """
    session_date = session_date_for(captured_at)
    contracts: list[dict] = []
    listed: list[str] = []
    spot = None
    quote = None
    for block in parsed:
        contracts.extend(block.get("contracts") or [])
        for expiry in block.get("expiries_listed") or []:
            if expiry not in listed:
                listed.append(expiry)
        if spot is None:
            spot = block.get("underlying_spot")
        if quote is None:
            quote = block.get("quote")

    captured_expiries = sorted({c["expiry"] for c in contracts if c.get("expiry")})
    greeks_label = (GREEKS_SOURCE_VENUE
                    if any(c.get("greeks_source") for c in contracts)
                    else GREEKS_SOURCE_NONE)

    header = {
        "record": "header",
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "underlying_symbol": symbol,
        "entity_id": fields.get("entity_id"),
        "listing_id": fields.get("listing_id"),
        "captured_at": captured_at,
        "session_date": session_date,
        "source": fields.get("source", SOURCE_YAHOO_OPTIONS),
        "source_endpoint": fields.get("source_endpoint", OPTIONS_PATH.format(symbol=symbol)),
        "underlying_spot": spot,
        "underlying_spot_field": "optionChain.result[0].quote.regularMarketPrice",
        "underlying_quote_raw": quote,
        "expiries_listed": sorted(listed),
        "expiries_captured": captured_expiries,
        "n_expiries_listed": len(listed),
        "n_expiries_captured": len(captured_expiries),
        "n_contracts": len(contracts),
        "greeks_source": greeks_label,
        "greeks_note": (
            "Yahoo's v7 option endpoint publishes no Greeks; any Greek in this "
            "archive under greeks_derived was computed downstream and is not a "
            "venue value."),
        "retrieval_status": fields.get("retrieval_status", STATUS_OK),
        "payloads_fetched": len(parsed),
    }
    return {
        "header": header,
        "contracts": contracts,
        "symbol": symbol,
        "captured_at": captured_at,
        "session_date": session_date,
        "underlying_spot": spot,
        "n_contracts": len(contracts),
        "n_expiries": len(captured_expiries),
        "greeks_source": greeks_label,
        "status": STATUS_OK if contracts else STATUS_EMPTY,
    }


# --------------------------------------------------------------------------
# The files
# --------------------------------------------------------------------------

def default_archive_dir() -> str:
    return os.path.join(pit_store.DEFAULT_ARCHIVE_DIR, ARCHIVE_SUBDIR)


def _safe_symbol(symbol: str) -> str:
    keep = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-^="
    return "".join(c if c in keep else "_" for c in (symbol or "").strip().upper())


def snapshot_relpath(symbol: str, captured_at: str) -> str:
    """Deterministic archive path: date / underlying / timestamp.

    Relative to the archive root and stored that way in the manifest, so the
    whole archive can be moved or mounted elsewhere without invalidating every
    row. Forward slashes on every platform: the manifest is data, not a path.
    """
    session = session_date_for(captured_at)
    stamp = _parse_iso(captured_at).strftime("%Y%m%dT%H%M%SZ")
    year, month, day = session[:4], session[5:7], session[8:10]
    return f"{year}/{month}/{day}/{_safe_symbol(symbol)}_{stamp}.jsonl.gz"


def snapshot_abspath(relpath: str, archive_dir: Optional[str] = None) -> str:
    return os.path.join(archive_dir or default_archive_dir(), *relpath.split("/"))


def write_snapshot_file(path: str, header: dict,
                        contracts: Iterable[dict]) -> tuple[int, str]:
    """Write one gzipped JSONL snapshot and return (bytes, sha256).

    Written to a temporary name and renamed into place, so a manifest row can
    never point at a half-written file after a crash. `mtime=0` in the gzip
    header makes the bytes a pure function of the content: the same chain
    written twice hashes identically, which is what lets a later reader treat a
    hash mismatch as tampering rather than as a re-write.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    digest = hashlib.sha256()
    written = 0
    with open(tmp, "wb") as handle:
        with gzip.GzipFile(filename="", mode="wb", fileobj=handle, mtime=0) as gz:
            for record in [header] + list(contracts):
                line = (json.dumps(record, sort_keys=True, ensure_ascii=False,
                                   separators=(",", ":"), default=str) + "\n")
                gz.write(line.encode("utf-8"))
                written += 1
    with open(tmp, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    size = os.path.getsize(tmp)
    os.replace(tmp, path)
    return size, digest.hexdigest()


def read_snapshot_file(path: str) -> tuple[Optional[dict], list[dict]]:
    """Read one archived snapshot back: (header, contracts). No interpretation."""
    header: Optional[dict] = None
    contracts: list[dict] = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("record") == "header" and header is None:
                header = record
            else:
                contracts.append(record)
    return header, contracts


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# The manifest
# --------------------------------------------------------------------------

def record_snapshot(conn: sqlite3.Connection, symbol: str, captured_at: str,
                    **fields) -> str:
    """Write one manifest row. Returns 'written' or 'exists'.

    Idempotent on (underlying_symbol, captured_at), which is the table's unique
    key: a re-run that produces the same timestamp is the same snapshot and
    must not be recorded twice. Never an UPDATE -- a manifest row describes a
    file that already exists and has already been hashed, so there is nothing
    about it that can legitimately change.
    """
    existing = conn.execute(
        "SELECT 1 FROM pit_option_snapshot WHERE underlying_symbol = ? AND captured_at = ?",
        (symbol, captured_at),
    ).fetchone()
    if existing:
        return "exists"
    with pit_store.transaction(conn):
        conn.execute(
            """INSERT INTO pit_option_snapshot
                   (underlying_symbol, entity_id, listing_id, captured_at,
                    session_date, underlying_spot, n_contracts, n_expiries,
                    greeks_source, file_path, file_bytes, sha256, source,
                    status, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (symbol, fields.get("entity_id"), fields.get("listing_id"), captured_at,
             fields.get("session_date") or session_date_for(captured_at),
             fields.get("underlying_spot"), fields.get("n_contracts"),
             fields.get("n_expiries"), fields.get("greeks_source"),
             fields.get("file_path"), fields.get("file_bytes"), fields.get("sha256"),
             fields.get("source", SOURCE_YAHOO_OPTIONS),
             fields.get("status", STATUS_OK), fields.get("error")),
        )
    return "written"


def record_error(conn: sqlite3.Connection, symbol: str, captured_at: str,
                 error: str, **fields) -> str:
    """Record a capture that did not happen. Returns 'written' or 'exists'.

    A failed capture is a row, not a silent gap. A year from now an empty
    Tuesday in the archive is either "Yahoo refused the crumb" or "nobody ran
    the job", and a coverage report that cannot tell them apart is not evidence.
    """
    return record_snapshot(conn, symbol, captured_at, status=STATUS_ERROR,
                           error=str(error)[:2000], greeks_source=GREEKS_SOURCE_NONE,
                           n_contracts=0, n_expiries=0, **fields)


def archive_snapshot(conn: sqlite3.Connection, snapshot: dict,
                     archive_dir: Optional[str] = None, **fields) -> dict:
    """Persist a parsed snapshot: file first, then checksum, then manifest.

    That order is the whole point. The manifest is the index a researcher
    trusts; a row written before its bytes exist would be a promise the archive
    cannot keep. If the manifest row already exists the file is left exactly as
    it was -- rewriting it would change nothing but would break the one
    guarantee the sha256 provides.
    """
    archive_dir = archive_dir or default_archive_dir()
    symbol = snapshot["symbol"]
    captured_at = snapshot["captured_at"]

    existing = conn.execute(
        "SELECT * FROM pit_option_snapshot WHERE underlying_symbol = ? AND captured_at = ?",
        (symbol, captured_at),
    ).fetchone()
    if existing:
        return {"symbol": symbol, "captured_at": captured_at, "status": existing["status"],
                "manifest": "exists", "file_path": existing["file_path"],
                "file_bytes": existing["file_bytes"], "sha256": existing["sha256"],
                "n_contracts": existing["n_contracts"],
                "n_expiries": existing["n_expiries"]}

    relpath = snapshot_relpath(symbol, captured_at)
    abspath = snapshot_abspath(relpath, archive_dir)
    size, digest = write_snapshot_file(abspath, snapshot["header"], snapshot["contracts"])

    status = record_snapshot(
        conn, symbol, captured_at,
        session_date=snapshot["session_date"],
        underlying_spot=snapshot.get("underlying_spot"),
        n_contracts=snapshot.get("n_contracts"),
        n_expiries=snapshot.get("n_expiries"),
        greeks_source=snapshot.get("greeks_source", GREEKS_SOURCE_NONE),
        file_path=relpath, file_bytes=size, sha256=digest,
        source=snapshot["header"].get("source", SOURCE_YAHOO_OPTIONS),
        status=snapshot.get("status", STATUS_OK),
        entity_id=fields.get("entity_id"), listing_id=fields.get("listing_id"),
    )
    return {"symbol": symbol, "captured_at": captured_at,
            "status": snapshot.get("status", STATUS_OK), "manifest": status,
            "file_path": relpath, "file_bytes": size, "sha256": digest,
            "n_contracts": snapshot.get("n_contracts"),
            "n_expiries": snapshot.get("n_expiries")}


def verify_snapshot(conn: sqlite3.Connection, snapshot_id: int,
                    archive_dir: Optional[str] = None) -> dict:
    """Re-hash an archived file and compare it with the manifest.

    The reason the sha256 is stored at all: a hedge backtest that quotes real
    premiums should be able to prove the quotes are the ones captured that day
    and have not been edited since -- by anyone, including a later version of
    this program.
    """
    row = conn.execute(
        "SELECT * FROM pit_option_snapshot WHERE snapshot_id = ?", (snapshot_id,)
    ).fetchone()
    if row is None:
        return {"ok": False, "reason": "no such snapshot"}
    if row["status"] == STATUS_ERROR or not row["file_path"]:
        return {"ok": False, "reason": f"snapshot status {row['status']}; no file"}
    path = snapshot_abspath(row["file_path"], archive_dir)
    if not os.path.exists(path):
        return {"ok": False, "reason": f"file missing: {path}"}
    digest = sha256_file(path)
    size = os.path.getsize(path)
    return {"ok": digest == row["sha256"] and size == row["file_bytes"],
            "reason": "" if digest == row["sha256"] else "sha256 mismatch",
            "sha256": digest, "expected_sha256": row["sha256"],
            "file_bytes": size, "expected_bytes": row["file_bytes"], "path": path}


# --------------------------------------------------------------------------
# Retrieval
# --------------------------------------------------------------------------

def _fetch_payload(symbol: str, expiry: Optional[str] = None) -> tuple[int, Optional[dict], Optional[str]]:
    """One crumb-authenticated option-chain call. Returns (status, body, error).

    The cookie+crumb handshake is `option_data`'s, not a second implementation
    of it: that module already caches the session, already knows Yahoo hands the
    cookie out from fc.yahoo.com and the crumb from query1/query2, and already
    resets itself on a 401. Two handshakes would mean two sets of cookies and
    twice the chance of being rate-limited into a refusal.
    """
    session, crumb, reason = option_data._authenticated_session()
    if session is None:
        return 0, None, reason or "crumb handshake failed"

    params: dict[str, Any] = {"crumb": crumb}
    if expiry:
        try:
            params["date"] = int(_dt.datetime.fromisoformat(expiry).replace(
                tzinfo=_dt.timezone.utc).timestamp())
        except ValueError:
            return 0, None, f"unparseable expiry {expiry!r}"

    last_error = None
    status = 0
    for host in YAHOO_HOSTS:
        _throttle()
        try:
            response = session.get(
                f"https://{host}/{OPTIONS_PATH.format(symbol=symbol)}",
                params=params, timeout=HTTP_TIMEOUT)
        except requests.RequestException as exc:
            last_error = str(exc)
            continue
        status = response.status_code
        if status == 200:
            try:
                return status, response.json(), None
            except ValueError as exc:
                last_error = f"HTTP 200 with unparseable JSON ({exc})"
                continue
        if status == 401:
            # The crumb has gone stale. Drop it so the next underlying performs
            # a fresh handshake rather than repeating a rejected one.
            option_data.reset_session()
            return status, None, "HTTP 401 Invalid Crumb"
        if status == 404:
            return status, None, f"HTTP 404: no listed options for {symbol}"
        last_error = f"HTTP {status}"
    return status, None, last_error or "no response"


def _is_handshake_failure(status_code: int, error: Optional[str]) -> bool:
    """Whether a failed call was the crumb gate rather than the symbol.

    A 404 means Yahoo has no options for this underlying and retrying is
    pointless noise; a crumb refusal is a session problem and one fresh
    handshake is worth trying.
    """
    text = (error or "").lower()
    return status_code in (0, 401) and "404" not in text


def archive_chain(conn: sqlite3.Connection, symbol: str,
                  archive_dir: Optional[str] = None,
                  max_expiries: int = DEFAULT_MAX_EXPIRIES,
                  max_dte: int = DEFAULT_MAX_DTE,
                  captured_at: Optional[str] = None, **fields) -> dict:
    """Capture and archive one underlying's chain. Never raises.

    Yahoo returns one expiry's contracts per call plus the full expiry list, so
    this is 1 + N calls: the first answers "what expiries exist", the rest
    collect the selected ones. Every failure path ends in a manifest row with
    status 'error' -- including a total handshake refusal, which produces no
    file and is still recorded, because "the archive did not start today" is
    itself a fact the owner needs.
    """
    symbol = (symbol or "").strip().upper()
    captured_at = captured_at or _iso(_now_utc())
    session_date = session_date_for(captured_at)
    archive_dir = archive_dir or default_archive_dir()
    common = {"captured_at": captured_at, "entity_id": fields.get("entity_id"),
              "listing_id": fields.get("listing_id")}

    if not symbol:
        return {"symbol": symbol, "status": STATUS_ERROR, "error": "no symbol supplied",
                "manifest": "skipped"}

    status_code, payload, error = _fetch_payload(symbol)
    if payload is None and _is_handshake_failure(status_code, error):
        # `option_data` caches a failed handshake for the life of the process,
        # so without this one retry a single transient refusal at the start of
        # a run would lose the WHOLE day rather than one underlying. Exactly
        # one retry: repeatedly re-handshaking a refusing endpoint is the
        # behaviour that earns a longer refusal.
        option_data.reset_session()
        status_code, payload, error = _fetch_payload(symbol)
    if payload is None:
        manifest = record_error(conn, symbol, captured_at,
                                f"{error} (HTTP {status_code})" if status_code else str(error),
                                session_date=session_date,
                                entity_id=fields.get("entity_id"),
                                listing_id=fields.get("listing_id"))
        return {"symbol": symbol, "captured_at": captured_at, "status": STATUS_ERROR,
                "error": error, "http_status": status_code, "manifest": manifest,
                "n_contracts": 0, "n_expiries": 0}

    first = parse_chain_payload(symbol, payload, session_date,
                                payload_fetched_at=_iso(_now_utc()), **common)
    parsed = [first]
    have = {c["expiry"] for c in first["contracts"] if c.get("expiry")}
    wanted = select_expiries(first["expiries_listed"], session_date,
                             max_expiries=max_expiries, max_dte=max_dte)

    errors: list[str] = []
    for expiry in wanted:
        if expiry in have:
            continue
        code, body, err = _fetch_payload(symbol, expiry)
        if body is None:
            errors.append(f"{expiry}: {err}")
            continue
        parsed.append(parse_chain_payload(
            symbol, body, session_date, payload_fetched_at=_iso(_now_utc()), **common))

    snapshot = build_snapshot(symbol, parsed, captured_at,
                              entity_id=fields.get("entity_id"),
                              listing_id=fields.get("listing_id"))
    snapshot["header"]["expiries_requested"] = wanted
    snapshot["header"]["expiry_errors"] = errors
    snapshot["header"]["max_dte"] = max_dte
    snapshot["header"]["max_expiries"] = max_expiries

    result = archive_snapshot(conn, snapshot, archive_dir,
                              entity_id=fields.get("entity_id"),
                              listing_id=fields.get("listing_id"))
    result["expiry_errors"] = errors
    result["expiries_listed"] = len(first["expiries_listed"])
    return result


def archive_watchlist(conn: sqlite3.Connection, symbols: Sequence[str],
                      archive_dir: Optional[str] = None,
                      progress: Optional[Callable[[str, int, int], None]] = None,
                      **fields) -> list[dict]:
    """Capture a set of underlyings, one after another, politely.

    Sequential on purpose. The crumb is a single shared session and parallel
    requests against a crumb-gated endpoint are the fastest way to be refused
    for the rest of the day -- which, for an archive that cannot be
    reconstructed, costs a day of data that never comes back.
    """
    # A scheduled run starts with a fresh handshake. `option_data` caches its
    # session for the life of the process, which is right for a Streamlit
    # session and wrong for a daily job that may inherit a stale crumb or a
    # cached refusal from whatever ran before it.
    option_data.reset_session()
    out: list[dict] = []
    total = len(symbols)
    for index, symbol in enumerate(symbols, start=1):
        if progress:
            progress(symbol, index, total)
        out.append(archive_chain(conn, symbol, archive_dir=archive_dir, **fields))
    return out


# --------------------------------------------------------------------------
# Coverage -- where the archive actually starts
# --------------------------------------------------------------------------

def archive_coverage(conn: sqlite3.Connection,
                     symbol: Optional[str] = None) -> list[dict]:
    """First and last real snapshot per underlying, with the failures counted.

    This is the function a hedge backtest calls before it claims anything about
    option costs. `first_session` is the archive's start date for that name:
    before it there are no real quotes and no amount of modelling makes one.
    Error captures are counted separately and never move `first_session` --
    a failed day is not coverage.
    """
    rows = conn.execute(
        """SELECT underlying_symbol,
                  MIN(CASE WHEN status != ? THEN session_date END) AS first_session,
                  MAX(CASE WHEN status != ? THEN session_date END) AS last_session,
                  COUNT(*) AS n_snapshots,
                  SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS n_ok,
                  SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS n_empty,
                  SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS n_error,
                  SUM(COALESCE(n_contracts, 0)) AS n_contracts,
                  SUM(COALESCE(file_bytes, 0)) AS file_bytes
             FROM pit_option_snapshot
            WHERE (? IS NULL OR underlying_symbol = ?)
            GROUP BY underlying_symbol
            ORDER BY underlying_symbol""",
        (STATUS_ERROR, STATUS_ERROR, STATUS_OK, STATUS_EMPTY, STATUS_ERROR,
         symbol, symbol),
    ).fetchall()
    return [dict(r) for r in rows]


def coverage_statement(conn: sqlite3.Connection, symbol: str) -> str:
    """One sentence a backtest can print about what it does and does not have."""
    rows = archive_coverage(conn, symbol)
    if not rows or not rows[0]["first_session"]:
        return (f"{symbol}: NO archived option quotes. Every option price for "
                f"{symbol} is UNAVAILABLE.")
    row = rows[0]
    return (f"{symbol}: real option quotes archived {row['first_session']} to "
            f"{row['last_session']} ({row['n_ok']} snapshots, "
            f"{row['n_contracts']} contract observations). Option quotes before "
            f"{row['first_session']} are UNAVAILABLE"
            + (f"; {row['n_error']} capture(s) failed." if row["n_error"] else "."))


def snapshot_rows(conn: sqlite3.Connection, session_date: Optional[str] = None,
                  symbol: Optional[str] = None, limit: int = 200) -> list[dict]:
    """Manifest rows, newest first. The archive's own index."""
    rows = conn.execute(
        """SELECT * FROM pit_option_snapshot
            WHERE (? IS NULL OR session_date = ?)
              AND (? IS NULL OR underlying_symbol = ?)
            ORDER BY captured_at DESC, underlying_symbol
            LIMIT ?""",
        (session_date, session_date, symbol, symbol, limit),
    ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    """Daily option-chain capture.

        # crontab -e   (15:45 ET weekdays, before the 16:15 option close)
        CRON_TZ=America/New_York
        45 15 * * 1-5 cd /path/to/shafferfineval && python3 pit_options.py

    Options:
        --symbols A,B,C     capture these instead of the default watchlist
        --max-expiries N    expiries per underlying (default 8)
        --max-dte N         furthest expiry in days (default 400)
        --coverage          print what the archive holds and exit
        --verify            re-hash every archived file and report mismatches

    Idempotent only within the same second: a snapshot is keyed on its capture
    timestamp, so running twice in one day archives two genuine snapshots taken
    at two different times, which is correct -- an option chain at 10:00 and at
    15:45 are different data.
    """
    parser = argparse.ArgumentParser(
        description="Archive raw listed option chains for point-in-time research.")
    parser.add_argument("--symbols",
                        help="comma-separated underlyings (default: "
                             + ",".join(DEFAULT_WATCHLIST) + ")")
    parser.add_argument("--db", default=pit_store.DEFAULT_PIT_DB_PATH)
    parser.add_argument("--archive-dir", default=None,
                        help="archive root (default: pit_archive/options)")
    parser.add_argument("--max-expiries", type=int, default=DEFAULT_MAX_EXPIRIES)
    parser.add_argument("--max-dte", type=int, default=DEFAULT_MAX_DTE)
    parser.add_argument("--coverage", action="store_true",
                        help="report first/last snapshot per underlying and exit")
    parser.add_argument("--verify", action="store_true",
                        help="re-hash archived files against the manifest and exit")
    args = parser.parse_args(list(argv) if argv is not None else None)

    conn = pit_store.init_db(args.db)
    archive_dir = args.archive_dir or default_archive_dir()

    if args.coverage:
        rows = archive_coverage(conn)
        if not rows:
            print("The option archive is EMPTY. No real option quotes exist for "
                  "any date. Every hedge backtest must report premiums as "
                  "UNAVAILABLE.")
            return 0
        print(f"Option archive: {archive_dir}")
        for row in rows:
            print(f"  {row['underlying_symbol']:<8} "
                  f"{row['first_session'] or '-'} -> {row['last_session'] or '-'}  "
                  f"snapshots={row['n_ok']} empty={row['n_empty']} "
                  f"errors={row['n_error']}  contracts={row['n_contracts']}  "
                  f"{row['file_bytes'] / 1e6:.2f} MB")
        return 0

    if args.verify:
        bad = 0
        rows = conn.execute(
            "SELECT snapshot_id FROM pit_option_snapshot WHERE status != ?",
            (STATUS_ERROR,)).fetchall()
        for row in rows:
            result = verify_snapshot(conn, int(row["snapshot_id"]), archive_dir)
            if not result["ok"]:
                bad += 1
                print(f"  MISMATCH snapshot {row['snapshot_id']}: {result['reason']}")
        print(f"verified {len(rows)} file(s), {bad} mismatch(es)")
        return 1 if bad else 0

    symbols = ([s.strip().upper() for s in args.symbols.split(",") if s.strip()]
               if args.symbols else list(DEFAULT_WATCHLIST))

    print(f"Option chain archive: {archive_dir}")
    print(f"PIT store: {args.db}")
    print(f"capturing {len(symbols)} underlying(s), up to {args.max_expiries} "
          f"expiries each within {args.max_dte} DTE", flush=True)

    def progress(symbol: str, index: int, total: int) -> None:
        print(f"  [{index}/{total}] {symbol}", flush=True)

    results = archive_watchlist(conn, symbols, archive_dir=archive_dir,
                                progress=progress,
                                max_expiries=args.max_expiries, max_dte=args.max_dte)

    ok = [r for r in results if r.get("status") == STATUS_OK]
    failed = [r for r in results if r.get("status") == STATUS_ERROR]
    total_bytes = sum(r.get("file_bytes") or 0 for r in results)
    for result in results:
        if result.get("status") == STATUS_ERROR:
            print(f"  ERROR {result['symbol']}: {result.get('error')}")
        else:
            print(f"  {result['symbol']:<8} contracts={result.get('n_contracts')} "
                  f"expiries={result.get('n_expiries')} "
                  f"{(result.get('file_bytes') or 0) / 1024:.1f} KiB "
                  f"[{result.get('manifest')}] {result.get('file_path')}")
            for note in result.get("expiry_errors") or []:
                print(f"    partial: {note}")

    print(f"\ncaptured {len(ok)} | failed {len(failed)} | "
          f"{sum(r.get('n_contracts') or 0 for r in results)} contracts | "
          f"{total_bytes / 1024:.1f} KiB written")
    if failed:
        print("A failed capture is permanent: expired chains cannot be "
              "re-fetched from any free source.")
    return 1 if failed and not ok else 0


if __name__ == "__main__":
    sys.exit(main())
