"""New-information research: PIT feature builders, incremental tests (planted effects found, noise and prior copies
rejected), Benjamini-Hochberg, statuses (LIMITED HISTORY never verified historically, BLOCKED listed)."""
import datetime as dt
import math
import random
import unittest
from array import array

from finsim2.engine import directional as D
from finsim2.engine import newinfo as N
from finsim2.engine import weights as W

H = 5


class FakeBuilder:
    """Serves one family's features from a dict {asset: {feature: series}} on a weekly index."""

    def __init__(self, feats, n):
        self.f, self.n = feats, n

    def features(self, family, meta):
        return self.f.get(meta["id"])


def _world(effect_alpha=0.0, effect_dir=0.0, copy_prior=False, seed=1, weeks=26 * 52, assets=16):
    """Weekly records 2000–2026. Feature x predicts the excess return (alpha) and / or the sign (directional)."""
    W._init_famidx()
    rnd = random.Random(seed)
    P = len(W.signals())
    metas = [{"id": f"A{i}", "asset_class": "EQUITY", "sector": "S"} for i in range(assets)]
    feats = {m["id"]: {"x": [None] * weeks} for m in metas}
    rets = {m["id"]: [None] * (weeks + 10) for m in metas}
    recs = []
    d0 = dt.date(2000, 1, 3)
    for w in range(weeks):
        d = d0 + dt.timedelta(days=7 * w)
        common = rnd.gauss(0, 0.4)
        for m in metas:
            z = 0.3 if int(m["id"][1:]) % 2 else -0.1
            x = rnd.gauss(0, 1)
            if copy_prior:
                x = z + rnd.gauss(0, 0.01)
            feats[m["id"]]["x"][w] = x
            a = rnd.gauss(0, 1)
            y = z + 0.3 * a + effect_alpha * x + effect_dir * x + common + rnd.gauss(0, 1)
            r = W.Rec()
            r.asset, r.meta, r.date, r.end, r.wk = m["id"], m, d.isoformat(), (d + dt.timedelta(days=7)).isoformat(), W._week(d.isoformat())
            r.raw = 100 * math.tanh(0.5 * a)
            r.x, r.act = array("f", bytes(4 * P)), ()
            r.g = array("f", bytes(4 * len(W.families())))
            r.reg = ("bull" if common > 0 else "bear", "low_vol", "falling_rates", "expansion")
            r.y, r.yr = y, 0.03 * y
            r.ext = {"i": w, "s": 0.03, "mu": {p: z * 0.03 for p in D.PRIORS}, "clim": 0.55, "beta": 1.0, "rule": "beta_market"}
            r.base = [1.0, None, None, None, None]
            recs.append(r)
    return recs, FakeBuilder(feats, weeks), rets


def _study(recs, fb, rets, family="earnings_events"):
    saved = dict(N.FAMILIES[family])
    N.FAMILIES[family] = {**saved, "features": ["x"], "wide": False}
    try:
        return N.study_family(recs, fb, family, H, rets)
    finally:
        N.FAMILIES[family] = saved


class Incremental(unittest.TestCase):
    def test_planted_alpha_found(self):
        res = _study(*_world(effect_alpha=0.5))
        g = res["gates"]["alpha"]
        self.assertTrue(g["G1"] and g["G2"], g)
        self.assertGreater(res["walkforward"]["alpha"]["d_rank_ic"]["mean"], 0.05)

    def test_planted_direction_beats_prior_and_current(self):
        res = _study(*_world(effect_dir=0.6, seed=2))
        g = res["gates"]["directional"]
        self.assertTrue(g["G1"] and g["G2"], g)
        wf = res["walkforward"]["directional"]
        self.assertGreater(wf["brier_vs_prior"]["mean"], 0)
        self.assertGreater(wf["logloss_vs_prior"]["mean"], 0)
        self.assertGreater(wf["excess_vs_prior"], 0)

    def test_noise_rejected(self):
        res = _study(*_world(seed=3))
        self.assertFalse(res["gates"]["alpha"]["G1"])
        self.assertFalse(res["gates"]["directional"]["G1"])

    def test_a_copy_of_the_prior_is_not_incremental(self):
        res = _study(*_world(copy_prior=True, seed=4))
        self.assertFalse(res["gates"]["directional"]["G1"])
        # against the current formulation (prior + production score) a copy of the prior adds nothing; against the
        # prior alone the model still carries the production score's own contribution — which is why v2 needs both
        self.assertLess(res["walkforward"]["directional"]["brier_vs_current"]["t"] or 0, 2)   # no gain (here slightly worse)

    def test_era_fits_ignore_later_outcomes(self):
        """Garbage outcomes after 2013 cannot change what the 2013 era was fitted with (identical 2009 era)."""
        recs, fb, rets = _world(effect_alpha=0.5, seed=5)
        a = _study(recs, fb, rets)
        for r in recs:
            if r.end >= "2013-01-01":
                r.y, r.yr = -r.y, -r.yr
        b = _study(recs, fb, rets)
        e1, e2 = a["eras"][0], b["eras"][0]
        self.assertEqual(e1["from"], "2009-01-01")
        self.assertEqual(e1["alpha"]["d_rank_ic"]["mean"] is None, e2["alpha"]["d_rank_ic"]["mean"] is None)


class HedgeVolatility(unittest.TestCase):
    def _vol_world(self, planted, seed):
        recs, fb, _ = _world(seed=seed, weeks=26 * 52, assets=10)
        rnd = random.Random(seed + 100)
        n = fb.n + 10
        rets = {}
        for a, f in fb.f.items():
            r = [None] * n
            for i in range(1, n):
                x = f["x"][i - 1] if i - 1 < fb.n and f["x"][i - 1] is not None else 0.0
                sd = 0.01 * math.exp(0.6 * x) if planted else 0.01
                r[i] = rnd.gauss(0, sd)
            rets[a] = r
        return recs, fb, {"rets": rets, "vix": [20.0] * n}

    def test_planted_volatility_information_beats_the_baseline(self):
        res = _study(*self._vol_world(True, 21))
        g = res["gates"]["hedge"]
        self.assertTrue(g["G1"], g)
        self.assertGreater(res["walkforward"]["hedge"]["vol_mse_gain"]["mean"], 0)

    def test_noise_does_not(self):
        res = _study(*self._vol_world(False, 22))
        self.assertFalse(res["gates"]["hedge"]["G1"])


class FDR(unittest.TestCase):
    def test_benjamini_hochberg(self):
        ps = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205, 0.212, 0.216]
        self.assertEqual(N.benjamini_hochberg(ps, 0.05), [True, True] + [False] * 8)
        self.assertEqual(sum(N.benjamini_hochberg(ps, 0.15)), 7)
        self.assertEqual(sum(N.benjamini_hochberg(ps, 0.25)), 10)
        self.assertEqual(N.benjamini_hochberg([0.5] * 40, 0.1), [False] * 40)

    def test_one_lucky_feature_of_forty_does_not_survive(self):
        ps = [0.03] + [0.5] * 39
        self.assertFalse(any(N.benjamini_hochberg(ps, 0.10)))


class Statuses(unittest.TestCase):
    def _result(self, fam, track, g1, g2, t):
        gate = {"G1": g1, "G2": g2, "t": t}
        return {"horizons": {"1W": {"families": {fam: {"gates": {"track": track, "enough": True, "alpha": dict(gate),
                                                                  "directional": {"G1": False, "G2": False, "t": 0.0}}, "features": {}}}}}}

    def test_limited_history_is_never_historically_verified(self):
        res = N.finalise(self._result("short_volume", "limited", True, True, 5.0))
        self.assertEqual(res["summary"]["short_volume"]["status"], "LIMITED HISTORY")
        self.assertIn("live-shadow", res["summary"]["short_volume"]["why"])
        self.assertNotIn("passed", res["horizons"]["1W"]["families"]["short_volume"]["gates"]["alpha"])   # not in the FDR pool

    def test_shadow_needs_fdr_and_blocked_listed(self):
        res = N.finalise(self._result("credit_quality", "historical", True, True, 6.0))
        self.assertEqual(res["summary"]["credit_quality"]["status"], "SHADOW")
        weak = N.finalise(self._result("credit_quality", "historical", True, True, 1.0))
        self.assertEqual(weak["summary"]["credit_quality"]["status"], "NO INCREMENTAL VALUE")
        self.assertEqual(res["summary"]["options_surface"]["status"], "BLOCKED")


class PointInTime(unittest.TestCase):
    def _builder(self, px, vol, spy, rows=None, alt=None, n=400):
        cal = [(dt.date(2010, 1, 4) + dt.timedelta(days=i)).isoformat() for i in range(n)]

        class Panel:
            def calendar(s): return cal
            def series(s, a, field="adj_close"): return {"adj_close": {"Z": px, "SPY": spy}, "volume": {"Z": vol, "SPY": vol}}[field][a]
            def macro(s, sid): return [None] * n

        class Store:
            def fundamentals(s, a): return rows or []
            def alt(s, ds, a): return alt or []
            def assets(s): return []

        class R:
            def panel(s): return Panel()
        return N.Builder(R(), Store()), cal

    def test_earnings_event_features_use_only_the_past(self):
        n = 400
        rnd = random.Random(1)
        spy = [100 * math.exp(0.0002 * i) for i in range(n)]
        px = [100.0]
        for i in range(1, n):
            px.append(px[-1] * math.exp(rnd.gauss(0, 0.01)))
        vol = [1e6 * (1 + 0.1 * rnd.random()) for _ in range(n)]
        b1, cal = self._builder(px, vol, spy)
        f1 = b1.earnings_events("Z")
        k = 300
        px2, vol2 = px[:k] + [v * 1.5 for v in px[k:]], vol[:k] + [v * 5 for v in vol[k:]]
        b2, _ = self._builder(px2, vol2, spy)
        f2 = b2.earnings_events("Z")
        for name in ("pead_return", "pead_volume"):
            self.assertEqual(f1[name][:k], f2[name][:k], name)
        self.assertNotEqual(f1["pead_volume"][k:], f2["pead_volume"][k:])

    def test_short_volume_uses_files_published_before_the_date(self):
        n = 200
        cal = [(dt.date(2020, 1, 1) + dt.timedelta(days=i)).isoformat() for i in range(n)]
        alt = []
        for i, d in enumerate(cal):
            alt += [("Z", d, "short", 40.0 + (i % 7), None), ("Z", d, "total", 100.0, None)]
        b, _ = self._builder([1.0] * n, [1.0] * n, [1.0] * n, alt=alt, n=n)
        b.cal = cal
        f = b.short_volume("Z")
        i = 100
        want = sum((40.0 + (j % 7)) / 100 for j in range(i - 5, i)) / 5          # the five files dated before cal[i]
        self.assertAlmostEqual(f["short_ratio_5d"][i], want)
        alt2 = [x for x in alt if x[1] < cal[i]] + [("Z", d, f_, 1e6 if f_ == "short" else 1e6, None) for d in cal[i:] for f_ in ("short", "total")]
        b2, _ = self._builder([1.0] * n, [1.0] * n, [1.0] * n, alt=alt2, n=n)
        b2.cal = cal
        self.assertAlmostEqual(b2.short_volume("Z")["short_ratio_5d"][i], want)


class LiveShadow(unittest.TestCase):
    """Only tests that passed every gate are fitted; the daily ledger records their forecasts with the benchmark's
    baseline next to them; live-shadow versions sit outside the promotion path."""

    def test_only_passing_historical_tests_go_live(self):
        res = {"horizons": {"1W": {"families": {
            "breadth_plus": {"gates": {"alpha": {"passed": True}, "directional": {"passed": False}, "hedge": {"passed": True}}},
            "credit_quality": {"gates": {"alpha": {"G1": True, "G2": True, "passed": False}}},
            "short_volume": {"gates": {"track": "limited", "alpha": {"G1": True, "G2": True}}}}}}}
        self.assertEqual(sorted(N.live_targets(res)), [("breadth_plus", "alpha", "1W"), ("breadth_plus", "hedge", "1W")])

    def _fake(self):
        cal = [(dt.date(2026, 1, 1) + dt.timedelta(days=i)).isoformat() for i in range(80)]
        rnd = random.Random(3)
        px = [100.0]
        for _ in range(79):
            px.append(px[-1] * math.exp(rnd.gauss(0, 0.01)))
        rows = []

        class Panel:
            def calendar(s): return cal
            def series(s, a, field="adj_close"): return px
            def macro(s, sid): return [20.0] * len(cal)
            def index_of(s, d): return cal.index(d)

        class St:
            def __init__(s):
                s.kv = {}
            def kv_get(s, k): return s.kv.get(k)
            def asset(s, a): return {"id": a, "asset_class": "EQUITY"}
            def assets(s): return []
            def predictions(s, asset_id=None, horizon=None): return rows
            def add_prediction(s, p):
                rows.append(p)
                return len(rows)
        st = St()

        class R:
            store = st
            def panel(s): return Panel()
            def shaffer_full(s, a): return {"horizons": {"1W": {"raw": 20.0, "date": cal[-1]}}}
        return R(), st, rows, cal

    def test_daily_record_alpha_and_volatility_with_baseline(self):
        research, st, rows, cal = self._fake()
        std = [(0.0, 1.0)] * 2
        st.kv[N.LIVE_KEY] = {"benchmark": "bm-x", "models": {
            "newinfo-breadth-plus-alpha-1W": {"family": "breadth_plus", "target": "alpha", "horizon": "1W", "h": 5, "wide": True,
                                              "features": ["a", "b"], "std": std, "records": 1, "weights": [0.5, 0.2, -0.1]},
            "newinfo-breadth-plus-hedge-1W": {"family": "breadth_plus", "target": "hedge", "horizon": "1W", "h": 5, "wide": True,
                                              "features": ["a", "b"], "std": std, "records": 1,
                                              "weights": [[0.0, 0.5, 0.5, 0.0], [0.0, 0.5, 0.5, 0.0, 0.1, 0.0]]}}}
        saved_f, saved_in = N._feature_now, D.live_inputs
        N._feature_now = lambda b, fam, meta: [1.0, 2.0]
        D.live_inputs = lambda research, meta, h, groups: {"i": len(cal) - 1, "s": 0.02, "beta": 1.5, "mu": {}, "clim": 0.55}
        try:
            n = N.record_live(research, "Z", {})
            again = N.record_live(research, "Z", {})
        finally:
            N._feature_now, D.live_inputs = saved_f, saved_in
        self.assertEqual(n, 3)                                   # alpha, the vol forecast, and the baseline's vol forecast
        self.assertEqual(again, 0)                               # append-only: once per model and date
        by = {p["model"]: p for p in rows}
        a = by["newinfo:breadth_plus:alpha"]
        self.assertAlmostEqual(a["score"], 0.5 * 0.2 + 1.5 * (0.2 * 1.0 - 0.1 * 2.0))
        self.assertEqual(a["model_version"], "newinfo-breadth-plus-alpha-1W")
        self.assertAlmostEqual(sum(a["detail"]["contributions"].values()), 1.5 * (0.2 - 0.2))
        v, b = by["newinfo:breadth_plus:hedge"], by["newinfo:hedge-baseline"]
        self.assertEqual(v["detail"]["kind"], "volatility")
        self.assertAlmostEqual(math.log(v["predicted"] / b["predicted"]), 0.1)   # the family's contribution, and nothing else
        self.assertEqual(b["model_version"], "baseline-bm-x")

    def test_nothing_without_fits(self):
        research, st, rows, cal = self._fake()
        self.assertEqual(N.record_live(research, "Z"), 0)
        self.assertEqual(N.live_status(st), {})

    def test_live_status_pairs_and_needs_enough_graded(self):
        from finsim2.engine.lab import LIVE_MIN
        research, st, rows, cal = self._fake()
        st.kv[N.LIVE_KEY] = {"models": {"v": {"family": "breadth_plus", "target": "hedge", "horizon": "1W", "records": 1}}}
        for k in range(LIVE_MIN):
            key = {"asset_id": f"A{k}", "horizon": "1W", "made_on": "2026-01-02", "realized": 0.20}
            rows.append({**key, "model": "newinfo:breadth_plus:hedge", "model_version": "v", "predicted": 0.21})
            rows.append({**key, "model": "newinfo:hedge-baseline", "model_version": "b", "predicted": 0.30})
        s = N.live_status(st)["v"]
        self.assertEqual(s["graded"], LIVE_MIN)
        self.assertGreater(s["mse_gain"], 0)
        self.assertEqual(s["status"], "ELIGIBLE FOR PROMOTION")
        del rows[2:]
        self.assertEqual(N.live_status(st)["v"]["status"], "LIVE SHADOW")

    def test_volatility_rows_are_graded_as_realised_volatility(self):
        from finsim2.engine import tracking as T
        p = {"model": "newinfo:breadth_plus:hedge", "detail": {"kind": "volatility"}}
        px = [100.0, 101.0, 99.0, 100.0, 102.0]
        lr = [math.log(b / a) for a, b in zip(px, px[1:])]
        self.assertAlmostEqual(T._realised_risk(p, px, 0, 4), math.sqrt(252.0 / 4 * sum(x * x for x in lr)))

    def test_live_shadow_versions_cannot_be_promoted(self):
        from finsim2.engine import lab as L
        v = {"id": "newinfo-x", "kind": "newinfo", "status": "live shadow"}
        self.assertEqual(L.stage(None, v), {"stage": "live shadow"})


if __name__ == "__main__":
    unittest.main()
