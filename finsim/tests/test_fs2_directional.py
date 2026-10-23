"""Shaffer Alpha vs Shaffer Directional: PIT priors, no leakage, planted effects, metrics, live = research formula."""
import datetime as dt
import math
import random
import unittest
from array import array

from finsim2.engine import directional as D
from finsim2.engine import weights as W

H = 5


def _recs(effect=0.8, drift=True, seed=1, years=(2000, 2026), assets=16, garbage_after=None):
    """Weekly records. The PIT prior says equities drift up (z = +0.3) and ETFs down (z = −0.3); the outcome's sign
    follows that drift and, with `effect`, the production score (raw). ext is set directly (what attach() would add)."""
    W._init_famidx()
    rnd = random.Random(seed)
    P = len(W.signals())
    metas = [{"id": f"A{i}", "asset_class": "EQUITY" if i % 2 else "ETF", "sector": "Financials" if i % 4 < 2 else "Energy"} for i in range(assets)]
    out = []
    d0 = dt.date(years[0], 1, 3)
    for w in range((years[1] - years[0]) * 52):
        d = d0 + dt.timedelta(days=7 * w)
        end = d + dt.timedelta(days=7)
        common = rnd.gauss(0, 0.3)
        for m in metas:
            r = W.Rec()
            r.asset, r.meta, r.date, r.end, r.wk = m["id"], m, d.isoformat(), end.isoformat(), W._week(d.isoformat())
            z = (0.3 if m["asset_class"] == "EQUITY" else -0.3) if drift else 0.0
            a = rnd.gauss(0, 1)
            r.raw = 100 * math.tanh(0.6 * a)
            x = array("f", bytes(4 * P))
            x[0] = max(-1.0, min(1.0, a / 2))
            r.x, r.act = x, ((0, 1, 0.1, 0.8, 1.0, 1.0),)
            r.g = array("f", bytes(4 * len(W.families())))
            r.reg = ("bull", "low_vol", "falling_rates", "expansion")
            s = 0.04
            y = z + effect * 0.5 * a + common + rnd.gauss(0, 1)
            if garbage_after and end.isoformat() >= garbage_after:
                y = rnd.gauss(0, 3)
            r.y, r.yr = y, y * s
            r.ext = {"s": s, "mu": {p: z * s for p in D.PRIORS}, "clim": 0.5, "rule": "hier"}
            r.ext["mu"]["zero"] = 0.0
            r.base = [1.0, None, 1.0 if rnd.random() < 0.5 else -1.0, None, None]
            out.append(r)
    return out


class Metrics(unittest.TestCase):
    def test_confusion_brier_auc(self):
        recs = _recs(years=(2000, 2002), assets=4)
        ps = [0.9 if r.yr > 0 else 0.1 for r in recs]
        m = D.dir_metrics(recs, ps, H)
        self.assertAlmostEqual(m["accuracy"], 1.0)
        self.assertAlmostEqual(m["balanced_accuracy"], 1.0)
        self.assertAlmostEqual(m["auc"], 1.0)
        self.assertAlmostEqual(m["brier"], 0.01, places=6)
        self.assertEqual(m["confusion"]["up_called_down"] + m["confusion"]["down_called_up"], 0)
        self.assertAlmostEqual(m["bear_precision"], 1.0)
        flipped = D.dir_metrics(recs, [1 - p for p in ps], H)
        self.assertAlmostEqual(flipped["accuracy"], 0.0)
        self.assertAlmostEqual(flipped["auc"], 0.0)
        half = D.dir_metrics(recs, [0.5] * len(recs), H)
        self.assertEqual(half["coverage"], 0.0)                 # p = 0.5 is not a call
        self.assertAlmostEqual(half["brier"], 0.25)

    def test_bands_expected_vs_realized(self):
        recs = _recs(years=(2000, 2004), assets=8)
        ps = [D._phi(D.z_of(r, "product")) for r in recs]
        m = D.dir_metrics(recs, ps, H, with_bands=True)
        self.assertEqual(len(m["bands"]), 9)
        self.assertEqual(sum(b["n"] for b in m["bands"]), m["n"])
        for b in m["bands"]:
            if b["n"]:
                self.assertTrue(0 <= b["expected_p"] <= 1 and 0 <= b["positive"] <= 1)

    def test_logistic_recovers_coefficients(self):
        rnd = random.Random(3)
        X, o = [], []
        for _ in range(20000):
            x = rnd.gauss(0, 1)
            X.append([1.0, x]); o.append(1.0 if rnd.random() < D._sig(0.4 + 1.2 * x) else 0.0)
        w = D.logistic(X, o, [0.0, 0.0], 1e-6, 1.0, iters=25)
        self.assertAlmostEqual(w[0], 0.4, delta=0.06)
        self.assertAlmostEqual(w[1], 1.2, delta=0.08)


class ProductRule(unittest.TestCase):
    def test_rules(self):
        R = D.product_rule
        self.assertEqual(R({"id": "XOM", "asset_class": "EQUITY", "sector": "Energy"}), "beta_market")
        self.assertEqual(R({"id": "XLE", "asset_class": "ETF", "sector": "Energy"}), "beta_market")
        self.assertEqual(R({"id": "USO", "asset_class": "ETF", "sector": "Energy"}), "zero")
        self.assertEqual(R({"id": "WTI", "asset_class": "COMMODITY"}), "zero")
        self.assertEqual(R({"id": "EURUSD", "asset_class": "FX"}), "zero")
        self.assertEqual(R({"id": "TLT", "asset_class": "ETF", "sector": "Fixed Income"}), "carry")
        self.assertEqual(R({"id": "UST10Y", "asset_class": "TREASURY"}), "carry")
        self.assertEqual(R({"id": "HYG", "asset_class": "ETF", "sector": "Fixed Income"}), "hier")
        self.assertEqual(R({"id": "BTC", "asset_class": "CRYPTO"}), "hier")
        self.assertEqual(R({"id": "VXX", "asset_class": "ETF", "sector": "Volatility"}), "own")
        self.assertEqual(R({"id": "SQQQ", "asset_class": "ETF", "sector": "Leveraged / Inverse"}), "beta_market")


class Directional(unittest.TestCase):
    def test_prior_plus_alpha_beats_prior_and_passes(self):
        recs = _recs(effect=0.8)
        prior = D.run_dir(recs, H, "prior", prior="product")
        both = D.run_dir(recs, H, "alpha+prior", depth="class")
        self.assertGreater(both["walkforward"]["accuracy"], prior["walkforward"]["accuracy"])
        self.assertGreater(both["walkforward"]["auc"], prior["walkforward"]["auc"])
        self.assertLess(both["walkforward"]["brier"], prior["walkforward"]["brier"])
        self.assertEqual(both["gates"]["status"], "SHADOW")
        # the prior alone knows the drift: better than always-zero drift
        zero = D.run_dir(recs, H, "prior", prior="zero")
        self.assertLess(prior["walkforward"]["brier"], zero["walkforward"]["brier"] + 1e-12)

    def test_paired_adds_only_with_a_real_effect(self):
        pairs = [("alpha+prior@global", "prior:product")]
        real = D.paired_study(_recs(effect=0.8, years=(2000, 2020), assets=8), H, pairs)[0]
        self.assertTrue(real["adds"])
        self.assertGreater(real["brier_gain"], 0)
        noise = D.paired_study(_recs(effect=0.0, seed=11, years=(2000, 2020), assets=8), H, pairs)[0]
        self.assertFalse(noise["adds"])

    def test_noise_rejected(self):
        recs = _recs(effect=0.0, drift=False, seed=5)
        res = D.run_dir(recs, H, "alpha+prior", depth="global")
        self.assertNotEqual(res["gates"]["status"], "SHADOW")

    def test_signal_plus_prior_runs(self):
        recs = _recs(effect=0.8, years=(2000, 2020), assets=8)
        res = D.run_dir(recs, H, "signal+prior", depth="global")
        self.assertIn("signal", res["final"])
        self.assertGreater(res["walkforward"]["auc"], 0.55)


class NoLeakage(unittest.TestCase):
    def test_era_fits_ignore_outcomes_after_the_cutoff(self):
        clean = _recs(effect=0.8, seed=7, years=(2000, 2016), assets=8)
        dirty = _recs(effect=0.8, seed=7, years=(2000, 2016), assets=8, garbage_after="2013-01-01")
        a = D.run_dir(clean, H, "alpha+prior", depth="class")
        b = D.run_dir(dirty, H, "alpha+prior", depth="class")
        for ea, eb in zip(a["eras"], b["eras"]):
            if ea["from"] <= "2013-01-01" and ea.get("coef_global"):
                for u, v in zip(ea["coef_global"], eb["coef_global"]):
                    self.assertAlmostEqual(u, v, places=10)

    def test_base_prior_and_climatology_are_point_in_time(self):
        """attach(): altering prices after day k and outcomes maturing after it leaves every earlier record's inputs unchanged."""
        n = 900
        cal = [(dt.date(2000, 1, 3) + dt.timedelta(days=i)).isoformat() for i in range(n)]
        rnd = random.Random(2)

        def walk(seed, mu):
            g = random.Random(seed)
            p, out = 100.0, []
            for _ in range(n):
                p *= math.exp(mu + g.gauss(0, 0.01)); out.append(p)
            return out
        spy, a0, b0 = walk(1, 0.0004), walk(2, 0.0006), walk(3, -0.0002)

        def build(k=None):
            series = {"SPY": list(spy), "Z": list(a0), "Q": list(b0)}
            if k is not None:
                for s in series.values():
                    for i in range(k, n):
                        s[i] *= 3.0
            class Panel:
                def calendar(self): return cal
                def series(self, a, field="adj_close"): return series[a]
                def macro(self, sid): return [4.0] * n
            class R:
                def panel(self): return Panel()
            recs = []
            for a, cls in (("Z", "EQUITY"), ("Q", "ETF")):
                meta = {"id": a, "asset_class": cls, "sector": "X"}
                for i in range(300, n - 21, 5):
                    r = W.Rec()
                    r.asset, r.meta, r.date, r.end, r.wk = a, meta, cal[i], cal[i + 21], i // 5
                    px = series[a]
                    r.yr = math.log(px[i + 21] / px[i]); r.y = r.yr / 0.05; r.raw = 0.0
                    recs.append(r)
            D.attach(recs, R(), 21)
            return {(r.asset, r.date): r.ext for r in recs}
        k = 700
        before, after = build(), build(k)
        checked = 0
        for key, e in before.items():
            if key[1] < cal[k]:
                e2 = after[key]
                self.assertAlmostEqual(e["clim"], e2["clim"], places=12)
                for p in e["mu"]:
                    u, v = e["mu"][p], e2["mu"][p]
                    self.assertTrue((u is None and v is None) or abs(u - v) < 1e-12, (key, p, u, v))
                checked += 1
        self.assertGreater(checked, 50)
        # and the later records do see the change (the test would pass vacuously otherwise)
        self.assertTrue(any(abs((before[kk]["mu"]["hier"] or 0) - (after[kk]["mu"]["hier"] or 0)) > 1e-9 for kk in before if kk[1] > cal[k + 30]))


class Alpha(unittest.TestCase):
    def test_alpha_metrics_rank_relative_opportunity(self):
        recs = _recs(effect=0.8, years=(2000, 2006))
        m = D.alpha_metrics(recs, lambda r: r.raw, H)
        self.assertGreater(m["rank_ic"], 0.1)
        self.assertGreater(m["quintile_spread"], 0)
        self.assertGreater(m["hit_vs_median"], 0.5)
        noise = D.alpha_metrics(recs, lambda r: random.Random(hash(r.asset + r.date)).gauss(0, 1), H)
        self.assertLess(abs(noise["rank_ic"]), 0.05)

    def test_alpha_target_removes_the_base_drift(self):
        r = _recs(years=(2000, 2001), assets=2)[0]
        self.assertAlmostEqual(D.y_alpha(r), max(-4, min(4, r.y - D.z_of(r, D.MAIN_PRIOR))))

    def test_run_alpha_finds_planted_signal(self):
        recs = _recs(effect=0.8, years=(2000, 2020), assets=8)
        res = D.run_alpha(recs, H, "signal", "global")
        self.assertGreater(res["final"]["weights"]["global"][0], 0)


class Live(unittest.TestCase):
    def test_live_p_equals_research_prediction(self):
        recs = _recs(effect=0.8, years=(2000, 2016), assets=8)
        res = D.run_dir(recs, H, "alpha+prior", depth="class")
        spec = {"model": "alpha+prior", "depth": "class", "prior": D.MAIN_PRIOR, **res["final"]}
        r = recs[-1]
        want = D._predict_tree(res["final"]["coef"], D._xrow(r, "alpha+prior", D.MAIN_PRIOR), W._cut(W.node_path(r.meta, "hier"), "class"))
        info = {}
        got = D.live_p(spec, r.meta, H, r.raw, None, r.ext, info)
        self.assertAlmostEqual(got, want, places=12)
        self.assertEqual(info["node"], "class:" + r.meta["asset_class"])
        self.assertIsNone(D.live_p(spec, r.meta, H, None, None, r.ext))


if __name__ == "__main__":
    unittest.main()
