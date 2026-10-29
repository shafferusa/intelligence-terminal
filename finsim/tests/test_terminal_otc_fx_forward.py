"""FX_CCS_PLAN rev 2, package P-B (release 1.0.6.1): the prime broker's OTC FX forward out to 2 years on the FX term curves (D2, D3, D5,
3.2), its value calendar (D10), its delivery at 00:30 ET (G12), its pricing record, the legacy method of the other pairs (1.5) and the K6
hooks.  Money tests B-T1 .. B-T5, B-T9 .. B-T11.

The world is the worked example's (FX_CCS_PLAN 3.2): F1 with its Goldman Sachs PB account (the as-of tests' runtime), Thursday
2026-10-29 10:00 ET (trade date T), ``fxcurves.fixture_curves`` (the 2026-10-27 rows: the Bank of England OIS 1M..60M row, the ECB AAA spot
rates, the Treasury par row; SONIA 3.7328 % of 10-26, ESTR 2.440 % and SOFR 3.95 % of 10-27), fresh delayed prints EURUSD 1.1158,
GBPUSD 1.3323, the cross print EURGBP 0.83725, USDJPY 153.32; the GS schedule seeds (forward half-width 2.0 bp, multiplier 1.0, forward IM
6 %, basis levels and EUR OIS spread 0.0 bp).  No network.  Every expected number is worked by hand in the comment above it (the
designer's Decimal scripts reproduce them, precision 34).

Hand-worked (3.2):
- dates: T = Thu 2026-10-29; EURGBP spot: T+1 Fri 10-30 (TARGET2 + London), T+2 Mon 11-02 (and New York) -> S = 2026-11-02; V = S + 24
  months = 2028-11-02 (a Thursday, good in TARGET2, London and New York); days = 731.
- spot: derived 1.1158 / 1.3323 = 0.837499061... -> 0.837499 (6 dp); the print 0.83725 is (0.83725 - 0.8374990617) / 0.0001 = -2.49 pips
  away (inside 5.0).
- curves (d0 2026-10-27, t = days / 365): t_S = 6 / 365 = 0.016438, t_V = 737 / 365 = 2.019178.  GBP z(t_S) = 3.732609 + (3.899390 -
  3.732609) x (0.016438 - 0.002740) / (0.083333 - 0.002740) = 3.760957 %; z(t_V) = 4.727447 + (4.734519 - 4.727447) x (2.019178 - 2) / (2.083333
  - 2) = 4.729075 %.  EUR z(t_S) = 2.473805 + (2.542643 - 2.473805) x 0.013699 / 0.247260 = 2.477619 %; z(t_V) = 3.015032 + (3.051054 -
  3.015032) x 0.019178 = 3.015723 %.
- DF*_EUR = exp(-0.03015723 x 2.019178 + 0.02477619 x 0.016438) = exp(-0.060486) = 0.9413073853; DF*_GBP = exp(-0.04729075 x 2.019178 +
  0.03760957 x 0.016438) = 0.9094909816 (basis levels 0.0 bp).
- F = 0.837499 x 0.9413073853 / 0.9094909816 = 0.86679693 -> 0.866797; points (0.866797 - 0.837499) / 0.0001 = 292.98.
- dealt: hw_px = 0.866797 x 2.0 / 10,000 = 0.00017336 -> 0.000173; a SALE of EUR deals F - hw_px: K = 0.866624.
- amounts: sell EUR 2,000,000.00, buy GBP 2,000,000 x 0.866624 = 1,733,248.00; USD notional 2,000,000 x 1.1158 = 2,231,600.00; IM 6 % =
  133,896.00.
- mark0 = -2,000,000 x (0.866797 - 0.866624) x 0.9094909816 x 1.3323 = -2,000,000 x 0.000173 x 0.9094909816 x 1.3323 = -419.2533 -> -419.25.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from datetime import date
from decimal import Decimal

from tests import test_terminal_otc_asof as A
from tests.terminal_helpers import assert_integrity, bal, execute
from finsim.terminal import fx, fxcurves
from finsim.terminal.clock import epoch_of
from finsim.terminal.commands import ConflictError, RejectError
from finsim.terminal.products import otc_options, swaps

D = Decimal
PB = A.PB
TRADE_AT = "2026-10-29T10:00:00-04:00"
B_TERMS = {"pair": "EURGBP", "side": "SELL", "notional": "2000000.00", "tenor": "2Y", "settlement_funding": {"EUR": "BUY_WITH_USD"}}
PRINTS = (("EURUSD", "1.1158"), ("GBPUSD", "1.3323"), ("EURGBP", "0.83725"), ("USDJPY", "153.32"))
#: V1061R2-01 (FX_CCS_PLAN 3.2 labels row, D16): the record's schedule-terms label, word for word - the basis levels, the dealer half-width
#: and IM are ASSUMED Goldman Sachs schedule terms (the float / float swap's record names its level, half-width and IM the same way)
GS_TERMS_LABEL = "ASSUMED: the basis levels, the dealer half-width and IM are Goldman Sachs schedule terms"
#: the EURGBP forward's labels in the worked example's world, in order: MODEL, each curve's own labels (EUR PROXY with its spread, the
#: EUR/USD level; GBP observed, the GBP/USD level), then the schedule-terms label - which replaces the curve module's basis-only one
FWD_LABELS = ["MODEL",
              "PROXY: ECB euro-area AAA government curve of 2026-10-27 + 0.0 bp (Goldman Sachs schedule, ASSUMED): no free EUR OIS curve",
              "ASSUMED: EUR OIS spread 0.0 bp over the ECB AAA curve (Goldman Sachs schedule (as edited), ASSUMED)",
              "ASSUMED: EUR/USD cross-currency basis level 0.0 bp on the EUR discount curve (Goldman Sachs schedule (as edited), ASSUMED)",
              "observed: Bank of England UK OIS curve of 2026-10-27 (continuously compounded)",
              "ASSUMED: GBP/USD cross-currency basis level 0.0 bp on the GBP discount curve (Goldman Sachs schedule (as edited), ASSUMED)",
              GS_TERMS_LABEL]
#: V1061R2-01: the IM percentage's source and the half-width's, each said ASSUMED where it is shown or stored
IM_SOURCE = "the prime broker's schedule, ASSUMED"
HW_SOURCE = "the dealer half-width is a Goldman Sachs schedule term, ASSUMED"


def runtime(tmp, at: str = TRADE_AT):
    """The as-of runtime moved to the trade time, the OTC options / forwards and FX modules bound, the scheduler rebuilt after the binds,
    the worked example's curves and prints, the ECB deposit rate and the OECD monthly JPY proxy (the legacy method's inputs)."""
    rt = A.runtime(tmp, at=at)
    rt.otc_options = otc_options
    otc_options.register(rt.bus, rt)
    otc_options.bind(rt)
    fx.register(rt.bus)
    rt.fx = fx
    fx.bind(rt)
    from finsim.terminal import lifecycle
    rt.lifecycle = lifecycle.LifecycleScheduler(rt.bus, rt.db, rt, rt.clock, rt.log)
    fxcurves.fixture_curves(rt, persist=False)
    rt.rates.put("ECBDFR", [(date(2026, 10, 27), D("2.50"))], persist=False)
    rt.rates.put("IRSTCI01JPM156N", [(date(2026, 9, 1), D("1.043"))], persist=False)
    prints(rt)
    return rt


def prints(rt, at=None, values=PRINTS):
    now = int(at if at is not None else rt.clock.epoch())
    for iid, px in values:
        rt.marks.set(iid, px, "delayed", now)


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-pbfwd-")
        self.rt = runtime(self.tmp)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def preview(self, **over):
        return swaps.preview(self.rt, PB, "FX_FORWARD", dict(B_TERMS, **over))

    def book_b(self, request_id="ui:pbfwd-b-0001", **over):
        r = execute(self.rt, "otc.book", {"account_id": PB, "kind": "FX_FORWARD", "terms": dict(B_TERMS, **over)}, request_id)
        self.assertTrue(r.ok, r.body)
        return r.body["contract_id"]


class WorkedExampleTests(_Base):
    """B-T1, B-T9, B-T11."""

    def test_b_t1_the_eurgbp_2y_sale_is_the_worked_example(self):
        p = self.preview()
        # the module docstring's arithmetic: S, F, points, hw_px, K, the dates and the amounts
        self.assertEqual(("0.837499", "0.866797", "292.98", "0.000173", "0.866624"), (p["S"], p["forward"], p["points"], p["half_width_px"], p["strike"]))
        self.assertEqual(("2026-10-29", "2026-11-02", "2028-11-02", 731, "2Y"), (p["trade_date"], p["spot_date"], p["value_date"], p["days_from_spot"], p["tenor"]))
        rec = p["pricing_record"]
        self.assertEqual(("0.9413073853", "0.9094909816"), (rec["dfs"]["base"], rec["dfs"]["quote"]))
        self.assertEqual("F = 0.837499 x 0.9413073853 / 0.9094909816 = 0.866797", rec["forward"]["arithmetic"])
        # sell EUR 2,000,000.00 / buy GBP 2,000,000 x 0.866624 = 1,733,248.00; USD notional 2,000,000 x 1.1158 = 2,231,600.00; IM 6 % = 133,896.00;
        # mark0 = -2,000,000 x 0.000173 x 0.9094909816 x 1.3323 = -419.2533 -> -419.25 (the cost of dealing, never a gain)
        self.assertEqual({"receive": {"ccy": "GBP", "amount_c": 173324800}, "pay": {"ccy": "EUR", "amount_c": 200000000}}, p["delivery"])
        self.assertEqual((223160000, 13389600, -41925, 41925, True, []), (p["notional_c"], p["im_c"], p["mark0_c"], p["cost_of_dealing_c"], p["bookable"], p["blockers"]))
        # the spot: derived from the USD pairs; the cross print 0.83725 stored with its difference (0.83725 - 0.8374990617) / 0.0001 = -2.49 pips
        self.assertEqual(("derived from the USD pairs", "0.837499", "0.83725", "-2.49", True), (rec["spot"]["basis"], rec["spot"]["mid"], rec["spot"]["print"]["mid"],
                                                                                               rec["spot"]["diff_pips"], rec["spot"]["executable"]))
        self.assertEqual(["EURUSD", "GBPUSD"], [lg["pair"] for lg in rec["spot"]["legs"]])
        # the labels: GBP observed (Bank of England), EUR PROXY (ECB AAA + spread), the schedule terms ASSUMED, the whole a MODEL number
        labels = p["labels"]
        self.assertEqual("MODEL", labels[0])
        self.assertTrue(any(x.startswith("observed: Bank of England UK OIS curve of 2026-10-27") for x in labels), labels)
        self.assertTrue(any(x.startswith("PROXY: ECB euro-area AAA government curve of 2026-10-27 + 0.0 bp") for x in labels), labels)
        self.assertTrue(any(x.startswith("ASSUMED:") for x in labels), labels)
        # V1061R2-01 (3.2 labels row, D16): the dealer half-width and the IM percentage are schedule terms, ASSUMED - named word for word in
        # the record's labels (one label with the basis levels; the curve module's basis-only "ASSUMED: the basis levels are ..." is gone),
        # in the record's dealer and IM blocks and on the ticket (the dealt-rate line and the IM rule)
        self.assertEqual(FWD_LABELS, labels)
        self.assertEqual(FWD_LABELS, rec["labels"])
        self.assertEqual(FWD_LABELS, p["terms"]["labels"])
        self.assertEqual([], [x for x in labels if x.startswith("ASSUMED: the basis levels are ")])
        self.assertEqual((IM_SOURCE, HW_SOURCE, HW_SOURCE), (rec["im"]["source"], rec["dealer"]["hw_source"], p["half_width_source"]))
        self.assertEqual("6.00 % of the USD notional 2,231,600.00 = 133,896.00 (the prime broker's schedule, ASSUMED)", p["im_rule"])
        self.assertTrue(p["price_rule"].endswith("= 0.866624; " + HW_SOURCE), p["price_rule"])
        self.assertEqual("CURVES", p["method"])
        # the curves as recorded: kind, date, the first node and the interpolated rates at S and V (the docstring's arithmetic)
        g, e = rec["curves"]["GBP"], rec["curves"]["EUR"]
        self.assertEqual(("observed", "2026-10-27", "IUDSOIA", "3.760957", "4.729075"), (g["kind"], g["as_of"], g["first_node"]["series"], g["z_S"], g["z_V"]))
        self.assertEqual(("PROXY", "2026-10-27", "ESTR", "2.477619", "3.015723"), (e["kind"], e["as_of"], e["first_node"]["series"], e["z_S"], e["z_V"]))

    def test_b_t1_the_booking_stores_the_record_and_posts_the_im(self):
        rt = self.rt
        tre0 = bal(rt, "F1:TREASURY", "1010")
        cid = self.book_b()
        row = A.one(rt, "SELECT * FROM otc_contracts WHERE contract_id=?", (cid,))
        t = json.loads(row["terms_json"])
        # the contract: OTC-000001 if it is the first; strike K, the two delivery legs, value date V; notional = the USD notional; mark0 -419.25
        self.assertEqual(("FX_FORWARD", "OPEN", "2026-10-29", "2028-11-02", 223160000, 13389600, -41925),
                         (row["kind"], row["status"], row["start_date"], row["maturity_date"], row["notional_c"], row["im_c"], row["mark_c"]))
        self.assertEqual(("0.866624", "EUR", 200000000, "GBP", 173324800, "CURVES", "292.98", "0.9413073853", "0.9094909816", {"EUR": "BUY_WITH_USD"}),
                         (t["strike"], t["sell_ccy"], t["sell_c"], t["buy_ccy"], t["buy_c"], t["pricing_method"], t["points"], t["df_base"], t["df_quote"],
                          t["settlement_funding"]))
        rec = t["pricing_record"]
        self.assertEqual(("fxcurves-2", "0.866797", "0.866624", 13389600), (rec["model_version"], rec["forward"]["outright"], rec["dealer"]["K"],
                                                                         rec["im"]["im_c"]))
        # V1061R2-01: the record stored at booking carries the complete labels - the dealer half-width and IM ASSUMED with the basis levels
        self.assertEqual((FWD_LABELS, FWD_LABELS), (rec["labels"], t["labels"]))
        self.assertEqual((IM_SOURCE, HW_SOURCE), (rec["im"]["source"], rec["dealer"]["hw_source"]))
        # IM: one Treasury sweep of 133,896.00, then Dr 1330 / Cr 1010 (OTC_IM); its memo says the percentage is an ASSUMED schedule term
        self.assertEqual(13389600, bal(rt, PB, "1330", "USD", cid))
        memo = A.one(rt, "SELECT memo FROM journal_entries WHERE kind='OTC_IM' AND ref_type='otc' AND ref_id=?", (cid,))["memo"]
        self.assertTrue(memo.endswith("IM = 6.00 % x notional 2,231,600.00 = 133,896.00 (the prime broker's schedule, ASSUMED)"), memo)
        self.assertEqual(tre0 - 13389600, bal(rt, "F1:TREASURY", "1010"))
        # nothing is delivered yet: no EUR or GBP on the ledger, the legs are off it (the risk hook carries them)
        self.assertEqual((0, 0, 0, 0), (bal(rt, PB, "1010", "EUR"), bal(rt, PB, "1010", "GBP"), bal(rt, PB, "1250", "GBP"), bal(rt, PB, "2350", "EUR")))
        # the live mark at the deal's prints is mark0 exactly (the quote's quantisations, D19)
        mark_c, inputs = otc_options.mark(rt, cid)
        self.assertEqual((-41925, "0.866797", "2026-11-02"), (mark_c, inputs["F"], inputs["spot_date"]))
        assert_integrity(self, rt, "full")

    def test_b_t9_the_record_carries_basis_limits_funding_and_cross_checks(self):
        p = self.preview()
        rec = p["pricing_record"]
        # the basis block: EURGBP, EUR/USD 0.0 and GBP/USD 0.0 bp (GS schedule, ASSUMED), cross 0.0 - 0.0 = 0.0 bp, on both discount curves
        b = rec["basis"]
        self.assertEqual(("EURGBP", "0.0", "0.0", "0.0", "both discount curves"), (b["pair"], b["eur_usd_level_bp"], b["gbp_usd_level_bp"], b["cross_bp"], b["on"]))
        self.assertIn("Goldman Sachs schedule", b["source"])
        self.assertIn("ASSUMED", b["source"])
        # V1061R2-01 (3.2 labels row, D16): the other schedule terms beside the basis levels - the dealer's half-width 2.0 bp x 1.0 and the
        # IM 6 % - are ASSUMED in the record: its labels name all three in one label (exactly once), its dealer and IM blocks say so
        self.assertEqual(1, rec["labels"].count(GS_TERMS_LABEL))
        self.assertEqual(("2.0", "1.0", "0.06"), (rec["dealer"]["forward_hw_bp"], rec["dealer"]["otc_spread_mult"], rec["im"]["pct"]))
        self.assertEqual((HW_SOURCE, IM_SOURCE), (rec["dealer"]["hw_source"], rec["im"]["source"]))
        self.assertTrue(rec["dealer"]["schedule_source"].startswith("Goldman Sachs schedule") and rec["dealer"]["schedule_source"].endswith("ASSUMED"),
                        rec["dealer"]["schedule_source"])
        # the limits: none configured; the headline gross does not move (outside the headline), the gross notional rises by 2,231,600.00
        lim = rec["limits"]
        self.assertEqual("notional limits: none configured in the Goldman Sachs schedule; compliance has no OTC limit", lim["text"])
        self.assertEqual(lim["headline_gross_before_c"], lim["headline_gross_after_c"])
        self.assertEqual(223160000, lim["gross_notional_after_c"] - lim["gross_notional_before_c"])
        # the settlement funding: ON for EUR, said in words; the plan's gap comes from the settlement-funding module (or says it is not built)
        self.assertTrue(rec["funding"]["line"].startswith("settlement funding ON: the EUR 2,000,000.00 delivered on 2028-11-02 is bought with USD"), rec["funding"])
        self.assertIn("plan", rec["funding"])
        # the cross-checks, never priced: flat overnight rates (revision 1's method) 0.858881 = 79.16 pips under F; on the print 0.83725 x
        # 0.9413073853 / 0.9094909816 = 0.866539 (-2.58 pips); the FX desk quotes forwards up to 1 year only
        cc = rec["cross_checks"]
        self.assertEqual(("0.858881", "-79.16"), (cc["flat_overnight"]["outright"], cc["flat_overnight"]["diff_pips"]))
        self.assertEqual(("0.866539", "-2.58"), (cc["on_print"]["outright"], cc["on_print"]["diff_pips"]))
        self.assertIsNone(cc["fx_desk"]["outright"])
        self.assertIn("none beyond 1Y", cc["fx_desk"]["note"])

    def test_b_t11_the_pb_eurusd_2y_outright_is_on_the_ccs_curves(self):
        """OTC-04: from the spot date 2026-11-02 the PB EURUSD 2Y outright is 1.1158 x DF*_EUR / DF_USD = 1.153030 - the curves the cross-
        currency swap prices on (the designer's script: 2Y EURUSD outright from S 1.153030)."""
        p = swaps.preview(self.rt, PB, "FX_FORWARD", {"pair": "EURUSD", "side": "BUY", "notional": "2000000.00", "tenor": "2Y"})
        self.assertEqual(("1.1158", "1.153030", "2026-11-02", "2028-11-02", "CURVES"), (p["S"], p["forward"], p["spot_date"], p["value_date"], p["method"]))
        self.assertTrue(any(x.startswith("PROXY: Treasury par curve of 2026-10-27") for x in p["labels"]), p["labels"])


class TenorAndDateTests(_Base):
    """B-T2, B-T3, the 2-year limit, B-T10."""

    def test_b_t2_two_weeks_from_spot(self):
        # 2W = S + 14 days rolled following on TARGET2 + London + New York: 2026-11-02 + 14 = Mon 2026-11-16
        p = self.preview(tenor="2W")
        self.assertEqual(("2026-11-16", 14), (p["value_date"], p["days_from_spot"]))
        self.assertEqual(list(otc_options.OTC_FX_TENORS), otc_options.desk(self.rt, PB)["fx_forward_tenors"])

    def test_b_t3_holiday_value_dates_are_refused_naming_the_calendar(self):
        cases = (("EURGBP", "2027-03-29", "not a business day in EUR (TARGET2)"),                     # Easter Monday
                 ("EURUSD", "2026-11-11", "not a business day in USD (New York)"),                     # Veterans Day
                 ("EURGBP", "2026-11-26", "not a business day in USD (New York): the EUR is bought against USD for the same value date"))   # Thanksgiving
        for pair, d, why in cases:
            with self.subTest(pair=pair, d=d):
                with self.assertRaises(RejectError) as cm:
                    swaps.preview(self.rt, PB, "FX_FORWARD", {"pair": pair, "side": "SELL", "notional": "2000000.00", "expiry": d})
                self.assertEqual(("VALUE_DATE", 400, "value_date"), (cm.exception.code, cm.exception.http, cm.exception.details["field"]))
                self.assertIn(why, cm.exception.message)

    def test_beyond_two_years_is_not_offered(self):
        with self.assertRaises(RejectError) as cm:
            self.preview(tenor="3Y")
        self.assertEqual(("TENOR", "beyond 2 years the PB forward is not offered"), (cm.exception.code, cm.exception.message))
        with self.assertRaises(RejectError) as cm:
            self.preview(tenor="", expiry="2028-11-03")                                                  # the 2Y date is 2028-11-02
        self.assertEqual("TENOR", cm.exception.code)
        self.assertIn("2028-11-02", cm.exception.message)
        p = self.preview(tenor="", expiry="2028-11-02")                                                  # the 2Y date itself, as a date
        self.assertEqual(("0.866797", "date"), (p["forward"], p["tenor"]))
        self.assertEqual("2Y", self.preview(tenor="24M")["tenor"])                                       # 24M is an alias of 2Y

    def test_b_t10_a_legacy_pair_keeps_its_method_to_one_year(self):
        """1.5: USDJPY has no term curve: up to 1Y the pre-1.0.6.1 method (one overnight / policy rate per currency, simple interest), labelled
        LEGACY METHOD - LIMITED; 18M answers 400 TENOR."""
        p = swaps.preview(self.rt, PB, "FX_FORWARD", {"pair": "USDJPY", "side": "BUY", "notional": "1000000.00", "tenor": "1Y"})
        self.assertEqual("LEGACY", p["method"])
        self.assertIn(otc_options.LEGACY_LABEL, p["labels"])
        self.assertIn("LEGACY METHOD - LIMITED", p["model"])
        for tenor in ("18M", "2Y"):
            with self.assertRaises(RejectError) as cm:
                swaps.preview(self.rt, PB, "FX_FORWARD", {"pair": "USDJPY", "side": "BUY", "notional": "1000000.00", "tenor": tenor})
            self.assertEqual(("TENOR", 400), (cm.exception.code, cm.exception.http))
            self.assertIn("beyond 1 year USDJPY has no term curve in this build", cm.exception.message)
        d = otc_options.desk(self.rt, PB)
        self.assertEqual(("curves", "legacy"), (d["forward_methods"]["EURUSD"], d["forward_methods"]["USDJPY"]))
        self.assertEqual(list(fx.TENORS), d["fx_forward_tenors_legacy"])


class DeliveryTests(_Base):
    """B-T4: delivery at 00:30 ET on V (G12), with no price input."""

    def _deliver_at(self, cid):
        rt = self.rt
        rt.clock.set("2028-11-02T00:30:00-04:00")
        prints(rt, values=(("EURUSD", "1.1200"), ("GBPUSD", "1.3300")))
        with rt.db.reader() as conn:
            self.assertEqual([(cid, "2028-11-02")], otc_options.expiry_keys(conn, rt.clock.epoch()))
            self.assertEqual([], otc_options.expiry_keys(conn, epoch_of(date(2028, 11, 2), "00:29")))
        r = execute(rt, "otc.expire", {"contract_id": cid, "date": "2028-11-02", "key": f"{cid}:2028-11-02"}, f"job:OTC_EXPIRY:{cid}:2028-11-02", actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        return r.body

    def test_b_t4_delivery_with_the_eur_bought_first(self):
        rt = self.rt
        cid = self.book_b()
        # two business days before V (Tue 2028-10-31, the settlement-funding purchase): BUY EUR 2,000,000.00 / SELL USD spot, value 2028-11-02
        rt.clock.set("2028-10-31T10:00:00-04:00")
        prints(rt, values=(("EURUSD", "1.1200"), ("GBPUSD", "1.3300")))
        s = execute(rt, "fx.spot", {"account_id": PB, "buy_ccy": "EUR", "sell_ccy": "USD", "amount_c": 200000000, "amount_ccy": "EUR"}, "ui:sfi-test-0001")
        self.assertTrue(s.ok, s.body)
        sfi = s.body["deal"]["deal_id"]
        self.assertEqual("2028-11-02", s.body["deal"]["value_date"])
        out = self._deliver_at(cid)
        # 00:30 ET: SETTLE_DUE settles the purchase first (EUR 2,000,000.00 in), the delivery is posted at K, then SETTLE_DUE settles its two legs
        self.assertTrue(execute(rt, "fx.settle", {"deal_id": sfi}, f"job:SETTLE_DUE:{sfi}", actor="lifecycle").ok)
        self.assertEqual(("DONE", 0, 13389600), (out["status"], out["value_c"], out["im_released_c"]))
        deal = out["delivery"]["deal_id"]
        # the delivery deal at K: Dr 1250 GBP 1,733,248.00 / Cr 3900 and Dr 3900 / Cr 2350 EUR 2,000,000.00 (the cross's ledger rule)
        e = A.one(rt, "SELECT entry_id FROM fx_deals WHERE deal_id=?", (deal,))["entry_id"]
        lines = {(r["gl"], r["currency"]): r["c"] for r in A.q(rt, "SELECT gl, currency, SUM(amount_c) c FROM journal_lines WHERE entry_id=? AND gl IN ('1250','2350')"
                                                                     " GROUP BY gl, currency", (e,))}
        self.assertEqual({("1250", "GBP"): 173324800, ("2350", "EUR"): -200000000}, lines)      # a leg may be two lines at two 8-dp rates (fit_base)
        r = execute(rt, "fx.settle", {"deal_id": deal}, f"job:SETTLE_DUE:{deal}", actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("SETTLED", []), (r.body["status"], r.body["unfunded"]))
        # the EUR bought is delivered, the GBP received stays GBP cash; IM released (Dr 1010 / Cr 1330 133,896.00); the contract MATURED
        self.assertEqual((0, 173324800, 0), (bal(rt, PB, "1010", "EUR"), bal(rt, PB, "1010", "GBP"), bal(rt, PB, "1330", "USD", cid)))
        row = A.one(rt, "SELECT status, mark_c, im_c FROM otc_contracts WHERE contract_id=?", (cid,))
        self.assertEqual(("MATURED", 0, 0), (row["status"], row["mark_c"], row["im_c"]))
        done = A.one(rt, "SELECT status, note FROM jobs_done WHERE job='OTC_EXPIRY' AND key=?", (f"{cid}:2028-11-02",))
        self.assertEqual(("DONE", "FX_FORWARD delivered at K 0.866624: value 0.00"), (done["status"], done["note"]))
        assert_integrity(self, rt, "full")

    def test_b_t4_without_eur_the_delivery_waits_unfunded_never_converted(self):
        rt = self.rt
        cid = self.book_b(settlement_funding={"EUR": "NONE"})
        n_deals = len(A.q(rt, "SELECT 1 FROM fx_deals"))
        out = self._deliver_at(cid)
        deal = out["delivery"]["deal_id"]
        for attempt in (1, 2):                                       # retried: the EUR leg waits, nothing converts USD into EUR on its own
            r = execute(rt, "fx.settle", {"deal_id": deal}, f"job:SETTLE_DUE:{deal}:{attempt}", actor="lifecycle")
            self.assertTrue(r.ok, r.body)
            self.assertEqual(("PENDING", ["EUR"]), (r.body["status"], [u["currency"] for u in r.body["unfunded"]]))
        att = A.q(rt, "SELECT severity, code FROM attention WHERE code='SETTLEMENT_UNFUNDED' AND resolved_ts IS NULL")
        self.assertEqual([("ERROR", "SETTLEMENT_UNFUNDED")], [(a["severity"], a["code"]) for a in att])
        self.assertEqual(n_deals + 1, len(A.q(rt, "SELECT 1 FROM fx_deals")))      # only the delivery deal: no invented conversion
        self.assertEqual((0, 173324800), (bal(rt, PB, "1010", "EUR"), bal(rt, PB, "1010", "GBP")))
        self.assertEqual("MATURED", A.one(rt, "SELECT status FROM otc_contracts WHERE contract_id=?", (cid,))["status"])
        assert_integrity(self, rt, "full")


class TicketTests(_Base):
    """B-T5 (and G11): the delivery note is never empty; a value-date refusal marks the date field of the ticket."""

    def test_b_t5_the_delivery_note_and_the_date_field(self):
        p = self.preview()
        dc = p["delivery_check"]
        self.assertTrue(dc["note"].startswith("settlement funding ON: the EUR 2,000,000.00 delivered on 2028-11-02 is bought with USD"), dc)
        self.assertTrue(dc["covered_by_sfi"])
        off = self.preview(settlement_funding={"EUR": "NONE"})["delivery_check"]
        self.assertIn("EUR 2,000,000.00 due 2028-11-02", off["note"])
        self.assertIn("settlement funding OFF for EUR", off["note"])
        static = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(otc_options.__file__))), "static", "components")
        with open(os.path.join(static, "otcticket.js"), encoding="ascii") as fh:
            js = fh.read()
        self.assertIn("FIELD_ALIAS = { value_date: 'expiry'", js)              # the server's "value_date" refusal marks the date input
        self.assertIn("'18M'", js)
        self.assertIn("delivery_check", js)

    @staticmethod
    def _static(rel: str) -> str:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(otc_options.__file__))), "static", *rel.split("/"))
        with open(path, encoding="ascii") as fh:
            return fh.read()

    def test_v1061r2_03_the_sensitivity_rows_are_money_formatted(self):
        """V1061R2-03 (source check, runs without a browser): every sensitivity on the forward ticket goes through the money formatter -
        the rows were built by string concatenation ("EUR -1880945.13") - and the swap ticket's rates +1 bp figures too."""
        js = self._static("components/otcticket.js")
        self.assertIn("function amountText(x) { return T.fmt.money(x, 2); }", js)
        for raw, fmt in (("c + ' ' + sens.pv_delta[c]", "c + ' ' + amountText(sens.pv_delta[c])"), ("'USD ' + sens.rate01[c]", "'USD ' + amountText(sens.rate01[c])"),
                         ("'USD ' + sens.basis01[p]", "'USD ' + amountText(sens.basis01[p])"), ("'USD ' + sens.spread01[c]", "'USD ' + amountText(sens.spread01[c])"),
                         ("'USD ' + sens.points01]", "'USD ' + amountText(sens.points01)]")):
            self.assertNotIn(raw, js)
            self.assertIn(fmt, js)
        sw = self._static("components/swapticket.js")
        self.assertNotIn("' rates +1 bp ' + r01[c];", sw)
        self.assertIn("' rates +1 bp ' + T.fmt.money(r01[c], 2);", sw)

    def test_v1061r2_03_the_ticket_renders_the_served_preview_with_money_and_assumed_terms(self):
        """V1061R2-03 and the ticket half of V1061R2-01, in a real headless browser (``terminal_cdp``): core.js, keys.js and otcticket.js
        render the SERVED preview of the worked example (the API codec: cents as decimal strings, as the ticket receives them).

        Hand-worked (3.2, at T): PV deltas -2,000,000 x DF*_EUR(S, V) = EUR -1,882,614.77 and +2,000,000 x 0.866624 x DF*_GBP(S, V) = GBP
        1,576,373.42; rate01 EUR +420.66 / GBP -420.57 (the basis levels move the same curves: EUR/USD +420.66, GBP/USD -420.57); EUR OIS
        spread +423.92; points01 -242.34 per pip - each printed as money (grouped, 2 dp), never "EUR -1882614.77".  The dealt-rate line and the
        IM rule say the half-width and the 6 % are ASSUMED schedule terms; the labels section shows the record's labels.  Skipped without
        Edge / Chrome (the source check above still runs)."""
        from finsim.terminal.api import encode
        from tests.terminal_cdp import NO_BROWSER, Browser, find_browser
        exe = find_browser()
        if exe is None:
            self.skipTest(NO_BROWSER)
        served = json.loads(json.dumps(encode(self.preview())))
        self.assertEqual({"EUR": "-1882614.77", "GBP": "1576373.42"}, served["sensitivities"]["pv_delta"])        # what the ticket formats
        mount = ("(function () { var el = document.createElement('div'); el.id = 'tk'; document.body.appendChild(el);"
                 " T.api.post = function () { return Promise.resolve(window.__PV); };"
                 " T.components.otcticket.mount(el, { account_id: 'F1:PB_GS', mode: 'forwards', desk: null });"
                 " var tab = Array.prototype.filter.call(el.querySelectorAll('button[role=tab]'), function (n) { return n.textContent === 'FX forward'; })[0];"
                 " tab.click();"
                 " var btn = Array.prototype.filter.call(el.querySelectorAll('button'), function (n) { return n.textContent === 'Preview'; })[0];"
                 " btn.click(); return true; })()")
        read = ("(function () { var pv = document.querySelector('#tk .ot-preview');"
                " function pairs(kv) { var c = kv ? kv.children : [], o = [];"
                " for (var i = 0; i + 1 < c.length; i += 2) { o.push([c[i].textContent, c[i + 1].textContent]); } return o; }"
                " var h = Array.prototype.filter.call(pv.querySelectorAll('h2'), function (n) { return n.firstChild && n.firstChild.textContent === 'sensitivities'; })[0];"
                " return { sens: h ? pairs(h.parentNode.querySelector('.kv')) : null, head: pairs(pv.querySelector('.kv')),"
                " labels: Array.prototype.map.call(pv.querySelectorAll('.ot-labels .hint'), function (n) { return n.textContent; }), text: pv.innerText }; })()")
        b = Browser(exe, width=1440, height=900)
        try:
            sid = b.page()
            for rel in ("core.js", "keys.js", "components/otcticket.js"):
                b.js(sid, self._static(rel) + "\n;true")
            b.js(sid, "window.__PV = " + json.dumps(served) + "; true")
            self.assertTrue(b.js(sid, mount))
            b.wait(sid, "!!document.querySelector('#tk .ot-preview h2')", timeout=20)
            got = b.js(sid, read)
            errors = b.errors(sid)
        finally:
            b.close()
        self.assertEqual([["PV delta EUR", "EUR -1,882,614.77"], ["PV delta GBP", "GBP 1,576,373.42"], ["EUR rates +1 bp", "USD 420.66"],
                          ["GBP rates +1 bp", "USD -420.57"], ["EUR/USD basis +1 bp", "USD 420.66"], ["GBP/USD basis +1 bp", "USD -420.57"],
                          ["EUR OIS spread +1 bp", "USD 423.92"], ["forward points +1 pip", "USD -242.34"]], got["sens"])
        head = dict(got["head"])
        self.assertEqual("model 0.866797 . dealt 0.866624 (2.0000 bp = 0.000173 against you; " + HW_SOURCE + ")", head["forward"])
        self.assertEqual("6.00 % of the USD notional 2,231,600.00 = 133,896.00 (the prime broker's schedule, ASSUMED)", head["IM rule"])
        self.assertEqual(FWD_LABELS, got["labels"])
        for bad in ("undefined", "NaN", "[object Object]"):
            self.assertNotIn(bad, got["text"])
        self.assertEqual([], errors)


class HookTests(_Base):
    """K6: the scheduled flows and the off-ledger risk of the open forward (nothing once delivered)."""

    def test_scheduled_flows_and_risk_sensitivities(self):
        rt = self.rt
        cid = self.book_b()
        row = A.one(rt, "SELECT * FROM otc_contracts WHERE contract_id=?", (cid,))
        with rt.db.reader() as conn:
            flows = otc_options.scheduled_flows(conn, row)
        self.assertEqual([("2028-11-02", "GBP", 173324800, "DELIVERY"), ("2028-11-02", "EUR", -200000000, "DELIVERY")],
                         [(f["date"], f["ccy"], f["amount_c"], f["event"]) for f in flows])
        s = otc_options.risk_sensitivities(rt, row)
        # at T: PV deltas EUR -2,000,000 x 0.9413073853 = -1,882,614.77, GBP +1,733,248 x 0.9094909816 = +1,576,373.42; rate01 EUR +420.66 / GBP
        # -420.57; basis01 the same discount curves; EUR OIS spread +423.92; points01 -2,000,000 x 0.0001 x 0.9094909816 x 1.3323 = -242.34
        self.assertEqual({"EUR": -200000000, "GBP": 173324800}, s["fx_nominal"])
        self.assertEqual({"EUR": -188261477, "GBP": 157637342}, s["fx_delta"])
        self.assertEqual(({"EUR": 42066, "GBP": -42057}, {"EURUSD": 42066, "GBPUSD": -42057}, {"EUR": 42392}, -24234),
                         (s["rate01_c"], s["basis01_c"], s["spread01_c"], s["points01_c"]))
        # D9: the settlement-funding purchase trades two TARGET2 + New York business days before V (Thu 2028-11-02 -> Tue 2028-10-31); the
        # delivery runs at 00:30 ET on V
        ev = otc_options.next_events(rt, cid)
        self.assertEqual([("2028-10-31", "SETTLEMENT_FUNDING"), ("2028-11-02", "DELIVERY")], [(e["date"], e["event"]) for e in ev])
        self.assertTrue(ev[1]["detail"].startswith("00:30 ET: receive GBP 1,733,248.00, pay EUR 2,000,000.00 at 0.866624"), ev[1])
        d = otc_options.contract_detail(rt, cid)
        self.assertEqual(("CURVES", "PENDING - the simulated confirmation follows at 17:05 ET"), (d["method"], d["confirmation"]["words"]))
        self.assertIn("the Fund delivers EUR 2,000,000.00 on 2028-11-02", d["confirmation"]["settlement_instructions"][0])
        self.assertNotIn("dealer-confirmed", json.dumps(d))
        closed = dict(row, status="MATURED")
        self.assertIsNone(otc_options.risk_sensitivities(rt, closed))
        with rt.db.reader() as conn:
            self.assertEqual([], otc_options.scheduled_flows(conn, closed))

    def test_the_contract_dv01_is_the_quote_curve_rate01(self):
        """OTC-09: the contracts table and drawer show an FX forward's one "DV01" as its rate01 on the quote currency's curve and point to
        Risk > Currency risk (FX), so the ticket's ``dv01_c``, the contract's (``swaps.contract_view``, the positions' extra) and the Risk
        page's GBP rate01 are one number, re-priced without intermediate rounding.  At T, +1 bp on every GBP node moves DF*_GBP(2026-11-02,
        2028-11-02) by 0.9094909816 x (exp(-0.0001 x 731 / 365) - 1) = 0.9094909816 x -0.000200254 = -0.0001821291; the GBP leg is 2,000,000
        x 0.866624 = 1,733,248.00, so 1,733,248 x -0.0001821291 x 1.3323 USD/GBP = -420.5737 -> -420.57 (B-T8).
        The difference of two rounded marks is NOT the DV01: F = 0.837499 x 0.9413073853 / DF*_GBP goes 0.8667969 -> 0.8669705 (+0.0001736),
        at 6 dp 0.866797 -> 0.866971; the bumped mark -2,000,000 x (0.866971 - 0.866624) x 0.9093088525 x 1.3323 = -840.7617 -> -840.76,
        less mark0 -419.25 = -421.51 - the 6 dp rounding of F alone can move it by up to 2,000,000 x 0.0000005 x 0.9095 x 1.3323 = 1.21."""
        from finsim.terminal import risk
        rt = self.rt
        p = self.preview()
        cid = self.book_b()
        self.assertEqual(-42057, p["dv01_c"])                                       # the ticket
        self.assertEqual(-42057, otc_options.dv01_c(rt, cid))                       # the product hook
        self.assertEqual(-42057, swaps.dv01_c(rt, cid))                             # the dispatcher the positions read
        self.assertEqual(-42057, swaps.contract_view(rt, cid)["dv01_c"])            # the contracts table and drawer
        rates01 = {x["ccy"]: x["usd_per_bp_c"] for x in risk.report(rt, PB).fx_sensitivities["rates01"]}
        self.assertEqual(-42057, rates01["GBP"])                                    # Risk > Currency risk (FX)
        # the rounded marks' difference (the cause, kept as the mark's own arithmetic): -840.76 - (-419.25) = -421.51
        row = A.one(rt, "SELECT * FROM otc_contracts WHERE contract_id=?", (cid,))
        row = {k: row[k] for k in row.keys()}
        terms = json.loads(row["terms_json"])
        now = int(rt.clock.epoch())
        snap = otc_options._snap_for(rt, row, terms, None)
        with rt.db.reader() as conn:
            v0, _i = otc_options._value(rt, conn, snap, row, terms, now)
            v1, _j = otc_options._value(rt, conn, snap, row, terms, now, bump={"r_bp": 1.0})
        self.assertEqual((-41925, -84076, -42151), (v0, v1, v1 - v0))
        # a legacy pair keeps its own method's DV01 (1.5): USDJPY 1Y, the difference of its two simple-rate marks, a number (not the curves')
        jid = self.book_b("ui:pbfwd-jpy-0001", pair="USDJPY", side="BUY", notional="1000000.00", tenor="1Y", settlement_funding={})
        jrow = A.one(rt, "SELECT * FROM otc_contracts WHERE contract_id=?", (jid,))
        self.assertEqual("LEGACY", json.loads(jrow["terms_json"]).get("pricing_method") or "LEGACY")
        self.assertIsInstance(otc_options.dv01_c(rt, jid), int)


class IndependentRepriceTests(_Base):
    """FX-C-05 / K9 / 10.2: the booking program accepts a quote only when the independent re-pricer (package P-D's ``otcplan.reprice_forward``,
    stdlib Decimal, never importing the pricing code) re-derives every price from the stored record alone - so the record must carry every
    input it needs.  EURGBP 2Y (B-T1) and EURUSD 2Y (B-T11) re-derive exactly."""

    def test_the_record_re_derives_exactly(self):
        try:
            from finsim.terminal import otcplan
        except ImportError:
            self.skipTest("the booking program (package P-D) is not in this build")
        for terms in (dict(B_TERMS), {"pair": "EURUSD", "side": "BUY", "notional": "2000000.00", "tenor": "2Y"}):
            p = swaps.preview(self.rt, PB, "FX_FORWARD", terms)
            rec = json.loads(json.dumps(p["pricing_record"], default=str))         # the record as stored (JSON)
            try:
                r = otcplan.reprice_forward(rec)
            except Exception as e:   # noqa: BLE001 - a re-pricer still being built is not this package's failure
                if "not built" in str(e):
                    self.skipTest(str(e))
                raise
            self.assertEqual((p["forward"], p["strike"], rec["dfs"]["base"], rec["dfs"]["quote"], p["points"], p["im_c"], p["mark0_c"], p["notional_c"]),
                             (r["F"], r["K"], r["df_base"], r["df_quote"], r["points"], r["im_c"], r["mark0_c"], r["usd_notional_c"]), terms["pair"])


class RfqTests(_Base):
    """The RFQ path (C14): the dealer's quote carries the pricing record; the acceptance re-prices on the pinned prints and books the quoted K."""

    def test_the_quote_carries_the_record_and_books_its_strike(self):
        rt = self.rt
        r = execute(rt, "otc.rfq.request", {"account_id": PB, "kind": "FX_FORWARD", "terms": dict(B_TERMS)}, "ui:pbfwd-rfq-0001")
        self.assertTrue(r.ok, r.body)
        rid = r.body["rfq_id"]
        rt.lifecycle.tick(at=rt.clock.epoch() + 30, only="RFQ_DEALER")
        row = A.one(rt, "SELECT * FROM rfqs WHERE rfq_id=?", (rid,))
        self.assertEqual("QUOTED", row["status"], row["reason"])
        quote = json.loads(row["quote_json"])
        self.assertEqual(("0.866624", "0.866797"), (quote["compare"]["strike"], quote["preview"]["pricing_record"]["forward"]["outright"]))
        self.assertTrue(quote["words"].startswith("you buy GBP 1,733,248.00 / sell EUR 2,000,000.00 for value 2028-11-02 at 0.866624"), quote["words"])
        # V1061R2-01: the dealer's quote carries the complete labels (the half-width and IM ASSUMED) - what the booking program checks
        self.assertEqual((FWD_LABELS, IM_SOURCE), (quote["preview"]["pricing_record"]["labels"], quote["preview"]["pricing_record"]["im"]["source"]))
        rt.clock.set(rt.clock.epoch() + 40)
        prints(rt, values=(("EURUSD", "1.1170"), ("GBPUSD", "1.3330")))                        # the market moved since: the quote's prints are pinned
        a = execute(rt, "otc.rfq.accept", {"rfq_id": rid}, "ui:pbfwd-rfq-accept")
        self.assertTrue(a.ok, a.body)
        t = json.loads(A.one(rt, "SELECT terms_json FROM otc_contracts WHERE contract_id=?", (a.body["contract_id"],))["terms_json"])
        self.assertEqual(("0.866624", 173324800), (t["strike"], t["buy_c"]))
        # ... and so does the record stored by the acceptance (the owner's booking path: 10.2's accept)
        self.assertEqual((FWD_LABELS, IM_SOURCE, HW_SOURCE), (t["pricing_record"]["labels"], t["pricing_record"]["im"]["source"],
                                                              t["pricing_record"]["dealer"]["hw_source"]))
        assert_integrity(self, rt, "full")


class QuoteFundingGapTests(_Base):
    """D9 / P-D-R2 (integration): the quote prints its own settlement-funding gap before anything is booked - the quoted trade's flows go
    to ``ccyfunding.plan(extra=...)``; without them the plan saw only booked contracts and the first quote showed no gap.

    Worked: F1 Treasury keeps USD 1,925,382.48 (the plan's D9 balance) - the rest is moved to F1:US_EXCHANGE, which the instruction never
    sweeps - and F1:PB_GS holds no USD.  The forward sells EUR 2,000,000.00 for value 2028-11-02 with the instruction ON: the EUR is bought
    with USD on Tue 2028-10-31 (two TARGET2 + New York business days before), about 2,000,000.00 x 1.1158 = USD 2,231,600.00 at today's
    mid.  V1061-03: the quote's own IM, 6 % x 2,231,600.00 = 133,896.00, is posted (one Treasury sweep) at the booking and stays posted until
    the 00:30 delivery - after the purchase's USD leg settles - so the PB account and Treasury hold 1,925,382.48 - 133,896.00 =
    1,791,486.48 for it: short 2,231,600.00 - 1,791,486.48 = 440,113.52, the gap the plan shows once the contract is booked (the quote said
    306,217.52 before: it left its own IM out).  The GBP received is not USD and does not count."""

    def setUp(self):
        super().setUp()
        from finsim.terminal import ccyfunding
        ccyfunding.register(self.rt.bus)
        ccyfunding.bind(self.rt)
        self.rt.ccyfunding = ccyfunding
        have = bal(self.rt, "F1:TREASURY", "1010", "USD")
        r = execute(self.rt, "treasury.transfer", {"from_account": "F1:TREASURY", "to_account": "F1:US_EXCHANGE", "currency": "USD",
                                                    "amount_c": have - 192538248}, "ui:pbfwd-gap-move")
        self.assertTrue(r.ok, r.body)

    def test_the_forward_quote_shows_its_own_purchase_gap(self):
        p = self.preview()
        f = p["pricing_record"]["funding"]
        self.assertTrue(f["plan"]["available"], f)
        # available 1,925,382.48 - the quote's IM 133,896.00 = 1,791,486.48; short 2,231,600.00 - 1,791,486.48 = 440,113.52 (V1061-03)
        self.assertEqual([("2028-11-02", "2028-10-31", 200000000, 223160000, 179148648, 44011352)],
                         [(g["value_date"], g["funding_trade_date"], g["amount_c"], g["usd_needed_c"], g["usd_available_c"], g["gap_c"]) for g in f["gaps"]])
        self.assertIn("will hold about USD 1,791,486.48 - short about USD 440,113.52", f["gaps"][0]["text"])
        # nothing is booked by a quote: the plan of the booked contracts alone is still empty
        self.assertEqual([], self.rt.ccyfunding.plan(self.rt, PB)["purchases"])

    def test_the_booked_record_keeps_the_gap_once_and_the_plan_reads_the_contract(self):
        cid = self.book_b("ui:pbfwd-gap-book")
        rec = json.loads(A.one(self.rt, "SELECT terms_json FROM otc_contracts WHERE contract_id=?", (cid,))["terms_json"])["pricing_record"]
        # the record was built before the contract row and its IM existed: the same single purchase and gap as the quote, its own IM counted
        # once (V1061-03: 440,113.52, equal to the booked plan below)
        self.assertEqual([("2028-11-02", 44011352)], [(g["value_date"], g["gap_c"]) for g in rec["funding"]["gaps"]])
        # once booked, the plan reads the contract itself; the IM's Treasury sweep of 133,896.00 (6 % x 2,231,600.00) now lowers the USD:
        # short 2,231,600.00 - (1,925,382.48 - 133,896.00) = 2,231,600.00 - 1,791,486.48 = 440,113.52
        p = self.rt.ccyfunding.plan(self.rt, PB)
        self.assertEqual([("2028-11-02", 44011352)], [(g["value_date"], g["gap_c"]) for g in p["gaps"]])
        self.assertEqual([[cid]], [x["contracts"] for x in p["purchases"]])

    def test_the_swap_quote_plans_its_own_eur_obligations(self):
        """The float swap's quote passes its own flows too (start net USD -40.00, each coupon, the final exchange): every EUR purchase is
        covered by the same day's USD inflows (coupon k in USD > coupon k in EUR x 1.1158; the final EUR 2,000,000.00 + coupon 8 against the
        USD 2,231,600.00 + coupon 8 returned), so the plan answers with no gap - and it does answer (a failure would leave no plan)."""
        terms = {"pay_ccy": "EUR", "recv_ccy": "USD", "pay_notional": "2000000.00", "pay_index": "ESTR", "recv_index": "SOFR", "months": 24, "mtm_reset": False,
                 "start_conversion": True, "settlement_funding": {"EUR": "BUY_WITH_USD"}}
        p = swaps.preview(self.rt, PB, "XCCY", terms)
        fl = p["pricing_record"]["funding"]
        self.assertEqual(({"EUR": "BUY_WITH_USD"}, {"gaps": []}), (fl["instruction"], fl["plan"]))

    def test_v1061_03_the_swap_quote_counts_its_own_im_and_start_net(self):
        """V1061-03: the float swap's quote hands the plan its IM - 2 % x 2,231,600.00 = USD 44,632.00, posted at the booking on Thu 2026-10-29
        and held until the swap ends - and its start-date net, USD -40.00 on 2026-11-02 (the conversion's spread: 2,231,560.00 received for
        the EUR against 2,231,600.00 lent), both as USD outflows.  The first EUR purchase (coupon 1, value 2027-02-04: EUR 12,945.83 x 1.1158 =
        12,945.83 + 1,499.127114 = 14,444.957114 -> USD 14,444.96 at today's mid) then sees 1,925,382.48 - 44,632.00 - 40.00 + the same
        day's USD coupon 23,571.46 = 1,904,281.94 (1,948,913.94 with the IM left out), and the booking's record is built before its IM is
        posted, so it counts it once."""
        from finsim.terminal import ccyfunding
        terms = {"pay_ccy": "EUR", "recv_ccy": "USD", "pay_notional": "2000000.00", "pay_index": "ESTR", "recv_index": "SOFR", "months": 24, "mtm_reset": False,
                 "start_conversion": True, "settlement_funding": {"EUR": "BUY_WITH_USD"}}
        seen, orig = [], ccyfunding.plan

        def spy(services, account_id, snapshot=None, extra=None):
            seen.append(list(extra or []))
            return orig(services, account_id, snapshot, extra)
        ccyfunding.plan = spy
        try:
            swaps.preview(self.rt, PB, "XCCY", terms)
            quoted = seen[-1]
            early = [(f["date"], f["ccy"], f["amount_c"]) for f in quoted if f["ccy"] == "USD" and f["date"] < "2027-01-01"]
            self.assertEqual([("2026-10-29", "USD", -4463200), ("2026-11-02", "USD", -4000)], early)
            self.assertTrue(next(f for f in quoted if f["amount_c"] == -4463200)["what"].startswith("initial margin USD 44,632.00"))
            # the quote's plan, before anything is booked: the first purchase and the USD the PB account and Treasury hold before it
            first = orig(self.rt, PB, extra=quoted)["purchases"][0]
            self.assertEqual(("2027-02-04", 1294583, 1444496, 190428194), (first["value_date"], first["amount_c"], first["usd_at_mid_c"], first["usd_before_c"]))
            r = execute(self.rt, "otc.book", {"account_id": PB, "kind": "XCCY", "terms": terms}, "ui:v1061-03-ccs-book")
            self.assertTrue(r.ok, r.body)
            booked = seen[-1]
        finally:
            ccyfunding.plan = orig
        self.assertEqual(quoted, booked)                                  # the booking's record hands the plan the quote's own flows, IM once
        # once booked, the plan reads the contract and the ledger: Treasury 1,925,382.48 - 44,632.00 (the IM sweep) = 1,880,750.48; on 11-02 the
        # conversion's USD 2,231,560.00 in and the USD 2,231,600.00 lent (-40.00); coupon 1's USD 23,571.46 the same day as the purchase:
        # 1,880,750.48 - 40.00 + 23,571.46 = 1,904,281.94 - the number the quote gave
        first = orig(self.rt, PB)["purchases"][0]
        self.assertEqual(("2027-02-04", 190428194, [r.body["contract_id"]]), (first["value_date"], first["usd_before_c"], first["contracts"]))

    def test_v1061_03_the_swap_booking_counts_its_im_once(self):
        """V1061-03: the swap's booking builds its record's funding plan BEFORE it posts the IM (the plan reads the command's own uncommitted
        writes), so its own IM - handed over as a flow - is counted once.  Treasury is left with USD 60,000.00 (the rest moved to US Exchange):
        IM 44,632.00 + the start net 40.00 = 44,672.00 are fundable.  Counted once, the first EUR purchase (coupon 1, value 2027-02-04, USD
        14,444.96 at today's mid) sees 60,000.00 - 44,632.00 - 40.00 + the same day's USD coupon 23,571.46 = 38,899.46: no gap.  Counted twice
        (the plan built after the IM is on the ledger AND handed the IM as a flow, the order the booking avoids) it would see 60,000.00 -
        44,632.00 - 44,632.00 - 40.00 + 23,571.46 = -5,732.54: a gap of the whole 14,444.96 - which the stored record must not show."""
        from finsim.terminal import ccyfunding
        terms = {"pay_ccy": "EUR", "recv_ccy": "USD", "pay_notional": "2000000.00", "pay_index": "ESTR", "recv_index": "SOFR", "months": 24, "mtm_reset": False,
                 "start_conversion": True, "settlement_funding": {"EUR": "BUY_WITH_USD"}}
        r = execute(self.rt, "treasury.transfer", {"from_account": "F1:TREASURY", "to_account": "F1:US_EXCHANGE", "currency": "USD",
                                                    "amount_c": 192538248 - 6000000}, "ui:v1061-03-ccs-move")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(6000000, bal(self.rt, "F1:TREASURY", "1010", "USD"))
        seen, orig = [], ccyfunding.plan

        def spy(services, account_id, snapshot=None, extra=None):
            seen.append(list(extra or []))
            return orig(services, account_id, snapshot, extra)
        ccyfunding.plan = spy
        try:
            p = swaps.preview(self.rt, PB, "XCCY", terms)
            quoted = seen[-1]
        finally:
            ccyfunding.plan = orig
        self.assertEqual(({"gaps": []}, []), (p["pricing_record"]["funding"]["plan"], p["blockers"]))
        im = [f for f in quoted if f.get("event") == "IM"]
        self.assertEqual([("2026-10-29", "USD", -4463200)], [(f["date"], f["ccy"], f["amount_c"]) for f in im])
        once = orig(self.rt, PB, extra=quoted)["purchases"][0]
        self.assertEqual(("2027-02-04", 1444496, 3889946, 0), (once["value_date"], once["usd_at_mid_c"], once["usd_before_c"], once["gap_c"]))
        twice = orig(self.rt, PB, extra=quoted + im)["purchases"][0]
        self.assertEqual((-573254, 1444496), (twice["usd_before_c"], twice["gap_c"]))
        r = execute(self.rt, "otc.book", {"account_id": PB, "kind": "XCCY", "terms": terms}, "ui:v1061-03-ccs-book-once")
        self.assertTrue(r.ok, r.body)
        cid = r.body["contract_id"]
        rec = json.loads(A.one(self.rt, "SELECT terms_json FROM otc_contracts WHERE contract_id=?", (cid,))["terms_json"])["pricing_record"]
        self.assertEqual({"gaps": []}, rec["funding"]["plan"])                          # the IM once: no gap in the stored record
        self.assertEqual(6000000 - 4463200, bal(self.rt, "F1:TREASURY", "1010", "USD"))   # the IM's one Treasury sweep: 60,000.00 - 44,632.00
        first = orig(self.rt, PB)["purchases"][0]                                          # booked: the ledger's IM, once - the quote's numbers
        self.assertEqual(("2027-02-04", 1444496, 3889946, 0), (first["value_date"], first["usd_at_mid_c"], first["usd_before_c"], first["gap_c"]))

if __name__ == "__main__":
    unittest.main()
