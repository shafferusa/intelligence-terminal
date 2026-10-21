"""WP-02 equity tests (TERMINAL_DESIGN.md §19.3, §19.4).  Every expected number is worked by hand in a comment.
The SETTLE_DUE job belongs to the lifecycle (WP-08); a test-local handler posts the same §5.4 settlement lines so the
ledger numbers of §19.3 / §19.4 can be checked here."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import date

from tests.terminal_helpers import T1, TREASURY, US_EXCHANGE, assert_integrity, bal, execute, fund, snapshot
from tests import test_terminal_orders as sc
from finsim.terminal import orders
from finsim.terminal.ledger import Entry, cr, dr


def _h_settle_due(ctx, payload):
    """What WP-08's SETTLE_DUE posts: receivable Dr 1010 / Cr 1200; payable Dr 2100 / Cr 1010 (idem settle:{settle_id})."""
    rows = ctx.conn.execute("SELECT * FROM settlements WHERE status='PENDING' AND due_date <= ? ORDER BY settle_id", (payload["as_of"],)).fetchall()
    out = []
    for s in rows:
        amt, aid, ccy = int(s["amount_c"]), s["account_id"], s["currency"]
        if amt > 0:
            lines = [dr(aid, "1010", ccy, amt), cr(aid, s["gl"], ccy, amt)]
        else:
            lines = [dr(aid, s["gl"], ccy, -amt), cr(aid, "1010", ccy, -amt)]
        eid = ctx.post(Entry("SETTLE", f"settlement {s['settle_id']} of fill {s['fill_id']}", lines, f"settle:{s['settle_id']}", ref_type="settlement", ref_id=s["settle_id"]))
        ctx.conn.execute("UPDATE settlements SET status='SETTLED', settled_ts=?, entry_id=? WHERE settle_id=?", (ctx.ts, eid, s["settle_id"]))
        out.append(s["settle_id"])
    return {"settled": out}


class EquityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-eq-")
        self.rt = None

    def tearDown(self):
        if self.rt is not None:
            self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start(self, at):
        self.rt = sc.runtime(self.tmp, at=at)
        self.rt.bus.register("test.settle_due", _h_settle_due)
        return self.rt

    def market_buy(self, qty, last, volume=120000):
        rt = self.rt
        now = rt.clock.epoch()
        rt.marks.set_quote(sc.rec("AAPL", last, now - 120, now, bar=(now - 180, volume)))
        rt.marks.set("AAPL", last)
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=qty, order_type="MARKET")
        self.assertTrue(r.ok, r.body)
        self.assertEqual("FILLED", r.body["order"]["status"])
        return r

    def settle(self, as_of):
        r = execute(self.rt, "test.settle_due", {"as_of": as_of}, actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        return r.body["settled"]

    def test_19_3_buy_fee_settlement_and_pnl_today(self):
        # Fri 2026-10-16: fund A 500,000.00; snapshots Fri / Sat / Sun 17:45 (NAV 500,000.00 each)
        rt = self.start("2026-10-16T14:00:00-04:00")
        fund(rt, US_EXCHANGE, 50000000)
        for d in ("2026-10-16", "2026-10-17", "2026-10-18"):
            rt.clock.set(f"{d}T17:45:00-04:00")
            row = snapshot(rt, US_EXCHANGE, date.fromisoformat(d))
            snapshot(rt, "T1:ALL", date.fromisoformat(d))
            self.assertEqual(50000000, row["nav_c"])
        # Mon 2026-10-19 10:00 ET: BUY 1,000 AAPL MARKET, last 339.50, bar volume 120,000 (cap 12,000)
        rt.clock.set("2026-10-19T10:00:00-04:00")
        r = self.market_buy(1000, "339.50")
        fill = r.body["fills"][0]
        # gross 339,500.00; fee 1,000 x 0.005 = 5.00; entry Dr 1100 AAPL 339,500.00 / Dr 5000 5.00 / Cr 2100 339,505.00; settlement due Tue 10-20
        self.assertEqual((33950000, 500, "2026-10-19", "2026-10-20"), (fill["gross_c"], fill["fee_c"], fill["trade_date"], fill["settle_date"]))
        lines = sc.q(rt, "SELECT gl, amount_c, instrument_id FROM journal_lines WHERE entry_id=? ORDER BY line_no", (fill["entry_id"],))
        self.assertEqual([("1100", 33950000, "AAPL"), ("5000", 500, "AAPL"), ("2100", -33950500, None)], [(l["gl"], l["amount_c"], l["instrument_id"]) for l in lines])
        lot = sc.one(rt, "SELECT * FROM lots WHERE account_id=? AND instrument_id='AAPL'", (US_EXCHANGE,))
        self.assertEqual(("LONG", 1000, 1000, "339.5", 33950000), (lot["side"], lot["qty_open"], lot["qty_remaining"], lot["unit_cost"], lot["cost_c"]))
        st = sc.one(rt, "SELECT * FROM settlements WHERE fill_id=?", (fill["fill_id"],))
        self.assertEqual(("2026-10-20", -33950500, "2100", "PENDING"), (st["due_date"], st["amount_c"], st["gl"], st["status"]))
        self.assertEqual([], r.body["sweeps"])                                              # own cash covered it: no sweep
        # reads Mon 10:01: settled 500,000.00; payable 339,505.00; available (settle 10-20) 160,495.00; MV 339,500.00; unrealised 0; NAV 499,995.00
        rt.clock.set("2026-10-19T10:01:00-04:00")
        cash = rt.accounting.cash(US_EXCHANGE)["USD"]
        self.assertEqual((50000000, 33950500, 0), (cash.settled_c, cash.payable_c, cash.reserved_c))
        self.assertEqual(16049500, rt.accounting.available(US_EXCHANGE, "USD", date(2026, 10, 20)))
        pos = rt.accounting.position(US_EXCHANGE, "AAPL")
        self.assertEqual((1000, 33950000, 0, "339.5"), (pos.mv_c, pos.mv_c, pos.unrealised_c, str(pos.mark.mark)) if False else (pos.qty, pos.mv_c, pos.unrealised_c, str(pos.mark.mark)))
        self.assertEqual(49999500, rt.accounting.nav(US_EXCHANGE).nav_c)
        # P&L TODAY vs the Sun snapshot = 499,995 - 500,000 - 0 flows = -5.00; explain: fees 5.00; residual 0
        p = rt.accounting.pnl(US_EXCHANGE, "TODAY")
        self.assertEqual((-500, 50000000, 0, 500, 0), (p.pnl_c, p.nav0_c, p.flows_c, p.components["fees_c"], p.residual_c))
        self.assertFalse(p.degraded)
        assert_integrity(self, rt, "full")
        # Tue 00:35: SETTLE_DUE Dr 2100 / Cr 1010 339,505.00 -> settled 160,495.00, payable 0, NAV unchanged 499,995.00; a second run settles nothing
        rt.clock.set("2026-10-19T17:45:00-04:00")
        snapshot(rt, US_EXCHANGE, date(2026, 10, 19))
        snapshot(rt, "T1:ALL", date(2026, 10, 19))
        rt.clock.set("2026-10-20T00:35:00-04:00")
        self.assertEqual([st["settle_id"]], self.settle("2026-10-20"))
        self.assertEqual((16049500, 0), (bal(rt, US_EXCHANGE, "1010"), bal(rt, US_EXCHANGE, "2100")))
        self.assertEqual(49999500, rt.accounting.nav(US_EXCHANGE).nav_c)
        self.assertEqual([], self.settle("2026-10-20"))
        # a settle run with the clock at Mon 16:00 (as-of 10-19) would have settled nothing: the due date is 10-20
        self.assertEqual(0, len(sc.q(rt, "SELECT 1 FROM settlements WHERE status='PENDING' AND due_date <= '2026-10-19'")))
        # Tue mark 342.00 -> MV 342,000.00; unrealised +2,500.00; NAV 502,495.00; P&L TODAY vs the Mon 17:45 snapshot (499,995.00 at 339.50) = +2,500.00
        rt.clock.set("2026-10-20T10:00:00-04:00")
        rt.marks.set("AAPL", "342.00")
        pos = rt.accounting.position(US_EXCHANGE, "AAPL")
        self.assertEqual((34200000, 250000), (pos.mv_c, pos.unrealised_c))
        self.assertEqual(50249500, rt.accounting.nav(US_EXCHANGE).nav_c)
        p = rt.accounting.pnl(US_EXCHANGE, "TODAY")
        self.assertEqual((250000, 49999500, 250000, 0, 0), (p.pnl_c, p.nav0_c, p.components["unrealised_c"], p.components["fees_c"], p.residual_c))
        assert_integrity(self, rt, "full")

    def test_19_4_fifo_sell_with_realised(self):
        # Lots: A 1,000 AAPL @ 339.50 (Mon, fee 5.00), B 500 @ 335.00 (Tue, fee 2.50, cost 167,500.00)
        rt = self.start("2026-10-19T10:00:00-04:00")
        fund(rt, US_EXCHANGE, 50000000)
        self.market_buy(1000, "339.50")
        rt.clock.set("2026-10-20T00:35:00-04:00")
        self.settle("2026-10-20")
        rt.clock.set("2026-10-20T10:00:00-04:00")
        r = self.market_buy(500, "335.00")
        # 500 x 335.00 = 167,500.00; fee 500 x 0.005 = 2.50; need 167,502.50; settled 160,495.00 -> shortfall 7,007.50 swept from Treasury
        self.assertEqual((16750000, 250), (r.body["fills"][0]["gross_c"], r.body["fills"][0]["fee_c"]))
        self.assertEqual(700750, r.body["sweeps"][0]["amount_c"])
        rt.clock.set("2026-10-21T00:35:00-04:00")
        self.settle("2026-10-21")
        # Wed SELL 1,200 MARKET last 345.00: gross 414,000.00; fee 6.00; FIFO 1,000 x 339.50 + 200 x 335.00 = 406,500.00; realised +7,500.00
        rt.clock.set("2026-10-21T10:00:00-04:00")
        now = rt.clock.epoch()
        rt.marks.set_quote(sc.rec("AAPL", "345.00", now - 120, now, bar=(now - 180, 120000)))
        rt.marks.set("AAPL", "345.00")
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="SELL", qty=1200, order_type="MARKET")
        self.assertTrue(r.ok, r.body)
        fill = r.body["fills"][0]
        self.assertEqual((41400000, 600, 40650000, 750000), (fill["gross_c"], fill["fee_c"], fill["cost_relieved_c"], fill["realised_c"]))
        lines = sc.q(rt, "SELECT gl, amount_c FROM journal_lines WHERE entry_id=? ORDER BY line_no", (fill["entry_id"],))
        # Dr 1200 413,994.00 / Dr 5000 6.00 / Cr 1100 AAPL 406,500.00 / Cr 4000 AAPL 7,500.00 (414,000.00 both sides)
        self.assertEqual([("1200", 41399400), ("5000", 600), ("1100", -40650000), ("4000", -750000)], [(l["gl"], l["amount_c"]) for l in lines])
        self.assertEqual(0, sum(l["amount_c"] for l in lines))
        reliefs = sc.q(rt, "SELECT * FROM lot_relief WHERE close_fill_id=? ORDER BY id", (fill["fill_id"],))
        # (A, 1,000, 339,500.00, 345,000.00, +5,500.00), (B, 200, 67,000.00, 69,000.00, +2,000.00)
        self.assertEqual([(1000, 33950000, 34500000, 550000), (200, 6700000, 6900000, 200000)],
                         [(x["qty"], x["cost_relieved_c"], x["proceeds_c"], x["realised_c"]) for x in reliefs])
        lots = sc.q(rt, "SELECT * FROM lots WHERE account_id=? AND instrument_id='AAPL' AND qty_remaining > 0", (US_EXCHANGE,))
        # remaining lot B 300 @ 335.00 cost 100,500.00; v_positions 300; L9: 1100 AAPL = 339,500 + 167,500 - 406,500 = 100,500
        self.assertEqual([(300, "335", 10050000)], [(l["qty_remaining"], l["unit_cost"], l["cost_c"]) for l in lots])
        self.assertEqual((300, 10050000), (rt.accounting.position(US_EXCHANGE, "AAPL").qty, rt.accounting.position(US_EXCHANGE, "AAPL").cost_c))
        self.assertEqual(10050000, bal(rt, US_EXCHANGE, "1100", inst="AAPL"))
        # realised ITD 7,500.00; fees ITD 5.00 + 2.50 + 6.00 = 13.50
        self.assertEqual((750000, 1350), (bal(rt, US_EXCHANGE, "4000", inst="AAPL"), bal(rt, US_EXCHANGE, "5000", inst="AAPL")))
        pos = rt.accounting.position(US_EXCHANGE, "AAPL")
        self.assertEqual((750000, -1350), (pos.realised_itd_c, pos.fees_itd_c))      # the position view carries fees as a negative cost (§6.2)
        # settlement Thu 10-22: Dr 1010 / Cr 1200 413,994.00
        st = sc.one(rt, "SELECT * FROM settlements WHERE fill_id=?", (fill["fill_id"],))
        self.assertEqual(("2026-10-22", 41399400, "1200"), (st["due_date"], st["amount_c"], st["gl"]))
        before = bal(rt, US_EXCHANGE, "1010")
        rt.clock.set("2026-10-22T00:35:00-04:00")
        self.settle("2026-10-22")
        self.assertEqual((before + 41399400, 0), (bal(rt, US_EXCHANGE, "1010"), bal(rt, US_EXCHANGE, "1200")))
        assert_integrity(self, rt, "full")
        # SELL 400 with a 300 position -> 400 OVERSELL; SELL 300 is the accepted size (+ SELL_SHORT 100 with a borrow once WP-06 lands)
        rt.clock.set("2026-10-22T10:00:00-04:00")
        now = rt.clock.epoch()
        rt.marks.set_quote(sc.rec("AAPL", "345.00", now - 60, now))
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="SELL", qty=400, order_type="MARKET")
        self.assertEqual((400, "OVERSELL"), (r.http, r.code))
        self.assertIn("sell 300 and SELL_SHORT 100", r.message)
        pv = orders.preview(rt, {"account_id": US_EXCHANGE, "instrument_id": "AAPL", "side": "SELL", "qty": 300, "order_type": "MARKET"})
        self.assertTrue(pv["ok"], pv["blockers"])
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="SELL_SHORT", qty=100, order_type="MARKET")
        self.assertEqual((400, "NOT_IN_THIS_BUILD"), (r.http, r.code))
        self.assertEqual(300, rt.accounting.position(US_EXCHANGE, "AAPL").qty)


if __name__ == "__main__":
    unittest.main()
