"""FX_CCS_PLAN rev 2 (release 1.0.6.1, package P-C) money tests of the EUR/USD ESTR / SOFR float / float cross-currency swap (Trade A), the VM on
the CSA exposure (D8), the owner reference (D13), the RFQ path, the termination in USD (D17), the VM key start (G16 / G17) and the TRS
stale-listing guard (INSTITUTIONAL_V3_PLAN 6.1 R-D).

Every expected number is worked by hand in a comment from the inputs; the curve arithmetic follows FX_CCS_PLAN D2 / 3.3 exactly and was re-derived
apart from the code by the designer's exact-Decimal scripts (scratchpad fxd/scripts common4.py + worked4.py: the same digits) - nothing is pasted from
the code's output.  No network: FakeClock, FixedMarks (EURUSD 1.1158, GBPUSD 1.3323: the 10-28 17:42 EDT prints), the worked example's curves of
2026-10-27 (``fxcurves.fixture_curves``: the BoE 1M..60M row, the ECB AAA spot rates, the Treasury par row; SONIA 3.7328 %, ESTR 2.440 %, SOFR 3.95 %),
a temp database, books="real" (F1:PB_GS; F1 Treasury USD 25,000,000.00).

The worked example (FX_CCS_PLAN 3.3; T = Thu 2026-10-29, the FX trade date of a 10:00 ET booking):
- dates: S = the EURUSD spot date Mon 2026-11-02 (T+1 Fri 10-30 is a TARGET2 day; T+2 skips the weekend); e_k = MF(S + 3k months) on TARGET2 + New
  York: 2027-02-02, 05-03 (05-02 is a Sunday), 08-02, 11-02, 2028-02-02, 05-02, 08-02, 11-02; p_k = e_k + 2 business days: 2027-02-04, 05-05, 08-04,
  11-04, 2028-02-04, 05-04, 08-04, 11-06 (11-04 / 11-05 is a weekend); termination e_8 = 2028-11-02, maturity (last payment + final exchange) p_8.
- notionals: EUR 2,000,000.00; USD = to_c(2,000,000 x 1.1158) = 2,231,600.00.
- zero curves (% cc, t = days / 365 from 2026-10-27): USD first node SOFR 365 x ln(1 + 0.0395 / 360) = 4.004641 %, then the Treasury bootstrap
  (0.5y 4.293581 %, 1y 4.451884 %, 1.5y 4.551366 %, 2y 4.651789 %, 2.5y 4.701837 %); EUR first node ESTR 365 x ln(1 + 0.0244 / 360) = 2.473805 %,
  then the ECB AAA rates (3M 2.542643 % ... 2Y 3.015032 %, 3Y 3.051054 %); linear in z; DF(a, b) = exp(-z(t_b) t_b + z(t_a) t_a), unquantised.
- PV(0) (the full value at spread 0 for value S): USD leg (coupons N x (1 / DF(s, e) - 1) at p_k, the notional back at p_8, minus the notional
  lent at S) -1,139.66; EUR leg in EUR +659.34 x 1.1158; total -403.98 = -370.75 (the final exchange paid on 2028-11-06, not on 11-02: USD back
  4 days later at about 4.65 %, EUR 4 days later at about 3.0 %) + -33.23 (the coupon payment delays).
- A* = sum over k of (d_k / 360) x DF*_EUR(S, p_k) = 1.9646274388; X0 x N x A* x 1 bp = 1.1158 x 2,000,000 x 1.9646274388 x 0.0001 = 438.43.
- s_par = PV(0) / (X0 N A*) = -403.977 / 4,384,262.59 = -0.0000921426 = -0.9214 bp; dealt = -0.9214 + 1.0 (GS half-width x 1) = +0.0786 bp,
  rounded UP on the 0.1 bp grid (the Fund pays the EUR leg: against it) = +0.1 bp.
- mark0 = PV(0) - X0 N A* x 0.1 bp = -403.98 - 43.84 = -447.82 (the half-width 438.43 + the rounding 0.0214 bp x 438.43 = 9.39): the cost of dealing.
- IM = 2 % (GS xccy_im_pct) x 2,231,600.00 = 44,632.00.  Conversion (D6): SELL EUR 2,000,000.00 at the bid 1.1158 - 0.2 pip = 1.11578 =
  USD 2,231,560.00; spread cost 2,231,600.00 - 2,231,560.00 = 40.00, the start-date net USD.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import date, timedelta
from decimal import Decimal

from tests.terminal_helpers import assert_integrity, bal, execute, make_runtime
from finsim.terminal import ccycal, fx, fxcurves, lifecycle, prime
from finsim.terminal.clock import epoch_of
from finsim.terminal.commands import ConflictError, RejectError
from finsim.terminal.products import swaps, swaps_trs
from finsim.terminal.quotes.rates import CurveStore, RateStore

D = Decimal
PB = "F1:PB_GS"
TRE = "F1:TREASURY"
AT = "2026-10-29T10:00:00-04:00"
MARKS = {"EURUSD": ("1.1158", "delayed"), "GBPUSD": ("1.3323", "delayed")}
CID = "SWP-000001"                    # the first swap of a fresh database
TERMS = {"pay_ccy": "EUR", "recv_ccy": "USD", "pay_notional": "2000000.00", "pay_index": "ESTR", "recv_index": "SOFR", "months": 24, "mtm_reset": False,
         "start_conversion": True, "settlement_funding": {"EUR": "BUY_WITH_USD"}}
S = date(2026, 11, 2)
ENDS = ["2027-02-02", "2027-05-03", "2027-08-02", "2027-11-02", "2028-02-02", "2028-05-02", "2028-08-02", "2028-11-02"]
PAYS = ["2027-02-04", "2027-05-05", "2027-08-04", "2027-11-04", "2028-02-04", "2028-05-04", "2028-08-04", "2028-11-06"]


def runtime(tmp, at=AT, marks=None):
    rt = make_runtime(tmp, at=at, books="real", marks=dict(MARKS, **(marks or {})))
    prime.register(rt.bus)
    prime.bind(rt)
    rt.rates = RateStore()
    rt.curve = CurveStore()
    fxcurves.fixture_curves(rt, persist=False)
    swaps.register(rt.bus, rt)
    rt.swaps = swaps
    swaps_trs.register(rt.bus, rt)
    rt.swaps_trs = swaps_trs
    fx.register(rt.bus)
    fx.bind(rt)
    rt.fx = fx
    swaps.bind(rt)
    lifecycle.register(rt.bus, rt)
    rt.lifecycle = lifecycle.LifecycleScheduler(rt.bus, rt.db, rt, rt.clock, rt.log)
    r = execute(rt, "account.open_pb", {"lifetime_id": "F1", "broker": "GS"})
    assert r.ok, r.body
    return rt


def q(rt, sql, params=()):
    with rt.db.reader() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(rt, sql, params=()):
    rows = q(rt, sql, params)
    return rows[0] if rows else None


def contract(rt, cid=CID):
    return one(rt, "SELECT * FROM otc_contracts WHERE contract_id=?", (cid,))


def terms_of(rt, cid=CID):
    return json.loads(contract(rt, cid)["terms_json"])


def lines_by_idem(rt, idem, base=False):
    e = one(rt, "SELECT entry_id FROM journal_entries WHERE idem_key=?", (idem,))
    assert e is not None, idem
    rows = q(rt, "SELECT gl, currency, instrument_id, amount_c, base_c FROM journal_lines WHERE entry_id=? ORDER BY line_no", (e["entry_id"],))
    return [(r["gl"], r["currency"], r["instrument_id"], r["amount_c"]) + ((r["base_c"],) if base else ()) for r in rows]


def book(rt, terms=None, request_id="ui:xccyf-book-0001", **extra):
    t = dict(TERMS, **(terms or {}))
    t.update(extra)
    return execute(rt, "otc.book", {"account_id": PB, "kind": "XCCY", "terms": t}, request_id)


def vm(rt, ds, cid=CID, suffix=""):
    return execute(rt, "swap.vm", {"contract_id": cid, "date": ds, "key": f"{cid}:{ds}"}, f"job:SWAP_VM:{cid}:{ds}{suffix}", actor="lifecycle")


def event(rt, ds, ev, cid=CID, suffix=""):
    key = f"{cid}:{ds}:{ev}"
    return execute(rt, "xccy.exchange", {"key": key}, f"job:XCCY_EVENTS:{key}{suffix}", actor="lifecycle")


def accrue(rt, kind, night, cid=CID, suffix=""):
    key = f"{kind}:{cid}:{night}"
    return execute(rt, "lifecycle.accrue", {"key": key, "kind": kind, "ref": cid, "night": night}, f"job:ACCRUE_DAILY:{key}{suffix}", actor="lifecycle")


def set_fx(rt, px, d=None, hhmm=None, quality="delayed"):
    """EURUSD ``px`` observed at ``d`` ``hhmm`` ET (default: now)."""
    qt = rt.clock.epoch() if d is None else epoch_of(d, hhmm or "17:00")
    rt.marks.set("EURUSD", px, quality, qt)


def flat_prints(rt, start: date, end: date):
    """FX_CCS_PLAN C-T6: a flat 3.95 % SOFR on every US government securities day and a flat 2.440 % ESTR on every TARGET2 day of [start, end], and
    their compounded indices built from them with weekend / holiday weights (I(next) = I(d) x (1 + r x n / 360), n = the calendar days to the next
    publication day; base 1 on ``start``), unrounded - so the index ratio equals the daily compounding the EUR fallback does."""
    for series, idx, rate, cal in (("SOFR", "SOFR_INDEX", D("3.95"), ccycal.USGS), ("ESTR", "ESTR_INDEX", D("2.440"), ccycal.TARGET)):
        prints, levels = [], []
        d = start if cal.is_business_day(start) else cal.next_business_day(start)
        level = D(1)
        while d <= end:
            nxt = cal.next_business_day(d)
            prints.append((d, rate))
            levels.append((d, level))
            level = level * (1 + rate / 100 * D((nxt - d).days) / D(360))
            d = nxt
        levels.append((d, level))
        rt.rates.put(series, prints, f"test fixture (flat {rate} %)", persist=False)
        rt.rates.put(idx, levels, f"test fixture ({idx} from the flat prints)", persist=False)


class FloatXccyBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-xccy-float-")
        self.rt = None

    def tearDown(self):
        if self.rt is not None:
            self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start(self, **kw):
        self.rt = runtime(self.tmp, **kw)
        return self.rt

    def booked(self, **terms):
        rt = self.rt or self.start()
        r = book(rt, terms)
        self.assertTrue(r.ok, r.body)
        return r

    def run_start(self):
        """The XCCY_EVENTS START key at 00:50 ET of S."""
        rt = self.rt
        rt.clock.set("2026-11-02T00:50:00-05:00")
        r = event(rt, "2026-11-02", "START")
        self.assertTrue(r.ok, r.body)
        self.assertEqual("DONE", r.body["status"], r.body)
        return r


class FloatTermsTests(FloatXccyBase):
    # ---- C-T1: parse and dates -------------------------------------------------------------------------------------------------------
    def test_ct1_parse_and_dates(self):
        rt = self.start()
        p = swaps_trs.parse_xccy_terms(TERMS)
        self.assertEqual(("FLOAT_FLOAT", "ESTR", "SOFR", "pay", True, 24, False), (p["variant"], p["pay_index"], p["recv_index"], p["eur_side"],
                                                                               p["start_conversion"], p["months"], p["mtm_reset"]))
        start, periods = swaps_trs.float_dates(rt, "EUR", date(2026, 10, 29), 24)
        self.assertEqual(S, start)
        self.assertEqual(ENDS, [e.isoformat() for _s, e, _p in periods])
        self.assertEqual(PAYS, [p.isoformat() for _s, _e, p in periods])
        self.assertEqual(["2026-11-02"] + ENDS[:-1], [s.isoformat() for s, _e, _p in periods])
        # T = Fri 10-30: S = Tue 11-03; ends 2027-02-03, 05-03, 08-03, 11-03, 2028-02-03, 05-03, 08-03, 11-03; payments +2 business days
        start2, periods2 = swaps_trs.float_dates(rt, "EUR", date(2026, 10, 30), 24)
        self.assertEqual(date(2026, 11, 3), start2)
        self.assertEqual(["2027-02-03", "2027-05-03", "2027-08-03", "2027-11-03", "2028-02-03", "2028-05-03", "2028-08-03", "2028-11-03"],
                         [e.isoformat() for _s, e, _p in periods2])
        self.assertEqual(["2027-02-05", "2027-05-05", "2027-08-05", "2027-11-05", "2028-02-07", "2028-05-05", "2028-08-07", "2028-11-07"],
                         [p.isoformat() for _s, _e, p in periods2])
        pv = swaps.preview(rt, PB, "XCCY", TERMS)
        self.assertEqual(("2026-10-29", "2026-11-02", "2028-11-02", "2028-11-06", 3, 2), (pv["trade_date"], pv["start_date"], pv["termination_date"],
                                                                                         pv["maturity_date"], pv["period_months"], pv["payment_delay_bd"]))
        self.assertEqual([92, 90, 91, 92, 92, 90, 92, 92], [r["days"] for r in pv["schedule"]])

    def test_ct1_refusals_name_the_reason(self):
        rt = self.start()

        def code(t):
            try:
                swaps_trs.parse_xccy_terms(t)
            except RejectError as e:
                return e.code, e.message
            return None, None
        self.assertEqual("NOT_IN_THIS_BUILD", code(dict(TERMS, recv_index=None))[0])          # one floating leg: fixed / floating is not offered
        self.assertIn("fixed / floating", code(dict(TERMS, recv_index=None))[1])
        self.assertEqual("TERMS", code(dict(TERMS, pay_index="SOFR"))[0])                      # the index must match its currency
        gbp = code(dict(TERMS, pay_ccy="GBP", pay_index="SONIA"))
        self.assertEqual("NOT_IN_THIS_BUILD", gbp[0])
        self.assertIn("SONIA index (Bank of England IUDZOS2) is stored", gbp[1])
        jpy = code(dict(TERMS, pay_ccy="JPY", pay_index="TONA"))
        self.assertEqual(("NOT_IN_THIS_BUILD", True), (jpy[0], "no daily overnight rate is sourced for JPY" in jpy[1]))
        self.assertEqual("NOT_IN_THIS_BUILD", code(dict(TERMS, mtm_reset=True))[0])            # no notional reset on the float variant
        self.assertEqual("TERMS", code(dict(TERMS, months=25))[0])                             # quarterly periods
        self.assertEqual("TERMS", code(dict(TERMS, pay_basis="30/360"))[0])
        self.assertEqual("TERMS", code(dict(TERMS, recv_spread_bp="1.0"))[0])                  # the spread is quoted on the EUR leg
        self.assertEqual("TERMS", code(dict(TERMS, pay_spread_bp="0.15"))[0])                  # finer than the 0.1 bp grid
        self.assertEqual("TERMS", code(dict(TERMS, settlement_funding={"USD": "BUY_WITH_USD"}))[0])
        # the conversion sells the EUR received at the start: a swap that LENDS EUR (receive EUR / pay USD) cannot carry it
        self.assertEqual("TERMS", code(dict(TERMS, pay_ccy="USD", recv_ccy="EUR", pay_index="SOFR", recv_index="ESTR", recv_notional="2000000.00",
                                            pay_notional=None))[0])


class FloatDealTests(FloatXccyBase):
    # ---- C-T2: the deal ----------------------------------------------------------------------------------------------------------------
    def test_ct2_the_dealer_quote(self):
        rt = self.start()
        pv = swaps.preview(rt, PB, "XCCY", TERMS)
        self.assertEqual(("FLOAT_FLOAT", "1.1158", 223160000, 200000000, "RECEIVE_USD_PAY_EUR"), (pv["variant"], pv["deal_rate"], pv["usd_notional_c"],
                                                                                                pv["foreign_notional_c"], pv["side"]))
        self.assertEqual("pay EUR ESTR + 0.1 bp (borrowed) / receive USD SOFR flat (lent)", pv["side_text"])
        # A* 1.9646274388; PV(0) -403.98 = -370.75 (final exchange timing) + -33.23 (coupon delays)
        self.assertEqual(("1.9646274388", -40398, -37075, -3323), (pv["annuity_star"], pv["pv_zero_c"], pv["pv_zero_parts"]["final_exchange_timing_c"],
                                                                   pv["pv_zero_parts"]["coupon_delays_c"]))
        sp = pv["spread"]
        # par -0.9214 bp; half-width 1.0 x 1; dealt +0.1 (rounded up from +0.0786: 0.0214 bp of rounding); level 0.0 bp ASSUMED
        self.assertEqual(("-0.9214", "1.0", "0.1", "0.0214", "0.0", "EUR"), (sp["par_bp_4dp"], sp["hw_bp"], sp["dealt_bp"], sp["rounding_bp"], sp["level_bp"],
                                                                             sp["on_leg"]))
        self.assertEqual(("0.1", "0"), (pv["pay_spread_bp"], pv["recv_spread_bp"]))
        # mark0 -447.82 (= -403.98 - 0.1 x 438.43); bp value 438.43; basis01 +437.98 (re-priced: the level +1 bp on the EUR discount curve);
        # rate01 USD -2.25 / EUR +2.32; EUR OIS spread +2.32; EURUSD +1 % before S (the CCS alone: the start and final exchanges offset) +6.92
        self.assertEqual((-44782, 44782, 43843, 43798, {"USD": -225, "EUR": 232}, 232, 692), (pv["mark0_c"], pv["cost_of_dealing_c"], pv["bp_value_c"],
                                                                                            pv["basis01_c"], pv["rate01_c"], pv["eur_ois_spread01_c"],
                                                                                            pv["fx_1pct_c"]))
        # IM 2 % x 2,231,600.00 = 44,632.00
        self.assertEqual((4463200, "2.00 %"), (pv["im_c"], pv["im_pct"]))
        # period 1 (92 days) indicative: EUR 2,000,000 x ((1 / DF_EUR(11-02, 02-02) - 1) + 0.00001 x 92 / 360) = 12,945.83; USD 2,231,600 x
        # (1 / DF_USD - 1) = 23,571.46 (projected on the Treasury proxy, above SOFR: the index decides the payment)
        self.assertEqual((1294583, 2357146, True), (pv["schedule"][0]["eur_indicative_c"], pv["schedule"][0]["usd_indicative_c"], pv["schedule"][0]["indicative"]))
        self.assertEqual([1294583, 1415575, 1514865, 1573275, 1545932, 1545963, 1614981, 1642065], [r["eur_indicative_c"] for r in pv["schedule"]])
        # the conversion: sell EUR 2,000,000.00 at 1.11578 = USD 2,231,560.00, spread 40.00, value S; net start flows USD -40.00 / EUR 0
        conv = pv["start_conversion"]
        self.assertEqual(("1.11578", 200000000, 223156000, 4000, "2026-11-02"), (conv["rate"], conv["sell"]["amount_c"], conv["buy"]["amount_c"],
                                                                                 conv["spread_cost_c"], conv["value_date"]))
        self.assertEqual((-4000, 0), (pv["net_start_flows"]["USD"], pv["net_start_flows"]["EUR"]))
        # the exchanges at X0, no reset; the funding line: IM 44,632.00 + the start net 40.00 = 44,672.00 swept from Treasury
        self.assertEqual((("USD", 223160000), ("EUR", 200000000), "2028-11-06"), ((pv["exchanges"]["initial"]["we_pay"]["currency"],
                                                                                    pv["exchanges"]["initial"]["we_pay"]["amount_c"]),
                                                                                   (pv["exchanges"]["initial"]["we_receive"]["currency"],
                                                                                    pv["exchanges"]["initial"]["we_receive"]["amount_c"]),
                                                                                   pv["exchanges"]["final"]["date"]))
        self.assertEqual((True, 4467200, 4467200), (pv["funding"]["ok"], pv["funding"]["need_c"], pv["funding"]["sweep_c"]))
        # labels: MODEL, the PROXY curves, the ASSUMED terms; never "dealer-confirmed"
        labels = " | ".join(pv["labels"])
        for word in ("MODEL", "PROXY: Treasury par curve of 2026-10-27", "PROXY: ECB euro-area AAA government curve of 2026-10-27", "ASSUMED",
                     "indicative model quote, simulated dealer"):
            self.assertIn(word, labels)
        self.assertNotIn("dealer-confirmed", json.dumps(pv))
        # the pricing record carries every input with its source and time, and the limits / funding blocks
        rec = pv["pricing_record"]
        self.assertEqual(("fxcurves-2", "EURUSD", "1.1158"), (rec["model_version"], rec["deal_fx"]["pair"], rec["deal_fx"]["mid"]))
        self.assertIsNotNone(rec["deal_fx"]["quote_time"])
        self.assertEqual(("2026-10-27", "2026-10-27"), (rec["curves"]["USD"]["as_of"], rec["curves"]["EUR"]["as_of"]))
        self.assertEqual(("SOFR", "ESTR"), (rec["curves"]["USD"]["first_node"]["series"], rec["curves"]["EUR"]["first_node"]["series"]))
        self.assertIn("none configured", rec["limits"]["words"])
        self.assertIsNotNone(rec["funding"])

    def test_ct2_client_spread_better_than_the_quote_is_off_market(self):
        rt = self.start()
        with self.assertRaises(RejectError) as cm:
            swaps.preview(rt, PB, "XCCY", dict(TERMS, pay_spread_bp="0.0"))
        self.assertEqual("OFF_MARKET", cm.exception.code)
        pv = swaps.preview(rt, PB, "XCCY", dict(TERMS, pay_spread_bp="0.5"))             # a worse spread deals as given
        # mark0 = PV(0) - X0 N A* x 0.5 bp = -403.98 - 219.21 = -623.19 (unrounded -403.97735 - 219.21313 = -623.19048)
        self.assertEqual(("0.5", -62319), (pv["dealt_spread_bp"], pv["mark0_c"]))


class FloatBookingTests(FloatXccyBase):
    # ---- C-T3: the booking with the conversion ---------------------------------------------------------------------------------------
    def test_ct3_booking_with_the_conversion_in_one_command(self):
        rt = self.start()
        tre0 = bal(rt, TRE, "1010", "USD")
        r = book(rt, client_ref="OWNER-FX-2026-10-28-A")
        self.assertTrue(r.ok, r.body)
        cid = r.body["contract_id"]
        self.assertEqual(CID, cid)
        row = contract(rt)
        t = json.loads(row["terms_json"])
        # the contract: start S, trade date T, maturity p_8; USD notional; mark0 the cost of dealing; the owner reference
        self.assertEqual(("XCCY", "OPEN", "2026-11-02", "2028-11-06", 223160000, -44782, 4463200, "OWNER-FX-2026-10-28-A"),
                         (row["kind"], row["status"], row["start_date"], row["maturity_date"], row["notional_c"], row["mark_c"], row["im_c"], row["client_ref"]))
        self.assertEqual(("FLOAT_FLOAT", "2026-10-29", "2026-11-02", "0.1"), (t["variant"], row["trade_date"], t["start_date"], t["spread"]["dealt_bp"]))
        # IM: Dr 1330 44,632.00 / Cr 1010 after one Treasury sweep of 44,632.00
        self.assertEqual([("1330", "USD", CID, 4463200), ("1010", "USD", None, -4463200)], lines_by_idem(rt, f"im:{CID}:open"))
        self.assertEqual(tre0 - 4463200, bal(rt, TRE, "1010", "USD"))
        # the conversion: SELL EUR 2,000,000.00 / BUY USD 2,231,560.00 at 1.11578 value 2026-11-02, linked both ways, its settlement rows tagged
        deal_id = t["start_conversion"]["deal_id"]
        d = one(rt, "SELECT * FROM fx_deals WHERE deal_id=?", (deal_id,))
        self.assertEqual(("SPOT", "USD", 223156000, "EUR", 200000000, "1.11578", "2026-11-02", "2026-10-29", 4000, "PENDING"),
                         (d["kind"], d["buy_ccy"], d["buy_c"], d["sell_ccy"], d["sell_c"], d["rate"], d["value_date"], d["trade_date"], d["spread_cost_c"],
                          d["status"]))
        self.assertEqual({"type": "xccy_start", "id": CID}, {k: json.loads(d["inputs_json"])["link"][k] for k in ("type", "id")})
        sett = q(rt, "SELECT currency, amount_c, due_date, status, ref_type, ref_id FROM settlements WHERE fx_deal_id=? ORDER BY currency", (deal_id,))
        self.assertEqual([("EUR", -200000000, "2026-11-02", "PENDING", "xccy_start", CID), ("USD", 223156000, "2026-11-02", "PENDING", "xccy_start", CID)],
                         [(s["currency"], s["amount_c"], s["due_date"], s["status"], s["ref_type"], s["ref_id"]) for s in sett])
        # one command: the IM entry, the conversion's FX entry, the contract and the deal share the command id
        self.assertEqual(row["command_id"], d["command_id"])
        # no exchange before S: nothing in 1360 / 2360, no NOTIONAL_START row
        self.assertEqual((0, 0, None), (bal(rt, PB, "1360", "USD", CID), bal(rt, PB, "2360", "EUR", CID),
                                        one(rt, "SELECT 1 FROM otc_resets WHERE contract_id=? AND kind='NOTIONAL_START'", (CID,))))
        # the contract view: client_ref, the linked deal, the confirmation (pending until 17:05 ET), never "dealer-confirmed"
        v = r.body["contract"]
        self.assertEqual(("OWNER-FX-2026-10-28-A", deal_id, "xccy_start"), (v["client_ref"], v["linked_deals"][0]["deal_id"], v["linked_deals"][0]["link_type"]))
        self.assertIn("PENDING CONFIRMATION", v["confirmation"]["label"])
        # V1061-05: the confirmation's notionals are cents under ``*_c`` keys, so the API codec serves them as amounts - EUR 2,000,000.00 and
        # USD 2,000,000 x 1.1158 = 2,231,600.00 - never the bare integers 200000000 / 223160000 under "EUR" / "USD" (read as EUR 200,000,000)
        from finsim.terminal.api import encode
        self.assertEqual({"eur_c": 200000000, "usd_c": 223160000}, v["confirmation"]["notionals"])
        self.assertEqual({"eur": "2000000.00", "usd": "2231600.00"}, encode(v["confirmation"])["notionals"])
        self.assertEqual("44632.00", encode(v["confirmation"])["im"])
        self.assertEqual("pay EUR ESTR + 0.1 bp (borrowed) / receive USD SOFR flat (lent)", v["side_text"])
        self.assertNotIn("dealer-confirmed", json.dumps(v))
        assert_integrity(self, rt, "full")

    def test_ct3_duplicate_client_ref_writes_nothing(self):
        rt = self.start()
        self.assertTrue(book(rt, client_ref="OWNER-FX-2026-10-28-A").ok)
        n_deals, n_entries = len(q(rt, "SELECT 1 FROM fx_deals")), len(q(rt, "SELECT 1 FROM journal_entries"))
        r = book(rt, request_id="ui:xccyf-book-0002", client_ref="OWNER-FX-2026-10-28-A")
        self.assertEqual((409, "DUPLICATE_CLIENT_REF", CID), (r.http, r.code, r.body["error"]["details"]["contract_id"]))
        self.assertEqual((n_deals, n_entries, 1), (len(q(rt, "SELECT 1 FROM fx_deals")), len(q(rt, "SELECT 1 FROM journal_entries")),
                                                   len(q(rt, "SELECT 1 FROM otc_contracts"))))

    # ---- C-T15: client_ref written by h_book for every kind --------------------------------------------------------------------------
    def test_ct15_client_ref_for_an_fx_forward_and_an_xccy(self):
        from finsim.terminal.products import otc_options
        rt = self.start()
        rt.otc_options = otc_options
        otc_options.register(rt.bus, rt)
        otc_options.bind(rt)
        r = execute(rt, "otc.book", {"account_id": PB, "kind": "FX_FORWARD", "terms": {"pair": "EURUSD", "side": "SELL", "notional": "100000.00", "tenor": "3M",
                                                                                   "client_ref": "REF-FWD-1"}}, "ui:fwd-ref-0001")
        if not r.ok and r.code in ("NOT_BUILT", "NOT_IN_THIS_BUILD"):
            self.skipTest(f"the FX forward is not bookable in this tree: {r.code}")
        self.assertTrue(r.ok, r.body)
        fwd = r.body["contract_id"]
        self.assertTrue(book(rt, client_ref="REF-XCCY-1").ok)
        self.assertEqual({fwd: "REF-FWD-1", CID: "REF-XCCY-1"}, {x["contract_id"]: x["client_ref"] for x in q(rt, "SELECT contract_id, client_ref FROM otc_contracts")})
        for kind, terms, ref in (("FX_FORWARD", {"pair": "EURUSD", "side": "SELL", "notional": "100000.00", "tenor": "3M"}, "REF-FWD-1"),
                                 ("XCCY", dict(TERMS), "REF-XCCY-1")):
            r = execute(rt, "otc.book", {"account_id": PB, "kind": kind, "terms": dict(terms, client_ref=ref)}, f"ui:dup-{kind}-0001")
            self.assertEqual((409, "DUPLICATE_CLIENT_REF"), (r.http, r.code))
        self.assertEqual(2, len(q(rt, "SELECT 1 FROM otc_contracts")))
        r = execute(rt, "otc.book", {"account_id": PB, "kind": "XCCY", "terms": dict(TERMS, client_ref="bad ref!")}, "ui:bad-ref-0001")
        self.assertEqual((400, "TERMS"), (r.http, r.code))


def _h_linked_spot(ctx, payload):
    """Test command: a settlement-funding purchase as P-D's OTC_CCY_FUNDING books it (``fx.book_linked_spot``, check_funding False, tagged
    xccy_event to the swap) - the job itself is P-D's; here the deal it would book."""
    return fx.book_linked_spot(ctx, payload["account_id"], payload["buy_ccy"], payload["sell_ccy"], int(payload["amount_c"]), payload["amount_ccy"],
                               payload["value_date"], payload.get("mid"), payload["link"], tuple(payload["settle_ref"]), check_funding=False)


def sfi_deal(rt, amount_c, due, request_id, cid=CID):
    if "test.linked_spot" not in rt.bus.kinds():
        rt.bus.register("test.linked_spot", _h_linked_spot)
    return execute(rt, "test.linked_spot", {"account_id": PB, "buy_ccy": "EUR", "sell_ccy": "USD", "amount_c": amount_c, "amount_ccy": "EUR", "value_date": due,
                                            "link": {"type": "otc_funding", "contracts": [cid], "due": due, "obligations": [f"{cid} EUR due {due}"]},
                                            "settle_ref": ["xccy_event", cid]}, request_id)


def accrue_through(tc, rt, first: date, last: date, kinds=("swaprcv", "swappay"), cid=CID):
    night = first
    while night <= last:
        for kind in kinds:
            r = accrue(rt, kind, night.isoformat(), cid)
            tc.assertTrue(r.ok, r.body)
        night += timedelta(days=1)


def deal_statuses(rt, deal_id):
    return [s["status"] for s in q(rt, "SELECT status FROM settlements WHERE fx_deal_id=? ORDER BY currency", (deal_id,))]


class FloatLifecycleTests(FloatXccyBase):
    # ---- C-T4: the START, payment versus payment ------------------------------------------------------------------------------------
    def test_ct4_start_pvp_in_one_command(self):
        rt = self.start()
        self.booked()
        deal_id = terms_of(rt)["start_conversion"]["deal_id"]
        tre0 = bal(rt, TRE, "1010", "USD")
        # SETTLE_DUE at 00:35 ET of S leaves the conversion's tagged rows alone: only the START settles them (D7, no next-day fallback)
        rt.lifecycle.tick(at="2026-11-02T00:35:00-05:00", only="SETTLE_DUE")
        self.assertEqual(["PENDING", "PENDING"], deal_statuses(rt, deal_id))
        r = self.run_start()
        # one command: (1) the conversion's USD receivable 2,231,560.00 in, (2) the net shortfall 2,231,600.00 - 2,231,560.00 = 40.00 swept, (3) the
        # exchange: USD 2,231,600.00 lent out, EUR 2,000,000.00 borrowed in at X0 1.1158 (base 2,231,600.00), (4) the conversion's EUR payable out
        self.assertEqual([("1360", "USD", CID, 223160000, 223160000), ("1010", "USD", None, -223160000, -223160000), ("1010", "EUR", None, 200000000, 223160000),
                          ("2360", "EUR", CID, -200000000, -223160000)], lines_by_idem(rt, f"xccy:{CID}:start", base=True))
        self.assertEqual(tre0 - 4000, bal(rt, TRE, "1010", "USD"))
        self.assertEqual(["SETTLED", "SETTLED"], deal_statuses(rt, deal_id))
        self.assertEqual("SETTLED", one(rt, "SELECT status FROM fx_deals WHERE deal_id=?", (deal_id,))["status"])
        # end balances: 1010 USD 0 (+2,231,560 + 40 - 2,231,600), 1010 EUR 0, 1360 USD 2,231,600.00, 2360 EUR 2,000,000.00, 1250 USD and 2350 EUR 0
        self.assertEqual((0, 0, 223160000, 200000000, 0, 0), (bal(rt, PB, "1010", "USD"), bal(rt, PB, "1010", "EUR"), bal(rt, PB, "1360", "USD", CID),
                                                              bal(rt, PB, "2360", "EUR", CID), bal(rt, PB, "1250", "USD"), bal(rt, PB, "2350", "EUR")))
        ns = one(rt, "SELECT * FROM otc_resets WHERE contract_id=? AND kind='NOTIONAL_START'", (CID,))
        self.assertEqual(("2026-11-02", 223160000, deal_id), (ns["event_date"], ns["amount_c"], json.loads(ns["detail_json"])["conversion"]["deal_id"]))
        # a second run replays nothing new
        self.assertEqual("DONE", event(rt, "2026-11-02", "START", suffix=":again").body["status"])
        assert_integrity(self, rt, "full")

    def test_ct4_failed_start_waits_and_raises_late_at_six(self):
        rt = self.start()
        self.booked()
        deal_id = terms_of(rt)["start_conversion"]["deal_id"]
        left = bal(rt, TRE, "1010", "USD")
        self.assertTrue(execute(rt, "treasury.withdraw", {"lifetime_id": "F1", "amount_c": left, "currency": "USD"}, "ui:wd-0001").ok)
        # 00:50: the 40.00 net cannot be funded (no Treasury cash, PB financing off): the whole step rolls back - SKIPPED with an ERROR, nothing posted
        rt.clock.set("2026-11-02T00:50:00-05:00")
        r = event(rt, "2026-11-02", "START")
        self.assertEqual(("SKIPPED", "INSUFFICIENT_FUNDS"), (r.body["status"], r.body["code"]), r.body)
        self.assertIsNone(one(rt, "SELECT 1 FROM journal_entries WHERE idem_key=?", (f"xccy:{CID}:start",)))
        self.assertEqual(["PENDING", "PENDING"], deal_statuses(rt, deal_id))
        self.assertEqual((0, 0), (bal(rt, PB, "1010", "USD"), bal(rt, PB, "1010", "EUR")))
        self.assertEqual("ERROR", one(rt, "SELECT severity FROM attention WHERE code='INSUFFICIENT_FUNDS' AND ref_id=?", (f"{CID}:2026-11-02:START",))["severity"])
        # 06:05: still not done -> one ERROR XCCY_EVENT_LATE naming the event and its last reason
        rt.clock.set("2026-11-02T06:05:00-05:00")
        r = event(rt, "2026-11-02", "START", suffix=":0605")
        self.assertEqual(("SKIPPED", True), (r.body["status"], r.body.get("late")))
        late = one(rt, "SELECT severity, message FROM attention WHERE code='XCCY_EVENT_LATE' AND ref_id=?", (f"{CID}:2026-11-02:START",))
        self.assertEqual("ERROR", late["severity"])
        self.assertIn("start exchange (with its linked conversion) of 2026-11-02 is not done by 06:00 ET", late["message"])
        # the next day SETTLE_DUE still leaves the tagged rows alone: nothing settles the conversion without the START (nothing is overdrawn)
        rt.lifecycle.tick(at="2026-11-03T00:35:00-05:00", only="SETTLE_DUE")
        self.assertEqual(["PENDING", "PENDING"], deal_statuses(rt, deal_id))
        # no EUR left the account: 1010 EUR 0; the conversion's EUR 2,000,000.00 payable still waits on 2350 with its USD receivable on 1250
        self.assertEqual((0, 200000000, 223156000), (bal(rt, PB, "1010", "EUR"), bal(rt, PB, "2350", "EUR"), bal(rt, PB, "1250", "USD")))
        assert_integrity(self, rt, "full")

    # ---- C-T5: accrual night 1 -------------------------------------------------------------------------------------------------------
    def test_ct5_accrual_night_one(self):
        rt = self.start()
        self.booked()
        self.run_start()
        rt.clock.set("2026-11-03T00:35:00-05:00")
        self.assertTrue(accrue(rt, "swaprcv", "2026-11-02").ok)
        self.assertTrue(accrue(rt, "swappay", "2026-11-02").ok)
        # USD (received, SOFR 3.95 % of 10-27): to_c(2,231,600 x (1.000109722222222222 - 1)) = to_c(244.8561...) = 244.86, Dr 1225 / Cr 4453
        self.assertEqual([("1225", "USD", CID, 24486), ("4453", "USD", CID, -24486)], lines_by_idem(rt, f"accr:swaprcv:{CID}:2026-11-02"))
        # EUR (paid, ESTR 2.440 % + 0.1 bp): to_c(2,000,000 x ((1.000067777777777778 - 1) + 0.00001 x 1 / 360)) = to_c(135.6111...) = 135.61,
        # Dr 4453 / Cr 2340 (base at the command's EURUSD)
        self.assertEqual([("4453", "EUR", CID, 13561), ("2340", "EUR", CID, -13561)], lines_by_idem(rt, f"accr:swappay:{CID}:2026-11-02"))

    # ---- C-T6: the first payment, 2027-02-04 --------------------------------------------------------------------------------------------
    def test_ct6_pay_with_the_index_true_up(self):
        """Period 1 = 2026-11-02 .. 2027-02-02 (92 days), paid 2027-02-04.  Flat prints: SOFR 3.95 % on every US government securities day,
        ESTR 2.440 % on every TARGET2 day, the indices compounded from them with weekend / holiday weights.
        - The nightly estimates (every calendar night, F x (1 + r / 360) at 1e-18): USD to_c(2,231,600 x (1.010145005845996024 - 1)) = 22,639.60;
          EUR to_c(2,000,000 x ((1.006254824475598546 - 1) + 0.00001 x 92 / 360)) = 12,514.76.
        - The index: USD I(02-02) / I(11-02) = 1.0101443979831955731... over 61 publication days (weights 92), R = (factor - 1) x 360 / 92 =
          0.0396955 (7 dp) -> to_c(2,231,600 x 0.0396955 x 92 / 360) = to_c(22,638.259...) = 22,638.26; EUR 1.0062546165004826497... over 64
          TARGET2 days, R = 0.0244746 -> to_c(2,000,000 x (0.0244746 + 0.00001) x 92 / 360) = to_c(12,514.35...) = 12,514.35.
        - True-ups: USD 22,638.26 - 22,639.60 = -1.34 (the estimate compounds on weekends too); EUR 12,514.35 - 12,514.76 = -0.41.
        - The settlement-funding deal bought EUR 12,514.35 on 02-02 at 1.1158 + 0.2 pip = 1.11582: USD 13,963.76 (to_c(12,514.35 x 1.11582)).
        - After the payment 1225 / 2340 hold the first two nights of period 2 (02-02, 02-03): USD to_c(2,231,600 x 0.000219456483410494) = 489.74;
          EUR to_c(2,000,000 x (0.000135560149382716 + 0.00001 x 2 / 360)) = 271.23."""
        rt = self.start()
        self.booked()
        self.run_start()
        flat_prints(rt, S, date(2027, 2, 5))
        rt.clock.set("2027-02-02T10:00:00-05:00")
        accrue_through(self, rt, S, date(2027, 2, 1))
        self.assertEqual((2263960, 1251476), (bal(rt, PB, "1225", "USD", CID), bal(rt, PB, "2340", "EUR", CID)))
        set_fx(rt, "1.1158")
        r = sfi_deal(rt, 1251435, "2027-02-04", "lifecycle:sfi-0001")
        self.assertTrue(r.ok, r.body)
        sfi = r.body["deal"]["deal_id"]
        self.assertEqual((1251435, 1396376, "1.11582"), (r.body["deal"]["buy_c"], r.body["deal"]["sell_c"], r.body["deal"]["rate"]))
        rt.clock.set("2027-02-04T00:35:00-05:00")
        accrue_through(self, rt, date(2027, 2, 2), date(2027, 2, 3))
        rt.lifecycle.tick(only="SETTLE_DUE")                                  # the funding deal is tagged to the swap's event: it waits for the PAY
        self.assertEqual(["PENDING", "PENDING"], deal_statuses(rt, sfi))
        # K5 scheduled_flows: the period has ended and both indices are published - its coupons are KNOWN (the index-exact amounts, + in / - out)
        with rt.db.reader() as conn:
            flows = swaps_trs.scheduled_flows(conn, contract(rt), rt)
        self.assertEqual([("USD", 2263826, True, "coupon"), ("EUR", -1251435, True, "coupon")],
                         [(f["ccy"], f["amount_c"], f["known"], f["flow_kind"]) for f in flows if f["date"] == "2027-02-04"])
        self.assertEqual([("USD", 223160000, "exchange"), ("EUR", -200000000, "exchange")],
                         [(f["ccy"], f["amount_c"], f["flow_kind"]) for f in flows if f["date"] == "2028-11-06" and f["flow_kind"] == "exchange"])
        # P-C-R4 (integration): fx.contract_flows - what fx.funding_check and ccyfunding read - hands THIS runtime to the hook (its third
        # argument), so the coupons are known from the published indices even when the runtime swaps_trs.register saw is another (here: none)
        saved = dict(swaps_trs._BOUND)
        swaps_trs._BOUND.clear()
        try:
            with rt.db.reader() as conn:
                via_fx = fx.contract_flows(conn, contract(rt), rt)
        finally:
            swaps_trs._BOUND.update(saved)
        self.assertEqual([("USD", 2263826, True, "coupon"), ("EUR", -1251435, True, "coupon")],
                         [(f["ccy"], f["amount_c"], f["known"], f["flow_kind"]) for f in via_fx if f["date"] == "2027-02-04"])
        rt.clock.set("2027-02-04T00:50:00-05:00")
        r = event(rt, "2027-02-04", "PAY")
        self.assertEqual("DONE", r.body["status"], r.body)
        legs = r.body["legs"]
        self.assertEqual((2263826, 2263960, -134, "0.0396955"), (legs["receive"]["amount_c"], legs["receive"]["posted_c"], legs["receive"]["trueup_c"],
                                                                 legs["receive"]["rate"]))
        self.assertEqual((1251435, 1251476, -41, "0.0244746"), (legs["pay"]["amount_c"], legs["pay"]["posted_c"], legs["pay"]["trueup_c"], legs["pay"]["rate"]))
        # the true-ups (idem accr:{kind}:{cid}:trueup:{e_k}): USD -1.34 reverses 1225 into 4453; EUR -0.41 reverses 2340 into 4453
        self.assertEqual([("4453", "USD", CID, 134), ("1225", "USD", CID, -134)], lines_by_idem(rt, f"accr:swaprcv:{CID}:trueup:2027-02-02"))
        self.assertEqual([("2340", "EUR", CID, 41), ("4453", "EUR", CID, -41)], lines_by_idem(rt, f"accr:swappay:{CID}:trueup:2027-02-02"))
        # the payment: USD 22,638.26 received out of 1225, EUR 12,514.35 paid out of 2340 with the EUR the funding deal delivered
        self.assertEqual([("1010", "USD", None, 2263826), ("1225", "USD", CID, -2263826), ("2340", "EUR", CID, 1251435), ("1010", "EUR", None, -1251435)],
                         lines_by_idem(rt, f"xccypay:{CID}:2027-02-04"))
        self.assertEqual(["SETTLED", "SETTLED"], deal_statuses(rt, sfi))
        # remainders: the first nights of period 2 only; EUR cash 0; USD cash = 22,638.26 - 13,963.76 = 8,674.50
        self.assertEqual((48974, 27123, 0, 867450), (bal(rt, PB, "1225", "USD", CID), bal(rt, PB, "2340", "EUR", CID), bal(rt, PB, "1010", "EUR"),
                                                     bal(rt, PB, "1010", "USD")))
        pay = one(rt, "SELECT * FROM otc_resets WHERE contract_id=? AND event_date='2027-02-04' AND kind='PAY'", (CID,))
        self.assertEqual(("ESTR", "SOFR"), (json.loads(pay["detail_json"])["legs"]["pay"]["index"], json.loads(pay["detail_json"])["legs"]["receive"]["index"]))
        # the timeline names the conversion (booked, settled at the START), the funding purchase (booked, settled at the PAY), the true-ups, the payment
        steps = [(s["step"], s.get("amount_c")) for s in swaps.contract_timeline(rt, CID)]
        for want in (("CONVERSION", None), ("FUNDING", None), ("TRUEUP", -134), ("TRUEUP", -41), ("EXCHANGE", 223160000)):
            self.assertIn(want, steps)
        self.assertEqual(2, sum(1 for s, _a in steps if s == "DEAL_SETTLED"))
        self.assertEqual(1, sum(1 for s, _a in steps if s == "PAY"))
        assert_integrity(self, rt, "full")

    # ---- C-T12: INDEX_PENDING, and the EUR fallback -----------------------------------------------------------------------------------
    def test_ct12_index_pending_then_pays_and_the_eur_fallback(self):
        """The SOFR Index of 2027-02-02 is not stored at 00:50 ET of the payment: the PAY skips quietly (INDEX_PENDING, its first day: no
        attention) and pays once it arrives.  No ESTR index is stored at all: the EUR coupon comes from the daily ESTR prints over the TARGET2
        days with n_i / 360 weights - the same 12,514.35 as from the index (C-T6)."""
        rt = self.start()
        self.booked()
        self.run_start()
        flat_prints(rt, S, date(2027, 2, 5))
        rt.rates = _without(rt.rates, {"SOFR_INDEX": {date(2027, 2, 2)}, "ESTR_INDEX": "all"})
        rt.clock.set("2027-02-04T00:35:00-05:00")
        accrue_through(self, rt, S, date(2027, 2, 3))
        rt.clock.set("2027-02-04T00:50:00-05:00")
        r = event(rt, "2027-02-04", "PAY")
        self.assertEqual(("SKIPPED", "INDEX_PENDING", True), (r.body["status"], r.body["code"], r.body["quiet"]), r.body)
        self.assertIsNone(one(rt, "SELECT 1 FROM attention WHERE code='INDEX_PENDING' AND resolved_ts IS NULL"))
        self.assertIsNone(one(rt, "SELECT 1 FROM journal_entries WHERE idem_key=?", (f"xccypay:{CID}:2027-02-04",)))
        rt.rates.restore()                                                    # the SOFR Index of 02-02 arrives (the ESTR index stays missing)
        r = event(rt, "2027-02-04", "PAY", suffix=":retry")
        self.assertEqual("DONE", r.body["status"], r.body)
        self.assertEqual((2263826, 1251435), (r.body["legs"]["receive"]["amount_c"], r.body["legs"]["pay"]["amount_c"]))

    # ---- C-T8: END ----------------------------------------------------------------------------------------------------------------------
    def test_ct8_end_reverse_exchange_with_the_funding_deal_pvp(self):
        """The END's mechanics on a 3-month swap (one period: 2026-11-02 .. 2027-02-02, the last payment and the final exchange 2027-02-04), dealt
        at +0.1 bp (a spread worse for the Fund than the quote is dealt as given) so its coupons are period 1's of C-T6: USD 22,638.26 in, EUR
        12,514.35 out.  The funding deal bought EUR 2,000,000.00 + 12,514.35 = 2,012,514.35 at 1.11582: USD to_c(2,012,514.35 x 1.11582) =
        2,245,603.76, settled around the exchange (its EUR in before, its USD out after).  Then VM unwound, IM 2 % x 2,231,600.00 = 44,632.00
        released, MATURED.  USD cash after = 22,638.26 + 2,231,600.00 - 2,245,603.76 + 44,632.00 = 53,266.50."""
        rt = self.start()
        self.booked(months=3, pay_spread_bp="0.1")
        t = terms_of(rt)
        self.assertEqual(("2027-02-04", [["2026-11-02", "2027-02-02", "2027-02-04"]]), (contract(rt)["maturity_date"], t["periods"]))
        self.run_start()
        flat_prints(rt, S, date(2027, 2, 5))
        rt.clock.set("2027-02-02T10:00:00-05:00")
        accrue_through(self, rt, S, date(2027, 2, 1))
        set_fx(rt, "1.1158")
        r = sfi_deal(rt, 201251435, "2027-02-04", "lifecycle:sfi-end-0001")
        self.assertTrue(r.ok, r.body)
        sfi = r.body["deal"]["deal_id"]
        self.assertEqual(224560376, r.body["deal"]["sell_c"])
        rt.clock.set("2027-02-04T00:50:00-05:00")
        with rt.db.reader() as conn:
            self.assertEqual([(CID, "2027-02-04", "END")], swaps_trs.event_keys(conn, rt.clock.epoch()))
        r = event(rt, "2027-02-04", "END")
        self.assertEqual("DONE", r.body["status"], r.body)
        self.assertEqual([("1010", "USD", None, 2263826), ("1225", "USD", CID, -2263826), ("2340", "EUR", CID, 1251435), ("1010", "EUR", None, -1251435)],
                         lines_by_idem(rt, f"xccypay:{CID}:2027-02-04"))
        self.assertEqual([("1010", "USD", None, 223160000), ("1360", "USD", CID, -223160000), ("2360", "EUR", CID, 200000000), ("1010", "EUR", None, -200000000)],
                         lines_by_idem(rt, f"xccy:{CID}:end"))
        self.assertEqual(["SETTLED", "SETTLED"], deal_statuses(rt, sfi))
        self.assertEqual([("1010", "USD", None, 4463200), ("1330", "USD", CID, -4463200)], lines_by_idem(rt, f"im:{CID}:release"))
        self.assertEqual("MATURED", contract(rt)["status"])
        self.assertEqual((0, 0, 0, 0, 0, 0, 5326650), (bal(rt, PB, "1010", "EUR"), bal(rt, PB, "1360", "USD", CID), bal(rt, PB, "2360", "EUR", CID),
                                                       bal(rt, PB, "1225", "USD", CID), bal(rt, PB, "2340", "EUR", CID), bal(rt, PB, "1330", "USD", CID),
                                                       bal(rt, PB, "1010", "USD")))
        assert_integrity(self, rt, "full")

    def test_ct8_the_24m_swap_ends_on_2028_11_06(self):
        rt = self.start()
        self.booked()
        rt.clock.set("2028-11-06T00:50:00-05:00")
        with rt.db.reader() as conn:
            keys = swaps_trs.event_keys(conn, rt.clock.epoch())
        self.assertEqual((CID, "2028-11-06", "END"), keys[-1])
        self.assertEqual([(CID, "2026-11-02", "START")] + [(CID, p, "PAY") for p in PAYS[:-1]] + [(CID, "2028-11-06", "END")], keys)


class _without:
    """A RateStore view without some index observations (C-T12: an index not published yet); ``restore`` brings the hidden ones back except the
    series hidden entirely."""

    def __init__(self, base, hide: dict):
        self._base = base
        self._hide = dict(hide)

    def restore(self):
        self._hide = {k: v for k, v in self._hide.items() if v == "all"}

    def observation(self, series, d, exact=False):
        h = self._hide.get(series)
        if h == "all" or (isinstance(h, set) and d in h):
            return None
        return self._base.observation(series, d, exact=exact)

    def __getattr__(self, name):
        return getattr(self._base, name)


class FloatVmTests(FloatXccyBase):
    # ---- C-T7: VM on the CSA exposure ----------------------------------------------------------------------------------------------------
    def test_ct7_vm_on_the_csa_exposure(self):
        rt = self.start()
        self.booked()
        # day one (10-29, the 17:00 ET close 1.1158, value S): the exposure = the full value = mark0 -447.82, posted: Dr 1320 / Cr 1010 447.82
        set_fx(rt, "1.1158", date(2026, 10, 29), "17:00")
        rt.clock.set("2026-10-29T17:10:00-04:00")
        r = vm(rt, "2026-10-29")
        self.assertEqual((-44782, -44782, -44782), (r.body["delta_c"], r.body["csa_exposure_c"], r.body["mark_c"]))
        self.run_start()
        # the live valuation of 11-02 needs curves at most 3 business days old: the example's rows are installed again dated Fri 10-30 (t now counts
        # from 10-30: the designer's pricer with T0 = 10-30 gives -259.82 at X0, -8,658.35 at 1.1200 - the plan's -259.44 / -8,657.97 on the 10-27
        # curves are pinned in test_ct7_after_start_values_on_the_example_curves)
        fxcurves.fixture_curves(rt, d0=date(2026, 10, 30), persist=False)
        # after S (valuation 11-02 after the START, value 11-04) at X0: full value = CSA exposure = -259.82; B(t) = 2,231,600.00 - 1.1158 x
        # 2,000,000.00 = 0.00, so the clean mark is -259.82 too
        set_fx(rt, "1.1158", date(2026, 11, 2), "17:00")
        rt.clock.set("2026-11-02T17:10:00-05:00")
        got = swaps_trs.mark_xccy(rt, CID, at=rt.clock.epoch(), valuation_date=date(2026, 11, 2))
        self.assertEqual((-25982, -25982, 0), (got[0], got[1]["csa_exposure_c"], got[1]["balance_sheet_c"]))
        # the close 1.1200: exposure -8,658.35 = the notional net 2,231,600.00 - 1.12 x 2,000,000.00 = -8,400.00 plus the clean mark -258.35;
        # the VM posts the change from the previous VM's exposure: -8,658.35 - (-447.82) = -8,210.53; the overlay is the clean mark
        set_fx(rt, "1.1200", date(2026, 11, 2), "17:00")
        r = vm(rt, "2026-11-02")
        self.assertEqual((-25835, -865835, -821053, -865835), (r.body["mark_c"], r.body["csa_exposure_c"], r.body["delta_c"], r.body["vm_balance_c"]))
        row = contract(rt)
        self.assertEqual((-25835, -865835), (row["mark_c"], row["vm_balance_c"]))
        self.assertEqual(-865835, one(rt, "SELECT mark_c FROM otc_vm WHERE contract_id=? AND vm_date='2026-11-02'", (CID,))["mark_c"])
        self.assertEqual([("1320", "USD", CID, 821053), ("1010", "USD", None, -821053)], lines_by_idem(rt, f"swapvm:{CID}:2026-11-02"))
        # with accrued coupons on the balance sheet (night 11-02: 1225 USD 244.86, 2340 EUR 135.61) the exposure includes them at X_t:
        # B = 2,231,600.00 + 244.86 - 1.12 x (2,000,000.00 + 135.61) = 2,231,844.86 - 2,240,151.8832 = -8,307.0232 -> -8,307.02
        rt.clock.set("2026-11-03T00:35:00-05:00")
        self.assertTrue(accrue(rt, "swaprcv", "2026-11-02").ok)
        self.assertTrue(accrue(rt, "swappay", "2026-11-02").ok)
        rt.clock.set("2026-11-03T10:00:00-05:00")
        got = swaps_trs.mark_xccy(rt, CID, at=rt.clock.epoch(), valuation_date=date(2026, 11, 2))
        self.assertEqual(-830702, got[1]["balance_sheet_c"])
        self.assertLessEqual(abs(got[1]["csa_exposure_c"] - got[0] - (-830702)), 1)
        self.assertEqual({"USD": {"1225": 24486, "1360": 223160000}, "EUR": {"2340": 13561, "2360": 200000000}}, got[1]["balance_sheet"])
        assert_integrity(self, rt, "full")

    def test_ct7_after_start_values_on_the_example_curves(self):
        """FX_CCS_PLAN 3.3 "after S" on the example's 10-27 curves (as the designer's exact pricer values them: t = 2026-11-02 17:05 after the
        START, value Wed 11-04, no night accrued): full value = CSA exposure -259.44 at X0 1.1158 (B 0.00); at 1.1200 the exposure is -8,657.97 =
        the notional net -8,400.00 + the clean mark -257.97; the termination value with the level 1.0 bp down (the half-width against the Fund)
        -696.34 (D17).  Pinned through the pricer itself: the live rule refuses a 10-27 curve on 11-02 (more than 3 business days old)."""
        rt = self.start()
        with rt.db.reader() as conn:
            acct = dict(conn.execute("SELECT * FROM accounts WHERE account_id=?", (PB,)).fetchone())
            market = swaps_trs.float_market(rt, conn, acct, date(2026, 10, 29))
        _s, periods = swaps_trs.float_dates(rt, "EUR", date(2026, 10, 29), 24)
        legs = swaps_trs.FloatLegs({"pay": "EUR", "receive": "USD"}, {"pay": 200000000, "receive": 223160000}, periods, "EUR")
        st = swaps_trs.FloatState(started=True)
        b = D("0.1") * D("0.0001")
        tspot = date(2026, 11, 4)
        proj, disc = market.zc()
        from finsim.terminal.units import to_c
        self.assertEqual("2026-10-27", market.usd.d0.isoformat())
        self.assertEqual(-25944, to_c(swaps_trs.float_full_value(legs, b, D("1.1158"), tspot, proj, disc, st)[0]))
        full = swaps_trs.float_full_value(legs, b, D("1.1200"), tspot, proj, disc, st)[0]
        self.assertEqual((-865797, -25797), (to_c(full), to_c(full - (D("2231600.00") - D("1.1200") * D("2000000.00")))))
        p2, d2 = market.zc(mid=-1)
        self.assertEqual(-69634, to_c(swaps_trs.float_full_value(legs, b, D("1.1158"), tspot, p2, d2, st)[0]))

    # ---- K5 / D14: the risk hook returns the off-ledger part only ------------------------------------------------------------------------
    def test_risk_sensitivities_off_ledger_part(self):
        """At T (before S, the example's curves): the clean mark's FX delta = the EUR flows' PV in EUR at the dealt spread = 620.04 (the designer's
        EUR leg; x 1.1158 x 1 % = 6.92, the CCS-alone EURUSD +1 % of C-T2); the exchanges not yet on the ledger net to 0 per currency; rate01 USD
        -2.25 / EUR +2.32, basis01 +437.98, EUR OIS spread +2.32 (C-T2).  After S (11-02 10:00, the example's rows re-dated 10-30, value 11-04):
        the ledger holds the notionals (2360 EUR -2,000,000.00), the hook only the rest: the EUR flows' PV -1,999,651.71 + 2,000,000.00 = +348.29
        (+347.92 on the 10-27 curves: section 6), nothing nominal; rate01 USD -1.03 / EUR +1.10; basis01 +436.83 (the designer's pricer, T0 =
        10-30).  EUR PV delta = ledger -2,000,000.00 + hook +348.29 = -1,999,651.71."""
        rt = self.start()
        self.booked()
        got = swaps_trs.risk_sensitivities(rt, contract(rt))
        self.assertEqual(({"EUR": 62004}, {"EUR": 0, "USD": 0}, {"USD": -225, "EUR": 232}, {"EURUSD": 43798}, 232, True),
                         (got["fx_delta"], got["fx_nominal"], got["rate01_c"], got["basis01_c"], got["eur_ois_spread01_c"], got["off_ledger_only"]))
        self.run_start()
        fxcurves.fixture_curves(rt, d0=date(2026, 10, 30), persist=False)
        rt.clock.set("2026-11-02T10:00:00-05:00")
        set_fx(rt, "1.1158")
        got = swaps_trs.risk_sensitivities(rt, contract(rt))
        self.assertEqual(({"EUR": 34829}, {}, {"USD": -103, "EUR": 110}, {"EURUSD": 43683}), (got["fx_delta"], got["fx_nominal"], got["rate01_c"],
                                                                                            got["basis01_c"]))
        self.assertEqual(-199965171, -bal(rt, PB, "2360", "EUR", CID) + got["fx_delta"]["EUR"])
        self.assertEqual(-103, swaps_trs.dv01_c(rt, contract(rt)))

    # ---- C-T11: the fixed / fixed swap on the CSA exposure ---------------------------------------------------------------------------------
    def test_ct11_fixed_fixed_vm_on_the_csa_exposure(self):
        """D8 applies to every XCCY: the s19.16 fixed / fixed swap (receive USD 11,238,000.00 at 4.00 % / pay EUR 10,000,000.00 at 2.50 %, dealt
        Mon 2026-05-18 at 1.1238, started Wed 05-20) margined after its start at a EURUSD close of 1.1300:
        - clean = USD 11,238,000 x (0.04000 - 0.04010) x 1.903633365 = -2,139.3032 + EUR -10,000,000 x (0.02500 - 0.02490) x 1.9392669142 x 1.13 =
          -2,191.3716 -> -4,330.6748 -> -4,330.67 (the overlay);
        - B = 1360 USD 11,238,000.00 - 2360 EUR 10,000,000.00 x 1.13 = -62,000.00;
        - CSA exposure = -4,330.67 - 62,000.00 = -66,330.67; VM = -66,330.67 - (-4,318.65, the day-one exposure) = -62,012.02 posted."""
        from tests import test_terminal_swaps_trs as TT
        rt = self.rt = TT.runtime(self.tmp, TT.XCCY_AT)
        rt.curve.set_fixed_dfs(TT.USD401, date(2026, 5, 15))
        rt.rates.put("ECBDFR", [(date(2026, 5, 15), D("2.49"))], persist=False)
        self.assertTrue(TT.book(rt, dict(TT.XCCY_TERMS), "ui:xccy-book-0001", kind="XCCY").ok)
        rt.marks.set("EURUSD", "1.1238", "delayed", epoch_of(date(2026, 5, 18), "17:00"))
        rt.clock.set("2026-05-18T17:10:00-04:00")
        self.assertEqual(-431865, TT.vm(rt, "2026-05-18").body["delta_c"])
        rt.clock.set("2026-05-20T00:50:00-04:00")
        rt.lifecycle.tick(only="XCCY_EVENTS")
        rt.marks.set("EURUSD", "1.1300", "delayed", epoch_of(date(2026, 5, 20), "17:00"))
        rt.clock.set("2026-05-20T17:10:00-04:00")
        r = TT.vm(rt, "2026-05-20")
        self.assertEqual((-433067, -6633067, -6201202), (r.body["mark_c"], r.body["csa_exposure_c"], r.body["delta_c"]))
        self.assertEqual(-433067, TT.contract(rt)["mark_c"])
        assert_integrity(self, rt, "full")

    # ---- C-T13: VM keys ----------------------------------------------------------------------------------------------------------------
    def test_ct13_vm_keys_from_the_trade_date_and_the_null_guard(self):
        rt = self.start()
        self.booked()
        # rows shaped like the owner's SWP-000004..6 (live TRS booked 10-28 in 1.0.5: trade_date / booking / confirm_status NULL), one with a VM row,
        # one without, and one whose trade date is unreadable
        with rt.db.writer() as tx:
            for cid, td in (("SWP-000904", None), ("SWP-000905", None), ("SWP-000906", "not-a-date")):
                tx.conn.execute("INSERT INTO otc_contracts(contract_id, command_id, account_id, broker, kind, terms_json, notional_c, currency, start_date,"
                                " maturity_date, margin_mode, im_c, status, mark_c, vm_balance_c, trade_date) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'OPEN',0,0,?)",
                                (cid, 0, PB, "GS", "TRS", json.dumps({"underlying_id": "AAPL", "units": 10, "receiver": False}), 100000, "USD", "2026-10-28",
                                 "2027-10-28", "CTM", 0, td))
            tx.conn.execute("INSERT INTO otc_vm(contract_id, vm_date, mark_c, delta_c, mode, entry_id, inputs_json) VALUES ('SWP-000904','2026-10-28',0,0,'CTM',NULL,'{}')")
        rt.clock.set("2026-10-29T17:10:00-04:00")
        with rt.db.reader() as conn:
            keys = swaps.vm_keys(conn, rt.clock.epoch())
        # the new CCS from its trade date 10-29 (before its start 11-02); SWP-000904 from its last VM + 1; SWP-000905 / -906 from their start
        # 10-28 (a NULL or unreadable trade date never raises and never moves a key)
        self.assertEqual([(CID, "2026-10-29"), ("SWP-000904", "2026-10-29"), ("SWP-000905", "2026-10-28"), ("SWP-000905", "2026-10-29"),
                          ("SWP-000906", "2026-10-28"), ("SWP-000906", "2026-10-29")], sorted(keys))
        self.assertEqual(date(2026, 10, 29), swaps.vm_first_date(contract(rt)))


class FloatRfqTests(FloatXccyBase):
    # ---- C-T9: the RFQ path ---------------------------------------------------------------------------------------------------------------
    def tick(self, secs=30):
        rt = self.rt
        return rt.lifecycle.tick(at=rt.clock.epoch() + secs, only="RFQ_DEALER")

    def request(self, ref, rid):
        return execute(self.rt, "otc.rfq.request", {"account_id": PB, "kind": "XCCY", "terms": dict(TERMS, client_ref=ref)}, rid)

    def test_ct9_request_quote_accept_and_the_duplicate_rules(self):
        rt = self.start()
        r = self.request("REF-A", "ui:rfq-a-0001")
        self.assertEqual("REQUESTED", r.body["status"], r.body)
        rid = r.body["rfq_id"]
        r2 = self.request("REF-A", "ui:rfq-a-0002")                            # REQUESTED carries the ref
        self.assertEqual((409, "DUPLICATE_CLIENT_REF", rid), (r2.http, r2.code, r2.body["error"]["details"]["rfq_id"]))
        self.tick()
        rfq = swaps.rfq_detail(rt, rid)
        self.assertEqual("QUOTED", rfq["status"])
        self.assertEqual("deal FX 1.1158; pay EUR ESTR + 0.1 bp / receive USD SOFR flat; EUR 2,000,000.00 / USD 2,231,600.00; 2026-11-02 to 2028-11-06",
                         rfq["quote_words"])
        self.assertEqual({"deal_rate": "1.1158", "pay_spread_bp": "0.1", "recv_spread_bp": "0"}, rfq["quote"]["terms"])
        self.assertEqual(("EUR/USD float / float cross-currency swap", 223160000), (rfq["label"], rfq["notional_c"]))
        r3 = self.request("REF-A", "ui:rfq-a-0003")                            # QUOTED carries the ref
        self.assertEqual((409, "DUPLICATE_CLIENT_REF"), (r3.http, r3.code))
        acc = execute(rt, "otc.rfq.accept", {"rfq_id": rid}, "ui:rfq-a-acc-0001")
        self.assertTrue(acc.ok, acc.body)
        row = contract(rt, acc.body["contract_id"])
        t = json.loads(row["terms_json"])
        self.assertEqual(("REF-A", "1.1158", "0.1", "FLOAT_FLOAT"), (row["client_ref"], t["deal_rate"], t["spread"]["dealt_bp"], t["variant"]))
        self.assertEqual("ACCEPTED", swaps.rfq_detail(rt, rid)["status"])
        r4 = self.request("REF-A", "ui:rfq-a-0004")                            # booked: a contract carries the ref
        self.assertEqual((409, "DUPLICATE_CLIENT_REF", acc.body["contract_id"]), (r4.http, r4.code, r4.body["error"]["details"]["contract_id"]))
        # an EXPIRED or DECLINED request never blocks a new one
        rb = self.request("REF-B", "ui:rfq-b-0001")
        self.assertTrue(execute(rt, "otc.rfq.decline", {"rfq_id": rb.body["rfq_id"]}, "ui:rfq-b-dec-0001").ok)
        self.assertEqual("REQUESTED", self.request("REF-B", "ui:rfq-b-0002").body["status"])
        rc = self.request("REF-C", "ui:rfq-c-0001")
        self.tick()
        self.tick(121)
        self.assertEqual("EXPIRED", swaps.rfq_detail(rt, rc.body["rfq_id"])["status"])
        self.assertEqual("REQUESTED", self.request("REF-C", "ui:rfq-c-0002").body["status"])

    # ---- C-T10: a schedule edit of the basis level -------------------------------------------------------------------------------------
    def test_ct10_basis_level_edit_reprices_quote_and_mark(self):
        """The GS EUR/USD basis level edited 0.0 -> -12.5 bp (one SETTINGS_CHANGED event with before / after).  Re-priced on the example's
        inputs (the designer's exact pricer): a new quote's A* 1.9673865085, PV(0) -5,886.02, par -13.4065 bp (it moved -12.4851 bp: about the
        -12.5), dealt -13.4065 + 1.0 = -12.4065 rounded up = -12.4 bp, mark0 -441.90; the open swap (dealt +0.1 bp) re-marks from -447.82 to
        -5,929.93: -5,482.11, about -12.5 x 437.98 = -5,474.75."""
        rt = self.start()
        self.booked()
        r = execute(rt, "account.update_schedule", {"account_id": PB, "xccy_basis_eur_bp": "-12.5"}, "ui:sched-basis-0001")
        self.assertTrue(r.ok, r.body)
        ev = one(rt, "SELECT payload_json FROM events WHERE type='SETTINGS_CHANGED' ORDER BY id DESC LIMIT 1")
        self.assertIn("xccy_basis_eur_bp", ev["payload_json"])
        pv = swaps.preview(rt, PB, "XCCY", TERMS)
        self.assertEqual(("1.9673865085", -588602, "-13.4065", "-12.4", -44190), (pv["annuity_star"], pv["pv_zero_c"], pv["spread"]["par_bp_4dp"],
                                                                                 pv["spread"]["dealt_bp"], pv["mark0_c"]))
        got = swaps_trs.mark_xccy(rt, CID, at=rt.clock.epoch())
        self.assertEqual((-592993, -592993), (got[0], got[1]["csa_exposure_c"]))


class FloatTerminationTests(FloatXccyBase):
    # ---- C-T14: termination (D17) -------------------------------------------------------------------------------------------------------
    def test_ct14_before_the_start_with_the_conversion_is_refused(self):
        rt = self.start()
        self.booked()
        n = len(q(rt, "SELECT 1 FROM journal_entries"))
        r = execute(rt, "otc.terminate", {"contract_id": CID}, "ui:term-0001")
        self.assertEqual((409, "XCCY_NOT_STARTED"), (r.http, r.code))
        self.assertIn("the start exchange and its conversion settle together on 2026-11-02; terminate from 2026-11-02 00:45 ET", r.body["error"]["message"])
        self.assertEqual(("OPEN", n), (contract(rt)["status"], len(q(rt, "SELECT 1 FROM journal_entries"))))

    def test_ct14_after_the_start_cash_settled_in_usd(self):
        """After S at the example state (11-02 10:00 ET, EURUSD 1.1158, value 11-04, no night accrued yet), on the example's rows re-dated 10-30 (the
        live 3-business-day rule; the plan's -696.34 on the 10-27 curves is pinned in test_ct7_after_start_values_on_the_example_curves): the full
        value re-priced with the basis level moved 1.0 bp down (the half-width against the Fund, the payer of the EUR leg) = -696.74 (the designer's
        pricer with T0 = 10-30; -259.82 at the mid level).  B = 2,231,600.00 - 1.1158 x 2,000,000.00 = 0.00, so the clean close-out is -696.74 too.
        The day-one VM posted 447.82 comes back; the Fund pays 696.74: net cash 447.82 - 696.74 = -248.92, one Treasury sweep.  No EUR moves (the
        Fund holds none); 1360 / 2360 are cleared through 3900 at 1.1158; IM 44,632.00 released."""
        rt = self.start()
        self.booked()
        set_fx(rt, "1.1158", date(2026, 10, 29), "17:00")
        rt.clock.set("2026-10-29T17:10:00-04:00")
        self.assertEqual(-44782, vm(rt, "2026-10-29").body["delta_c"])
        self.run_start()
        fxcurves.fixture_curves(rt, d0=date(2026, 10, 30), persist=False)
        rt.clock.set("2026-11-02T10:00:00-05:00")
        set_fx(rt, "1.1158")
        tre0 = bal(rt, TRE, "1010", "USD")
        r = execute(rt, "otc.terminate", {"contract_id": CID}, "ui:term-0002")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((-69674, -69674, 0, 4463200), (r.body["termination_value_c"], r.body["inputs"]["clean_c"], r.body["inputs"]["balance_sheet_c"],
                                                        r.body["im_released_c"]))
        self.assertEqual([("1360", "USD", CID, -223160000, -223160000), ("2360", "EUR", CID, 200000000, 223160000), ("3900", "EUR", None, -200000000, -223160000),
                          ("3900", "USD", None, 223160000, 223160000)], lines_by_idem(rt, f"xccy:{CID}:terminate", base=True))
        self.assertEqual([("1010", "USD", None, 44782), ("1320", "USD", CID, -44782), ("4450", "USD", CID, 69674), ("1010", "USD", None, -69674)],
                         lines_by_idem(rt, f"otc:{CID}:terminate"))
        self.assertEqual(tre0 - 24892, bal(rt, TRE, "1010", "USD"))
        self.assertEqual(("TERMINATED", 0, 0, 0, 0, 0), (contract(rt)["status"], bal(rt, PB, "1010", "EUR"), bal(rt, PB, "1360", "USD", CID),
                                                        bal(rt, PB, "2360", "EUR", CID), bal(rt, PB, "1320", "USD", CID), bal(rt, PB, "1330", "USD", CID)))
        self.assertEqual(-69674, bal(rt, PB, "4450", "USD", CID))
        assert_integrity(self, rt, "full")


    def test_ct14_termination_catches_up_the_owed_nights_first(self):
        """Terminated Wed 11-04 10:00 ET with the nights of 11-02 and 11-03 not yet accrued by the scheduler: the termination posts them first
        (the same ``accr:{kind}:{cid}:{night}`` entries, MONEY-2), so the balance sheet it clears holds them: USD 2 nights to_c(2,231,600 x
        0.000219456483410494) = 489.74 (1225); EUR to_c(2,000,000 x (0.000135560149382716 + 0.00001 x 2 / 360)) = 271.23 (2340).  At 1.1158:
        B = 2,231,600.00 + 489.74 - 2,231,600.00 (fx_c of EUR 2,000,000.00) - 302.64 (fx_c(271.23 x 1.1158 = 302.638434)) = 187.10, received in
        USD on the clearing entry; the full value = the clean close-out + 187.10.  Nothing in EUR moves."""
        rt = self.start()
        self.booked()
        self.run_start()
        fxcurves.fixture_curves(rt, d0=date(2026, 10, 30), persist=False)
        rt.clock.set("2026-11-04T10:00:00-05:00")
        set_fx(rt, "1.1158")
        r = execute(rt, "otc.terminate", {"contract_id": CID}, "ui:term-0003")
        self.assertTrue(r.ok, r.body)
        cmd = one(rt, "SELECT command_id FROM journal_entries WHERE idem_key=?", (f"xccy:{CID}:terminate",))["command_id"]
        for idem in (f"accr:swaprcv:{CID}:2026-11-02", f"accr:swaprcv:{CID}:2026-11-03", f"accr:swappay:{CID}:2026-11-02", f"accr:swappay:{CID}:2026-11-03"):
            self.assertEqual(cmd, one(rt, "SELECT command_id FROM journal_entries WHERE idem_key=?", (idem,))["command_id"])
        self.assertEqual(18710, r.body["inputs"]["balance_sheet_c"])
        self.assertEqual(r.body["inputs"]["clean_c"] + 18710, r.body["termination_value_c"])
        self.assertEqual([("1225", "USD", CID, -48974, -48974), ("1360", "USD", CID, -223160000, -223160000), ("2340", "EUR", CID, 27123, 30264),
                          ("2360", "EUR", CID, 200000000, 223160000), ("3900", "EUR", None, -200027123, -223190264), ("3900", "USD", None, 223190264, 223190264),
                          ("1010", "USD", None, 18710, 18710)], lines_by_idem(rt, f"xccy:{CID}:terminate", base=True))
        self.assertEqual((0, 0, 0, 0, 0, "TERMINATED"), (bal(rt, PB, "1010", "EUR"), bal(rt, PB, "1225", "USD", CID), bal(rt, PB, "2340", "EUR", CID),
                                                        bal(rt, PB, "1360", "USD", CID), bal(rt, PB, "2360", "EUR", CID), contract(rt)["status"]))
        assert_integrity(self, rt, "full")


class DisplayOnlyDifferenceTests(FloatXccyBase):
    """FX_CCS_PLAN 9 item 2 (1.0.6 against 1.0.6.1 on the owner's book, before any booking): for a contract booked without an owner reference -
    every contract of the owner's book (CDX-000001/2, SWP-000001..6: TRS, IRS and CDX; no XCCY, no FX forward) - this package changes display
    only.  Its row keeps every value (``client_ref`` is an additive column, NULL for it; ``swaps.bind`` adds the column and its partial unique
    index), its timeline has no new step, and its contract view gains three read-only fields: ``client_ref`` None, ``linked_deals`` [] (no FX
    desk deal is linked to it) and ``confirmation`` (the parties, the dates, the notional, the CSA and the IM in words, with the confirmation
    status the contract already had).  The cross-currency swap's Settings > Unsupported row (P8B-05) now says what 1.0.6.1 offers and what it
    still does not."""

    def test_a_contract_without_a_ref_gains_display_fields_only(self):
        rt = self.start()
        group = rt.instruments.get("AAPL").session_group
        with rt.db.writer() as tx:
            rt.instruments.upsert(tx, instrument_id="VMRK", kind="REIT", symbol="VMRK", name="Vivmark Residential", currency="USD", session_group=group)
        rt.instruments.load()
        rt.marks.set("VMRK", "57.10", "delayed", rt.clock.epoch())
        # receive the total return on 1,000 VMRK at the 10:00 ET print 57.10, no client_ref: notional 1,000 x 57.10 = 57,100.00;
        # IM 10 % (the GS TRS schedule) x 57,100.00 = 5,710.00
        r = execute(rt, "otc.book", {"account_id": PB, "kind": "TRS", "terms": {"underlying_id": "VMRK", "units": 1000, "receiver": True,
                                                                              "reset_months": 1, "tenor_months": 12}}, "ui:trs-noref-0001")
        self.assertTrue(r.ok, r.body)
        row = contract(rt)
        self.assertEqual((CID, "TRS", None, 5710000, 571000), (row["contract_id"], row["kind"], row["client_ref"], row["notional_c"], row["im_c"]))
        with rt.db.reader() as conn:
            self.assertIn("ux_otc_client_ref", [x["name"] for x in conn.execute("PRAGMA index_list(otc_contracts)").fetchall()])
        # the view's three new read-only fields
        v = swaps.contract_view(rt, CID)
        self.assertEqual((None, []), (v["client_ref"], v["linked_deals"]))
        c = v["confirmation"]
        self.assertEqual((swaps.PENDING_CONFIRM_LABEL, "Goldman Sachs", "total return swap", 5710000, 571000, None),
                         (c["label"], c["parties"]["counterparty"], c["kind"], c["notional_c"], c["im_c"], c["client_ref"]))
        self.assertIn("variation margin daily in USD (CTM)", c["csa"])
        self.assertNotIn("dealer-confirmed", json.dumps(v))
        # the timeline gives the steps 1.0.6 gives (booked, IM 5,710.00, pending confirmation): no conversion, funding, true-up or deal step
        self.assertEqual([("BOOKED", None), ("IM", 571000), ("CONFIRM_PENDING", None)],
                         [(x["step"], x.get("amount_c")) for x in swaps.contract_timeline(rt, CID)])
        # P8B-05: the cross-currency swap's Unsupported row (GET /api/unsupported) names what this version offers and what it does not
        from finsim.terminal import api
        rows = [x for x in api.unsupported_rows(rt) if x.get("where") == "prime-broker OTC tab, Swaps (cross-currency swap)"]
        self.assertEqual(["NOT_OFFERED"], [x["state"] for x in rows])
        for words in ("other than EUR ESTR / USD SOFR", "GBP SONIA", "JPY TONA", "fixed / floating"):
            self.assertIn(words, rows[0]["item"])
        self.assertIn("this version offers the EUR/USD float / float swap", rows[0]["wording"])
        self.assertNotIn("cross-currency swaps are fixed / fixed in this version", rows[0]["wording"])       # the 1.0.6 words


class StaleListingTests(unittest.TestCase):
    """INSTITUTIONAL_V3_PLAN 6.1 R-D (built in 1.0.6.1 as one helper, K-V5): a TRS preview and booking refuse an exchange-listed underlying whose
    last observed trade is older than 5 sessions - 409 STALE_LISTING "no trade since <date>: the security may be delisted or suspended".
    AvalonBay (AVB) last printed 184.06 on Fri 2026-08-14 16:00 ET (NYSE removed it before the open of 08-17); on Thu 2026-10-29 10:00 ET the
    sessions that closed since are 08-17..08-31 (11), September (22 weekdays - Labor Day = 21) and 10-01..10-28 (20) = 52.  Vivmark (VMRK) printed
    today: 0.  Boundary: a print of Wed 10-21 leaves 10-22, 10-23, 10-26, 10-27, 10-28 = 5 (allowed); one of Tue 10-20 leaves 6 (refused)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-stale-")
        self.rt = rt = runtime(self.tmp)
        group = rt.instruments.get("AAPL").session_group
        with rt.db.writer() as tx:
            for iid, name in (("AVB", "AvalonBay Communities, Inc."), ("VMRK", "Vivmark Residential"), ("OLDX", "Old Test Co"), ("NEWX", "New Test Co")):
                rt.instruments.upsert(tx, instrument_id=iid, kind="REIT" if iid in ("AVB", "VMRK") else "EQUITY", symbol=iid, name=name, currency="USD",
                                      session_group=group)
        rt.instruments.load()
        rt.marks.set("AVB", "184.06", "close", epoch_of(date(2026, 8, 14), "16:00"))
        rt.marks.set("VMRK", "57.10", "delayed", rt.clock.epoch())
        rt.marks.set("OLDX", "10.00", "close", epoch_of(date(2026, 10, 20), "16:00"))
        rt.marks.set("NEWX", "10.00", "close", epoch_of(date(2026, 10, 21), "16:00"))

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rd1_the_helper_counts_sessions_since_the_last_print(self):
        rt = self.rt
        self.assertEqual((52, 0, 6, 5), tuple(swaps_trs.last_trade_age_sessions(rt, iid) for iid in ("AVB", "VMRK", "OLDX", "NEWX")))
        # instruments without exchange prints are never counted: an FX pair, an unknown id, an OTC contract id
        self.assertEqual((None, None, None), (swaps_trs.last_trade_age_sessions(rt, "EURUSD"), swaps_trs.last_trade_age_sessions(rt, "NOPE"),
                                              swaps_trs.last_trade_age_sessions(rt, "SWP-000001")))
        # INSTITUTIONAL_V3_PLAN 6.1 R-D (integration): a Treasury and a private company pass too, however old their last mark (a bond trades
        # over the counter; a private company has no exchange print) - both last marked Fri 08-14, like AVB
        group = rt.instruments.get("AAPL").session_group
        with rt.db.writer() as tx:
            rt.instruments.upsert(tx, instrument_id="912797XX1", kind="BOND", symbol="912797XX1", name="US Treasury bill (test)", currency="USD",
                                  session_group=group)
            rt.instruments.upsert(tx, instrument_id="PRIVX", kind="PRIVATE", symbol="PRIVX", name="Private Test Co", currency="USD",
                                  session_group="PRIVATE_MARKETS")
        rt.instruments.load()
        for iid in ("912797XX1", "PRIVX"):
            rt.marks.set(iid, "99.50", "close", epoch_of(date(2026, 8, 14), "16:00"))
        self.assertEqual((None, None), (swaps_trs.last_trade_age_sessions(rt, "912797XX1"), swaps_trs.last_trade_age_sessions(rt, "PRIVX")))
        self.assertEqual((None, None), (swaps_trs.stale_listing_check(rt, "912797XX1"), swaps_trs.stale_listing_check(rt, "PRIVX")))
        with self.assertRaises(ConflictError) as cm:
            swaps_trs.stale_listing_check(rt, "AVB")
        self.assertEqual("STALE_LISTING", cm.exception.code)
        self.assertIn("AVB: no trade since 2026-08-14", cm.exception.message)
        self.assertIn("the security may be delisted or suspended", cm.exception.message)
        self.assertEqual(5, swaps_trs.stale_listing_check(rt, "NEWX")["sessions_since"])
        with self.assertRaises(ConflictError):
            swaps_trs.stale_listing_check(rt, "OLDX")

    def test_rd1_trs_preview_and_booking_refuse_avb_and_pass_vmrk(self):
        rt = self.rt
        terms = {"units": 1000, "receiver": False, "reset_months": 1, "tenor_months": 12}
        with self.assertRaises(ConflictError) as cm:
            swaps.preview(rt, PB, "TRS", dict(terms, underlying_id="AVB"))
        self.assertEqual("STALE_LISTING", cm.exception.code)
        n = len(q(rt, "SELECT 1 FROM journal_entries"))
        r = execute(rt, "otc.book", {"account_id": PB, "kind": "TRS", "terms": dict(terms, underlying_id="AVB")}, "ui:trs-avb-0001")
        self.assertEqual((409, "STALE_LISTING"), (r.http, r.code))
        self.assertEqual((n, 0), (len(q(rt, "SELECT 1 FROM journal_entries")), len(q(rt, "SELECT 1 FROM otc_contracts"))))
        pv = swaps.preview(rt, PB, "TRS", dict(terms, underlying_id="VMRK"))
        self.assertEqual(("VMRK", "57.1"), (pv["underlying_id"], pv["reset_px"]))


class _Subscriptions:
    """The quote cache's subscription hook as ``swaps_trs._pin`` uses it (PO-08): the ids asked for at ORDER priority, recorded."""

    def __init__(self):
        self.calls = []

    def subscribe(self, ids, ttl_s, priority):
        self.calls.append((list(ids), int(ttl_s), priority))


class StaleListingOutOfSessionTests(unittest.TestCase):
    """V1061-04: on the owner's copy at Thu 2026-10-29 04:09 ET (before the US equity session) an AVB TRS preview answered CLOSE_PENDING ("the
    official close of 2026-10-28 is not recorded yet ... it is being recorded now") three times in 17 s and would forever: AVB had no observed
    print and no recorded close, so R-D's guard had nothing to measure (it passes an instrument never observed) and the CLOSE_PENDING path
    returned before anything asked for the quote - nothing recorded it.  Now that path requests the underlying at ORDER priority (the quote
    worker's next tick, 15 s), and the preview the ticket sends again (retry_ms 2,000) sees the fetched print - Yahoo's last AVB price 184.06
    of Fri 2026-08-14 16:00 ET - and refuses: the sessions closed since are 08-17..08-31 (11), September (21) and 10-01..10-28 (20) = 52, more
    than 5.  (The test's instrument is GONE, a REIT with no print, so the class above keeps its AVB mark.)"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-stale-oos-")
        self.rt = rt = runtime(self.tmp, at="2026-10-29T04:09:00-04:00")
        group = rt.instruments.get("AAPL").session_group
        with rt.db.writer() as tx:
            rt.instruments.upsert(tx, instrument_id="GONE", kind="REIT", symbol="GONE", name="Gone Residential (test)", currency="USD", session_group=group)
        rt.instruments.load()
        rt.cache = _Subscriptions()

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_v1061_04_the_close_pending_path_requests_the_quote_then_rd_refuses(self):
        rt = self.rt
        terms = {"underlying_id": "GONE", "units": 1000, "receiver": False, "reset_months": 1, "tenor_months": 12}
        with self.assertRaises(ConflictError) as cm:
            swaps.preview(rt, PB, "TRS", terms)
        self.assertEqual("CLOSE_PENDING", cm.exception.code)
        self.assertIn("the official close of 2026-10-28 is not recorded yet", cm.exception.message)
        self.assertIn("its quote is being fetched now", cm.exception.message)
        self.assertEqual((2000, True), (cm.exception.details["retry_ms"], cm.exception.details["fetching"]))
        self.assertEqual([(["GONE"], swaps_trs.PIN_TTL_S, "ORDER")], rt.cache.calls)          # asked for at ORDER priority (it was never asked)
        # the worker's next tick observes Yahoo's last print: 184.06 of Fri 2026-08-14 16:00 ET; the ticket's next preview is refused by R-D
        rt.marks.set("GONE", "184.06", "close", epoch_of(date(2026, 8, 14), "16:00"))
        with self.assertRaises(ConflictError) as cm:
            swaps.preview(rt, PB, "TRS", terms)
        self.assertEqual("STALE_LISTING", cm.exception.code)
        self.assertIn("GONE: no trade since 2026-08-14 (52 sessions without a print, at most 5)", cm.exception.message)
        n = len(q(rt, "SELECT 1 FROM journal_entries"))
        r = execute(rt, "otc.book", {"account_id": PB, "kind": "TRS", "terms": terms}, "ui:trs-gone-0001")
        self.assertEqual((409, "STALE_LISTING"), (r.http, r.code))
        self.assertEqual((n, 0), (len(q(rt, "SELECT 1 FROM journal_entries")), len(q(rt, "SELECT 1 FROM otc_contracts"))))

    def test_v1061_04_without_a_quote_cache_the_wording_stays(self):
        # a runtime without a quote cache (offline tools): nothing is asked for, and the refusal says so the way 1.0.6 did
        rt = self.rt
        rt.cache = None
        with self.assertRaises(ConflictError) as cm:
            swaps.preview(rt, PB, "TRS", {"underlying_id": "GONE", "units": 1000, "receiver": False, "reset_months": 1, "tenor_months": 12})
        self.assertEqual(("CLOSE_PENDING", False), (cm.exception.code, cm.exception.details["fetching"]))
        self.assertIn("it is being recorded now", cm.exception.message)


if __name__ == "__main__":
    unittest.main()
