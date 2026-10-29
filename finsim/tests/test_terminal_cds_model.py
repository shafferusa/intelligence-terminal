"""Single-name CDS spread MODEL tests: products/cds_model.py (CDS_SINGLE_NAME_PLAN rev 2, package B3; release 1.0.7).

Every expected number is one of the plan's own vectors (Appendix A: CreditGrades with every intermediate, the quadrature path, the calibrated
spreads and buckets, the equity shocks, the realised-volatility vector; Appendix B: the shipped model on the saved 2026-10-28 production inputs)
or is worked by hand in a comment from the inputs; nothing is pasted from the module's output.  No network: a FakeClock, a RateStore with the
FRED prints given directly, a CurveStore with fixed discount factors, the FakeProvider (or a YahooChart whose getter serves a fixture file), a
stub of the reference-entity contract C-ENT in the shape of ``credit_entities.entity``, a temp database.

Hand-worked inputs used below
- DFS: 1/1.02^k at 0.5 k years (the CDX tests' curve, 8 dp): ln DF(t) = -2 t ln 1.02 at the nodes and log-linear between them, so
  r = -ln DF(T) / T = 2 ln 1.02 = 0.0396052546 (to about 1e-9: the nodes are rounded to 8 dp).
- The FRED ladder of 2026-10-27 (plan section 1): AAA 0.42 %, AA 0.63, A 0.73, BBB 1.09, BB 1.87, B 3.22, CCC 13.21 = 42 / 63 / 73 / 109 /
  187 / 322 / 1,321 bp; the floor 42.00 bp.
- ORCL shaped like the real one (plan 3.2): debt 125,337,000,000.00 (R6 at 2026-08-31), 3,023,736,000 shares (cover page 2026-09-08):
  D = 125,337,000,000 / 3,023,736,000 = 41.451039 a share; the price 144.54 (delayed, in session).
- Daily closes alternating 140.00 / 142.80 (x 1.02, then / 1.02): every log return is +L or -L with L = ln 1.02 = 0.019802627296; a 1,000-return
  window has 500 of each, mean 0, so the sample sd = L x sqrt(1000/999) = 0.019802627296 x 1.000500375 = 0.019812536042; x sqrt(252)
  (15.874507866) = 0.314513 -> sigma 0.3145.
- Thu 2026-10-29 11:00 ET: the last COMPLETE session (close + 20 minutes) is Wed 10-28; "the last 2 sessions" of a desk preview are Tue 10-27 and
  Wed 10-28.  The 5Y maturity is 2031-12-20 (the 2026-09-21 roll): T = 1,878 days / 365 live (from 10-29) and 1,879 / 365 for a VM of 10-28.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
import threading
import time
import types
import unittest
from datetime import date, timedelta
from decimal import Decimal

from tests.terminal_helpers import execute, make_runtime
from finsim.terminal import api, config, db as _db
from finsim.terminal.calendar import TradingCalendar
from finsim.terminal.clock import epoch_of
from finsim.terminal.config import BROWSER_UA
from finsim.terminal.products import cds_model as cm
from finsim.terminal.quotes import worker as qworker
from finsim.terminal.quotes.http import ProviderHealth
from finsim.terminal.quotes.providers import ProviderSet, YahooChart
from finsim.terminal.quotes.rates import CurveStore, RateStore

D = Decimal
HOOKS = "CDS" in getattr(_db, "OTC_KINDS", ())
SKIP_WHY = ("the single-name CDS hook edits are not applied (hooks_b1.py H1-1 / H1-2: the otc_contracts CHECK and db.OTC_KINDS gain CDS):"
            " the test that reads an OPEN CDS contract runs on a rehearsal copy")
FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "terminal", "cds", "yahoo")
THU_11 = "2026-10-29T11:00:00-04:00"
DFS = {"0.5": "0.98039216", "1.0": "0.96116878", "1.5": "0.94232233", "2.0": "0.92384543", "2.5": "0.90573081", "3.0": "0.88797138", "3.5": "0.87056018",
       "4.0": "0.85349037", "4.5": "0.83675527", "5.0": "0.82034830", "5.5": "0.80426304", "6.0": "0.78849318"}
LADDER_1027 = {"AAA": "0.42", "AA": "0.63", "A": "0.73", "BBB": "1.09", "BB": "1.87", "B": "3.22", "CCC": "13.21"}
LADDER_BP = {b: D(v) * 100 for b, v in LADDER_1027.items()}
ORCL_CIK, VZ_CIK, KBH_CIK, F_CIK, ZZ_CIK = 1341439, 732712, 795266, 37996, 9990001
L = math.log(1.02)
SQRT252 = math.sqrt(252)
ORCL_DEBT_C = 12533700000000
ORCL_SHARES = 3023736000


def _cal():
    return TradingCalendar(config.holidays_path())


def _sessions(cal, last: date, n: int):
    out, d = [], last
    while len(out) < n:
        if cal.is_business_day(d):
            out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))


def _alternating(cal, last: date, n: int, a="140.00", b="142.80"):
    """``n`` closes on the trading days up to ``last``, alternating a / b from the oldest."""
    return [(d, a if i % 2 == 0 else b) for i, d in enumerate(_sessions(cal, last, n))]


def _rel(tc, want, got, what, tol=1e-9):
    want, got = float(want), float(got)
    tc.assertLessEqual(abs(got - want), tol * max(abs(want), 1e-300), f"{what}: {got!r} vs {want!r}")


# ---- Appendix A: the CreditGrades vectors (12 significant digits; checked to 1e-9 relative) -----------------------------------------------------
V = {
    "V1": dict(args=(50, 0.35, 40, 5, 0.04), LD=20, sigma_A=0.25, d=3.82960999297, A_T=0.634428877022, P0=0.999985261119, PT=0.935395466934, xi=1.44,
               z=1.23693168769, G_xi=1.39793633687e-05, G_T_xi=0.0531499540613, H=0.0562864701075, c=0.00759707452873, raw="75.97"),
    "V2": dict(args=(300, 0.30, 5, 5, 0.04), LD=2.5, sigma_A=0.297520661157, d=132.395088328, A_T=0.729789503266, P0=1.0, PT=0.999999999767,
               xi=1.01673611111, z=1.07413473647, G_xi=1.35673383664e-58, G_T_xi=1.85061363504e-10, H=1.92742848471e-10, c=2.55191017886e-11, raw="0.00"),
    "V3": dict(args=(12, 0.55, 30, 5, 0.04), LD=15, sigma_A=0.244444444444, d=1.96951371067, A_T=0.62351057096, P0=0.966800173471, PT=0.621512807108,
               xi=1.50619834711, z=1.26049314762, G_xi=0.0316766906791, G_T_xi=0.328638780548, H=0.315403389455, c=0.0586935300167, raw="586.94"),
    "V4": dict(args=(50, 0.35, 40, 3, 0.04), LD=20, sigma_A=0.25, d=3.82960999297, A_T=0.526782687643, P0=0.999985261119, PT=0.979444756474, xi=1.44,
               z=1.23693168769, G_xi=1.39793633687e-05, G_T_xi=0.0178034406123, H=0.0188442196555, c=0.00402498076378, raw="40.25"),
}
VQ = {   # the quadrature path (P(0) < 0.95): I instead of xi, z, G and H
    "V5": dict(args=(2, 0.90, 40, 5, 0.04), LD=20, sigma_A=0.0818181818182, d=1.20359171208, A_T=0.351384510729, P0=0.413606633008,
               PT=0.347385350754, I=1.71863075879, c=0.225821525599, raw="2258.22"),
    "V6": dict(args=(0.01, 2.0, 40, 5, 0.04), sigma_A=0.000999500249875, d=1.09472137085, P0=0.203664260132, PT=0.203657657495, I=0.922937213944,
               c=0.517700475474, raw="5177.00"),
    "V7": dict(args=(0.05, 1.5, 40, 5, 0.04), sigma_A=0.00374064837905, d=1.09690971941, P0=0.208201857772, PT=0.20810748491, I=0.943308095548,
               c=0.503685073037, raw="5036.85"),
}


class CreditGradesVectorTests(unittest.TestCase):
    """Pure: no database, no hooks."""

    def test_closed_form_vectors_v1_to_v4_every_intermediate(self):
        for name, v in V.items():
            got = cm.cg_spread(*v["args"])
            self.assertTrue(got["ok"], (name, got))
            self.assertEqual(cm.METHOD_CLOSED, got["method"], name)
            for k in ("LD", "sigma_A", "d", "A_T", "PT", "xi", "z", "G_xi", "G_T_xi", "H", "c"):
                _rel(self, v[k], got[k], f"{name} {k}")
            if name == "V2":
                self.assertLessEqual(abs(got["P0"] - 1.0), 1e-12)      # P(0) = 1 to float precision (ln d / A_0 = 4.886 / 0.3 = 16.3)
            else:
                _rel(self, v["P0"], got["P0"], f"{name} P0")
            self.assertEqual(D(v["raw"]), got["raw_bp"], name)
            self.assertFalse(got["distressed"], name)

    def test_v2_does_not_take_the_far_barrier_shortcut(self):
        # ln d / A_T = ln 132.395 / 0.72979 = 4.88579 / 0.72979 = 6.6948 < 8 (plan: 6.69): the full formula gives 2.55e-7 bp, which quantizes to 0.00 bp
        got = cm.cg_spread(300, 0.30, 5, 5, 0.04)
        self.assertEqual(cm.METHOD_CLOSED, got["method"])
        _rel(self, 6.6948, got["ln_d_over_A_T"], "ln d / A_T", tol=1e-4)
        self.assertEqual(D("0.00"), got["raw_bp"])

    def test_quadrature_vectors_v5_to_v7(self):
        for name, v in VQ.items():
            got = cm.cg_spread(*v["args"])
            self.assertTrue(got["ok"], (name, got))
            self.assertEqual(cm.METHOD_QUAD, got["method"], name)
            for k in ("sigma_A", "d", "P0", "PT", "I", "c") + (("LD", "A_T") if "LD" in v else ()):
                _rel(self, v[k], got[k], f"{name} {k}")
            self.assertEqual(D(v["raw"]), got["raw_bp"], name)
            self.assertTrue(got["distressed"], name)                  # P(0) < 0.95
            self.assertNotIn("H", got)                                # the quadrature reports I instead of xi, z, G and H

    def test_v5_closed_form_agrees_with_the_quadrature(self):
        # Appendix A, first table: V5's closed form gives xi 13.4444444444, z 3.49293820214, G(xi) 0.483130472672, G(T + xi) 0.518433832295,
        # H 0.060446332825 and c* 0.225821525599 = the quadrature's c* (the closed form's denominator is exactly r I)
        q = cm.cg_spread(2, 0.90, 40, 5, 0.04)
        sig, lam = q["sigma_A"], 0.3
        cf = cm._closed_form(sig, lam, q["d"], math.log(q["d"]), q["P0"], q["PT"], 5.0, 0.04, 0.40)
        for k, want in (("xi", 13.4444444444), ("z", 3.49293820214), ("G_xi", 0.483130472672), ("G_T_xi", 0.518433832295), ("H", 0.060446332825),
                        ("c", 0.225821525599)):
            _rel(self, want, cf[k], f"V5 closed form {k}")
        _rel(self, q["c"], cf["c"], "V5 both forms agree", tol=1e-11)
        # r I = the closed form's denominator: 0.04 x 1.71863075879 = 0.0687452303516
        _rel(self, 0.04 * 1.71863075879, cf["den"], "V5 denominator = r I")

    def test_v6_closed_form_raises_and_no_exception_leaves_cg_spread(self):
        # V6: sigma = 2.0 x 0.01 / 20.01 = 0.0009995; xi = 0.09 / sigma^2 = 9.0e4; e^(r xi) = e^3603 overflows a float
        q = cm.cg_spread(0.01, 2.0, 40, 5, 0.04)
        with self.assertRaises(OverflowError):
            cm._closed_form(q["sigma_A"], 0.3, q["d"], math.log(q["d"]), q["P0"], q["PT"], 5.0, 0.04, 0.40)
        self.assertTrue(q["ok"])
        self.assertEqual((cm.METHOD_QUAD, D("5177.00")), (q["method"], q["raw_bp"]))

    def test_v7_takes_the_quadrature_value_not_the_cancelled_closed_form(self):
        # near the barrier the closed form cancels: 5,024.92 bp (Appendix A) against the quadrature's 5,036.85 bp
        q = cm.cg_spread(0.05, 1.5, 40, 5, 0.04)
        cf = cm._closed_form(q["sigma_A"], 0.3, q["d"], math.log(q["d"]), q["P0"], q["PT"], 5.0, 0.04, 0.40)
        self.assertEqual(D("5024.92"), cm._bp(1e4 * cf["c"]))
        self.assertEqual(D("5036.85"), q["raw_bp"])

    def test_closed_form_overflow_with_healthy_survival_falls_back_to_the_quadrature(self):
        # S 29.7, sigma_S 0.0017, D 40: sigma = 0.0017 x 29.7 / 49.7 = 0.001016, xi = 0.09 / 1.03e-6 = 8.7e4, e^(0.04 x 8.7e4) overflows;
        # d = 49.7 / 20 x e^0.09 = 2.719, A_T = 0.30001: ln d / A_T = 3.33 < 8 and P(0) = Phi(3.18) - 2.719 x Phi(-3.48) = 0.9986 >= 0.95, so the
        # closed form IS tried, raises, and the quadrature answers - no exception leaves cg_spread
        q = cm.cg_spread(29.7, 0.0017, 40, 5, 0.04)
        self.assertTrue(q["ok"], q)
        self.assertGreaterEqual(q["P0"], 0.95)
        self.assertEqual(cm.METHOD_QUAD, q["method"])
        self.assertIn("OverflowError", q["fallback"])
        self.assertTrue(q["raw_bp"].is_finite() and q["raw_bp"] >= 0)

    def test_far_from_the_barrier_is_exactly_zero(self):
        # S 1,000, sigma_S 0.20, D 1: LD 0.5, sigma 0.19990, d = 1000.5 / 0.5 x e^0.09 = 2,189.4, ln d = 7.691; A_T = sqrt(0.03996 x 5 + 0.09)
        # = 0.53847: ln d / A_T = 14.3 >= 8 -> raw 0.00 bp exactly, nothing raised
        q = cm.cg_spread(1000, 0.20, 1, 5, 0.04)
        self.assertEqual((True, cm.METHOD_FAR, D("0.00"), 0.0), (q["ok"], q["method"], q["raw_bp"], q["c"]))

    def test_guards_never_raise(self):
        self.assertEqual("NO_PRICE", cm.cg_spread(0, 0.3, 40, 5, 0.04)["code"])
        self.assertEqual("NO_VOL", cm.cg_spread(50, 0, 40, 5, 0.04)["code"])
        self.assertEqual("NOT_COVERED", cm.cg_spread(50, 0.3, 0, 5, 0.04)["code"])
        self.assertEqual("MODEL_DOMAIN", cm.cg_spread(50, 0.3, 40, 0, 0.04)["code"])            # T not after the valuation date
        self.assertEqual("MODEL_DOMAIN", cm.cg_spread(float("nan"), 0.3, 40, 5, 0.04)["code"])
        self.assertEqual("MODEL_DOMAIN", cm.cg_spread("x", 0.3, 40, 5, 0.04)["code"])
        self.assertEqual("MODEL_DOMAIN", cm.cg_spread(50, 0.3, 40, 5, 0.04, R=1.0)["code"])
        for bad in (cm.cg_spread(1e308, 1e308, 1e-308, 5, 0.04), cm.cg_spread(1e-300, 50.0, 1e300, 1e6, 5.0)):
            self.assertIn(bad["ok"], (True, False))                                              # an answer, never an exception

    def test_rate_not_above_zero_takes_the_quadrature(self):
        # the closed form's denominator is r x I: with r = 0 it is 0, so the integral form is used (V1's inputs at r 0)
        q = cm.cg_spread(50, 0.35, 40, 5, 0.0)
        self.assertEqual(cm.METHOD_QUAD, q["method"])
        self.assertTrue(q["raw_bp"] > 0)

    def test_raw_bp_quantization_half_even_from_repr(self):
        self.assertEqual(D("75.97"), cm._bp(75.9707452873))
        self.assertEqual(D("0.12"), cm._bp(0.125))              # Decimal("0.125") -> 0.12 (half even)
        self.assertEqual(D("0.14"), cm._bp(0.135))              # repr(0.135) = "0.135" -> 0.14 (half even), never the binary 0.13499999...
        self.assertEqual("0.00", str(cm._bp(-1e-9)))            # never "-0.00"


class CalibrationTests(unittest.TestCase):
    """Plan 3.3 and Appendix A: model = the AAA floor (42.00 bp) + raw; the rating-like bucket; the equity shocks of 5.2."""

    def test_calibrated_vectors_and_buckets(self):
        floor = LADDER_BP["AAA"]
        want = {"V1": ("117.97", "BBB"), "V2": ("42.00", "AAA"), "V3": ("628.94", "B"), "V4": ("82.25", "A"), "V5": ("2300.22", None), "V6": ("5219.00", None),
                "V7": ("5078.85", None)}
        allv = dict(V, **VQ)
        for name, (spread, bucket) in want.items():
            q = cm.cg_spread(*allv[name]["args"])
            s = floor + q["raw_bp"]
            self.assertEqual(D(spread), s, name)
            if bucket is None:
                self.assertTrue(q["distressed"], name)
            else:
                # V1: |ln(117.97 / 109)| = 0.079 < BB |ln(117.97 / 187)| = 0.461; V3: |ln(628.94 / 322)| = 0.669 < CCC |ln(628.94 / 1321)| = 0.742;
                # V4: |ln(82.25 / 73)| = 0.119 < BBB |ln(82.25 / 109)| = 0.282; V2: 42.00 is the AAA floor itself (distance 0)
                self.assertEqual(bucket, cm.bucket_of(s, LADDER_BP), name)
                self.assertFalse(q["distressed"], name)

    def test_bucket_tie_goes_to_the_wider_bucket(self):
        # A 100 / BBB 400: 200.00 bp is ln 2 = 0.693147180560 from each (Decimal ln, 12 digits): the wider bucket, BBB
        self.assertEqual("BBB", cm.bucket_of(D("200.00"), {"A": D(100), "BBB": D(400)}))
        self.assertEqual("A", cm.bucket_of(D("199.99"), {"A": D(100), "BBB": D(400)}))
        # two buckets with the same OAS: the lower rating (the later in the ladder) is the wider one
        self.assertEqual("AA", cm.bucket_of(D("42.00"), {"AAA": D(42), "AA": D(42), "A": D(73)}))
        # a spread not above 0 takes the tightest bucket; an empty ladder is an error, never a guessed bucket
        self.assertEqual("AAA", cm.bucket_of(D("-5"), LADDER_BP))
        with self.assertRaises(ValueError):
            cm.bucket_of(D(100), {})
        self.assertEqual("BBB-like (model spread bucket - not a credit rating)", cm.bucket_label("BBB"))

    def test_spread_at_equity_shocks_of_v1(self):
        # Appendix A: V1 with the floor 42.00: S x 0.7 = 35 -> raw 118.31, model 160.31 (BB); S x 1.3 = 65 -> 51.85, 93.85 (BBB); S x 1.5 = 75 ->
        # 41.09, 83.09 (A); S x 1 = 50 -> 117.97 (the model itself)
        model = {"ok": True, "T": "5", "floor_bp": D("42.00"), "inputs": {"S": "50", "sigma": "0.35", "dps": "40", "r": "0.04", "R": "0.40", "Lbar": "0.5",
                                                                        "lambda": "0.3"}}
        self.assertEqual([D("160.31"), D("93.85"), D("83.09"), D("117.97")],
                         [cm.spread_at(model, D(x)) for x in ("0.7", "1.3", "1.5", "1")])
        self.assertEqual("BB", cm.bucket_of(D("160.31"), LADDER_BP))
        # unavailable, never 0: a refused model, a missing input, a shock to a zero price
        self.assertIsNone(cm.spread_at({"ok": False, "code": "NO_VOL"}, D("0.7")))
        self.assertIsNone(cm.spread_at({"ok": True, "T": "5", "floor_bp": "42", "inputs": {"S": "50"}}, D("0.7")))
        self.assertIsNone(cm.spread_at(model, D("0")))

    def test_appendix_b_regression_on_the_saved_2026_10_28_inputs(self):
        # B3 task 7, regression first: the shipped model (debt-only) and the CreditGrades Appendix B variant (debt + 50 % x (liabilities - debt
        # - accounts payable)) on the inputs saved at 18:54 ET on 2026-10-28 (Yahoo last close and 1000-day sigma, the dei cover shares, the SEC
        # balance sheet; T = 1,879 / 365 to 2031-12-20, r 4.76 %, floor 42 bp) reproduce Appendix B to 0.01 bp
        T = 1879 / 365
        rows = (  # name, S (the Yahoo close as fetched), sigma, shares, debt, liabilities, accounts payable, model bp, variant bp, buckets
            ("ORCL", 144.5399932861328, "0.4577", 3023736000, 125337000000, 236063000000, 11063000000, "112.54", "149.08", ("BBB", "BB")),
            ("ORCL 10-K", 144.5399932861328, "0.4577", 3023736000, 129541000000, None, None, "115.74", None, ("BBB", None)),
            ("VZ", 43.18000030517578, "0.2371", 4154775202, 165231000000, 304990000000, 20422000000, "59.89", "78.72", ("AA", "A")),
            ("KBH", 46.16999816894531, "0.3619", 60762736, 2109145000, 3167643000, 0, "124.15", "149.73", ("BBB", "BB")),
            ("KSS", 23.34000015258789, "0.6834", 113376445, 1325000000, 9242000000, 1418000000, "547.33", "812.29", ("B", "CCC")))
        for name, S, sig, sh, debt, liab, ap, want, want_v, buckets in rows:
            m = D("42") + cm.cg_spread(S, float(sig), debt / sh, T, 0.0476)["raw_bp"]
            self.assertEqual((D(want), buckets[0]), (m, cm.bucket_of(m, LADDER_BP)), name)
            if want_v is not None:
                variant = debt + 0.5 * (liab - debt - ap)       # ORCL: 125.337B + 0.5 x (236.063 - 125.337 - 11.063)B = 175.1685B
                mv = D("42") + cm.cg_spread(S, float(sig), variant / sh, T, 0.0476)["raw_bp"]
                self.assertEqual((D(want_v), buckets[1]), (mv, cm.bucket_of(mv, LADDER_BP)), name + " variant")

    def test_maturity_rule(self):
        cal = _cal()
        # the 2026-09-21 roll (20 Sep 2026 a Sunday): 20 December maturities; the 2027-03-22 roll: 20 June
        self.assertEqual([date(2027, 12, 20), date(2029, 12, 20), date(2031, 12, 20), date(2033, 12, 20), date(2036, 12, 20)],
                         [cm.maturity_for(cal, date(2026, 10, 29), n) for n in (1, 3, 5, 7, 10)])
        self.assertEqual(date(2032, 6, 20), cm.maturity_for(cal, date(2027, 3, 22), 5))


class RealisedVolTests(unittest.TestCase):

    def test_the_plan_vector(self):
        # Appendix A: 100, then alternately x 1.01 and x 0.99 ten times: returns ln 1.01 = 0.0099503309 and ln 0.99 = -0.0100503359; mean
        # -0.0000500025; deviations +-0.0100003334; sample variance 10 x 0.0100003334^2 / 9 = 1.111185e-4; sd 0.01054127692; x sqrt 252 =
        # 0.1673375834 -> stored 0.1673
        closes, c = [D(100)], D(100)
        for i in range(10):
            c = c * (D("1.01") if i % 2 == 0 else D("0.99"))
            closes.append(c)
        self.assertEqual([D(100), D(101), D("99.99"), D("100.9899")], closes[:4])
        sigma, n = cm.realised_vol(closes, minimum=10)
        self.assertEqual((D("0.1673"), 10), (sigma, n))
        sd = math.sqrt(10 * ((math.log(1.01) - math.log(0.99)) / 2) ** 2 / 9)
        _rel(self, 0.01054127692, sd, "daily sd", tol=1e-9)

    def test_window_minimum_and_bad_closes(self):
        cal = _cal()
        closes = [D(x) for _d, x in _alternating(cal, date(2026, 10, 28), 1201)]
        # the last 1,001 closes: 1,000 returns, 500 up and 500 down: sigma = L x sqrt(1000/999) x sqrt 252 = 0.019812536 x 15.874508 = 0.314513
        self.assertEqual((D("0.3145"), 1000), cm.realised_vol(closes))
        # 251 closes = 250 returns: allowed; 250 closes = 249 returns: SHORT_HISTORY
        self.assertEqual(250, cm.realised_vol(closes[-251:])[1])
        with self.assertRaises(cm.ShortHistory):
            cm.realised_vol(closes[-250:])
        with self.assertRaises(ValueError):
            cm.realised_vol([D(1), D(0), D(1)] * 100, minimum=2)


# ---- a C-ENT stub in the shape of credit_entities.entity (B2) -------------------------------------------------------------------------------------
def _fund(debt_c=ORCL_DEBT_C, shares=ORCL_SHARES, end="2026-08-31", shares_end="2026-09-08", known_from="2026-09-10", form="10-Q", recipe="R6",
          parts=None):
    return {"end_date": end, "accn": f"acc-{end}", "known_from": known_from, "filed": known_from, "form": form, "debt_c": debt_c, "debt_recipe": recipe,
            "debt_parts": parts if parts is not None else {"LongTermNotesAndLoans": 11771200000000, "NotesPayableCurrent": 762500000000},
            "debt_note": None, "liabilities_c": None, "shares": shares, "shares_end": shares_end, "shares_tag": "dei:EntityCommonStockSharesOutstanding",
            "source": "test fixture (SEC XBRL shape)"}


class _Entities:
    """``services.credit_entities`` stub: status OFFERED / NOT_OFFERED with status_reason and status_words, flags with their source,
    point-in-time fundamentals (``known_from``), the instrument id, the SEC check (``refresh_entity``) and the universe state."""

    def __init__(self):
        self.rows, self.funds, self.refreshed, self.resolving, self.state = {}, {}, [], set(), "ready"

    def add(self, cik, ticker, name, instrument_id, funds=(), offered=True, reason=None, words=None, flag="", flag_source=None, checked_ts=None):
        self.rows[cik] = {"cik": cik, "ticker": ticker, "name": name, "exchange": "NYSE", "tickers": [ticker], "status": "OFFERED" if offered else "NOT_OFFERED",
                          "status_reason": reason, "status_words": words or ("offered" if offered else f"not offered ({reason})"), "offered": offered,
                          "flag": flag, "flag_source": flag_source, "flag_note": None, "limited": flag == "FINANCIAL", "limited_reason": None,
                          "checked_ts": checked_ts, "instrument_id": instrument_id}
        self.funds[cik] = [dict(f, cik=cik) for f in funds]

    def _pick(self, cik, d=None):
        rows = [f for f in self.funds.get(cik, []) if d is None or f["known_from"] <= d.isoformat()]
        return dict(max(rows, key=lambda f: f["end_date"])) if rows else None

    def entity(self, conn, key, today=None):
        cik = key if isinstance(key, int) else next((c for c, r in self.rows.items() if r["ticker"] == str(key).upper()), None)
        r = self.rows.get(cik)
        return None if r is None else dict(r, fundamentals=self._pick(cik))

    def fundamentals_now(self, conn, cik, today=None):
        return self._pick(cik)

    def fundamentals_on(self, conn, cik, d, today=None):
        return self._pick(cik, d)

    def instrument_for(self, services, ent):
        if ent["cik"] in self.resolving:
            return None, "resolving"
        return (ent["instrument_id"], "ok") if ent.get("instrument_id") else (None, "unavailable: not on the quote provider")

    def refresh_entity(self, services, cik, getter=None):
        self.refreshed.append(cik)
        self.rows[cik]["checked_ts"] = int(services.clock.epoch())
        return {"ok": True, "cik": cik}

    def universe_status(self, conn, today=None):
        words = {"offline": "the reference-entity list is not loaded: the terminal runs offline (SEC EDGAR is not contacted)"}.get(self.state)
        return {"state": self.state, "message": words}


class _ProviderLike:
    """B4-R1 (integration): the shape of the served terminal's ``services.marks`` (``marks.MarkProvider``): ``snapshot(ids)`` and
    ``mark_for(inst)``, and NO ``mark()``.  It wraps the test runtime's FixedMarks and records each ``snapshot`` call."""

    def __init__(self, fixed):
        self._fixed, self.calls = fixed, []

    def snapshot(self, ids=None):
        self.calls.append(None if ids is None else list(ids))
        return self._fixed.snapshot(ids)

    def mark_for(self, inst, at=None):
        return self._fixed.snapshot().mark(getattr(inst, "instrument_id", inst))


def _put_ladder(rates, d: date, values=None, only=None):
    for b, v in (values or LADDER_1027).items():
        if only is None or b in only:
            rates.put(cm.LADDER_SERIES[b], [(d, D(v))], persist=False)


class _ModelCase(unittest.TestCase):
    """A test runtime with the module bound: the FRED ladder of 10-27 and 10-28, the fixed DFS, ORCL in the C-ENT stub, its delayed mark and
    1,010 alternating daily closes through Thu 10-29 (today's partial bar, as Yahoo serves it in session) in the FakeProvider."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-cdsm-")
        self.rt = self._runtime()

    def _runtime(self, at=THU_11):
        rt = make_runtime(self.tmp, at=at)
        rt.rates = RateStore()
        for d in (date(2026, 10, 27), date(2026, 10, 28)):
            _put_ladder(rt.rates, d)
        rt.curve = CurveStore()
        rt.curve.set_fixed_dfs(DFS, date(2026, 10, 20))
        self.ce = rt.credit_entities = _Entities()
        self.ce.add(ORCL_CIK, "ORCL", "ORACLE CORP", "ORCL", funds=[_fund()])
        rt.marks.set("ORCL", "144.54", "delayed", rt.clock.epoch())
        rt.providers = types.SimpleNamespace(yahoo=rt.fp)
        rt.fp.set_daily("ORCL", _alternating(rt.cal, date(2026, 10, 29), 1010))
        cm.register(rt.bus)
        cm.bind(rt)
        rt.cds_model = cm
        return rt

    def tearDown(self):
        for n in ("cds_inputs", "cds_eod"):
            qworker.unregister_refresher(n)
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def q(self, sql, params=()):
        with self.rt.db.reader() as conn:
            return [tuple(r) for r in conn.execute(sql, params).fetchall()]

    def drain(self, rt=None):
        rt = rt or self.rt
        return cm.refresh_inputs(rt, rt.clock.epoch())

    def mat5(self):
        return cm.maturity_for(self.rt.cal, date(2026, 10, 29), 5)

    def record_close(self, iid, d: date, price, rt=None):
        rt = rt or self.rt
        with rt.db.writer() as tx:
            tx.conn.execute("INSERT OR REPLACE INTO marks(instrument_id, mark_date, kind, price, currency, quality, source, method, quote_time, fetched_at, note)"
                            " VALUES (?,?,'CLOSE',?,'USD','close','test','direct',?,?,NULL)", (iid, d.isoformat(), str(price), epoch_of(d, "16:00"),
                                                                                                  epoch_of(d, "16:05")))


class ModelSpreadLiveTests(_ModelCase):

    def test_first_preview_queues_the_fetch_then_computes(self):
        rt, ts = self.rt, self.rt.clock.epoch()
        self.assertEqual(date(2031, 12, 20), self.mat5())
        m = cm.model_spread(rt, ORCL_CIK, self.mat5())
        self.assertEqual(("INPUTS_PENDING", 1500), (m["code"], m["retry_ms"]))
        self.assertIn("ORACLE CORP's SEC filings and 1,000 daily closes", m["reason"])
        self.assertEqual({}, {k: v for k, v in rt.fp.calls.items() if k in ("daily", "events")})      # nothing fetched on the calling thread
        self.assertEqual("queued", cm.queue_status(rt)["entries"][0]["status"])
        self.assertIn("ORCL: ok", self.drain())
        self.assertEqual([ORCL_CIK], self.ce.refreshed)                      # never checked: the SEC submissions check ran first
        self.assertEqual((1, 1), (rt.fp.calls["daily"], rt.fp.calls["events"]))
        # the last 2 complete sessions at 11:00 ET on Thu 10-29: Tue 10-27 and Wed 10-28, each from 1,000 returns -> 0.3145 (module docstring)
        self.assertEqual([("2026-10-27", "0.3145", 1000, "2026-10-29"), ("2026-10-28", "0.3145", 1000, "2026-10-29")],
                         self.q("SELECT vol_date, sigma, n_returns, events_through FROM cds_vol WHERE ticker='ORCL' ORDER BY vol_date"))
        m = cm.model_spread(rt, ORCL_CIK, self.mat5())
        self.assertTrue(m["ok"], m)
        inp = m["inputs"]
        self.assertEqual(("144.54", "0.3145", 1000, "2026-10-28", ORCL_DEBT_C, ORCL_SHARES, 1878),
                         (inp["S"], inp["sigma"], inp["sigma_window"], inp["sigma_through"], inp["debt_c"], inp["shares"], inp["T_days"]))
        # D = 125,337,000,000 / 3,023,736,000 = 41.451039 a share; T = 1,878 / 365 = 5.1452055; r = 2 ln 1.02 = 0.0396052546 (the DFS)
        _rel(self, 125337000000 / 3023736000, float(inp["dps"]), "debt per share", tol=1e-12)
        _rel(self, 1878 / 365, float(m["T"]), "T", tol=1e-15)
        self.assertLess(abs(float(inp["r"]) - 2 * L), 1e-8)
        # the spread is the floor 42.00 + CreditGrades on exactly these inputs (cg_spread is checked against Appendix A above)
        cg = cm.cg_spread(144.54, 0.3145, float(inp["dps"]), float(m["T"]), float(inp["r"]))
        self.assertEqual((D("42.00"), cg["raw_bp"], D("42.00") + cg["raw_bp"]), (m["floor_bp"], m["raw_bp"], m["spread_bp"]))
        self.assertEqual(cm.bucket_of(m["spread_bp"], LADDER_BP), m["bucket"])
        self.assertEqual((cm.METHOD_CLOSED, "computed", True, None, "MODEL"), (m["method"], m["quality"], m["dealable"], m["dealable_reason"], m["label"]))
        # the inputs named for swaps._input_stamp: the live price with its time, the floor's date, the curve's date
        self.assertEqual({"instrument_id": "ORCL", "quote_time": ts}, {k: m["underlying"][k] for k in ("instrument_id", "quote_time")})
        self.assertEqual(({"as_of": "2026-10-28", "series_id": "BAMLC0A1CAAA"}), m["proxy"])
        self.assertEqual("2026-10-20", m["curve_as_of"])
        self.assertEqual("2026-10-28", m["ladder"]["as_of"])
        for k in ("LD", "sigma_A", "d", "A_T", "P0", "PT", "xi", "z", "G_xi", "G_T_xi", "H", "c"):
            self.assertIsNotNone(m["intermediate"][k], k)
        # the desk list keeps this session's 5Y result in memory
        kept = cm.last_models([ORCL_CIK])[ORCL_CIK]
        self.assertEqual((m["spread_bp"], m["bucket"]), (kept["spread_bp"], kept["bucket"]))

    def test_b4r1_the_live_mark_provider_without_mark_is_frozen_for_the_name(self):
        """B4-R1: a served terminal's services.marks is marks.MarkProvider (snapshot() and mark_for(), no mark()).  B1's previews pass no
        snapshot, so the model reads services.marks: it must freeze it for the one name.  Before the fix the AttributeError was swallowed and
        every live price read 'no share price for ORCL' (B4's online walk, 12:13-12:31 EDT)."""
        rt = self.rt
        prov = rt.marks = _ProviderLike(rt.marks)
        self.assertFalse(hasattr(prov, "mark"))
        # the first preview: the price is found (144.54 delayed), the volatility is not fetched yet -> INPUTS_PENDING (not NO_PRICE)
        self.assertEqual("INPUTS_PENDING", cm.model_spread(rt, ORCL_CIK, self.mat5())["code"])
        self.assertIn(["ORCL"], prov.calls)                               # one FrozenMarks of the name, as the TRS does
        self.drain()
        m = cm.model_spread(rt, ORCL_CIK, self.mat5())
        self.assertTrue(m["ok"], m)
        self.assertEqual(("144.54", "delayed"), (m["inputs"]["S"], m["inputs"]["S_quality"]))

    def test_a_split_after_the_share_count_date_divides_the_debt_per_share(self):
        rt = self.rt
        rt.fp.set_events("ORCL", splits=[("2026-10-15", "2:1"), ("2026-06-01", "3:1")])
        self.assertEqual("INPUTS_PENDING", cm.model_spread(rt, ORCL_CIK, self.mat5())["code"])
        self.drain()
        self.assertEqual([("2026-06-01", "3", "1"), ("2026-10-15", "2", "1")],
                         self.q("SELECT split_date, numerator, denominator FROM cds_splits WHERE ticker='ORCL' ORDER BY split_date"))
        m = cm.model_spread(rt, ORCL_CIK, self.mat5())
        # only the split dated after the share count's date (2026-09-08) applies: 3,023,736,000 x 2 / 1 = 6,047,472,000 shares;
        # D = 125,337,000,000 / 6,047,472,000 = 20.725520 a share (the 2026-06-01 split is already in the count)
        self.assertEqual((6047472000, ORCL_SHARES), (m["inputs"]["shares"], m["inputs"]["shares_reported"]))
        _rel(self, 125337000000 / 6047472000, float(m["inputs"]["dps"]), "debt per share after the split", tol=1e-12)
        self.assertIn("2026-10-15 2:1", m["inputs"]["shares_adj_note"])

    def test_codes_entity_coverage_and_floor(self):
        rt, mat = self.rt, self.mat5()
        self.assertEqual("ENTITY_UNKNOWN", cm.model_spread(rt, 999, mat)["code"])
        self.ce.state = "loading"
        self.assertEqual(("INPUTS_PENDING", "loading the reference-entity list from SEC EDGAR (first run, about 2-3 minutes)"),
                         tuple(cm.model_spread(rt, 999, mat)[k] for k in ("code", "reason")))
        self.ce.state = "offline"
        self.assertEqual(("ENTITY_UNKNOWN", "the reference-entity list is not loaded: the terminal runs offline (SEC EDGAR is not contacted)"),
                         tuple(cm.model_spread(rt, 999, mat)[k] for k in ("code", "reason")))
        self.ce.state = "ready"
        self.assertEqual("no SEC registrant with CIK 999 is in the reference-entity list", cm.model_spread(rt, 999, mat)["reason"])
        # Ford: no debt tag -> NOT_COVERED with the words; a name without a share count -> NOT_COVERED (NO_SHARES)
        self.ce.add(F_CIK, "F", "FORD MOTOR CO", "F", funds=[], offered=False, reason="NO_DEBT",
                    words="not offered: no debt reported in the latest 10-Q / 10-K XBRL (or it uses the company's own tags)")
        m = cm.model_spread(rt, F_CIK, mat)
        self.assertEqual(("NOT_COVERED", "NO_DEBT"), (m["code"], m["status_reason"]))
        self.assertTrue(m["reason"].startswith("not offered: no debt reported"))
        self.ce.add(VZ_CIK, "VZ", "VERIZON COMMUNICATIONS INC", "VZ", funds=[_fund(debt_c=16523100000000, shares=None)])
        self.assertEqual(("NOT_COVERED", "NO_SHARES"), tuple(cm.model_spread(rt, VZ_CIK, mat)[k] for k in ("code", "status_reason")))
        # the floor: none observed -> FLOOR_UNAVAILABLE; the latest AAA of Thu 10-22 is 5 business days old on 10-29 (10-23, 26, 27, 28, 29) -> FLOOR_STALE
        rt.rates = RateStore()
        self.assertEqual("FLOOR_UNAVAILABLE", cm.model_spread(rt, ORCL_CIK, mat)["code"])
        _put_ladder(rt.rates, date(2026, 10, 22))
        m = cm.model_spread(rt, ORCL_CIK, mat)
        self.assertEqual("FLOOR_STALE", m["code"])
        self.assertIn("2026-10-22, 5 business days old", m["reason"])
        rt.rates = None
        self.assertEqual("FLOOR_UNAVAILABLE", cm.model_spread(rt, ORCL_CIK, mat)["code"])

    def test_codes_price(self):
        rt, mat = self.rt, self.mat5()
        # no mark for VZ and no quote worker to wait for one -> NO_PRICE at once
        self.ce.add(VZ_CIK, "VZ", "VERIZON COMMUNICATIONS INC", "VZ", funds=[_fund(debt_c=16523100000000, shares=4154775202)])
        self.assertEqual("NO_PRICE", cm.model_spread(rt, VZ_CIK, mat)["code"])
        rt.marks.set("ORCL", "144.54", "stale", rt.clock.epoch() - 3600)
        self.assertEqual("PRICE_STALE", cm.model_spread(rt, ORCL_CIK, mat)["code"])
        # out of session (Thu 18:00 ET): the price must be a print of the 16:00 close (15 minutes of slack): 13:00 is older -> PRICE_STALE
        rt.clock.set("2026-10-29T18:00:00-04:00")
        rt.marks.set("ORCL", "144.54", "close", epoch_of(date(2026, 10, 29), "13:00"))
        m = cm.model_spread(rt, ORCL_CIK, mat)
        self.assertEqual("PRICE_STALE", m["code"])
        self.assertIn("older than its session's last close", m["reason"])
        rt.marks.set("ORCL", "144.54", "close", epoch_of(date(2026, 10, 29), "16:00") + 2)
        self.assertNotIn(cm.model_spread(rt, ORCL_CIK, mat).get("code"), ("PRICE_STALE", "NO_PRICE"))

    def test_codes_volatility(self):
        rt, mat = self.rt, self.mat5()
        # KBH listed 200 sessions ago: the fetch finds fewer than 250 returns -> SHORT_HISTORY with the listing date, kept 10 minutes
        self.ce.add(KBH_CIK, "KBH", "KB HOME", "KBH", funds=[_fund(debt_c=210914500000, shares=60762736, shares_end="2026-09-30")])
        rt.marks.set("KBH", "46.17", "delayed", rt.clock.epoch())
        bars = _alternating(rt.cal, date(2026, 10, 29), 201, "46.00", "46.92")
        rt.fp.set_daily("KBH", bars)
        self.assertEqual("INPUTS_PENDING", cm.model_spread(rt, KBH_CIK, mat)["code"])
        self.drain()
        m = cm.model_spread(rt, KBH_CIK, mat)
        self.assertEqual("SHORT_HISTORY", m["code"])
        self.assertIn(f"fewer than 250 daily closes (listed since {bars[0][0].isoformat()})", m["reason"])
        # a provider failure -> NO_VOL with the reason (the entry keeps it 10 minutes, then the next request queues a new fetch)
        rt.fp.fail("ORCL")
        cm.model_spread(rt, ORCL_CIK, mat)
        self.drain()
        m = cm.model_spread(rt, ORCL_CIK, mat)
        self.assertEqual("NO_VOL", m["code"])
        self.assertIn("could not be fetched", m["reason"])
        rt.fp.clear_failure("ORCL")
        rt.clock.advance(cm.ERROR_KEEP_S + 1)
        self.assertEqual("INPUTS_PENDING", cm.model_spread(rt, ORCL_CIK, mat)["code"])

    def test_volatility_age_rules(self):
        rt, mat = self.rt, self.mat5()
        cm.model_spread(rt, ORCL_CIK, mat)
        self.drain()
        # Thu 18:00 ET: Thursday's session is complete (16:20) but its row is not stored: the 10-28 row is 1 business day old -> labelled, a
        # refresh queued in the background (the entry is 7 hours old)
        rt.clock.set("2026-10-29T18:00:00-04:00")
        rt.marks.set("ORCL", "144.54", "close", epoch_of(date(2026, 10, 29), "16:00") + 2)
        m = cm.model_spread(rt, ORCL_CIK, mat)
        self.assertTrue(m["ok"], m)
        self.assertIn("volatility as of 2026-10-28, 1 business day before the last complete session", m["notes"])
        self.assertEqual("queued", cm.queue_status(rt)["entries"][-1]["status"])
        # five business days later (Thu 11-05 18:00) with no new row: 10-28 is 6 business days before 11-05 -> NO_VOL once the refetch found nothing
        self.ce.rows[ORCL_CIK]["checked_ts"] = int(rt.clock.epoch())
        rt.clock.set("2026-11-05T18:00:00-05:00")
        rt.marks.set("ORCL", "144.54", "close", epoch_of(date(2026, 11, 5), "16:00") + 2)
        rt.fp.set_daily("ORCL", _alternating(rt.cal, date(2026, 10, 29), 1010))        # the provider has nothing after 10-29
        _put_ladder(rt.rates, date(2026, 11, 4))
        st = cm._state(rt)
        st.fetched["ORCL"]["events_through"] = "2026-11-05"
        with st.lock:
            st.entries[ORCL_CIK].update(status="ready", ts=int(rt.clock.epoch()), since_done="2026-11-04", events_through="2026-11-05")
        m = cm.model_spread(rt, ORCL_CIK, mat)
        self.assertEqual("NO_VOL", m["code"])
        self.assertIn("6 business days before the last complete session 2026-11-05", m["reason"])

    def test_codes_curve_and_domain(self):
        rt, mat = self.rt, self.mat5()
        cm.model_spread(rt, ORCL_CIK, mat)
        self.drain()
        rt.curve = CurveStore()
        self.assertEqual("NO_CURVE", cm.model_spread(rt, ORCL_CIK, mat)["code"])
        rt.curve.set_fixed_dfs(DFS, date(2026, 10, 20))
        self.assertEqual("MODEL_DOMAIN", cm.model_spread(rt, ORCL_CIK, date(2026, 10, 29))["code"])     # T = 0

    def test_not_offered_entity_is_valued_but_not_dealable(self):
        # an open contract keeps its marks and VM when its name stops being offered: the model is computed, dealable False, the words given
        rt = self.rt
        words = "not offered: reported debt USD 30,000,000.00 is below the USD 100,000,000.00 minimum (assumed)"
        self.ce.rows[ORCL_CIK].update(status="NOT_OFFERED", status_reason="BELOW_MIN_DEBT", offered=False, status_words=words)
        cm.model_spread(rt, ORCL_CIK, self.mat5())
        self.drain()
        m = cm.model_spread(rt, ORCL_CIK, self.mat5())
        self.assertEqual((True, False, words, "BELOW_MIN_DEBT"), (m["ok"], m["dealable"], m["dealable_reason"], m["status_reason"]))

    def test_no_listed_common_reason_reaches_status_reason(self):
        # integration request B2:remainder-R2: C-ENT's NO_LISTED_COMMON (only preferred / warrant / unit / right symbols listed) is one of
        # this module's not-covered reasons, so its code reaches status_reason (the hover) beside C-ENT's own words; with the code alone the
        # module's words are shown (before R2 the code was dropped: status_reason None and the generic "not offered for single-name CDS")
        rt = self.rt
        words = ("not offered: no common stock listed - the SEC's exchange file lists only ORCL-PA (preferred shares, warrants, units or rights);"
                 " the model needs the share price of listed common stock")
        self.ce.rows[ORCL_CIK].update(status="NOT_OFFERED", status_reason="NO_LISTED_COMMON", offered=False, status_words=words)
        cm.model_spread(rt, ORCL_CIK, self.mat5())
        self.drain()
        m = cm.model_spread(rt, ORCL_CIK, self.mat5())
        self.assertEqual((True, False, words, "NO_LISTED_COMMON"), (m["ok"], m["dealable"], m["dealable_reason"], m["status_reason"]))
        self.ce.rows[ORCL_CIK].update(status_words=None)
        m = cm.model_spread(rt, ORCL_CIK, self.mat5())
        self.assertEqual((False, cm.REASON_WORDS["NO_LISTED_COMMON"], "NO_LISTED_COMMON"), (m["dealable"], m["dealable_reason"], m["status_reason"]))
        self.assertTrue(cm.REASON_WORDS["NO_LISTED_COMMON"].startswith("not offered: no common stock listed"))

    def test_balance_sheet_age_rules(self):
        rt, mat = self.rt, self.mat5()
        cm.model_spread(rt, ORCL_CIK, mat)
        self.drain()
        # 2025-06-30 to 2026-10-29 = 486 days > 465: not dealable, "16 months old" (486 / 30.44 = 15.97 -> 16); marks go on
        self.ce.funds[ORCL_CIK] = [_fund(end="2025-06-30", shares_end="2026-09-08", known_from="2025-08-01")]
        m = cm.model_spread(rt, ORCL_CIK, mat)
        self.assertEqual((True, False, "not offered: balance sheet 2025-06-30 is 16 months old"), (m["ok"], m["dealable"], m["dealable_reason"]))
        # 2026-03-31: 212 days (183-465): offered, with the amber age note "7 months old" (212 / 30.44 = 6.96 -> 7)
        self.ce.funds[ORCL_CIK] = [_fund(end="2026-03-31", shares_end="2026-04-20", known_from="2026-04-25")]
        m = cm.model_spread(rt, ORCL_CIK, mat)
        self.assertTrue(m["dealable"])
        self.assertIn("balance sheet 2026-03-31 is 7 months old", m["notes"])

    def test_financial_flag_with_its_source_and_distressed_label(self):
        rt, mat = self.rt, self.mat5()
        self.ce.rows[ORCL_CIK].update(flag="FINANCIAL", flag_source="tags", flag_note="financial (from filing tags: Deposits; SIC not checked yet)")
        rt.marks.set("ORCL", "2.00", "delayed", rt.clock.epoch())
        cm.model_spread(rt, ORCL_CIK, mat)
        self.drain()
        m = cm.model_spread(rt, ORCL_CIK, mat)
        # S 2.00 against D 41.45: LD 20.73, sigma = 0.3145 x 2 / 22.73 = 0.0277, d = 22.73 / 20.73 x e^0.09 = 1.1998, P(0) = Phi(0.457) - 1.1998 x
        # Phi(-0.758) = 0.676 - 0.269 = 0.41 < 0.95: distressed, by the quadrature; still dealable (a negotiated mid is the engine's rule)
        self.assertEqual(("distressed", cm.METHOD_QUAD, True), (m["quality"], m["method"], m["dealable"]))
        self.assertIn(cm.DISTRESSED_LABEL, " ".join(m["notes"]))
        self.assertEqual("FINANCIAL", m["inputs"]["flag"])
        self.assertIn("SIC not checked yet", m["inputs"]["flag_note"])
        self.assertIn("CreditGrades was calibrated for industrial firms", m["inputs"]["flag_note"])

    def test_the_ladder_day_is_one_date_for_every_bucket(self):
        rt = self.rt
        # FRED posted the AAA of 10-28 but not yet CCC: the ladder of 10-27 (the latest date within 3 business days with every bucket)
        rt.rates = RateStore()
        _put_ladder(rt.rates, date(2026, 10, 27))
        _put_ladder(rt.rates, date(2026, 10, 28), only=("AAA", "AA", "A", "BBB", "BB", "B"))
        lad = cm.ladder(rt)
        self.assertEqual(("2026-10-27", 2, D("42.00"), D("1321.00"), []), (lad["as_of"], lad["age_bd"], lad["floor"]["spread_bp"], lad["rows"]["CCC"]["spread_bp"],
                                                                            lad["missing"]))
        # every bucket of 10-28 -> the ladder of 10-28 (1 business day old on 10-29); the spreads in bp are the percents x 100
        _put_ladder(rt.rates, date(2026, 10, 28))
        lad = cm.ladder(rt)
        self.assertEqual(("2026-10-28", 1), (lad["as_of"], lad["age_bd"]))
        self.assertEqual(LADDER_BP, {b: r["spread_bp"] for b, r in lad["rows"].items()})


class ModelSpreadVmTests(_ModelCase):

    def setUp(self):
        super().setUp()
        cm.model_spread(self.rt, ORCL_CIK, self.mat5())
        self.drain()
        self.record_close("ORCL", date(2026, 10, 28), "143.10")

    def test_vm_values_every_input_of_r(self):
        rt = self.rt
        m = cm.model_spread(rt, ORCL_CIK, self.mat5(), valuation_date=date(2026, 10, 28))
        self.assertTrue(m["ok"], m)
        # R = Wed 10-28: the recorded close 143.10, the vol row of 10-28, the AAA OAS OF 10-28, T = 1,879 / 365
        self.assertEqual(("2026-10-28", "143.1", "0.3145", "2026-10-28", 1879), (m["valuation_date"], m["inputs"]["S"], m["inputs"]["sigma"],
                                                                                   m["inputs"]["sigma_through"], m["T_days"]))
        self.assertEqual({"instrument_id": "ORCL", "close_date": "2026-10-28"}, {k: m["underlying"][k] for k in ("instrument_id", "close_date")})
        self.assertEqual("2026-10-28", m["proxy"]["as_of"])
        cg = cm.cg_spread(143.10, 0.3145, float(m["inputs"]["dps"]), 1879 / 365, float(m["inputs"]["r"]))
        self.assertEqual(D("42.00") + cg["raw_bp"], m["spread_bp"])
        self.assertEqual({}, {k: v for k, v in rt.fp.calls.items() if k in ("daily", "events") and v != 1})      # a VM fetches nothing

    def test_vm_close_pending_until_fred_publishes_r(self):
        rt = self.rt
        rt.rates = RateStore()
        _put_ladder(rt.rates, date(2026, 10, 27))
        m = cm.model_spread(rt, ORCL_CIK, self.mat5(), valuation_date=date(2026, 10, 28))
        self.assertEqual(("CLOSE_PENDING", "CLOSE_PENDING", "2026-10-28"), (m["code"], m["vm_outcome"], m["close_date"]))
        self.assertIn("not published yet", m["note"])
        self.assertEqual(epoch_of(date(2026, 10, 29), "12:00"), m["quiet_until"])          # quiet until noon of the next business day (the CDX rule)

    def test_vm_caught_up_when_fred_skipped_r(self):
        rt = self.rt
        rt.rates = RateStore()
        _put_ladder(rt.rates, date(2026, 10, 27))
        _put_ladder(rt.rates, date(2026, 10, 29))
        m = cm.model_spread(rt, ORCL_CIK, self.mat5(), valuation_date=date(2026, 10, 28))
        self.assertEqual(("CAUGHT_UP", "CAUGHT_UP"), (m["code"], m["vm_outcome"]))
        self.assertIn("FRED skipped that date", m["reason"])

    def test_vm_without_the_vol_row_of_r_is_pending_and_queues_the_fetch(self):
        rt = self.rt
        # Mon 10-26: no vol row (the preview stored 10-27 and 10-28 only): CLOSE_PENDING and a fetch queued from 10-26, nothing fetched here
        self.record_close("ORCL", date(2026, 10, 26), "141.00")
        _put_ladder(rt.rates, date(2026, 10, 26))
        calls = dict(rt.fp.calls)
        m = cm.model_spread(rt, ORCL_CIK, self.mat5(), valuation_date=date(2026, 10, 26))
        self.assertEqual(("CLOSE_PENDING", "2026-10-26"), (m["vm_outcome"], m["close_date"]))
        self.assertEqual(calls, rt.fp.calls)
        e = cm._state(rt).entries[ORCL_CIK]
        self.assertEqual(("queued", "2026-10-26"), (e["status"], e["since"]))
        self.drain()
        self.assertEqual(["2026-10-26", "2026-10-27", "2026-10-28"], [r[0] for r in self.q("SELECT vol_date FROM cds_vol WHERE ticker='ORCL' ORDER BY vol_date")])
        m = cm.model_spread(rt, ORCL_CIK, self.mat5(), valuation_date=date(2026, 10, 26))
        self.assertEqual((True, "141", 1881), (m.get("ok"), (m.get("inputs") or {}).get("S"), m.get("T_days")), m)

    def test_vm_close_of_r_not_known(self):
        m = cm.model_spread(self.rt, ORCL_CIK, self.mat5(), valuation_date=date(2026, 10, 27))
        # no recorded close of 10-27 and the live price is of a later session -> CAUGHT_UP (swaps_trs._vm_close, LC-3)
        self.assertEqual(("CAUGHT_UP", "2026-10-27"), (m["vm_outcome"], m["close_date"]))

    def test_b4r1_vm_with_the_live_mark_provider_without_mark(self):
        """B4-R1: the VM path reads the same services.marks.  R = 10-28 has a recorded close (143.10, read from the marks table); R = 10-27
        has none, so swaps_trs.close_on asks the snapshot for the print at the close: with the live MarkProvider (no mark()) that raised
        AttributeError out of model_spread before the fix; now the frozen view is of a later session -> CAUGHT_UP (LC-3)."""
        rt = self.rt
        prov = rt.marks = _ProviderLike(rt.marks)
        m = cm.model_spread(rt, ORCL_CIK, self.mat5(), valuation_date=date(2026, 10, 28))
        self.assertEqual((True, "143.1"), (m.get("ok"), (m.get("inputs") or {}).get("S")), m)
        m = cm.model_spread(rt, ORCL_CIK, self.mat5(), valuation_date=date(2026, 10, 27))
        self.assertEqual(("CAUGHT_UP", "2026-10-27"), (m.get("vm_outcome"), m.get("close_date")), m)
        self.assertIn(["ORCL"], prov.calls)

    def test_vm_on_a_new_runtime_uses_the_persisted_split(self):
        rt = self.rt
        rt.fp.set_events("ORCL", splits=[("2026-10-15", "2:1")])
        cm._state(rt).entries.clear()
        cm.request_inputs(rt, ORCL_CIK, force=True)
        self.drain()
        self.assertEqual([("2026-10-15", "2", "1")], self.q("SELECT split_date, numerator, denominator FROM cds_splits WHERE ticker='ORCL'"))
        for n in ("cds_inputs", "cds_eod"):
            qworker.unregister_refresher(n)
        rt.close()
        # a NEW runtime on the same database: nothing in memory, the split comes from cds_splits
        self.rt = rt2 = self._runtime()
        self.assertEqual({}, dict(cm._state(rt2).fetched))
        m = cm.model_spread(rt2, ORCL_CIK, self.mat5(), valuation_date=date(2026, 10, 28))
        self.assertTrue(m["ok"], m)
        # 3,023,736,000 x 2 = 6,047,472,000 shares: D = 125,337,000,000 / 6,047,472,000 = 20.725520
        self.assertEqual(6047472000, m["inputs"]["shares"])
        _rel(self, 125337000000 / 6047472000, float(m["inputs"]["dps"]), "debt per share", tol=1e-12)
        # live, the split list must be this process's own fetch of the session: INPUTS_PENDING and a fetch queued
        self.assertEqual("INPUTS_PENDING", cm.model_spread(rt2, ORCL_CIK, self.mat5())["code"])
        # a session with no vol row (Fri 10-23) answers CLOSE_PENDING and queues a fetch from that date
        self.record_close("ORCL", date(2026, 10, 23), "140.00", rt=rt2)
        _put_ladder(rt2.rates, date(2026, 10, 23))
        m = cm.model_spread(rt2, ORCL_CIK, self.mat5(), valuation_date=date(2026, 10, 23))
        self.assertEqual(("CLOSE_PENDING", "2026-10-23"), (m["vm_outcome"], m["close_date"]))
        self.assertEqual("2026-10-23", cm._state(rt2).entries[ORCL_CIK]["since"])


class QueueAndRefresherTests(_ModelCase):

    def test_request_inputs_never_blocks_and_does_no_io(self):
        rt = self.rt

        class _Boom:
            def __getattr__(self, name):
                raise AssertionError(f"the network was touched: {name}")
        rt.providers = types.SimpleNamespace(yahoo=_Boom())
        t0 = time.perf_counter()
        for k in range(200):
            r = cm.request_inputs(rt, 1000 + k, name=f"N{k}")
        self.assertLess(time.perf_counter() - t0, 0.5)
        self.assertEqual(("queued", 1500), (r["status"], r["retry_ms"]))
        # another thread holding a request in progress never makes a reader wait on the network: a second request is answered from memory
        out = []
        th = threading.Thread(target=lambda: out.append(cm.request_inputs(rt, 1000)))
        th.start()
        th.join(2)
        self.assertEqual("queued", out[0]["status"])

    def test_offline_refreshers_make_no_call(self):
        rt = self.rt
        rt.providers = ProviderSet.offline_set(rt.fp, rt.clock)
        cm.request_inputs(rt, ORCL_CIK)
        before = dict(rt.fp.calls)
        self.assertIsNone(cm.refresh_inputs(rt, rt.clock.epoch()))
        self.assertIsNone(cm.refresh_eod(rt, rt.clock.epoch()))
        self.assertEqual(before, rt.fp.calls)
        self.assertEqual("queued", cm._state(rt).entries[ORCL_CIK]["status"])

    def test_an_empty_queue_returns_at_once(self):
        rt = self.rt
        self.assertIsNone(cm.refresh_inputs(rt, rt.clock.epoch()))
        self.assertEqual({}, {k: v for k, v in rt.fp.calls.items() if k in ("daily", "events")})

    def test_ready_entries_are_not_fetched_again_for_ten_minutes(self):
        rt = self.rt
        cm.request_inputs(rt, ORCL_CIK)
        self.drain()
        self.assertEqual("ready", cm.request_inputs(rt, ORCL_CIK)["status"])
        self.assertIsNone(self.drain())
        self.assertEqual(1, rt.fp.calls["daily"])
        # an earlier session wanted (a VM back-fill) queues again at once
        self.assertEqual("queued", cm.request_inputs(rt, ORCL_CIK, since=date(2026, 10, 20))["status"])
        rt.clock.advance(cm.REFETCH_MIN_S)
        self.drain()
        self.assertEqual(2, rt.fp.calls["daily"])

    def test_a_resolving_ticker_stays_queued_then_fails_in_words(self):
        rt = self.rt
        self.ce.resolving.add(ORCL_CIK)
        cm.request_inputs(rt, ORCL_CIK)
        self.assertEqual("ORCL: resolving", self.drain())
        e = cm._state(rt).entries[ORCL_CIK]
        self.assertEqual(("queued", 1), (e["status"], e["tries"]))
        self.assertIsNone(self.drain())                     # not before the next 5-second run
        for _ in range(cm.RESOLVE_TRIES):
            rt.clock.advance(cm.INPUTS_INTERVAL_S)
            self.drain()
        e = cm._state(rt).entries[ORCL_CIK]
        self.assertEqual(("error", "NO_PRICE"), (e["status"], e["code"]))
        self.assertEqual(0, rt.fp.calls.get("daily", 0))

    def test_eod_and_the_morning_split_pass_queue_the_open_contracts(self):
        rt = self.rt
        orig = cm._open_ciks
        cm._open_ciks = lambda services: {ORCL_CIK: ("2026-10-21", "ORACLE CORP")}
        try:
            self.assertEqual("1 reference equities queued", cm.refresh_eod(rt, rt.clock.epoch()))
            e = cm._state(rt).entries[ORCL_CIK]
            self.assertEqual(("queued", "2026-10-21", False), (e["status"], e["since"], e["force"]))
            cm._state(rt).entries.clear()
            # before 09:45 ET no pass; at 11:00 one pass a session (the entry is forced: the split events of the session)
            rt.clock.set("2026-10-29T09:30:00-04:00")
            self.assertEqual(0, cm._events_pass(rt, cm._state(rt), rt.clock.epoch()))
            rt.clock.set(THU_11)
            self.assertEqual(1, cm._events_pass(rt, cm._state(rt), rt.clock.epoch()))
            self.assertEqual(0, cm._events_pass(rt, cm._state(rt), rt.clock.epoch()))
            self.assertTrue(cm._state(rt).entries[ORCL_CIK]["force"])
        finally:
            cm._open_ciks = orig

    @unittest.skipUnless(HOOKS, SKIP_WHY)
    def test_open_cds_contracts_are_read_from_otc_contracts(self):
        rt = self.rt
        terms = {"kind": "CDS", "cik": ORCL_CIK, "ticker": "ORCL", "entity_name": "ORACLE CORP", "trade_date": "2026-10-21"}
        with rt.db.writer() as tx:
            tx.conn.execute("INSERT INTO otc_contracts(contract_id, command_id, account_id, broker, kind, terms_json, notional_c, currency, start_date,"
                            " maturity_date, margin_mode, im_c, status) VALUES ('CDS-000001', 1, 'T1:TREASURY', 'GS', 'CDS', ?, 500000000, 'USD',"
                            " '2026-10-21', '2031-12-20', 'CTM', 0, 'OPEN')", (json.dumps(terms),))
        self.assertEqual({ORCL_CIK: ("2026-10-21", "ORACLE CORP")}, cm._open_ciks(rt))

    def test_bind_creates_the_tables_and_registers_the_refreshers(self):
        self.assertEqual([("cds_splits",), ("cds_vol",)], self.q("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('cds_vol','cds_splits')"
                                                                  " ORDER BY name"))
        regs = qworker.registered_refreshers()
        self.assertIn("cds_inputs", regs)
        self.assertIn("cds_eod", regs)

    def test_b3r2_an_idle_run_is_not_counted_as_a_request(self):
        """B3-R2 (integration): Data Status shows 'N requests today' per provider row and the worker records every refresher run there;
        cds_inputs runs every 5 s and an idle run (empty queue) makes no request, so it records ok without counting.  A run that drains a
        name counts one; a refresher without the opt-in (every other one) still counts each run."""
        rt = self.rt
        health = ProviderHealth(lambda: rt.clock.epoch())
        wk = types.SimpleNamespace(providers=types.SimpleNamespace(health=health), services=rt, _lock=threading.Lock(),
                                   stats_={"refresher_runs": 0, "refresher_errors": 0}, log=None)
        ref = qworker.Refresher("cds_inputs", cm.INPUTS_INTERVAL_S, cm.refresh_inputs)
        now = rt.clock.epoch()
        self.assertTrue(qworker.QuoteWorker._run_refresher(wk, ref, now))                # the queue is empty: None at once, no request
        self.assertEqual((True, 0, None), (health.get("cds_inputs")["ok"], health.get("cds_inputs")["requests_today"], ref.last_result))
        cm.request_inputs(rt, ORCL_CIK)
        self.assertTrue(qworker.QuoteWorker._run_refresher(wk, ref, now + 5))            # drains ORCL: the SEC check and the daily closes
        self.assertEqual(1, health.get("cds_inputs")["requests_today"])
        self.assertIn("ORCL: ok", ref.last_result)
        other = qworker.Refresher("debt", 60, lambda services, now: None)                  # no opt-in: every run counts, as before
        qworker.QuoteWorker._run_refresher(wk, other, now)
        qworker.QuoteWorker._run_refresher(wk, other, now + 60)
        self.assertEqual(2, health.get("debt")["requests_today"])


class VolRecordCommandTests(_ModelCase):

    ROW = {"vol_date": "2026-10-28", "sigma": "0.3145", "n_returns": 1000, "first_date": "2022-10-27", "last_close": "142.8", "events_through": "2026-10-29",
           "source": "test"}

    def test_replay_is_a_no_op_and_a_stored_row_never_changes(self):
        rt = self.rt
        p = {"ticker": "ORCL", "vol": [dict(self.ROW)], "splits": [{"split_date": "2026-10-15", "numerator": "2", "denominator": "1", "source": "t"}]}
        r1 = execute(rt, "cds.vol_record", p, "cds:vol:ORCL:test00000001", actor="worker")
        self.assertTrue(r1.ok, r1.body)
        self.assertEqual((1, 1), (r1.body["vol_rows"], r1.body["split_rows"]))
        r2 = execute(rt, "cds.vol_record", p, "cds:vol:ORCL:test00000001", actor="worker")
        self.assertTrue(r2.replayed)
        # a second fetch with another sigma for the same session writes nothing (a VM of a past date stays reproducible); a changed split ratio
        # replaces the stored one
        p2 = {"ticker": "ORCL", "vol": [dict(self.ROW, sigma="0.4000")], "splits": [{"split_date": "2026-10-15", "numerator": "3", "denominator": "1"}]}
        r3 = execute(rt, "cds.vol_record", p2, "cds:vol:ORCL:test00000002", actor="worker")
        self.assertEqual((0, 1), (r3.body["vol_rows"], r3.body["split_rows"]))
        self.assertEqual([("0.3145",)], self.q("SELECT sigma FROM cds_vol WHERE ticker='ORCL'"))
        self.assertEqual([("3", "1")], self.q("SELECT numerator, denominator FROM cds_splits WHERE ticker='ORCL'"))

    def test_refusals(self):
        rt = self.rt
        too_many = {"ticker": "ORCL", "vol": [dict(self.ROW, vol_date=(date(2024, 1, 1) + timedelta(days=i)).isoformat(), events_through="2026-10-29")
                                              for i in range(501)]}
        self.assertEqual("TOO_MANY_ROWS", execute(rt, "cds.vol_record", too_many, actor="worker").body["error"]["code"])
        for bad in ({"ticker": "orcl; drop", "vol": []}, {"ticker": "ORCL", "vol": [dict(self.ROW, sigma="-1")]},
                    {"ticker": "ORCL", "vol": [dict(self.ROW, n_returns=1)]}, {"ticker": "ORCL", "vol": [dict(self.ROW, events_through="2026-10-27")]},
                    {"ticker": "ORCL", "splits": [{"split_date": "2026-10-15", "numerator": "0", "denominator": "1"}]}):
            r = execute(rt, "cds.vol_record", bad, actor="worker")
            self.assertEqual((400, "TERMS"), (r.http, r.body["error"]["code"]), bad)

    def test_more_than_500_rows_go_in_several_commands(self):
        rt = self.rt
        rows = [dict(self.ROW, vol_date=(date(2024, 1, 1) + timedelta(days=i)).isoformat()) for i in range(600)]
        self.assertEqual(600, cm._record(rt, "ORCL", rows, []))
        self.assertEqual([(2,)], self.q("SELECT COUNT(*) FROM commands WHERE kind='cds.vol_record'"))
        self.assertEqual([(600,)], self.q("SELECT COUNT(*) FROM cds_vol WHERE ticker='ORCL'"))
        rid = self.q("SELECT request_id FROM commands WHERE kind='cds.vol_record' ORDER BY id LIMIT 1")[0][0]
        self.assertRegex(rid, r"^cds:vol:ORCL:[0-9a-f]{12}$")


class LiveChartPathTests(_ModelCase):
    """The live provider path: ``providers.yahoo._chart(symbol, "range=5y&interval=1d&events=div%2Csplits")`` through a YahooChart whose getter
    serves ``fixtures/terminal/cds/yahoo/chart_zzcds_5y.json`` (270 synthetic sessions 2025-10-02 .. 2026-10-28 alternating 50.00 / 51.00, a 2:1
    split on 2026-06-15)."""

    def test_chart_request_vol_rows_and_split(self):
        rt = self.rt
        with open(os.path.join(FIX, "chart_zzcds_5y.json"), "rb") as fh:
            raw = fh.read()
        seen = []

        def getter(url, ua, timeout, accept):
            seen.append((url, ua))
            return raw
        health = ProviderHealth(lambda: int(rt.clock.epoch()))
        rt.providers = types.SimpleNamespace(yahoo=YahooChart(rt.clock, health=health, getter=getter, sleep=lambda s: None), offline=False, health=health)
        self.ce.add(ZZ_CIK, "ZZCDS", "ZZ TEST CORP", "ZZCDS", funds=[_fund(debt_c=50000000000, shares=100000000)])
        cm.request_inputs(rt, ZZ_CIK)
        self.assertIn("ZZCDS: ok", self.drain())
        self.assertEqual(1, len(seen))
        self.assertTrue(seen[0][0].endswith("/v8/finance/chart/ZZCDS?range=5y&interval=1d&events=div%2Csplits"), seen[0][0])
        self.assertEqual(BROWSER_UA, seen[0][1])                       # the browser user agent is allowed for Yahoo only
        self.assertTrue(health.get("yahoo")["ok"])
        # 10-28: 270 closes = 269 returns (135 up, 134 down): mean L / 269, so the sample variance = L^2 x 270 / 269 and sd = L x sqrt(270/269) =
        # 0.019802627 x 1.001857012 = 0.019839401; x 15.874508 = 0.314941 -> 0.3149 (window said: 269 returns).  10-27: 268 returns (134 / 134):
        # sd = L x sqrt(268/267) = 0.019839676 -> 0.314945 -> 0.3149
        self.assertEqual([("2026-10-27", "0.3149", 268, "2025-10-02", "50"), ("2026-10-28", "0.3149", 269, "2025-10-02", "51")],
                         self.q("SELECT vol_date, sigma, n_returns, first_date, last_close FROM cds_vol WHERE ticker='ZZCDS' ORDER BY vol_date"))
        _rel(self, 0.314941, L * math.sqrt(270 / 269) * SQRT252, "the hand formula", tol=2e-6)
        self.assertEqual([("2026-06-15", "2", "1")], self.q("SELECT split_date, numerator, denominator FROM cds_splits WHERE ticker='ZZCDS'"))


class ModelRouteTests(_ModelCase):

    def test_route_202_then_200_404_400_and_refusal_words(self):
        rt = self.rt
        router = api.Router(rt)
        cm.register_routes(router)
        st, body, _h = router.handle("GET", "/api/otc/cds/model?ticker=ORCL&tenor_years=5")
        self.assertEqual((202, "fetching", 1500), (st, body["status"], body["retry_ms"]))
        self.drain()
        st, body, _h = router.handle("GET", f"/api/otc/cds/model?cik={ORCL_CIK}")
        self.assertEqual((200, True, "MODEL", "2031-12-20", 5), (st, body["ok"], body["label"], body["maturity_date"], body["tenor_years"]))
        self.assertIsInstance(body["spread_bp"], str)                          # Decimals as strings (the API codec)
        st, body, _h = router.handle("GET", "/api/otc/cds/model?ticker=ORCL&tenor_years=7")
        self.assertEqual((200, "2033-12-20"), (st, body["maturity_date"]))
        self.assertEqual(400, router.handle("GET", "/api/otc/cds/model?ticker=ORCL&tenor_years=6")[0])
        self.assertEqual(400, router.handle("GET", "/api/otc/cds/model")[0])
        st, body, _h = router.handle("GET", "/api/otc/cds/model?ticker=NOPE")
        self.assertEqual((404, "ENTITY_UNKNOWN"), (st, body["error"]["code"]))
        rt.rates = RateStore()
        st, body, _h = router.handle("GET", "/api/otc/cds/model?ticker=ORCL")
        self.assertEqual((200, True, "FLOOR_UNAVAILABLE"), (st, body["refused"], body["code"]))
        self.assertTrue(body["message"].startswith("no single-name CDS price"))          # words first; the code only for the hover


if __name__ == "__main__":
    unittest.main()
