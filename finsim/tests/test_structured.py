"""Structured credit: floaters reset and carry little rate risk, fixed tranches carry it, spreads move with the indices, coupons pay, replay."""
import unittest

try:
    from helpers import make_world, assert_ledger_invariants
except ImportError:
    from tests.helpers import make_world, assert_ledger_invariants

from finsim.engines.pricing import BondPricer, interp_rate
from finsim.engines.structured import STRUCTURED_SEED
from finsim.engines.collateral import asset_key
from finsim.money import D, ZERO
from finsim.world import World


class StructuredCreditTest(unittest.TestCase):
    def test_tranches_float_or_fix_and_react_to_rates_and_spreads(self):
        w, pf, store = make_world(capital=100_000_000)
        ids = [r[0] for r in STRUCTURED_SEED]
        for sid in ids:
            self.assertIn(sid, w.securities); self.assertTrue(w.market.history.get(sid))
        clo, cmbs, card = w.securities["CLO-2026-1A"], w.securities["CMBS-2026-C1-A4"], w.securities["CARD-2026-A1"]
        c = w.market.curve()
        self.assertTrue(clo.floating and card.floating and not cmbs.floating)
        self.assertAlmostEqual(clo.coupon, interp_rate(c, 0.25) + clo.float_spread, places=5, msg="a floater's coupon is the 3-month rate plus its spread")
        m_clo, m_cmbs = BondPricer.risk_metrics(clo, w.current_date, float(w.market.last_bar(clo.id).close), c), BondPricer.risk_metrics(cmbs, w.current_date, float(w.market.last_bar(cmbs.id).close), c)
        self.assertLess(m_clo["modified_duration"], 0.3); self.assertGreater(m_clo["spread_duration"], 4.0); self.assertGreater(m_cmbs["modified_duration"], 5.0)
        self.assertEqual(asset_key(clo), "CORP_BOND_IG"); self.assertEqual(asset_key(w.securities["CLO-2026-1E"]), "CORP_BOND_HY")
        # a rate shock: the fixed CMBS moves, the floater barely
        p_clo0, p_cmbs0 = w.market.last_bar(clo.id).close, w.market.last_bar(cmbs.id).close
        w.force_rate_shock(100.0) if hasattr(w, "force_rate_shock") else w.market.force_rate_shock(100.0)
        w.advance(1)
        p_clo1, p_cmbs1 = w.market.last_bar(clo.id).close, w.market.last_bar(cmbs.id).close
        self.assertLess(float(p_cmbs1 / p_cmbs0), 0.97, "a 100bp shock knocks a 7-year fixed tranche down several points")
        self.assertGreater(float(p_clo1 / p_clo0), 0.985, "a floater's price hardly moves with rates")
        self.assertGreater(clo.coupon, interp_rate(c, 0.25) + clo.float_spread + 0.005, "but its coupon reset higher")
        # buy the CLO and hold through a coupon
        w.place_order(pf.id, clo.id, "BUY", 2_000_000)
        w.advance(2)
        pos = pf.position(clo.id)
        self.assertEqual(pos.quantity, D(2_000_000))
        w.advance(70)
        self.assertTrue(any(e.type == "COUPON_PAID" and e.payload["security_id"] == clo.id for e in w.events), "a quarterly floating coupon was paid")
        self.assertGreater(pos.interest_income, 0)
        assert_ledger_invariants(self, w, pf)
        # spreads live: the tranche's spread drifts around its own norm, not the corporate rating's
        self.assertGreater(clo.spread_bps_credit, 90.0); self.assertLess(clo.spread_bps_credit, 260.0)
        self.assertIn(clo.id, w.market.macro.issuers); self.assertEqual(w.market.macro.issuers[clo.id].base_spread, 135.0)
        # replay
        w2 = World.load(store, w.id)
        self.assertEqual(w2.replay_errors, [])
        self.assertEqual(w2.securities[clo.id].coupon, clo.coupon); self.assertEqual(w2.market.last_bar(clo.id).close, w.market.last_bar(clo.id).close)
        self.assertEqual(w2.ledgers[pf.id].nav(), w.ledgers[pf.id].nav())
        self.assertEqual(w2.portfolio(pf.id).position(clo.id).interest_income, pos.interest_income)


if __name__ == "__main__":
    unittest.main()
