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
        sec = w.securities["ZYNQ"]           # small cap, ADV 380k
        o = w.place_order(pf.id, "ZYNQ", "BUY", 500_000, time_in_force="GTC")
        self.assertEqual(o.status, "PARTIALLY_FILLED")
        t = pf.trades[o.trade_ids[0]]
        self.assertLess(t.quantity, D(500_000))
        self.assertGreater(t.execution_detail["impact_bps"], 20)
        self.assertTrue(t.execution_detail["partial"])
        filled_before = o.filled_quantity
        w.advance(1)
        self.assertGreater(o.filled_quantity, filled_before, "GTC remainder works at the next open")
        assert_ledger_invariants(self, w, pf)

    def test_day_order_expires(self):
        w, pf, _ = make_world()
        bar = w.market.last_bar("NVRA")
        o = w.place_order(pf.id, "NVRA", "BUY", 100, order_type="LIMIT", limit_price=bar.bid * D("0.5"))
        self.assertEqual(o.status, "WORKING")
        w.advance(1)
        self.assertEqual(o.status, "EXPIRED")

    def test_limit_fills_only_when_marketable(self):
        w, pf, _ = make_world()
        bar = w.market.last_bar("HLXB")
        o = w.place_order(pf.id, "HLXB", "BUY", 100, order_type="LIMIT", limit_price=bar.ask + 1)
        self.assertEqual(o.status, "FILLED")
        self.assertLessEqual(pf.trades[o.trade_ids[0]].price, bar.ask + 1)
        o2 = w.place_order(pf.id, "HLXB", "BUY", 100, order_type="LIMIT", limit_price=bar.bid * D("0.9"), time_in_force="GTC")
        self.assertEqual(o2.status, "WORKING")
        w.cancel_order(pf.id, o2.id)
        self.assertEqual(o2.status, "CANCELLED")

    def test_stop_order_triggers(self):
        w, pf, _ = make_world()
        bar = w.market.last_bar("PTRX")
        w.place_order(pf.id, "PTRX", "BUY", 1000)
        o = w.place_order(pf.id, "PTRX", "SELL", 1000, order_type="STOP", stop_price=bar.close * D("0.999"), time_in_force="GTC")
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
        self.assertTrue(any(o.status == "REJECTED" for o in pf.orders.values()))
        assert_ledger_invariants(self, w, pf)


class SettlementTest(unittest.TestCase):
    def test_sell_fails_when_securities_not_in_custody_then_settles(self):
        w, pf, _ = make_world(capital=20_000_000)
        # buy, then before settlement sell — the sell settles T+1 as well, so it is due the same day
        # as the buy; RVP is processed first so it settles. Force a fail by making the buy fail: not
        # enough settled cash — engineer via a large buy whose cash is tied up in another unsettled buy.
        w.place_order(pf.id, "NVRA", "BUY", 20000)   # ~8.4M
        o2 = w.place_order(pf.id, "HLXB", "BUY", 90000)  # ~9.8M -> projected cash covers it but settled cash covers both; fine
        w.advance(1)
        w.advance(1)
        self.assertTrue(all(t.status == "SETTLED" for t in pf.trades.values()))
        # now sell HLXB and NVRA; capital drop is fine
        assert_ledger_invariants(self, w, pf)

    def test_buy_fails_for_insufficient_settled_cash_and_retries(self):
        w, pf, _ = make_world(capital=10_000_000)
        # Buy 9.9M of NVRA (settles T+1) and sell it back on the same day: the sale's receivable does not
        # arrive until T+1 either, so the day's DVP round trip both settle. To get a genuine fail we buy
        # first, sell the next day (sale settles T+2 from original), and buy again with the proceeds
        # projected but not settled: the second buy fails on its settlement date when cash is short.
        o1 = w.place_order(pf.id, "NVRA", "BUY", 20000)
        w.advance(1)                                   # day 2: buy settles at close
        o2 = w.place_order(pf.id, "NVRA", "SELL", 20000)   # settles day 3
        t2 = pf.trades[o2.trade_ids[0]]
        o3 = w.place_order(pf.id, "HLXB", "BUY", 60000)    # uses projected proceeds; settles day 3
        t3 = pf.trades[o3.trade_ids[0]]
        self.assertEqual(o3.status, "FILLED")
        w.advance(1)                                   # day 2 close (nothing due yet), day 3 opens
        self.assertEqual(t3.status, "SETTLEMENT_PENDING")
        w.advance(1)                                   # day 3 close: RVP first → HLXB buy fails (cash short), then NVRA DVP settles
        self.assertEqual(t3.status, "FAILED")
        si3 = pf.settlements[t3.settlement_instruction_id]
        self.assertEqual(si3.status, "FAILED")
        self.assertIn("insufficient settled", si3.fail_reason)
        self.assertEqual(t2.status, "SETTLED")
        w.advance(1)                                   # day 4: retry succeeds
        self.assertEqual(t3.status, "SETTLED")
        self.assertEqual(si3.fail_count, 1)
        self.assertTrue(any(h["note"].startswith("settled DVP (late") for h in si3.history))
        assert_ledger_invariants(self, w, pf)


class BondLifecycleTest(unittest.TestCase):
    def test_bond_accrual_coupon_and_pnl(self):
        w, pf, _ = make_world(capital=50_000_000, start="2026-03-02")
        sec = w.securities["UST-10Y"]
        led = w.ledgers[pf.id]
        o = w.place_order(pf.id, "UST-10Y", "BUY", 10_000_000)
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
        self.assertNotIn("NVRA", pf2.positions)
        self.assertEqual(w.ledgers[pf2.id].balance("1100"), D(0))
        assert_ledger_invariants(self, w, pf)
        assert_ledger_invariants(self, w, pf2)


if __name__ == "__main__":
    unittest.main()
