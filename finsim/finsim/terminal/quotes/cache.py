"""QuoteCache, ChainCache, record builders and the ``quotes.persist_batch`` light command (TERMINAL_DESIGN.md §8.5).

Conventions
- A record's ``quality`` is recomputed at READ time with the current session (``freshness()``, §8.2); the stored
  record is never mutated in place — ``put`` replaces the object, readers get a copy, so no thread sees a half-updated
  quote.  ``put`` returns the ids whose price / size / time / bar changed (what the matcher re-evaluates).
- Scheduling (``QUOTE_INTERVALS``): an instrument's refresh interval is the smallest of its pins — ORDER 15 s, HELD
  30 s, PAGE 30 s, WATCH 60 s (UI subscriptions expire 90 s after the last poll) — and CLOSED 300 s whenever its
  session is closed.  A symbol answering 404/400 is ``unavailable`` with the error and retried hourly.  ``due()`` hands
  out each due id once per interval (the attempt is scheduled when taken, so a failing fetch is not retried every
  second).  A new or shorter pin on an instrument already scheduled further out moves it forward (last attempt + the
  new interval), so opening a page or placing an order never waits for a list page's 20-minute schedule.
- BROWSE (owner 2026-10-21: "it can pull 50 prices per minute and stagger it within 15/30 min"): the lowest class, for
  list pages.  An instrument whose only pin is BROWSE is refreshed every ``QUOTE_INTERVALS["BROWSE"]`` (20 min) whatever
  the session state, and ``due()`` hands out at most ``config.BROWSE_PER_MIN`` (50) such refreshes per rolling minute,
  spread over the minute (a token bucket of ``BROWSE_BURST``), oldest observation first (never observed, then the
  oldest successful fetch); the rest stay due for later minutes.  ORDER / HELD / PAGE / WATCH (and any other class)
  are never held back by that budget, and a BROWSE row whose refresh costs no network request (a bond or private name
  without a Yahoo symbol: the worker only records "no quote source") never uses it.  ``browse_stats()`` reports it.
- A failed fetch never makes an old print look fresh (EXEC-2, 2026-10-21): ``record_error`` keeps the record's
  ``fetched_at`` and ``quote_time`` (the last SUCCESSFUL observation) and only attaches the error and the attempt time
  (``last_error_at``), so §8.2 ages it on schedule — stale 60 s after the last good fetch while an error is attached,
  5 min after it at the latest — and executions pause (``is_executable`` -> STALE) until a good fetch replaces it.
- Restored records (``load_persisted`` after a restart) come back ``close`` or ``stale``, never ``delayed``, until the
  first live fetch replaces them: they carry ``restored=True``, which ``freshness`` / ``is_executable`` honour (EXEC-3:
  the label alone did not stop the order path), so a DAY order can never match on a print the process has not observed
  itself; a failed first fetch keeps the flag.
- ``due()`` hands out first the ids that were never observed and carry an ORDER / HELD / PAGE / WATCH pin (an instrument
  page or a ticket just opened: USX-08), so their first fetch starts within one worker tick, ahead of every refresh.
- ``ChainCache.underlying_quote(uid)`` (EXEC-7): the Cboe underlying bid / ask / sizes of a fresh cached chain as a
  ``QuoteRecord`` (method ``chain_underlying``), so a stock order fills at the observed ask (buys) / bid (sells) while a
  fresh chain of it is in memory (nothing is fetched for this).
- ``ChainCache``: an LRU of two reduced chains with ONE in-flight fetch per underlying shared by every requester
  (single-flight ``Future``); the worker serves pending requests one at a time so the SPX parse never overlaps another.
- Persistence (TX-6, 2026-10-21): ``persist()`` (the worker's 60 s slot and shutdown) upserts only the records that
  changed since the last persist (the dirty set: every ``put``, and a ``record_error`` that creates a record or changes
  its error text) straight into ``quote_cache`` in one short writer transaction — never through the command journal
  (the old ``quotes.persist_batch`` command stored the WHOLE cache in ``commands.payload_json`` every 60 s: +97 MB a day,
  1 GB with the Markets list).  ``updated_ts`` is the record's own observation time (``fetched_at``; the last attempt
  for a record never fetched), so the 00:15 HOUSEKEEPING prune sees real ages.  BROWSE (list) records are persisted
  like the others — dirty only, so at most the 50-a-minute BROWSE refreshes — so a restart shows the Markets list's
  last prices (``stale``, with their times) instead of blank rows for the ~16 minutes the budget needs to refill it.
- Pruning: ``prune(keep)`` (the persist slot, before each persist) drops from memory — and, at the next persist, from
  ``quote_cache`` — every record that has had no pin of any class and was not in ``keep`` (held / working / borrowed /
  watch-listed names, the inputs of open positions' marks: ``RatesRefresher.keep_ids``) for ``PRUNE_IDLE_S`` (1 h).
  Held / ordered / watched records are never dropped, so a restart still shows their last marks with their times.
- ``compact_persist_journal`` (run by ``Database.open`` before any worker): the one-time repair of databases written by
  older builds — payloads of ``quotes.persist_batch`` rows older than 24 h become a record-count summary, then VACUUM
  when that is quick and safe.
"""
from __future__ import annotations

import copy
import heapq
import json
import os
import shutil
import threading
import time as _time
from concurrent.futures import Future
from collections import OrderedDict, deque
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Tuple

from ..config import BROWSE_PER_MIN, QUOTE_INTERVALS
from ..db import canonical_json, get_meta, set_meta
from ..instruments import Instrument
from ..units import qprice
from . import EXECUTABLE_QUALITIES, QuoteRecord, freshness
from .providers import ChainSnapshot, YAHOO_DELAY_LABEL, YAHOO_DELAY_S, YahooIntraday

BROWSE = "BROWSE"
PRIORITY_RANK = {"ORDER": 0, "HELD": 1, "PAGE": 2, "WATCH": 3, BROWSE: 9}
UNKNOWN_RANK = 5                 # any other class (e.g. PRIVATE_LISTED) ranks below WATCH and above BROWSE, and is never throttled
BROWSE_BURST = 5                 # BROWSE hand-outs one due() call may make after a quiet spell; the steady rate is BROWSE_PER_MIN / 60 s
RETRY_NOT_FOUND_S = 3600
PRUNE_IDLE_S = 3600              # a record nobody pins, holds, works, watches or marks with is dropped after this long (TX-6)
# the one-time journal compaction (compact_persist_journal, TX-6)
COMPACT_KIND = "quotes.persist_batch"
COMPACT_AGE_S = 86400            # the last day's rows keep their payload
COMPACT_MARK = "tx6_persist_compacted_through"     # meta: every quotes.persist_batch row with id <= this has been examined
COMPACT_MIN_PAYLOAD = 512        # a payload this short is already a summary (or tiny): left as it is
VACUUM_MIN_FREE_BYTES = 8 * 1024 * 1024            # VACUUM only when the free pages are >= 8 MB ...
VACUUM_MIN_FREE_SHARE = 0.25     # ... and >= a quarter of the file
VACUUM_MAX_LIVE_BYTES = 256 * 1024 * 1024          # the transient copy is built in memory (temp_store=MEMORY): never above this
                                                   # (measured ~30 ms per live MB: 48.7 MB in 1.45 s, so <= ~8 s at the cap)


def _rank(priority: str) -> int:
    return PRIORITY_RANK.get(priority, UNKNOWN_RANK)


def _observed(rec: Optional[QuoteRecord]) -> bool:
    """A record with a price observed at some point (a restored or failing record still counts: it has a price)."""
    return rec is not None and (rec.last is not None or rec.mid is not None)


# ---- record builders -----------------------------------------------------------------------------------------------------------
def record_from_intraday(inst: Instrument, iq: YahooIntraday, method: str = "direct", source: Optional[str] = None) -> QuoteRecord:
    """A Yahoo ``QuoteRecord`` for ``inst`` (prices as quoted; FX pairs keep the market convention, e.g. USDJPY 158.10)."""
    bid = iq.meta.get("bid") if isinstance(iq.meta, dict) else None
    ask = iq.meta.get("ask") if isinstance(iq.meta, dict) else None
    bid_d = None if bid is None else qprice(bid)
    ask_d = None if ask is None else qprice(ask)
    mid = qprice((bid_d + ask_d) / 2) if (bid_d is not None and ask_d is not None and bid_d > 0 and ask_d >= bid_d) else None
    return QuoteRecord(instrument_id=inst.instrument_id, provider="yahoo", source=source or f"Yahoo Finance chart ({iq.symbol})", method=method,
                       last=iq.last, bid=bid_d, ask=ask_d, bid_size=None, ask_size=None, mid=mid, prev_close=iq.prev_close, day_high=iq.day_high,
                       day_low=iq.day_low, day_volume=iq.day_volume, quote_time=iq.quote_time, fetched_at=int(iq.fetched_at), delay_s=YAHOO_DELAY_S,
                       delay_label=YAHOO_DELAY_LABEL, currency=inst.currency, session_group=inst.session_group, exchange_tz=iq.exchange_tz,
                       provider_session=iq.provider_session, bar=iq.bar, bars_tail=list(iq.bars_tail[-5:]), error=None, inputs=None, quality="unavailable")


def record_from_chain_row(inst: Instrument, snap: ChainSnapshot, row) -> QuoteRecord:
    """A Cboe ``QuoteRecord`` (method ``chain``) for a materialised option; ``quote_time`` is the UNDERLYING's last trade time.
    ``last`` is the contract's own last trade as Cboe reports it (its time in ``inputs.last_time``; it can be months old and
    is display data only — no order decision reads it, EXEC-1); Cboe's ``last_trade_price`` 0 means the contract never
    traded, so it is recorded as no last (None), never as a price of 0."""
    last = row.last if row.last is not None and row.last > 0 else None
    inputs = {"iv": row.iv, "delta": row.delta, "gamma": row.gamma, "theta": row.theta, "vega": row.vega, "oi": row.oi, "volume": row.volume,
              "last": None if last is None else str(last), "last_time": row.last_time if last is not None else None,
              "underlying_last": None if snap.spot is None else str(snap.spot)}
    return QuoteRecord(instrument_id=inst.instrument_id, provider="cboe", source=f"{snap.source} ({snap.cboe_symbol})", method="chain", last=last,
                       bid=row.bid, ask=row.ask, bid_size=row.bid_size, ask_size=row.ask_size, mid=row.mid, prev_close=None, day_high=None, day_low=None,
                       day_volume=row.volume, quote_time=snap.quote_time, fetched_at=int(snap.fetched_at), delay_s=snap.delay_s, delay_label=snap.delay_label,
                       currency=inst.currency, session_group=inst.session_group, exchange_tz="America/New_York", provider_session=None, bar=None, bars_tail=[],
                       error=None, inputs=inputs, quality="unavailable")


def records_from_chain(snap: ChainSnapshot, instruments) -> List[QuoteRecord]:
    """Records for the options of ``snap``'s underlying that exist in the master (positions / orders / watched)."""
    out: List[QuoteRecord] = []
    for inst in instruments.by_underlying(snap.underlying_id):
        if inst.kind != "OPTION" or inst.status != "ACTIVE":
            continue
        row = snap.by_occ(inst.cboe_symbol or inst.instrument_id) or snap.by_occ(inst.instrument_id)
        if row is not None:
            out.append(record_from_chain_row(inst, snap, row))
    return out


def unavailable_record(inst: Instrument, error: str, fetched_at: int, provider: str = "yahoo") -> QuoteRecord:
    """A record with no price: ``unavailable``.  ``record_error`` passes ``fetched_at`` 0 (nothing was ever fetched
    successfully); the attempt time rides as ``last_error_at``."""
    return QuoteRecord(instrument_id=inst.instrument_id, provider=provider, source="", method="direct", fetched_at=int(fetched_at), currency=inst.currency,
                       session_group=inst.session_group, error=str(error)[:200], quality="unavailable")


def _changed(old: Optional[QuoteRecord], new: QuoteRecord) -> bool:
    if old is None:
        return True
    if old.error is not None and new.error is None:
        return True
    for k in ("last", "bid", "ask", "mid", "bid_size", "ask_size", "quote_time"):
        if getattr(old, k) != getattr(new, k):
            return True
    ot, nt = (old.bar.t if old.bar else None), (new.bar.t if new.bar else None)
    return ot != nt


# ---- the quote cache -------------------------------------------------------------------------------------------------------------
class QuoteCache:
    def __init__(self, db=None, instruments=None, sessions=None, clock=None, bus=None, log=None):
        self.db, self.instruments, self.sessions, self.clock, self.bus, self.log = db, instruments, sessions, clock, bus, log
        self._lock = threading.RLock()
        self._recs: Dict[str, QuoteRecord] = {}
        self._pins: Dict[str, Dict[str, Optional[int]]] = {}
        self._next_due: Dict[str, int] = {}
        self._last_attempt: Dict[str, int] = {}       # when each id was last taken / refreshed (a shorter pin reschedules from it)
        self._retry_at: Dict[str, int] = {}
        self._restored: set = set()
        # persistence (TX-6): ids whose record changed since the last persist, ids pruned whose row is still to delete,
        # and since when each unneeded record has been seen unneeded (no live pin, not kept; prune() drops it PRUNE_IDLE_S later)
        self._dirty: set = set()
        self._gone: set = set()
        self._idle_since: Dict[str, int] = {}
        self._persist_lock = threading.Lock()         # one persist at a time (the worker slot vs the shutdown persist)
        self.last_persist_ts: Optional[int] = None
        self._pstats = {"persists": 0, "written": 0, "deleted": 0, "pruned": 0, "last_written": 0, "last_deleted": 0, "last_bytes": 0,
                        "bytes": 0, "last_error": None}
        # BROWSE budget: hand-out times of the rolling minute + a token bucket (tokens in 1/60 units: +BROWSE_PER_MIN per
        # second, one hand-out costs 60, at most BROWSE_BURST hand-outs banked) so the 50 a minute are spread over the minute
        self._browse_seq: Dict[str, int] = {}         # subscription order of BROWSE pins (oldest first among never-observed ids)
        self._browse_free: set = set()                # BROWSE ids whose refresh costs no network request (classified once per pin)
        self._browse_n = 0
        self._browse_log: deque = deque()
        self._browse_tokens = 0                       # filled to the cap on the first BROWSE hand-out (_browse_budget)
        self._browse_refill_ts: Optional[int] = None

    # ---- time / sessions ----------------------------------------------------------------------------------------------
    def _now(self) -> int:
        return int(self.clock.epoch()) if self.clock is not None else 0

    @staticmethod
    def _foreign(rec: QuoteRecord) -> Optional[dict]:
        if rec.provider_session is None:
            return None
        return {"open_ts": rec.provider_session[0], "close_ts": rec.provider_session[1]}

    def session_for(self, rec: QuoteRecord, now: int, memo: Optional[dict] = None):
        """The session state of a record's group at ``now``.  ``memo`` (one call of ``get_many`` / ``snapshot``, one ``now``) keeps each
        state once: by group, and - P8B-09 - by (group, the provider's session window) for a record that carries one (almost every Yahoo
        record does: the 770 US equities of the owner's cache share one window).  ``sessions.state`` depends only on those and ``now``, so
        the answer is the same; without the window in the key every record recomputed its group's window (about 0.3 ms each through the
        tz fallback), which made one Dashboard mark snapshot cost ~240 ms."""
        if self.sessions is None:
            return None
        group = rec.session_group
        ps = rec.provider_session
        try:
            key = group if ps is None else (group, ps[0], ps[1])
            hash(key)
        except (TypeError, IndexError):            # an unreadable provider window: computed, never memoised
            key = None
        if memo is not None and key is not None and key in memo:
            return memo[key]
        try:
            st = self.sessions.state(group, now, foreign=self._foreign(rec))
        except KeyError:
            st = None
        if memo is not None and key is not None:
            memo[key] = st
        return st

    def quality_for(self, rec: QuoteRecord, now: int, memo: Optional[dict] = None) -> str:
        q = freshness(rec, self.session_for(rec, now, memo), now)
        if q in EXECUTABLE_QUALITIES and rec.instrument_id in self._restored:
            return "stale"
        return q

    def _view(self, rec: QuoteRecord, now: int, memo: Optional[dict] = None) -> QuoteRecord:
        out = copy.copy(rec)
        out.quality = self.quality_for(rec, now, memo)
        return out

    # ---- reads ------------------------------------------------------------------------------------------------------
    def get(self, instrument_id: str, at: Optional[int] = None) -> Optional[QuoteRecord]:
        with self._lock:
            rec = self._recs.get(instrument_id)
        if rec is None:
            return None
        return self._view(rec, at if at is not None else self._now())

    def get_many(self, ids: Iterable[str], at: Optional[int] = None) -> Dict[str, QuoteRecord]:
        now = at if at is not None else self._now()
        memo: dict = {}
        with self._lock:
            recs = {i: self._recs[i] for i in ids if i in self._recs}
        return {i: self._view(r, now, memo) for i, r in recs.items()}

    def raw(self, instrument_id: str) -> Optional[QuoteRecord]:
        """The stored object (quality as of its last put); callers must not mutate it."""
        with self._lock:
            return self._recs.get(instrument_id)

    def all_raw(self) -> Dict[str, QuoteRecord]:
        with self._lock:
            return dict(self._recs)

    def snapshot(self, ids: Optional[Iterable[str]] = None, at: Optional[int] = None) -> Dict[str, QuoteRecord]:
        """Deep copies with the quality as of ``at`` (for a command that must not see later updates)."""
        now = at if at is not None else self._now()
        memo: dict = {}
        with self._lock:
            recs = {i: self._recs[i] for i in (ids if ids is not None else self._recs) if i in self._recs}
        out = {}
        for i, r in recs.items():
            c = copy.deepcopy(r)
            c.quality = self.quality_for(r, now, memo)
            out[i] = c
        return out

    def ids(self) -> List[str]:
        with self._lock:
            return list(self._recs)

    def size(self) -> int:
        with self._lock:
            return len(self._recs)

    def is_restored(self, instrument_id: str) -> bool:
        with self._lock:
            return instrument_id in self._restored

    # ---- writes -----------------------------------------------------------------------------------------------------
    def put(self, rec: QuoteRecord, at: Optional[int] = None) -> List[str]:
        if not isinstance(rec, QuoteRecord):
            raise TypeError("put() takes a QuoteRecord")
        now = at if at is not None else self._now()
        rec.quality = self.quality_for(rec, now)
        with self._lock:
            old = self._recs.get(rec.instrument_id)
            self._recs[rec.instrument_id] = rec
            self._restored.discard(rec.instrument_id)
            self._retry_at.pop(rec.instrument_id, None)
            self._dirty.add(rec.instrument_id)          # a new observation: its row (and its observation time) is rewritten
            self._gone.discard(rec.instrument_id)
            self._schedule(rec.instrument_id, now)
            return [rec.instrument_id] if _changed(old, rec) else []

    def record_error(self, inst: Instrument, error: str, status: Optional[int] = None, at: Optional[int] = None, provider: str = "yahoo") -> None:
        """A failed attempt: keep the previous values AND their observation times, attach the error text (status only,
        never a URL) and the attempt time, and reschedule.  ``fetched_at`` / ``quote_time`` stay those of the last
        SUCCESSFUL observation (EXEC-2: stamping the attempt time here kept a failing provider's frozen print "delayed" and
        executable for 20 minutes); the attempt time is ``last_error_at``.  A record never observed is ``unavailable``
        with ``fetched_at`` 0."""
        now = at if at is not None else self._now()
        with self._lock:
            old = self._recs.get(inst.instrument_id)
            if old is None:
                rec = unavailable_record(inst, error, 0, provider)
            else:
                rec = copy.copy(old)
                rec.error = str(error)[:200]
            rec.last_error_at = int(now)               # a QuoteRecord field (EXEC-2); put() replaces the record and clears it
            rec.quality = self.quality_for(rec, now)
            self._recs[inst.instrument_id] = rec
            if old is None or old.error != rec.error:   # a repeat of the same failure only moves the attempt time: not rewritten
                self._dirty.add(inst.instrument_id)
                self._gone.discard(inst.instrument_id)
            if status in (400, 404):
                self._retry_at[inst.instrument_id] = now + RETRY_NOT_FOUND_S
            self._schedule(inst.instrument_id, now)

    def prune(self, keep: Iterable[str] = (), now: Optional[int] = None, idle_s: int = PRUNE_IDLE_S) -> List[str]:
        """Drop the records nobody needs (TX-6).  A record is needed while it has a live pin of any class (ORDER, HELD, PAGE,
        WATCH, BROWSE, PRIVATE_LISTED …) or is in ``keep`` (the caller's held / working / watched / mark-input ids); one that
        has not been needed for ``idle_s`` (first noticed unneeded = the start of its idle time) leaves memory, and its
        ``quote_cache`` row is deleted by the next ``persist()``.  ``idle_s=0`` drops at once.  Returns the dropped ids."""
        keep_set = set(keep)
        now = int(now if now is not None else self._now())
        drop: List[str] = []
        with self._lock:
            for i in list(self._recs):
                p = self._pins.get(i)
                if i in keep_set or (p and any(exp is None or exp > now for exp in p.values())):
                    self._idle_since.pop(i, None)
                    continue
                since = self._idle_since.setdefault(i, now)   # the idle time starts at the first pass that sees it unneeded
                if now - since >= int(idle_s):
                    drop.append(i)
            for i in drop:
                self._recs.pop(i, None)
                self._next_due.pop(i, None)
                self._last_attempt.pop(i, None)
                self._restored.discard(i)
                self._idle_since.pop(i, None)
                self._dirty.discard(i)
                self._gone.add(i)
            for i in [i for i in self._idle_since if i not in self._recs]:
                self._idle_since.pop(i, None)
            self._pstats["pruned"] += len(drop)
        return drop

    # ---- pins and scheduling ------------------------------------------------------------------------------------------
    def subscribe(self, ids: Iterable[str], ttl_s: int = 90, priority: str = "WATCH") -> None:
        now = self._now()
        memo: dict = {}
        with self._lock:
            for i in ids:
                p = self._pins.setdefault(i, {})
                if priority == BROWSE and BROWSE not in p:
                    self._new_browse_pin(i)
                p[priority] = now + int(ttl_s)
                if priority != BROWSE:
                    self._sooner(i, now, memo)

    def pin(self, ids: Iterable[str], priority: str = "HELD") -> None:
        now = self._now()
        memo: dict = {}
        with self._lock:
            for i in ids:
                p = self._pins.setdefault(i, {})
                if priority == BROWSE and BROWSE not in p:
                    self._new_browse_pin(i)
                p[priority] = None
                if priority != BROWSE:
                    self._sooner(i, now, memo)

    def unpin(self, ids: Iterable[str], priority: Optional[str] = None) -> None:
        now = self._now()
        memo: dict = {}
        with self._lock:
            for i in ids:
                if priority is None:
                    self._pins.pop(i, None)
                    self._drop_browse_pin(i)
                else:
                    p = self._pins.get(i)
                    if p is not None:
                        had = priority in p
                        p.pop(priority, None)
                        if priority == BROWSE:
                            self._drop_browse_pin(i)
                        if not p:
                            self._pins.pop(i, None)
                        elif had:
                            self._reschedule(i, now, memo)

    def _new_browse_pin(self, instrument_id: str) -> None:
        """Bookkeeping for a new BROWSE pin: its subscription order, and whether its refresh costs a network request
        (classified once here, never in the per-second ``due()``).  Caller holds the lock."""
        self._browse_n += 1
        self._browse_seq[instrument_id] = self._browse_n
        if self._no_fetch(instrument_id):
            self._browse_free.add(instrument_id)
        else:
            self._browse_free.discard(instrument_id)

    def _drop_browse_pin(self, instrument_id: str) -> None:
        self._browse_seq.pop(instrument_id, None)
        self._browse_free.discard(instrument_id)

    def _reschedule(self, instrument_id: str, now: int, memo: dict) -> None:
        """A pin ended (expired or removed) while others remain: the next refresh follows the remaining pins from the last
        attempt (a name back to BROWSE only waits its 20 minutes, not the page's 30 s).  Caller holds the lock."""
        last = self._last_attempt.get(instrument_id)
        if last is None:
            return
        st = self._session_state(instrument_id, now, memo)
        self._next_due[instrument_id] = last + self.interval_for(instrument_id, now, st)

    def _sooner(self, instrument_id: str, now: int, memo: dict) -> None:
        """A pin was added: when the id's interval is now shorter than the one it was scheduled with (a BROWSE name opened
        on a page, a watched name that gets an order), bring the next refresh forward to its last attempt + the new
        interval; never pushes a refresh later.  Caller holds the lock."""
        last = self._last_attempt.get(instrument_id)
        if last is None:
            return                                  # never attempted: already due (no _next_due entry)
        nd = self._next_due.get(instrument_id)
        st = self._session_state(instrument_id, now, memo)
        sooner = last + self.interval_for(instrument_id, now, st)
        if nd is None or sooner < nd:
            self._next_due[instrument_id] = sooner

    def set_pins(self, priority: str, ids: Iterable[str]) -> Tuple[List[str], List[str]]:
        """Replace the whole set for ``priority`` (HELD / ORDER from the database scan); returns (added, removed)."""
        wanted = set(ids)
        with self._lock:
            have = {i for i, p in self._pins.items() if priority in p}
            added, removed = sorted(wanted - have), sorted(have - wanted)
            self.pin(added, priority)
            self.unpin(removed, priority)
        return added, removed

    def priorities(self, instrument_id: str, now: Optional[int] = None) -> List[str]:
        now = now if now is not None else self._now()
        with self._lock:
            p = self._pins.get(instrument_id) or {}
            return sorted((k for k, exp in p.items() if exp is None or exp > now), key=_rank)

    def pinned(self, priority: Optional[str] = None) -> List[str]:
        with self._lock:
            return sorted(i for i, p in self._pins.items() if priority is None or priority in p)

    def interval_for(self, instrument_id: str, now: Optional[int] = None, session=None) -> int:
        """§8.5: BROWSE 1,200 s when BROWSE is the only pin, whatever the session state; else CLOSED 300 s when the session
        is closed; else the smallest pin interval (WATCH when nothing is pinned)."""
        now = now if now is not None else self._now()
        pr = self.priorities(instrument_id, now)
        if pr and all(p == BROWSE for p in pr):
            return int(QUOTE_INTERVALS[BROWSE])
        if session is not None and not session.open:
            return int(QUOTE_INTERVALS["CLOSED"])
        pr = [p for p in pr if p != BROWSE]
        if not pr:
            return int(QUOTE_INTERVALS["WATCH"])
        return min(int(QUOTE_INTERVALS.get(p, QUOTE_INTERVALS["WATCH"])) for p in pr)

    def _session_state(self, instrument_id: str, now: int, memo: dict):
        rec = self._recs.get(instrument_id)
        if rec is not None:
            return self.session_for(rec, now, memo)
        inst = self.instruments.get(instrument_id) if self.instruments is not None else None
        if inst is None or self.sessions is None:
            return None
        if inst.session_group not in memo:
            try:
                memo[inst.session_group] = self.sessions.state(inst.session_group, now)
            except KeyError:
                memo[inst.session_group] = None
        return memo[inst.session_group]

    def _schedule(self, instrument_id: str, now: int, memo: Optional[dict] = None) -> None:
        st = self._session_state(instrument_id, now, memo if memo is not None else {})
        self._next_due[instrument_id] = now + self.interval_for(instrument_id, now, st)
        self._last_attempt[instrument_id] = int(now)

    def _browse_budget(self, now: int) -> int:
        """How many BROWSE refreshes this call may hand out: the rolling-minute cap (BROWSE_PER_MIN) and the token bucket
        that spreads them over the minute.  Caller holds the lock."""
        log = self._browse_log
        if log and log[-1] > now:                   # the clock moved back (a test, a clock correction): start a fresh window
            log.clear()
            self._browse_refill_ts = None
        while log and log[0] <= now - 60:
            log.popleft()
        cap = max(BROWSE_BURST, -(-int(BROWSE_PER_MIN) // 60)) * 60     # never below one second's worth of the rate
        if self._browse_refill_ts is None or now < self._browse_refill_ts:
            self._browse_tokens = cap
        else:
            self._browse_tokens = min(cap, self._browse_tokens + (now - self._browse_refill_ts) * int(BROWSE_PER_MIN))
        self._browse_refill_ts = now
        return max(0, min(self._browse_tokens // 60, int(BROWSE_PER_MIN) - len(log)))

    def _no_fetch(self, instrument_id: str) -> bool:
        """True when refreshing the id costs no network request: a known non-option without a Yahoo symbol (a bond, a
        private name) only gets the worker's 'no quote source' record, so it never uses the BROWSE budget."""
        inst = self.instruments.get(instrument_id) if self.instruments is not None else None
        return inst is not None and inst.kind != "OPTION" and not getattr(inst, "yahoo_symbol", None)

    def _browse_age_key(self, instrument_id: str):
        """Oldest first: never observed (no priced record) before the oldest successful fetch; then the longest overdue,
        then subscription order."""
        rec = self._recs.get(instrument_id)
        good = int(rec.fetched_at or 0) if rec is not None and (rec.last is not None or rec.mid is not None) else 0
        return (good, self._next_due.get(instrument_id, 0), self._browse_seq.get(instrument_id, 0), instrument_id)

    def due(self, now: Optional[int] = None) -> List[Tuple[str, str]]:
        """``[(instrument_id, best priority)]`` ordered ORDER → HELD → PAGE → WATCH (→ other classes) → BROWSE; expired
        subscriptions are dropped; the ids returned are rescheduled at once (taken = attempted).  Ids whose only pin is
        BROWSE are handed out at most ``BROWSE_PER_MIN`` per rolling minute, spread over the minute, oldest observation
        first; the ones not handed out stay due for a later call.  Every other class is handed out in full."""
        now = now if now is not None else self._now()
        memo: dict = {}
        out: List[Tuple[str, str]] = []
        browse: List[str] = []
        free: List[str] = []
        with self._lock:
            for i in list(self._pins):
                p = self._pins[i]
                ended = False
                for k, exp in list(p.items()):
                    if exp is not None and exp <= now:
                        p.pop(k)
                        ended = True
                        if k == BROWSE:
                            self._drop_browse_pin(i)
                if not p:
                    self._pins.pop(i)
                    continue
                if ended:
                    self._reschedule(i, now, memo)
                if self._retry_at.get(i, 0) > now:
                    continue
                if self._next_due.get(i, 0) <= now:
                    best = self.priorities(i, now)[0]
                    if best != BROWSE:
                        out.append((i, best))
                    elif i in self._browse_free:
                        free.append(i)
                    else:
                        browse.append(i)
            # USX-08: a name never observed (an instrument page / ticket just opened) goes first, ahead of every refresh
            out.sort(key=lambda t: (0 if not _observed(self._recs.get(t[0])) else 1, _rank(t[1]), t[0]))
            out.extend((i, BROWSE) for i in sorted(free))
            if browse:
                n = min(len(browse), self._browse_budget(now))
                if n > 0:
                    taken = heapq.nsmallest(n, browse, key=self._browse_age_key)
                    self._browse_tokens -= 60 * n
                    self._browse_log.extend([now] * n)
                    out.extend((i, BROWSE) for i in taken)
            for i, _ in out:
                self._schedule(i, now, memo)
        return out

    def browse_stats(self, now: Optional[int] = None) -> dict:
        """The BROWSE budget as of ``now``: hand-outs in the rolling minute, BROWSE-only ids pinned and due (the backlog)."""
        now = now if now is not None else self._now()
        with self._lock:
            pinned = due = 0
            for i, p in self._pins.items():
                live = [k for k, exp in p.items() if exp is None or exp > now]
                if live and all(k == BROWSE for k in live):
                    pinned += 1
                    if self._retry_at.get(i, 0) <= now and self._next_due.get(i, 0) <= now:
                        due += 1
            last_minute = sum(1 for t in self._browse_log if now - 60 < t <= now)
        return {"per_min": int(BROWSE_PER_MIN), "interval_s": int(QUOTE_INTERVALS[BROWSE]), "handed_out_last_60s": last_minute, "browse_only_pinned": pinned,
                "due_backlog": due}

    def next_due(self, instrument_id: str) -> Optional[int]:
        with self._lock:
            return self._next_due.get(instrument_id)

    # ---- persistence --------------------------------------------------------------------------------------------------
    def load_persisted(self) -> int:
        if self.db is None:
            return 0
        n = 0
        with self.db.reader() as conn:
            rows = conn.execute("SELECT instrument_id, record_json, updated_ts FROM quote_cache").fetchall()
        with self._lock:
            for r in rows:
                try:
                    rec = QuoteRecord.from_dict(json.loads(r["record_json"]))
                except (ValueError, TypeError, KeyError) as e:
                    if self.log:
                        self.log.warning("quote_cache row %s unreadable: %s", r["instrument_id"], e)
                    continue
                rec.quality = "stale" if rec.last is not None or rec.mid is not None else "unavailable"
                rec.restored = True                       # EXEC-3: never executable until a live fetch replaces it
                self._recs[rec.instrument_id] = rec
                self._restored.add(rec.instrument_id)
                self._gone.discard(rec.instrument_id)
                n += 1
        return n

    def persist(self) -> int:
        """Write the records that changed since the last persist (``_dirty``) and delete the rows of pruned ones (``_gone``)
        in ONE short writer transaction (TX-6): no command row, nothing in the journal, ``updated_ts`` = the record's own
        observation time.  Nothing changed → no transaction at all.  On a failure the ids are queued again (a newer change
        or a prune in the meantime wins) and the error goes to the caller (the slot logs and counts it).  One persist at a
        time, so an older snapshot of a record can never overwrite a newer one.  Returns the rows written."""
        if self.db is None:
            return 0
        with self._persist_lock:
            with self._lock:
                ids = list(self._dirty)
                recs = [self._recs[i] for i in ids if i in self._recs]
                gone = sorted(self._gone)
                self._dirty.clear()
                self._gone.clear()
            if not recs and not gone:
                return 0
            # stored records are never mutated in place (put / record_error replace the object), so this runs unlocked
            rows = [(r.instrument_id, json.dumps(r.to_dict(), separators=(",", ":"), default=str), _observed_ts(r)) for r in recs]
            try:
                with self.db.writer() as tx:
                    if gone:
                        tx.conn.executemany("DELETE FROM quote_cache WHERE instrument_id=?", [(i,) for i in gone])
                    if rows:
                        tx.conn.executemany("INSERT INTO quote_cache(instrument_id, record_json, updated_ts) VALUES (?,?,?) ON CONFLICT(instrument_id)"
                                            " DO UPDATE SET record_json=excluded.record_json, updated_ts=excluded.updated_ts", rows)
            except BaseException as e:
                with self._lock:
                    self._dirty.update(i for i in ids if i in self._recs)
                    self._gone.update(i for i in gone if i not in self._recs)
                    self._pstats["last_error"] = f"{type(e).__name__}: {e}"[:200]
                raise
            nbytes = sum(len(r[1]) for r in rows)
            with self._lock:
                st = self._pstats
                st["persists"] += 1
                st["written"] += len(rows)
                st["deleted"] += len(gone)
                st["bytes"] += nbytes
                st["last_written"], st["last_deleted"], st["last_bytes"], st["last_error"] = len(rows), len(gone), nbytes, None
            self.last_persist_ts = self._now()
            return len(rows)

    def persist_stats(self) -> dict:
        """Counters of ``persist`` / ``prune`` (rows written / deleted / pruned, JSON bytes written) and the pending sets."""
        with self._lock:
            return dict(self._pstats, dirty=len(self._dirty), pending_deletes=len(self._gone), records=len(self._recs), last_persist_ts=self.last_persist_ts)


def _observed_ts(rec: QuoteRecord) -> int:
    """``quote_cache.updated_ts`` = the record's own observation time: its last SUCCESSFUL fetch; a record never fetched
    successfully carries its attempt time (``last_error_at``), else 0 — never the persist time (TX-6)."""
    t = int(rec.fetched_at or 0)
    if t <= 0:
        t = int(getattr(rec, "last_error_at", 0) or 0)
    return t


def register(bus) -> None:
    """Nothing to register since TX-6 (2026-10-21): the quote cache writes ``quote_cache`` directly (``QuoteCache.persist``)
    and never journals records.  The old ``quotes.persist_batch`` kind is retired; its rows in older databases are
    compacted by ``compact_persist_journal``."""
    return None


# ---- the one-time journal compaction (TX-6) ----------------------------------------------------------------------------------------
def _persist_rid_ts(request_id) -> Optional[int]:
    """The epoch second inside an old persist request id ``quotes:persist:<epoch>:<seq>`` (None for any other shape)."""
    parts = str(request_id or "").split(":")
    if len(parts) >= 4 and parts[0] == "quotes" and parts[1] == "persist":
        try:
            return int(parts[2])
        except ValueError:
            return None
    return None


def compact_persist_journal(conn, now_ts: int, path: Optional[str] = None, log=None, vacuum: bool = True) -> dict:
    """The one-time repair of the TX-6 bloat in databases written by older builds; ``Database.open`` runs it on the writer
    connection before any reader, worker or request exists.

    - Every ``quotes.persist_batch`` command row older than ``COMPACT_AGE_S`` (24 h) gets its payload (the whole quote cache
      as it stood) replaced by ``{"compacted": "TX-6", "records": n, "payload_bytes": b, "compacted_ts": t}`` (n from the
      row's own result).  The rows themselves stay — ids, request ids, kinds, results, times and ``payload_sha256`` (the
      hash of the payload that ran) — so the journal stays continuous.  No other command kind and no other table is
      touched, except ``meta[COMPACT_MARK]``: the highest command id examined, so a later start scans only newer rows.
    - Then, when that left at least 8 MB and a quarter of the file free, ``VACUUM`` gives the space back to the disk —
      only when the live data is at most ``VACUUM_MAX_LIVE_BYTES`` (the transient copy is built in memory) and the disk
      has twice the live size free; otherwise SQLite simply reuses the free pages.
    Returns what it did; logs ONE INFO line when it compacted anything."""
    t0 = _time.monotonic()
    out = {"examined": 0, "compacted": 0, "records": 0, "payload_bytes": 0, "left_recent": 0, "vacuum": None, "seconds": 0.0}
    if conn.in_transaction:
        raise RuntimeError("compact_persist_journal needs a connection without an open transaction")
    mark = int(get_meta(conn, COMPACT_MARK, "0") or 0)
    top = int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM commands").fetchone()[0] or 0)
    if top <= mark:
        return out
    cutoff = int(now_ts) - COMPACT_AGE_S
    # kind and request_id sit in each row's first bytes: this scan never reads a payload's overflow pages
    rows = conn.execute("SELECT id, request_id FROM commands WHERE id > ? AND kind = ? ORDER BY id", (mark, COMPACT_KIND)).fetchall()
    new_mark, todo = top, []
    for k, r in enumerate(rows):
        ts = _persist_rid_ts(r[1])
        if ts is None:
            ts = int(conn.execute("SELECT started_ts FROM commands WHERE id=?", (int(r[0]),)).fetchone()[0])
        if ts >= cutoff:                                  # this row and the later ones are less than a day old: a later start
            new_mark = int(r[0]) - 1
            out["left_recent"] = len(rows) - k
            break
        todo.append(int(r[0]))
    out["examined"] = len(todo)
    if not todo and new_mark == mark:
        return out
    conn.execute("BEGIN IMMEDIATE")
    try:
        for cid in todo:
            row = conn.execute("SELECT length(payload_json), result_json FROM commands WHERE id=? AND kind=?", (cid, COMPACT_KIND)).fetchone()
            if row is None or int(row[0] or 0) < COMPACT_MIN_PAYLOAD:
                continue                                  # already a summary (or tiny): nothing to gain
            try:
                n_rec = int((json.loads(row[1] or "{}") or {}).get("rows"))
            except (ValueError, TypeError, AttributeError):
                n_rec = None
            summary = canonical_json({"compacted": "TX-6", "records": n_rec, "payload_bytes": int(row[0]), "compacted_ts": int(now_ts)})
            conn.execute("UPDATE commands SET payload_json=? WHERE id=? AND kind=?", (summary, cid, COMPACT_KIND))
            out["compacted"] += 1
            out["records"] += n_rec or 0
            out["payload_bytes"] += int(row[0])
        if new_mark != mark:
            set_meta(conn, COMPACT_MARK, str(new_mark))
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except Exception:   # noqa: BLE001 — the original error is the one that matters
            pass
        raise
    if vacuum and out["compacted"]:
        out["vacuum"] = _vacuum_if_worth_it(conn, path)
    out["seconds"] = round(_time.monotonic() - t0, 2)
    if log is not None and out["compacted"]:
        v = out["vacuum"] or {}
        if v.get("ran"):
            vtxt = f"VACUUM {v['before_bytes'] / 1048576:.1f} MB -> {v['after_bytes'] / 1048576:.1f} MB in {v['seconds']:.2f} s"
        else:
            vtxt = f"no VACUUM ({v.get('skipped') or 'not requested'}; SQLite reuses the {v.get('free_bytes', 0) / 1048576:.1f} MB of free pages)"
        log.info("TX-6 startup compaction: %d quotes.persist_batch payloads older than 24 h (%d quote records, %.1f MB) replaced by a record count, "
                 "the command rows kept; %d newer rows left for a later start; %s; %.2f s in all",
                 out["compacted"], out["records"], out["payload_bytes"] / 1048576, out["left_recent"], vtxt, out["seconds"])
    return out


def _vacuum_if_worth_it(conn, path: Optional[str]) -> dict:
    """``VACUUM`` + a truncating WAL checkpoint when the free pages are worth it and it is safe (see ``compact_persist_journal``)."""
    ps = int(conn.execute("PRAGMA page_size").fetchone()[0])
    file_b = int(conn.execute("PRAGMA page_count").fetchone()[0]) * ps
    free_b = int(conn.execute("PRAGMA freelist_count").fetchone()[0]) * ps
    live_b = file_b - free_b
    res = {"ran": False, "before_bytes": file_b, "free_bytes": free_b, "live_bytes": live_b}
    if free_b < VACUUM_MIN_FREE_BYTES or free_b < VACUUM_MIN_FREE_SHARE * file_b:
        res["skipped"] = "little to reclaim"
        return res
    if live_b > VACUUM_MAX_LIVE_BYTES:
        res["skipped"] = f"{live_b / 1048576:.0f} MB of live data is above the {VACUUM_MAX_LIVE_BYTES / 1048576:.0f} MB in-memory limit"
        return res
    try:
        disk_free = shutil.disk_usage(os.path.dirname(os.path.abspath(path))).free if path else None
    except OSError:
        disk_free = None
    if disk_free is None or disk_free < 2 * live_b + 64 * 1048576:
        res["skipped"] = "not enough free disk space" if disk_free is not None else "free disk space unknown"
        return res
    t = _time.monotonic()
    try:
        conn.execute("VACUUM")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception as e:   # noqa: BLE001 — VACUUM is all-or-nothing: on a failure the file is as it was (the free pages get reused)
        res["skipped"] = f"VACUUM failed: {type(e).__name__}: {e}"[:200]
        return res
    res.update(ran=True, seconds=round(_time.monotonic() - t, 2), after_bytes=int(conn.execute("PRAGMA page_count").fetchone()[0]) * ps)
    return res


# ---- the chain cache ----------------------------------------------------------------------------------------------------------------
class ChainCache:
    def __init__(self, clock=None, max_entries: int = 2, log=None):
        self.clock, self.log = clock, log
        self.max_entries = int(max_entries)
        self._lock = threading.RLock()
        self._snaps: "OrderedDict[str, ChainSnapshot]" = OrderedDict()
        self._futures: Dict[str, Future] = {}
        self._pending: List[str] = []
        self._in_flight: Optional[str] = None
        self._screen: Dict[str, int] = {}
        self._pins: set = set()
        self._next_due: Dict[str, int] = {}
        self._errors: Dict[str, str] = {}

    def _now(self) -> int:
        return int(self.clock.epoch()) if self.clock is not None else 0

    def get(self, underlying_id: str, max_age_s: int = 90) -> Optional[ChainSnapshot]:
        with self._lock:
            snap = self._snaps.get(underlying_id)
            if snap is None or snap.fetched_at < self._now() - int(max_age_s):
                return None
            self._snaps.move_to_end(underlying_id)
            return snap

    def peek(self, underlying_id: str) -> Optional[ChainSnapshot]:
        """The last snapshot whatever its age (valuation keeps the last mark)."""
        with self._lock:
            return self._snaps.get(underlying_id)

    def underlying_quote(self, underlying_id: str, max_age_s: Optional[int] = None) -> Optional[QuoteRecord]:
        """EXEC-7: the underlying's Cboe bid / ask / sizes / last from a FRESH cached chain snapshot (fetched within
        ``max_age_s``, default the CHAIN refresh interval), as a ``QuoteRecord`` with method ``chain_underlying``; None when
        no fresh snapshot is cached or it carries no usable bid / ask.  ``quote_time`` is the underlying's last trade time
        (the chain observation time when Cboe gives none) and ``fetched_at`` the chain fetch, so ``is_executable`` judges it
        like any delayed print.  Never fetches."""
        snap = self.get(underlying_id, max_age_s=int(max_age_s if max_age_s is not None else QUOTE_INTERVALS["CHAIN"]))
        if snap is None:
            return None
        u = snap.underlying
        bid, ask = getattr(u, "bid", None), getattr(u, "ask", None)
        if bid is None or ask is None or bid <= 0 or ask < bid:
            return None
        qt = getattr(u, "last_trade_time", None) or snap.quote_time
        return QuoteRecord(instrument_id=underlying_id, provider="cboe", source=f"{snap.source} ({snap.cboe_symbol}) underlying", method="chain_underlying",
                           last=getattr(u, "last", None), bid=bid, ask=ask, bid_size=getattr(u, "bid_size", None), ask_size=getattr(u, "ask_size", None),
                           mid=qprice((bid + ask) / 2), prev_close=getattr(u, "prev_close", None), quote_time=None if qt is None else int(qt),
                           fetched_at=int(snap.fetched_at), delay_s=snap.delay_s, delay_label=snap.delay_label, quality="delayed")

    def last_error(self, underlying_id: str) -> Optional[str]:
        with self._lock:
            return self._errors.get(underlying_id)

    def request(self, underlying_id: str, max_age_s: int = 90) -> Future:
        """One in-flight fetch per underlying shared by every requester; a fresh snapshot answers at once."""
        with self._lock:
            snap = self._snaps.get(underlying_id)
            if snap is not None and snap.fetched_at >= self._now() - int(max_age_s):
                f: Future = Future()
                f.set_result(snap)
                return f
            f = self._futures.get(underlying_id)
            if f is None:
                f = Future()
                self._futures[underlying_id] = f
                if underlying_id not in self._pending and self._in_flight != underlying_id:
                    self._pending.append(underlying_id)
            return f

    def pending(self) -> List[str]:
        with self._lock:
            return list(self._pending)

    def take_pending(self) -> Optional[str]:
        """The next underlying to fetch (one at a time: None while one is in flight)."""
        with self._lock:
            if self._in_flight is not None or not self._pending:
                return None
            uid = self._pending.pop(0)
            self._in_flight = uid
            return uid

    def put(self, snap: ChainSnapshot) -> None:
        with self._lock:
            uid = snap.underlying_id
            self._snaps[uid] = snap
            self._snaps.move_to_end(uid)
            while len(self._snaps) > self.max_entries:
                self._snaps.popitem(last=False)
            self._errors.pop(uid, None)
            self._next_due[uid] = int(snap.fetched_at) + int(QUOTE_INTERVALS["CHAIN"])
            if self._in_flight == uid:
                self._in_flight = None
            f = self._futures.pop(uid, None)
        if f is not None and not f.done():
            f.set_result(snap)

    def fail(self, underlying_id: str, exc: Exception) -> None:
        with self._lock:
            self._errors[underlying_id] = str(exc)[:200]
            self._next_due[underlying_id] = self._now() + int(QUOTE_INTERVALS["CHAIN"])
            if self._in_flight == underlying_id:
                self._in_flight = None
            f = self._futures.pop(underlying_id, None)
        if f is not None and not f.done():
            f.set_exception(exc)

    def keep_on_screen(self, underlying_id: str, ttl_s: int = 90) -> None:
        with self._lock:
            self._screen[underlying_id] = self._now() + int(ttl_s)

    def pin(self, underlying_id: str) -> None:
        with self._lock:
            self._pins.add(underlying_id)

    def unpin(self, underlying_id: str) -> None:
        with self._lock:
            self._pins.discard(underlying_id)

    def set_pins(self, ids: Iterable[str]) -> None:
        with self._lock:
            self._pins = set(ids)

    def due(self, now: Optional[int] = None) -> List[str]:
        """Underlyings whose chain is on screen or pinned and older than CHAIN (90 s); each is queued once."""
        now = now if now is not None else self._now()
        out: List[str] = []
        with self._lock:
            for uid, exp in list(self._screen.items()):
                if exp <= now:
                    self._screen.pop(uid)
            active = set(self._screen) | set(self._pins)
            for uid in sorted(active):
                if self._next_due.get(uid, 0) <= now and uid not in self._pending and self._in_flight != uid:
                    out.append(uid)
                    self._next_due[uid] = now + int(QUOTE_INTERVALS["CHAIN"])
        for uid in out:
            self.request(uid, max_age_s=0)
        return out

    def stats(self) -> dict:
        with self._lock:
            return {"cached": list(self._snaps), "pending": list(self._pending), "in_flight": self._in_flight, "pinned": sorted(self._pins),
                    "on_screen": sorted(self._screen), "errors": dict(self._errors)}


__all__ = ["QuoteCache", "ChainCache", "record_from_intraday", "record_from_chain_row", "records_from_chain", "unavailable_record", "register",
           "compact_persist_journal", "PRIORITY_RANK", "UNKNOWN_RANK", "BROWSE", "BROWSE_BURST", "RETRY_NOT_FOUND_S", "PRUNE_IDLE_S", "COMPACT_KIND",
           "COMPACT_AGE_S", "COMPACT_MARK", "Decimal"]
