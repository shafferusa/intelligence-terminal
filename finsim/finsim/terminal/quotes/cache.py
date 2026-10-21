"""QuoteCache, ChainCache, record builders and the ``quotes.persist_batch`` light command (TERMINAL_DESIGN.md §8.5).

Conventions
- A record's ``quality`` is recomputed at READ time with the current session (``freshness()``, §8.2); the stored
  record is never mutated in place — ``put`` replaces the object, readers get a copy, so no thread sees a half-updated
  quote.  ``put`` returns the ids whose price / size / time / bar changed (what the matcher re-evaluates).
- Scheduling (``QUOTE_INTERVALS``): an instrument's refresh interval is the smallest of its pins — ORDER 15 s, HELD
  30 s, PAGE 30 s, WATCH 60 s (UI subscriptions expire 90 s after the last poll) — and CLOSED 300 s whenever its
  session is closed.  A symbol answering 404/400 is ``unavailable`` with the error and retried hourly.  ``due()`` hands
  out each due id once per interval (the attempt is scheduled when taken, so a failing fetch is not retried every
  second).
- Restored records (``load_persisted`` after a restart) come back ``close`` or ``stale``, never ``delayed``, until the
  first live fetch replaces them: the cache downgrades ``delayed`` → ``stale`` for them, so a DAY order can never match
  on a print the process has not observed itself.
- ``ChainCache``: an LRU of two reduced chains with ONE in-flight fetch per underlying shared by every requester
  (single-flight ``Future``); the worker serves pending requests one at a time so the SPX parse never overlaps another.
- Persistence: ``persist()`` writes every record to ``quote_cache`` through the light command ``quotes.persist_batch``
  (or directly when no bus is bound) every 60 s and at shutdown.
"""
from __future__ import annotations

import copy
import json
import threading
from concurrent.futures import Future
from collections import OrderedDict
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Tuple

from ..commands import Command
from ..config import QUOTE_INTERVALS
from ..instruments import Instrument
from ..units import qprice
from . import EXECUTABLE_QUALITIES, QuoteRecord, freshness
from .providers import ChainSnapshot, YAHOO_DELAY_LABEL, YAHOO_DELAY_S, YahooIntraday

PRIORITY_RANK = {"ORDER": 0, "HELD": 1, "PAGE": 2, "WATCH": 3}
RETRY_NOT_FOUND_S = 3600
PERSIST_CHUNK = 500


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
    """A Cboe ``QuoteRecord`` (method ``chain``) for a materialised option; ``quote_time`` is the UNDERLYING's last trade time."""
    inputs = {"iv": row.iv, "delta": row.delta, "gamma": row.gamma, "theta": row.theta, "vega": row.vega, "oi": row.oi, "volume": row.volume,
              "last": None if row.last is None else str(row.last), "last_time": row.last_time, "underlying_last": None if snap.spot is None else str(snap.spot)}
    return QuoteRecord(instrument_id=inst.instrument_id, provider="cboe", source=f"{snap.source} ({snap.cboe_symbol})", method="chain", last=row.last,
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
        self._retry_at: Dict[str, int] = {}
        self._restored: set = set()
        self._persist_seq = 0
        self.last_persist_ts: Optional[int] = None

    # ---- time / sessions ----------------------------------------------------------------------------------------------
    def _now(self) -> int:
        return int(self.clock.epoch()) if self.clock is not None else 0

    @staticmethod
    def _foreign(rec: QuoteRecord) -> Optional[dict]:
        if rec.provider_session is None:
            return None
        return {"open_ts": rec.provider_session[0], "close_ts": rec.provider_session[1]}

    def session_for(self, rec: QuoteRecord, now: int, memo: Optional[dict] = None):
        if self.sessions is None:
            return None
        key = rec.session_group
        if memo is not None and key in memo and rec.provider_session is None:
            return memo[key]
        try:
            st = self.sessions.state(key, now, foreign=self._foreign(rec))
        except KeyError:
            st = None
        if memo is not None and rec.provider_session is None:
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
            self._schedule(rec.instrument_id, now)
            return [rec.instrument_id] if _changed(old, rec) else []

    def record_error(self, inst: Instrument, error: str, status: Optional[int] = None, at: Optional[int] = None, provider: str = "yahoo") -> None:
        """Keep the previous values, attach the error text (status only, never a URL) and reschedule."""
        now = at if at is not None else self._now()
        with self._lock:
            old = self._recs.get(inst.instrument_id)
            if old is None:
                rec = unavailable_record(inst, error, now, provider)
            else:
                rec = copy.copy(old)
                rec.error = str(error)[:200]
                rec.fetched_at = now
            rec.quality = self.quality_for(rec, now)
            self._recs[inst.instrument_id] = rec
            if status in (400, 404):
                self._retry_at[inst.instrument_id] = now + RETRY_NOT_FOUND_S
            self._schedule(inst.instrument_id, now)

    def prune(self, keep: Iterable[str]) -> int:
        keep_set = set(keep)
        with self._lock:
            drop = [i for i in self._recs if i not in keep_set and i not in self._pins]
            for i in drop:
                self._recs.pop(i, None)
                self._next_due.pop(i, None)
                self._restored.discard(i)
            return len(drop)

    # ---- pins and scheduling ------------------------------------------------------------------------------------------
    def subscribe(self, ids: Iterable[str], ttl_s: int = 90, priority: str = "WATCH") -> None:
        now = self._now()
        with self._lock:
            for i in ids:
                self._pins.setdefault(i, {})[priority] = now + int(ttl_s)

    def pin(self, ids: Iterable[str], priority: str = "HELD") -> None:
        with self._lock:
            for i in ids:
                self._pins.setdefault(i, {})[priority] = None

    def unpin(self, ids: Iterable[str], priority: Optional[str] = None) -> None:
        with self._lock:
            for i in ids:
                if priority is None:
                    self._pins.pop(i, None)
                else:
                    p = self._pins.get(i)
                    if p is not None:
                        p.pop(priority, None)
                        if not p:
                            self._pins.pop(i, None)

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
            return sorted((k for k, exp in p.items() if exp is None or exp > now), key=lambda k: PRIORITY_RANK.get(k, 9))

    def pinned(self, priority: Optional[str] = None) -> List[str]:
        with self._lock:
            return sorted(i for i, p in self._pins.items() if priority is None or priority in p)

    def interval_for(self, instrument_id: str, now: Optional[int] = None, session=None) -> int:
        """§8.5: CLOSED 300 s when the session is closed; else the smallest pin interval (WATCH when nothing is pinned)."""
        now = now if now is not None else self._now()
        if session is not None and not session.open:
            return int(QUOTE_INTERVALS["CLOSED"])
        pr = self.priorities(instrument_id, now)
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

    def due(self, now: Optional[int] = None) -> List[Tuple[str, str]]:
        """``[(instrument_id, best priority)]`` ordered ORDER → HELD → PAGE → WATCH; expired subscriptions are dropped;
        the ids returned are rescheduled at once (taken = attempted)."""
        now = now if now is not None else self._now()
        memo: dict = {}
        out: List[Tuple[str, str]] = []
        with self._lock:
            for i in list(self._pins):
                p = self._pins[i]
                for k, exp in list(p.items()):
                    if exp is not None and exp <= now:
                        p.pop(k)
                if not p:
                    self._pins.pop(i)
                    continue
                if self._retry_at.get(i, 0) > now:
                    continue
                if self._next_due.get(i, 0) <= now:
                    out.append((i, self.priorities(i, now)[0]))
            out.sort(key=lambda t: (PRIORITY_RANK.get(t[1], 9), t[0]))
            for i, _ in out:
                self._schedule(i, now, memo)
        return out

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
                self._recs[rec.instrument_id] = rec
                self._restored.add(rec.instrument_id)
                n += 1
        return n

    def persist(self) -> int:
        with self._lock:
            recs = [r.to_dict() for r in self._recs.values()]
        if not recs:
            return 0
        now = self._now()
        if self.bus is not None:
            for k in range(0, len(recs), PERSIST_CHUNK):
                self._persist_seq += 1
                res = self.bus.execute(Command("quotes.persist_batch", {"records": recs[k:k + PERSIST_CHUNK]}, f"quotes:persist:{now}:{self._persist_seq}", "worker"))
                if not res.ok:
                    raise RuntimeError(f"quotes.persist_batch failed: {res.code} {res.message}")
        elif self.db is not None:
            with self.db.writer() as tx:
                _write_records(tx.conn, recs, tx.ts)
        self.last_persist_ts = now
        return len(recs)


def _write_records(conn, records: List[dict], ts: int) -> int:
    n = 0
    for d in records:
        iid = d.get("instrument_id")
        if not iid:
            continue
        conn.execute("INSERT INTO quote_cache(instrument_id, record_json, updated_ts) VALUES (?,?,?)"
                     " ON CONFLICT(instrument_id) DO UPDATE SET record_json=excluded.record_json, updated_ts=excluded.updated_ts",
                     (str(iid), json.dumps(d, separators=(",", ":"), default=str), ts))
        n += 1
    return n


def h_quotes_persist_batch(ctx, payload: dict) -> dict:
    """Light: ``{records: [QuoteRecord.to_dict() …]}`` → ``quote_cache`` upserts."""
    return {"rows": _write_records(ctx.conn, list(payload.get("records") or []), ctx.ts)}


def register(bus) -> None:
    bus.register("quotes.persist_batch", h_quotes_persist_batch, light=True)


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
           "h_quotes_persist_batch", "PRIORITY_RANK", "RETRY_NOT_FOUND_S", "Decimal"]
