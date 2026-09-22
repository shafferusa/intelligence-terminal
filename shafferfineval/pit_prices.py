"""Listing resolution and price ingest -- the last hard link in the replay chain.

Everything upstream of here is issuer-keyed: facts, SIC, peers, macro. Nothing
upstream can produce a RETURN. A return needs a tradeable line, and a tradeable
line needs an answer to the one question a ticker cannot answer by itself:
*was this symbol this issuer's instrument on that date?*

THE ONE CALL. Yahoo's chart endpoint returns `meta` and `timestamp`/`indicators`
in the same payload. `meta` is the identity evidence and the bars are the price
history, so this module fetches ONCE per symbol and uses the response twice.
Fetching the gate and the prices separately would double the request count, and
would additionally allow the two to disagree -- a gate that passed against one
payload while the bars came from another is not a gate.

THE GATE IS THE POINT. `pit_identity.resolve_listing` owns the discipline and
its constants are imported here rather than restated; this module differs from
it in exactly two ways, both deliberate and both recorded on every row:

  * it supplies `meta` it already has instead of making a second probe call;
  * it FLAGS an OTC venue rather than rejecting it, because the survivorship
    cohort this store exists to hold is full of names that finished on the pink
    sheets. FRCB -- First Republic, seized by the FDIC -- answers today as
    "OTC Markets OTCPK" and is a name the replay must be able to price all the
    way to its end. `otc_flag` carries the caveat so a caller can still refuse it.

WHAT THE GATE CANNOT DO, stated plainly and recorded as confidence rather than
hidden: `firstTradeDate` catches a symbol that moved to a YOUNGER instrument
(BBBY, FB, INFO, AMR, AMTD, SPWR) and is blind to one that moved to an OLDER
one (`T` before 2005 was AT&T Corp, not SBC; `META` before 2022 was Meta
Materials). A passing gate is therefore `inferred` unless a filed
`dei:TradingSymbol` backs it, and `proved` never rests on the ticker alone.

THREE OUTCOMES, NEVER TWO. 'delisted', 'never existed' and 'the network failed'
are different facts and are stored as different verdicts:

    accepted   the gate passed; bars and corporate actions were written
    rejected   Yahoo answered, and the evidence says this is not that instrument
    no_data    Yahoo answered 404 -- the symbol is dead or was never listed
    error      the request failed or was throttled; nothing is concluded

21 of 26 delisted tickers in the pilot cohort return HTTP 404. Folding that into
`rejected` would turn "we have no series for this dead company" into "we proved
this was the wrong company", which is a different and much stronger claim.

AND ONE REJECTION MEANS LESS THAN IT LOOKS. Measured on this store: AvalonBay,
Electronic Arts, Leggett & Platt, Webster Financial, AstroNova and Cross Country
Healthcare all return `firstTradeDate` 2026-07-17 -- BBBY's date -- with no
history before it. Six unrelated live issuers cannot share a first trade date,
so for them the field reports a truncated feed and not a reassigned ticker. The
gate still refuses them, correctly, because there is no series to attribute; but
`suspect_first_trade_dates` exists so a reader is told which rejections mean
"the history is missing" rather than "the ticker was reused".

DISK IS THE BINDING CONSTRAINT. An earlier ingest on this volume filled the disk
because a SQLite WAL grew to 9.3 GB: a concurrent reader held back the
checkpointer, `PRAGMA wal_checkpoint(TRUNCATE)` returned busy, and nobody read
the return value. Here every checkpoint's return value is read and counted, free
space is measured before every batch, and the run STOPS on its own floor rather
than discovering the floor by hitting it. A partial ingest that reports its
boundary is a result; a full disk is not.

Tables written: `pit_listing`, `pit_price_bar`, `pit_corporate_action`,
`pit_listing_gate` (this module's, see its comment), and the per-entity roll-up
in `pit_listing_status` via `pit_symbols.set_listing_status`.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import os
import shutil
import sqlite3
import sys
import time
from typing import Any, Optional, Sequence

import pit_identity
import pit_store
import pit_symbols
from pit_identity import (
    CONFIDENCE_INFERRED,
    CONFIDENCE_PROVED,
    CONFIDENCE_UNVERIFIED,
    EXPECTED_INSTRUMENT_TYPE,
    OTC_EXCHANGE_MARKERS,
    REJECT_FIRST_TRADE_AFTER,
    REJECT_INSTRUMENT_TYPE,
    REJECT_NO_FIRST_TRADE,
    REJECT_NO_META,
    REJECT_NO_YAHOO_DATA,
    REJECT_NOT_DAILY,
    REJECT_OTC,
    ACCEPT_REASON,
)
from pit_store import transaction

# --------------------------------------------------------------------------
# Versions and vocabulary
# --------------------------------------------------------------------------

#: Stamped on every gate row, so a later gate revision lands beside this one
#: instead of overwriting its verdicts.
PRICE_SOURCE_VERSION = "price_gate_v1_yahoo_first_trade"

SOURCE_YAHOO_CHART = "yahoo:chart"
SOURCE_YAHOO_EVENTS = "yahoo:chart_events"

#: Verdicts. See the module docstring: these are four states, not two.
VERDICT_ACCEPTED = "accepted"
VERDICT_REJECTED = "rejected"
VERDICT_NO_DATA = "no_data"
VERDICT_ERROR = "error"

#: Rejection reasons this module adds to `pit_identity`'s set.
REJECT_CLAIMED_BY_OTHER_ENTITY = "symbol_claimed_by_other_entity"
REJECT_NO_BARS_IN_WINDOW = "no_bars_in_price_window"

#: Non-rejection outcomes.
STATUS_ALREADY_INGESTED = "already_ingested"
STATUS_HTTP_ERROR = "http_error"

#: The price window. 2011 rather than 2013: momentum and growth features need a
#: warm-up run before the first scored month-end, and a 12-month lookback taken
#: from a series that starts at the as-of date is not a lookback.
PRICE_START = "2011-01-01"

#: Politeness. The measured ceiling was 7.98 req/s with zero throttling; this is
#: the floor of that, and it is a POLICY not an optimum. `pit_identity` throttles
#: its own Yahoo calls at 10/s, so this interval is applied on top of that one.
MAX_REQUESTS_PER_SECOND = 8.0
MIN_REQUEST_INTERVAL = 1.0 / MAX_REQUESTS_PER_SECOND

#: Retry ladder for a transient failure. A 429 is NOT on it: see `ThrottleStop`.
RETRY_BACKOFF_SECONDS = (1.0, 2.0, 4.0)
THROTTLE_STATUSES = frozenset({429, 999})

#: Free-space floor, in GiB. The run refuses to start and refuses to continue
#: below it. 8 GiB on a volume that had 13 free leaves room for the WAL, for the
#: checkpoint's copy, and for whatever else is running on this machine.
DEFAULT_MIN_FREE_GIB = 8.0

#: How often to checkpoint and re-measure. Small enough that a WAL cannot run
#: away between two looks at it.
DEFAULT_CHECKPOINT_EVERY = 25

#: Bars written before the run stops voluntarily. A bar costs ~150 bytes once
#: its two indexes are paid for; this default is about 600 MB.
DEFAULT_MAX_BARS = 4_000_000

#: Lock retry. Another agent may hold the write lock; a lock is a wait, not an
#: error.
BUSY_TIMEOUT_MS = 60_000
LOCK_RETRIES = 5

#: A split ratio is CLEAN when both sides reduce to small integers. 4:1, 1:8,
#: 3:2 and even a 21:20 stock dividend are share-count events. AT&T's
#: 1324:1000 reduces to 331:250, which no transfer agent ever announced as a
#: split -- it is the price adjustment for the Warner spin-off, and applying it
#: to the share count is a 32.4% error.
CLEAN_RATIO_BOUND = 50

EVENT_SPLIT = "split"
EVENT_SPINOFF = "spinoff"
EVENT_DIVIDEND = "dividend"


SCHEMA = """
-- =====================================================================
-- THE GATE LEDGER. One row per (entity, symbol) identity attempt.
-- =====================================================================

-- `pit_listing_status` is keyed `entity_id PRIMARY KEY`: it holds ONE
-- conclusion per issuer and cannot hold a per-symbol verdict. A rejection
-- ledger needs one row per attempt -- an issuer with three reported symbols can
-- have three different reasons -- so the evidence lives here and
-- `pit_listing_status` carries the roll-up. The two are written together and
-- neither is derived from the other after the fact.
--
-- Mutable by design, like `pit_listing_status` and for the same reason: a gate
-- verdict is a CONCLUSION about identity that improves as evidence arrives, not
-- a published fact. `source_version` says which gate produced it.
--
-- `verdict` is four-valued on purpose. 'no_data' (HTTP 404, the symbol is dead
-- or never existed) and 'error' (the request failed) are not rejections, and
-- collapsing either into 'rejected' would assert a proof that was never made.
CREATE TABLE IF NOT EXISTS pit_listing_gate (
    entity_id             INTEGER NOT NULL REFERENCES pit_entity(entity_id),
    symbol                TEXT NOT NULL,
    as_of_date            TEXT NOT NULL,   -- earliest as-of the entity reported it
    verdict               TEXT NOT NULL,   -- accepted | rejected | no_data | error
    reason                TEXT NOT NULL,
    listing_id            INTEGER REFERENCES pit_listing(listing_id),
    confidence            TEXT,
    first_trade_date      TEXT,
    instrument_type       TEXT,
    exchange              TEXT,
    otc_flag              INTEGER NOT NULL DEFAULT 0,
    long_name             TEXT,
    http_status           INTEGER,
    granularity           TEXT,
    n_bars                INTEGER,
    bar_first             TEXT,
    bar_last              TEXT,
    n_zero_volume_bars    INTEGER,
    n_splits              INTEGER,
    n_spinoffs            INTEGER,
    n_dividends           INTEGER,
    priority_tier         INTEGER,
    evidence_json         TEXT,
    ingest_id             INTEGER,
    source_version        TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    UNIQUE (entity_id, symbol, as_of_date),
    CHECK (verdict IN ('accepted', 'rejected', 'no_data', 'error'))
);

CREATE INDEX IF NOT EXISTS idx_pit_listing_gate_verdict
    ON pit_listing_gate (verdict, reason);
CREATE INDEX IF NOT EXISTS idx_pit_listing_gate_symbol
    ON pit_listing_gate (symbol);
-- Bars are looked up FROM a listing; auditing them runs the other way. Without
-- this, "which bars belong to a listing whose gate did not pass" is a scan of
-- the gate table per bar row -- 9.3M x 4k, which is a query nobody ever runs
-- twice, so the invariant stops being checked.
CREATE INDEX IF NOT EXISTS idx_pit_listing_gate_listing
    ON pit_listing_gate (listing_id);
"""


class ThrottleStop(RuntimeError):
    """Yahoo asked us to stop. It is a stop signal, not an error to retry past.

    A 429 answered with a backoff-and-retry is a politeness failure dressed up
    as resilience: the remote has already said the request rate is too high, and
    the correct response is to end the run and report how far it got.
    """


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _day(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return _dt.date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def _epoch(day: str) -> int:
    date = _dt.date.fromisoformat(day[:10])
    return int(_dt.datetime(date.year, date.month, date.day,
                            tzinfo=_dt.timezone.utc).timestamp())


def exchange_day(epoch: Any, gmtoffset: Any) -> Optional[str]:
    """This module's name for `pit_identity._exchange_date`. One implementation.

    PATCHED AT SOURCE, NO LONGER SHADOWED. This used to carry a second copy of
    the conversion, guarding a Windows-only crash: `_exchange_date` called
    `datetime.fromtimestamp`, which rejects a sufficiently negative timestamp
    with OSError [Errno 22], so every issuer whose first trade predates 1970 --
    Boeing, GE, IBM, Coca-Cola and the rest of the 1962-01-02 NYSE roll --
    killed the ingest. Catching it here was the wrong place: dating a listing is
    identity's job, the same crash was reachable through `pit_identity`'s own
    `listing_gate`, and two implementations of one date rule drift. The defect
    is now fixed in `pit_identity._exchange_date` itself and this is a plain
    delegation, kept only so the four call sites below read in this module's
    vocabulary.

    INVARIANT: `exchange_day(e, o) is _exchange_date(e, o)` for every input,
    now and after any future change, because there is nothing here but the call.
    """
    return pit_identity._exchange_date(epoch, gmtoffset)


def _schema_objects(conn: sqlite3.Connection) -> set[str]:
    return {f"{r[1]}:{r[0]}" for r in conn.execute(
        "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'index')")}


def ensure_schema(conn: sqlite3.Connection) -> str:
    """Create this module's tables and indexes if absent. Idempotent.

    Also calls `pit_symbols.ensure_schema`, because the per-entity roll-up this
    module writes lands in that module's `pit_listing_status`.

    The status string counts INDEXES as well as tables, because an index added
    to an existing table is exactly the change a caller would otherwise never
    be told about -- and one of them here turns the "do any bars belong to a
    listing that failed its gate" audit from ten minutes into four seconds.
    """
    before = _schema_objects(conn)
    pit_symbols.ensure_schema(conn)
    conn.executescript(SCHEMA)
    conn.commit()
    created = sorted(_schema_objects(conn) - before)
    return f"created {created}" if created else "already present"


def connect(db_path: str = pit_store.DEFAULT_PIT_DB_PATH) -> sqlite3.Connection:
    """A store connection tuned for an ingest that shares the file.

    `busy_timeout` is the whole point: another agent may hold the write lock,
    and a lock is a wait rather than a failure. Without it SQLite raises
    immediately and a long ingest dies on somebody else's commit.
    """
    conn = pit_store.connect(db_path)
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    return conn


def _with_lock_retry(fn, *args, **kwargs):
    """Run `fn`, retrying while SQLite reports the database locked.

    `busy_timeout` covers most contention, but a writer that holds the lock
    across a long transaction can still surface `database is locked`. Retrying
    is correct here because every write this module makes is idempotent.
    """
    delay = 0.5
    for attempt in range(LOCK_RETRIES):
        try:
            return fn(*args, **kwargs)
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) and "busy" not in str(exc):
                raise
            if attempt == LOCK_RETRIES - 1:
                raise
            time.sleep(delay)
            delay *= 2
    return None


# --------------------------------------------------------------------------
# Disk and WAL -- the binding constraint, measured rather than assumed
# --------------------------------------------------------------------------

def disk_free_gib(path: str) -> float:
    """Free space on the volume holding `path`, in GiB."""
    return shutil.disk_usage(os.path.dirname(os.path.abspath(path)) or ".").free / 2 ** 30


def wal_bytes(db_path: str) -> int:
    """Size of the write-ahead log beside `db_path`, or 0 when there is none."""
    try:
        return os.path.getsize(db_path + "-wal")
    except OSError:
        return 0


def checkpoint_wal(conn: sqlite3.Connection, db_path: str) -> dict:
    """Truncate the WAL and REPORT WHAT HAPPENED.

    `PRAGMA wal_checkpoint(TRUNCATE)` returns (busy, log_frames,
    checkpointed_frames). A busy of 1 means a reader held a snapshot and THE
    CHECKPOINT DID NOTHING -- the WAL keeps growing and the pragma returns
    success-shaped output anyway. That is exactly how a WAL reached 9.3 GB on
    this volume and filled the disk without a single error being raised. The
    return value is read here, counted, and surfaced in the run summary.
    """
    before = wal_bytes(db_path)
    try:
        row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    except sqlite3.OperationalError as exc:
        return {"busy": 1, "ok": False, "error": str(exc),
                "wal_before": before, "wal_after": wal_bytes(db_path)}
    busy = int(row[0]) if row else 1
    return {
        "busy": busy,
        "ok": busy == 0,
        "log_frames": int(row[1]) if row and row[1] is not None else None,
        "checkpointed_frames": int(row[2]) if row and row[2] is not None else None,
        "wal_before": before,
        "wal_after": wal_bytes(db_path),
    }


def require_disk(db_path: str, min_free_gib: float = DEFAULT_MIN_FREE_GIB) -> float:
    """Return free GiB, raising before a bulk write would cross the floor.

    Called BEFORE every batch, not once at start-up. A check at start-up tells
    you the disk was fine an hour ago.
    """
    free = disk_free_gib(db_path)
    if free < min_free_gib:
        raise RuntimeError(
            f"ABORT: {free:.2f} GiB free on the volume holding {db_path}, "
            f"below the {min_free_gib:.2f} GiB floor. No bulk write attempted. "
            f"WAL is {wal_bytes(db_path) / 2 ** 20:.1f} MiB.")
    return free


# --------------------------------------------------------------------------
# 1. The candidate set and its ordering
# --------------------------------------------------------------------------

def _peer_month_end_counts(conn: sqlite3.Connection, month_ends: Sequence[str],
                           max_filing_age_days: int =
                           pit_identity.DEFAULT_PEER_MAX_FILING_AGE_DAYS
                           ) -> dict[int, list[str]]:
    """For each entity, the month-end sessions at which it was a peer.

    This is `pit_identity.peer_universe_as_of` evaluated across the whole grid
    at once rather than 405 times. The membership rule is identical -- an annual
    filing at or before the session and no more than `max_filing_age_days`
    before it, and no exit before the session -- and it is evaluated from the
    same two tables. Running the per-date query 405 times over 410k SIC rows
    would cost minutes to produce the same answer.
    """
    exits: dict[int, str] = {}
    for row in conn.execute(
            "SELECT entity_id, exit_date FROM pit_entity_exit WHERE exit_date IS NOT NULL"):
        day = _day(row["exit_date"])
        if day:
            exits[int(row["entity_id"])] = day

    windows: dict[int, list[tuple[str, str]]] = {}
    for row in conn.execute("SELECT entity_id, filed FROM pit_entity_sic"):
        filed = _day(row["filed"])
        if not filed:
            continue
        end = (_dt.date.fromisoformat(filed)
               + _dt.timedelta(days=int(max_filing_age_days))).isoformat()
        windows.setdefault(int(row["entity_id"]), []).append((filed, end))

    out: dict[int, list[str]] = {}
    for entity_id, spans in windows.items():
        exit_day = exits.get(entity_id)
        hits = [m for m in month_ends
                if (exit_day is None or exit_day >= m)
                and any(start <= m <= stop for start, stop in spans)]
        if hits:
            out[entity_id] = hits
    return out


def _selected_symbols_for_entity(conn: sqlite3.Connection, entity_id: int,
                                 sessions: Sequence[str],
                                 grid_start: str = "0000-00-00") -> dict[str, dict]:
    """Which symbols `pit_symbols.symbol_as_of` would return across `sessions`.

    One query per entity instead of two per (entity, session). The selection
    rule is not restated: the latest available accession wins, and within it the
    share-class filter is `pit_symbols`' own -- a second copy of "is this common
    stock" is how two answers to that question start to differ. Berkshire's 2023
    10-K reports thirteen symbols and only BRK.A and BRK.B are shares.
    """
    rows = conn.execute(
        """SELECT symbol, source_accn, available_date, filed, security_title
             FROM pit_symbol_obs
            WHERE entity_id = ? AND status = ?""",
        (entity_id, pit_symbols.OBS_OK),
    ).fetchall()
    if not rows:
        return {}

    by_accn: dict[str, list[sqlite3.Row]] = {}
    order: dict[str, tuple[str, str, str]] = {}
    for row in rows:
        accn = row["source_accn"]
        by_accn.setdefault(accn, []).append(row)
        order[accn] = (row["available_date"], row["filed"] or "", accn)
    ranked = sorted(order.items(), key=lambda kv: kv[1])

    def eligible(accn: str) -> list[str]:
        members = by_accn[accn]
        common = [r["symbol"] for r in members
                  if pit_symbols._is_common_stock(r["security_title"]) is True]
        if common:
            return sorted(set(common))
        unknown = [r["symbol"] for r in members
                   if pit_symbols._is_common_stock(r["security_title"]) is None]
        return sorted(set(unknown))

    cache: dict[str, list[str]] = {}
    out: dict[str, dict] = {}
    for session in sessions:
        chosen = None
        for accn, key in ranked:
            if key[0] <= session:
                chosen = accn
            else:
                break
        if chosen is None:
            continue
        if chosen not in cache:
            cache[chosen] = eligible(chosen)
        for symbol in cache[chosen]:
            entry = out.setdefault(symbol, {"n_sessions": 0, "n_grid_sessions": 0,
                                            "accns": set()})
            entry["n_sessions"] += 1
            if session >= grid_start:
                entry["n_grid_sessions"] += 1
            entry["accns"].add(chosen)
    return out


def candidate_listings(conn: sqlite3.Connection, *,
                       grid_start: str = "2013-01-01",
                       market: str = "XNYS",
                       limit: Optional[int] = None) -> list[dict]:
    """The (entity_id, symbol) pairs to gate, most valuable first.

    ORDERING, stated because a partial run's boundary is only meaningful if the
    order is:

      tier 0  the symbol the point-in-time selector would return for a peer
              entity at one of the SCORED month-end sessions (2013 onward).
              These are the pairs a replay actually asks for. Ranked by how many
              of those sessions the pair covers, so a name that was tradeable
              for a decade outranks one that appeared for a quarter.
      tier 1  the same, but only at pre-2013 month-ends -- warm-up history for a
              name that had left the grid before scoring starts.
      tier 2  every other 'ok' symbol observation for a peer entity: other share
              classes, listed notes, renamed lines.

    `as_of_date` on each candidate is the EARLIEST availability date at which
    the entity reported the symbol, because that is the strictest form of the
    first-trade-date test: a symbol whose Yahoo series begins after the issuer
    first named it has been reassigned.
    """
    month_ends = pit_store.month_end_sessions(conn, "1900-01-01", "2100-01-01", market)
    peers = _peer_month_end_counts(conn, month_ends)

    # The earliest availability AND the accession that carried it. The accession
    # is the proof: it is a filed dei:TradingSymbol, dated, and it is what
    # raises a passing gate from `inferred` to `proved`. Reading the date
    # without the accession would leave every listing this module writes
    # unprovable against evidence the store already holds.
    earliest: dict[tuple[int, str], str] = {}
    proofs: dict[tuple[int, str], str] = {}
    for row in conn.execute(
            """SELECT entity_id, symbol, available_date, filed, source_accn,
                      source, source_version
                 FROM pit_symbol_obs WHERE status = ?
                ORDER BY entity_id, symbol, available_date DESC, source_accn DESC""",
            (pit_symbols.OBS_OK,)):
        key = (int(row["entity_id"]), row["symbol"])
        earliest[key] = row["available_date"]          # last write wins = earliest
        proofs[key] = json.dumps(
            {"tag": "dei:TradingSymbol", "accn": row["source_accn"],
             "filed": row["filed"], "available_date": row["available_date"],
             "source": row["source"], "source_version": row["source_version"]},
            sort_keys=True)

    candidates: dict[tuple[int, str], dict] = {}
    for entity_id, sessions in peers.items():
        early_sessions = [s for s in sessions if s < grid_start]
        picked = _selected_symbols_for_entity(conn, entity_id, sessions, grid_start)
        for symbol, info in picked.items():
            key = (entity_id, symbol)
            if key not in earliest:
                continue
            n_grid = info["n_grid_sessions"]
            candidates[key] = {
                "entity_id": entity_id,
                "symbol": symbol,
                "as_of_date": earliest[key],
                "proof": proofs.get(key),
                "n_grid_sessions": n_grid,
                "n_sessions": info["n_sessions"],
                "tier": 0 if n_grid else 1,
                "n_peer_month_ends": len(sessions),
                "n_early_month_ends": len(early_sessions),
            }

    for (entity_id, symbol), first_seen in earliest.items():
        if entity_id not in peers or (entity_id, symbol) in candidates:
            continue
        candidates[(entity_id, symbol)] = {
            "entity_id": entity_id, "symbol": symbol, "as_of_date": first_seen,
            "proof": proofs.get((entity_id, symbol)),
            "n_grid_sessions": 0, "n_sessions": 0, "tier": 2,
            "n_peer_month_ends": len(peers[entity_id]), "n_early_month_ends": 0,
        }

    ordered = sorted(candidates.values(),
                     key=lambda c: (c["tier"], -c["n_grid_sessions"],
                                    -c["n_sessions"], c["symbol"]))
    return ordered[:limit] if limit else ordered


# --------------------------------------------------------------------------
# 2. One chart call, used twice
# --------------------------------------------------------------------------

_last_request = [0.0]


def _pace() -> None:
    """Hold the process to MAX_REQUESTS_PER_SECOND. Politeness, not throughput."""
    wait = MIN_REQUEST_INTERVAL - (time.monotonic() - _last_request[0])
    if wait > 0:
        time.sleep(wait)
    _last_request[0] = time.monotonic()


def fetch_chart(symbol: str, start: str = PRICE_START,
                end: Optional[str] = None) -> dict:
    """ONE Yahoo chart call covering `start`..`end`. Gate evidence and bars both.

    `range=max` is never used and must never be: it silently coarsens to monthly
    bars -- AAPL returns 169 points instead of 11,534 -- and every date
    comparison the gate makes would then be against the wrong series. The
    explicit epoch window keeps `dataGranularity` at '1d', which the caller
    asserts before trusting anything in the payload.

    Returns {'status', 'meta', 'granularity', 'timestamps', 'quote', 'adjclose',
    'events', 'error'}. Never raises for an ordinary miss; raises ThrottleStop
    when the remote says slow down, because that is a stop signal.
    """
    params = {
        "period1": _epoch(start),
        "period2": int(time.time()) if end is None else _epoch(end) + 86_400,
        "interval": "1d",
        "events": "div,split",
    }
    status, body = 0, None
    for attempt in range(len(RETRY_BACKOFF_SECONDS) + 1):
        _pace()
        # pit_identity owns the session, the browser UA (acceptable for Yahoo
        # and only for Yahoo) and the host failover. cache_dir is deliberately
        # None: a 4,000-bar payload cached per symbol is gigabytes on a volume
        # whose free space is the binding constraint.
        status, body = pit_identity._yahoo_chart(symbol, params, None)
        if status in THROTTLE_STATUSES:
            raise ThrottleStop(
                f"Yahoo returned HTTP {status} for {symbol}: stopping the run")
        if status == 200 or status in (400, 404):
            break
        if attempt < len(RETRY_BACKOFF_SECONDS):
            time.sleep(RETRY_BACKOFF_SECONDS[attempt])

    chart = (body or {}).get("chart") or {}
    results = chart.get("result") or []
    if not results:
        return {"status": status, "meta": None, "granularity": None,
                "timestamps": [], "quote": {}, "adjclose": [], "events": {},
                "error": (chart.get("error") or {}).get("code") if chart.get("error") else None}

    payload = results[0]
    meta = payload.get("meta") or {}
    indicators = payload.get("indicators") or {}
    quote = (indicators.get("quote") or [{}])[0] or {}
    adj = (indicators.get("adjclose") or [{}])[0] or {}
    return {
        "status": status,
        "meta": meta,
        "granularity": meta.get("dataGranularity"),
        "timestamps": payload.get("timestamp") or [],
        "quote": quote,
        "adjclose": adj.get("adjclose") or [],
        "events": payload.get("events") or {},
        "error": None,
    }


# --------------------------------------------------------------------------
# 3. The gate
# --------------------------------------------------------------------------

def gate_chart(conn: sqlite3.Connection, symbol: str, entity_id: int, as_of: str,
               chart: dict, *, proof: Optional[str] = None,
               expected_instrument_type: str = EXPECTED_INSTRUMENT_TYPE,
               expect_national_exchange: bool = False,
               persist: bool = True) -> dict:
    """`pit_identity.resolve_listing`'s discipline, applied to a payload we hold.

    Same checks, same constants, same order, and the same refusal to write a
    `pit_listing` row for a symbol that fails: the replay's job at a failed gate
    is to find NO listing, not to filter a bad one out later.

      1. `dataGranularity == '1d'`, else the whole payload is rejected. A
         coarsened series makes every date comparison below meaningless.
      2. `firstTradeDate` LATER than `as_of` -> rejected. This is the gate.
         BBBY today is EQUITY on NYSE named "Bed Bath & Beyond, Inc." -- ticker,
         name and exchange all match the retailer that died in 2023 -- and the
         2026-07-17 first bar is the only thing that separates them. Read the
         rejection carefully though: the same date comes back for AvalonBay and
         five other live issuers, where it reports a truncated feed rather than
         a reused ticker. The refusal is right either way -- there is no series
         to attribute -- and `suspect_first_trade_dates` says which kind it was.
      3. `instrumentType` must be what an operating company is: SHLD today is
         "Global X Defense Tech ETF" and answers every other check.
      4. OTC venue: FLAGGED here, not rejected (see the module docstring), and
         rejected only when the caller asks for a national exchange.

    One check `resolve_listing` does not make, because only a bulk caller can:
    if `pit_listing` already holds (symbol, first_trade_date) for a DIFFERENT
    entity, the row is refused rather than silently reattributed. The store's
    natural key is (symbol, first_trade_date), so an upsert would otherwise move
    an existing listing to whichever issuer asked last.

    Confidence is `proved` only with `proof` -- a filed `dei:TradingSymbol` --
    and `inferred` otherwise. Never `proved` on ticker alone.
    """
    as_of_day = _day(as_of)
    if as_of_day is None:
        raise ValueError(f"as_of must be a date: {as_of!r}")
    symbol = (symbol or "").strip().upper()

    result: dict[str, Any] = {
        "symbol": symbol, "entity_id": entity_id, "as_of": as_of_day,
        "accepted": False, "verdict": VERDICT_REJECTED, "listing_id": None,
        "confidence": CONFIDENCE_UNVERIFIED, "reason": REJECT_NO_YAHOO_DATA,
        "first_trade_date": None, "instrument_type": None, "exchange": None,
        "currency": None, "long_name": None, "otc_flag": 0,
        "http_status": chart.get("status"), "granularity": chart.get("granularity"),
    }

    meta = chart.get("meta")
    if not meta:
        if chart.get("status") in (0, 404):
            result["verdict"] = (VERDICT_NO_DATA if chart.get("status") == 404
                                 else VERDICT_ERROR)
            result["reason"] = (REJECT_NO_YAHOO_DATA if chart.get("status") == 404
                                else STATUS_HTTP_ERROR)
        else:
            result["verdict"] = VERDICT_REJECTED
            result["reason"] = REJECT_NO_META
        return result

    if chart.get("granularity") != "1d":
        result["reason"] = REJECT_NOT_DAILY
        return result

    first_trade = exchange_day(meta.get("firstTradeDate"), meta.get("gmtoffset"))
    exchange = meta.get("fullExchangeName") or meta.get("exchangeName")
    venue = (exchange or "").lower()
    result.update({
        "first_trade_date": first_trade,
        "instrument_type": meta.get("instrumentType"),
        "exchange": exchange,
        "currency": meta.get("currency"),
        "long_name": meta.get("longName") or meta.get("shortName"),
        "otc_flag": 1 if any(m in venue for m in OTC_EXCHANGE_MARKERS) else 0,
    })

    if first_trade is None:
        result["reason"] = REJECT_NO_FIRST_TRADE
        return result
    if first_trade > as_of_day:
        result["reason"] = REJECT_FIRST_TRADE_AFTER
        return result
    if expected_instrument_type and (result["instrument_type"] or "").upper() != \
            expected_instrument_type.upper():
        result["reason"] = REJECT_INSTRUMENT_TYPE
        return result
    if expect_national_exchange and result["otc_flag"]:
        result["reason"] = REJECT_OTC
        return result

    claimed = conn.execute(
        """SELECT listing_id, entity_id FROM pit_listing
            WHERE symbol = ? AND first_trade_date IS ?""",
        (symbol, first_trade)).fetchone()
    if claimed and claimed["entity_id"] is not None \
            and int(claimed["entity_id"]) != int(entity_id):
        result["reason"] = REJECT_CLAIMED_BY_OTHER_ENTITY
        result["claimed_by_entity_id"] = int(claimed["entity_id"])
        return result

    result["accepted"] = True
    result["verdict"] = VERDICT_ACCEPTED
    result["reason"] = ACCEPT_REASON
    result["confidence"] = CONFIDENCE_PROVED if proof else CONFIDENCE_INFERRED
    if proof:
        result["proof"] = proof

    if persist:
        exit_row = conn.execute(
            "SELECT exit_date FROM pit_entity_exit WHERE entity_id = ?",
            (entity_id,)).fetchone()
        result["listing_id"] = _with_lock_retry(
            pit_store.upsert_listing, conn, symbol, first_trade,
            entity_id=entity_id, exchange=exchange,
            instrument_type=result["instrument_type"], currency=result["currency"],
            valid_from=first_trade,
            valid_to=(_day(exit_row["exit_date"]) if exit_row else None),
            confidence=result["confidence"], source=SOURCE_YAHOO_CHART)
    return result


def record_gate(conn: sqlite3.Connection, gate: dict, **fields) -> str:
    """Write one gate verdict to `pit_listing_gate`. Returns the verdict stored.

    Every attempt is recorded, accepted or not. The rejection list is a result
    in its own right: it is the only place a reader can learn that SHLD is now
    an ETF, or that 21 of 26 delisted tickers simply 404.
    """
    row = (
        gate["entity_id"], gate["symbol"], gate["as_of"], gate["verdict"],
        gate["reason"], gate.get("listing_id"), gate.get("confidence"),
        gate.get("first_trade_date"), gate.get("instrument_type"),
        gate.get("exchange"), int(gate.get("otc_flag", 0)), gate.get("long_name"),
        gate.get("http_status"), gate.get("granularity"),
        fields.get("n_bars"), fields.get("bar_first"), fields.get("bar_last"),
        fields.get("n_zero_volume_bars"), fields.get("n_splits"),
        fields.get("n_spinoffs"), fields.get("n_dividends"),
        fields.get("priority_tier"),
        pit_store.dumps(fields.get("evidence")), fields.get("ingest_id"),
        PRICE_SOURCE_VERSION, _now(),
    )

    def _write():
        with transaction(conn):
            conn.execute(
                """INSERT INTO pit_listing_gate
                       (entity_id, symbol, as_of_date, verdict, reason, listing_id,
                        confidence, first_trade_date, instrument_type, exchange,
                        otc_flag, long_name, http_status, granularity, n_bars,
                        bar_first, bar_last, n_zero_volume_bars, n_splits,
                        n_spinoffs, n_dividends, priority_tier, evidence_json,
                        ingest_id, source_version, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (entity_id, symbol, as_of_date) DO UPDATE SET
                        verdict = excluded.verdict,
                        reason = excluded.reason,
                        listing_id = excluded.listing_id,
                        confidence = excluded.confidence,
                        first_trade_date = excluded.first_trade_date,
                        instrument_type = excluded.instrument_type,
                        exchange = excluded.exchange,
                        otc_flag = excluded.otc_flag,
                        long_name = excluded.long_name,
                        http_status = excluded.http_status,
                        granularity = excluded.granularity,
                        n_bars = excluded.n_bars,
                        bar_first = excluded.bar_first,
                        bar_last = excluded.bar_last,
                        n_zero_volume_bars = excluded.n_zero_volume_bars,
                        n_splits = excluded.n_splits,
                        n_spinoffs = excluded.n_spinoffs,
                        n_dividends = excluded.n_dividends,
                        priority_tier = excluded.priority_tier,
                        evidence_json = excluded.evidence_json,
                        ingest_id = excluded.ingest_id,
                        source_version = excluded.source_version,
                        updated_at = excluded.updated_at""",
                row)

    _with_lock_retry(_write)
    return gate["verdict"]


# --------------------------------------------------------------------------
# 4. Bars and corporate actions
# --------------------------------------------------------------------------

BAR_COLUMNS = ("listing_id", "bar_date", "open", "high", "low", "close",
               "adjclose", "volume", "source_symbol", "ingest_id")

_BAR_INSERT = (
    "INSERT OR IGNORE INTO pit_price_bar (" + ", ".join(BAR_COLUMNS) + ") VALUES ("
    + ", ".join("?" * len(BAR_COLUMNS)) + ")"
)


def bars_from_chart(chart: dict, listing_id: int, symbol: str, ingest_id: int,
                    start: str = PRICE_START) -> tuple[list[tuple], dict]:
    """Chart payload -> `pit_price_bar` tuples, plus what was seen doing it.

    BOTH `close` and `adjclose` are kept, because they answer different
    questions and neither substitutes for the other. `adjclose` is split AND
    dividend adjusted and is the correct input to a RETURN label (verified
    window-invariant to 2.5e-7). `close` is split-adjusted only: AAPL's
    2015-06-30 close reads 31.3575 even in a window that ends in 2019, and the
    true as-traded price of 125.43 is that times the later 4:1 split. Narrowing
    the request does not recover it -- only the corporate-action record does.

    A row with no usable price at all is dropped; a row with volume 0 is KEPT
    and counted. Stale zero-volume prints sit exactly at the events that matter:
    FRCB printed $3.51 at zero volume for two sessions after the FDIC seizure
    and the next real trade was $0.3336. Dropping them here would hide the
    halt; counting them lets a selector refuse them (`scored_universe_as_of`
    already requires volume > 0).
    """
    timestamps = chart.get("timestamps") or []
    quote = chart.get("quote") or {}
    adj = chart.get("adjclose") or []
    opens, highs, lows, closes, volumes = (quote.get("open") or [],
                                           quote.get("high") or [],
                                           quote.get("low") or [],
                                           quote.get("close") or [],
                                           quote.get("volume") or [])
    gmtoffset = (chart.get("meta") or {}).get("gmtoffset")

    def at(series: Sequence, index: int):
        if index < len(series):
            value = series[index]
            if value is None:
                return None
            try:
                number = float(value)
            except (TypeError, ValueError):
                return None
            return None if math.isnan(number) else number
        return None

    rows: list[tuple] = []
    zero_volume = 0
    for index, stamp in enumerate(timestamps):
        day = exchange_day(stamp, gmtoffset)
        if day is None or day < start:
            continue
        o, h, l, c = (at(opens, index), at(highs, index),
                      at(lows, index), at(closes, index))
        a = at(adj, index)
        v = at(volumes, index)
        if o is None and h is None and l is None and c is None and a is None:
            continue
        if v == 0:
            zero_volume += 1
        rows.append((listing_id, day, o, h, l, c, a, v, symbol, ingest_id))

    stats = {
        "n_bars": len(rows),
        "bar_first": rows[0][1] if rows else None,
        "bar_last": rows[-1][1] if rows else None,
        "n_zero_volume_bars": zero_volume,
    }
    return rows, stats


def classify_split(numerator: Any, denominator: Any) -> dict:
    """Is this Yahoo `split` event a share split or a spin-off? Decide and say why.

    SPIN-OFFS ARRIVE AS SPLITS. AT&T's 2022-04-11 event carries splitRatio
    '1324:1000' in the same array that holds GE's genuine 1:8 reverse split. As
    a PRICE adjustment it is right: the shares really did reprice by that factor
    when Warner separated. As a SHARE-COUNT divisor it is wrong by 32.4%, and
    the error is silent because the row looks exactly like a split.

    TWO WAYS TO BE A SPLIT, and the second was learned from real data. A ratio
    with 1 on either side is a split however large the other side is: 1:100 and
    1:200 reverse splits are routine for a distressed name, and a bound alone
    would have filed them as spin-offs -- leaving the share count unchanged
    through a 100x consolidation. Otherwise both reduced sides must fall within
    `CLEAN_RATIO_BOUND`: a transfer agent announces 4:1, 1:8, 3:2, 6:5, even a
    21:20 stock dividend. AT&T's 1324:1000 reduces to 331:250, Danaher's
    1128:1000 to 141:125 and eBay's 2376:1000 to 297:125 -- none has a 1
    anywhere and none is small, which is what a separation factor looks like.

    The bound is a JUDGEMENT, so the reasoning is written onto the row
    (`pit_corporate_action` has no notes column, so it rides in `source`) and a
    later reader can disagree with the specific number rather than guess at it.
    `reclassify_splits` re-runs this function over stored rows when they do.

    Returns {'event_type', 'applies_to_price', 'applies_to_shares', 'ratio_num',
    'ratio_den', 'note'}.
    """
    try:
        num = float(numerator)
        den = float(denominator)
    except (TypeError, ValueError):
        return {"event_type": EVENT_SPLIT, "applies_to_price": 1,
                "applies_to_shares": 0, "ratio_num": None, "ratio_den": None,
                "note": "unparseable_ratio;shares_not_applied"}
    if num <= 0 or den <= 0:
        return {"event_type": EVENT_SPLIT, "applies_to_price": 1,
                "applies_to_shares": 0, "ratio_num": num, "ratio_den": den,
                "note": "non_positive_ratio;shares_not_applied"}

    clean = False
    rule = "non_integer_ratio"
    reduced = f"{num:g}:{den:g}"
    if float(num).is_integer() and float(den).is_integer():
        gcd = math.gcd(int(num), int(den))
        rn, rd = int(num) // gcd, int(den) // gcd
        reduced = f"{rn}:{rd}"
        # Two ways to be clean, and the second one matters. A ratio with 1 on
        # either side is a split by construction however large the other side
        # is: 1:100 and 1:200 reverse splits are routine for a distressed name,
        # and a bound alone would file them as spin-offs. A spin-off factor is
        # never a whole number of shares per share -- AT&T's is 331:250 and
        # Danaher's 141:125, neither of which has a 1 anywhere.
        if rn == 1 or rd == 1:
            clean, rule = True, "one_side_is_unity"
        elif rn <= CLEAN_RATIO_BOUND and rd <= CLEAN_RATIO_BOUND:
            clean, rule = True, f"both_sides<={CLEAN_RATIO_BOUND}"
        else:
            rule = f"neither_side_unity;exceeds_clean_bound_{CLEAN_RATIO_BOUND}"

    if clean:
        return {"event_type": EVENT_SPLIT, "applies_to_price": 1,
                "applies_to_shares": 1, "ratio_num": num, "ratio_den": den,
                "note": (f"reduced={reduced};{rule};"
                         "verdict=share_split;price+shares")}
    return {"event_type": EVENT_SPINOFF, "applies_to_price": 1,
            "applies_to_shares": 0, "ratio_num": num, "ratio_den": den,
            "note": f"reduced={reduced};{rule};verdict=spin_off;price_only"}


def actions_from_chart(chart: dict, listing_id: int,
                       start: str = PRICE_START) -> tuple[list[tuple], dict]:
    """Chart `events` -> `pit_corporate_action` tuples, splits classified.

    Dividends set applies_to_shares = 0: cash changes the price series and the
    share count not at all. Splits are routed through `classify_split`.
    """
    events = chart.get("events") or {}
    gmtoffset = (chart.get("meta") or {}).get("gmtoffset")
    rows: list[tuple] = []
    counts = {"n_splits": 0, "n_spinoffs": 0, "n_dividends": 0}

    for payload in (events.get("splits") or {}).values():
        day = exchange_day(payload.get("date"), gmtoffset)
        if day is None or day < start:
            continue
        verdict = classify_split(payload.get("numerator"), payload.get("denominator"))
        source = (f"{SOURCE_YAHOO_EVENTS}|raw={payload.get('splitRatio')}"
                  f"|{verdict['note']}")
        rows.append((listing_id, day, verdict["event_type"], verdict["ratio_num"],
                     verdict["ratio_den"], None, verdict["applies_to_price"],
                     verdict["applies_to_shares"], CONFIDENCE_INFERRED, source))
        if verdict["event_type"] == EVENT_SPINOFF:
            counts["n_spinoffs"] += 1
        else:
            counts["n_splits"] += 1

    for payload in (events.get("dividends") or {}).values():
        day = exchange_day(payload.get("date"), gmtoffset)
        if day is None or day < start:
            continue
        try:
            amount = float(payload.get("amount"))
        except (TypeError, ValueError):
            continue
        rows.append((listing_id, day, EVENT_DIVIDEND, None, None, amount, 1, 0,
                     CONFIDENCE_INFERRED,
                     f"{SOURCE_YAHOO_EVENTS}|cash_distribution;price_only"))
        counts["n_dividends"] += 1

    return rows, counts


def write_bars(conn: sqlite3.Connection, bars: Sequence[tuple],
               actions: Sequence[tuple]) -> tuple[int, int]:
    """Write one symbol's bars and corporate actions in a single transaction.

    INSERT OR IGNORE against the store's own unique keys, so a resumed run
    cannot double-count. Note the trap this guards: `pit_price_bar`'s key is
    (listing_id, bar_date, INGEST_ID), so a re-run under a NEW ingest id would
    happily store a second copy of every bar. Resumption therefore skips a
    listing that already has bars rather than relying on the key.
    """
    def _write() -> tuple[int, int]:
        with transaction(conn):
            n_bars = 0
            if bars:
                cursor = conn.executemany(_BAR_INSERT, bars)
                n_bars = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
            n_actions = 0
            if actions:
                cursor = conn.executemany(
                    """INSERT OR IGNORE INTO pit_corporate_action
                           (listing_id, event_date, event_type, ratio_num, ratio_den,
                            cash_amount, applies_to_price, applies_to_shares,
                            confidence, source)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    actions)
                n_actions = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
            return n_bars, n_actions

    return _with_lock_retry(_write)


def reclassify_splits(conn: sqlite3.Connection) -> dict:
    """Re-run `classify_split` over stored split/spin-off rows and fix disagreements.

    The classifier is a JUDGEMENT about a ratio, not a published fact, and a
    judgement gets revised -- the unity rule was added after a 1:100 reverse
    split was found filing itself as a spin-off. Rows stored under the older
    rule would otherwise keep a verdict the code no longer stands behind, and
    the difference is a share count, so they are corrected in place rather than
    left to disagree with the function that claims to produce them.

    Corporate actions are not append-only (unlike `pit_fact`), and the row keeps
    its own reasoning in `source`, so the change is visible to a later reader.
    Returns counts of what moved.
    """
    rows = conn.execute(
        """SELECT rowid, listing_id, event_date, event_type, ratio_num, ratio_den, source
             FROM pit_corporate_action
            WHERE event_type IN (?, ?)""",
        (EVENT_SPLIT, EVENT_SPINOFF)).fetchall()
    changes = []
    for row in rows:
        verdict = classify_split(row["ratio_num"], row["ratio_den"])
        if verdict["event_type"] == row["event_type"]:
            continue
        raw = (row["source"] or "").split("|")
        prefix = "|".join(raw[:2]) if len(raw) > 1 else SOURCE_YAHOO_EVENTS
        changes.append((verdict["event_type"], verdict["applies_to_price"],
                        verdict["applies_to_shares"],
                        f"{prefix}|{verdict['note']}|reclassified_from={row['event_type']}",
                        row["rowid"]))
    if changes:
        def _write():
            with transaction(conn):
                conn.executemany(
                    """UPDATE pit_corporate_action
                          SET event_type = ?, applies_to_price = ?,
                              applies_to_shares = ?, source = ?
                        WHERE rowid = ?""", changes)
        _with_lock_retry(_write)
    return {"examined": len(rows), "reclassified": len(changes),
            "to_split": sum(1 for c in changes if c[0] == EVENT_SPLIT),
            "to_spinoff": sum(1 for c in changes if c[0] == EVENT_SPINOFF)}


def attach_filed_proof(conn: sqlite3.Connection) -> dict:
    """Raise a passing gate's confidence to `proved` where a filed symbol backs it.

    THE EVIDENCE WAS ALWAYS IN THE STORE. A listing this module accepts came
    from a `pit_symbol_obs` row -- a dated `dei:TradingSymbol` read off a filed
    cover page -- and `pit_identity.resolve_listing` defines exactly that as the
    difference between `inferred` and `proved`. A run that gated without passing
    the proof through leaves every listing recorded WEAKER than the evidence
    already in the store supports, which is its own kind of dishonesty.

    The bar for the upgrade is deliberately not "the ticker matches": the filed
    observation must have been AVAILABLE at or before the date the gate was
    tested at, so a symbol first reported in 2019 cannot retroactively prove a
    2015 listing. Nothing about the accept/reject decision changes here; only
    the recorded strength of the evidence behind an acceptance already made.

    This does NOT close the gate's blind spot. `proved` still means "the issuer
    filed this string and a series under it existed by then", never "this series
    is that issuer's" -- `T` in 2004 passes both tests and is still the wrong
    company.

    Returns counts. Idempotent: a second call upgrades nothing.
    """
    rows = conn.execute(
        """SELECT g.entity_id, g.symbol, g.as_of_date, g.listing_id
             FROM pit_listing_gate g
             JOIN pit_listing l ON l.listing_id = g.listing_id
            WHERE g.verdict = ? AND g.listing_id IS NOT NULL
              AND l.confidence = ?""",
        (VERDICT_ACCEPTED, CONFIDENCE_INFERRED)).fetchall()

    upgrades: list[tuple] = []
    unproved = 0
    for row in rows:
        obs = conn.execute(
            """SELECT source_accn, filed, available_date, source, source_version
                 FROM pit_symbol_obs
                WHERE entity_id = ? AND symbol = ? AND status = ?
                  AND available_date <= ?
                ORDER BY available_date DESC, source_accn DESC LIMIT 1""",
            (row["entity_id"], row["symbol"], pit_symbols.OBS_OK,
             row["as_of_date"])).fetchone()
        if obs is None:
            unproved += 1
            continue
        proof = json.dumps(
            {"tag": "dei:TradingSymbol", "accn": obs["source_accn"],
             "filed": obs["filed"], "available_date": obs["available_date"],
             "source": obs["source"], "source_version": obs["source_version"]},
            sort_keys=True)
        upgrades.append((row["listing_id"], proof, row["entity_id"],
                         row["symbol"], row["as_of_date"]))

    if upgrades:
        def _write():
            with transaction(conn):
                conn.executemany(
                    "UPDATE pit_listing SET confidence = ? WHERE listing_id = ?",
                    [(CONFIDENCE_PROVED, u[0]) for u in upgrades])
                conn.executemany(
                    """UPDATE pit_listing_gate
                          SET confidence = ?,
                              evidence_json = json_set(
                                  COALESCE(evidence_json, '{}'), '$.proof', ?)
                        WHERE entity_id = ? AND symbol = ? AND as_of_date = ?""",
                    [(CONFIDENCE_PROVED, u[1], u[2], u[3], u[4]) for u in upgrades])
        _with_lock_retry(_write)
    return {"examined": len(rows), "upgraded_to_proved": len(upgrades),
            "left_inferred_no_filed_symbol": unproved}



def listing_has_bars(conn: sqlite3.Connection, listing_id: int) -> bool:
    """Whether any ingest has already stored bars for this listing."""
    row = conn.execute(
        "SELECT 1 FROM pit_price_bar WHERE listing_id = ? LIMIT 1",
        (listing_id,)).fetchone()
    return row is not None


# --------------------------------------------------------------------------
# 5. One symbol, end to end
# --------------------------------------------------------------------------

def ingest_symbol(conn: sqlite3.Connection, entity_id: int, symbol: str,
                  as_of: str, ingest_id: int, *,
                  start: str = PRICE_START,
                  proof: Optional[str] = None,
                  priority_tier: Optional[int] = None,
                  expect_national_exchange: bool = False,
                  write: bool = True) -> dict:
    """Gate one symbol and, if it passes, store its bars and actions.

    One chart call. The gate reads `meta`; the ingest reads the same payload's
    bars. A rejected symbol writes a gate row and NOTHING else -- no listing, no
    bars -- because a price series attributed to the wrong issuer is worse than
    no price series at all.

    Returns a status dict whose `verdict` is one of accepted / rejected /
    no_data / error.
    """
    chart = fetch_chart(symbol, start=start)
    gate = gate_chart(conn, symbol, entity_id, as_of, chart, proof=proof,
                      expect_national_exchange=expect_national_exchange,
                      persist=write)

    if not gate["accepted"]:
        if write:
            record_gate(conn, gate, priority_tier=priority_tier,
                        ingest_id=ingest_id,
                        evidence={"chart_error": chart.get("error")})
        gate.update({"n_bars": 0, "n_actions": 0, "bars_written": 0})
        return gate

    listing_id = gate["listing_id"]
    if write and listing_id is not None and listing_has_bars(conn, listing_id):
        gate.update({"verdict": VERDICT_ACCEPTED, "n_bars": 0, "n_actions": 0,
                     "bars_written": 0, "skipped": STATUS_ALREADY_INGESTED})
        record_gate(conn, gate, priority_tier=priority_tier, ingest_id=ingest_id,
                    evidence={"skipped": STATUS_ALREADY_INGESTED})
        return gate

    bars, stats = bars_from_chart(chart, listing_id or -1, symbol, ingest_id, start)
    actions, counts = actions_from_chart(chart, listing_id or -1, start)

    if not bars:
        # The gate passed on identity but the window holds no series. That is a
        # real answer about coverage, not an acceptance of a price history.
        gate["reason"] = REJECT_NO_BARS_IN_WINDOW
        if write:
            record_gate(conn, gate, priority_tier=priority_tier,
                        ingest_id=ingest_id, **stats, **counts)
        gate.update({"n_bars": 0, "n_actions": 0, "bars_written": 0})
        return gate

    written_bars = written_actions = 0
    if write:
        written_bars, written_actions = write_bars(conn, bars, actions)
        record_gate(conn, gate, priority_tier=priority_tier, ingest_id=ingest_id,
                    **stats, **counts)

    gate.update({"n_bars": stats["n_bars"], "n_actions": len(actions),
                 "bars_written": written_bars, "actions_written": written_actions,
                 **stats, **counts})
    return gate


# --------------------------------------------------------------------------
# 6. The run
# --------------------------------------------------------------------------

def ingest_prices(conn: sqlite3.Connection, candidates: Sequence[dict], *,
                  db_path: str = pit_store.DEFAULT_PIT_DB_PATH,
                  start: str = PRICE_START,
                  min_free_gib: float = DEFAULT_MIN_FREE_GIB,
                  max_bars: int = DEFAULT_MAX_BARS,
                  checkpoint_every: int = DEFAULT_CHECKPOINT_EVERY,
                  progress: bool = True) -> dict:
    """Gate and ingest `candidates` in order, stopping on the first hard limit.

    THE RUN STOPS ITSELF. Four boundaries, each reported by name:
    the free-space floor, the bar budget, a throttle signal, and the end of the
    candidate list. A run that stops at its own floor and says where it stopped
    is resumable; one that discovers the floor by filling the disk is not.

    Peak disk use and peak WAL size are MEASURED across the run, not estimated,
    and every checkpoint's busy flag is counted -- a run whose checkpoints were
    all busy grew a WAL it never reclaimed, which is the failure this guard
    exists for.
    """
    require_disk(db_path, min_free_gib)
    ingest_id = pit_store.start_ingest(
        conn, SOURCE_YAHOO_CHART,
        scope=json.dumps({"start": start, "n_candidates": len(candidates),
                          "version": PRICE_SOURCE_VERSION}, sort_keys=True))

    free_start = disk_free_gib(db_path)
    summary = {
        "ingest_id": ingest_id,
        "n_candidates": len(candidates),
        "n_attempted": 0,
        "verdicts": {},
        "reasons": {},
        "bars_written": 0,
        "actions_written": 0,
        "listings_accepted": 0,
        "accepted_without_bars": 0,
        "zero_volume_bars": 0,
        "splits": 0, "spinoffs": 0, "dividends": 0,
        "stopped_because": "candidates_exhausted",
        "boundary_index": len(candidates),
        "boundary_candidate": None,
        "free_gib_start": round(free_start, 3),
        "free_gib_min": round(free_start, 3),
        "peak_disk_used_mib": 0.0,
        "peak_wal_mib": round(wal_bytes(db_path) / 2 ** 20, 3),
        "checkpoints": 0,
        "checkpoints_busy": 0,
        "started_at": _now(),
    }
    rejections: list[dict] = []
    entity_outcomes: dict[int, dict] = {}

    def bump(mapping: str, key: str) -> None:
        summary[mapping][key] = summary[mapping].get(key, 0) + 1

    def measure() -> None:
        free = disk_free_gib(db_path)
        summary["free_gib_min"] = round(min(summary["free_gib_min"], free), 3)
        summary["peak_disk_used_mib"] = round(
            max(summary["peak_disk_used_mib"], (free_start - free) * 1024.0), 3)
        summary["peak_wal_mib"] = round(
            max(summary["peak_wal_mib"], wal_bytes(db_path) / 2 ** 20), 3)

    t0 = time.monotonic()
    try:
        for index, candidate in enumerate(candidates):
            if summary["bars_written"] >= max_bars:
                summary["stopped_because"] = "bar_budget_reached"
                summary["boundary_index"] = index
                summary["boundary_candidate"] = candidate["symbol"]
                break
            free = disk_free_gib(db_path)
            if free < min_free_gib:
                summary["stopped_because"] = "disk_floor_reached"
                summary["boundary_index"] = index
                summary["boundary_candidate"] = candidate["symbol"]
                break

            try:
                result = ingest_symbol(
                    conn, candidate["entity_id"], candidate["symbol"],
                    candidate["as_of_date"], ingest_id, start=start,
                    proof=candidate.get("proof"),
                    priority_tier=candidate.get("tier"))
            except ThrottleStop as exc:
                summary["stopped_because"] = f"throttled: {exc}"
                summary["boundary_index"] = index
                summary["boundary_candidate"] = candidate["symbol"]
                break

            summary["n_attempted"] += 1
            bump("verdicts", result["verdict"])
            bump("reasons", result["reason"])
            summary["bars_written"] += result.get("bars_written", 0) or 0
            summary["actions_written"] += result.get("actions_written", 0) or 0
            summary["zero_volume_bars"] += result.get("n_zero_volume_bars", 0) or 0
            summary["splits"] += result.get("n_splits", 0) or 0
            summary["spinoffs"] += result.get("n_spinoffs", 0) or 0
            summary["dividends"] += result.get("n_dividends", 0) or 0
            if result["verdict"] == VERDICT_ACCEPTED:
                # Identity accepted. A listing whose window holds no bars is
                # still a resolved identity -- it is a coverage gap, not a
                # rejection, and calling it one would overstate what the gate
                # proved. `scored_universe_as_of` refuses it anyway, on the
                # price recency test where that decision belongs.
                if result["reason"] == ACCEPT_REASON:
                    summary["listings_accepted"] += 1
                else:
                    summary["accepted_without_bars"] += 1
            else:
                rejections.append({
                    "symbol": result["symbol"], "entity_id": result["entity_id"],
                    "as_of": result["as_of"], "verdict": result["verdict"],
                    "reason": result["reason"],
                    "first_trade_date": result.get("first_trade_date"),
                    "instrument_type": result.get("instrument_type"),
                    "exchange": result.get("exchange"),
                    "long_name": result.get("long_name"),
                    "tier": candidate.get("tier"),
                })

            outcome = entity_outcomes.setdefault(
                int(candidate["entity_id"]),
                {"resolved": False, "symbol": None, "listing_id": None,
                 "reason": None, "as_of": candidate["as_of_date"]})
            if result["verdict"] == VERDICT_ACCEPTED and not outcome["resolved"]:
                outcome.update({"resolved": True, "symbol": result["symbol"],
                                "listing_id": result.get("listing_id"),
                                "reason": None})
            elif not outcome["resolved"]:
                outcome["reason"] = result["reason"]

            if (index + 1) % checkpoint_every == 0:
                report = checkpoint_wal(conn, db_path)
                summary["checkpoints"] += 1
                summary["checkpoints_busy"] += int(report["busy"] != 0)
                measure()
                if progress:
                    rate = (index + 1) / max(time.monotonic() - t0, 1e-6)
                    print(f"  [{index + 1}/{len(candidates)}] "
                          f"accepted={summary['listings_accepted']} "
                          f"bars={summary['bars_written']:,} "
                          f"free={summary['free_gib_min']:.2f}GiB "
                          f"wal={summary['peak_wal_mib']:.0f}MiB "
                          f"ckpt_busy={summary['checkpoints_busy']} "
                          f"{rate:.2f}/s", flush=True)
    finally:
        report = checkpoint_wal(conn, db_path)
        summary["checkpoints"] += 1
        summary["checkpoints_busy"] += int(report["busy"] != 0)
        summary["final_checkpoint"] = report
        measure()
        _roll_up_listing_status(conn, entity_outcomes)
        summary["finished_at"] = _now()
        summary["elapsed_seconds"] = round(time.monotonic() - t0, 1)
        summary["requests_per_second"] = round(
            summary["n_attempted"] / max(summary["elapsed_seconds"], 1e-6), 2)
        summary["rejections"] = rejections
        pit_store.finish_ingest(
            conn, ingest_id,
            status="complete" if summary["stopped_because"] == "candidates_exhausted"
            else "partial",
            rows_read=summary["n_attempted"],
            rows_kept=summary["bars_written"],
            rows_rejected=len(rejections),
            summary={k: v for k, v in summary.items() if k != "rejections"})
    return summary


def _roll_up_listing_status(conn: sqlite3.Connection,
                            outcomes: dict[int, dict]) -> None:
    """Write the per-entity conclusion that `pit_listing_status` is shaped for.

    `pit_listing_gate` holds one row per (entity, symbol) attempt; this holds one
    per issuer, which is what `scored_universe_as_of` and the coverage reports
    read. An entity with any accepted symbol is LISTING_RESOLVED; one with none
    is LISTING_UNRESOLVED with the reason its last attempt gave.
    """
    for entity_id, outcome in outcomes.items():
        if outcome["resolved"]:
            _with_lock_retry(
                pit_symbols.set_listing_status, conn, entity_id,
                pit_symbols.LISTING_RESOLVED,
                resolved_symbol=outcome["symbol"], listing_id=outcome["listing_id"],
                evidence={"as_of": outcome["as_of"], "gate": PRICE_SOURCE_VERSION})
        else:
            _with_lock_retry(
                pit_symbols.set_listing_status, conn, entity_id,
                pit_symbols.LISTING_UNRESOLVED,
                reason=pit_symbols.UNRESOLVED_PRICE_GATE_FAILED,
                evidence={"as_of": outcome["as_of"], "gate": PRICE_SOURCE_VERSION,
                          "gate_reason": outcome["reason"]})


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def coverage_report(conn: sqlite3.Connection) -> dict:
    """What the store holds now: listings, bars, actions and gate verdicts."""
    out: dict[str, Any] = {}
    for table in ("pit_listing", "pit_price_bar", "pit_corporate_action",
                  "pit_listing_gate", "pit_listing_status"):
        try:
            out[table] = int(conn.execute(
                f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])
        except sqlite3.Error:
            out[table] = -1
    out["verdicts"] = {r["verdict"]: r["n"] for r in conn.execute(
        "SELECT verdict, COUNT(*) AS n FROM pit_listing_gate GROUP BY verdict")}
    out["reasons"] = {r["reason"]: r["n"] for r in conn.execute(
        "SELECT reason, COUNT(*) AS n FROM pit_listing_gate GROUP BY reason "
        "ORDER BY n DESC")}
    row = conn.execute(
        "SELECT MIN(bar_date) AS lo, MAX(bar_date) AS hi FROM pit_price_bar"
    ).fetchone()
    out["bar_range"] = [row["lo"], row["hi"]]
    out["actions"] = {r["event_type"]: r["n"] for r in conn.execute(
        "SELECT event_type, COUNT(*) AS n FROM pit_corporate_action "
        "GROUP BY event_type")}
    return out


def suspect_first_trade_dates(conn: sqlite3.Connection,
                              min_entities: int = 5) -> list[sqlite3.Row]:
    """First-trade dates shared by many unrelated issuers, ranked by the harm.

    A shared date means the field is reporting the SERIES, not the instrument,
    and there are two flavours with opposite consequences:

    HARMLESS -- a vendor history epoch. 173 issuers share 1980-03-17, 65 share
    1973-02-21 and 26 share 1962-01-02 (the CRSP NYSE daily start). Boeing did
    not begin trading in 1962; that is simply where the history begins. These
    dates precede every as-of a replay asks about, so the gate passes and no
    decision turns on them.

    HARMFUL -- a truncated feed. 28 issuers share 2026-07-17, BBBY's date, among
    them AvalonBay, Electronic Arts, Leggett & Platt and Webster Financial, each
    returning between 1 and 45 bars and no history at all before it. AvalonBay
    has been listed continuously since 1994. For these the gate rejects, and the
    reason `first_trade_after_as_of` invites exactly the wrong reading: "this
    ticker was reassigned to a younger instrument". For BBBY that reassignment
    is real and documented; for AvalonBay it is a broken feed.

    The gate's DECISION is right in both cases -- a payload with no series
    before the as-of has nothing to attribute -- so this function changes no
    verdict. It exists so a reader is told which rejections mean "the history is
    missing" rather than "the ticker was reused", which is the difference
    between a vendor problem to re-pull and a real identity finding.

    Ranked by `n_rejected`, because a shared date that rejected nothing cost
    nothing. Returns (first_trade_date, n_entities, n_symbols, n_rejected,
    symbols).
    """
    return conn.execute(
        """SELECT first_trade_date,
                  COUNT(DISTINCT entity_id) AS n_entities,
                  COUNT(*) AS n_symbols,
                  SUM(CASE WHEN verdict != ? THEN 1 ELSE 0 END) AS n_rejected,
                  GROUP_CONCAT(symbol) AS symbols
             FROM pit_listing_gate
            WHERE first_trade_date IS NOT NULL
            GROUP BY first_trade_date
           HAVING COUNT(DISTINCT entity_id) >= ?
            ORDER BY n_rejected DESC, n_entities DESC""",
        (VERDICT_ACCEPTED, min_entities)).fetchall()


def rejection_report(conn: sqlite3.Connection, limit: int = 40) -> list[sqlite3.Row]:
    """The rejection list, most interesting first.

    "Interesting" is ordered deliberately: a symbol reassigned to a younger
    instrument and one that is now a fund are identity failures the gate exists
    to catch, and they belong above a plain 404.
    """
    return conn.execute(
        """SELECT g.*, e.cik FROM pit_listing_gate g
             JOIN pit_entity e ON e.entity_id = g.entity_id
            WHERE g.verdict != 'accepted'
            ORDER BY CASE g.reason
                        WHEN ? THEN 0 WHEN ? THEN 1 WHEN ? THEN 2
                        WHEN ? THEN 3 WHEN ? THEN 4 ELSE 5 END,
                     g.priority_tier, g.symbol
            LIMIT ?""",
        (REJECT_FIRST_TRADE_AFTER, REJECT_INSTRUMENT_TYPE,
         REJECT_CLAIMED_BY_OTHER_ENTITY, REJECT_NOT_DAILY, REJECT_NO_META, limit),
    ).fetchall()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    """Resolve listings and ingest prices, bounded by disk and by politeness."""
    args = list(sys.argv[1:] if argv is None else argv)

    def opt(name: str, default: Any) -> Any:
        if name in args:
            return args[args.index(name) + 1]
        return default

    db_path = str(opt("--db", pit_store.DEFAULT_PIT_DB_PATH))
    limit = int(opt("--limit", 0)) or None
    max_bars = int(opt("--max-bars", DEFAULT_MAX_BARS))
    min_free = float(opt("--min-free-gib", DEFAULT_MIN_FREE_GIB))
    start = str(opt("--start", PRICE_START))
    only = [s.strip().upper() for s in str(opt("--symbols", "")).split(",") if s.strip()]

    conn = connect(db_path)
    print(ensure_schema(conn))
    free = disk_free_gib(db_path)
    print(f"disk: {free:.2f} GiB free, floor {min_free:.2f} GiB, "
          f"wal {wal_bytes(db_path) / 2 ** 20:.1f} MiB")
    if free < min_free:
        print("ABORT: below the free-space floor before any write.")
        return 2

    print("building candidate set ...", flush=True)
    t0 = time.monotonic()
    candidates = candidate_listings(conn, limit=None)
    if only:
        candidates = [c for c in candidates if c["symbol"] in only]
    tiers: dict[int, int] = {}
    for candidate in candidates:
        tiers[candidate["tier"]] = tiers.get(candidate["tier"], 0) + 1
    print(f"  {len(candidates):,} candidates in {time.monotonic() - t0:.1f}s; "
          f"tiers {dict(sorted(tiers.items()))}")
    if limit:
        candidates = candidates[:limit]

    summary = ingest_prices(conn, candidates, db_path=db_path, start=start,
                            min_free_gib=min_free, max_bars=max_bars)
    printable = {k: v for k, v in summary.items() if k != "rejections"}
    print(json.dumps(printable, indent=2, sort_keys=True))

    # Two repair passes that re-read evidence the store already holds rather
    # than re-fetching anything.
    print("proof:", json.dumps(attach_filed_proof(conn), sort_keys=True))
    print("reclassify:", json.dumps(reclassify_splits(conn), sort_keys=True))
    print(json.dumps(coverage_report(conn), indent=2, sort_keys=True))

    suspects = suspect_first_trade_dates(conn)
    if suspects:
        print("\nFIRST TRADE DATES SHARED BY UNRELATED ISSUERS "
              "(these rejections mean 'history missing', not 'ticker reused'):")
        for row in suspects:
            print(f"  {row['first_trade_date']}  {row['n_entities']:>4} entities  "
                  f"{row['n_rejected']:>4} rejected  {(row['symbols'] or '')[:80]}")

    print("\nREJECTIONS, most interesting first:")
    for row in rejection_report(conn, limit=10):
        print(f"  {row['symbol']:<8} {row['verdict']:<9} {row['reason']:<26} "
              f"first_trade={row['first_trade_date']} "
              f"{row['instrument_type'] or ''} {row['exchange'] or ''} "
              f"{(row['long_name'] or '')[:36]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
