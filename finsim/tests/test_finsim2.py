"""FinSim2 v2, the quantitative evidence engine: horizons, scores, the portfolio ledger, the optimiser, Monte Carlo,
scenarios, backtests, tracking and the HTTP routes, on a small synthetic research store (no network)."""
import datetime as dt
import math
import os
import random
import shutil
import tempfile
import unittest
import unittest.mock

from finsim.world import CommandError
from finsim2 import shaffer_score
from finsim2.data.store import Store
from finsim2.engine import horizons as hz
from finsim2.engine import montecarlo, optimize, scores
from finsim2.engine.backtest import run as run_backtest
from finsim2.engine.signals import standardize
from finsim2.server import App, NotFound, Router

ASSETS = {"SPY": (0.0004, 0.011), "QQQ": (0.0005, 0.014), "TLT": (0.0001, 0.009), "GOLD": (0.0002, 0.010), "EURUSD": (0.0, 0.005),
          "IWM": (0.0003, 0.014), "EFA": (0.0002, 0.011), "HYG": (0.0001, 0.004), "LQD": (0.0001, 0.005), "DXY": (0.0, 0.005)}


def business_days(start: str, n: int):
    d = dt.date.fromisoformat(start)
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def build_store(path: str, n: int = 1800) -> Store:
    rnd = random.Random(7)
    s = Store(path)
    days = business_days("2016-01-04", n)
    common = [rnd.gauss(0, 1) for _ in days]
    for k, (mu, vol) in ASSETS.items():
        p, rows = 100.0, []
        for i, d in enumerate(days):
            e = 0.6 * common[i] + 0.8 * rnd.gauss(0, 1)
            p *= math.exp(mu + vol * e)
            rows.append({"date": d, "open": p, "high": p * 1.004, "low": p * 0.996, "close": p, "adj_close": p, "volume": 1e6})
        s.upsert_prices(k, rows)
    months = [d for i, d in enumerate(days) if i == 0 or d[:7] != days[i - 1][:7]]
    s.upsert_macro("DGS10", [(d, 2.0 + 0.5 * math.sin(i / 200)) for i, d in enumerate(days)])
    s.upsert_macro("DGS2", [(d, 1.5 + 0.5 * math.sin(i / 180)) for i, d in enumerate(days)])
    s.upsert_macro("DGS3MO", [(d, 1.0) for d in days])
    s.upsert_macro("VIXCLS", [(d, 15 + 5 * math.sin(i / 90)) for i, d in enumerate(days)])
    s.upsert_macro("CPIAUCSL", [(d, 250 * (1.002 ** i)) for i, d in enumerate(months)])
    s.upsert_macro("UNRATE", [(d, 4.0 + 0.3 * math.sin(i / 7)) for i, d in enumerate(months)])
    return s


class Evidence(unittest.TestCase):
    """The horizon engine finds a planted relation, rejects noise, and never uses the future."""

    def setUp(self):
        rnd = random.Random(3)
        n, self.h = 3000, 21
        r = [rnd.gauss(0.0003, 0.01) for _ in range(n)]
        self.price = [100.0]
        for x in r[1:]:
            self.price.append(self.price[-1] * math.exp(x))
        fwd = [math.log(self.price[i + self.h] / self.price[i]) if i + self.h < n else None for i in range(n)]
        raw_good = [(f if f is not None else 0.0) + rnd.gauss(0, 0.06) for f in fwd]
        self.good = standardize(raw_good)
        self.noise = standardize([rnd.gauss(0, 1) for _ in range(n)])
        self.target = fwd

    def test_planted_signal_is_found_and_noise_is_not(self):
        g = hz.evaluate(self.good, self.target, self.h)
        z = hz.evaluate(self.noise, self.target, self.h)
        self.assertGreater(g["ic"], 0.2)
        self.assertLess(g["p"], 0.01)
        self.assertLess(abs(z["ic"]), 0.1)
        self.assertGreater(z["p"], 0.01)
        self.assertLessEqual(g["n_eff"], g["n"])            # overlapping h-day targets are not independent

    def test_cutoff_ignores_later_rows(self):
        cut = 1500
        a = hz.evaluate(self.good, self.target, self.h, cutoff=cut)
        tampered = list(self.target[:cut]) + [-x if x is not None else None for x in self.target[cut:]]
        b = hz.evaluate(self.good, tampered, self.h, cutoff=cut)
        self.assertAlmostEqual(a["ic"], b["ic"], places=12)

    def test_multiple_testing_and_stats(self):
        q = hz.bh_qvalues({"a": 0.001, "b": 0.02, "c": 0.5, "d": None})
        self.assertIsNone(q["d"])
        self.assertLessEqual(q["a"], q["b"])
        self.assertGreaterEqual(q["b"], 0.02)
        self.assertAlmostEqual(hz.t_pvalue(0.0, 30), 1.0, places=6)
        self.assertAlmostEqual(hz.t_pvalue(2.042, 30), 0.05, places=2)

    def test_standardize_is_capped_and_point_in_time(self):
        xs = [float(i % 7) for i in range(400)] + [1e6]
        z = standardize(xs)
        self.assertEqual(max(v for v in z if v is not None), 3.0)
        self.assertEqual(standardize(xs[:300])[:300], z[:300])  # later data never changes earlier z-scores

    def test_score_is_bounded_and_gated_by_evidence(self):
        items = [{"z": 3.0, "weight": 0.2, "usefulness": 0.2, "n_eff": 300}, {"z": 2.5, "weight": 0.1, "usefulness": 0.1, "n_eff": 300}]
        s = scores.quant_score(items)
        self.assertTrue(0 < s <= 100)
        few = [dict(i, n_eff=6) for i in items]                 # the same readings on six independent windows
        self.assertAlmostEqual(scores.quant_score(few), s * 6 / 30, places=6)
        weak = [{"z": 3.0, "weight": 0.001, "usefulness": 0.001, "n_eff": 300}]
        self.assertLess(abs(scores.quant_score(weak)), 5)       # a strong reading with no evidence scores near zero
        self.assertIsNone(scores.quant_score([]))


class Engines(unittest.TestCase):
    def test_optimiser_respects_the_simplex_and_cap(self):
        cov = [[0.04, 0.01, 0.0], [0.01, 0.09, 0.0], [0.0, 0.0, 0.01]]
        out = optimize.optimise(["A", "B", "C"], [0.08, 0.10, 0.03], cov, {"A": 1.0, "B": 1.0}, cap=0.6)
        for p in out["portfolios"]:
            w = p["weights"]
            self.assertAlmostEqual(sum(w.values()), 1.0, places=6)
            self.assertTrue(all(-1e-9 <= v <= 0.6 + 1e-6 for v in w.values()) or p["name"] in ("Current", "Risk parity", "Equal weight"))
        rp = next(p for p in out["portfolios"] if p["name"] == "Risk parity")
        rc = list(rp["risk_contributions"].values())
        self.assertLess(max(rc) - min(rc), 0.02)

    def test_monte_carlo_is_reproducible_and_ordered(self):
        rnd = random.Random(1)
        daily = [rnd.gauss(0.0003, 0.01) for _ in range(1500)]
        a = montecarlo.simulate(daily, 252, 500, "bootstrap", start_value=100.0)
        b = montecarlo.simulate(daily, 252, 500, "bootstrap", start_value=100.0)
        self.assertEqual(a, b)
        vals = [a[k] for k in ("p5", "p25", "median", "p75", "p95")]
        self.assertEqual(vals, sorted(vals))
        self.assertTrue(0.0 <= a["prob_loss"] <= 1.0)

    def test_backtest_long_only_and_costs(self):
        days = business_days("2018-01-01", 600)
        price = [100 * (1.001 ** i) for i in range(600)]
        sig = [2.0 if (i // 50) % 2 == 0 else -2.0 for i in range(600)]
        spec = {"entry": 1.0, "exit": 0.0, "mode": "long", "min_hold": 1, "cost_bps": 0.0, "direction_sign": 1}
        free = run_backtest(days, price, sig, spec, {}, price)
        costly = run_backtest(days, price, sig, {**spec, "cost_bps": 50.0}, {}, price)
        self.assertGreater(free["metrics"]["total_return"], costly["metrics"]["total_return"])
        self.assertGreater(free["metrics"]["total_return"], 0)
        self.assertLess(free["metrics"]["exposure"], 0.7)


class Routes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.store = build_store(os.path.join(cls.tmp, "research.db"))
        cls.app = App(cls.store)
        cls.r = Router(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.store.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def get(self, path, **q):
        return self.r.dispatch("GET", "/api/fs2/" + path, {k: [v] for k, v in q.items()}, {})

    def post(self, path, body):
        return self.r.dispatch("POST", "/api/fs2/" + path, {}, body)

    def test_status_equations_features_and_shaffer_slot(self):
        st = self.get("status")
        self.assertIn("data_version", st)
        cat = self.get("equations")
        ids = {e["id"] for e in cat["equations"]}
        self.assertTrue({str(i) for i in range(1, 119)} <= ids)
        self.assertIn("def ", self.get("equations/101/source")["source"])
        feats = self.get("features")
        self.assertIn("mom_12_1", feats)
        self.assertEqual(self.get("shaffer")["state"], shaffer_score.status()["state"])

    def test_asset_bundle_has_the_standard_output(self):
        b = self.get("asset/SPY")
        self.assertEqual(set(b["horizons"]) >= {"1D", "1W", "1M", "3M", "6M", "12M"}, True)
        for hs in b["horizons"].values():
            if hs.get("score") is not None:
                self.assertTrue(-100 <= hs["score"] <= 100)
        mat = self.get("asset/SPY/matrix")["matrix"]
        rec = next(r for row in mat.values() for r in row.values() if r)
        for k in ("ic", "p", "q", "n", "n_eff", "hit_rate", "stability", "usefulness"):
            self.assertIn(k, rec)
        sig = next(iter(mat))
        cell = self.get(f"asset/SPY/cell/{sig}/1M")
        self.assertIn("ic", str(cell).lower())
        self.assertTrue(self.get("asset/SPY/prices")["dates"])
        self.assertIsInstance(self.get("asset/SPY/leaderboard", horizon="1M"), list)
        eq = self.get("asset/SPY/equations")
        self.assertTrue(eq)

    def test_shaffer_score_plugs_in_without_a_code_change(self):
        self.assertIsNone(self.get("asset/SPY")["shaffer"])
        path = os.path.join(self.tmp, "shaffer_score.py")
        with open(path, "w") as f:
            f.write("VERSION = 't1'\ndef score(metrics, asset):\n    return 50.0 + 10 * (metrics['z'].get('mom_12_1') or 0.0)\n")
        with unittest.mock.patch.dict(os.environ, {"FINSIM2_SHAFFER": path}):
            self.assertEqual(self.get("shaffer")["state"], "live")
            v = self.get("asset/SPY")["shaffer"]
            self.assertTrue(20.0 <= v <= 80.0)
            row = next(r for r in self.get("markets") if r["id"] == "SPY")
            self.assertAlmostEqual(row["shaffer"], v)

    def test_unknown_things_are_404_and_bad_input_is_400(self):
        with self.assertRaises(NotFound):
            self.get("asset/NOPE")
        with self.assertRaises(NotFound):
            self.get("nothing/here")
        with self.assertRaises(CommandError):
            self.post("portfolio/optimize", {"assets": ["SPY"]})

    def test_portfolio_ledger_analytics_and_tools(self):
        cal = self.app.research.panel().calendar()
        self.post("portfolio/deposit", {"amount": 1_000_000, "date": cal[-400]})
        self.post("portfolio/trade", {"asset_id": "SPY", "amount": 400_000, "side": "BUY", "date": cal[-400]})
        self.post("portfolio/trade", {"asset_id": "TLT", "quantity": 1000, "side": "BUY", "date": cal[-300]})
        self.post("portfolio/trade", {"asset_id": "TLT", "quantity": 400, "side": "SELL", "date": cal[-100]})
        an = self.get("portfolio")
        pos = {p["asset_id"]: p for p in an["positions"]}
        self.assertAlmostEqual(pos["TLT"]["quantity"], 600, places=6)
        self.assertAlmostEqual(an["nav"], an["cash"] + sum(p["market_value"] for p in an["positions"]), places=2)
        self.assertAlmostEqual(sum(p["weight"] for p in an["positions"]) + an["cash"] / an["nav"], 1.0, places=6)
        spy_px = self.store.prices("SPY", cal[-400], cal[-400])[0]["close"]
        self.assertAlmostEqual(pos["SPY"]["quantity"] * spy_px, 400_000, places=2)   # an amount is sized at the trade-date close
        self.assertTrue(self.get("portfolio/transactions"))
        opt = self.post("portfolio/optimize", {"assets": ["SPY", "TLT", "GOLD"], "cap": 0.7})
        self.assertEqual({p["name"] for p in opt["portfolios"]} >= {"Minimum variance", "Maximum Sharpe", "Risk parity"}, True)
        mc = self.post("portfolio/montecarlo", {"horizon": "1Y", "paths": 300})
        self.assertTrue(0 <= mc["prob_loss"] <= 1)
        scn = self.post("portfolio/scenario", {"shock": {"nasdaq_pct": -10}})
        self.assertEqual(scn["completed"]["nasdaq_pct"], -10)
        self.assertEqual(len(scn["positions"]), 2)
        self.assertLess(scn["pnl"], 0)                        # both holdings co-move with the Nasdaq proxy here
        self.assertIn("oil_pct", scn["unavailable"])          # no oil series in this store: reported, not assumed flat
        self.assertIsNone(scn["completed"]["oil_pct"])
        dr = self.get("portfolio/drivers")
        self.assertIn("risk", dr)

    def test_withdrawal_cannot_overdraw(self):
        with self.assertRaises(CommandError):
            self.post("portfolio/withdraw", {"amount": 1e15})

    def test_backtest_route_and_watchlist(self):
        bt = self.post("backtest", {"asset": "SPY", "signal": "mom_12_1", "horizon": "1M"})
        self.assertIn("metrics", bt)
        self.assertTrue(self.get("backtests"))
        self.post("watchlist", {"asset_id": "QQQ"})
        self.assertIn("QQQ", [w["asset_id"] for w in self.get("watchlist")])
        self.r.dispatch("DELETE", "/api/fs2/watchlist/QQQ", {}, {})
        self.assertNotIn("QQQ", [w["asset_id"] for w in self.get("watchlist")])


if __name__ == "__main__":
    unittest.main()
