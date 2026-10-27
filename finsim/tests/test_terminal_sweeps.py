"""WP-02 sweep tests (TERMINAL_DESIGN.md §19.2, §10.8, §11.6; D2).  Every expected number is worked by hand in a comment.
The SWEEP_OUT and SETTLE_DUE steps of §19.2 belong to the lifecycle (WP-08) and are not run here."""
from __future__ import annotations

import shutil
import tempfile
import unittest

from tests.terminal_helpers import T1, TREASURY, US_EXCHANGE, assert_integrity, bal, deposit, execute, fund, make_runtime
from tests import test_terminal_orders as sc
from finsim.terminal import execution, orders
from finsim.terminal.ledger import Entry, cr, dr


def _h_vm_loss(ctx, payload):
    """A stubbed variation-margin loss (what futures.vm posts): fund() with the overdraft rule, then Dr 4400 / Cr 1010."""
    aid, amount, inst = payload["account_id"], int(payload["amount_c"]), payload["instrument_id"]
    res = execution.fund(ctx, aid, "USD", amount, "vm", payload["key"], None, immediate=True, overdraft=True)
    entry_id = ctx.post(Entry("VM", f"test VM loss {amount}", [dr(aid, "4400", "USD", amount, instrument_id=inst), cr(aid, "1010", "USD", amount)],
                              f"vm:{payload['key']}", ref_type="vm", ref_id=payload["key"]))
    return {"funding": res.to_dict(), "entry_id": entry_id}


def _h_resolve(ctx, payload):
    return {"resolved": execution.auto_resolve_shortfall(ctx, payload["account_id"], "USD")}


class SweepTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-swp-")
        self.rt = sc.runtime(self.tmp)
        self.rt.bus.register("test.vm_loss", _h_vm_loss)
        self.rt.bus.register("test.resolve", _h_resolve)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_transfer_confirm_sweep_in_and_refusal(self):
        rt = self.rt
        # Transfer 1,000,000.00 TREASURY -> US_EXCHANGE: T Dr 3100 / Cr 1010; A Dr 1010 / Cr 3100; T settled 9,000,000; A 1,000,000; NAV(ALL) 10,000,000
        r = fund(rt, US_EXCHANGE, 100000000)
        self.assertTrue(r.ok, r.body)
        self.assertEqual((900000000, 100000000), (bal(rt, TREASURY, "1010"), bal(rt, US_EXCHANGE, "1010")))
        self.assertEqual(-100000000, bal(rt, TREASURY, "3100"))          # equity: a debit shows as a negative natural balance
        self.assertEqual(100000000, bal(rt, US_EXCHANGE, "3100"))
        self.assertEqual(0, bal(rt, TREASURY, "3100") + bal(rt, US_EXCHANGE, "3100"))     # L5: sum of 3100 = 0
        self.assertEqual(1000000000, rt.accounting.nav("ALL").nav_c)
        # Tue 2026-10-20 14:00 ET: BUY 5,000 SPY MARKET, last 769.32 (quote_time now - 120), bar volume 120,000 -> cap 12,000 >= 5,000
        now = rt.clock.epoch()
        rt.marks.set_quote(sc.rec("SPY", "769.32", now - 120, now, bar=(now - 180, 120000)))
        req = dict(account_id=US_EXCHANGE, instrument_id="SPY", side="BUY", qty=5000, order_type="MARKET", tif="DAY")
        # gross 5,000 x 769.32 = 3,846,600.00; fee 25.00; need 3,846,625.00; available 1,000,000.00 -> shortfall 2,846,625.00 > 2,000,000.00
        pv = orders.preview(rt, req)
        self.assertEqual(("3846600.00", "25.00", "3846625.00", "2846625.00", True, True), (pv["economics"]["gross"], pv["economics"]["fee"], pv["funding"]["need"],
                                                                                            pv["funding"]["sweep_in"], pv["funding"]["confirm_required"], pv["ok"]))
        r = sc.submit(rt, **req)
        self.assertEqual((409, "CONFIRM_REQUIRED"), (r.http, r.code))
        self.assertEqual(("2846625.00", 284662500), (r.body["error"]["details"]["sweep_in"], r.body["error"]["details"]["sweep_in_c"]))
        self.assertEqual(0, sc.one(rt, "SELECT COUNT(*) n FROM commands WHERE kind='order.submit'")["n"])
        self.assertEqual(0, sc.one(rt, "SELECT COUNT(*) n FROM orders")["n"])
        # resubmit the same request_id with confirm_large -> SWEEP_IN linked to the fill, shortfall_c 284662500, fill entry, settlement due 2026-10-21
        r = sc.submit(rt, request_id=r.body["request_id"], confirm_large=True, **req)
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("FILLED", 5000), (r.body["order"]["status"], r.body["order"]["filled_qty"]))
        fill = r.body["fills"][0]
        self.assertEqual((384660000, 2500, "last", "2026-10-21"), (fill["gross_c"], fill["fee_c"], fill["price_basis"], fill["settle_date"]))
        sweep = sc.one(rt, "SELECT * FROM transfers WHERE kind='SWEEP_IN'")
        self.assertEqual((284662500, "fill", fill["fill_id"], 284662500, TREASURY, US_EXCHANGE), (sweep["amount_c"], sweep["linked_type"], sweep["linked_id"], sweep["shortfall_c"],
                                                                                                   sweep["from_account"], sweep["to_account"]))
        self.assertEqual([sweep["transfer_id"]], [s["transfer_id"] for s in r.body["sweeps"]])
        lines = sc.q(rt, "SELECT gl, amount_c FROM journal_lines WHERE entry_id=? ORDER BY line_no", (fill["entry_id"],))
        # Dr 1100 SPY 3,846,600.00 / Dr 5000 25.00 / Cr 2100 3,846,625.00
        self.assertEqual([("1100", 384660000), ("5000", 2500), ("2100", -384662500)], [(l["gl"], l["amount_c"]) for l in lines])
        st = sc.one(rt, "SELECT * FROM settlements WHERE fill_id=?", (fill["fill_id"],))
        self.assertEqual((-384662500, "2100", "2026-10-21", "PENDING"), (st["amount_c"], st["gl"], st["due_date"], st["status"]))
        # T settled 6,153,375.00 (10,000,000 - 1,000,000 - 2,846,625); T 3100 debit 3,846,625.00; A settled 3,846,625.00, payable 3,846,625.00, available 0.00
        self.assertEqual(615337500, bal(rt, TREASURY, "1010"))
        self.assertEqual(-384662500, bal(rt, TREASURY, "3100"))
        cash = rt.accounting.cash(US_EXCHANGE)["USD"]
        self.assertEqual((384662500, 384662500, 0), (cash.settled_c, cash.payable_c, cash.available_c))
        pos = rt.accounting.position(US_EXCHANGE, "SPY")
        self.assertEqual((5000, 384660000, "769.32"), (pos.qty, pos.cost_c, str(pos.avg_px)))
        # NAV(A) at 769.32 = 3,846,625 - 3,846,625 + 3,846,600 = 3,846,600.00; NAV(ALL) = 6,153,375 + 3,846,600 = 9,999,975.00 = 10,000,000 - 25.00
        self.assertEqual(384660000, rt.accounting.nav(US_EXCHANGE).nav_c)
        self.assertEqual(999997500, rt.accounting.nav("ALL").nav_c)
        assert_integrity(self, rt, "full")                                                   # L19 sweep linkage holds
        # funding alone no longer refuses (FUNDS_WAIT, hotfix 2026-10-21): Treasury settled 100.00 (withdraw 6,153,275.00), A available 0.00,
        # BUY 1,000 AAPL at 339.50 -> the preview is ok with the queue warning and the order is accepted SUSPENDED, waiting for funds
        execute(rt, "treasury.withdraw", {"lifetime_id": T1, "amount_c": 615327500, "currency": "USD"})
        self.assertEqual(10000, bal(rt, TREASURY, "1010"))
        rt.marks.set_quote(sc.rec("AAPL", "339.50", now - 60, now))
        req2 = dict(account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=1000, order_type="MARKET")
        pv = orders.preview(rt, req2)
        # need 1,000 x 339.50 + 5.00 = 339,505.00; own 0.00; Treasury 100.00 -> available 100.00: not fundable now, so it queues
        f = pv["funding"]
        self.assertEqual((True, [], "339505.00", "0.00", "100.00", "100.00", False, True), (pv["ok"], pv["blockers"], f["need"], f["available"], f["treasury_available"],
                                                                                          f["sweep_in"], f["ok"], f["queued"]))
        self.assertIn("will queue: waiting for funds (needs USD 339,505.00, available USD 100.00); it executes automatically when cash frees up", pv["warnings"])
        self.assertEqual((False, True), (pv["execution"]["fill_now"], pv["execution"]["queued"]))
        n_cmd = sc.one(rt, "SELECT COUNT(*) n FROM commands")["n"]
        r = sc.submit(rt, **req2)
        self.assertTrue(r.ok, r.body)
        o = r.body["order"]
        self.assertEqual(("SUSPENDED", 0, "waiting for funds: needs USD 339,505.00, available USD 100.00"), (o["status"], o["filled_qty"], o["reason"]))
        # one command, the order stored, no fill and no second sweep; one INFO item; the queued order reserves nothing (available stays 0.00,
        # buying power = Treasury 100.00)
        self.assertEqual((n_cmd + 1, 2, 1, 1), (sc.one(rt, "SELECT COUNT(*) n FROM commands")["n"], sc.one(rt, "SELECT COUNT(*) n FROM orders")["n"],
                                                sc.one(rt, "SELECT COUNT(*) n FROM transfers WHERE kind='SWEEP_IN'")["n"],
                                                sc.one(rt, "SELECT COUNT(*) n FROM attention WHERE code='ORDER_WAITING_FUNDS' AND severity='INFO' AND resolved_ts IS NULL")["n"]))
        self.assertEqual((0, 10000), (rt.accounting.cash(US_EXCHANGE)["USD"].reserved_c, rt.accounting.buying_power(US_EXCHANGE, "USD").total_c))
        assert_integrity(self, rt, "full")

    def test_d2_buy_100_aapl_zero_cash(self):
        # D2: BUY 100 AAPL with 0 cash in the account at the observed last 339.50 (no ask in the free feed, disclosed)
        rt = self.rt
        now = rt.clock.epoch()
        rt.marks.set_quote(sc.rec("AAPL", "339.50", now - 120, now))
        self.assertEqual(0, bal(rt, US_EXCHANGE, "1010"))
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=100, order_type="MARKET")
        self.assertTrue(r.ok, r.body)
        fill = r.body["fills"][0]
        # gross 100 x 339.50 = 33,950.00; fee max(100 x 0.005 = 0.50, 1.00) = 1.00; sweep 33,951.00 linked to the fill; settles 2026-10-21
        self.assertEqual((3395000, 100, "2026-10-21", "last"), (fill["gross_c"], fill["fee_c"], fill["settle_date"], fill["price_basis"]))
        self.assertEqual("fills at the last observed trade price; no bid/ask is available from the free feed", r.body["preview"]["execution"]["assumption"])
        sweep = sc.one(rt, "SELECT * FROM transfers WHERE kind='SWEEP_IN'")
        self.assertEqual((3395100, 3395100, "fill", fill["fill_id"]), (sweep["amount_c"], sweep["shortfall_c"], sweep["linked_type"], sweep["linked_id"]))
        self.assertEqual((3395100, 3395100), (bal(rt, US_EXCHANGE, "1010"), bal(rt, US_EXCHANGE, "2100")))
        # NAV(T1:ALL) = 10,000,000.00 - 1.00 = 9,999,999.00
        self.assertEqual(999999900, rt.accounting.nav("T1:ALL").nav_c)
        self.assertEqual(1000000000 - 3395100, bal(rt, TREASURY, "1010"))
        assert_integrity(self, rt, "full")

    def test_overdraft_rule_funding_shortfall(self):
        # §11.6: a VM loss that cannot be funded is still posted; 1010 goes negative under an open FUNDING_SHORTFALL item; new orders refused
        rt = self.rt
        now = rt.clock.epoch()
        fut = sc.one(rt, "SELECT instrument_id FROM instruments WHERE kind='FUTURE' ORDER BY instrument_id LIMIT 1")["instrument_id"]
        execute(rt, "treasury.withdraw", {"lifetime_id": T1, "amount_c": 999990000, "currency": "USD"})        # Treasury 100.00 left
        r = execute(rt, "test.vm_loss", {"account_id": US_EXCHANGE, "amount_c": 500000, "instrument_id": fut, "key": "t1"})
        self.assertTrue(r.ok, r.body)
        f = r.body["funding"]
        # C12 a (the one cascade): need 5,000.00; own 0.00; Treasury sweeps what it has, min(5,000.00, 100.00) = 100.00, as ONE linked
        # SWEEP_IN; no PB (not a PB account); the rest 5,000.00 - 100.00 = 4,900.00 is posted under the overdraft rule:
        # 1010 = 0.00 + 100.00 - 5,000.00 = -4,900.00
        self.assertEqual((False, 490000, "sweep+overdraft", 10000, 10000), (f["funded"], f["unfunded_c"], f["source"], f["sweep_c"], f["treasury_available_c"]))
        self.assertEqual(-490000, bal(rt, US_EXCHANGE, "1010"))
        sw = sc.one(rt, "SELECT amount_c, shortfall_c FROM transfers WHERE kind='SWEEP_IN'")
        self.assertEqual((10000, 10000), (sw["amount_c"], sw["shortfall_c"]))           # L19: the sweep moves exactly the part it covers
        att = sc.one(rt, "SELECT * FROM attention WHERE code='FUNDING_SHORTFALL' AND resolved_ts IS NULL")
        self.assertEqual(("ERROR", US_EXCHANGE), (att["severity"], att["account_id"]))
        assert_integrity(self, rt, "full")                                                   # L18 accepts the negative 1010 while the item is open
        rt.marks.set_quote(sc.rec("AAPL", "339.50", now - 60, now))
        rs = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=1, order_type="LIMIT", limit_px="300.00")
        self.assertEqual((409, "FUNDING_SHORTFALL"), (rs.http, rs.code))
        # a deposit + transfer of 4,900.00 brings 1010 back to 0 -> the item auto-resolves at the transfer (Build 2 P04: a transfer resolves the
        # lifetime's covered shortfalls; the housekeeping job then finds nothing left)
        deposit(rt, 10000000)
        r = fund(rt, US_EXCHANGE, 490000)
        self.assertEqual(0, bal(rt, US_EXCHANGE, "1010"))
        self.assertEqual(1, r.body["shortfalls_resolved"])
        rr = execute(rt, "test.resolve", {"account_id": US_EXCHANGE})
        self.assertEqual(0, rr.body["resolved"])
        self.assertEqual(0, sc.one(rt, "SELECT COUNT(*) n FROM attention WHERE code='FUNDING_SHORTFALL' AND resolved_ts IS NULL")["n"])
        rs = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=1, order_type="LIMIT", limit_px="300.00")
        self.assertTrue(rs.ok, rs.body)
        assert_integrity(self, rt, "full")

    def test_fund_order_own_then_sweep(self):
        # fund(): own available first (no sweep when it covers), exactly the shortfall otherwise, linked and recorded (L19)
        rt = self.rt
        fund(rt, US_EXCHANGE, 10000000)
        now = rt.clock.epoch()
        rt.marks.set_quote(sc.rec("AAPL", "339.50", now - 60, now))
        # 100 x 339.50 + 1.00 = 33,951.00 <= 100,000.00 own -> no sweep
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=100, order_type="MARKET")
        self.assertEqual(([], "own"), (r.body["sweeps"], r.body["fills"][0]["funding"]["source"]))
        # 300 x 339.50 + 1.50 = 101,851.50; available = 100,000 - 33,951 = 66,049.00 -> sweep 35,802.50
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=300, order_type="MARKET")
        self.assertEqual((3580250, "sweep"), (r.body["fills"][0]["funding"]["shortfall_c"], r.body["fills"][0]["funding"]["source"]))
        self.assertEqual(3580250, r.body["sweeps"][0]["amount_c"])
        assert_integrity(self, rt, "full")


if __name__ == "__main__":
    unittest.main()
