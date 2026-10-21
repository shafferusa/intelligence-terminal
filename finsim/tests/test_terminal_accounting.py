"""WP-00 accounting read-model tests (TERMINAL_DESIGN.md §6, §19.3, §19.4): cash, buying power, positions, NAV, P&L explain,
balance sheet, treasury, transactions, obligations, liquidity, scopes, integrity L/M sets and the per-lifetime INTEGRITY_HOLD."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import date
from decimal import Decimal

from tests.terminal_helpers import (CREDIT, T1, TREASURY, US_EXCHANGE, assert_integrity, bal, buy, deposit, execute, fund, make_runtime, sell, set_mark,
                                    snapshot)
from finsim.terminal.accounting import ScopeError
from finsim.terminal.clock import epoch_of


class AccountingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-acc-")
        self.rt = make_runtime(self.tmp)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cash_and_buying_power_after_funding(self):
        self.assertTrue(fund(self.rt, US_EXCHANGE, 100000000).ok)
        c = self.rt.accounting.cash(US_EXCHANGE)["USD"]
        self.assertEqual((100000000, 0, 0, 0, 0, 0, 0, 100000000), (c.settled_c, c.receivable_c, c.payable_c, c.pending_c, c.held_at_lender_c, c.margin_c, c.reserved_c, c.available_c))
        bp = self.rt.accounting.buying_power(US_EXCHANGE, "USD")
        # own 1,000,000 + Treasury 9,000,000 + PB 0 = 10,000,000.00
        self.assertEqual((100000000, 900000000, 0, 1000000000), (bp.available_c, bp.treasury_available_c, bp.pb_headroom_c, bp.total_c))
        bpt = self.rt.accounting.buying_power(TREASURY, "USD")
        self.assertEqual((900000000, 0, 900000000), (bpt.available_c, bpt.treasury_available_c, bpt.total_c))
        self.assertEqual(900000000, self.rt.accounting.cash(TREASURY)["USD"].settled_c)
        # a Treasury reserve reduces what the accounts can draw: reserve 100,000.00 -> treasury_available 8,900,000.00
        with self.rt.db.writer() as tx:
            from finsim.terminal.db import set_setting
            set_setting(tx.conn, "treasury_reserve_c", 10000000)
        self.assertEqual(890000000, self.rt.accounting.buying_power(US_EXCHANGE, "USD").treasury_available_c)

    def test_reserved_cash_from_working_orders(self):
        self.assertTrue(fund(self.rt, US_EXCHANGE, 100000000).ok)
        now = self.rt.clock.epoch()
        with self.rt.db.writer() as tx:
            common = (US_EXCHANGE, "AAPL", 100, "DAY", "WORKING", "US_EQUITY", now, now, now + 3600, "{}")
            # LIMIT BUY 100 @ 300.00: reserves 100 x 300 + fee max(100 x 0.005, 1.00) = 30,000.00 + 1.00 = 30,001.00
            tx.x("INSERT INTO orders(order_id, request_id, account_id, instrument_id, side, qty, order_type, limit_px, tif, status, session_group, created_ts, updated_ts, expires_ts, preview_json)"
                 " VALUES ('O-000001','ui:o1',?,?,'BUY',?,'LIMIT','300.00',?,?,?,?,?,?,?)", common)
            # MARKET BUY 100 at last 339.50 x 1.02 = 346.29 -> 34,629.00 + 1.00 = 34,630.00
            tx.x("INSERT INTO orders(order_id, request_id, account_id, instrument_id, side, qty, order_type, tif, status, session_group, created_ts, updated_ts, expires_ts, preview_json)"
                 " VALUES ('O-000002','ui:o2',?,?,'BUY',?,'MARKET',?,?,?,?,?,?,?)", common)
            # SELL_SHORT 100: collateral not yet posted 1.02 x 100 x 339.50 = 34,629.00
            tx.x("INSERT INTO orders(order_id, request_id, account_id, instrument_id, side, qty, order_type, tif, status, session_group, created_ts, updated_ts, expires_ts, preview_json)"
                 " VALUES ('O-000003','ui:o3',?,?,'SELL_SHORT',?,'MARKET',?,?,?,?,?,?,?)", common)
            # a FILLED order reserves nothing
            tx.x("INSERT INTO orders(order_id, request_id, account_id, instrument_id, side, qty, order_type, limit_px, tif, status, session_group, created_ts, updated_ts, expires_ts, preview_json)"
                 " VALUES ('O-000004','ui:o4',?,?,'BUY',?,'LIMIT','1.00',?,'FILLED',?,?,?,?,?)", (US_EXCHANGE, "AAPL", 100, "DAY", "US_EQUITY", now, now, now + 3600, "{}"))
        c = self.rt.accounting.cash(US_EXCHANGE)["USD"]
        self.assertEqual(3000100 + 3463000 + 3462900, c.reserved_c)        # 99,260.00
        self.assertEqual(100000000 - 9926000, c.available_c)               # 900,740.00

    def test_equity_buy_positions_nav_pnl_today(self):
        # §19.3 with the test fill helper (no matcher): Fri 2026-10-16 fund 500,000.00; snapshots Fri/Sat/Sun 17:45 (NAV 500,000.00)
        rt = self.rt
        rt.clock.set("2026-10-16T10:00:00-04:00")
        self.assertTrue(fund(rt, US_EXCHANGE, 50000000).ok)
        for d in ("2026-10-16", "2026-10-17", "2026-10-18"):
            rt.clock.set(f"{d}T17:45:00-04:00")
            row = snapshot(rt, US_EXCHANGE, date.fromisoformat(d))
            self.assertEqual(50000000, row["nav_c"])
            snapshot(rt, "T1:ALL", date.fromisoformat(d))
            snapshot(rt, TREASURY, date.fromisoformat(d))
        # Mon 2026-10-19 10:00: BUY 1,000 AAPL @ 339.50; fee 1,000 x 0.005 = 5.00; entry Dr 1100 339,500.00 / Dr 5000 5.00 / Cr 2100 339,505.00; settle Tue 10-20
        rt.clock.set("2026-10-19T10:00:00-04:00")
        r = buy(rt, US_EXCHANGE, "AAPL", 1000, "339.50")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((33950000, 500, "2026-10-20"), (r.body["gross_c"], r.body["fee_c"], r.body["settle_date"]))
        self.assertEqual(33950000, bal(rt, US_EXCHANGE, "1100", inst="AAPL"))
        self.assertEqual(500, bal(rt, US_EXCHANGE, "5000", inst="AAPL"))
        self.assertEqual(33950500, bal(rt, US_EXCHANGE, "2100"))
        # reads Mon 10:01: settled 500,000.00; payable 339,505.00; available (settle 10-20) 160,495.00; MV at 339.50 339,500.00; unrealised 0; NAV 499,995.00
        rt.clock.advance(60)
        c = rt.accounting.cash(US_EXCHANGE)["USD"]
        self.assertEqual((50000000, 33950500, -33950500), (c.settled_c, c.payable_c, c.pending_c))
        self.assertEqual(16049500, rt.accounting.available(US_EXCHANGE, "USD", date(2026, 10, 20)))
        self.assertEqual(16049500, c.available_c)
        pos = rt.accounting.positions(US_EXCHANGE)
        self.assertEqual(1, len(pos))
        p = pos[0]
        self.assertEqual(("AAPL", 1000, 33950000, Decimal("339.5"), 33950000, 0, "delayed", "securities"), (p.instrument_id, p.qty, p.cost_c, p.avg_px, p.mv_c, p.unrealised_c, p.mark.quality, p.klass))
        self.assertEqual((-500, 0, 0, 0), (p.fees_itd_c, p.realised_itd_c, p.income_itd_c, p.financing_itd_c))
        self.assertEqual(-500, p.day_pnl_c)
        self.assertEqual(1, len(p.lots))
        self.assertEqual((1000, 1000, Decimal("339.5"), 33950000, "2026-10-19"), (p.lots[0].qty_open, p.lots[0].qty_remaining, p.lots[0].unit_cost, p.lots[0].cost_c, p.lots[0].open_date))
        nav = rt.accounting.nav(US_EXCHANGE)
        self.assertEqual((49999500, False, "delayed", 0, 0, 33950000), (nav.nav_c, nav.partial, nav.quality, nav.unrealised_c, nav.fx_result_c, nav.mv_long_c))
        self.assertEqual(49999500 + 950000000, rt.accounting.nav("ALL").nav_c)        # 499,995 + 9,500,000 Treasury = 9,999,995.00
        # P&L TODAY vs the Sunday snapshot = -5.00 (explain: fees -5.00; residual 0)
        pnl = rt.accounting.pnl(US_EXCHANGE, "TODAY")
        self.assertEqual((49999500, 50000000, 0, -500, -500, 0, False), (pnl.nav_c, pnl.nav0_c, pnl.flows_c, pnl.pnl_c, pnl.explained_c, pnl.residual_c, pnl.degraded))
        self.assertEqual({"realised_c": 0, "income_c": 0, "financing_c": 0, "fees_c": 500, "unrealised_c": 0, "fx_result_c": 0}, pnl.components)
        self.assertEqual("2026-10-18", pnl.baseline["snap_date"])
        # ITD on the account: NAV 499,995 - flows 500,000 (the transfer in) = -5.00
        itd = rt.accounting.pnl(US_EXCHANGE, "ITD")
        self.assertEqual((50000000, -500, 0, True), (itd.flows_c, itd.pnl_c, itd.residual_c, itd.baseline["inception"]))
        # the lifetime: NAV 9,999,995 - capital 10,000,000 = -5.00
        self.assertEqual(-500, rt.accounting.pnl("ALL", "ITD").pnl_c)
        assert_integrity(self, rt, "full")
        # Mon 17:45 snapshot at 339.50 (NAV 499,995.00); Tue mark 342.00 -> MV 342,000.00; unrealised +2,500.00; NAV 502,495.00; P&L TODAY +2,500.00
        rt.clock.set("2026-10-19T17:45:00-04:00")
        row = snapshot(rt, US_EXCHANGE, date(2026, 10, 19))
        self.assertEqual((49999500, -500, 0), (row["nav_c"], row["pnl_day_c"], row["flows_day_c"]))
        snapshot(rt, "T1:ALL", date(2026, 10, 19))
        rt.clock.set("2026-10-20T10:00:00-04:00")
        set_mark(rt, "AAPL", "342.00")
        p = rt.accounting.positions(US_EXCHANGE)[0]
        self.assertEqual((34200000, 250000, 250000), (p.mv_c, p.unrealised_c, p.day_pnl_c))
        nav = rt.accounting.nav(US_EXCHANGE)
        self.assertEqual((50249500, 250000), (nav.nav_c, nav.unrealised_c))
        pnl = rt.accounting.pnl(US_EXCHANGE, "TODAY")
        self.assertEqual((250000, 250000, 0, "2026-10-19"), (pnl.pnl_c, pnl.explained_c, pnl.residual_c, pnl.baseline["snap_date"]))
        self.assertEqual(250000, pnl.components["unrealised_c"])
        # MTD / YTD baselines: the last snapshot before 1 Oct / 1 Jan does not exist -> degraded, inception baseline
        mtd = rt.accounting.pnl(US_EXCHANGE, "MTD")
        self.assertTrue(mtd.degraded)
        self.assertTrue(mtd.baseline["inception"])
        self.assertEqual(0, mtd.residual_c)
        assert_integrity(self, rt, "full")       # M1 residual 0, M2 ties, M4/M5 over the T1:ALL chain 10-16..10-19

    def test_fifo_sell_realised(self):
        # §19.4: lots A 1,000 @ 339.50 (fee 5.00), B 500 @ 335.00 (fee 2.50, cost 167,500.00); SELL 1,200 @ 345.00: gross 414,000.00, fee 6.00,
        # FIFO relief 339,500 + 200 x 335 = 406,500.00; realised +7,500.00
        rt = self.rt
        self.assertTrue(buy(rt, US_EXCHANGE, "AAPL", 1000, "339.50").ok)
        rt.clock.advance(60)
        r = buy(rt, US_EXCHANGE, "AAPL", 500, "335.00")
        self.assertEqual(250, r.body["fee_c"])
        rt.clock.advance(60)
        r = sell(rt, US_EXCHANGE, "AAPL", 1200, "345.00")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((41400000, 600, 40650000, 750000), (r.body["gross_c"], r.body["fee_c"], r.body["cost_relieved_c"], r.body["realised_c"]))
        self.assertEqual([(1000, 33950000, 34500000, 550000), (200, 6700000, 6900000, 200000)],
                         [(x["qty"], x["cost_relieved_c"], x["proceeds_c"], x["realised_c"]) for x in r.body["reliefs"]])
        with rt.db.reader() as conn:
            lines = conn.execute("SELECT gl, amount_c FROM journal_lines WHERE entry_id=? ORDER BY line_no", (r.body["entry_id"],)).fetchall()
        self.assertEqual([("1200", 41399400), ("5000", 600), ("1100", -40650000), ("4000", -750000)], [tuple(x) for x in lines])
        self.assertEqual(10050000, bal(rt, US_EXCHANGE, "1100", inst="AAPL"))       # 339,500 + 167,500 - 406,500 = 100,500.00
        p = rt.accounting.position(US_EXCHANGE, "AAPL")
        self.assertEqual((300, 10050000, Decimal("335"), 750000, -1350), (p.qty, p.cost_c, p.avg_px, p.realised_itd_c, p.fees_itd_c))
        self.assertEqual(1, len(p.lots))
        self.assertEqual((300, 10050000), (p.lots[0].qty_remaining, p.lots[0].cost_c))
        assert_integrity(self, rt, "full")

    def test_nav_partial_flag(self):
        rt = self.rt
        self.assertIsNotNone(rt.instruments.get("MSFT"))
        self.assertTrue(buy(rt, US_EXCHANGE, "MSFT", 100, "500.00").ok)
        nav = rt.accounting.nav(US_EXCHANGE)
        self.assertEqual((True, ["MSFT"], "unavailable"), (nav.partial, nav.missing, nav.quality))
        self.assertIn("1 unpriced", nav.note)
        p = rt.accounting.positions(US_EXCHANGE)[0]
        self.assertEqual(("unavailable", None, None), (p.mark.quality, p.mv_c, p.unrealised_c))
        pnl = rt.accounting.pnl(US_EXCHANGE, "ITD")
        self.assertTrue(pnl.partial)
        set_mark(rt, "MSFT", "510.00")
        nav = rt.accounting.nav(US_EXCHANGE)
        self.assertEqual((False, [], 100000), (nav.partial, nav.missing, nav.unrealised_c))    # 100 x (510 - 500) = 1,000.00
        rep = rt.accounting.integrity("full")
        self.assertTrue(rep.ok)

    def test_integrity_clean_on_empty_and_seeded_book(self):
        rep = self.rt.accounting.integrity("full")
        self.assertTrue(rep.ok)
        self.assertEqual([], rep.problems)
        self.assertEqual({"T1": False}, rep.holds)
        self.assertEqual("full", rep.mode)
        self.assertTrue(fund(self.rt, US_EXCHANGE, 100000000).ok)
        self.assertTrue(buy(self.rt, US_EXCHANGE, "AAPL", 1000, "339.50").ok)
        rep = self.rt.accounting.integrity("full")
        self.assertTrue(rep.ok, [p.to_dict() for p in rep.problems])
        self.assertEqual([], rep.problems)
        self.assertIsNotNone(self.rt.accounting.last_report())
        self.assertEqual("full", self.rt.accounting.last_report()["mode"])

    def test_integrity_hold_set_and_cleared_per_lifetime(self):
        rt = self.rt
        with rt.db.writer() as tx:
            tx.x("UPDATE gl_balances SET balance_c = balance_c + 1 WHERE account_id=? AND gl='1010' AND currency='USD'", (TREASURY,))
        rep = rt.accounting.integrity("ledger")
        self.assertFalse(rep.ok)
        self.assertTrue(rep.hold)
        self.assertEqual({"T1": True}, rep.holds)
        self.assertIn("L3", rep.failing)
        self.assertTrue(rt.accounting.is_held("T1"))
        with rt.db.reader() as conn:
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM attention WHERE source='integrity' AND code='INTEGRITY' AND ref_id='T1' AND resolved_ts IS NULL").fetchone()[0])
        rep = rt.accounting.integrity("ledger")           # a second failing run does not duplicate the attention item
        with rt.db.reader() as conn:
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM attention WHERE source='integrity' AND code='INTEGRITY' AND ref_id='T1' AND resolved_ts IS NULL").fetchone()[0])
        with rt.db.writer() as tx:
            tx.x("UPDATE gl_balances SET balance_c = balance_c - 1 WHERE account_id=? AND gl='1010' AND currency='USD'", (TREASURY,))
        rep = rt.accounting.integrity("ledger")
        self.assertTrue(rep.ok)
        self.assertEqual({"T1": False}, rep.holds)
        self.assertFalse(rt.accounting.is_held("T1"))
        with rt.db.reader() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM attention WHERE source='integrity' AND code='INTEGRITY' AND resolved_ts IS NULL").fetchone()[0])
        # a single-lifetime run on an unknown lifetime is a ScopeError
        with self.assertRaises(ScopeError):
            rt.accounting.integrity("ledger", lifetime_id="Z9")

    def test_liquidity_formulas(self):
        rt = self.rt
        self.assertTrue(fund(rt, US_EXCHANGE, 100000000).ok)
        self.assertTrue(buy(rt, US_EXCHANGE, "AAPL", 1000, "339.50").ok)
        # available(A) = 1,000,000 - 339,505 = 660,495.00; Treasury 9,000,000.00; excess = 9,660,495.00; nothing encumbered
        liq = rt.accounting.liquidity("ALL")
        self.assertEqual((966049500, 900000000, 66049500, 0, 0), (liq.excess_liquidity_c, liq.treasury_available_c, liq.accounts_available_c, liq.maintenance_shortfall_c, liq.encumbrance_c))
        self.assertIn("Excess liquidity", liq.formulas["excess_liquidity"])
        self.assertEqual(66049500, liq.detail["accounts"][US_EXCHANGE])
        liq_a = rt.accounting.liquidity(US_EXCHANGE)
        self.assertEqual(966049500, liq_a.excess_liquidity_c)        # the account scope still counts its lifetime's Treasury

    def test_treasury_view_after_funding(self):
        self.assertTrue(fund(self.rt, US_EXCHANGE, 100000000).ok)
        self.assertTrue(fund(self.rt, CREDIT, 50000000).ok)
        t = self.rt.accounting.treasury()
        self.assertEqual((1000000000, 150000000, 0, 850000000, 850000000, 0), (t.capital_c, t.advances_c, t.retained_c, t.nav_c, t.available_c, t.encumbrance_c))
        self.assertEqual({US_EXCHANGE: 100000000, CREDIT: 50000000}, t.advances_by_account)
        self.assertTrue(t.identity_ok)
        self.assertIn("own funds", t.formulas["treasury"])

    def test_balance_sheet_ties_and_memo(self):
        rt = self.rt
        self.assertTrue(fund(rt, US_EXCHANGE, 100000000).ok)
        self.assertTrue(buy(rt, US_EXCHANGE, "AAPL", 1000, "339.50").ok)
        set_mark(rt, "AAPL", "342.00")
        bs = rt.accounting.balance_sheet(US_EXCHANGE)
        # assets: cash 1,000,000 + AAPL at market 342,000 = 1,342,000; liabilities: payable 339,505; NAV 1,002,495.00
        self.assertEqual((134200000, 33950500, 100249500, 0), (bs.assets_c, bs.liabilities_c, bs.nav_c, bs.rounding_c))
        self.assertEqual(bs.nav_c, bs.assets_c - bs.liabilities_c)
        self.assertEqual((0, 100000000, 249500), (bs.memo["capital_c"], bs.memo["advances_c"], bs.memo["retained_c"]))
        self.assertEqual(bs.nav_c, bs.memo["capital_c"] + bs.memo["advances_c"] + bs.memo["retained_c"])
        labels = {r["label"]: r["base_c"] for r in bs.rows}
        self.assertEqual(100000000, labels["Cash — settled"])
        self.assertEqual(33950000, labels["Securities and options at cost (long)"])
        self.assertEqual(250000, labels["Unrealised on long positions"])
        self.assertEqual(33950500, labels["Payables (purchases, FX)"])
        # the lifetime: Treasury 9,000,000 + the account 1,002,495 = 10,002,495.00 = capital 10,000,000 + retained 2,495 (unrealised 2,500 - fee 5)
        bs_all = rt.accounting.balance_sheet("ALL")
        self.assertEqual((1000249500, 1000000000, 0, 249500), (bs_all.nav_c, bs_all.memo["capital_c"], bs_all.memo["advances_c"], bs_all.memo["retained_c"]))

    def test_transactions_and_obligations(self):
        rt = self.rt
        self.assertTrue(fund(rt, US_EXCHANGE, 100000000).ok)
        r = buy(rt, US_EXCHANGE, "AAPL", 1000, "339.50")
        rows = rt.accounting.transactions(US_EXCHANGE)
        self.assertEqual(2, len(rows))                                # the transfer in and the fill, each once, newest first
        fill_row, xfer_row = rows
        self.assertEqual(("FILL", "AAPL", 1000, "339.50", 33950000, 500, {}), (fill_row.kind, fill_row.instrument_id, fill_row.qty, fill_row.price, fill_row.gross_c, fill_row.fee_c, fill_row.net_cash))
        self.assertEqual(("TRANSFER", {"USD": 100000000}, [US_EXCHANGE]), (xfer_row.kind, xfer_row.net_cash, xfer_row.accounts))
        self.assertEqual(4, len(xfer_row.lines))
        self.assertEqual(1, len(rt.accounting.transactions(US_EXCHANGE, kinds=["FILL"])))
        self.assertEqual(1, len(rt.accounting.transactions(US_EXCHANGE, instrument_id="AAPL")))
        self.assertEqual(3, len(rt.accounting.transactions("ALL")))      # + the initial capital
        self.assertEqual(1, len(rt.accounting.transactions("ALL", limit=1)))
        ob = rt.accounting.obligations(US_EXCHANGE)
        self.assertEqual(1, len(ob.settlements))
        self.assertEqual((-33950500, "2100", "2026-10-21"), (ob.settlements[0]["amount_c"], ob.settlements[0]["gl"], ob.settlements[0]["due_date"]))
        self.assertEqual(({}, {"USD": 33950500}, "2026-10-21"), (ob.pending_in, ob.pending_out, ob.next_due))
        self.assertEqual((0, 0, 0), (ob.margin["futures_required_c"], ob.margin["options_required_c"], ob.collateral_c))
        self.assertEqual(r.body["settle_id"], ob.settlements[0]["settle_id"])

    def test_scopes_all_books_and_lifetime(self):
        tmp2 = tempfile.mkdtemp(prefix="fst-acc2-")
        rt2 = make_runtime(tmp2, books="real")
        try:
            self.assertEqual(102600000000, rt2.accounting.nav("ALL_BOOKS").nav_c)       # 1,000,000 + 25,000,000 + 1,000,000,000 = 1,026,000,000.00
            self.assertEqual(100000000, rt2.accounting.nav("P1:ALL").nav_c)
            self.assertEqual(2500000000, rt2.accounting.nav("F1:ALL").nav_c)
            self.assertEqual(100000000000, rt2.accounting.nav("I1:ALL").nav_c)
            self.assertEqual(["P1", "F1", "I1"], rt2.accounting.nav("ALL_BOOKS").lifetime_ids)
            self.assertEqual([], rt2.accounting.positions("ALL_BOOKS"))
            rt2.accounting.selected_book = "F"
            self.assertEqual(2500000000, rt2.accounting.nav("ALL").nav_c)
            self.assertEqual(0, rt2.accounting.pnl("ALL_BOOKS", "ITD").pnl_c)
            self.assertEqual(102600000000, rt2.accounting.pnl("ALL_BOOKS", "ITD").flows_c)
            with self.assertRaises(ScopeError):
                rt2.accounting.nav("X9:ALL")
            with self.assertRaises(ScopeError):
                rt2.accounting.nav("P1:NOPE")
            with self.assertRaises(ScopeError):
                rt2.accounting.cash("nonsense")
            assert_integrity(self, rt2, "full")
        finally:
            rt2.close()
            shutil.rmtree(tmp2, ignore_errors=True)

    def test_flows_and_snapshot_row(self):
        rt = self.rt
        t0 = rt.clock.epoch()
        rt.clock.advance(10)
        self.assertTrue(deposit(rt, 250000000).ok)
        rt.clock.advance(10)
        self.assertTrue(fund(rt, US_EXCHANGE, 100000000).ok)
        t1 = rt.clock.epoch()
        self.assertEqual(250000000, rt.accounting.flows("ALL", t0, t1))              # the lifetime sees the deposit only (3100 nets)
        self.assertEqual(100000000, rt.accounting.flows(US_EXCHANGE, t0, t1))        # the account sees its transfer in
        self.assertEqual(150000000, rt.accounting.flows(TREASURY, t0, t1))           # Treasury: +2,500,000 deposit - 1,000,000 advanced
        self.assertEqual(0, rt.accounting.flows("ALL", t1, t1 + 100))
        row = rt.accounting.snapshot_row("T1:ALL", date(2026, 10, 20))
        self.assertEqual(("T1:ALL", 1250000000, 1250000000, 0, 1250000000, 0, 0), (row["account_id"], row["nav_c"], row["capital_cum_c"], row["transfers_cum_c"], row["flows_day_c"], row["pnl_day_c"], row["partial"]))


if __name__ == "__main__":
    unittest.main()
