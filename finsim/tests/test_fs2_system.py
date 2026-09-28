"""engine/system.py: the Shaffer System learner — planted effects by asset class, adoption rule, forecast distribution."""
import datetime as dt
import math
import os
import random
import shutil
import tempfile
import unittest

from finsim2.data.store import Store
from finsim2.engine import system as S


def _dates(start="1995-01-02", step_days=30, n=380):
    d0 = dt.date.fromisoformat(start)
    return [(d0 + dt.timedelta(days=step_days * k)).isoformat() for k in range(n)]


class Learner(unittest.TestCase):
    """Equities: y = base + 0.04 · momentum + noise. ETFs: pure noise around the base. 1M records."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.st = Store(os.path.join(cls.tmp, "x.db"))
        rng = random.Random(4)
        dates = _dates()
        for k in range(40):
            eq = k < 24
            aid = f"S{k}" if eq else f"E{k}"
            cls.st.upsert_asset({"id": aid, "name": aid, "asset_class": "EQUITY" if eq else "ETF", "sector": ["Tech", "Utilities"][k % 2] if eq else "Broad Market",
                                 "country": "United States", "currency": "USD", "yahoo": aid, "meta": {"industry": "Semis" if k % 4 == 0 else None}})
            rows = []
            for i, d in enumerate(dates[:-1]):
                mom = max(-1.0, min(1.0, rng.gauss(0, 0.5)))
                fam = [mom] + [max(-1, min(1, rng.gauss(0, 0.4))) for _ in range(10)]
                base = 0.006
                y = base + (0.04 * mom if eq else 0.0) + rng.gauss(0, 0.05)
                rows.append([d, dates[i + 1], round(y, 6), round(base + rng.gauss(0, 0.04), 6), base, rng.gauss(0, 30), math.log(1.0), math.log(1.0),
                             *[round(v, 4) for v in fam], 0.0, 1.0, 0.0, 2.0])
            cls.st.put_lab_records(aid, "1M", S.SYS_VERSION, "t", rows)
        cls.res = S.study_horizon(cls.st, "1M")

    @classmethod
    def tearDownClass(cls):
        cls.st.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_planted_class_learns_and_the_noise_class_falls_back(self):
        eq, etf = self.res["classes"]["EQUITY"], self.res["classes"]["ETF"]
        self.assertGreater(eq["gain"]["t"], 2.0, eq["gain"])
        self.assertEqual(eq["adopted"], "learned")
        fin = self.res["final"]["classes"]
        self.assertIn(fin["EQUITY"]["family"], ("E2", "E3", "E4"))
        self.assertGreater(eq["rank_ic"]["mean"], 0.2)
        self.assertLess(abs(etf["gain"]["mean"] or 0), 1e-3, "no effect to find in the ETFs")
        self.assertTrue(0.8 <= eq["calibration"]["slope"] <= 1.3, eq["calibration"])

    def test_ranges_are_calibrated_out_of_sample(self):
        cov = self.res["classes"]["EQUITY"]["coverage"]
        # the planted noise is below a random walk's, so the 1M floor on the residual scale makes the ranges
        # conservative: at or above nominal coverage, never grossly wide
        self.assertTrue(0.88 <= cov["r90"] <= 0.98, cov)
        self.assertTrue(0.46 <= cov["r50"] <= 0.66, cov)
        self.assertEqual(S.scale_c({"c": 0.8}, 21), 1.0)
        self.assertEqual(S.scale_c({"c": 0.8}, 5), 0.8, "no floor below 1M: short horizons are well identified")
        self.assertEqual(S.scale_c({"c": 1.3}, 252), 1.3)

    def test_live_forecast_from_the_spec(self):
        spec = {"version": S.SYS_VERSION, "hash": "x" * 64, "group_mean": {}, "horizons": {"1M": {"classes": self.res["final"]["classes"],
                                                                                                    "fits": self.res["final"]["fits"]}}}
        a = self.st.asset("S0")
        inp = {"date": "2026-09-25", "fam": [0.8] + [0.0] * 10, "logvol": 0.0, "logrel": 0.0, "own_sum": 0.0003 * 5000, "own_n": 5000,
               "vix": 0.0, "term": 1.0, "credit": 0.0, "rf": 2.0, "bench": "SPY"}
        up = S.forecast(spec, a, inp, {"1M": 10.0}, bench_mu={"1M": 0.006})["1M"]
        inp2 = dict(inp, fam=[-0.8] + [0.0] * 10)
        dn = S.forecast(spec, a, inp2, {"1M": 10.0}, bench_mu={"1M": 0.006})["1M"]
        self.assertGreater(up["expected"], dn["expected"])
        self.assertGreater(up["p_beat"], dn["p_beat"])
        for f in (up, dn):
            self.assertGreater(f["expected"], -1.0)
            self.assertGreater(f["expected"], f["median"], "the mean is above the median (convexity)")
            self.assertLess(f["range90"][0], f["range50"][0])
            self.assertLess(f["range50"][1], f["range90"][1])
            self.assertAlmostEqual(f["score"], 100 * f["expected"])
        self.assertTrue(any(c["name"] == "Momentum" for c in up["positive"]))


class Pieces(unittest.TestCase):
    def test_distribution_respects_the_total_loss_bound(self):
        rm = {"c": 1.0, "q": [(-3 + 6 * (k + 0.5) / S.NQ) for k in range(S.NQ)]}
        d = S.distribution(-8.0, 2.0, rm)
        self.assertGreater(d["expected"], -1.0)
        self.assertGreater(d["median"], -1.0)
        self.assertLess(d["p_pos"], 0.01)

    def test_paths_and_benchmarks(self):
        nvda = {"id": "NVDA", "asset_class": "EQUITY", "sector": "Information Technology", "country": "United States",
                "meta": {"industry": "Semiconductors & Related Devices"}}
        p = S.node_path(nvda)
        self.assertEqual(p[0], "global")
        self.assertEqual(p[-1], "c:EQUITY|p:US common|s:Information Technology|i:Semiconductors & Related Devices|a:NVDA")
        self.assertEqual(S.benchmark_of(nvda), "SPY")
        self.assertEqual(S.benchmark_of({"id": "TLT", "asset_class": "ETF", "sector": "Fixed Income"}), "AGG")
        self.assertEqual(S.benchmark_of({"id": "USO", "asset_class": "ETF", "sector": "Energy", "name": "United States Oil Fund"}), "DBC")
        self.assertIsNone(S.benchmark_of({"id": "EURUSD", "asset_class": "FX", "sector": "Currency"}))
        noind = S.node_path({"id": "X", "asset_class": "EQUITY", "sector": "Utilities", "meta": {}})
        self.assertEqual(noind[3], noind[4], "a missing industry repeats the sector node")

    def test_elastic_net_selects(self):
        G = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        w = S._en(G, [0.1, 0.5, 0.01], [0.0, 0.0, 0.0], 0.05, 0.5)
        self.assertAlmostEqual(w[0], 0.1)
        self.assertGreater(w[1], 0.4)
        self.assertEqual(w[2], 0.0, "a weak signal is dropped")


class Instruments(unittest.TestCase):
    SYS = {"1M": {"mu_log": 0.01, "sigma_log": 0.08}, "12M": {"mu_log": 0.12, "sigma_log": 0.28}}

    def test_interpolation(self):
        from finsim2.engine import instruments as I
        mu, s = I.forecast_at(self.SYS, 21)
        self.assertAlmostEqual(mu, 0.01)
        mu, s = I.forecast_at(self.SYS, 252)
        self.assertAlmostEqual(s, 0.28)
        mu, s = I.forecast_at(self.SYS, 136)
        self.assertTrue(0.01 < mu < 0.12 and 0.08 < s < 0.28)
        self.assertAlmostEqual(I.forecast_at(self.SYS, 504)[0], 0.24, msg="beyond the last horizon: linear in time")

    def test_option_payoffs_obey_parity_under_the_forecast(self):
        from finsim2.engine import instruments as I
        S, K, mu, s = 100.0, 105.0, 0.05, 0.2
        c, p = I.option_expected_payoff(S, K, "C", mu, s), I.option_expected_payoff(S, K, "P", mu, s)
        self.assertAlmostEqual(c - p, S * math.exp(mu + s * s / 2) - K, places=9)
        self.assertGreater(c, max(0.0, S * math.exp(mu + s * s / 2) - K))

    def test_linear_instruments(self):
        from finsim2.engine import instruments as I

        class Inst:
            id, type, underlying, expiry, spec, multiplier, strike, right = "FUT:ES:Z26", "FUTURE", "SPY", "2026-12-18", {"kind": "equity"}, 50.0, None, None

        class Pr:
            price = 6000.0
            def unit_notional(self): return 300000.0
            def unit_value(self): return 0.0
        x = I.position_expected_pnl(Inst(), Pr(), 2, self.SYS, 21, "2026-09-25")
        er = math.exp(0.01 + 0.5 * 0.08 ** 2) - 1
        self.assertAlmostEqual(x["expected_pnl"], 2 * 300000.0 * er)
        short = I.position_expected_pnl(Inst(), Pr(), -2, self.SYS, 21, "2026-09-25")
        self.assertAlmostEqual(short["expected_pnl"], -x["expected_pnl"])
        self.assertIn("no Shaffer", I.position_expected_pnl(Inst(), Pr(), 1, {}, 21, "2026-09-25")["note"])


if __name__ == "__main__":
    unittest.main()
