"""Signal-level weight research: planted effects found, noise rejected, no look-ahead, bounds, baselines, bands."""
import datetime as dt
import math
import random
import unittest
from array import array

from finsim2.engine import weights as W

H = 5


def _recs(effect=0.0, seed=1, years=(2000, 2026), garbage_after=None, assets=24):
    """Weekly records. Signal 0 (active, δ=+1) drives y with strength `effect`; production raw is built from ten
    active signals with equal weight, so most of it is noise."""
    W._init_famidx()
    rnd = random.Random(seed)
    P = len(W.signals())
    metas = [{"id": f"A{i}", "asset_class": "EQUITY" if i % 2 else "ETF", "sector": "Financials" if i % 4 < 2 else "Energy"}
             for i in range(assets)]
    out = []
    d0 = dt.date(years[0], 1, 3)
    for w in range((years[1] - years[0]) * 52):
        d = d0 + dt.timedelta(days=7 * w)
        end = d + dt.timedelta(days=7)
        common = rnd.gauss(0, 0.5)
        for m in metas:
            r = W.Rec()
            r.asset, r.meta, r.date, r.end, r.wk = m["id"], m, d.isoformat(), end.isoformat(), W._week(d.isoformat())
            x = array("f", bytes(4 * P))
            act = []
            for j in range(10):
                x[j] = max(-1.0, min(1.0, rnd.gauss(0, 0.5)))
                act.append((j, 1, 0.1, 0.8, 1.0, 1.0))
            r.x, r.act = x, tuple(act)
            r.g = array("f", bytes(4 * len(W.families())))
            r.reg = ("bull", "low_vol", "falling_rates", "expansion")
            y = effect * x[0] + common + rnd.gauss(0, 1)
            if garbage_after and end.isoformat() >= garbage_after:
                y = rnd.gauss(0, 50)
            r.y, r.yr = y, 0.02 * y
            r.raw = 100 * math.tanh(sum(x[j] for j in range(10)) * 0.2)
            r.base = [1.0, None, 1.0 if rnd.random() < 0.5 else -1.0, None, None]
            out.append(r)
    return out


class SignalWeights(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.planted = _recs(effect=0.6)
        cls.res = W.run_challenger(cls.planted, H, "signal", "class")

    def test_planted_signal_found_and_passes(self):
        res = self.res
        g = res["final"]["weights"]["global"]
        self.assertEqual(max(range(len(g)), key=lambda j: g[j]), 0, "signal 0 should carry the largest weight")
        wf = res["walkforward"]
        self.assertGreater(wf["challenger"]["ic"], wf["production"]["ic"])
        self.assertGreater(wf["delta_acc"]["delta"], 0)
        self.assertTrue(res["gates"]["G1_walkforward"])
        self.assertTrue(res["gates"]["G2_eras"])
        self.assertEqual(res["gates"]["status"], "SHADOW")

    def test_bounds_hold(self):
        for node, w in self.res["final"]["weights"].items():
            for v in w:
                self.assertGreaterEqual(v, 0.0)
                self.assertLessEqual(v, W.W_MAX + 1e-12)

    def test_noise_rejected(self):
        res = W.run_challenger(_recs(effect=0.0, seed=7), H, "signal", "class")
        self.assertNotEqual(res["gates"]["status"], "SHADOW")
        self.assertFalse(res["gates"]["G1_walkforward"])

    def test_bands_and_metrics(self):
        wf = self.res["walkforward"]
        self.assertEqual(len(wf["challenger"]["bands"]), 9)
        self.assertEqual(sum(b["n"] for b in wf["challenger"]["bands"]), wf["n"])
        self.assertEqual(sum(b["n"] for b in wf["challenger"]["abs_bands"]), wf["n"])
        for k in ("acc", "lo", "hi", "ic", "ic_t", "rank_ic", "rank_t"):
            self.assertIn(k, wf["challenger"])
        self.assertIn("always_bullish", wf["baselines"]["by"])
        self.assertEqual(len(self.res["eras"]), len(W.ERAS))
        self.assertIn("EQUITY", self.res["by_class"])

    def test_research_score_and_stability(self):
        self.assertIn("score", self.res["research_score"])
        st = W.stability(self.res)
        self.assertTrue(st[W.signals()[0]]["same_sign"])


class Report(unittest.TestCase):
    def test_markdown_answers_and_main_table(self):
        recs = _recs(effect=0.6, seed=13, years=(2000, 2026), assets=8)
        c = W.run_challenger(recs, H, "signal", "class")
        hz = {"records": len(recs), "assets": 8, "first": recs[0].date, "last": recs[-1].date, "challengers": {"signal@class": c}, "best": "signal@class",
              "specialisation": {"class": {"acc": 0.55, "ic": 0.1, "production_acc": 0.5, "production_ic": 0.0}},
              "weights": W.effective_weights(recs, c), "stability": W.stability(c)}
        md = W.markdown({"started": "x", "score_version": "2.1", "w_max": W.W_MAX, "shrink_k": W.SHRINK_K, "horizons": {"1W": hz}})
        self.assertIn("| Horizon | Production accuracy | Best challenger accuracy | Naive baseline | Excess vs baseline", md)
        for head in ("**1.", "**2.", "**3–4.", "**5.", "**6–8.", "**9.", "**10.", "**11–13.", "**14.", "**15."):
            self.assertIn(head, md)
        self.assertIn("SHADOW", md)
        rows = W.summary_rows({"horizons": {"1W": hz}})
        self.assertEqual(rows[0]["status"], "SHADOW")
        self.assertAlmostEqual(rows[0]["excess"], rows[0]["challenger_acc"] - rows[0]["baseline_acc"])


class OtherKinds(unittest.TestCase):
    def test_scaling_and_interact_run(self):
        recs = _recs(effect=0.6, seed=9, years=(2000, 2020), assets=8)
        sc = W.run_challenger(recs, H, "scaling", "global", select_gamma=True)
        self.assertIn(tuple(sc["gamma"]), [tuple(g) for g in W.GAMMAS])
        for e in sc["eras"]:
            if "gamma" in e:
                self.assertIn(tuple(e["gamma"]), [tuple(g) for g in W.GAMMAS])
        it = W.run_challenger(recs, H, "interact", "global")
        self.assertEqual(len(it["final"]["weights"]["global"]), len(W.signals()) + len(W.INTERACTIONS))
        fr = W.run_challenger(recs, H, "free", "global")
        self.assertGreater(fr["final"]["weights"]["global"][0], 0)


class Live(unittest.TestCase):
    def test_live_score_equals_research_formula(self):
        recs = _recs(effect=0.6, seed=11, years=(2000, 2016), assets=6)
        res = W.run_challenger(recs, H, "signal", "class")
        fin = res["final"]

        class KV:
            def kv_get(s, k):
                return {"1W": {"kind": "signal", "depth": "class", "gamma": res["gamma"], **fin}} if k == "formula:weights:v" else None

        sigs = W.signals()
        r = recs[-1]
        sig = {"x": {sigs[j]: r.x[j] for j in range(len(sigs)) if r.x[j]}, "a": {sigs[j]: [dl, om, c, rr, d] for j, dl, om, c, rr, d in r.act}, "g": {}}
        live = W.live_score(KV(), "v", r.meta, "1W", sig, {})
        w = fin["weights"]["class:" + r.meta["asset_class"]]
        want = 100 * math.tanh(W._index(w, fin["rms"], W.features(r, "signal")) / fin["scale"])
        self.assertAlmostEqual(live, want, places=4)
        self.assertIsNone(W.live_score(KV(), "v", r.meta, "1W", None, {}))

    def test_record_shadow_stores_signal_challenger_with_node_and_versions(self):
        from finsim2.engine import lab as L
        import finsim2.engine.tracking as T
        recs = _recs(effect=0.6, seed=12, years=(2000, 2016), assets=6)
        res = W.run_challenger(recs, H, "signal", "class")

        class St:
            def __init__(s):
                s.rows, s.kv = [], {}
            def kv_get(s, k): return s.kv.get(k)
            def kv_set(s, k, v): s.kv[k] = v
            def asset(s, a): return {"id": a, "asset_class": "EQUITY"}
        st = St()
        vid = "shaffer-2.1-sig-signal-class-exp"
        st.kv[L.REGISTRY_KEY] = {"versions": [{"id": vid, "kind": "shaffer", "status": "challenger", "family": "signal-weights",
                                               "validation": {"1W": {"gates": {"G1_discovery": True, "G2_confirmation": True}}}}]}
        st.kv[f"formula:weights:{vid}"] = {"1W": {"kind": "signal", "depth": "class", "gamma": res["gamma"], **res["final"]}}
        sigs = W.signals()
        r = recs[-1]
        sig = {"x": {sigs[j]: r.x[j] for j in range(len(sigs)) if r.x[j]}, "a": {sigs[j]: [dl, om, c, rr, d] for j, dl, om, c, rr, d in r.act}, "g": {}}

        class Panel:
            def calendar(s): return ["2026-01-02"]

        class R:
            store = st
            def shaffer_full(s, a): return {"horizons": {"1W": {"raw": 5.0, "date": "2026-01-02", "families": [], "sig": sig, "confidence": 0.4}}}
            def panel(s): return Panel()
            def regimes(s): return {"volatility": ["low_vol"]}
        saved = T.record
        T.record = lambda store, panel, a, d, model, version, lab, h, e, eb, c, score, detail, **kw: store.rows.append((model, lab, score, detail)) or 1
        try:
            n = L.record_shadow(R(), "Z")
        finally:
            T.record = saved
        self.assertEqual(n, 1)
        model, lab, score, detail = st.rows[0]
        self.assertEqual(model, f"shaffer:{vid}")
        self.assertEqual(detail["node"], "class:EQUITY")
        self.assertEqual(detail["production_version"], "shaffer-2.1")
        self.assertEqual(detail["challenger_version"], vid)
        self.assertEqual(detail["confidence"], 0.4)
        self.assertEqual(detail["production_raw"], 5.0)

    def test_reliability_bands(self):
        class KV:
            def kv_get(s, k):
                return {"horizons": {"1M": {"production_all": {"abs_bands": [{"band": "0..10", "correct": 0.5, "lo": 0.48, "hi": 0.52, "n": 100},
                                                                             {"band": "75..100", "correct": 0.7, "lo": 0.65, "hi": 0.75, "n": 50}]},
                                            "baselines_all": {"best": "always_bullish", "best_acc": 0.6}}}}
        self.assertFalse(W.reliability(KV(), "1M", 5.0)["validated"])
        r = W.reliability(KV(), "1M", -100.0)
        self.assertEqual(r["band"], "75..100")
        self.assertTrue(r["validated"])
        self.assertIsNone(W.reliability(KV(), "1M", None))


class NoLookAhead(unittest.TestCase):
    def test_era_fit_ignores_outcomes_after_cutoff(self):
        clean = _recs(effect=0.6, seed=3, years=(2000, 2016))
        dirty = _recs(effect=0.6, seed=3, years=(2000, 2016), garbage_after="2013-01-01")
        for recs in (clean, dirty):
            paths = [W._cut(W.node_path(r.meta, "hier"), "class") for r in recs]
            recs.append(W._Trainer(recs, paths, "signal", H, ["2013-01-01", "2009-01-01"]).fit("2013-01-01", (1, 1, 1)))
        a, b = clean[-1], dirty[-1]
        self.assertEqual(a.n, b.n)
        for node in a.W:
            for u, v in zip(a.W[node], b.W[node]):
                self.assertAlmostEqual(u, v, places=12)

    def test_purge_uses_outcome_date(self):
        recs = _recs(effect=0.6, seed=4, years=(2000, 2010))
        paths = [W._cut(W.node_path(r.meta, "hier"), "global") for r in recs]
        T = W._Trainer(recs, paths, "signal", H, ["2005-01-01"])
        ft = T.fit("2005-01-01", (1, 1, 1))
        self.assertEqual(ft.n, sum(1 for r in recs if r.end < "2005-01-01"))

    def test_bucketed_stats_equal_direct(self):
        recs = _recs(effect=0.6, seed=5, years=(2000, 2008), assets=6)
        paths = [W._cut(W.node_path(r.meta, "hier"), "sector") for r in recs]
        T = W._Trainer(recs, paths, "signal", H, ["2003-01-01", "2006-01-01"])
        T.fit("2006-01-01", (1, 1, 1))
        direct = W.PStats(T.P)
        for r in recs:
            if r.end < "2006-01-01":
                direct.add(W.features(r, "signal"), r.y)
        k = T.cuts.index("2006-01-01")
        agg = W.PStats(T.P)
        for row in T._cum((1, 1, 1)).values():
            agg.merge(row[k])
        self.assertEqual(agg.n, direct.n)
        for u, v in zip(agg.xx, direct.xx):
            self.assertAlmostEqual(u, v, places=6)


class Solver(unittest.TestCase):
    def test_box_solution_matches_unconstrained_when_inside(self):
        rnd = random.Random(2)
        st = W.PStats(3)
        for _ in range(2000):
            f = [(0, rnd.gauss(0, 1)), (1, rnd.gauss(0, 1)), (2, rnd.gauss(0, 1))]
            st.add(f, 0.05 * f[0][1] - 0.03 * f[1][1] + rnd.gauss(0, 1))
        rms = [1.0, 1.0, 1.0]
        w = W.solve_box(st, rms, [0.0] * 3, 1.0, 1e-9, [-1] * 3, [1] * 3, iters=500, tol=1e-12)
        self.assertAlmostEqual(w[0], 0.05, delta=0.03)
        self.assertAlmostEqual(w[1], -0.03, delta=0.03)
        wb = W.solve_box(st, rms, [0.0] * 3, 1.0, 1e-9, [0.0] * 3, [0.01] * 3, iters=500)
        self.assertEqual(wb[1], 0.0)
        self.assertAlmostEqual(wb[0], 0.01)

    def test_shrinkage_pulls_to_parent(self):
        st = W.PStats(1)
        for i in range(100):
            st.add([(0, 1.0 if i % 2 else -1.0)], 1.0 if i % 2 else -1.0)
        w = W.solve_box(st, [1.0], [0.0], 1.0, 1e6, [-1.0], [1.0])
        self.assertLess(abs(w[0]), 0.01)


class PITBaselines(unittest.TestCase):
    def test_positive_frequency_uses_matured_outcomes_only(self):
        """load(): the asset's positive-outcome frequency counts only outcomes that ended before the date."""
        class FakePanel:
            def __init__(s, cal): s.cal = cal
            def calendar(s): return s.cal
            def series(s, a): return [100.0 + i for i in range(len(s.cal))]

        class FakeResearch:
            def __init__(s, cal): s.p = FakePanel(cal)
            def panel(s): return s.p
            def regimes(s): return {}

        cal = [(dt.date(2010, 1, 4) + dt.timedelta(days=i)).isoformat() for i in range(400)]
        rows = []
        for i in range(0, 380, 7):
            yr = 0.01 if i < 200 else -0.01                  # positive early, negative late
            rows.append([cal[i], 0.0, yr, yr, {"x": {}, "a": {}, "g": {}}])

        class FakeStore:
            def assets(s): return [{"id": "Z", "asset_class": "ETF"}]
            def lab_records(s, v, h): return [{"asset_id": "Z", "rows": rows}]

        recs = W.load(FakeStore(), FakeResearch(cal), "1M")
        h = 21
        for r in recs:
            i = cal.index(r.date)
            matured = [x for x in rows if cal.index(x[0]) + h < len(cal) and cal[cal.index(x[0]) + h] < r.date]
            if len(matured) < 20:
                self.assertIsNone(r.base[1])
            else:
                pos = sum(1 for x in matured if x[3] > 0) / len(matured)
                self.assertEqual(r.base[1], 1.0 if pos > 0.5 else -1.0)
            prev = math.log(cal and (100.0 + i) / (100.0 + i - h)) if i - h >= 0 else None
            self.assertEqual(r.base[2], (1.0 if prev > 0 else -1.0) if prev else None)


if __name__ == "__main__":
    unittest.main()
