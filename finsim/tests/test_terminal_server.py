"""WP-11 server tests (TERMINAL_DESIGN.md §16, §19.12 "Duplicate server", "Health is lock-free", "Graceful shutdown",
§19.14 first run).  Every server binds port 0 on loopback (never 8765 / 8865), runs offline (FakeProvider, FakeClock)
under a temp home with FINSIM_HOME / FINSIM_TERMINAL_DB pointed there, and is stopped by the test that started it.
"""
from __future__ import annotations

import http.client
import json
import os
import shutil
import tempfile
import threading
import time
import types
import unittest

from tests.terminal_helpers import _Log
from finsim.terminal import config, server as _server
from finsim.terminal.clock import FakeClock
from finsim.terminal.quotes.providers import FakeProvider

AT = "2026-10-20T14:00:00-04:00"


class SlowProvider(FakeProvider):
    """A provider whose every intraday fetch sleeps 5 s (the request thread must never wait for it)."""

    def intraday(self, symbol):
        time.sleep(5.0)
        return super().intraday(symbol)


def point_env(tmp: str) -> None:
    os.environ[config.HOME_ENV] = tmp
    os.environ[config.DB_ENV] = os.path.join(tmp, "terminal.db")


def build(tmp: str, books=None, provider=None, fetch_threads: int = 0):
    point_env(tmp)
    clock = FakeClock(AT)
    fp = provider or FakeProvider(clock)
    return _server.build_runtime(tmp, os.path.join(tmp, "terminal.db"), clock=clock, offline=True, fake_provider=fp, log=_Log(), books=books or [config.TEST_BOOK],
                                 fetch_threads=fetch_threads, news_enabled=False)


class ServerThread:
    """``serve`` on a thread with port 0; ``start`` waits for the ready callback; ``stop`` sets the stop event and joins."""

    def __init__(self, tmp: str, msgs=None):
        self.tmp, self.msgs, self.rt, self.rc = tmp, msgs if msgs is not None else [], None, None
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        clock = FakeClock(AT)
        self.rc = _server.serve(port=0, home=self.tmp, db=os.path.join(self.tmp, "terminal.db"), offline=True, ready=self._ready, out=self.msgs.append, clock=clock,
                                fake_provider=FakeProvider(clock), books=[config.TEST_BOOK], fetch_threads=0, supervisor_interval=0.2, log=_Log())

    def _ready(self, rt):
        self.rt = rt
        self.ready.set()

    def start(self, timeout: float = 60.0) -> "ServerThread":
        self.thread.start()
        if not self.ready.wait(timeout) and self.thread.is_alive():
            raise RuntimeError("the server did not become ready")
        return self

    @property
    def port(self) -> int:
        return int(self.rt.httpd.server_address[1])

    def stop(self, timeout: float = 10.0) -> int:
        if self.rt is not None:
            self.rt.stop_event.set()
        self.thread.join(timeout)
        return self.rc


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-server-")
        point_env(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- duplicate server / exclusive bind -----------------------------------------------------------------------------
    def test_duplicate_server_exit_3(self):
        first = ServerThread(self.tmp).start()
        try:
            self.assertEqual(0, first.rc if first.rc is not None else 0)
            self.assertTrue(os.path.exists(os.path.join(self.tmp, "terminal.lock")))
            info = _server.InstanceLock(os.path.join(self.tmp, "terminal.lock")).read()
            version = __import__("finsim.terminal", fromlist=["VERSION"]).VERSION
            self.assertEqual((os.getpid(), first.port, version), (info["pid"], info["port"], info["version"]))   # readable while the byte lock is held
            self.assertEqual(_server.code_tree(), info["code"])                   # STARTUP-03: the code tree that runs
            self.assertTrue(os.path.isdir(os.path.join(info["code"], "finsim", "terminal")))
            msgs = []
            clock = FakeClock(AT)
            rc2 = _server.serve(port=0, home=self.tmp, db=os.path.join(self.tmp, "terminal.db"), offline=True, out=msgs.append, clock=clock,
                                fake_provider=FakeProvider(clock), books=[config.TEST_BOOK], fetch_threads=0, log=_Log())
            self.assertEqual(3, rc2)                                               # the lock refuses it ...
            self.assertFalse(os.path.exists(os.path.join(self.tmp, "backups")))     # ... before the database was opened (open() backs up first)
            # STARTUP-03: who runs, since when (New York time), where to open it, which code tree, and what to do instead
            since = __import__("finsim.terminal.clock", fromlist=["ny_str"]).ny_str(int(info["started_ts"]))
            self.assertEqual([f"FinSim is already running: finsim-terminal {version}, PID {os.getpid()}, since {since}, http://127.0.0.1:{first.port}/ "
                              f"(code {_server.code_tree()}). Open it with `python -m finsim open` or the FinSim desktop icon. To run this copy instead: "
                              "`python -m finsim stop`, then `python -m finsim serve`."], msgs)
            self.assertTrue(since.endswith(" ET"))
        finally:
            rc = first.stop()
        self.assertEqual(0, rc)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "terminal.lock")))
        second = ServerThread(self.tmp).start()                                    # after the first stops, the second starts
        self.assertTrue(second.ready.is_set())
        self.assertEqual(0, second.stop())

    def test_already_running_message_of_an_older_lock(self):
        """STARTUP-03: a lock written by an older release has no `code`: the clause is left out; an unreadable lock still says what to do."""
        msg = _server.already_running_message({"pid": 17272, "port": 8765, "started_ts": 1792631054, "version": "1.0.3.1"})
        # 1792631054 = 2026-10-22 01:04:14 UTC = 2026-10-21 21:04:14 ET (EDT, UTC-4)
        self.assertEqual("FinSim is already running: finsim-terminal 1.0.3.1, PID 17272, since 2026-10-21 21:04:14 ET, http://127.0.0.1:8765/. "
                         "Open it with `python -m finsim open` or the FinSim desktop icon. To run this copy instead: `python -m finsim stop`, "
                         "then `python -m finsim serve`.", msg)
        self.assertNotIn("(code", msg)
        msg = _server.already_running_message({})
        self.assertTrue(msg.startswith("FinSim is already running: finsim-terminal (version unknown), PID ?. Open it with `python -m finsim open`"), msg)
        self.assertNotIn("None", msg)

    def test_port_taken_by_another_program(self):
        """STARTUP-03: a port bound by another program is said in words - no FinSim lock in this home, what to run - never with the refused
        process's own pid, and nothing is opened or written (no database, no backup, the lock released)."""
        import socket
        holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        port = holder.getsockname()[1]
        try:
            msgs = []
            clock = FakeClock(AT)
            rc = _server.serve(port=port, home=self.tmp, db=os.path.join(self.tmp, "terminal.db"), offline=True, out=msgs.append, clock=clock,
                               fake_provider=FakeProvider(clock), books=[config.TEST_BOOK], fetch_threads=0, log=_Log())
        finally:
            holder.close()
        self.assertEqual(3, rc)
        self.assertEqual([f"port {port} is used by another program (no FinSim lock in {self.tmp}); run `python -m finsim status` or pass --port"], msgs)
        self.assertNotIn(str(os.getpid()), msgs[0])
        self.assertNotIn("PID", msgs[0])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "terminal.db")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "backups")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "terminal.lock")))   # released and removed

    def test_exclusive_bind_oserror(self):
        rt = build(self.tmp)
        try:
            s1 = _server.TerminalServer(("127.0.0.1", 0), _server.make_handler(rt))
            try:
                port = s1.server_address[1]
                self.assertFalse(_server.TerminalServer.allow_reuse_address)
                with self.assertRaises(OSError):
                    _server.TerminalServer(("127.0.0.1", port), _server.make_handler(rt))
            finally:
                s1.server_close()
        finally:
            rt.close()

    # ---- health is lock-free; reads never wait on the network ---------------------------------------------------------
    def test_health_under_a_2s_writer_lock(self):
        rt = build(self.tmp)
        try:
            held = threading.Event()

            def hold():
                rt.db.acquire_writer()
                held.set()
                time.sleep(2.0)
                rt.db.release_writer()
            t = threading.Thread(target=hold)
            t.start()
            held.wait(2)
            t0 = time.perf_counter()
            status, body, _ = rt.router.handle("GET", "/api/health")
            dt = time.perf_counter() - t0
            t.join()
            self.assertEqual(200, status)
            self.assertEqual("finsim-terminal", body["app"])
            self.assertLess(dt, 0.05, f"health took {dt:.3f}s while the writer lock was held")
        finally:
            rt.close()

    def test_accounts_answer_while_the_provider_sleeps_5s(self):
        clock = FakeClock(AT)
        slow = SlowProvider(clock)
        now = clock.epoch()
        slow.set("AAPL", last="339.50", quote_time=now - 120, bar=(now - 180, 61900))
        rt = build(self.tmp, provider=slow, fetch_threads=1)
        try:
            rt.cache.subscribe(["AAPL"], 300, "PAGE")
            rt.worker.start()                                                       # the fetch (5 s) happens on the worker's pool, not here
            time.sleep(0.3)
            t0 = time.perf_counter()
            status, rows, _ = rt.router.handle("GET", "/api/accounts?book=T")
            dt = time.perf_counter() - t0
            self.assertEqual(200, status)
            self.assertEqual("T1:TREASURY", rows[0]["account_id"])
            self.assertLess(dt, 0.2, f"GET /api/accounts took {dt:.3f}s while a fetch was in flight")
            t0 = time.perf_counter()
            status, q, _ = rt.router.handle("GET", "/api/quotes?ids=AAPL")
            self.assertEqual(200, status)
            self.assertLess(time.perf_counter() - t0, 0.2)
            self.assertNotIn("AAPL", q["quotes"])                                   # not observed yet: absent, never invented
        finally:
            rt.close()

    # ---- graceful shutdown ----------------------------------------------------------------------------------------------
    def test_shutdown_within_3s_and_lock_released(self):
        srv = ServerThread(self.tmp).start()
        conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
        conn.request("GET", "/api/health")
        r = conn.getresponse()
        h = json.loads(r.read())
        self.assertEqual((200, "finsim-terminal", True), (r.status, h["app"], h["offline"]))
        self.assertTrue(h["workers"]["quotes"]["alive"])
        self.assertTrue(h["workers"]["matcher"]["alive"])
        self.assertTrue(h["workers"]["supervisor"]["alive"])
        self.assertEqual(srv.port, h["port"])
        t0 = time.perf_counter()
        conn.request("POST", "/api/shutdown", body="{}", headers={"Content-Type": "application/json"})
        r = conn.getresponse()
        self.assertEqual((200, {"shutting_down": True}), (r.status, json.loads(r.read())))
        conn.close()
        srv.thread.join(3.0)
        dt = time.perf_counter() - t0
        self.assertFalse(srv.thread.is_alive(), f"the server did not stop within 3 s ({dt:.2f}s)")
        self.assertEqual(0, srv.rc)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "terminal.lock")))
        lock = _server.InstanceLock(os.path.join(self.tmp, "terminal.lock"))
        self.assertTrue(lock.acquire())                                              # released: a new holder may take it
        lock.release()
        self.assertFalse(_server.thread_alive(srv.rt.worker))                      # (is_alive() itself raises on 3.12: the core's threads shadow Thread._stop)
        self.assertFalse(_server.thread_alive(srv.rt.matcher))
        self.assertIn("stopped", srv.msgs[-1])

    def test_off_loopback_shutdown_403(self):
        rt = build(self.tmp)
        try:
            status, body, _ = rt.router.handle("POST", "/api/shutdown", None, {}, "10.0.0.5")
            self.assertEqual((403, "FORBIDDEN"), (status, body["error"]["code"]))
            self.assertFalse(rt.stop_event.is_set())
            status, body, _ = rt.router.handle("POST", "/api/shutdown", None, {}, "127.0.0.1")
            self.assertEqual((200, True), (status, body["shutting_down"]))
            self.assertTrue(rt.stop_event.is_set())
        finally:
            rt.close()

    # ---- supervisor --------------------------------------------------------------------------------------------------------
    def test_supervisor_restarts_a_killed_worker(self):
        rt = build(self.tmp)
        try:
            rt.matcher.start()
            old = rt.matcher
            self.assertEqual({"restarted": [], "stalled": [], "recovered": []}, rt.supervisor.check())
            old.stop()
            for _ in range(60):                                                     # old.join() raises on 3.12 (the core's Matcher shadows Thread._stop)
                if not _server.thread_alive(old):
                    break
                time.sleep(0.05)
            self.assertFalse(_server.thread_alive(old))
            out = rt.supervisor.check()
            self.assertEqual(["matcher"], out["restarted"])
            self.assertIsNot(old, rt.matcher)
            self.assertTrue(_server.thread_alive(rt.matcher))
            self.assertIs(rt.matcher, rt.worker.matcher)                              # the quote worker's hook follows the restart
            with rt.db.reader() as conn:
                rows = conn.execute("SELECT severity, source, code FROM attention WHERE code='WORKER_DIED'").fetchall()
            self.assertEqual([("ERROR", "supervisor", "WORKER_DIED")], [tuple(r) for r in rows])
            status, h, _ = rt.router.handle("GET", "/api/health")
            self.assertTrue(h["workers"]["matcher"]["alive"])
            self.assertEqual(1, h["attention_open"])
            # a stalled worker: a tick 100 s ago is more than 3 x 15 s -> WARN once, recovered when it ticks again
            rt.matcher.last_tick_ts = rt.clock.epoch() - 100
            self.assertEqual(["matcher"], rt.supervisor.check()["stalled"])
            self.assertEqual([], rt.supervisor.check()["stalled"])
            rt.matcher.last_tick_ts = rt.clock.epoch()
            self.assertEqual(["matcher"], rt.supervisor.check()["recovered"])
        finally:
            rt.close()

    def test_supervisor_counts_the_quote_worker_progress_inside_a_long_tick(self):
        """V104R2-BW-10: the quote worker's first tick after a start runs every startup slot (about a minute on the owner's book); its
        last tick is then 100 s old while it is busy.  The supervisor and health count its progress inside the tick (last_progress_ts,
        5 s ago here): not stalled.  With no progress for 50 s (more than 3 x 15 s) it is stalled - one WARN, as before."""
        rt = build(self.tmp)
        real = rt.worker
        try:
            now = int(rt.clock.epoch())
            st = {"last_tick_ts": now - 100, "last_progress_ts": now - 5, "loop_errors": 0}
            rt.worker = types.SimpleNamespace(ident=1, is_alive=lambda: True, stats=lambda: dict(st))
            self.assertEqual([], rt.supervisor.check(now)["stalled"])
            status, h, _ = rt.router.handle("GET", "/api/health")
            self.assertEqual((200, False, now - 5), (status, h["workers"]["quotes"]["stalled"], h["workers"]["quotes"]["last_progress_ts"]))
            st["last_progress_ts"] = now - 50
            self.assertEqual(["quotes"], rt.supervisor.check(now)["stalled"])
            with rt.db.reader() as conn:
                rows = conn.execute("SELECT severity, code FROM attention WHERE code='WORKER_STALLED'").fetchall()
            self.assertEqual([("WARN", "WORKER_STALLED")], [tuple(r) for r in rows])
            status, h, _ = rt.router.handle("GET", "/api/health")
            self.assertTrue(h["workers"]["quotes"]["stalled"])
        finally:
            rt.worker = real
            rt.close()

    # ---- offline guard --------------------------------------------------------------------------------------------------------
    def test_offline_on_the_default_home_refused(self):
        saved = {k: os.environ.pop(k, None) for k in (config.HOME_ENV, config.DB_ENV)}
        try:
            msgs = []
            rc = _server.serve(port=0, home=None, db=None, offline=True, out=msgs.append, log=_Log())
            self.assertEqual(5, rc)
            self.assertIn("refused", msgs[0])
            self.assertFalse(os.path.exists(os.path.join(_server.default_home(), "terminal.lock")) and False)   # nothing of ours was created there
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v
        self.assertIsNotNone(_server.offline_refusal(_server.default_home(), os.path.join(_server.default_home(), "terminal.db")))
        self.assertIsNotNone(_server.offline_refusal(self.tmp, os.path.join(_server.default_home(), "terminal.db")))    # a scratch home with the real db: refused
        self.assertIsNone(_server.offline_refusal(self.tmp, os.path.join(self.tmp, "terminal.db")))

    # ---- first run ------------------------------------------------------------------------------------------------------------
    def test_first_run_creates_p1_f1_i1(self):
        rt = build(self.tmp, books=list(config.BOOKS))
        try:
            with rt.db.reader() as conn:
                lids = [r[0] for r in conn.execute("SELECT lifetime_id FROM lifetimes ORDER BY lifetime_id").fetchall()]
                rids = [r[0] for r in conn.execute("SELECT request_id FROM commands WHERE request_id LIKE 'init:capital:%' ORDER BY request_id").fetchall()]
                accounts = {r[0] for r in conn.execute("SELECT account_id FROM accounts").fetchall()}
            self.assertEqual(["F1", "I1", "P1"], lids)
            self.assertEqual(["init:capital:F1", "init:capital:I1", "init:capital:P1"], rids)
            self.assertIn("I1:PRIVATE", accounts)                                   # INSTITUTION: Private Markets from the start
            self.assertNotIn("P1:PRIVATE", accounts)                                # 1,000,000 < 5,000,000
            # 1,000,000 + 25,000,000 + 1,000,000,000 = 1,026,000,000.00
            self.assertEqual(102_600_000_000, rt.accounting.nav("ALL_BOOKS").nav_c)
            status, books, _ = rt.router.handle("GET", "/api/books")
            self.assertEqual(["P", "F", "I"], [b["book_code"] for b in books])
            self.assertEqual(["P1", "F1", "I1"], [b["lifetime"]["lifetime_id"] for b in books])
            self.assertEqual("1000000000.00", books[2]["nav"]["value"])
            status, rows, _ = rt.router.handle("GET", "/api/accounts?book=I")
            self.assertEqual("I1:TREASURY", rows[0]["account_id"])
            self.assertEqual("1000000000.00", rows[0]["nav"])
            # a second build on the same file re-posts nothing
            rt.close()
            rt = build(self.tmp, books=list(config.BOOKS))
            with rt.db.reader() as conn:
                self.assertEqual(3, conn.execute("SELECT COUNT(*) FROM commands WHERE request_id LIKE 'init:capital:%'").fetchone()[0])
        finally:
            rt.close()


if __name__ == "__main__":
    unittest.main()
