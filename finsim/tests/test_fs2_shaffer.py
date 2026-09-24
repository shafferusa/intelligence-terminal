"""Shaffer Score v2: one point-in-time function for history and live, family aggregation with a correlation penalty,
economic priors, calibration and the append-only prediction ledger."""
import math
import os
import shutil
import tempfile
import unittest

from finsim2 import shaffer_score as cfg
from finsim2.data.store import Store
from finsim2.engine import shaffer as sh
from finsim2.engine.research import Research
from finsim2.engine.tracking import record_shaffer, score_matured
from test_finsim2 import build_store


class Canonical(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.store = build_store(os.path.join(cls.tmp, "r.db"), n=1800)
        cls.r = Research(cls.store)
        cls.srun = sh.ShafferRun(cls.r, "SPY")
        cls.full = cls.srun.run()

    @classmethod
    def tearDownClass(cls):
        cls.store.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_history_is_produced_and_the_live_score_is_its_last_record(self):
        lab = "1M"
        hist = self.full["history"][lab]
        self.assertTrue(len(hist) > 50)
        self.assertEqual(hist[-1][0], self.srun.n - 1)
        self.assertEqual(hist[-1][1], self.full["latest"][lab]["raw"])

    def test_a_past_date_scored_directly_equals_its_record(self):
        lab = "1M"
        t, raw = self.full["history"][lab][len(self.full["history"][lab]) // 2][:2]
        rec = sh.compute_shaffer_score(self.r, "SPY", lab, self.srun.cal[t])
        self.assertAlmostEqual(rec["raw"], raw, places=9)

    def test_later_prices_do_not_change_an_earlier_score(self):
        lab = "1W"
        t, raw = self.full["history"][lab][len(self.full["history"][lab]) // 2][:2]
        tmp = tempfile.mkdtemp()
        try:
            st = Store(os.path.join(tmp, "r.db"))
            for a in [x["id"] for x in self.store.assets() if self.store.price_count(x["id"])]:
                rows = self.store.prices(a)
                st.upsert_prices(a, [dict(r, close=r["close"] * (0.5 if r["date"] > self.srun.cal[t] else 1.0),
                                          adj_close=r["adj_close"] * (0.5 if r["date"] > self.srun.cal[t] else 1.0)) for r in rows])
            for s_id in ("DGS10", "DGS2", "DGS3MO", "VIXCLS", "CPIAUCSL", "UNRATE"):
                st.upsert_macro(s_id, self.store.macro(s_id))
            rec = sh.compute_shaffer_score(Research(st), "SPY", lab, self.srun.cal[t])
            self.assertAlmostEqual(rec["raw"], raw, places=9)
            st.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_correlated_signals_do_not_vote_twice(self):
        lab, h = "3M", 63
        tau = self.srun.n - 1
        e = {"w": 0.05, "delta": 1, "ps": 0.05, "c": 0.5, "d": 1.0, "ic": 0.05, "n_eff": 100.0, "decay": "HEALTHY", "status": "active"}
        one = {"ret_3m": dict(e)}
        two = {"ret_3m": dict(e), "ret_6m": dict(e)}
        z = self.srun.z
        saved = (list(z["ret_3m"]), list(z["ret_6m"]))
        try:
            z["ret_6m"][tau] = z["ret_3m"][tau] = 1.0
            corr = {"Momentum": {("ret_3m", "ret_6m"): 1.0}}
            a = self.srun.score_at(tau, lab, h, one, corr, {}, {})
            b = self.srun.score_at(tau, lab, h, two, corr, {}, {})
            fa = next(f for f in a["families"] if f["family"] == "Momentum")
            fb = next(f for f in b["families"] if f["family"] == "Momentum")
            self.assertAlmostEqual(fa["score"], fb["score"], places=12)                      # a duplicate adds nothing
            self.assertAlmostEqual(sum(x["omega"] for x in fb["signals"] if x.get("active")), 1.0)
        finally:
            z["ret_3m"][:], z["ret_6m"][:] = saved

    def test_points_add_up_to_the_score(self):
        for lab, rec in self.full["latest"].items():
            if rec.get("raw") is None:
                continue
            at = sh.attribute(rec)
            self.assertAlmostEqual(sum(f["points"] for f in at["families"]), rec["raw"], places=9)
            self.assertAlmostEqual(sum(x["points"] for x in at["signals"]), rec["raw"], places=9)

    def test_priors_come_from_other_assets_and_earlier_januaries(self):
        self.assertTrue(self.store.kv_get(f"shaffer_cp:SPY:{cfg.VERSION}"))       # a full run saves its checkpoints
        self.assertEqual(sh.load_priors(self.r, "SPY", self.srun.cls), {})         # never its own evidence
        other = sh.load_priors(self.r, "ZZZ", self.srun.cls)
        self.assertTrue(other)
        run = sh.ShafferRun(self.r, "SPY", use_priors=False)
        run.priors = {"2017": {21: "a"}, "2019": {21: "b"}}
        tau = next(i for i, d in enumerate(run.cal) if d[:4] == "2018")
        self.assertEqual(run._prior_at(tau, 21), "a")                              # the 2019 checkpoint is still in the future
        self.assertIsNone(run._prior_at(tau, sh.PRIOR_MAX_H + 1))


class Rules(unittest.TestCase):
    def test_isotonic_is_monotone_and_pools_violations(self):
        fit = sh.isotonic([1, 2, 3, 4], [1.0, 3.0, 2.0, 4.0], [1, 1, 1, 1])
        self.assertEqual(fit, [1.0, 2.5, 2.5, 4.0])

    def test_bh_qvalues(self):
        q = sh._bh({"a": 0.001, "b": 0.02, "c": 0.5})
        self.assertLessEqual(q["a"], q["b"])
        self.assertLessEqual(q["b"], q["c"])
        self.assertAlmostEqual(q["c"], 0.5)

    def test_every_family_and_prior_is_declared(self):
        self.assertEqual(len(cfg.FAMILIES), 15)
        for f, sigs in cfg.FAMILIES.items():
            self.assertIn(f, cfg.APPLICABILITY)
            self.assertIn(f, cfg.HORIZON_FIT)
            self.assertEqual(len(cfg.HORIZON_FIT[f]), len(cfg.HORIZONS))
            for s, p in sigs:
                self.assertIn(p, (-1, 0, 1))
        self.assertEqual(cfg.applicability("Fundamental Quality", "CRYPTO"), 0.0)
        self.assertEqual(cfg.horizon_fit("Valuation", "1D"), 0.1)


class Ledger(unittest.TestCase):
    def test_record_is_append_only_and_graded_once(self):
        tmp = tempfile.mkdtemp()
        try:
            st = build_store(os.path.join(tmp, "r.db"), n=600)
            r = Research(st)
            cal = r.panel().calendar()
            made = cal[-100]
            full = {"horizons": {"1M": {"raw": 30.0, "calibrated": 12.0, "expected": 0.02, "range": (-0.05, 0.08), "confidence": 0.4,
                                        "regime": {"market": "bull"}, "families": [{"family": "Momentum", "points": 30.0}], "date": made}}}
            import unittest.mock as um
            with um.patch.object(r.panel(), "calendar", return_value=cal[:-99]):
                n = record_shaffer(st, r.panel(), "SPY", full)
            self.assertEqual(n, 1)
            p = st.predictions(asset_id="SPY")[0]
            self.assertEqual((p["model"], p["source"], p["raw"], p["calibrated"]), ("shaffer", "live", 30.0, 12.0))
            self.assertEqual(p["regime"], {"market": "bull"})
            self.assertEqual(score_matured(st, r.panel()), 1)
            p = st.predictions(asset_id="SPY")[0]
            self.assertIsNotNone(p["realized"])
            self.assertEqual(p["correct"], int((p["realized"] > 0) == True))
            st.score_prediction(p["id"], 99.0, 0.0, cal[-1], False)                        # a second grading is refused
            self.assertNotEqual(st.predictions(asset_id="SPY")[0]["realized"], 99.0)
            st.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()


class NonPositivePrices(unittest.TestCase):
    def test_a_negative_close_is_missing_for_analytics_and_features_still_compute(self):
        tmp = tempfile.mkdtemp()
        try:
            st = build_store(os.path.join(tmp, "r.db"), n=900)
            rows = st.prices("GOLD")
            bad = rows[500]["date"]
            st.upsert_prices("GOLD", [dict(rows[500], close=-37.63, adj_close=-37.63)])
            r = Research(st)
            cal = r.panel().calendar()
            s = r.panel().series("GOLD")
            self.assertTrue(all(v is None or v > 0 for v in s))
            self.assertEqual(s[cal.index(bad)], s[cal.index(bad) - 1])        # the previous close carries, as for any gap
            self.assertTrue(r.features("GOLD"))
            st.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
