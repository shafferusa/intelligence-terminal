"""ML Lab: hierarchical weight research without look-ahead, the version registry and the promotion gate."""
import datetime as dt
import math
import os
import random
import shutil
import tempfile
import unittest

from finsim2.data.store import Store
from finsim2.engine import lab as L


def _recs(signal=True, seed=1, years=(2004, 2024), h=21, garbage_after=None):
    """Weekly records for 16 equities and 16 ETFs. Family 0 predicts +y for equities and −y for ETFs (so a single
    global weight cancels out); production raw is noise."""
    rnd = random.Random(seed)
    p = len(L.families())
    out = []
    d0 = dt.date(years[0], 1, 5)
    weeks = (years[1] - years[0]) * 52
    assets = [({"id": f"E{i}", "asset_class": "EQUITY", "sector": "Information Technology" if i < 8 else "Financials"}) for i in range(16)] + \
             [({"id": f"F{i}", "asset_class": "ETF", "sector": "Broad Market"}) for i in range(16)]
    for w in range(weeks):
        d = d0 + dt.timedelta(days=7 * w)
        end = d + dt.timedelta(days=int(h * 1.45))
        common = rnd.gauss(0, 1)
        for m in assets:
            x = [rnd.gauss(0, 0.3) for _ in range(p)]
            sgn = 1 if m["asset_class"] == "EQUITY" else -1
            y = (sgn * 1.2 * x[0] if signal else 0.0) + 0.5 * common + rnd.gauss(0, 1)
            if garbage_after and d.isoformat() >= garbage_after:
                y = rnd.gauss(0, 5)
            out.append({"asset": m["id"], "meta": m, "date": d.isoformat(), "end": end.isoformat(), "raw": 100 * math.tanh(rnd.gauss(0, 0.3)),
                        "y": y, "yr": 0.05 * y, "x": x, "c": [0.0] * p, "regime": "high_vol" if common > 0 else "low_vol"})
    return out


class Weights(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recs = _recs()
        cls.res = {v: L.study(cls.recs, 21, v) for v in ("global", "class", "hier")}

    def test_specialisation_found_where_it_exists(self):
        cl = self.res["class"]
        wf = cl["walkforward"]["from"]
        self.assertGreater(wf["challenger"]["ic"], wf["production"]["ic"] + 0.05)
        self.assertTrue(cl["gates"]["G1_discovery"], cl["walkforward"]["before"]["paired"])
        self.assertTrue(cl["gates"]["G2_confirmation"], cl.get("confirmation"))
        self.assertTrue(self.res["hier"]["gates"]["G2_confirmation"])
        # one global weight cannot express opposite signs by class: it must not pass
        self.assertFalse(self.res["global"]["gates"]["G2_confirmation"])
        wE = self.res["class"]["final"]["weights"]["class:EQUITY"][0]
        wF = self.res["class"]["final"]["weights"]["class:ETF"][0]
        self.assertGreater(wE, 0)
        self.assertLess(wF, 0)

    def test_noise_passes_no_gate(self):
        for v in ("global", "class", "hier"):
            g = L.study(_recs(signal=False, seed=2), 21, v)["gates"]
            self.assertFalse(g["G1_discovery"] and g["G2_confirmation"], v)

    def test_discovery_weights_never_see_the_confirmation_period(self):
        a = L.study(self.recs, 21, "class")
        b = L.study(_recs(garbage_after=L.CONFIRM_FROM), 21, "class")
        wa = a.get("discovery_weights") or {}
        wb = b.get("discovery_weights") or {}
        self.assertTrue(wa)
        for node in wa:
            for x, y in zip(wa[node], wb[node]):
                self.assertAlmostEqual(x, y, places=9)

    def test_shrinkage_toward_the_parent(self):
        st_parent, st_child = L._Stats(2), L._Stats(2)
        rnd = random.Random(4)
        for _ in range(5000):
            x = [rnd.gauss(0, 1), rnd.gauss(0, 1)]
            st_parent.add(x, 0.5 * x[0] + rnd.gauss(0, 1))
        for _ in range(10):                                  # a tiny child that says the opposite
            x = [rnd.gauss(0, 1), rnd.gauss(0, 1)]
            st_child.add(x, -0.5 * x[0])
        st_parent.merge(st_child)
        orig = L.families
        L.families = lambda: ["a", "b"]
        try:
            W, rms = L.fit_tree({"global": st_parent, "asset:X": st_child}, {"asset:X": "global"}, 5)
        finally:
            L.families = orig
        self.assertGreater(W["global"][0], 0.3)
        self.assertGreater(W["asset:X"][0], 0.0)          # ten records are not enough to flip the parent's sign
        self.assertLess(W["asset:X"][0], W["global"][0])


class LiveShadow(unittest.TestCase):
    def test_only_challengers_past_both_gates_are_recorded(self):
        class St:
            def __init__(self):
                self.rows = []
                self.kv = {}
            def kv_get(self, k): return self.kv.get(k)
            def kv_set(self, k, v): self.kv[k] = v
            def asset(self, a): return {"id": a, "asset_class": "EQUITY"}
            def predictions(self, **k): return []
            def add_prediction(self, p): self.rows.append(p); return len(self.rows)
        st = St()
        good = L.study(_recs(), 21, "class")
        bad = L.study(_recs(signal=False, seed=9), 21, "class")
        st.kv[L.REGISTRY_KEY] = {"versions": [
            {"id": "shaffer-2.1-class-exp", "kind": "shaffer", "status": "challenger", "validation": {"1M": {"gates": good["gates"]}}},
            {"id": "shaffer-2.1-global-exp", "kind": "shaffer", "status": "challenger", "validation": {"1M": {"gates": bad["gates"]}}}]}
        for vid, s in (("shaffer-2.1-class-exp", good), ("shaffer-2.1-global-exp", bad)):
            st.kv[f"formula:weights:{vid}"] = {"1M": {"weights": s["final"]["weights"], "rms": s["final"]["rms"], "scale": s["final"]["scale"]}}

        class Panel:
            def calendar(self): return ["2026-01-02"]
        class R:
            store = st
            def shaffer_full(self, a): return {"horizons": {"1M": {"raw": 5.0, "date": "2026-01-02", "families": [{"family": L.families()[0], "score": 0.3}]}}}
            def panel(self): return Panel()
            def regimes(self): return {"volatility": ["low_vol"]}
        import finsim2.engine.tracking as T
        saved = T.record
        T.record = lambda store, panel, a, d, model, *args, **kw: store.add_prediction({"model": model})
        try:
            n = L.record_shadow(R(), "NVDA")
        finally:
            T.record = saved
        self.assertTrue(good["gates"]["G1_discovery"] and good["gates"]["G2_confirmation"])
        self.assertEqual([r["model"] for r in st.rows], ["shaffer:shaffer-2.1-class-exp"])
        self.assertEqual(n, 1)


class Registry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.st = Store(os.path.join(self.tmp, "l.db"))

    def tearDown(self):
        self.st.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _result(self):
        s = L.study(_recs(), 21, "class")
        return {"started": "2026-09-25", "data_version": "t", "horizons": {"1M": {"variants": {"class": s}}}}

    def test_production_versions_are_registered(self):
        reg = L.registry(self.st)
        kinds = {(v["kind"], v["status"]) for v in reg["versions"]}
        self.assertIn(("shaffer", "production"), kinds)
        self.assertIn(("hedge", "production"), kinds)

    def test_challenger_needs_live_shadow_and_an_explicit_promotion(self):
        made = L.register_challengers(self.st, self._result())
        self.assertEqual(len(made), 1)
        vid = made[0]
        v = next(x for x in L.registry(self.st)["versions"] if x["id"] == vid)
        self.assertEqual(v["status"], "challenger")
        s = L.stage(self.st, v)
        self.assertEqual(s["by_horizon"]["1M"], "live shadow")
        self.assertEqual(s["stage"], "live shadow")                 # no live evidence yet
        with self.assertRaises(ValueError):
            L.promote(self.st, vid, confirm=True)
        # graded live forecasts where the challenger ranks better than production
        rnd = random.Random(5)
        for i in range(L.LIVE_MIN + 5):
            day = (dt.date(2026, 1, 1) + dt.timedelta(days=i)).isoformat()
            r = rnd.gauss(0, 0.05)
            for model, sc in (("shaffer", rnd.gauss(0, 1)), (f"shaffer:{vid}", 100 * r + rnd.gauss(0, 1))):
                pid = self.st.add_prediction({"asset_id": "SPY", "horizon": "1M", "model": model, "model_version": "t", "made_on": day,
                                              "target_date": day, "predicted": None, "error_band": None, "confidence": None, "score": sc, "detail": {}, "raw": sc})
                self.st.score_prediction(pid, r, None, day, None)
        s = L.stage(self.st, next(x for x in L.registry(self.st)["versions"] if x["id"] == vid))
        self.assertEqual(s["stage"], "eligible for promotion")
        with self.assertRaises(ValueError):
            L.promote(self.st, vid)                                   # no confirmation: refused
        v = L.promote(self.st, vid, confirm=True)
        self.assertEqual(v["status"], "production")
        reg = L.registry(self.st)
        self.assertEqual(sum(1 for x in reg["versions"] if x["kind"] == "shaffer" and x["status"] == "production"), 1)

    def test_challenger_score_uses_the_deepest_node(self):
        vid = L.register_challengers(self.st, self._result())[0]
        fam = {f: 0.0 for f in L.families()}
        fam[L.families()[0]] = 0.5
        e = L.challenger_score(self.st, vid, {"id": "ZZZ", "asset_class": "EQUITY"}, "1M", fam)
        f = L.challenger_score(self.st, vid, {"id": "YYY", "asset_class": "ETF"}, "1M", fam)
        self.assertGreater(e, 0)
        self.assertLess(f, 0)


if __name__ == "__main__":
    unittest.main()
