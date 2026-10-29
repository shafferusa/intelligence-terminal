"""FX_CCS_PLAN rev 2 3.1 / D2 / D3 / K3 (release 1.0.6.1, package P-A): ``finsim.terminal.fxcurves`` on the worked example's inputs.

Every expected number is hand-worked in a comment (the plan's 3.2 table; ``z`` in percent, continuously compounded, t = ACT/365 days from
the curves' date 2026-10-27; Decimal precision 34).  The world: a FakeClock at Thursday 2026-10-29 10:00 ET (the FX session is open),
FixedMarks EURUSD 1.1158 / GBPUSD 1.3323 / EURGBP 0.83725 printed 30 s earlier, and ``fxcurves.fixture_curves`` (the Bank of England
1M..60M row, the seven ECB AAA spot rates and the Treasury par row of 10-27; SONIA 3.7328 % of 10-26, ESTR 2.440 % and SOFR 3.95 % of
10-27).  No network."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import date
from decimal import Decimal

from tests.terminal_helpers import T1, deposit, execute, make_runtime, pb
from finsim.terminal import ccycal, fxcurves, prime
from finsim.terminal.commands import ConflictError, RejectError
from finsim.terminal.quotes import rates as rates_mod
from finsim.terminal.quotes.rates import CurveStore, RateStore

D = Decimal
T = date(2026, 10, 29)
S_ = date(2026, 11, 2)
V_ = date(2028, 11, 2)
PB_GS = pb("GS")


def c2(x) -> Decimal:
    return D(x).quantize(D("0.01"))


def world(tmp: str, persist: bool = False, at: str = "2026-10-29T10:00:00-04:00"):
    rt = make_runtime(tmp, at=at)
    now = rt.clock.epoch()
    for k, v in (("EURUSD", "1.1158"), ("GBPUSD", "1.3323"), ("EURGBP", "0.83725")):
        rt.marks.set(k, v, "delayed", now - 30)
    rates_mod.register(rt.bus)
    rt.rates = RateStore(rt.db, rt.bus)
    rt.curve = CurveStore(rt.db, rt.bus, rt.clock)
    fxcurves.fixture_curves(rt, persist=persist)
    return rt


class _Base(unittest.TestCase):
    persist = False

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-fxc-")
        self.rt = world(self.tmp, self.persist)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def snap(self):
        return self.rt.marks.snapshot()


class CurveTests(_Base):
    def test_first_nodes(self):
        # z = 365 x ln(1 + r / B) x 100 at t = 1/365:
        #   SONIA 3.7328 %, B 365: ln(1 + 0.000102268493) = 0.000102263264 -> x 365 = 0.0373260913 -> 3.732609 %
        #   ESTR 2.440 %, B 360:  ln(1 + 0.0000677778) = 0.0000677755 -> x 365 = 0.0247380 -> 2.473805 %
        #   SOFR 3.95 %, B 360:   ln(1 + 0.000109722) = 0.000109716 -> x 365 = 0.0400464 -> 4.004641 %
        got = {ccy: fxcurves.curve(self.rt, ccy, T).first_node for ccy in ("GBP", "EUR", "USD")}
        self.assertEqual({"GBP": "3.732609", "EUR": "2.473805", "USD": "4.004641"}, {k: v["z_pct6"] for k, v in got.items()})
        self.assertEqual(("IUDSOIA", "2026-10-26", "3.7328", 365, 3), (got["GBP"]["series"], got["GBP"]["obs_date"], got["GBP"]["value_pct"], got["GBP"]["basis"],
                                                                      got["GBP"]["age_days"]))
        self.assertEqual(str(D(1) / D(365)), got["EUR"]["t"])

    def test_usd_bootstrap(self):
        # the IRS desk's semi-annual bootstrap of the 10-27 par row (6M 4.34, 1Y 4.50, 2Y 4.70, 3Y 4.80; 1.5Y and 2.5Y interpolated 4.60 / 4.75):
        #   DF(0.5) = 1 / (1 + 0.0434 / 2) = 1 / 1.0217 = 0.9787608887
        #   DF(1.0) = (1 - 0.0225 x 0.9787608887) / 1.0225 = 0.9779778800 / 1.0225 = 0.9564575844 ... (1e-10 each)
        #   z_i = -ln(DF_i) / t_i: z(0.5) = 0.0214679 / 0.5 = 4.293581 %
        usd = fxcurves.curve(self.rt, "USD", T)
        self.assertEqual(["0.9787608887", "0.9564575844", "0.9340077958", "0.9111609015", "0.8890996871", "0.8671214018"],
                         [usd.grid["dfs"][k] for k in ("0.5000", "1.0000", "1.5000", "2.0000", "2.5000", "3.0000")])
        self.assertEqual(["4.293581", "4.451884", "4.551366", "4.651789", "4.701837", "4.752543"], [fxcurves._pct6(z) for _t, _tt, z in usd.nodes[1:7]])
        self.assertEqual(("PROXY", "2026-10-27"), (usd.kind, usd.d0.isoformat()))
        self.assertEqual("PROXY: Treasury par curve of 2026-10-27, the IRS desk's stand-in for the SOFR curve", usd.labels[0])

    def test_interpolated_zero_rates(self):
        # t_S = (11-02 - 10-27) / 365 = 6 / 365 = 0.016438; t_V = 737 / 365 = 2.019178
        # GBP z(t_S) = 3.732609 + (3.899390 - 3.732609) x (0.016438 - 0.002740) / (0.083333 - 0.002740) = 3.732609 + 0.028348 = 3.760957
        # GBP z(t_V) = 4.727447 (24M) + (4.734519 - 4.727447) x (2.019178 - 2) / (2.083333 - 2) = 4.727447 + 0.001628 = 4.729075
        # EUR z(t_S) = 2.473805 + (2.542643 - 2.473805) x 0.013699 / 0.247260 = 2.473805 + 0.003814 = 2.477619
        # EUR z(t_V) = 3.015032 (2Y) + (3.051054 - 3.015032) x 0.019178 = 3.015032 + 0.000691 = 3.015723
        g, e = fxcurves.curve(self.rt, "GBP", T), fxcurves.curve(self.rt, "EUR", T)
        self.assertEqual(("3.760957", "4.729075"), (fxcurves._pct6(g.z_at(g.t_of(S_))), fxcurves._pct6(g.z_at(g.t_of(V_)))))
        self.assertEqual(("2.477619", "3.015723"), (fxcurves._pct6(e.z_at(e.t_of(S_))), fxcurves._pct6(e.z_at(e.t_of(V_)))))
        self.assertEqual(("observed", "PROXY"), (g.kind, e.kind))
        self.assertEqual("observed: Bank of England UK OIS curve of 2026-10-27 (continuously compounded)", g.labels[0])
        self.assertIn("PROXY: ECB euro-area AAA government curve of 2026-10-27 + 0.0 bp", e.labels[0])
        self.assertIn("no free EUR OIS curve", e.labels[0])

    def test_discount_factors_and_the_forward(self):
        # DF*_EUR(S, V) = exp(-0.03015723 x 2.019178 + 0.02477619 x 0.016438) = exp(-0.0604855) = 0.9413073853 (1e-10)
        # DF*_GBP(S, V) = exp(-0.04729075 x 2.019178 + 0.03760957 x 0.016438) = exp(-0.0948701) = 0.9094909816
        # S = 1.1158 / 1.3323 = 0.83749906 -> 0.837499; F = 0.837499 x 0.9413073853 / 0.9094909816 = 0.866797 (6 dp)
        # points = (0.866797 - 0.837499) / 0.0001 = 292.98
        f = fxcurves.forward(self.rt, "EURGBP", V_, self.snap())
        self.assertEqual((D("0.837499"), D("0.9413073853"), D("0.9094909816"), D("0.866797")), (f["S"], f["df_base"], f["df_quote"], f["F"]))
        self.assertEqual(("292.98", "0.866797", "0.0001"), (f["forward"]["points"], f["forward"]["outright"], f["forward"]["pip"]))
        self.assertEqual(("2026-10-29", "2026-11-02", "2028-11-02", 731), (f["trade_date"], f["spot_date"], f["value_date"], f["days"]))
        self.assertEqual("F = 0.837499 x 0.9413073853 / 0.9094909816 = 0.866797", f["forward"]["arithmetic"])
        self.assertEqual("fxcurves-2", f["model_version"])
        self.assertIn("GBP observed (Bank of England OIS), EUR PROXY (ECB AAA + spread)", f["method"])
        # the record keeps the nodes up to the first one beyond V: GBP ON + 1M..25M (25M = 2.0833 > 2.0192), EUR ON 3M 6M 9M 1Y 2Y 3Y
        self.assertEqual((26, "25M", 7, "3Y"), (len(f["curves"]["GBP"]["nodes"]), f["curves"]["GBP"]["nodes"][-1]["tenor"], len(f["curves"]["EUR"]["nodes"]),
                                                f["curves"]["EUR"]["nodes"][-1]["tenor"]))
        self.assertEqual(("3.760957", "4.729075", "2.477619", "3.015723"), (f["curves"]["GBP"]["z_S"], f["curves"]["GBP"]["z_V"], f["curves"]["EUR"]["z_S"],
                                                                           f["curves"]["EUR"]["z_V"]))
        self.assertEqual({"pair": "EURGBP", "eur_usd_level_bp": "0.0", "gbp_usd_level_bp": "0.0", "cross_bp": "0.0", "on": "both discount curves"},
                         {k: f["basis"][k] for k in ("pair", "eur_usd_level_bp", "gbp_usd_level_bp", "cross_bp", "on")})
        self.assertEqual("MODEL", f["labels"][0])
        self.assertTrue(any(x.startswith("observed: Bank of England") for x in f["labels"]))
        self.assertTrue(any(x.startswith("PROXY: ECB") for x in f["labels"]))
        self.assertTrue(any(x.startswith("ASSUMED") for x in f["labels"]))
        self.assertEqual([("EURUSD", "1.1158"), ("GBPUSD", "1.3323")], [(u["pair"], str(D(u["mid"]).normalize())) for u in f["usd_rates"]])

    def test_eurusd_two_year_on_the_ccs_curves(self):
        # B-T11 (OTC-04): the PB EURUSD 2Y outright from 2026-11-02 on the CCS's curves: 1.1158 x DF*_EUR / DF_USD = 1.153030
        f = fxcurves.forward(self.rt, "EURUSD", V_, self.snap())
        self.assertEqual((D("1.1158"), D("1.153030")), (f["S"].normalize(), f["F"]))
        self.assertEqual("the EUR discount curve", f["basis"]["on"])

    def test_cross_checks(self):
        # flat overnight (revision 1's method): DF_EUR = (1 + 0.0244/360)^-731, DF_GBP = (1 + 0.037328/365)^-731 -> F 0.858881, -79.16 pips
        # on the print: 0.83725 x 0.9413073853 / 0.9094909816 = 0.866539, -2.58 pips; the FX desk quotes no forward beyond 1Y
        snap = self.snap()
        f = fxcurves.forward(self.rt, "EURGBP", V_, snap)
        cc = fxcurves.cross_checks(self.rt, "EURGBP", V_, snap, f)
        self.assertEqual(("0.858881", "-79.16"), (cc["flat_overnight"]["outright"], cc["flat_overnight"]["diff_pips"]))
        self.assertEqual(("0.866539", "-2.58"), (cc["on_print"]["outright"], cc["on_print"]["diff_pips"]))
        self.assertIsNone(cc["fx_desk"]["outright"])
        self.assertIn("none beyond 1Y", cc["fx_desk"]["note"])

    def test_eur_ois_spread_variants_of_q1(self):
        # The EUR OIS spread sits on the ECB GRID nodes only, never on the ESTR node (D2 / D5; the same rule as the 423.92 sensitivity):
        # at +10 bp z(t_V) rises 10 bp but z(t_S) only 10 x 0.0554 bp (t_S lies 5.54 % of the way from the ESTR node to 3M), so
        # DF*_EUR falls by exp(-0.0010 x 2.019178 + 0.0000554 x 0.016438) and F = 0.866797... x 0.997984 -> 0.865049; at -10 bp 0.868548.
        # Plan Q1's 0.865063 / 0.868535 are the PARALLEL shift of the whole EUR curve, ESTR node included (exp(-0.0010 x (t_V - t_S)))
        # - reproduced below with ``shifted`` - 1.4 pips from the rule the curve applies.
        e = fxcurves.discount_curve(self.rt, "EUR", T)
        g = fxcurves.discount_curve(self.rt, "GBP", T)
        out, par = {}, {}
        for bp in (10, -10):
            out[bp] = str(fxcurves.forward(self.rt, "EURGBP", V_, self.snap(), curves={"EUR": fxcurves.with_spread(e, bp), "GBP": g})["F"])
            par[bp] = str(fxcurves.forward(self.rt, "EURGBP", V_, self.snap(), curves={"EUR": fxcurves.shifted(e, bp), "GBP": g})["F"])
        self.assertEqual({10: "0.865049", -10: "0.868548"}, out)
        self.assertEqual({10: "0.865063", -10: "0.868535"}, par)

    def test_trade_b_sensitivities_by_re_pricing(self):
        # B's value to the Fund = -A x (F_t - K) x DF*_GBP x GBPUSD, A = 2,000,000, K = 0.866797 - 0.000173 = 0.866624 (hw 2.0 bp of F);
        # re-priced without intermediate rounding: rate01 EUR +420.66, GBP -420.57; EUR OIS spread +423.92 (grid nodes only, not the ESTR
        # node); points01 (F + 1 pip) -242.34; PV deltas -A x DF*_EUR = -1,882,614.77 EUR and +A x K x DF*_GBP = +1,576,373.42 GBP
        A, K = D("2000000"), D("0.866624")
        e, g = fxcurves.discount_curve(self.rt, "EUR", T), fxcurves.discount_curve(self.rt, "GBP", T)

        def value(ec=e, gc=g, bump=D(0)):
            ft = (D("1.1158") / D("1.3323")) * fxcurves.df(ec, S_, V_) / fxcurves.df(gc, S_, V_) + bump
            return -A * (ft - K) * fxcurves.df(gc, S_, V_) * D("1.3323")
        v0 = value()
        self.assertEqual(c2("-419.25"), c2(v0))
        self.assertEqual((c2("420.66"), c2("-420.57"), c2("423.92"), c2("-242.34")),
                         (c2(value(ec=fxcurves.shifted(e, 1)) - v0), c2(value(gc=fxcurves.shifted(g, 1)) - v0),
                          c2(value(ec=fxcurves.with_spread(e, 1)) - v0), c2(value(bump=D("0.0001")) - v0)))
        # basis01 = the level moving the discount curve: the same numbers as rate01 here (D5)
        self.assertEqual(c2("420.66"), c2(value(ec=fxcurves.discount(e, 1)) - v0))
        self.assertEqual((c2("-1882614.77"), c2("1576373.42")), (c2(-A * fxcurves.df(e, S_, V_)), c2(A * K * fxcurves.df(g, S_, V_))))

    def test_basis_level_sits_on_discounting_only(self):
        # a level of +1 bp lowers DF by exp(-0.0001 x (t_V - t_S)): 0.9413073853 x exp(-0.0001 x 2.002740) = 0.9411188...
        e = fxcurves.curve(self.rt, "EUR", T)
        d1 = fxcurves.discount(e, D("1"), "EURUSD", "test", None)
        self.assertEqual(D("0"), e.level_bp)
        self.assertEqual(D("1"), d1.level_bp)
        ratio = fxcurves.df(d1, S_, V_) / fxcurves.df(e, S_, V_)
        self.assertAlmostEqual(float((-D("0.0001") * (D(737 - 6) / 365)).exp()), float(ratio), places=12)
        self.assertTrue(any("cross-currency basis level 1 bp" in x for x in d1.labels))
        with self.assertRaises(RejectError):
            fxcurves.with_spread(fxcurves.curve(self.rt, "GBP", T), 1)


class SpotTests(_Base):
    def test_derived_cross_with_the_print_inside_the_tolerance(self):
        # 1.1158 / 1.3323 = 0.837499061773... -> 0.837499 (6 dp, never 8); print 0.83725: (0.83725 - 0.8374990618) / 0.0001 = -2.49 pips
        sp = fxcurves.spot(self.rt, "EURGBP", self.snap())
        self.assertEqual((D("0.837499"), "derived from the USD pairs", D("-2.49"), True, None, 0), (sp["mid"], sp["basis"], sp["diff_pips"], sp["executable"],
                                                                                                    sp["code"], sp["legs_gap_s"]))
        self.assertEqual((D("0.83725"), True), (sp["print"]["mid"], sp["print"]["compared"]))
        self.assertEqual("5.0", sp["tolerance_pips"])

    def test_print_beyond_five_pips_is_spot_inconsistent(self):
        # 0.83690 - 0.8374990618 = -0.0005990618 -> -5.99 pips, beyond 5.0
        now = self.rt.clock.epoch()
        self.rt.marks.set("EURGBP", "0.83690", "delayed", now - 30)
        sp = fxcurves.spot(self.rt, "EURGBP", self.snap())
        self.assertEqual((False, "SPOT_INCONSISTENT", D("-5.99")), (sp["executable"], sp["code"], sp["diff_pips"]))
        with self.assertRaises(ConflictError) as cm:
            fxcurves.require_spot(self.rt, "EURGBP", self.snap())
        self.assertEqual("SPOT_INCONSISTENT", cm.exception.code)
        self.assertIn("limit 5.0", cm.exception.message)

    def test_legs_more_than_120_s_apart_are_not_executable(self):
        now = self.rt.clock.epoch()
        self.rt.marks.set("GBPUSD", "1.3323", "delayed", now - 30 - 121)       # 121 s before the EURUSD print
        sp = fxcurves.spot(self.rt, "EURGBP", self.snap())
        self.assertEqual((121, False, "QUOTE_STALE"), (sp["legs_gap_s"], sp["executable"], sp["code"]))
        self.rt.marks.set("GBPUSD", "1.3323", "delayed", now - 30 - 120)       # exactly 120 s: executable
        self.assertTrue(fxcurves.spot(self.rt, "EURGBP", self.snap())["executable"])

    def test_a_stale_print_is_stored_not_compared(self):
        now = self.rt.clock.epoch()
        self.rt.marks.set("EURGBP", "0.83000", "stale", now - 7200)
        sp = fxcurves.spot(self.rt, "EURGBP", self.snap())
        self.assertEqual((True, False), (sp["executable"], sp["print"]["compared"]))
        self.assertIn("stored, not compared", sp["print"]["note"])

    def test_usd_pair_is_its_print(self):
        sp = fxcurves.spot(self.rt, "EURUSD", self.snap())
        self.assertEqual((D("1.1158"), "pair print", True), (sp["mid"].normalize(), sp["basis"], sp["executable"]))


class RefusalTests(_Base):
    def test_overnight_print_age_10_days_ok_11_refused(self):
        rt = self.rt
        rt.rates = RateStore()
        fxcurves.fixture_curves(rt, persist=False)
        rt.rates._obs["ESTR"] = [(date(2026, 10, 19), D("2.44"))]          # 10 days before 10-29: accepted
        self.assertEqual(10, fxcurves.curve(rt, "EUR", T).first_node["age_days"])
        rt.rates._obs["ESTR"] = [(date(2026, 10, 18), D("2.44"))]          # 11 days: refused
        with self.assertRaises(ConflictError) as cm:
            fxcurves.curve(rt, "EUR", T)
        self.assertEqual(("RATE_UNAVAILABLE", 11), (cm.exception.code, cm.exception.details["age_days"]))
        rt.rates._obs["ESTR"] = []
        with self.assertRaises(ConflictError) as cm:
            fxcurves.curve(rt, "EUR", T)
        self.assertEqual("RATE_UNAVAILABLE", cm.exception.code)

    def test_grid_3_business_days_old_ok_4_refused(self):
        # valuation Friday 2026-10-30: a grid of Tuesday 10-27 is 3 business days old (27, 28, 29) -> used; of Monday 10-26: 4 -> refused
        rt = self.rt
        on = date(2026, 10, 30)
        for ccy in ("EUR", "GBP", "USD"):
            self.assertEqual("2026-10-27", fxcurves.curve(rt, ccy, on).grid["as_of"], ccy)
        rt.rates = RateStore()
        rt.curve = CurveStore()
        fxcurves.fixture_curves(rt, d0=date(2026, 10, 26), persist=False)
        for ccy in ("EUR", "GBP", "USD"):
            with self.assertRaises(ConflictError) as cm:
                fxcurves.curve(rt, ccy, on)
            self.assertEqual("CURVE_UNAVAILABLE", cm.exception.code, ccy)
        rt.rates.fetch_status["BOE_OIS_SPOT"] = {"ok": False, "text": "Bank of England yield-curve file: HTTP 403 at 2026-10-30 09:12 ET"}
        with self.assertRaises(ConflictError) as cm:
            fxcurves.curve(rt, "GBP", on)
        self.assertIn("4 London business days old", cm.exception.message)
        self.assertIn("HTTP 403 at 2026-10-30 09:12 ET", cm.exception.message)        # the source tried, named

    def test_no_curve_and_a_missing_node_block(self):
        rt = self.rt
        rt.rates = RateStore()
        fxcurves.fixture_curves(rt, persist=False)
        rt.rates.put_grid("ECB_AAA_SPOT", date(2026, 10, 28), {"3M": "2.5", "6M": "2.6", "1Y": "2.8", "2Y": "3.0"}, "test", persist=False)
        with self.assertRaises(ConflictError) as cm:
            fxcurves.curve(rt, "EUR", T)
        self.assertEqual(("CURVE_UNAVAILABLE", "9M"), (cm.exception.code, cm.exception.details["missing"]))      # a hole: never interpolated over
        rt.rates = RateStore()
        with self.assertRaises(ConflictError) as cm:
            fxcurves.curve(rt, "GBP", T)
        self.assertEqual("RATE_UNAVAILABLE", cm.exception.code)                     # no SONIA either: the first missing input is named
        with self.assertRaises(ConflictError) as cm:
            fxcurves.curve(rt, "JPY", T)
        self.assertEqual("CURVE_UNAVAILABLE", cm.exception.code)

    def test_curve_short_at_61_months_for_gbp(self):
        # the BoE grid ends at 60M (t = 5.0): a date 61 months out is never extrapolated
        # 60M = t 5.0 = 1825 days after 2026-10-27 = 2031-10-26, the last date the curve reaches; 2031-10-27 (t 1826 / 365) is beyond
        g = fxcurves.curve(self.rt, "GBP", T)
        self.assertGreater(fxcurves.df(g, S_, date(2031, 10, 26)), D("0.78"))
        for beyond in (date(2031, 10, 27), date(2031, 11, 27)):                  # one day past 60M, and 61 months
            with self.assertRaises(ConflictError) as cm:
                fxcurves.df(g, S_, beyond)
            self.assertEqual("CURVE_SHORT", cm.exception.code)
        with self.assertRaises(ConflictError) as cm:
            fxcurves.df(g, S_, date(2031, 11, 27))
        self.assertEqual("CURVE_SHORT", cm.exception.code)
        with self.assertRaises(ConflictError) as cm:                              # a forward 61 months out (11-27 is Thanksgiving 2031: 11-28)
            fxcurves.forward(self.rt, "EURGBP", date(2031, 11, 28), self.snap())
        self.assertEqual("CURVE_SHORT", cm.exception.code)

    def test_value_date_and_legacy_pair(self):
        with self.assertRaises(RejectError) as cm:
            fxcurves.forward(self.rt, "EURGBP", date(2026, 11, 26), self.snap())
        self.assertEqual("VALUE_DATE", cm.exception.code)
        self.assertIn("not a business day in USD (New York)", cm.exception.message)
        with self.assertRaises(RejectError) as cm:
            fxcurves.forward(self.rt, "EURGBP", date(2026, 11, 2), self.snap())
        self.assertIn("not after the spot date", cm.exception.message)
        with self.assertRaises(ConflictError) as cm:
            fxcurves.forward(self.rt, "USDJPY", date(2027, 11, 2), self.snap())
        self.assertEqual("CURVE_UNAVAILABLE", cm.exception.code)
        self.assertIn("LEGACY METHOD - LIMITED", cm.exception.message)


class IndexTests(_Base):
    def test_index_ratio_and_compounded_rate(self):
        # I(10-28) / I(10-27) = 110.09953057 / 110.09206878 = 1.0000677777...; (factor - 1) x 360 / 1 = 0.0244000 (7 dp): the 10-27 ESTR
        rs = self.rt.rates
        rs.put("ESTR_INDEX", [(date(2026, 10, 27), D("110.09206878")), (date(2026, 10, 28), D("110.09953057"))], "test", persist=False)
        f = fxcurves.index_factor(self.rt, "EUR", date(2026, 10, 27), date(2026, 10, 28))
        self.assertEqual(D("110.09953057") / D("110.09206878"), f)
        self.assertEqual(D("0.0244000"), fxcurves.compounded_rate(f, 1))
        det = fxcurves.index_detail(self.rt, "EUR", date(2026, 10, 27), date(2026, 10, 28))
        self.assertEqual(("110.09206878", "110.09953057"), (det["start"]["value"], det["end"]["value"]))
        # SOFR: the index only; no fallback from daily prints
        rs.put("SOFR_INDEX", [(date(2026, 10, 27), D("1.26453751")), (date(2026, 10, 28), D("1.26467626"))], "test", persist=False)
        self.assertEqual(D("1.26467626") / D("1.26453751"), fxcurves.index_factor(self.rt, "USD", date(2026, 10, 27), date(2026, 10, 28)))
        with self.assertRaises(ConflictError) as cm:
            fxcurves.index_factor(self.rt, "USD", date(2026, 10, 27), date(2026, 10, 29))
        self.assertEqual("INDEX_PENDING", cm.exception.code)

    def test_eur_fallback_equals_the_index_ratio_on_a_quarter_with_weekends(self):
        # a synthetic quarter 2026-11-02 .. 2027-02-02 (92 days; weekends, Christmas and New Year closing TARGET2): ESTR flat 2.440 % on every
        # TARGET2 day, the index built the ECB's way (I_next = I x (1 + r x n / 360)) and published to 8 decimals; the fallback (the prints
        # with n_i / 360 weights) must equal the published ratio within 1e-9
        rs = self.rt.rates
        s, e = date(2026, 11, 2), date(2027, 2, 2)
        d, level, prints = s, D("110.0"), []
        while d < e:
            nxt = ccycal.TARGET.next_business_day(d)
            n = (nxt - d).days
            prints.append((d, D("2.440")))
            level = level * (1 + D("0.02440") * n / 360)
            d = nxt
        self.assertEqual(e, d)
        rs.put("ESTR", prints, "test prints", persist=False)
        rs.put("ESTR_INDEX", [(s, D("110.0")), (e, level.quantize(D("0.00000001")))], "test index", persist=False)
        ratio = fxcurves.index_factor(self.rt, "EUR", s, e)
        rs._obs["ESTR_INDEX"] = []                                                  # the index missing: the fallback from the prints
        det = fxcurves.index_detail(self.rt, "EUR", s, e)
        self.assertIn("TARGET2 days", det["method"])
        self.assertLess(abs(det["factor"] - ratio), D("1e-9"))
        self.assertEqual(len(prints), len(det["nights"]))
        # a coupon on it: the factor 1.0062546165... -> (factor - 1) x 360 / 92 = 0.02447464... -> 0.0244746 (7 dp): 2.440 % compounded daily
        # over the quarter is worth 0.0075 % more than simple interest
        self.assertEqual(D("0.0244746"), fxcurves.compounded_rate(det["factor"], 92))
        rs._obs["ESTR"] = prints[:10] + prints[11:]                                 # one print missing: the payment waits
        with self.assertRaises(ConflictError) as cm:
            fxcurves.index_factor(self.rt, "EUR", s, e)
        self.assertEqual("INDEX_PENDING", cm.exception.code)
        self.assertEqual([prints[10][0].isoformat()], cm.exception.details["missing_prints"])

    def test_compounded_rate_rounds_half_even_to_7_decimals(self):
        # (1.02440005 - 1) x 360 / 360 = 0.02440005 -> 0.0244000 (half to even); 0.02440015 -> 0.0244002
        self.assertEqual(D("0.0244000"), fxcurves.compounded_rate(D("1.02440005"), 360))
        self.assertEqual(D("0.0244002"), fxcurves.compounded_rate(D("1.02440015"), 360))
        # (0.00623456789) x 360 / 92 = 0.0243961352... -> 0.0243961
        self.assertEqual(D("0.0243961"), fxcurves.compounded_rate(D("1.00623456789"), 92))
        with self.assertRaises(RejectError):
            fxcurves.compounded_rate(D("1.01"), 0)


class FixtureRowsTests(unittest.TestCase):
    def test_fixture_constants_are_the_published_rows_to_6_decimals(self):
        # fixture_curves' constants = the Bank of England and ECB rows of 2026-10-27 as published (tests/fixtures/terminal/rfr/rows_20261027.json,
        # full precision) rounded HALF_EVEN to 6 decimals of a percent - the value a curve node uses (rates.CURVE_NODE_Q)
        import json
        import os
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "terminal", "rfr", "rows_20261027.json"), encoding="utf-8") as fh:
            rows = json.load(fh)
        q = D("0.000001")
        self.assertEqual(list(fxcurves.FIXTURE_BOE_20261027), [str(D(rows["boe_ois_spot_pct"][f"{m}M"]).quantize(q)) for m in range(1, 61)])
        self.assertEqual(fxcurves.FIXTURE_ECB_20261027, {k: str(D(rows["ecb_aaa_spot_pct"][k]).quantize(q)) for k in fxcurves.FIXTURE_ECB_20261027})
        self.assertEqual(fxcurves.FIXTURE_UST_20261027, rows["ust_par_pct"])
        self.assertEqual((D("3.7328"), D("2.440"), D("3.95")), (D(rows["prints"]["IUDSOIA"][1]), D(rows["prints"]["ESTR"][1]), D(rows["prints"]["SOFR"][1])))


class PersistAndScheduleTests(_Base):
    persist = True

    def test_fixture_rows_are_persisted_and_reload(self):
        with self.rt.db.reader() as conn:
            n = {r["series"]: r["n"] for r in conn.execute("SELECT series, COUNT(*) AS n FROM curve_points GROUP BY series").fetchall()}
        self.assertEqual((60, 7, 13, 1, 1, 1), (n["BOE_OIS_SPOT"], n["ECB_AAA_SPOT"], n["UST_PAR"], n["IUDSOIA"], n["ESTR"], n["SOFR"]))
        rs = RateStore(self.rt.db)
        rs.load_persisted()
        g = rs.grid("BOE_OIS_SPOT", date(2026, 10, 27))
        self.assertEqual((D("3.899390"), D("4.727447")), (g.points["1M"], g.points["24M"]))
        self.assertIn("test fixture", rs.observation("IUDSOIA", T)["source"])

    def test_pb_schedule_terms_reach_the_curves(self):
        # with Goldman Sachs' schedule: EUR OIS spread +10 bp and a EUR/USD level of -12.5 bp (both ASSUMED, edited) reach the EUR curve
        rt = self.rt
        prime.register(rt.bus)
        prime.bind(rt)
        self.assertTrue(deposit(rt, 1500000000).ok)
        r = execute(rt, "account.open_pb", {"lifetime_id": T1, "broker": "GS"})
        self.assertTrue(r.ok, r.body)
        r = execute(rt, "account.update_schedule", {"account_id": PB_GS, "eur_ois_spread_bp": "10", "xccy_basis_eur_bp": "-12.5"})
        self.assertTrue(r.ok, r.body)
        e = fxcurves.curve(rt, "EUR", T, PB_GS)
        self.assertEqual((D("10"), D("2.642643")), (e.spread_bp, e.nodes[1][2]))       # 2.542643 + 10 x 0.01 on the 3M node; the ESTR node unchanged
        self.assertEqual(fxcurves.curve(rt, "EUR", T).nodes[0], e.nodes[0])
        self.assertIn("+ 10 bp (Goldman Sachs schedule, ASSUMED)", e.labels[0])
        f = fxcurves.forward(rt, "EURGBP", V_, rt.marks.snapshot(), PB_GS)
        self.assertEqual(("-12.5", "0.0", "-12.5"), (f["basis"]["eur_usd_level_bp"], f["basis"]["gbp_usd_level_bp"], f["basis"]["cross_bp"]))
        self.assertIn("Goldman Sachs schedule", f["basis"]["source"])
        # the spread alone gives Q1's 0.865063; the -12.5 bp EUR level raises DF*_EUR by about 12.5 bp x 2 years -> a higher F
        self.assertGreater(f["F"], D("0.865063"))


if __name__ == "__main__":
    unittest.main()
