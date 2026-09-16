"""Once-per-day cycle: order types evaluated against the session, futures margining,
expiry, careers, briefings, the real-time clock, and replay of all of it."""
import unittest
from datetime import date, datetime, timezone

from tests.helpers import D, assert_ledger_invariants, make_world
from finsim.clock import ClockConfig
from finsim.domain.events import E
from finsim.store import EventStore
from finsim.world import CommandError, World


class OrderTypesTest(unittest.TestCase):
    def test_conditional_order_waits_for_condition(self):
        w, pf, _ = make_world()
        last = float(w.market.last_bar("NVRA").close)
        o = w.place_order(pf.id, "NVRA", "BUY", 100, time_in_force="GTC", condition={"ref": "NVRA", "op": "<=", "value": last * 0.90})
        far = w.place_order(pf.id, "MRDN", "BUY", 100, time_in_force="GTC", condition={"ref": "CURVE:10Y", "op": ">=", "value": 99.0})
        for _ in range(60):
            w.advance(1)
            if o.status == "FILLED":
                break
        self.assertEqual(far.status, "WORKING")
        if o.status == "FILLED":
            t = pf.trades[o.trade_ids[0]]
            self.assertIsNotNone(o.condition_met_date)
            self.assertLessEqual(float(w.market.history["NVRA"][[b.date for b in w.market.history["NVRA"]].index(t.trade_date)].close), last * 0.90 + 1e-6)
            self.assertEqual(t.execution_detail["session"], "CLOSE")
        self.assertTrue(any("condition not met" in h["note"] for h in o.history))

    def test_trailing_stop_ratchets_and_triggers(self):
        w, pf, _ = make_world()
        w.place_order(pf.id, "QNTM", "BUY", 1000)
        w.advance(1)
        o = w.place_order(pf.id, "QNTM", "SELL", 1000, order_type="TRAILING_STOP", trail_pct=0.03, time_in_force="GTC")
        level0 = o.trail_level
        self.assertIsNotNone(level0)
        levels = [level0]
        for _ in range(80):
            w.advance(1)
            levels.append(o.trail_level)
            if o.status == "FILLED":
                break
        self.assertEqual(o.status, "FILLED")
        self.assertTrue(o.triggered)
        self.assertTrue(all(b >= a for a, b in zip(levels, levels[1:]) if a and b), "trail level only ratchets up for a sell stop")
        assert_ledger_invariants(self, w, pf)

    def test_take_profit_fills_only_when_reached(self):
        w, pf, _ = make_world()
        w.place_order(pf.id, "HLXB", "BUY", 500)
        w.advance(1)
        px = w.market.last_bar("HLXB").close
        o = w.place_order(pf.id, "HLXB", "SELL", 500, order_type="TAKE_PROFIT", limit_price=px * D("1.04"), time_in_force="GTC")
        for _ in range(120):
            w.advance(1)
            if o.status == "FILLED":
                break
        if o.status == "FILLED":
            self.assertGreaterEqual(pf.trades[o.trade_ids[0]].price, o.limit_price)


class FuturesTest(unittest.TestCase):
    def test_variation_margin_and_initial_margin_flow_through_ledger(self):
        w, pf, _ = make_world(capital=50_000_000)
        led = w.ledgers[pf.id]
        cl = [s for s in w.securities.values() if s.underlying == "CL" and not s.expired][1]
        gc = [s for s in w.securities.values() if s.underlying == "GC" and not s.expired][1]
        o1 = w.place_order(pf.id, cl.id, "BUY", 20)
        o2 = w.place_order(pf.id, gc.id, "SELL", 5)            # shorting futures needs no borrow
        w.advance(1)
        self.assertEqual(o1.status, "FILLED")
        self.assertEqual(o2.status, "FILLED")
        p1, p2 = pf.positions[cl.id], pf.positions[gc.id]
        self.assertEqual(p1.quantity, D(20))
        self.assertEqual(p2.quantity, D(-5))
        t1 = pf.trades[o1.trade_ids[0]]
        self.assertEqual(t1.status, "CLEARED")
        self.assertIsNone(t1.settlement_instruction_id)
        settle = w.market.last_bar(cl.id).close
        expected_vm = (settle - t1.price) * 20 * D(1000)
        self.assertEqual(p1.variation_margin_total, expected_vm.quantize(D("0.01")))
        self.assertEqual(led.security_balance(cl.id, "4400"), p1.variation_margin_total)
        self.assertEqual(led.balance("1300"), w.futures.required_margin(pf))
        self.assertGreater(led.balance("1300"), 0)
        self.assertEqual(p1.market_value, D(0), "futures carry no market value: P&L is settled daily in cash")
        assert_ledger_invariants(self, w, pf)
        w.advance(3)
        s = pf.nav_history[-1]
        self.assertEqual(sum((v for k, v in s.explain.items() if not k.startswith("_")), D(0)), s.day_pnl)
        self.assertNotEqual(s.explain["commodities"], D(0))
        # close the long: VM on the day includes the closing fill, position flat, margin released
        w.place_order(pf.id, cl.id, "SELL", 20)
        w.advance(1)
        self.assertEqual(pf.positions[cl.id].quantity, D(0))
        self.assertEqual(pf.positions[cl.id].initial_margin, D(0))
        self.assertEqual(led.balance("1300"), w.futures.required_margin(pf))
        assert_ledger_invariants(self, w, pf)

    def test_margin_call_then_forced_liquidation(self):
        w, pf, _ = make_world(capital=3_000_000)
        cl = [s for s in w.securities.values() if s.underlying == "CL" and not s.expired][1]
        # 40 contracts * ~70 * 1000 = ~2.8MM notional; margin ~8% = 224k. A big adverse move overdraws cash only
        # with leverage, so tie up cash in stock first, leaving little free cash.
        w.place_order(pf.id, "SPXE", "BUY", 5000)
        w.place_order(pf.id, cl.id, "BUY", 25)
        w.advance(2)
        self.assertEqual(pf.positions[cl.id].quantity, D(25))
        # force an overdraft: contribute nothing, and drain cash by buying more stock with projected cash
        avail = w.trading.projected_cash(pf, "USD")
        px = w.market.last_bar("BRWN").ask
        w.place_order(pf.id, "BRWN", "BUY", int(avail / px * D("0.98")))
        w.advance(2)
        # cash is now near zero; any VM loss overdraws. Walk forward until a margin call appears.
        called = False
        for _ in range(60):
            w.advance(1)
            if any(m.status == "OPEN" for m in pf.margin_calls):
                called = True
                break
        if called:
            call = [m for m in pf.margin_calls if m.status == "OPEN"][0]
            self.assertTrue(any(a["severity"] == "HIGH" and "Margin call" in a["text"] for a in pf.briefings[-1]["attention"]))
            for _ in range(6):
                w.advance(1)
                if call.status != "OPEN":
                    break
            self.assertIn(call.status, ("MET", "FORCED"))
            if call.status == "FORCED":
                self.assertTrue(any(t.execution_detail.get("forced") for t in pf.trades.values()))
                self.assertTrue(any(e.type == E.FORCED_LIQUIDATION for e in w.events))
        assert_ledger_invariants(self, w, pf)

    def test_contract_expiry_auto_closes(self):
        w, pf, _ = make_world(capital=50_000_000)
        front = w.market.front_contract("GC")
        o = w.place_order(pf.id, front.id, "BUY", 3)
        w.advance(1)
        self.assertEqual(o.status, "FILLED")
        exp = date.fromisoformat(front.expiry)
        while w.current_date < exp:
            w.advance(1)
        self.assertEqual(w.current_date, exp)
        self.assertEqual(pf.positions[front.id].quantity, D(0))
        self.assertTrue(any(e.type == E.CONTRACT_EXPIRED for e in w.events))
        self.assertTrue(any(t.execution_detail.get("note", "").startswith("auto-close at expiry") for t in pf.trades.values()))
        w.advance(1)
        self.assertTrue(w.securities[front.id].expired)
        with self.assertRaises(CommandError):
            w.place_order(pf.id, front.id, "BUY", 1)
        assert_ledger_invariants(self, w, pf)

    def test_curve_shape_follows_inventories(self):
        from finsim.engines.commodities import SPEC_BY_CODE, CommodityState
        w, _, _ = make_world()
        model = w.market.commodities
        spec = SPEC_BY_CODE["CL"]
        tight = model.curve(spec, CommodityState(70.0, inventory_z=-2.5, conv_yield=spec.conv_base + spec.inv_sens * 2.5), w.current_date, 0.04)
        glut = model.curve(spec, CommodityState(70.0, inventory_z=2.5, conv_yield=spec.conv_base - spec.inv_sens * 2.5), w.current_date, 0.04)
        tv, gv = list(tight.values()), list(glut.values())
        self.assertLess(tv[-1], tv[0], "tight inventories: backwardation")
        self.assertGreater(gv[-1], gv[0], "glut: contango")


class CareerTest(unittest.TestCase):
    def test_mandate_limits_and_reviews(self):
        w, pf, store = make_world(start="2026-03-02")
        fund = w.create_portfolio("Commodity book", "FUND", D(100_000_000), job="COMMODITY_TRADER")
        with self.assertRaises(CommandError):
            w.place_order(fund.id, "NVRA", "BUY", 100)        # equities outside the mandate
        with self.assertRaises(CommandError):
            w.create_portfolio("HF", "FUND", D(1), job="HEDGE_FUND")   # planned job not playable
        cl = [s for s in w.securities.values() if s.underlying == "CL" and not s.expired][2]
        w.place_order(fund.id, cl.id, "BUY", 900)              # tens of MM notional: well over the 35% single-position limit
        w.advance(1)
        self.assertTrue(any(b["kind"] == "CONCENTRATION" for b in fund.breaches))
        self.assertTrue(any("Risk limit breach" in a["text"] for a in fund.briefings[-1]["attention"]))
        while not fund.reviews:
            w.advance(1)
        r = fund.reviews[-1]
        self.assertIn(r["period"], ("MONTH", "QUARTER"))
        self.assertIn(r["rating"], ("EXCEEDS", "MEETS", "BELOW", "UNACCEPTABLE"))
        self.assertGreaterEqual(r["risk_breaches"], 1)
        self.assertIn("largest_contribution", r)
        # quarter-end review (end of March) triggers a career decision
        while w.current_date < date(2026, 4, 1):
            w.advance(1)
        self.assertTrue(any(x["period"] == "QUARTER" for x in fund.reviews))
        assert_ledger_invariants(self, w, fund)
        w2 = World.load(store, "t")
        self.assertEqual(len(w2.portfolios[fund.id].reviews), len(fund.reviews))
        self.assertEqual(w2.portfolios[fund.id].briefings[-1]["attention"], fund.briefings[-1]["attention"])
        self.assertEqual(w2.ledgers[fund.id].nav(), w.ledgers[fund.id].nav())


class ClockTest(unittest.TestCase):
    def test_career_world_processes_days_on_its_own_and_refuses_manual_advance(self):
        store = EventStore(":memory:")
        clock = ClockConfig("REAL_TIME", "America/New_York", "09:00")
        w = World.create("rt", "career", 3, date(2026, 9, 14), store=store, clock=clock)
        pf = w.create_portfolio("Macro", "FUND", D(250_000_000), job="GLOBAL_MACRO")
        with self.assertRaises(CommandError):
            w.advance(1)
        w.place_order(pf.id, "UST-10Y", "BUY", 5_000_000)
        # Tuesday 08:00 ET: not yet
        self.assertEqual(w.catch_up(datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)), [])
        # Tuesday 09:30 ET: Tuesday is processed, the overnight order fills, the briefing is for Tuesday
        closed = w.catch_up(datetime(2026, 9, 15, 13, 30, tzinfo=timezone.utc))
        self.assertEqual(closed, ["2026-09-15"])
        self.assertEqual(pf.briefings[-1]["date"], "2026-09-15")
        self.assertEqual(pf.positions["UST-10Y"].quantity, D(5_000_000))
        # a week away: Monday night the world has caught up through Monday (weekend skipped)
        closed = w.catch_up(datetime(2026, 9, 22, 1, 0, tzinfo=timezone.utc))   # Mon 21:00 ET
        self.assertEqual(closed, ["2026-09-16", "2026-09-17", "2026-09-18", "2026-09-21"])
        self.assertIsNotNone(w.next_update_at(datetime(2026, 9, 22, 1, 0, tzinfo=timezone.utc)))
        self.assertEqual(w.next_update_at(datetime(2026, 9, 22, 1, 0, tzinfo=timezone.utc)).date(), date(2026, 9, 22))
        w2 = World.load(store, "rt")
        self.assertEqual(w2.current_date, w.current_date)
        self.assertEqual(w2.clock.mode, "REAL_TIME")
        self.assertEqual(w2.ledgers[pf.id].nav(), w.ledgers[pf.id].nav())


if __name__ == "__main__":
    unittest.main()
