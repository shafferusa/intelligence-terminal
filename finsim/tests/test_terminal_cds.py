"""CDX (CDS index) money tests: products/swaps_cds.py - the CDX delegate kind of the PB OTC swaps (the owner 2026-10-21: "Buy CDX.NA.IG 5Y
protection $3.000M notional", "Buy CDX.NA.HY 5Y protection $4.000M notional").  Every expected number is worked by hand in a comment from
the inputs; nothing is pasted from the code's output.  No network: FakeClock, a RateStore with the FRED proxy prints given directly, a
CurveStore with fixed discount factors, a temp database.

The pure money math (upfront for a given S / C / RPV01, the risky annuity, CS01, the rebate and the coupons, the schedule and the series)
runs everywhere.  The booked-contract tests need the hook edits that wire the CDX kind into the platform (db.OTC_KINDS, prime.OTC_LIVE,
swaps._module_for, lifecycle_jobs.ACCRUAL_PRODUCT); until they are applied those tests SKIP with that reason.

Hand-worked inputs used below
- Fixed discount factors 1/1.02^k at 0.5 k years, k = 1..12 (0.98039216 ... 0.78849318, rounded to 8 dp), the curve the swaps tests use.
- The trade date Wed 2026-10-21: the accrual start is the coupon date 20 Sep 2026 (a Sunday) rolled to Mon 2026-09-21; the step-in date is
  2026-10-22; the first coupon date 20 Dec 2026 (a Sunday) rolls to Mon 2026-12-21; the S47 5Y maturity is Sat 2031-12-20, its last coupon
  is paid Mon 2031-12-22 for 2031-09-22 .. 2031-12-20 inclusive (90 days).
"""
from __future__ import annotations

import json
import shutil
import tempfile
import types
import unittest
from datetime import date, timedelta
from decimal import Decimal

from tests.terminal_helpers import T1, assert_integrity, bal, deposit, execute, fund, make_runtime, pb
from finsim.terminal import config, db as _db, lifecycle, lifecycle_jobs, prime
from finsim.terminal.calendar import TradingCalendar
from finsim.terminal.products import swaps, swaps_cds as cds
from finsim.terminal.quotes.rates import CurveStore, RateStore

D = Decimal
PB_GS = pb("GS")
WED_14 = "2026-10-21T14:00:00-04:00"
DFS = {"0.5": "0.98039216", "1.0": "0.96116878", "1.5": "0.94232233", "2.0": "0.92384543", "2.5": "0.90573081", "3.0": "0.88797138", "3.5": "0.87056018",
       "4.0": "0.85349037", "4.5": "0.83675527", "5.0": "0.82034830", "5.5": "0.80426304", "6.0": "0.78849318"}
HOOKS = ("CDX" in getattr(_db, "OTC_KINDS", ()) and "CDX" in getattr(prime, "OTC_LIVE", ()) and "CDX" in getattr(swaps, "CDS_KINDS", ())
         and "CDX" in lifecycle_jobs.ACCRUAL_PRODUCT.get("swappay", ("", "", "", ()))[3])
SKIP_WHY = ("the CDX hook edits are not applied yet (db.OTC_KINDS, prime.OTC_LIVE, swaps.CDS_KINDS / _module_for, lifecycle_jobs.ACCRUAL_PRODUCT):"
            " the booked-contract tests run once they land")
IG_TERMS = {"index": "IG", "side": "BUY_PROTECTION", "notional": "3000000.00"}


def _cal():
    return TradingCalendar(config.holidays_path())


class CdxMathTests(unittest.TestCase):
    """Pure functions: no database, no hooks."""

    def test_upfront_for_a_given_spread_coupon_and_rpv01(self):
        # IG buyer: 3,000,000 x (78.5 - 100) bp x RPV01 4.6 = 3,000,000 x -0.00215 x 4.6 = -29,670.00 (the buyer RECEIVES 29,670.00)
        self.assertEqual(-2967000, cds.upfront_c(300000000, D("78.5"), D(100), D("4.6")))
        # HY buyer: 4,000,000 x (296 - 500) bp x 4.3 = 4,000,000 x -0.0204 x 4.3 = -350,880.00; upfront -8.772 % -> price 100 + 8.772 = 108.772
        self.assertEqual(-35088000, cds.upfront_c(400000000, D(296), D(500), D("4.3")))
        self.assertEqual(D("108.772"), D(100) - D(100) * (D(296) - D(500)) * D("0.0001") * D("4.3"))
        # a spread above the coupon: the buyer PAYS - 1,000,000 x (150 - 100) bp x 4.0 = 1,000,000 x 0.005 x 4 = 20,000.00
        self.assertEqual(2000000, cds.upfront_c(100000000, D(150), D(100), D(4)))

    def test_risky_annuity_upfront_and_cs01_by_hand(self):
        # v = 2026-10-21, step-in 10-22; periods 09-21 -> 12-21 (60 days left: 10-22 .. 12-20) and 12-21 -> 2027-03-22 (91 days); DF = 1 (no rates)
        # S = 60 bp, R = 40 %: lambda = 0.0060 / 0.6 = 0.01; Q(t) = exp(-0.01 x days / 365)
        #   Q(1) = 0.999972603115, Q(61) = 0.998330162855, Q(152) = 0.995844275460
        #   term 1 = 60/360 x (Q(1) + Q(61)) / 2 = 0.166666667 x 0.999151382985 = 0.166525230498
        #   term 2 = 91/360 x (Q(61) + Q(152)) / 2 = 0.252777778 x 0.997087219158 = 0.252041491509
        #   RPV01 = 0.418566722007
        periods = [(date(2026, 9, 21), date(2026, 12, 21), date(2026, 12, 21)), (date(2026, 12, 21), date(2027, 3, 22), date(2027, 3, 22))]
        one = lambda t: D(1)                                                          # noqa: E731
        rpv, rows = cds.rpv01(periods, date(2026, 10, 21), cds.hazard(D("0.006"), D("0.4")), one)
        self.assertEqual(D("0.418566722"), rpv.quantize(D("0.000000001")))
        self.assertEqual([60, 91], [r["days"] for r in rows])
        # the buyer's value on 1,000,000 at 60 bp vs the 100 bp coupon: (0.0060 - 0.0100) x 0.418566722007 x 1,000,000 = -1,674.266888 -> -1,674.27
        v60, _ = cds.value_c("BUY_PROTECTION", 100000000, D(60), D(100), D("0.4"), periods, date(2026, 10, 21), one)
        self.assertEqual(-167427, v60)
        # a seller holds the opposite: +1,674.27
        self.assertEqual(167427, cds.value_c("SELL_PROTECTION", 100000000, D(60), D(100), D("0.4"), periods, date(2026, 10, 21), one)[0])
        # CS01: at 61 bp lambda = 0.0061 / 0.6 = 0.0101666667 -> RPV01 0.418552116775; value (0.0061 - 0.0100) x 0.418552116775 x 1,000,000
        # = -1,632.353255 -> -1,632.35; CS01 = -1,632.35 - (-1,674.27) = +41.92 (bought protection gains when the spread widens)
        self.assertEqual(4192, cds.cs01_from("BUY_PROTECTION", 100000000, D(60), D(100), D("0.4"), periods, date(2026, 10, 21), one))
        self.assertEqual(-4192, cds.cs01_from("SELL_PROTECTION", 100000000, D(60), D(100), D("0.4"), periods, date(2026, 10, 21), one))

    def test_discount_factors_log_linear(self):
        # DF(0.25) between DF(0) = 1 and DF(0.5) = 0.98039216: exp(0.5 x ln 0.98039216) = sqrt(0.98039216) = 0.99014754456 -> 0.990147545
        df = cds.df_function({D("0.5"): D("0.98039216"), D("1.0"): D("0.96116878")})
        self.assertEqual(D("0.990147545"), df(D("0.25")).quantize(D("0.000000001")))
        self.assertEqual(D("0.96116878"), df(D(1)).quantize(D("0.00000001")))
        # beyond the last node: the last segment's forward, ln(0.96116878 / 0.98039216) / 0.5 per year: DF(1.5) = 0.96116878^2 / 0.98039216
        # = 0.92384542... / 0.98039216 = 0.94232232910 -> 0.94232233 (1/1.02^3, as the geometric curve says)
        self.assertEqual(D("0.94232233"), df(D("1.5")).quantize(D("0.00000001")))
        # +1 bp of the zero curve: DF x exp(-0.0001 x t): DF(1) = 0.96116878 x exp(-0.0001) = 0.96107267
        self.assertEqual(D("0.96107267"), cds.df_function({D("0.5"): D("0.98039216"), D("1.0"): D("0.96116878")}, 1)(D(1)).quantize(D("0.00000001")))

    def test_rebate_and_coupons(self):
        # the accrued rebate: trade 2026-10-21, accrual start 2026-09-21: 10-22 - 09-21 = 31 days (09-21 .. 10-21 inclusive)
        # IG 3,000,000 x 1 % x 31 / 360 = 2,583.333 -> 2,583.33; HY 4,000,000 x 5 % x 31 / 360 = 17,222.222 -> 17,222.22
        self.assertEqual(258333, cds.rebate_c(300000000, D(100), 31))
        self.assertEqual(1722222, cds.rebate_c(400000000, D(500), 31))
        # the full first coupon 09-21 -> 12-21 = 91 days: IG 3,000,000 x 1 % x 91 / 360 = 7,583.333 -> 7,583.33; HY 4,000,000 x 5 % x 91 / 360 = 50,555.56
        self.assertEqual(758333, cds.coupon_amount_c(300000000, D(100), date(2026, 9, 21), date(2026, 12, 21)))
        self.assertEqual(5055556, cds.coupon_amount_c(400000000, D(500), date(2026, 9, 21), date(2026, 12, 21)))
        # the last period 2031-09-22 .. 2031-12-20 inclusive (end 12-21): 90 days -> IG 7,500.00
        self.assertEqual(750000, cds.coupon_amount_c(300000000, D(100), date(2031, 9, 22), date(2031, 12, 21)))

    def test_series_schedule_and_dates(self):
        cal = _cal()
        otr = cds.on_the_run(cal, date(2026, 10, 21))
        self.assertEqual((47, date(2026, 9, 21), date(2031, 12, 20), date(2027, 3, 22)), (otr["series"], otr["roll_date"], otr["maturity"], otr["next_roll"]))
        # 20 March 2027 is a Saturday: S48 rolls Mon 2027-03-22 and its 5Y matures on 20 June 2032 (a March series -> 20 June)
        self.assertEqual((47, 48), (cds.on_the_run(cal, date(2027, 3, 19))["series"], cds.on_the_run(cal, date(2027, 3, 22))["series"]))
        self.assertEqual(date(2032, 6, 20), cds.on_the_run(cal, date(2027, 3, 22))["maturity"])
        self.assertEqual((date(2026, 9, 20), date(2026, 9, 21)), cds.accrual_start(cal, date(2026, 10, 21)))
        ps = cds.coupon_periods(cal, date(2026, 10, 21), date(2031, 12, 20))
        # 2026-12-21 .. 2031-12-22: 21 coupons (Dec 26; Mar / Jun / Sep / Dec 27 .. 31 = 20)
        self.assertEqual(21, len(ps))
        self.assertEqual((date(2026, 9, 21), date(2026, 12, 21), date(2026, 12, 21)), ps[0])
        self.assertEqual((date(2027, 3, 22), date(2027, 6, 21), date(2027, 6, 21)), ps[2])          # 20 Mar 27 Sat -> 22nd, 20 Jun 27 Sun -> 21st
        self.assertEqual((date(2031, 9, 22), date(2031, 12, 21), date(2031, 12, 22)), ps[-1])       # last period includes the maturity; paid Mon 12-22
        # a trade ON a coupon date accrues from that date: rebate 1 day (the trade date itself)
        self.assertEqual(date(2026, 12, 21), cds.accrual_start(cal, date(2026, 12, 21))[1])

    def test_terms_parsing(self):
        t = cds.parse_terms({"index": "CDX.NA.HY", "side": "buy", "notional": "4,000,000.00"})
        self.assertEqual(("HY", "BUY_PROTECTION", 400000000, "5Y"), (t["index"], t["side"], t["notional_c"], t["tenor"]))
        for bad, field in (({"index": "XO", "side": "BUY", "notional": "1000000"}, "index"), ({"index": "IG", "side": "LONG", "notional": "1000000"}, "side"),
                           ({"index": "IG", "side": "BUY", "notional": "1000000.001"}, "notional"), ({"index": "IG", "side": "BUY", "notional": "5000"}, "notional"),
                           ({"index": "IG", "side": "BUY", "notional": "1000000", "tenor": "3Y"}, "tenor")):
            with self.assertRaises(Exception) as cm:
                cds.parse_terms(bad)
            self.assertEqual(field, (getattr(cm.exception, "details", None) or {}).get("field"), bad)


def _h_open_pb_unchecked(ctx, payload):
    return prime.open_pb(ctx, payload["lifetime_id"], payload["broker"], check_eligibility=False)


@unittest.skipUnless(HOOKS, SKIP_WHY)
class CdxBookedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-cdx-")
        rt = self.rt = make_runtime(self.tmp, at=WED_14, books="test")
        prime.register(rt.bus)
        prime.bind(rt)
        rt.rates = RateStore()
        rt.rates.put("SOFR", [(date(2026, 10, 19), D("3.87"))], persist=False)
        rt.rates.put("BAMLC0A0CM", [(date(2026, 10, 19), D("0.79")), (date(2026, 10, 20), D("0.78"))], persist=False)     # IG proxy 78 bp as of 10-20
        rt.rates.put("BAMLH0A0HYM2", [(date(2026, 10, 20), D("2.94"))], persist=False)                                     # HY proxy 294 bp
        rt.curve = CurveStore()
        rt.curve.set_fixed_dfs(DFS, date(2026, 10, 20))
        swaps.register(rt.bus, rt)
        rt.swaps = swaps
        cds.register(rt.bus, rt)
        cds.bind(rt)
        rt.swaps_cds = cds
        rt.providers = types.SimpleNamespace(yahoo=rt.fp)
        lifecycle.register(rt.bus, rt)
        rt.lifecycle = lifecycle.LifecycleScheduler(rt.bus, rt.db, rt, rt.clock, rt.log)
        self.assertTrue(deposit(rt, 1510000000).ok)                  # 10,000,000 + 15,100,000 = 25,100,000.00: past the PB / OTC threshold
        r = execute(rt, "account.open_pb", {"lifetime_id": T1, "broker": "GS"})
        self.assertTrue(r.ok, r.body)
        self.assertTrue(fund(rt, PB_GS, 50000000).ok)                 # 500,000.00 settled in T1:PB_GS

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def q(self, sql, params=()):
        with self.rt.db.reader() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def lines(self, idem):
        return [(r["gl"], r["amount_c"]) for r in self.q("SELECT l.gl, l.amount_c FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id WHERE e.idem_key=?"
                                                           " ORDER BY l.line_no", (idem,))]

    def book(self, terms=None, rid="ui:cdx-book-0001"):
        r = execute(self.rt, "otc.book", {"account_id": PB_GS, "kind": "CDX", "terms": dict(terms or IG_TERMS)}, rid)
        self.assertTrue(r.ok, r.body)
        return r.body

    def vm(self, cid, ds, suffix=""):
        return execute(self.rt, "swap.vm", {"contract_id": cid, "date": ds, "key": f"{cid}:{ds}"}, f"job:SWAP_VM:{cid}:{ds}{suffix}", actor="lifecycle")

    def test_book_ig_buy_protection_ledger(self):
        rt = self.rt
        p = swaps.preview(rt, PB_GS, "CDX", IG_TERMS)
        # mid = proxy 0.78 % = 78 bp + basis 0; dealt = 78 + 0.50 bp (seed half-spread x the GS multiplier 1) = 78.5 bp
        self.assertEqual(("78.0000", "78.5000", "2026-09-21", "2026-10-22", 47, "2031-12-20"),
                         (p["mid_spread_bp"], p["deal_spread_bp"], p["accrual_start"], p["initial_payment"]["settle_date"], p["series"], p["maturity_date"]))
        # IM = 2.00 % x 3,000,000 = 60,000.00; rebate 31 days = 2,583.33 (test_rebate_and_coupons)
        self.assertEqual((6000000, 31, 258333), (p["im_c"], p["accrued_rebate"]["days"], p["accrued_rebate"]["amount_c"]))
        # the upfront the BUYER pays = to_c(3,000,000 x (78.5 - 100) bp x RPV01 at 78.5 bp): negative (received) because 78.5 < 100
        rpv = D(p["rpv01_deal"])
        self.assertLess(p["upfront_c"], 0)
        self.assertLessEqual(abs(p["upfront_c"] - cds.upfront_c(300000000, D("78.5"), D(100), rpv)), 1)         # RPV01 shown to 1e-10
        body = self.book()
        cid = body["contract_id"]
        up, reb = body["initial_payment"]["upfront_c"], body["initial_payment"]["rebate_c"]
        self.assertEqual((p["upfront_c"], 258333), (up, reb))
        # IM: Dr 1330 / Cr 1010 60,000.00
        self.assertEqual([("1330", 6000000), ("1010", -6000000)], self.lines(f"im:{cid}:open"))
        # the initial payment entry: Cr 2345 |upfront| (the upfront received is the contract's COST BASIS, FINSIM1 C11 / PORTFOLIO-08 - never a day-one
        # realised gain), Cr 2340 2,583.33 (the pre-trade part of the full first coupon), Dr 1200 |upfront| + 2,583.33 receivable on 10-22
        net = -up + reb
        self.assertEqual([("2345", up), ("2340", -258333), ("1200", net)], self.lines(f"otc:{cid}:upfront"))
        # C9c: the position carries MV = mark0 against the cost basis 1335 - 2345 = upfront (negative: received), so unrealised = mark0 - upfront = the cost
        # of dealing and nothing is realised; the upfront sits on 2345 for the contract
        self.assertEqual((-up, 0), (bal(rt, PB_GS, "2345", "USD", cid), bal(rt, PB_GS, "4450", "USD", cid)))
        pos = rt.accounting.position(PB_GS, cid)
        self.assertEqual((p["mark0_c"] - up, 0), (pos.unrealised_c, rt.accounting.nav(PB_GS).realised_c))
        self.assertEqual(p["cost_of_dealing_c"], pos.unrealised_c)
        s = self.q("SELECT * FROM settlements WHERE leg='UPFRONT'")
        self.assertEqual([(PB_GS, net, "1200", "2026-10-22", "PENDING")], [(x["account_id"], x["amount_c"], x["gl"], x["due_date"], x["status"]) for x in s])
        # the premium leg starts at the accrual start with the rebate as its cumulative; the seller's kind is marked done through the maturity
        st = {r["kind"]: r for r in self.q("SELECT * FROM accrual_state WHERE ref_id=?", (cid,))}
        self.assertEqual(("2026-09-21", "2026-10-21", 258333, "0.01", 300000000), (st["swappay"]["segment_start"], st["swappay"]["accrued_through"], st["swappay"]["cum_c"],
                                                                                    st["swappay"]["rate"], st["swappay"]["base_c"]))
        self.assertEqual("2031-12-20", st["swaprcv"]["accrued_through"])
        row = self.q("SELECT * FROM otc_contracts WHERE contract_id=?", (cid,))[0]
        self.assertEqual(("CDX", "OPEN", 300000000, 6000000, "2026-10-21", "2031-12-20"), (row["kind"], row["status"], row["notional_c"], row["im_c"], row["start_date"], row["maturity_date"]))
        # NAV: the day-one result is the cost of dealing = mark0 - the deal value (half-spread 0.5 bp x about the CS01)
        self.assertEqual(p["mark0_c"], row["mark_c"])
        self.assertLess(p["cost_of_dealing_c"], 0)
        assert_integrity(self, rt)
        # the contracts view: the side as a sentence with the notional and CS01, the kind label, the coupon events
        v = swaps.contract_view(rt, cid)
        self.assertEqual(("CDS index (CDX)", "BUY_PROTECTION"), (v["kind_label"], v["side"]))
        self.assertTrue(v["side_text"].startswith("buy protection on CDX.NA.IG S47 5Y (short credit): USD 3,000,000.00 notional; CS01 +"), v["side_text"])
        self.assertEqual(("2026-12-21", 758333), (v["next_events"][0]["date"], v["next_events"][0]["legs"][0]["amount_c"]))

    def test_vm_two_days_against_the_model(self):
        rt = self.rt
        cid = self.book()["contract_id"]
        rt.clock.set("2026-10-21T17:06:00-04:00")
        r = self.vm(cid, "2026-10-21")
        # FRED publishes the 10-21 OAS on 10-22: the VM of 10-21 waits (CLOSE_PENDING), never a value of another day
        self.assertEqual(("SKIPPED", "CLOSE_PENDING"), (r.body["status"], r.body["code"]))
        rt.rates.put("BAMLC0A0CM", [(date(2026, 10, 21), D("0.80"))], persist=False)
        rt.clock.set("2026-10-22T09:00:00-04:00")
        r = self.vm(cid, "2026-10-21", ":r1")
        self.assertEqual("DONE", r.body["status"], r.body)
        periods = cds.coupon_periods(rt.cal, date(2026, 10, 21), date(2031, 12, 20))
        dfs, _ = swaps.dfs_for(rt, 12)
        # the mark of 10-21 = the buyer's value at 80 bp valued as of 10-21; the first VM moves it all (reference 0): CTM, a negative mark -> posted
        m1, _ = cds.value_c("BUY_PROTECTION", 300000000, D(80), D(100), D("0.4"), periods, date(2026, 10, 21), cds.df_function(dfs))
        self.assertEqual((m1, m1), (r.body["mark_c"], r.body["delta_c"]))
        self.assertEqual([("1320", -m1), ("1010", m1)], self.lines(f"swapvm:{cid}:2026-10-21"))
        # day two: 81 bp as of 10-22 -> delta = mark(81 bp, 10-22) - mark(10-21) > 0 (spreads widened: bought protection gained); VM posted comes back
        rt.rates.put("BAMLC0A0CM", [(date(2026, 10, 22), D("0.81"))], persist=False)
        rt.clock.set("2026-10-23T09:00:00-04:00")
        r = self.vm(cid, "2026-10-22")
        m2, _ = cds.value_c("BUY_PROTECTION", 300000000, D(81), D(100), D("0.4"), periods, date(2026, 10, 22), cds.df_function(dfs))
        self.assertEqual((m2, m2 - m1), (r.body["mark_c"], r.body["delta_c"]))
        self.assertGreater(m2 - m1, 0)
        self.assertEqual([("1010", m2 - m1), ("1320", -(m2 - m1))], self.lines(f"swapvm:{cid}:2026-10-22"))
        self.assertEqual(-m2, bal(rt, PB_GS, "1320", "USD", cid))
        # a weekend date values at Friday's close: Sat 10-24 with no new print and the same curve -> NO_CHANGE once 10-23 is posted
        rt.rates.put("BAMLC0A0CM", [(date(2026, 10, 23), D("0.81"))], persist=False)
        rt.clock.set("2026-10-25T09:00:00-04:00")
        self.assertEqual("DONE", self.vm(cid, "2026-10-23").body["status"])
        self.assertEqual("NO_CHANGE", self.vm(cid, "2026-10-24").body["code"])
        assert_integrity(self, rt)

    def test_accruals_coupon_and_termination(self):
        rt = self.rt
        body = self.book()
        cid = body["contract_id"]
        # SETTLE_DUE on 10-22 (T+1): the initial payment receivable comes in - Dr 1010 / Cr 1200 |upfront| + 2,583.33
        sid, net = body["initial_payment"]["settle_id"], body["initial_payment"]["amount_c"]
        rt.clock.set("2026-10-22T09:00:00-04:00")
        r = execute(rt, "lifecycle.settle", {"key": sid, "settle_id": sid}, f"job:SETTLE_DUE:{sid}", actor="lifecycle")
        self.assertTrue(r.ok and r.body["status"] == "DONE", r.body)
        self.assertEqual([("1010", net), ("1200", -net)], self.lines(f"settle:{sid}"))
        self.assertEqual(0, bal(rt, PB_GS, "1200"))
        # ACCRUE_DAILY nights 10-22 .. 12-20 (60 nights) through the scheduler's handler: night 10-22 = to_c(3,000,000 x 1 % x 32 / 360) - 2,583.33
        # = 2,666.67 - 2,583.33 = 83.34; the last night (12-20) trues up to the full coupon 7,583.33
        rt.clock.set("2026-12-21T00:50:00-05:00")
        first = date(2026, 10, 22)
        for i in range(60):
            night = (first + timedelta(days=i)).isoformat()
            key = f"swappay:{cid}:{night}"
            r = execute(rt, "lifecycle.accrue", {"key": key, "kind": "swappay", "ref": cid, "night": night}, f"job:ACCRUE_DAILY:{key}", actor="lifecycle")
            self.assertTrue(r.ok and r.body["status"] == "DONE", (night, r.body))
        self.assertEqual([("4453", 8334), ("2340", -8334)], self.lines(f"accr:swappay:{cid}:2026-10-22"))           # 4453: CDS premium (financing)
        self.assertEqual(758333, bal(rt, PB_GS, "2340", "USD", cid))
        # the coupon of 12-21: Dr 2340 / Cr 1010 7,583.33 (the full first coupon; the rebate received at the trade refunded 2,583.33 of it)
        r = execute(rt, "cdx.coupon", {"contract_id": cid, "date": "2026-12-21", "key": f"{cid}:2026-12-21"}, f"job:CDX_COUPON:{cid}:2026-12-21", actor="lifecycle")
        self.assertEqual(("DONE", 758333), (r.body["status"], r.body["amount_c"]), r.body)
        self.assertEqual([("2340", 758333), ("1010", -758333)], self.lines(f"cdxcpn:{cid}:2026-12-21"))
        self.assertEqual((0, -758333), (bal(rt, PB_GS, "2340", "USD", cid), self.q("SELECT amount_c FROM otc_resets WHERE contract_id=? AND kind='PAY'", (cid,))[0]["amount_c"]))
        # the premium expense booked = 61 nights (10-22 .. 12-21 is 60 nights + the rebate days netted): 7,583.33 - 2,583.33 = 5,000.00 over 60 nights
        self.assertEqual(500000, sum(a for gl, a in (x for n in range(60) for x in self.lines(f"accr:swappay:{cid}:{(first + timedelta(days=n)).isoformat()}")) if gl == "4453"))
        # terminate 12-21 10:00 at mid - 0.50 bp (the dealer buys the protection back below mid): night 12-21 accrued first (3,000,000 x 1 % / 360 = 83.33),
        # then the value, the VM unwind (none posted here), the accrued premium 83.33 paid and the IM 60,000.00 released; FRED printed 0.78 % for Fri 12-18
        rt.rates.put("BAMLC0A0CM", [(date(2026, 12, 18), D("0.78"))], persist=False)
        rt.clock.set("2026-12-21T10:00:00-05:00")
        r = execute(rt, "otc.terminate", {"contract_id": cid}, "ui:cdx-term-0001")
        self.assertTrue(r.ok, r.body)
        t = r.body
        periods = cds.coupon_periods(rt.cal, date(2026, 10, 21), date(2031, 12, 20))
        dfs, _ = swaps.dfs_for(rt, 12)
        val, _ = cds.value_c("BUY_PROTECTION", 300000000, D("77.5"), D(100), D("0.4"), periods, date(2026, 12, 21), cds.df_function(dfs))
        self.assertEqual((val, 8333, 6000000, "2026-12-21"), (t["termination_value_c"], t["accrued_pay_c"], t["im_released_c"], t["accrued_through"]))
        # cash = the value (negative: the buyer pays to close a contract worth less than nothing at 77.5 bp) - the accrued 83.33
        self.assertEqual(val - 8333, t["cash_c"])
        for gl in ("1330", "1320", "2330", "2340", "1225", "2345", "1335"):
            self.assertEqual(0, bal(rt, PB_GS, gl, "USD", cid), gl)
        # the contract cost (the upfront received, 2345) is released into the swap result with the close-out: Dr 2345 |upfront| / Cr 4450 |upfront|;
        # 4450 = the termination value + the released upfront (realised), the premium on 4453
        self.assertEqual(-body["initial_payment"]["upfront_c"], t["cost_released_c"])
        self.assertIn(("2345", -body["initial_payment"]["upfront_c"]), self.lines(f"otc:{cid}:terminate"))
        self.assertEqual(val - body["initial_payment"]["upfront_c"], bal(rt, PB_GS, "4450", "USD", cid))
        self.assertEqual([], self.q("SELECT * FROM accrual_state WHERE ref_id=?", (cid,)))
        assert_integrity(self, rt)

    def test_seller_mirror_and_payable(self):
        rt = self.rt
        hy = {"index": "HY", "side": "SELL_PROTECTION", "notional": "1000000.00"}
        p = swaps.preview(rt, PB_GS, "CDX", hy)
        # the seller deals at mid - half-spread = 294 - 2 = 292 bp: below the 500 bp coupon the BUYER receives the upfront, so the seller pays it, and
        # pays the rebate 1,000,000 x 5 % x 31 / 360 = 4,305.56 too: one payable on 10-22; IM 6 % x 1,000,000 = 60,000.00
        self.assertEqual(("292.0000", 430556, 6000000), (p["deal_spread_bp"], p["accrued_rebate"]["amount_c"], p["im_c"]))
        self.assertGreater(p["upfront_c"], 0)
        body = self.book(hy, "ui:cdx-book-hy-sell")
        cid = body["contract_id"]
        up = body["initial_payment"]["upfront_c"]
        # the seller PAYS the upfront: Dr 1335 (the contract cost paid, C11), Dr 1225 the rebate paid, Cr 2100 the payable on 10-22
        self.assertEqual([("1335", up), ("1225", 430556), ("2100", -(up + 430556))], self.lines(f"otc:{cid}:upfront"))
        s = self.q("SELECT amount_c, gl FROM settlements WHERE leg='UPFRONT'")
        self.assertEqual([(-(up + 430556), "2100")], [(x["amount_c"], x["gl"]) for x in s])
        st = {r["kind"]: r for r in self.q("SELECT * FROM accrual_state WHERE ref_id=?", (cid,))}
        self.assertEqual((430556, "0.05", "2031-12-20"), (st["swaprcv"]["cum_c"], st["swaprcv"]["rate"], st["swappay"]["accrued_through"]))
        row = self.q("SELECT terms_json FROM otc_contracts WHERE contract_id=?", (cid,))[0]
        self.assertIs(True, json.loads(row["terms_json"])["receiver"])                            # long credit: a positive position quantity
        assert_integrity(self, rt)
        # terminated the same day: the upfront the seller PAID (the contract cost on 1335) is released into the swap result with the close-out -
        # Dr 4450 |upfront| / Cr 1335 |upfront| - so 4450 = the termination value - the upfront paid and 1335 is 0 (C11 / PORTFOLIO-08)
        r = execute(rt, "otc.terminate", {"contract_id": cid}, "ui:cdx-term-hy-sell")
        self.assertTrue(r.ok, r.body)
        t = r.body
        self.assertEqual((-up, 0, 0), (t["cost_released_c"], bal(rt, PB_GS, "1335", "USD", cid), bal(rt, PB_GS, "2345", "USD", cid)))
        self.assertIn(("1335", -up), self.lines(f"otc:{cid}:terminate"))
        self.assertIn(("4450", up), self.lines(f"otc:{cid}:terminate"))
        self.assertEqual(t["termination_value_c"] - up, bal(rt, PB_GS, "4450", "USD", cid))
        self.assertEqual(t["realised_c"], t["termination_value_c"] - up)
        assert_integrity(self, rt)

    def test_no_price_without_a_fresh_proxy(self):
        rt = self.rt
        # the latest HY print is 10-20; on Wed 10-28 it is 6 business days old (> 3): no CDX price, the reason says so
        rt.clock.set("2026-10-28T10:00:00-04:00")
        with self.assertRaises(Exception) as cm:
            swaps.preview(rt, PB_GS, "CDX", {"index": "HY", "side": "BUY_PROTECTION", "notional": "4000000.00"})
        self.assertEqual("PROXY_STALE", getattr(cm.exception, "code", None))
        self.assertIn("BAMLH0A0HYM2", cm.exception.message)
        rt.rates = RateStore()                                                                    # nothing observed at all
        with self.assertRaises(Exception) as cm:
            swaps.preview(rt, PB_GS, "CDX", IG_TERMS)
        self.assertEqual("PROXY_UNAVAILABLE", getattr(cm.exception, "code", None))

    def test_editable_basis_half_spread_and_im(self):
        rt = self.rt
        r = execute(rt, "cdx.terms", {"account_id": PB_GS, "index": "IG", "basis_bp": "-20", "half_spread_bp": "0.25", "im_pct": "1.5 %"})
        self.assertTrue(r.ok, r.body)
        self.assertEqual({"basis_bp": "-20", "half_spread_bp": "0.25", "im_pct": "0.015"}, r.body["after"])
        p = swaps.preview(rt, PB_GS, "CDX", IG_TERMS)
        # mid = 78 - 20 = 58 bp; dealt 58.25 bp; IM 1.5 % x 3,000,000 = 45,000.00
        self.assertEqual(("58.0000", "58.2500", 4500000), (p["mid_spread_bp"], p["deal_spread_bp"], p["im_c"]))
        r = execute(rt, "cdx.terms", {"account_id": PB_GS, "index": "IG", "basis_bp": "-20"})
        self.assertEqual("NOTHING_TO_UPDATE", r.body["error"]["code"])
        r = execute(rt, "cdx.terms", {"account_id": PB_GS, "index": "IG", "im_pct": "75 %"})
        self.assertEqual("TERMS_VALUE", r.body["error"]["code"])


if __name__ == "__main__":
    unittest.main()
