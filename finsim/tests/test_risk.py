"""Phase 5 — risk: exposures, historical VaR / ES, stress tests, component VaR, liquidity ladders, limits and hard-limit blocking."""
import unittest

from helpers import make_world, assert_ledger_invariants
from finsim.engines.risk import STRESS_SCENARIOS
from finsim.money import D
from finsim.world import CommandError, World


class ExposureAndVaRTest(unittest.TestCase):
    def test_equity_var_is_linear_and_hedging_reduces_it(self):
        w, pf, store = make_world(capital=50_000_000)
        # a diversified holding: its risk is mostly the market factor, so an index hedge should remove most of it
        # (a single name like NVDA keeps most of its variance as idiosyncratic risk no index hedge can touch)
        w.place_order(pf.id, "SPY", "BUY", 10_000)
        w.advance(1)
        rep = w.risk.report(pf)
        mv = float(pf.positions["SPY"].market_value)
        self.assertAlmostEqual(rep["factors"]["equity_dollar"], mv, places=2)
        self.assertAlmostEqual(rep["factors"]["beta_dollar"], mv * w.securities["SPY"].beta, places=2)
        v = rep["var"]
        self.assertTrue(v["available"])
        self.assertEqual(v["days"], 250)
        self.assertGreater(v["var99"], v["var95"])
        self.assertGreater(v["var95"], 0)
        self.assertGreater(v["es975"], v["var95"])
        self.assertAlmostEqual(sum(v["component_var99"].values()), v["var99"], delta=v["var99"] * 0.02, msg="Euler components add up")
        w.place_order(pf.id, "SPY", "BUY", 10_000)
        w.advance(1)
        v2 = w.risk.var(pf)
        ratio = v2["var99"] / v["var99"]
        self.assertGreater(ratio, 1.7)
        self.assertLess(ratio, 2.4, "twice the position, about twice the VaR (marks moved a day)")
        # hedge the beta with short index futures: VaR falls
        es = w.market.front_contract("ES")
        beta_dollar = w.risk.factor_summary(pf)["beta_dollar"]
        px = float(w.market.last_bar(es.id).close) * es.multiplier
        n = max(1, int(beta_dollar / px))
        w.place_order(pf.id, es.id, "SELL", n)
        w.advance(1)
        v3 = w.risk.var(pf)
        self.assertLess(v3["var99"], v2["var99"] * 0.8, "index hedge removes most of the market risk")
        self.assertLess(abs(w.risk.factor_summary(pf)["beta_dollar"]), abs(beta_dollar) * 0.3)
        assert_ledger_invariants(self, w, pf)

    def test_stress_matches_sensitivities(self):
        w, pf, store = make_world(capital=100_000_000)
        w.place_order(pf.id, "NVDA", "BUY", 100_000)
        w.place_order(pf.id, "JPM", "BUY", 50_000)
        w.place_order(pf.id, "UST-10Y", "BUY", 10_000_000)
        w.place_order(pf.id, "F-32", "BUY", 5_000_000)
        w.advance(1)
        rep = w.risk.report(pf)
        f = rep["factors"]
        st = rep["stress"]
        eq_only = w.risk.stress(pf, custom={"equity": -0.10})["CUSTOM"]["pnl"]
        self.assertAlmostEqual(eq_only, -0.10 * f["beta_dollar"], delta=1.0, msg="pure equity shock = beta dollars x shock")
        rates_only = w.risk.stress(pf, custom={"rates_bp": 100})["CUSTOM"]["pnl"]
        self.assertAlmostEqual(rates_only, f["dv01"] * 100, delta=1.0)
        self.assertLess(f["dv01"], 0, "long bonds lose when rates rise")
        self.assertLess(f["cs01"], 0)
        spread_only = w.risk.stress(pf, custom={"spreads_bp": 200})["CUSTOM"]["pnl"]
        self.assertAlmostEqual(spread_only, f["cs01"] * 100, delta=1.0, msg="IG name moves half the HY index shock")
        self.assertLess(st["GFC_2008"]["pnl"], st["RATES_DOWN_100"]["pnl"])
        self.assertEqual(set(st), set(STRESS_SCENARIOS))
        worst = st["EQUITY_CRASH_20"]["worst_contributors"][0]
        self.assertEqual(worst[0], "NVDA")

    def test_option_gamma_and_vega_in_stress(self):
        w, pf, store = make_world(capital=20_000_000)
        ch = w.options.chain("NVDA", w.options.chain("NVDA")["expiries"][1])
        atm = min(ch["rows"], key=lambda r: abs(r["strike"] - ch["level"]))
        w.place_order(pf.id, atm["C"]["id"], "BUY", 50)
        w.place_order(pf.id, atm["P"]["id"], "BUY", 50)      # long straddle: little delta, positive gamma and vega
        w.advance(1)
        rep = w.risk.report(pf)
        f = rep["factors"]
        self.assertGreater(f["gamma_usd"], 0)
        self.assertGreater(f["vega"], 0)
        self.assertLess(abs(f["equity_dollar"]), 0.4 * 100 * 100 * float(w.market.last_bar("NVDA").close))
        vol_up = w.risk.stress(pf, custom={"vol_pts": 10})["CUSTOM"]["pnl"]
        self.assertAlmostEqual(vol_up, f["vega"] * 10, delta=1.0)
        crash = w.risk.stress(pf, custom={"equity": -0.2})["CUSTOM"]["pnl"]
        rally = w.risk.stress(pf, custom={"equity": 0.2})["CUSTOM"]["pnl"]
        self.assertGreater(crash + rally, 0, "a straddle is long gamma: convex in both directions")
        self.assertGreater(rep["var"]["var99"], 0)

    def test_otc_sensitivities_flow_into_risk(self):
        w, pf, store = make_world(capital=50_000_000)
        r = w.request_quote(pf.id, "IRS", {"notional": 50_000_000, "tenor_years": 5, "pay_fixed": True})
        w.execute_rfq(pf.id, r.id, min((q for q in r.quotes if not q.get("declined")), key=lambda q: q["cost_vs_mid"])["dealer"])
        w.advance(1)
        f = w.risk.factor_summary(pf)
        self.assertGreater(f["dv01"], 0, "pay fixed gains when rates rise")
        rep = w.risk.report(pf)
        self.assertGreater(rep["stress"]["RATES_UP_100"]["pnl"], 0)
        self.assertGreater(rep["counterparty"]["total_current_exposure"] + rep["counterparty"]["total_pfe"], 0)
        self.assertTrue(any(l["name"].startswith("Largest counterparty") for l in rep["limits"]))


class LiquidityAndLimitsTest(unittest.TestCase):
    def test_liquidity_ladder(self):
        w, pf, store = make_world(capital=100_000_000)
        w.place_order(pf.id, "RIVN", "BUY", 300_000, time_in_force="GTC")
        w.place_order(pf.id, "UST-2Y", "BUY", 20_000_000)
        w.advance(1)
        liq = w.risk.liquidity(pf)
        row = next(r for r in liq["positions"] if r["id"] == "RIVN")
        sec = w.securities["RIVN"]
        self.assertAlmostEqual(row["days_to_liquidate"], row["quantity"] / (sec.adv * w.market.regime().depth_mult * 0.2), places=6)
        self.assertEqual(liq["buckets"]["1d"] + liq["buckets"]["5d"] + liq["buckets"]["20d"] + liq["buckets"]["over"],
                         sum(r["market_value"] for r in liq["positions"]))
        ladder = liq["cash_ladder"]
        self.assertEqual(len(ladder), 20)
        settle = [f for day in ladder for f in day["flows"] if f["kind"] == "SETTLEMENT"]
        self.assertTrue(settle and all(f["amount"] < 0 for f in settle), "tomorrow's purchase settlements are cash out")
        self.assertLess(ladder[0]["projected_cash"], float(pf.cash_account("USD").balance))

    def test_hard_var_limit_blocks_risk_increasing_orders(self):
        w, pf0, store = make_world(capital=1_000_000)
        pf = w.create_portfolio("PM", "PERSONAL", D(100_000_000), job="PORTFOLIO_MANAGER")
        px = w.market.last_bar("NVDA").ask
        w.place_order(pf.id, "NVDA", "BUY", int(D(80_000_000) / px))
        w.advance(1)
        snap = pf.risk_history[-1]
        var_limit = next(l for l in snap["limits"] if l["name"].startswith("VaR 99%"))
        self.assertEqual(var_limit["status"], "HARD", f"80% of NAV in a high-beta stock breaches the PM's 3.5% VaR limit ({var_limit['value']:.2%})")
        self.assertTrue(any(b["kind"] == "VAR" and b.get("severity") == "HARD" for b in pf.breaches))
        with self.assertRaises(CommandError) as cm:
            w.place_order(pf.id, "JPM", "BUY", 1000)
        self.assertIn("hard risk limit", str(cm.exception))
        with self.assertRaises(CommandError):
            w.request_quote(pf.id, "IRS", {"notional": 1_000_000})
        w.place_order(pf.id, "NVDA", "SELL", 1000)     # reducing risk is allowed
        att = " ".join(a["text"] for a in pf.briefings[-1]["attention"])
        self.assertIn("VaR", att)
        self.assertIn("risk", pf.briefings[-1])
        self.assertEqual(pf.briefings[-1]["risk"]["var99"], snap["var99"])
        w.advance(1)
        assert_ledger_invariants(self, w, pf)

    def test_risk_snapshots_replay(self):
        w, pf, store = make_world(capital=20_000_000)
        w.place_order(pf.id, "SPY", "BUY", 5_000)
        w.advance(3)
        self.assertEqual(len(pf.risk_history), 4)
        w2 = World.load(store, "t")
        self.assertEqual(w2.portfolios[pf.id].risk_history, pf.risk_history)
        self.assertEqual(w2.risk.var(w2.portfolios[pf.id])["var99"], w.risk.var(pf)["var99"])


if __name__ == "__main__":
    unittest.main()
