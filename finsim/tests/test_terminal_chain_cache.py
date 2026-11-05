"""1.0.7.1 change A (owner 2026-11-03 "raise the option-chain limit"): how many option chains the server keeps in memory is the owner
setting ``chain_cache_entries`` ("option chains kept live"): a whole number from 2 to 24, default 12, validated like the participation
rate, always shown by GET /api/settings, read at server start and applied live when a save commits (``ChainCache.resize`` under the
cache's lock; a smaller limit drops the least recently used chains at once).  1.0.7 kept 2.

Nothing here touches the network, a port or ``~/.finsim``: ``ChainCache`` on its own with a fixed clock, and ``server.build_runtime``
offline (FakeProvider, FakeClock) in a temp home with FINSIM_HOME / FINSIM_TERMINAL_DB pointed at it and restored afterwards; the
routes go through ``Router.handle`` without sockets.  A snapshot here has no contracts: the cache keys and ages it by underlying and
fetch time only.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import unittest
from decimal import Decimal

from tests.terminal_helpers import _Log
from finsim.terminal import api as _api, config, db as _db, server as _server
from finsim.terminal.clock import FakeClock
from finsim.terminal.db import get_setting, set_setting
from finsim.terminal.quotes.cache import ChainCache
from finsim.terminal.quotes.parsers import ChainSnapshot, ChainUnderlying
from finsim.terminal.quotes.providers import FakeProvider

AT = "2026-10-20T14:00:00-04:00"      # Tuesday 14:00 ET, US_EQUITY open (the world of test_terminal_api)
KEY = "chain_cache_entries"
LABEL = "option chains kept live"
# 25 fictional underlyings: the first 12 fill the default cache, then 13 more
FIRST = ["ZZ01", "ZZ02", "ZZ03", "ZZ04", "ZZ05", "ZZ06", "ZZ07", "ZZ08", "ZZ09", "ZZ10", "ZZ11", "ZZ12"]
MORE = ["ZZ13", "ZZ14", "ZZ15", "ZZ16", "ZZ17", "ZZ18", "ZZ19", "ZZ20", "ZZ21", "ZZ22", "ZZ23", "ZZ24", "ZZ25"]
ALL = FIRST + MORE


def snap(uid: str, fetched_at: int) -> ChainSnapshot:
    return ChainSnapshot(uid, uid, ChainUnderlying(uid, Decimal("100.00"), None, None, None, None, None, None, None), [], int(fetched_at), None, 0)


# ===== the cache on its own ==============================================================================================================
class ChainCacheLimit(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock(AT)
        self.now = int(self.clock.epoch())

    def fill(self, cache: ChainCache, names) -> None:
        for u in names:
            cache.put(snap(u, self.now))

    def test_class_default_stays_two(self):
        """Every other test builds ChainCache(clock): the class default is still 2; only the server passes the owner setting."""
        c = ChainCache(self.clock)
        self.assertEqual(2, c.max_entries)
        self.fill(c, ["ZZ01", "ZZ06", "ZZ03"])
        self.assertEqual(["ZZ06", "ZZ03"], c.stats()["cached"])                  # 3 chains into 2 slots: the first (ZZ01) went

    def test_eviction_order_is_least_recently_used(self):
        c = ChainCache(self.clock, max_entries=3)
        self.fill(c, ["ZZ01", "ZZ06", "ZZ03"])                                   # oldest first: ZZ01, ZZ06, ZZ03
        self.assertIsNotNone(c.get("ZZ01"))                                     # a read (fresh: fetched now) refreshes ZZ01: ZZ06, ZZ03, ZZ01
        self.assertIsNotNone(c.peek("ZZ06"))                                    # peek never refreshes
        c.put(snap("ZZ04", self.now))                                           # 4 > 3: ZZ06, the least recently used, goes
        self.assertEqual(["ZZ03", "ZZ01", "ZZ04"], c.stats()["cached"])
        c.put(snap("ZZ03", self.now))                                            # a new snapshot of a cached name refreshes it; nothing goes
        self.assertEqual(["ZZ01", "ZZ04", "ZZ03"], c.stats()["cached"])
        c.put(snap("ZZ05", self.now))                                           # ZZ01 goes
        self.assertEqual(["ZZ04", "ZZ03", "ZZ05"], c.stats()["cached"])
        self.assertIsNone(c.peek("ZZ06"))
        self.assertIsNone(c.peek("ZZ01"))

    def test_default_twelve_holds_twelve_underlyings(self):
        c = ChainCache(self.clock, max_entries=config.CHAIN_CACHE_ENTRIES)
        self.fill(c, FIRST)
        self.assertEqual(FIRST, c.stats()["cached"])                             # all 12 stay (2 slots kept only the last two)
        c.put(snap("ZZ13", self.now))                                            # the 13th drops the oldest, ZZ01
        self.assertEqual(FIRST[1:] + ["ZZ13"], c.stats()["cached"])

    def test_resize_smaller_drops_the_oldest_at_once(self):
        c = ChainCache(self.clock, max_entries=12)
        self.fill(c, FIRST)
        self.assertIsNotNone(c.get("ZZ02"))                                      # ZZ02 becomes the most recently used
        dropped = c.resize(4)
        # 12 -> 4: the 8 least recently used go at once, oldest first; ZZ10, ZZ11, ZZ12 and ZZ02 stay
        self.assertEqual(["ZZ01", "ZZ03", "ZZ04", "ZZ05", "ZZ06", "ZZ07", "ZZ08", "ZZ09"], dropped)
        self.assertEqual(["ZZ10", "ZZ11", "ZZ12", "ZZ02"], c.stats()["cached"])
        self.assertEqual(4, c.max_entries)
        c.put(snap("ZZ13", self.now))                                            # later puts keep the new limit: ZZ10 goes
        self.assertEqual(["ZZ11", "ZZ12", "ZZ02", "ZZ13"], c.stats()["cached"])

    def test_resize_larger_keeps_every_chain(self):
        c = ChainCache(self.clock)                                              # 2
        self.fill(c, ALL[:2])
        self.assertEqual([], c.resize(24))                                      # growing drops nothing
        self.assertEqual(ALL[:2], c.stats()["cached"])
        self.fill(c, ALL[2:24])
        self.assertEqual(ALL[:24], c.stats()["cached"])                         # 24 chains, all kept
        c.put(snap(ALL[24], self.now))                                          # the 25th drops the oldest, ZZ01
        self.assertEqual(ALL[1:25], c.stats()["cached"])
        self.assertEqual(24, c.stats()["max_entries"])

    def test_resize_same_limit_no_op_and_a_limit_below_one_raises(self):
        log = _Log()
        c = ChainCache(self.clock, max_entries=12, log=log)
        self.fill(c, FIRST[:3])
        self.assertEqual([], c.resize(12))
        self.assertEqual([], log.records)                                       # nothing changed: no log line
        for bad in (0, -1):
            with self.assertRaises(ValueError):
                c.resize(bad)
        self.assertEqual(12, c.max_entries)
        self.assertEqual(FIRST[:3], c.stats()["cached"])
        self.assertEqual(["ZZ01"], c.resize(2))
        self.assertEqual([("INFO", "option chains kept live: 12 -> 2 (dropped ZZ01)")], log.records)
        self.assertEqual(2, c.stats()["max_entries"])

    def test_resize_keeps_fetches_pins_and_errors(self):
        """Only snapshots go: an in-flight fetch still resolves its waiters, and a dropped chain that is pinned is queued again
        when its 90 s refresh is due."""
        c = ChainCache(self.clock, max_entries=4)
        self.fill(c, ["ZZ01", "ZZ06", "ZZ03", "ZZ04"])
        c.pin("ZZ01")
        f = c.request("ZZ10")                                                   # not cached: one fetch queued
        self.assertEqual("ZZ10", c.take_pending())                              # the worker takes it: in flight
        c.fail("ZZ07", RuntimeError("HTTP 503"))
        self.assertEqual(["ZZ01", "ZZ06"], c.resize(2))
        st = c.stats()
        # 1.0.7.2 (CHAIN-FETCH-1): in_flight is the sorted list of the fetches out (several at once with a chain pool)
        self.assertEqual((["ZZ03", "ZZ04"], ["ZZ01"], ["ZZ10"], {"ZZ07": "HTTP 503"}), (st["cached"], st["pinned"], st["in_flight"], st["errors"]))
        self.assertIsNone(c.peek("ZZ01"))
        self.clock.advance(90)                                                  # put set ZZ01's next refresh at fetch + 90 s
        self.assertEqual(["ZZ01"], c.due())
        self.assertEqual(["ZZ01"], c.pending())                                 # nothing cached any more, so it is fetched again
        landed = snap("ZZ10", int(self.clock.epoch()))
        c.put(landed)                                                             # the in-flight fetch lands: ZZ03 (oldest) goes
        self.assertIs(landed, f.result(timeout=0))
        self.assertEqual(["ZZ04", "ZZ10"], c.stats()["cached"])

    def test_resize_takes_the_cache_lock(self):
        c = ChainCache(self.clock, max_entries=4)
        self.fill(c, ["ZZ01", "ZZ06", "ZZ03", "ZZ04"])
        out = []
        t = threading.Thread(target=lambda: out.append(c.resize(2)), daemon=True)
        with c._lock:                                                           # another thread (the quote worker) is inside the cache
            t.start()
            t.join(0.3)
            self.assertTrue(t.is_alive())                                       # resize waits for the lock ...
            self.assertEqual(4, c.max_entries)                                  # ... and has changed nothing yet
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertEqual([["ZZ01", "ZZ06"]], out)
        self.assertEqual(["ZZ03", "ZZ04"], c.stats()["cached"])


# ===== 1.0.7.2 CHAIN-FETCH-1: priorities, several fetches in flight, requeue, stats ======================================================
PINS = [f"PIN{i:02d}" for i in range(1, 23)]            # 22 fictional pinned underlyings


class ChainPriorityTests(unittest.TestCase):
    """A requested chain (a ticket, an instrument page, the hedge panel) is handed out ahead of the 90 s refreshes of the pinned chains; an
    option with a working order (ORDER) sits between; up to ``max_in_flight`` fetches are out at once (the worker's chain pool), the class
    default still one at a time.  Fictional underlyings only."""

    def setUp(self):
        self.clock = FakeClock(AT)
        self.now = int(self.clock.epoch())

    def test_a_request_is_taken_ahead_of_22_pinned_refreshes(self):
        c = ChainCache(self.clock, max_entries=24)
        c.set_pins(PINS)
        self.assertEqual(PINS, c.due())                                         # never fetched: all 22 due, queued in id order as REFRESH
        self.assertEqual(PINS, c.pending())
        self.assertEqual({u: "REFRESH" for u in PINS}, c.stats()["pending_priority"])
        f = c.request("ACME")                                                   # a ticket asks for ACME after the 22 refreshes were queued
        self.assertEqual(["ACME"] + PINS, c.pending())                          # ... and it goes first (it waited behind all 22 in 1.0.7.1)
        self.assertEqual("REQUEST", c.stats()["pending_priority"]["ACME"])
        self.assertEqual("ACME", c.take_pending())
        self.assertIsNone(c.take_pending())                                     # the class default: one fetch at a time
        c.put(snap("ACME", self.now))
        self.assertIs(c.peek("ACME"), f.result(timeout=0))
        self.assertEqual(["PIN01", "PIN02"], [c.take_pending(), c.pending()[0]])  # then the refreshes in their arrival order

    def test_a_promoted_refresh_keeps_its_place_among_the_requests(self):
        c = ChainCache(self.clock, max_entries=24)
        c.set_pins(PINS[:5])
        c.due()                                                                 # PIN01 .. PIN05 queued as REFRESH (arrivals 1 .. 5)
        f3 = c.request("PIN03", priority="REFRESH")                             # asked again at the same rank: nothing moves, the same future
        self.assertEqual(PINS[:5], c.pending())
        c.request("ACME")                                                       # REQUEST, arrival 6
        self.assertIs(f3, c.request("PIN03"))                                   # a ticket asks for PIN03: promoted, still one shared fetch
        # PIN03 now ranks with the requests and keeps its arrival (3), so it is ahead of ACME (6); the other refreshes follow in order
        self.assertEqual(["PIN03", "ACME", "PIN01", "PIN02", "PIN04", "PIN05"], c.pending())
        self.assertEqual({"PIN03": "REQUEST", "ACME": "REQUEST", "PIN01": "REFRESH", "PIN02": "REFRESH", "PIN04": "REFRESH", "PIN05": "REFRESH"},
                         c.stats()["pending_priority"])
        c.request("ACME", priority="REFRESH")                                   # a worse rank never demotes an entry
        self.assertEqual("REQUEST", c.stats()["pending_priority"]["ACME"])
        with self.assertRaises(ValueError):
            c.request("WIDG", priority="SOON")                                  # an unknown priority is a programming error, never a guess
        self.assertNotIn("WIDG", c.pending())

    def test_order_sits_between_request_and_refresh(self):
        c = ChainCache(self.clock, max_entries=24)
        c.set_pins(["PIN01", "PIN02"])
        c.due()                                                                 # 2 refreshes
        c.request("WIDG", priority="ORDER")                                     # an option of WIDG has a working order
        c.request("ACME")                                                       # a ticket asks for ACME
        self.assertEqual(["ACME", "WIDG", "PIN01", "PIN02"], c.pending())
        self.assertEqual({"ACME": "REQUEST", "WIDG": "ORDER", "PIN01": "REFRESH", "PIN02": "REFRESH"}, c.stats()["pending_priority"])
        c.request("PIN02", priority="ORDER")                                    # an order arrives on a PIN02 option: up to ORDER, keeping its
        self.assertEqual(["ACME", "PIN02", "WIDG", "PIN01"], c.pending())       # arrival (2), so ahead of WIDG (3); PIN01 stays a refresh

    def test_three_in_flight_then_a_put_or_a_fail_frees_a_slot(self):
        c = ChainCache(self.clock, max_entries=24, max_in_flight=3)
        names = ["ACME", "WIDG", "PIN01", "PIN02", "PIN03"]
        futs = [c.request(u) for u in names]
        self.assertEqual(names[:3], [c.take_pending(), c.take_pending(), c.take_pending()])
        self.assertIsNone(c.take_pending())                                     # three out: the fourth waits
        st = c.stats()
        self.assertEqual((["ACME", "PIN01", "WIDG"], 3, ["PIN02", "PIN03"]), (st["in_flight"], st["max_in_flight"], st["pending"]))
        self.assertEqual([], c.due())                                           # nothing pinned; and an id in flight is never queued twice
        c.request("WIDG")                                                       # asked again while in flight: not queued again
        self.assertEqual(["PIN02", "PIN03"], c.pending())
        c.put(snap("WIDG", self.now))                                           # a fetch lands: its slot is free
        self.assertEqual("PIN02", c.take_pending())
        self.assertIsNone(c.take_pending())
        c.fail("ACME", RuntimeError("HTTP 503"))                                # a fetch fails: its slot is free too, the waiters get the error
        with self.assertRaises(RuntimeError):
            futs[0].result(timeout=0)
        self.assertEqual("PIN03", c.take_pending())
        self.assertEqual(["PIN01", "PIN02", "PIN03"], c.stats()["in_flight"])
        self.assertEqual(2, c.set_max_in_flight(2))                             # a smaller limit: no new fetch until two have landed
        self.assertIsNone(c.take_pending())
        with self.assertRaises(ValueError):
            c.set_max_in_flight(0)
        self.assertEqual(2, c.max_in_flight)

    def test_a_slot_a_live_job_holds_is_never_requeued(self):
        """1.0.7.2 review: each slot keeps the worker that took it; a restarted worker requeues only the slots no live job holds (its
        predecessor's chain pool may still be downloading one), so one underlying is never downloaded twice at once."""
        c = ChainCache(self.clock, max_entries=24, max_in_flight=3)
        c.request("ACME"), c.request("WIDG")
        live, dead = object(), object()
        self.assertEqual(["ACME", "WIDG"], [c.take_pending(owner=live), c.take_pending(owner=dead)])
        held = lambda uid, owner: owner is live                                # what QuoteWorker._slot_held_elsewhere answers
        self.assertEqual(["WIDG"], c.requeue_in_flight(keep=held))
        self.assertEqual((["ACME"], ["WIDG"]), (c.stats()["in_flight"], c.pending()))
        c.put(ChainSnapshot("ACME", "ACME", ChainUnderlying("ACME", Decimal("100"), None, None, None, None, None, None, None), [], self.now, None, 0))
        self.assertEqual([], c.stats()["in_flight"])                            # the live job's put releases its own slot
        self.assertEqual([], c.requeue_in_flight(keep=held))

    def test_requeue_in_flight_gives_a_dead_workers_slots_back(self):
        c = ChainCache(self.clock, max_entries=24, max_in_flight=3)
        fa, fw = c.request("ACME"), c.request("WIDG")
        self.assertEqual(["ACME", "WIDG"], [c.take_pending(), c.take_pending()])  # the worker that took them dies before put / fail
        c.request("PIN01")                                                      # REQUEST
        c.set_pins(["PIN02"])
        c.due()                                                                 # REFRESH
        self.assertEqual([], c.requeue_in_flight(["PIN01"]))                    # an id that is not in flight is left alone
        self.assertEqual(["ACME", "WIDG"], c.requeue_in_flight())               # a restarted worker hands them back ...
        self.assertEqual([], c.stats()["in_flight"])
        # ... as REQUESTs behind the requests already queued and ahead of the refreshes; the waiters keep their futures
        self.assertEqual(["PIN01", "ACME", "WIDG", "PIN02"], c.pending())
        self.assertEqual("REQUEST", c.stats()["pending_priority"]["ACME"])
        self.assertIs(fa, c.request("ACME"))
        self.assertFalse(fa.done() or fw.done())
        for _ in range(3):
            uid = c.take_pending()
            c.put(snap(uid, self.now))
        self.assertIs(c.peek("ACME"), fa.result(timeout=0))
        self.assertIs(c.peek("WIDG"), fw.result(timeout=0))

    def test_stats_shapes(self):
        c = ChainCache(self.clock, max_entries=24, max_in_flight=3)
        st = c.stats()
        self.assertEqual(([], 3, {}, None, [], []), (st["in_flight"], st["max_in_flight"], st["pending_priority"], st["oldest_pinned_age_s"],
                                                      st["pending"], st["cached"]))
        c.put(snap("PIN01", self.now - 100))
        c.put(snap("PIN02", self.now - 30))
        c.put(snap("ACME", self.now - 500))                                     # cached but not pinned: not counted
        self.assertIsNone(c.stats()["oldest_pinned_age_s"])                     # nothing pinned yet
        c.set_pins(["PIN01", "PIN02", "PIN03"])                                 # PIN03 is pinned but not cached: not counted
        self.assertEqual(100, c.stats()["oldest_pinned_age_s"])                 # now - the oldest fetch among the pinned chains cached
        c.request("WIDG")
        c.take_pending()
        st = c.stats()
        # PIN01 (fetched 100 s ago) is past its 90 s refresh and PIN03 was never fetched: both queued as REFRESH; PIN02 (30 s) is not due
        self.assertEqual((["WIDG"], ["PIN01", "PIN03"], {"PIN01": "REFRESH", "PIN03": "REFRESH"}), (st["in_flight"], c.due(), c.stats()["pending_priority"]))
        for key in ("cached", "pending", "in_flight", "pinned", "on_screen", "errors", "max_entries", "max_in_flight", "pending_priority",
                    "oldest_pinned_age_s"):
            self.assertIn(key, st, key)
        json.dumps(c.stats())                                                   # plain JSON: the health view can carry it

    def test_the_class_default_still_hands_out_one_at_a_time(self):
        c = ChainCache(self.clock)
        self.assertEqual((2, 1), (c.max_entries, c.max_in_flight))
        c.request("ACME")
        c.request("WIDG")
        self.assertEqual("ACME", c.take_pending())
        self.assertIsNone(c.take_pending())
        self.assertEqual(["ACME"], c.stats()["in_flight"])
        c.put(snap("ACME", self.now))
        self.assertEqual("WIDG", c.take_pending())


# ===== the owner setting through the server and the API =================================================================================
def build(tmp: str):
    clock = FakeClock(AT)
    return _server.build_runtime(tmp, os.path.join(tmp, "terminal.db"), clock=clock, offline=True, fake_provider=FakeProvider(clock), log=_Log(),
                                 books=[config.TEST_BOOK], fetch_threads=0)


class ChainCacheSetting(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in (config.HOME_ENV, config.DB_ENV)}
        self.tmp = tempfile.mkdtemp(prefix="fst-chain1071-")
        os.environ[config.HOME_ENV] = self.tmp
        os.environ[config.DB_ENV] = os.path.join(self.tmp, "terminal.db")
        self.rt = build(self.tmp)
        self.n = 0

    def tearDown(self):
        if self.rt is not None:
            self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def restart(self) -> None:
        """A new process on the same database (the chains in memory are gone; the settings are read again)."""
        self.rt.close()
        self.rt = None
        self.rt = build(self.tmp)

    def patch(self, body: dict, request_id=None):
        self.n += 1
        rid = request_id or f"ui:chain1071-{self.n:04d}"
        return self.rt.router.handle("PATCH", "/api/settings", None, dict(body, request_id=rid), "127.0.0.1", None)

    def settings(self) -> dict:
        status, body, _ = self.rt.router.handle("GET", "/api/settings", None, None, "127.0.0.1", None)
        self.assertEqual(200, status, body)
        return body

    def stored(self):
        with self.rt.db.reader() as conn:
            return get_setting(conn, KEY, "absent")

    def changed_events(self) -> list:
        with self.rt.db.reader() as conn:
            rows = conn.execute("SELECT payload_json FROM events WHERE type='SETTINGS_CHANGED' AND ref_id LIKE ? ORDER BY id", (f"%{KEY}%",)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def test_default_is_twelve(self):
        rt = self.rt
        self.assertEqual((12, 2, 24), (config.CHAIN_CACHE_ENTRIES, config.CHAIN_CACHE_ENTRIES_MIN, config.CHAIN_CACHE_ENTRIES_MAX))
        self.assertEqual(12, _db.DEFAULT_SETTINGS[KEY])
        self.assertEqual(12, self.stored())                                     # a new database is seeded with 12
        self.assertEqual(12, rt.chains.max_entries)                             # read at start
        self.assertIs(rt.chains, rt.worker.chains)                              # one cache: the quote worker's and the marks'
        self.assertIs(rt.chains, rt.marks.chains)
        self.assertEqual(12, self.settings()[KEY])
        status, st, _ = rt.router.handle("GET", "/api/state", None, None, "127.0.0.1", None)
        self.assertEqual(200, status)
        self.assertEqual(12, st["settings"][KEY])
        self.assertIn(KEY, _api.SETTING_KEYS)
        self.assertEqual(LABEL, _api.SETTING_LABELS[KEY])

    def test_default_is_twelve_on_a_database_without_the_row(self):
        """The live database was created before 1.0.7.1 and seeding runs only on a new file: no row means 12."""
        with self.rt.db.writer() as tx:
            tx.conn.execute("DELETE FROM settings WHERE key=?", (KEY,))
        self.assertEqual(12, self.settings()[KEY])                              # GET always carries the number in force
        self.restart()
        self.assertEqual("absent", self.stored())                               # a restart does not seed an existing file
        self.assertEqual(12, self.rt.chains.max_entries)
        self.assertEqual(12, self.settings()[KEY])
        status, body, _ = self.patch({KEY: 3})                                  # the first save writes the row
        self.assertEqual((200, 3, 3, 3), (status, body[KEY], self.stored(), self.rt.chains.max_entries))
        self.assertEqual([{"before": {KEY: None}, "after": {KEY: 3}}], self.changed_events())

    def test_bounds_and_bad_values_are_refused(self):
        rt = self.rt
        for bad in (1, 25, 0, -3, 100, 12.5, "12.5", "abc", "", "  ", None, True, False, [12], {"n": 12}, "NaN", "Infinity", "1e3"):
            status, body, _ = self.patch({KEY: bad})
            self.assertEqual((400, "SETTING_VALUE"), (status, body["error"]["code"]), repr(bad))
            err = body["error"]
            self.assertEqual({"field": LABEL, "min": 2, "max": 24}, err["details"], repr(bad))
            self.assertEqual(f"{LABEL}: {bad!r} is not a valid value (a whole number from 2 to 24)", err["message"])
        self.assertEqual(12, self.stored())                                     # nothing stored ...
        self.assertEqual(12, rt.chains.max_entries)                             # ... nothing applied ...
        self.assertEqual([], self.changed_events())                             # ... nothing journaled
        status, body, _ = self.patch({"participation_rate": "0.20", KEY: 30})   # a refused value refuses the whole save
        self.assertEqual((400, "SETTING_VALUE"), (status, body["error"]["code"]))
        self.assertEqual("0.10", self.settings()["participation_rate"])
        for good, want in ((2, 2), (24, 24), ("7", 7), (9.0, 9), (" 16 ", 16)):  # the edges, and text or 9.0 as participation_rate takes them
            status, body, _ = self.patch({KEY: good})
            self.assertEqual(200, status, body)
            self.assertEqual(want, body[KEY])
            self.assertEqual(want, self.stored())
            self.assertIs(int, type(self.stored()))                             # stored as a JSON integer
            self.assertEqual(want, rt.chains.max_entries)

    def test_live_resize_on_save_and_read_again_at_start(self):
        rt = self.rt
        now = int(rt.clock.epoch())
        for u in FIRST:                                                          # what the quote worker puts: 12 chains
            rt.chains.put(snap(u, now))
        self.assertEqual(FIRST, rt.chains.stats()["cached"])
        self.assertIsNotNone(rt.chains.get("ZZ01"))                             # a mark reads ZZ01: now the most recently used
        status, body, _ = self.patch({KEY: 5}, "ui:chain1071-live-0001")
        self.assertEqual((200, 5), (status, body[KEY]))
        self.assertEqual(5, rt.chains.max_entries)                              # applied at once, no restart
        # 12 -> 5: the 7 least recently used (ZZ02 ZZ03 ZZ04 ZZ05 ZZ06 ZZ07 ZZ08) went; ZZ09 ZZ10 ZZ11 ZZ12 ZZ01 stay
        self.assertEqual(["ZZ09", "ZZ10", "ZZ11", "ZZ12", "ZZ01"], rt.chains.stats()["cached"])
        self.assertEqual(5, rt.worker.stats()["chains"]["max_entries"])         # the quote worker's stats show the limit in force
        self.assertEqual([{"before": {KEY: 12}, "after": {KEY: 5}}], self.changed_events())   # who changed it, from what, to what
        status, body, _ = self.patch({KEY: 20}, "ui:chain1071-live-0002")       # a larger limit keeps every chain still cached
        self.assertEqual((200, 20, 20), (status, body[KEY], rt.chains.max_entries))
        self.assertEqual(["ZZ09", "ZZ10", "ZZ11", "ZZ12", "ZZ01"], rt.chains.stats()["cached"])
        for u in MORE:
            rt.chains.put(snap(u, now))
        self.assertEqual(["ZZ09", "ZZ10", "ZZ11", "ZZ12", "ZZ01"] + MORE, rt.chains.stats()["cached"])   # 5 + 13 = 18 <= 20: nothing went
        status, body, headers = self.patch({KEY: 5}, "ui:chain1071-live-0001")  # a replay of the first save changes nothing
        self.assertEqual((200, "1"), (status, headers.get("X-Replayed")))
        self.assertEqual((20, 20, 20), (body[KEY], self.stored(), rt.chains.max_entries))
        calls = []
        resize = rt.chains.resize
        rt.chains.resize = lambda n: calls.append(n) or resize(n)
        try:
            self.assertEqual(200, self.patch({"participation_rate": "0.20"})[0])
            self.assertEqual([], calls)                                         # a save of another setting leaves the cache alone
            self.assertEqual(200, self.patch({KEY: 20})[0])
            self.assertEqual([20], calls)                                       # the same value again: applied, nothing dropped
        finally:
            del rt.chains.resize
        self.assertEqual(18, len(rt.chains.stats()["cached"]))
        self.restart()
        self.assertEqual(20, self.rt.chains.max_entries)                        # read at server start
        self.assertEqual([], self.rt.chains.stats()["cached"])                  # a new process holds no chain until it fetches one
        self.assertEqual(20, self.settings()[KEY])

    def test_apply_reads_the_stored_value_back(self):
        rt = self.rt
        with rt.db.writer() as tx:
            set_setting(tx.conn, KEY, 6)                                        # stored without a save: nothing applied yet
        self.assertEqual(12, rt.chains.max_entries)
        self.assertEqual(6, _server.apply_chain_cache_setting(rt))
        self.assertEqual(6, rt.chains.max_entries)
        saved, rt.chains = rt.chains, object()                                  # no resizable cache: nothing to apply
        try:
            self.assertIsNone(_server.apply_chain_cache_setting(rt))
        finally:
            rt.chains = saved

    def test_a_refused_stored_value_falls_back_to_twelve(self):
        """A value a save would refuse (written by hand, or by a later release with other bounds) is not applied: 12, logged."""
        for raw, warned in (("99", "99"), ("1", "1"), ('"lots"', "'lots'"), ("12.5", "12.5"), ("not json", None)):
            with self.rt.db.writer() as tx:
                tx.conn.execute("UPDATE settings SET value_json=? WHERE key=?", (raw, KEY))
            self.restart()
            self.assertEqual(12, self.rt.chains.max_entries, raw)
            self.assertEqual(12, self.settings()[KEY], raw)                     # the page shows the number in force
            warns = [m for level, m in self.rt.log.records if level == "WARNING" and KEY in m]
            if warned is None:                                                  # unreadable JSON reads as no row (get_setting): no warning
                self.assertEqual([], warns, raw)
            else:
                self.assertEqual([f"setting chain_cache_entries {warned} is not a whole number from 2 to 24: 12 option chains are kept live"],
                                 warns, raw)


if __name__ == "__main__":
    unittest.main()
