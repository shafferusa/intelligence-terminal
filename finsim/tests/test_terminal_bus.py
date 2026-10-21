"""WP-00 CommandBus tests (TERMINAL_DESIGN.md §4, §19.12): rollback, replay, reuse, busy, light handlers, integrity rollback (D12)."""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
import threading
import time
import unittest

from tests.terminal_helpers import TREASURY, US_EXCHANGE, assert_integrity, bal, deposit, execute, fund, make_runtime, post_entry, rid
from finsim.terminal.commands import Command


class BusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-bus-")
        self.rt = make_runtime(self.tmp)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _count(self, sql, params=()):
        with self.rt.db.reader() as conn:
            return conn.execute(sql, params).fetchone()[0]

    def test_rollback_on_failing_handler(self):
        # a handler posts one valid entry then raises: no journal entry, no commands row, 500, one attention row
        n_entries = self._count("SELECT COUNT(*) FROM journal_entries")
        request_id = rid()
        r = post_entry(self.rt, [[TREASURY, "1230", "USD", 100], [TREASURY, "4300", "USD", -100, "AAPL"]], "t:fail", kind="ACCRUAL", raise_after=True)
        self.assertEqual(500, r.http)
        self.assertEqual("COMMAND_FAILED", r.code)
        self.assertEqual(n_entries, self._count("SELECT COUNT(*) FROM journal_entries"))
        self.assertEqual(0, self._count("SELECT COUNT(*) FROM commands WHERE request_id=?", (r.body["request_id"],)))
        self.assertEqual(1, self._count("SELECT COUNT(*) FROM attention WHERE source='bus' AND code='COMMAND_FAILED' AND severity='ERROR'"))
        self.assertEqual(0, bal(self.rt, TREASURY, "1230"))
        r2 = execute(self.rt, "test.fail", {"message": "kaboom"}, request_id)
        self.assertEqual(500, r2.http)
        self.assertIn("kaboom", r2.message)
        self.assertEqual(0, self._count("SELECT COUNT(*) FROM commands WHERE request_id=?", (request_id,)))

    def test_d12_corrupted_balance_rolls_back(self):
        # the handler posts Dr 1230 / Cr 4300 100.00 then adds 1 cent to gl_balances 1010 of Treasury: L3 fails before COMMIT
        n_entries = self._count("SELECT COUNT(*) FROM journal_entries")
        request_id = rid()
        r = execute(self.rt, "test.post", {"kind": "ACCRUAL", "memo": "x", "idem_key": "t:d12",
                                           "lines": [[TREASURY, "1230", "USD", 10000], [TREASURY, "4300", "USD", -10000, "AAPL"]],
                                           "corrupt": [TREASURY, "1010", "USD"]}, request_id)
        self.assertEqual(500, r.http)
        self.assertEqual("INTEGRITY", r.code)
        self.assertIn("L3", [p["id"] for p in r.body["error"]["details"]["problems"]])
        self.assertEqual(n_entries, self._count("SELECT COUNT(*) FROM journal_entries"))
        self.assertEqual(0, self._count("SELECT COUNT(*) FROM commands WHERE request_id=?", (request_id,)))
        self.assertEqual(1, self._count("SELECT COUNT(*) FROM attention WHERE source='bus' AND code='INTEGRITY' AND severity='ERROR'"))
        self.assertEqual(1000000000, bal(self.rt, TREASURY, "1010"))       # 10,000,000.00 untouched
        assert_integrity(self, self.rt, "full")
        # retrying the same request_id after the cause is removed succeeds once
        r2 = execute(self.rt, "test.post", {"kind": "ACCRUAL", "memo": "x", "idem_key": "t:d12",
                                            "lines": [[TREASURY, "1230", "USD", 10000], [TREASURY, "4300", "USD", -10000, "AAPL"]]}, request_id)
        self.assertTrue(r2.ok, r2.body)
        self.assertFalse(r2.replayed)
        self.assertEqual(10000, bal(self.rt, TREASURY, "1230"))

    def test_replay_and_reuse(self):
        r1 = deposit(self.rt, 250000000, request_id="ui:dep-0001")      # 2,500,000.00
        self.assertTrue(r1.ok, r1.body)
        self.assertEqual(1250000000, bal(self.rt, TREASURY, "1010"))
        r2 = deposit(self.rt, 250000000, request_id="ui:dep-0001")
        self.assertTrue(r2.ok)
        self.assertTrue(r2.replayed)
        self.assertEqual(r1.body, r2.body)
        self.assertEqual(r1.command_id, r2.command_id)
        self.assertEqual(1250000000, bal(self.rt, TREASURY, "1010"))     # nothing posted twice
        r3 = deposit(self.rt, 100, request_id="ui:dep-0001")
        self.assertEqual(409, r3.http)
        self.assertEqual("REQUEST_ID_REUSED", r3.code)
        self.assertEqual("treasury.deposit", r3.body["error"]["details"]["stored_kind"])
        self.assertEqual(1250000000, bal(self.rt, TREASURY, "1010"))
        # a rejected command (400) stores nothing, so its id can be reused
        r4 = execute(self.rt, "treasury.deposit", {"lifetime_id": "T1", "currency": "USD"}, "ui:dep-0002")
        self.assertEqual(400, r4.http)
        self.assertEqual("AMOUNT", r4.code)
        self.assertEqual(0, self._count("SELECT COUNT(*) FROM commands WHERE request_id='ui:dep-0002'"))
        r5 = deposit(self.rt, 100, request_id="ui:dep-0002")
        self.assertTrue(r5.ok, r5.body)

    def test_bad_request_id_and_unknown_command(self):
        r = execute(self.rt, "treasury.deposit", {"lifetime_id": "T1", "amount_c": 100}, "short")
        self.assertEqual(400, r.http)
        self.assertEqual("BAD_REQUEST_ID", r.code)
        r = execute(self.rt, "treasury.deposit", {"lifetime_id": "T1", "amount_c": 100}, "ui:" + "x" * 90)
        self.assertEqual("BAD_REQUEST_ID", r.code)
        r = execute(self.rt, "nope.nothing", {}, rid())
        self.assertEqual(400, r.http)
        self.assertEqual("UNKNOWN_COMMAND", r.code)
        self.assertEqual(1, self._count("SELECT COUNT(*) FROM commands"))     # only the init

    def test_light_handler_skips_integrity_and_writes_event(self):
        # corrupt a balance by hand: a light handler still runs (no scoped check); a ledger handler is refused by L3
        with self.rt.db.writer() as tx:
            tx.x("UPDATE gl_balances SET balance_c = balance_c + 1 WHERE account_id=? AND gl='1010' AND currency='USD'", (TREASURY,))
        r = execute(self.rt, "account.update_settings", {"account_id": US_EXCHANGE, "sweep_out": 0, "sweep_buffer_c": 500000})
        self.assertTrue(r.ok, r.body)
        self.assertEqual({"sweep_out": 1, "sweep_buffer_c": 1000000}, r.body["before"])
        self.assertEqual({"sweep_out": 0, "sweep_buffer_c": 500000}, r.body["after"])
        self.assertEqual(1, self._count("SELECT COUNT(*) FROM events WHERE type='SETTINGS_CHANGED' AND account_id=?", (US_EXCHANGE,)))
        r2 = fund(self.rt, US_EXCHANGE, 100)
        self.assertEqual(500, r2.http)
        self.assertEqual("INTEGRITY", r2.code)
        with self.rt.db.writer() as tx:
            tx.x("UPDATE gl_balances SET balance_c = balance_c - 1 WHERE account_id=? AND gl='1010' AND currency='USD'", (TREASURY,))
        self.assertTrue(fund(self.rt, US_EXCHANGE, 100).ok)

    def test_writer_busy_503(self):
        self.rt.bus.lock_timeout = 0.2
        held = threading.Event()
        release = threading.Event()

        def holder():
            self.rt.db.acquire_writer(5.0)
            held.set()
            release.wait(5.0)
            self.rt.db.release_writer()
        t = threading.Thread(target=holder, daemon=True)
        t.start()
        self.assertTrue(held.wait(2.0))
        try:
            r = deposit(self.rt, 100, request_id="ui:busy-0001")
        finally:
            release.set()
            t.join(2.0)
        self.assertEqual(503, r.http)
        self.assertEqual("WRITER_BUSY", r.code)
        self.assertEqual(1, r.retry_after)
        self.assertEqual(0, self._count("SELECT COUNT(*) FROM commands WHERE request_id='ui:busy-0001'"))
        self.rt.bus.lock_timeout = 10.0
        self.assertTrue(deposit(self.rt, 100, request_id="ui:busy-0001").ok)

    def test_retry_503_when_begin_is_busy(self):
        real = self.rt.db.writer_conn()

        class Flaky:
            def __init__(self, conn):
                self._c = conn
                self.failed = False

            def execute(self, sql, params=()):
                if sql == "BEGIN IMMEDIATE" and not self.failed:
                    self.failed = True
                    raise sqlite3.OperationalError("database is locked")
                return self._c.execute(sql, params)

            def __getattr__(self, name):
                return getattr(self._c, name)
        flaky = Flaky(real)
        self.rt.db.writer_conn = lambda: flaky
        try:
            r = deposit(self.rt, 100, request_id="ui:retry-0001")
            self.assertEqual(503, r.http)
            self.assertEqual("RETRY", r.code)
            self.assertEqual(1, r.retry_after)
            r2 = deposit(self.rt, 100, request_id="ui:retry-0001")
            self.assertTrue(r2.ok, r2.body)
            self.assertFalse(r2.replayed)
        finally:
            del self.rt.db.writer_conn

    def test_on_commit_listener_and_state_version(self):
        seen = []
        self.rt.bus.on_commit(lambda cmd, res: seen.append((cmd.kind, res.ok, res.command_id)))
        sv0 = self.rt.db.state_version()
        r = deposit(self.rt, 100)
        self.assertEqual([("treasury.deposit", True, r.command_id)], seen)
        self.assertEqual(sv0 + 1, self.rt.db.state_version())
        execute(self.rt, "treasury.deposit", {"lifetime_id": "T1"}, rid())      # rejected: no listener call, no version bump
        self.assertEqual(1, len(seen))
        self.assertEqual(sv0 + 1, self.rt.db.state_version())
        with self.rt.db.reader() as conn:
            row = conn.execute("SELECT state_version, http_status, duration_ms FROM commands WHERE id=?", (r.command_id,)).fetchone()
        self.assertEqual((sv0 + 1, 200), (row["state_version"], row["http_status"]))

    def test_duplicate_posting_409(self):
        r1 = post_entry(self.rt, [[TREASURY, "1230", "USD", 100], [TREASURY, "4300", "USD", -100, "AAPL"]], "t:dup", kind="ACCRUAL")
        self.assertTrue(r1.ok, r1.body)
        r2 = post_entry(self.rt, [[TREASURY, "1230", "USD", 100], [TREASURY, "4300", "USD", -100, "AAPL"]], "t:dup", kind="ACCRUAL")
        self.assertEqual(409, r2.http)
        self.assertEqual("DUPLICATE_POSTING", r2.code)
        self.assertEqual(100, bal(self.rt, TREASURY, "1230"))
        self.assertEqual(0, self._count("SELECT COUNT(*) FROM commands WHERE request_id=?", (r2.body["request_id"],)))

    def test_touched_keys_recorded(self):
        captured = {}

        def h(ctx, payload):
            from finsim.terminal.ledger import Entry, cr, dr
            ctx.post(Entry("ACCRUAL", "m", [dr(TREASURY, "1230", "USD", 5), cr(TREASURY, "4300", "USD", 5, instrument_id="AAPL")], "t:touch"))
            captured["accounts"] = set(ctx.touched_accounts)
            captured["instruments"] = set(ctx.touched_instruments)
            captured["entries"] = list(ctx.touched_entries)
            return {"ok": 1}
        self.rt.bus.register("test.touch", h)
        r = execute(self.rt, "test.touch", {})
        self.assertTrue(r.ok, r.body)
        self.assertEqual({TREASURY}, captured["accounts"])
        self.assertEqual({(TREASURY, "AAPL")}, captured["instruments"])
        self.assertEqual(1, len(captured["entries"]))


if __name__ == "__main__":
    unittest.main()
