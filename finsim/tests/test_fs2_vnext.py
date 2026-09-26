"""Shaffer vNext research programs: SEC extra concepts are first-reported and point in time (YTD → TTM), the Alpha
ridge tree recovers planted effects and shrinks children to parents, long-short costs and conviction buckets are right,
Directional classes / calibration / gates behave, and the hedge rules learn only from matured cases."""
import datetime as dt
import math
import random
import unittest

from finsim2.data import sec_extra as X
from finsim2.engine import alphanext as A
from finsim2.engine import dirnext as DN
from finsim2.engine import weights as W
from finsim2.hedge import hedgenext as HN
from finsim2.hedge import volhedge as V


def _fact(start, end, val, filed, form="10-Q"):
    return {"start": start, "end": end, "val": val, "filed": filed, "form": form, "fp": "Q"}


class SecExtra(unittest.TestCase):
    def facts(self):
        ocf = [_fact("2019-01-01", "2019-12-31", 100.0, "2020-02-15", "10-K"),
               _fact("2020-01-01", "2020-03-31", 30.0, "2020-05-01"),
               _fact("2020-01-01", "2020-06-30", 55.0, "2020-08-01"),
               _fact("2019-01-01", "2019-06-30", 45.0, "2019-08-01"),
               _fact("2019-01-01", "2019-03-31", 20.0, "2019-05-01"),
               _fact("2020-01-01", "2020-06-30", 999.0, "2021-03-01")]           # a later restatement (not known in 2020)
        assets = [{"end": "2019-12-31", "val": 1000.0, "filed": "2020-02-15", "form": "10-K"},
                  {"end": "2020-06-30", "val": 1100.0, "filed": "2020-08-01", "form": "10-Q"}]
        return {"facts": {"us-gaap": {"NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": ocf}},
                                      "Assets": {"units": {"USD": assets}}}}}

    def test_first_reported_ttm_and_point_in_time(self):
        rows = [("Z",) + r for r in X.extract(self.facts())]
        f = X.Facts(rows)
        self.assertEqual(f.ttm("ocf", "2020-02-14"), None)                          # the 10-K was not filed yet
        self.assertEqual(f.ttm("ocf", "2020-02-15"), ("2019-12-31", 100.0))
        self.assertEqual(f.ttm("ocf", "2020-05-01"), ("2020-03-31", 100.0 + 30.0 - 20.0))
        self.assertEqual(f.ttm("ocf", "2020-08-01"), ("2020-06-30", 100.0 + 55.0 - 45.0))  # first report, not the restatement
        self.assertEqual(f.instant("assets", "2020-07-31"), ("2019-12-31", 1000.0))
        self.assertEqual(f.instant("assets", "2020-08-01"), ("2020-06-30", 1100.0))


class AlphaTree(unittest.TestCase):
    def test_ridge_recovers_a_planted_effect_and_children_shrink(self):
        rnd = random.Random(1)
        st = A.Stats(3, ["2010-01-01", "9999-12-31"])
        for _ in range(4000):
            x1 = rnd.gauss(0, 1)
            cls = "EQUITY" if rnd.random() < 0.5 else "ETF"
            y = 0.5 * x1 + (0.3 * x1 if cls == "ETF" else 0.0) + rnd.gauss(0, 1)
            st.add(A._node_keys({"asset_class": cls, "sector": "S"}, "class"), [1.0, 0.0, x1], y, "2005-01-01")
        fit = A.fit_tree(st, 1, ["global", "c:EQUITY", "c:ETF"])
        self.assertAlmostEqual(fit["global"][2], 0.65, delta=0.05)
        self.assertLess(fit["c:EQUITY"][2], fit["global"][2])
        self.assertGreater(fit["c:ETF"][2], fit["global"][2])
        # nothing that matures after the cut enters it
        late = A.Stats(3, ["2010-01-01", "9999-12-31"])
        for _ in range(500):
            late.add(["global"], [1.0, 0.0, rnd.gauss(0, 1)], rnd.gauss(0, 1), "2012-01-01")
        self.assertIsNone(A.fit_tree(late, 0, ["global"]).get("global"))
        self.assertIsNotNone(A.fit_tree(late, 1, ["global"]).get("global"))


def _rec(asset, wk, date, raw, score, yr, cls="EQUITY"):
    r = W.Rec()
    r.asset, r.wk, r.date, r.raw, r.score, r.yr, r.y = asset, wk, date, raw, score, yr, yr * 10
    r.meta = {"id": asset, "asset_class": cls}
    r.ext = {"s": 0.03, "mu": {p: 0.0 for p in ("zero", "product")}, "clim": 0.5}
    return r


class AlphaMetrics(unittest.TestCase):
    def test_long_short_costs_turnover(self):
        rows = []
        for w in range(30):
            for k in range(20):
                rows.append(_rec(f"A{k}", w, f"2010-01-{1 + w % 28:02d}", float(k), float(k), 0.01 if k >= 16 else (-0.01 if k < 4 else 0.0)))
        ls = A.long_short(rows, lambda r: r.score, 5)
        self.assertAlmostEqual(ls["gross"], math.expm1(0.01) - math.expm1(-0.01), places=9)
        # the same names every week: only the first formation pays full turnover
        self.assertAlmostEqual(ls["gross"] - ls["net"], A.COST_ONE_WAY * 4 / ls["n"], places=9)

    def test_conviction_buckets_and_monotonicity(self):
        rnd = random.Random(2)
        rows = []
        for w in range(200):
            for k in range(20):
                s = rnd.uniform(-100, 100)
                rows.append(_rec(f"A{k}", w, "2010-01-01", s, s, 0.0005 * s / 10 + rnd.gauss(0, 0.01)))
        cv = A.conviction(rows, lambda r: r.score, 5)
        self.assertEqual(len(cv["buckets"]), 5)
        self.assertTrue(cv["monotonic"])
        self.assertGreater(cv["buckets"][-1]["relative_return"], cv["buckets"][0]["relative_return"])

    def test_paired_rank_ic_is_zero_for_a_copy_of_production(self):
        rnd = random.Random(3)
        rows = [_rec(f"A{k}", w, "2010-01-01", s, s, rnd.gauss(0, 0.02)) for w in range(60) for k, s in enumerate(rnd.sample(range(100), 12))]
        self.assertAlmostEqual(A.paired_rank_ic(rows, 5)["mean"], 0.0)


class AlphaGates(unittest.TestCase):
    def _ch(self, t, split_t, eras, qs=(0.004, 0.002), ls=(0.002, 0.001), ric=(0.05, 0.04)):
        e = [{"paired": {"mean": m}} for m in eras]
        return {"walkforward": {"paired": {"t": t, "mean": 0.01}, "challenger": {"rank_ic": ric[0], "quintile_spread": qs[0]},
                                "production": {"rank_ic": ric[1], "quintile_spread": qs[1]},
                                "ls_challenger": {"net": ls[0]}, "ls_production": {"net": ls[1]}},
                "split": {"paired": {"t": split_t}}, "eras": e}

    def test_all_gates_required(self):
        g = A.gates(self._ch(3.0, 1.5, [0.01, 0.01, 0.01, -0.01]), "1M")
        self.assertTrue(g["G1"] and g["G2"] and g["G3"] and g["G4"])
        self.assertFalse(A.gates(self._ch(1.9, 1.5, [0.01] * 4), "1M")["G1"])
        self.assertFalse(A.gates(self._ch(3.0, 0.5, [0.01] * 4), "1M")["G2"])
        self.assertFalse(A.gates(self._ch(3.0, 1.5, [0.01, -0.01, -0.01, 0.01]), "1M")["G3"])
        self.assertFalse(A.gates(self._ch(3.0, 1.5, [0.01] * 4, ls=(-0.001, 0.001)), "1M")["G4"])
        self.assertFalse(A.gates(self._ch(3.0, 1.5, [0.01] * 4, ric=(0.03, 0.04)), "1W")["G4"])   # 1W: never lose the edge


class Directional(unittest.TestCase):
    def test_product_classes(self):
        self.assertEqual(DN.product_class({"id": "SQQQ", "asset_class": "ETF", "sector": "Leveraged / Inverse", "meta": {"product_type": "inverse_etf"}}), "inverse / leveraged ETFs")
        self.assertEqual(DN.product_class({"id": "VXX", "asset_class": "ETF", "sector": "Volatility"}), "volatility products")
        self.assertEqual(DN.product_class({"id": "AAPL", "asset_class": "EQUITY", "sector": "Information Technology"}), "ordinary equities")
        self.assertEqual(DN.product_class({"id": "TLT", "asset_class": "ETF", "sector": "Fixed Income"}), "bonds")
        self.assertEqual(DN.product_class({"id": "GLD", "asset_class": "ETF", "sector": "Precious Metals"}), "commodities")
        self.assertEqual(DN.product_class({"id": "BTC", "asset_class": "CRYPTO", "sector": "Crypto"}), "crypto")
        self.assertEqual(DN.product_class({"id": "SPY", "asset_class": "ETF", "sector": "Broad Market"}), "equity ETFs / indices")

    def test_calibration_of_a_calibrated_forecaster(self):
        rnd = random.Random(4)
        recs, ps = [], []
        for _ in range(20000):
            p = rnd.uniform(0.3, 0.8)
            r = W.Rec()
            r.yr = 0.01 if rnd.random() < p else -0.01
            recs.append(r); ps.append(p)
        cal = DN.calibration(recs, ps)
        self.assertAlmostEqual(cal["slope"], 1.0, delta=0.1)
        for b in cal["bins"]:
            if b["n"] > 500:
                self.assertAlmostEqual(b["realised"], b["predicted"], delta=0.03)

    def test_gates(self):
        def hz(bt, ll, bal, ct, st_, eras, ece, slope):
            c = {"vs_prior": {"brier_t": bt, "brier_gain": 0.001, "logloss_gain": ll}, "vs_current": {"brier_t": ct}, "balanced_gain": bal,
                 "metrics": {"ece": ece}, "calibration": {"slope": slope}}
            return {"walkforward": {"prior": {"ece": 0.01}, "challengers": {"global": c}},
                    "split": {"challengers": {"global": {"vs_prior": {"brier_t": st_}}}},
                    "eras": [{"challengers": {"global": {"vs_prior": {"brier_gain": g}}}} for g in eras]}
        ok = DN.gates(hz(3, 0.001, 0.01, 2.5, 1.5, [1, 1, 1, -1], 0.012, 1.0), "global")
        self.assertTrue(ok["G1"] and ok["G2"] and ok["G3"] and ok["G4"])
        self.assertFalse(DN.gates(hz(3, 0.001, 0.0, 2.5, 1.5, [1] * 4, 0.012, 1.0), "global")["G1"])     # balanced accuracy must improve
        self.assertFalse(DN.gates(hz(3, 0.001, 0.01, 1.0, 1.5, [1] * 4, 0.012, 1.0), "global")["G1"])    # must beat the current formulation
        self.assertFalse(DN.gates(hz(3, 0.001, 0.01, 2.5, 1.5, [1] * 4, 0.012, 0.5), "global")["G4"])    # overconfident
        self.assertFalse(DN.gates(hz(3, 0.001, 0.01, 2.5, 1.5, [1] * 4, 0.030, 1.0), "global")["G4"])    # worse calibrated


def _case(date, u, hp, cost=5.0, obj="beta", regime="low_vol", book="AAPL"):
    return {"date": date, "end": date, "book": book, "btype": "t", "objective": obj, "u": u, "ideal": [-x for x in u],
            "regime": {"volatility": regime}, "vix": 20.0,
            "arms": {"A": {"hp": hp, "cost": cost, "skew": 0.0, "mult": 1.0,
                           "legs": [{"id": "SPY", "type": "SPOT", "product_type": "etf", "q": -1.0, "notional": 1.0, "beta_usd": -1.0, "option": False}]}}}


class HedgeRules(unittest.TestCase):
    def test_ewma_equals_sample_covariance_without_decay(self):
        class RK:
            def fwin(s, f):
                rnd = random.Random(hash(f) % 100)
                return [rnd.gauss(0, 0.01) for _ in range(252)]
        c = HN.ewma_cov(RK(), ["MKT", "SEC:XLK"], lam=1.0)
        xs = RK().fwin("MKT")
        m = sum(xs) / len(xs)
        self.assertAlmostEqual(c[("MKT", "MKT")], sum((x - m) ** 2 for x in xs) / len(xs), places=12)
        self.assertEqual(c[("MKT", "SEC:XLK")], c[("SEC:XLK", "MKT")])

    def test_rescaled_arm_is_linear(self):
        c = _case("2010-01-01", [1.0, -2.0], [-0.5, 1.0], 4.0)
        HN._scaled(c, 0.8)
        self.assertEqual(c["arms"]["S"]["hp"], [-0.4, 0.8])
        self.assertAlmostEqual(c["arms"]["S"]["cost"], 3.2)
        self.assertEqual(c["arms"]["S"]["legs"][0]["q"], -0.8)

    def test_sizing_rule_learns_only_from_matured_cases(self):
        rnd = random.Random(5)
        cases = []
        d0 = dt.date(2005, 1, 3)
        for k in range(1100):
            d = (d0 + dt.timedelta(days=7 * k)).isoformat()
            u = [rnd.gauss(0, 1000) for _ in range(5)]
            over = 2.0 if d < "2013-01-01" else 0.8                          # hedge-2 oversized early, undersized later
            cases.append(_case(d, u, [-over * x for x in u], 0.0))
        scored = HN.walk_forward([c for c in cases], "beta", HN.rule_obj)
        first = [c for c in scored if "2009-01-01" <= c["date"] < "2013-01-01"]
        self.assertAlmostEqual(first[0]["arms"]["S"]["mult"], 0.5)               # learned from the oversized past (floor 0.5)
        later = [c for c in scored if "2017-01-01" <= c["date"] < "2021-01-01"]
        self.assertGreater(later[0]["arms"]["S"]["mult"], 0.5)                   # the era after sees the change only once matured

    def test_product_rule_picks_the_better_type_and_falls_back(self):
        rnd = random.Random(6)
        cases = []
        d0 = dt.date(2005, 1, 3)
        for k in range(800):
            d = (d0 + dt.timedelta(days=7 * k)).isoformat()
            u = [rnd.gauss(0, 1000) for _ in range(5)]
            c = _case(d, u, [-0.5 * x for x in u])
            c["arms"]["type:future"] = {"hp": [-1.0 * x for x in u], "cost": 1.0, "skew": 0.0, "mult": 1.0, "legs": []}
            cases.append(c)
        scored = HN.walk_forward(cases, "beta", HN.rule_product)
        self.assertTrue(all(c["_choice"]["S"] == "type:future" for c in scored if c["date"] >= "2009-01-01"))
        early = HN.walk_forward([c for c in cases if "2008-09-01" <= c["date"] < "2009-06-01"], "beta", HN.rule_product)
        self.assertTrue(all(c["_choice"]["S"] == "hedge-2" for c in early))      # too little matured history


if __name__ == "__main__":
    unittest.main()
