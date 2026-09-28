"""SHAFFER_IV_PROTOCOL.md machinery: the batch-iv families (point in time) and the move-size IV study (exact fit,
planted effect found, missing data declared, never invented)."""
import datetime as dt
import math
import os
import random
import shutil
import tempfile
import unittest
from array import array

from finsim2.data.store import Store
from finsim2.engine import ivstudy as IV
from finsim2.engine import movesize as MS
from finsim2.engine import newinfo as N


class _Panel:
    def __init__(self, cal, px, macro=None):
        self.cal, self.px, self.m = cal, px, macro or {}

    def calendar(self):
        return self.cal

    def series(self, a, field="adj_close"):
        return list(self.px[a]) if a in self.px else [None] * len(self.cal)

    def macro(self, sid, max_age_days=400):
        return list(self.m.get(sid, [None] * len(self.cal)))


class _Research:
    def __init__(self, p):
        self._p = p

    def panel(self):
        return self._p


class Families(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.st = Store(os.path.join(self.tmp, "x.db"))
        d0 = dt.date(2010, 1, 4)
        self.cal = [(d0 + dt.timedelta(days=k)).isoformat() for k in range(0, 700) if (d0 + dt.timedelta(days=k)).weekday() < 5]
        rng = random.Random(3)
        px, p = [], 100.0
        for _ in self.cal:
            p *= math.exp(rng.gauss(0, 0.01))
            px.append(p)
        self.px = {"SPY": px, "USO": px, "AAPL": px}

    def tearDown(self):
        self.st.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _b(self, macro=None):
        return N.Builder(_Research(_Panel(self.cal, self.px, macro)), self.st)

    def test_own_iv_and_term_structure(self):
        n = len(self.cal)
        b = self._b({"VIXCLS": [16.0] * n, "VXVCLS": [20.0] * n})
        f = b.iv_index("SPY")
        rv = b.rv21("SPY")[100]
        self.assertAlmostEqual(f["iv_rv_log"][100], math.log(0.16) - math.log(rv))
        self.assertAlmostEqual(f["iv_chg_5d"][100], 0.0)
        self.assertAlmostEqual(b.vol_term()["vix_vix3m_log"][100], math.log(16 / 20))
        self.assertIsNone(b.vol_cboe()["vvix_z"][100], "no Cboe CSVs stored: missing, not invented")
        self.assertTrue(N.applies("iv_index", {"id": "SPY", "asset_class": "ETF"}))
        self.assertFalse(N.applies("iv_index", {"id": "XOM", "asset_class": "EQUITY"}))

    def test_chain_features_visible_from_publication(self):
        d, pub = self.cal[300], self.cal[301]
        self.st.put_alt("options_hist", [("AAPL", d, "iv30", 0.25, pub), ("AAPL", d, "iv90", 0.30, pub), ("AAPL", d, "skew25", 0.04, pub),
                                         ("AAPL", d, "put_oi", 999.0, pub), ("AAPL", d, "call_oi", 499.0, pub)])
        self.assertTrue(all(v is None for v in self._b().iv_chain("AAPL")["chain_term"]), "no quality gate passed: not used")
        self.st.kv_set("optionsdump:quality", {"passed": True})
        f = self._b().iv_chain("AAPL")
        i = self.cal.index(pub)
        self.assertIsNone(f["chain_term"][i - 1], "not visible on the session itself")
        self.assertAlmostEqual(f["chain_term"][i], math.log(0.30 / 0.25))
        self.assertAlmostEqual(f["chain_pc_oi"][i], math.log(1000 / 500))
        self.assertEqual(f["chain_skew25"][i + 4], 0.04)
        self.assertIsNone(f["chain_skew25"][i + 5], "held at most five sessions")

    def test_futures_roll_yield_uses_contract_months(self):
        d, pub = self.cal[200], self.cal[201]
        self.st.put_alt("futures_curve", [("fut:WTI", d, "c1", 90.0, pub), ("fut:WTI", d, "c2", 88.0, pub), ("fut:WTI", d, "c4", 84.0, pub),
                                          ("fut:WTI", d, "c1_ym", 202611.0, pub), ("fut:WTI", d, "c2_ym", 202612.0, pub),
                                          ("fut:WTI", d, "c4_ym", 202702.0, pub)])
        f = self._b().futures_curve("USO")
        i = self.cal.index(pub)
        self.assertAlmostEqual(f["roll_1_2"][i], math.log(90 / 88) * 12, msg="backwardation: positive roll yield")
        self.assertAlmostEqual(f["roll_1_4"][i], math.log(90 / 84) * 12 / 3)
        self.assertIsNone(f["roll_1_2"][i - 1])
        self.assertEqual(N.CURVE_OF["USO"], "WTI")


def _records(n_dates=1300, n_assets=20, iv_assets=16, seed=5):
    """1W-like records: true log vol is known to the own-IV feature (noisy) better than to trailing vol (noisier)."""
    rng = random.Random(seed)
    d0 = dt.date(2003, 1, 6)
    out = []
    for t in range(n_dates):
        d = (d0 + dt.timedelta(days=7 * t)).isoformat()
        end = (d0 + dt.timedelta(days=7 * t + 7)).isoformat()
        for a in range(n_assets):
            lv = math.log(0.01) + 0.5 * rng.gauss(0, 1)
            x = array("d", [float("nan")] * len(MS.ALL))
            x[MS.IDX["log_s21"]] = lv + rng.gauss(0, 0.6)
            e = array("d", [float("nan")] * IV.NE)
            if a < iv_assets:
                e[IV.EIDX["log_iv"]] = lv + rng.gauss(0, 0.2)
            e[IV.EIDX["vix_vix3m_log"]] = rng.gauss(0, 0.1)               # pure noise, every record
            rets = [rng.gauss(0, math.exp(lv)) for _ in range(5)]
            r2 = sum(v * v for v in rets) / 5
            base = MS.Rec(f"A{a}", d, end, x, math.log(max(math.sqrt(r2), MS.FLOOR)), r2, sum(rets), 5)
            out.append(IV.R(base, e))
    return out


class Study(unittest.TestCase):
    def test_sufficient_statistics_fit_equals_direct_least_squares(self):
        rows = _records(400, 20, 10)
        stats = IV.year_stats(rows)
        m = IV.fit(stats, 9999, ["log_iv"], rows)
        # direct ridge OLS on [1, base (missing 0), log_iv − mean (missing = mean)]
        present = [r.e[IV.EIDX["log_iv"]] for r in rows if r.e[IV.EIDX["log_iv"]] == r.e[IV.EIDX["log_iv"]]]
        mu = sum(present) / len(present)
        X = []
        for r in rows:
            b = [0.0 if v != v else v for v in r.x]
            z = r.e[IV.EIDX["log_iv"]]
            X.append([1.0] + b + [0.0 if z != z else z - mu])
        p = len(X[0])
        A = [[sum(x[i] * x[j] for x in X) + (MS.LAMBDA if i == j and i else 0.0) for j in range(p)] for i in range(p)]
        bb = [sum(x[i] * r.y for x, r in zip(X, rows)) for i in range(p)]
        from finsim2.engine.alphahz import _solve
        w = _solve(A, bb)
        self.assertEqual(m["used"], ["log_iv"])
        for u, v in zip(m["w"], w):
            self.assertAlmostEqual(u, v, places=6)

    def test_planted_iv_effect_is_found_and_absent_data_is_declared(self):
        rows = _records()
        res = IV.study({"1D": [], "1W": rows, "1M": []})
        hz = res["horizons"]["1W"]
        v1, v2, v3, v5 = (hz["models"][k] for k in ("V1", "V2", "V3", "V5"))
        self.assertEqual(v1["subset"], "iv")
        self.assertGreater(v1["gain"]["mean"], 0.0)
        self.assertGreater(v1["gain"]["t"], 2.0)
        self.assertLess(v1["width_ratio"], 1.0, "sharper ranges")
        self.assertTrue(0.85 <= v1["coverage_90"] <= 0.95, v1["coverage_90"])
        self.assertLess(abs(v2["gain"]["t"] or 0), 3.0, "noise feature: no reliable gain")
        self.assertEqual(v3["status"], "INSUFFICIENT DATA", "no dump rows")
        self.assertEqual(v5["status"], "INSUFFICIENT DATA", "no Cboe CSV values")
        self.assertEqual(v1["status"], "PASSED", v1.get("gates"))
        self.assertEqual(res["horizons"]["1D"]["models"]["V1"]["status"], "INSUFFICIENT DATA")


if __name__ == "__main__":
    unittest.main()
