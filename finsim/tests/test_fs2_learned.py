"""The learned Shaffer weights (engine/learned.py) and learned hedge parameters (hedge/hedgelearn.py)."""
import random
import unittest

from finsim2.engine import learned as L
from finsim2.hedge import hedgelearn as HL


class Taxonomy(unittest.TestCase):
    def test_economic_hierarchy(self):
        nvda = L.taxonomy({"id": "NVDA", "asset_class": "EQUITY", "sector": "Information Technology"})
        self.assertEqual(nvda, ["global", "class:Equity", "ptype:Equity/single stock", "sector:Equity/single stock/Information Technology",
                                "industry:Equity/single stock/Information Technology/Semiconductors", "asset:NVDA"])
        xlk = L.taxonomy({"id": "XLK", "asset_class": "ETF", "sector": "Information Technology"})
        self.assertEqual(xlk[1:3], ["class:Equity", "ptype:Equity/sector ETF"])
        ust = L.taxonomy({"id": "UST10Y", "asset_class": "TREASURY", "sector": "Government Bonds"})
        tlt = L.taxonomy({"id": "IEF", "asset_class": "ETF", "sector": "Fixed Income"})
        self.assertEqual(ust[:4], tlt[:4])                                   # Rates → Treasury → intermediate, wrapper-free
        wti = L.taxonomy({"id": "WTI", "asset_class": "COMMODITY", "sector": "Energy"})
        uso = L.taxonomy({"id": "USO", "asset_class": "ETF", "sector": "Energy"})
        self.assertEqual(wti[:5], uso[:5])                                   # Commodity → Energy → Oil
        self.assertTrue(wti[4].endswith("/Oil"))
        self.assertIn("ptype:FX/major FX", L.taxonomy({"id": "EURUSD", "asset_class": "FX", "sector": "Currency"}))
        self.assertEqual(L.cut_path(nvda, "sector"), nvda[3])
        self.assertEqual(L.cut_path(L.taxonomy({"id": "SPY", "asset_class": "ETF", "sector": "Broad Market"}), "industry"),
                         "ptype:Equity/broad equity ETF")             # missing levels fall back to the deepest existing


def _rows(n_assets=6, weeks=900, beta=(0.4, -0.2, 0.0), shift_after=None, seed=3):
    import datetime as dt
    rnd = random.Random(seed)
    rows = []
    d0 = dt.date(2003, 1, 1)
    for w in range(weeks):
        d = d0 + dt.timedelta(days=7 * w)
        e = d + dt.timedelta(days=8)
        for k in range(n_assets):
            x = [rnd.gauss(0, 1) for _ in beta]
            b = beta if shift_after is None or d.isoformat() < shift_after else [-v * 5 for v in beta]
            y = sum(bi * xi for bi, xi in zip(b, x)) + rnd.gauss(0, 1)
            rows.append({"asset": f"A{k}", "path": ["global", f"class:C{k % 2}", f"asset:A{k}"], "date": d.isoformat(), "end": e.isoformat(),
                         "wk": d.toordinal() // 7, "x": x, "y": y, "z": 0.0, "o": 1 if y > 0 else 0, "reg": ("bull", "high_vol", "rising_rates", "expansion")})
    return rows


class Engine(unittest.TestCase):
    def test_fits_are_point_in_time(self):
        """A fit at a cutoff is identical whether or not the future (with a completely different equation) exists."""
        names = ["a", "b", "c"]
        past = [r for r in _rows() if r["end"] < "2013-01-01"]
        full = _rows(shift_after="2013-01-01")
        f1, f2 = L.Learner(L.Table(past, names, 5, "value"), "alpha"), L.Learner(L.Table(full, names, 5, "value"), "alpha")
        f1.fit_all(); f2.fit_all()
        for K in (L.K_BEST, 1000):
            for nd in ("global", "class:C0", "asset:A1"):
                self.assertEqual([round(v, 10) for v in f1.fits["2013-01-01"]["W"][K][nd]],
                                 [round(v, 10) for v in f2.fits["2013-01-01"]["W"][K][nd]])

    def test_recovers_weights_and_pools_children(self):
        tab = L.Table(_rows(), ["a", "b", "c"], 5, "value")
        lr = L.Learner(tab, "alpha")
        lr.fit_all()
        g = lr.fits[L.FINAL]["W"][L.K_BEST]["global"]
        self.assertAlmostEqual(g[0], 0.4, delta=0.05)
        self.assertAlmostEqual(g[1], -0.2, delta=0.05)
        # heavy pooling: every child sits on its parent
        W = lr.fits[L.FINAL]["W"][20000]
        self.assertLess(abs(W["asset:A1"][0] - W["class:C1"][0]), 0.05)

    def test_js_trust_and_bh(self):
        self.assertEqual(L.js_trust(0.5), 0.0)
        self.assertEqual(L.js_trust(None), 0.0)
        self.assertAlmostEqual(L.js_trust(2.0), 0.75)
        self.assertEqual(L.benjamini_hochberg([0.001, 0.5, 0.02], 0.1), [True, False, True])

    def test_stability_classes(self):
        self.assertEqual(L.stability([0.3, 0.25, 0.35, 0.3, 0.28, 0.32], 0.3, None)["class"], "STABLE")
        self.assertEqual(L.stability([0.01, -0.02, 0.015, -0.01, 0.0, 0.005], 0.0, None)["class"], "NO EVIDENCE")
        self.assertEqual(L.stability([0.3, -0.3, 0.3, -0.3, 0.3, -0.25], 0.1, 3.0)["class"], "REGIME DEPENDENT")


class Capability(unittest.TestCase):
    """The planted-effect suite on smaller data: what is planted must be recovered, noise must not be validated."""

    def test_sector_specific_and_noise(self):
        rows, names, _ = L.synth("sector", 5, n_assets=16)
        tab, lr, ev, st, _ = L._learn(rows, names, 5)
        ch = lr.choices[L.FINAL]
        kp = lr.kept(ch).get(0, {})
        self.assertIn("sector:C0/P/S0", kp.get("sector", []))
        self.assertAlmostEqual(lr.deployable(L.FINAL, ch, tab.paths["X00"])[0], 0.5, delta=0.12)
        self.assertAlmostEqual(lr.deployable(L.FINAL, ch, tab.paths["X01"])[0], 0.0, delta=0.12)
        rows, names, _ = L.synth("noise", 9, n_assets=16)
        tab, lr, ev, st, _ = L._learn(rows, names, 5)
        self.assertEqual(st.count("VALIDATED"), 0)

    def test_asset_specific_short_history_inherits(self):
        rows, names, _ = L.synth("asset", 10, n_assets=16)
        tab, lr, ev, st, _ = L._learn(rows, names, 5)
        ch = lr.choices[L.FINAL]
        kept = lr.kept(ch).get(0, {}).get("asset", [])
        self.assertIn("asset:X00", kept)
        self.assertNotIn("asset:X01", kept)
        self.assertLess(abs(lr.deployable(L.FINAL, ch, tab.paths["X01"])[0]), 0.3)


class HedgeParams(unittest.TestCase):
    def test_paths_and_parents(self):
        c = {"primary": "RATE:10Y", "btype": "bond portfolio", "objective": "duration",
             "arms": {"A": {"legs": [{"id": "ZN", "product_type": "treasury_future", "notional": 1e5}]}}}
        p = HL.hpath(c)
        self.assertEqual(p, ["global", "risk:Rates", "objective:Rates/duration", "product:Rates/duration/treasury_future",
                             "instrument:Rates/duration/treasury_future/ZN"])
        self.assertEqual(HL._parent(p[3]), p[2])
        self.assertEqual(HL._parent(p[1]), "global")
        self.assertEqual(HL.risk_class({"primary": "IDIO:HYG", "btype": "credit portfolio"}), "Credit")

    def test_shrinkage_inherits_with_few_dates(self):
        fit = {"global": {"n": 400, "m_hat": 1.2}, "risk:Equity": {"n": 300, "m_hat": 1.5},
               "objective:Equity/beta": {"n": 3, "m_hat": 0.5}, "product:Equity/beta/etf": {"n": 0, "m_hat": None}}
        sz = HL.shrink_sizing(fit, 60)
        self.assertAlmostEqual(sz["global"], 400 / 460 * 1.2 + 60 / 460 * 1.0)
        self.assertGreater(sz["risk:Equity"], sz["global"])
        self.assertAlmostEqual(sz["objective:Equity/beta"], sz["risk:Equity"], delta=0.05)   # 3 dates: mostly the parent
        self.assertEqual(sz["product:Equity/beta/etf"], sz["objective:Equity/beta"])


if __name__ == "__main__":
    unittest.main()
