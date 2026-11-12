"""engine/ahzlive.py: the 1M Alpha challenger in live shadow — frozen weights, the weekly statistic and the live gate."""
import datetime as dt
import json
import os
import random
import shutil
import tempfile
import unittest

from finsim2.data.store import Store
from finsim2.engine import ahzlive as L
from finsim2.engine import alphahz as A


def _spec(d=0.0118, t=1.13, n=212):
    p = len(A.feature_names())
    spec = {"id": "test-1m", "lab": "1M", "depth": 4, "K": 10.0, "features": A.feature_names(), "rms": [1.0] * p,
            "weights": {"global": [0.1] * p, "g:short": [0.2] * p, "h:1M": [0.3] * p, "h:1M|s:Tech": [1.0] + [0.0] * (p - 1)},
            "backtest": {"d": d, "t": t, "dates": n, "d_original": 0.0}}
    spec["hash"] = L._digest(spec)
    return spec


class Frozen(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_hash_and_feature_checks(self):
        path = os.path.join(self.tmp, "m.json")
        L.write_frozen(_spec(), path)
        self.assertEqual(L.load_frozen(path)["id"], "test-1m")
        with open(path) as f:
            s = json.load(f)
        s["weights"]["global"][0] = 9.0                                   # an edit without a new hash
        with open(path, "w") as f:
            json.dump(s, f)
        with self.assertRaises(ValueError):
            L.load_frozen(path)
        s = _spec()
        s["features"] = s["features"][:-1]
        s["hash"] = L._digest(s)
        L.write_frozen(s, path)
        with self.assertRaises(ValueError):
            L.load_frozen(path)

    def test_the_committed_model_loads(self):
        spec = L.load_frozen()
        self.assertIsNotNone(spec, "finsim2/frozen/alpha_hz_1m.json is committed with the protocol")
        self.assertEqual(spec["lab"], "1M")
        self.assertIn("h:1M", spec["weights"])

    def test_scores_fall_back_to_the_deepest_known_node(self):
        spec = _spec()
        p = len(spec["features"])
        x = [1.0] + [0.5] * (p - 1)
        self.assertAlmostEqual(L.score_x(spec, "Tech", "NEW", x), 1.0, "sector node (depth 4 has no stock nodes)")
        self.assertAlmostEqual(L.score_x(spec, "Energy", "NEW", x), 0.3 * (1.0 + 0.5 * (p - 1)), msg="no sector node: the horizon node")


class LiveGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.st = Store(os.path.join(self.tmp, "x.db"))

    def tearDown(self):
        self.st.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _weeks(self, weeks, edge_c, edge_p, stocks=100, seed=1, collapse_after=None):
        rnd = random.Random(seed)
        panels = []
        d0 = dt.date(2026, 10, 5)
        for w in range(weeks):
            d = (d0 + dt.timedelta(days=7 * w)).isoformat()
            panels.append({"date": d, "week": L._week(d), "scored": stocks})
            ec = 0.0 if collapse_after is not None and w >= collapse_after else edge_c
            for k in range(stocks):
                f = rnd.gauss(0, 1)
                y = f + rnd.gauss(0, 2.5)
                cs = ec * f + rnd.gauss(0, 1) * (1 - ec)
                ps = edge_p * f + rnd.gauss(0, 1) * (1 - edge_p)
                common = {"asset_id": f"S{k}", "horizon": "1M", "made_on": d, "target_date": d, "realized": y}
                self.st.add_prediction({**common, "model": L.MODEL, "model_version": "t", "raw": cs, "source": "shadow",
                                        "detail": {"original": k < 45}})
                self.st.add_prediction({**common, "model": "shaffer", "model_version": "t", "raw": ps, "source": "panel"})
        self.st.kv_set(L.PANEL_KEY, panels)

    def test_a_persistent_edge_passes(self):
        self._weeks(60, 0.35, 0.2)
        g = L.live_gate(self.st, _spec(d=0.05, t=3.0))
        self.assertEqual(g["weeks_compared"], 60)
        self.assertTrue(all(g["checks"].values()), g["checks"])
        self.assertEqual(g["label"], L.PERSISTS)
        self.assertGreater(g["mean_d"], 0)

    def test_too_few_weeks_accumulates(self):
        self._weeks(20, 0.35, 0.2)
        g = L.live_gate(self.st, _spec(d=0.05, t=3.0))
        self.assertFalse(g["passed"])
        self.assertFalse(g["checks"]["weeks"])
        self.assertEqual(g["label"], L.ACCUMULATING)

    def test_a_collapsing_effect_fires_the_decay_alarm(self):
        self._weeks(60, 0.6, 0.2, collapse_after=10)
        g = L.live_gate(self.st, _spec(d=0.3, t=6.0))
        self.assertIsNotNone(g["cusum"]["fired_at_week"])
        self.assertFalse(g["passed"])
        self.assertEqual(g["label"], L.FAILED)

    def test_weeks_needed_is_honest_at_the_backtest_effect(self):
        g = L.live_gate(self.st, _spec())
        self.assertGreater(g["weeks_needed_vs_production"], 1000, "a +0.012 Δ at t 1.1 cannot be confirmed live within years")
        self.assertEqual(g["weeks_graded"], 0)


if __name__ == "__main__":
    unittest.main()
