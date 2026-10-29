"""FinSim1 1.0.6 fixer, round 1 (P8 phase-2 defects P8B-01 .. P8B-15 on the PB OTC forwards, the FX desk rates and their views).

The runtime is the as-of tests' (``test_terminal_otc_asof.runtime``): F1 with its Goldman Sachs PB account, Wed 2026-10-28 10:00 ET, SOFR
printed through 10-26 (3.93 %), the curves of 10-21 .. 10-26; plus the OTC options / forwards module bound, the ECB deposit rate observed
10-27 (2.50 %), the OECD monthly proxies the owner's database holds (JPY 1.043 % for 2026-09, CHF 1.34 % for 2024-03 - the series stopped)
and fresh delayed prints (EURUSD 1.1158, USDJPY 153.32, USDCHF 0.8277, AAPL 343.83).  No network.  Every expected number is worked by hand
in the comment above it.

- P8B-01: a rate older than its series allows is never used: CHF forwards and CHF cross-currency legs refuse with the date and the age;
  every rate used carries its age.
- P8B-02: an OTC equity forward is marked at the SOFR-carry mid; the PB financing spread is charged on the side dealt (SOFR + spread when
  the client buys, SOFR - spread when he sells), so a sale is no longer dealt above the funding-neutral forward.
- P8B-03: the FX forward tenors the desk offers (ON, 1W, 2W .. 1Y) are the ones the pricer prices.
- P8B-04: the pairs without a usable rate are listed spot only, with the reason.
- P8B-07: a name with no dividend data is not priced (q is never assumed 0).
- P8B-08: an OTC FX forward is outside the headline exposure and its two legs are in the currency exposure and the FX scenarios.
- P8B-09: one mark snapshot computes each session window once (the Dashboard's 'marks' part).
- P8B-14: a missing daily bar is said as a data gap, never as a delisting.
- P8B-15: GET /api/jobs gives the product jobs' next run from their open contracts.

1.0.6.1 (FX_CCS_PLAN rev 2 D3, package P-B): a PB OTC FX forward of a USD / EUR / GBP pair is priced on the FX term curves (1W .. 2Y), so
the tests that price or book a EURUSD forward first install the worked example's curves (``fxcurves.fixture_curves``: the 2026-10-27 rows,
SOFR 3.95 % and ESTR 2.440 % of 10-27) and their figures are re-pinned on them; a pair with another currency keeps the legacy method (and
the FX desk's own forwards are unchanged), so the other tests keep their numbers.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import types
import unittest
from datetime import date
from decimal import Decimal

from tests import test_terminal_otc_asof as A
from tests.terminal_helpers import assert_integrity, execute
from finsim.terminal import fx, fxcurves, lifecycle_eod, risk, tables
from finsim.terminal.clock import epoch_of
from finsim.terminal.commands import ConflictError, RejectError
from finsim.terminal.products import otc_options, swaps, swaps_trs

D = Decimal
PB = A.PB
FWD = {"pair": "EURUSD", "side": "BUY", "notional": "1000000.00", "tenor": "3M"}


def runtime(tmp):
    rt = A.runtime(tmp)
    rt.otc_options = otc_options
    otc_options.register(rt.bus, rt)
    otc_options.bind(rt)
    fx.register(rt.bus)
    rt.fx = fx
    fx.bind(rt)
    from finsim.terminal import lifecycle
    rt.lifecycle = lifecycle.LifecycleScheduler(rt.bus, rt.db, rt, rt.clock, rt.log)     # rebuilt after the binds: OTC_EXPIRY / FX_FORWARD_DUE are its jobs
    rt.rates.put("ECBDFR", [(date(2026, 10, 27), D("2.50"))], persist=False)
    rt.rates.put("IRSTCI01JPM156N", [(date(2026, 9, 1), D("1.043"))], persist=False)
    rt.rates.put("IRSTCI01CHM156N", [(date(2024, 3, 1), D("1.34"))], persist=False)
    now = rt.clock.epoch()
    for iid, px in (("EURUSD", "1.1158"), ("USDJPY", "153.32"), ("USDCHF", "0.8277"), ("AAPL", "343.83"), ("TSLA", "441.00")):
        rt.marks.set(iid, px, "delayed", now)
    return rt


def curves(rt):
    """1.0.6.1: the FX term curves of the worked example (the 2026-10-27 rows; one business day old on 10-28) for the EURUSD PB forwards."""
    fxcurves.fixture_curves(rt, persist=False)


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-otcfwd-")
        self.rt = runtime(self.tmp)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


class RateAgeTests(_Base):
    """P8B-01 / OTC-01."""

    def test_rate_age_rule(self):
        # monthly OECD averages are aged from the end of the month they average; daily prints from their own date
        a = tables.rate_age("IRSTCI01CHM156N", date(2024, 3, 1), date(2026, 10, 28))       # 2024-03-31 .. 2026-10-28 = 941 days > 62
        self.assertEqual((True, True, 941, 62, "2024-03-31"), (a["monthly"], a["stale"], a["age_days"], a["max_age_days"], a["period_end"]))
        a = tables.rate_age("IRSTCI01JPM156N", date(2026, 9, 1), date(2026, 10, 28))       # 2026-09-30 .. 10-28 = 28 days <= 62
        self.assertEqual((False, 28), (a["stale"], a["age_days"]))
        a = tables.rate_age("SOFR", date(2026, 10, 18), date(2026, 10, 28))                 # 10 days: still allowed
        self.assertEqual((False, 10), (a["stale"], a["age_days"]))
        self.assertTrue(tables.rate_age("SOFR", date(2026, 10, 17), date(2026, 10, 28))["stale"])                       # 11 days: refused
        self.assertFalse(tables.rate_age("SOFR", date(2026, 10, 17), None)["stale"])                                    # no clock: age unknown

    def test_rate_for_refuses_a_series_that_stopped_and_says_each_age(self):
        rt = self.rt
        v, info = fx.rate_for(rt, "CHF", 365)
        self.assertIsNone(v)
        self.assertEqual((True, "2024-03-01", "1.34", 941), (info["stale"], info["as_of"], info["value_pct"], info["age"]["age_days"]))
        self.assertIn("IRSTCI01CHM156N has stopped updating - its last value is the March 2024 average (1.34 %), 941 days after that month ended", info["note"])
        v, info = fx.rate_for(rt, "JPY", 365)
        self.assertEqual((D("1.043"), "IRSTCI01JPM156N 1.043 % (monthly proxy, LIMITED, as of 2026-09-01, the September 2026 average, 28 days after that month"
                                      " ended (at most 62))"), (v, info["label"]))
        v, info = fx.rate_for(rt, "USD", 90)                                    # SOFR 3.93 % of 10-26, 2 days old on 10-28
        self.assertEqual((D("3.93"), "SOFR 3.93 % (daily, as of 2026-10-26, 2 days old (at most 10))"), (v, info["label"]))
        # SOFR stopped (no print after 10-26): on 11-09 it is 14 days old -> the Treasury par curve's bill yield, when that curve is itself current
        rt.clock.set("2026-11-09T10:00:00-05:00")
        v, info = fx.rate_for(rt, "USD", 90)
        self.assertIsNone(v)                                                    # the newest curve (10-26) is 14 days old too: no USD rate
        self.assertIn("SOFR has stopped updating - its last print is 3.93 % of 2026-10-26, 14 days old", info["note"])

    def test_chf_forward_and_xccy_leg_refuse_with_the_reason(self):
        rt = self.rt
        with self.assertRaises(ConflictError) as cm:
            swaps.preview(rt, PB, "FX_FORWARD", dict(FWD, pair="USDCHF", tenor="1Y"))
        self.assertEqual(("FORWARD_UNAVAILABLE", "pair"), (cm.exception.code, cm.exception.details["field"]))
        self.assertIn("USDCHF 1Y: no forward price - no current CHF rate: IRSTCI01CHM156N has stopped updating", cm.exception.message)
        # the CHF leg of a cross-currency swap: 409 NO_CURVE with the date and the age, nothing is priced on 1.34 %
        with self.assertRaises(ConflictError) as cm:
            swaps.preview(rt, PB, "XCCY", {"pay_ccy": "CHF", "recv_ccy": "USD", "pay_notional": "1000000.00", "months": 24})
        self.assertEqual(("NO_CURVE", True, "2024-03-01", 941), (cm.exception.code, cm.exception.details["stale"], cm.exception.details["as_of"],
                                                                  cm.exception.details["age_days"]))
        # the FX desk's own forward grid says the same (no 1.34 % outright on any tenor)
        g = fx.forward_grid(rt, "USDCHF")
        self.assertEqual({False}, {r["ok"] for r in g["tenors"]})
        self.assertIn("no current CHF rate", g["tenors"][0]["note"])

    def test_desk_lists_spot_only_pairs_with_the_reason(self):
        """P8B-04: a pair one of whose currencies has no usable rate is spot only - NZD (no free series), CHF (stopped) and, in this fixture, the
        currencies never observed - each with its reason; the forward pairs are the others."""
        d = otc_options.desk(self.rt, PB)
        # 1.0.6.1: GBPUSD is priced on the term curves (a curve that is unavailable refuses its preview, with the reason), so it is not spot only
        self.assertEqual(["EURUSD", "GBPUSD", "USDJPY"], [p for p in d["forward_pairs"] if p in otc_options.FX_PAIRS])
        so = d["spot_only"]
        self.assertTrue(so["USDCHF"].startswith("spot only: no current CHF rate: IRSTCI01CHM156N has stopped updating"), so["USDCHF"])
        self.assertEqual("spot only: no free policy-rate series for NZD in this terminal: no forward price (spot only)", so["NZDUSD"])
        self.assertNotIn("EURUSD", so)
        self.assertEqual((True, False), (d["rates"]["EUR"]["ok"], d["rates"]["CHF"]["ok"]))
        self.assertEqual(1, d["rates"]["EUR"]["age_days"])


class ForwardTenorTests(_Base):
    """P8B-03 / OTC-07: every tenor offered is priced - a USD / EUR / GBP pair's on the term curves (1W .. 2Y, FX_CCS_PLAN D3), another
    pair's the FX desk's table (ON .. 1Y, the legacy method)."""

    def test_the_offered_tenors_are_priced(self):
        rt = self.rt
        curves(rt)
        d = otc_options.desk(rt, PB)
        self.assertEqual(["1W", "2W", "1M", "2M", "3M", "6M", "9M", "1Y", "18M", "2Y"], d["fx_forward_tenors"])
        self.assertEqual(list(fx.TENORS), d["fx_forward_tenors_legacy"])
        # Wed 10-28 10:00: FX trade date 10-28, spot Fri 10-30 (T+1 10-29 TARGET2, T+2 10-30 TARGET2 + New York); 2W = spot + 14 = Fri 11-13 (14 days).
        # The curves of 10-27 (t = days from 10-27 / 365): t_S = 3/365 = 0.008219, t_V = 17/365 = 0.046575.  EUR: z(t_S) = 2.473805 + (2.542643 -
        # 2.473805) x (0.008219 - 0.002740) / (0.25 - 0.002740) = 2.475330 %; z(t_V) = 2.473805 + 0.068838 x 0.177283 = 2.486009 %; DF*_EUR = exp(-0.02486009
        # x 0.046575 + 0.02475330 x 0.008219) = 0.9990460399.  USD (SOFR 3.95 % -> 4.004641 %, the Treasury bootstrap 4.293581 % at 0.5): z(t_S) =
        # 4.007825 %, z(t_V) = 4.030112 %; DF_USD = 0.9984535685.  F = 1.1158 x 0.9990460399 / 0.9984535685 = 1.116462; dealt 2 bp of F against you:
        # 1.116462 x 0.0002 = 0.000223 -> K = 1.116685
        p = swaps.preview(rt, PB, "FX_FORWARD", dict(FWD, tenor="2W"))
        self.assertEqual(("2026-11-13", 14, "1.116462", "1.116685", "2W"), (p["value_date"], p["days_from_spot"], p["forward"], p["strike"], p["tenor"]))
        # a tenor outside the table (ON is the FX desk's; 3W nobody's): 400 TERMS on the tenor field, listing the table; an equity forward keeps its own (no ON)
        for tenor in ("ON", "3W"):
            with self.assertRaises(RejectError) as cm:
                swaps.preview(rt, PB, "FX_FORWARD", dict(FWD, tenor=tenor))
            self.assertEqual(("TERMS", "tenor", d["fx_forward_tenors"]), (cm.exception.code, cm.exception.details["field"], cm.exception.details["tenors"]))
        with self.assertRaises(RejectError) as cm:
            swaps.preview(rt, PB, "EQ_FORWARD", {"underlying_id": "AAPL", "units": 1000, "side": "BUY", "tenor": "ON"})
        self.assertEqual("tenor", cm.exception.details["field"])
        # the FX desk's own grid (unchanged in 1.0.6.1: simple-interest CIP on one rate per currency) has the 2W row on the same value date:
        # F = 1.1158 x (1 + 3.95 % x 14/360) / (1 + 2.5 % x 14/360) = 1.1158 x 1.00153611 / 1.00097222 = 1.116429 (the PB curves: 1.116462)
        g = {r["tenor"]: r for r in fx.forward_grid(rt, "EURUSD")["tenors"]}
        self.assertEqual(("2026-11-13", "1.116429"), (g["2W"]["value_date"], g["2W"]["outright"]))


class EquityForwardTests(_Base):
    """P8B-02 / P8B-07."""

    def test_the_financing_spread_is_charged_on_the_side_dealt(self):
        """AAPL 343.83, 1Y (365 days), SOFR 3.93 %, q 0.318 % (the instrument master's trailing yield), GS financing spread 1.25 %, half-width 2 bp.
        - mid (the mark): r = ln(1 + 0.0393 x 365/360) = ln(1.03984583) = 0.039072; F = 343.83 x e^(0.039072 - 0.00318) = 343.83 x 1.0365440 = 356.3951
        - BUY: carry 3.93 + 1.25 = 5.18 %: r = ln(1.05251944) = 0.051187; F_deal = 343.83 x e^(0.048007) = 360.7388; hw = 360.7388 x 0.0002 =
          0.0721; K = 360.8109.  DF = e^(-0.039072) = 0.961681; mark0 = 1,000 x (356.3951 - 360.8109) x 0.961681 = -4,246.59 (the whole cost of
          dealing: financing spread and half-width, both against you)
        - SELL: carry 3.93 - 1.25 = 2.68 %: r = ln(1.02717222) = 0.026810; F_deal = 343.83 x e^(0.02363) = 352.0513; hw 0.0704; K = 351.9809 - BELOW
          the SOFR-carry mid 356.3951 (before the fix a sale dealt at 360.74, 4.28 a share above it); mark0 = -1,000 x (356.3951 - 351.9809) x
          0.961681 = -4,245.05"""
        rt = self.rt
        buy = swaps.preview(rt, PB, "EQ_FORWARD", {"underlying_id": "AAPL", "units": 1000, "side": "BUY", "tenor": "1Y"})
        sell = swaps.preview(rt, PB, "EQ_FORWARD", {"underlying_id": "AAPL", "units": 1000, "side": "SELL", "tenor": "1Y"})
        self.assertEqual(("356.3951", "360.7388", "360.8109", -424659, 424659, "5.18"),
                         (buy["forward"], buy["forward_deal"], buy["strike"], buy["mark0_c"], buy["cost_of_dealing_c"], buy["carry_pct"]))
        self.assertEqual(("356.3951", "352.0513", "351.9809", -424505, 424505, "2.68"),
                         (sell["forward"], sell["forward_deal"], sell["strike"], sell["mark0_c"], sell["cost_of_dealing_c"], sell["carry_pct"]))
        self.assertLess(D(sell["strike"]), D(sell["forward"]))
        self.assertGreater(D(buy["strike"]), D(buy["forward"]))
        self.assertEqual(("0.039072", "0.051187", "0.026810"), (buy["inputs"]["r_mid"], buy["inputs"]["r_financing"], sell["inputs"]["r_financing"]))
        # booked, the contract is marked at the mid (SOFR carry): the stored mark is mark0 and its terms keep the dealer's forward and carry
        r = execute(rt, "otc.book", {"account_id": PB, "kind": "EQ_FORWARD", "terms": {"underlying_id": "AAPL", "units": 1000, "side": "SELL", "tenor": "1Y"}},
                    "ui:eqfwd-sell-0001")
        self.assertTrue(r.ok, r.body)
        cid = r.body["contract_id"]
        row = A.one(rt, "SELECT mark_c, terms_json FROM otc_contracts WHERE contract_id=?", (cid,))
        t = json.loads(row["terms_json"])
        self.assertEqual((-424505, "351.9809", "356.3951", "352.0513", "2.68", "SOFR"), (row["mark_c"], t["strike"], t["F0"], t["F_deal0"], t["carry_pct"], t["mark_carry"]))
        # the live mark carries F at full precision: 343.83 x e^(0.0390725 - 0.00318) = 356.3950527 -> -1,000 x (356.3950527 - 351.9809) x 0.9616810
        # = -4,245.0069 -> -4,245.01 (the deal's mark0 used F at 4 dp: -4,245.05)
        mark_c, inputs = otc_options.mark(rt, cid)
        self.assertEqual((-424501, "356.3951"), (mark_c, inputs["F"]))
        # terminating at once deals the opposite side the same way: the short is bought back on the dealer's BUY carry, 360.7388 + 0.0721 =
        # 360.8109: value = -1,000 x (360.8109 - 351.9809) x 0.961681 = -1,000 x 8.8300 x 0.961681 = -8,491.64 (both dealings' costs, as a round trip)
        rt.clock.set(rt.clock.epoch() + 60)
        rt.marks.set("AAPL", "343.83", "delayed", rt.clock.epoch())
        r = execute(rt, "otc.terminate", {"contract_id": cid}, "ui:eqfwd-term-0001")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(-849164, r.body["termination_value_c"])
        self.assertIn("closed at 360.8109: the dealer's forward 360.7388 at SOFR + 1.25 %", r.body["rule"])
        assert_integrity(self, rt, "full")

    def test_a_name_without_dividend_data_is_not_priced(self):
        rt = self.rt
        # VMRK as the owner's master holds it: looked up from Yahoo (no universe yield) and no dividend event recorded -> 409 NO_DIVIDEND_DATA
        with rt.db.writer() as tx:
            rt.instruments.upsert_from_yahoo_meta(tx, "VMRK", {"exchangeName": "NYQ", "currency": "USD", "instrumentType": "EQUITY", "longName": "VMRK test name"})
        rt.instruments.load()
        rt.marks.set("VMRK", "60.90", "delayed", rt.clock.epoch())
        for kind, terms in (("EQ_FORWARD", {"underlying_id": "VMRK", "units": 1000, "side": "SELL", "tenor": "6M"}),
                            ("EQ_OPTION", {"underlying_id": "VMRK", "units": 1000, "side": "BUY", "right": "PUT", "strike_pct": "95", "tenor": "3M"})):
            with self.assertRaises(ConflictError) as cm:
                swaps.preview(rt, PB, kind, terms)
            if cm.exception.code == "NO_DIVIDEND_DATA":
                self.assertEqual(("underlying_id", "VMRK"), (cm.exception.details["field"], cm.exception.details["underlying_id"]))
                self.assertIn("its dividend yield is unknown and is never assumed to be 0", cm.exception.message)
            else:                                   # the option needs a Cboe chain first (none for VMRK here): it is refused before the yield
                self.assertEqual("EQ_OPTION", kind)
                self.assertIn(cm.exception.code, ("NO_CHAIN", "CHAIN_FETCHING"))
        # a name the universe lists without a dividend (TSLA: dividend_yield 0.0) is data, not an assumption: priced, said so
        p = swaps.preview(rt, PB, "EQ_FORWARD", {"underlying_id": "TSLA", "units": 100, "side": "BUY", "tenor": "3M"})
        self.assertEqual(D(0), D(p["inputs"]["q"]))
        self.assertTrue(p["inputs"]["dividend"]["method"].startswith("trailing dividend yield of the instrument master"), p["inputs"]["dividend"])


class RiskForwardTests(_Base):
    """P8B-08 / OTC-02: buy EUR 1,000,000.00 3M on the curves (1.0.6.1): spot 10-30, value 2027-01-30 (a Saturday) modified following ->
    Fri 2027-01-29, 91 days; t_V = 94/365 = 0.257534: DF*_EUR = exp(-0.02547191 x 0.257534 + 0.02475330 x 0.008219) = 0.9936637222 (EUR z(t_V)
    between 3M 2.542643 and 6M 2.693553), DF_USD = exp(-0.04152693 x 0.257534 + 0.04007825 x 0.008219) = 0.9896883365; F = 1.1158 x 0.9936637222
    / 0.9896883365 = 1.120282; K = F + 2 bp = 1.120282 + 0.000224 = 1.120506 (the delivery sells USD 1,000,000 x 1.120506 = 1,120,506.00).
    Its USD notional at the deal 1,000,000 x 1.1158 = 1,115,800.00 sits in 'FX forwards (notional)' outside the headline (long 0, short 0);
    the currency exposure carries the EUR leg: EUR 1,000,000.00 x 1.1158 = USD 1,115,800.00, so 'USD +5 % vs foreign currencies' = -55,790.00
    and 'USD -5 %' = +55,790.00."""

    def test_an_otc_fx_forward_is_a_currency_exposure_not_a_headline_long(self):
        rt = self.rt
        curves(rt)
        r = execute(rt, "otc.book", {"account_id": PB, "kind": "FX_FORWARD", "terms": dict(FWD)}, "ui:fxfwd-0001")
        self.assertTrue(r.ok, r.body)
        t = json.loads(A.one(rt, "SELECT terms_json FROM otc_contracts WHERE contract_id=?", (r.body["contract_id"],))["terms_json"])
        self.assertEqual(("1.120506", "EUR", 100000000, "USD", 112050600), (t["strike"], t["buy_ccy"], t["buy_c"], t["sell_ccy"], t["sell_c"]))
        v = risk.report(rt, PB)
        b = {e["bucket"]: e for e in v.exposures}
        self.assertNotIn("OTC options and forwards", b)
        self.assertEqual((111580000, 0, False), (b["FX forwards (notional)"]["long_c"], b["FX forwards (notional)"]["short_c"], b["FX forwards (notional)"]["headline"]))
        self.assertEqual((0, 0), (v.long_c, v.short_c))
        self.assertEqual((100000000, 111580000), (v.exposure_by_currency["EUR"]["native_c"], v.exposure_by_currency["EUR"]["usd_c"]))
        self.assertEqual({"USD +5 % vs foreign currencies": -5579000, "USD -5 % vs foreign currencies": 5579000},
                         {s["name"]: s["pnl_c"] for s in v.scenarios if s["group"] == "fx"})
        # OTC-18 (15.15b): the position row reads as the forward it is - 1,000,000 EUR bought forward vs USD, its two legs and value date, the
        # dealt rate 1.120506 against the forward now, held 0 days (never a negative count)
        p = rt.accounting.position(PB, r.body["contract_id"])
        pf = p.extra["position_fields"]["extra"]
        self.assertEqual((1000000, "EUR bought forward vs USD", "1.120506", "buy EUR / sell USD", "buy EUR 1,000,000.00 / sell USD 1,120,506.00, value 2027-01-29",
                          "dealt rate", 0),
                         (p.extra["units"], p.extra["unit_label"], p.extra["entry"], pf["side"], pf["legs"], pf["entry_label"], p.days_held))
        # OTC-23 (15.15b): the dealer's quote spells the deal out
        pv = swaps.preview(rt, PB, "FX_FORWARD", dict(FWD))
        self.assertEqual("you buy EUR 1,000,000.00 / sell USD 1,120,506.00 for value 2027-01-29 at 1.120506 (model forward 1.120282)", swaps._quote_terms("FX_FORWARD", pv)[2])
        # an equity forward stays in the headline (equity delta)
        r = execute(rt, "otc.book", {"account_id": PB, "kind": "EQ_FORWARD", "terms": {"underlying_id": "AAPL", "units": 100, "side": "BUY", "tenor": "3M"}},
                    "ui:eqfwd-0002")
        self.assertTrue(r.ok, r.body)
        b = {e["bucket"]: e for e in risk.report(rt, PB).exposures}
        self.assertTrue(b["OTC options and forwards"]["headline"])
        assert_integrity(self, rt, "full")


class JobsAndWordingTests(_Base):
    def test_jobs_view_gives_the_product_jobs_next_run(self):
        """P8B-15: with an FX forward (value 2027-01-29 -> OTC_EXPIRY from 17:05 ET) and an equity forward maturing 2027-01-28 (from 16:20 ET),
        OTC_EXPIRY's next run is the earlier, 2027-01-28 16:20 ET; FX_FORWARD_DUE has no FX-desk forward pending (none here); XCCY_EVENTS none."""
        from finsim.terminal import api
        rt = self.rt
        curves(rt)
        self.assertTrue(execute(rt, "otc.book", {"account_id": PB, "kind": "FX_FORWARD", "terms": dict(FWD)}, "ui:fxfwd-0002").ok)
        self.assertTrue(execute(rt, "otc.book", {"account_id": PB, "kind": "EQ_FORWARD", "terms": {"underlying_id": "AAPL", "units": 100, "side": "BUY",
                                                                                                       "tenor": "3M"}}, "ui:eqfwd-0003").ok)
        rows = {r["name"]: r for r in api.jobs_view(rt, rt.clock.epoch())["jobs"]}
        exp = rows[otc_options.JOB_EXPIRY]
        self.assertEqual((epoch_of(date(2027, 1, 28), "16:20"), epoch_of(date(2027, 1, 28), "16:20"), "OTC-000002 maturity 2027-01-28"),
                         (exp["next_due_ts"], exp["next_due"], exp["next_due_ref"]))
        self.assertIsNone(rows["FX_FORWARD_DUE"]["next_due_ts"])
        self.assertIsNone(rows["XCCY_EVENTS"]["next_due_ts"])
        # an FX-desk forward: FX_FORWARD_DUE runs from 00:30 ET of its value date
        with rt.db.reader() as conn:
            self.assertIsNone(fx.next_forward_due(conn, rt.clock.epoch()))
        r = execute(rt, "fx.forward", {"account_id": PB, "buy_ccy": "EUR", "sell_ccy": "USD", "amount_c": 10000000, "amount_ccy": "EUR", "tenor": "1M"})
        self.assertTrue(r.ok, r.body)
        rows = {x["name"]: x for x in api.jobs_view(rt, rt.clock.epoch())["jobs"]}
        vd = date.fromisoformat(r.body["deal"]["value_date"])
        self.assertEqual((epoch_of(vd, "00:30"), f"{r.body['deal']['deal_id']} value {vd.isoformat()}"),
                         (rows["FX_FORWARD_DUE"]["next_due_ts"], rows["FX_FORWARD_DUE"]["next_due_ref"]))

    def test_the_fx_forward_notional_says_its_usd_value_is_at_the_spot_when_dealt(self):
        """P8R1-07: an FX forward's USD notional is the base notional at the base currency's USD rate WHEN DEALT (the IM's base), not what
        the deal delivers.  BUY EUR 1,000,000.00 3M at spot 1.1158: USD notional 1,000,000 x 1.1158 = 1,115,800.00 (IM 6 % = 66,948.00),
        while the delivery pays USD 1,120,506.00 at the dealt forward 1.120506 (1.0.6.1: on the curves, RiskForwardTests) - so the drawer says
        "at the spot rate 1.1158 when dealt" (the old "USD 1,115,800.00 at the deal" read as the delivered amount)."""
        rt = self.rt
        curves(rt)
        r = execute(rt, "otc.book", {"account_id": PB, "kind": "FX_FORWARD", "terms": dict(FWD)}, "ui:fxfwd-im-base")
        self.assertTrue(r.ok, r.body)
        cid = r.body["contract_id"]
        row = A.one(rt, "SELECT notional_c, im_c FROM otc_contracts WHERE contract_id=?", (cid,))
        self.assertEqual((111580000, 6694800), (row["notional_c"], row["im_c"]))
        d = otc_options.contract_detail(rt, cid)
        self.assertEqual(("1.1158", "EUR/USD", ["USD", 112050600]), (d["im_base_rate"], d["im_base_pair"], d["delivery"]["pay"]))
        # a cross's USD notional is at its base currency's USD rate: EUR 2,000,000.00 at EUR/USD 1.1158 = USD 2,231,600.00 (a contract booked
        # before the field existed reads the same: the rate is the stored USD notional over the stored base notional)
        self.assertEqual({"im_base_rate": "1.1158", "im_base_pair": "EUR/USD"},
                         otc_options.fx_im_base({"notional_c": 223160000}, {"notional_c": 200000000, "pair": "EURGBP"}))
        self.assertEqual({}, otc_options.fx_im_base({"notional_c": 0}, {"notional_c": 200000000, "pair": "EURGBP"}))
        import os
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(swaps.__file__))), "static", "components", "swapticket.js"), encoding="ascii") as fh:
            js = fh.read()
        self.assertIn("' when dealt: the IM base)'", js)
        self.assertNotIn("' at the deal)'", js)
        self.assertNotIn("' at the deal' : ''", js)

    def test_the_otc_contract_tables_stack_their_figures(self):
        """P8R1-05 (rule 5): the Derivatives > OTC contracts table puts the status and account under the contract, the units over the USD
        notional, what NAV carries under the model mark and the IM under the VM balance (9 columns, every mark on screen at 1440 px); the PB OTC
        tab's contracts table puts the Terminate button under the confirmation, the maturity under the notional and the IM under the VM balance
        (7 columns: it fits with the ticket dock open); the quote-request block wraps its quote words wherever it is mounted.  The browser
        measurement on the owner's copy is in the fixer's evidence."""
        import os
        static = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(swaps.__file__))), "static")
        with open(os.path.join(static, "pages", "derivatives.js"), encoding="ascii") as fh:
            deriv = fh.read()
        with open(os.path.join(static, "components", "swapticket.js"), encoding="ascii") as fh:
            st = fh.read()
        for label in ("contract / status / account", "model mark / in NAV", "VM balance / IM", "DV01 / CS01"):
            self.assertIn(f"'{label}'", deriv)
        self.assertNotIn("th('in NAV', 'num'", deriv)
        self.assertIn("T.h('td', { colspan: '9' }, otcDrawer(st, c))", deriv)
        self.assertIn("var COLS = 7;", st)
        self.assertIn("'notional / maturity'", st)
        self.assertIn("'VM balance / IM'", st)
        self.assertIn("ensureCss();                                /* P8R1-05", st)

    def test_a_missing_daily_bar_is_a_data_gap_not_a_delisting(self):
        rt = self.rt
        r = execute(rt, "lifecycle.marks_record", {"rows": [], "missing": [{"instrument_id": "EURUSD", "mark_date": "2026-10-23", "symbol": "EURUSD=X"}]},
                    "job:MARKS_RECORD:test", actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        msg = A.one(rt, "SELECT message FROM attention WHERE code='MARK_MISSING'")["message"]
        self.assertEqual("EURUSD: the free data feed sent no daily bar for 2026-10-23 (EURUSD=X); the close of that date stays unrecorded and accruals /"
                         " expiries that need it wait for it", msg)
        self.assertNotIn("delisted", msg)


class SnapshotSessionMemoTests(unittest.TestCase):
    """P8B-09: ``QuoteCache.get_many`` (one mark snapshot) asks the session table once per (group, provider window) - the 770 US equities of the
    owner's cache share one window - instead of once per record; the qualities are those of a per-record computation."""

    def test_one_session_state_per_window(self):
        from finsim.terminal.calendar import TradingCalendar
        from finsim.terminal.clock import FakeClock
        from finsim.terminal import config
        from finsim.terminal.quotes import QuoteRecord
        from finsim.terminal.quotes.cache import QuoteCache
        from finsim.terminal.sessions import SessionTable
        clock = FakeClock("2026-10-28T11:00:00-04:00")
        sessions = SessionTable(TradingCalendar(config.holidays_path()), clock)
        calls = []

        class Counting:
            def state(self, group, now, foreign=None):
                calls.append((group, None if foreign is None else (foreign["open_ts"], foreign["close_ts"])))
                return sessions.state(group, now, foreign=foreign)
        cache = QuoteCache(sessions=Counting(), clock=clock)
        now = clock.epoch()
        window = (epoch_of(date(2026, 10, 28), "09:30"), epoch_of(date(2026, 10, 28), "16:00"))
        for i in range(50):
            cache.put(QuoteRecord(instrument_id=f"N{i:03d}", provider="yahoo", source="Yahoo", method="direct", fetched_at=now - 30, currency="USD",
                                  session_group="US_EQUITY", last=D("10.00"), quote_time=now - 60, delay_s=0, provider_session=window), at=now)
        calls.clear()
        recs = cache.get_many(cache.ids(), now)
        self.assertEqual([("US_EQUITY", window)], calls)                       # once for the 50 records (before: 50 times)
        self.assertEqual({"live"}, {r.quality for r in recs.values()})
        alone = cache.get("N007", now)                                          # one record, no memo: the same quality
        self.assertEqual("live", alone.quality)


if __name__ == "__main__":
    unittest.main()
