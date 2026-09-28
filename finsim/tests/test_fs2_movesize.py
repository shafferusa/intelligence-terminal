"""engine/movesize.py: 1D – 1W move-size forecasts — planted event effects, calibration, point in time."""
import math
import random
import unittest
from array import array

from finsim2.engine import movesize as M


def _rows(n=24000, event_mult=2.0, seed=5):
    """Daily returns with σ = base level (from log_s21) × event_mult on announced macro-release days."""
    rng = random.Random(seed)
    out = []
    for k in range(n):
        y0 = 2001 + k * 24 // n
        d = f"{y0}-{(k % 12) + 1:02d}-{(k % 27) + 1:02d}"
        s21 = math.exp(rng.gauss(math.log(0.012), 0.3))
        ev = 1.0 if rng.random() < 0.15 else 0.0
        sig = s21 * (event_mult if ev else 1.0)
        ret = rng.gauss(0, sig)
        x = [math.nan] * len(M.ALL)
        for f, v in (("log_s5", math.log(s21)), ("log_s21", math.log(s21)), ("log_s63", math.log(s21)), ("log_vix", math.log(20.0)),
                     ("log_abs_r", math.log(0.01)), ("macro_next", ev), ("fomc_next", 0.0)):
            x[M.IDX[f]] = v
        for c in M.CLASSES[1:]:
            x[M.IDX[f"cls_{c}"]] = 0.0
        out.append(M.Rec("A", d, d, array("d", x), math.log(max(abs(ret), M.FLOOR)), ret * ret, ret, 1))
    return out


class MoveSize(unittest.TestCase):
    def test_planted_event_volatility_is_found_and_ranges_are_calibrated(self):
        rows = _rows()
        train = [r for r in rows if r.end < "2017-01-01"]
        test = [r for r in rows if r.d >= "2017-01-01"]
        st = M.year_stats(rows)
        m0 = M.fit(st, 2017, M.MODELS["M0"], train)
        m1 = M.fit(st, 2017, M.MODELS["M1"], train)
        self.assertGreater(m1["w"][1 + M.MODELS["M1"].index("macro_next")], 0.4, "log σ rises by ~log 2 on event days")
        q0 = sum(M.qlike(r.r2, M.predict(m0, r)) for r in test) / len(test)
        q1 = sum(M.qlike(r.r2, M.predict(m1, r)) for r in test) / len(test)
        self.assertLess(q1, q0 - 0.02)
        cover = sum(1 for r in test if m1["q"][0] * math.sqrt(M.predict(m1, r)) <= r.ret <= m1["q"][1] * math.sqrt(M.predict(m1, r))) / len(test)
        self.assertTrue(0.87 <= cover <= 0.93, cover)

    def test_no_event_effect_no_gain(self):
        rows = _rows(event_mult=1.0, seed=9)
        train = [r for r in rows if r.end < "2017-01-01"]
        test = [r for r in rows if r.d >= "2017-01-01"]
        st = M.year_stats(rows)
        m0 = M.fit(st, 2017, M.MODELS["M0"], train)
        m1 = M.fit(st, 2017, M.MODELS["M1"], train)
        q0 = sum(M.qlike(r.r2, M.predict(m0, r)) for r in test) / len(test)
        q1 = sum(M.qlike(r.r2, M.predict(m1, r)) for r in test) / len(test)
        self.assertLess(abs(q1 - q0), 0.01)

    def test_training_only_uses_matured_years(self):
        rows = _rows(n=4000)
        st = M.year_stats(rows)
        self.assertTrue(all(y < 2030 for y in st))
        self.assertIsNone(M.fit(st, 2002, M.MODELS["M0"], [r for r in rows if r.end < "2002-01-01"]), "too few records before 2002")

    def test_qlike_is_zero_at_the_truth(self):
        self.assertAlmostEqual(M.qlike(0.0004, 0.0004), 0.0)
        self.assertGreater(M.qlike(0.0004, 0.0001), 0.0)
        self.assertGreater(M.qlike(0.0001, 0.0004), 0.0)


class LiveLayer(unittest.TestCase):
    """The app's move-size layer: weekly frozen fit, today's forecast, the asset view and the refit schedule."""

    def setUp(self):
        import datetime as dt
        import os
        import tempfile
        from finsim2.data.store import Store
        self.tmp = tempfile.mkdtemp()
        self.st = Store(os.path.join(self.tmp, "x.db"))
        days, d = [], dt.date(2001, 1, 2)
        while len(days) < 2000:
            if d.weekday() < 5:
                days.append(d.isoformat())
            d += dt.timedelta(days=1)
        self.days = days
        rng = random.Random(3)
        self.vol = {}
        for k in range(14):
            aid = "SPY" if k == 0 else f"S{k}"
            sig = 0.006 if k < 7 else 0.03                       # two volatility levels
            self.vol[aid] = sig
            self.st.upsert_asset({"id": aid, "name": aid, "asset_class": "ETF" if k == 0 else "EQUITY", "sector": "Tech",
                                  "country": "United States", "currency": "USD", "yahoo": aid, "meta": {}})
            px, rows = 100.0, []
            for day in days:
                px *= math.exp(rng.gauss(0, sig))
                rows.append({"date": day, "open": px, "high": px, "low": px, "close": px, "adj_close": px, "volume": 1e6})
            self.st.upsert_prices(aid, rows)
        self.st.upsert_macro("VIXCLS", [(day, 20.0, day) for day in days])

    def tearDown(self):
        import shutil
        self.st.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fit_forecast_view_and_schedule(self):
        from finsim2.engine.research import Research
        r = Research(self.st)
        self.assertTrue(M.due(self.st, self.days[-1]), "no fit yet")
        self.assertEqual(M.asset_view(self.st, r, self.st.asset("S1"))["available"], False)
        live = M.fit_live(self.st, r)
        self.assertEqual(set(live["horizons"]), {"1D", "1W"})
        self.assertEqual(live["horizons"]["1D"]["model"], "M3")
        self.assertFalse(M.due(self.st, self.days[-1]))
        self.assertTrue(M.due(self.st, "2099-01-01"), "a week-old fit is refitted")
        view = M.today_live(self.st, r)
        self.assertEqual(len(view["items"]), 14)
        lo = view["items"]["S1"]["horizons"]["1D"]
        hi = view["items"]["S10"]["horizons"]["1D"]
        self.assertAlmostEqual(lo["sigma"], 0.006, delta=0.003)
        self.assertAlmostEqual(hi["sigma"], 0.03, delta=0.012)
        self.assertLess(lo["lo"], 0.0)
        self.assertGreater(lo["hi"], 0.0)
        wk = view["items"]["S10"]["horizons"]["1W"]
        self.assertAlmostEqual(wk["sigma"] / hi["sigma"], math.sqrt(5), delta=0.8)
        v = M.asset_view(self.st, r, self.st.asset("S10"))
        self.assertTrue(v["available"])
        x = v["horizons"]["1D"]
        self.assertAlmostEqual(x["move_pct"], math.expm1(x["sigma"]))
        self.assertIsNotNone(x["p_up"])
        self.assertFalse(x["p_up_calibrated"], "no lab:directional:live fit here: the uncalibrated prior is labelled")
        self.assertTrue(0.0 < x["p_up"] < 1.0)

    def test_frozen_fit_is_used_until_refit(self):
        from finsim2.engine.research import Research
        r = Research(self.st)
        M.fit_live(self.st, r)
        f1 = M.forecast(self.st, r, self.st.asset("S3"))
        live = self.st.kv_get(M.LIVE_KEY)
        live["horizons"]["1D"]["k"] *= 4.0                          # a different frozen level doubles σ
        self.st.kv_set(M.LIVE_KEY, live)
        f2 = M.forecast(self.st, r, self.st.asset("S3"))
        self.assertAlmostEqual(f2["horizons"]["1D"]["sigma"], 2 * f1["horizons"]["1D"]["sigma"], places=9)


if __name__ == "__main__":
    unittest.main()
