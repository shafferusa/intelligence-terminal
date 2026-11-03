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
# the 12 option underlyings held on 2026-10-29 (CHANGE_PLAN section 1), then 13 more names: 25 in all
HELD = ["AAPL", "DLR", "GLD", "IBIT", "META", "NVDA", "ORCL", "QQQ", "TLT", "TSLA", "USO", "XHB"]
MORE = ["SPY", "IWM", "MSFT", "AMZN", "GOOGL", "AMD", "XLE", "XLF", "EEM", "SLV", "HYG", "SMH", "COIN"]
ALL = HELD + MORE


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
        self.fill(c, ["AAPL", "NVDA", "GLD"])
        self.assertEqual(["NVDA", "GLD"], c.stats()["cached"])                  # 3 chains into 2 slots: the first (AAPL) went

    def test_eviction_order_is_least_recently_used(self):
        c = ChainCache(self.clock, max_entries=3)
        self.fill(c, ["AAPL", "NVDA", "GLD"])                                   # oldest first: AAPL, NVDA, GLD
        self.assertIsNotNone(c.get("AAPL"))                                     # a read (fresh: fetched now) refreshes AAPL: NVDA, GLD, AAPL
        self.assertIsNotNone(c.peek("NVDA"))                                    # peek never refreshes
        c.put(snap("IBIT", self.now))                                           # 4 > 3: NVDA, the least recently used, goes
        self.assertEqual(["GLD", "AAPL", "IBIT"], c.stats()["cached"])
        c.put(snap("GLD", self.now))                                            # a new snapshot of a cached name refreshes it; nothing goes
        self.assertEqual(["AAPL", "IBIT", "GLD"], c.stats()["cached"])
        c.put(snap("META", self.now))                                           # AAPL goes
        self.assertEqual(["IBIT", "GLD", "META"], c.stats()["cached"])
        self.assertIsNone(c.peek("NVDA"))
        self.assertIsNone(c.peek("AAPL"))

    def test_default_twelve_holds_the_twelve_held_underlyings(self):
        c = ChainCache(self.clock, max_entries=config.CHAIN_CACHE_ENTRIES)
        self.fill(c, HELD)
        self.assertEqual(HELD, c.stats()["cached"])                             # all 12 stay (2 slots kept only USO and XHB)
        c.put(snap("SPY", self.now))                                            # the 13th drops the oldest, AAPL
        self.assertEqual(HELD[1:] + ["SPY"], c.stats()["cached"])

    def test_resize_smaller_drops_the_oldest_at_once(self):
        c = ChainCache(self.clock, max_entries=12)
        self.fill(c, HELD)
        self.assertIsNotNone(c.get("DLR"))                                      # DLR becomes the most recently used
        dropped = c.resize(4)
        # 12 -> 4: the 8 least recently used go at once, oldest first; TSLA, USO, XHB and DLR stay
        self.assertEqual(["AAPL", "GLD", "IBIT", "META", "NVDA", "ORCL", "QQQ", "TLT"], dropped)
        self.assertEqual(["TSLA", "USO", "XHB", "DLR"], c.stats()["cached"])
        self.assertEqual(4, c.max_entries)
        c.put(snap("SPY", self.now))                                            # later puts keep the new limit: TSLA goes
        self.assertEqual(["USO", "XHB", "DLR", "SPY"], c.stats()["cached"])

    def test_resize_larger_keeps_every_chain(self):
        c = ChainCache(self.clock)                                              # 2
        self.fill(c, ALL[:2])
        self.assertEqual([], c.resize(24))                                      # growing drops nothing
        self.assertEqual(ALL[:2], c.stats()["cached"])
        self.fill(c, ALL[2:24])
        self.assertEqual(ALL[:24], c.stats()["cached"])                         # 24 chains, all kept
        c.put(snap(ALL[24], self.now))                                          # the 25th drops the oldest, AAPL
        self.assertEqual(ALL[1:25], c.stats()["cached"])
        self.assertEqual(24, c.stats()["max_entries"])

    def test_resize_same_limit_no_op_and_a_limit_below_one_raises(self):
        log = _Log()
        c = ChainCache(self.clock, max_entries=12, log=log)
        self.fill(c, HELD[:3])
        self.assertEqual([], c.resize(12))
        self.assertEqual([], log.records)                                       # nothing changed: no log line
        for bad in (0, -1):
            with self.assertRaises(ValueError):
                c.resize(bad)
        self.assertEqual(12, c.max_entries)
        self.assertEqual(HELD[:3], c.stats()["cached"])
        self.assertEqual(["AAPL"], c.resize(2))
        self.assertEqual([("INFO", "option chains kept live: 12 -> 2 (dropped AAPL)")], log.records)
        self.assertEqual(2, c.stats()["max_entries"])

    def test_resize_keeps_fetches_pins_and_errors(self):
        """Only snapshots go: an in-flight fetch still resolves its waiters, and a dropped chain that is pinned is queued again
        when its 90 s refresh is due."""
        c = ChainCache(self.clock, max_entries=4)
        self.fill(c, ["AAPL", "NVDA", "GLD", "IBIT"])
        c.pin("AAPL")
        f = c.request("TSLA")                                                   # not cached: one fetch queued
        self.assertEqual("TSLA", c.take_pending())                              # the worker takes it: in flight
        c.fail("ORCL", RuntimeError("HTTP 503"))
        self.assertEqual(["AAPL", "NVDA"], c.resize(2))
        st = c.stats()
        self.assertEqual((["GLD", "IBIT"], ["AAPL"], "TSLA", {"ORCL": "HTTP 503"}), (st["cached"], st["pinned"], st["in_flight"], st["errors"]))
        self.assertIsNone(c.peek("AAPL"))
        self.clock.advance(90)                                                  # put set AAPL's next refresh at fetch + 90 s
        self.assertEqual(["AAPL"], c.due())
        self.assertEqual(["AAPL"], c.pending())                                 # nothing cached any more, so it is fetched again
        tsla = snap("TSLA", int(self.clock.epoch()))
        c.put(tsla)                                                             # the in-flight fetch lands: GLD (oldest) goes
        self.assertIs(tsla, f.result(timeout=0))
        self.assertEqual(["IBIT", "TSLA"], c.stats()["cached"])

    def test_resize_takes_the_cache_lock(self):
        c = ChainCache(self.clock, max_entries=4)
        self.fill(c, ["AAPL", "NVDA", "GLD", "IBIT"])
        out = []
        t = threading.Thread(target=lambda: out.append(c.resize(2)), daemon=True)
        with c._lock:                                                           # another thread (the quote worker) is inside the cache
            t.start()
            t.join(0.3)
            self.assertTrue(t.is_alive())                                       # resize waits for the lock ...
            self.assertEqual(4, c.max_entries)                                  # ... and has changed nothing yet
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertEqual([["AAPL", "NVDA"]], out)
        self.assertEqual(["GLD", "IBIT"], c.stats()["cached"])


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
        for u in HELD:                                                          # what the quote worker puts: the 12 held names' chains
            rt.chains.put(snap(u, now))
        self.assertEqual(HELD, rt.chains.stats()["cached"])
        self.assertIsNotNone(rt.chains.get("AAPL"))                             # a mark reads AAPL: now the most recently used
        status, body, _ = self.patch({KEY: 5}, "ui:chain1071-live-0001")
        self.assertEqual((200, 5), (status, body[KEY]))
        self.assertEqual(5, rt.chains.max_entries)                              # applied at once, no restart
        # 12 -> 5: the 7 least recently used (DLR GLD IBIT META NVDA ORCL QQQ) went; TLT TSLA USO XHB AAPL stay
        self.assertEqual(["TLT", "TSLA", "USO", "XHB", "AAPL"], rt.chains.stats()["cached"])
        self.assertEqual(5, rt.worker.stats()["chains"]["max_entries"])         # the quote worker's stats show the limit in force
        self.assertEqual([{"before": {KEY: 12}, "after": {KEY: 5}}], self.changed_events())   # who changed it, from what, to what
        status, body, _ = self.patch({KEY: 20}, "ui:chain1071-live-0002")       # a larger limit keeps every chain still cached
        self.assertEqual((200, 20, 20), (status, body[KEY], rt.chains.max_entries))
        self.assertEqual(["TLT", "TSLA", "USO", "XHB", "AAPL"], rt.chains.stats()["cached"])
        for u in MORE:
            rt.chains.put(snap(u, now))
        self.assertEqual(["TLT", "TSLA", "USO", "XHB", "AAPL"] + MORE, rt.chains.stats()["cached"])   # 5 + 13 = 18 <= 20: nothing went
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
