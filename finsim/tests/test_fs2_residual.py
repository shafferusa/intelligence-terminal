"""Residual / meta-learning program (engine/residual.py, hedge/hedgepolicy.py): the machinery on planted worlds."""
import math
import os
import random
import shutil
import tempfile
import unittest
from array import array

from finsim2.engine import learned as L
from finsim2.engine import residual as RS
from finsim2.hedge import hedgepolicy as HP


class Numerics(unittest.TestCase):
    def test_logit_fit_recovers_coefficients(self):
        rnd = random.Random(3)
        n = 20000
        x1 = array("d", (rnd.gauss(0, 1) for _ in range(n)))
        x2 = array("d", (rnd.gauss(0, 1) for _ in range(n)))
        y = array("d", (1.0 if rnd.random() < RS._sig(-0.3 + 0.8 * a - 0.5 * b) else 0.0 for a, b in zip(x1, x2)))
        b = RS.logit_fit([array("d", [1.0] * n), x1, x2], y, 1e-6, [0.0, 1.0, 1.0])
        self.assertAlmostEqual(b[0], -0.3, delta=0.06)
        self.assertAlmostEqual(b[1], 0.8, delta=0.06)
        self.assertAlmostEqual(b[2], -0.5, delta=0.06)

    def test_inverse_normal(self):
        for p in (0.001, 0.02, 0.3, 0.5, 0.77, 0.999):
            self.assertAlmostEqual(0.5 * (1 + math.erf(RS._inv_phi(p) / math.sqrt(2))), p, places=6)


class Worlds(unittest.TestCase):
    """Small versions of the capability worlds (the full suite runs before every market run)."""

    @classmethod
    def setUpClass(cls):
        cls.A = RS.Resid(RS.synth_world("A", n_assets=32))
        cls.E = RS.Resid(RS.synth_world("E", n_assets=32))

    def test_missing_signal_is_recovered_out_of_sample(self):
        m1 = RS.r1(self.A)
        ev = RS.evaluate_alpha(self.A, m1["wf"], "R1")
        self.assertGreater(ev["delta"]["t"], 5)
        w = RS.r1_final_weights(self.A, m1)
        sig = {k: v for k, v in w.items() if not k.startswith("ctx:") and k != "sig0"}      # sig0 is E itself (collinear with zE)
        self.assertEqual(max(sig, key=lambda k: abs(sig[k])), "sig1")
        self.assertGreater(sig["sig1"], 0)
        # the first residual era uses the pre-registered default (no scored inner history yet)
        self.assertEqual(m1["choices"]["2013-01-01"], f"{RS.R1_LAMBDAS[1]:g}|0.5")

    def test_optimal_baseline_passes_nothing(self):
        res, _ = RS.alpha_suite(self.E)
        self.assertFalse(any(v["passed"] for v in res.values()))

    def test_d_vs_e_ties_carry_no_label(self):
        de = RS.d_vs_e(self.E)
        lab = de["label"]
        for k in range(self.E.tab.n):
            if abs(self.E.pE[k] - self.E.pu[k]) == abs(self.E.pD[k] - self.E.pu[k]):
                self.assertNotEqual(lab[k], lab[k])          # NaN
        m = [v for v in lab if v == v]
        self.assertGreater(sum(m) / len(m), 0.5)             # E (= the truth's own signal) is closer more often than noisy D

    def test_reliability_beyond_conviction_is_zero_for_an_optimal_baseline(self):
        rel = RS.reliability(self.E)
        self.assertLess(rel["beyond_conviction"]["t"] or 0, 2.0)

    def test_buckets_are_monotone_when_E_has_skill(self):
        bk = RS.buckets(self.A)["2013–2024"]
        self.assertGreater(bk["monotonicity_rho"], 0.8)
        r = bk["rows"][0]
        self.assertLess(r["range95"][0], r["mean"])
        self.assertLess(r["ci95"][0], r["mean"])


class Nesting(unittest.TestCase):
    def test_short_history_and_ties_keep_the_default(self):
        R = RS.Resid(RS.synth_world("E", n_assets=16, weeks=700))
        s = R.base_score
        ch = RS.nested_pick(R.tab, R.ctx.lr, {"a": s, "b": s, "c": s}, "b", R.metric)
        self.assertTrue(all(v == "b" for v in ch["choices"].values()))


class HedgePolicy(unittest.TestCase):
    def test_planted_sizing_is_learned_and_noise_passes_nothing(self):
        p = HP.capability("planted")
        self.assertGreater(p["low_vol_to_half"], 0.8)
        self.assertGreater(p["high_vol_kept"], 0.8)
        self.assertTrue(p["passed"])
        self.assertFalse(HP.capability("noise")["passed"])

    def test_single_training_era_is_split_in_halves(self):
        cases = HP.synth_cases("noise", weeks=200)
        bl = HP._blocks(cases)
        self.assertEqual(bl, [("2009-01-01", "2011-01-01"), ("2011-01-01", "2013-01-01")])


class Registry(unittest.TestCase):
    def test_versions_are_research_only_and_protected_ids_untouched(self):
        from finsim2.data.store import Store
        from finsim2.engine.lab import registry
        tmp = tempfile.mkdtemp()
        st = Store(os.path.join(tmp, "x.db"))
        try:
            res = {"started": "t", "1W": {"alpha": {"R1": {"name": "R1", "gates": {"A1": True}, "passed": True}},
                                         "rel": {"name": "REL", "gates": {}, "passed": False}}}
            made = RS.register(st, res)
            self.assertIn("resid-ridge-exp", made)
            vs = {v["id"]: v for v in registry(st)["versions"]}
            self.assertEqual(vs["resid-ridge-exp"]["status"], "research")
            self.assertTrue(vs["resid-ridge-exp"]["eligible_for_live_shadow_proposal"])
            self.assertNotIn("alpha-learned-1w-hierarchy-exp", made)
        finally:
            st.close()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
