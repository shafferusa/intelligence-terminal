"""WP-00 Treasury tests (TERMINAL_DESIGN.md §19.1 and the transfer part of §19.2; D1)."""
from __future__ import annotations

import shutil
import tempfile
import unittest

from tests.terminal_helpers import (CREDIT, T1, TREASURY, US_EXCHANGE, assert_integrity, bal, buy, deposit, execute, fund, make_runtime, rid)
from finsim.terminal import treasury
from finsim.terminal.commands import ConflictError


class TreasuryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-tre-")
        self.rt = make_runtime(self.tmp)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _count(self, sql, params=()):
        with self.rt.db.reader() as conn:
            return conn.execute(sql, params).fetchone()[0]

    def test_d1_initial_capital_and_deposit(self):
        # Start: init:capital:T1 -> TREASURY 1010 USD 10,000,000.00; 3000 10,000,000.00; NAV(ALL) 10,000,000.00; P&L ITD 0.00
        self.assertEqual(1000000000, bal(self.rt, TREASURY, "1010"))
        self.assertEqual(1000000000, bal(self.rt, TREASURY, "3000"))
        self.assertEqual(1000000000, self.rt.accounting.nav("ALL").nav_c)
        self.assertEqual(1000000000, self.rt.accounting.nav("T1:ALL").nav_c)
        itd = self.rt.accounting.pnl("ALL", "ITD")
        self.assertEqual((0, 1000000000, 0), (itd.pnl_c, itd.flows_c, itd.residual_c))
        self.assertEqual(1, self._count("SELECT COUNT(*) FROM commands WHERE request_id='init:capital:T1'"))
        self.assertEqual(1, self._count("SELECT COUNT(*) FROM transfers WHERE kind='INITIAL_CAPITAL'"))
        # deposit 2,500,000.00 (ui:d1) -> 1010 12,500,000.00; 3000 12,500,000.00; L6 ok; pnl(ALL, ITD) = 12,500,000 - 12,500,000 flows = 0.00
        r = deposit(self.rt, 250000000, request_id="ui:d1-deposit-1")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(1250000000, bal(self.rt, TREASURY, "1010"))
        self.assertEqual(1250000000, bal(self.rt, TREASURY, "3000"))
        self.assertEqual(1250000000, r.body["settled_c"])
        assert_integrity(self, self.rt, "full")
        itd = self.rt.accounting.pnl("ALL", "ITD")
        self.assertEqual((0, 1250000000, 0), (itd.pnl_c, itd.flows_c, itd.residual_c))
        self.assertEqual(1, self._count("SELECT COUNT(*) FROM events WHERE type='CAPITAL_DEPOSITED' AND payload_json NOT LIKE '%initial%'"))
        # replay ui:d1 -> replayed=True, balances unchanged; ui:d1 with amount 1.00 -> 409 REQUEST_ID_REUSED
        r2 = deposit(self.rt, 250000000, request_id="ui:d1-deposit-1")
        self.assertTrue(r2.replayed)
        self.assertEqual(1250000000, bal(self.rt, TREASURY, "1010"))
        r3 = deposit(self.rt, 100, request_id="ui:d1-deposit-1")
        self.assertEqual((409, "REQUEST_ID_REUSED"), (r3.http, r3.code))
        self.assertEqual(1250000000, bal(self.rt, TREASURY, "1010"))
        # deposit without amount -> 400 AMOUNT; amount 0 -> 400; negative -> 400; not a number -> 400
        self.assertEqual((400, "AMOUNT"), (lambda x: (x.http, x.code))(execute(self.rt, "treasury.deposit", {"lifetime_id": T1, "currency": "USD"})))
        self.assertEqual((400, "AMOUNT"), (lambda x: (x.http, x.code))(deposit(self.rt, 0)))
        self.assertEqual((400, "AMOUNT"), (lambda x: (x.http, x.code))(deposit(self.rt, -5)))
        self.assertEqual((400, "AMOUNT"), (lambda x: (x.http, x.code))(execute(self.rt, "treasury.deposit", {"lifetime_id": T1, "amount": "abc"})))
        # withdraw 20,000,000.00 -> 409 INSUFFICIENT_FUNDS, nothing posted
        r4 = execute(self.rt, "treasury.withdraw", {"lifetime_id": T1, "amount_c": 2000000000, "currency": "USD"})
        self.assertEqual((409, "INSUFFICIENT_FUNDS"), (r4.http, r4.code))
        self.assertEqual(1250000000, bal(self.rt, TREASURY, "1010"))
        self.assertEqual(0, self._count("SELECT COUNT(*) FROM commands WHERE kind='treasury.withdraw'"))
        # treasury(): capital 12,500,000; retained 0; advances 0; cash 12,500,000 (M3 identity)
        t = self.rt.accounting.treasury()
        self.assertEqual(1250000000, t.capital_c)
        self.assertEqual(0, t.retained_c)
        self.assertEqual(0, t.advances_c)
        self.assertEqual({"USD": 1250000000}, t.cash_by_ccy)
        self.assertEqual(1250000000, t.nav_c)
        self.assertEqual(1250000000, t.available_c)
        self.assertTrue(t.identity_ok)
        # a decimal-string amount is accepted too: 0.50 -> 50 cents
        r5 = execute(self.rt, "treasury.deposit", {"lifetime_id": T1, "amount": "0.50"})
        self.assertTrue(r5.ok, r5.body)
        self.assertEqual(50, r5.body["amount_c"])

    def test_reopen_skips_init(self):
        self.rt.close()
        rt2 = make_runtime(self.tmp)
        try:
            self.assertEqual(1000000000, bal(rt2, TREASURY, "1010"))
            with rt2.db.reader() as conn:
                self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM commands WHERE kind='treasury.init_capital'").fetchone()[0])
                self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM lifetimes").fetchone()[0])
                self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM journal_entries WHERE idem_key='capital:initial:T1'").fetchone()[0])
            assert_integrity(self, rt2, "full")
        finally:
            rt2.close()
            self.rt = make_runtime(self.tmp)   # for tearDown

    def test_init_capital_handler_is_idempotent_by_idem_key(self):
        # a second init command with another request id posts nothing (the capital entry exists) and says so
        r = execute(self.rt, "treasury.init_capital", {"lifetime_id": T1, "amount_c": 999, "currency": "USD", "source": "initial capital"}, "init:capital:T1:again", actor="init")
        self.assertTrue(r.ok, r.body)
        self.assertTrue(r.body["skipped"])
        self.assertEqual(1000000000, bal(self.rt, TREASURY, "1010"))
        assert_integrity(self, self.rt, "full")      # L6 ignores skipped commands

    def test_transfer_treasury_to_us_exchange(self):
        # §19.2: 1,000,000.00 TREASURY -> US_EXCHANGE: T Dr 3100 / Cr 1010; A Dr 1010 / Cr 3100; T settled 9,000,000.00; A settled 1,000,000.00;
        # NAV(ALL) 10,000,000.00; L5 Σ 3100 = 0 (Treasury's 3100 natural -1,000,000.00, the account's +1,000,000.00)
        r = fund(self.rt, US_EXCHANGE, 100000000)
        self.assertTrue(r.ok, r.body)
        self.assertEqual("X-000002", r.body["transfer_id"])         # X-000001 was the initial capital
        self.assertEqual(900000000, bal(self.rt, TREASURY, "1010"))
        self.assertEqual(100000000, bal(self.rt, US_EXCHANGE, "1010"))
        self.assertEqual(-100000000, bal(self.rt, TREASURY, "3100"))
        self.assertEqual(100000000, bal(self.rt, US_EXCHANGE, "3100"))
        self.assertEqual(1000000000, self.rt.accounting.nav("ALL").nav_c)
        self.assertEqual(900000000, self.rt.accounting.nav(TREASURY).nav_c)
        self.assertEqual(100000000, self.rt.accounting.nav(US_EXCHANGE).nav_c)
        assert_integrity(self, self.rt, "full")
        with self.rt.db.reader() as conn:
            row = conn.execute("SELECT kind, from_account, to_account, amount_c, currency FROM transfers WHERE transfer_id='X-000002'").fetchone()
            self.assertEqual(("TRANSFER", TREASURY, US_EXCHANGE, 100000000, "USD"), tuple(row))
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM events WHERE type='TRANSFER'").fetchone()[0])
            lines = conn.execute("SELECT account_id, gl, amount_c FROM journal_lines WHERE entry_id=? ORDER BY line_no", (r.body["entry_id"],)).fetchall()
        self.assertEqual([(TREASURY, "3100", 100000000), (TREASURY, "1010", -100000000), (US_EXCHANGE, "1010", 100000000), (US_EXCHANGE, "3100", -100000000)],
                         [tuple(x) for x in lines])
        t = self.rt.accounting.treasury()
        self.assertEqual((1000000000, 100000000, {US_EXCHANGE: 100000000}, 0, 900000000), (t.capital_c, t.advances_c, t.advances_by_account, t.retained_c, t.nav_c))
        self.assertTrue(t.identity_ok)
        # account -> account transfers are internal too: 250,000.00 US_EXCHANGE -> CREDIT keeps Σ 3100 = 0
        r2 = execute(self.rt, "treasury.transfer", {"from_account": US_EXCHANGE, "to_account": CREDIT, "currency": "USD", "amount_c": 25000000})
        self.assertTrue(r2.ok, r2.body)
        self.assertEqual(75000000, bal(self.rt, US_EXCHANGE, "1010"))
        self.assertEqual(25000000, bal(self.rt, CREDIT, "1010"))
        assert_integrity(self, self.rt, "full")

    def test_transfer_refusals(self):
        r = fund(self.rt, US_EXCHANGE, 2000000000)                               # 20,000,000.00 > 10,000,000.00 available
        self.assertEqual((409, "INSUFFICIENT_FUNDS"), (r.http, r.code))
        self.assertEqual(1000000000, bal(self.rt, TREASURY, "1010"))
        self.assertEqual(0, self._count("SELECT COUNT(*) FROM transfers WHERE kind='TRANSFER'"))
        r = execute(self.rt, "treasury.transfer", {"from_account": TREASURY, "to_account": TREASURY, "currency": "USD", "amount_c": 100})
        self.assertEqual("SAME_ACCOUNT", r.code)
        r = execute(self.rt, "treasury.transfer", {"from_account": TREASURY, "to_account": "T1:NOPE", "currency": "USD", "amount_c": 100})
        self.assertEqual("ACCOUNT_UNKNOWN", r.code)
        r = execute(self.rt, "treasury.deposit", {"account_id": US_EXCHANGE, "amount_c": 100})
        self.assertEqual("NOT_TREASURY", r.code)       # capital enters only through Treasury
        r = execute(self.rt, "treasury.deposit", {"lifetime_id": T1, "amount_c": 100, "currency": "eu"})
        self.assertEqual("CURRENCY", r.code)

    def test_cross_book_refused(self):
        tmp2 = tempfile.mkdtemp(prefix="fst-tre2-")
        rt2 = make_runtime(tmp2, books="real")
        try:
            r = execute(rt2, "treasury.transfer", {"from_account": "P1:TREASURY", "to_account": "F1:US_EXCHANGE", "currency": "USD", "amount_c": 100})
            self.assertEqual((400, "CROSS_BOOK"), (r.http, r.code))
            self.assertEqual({"from_lifetime": "P1", "to_lifetime": "F1"}, r.body["error"]["details"])
            self.assertEqual(100000000, bal(rt2, "P1:TREASURY", "1010"))
            assert_integrity(self, rt2, "full")
        finally:
            rt2.close()
            shutil.rmtree(tmp2, ignore_errors=True)

    def test_lifetime_closed_refused(self):
        with self.rt.db.writer() as tx:
            tx.x("UPDATE lifetimes SET status='CLOSED', ended_ts=? WHERE lifetime_id='T1'", (tx.ts,))
            tx.x("UPDATE accounts SET status='CLOSED', closed_ts=? WHERE lifetime_id='T1'", (tx.ts,))
        r = deposit(self.rt, 100)
        self.assertEqual((400, "LIFETIME_CLOSED"), (r.http, r.code))
        r = fund(self.rt, US_EXCHANGE, 100)
        self.assertEqual((400, "LIFETIME_CLOSED"), (r.http, r.code))
        r = execute(self.rt, "treasury.withdraw", {"lifetime_id": T1, "amount_c": 100})
        self.assertEqual((400, "LIFETIME_CLOSED"), (r.http, r.code))
        self.assertEqual(1000000000, bal(self.rt, TREASURY, "1010"))

    def test_sweep_in_links_the_fill_and_l19_holds(self):
        # a buy of 1,000 AAPL @ 339.50 fee 5.00 in an empty account leaves a payable of 339,505.00; a sweep of exactly that amount
        # linked to the fill moves it from Treasury: T 1010 10,000,000 - 339,505 = 9,660,495.00; A 1010 339,505.00
        r = buy(self.rt, US_EXCHANGE, "AAPL", 1000, "339.50")
        self.assertTrue(r.ok, r.body)
        fill_id = r.body["fill_id"]

        def h(ctx, payload):
            tid = treasury.sweep_in(ctx, US_EXCHANGE, "USD", 33950500, "fill", fill_id, 33950500)
            return {"transfer_id": tid}
        self.rt.bus.register("test.sweep", h)
        r2 = execute(self.rt, "test.sweep", {})
        self.assertTrue(r2.ok, r2.body)
        self.assertEqual(966049500, bal(self.rt, TREASURY, "1010"))
        self.assertEqual(33950500, bal(self.rt, US_EXCHANGE, "1010"))
        with self.rt.db.reader() as conn:
            row = conn.execute("SELECT kind, from_account, to_account, amount_c, linked_type, linked_id, shortfall_c FROM transfers WHERE transfer_id=?", (r2.body["transfer_id"],)).fetchone()
            self.assertEqual(("SWEEP_IN", TREASURY, US_EXCHANGE, 33950500, "fill", fill_id, 33950500), tuple(row))
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM events WHERE type='SWEEP_IN'").fetchone()[0])
        assert_integrity(self, self.rt, "full")       # L19: linked fill exists, amount == shortfall
        # a sweep beyond Treasury's available cash is refused with the amounts
        def h2(ctx, payload):
            treasury.sweep_in(ctx, US_EXCHANGE, "USD", 2000000000, "fill", fill_id, 2000000000)
            return {}
        self.rt.bus.register("test.sweep2", h2)
        r3 = execute(self.rt, "test.sweep2", {})
        self.assertEqual((409, "INSUFFICIENT_FUNDS"), (r3.http, r3.code))
        self.assertEqual(2000000000, r3.body["error"]["details"]["need_c"])
        self.assertEqual(966049500, r3.body["error"]["details"]["treasury_available_c"])
        self.assertEqual(966049500, bal(self.rt, TREASURY, "1010"))

    def test_update_settings_light(self):
        r = execute(self.rt, "account.update_settings", {"account_id": US_EXCHANGE, "sweep_out": False, "sweep_buffer_c": 500000, "pb_limit_c": 0})
        self.assertTrue(r.ok, r.body)
        self.assertEqual({"sweep_out": 1, "sweep_buffer_c": 1000000, "pb_limit_c": 0}, r.body["before"])
        self.assertEqual({"sweep_out": 0, "sweep_buffer_c": 500000, "pb_limit_c": 0}, r.body["after"])
        with self.rt.db.reader() as conn:
            row = conn.execute("SELECT sweep_out, sweep_buffer_c FROM accounts WHERE account_id=?", (US_EXCHANGE,)).fetchone()
            self.assertEqual((0, 500000), tuple(row))
        r = execute(self.rt, "account.update_settings", {"account_id": US_EXCHANGE, "pb_financing": True})
        self.assertEqual("NOT_PB", r.code)
        r = execute(self.rt, "account.update_settings", {"account_id": US_EXCHANGE, "sweep_buffer_c": -1})
        self.assertEqual("SETTING_VALUE", r.code)
        r = execute(self.rt, "account.update_settings", {"account_id": US_EXCHANGE})
        self.assertEqual("NOTHING_TO_UPDATE", r.code)

    def test_deposit_in_eur_base_at_snapshot_rate(self):
        # EUR 1,000,000.00 at the fixed EURUSD 1.1238: base 1,123,800.00; NAV(ALL) = 10,000,000 + 1,123,800 = 11,123,800.00; fx_result 0 at the same rate
        r = deposit(self.rt, 100000000, ccy="EUR")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(100000000, bal(self.rt, TREASURY, "1010", "EUR"))
        self.assertEqual(100000000, bal(self.rt, TREASURY, "3000", "EUR"))
        with self.rt.db.reader() as conn:
            lines = conn.execute("SELECT gl, base_c, fx_rate FROM journal_lines WHERE entry_id=? ORDER BY line_no", (r.body["entry_id"],)).fetchall()
        self.assertEqual([("1010", 112380000, "1.1238"), ("3000", -112380000, "1.1238")], [tuple(x) for x in lines])
        nav = self.rt.accounting.nav("ALL")
        self.assertEqual((1112380000, 0, 0), (nav.nav_c, nav.fx_result_c, nav.unrealised_c))
        assert_integrity(self, self.rt, "full")
        # the rate moves to 1.1300: NAV = 10,000,000 + 1,130,000 = 11,130,000.00 and fx_result = 1,130,000 - 1,123,800 = +6,200.00; residual 0
        self.rt.marks.set("EURUSD", "1.1300")
        nav = self.rt.accounting.nav("ALL")
        self.assertEqual((1113000000, 620000), (nav.nav_c, nav.fx_result_c))
        itd = self.rt.accounting.pnl("ALL", "ITD")
        self.assertEqual((620000, 0), (itd.pnl_c, itd.residual_c))
        t = self.rt.accounting.treasury()
        self.assertEqual({"USD": 1000000000, "EUR": 100000000}, t.cash_by_ccy)
        self.assertEqual({"USD": 1000000000, "EUR": 100000000}, t.capital_by_ccy)

    def test_withdraw_ok(self):
        r = execute(self.rt, "treasury.withdraw", {"lifetime_id": T1, "amount_c": 10000, "memo": "test"})
        self.assertTrue(r.ok, r.body)
        self.assertEqual(999990000, bal(self.rt, TREASURY, "1010"))       # 10,000,000 - 100.00
        self.assertEqual(999990000, bal(self.rt, TREASURY, "3000"))
        self.assertEqual(1, self._count("SELECT COUNT(*) FROM transfers WHERE kind='WITHDRAWAL'"))
        self.assertEqual(999990000, self.rt.accounting.treasury().capital_c)
        assert_integrity(self, self.rt, "full")


if __name__ == "__main__":
    unittest.main()
