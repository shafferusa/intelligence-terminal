"""The Treasury book backstops the others; real ten-year yields anchor the local curves in a tracking save."""
import unittest
from datetime import date

try:
    from helpers import assert_ledger_invariants
except ImportError:
    from tests.helpers import assert_ledger_invariants

from finsim.api.service import Service
from finsim.money import D, ZERO
from finsim.store import EventStore
from finsim.world import World, CommandError


class TreasuryBackstopTest(unittest.TestCase):
    def test_the_treasury_covers_orders_settlements_and_overdrafts(self):
        s = Service(EventStore(":memory:"))
        r = s.create_world("firm", 42, start_date="2026-01-05", capital=50_000_000, treasury=True, clock_mode="SANDBOX")
        w = s.worlds[r["world_id"]]
        tb = w.treasury_book()
        b = s.create_portfolio(w.id, "Equity", "PERSONAL", 0, "PROFESSIONAL", "SANDBOX", None, from_treasury=True)
        pf = w.portfolio(b["portfolio_id"])
        self.assertEqual(pf.cash_account("USD").balance, ZERO)
        # an order the empty book could never pay for alone is accepted on the Treasury's spare cash
        w.place_order(pf.id, "AAPL", "BUY", 10_000)
        w.advance(3)
        self.assertEqual(pf.position("AAPL").settled_quantity, D(10_000), "the settlement drew the shortfall from the Treasury")
        draws = [e for e in w.events if e.type == "TREASURY_ALLOCATED" and e.payload["portfolio_id"] == pf.id and "backstop" in (e.payload.get("note") or "")]
        self.assertTrue(draws); self.assertGreater(pf.contributed_capital, ZERO)
        self.assertLess(tb.cash_account("USD").balance, D(50_000_000))
        assert_ledger_invariants(self, w, pf); assert_ledger_invariants(self, w, tb)
        # a foreign-currency overdraft is covered too, in that currency, when the Treasury holds it
        w.fx_spot(tb.id, "EUR", "USD", 2_000_000, "BUY")
        w.advance(3)
        loan = w.ccy_borrow(pf.id, "EUR", 500_000, 3)
        w.fx_spot(pf.id, "USD", "EUR", 500_000, "SELL")            # spend the euros; the loan matures with nothing to repay it
        w.advance(6)
        self.assertGreaterEqual(pf.cash_account("EUR").balance, ZERO, "no book stays overdrawn while the Treasury has the currency")
        assert_ledger_invariants(self, w, pf); assert_ledger_invariants(self, w, tb)
        # beyond the Treasury's spare cash the usual rejection stands
        with self.assertRaises(CommandError):
            w.place_order(pf.id, "AAPL", "BUY", 5_000_000)
        w2 = World.load(w.store, w.id)
        self.assertEqual(w2.replay_errors, []); self.assertEqual(w2.ledgers[pf.id].nav(), w.ledgers[pf.id].nav())

    def test_real_ten_year_yields_anchor_the_local_curves(self):
        try:
            from helpers import make_world
        except ImportError:
            from tests.helpers import make_world
        w, pf, _ = make_world()
        d = w.current_date
        eur0 = w.market.curve_for_ccy("EUR").rates[7]
        w.market.real_history = {"equities": {}}                     # a tracking save
        w.market.real_macro = {"yield10_DE": {"2025-12-01": 2.50}, "yield10_IT": {"2025-12-01": 3.60}, "yield10_JP": {"2025-12-01": 1.20}}
        de, it, jp = w.market.curve_for_ccy("EUR"), w.market.curve_for_ccy("EUR", country="IT"), w.market.curve_for_ccy("JPY")
        usd_then = next(c.rates[7] for c in reversed(w.market.curves) if c.date <= "2025-12-15")
        drift = 0.6 * (w.market.curve().rates[7] - usd_then)
        self.assertAlmostEqual(de.rates[7], 0.025 + drift, places=5, msg="the Bund ten-year sits on FRED's last print, moved by the dollar since")
        self.assertAlmostEqual(it.rates[7], 0.036 + drift, places=5); self.assertAlmostEqual(jp.rates[7], 0.012 + drift, places=5)
        self.assertNotEqual(de.rates[7], eur0)
        self.assertLess(abs(de.rates[0] - w.market.curve_for_ccy("EUR").rates[0]), 1e-9)
        bund = w.securities["DE-10Y"]
        px = float(w.market.bond_clean_price(bund, w.market.curve(), d))
        self.assertGreater(px, 60.0); self.assertLess(px, 140.0)


if __name__ == "__main__":
    unittest.main()
