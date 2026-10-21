"""QuoteWorker (scheduler + fetch pool), RatesRefresher slots and the worker's light commands (TERMINAL_DESIGN.md §8.5).

Conventions
- One scheduler thread ticks every second: due ids (``QuoteCache.due``) → fetched on a small pool (2 threads; inline
  when ``fetch_threads=0``, which is how tests drive ``tick()`` with a FakeClock) → ``cache.put`` → the matcher hook
  ``matcher.on_quotes(changed_ids)`` on the worker thread.  The writer lock is never held during network I/O: every
  database write goes through a light command on the bus after the fetch.
- Pins: every 30 s the worker scans the database — HELD = instruments with a position in an OPEN lifetime, ORDER =
  instruments with a WORKING / PARTIAL / WAITING_PARENT order — and keeps the option underlyings of those on the
  chain cache's pin list (CHAIN 90 s).  Chains are fetched ONE at a time (single-flight) and shared by every
  requester; a materialised option gets its ``chain`` record from each snapshot.
- A FUTURE whose per-contract symbol Yahoo rejects (404) falls back to the continuous ``<root>=F`` ONLY for the front
  month (method ``front_month_continuous``, source "continuous front month (reference)"); deferred months stay
  ``unavailable`` with the error (never priced off the front).
- ``RatesRefresher`` slots (registered callables, each with its own interval or "daily after HH:MM ET" rule and a run
  at startup): the Treasury par curve (daily after 18:00 ET), FRED (every 6 h), FINRA short volume (after 18:00 ET),
  Frankfurter (daily), dividend / split events for held names (daily after 18:00 ET; ``dividends.upsert``), the
  Daily News fetchers when ``services.news`` exists, and the 60 s persist of the quote cache and provider health.
  A slot failure is logged, counted and visible in ``stats()``; provider errors land in ``ProviderHealth`` and, via
  ``provider_health.update``, in the ``provider_health`` table and ONE open WARN attention per provider (auto-resolved
  on the next success).  Nothing is retried in a tight loop and nothing is ever invented when a fetch fails.
- Light commands here: ``provider_health.update``, ``dividends.upsert``, ``instruments.upsert`` (resolve by symbol via
  ``InstrumentMaster.upsert_from_yahoo_meta``), ``finra.record``.  ``register_all(bus)`` registers every WP-01 handler.
"""
from __future__ import annotations

import hashlib
import re
import threading
import time as _time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import time as dtime
from typing import Callable, Dict, List, Optional, Tuple

from ..clock import ny_of, ny_str
from ..commands import Command
from ..config import NEWS_INTERVALS, QUOTE_INTERVALS
from ..db import canonical_json
from ..instruments import EQUITY_KINDS, Instrument, parse_futures_id
from . import cache as _cache_mod, rates as _rates_mod
from .cache import ChainCache, QuoteCache, record_from_intraday, records_from_chain
from .http import FetchError
from .providers import ProviderSet
from .rates import FRED_SERIES

PIN_SCAN_S = 30
PERSIST_S = 60
FETCH_WAIT_S = 15.0
RESOLVE_MAX = 50
SLOT_RETRY_S = 3600
SPLIT_CODE = "SPLIT_MANUAL_REVIEW"
PROVIDER_LABEL = {"yahoo": "Yahoo", "cboe": "Cboe", "treasury": "Treasury.gov", "fred": "FRED", "finra": "FINRA", "frankfurter": "Frankfurter"}



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
    bus.register("finra.record", h_finra_record, light=True)


def register_all(bus) -> None:
    """Every WP-01 light command: quotes.persist_batch, rates.record, marks.record, provider_health.update, dividends.upsert,
    instruments.upsert, finra.record."""
    _cache_mod.register(bus)
    _rates_mod.register(bus)
    register(bus)


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
    """The slots that are not quotes: curve, FRED, FINRA, Frankfurter, dividend events, news, persistence."""

    def __init__(self, providers: ProviderSet, cache: QuoteCache, curve, rates, fxstore, instruments, db, bus, clock, cal=None, services=None, log=None):
        self.providers, self.cache, self.curve, self.rates, self.fxstore = providers, cache, curve, rates, fxstore
        self.instruments, self.db, self.bus, self.clock, self.cal, self.services, self.log = instruments, db, bus, clock, cal, services, log
        self.slots: List[Slot] = []
        self._lock = threading.Lock()
        self.add_slot("curve", self._slot_curve, after_et="18:00")
        self.add_slot("fred", self._slot_fred, interval_s=int(QUOTE_INTERVALS.get("FRED", 21600)))
        self.add_slot("finra", self._slot_finra, after_et="18:00")
        self.add_slot("frankfurter", self._slot_frankfurter, interval_s=86400)
        self.add_slot("dividends", self._slot_dividends, after_et="18:00")
        news = getattr(services, "news", None) if services is not None else None
        if news is not None and hasattr(news, "refresh"):
            for which, every in NEWS_INTERVALS.items():
                self.add_slot(f"news_{which.lower()}", (lambda w=which.lower(): news.refresh(w)), interval_s=int(every))
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

    def run_due(self, now: int) -> List[str]:
        ran = []
        for s in self.due(now):
            self.run_slot(s, now)
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
                continue
            if obs:
                self.rates.put(sid, obs, "FRED")
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
        """``{instrument_id: [account_ids]}`` of positions in OPEN lifetimes."""
        if self.db is None:
            return {}
        out: Dict[str, List[str]] = {}
        with self.db.reader() as conn:
            rows = conn.execute("SELECT p.instrument_id AS iid, p.account_id AS aid FROM v_positions p JOIN accounts a ON a.account_id = p.account_id"
                                " JOIN lifetimes l ON l.lifetime_id = a.lifetime_id WHERE l.status='OPEN' AND p.qty != 0 ORDER BY 1, 2").fetchall()
        for r in rows:
            out.setdefault(r["iid"], []).append(r["aid"])
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
                continue
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

    def _slot_persist(self):
        self.cache.persist()
        if self.bus is not None and self.providers.health is not None:
            snap = self.providers.health.snapshot()
            res = self.bus.execute(Command("provider_health.update", {"providers": snap}, f"health:{int(self.clock.epoch())}:{_time.monotonic_ns() % 1000000}", "worker"))
            if not res.ok:
                raise RuntimeError(f"provider_health.update failed: {res.code}")


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
        self._last_pin_scan: Optional[int] = None
        self._scanned: Dict[str, set] = {}
        self.refresher = RatesRefresher(providers, cache, curve, rates, fxstore, instruments, self.db, bus, clock, cal, services, log)
        self.stats_ = {"ticks": 0, "last_tick_ts": None, "fetches": 0, "fetch_errors": 0, "changed": 0, "chains": 0, "chain_errors": 0, "loop_errors": 0,
                       "last_error": None, "matcher_calls": 0, "resolved": 0}

    # ---- public ------------------------------------------------------------------------------------------------------
    def set_matcher(self, matcher) -> None:
        self.matcher = matcher

    def add_slot(self, name: str, fn: Callable[[], object], interval_s: Optional[int] = None, after_et: Optional[str] = None, startup: bool = True) -> Slot:
        return self.refresher.add_slot(name, fn, interval_s, after_et, startup)

    def subscribe(self, ids, ttl_s: int = 90, priority: str = "WATCH") -> None:
        self.cache.subscribe(ids, ttl_s, priority)

    def request_chain(self, underlying_id: str):
        self.chains.keep_on_screen(underlying_id)
        return self.chains.request(underlying_id)

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

    def stats(self) -> dict:
        with self._lock:
            d = dict(self.stats_)
        d.update(alive=self.is_alive(), stopping=self._stop_event.is_set(), cache_size=self.cache.size(), chains=self.chains.stats(), slots=self.refresher.stats(),
                 providers=self.providers.health.snapshot() if self.providers.health is not None else {}, fetch_threads=self.fetch_threads)
        return d

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
        report = {"now": now, "fetched": [], "errors": [], "changed": [], "chain": None, "slots": [], "resolved": []}
        if self._last_pin_scan is None or now - self._last_pin_scan >= PIN_SCAN_S:
            self._scan_pins(now)
            self._last_pin_scan = now
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
        self._run_resolves(report)
        report["slots"] = self.refresher.run_due(now)
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
                orders = [r[0] for r in conn.execute("SELECT DISTINCT instrument_id FROM orders WHERE status IN ('WORKING','PARTIAL','WAITING_PARENT')").fetchall()]
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

    def _fetch_chain(self, uid: str, now: int) -> dict:
        inst = self.instruments.get(uid) if self.instruments is not None else None
        sym = inst.cboe_symbol if inst is not None else None
        if not sym:
            self.chains.fail(uid, FetchError(f"{uid}: no Cboe symbol", status=404, retryable=False))
            return {"underlying": uid, "ok": False, "error": "no Cboe symbol"}
        try:
            snap = self.providers.cboe.chain(sym, uid)
        except Exception as e:   # noqa: BLE001 — surfaced on the future, in health and in stats
            self.chains.fail(uid, e if isinstance(e, FetchError) else FetchError(f"{type(e).__name__}: {e}", retryable=False))
            with self._lock:
                self.stats_["chain_errors"] += 1
            return {"underlying": uid, "ok": False, "error": str(e)[:200]}
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
        with self._lock:
            self.stats_["resolved"] += 1
        report["resolved"].append(sym)

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


__all__ = ["QuoteWorker", "RatesRefresher", "Slot", "register", "register_all", "h_provider_health_update", "h_dividends_upsert", "h_instruments_upsert",
           "h_finra_record"]
