"""Model health: the pre-declared status rules, the date-clustered IC and the live expected-vs-realised record."""
import datetime as dt
import os
import random
import shutil
import tempfile
import unittest

from finsim2.data.store import Store
from finsim2.engine import health as HL


class Rules(unittest.TestCase):
    def test_status_order(self):
        self.assertEqual(HL.classify(10, True, 0.1, 0.1), "INSUFFICIENT DATA")
        self.assertEqual(HL.classify(100, False, 0.1, 0.1), "NO VERIFIED EDGE")
        self.assertEqual(HL.classify(100, True, 0.1, -0.01), "DECAYING")
        self.assertEqual(HL.classify(100, True, 0.1, 0.1, live_ic=-0.1, live_n=40), "DECAYING")
        self.assertEqual(HL.classify(100, True, 0.1, 0.1, live_ic=-0.1, live_n=10), "HEALTHY")       # too few live grades
        self.assertEqual(HL.classify(100, True, 0.1, 0.04), "WEAKENING")
        self.assertEqual(HL.classify(100, True, None, None, stable=False), "WEAKENING")
        self.assertEqual(HL.classify(100, True, 0.1, 0.08), "HEALTHY")


class ClusteredIC(unittest.TestCase):
    def _series(self, signal, assets=8, n=400, seed=1):
        rnd = random.Random(seed)
        d0 = dt.date(2010, 1, 4)
        dates = [(d0 + dt.timedelta(days=7 * i)).isoformat() for i in range(n)]
        common = [rnd.gauss(0, 1) for _ in range(n)]              # a shared factor: assets are NOT independent
        out = []
        for _ in range(assets):
            x = [rnd.gauss(0, 1) for _ in range(n)]
            y = [signal * a + c + rnd.gauss(0, 1) for a, c in zip(x, common)]
            out.append({"dates": dates, "x": x, "y": y})
        return out

    def test_signal_found_and_noise_not(self):
        s = HL.clustered_ic(self._series(0.3), 5)
        self.assertGreater(s["t"], 2)
        z = HL.clustered_ic(self._series(0.0, seed=2), 5)
        self.assertLess(abs(z["t"]), 2.5)

    def test_since_filter(self):
        s = HL.clustered_ic(self._series(0.3), 5, since="2016-01-01")
        full = HL.clustered_ic(self._series(0.3), 5)
        self.assertLess(s["weeks"], full["weeks"])


class Live(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.st = Store(os.path.join(self.tmp, "h.db"))

    def tearDown(self):
        self.st.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_expected_vs_realised(self):
        rnd = random.Random(3)
        for i in range(40):
            e = rnd.gauss(0.01, 0.02)
            pid = self.st.add_prediction({"asset_id": "SPY", "horizon": "1M", "model": "shaffer", "model_version": "t", "made_on": f"2020-01-{i % 28 + 1:02d}",
                                          "target_date": "2020-03-01", "predicted": e, "error_band": 0.05, "confidence": 0.5, "score": 10.0,
                                          "detail": {}, "range_lo": e - 0.05, "range_hi": e + 0.05})
            r = e + rnd.gauss(0, 0.01)
            self.st.score_prediction(pid, r, r - e, "2020-03-01", (e > 0) == (r > 0))
        self.st.add_prediction({"asset_id": "SPY", "horizon": "1M", "model": "shaffer", "model_version": "t", "made_on": "2020-02-01",
                                "target_date": "2020-04-01", "predicted": 0.01, "error_band": None, "confidence": None, "score": 1.0, "detail": {}})
        rec = HL.live_record(self.st, "shaffer", "1M")
        self.assertEqual((rec["graded"], rec["pending"]), (40, 1))
        self.assertGreater(rec["ic"], 0.5)
        self.assertGreater(rec["range_coverage"], 0.9)
        self.assertAlmostEqual(rec["mean_expected"], rec["mean_realized"], delta=0.01)
        self.assertEqual(HL.live_record(self.st, "ml_ensemble", "1M")["graded"], 0)


if __name__ == "__main__":
    unittest.main()


class RiskForecastGrading(unittest.TestCase):
    def test_volatility_and_drawdown_forecasts_are_graded_on_what_they_forecast(self):
        import math
        from finsim2.engine import tracking
        from finsim2.engine.research import Research
        from test_finsim2 import build_store
        tmp = tempfile.mkdtemp()
        try:
            st = build_store(os.path.join(tmp, "r.db"), n=400)
            panel = Research(st).panel()
            cal = panel.calendar()
            made = cal[-40]
            self.assertEqual(tracking.record_risk(st, panel, "SPY", made, "t", "1M", 21, 0.2, 0.3, 0.05), 2)
            self.assertEqual(tracking.record_risk(st, panel, "SPY", made, "t", "1M", 21, 0.2, 0.3, 0.05), 0)   # once per day
            tracking.score_matured(st, panel)
            rows = {p["model"]: p for p in st.predictions(asset_id="SPY") if p["model"] in tracking.RISK_MODELS}
            px = panel.series("SPY")
            i0, i1 = panel.index_of(made), panel.index_of(rows["ml_vol"]["target_date"])
            seg = [v for v in px[i0:i1 + 1] if v]
            lr = [math.log(b / a) for a, b in zip(seg, seg[1:])]
            self.assertAlmostEqual(rows["ml_vol"]["realized"], math.sqrt(252.0 / len(lr) * sum(x * x for x in lr)), places=10)
            self.assertIn(rows["ml_drawdown"]["realized"], (0.0, 1.0))
            self.assertAlmostEqual(rows["ml_drawdown"]["error"], rows["ml_drawdown"]["realized"] - 0.3)
        finally:
            st.close()
            shutil.rmtree(tmp, ignore_errors=True)
