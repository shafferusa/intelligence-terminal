"""WP-17 PB OTC swap tests (TERMINAL_DESIGN.md §13.11, §11.2 SWAP_VM / SWAP_FIX / SWAP_PAY, §11.3, §5.4, §19.15; TERMINAL_WORKPLAN.md
WP-17; the owner amendments "variation margin on swaps too" and "swap variation margin AND resets").  Every expected number is worked by
hand in a comment from the inputs; nothing is pasted from the code's output.  No network: FakeClock, FixedMarks, a CurveStore with the
§19.15 FixedCurve discount factors given directly, a RateStore with SOFR prints, a temp database.  The lifecycle scheduler is driven by
``tick(only=...)`` for the wiring checks; the long accrual catch-ups drive the ACCRUE_DAILY handler directly with the scheduler's own
request ids.

The §19.15 account numbers are per account (F1:PB_GS in the design); most tests run them in the Test Book's T1:PB_GS (a 15,100,000.00
deposit takes T1 past the 25,000,000.00 PB/OTC threshold with 100,000.00 to spare, so the cost of dealing never drops the lifetime below
it: ``account.open_pb`` records no agreements, WP-23 does); the F1 / P1 cases run on ``books="real"``.

Hand-worked §19.15 figures (flat 4.00 %): df 0.98039216 / 0.96116878 / 0.94232233 / 0.92384543, Σ = 3.80772870, annuity = 1.90386435;
par = 2 × (1 − 0.92384543) / 3.80772870 = 0.15230914 / 3.80772870 = 0.0399999979 → 0.04000 at the 0.001 % dealing precision; dealt
(pay fixed) = 0.04000 + 0.00010 = 0.04010; mark0 = 10,000,000 × (0.04000 − 0.04010) × 1.90386435 = −1,903.86435 → −1,903.86; DV01 (the
PV change for +1 bp, + for a fixed payer) = 10,000,000 × 0.0001 × 1.90386435 = 1,903.86435 → 1,903.86; IM = |DV01| × 50 = 95,193.00.
Day 2 (flat 4.10 %, the design's factors as given): df 0.97991181 / 0.96022716 / 0.94093744 / 0.92203474, Σ = 3.80311115, annuity =
1.901555575; par = 2 × 0.07796526 / 3.80311115 = 0.15593052 / 3.80311115 = 0.041000779 → 0.04100; mark = 10,000,000 × (0.04100 − 0.04010)
× 1.901555575 = 9,000 × 1.901555575 = 17,114.000175 → 17,114.00.  (The design's day-2 factors differ from 1/1.0205^k in the 7th decimal;
the pinned 17,114.00 follows the factors as the design gives them, through the same formula and the 0.1 bp par precision.)
"""
from __future__ import annotations

import http.client
import json
import os
import shutil
import tempfile
import types
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal

from tests.terminal_helpers import T1, assert_integrity, bal, deposit, execute, fund, make_runtime, pb, snapshot
from tests.test_terminal_static import js_balance
from tests import terminal_fixture_server as fx
from finsim.terminal import api as _api, lifecycle, prime, risk
from finsim.terminal.commands import ConflictError, RejectError
from finsim.terminal.products import swaps
from finsim.terminal.quotes import MarkView
from finsim.terminal.quotes.rates import CurveStore, RateStore

D = Decimal
PB_GS = pb("GS")
SWP1 = "SWP-000001"
TUE_14 = "2026-10-20T14:00:00-04:00"
DAY1_DFS = {"0.5": "0.98039216", "1.0": "0.96116878", "1.5": "0.94232233", "2.0": "0.92384543"}
DAY2_DFS = {"0.5": "0.97991181", "1.0": "0.96022716", "1.5": "0.94093744", "2.0": "0.92203474"}
TERMS = {"notional": "10000000.00", "currency": "USD", "pay_fixed": True, "tenor_months": 24, "fixed_rate": None, "fixed_basis": "30/360", "fixed_months": 6,
         "float_index": "SOFR", "float_basis": "ACT/360", "float_months": 6, "fixing": "TERM"}
TERMS6 = dict(TERMS, tenor_months=6)                                                  # one semi-annual period: the maturity case
TERMS_CA = dict(TERMS, tenor_months=6, float_months=1, fixing="COMPOUND_ARREARS")     # SOFR compounded in arrears, monthly payments
STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "finsim", "terminal", "static")


def _h_open_pb_unchecked(ctx, payload):
    """What WP-23's book.onboard_pb does: open the PB account without the eligibility gate (the OTC gate is tested apart)."""
    return prime.open_pb(ctx, payload["lifetime_id"], payload["broker"], check_eligibility=False)


def runtime(tmp, at=TUE_14, books_kind="test"):
    """make_runtime + prime (WP-07) + swaps + the lifecycle scheduler; SOFR 3.87 % observed 2026-10-19; the day-1 curve."""
    rt = make_runtime(tmp, at=at, books=books_kind)
    prime.register(rt.bus)
    prime.bind(rt)
    rt.rates = RateStore()
    rt.rates.put("SOFR", [(date(2026, 10, 19), D("3.87"))], persist=False)
    rt.curve = CurveStore()
    rt.curve.set_fixed_dfs(DAY1_DFS, date(2026, 10, 19))
    swaps.register(rt.bus, rt)
    rt.swaps = swaps
    rt.providers = types.SimpleNamespace(yahoo=rt.fp)
    lifecycle.register(rt.bus, rt)
    rt.lifecycle = lifecycle.LifecycleScheduler(rt.bus, rt.db, rt, rt.clock, rt.log)
    rt.bus.register("test.open_pb_unchecked", _h_open_pb_unchecked)
    return rt


def q(rt, sql, params=()):
    with rt.db.reader() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(rt, sql, params=()):
    rows = q(rt, sql, params)
    return rows[0] if rows else None


def lines_of(rt, entry_id):
    """``[(gl, instrument_id, amount_c)]`` in line order (storage sign: debit +, credit −)."""
    return [(r["gl"], r["instrument_id"], r["amount_c"]) for r in q(rt, "SELECT gl, instrument_id, amount_c FROM journal_lines WHERE entry_id=? ORDER BY line_no", (entry_id,))]


def entry_by_idem(rt, idem):
    return one(rt, "SELECT * FROM journal_entries WHERE idem_key=?", (idem,))


def contract(rt, cid=SWP1):
    return one(rt, "SELECT * FROM otc_contracts WHERE contract_id=?", (cid,))


def book(rt, account=PB_GS, terms=None, request_id="ui:swap-book-0001", kind="IRS"):
    return execute(rt, "otc.book", {"account_id": account, "kind": kind, "terms": dict(TERMS if terms is None else terms)}, request_id)


def vm(rt, ds, cid=SWP1, suffix=""):
    """``swap.vm`` with the scheduler's request id (``:r1`` … for a retry)."""
    return execute(rt, "swap.vm", {"contract_id": cid, "date": ds, "key": f"{cid}:{ds}"}, f"job:SWAP_VM:{cid}:{ds}{suffix}", actor="lifecycle")


def pay(rt, ds, cid=SWP1, suffix=""):
    return execute(rt, "swap.pay", {"contract_id": cid, "date": ds, "key": f"{cid}:{ds}"}, f"job:SWAP_PAY:{cid}:{ds}{suffix}", actor="lifecycle")


def accrue(rt, kind, night, cid=SWP1, suffix=""):
    key = f"{kind}:{cid}:{night}"
    return execute(rt, "lifecycle.accrue", {"key": key, "kind": kind, "ref": cid, "night": night}, f"job:ACCRUE_DAILY:{key}{suffix}", actor="lifecycle")


def accrue_nights(tc, rt, first: date, n: int, kinds=("swappay", "swaprcv"), cid=SWP1):
    """Both legs, night by night, through the ACCRUE_DAILY handler (what a catch-up tick does)."""
    for i in range(n):
        night = (first + timedelta(days=i)).isoformat()
        for kind in kinds:
            res = accrue(rt, kind, night, cid)
            tc.assertTrue(res.ok and res.body["status"] == "DONE", (night, kind, res.body))


def ran(out, job):
    return [(i["key"], i["status"]) for i in out["ran"] if i["job"] == job]


class SwapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-swaps-")
        self.rt = None

    def tearDown(self):
        if self.rt is not None:
            self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start(self, at=TUE_14, books_kind="test"):
        self.rt = runtime(self.tmp, at=at, books_kind=books_kind)
        return self.rt

    def scenario(self, margin_mode=None):
        """The §19.15 fixture in T1: eligible (25,100,000.00 NAV), T1:PB_GS open with 100,000.00 settled cash."""
        rt = self.rt
        self.assertTrue(deposit(rt, 1510000000).ok)                                   # 10,000,000 + 15,100,000 = 25,100,000.00 >= the 25,000,000.00 threshold
        r = execute(rt, "account.open_pb", {"lifetime_id": T1, "broker": "GS"})
        self.assertTrue(r.ok, r.body)
        if margin_mode:
            r = execute(rt, "account.update_schedule", {"account_id": PB_GS, "swap_margin_mode": margin_mode})
            self.assertTrue(r.ok, r.body)
        self.assertTrue(fund(rt, PB_GS, 10000000).ok)                                 # 100,000.00 settled in T1:PB_GS
        self.assertEqual(10000000, bal(rt, PB_GS, "1010"))
        return rt

    # ---- §19.15 preview ----------------------------------------------------------------------------------------------------------------
    def test_preview_par_dealt_mark0_dv01_im_schedule(self):
        rt = self.start()
        self.scenario()
        p = swaps.preview(rt, PB_GS, "IRS", TERMS)
        # par 0.0399999979 -> 0.04000; dealt 0.04010; mark0 -1,903.86; DV01 +1,903.86 (a payer gains when rates rise); IM 95,193.00 (module docstring)
        self.assertEqual(("0.04000", "4.0000 %", "0.04010", "4.0100 %", "0.00010", "1", "1"),
                         (p["par_rate"], p["par_rate_pct"], p["dealt_rate"], p["dealt_rate_pct"], p["half_width"], p["half_width_bp"], p["otc_spread_mult"]))
        self.assertEqual((-190386, 190386, 190386, 9519300, "CTM", "1.9038643500"), (p["mark0_c"], p["cost_of_dealing_c"], p["dv01_c"], p["im_c"], p["margin_mode"], p["annuity"]))
        self.assertEqual(("2026-10-20", "2028-10-20", "PAY_FIXED", 1000000000, "GS"), (p["start_date"], p["maturity_date"], p["side"], p["notional_c"], p["broker"]))
        # fixing: SOFR 3.87 (2026-10-19) -> 0.03870 for the period 2026-10-20..2027-04-20
        self.assertEqual(("0.03870", "2026-10-19", "2026-10-20", "TERM"), (p["fixing"]["rate"], p["fixing"]["obs_date"], p["fixing"]["reset_date"], p["fixing_method"]))
        # schedule: 2027-04-20 (Tue), 2027-10-20 (Wed), 2028-04-20 (Thu), 2028-10-20 (Fri) — no rolls; fixed 30/360 180 days each:
        # 10,000,000 x 0.0401 x 180/360 = 200,500.00; floating ACT/360 at 3.87 %: 182 days -> 195,650.00, then 183 days -> 196,725.00
        self.assertEqual(["2027-04-20", "2027-10-20", "2028-04-20", "2028-10-20"], [r["date"] for r in p["schedule"]])
        self.assertEqual([20050000] * 4, [r["fixed_c"] for r in p["schedule"]])
        self.assertEqual([19565000, 19672500, 19672500, 19672500], [r["float_c"] for r in p["schedule"]])
        self.assertEqual([False, True, True, True], [r["indicative"] for r in p["schedule"]])
        self.assertEqual([180, 180, 180, 180], [r["fixed_days"] for r in p["schedule"]])
        self.assertEqual([182, 183, 183, 183], [r["float_days"] for r in p["schedule"]])
        # funding: IM 95,193.00 against 100,000.00 settled -> no sweep
        self.assertEqual((9519300, 10000000, 0), (p["funding"]["need_c"], p["funding"]["own_settled_c"], p["funding"]["sweep_c"]))
        self.assertEqual(("2026-10-19", "3.87", 4), (p["inputs"]["curve_as_of"], p["inputs"]["sofr"], p["inputs"]["remaining_periods"]))
        self.assertEqual(list(swaps.CONVENTIONS), p["conventions"])
        self.assertTrue(p["eligibility"]["pb_otc"])
        # receive fixed: dealt = par - hw = 0.03990; mark0 = -1 x 10,000,000 x (0.04000 - 0.03990) x 1.90386435 = -1,903.86 (the same cost);
        # DV01 = -1 x 10,000,000 x 0.0001 x 1.90386435 = -1,903.86 (a receiver loses when rates rise); IM = |DV01| x 50 = 95,193.00
        p2 = swaps.preview(rt, PB_GS, "IRS", dict(TERMS, pay_fixed=False))
        self.assertEqual(("0.03990", -190386, -190386, 9519300, "RECEIVE_FIXED"), (p2["dealt_rate"], p2["mark0_c"], p2["dv01_c"], p2["im_c"], p2["side"]))
        # an off-market fixed rate 4.500 %: dealt as given; mark0 = 10,000,000 x (0.04000 - 0.04500) x 1.90386435 = -95,193.2175 -> -95,193.22
        p3 = swaps.preview(rt, PB_GS, "IRS", dict(TERMS, fixed_rate="0.045"))
        self.assertEqual(("0.04500", True, -9519322), (p3["dealt_rate"], p3["fixed_rate_given"], p3["mark0_c"]))
        # a fixed leg booked ACT/360 ("30/360 unless booked otherwise"): 10,000,000 x 0.0401 x 182/360 = 72,982,000 / 360 = 202,727.777... -> 202,727.78,
        # then 183 days: 73,383,000 / 360 = 203,841.666... -> 203,841.67 (Apr 20 -> Oct 20 = 10+31+30+31+31+30+20 = 183; Oct 20 -> Apr 20 2028 = 11+30+31+31+29+31+20 = 183)
        pa = swaps.preview(rt, PB_GS, "IRS", dict(TERMS, fixed_basis="ACT/360"))
        self.assertEqual(([20272778, 20384167, 20384167, 20384167], [182, 183, 183, 183]), ([r["fixed_c"] for r in pa["schedule"]], [r["fixed_days"] for r in pa["schedule"]]))
        # SOFR compounded in arrears: every floating amount is indicative; the first one at the current 3.87 %:
        # 10,000,000 x ((1 + 0.0387/360)^182 - 1) = 10,000,000 x ((1.0001075)^182 - 1) = 197,565.77 (exact rationals, worked apart from the code)
        pc = swaps.preview(rt, PB_GS, "IRS", dict(TERMS, fixing="COMPOUND_ARREARS"))
        self.assertEqual(("COMPOUND_ARREARS", True, "0.04010", 19756577), (pc["fixing_method"], pc["fixing"]["indicative"], pc["dealt_rate"], pc["schedule"][0]["float_c"]))
        self.assertEqual([True, True, True, True], [r["indicative"] for r in pc["schedule"]])
        # the API codec renders the cents keys as decimal strings under the fixture's names
        enc = _api.encode(p)
        self.assertEqual(("-1903.86", "1903.86", "95193.00", "10000000.00"), (enc["mark0"], enc["dv01"], enc["im"], enc["notional"]))
        # bad terms
        for terms, code in ((dict(TERMS, currency="EUR"), "CURRENCY_UNSUPPORTED"), (dict(TERMS, float_index="EURIBOR"), "CURRENCY_UNSUPPORTED"),
                            (dict(TERMS, fixing="BOGUS"), "TERMS"), (dict(TERMS, fixed_rate="0.040125"), "TERMS"), (dict(TERMS, tenor_months=15), "TERMS"),
                            (dict(TERMS, fixed_months=3), "TERMS"), (dict(TERMS, notional="0"), "TERMS"), (dict(TERMS, fixed_basis="ACT/365"), "TERMS"),
                            (dict(TERMS, float_basis="30/360"), "TERMS")):
            with self.assertRaises(RejectError) as cm:
                swaps.preview(rt, PB_GS, "IRS", terms)
            self.assertEqual(code, cm.exception.code, terms)
        with self.assertRaises(RejectError) as cm:
            swaps.preview(rt, PB_GS, "OPTION", TERMS)
        self.assertEqual("NOT_IN_THIS_BUILD", cm.exception.code)

    # ---- §19.15 booking ---------------------------------------------------------------------------------------------------------------
    def test_book_im_contract_instrument_nav_events_and_replay(self):
        rt = self.start()
        self.scenario()
        r = book(rt)
        self.assertTrue(r.ok, r.body)
        self.assertEqual((SWP1, "OPEN", "IRS"), (r.body["contract_id"], r.body["contract"]["status"], r.body["contract"]["kind"]))
        # IM: Dr 1330 SWP-000001 95,193.00 / Cr 1010 95,193.00; the account held 100,000.00 -> no sweep (own)
        e = entry_by_idem(rt, f"im:{SWP1}:open")
        self.assertEqual([("1330", SWP1, 9519300), ("1010", None, -9519300)], lines_of(rt, e["entry_id"]))
        self.assertEqual("own", r.body["im_transfer"]["funding"]["source"])
        self.assertEqual([], q(rt, "SELECT 1 FROM transfers WHERE kind='SWEEP_IN'"))
        # settled 100,000 - 95,193 = 4,807.00; 1330 = 95,193.00
        self.assertEqual((480700, 9519300), (bal(rt, PB_GS, "1010"), bal(rt, PB_GS, "1330", inst=SWP1)))
        c = contract(rt)
        self.assertEqual(("OPEN", -190386, 0, 9519300, "CTM", "2026-10-20", "2028-10-20", "GS", 1000000000, "2026-10-20"),
                         (c["status"], c["mark_c"], c["vm_balance_c"], c["im_c"], c["margin_mode"], c["start_date"], c["maturity_date"], c["broker"], c["notional_c"], c["last_reset_date"]))
        terms = json.loads(c["terms_json"])
        self.assertEqual(("0.04010", "0.04000", True, "1.9038643500", "TERM", "2026-10-20"), (terms["fixed_rate"], terms["par_rate"], terms["pay_fixed"], terms["annuity0"],
                                                                                            terms["fixing"], terms["trade_date"]))
        self.assertEqual([["2026-10-20", "2027-04-20", "2027-04-20"], ["2027-04-20", "2027-10-20", "2027-10-20"], ["2027-10-20", "2028-04-20", "2028-04-20"], ["2028-04-20", "2028-10-20", "2028-10-20"]],
                         terms["fixed_periods"])
        self.assertEqual(terms["fixed_periods"], terms["float_periods"])
        # the SWAP instrument row and the first fixing
        inst = rt.instruments.get(SWP1)
        self.assertEqual(("SWAP", "USD", ("PB",), "COMPUTED", "ACTIVE", "2028-10-20"), (inst.kind, inst.currency, inst.account_kinds, inst.support, inst.status, inst.expiry))
        fix = one(rt, "SELECT * FROM otc_resets WHERE contract_id=? AND kind='FIX'", (SWP1,))
        self.assertEqual(("2026-10-20", "0.03870", "2026-10-19"), (fix["event_date"], json.loads(fix["detail_json"])["rate"], json.loads(fix["detail_json"])["obs_date"]))
        # NAV(T1:PB_GS) = 100,000 - 95,193 (cash) + 95,193 (1330) - 1,903.86 (overlay) = 98,096.14; the position carries the mark
        nav = rt.accounting.nav(PB_GS)
        self.assertEqual((9809614, -190386), (nav.nav_c, nav.unrealised_c))
        # §6.2 / APIUI-07: qty = the notional in whole currency units signed by the leg (a fixed payer is -10,000,000 = -1,000,000,000 cents // 100)
        pos = rt.accounting.position(PB_GS, SWP1)
        self.assertEqual((-10000000, -190386, "computed", 190386), (pos.qty, pos.mv_c, pos.mark.quality, pos.extra["dv01_c"]))
        assert_integrity(self, rt, "full")
        # the live mark / DV01 helpers agree with the stored values
        mark_c, inputs = swaps.mark(rt, SWP1)
        self.assertEqual((-190386, "0.04000", "1.9038643500", 4, "2026-10-20"), (mark_c, inputs["par_rate"], inputs["annuity"], inputs["remaining_periods"], inputs["valuation_date"]))
        self.assertEqual(190386, swaps.dv01_c(rt, SWP1))
        # next events at 14:00 on 2026-10-20: the 2027-04-20 fixing, the 2027-04-20 payment (fixed 200,500.00 at 0.0401 pay; floating 195,650.00 at the
        # booked 0.0387 receive), the 2027-10-20 payment (fixed 200,500.00; floating not fixed yet -> unknown), the 2028-10-20 maturity
        ev = swaps.next_events(rt, SWP1)
        self.assertEqual([("2027-04-20", "FIX"), ("2027-04-20", "PAY"), ("2027-10-20", "PAY"), ("2028-10-20", "MATURITY")], [(x["date"], x["event"]) for x in ev])
        self.assertEqual([("fixed", "pay", 20050000, "0.0401"), ("float", "receive", 19565000, "0.0387")], [(l["leg"], l["side"], l["amount_c"], l["rate"]) for l in ev[1]["legs"]])
        self.assertEqual([("fixed", 20050000, True), ("float", None, False)], [(l["leg"], l["amount_c"], l["known"]) for l in ev[2]["legs"]])
        # risk passes the obligations row (no terms): the contract is re-read by its id
        self.assertEqual(ev, swaps.next_events(rt, {"contract_id": SWP1, "kind": "IRS", "status": "OPEN"}))
        self.assertEqual(ev, r.body["contract"]["next_events"])
        # replay: the same request id answers the stored result, no second contract
        r2 = book(rt)
        self.assertTrue(r2.ok and r2.replayed, r2.body)
        self.assertEqual(1, len(q(rt, "SELECT 1 FROM otc_contracts")))
        # a new request books a second contract (SWP-000002) from the same 4.00 % curve: IM 95,193.00 funded by a 90,386.00 sweep
        # (settled 4,807.00; need 95,193.00 -> shortfall 90,386.00 from Treasury)
        r3 = book(rt, request_id="ui:swap-book-0002")
        self.assertTrue(r3.ok, r3.body)
        self.assertEqual(("SWP-000002", "sweep", 9038600), (r3.body["contract_id"], r3.body["im_transfer"]["funding"]["source"], r3.body["im_transfer"]["funding"]["sweep_c"]))
        self.assertEqual((0, 9519300), (bal(rt, PB_GS, "1010"), bal(rt, PB_GS, "1330", inst="SWP-000002")))
        # a receiver (SWP-000003): IM |-1,903.86| x 50 = 95,193.00 swept in full (settled 0); the position's DV01 is -1,903.86 (risk: rates +1 bp loses 1,903.86)
        r4 = book(rt, terms=dict(TERMS, pay_fixed=False), request_id="ui:swap-book-0003")
        self.assertTrue(r4.ok, r4.body)
        self.assertEqual(("SWP-000003", 9519300, 9519300), (r4.body["contract_id"], r4.body["im_transfer"]["funding"]["sweep_c"], bal(rt, PB_GS, "1330", inst="SWP-000003")))
        pos3 = rt.accounting.position(PB_GS, "SWP-000003")
        self.assertEqual((10000000, -190386, -190386), (pos3.qty, pos3.mv_c, pos3.extra["dv01_c"]))
        views = swaps.contracts_view(rt, account_id=PB_GS, detail=True)
        self.assertEqual([SWP1, "SWP-000002", "SWP-000003"], [v["contract_id"] for v in views])
        self.assertEqual(([], 1, -190386), (views[0]["history"]["vm"], len(views[0]["history"]["resets"]), views[2]["dv01_c"]))
        self.assertEqual(3, len(swaps.contracts_view(rt, scope="T1:ALL")))
        # risk (WP-09) reads the signed DV01 and the next events: payer + payer + receiver = 1,903.86 + 1,903.86 - 1,903.86 = +1,903.86 per +1 bp,
        # so rates +100 bp = +190,386.00 (DV01 x bp, the risk module's linear rule)
        v = risk.report(rt, PB_GS)
        self.assertEqual((190386, 190386, 19038600), (v.dv01_c, v.dv01_signed_c, next(s for s in v.scenarios if s["name"] == "rates +100 bp")["pnl_c"]))
        o = v.otc[0]
        self.assertEqual((3, 190386, [SWP1, "SWP-000002", "SWP-000003"]), (o["contracts"], o["dv01_c"], [x["contract_id"] for x in o["next_events"]]))
        self.assertEqual(ev, o["next_events"][0]["events"])
        assert_integrity(self, rt, "full")
        # the SWAP row is not an order instrument: NOT_TRADEABLE_HERE outside a PB account by the master's own rule
        self.assertEqual((False, True), (rt.instruments.tradeable_in(inst, "TRADING"), rt.instruments.tradeable_in(inst, "PB")))

    # ---- §19.15 CTM variation margin ------------------------------------------------------------------------------------------------
    def test_vm_ctm_day1_day2_zero_crossings_nav_pnl_l22(self):
        rt = self.start()
        self.scenario()
        self.assertTrue(book(rt).ok)
        # before 17:05 the key is not due; at 17:10 the scheduler finds exactly (SWP-000001, 2026-10-20)
        with rt.db.reader() as conn:
            self.assertEqual([], swaps.vm_keys(conn, rt.clock.epoch()))
        rt.clock.set("2026-10-20T17:10:00-04:00")
        with rt.db.reader() as conn:
            self.assertEqual([(SWP1, "2026-10-20")], swaps.vm_keys(conn, rt.clock.epoch()))
        out = rt.lifecycle.tick(only="SWAP_VM")
        self.assertEqual([(f"{SWP1}:2026-10-20", "DONE")], ran(out, "SWAP_VM"))
        self.assertNotIn("keys", rt.lifecycle.job("SWAP_VM").notes)
        # day 1: curve unchanged, mark -1,903.86, reference 0 -> delta -1,903.86 -> Dr 1320 1,903.86 / Cr 1010 1,903.86
        e = entry_by_idem(rt, f"swapvm:{SWP1}:2026-10-20")
        self.assertEqual([("1320", SWP1, 190386), ("1010", None, -190386)], lines_of(rt, e["entry_id"]))
        self.assertEqual("2026-10-20", e["business_date"])
        c = contract(rt)
        self.assertEqual((-190386, -190386), (c["mark_c"], c["vm_balance_c"]))
        self.assertEqual((190386, 0, 290314), (bal(rt, PB_GS, "1320", inst=SWP1), bal(rt, PB_GS, "2330", inst=SWP1), bal(rt, PB_GS, "1010")))
        # NAV unchanged: cash 2,903.14 + 1330 95,193.00 + 1320 1,903.86 - overlay 1,903.86 = 98,096.14
        self.assertEqual(9809614, rt.accounting.nav(PB_GS).nav_c)
        assert_integrity(self, rt, "full")
        # rerun: the scheduler finds nothing; a direct retry answers DONE "already posted" and posts nothing
        self.assertEqual([], ran(rt.lifecycle.tick(only="SWAP_VM"), "SWAP_VM"))
        r = vm(rt, "2026-10-20", suffix=":r1")
        self.assertTrue(r.ok and r.body["already"] and not r.body["posted"], r.body)
        self.assertEqual(1, len(q(rt, "SELECT 1 FROM journal_entries WHERE idem_key LIKE 'swapvm:%'")))
        self.assertEqual(1, len(q(rt, "SELECT 1 FROM otc_vm")))
        snapshot(rt, PB_GS, date(2026, 10, 20))
        snapshot(rt, "T1:ALL", date(2026, 10, 20))
        # day 2 at +10 bp: mark 17,114.00; delta = 17,114.00 - (-1,903.86) = 19,017.86 -> ONE entry: release Dr 1010 1,903.86 / Cr 1320 1,903.86
        # and receive Dr 1010 17,114.00 / Cr 2330 17,114.00
        rt.clock.set("2026-10-21T17:10:00-04:00")
        rt.curve.set_fixed_dfs(DAY2_DFS, date(2026, 10, 21))
        out = rt.lifecycle.tick(only="SWAP_VM")
        self.assertEqual([(f"{SWP1}:2026-10-21", "DONE")], ran(out, "SWAP_VM"))
        e = entry_by_idem(rt, f"swapvm:{SWP1}:2026-10-21")
        self.assertEqual([("1010", None, 190386), ("1320", SWP1, -190386), ("1010", None, 1711400), ("2330", SWP1, -1711400)], lines_of(rt, e["entry_id"]))
        c = contract(rt)
        self.assertEqual((1711400, 1711400), (c["mark_c"], c["vm_balance_c"]))
        row = one(rt, "SELECT * FROM otc_vm WHERE vm_date='2026-10-21'")
        self.assertEqual((1711400, 1901786, "CTM"), (row["mark_c"], row["delta_c"], row["mode"]))
        self.assertEqual(("0.04100", "2026-10-21"), (json.loads(row["inputs_json"])["par_rate"], json.loads(row["inputs_json"])["valuation_date"]))
        self.assertEqual((0, 1711400, 2192100), (bal(rt, PB_GS, "1320", inst=SWP1), bal(rt, PB_GS, "2330", inst=SWP1), bal(rt, PB_GS, "1010")))
        # NAV = cash 21,921.00 + 1330 95,193.00 - 2330 17,114.00 + overlay 17,114.00 = 117,114.00 = 100,000 + 17,114.00
        nav = rt.accounting.nav(PB_GS)
        self.assertEqual((11711400, 1711400), (nav.nav_c, nav.unrealised_c))
        # P&L TODAY vs the day-1 snapshot (98,096.14): +19,017.86, all Δunrealised (the overlay), residual 0, no flows
        p = rt.accounting.pnl(PB_GS, "TODAY")
        self.assertEqual((1901786, 9809614, 0, 1901786, 0), (p.pnl_c, p.nav0_c, p.flows_c, p.components["unrealised_c"], p.residual_c))
        assert_integrity(self, rt, "full")
        # the obligations view carries the OTC block
        ob = rt.accounting.obligations(PB_GS)
        self.assertEqual((9519300, 0, 1711400), (ob.margin["otc_im_c"], ob.margin["otc_vm_posted_c"], ob.margin["otc_vm_received_c"]))
        self.assertEqual([SWP1], [o["contract_id"] for o in ob.otc])
        # day 3, curve unchanged: delta 0 -> a quiet terminal SKIPPED (no attention), not listed again
        rt.clock.set("2026-10-22T17:10:00-04:00")
        out = rt.lifecycle.tick(only="SWAP_VM")
        self.assertEqual([(f"{SWP1}:2026-10-22", "SKIPPED")], ran(out, "SWAP_VM"))
        jd = one(rt, "SELECT * FROM jobs_done WHERE job='SWAP_VM' AND key=?", (f"{SWP1}:2026-10-22",))
        self.assertTrue(jd["note"].startswith("NO_CHANGE"), jd)
        self.assertEqual([], q(rt, "SELECT 1 FROM attention WHERE resolved_ts IS NULL"))
        with rt.db.reader() as conn:
            self.assertEqual([], swaps.vm_keys(conn, rt.clock.epoch()))
        self.assertEqual(2, len(q(rt, "SELECT 1 FROM otc_vm")))
        assert_integrity(self, rt, "full")
        # day 4, the curve back at 4.00 % (as of 2026-10-23): mark -1,903.86; delta = -1,903.86 - 17,114.00 = -19,017.86 -> the reverse zero crossing in ONE
        # entry: release the VM received Dr 2330 17,114.00 / Cr 1010 17,114.00, then post Dr 1320 1,903.86 / Cr 1010 1,903.86; funded from settled cash
        # 21,921.00 (no sweep): 21,921.00 - 19,017.86 = 2,903.14
        rt.clock.set("2026-10-23T17:10:00-04:00")
        rt.curve.set_fixed_dfs(DAY1_DFS, date(2026, 10, 23))
        out = rt.lifecycle.tick(only="SWAP_VM")
        self.assertEqual([(f"{SWP1}:2026-10-23", "DONE")], ran(out, "SWAP_VM"))
        e = entry_by_idem(rt, f"swapvm:{SWP1}:2026-10-23")
        self.assertEqual([("2330", SWP1, 1711400), ("1010", None, -1711400), ("1320", SWP1, 190386), ("1010", None, -190386)], lines_of(rt, e["entry_id"]))
        c = contract(rt)
        self.assertEqual((-190386, -190386), (c["mark_c"], c["vm_balance_c"]))
        self.assertEqual((190386, 0, 290314), (bal(rt, PB_GS, "1320", inst=SWP1), bal(rt, PB_GS, "2330", inst=SWP1), bal(rt, PB_GS, "1010")))
        self.assertEqual([], q(rt, "SELECT 1 FROM transfers WHERE kind='SWEEP_IN'"))
        # NAV back to 2,903.14 + 95,193.00 + 1,903.86 - 1,903.86 = 98,096.14
        self.assertEqual(9809614, rt.accounting.nav(PB_GS).nav_c)
        assert_integrity(self, rt, "full")

    # ---- §19.15 STM variant ----------------------------------------------------------------------------------------------------------
    def test_vm_stm_variant(self):
        rt = self.start()
        self.scenario(margin_mode="STM")
        r = book(rt)
        self.assertTrue(r.ok, r.body)
        self.assertEqual("STM", contract(rt)["margin_mode"])
        # day 1: Dr 4450 1,903.86 / Cr 1010 1,903.86; mark_c -> 0 (the model mark -1,903.86 stays the reference on otc_vm)
        rt.clock.set("2026-10-20T17:10:00-04:00")
        self.assertTrue(vm(rt, "2026-10-20").ok)
        e = entry_by_idem(rt, f"swapvm:{SWP1}:2026-10-20")
        self.assertEqual([("4450", SWP1, 190386), ("1010", None, -190386)], lines_of(rt, e["entry_id"]))
        c = contract(rt)
        self.assertEqual((0, 0, -190386), (c["mark_c"], c["vm_balance_c"], one(rt, "SELECT mark_c FROM otc_vm WHERE vm_date='2026-10-20'")["mark_c"]))
        # NAV = cash (4,807.00 - 1,903.86 = 2,903.14) + 1330 95,193.00 + overlay 0 = 98,096.14 (realised instead of overlay)
        nav = rt.accounting.nav(PB_GS)
        self.assertEqual((9809614, 0, -190386), (nav.nav_c, nav.unrealised_c, nav.realised_c))
        assert_integrity(self, rt, "full")
        # day 2: mark 17,114.00 - reference (-1,903.86) = 19,017.86 -> Dr 1010 19,017.86 / Cr 4450 19,017.86; 4450 cumulative +17,114.00
        rt.clock.set("2026-10-21T17:10:00-04:00")
        rt.curve.set_fixed_dfs(DAY2_DFS, date(2026, 10, 21))
        r = vm(rt, "2026-10-21")
        self.assertTrue(r.ok, r.body)
        e = entry_by_idem(rt, f"swapvm:{SWP1}:2026-10-21")
        self.assertEqual([("1010", None, 1901786), ("4450", SWP1, -1901786)], lines_of(rt, e["entry_id"]))
        self.assertEqual((1711400, 0, 0, 2192100), (bal(rt, PB_GS, "4450", inst=SWP1), bal(rt, PB_GS, "1320", inst=SWP1), bal(rt, PB_GS, "2330", inst=SWP1), bal(rt, PB_GS, "1010")))
        c = contract(rt)
        self.assertEqual((0, 0), (c["mark_c"], c["vm_balance_c"]))
        # NAV identical to CTM: cash 21,921.00 + 1330 95,193.00 = 117,114.00
        nav = rt.accounting.nav(PB_GS)
        self.assertEqual((11711400, 0, 1711400), (nav.nav_c, nav.unrealised_c, nav.realised_c))
        assert_integrity(self, rt, "full")

    # ---- §19.15 accruals through the scheduler -----------------------------------------------------------------------------------------
    def test_accruals_fixed_30_360_and_floating_act_360(self):
        rt = self.start()
        self.scenario()
        self.assertTrue(book(rt).ok)
        # Sun 2026-10-25 00:35: ACCRUE_DAILY owes the nights of 10-20 .. 10-24 to both legs (and T1's own cash / PB credit keys)
        out = rt.lifecycle.tick(at="2026-10-25T00:35:00-04:00", only="ACCRUE_DAILY")
        swap_keys = sorted(k for k, s in ran(out, "ACCRUE_DAILY") if k.startswith("swap"))
        self.assertEqual(sorted([f"swappay:{SWP1}:2026-10-2{d}" for d in range(5)] + [f"swaprcv:{SWP1}:2026-10-2{d}" for d in range(5)]), swap_keys)
        self.assertNotIn("swaprcv", rt.lifecycle.job("ACCRUE_DAILY").notes)
        # fixed pay leg 10,000,000 x 0.0401 / 360 = 1,113.8889 per night, cumulative rounding:
        # to_c(1,113.8889) = 1,113.89; to_c(2,227.7778) = 2,227.78 (+1,113.89); to_c(3,341.6667) = 3,341.67 (+1,113.89);
        # to_c(4,455.5556) = 4,455.56 (+1,113.89); to_c(5,569.4444) = 5,569.44 (+1,113.88) -> Dr 4453 / Cr 2340 (4453 = swap coupons, a financing GL:
        # FINSIM1 C11 / PORTFOLIO-08 moved the coupon accruals off the realised 4450)
        for d, amount in zip(range(20, 25), (111389, 111389, 111389, 111389, 111388)):
            e = entry_by_idem(rt, f"accr:swappay:{SWP1}:2026-10-{d}")
            self.assertEqual([("4453", SWP1, amount), ("2340", SWP1, -amount)], lines_of(rt, e["entry_id"]), d)
            # floating receive leg at 3.87 %: 10,000,000 x 0.0387 / 360 = 1,075.00 every night -> Dr 1225 / Cr 4453
            e = entry_by_idem(rt, f"accr:swaprcv:{SWP1}:2026-10-{d}")
            self.assertEqual([("1225", SWP1, 107500), ("4453", SWP1, -107500)], lines_of(rt, e["entry_id"]), d)
        # 2340 = 5,569.44; 1225 = 5 x 1,075.00 = 5,375.00; 4453 (kind EXPENSE: a cost is positive) = 5,569.44 paid - 5,375.00 received = +194.44
        # (net 38.89 x 4 + 38.88); 4450 untouched (0.00)
        self.assertEqual((556944, 537500, 19444, 0), (bal(rt, PB_GS, "2340", inst=SWP1), bal(rt, PB_GS, "1225", inst=SWP1), bal(rt, PB_GS, "4453", inst=SWP1),
                                                     bal(rt, PB_GS, "4450", inst=SWP1)))
        st = {r["kind"]: r for r in q(rt, "SELECT * FROM accrual_state WHERE ref_id=?", (SWP1,))}
        self.assertEqual(("2026-10-20", "2026-10-24", 556944, "0.0401"), (st["swappay"]["segment_start"], st["swappay"]["accrued_through"], st["swappay"]["cum_c"], st["swappay"]["rate"]))
        self.assertEqual(("2026-10-20", "2026-10-24", 537500, "0.0387"), (st["swaprcv"]["segment_start"], st["swaprcv"]["accrued_through"], st["swaprcv"]["cum_c"], st["swaprcv"]["rate"]))
        # the position shows the net accrued; a second tick accrues nothing more
        self.assertEqual(537500 - 556944, rt.accounting.position(PB_GS, SWP1).extra["accrued_c"])
        self.assertEqual([], [k for k, s in ran(rt.lifecycle.tick(only="ACCRUE_DAILY"), "ACCRUE_DAILY") if k.startswith("swap")])
        self.assertEqual(10, len(q(rt, "SELECT 1 FROM journal_entries WHERE idem_key LIKE 'accr:swap%'")))
        assert_integrity(self, rt, "full")

    # ---- §19.15 payment date, re-fix, maturity-side helpers -----------------------------------------------------------------------------
    def test_payment_net_settlement_refix_and_idempotency(self):
        rt = self.start()
        self.scenario()
        self.assertTrue(book(rt).ok)
        rt.rates.put("SOFR", [(date(2027, 4, 16), D("3.95"))], persist=False)
        rt.clock.set("2027-04-20T00:50:00-04:00")
        with rt.db.reader() as conn:
            self.assertEqual(([(SWP1, "2027-04-20")], [(SWP1, "2027-04-20")]), (swaps.pay_keys(conn, rt.clock.epoch()), swaps.fix_keys(conn, rt.clock.epoch())))
        # before the catch-up the payment waits: SKIPPED ACCRUALS_PENDING with one WARN attention
        r = pay(rt, "2027-04-20")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("SKIPPED", "ACCRUALS_PENDING"), (r.body["status"], r.body["code"]))
        self.assertEqual(1, len(q(rt, "SELECT 1 FROM attention WHERE code='ACCRUALS_PENDING' AND resolved_ts IS NULL")))
        # the catch-up: 182 nights (2026-10-20 .. 2027-04-19) for both legs through the ACCRUE_DAILY handler with its own request ids
        self.assertEqual("2027-04-19", (date(2026, 10, 20) + timedelta(days=181)).isoformat())
        accrue_nights(self, rt, date(2026, 10, 20), 182)
        # fixed leg: nights 1..181 cumulate to to_c(10,000,000 x 0.0401 x 181/360) = to_c(201,613.8889) = 201,613.89; the 182nd night (2027-04-19)
        # trues up to the 30/360 coupon 10,000,000 x 0.0401 x 180/360 = 200,500.00 -> -1,113.89 (Dr 2340 / Cr 4453); 2340 = 200,500.00
        e = entry_by_idem(rt, f"accr:swappay:{SWP1}:2027-04-19")
        self.assertEqual([("2340", SWP1, 111389), ("4453", SWP1, -111389)], lines_of(rt, e["entry_id"]))
        # floating leg: 182 x 1,075.00 = 195,650.00 = 10,000,000 x 0.0387 x 182/360 exactly
        e = entry_by_idem(rt, f"accr:swaprcv:{SWP1}:2027-04-19")
        self.assertEqual([("1225", SWP1, 107500), ("4453", SWP1, -107500)], lines_of(rt, e["entry_id"]))
        self.assertEqual((20050000, 19565000), (bal(rt, PB_GS, "2340", inst=SWP1), bal(rt, PB_GS, "1225", inst=SWP1)))
        self.assertEqual(364, len(q(rt, "SELECT 1 FROM journal_entries WHERE idem_key LIKE 'accr:swap%'")))
        assert_integrity(self, rt, "full")
        # the payment (retry id): fixed 200,500.00 / floating 195,650.00 / net pay 4,850.00: Dr 2340 200,500.00 / Cr 1225 195,650.00 / Cr 1010 4,850.00;
        # settled cash 4,807.00 < 4,850.00 -> a 43.00 Treasury sweep linked to the payment entry; 1225 = 2340 = 0 afterwards
        r = pay(rt, "2027-04-20", suffix=":r1")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("DONE", 19565000, 20050000, -485000, "sweep", 4300), (r.body["status"], r.body["receive_c"], r.body["pay_c"], r.body["net_c"], r.body["funding"]["source"], r.body["funding"]["sweep_c"]))
        e = entry_by_idem(rt, f"swappay:{SWP1}:2027-04-20")
        self.assertEqual([("2340", SWP1, 20050000), ("1225", SWP1, -19565000), ("1010", None, -485000)], lines_of(rt, e["entry_id"]))
        self.assertEqual("2027-04-20", e["business_date"])
        self.assertEqual((0, 0, 0), (bal(rt, PB_GS, "2340", inst=SWP1), bal(rt, PB_GS, "1225", inst=SWP1), bal(rt, PB_GS, "1010")))
        self.assertEqual(("swappay", f"swappay:{SWP1}:2027-04-20", 4300), tuple(one(rt, "SELECT linked_type, linked_id, amount_c FROM transfers WHERE kind='SWEEP_IN'").values()))
        pay_row = one(rt, "SELECT * FROM otc_resets WHERE contract_id=? AND kind='PAY'", (SWP1,))
        self.assertEqual(("2027-04-20", -485000), (pay_row["event_date"], pay_row["amount_c"]))
        self.assertEqual([], q(rt, "SELECT 1 FROM attention WHERE code='ACCRUALS_PENDING' AND resolved_ts IS NULL"))
        # 4453 over the period: pay 200,500.00 - receive 195,650.00 = +4,850.00 (the net coupon: a financing cost, kind EXPENSE); nothing on 4450
        self.assertEqual((485000, 0), (bal(rt, PB_GS, "4453", inst=SWP1), bal(rt, PB_GS, "4450", inst=SWP1)))
        # SWAP_FIX 2027-04-20 re-fixes from the latest SOFR <= that date: 3.95 (2027-04-16) -> 0.03950
        r = execute(rt, "swap.fix", {"contract_id": SWP1, "date": "2027-04-20", "key": f"{SWP1}:2027-04-20"}, f"job:SWAP_FIX:{SWP1}:2027-04-20", actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("DONE", "0.03950", "2027-04-16"), (r.body["status"], r.body["fixing"]["rate"], r.body["fixing"]["obs_date"]))
        self.assertEqual("2027-04-20", contract(rt)["last_reset_date"])
        with rt.db.reader() as conn:
            self.assertEqual(([], []), (swaps.pay_keys(conn, rt.clock.epoch()), swaps.fix_keys(conn, rt.clock.epoch())))
        # the next period's first night restarts both segments: fixed 1,113.89 again; floating 10,000,000 x 0.0395 / 360 = 1,097.2222 -> 1,097.22
        rt.clock.set("2027-04-21T00:35:00-04:00")
        self.assertEqual(111389, accrue(rt, "swappay", "2027-04-20").body["amount_c"])
        self.assertEqual(109722, accrue(rt, "swaprcv", "2027-04-20").body["amount_c"])
        st = {r["kind"]: r for r in q(rt, "SELECT * FROM accrual_state WHERE ref_id=?", (SWP1,))}
        self.assertEqual(("2027-04-20", 111389, "2027-04-20", 109722, "0.0395"), (st["swappay"]["segment_start"], st["swappay"]["cum_c"], st["swaprcv"]["segment_start"], st["swaprcv"]["cum_c"], st["swaprcv"]["rate"]))
        # rerun idempotency of every key: nothing is posted twice
        n_entries = len(q(rt, "SELECT 1 FROM journal_entries"))
        r = pay(rt, "2027-04-20", suffix=":r2")
        self.assertTrue(r.ok and r.body["already"] and not r.body["posted"], r.body)
        r = execute(rt, "swap.fix", {"contract_id": SWP1, "date": "2027-04-20", "key": f"{SWP1}:2027-04-20"}, f"job:SWAP_FIX:{SWP1}:2027-04-20:r1", actor="lifecycle")
        self.assertTrue(r.ok and r.body["already"], r.body)
        # (the runner short-circuits a DONE key with "already done"; a direct accrue_night of an accrued night answers amount 0)
        r = accrue(rt, "swaprcv", "2027-04-20", suffix=":r1")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((0, False), (r.body.get("amount_c", 0), bool(r.body.get("posted"))))
        r = accrue(rt, "swappay", "2026-10-20", suffix=":r1")
        self.assertEqual((0, False), (r.body.get("amount_c", 0), bool(r.body.get("posted"))))
        with rt.db.writer() as tx:
            ctx = types.SimpleNamespace(conn=tx.conn, services=rt, now=tx.now, ts=tx.ts)
            self.assertEqual([], swaps.accrual_lines_swap(ctx, SWP1, "2027-04-20"))
        self.assertEqual(n_entries, len(q(rt, "SELECT 1 FROM journal_entries")))
        # the mark after the first payment: 3 remaining periods -> the 1.5-year bucket of the 4.00 % curve:
        # Σ df(0.5, 1.0, 1.5) = 0.98039216 + 0.96116878 + 0.94232233 = 2.88388327; annuity = 1.441941635; par = 2 x (1 - 0.94232233) / 2.88388327
        # = 0.11535534 / 2.88388327 = 0.0399999... -> 0.04000; mark = 10,000,000 x (0.04000 - 0.04010) x 1.441941635 = -1,441.941635 -> -1,441.94
        mark_c, inputs = swaps.mark(rt, SWP1)
        self.assertEqual((-144194, 3, "1.4419416350"), (mark_c, inputs["remaining_periods"], inputs["annuity"]))
        assert_integrity(self, rt, "full")

    # ---- maturity: the last payment unwinds VM and releases IM ------------------------------------------------------------------------------
    def test_maturity_unwinds_vm_releases_im(self):
        rt = self.start()
        self.scenario()
        r = book(rt, terms=TERMS6)
        self.assertTrue(r.ok, r.body)
        # one semi-annual period: df(0.5) = 0.98039216; par = 2 x (1 - 0.98039216) / 0.98039216 = 0.03921568 / 0.98039216 = 0.0399999935 -> 0.04000;
        # A = 0.98039216 / 2 = 0.49019608; dealt 0.04010; mark0 = 10,000,000 x (0.04000 - 0.04010) x 0.49019608 = -490.19608 -> -490.20;
        # DV01 = 10,000,000 x 0.0001 x 0.49019608 = 490.19608 -> 490.20; IM = 490.20 x 50 = 24,510.00; settled 100,000 - 24,510 = 75,490.00
        c = contract(rt)
        self.assertEqual((-49020, 2451000, "2027-04-20", 7549000), (c["mark_c"], c["im_c"], c["maturity_date"], bal(rt, PB_GS, "1010")))
        # day-1 VM: delta -490.20 - 0 -> Dr 1320 490.20 / Cr 1010 490.20; settled 75,490.00 - 490.20 = 74,999.80
        rt.clock.set("2026-10-20T17:10:00-04:00")
        self.assertEqual(-49020, vm(rt, "2026-10-20").body["delta_c"])
        # 182 nights: fixed 30/360 coupon 10,000,000 x 0.0401 x 180/360 = 200,500.00; floating 3.87 % x 182/360 = 195,650.00
        rt.clock.set("2027-04-20T00:50:00-04:00")
        accrue_nights(self, rt, date(2026, 10, 20), 182)
        self.assertEqual((20050000, 19565000), (bal(rt, PB_GS, "2340", inst=SWP1), bal(rt, PB_GS, "1225", inst=SWP1)))
        with rt.db.reader() as conn:
            self.assertEqual(([(SWP1, "2027-04-20")], []), (swaps.pay_keys(conn, rt.clock.epoch()), swaps.fix_keys(conn, rt.clock.epoch())))
        # the last payment: net -4,850.00 from settled cash 74,999.80 (no sweep) -> 70,149.80; then the maturity close: VM balance -490.20 unwound
        # (Dr 1010 490.20 / Cr 1320 490.20 -> 70,640.00; value 0, nothing realised) and the IM released (Dr 1010 24,510.00 / Cr 1330 -> 95,150.00)
        r = pay(rt, "2027-04-20")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((-485000, "own", 0, "MATURED"), (r.body["net_c"], r.body["funding"]["source"], r.body["matured"]["result_c"], contract(rt)["status"]))
        e = entry_by_idem(rt, f"otc:{SWP1}:mature")
        self.assertEqual([("1010", None, 49020), ("1320", SWP1, -49020)], lines_of(rt, e["entry_id"]))
        e2 = entry_by_idem(rt, f"im:{SWP1}:release")
        self.assertEqual([("1010", None, 2451000), ("1330", SWP1, -2451000)], lines_of(rt, e2["entry_id"]))
        # every per-contract balance is 0 but 4453 = 200,500.00 paid - 195,650.00 received = +4,850.00 (the net coupon, a financing cost); 4450 = 0
        # (value 0 at maturity, nothing realised); NAV = cash 95,150.00 = 100,000 - 4,850.00
        self.assertEqual((0, 0, 0, 0, 0, 485000, 0, 9515000),
                         (bal(rt, PB_GS, "1320", inst=SWP1), bal(rt, PB_GS, "2330", inst=SWP1), bal(rt, PB_GS, "1330", inst=SWP1), bal(rt, PB_GS, "1225", inst=SWP1),
                          bal(rt, PB_GS, "2340", inst=SWP1), bal(rt, PB_GS, "4453", inst=SWP1), bal(rt, PB_GS, "4450", inst=SWP1), bal(rt, PB_GS, "1010")))
        nav = rt.accounting.nav(PB_GS)
        self.assertEqual((9515000, 0), (nav.nav_c, nav.unrealised_c))
        c = contract(rt)
        self.assertEqual(("MATURED", 0, 0, 0), (c["status"], c["mark_c"], c["vm_balance_c"], c["im_c"]))
        self.assertEqual(("EXPIRED", [], []), (rt.instruments.get(SWP1).status, rt.accounting.positions(PB_GS), q(rt, "SELECT 1 FROM accrual_state WHERE ref_id=?", (SWP1,))))
        with rt.db.reader() as conn:
            self.assertEqual(([], [], []), (swaps.vm_keys(conn, rt.clock.epoch()), swaps.pay_keys(conn, rt.clock.epoch()), swaps.fix_keys(conn, rt.clock.epoch())))
        self.assertEqual([], swaps.next_events(rt, SWP1))
        assert_integrity(self, rt, "full")
        # a retry finds the contract closed: a no-op, nothing posted
        r = pay(rt, "2027-04-20", suffix=":r1")
        self.assertTrue(r.ok and not r.body["posted"], r.body)

    # ---- §19.15 termination ----------------------------------------------------------------------------------------------------------
    def test_terminate_at_the_day2_mark(self):
        rt = self.start()
        self.scenario()
        self.assertTrue(book(rt).ok)
        rt.clock.set("2026-10-20T17:10:00-04:00")
        self.assertTrue(vm(rt, "2026-10-20").ok)
        rt.clock.set("2026-10-21T17:10:00-04:00")
        rt.curve.set_fixed_dfs(DAY2_DFS, date(2026, 10, 21))
        self.assertTrue(vm(rt, "2026-10-21").ok)
        self.assertEqual((1711400, 1711400, 2192100), (contract(rt)["vm_balance_c"], bal(rt, PB_GS, "2330", inst=SWP1), bal(rt, PB_GS, "1010")))
        r = execute(rt, "otc.terminate", {"contract_id": SWP1}, "ui:swap-term-0001")
        self.assertTrue(r.ok, r.body)
        # MONEY-2: no ACCRUE_DAILY ran, but the contract was open across the night of 10-20 (its start), so the termination posts that night
        # first on both legs: pay fixed to_c(10,000,000 x 0.0401 x 1 / 360) = to_c(1,113.888..) = 1,113.89 (Dr 4453 / Cr 2340), receive SOFR
        # to_c(10,000,000 x 0.0387 / 360) = 1,075.00 (Dr 1225 / Cr 4453)
        self.assertEqual([("4453", SWP1, 111389), ("2340", SWP1, -111389)], lines_of(rt, entry_by_idem(rt, f"accr:swappay:{SWP1}:2026-10-20")["entry_id"]))
        self.assertEqual([("1225", SWP1, 107500), ("4453", SWP1, -107500)], lines_of(rt, entry_by_idem(rt, f"accr:swaprcv:{SWP1}:2026-10-20")["entry_id"]))
        # value = the day-2 mark 17,114.00: Dr 2330 17,114.00 / Cr 1010 17,114.00 (VM unwound) + Dr 1010 17,114.00 / Cr 4450 17,114.00 (value); the
        # accrued legs settle net: Dr 2340 1,113.89 / Cr 1225 1,075.00 / Cr 1010 38.89 -> cash -17,114.00 + 17,114.00 - 38.89 = -38.89 (own cash 21,921.00:
        # no sweep); IM Dr 1010 95,193.00 / Cr 1330 95,193.00
        self.assertEqual((1711400, 1711400, -3889, 9519300, 111389, 107500, "TERMINATED", "2026-10-20"),
                         (r.body["termination_value_c"], r.body["result_c"], r.body["cash_c"], r.body["im_released_c"], r.body["accrued_pay_c"],
                          r.body["accrued_receive_c"], r.body["contract"]["status"], r.body["accrued_through"]))
        e = entry_by_idem(rt, f"otc:{SWP1}:terminate")
        self.assertEqual([("2330", SWP1, 1711400), ("1010", None, -1711400), ("1010", None, 1711400), ("4450", SWP1, -1711400), ("2340", SWP1, 111389),
                          ("1225", SWP1, -107500), ("1010", None, -3889)], lines_of(rt, e["entry_id"]))
        e2 = entry_by_idem(rt, f"im:{SWP1}:release")
        self.assertEqual([("1010", None, 9519300), ("1330", SWP1, -9519300)], lines_of(rt, e2["entry_id"]))
        self.assertEqual(4, len(r.body["entries"]))
        self.assertIn(e["entry_id"], r.body["entries"])
        self.assertIn(e2["entry_id"], r.body["entries"])
        # L22 all zero; cash 21,921.00 - 38.89 + 95,193.00 = 117,075.11 = NAV (nothing left but cash); 4450 = the termination value 17,114.00 (realised),
        # 4453 = 1,113.89 paid - 1,075.00 received = +38.89 (a financing cost): 17,114.00 - 38.89 = 17,075.11 of P&L
        self.assertEqual((0, 0, 0, 0, 0, 1711400, 3889, 11707511),
                         (bal(rt, PB_GS, "1320", inst=SWP1), bal(rt, PB_GS, "2330", inst=SWP1), bal(rt, PB_GS, "1330", inst=SWP1), bal(rt, PB_GS, "1225", inst=SWP1),
                          bal(rt, PB_GS, "2340", inst=SWP1), bal(rt, PB_GS, "4450", inst=SWP1), bal(rt, PB_GS, "4453", inst=SWP1), bal(rt, PB_GS, "1010")))
        c = contract(rt)
        self.assertEqual(("TERMINATED", 0, 0, 0), (c["status"], c["mark_c"], c["vm_balance_c"], c["im_c"]))
        self.assertIsNotNone(c["closed_ts"])
        nav = rt.accounting.nav(PB_GS)
        self.assertEqual((11707511, 0, 1711400), (nav.nav_c, nav.unrealised_c, nav.realised_c))
        self.assertEqual([], rt.accounting.positions(PB_GS))
        self.assertEqual("EXPIRED", rt.instruments.get(SWP1).status)
        self.assertEqual([], q(rt, "SELECT 1 FROM accrual_state WHERE ref_id=?", (SWP1,)))
        with rt.db.reader() as conn:
            self.assertEqual([], swaps.vm_keys(conn, rt.clock.epoch()))
        assert_integrity(self, rt, "full")
        # a second termination is refused; the replay answers the stored result; the view shows the history
        r2 = execute(rt, "otc.terminate", {"contract_id": SWP1}, "ui:swap-term-0002")
        self.assertEqual((409, "NOT_OPEN"), (r2.http, r2.code))
        r3 = execute(rt, "otc.terminate", {"contract_id": SWP1}, "ui:swap-term-0001")
        self.assertTrue(r3.ok and r3.replayed)
        v = swaps.contracts_view(rt, account_id=PB_GS, detail=True)[0]
        self.assertEqual(("TERMINATED", 2, 0, [], None, None, 0), (v["status"], len(v["history"]["vm"]), v["mark_c"], v["next_events"], v["next_fix_date"], v["next_pay_date"],
                                                                 v["remaining_periods"]))
        self.assertEqual(0, swaps.dv01_c(rt, SWP1))

    def test_terminate_settles_accrued_legs(self):
        rt = self.start()
        self.scenario()
        self.assertTrue(book(rt).ok)
        # five nights of both legs (10-20 .. 10-24): 2340 = 5,569.44, 1225 = 5 x 1,075.00 = 5,375.00 (see the accrual test)
        rt.clock.set("2026-10-25T00:35:00-04:00")
        accrue_nights(self, rt, date(2026, 10, 20), 5)
        self.assertEqual((556944, 537500), (bal(rt, PB_GS, "2340", inst=SWP1), bal(rt, PB_GS, "1225", inst=SWP1)))
        # terminate on 2026-10-25 (no VM ran: balance 0): value = the mark on the unchanged 4.00 % curve, 4 periods = -1,903.86 -> Dr 4450 1,903.86 /
        # Cr 1010 1,903.86; the accrued legs settle net: Dr 2340 5,569.44 / Cr 1225 5,375.00 / Cr 1010 194.44; cash 4,807.00 - 1,903.86 - 194.44 = 2,708.70
        # (own funds), then the IM release +95,193.00 -> 97,901.70 = NAV = 100,000 - 1,903.86 - 194.44
        rt.clock.set("2026-10-25T10:00:00-04:00")
        r = execute(rt, "otc.terminate", {"contract_id": SWP1}, "ui:swap-term-0003")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((-190386, -190386, -209830, 537500, 556944, 9519300),
                         (r.body["termination_value_c"], r.body["result_c"], r.body["cash_c"], r.body["accrued_receive_c"], r.body["accrued_pay_c"], r.body["im_released_c"]))
        e = entry_by_idem(rt, f"otc:{SWP1}:terminate")
        self.assertEqual([("4450", SWP1, 190386), ("1010", None, -190386), ("2340", SWP1, 556944), ("1225", SWP1, -537500), ("1010", None, -19444)], lines_of(rt, e["entry_id"]))
        # 4450 for the contract: the termination value -1,903.86 (realised); 4453: the accruals 5,569.44 paid - 5,375.00 received = +194.44 (a financing
        # cost); together -2,098.30 = 100,000.00 - 97,901.70
        self.assertEqual((0, 0, 0, -190386, 19444, 9790170), (bal(rt, PB_GS, "1225", inst=SWP1), bal(rt, PB_GS, "2340", inst=SWP1), bal(rt, PB_GS, "1330", inst=SWP1),
                                                              bal(rt, PB_GS, "4450", inst=SWP1), bal(rt, PB_GS, "4453", inst=SWP1), bal(rt, PB_GS, "1010")))
        self.assertEqual(9790170, rt.accounting.nav(PB_GS).nav_c)
        self.assertEqual([], q(rt, "SELECT 1 FROM transfers WHERE kind='SWEEP_IN'"))
        assert_integrity(self, rt, "full")

    def test_terminate_after_missed_nights_catches_up_the_coupons(self):
        """MONEY-2 (checks/money/s14_irs_terminate.py): booked Tue 2026-10-20 14:00, the server is down until Fri 10-23 10:00 and the owner
        terminates before any lifecycle tick - every night the contract owes is posted first and settled at the termination."""
        rt = self.start()
        self.scenario()
        self.assertTrue(book(rt).ok)
        rt.clock.set("2026-10-23T10:00:00-04:00")
        r = execute(rt, "otc.terminate", {"contract_id": SWP1}, "ui:swap-term-0004")
        self.assertTrue(r.ok, r.body)
        # the nights 10-20, 10-21, 10-22 (through min(today, maturity) - 1 = 10-22), each leg cumulatively in its segment:
        # pay fixed to_c(10,000,000 x 0.0401 x 1/360) = 1,113.89, to_c(x 2/360) = to_c(2,227.777..) = 2,227.78 (+1,113.89),
        # to_c(x 3/360) = to_c(3,341.666..) = 3,341.67 (+1,113.89); receive SOFR at the 0.0387 booking fixing 3 x 1,075.00 = 3,225.00
        self.assertEqual([111389, 111389, 111389], [lines_of(rt, entry_by_idem(rt, f"accr:swappay:{SWP1}:2026-10-{d}")["entry_id"])[0][2] for d in (20, 21, 22)])
        self.assertEqual([107500, 107500, 107500], [lines_of(rt, entry_by_idem(rt, f"accr:swaprcv:{SWP1}:2026-10-{d}")["entry_id"])[0][2] for d in (20, 21, 22)])
        self.assertEqual((334167, 322500, "2026-10-22", 6), (r.body["accrued_pay_c"], r.body["accrued_receive_c"], r.body["accrued_through"], len(r.body["caught_up"])))
        # no VM ran (balance 0); the value is the mark on the unchanged 4.00 % curve, 4 periods: -1,903.86 -> Dr 4450 / Cr 1010 1,903.86; the coupons
        # settle net 3,225.00 - 3,341.67 = -116.67 (Dr 2340 3,341.67 / Cr 1225 3,225.00 / Cr 1010 116.67); cash -1,903.86 - 116.67 = -2,020.53 from
        # the 4,807.00 own cash (no sweep); then the IM 95,193.00 is released -> settled 4,807.00 - 2,020.53 + 95,193.00 = 97,979.47
        self.assertEqual((-190386, -202053), (r.body["termination_value_c"], r.body["cash_c"]))
        e = entry_by_idem(rt, f"otc:{SWP1}:terminate")
        self.assertEqual([("4450", SWP1, 190386), ("1010", None, -190386), ("2340", SWP1, 334167), ("1225", SWP1, -322500), ("1010", None, -11667)],
                         lines_of(rt, e["entry_id"]))
        # 4450 = the termination value -1,903.86; 4453 = 3,341.67 paid - 3,225.00 received = +116.67 (a financing cost); together -2,020.53;
        # NAV = cash 97,979.47 = 100,000.00 - 2,020.53; every contract balance 0 (L22)
        self.assertEqual((0, 0, 0, -190386, 11667, 9797947), (bal(rt, PB_GS, "1225", inst=SWP1), bal(rt, PB_GS, "2340", inst=SWP1), bal(rt, PB_GS, "1330", inst=SWP1),
                                                              bal(rt, PB_GS, "4450", inst=SWP1), bal(rt, PB_GS, "4453", inst=SWP1), bal(rt, PB_GS, "1010")))
        self.assertEqual(9797947, rt.accounting.nav(PB_GS).nav_c)
        self.assertEqual([], q(rt, "SELECT 1 FROM transfers WHERE kind='SWEEP_IN'"))
        # the scheduler afterwards posts nothing more for the contract (TERMINATED: no keys), nothing twice
        rt.lifecycle.tick(at="2026-10-24T00:40:00-04:00")
        self.assertEqual(6, len(q(rt, "SELECT 1 FROM journal_entries WHERE idem_key LIKE ?", (f"accr:swap%:{SWP1}:%",))))
        assert_integrity(self, rt, "full")

    def test_terminate_fixes_a_missed_reset_first_or_refuses(self):
        """MONEY-2 with a TERM reset inside the missed nights: a 3-month floating leg resets on 2027-01-20; the server is down from 01-19 and the owner
        terminates on 01-22.  The period's fixing is recorded from its SOFR print first; without a print within 10 days the termination is refused 409
        RATE_UNAVAILABLE and nothing is written."""
        rt = self.start()
        self.scenario()
        terms = dict(TERMS, float_months=3)
        self.assertTrue(book(rt, terms=terms).ok)
        # every night from the start through 2027-01-18 accrues through ACCRUE_DAILY (91 nights: 10-20 .. 01-18)
        rt.clock.set("2027-01-19T00:35:00-05:00")
        accrue_nights(self, rt, date(2026, 10, 20), 91)
        # the next floating period starts 2027-01-20 (Wed); SOFR has no print within 10 days of it -> 409 RATE_UNAVAILABLE, nothing written
        rt.clock.set("2027-01-22T10:00:00-05:00")
        r = execute(rt, "otc.terminate", {"contract_id": SWP1}, "ui:swap-term-0005")
        self.assertEqual((409, "RATE_UNAVAILABLE"), (r.http, r.code))
        self.assertEqual(("OPEN", None, None), (contract(rt)["status"], entry_by_idem(rt, f"accr:swappay:{SWP1}:2027-01-19"),
                                                one(rt, "SELECT 1 FROM otc_resets WHERE contract_id=? AND event_date='2027-01-20' AND kind='FIX'", (SWP1,))))
        # SOFR 3.95 % observed 2027-01-19 (on or before the 01-20 reset): the termination fixes the period at 0.03950 and posts the nights 01-19, 01-20, 01-21:
        # receive 01-19 at the first period's 0.0387 (its 92nd and last night: the true-up to the ACT/360 coupon to_c(10,000,000 x 0.0387 x 92/360)
        # = to_c(98,900.00) = 98,900.00 less the 91 x 1,075.00 = 97,825.00 posted = 1,075.00), then 01-20 and 01-21 at 0.0395:
        # to_c(10,000,000 x 0.0395 / 360) = to_c(1,097.222..) = 1,097.22 and to_c(2,194.444..) = 2,194.44 (+1,097.22)
        rt.rates.put("SOFR", [(date(2027, 1, 19), D("3.95"))], persist=False)
        r = execute(rt, "otc.terminate", {"contract_id": SWP1}, "ui:swap-term-0006")
        self.assertTrue(r.ok, r.body)
        fix = one(rt, "SELECT detail_json FROM otc_resets WHERE contract_id=? AND event_date='2027-01-20' AND kind='FIX'", (SWP1,))
        self.assertEqual(("0.03950", "2027-01-19", "termination catch-up"), (json.loads(fix["detail_json"])["rate"], json.loads(fix["detail_json"])["obs_date"],
                                                                           json.loads(fix["detail_json"])["source"]))
        self.assertEqual([107500, 109722, 109722], [lines_of(rt, entry_by_idem(rt, f"accr:swaprcv:{SWP1}:{d}")["entry_id"])[0][2]
                                                    for d in ("2027-01-19", "2027-01-20", "2027-01-21")])
        self.assertEqual(("TERMINATED", "2027-01-21"), (contract(rt)["status"], r.body["accrued_through"]))
        assert_integrity(self, rt, "full")

    # ---- no curve: nothing booked, VM skipped, mark stale --------------------------------------------------------------------------------
    def test_no_curve_skips_and_refuses(self):
        rt = self.start()
        self.scenario()
        self.assertTrue(book(rt).ok)
        rt.curve.clear_fixed_dfs()
        with self.assertRaises(ConflictError) as cm:
            swaps.preview(rt, PB_GS, "IRS", TERMS)
        self.assertEqual("NO_CURVE", cm.exception.code)
        r = book(rt, request_id="ui:swap-book-0002")
        self.assertEqual((409, "NO_CURVE"), (r.http, r.code))
        self.assertEqual(1, len(q(rt, "SELECT 1 FROM otc_contracts")))
        rt.clock.set("2026-10-20T17:10:00-04:00")
        r = vm(rt, "2026-10-20")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("SKIPPED", "NO_CURVE", True), (r.body["status"], r.body["code"], r.body["retry"]))
        att = q(rt, "SELECT severity, code, ref_id, resolved_ts FROM attention WHERE code='NO_CURVE'")
        self.assertEqual([("WARN", "NO_CURVE", f"{SWP1}:2026-10-20", None)], [(a["severity"], a["code"], a["ref_id"], a["resolved_ts"]) for a in att])
        # the mark keeps its last value, labelled stale; nothing was posted; the key stays due
        self.assertEqual(-190386, contract(rt)["mark_c"])
        mv = swaps.mark(rt, SWP1)
        self.assertIsInstance(mv, MarkView)
        self.assertEqual(("stale", "-1903.86", False), (mv.quality, str(mv.mark), mv.executable))
        self.assertEqual([], q(rt, "SELECT 1 FROM otc_vm") + q(rt, "SELECT 1 FROM journal_entries WHERE idem_key LIKE 'swapvm:%'"))
        with rt.db.reader() as conn:
            self.assertEqual([(SWP1, "2026-10-20")], swaps.vm_keys(conn, rt.clock.epoch()))
        with self.assertRaises(ConflictError):
            swaps.dv01_c(rt, SWP1)
        self.assertIsNone(rt.accounting.position(PB_GS, SWP1).extra["dv01_c"])
        # the otc.terminate needs a mark too
        r = execute(rt, "otc.terminate", {"contract_id": SWP1}, "ui:swap-term-0001")
        self.assertEqual((409, "NO_CURVE"), (r.http, r.code))
        # the curve returns: the retry posts the day-1 VM and resolves the attention
        rt.curve.set_fixed_dfs(DAY1_DFS, date(2026, 10, 20))
        r = vm(rt, "2026-10-20", suffix=":r1")
        self.assertTrue(r.ok and r.body["status"] == "DONE", r.body)
        self.assertEqual(-190386, r.body["delta_c"])
        self.assertEqual([], q(rt, "SELECT 1 FROM attention WHERE code='NO_CURVE' AND resolved_ts IS NULL"))
        assert_integrity(self, rt, "full")

    # ---- VM catch-up after downtime: dated by the curve, valued as of the VM date, never backwards ---------------------------------------------
    def test_vm_catch_up_valuation_date_and_superseded(self):
        rt = self.start()
        self.scenario()
        self.assertTrue(book(rt).ok)
        rt.clock.set("2026-10-20T17:10:00-04:00")
        self.assertEqual([(f"{SWP1}:2026-10-20", "DONE")], ran(rt.lifecycle.tick(only="SWAP_VM"), "SWAP_VM"))
        # downtime until Fri 2026-10-23 18:00; the only curve observed meanwhile is the +10 bp curve of Thu 2026-10-22
        rt.curve.set_fixed_dfs(DAY2_DFS, date(2026, 10, 22))
        rt.clock.set("2026-10-23T18:00:00-04:00")
        with rt.db.reader() as conn:
            self.assertEqual([(SWP1, "2026-10-21"), (SWP1, "2026-10-22"), (SWP1, "2026-10-23")], swaps.vm_keys(conn, rt.clock.epoch()))
        # 2026-10-21: the curve available is dated after it -> CAUGHT_UP (quiet, terminal, nothing posted; the series waits for the next tick)
        out = rt.lifecycle.tick(only="SWAP_VM")
        self.assertEqual([(f"{SWP1}:2026-10-21", "SKIPPED")], ran(out, "SWAP_VM"))
        self.assertTrue(one(rt, "SELECT note FROM jobs_done WHERE job='SWAP_VM' AND key=?", (f"{SWP1}:2026-10-21",))["note"].startswith("CAUGHT_UP"))
        # next tick: 2026-10-22 posts the whole move on its own curve (17,114.00 - (-1,903.86) = 19,017.86) and 2026-10-23 finds no change
        out = rt.lifecycle.tick(only="SWAP_VM")
        self.assertEqual([(f"{SWP1}:2026-10-22", "DONE"), (f"{SWP1}:2026-10-23", "SKIPPED")], ran(out, "SWAP_VM"))
        self.assertEqual([("2026-10-20", -190386), ("2026-10-22", 1901786)], [(r["vm_date"], r["delta_c"]) for r in q(rt, "SELECT vm_date, delta_c FROM otc_vm ORDER BY vm_date")])
        self.assertIsNone(entry_by_idem(rt, f"swapvm:{SWP1}:2026-10-21"))
        self.assertEqual([], q(rt, "SELECT 1 FROM attention WHERE resolved_ts IS NULL"))
        with rt.db.reader() as conn:
            self.assertEqual([], swaps.vm_keys(conn, rt.clock.epoch()))
        # a VM never runs backwards: a direct retry of 2026-10-21 is superseded by the 2026-10-22 VM and posts nothing
        r = vm(rt, "2026-10-21", suffix=":r1")
        self.assertEqual(("DONE", "SUPERSEDED", False), (r.body["status"], r.body["code"], r.body["posted"]))
        # the valuation date is the VM date: on 2027-04-20 the VM of 2027-04-19 still counts the period paid on 2027-04-20 (4 periods: the 17,114.00 mark,
        # no change), the VM of 2027-04-20 has 3 left: Σ df(0.5..1.5) of the day-2 curve = 0.97991181 + 0.96022716 + 0.94093744 = 2.88107641,
        # A = 1.440538205, par = 2 x (1 - 0.94093744) / 2.88107641 = 0.11812512 / 2.88107641 = 0.0410003 -> 0.04100,
        # mark = 10,000,000 x (0.04100 - 0.04010) x 1.440538205 = 9,000 x 1.440538205 = 12,964.843845 -> 12,964.84; delta = 12,964.84 - 17,114.00 = -4,149.16
        # -> release VM received Dr 2330 4,149.16 / Cr 1010 4,149.16 (balance 17,114.00 -> 12,964.84)
        rt.clock.set("2027-04-20T17:10:00-04:00")
        r = vm(rt, "2027-04-19")
        self.assertEqual(("SKIPPED", "NO_CHANGE", 1711400), (r.body["status"], r.body["code"], r.body["mark_c"]))
        r = vm(rt, "2027-04-20")
        self.assertEqual(("DONE", 1296484, -414916, 1296484), (r.body["status"], r.body["mark_c"], r.body["delta_c"], r.body["vm_balance_c"]))
        self.assertEqual(("2027-04-20", 3), (r.body["inputs"]["valuation_date"], r.body["inputs"]["remaining_periods"]))
        e = entry_by_idem(rt, f"swapvm:{SWP1}:2027-04-20")
        self.assertEqual([("2330", SWP1, 414916), ("1010", None, -414916)], lines_of(rt, e["entry_id"]))
        self.assertEqual(1296484, bal(rt, PB_GS, "2330", inst=SWP1))
        assert_integrity(self, rt, "full")

    # ---- SOFR compounded in arrears (the 17:07 amendment: "compounded in arrears or term as booked") -----------------------------------------
    def test_compound_arrears_accrual_and_payment_through_the_scheduler(self):
        rt = self.start()
        self.scenario()
        rt.rates.put("SOFR", [(date(2026, 10, 27), D("3.90")), (date(2026, 11, 6), D("3.95"))], persist=False)
        r = book(rt, terms=TERMS_CA)
        self.assertTrue(r.ok, r.body)
        # 6 months = one semi-annual fixed period (the maturity test's figures): mark0 -490.20, IM 24,510.00; settled 100,000 - 24,510 = 75,490.00
        c = contract(rt)
        self.assertEqual((-49020, 2451000, "2027-04-20", 7549000), (c["mark_c"], c["im_c"], c["maturity_date"], bal(rt, PB_GS, "1010")))
        terms = json.loads(c["terms_json"])
        # monthly floating periods, modified following: Nov 20 (Fri), Dec 20 (Sun) -> Dec 21, Jan 20 (Wed), Feb 20 (Sat) -> Feb 22, Mar 20 (Sat) -> Mar 22, Apr 20 (Tue)
        self.assertEqual(["2026-11-20", "2026-12-21", "2027-01-20", "2027-02-22", "2027-03-22", "2027-04-20"], [p[2] for p in terms["float_periods"]])
        # no reset fixing for a compounded leg (nothing in otc_resets; SWAP_FIX answers a no-op)
        self.assertEqual(("COMPOUND_ARREARS", []), (terms["fixing"], q(rt, "SELECT 1 FROM otc_resets")))
        r = execute(rt, "swap.fix", {"contract_id": SWP1, "date": "2026-10-20", "key": f"{SWP1}:2026-10-20"}, f"job:SWAP_FIX:{SWP1}:2026-10-20", actor="lifecycle")
        self.assertEqual(("DONE", "COMPOUND_ARREARS"), (r.body["status"], r.body["code"]))
        # 2026-11-20 00:50: ACCRUE_DAILY owes the nights 10-20 .. 11-19.  Floating receive leg, compounded daily at the latest SOFR on or before each night:
        # 10-20..10-26 at 3.87 (obs 10-19), 10-27..11-05 at 3.90 (obs 10-27), 11-06..11-16 at 3.95 (obs 11-06); 11-17 is 11 days after the last print ->
        # SKIPPED RATE_UNAVAILABLE (never defaulted) and the leg's later nights wait.  The fixed pay leg needs no rate: all 31 nights post.
        out = rt.lifecycle.tick(at="2026-11-20T00:50:00-05:00", only="ACCRUE_DAILY")
        rcv = [(k, s) for k, s in ran(out, "ACCRUE_DAILY") if k.startswith("swaprcv")]
        self.assertEqual((28, (f"swaprcv:{SWP1}:2026-11-17", "SKIPPED")), (len([1 for _k, s in rcv if s == "DONE"]), rcv[-1]))
        self.assertEqual(31, len([1 for k, s in ran(out, "ACCRUE_DAILY") if k.startswith("swappay") and s == "DONE"]))
        # nights 1..3 at 3.87 %: F = (1 + 0.0387/360)^n = 1.0001075^n; cumulative to_c(10,000,000 x (F - 1)): 1,075.00; 1.00021501155625 -> 2,150.12 (+1,075.12);
        # 1.000322534669992296875 -> 3,225.35 (+1,075.23) -> Dr 1225 / Cr 4453
        for night, amount in (("2026-10-20", 107500), ("2026-10-21", 107512), ("2026-10-22", 107523)):
            e = entry_by_idem(rt, f"accr:swaprcv:{SWP1}:{night}")
            self.assertEqual([("1225", SWP1, amount), ("4453", SWP1, -amount)], lines_of(rt, e["entry_id"]), night)
        # after 28 nights: to_c(10,000,000 x (1.0001075^7 x (1 + 0.039/360)^10 x (1 + 0.0395/360)^11 - 1)) = 30,472.46 (exact rationals);
        # fixed leg 31 nights: to_c(10,000,000 x 0.0401 x 31/360) = to_c(34,530.5556) = 34,530.56
        self.assertEqual((3047246, 3453056), (bal(rt, PB_GS, "1225", inst=SWP1), bal(rt, PB_GS, "2340", inst=SWP1)))
        st = one(rt, "SELECT * FROM accrual_state WHERE kind='swaprcv' AND ref_id=?", (SWP1,))
        self.assertEqual(("2026-10-20", "2026-11-16", 3047246), (st["segment_start"], st["accrued_through"], st["cum_c"]))
        self.assertEqual(1, len(q(rt, "SELECT 1 FROM attention WHERE code='RATE_UNAVAILABLE' AND ref_id=? AND resolved_ts IS NULL", (f"ACCRUE_DAILY:swaprcv:{SWP1}:2026-11-17",))))
        # the 2026-11-20 payment waits for the floating leg
        out = rt.lifecycle.tick(only="SWAP_PAY")
        self.assertEqual([(f"{SWP1}:2026-11-20", "SKIPPED")], ran(out, "SWAP_PAY"))
        # the 2026-11-13 print arrives (3.95): nights 11-17, 11-18, 11-19 compound -> cumulative 31,573.02 (+1,100.56), 32,673.71 (+1,100.69),
        # 33,774.52 (+1,100.81) = to_c(10,000,000 x (1.0001075^7 x (1 + 0.039/360)^10 x (1 + 0.0395/360)^14 - 1)), F = 1.0033774518...
        rt.rates.put("SOFR", [(date(2026, 11, 13), D("3.95"))], persist=False)
        out = rt.lifecycle.tick(at="2026-11-20T01:00:00-05:00", only="ACCRUE_DAILY")
        self.assertEqual([(f"swaprcv:{SWP1}:2026-11-1{d}", "DONE") for d in (7, 8, 9)], [(k, s) for k, s in ran(out, "ACCRUE_DAILY") if k.startswith("swaprcv")])
        for night, amount in (("2026-11-17", 110056), ("2026-11-18", 110069), ("2026-11-19", 110081)):
            e = entry_by_idem(rt, f"accr:swaprcv:{SWP1}:{night}")
            self.assertEqual([("1225", SWP1, amount), ("4453", SWP1, -amount)], lines_of(rt, e["entry_id"]), night)
        self.assertEqual(3377452, bal(rt, PB_GS, "1225", inst=SWP1))
        self.assertEqual([], q(rt, "SELECT 1 FROM attention WHERE code='RATE_UNAVAILABLE' AND resolved_ts IS NULL"))
        # the payment: only the floating leg pays on 2026-11-20 (the fixed leg pays on 2027-04-20): receive 33,774.52 = the period's posted accruals ->
        # Cr 1225 33,774.52 / Dr 1010 33,774.52; realised rate = 33,774.52 x 360 / (10,000,000 x 31) = 0.039222023 -> 0.03922202
        out = rt.lifecycle.tick(only="SWAP_PAY")
        self.assertEqual([(f"{SWP1}:2026-11-20", "DONE")], ran(out, "SWAP_PAY"))
        e = entry_by_idem(rt, f"swappay:{SWP1}:2026-11-20")
        self.assertEqual([("1225", SWP1, -3377452), ("1010", None, 3377452)], lines_of(rt, e["entry_id"]))
        paid = json.loads(one(rt, "SELECT detail_json FROM otc_resets WHERE kind='PAY'")["detail_json"])
        self.assertEqual((3377452, 0, ["float"], "0.03922202", "COMPOUND_ARREARS"), (paid["receive_c"], paid["pay_c"], list(paid["legs"]), paid["legs"]["float"]["rate"],
                                                                                  paid["legs"]["float"]["fixing"]))
        # settled 75,490.00 + 33,774.52 = 109,264.52; 1225 = 0; the fixed leg keeps accruing toward April (2340 = 34,530.56)
        self.assertEqual((10926452, 0, 3453056), (bal(rt, PB_GS, "1010"), bal(rt, PB_GS, "1225", inst=SWP1), bal(rt, PB_GS, "2340", inst=SWP1)))
        with rt.db.reader() as conn:
            self.assertEqual(([], []), (swaps.pay_keys(conn, rt.clock.epoch()), swaps.fix_keys(conn, rt.clock.epoch())))
        # the next period starts afresh: night 11-20 at 3.95 % (obs 11-13): F = 1 + 0.0395/360 = 1.000109722222222222 -> to_c(1,097.2222) = 1,097.22;
        # the view's compounding state: 1 night, rate so far (F - 1) x 360 / 1 = 0.0395
        out = rt.lifecycle.tick(at="2026-11-21T00:40:00-05:00", only="ACCRUE_DAILY")
        self.assertIn((f"swaprcv:{SWP1}:2026-11-20", "DONE"), ran(out, "ACCRUE_DAILY"))
        e = entry_by_idem(rt, f"accr:swaprcv:{SWP1}:2026-11-20")
        self.assertEqual([("1225", SWP1, 109722), ("4453", SWP1, -109722)], lines_of(rt, e["entry_id"]))
        v = swaps.contract_view(rt, SWP1)
        self.assertEqual(("COMPOUND_ARREARS", "2026-11-20", "2026-12-21", 1, 109722, "0.03950000", "1.000109722222222222"),
                         (v["fixing_method"], v["fixing"]["period_start"], v["fixing"]["period_end"], v["fixing"]["nights"], v["fixing"]["accrued_c"], v["fixing"]["rate_so_far"],
                          v["fixing"]["factor"]))
        # next events: the 2026-12-21 floating amount is unknown until it has compounded
        ev = swaps.next_events(rt, SWP1)
        self.assertEqual([("2026-12-21", "PAY", [("float", None, False)]), ("2027-01-20", "PAY", [("float", None, False)]), ("2027-04-20", "MATURITY", None)],
                         [(x["date"], x["event"], [(l["leg"], l["amount_c"], l["known"]) for l in x["legs"]] if "legs" in x else None) for x in ev])
        assert_integrity(self, rt, "full")

    # ---- a weekend booking at a month end starts on the next business day -------------------------------------------------------------------
    def test_weekend_booking_starts_following(self):
        rt = self.start(at="2026-10-31T11:00:00-04:00")
        rt.rates.put("SOFR", [(date(2026, 10, 30), D("3.88"))], persist=False)
        self.scenario()
        # Sat 2026-10-31: the effective date rolls FOLLOWING to Mon 2026-11-02 (modified following would have gone back to Fri 10-30, before the trade);
        # ends start + 6k months, modified following: 2027-05-02 (Sun) -> 2027-05-03, 2027-11-02 (Tue), 2028-05-02 (Tue), 2028-11-02 (Thu)
        p = swaps.preview(rt, PB_GS, "IRS", TERMS)
        self.assertEqual(("2026-11-02", "2028-11-02"), (p["start_date"], p["maturity_date"]))
        self.assertEqual(["2027-05-03", "2027-11-02", "2028-05-02", "2028-11-02"], [r["date"] for r in p["schedule"]])
        # 30/360: Nov 2 -> May 3 = 360 - 6 x 30 + 1 = 181, May 3 -> Nov 2 = 6 x 30 - 1 = 179, then 180 and 180;
        # actual: 28+31+31+28+31+30+3 = 182, 28+30+31+31+30+31+2 = 183, 28+31+31+29+31+30+2 = 182, 29+30+31+31+30+31+2 = 184
        self.assertEqual(([181, 179, 180, 180], [182, 183, 182, 184]), ([r["fixed_days"] for r in p["schedule"]], [r["float_days"] for r in p["schedule"]]))
        # the first fixing: the latest SOFR on or before 2026-11-02 = 3.88 (2026-10-30) -> 0.03880
        self.assertEqual(("0.03880", "2026-10-30", "2026-11-02"), (p["fixing"]["rate"], p["fixing"]["obs_date"], p["fixing"]["reset_date"]))
        self.assertTrue(book(rt).ok)
        self.assertEqual(("2026-11-02", "2026-11-02"), (contract(rt)["start_date"], json.loads(contract(rt)["terms_json"])["float_periods"][0][0]))
        self.assertEqual("2026-10-31", json.loads(contract(rt)["terms_json"])["trade_date"])
        # no VM and no accrual before the start: Sat 17:10 nothing is due; Mon 2026-11-02 17:10 (EST) the first VM is
        rt.clock.set("2026-10-31T17:10:00-04:00")
        with rt.db.reader() as conn:
            self.assertEqual([], swaps.vm_keys(conn, rt.clock.epoch()))
        out = rt.lifecycle.tick(at="2026-11-02T17:10:00-05:00", only="ACCRUE_DAILY")
        self.assertEqual([], [k for k, s in ran(out, "ACCRUE_DAILY") if k.startswith("swap")])
        with rt.db.reader() as conn:
            self.assertEqual([(SWP1, "2026-11-02")], swaps.vm_keys(conn, rt.clock.epoch()))
        assert_integrity(self, rt, "full")

    def test_booking_after_the_cut_off_margins_from_its_business_day(self):
        """P8R1-02: a live contract booked after the 17:00 ET cut-off of Tue 10-20 belongs to Wed 10-21: its IM is dated 10-21 and so is its
        first variation margin - never a VM of 10-20 posted after that day closed (the old first VM date was the start, the calendar date)."""
        rt = self.start()
        self.scenario()
        rt.clock.set("2026-10-20T21:08:00-04:00")                                     # Tue 21:08 ET: after the cut-off (and after the 17:05 VM time)
        r = book(rt)
        self.assertTrue(r.ok, r.body)
        c = contract(rt)
        # the contract still starts on its calendar date (the schedule is unchanged); its IM 95,193.00 (|DV01| 1,903.86 x 50 bp) carries business
        # date 10-21, the booking's accounting day, paid from the account's own 100,000.00 (no sweep)
        self.assertEqual(("2026-10-20", "2026-10-20"), (c["start_date"], json.loads(c["terms_json"])["trade_date"]))
        im = entry_by_idem(rt, f"im:{SWP1}:open")
        self.assertEqual(("2026-10-21", [("1330", SWP1, 9519300), ("1010", None, -9519300)]), (im["business_date"], lines_of(rt, im["entry_id"])))
        self.assertEqual(date(2026, 10, 21), swaps.vm_first_date(c))
        self.assertEqual(date(2026, 10, 21), swaps.booked_business_day(c))
        # at 21:09 nothing is due (the old rule listed (SWP-000001, 2026-10-20) and posted the -1,903.86 day-one mark dated the closed 10-20);
        # a VM asked for 10-20 is refused, nothing is written
        rt.clock.set("2026-10-20T21:09:00-04:00")
        with rt.db.reader() as conn:
            self.assertEqual([], swaps.vm_keys(conn, rt.clock.epoch()))
        self.assertEqual([], ran(rt.lifecycle.tick(only="SWAP_VM"), "SWAP_VM"))
        bad = vm(rt, "2026-10-20")
        self.assertEqual((400, "KEY_INVALID"), (bad.http, bad.code))
        self.assertEqual([], q(rt, "SELECT 1 FROM otc_vm"))
        self.assertIsNone(entry_by_idem(rt, f"swapvm:{SWP1}:2026-10-20"))
        # Wed 10-21 17:10: the first VM, on the curve available (the 10-19 one, within C12's 3 business days): par 0.04000 vs dealt 0.04010 ->
        # mark = 10,000,000 x (0.04000 - 0.04010) x 1.90386435 = -1,903.86, reference 0 -> Dr 1320 1,903.86 / Cr 1010 1,903.86, business date
        # 10-21 = the IM's: one booking, one business date
        rt.clock.set("2026-10-21T17:10:00-04:00")
        with rt.db.reader() as conn:
            self.assertEqual([(SWP1, "2026-10-21")], swaps.vm_keys(conn, rt.clock.epoch()))
        out = rt.lifecycle.tick(only="SWAP_VM")
        self.assertEqual([(f"{SWP1}:2026-10-21", "DONE")], ran(out, "SWAP_VM"))
        e = entry_by_idem(rt, f"swapvm:{SWP1}:2026-10-21")
        self.assertEqual(("2026-10-21", [("1320", SWP1, 190386), ("1010", None, -190386)]), (e["business_date"], lines_of(rt, e["entry_id"])))
        self.assertEqual([("2026-10-21", -190386, -190386)], [(r["vm_date"], r["mark_c"], r["delta_c"]) for r in q(rt, "SELECT * FROM otc_vm")])
        # cash: 100,000.00 - 95,193.00 IM - 1,903.86 VM = 2,903.14
        self.assertEqual(290314, bal(rt, PB_GS, "1010"))
        assert_integrity(self, rt, "full")

    def test_vm_first_date_rule(self):
        """P8R1-02: the first VM date of each kind of row - a live booking's accounting day when it is later than the start; an as-of
        booking's value date whenever it was entered (D7); an XCCY's trade date (P8B-06); a row without a booking time keeps its start."""
        def row(kind="TRS", start="2026-10-28", booking=None, entered=None, terms=None):
            return {"kind": kind, "start_date": start, "booking": booking, "entered_ts": entered, "terms_json": json.dumps(terms or {})}
        ts = lambda s: int(datetime.fromisoformat(s).timestamp())                                                         # noqa: E731
        self.assertEqual(date(2026, 10, 28), swaps.vm_first_date(row(entered=ts("2026-10-28T16:59:59-04:00"))))          # before the cut-off: the start
        self.assertEqual(date(2026, 10, 28), swaps.vm_first_date(row(entered=ts("2026-10-28T17:00:00-04:00"))))          # 17:00:00 is still day D
        self.assertEqual(date(2026, 10, 29), swaps.vm_first_date(row(entered=ts("2026-10-28T17:05:51-04:00"))))          # SWP-000006's booking time
        self.assertEqual(date(2026, 10, 29), swaps.vm_first_date(row(terms={"dealt_ts": ts("2026-10-28T21:08:45-04:00")})))   # 1.0.5 rows: dealt_ts
        self.assertEqual(date(2026, 10, 28), swaps.vm_first_date(row()))                                                    # no booking time recorded
        # Fri 10-30 18:00 ET: the accounting day is Sat 10-31 (the IM's business date); Saturday's VM measures nothing new
        self.assertEqual(date(2026, 10, 31), swaps.vm_first_date(row(start="2026-10-30", entered=ts("2026-10-30T18:00:00-04:00"))))
        # a Saturday booking starts Monday (following) and margins from Monday
        self.assertEqual(date(2026, 11, 2), swaps.vm_first_date(row(start="2026-11-02", entered=ts("2026-10-31T11:00:00-04:00"))))
        # an as-of booking keeps its value date: the owner's TRS, dealt 10-21 19:45 ET (accounting day 10-22) and entered 10-28 09:57 ET
        asof = row(start="2026-10-21", booking="AS_OF", entered=ts("2026-10-28T09:57:39-04:00"), terms={"dealt_ts": ts("2026-10-21T19:45:00-04:00")})
        self.assertEqual(date(2026, 10, 21), swaps.vm_first_date(asof))
        self.assertIsNone(swaps.booked_business_day(asof))
        self.assertEqual(date(2026, 10, 21), swaps.vm_first_date(dict(asof, booking="HISTORICAL_MODEL")))
        # an XCCY dealt after the FX roll: trade date 10-29, spot start 11-02 -> margined from 10-29 (the booking's accounting day too)
        x = row(kind="XCCY", start="2026-11-02", entered=ts("2026-10-28T21:09:15-04:00"), terms={"trade_date": "2026-10-29"})
        self.assertEqual(date(2026, 10, 29), swaps.vm_first_date(x))

    # ---- eligibility and tradeability (books="real": P1 / F1) -------------------------------------------------------------------------
    def test_not_eligible_in_p1_not_tradeable_outside_pb_and_other_kinds(self):
        rt = self.start(books_kind="real")
        # P1 (1,000,000.00) gets a PB account the WP-23 way; its lifetime is below the ECP floor and the threshold -> NOT_ELIGIBLE
        r = execute(rt, "test.open_pb_unchecked", {"lifetime_id": "P1", "broker": "GS"})
        self.assertTrue(r.ok, r.body)
        r = book(rt, account="P1:PB_GS")
        self.assertEqual((400, "NOT_ELIGIBLE"), (r.http, r.code))
        self.assertIn("ECP", r.body["error"]["message"])
        with self.assertRaises(RejectError) as cm:
            swaps.preview(rt, "P1:PB_GS", "IRS", TERMS)
        self.assertEqual("NOT_ELIGIBLE", cm.exception.code)
        # the gate is common to every OTC kind: a TRS in P1 is NOT_ELIGIBLE too (not NOT_BUILT)
        r = book(rt, account="P1:PB_GS", kind="TRS", request_id="ui:swap-book-p1-trs")
        self.assertEqual((400, "NOT_ELIGIBLE"), (r.http, r.code))
        # a non-PB account of an eligible lifetime -> NOT_TRADEABLE_HERE (checked before eligibility)
        r = book(rt, account="F1:US_EXCHANGE")
        self.assertEqual((400, "NOT_TRADEABLE_HERE"), (r.http, r.code))
        r = book(rt, account="F1:NOPE")
        self.assertEqual(404, r.http)
        r = execute(rt, "account.open_pb", {"lifetime_id": "F1", "broker": "GS"})
        self.assertTrue(r.ok, r.body)
        self.assertTrue(fund(rt, "F1:PB_GS", 10000000).ok)
        # other OTC kinds while F1 sits exactly at its 25,000,000.00 threshold: options / FRAs / caps are NOT IN THIS BUILD; TRS / XCCY are WP-26
        # (501 NOT_BUILT) until bound; anything else 400 OTC_KIND
        for kind, http_, code in (("OPTION", 400, "NOT_IN_THIS_BUILD"), ("FRA", 400, "NOT_IN_THIS_BUILD"), ("TRS", 501, "NOT_BUILT"), ("XCCY", 501, "NOT_BUILT"), ("BANANA", 400, "OTC_KIND")):
            r = book(rt, account="F1:PB_GS", kind=kind, request_id=f"ui:swap-book-{kind.lower()}")
            self.assertEqual((http_, code), (r.http, r.code), kind)
        with self.assertRaises(RejectError) as cm:
            swaps.preview(rt, "F1:PB_GS", "TRS", {"underlying_id": "AAPL", "units": 100})
        self.assertEqual((501, "NOT_BUILT"), (cm.exception.http, cm.exception.code))
        # the WP-26 hook: a bound swaps_trs module receives the TRS preview / booking after the common gate
        rt.swaps_trs = types.SimpleNamespace(preview=lambda services, aid, kind, terms: {"kind": kind, "hooked": True}, book=lambda ctx, payload: {"hooked": payload["kind"]})
        self.assertEqual({"kind": "TRS", "hooked": True}, swaps.preview(rt, "F1:PB_GS", "TRS", {}))
        r = book(rt, account="F1:PB_GS", kind="XCCY", request_id="ui:swap-book-xccy-hook")
        self.assertEqual({"hooked": "XCCY"}, r.body)
        rt.swaps_trs = None
        # F1 previews and books the §19.15 numbers under the design's own name: NAV(F1:PB_GS) 98,096.14
        p = swaps.preview(rt, "F1:PB_GS", "IRS", TERMS)
        self.assertEqual(("0.04000", "0.04010", -190386, 190386, 9519300), (p["par_rate"], p["dealt_rate"], p["mark0_c"], p["dv01_c"], p["im_c"]))
        r = book(rt, account="F1:PB_GS")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(9809614, rt.accounting.nav("F1:PB_GS").nav_c)
        assert_integrity(self, rt, "full")
        # the cost of dealing takes F1 to 25,000,000 - 1,903.86 = 24,998,096.14, below the threshold: the Fund Book stays eligible BY PROFILE
        # (design 13.10 / BM-03: it started at the 25,000,000.00 threshold - eligible from day one), so the next deal is not refused NOT_ELIGIBLE
        self.assertTrue(rt.books.eligibility(rt, "F1").pb_otc)
        r = book(rt, account="F1:PB_GS", request_id="ui:swap-book-f1-2")
        self.assertNotEqual("NOT_ELIGIBLE", r.code, r.body)

    # ---- the ticket renders from the fixture server --------------------------------------------------------------------------------------
    def test_swap_ticket_component_and_fixtures(self):
        path = os.path.join(STATIC, "components", "swapticket.js")
        with open(path, "rb") as fh:
            raw = fh.read()
        src = raw.decode("ascii")                                                          # ASCII only (the static test's rule)
        self.assertIn("T.components.swapticket = ", src)
        self.assertEqual((0, 0, 0), js_balance(src))
        for needle in ("/api/otc/preview", "/api/otc/contracts", "/terminate", "T.api.post", "T.api.get", "T.ui.modal", "SWAP_MARK", "COMPOUND_ARREARS",
                       "fixed_basis", "next_events", "dv01"):
            self.assertIn(needle, src)
        self.assertNotRegex(src, r"https?://")
        # the fixture server serves the real component (not its stub) and the OTC fixtures the component reads
        server = fx.FixtureServer().start()
        try:
            def req(method, p, body=None):
                c = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
                c.request(method, p, body=None if body is None else json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
                resp = c.getresponse()
                data = resp.read()
                hdrs = {k.lower(): v for k, v in resp.getheaders()}
                c.close()
                return resp.status, data, hdrs
            status, data, hdrs = req("GET", "/static/components/swapticket.js")
            self.assertEqual((200, None), (status, hdrs.get("x-fixture-stub")))
            self.assertIn(b"T.components.swapticket", data)
            status, data, _h = req("GET", "/api/otc/contracts?account_id=P2:PB_GS")
            self.assertEqual((200, []), (status, json.loads(data)))
            status, data, _h = req("POST", "/api/otc/preview", {"account_id": "P2:PB_GS", "kind": "IRS", "terms": {}})
            self.assertEqual(200, status)
            fixture = json.loads(data)
        finally:
            server.stop()
        # every key the fixture (and so the ticket) reads exists in the real, encoded preview of the §19.15 deal
        rt = self.start()
        self.scenario()
        real = _api.encode(swaps.preview(rt, PB_GS, "IRS", TERMS))
        self.assertEqual(set(), set(fixture) - set(real))
        self.assertEqual(set(), set(fixture["schedule"][0]) - set(real["schedule"][0]))
        self.assertEqual(("IRS", "CTM"), (real["kind"], real["margin_mode"]))
        self.assertTrue(book(rt).ok)
        view = _api.encode(swaps.contracts_view(rt, account_id=PB_GS, detail=True))[0]
        for key in ("contract_id", "kind", "side", "notional", "fixed_rate", "maturity_date", "mark", "dv01", "im_balance", "vm_balance", "accrued_receive", "accrued_pay",
                    "next_fix_date", "next_pay_date", "status", "margin_mode", "history", "fixing", "result", "next_events", "fixing_method"):
            self.assertIn(key, view, key)
        self.assertEqual(("-1903.86", "computed", "1903.86", "95193.00"), (view["mark"]["value"], view["mark"]["quality"], view["dv01"], view["im_balance"]))
        self.assertEqual(("200500.00", "195650.00"), tuple(l["amount"] for l in view["next_events"][1]["legs"]))
        # the ticket's own money / rate conversions (string arithmetic) are pinned here by their JS source
        self.assertIn("function pctToFraction", src)
        self.assertIn("function toCents", src)


if __name__ == "__main__":
    unittest.main()
