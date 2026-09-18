"""Currency loans: drawn at the currency's policy rate plus a spread, accrued daily, retranslated at spot, repaid or rolled; real policy rates pinned from FRED."""
import unittest
from datetime import date

try:
    from helpers import make_world, assert_ledger_invariants
except ImportError:
    from tests.helpers import make_world, assert_ledger_invariants

from finsim.engines.ccy_funding import TIER_SPREAD
from finsim.engines.realmacro import policy_rates_as_of
from finsim.money import D, ZERO, money
from finsim.world import World, CommandError


class CcyFundingTest(unittest.TestCase):
    def test_borrow_accrue_translate_repay_and_roll(self):
        w, pf, store = make_world(capital=50_000_000)
        rates = {r["currency"]: r for r in w.funding.rates()}
        self.assertAlmostEqual(rates["JPY"]["borrow_rate"], w.market.fx.rate["JPY"] + TIER_SPREAD["G10"], places=6)
        self.assertGreater(rates["TRY"]["borrow_rate"], rates["JPY"]["borrow_rate"] + 0.2)
        # an open yen loan
        loan = w.ccy_borrow(pf.id, "JPY", 1_000_000_000)
        self.assertEqual(pf.cash_account("JPY").balance, D(1_000_000_000)); self.assertIsNone(loan.maturity)
        base0 = loan.base_value
        self.assertAlmostEqual(float(base0), float(money(D(1_000_000_000) * w.fx.k("JPY"))), delta=1.0)
        assert_ledger_invariants(self, w, pf)
        nav0 = w.pnl.compute_summary(pf)["nav"]
        w.advance(5)
        self.assertGreater(loan.accrued, 0)
        expected = money(D(1_000_000_000) * D(repr(loan.rate)) * (date.fromisoformat(w.current_date.isoformat()) - date(2026, 1, 5)).days / 360)
        self.assertLess(abs(loan.accrued - expected) / expected, D("0.2"), "interest accrues at the loan's rate, ACT/360")
        self.assertGreater(pf.cash_account("JPY").balance, D(1_000_000_000), "the yen balance earns the yen deposit rate")
        s = w.pnl.compute_summary(pf)
        self.assertGreater(s["ccy_loans"], 0); assert_ledger_invariants(self, w, pf)
        # the yen strengthens 5%: the loan is dearer in dollars (unrealised FX), the yen cash worth more; the two nearly offset
        spot0 = w.market.fx.spot["JPY"]
        w.market.fx.spot["JPY"] = spot0 * 1.05
        w.funding.process_day(w.events[-1], w.current_date); w.fx.process_day(w.events[-1])
        self.assertEqual(loan.base_value, money(loan.principal * w.fx.k("JPY")), "the loan is carried at today's spot")
        self.assertGreater(loan.base_value, money(loan.principal * D(repr(spot0))) * D("1.04"))
        w.market.fx.spot["JPY"] = spot0
        w.advance(1)
        assert_ledger_invariants(self, w, pf)
        # over the funding line
        with self.assertRaises(CommandError):
            w.ccy_borrow(pf.id, "EUR", 200_000_000)
        # a 30-day euro term loan repays itself at maturity from the euro account
        eur = w.ccy_borrow(pf.id, "EUR", 5_000_000, 30)
        self.assertIsNotNone(eur.maturity); rate_fixed = eur.rate
        w.fx_spot(pf.id, "EUR", "USD", 100_000, "BUY")           # the interest: the loan costs more than the deposit earns
        w.advance(33)
        self.assertEqual(eur.status, "REPAID"); self.assertGreater(eur.interest_paid, 0); self.assertEqual(eur.rate, rate_fixed)
        self.assertLess(pf.cash_account("EUR").balance, D(150_000), "principal and interest came out of the euro account, leaving the top-up less net interest")
        assert_ledger_invariants(self, w, pf)
        # repay the yen loan in full (topping up the yen for the interest): realised FX on the difference between carrying and paid
        w.fx_spot(pf.id, "JPY", "USD", 5_000_000, "BUY")
        w.advance(3)
        w.ccy_repay(pf.id, loan.id)
        self.assertEqual(loan.status, "REPAID"); self.assertEqual(loan.principal, ZERO)
        self.assertEqual(w.ledgers[pf.id].balance("2750"), ZERO); self.assertEqual(w.ledgers[pf.id].balance("2755"), ZERO)
        assert_ledger_invariants(self, w, pf)
        # a loan that cannot be repaid at maturity rolls
        try_ = w.ccy_borrow(pf.id, "TRY", 10_000_000, 5)
        w.fx_spot(pf.id, "USD", "TRY", 9_000_000, "SELL")        # spend most of the lira
        w.advance(8)
        self.assertEqual(try_.status, "OPEN"); self.assertGreaterEqual(try_.rolls, 1)
        assert_ledger_invariants(self, w, pf)
        w2 = World.load(store, w.id)
        self.assertEqual(w2.replay_errors, [])
        p2 = w2.portfolio(pf.id)
        self.assertEqual(p2.ccy_loans[try_.id].principal, try_.principal); self.assertEqual(p2.ccy_loans[loan.id].status, "REPAID")
        self.assertEqual(w2.ledgers[pf.id].nav(), w.ledgers[pf.id].nav())

    def test_real_policy_rates_pin_the_fx_model(self):
        series = {"fed_upper": {"2026-01-02": 4.5, "2026-01-29": 4.25}, "policy_EUR": {"2025-12-18": 2.0, "2026-01-30": 1.75}, "policy_JPY": {"2025-12-01": 0.75},
                  "policy_TRY": {"2025-12-01": 38.0}}
        r = policy_rates_as_of(series, date(2026, 1, 15))
        self.assertEqual(r["USD"]["rate"], 0.045); self.assertEqual(r["EUR"]["rate"], 0.02); self.assertEqual(r["JPY"]["rate"], 0.0075); self.assertEqual(r["TRY"]["rate"], 0.38)
        self.assertEqual(r["HKD"]["rate"], 0.05); self.assertIn("peg", r["HKD"]["source"])
        r2 = policy_rates_as_of(series, date(2026, 2, 5))
        self.assertEqual(r2["EUR"]["rate"], 0.0175); self.assertEqual(r2["USD"]["rate"], 0.0425)
        w, pf, _ = make_world()
        w.market.real_macro = series
        w.market._pin_fx_rates(date(2026, 1, 15))
        self.assertEqual(w.market.fx.rate["EUR"], 0.02); self.assertEqual(w.market.fx.rate["TRY"], 0.38); self.assertEqual(w.market.fx.rate_source["JPY"], "FRED IRSTCI01JPM156N")
        self.assertAlmostEqual(w.funding.borrow_rate("JPY"), 0.0075 + TIER_SPREAD["G10"], places=6)
        self.assertAlmostEqual(w.market.curve_for_ccy("EUR").policy_rate, 0.02, places=5, msg="the local curve reads the pinned rate")


if __name__ == "__main__":
    unittest.main()
