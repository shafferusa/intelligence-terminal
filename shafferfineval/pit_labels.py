"""Forward-return labels and hedge paths -- the outcome half of the replay chain.

Everything upstream of here is point-in-time: a fact, a peer cohort, a score may
only know what was knowable on its as-of date. This module is the one place that
is ALLOWED to know the future, because knowing the future is what an outcome is
for. The availability rule binds the FEATURES; the label is the answer.

That licence is exactly why the honesty rules here are stricter, not looser. A
backtest is not corrupted by a missing label -- it is corrupted by a label that
looks like a number and is not one. Four ways that happens, and what this module
does instead:

  TRUNCATION. A 12-month window opened in 2026-03 has not finished. Taking the
  last available bar as its terminal turns an 6-month return into a 12-month
  one and biases every recent cohort toward whatever the last six months did.
  Here the window is CENSORED: `forward_return` is NULL and `censored` is 1.

  CALENDAR OFFSETS. "+365 days" is not a year of trading. Unscheduled closures
  are real -- Sandy closed 2012-10-29 and 2012-10-30 -- and session counts run
  250 to 253 a year. Every horizon here is a count of sessions on
  `pit_calendar`, resolved through the same index `pit_store.session_offset`
  uses, and the test asserts the two agree on every session in its fixture.

  STALE PRINTS AT THE ENDS. A carried-forward zero-volume print sits exactly
  where it does the most damage: at an event. First Republic printed $3.51 at
  zero volume for two sessions after the FDIC seizure and the next real trade
  was $0.3336 -- a terminal taken from the stale print understates the loss by
  90%. A stale ANCHOR does the opposite and overstates it. The two errors point
  in opposite directions, so neither cancels the other and both are wrong. Both
  ends are therefore required to be real trades, and the roll to the next one is
  recorded per row in `anchor_roll_days` / `terminal_roll_days`.

  SILENT TERMINATION. A series that simply stops is the most dangerous row in
  the table. Filling it with -100% invents a bankruptcy; filling it with zero
  invents a flat year; carrying the last price forward invents a survivor; and
  DROPPING IT is how a backtest lies, because the names that disappear are not a
  random sample. So a stopped series is resolved from a corporate action where
  one exists and is `pit_store.OUTCOME_UNKNOWN` where it does not -- a row that
  exists, is counted, and can never be mistaken for a return.

THE INVARIANT, checkable in one line of SQL:

    forward_return IS NULL  <=>  censored = 1

`censored` means "this row carries no usable return", which is the question a
consumer must ask before pooling. `terminal_reason` says WHY: 'censored' (the
window has not finished), OUTCOME_UNKNOWN (the series stopped and nothing
explains it), or a resolved outcome. A bankruptcy with no recovery is exactly
-1.0 and is NOT censored: that outcome is observed.

WHY THE PATH IS WRITTEN BESIDE THE LABEL. `pit_label` records where a position
ENDED, which is the right target for Shaffer Score and the wrong one for Shaffer
Hedge: a stock that ends flat having fallen 40% needed a hedge and one that
never moved did not, and both have forward_return = 0. `pit_path` (see
`pit_path.py`, whose statistics and barrier ladder are used here, not restated)
records what happened in between. The two are computed in the same pass over the
same window from the same bars, so they can never disagree about which window
they describe.

ADJUSTED CLOSES THROUGHOUT. `adjclose` is split- and dividend-adjusted, and a
corporate action inside the window rescales BOTH endpoints by one common factor,
so the ratio is invariant to it (verified to 2.5e-7 across differing pull
windows). `close` is split-adjusted only and is a price LEVEL, not a return
basis; `pit_rawprice.py` is where a level is reconstructed. `anchor_price` in
this table is therefore an adjusted close as of the pull, never an as-traded
price, and nothing here should be read as one.

DISK IS THE BINDING CONSTRAINT and the discipline is imported, not re-invented.
`pit_prices` already owns the floor, the free-space check and -- the part that
matters -- reading `PRAGMA wal_checkpoint(TRUNCATE)`'s BUSY FLAG, which returns
success-shaped output while doing nothing whenever a concurrent reader holds a
snapshot. That is how a WAL reached 9.3 GB on this volume and filled the disk
without raising a single error. Two modules with two floors is one module
quietly overriding the other's safety margin, so there is one of each.

Tables written: `pit_label` (via the store's own columns) and `pit_path` (via
`pit_path.save_path`'s columns). Nothing else is touched, and the frozen
production core is not imported at all.
"""

from __future__ import annotations

import bisect
import datetime as _dt
import json
import math
import os
import sqlite3
import sys
import tempfile
import time
from typing import Any, Optional, Sequence

import pit_path
import pit_prices
import pit_store
from pit_prices import checkpoint_wal, disk_free_gib, require_disk, wal_bytes
# One retry policy per store, for the same reason there is one disk floor: a
# second copy of "what counts as contention" is a second answer to it. Another
# agent may hold the write lock and a lock is a wait, not a failure.
from pit_prices import _with_lock_retry
from pit_store import transaction

# --------------------------------------------------------------------------
# Versions and vocabulary
# --------------------------------------------------------------------------

#: Stamped in `price_source` on every label row. A later labelling revision
#: lands beside these rows rather than silently reinterpreting them.
LABEL_SOURCE_VERSION = "pit_label_v1_adjclose_sessions"

#: What the return was computed from, recorded per row. `adjclose` is not
#: cosmetic here -- it is the reason a split inside the window cancels.
PRICE_SOURCE = f"{pit_prices.SOURCE_YAHOO_CHART}:adjclose"

#: Horizons in SESSIONS. The mapping has ONE definition and it lives in
#: `pit_path`, because a path and the label it describes disagreeing about how
#: long a year is would be undetectable and fatal.
HORIZON_SESSIONS: dict[str, int] = pit_path.HORIZON_SESSIONS

#: Label horizons, shortest first, so a partial run always leaves the cheap
#: horizons complete.
HORIZON_ORDER: tuple[str, ...] = ("1D", "5D", "1M", "3M", "6M", "12M")

#: Path horizons. 1D and 5D are deliberately absent: a one-session path has two
#: points, its 13-barrier ladder is all misses by construction, and it is not
#: free -- a `pit_path` row is 1,442 bytes MEASURED (see `project_disk`), almost
#: all of it `barriers_json`. Hedge research is about windows a hedge can be
#: bought for.
PATH_HORIZONS: tuple[str, ...] = ("1M", "3M", "6M", "12M")

#: Paths are written on every Nth as-of date, labels on all of them. This is a
#: DISK decision and it is named rather than discovered: at 1,442 bytes a row,
#: paths on all 165 monthly dates for 2,574 listings need 2.3 GiB against the
#: 3.8 GiB of headroom this volume has above its floor -- on a disk another
#: agent is writing to. Quarterly anchors keep all four tenors, which is the
#: axis a hedge decision actually turns on, and cost a third as much. The cheap
#: thing to lose is anchor density: consecutive monthly windows overlap 11/12
#: and, as `independent_observations` measures, add almost no independent
#: information. Set the stride to 1 when the volume allows it.
PATH_GRID_STRIDE = 3

#: Terminal-reason vocabulary. `censored` and OUTCOME_UNKNOWN are different
#: facts and must stay distinguishable: one says the future has not happened
#: yet, the other says it happened and we cannot say what it was.
REASON_COMPLETE = "complete"
REASON_CENSORED = "censored"
REASON_NO_REAL_TRADE = pit_store.OUTCOME_UNKNOWN

#: Terminal corporate-action types. A split, a spin-off and a dividend are NOT
#: terminal -- the series continues through all three -- so they are absent.
EVENT_BANKRUPTCY = "bankruptcy"
EVENT_LIQUIDATION = "liquidation"
EVENT_MERGER_CASH = "merger_cash"
EVENT_MERGER_STOCK = "merger_stock"
TERMINAL_EVENT_TYPES = frozenset({
    EVENT_BANKRUPTCY, EVENT_LIQUIDATION, EVENT_MERGER_CASH, EVENT_MERGER_STOCK,
})

#: How far a missing or zero-volume ANCHOR may be rolled forward, in sessions.
#: Small on purpose: a position opened a week after the as-of date is a
#: different position, and the score it is being matched to was computed on the
#: as-of date. Beyond this the anchor is abandoned and counted, never stretched.
MAX_ANCHOR_ROLL_SESSIONS = 5

#: How far a missing or zero-volume TERMINAL may be rolled forward. Larger than
#: the anchor roll because the event that stops trading is exactly the event
#: worth measuring: FRCB's next real trade came two sessions after the seizure,
#: and a halt around a merger vote can run a fortnight. Past this there is no
#: observable exit price and the row is OUTCOME_UNKNOWN, not a guess.
MAX_TERMINAL_ROLL_SESSIONS = 20

#: A series whose last bar is within this many sessions of the data horizon is
#: ALIVE -- its unfinished windows are censored. A series that stopped earlier
#: TERMINATED, and its windows need an outcome. Without this distinction every
#: delisting looks like censoring and the survivors' bias becomes invisible.
ALIVE_TAIL_SESSIONS = 5

#: Free-space floor in GiB. Deliberately identical to `pit_prices`.
DEFAULT_MIN_FREE_GIB = pit_prices.DEFAULT_MIN_FREE_GIB

#: Rows per write batch, and batches per checkpoint. Small enough that a WAL
#: cannot run away between two looks at it.
DEFAULT_BATCH_ROWS = 20_000
DEFAULT_CHECKPOINT_EVERY = 10

#: Market. One canonical calendar; the store holds exactly one.
MARKET = "XNYS"


SCHEMA = """
-- `pit_label` and `pit_path` are declared by pit_store and pit_path. This
-- module adds one index and nothing else.
--
-- The table's own index is (as_of_date, horizon) -- the cross-section a replay
-- reads. Coverage and repair run the other way, per listing, and without this
-- "which horizons did this listing get" is a scan of 2.4M rows. An audit that
-- costs a full scan is an audit nobody runs twice.
CREATE INDEX IF NOT EXISTS idx_pit_label_listing
    ON pit_label (listing_id, horizon);
"""


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


def _schema_objects(conn: sqlite3.Connection) -> set[str]:
    return {f"{r[1]}:{r[0]}" for r in conn.execute(
        "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'index')")}


def ensure_schema(conn: sqlite3.Connection) -> str:
    """Create `pit_path` and this module's index if absent. Idempotent.

    `pit_label` belongs to `pit_store.SCHEMA` and is created by `init_db`; it is
    not restated here, because a second CREATE TABLE for the same name is how
    two definitions of one table start to drift.
    """
    before = _schema_objects(conn)
    pit_path.ensure_schema(conn)
    conn.executescript(SCHEMA)
    conn.commit()
    created = sorted(_schema_objects(conn) - before)
    return f"created {created}" if created else "already present"


def connect(db_path: str = pit_store.DEFAULT_PIT_DB_PATH) -> sqlite3.Connection:
    """A store connection tuned for a build that shares the file with others."""
    return pit_prices.connect(db_path)


# --------------------------------------------------------------------------
# 1. The calendar, loaded once
# --------------------------------------------------------------------------

def load_sessions(conn: sqlite3.Connection,
                  market: str = MARKET) -> tuple[list[str], dict[str, int]]:
    """The whole session calendar, ordered, plus date -> ordinal.

    `pit_store.session_offset` is the canonical single lookup and stays so; this
    is that same table read once instead of 14 million times. A 2.4M-row build
    doing two SELECTs per label would spend its entire runtime in the calendar.
    The test asserts this index and `session_offset` agree on every session in
    its fixture, which is the only thing that makes the shortcut safe.
    """
    rows = conn.execute(
        """SELECT session_date FROM pit_calendar
            WHERE market = ? ORDER BY session_index""",
        (market,),
    ).fetchall()
    sessions = [r[0] for r in rows]
    return sessions, {day: i for i, day in enumerate(sessions)}


def session_offset_in(sessions: Sequence[str], index: dict[str, int],
                      session_date: str, offset: int) -> Optional[str]:
    """`pit_store.session_offset` against a preloaded calendar.

    Returns None past the end of the calendar -- which is how a label learns it
    is censored instead of silently truncating to the last session that exists.
    """
    base = index.get(session_date)
    if base is None:
        return None
    target = base + offset
    if target < 0 or target >= len(sessions):
        return None
    return sessions[target]


def label_grid(conn: sqlite3.Connection, start: Optional[str] = None,
               end: Optional[str] = None, market: str = MARKET) -> list[str]:
    """The as-of dates to label.

    Defaults to the dates `pit_peer_set` was actually built on, not to every
    month-end. A label at a date where no feature and no peer cohort exists can
    never be joined to anything; it is pure storage. Falls back to the month-end
    grid when no peer set has been built yet, so this module is usable before
    the peer builder has run.
    """
    rows = conn.execute(
        """SELECT DISTINCT as_of_date FROM pit_peer_set
            WHERE (? IS NULL OR as_of_date >= ?)
              AND (? IS NULL OR as_of_date <= ?)
            ORDER BY as_of_date""",
        (start, start, end, end),
    ).fetchall()
    if rows:
        return [r[0] for r in rows]
    return pit_store.month_end_sessions(
        conn, start or "1900-01-01", end or "2999-12-31", market)


def data_horizon(conn: sqlite3.Connection, market: str = MARKET) -> Optional[str]:
    """The last session for which this store could possibly hold a terminal.

    The EARLIER of the last bar and the last calendar session. Both bounds are
    required: a calendar that runs past the price pull would make a censored
    window look resolvable, and a price pull that runs past the calendar would
    put a terminal on a session no horizon can address.
    """
    bar = conn.execute("SELECT MAX(bar_date) FROM pit_price_bar").fetchone()[0]
    session = conn.execute(
        "SELECT MAX(session_date) FROM pit_calendar WHERE market = ?",
        (market,)).fetchone()[0]
    days = [_day(bar), _day(session)]
    days = [d for d in days if d]
    return min(days) if days else None


# --------------------------------------------------------------------------
# 2. Bars and outcomes for one listing
# --------------------------------------------------------------------------

#: Bar tuple layout. Plain tuples rather than a named record: this is the inner
#: loop of a 14-million-bar pass and attribute lookup is not free.
B_DATE, B_ADJ, B_CLOSE, B_VOL, B_INGEST = 0, 1, 2, 3, 4


def load_bars(conn: sqlite3.Connection, listing_id: int) -> list[tuple]:
    """One listing's bars, oldest first, one row per session.

    `pit_price_bar`'s natural key is (listing_id, bar_date, INGEST_ID), so the
    same session can legitimately appear twice after a re-pull. The later ingest
    wins -- not an arbitrary one -- because `adjclose` is retroactively restated
    by every later split and dividend, so mixing two pulls inside one window
    puts a phantom jump in the middle of the return.
    """
    rows = conn.execute(
        """SELECT bar_date, adjclose, close, volume, ingest_id
             FROM pit_price_bar
            WHERE listing_id = ?
            ORDER BY bar_date, ingest_id""",
        (listing_id,),
    ).fetchall()
    out: list[tuple] = []
    for row in rows:
        bar = (row[0], row[1], row[2], row[3], row[4])
        if out and out[-1][B_DATE] == bar[B_DATE]:
            out[-1] = bar
        else:
            out.append(bar)
    return out


def _tradeable(bar: tuple) -> bool:
    """A real trade: a positive adjusted close AND a non-zero volume.

    Both conditions, because a carried-forward print has a perfectly good price
    and is not a price anyone could have transacted at.
    """
    adj = bar[B_ADJ]
    vol = bar[B_VOL]
    return adj is not None and adj > 0 and vol is not None and vol > 0


def terminal_outcome(conn: sqlite3.Connection, listing_id: int,
                     entity_id: Optional[int]) -> Optional[dict[str, Any]]:
    """What ended this listing, if the store can say.

    Read in order of evidential strength:

      1. a TERMINAL corporate action on this listing. Splits, spin-offs and
         dividends are not terminal and are ignored -- the series continues
         through all three.
      2. the issuer's exit record, but only where it establishes a VALUE.
         `exchange_delisting` does not: a delisted stock very often keeps
         trading on the pink sheets, and the store's own price ingest is built
         to follow it there. A merger with no recorded consideration does not
         either -- knowing that a company merged is not knowing for how much.

    Returning None means "nothing here establishes an outcome", which the caller
    turns into OUTCOME_UNKNOWN. That refusal is the point: an exit we cannot
    price is a row that must exist, be counted, and never look like a return.
    """
    row = conn.execute(
        """SELECT event_date, event_type, terminal_value_per_share, exchange_ratio,
                  cash_amount, acquirer_entity_id, confidence
             FROM pit_corporate_action
            WHERE listing_id = ? AND event_type IN ({})
            ORDER BY event_date
            LIMIT 1""".format(",".join("?" * len(TERMINAL_EVENT_TYPES))),
        (listing_id, *sorted(TERMINAL_EVENT_TYPES)),
    ).fetchone()
    if row is not None:
        kind = row["event_type"]
        value = row["terminal_value_per_share"]
        if value is None and kind in (EVENT_MERGER_CASH,):
            value = row["cash_amount"]
        if kind in (EVENT_BANKRUPTCY, EVENT_LIQUIDATION):
            # No recovery is the DEFAULT for a bankruptcy, and it is a fact, not
            # a fallback: common equity in a Chapter 7 is cancelled at zero.
            return {"type": pit_store.OUTCOME_BANKRUPTCY,
                    "date": _day(row["event_date"]),
                    "value_per_share": float(value) if value is not None else 0.0,
                    "source": "pit_corporate_action"}
        if kind == EVENT_MERGER_CASH and value is not None:
            return {"type": pit_store.OUTCOME_MERGER_CASH,
                    "date": _day(row["event_date"]),
                    "value_per_share": float(value),
                    "source": "pit_corporate_action"}
        if kind == EVENT_MERGER_STOCK:
            value = _stock_merger_value(conn, row)
            if value is not None:
                return {"type": pit_store.OUTCOME_MERGER_STOCK,
                        "date": _day(row["event_date"]),
                        "value_per_share": float(value),
                        "source": "pit_corporate_action+acquirer_close"}
        return None

    if entity_id is None:
        return None
    exit_row = conn.execute(
        "SELECT exit_date, exit_type FROM pit_entity_exit WHERE entity_id = ?",
        (entity_id,),
    ).fetchone()
    if exit_row is None:
        return None
    kind = (exit_row["exit_type"] or "").strip().lower()
    if kind in (EVENT_BANKRUPTCY, EVENT_LIQUIDATION):
        return {"type": pit_store.OUTCOME_BANKRUPTCY,
                "date": _day(exit_row["exit_date"]), "value_per_share": 0.0,
                "source": "pit_entity_exit"}
    return None


def _stock_merger_value(conn: sqlite3.Connection, action: sqlite3.Row) -> Optional[float]:
    """Cash-equivalent per share of a stock merger, or None.

    exchange_ratio x the ACQUIRER's as-traded close on the event date. Both
    halves are required and neither is assumed: without the ratio there is no
    conversion, and without the acquirer's own listing there is no price to
    convert into. None here becomes OUTCOME_UNKNOWN, which is the correct answer
    to "they got stock, in what and worth what" when the store cannot say.
    """
    ratio = action["exchange_ratio"]
    acquirer = action["acquirer_entity_id"]
    day = _day(action["event_date"])
    if ratio is None or acquirer is None or day is None:
        return None
    row = conn.execute(
        """SELECT b.close FROM pit_price_bar b
             JOIN pit_listing l ON l.listing_id = b.listing_id
            WHERE l.entity_id = ? AND b.bar_date <= ? AND b.close > 0
            ORDER BY b.bar_date DESC LIMIT 1""",
        (int(acquirer), day),
    ).fetchone()
    if row is None:
        return None
    return float(ratio) * float(row[0])


def _adjustment_factor(bars: Sequence[tuple]) -> float:
    """adjclose / close at the last real bar -- the bridge from cash to adjusted.

    A terminal value per share is an AS-TRADED amount; every other price in this
    module is an adjusted close. Dividing a cash amount into an adjusted anchor
    without this factor mixes two scales and silently misstates the return of
    every dividend-paying name. Falls back to 1.0 only when `close` is unusable,
    which for a name with no dividends is exact anyway.
    """
    for bar in reversed(bars):
        close = bar[B_CLOSE]
        adj = bar[B_ADJ]
        if close and close > 0 and adj and adj > 0:
            return float(adj) / float(close)
    return 1.0


# --------------------------------------------------------------------------
# 3. The pure core: labels and paths for one listing
# --------------------------------------------------------------------------

def compute_listing_labels(bars: Sequence[tuple], as_of_dates: Sequence[str],
                           sessions: Sequence[str], index: dict[str, int],
                           horizon_end: str, *, horizons: Sequence[str] = HORIZON_ORDER,
                           path_horizons: Sequence[str] = PATH_HORIZONS,
                           outcome: Optional[dict[str, Any]] = None,
                           path_dates: Optional[set] = None,
                           anchor_roll_max: int = MAX_ANCHOR_ROLL_SESSIONS,
                           terminal_roll_max: int = MAX_TERMINAL_ROLL_SESSIONS,
                           alive_tail: int = ALIVE_TAIL_SESSIONS,
                           ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Every label and path for one listing. Pure: no connection, no writes.

    `bars` is one listing's series, oldest first, deduplicated. `horizon_end` is
    the store's data horizon -- the last session a terminal could exist on.
    `outcome` is what `terminal_outcome` found, or None. `path_dates` narrows
    the path grid without narrowing the label grid: paths are 3.7x the storage
    of a label and their marginal information falls off with anchor density.

    Returns (labels, paths, counters). A label dict carries exactly the fields
    `pit_label` stores; a path dict carries the window plus the stats
    `pit_path.compute_path` produced, so the writer does no arithmetic.

    THE THREE ENDINGS, kept apart on purpose:

      the window has not finished          -> censored, reason 'censored'
      the series stopped and we know why   -> the resolved outcome, a real return
      the series stopped and we do not     -> censored, reason OUTCOME_UNKNOWN

    and the invariant that makes them safe to pool: forward_return IS NULL if
    and only if censored is 1.
    """
    labels: list[dict[str, Any]] = []
    paths: list[dict[str, Any]] = []
    counters: dict[str, int] = {}

    def bump(key: str, n: int = 1) -> None:
        counters[key] = counters.get(key, 0) + n

    if not bars:
        return labels, paths, counters

    bar_dates = [b[B_DATE] for b in bars]
    first_bar, last_bar = bar_dates[0], bar_dates[-1]
    path_set = set(path_horizons)

    # Alive at the horizon, or terminated? Without this every delisting looks
    # like an unfinished window and the survivorship of the cohort disappears.
    horizon_i = index.get(horizon_end)
    last_i = index.get(last_bar)
    if horizon_i is None or last_i is None:
        alive = last_bar >= horizon_end
    else:
        alive = (horizon_i - last_i) <= alive_tail
    factor = _adjustment_factor(bars) if outcome else 1.0

    for as_of in as_of_dates:
        base = index.get(as_of)
        if base is None:
            bump("as_of_not_a_session")
            continue
        if as_of < first_bar or as_of > last_bar:
            bump("as_of_outside_series")
            continue

        # -- anchor: the first REAL trade at or after the as-of date ----------
        a0 = bisect.bisect_left(bar_dates, as_of)
        ai = a0
        while ai < len(bars) and not _tradeable(bars[ai]):
            ai += 1
        if ai >= len(bars):
            bump("anchor_abandoned_no_trade")
            continue
        anchor_bar = bars[ai]
        anchor_roll = _gap(index, as_of, anchor_bar[B_DATE], ai - a0)
        if anchor_roll > anchor_roll_max:
            bump("anchor_abandoned_roll_exceeded")
            continue
        if anchor_roll:
            bump("anchor_rolled")
        anchor_price = float(anchor_bar[B_ADJ])

        for horizon in horizons:
            sessions_out = HORIZON_SESSIONS.get(horizon)
            if sessions_out is None:
                continue
            row = {
                "as_of_date": as_of,
                "horizon": horizon,
                "anchor_date": anchor_bar[B_DATE],
                "anchor_price": anchor_price,
                "anchor_roll_days": anchor_roll,
                "end_date": None,
                "end_price": None,
                "terminal_roll_days": 0,
                "forward_return": None,
                "censored": 1,
                "terminal_reason": REASON_CENSORED,
                "price_ingest_id": anchor_bar[B_INGEST],
            }

            target = session_offset_in(sessions, index, as_of, sessions_out)
            if target is None or target > horizon_end:
                # Past the calendar or past the data horizon: the future has
                # not happened. NEVER truncate to the last available bar.
                bump("censored_horizon")
                labels.append(row)
                continue

            ti = bisect.bisect_left(bar_dates, target)
            walked = 0
            while ti < len(bars) and not _tradeable(bars[ti]):
                ti += 1
                walked += 1

            if ti >= len(bars):
                # The series stops before the terminal session.
                if walked > terminal_roll_max:
                    # Not a stopped series: a series whose remaining prints are
                    # all carried-forward. There is no observable exit price.
                    row["terminal_reason"] = REASON_NO_REAL_TRADE
                    bump("terminal_roll_exceeded")
                    labels.append(row)
                    continue
                if alive:
                    # It is alive at the data horizon; the vendor simply has no
                    # bar this far out yet. Censored, not terminated.
                    bump("censored_series_short")
                    labels.append(row)
                    continue
                resolved = _resolve_terminal(row, outcome, anchor_price, factor,
                                             target, last_bar)
                bump("terminated_" + str(resolved))
                labels.append(row)
                continue

            end_bar = bars[ti]
            terminal_roll = _gap(index, target, end_bar[B_DATE], walked)
            if terminal_roll > terminal_roll_max:
                # A real trade exists, but so far past the horizon that it is a
                # different observation. No observable exit price.
                row["terminal_reason"] = REASON_NO_REAL_TRADE
                bump("terminal_roll_exceeded")
                labels.append(row)
                continue
            if terminal_roll:
                bump("terminal_rolled")

            end_price = float(end_bar[B_ADJ])
            row["end_date"] = end_bar[B_DATE]
            row["end_price"] = end_price
            row["terminal_roll_days"] = terminal_roll
            row["forward_return"] = end_price / anchor_price - 1.0
            row["censored"] = 0
            row["terminal_reason"] = REASON_COMPLETE
            bump("complete")
            labels.append(row)

            if horizon in path_set and (path_dates is None or as_of in path_dates):
                window = [(b[B_DATE], b[B_ADJ], b[B_VOL]) for b in bars[ai:ti + 1]]
                stats = pit_path.compute_path(window, anchor_price)
                paths.append({
                    "as_of_date": as_of, "horizon": horizon,
                    "anchor_date": anchor_bar[B_DATE], "anchor_price": anchor_price,
                    "end_date": end_bar[B_DATE],
                    "price_ingest_id": anchor_bar[B_INGEST],
                    "stats": stats,
                })

    return labels, paths, counters


def _gap(index: dict[str, int], start: str, end: str, fallback: int) -> int:
    """Sessions between two dates, in CALENDAR sessions where both are on it.

    The fallback is the distance in BARS, used when a vendor bar falls on a day
    the canonical calendar does not carry. Counting bars there under-reports the
    roll (the missing days are exactly the ones the calendar disagrees about),
    so it is a floor, not an equivalent -- which is why the calendar is tried
    first and the fallback is never silently preferred.
    """
    a = index.get(start)
    b = index.get(end)
    if a is None or b is None:
        return max(0, fallback)
    return max(0, b - a)


def _resolve_terminal(row: dict[str, Any], outcome: Optional[dict[str, Any]],
                      anchor_price: float, factor: float, target: str,
                      last_bar: str) -> str:
    """Fill a terminated row in place and return the reason it was given.

    A bankruptcy with no recovery is EXACTLY -1.0: the terminal value is zero,
    so the arithmetic is 0/anchor - 1 and no floating-point residue survives it.
    A bankruptcy with an OTC stub never arrives here at all -- the stub is in the
    series, so the ordinary terminal path prices it, which is precisely the
    behaviour wanted.

    Anything the outcome cannot price stays censored with OUTCOME_UNKNOWN. That
    row is not a failure of this function; it is the honest record of a position
    whose exit the store cannot establish, and dropping it is how a backtest
    lies about the names that disappeared.
    """
    if not outcome:
        row["terminal_reason"] = pit_store.OUTCOME_UNKNOWN
        return pit_store.OUTCOME_UNKNOWN

    kind = outcome.get("type") or pit_store.OUTCOME_UNKNOWN
    value = outcome.get("value_per_share")
    event_day = outcome.get("date") or last_bar
    if value is None:
        row["terminal_reason"] = pit_store.OUTCOME_UNKNOWN
        return pit_store.OUTCOME_UNKNOWN

    end_price = 0.0 if float(value) == 0.0 else float(value) * factor
    row["end_date"] = event_day
    row["end_price"] = end_price
    row["forward_return"] = end_price / anchor_price - 1.0
    row["censored"] = 0
    row["terminal_reason"] = kind
    return kind


# --------------------------------------------------------------------------
# 4. Writers. Bulk, and proved identical to the single-row helpers.
# --------------------------------------------------------------------------

#: `pit_store.save_label`'s columns, in its order. The bulk writer below issues
#: the SAME upsert; `test_pit_labels.py` writes one row each way and compares
#: every column, so the two cannot drift.
LABEL_COLUMNS = (
    "listing_id", "entity_id", "as_of_date", "horizon", "anchor_date",
    "anchor_price", "anchor_roll_days", "end_date", "end_price",
    "terminal_roll_days", "forward_return", "benchmark_forward_return",
    "excess_return", "net_forward_return", "cost_assumption_set_id",
    "censored", "terminal_reason", "price_source", "price_ingest_id",
    "computed_at", "label_policy_version", "label_run_id",
    "benchmark_listing_id",
)

#: `label_policy_version` IS in the DO UPDATE list, deliberately. The column is
#: not in the table's UNIQUE key -- it could not be, see `pit_labelset` -- so
#: the only thing standing between a second policy and a silent overwrite of
#: 2.3M rows is `trg_pit_label_policy_immutable`, and that trigger fires only
#: on an UPDATE that actually assigns the column. Leaving it out of this list
#: would disarm the guard exactly where it is needed.
_LABEL_INSERT = """
INSERT INTO pit_label ({cols}) VALUES ({marks})
ON CONFLICT (listing_id, as_of_date, horizon) DO UPDATE SET
     anchor_date = excluded.anchor_date,
     anchor_price = excluded.anchor_price,
     anchor_roll_days = excluded.anchor_roll_days,
     end_date = excluded.end_date,
     end_price = excluded.end_price,
     terminal_roll_days = excluded.terminal_roll_days,
     forward_return = excluded.forward_return,
     benchmark_forward_return = excluded.benchmark_forward_return,
     excess_return = excluded.excess_return,
     net_forward_return = excluded.net_forward_return,
     cost_assumption_set_id = excluded.cost_assumption_set_id,
     censored = excluded.censored,
     terminal_reason = excluded.terminal_reason,
     computed_at = excluded.computed_at,
     label_policy_version = excluded.label_policy_version,
     label_run_id = excluded.label_run_id,
     benchmark_listing_id = excluded.benchmark_listing_id
""".format(cols=", ".join(LABEL_COLUMNS), marks=", ".join("?" * len(LABEL_COLUMNS)))


def label_row_tuple(listing_id: int, entity_id: Optional[int],
                    row: dict[str, Any], *, benchmark: Optional[float] = None,
                    benchmark_listing_id: Optional[int] = None,
                    label_policy_version: str = pit_store.LABEL_POLICY_VERSION,
                    label_run_id: Optional[int] = None,
                    computed_at: Optional[str] = None) -> tuple:
    """One label dict as a tuple in LABEL_COLUMNS order.

    `excess_return` is derived here and only here, so a benchmark can never be
    subtracted twice, and it is NULL whenever either leg is NULL -- an excess
    return against a censored benchmark is not a small error, it is a number
    with no meaning.

    `benchmark_listing_id` is stamped even when `benchmark` is None, because a
    row that says which benchmark was measured and reports NULL is honest,
    while a row with a NULL benchmark and no instrument named is unreadable:
    nothing distinguishes "the benchmark window had not finished" from "no
    benchmark was ever applied".

    `label_policy_version` defaults to the store's CURRENT policy rather than a
    frozen literal, so a v2 labeller that bumps the constant is stamped by
    bumping one constant -- and `pit_store`'s triggers refuse both an unstamped
    row and an upsert that would change a row's policy.
    """
    forward = row.get("forward_return")
    excess = None
    if benchmark is not None and forward is not None:
        excess = forward - benchmark
    return (
        listing_id, entity_id, row["as_of_date"], row["horizon"],
        row.get("anchor_date"), row.get("anchor_price"),
        int(row.get("anchor_roll_days", 0)), row.get("end_date"),
        row.get("end_price"), int(row.get("terminal_roll_days", 0)),
        forward, benchmark, excess, None, None,
        int(row.get("censored", 1)), row.get("terminal_reason"),
        PRICE_SOURCE, row.get("price_ingest_id"), computed_at or _now(),
        label_policy_version, label_run_id, benchmark_listing_id,
    )


def insert_labels(conn: sqlite3.Connection, rows: Sequence[Sequence[Any]]) -> int:
    """Bulk upsert label tuples in LABEL_COLUMNS order. Returns rows attempted.

    `pit_store.save_label` stays the canonical single-row writer and its SQL is
    reproduced here verbatim rather than paraphrased. The reason this exists at
    all is arithmetic: `save_label` commits per row, and 2.4 million commits is
    not a slow build, it is a build that does not finish.
    """
    if not rows:
        return 0
    with transaction(conn):
        conn.executemany(_LABEL_INSERT, rows)
    return len(rows)


#: `pit_path.save_path`'s columns, in its order, for the same reason.
PATH_COLUMNS = (
    "listing_id", "as_of_date", "horizon", "anchor_date", "anchor_price",
    "end_date", "n_sessions", "max_adverse_excursion", "mae_date",
    "sessions_to_mae", "max_favourable_excursion", "mfe_date", "sessions_to_mfe",
    "max_drawdown", "drawdown_peak_date", "drawdown_trough_date",
    "drawdown_sessions", "realized_vol", "downside_vol", "terminal_return",
    "barriers_json", "path_quality", "quality_reason", "zero_volume_sessions",
    "price_source", "price_ingest_id", "model_version", "computed_at",
)

_PATH_INSERT = """
INSERT INTO pit_path ({cols}) VALUES ({marks})
ON CONFLICT (listing_id, as_of_date, horizon, model_version) DO UPDATE SET
    end_date=excluded.end_date, n_sessions=excluded.n_sessions,
    max_adverse_excursion=excluded.max_adverse_excursion,
    mae_date=excluded.mae_date, sessions_to_mae=excluded.sessions_to_mae,
    max_favourable_excursion=excluded.max_favourable_excursion,
    mfe_date=excluded.mfe_date, sessions_to_mfe=excluded.sessions_to_mfe,
    max_drawdown=excluded.max_drawdown,
    drawdown_peak_date=excluded.drawdown_peak_date,
    drawdown_trough_date=excluded.drawdown_trough_date,
    drawdown_sessions=excluded.drawdown_sessions,
    realized_vol=excluded.realized_vol, downside_vol=excluded.downside_vol,
    terminal_return=excluded.terminal_return,
    barriers_json=excluded.barriers_json, path_quality=excluded.path_quality,
    quality_reason=excluded.quality_reason,
    zero_volume_sessions=excluded.zero_volume_sessions,
    computed_at=excluded.computed_at
""".format(cols=", ".join(PATH_COLUMNS), marks=", ".join("?" * len(PATH_COLUMNS)))


def path_row_tuple(listing_id: int, row: dict[str, Any],
                   computed_at: Optional[str] = None) -> tuple:
    """One path dict as a tuple in PATH_COLUMNS order."""
    stats = row["stats"]
    return (
        listing_id, row["as_of_date"], row["horizon"], row["anchor_date"],
        row["anchor_price"], row.get("end_date"), stats.get("n_sessions", 0),
        stats.get("max_adverse_excursion"), stats.get("mae_date"),
        stats.get("sessions_to_mae"), stats.get("max_favourable_excursion"),
        stats.get("mfe_date"), stats.get("sessions_to_mfe"),
        stats.get("max_drawdown"), stats.get("drawdown_peak_date"),
        stats.get("drawdown_trough_date"), stats.get("drawdown_sessions"),
        stats.get("realized_vol"), stats.get("downside_vol"),
        stats.get("terminal_return"),
        pit_store.dumps(stats.get("barriers") or {}),
        stats.get("path_quality", "unusable"), stats.get("quality_reason"),
        int(stats.get("zero_volume_sessions", 0)), PRICE_SOURCE,
        row.get("price_ingest_id"), pit_path.PATH_MODEL_VERSION,
        computed_at or _now(),
    )


def insert_paths(conn: sqlite3.Connection, rows: Sequence[Sequence[Any]]) -> int:
    """Bulk upsert path tuples in PATH_COLUMNS order. Returns rows attempted."""
    if not rows:
        return 0
    with transaction(conn):
        conn.executemany(_PATH_INSERT, rows)
    return len(rows)


# --------------------------------------------------------------------------
# 5. Disk projection -- measured on a throwaway store, not guessed
# --------------------------------------------------------------------------

def project_disk(n_labels: int, n_paths: int, sample: int = 2000) -> dict[str, Any]:
    """How much disk this build will need, MEASURED before it starts.

    A byte-per-row constant written into a comment is a guess that ages. This
    writes a real sample of both row shapes -- including `barriers_json`, which
    is most of a path row -- into a throwaway database under the system temp
    directory, measures the file, and extrapolates. The production store is not
    touched and not opened.

    The history this exists for: a bulk write on this volume filled a 238 GiB
    disk. Knowing the size of the write BEFORE starting it is the difference
    between a run that declines and a run that has to be cleaned up after.
    """
    tmpdir = tempfile.mkdtemp(prefix="pit_labels_project_")
    path = os.path.join(tmpdir, "project.db")
    try:
        conn = pit_store.init_db(path)
        pit_path.ensure_schema(conn)
        conn.executescript(SCHEMA)
        conn.execute("PRAGMA foreign_keys = OFF")
        base = os.path.getsize(path)

        barriers = {f"{s:+.2f}": {"hit": s > -0.10, "date": "2019-06-28",
                                  "session": 41} for s in pit_path.BARRIER_LADDER}
        # The benchmark, excess, net and cost columns are populated in the
        # sample, not left NULL: they are populated on the real table too, and a
        # projection measured on rows four columns narrower than the ones that
        # will be written is a projection that under-reads by ~30 bytes a row.
        label_rows = [
            (1, 2, f"2019-{1 + i % 12:02d}-28", "12M", "2019-01-31", 123.456789, 0,
             "2020-01-31", 145.6789, 0, 0.18, 0.12, 0.06, 0.1776, 2, 0,
             REASON_COMPLETE, PRICE_SOURCE, 171, _now(),
             pit_store.LABEL_POLICY_VERSION, 1, 2575)
            for i in range(sample)
        ]
        # A distinct as_of per row so the unique index is exercised realistically.
        label_rows = [r[:2] + (f"2019-01-{1 + i % 28:02d}T{i}",) + r[3:]
                      for i, r in enumerate(label_rows)]
        with transaction(conn):
            conn.executemany(_LABEL_INSERT, label_rows)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        after_labels = os.path.getsize(path)

        path_rows = [
            (1, f"2019-01-{1 + i % 28:02d}T{i}", "12M", "2019-01-31", 123.456789,
             "2020-01-31", 252, -0.21, "2019-08-05", 130, 0.18, "2020-01-02", 230,
             -0.27, "2019-07-01", "2019-08-05", 25, 0.2841, 0.3122, 0.18,
             json.dumps(barriers, sort_keys=True), "complete", None, 0,
             PRICE_SOURCE, 171, pit_path.PATH_MODEL_VERSION, _now())
            for i in range(sample)
        ]
        with transaction(conn):
            conn.executemany(_PATH_INSERT, path_rows)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        after_paths = os.path.getsize(path)
        conn.close()

        label_bytes = max(1.0, (after_labels - base) / float(sample))
        path_bytes = max(1.0, (after_paths - after_labels) / float(sample))
        projected = n_labels * label_bytes + n_paths * path_bytes
        return {
            "label_bytes_per_row": round(label_bytes, 1),
            "path_bytes_per_row": round(path_bytes, 1),
            "projected_mib": round(projected / 2 ** 20, 1),
            "sample_rows": sample,
        }
    finally:
        for name in ("project.db", "project.db-wal", "project.db-shm"):
            try:
                os.remove(os.path.join(tmpdir, name))
            except OSError:
                pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass


# --------------------------------------------------------------------------
# 6. The build
# --------------------------------------------------------------------------

def listings_with_bars(conn: sqlite3.Connection,
                       limit: Optional[int] = None) -> list[tuple[int, Optional[int]]]:
    """(listing_id, entity_id) for every COMPANY listing that has price history.

    Driven from `pit_price_bar`, not from `pit_listing`: a resolved listing with
    no bars has nothing to label, and iterating it would cost a query per empty
    listing to discover that.

    Benchmarks are excluded by `instrument_kind`, and that filter is the price
    of keeping benchmark bars in `pit_price_bar` where every other rule about
    them already lives (see `pit_benchmark`). Without it VTI would be labelled
    as though it were a company, land in `pit_label` beside the 2,573 issuers,
    and then be benchmarked against itself.
    """
    rows = conn.execute(
        """SELECT b.listing_id, l.entity_id
             FROM (SELECT DISTINCT listing_id FROM pit_price_bar) b
             JOIN pit_listing l ON l.listing_id = b.listing_id
            WHERE l.instrument_kind = ?
            ORDER BY b.listing_id""",
        (pit_store.INSTRUMENT_COMPANY,),
    ).fetchall()
    out = [(int(r[0]), int(r[1]) if r[1] is not None else None) for r in rows]
    return out[:limit] if limit else out


def benchmark_returns(conn: sqlite3.Connection, symbol: str,
                      as_of_dates: Sequence[str], sessions: Sequence[str],
                      index: dict[str, int], horizon_end: str,
                      horizons: Sequence[str] = HORIZON_ORDER,
                      ) -> dict[tuple[str, str], float]:
    """(as_of, horizon) -> the benchmark's forward return, for excess returns.

    Computed by the SAME core as every other listing, so the benchmark cannot be
    measured on a different convention from the names measured against it.

    Returns an empty map when the symbol has no listing. That WAS the case for
    every symbol until 2026-09-21: `pit_listing` held no index instrument,
    because the listing gate is driven from issuers that file
    `dei:TradingSymbol` and an index ETF trust does not appear that way. It is
    now seeded explicitly by `pit_benchmark`, which is the module a caller
    should reach for -- it resolves the REGISTERED default for a benchmark kind
    rather than trusting a ticker, and a ticker is not an identity.

    Synthesising a benchmark from this cohort's own cross-section would still be
    worse than none: every accepted listing here is alive at the data horizon,
    so the cohort's own mean is a survivors' mean and subtracting it would bake
    that bias into every excess return in the store.

    A benchmark listing is preferred over a company listing with the same
    ticker. They should never collide -- `pit_benchmark`'s gate refuses a symbol
    an issuer already claims -- but if one ever does, "the benchmark named SPY"
    is the right answer to this function and "some issuer that used to trade as
    SPY" is not.
    """
    row = conn.execute(
        """SELECT listing_id FROM pit_listing WHERE symbol = ?
            ORDER BY (instrument_kind = ?) DESC, first_trade_date LIMIT 1""",
        (symbol, pit_store.INSTRUMENT_BENCHMARK),
    ).fetchone()
    if row is None:
        return {}
    bars = load_bars(conn, int(row[0]))
    labels, _paths, _counters = compute_listing_labels(
        bars, as_of_dates, sessions, index, horizon_end,
        horizons=horizons, path_horizons=())
    return {(label["as_of_date"], label["horizon"]): label["forward_return"]
            for label in labels if label.get("forward_return") is not None}


def build_labels(conn: sqlite3.Connection, *,
                 db_path: str = pit_store.DEFAULT_PIT_DB_PATH,
                 as_of_dates: Optional[Sequence[str]] = None,
                 listings: Optional[Sequence[tuple[int, Optional[int]]]] = None,
                 horizons: Sequence[str] = HORIZON_ORDER,
                 path_horizons: Sequence[str] = PATH_HORIZONS,
                 path_stride: int = PATH_GRID_STRIDE,
                 min_free_gib: float = DEFAULT_MIN_FREE_GIB,
                 batch_rows: int = DEFAULT_BATCH_ROWS,
                 checkpoint_every: int = DEFAULT_CHECKPOINT_EVERY,
                 max_label_rows: Optional[int] = None,
                 benchmark_symbol: Optional[str] = None,
                 label_policy_version: str = pit_store.LABEL_POLICY_VERSION,
                 label_run_id: Optional[int] = None,
                 progress: bool = True) -> dict[str, Any]:
    """Label every listing on the grid, writing labels and paths together.

    Disk is checked BEFORE every batch, not once at start-up: a check at
    start-up tells you the disk was fine an hour ago, and another agent shares
    this volume. Every checkpoint's BUSY FLAG is read and counted -- a busy
    checkpoint does nothing at all while returning success-shaped output, which
    is exactly how a WAL grew to 9.3 GB here unnoticed.

    Returns a summary with the measured peaks, never an assumed one, and with
    the free-space minimum reported as a VOLUME fact rather than as this run's
    consumption: the db grew while three other agents were writing to it, and
    claiming the whole delta would be a lie in this direction.
    """
    started = time.monotonic()
    try:
        free_start = require_disk(db_path, min_free_gib)
    except RuntimeError as exc:
        # The floor is checked before anything is opened, and refusing is a
        # RESULT: a caller gets a summary it can print, not a traceback that
        # looks like a crash in the middle of a bulk write.
        return {"status": "aborted", "reason": str(exc),
                "free_gib_at_start": round(disk_free_gib(db_path), 2)}
    db_bytes_start = os.path.getsize(db_path) if os.path.exists(db_path) else 0

    sessions, index = load_sessions(conn)
    horizon_end = data_horizon(conn)
    if not sessions or not horizon_end:
        return {"status": "aborted", "reason": "no calendar or no price bars"}

    grid = list(as_of_dates) if as_of_dates is not None else label_grid(conn)
    off_calendar = [d for d in grid if d not in index]
    grid = [d for d in grid if d in index]
    targets = list(listings) if listings is not None else listings_with_bars(conn)

    # The benchmark's own listing is resolved and STAMPED on every row, not
    # only its returns subtracted. A row that reports a benchmark return with
    # no instrument named cannot be audited and cannot be compared with a row
    # benchmarked against something else. See `pit_benchmark`.
    bench: dict[tuple[str, str], float] = {}
    bench_listing_id: Optional[int] = None
    if benchmark_symbol:
        row = conn.execute(
            """SELECT listing_id FROM pit_listing WHERE symbol = ?
                ORDER BY (instrument_kind = ?) DESC, first_trade_date LIMIT 1""",
            (benchmark_symbol, pit_store.INSTRUMENT_BENCHMARK)).fetchone()
        bench_listing_id = int(row[0]) if row else None
        bench = benchmark_returns(conn, benchmark_symbol, grid, sessions,
                                  index, horizon_end, horizons)

    path_grid = grid[::max(1, int(path_stride))]
    path_dates = set(path_grid)

    projection = project_disk(
        len(targets) * len(grid) * len(horizons),
        len(targets) * len(path_grid) * len(path_horizons))
    headroom_mib = (free_start - min_free_gib) * 1024
    if projection["projected_mib"] > headroom_mib:
        return {
            "status": "aborted",
            "reason": (f"projected {projection['projected_mib']:.0f} MiB exceeds "
                       f"{headroom_mib:.0f} MiB of headroom above the "
                       f"{min_free_gib:.1f} GiB floor"),
            "projection": projection, "free_gib_at_start": round(free_start, 2),
        }

    label_buf: list[tuple] = []
    path_buf: list[tuple] = []
    counters: dict[str, int] = {}
    n_labels = n_paths = 0
    batches = checkpoints = checkpoints_busy = 0
    peak_wal = wal_bytes(db_path)
    min_free = free_start
    stopped_early = None

    def flush(force: bool = False) -> None:
        nonlocal n_labels, n_paths, batches, checkpoints, checkpoints_busy
        nonlocal peak_wal, min_free
        if not force and len(label_buf) < batch_rows and len(path_buf) < batch_rows:
            return
        if not label_buf and not path_buf:
            return
        free = require_disk(db_path, min_free_gib)
        min_free = min(min_free, free)
        n_labels += _with_lock_retry(insert_labels, conn, label_buf)
        n_paths += _with_lock_retry(insert_paths, conn, path_buf)
        label_buf.clear()
        path_buf.clear()
        batches += 1
        peak_wal = max(peak_wal, wal_bytes(db_path))
        if batches % checkpoint_every == 0:
            result = checkpoint_wal(conn, db_path)
            checkpoints += 1
            if result.get("busy"):
                checkpoints_busy += 1
            peak_wal = max(peak_wal, result.get("wal_before") or 0)
            min_free = min(min_free, disk_free_gib(db_path))

    try:
        for done, (listing_id, entity_id) in enumerate(targets, start=1):
            bars = load_bars(conn, listing_id)
            if not bars:
                continue
            outcome = terminal_outcome(conn, listing_id, entity_id)
            labels, paths, counts = compute_listing_labels(
                bars, grid, sessions, index, horizon_end,
                horizons=horizons, path_horizons=path_horizons,
                path_dates=path_dates, outcome=outcome)
            for key, value in counts.items():
                counters[key] = counters.get(key, 0) + value
            stamp = _now()
            for label in labels:
                label_buf.append(label_row_tuple(
                    listing_id, entity_id, label,
                    benchmark=bench.get((label["as_of_date"], label["horizon"])),
                    benchmark_listing_id=bench_listing_id,
                    label_policy_version=label_policy_version,
                    label_run_id=label_run_id,
                    computed_at=stamp))
            for path in paths:
                path_buf.append(path_row_tuple(listing_id, path, stamp))
            flush()
            if progress and done % 200 == 0:
                print(f"  {done}/{len(targets)} listings  "
                      f"{n_labels + len(label_buf):,} labels  "
                      f"{n_paths + len(path_buf):,} paths  "
                      f"free {min_free:.2f} GiB  wal {peak_wal / 2 ** 20:.1f} MiB",
                      flush=True)
            if max_label_rows and n_labels + len(label_buf) >= max_label_rows:
                stopped_early = f"max_label_rows {max_label_rows} reached"
                break
        flush(force=True)
    except RuntimeError as exc:
        # The disk floor. A partial build that reports its boundary is a result;
        # a full volume is not. The buffered rows are dropped rather than forced.
        stopped_early = str(exc)
        label_buf.clear()
        path_buf.clear()

    result = checkpoint_wal(conn, db_path)
    checkpoints += 1
    if result.get("busy"):
        checkpoints_busy += 1
    peak_wal = max(peak_wal, result.get("wal_before") or 0)
    min_free = min(min_free, disk_free_gib(db_path))

    db_bytes_end = os.path.getsize(db_path) if os.path.exists(db_path) else 0
    return {
        "status": "complete" if not stopped_early else "stopped",
        "stopped_reason": stopped_early,
        "n_listings": len(targets),
        "n_as_of_dates": len(grid),
        "as_of_first": grid[0] if grid else None,
        "as_of_last": grid[-1] if grid else None,
        "as_of_off_calendar": len(off_calendar),
        "horizons": list(horizons),
        "path_horizons": list(path_horizons),
        "path_stride": int(path_stride),
        "n_path_as_of_dates": len(path_grid),
        "data_horizon": horizon_end,
        "labels_written": n_labels,
        "paths_written": n_paths,
        "counters": dict(sorted(counters.items())),
        "benchmark_symbol": benchmark_symbol,
        "benchmark_points": len(bench),
        "batches": batches,
        "checkpoints": checkpoints,
        "checkpoints_busy": checkpoints_busy,
        "peak_wal_mib": round(peak_wal / 2 ** 20, 1),
        "free_gib_at_start": round(free_start, 2),
        "free_gib_minimum": round(min_free, 2),
        "db_growth_mib_shared_volume": round(
            (db_bytes_end - db_bytes_start) / 2 ** 20, 1),
        "projection": projection,
        "seconds": round(time.monotonic() - started, 1),
    }


# --------------------------------------------------------------------------
# 7. Coverage -- first-class output, not a footnote
# --------------------------------------------------------------------------

def coverage_report(conn: sqlite3.Connection) -> dict[str, Any]:
    """Label coverage by horizon, plus the roll and censoring counts.

    Every number a consumer needs in order to know what it is allowed to pool:
    how many rows carry a return, how many are censored and why, how often an
    endpoint had to be rolled to a real trade, and how much of the universe is
    represented at all.
    """
    by_horizon = []
    for row in conn.execute(
            """SELECT horizon,
                      COUNT(*) AS n_rows,
                      SUM(CASE WHEN forward_return IS NOT NULL THEN 1 ELSE 0 END) AS n_labelled,
                      SUM(censored) AS n_censored,
                      SUM(CASE WHEN terminal_reason = ? THEN 1 ELSE 0 END) AS n_unknown,
                      SUM(CASE WHEN anchor_roll_days > 0 THEN 1 ELSE 0 END) AS n_anchor_rolled,
                      SUM(CASE WHEN terminal_roll_days > 0 THEN 1 ELSE 0 END) AS n_terminal_rolled,
                      MAX(anchor_roll_days) AS max_anchor_roll,
                      MAX(terminal_roll_days) AS max_terminal_roll,
                      COUNT(DISTINCT listing_id) AS n_listings,
                      COUNT(DISTINCT entity_id) AS n_entities
                 FROM pit_label GROUP BY horizon""",
            (pit_store.OUTCOME_UNKNOWN,)):
        by_horizon.append({k: row[k] for k in row.keys()})
    by_horizon.sort(key=lambda r: HORIZON_SESSIONS.get(r["horizon"], 0))

    totals = conn.execute(
        """SELECT COUNT(*) AS n_rows,
                  COUNT(DISTINCT listing_id) AS n_listings,
                  COUNT(DISTINCT entity_id) AS n_entities,
                  COUNT(DISTINCT as_of_date) AS n_dates,
                  MIN(as_of_date) AS first_date, MAX(as_of_date) AS last_date
             FROM pit_label""").fetchone()

    reasons = {r[0]: r[1] for r in conn.execute(
        "SELECT terminal_reason, COUNT(*) FROM pit_label GROUP BY 1 ORDER BY 2 DESC")}

    invariant = conn.execute(
        """SELECT COUNT(*) FROM pit_label
            WHERE (forward_return IS NULL AND censored = 0)
               OR (forward_return IS NOT NULL AND censored = 1)""").fetchone()[0]

    paths = conn.execute(
        """SELECT COUNT(*) AS n_rows, COUNT(DISTINCT listing_id) AS n_listings,
                  SUM(CASE WHEN path_quality = 'complete' THEN 1 ELSE 0 END) AS n_complete,
                  SUM(CASE WHEN path_quality = 'partial' THEN 1 ELSE 0 END) AS n_partial,
                  SUM(zero_volume_sessions) AS zero_volume_sessions
             FROM pit_path""").fetchone()

    return {
        "totals": {k: totals[k] for k in totals.keys()},
        "by_horizon": by_horizon,
        "terminal_reasons": reasons,
        "invariant_violations_null_return_vs_censored": invariant,
        "paths": {k: paths[k] for k in paths.keys()},
    }


def coverage_by_year(conn: sqlite3.Connection,
                     horizon: str = "12M") -> list[dict[str, Any]]:
    """Label coverage per calendar year of the as-of date, for one horizon.

    Read this next to the terminal-reason counts: a year whose labels are all
    censored is not a year of data, and the last twelve months of any panel look
    exactly like that by construction.
    """
    rows = conn.execute(
        """SELECT substr(as_of_date, 1, 4) AS year,
                  COUNT(*) AS n_rows,
                  SUM(CASE WHEN forward_return IS NOT NULL THEN 1 ELSE 0 END) AS n_labelled,
                  SUM(censored) AS n_censored,
                  COUNT(DISTINCT listing_id) AS n_listings,
                  AVG(forward_return) AS mean_return
             FROM pit_label WHERE horizon = ?
            GROUP BY year ORDER BY year""",
        (horizon,)).fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]


#: Scales the design effect is measured on. `raw` is included and is NOT the
#: headline: measured on this store, 12M raw returns have a standard deviation
#: of 37.8 against a median of +0.058, because 870 of them exceed +900% and the
#: largest is +1,749,900% -- sub-penny OTC prints where an adjusted-close ratio
#: is arithmetic rather than a return. On that scale the within-date variance is
#: so inflated that the date effect vanishes and the estimator reports a design
#: effect of 1.0, which would claim 355,727 independent annual observations.
#: That number is wrong and it is wrong in the dangerous direction, so the
#: headline is the log scale -- the standard one for co-movement -- and the raw
#: figure is kept beside it as the evidence for why.
SCALE_LOG = "log"
SCALE_WINSORIZED = "winsorized"
SCALE_RAW = "raw"
RETURN_SCALES = (SCALE_LOG, SCALE_WINSORIZED, SCALE_RAW)

#: Winsorisation bounds, as pooled quantiles. Applied to the whole horizon's
#: distribution at once, never per date: trimming each cross-section separately
#: removes part of the very date effect being measured.
WINSOR_LO, WINSOR_HI = 0.01, 0.99


def return_distribution(conn: sqlite3.Connection,
                        horizon: str = "12M") -> dict[str, Any]:
    """The shape of one horizon's realised returns, quantile by quantile.

    A coverage statistic, not decoration. A label set whose mean is seven times
    its median is not a distribution any pooled statistic describes, and the
    rows responsible are identifiable: sub-penny OTC listings, where a ratio of
    adjusted closes is a real number that nobody could have traded. Reported so
    a consumer winsorises or filters on price level DELIBERATELY rather than
    discovering the tail inside a backtest result.
    """
    values = [r[0] for r in conn.execute(
        """SELECT forward_return FROM pit_label
            WHERE horizon = ? AND forward_return IS NOT NULL""", (horizon,))]
    if not values:
        return {"horizon": horizon, "n": 0}
    values.sort()
    n = len(values)

    def q(fraction: float) -> float:
        return values[min(n - 1, max(0, int(fraction * (n - 1))))]

    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / max(1, n - 1)
    return {
        "horizon": horizon, "n": n,
        "min": round(values[0], 4), "p01": round(q(0.01), 4),
        "p25": round(q(0.25), 4), "median": round(q(0.50), 4),
        "p75": round(q(0.75), 4), "p99": round(q(0.99), 4),
        "max": round(values[-1], 1),
        "mean": round(mean, 4), "sd": round(var ** 0.5, 3),
        "n_above_10x": sum(1 for v in values if v > 9.0),
        "n_above_100x": sum(1 for v in values if v > 99.0),
        "n_total_loss_exactly_minus_1": sum(1 for v in values if v == -1.0),
    }


def independent_observations(conn: sqlite3.Connection, horizon: str = "12M",
                             ) -> dict[str, Any]:
    """How many GENUINELY INDEPENDENT observations this panel holds.

    The row count is not the sample size and the difference is not small. Two
    things destroy independence here and they compound:

      OVERLAP. Monthly anchors with a 252-session window share 11/12 of their
      period with their neighbour. Consecutive rows are near-repeats of one
      draw, not two.

      ONE MARKET. Every name on a given as-of date lives through the SAME
      twelve months. 2,340 names in March 2020 are not 2,340 draws from the
      distribution of annual returns; they are one bad year seen 2,340 times.

    Both are measured here rather than asserted. The second is a one-way
    random-effects design effect: decompose the variance into a between-date
    and a within-date component, take the intraclass correlation
    rho = s2_between / (s2_between + s2_within), and the effective sample size
    is N / (1 + (m - 1) * rho) with m the average cross-section. The unbalanced
    correction m0 is the standard one, so cross-sections of different sizes are
    not silently averaged as if equal.

    The first is handled by SUBSAMPLING rather than by modelling: take every
    12th as-of date -- a set of windows that do not overlap at all -- and run
    the same calculation on it. There are twelve such phases; all twelve are
    computed, and the spread between them is itself a result, because a headline
    that moves by a factor of two depending on which month you start in is a
    headline with an error bar.

    MEASURED ON THREE SCALES, and the headline is the log one. See
    RETURN_SCALES: on raw returns this store's tails are so heavy that the
    estimator reports no clustering at all, which is arithmetically correct and
    substantively false. `n_effective_non_overlapping_median` on the log scale
    is the honest answer to "how many independent annual outcomes are in here",
    and for a panel of this shape it is in the hundreds, not the millions.
    """
    rows = conn.execute(
        """SELECT as_of_date, forward_return FROM pit_label
            WHERE horizon = ? AND forward_return IS NOT NULL AND censored = 0
            ORDER BY as_of_date""",
        (horizon,)).fetchall()
    if not rows:
        return {"horizon": horizon, "n_observations": 0,
                "note": "no uncensored labels at this horizon"}

    pooled = sorted(float(r[1]) for r in rows)
    n_pooled = len(pooled)
    lo = pooled[min(n_pooled - 1, int(WINSOR_LO * (n_pooled - 1)))]
    hi = pooled[min(n_pooled - 1, int(WINSOR_HI * (n_pooled - 1)))]

    scales: dict[str, dict[str, list[float]]] = {s: {} for s in RETURN_SCALES}
    n_dropped_log = 0
    for date, value in rows:
        value = float(value)
        scales[SCALE_RAW].setdefault(date, []).append(value)
        scales[SCALE_WINSORIZED].setdefault(date, []).append(min(hi, max(lo, value)))
        if value > -1.0:
            scales[SCALE_LOG].setdefault(date, []).append(math.log1p(value))
        else:
            # An exact total loss has no logarithm. It is a real outcome and is
            # counted here rather than winsorised into a survivable one.
            n_dropped_log += 1

    sessions_per_year = HORIZON_SESSIONS.get(horizon, 252)
    stride = max(1, round(sessions_per_year / 21.0))

    out: dict[str, Any] = {"horizon": horizon, "scales": {}}
    for scale in RETURN_SCALES:
        by_date = scales[scale]
        dates = sorted(by_date)
        overall = _design_effect(by_date, dates)
        phases = []
        for phase in range(stride):
            subset = dates[phase::stride]
            if len(subset) < 2:
                continue
            sub = _design_effect(by_date, subset)
            sub["phase"] = phase
            sub["first_date"] = subset[0]
            phases.append(sub)
        effective = sorted(p["n_effective"] for p in phases) or [0.0]
        out["scales"][scale] = {
            "n_observations": overall["n"],
            "n_as_of_dates": len(dates),
            "mean_cross_section": round(overall["n"] / max(1, len(dates)), 1),
            "intraclass_correlation_date": round(overall["rho"], 5),
            "design_effect_all_dates": round(overall["deff"], 1),
            "n_effective_all_dates": round(overall["n_effective"], 1),
            "n_effective_non_overlapping_median": round(
                effective[len(effective) // 2], 1),
            "n_effective_non_overlapping_min": round(min(effective), 1),
            "n_effective_non_overlapping_max": round(max(effective), 1),
            "phases": [{k: (round(v, 5) if isinstance(v, float) else v)
                        for k, v in p.items()} for p in phases],
        }

    dates = sorted(scales[SCALE_RAW])
    span = conn.execute(
        """SELECT COUNT(*) FROM pit_calendar
            WHERE market = ? AND session_date >= ? AND session_date <= ?""",
        (MARKET, dates[0], dates[-1])).fetchone()[0]
    # Windows opened at the first and last date both run `sessions_per_year`
    # forward, so the period the panel spans is the grid span plus one horizon.
    n_blocks = max(1, int((span + sessions_per_year) // sessions_per_year))

    head = out["scales"][SCALE_LOG]
    out.update({
        "headline_scale": SCALE_LOG,
        "n_observations": len(rows),
        "n_as_of_dates": len(dates),
        "mean_cross_section": round(len(rows) / len(dates), 1),
        "first_date": dates[0], "last_date": dates[-1],
        "non_overlapping_stride_months": stride,
        "n_non_overlapping_periods_in_span": n_blocks,
        "winsor_bounds": [round(lo, 4), round(hi, 4)],
        "n_total_loss_excluded_from_log": n_dropped_log,
        "intraclass_correlation_date": head["intraclass_correlation_date"],
        "design_effect_all_dates": head["design_effect_all_dates"],
        "n_effective_all_dates": head["n_effective_all_dates"],
        "n_effective_non_overlapping_median":
            head["n_effective_non_overlapping_median"],
        "n_effective_non_overlapping_min": head["n_effective_non_overlapping_min"],
        "n_effective_non_overlapping_max": head["n_effective_non_overlapping_max"],
    })
    return out


def _design_effect(by_date: dict[str, list[float]],
                   dates: Sequence[str]) -> dict[str, float]:
    """One-way random-effects design effect over a set of cross-sections.

    Returns rho (the intraclass correlation induced by the shared market
    period), the design effect, and the effective sample size. A negative
    between-group variance estimate -- which the unbiased estimator can produce
    when the true effect is near zero -- is clipped to zero rather than reported
    as a negative variance, and that clipping is why rho can be exactly 0.
    """
    groups = [by_date[d] for d in dates if len(by_date.get(d, ())) > 0]
    n_total = sum(len(g) for g in groups)
    k = len(groups)
    if k < 2 or n_total <= k:
        return {"rho": 0.0, "deff": 1.0, "n_effective": float(n_total),
                "n": n_total, "k": k}

    grand = sum(sum(g) for g in groups) / n_total
    ss_between = sum(len(g) * (sum(g) / len(g) - grand) ** 2 for g in groups)
    ss_within = 0.0
    for g in groups:
        mean = sum(g) / len(g)
        ss_within += sum((x - mean) ** 2 for x in g)
    ms_between = ss_between / (k - 1)
    ms_within = ss_within / (n_total - k)
    m0 = (n_total - sum(len(g) ** 2 for g in groups) / n_total) / (k - 1)
    m_bar = n_total / k
    if ms_within <= 0.0:
        # Every name on a date moved identically. That is not "no clustering",
        # it is TOTAL clustering -- the cross-section carries one observation,
        # repeated. Returning rho = 0 here (the arithmetic's natural division by
        # zero) would report the most dependent panel imaginable as the most
        # independent one, so the degenerate case is answered explicitly.
        if ms_between > 0.0:
            return {"rho": 1.0, "deff": m_bar, "n_effective": float(k),
                    "n": n_total, "k": k}
        return {"rho": 0.0, "deff": 1.0, "n_effective": float(n_total),
                "n": n_total, "k": k}
    if m0 <= 0:
        return {"rho": 0.0, "deff": 1.0, "n_effective": float(n_total),
                "n": n_total, "k": k}
    var_between = max(0.0, (ms_between - ms_within) / m0)
    rho = var_between / (var_between + ms_within)
    deff = 1.0 + (m_bar - 1.0) * rho
    return {"rho": rho, "deff": deff, "n_effective": n_total / deff,
            "n": n_total, "k": k}


def survivorship_report(conn: sqlite3.Connection) -> dict[str, Any]:
    """How much of this label set is a survivors' sample.

    The single most important caveat a consumer of `pit_label` needs, and it is
    a property of the PRICE INGEST rather than of the labelling: the listing
    gate is driven from symbols a live vendor still answers for, so a company
    that died is one the gate could not accept. A cohort in which every listing
    trades on the last day of the sample has no failures in it, and a model
    trained on it has never seen one.

    Benchmark listings are excluded. An index ETF is alive at the horizon by
    construction, so counting one here would move `listings_alive_at_data_horizon`
    toward 100% -- flattering the very number that exists to show how bad the
    survivorship is.
    """
    horizon = data_horizon(conn)
    row = conn.execute(
        """SELECT COUNT(*) AS n_listings,
                  SUM(CASE WHEN last_bar >= ? THEN 1 ELSE 0 END) AS n_alive_at_horizon,
                  MIN(last_bar) AS earliest_last_bar
             FROM (SELECT b.listing_id, MAX(b.bar_date) AS last_bar
                     FROM pit_price_bar b
                     JOIN pit_listing l ON l.listing_id = b.listing_id
                    WHERE l.instrument_kind = ?
                    GROUP BY b.listing_id)""",
        (horizon, pit_store.INSTRUMENT_COMPANY)).fetchone()
    labelled = conn.execute(
        """SELECT COUNT(DISTINCT listing_id) FROM pit_label""").fetchone()[0]
    return {
        "data_horizon": horizon,
        "listings_with_bars": row["n_listings"],
        "listings_alive_at_data_horizon": row["n_alive_at_horizon"],
        "earliest_last_bar": row["earliest_last_bar"],
        "listings_labelled": labelled,
        "terminal_corporate_actions": conn.execute(
            "SELECT COUNT(*) FROM pit_corporate_action WHERE event_type IN ({})"
            .format(",".join("?" * len(TERMINAL_EVENT_TYPES))),
            tuple(sorted(TERMINAL_EVENT_TYPES))).fetchone()[0],
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    """Build labels and paths, then report coverage. Bounded by disk."""
    args = list(sys.argv[1:] if argv is None else argv)

    def opt(name: str, default: Any) -> Any:
        if name in args:
            return args[args.index(name) + 1]
        return default

    db_path = str(opt("--db", pit_store.DEFAULT_PIT_DB_PATH))
    limit = int(opt("--limit", 0)) or None
    max_rows = int(opt("--max-rows", 0)) or None
    min_free = float(opt("--min-free-gib", DEFAULT_MIN_FREE_GIB))
    start = opt("--start", None)
    end = opt("--end", None)
    benchmark = opt("--benchmark", None)
    path_stride = int(opt("--path-stride", PATH_GRID_STRIDE))
    report_only = "--report-only" in args

    conn = connect(db_path)
    print(ensure_schema(conn))
    print(f"disk: {disk_free_gib(db_path):.2f} GiB free, floor {min_free:.2f} GiB, "
          f"wal {wal_bytes(db_path) / 2 ** 20:.1f} MiB")

    if not report_only:
        grid = label_grid(conn, start, end)
        targets = listings_with_bars(conn, limit=limit)
        print(f"grid: {len(grid)} as-of dates "
              f"{grid[0] if grid else '-'}..{grid[-1] if grid else '-'}; "
              f"{len(targets):,} listings; horizon {data_horizon(conn)}")
        summary = build_labels(conn, db_path=db_path, as_of_dates=grid,
                               listings=targets, min_free_gib=min_free,
                               path_stride=path_stride, max_label_rows=max_rows,
                               benchmark_symbol=benchmark)
        print(json.dumps(summary, indent=2, sort_keys=True))

    print("\ncoverage:")
    print(json.dumps(coverage_report(conn), indent=2, sort_keys=True))
    print("\nby year (12M):")
    for row in coverage_by_year(conn, "12M"):
        print(f"  {row['year']}  rows {row['n_rows']:>8,}  "
              f"labelled {row['n_labelled']:>8,}  censored {row['n_censored']:>8,}  "
              f"listings {row['n_listings']:>6,}  "
              f"mean {row['mean_return'] if row['mean_return'] is None else round(row['mean_return'], 4)}")
    print("\nreturn distribution (12M):")
    print(json.dumps(return_distribution(conn, "12M"), indent=2, sort_keys=True))
    print("\nindependent observations:")
    print(json.dumps(independent_observations(conn, "12M"), indent=2, sort_keys=True))
    print("\nsurvivorship:")
    print(json.dumps(survivorship_report(conn), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
