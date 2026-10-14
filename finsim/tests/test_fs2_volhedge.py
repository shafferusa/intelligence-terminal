"""Breadth-volatility → Shaffer Hedge study: production is untouched by the replay hook, every decision on a date is
point in time (changing later prices, volatility and breadth changes nothing on or before it), the covariance swap keeps
correlations, and the paired utility / bootstrap / gate logic is right."""
import math
import os
import random
import shutil
import tempfile
import unittest

from finsim2.engine.research import Research
from finsim2.hedge import engine as E
from finsim2.hedge import volhedge as V
from finsim2.hedge.market import Market
from finsim2.hedge.risk import RiskModel
from test_fs2_hedge import build_hedge_store

FEATS = ["pct_above_50d", "net_new_highs", "dispersion_21d", "ew_minus_spy_63d", "breadth_thrust_10d"]
MODELS = {"2016-01-01": {"b0": [-0.4, 0.5, 0.3, 0.1], "b1": [-0.4, 0.5, 0.3, 0.1, 0.05, -0.04, 0.03, 0.02, -0.01],
                         "std": [(0.5, 0.2), (0.0, 0.1), (0.02, 0.01), (0.0, 0.05), (0.0, 0.3)], "k0": 1.1, "k1": 1.05,
                         "n": 5000, "n_spy": 100, "to": "9999-12-31", "features": FEATS}}
BOOKS = [{"key": "Q", "type": "broad equity ETF", "w": {"QQQ": 1.0}},
         {"key": "MIX", "type": "mixed portfolio", "w": {"QQQ": 0.5, "TLT": 0.3, "IWM": 0.2}}]
OBJ = ["min_variance", "target_vol", "beta", "es", "crash"]


def _store(path, shock_from=None):
    st = build_hedge_store(path)
    st.upsert_asset({"id": "SPY", "name": "SPY", "asset_class": "ETF", "sector": "Broad Market", "currency": "USD"})
    rnd = random.Random(5)
    for k in range(22):                             # equities for the breadth features (it needs ≥ 20)
        a = f"EQ{k}"
        spy = st.prices("SPY")
        p, rows = 50.0, []
        for r in spy:
            p *= math.exp(0.0002 + 0.012 * rnd.gauss(0, 1))
            rows.append({"date": r["date"], "open": p, "high": p * 1.01, "low": p * 0.99, "close": p, "adj_close": p, "volume": 1e6})
        st.upsert_prices(a, rows)
        st.upsert_asset({"id": a, "name": a, "asset_class": "EQUITY", "sector": "Information Technology", "currency": "USD"})
    if shock_from:                                  # a different future: prices, volatility and rates after the date
        rnd2 = random.Random(99)
        for a in ["SPY", "QQQ", "TLT", "IWM", "SPX"] + [f"EQ{k}" for k in range(22)]:
            rows = [dict(r) for r in st.prices(a) if r["date"] > shock_from]
            for r in rows:
                f = 1.3 * math.exp(0.05 * rnd2.gauss(0, 1))
                for c in ("open", "high", "low", "close", "adj_close"):
                    r[c] = r[c] * f
            st.upsert_prices(a, rows)
        days = [r["date"] for r in st.prices("SPY") if r["date"] > shock_from]
        st.upsert_macro("VIXCLS", [(d, 60.0) for d in days])
        st.upsert_macro("DGS10", [(d, 7.0) for d in days])
    return st


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.st = _store(os.path.join(cls.tmp, "a.db"))
        cls.r = Research(cls.st)
        cls.cal = cls.r.panel().calendar()

    @classmethod
    def tearDownClass(cls):
        cls.st.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)


class ProductionUnchanged(Base):
    def test_replay_at_today_equals_production_raw_package(self):
        pos = [{"id": "QQQ", "quantity": 1000}, {"id": "TLT", "quantity": 500}]
        m = Market(self.r)
        rk = RiskModel(m)
        for obj in ("beta", "min_variance", "es", "duration"):
            prod = E.analyze(self.r, pos, obj, {"horizon": "1W"}, history=False)
            rep = E.analyze(self.r, pos, obj, {"horizon": "1W"}, replay={"market": m, "rk": rk})
            self.assertEqual([(L["id"], L["quantity"]) for L in prod["package"]["raw"]],
                             [(L["id"], L["quantity"]) for L in rep["package"]], obj)
            same = E.analyze(self.r, pos, obj, {"horizon": "1W"},
                             replay={"market": m, "rk": rk, "cov_adjust": lambda C: V.scale_market(C, C.get(("MKT", "MKT")))})
            self.assertEqual([(L["id"], L["quantity"]) for L in same["package"]], [(L["id"], L["quantity"]) for L in rep["package"]])

    def test_default_call_has_no_replay_path(self):
        out = E.analyze(self.r, [{"id": "QQQ", "quantity": 100}], "beta", {"horizon": "1M"}, history=False)
        self.assertIn("scenarios", out)                  # the full production output, not the compact replay
        self.assertIn("ml", out)


class CovarianceSwap(unittest.TestCase):
    def test_market_variance_replaced_and_correlations_kept(self):
        C = {("MKT", "MKT"): 1e-4, ("A", "A"): 4e-4, ("MKT", "A"): 1e-4, ("A", "MKT"): 1e-4, ("B", "B"): 1e-4, ("MKT", "B"): -2e-5, ("B", "MKT"): -2e-5,
             ("A", "B"): 0.0, ("B", "A"): 0.0}
        D = V.scale_market(C, 4e-4)
        self.assertAlmostEqual(D[("MKT", "MKT")], 4e-4)
        corr = lambda X, a, b: X[(a, b)] / math.sqrt(X[(a, a)] * X[(b, b)])  # noqa: E731
        for f in ("A", "B"):
            self.assertAlmostEqual(corr(C, "MKT", f), corr(D, "MKT", f))
        self.assertEqual(D[("A", "A")], C[("A", "A")])
        self.assertIs(V.scale_market(C, None), C)


class PointInTime(unittest.TestCase):
    """Two stores identical up to session k and different after it: every decision on or before k is identical."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        a = _store(os.path.join(cls.tmp, "a.db"))
        cal = Research(a).panel().calendar()
        cls.k = len(cal) - 120
        cls.cut = cal[cls.k]
        b = _store(os.path.join(cls.tmp, "b.db"), shock_from=cls.cut)
        cls.st = (a, b)
        cls.r = (Research(a), Research(b))

    @classmethod
    def tearDownClass(cls):
        for s in cls.st:
            s.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _fc(self, j):
        return V.VolForecaster(self.r[j], self.st[j], MODELS, 5)

    def test_volatility_and_breadth_forecast_ignore_the_future(self):
        fa, fb = self._fc(0), self._fc(1)
        for i in (self.k - 30, self.k):
            a, b = fa.at(i), fb.at(i)
            self.assertIsNotNone(a)
            self.assertEqual(a, b)
        self.assertNotEqual(fa.at(self.k + 20), fb.at(self.k + 20))           # after the cut the data differ

    def test_hedge_decisions_on_the_date_ignore_the_future(self):
        da = V.replay_date(self.r[0], self.k, "1W", 5, self._fc(0), {}, BOOKS, OBJ)
        db = V.replay_date(self.r[1], self.k, "1W", 5, self._fc(1), {}, BOOKS, OBJ)
        self.assertTrue(da["cases"])
        key = lambda d: [(c["book"], c["objective"], {k: [(L["id"], L["q"]) for L in a["legs"]] for k, a in c["arms"].items()},  # noqa: E731
                          {k: a["cost"] for k, a in c["arms"].items()}) for c in d["cases"]]
        self.assertEqual(key(da), key(db))                                     # same decisions, costs, products, sizes
        self.assertEqual(da["forecast"], db["forecast"])
        self.assertNotEqual([c["u"] for c in da["cases"]], [c["u"] for c in db["cases"]])   # the outcomes are the future

    def test_matured_windows_are_identical(self):
        i = self.k - 10                                                          # window (i, i+5] ends before the cut
        da = V.replay_date(self.r[0], i, "1W", 5, self._fc(0), {}, BOOKS, OBJ)
        db = V.replay_date(self.r[1], i, "1W", 5, self._fc(1), {}, BOOKS, OBJ)
        strip = lambda d: [(c["book"], c["objective"], c["u"], {k: a["hp"] for k, a in c["arms"].items()}) for c in d["cases"]]  # noqa: E731
        self.assertEqual(strip(da), strip(db))

    def test_the_challenger_differs_only_in_the_volatility_forecast(self):
        d = V.replay_date(self.r[0], self.k, "1W", 5, self._fc(0), {}, BOOKS, OBJ)
        for c in d["cases"]:
            self.assertEqual(c["u"], c["u"])                                    # one book P&L for every arm
        fc = d["forecast"]
        self.assertNotAlmostEqual(fc["v_breadth"], fc["v_reg"])


def _case(date, u, hA, hB, cost=(0.0, 0.0), book="X", obj="beta"):
    arm = lambda hp, co: {"hp": hp, "cost": co, "legs": [{"id": "S", "type": "SPOT", "product_type": "etf", "q": sum(hp), "notional": abs(sum(hp)),  # noqa: E731
                                                          "beta_usd": 1.0, "option": False}] if any(hp) else [], "skew": 0.0, "mult": 0.9}
    return {"date": date, "book": book, "btype": "t", "objective": obj, "u": u, "ideal": [-x for x in u],
            "arms": {"A": arm(hA, cost[0]), "B": arm(hB, cost[1]), "R": arm(hA, cost[0])}, "regime": {}, "vix": 20.0}


def _dates(n, start=2010):
    out = []
    for k in range(n):
        y = start + k // 50
        out.append(f"{y}-{1 + (k % 50) // 5:02d}-{1 + (k % 5) * 5:02d}")
    return out


class Utility(unittest.TestCase):
    def test_identical_arms_have_zero_delta_and_no_significance(self):
        rnd = random.Random(1)
        cases = []
        for d in _dates(120):
            u = [rnd.gauss(0, 1000) for _ in range(5)]
            h = [-0.5 * x for x in u]
            cases.append(_case(d, u, h, list(h), (10.0, 10.0)))
        p = V.paired(cases, "beta")
        for l in V.LAMBDAS:
            self.assertAlmostEqual(p["d"][str(l)], 0.0)
            self.assertEqual(p["ci"][str(l)], (0.0, 0.0))
        self.assertGreater(p["p"]["1.0"], 0.9)

    def test_a_better_hedge_wins_and_utility_formula(self):
        rnd = random.Random(2)
        cases = []
        for d in _dates(150):
            u = [rnd.gauss(0, 1000) for _ in range(5)]
            cases.append(_case(d, u, [-0.3 * x for x in u], [-0.9 * x for x in u], (5.0, 6.0)))
        p = V.paired(cases, "beta")
        self.assertGreater(p["d"]["1.0"], 0)
        self.assertLess(p["p"]["1.0"], 0.05)
        o = p["b"]
        self.assertAlmostEqual(V.utility(o, 2.0), o["loss_reduction"] - 2.0 * o["profit_sacrificed"] - o["cost"])
        self.assertAlmostEqual(o["variance_reduction"], 1 - 0.1 ** 2, places=6)
        self.assertAlmostEqual(o["hedge_error"], 1 / 0.9 - 1, places=6)          # the ex-post optimal multiple of a −0.9 hedge

    def test_tail_objectives_use_expected_shortfall(self):
        rnd = random.Random(3)
        cases = [_case(d, [rnd.gauss(0, 1000) for _ in range(5)], [0.0] * 5, [0.0] * 5, obj="es") for d in _dates(100)]
        o = V.outcome(cases, "A", "es")
        self.assertAlmostEqual(o["risk_u"], o["es_u"])
        o2 = V.outcome(cases, "A", "beta")
        self.assertNotAlmostEqual(o2["risk_u"], o2["es_u"])

    def test_resizing_arms_only_from_2018(self):
        rnd = random.Random(4)
        cases = [_case(d, [rnd.gauss(0, 1000) for _ in range(5)], [-100.0] * 5, [-90.0] * 5) for d in _dates(300, 2012)]
        cell = V.analyse_cell(cases, "beta", reps=0)
        self.assertEqual(cell["from_2018"]["cases"], sum(1 for c in cases if c["date"] >= "2018-01-01"))
        hp, co = V.arm_series(cases[0], "C")
        self.assertEqual(hp, [0.9 * x for x in cases[0]["arms"]["A"]["hp"]])
        self.assertEqual(cell["decisions"]["sizing"], 1.0)


class Gates(unittest.TestCase):
    def _res(self, d, p, eras_pos, g1=True, dates=200, es=(100.0, 100.0), basis=(10.0, 10.0), cost=(5.0, 5.0), changed=0.5):
        eras = {f"e{k}": {"complete": True, "d": {"1.0": (1.0 if k < eras_pos else -1.0)}} for k in range(4)}
        cell = {"dates": dates, "changed_share": changed, "eras": eras,
                "test": {"d": {"1.0": d}, "p": {"1.0": p}, "a": {"es_u": 1000.0, "es_h": es[0], "basis_error": basis[0], "cost": cost[0]},
                         "b": {"es_h": es[1], "basis_error": basis[1], "cost": cost[1]}}}
        cell["gates"] = V.gates(cell, g1, "beta")
        return {"horizons": {"1W": {"cells": {"beta": cell}}}}

    def status(self, **kw):
        return V.finalise(self._res(**kw))["horizons"]["1W"]["cells"]["beta"]["status"]

    def test_statuses(self):
        self.assertEqual(self.status(d=50.0, p=0.001, eras_pos=4), "SHADOW")
        self.assertEqual(self.status(d=50.0, p=0.001, eras_pos=2), "IMPROVES FORECAST ONLY")         # G3
        self.assertEqual(self.status(d=50.0, p=0.30, eras_pos=4), "IMPROVES FORECAST ONLY")          # G2
        self.assertEqual(self.status(d=50.0, p=0.001, eras_pos=4, g1=False), "NO HEDGE IMPROVEMENT")
        self.assertEqual(self.status(d=50.0, p=0.001, eras_pos=4, dates=30), "INSUFFICIENT DATA")
        self.assertEqual(self.status(d=50.0, p=0.001, eras_pos=4, es=(100.0, 130.0)), "IMPROVES FORECAST ONLY")   # G4: tail worse
        self.assertEqual(self.status(d=50.0, p=0.001, eras_pos=4, cost=(5.0, 6.0)), "IMPROVES FORECAST ONLY")     # G4: cost +20%

    def test_forecast_improvement_alone_is_not_a_pass(self):
        res = self._res(d=0.0, p=1.0, eras_pos=0, changed=0.0)
        cell = V.finalise(res)["horizons"]["1W"]["cells"]["beta"]
        self.assertEqual(cell["status"], "IMPROVES FORECAST ONLY")
        self.assertIn("rarely changes", cell["why"])

    def test_resize_multiple_lookup(self):
        res = {"objective": "beta", "primary_factor": "MKT", "package": [{"id": "SPY", "type": "SPOT", "notional": 1e5}]}
        self.assertEqual(V.resize_multiple(res, {"equity:spot:5": {"exposure": 0.8}}, 5, None), 0.8)
        self.assertEqual(V.resize_multiple(res, {"equity:spot:21": {"exposure": 0.8}}, 5, None), 1.0)


class ForecastCheck(unittest.TestCase):
    def test_breadth_better_than_reference_when_closer(self):
        rnd = random.Random(6)
        dates = []
        for _ in range(200):
            t = rnd.gauss(-4.5, 0.3)
            dates.append({"forecast": {"f_reg": t + rnd.gauss(0, 0.3), "f_breadth": t + rnd.gauss(0, 0.2)}, "realised_log_vol": t,
                          "var_prod": math.exp(2 * (t + rnd.gauss(0, 0.4)))})
        f = V.forecast_check(dates)
        self.assertGreater(f["gain_vs_reference"], 0)
        self.assertGreater(f["t"], 2)
        self.assertLess(f["mse_breadth"], f["mse_production"])


class Extensions(unittest.TestCase):
    def _cases(self, obj):
        rnd = random.Random(8)
        out = []
        for d in _dates(200):
            u = [rnd.gauss(0, 1000) for _ in range(5)]
            out.append(_case(d, u, [-0.4 * x for x in u], [-0.7 * x + rnd.gauss(0, 50) for x in u], (4.0, 5.0), obj=obj))
        return out

    def test_case_shares_sum_to_the_utility_difference(self):
        for obj in ("beta", "es"):
            cases = self._cases(obj)
            for lam in (0.5, 1.0, 5.0):
                d = V.paired(cases, obj, reps=0)["d"][str(lam)]
                self.assertAlmostEqual(sum(V.case_contributions(cases, "A", "B", obj, lam)), d, places=6)

    def test_interaction_classes(self):
        self.assertEqual(V.interaction(10.0, 5.0, 15.5), "additive")
        self.assertEqual(V.interaction(10.0, 5.0, 10.5), "redundant")
        self.assertEqual(V.interaction(10.0, -8.0, 3.0), "conflicting")
        self.assertEqual(V.interaction(10.0, 5.0, 40.0), "interacting")

    def test_extended_cell_runs_and_reports_the_three_arms(self):
        ext = V.extended_cell(self._cases("beta"), "beta")
        self.assertIn("breadth_vs_nobreadth", ext["d"])
        self.assertAlmostEqual(ext["per_case"]["breadth_vs_production"]["mean"] * ext["cases"], ext["d"]["breadth_vs_production"]["1.0"], places=6)


if __name__ == "__main__":
    unittest.main()
