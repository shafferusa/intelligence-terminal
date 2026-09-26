"""The learned-Shaffer fine-tune (engine/finetune.py), its reports (engine/finetune_report.py) and the hedge λ surface
(hedge/hedgetune.py)."""
import random
import unittest
from array import array

from finsim2.engine import finetune as F
from finsim2.engine import finetune_report as FR
from finsim2.engine import learned as L
from finsim2.hedge import hedgetune as HT


def _stat(P=3, n=4000, beta=(0.5, -0.3, 0.0), seed=1):
    rnd = random.Random(seed)
    cols = [array("d", (rnd.gauss(0, 1) for _ in range(n))) for _ in range(P)]
    y = array("d", (sum(b * c[k] for b, c in zip(beta, cols)) + rnd.gauss(0, 1) for k in range(n)))
    return L.cell_stat(cols, y, array("d", [1.0] * n), 0, n)


class Solvers(unittest.TestCase):
    def test_ridge_vec_recovers_and_restricts(self):
        st = _stat()
        w = F.ridge_vec(st, [1.0] * 3, [0.0] * 3, [1.0] * 3)
        self.assertAlmostEqual(w[0], 0.5, delta=0.05)
        self.assertAlmostEqual(w[1], -0.3, delta=0.05)
        only0 = F.ridge_vec(st, [1.0] * 3, [0.0] * 3, [1.0] * 3, idx=[0])
        self.assertEqual(only0[1:], [0.0, 0.0])                              # features outside the subset stay at the prior
        pulled = F.ridge_vec(st, [1.0] * 3, [0.0, 0.9, 0.0], [1.0, 1e9, 1.0])
        self.assertAlmostEqual(pulled[1], 0.9, delta=1e-3)                   # a huge per-coefficient penalty pins it to its prior

    def test_week_rank_is_centred(self):
        rows = []
        for w in range(3):
            for k in range(5):
                rows.append({"asset": f"A{k}", "path": ["global", f"asset:A{k}"], "date": f"2010-01-{10 + 7 * w:02d}", "end": f"2010-01-{17 + 7 * w:02d}",
                             "wk": 100 + w, "x": [float(k)], "y": 0.0, "z": 0.0, "o": 0})
        tab = L.Table(rows, ["a"], 5, "value")
        r = F.week_rank(tab, array("d", tab.X[0]))
        vals = sorted({round(v, 6) for v in r})
        self.assertEqual(vals, [-0.5, -0.25, 0.0, 0.25, 0.5])


class Gates(unittest.TestCase):
    def _res(self, g5, t=3.0):
        g = {"G1": True, "G2": True, "G3": True, "G4": True, "G5": g5, "t": t}
        return {"1W": {"models": {"learned global (D)": {"family": "baseline", "pit": True, "gates": dict(g)},
                                  "x": {"family": "time", "pit": True, "gates": dict(g)},
                                  "gbm": {"family": "interactions", "pit": False, "gates": dict(g)}}}}

    def test_g5_is_required_for_1w(self):
        r = F.finalise(self._res(True))
        self.assertEqual(r["1W"]["models"]["x"]["status"], "LIVE SHADOW ELIGIBLE")
        self.assertEqual(r["1W"]["models"]["learned global (D)"]["status"], "baseline (live shadow)")
        self.assertEqual(r["1W"]["models"]["gbm"]["status"], "descriptive only (selection not PIT)")
        r = F.finalise(self._res(False))
        self.assertEqual(r["1W"]["models"]["x"]["status"], "NOT VALIDATED")   # beating production is not enough
        r = F.finalise(self._res(True, t=0.3))
        self.assertEqual(r["1W"]["models"]["x"]["status"], "NOT VALIDATED")   # FDR

    def test_versions_never_touch_live_shadow(self):
        self.assertEqual(F.vid_of("1W", "time decay (global)"), "alpha-learned-1w-decay-exp")
        self.assertEqual(F.vid_of("1W", "blend"), "alpha-learned-1w-blend-exp")
        ids = {F.vid_of("1W", k) for k in F.VERSION_OF}
        self.assertNotIn("alpha-learned-1w-global-exp", ids)
        self.assertNotIn("alpha-learned-1w-hierarchy-exp", ids)


class HedgeSurface(unittest.TestCase):
    def test_isotonic_non_increasing(self):
        v = HT._isotonic_dec([1.2, 1.3, 1.0, 1.1, 0.9])
        self.assertTrue(all(a >= b - 1e-12 for a, b in zip(v, v[1:])))
        self.assertAlmostEqual(sum(v), 1.2 + 1.3 + 1.0 + 1.1 + 0.9)
        self.assertEqual(HT._isotonic_dec([1.5, 1.2, 0.8]), [1.5, 1.2, 0.8])

    def test_shrinkage_and_monotone_in_lambda(self):
        L_ = HT.LAMBDAS
        fit = {"global": {"n": 600, "m_hat": dict(zip(L_, [1.4, 1.3, 1.1, 0.9, 0.7]))},
               "risk:Equity": {"n": 300, "m_hat": dict(zip(L_, [1.5, 1.4, 1.2, 1.3, 0.6]))},
               "objective:Equity/beta": {"n": 3, "m_hat": dict(zip(L_, [0.5] * 5))},
               "objective:Equity/beta|high_vol": {"n": 0, "m_hat": {l: None for l in L_}}}
        s = HT.shrink_surface(fit)
        for nd, v in s.items():
            vals = [v[l] for l in L_]
            self.assertTrue(all(a >= b - 1e-12 for a, b in zip(vals, vals[1:])), nd)
        self.assertAlmostEqual(s["global"][0.5], 600 / 660 * 1.4 + 60 / 660 * 1.0)
        for l in L_:                                                       # 3 dates: close to the parent; none: the parent
            self.assertLess(abs(s["objective:Equity/beta"][l] - s["risk:Equity"][l]), 0.05)
            self.assertEqual(s["objective:Equity/beta|high_vol"][l], s["objective:Equity/beta"][l])
        c = {"_tp": ["global", "risk:Equity", "objective:Equity/beta", "objective:Equity/beta|high_vol"]}
        self.assertEqual(HT.mult_at(c, s, 1.0), s["objective:Equity/beta|high_vol"][1.0])


class Capability(unittest.TestCase):
    def test_planted_effects(self):
        cap = F.capability2(n_assets=16)
        for k, v in cap.items():
            self.assertTrue(v["pass"], f"{k}: {v}")


class Reports(unittest.TestCase):
    def test_reports_render_from_partial_results(self):
        res = {"started": "x", "seconds": 1, "capability": {"t": {"pass": True, "truth": 1, "got": 1}},
               "1W": {"signals": ["a", "b"], "models": {"learned global (D)": {"family": "baseline", "pit": True, "gates": {}, "walkforward": {}},
                                                        "blend": {"family": "blend", "pit": True, "gates": {"vsD_t": 0.5, "vsD_mean": 0.001}, "walkforward": {},
                                                                  "status": "NOT VALIDATED", "choices": {"2013-01-01": "0.5"}, "final": "0.5"}},
                      "weights": {"D": [0.2, -0.1], "blend": [0.1, 0.1]}, "stability_today": {"classes": ["STABLE", "UNSTABLE"], "stable_sign": [True, False]}},
               "hedge": {"cells": {}, "surface": {}}}
        md = FR.markdown(res)
        self.assertIn("## 2. Answers", md)
        self.assertIn("**19.", md)
        self.assertIn("alpha-learned-1w-blend-exp", md)
        hm = FR.hedge_markdown({"cells": {"1W": {"beta@1.0": {"lam": 1.0, "node": "objective:Equity/beta", "d": 1.0, "t": 2.5, "status": "NO HEDGE IMPROVEMENT",
                                                              "gates": {"enough": True}, "a": {}, "b": {}}}}, "surface": {}})
        self.assertIn("| beta | 1W | 1.0 | 1.00× |", hm)


if __name__ == "__main__":
    unittest.main()
