"""Global government bonds: local curves, Bunds / Gilts / JGBs settling in their own currency, base-currency marks and P&L,
Bund futures margined in euros, FX moving the book, replay."""
import unittest
from datetime import date

try:
    from helpers import make_world, assert_ledger_invariants
except ImportError:
    from tests.helpers import make_world, assert_ledger_invariants

from finsim.engines.global_rates import SOVEREIGN_SPECS, foreign_curve
from finsim.engines.pricing import BondPricer, interp_rate
from finsim.money import D, ZERO, money
from finsim.world import World


class GlobalRatesTest(unittest.TestCase):
    def test_local_curves_and_sovereign_bonds(self):
        w, pf, _ = make_world()
        usd = w.market.curve()
        eur, gbp, jpy = (w.market.curve_for_ccy(c) for c in ("EUR", "GBP", "JPY"))
        self.assertLess(jpy.rates[7], eur.rates[7]); self.assertLess(eur.rates[7], usd.rates[7], "yen below euro below dollar at ten years")
        self.assertAlmostEqual(eur.policy_rate, w.market.fx.rate["EUR"], places=4)
        it = w.market.curve_for_ccy("EUR", country="IT"); de = w.market.curve_for_ccy("EUR", country="DE")
        self.assertGreater(it.rates[7] - de.rates[7], 0.008, "BTPs trade wide of Bunds")
        for (sid, name, country, ccy, yrs, freq, adv, mkt, nick) in SOVEREIGN_SPECS:
            sec = w.securities[sid]
            self.assertEqual(sec.currency, ccy); self.assertTrue(w.market.history.get(sid))
            px = float(w.market.last_bar(sid).close)
            self.assertGreater(px, 60.0); self.assertLess(px, 140.0)
            m = BondPricer.risk_metrics(sec, w.current_date, px, w.market.curve_for_security(sec))
            self.assertLess(abs(m["spread_to_curve_bps"]), 25.0, f"{sid} prices on its own curve")
        bund, gilt, jgb = w.securities["DE-10Y"], w.securities["GB-10Y"], w.securities["JP-10Y"]
        self.assertLess(jgb.coupon, bund.coupon); self.assertGreater(w.securities["IT-10Y"].coupon, bund.coupon)
        self.assertEqual(bund.market, "EU_GOVT"); self.assertEqual(w.settlement_config.cycle_for("EU_GOVT"), 2)

    def test_a_bund_settles_in_euros_and_marks_in_dollars(self):
        w, pf, store = make_world(capital=50_000_000)
        bund = w.securities["DE-10Y"]
        k0 = w.fx.k("EUR")
        # pay for it with dollars: the FX spot is dealt alongside (as the ticket does)
        px = w.market.last_bar(bund.id).ask
        need = money(D(10_000_000) * px / 100 * D("1.03"))          # price, four months of accrued and a cushion
        w.fx_spot(pf.id, "EUR", "USD", need, "BUY")
        w.place_order(pf.id, bund.id, "BUY", 10_000_000)
        w.advance(1)
        t = [x for x in pf.trades.values() if x.security_id == bund.id][0]
        self.assertEqual(t.currency, "EUR"); self.assertGreater(D(str(t.execution_detail["fx_rate"])), D("0.5")); self.assertLess(D(str(t.execution_detail["fx_rate"])), D("2"))
        pos = pf.position(bund.id)
        self.assertAlmostEqual(float(pos.cost_basis), float(t.gross_amount * D(str(t.execution_detail["fx_rate"]))), delta=1.0, msg="cost basis in dollars")
        w.advance(3)
        self.assertEqual(pos.settled_quantity, D(10_000_000), "settled T+2 in euros")
        self.assertLess(pf.cash_account("EUR").balance, need * D("0.05"), "the euros bought paid for it")
        mv_local = money(pos.quantity * w.market.last_bar(bund.id).close / 100)
        self.assertAlmostEqual(float(pos.market_value), float(money(mv_local * w.fx.k("EUR"))), delta=1.0, msg="marked at the local price times spot")
        self.assertGreater(pos.accrued_interest, 0)
        assert_ledger_invariants(self, w, pf)
        s = w.pnl.compute_summary(pf)
        self.assertAlmostEqual(float(s["nav"]), float(w.ledgers[pf.id].nav()), places=2)
        # the euro moves: the dollar value of the position moves with it
        nav0 = s["nav"]; spot0 = w.market.fx.spot["EUR"]; mv0 = pos.market_value
        w.market.fx.spot["EUR"] = spot0 * 1.05
        w.pnl.mark_all(w.events[-1]); w.fx.process_day(w.events[-1])
        self.assertGreater(pf.position(bund.id).market_value, mv0 * D("1.04"))
        w.market.fx.spot["EUR"] = spot0
        w.advance(1)
        assert_ledger_invariants(self, w, pf)
        # sell half back: proceeds in euros, realised P&L in dollars
        w.place_order(pf.id, bund.id, "SELL", 5_000_000)
        w.advance(3)
        self.assertEqual(pf.position(bund.id).quantity, D(5_000_000))
        self.assertGreater(pf.cash_account("EUR").balance, D(4_000_000))
        assert_ledger_invariants(self, w, pf)
        # replay
        w2 = World.load(store, w.id)
        self.assertEqual(w2.replay_errors, [])
        p2 = w2.portfolio(pf.id).position(bund.id)
        self.assertEqual((p2.quantity, p2.cost_basis, p2.realized_pnl, p2.market_value), (pf.position(bund.id).quantity, pf.position(bund.id).cost_basis, pf.position(bund.id).realized_pnl, pf.position(bund.id).market_value))
        self.assertEqual(w2.ledgers[pf.id].nav(), w.ledgers[pf.id].nav())
        self.assertEqual(w2.portfolio(pf.id).cash_account("EUR").base_value, pf.cash_account("EUR").base_value)

    def test_bund_future_margins_in_dollars_and_settles_in_euros(self):
        w, pf, store = make_world(capital=50_000_000)
        fut = w.market.front_contract("FGBL")
        self.assertIsNotNone(fut); self.assertEqual(fut.currency, "EUR")
        px = float(w.market.last_bar(fut.id).close)
        bund_px = float(w.market.last_bar("DE-10Y").close)
        self.assertLess(abs(px - bund_px) / bund_px, 0.03, "the Bund future rides the 10-year Bund with local carry")
        im = w.futures.initial_margin_per_contract(fut)
        self.assertGreater(im, D(1500)); self.assertLess(im, D(6000))
        w.place_order(pf.id, fut.id, "BUY", 50)
        w.advance(3)
        pos = pf.position(fut.id)
        self.assertEqual(pos.quantity, D(50))
        vms = [e for e in w.events if e.type == "FUTURES_SETTLED" and e.payload["security_id"] == fut.id]
        self.assertTrue(vms); self.assertEqual(vms[0].payload["currency"], "EUR")
        moved = [m for m in pf.cash_movements if m.kind == "VARIATION_MARGIN" and m.currency == "EUR"]
        self.assertTrue(moved, "variation margin settles in euros")
        self.assertGreater(pos.initial_margin, ZERO)
        assert_ledger_invariants(self, w, pf)
        gilt = w.market.front_contract("GLT"); jgb = w.market.front_contract("JGB")
        self.assertEqual((gilt.currency, jgb.currency), ("GBP", "JPY")); self.assertEqual(jgb.multiplier, 1_000_000)
        w2 = World.load(store, w.id)
        self.assertEqual(w2.replay_errors, []); self.assertEqual(w2.ledgers[pf.id].nav(), w.ledgers[pf.id].nav())


if __name__ == "__main__":
    unittest.main()
