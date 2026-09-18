"""The wider universe: new currencies, LME / gas / carbon contracts in euros, native foreign listings settling in their home currency."""
import unittest

try:
    from helpers import make_world, assert_ledger_invariants
except ImportError:
    from tests.helpers import make_world, assert_ledger_invariants

from finsim.engines.fx_market import CURRENCIES, SPECS as FX_SPECS
from finsim.engines.otc_extra import NDF_CURRENCIES
from finsim.money import D, ZERO, money
from finsim.world import World


class UniverseExtraTest(unittest.TestCase):
    def test_new_currencies_and_commodities(self):
        w, pf, store = make_world(capital=50_000_000)
        for c in ("TRY", "TWD", "IDR", "THB", "CZK", "HUF", "SAR"):
            self.assertIn(c, CURRENCIES); self.assertIn(c, w.market.fx.spot); self.assertGreater(w.market.fx.spot[c], 0)
        self.assertIn("TWD", NDF_CURRENCIES); self.assertIn("IDR", NDF_CURRENCIES)
        self.assertGreater(w.market.fx.rate["TRY"], 0.2, "the lira carries a high policy rate")
        self.assertAlmostEqual(w.market.fx.spot["SAR"], 1 / 3.75, delta=0.01)
        t = w.fx_spot(pf.id, "TRY", "USD", 1_000_000, "BUY")
        w.advance(3)
        self.assertEqual(pf.fx_trades[t.id].status, "SETTLED"); self.assertGreaterEqual(pf.cash_account("TRY").balance, D(1_000_000), "the lira arrived (and earns its 40% rate daily)")
        assert_ledger_invariants(self, w, pf)
        for code, ccy in (("NI", "USD"), ("ZNC", "USD"), ("PB", "USD"), ("SN", "USD"), ("TIO", "USD"), ("TTF", "EUR"), ("JKM", "USD"), ("EUA", "EUR")):
            f = w.market.front_contract(code)
            self.assertIsNotNone(f, code); self.assertEqual(f.currency, ccy)
            self.assertGreater(w.market.spot(code), 0)
        ttf = w.market.front_contract("TTF")
        w.place_order(pf.id, ttf.id, "BUY", 10)
        w.advance(2)
        self.assertEqual(pf.position(ttf.id).quantity, D(10))
        self.assertTrue(any(m.currency == "EUR" and m.kind in ("VARIATION_MARGIN", "COMMISSION") for m in pf.cash_movements), "TTF settles its margin in euros")
        self.assertTrue(any(u.startswith("NI") for u in w.options.optionable_futures()), "options on the front nickel contracts")
        assert_ledger_invariants(self, w, pf)

    def test_a_native_listing_settles_in_its_home_currency(self):
        w, pf, store = make_world(capital=50_000_000)
        native = [s for s in w.securities.values() if s.asset_class == "EQUITY" and s.currency != "USD"]
        if not native:
            self.skipTest("no native listings in this universe snapshot")
        by_ccy = {s.currency: s for s in native}
        self.assertNotIn("USD", by_ccy)
        sec = by_ccy.get("HKD") or native[0]
        self.assertNotEqual(sec.market, "US_EQUITY"); self.assertEqual(w.settlement_config.cycle_for(sec.market), 2 if sec.country != "IN" else 1)
        self.assertNotIn(sec.id, w.options.optionable(), "no listed options on a native listing")
        px = w.market.last_bar(sec.id).ask
        q = 10_000
        w.fx_spot(pf.id, sec.currency, "USD", money(D(q) * px * D("1.02")), "BUY")
        w.place_order(pf.id, sec.id, "BUY", q)
        w.advance(1)
        t = [x for x in pf.trades.values() if x.security_id == sec.id][0]
        self.assertEqual(t.currency, sec.currency)
        k = D(str(t.execution_detail["fx_rate"]))
        pos = pf.position(sec.id)
        self.assertAlmostEqual(float(pos.cost_basis), float(t.gross_amount * k), delta=1.0, msg="the book carries the cost in dollars")
        w.advance(3)
        self.assertEqual(pos.settled_quantity, D(q))
        self.assertAlmostEqual(float(pos.market_value), float(money(D(q) * w.market.last_bar(sec.id).close * w.fx.k(sec.currency))), delta=1.0)
        assert_ledger_invariants(self, w, pf)
        w.place_order(pf.id, sec.id, "SELL", q)
        w.advance(4)
        self.assertEqual(pf.position(sec.id).quantity, ZERO)
        self.assertGreater(pf.cash_account(sec.currency).balance, D(0))
        assert_ledger_invariants(self, w, pf)
        w2 = World.load(store, w.id)
        self.assertEqual(w2.replay_errors, []); self.assertEqual(w2.ledgers[pf.id].nav(), w.ledgers[pf.id].nav())


if __name__ == "__main__":
    unittest.main()
