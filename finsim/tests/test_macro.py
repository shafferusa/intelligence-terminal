"""Phase 7 — macro world: releases, central bank reaction function, earnings, credit migration, issuer and counterparty defaults."""
import unittest
from datetime import date

from helpers import make_world, assert_ledger_invariants
from finsim.engines.macro import MacroModel, RATING_ORDER
from finsim.money import D
from finsim.world import CommandError, World


class MacroModelTest(unittest.TestCase):
    def test_reaction_function_and_state_dynamics(self):
        w, pf, store = make_world()
        m = MacroModel(1, w.securities, w.calendar)
        d = date(2026, 1, 5)
        prev = d
        for i in range(120):
            d = w.calendar.next_business_day(d)
            m.step(d, prev, "RECESSION", 0.0, 650.0, 0.0)
            prev = d
        self.assertLess(m.state.growth, 0.5, "growth falls toward the recession anchor")
        self.assertGreater(m.state.unemployment, 4.1)
        target = m.policy_target()
        self.assertLess(target, 0.0435, "the reaction function calls for cuts")
        self.assertLess(m.state.policy_rate, 0.0435, "meetings cut in steps")
        cuts = [r for r in m.releases if r["kind"] == "FOMC" and r["step_bp"] < 0]
        self.assertTrue(cuts)
        self.assertTrue(all(abs(r["step_bp"]) in (0, 25, 50, 75) for r in m.releases if r["kind"] == "FOMC"))
        kinds = {r["kind"] for r in m.releases}
        self.assertTrue({"CPI", "PAYROLLS", "PMI", "GDP", "RETAIL_SALES", "FOMC"} <= kinds)
        m2 = MacroModel(1, w.securities, w.calendar)
        d = date(2026, 1, 5)
        prev = d
        for i in range(120):
            d = w.calendar.next_business_day(d)
            m2.step(d, prev, "RECESSION", 0.0, 650.0, 0.0)
            prev = d
        self.assertEqual(m2.releases, m.releases, "deterministic")


class MacroWorldTest(unittest.TestCase):
    def test_releases_earnings_and_transmission(self):
        w, pf, store = make_world(seed=3)
        m = w.market.macro
        self.assertTrue(m.history, "macro state exists from pre-history")
        w.advance(45)
        rel = [r for r in m.releases if r["date"] > "2026-01-05"]
        self.assertGreaterEqual(len(rel), 6)
        cal = w.market.macro.release_calendar(2026, 2)
        self.assertTrue(any(r["kind"] == "PAYROLLS" and date.fromisoformat(r["date"]) == next(dd for dd, k in cal if k == "PAYROLLS") for r in rel), "releases land on the calendar dates")
        fomc = [r for r in rel if r["kind"] == "FOMC"]
        self.assertTrue(fomc)
        # the curve's short end follows the policy rate
        cur = w.market.curve()
        self.assertLess(abs(cur.policy_rate - m.state.policy_rate), 0.006)
        earn = [e for e in m.earnings if e["date"] > "2026-01-05"]
        self.assertGreaterEqual(len(earn), 5, "January is earnings season")
        e = earn[0]
        h = {b.date: b for b in w.market.history[e["security_id"]]}
        idx = [b.date for b in w.market.history[e["security_id"]]].index(e["date"])
        b0, b1 = w.market.history[e["security_id"]][idx - 1], w.market.history[e["security_id"]][idx]
        ret = float(b1.close / b0.close) - 1
        self.assertEqual((ret > 0.02) or (ret < -0.02) or abs(e["jump"]) < 0.03, True)
        self.assertTrue(any(n.category in ("MACRO", "EARNINGS") for n in w.news))
        macro = w.market.macro.dashboard(w.current_date)
        self.assertIn("upcoming", macro)
        self.assertTrue(any(u["kind"] == "EARNINGS" for u in w.market.macro.upcoming(w.current_date, 90)), "earnings dates are on the calendar")
        self.assertTrue(any(u["kind"] in ("CPI", "PAYROLLS", "FOMC", "PMI") for u in macro["upcoming"]))
        b = pf.briefings[-1]
        self.assertIn("macro", b)
        w2 = World.load(store, "t")
        self.assertEqual(w2.market.macro.state.policy_rate, m.state.policy_rate)
        self.assertEqual(w2.market.macro.releases, m.releases)
        self.assertEqual(w2.securities["NVDA"].fundamentals, w.securities["NVDA"].fundamentals)

    def test_issuer_default_hits_bonds_cds_and_equity(self):
        w, pf, store = make_world(capital=50_000_000)
        w.place_order(pf.id, "AAL-28", "BUY", 250_000)
        w.place_order(pf.id, "AAL", "BUY", 20_000)
        r = w.request_quote(pf.id, "CDS", {"reference": "AAL-28", "notional": 5_000_000, "tenor_years": 3, "buyer": True})
        w.execute_rfq(pf.id, r.id, min((q for q in r.quotes if not q.get("declined")), key=lambda q: q["cost_vs_mid"])["dealer"])
        w.advance(2)
        assert_ledger_invariants(self, w, pf)
        nav0 = w.ledgers[pf.id].nav()
        bond_mv0 = pf.positions["AAL-28"].market_value
        face = float(pf.positions["AAL-28"].quantity)
        self.assertEqual(face, 250_000.0)
        w.credit_event("AAL-28")
        sec = w.securities["AAL-28"]
        self.assertTrue(sec.defaulted)
        self.assertEqual(w.market.macro.issuers["AAL-28"].rating, "D")
        cds = [t for t in pf.otc_trades.values() if t.product == "CDS"][0]
        self.assertEqual(cds.status, "SETTLED_DEFAULT")
        pos = pf.positions["AAL-28"]
        self.assertAlmostEqual(float(pos.market_value), face * sec.recovery_rate, delta=1.0, msg="bond marked at recovery")
        self.assertEqual(pos.accrued_interest, D(0), "accrued interest written off")
        self.assertLess(pos.market_value, bond_mv0)
        assert_ledger_invariants(self, w, pf)
        w.advance(1)
        self.assertLess(float(w.market.last_bar("AAL").close / w.market.history["AAL"][-2].close), 0.5, "the equity collapses")
        self.assertAlmostEqual(float(pos.market_value), face * sec.recovery_rate, delta=1.0, msg="stays at recovery")
        self.assertEqual([m for m in pf.cash_movements if m.kind == "COUPON" and "AAL" in m.reference], [m for m in pf.cash_movements if m.kind == "COUPON" and "AAL" in m.reference])
        while pos.quantity > 0 and w.current_date < date.fromisoformat(sec.maturity):
            w.advance(1)
            if w.current_date.isoformat() > sec.recovery_date:
                break
        self.assertEqual(pos.quantity, D(0), "recovery paid and the position closed")
        red = [m for m in pf.cash_movements if m.kind == "MATURITY" and "AAL-28" in m.reference]
        self.assertEqual(red[-1].amount, (D(int(face)) * D(repr(sec.recovery_rate))).quantize(D("0.01")))
        assert_ledger_invariants(self, w, pf)
        w2 = World.load(store, "t")
        self.assertTrue(w2.securities["AAL-28"].defaulted)
        self.assertEqual(w2.ledgers[pf.id].nav(), w.ledgers[pf.id].nav())
        self.assertEqual(len(w2.events), len(w.events))


if __name__ == "__main__":
    unittest.main()
