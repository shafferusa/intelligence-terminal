"""Quantitative equation library: known values, round-trips and robustness of asset metrics."""
import importlib
import json
import math
import random
import unittest

from finsim.quant import credit, derivatives as dv, fixed_income as fi, linalg, ml, nn, portfolio as pf
from finsim.quant import regression as reg, stats, stochastic as sp, swaps, timeseries as ts, volatility as vol
from finsim.quant.asset_metrics import METRIC_INFO, asset_metrics, score_table
from finsim.quant.catalog import EQUATIONS, SECTIONS, catalog, source


def gbm_closes(n, seed, mu=0.0003, sigma=0.015, s0=100.0):
    rng = random.Random(seed)
    p = [s0]
    for _ in range(n - 1):
        p.append(p[-1] * math.exp(rng.gauss(mu, sigma)))
    return p


class TestStats(unittest.TestCase):
    def test_normal(self):
        self.assertAlmostEqual(stats.norm_ppf(0.975), 1.959963984540054, places=8)
        self.assertAlmostEqual(stats.norm_ppf(0.01), -2.3263478740408408, places=8)
        self.assertAlmostEqual(stats.norm_cdf(stats.norm_ppf(1e-6)), 1e-6, places=12)
        self.assertAlmostEqual(stats.norm_cdf(0.0), 0.5)

    def test_returns_and_moments(self):
        self.assertAlmostEqual(stats.simple_return(105, 100, 1), 0.06)
        self.assertAlmostEqual(stats.log_return(110, 100), math.log(1.1))
        self.assertAlmostEqual(stats.cumulative_return([0.1, -0.1]), -0.01)
        self.assertAlmostEqual(stats.annualized_return([0.01] * 12, 12), 1.01 ** 12 - 1)
        x = [1.0, 2.0, 3.0, 4.0]
        self.assertAlmostEqual(stats.variance(x), 5 / 3)
        self.assertAlmostEqual(stats.correlation(x, [2 * v + 1 for v in x]), 1.0)
        self.assertAlmostEqual(stats.skewness(x), 0.0)
        self.assertAlmostEqual(stats.expected_value([1, 2, 3], [0.2, 0.3, 0.5]), 2.3)
        grid = [-8 + 0.01 * i for i in range(1601)]
        self.assertAlmostEqual(stats.expected_value_pdf(grid, stats.norm_pdf, lambda v: v * v), 1.0, places=6)
        self.assertEqual(stats.conditional_expectation([1, 3, 10, 20], ["a", "a", "b", "b"]), {"a": 2.0, "b": 15.0})
        self.assertAlmostEqual(stats.bayes(0.9, 0.01, 0.9 * 0.01 + 0.1 * 0.99), 0.009 / 0.108)
        self.assertGreater(stats.sortino_ratio([0.02, -0.01, 0.03, -0.02, 0.01]), 0)


class TestLinalg(unittest.TestCase):
    def test_solve_inverse_eigen(self):
        A = [[4.0, 1.0, 2.0], [1.0, 3.0, 0.5], [2.0, 0.5, 5.0]]
        inv = linalg.inverse(A)
        I = linalg.matmul(A, inv)
        for i in range(3):
            for j in range(3):
                self.assertAlmostEqual(I[i][j], 1.0 if i == j else 0.0, places=10)
        vals, vecs = linalg.eigh_sym(A)
        for lam, v in zip(vals, vecs):
            Av = linalg.matvec(A, v)
            for a, b in zip(Av, v):
                self.assertAlmostEqual(a, lam * b, places=9)
        lam, _ = linalg.power_iteration(A)
        self.assertAlmostEqual(lam, vals[0], places=6)


class TestRegression(unittest.TestCase):
    def setUp(self):
        rng = random.Random(3)
        self.x1 = [rng.gauss(0, 1) for _ in range(400)]
        self.x2 = [rng.gauss(0, 1) for _ in range(400)]
        self.x3 = [rng.gauss(0, 1) for _ in range(400)]
        self.y = [1.5 + 2.0 * a - 0.7 * b + rng.gauss(0, 0.1) for a, b in zip(self.x1, self.x2)]
        self.X = [[a, b, c] for a, b, c in zip(self.x1, self.x2, self.x3)]

    def test_ols_recovers(self):
        fit = reg.ols_fit(reg.add_constant(self.X), self.y)
        for est, true in zip(fit["beta"], [1.5, 2.0, -0.7, 0.0]):
            self.assertAlmostEqual(est, true, delta=0.03)
        self.assertGreater(fit["r2"], 0.99)
        self.assertGreater(abs(fit["t"][1]), 100)
        self.assertLess(abs(fit["t"][3]), 4)

    def test_ridge_zero_equals_ols(self):
        b = reg.ols(reg.add_constant(self.X), self.y)
        icpt, coefs = ml.ridge(self.X, self.y, lam=0.0)
        self.assertAlmostEqual(icpt, b[0], places=9)
        for a, c in zip(coefs, b[1:]):
            self.assertAlmostEqual(a, c, places=9)

    def test_lasso_zeroes_irrelevant(self):
        _, coefs = ml.lasso(self.X, self.y, lam=0.05)
        self.assertEqual(coefs[2], 0.0)
        self.assertGreater(coefs[0], 1.8)
        _, en = ml.elastic_net(self.X, self.y, lam=0.05, l1_ratio=0.5)
        self.assertEqual(en[2], 0.0)

    def test_interaction_and_loglinear(self):
        y = [1 + a + 2 * b + 3 * a * b for a, b in zip(self.x1, self.x2)]
        for est, true in zip(reg.interaction_model(self.x1, self.x2, y), [1, 1, 2, 3]):
            self.assertAlmostEqual(est, true, places=8)
        yl = [math.exp(0.5 + 0.2 * a) for a in self.x1]
        b = reg.log_linear_regression(reg.add_constant(self.x1), yl)
        self.assertAlmostEqual(b[1], 0.2, places=9)


class TestTimeSeries(unittest.TestCase):
    def test_adf(self):
        rng = random.Random(11)
        noise = [rng.gauss(0, 1) for _ in range(500)]
        walk = [0.0]
        for e in noise:
            walk.append(walk[-1] + e)
        self.assertFalse(ts.adf_test(walk)["stationary"])
        self.assertTrue(ts.adf_test(noise)["stationary"])
        self.assertAlmostEqual(ts.adf_test(noise, trend="c")["crit_5"], -2.86)

    def test_ar_ma_arma(self):
        x = ts.simulate_arma(3000, 0.1, (0.6,), (), seed=5)
        c, phi, _ = ts.fit_ar1(x)
        self.assertAlmostEqual(phi, 0.6, delta=0.05)
        self.assertAlmostEqual(ts.fit_ar(x, 2)["phi"][0], 0.6, delta=0.06)
        m = ts.simulate_ma(3000, 0.0, (0.5,), seed=6)
        self.assertAlmostEqual(ts.fit_ma(m, 1)["theta"][0], 0.5, delta=0.06)
        a = ts.simulate_arma(3000, 0.0, (0.5,), (0.3,), seed=7)
        r = ts.fit_arma(a, 1, 1)
        self.assertAlmostEqual(r["phi"][0], 0.5, delta=0.1)
        self.assertAlmostEqual(r["theta"][0], 0.3, delta=0.1)
        self.assertEqual(ts.diff_n([1, 4, 9, 16], 2), [2, 2])
        self.assertEqual(ts.fit_arima([float(i * i) for i in range(50)], 1, 1, 0)["d"], 1)
        self.assertAlmostEqual(ts.autocorrelation(x, 1), 0.6, delta=0.05)
        self.assertTrue(ts.stationarity_check(x)["stationary"])


class TestVolatility(unittest.TestCase):
    def test_garch_fit_persistence(self):
        r = vol.simulate_garch(3000, 2e-6, 0.08, 0.90, seed=21)
        fit = vol.garch_fit(r, fast=False)
        self.assertAlmostEqual(fit["persistence"], 0.98, delta=0.04)
        fast = vol.garch_fit(r, fast=True)
        self.assertAlmostEqual(fast["persistence"], 0.98, delta=0.06)

    def test_recursions(self):
        r = [0.01, -0.02, 0.015]
        self.assertEqual(len(vol.ewma_variance(r)), 4)
        g = vol.garch_variance(r, 1e-6, 0.1, 0.8, h0=1e-4)
        self.assertAlmostEqual(g[1], 1e-6 + 0.1 * 1e-4 + 0.8 * 1e-4)
        gjr = vol.gjr_garch_variance(r, 1e-6, 0.05, 0.1, 0.8, h0=1e-4)
        self.assertAlmostEqual(gjr[2], 1e-6 + 0.15 * 4e-4 + 0.8 * gjr[1])
        self.assertAlmostEqual(vol.realized_variance(r), 0.0001 + 0.0004 + 0.000225)


class TestStochastic(unittest.TestCase):
    def test_processes(self):
        self.assertEqual(sp.gbm_path(100, 0.05, 0.2, 10, 1 / 252, seed=1), sp.gbm_path(100, 0.05, 0.2, 10, 1 / 252, seed=1))
        drift, diff = sp.ito_lemma(0.0, 1 / 100, -1 / 100 ** 2, 0.05 * 100, 0.2 * 100)
        self.assertAlmostEqual(drift, 0.05 - 0.02)
        self.assertAlmostEqual(diff, 0.2)
        self.assertAlmostEqual(sp.half_life(sp.ar1_to_kappa(0.5)), 1.0)
        paths = [sp.ou_path(1.0, 2.0, 0.0, 0.3, 50, 0.01, seed=s)[-1] for s in range(400)]
        self.assertAlmostEqual(sum(paths) / 400, sp.ou_expected(1.0, 2.0, 0.0, 0.5), delta=0.03)
        self.assertLess(sp.vasicek_zcb_price(0.03, 0.5, 0.04, 0.01, 5), math.exp(-0.03 * 5))
        self.assertTrue(all(v >= -1 for v in sp.cir_path(0.02, 1.0, 0.03, 0.2, 200, 0.01, seed=2)))
        s, v = sp.heston_paths(100, 0.04, 0.05, 2.0, 0.04, 0.3, -0.7, 100, 1 / 252, seed=3)
        self.assertEqual(len(s), 101)
        self.assertEqual(sp.heston_price_path(100, 0.04, 0.05, 2.0, 0.04, 0.3, -0.7, 100, 1 / 252, seed=3), s)


class TestML(unittest.TestCase):
    def test_kmeans(self):
        rng = random.Random(4)
        pts = [[rng.gauss(0, 0.3), rng.gauss(0, 0.3)] for _ in range(50)] + \
              [[rng.gauss(5, 0.3), rng.gauss(5, 0.3)] for _ in range(50)]
        _, labels, _ = ml.kmeans(pts, 2, seed=1)
        self.assertEqual(len(set(labels[:50])), 1)
        self.assertEqual(len(set(labels[50:])), 1)
        self.assertNotEqual(labels[0], labels[50])

    def test_pca(self):
        vals, vecs, ratio = ml.pca([[2.0, 1.0], [1.0, 2.0]])
        self.assertAlmostEqual(vals[0], 3.0)
        self.assertAlmostEqual(vals[1], 1.0)
        self.assertAlmostEqual(abs(vecs[0][0]), 1 / math.sqrt(2))
        self.assertAlmostEqual(ratio[0], 0.75)
        z = ml.pca_project([[1.0, 1.0], [-1.0, -1.0]], vecs[:1])
        self.assertAlmostEqual(z[0][0], math.sqrt(2))

    def test_classifiers_and_trees(self):
        self.assertAlmostEqual(ml.sigmoid(-1000), 0.0)
        self.assertAlmostEqual(sum(ml.softmax([1000, 1001, 999])), 1.0)
        X = [[i / 10.0] for i in range(-20, 21)]
        y = [1.0 if r[0] > 0 else 0.0 for r in X]
        w, b = ml.logistic_fit(X, y, lr=1.0, epochs=500)
        self.assertGreater(ml.logistic_predict_proba([1.0], w, b), 0.9)
        self.assertEqual(ml.best_split(X, y)[0], 0)
        self.assertAlmostEqual(ml.best_split(X, y)[1], 0.05)
        yr = [3.0 if r[0] > 0.5 else -1.0 for r in X]
        gb = ml.gradient_boosting_fit(X, yr, 60, 0.3)
        self.assertAlmostEqual(ml.gradient_boosting_predict(gb, [1.5]), 3.0, delta=0.05)
        rf = ml.random_forest_fit(X, yr, n_trees=15, seed=2)
        self.assertEqual(rf, ml.random_forest_fit(X, yr, n_trees=15, seed=2))
        self.assertGreater(ml.random_forest_predict(rf, [1.8]), 2.0)
        self.assertAlmostEqual(ml.xgb_objective([1.0], [1.0], [[0.5, -0.5]], gamma=1.0, lam=2.0), 2.5)
        self.assertEqual(ml.hinge_loss(1, 2.0), 0.0)


class TestNN(unittest.TestCase):
    def test_xor(self):
        X = [[0.0, 0.0], [0.0, 1.0], [1.0, 0.0], [1.0, 1.0]]
        y = [0.0, 1.0, 1.0, 0.0]
        m = nn.train_mlp(X, y, hidden=8, epochs=1500, lr=0.05, seed=1)
        h = m["loss_history"]
        self.assertLess(h[-1], 0.1 * h[0])
        for x, t in zip(X, y):
            self.assertEqual(round(nn.mlp_predict(m, x)), t)

    def test_regression_and_pieces(self):
        X = [[i / 10.0] for i in range(-10, 11)]
        y = [v[0] ** 2 for v in X]
        m = nn.train_mlp(X, y, hidden=10, task="regression", epochs=800, lr=0.03, seed=0)
        self.assertLess(m["loss_history"][-1], 0.2 * m["loss_history"][0])
        self.assertEqual(nn.softmax_ce_grad([0.7, 0.3], [1, 0])[0], 0.7 - 1)
        f = lambda v: math.sin(v * v)  # noqa: E731
        self.assertAlmostEqual(nn.numeric_derivative(f, 0.7), nn.chain_rule(math.cos(0.49), 1.4), places=6)
        m1 = nn.adam_first_moment([0.0], [0.1])
        v1 = nn.adam_second_moment([0.0], [0.1])
        th = nn.adam_update([1.0], m1, v1, 1, lr=0.1)
        self.assertAlmostEqual(th[0], 0.9, places=6)  # first Adam step moves by lr


class TestPortfolio(unittest.TestCase):
    mu = [0.08, 0.10, 0.12]
    cov = [[0.04, 0.006, 0.01], [0.006, 0.09, 0.012], [0.01, 0.012, 0.16]]

    def test_optimizers(self):
        w = pf.mean_variance_weights_budget(self.mu, self.cov, 3.0)
        self.assertTrue(pf.is_fully_invested(w))
        g = pf.gmv_weights(self.cov)
        self.assertTrue(pf.is_fully_invested(g))
        f = pf.frontier_weights(self.mu, self.cov, 0.11)
        self.assertAlmostEqual(sum(a * b for a, b in zip(f, self.mu)), 0.11)
        self.assertTrue(pf.is_fully_invested(f))
        self.assertLessEqual(pf.portfolio_variance(g, self.cov), pf.portfolio_variance(f, self.cov))
        rc = pf.risk_contributions(g, self.cov)
        self.assertAlmostEqual(sum(rc), math.sqrt(pf.portfolio_variance(g, self.cov)))
        wu = pf.mean_variance_weights(self.mu, self.cov, 3.0)
        self.assertAlmostEqual(linalg.matvec(self.cov, wu)[0] * 3.0, self.mu[0])

    def test_risk(self):
        self.assertAlmostEqual(pf.parametric_var(0.0, 1.0, 0.95), 1.6448536269514722, places=8)
        self.assertAlmostEqual(pf.expected_shortfall(0.0, 1.0, 0.975), 2.3378, places=4)
        self.assertAlmostEqual(pf.capm_expected_return(0.04, 1.2, 0.09), 0.10)
        self.assertAlmostEqual(pf.jensens_alpha(0.12, 0.04, 1.2, 0.09), 0.02)


class TestFixedIncome(unittest.TestCase):
    def test_bonds(self):
        self.assertAlmostEqual(fi.bond_price(100, 0.05, 0.05, 10, 2), 100.0, places=9)
        t, cf = fi.bond_cash_flows(100, 0.06, 7, 2)
        p = fi.bond_price_cf(cf, t, 0.045, 2)
        self.assertAlmostEqual(fi.ytm(p, cf, t, 2), 0.045, places=10)
        self.assertAlmostEqual(fi.modified_duration([100], [5], 0.04), 5 / 1.04)
        self.assertAlmostEqual(fi.macaulay_duration([100], [5], 0.04), 5.0)
        c = fi.convexity(cf, t, 0.045, 2)
        d = fi.modified_duration(cf, t, 0.045, 2)
        exact = fi.bond_price_cf(cf, t, 0.055, 2) - p
        self.assertAlmostEqual(fi.price_change_approx(p, d, c, 0.01), exact, delta=0.02)
        self.assertAlmostEqual(fi.dv01(p, d), (fi.bond_price_cf(cf, t, 0.0449, 2) - fi.bond_price_cf(cf, t, 0.0451, 2)) / 2, places=5)
        self.assertAlmostEqual(fi.zero_coupon_price(0.05, 2, continuous=True), math.exp(-0.1))
        self.assertAlmostEqual(fi.forward_rate(0.02, 1, 0.03, 2), 0.04)
        self.assertAlmostEqual(fi.forward_rate(0.02, 1, 0.03, 2, continuous=False), 1.03 ** 2 / 1.02 - 1)


class TestDerivatives(unittest.TestCase):
    def test_bsm(self):
        c = dv.bsm_call(100, 100, 0.05, 0.2, 1.0)
        p = dv.bsm_put(100, 100, 0.05, 0.2, 1.0)
        self.assertAlmostEqual(c, 10.4506, places=4)
        self.assertAlmostEqual(p, 5.5735, places=4)
        self.assertAlmostEqual(dv.parity_gap(c, p, 100, 100, 0.05, 1.0), 0.0, places=10)
        cq = dv.bsm_call(100, 95, 0.03, 0.25, 0.5, q=0.02)
        pq = dv.bsm_put(100, 95, 0.03, 0.25, 0.5, q=0.02)
        self.assertAlmostEqual(dv.parity_put_from_call(cq, 100, 95, 0.03, 0.5, q=0.02), pq, places=10)
        self.assertAlmostEqual(dv.implied_vol(c, 100, 100, 0.05, 1.0), 0.2, places=8)
        self.assertAlmostEqual(dv.implied_vol(pq, 100, 95, 0.03, 0.5, q=0.02, kind="put"), 0.25, places=8)

    def test_greeks(self):
        args = (100, 100, 0.05, 0.2, 1.0)
        h = 1e-4
        self.assertAlmostEqual(dv.call_delta(*args), (dv.bsm_call(100 + h, *args[1:]) - dv.bsm_call(100 - h, *args[1:])) / (2 * h), places=6)
        self.assertAlmostEqual(dv.call_delta(*args) - dv.put_delta(*args), 1.0)
        self.assertAlmostEqual(dv.vega(*args), (dv.bsm_call(100, 100, 0.05, 0.2 + h, 1) - dv.bsm_call(100, 100, 0.05, 0.2 - h, 1)) / (2 * h), places=5)
        self.assertAlmostEqual(dv.call_rho(*args), (dv.bsm_call(100, 100, 0.05 + h, 0.2, 1) - dv.bsm_call(100, 100, 0.05 - h, 0.2, 1)) / (2 * h), places=5)
        self.assertAlmostEqual(dv.put_rho(*args), (dv.bsm_put(100, 100, 0.05 + h, 0.2, 1) - dv.bsm_put(100, 100, 0.05 - h, 0.2, 1)) / (2 * h), places=5)
        self.assertAlmostEqual(dv.gamma(*args), (dv.call_delta(100 + h, *args[1:]) - dv.call_delta(100 - h, *args[1:])) / (2 * h), places=6)
        self.assertAlmostEqual(dv.theta(*args), -(dv.bsm_call(100, 100, 0.05, 0.2, 1 + h) - dv.bsm_call(100, 100, 0.05, 0.2, 1 - h)) / (2 * h), places=5)

    def test_forwards(self):
        self.assertAlmostEqual(dv.forward_price_dividend(100, 0.05, 0.05, 2), 100)
        self.assertAlmostEqual(dv.fx_forward(1.1, 0.03, 0.03, 1), 1.1)
        self.assertEqual(dv.futures_pnl_long(2, 50, 4000, 4010), 1000)
        self.assertEqual(dv.futures_pnl_short(2, 50, 4000, 4010), -1000)
        self.assertEqual(dv.long_call(110, 100, 4)["profit"], 6)
        self.assertEqual(dv.long_put(110, 100, 4)["profit"], -4)


class TestSwapsCredit(unittest.TestCase):
    def test_irs_par(self):
        acc = [0.5] * 10
        dfs = [math.exp(-0.03 * 0.5 * (i + 1)) for i in range(10)]
        L = swaps.forward_rates_from_dfs(dfs, acc)
        k = swaps.par_swap_rate(L, acc, dfs)
        self.assertAlmostEqual(swaps.irs_pv(1e6, L, k, acc, dfs), 0.0, places=6)
        self.assertAlmostEqual(k, (1 - dfs[-1]) / sum(a * d for a, d in zip(acc, dfs)), places=12)
        self.assertAlmostEqual(swaps.swap_pv(swaps.floating_leg_pv(1e6, L, acc, dfs), swaps.fixed_leg_pv(1e6, k, acc, dfs)), 0.0, places=6)
        self.assertAlmostEqual(swaps.trs_net_cash_flow(1e6, 100, 105, 1, 0.04, 0.01, 0.25), 60000 - 12500)

    def test_cds(self):
        lam, R = 0.02, 0.4
        _, acc, dfs, q = credit.cds_schedule(lam, 0.03, 5, 4)
        s = credit.par_cds_spread(R, acc, dfs, q)
        self.assertAlmostEqual(s, credit.credit_triangle_spread(lam, R), delta=0.0002)
        prot = credit.cds_protection_leg_pv(1e7, R, dfs, q)
        self.assertAlmostEqual(prot, credit.cds_premium_leg_pv(1e7, s, acc, dfs, q), places=4)
        self.assertAlmostEqual(credit.bootstrap_flat_hazard(s, R, 5, 0.03, 4), lam, places=9)
        self.assertAlmostEqual(credit.expected_loss(0.02, credit.loss_given_default(0.4), 1e6), 12000)
        self.assertAlmostEqual(credit.survival_probability(0.1, 2) + credit.default_probability(0.1, 2), 1.0)


class TestCatalog(unittest.TestCase):
    def test_every_fn_imports(self):
        for e in EQUATIONS:
            mod, _, name = e["fn"].rpartition(".")
            self.assertTrue(callable(getattr(importlib.import_module(mod), name)), e["fn"])
            self.assertTrue(source(e["fn"]).lstrip().startswith("def "))

    def test_ids_and_structure(self):
        ids = [e["id"] for e in EQUATIONS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual({str(i) for i in range(1, 119)}, {i for i in ids if i.isdigit()})
        keys = {k for k, _ in SECTIONS}
        self.assertEqual(len(SECTIONS), 13)
        for e in EQUATIONS:
            self.assertIn(e["section"], keys)
            self.assertTrue(e["latex"] and e["note"] and e["name"])
            self.assertEqual(set(e), {"id", "section", "name", "latex", "note", "fn", "metric"})
            if e["metric"] is not None:
                self.assertIn(e["metric"], METRIC_INFO)
                self.assertIn(e["id"], METRIC_INFO[e["metric"]]["equations"])
        self.assertIn("Equation 101: mean-variance optimization", next(e for e in EQUATIONS if e["id"] == "101")["note"])
        by_id = {e["id"]: e for e in EQUATIONS}
        for key, info in METRIC_INFO.items():
            self.assertIn(info["fmt"], {"pct", "num", "ratio", "days", "bool"})
            for i in info["equations"]:
                self.assertIn(i, by_id, key)
        json.dumps(catalog())


class TestAssetMetrics(unittest.TestCase):
    def test_degenerate_inputs(self):
        for closes in ([], None, [100.0], [100.0] * 300, [1.0, 2.0, 3.0], [100, -1, 0, float("nan")],
                       ["x", 5], [100.0] * 10):
            m = asset_metrics(closes, market_closes=[100.0] * 300)
            self.assertEqual(set(m), set(METRIC_INFO))
            for key in ("vol_ann", "sharpe", "beta", "garch_vol", "adf_t", "var_95", "skew", "half_life"):
                self.assertIsNone(m[key], (closes, key))
            json.dumps(m, allow_nan=False)

    def test_values(self):
        mkt = gbm_closes(500, 1)
        rng = random.Random(9)
        asset = [100.0]
        for i in range(1, 500):
            rm = mkt[i] / mkt[i - 1] - 1
            asset.append(asset[-1] * (1 + 1.3 * rm + rng.gauss(0, 0.005)))
        m = asset_metrics(asset, mkt, rf=0.03)
        self.assertAlmostEqual(m["beta"], 1.3, delta=0.08)
        self.assertGreater(m["corr_mkt"], 0.9)
        self.assertAlmostEqual(m["r2_mkt"], m["corr_mkt"] ** 2)
        self.assertEqual(m["n"], 500)
        R = stats.simple_returns(asset)
        self.assertAlmostEqual(m["vol_ann"], stats.stdev(R) * math.sqrt(252))
        self.assertAlmostEqual(m["sharpe"], stats.sharpe_ratio(R, 0.03 / 252, 252))
        self.assertAlmostEqual(m["ret_1y"], asset[-1] / asset[-253] - 1)
        self.assertAlmostEqual(m["mom_12_1"], asset[-22] / asset[-253] - 1)
        self.assertAlmostEqual(m["skew"], stats.skewness(R))
        self.assertGreater(m["es_95"], m["var_95"])
        self.assertLessEqual(m["max_drawdown"], 0)
        self.assertIsInstance(m["adf_stationary"], bool)
        # short market history: market metrics None, own metrics present
        m2 = asset_metrics(asset, mkt[-20:])
        self.assertIsNone(m2["beta"])
        self.assertIsNotNone(m2["vol_ann"])
        table = score_table({"A": asset, "B": []}, mkt, 0.03)
        self.assertEqual(table["A"]["beta"], m["beta"])
        self.assertIsNone(table["B"]["vol_ann"])


if __name__ == "__main__":
    unittest.main()
