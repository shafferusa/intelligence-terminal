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


class CalibrationIntegrity(unittest.TestCase):
    def test_bins_cover_every_pair_exactly_once(self):
        # 10 bins of 23 and a short tail of 7: the tail is merged into the last bin; nothing duplicated or dropped
        for m in (237, 230, 247, 61):
            pairs = [((i * 37 % m) - m / 2, math.sin(i) * 0.3, 0.01 * math.sin(i), i) for i in range(m)]
            cal = sh.ShafferRun._calibration(None, pairs, 21)
            self.assertEqual(sum(b["n"] for b in cal["bins"]), m, m)
            self.assertEqual(len({(b["lo"], b["hi"]) for b in cal["bins"]}), len(cal["bins"]), m)       # no bin twice

    def test_expected_return_is_typical_plus_evidence_and_the_evidence_has_the_calibrated_sign(self):
        store = build_store(os.path.join(tempfile.mkdtemp(), "r.db"), n=2200)
        try:
            run = sh.ShafferRun(Research(store), "SPY", use_priors=False)
            res = run.run()
            shown = 0
            for lab, rec in res["latest"].items():
                if rec.get("expected") is None:
                    continue
                shown += 1
                self.assertAlmostEqual(rec["expected"], rec["expected_typical"] + rec["expected_edge"], places=12)
                if abs(rec["calibrated"]) > 1e-9 and abs(rec["expected_edge"]) > 1e-12:
                    self.assertEqual(rec["expected_edge"] > 0, rec["calibrated"] > 0, lab)
            for lab, (n, total_differs, evidence_differs) in run.sign_checks.items():      # every scored date
                self.assertEqual(evidence_differs, 0, lab)
        finally:
            store.close()


class ShadowFamilies(unittest.TestCase):
    """Candidate families run in shadow: they must not move the production score, must be point in time, and enter
    the score only when admitted."""
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.store = build_store(os.path.join(cls.tmp, "r.db"), n=1800)
        cls.r = Research(cls.store)
        cls.srun = sh.ShafferRun(cls.r, "SPY", use_priors=False)
        cls.res = cls.srun.run()

    @classmethod
    def tearDownClass(cls):
        cls.store.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _stripped(self):
        saved = (cfg.ALL_FAMILIES, cfg.ALL_FAMILY_OF)
        cfg.ALL_FAMILIES, cfg.ALL_FAMILY_OF = dict(cfg.FAMILIES), dict(cfg.FAMILY_OF)
        try:
            run = sh.ShafferRun(Research(self.store), "SPY", use_priors=False)
            return run, run.run()
        finally:
            cfg.ALL_FAMILIES, cfg.ALL_FAMILY_OF = saved

    def test_candidates_do_not_change_the_production_score(self):
        self.assertTrue(any(sg not in cfg.FAMILY_OF for sg in self.srun.signals))            # candidates are in the run
        self.assertTrue(any(self.res["shadow"][lab] for lab in self.res["shadow"]))          # shadow records exist
        _, base = self._stripped()
        for lab in base["history"]:
            self.assertEqual([x[:3] for x in base["history"][lab]], [x[:3] for x in self.res["history"][lab]], lab)

    def test_an_admitted_family_enters_the_score(self):
        lab = "1M"
        shadow = self.res["latest"][lab].get("shadow") or {}
        fams = sorted(shadow.get("families") or [], key=lambda f: -f["n_active"])
        self.assertTrue(fams, "no candidate family present in the synthetic store")
        fams = [x["family"] for x in fams]
        f = fams[0]
        saved = dict(cfg.ADMITTED)
        cfg.ADMITTED[f] = [lab]
        try:
            res = sh.ShafferRun(Research(self.store), "SPY", use_priors=False).run()
        finally:
            cfg.ADMITTED.clear(); cfg.ADMITTED.update(saved)
        rec = res["latest"][lab]
        self.assertIn(f, [x["family"] for x in rec["families"]])
        self.assertAlmostEqual(rec["raw"], shadow["with"][f], places=9)          # exactly what the shadow said it would read

    def test_candidate_signals_are_point_in_time(self):
        from finsim2.engine.candidates import CANDIDATE_FEATURES
        feats = self.r.features("SPY")
        cal = self.r.panel().calendar()
        t = len(cal) // 2
        tmp = tempfile.mkdtemp()
        try:
            st = Store(os.path.join(tmp, "r.db"))
            for a in [x["id"] for x in self.store.assets() if self.store.price_count(x["id"])]:
                st.upsert_prices(a, [dict(r, close=r["close"] * (0.5 if r["date"] > cal[t] else 1.0),
                                          adj_close=r["adj_close"] * (0.5 if r["date"] > cal[t] else 1.0)) for r in self.store.prices(a)])
            for s_id in ("DGS10", "DGS2", "DGS3MO", "VIXCLS", "CPIAUCSL", "UNRATE"):
                st.upsert_macro(s_id, [(d, v * (1.3 if d > cal[t] else 1.0)) for d, v in self.store.macro(s_id)])
            f2 = Research(st).features("SPY")
            checked = 0
            for k in CANDIDATE_FEATURES:
                a, b = feats[k][t], f2[k][t]
                self.assertEqual(a is None, b is None, k)
                if a is not None:
                    self.assertAlmostEqual(a, b, places=12, msg=k)
                    checked += 1
            self.assertGreater(checked, 2)
            st.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_admission_rule(self):
        import random
        from finsim2.engine.audit import candidate_verdicts
        rnd = random.Random(3)

        def part(mean, n=400, noise=1.0, seed=None):
            g = random.Random(seed) if seed is not None else rnd
            prods = [mean + g.gauss(0, noise) for _ in range(n)]
            return {"n_eff": n, "own": mean, "t_own": None, "partial": sum(prods) / n, "t_partial": None, "ic_without": 0.0, "ic_with": 0.01,
                    "mono_without": 0.1, "mono_with": 0.2, "weeks": list(range(n)), "prods": prods}

        good = [{"Carry": {"decide": part(0.3), "confirm": part(0.3)}} for _ in range(6)]
        self.assertEqual(candidate_verdicts(good, 5)["Carry"]["verdict"], "ADMIT")
        bad = [{"Carry": {"decide": part(0.3), "confirm": part(-0.2)}} for _ in range(6)]
        v = candidate_verdicts(bad, 5)["Carry"]
        self.assertEqual(v["verdict"], "REJECT")
        self.assertTrue(any("not confirmed" in r for r in v["reasons"]))
        self.assertEqual(candidate_verdicts(good[:3], 5)["Carry"]["verdict"], "REJECT")             # too few assets
        # six identical (perfectly correlated) copies of a weak asset are ONE piece of evidence: pooling by date gives the
        # same t as a single copy, where treating them as independent would multiply it by √6
        weak = {"decide": part(0.06, seed=11), "confirm": part(0.06, seed=12)}
        one = candidate_verdicts([{"Carry": weak}], 5)["Carry"]["decide"]["t_partial"]
        six = candidate_verdicts([{"Carry": weak} for _ in range(6)], 5)["Carry"]["decide"]["t_partial"]
        self.assertAlmostEqual(one, six, places=9)


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


class EarningsSurprise(unittest.TestCase):
    """Research phase 2: SUE from first-reported SEC figures, usable only from the filing date."""

    def _rows(self):
        rows = []
        import datetime as _d
        v = 1.0
        for y in range(2010, 2016):
            for q, (m, fp) in enumerate(((3, "Q1"), (6, "Q2"), (9, "Q3"))):
                end = _d.date(y, m, 28)
                v += 0.05 + 0.02 * ((y * 7 + q * 3) % 5 - 2) + (0.5 if (y, fp) == (2014, "Q2") else 0.0)
                rows.append({"concept": "eps", "period_end": end.isoformat(), "filed": (end + _d.timedelta(days=35)).isoformat(), "value": v, "fp": fp})
            fy_q4 = v + 0.05
            fy = sum(r["value"] for r in rows[-3:]) + fy_q4
            end = _d.date(y, 12, 28)
            rows.append({"concept": "eps", "period_end": end.isoformat(), "filed": (end + _d.timedelta(days=50)).isoformat(), "value": fy, "fp": "FY"})
            v = fy_q4
        # a later restatement of 2014 Q2 must NOT replace the first report
        rows.append({"concept": "eps", "period_end": "2014-06-28", "filed": "2015-08-01", "value": 99.0, "fp": "Q2"})
        return rows

    def test_first_report_and_derived_q4(self):
        from finsim2.engine.candidates import quarterly_values
        q = quarterly_values(self._rows(), "eps")
        d = {end: (avail, v) for end, avail, v in q}
        self.assertNotEqual(d["2014-06-28"][1], 99.0)                    # first report kept
        self.assertEqual(d["2012-12-28"][0], "2013-02-16")                 # Q4 known from the 10-K filing date
        self.assertEqual(len(q), 6 * 4)

    def test_sue_is_available_only_from_the_filing_date(self):
        import datetime as _d
        from finsim2.engine.candidates import quarterly_values, sue_series
        cal = [(_d.date(2010, 1, 1) + _d.timedelta(days=i)).isoformat() for i in range(0, 6 * 365)]
        s = sue_series(quarterly_values(self._rows(), "eps"), cal, hold=63)
        i = cal.index("2014-08-02")                                           # Q2 2014 filed 35 days after 2014-06-28
        self.assertIsNone(s[i - 1])
        self.assertIsNotNone(s[i])
        self.assertGreater(s[i], 2.0)                                         # the jump is a large positive surprise
        self.assertIsNone(s[i + 63])


class MethodologyVariants(unittest.TestCase):
    def test_variants_equal_production_weighting_when_priors_and_validation_are_neutral(self):
        fams = [{"family": "A", "A": 1.0, "H": 1.0, "E": 0.8, "V": 1.0, "score": 0.4, "n_active": 2},
                {"family": "B", "A": 1.0, "H": 1.0, "E": 0.5, "V": 1.0, "score": -0.2, "n_active": 1}]
        vf = {"A": {"t": 2.5}, "B": {"t": 3.0}}
        v = sh.ShafferRun._variants(fams, vf)
        base = 100.0 * math.tanh((0.8 * 0.4 - 0.5 * 0.2) / (cfg.KAPPA * 2.0))
        for k in ("no_prior_H", "strict_V", "no_H_strict_V"):
            self.assertAlmostEqual(v[k], base, places=12)

    def test_strict_validation_zeroes_a_negative_record_and_never_flips_it(self):
        fams = [{"family": "A", "A": 1.0, "H": 0.5, "E": 1.0, "V": 0.25, "score": 0.6, "n_active": 1}]
        v = sh.ShafferRun._variants(fams, {"A": {"t": -1.0}})
        self.assertEqual(v["strict_V"], 0.0)
        v = sh.ShafferRun._variants(fams, {"A": {"t": 1.0}})
        self.assertGreater(v["strict_V"], 0.0)                               # same sign as the family score
