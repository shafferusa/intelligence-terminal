"""ML v2: the verified-edge rule (baselines, holdout, permutation), point-in-time helpers, and the Shaffer/ML
agreement labels."""
import random
import unittest

from finsim2.engine import ml
from finsim2.engine.scores import agreement


def _dataset(n=600, h=5, signal=0.0, seed=1):
    """Synthetic rows every 5 sessions: y = signal * x0 + noise; the baseline inputs are pure noise."""
    rnd = random.Random(seed)
    X = [[rnd.gauss(0, 1) for _ in range(4)] for _ in range(n)]
    y = [signal * x[0] * 0.02 + rnd.gauss(0, 0.02) for x in X]
    noise = lambda: [rnd.gauss(0, 1) for _ in range(n)]
    return {"names": ["ret_1m", "ret_3m", "vol_20", "z_20"], "idx": [5 * i for i in range(n)], "X": X, "y": y,
            "x_today": [1.0, 0.0, 0.0, 0.0], "last": 5 * n, "step": 5, "prev": noise(), "mom": noise(), "mr": noise(),
            "vol_target": [abs(rnd.gauss(0.2, 0.05)) for _ in range(n)], "dd_target": [float(rnd.random() < 0.2) for _ in range(n)],
            "dd_threshold": 0.05, "vol_now": [abs(rnd.gauss(0.2, 0.05)) for _ in range(n)], "ewma_now": [abs(rnd.gauss(0.2, 0.05)) for _ in range(n)]}


class VerifiedEdge(unittest.TestCase):
    def test_noise_has_no_verified_edge_and_scores_zero(self):
        out = ml.train_horizon(_dataset(signal=0.0), 5, "1W")
        self.assertEqual(out["status"], "ok")
        self.assertFalse(out["verified"])
        self.assertEqual(out["score"], 0.0)
        self.assertEqual(out["confidence"]["value"], 0.0)
        self.assertTrue(out["message"].startswith("NO VERIFIED ML EDGE"))
        self.assertTrue(out["edge_reasons"])

    def test_a_real_relationship_is_verified_against_every_baseline(self):
        out = ml.train_horizon(_dataset(signal=1.0), 5, "1W")
        self.assertTrue(out["verified"], out["edge_reasons"])
        self.assertGreater(out["ensemble"]["holdout"]["ic"], 0)
        self.assertLess(out["ensemble"]["permutation_p"], 0.05)
        for k in ("zero", "historical_mean", "previous_return", "momentum", "mean_reversion"):
            self.assertIn(k, out["baselines"])
        self.assertGreater(out["score"], 0)            # today's x0 = +1 with a positive relationship

    def test_a_shaffer_baseline_that_already_knows_the_signal_blocks_the_edge(self):
        ds = _dataset(signal=1.0)
        shaffer = [x[0] for x in ds["X"]]                # the transparent score already carries all the information
        out = ml.train_horizon(ds, 5, "1W", shaffer=shaffer)
        self.assertIn("shaffer", out["baselines"])
        self.assertFalse(out["verified"])
        self.assertTrue(any("shaffer" in r for r in out["edge_reasons"]))

    def test_holdout_is_the_last_fifteen_percent(self):
        ds = _dataset()
        H0, hold = ml._split(ds, 5)
        self.assertEqual(len(hold), len(ds["idx"]) - H0)
        self.assertAlmostEqual(len(hold) / len(ds["idx"]), ml.HOLDOUT_FRAC, delta=0.01)


class PointInTime(unittest.TestCase):
    def test_expanding_mean_uses_only_matured_outcomes(self):
        ds = {"idx": list(range(0, 200, 5))}
        series = [float(i) for i in range(40)]
        rows = list(range(40))
        out = ml._expanding_mean(ds, rows, 21, series)
        for r, v in zip(rows, out):
            known = [series[i] for i in range(40) if ds["idx"][i] + 21 < ds["idx"][r]]
            if len(known) < 10:
                self.assertIsNone(v)
            else:
                self.assertAlmostEqual(v, sum(known) / len(known))

    def test_permutation_pvalue_separates_signal_from_noise(self):
        rnd = random.Random(3)
        y = [rnd.gauss(0, 1) for _ in range(300)]
        self.assertLess(ml._perm_pvalue([v + rnd.gauss(0, 1) for v in y], y, 1), 0.05)
        self.assertGreater(ml._perm_pvalue([rnd.gauss(0, 1) for _ in y], y, 1), 0.05)


class Agreement(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(agreement(60, 0, False, 0.7), "NO VERIFIED ML EDGE")
        self.assertEqual(agreement(60, 55, True, 0.7), "STRONG AGREEMENT")
        self.assertEqual(agreement(60, -55, True, 0.7), "STRONG DISAGREEMENT")


if __name__ == "__main__":
    unittest.main()
