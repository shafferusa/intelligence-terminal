"""Raw-price reconstruction and the market-cap bridge for the PIT replay.

`pit_price_bar` stores what Yahoo publishes: `close`, which is split-adjusted,
and `adjclose`, which is split AND dividend adjusted. **Neither is admissible in
a market capitalisation**, and the reason is not a rounding argument:

    A point-in-time share count is expressed in the share basis of its own era.

Apple's `CommonStockSharesOutstanding` for the instant 2019-09-28 reads
4,443,236,000 in four consecutive filings and 17,772,945,000 in the 10-K filed
2020-10-30, because the 4:1 split of 2020-08-31 restated it. A point-in-time
selector at a 2020-01-31 as-of correctly returns the pre-split number. Yahoo's
`close` for that same session reads 77.3775 today -- also correct, and also
restated by that same split, in the OPPOSITE direction. Multiplying the two
produces $339bn for a company that was worth $1.38tn, and every number in that
calculation has clean provenance. That is the failure this module exists to
prevent, and it is invisible at the call site.

So this module does two things and refuses to do a third.

1. `raw_close_as_of` reconstructs the AS-TRADED price by un-applying every
   price-affecting corporate action recorded AFTER the bar date and at or before
   the date the bars were pulled. It returns the price WITH a declared
   `price_basis` and the list of actions it un-applied, so a caller can audit
   the arithmetic rather than trust it. Verified: AAPL 2015-06-30 reconstructs
   to 125.43, never 31.3575.

2. `market_cap_as_of` pairs that raw price with a point-in-time share count and
   returns None WITH A REASON whenever either side is missing or unsafe.

3. It never guesses. A share count that might be one class of a multi-class
   issuer is UNAVAILABLE, because a single class taken for the whole company is
   a silent 2x error that looks exactly like a cheap stock.

WHY THE BOUND ON THE ACTION WINDOW. Yahoo's `close` is adjusted for every split
KNOWN AT PULL TIME, including splits that post-date the window requested --
narrowing the request does not recover the as-traded level. So the actions to
un-apply are those with `bar_date < event_date <= pull_date`, where the pull
date comes from `pit_ingest_run`. An action recorded later than the pull cannot
already be in the stored close, and un-applying it would manufacture an error of
exactly the split ratio in the other direction.

WHY SPIN-OFFS ARE NOT SPLITS. AT&T carries `splitRatio` '1324:1000' on
2022-04-11 in the same array as GE's genuine 1:8 reverse split. It is a valid
PRICE adjustment and wrong by 32.4% as a share-count divisor, which is why
`pit_corporate_action` carries `applies_to_price` and `applies_to_shares` as
separate flags. This module reads the price flag; `pit_shares.split_factor_between`
reads the share flag. Neither is allowed to answer for the other.

WHAT IT REUSES AND NEVER DUPLICATES:

    pit_shares      the share ladder, its concepts, its staleness concept, its
                    price-basis vocabulary, its reason strings, and -- wherever
                    `pit_share_obs` can answer -- its `market_cap_as_of_detail`
                    itself. The Shaffer production core is frozen and so is the
                    share module's policy; this file adds the price half.
    pit_policy      `is_stale`, `max_age_days`, the latency policy version.
    pit_store       `latest_period_as_of`, the only sanctioned fact selector.
    pit_symbols     `symbols_as_of`, which is the share-class evidence.

INVARIANT: this module is READ-ONLY. It opens no network connection, creates no
table and writes no row; the disk helpers exist so a CALLER that does write can
check first. The store was filled to 95% once already by an ingest whose WAL
reached 9.3 GB because a concurrent reader silently blocked checkpointing, and
`wal_checkpoint` here reads the pragma's RETURN VALUE, which is the only thing
that makes that failure visible.

MEASURED, 2026-09-21, against the 14,072,934-row archive (see `main()`):

    share-shaped tags present at all
      us-gaap:CommonStockSharesOutstanding                 589,989 rows / 12,689 entities
      us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding
                                                           696,357 rows / 10,335 entities
      dei:EntityCommonStockSharesOutstanding                   606 rows /    150 entities
      dei:EntityPublicFloat                                    130 rows /     78 entities
      us-gaap:CommonStockSharesIssued                            0 rows
      us-gaap:CommonStockSharesAuthorized                        0 rows
      us-gaap:CommonStockValue                                   0 rows

    The designed lead rung is effectively absent and the opt-in ISSUED fallback
    does not exist in this store at all. The ladder has ONE usable rung.

    point-in-time count resolvable, of the peer universe
      2015-06-30   4,410 / 7,950   (55.47%)   dei rung answered for 11
      2019-06-28   3,912 / 7,102   (55.08%)   dei rung answered for 1
      2024-06-28   3,795 / 7,311   (51.91%)   dei rung answered for 2

    why the rest have none, at 2024-06-28: 1,732 stale, 973 never filed a
    share-shaped fact, 711 filed ONLY the weighted-average diluted count --
    which is a period average and is never substituted for a count -- and 100
    had not filed yet.

    count AND a resolvable symbol, which is the ceiling on a market cap
      2015-06-30   2,054 / 7,950   (25.84%)
      2019-06-28   2,559 / 7,102   (36.03%)
      2024-06-28   3,163 / 7,311   (43.26%)

    DEFENSIBLE after the share-class guard below
      2015-06-30   2,015 (25.35%)   2019-06-28   2,500 (35.20%)
      2024-06-28   3,023 (41.35%)

    So: a market cap is NOT computable at scale today. It is computable for two
    issuers in five at 2024 and one in four at 2015, and the binding constraint
    is the share count before 2019 and the symbol after it. `targeted_fetch_plan`
    prices the fix: 3,974 entities x 2 concepts = 7,948 SEC companyconcept
    requests, 13.2 minutes at the 10 req/s ceiling, ~0.175 GiB downloaded to a
    temp cache. Re-run `python pit_rawprice.py` to re-measure; the store is
    written concurrently by other agents, so counts drift by a few rows.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import sqlite3
import sys
from typing import Any, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_policy
import pit_shares
import pit_store
import pit_symbols

__all__ = [
    "RECONSTRUCTION_VERSION", "CLASS_POLICY_VERSION",
    "PRICE_BASIS_RAW", "PRICE_BASIS_SPLIT_ADJUSTED", "PRICE_BASIS_TOTAL_RETURN",
    "SOURCE_COLUMN",
    "EVENT_SPLIT", "EVENT_REVERSE_SPLIT", "EVENT_SPINOFF", "EVENT_STOCK_DIVIDEND",
    "EVENT_CASH_DIVIDEND", "RATIO_EVENT_TYPES", "CASH_EVENT_TYPES",
    "BOUND_INGEST_RUN", "BOUND_LAST_BAR", "BOUND_CALLER", "BOUND_UNBOUNDED",
    "CLASS_SINGLE", "CLASS_MULTI", "CLASS_UNKNOWN",
    "RESOLUTION_SINGLE", "RESOLUTION_CORROBORATED", "RESOLUTION_UNCORROBORATED",
    "RESOLUTION_AMBIGUOUS", "RESOLUTION_IMPLAUSIBLE",
    "CLASS_POLICY_STRICT", "CLASS_POLICY_PERMISSIVE",
    "SHARES_SOURCE_AUTO", "SHARES_SOURCE_SHARE_OBS", "SHARES_SOURCE_ARCHIVE",
    "CORROBORATION_BAND", "SINGLE_CLASS_FLOOR", "IMPLAUSIBLE_BAND",
    "disk_guard", "require_disk", "wal_checkpoint",
    "ingest_bound", "price_actions_between", "unapply_factor",
    "raw_close_as_of", "raw_close",
    "archive_shares_as_of", "shares_as_of_any",
    "weighted_average_crosscheck", "share_class_audit", "class_decision",
    "market_cap_as_of_detail", "market_cap_as_of",
    "market_cap_coverage", "targeted_fetch_plan", "reconstruction_spec",
]

# --------------------------------------------------------------------------
# Versions. Never edited in place -- a change means a v2 constant beside v1.
# --------------------------------------------------------------------------

#: How a raw level was rebuilt from a stored bar. Stamped on every record so a
#: price can be re-derived later by the same rule that produced it.
RECONSTRUCTION_VERSION = "raw_price_v1_unapply_price_actions"

#: How the multi-class refusal was decided. Separate from the reconstruction
#: version because the two change for unrelated reasons.
CLASS_POLICY_VERSION = "share_class_guard_v1"

#: Re-exported, never redefined: `pit_shares` owns the price-basis vocabulary
#: and its `ADMISSIBLE_PRICE_BASES` is the gate a market cap has to pass.
PRICE_BASIS_RAW = pit_shares.PRICE_BASIS_RAW
PRICE_BASIS_SPLIT_ADJUSTED = pit_shares.PRICE_BASIS_SPLIT_ADJUSTED
PRICE_BASIS_TOTAL_RETURN = pit_shares.PRICE_BASIS_TOTAL_RETURN

#: The column reconstruction starts from. `adjclose` is split AND dividend
#: adjusted; un-applying splits from it would leave every dividend since baked
#: into the "raw" level. `adjclose` is correct for RETURN labels and is verified
#: window-invariant to 2.5e-7 -- it is simply not a price level.
SOURCE_COLUMN = "close"

# --------------------------------------------------------------------------
# Corporate-action vocabulary. Defined here because `pit_corporate_action` has
# no loader yet and the loader must speak the same words the reader does.
# --------------------------------------------------------------------------

EVENT_SPLIT = "split"
EVENT_REVERSE_SPLIT = "reverse_split"
EVENT_SPINOFF = "spinoff"
EVENT_STOCK_DIVIDEND = "stock_dividend"
EVENT_CASH_DIVIDEND = "cash_dividend"
EVENT_MERGER = "merger"

#: Events that move a price by a RATIO and are therefore baked into a stored
#: split-adjusted close. A spin-off is here because Yahoo delivers it as a
#: split and it really does move the price; `applies_to_shares` is what stops it
#: moving the share count.
RATIO_EVENT_TYPES = frozenset({
    EVENT_SPLIT, EVENT_REVERSE_SPLIT, EVENT_SPINOFF, EVENT_STOCK_DIVIDEND,
})

#: Events that move a price by a CASH AMOUNT. They are never un-applied from
#: `close`, which is not dividend-adjusted in the first place. Un-applying one
#: would be subtracting an adjustment that was never made.
CASH_EVENT_TYPES = frozenset({EVENT_CASH_DIVIDEND, "dividend", "special_dividend"})

# --------------------------------------------------------------------------
# Statuses and reasons. Each names a DIFFERENT fact about the world; none of
# them may be collapsed into a bare False.
# --------------------------------------------------------------------------

REASON_NO_BARS_FOR_LISTING = "no_bars_for_listing"
REASON_NO_BAR = "no_bar_on_date"
REASON_NO_CLOSE = "bar_close_missing"
REASON_NON_POSITIVE_CLOSE = "bar_close_not_positive"
REASON_ZERO_VOLUME_PRINT = "zero_volume_print"
REASON_RATIO_UNUSABLE = "corporate_action_ratio_unusable"
REASON_UNKNOWN_EVENT_TYPE = "unknown_price_event_type"
REASON_NO_LISTING = "no_listing_supplied"
REASON_LISTING_MISMATCH = "listing_belongs_to_another_entity"
REASON_NO_PRICE = pit_shares.REASON_NO_PRICE
REASON_ZERO_SHARE_COUNT = pit_shares.REASON_ZERO_SHARE_COUNT
REASON_NEVER_FILED = pit_shares.REASON_NEVER_FILED
REASON_NOT_YET_FILED = pit_shares.REASON_NOT_YET_FILED
REASON_STALE = pit_shares.REASON_STALE
REASON_ONLY_PERIOD_AVERAGE = pit_shares.REASON_ONLY_PERIOD_AVERAGE
REASON_ONLY_PUBLIC_FLOAT = pit_shares.REASON_ONLY_PUBLIC_FLOAT
REASON_CLASS_AMBIGUOUS = "share_class_ambiguous"
REASON_CLASS_UNCORROBORATED = "share_class_uncorroborated"
REASON_COUNT_IMPLAUSIBLE = "share_count_implausible_vs_weighted_average"

#: How the end of the un-apply window was established.
BOUND_INGEST_RUN = "ingest_run_timestamp"
BOUND_LAST_BAR = "last_bar_in_pull"
BOUND_CALLER = "caller_supplied"
BOUND_UNBOUNDED = "unbounded_no_ingest_evidence"

#: What the symbol evidence says about share classes.
CLASS_SINGLE = "single_class_no_evidence_of_more"
CLASS_MULTI = "multi_class"
CLASS_UNKNOWN = "unknown_no_symbol_evidence"

#: What the guard concluded.
RESOLUTION_SINGLE = "single_class"
RESOLUTION_CORROBORATED = "multi_class_corroborated"
RESOLUTION_UNCORROBORATED = "multi_class_uncorroborated"
RESOLUTION_AMBIGUOUS = "multi_class_ambiguous"
RESOLUTION_IMPLAUSIBLE = "count_implausible"

CLASS_POLICY_STRICT = "strict"
CLASS_POLICY_PERMISSIVE = "permissive"

#: Where a share count came from. `pit_share_obs` is the designed home and
#: `pit_fact` is where 589,989 of the 590,725 point-in-time counts actually sit
#: today, so both are addressable and every record says which answered.
SHARES_SOURCE_AUTO = "auto"
SHARES_SOURCE_SHARE_OBS = "pit_share_obs"
SHARES_SOURCE_ARCHIVE = "pit_fact"

# --------------------------------------------------------------------------
# The share-class guard's thresholds, and the measurements behind them.
# --------------------------------------------------------------------------

#: Basic shares outstanding divided by the trailing weighted-average DILUTED
#: count. Measured across the peer cross-section on 2026-09-21 (n=2,270 at
#: 2015-06-30 and n=3,110 at 2024-06-28): median 1.000 at both dates,
#: p10 0.956/0.965, p25 0.985/0.991, p75 1.009/1.009, p90 1.069/1.097,
#: p95 1.243/1.288. A band of [0.85, 1.25] therefore contains roughly nine
#: issuers in ten whose single filed count really is the whole company. It is
#: the evidence that a multi-class issuer's undimensioned count covers every
#: class -- Alphabet files 12,381,000,000 at 2024-03-31, which is A+B+C -- and
#: it is the only such evidence this store holds.
CORROBORATION_BAND = (0.85, 1.25)

#: Below this, a count is treated as probably ONE CLASS of several even when no
#: second symbol was visible. 2.1% of the cross-section sits here at both dates.
#: The known limit, recorded rather than patched: a class carrying more than 70%
#: of an issuer's shares is not caught by this ratio, which is why the symbol
#: evidence is consulted first and the ratio only ever confirms it.
SINGLE_CLASS_FLOOR = 0.70

#: Outside this, the two numbers are not the same quantity at all. The measured
#: p99 of the ratio is ~995 at both dates: those filers tag the weighted average
#: in THOUSANDS and the outstanding count in units, under the same 'shares'
#: unit string. A 1,000x market cap is not a share-class problem, and it gets
#: its own refusal so it can be counted separately.
IMPLAUSIBLE_BAND = (0.10, 10.0)

#: How stale the weighted-average cross-check may be. One annual cadence plus a
#: filing lag -- the same 400 days the peer universe uses for "still reporting".
CROSSCHECK_MAX_AGE_DAYS = 400

#: The tag the cross-check reads. A DURATION average, never a count: it is used
#: here only as a scale reference and never as a substitute.
CROSSCHECK_TAG = "WeightedAverageNumberOfDilutedSharesOutstanding"

#: Free space a bulk writer must see before it starts. The store is 6.3 GiB on a
#: volume with ~13 GiB free at 95% used; an ingest that doubles a table has
#: nowhere to go, and a WAL that cannot checkpoint grows without bound.
MIN_FREE_GIB = 2.0

_CLASS_LETTER = re.compile(r"\bclass\s*([a-z0-9])\b", re.IGNORECASE)

#: A `Security12bTitle` that names an EQUITY line rather than a note.
#: Deliberately wider than `pit_identity.is_common_stock_class`, which is a
#: DELISTING test and answers False for Alphabet's GOOG -- titled "Class C
#: Capital Stock", not common stock. That is the right answer for a Form 25 and
#: the wrong one here: for the share-class question GOOG is exactly the second
#: class that must be seen. Berkshire's BRK23 ("0.750% Senior Notes due 2023")
#: and Ford's FPRB ("6.200% Notes due June 1, 2059") match nothing here.
#: Known limit, recorded rather than patched: "Common Stock Purchase Warrants"
#: matches, which over-detects a second class and therefore only ever refuses.
_EQUITY_TITLE = re.compile(
    r"\b(common|capital|ordinary)\s+(stock|shares)\b|\bclass\s*[a-z0-9]\b",
    re.IGNORECASE)


# --------------------------------------------------------------------------
# Small helpers, local by design: a shared `_day` that drifts between modules
# is worse than four identical ones that cannot.
# --------------------------------------------------------------------------

def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _day(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) < 10:
        return None
    try:
        _dt.date.fromisoformat(text[:10])
    except ValueError:
        return None
    return text[:10]


def _days_between(start: Any, end: Any) -> int:
    """Calendar days from `start` to `end`; a huge number if either is unusable."""
    a, b = _day(start), _day(end)
    if a is None or b is None:
        return 10 ** 6
    return (_dt.date.fromisoformat(b) - _dt.date.fromisoformat(a)).days


def _shift_days(day: str, delta: int) -> str:
    return (_dt.date.fromisoformat(day) + _dt.timedelta(days=delta)).isoformat()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def _symbols_as_of(conn: sqlite3.Connection, entity_id: int, as_of: str,
                   **options: Any) -> list[Any]:
    """`pit_symbols.symbols_as_of`, tolerant of a store without that table.

    An absent table is a store that has not loaded symbols yet, which is a
    different thing from an issuer that never reported one -- and neither is an
    error worth raising in the middle of a coverage sweep.
    """
    if not _table_exists(conn, "pit_symbol_obs"):
        return []
    return list(pit_symbols.symbols_as_of(conn, entity_id, as_of, **options))


# ==========================================================================
# DISK. The binding constraint, and the one that failed silently before.
# ==========================================================================

def disk_guard(path: Optional[str] = None,
               need_gib: float = MIN_FREE_GIB) -> dict[str, Any]:
    """Measure free space and say whether a bulk write may proceed.

    Returns a record, never raises -- a caller that wants the exception calls
    `require_disk`. `ok` is False when free space is below `need_gib`.

    This exists because the failure it guards was not a crash. An earlier ingest
    filled a 238 GiB volume to 95% and the visible symptom was a slow loader;
    the actual cause was a 9.3 GB write-ahead log that could not be checkpointed
    while a reader held the database open. Free space is the number that makes
    that state legible from outside.
    """
    target = os.path.abspath(path or pit_store.DEFAULT_PIT_DB_PATH)
    anchor = target if os.path.isdir(target) else os.path.dirname(target)
    usage = shutil.disk_usage(anchor or ".")
    free_gib = usage.free / (1024 ** 3)
    record = {
        "path": target,
        "total_gib": round(usage.total / (1024 ** 3), 3),
        "used_gib": round(usage.used / (1024 ** 3), 3),
        "free_gib": round(free_gib, 3),
        "pct_used": round(100.0 * usage.used / usage.total, 2) if usage.total else None,
        "need_gib": float(need_gib),
        "ok": free_gib >= float(need_gib),
        "measured_at": _now(),
    }
    for suffix, key in ((".db-wal", "wal_bytes"), (".db-shm", "shm_bytes")):
        sidecar = target[:-3] + suffix if target.endswith(".db") else target + suffix[3:]
        record[key] = os.path.getsize(sidecar) if os.path.exists(sidecar) else 0
    record["db_bytes"] = os.path.getsize(target) if os.path.isfile(target) else 0
    return record


def require_disk(path: Optional[str] = None,
                 need_gib: float = MIN_FREE_GIB) -> dict[str, Any]:
    """`disk_guard`, but ABORTS the caller when the volume is too full.

    The abort is the point. A bulk write that starts on a full volume does not
    fail cleanly: it fails partway through a transaction, leaves a WAL that
    cannot be truncated, and takes the rest of the free space with it.
    """
    record = disk_guard(path, need_gib)
    if not record["ok"]:
        raise RuntimeError(
            f"ABORT: {record['free_gib']:.2f} GiB free on {record['path']} "
            f"({record['pct_used']}% used); {need_gib:.2f} GiB required before a "
            "bulk write. Free space or write elsewhere -- do not start.")
    return record


def wal_checkpoint(conn: sqlite3.Connection, mode: str = "TRUNCATE") -> dict[str, Any]:
    """Checkpoint the write-ahead log AND READ THE RESULT.

    `PRAGMA wal_checkpoint(TRUNCATE)` returns one row, `(busy, log, checkpointed)`.
    A `busy` of 1 means the checkpoint DID NOTHING -- another connection held a
    read lock -- and the log keeps growing. Issuing the pragma and discarding
    its return value is exactly how a 9.3 GB WAL stayed invisible until the
    volume was full, so this helper returns `busy` as a first-class field and
    names the status in words.

    Other agents may be reading this store concurrently, so `busy` is a normal
    outcome, not an error. The caller's job is to see it and retry later.
    """
    if mode.upper() not in {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}:
        raise ValueError(f"unknown checkpoint mode {mode!r}")
    try:
        row = conn.execute(f"PRAGMA wal_checkpoint({mode.upper()})").fetchone()
    except sqlite3.Error as exc:
        return {"mode": mode.upper(), "busy": None, "status": "error", "error": str(exc)}
    busy, log_pages, checkpointed = (int(row[0]), int(row[1]), int(row[2]))
    return {
        "mode": mode.upper(),
        "busy": busy,
        "log_pages": log_pages,
        "checkpointed_pages": checkpointed,
        "status": "blocked_by_reader" if busy else "checkpointed",
        "note": ("busy=1 means the log was NOT truncated and is still growing"
                 if busy else "the log was checkpointed"),
    }


# ==========================================================================
# THE UN-APPLY WINDOW
# ==========================================================================

def ingest_bound(conn: sqlite3.Connection, ingest_id: Optional[int],
                 listing_id: Optional[int] = None) -> dict[str, Any]:
    """The date the bars were PULLED -- the upper edge of the un-apply window.

    A stored `close` carries every split the provider knew about when the pull
    happened, and nothing after. Narrowing the request does not change that:
    AAPL's 2015-06-30 close reads 31.3575 even in a window that ends in 2019.
    So the actions to un-apply end at the pull date, and an action recorded
    afterwards must be left alone.

    Three answers, in descending order of evidence, and each one named:

      ingest_run_timestamp  `pit_ingest_run` recorded when the pull ran. Exact.
      last_bar_in_pull      no ingest row; the newest bar in that pull is used
                            instead. WEAKER, and flagged: a split between the
                            last bar and the real pull date is already in the
                            close and would not be un-applied.
      unbounded_no_ingest_evidence
                            neither is available, so today stands in and every
                            recorded action is un-applied. Flagged loudest.
    """
    record: dict[str, Any] = {
        "ingest_id": ingest_id, "bound": None, "status": None, "warnings": []}
    if ingest_id is not None and _table_exists(conn, "pit_ingest_run"):
        row = conn.execute(
            "SELECT started_at, finished_at FROM pit_ingest_run WHERE ingest_id = ?",
            (ingest_id,),
        ).fetchone()
        if row is not None:
            stamp = _day(row["finished_at"]) or _day(row["started_at"])
            if stamp:
                record.update({"bound": stamp, "status": BOUND_INGEST_RUN})
                return record
    if listing_id is not None and ingest_id is not None:
        row = conn.execute(
            "SELECT MAX(bar_date) AS last_bar FROM pit_price_bar "
            "WHERE listing_id = ? AND ingest_id = ?",
            (listing_id, ingest_id),
        ).fetchone()
        stamp = _day(row["last_bar"]) if row else None
        if stamp:
            record.update({"bound": stamp, "status": BOUND_LAST_BAR})
            record["warnings"].append(
                "no pit_ingest_run row; the last bar in the pull stands in for the "
                "pull date. A split between that bar and the real pull is already "
                "in the stored close and will NOT be un-applied.")
            return record
    record.update({"bound": _dt.date.today().isoformat(), "status": BOUND_UNBOUNDED})
    record["warnings"].append(
        "no ingest evidence at all; every recorded price action after the bar "
        "date is un-applied, which is right for a recent pull and wrong for an "
        "old one.")
    return record


def price_actions_between(conn: sqlite3.Connection, listing_id: Optional[int],
                          after: str, through: str) -> dict[str, Any]:
    """Price-affecting actions in the window (after, through]. The price twin of
    `pit_shares.split_factor_between`, which answers the SHARE question.

    Only `applies_to_price = 1` rows are considered, and within them:

      a ratio event (split, reverse split, spin-off, stock dividend) is
        multiplied into the factor;
      a CASH event is ignored -- `close` is not dividend-adjusted, so there is
        no dividend adjustment to remove;
      anything else, or a ratio event with an unusable ratio, lands in
        `unusable` and the caller REFUSES. A silently skipped action is a price
        wrong by the whole ratio with no marker on it.

    `status` reuses `pit_shares`' vocabulary so a reader sees the same words on
    both halves of the problem, and `no_events_recorded` stays honest: it is
    ambiguous between "nothing happened" and "nothing was ingested".
    """
    record: dict[str, Any] = {
        "factor": 1.0, "status": pit_shares.SPLIT_UNCHECKED, "events": [],
        "ignored_cash_events": [], "unusable": [],
        "window": {"after": _day(after), "through": _day(through)},
    }
    if listing_id is None:
        return record
    try:
        rows = conn.execute(
            """SELECT event_date, event_type, ratio_num, ratio_den, cash_amount,
                      applies_to_shares, confidence, source
                 FROM pit_corporate_action
                WHERE listing_id = ? AND applies_to_price = 1
                  AND event_date > ? AND event_date <= ?
                ORDER BY event_date""",
            (listing_id, _day(after), _day(through)),
        ).fetchall()
        any_row = conn.execute(
            "SELECT COUNT(*) AS n FROM pit_corporate_action WHERE listing_id = ?",
            (listing_id,),
        ).fetchone()
    except sqlite3.Error as exc:
        record["error"] = str(exc)
        return record

    factor = 1.0
    for row in rows:
        kind = (row["event_type"] or "").strip().lower()
        if kind in CASH_EVENT_TYPES:
            record["ignored_cash_events"].append(
                {"event_date": row["event_date"], "event_type": kind,
                 "cash_amount": row["cash_amount"],
                 "why": "close is split-adjusted only; no dividend adjustment to remove"})
            continue
        if kind not in RATIO_EVENT_TYPES:
            record["unusable"].append(
                {"event_date": row["event_date"], "event_type": kind,
                 "reason": REASON_UNKNOWN_EVENT_TYPE})
            continue
        num, den = row["ratio_num"], row["ratio_den"]
        if not num or not den or float(num) <= 0 or float(den) <= 0:
            record["unusable"].append(
                {"event_date": row["event_date"], "event_type": kind,
                 "reason": REASON_RATIO_UNUSABLE, "ratio_num": num, "ratio_den": den})
            continue
        step = float(num) / float(den)
        factor *= step
        record["events"].append({
            "event_date": row["event_date"], "event_type": kind,
            "ratio": f"{num}:{den}", "factor": step,
            "applies_to_shares": int(row["applies_to_shares"]),
            "confidence": row["confidence"], "source": row["source"],
        })
    record["factor"] = factor
    record["status"] = (pit_shares.SPLIT_CHECKED if (any_row and any_row["n"])
                        else pit_shares.SPLIT_NO_EVENTS_RECORDED)
    return record


def unapply_factor(events: Sequence[dict[str, Any]]) -> float:
    """The cumulative multiplier that turns a split-adjusted close into a raw one.

    raw = stored_close x PROD(ratio_num / ratio_den)

    The direction is the same for both cases, which is why one expression covers
    them: a 4:1 forward split divided every earlier adjusted price by 4, so
    multiplying by 4/1 restores it; a 1:8 reverse split multiplied every earlier
    adjusted price by 8, so multiplying by 1/8 restores it. It is also the same
    multiplier `pit_shares.split_factor_between` applies to a SHARE COUNT, for
    the symmetric reason -- a count in the old basis times the factor is the new
    basis, and a price in the new basis times the factor is the old one.
    """
    factor = 1.0
    for event in events:
        factor *= float(event["factor"])
    return factor


# ==========================================================================
# RAW PRICE
# ==========================================================================

def raw_close_as_of(conn: sqlite3.Connection, listing_id: int, date: str, *,
                    ingest_id: Optional[int] = None,
                    bound_date: Optional[str] = None,
                    roll_back_days: int = 0,
                    require_traded: bool = False) -> dict[str, Any]:
    """The AS-TRADED close for one listing on one date. Always a record.

    Returns `raw_close` together with `price_basis='raw_as_printed_unadjusted'`
    and `actions_unapplied`, the list of events that were removed, so the
    arithmetic can be audited rather than believed. `available` is False with a
    named `reason` whenever the level cannot be reconstructed.

    Verification this function exists to satisfy: Apple's 2015-06-30 stored
    close is 31.3575, the 4:1 split of 2020-08-31 lies inside the window, and
    the reconstructed level is 125.43 -- the price a trader actually saw.

    `roll_back_days` defaults to 0, meaning the exact session or nothing. A
    halted or thinly traded line has no print on some month-ends, and a caller
    that wants the previous print must ask for it; `roll_days` in the record
    says how far it rolled. Nothing rolls silently.

    `require_traded` refuses a zero-volume print. Stale zero-volume prints sit
    exactly at events -- FRCB printed $3.51 at volume 0 for two sessions after
    the FDIC seizure and the next real trade was $0.3336 -- so the flag is
    always raised in `warnings` and the refusal is opt-in, because a zero-volume
    session is also a perfectly ordinary thing for a small listing.
    """
    day = _day(date)
    record: dict[str, Any] = {
        "listing_id": listing_id,
        "as_of": day,
        "available": False,
        "reason": None,
        "raw_close": None,
        "price_basis": PRICE_BASIS_RAW,
        "source_column": SOURCE_COLUMN,
        "bar_date": None,
        "roll_days": 0,
        "stored_close": None,
        "adjclose": None,
        "volume": None,
        "ingest_id": ingest_id,
        "ingest_bound": None,
        "bound_status": None,
        "factor": None,
        "actions_unapplied": [],
        "actions_status": None,
        "warnings": [],
        "reconstruction_version": RECONSTRUCTION_VERSION,
        "latency_policy_version": pit_policy.LATENCY_POLICY_VERSION,
    }
    if day is None:
        record["reason"] = REASON_NO_BAR
        record["detail"] = f"{date!r} is not a date"
        return record
    if not _table_exists(conn, "pit_price_bar"):
        record["reason"] = REASON_NO_BARS_FOR_LISTING
        record["detail"] = "pit_price_bar does not exist in this store"
        return record

    floor = _shift_days(day, -int(roll_back_days)) if roll_back_days else day
    params: list[Any] = [listing_id, day, floor]
    clause = ""
    if ingest_id is not None:
        clause = " AND b.ingest_id = ?"
        params.append(ingest_id)
    # Ordered by the PULL DATE, not by ingest_id. Ids are assigned in creation
    # order, so a backfill registered after a newer pull carries the larger id
    # while holding the older adjustment state -- and the un-apply window is
    # drawn from the pull date, so picking the wrong row picks the wrong window.
    if _table_exists(conn, "pit_ingest_run"):
        bar = conn.execute(
            "SELECT b.* FROM pit_price_bar b "
            "LEFT JOIN pit_ingest_run r ON r.ingest_id = b.ingest_id "
            "WHERE b.listing_id = ? AND b.bar_date <= ? AND b.bar_date >= ?"
            + clause +
            " ORDER BY b.bar_date DESC, "
            "COALESCE(r.finished_at, r.started_at, '') DESC, b.ingest_id DESC "
            "LIMIT 1",
            params,
        ).fetchone()
    else:
        bar = conn.execute(
            "SELECT * FROM pit_price_bar WHERE listing_id = ? AND bar_date <= ? "
            "AND bar_date >= ?" + clause.replace("b.", "") +
            " ORDER BY bar_date DESC, ingest_id DESC LIMIT 1",
            params,
        ).fetchone()
    if bar is None:
        has_any = conn.execute(
            "SELECT 1 FROM pit_price_bar WHERE listing_id = ? LIMIT 1", (listing_id,)
        ).fetchone()
        record["reason"] = REASON_NO_BAR if has_any else REASON_NO_BARS_FOR_LISTING
        return record

    record.update({
        "bar_date": bar["bar_date"],
        "roll_days": _days_between(bar["bar_date"], day),
        "stored_close": bar["close"],
        "adjclose": bar["adjclose"],
        "volume": bar["volume"],
        "ingest_id": bar["ingest_id"],
        "source_symbol": bar["source_symbol"],
    })
    if bar["close"] is None:
        record["reason"] = REASON_NO_CLOSE
        if bar["adjclose"] is not None:
            record["detail"] = (
                "the bar has an adjclose but no close. adjclose is split AND "
                "dividend adjusted and is not a price level; it is never "
                "substituted here.")
        return record
    stored = float(bar["close"])
    if stored <= 0:
        record["reason"] = REASON_NON_POSITIVE_CLOSE
        return record

    volume = bar["volume"]
    if volume is None or float(volume) == 0.0:
        record["warnings"].append(REASON_ZERO_VOLUME_PRINT)
        if require_traded:
            record["reason"] = REASON_ZERO_VOLUME_PRINT
            record["detail"] = (
                "a zero-volume print is a quote carried forward, not a trade. "
                "FRCB printed $3.51 at volume 0 for two sessions after the FDIC "
                "seizure; the next real trade was $0.3336.")
            return record

    if bound_date is not None:
        bound = {"bound": _day(bound_date), "status": BOUND_CALLER, "warnings": []}
    else:
        bound = ingest_bound(conn, bar["ingest_id"], listing_id)
    record["ingest_bound"] = bound["bound"]
    record["bound_status"] = bound["status"]
    record["warnings"].extend(bound.get("warnings", []))

    actions = price_actions_between(conn, listing_id, bar["bar_date"], bound["bound"])
    record["actions_status"] = actions["status"]
    record["actions_unapplied"] = actions["events"]
    record["ignored_cash_events"] = actions["ignored_cash_events"]
    if actions["unusable"]:
        record["reason"] = actions["unusable"][0]["reason"]
        record["unusable_actions"] = actions["unusable"]
        record["detail"] = (
            "a price-affecting action in the window could not be applied. It is "
            "not skipped: an un-applied split leaves the level wrong by the whole "
            "ratio and nothing downstream would see it.")
        return record
    if actions["status"] == pit_shares.SPLIT_NO_EVENTS_RECORDED:
        record["warnings"].append(
            "no corporate action is recorded for this listing at all, which is "
            "ambiguous between 'none happened' and 'none were ingested'.")

    factor = unapply_factor(actions["events"])
    record.update({
        "available": True,
        "factor": factor,
        "raw_close": stored * factor,
        "reason": None,
    })
    return record


def raw_close(conn: sqlite3.Connection, listing_id: int, date: str,
              **options: Any) -> Optional[float]:
    """The as-traded close, or None. See `raw_close_as_of` for the reasoning.

    None is a first-class answer. It is never the stored close "for now" and
    never an adjusted level standing in until the actions arrive.
    """
    record = raw_close_as_of(conn, listing_id, date, **options)
    return record["raw_close"] if record["available"] else None


# ==========================================================================
# SHARE COUNTS: the designed home, and where they actually live today
# ==========================================================================

def archive_shares_as_of(conn: sqlite3.Connection, entity_id: int, as_of: str, *,
                         ladder: Sequence[str] = pit_shares.SHARES_LADDER_STRICT,
                         enforce_staleness: bool = True) -> dict[str, Any]:
    """`pit_shares.shares_as_of_detail`'s selector, run against `pit_fact`.

    Same ladder, same concepts, same staleness concept, same reason vocabulary,
    same record shape -- the ONLY difference is the table the rows come from.
    It exists because the DERA archive is where the counts are: 589,989 rows
    across 12,689 entities in `pit_fact`, against an empty `pit_share_obs` in
    this store. Re-implementing the policy would be a second opinion waiting to
    diverge, so the policy is imported and only the SQL is new.

    The ladder invariant is enforced here exactly as it is there: a concept that
    is not `point_in_time` raises rather than being quietly accepted, because a
    period average standing in for a count is the failure both modules exist to
    stop.

    One thing the archive cannot give and this record therefore does not claim:
    `pit_fact` holds consolidated rows only -- every dimensional row was dropped
    at ingest, correctly, since 47% of DERA rows are dimensional and a segment's
    value substituted for the company's corrupts a score silently. For a
    multi-class issuer that tags its counts PER CLASS, that means no count at
    all rather than a wrong one. Ford and Fox are exactly this case.
    """
    day = _day(as_of)
    record: dict[str, Any] = {
        "entity_id": entity_id, "as_of": day, "available": False, "reason": None,
        "shares": None, "concept_key": None, "tag": None, "taxonomy": None,
        "measure_kind": None, "measured_at": None, "accn": None, "form": None,
        "filed": None, "available_date": None, "availability_rule": None,
        "age_days": None, "reporting_lag_days": None, "stale": False,
        "zero_count": False, "upper_bound": False,
        "price_basis": PRICE_BASIS_RAW, "ladder": list(ladder), "ladder_trace": [],
        "shares_source": SHARES_SOURCE_ARCHIVE,
        "latency_policy_version": pit_policy.LATENCY_POLICY_VERSION,
    }
    if day is None:
        raise ValueError(f"as_of must be a date: {as_of!r}")

    stale_seen = False
    future_seen = False
    for key in ladder:
        concept = pit_shares.concept_for(key)
        if not concept.point_in_time:
            raise ValueError(
                f"{key} is {concept.measure_kind}, not a point-in-time count; it "
                "may never appear in a shares ladder")
        # No `max_age_days` pre-filter on purpose. It would fold "stale" into
        # "nothing found", and those are different findings the caller must be
        # able to tell apart -- 1,893 of 7,950 peers at 2015-06-30 have a count
        # that is merely too old. `is_stale`'s month arithmetic is the authority
        # anyway; the day bound is only ever a looser pre-filter.
        row = pit_store.latest_period_as_of(
            conn, entity_id, concept.tag, concept.unit, 0, day)
        if row is not None and row["taxonomy"] != concept.taxonomy:
            row = None                       # same tag name, different taxonomy
        if row is None:
            seen = conn.execute(
                "SELECT 1 FROM pit_fact WHERE entity_id = ? AND taxonomy = ? "
                "AND tag = ? LIMIT 1", (entity_id, concept.taxonomy, concept.tag),
            ).fetchone()
            future_seen = future_seen or bool(seen)
            record["ladder_trace"].append(
                {"concept_key": key, "outcome": "not_yet_filed" if seen else "no_rows"})
            continue
        stale = pit_policy.is_stale(row["period_end"], day, int(row["qtrs"]),
                                    pit_shares.STALENESS_CONCEPT)
        if stale and enforce_staleness:
            stale_seen = True
            record["ladder_trace"].append(
                {"concept_key": key, "outcome": "stale",
                 "measured_at": row["period_end"],
                 "age_days": _days_between(row["period_end"], day)})
            continue
        record.update({
            "available": True, "reason": None, "shares": float(row["val"]),
            "concept_key": key, "tag": row["tag"], "taxonomy": row["taxonomy"],
            "measure_kind": concept.measure_kind, "measured_at": row["period_end"],
            "accn": row["accn"], "form": row["form"], "filed": row["filed"],
            "available_date": row["available_date"],
            "availability_rule": row["latency_policy_version"],
            "age_days": _days_between(row["period_end"], day),
            "reporting_lag_days": _days_between(row["period_end"],
                                                row["available_date"]),
            "stale": bool(stale), "zero_count": float(row["val"]) == 0.0,
            "upper_bound": concept.upper_bound,
        })
        record["ladder_trace"].append({"concept_key": key, "outcome": "selected"})
        if record["zero_count"]:
            record["warning"] = REASON_ZERO_SHARE_COUNT
        return record

    if stale_seen:
        record["reason"] = REASON_STALE
    elif future_seen:
        record["reason"] = REASON_NOT_YET_FILED
    else:
        record["reason"] = _archive_absence_reason(conn, entity_id)
    return record


def _archive_absence_reason(conn: sqlite3.Connection, entity_id: int) -> str:
    """Why this issuer has no point-in-time count in the archive AT ALL.

    Mirrors `pit_shares._absence_reason` against `pit_fact`, and keeps its one
    load-bearing distinction: an issuer whose only share-shaped tag is the
    weighted-average diluted count has not partly answered the question. A
    period average is a different measurement, so the count stays absent and the
    reason says which absence it is.
    """
    tags = {c.tag: c.key for c in pit_shares.SHARE_CONCEPTS}
    present: set[str] = set()
    for tag, key in tags.items():
        row = conn.execute(
            "SELECT 1 FROM pit_fact WHERE entity_id = ? AND tag = ? LIMIT 1",
            (entity_id, tag),
        ).fetchone()
        if row:
            present.add(key)
    if not present:
        return REASON_NEVER_FILED
    if present == {pit_shares.CONCEPT_WEIGHTED_DILUTED}:
        return REASON_ONLY_PERIOD_AVERAGE
    if present == {pit_shares.CONCEPT_PUBLIC_FLOAT}:
        return REASON_ONLY_PUBLIC_FLOAT
    if not (present & set(pit_shares.SHARES_LADDER_WITH_ISSUED)):
        return REASON_ONLY_PERIOD_AVERAGE
    return REASON_NEVER_FILED


def shares_as_of_any(conn: sqlite3.Connection, entity_id: int, as_of: str, *,
                     source: str = SHARES_SOURCE_AUTO,
                     ladder: Sequence[str] = pit_shares.SHARES_LADDER_STRICT,
                     ) -> dict[str, Any]:
    """The point-in-time count from whichever table can answer, saying which.

    `auto` prefers `pit_share_obs` -- the designed home, with its own acceptance
    audit trail and append-only triggers -- and falls back to the DERA archive
    when that table is absent or holds nothing for this issuer. The record
    always carries `shares_source`, because "which table answered" changes what
    the number is evidence of and must never be inferred from its shape.
    """
    if source not in (SHARES_SOURCE_AUTO, SHARES_SOURCE_SHARE_OBS,
                      SHARES_SOURCE_ARCHIVE):
        raise ValueError(f"unknown shares source {source!r}")
    use_obs = source == SHARES_SOURCE_SHARE_OBS
    if source == SHARES_SOURCE_AUTO and _table_exists(conn, "pit_share_obs"):
        use_obs = conn.execute(
            "SELECT 1 FROM pit_share_obs WHERE entity_id = ? LIMIT 1", (entity_id,)
        ).fetchone() is not None
    if use_obs:
        if not _table_exists(conn, "pit_share_obs"):
            return {"entity_id": entity_id, "as_of": _day(as_of), "available": False,
                    "reason": "share_observation_table_absent",
                    "shares_source": SHARES_SOURCE_SHARE_OBS, "shares": None,
                    "measured_at": None, "zero_count": False, "upper_bound": False,
                    "ladder": list(ladder), "ladder_trace": []}
        record = dict(pit_shares.shares_as_of_detail(conn, entity_id, as_of,
                                                     ladder=ladder))
        record["shares_source"] = SHARES_SOURCE_SHARE_OBS
        return record
    return archive_shares_as_of(conn, entity_id, as_of, ladder=ladder)


# ==========================================================================
# THE SHARE-CLASS GUARD
# ==========================================================================

def weighted_average_crosscheck(conn: sqlite3.Connection, entity_id: int,
                                as_of: str, shares: Optional[float]) -> dict[str, Any]:
    """Scale-check a count against the trailing weighted-average diluted count.

    The weighted average is a DURATION average built for earnings per share and
    is never a count on a date -- it is read here only as a scale reference,
    which is a use its own concept record permits and substitution does not.

    What the ratio detects, measured on the real cross-section:

      ~1.00   the filed count is the whole company. Median is exactly 1.000.
      ~0.5    one class of a multi-class issuer, taken for the whole.
      ~1000   the two tags were filed in different SCALES -- units against
              thousands -- under the same 'shares' unit string. p99 is ~995.

    `verdict` is one of consistent / below_band / above_band / implausible /
    unavailable. `unavailable` is common and honest: 685 of 3,795 resolvable
    counts at 2024-06-28 have no usable cross-check, and 2,140 of 4,410 at
    2015-06-30, because the issuer tags its diluted average per class.
    """
    day = _day(as_of)
    record: dict[str, Any] = {
        "entity_id": entity_id, "as_of": day, "available": False,
        "wa_diluted": None, "period_end": None, "qtrs": None, "ratio": None,
        "verdict": "unavailable", "tag": CROSSCHECK_TAG,
        "max_age_days": CROSSCHECK_MAX_AGE_DAYS,
    }
    best = None
    for qtrs in (1, 2, 3, 4):
        row = pit_store.latest_period_as_of(
            conn, entity_id, CROSSCHECK_TAG, pit_shares.UNIT_SHARES, qtrs, day,
            max_age_days=CROSSCHECK_MAX_AGE_DAYS)
        if row is None or not row["val"]:
            continue
        if best is None or row["period_end"] > best["period_end"]:
            best = row
    if best is None or shares is None or float(best["val"]) <= 0:
        return record
    ratio = float(shares) / float(best["val"])
    low, high = CORROBORATION_BAND
    floor, ceiling = IMPLAUSIBLE_BAND
    if ratio < floor or ratio > ceiling:
        verdict = "implausible"
    elif ratio < low:
        verdict = "below_band"
    elif ratio > high:
        verdict = "above_band"
    else:
        verdict = "consistent"
    record.update({
        "available": True, "wa_diluted": float(best["val"]),
        "period_end": best["period_end"], "qtrs": int(best["qtrs"]),
        "accn": best["accn"], "available_date": best["available_date"],
        "ratio": ratio, "verdict": verdict,
        "band": list(CORROBORATION_BAND), "implausible_band": list(IMPLAUSIBLE_BAND),
    })
    return record


def share_class_audit(conn: sqlite3.Connection, entity_id: int, as_of: str, *,
                      shares: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Is this count the WHOLE company, or one class of several?

    A per-class count summed wrongly, or a single class taken for the company,
    is a silent 2x error -- it produces a plausible number that ranks as cheap.
    Two independent pieces of evidence exist in this store and the audit uses
    both, in this order:

      SYMBOLS. `pit_symbols.symbols_as_of` returns every symbol reported in the
        latest accession available. Alphabet reports GOOGL 'Class A Common
        Stock' and GOOG 'Class C Capital Stock'; Fox reports FOXA and FOX;
        Berkshire reports BRK.A and BRK.B. Two common-stock symbols, or two
        distinct class letters in the `Security12bTitle`, is direct evidence.
      SCALE. `weighted_average_crosscheck` then asks whether the filed count is
        the size of the whole company. Alphabet's 12,381,000,000 at 2024-03-31
        is A+B+C and would pass; a Class-A-only count would not.

    The known limit, recorded rather than worked around: `dei:Security12bTitle`
    does not exist before the 2019 cover-page rule, so every pre-2019 title is
    NULL and a second class can be invisible to the symbol evidence. That is why
    `confidence` is part of the record and why the scale check still runs when
    the symbols say one class.

    Every reported line is read, not just the ones a delisting test would call
    common stock: `pit_symbols.symbols_as_of`'s default filter answers False for
    Alphabet's GOOG, because "Class C Capital Stock" is not "Common Stock", and
    that is the one row this audit most needs to see.
    """
    day = _day(as_of)
    if not _table_exists(conn, "pit_symbol_obs"):
        # No symbol evidence exists in this store at all. That is not the same
        # as "one class": it is no evidence, and it is recorded as such so the
        # scale check stays the only thing speaking.
        cross = weighted_average_crosscheck(conn, entity_id, day,
                                            (shares or {}).get("shares"))
        return {"entity_id": entity_id, "as_of": day, "class_verdict": CLASS_UNKNOWN,
                "confidence": "none", "symbols": [], "class_letters": [],
                "n_common_symbols": 0, "title_evidence": "absent",
                "note": "pit_symbol_obs is absent from this store",
                "crosscheck": cross, "class_policy_version": CLASS_POLICY_VERSION}
    reported = _symbols_as_of(conn, entity_id, day, require_common=False)
    titles = [r["security_title"] for r in reported if r["security_title"]]
    equity = [r for r in reported
              if r["security_title"] and _EQUITY_TITLE.search(r["security_title"])]
    if titles and not equity:
        # Titles exist and none names an equity line: every reported symbol is a
        # note or a preferred series. Fall back to the class-aware selector,
        # which is what a price lookup would use.
        equity = _symbols_as_of(conn, entity_id, day)
    elif not titles:
        equity = _symbols_as_of(conn, entity_id, day)
    letters = {m.group(1).upper() for r in equity if r["security_title"]
               for m in [_CLASS_LETTER.search(r["security_title"])] if m}
    symbols = sorted({r["symbol"] for r in equity})

    if not equity:
        verdict, confidence = CLASS_UNKNOWN, "none"
    elif len(symbols) > 1 or len(letters) > 1:
        verdict, confidence = CLASS_MULTI, "proved" if titles else "inferred"
    else:
        verdict = CLASS_SINGLE
        confidence = "inferred" if titles else "low"

    count = (shares or {}).get("shares")
    cross = weighted_average_crosscheck(conn, entity_id, day, count)
    record = {
        "entity_id": entity_id, "as_of": day, "class_verdict": verdict,
        "confidence": confidence, "symbols": symbols, "class_letters": sorted(letters),
        "n_common_symbols": len(symbols),
        "title_evidence": "present" if titles else "absent",
        "crosscheck": cross, "class_policy_version": CLASS_POLICY_VERSION,
    }
    if confidence == "low":
        record["note"] = (
            "dei:Security12bTitle does not exist before the 2019 cover-page rule, "
            "so a pre-2019 single-symbol reading cannot rule out a second class.")
    return record


def class_decision(audit: dict[str, Any],
                   policy: str = CLASS_POLICY_STRICT) -> dict[str, Any]:
    """Turn the audit into an allow/refuse, with the resolution named.

    Strict policy, which is the default because the error it prevents is silent:

      multi-class + consistent scale   ALLOW, resolution multi_class_corroborated
      multi-class + off-band scale     REFUSE share_class_ambiguous
      multi-class + no scale check     REFUSE share_class_uncorroborated
      single class + scale below 0.70  REFUSE share_class_ambiguous -- most
                                       likely a second class that the pre-2019
                                       title gap made invisible
      any + scale outside [0.10, 10]   REFUSE share_count_implausible...,
                                       which is a units bug, not a class bug
      otherwise                        ALLOW, resolution single_class

    Permissive returns the same resolution with `ok` True and the refusal moved
    into `warnings`. It is a diagnostic mode: it exists so a coverage report can
    count what the strict path refuses, and no scorer may use it.
    """
    cross = audit.get("crosscheck") or {}
    verdict = cross.get("verdict", "unavailable")
    klass = audit.get("class_verdict")
    ok, reason, resolution = True, None, RESOLUTION_SINGLE

    if verdict == "implausible":
        ok, reason, resolution = False, REASON_COUNT_IMPLAUSIBLE, RESOLUTION_IMPLAUSIBLE
    elif klass == CLASS_MULTI:
        if verdict == "consistent":
            resolution = RESOLUTION_CORROBORATED
        elif verdict == "unavailable":
            ok, reason, resolution = (False, REASON_CLASS_UNCORROBORATED,
                                      RESOLUTION_UNCORROBORATED)
        else:
            ok, reason, resolution = (False, REASON_CLASS_AMBIGUOUS,
                                      RESOLUTION_AMBIGUOUS)
    elif cross.get("ratio") is not None and cross["ratio"] < SINGLE_CLASS_FLOOR:
        ok, reason, resolution = False, REASON_CLASS_AMBIGUOUS, RESOLUTION_AMBIGUOUS

    decision = {"ok": ok, "reason": reason, "resolution": resolution,
                "policy": policy, "class_policy_version": CLASS_POLICY_VERSION,
                "warnings": []}
    if not ok and policy == CLASS_POLICY_PERMISSIVE:
        decision["warnings"].append(reason)
        decision.update({"ok": True, "reason": None, "refused_under_strict": reason})
    return decision


# ==========================================================================
# THE BRIDGE
# ==========================================================================

def market_cap_as_of_detail(conn: sqlite3.Connection, entity_id: int, as_of: str, *,
                            listing_id: Optional[int] = None,
                            raw_price: Optional[float] = None,
                            shares_source: str = SHARES_SOURCE_AUTO,
                            ladder: Sequence[str] = pit_shares.SHARES_LADDER_STRICT,
                            class_policy: str = CLASS_POLICY_STRICT,
                            require_split_check: bool = False,
                            require_traded: bool = False,
                            roll_back_days: int = 0,
                            allow_zero_shares: bool = False) -> dict[str, Any]:
    """MarketCap = reconstructed raw price x point-in-time share count.

    This is the bridge `pit_shares.market_cap_as_of_detail` was written to be
    handed a price by: that function REFUSES any basis but raw and cannot
    un-adjust a price itself, because the share module holds no price data.
    This one reconstructs the raw level from `pit_price_bar` and then hands it
    over.

    Two paths, and the record says which ran:

      pit_share_obs   the whole multiplication is delegated to
                      `pit_shares.market_cap_as_of_detail`, which stays the
                      single authority on the share side.
      pit_fact        the archive path mirrors the same steps here, because the
                      frozen module cannot be taught to accept a share record
                      from elsewhere. `test_pit_rawprice.py` asserts both paths
                      produce the same number from the same fixture, which is
                      the guard against the two drifting apart.

    `market_cap` is None whenever anything is missing or unsafe and `reason`
    names which. A None means the valuation factor -- 40% of the equity Shaffer
    Score -- is unavailable for this entity on this date, and the frozen
    drop-and-renormalise rule applies: see `pit_shares.valuation_dropped_weights`.

    Sanity case this satisfies: Apple at 2020-01-31, raw close 309.51
    reconstructed from a stored 77.3775, times the 4,375,480,000 cover-page
    count dated 2020-01-17, is $1.354tn against ~$1.38tn actual -- the gap being
    the two weeks between the cover date and the as-of.
    """
    day = _day(as_of)
    record: dict[str, Any] = {
        "entity_id": entity_id, "as_of": day, "listing_id": listing_id,
        "market_cap": None, "reason": None, "path": None,
        "price": None, "raw_price": None, "price_basis": PRICE_BASIS_RAW,
        "shares": None, "shares_source": None, "class_audit": None,
        "class_decision": None, "valuation_factor": "unavailable",
        "effective_weights_if_dropped": dict(pit_shares.VALUATION_DROPPED_WEIGHTS),
        "reconstruction_version": RECONSTRUCTION_VERSION,
        "class_policy_version": CLASS_POLICY_VERSION,
        "latency_policy_version": pit_policy.LATENCY_POLICY_VERSION,
    }

    shares = shares_as_of_any(conn, entity_id, day, source=shares_source, ladder=ladder)
    record["shares"] = shares
    record["shares_source"] = shares.get("shares_source")
    if not shares.get("available"):
        record["reason"] = shares.get("reason")
        return record
    if shares.get("zero_count") and not allow_zero_shares:
        record["reason"] = REASON_ZERO_SHARE_COUNT
        record["detail"] = (
            "a genuine, correctly selected zero -- Motors Liquidation Co reports "
            "exactly this -- and a $0 market cap ranks as infinitely cheap.")
        return record

    audit = share_class_audit(conn, entity_id, day, shares=shares)
    decision = class_decision(audit, class_policy)
    record["class_audit"] = audit
    record["class_decision"] = decision
    if not decision["ok"]:
        record["reason"] = decision["reason"]
        record["detail"] = (
            "the share count cannot be shown to cover the whole company. A single "
            "class taken for the issuer is a silent 2x error, so the answer is "
            "UNAVAILABLE rather than a plausible number.")
        return record

    if raw_price is not None:
        price = {"available": True, "raw_close": float(raw_price),
                 "price_basis": PRICE_BASIS_RAW, "price_source": "caller",
                 "actions_unapplied": [],
                 "warnings": ["price declared raw by the caller"]}
    elif listing_id is None:
        record["reason"] = REASON_NO_LISTING
        record["detail"] = ("no listing_id and no raw_price: there is nothing to "
                            "reconstruct a price from. Resolve the listing first "
                            "(pit_identity.resolve_listing).")
        return record
    else:
        owner = conn.execute(
            "SELECT entity_id FROM pit_listing WHERE listing_id = ?", (listing_id,),
        ).fetchone()
        if owner is not None and owner["entity_id"] not in (None, entity_id):
            # A price series from the wrong issuer multiplied by this issuer's
            # share count is a fully provenanced wrong number. Identity is
            # primary in this store and it is checked, not assumed.
            record["reason"] = REASON_LISTING_MISMATCH
            record["detail"] = (
                f"listing {listing_id} belongs to entity {owner['entity_id']}, "
                f"not {entity_id}")
            return record
        price = raw_close_as_of(conn, listing_id, day, roll_back_days=roll_back_days,
                                require_traded=require_traded)
        price["price_source"] = f"pit_rawprice:{RECONSTRUCTION_VERSION}"
    record["price"] = price
    if not price.get("available"):
        record["reason"] = price.get("reason") or REASON_NO_PRICE
        return record
    record["raw_price"] = price["raw_close"]

    if record["shares_source"] == SHARES_SOURCE_SHARE_OBS:
        record["path"] = "pit_shares.market_cap_as_of_detail"
        delegated = pit_shares.market_cap_as_of_detail(
            conn, entity_id, day, price["raw_close"], listing_id=listing_id,
            price_basis=PRICE_BASIS_RAW,
            price_source=price.get("price_source", "pit_rawprice"),
            ladder=ladder, allow_zero_shares=allow_zero_shares,
            require_split_check=require_split_check)
        for key in ("market_cap", "reason", "shares_used", "shares_as_published",
                    "split_factor", "split_check", "split_events", "detail",
                    "upper_bound", "price_basis_required"):
            if key in delegated:
                record[key] = delegated[key]
        record["valuation_factor"] = delegated.get("valuation_factor", "unavailable")
        return record

    record["path"] = "pit_rawprice.archive_path"
    splits = pit_shares.split_factor_between(conn, listing_id, shares["measured_at"], day)
    record.update({"split_factor": splits["factor"], "split_check": splits["status"],
                   "split_events": splits["events"]})
    if require_split_check and splits["status"] in (pit_shares.SPLIT_UNCHECKED,
                                                    pit_shares.SPLIT_NO_EVENTS_RECORDED):
        record["reason"] = pit_shares.REASON_SPLIT_UNRESOLVED
        record["detail"] = (
            f"the split window ({shares['measured_at']}, {day}] was "
            f"{splits['status']}; a split inside it moves the answer by the whole "
            "split ratio.")
        return record
    adjusted = float(shares["shares"]) * float(splits["factor"])
    record.update({
        "shares_as_published": float(shares["shares"]),
        "shares_used": adjusted,
        "market_cap": adjusted * float(price["raw_close"]),
        "valuation_factor": "available",
        "upper_bound": shares.get("upper_bound", False),
        "price_basis_required": pit_shares.price_basis_for(shares),
    })
    return record


def market_cap_as_of(conn: sqlite3.Connection, entity_id: int, as_of: str,
                     **options: Any) -> Optional[dict[str, Any]]:
    """The market cap with its provenance, or None. See the detail function.

    None is never zero and never an estimate. It means the valuation factor is
    unavailable for this entity on this date, and the reason is in the detail
    record -- call `market_cap_as_of_detail` when the reason is what you need.
    """
    record = market_cap_as_of_detail(conn, entity_id, as_of, **options)
    return record if record["market_cap"] is not None else None


# ==========================================================================
# MEASUREMENT. The answer to "is this computable at scale" has to be counted,
# not assumed, and the counting has to be re-runnable.
# ==========================================================================

def market_cap_coverage(conn: sqlite3.Connection, as_of: str, *,
                        entity_ids: Optional[Sequence[int]] = None,
                        ladder: Sequence[str] = pit_shares.SHARES_LADDER_STRICT,
                        limit: Optional[int] = None) -> dict[str, Any]:
    """How many issuers can get a DEFENSIBLE share count, and why the rest cannot.

    Read-only and deliberately price-free: it stops at the share count, the
    symbol and the class guard, because `pit_price_bar` is empty and a coverage
    number that waited for prices would measure the loader instead of the store.

    Counts, per as-of date:
      peers                   the survivorship-free reporting universe
      count_resolved          a fresh point-in-time count exists
      by_rung                 which ladder rung answered
      stale / never_filed / not_yet_filed / only_period_average
      symbol_resolvable       a point-in-time symbol exists (a price needs one)
      count_and_symbol        both -- the ceiling on a computable market cap
      refused_*               what the share-class guard removes, by reason
      defensible              what survives, which is the honest answer
    """
    day = _day(as_of)
    rows = (list(entity_ids) if entity_ids is not None
            else [r["entity_id"] for r in
                  pit_symbols.peer_universe_as_of(conn, day)])
    if limit:
        rows = rows[:limit]
    tally: dict[str, int] = {
        "peers": len(rows), "count_resolved": 0, "stale": 0, "never_filed": 0,
        "not_yet_filed": 0, "only_period_average": 0, "other_reason": 0,
        "symbol_resolvable": 0, "count_and_symbol": 0, "multi_class": 0,
        "crosscheck_available": 0, "refused_ambiguous": 0,
        "refused_uncorroborated": 0, "refused_implausible": 0, "defensible": 0,
    }
    by_rung: dict[str, int] = {}
    for entity_id in rows:
        shares = shares_as_of_any(conn, entity_id, day, ladder=ladder)
        symbols = _symbols_as_of(conn, entity_id, day)
        if symbols:
            tally["symbol_resolvable"] += 1
        if not shares.get("available"):
            reason = shares.get("reason")
            key = {REASON_STALE: "stale", REASON_NEVER_FILED: "never_filed",
                   REASON_NOT_YET_FILED: "not_yet_filed",
                   REASON_ONLY_PERIOD_AVERAGE: "only_period_average"}.get(
                       reason, "other_reason")
            tally[key] += 1
            continue
        tally["count_resolved"] += 1
        by_rung[shares["concept_key"]] = by_rung.get(shares["concept_key"], 0) + 1
        if not symbols:
            continue
        tally["count_and_symbol"] += 1
        audit = share_class_audit(conn, entity_id, day, shares=shares)
        if audit["class_verdict"] == CLASS_MULTI:
            tally["multi_class"] += 1
        if audit["crosscheck"]["available"]:
            tally["crosscheck_available"] += 1
        decision = class_decision(audit, CLASS_POLICY_STRICT)
        if decision["ok"]:
            tally["defensible"] += 1
        elif decision["reason"] == REASON_CLASS_AMBIGUOUS:
            tally["refused_ambiguous"] += 1
        elif decision["reason"] == REASON_CLASS_UNCORROBORATED:
            tally["refused_uncorroborated"] += 1
        else:
            tally["refused_implausible"] += 1
    peers = max(tally["peers"], 1)
    return {
        "as_of": day, "ladder": list(ladder), "tally": tally, "by_rung": by_rung,
        "pct_count_of_peers": round(100.0 * tally["count_resolved"] / peers, 2),
        "pct_computable_of_peers": round(100.0 * tally["count_and_symbol"] / peers, 2),
        "pct_defensible_of_peers": round(100.0 * tally["defensible"] / peers, 2),
        "shares_source_note": (
            "pit_share_obs is empty in the production store, so `auto` reads the "
            "DERA archive in pit_fact. See targeted_fetch_plan for the fix."),
        "measured_at": _now(),
    }


#: Measured 2026-09-21: the median cached companyconcept response for one
#: (entity, concept) is 23.1 KiB on disk. Apple's cover count returns 70 rows,
#: Microsoft's 68, Ford's 8; Alphabet's 404s because it tags per class.
MEASURED_CONCEPT_BYTES = 23.1 * 1024

#: The SEC ceiling this project holds itself to. `pit_identity` owns the session
#: and the throttle, so a fetch here would share one budget with every other
#: SEC caller in the codebase.
SEC_REQUESTS_PER_SECOND = 10


def targeted_fetch_plan(conn: sqlite3.Connection,
                        as_of_dates: Sequence[str] = ("2015-06-30", "2019-06-28",
                                                      "2024-06-28"),
                        concepts: Sequence[str] = pit_shares.SHARES_LADDER_STRICT,
                        ) -> dict[str, Any]:
    """What fetch would fix the share-count gap, and what it would cost.

    The gap is not a loader bug and cannot be closed from what is already here.
    DERA's compact `num.txt` effectively drops the cover-page count -- 606 rows
    for 150 entities in a 14,072,934-row archive -- and drops every dimensional
    row, which is where a multi-class issuer's per-class counts live. Measured
    against the SEC's own API on 2026-09-21, the same issuers answer:

      Apple      dei cover 200 / 70 rows      us-gaap outstanding 200 / 144 rows
      Microsoft  dei cover 200 / 68 rows      us-gaap outstanding 200 / 182 rows
      Ford       dei cover 200 /  8 rows      us-gaap outstanding 404
      Alphabet   dei cover 404                us-gaap outstanding 200 /  88 rows

    Ford is the case that proves the point: the archive holds no point-in-time
    count for it at all, and the SEC publishes one. Alphabet is the case that
    bounds the claim: a per-class cover page is invisible to companyconcept, so
    the fetch raises coverage without closing the multi-class question.

    The fetch already exists -- `pit_shares.ingest_entity_shares` -- so the plan
    is a scope and a cost, not new code. Cost is counted for the entities that
    have a symbol and lack a defensible count: fetching an issuer that can never
    be priced buys nothing.
    """
    need: set[int] = set()
    for day in as_of_dates:
        stamp = _day(day)
        for row in pit_symbols.peer_universe_as_of(conn, stamp):
            entity_id = row["entity_id"]
            if entity_id in need:
                continue
            if not _symbols_as_of(conn, entity_id, stamp):
                continue
            shares = shares_as_of_any(conn, entity_id, stamp)
            if not shares.get("available"):
                need.add(entity_id)
                continue
            audit = share_class_audit(conn, entity_id, stamp, shares=shares)
            if not class_decision(audit, CLASS_POLICY_STRICT)["ok"]:
                need.add(entity_id)
    requests = len(need) * len(concepts)
    return {
        "as_of_dates": [_day(d) for d in as_of_dates],
        "concepts": list(concepts),
        "entities_to_fetch": len(need),
        "requests": requests,
        "requests_per_second": SEC_REQUESTS_PER_SECOND,
        "minutes_at_ceiling": round(requests / SEC_REQUESTS_PER_SECOND / 60.0, 1),
        "estimated_download_bytes": int(requests * MEASURED_CONCEPT_BYTES),
        "estimated_download_gib": round(
            requests * MEASURED_CONCEPT_BYTES / (1024 ** 3), 3),
        "writer": "pit_shares.ingest_entity_shares",
        "target_table": "pit_share_obs",
        "cache_note": ("the HTTP cache is a temp directory and is NOT on the "
                       "store's growth path; only pit_share_obs rows land in the db"),
        "does_not_fix": ("per-class counts. companyconcept and companyfacts carry "
                         "no dimensional facts, so a multi-class issuer that tags "
                         "per class still returns nothing -- Alphabet's cover count "
                         "404s. Those need the filing's own XBRL instance."),
        "measured_at": _now(),
    }


def reconstruction_spec() -> dict[str, Any]:
    """The whole price/market-cap policy as a JSON-serialisable record.

    What a replay run stores so a future reader can tell which rule produced a
    level, which basis it is in, and what the share-class guard was, without
    re-reading this file.
    """
    return {
        "module": "pit_rawprice",
        "reconstruction_version": RECONSTRUCTION_VERSION,
        "class_policy_version": CLASS_POLICY_VERSION,
        "latency_policy_version": pit_policy.LATENCY_POLICY_VERSION,
        "source_column": SOURCE_COLUMN,
        "price_basis": PRICE_BASIS_RAW,
        "refused_price_bases": [PRICE_BASIS_SPLIT_ADJUSTED, PRICE_BASIS_TOTAL_RETURN],
        "formula": "raw_close = stored_close * PROD(ratio_num / ratio_den)",
        "window": "(bar_date, pull_date] over applies_to_price = 1",
        "ratio_event_types": sorted(RATIO_EVENT_TYPES),
        "cash_event_types": sorted(CASH_EVENT_TYPES),
        "corroboration_band": list(CORROBORATION_BAND),
        "single_class_floor": SINGLE_CLASS_FLOOR,
        "implausible_band": list(IMPLAUSIBLE_BAND),
        "crosscheck_tag": CROSSCHECK_TAG,
        "crosscheck_max_age_days": CROSSCHECK_MAX_AGE_DAYS,
        "share_sources": [SHARES_SOURCE_SHARE_OBS, SHARES_SOURCE_ARCHIVE],
        "writes": "nothing -- this module is read-only",
        "why": ("A point-in-time share count is in its own era's share basis. "
                "Yahoo's close is in today's. Pairing them is a clean-looking "
                "error of exactly the split ratio."),
    }


# ==========================================================================
# CLI
# ==========================================================================

def main(argv: Optional[Sequence[str]] = None) -> int:
    """Measure the share-count reality against the production store. Read-only."""
    args = list(argv if argv is not None else sys.argv[1:])
    db_path = pit_store.DEFAULT_PIT_DB_PATH
    if "--db" in args:
        db_path = args[args.index("--db") + 1]
    dates = [a for a in args if re.fullmatch(r"\d{4}-\d{2}-\d{2}", a)] or [
        "2015-06-30", "2019-06-28", "2024-06-28"]

    before = disk_guard(db_path)
    print(f"disk: {before['free_gib']:.2f} GiB free of {before['total_gib']:.1f} "
          f"({before['pct_used']}% used); db {before['db_bytes'] / 2 ** 30:.2f} GiB, "
          f"wal {before['wal_bytes'] / 2 ** 20:.1f} MiB")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")

    reports = []
    for day in dates:
        report = market_cap_coverage(conn, day)
        reports.append(report)
        tally = report["tally"]
        print(f"\n{day}  peers {tally['peers']}")
        print(f"  count resolved      {tally['count_resolved']:>6} "
              f"({report['pct_count_of_peers']}%)  rungs {report['by_rung']}")
        print(f"  no count: stale {tally['stale']}, never filed "
              f"{tally['never_filed']}, only a period average "
              f"{tally['only_period_average']}, not yet filed "
              f"{tally['not_yet_filed']}")
        print(f"  symbol resolvable   {tally['symbol_resolvable']:>6}")
        print(f"  count AND symbol    {tally['count_and_symbol']:>6} "
              f"({report['pct_computable_of_peers']}%)  multi-class "
              f"{tally['multi_class']}, scale check available "
              f"{tally['crosscheck_available']}")
        print(f"  class refusals      ambiguous {tally['refused_ambiguous']}, "
              f"uncorroborated {tally['refused_uncorroborated']}, "
              f"implausible {tally['refused_implausible']}")
        print(f"  DEFENSIBLE          {tally['defensible']:>6} "
              f"({report['pct_defensible_of_peers']}%)")

    plan = targeted_fetch_plan(conn, dates)
    print(f"\ntargeted fetch: {plan['entities_to_fetch']} entities x "
          f"{len(plan['concepts'])} concepts = {plan['requests']} requests, "
          f"{plan['minutes_at_ceiling']} min at {plan['requests_per_second']}/s, "
          f"~{plan['estimated_download_gib']} GiB downloaded")
    if "--json" in args:
        print(json.dumps({"spec": reconstruction_spec(), "coverage": reports,
                          "fetch_plan": plan}, indent=2, default=str))

    after = disk_guard(db_path)
    print(f"disk after: {after['free_gib']:.2f} GiB free, "
          f"wal {after['wal_bytes'] / 2 ** 20:.1f} MiB "
          f"(delta {after['free_gib'] - before['free_gib']:+.3f} GiB). A delta "
          "here is not necessarily this process: it writes nothing, and other "
          "agents write to this store concurrently.")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
