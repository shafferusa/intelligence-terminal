"""Objective-specific hedge ML (finsim2.hedge.objml): exact re-scoring of any hedge multiple, point-in-time features,
the incremental ridge, the verification bar per objective, and the live cap."""
import datetime as dt
import math
import random
import unittest
from array import array

from finsim2.hedge import objml as O
from finsim2.hedge.history import ADJ_CAP, ALPHA


def _rows(n=240, h=21, signal=True, seed=1):
    """Synthetic month-start windows. The hedge leg is a perfect offset scaled by k: when the feature x is high the
    sized hedge is too SMALL (k < 1 → more hedge helps), when low it is too big. Without signal, k is noise."""
    rnd = random.Random(seed)
    d0 = dt.date(2008, 1, 1)
    out = []
    for j in range(n):
        x = rnd.gauss(0, 1)
        k = 1 - 0.12 * x if signal else 1 + 0.12 * rnd.gauss(0, 1)
        f = [rnd.gauss(0, 0.01) for _ in range(h)]
        u = [100_000 * v + rnd.gauss(0, 50) for v in f]
        hp = [-k * 100_000 * v for v in f]
        a, b = d0 + dt.timedelta(days=30 * j), d0 + dt.timedelta(days=30 * j + 29)
        out.append({"date": a.isoformat(), "end": b.isoformat(), "h": h, "ratio": 1.0, "case": "A|leg", "U": sum(u), "H": sum(hp),
                    "var_u": sum(v * v for v in u), "var_h": sum((p + q) ** 2 for p, q in zip(u, hp)),
                    "features": {"vix": 0.2 + 0.05 * x, "corr_63": None if j % 7 == 0 else 0.9}, "mv_mult": 1.0,
                    "_u": array("d", u), "_hp": array("d", hp), "_f": array("d", f)})
    return out


class Rescoring(unittest.TestCase):
    def test_window_metrics_are_exact_for_any_multiple(self):
        r = _rows(n=1)[0]
        u, hp = list(r["_u"]), list(r["_hp"])
        for m in (0.85, 1.0, 1.15):
            self.assertAlmostEqual(O.window_metric(r, "variance", m), sum((a + m * b) ** 2 for a, b in zip(u, hp)))
            self.assertAlmostEqual(O.window_metric(r, "downside", m), sum(min(0, a + m * b) ** 2 for a, b in zip(u, hp)))
        eq, pk, dd = 0.0, 0.0, 0.0
        for a, b in zip(u, hp):
            eq += a + b; pk = max(pk, eq); dd = min(dd, eq - pk)
        self.assertAlmostEqual(O.window_metric(r, "drawdown", 1.0), -dd)
        self.assertGreaterEqual(O.window_metric(r, "exposure", 1.0), 0.0)

    def test_best_adjustment_is_on_the_capped_grid(self):
        for r in _rows(n=30):
            for mt in O.METRICS:
                a = O.best_adjustment(r, mt)
                self.assertIsNotNone(a)
                self.assertLessEqual(abs(a), ADJ_CAP + 1e-12)

    def test_objective_to_metric(self):
        self.assertEqual(O.metric_for("crash"), "es95")
        self.assertEqual(O.metric_for("var"), "var95")
        self.assertEqual(O.metric_for("duration"), "exposure")
        self.assertEqual(O.metric_for("drawdown"), "drawdown")
        self.assertEqual(O.metric_for("min_variance"), "variance")
        self.assertEqual(O.metric_for(None), "variance")


class PointInTime(unittest.TestCase):
    def test_missing_values_are_filled_from_earlier_windows_only(self):
        rows = [{"features": {"a": v}} for v in (None, 1.0, 3.0, None, 10.0, None)]
        X = O.design(rows, ["a"])
        self.assertEqual([x[0] for x in X], [0.0, 1.0, 3.0, 2.0, 10.0, (1 + 3 + 10) / 3])

    def test_track_record_uses_finished_windows_only(self):
        rows = _rows(n=20)
        t = O.track_features(rows, rows[10]["date"])
        done = [r for r in rows if r["end"] < rows[10]["date"]][-O.TRACK_N:]
        vu = sum(r["var_u"] for r in done)
        self.assertAlmostEqual(t["track_reduction"], 1 - sum(r["var_h"] for r in done) / vu)
        self.assertEqual(O.track_features(rows[:3], rows[3]["date"])["track_reduction"], None)

    def test_incremental_ridge_matches_least_squares_without_penalty(self):
        rnd = random.Random(2)
        X = [[rnd.gauss(0, 1), rnd.gauss(0, 2), 5.0] for _ in range(300)]        # a constant column is ignored
        y = [0.5 + 2 * a - 0.3 * b + rnd.gauss(0, 0.1) for a, b, _ in X]
        rg = O.IncRidge(3, lam=0.0)
        for x, v in zip(X, y):
            rg.add(x, v)
        m = rg.solve()
        self.assertAlmostEqual(m["b"][0], 2.0, delta=0.02)
        self.assertAlmostEqual(m["b"][1], -0.3, delta=0.02)
        self.assertEqual(m["b"][2], 0.0)
        self.assertAlmostEqual(O.predict(m, [1.0, 1.0, 5.0]), 0.5 + 2 - 0.3, delta=0.03)


class Verification(unittest.TestCase):
    def test_a_learnable_sizing_error_is_verified_on_variance(self):
        res = O.evaluate(_rows(signal=True), "variance", ["vix", "corr_63"])
        self.assertTrue(res["verified"], res["reasons"])
        self.assertGreater(res["gain_vs_static"], 0)

    def test_noise_is_not_verified_on_any_metric(self):
        for mt in O.METRICS:
            res = O.evaluate(_rows(signal=False, seed=3), mt, ["vix", "corr_63"])
            self.assertFalse(res["verified"], mt)
            self.assertTrue(res["reasons"])

    def test_a_constant_sizing_bias_is_not_conditional_skill(self):
        rows = _rows(signal=False, seed=5)
        for r in rows:                                   # every window's hedge is 20% too big, whatever the features say
            r["_hp"] = array("d", [1.2 * v for v in r["_hp"]])
            r["H"] = sum(r["_hp"])
            r["mv_mult"] = None                          # no min-variance baseline (as for options)
        res = O.evaluate(rows, "variance", ["vix", "corr_63"])
        self.assertGreater(res["gain_vs_static"], 0)     # resizing helps ...
        self.assertFalse(res["verified"])                # ... but the model has no skill beyond a constant
        self.assertTrue(any("constant" in x for x in res["reasons"]))
        self.assertLess(res["constant_adjustment"], -0.1)

    def test_many_books_on_the_same_dates_are_not_independent_evidence(self):
        rows = _rows(signal=True, seed=7)
        one = O.evaluate([dict(r, features=dict(r["features"])) for r in rows], "variance", ["vix", "corr_63"])
        many = []
        for k in range(10):                               # the same windows held by ten books
            many += [dict(r, case=f"B{k}|leg", features=dict(r["features"])) for r in rows]
        ten = O.evaluate(many, "variance", ["vix", "corr_63"])
        self.assertAlmostEqual(ten["t_vs_static"], one["t_vs_static"], delta=0.2 * abs(one["t_vs_static"]))
        self.assertAlmostEqual(ten["n_eff"], one["n_eff"], delta=2)

    def test_too_few_windows(self):
        res = O.evaluate(_rows(n=30), "variance", ["vix"])
        self.assertFalse(res["verified"])
        self.assertIn("out-of-sample windows", res["reasons"][0])


class LiveCap(unittest.TestCase):
    def test_unverified_or_missing_model_changes_nothing(self):
        self.assertEqual(O.adjustment(None, "equity:spot:21", "beta", {})["applied"], 0.0)
        m = {"groups": {"equity:spot:21": {"exposure": {"verified": False, "reasons": ["x"]}}}}
        a = O.adjustment(m, "equity:spot:21", "beta", {})
        self.assertEqual((a["applied"], a["metric"], a["reasons"]), (0.0, "exposure", ["x"]))
        # a verified VARIANCE model is not used for a crash (ES) hedge
        m = {"groups": {"equity:spot:21": {"variance": {"verified": True, "model": {"my": 9.0, "mu": [0.0], "b": [0.0]}, "features": ["vix"], "fill": {}}}}}
        self.assertEqual(O.adjustment(m, "equity:spot:21", "crash", {})["applied"], 0.0)

    def test_verified_model_is_capped(self):
        m = {"groups": {"g": {"es95": {"verified": True, "model": {"my": 9.0, "mu": [0.0], "b": [0.0]}, "features": ["vix"], "fill": {}}}}}
        a = O.adjustment(m, "g", "crash", {"vix": 0.3})
        self.assertAlmostEqual(a["adjustment"], ADJ_CAP)
        self.assertAlmostEqual(a["applied"], ALPHA * ADJ_CAP)


if __name__ == "__main__":
    unittest.main()
