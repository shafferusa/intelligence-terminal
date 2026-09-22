"""Historical trading symbols, as REPORTED, one observation per filing.

This module answers one question and refuses to answer a second. The question
it answers is:

    On <date>, what ticker had this issuer most recently TOLD THE SEC it traded
    under, in a filing a reader could already have seen?

The question it refuses is "what price series belongs to this issuer", which is
`pit_identity.resolve_listing`'s job and stays its job. The distinction is the
whole design. A `dei:TradingSymbol` on a cover page proves the FILER REPORTED a
symbol on that date; it does not prove the symbol was tradeable, that the line
was common stock, or that today's Yahoo series under that symbol is the same
instrument. Old GM is the proof: CIK 40730 kept filing 10-Ks as MOTORS
LIQUIDATION CO through at least 2019, reporting `CK0000040730` in 2013 and
`MTLQQ` from 2015 -- a filed symbol, a real issuer, and a pink-sheet husk that
no equity strategy could hold.

WHY THIS SOURCE. `dei:TradingSymbol` is a NON-NUMERIC fact. DERA's compact
Financial Statement `num.txt` carries only numeric facts, so it cannot carry a
ticker at any price. The SEC Financial Statement AND NOTES data sets publish a
`txt.tsv` member holding every non-numeric tagged fact at accession level, and
that is the only bulk, as-filed, point-in-time source for this tag that exists.
Measured 2026-09-20, and every alternative was checked first:

    companyconcept dei/TradingSymbol    HTTP 404 for every CIK tried
    companyfacts, `dei` taxonomy        EntityCommonStockSharesOutstanding and
                                        EntityPublicFloat ONLY -- no TradingSymbol
    submissions API `tickers`           a CURRENT survivor snapshot: [] for
                                        Twitter, BBBY, Sears, SVB and old GM,
                                        and BBBY's `name` now reads
                                        "20230930-DK-Butterfly-1, Inc."

The last line is the reason this module exists. Every free per-company ticker
source is a snapshot of the survivors, and a snapshot of the survivors is the
exact bias the point-in-time store was built to remove.

THREE INVARIANTS.

1. AN OBSERVATION ATTACHES TO THE FILING THAT REPORTED IT. `pit_symbol_obs` is
   keyed on the accession and has NO `valid_to` column, because a filing cannot
   know when its own statement stopped being true. A CIK -> ticker field would
   be a timeless assertion assembled from the future, which is the specific
   shape of look-ahead this store exists to prevent.

2. A DERIVED INTERVAL IS LABELLED AND IS NOT A REPLAY SOURCE.
   `pit_symbol_interval` may be built ONLY after observations are loaded, its
   `is_derived` column is pinned to 1 by a CHECK, and it records
   `derived_through` -- the latest availability date in the evidence it was
   built from. `symbol_as_of` never reads it. Reporting and human inspection
   read it; a replay does not.

3. IDENTITY THAT CANNOT BE PROVED IS A STATUS, NOT A DELETION. An entity whose
   line cannot be resolved is marked `LISTING_UNRESOLVED`. It STAYS in the peer
   universe -- it was a live reporting company and its fundamentals belong in
   the percentile -- and it is excluded from the SCORED universe, because a
   position cannot be taken in a line that cannot be identified. Dropping it
   from both would reintroduce survivorship through the back door.

MEASURED COVERAGE (SEC Notes data sets, 2026-09-20, live):

    quarter   filings   accessions with a symbol
    2009q4        486      171  (35.2%)   <- the tag is live from the first
    2010q4      1,475      371  (25.2%)      Notes quarter, 2009q3
    2011q4      8,016    1,703  (21.2%)
    2013q4      7,850    2,160  (27.5%)
    2015q4      7,234    3,318  (45.9%)
    2017q4      6,550    3,429  (52.4%)
    2018q1      5,992    3,052  (50.9%)
    2019q2      7,300    4,015  (55.0%)
    2022q1     23,658   20,267  (85.7%)
    2023q1     25,942   21,297  (82.1%)

The 2019 cover-page rule is a COMPLETENESS step, not the floor: `TradingSymbol`
is populated from 2009-10-01. What the rule did add is the companions --
`Security12bTitle` and `SecurityExchangeName` are absent in 2018q1, appear on
15 rows in 2019q2, and are near-universal by 2022. That asymmetry is why
`security_title` is NULLABLE here and why a NULL title is never read as "not
common stock".
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import sqlite3
from typing import Any, Iterable, Optional, Sequence

import pit_identity
import pit_policy
import pit_store
from pit_store import transaction

__all__ = [
    "SCHEMA", "ensure_schema", "SYMBOL_SOURCE_VERSION", "INTERVAL_DERIVATION",
    "SOURCE_DERA_NOTES", "SOURCE_FILING_COVER",
    "OBS_OK", "OBS_CIK_PLACEHOLDER", "OBS_NOT_TICKER_SHAPED", "OBS_EMPTY",
    "LISTING_RESOLVED", "LISTING_UNRESOLVED",
    "UNRESOLVED_NO_OBSERVATION", "UNRESOLVED_AMBIGUOUS_CLASS",
    "UNRESOLVED_PLACEHOLDER_ONLY", "UNRESOLVED_PRICE_GATE_FAILED",
    "normalise_symbol", "is_cik_placeholder", "looks_like_ticker",
    "insert_symbol_obs", "record_symbol_obs", "OBS_COLUMNS",
    "symbol_as_of", "symbols_as_of", "symbol_observations",
    "build_symbol_intervals", "symbol_intervals",
    "set_listing_status", "listing_status", "unresolved_entities",
    "peer_universe_as_of", "scored_universe_as_of",
    "resolve_listing_with_filed_symbol",
]


# --------------------------------------------------------------------------
# Versions. Never edited in place -- a change means a v2 constant beside v1.
# --------------------------------------------------------------------------

#: How a raw observation row was produced: which fields were read, how the
#: symbol was normalised, and which statuses were assigned.
SYMBOL_SOURCE_VERSION = "symbol_obs_v1_dera_notes"

#: How an interval row was folded out of observations. Stored on every derived
#: row so a reader can never mistake one for evidence.
INTERVAL_DERIVATION = "symbol_interval_v1_from_obs"

#: Where an observation came from. The quarter is appended by the loader
#: ("dera_notes:2023q1"), matching pit_fact.source's "dera:2021q2" shape.
SOURCE_DERA_NOTES = "dera_notes"
SOURCE_FILING_COVER = "sec:filing_cover"

#: Observation statuses. A rejected value is STORED with its status, never
#: dropped: "this filer reported a CIK placeholder" is a finding about the
#: source, and a silently discarded row cannot be counted later.
OBS_OK = "ok"
OBS_CIK_PLACEHOLDER = "cik_placeholder"
OBS_NOT_TICKER_SHAPED = "not_ticker_shaped"
OBS_EMPTY = "empty"

#: Entity listing statuses.
LISTING_RESOLVED = "LISTING_RESOLVED"
LISTING_UNRESOLVED = "LISTING_UNRESOLVED"

#: Why a listing is unresolved. Each is a different fact about the world and
#: they must not be collapsed into one flag.
UNRESOLVED_NO_OBSERVATION = "no_symbol_observation"
UNRESOLVED_AMBIGUOUS_CLASS = "ambiguous_share_class"
UNRESOLVED_PLACEHOLDER_ONLY = "only_placeholder_symbols"
UNRESOLVED_PRICE_GATE_FAILED = "price_series_gate_failed"

#: The EDGAR filler a registrant with no ticker may tag: literally "CK" plus
#: the ten-digit CIK. Old GM reported `CK0000040730` on its 2013-11-07 10-Q.
#: It is a well-formed string, it is not a ticker, and a shape test alone
#: accepts it -- which is why it gets its own named status.
_CIK_PLACEHOLDER = re.compile(r"^CK\d{10}$", re.IGNORECASE)

#: A national-market ticker: letters, digits, dot and hyphen, at most 14 long.
#: Deliberately PERMISSIVE. 805 of the 8,732 distinct 2023q1 values fail it --
#: `AIG PRA`, `BDX/26A`, `SNV - PrE` -- and essentially all of those are
#: preferred series or listed notes rather than common stock. They are stored
#: with `not_ticker_shaped` rather than discarded, because "this row is a note,
#: not a share" is information the share-class resolver needs.
_TICKER_SHAPE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,13}$")


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------

SCHEMA = """
-- =====================================================================
-- RAW OBSERVATIONS. One row per (filing, reported symbol, dimension).
-- Append-only, trigger-enforced, and deliberately WITHOUT a valid_to.
-- =====================================================================

-- `valid_from` is the reported period end and is NOT a claim about when the
-- symbol started; it is the filing's own dating of its cover page. There is no
-- `valid_to` on purpose: the filing that reported "ATVI" in February 2023 could
-- not know Activision would be acquired that October, and a column for the end
-- date would have to be filled from the future.
--
-- `segments` mirrors pit_fact's column name and holds the Notes dataset's
-- dimension hash (`dimh`). It is NOT emptied for the consolidated case the way
-- pit_fact's is: 15,997 of 32,131 2023q1 TradingSymbol rows are dimensional,
-- and among them are Apple and Microsoft, which tag their single class under a
-- dimension. Filtering to segments = '' would silently drop AAPL and MSFT.
CREATE TABLE IF NOT EXISTS pit_symbol_obs (
    obs_id                INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id             INTEGER NOT NULL REFERENCES pit_entity(entity_id),
    symbol                TEXT NOT NULL,       -- normalised, upper case
    symbol_raw            TEXT NOT NULL,       -- exactly as filed
    source_accn           TEXT NOT NULL,
    form                  TEXT,
    reported_period       TEXT,                -- the filing's ddate
    qtrs                  INTEGER NOT NULL DEFAULT 0,
    segments              TEXT NOT NULL DEFAULT '',
    coreg                 TEXT NOT NULL DEFAULT '',
    security_title        TEXT,                -- dei:Security12bTitle, 2019+
    exchange_name         TEXT,                -- dei:SecurityExchangeName, 2019+
    valid_from            TEXT,                -- reported period end; see above
    filed                 TEXT NOT NULL,
    accepted_raw          TEXT,
    accepted_eastern      TEXT,
    available_date        TEXT NOT NULL,
    latency_policy_version TEXT NOT NULL,
    status                TEXT NOT NULL DEFAULT 'ok',
    source                TEXT NOT NULL,
    source_version        TEXT NOT NULL,
    created_at            TEXT NOT NULL,
    UNIQUE (entity_id, source_accn, symbol, segments, coreg),
    CHECK (available_date >= filed)
);

CREATE INDEX IF NOT EXISTS idx_pit_symbol_obs_select
    ON pit_symbol_obs (entity_id, available_date, filed, source_accn);
CREATE INDEX IF NOT EXISTS idx_pit_symbol_obs_symbol
    ON pit_symbol_obs (symbol, available_date);

CREATE TRIGGER IF NOT EXISTS trg_pit_symbol_obs_no_update
BEFORE UPDATE ON pit_symbol_obs
BEGIN
    SELECT RAISE(ABORT,
        'pit_symbol_obs is append-only: a changed symbol is a new filing, not an edit');
END;

CREATE TRIGGER IF NOT EXISTS trg_pit_symbol_obs_no_delete
BEFORE DELETE ON pit_symbol_obs
BEGIN
    SELECT RAISE(ABORT,
        'pit_symbol_obs is append-only: a reported symbol is never unreported');
END;

-- =====================================================================
-- DERIVED INTERVALS. Built from observations, AFTER loading, and labelled.
-- =====================================================================

-- Every row here is a conclusion, not evidence. `valid_to` exists only in this
-- table and is always drawn from information later than `valid_from` -- an
-- interval ends because a LATER filing said something different, which is
-- exactly the future knowledge a replay must not touch.
--
-- `is_derived` is pinned by a CHECK rather than defaulted, so a row asserting
-- itself to be raw cannot be inserted here at all. `derived_through` is the
-- latest available_date among the observations that produced the row: it is
-- what tells a reader how much of the future this conclusion already contains.
--
-- `symbol_as_of` NEVER reads this table.
CREATE TABLE IF NOT EXISTS pit_symbol_interval (
    interval_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id             INTEGER NOT NULL REFERENCES pit_entity(entity_id),
    symbol                TEXT NOT NULL,
    valid_from            TEXT NOT NULL,       -- first available_date saying so
    valid_to              TEXT,                -- NULL = still current in evidence
    first_obs_accn        TEXT NOT NULL,
    last_obs_accn         TEXT NOT NULL,
    n_obs                 INTEGER NOT NULL,
    is_derived            INTEGER NOT NULL DEFAULT 1,
    derivation            TEXT NOT NULL,
    derived_through       TEXT NOT NULL,
    derived_at            TEXT NOT NULL,
    UNIQUE (entity_id, symbol, valid_from, derivation),
    CHECK (is_derived = 1),
    CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

CREATE INDEX IF NOT EXISTS idx_pit_symbol_interval_entity
    ON pit_symbol_interval (entity_id, valid_from);

-- =====================================================================
-- LISTING STATUS. Mutable by design: it is a CONCLUSION about identity that
-- improves as evidence arrives, not a published fact.
-- =====================================================================

CREATE TABLE IF NOT EXISTS pit_listing_status (
    entity_id             INTEGER PRIMARY KEY REFERENCES pit_entity(entity_id),
    status                TEXT NOT NULL,
    reason                TEXT,
    resolved_symbol       TEXT,
    listing_id            INTEGER REFERENCES pit_listing(listing_id),
    evidence_json         TEXT,
    source_version        TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    CHECK (status IN ('LISTING_RESOLVED', 'LISTING_UNRESOLVED'))
);

CREATE INDEX IF NOT EXISTS idx_pit_listing_status
    ON pit_listing_status (status);
"""


def ensure_schema(conn: sqlite3.Connection) -> str:
    """Create this module's tables if absent. Idempotent; safe to call always.

    Deliberately NOT folded into `pit_store.SCHEMA` yet: another ingest holds
    the write lock on the production store while this module is in pilot, and a
    self-contained `CREATE TABLE IF NOT EXISTS` script can be applied to a
    throwaway database without touching it. Consolidation is a later step.

    Returns a status string naming what it found.
    """
    before = _table_names(conn)
    conn.executescript(SCHEMA)
    conn.commit()
    after = _table_names(conn)
    created = sorted(after - before)
    return f"created {created}" if created else "already present"


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _day(value: Any) -> Optional[str]:
    """A DERA 'YYYYMMDD' or an ISO date, as 'YYYY-MM-DD'. None if unusable."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return _dt.date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------

def is_cik_placeholder(symbol: Optional[str]) -> bool:
    """Whether a filed value is EDGAR's "CK" + CIK filler rather than a ticker."""
    return bool(symbol) and bool(_CIK_PLACEHOLDER.match(symbol.strip()))


def looks_like_ticker(symbol: Optional[str]) -> bool:
    """Whether a normalised value has the shape of a national-market ticker."""
    return bool(symbol) and bool(_TICKER_SHAPE.match(symbol.strip()))


def normalise_symbol(raw: Optional[str]) -> tuple[str, str]:
    """Return (normalised symbol, status) for one filed value.

    Upper-casing is not cosmetic. Bed Bath & Beyond filed its symbol as the
    literal string `bbby` on every 10-Q measured from 2015 through 2019, and a
    case-sensitive join against a price source would have found nothing for a
    company that was trading the whole time. 142 of the 8,732 distinct 2023q1
    values are non-upper.

    Internal whitespace is collapsed but NOT removed: `SNV - PrE` and `SNV-PrE`
    are both filed, both are preferred series, and neither is a ticker. Making
    them look like one by stripping the spaces would be inventing a symbol.

    The status is a finding and is stored with the row, never used to drop it.
    """
    if raw is None:
        return "", OBS_EMPTY
    text = re.sub(r"\s+", " ", str(raw)).strip().upper()
    if not text:
        return "", OBS_EMPTY
    if is_cik_placeholder(text):
        return text, OBS_CIK_PLACEHOLDER
    if not looks_like_ticker(text):
        return text, OBS_NOT_TICKER_SHAPED
    return text, OBS_OK


def _is_common_stock(security_title: Optional[str]) -> Optional[bool]:
    """Tri-state: True / False / None where the title was never filed.

    None is a real and common answer, not a missing value to be defaulted.
    `dei:Security12bTitle` does not exist before the 2019 cover-page rule --
    zero rows in 2018q1, 15 in 2019q2 -- so every pre-2019 observation has a
    NULL title. Reading NULL as False would mark the entire pre-2019 archive
    "not common stock"; reading it as True would let a 2023 preferred series
    through. It stays unknown, and `symbols_as_of` handles unknown explicitly.
    """
    if security_title is None or not str(security_title).strip():
        return None
    return pit_identity.is_common_stock_class(security_title)


# --------------------------------------------------------------------------
# Writing observations
# --------------------------------------------------------------------------

OBS_COLUMNS = (
    "entity_id", "symbol", "symbol_raw", "source_accn", "form",
    "reported_period", "qtrs", "segments", "coreg", "security_title",
    "exchange_name", "valid_from", "filed", "accepted_raw", "accepted_eastern",
    "available_date", "latency_policy_version", "status", "source",
    "source_version", "created_at",
)

_OBS_INSERT = (
    "INSERT OR IGNORE INTO pit_symbol_obs (" + ", ".join(OBS_COLUMNS) + ") VALUES ("
    + ", ".join("?" * len(OBS_COLUMNS)) + ")"
)


def insert_symbol_obs(conn: sqlite3.Connection,
                      rows: Iterable[Sequence[Any]]) -> int:
    """Bulk-insert observation tuples in OBS_COLUMNS order. Returns rows added.

    INSERT OR IGNORE is safe here for the same reason it is safe in
    `pit_store.insert_facts`: the unique key contains the accession, so a
    conflict means this exact filing already reported this exact symbol under
    this exact dimension. It cannot overwrite -- the update trigger forbids it.
    """
    cursor = conn.executemany(_OBS_INSERT, rows)
    return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0


def record_symbol_obs(conn: sqlite3.Connection, entity_id: int, symbol_raw: Any,
                      source_accn: str, filed: Any, *,
                      accepted: Any = None,
                      form: Optional[str] = None,
                      reported_period: Any = None,
                      qtrs: int = 0,
                      segments: str = "",
                      coreg: str = "",
                      security_title: Optional[str] = None,
                      exchange_name: Optional[str] = None,
                      source: str = SOURCE_DERA_NOTES,
                      next_session: Any = None) -> str:
    """Record ONE reported symbol, deriving availability from `pit_policy`.

    Availability is computed by exactly the rule that governs every other fact
    in this store -- accepted at or before 15:30 ET is same-session, after is
    next-session, no timestamp is next-session always. A cover page is not
    special: an 8-K accepted at 16:31 ET announcing a ticker change is not
    tradeable information that afternoon, and 2023q1's own acceptance stamps run
    past the bell constantly (Apple's 2023-02-02 8-K is accepted 16:31:00).

    Returns a status string: 'inserted:<status>' or 'duplicate'.
    """
    filed_day = _day(filed)
    if filed_day is None:
        raise ValueError(f"filed is not a date: {filed!r}")
    symbol, status = normalise_symbol(symbol_raw)
    detail = pit_policy.available_date_detail(accepted, filed_day, next_session)
    row = (entity_id, symbol, "" if symbol_raw is None else str(symbol_raw),
           source_accn, form, _day(reported_period), int(qtrs), segments or "",
           coreg or "", security_title, exchange_name, _day(reported_period),
           filed_day, None if accepted is None else str(accepted),
           detail["accepted_et"], detail["available_date"],
           detail["latency_policy_version"], status, source,
           SYMBOL_SOURCE_VERSION, _now())
    with transaction(conn):
        added = insert_symbol_obs(conn, [row])
    return f"inserted:{status}" if added else "duplicate"


# --------------------------------------------------------------------------
# THE REPLAY SELECTOR
# --------------------------------------------------------------------------

def symbol_as_of(conn: sqlite3.Connection, entity_id: int, as_of: str, *,
                 require_common: bool = True,
                 allow_unknown_class: bool = True) -> Optional[sqlite3.Row]:
    """The symbol this issuer had most recently REPORTED as of `as_of`.

    THE point-in-time selector for symbols, and the only sanctioned way to read
    one. Its contract mirrors `pit_store.fact_as_of` exactly:

      * `available_date <= as_of` -- never `filed <= as_of`. `filed` is a date
        and availability is a timestamp question, and the two differ for the
        majority of filings.
      * ordered by availability, then `filed`, then accession. The accession
        tie-break is load-bearing: Apple filed a 10-K/A and a 10-Q on the same
        day in 2010, and an unstable ORDER BY makes a replay irreproducible.
      * the DERIVED interval table is not consulted, so no `valid_to` computed
        from a later filing can reach a replay row.

    Returns None where nothing had been reported yet -- which is a real answer
    for roughly half the cross-section before 2019, not a failure.

    SHARE CLASSES. A single filing routinely reports many symbols. Berkshire's
    2023 10-K (accession 0000950170-23-004451) reports THIRTEEN: BRK.A, BRK.B
    and eleven listed notes tagged BRK23, BRK24 ... BRK59. Returning "BRK41" as
    Berkshire's ticker would be a defensible-looking, completely wrong answer.
    So within the latest available accession:

      * rows whose `Security12bTitle` says common stock win outright;
      * failing that, and only if `allow_unknown_class`, rows with NO title --
        the pre-2019 case, where the tag did not exist -- are eligible;
      * a tie among eligible rows returns the alphabetically first and the
        caller is expected to treat multi-class issuers via `symbols_as_of`.

    `require_common=False` disables the class filter entirely, for a caller that
    genuinely wants every reported line.

    Rows whose `status` is not `ok` -- CIK placeholders and non-ticker-shaped
    preferred series -- are never returned. Old GM's `CK0000040730` is stored,
    counted and excluded.
    """
    rows = symbols_as_of(conn, entity_id, as_of,
                         require_common=require_common,
                         allow_unknown_class=allow_unknown_class)
    return rows[0] if rows else None


def symbols_as_of(conn: sqlite3.Connection, entity_id: int, as_of: str, *,
                  require_common: bool = True,
                  allow_unknown_class: bool = True) -> list[sqlite3.Row]:
    """Every symbol from the latest accession available on `as_of`.

    The multi-class answer behind `symbol_as_of`. Returns [] when nothing had
    been reported. Rows come from one accession only -- mixing classes across
    filings would pair a current Class A with a retired Class B.
    """
    day = str(as_of)[:10]
    latest = conn.execute(
        """SELECT source_accn, available_date, filed FROM pit_symbol_obs
            WHERE entity_id = ? AND status = ? AND available_date <= ?
            ORDER BY available_date DESC, filed DESC, source_accn DESC
            LIMIT 1""",
        (entity_id, OBS_OK, day),
    ).fetchone()
    if latest is None:
        return []
    rows = conn.execute(
        """SELECT * FROM pit_symbol_obs
            WHERE entity_id = ? AND source_accn = ? AND status = ?
              AND available_date <= ?
            ORDER BY symbol""",
        (entity_id, latest["source_accn"], OBS_OK, day),
    ).fetchall()
    if not require_common:
        return rows
    common = [r for r in rows if _is_common_stock(r["security_title"]) is True]
    if common:
        return common
    if allow_unknown_class:
        unknown = [r for r in rows if _is_common_stock(r["security_title"]) is None]
        if unknown:
            return unknown
    return []


def symbol_observations(conn: sqlite3.Connection, entity_id: int, *,
                        as_of: Optional[str] = None,
                        include_rejected: bool = False) -> list[sqlite3.Row]:
    """Every observation for an entity, oldest first. The audit view.

    With `as_of`, bounded by availability exactly as the selector is, so an
    audit shows the same evidence the replay had. `include_rejected` brings back
    placeholder and non-ticker rows, which is how a coverage report counts them.
    """
    clauses = ["entity_id = ?"]
    params: list[Any] = [entity_id]
    if not include_rejected:
        clauses.append("status = ?")
        params.append(OBS_OK)
    if as_of:
        clauses.append("available_date <= ?")
        params.append(str(as_of)[:10])
    return conn.execute(
        "SELECT * FROM pit_symbol_obs WHERE " + " AND ".join(clauses)
        + " ORDER BY available_date, filed, source_accn, symbol",
        params,
    ).fetchall()


# --------------------------------------------------------------------------
# The derived interval table
# --------------------------------------------------------------------------

def build_symbol_intervals(conn: sqlite3.Connection, *,
                           entity_id: Optional[int] = None,
                           derivation: str = INTERVAL_DERIVATION) -> dict[str, Any]:
    """Fold observations into labelled intervals. RUN ONLY AFTER LOADING.

    Every `valid_to` written here is future information by construction: an
    interval closes because a LATER filing reported a different symbol, and that
    later filing is the only evidence the earlier one ever ended. Constructing
    such a row while ingesting -- or reading one during a replay -- is the leak
    this whole module is shaped to avoid. Hence:

      * the rows are stamped `is_derived = 1`, pinned by a CHECK;
      * `derived_through` records the latest availability in the evidence, so a
        reader can see how far into the future the conclusion reaches;
      * `symbol_as_of` does not consult this table at all.

    It exists for reporting, for a human looking at one issuer's ticker history,
    and for a future price-alignment job that legitimately works after the fact.

    Rebuilding is destructive for the chosen derivation and that is correct: an
    interval is a conclusion, and a conclusion from more evidence replaces the
    one from less. The raw observations underneath are immutable regardless.

    Returns a status record: entities, intervals, derived_through, status.
    """
    where, params = "status = ?", [OBS_OK]
    if entity_id is not None:
        where += " AND entity_id = ?"
        params.append(entity_id)
    rows = conn.execute(
        f"""SELECT entity_id, symbol, source_accn, available_date
              FROM pit_symbol_obs WHERE {where}
             ORDER BY entity_id, symbol, available_date, filed, source_accn""",
        params,
    ).fetchall()
    if not rows:
        return {"status": "no_observations", "entities": 0, "intervals": 0,
                "derived_through": None, "derivation": derivation}

    derived_through = max(r["available_date"] for r in rows)
    now = _now()

    # Per entity, the ordered set of availability dates on which ANY symbol was
    # reported. An interval for symbol S ends at the last date S was reported,
    # UNLESS the entity kept filing afterwards -- in which case S stopped.
    seen: dict[int, list[str]] = {}
    for r in rows:
        seen.setdefault(r["entity_id"], []).append(r["available_date"])
    timeline = {eid: sorted(set(days)) for eid, days in seen.items()}

    grouped: dict[tuple[int, str], list[sqlite3.Row]] = {}
    for r in rows:
        grouped.setdefault((r["entity_id"], r["symbol"]), []).append(r)

    out: list[tuple] = []
    for (eid, symbol), obs in grouped.items():
        first, last = obs[0], obs[-1]
        days = timeline[eid]
        later = [d for d in days if d > last["available_date"]]
        valid_to = later[0] if later else None
        out.append((eid, symbol, first["available_date"], valid_to,
                    first["source_accn"], last["source_accn"], len(obs),
                    1, derivation, derived_through, now))

    with transaction(conn):
        if entity_id is None:
            conn.execute("DELETE FROM pit_symbol_interval WHERE derivation = ?",
                         (derivation,))
        else:
            conn.execute(
                "DELETE FROM pit_symbol_interval WHERE derivation = ? AND entity_id = ?",
                (derivation, entity_id))
        conn.executemany(
            """INSERT INTO pit_symbol_interval
                   (entity_id, symbol, valid_from, valid_to, first_obs_accn,
                    last_obs_accn, n_obs, is_derived, derivation,
                    derived_through, derived_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            out)
    return {"status": "built", "entities": len(timeline), "intervals": len(out),
            "derived_through": derived_through, "derivation": derivation}


def symbol_intervals(conn: sqlite3.Connection, entity_id: int,
                     derivation: str = INTERVAL_DERIVATION) -> list[sqlite3.Row]:
    """Derived intervals for one entity. NOT a replay source -- see the builder."""
    return conn.execute(
        """SELECT * FROM pit_symbol_interval
            WHERE entity_id = ? AND derivation = ?
            ORDER BY valid_from, symbol""",
        (entity_id, derivation),
    ).fetchall()


# --------------------------------------------------------------------------
# Listing status
# --------------------------------------------------------------------------

def set_listing_status(conn: sqlite3.Connection, entity_id: int, status: str, *,
                       reason: Optional[str] = None,
                       resolved_symbol: Optional[str] = None,
                       listing_id: Optional[int] = None,
                       evidence: Any = None) -> str:
    """Record whether this entity's tradeable line could be identified.

    Upsertable, unlike everything else this module writes, and deliberately so:
    a status is a CONCLUSION about identity, not a published fact. An entity
    that is unresolved today because no price series could be gated becomes
    resolved when a dated attribution arrives, and that is an improvement in
    knowledge rather than a restatement of the record.

    Returns the status string that is now stored.
    """
    if status not in (LISTING_RESOLVED, LISTING_UNRESOLVED):
        raise ValueError(f"unknown listing status: {status!r}")
    with transaction(conn):
        conn.execute(
            """INSERT INTO pit_listing_status
                   (entity_id, status, reason, resolved_symbol, listing_id,
                    evidence_json, source_version, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(entity_id) DO UPDATE SET
                    status = excluded.status,
                    reason = excluded.reason,
                    resolved_symbol = excluded.resolved_symbol,
                    listing_id = excluded.listing_id,
                    evidence_json = excluded.evidence_json,
                    source_version = excluded.source_version,
                    updated_at = excluded.updated_at""",
            (entity_id, status, reason, resolved_symbol, listing_id,
             pit_store.dumps(evidence), SYMBOL_SOURCE_VERSION, _now()),
        )
    return status


def listing_status(conn: sqlite3.Connection, entity_id: int) -> Optional[sqlite3.Row]:
    """This entity's recorded listing status, or None where none was recorded.

    None is NOT the same as LISTING_UNRESOLVED. An unexamined entity has no
    status; an examined one that could not be proved has a status and a reason.
    `scored_universe_as_of` excludes only the latter, because silently excluding
    everything unexamined would quietly empty the universe.
    """
    return conn.execute(
        "SELECT * FROM pit_listing_status WHERE entity_id = ?", (entity_id,)
    ).fetchone()


def unresolved_entities(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every entity marked LISTING_UNRESOLVED, with its reason. A coverage report."""
    return conn.execute(
        """SELECT s.*, e.cik FROM pit_listing_status s
             JOIN pit_entity e ON e.entity_id = s.entity_id
            WHERE s.status = ?
            ORDER BY e.cik""",
        (LISTING_UNRESOLVED,),
    ).fetchall()


# --------------------------------------------------------------------------
# The two universes, with identity status applied
# --------------------------------------------------------------------------

def peer_universe_as_of(conn: sqlite3.Connection, as_of: str,
                        **kwargs: Any) -> list[sqlite3.Row]:
    """`pit_identity.peer_universe_as_of`, UNCHANGED and re-exported on purpose.

    Wrapped rather than left to the caller so that the asymmetry is visible in
    one file: an entity whose listing cannot be resolved is NOT filtered here.
    It was a live reporting company, its fundamentals are real, and its
    percentile contribution is exactly what a survivorship-free cohort needs.
    Dropping it from the peer set would reintroduce the bias through the one
    door the store does not otherwise watch.
    """
    return pit_identity.peer_universe_as_of(conn, as_of, **kwargs)


def scored_universe_as_of(conn: sqlite3.Connection, as_of: str,
                          **kwargs: Any) -> list[sqlite3.Row]:
    """`pit_identity.scored_universe_as_of` minus every LISTING_UNRESOLVED entity.

    This is where identity status bites. A position cannot be taken in a line
    that cannot be identified, and a forward label computed from the wrong price
    series is worse than no label -- it is a wrong number with full provenance.

    Only an entity explicitly marked LISTING_UNRESOLVED is removed. An entity
    with no status row has not been examined and is left to the upstream gates,
    because treating "not yet checked" as "not investable" would empty the
    universe the first time this table is added to a store.
    """
    rows = pit_identity.scored_universe_as_of(conn, as_of, **kwargs)
    blocked = {r["entity_id"] for r in conn.execute(
        "SELECT entity_id FROM pit_listing_status WHERE status = ?",
        (LISTING_UNRESOLVED,))}
    return [r for r in rows if r["entity_id"] not in blocked]


# --------------------------------------------------------------------------
# Wiring: a filed symbol is a PROOF STRING, never a bypass
# --------------------------------------------------------------------------

def resolve_listing_with_filed_symbol(
        conn: sqlite3.Connection, entity_id: int, as_of: str, *,
        cache_dir: Optional[str] = None,
        persist: bool = True,
        record_status: bool = True,
        resolver: Any = None) -> dict:
    """Gate the filed symbol through `pit_identity.resolve_listing`. Both, always.

    THE POINT OF THIS FUNCTION IS THAT IT DOES NOT SHORTCUT. A filed
    `dei:TradingSymbol` proves the issuer REPORTED a symbol on a date. It does
    not prove that a Yahoo series under that symbol is the same instrument, and
    three of the pilot's own cases say so out loud:

      * BERKSHIRE filed `BRKA` on every 10-Q from 2011 to 2019 and `BRK.A` from
        2022. Yahoo's symbol is `BRK-A`. The filed string is not a price key.
      * BBBY's symbol is genuinely reassigned. Yahoo answers HTTP 200 for BBBY
        today with a matching name and exchange, and only `firstTradeDate`
        separates it from the retailer that died in 2023.
      * SHLD is an ETF now. The filed 2015 `SHLD` and today's `SHLD` are
        different instruments sharing four letters.

    So the filed symbol is passed to `resolve_listing` as `proof` -- which is
    what raises a passing gate's confidence from `inferred` to `proved` -- and
    the `firstTradeDate` gate still decides. A refused gate writes no listing
    and, with `record_status`, marks the entity LISTING_UNRESOLVED.

    Returns `resolve_listing`'s record with three extra keys: `filed_symbol`,
    `filed_symbol_accn`, and `listing_status`.
    """
    obs = symbol_as_of(conn, entity_id, as_of)
    if obs is None:
        if record_status:
            set_listing_status(conn, entity_id, LISTING_UNRESOLVED,
                               reason=UNRESOLVED_NO_OBSERVATION,
                               evidence={"as_of": str(as_of)[:10]})
        return {"accepted": False, "entity_id": entity_id, "as_of": str(as_of)[:10],
                "symbol": None, "filed_symbol": None, "filed_symbol_accn": None,
                "reason": UNRESOLVED_NO_OBSERVATION, "listing_id": None,
                "confidence": pit_identity.CONFIDENCE_UNVERIFIED,
                "listing_status": LISTING_UNRESOLVED}

    proof = json.dumps({"tag": "dei:TradingSymbol", "accn": obs["source_accn"],
                        "filed": obs["filed"],
                        "available_date": obs["available_date"],
                        "source": obs["source"],
                        "source_version": obs["source_version"]},
                       sort_keys=True)
    call = resolver or pit_identity.resolve_listing
    result = dict(call(conn, obs["symbol"], entity_id, as_of,
                       cache_dir=cache_dir, proof=proof, persist=persist))
    result["filed_symbol"] = obs["symbol"]
    result["filed_symbol_accn"] = obs["source_accn"]

    if result.get("accepted"):
        status, reason = LISTING_RESOLVED, None
    else:
        status, reason = LISTING_UNRESOLVED, UNRESOLVED_PRICE_GATE_FAILED
    if record_status:
        set_listing_status(
            conn, entity_id, status, reason=reason,
            resolved_symbol=obs["symbol"] if status == LISTING_RESOLVED else None,
            listing_id=result.get("listing_id"),
            evidence={"as_of": str(as_of)[:10], "filed_symbol": obs["symbol"],
                      "accn": obs["source_accn"],
                      "gate_reason": result.get("reason"),
                      "first_trade_date": result.get("first_trade_date")})
    result["listing_status"] = status
    return result
