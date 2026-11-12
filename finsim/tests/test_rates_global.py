"""Global index futures, Ultra Treasury contracts, options on financial futures, TIPS and the Treasury FRN."""
import unittest

try:
    from helpers import make_world, assert_ledger_invariants
except ImportError:
    from tests.helpers import make_world, assert_ledger_invariants

from finsim.engines.commodities import GLOBAL_INDICES
from finsim.engines.options import FUT_STRIKE_STEP
from finsim.money import D, ZERO
from finsim.world import World


class RatesAndIndexTest(unittest.TestCase):
    def test_global_indices_and_their_futures(self):
        w, pf, store = make_world(capital=50_000_000)
        for code, (etf, ccy, name, lvl) in GLOBAL_INDICES.items():
            idx = w.securities[code]
            self.assertEqual(idx.index_level_source, etf); self.assertEqual(idx.currency, ccy)
            level = float(w.market.last_bar(code).close); e = float(w.market.last_bar(etf).close)
            self.assertAlmostEqual(level, e * idx.index_factor, delta=0.05, msg="the index rides its ETF")
        for code, ccy in (("FESX", "EUR"), ("FDAX", "EUR"), ("FTSE", "GBP"), ("NKD", "USD"), ("HSIF", "HKD"), ("MME", "USD"), ("MFS", "USD")):
            f = w.market.front_contract(code)
            self.assertIsNotNone(f, code); self.assertEqual(f.currency, ccy)
            src = w.securities[code if False else {"FESX": "SX5E", "FDAX": "DAX", "FTSE": "UKX", "NKD": "NKY", "HSIF": "HSI", "MME": "MXEF", "MFS": "MXEA"}[code]]
            self.assertLess(abs(float(w.market.last_bar(f.id).close) / float(w.market.last_bar(src.id).close) - 1.0), 0.03)
        f = w.market.front_contract("FESX")
        w.place_order(pf.id, f.id, "BUY", 20)
        w.advance(3)
        pos = pf.position(f.id)
        self.assertEqual(pos.quantity, D(20))
        self.assertTrue(any(m.kind == "VARIATION_MARGIN" and m.currency == "EUR" for m in pf.cash_movements), "Euro Stoxx variation margin settles in euros")
        assert_ledger_invariants(self, w, pf)
        w2 = World.load(store, w.id)
        self.assertEqual(w2.replay_errors, []); self.assertEqual(w2.ledgers[pf.id].nav(), w.ledgers[pf.id].nav())

    def test_ultra_contracts_and_options_on_financial_futures(self):
        w, pf, store = make_world(capital=50_000_000)
        tn, ub, zn = w.market.front_contract("TN"), w.market.front_contract("UB"), w.market.front_contract("ZN")
        self.assertIsNotNone(tn); self.assertIsNotNone(ub)
        self.assertGreater(float(w.market.last_bar(ub.id).close), 50.0)
        under = w.options.optionable_futures()
        for code in ("ZN", "ZF", "ZB", "TN", "UB", "SR3", "ES", "NQ", "FGBL"):
            self.assertTrue(any(u.startswith(code) for u in under), f"options on {code}")
        chain = [s for s in w.securities.values() if s.is_option and s.underlying == zn.id]
        self.assertGreaterEqual(len(chain), 20)
        strikes = sorted({s.strike for s in chain})
        self.assertAlmostEqual(strikes[1] - strikes[0], FUT_STRIKE_STEP["ZN"], places=6)
        sr = w.market.front_contract("SR3")
        chain_sr = [s for s in w.securities.values() if s.is_option and s.underlying == sr.id]
        ks = sorted({s.strike for s in chain_sr})
        self.assertAlmostEqual(ks[1] - ks[0], 0.125, places=6)
        atm = min(chain_sr, key=lambda s: abs(s.strike - float(w.market.last_bar(sr.id).close)))
        q = w.options.quote(atm)
        self.assertLess(q["iv"], 0.03, "SOFR options are marked at rate-future vol, not equity vol")
        self.assertLess(q["mid"], 0.5, "an ATM SOFR option is worth tens of basis points, not points")
        znatm = min((s for s in chain if s.option_type == "C"), key=lambda s: abs(s.strike - float(w.market.last_bar(zn.id).close)))
        qz = w.options.quote(znatm)
        self.assertGreater(qz["mid"], 0.2); self.assertLess(qz["mid"], 3.0)
        w.place_order(pf.id, znatm.id, "BUY", 10)
        w.advance(2)
        self.assertEqual(pf.position(znatm.id).quantity, D(10))
        assert_ledger_invariants(self, w, pf)

    def test_tips_and_the_frn(self):
        w, pf, store = make_world(capital=50_000_000)
        t10, frn, ust = w.securities["TIPS-10Y"], w.securities["UST-FRN-2Y"], w.securities["UST-10Y"]
        self.assertTrue(t10.inflation_linked); self.assertGreater(t10.index_ratio, 1.0); self.assertAlmostEqual(t10.coupon, t10.real_coupon * t10.index_ratio, places=6)
        self.assertLess(t10.real_coupon, ust.coupon)
        from finsim.api.service import Service
        svc = Service.__new__(Service); svc.worlds = {w.id: w}
        m = Service._tips_metrics(w, t10, float(w.market.last_bar(t10.id).close))
        self.assertGreater(m["breakeven"], 0.01); self.assertLess(m["breakeven"], 0.04)
        self.assertLess(m["real_yield"], m["ytm"]); self.assertAlmostEqual(m["ytm"] - m["real_yield"], m["breakeven"], places=6)
        self.assertTrue(frn.floating); self.assertAlmostEqual(frn.coupon, w.market.curve().policy_rate + 0.0015, delta=0.004)
        # buy both, hold through a quarterly FRN coupon and a couple of months of index accretion
        w.place_order(pf.id, t10.id, "BUY", 5_000_000); w.place_order(pf.id, frn.id, "BUY", 5_000_000)
        w.advance(2)
        r0 = t10.index_ratio
        w.advance(70)
        self.assertNotEqual(t10.index_ratio, r0, "the index ratio moves with the CPI")
        self.assertTrue(any(e.type == "COUPON_PAID" and e.payload["security_id"] == frn.id for e in w.events), "the FRN paid a quarterly coupon")
        assert_ledger_invariants(self, w, pf)
        w2 = World.load(store, w.id)
        self.assertEqual(w2.replay_errors, []); self.assertEqual(w2.securities[t10.id].index_ratio, t10.index_ratio)
        self.assertEqual(w2.ledgers[pf.id].nav(), w.ledgers[pf.id].nav())


if __name__ == "__main__":
    unittest.main()
