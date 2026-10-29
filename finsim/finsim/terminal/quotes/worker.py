"""QuoteWorker (scheduler + fetch pool), RatesRefresher slots and the worker's light commands (TERMINAL_DESIGN.md §8.5).

Conventions
- One scheduler thread ticks every second: due ids (``QuoteCache.due``) → fetched on a small pool (2 threads; inline
  when ``fetch_threads=0``, which is how tests drive ``tick()`` with a FakeClock) → ``cache.put`` → the matcher hook
  ``matcher.on_quotes(changed_ids)`` on the worker thread.  The writer lock is never held during network I/O: every
  database write goes through a light command on the bus after the fetch.
- Pins: every 30 s the worker scans the database — HELD = instruments with a position in an OPEN lifetime (plus the
  underlyings of OPEN TRS contracts and the USD pairs of OPEN cross-currency swaps, ``instruments.otc_reference_ids``), ORDER =
  instruments with a WORKING / PARTIAL / WAITING_PARENT order — and keeps the option underlyings of those on the
  chain cache's pin list (CHAIN 90 s).  Chains are fetched ONE at a time (single-flight) and shared by every
  requester; a materialised option gets its ``chain`` record from each snapshot.
- A failed Cboe chain fetch is attached to every cached option record of that underlying (``record_error``, EXEC-2): their
  ``fetched_at`` stays the last good chain, so they go stale 60 s after it (ERROR_AGE_S) and executions pause - the chain's own
  failure (``ChainCache.fail``) never reached the option records before.
- A FUTURE whose per-contract symbol Yahoo rejects (404) falls back to the continuous ``<root>=F`` ONLY for the front
  month (method ``front_month_continuous``, source "continuous front month (reference)"); deferred months stay
  ``unavailable`` with the error (never priced off the front).
- ``RatesRefresher`` slots (registered callables, each with its own interval or "daily after HH:MM ET" rule and a run
  at startup): the Treasury par curve (daily after 18:00 ET), FRED (every 6 h), FINRA short volume (after 18:00 ET),
  Frankfurter (daily), dividend / split events for held names and TRS underlyings (daily after 18:00 ET; ``dividends.upsert``) and
  the 60 s persist of the quote cache and provider health.  (The news fetchers left with the news feature in 1.0.5 / 1.0.6: no news
  slot exists.)
- Progress heartbeat (V104R2-BW-10): ``stats()["last_progress_ts"]`` moves after every unit of work inside a tick - the pin scan,
  each fetch result, the chain fetch, each refresher slot and each name / series inside the long slots (dividend events, FRED) - while
  ``last_tick_ts`` moves only when a whole tick ends.  A first tick after a start runs every startup slot one after another (on the
  owner's book about a minute of network work); health and the supervisor call the worker stalled only when it has made NO progress
  for more than 3 cycles (``max(last_tick_ts, last_progress_ts)``), so a busy worker is never reported as stalled, a stuck one still is.
  The persist slot (TX-6): ``keep_ids()`` (held / working / borrowed / watch-listed names, the inputs of open positions'
  marks) -> ``cache.prune(keep)`` (records nobody pinned or kept for 1 h leave memory and ``quote_cache``) ->
  ``cache.persist()`` (only the changed records, straight into ``quote_cache``, never into the command journal) ->
  ``provider_health.update`` at once on any status change, else at most every 15 min (it is journaled like every command).
  A slot failure is logged, counted and visible in ``stats()``; provider errors land in ``ProviderHealth`` and, via
  ``provider_health.update``, in the ``provider_health`` table and ONE open WARN attention per provider (auto-resolved
  on the next success).  Nothing is retried in a tight loop and nothing is ever invented when a fetch fails.
- Light commands here: ``provider_health.update``, ``dividends.upsert``, ``instruments.upsert`` (resolve by symbol via
  ``InstrumentMaster.upsert_from_yahoo_meta``), ``instruments.set_adv``, ``finra.record``.  ``register_all(bus)`` registers
  every WP-01 handler.
- ADV (hotfix 1.0.3.1, 2026-10-21, BORROW_AVAIL): a name resolved on demand has no ADV in its spec, so it could never be borrowed.
  After a resolve, and whenever ``lending.locate`` meets such a name (``request_adv``, never on the request thread), the worker
  fetches 3 months of Yahoo daily bars, computes ``instruments.adv_from_daily`` and stores it with its basis through
  ``instruments.set_adv`` (one fetch per name per ``ADV_RETRY_S`` when Yahoo gives no volume: no data, no borrow).
- FUNDS_WAIT orders (SUSPENDED waiting for funds, hotfix 1.0.3.1) are pinned like working ones, so the automatic resume re-checks
  them at a current price.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time as _time
from collections import OrderedDict, deque
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import time as dtime
from typing import Callable, Dict, List, Optional, Tuple

from ..clock import ny_of, ny_str
from ..commands import Command, RejectError
from ..config import QUOTE_INTERVALS
from ..db import canonical_json
from ..instruments import ADV_DAYS, EQUITY_KINDS, Instrument, adv_from_daily, fx_pair_id, needs_adv, otc_reference_ids, parse_futures_id
from . import cache as _cache_mod, rates as _rates_mod
from .cache import ChainCache, QuoteCache, record_from_intraday, records_from_chain
from .http import FetchError
from .parsers import chart_bars, parse_intraday
from .providers import YAHOO_DELAY_LABEL, ProviderSet
from .rates import FRED_SERIES, SHIFT_SYMBOLS

PIN_SCAN_S = 30
PERSIST_S = 60
HEALTH_REFRESH_S = 900           # provider_health.update when only the success counters moved: at most every 15 min (TX-6)
HEALTH_VOLATILE = ("last_ok_ts", "requests_today")     # the fields every successful request moves
FETCH_WAIT_S = 15.0
RESOLVE_MAX = 50
SLOT_RETRY_S = 3600
ADV_RETRY_S = 600                # one Yahoo daily-bars fetch per name per 10 min when it gave no ADV (the ticket re-previews every 5 s)
ADV_MAX = 50
SPLIT_CODE = "SPLIT_MANUAL_REVIEW"
PROVIDER_LABEL = {"yahoo": "Yahoo", "cboe": "Cboe", "treasury": "Treasury.gov", "fred": "FRED", "finra": "FINRA", "frankfurter": "Frankfurter"}
# C14 (Build 2): product refreshers and the chart's full-session bars
REFRESH_BACKOFF_S = (60, 120, 300, 900, 1800, 3600)     # a failing refresher retries after these (1st, 2nd ... failure in a row), then hourly
REFRESHER_NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,40}$")
BARS_MAX_AGE_S = 60              # request_bars answers from memory while the last fetch is younger than this
BARS_KEEP = 12                   # full-session bar snapshots kept in memory (LRU): ~400 small dicts each
BARS_PER_TICK = 2                # bar fetches started per worker tick (the Yahoo rate limiter spaces them further)
INTRADAY_QUERY = "range=1d&interval=1m&includePrePost=false"



def _id_safe(sym: str) -> str:
    """A symbol inside a request id: ``ES=F`` / ``EURUSD=X`` / ``BRK.B`` carry characters the id rule
    ``^[A-Za-z0-9:_.-]{8,80}$`` refuses, so every other character becomes ``_`` (the payload keeps the real symbol)."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(sym))


def _rid(prefix: str, payload: dict) -> str:
    return f"{re.sub(r'[^A-Za-z0-9:_.-]', '_', prefix)}:{hashlib.sha256(canonical_json(payload).encode('utf-8')).hexdigest()[:12]}"


# ---- light commands -----------------------------------------------------------------------------------------------------------
def h_provider_health_update(ctx, payload: dict) -> dict:
    """Light: ``{providers: {name: {last_ok_ts, last_err_ts, last_error, consecutive_errors, requests_today, day, down_until_ts, down,
    refused_ua}}}`` → ``provider_health`` rows; one open WARN attention per provider while it is down or refused, resolved on recovery."""
    n = 0
    for name, r in (payload.get("providers") or {}).items():
        if not isinstance(r, dict):
            continue
        ctx.conn.execute("INSERT OR REPLACE INTO provider_health(provider, last_ok_ts, last_err_ts, last_error, consecutive_errors, requests_today, day, down_until_ts)"
                         " VALUES (?,?,?,?,?,?,?,?)",
                         (str(name), r.get("last_ok_ts"), r.get("last_err_ts"), r.get("last_error"), int(r.get("consecutive_errors") or 0),
                          int(r.get("requests_today") or 0), r.get("day"), r.get("down_until_ts")))
        n += 1
        bad = bool(r.get("down")) or int(r.get("consecutive_errors") or 0) >= 3 or bool(r.get("refused_ua"))
        open_row = ctx.conn.execute("SELECT id FROM attention WHERE source='provider' AND ref_type='provider' AND ref_id=? AND resolved_ts IS NULL", (name,)).fetchone()
        if bad and open_row is None:
            label = PROVIDER_LABEL.get(name, name)
            if r.get("refused_ua"):
                code, msg = "CBOE_REFUSED_UA" if name == "cboe" else "PROVIDER_REFUSED", f"{label} refused the honest User-Agent; options are computed-only (no execution)"
            else:
                since = ny_str(int(r["last_err_ts"])) if r.get("last_err_ts") else "now"
                msg = f"{label} unreachable since {since} — its quotes go stale; executions needing them pause"
                code = "PROVIDER_DOWN"
            ctx.attention("WARN", "provider", code, msg, {"ref_type": "provider", "ref_id": name, "last_error": r.get("last_error"), "auto_resolve": True})
        elif not bad and open_row is not None:
            ctx.conn.execute("UPDATE attention SET resolved_ts=? WHERE id=?", (ctx.ts, int(open_row["id"])))
    return {"rows": n}


def h_dividends_upsert(ctx, payload: dict) -> dict:
    """Light: ``{instrument_id, source, dividends: [[ex_date, amount]], splits: [[date, "4:1"]], held_accounts: [...]}``.
    Yahoo gives the ex-date and the amount only, so ``pay_date`` stays NULL (ex + 15 business days convention, §13.15);
    a split raises WARN ``SPLIT_MANUAL_REVIEW`` for every account holding the name (lots are not adjusted)."""
    iid = str(payload["instrument_id"])
    source = str(payload.get("source") or "Yahoo chart events")
    n = 0
    for ex_date, amount in payload.get("dividends") or []:
        ctx.conn.execute("INSERT INTO dividends(instrument_id, ex_date, amount, pay_date, source, fetched_ts) VALUES (?,?,?,NULL,?,?)"
                         " ON CONFLICT(instrument_id, ex_date) DO UPDATE SET amount=excluded.amount, source=excluded.source, fetched_ts=excluded.fetched_ts",
                         (iid, str(ex_date), str(amount), source, ctx.ts))
        n += 1
    splits = 0
    for d, ratio in payload.get("splits") or []:
        ref = f"{iid}:{d}"
        for aid in payload.get("held_accounts") or []:
            row = ctx.conn.execute("SELECT id FROM attention WHERE code=? AND ref_id=? AND account_id=? AND resolved_ts IS NULL", (SPLIT_CODE, ref, aid)).fetchone()
            if row is None:
                ctx.attention("WARN", "dividends", SPLIT_CODE, f"{iid}: split {ratio} on {d} — lots are not adjusted in this build; review the position manually",
                              {"account_id": aid, "ref_type": "split", "ref_id": ref, "ratio": str(ratio), "date": str(d)})
                splits += 1
    return {"dividends": n, "split_notices": splits}


def h_instruments_upsert(ctx, payload: dict) -> dict:
    """Light: ``{symbol, meta}`` (Yahoo chart meta from the worker) → ``InstrumentMaster.upsert_from_yahoo_meta`` (§7.3)."""
    master = getattr(ctx.services, "instruments", None) if ctx.services is not None else None
    if master is None:
        raise RuntimeError("instruments.upsert needs services.instruments")
    inst = master.upsert_from_yahoo_meta(ctx.tx, str(payload["symbol"]), dict(payload.get("meta") or {}))
    return {"instrument": inst.to_dict()}


def h_instruments_set_adv(ctx, payload: dict) -> dict:
    """Light: ``{instrument_id, adv, sessions, first, last, basis}`` (``instruments.adv_from_daily`` on Yahoo daily bars)
    -> the row's spec ``adv`` / ``adv_basis`` / ``adv_sessions`` / ``adv_from`` / ``adv_to`` / ``adv_asof`` (BORROW_AVAIL).
    A row whose spec already has an ADV (the seeded master) is left as it is."""
    iid = str(payload.get("instrument_id") or "")
    row = ctx.conn.execute("SELECT spec_json FROM instruments WHERE instrument_id=?", (iid,)).fetchone()
    if row is None:
        raise RejectError("INSTRUMENT_UNKNOWN", f"instrument {iid!r} is not in the master")
    try:
        spec = json.loads(row["spec_json"] or "{}")
    except (TypeError, ValueError):
        spec = {}
    spec = spec if isinstance(spec, dict) else {}
    if spec.get("adv") not in (None, "", 0, "0"):
        return {"instrument_id": iid, "adv": spec.get("adv"), "skipped": "the master already has an ADV"}
    try:
        adv = int(payload.get("adv"))
    except (TypeError, ValueError) as e:
        raise RejectError("ADV_INVALID", f"adv must be a whole number of shares, got {payload.get('adv')!r}") from e
    if adv <= 0:
        raise RejectError("ADV_INVALID", "adv must be positive (no data -> no ADV)")
    spec.update({"adv": adv, "adv_basis": str(payload.get("basis") or "ADV from Yahoo daily bars"), "adv_sessions": int(payload.get("sessions") or 0),
                 "adv_from": payload.get("first"), "adv_to": payload.get("last"), "adv_asof": ctx.ts})
    ctx.conn.execute("UPDATE instruments SET spec_json=?, updated_ts=? WHERE instrument_id=?", (json.dumps(spec), ctx.ts, iid))
    master = getattr(ctx.services, "instruments", None) if ctx.services is not None else None
    if master is not None and hasattr(master, "invalidate"):
        master.invalidate([iid])                      # TX-2: re-read after the commit (the worker refreshes the row then)
    return {"instrument_id": iid, "adv": adv, "basis": spec["adv_basis"]}


def h_finra_record(ctx, payload: dict) -> dict:
    """Light: ``{day, rows: [[symbol, short_volume, total_volume]]}`` → ``finra_short``."""
    day = str(payload["day"])
    n = 0
    for sym, sv, tv in payload.get("rows") or []:
        ctx.conn.execute("INSERT OR REPLACE INTO finra_short(symbol, day, short_volume, total_volume) VALUES (?,?,?,?)", (str(sym), day, int(sv), int(tv)))
        n += 1
    return {"rows": n, "day": day}


def register(bus) -> None:
    bus.register("provider_health.update", h_provider_health_update, light=True)
    bus.register("dividends.upsert", h_dividends_upsert, light=True)
    bus.register("instruments.upsert", h_instruments_upsert, light=True)
    bus.register("instruments.set_adv", h_instruments_set_adv, light=True)
    bus.register("finra.record", h_finra_record, light=True)


def register_all(bus) -> None:
    """Every WP-01 light command: rates.record, marks.record, provider_health.update, dividends.upsert, instruments.upsert,
    finra.record (quotes.persist_batch is retired since TX-6: the quote cache writes ``quote_cache`` directly)."""
    _cache_mod.register(bus)
    _rates_mod.register(bus)
    register(bus)


# ---- C14: product refreshers registered from a module's bind(rt) ------------------------------------------------------------------
_REFRESHERS: "OrderedDict[str, tuple]" = OrderedDict()      # name -> (interval_s, fn, run_after_et, label): waiting for the next worker
_REG_LOCK = threading.Lock()


def register_refresher(name: str, interval_s: Optional[int], fn: Callable, run_after_et: Optional[str] = None, label: Optional[str] = None) -> None:
    """C14 (Build 2): a product module's periodic fetch (Treasury prices, ECB / MOF yields, NY Fed SOFR ...), registered from its
    ``bind(rt)``: product modules are bound before the runtime builds its QuoteWorker, and the NEXT worker built takes every
    registration waiting (so a process that builds several runtimes - the tests - never leaks one runtime's refreshers into
    another's worker; each runtime's bind registers again).  A worker that already runs takes a refresher through
    ``QuoteWorker.add_refresher`` (same arguments).

    - ``fn(services, now)`` runs on the QuoteWorker's fetch threads (inline when ``fetch_threads=0``, as tests drive ``tick()``):
      never on a request thread and never inside the writer; it writes through ``services.bus`` like every other light command.
      ``services.providers`` is the FakeProvider set in ``--offline`` mode: a refresher must not touch the network then.
    - Schedule: the first run at the worker's first tick (startup), then every ``interval_s`` seconds; with ``run_after_et``
      ("HH:MM", New York) never before that time of day and at most once per ET day when ``interval_s`` is None.  One run per name
      at a time (a run still in flight is never started twice).
    - Outcome: an exception is an error (``FetchError.status`` kept) recorded in provider health under ``name`` (``/api/health``
      providers, Data Status; three in a row raise the provider attention) and retried with a back-off (1, 2, 5, 15, 30 min, then
      hourly); a normal return records ok; returning ``"retry"`` (published data not out yet) records ok and runs again in an hour.
    - ``label`` names the source in the attention text (default: the name).  Registering a name again replaces it (idempotent)."""
    if not isinstance(name, str) or not REFRESHER_NAME_RE.match(name):
        raise ValueError(f"register_refresher: name {name!r} must match {REFRESHER_NAME_RE.pattern}")
    if not callable(fn):
        raise TypeError("register_refresher: fn must be callable as fn(services, now)")
    if interval_s is not None:
        interval_s = int(interval_s)
        if interval_s <= 0:
            raise ValueError("register_refresher: interval_s must be positive (or None with run_after_et)")
    if run_after_et is not None:
        try:
            hh, mm = [int(x) for x in str(run_after_et).split(":")[:2]]
            dtime(hh, mm)
        except (TypeError, ValueError) as e:
            raise ValueError(f"register_refresher: run_after_et {run_after_et!r} is not HH:MM") from e
    elif interval_s is None:
        raise ValueError("register_refresher: give interval_s, run_after_et or both")
    with _REG_LOCK:
        _REFRESHERS[name] = (interval_s, fn, run_after_et, label)
        if label:
            PROVIDER_LABEL.setdefault(name, str(label))


def unregister_refresher(name: str) -> None:
    """Drop a registration still waiting for a worker (tests; a module that is unbound before the worker is built)."""
    with _REG_LOCK:
        _REFRESHERS.pop(name, None)


def registered_refreshers() -> List[str]:
    """The registrations waiting for the next worker."""
    with _REG_LOCK:
        return list(_REFRESHERS)


def _take_registrations() -> List[tuple]:
    with _REG_LOCK:
        regs = list(_REFRESHERS.items())
        _REFRESHERS.clear()
    return regs


class Refresher:
    """One registered refresher's schedule and outcome counters (``QuoteWorker.stats()["refreshers"]``)."""

    def __init__(self, name: str, interval_s: Optional[int], fn: Callable, run_after_et: Optional[str] = None):
        self.name, self.fn, self.interval_s = name, fn, interval_s
        self.after_et = dtime(*[int(x) for x in run_after_et.split(":")[:2]]) if run_after_et else None
        self.last_run: Optional[int] = None
        self.last_ok_ts: Optional[int] = None
        self.last_error: Optional[str] = None
        self.last_result: Optional[str] = None
        self.retry_at: Optional[int] = None
        self.runs = 0
        self.failures = 0
        self.streak = 0
        self.in_flight = False
        self.last_duration_s: Optional[float] = None

    def due(self, now: int) -> bool:
        if self.in_flight:
            return False
        if self.retry_at is not None:
            return now >= self.retry_at
        if self.last_run is None:
            return True                              # startup run
        if self.after_et is not None:
            ny = ny_of(now)
            if ny.time() < self.after_et:
                return False
            new_day = ny.date() > ny_of(self.last_run).date()
            if self.interval_s is None:
                return new_day
            return new_day or now - self.last_run >= int(self.interval_s)
        return now - self.last_run >= int(self.interval_s or 0)

    def to_dict(self) -> dict:
        return {"name": self.name, "interval_s": self.interval_s, "after_et": None if self.after_et is None else self.after_et.strftime("%H:%M"),
                "last_run": self.last_run, "last_ok_ts": self.last_ok_ts, "last_error": self.last_error, "last_result": self.last_result, "runs": self.runs,
                "failures": self.failures, "failures_in_a_row": self.streak, "retry_at": self.retry_at, "in_flight": self.in_flight,
                "last_duration_s": self.last_duration_s}


# ---- refresher slots --------------------------------------------------------------------------------------------------------------
class Slot:
    def __init__(self, name: str, fn: Callable[[], object], interval_s: Optional[int] = None, after_et: Optional[str] = None, startup: bool = True):
        self.name, self.fn, self.interval_s, self.startup = name, fn, interval_s, startup
        self.after_et = dtime(*[int(x) for x in after_et.split(":")[:2]]) if after_et else None
        self.last_run: Optional[int] = None
        self.retry_at: Optional[int] = None
        self.runs = 0
        self.failures = 0
        self.last_error: Optional[str] = None
        self.last_ok_ts: Optional[int] = None

    def due(self, now: int) -> bool:
        if self.retry_at is not None:
            return now >= self.retry_at
        if self.last_run is None:
            if self.startup:
                return True
            self.last_run = now          # a non-startup slot (persist) first runs one interval after it was first seen
            return False
        if self.after_et is not None:
            ny = ny_of(now)
            return ny.date() > ny_of(self.last_run).date() and ny.time() >= self.after_et
        return self.interval_s is not None and now - self.last_run >= int(self.interval_s)

    def to_dict(self) -> dict:
        return {"name": self.name, "interval_s": self.interval_s, "after_et": None if self.after_et is None else self.after_et.strftime("%H:%M"),
                "last_run": self.last_run, "last_ok_ts": self.last_ok_ts, "runs": self.runs, "failures": self.failures, "last_error": self.last_error,
                "retry_at": self.retry_at}


class RatesRefresher:
    """The slots that are not quotes: curve, FRED, FINRA, Frankfurter, dividend events, persistence."""

    def __init__(self, providers: ProviderSet, cache: QuoteCache, curve, rates, fxstore, instruments, db, bus, clock, cal=None, services=None, log=None,
                 beat: Optional[Callable[[], None]] = None):
        self.providers, self.cache, self.curve, self.rates, self.fxstore = providers, cache, curve, rates, fxstore
        self.instruments, self.db, self.bus, self.clock, self.cal, self.services, self.log = instruments, db, bus, clock, cal, services, log
        self.beat = beat                     # the worker's progress heartbeat (V104R2-BW-10); None for a stand-alone refresher
        self.slots: List[Slot] = []
        self._lock = threading.Lock()
        self._health_sent: Optional[Tuple[str, int]] = None      # (status signature, ts) of the last provider_health.update
        self.add_slot("curve", self._slot_curve, after_et="18:00")
        self.add_slot("fred", self._slot_fred, interval_s=int(QUOTE_INTERVALS.get("FRED", 21600)))
        self.add_slot("finra", self._slot_finra, after_et="18:00")
        self.add_slot("frankfurter", self._slot_frankfurter, interval_s=86400)
        self.add_slot("dividends", self._slot_dividends, after_et="18:00")
        self.add_slot("persist", self._slot_persist, interval_s=PERSIST_S, startup=False)

    def add_slot(self, name: str, fn: Callable[[], object], interval_s: Optional[int] = None, after_et: Optional[str] = None, startup: bool = True) -> Slot:
        slot = Slot(name, fn, interval_s, after_et, startup)
        with self._lock:
            self.slots = [s for s in self.slots if s.name != name] + [slot]
        return slot

    def slot(self, name: str) -> Optional[Slot]:
        with self._lock:
            return next((s for s in self.slots if s.name == name), None)

    def due(self, now: int) -> List[Slot]:
        with self._lock:
            return [s for s in self.slots if s.due(now)]

    def run_slot(self, slot: Slot, now: int) -> bool:
        slot.last_run = now
        slot.retry_at = None
        slot.runs += 1
        try:
            result = slot.fn()
        except Exception as e:   # noqa: BLE001 — surfaced in stats / health, never raised into the loop
            slot.failures += 1
            slot.last_error = f"{type(e).__name__}: {e}"[:200]
            if self.log:
                self.log.warning("refresher slot %s failed: %s", slot.name, slot.last_error)
            slot.retry_at = now + SLOT_RETRY_S if slot.after_et is not None else None
            return False
        slot.last_ok_ts = now
        if result == "retry":
            slot.retry_at = now + SLOT_RETRY_S
        return True

    def _beat(self) -> None:
        if self.beat is not None:
            self.beat()

    def run_due(self, now: int) -> List[str]:
        ran = []
        for s in self.due(now):
            self.run_slot(s, now)
            self._beat()
            ran.append(s.name)
        return ran

    def stats(self) -> dict:
        with self._lock:
            return {s.name: s.to_dict() for s in self.slots}

    # ---- the slots ------------------------------------------------------------------------------------------------------
    def _slot_curve(self):
        c = self.providers.treasury.curve()
        if c is not None:
            self.curve.put(c)
        dfs = getattr(self.providers.treasury, "curve_dfs", lambda: None)()
        if dfs:
            self.curve.set_fixed_dfs(*dfs)
        if c is None and not dfs:
            raise FetchError("no par curve published for this month or the previous one")
        if c is not None and self.cal is not None and c.as_of < self.cal.prev_business_day(self.clock.today()):
            return "retry"

    def _slot_fred(self):
        errors = []
        for sid in FRED_SERIES:
            try:
                obs = self.providers.fred.latest(sid, 60)
            except FetchError as e:
                errors.append(f"{sid}: {e}")
                self._beat()
                continue
            if obs:
                self.rates.put(sid, obs, "FRED")
            self._beat()
        if errors and len(errors) == len(FRED_SERIES):
            raise FetchError("FRED: " + "; ".join(errors)[:160])

    def _finra_day(self):
        today = self.clock.today()
        ny = self.clock.ny()
        if self.cal is None:
            return today
        if self.cal.is_business_day(today) and ny.time() >= dtime(18, 0):
            return today
        return self.cal.prev_business_day(today)

    def _slot_finra(self):
        d = self._finra_day()
        if self.db is not None:
            with self.db.reader() as conn:
                if conn.execute("SELECT 1 FROM finra_short WHERE day=? LIMIT 1", (d.isoformat(),)).fetchone() is not None:
                    return None
        wanted = None
        if self.instruments is not None:
            wanted = {i.symbol.replace("-", ".") for i in self.instruments.all() if i.kind in EQUITY_KINDS}
        rows = self.providers.finra.finra_daily(d, wanted)
        if rows is None:
            if self.log:
                self.log.info("FINRA short volume for %s not published yet", d.isoformat())
            return "retry"
        items = [[sym.replace(".", "-"), sv, tv] for sym, (sv, tv) in sorted(rows.items())]
        if self.bus is not None:
            for k in range(0, len(items), 2000):
                payload = {"day": d.isoformat(), "rows": items[k:k + 2000]}
                res = self.bus.execute(Command("finra.record", payload, _rid("finra:" + d.isoformat(), payload), "worker"))
                if not res.ok:
                    raise RuntimeError(f"finra.record failed: {res.code}")
        elif self.db is not None:
            with self.db.writer() as tx:
                for sym, sv, tv in items:
                    tx.conn.execute("INSERT OR REPLACE INTO finra_short(symbol, day, short_volume, total_volume) VALUES (?,?,?,?)", (sym, d.isoformat(), sv, tv))
        return None

    def _slot_frankfurter(self):
        fx = self.providers.frankfurter.fx_latest()
        self.fxstore.put(fx)

    def held_names(self) -> Dict[str, List[str]]:
        """``{instrument_id: [account_ids]}`` of positions in OPEN lifetimes, plus the underlyings of OPEN TRS contracts (a
        TRS receiver is owed the dividends of a name it does not hold: ``instruments.otc_reference_ids``)."""
        if self.db is None:
            return {}
        out: Dict[str, List[str]] = {}
        with self.db.reader() as conn:
            rows = conn.execute("SELECT p.instrument_id AS iid, p.account_id AS aid FROM v_positions p JOIN accounts a ON a.account_id = p.account_id"
                                " JOIN lifetimes l ON l.lifetime_id = a.lifetime_id WHERE l.status='OPEN' AND p.qty != 0 ORDER BY 1, 2").fetchall()
            otc = otc_reference_ids(conn)
        for r in rows:
            out.setdefault(r["iid"], []).append(r["aid"])
        for iid, accounts in otc.items():
            for aid in accounts:
                if aid not in out.setdefault(iid, []):
                    out[iid].append(aid)
        return out

    def _slot_dividends(self):
        held = self.held_names()
        errors = []
        for iid, accounts in held.items():
            inst = self.instruments.get(iid) if self.instruments is not None else None
            if inst is None or inst.kind not in EQUITY_KINDS or not inst.yahoo_symbol:
                continue
            try:
                ev = self.providers.yahoo.events(inst.yahoo_symbol)
            except FetchError as e:
                errors.append(f"{iid}: {e}")
                self._beat()
                continue
            self._beat()
            if not ev.dividends and not ev.splits:
                continue
            payload = {"instrument_id": iid, "source": "Yahoo chart events", "dividends": [[d.ex_date.isoformat(), str(d.amount)] for d in ev.dividends],
                       "splits": [[s.date.isoformat(), s.ratio] for s in ev.splits], "held_accounts": accounts}
            if self.bus is not None:
                res = self.bus.execute(Command("dividends.upsert", payload, _rid(f"div:upsert:{iid}", payload), "worker"))
                if not res.ok:
                    errors.append(f"{iid}: {res.code}")
        if errors and len(errors) == len(held):
            raise FetchError("dividend events: " + "; ".join(errors)[:160])

    def keep_ids(self) -> Optional[set]:
        """The records the quote cache must keep without a pin (TX-6): ``lifecycle_eod.recorded_set`` (held / working /
        borrowed names of OPEN lifetimes, the underlyings of held options, the FX pairs of foreign cash, the TRS / XCCY
        references, the benchmark — what the 00:15 prune keeps too), every watch-listed name, the USD pair of every
        foreign-currency name among them (their marks convert through it) and the Treasury yield records bond marks
        shift with.  None when the database cannot be read: the caller then prunes nothing."""
        if self.db is None:
            return None
        try:
            with self.db.reader() as conn:
                try:
                    from ..lifecycle_eod import recorded_set
                    keep = dict(recorded_set(conn, self.instruments))
                except Exception as e:   # noqa: BLE001 — no lifecycle package / a master without get(id, conn): the direct scan below
                    if self.log:
                        self.log.debug("keep_ids: recorded_set unavailable (%s); direct scan", e)
                    keep = {}
                    for sql in ("SELECT DISTINCT p.instrument_id FROM v_positions p JOIN accounts a ON a.account_id = p.account_id"
                                " JOIN lifetimes l ON l.lifetime_id = a.lifetime_id WHERE l.status='OPEN' AND p.qty != 0",
                                "SELECT DISTINCT instrument_id FROM orders WHERE status IN ('WORKING','PARTIAL','SUSPENDED','WAITING_PARENT')"):
                        keep.update((r[0], None) for r in conn.execute(sql).fetchall())
                    keep.update((i, None) for i in otc_reference_ids(conn))
                ids = set(keep) | {r[0] for r in conn.execute("SELECT DISTINCT instrument_id FROM watchlists").fetchall()}
        except Exception as e:   # noqa: BLE001 — a busy reader pool is not fatal: no prune this round
            if self.log:
                self.log.warning("keep_ids failed (no quote-cache prune this round): %s", e)
            return None
        for iid in list(ids):
            inst = self.instruments.get(iid) if self.instruments is not None else None
            ccy = getattr(inst, "currency", None)
            if ccy and ccy != "USD":
                ids.add(fx_pair_id(ccy))
        ids.update(SHIFT_SYMBOLS)
        return ids

    def _slot_persist(self):
        keep = self.keep_ids()
        if keep is not None:
            self.cache.prune(keep, int(self.clock.epoch()))
        self.cache.persist()
        if self.bus is not None and self.providers.health is not None:
            snap = self.providers.health.snapshot()
            now = int(self.clock.epoch())
            # TX-6: every command keeps its payload in the journal, so a minute in which only the success counters moved
            # (last_ok_ts, requests_today) is not journaled; any status change (an error, a recovery, down / refused, a new
            # day) is sent at once as before, and the counters at least every HEALTH_REFRESH_S
            sig = canonical_json({p: {k: v for k, v in (r or {}).items() if k not in HEALTH_VOLATILE} for p, r in snap.items()})
            sent = self._health_sent
            if sent is not None and sent[0] == sig and 0 <= now - sent[1] < HEALTH_REFRESH_S:
                return
            res = self.bus.execute(Command("provider_health.update", {"providers": snap}, f"health:{now}:{_time.monotonic_ns() % 1000000}", "worker"))
            if not res.ok:
                raise RuntimeError(f"provider_health.update failed: {res.code}")
            self._health_sent = (sig, now)


# ---- the worker -------------------------------------------------------------------------------------------------------------------
class QuoteWorker(threading.Thread):
    def __init__(self, cache: QuoteCache, chains: ChainCache, instruments, providers: ProviderSet, sessions, bus, matcher, clock, log, fetch_threads: int = 2,
                 db=None, services=None, curve=None, rates=None, fxstore=None, cal=None, period_s: float = 1.0):
        super().__init__(name="quote-worker", daemon=True)
        self.cache, self.chains, self.instruments, self.providers, self.sessions = cache, chains, instruments, providers, sessions
        self.bus, self.matcher, self.clock, self.log = bus, matcher, clock, log
        self.db = db if db is not None else getattr(cache, "db", None)
        self.services = services
        self.fetch_threads = int(fetch_threads)
        self.period_s = float(period_s)
        self._stop_event = threading.Event()
        self._pool: Optional[ThreadPoolExecutor] = None
        self._lock = threading.Lock()
        self._changed: List[str] = []
        self._resolve: deque = deque()
        self._resolve_errors: Dict[str, Tuple[str, Optional[int]]] = {}
        self._adv: deque = deque()
        self._adv_tried: Dict[str, int] = {}
        self._adv_errors: Dict[str, str] = {}
        self._last_pin_scan: Optional[int] = None
        self._scanned: Dict[str, set] = {}
        self.refresher = RatesRefresher(providers, cache, curve, rates, fxstore, instruments, self.db, bus, clock, cal, services, log, beat=self._beat)
        self.stats_ = {"ticks": 0, "last_tick_ts": None, "last_progress_ts": None, "fetches": 0, "fetch_errors": 0, "changed": 0, "chains": 0, "chain_errors": 0,
                       "loop_errors": 0, "last_error": None, "matcher_calls": 0, "resolved": 0, "refresher_runs": 0, "refresher_errors": 0, "bars_fetches": 0,
                       "bars_errors": 0, "adv_set": 0}
        # C14: registered product refreshers and the chart's full-session bars (request_bars)
        self._refreshers: Dict[str, Refresher] = {}
        self._bars: "OrderedDict[str, dict]" = OrderedDict()
        self._bars_pending: deque = deque()
        self._bars_futures: Dict[str, Future] = {}
        self._bars_inflight: set = set()
        self._bars_errors: Dict[str, str] = {}
        for name, (interval_s, fn, after_et, _label) in _take_registrations():      # this runtime's binds registered them just before
            self._install_refresher(name, interval_s, fn, after_et)

    # ---- public ------------------------------------------------------------------------------------------------------
    def _beat(self) -> None:
        """V104R2-BW-10: one unit of work inside a tick is done (``last_progress_ts``, the clock's time now)."""
        try:
            ts = int(self.clock.epoch())
        except Exception:   # noqa: BLE001 - a heartbeat never breaks the work it reports on
            return
        with self._lock:
            self.stats_["last_progress_ts"] = ts

    def set_matcher(self, matcher) -> None:
        self.matcher = matcher

    def add_slot(self, name: str, fn: Callable[[], object], interval_s: Optional[int] = None, after_et: Optional[str] = None, startup: bool = True) -> Slot:
        return self.refresher.add_slot(name, fn, interval_s, after_et, startup)

    def subscribe(self, ids, ttl_s: int = 90, priority: str = "WATCH") -> None:
        self.cache.subscribe(ids, ttl_s, priority)

    def request_chain(self, underlying_id: str):
        self.chains.keep_on_screen(underlying_id)
        return self.chains.request(underlying_id)

    def request_bars(self, instrument_id: str, max_age_s: int = BARS_MAX_AGE_S) -> Future:
        """C14 / C8: the full-session 1-minute bars of an instrument (the chart), like ``request_chain``: a ``Future`` that resolves to
        the kept snapshot (``bars()``) - at once when the last fetch is younger than ``max_age_s`` - else ONE fetch per instrument is
        queued for the worker (never the request thread) and every requester shares it.  A failed fetch sets the exception (a
        ``FetchError``) on the future and is kept in ``bars_error()``; the same Yahoo answer also refreshes the instrument's quote."""
        iid = str(instrument_id)
        now = int(self.clock.epoch())
        with self._lock:
            snap = self._bars.get(iid)
            if snap is not None and int(snap.get("fetched_at") or 0) >= now - int(max_age_s):
                self._bars.move_to_end(iid)
                f: Future = Future()
                f.set_result(snap)
                return f
            f = self._bars_futures.get(iid)
            if f is None:
                f = Future()
                self._bars_futures[iid] = f
                if iid not in self._bars_pending and iid not in self._bars_inflight:
                    self._bars_pending.append(iid)
            return f

    def bars(self, instrument_id: str) -> Optional[dict]:
        """The last full-session bar snapshot kept for an instrument (whatever its age), else None.  Shape:
        ``{instrument_id, symbol, bars: [{t, o, h, l, c, v, complete}], count, fetched_at, quote_time, provider_session, source,
        delay_label, full_session, note}`` (prices as strings, ``t`` epoch seconds, ``complete`` = the minute had ended at the fetch)."""
        with self._lock:
            return self._bars.get(str(instrument_id))

    def bars_error(self, instrument_id: str) -> Optional[str]:
        with self._lock:
            return self._bars_errors.get(str(instrument_id))

    def refreshers(self) -> Dict[str, dict]:
        """C14: the registered refreshers' schedules and outcomes."""
        with self._lock:
            return {n: r.to_dict() for n, r in self._refreshers.items()}

    def add_refresher(self, name: str, interval_s: Optional[int], fn: Callable, run_after_et: Optional[str] = None, label: Optional[str] = None) -> None:
        """C14 for a worker that already runs: the same arguments and rules as ``register_refresher``, on this worker only."""
        if not isinstance(name, str) or not REFRESHER_NAME_RE.match(name):
            raise ValueError(f"add_refresher: name {name!r} must match {REFRESHER_NAME_RE.pattern}")
        if not callable(fn):
            raise TypeError("add_refresher: fn must be callable as fn(services, now)")
        if interval_s is None and run_after_et is None:
            raise ValueError("add_refresher: give interval_s, run_after_et or both")
        if label:
            PROVIDER_LABEL.setdefault(name, str(label))
        self._install_refresher(name, None if interval_s is None else int(interval_s), fn, run_after_et)

    def remove_refresher(self, name: str) -> None:
        self._drop_refresher(name)

    def _install_refresher(self, name: str, interval_s: Optional[int], fn: Callable, run_after_et: Optional[str]) -> None:
        with self._lock:
            old = self._refreshers.get(name)
            ref = Refresher(name, interval_s, fn, run_after_et)
            if old is not None:                      # a re-registration keeps the schedule it had (no extra startup run)
                ref.last_run, ref.last_ok_ts, ref.runs, ref.failures = old.last_run, old.last_ok_ts, old.runs, old.failures
            self._refreshers[name] = ref

    def _drop_refresher(self, name: str) -> None:
        with self._lock:
            self._refreshers.pop(name, None)

    def resolve(self, symbol: str) -> dict:
        """Queue a Yahoo meta fetch for an unknown ticker (never on the request thread); see ``resolve_status``."""
        sym = str(symbol).strip()
        with self._lock:
            if sym not in self._resolve and len(self._resolve) < RESOLVE_MAX:
                self._resolve.append(sym)
                self._resolve_errors.pop(sym, None)
        return self.resolve_status(sym)

    def resolve_status(self, symbol: str) -> dict:
        with self._lock:
            err = self._resolve_errors.get(symbol)
            queued = symbol in self._resolve
        if err is not None:
            return {"status": "error", "message": err[0], "http_status": err[1]}
        return {"status": "resolving" if queued else "idle"}

    def request_adv(self, instrument_id: str) -> str:
        """Queue a Yahoo daily-bars ADV fetch for a name with none in its spec (``lending.locate``, a resolve); never on the
        request thread.  Returns ``queued`` (now or already), ``tried`` (fetched within ``ADV_RETRY_S`` and Yahoo gave no
        usable volume) or ``full``."""
        iid = str(instrument_id or "").strip()
        now = int(self.clock.epoch())
        with self._lock:
            if iid in self._adv:
                return "queued"
            last = self._adv_tried.get(iid)
            if last is not None and now - last < ADV_RETRY_S:
                return "tried"
            if len(self._adv) >= ADV_MAX:
                return "full"
            self._adv.append(iid)
        return "queued"

    def adv_status(self, instrument_id: str) -> Optional[str]:
        """The last ADV fetch error of a name (None when none)."""
        with self._lock:
            return self._adv_errors.get(str(instrument_id))

    def stats(self) -> dict:
        with self._lock:
            d = dict(self.stats_)
        d.update(alive=self.is_alive(), stopping=self._stop_event.is_set(), cache_size=self.cache.size(), chains=self.chains.stats(), slots=self.refresher.stats(),
                 providers=self.providers.health.snapshot() if self.providers.health is not None else {}, fetch_threads=self.fetch_threads,
                 refreshers=self.refreshers(), bars=self._bars_stats())
        return d

    def _bars_stats(self) -> dict:
        with self._lock:
            return {"kept": list(self._bars), "pending": list(self._bars_pending), "in_flight": sorted(self._bars_inflight), "errors": dict(self._bars_errors)}

    def run(self) -> None:
        if self.fetch_threads > 0 and self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=self.fetch_threads, thread_name_prefix="quote-fetch")
        while not self._stop_event.is_set():
            try:
                self.tick()
            except Exception as e:   # noqa: BLE001 — the loop survives; the failure is counted and logged
                with self._lock:
                    self.stats_["loop_errors"] += 1
                    self.stats_["last_error"] = f"{type(e).__name__}: {e}"[:200]
                if self.log:
                    self.log.exception("quote worker tick failed")
            self._stop_event.wait(self.period_s)

    def stop(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        if self.is_alive():
            self.join(timeout)
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None
        try:
            self.cache.persist()
        except Exception as e:   # noqa: BLE001 — shutdown must not hang on a persist failure; it is logged
            if self.log:
                self.log.error("final quote persist failed: %s", e)

    # ---- one pass ----------------------------------------------------------------------------------------------------
    def tick(self, now: Optional[int] = None) -> dict:
        now = int(now if now is not None else self.clock.epoch())
        report = {"now": now, "fetched": [], "errors": [], "changed": [], "chain": None, "slots": [], "resolved": [], "adv": []}
        if self._last_pin_scan is None or now - self._last_pin_scan >= PIN_SCAN_S:
            self._scan_pins(now)
            self._last_pin_scan = now
            self._beat()
        jobs = []
        chain_requests: List[str] = []
        for iid, _prio in self.cache.due(now):
            inst = self.instruments.get(iid) if self.instruments is not None else None
            if inst is None:
                continue
            if inst.kind == "OPTION":
                if inst.underlying_id:
                    chain_requests.append(inst.underlying_id)
                continue
            if not inst.yahoo_symbol:
                self.cache.record_error(inst, "no quote source for this instrument", None, now)
                report["errors"].append((iid, "no quote source"))
                continue
            jobs.append(inst)
        for uid in dict.fromkeys(chain_requests):
            self.chains.keep_on_screen(uid, ttl_s=int(QUOTE_INTERVALS["CHAIN"]) + 30)
            self.chains.request(uid)
        self._run_fetches(jobs, now, report)
        self.chains.due(now)                      # queues a refresh for every on-screen / pinned chain older than CHAIN (90 s)
        uid = self.chains.take_pending()
        if uid is not None:
            report["chain"] = self._fetch_chain(uid, now)
            self._beat()
        report["bars"] = self._serve_bars()
        self._run_resolves(report)
        self._run_adv(report)
        self._beat()
        report["slots"] = self.refresher.run_due(now)
        report["refreshers"] = self._run_refreshers(now)
        changed = self._drain_changed()
        if changed:
            report["changed"] = changed
            self._notify(changed)
        with self._lock:
            self.stats_["ticks"] += 1
            self.stats_["last_tick_ts"] = now
            self.stats_["changed"] += len(changed)
        return report

    # ---- internals ---------------------------------------------------------------------------------------------------
    def _scan_pins(self, now: int) -> None:
        if self.db is None:
            return
        try:
            with self.db.reader() as conn:
                held = [r[0] for r in conn.execute("SELECT DISTINCT p.instrument_id FROM v_positions p JOIN accounts a ON a.account_id = p.account_id"
                                                   " JOIN lifetimes l ON l.lifetime_id = a.lifetime_id WHERE l.status='OPEN' AND p.qty != 0").fetchall()]
                orders = [r[0] for r in conn.execute("SELECT DISTINCT instrument_id FROM orders WHERE status IN ('WORKING','PARTIAL','WAITING_PARENT')"
                                                     " OR (status='SUSPENDED' AND reason LIKE 'waiting for funds%')").fetchall()]
                # the underlyings of OPEN TRS contracts and the FX pairs of OPEN XCCY contracts are observed like held names: their
                # marks drive SWAP_VM, the NAV overlay and the resets (WP-15 integration)
                held += [i for i in otc_reference_ids(conn) if self.instruments is None or self.instruments.get(i) is not None]
        except Exception as e:   # noqa: BLE001 — a busy reader pool is not fatal; the next scan retries
            if self.log:
                self.log.warning("pin scan failed: %s", e)
            return
        # apply the DIFFERENCE against the previous scan, so pins set by other parties (the API, tests) with the same priority survive
        for prio, found in (("HELD", set(held)), ("ORDER", set(orders))):
            prev = self._scanned.get(prio, set())
            self.cache.pin(sorted(found - prev), prio)
            self.cache.unpin(sorted(prev - found), prio)
            self._scanned[prio] = found
        unders = set()
        for iid in set(held) | set(orders):
            inst = self.instruments.get(iid) if self.instruments is not None else None
            if inst is not None and inst.kind == "OPTION" and inst.underlying_id:
                unders.add(inst.underlying_id)
        self.chains.set_pins(unders)

    def _front_month(self, inst: Instrument) -> bool:
        parsed = parse_futures_id(inst.instrument_id)
        if parsed is None or self.instruments is None:
            return False
        root = parsed[0]
        same = [i for i in self.instruments.all() if i.kind == "FUTURE" and i.status == "ACTIVE" and (i.spec or {}).get("root") == root and i.expiry]
        if not same:
            return False
        return min(same, key=lambda i: i.expiry).instrument_id == inst.instrument_id

    def _fetch_one(self, inst: Instrument, now: int):
        """Runs on the pool (or inline): returns ``(inst, record | None, error | None, status)``."""
        try:
            iq = self.providers.yahoo.intraday(inst.yahoo_symbol)
            return inst, record_from_intraday(inst, iq), None, None
        except FetchError as e:
            if e.status == 404 and inst.kind == "FUTURE" and self._front_month(inst):
                parsed = parse_futures_id(inst.instrument_id)
                try:
                    iq = self.providers.yahoo.intraday(f"{parsed[0]}=F")
                    rec = record_from_intraday(inst, iq, method="front_month_continuous", source=f"Yahoo continuous front month {parsed[0]}=F (reference)")
                    return inst, rec, None, None
                except FetchError as e2:
                    return inst, None, f"{e} / continuous: {e2}", e2.status
            return inst, None, str(e), e.status
        except Exception as e:   # noqa: BLE001 — a parser surprise is an error on that record, never a dead worker
            return inst, None, f"{type(e).__name__}: {e}", None

    def _apply(self, result, now: int, report: dict) -> None:
        inst, rec, err, status = result
        if rec is not None:
            changed = self.cache.put(rec, now)
            with self._lock:
                self.stats_["fetches"] += 1
                self._changed.extend(changed)
            report["fetched"].append(inst.instrument_id)
        else:
            self.cache.record_error(inst, err or "fetch failed", status, now)
            with self._lock:
                self.stats_["fetch_errors"] += 1
            report["errors"].append((inst.instrument_id, err))
        self._beat()

    def _run_fetches(self, jobs: List[Instrument], now: int, report: dict) -> None:
        if not jobs:
            return
        if self._pool is None or self.fetch_threads <= 0:
            for inst in jobs:
                self._apply(self._fetch_one(inst, now), now, report)
            return
        futs = [self._pool.submit(self._fetch_one, inst, now) for inst in jobs]
        deadline = _time.monotonic() + FETCH_WAIT_S
        for f in futs:
            remaining = max(0.0, deadline - _time.monotonic())
            try:
                res = f.result(timeout=remaining)
            except TimeoutError:
                f.add_done_callback(lambda fut: self._late(fut))
                continue
            except Exception as e:   # noqa: BLE001
                with self._lock:
                    self.stats_["fetch_errors"] += 1
                    self.stats_["last_error"] = str(e)[:200]
                continue
            self._apply(res, now, report)

    def _late(self, fut) -> None:
        try:
            res = fut.result()
        except Exception:   # noqa: BLE001
            return
        try:
            self._apply(res, int(self.clock.epoch()), {"fetched": [], "errors": []})
        except Exception as e:   # noqa: BLE001
            if self.log:
                self.log.warning("late fetch apply failed: %s", e)

    def _option_records_failed(self, uid: str, error: str, status: Optional[int], now: int) -> int:
        """EXEC-2: attach a failed chain fetch to every cached option record of the underlying (``record_error`` keeps their last
        good ``fetched_at``), so ERROR_AGE_S makes them stale 60 s after the last good chain and executions pause."""
        if self.instruments is None:
            return 0
        n = 0
        for inst in self.instruments.by_underlying(uid):
            if inst.kind != "OPTION" or self.cache.raw(inst.instrument_id) is None:
                continue
            self.cache.record_error(inst, f"Cboe chain: {error}", None, now, provider="cboe")   # never the 1-hour not-found retry: chains have their own schedule
            n += 1
        return n

    def _fetch_chain(self, uid: str, now: int) -> dict:
        inst = self.instruments.get(uid) if self.instruments is not None else None
        sym = inst.cboe_symbol if inst is not None else None
        if not sym:
            self.chains.fail(uid, FetchError(f"{uid}: no Cboe symbol", status=404, retryable=False))
            self._option_records_failed(uid, "no Cboe symbol", None, now)
            return {"underlying": uid, "ok": False, "error": "no Cboe symbol"}
        try:
            snap = self.providers.cboe.chain(sym, uid)
        except Exception as e:   # noqa: BLE001 — surfaced on the future, in health and in stats
            self.chains.fail(uid, e if isinstance(e, FetchError) else FetchError(f"{type(e).__name__}: {e}", retryable=False))
            n = self._option_records_failed(uid, str(e)[:160], getattr(e, "status", None), now)
            with self._lock:
                self.stats_["chain_errors"] += 1
            return {"underlying": uid, "ok": False, "error": str(e)[:200], "option_records_flagged": n}
        self.chains.put(snap)
        n = 0
        for rec in records_from_chain(snap, self.instruments):
            changed = self.cache.put(rec, now)
            n += 1
            with self._lock:
                self._changed.extend(changed)
        with self._lock:
            self.stats_["chains"] += 1
        return {"underlying": uid, "ok": True, "contracts": snap.n_kept(), "records": n}

    # ---- C14 internals: refreshers and bars --------------------------------------------------------------------------------
    def _run_refreshers(self, now: int) -> List[str]:
        with self._lock:
            due = [r for r in self._refreshers.values() if r.due(now)]
            for r in due:
                r.in_flight = True
        for r in due:
            if self._pool is not None and self.fetch_threads > 0:
                try:
                    self._pool.submit(self._run_refresher, r, now)
                    continue
                except RuntimeError:                 # the pool is shutting down: run inline once
                    pass
            self._run_refresher(r, now)
        return [r.name for r in due]

    def _run_refresher(self, ref: Refresher, now: int) -> bool:
        t0 = _time.monotonic()
        ref.last_run = now
        ref.runs += 1
        health = self.providers.health if self.providers is not None else None
        try:
            result = ref.fn(self.services, now)
        except Exception as e:   # noqa: BLE001 - an error is recorded (health + stats), backed off, never raised into the loop
            ref.failures += 1
            ref.streak += 1
            ref.last_error = (str(e) if isinstance(e, FetchError) else f"{type(e).__name__}: {e}")[:200]
            ref.retry_at = now + REFRESH_BACKOFF_S[min(ref.streak, len(REFRESH_BACKOFF_S)) - 1]
            with self._lock:
                self.stats_["refresher_runs"] += 1
                self.stats_["refresher_errors"] += 1
            if health is not None:
                health.record(ref.name, False, getattr(e, "status", None), ref.last_error)
            if self.log:
                self.log.warning("refresher %s failed (%d in a row; next try %s): %s", ref.name, ref.streak, ny_str(ref.retry_at), ref.last_error)
            return False
        finally:
            ref.in_flight = False
            ref.last_duration_s = round(_time.monotonic() - t0, 2)
        ref.streak = 0
        ref.last_ok_ts = now
        ref.last_error = None
        ref.last_result = None if result is None else str(result)[:200]
        ref.retry_at = now + SLOT_RETRY_S if result == "retry" else None
        with self._lock:
            self.stats_["refresher_runs"] += 1
        if health is not None:
            health.record(ref.name, True, 200)
        return True

    def _serve_bars(self) -> List[str]:
        taken: List[str] = []
        with self._lock:
            while self._bars_pending and len(taken) < BARS_PER_TICK:
                iid = self._bars_pending.popleft()
                self._bars_inflight.add(iid)
                taken.append(iid)
        for iid in taken:
            if self._pool is not None and self.fetch_threads > 0:
                try:
                    self._pool.submit(self._bars_job, iid)
                    continue
                except RuntimeError:
                    pass
            self._bars_job(iid)
        return taken

    def _bars_job(self, iid: str) -> None:
        now = int(self.clock.epoch())
        try:
            snap = self._fetch_bars(iid, now)
        except Exception as e:   # noqa: BLE001 - surfaced on the future and in bars_error / stats
            err = e if isinstance(e, FetchError) else FetchError(f"{type(e).__name__}: {e}", retryable=False)
            with self._lock:
                self._bars_errors[iid] = str(err)[:200]
                self.stats_["bars_errors"] += 1
                f = self._bars_futures.pop(iid, None)
                self._bars_inflight.discard(iid)
            if f is not None and not f.done():
                f.set_exception(err)
            return
        with self._lock:
            self._bars[iid] = snap
            self._bars.move_to_end(iid)
            while len(self._bars) > BARS_KEEP:
                self._bars.popitem(last=False)
            self._bars_errors.pop(iid, None)
            self.stats_["bars_fetches"] += 1
            f = self._bars_futures.pop(iid, None)
            self._bars_inflight.discard(iid)
        if f is not None and not f.done():
            f.set_result(snap)

    def _fetch_bars(self, iid: str, now: int) -> dict:
        """One Yahoo 1-day / 1-minute chart: every minute bar of the session (``YahooChart.intraday_bars`` when the provider has it,
        else the chart itself through ``_chart`` + ``parsers.chart_bars``; a provider with neither - the FakeProvider - gives the
        bars it holds, disclosed as not the full session).  The same answer refreshes the instrument's quote record."""
        inst = self.instruments.get(iid) if self.instruments is not None else None
        if inst is None:
            raise FetchError(f"{iid}: not in the instrument master", status=404, retryable=False)
        if inst.kind == "OPTION" or not inst.yahoo_symbol:
            raise FetchError(f"{iid}: no 1-minute chart source for this instrument", status=404, retryable=False)
        y = self.providers.yahoo
        sym = inst.yahoo_symbol
        full = True
        fn = getattr(y, "intraday_bars", None)
        if callable(fn):
            iq, bars = fn(sym)
        else:
            chart = getattr(y, "_chart", None)
            if callable(chart):
                res = chart(sym, INTRADAY_QUERY)
                iq = parse_intraday(res, sym, int(self.clock.epoch()))
                bars = [b for b in chart_bars(res) if int(b.t) % 60 == 0]      # Yahoo's trailing last-trade point is not a 1-minute bar
            else:
                iq = y.intraday(sym)
                bars, full = list(iq.bars_tail or []), False
        rec = record_from_intraday(inst, iq)
        changed = self.cache.put(rec, now)
        with self._lock:
            self._changed.extend(changed)
        fetched = int(iq.fetched_at or now)
        rows = [{"t": int(b.t), "o": None if b.o is None else str(b.o), "h": None if b.h is None else str(b.h), "l": None if b.l is None else str(b.l),
                 "c": None if b.c is None else str(b.c), "v": b.v, "complete": int(b.t) <= fetched - 60} for b in sorted(bars, key=lambda b: int(b.t))]
        note = "" if full else f"this source keeps only the last {len(rows)} one-minute bars, not the whole session"
        return {"instrument_id": iid, "symbol": sym, "bars": rows, "count": len(rows), "fetched_at": fetched, "quote_time": iq.quote_time,
                "provider_session": None if iq.provider_session is None else list(iq.provider_session), "source": f"Yahoo Finance chart ({sym}), 1-minute bars",
                "delay_label": YAHOO_DELAY_LABEL, "full_session": full, "note": note}

    def _run_resolves(self, report: dict) -> None:
        with self._lock:
            sym = self._resolve.popleft() if self._resolve else None
        if sym is None:
            return
        try:
            meta = self.providers.yahoo.meta(sym)
        except FetchError as e:
            with self._lock:
                self._resolve_errors[sym] = (str(e), e.status)
            return
        if self.bus is None:
            return
        res = self.bus.execute(Command("instruments.upsert", {"symbol": sym, "meta": meta}, f"inst:upsert:{_id_safe(sym)}:{int(self.clock.epoch())}", "worker"))
        if not res.ok:
            with self._lock:
                self._resolve_errors[sym] = (res.message or res.code or "upsert failed", res.http)
            return
        if self.instruments is not None:
            self.instruments.refresh(sym)
            self.instruments.load()
            if needs_adv(self.instruments.get(sym)):
                self.request_adv(sym)                   # BORROW_AVAIL: a resolved equity gets its ADV from Yahoo daily bars right away
        with self._lock:
            self.stats_["resolved"] += 1
        report["resolved"].append(sym)

    def _run_adv(self, report: dict) -> None:
        """One queued ADV per tick (BORROW_AVAIL): 3 months of Yahoo daily bars -> ``adv_from_daily`` -> ``instruments.set_adv``
        (light).  A fetch error or no usable volume stores nothing (no data -> no borrow) and is retried after ADV_RETRY_S."""
        with self._lock:
            iid = self._adv.popleft() if self._adv else None
        if iid is None:
            return
        now = int(self.clock.epoch())
        inst = self.instruments.get(iid) if self.instruments is not None else None
        if not needs_adv(inst):
            return
        with self._lock:
            self._adv_tried[iid] = now
        try:
            bars = self.providers.yahoo.daily(inst.yahoo_symbol, ADV_DAYS)          # range=3mo&interval=1d
        except FetchError as e:
            with self._lock:
                self._adv_errors[iid] = f"Yahoo daily bars: {e}"[:200]
            return
        got = adv_from_daily(bars, ny_of(now).date())
        if got is None:
            with self._lock:
                self._adv_errors[iid] = f"Yahoo daily bars for {inst.yahoo_symbol}: fewer than the minimum completed sessions with a volume (no ADV, no borrow)"
            return
        if self.bus is None:
            return
        res = self.bus.execute(Command("instruments.set_adv", dict(got, instrument_id=iid), f"inst:adv:{_id_safe(iid)}:{now}", "worker"))
        if not res.ok:
            with self._lock:
                self._adv_errors[iid] = f"instruments.set_adv: {res.code} {res.message}"[:200]
            return
        if self.instruments is not None:
            self.instruments.refresh(iid)
        with self._lock:
            self._adv_errors.pop(iid, None)
            self.stats_["adv_set"] += 1
        report["adv"].append(iid)

    def _drain_changed(self) -> List[str]:
        with self._lock:
            out = list(dict.fromkeys(self._changed))
            self._changed = []
        return out

    def _notify(self, changed: List[str]) -> None:
        if self.matcher is None:
            return
        try:
            self.matcher.on_quotes(changed)
            with self._lock:
                self.stats_["matcher_calls"] += 1
        except Exception as e:   # noqa: BLE001 — the matcher's own failures are its problem; the worker records and continues
            with self._lock:
                self.stats_["last_error"] = f"matcher: {e}"[:200]
            if self.log:
                self.log.exception("matcher.on_quotes failed")


__all__ = ["QuoteWorker", "RatesRefresher", "Slot", "Refresher", "register", "register_all", "register_refresher", "unregister_refresher",
           "registered_refreshers", "h_provider_health_update", "h_dividends_upsert", "h_instruments_upsert", "h_instruments_set_adv", "h_finra_record"]
