"""engine/alphahz.py: Shaffer Alpha 1M–5Y with horizon / sector / stock pooling — capability on planted effects."""
import random
import unittest

from finsim2.engine import alphahz as A


def _recs(n_dates=120, n_stocks=60, p=3, seed=1, sector_sign=True):
    """Two sectors; feature 0 predicts the outcome with + sign in 'Tech' and − sign in 'Energy' (sector_sign), feature
    1 predicts + everywhere, feature 2 is noise."""
    rng = random.Random(seed)
    data = {lab: [] for lab, _ in A.HORIZONS}
    for lab, h in A.HORIZONS[:2]:
        for k in range(n_dates):
            y0 = 1995 + k // 12
            d = f"{y0}-{k % 12 + 1:02d}-15"
            end = f"{y0 + (1 if h >= 252 else 0)}-{k % 12 + 1:02d}-{20 if h < 252 else 15}"
            for s in range(n_stocks):
                sec = "Tech" if s % 2 == 0 else "Energy"
                x = [rng.gauss(0, 1) for _ in range(p)]
                b0 = 0.5 if (sec == "Tech" or not sector_sign) else -0.5
                y = b0 * x[0] + 0.3 * x[1] + rng.gauss(0, 1)
                r = A.R()
                r.a, r.d, r.end, r.raw, r.y, r.yr, r.x = f"S{s}", d, end, x[2], y, y / 10, x
                r.sector, r.orig = sec, s < 30
                data[lab].append(r)
    for lab in data:
        A._prepare(data[lab])
    return data


class Pooling(unittest.TestCase):
    def test_sector_weights_recover_a_planted_sector_sign(self):
        data = _recs()
        leaves = A.leaf_stats(data, 3, "2100-01-01")
        W, rms = A.fit(leaves, 3, 100.0, 4)
        tech, energy = W["h:1M|s:Tech"], W["h:1M|s:Energy"]
        self.assertGreater(tech[0], 0.1)
        self.assertLess(energy[0], -0.1)
        self.assertGreater(tech[1], 0.05)
        self.assertGreater(energy[1], 0.05)
        self.assertLess(abs(W["h:1M"][0]), abs(tech[0]), "the horizon level averages the opposite signs away")

    def test_heavy_shrinkage_pulls_children_to_the_parent(self):
        data = _recs()
        leaves = A.leaf_stats(data, 3, "2100-01-01")
        W, _ = A.fit(leaves, 3, 1e9, 4)
        for i in range(3):
            self.assertAlmostEqual(W["h:1M|s:Tech"][i], W["h:1M"][i], places=4)

    def test_sector_model_ranks_better_out_of_sample_when_the_effect_is_sector_specific(self):
        data = _recs(n_dates=144)
        leaves = A.leaf_stats(data, 3, "2001-01-01")
        test = [r for r in data["1M"] if r.d >= "2001-01-01"]
        ic = {}
        for depth in (3, 4):
            W, rms = A.fit(leaves, 3, 100.0, depth)
            ics = A.date_ics([(r, A.score(W, rms, "1M", r, depth)) for r in test])
            ic[depth] = sum(ics.values()) / len(ics)
        self.assertGreater(ic[4], ic[3] + 0.05)

    def test_no_planted_sector_effect_no_sector_gain(self):
        data = _recs(n_dates=144, sector_sign=False, seed=3)
        leaves = A.leaf_stats(data, 3, "2001-01-01")
        test = [r for r in data["1M"] if r.d >= "2001-01-01"]
        ic = {}
        for depth in (3, 4):
            W, rms = A.fit(leaves, 3, 1000.0, depth)
            ics = A.date_ics([(r, A.score(W, rms, "1M", r, depth)) for r in test])
            ic[depth] = sum(ics.values()) / len(ics)
        self.assertLess(abs(ic[4] - ic[3]), 0.02)


class PointInTime(unittest.TestCase):
    def test_training_uses_only_matured_outcomes(self):
        data = _recs()
        cut = "2000-01-01"
        leaves = A.leaf_stats(data, 3, cut)
        n = sum(s.w for (lab, _, _), s in leaves.items() if lab == "1M")
        self.assertAlmostEqual(n, sum(1 for r in data["1M"] if r.end < cut))
        n3 = sum(s.w for (lab, _, _), s in leaves.items() if lab == "3M") / (21 / 63)
        self.assertAlmostEqual(n3, sum(1 for r in data["3M"] if r.end < cut), places=6)


class Magnitude(unittest.TestCase):
    def test_deciles_order_and_interval(self):
        data = _recs()
        rows = [(r, r.x[1]) for r in data["1M"]]
        cal = A._calibrate(rows)
        self.assertEqual(len(cal), 10)
        self.assertLess(cal[0]["mean"], cal[9]["mean"])
        self.assertTrue(all(c["q05"] <= c["mean"] <= c["q95"] for c in cal))
        self.assertLess(cal[0]["p_beat"], cal[9]["p_beat"])

    def test_spearman_and_paths(self):
        self.assertAlmostEqual(A._spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)
        self.assertAlmostEqual(A._spearman([1, 2, 3, 4], [4, 3, 2, 1]), -1.0)
        self.assertEqual(A.path("60M", "Energy", "XOM", 5), ["global", "g:long", "h:60M", "h:60M|s:Energy", "h:60M|s:Energy|a:XOM"])
        self.assertEqual(A.path("1M", "Energy", "XOM", 3), ["global", "g:short", "h:1M"])
        self.assertEqual(A.BASE_LAB["60M"], "12M")

    def test_extra_features_only_from_passed_alpha_families(self):
        b2 = {"horizons": {"1M": {"families": {
            "insider": {"gates": {"track": "historical", "alpha": {"passed": True}}},
            "sec_events": {"gates": {"track": "historical", "alpha": {"passed": False}, "hedge": {"passed": True}}},
            "crypto_derivs": {"gates": {"track": "limited", "alpha": {"passed": True}}}}}}}
        ex = A.passed_extra(b2)
        self.assertTrue(ex and all(f == "insider" for f, _ in ex))


if __name__ == "__main__":
    unittest.main()
