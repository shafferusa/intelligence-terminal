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


class RiskModels(unittest.TestCase):
    """Item 10: risk problems (volatility, drawdown, tail loss, beta change) each judged against naive baselines on the
    same out-of-sample rows; verified = beats every baseline, stable = beats them in both halves."""

    def test_risk_compare_verified_and_stable(self):
        rnd = random.Random(3)
        y = [rnd.gauss(0, 1) for _ in range(200)]
        good = [v + rnd.gauss(0, 0.3) for v in y]
        naive = [rnd.gauss(0, 1) for _ in y]
        out = ml._risk_compare(good, y, {"naive": naive, "zero": [0.0] * len(y)}, 21, 21)
        self.assertTrue(out["verified"])
        self.assertTrue(out["stable"])
        self.assertGreater(out["rmse_improvement"], 0.5)
        bad = ml._risk_compare(naive, y, {"zero": [0.0] * len(y)}, 21, 21)
        self.assertFalse(bad["verified"])
        self.assertFalse(bad["stable"])

    def test_risk_compare_good_in_one_half_only_is_not_stable(self):
        rnd = random.Random(4)
        y = [rnd.gauss(0, 1) for _ in range(200)]
        pred = [v + rnd.gauss(0, 0.1) for v in y[:100]] + [0.3 + rnd.gauss(0, 0.3) for _ in y[100:]]
        out = ml._risk_compare(pred, y, {"zero": [0.0] * len(y)}, 21, 21)
        self.assertTrue(out["verified"])
        self.assertFalse(out["stable"])

    def test_risk_compare_uses_common_rows(self):
        y = [float(i % 7) for i in range(60)]
        base = [None] * 30 + [3.0] * 30
        out = ml._risk_compare([v for v in y], y, {"b": base}, 5, 5)
        self.assertEqual(out["model"]["n"], 30)
        self.assertEqual(out["baseline_b"]["n"], 30)

    def test_tail_and_beta_targets_are_point_in_time(self):
        import math
        rnd = random.Random(5)
        n = 700
        mret = [rnd.gauss(0, 0.01) for _ in range(n)]
        beta = [1.0 if i < 400 else 2.0 for i in range(n)]             # the asset's beta doubles at session 400
        market, price = [100.0], [100.0]
        for i in range(1, n):
            market.append(market[-1] * math.exp(mret[i]))
            price.append(price[-1] * math.exp(beta[i] * mret[i] + rnd.gauss(0, 0.002)))
        z = {k: [rnd.gauss(0, 1) for _ in range(n)] for k in ("ret_1m", "vol_20", "z_20", "mom_12_1")}
        ds = ml.asset_dataset(z, price, 21, names=list(z), market=market)
        lr = [None] + [math.log(price[i] / price[i - 1]) for i in range(1, n)]
        for j, t in enumerate(ds["idx"]):
            if ds["tail_target"][j] is not None:
                self.assertAlmostEqual(ds["tail_target"][j], -min(lr[t + 1:t + 22]), places=12)
            if ds["tail_prev"][j] is not None:
                self.assertAlmostEqual(ds["tail_prev"][j], -min(lr[max(1, t - 20):t + 1]), places=12)
        # well after the break the trailing beta is ~2 and the change ~0; just after it the window beta jumps by ~1
        j_after = next(j for j, t in enumerate(ds["idx"]) if 400 <= t + 1 and t + 21 < 500 and t >= 380)
        self.assertGreater(ds["beta_target"][j_after], 0.5)
        j_late = max(j for j, t in enumerate(ds["idx"]) if ds["beta_target"][j] is not None)
        self.assertLess(abs(ds["beta_target"][j_late]), 0.3)

    def test_expected_max_normal(self):
        self.assertAlmostEqual(ml._expected_max_normal(5), 1.1630, places=3)
        self.assertGreater(ml._expected_max_normal(21), ml._expected_max_normal(5))
        self.assertLess(ml._expected_max_normal(21), 2.2)
