"""FX crosses deal through the dollar (except EUR/GBP); London listings quote in pence and settle in pounds."""
import unittest
from datetime import date

try:
    from helpers import make_world, assert_ledger_invariants
except ImportError:
    from tests.helpers import make_world, assert_ledger_invariants

from finsim.engines.realfeed import RealFeed, equity_scales_for
from finsim.money import D, ZERO, money
from finsim.world import World, CommandError


class FXRoutingTest(unittest.TestCase):
    def test_crosses_go_through_the_dollar(self):
        w, pf, store = make_world(capital=50_000_000)
        w.fx_spot(pf.id, "EUR", "USD", 3_000_000, "BUY")
        w.advance(3)
        n0 = len(pf.fx_trades)
        t = w.fx_spot(pf.id, "JPY", "EUR", 100_000_000, "BUY")            # yen for euros: two legs
        legs = [x for x in pf.fx_trades.values() if x.id not in {}][n0:]
        self.assertEqual(len(pf.fx_trades) - n0, 2)
        leg1, leg2 = sorted(list(pf.fx_trades.values())[n0:], key=lambda x: x.id)
        self.assertEqual((leg1.buy_ccy, leg1.sell_ccy), ("USD", "EUR")); self.assertEqual((leg2.buy_ccy, leg2.sell_ccy), ("JPY", "USD"))
        self.assertEqual(t.id, leg2.id); self.assertEqual(leg2.buy_amount, D(100_000_000)); self.assertAlmostEqual(float(leg1.buy_amount), float(leg2.sell_amount), delta=1.0)
        self.assertIn("through the dollar", leg1.route); self.assertIn("leg 2", leg2.route)
        w.advance(3)
        self.assertGreaterEqual(pf.cash_account("JPY").balance, D(100_000_000)); self.assertTrue(all(x.status == "SETTLED" for x in (leg1, leg2)))
        assert_ledger_invariants(self, w, pf)
        # selling a known amount of the cross currency
        t2 = w.fx_spot(pf.id, "CHF", "JPY", 50_000_000, "SELL")
        self.assertEqual(t2.sell_ccy, "USD"); self.assertEqual(t2.buy_ccy, "CHF")
        # EUR/GBP is dealt direct; a yen forward against euros is not a dealt pair
        g = w.fx_spot(pf.id, "GBP", "EUR", 100_000, "BUY")
        self.assertEqual((g.buy_ccy, g.sell_ccy), ("GBP", "EUR")); self.assertIsNone(g.route)
        with self.assertRaises(CommandError):
            w.fx_forward(pf.id, "JPY", "EUR", 1_000_000, w.calendar.add_business_days(w.current_date, 60).isoformat())
        w.fx_forward(pf.id, "EUR", "GBP", 100_000, w.calendar.add_business_days(w.current_date, 60).isoformat())
        w.advance(3)
        assert_ledger_invariants(self, w, pf)
        w2 = World.load(store, w.id)
        self.assertEqual(w2.replay_errors, []); self.assertEqual(w2.portfolio(pf.id).fx_trades[leg2.id].route, leg2.route)

    def test_london_quotes_in_pence_settle_in_pounds(self):
        w, pf, _ = make_world(capital=50_000_000)
        lse = [s for s in w.securities.values() if s.asset_class == "EQUITY" and s.country == "GB" and s.currency == "GBP"]
        if not lse:
            self.skipTest("no London listings in this universe snapshot")
        shel = next((s for s in lse if s.id == "SHEL-L"), lse[0])
        self.assertEqual(shel.yahoo_scale, 0.01); self.assertEqual(shel.market, "UK_EQUITY")
        px = float(w.market.last_bar(shel.id).close)
        self.assertLess(px, 500.0, "pounds, not pence"); self.assertGreater(px, 1.0)
        scales = equity_scales_for(w.securities)
        self.assertEqual(scales[shel.id], 0.01)
        feed = RealFeed({shel.id: shel.yahoo, "AAPL": "AAPL", "SPY": "SPY"}, cache_path="/dev/null", fetch=lambda syms, rng: {}, scales=scales)
        feed.data = {shel.yahoo: {"2026-01-05": 2850.0}, "AAPL": {"2026-01-05": 250.0}, "SPY": {"2026-01-05": 600.0}}
        t = feed.targets_for(date(2026, 1, 5))
        self.assertAlmostEqual(t["equities"][shel.id], 28.5, places=6, msg="the pin is in pounds"); self.assertEqual(t["equities"]["AAPL"], 250.0)
        h = feed.history(date(2026, 1, 1), date(2026, 1, 31))
        self.assertAlmostEqual(h["equities"][shel.id]["2026-01-05"], 28.5, places=6)


if __name__ == "__main__":
    unittest.main()
