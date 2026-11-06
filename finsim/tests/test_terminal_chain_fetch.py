"""1.0.7.2 CHAIN-FETCH-1 (2026-11-04: one serial chain fetcher let the pinned chains pass the 5-minute fetch grace, and a newly requested
chain waited behind every pinned refresh): the quote worker fetches chains on its own pool of ``chain_threads`` threads ("chain-fetch",
``config.CHAIN_FETCH_WORKERS`` = 3), a requested chain ahead of the pinned refreshes, the downloads overlapping while the JSON parses stay one
at a time (``providers._CHAIN_PARSE_LOCK``); with ``fetch_threads=0`` (how tests drive ``tick()``) one chain per tick on the scheduler
thread, as before.

Nothing here touches the network, a port or ``~/.finsim``: ``make_runtime`` (a temp database, FakeClock), a FakeProvider whose ``chain()``
sleeps and records how many run at once, and ``CboeChain`` with an injected getter.  Fictional underlyings (ACME, WIDG, WIDG1 ..) only.
Every worker built here is stopped in ``tearDown`` (and the started ones in ``finally`` too).
"""
from __future__ import annotations

import json
import shutil
import tempfile
import threading
import time
import unittest
from decimal import Decimal
from unittest import mock

from tests.terminal_helpers import make_runtime
from finsim.terminal import config
from finsim.terminal.quotes import providers as _providers
from finsim.terminal.quotes.cache import ChainCache, QuoteCache
from finsim.terminal.quotes.parsers import ChainSnapshot, ChainUnderlying
from finsim.terminal.quotes.providers import CboeChain, FakeProvider, ProviderSet
from finsim.terminal.quotes.rates import CurveStore, FxStore, RateStore
from finsim.terminal.quotes.worker import CHAIN_FETCH_TIMES_KEPT, QuoteWorker, register_all

REQUESTED = "ACME"
PINNED = [f"WIDG{i}" for i in range(1, 7)]               # six pinned underlyings (fictional)
ALL = [REQUESTED] + PINNED
DELAY_S = 0.15


class _Peak:
    """How many callers are inside at once (``peak``) and how many came in (``entered``) - a thread-safe context manager."""

    def __init__(self):
        self._mu = threading.Lock()
        self.active = 0
        self.peak = 0
        self.entered = 0

    def __enter__(self):
        with self._mu:
            self.active += 1
            self.entered += 1
            self.peak = max(self.peak, self.active)
        return self

    def __exit__(self, *exc):
        with self._mu:
            self.active -= 1
        return False


class SlowChains(FakeProvider):
    """A FakeProvider whose ``chain()`` takes ``delay`` seconds and records the order the fetches start in, the threads they run on and how
    many run at once.  ``first_together=n``: the first n fetches wait for each other (at most 10 s) before their delay, so on a busy machine
    they still overlap and the n first started are exactly the n first handed out."""

    def __init__(self, clock, delay: float = DELAY_S, first_together: int = 0):
        super().__init__(clock)
        self.delay = float(delay)
        self.inside = _Peak()
        self._mu = threading.Lock()
        self.started = []
        self.threads = set()
        self.gate = threading.Barrier(first_together) if first_together > 1 else None

    def chain(self, cboe_symbol, underlying_id=None):
        with self._mu:
            self.started.append(cboe_symbol)
            self.threads.add(threading.current_thread())
            nth = len(self.started)
        if self.gate is not None and nth <= self.gate.parties:
            try:
                self.gate.wait(timeout=10.0)
            except threading.BrokenBarrierError:          # fewer arrived: the peak assertion says so
                pass
        with self.inside:
            if self.delay:
                time.sleep(self.delay)
            return super().chain(cboe_symbol, underlying_id)


def _empty_snap(uid: str, fetched_at: int) -> ChainSnapshot:
    return ChainSnapshot(uid, uid, ChainUnderlying(uid, Decimal("100.00"), None, None, None, None, None, None, None), [], int(fetched_at), None, 0)


def _add_underlyings(rt, names) -> None:
    with rt.db.writer() as tx:
        for u in names:
            rt.instruments.upsert(tx, instrument_id=u, kind="EQUITY", symbol=u, name=f"{u} Test Co (fictional)", currency="USD", session_group="US_EQUITY",
                                  account_kinds=["US_EXCHANGE", "PB"], yahoo_symbol=u, cboe_symbol=u, spec={"adv": 500000})
    rt.instruments.load()


def _occ(root: str, right: str, strike: int) -> str:
    return f"{root}261218{right}{strike * 1000:08d}"


def _wait(cond, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-chainfetch-")
        self.rt = make_runtime(self.tmp)                                       # Tue 2026-10-20 14:00 ET (FakeClock: time does not move)
        register_all(self.rt.bus)
        self.workers = []

    def tearDown(self):
        try:
            for w in self.workers:
                w.stop(timeout=5.0)
        finally:
            self.rt.close()
            shutil.rmtree(self.tmp, ignore_errors=True)

    def worker(self, fp, **kw) -> QuoteWorker:
        rt = self.rt
        self.cache = QuoteCache(rt.db, rt.instruments, rt.sessions, rt.clock, rt.bus, rt.log)
        self.chains = ChainCache(rt.clock, max_entries=24)
        w = QuoteWorker(self.cache, self.chains, rt.instruments, ProviderSet.offline_set(fp, rt.clock), rt.sessions, rt.bus, None, rt.clock, rt.log,
                        db=rt.db, services=rt, curve=CurveStore(rt.db, rt.bus, rt.clock), rates=RateStore(rt.db, rt.bus), fxstore=FxStore(rt.db, rt.bus),
                        cal=rt.cal, **kw)
        w._last_pin_scan = int(rt.clock.epoch())       # no option is held in this database: the pin scan must not replace the test's pins
        self.workers.append(w)
        return w

    def chains_for(self, fp, names) -> None:
        now = int(self.rt.clock.epoch())
        for u in names:
            fp.set_chain(u, _empty_snap(u, now))


class PoolModeTests(_Base):
    def test_a_requested_chain_starts_among_the_first_three_and_all_seven_land(self):
        """6 pinned chains are queued for their refresh, then a ticket asks for ACME: with 3 chain threads the requested chain is among the first
        three fetches started (1.0.7.1 fetched it 7th, one per tick), every chain lands once, and never more than 3 run at once."""
        rt = self.rt
        _add_underlyings(rt, ALL)
        fp = SlowChains(rt.clock, first_together=3)
        self.chains_for(fp, ALL)
        w = self.worker(fp, fetch_threads=1, chain_threads=3, period_s=0.05)
        try:
            self.assertEqual((3, 3), (w.chain_threads, self.chains.max_in_flight))    # the pool size is the cache's in-flight limit
            now = int(rt.clock.epoch())
            self.chains.set_pins(PINNED)
            self.assertEqual(PINNED, self.chains.due(now))                    # the six refreshes are queued first ...
            fut = w.request_chain(REQUESTED)                                  # ... then the ticket's request (REQUEST: ahead of them)
            self.assertEqual(ALL, self.chains.pending())
            w.start()
            self.assertTrue(_wait(lambda: fut.done() and all(self.chains.peek(u) is not None for u in ALL)), self.chains.stats())
            self.assertIs(self.chains.peek(REQUESTED), fut.result(timeout=0))
            self.assertIn(REQUESTED, fp.started[:3], fp.started)
            self.assertEqual(sorted(ALL), sorted(fp.started))                 # each chain fetched exactly once
            self.assertEqual(3, fp.inside.peak)                               # three downloads at once, never more
            st = w.stats()
            with w._lock:
                fetched = w.stats_["chains"]
            self.assertEqual((7, 0, 3, [], []), (fetched, st["chain_errors"], st["chain_threads"], st["chains"]["pending"], st["chains"]["in_flight"]))
            self.assertEqual(sorted(ALL), sorted(st["chain_fetch_s"]))        # the last fetch time of each underlying, in seconds
            self.assertTrue(all(DELAY_S - 0.05 <= v < 10 for v in st["chain_fetch_s"].values()), st["chain_fetch_s"])
            json.dumps(st["chains"])
            json.dumps(st["chain_fetch_s"])
        finally:
            w.stop(timeout=5.0)
        # stop(): the pool is shut and none of its threads is alive any more
        self.assertIsNone(w._chain_pool)
        self.assertFalse(w.is_alive())
        self.assertEqual(3, len(fp.threads))
        self.assertTrue(all(t.name.startswith("chain-fetch") for t in fp.threads), [t.name for t in fp.threads])
        self.assertEqual([], [t.name for t in fp.threads if t.is_alive()])

    def test_a_failing_chain_releases_its_slot_and_the_others_land(self):
        """Any failure inside a chain job - the provider's (404) or an unexpected one in the worker (here the master) - ends in ChainCache.fail:
        the slot is released, the waiter gets the error, chain_errors counts it, and the other chains still land on the pool."""
        rt = self.rt
        _add_underlyings(rt, ALL)
        fp = SlowChains(rt.clock, delay=0.02)
        self.chains_for(fp, [u for u in ALL if u != "WIDG2"])               # WIDG2 has no chain: the provider answers 404
        w = self.worker(fp, fetch_threads=1, chain_threads=3, period_s=0.05)
        real_get = rt.instruments.get

        def get(iid, conn=None):
            if iid == "WIDG3":
                raise RuntimeError("master unavailable (test)")
            return real_get(iid, conn)
        try:
            with mock.patch.object(rt.instruments, "get", side_effect=get):
                futs = {u: w.request_chain(u) for u in ALL}
                w.start()
                self.assertTrue(_wait(lambda: all(f.done() for f in futs.values())), self.chains.stats())
            self.assertIn("no chain for WIDG2", str(futs["WIDG2"].exception(timeout=0)))
            self.assertIn("RuntimeError: master unavailable (test)", str(futs["WIDG3"].exception(timeout=0)))
            self.assertEqual(sorted(set(ALL) - {"WIDG2", "WIDG3"}), sorted(self.chains.stats()["cached"]))
            st = w.stats()
            with w._lock:
                fetched = w.stats_["chains"]
            self.assertEqual((5, 2), (fetched, st["chain_errors"]))
            self.assertEqual(([], []), (st["chains"]["in_flight"], st["chains"]["pending"]))   # no slot leaked
            self.assertEqual({"WIDG2", "WIDG3"}, set(st["chains"]["errors"]))
        finally:
            w.stop(timeout=5.0)

    def test_run_hands_a_dead_workers_slot_back_first(self):
        """A restarted worker (the supervisor's make_worker) gets the cache with a slot a dead thread still held: run() gives it back to the
        queue before its pool starts, so the chain is fetched (it would wait forever in 1.0.7.1)."""
        rt = self.rt
        _add_underlyings(rt, ["ACME"])
        fp = SlowChains(rt.clock, delay=0.0)
        self.chains_for(fp, ["ACME"])
        w = self.worker(fp, fetch_threads=1, chain_threads=1, period_s=0.05)
        fut = self.chains.request("ACME")
        self.assertEqual("ACME", self.chains.take_pending())                   # taken by a thread that died before put / fail
        self.assertEqual(["ACME"], self.chains.stats()["in_flight"])
        try:
            w.start()
            got = fut.result(timeout=10)
            self.assertIs(self.chains.peek("ACME"), got)
            self.assertEqual(["ACME"], fp.started)
        finally:
            w.stop(timeout=5.0)
        self.assertIsNone(w._chain_pool)
        self.assertEqual([], [t.name for t in fp.threads if t.is_alive()])

    def test_a_retired_worker_hands_its_changes_to_the_live_one_and_is_never_linked(self):
        """1.0.7.2 second review: ``Runtime.make_worker`` retires a dead quote worker.  Its pending quote changes, and those of a chain job
        that ends after it retired, go to the worker that replaced it - followed through later replacements to the live one - and no worker
        keeps a link to a dead one, so restarts never build a chain of dead workers that every tick walks."""
        fp = SlowChains(self.rt.clock, delay=0.0)
        w1, w2, w3 = (self.worker(fp, fetch_threads=0) for _ in range(3))
        w1._changed = ["ACME"]
        w1.retire(w2)
        self.assertEqual((["ACME"], [], None), (w2._changed, w1._changed, w2._predecessor))
        w2.retire(w3)                                                         # w2 died too: w3 is the live worker
        self.assertEqual((["ACME"], None, None), (w3._changed, w3._predecessor, w2._predecessor))
        w1._run_chain = lambda uid, now: w1._changed.append(uid)              # w1's last chain job, still running, ends now
        w1._chain_job("WIDG1")                                                # retired: one chain, then its changes go forward and it ends
        self.assertEqual((["ACME", "WIDG1"], []), (w3._drain_changed(), w1._changed))
        self.assertEqual([], w3._drain_changed())

    def test_a_retired_worker_hands_its_product_refreshers_to_the_live_one(self):
        """1.0.7.2 third review: the product refreshers the modules register at bind time are taken by the first worker built; a worker
        that replaces a dead one (``Runtime.make_worker``) gets them from it at ``retire`` - the same schedules, counters and back-off - so a
        restart never stops the debt, OTC option or CDS inputs.  Its own ``rfr`` and ``curves`` stay its own.  A run queued on the dead
        worker's pool is cancelled with it and clears its in-flight flag, so the refresher runs again in the live worker."""
        from concurrent.futures import ThreadPoolExecutor
        fp = SlowChains(self.rt.clock, delay=0.0)
        now = int(self.rt.clock.epoch())
        calls = []

        def failing(services, at):
            calls.append(at)
            raise RuntimeError("source down (test)")
        w1 = self.worker(fp, fetch_threads=1)
        w1.add_refresher("debt", 60, failing)
        w1.add_refresher("otc_options", 15, lambda services, at: calls.append(("otc", at)))
        gate = threading.Event()
        w1._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="quote-test")
        try:
            w1._pool.submit(gate.wait, 10)                                    # the pool's only thread is busy: the next runs queue behind it
            w1._run_refresher(w1._refreshers["debt"], now)                    # one failed run: backed off
            debt = w1._refreshers["debt"]
            self.assertEqual((1, 1, 1, now + 60), (debt.runs, debt.failures, debt.streak, debt.retry_at))
            self.assertIn("otc_options", w1._run_refreshers(now))             # queued on the busy pool: in flight
            otc = w1._refreshers["otc_options"]
            self.assertTrue(otc.in_flight)
            w2 = self.worker(fp, fetch_threads=0)
            own = {n: w2._refreshers[n] for n in ("rfr", "curves")}
            w1.retire(w2)                                                     # the pool shuts: the queued run is cancelled
        finally:
            gate.set()
        self.assertFalse(otc.in_flight)
        self.assertEqual({"debt", "otc_options", "rfr", "curves"} - set(w2.refreshers()), set())
        self.assertIs(debt, w2._refreshers["debt"])                            # the same schedule and back-off
        self.assertIs(otc, w2._refreshers["otc_options"])
        self.assertEqual(own, {n: w2._refreshers[n] for n in ("rfr", "curves")})
        self.assertEqual((1, 1, now + 60), (w2.refreshers()["debt"]["runs"], w2.refreshers()["debt"]["failures"], w2.refreshers()["debt"]["retry_at"]))
        ran = w2._run_refreshers(now + 30)                                    # the live worker runs them: OTC due, debt still backed off
        self.assertIn("otc_options", ran)
        self.assertNotIn("debt", ran)
        self.assertIn(("otc", now + 30), calls)
        self.assertIn("debt", w2._run_refreshers(now + 60))

    def test_the_pool_size_follows_the_config_and_fetch_threads(self):
        fp = SlowChains(self.rt.clock, delay=0.0)
        self.assertEqual((3, 32), (config.CHAIN_FETCH_WORKERS, CHAIN_FETCH_TIMES_KEPT))
        w = self.worker(fp, fetch_threads=2)                                  # the server's worker: the config's 3
        self.assertEqual((3, 3), (w.chain_threads, self.chains.max_in_flight))
        w = self.worker(fp, fetch_threads=0, chain_threads=3)                 # inline: never a pool, whatever is asked
        self.assertEqual((0, 1), (w.chain_threads, self.chains.max_in_flight))
        w = self.worker(fp, fetch_threads=2, chain_threads=0)                 # a quote pool, chains inline
        self.assertEqual((0, 1), (w.chain_threads, self.chains.max_in_flight))


class ParseLockTests(_Base):
    def test_the_parses_never_overlap_while_the_downloads_do(self):
        """Two CboeChain.chain calls at once: both downloads run together (the rate limiter spaces only their starts), the JSON decode +
        parse_chain run one after the other under _CHAIN_PARSE_LOCK (the memory peak is one parse)."""
        rt = self.rt
        payloads = {}
        for sym, spot in (("ACME", 112.5), ("WIDG", 48.0)):
            k = int(spot)
            payloads[sym] = json.dumps({"data": {"symbol": sym, "current_price": spot, "bid": spot - 0.05, "ask": spot + 0.05, "last_trade_time": "2026-10-20T13:58:00",
                                                 "options": [{"option": _occ(sym, r, k), "bid": 2.0, "ask": 2.2, "bid_size": 10, "ask_size": 12, "iv": 0.31}
                                                             for r in ("C", "P")]}}).encode("utf-8")
        downloads, parses = _Peak(), _Peak()
        both = threading.Barrier(2)                     # the two downloads are in flight together, however busy the machine

        def getter(url, ua, timeout, accept):
            with downloads:
                both.wait(timeout=10.0)
                time.sleep(0.2)
            return payloads[url.rsplit("/", 1)[1][:-len(".json")]]
        real_parse = _providers.parse_chain

        def slow_parse(*a, **k):
            with parses:
                time.sleep(0.15)
                return real_parse(*a, **k)
        out, errors = {}, []
        cc = CboeChain(rt.clock, None, getter=getter, sleep=lambda s: None)  # an injected sleep: the limiter never waits on wall time

        def run(sym):
            try:
                out[sym] = cc.chain(sym, sym)
            except Exception as e:   # noqa: BLE001 - reported below
                errors.append(e)
        with mock.patch.object(_providers, "parse_chain", slow_parse):
            threads = [threading.Thread(target=run, args=(s,), name=f"test-cboe-{s}", daemon=True) for s in ("ACME", "WIDG")]
            for t in threads:
                t.start()
            for t in threads:
                t.join(15.0)
        self.assertEqual([], errors)
        self.assertEqual([False, False], [t.is_alive() for t in threads])
        self.assertEqual((2, 2), (downloads.entered, downloads.peak))         # the two downloads overlapped
        self.assertEqual((2, 1), (parses.entered, parses.peak))               # the two parses never did
        self.assertEqual({"ACME": 2, "WIDG": 2}, {s: snap.n_kept() for s, snap in out.items()})
        self.assertFalse(_providers._CHAIN_PARSE_LOCK.locked())


class InlineModeTests(_Base):
    def test_one_chain_per_tick_request_first(self):
        """fetch_threads=0: no pool; each tick fetches ONE chain on the scheduler thread, after the quote fetches, the REQUEST first, and
        report["chain"] is the fetch's own answer, as before."""
        rt = self.rt
        _add_underlyings(rt, ["ACME", "WIDG1", "WIDG2"])
        fp = SlowChains(rt.clock, delay=0.0)
        self.chains_for(fp, ["ACME", "WIDG1", "WIDG2"])
        w = self.worker(fp, fetch_threads=0)
        self.assertEqual((0, None), (w.chain_threads, w._chain_pool))
        now = int(rt.clock.epoch())
        self.chains.set_pins(["WIDG1", "WIDG2"])
        self.chains.due(now)                                                   # the two refreshes first ...
        w.request_chain("ACME")                                               # ... then the ticket's request
        order = [w.tick()["chain"] for _ in range(4)]
        self.assertEqual([{"underlying": u, "ok": True, "contracts": 0, "records": 0} for u in ("ACME", "WIDG1", "WIDG2")] + [None], order)
        self.assertEqual(["ACME", "WIDG1", "WIDG2"], fp.started)
        self.assertEqual(["ACME", "WIDG1", "WIDG2"], sorted(w.stats()["chain_fetch_s"]))
        self.assertEqual({threading.current_thread()}, fp.threads)            # fetched on this (the scheduler's) thread

    def test_a_due_options_pin_sets_its_chains_priority(self):
        """An option due for its quote asks for its underlying's chain: a working order on it (ORDER pin) -> ORDER, a ticket / instrument page
        (PAGE) -> REQUEST, anything else (HELD, WATCH) -> REFRESH; the best of each underlying's options counts."""
        rt = self.rt
        unders = ["ACME", "WIDG1", "WIDG2"]
        _add_underlyings(rt, unders)
        fp = SlowChains(rt.clock, delay=0.0)
        self.chains_for(fp, unders)
        w = self.worker(fp, fetch_threads=0)
        occ = {u: _occ(u, "C", 100) for u in unders}
        put2 = _occ("WIDG2", "P", 100)
        with rt.db.writer() as tx:
            for u, o in list(occ.items()) + [("WIDG2", put2)]:
                rt.instruments.ensure_option(tx, o, rt.instruments.get(u, tx.conn), {"option": o})
        rt.instruments.load()
        self.cache.pin([occ["WIDG1"]], "ORDER")                                # a working order on a WIDG1 option
        self.cache.subscribe([occ["ACME"]], 90, "PAGE")                        # a ticket open on an ACME option
        self.cache.pin([occ["WIDG2"]], "HELD")                                 # a held WIDG2 option ...
        self.cache.subscribe([put2], 90, "WATCH")                              # ... and a watched one: both REFRESH
        rep = w.tick()
        self.assertEqual("ACME", rep["chain"]["underlying"])                   # the REQUEST went first
        self.assertEqual(["WIDG1", "WIDG2"], self.chains.pending())
        self.assertEqual({"WIDG1": "ORDER", "WIDG2": "REFRESH"}, self.chains.stats()["pending_priority"])


if __name__ == "__main__":
    unittest.main()
