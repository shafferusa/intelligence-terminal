"""Shaffer Hedge: product-specific sizing units, eligibility, the optimiser, scenarios, the ML cap, and the ledger's
short sales, futures, options, forwards and atomic trade packages."""
import math
import os
import shutil
import tempfile
import unittest

from finsim2.engine.portfolio import Ledger, LedgerError, analytics
from finsim2.engine.research import Research
from finsim2.hedge import engine as E
from finsim2.hedge import history as H
from finsim2.hedge import pricing as px
from finsim2.hedge import products as P
from finsim2.hedge.market import Market
from finsim2.hedge.risk import RiskModel
from finsim2.hedge.series import margin_rate, price_series, unit_size
from test_finsim2 import build_store


def build_hedge_store(path):
    st = build_store(path, n=1800)
    spy = st.prices("SPY")
    st.upsert_prices("SPX", [dict(r, open=r["open"] * 10, high=r["high"] * 10, low=r["low"] * 10, close=r["close"] * 10,
                                  adj_close=r["adj_close"] * 10) for r in spy])
    days = [r["date"] for r in spy]
    # quarterly SPY dividends of 0.4% of the price
    st.upsert_actions("SPY", [(r["date"], "DIVIDEND", r["close"] * 0.004) for k, r in enumerate(spy) if k % 63 == 30])
    for sid, v in (("DGS1MO", 0.9), ("DGS1", 1.2), ("DGS5", 1.8), ("DGS7", 1.9), ("DGS20", 2.2), ("DGS30", 2.3), ("ECBDFR", 0.5)):
        st.upsert_macro(sid, [(d, v) for d in days])
    return st


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.store = build_hedge_store(os.path.join(cls.tmp, "r.db"))
        cls.r = Research(cls.store)
        cls.m = Market(cls.r)
        cls.rk = RiskModel(cls.m)
        cls.cal = cls.m.cal

    @classmethod
    def tearDownClass(cls):
        cls.store.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def priced(self, iid):
        return P.Priced(P.parse(iid, self.store), self.m, self.rk)


class SizingUnits(Base):
    def test_equity_hedge_uses_beta_dollars_not_market_value(self):
        pr = self.priced("QQQ")
        beta = self.rk.unit_exposures("QQQ")["exposures"]["MKT"]
        self.assertAlmostEqual(pr.exposures()["MKT"], pr.unit_value() * beta, places=6)
        self.assertNotAlmostEqual(beta, 1.0, places=2)          # a beta hedge of QQQ is not a market-value hedge

    def test_futures_use_notional_times_beta_and_margin_is_not_exposure(self):
        code = px.future_expiries("equity", self.m.asof, 1)[0][0]
        pr = self.priced(f"FUT:ES:{code}")
        F = pr.price
        self.assertAlmostEqual(pr.unit_notional(), F * 50, places=6)                 # contract notional = F × multiplier
        beta = self.rk.unit_exposures("SPX")["exposures"]["MKT"]
        self.assertAlmostEqual(pr.exposures()["MKT"], F * 50 * beta, places=4)
        self.assertEqual(pr.unit_value(), 0.0)                                       # a future costs nothing to open
        self.assertAlmostEqual(margin_rate(pr.inst), 0.06)                            # margin: collateral only
        self.assertNotIn("margin", pr.exposures())

    def test_option_hedge_is_delta_adjusted_notional_not_premium(self):
        S = self.m.price("SPY")
        exp = px.monthly_option_expiries(self.m.asof)[2]
        pr = self.priced(P.option_id("SPY", "P", px.strike_for(S, 0.95, 1.0), exp))
        g = pr.greeks_per_unit()
        self.assertLess(g["delta"], 0)
        self.assertAlmostEqual(g["delta_usd"], 1 * 100 * S * g["delta"], places=6)   # contracts × 100 × S × Δ
        beta = self.rk.unit_exposures("SPY")["exposures"]["MKT"]
        self.assertAlmostEqual(pr.exposures()["MKT"], g["delta_usd"] * beta, places=6)
        self.assertGreater(abs(pr.exposures()["MKT"]), 3 * g["premium"])             # exposure ≫ premium
        self.assertAlmostEqual(pr.unit_value(), g["premium"])                         # the premium is what it costs
        for k in ("gamma", "vega_usd", "theta_usd"):
            self.assertIsNotNone(g[k])

    def test_treasury_future_uses_dv01(self):
        code = px.future_expiries("treasury", self.m.asof, 1)[0][0]
        pr = self.priced(f"FUT:ZN:{code}")
        dv = pr.inputs["treasury"]["dv01"]
        self.assertGreater(dv, 30)
        self.assertLess(dv, 150)
        self.assertAlmostEqual(-sum(v for f, v in pr.exposures().items() if f.startswith("RATE:")), dv, places=6)
        # contracts for a $500/bp target
        self.assertAlmostEqual(px.solve_contracts(500.0, dv, integer=False), 500.0 / dv)

    def test_bond_etf_dv01_and_credit_cs01(self):
        tlt = self.rk.unit_exposures("TLT")["exposures"]
        d = self.store.asset("TLT")["duration"]
        self.assertAlmostEqual(-sum(v for f, v in tlt.items() if f.startswith("RATE:")), d * 1e-4, places=10)
        hyg = self.rk.unit_exposures("HYG")["exposures"]
        self.assertAlmostEqual(-hyg["CREDIT:HY"], self.store.asset("HYG")["duration"] * 1e-4, places=10)

    def test_fx_forward_uses_currency_notional(self):
        pr = self.priced("FWD:EURUSD:" + px.monthly_option_expiries(self.m.asof)[2])
        S = self.m.fx("EUR")
        self.assertAlmostEqual(pr.exposures()["FX:EUR"], S, places=8)                # one euro of forward = S dollars of EUR
        self.assertAlmostEqual(pr.unit_value(), 0.0)

    def test_commodity_structural_exposure(self):
        self.assertEqual(self.rk.unit_exposures("GOLD")["exposures"].get("CMD:GOLD"), 1.0)


class Eligibility(Base):
    def test_refusals_carry_reasons(self):
        exp = px.monthly_option_expiries(self.m.asof)[2]
        ok, why = self.priced(P.option_id("QQQ", "P", 100, exp)).eligibility()           # no VXN in this store
        self.assertFalse(ok)
        self.assertTrue(any("implied-volatility" in w for w in why))
        code = px.future_expiries("equity", self.m.asof, 1)[0][0]
        ok, why = self.priced(f"FUT:CL:{code}").eligibility()
        self.assertFalse(ok)
        self.assertTrue(any("continuous front-month" in w for w in why))
        ok, why = self.priced("SPX").eligibility()
        self.assertFalse(ok)
        self.assertTrue(any("not investable" in w for w in why))

    def test_registry_is_complete_and_honest(self):
        keys = {p["key"] for p in P.PRODUCT_TYPES}
        self.assertGreaterEqual(len(keys), 61)
        for p in P.PRODUCT_TYPES:
            self.assertIn(p["status"], ("SUPPORTED", "MODELLED", "PROXY", "PARTIAL", "ANALYSIS_ONLY", "NOT_SUPPORTED"))
            if p["status"] == "NOT_SUPPORTED":
                self.assertTrue(p["reason"], p["key"])


class InvalidInput(Base):
    def test_unknown_objective_is_refused(self):
        with self.assertRaises(ValueError):
            E.analyze(self.r, [{"id": "SPY", "quantity": 10}], "nonsense", {}, nav=1e5, history=False)

    def test_execute_refuses_non_positive_or_invalid_quantities(self):
        from finsim2.hedge import service as SV
        app = type("App", (), {"store": self.store, "research": self.r, "ledger": lambda s: None})()
        for q in (0, -5, float("nan")):
            with self.assertRaises(ValueError):
                SV.execute(app, {"asset_id": "SPY", "side": "BUY", "quantity": q}, [], None, "trade_only")


class Engine(Base):
    def test_beta_target_is_met_within_a_lot_and_long_short_symmetric(self):
        long_ = E.analyze(self.r, [{"id": "QQQ", "quantity": 3000}], "beta", {"reduction": 0.5}, nav=1e6, history=False)
        short = E.analyze(self.r, [{"id": "QQQ", "quantity": -3000}], "beta", {"reduction": 0.5}, nav=1e6, history=False)
        for res, sign in ((long_, 1), (short, -1)):
            f = next(x for x in res["factors"] if x["factor"] == "MKT")
            self.assertGreater(f["hedge_pct_raw"], 0.35)
            self.assertLess(f["hedge_pct_raw"], 0.65)
            legs = res["package"]["raw"]
            self.assertTrue(legs)
            # a long book is hedged by selling (or buying puts / inverse funds); a short book by buying
            first = legs[0]
            if first["type"] != "OPTION" and "SH" not in first["id"]:
                self.assertEqual(first["side"], "SELL" if sign > 0 else "BUY")

    def test_every_candidate_is_sized_in_its_own_unit(self):
        res = E.analyze(self.r, [{"id": "SPY", "quantity": 1000}], "beta", {"reduction": 1.0}, nav=1e6, history=False)
        for c in res["candidates"]:
            if c.get("status") == "ELIGIBLE":
                self.assertTrue(c["sizing_rule"])
                self.assertTrue(c["risk_unit"])

    def test_option_hedge_ratio_rises_as_the_market_falls(self):
        exp = px.monthly_option_expiries(self.m.asof)[2]
        S = self.m.price("SPY")
        iid = P.option_id("SPY", "P", px.strike_for(S, 0.95, 1.0), exp)
        res = E.analyze(self.r, [{"id": "SPY", "quantity": 1000}], "crash", {"reduction": 0.5, "candidates": [iid], "use": [iid]},
                        nav=1e6, history=False)
        leg = next((L for L in res["package"]["raw"] if L["id"] == iid), None)
        self.assertIsNotNone(leg)
        path = {round(p["market_move"], 2): p["hedge_ratio"] for p in leg["option"]["hedge_ratio_scenarios"]}
        self.assertLess(path[0.0], path[-0.1])
        self.assertLess(path[-0.1], path[-0.2])
        self.assertAlmostEqual(leg["option"]["delta_adjusted_notional"], leg["quantity"] * 100 * S * leg["option"]["delta"], places=4)
        # an option price is a model price at flat volatility and says so, on the leg and in the warnings
        self.assertEqual(leg["pricing_label"], P.FLAT_VOL_LABEL)
        self.assertTrue(any(P.FLAT_VOL_LABEL in w for w in res["warnings"]))
        cand = next(c for c in res["candidates"] if c["id"] == iid)
        self.assertEqual(cand["components"]["M"], E.FLAT_VOL_CONFIDENCE)            # flat-vol option ranked with a penalty
        self.assertGreater(cand["convexity"], 1.0)                                    # a put gains more than a same-delta linear hedge
        self.assertIn(cand["variance_quality"], ("HIGH", "MEDIUM", "LOW", "NONE", "NEGATIVE"))

    def test_new_stress_scenarios_and_correlation_convergence(self):
        res = E.analyze(self.r, [{"id": "SPY", "quantity": 1000}, {"id": "TLT", "quantity": 500}], "beta", {"reduction": 0.5}, nav=1e6, history=False)
        keys = {s["key"] for s in res["scenarios"]}
        for k in ("rates200", "flatten", "cmd", "eqvol", "crediteq", "usdrates"):
            self.assertIn(k, keys)
        b = res["risk"]["before"]
        self.assertGreaterEqual(b["sigma_daily_corr1"], b["sigma_daily"] - 1e-6)     # no diversification when everything moves together

    def test_scenarios_and_before_after(self):
        res = E.analyze(self.r, [{"id": "SPY", "quantity": 1000}, {"id": "TLT", "quantity": 2000}], "beta", {"reduction": 0.5}, nav=1e6,
                        history=False)
        keys = {s["key"] for s in res["scenarios"]}
        for k in ("up10", "up5", "flat", "dn5", "dn10", "dn20", "vol", "rates", "credit", "usd"):
            self.assertIn(k, keys)
        self.assertLess(abs(res["risk"]["raw"]["beta_usd"]), abs(res["risk"]["before"]["beta_usd"]))
        self.assertIn("var95", res["risk"]["before"])


class EffectivenessTerm(unittest.TestCase):
    def test_overshoot_is_penalised(self):
        from finsim2.hedge.engine import effectiveness_term as e
        self.assertAlmostEqual(e(1.0), 1.0)
        self.assertAlmostEqual(e(1.25), 1.25)
        self.assertLess(e(2.0), e(1.0))          # 2x the requested tail offset scores below an exact hedge
        self.assertEqual(e(6.2), -1.0)           # 3x over-hedged deep put: floored
        self.assertEqual(e(-3.0), -1.0)          # a hedge that added risk


class HedgeType(unittest.TestCase):
    def test_variance_and_tail_hedges_are_told_apart(self):
        h = lambda v, t: {"n": 100, "realized_reduction": v, "tail": {"reduction": t}}
        self.assertEqual(E.hedge_type(h(0.9, 0.95)), "variance + tail hedge")
        self.assertEqual(E.hedge_type(h(0.6, 0.1)), "variance hedge")
        self.assertEqual(E.hedge_type(h(-0.04, 1.08)), "tail hedge (adds variance)")      # an index put
        self.assertEqual(E.hedge_type(h(0.05, 0.1)), "weak hedge")
        self.assertIsNone(E.hedge_type({"n": 0}))
        self.assertEqual(E.JUDGED_ON["es"], "tail-loss")
        self.assertIn("crash", E.TAIL_OBJECTIVES)


class MLCap(unittest.TestCase):
    def test_unverified_model_changes_nothing_and_verified_is_capped(self):
        self.assertEqual(H.ml_adjustment(None, {})["adjustment"], 0.0)
        self.assertEqual(H.ml_adjustment({"verified": False, "reasons": ["x"]}, {})["adjustment"], 0.0)
        from finsim2.engine import models as M
        mod = M.make_model("ridge")
        mod.fit([[0.0], [1.0], [2.0], [3.0]], [5.0, 5.0, 5.0, 5.0])        # absurd prediction: log-ratio 5
        adj = H.ml_adjustment({"verified": True, "model": M.get_state(mod), "features": ["x"], "cap": H.ADJ_CAP, "alpha": H.ALPHA}, {"x": 1.0})
        self.assertAlmostEqual(adj["adjustment"], H.ADJ_CAP)
        self.assertAlmostEqual(adj["applied"], H.ALPHA * H.ADJ_CAP)
        self.assertLessEqual(adj["applied"], 0.15 + 1e-12)                  # never more than ±15% of the raw hedge


class LedgerDerivatives(Base):
    def setUp(self):
        for t in self.store.transactions("main"):
            self.store.delete_transaction(t["id"])
        self.led = Ledger(self.store, self.r.panel(), "main")

    def d(self, k):
        return self.cal[k]

    def test_short_sale_collateral_borrow_and_cover(self):
        led = self.led
        led.deposit(100000, self.d(300))
        with self.assertRaises(LedgerError):                               # 150% requirement: $100k supports < $200k short
            led.trade("SPY", 2000000 / self.r.panel().series("SPY", "close")[300], "SHORT", date=self.d(300))
        q = 100000 / self.r.panel().series("SPY", "close")[300]
        led.trade("SPY", q, "SHORT", date=self.d(300))
        h = led.holdings(self.d(400))
        self.assertLess(h["positions"]["SPY"]["quantity"], 0)
        self.assertGreater(h["financing"], 0)                               # borrow fee charged every session
        with self.assertRaises(LedgerError):
            led.trade("SPY", 1, "BUY", date=self.d(401))                     # cover first
        with self.assertRaises(LedgerError):
            led.trade("SPY", q * 2, "COVER", date=self.d(401))                # cannot cover more than is short
        led.trade("SPY", q, "COVER", date=self.d(401))
        h = led.holdings()
        self.assertAlmostEqual(h["positions"]["SPY"]["quantity"], 0.0)
        # dividends were debited while short
        self.assertLess(h["income"], 0)
        an = analytics(self.store, self.r.panel(), led)
        self.assertAlmostEqual(an["nav"], led.nav_history()["nav"][-1], places=4)

    def test_future_marked_to_model_margin_and_settlement(self):
        led = self.led
        led.deposit(100000, self.d(1000))
        code = next(c for c, ltd, _ in px.future_expiries("equity", self.d(1000), 6) if self.d(1150) < ltd < self.d(1400))
        iid = f"FUT:ES:{code}"
        inst = P.parse(iid, self.store)
        F0 = price_series(self.r.panel(), self.store, inst)[1000]
        led.trade(iid, 1, "SELL", date=self.d(1000))
        h0 = led.holdings(self.d(1000))
        self.assertAlmostEqual(h0["cash"], 100000.0)                         # no cash changes hands for a future
        self.assertAlmostEqual(h0["requirement"], 0.06 * F0 * 50, places=4)  # margin is collateral, not exposure
        hist = led.nav_history()
        k = self.cal.index(self.d(1100)) - self.cal.index(hist["dates"][0])
        F1 = price_series(self.r.panel(), self.store, inst)[1100]
        self.assertAlmostEqual(hist["nav"][k], 100000 - 50 * (F1 - F0), places=4)
        h = led.holdings()
        self.assertEqual(h["positions"][iid]["quantity"], 0.0)               # settled at expiry
        self.assertEqual(len(h["settled"]), 1)
        self.assertAlmostEqual(h["cash"], 100000 + h["settled"][0]["pnl"], places=4)

    def test_option_premium_expiry_and_no_writing(self):
        led = self.led
        led.deposit(50000, self.d(1000))
        S = self.r.panel().series("SPY", "close")[1000]
        exp = next(e for e in px.monthly_option_expiries(self.d(1000), 8) if self.d(1060) < e < self.d(1200))
        iid = P.option_id("SPY", "P", round(S), exp)
        inst = P.parse(iid, self.store)
        prem = price_series(self.r.panel(), self.store, inst)[1000]
        led.trade(iid, 2, "BUY", date=self.d(1000))
        self.assertAlmostEqual(led.holdings(self.d(1000))["cash"], 50000 - 2 * 100 * prem, places=4)
        with self.assertRaises(LedgerError):
            led.trade(iid, 3, "SELL", date=self.d(1010))                     # writing options is refused
        h = led.holdings()
        s = h["settled"][0]
        k = self.r.panel().index_of(exp)
        Sx = self.r.panel().series("SPY", "close")[k]
        self.assertAlmostEqual(s["price"], max(0.0, round(S) - Sx), places=6)
        self.assertAlmostEqual(h["cash"], 50000 - 2 * 100 * prem + 2 * 100 * s["price"], places=4)

    def test_package_is_atomic(self):
        led = self.led
        led.deposit(100000, self.d(1500))
        n0 = len(self.store.transactions("main"))
        with self.assertRaises((LedgerError, ValueError)):
            led.trade_package([{"asset_id": "QQQ", "quantity": 100, "side": "BUY", "date": self.d(1500)},
                               {"asset_id": "SPY", "quantity": 1e9, "side": "SHORT", "date": self.d(1500)}])
        self.assertEqual(len(self.store.transactions("main")), n0)            # nothing recorded
        ids = led.trade_package([{"asset_id": "QQQ", "quantity": 100, "side": "BUY", "date": self.d(1500)},
                                 {"asset_id": "SPY", "quantity": 50, "side": "SHORT", "date": self.d(1500)}])
        txs = [t for t in self.store.transactions("main") if t["id"] in ids]
        self.assertEqual(len(txs), 2)
        self.assertEqual(len({t["package_id"] for t in txs}), 1)
        an = analytics(self.store, self.r.panel(), led)
        self.assertAlmostEqual(an["nav"], led.nav_history()["nav"][-1], places=4)

    def test_forward_value(self):
        led = self.led
        led.deposit(100000, self.d(1000))
        vd = self.d(1100)
        iid = f"FWD:EURUSD:{vd}"
        inst = P.parse(iid, self.store)
        ser = price_series(self.r.panel(), self.store, inst)
        led.trade(iid, 500, "SELL", date=self.d(1000))           # 500 units of base currency (the synthetic pair trades near 100)
        hist = led.nav_history()
        k = self.cal.index(self.d(1050)) - self.cal.index(hist["dates"][0])
        self.assertAlmostEqual(hist["nav"][k], 100000 - 500 * (ser[1050] - ser[1000]), places=4)


if __name__ == "__main__":
    unittest.main()
