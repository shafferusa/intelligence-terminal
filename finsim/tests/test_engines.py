import unittest
from datetime import date

from tests.helpers import D, assert_ledger_invariants, make_world
from finsim.calendar import BusinessCalendar, SettlementConfig, settlement_date
from finsim.domain.models import YieldCurve
from finsim.engines.market import TENORS, MarketEngine, build_universe
from finsim.engines.pricing import BondPricer
from finsim.world import CommandError


class CalendarTest(unittest.TestCase):
    def test_holidays_and_settlement(self):
        cal = BusinessCalendar()
        self.assertIn(date(2026, 9, 7), cal.holidays(2026))      # Labor Day
        self.assertIn(date(2026, 7, 3), cal.holidays(2026))      # July 4 observed Friday
        self.assertFalse(cal.is_business_day(date(2026, 9, 7)))
        cfg = SettlementConfig()
        self.assertEqual(settlement_date(cal, cfg, "US_EQUITY", date(2026, 9, 4)), date(2026, 9, 8))
        self.assertEqual(settlement_date(cal, cfg, "EU_EQUITY", date(2026, 9, 4)), date(2026, 9, 9))
        with self.assertRaises(KeyError):
            cfg.cycle_for("MARS_EQUITY")


class MarketTest(unittest.TestCase):
    def test_deterministic(self):
        w1, _, _ = make_world(seed=7)
        w2, _, _ = make_world(seed=7)
        w3, _, _ = make_world(seed=8)
        self.assertEqual([b.close for b in w1.market.history["NVRA"]], [b.close for b in w2.market.history["NVRA"]])
        self.assertNotEqual([b.close for b in w1.market.history["NVRA"]], [b.close for b in w3.market.history["NVRA"]])
        w1.advance(5); w2.advance(5)
        self.assertEqual(w1.market.last_bar("PTRX").close, w2.market.last_bar("PTRX").close)

    def test_correlation_structure(self):
        """Stocks share a market factor: cross-sectional correlation is clearly positive."""
        import math
        w, _, _ = make_world(seed=3)
        h = w.market.history
        def rets(t):
            c = [float(b.close) for b in h[t]]
            return [math.log(c[i] / c[i - 1]) for i in range(1, len(c))]
        a, b = rets("NVRA"), rets("SPXE")
        ma, mb = sum(a) / len(a), sum(b) / len(b)
        cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        va = sum((x - ma) ** 2 for x in a); vb = sum((y - mb) ** 2 for y in b)
        self.assertGreater(cov / math.sqrt(va * vb), 0.4)

    def test_ex_dividend_drop_and_spread_widening(self):
        w, _, _ = make_world(seed=11, regime="LIQUIDITY_STRESS")
        sec = w.securities["MRDN"]
        bar = w.market.last_bar("MRDN")
        self.assertGreater(float((bar.ask - bar.bid) / bar.close) * 1e4, sec.spread_bps * 2)


class BondPricingTest(unittest.TestCase):
    def setUp(self):
        self.start = date(2026, 1, 5)
        self.secs = build_universe(self.start, 1)

    def test_par_bond_on_flat_curve(self):
        b = self.secs["UST-5Y"]
        flat = YieldCurve(self.start.isoformat(), TENORS, [b.coupon] * len(TENORS))
        px = BondPricer.clean_price_from_curve(b, flat, self.start)
        self.assertAlmostEqual(float(px), 100.0, delta=0.05)
        m = BondPricer.risk_metrics(b, self.start, float(px), flat)
        self.assertAlmostEqual(m["ytm"], b.coupon, places=4)
        self.assertGreater(m["modified_duration"], 4.0)
        self.assertLess(m["modified_duration"], 5.0)
        self.assertGreater(m["convexity"], 0)
        self.assertGreater(m["dv01_per_100"], 0.04)

    def test_accrued_interest_grows_and_resets(self):
        b = self.secs["UST-10Y"]
        a0 = BondPricer.accrued_per_100(b, self.start)
        cd = BondPricer.coupon_dates(b)[0]
        issue = date.fromisoformat(b.issue_date)
        self.assertEqual(issue, date(2025, 9, 15))
        self.assertEqual(cd, date(2026, 3, 15))
        self.assertGreater(a0, D(0), "seasoned bond has accrued at world start")
        self.assertAlmostEqual(float(a0), b.coupon * 100 / 2 * ((self.start - issue).days / (cd - issue).days), places=4)
        self.assertEqual(BondPricer.accrued_per_100(b, cd), D(0))
        self.assertEqual(BondPricer.accrued_per_100(b, issue), D(0))

    def test_credit_spread_lowers_price(self):
        flat = YieldCurve(self.start.isoformat(), TENORS, [0.04] * len(TENORS))
        g = self.secs["UST-5Y"]; c = self.secs["PTRX-32"]
        self.assertGreater(BondPricer.yield_to_maturity(c, self.start, float(BondPricer.clean_price_from_curve(c, flat, self.start))),
                           BondPricer.yield_to_maturity(g, self.start, float(BondPricer.clean_price_from_curve(g, flat, self.start))))


class ExecutionTest(unittest.TestCase):
    def test_large_order_partially_fills_and_pays_impact(self):
        w, pf, _ = make_world(capital=1_000_000_000)
        o = w.place_order(pf.id, "ZYNQ", "BUY", 500_000, time_in_force="GTC")   # small cap, ADV 380k
        w.advance(1)
        self.assertEqual(o.status, "PARTIALLY_FILLED")
        t = pf.trades[o.trade_ids[0]]
        self.assertLess(t.quantity, D(500_000))
        self.assertGreater(t.execution_detail["impact_bps"], 20)
        self.assertTrue(t.execution_detail["partial"])
        filled_before = o.filled_quantity
        w.advance(1)
        self.assertGreater(o.filled_quantity, filled_before, "GTC remainder works at the next session")
        assert_ledger_invariants(self, w, pf)

    def test_day_order_expires(self):
        w, pf, _ = make_world()
        bar = w.market.last_bar("NVRA")
        o = w.place_order(pf.id, "NVRA", "BUY", 100, order_type="LIMIT", limit_price=bar.bid * D("0.5"))
        self.assertEqual(o.status, "WORKING")
        w.advance(1)
        self.assertEqual(o.status, "EXPIRED", "good-for-day = good for the next session")

    def test_limit_fills_only_when_marketable(self):
        w, pf, _ = make_world()
        bar = w.market.last_bar("HLXB")
        o = w.place_order(pf.id, "HLXB", "BUY", 100, order_type="LIMIT", limit_price=bar.ask * D("1.05"), time_in_force="GTC")
        o2 = w.place_order(pf.id, "HLXB", "BUY", 100, order_type="LIMIT", limit_price=bar.bid * D("0.7"), time_in_force="GTC")
        w.advance(1)
        self.assertEqual(o.status, "FILLED")
        self.assertLessEqual(pf.trades[o.trade_ids[0]].price, bar.ask * D("1.05"))
        self.assertEqual(o2.status, "WORKING")
        self.assertIn("not reached", o2.reason)
        w.cancel_order(pf.id, o2.id)
        self.assertEqual(o2.status, "CANCELLED")

    def test_stop_order_triggers(self):
        w, pf, _ = make_world()
        bar = w.market.last_bar("PTRX")
        w.place_order(pf.id, "PTRX", "BUY", 1000)
        w.advance(1)
        o = w.place_order(pf.id, "PTRX", "SELL", 1000, order_type="STOP", stop_price=bar.close * D("0.97"), time_in_force="GTC")
        self.assertEqual(o.status, "WORKING")
        for _ in range(40):
            w.advance(1)
            if o.status == "FILLED":
                break
        self.assertEqual(o.status, "FILLED")
        self.assertTrue(o.triggered)

    def test_rejections(self):
        w, pf, _ = make_world(capital=100_000)
        with self.assertRaises(CommandError):
            w.place_order(pf.id, "NVRA", "SELL", 100)          # no short selling in phase 1
        with self.assertRaises(CommandError):
            w.place_order(pf.id, "NVRA", "BUY", 100_000)       # insufficient projected cash
        with self.assertRaises(CommandError):
            w.place_order(pf.id, "UST-10Y", "BUY", 1500)       # lot size
        with self.assertRaises(CommandError):
            w.place_order(pf.id, "NOPE", "BUY", 1)
        with self.assertRaises(CommandError):
            w.place_order(pf.id, "NVRA", "BUY", 10, order_type="TRAILING_STOP")     # needs trail pct
        with self.assertRaises(CommandError):
            w.place_order(pf.id, "NVRA", "BUY", 10, condition={"ref": "NOPE", "op": "<=", "value": 1})
        self.assertTrue(any(o.status == "REJECTED" for o in pf.orders.values()))
        assert_ledger_invariants(self, w, pf)


class SettlementTest(unittest.TestCase):
    def test_buy_fails_for_insufficient_settled_cash_and_retries(self):
        w, pf, _ = make_world(capital=10_000_000)
        # D0 evening: buy NVRA. D1: fills (settles D2). D2 evening: sell NVRA and buy HLXB with the projected
        # proceeds. D3: both fill, both settle D4. D4: RVP (HLXB) is processed first and fails — the NVRA
        # proceeds are not yet settled cash — then the NVRA DVP settles. D5: the retry succeeds, late.
        w.place_order(pf.id, "NVRA", "BUY", 20000)
        w.advance(2)
        self.assertTrue(all(t.status == "SETTLED" for t in pf.trades.values()))
        o2 = w.place_order(pf.id, "NVRA", "SELL", 20000)
        o3 = w.place_order(pf.id, "HLXB", "BUY", 60000)
        w.advance(1)
        t2, t3 = pf.trades[o2.trade_ids[0]], pf.trades[o3.trade_ids[0]]
        self.assertEqual(t3.status, "SETTLEMENT_PENDING")
        w.advance(1)
        self.assertEqual(t3.status, "FAILED")
        si3 = pf.settlements[t3.settlement_instruction_id]
        self.assertEqual(si3.status, "FAILED")
        self.assertTrue("insufficient settled" in si3.fail_reason or "unmatched" in si3.fail_reason)
        self.assertIn(t2.status, ("SETTLED", "FAILED"))
        self.assertTrue(any("settlement" in a["text"].lower() and "failed" in a["text"].lower() for a in pf.briefings[-1]["attention"]))
        w.advance(2)
        self.assertEqual(t3.status, "SETTLED")
        self.assertGreaterEqual(si3.fail_count, 1)
        self.assertTrue(any(h["note"].startswith("settled DVP (late") for h in si3.history))
        assert_ledger_invariants(self, w, pf)


class BondLifecycleTest(unittest.TestCase):
    def test_bond_accrual_coupon_and_pnl(self):
        w, pf, _ = make_world(capital=50_000_000, start="2026-03-02")
        sec = w.securities["UST-10Y"]
        led = w.ledgers[pf.id]
        o = w.place_order(pf.id, "UST-10Y", "BUY", 10_000_000)
        w.advance(1)
        t = pf.trades[o.trade_ids[0]]
        self.assertGreater(t.accrued_interest, 0, "buying between coupons pays accrued")
        self.assertEqual(t.net_amount, t.gross_amount + t.accrued_interest + t.commission)
        pos = pf.positions["UST-10Y"]
        self.assertEqual(pos.accrued_interest, t.accrued_interest)
        w.advance(3)
        self.assertGreater(pos.accrued_interest, t.accrued_interest)
        self.assertEqual(led.balance("1220"), pos.accrued_interest)
        self.assertGreater(led.balance("4300"), 0)
        # run to the coupon date
        cd = BondPricer.coupon_dates(sec)
        next_cd = next(d for d in cd if d > w.current_date)
        while w.current_date <= next_cd:
            w.advance(1)
        coupons = [m for m in pf.cash_movements if m.kind == "COUPON"]
        self.assertEqual(len(coupons), 1)
        self.assertEqual(coupons[0].amount, D(10_000_000) * D(sec.coupon) / 2)
        self.assertLess(pos.accrued_interest, coupons[0].amount)
        s = pf.nav_history[-1]
        self.assertEqual(sum((v for k, v in s.explain.items() if not k.startswith("_")), D(0)), s.day_pnl)
        assert_ledger_invariants(self, w, pf)
        # sell: accrued sold comes off the receivable
        o2 = w.place_order(pf.id, "UST-10Y", "SELL", 10_000_000)
        w.advance(1)
        self.assertEqual(pos.quantity, D(0))
        self.assertEqual(pos.accrued_interest, D(0))
        self.assertEqual(led.security_balance("UST-10Y", "1220"), D(0))
        assert_ledger_invariants(self, w, pf)


class MultiPortfolioAndInterestTest(unittest.TestCase):
    def test_cash_interest_accrues_and_settles_monthly(self):
        w, pf, _ = make_world(start="2026-01-28")
        led = w.ledgers[pf.id]
        w.advance(1)
        self.assertGreater(led.balance("1230"), 0)
        w.advance(3)   # crosses into February
        self.assertTrue(any(m.kind == "INTEREST" for m in pf.cash_movements))
        self.assertEqual(led.balance("1230"), pf.cash["USD"].accrued_interest)
        assert_ledger_invariants(self, w, pf)

    def test_multiple_portfolios_are_independent(self):
        w, pf, _ = make_world()
        pf2 = w.create_portfolio("Fund II", "LONG_SHORT_EQUITY", D(1_000_000))
        w.place_order(pf.id, "NVRA", "BUY", 100)
        w.advance(2)
        self.assertEqual(pf.positions["NVRA"].settled_quantity, D(100))
        self.assertNotIn("NVRA", pf2.positions)
        self.assertEqual(w.ledgers[pf2.id].balance("1100"), D(0))
        assert_ledger_invariants(self, w, pf)
        assert_ledger_invariants(self, w, pf2)


if __name__ == "__main__":
    unittest.main()
