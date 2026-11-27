"""FinSim2 equations tab: every catalogue equation evaluated on synthetic market data."""
import datetime as _dt
import json
import math
import random
import time
import unittest

from finsim.quant.catalog import EQUATIONS
from finsim2.engine import equations as E

KEYS = {"id", "name", "family", "formula", "variables", "value", "display", "fmt", "percentile", "direction",
        "signal", "usefulness", "confidence", "best_horizon", "hit_rate", "ic", "n", "n_eff", "regime",
        "interpretation", "history", "applies", "note"}
SIGNALS = {None, "Bullish", "Slightly Bullish", "Neutral", "Slightly Bearish", "Bearish"}
DIRECTIONS = {None, "Increasing", "Decreasing", "Stable"}
HORIZONS = [["1D", 1], ["1W", 5], ["1M", 21], ["3M", 63], ["6M", 126], ["12M", 252], ["3Y", 756], ["5Y", 1260],
            ["10Y", 2520]]


def _dates(n, start=_dt.date(2006, 1, 2)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += _dt.timedelta(days=1)
    return out


def _walk(rng, n, x0, kappa, theta, sd, lo=None):
    x, out = x0, []
    for _ in range(n):
        x += kappa * (theta - x) + rng.gauss(0.0, sd)
        if lo is not None:
            x = max(lo, x)
        out.append(x)
    return out


def make_ctx(n=5000, asset_class="EQUITY", asset_id="TEST", duration=None, seed=7, beta=1.5, features=True,
             portfolio=False, ml=None, signals=True):
    """Synthetic context: GARCH-ish market, asset = beta × market + noise, macro walks, a small universe."""
    rng = random.Random(seed)
    dates = _dates(n)
    # market with GARCH(1,1) shocks
    h, mret = 0.011 ** 2, []
    for _ in range(n):
        z = rng.gauss(0.0, 1.0)
        r = 0.0003 + math.sqrt(h) * z
        mret.append(r)
        h = 0.000002 + 0.08 * (r - 0.0003) ** 2 + 0.9 * h
    noise = [rng.gauss(0.0, 0.008) for _ in range(n)]
    aret = [0.0001 + beta * m + e for m, e in zip(mret, noise)]
    mk, px = [400.0], [50.0]
    for i in range(1, n):
        mk.append(mk[-1] * math.exp(mret[i]))
        px.append(px[-1] * math.exp(aret[i]))
    y3m = _walk(rng, n, 0.03, 0.002, 0.03, 0.0008, lo=0.0005)
    term = _walk(rng, n, 0.01, 0.002, 0.012, 0.0004)
    y2 = [a + 0.3 * t for a, t in zip(y3m, term)]
    y5 = [a + 0.7 * t for a, t in zip(y3m, term)]
    y10 = [a + t for a, t in zip(y3m, term)]
    y30 = [a + 1.2 * t for a, t in zip(y3m, term)]
    cs = _walk(rng, n, 0.02, 0.003, 0.02, 0.0005, lo=0.005)
    vix = _walk(rng, n, 16.0, 0.02, 17.0, 0.8, lo=9.0)
    dollar = [100.0]
    oil = [60.0]
    gold = [1200.0]
    for _ in range(1, n):
        dollar.append(dollar[-1] * math.exp(rng.gauss(0, 0.004)))
        oil.append(oil[-1] * math.exp(rng.gauss(0, 0.02)))
        gold.append(gold[-1] * math.exp(rng.gauss(0, 0.01)))
    macro = {"y3m": y3m, "y2": y2, "y5": y5, "y10": y10, "y30": y30, "baa": [a + b for a, b in zip(y10, cs)],
             "credit_spread": cs, "vix": vix, "cpi_yoy": [0.025] * n, "real_y10": [y - 0.022 for y in y10],
             "breakeven_10y": [0.022] * n, "dollar": dollar, "oil": oil, "gold": gold}
    uni = {}
    for k in range(12):
        b = 0.3 + 0.15 * k
        uni["U%02d" % k] = [b * m + rng.gauss(0, 0.004 + 0.001 * k) for m in mret[-252:]]
    uni["SPY"] = mret[-252:]
    feats = {}
    if features:
        def lag(k):
            return [None if i < k else math.log(px[i] / px[i - k]) for i in range(n)]
        feats["ret_1m"], feats["ret_3m"] = lag(21), lag(63)
        feats["mom_12_1"] = [None if i < 252 else math.log(px[i - 21] / px[i - 252]) for i in range(n)]
        vol = [None] * n
        for i in range(21, n):
            seg = aret[i - 19:i + 1]
            m = sum(seg) / 20
            vol[i] = math.sqrt(sum((x - m) ** 2 for x in seg) / 19 * 252)
        feats["vol_20"] = vol
        feats["vix"] = vix
        feats["credit_spread"] = cs
        feats["value_5y"] = [None] * (n - 300) + [rng.gauss(0, 1) for _ in range(300)]
    sig = {}
    if signals:
        sig = {"vol_20": {"1M": {"ic": -0.08, "hit_rate": 0.55, "t": -2.4, "p": 0.02, "n": 4800, "n_eff": 228,
                                 "usefulness": -0.08, "direction": -1},
                          "3M": {"ic": -0.05, "hit_rate": 0.53, "t": -0.9, "p": 0.37, "n": 4700, "n_eff": 75,
                                 "usefulness": -0.02, "direction": -1}},
               "beta_252": {"1W": {"ic": 0.02, "usefulness": 0.01, "p": 0.5, "n": 4000, "n_eff": 800}}}
    ctx = {
        "asset": {"id": asset_id, "name": "Synthetic %s" % asset_class, "asset_class": asset_class, "currency": "USD",
                  "duration": duration, "convexity": None, "dividend_yield": 0.015},
        "dates": dates, "close": list(px), "adj": list(px), "high": [p * 1.01 for p in px],
        "low": [p * 0.99 for p in px], "volume": [1e6 + rng.random() * 1e5 for _ in range(n)], "market": mk,
        "macro": macro, "universe_returns": uni, "features": feats, "signals": sig, "rf": y3m[-1],
        "portfolio": None, "ml": ml, "horizons": HORIZONS,
    }
    if portfolio:
        ctx["portfolio"] = {"weights": {asset_id: 0.4, "SPY": 0.4, "U03": 0.2},
                            "returns": {asset_id: aret[-400:], "SPY": mret[-400:],
                                        "U03": [0.75 * m + rng.gauss(0, 0.006) for m in mret[-400:]]}}
    return ctx


def by_id(results):
    return {r["id"]: r for r in results}


class EquationsTab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = make_ctx()
        t0 = time.time()
        cls.cat = E.evaluate_catalog(cls.ctx)
        t1 = time.time()
        cls.tabs = E.analytics_tabs(cls.ctx, cls.cat)
        t2 = time.time()
        cls.summary = E.summary_numbers(cls.ctx)
        t3 = time.time()
        cls.timing = (t1 - t0, t2 - t1, t3 - t2)
        print("\n[fs2 equations] 5,000 points: evaluate_catalog %.2fs, analytics_tabs %.2fs, summary %.2fs" % cls.timing)
        cls.r = by_id(cls.cat)

    def test_every_catalogue_id_once_in_order(self):
        ids = [r["id"] for r in self.cat]
        self.assertEqual(ids, [e["id"] for e in EQUATIONS])
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 156)

    def test_standard_format(self):
        for r in self.cat + [x for t in self.tabs.values() for x in t]:
            self.assertTrue(KEYS <= set(r), (r["id"], KEYS - set(r)))
            self.assertIn(r["signal"], SIGNALS, r["id"])
            self.assertIn(r["direction"], DIRECTIONS, r["id"])
            self.assertIn(r["family"], E.FAMILIES + ["Valuation"], r["id"])
            self.assertLessEqual(len(r["history"]["values"]), 261, r["id"])
            self.assertEqual(len(r["history"]["values"]), len(r["history"]["dates"]), r["id"])
            self.assertTrue(r["interpretation"], r["id"])
            self.assertIsInstance(r["display"], str)
            if r["percentile"] is not None:
                self.assertTrue(0.0 <= r["percentile"] <= 1.0, r["id"])

    def test_json_serialisable(self):
        for obj in (self.cat, self.tabs, self.summary):
            json.dumps(obj, allow_nan=False)

    def test_most_items_computed(self):
        missing = [(r["id"], r["note"]) for r in self.cat if r["value"] is None]
        self.assertLessEqual(len(missing), 3, missing)

    def test_beta_recovered(self):
        self.assertAlmostEqual(self.r["22"]["value"], 1.5, delta=0.1)
        self.assertAlmostEqual(self.r["106"]["value"], 1.5, delta=0.1)
        self.assertAlmostEqual(self.summary["beta"], 1.5, delta=0.1)
        self.assertGreater(self.r["29"]["value"], 0.5)

    def test_garch(self):
        g = self.r["46"]
        pers = g["variables"]["persistence"]
        self.assertTrue(0.0 < pers < 1.0)
        self.assertGreater(g["value"], 0.0)
        self.assertTrue(g["history"]["values"])
        self.assertIsNotNone(g["percentile"])

    def test_bsm_and_greeks(self):
        self.assertGreater(self.r["D10"]["value"], 0.0)
        self.assertGreater(self.r["D11"]["value"], 0.0)
        self.assertAlmostEqual(self.r["D9"]["value"], 0.0, places=6)
        self.assertTrue(0.0 < self.r["D13"]["value"] < 1.0)
        self.assertTrue(-1.0 < self.r["D14"]["value"] < 0.0)
        self.assertAlmostEqual(self.r["D20"]["value"], self.r["D20"]["variables"]["sigma"], places=3)

    def test_bond_items_flagged_for_stock(self):
        for i in ("111", "113", "114", "116"):
            self.assertFalse(self.r[i]["applies"], i)
            self.assertIn("10-year Treasury", self.r[i]["note"])
            self.assertIsNotNone(self.r[i]["value"], i)
        self.assertFalse(self.r["C4"]["applies"])
        self.assertTrue(self.r["D2"]["applies"])

    def test_evidence_attached(self):
        v = self.r["49"]
        self.assertEqual(v["best_horizon"], "1M")
        self.assertAlmostEqual(v["usefulness"], -0.08)
        self.assertIsNotNone(v["signal"])
        self.assertIsNotNone(v["confidence"])
        self.assertIsNone(self.r["25"]["best_horizon"])

    def test_known_relationships(self):
        r = self.r
        self.assertAlmostEqual(r["9"]["value"], r["8"]["value"] ** 0.5 * 252 ** 0.5, places=6)
        self.assertAlmostEqual(r["27"]["value"], r["26"]["value"] ** 0.5, places=8)
        self.assertAlmostEqual(r["C9"]["value"], r["C9"]["variables"]["first_guess"], delta=0.01)
        self.assertAlmostEqual(r["S1"]["value"], 0.0, delta=1.0)
        self.assertGreater(r["S4"]["value"], 0.0)
        self.assertLess(r["54"]["variables"]["p5"], r["54"]["value"])
        self.assertLess(r["54"]["value"], r["54"]["variables"]["p95"])
        self.assertTrue(r["101"]["variables"]["weights"])
        self.assertAlmostEqual(r["102"]["value"], 1.0)
        self.assertGreater(r["110"]["value"], r["109"]["value"])
        self.assertTrue(all(r[str(i)].get("experimental") for i in range(84, 101)))

    def test_tabs(self):
        self.assertEqual(list(self.tabs), E.TABS)
        ids = {x["id"] for t in self.tabs.values() for x in t}
        for x in ("X-ret-1M", "X-ret-12M", "X-rel-12M", "X-median", "X-percentiles", "X-distribution",
                  "X-downside-vol", "X-tracking-error", "X-information-ratio", "X-max-drawdown", "X-rolling-beta",
                  "X-multifactor", "X-beta-stability", "X-pacf", "X-momentum-persistence", "X-vol-regime",
                  "X-vol-of-vol", "X-yield-curve", "X-spread-change", "X-value_5y", "X-earnings_yield",
                  "X-marginal-risk"):
            self.assertIn(x, ids)
        tab = {x["id"]: x for t in self.tabs.values() for x in t}
        hist = tab["X-distribution"]["history"]
        self.assertEqual(len(hist["bins"]), len(hist["counts"]) + 1)
        self.assertIsNone(tab["X-earnings_yield"]["value"])
        self.assertIsNotNone(tab["X-value_5y"]["value"])
        self.assertIsNotNone(tab["X-multifactor"]["value"])
        self.assertIn("46", {x["id"] for x in self.tabs["Volatility"]})

    def test_summary(self):
        s = self.summary
        for k in ("vol_20", "garch_vol", "beta", "sharpe", "max_drawdown", "var_95", "es_95", "adf_p"):
            self.assertIsNotNone(s[k], k)
        self.assertLessEqual(s["max_drawdown"], 0.0)

    def test_performance(self):
        self.assertLess(sum(self.timing), 30.0)


class OtherAssets(unittest.TestCase):
    def test_treasury_par_bond(self):
        ctx = make_ctx(n=1500, asset_class="TREASURY", asset_id="UST10", duration=8.5, features=False, signals=False)
        r = by_id(E.evaluate_catalog(ctx))
        self.assertTrue(r["111"]["applies"])
        self.assertAlmostEqual(r["111"]["value"], 100.0, places=6)
        self.assertAlmostEqual(r["112"]["value"], r["111"]["variables"]["coupon"], delta=1e-5)
        self.assertAlmostEqual(r["114"]["value"], 8.5, delta=0.3)
        self.assertTrue(r["S5"]["applies"])
        # ML falls back to price-derived features when the feature store is empty
        self.assertIsNotNone(r["73"]["value"])
        json.dumps(r, allow_nan=False)

    def test_corporate_bond_credit(self):
        ctx = make_ctx(n=1500, asset_class="CORP_BOND", asset_id="CORP", duration=7.0, features=False)
        r = by_id(E.evaluate_catalog(ctx))
        self.assertTrue(r["C4"]["applies"])
        s = ctx["macro"]["credit_spread"][-1]
        self.assertAlmostEqual(r["C5"]["value"], s, places=9)
        self.assertAlmostEqual(r["C8"]["value"], s, delta=0.002)
        self.assertTrue(r["111"]["applies"])

    def test_portfolio_and_ml_override(self):
        ml = {"logistic_prob_up": {"1M": 0.61}, "predictions": {"ridge": {"1M": 0.012}},
              "importance": {"ret_1m": 0.4}}
        ctx = make_ctx(n=1200, portfolio=True, ml=ml)
        r = by_id(E.evaluate_catalog(ctx))
        self.assertAlmostEqual(r["68"]["value"], 0.61)
        self.assertAlmostEqual(r["73"]["value"], 0.012)
        w = r["104"]["variables"]["weights"]
        self.assertEqual(set(w), {"TEST", "SPY", "U03"})
        self.assertAlmostEqual(sum(w.values()), 1.0, places=2)
        self.assertAlmostEqual(r["102"]["value"], 1.0)
        tabs = E.analytics_tabs(ctx)
        self.assertTrue(any(x["id"] == "X-marginal-risk" and x["value"] is not None for x in tabs["Portfolio"]))

    def test_fx_forward(self):
        ctx = make_ctx(n=800, asset_class="FX", asset_id="EURUSD", features=False, signals=False)
        r = by_id(E.evaluate_catalog(ctx))
        self.assertTrue(r["D3"]["applies"])
        self.assertIn("approximated", r["D3"]["note"])

    def test_short_history_does_not_raise(self):
        ctx = make_ctx(n=40, features=False)
        cat = E.evaluate_catalog(ctx)
        self.assertEqual(len(cat), 156)
        nones = sum(1 for x in cat if x["value"] is None)
        self.assertGreater(nones, 80)
        tabs = E.analytics_tabs(ctx)
        s = E.summary_numbers(ctx)
        json.dumps([cat, tabs, s], allow_nan=False)

    def test_missing_everything(self):
        for ctx in ({}, {"dates": [], "adj": []}, None,
                    {"dates": _dates(300), "adj": [None] * 300, "macro": {}}):
            cat = E.evaluate_catalog(ctx)
            self.assertEqual(len(cat), 156)
            json.dumps([cat, E.analytics_tabs(ctx), E.summary_numbers(ctx)], allow_nan=False)

    def test_gaps_and_bad_values(self):
        ctx = make_ctx(n=900, features=False)
        ctx["adj"][100] = None
        ctx["market"][850] = None
        ctx["macro"]["vix"][-5] = float("nan")
        ctx["close"][-1] = float("inf")
        cat = E.evaluate_catalog(ctx)
        json.dumps(cat, allow_nan=False)
        self.assertIsNotNone(by_id(cat)["22"]["value"])


if __name__ == "__main__":
    unittest.main()
