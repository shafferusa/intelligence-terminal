"""FinSim2 pure-Python models: correctness, attribution, missing data, persistence, speed."""
import json
import math
import random
import time
import unittest

from finsim2.engine import models as M


def _linear_data(n=800, p=6, seed=0, noise=0.1):
    rng = random.Random(seed)
    X = [[rng.gauss(0, 1) for _ in range(p)] for _ in range(n)]
    coef = [1.5, -2.0, 0.5] + [0.0] * (p - 3)
    y = [0.3 + sum(c * v for c, v in zip(coef, r)) + rng.gauss(0, noise) for r in X]
    return X, y, coef


def _nonlinear_data(n, p=6, seed=0, noise=0.2):
    rng = random.Random(seed)
    X = [[rng.uniform(-2, 2) for _ in range(p)] for _ in range(n)]
    y = [math.sin(r[0]) + r[1] * r[2] + rng.gauss(0, noise) for r in X]
    return X, y


def _mse(a, b):
    return sum((u - v) ** 2 for u, v in zip(a, b)) / len(a)


def _all_models():
    return [
        ("ols", M.OLS(), False), ("ridge", M.Ridge(alpha=0.1), False),
        ("lasso", M.Lasso(alpha=0.01), False), ("elastic_net", M.ElasticNet(alpha=0.01), False),
        ("logistic", M.Logistic(), True), ("decision_tree", M.DecisionTree(), False),
        ("random_forest", M.RandomForest(n_estimators=10), False),
        ("gbm", M.GradientBoosting(n_estimators=30), False),
        ("gbm_logistic", M.GradientBoosting(n_estimators=30, loss="logistic"), True),
    ]


class LinearModels(unittest.TestCase):
    def test_ols_and_ridge_recover_coefficients(self):
        X, y, coef = _linear_data()
        for m in (M.OLS(standardize=False), M.Ridge(alpha=1e-6, standardize=False)):
            m.fit(X, y)
            # standardize=False: coefficients are in raw units, intercept ~0.3
            for got, want in zip(m.coef_, coef):
                self.assertAlmostEqual(got, want, delta=0.02)
            self.assertAlmostEqual(m.intercept_, 0.3, delta=0.02)
        std = M.OLS().fit(X, y)
        raw = [c * s for c, s in zip(coef, std.pre_.stds_)]  # standardised-space coefficients
        for got, want in zip(std.coef_, raw):
            self.assertAlmostEqual(got, want, delta=0.03)

    def test_ridge_shrinks_by_per_sample_alpha(self):
        X, y, _ = _linear_data(n=2000)
        ols = M.OLS().fit(X, y)
        r = M.Ridge(alpha=1.0).fit(X, y)
        # (near-)orthogonal unit-variance features: ridge coef ~ ols / (1 + alpha)
        for a, b in zip(r.coef_[:3], ols.coef_[:3]):
            self.assertAlmostEqual(a, b / 2.0, delta=0.08)

    def test_lasso_zeroes_noise_features(self):
        rng = random.Random(3)
        n, p = 1000, 10
        X = [[rng.gauss(0, 1) for _ in range(p)] for _ in range(n)]
        y = [2 * r[0] - 1 * r[1] + rng.gauss(0, 0.5) for r in X]
        m = M.Lasso(alpha=0.3).fit(X, y)
        self.assertGreater(m.coef_[0], 1.0)
        self.assertLess(m.coef_[1], -0.3)
        self.assertEqual(m.coef_[2:], [0.0] * (p - 2))
        rel = M.Lasso(alpha=0.12, relative_alpha=True).fit(X, y)   # threshold on |corr|
        self.assertEqual(rel.coef_[2:], [0.0] * (p - 2))
        self.assertNotEqual(rel.coef_[0], 0.0)
        en = M.ElasticNet(alpha=0.5, l1_ratio=0.7).fit(X, y)
        self.assertEqual(en.coef_[2:], [0.0] * (p - 2))
        self.assertGreater(en.coef_[0], 0.5)
        self.assertEqual(m.feature_importances_[2:], [0.0] * (p - 2))
        self.assertAlmostEqual(sum(m.feature_importances_), 1.0)

    def test_lasso_matches_ols_when_alpha_zero(self):
        X, y, _ = _linear_data(n=500)
        a = M.Lasso(alpha=0.0, max_iter=2000, tol=1e-10).fit(X, y)
        b = M.OLS().fit(X, y)
        for u, v in zip(a.coef_, b.coef_):
            self.assertAlmostEqual(u, v, delta=1e-5)

    def test_logistic_accuracy(self):
        rng = random.Random(5)
        n, p = 1500, 5
        X = [[rng.gauss(0, 1) for _ in range(p)] for _ in range(n)]
        y = [1.0 if 3 * r[0] - 2 * r[1] + rng.gauss(0, 0.8) > 0 else 0.0 for r in X]
        m = M.Logistic(C=1.0).fit(X[:1000], y[:1000])
        acc = sum(1 for a, b in zip(m.predict(X[1000:]), y[1000:]) if a == b) / 500
        self.assertGreater(acc, 0.85)
        self.assertLess(m.n_iter_, 20)            # Newton converges in a handful of steps
        pr = m.predict_proba(X[1000:])
        self.assertTrue(all(0.0 < v < 1.0 for v in pr))
        self.assertGreater(m.coef_[0], 0)
        self.assertLess(m.coef_[1], 0)
        # stronger regularisation shrinks the coefficients
        s = M.Logistic(C=0.001).fit(X[:1000], y[:1000])
        self.assertLess(abs(s.coef_[0]), abs(m.coef_[0]))

    def test_sample_weights(self):
        X, y, _ = _linear_data(n=400)
        a = M.Ridge(alpha=0.1).fit(X + X, y + y)
        b = M.Ridge(alpha=0.1).fit(X, y, sample_weight=[2.0] * len(y))
        for u, v in zip(a.coef_, b.coef_):
            self.assertAlmostEqual(u, v, places=8)
        # zero weight == row absent (trees too)
        w = [1.0] * 300 + [0.0] * 100
        t1 = M.DecisionTree(min_leaf=10).fit(X[:300], y[:300])
        t2 = M.DecisionTree(min_leaf=10, n_bins=32).fit(X, y, sample_weight=w)
        self.assertEqual(len(t2.predict(X)), 400)
        self.assertAlmostEqual(t2.trees_[0][5][0], t1.trees_[0][5][0], places=9)  # root mean


class TreeModels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        X, y = _nonlinear_data(2400, seed=11)
        cls.Xtr, cls.ytr, cls.Xte, cls.yte = X[:1800], y[:1800], X[1800:], y[1800:]
        mean = sum(cls.ytr) / len(cls.ytr)
        cls.base_mse = _mse([mean] * len(cls.yte), cls.yte)

    def test_trees_beat_mean_out_of_sample(self):
        for m in (M.DecisionTree(max_depth=4), M.RandomForest(), M.GradientBoosting(),
                  M.GradientBoosting(validation_fraction=0.1, n_estimators=300)):
            m.fit(self.Xtr, self.ytr)
            mse = _mse(m.predict(self.Xte), self.yte)
            self.assertLess(mse, 0.8 * self.base_mse, f"{m!r}: {mse:.3f} vs {self.base_mse:.3f}")
            imp = m.feature_importances_
            self.assertAlmostEqual(sum(imp), 1.0)
            self.assertGreater(imp[0] + imp[1] + imp[2], 0.8)   # the informative features

    def test_gbm_early_stopping_uses_last_rows(self):
        m = M.GradientBoosting(n_estimators=400, learning_rate=0.3, validation_fraction=0.1,
                               n_iter_no_change=5).fit(self.Xtr, self.ytr)
        self.assertLess(m.n_estimators_, 400)
        self.assertEqual(len(m.trees_), m.n_estimators_)
        best = min(range(len(m.validation_scores_)), key=m.validation_scores_.__getitem__)
        self.assertEqual(m.n_estimators_, best + 1)

    def test_gbm_regularisation_params(self):
        a = M.GradientBoosting(n_estimators=20, reg_lambda=0.0).fit(self.Xtr, self.ytr)
        b = M.GradientBoosting(n_estimators=20, reg_lambda=1e6).fit(self.Xtr, self.ytr)
        spread = lambda m: max(m.predict(self.Xte)) - min(m.predict(self.Xte))
        self.assertLess(spread(b), 0.01 * spread(a))
        g = M.GradientBoosting(n_estimators=5, gamma=1e9).fit(self.Xtr, self.ytr)
        self.assertTrue(all(t[0] == [-1] for t in g.trees_))   # no split clears gamma

    def test_gbm_logistic_classifier(self):
        yb = [1.0 if v > 0 else 0.0 for v in self.ytr]
        yt = [1.0 if v > 0 else 0.0 for v in self.yte]
        m = M.GradientBoosting(loss="logistic", n_estimators=150, learning_rate=0.1).fit(self.Xtr, yb)
        acc = sum(1 for a, b in zip(m.predict(self.Xte), yt) if a == b) / len(yt)
        self.assertGreater(acc, 0.75)
        pr = m.predict_proba(self.Xte)
        self.assertTrue(all(0 < v < 1 for v in pr))
        self.assertTrue(m.is_classifier)

    def test_splits_respect_min_leaf_and_depth(self):
        m = M.DecisionTree(max_depth=3, min_leaf=100).fit(self.Xtr, self.ytr)
        f, _, thr, left, right, val, _g = m.trees_[0]
        depth = {0: 0}
        for nd in range(len(f)):
            if f[nd] >= 0:
                depth[left[nd]] = depth[right[nd]] = depth[nd] + 1
        self.assertLessEqual(max(depth.values()), 3)
        # every leaf holds >= min_leaf training rows
        counts = {}
        for r in self.Xtr:
            nd = 0
            while f[nd] >= 0:
                nd = left[nd] if r[f[nd]] <= thr[nd] else right[nd]
            counts[nd] = counts.get(nd, 0) + 1
        self.assertTrue(all(c >= 100 for c in counts.values()))
        # leaf values are the training means of their rows
        sums = {}
        for r, t in zip(self.Xtr, self.ytr):
            nd = 0
            while f[nd] >= 0:
                nd = left[nd] if r[f[nd]] <= thr[nd] else right[nd]
            sums[nd] = sums.get(nd, 0.0) + t
        for nd, s in sums.items():
            self.assertAlmostEqual(val[nd], s / counts[nd], places=9)

    def test_forest_max_features_and_seed(self):
        a = M.RandomForest(n_estimators=5, seed=1).fit(self.Xtr, self.ytr).predict(self.Xte)
        b = M.RandomForest(n_estimators=5, seed=1).fit(self.Xtr, self.ytr).predict(self.Xte)
        c = M.RandomForest(n_estimators=5, seed=2).fit(self.Xtr, self.ytr).predict(self.Xte)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_compiled_matches_interpreter(self):
        for m in (M.RandomForest(n_estimators=8), M.GradientBoosting(n_estimators=25)):
            m.fit(self.Xtr, self.ytr)
            X = [list(r) for r in self.Xte[:200]]
            X[0][0] = None
            X[1][1] = float("nan")
            self.assertEqual(m.predict(X), m._interp(X))

    def test_binning_bounds(self):
        rng = random.Random(0)
        X = [[rng.random(), float(rng.randrange(2)), 7.0] for _ in range(500)]
        bn = M._Binned([list(c) for c in zip(*X)], 16)
        self.assertLessEqual(bn.nbins[0], 16)
        self.assertEqual(bn.nbins[1], 2)     # binary feature -> 2 bins
        self.assertEqual(bn.nbins[2], 1)     # constant feature -> never split


class Contributions(unittest.TestCase):
    def test_contributions_sum_to_prediction_every_model(self):
        X, y = _nonlinear_data(700, seed=4)
        X[3][0] = None
        X[10][2] = float("nan")
        yb = [1.0 if v > 0 else 0.0 for v in y]
        for name, m, clf in _all_models():
            m.fit(X, yb if clf else y)
            for row in (X[0], X[3], X[10], [None] * 6, [9.0] * 6):
                c = m.contributions(row)
                self.assertEqual(len(c), 6)
                out = (m.decision_function([row]) if clf else m.predict([row]))[0]
                self.assertAlmostEqual(sum(c) + m.base_value_, out, places=9, msg=name)
            if clf:
                e = m.decision_function([X[0]])[0]
                self.assertAlmostEqual(m.predict_proba([X[0]])[0], 1 / (1 + math.exp(-e)), places=12)
            imp = m.feature_importances_
            self.assertEqual(len(imp), 6)
            self.assertAlmostEqual(sum(imp), 1.0, msg=name)

    def test_saabas_credits_split_features_only(self):
        X, y = _nonlinear_data(1000, seed=6)
        m = M.DecisionTree(max_depth=2, min_leaf=30).fit(X, y)
        used = {j for j in m.trees_[0][0] if j >= 0}
        c = m.contributions(X[0])
        for j in range(6):
            if j not in used:
                self.assertEqual(c[j], 0.0)


class Preprocessing(unittest.TestCase):
    def test_train_statistics_only(self):
        X, y, _ = _linear_data(n=500)
        m = M.Ridge(alpha=0.1).fit(X, y)
        mu, sd = list(m.pre_.means_), list(m.pre_.stds_)
        shifted = [[v + 10.0 for v in r] for r in X]
        p0 = m.predict(X)
        p1 = m.predict(shifted)
        self.assertNotEqual(p0, p1)                           # not re-standardised on predict rows
        self.assertEqual(m.pre_.means_, mu)
        self.assertEqual(m.pre_.stds_, sd)
        # a row's prediction does not depend on the other rows in the batch
        self.assertEqual(m.predict([X[7]])[0], p0[7])
        self.assertEqual(m.predict(X[5:9])[2], p0[7])
        t = M.GradientBoosting(n_estimators=10).fit(X, y)
        rows = [[None] * 6, X[0]]
        self.assertEqual(t.predict(rows)[0], t.predict([[None] * 6])[0])

    def test_clip_and_std_floor(self):
        X = [[float(i), 1.0, None if i % 3 == 0 else float(i % 5)] for i in range(100)]
        X.append([1e9, 1.0, 2.0])
        pre = M.Preprocessor().fit(X)
        Z = pre.transform(X)
        self.assertTrue(all(-5.0 <= v <= 5.0 for r in Z for v in r))
        self.assertEqual(Z[-1][0], 5.0)
        self.assertEqual(pre.stds_[1], 1e-12)                  # constant column
        self.assertTrue(all(r[1] == 0.0 for r in Z))
        self.assertEqual(pre.medians_[2], 2.0)
        self.assertEqual(pre.transform_row([None, None, None])[2], (2.0 - pre.means_[2]) / pre.stds_[2])

    def test_missing_values_everywhere(self):
        X, y = _nonlinear_data(900, seed=8)
        rng = random.Random(9)
        Xm = [[None if rng.random() < 0.15 else v for v in r] for r in X]
        Xm[0] = [None] * 6
        Xm[1][4] = float("inf")
        y[2] = None                      # rows with a missing target are dropped
        yb = [None if v is None else (1.0 if v > 0 else 0.0) for v in y]
        for name, m, clf in _all_models():
            m.fit(Xm, yb if clf else y)
            out = m.predict(Xm)
            self.assertEqual(len(out), len(Xm))
            self.assertTrue(all(math.isfinite(v) for v in out), name)
        km = M.KMeans(k=3).fit(Xm)
        self.assertEqual(len(km.predict(Xm)), len(Xm))
        pc = M.PCA(2).fit(Xm)
        self.assertEqual(len(pc.transform(Xm)[0]), 2)


class Persistence(unittest.TestCase):
    def test_state_round_trip_identical(self):
        X, y = _nonlinear_data(600, seed=12)
        X[5][1] = None
        yb = [1.0 if v > 0 else 0.0 for v in y]
        models = _all_models() + [("pca", M.PCA(3), None), ("kmeans", M.KMeans(k=4), None)]
        for name, m, clf in models:
            if clf is None:
                m.fit(X)
            else:
                m.fit(X, yb if clf else y)
            s = json.loads(json.dumps(M.get_state(m)))
            m2 = M.from_state(s)
            self.assertEqual(type(m2), type(m))
            self.assertEqual(m2.params, m.params)
            if name == "pca":
                self.assertEqual(m2.transform(X), m.transform(X))
                continue
            self.assertEqual(m2.predict(X), m.predict(X), name)
            if clf:
                self.assertEqual(m2.predict_proba(X), m.predict_proba(X))
            if hasattr(m, "contributions"):
                self.assertEqual(m2.contributions(X[5]), m.contributions(X[5]))
                self.assertEqual(m2.base_value_, m.base_value_)
                self.assertEqual(m2.feature_importances_, m.feature_importances_)

    def test_unfitted_state_raises(self):
        with self.assertRaises(RuntimeError):
            M.get_state(M.Ridge())


class Factory(unittest.TestCase):
    def test_make_model_names_and_labels(self):
        X, y, _ = _linear_data(n=300)
        for name in M.MODEL_NAMES:
            m = M.make_model(name)
            self.assertEqual(m.name, name)
            self.assertIn(name, M.MODEL_LABELS)
            yy = [1.0 if v > 0 else 0.0 for v in y] if name == "logistic" else y
            self.assertEqual(len(m.fit(X, yy).predict(X)), 300)
        self.assertEqual(M.MODEL_LABELS["gradient_boosting"], "Gradient boosting (XGBoost-style)")
        gb = M.make_model("gradient_boosting", n_estimators=7, loss="logistic")
        self.assertEqual(gb.params["n_estimators"], 7)
        self.assertEqual(gb.params["max_depth"], 2)
        with self.assertRaises(ValueError):
            M.make_model("xgboost")


class Utilities(unittest.TestCase):
    def test_rank_pearson_spearman(self):
        self.assertEqual(M.rank([10, 20, 20, 5]), [2.0, 3.5, 3.5, 1.0])
        a = [1, 2, 3, 4, 5, None]
        b = [2, 4, 6, 8, 11, 3]
        self.assertAlmostEqual(M.spearman(a, b), 1.0)
        self.assertAlmostEqual(M.pearson([1, 2, 3], [3, 2, 1]), -1.0)
        self.assertAlmostEqual(M.pearson(a, b), 0.99589, places=4)
        self.assertIsNone(M.pearson([1, None], [2, 3]))
        self.assertIsNone(M.spearman([1, 1, 1], [1, 2, 3]))
        x = [math.exp(v / 10) for v in range(50)]
        self.assertAlmostEqual(M.spearman(x, list(range(50))), 1.0)
        self.assertLess(M.pearson(x, list(range(50))), 1.0)

    def test_permutation_importance(self):
        X, y, _ = _linear_data(n=600, p=5)
        m = M.OLS().fit(X, y)
        r2 = lambda t, p: 1 - _mse(t, p) / _mse(t, [sum(t) / len(t)] * len(t))
        imp = M.permutation_importance(m, X, y, r2, n_repeats=2)
        self.assertGreater(imp[1], imp[0])     # |coef| 2.0 > 1.5
        self.assertGreater(imp[0], imp[2])
        self.assertLess(abs(imp[3]), 0.01)
        self.assertEqual(X[0], _linear_data(n=600, p=5)[0][0])   # X untouched

    def test_pca(self):
        rng = random.Random(1)
        X = []
        for _ in range(800):
            f = rng.gauss(0, 1)
            X.append([f + rng.gauss(0, 0.1), f + rng.gauss(0, 0.1), -f + rng.gauss(0, 0.1), rng.gauss(0, 1)])
        pc = M.PCA().fit(X)
        self.assertAlmostEqual(sum(pc.explained_variance_ratio_), 1.0)
        self.assertGreater(pc.explained_variance_ratio_[0], 0.7)
        self.assertEqual(pc.explained_variance_ratio_, sorted(pc.explained_variance_ratio_, reverse=True))
        v = pc.components_[0]
        self.assertAlmostEqual(sum(a * a for a in v), 1.0)
        self.assertAlmostEqual(abs(v[3]), 0.0, delta=0.05)
        for i in range(4):   # orthonormal
            for j in range(i + 1, 4):
                self.assertAlmostEqual(sum(a * b for a, b in zip(pc.components_[i], pc.components_[j])), 0.0)
        T = pc.transform(X)
        var0 = sum(r[0] ** 2 for r in T) / (len(T) - 1)
        self.assertAlmostEqual(var0, pc.explained_variance_[0], places=6)

    def test_kmeans(self):
        rng = random.Random(2)
        X = [[rng.gauss(cx, 0.3), rng.gauss(cy, 0.3)] for cx, cy in ((0, 0), (5, 5), (0, 5)) for _ in range(100)]
        km = M.KMeans(k=3, seed=0).fit(X)
        labels = km.labels_
        for g in range(3):
            self.assertEqual(len(set(labels[g * 100:(g + 1) * 100])), 1)
        self.assertEqual(len(set(labels)), 3)
        self.assertEqual(km.predict(X), labels)
        d = km.transform(X[:2])
        self.assertEqual(len(d[0]), 3)


class Speed(unittest.TestCase):
    def test_timing_3000_by_40(self):
        rng = random.Random(0)
        n, p = 3000, 40
        X = [[rng.gauss(0, 1) for _ in range(p)] for _ in range(n)]
        y = [math.sin(r[0]) + r[1] * r[2] + rng.gauss(0, 0.5) for r in X]
        yb = [1.0 if v > 0 else 0.0 for v in y]
        timings = {}
        for label, m, target in (
            ("RandomForest(40 trees)", M.RandomForest(n_estimators=40), y),
            ("GradientBoosting(100 rounds)", M.GradientBoosting(n_estimators=100), y),
            ("GradientBoosting(100, logistic)", M.GradientBoosting(n_estimators=100, loss="logistic"), yb),
            ("Ridge", M.Ridge(), y),
            ("Lasso", M.Lasso(), y),
            ("Logistic", M.Logistic(), yb),
        ):
            t0 = time.perf_counter()
            m.fit(X, target)
            m.predict(X)
            timings[label] = time.perf_counter() - t0
        print("\n[fs2 models] fit+predict on 3000 x 40:")
        for k, v in timings.items():
            print(f"  {k:34s} {v:6.3f}s")
        self.assertLess(timings["RandomForest(40 trees)"], 5.0)
        self.assertLess(timings["GradientBoosting(100 rounds)"], 5.0)


if __name__ == "__main__":
    unittest.main()
