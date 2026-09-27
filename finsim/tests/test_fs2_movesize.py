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


if __name__ == "__main__":
    unittest.main()
