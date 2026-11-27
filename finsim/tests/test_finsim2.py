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


class BacktestLookahead(unittest.TestCase):
    """The backtester must not know the future: execution lag, point-in-time direction, expanding ML z-scores."""

    @staticmethod
    def iid_prices(n=2000, seed=11, vol=0.01):
        rnd = random.Random(seed)
        p = [100.0]
        for _ in range(n - 1):
            p.append(p[-1] * math.exp(rnd.gauss(0.0, vol)))
        return p

    def test_perfect_foresight_needs_the_lag(self):
        n = 5000                                                        # 20 years: the CAGR's noise is ~2.5%
        days = business_days("2000-01-03", n)
        price = self.iid_prices(n)
        # signal[t] = the sign of the NEXT day's return: perfect foresight, usable only by trading at the same close
        sig = [(2.0 if price[t + 1] > price[t] else -2.0) for t in range(n - 1)] + [None]
        spec = {"entry": 1.0, "exit": 0.0, "mode": "long", "min_hold": 1, "cost_bps": 0.0}
        same_close = run_backtest(days, price, sig, {**spec, "lag": 0})["metrics"]
        lagged = run_backtest(days, price, sig, {**spec, "lag": 1})["metrics"]
        self.assertGreater(same_close["cagr"], 1.0)                     # > 100% a year from peeking
        self.assertLess(abs(lagged["cagr"]), 0.06)                      # about nothing once the trade waits a session
        self.assertLess(abs(lagged["cagr"] - lagged["buy_hold_cagr"]), 0.06)
        default = run_backtest(days, price, sig, spec)["metrics"]
        self.assertEqual(default["lag"], 1)
        self.assertAlmostEqual(default["cagr"], lagged["cagr"], places=12)

    @staticmethod
    def pipeline(price, days, h=21):
        from finsim2.engine.backtest import simulate
        from finsim2.engine.features import targets
        mom = [None] * 20 + [math.log(price[t] / price[t - 20]) for t in range(20, len(price))]
        z = standardize(mom)
        dirs = hz.expanding_direction(z, targets(price, h), h, min_obs=20, t_min=0.0)
        directed = [v * d if v is not None else None for v, d in zip(z, dirs)]
        spec = {"entry": 0.5, "exit": 0.0, "mode": "long_short", "min_hold": 3, "cost_bps": 10.0}
        return simulate(days, price, directed, spec), dirs

    def test_future_prices_do_not_move_past_positions(self):
        n, T = 1600, 1100
        days = business_days("2012-01-02", n)
        price = self.iid_prices(n, seed=3)
        rnd = random.Random(99)
        tampered = price[:T + 1] + [price[T] * math.exp(0.005 * i + rnd.gauss(0, 0.03)) for i in range(1, n - T)]
        a, da = self.pipeline(price, days)
        b, db = self.pipeline(tampered, days)
        self.assertEqual(da[:T + 1], db[:T + 1])
        cut = [k for k, d in enumerate(a["dates"]) if d <= days[T]]
        self.assertTrue(cut and any(a["positions"][k] != 0 for k in cut))
        self.assertEqual([a["positions"][k] for k in cut], [b["positions"][k] for k in cut])
        self.assertEqual([a["daily"][k] for k in cut], [b["daily"][k] for k in cut])
        self.assertNotEqual(a["daily"], b["daily"])                     # the tampering did reach the later part

    def test_expanding_direction_ignores_later_outcomes(self):
        rnd = random.Random(5)
        n, h = 1500, 5
        sig = [rnd.gauss(0, 1) for _ in range(n)]
        tgt = [0.4 * sig[i] + rnd.gauss(0, 1) for i in range(n - h)] + [None] * h
        d1 = hz.expanding_direction(sig, tgt, h)
        flip_from = 400
        flipped = tgt[:flip_from] + [-y if y is not None else None for y in tgt[flip_from:]]
        d2 = hz.expanding_direction(sig, flipped, h)
        known = flip_from + h + 1                                       # the first t allowed to see row flip_from
        self.assertEqual(d1[:known], d2[:known])
        self.assertTrue(all(d == 0 for d in d1[:hz.MIN_OBS + h]))       # no direction before min_obs known rows
        self.assertEqual(d1[known - 1], 1)
        self.assertEqual(d1[-1], 1)
        self.assertNotEqual(d1[known:], d2[known:])                     # later, the flipped outcomes do count
        # a relation without enough evidence (|t| below t_min) gives no direction
        noise = [rnd.gauss(0, 1) for _ in range(n)]
        self.assertLess(sum(1 for d in hz.expanding_direction(noise, tgt, h, t_min=3.0) if d != 0), n // 10)

    def test_expanding_standardize_uses_earlier_forecasts_only(self):
        from finsim2.engine.research import expanding_standardize
        rnd = random.Random(8)
        rows = list(range(0, 2000, 10))
        vals = [rnd.gauss(0.01, 0.02) for _ in rows]
        z1 = expanding_standardize(rows, vals)
        later = vals[:120] + [v * 50 + 1 for v in vals[120:]]
        z2 = expanding_standardize(rows, later)
        self.assertEqual(z1[:120], z2[:120])
        self.assertNotEqual(z1[120:], z2[120:])
        self.assertTrue(all(z is None for z in z1[:20]))
        self.assertIsNotNone(z1[20])
        prev = vals[:57]
        mu = sum(prev) / len(prev)
        sd = math.sqrt(sum((x - mu) ** 2 for x in prev) / (len(prev) - 1))
        self.assertAlmostEqual(z1[57], (vals[57] - mu) / sd, places=9)

    def test_costs_lower_win_rate_and_profit_factor(self):
        n = 1500
        days = business_days("2014-01-01", n)
        price = self.iid_prices(n, seed=21)
        rnd = random.Random(4)
        sig, v = [], 2.0
        for _ in range(n):
            if rnd.random() < 0.25:
                v = -v
            sig.append(v)
        spec = {"entry": 1.0, "exit": 0.0, "mode": "long_short", "min_hold": 1}
        free = run_backtest(days, price, sig, {**spec, "cost_bps": 0.0})["metrics"]
        costly = run_backtest(days, price, sig, {**spec, "cost_bps": 30.0})["metrics"]
        self.assertEqual(free["trades"], costly["trades"])
        self.assertLess(costly["win_rate"], free["win_rate"])
        self.assertLess(costly["profit_factor"], free["profit_factor"])
        self.assertLess(costly["avg_gain"], free["avg_gain"])

    def test_calmar_and_buy_hold_keys(self):
        n = 1200
        days = business_days("2015-01-01", n)
        price = self.iid_prices(n, seed=2)
        sig = [2.0 if (i // 40) % 2 == 0 else -2.0 for i in range(n)]
        m = run_backtest(days, price, sig, {"entry": 1.0, "exit": 0.0, "mode": "long", "cost_bps": 5.0})["metrics"]
        self.assertIn("calmar", m)
        self.assertLess(m["max_drawdown"], 0)
        self.assertAlmostEqual(m["calmar"], m["cagr"] / abs(m["max_drawdown"]), places=12)
        for k in ("return", "cagr", "sharpe"):
            self.assertEqual(m[f"buy_hold_{k}"], m[f"benchmark_{k}"])


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
        notes = b["data_notes"]                                  # latest-vintage macro, stated as such
        self.assertTrue(any("latest revised values" in n for n in notes))
        self.assertTrue(any("NFCI is excluded" in n for n in notes))
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

    def test_shaffer_score_on_every_surface(self):
        b = self.get("asset/SPY")
        self.assertEqual(b["shaffer_version"], shaffer_score.VERSION)
        hs = b["horizons"]
        scored = [h for h in hs.values() if h["score"] is not None]
        self.assertTrue(scored)
        for h in scored:
            self.assertTrue(-100 <= h["score"] <= 100)
            self.assertIn(h["confidence"]["label"], ("High", "Medium", "Low"))
            self.assertAlmostEqual(sum(f["points"] for f in h["families"]), h["score"], places=6)   # family points add up
        full = self.get("asset/SPY/shaffer")
        lab = next(k for k, v in hs.items() if v["score"] is not None)
        self.assertEqual(full["horizons"][lab]["raw"], hs[lab]["score"])
        self.assertEqual(full["history"][lab]["dates"][-1], b["as_of"])                # the live score is the last record
        self.assertEqual(full["history"][lab]["raw"][-1], hs[lab]["score"])
        row = next(r for r in self.get("markets") if r["id"] == "SPY")
        self.assertEqual(row["scores"]["3M"], hs["3M"]["score"])
        st = self.get("shaffer")
        self.assertEqual(st["version"], shaffer_score.VERSION)
        self.assertEqual(len(st["families"]), 15)
        preds = self.store.predictions(asset_id="SPY")
        self.assertTrue(any(p["model"] == "shaffer" and p["source"] == "live" for p in preds))   # today's scores are in the ledger

    def test_custom_override_is_shown_live_only_and_fails_safely(self):
        full = os.path.join(self.tmp, "full.py")
        with open(full, "w") as f:
            f.write("VERSION = 't2'\ndef score_asset(inputs):\n    return {'horizons': {h: {'score': 250.0 if h == '1D' else -7.0} for h in inputs['horizons']}}\n")
        broken = os.path.join(self.tmp, "broken.py")
        with open(broken, "w") as f:
            f.write("this is not python")
        with unittest.mock.patch.dict(os.environ, {"FINSIM2_SHAFFER": full}):
            c = self.get("asset/SPY/shaffer")["custom"]
            self.assertEqual(c["version"], "t2")
            self.assertEqual(c["horizons"]["1D"], 100.0)                                 # clamped
            self.assertEqual(c["horizons"]["3M"], -7.0)
        with unittest.mock.patch.dict(os.environ, {"FINSIM2_SHAFFER": broken}):
            c = self.get("asset/SPY/shaffer")["custom"]
            self.assertIn("failed to load", c["error"])
            self.assertIsNotNone(self.get("asset/SPY")["horizons"]["3M"])              # the built-in is untouched

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
        m = bt["metrics"]
        self.assertEqual(m["lag"], 1)                                   # a one-session execution lag by default
        self.assertEqual(bt["spec"]["direction"], "point in time (expanding)")
        self.assertEqual(m["buy_hold_cagr"], m["benchmark_cagr"])
        self.assertIn("calmar", m)
        same_close = self.post("backtest", {"asset": "SPY", "signal": "mom_12_1", "horizon": "1M", "lag": 0})
        self.assertEqual(same_close["metrics"]["lag"], 0)
        self.assertIn("optimistic", same_close["execution"])
        self.assertTrue(self.get("backtests"))
        self.post("watchlist", {"asset_id": "QQQ"})
        self.assertIn("QQQ", [w["asset_id"] for w in self.get("watchlist")])
        self.r.dispatch("DELETE", "/api/fs2/watchlist/QQQ", {}, {})
        self.assertNotIn("QQQ", [w["asset_id"] for w in self.get("watchlist")])


if __name__ == "__main__":
    unittest.main()
