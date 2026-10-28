"""FINSIM1 P4 (C10-C12, OMS-04, PORTFOLIO-01 / -08): the owner's Goldman OTC contracts booked AS OF their trade date with
``otc.book_as_of`` - the batch IMP-F1-20261027-A payloads of lines 24-28 - and their lifecycle caught up with the inputs OF each
date.  Every expected number is worked by hand in a comment from the inputs; nothing is pasted from the code's output.

No network: a FakeClock, a RateStore with the FRED prints of the owner's database (SOFR 10-15 .. 10-26; ICE BofA HY OAS; the IG
OAS of 10-21 the reconciliation fetched, 0.88 %, and for the VM dates the IG values the reconciliation implied from its marks), a
CurveStore holding the Treasury par curves of 10-21 / 10-22 / 10-23 / 10-26 exactly as the owner's database recorded them, and the
ORCL / META closes of 10-21 .. 10-26 recorded as CLOSE marks (as the import's S2 records them).  The book F1 starts on Tue
2026-10-20 (its 25,000,000.00 capital is dated 10-20, so an as-of need of 10-21 finds Treasury cash on that business date), the
GS prime-broker account opens on 10-21 and the bookings run on Wed 2026-10-28 10:00 ET, the apply day.

The GS schedule (seeds, assumed): TRS financing SOFR - 0.60 % (trs_spread 0.0060 x otc_spread_mult 1), TRS IM 10 % of the notional,
IRS half-width 1 bp and IM |DV01| x 50 bp, CDX half-spread IG 0.50 bp / HY 2.00 bp and IM IG 2 % / HY 6 % (cdx_terms seeds); the
CDX basis is calibrated first (the import's S4): IG -24.5 bp, HY +23.0 bp.

Hand-worked figures (FINSIM1_PLAN P4 acceptance 1)
- TRS ORCL short (pay total return) 38,700 @ 129.13: notional 38,700 x 129.13 = 4,997,331.00; IM 10 % = 499,733.10.  The mark of
  a payer is -38,700 x (S - 129.13): close 10-21 129.13 -> 0.00 (day-0 VM 0.00, NO_CHANGE); 10-22 125.54 -> -38,700 x -3.59 =
  +138,933.00 (VM +138,933.00 received); 10-23 126.16 -> -38,700 x -2.97 = +114,939.00 (VM -23,994.00); 10-26 126.06 -> +118,809.00
  (VM +3,870.00).  Financing RECEIVED at SOFR - 0.60 %: night 10-21 at 3.88 % -> 4,997,331 x 0.0328 / 360 = 455.3124 -> 455.31
  (business date 10-22); night 10-22 at 3.90 % (a new segment: the rate changed) -> 4,997,331 x 0.0330 / 360 = 458.0887 -> 458.09
  (business date 10-23); 913.40 on 4451 through night 10-22.
- TRS META short 7,200 @ 697.52: notional 5,022,144.00; IM 502,214.40; closes 648.85 / 642.62 / 630.56 -> VM -7,200 x (648.85 -
  697.52) = +350,424.00, -7,200 x (642.62 - 648.85) = +44,856.00, -7,200 x (630.56 - 642.62) = +86,832.00.
- IRS pay 4.720 % on 7,500,000.00, 3Y, fixed semi-annual ACT/360, SOFR compounded in arrears annually: on the 10-21 curve the six
  semi-annual factors give par 4.890 % and annuity A = 2.7644767216; DV01 = 7,500,000 x 0.0001 x 2.7644767216 = 2,073.3575 ->
  2,073.36; IM = 2,073.36 x 50 = 103,668.00; the contract curve spread = 4.720 - 4.890 - 0.010 = -0.180 % = -18.0 bp; mark0 =
  7,500,000 x (4.890 - 0.180 - 4.720) % x A = -7,500,000 x 0.0001 x 2.7644767216 = -2,073.36 (the day-0 VM, posted).  On the curves
  of 10-22 / 10-23 / 10-26 (par 4.720 / 4.750 / 4.790 %, annuity 2.7715046766 / 2.7699756644 / 2.7690540360) the marks are
  7.5M x (par - 0.0018 - 0.0472) x A = -37,415.31 / -31,162.23 / -22,844.70: VM -35,341.95, +6,253.08, +8,317.53.
- CDX.NA.IG S47 5Y buy protection 3,000,000.00 at 64 bp: proxy 0.88 % = 88 bp + basis -24.5 = mid 63.5 bp, + half-spread 0.5 = 64 bp
  (the dealt spread, exactly).  On the 10-21 curve RPV01(64 bp) = 4.4901: the upfront a buyer pays = 3,000,000 x (64 - 100) bp x
  4.4901 = -48,492.57 (received, booked to 2345: the contract's cost); rebate 3,000,000 x 1 % x 31 / 360 = 2,583.33 (09-21 .. 10-21);
  initial payment 48,492.57 + 2,583.33 = 51,075.90 due T+1 = 10-22; IM 2 % = 60,000.00; mark0 at mid 63.5 bp = -49,176.13 (the
  day-0 VM, posted); unrealised = mark0 - cost = -49,176.13 + 48,492.57 = -683.56 = the cost of dealing; realised 0.00.  The
  premium's first new night is 10-22: cumulative 3,000,000 x 1 % x 32 / 360 = 2,666.67 - the rebate 2,583.33 = 83.34 (business date
  10-23); night 10-21, which the rebate covers, is never accrued.
- CDX.NA.HY S47 5Y buy protection 4,000,000.00 at 352 bp: proxy 3.27 % = 327 bp + basis 23.0 + half-spread 2.0 = 352 bp; RPV01 4.0815;
  upfront 4,000,000 x (352 - 500) bp x 4.0815 = -241,622.83 (received); rebate 4,000,000 x 5 % x 31 / 360 = 17,222.22; IM 6 % =
  240,000.00; mark0 at 350 bp = -245,053.51; cost of dealing -245,053.51 + 241,622.83 = -3,430.68.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import types
import unittest
from datetime import date, timedelta
from decimal import Decimal

from tests.terminal_helpers import assert_integrity, bal, execute, make_runtime
from finsim.terminal import lifecycle, prime
from finsim.terminal.clock import epoch_of
from finsim.terminal.products import swaps, swaps_cds as cds, swaps_trs
from finsim.terminal.quotes.providers import ParCurve, TENOR_YEARS
from finsim.terminal.quotes.rates import CurveStore, RateStore

D = Decimal
PB = "F1:PB_GS"
BATCH = "IMP-F1-20261027-A"
EXEC = "2026-10-21T19:45:00-04:00"
APPLY_AT = "2026-10-28T10:00:00-04:00"
#: the UST_PAR curves of the owner's database (read-only copy 2026-10-27 14:54 EDT), percent by tenor
CURVES = {
    "2026-10-21": {"1M": "4.09", "2M": "4.16", "3M": "4.25", "4M": "4.34", "6M": "4.35", "1Y": "4.52", "2Y": "4.81", "3Y": "4.89", "5Y": "4.95", "7Y": "5.03",
                   "10Y": "5.13", "20Y": "5.47", "30Y": "5.42"},
    "2026-10-22": {"1M": "4.11", "2M": "4.16", "3M": "4.22", "4M": "4.29", "6M": "4.29", "1Y": "4.41", "2Y": "4.65", "3Y": "4.72", "5Y": "4.78", "7Y": "4.86",
                   "10Y": "4.96", "20Y": "5.32", "30Y": "5.29"},
    "2026-10-23": {"1M": "4.11", "2M": "4.16", "3M": "4.22", "4M": "4.3", "6M": "4.3", "1Y": "4.44", "2Y": "4.69", "3Y": "4.75", "5Y": "4.82", "7Y": "4.89",
                   "10Y": "5", "20Y": "5.37", "30Y": "5.33"},
    "2026-10-26": {"1M": "4.11", "2M": "4.18", "3M": "4.22", "4M": "4.32", "6M": "4.35", "1Y": "4.47", "2Y": "4.68", "3Y": "4.79", "5Y": "4.84", "7Y": "4.91",
                   "10Y": "5", "20Y": "5.35", "30Y": "5.31"},
}
SOFR = [("2026-10-15", "3.89"), ("2026-10-16", "3.88"), ("2026-10-19", "3.87"), ("2026-10-20", "3.88"), ("2026-10-21", "3.88"), ("2026-10-22", "3.90"),
        ("2026-10-23", "3.92"), ("2026-10-26", "3.93")]
HY_OAS = [("2026-10-19", "3.29"), ("2026-10-20", "3.33"), ("2026-10-21", "3.27"), ("2026-10-22", "3.40"), ("2026-10-23", "3.38"), ("2026-10-26", "3.30")]
IG_OAS = [("2026-10-20", "0.89"), ("2026-10-21", "0.88"), ("2026-10-22", "0.89"), ("2026-10-23", "0.91"), ("2026-10-26", "0.90")]
CLOSES = {"ORCL": {"2026-10-21": "129.13", "2026-10-22": "125.54", "2026-10-23": "126.16", "2026-10-26": "126.06"},
          "META": {"2026-10-21": "697.52", "2026-10-22": "648.85", "2026-10-23": "642.62", "2026-10-26": "630.56"}}


def as_of(line_key: str, trade: str = "2026-10-21", value: str = "2026-10-21", exec_ts: str = EXEC, **extra) -> dict:
    return dict({"trade_date": trade, "value_date": value, "exec_ts": exec_ts, "batch_id": BATCH, "line_key": line_key}, **extra)


#: the batch payloads of lines 24-28 (IMP-F1-20261027-A), verbatim
LINES = {
    "TRS_ORCL": ("imp:b43f9a9dbbcba640298db075", {"account_id": PB, "kind": "TRS", "terms": {"underlying_id": "ORCL", "units": 38700, "receiver": False, "reset_months": 1,
                                                                                          "tenor_months": 12, "reset_px": "129.13"}}),
    "TRS_META": ("imp:c80f3da48b497b280f8fda29", {"account_id": PB, "kind": "TRS", "terms": {"underlying_id": "META", "units": 7200, "receiver": False, "reset_months": 1,
                                                                                          "tenor_months": 12, "reset_px": "697.52"}}),
    "CDX_IG": ("imp:989ff3b7faebab0b6f157737", {"account_id": PB, "kind": "CDX", "terms": {"index": "IG", "series": 47, "tenor": "5Y", "side": "BUY_PROTECTION",
                                                                                        "notional": "3000000.00", "dealt_spread_bp": "64"}}),
    "CDX_HY": ("imp:402123322db438f9a97535c1", {"account_id": PB, "kind": "CDX", "terms": {"index": "HY", "series": 47, "tenor": "5Y", "side": "BUY_PROTECTION",
                                                                                        "notional": "4000000.00", "dealt_spread_bp": "352"}}),
    "IRS": ("imp:6cd42a13acf7b6a32636f3ec", {"account_id": PB, "kind": "IRS", "terms": {"notional": "7500000.00", "pay_fixed": True, "tenor_months": 36,
                                                                                     "fixed_rate": "0.04720", "fixed_months": 6, "fixed_basis": "ACT/360",
                                                                                     "float_index": "SOFR", "float_basis": "ACT/360", "float_months": 12,
                                                                                     "fixing": "COMPOUND_ARREARS", "calibrate_curve_spread_to_rate": True}}),
}


def payload(name: str, **as_of_extra) -> dict:
    key, body = LINES[name]
    p = json.loads(json.dumps(body))
    p["as_of"] = as_of(key, **as_of_extra)
    return p


def _curve(ds: str) -> ParCurve:
    return ParCurve(date.fromisoformat(ds), {TENOR_YEARS[k]: D(v) for k, v in CURVES[ds].items()}, source="treasury.gov daily par yield curve (test copy)")


def _h_open_pb_unchecked(ctx, p):
    return prime.open_pb(ctx, p["lifetime_id"], p["broker"], check_eligibility=False)


def runtime(tmp, curves=tuple(CURVES), closes=True, ig_oas=True, at=APPLY_AT):
    """F1 from Tue 2026-10-20 09:00 (capital dated 10-20), GS PB opened Wed 10-21 19:44, the FRED prints and curves above, the closes recorded,
    prime / swaps / swaps_trs / swaps_cds bound, the CDX basis calibrated (S4); the clock left at the apply time."""
    rt = make_runtime(tmp, at="2026-10-20T09:00:00-04:00", books="real")
    prime.register(rt.bus)
    prime.bind(rt)
    rt.rates = RateStore()
    rt.rates.put("SOFR", [(date.fromisoformat(d), D(v)) for d, v in SOFR], persist=False)
    rt.rates.put("BAMLH0A0HYM2", [(date.fromisoformat(d), D(v)) for d, v in HY_OAS], persist=False)
    if ig_oas:
        rt.rates.put("BAMLC0A0CM", [(date.fromisoformat(d), D(v)) for d, v in IG_OAS], persist=False)
    rt.curve = CurveStore()
    for ds in curves:
        rt.curve.put(_curve(ds), persist=False)
    for mod, attr in ((swaps, "swaps"), (swaps_trs, "swaps_trs"), (cds, "swaps_cds")):
        mod.register(rt.bus, rt)
        setattr(rt, attr, mod)
        if hasattr(mod, "bind"):
            mod.bind(rt)
    rt.providers = types.SimpleNamespace(yahoo=rt.fp)
    lifecycle.register(rt.bus, rt)
    rt.lifecycle = lifecycle.LifecycleScheduler(rt.bus, rt.db, rt, rt.clock, rt.log)
    rt.clock.set("2026-10-21T19:44:00-04:00")
    r = execute(rt, "account.open_pb", {"lifetime_id": "F1", "broker": "GS"})
    assert r.ok, r.body
    if closes:
        with rt.db.writer() as tx:
            for iid, by in CLOSES.items():
                for ds, px in by.items():
                    tx.conn.execute("INSERT OR REPLACE INTO marks(instrument_id, mark_date, kind, price, currency, quality, source, method, quote_time, fetched_at, note)"
                                    " VALUES (?,?,'CLOSE',?,'USD','close','Yahoo daily bars (observed close)','daily_close',?,?,'recorded late')",
                                    (iid, ds, px, epoch_of(date.fromisoformat(ds), "16:00"), epoch_of(date(2026, 10, 27), "12:00")))
    rt.clock.set(at)
    for index, basis in (("IG", "-24.5"), ("HY", "23.0")):
        r = execute(rt, "cdx.terms", {"account_id": PB, "index": index, "basis_bp": basis}, f"impset:{BATCH}:S4:{index}", actor="import")
        assert r.ok, r.body
    return rt


def q(rt, sql, params=()):
    with rt.db.reader() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(rt, sql, params=()):
    rows = q(rt, sql, params)
    return rows[0] if rows else None


def entry(rt, idem):
    return one(rt, "SELECT * FROM journal_entries WHERE idem_key=?", (idem,))


def lines(rt, idem):
    e = entry(rt, idem)
    assert e is not None, idem
    return [(r["gl"], r["amount_c"]) for r in q(rt, "SELECT gl, amount_c FROM journal_lines WHERE entry_id=? ORDER BY line_no", (e["entry_id"],))]


def book(rt, name, actor="import", request_id=None, **as_of_extra):
    p = payload(name, **as_of_extra)
    return execute(rt, "otc.book_as_of", p, request_id or p["as_of"]["line_key"], actor=actor)


def vm(rt, cid, ds, suffix=""):
    return execute(rt, "swap.vm", {"contract_id": cid, "date": ds, "key": f"{cid}:{ds}"}, f"job:SWAP_VM:{cid}:{ds}{suffix}", actor="lifecycle")


def accrue(rt, kind, cid, night):
    key = f"{kind}:{cid}:{night}"
    return execute(rt, "lifecycle.accrue", {"key": key, "kind": kind, "ref": cid, "night": night}, f"job:ACCRUE_DAILY:{key}", actor="lifecycle")


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-otc-asof-")
        self.rt = runtime(self.tmp)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def booked(self, name, **kw):
        r = book(self.rt, name, **kw)
        self.assertTrue(r.ok, r.body)
        return r.body

    def counts(self):
        return tuple(len(q(self.rt, f"SELECT 1 FROM {t}")) for t in ("otc_contracts", "journal_entries", "settlements", "transfers", "otc_vm"))


class TrsAsOfTests(_Base):
    def test_trs_orcl_short_booked_at_the_dealt_reset_with_its_lifecycle(self):
        rt = self.rt
        b = self.booked("TRS_ORCL")
        cid = b["contract_id"]
        row = one(rt, "SELECT * FROM otc_contracts WHERE contract_id=?", (cid,))
        terms = json.loads(row["terms_json"])
        # the dealt terms exactly: reset 129.13 on 38,700 units, pay total return; notional 4,997,331.00; IM 499,733.10 (10 %)
        self.assertEqual(("TRS", "129.13", 499733100, 49973310, "2026-10-21", "2026-10-21", "2027-10-21", False, 38700),
                         (row["kind"], row["reset_px"], row["notional_c"], row["im_c"], row["start_date"], row["trade_date"], row["maturity_date"], terms["receiver"],
                          terms["units"]))
        # the as-of record: booking AS_OF, the line key, value date 10-21, CONFIRMED at the owner's instruction, entered now (the apply), executed 19:45 ET
        self.assertEqual(("AS_OF", LINES["TRS_ORCL"][0], "2026-10-21", "CONFIRMED", rt.clock.epoch(), epoch_of(date(2026, 10, 21), "19:45")),
                         (row["booking"], row["import_key"], row["value_date"], row["confirm_status"], row["entered_ts"], terms["dealt_ts"]))
        # the IM entry is dated the value date 10-21 (C10 / D7, not the 17:00 ET accounting day of a 19:45 booking): Dr 1330 / Cr 1010 499,733.10
        e = entry(rt, f"im:{cid}:open")
        self.assertEqual(("2026-10-21", [("1330", 49973310), ("1010", -49973310)]), (e["business_date"], lines(rt, f"im:{cid}:open")))
        # day-0 VM: the 10-21 close 129.13 = the reset -> mark 0.00 -> NO_CHANGE (nothing posted), never an error
        self.assertEqual(("SKIPPED", "NO_CHANGE"), (b["day0_vm"]["status"], b["day0_vm"]["code"]))
        # the catch-up's VMs in date order (C1, each date with its own close, business date its own date); the weekend values at Friday's close: unchanged
        for ds, delta, mark in (("2026-10-22", 13893300, 13893300), ("2026-10-23", -2399400, 11493900), ("2026-10-24", 0, 11493900), ("2026-10-25", 0, 11493900),
                                ("2026-10-26", 387000, 11880900)):
            r = vm(rt, cid, ds)
            if delta == 0:
                self.assertEqual(("SKIPPED", "NO_CHANGE", mark), (r.body["status"], r.body["code"], r.body["mark_c"]), ds)
                continue
            self.assertEqual(("DONE", delta, mark), (r.body["status"], r.body["delta_c"], r.body["mark_c"]), ds)
            self.assertEqual(ds, entry(rt, f"swapvm:{cid}:{ds}")["business_date"])
        # VM received 138,933.00 - 23,994.00 + 3,870.00 = 118,809.00 = the 10-26 mark (CTM: 2330 = the VM balance)
        self.assertEqual((11880900, 11880900), (one(rt, "SELECT vm_balance_c FROM otc_contracts WHERE contract_id=?", (cid,))["vm_balance_c"], bal(rt, PB, "2330", inst=cid)))
        # financing nights 10-21 (455.31, business date 10-22) and 10-22 (458.09, business date 10-23): received by the payer, Dr 1225 / Cr 4451
        for night, amount, bd in (("2026-10-21", 45531, "2026-10-22"), ("2026-10-22", 45809, "2026-10-23")):
            r = accrue(rt, "trsfin", cid, night)
            self.assertTrue(r.ok and r.body["status"] == "DONE", r.body)
            self.assertEqual(([("1225", amount), ("4451", -amount)], bd), (lines(rt, f"accr:trsfin:{cid}:{night}"), entry(rt, f"accr:trsfin:{cid}:{night}")["business_date"]))
        self.assertEqual((91340, -91340), (bal(rt, PB, "1225", inst=cid), bal(rt, PB, "4451", inst=cid)))         # 913.40 received (a negative cost)
        assert_integrity(self, rt)

    def test_trs_meta_short_vm_by_date(self):
        rt = self.rt
        b = self.booked("TRS_META")
        cid = b["contract_id"]
        row = one(rt, "SELECT * FROM otc_contracts WHERE contract_id=?", (cid,))
        self.assertEqual((502214400, 50221440, "697.52"), (row["notional_c"], row["im_c"], row["reset_px"]))
        self.assertEqual([35042400, 4485600, 8683200], [vm(rt, cid, ds).body["delta_c"] for ds in ("2026-10-22", "2026-10-23", "2026-10-26")])
        assert_integrity(self, rt)

    def test_trs_close_differs_from_the_dealt_reset(self):
        """D10 / F05: a recorded 10-21 close of 129.12 never changes the contract - it books reset 129.13 and the day-0 VM moves the difference:
        -38,700 x (129.12 - 129.13) = +387.00 received (the short gains), posted on the value date."""
        rt = self.rt
        with rt.db.writer() as tx:
            tx.conn.execute("UPDATE marks SET price='129.12' WHERE instrument_id='ORCL' AND mark_date='2026-10-21' AND kind='CLOSE'")
        b = self.booked("TRS_ORCL")
        cid = b["contract_id"]
        self.assertEqual("129.13", one(rt, "SELECT reset_px FROM otc_contracts WHERE contract_id=?", (cid,))["reset_px"])
        self.assertEqual(("DONE", 38700, 38700), (b["day0_vm"]["status"], b["day0_vm"]["delta_c"], b["day0_vm"]["mark_c"]))
        e = entry(rt, f"swapvm:{cid}:2026-10-21")
        self.assertEqual(("2026-10-21", [("1010", 38700), ("2330", -38700)]), (e["business_date"], lines(rt, f"swapvm:{cid}:2026-10-21")))
        assert_integrity(self, rt)


class IrsAsOfTests(_Base):
    def test_irs_pay_fixed_calibrated_curve_spread_and_vm_on_each_dates_curve(self):
        rt = self.rt
        b = self.booked("IRS")
        cid = b["contract_id"]
        row = one(rt, "SELECT * FROM otc_contracts WHERE contract_id=?", (cid,))
        t = json.loads(row["terms_json"])
        # the dealt rate exactly; the curve spread -18.0 bp; DV01 2,073.36; IM 103,668.00; start 10-21, maturity 2029-10-22 (10-21-2029 is a Sunday)
        self.assertEqual(("0.04720", "-0.00180", "-18.0", "0.04890", 10366800, "2026-10-21", "2029-10-22", "COMPOUND_ARREARS", "ACT/360"),
                         (t["fixed_rate"], t["curve_spread"], t["curve_spread_bp"], t["par_rate"], row["im_c"], row["start_date"], row["maturity_date"], t["fixing"],
                          t["fixed_basis"]))
        self.assertEqual((-207336, 207336), (t["mark0_c"], t["dv01_0_c"]))
        self.assertEqual("contract curve spread (SOFR swap vs Treasury par curve) -18.0 bp, calibrated at booking", b["contract"]["curve_spread_text"])
        # day-0 VM = mark0 -2,073.36 posted (Dr 1320 / Cr 1010) on 10-21, the IM on 10-21 too
        self.assertEqual(("DONE", -207336, -207336), (b["day0_vm"]["status"], b["day0_vm"]["mark_c"], b["day0_vm"]["delta_c"]))
        self.assertEqual(("2026-10-21", [("1320", 207336), ("1010", -207336)]), (entry(rt, f"swapvm:{cid}:2026-10-21")["business_date"], lines(rt, f"swapvm:{cid}:2026-10-21")))
        self.assertEqual("2026-10-21", entry(rt, f"im:{cid}:open")["business_date"])
        # the VM by date on the curve OF each date (C12): -35,341.95 / +6,253.08 / +8,317.53 -> marks -37,415.31 / -31,162.23 / -22,844.70
        out = [(vm(rt, cid, ds).body.get("delta_c"), one(rt, "SELECT mark_c FROM otc_vm WHERE contract_id=? AND vm_date=?", (cid, ds))["mark_c"])
               for ds in ("2026-10-22", "2026-10-23", "2026-10-26")]
        self.assertEqual([(-3534195, -3741531), (625308, -3116223), (831753, -2284470)], out)
        # the pure function on the 10-22 curve agrees: 7.5M x (0.04720 - 0.0018 - 0.04720) x 2.7715046766 = -37,415.31
        self.assertEqual(-3741531, swaps.model_mark_c(750000000, True, D("0.04720"), D("0.04720") + D("-0.00180"), D("2.7715046766")))
        # 10-27 has no curve recorded: the curve of 10-26 (one business day back) values it - unchanged -> NO_CHANGE, not CAUGHT_UP
        self.assertEqual("NO_CHANGE", vm(rt, cid, "2026-10-27").body["code"])
        # accruals from the value date, each night D on business date D + 1: fixed 7,500,000 x 4.72 % / 360 = 983.33 (Dr 4453 / Cr 2340); SOFR
        # compounded: night 10-21 at 3.88 % -> to_c(7,500,000 x 0.0388 / 360) = to_c(808.3333) = 808.33 (Dr 1225 / Cr 4453)
        for kind, gl_lines in (("swappay", [("4453", 98333), ("2340", -98333)]), ("swaprcv", [("1225", 80833), ("4453", -80833)])):
            self.assertTrue(accrue(rt, kind, cid, "2026-10-21").ok)
            self.assertEqual((gl_lines, "2026-10-22"), (lines(rt, f"accr:{kind}:{cid}:2026-10-21"), entry(rt, f"accr:{kind}:{cid}:2026-10-21")["business_date"]))
        assert_integrity(self, rt)

    def test_irs_from_the_ui_stays_off_market(self):
        """Owner Q1 variant A is an as-of booking of a dealt rate only: ``otc.book`` from the UI refuses the calibration flag and still refuses a
        rate better than the dealer's quote (OFF_MARKET)."""
        rt = self.rt
        terms = dict(LINES["IRS"][1]["terms"])
        r = execute(rt, "otc.book", {"account_id": PB, "kind": "IRS", "terms": terms}, "ui:irs-calibrate-0001")
        self.assertEqual((400, "TERMS"), (r.http, r.code))
        terms.pop("calibrate_curve_spread_to_rate")
        rt.curve.put(_curve("2026-10-26"), persist=False)
        r = execute(rt, "otc.book", {"account_id": PB, "kind": "IRS", "terms": terms}, "ui:irs-offmarket-0001")
        self.assertEqual((400, "OFF_MARKET"), (r.http, r.code))
        self.assertEqual(0, len(q(rt, "SELECT 1 FROM otc_contracts")))


class CdxAsOfTests(_Base):
    def test_cdx_ig_upfront_to_cost_rebate_initial_payment_and_im(self):
        rt = self.rt
        b = self.booked("CDX_IG")
        cid = b["contract_id"]
        row = one(rt, "SELECT * FROM otc_contracts WHERE contract_id=?", (cid,))
        t = json.loads(row["terms_json"])
        self.assertEqual(("64.0000", "63.5000", "88.0000", "-24.5", 47, "2031-12-20", "2026-09-21", 31), (t["deal_spread_bp"], t["mid_spread_bp"], t["proxy_bp"], t["basis_bp"],
                                                                                                         t["series"], t["maturity_date"], t["accrual_start"], t["rebate_days"]))
        # upfront received 48,492.57 -> Cr 2345 (contract cost); rebate 2,583.33 Cr 2340; initial payment 51,075.90 Dr 1200, due T+1 = 10-22; all dated 10-21
        self.assertEqual((-4849257, 258333, 5107590, "2026-10-22"), (t["upfront_c"], t["rebate_c"], t["initial_payment_c"], t["settle_date"]))
        self.assertEqual([("2345", -4849257), ("2340", -258333), ("1200", 5107590)], lines(rt, f"otc:{cid}:upfront"))
        self.assertEqual("2026-10-21", entry(rt, f"otc:{cid}:upfront")["business_date"])
        s = one(rt, "SELECT * FROM settlements WHERE settle_id=?", (t["settle_id"],))
        self.assertEqual((5107590, "1200", "2026-10-22", "PENDING", "otc", cid), (s["amount_c"], s["gl"], s["due_date"], s["status"], s["ref_type"], s["ref_id"]))
        # IM 60,000.00 (2 %) dated 10-21; mark0 -49,176.13 posted as the day-0 VM on 10-21
        self.assertEqual((6000000, "2026-10-21"), (row["im_c"], entry(rt, f"im:{cid}:open")["business_date"]))
        self.assertEqual((-4917613, -4917613), (b["day0_vm"]["mark_c"], b["day0_vm"]["delta_c"]))
        self.assertEqual("2026-10-21", entry(rt, f"swapvm:{cid}:2026-10-21")["business_date"])
        # C9c: cost basis -48,492.57 (2345); at the trade date's mark -49,176.13 the unrealised is -49,176.13 + 48,492.57 = -683.56 = the cost of
        # dealing, and nothing is realised (the upfront is not a gain)
        self.assertEqual((4849257, 0, -68356), (bal(rt, PB, "2345", inst=cid), bal(rt, PB, "4450", inst=cid), t["mark0_c"] - t["upfront_c"]))
        self.assertEqual(0, rt.accounting.nav(PB).realised_c)
        # today (10-28) the proxy of 10-26 and the curve of 10-26 are newer than the mark of 10-21 NAV carries: the row carries the intraday model
        # mark (C13) and unrealised = that MV - the cost -48,492.57
        pos = rt.accounting.position(PB, cid)
        self.assertEqual(("model, intraday", pos.mv_c + 4849257), (pos.mark.source, pos.unrealised_c))
        # the premium: night 10-21 is covered by the rebate (accrued through the trade date at booking); the first new night 10-22 = 2,666.67 -
        # 2,583.33 = 83.34, business date 10-23 (D7)
        st = one(rt, "SELECT * FROM accrual_state WHERE kind='swappay' AND ref_id=?", (cid,))
        self.assertEqual(("2026-09-21", "2026-10-21", 258333), (st["segment_start"], st["accrued_through"], st["cum_c"]))
        with rt.db.reader() as conn:
            due = rt.lifecycle.job("ACCRUE_DAILY").due(conn, rt.clock, rt.cal, rt.sessions)
        self.assertNotIn(f"swappay:{cid}:2026-10-21", due)
        self.assertIn(f"swappay:{cid}:2026-10-22", due)
        self.assertTrue(accrue(rt, "swappay", cid, "2026-10-22").ok)
        self.assertEqual(([("4453", 8334), ("2340", -8334)], "2026-10-23"), (lines(rt, f"accr:swappay:{cid}:2026-10-22"),
                                                                            entry(rt, f"accr:swappay:{cid}:2026-10-22")["business_date"]))
        # the VM of 10-22 values at the IG OAS of 10-22 (0.89 % -> mid 64.5 bp) on the curve of 10-22: the pure function minus the 10-21 mark
        periods = cds.coupon_periods(rt.cal, date(2026, 10, 21), date(2031, 12, 20))
        dfs, _ = cds._curve(rt, periods, date(2026, 10, 22), on=date(2026, 10, 22))
        v22, _ = cds.value_c("BUY_PROTECTION", 300000000, D("64.5"), D(100), D("0.40"), periods, date(2026, 10, 22), cds.df_function(dfs))
        r = vm(rt, cid, "2026-10-22")
        self.assertEqual(("DONE", v22, v22 + 4917613), (r.body["status"], r.body["mark_c"], r.body["delta_c"]))
        # the initial payment settles on its due date 10-22 (an as-of settlement is dated its due date, C15)
        r = execute(rt, "lifecycle.settle", {"key": t["settle_id"], "settle_id": t["settle_id"]}, f"job:SETTLE_DUE:{t['settle_id']}", actor="lifecycle")
        self.assertTrue(r.ok and r.body["status"] == "DONE", r.body)
        self.assertEqual("2026-10-22", entry(rt, f"settle:{t['settle_id']}")["business_date"])
        assert_integrity(self, rt)

    def test_cdx_hy_upfront_rebate_im_and_cost_of_dealing(self):
        rt = self.rt
        b = self.booked("CDX_HY")
        cid = b["contract_id"]
        t = json.loads(one(rt, "SELECT terms_json FROM otc_contracts WHERE contract_id=?", (cid,))["terms_json"])
        self.assertEqual(("352.0000", "350.0000", -24162283, 1722222, 25884505), (t["deal_spread_bp"], t["mid_spread_bp"], t["upfront_c"], t["rebate_c"], t["initial_payment_c"]))
        self.assertEqual((24000000, -24505351), (one(rt, "SELECT im_c FROM otc_contracts WHERE contract_id=?", (cid,))["im_c"], b["day0_vm"]["mark_c"]))
        # cost of dealing -245,053.51 + 241,622.83 = -3,430.68 (mark0 - the upfront received); the upfront sits on 2345; today's row: MV - cost
        pos = rt.accounting.position(PB, cid)
        self.assertEqual((-343068, 24162283, pos.mv_c + 24162283), (t["mark0_c"] - t["upfront_c"], bal(rt, PB, "2345", inst=cid), pos.unrealised_c))
        assert_integrity(self, rt)

    def test_cdx_with_an_uncalibrated_basis_is_refused_and_writes_nothing(self):
        """F05 / C10: with the IG basis back at 0 the account deals mid 88 + 0.5 = 88.5 bp, not the dealt 64 bp: 409 ASOF_BASIS_NOT_CALIBRATED, naming the
        basis that would deal it (64 - 0.5 - 88 = -24.5 bp); nothing is written."""
        rt = self.rt
        self.assertTrue(execute(rt, "cdx.terms", {"account_id": PB, "index": "IG", "basis_bp": "0"}, "test:cdx-basis-zero", actor="import").ok)
        before = self.counts()
        r = book(rt, "CDX_IG")
        self.assertEqual((409, "ASOF_BASIS_NOT_CALIBRATED", "-24.5000"), (r.http, r.code, r.body["error"]["details"]["basis_needed_bp"]))
        self.assertEqual(before, self.counts())


class AsOfRefusalTests(_Base):
    def test_duplicate_line_key_replay_and_reuse(self):
        rt = self.rt
        b = self.booked("TRS_ORCL")
        n = self.counts()
        # the same request id with the same payload replays the stored result (nothing posted twice)
        r = book(rt, "TRS_ORCL")
        self.assertTrue(r.ok and r.replayed)
        self.assertEqual((b["contract_id"], n), (r.body["contract_id"], self.counts()))
        # the same line key under another request id: 409 DUPLICATE_IMPORT, nothing written
        r = book(rt, "TRS_ORCL", request_id="imp:another-request-0001")
        self.assertEqual((409, "DUPLICATE_IMPORT", b["contract_id"]), (r.http, r.code, r.body["error"]["details"]["contract_id"]))
        # the same request id with a different payload: 409 REQUEST_ID_REUSED
        p = payload("TRS_ORCL")
        p["terms"]["units"] = 38701
        r = execute(rt, "otc.book_as_of", p, LINES["TRS_ORCL"][0], actor="import")
        self.assertEqual((409, "REQUEST_ID_REUSED"), (r.http, r.code))
        self.assertEqual(n, self.counts())

    def test_actor_ui_is_refused(self):
        r = book(self.rt, "TRS_ORCL", actor="ui")
        self.assertEqual((403, "AS_OF_FORBIDDEN"), (r.http, r.code))
        self.assertEqual(0, len(q(self.rt, "SELECT 1 FROM otc_contracts")))

    def test_missing_inputs_are_refused_and_write_nothing(self):
        rt = self.rt
        before = self.counts()
        # no recorded ORCL close of 10-21 (the day-0 valuation input)
        with rt.db.writer() as tx:
            tx.conn.execute("DELETE FROM marks WHERE instrument_id='ORCL' AND mark_date='2026-10-21'")
        r = book(rt, "TRS_ORCL")
        self.assertEqual((409, "ASOF_INPUT_MISSING"), (r.http, r.code))
        # no Treasury curve of 10-21 or of the 3 business days before it (a store holding only later curves)
        rt.curve = CurveStore()
        rt.curve.put(_curve("2026-10-26"), persist=False)
        r = book(rt, "IRS")
        self.assertEqual((409, "ASOF_INPUT_MISSING"), (r.http, r.code))
        # no IG OAS on or before 10-21
        rt.rates = RateStore()
        rt.rates.put("SOFR", [(date.fromisoformat(d), D(v)) for d, v in SOFR], persist=False)
        r = book(rt, "CDX_IG")
        self.assertEqual((409, "ASOF_INPUT_MISSING"), (r.http, r.code))
        self.assertEqual(before, self.counts())

    def test_a_term_the_booking_would_change_is_refused(self):
        """D10: a CDX series that was not on the run on the trade date (S46) cannot be booked as dealt - 409 ASOF_TERMS_MISMATCH; a TRS whose value date is
        not its start (the trade date rolled to a business day) likewise; nothing is written."""
        rt = self.rt
        before = self.counts()
        p = payload("CDX_IG")
        p["terms"]["series"] = 46
        r = execute(rt, "otc.book_as_of", p, "imp:mismatch-series-0001", actor="import")
        self.assertEqual((409, "ASOF_TERMS_MISMATCH"), (r.http, r.code))
        r = book(rt, "TRS_ORCL", request_id="imp:mismatch-value-0001", value="2026-10-22")
        self.assertEqual((409, "ASOF_TERMS_MISMATCH"), (r.http, r.code))
        self.assertEqual(before, self.counts())


class IntradayMarkTests(_Base):
    """C13 (PORTFOLIO-02 / -06): the row carries the live model mark when an input moved after the mark of the last variation margin."""

    def test_trs_intraday_move_and_display_fields(self):
        """P4 acceptance 4: ORCL +1.00 above the 10-26 close moves the short 38,700 TRS MV by -38,700.00 versus the overlay."""
        rt = self.rt
        cid = self.booked("TRS_ORCL")["contract_id"]
        for ds in ("2026-10-22", "2026-10-23", "2026-10-24", "2026-10-25", "2026-10-26"):
            self.assertTrue(vm(rt, cid, ds).ok)
        # the overlay: the mark of the 10-26 VM, -38,700 x (126.06 - 129.13) = +118,809.00; with no newer print the row carries it
        pos = rt.accounting.position(PB, cid)
        self.assertEqual((11880900, "as of the last VM"), (pos.mv_c, pos.mark.source))
        # a print of 127.06 at 10:05 today (the 10-26 close + 1.00): -38,700 x (127.06 - 129.13) = +80,109.00 = the overlay - 38,700.00, "model, intraday"
        rt.marks.set("ORCL", "127.06", "delayed", epoch_of(date(2026, 10, 28), "10:05"))
        pos = rt.accounting.position(PB, cid)
        self.assertEqual((8010900, 8010900 - 11880900, "model, intraday", 8010900), (pos.mv_c, pos.extra["position_fields"]["move_since_vm_c"], pos.mark.source,
                                                                                     pos.unrealised_c))
        self.assertEqual(-3870000, pos.mv_c - 11880900)
        # PORTFOLIO-06: the TRS row shows the reference units, the reset price and the underlying price
        f = pos.extra["position_fields"]
        self.assertEqual((38700, "ORCL reference shares", "129.13", "127.06", 499733100), (f["units"], f["unit_label"], f["entry"], f["current"], f["notional_c"]))
        # P8L-02: what the Portfolio and instrument pages show as its quantity (core.js T.contractQty): the row's qty is the signed notional in
        # whole dollars (-1 x 38,700 x 129.13 = -4,997,331), so the page shows the reference units with the row's sign - "-38,700 ORCL reference
        # shares" - and the notional USD 4,997,331.00 beside them, never -4,997,331 as a quantity
        self.assertEqual((-4997331, 38700, "ORCL reference shares", "USD", 499733100),
                         (pos.qty, pos.extra["units"], pos.extra["unit_label"], pos.extra["qty_unit"], pos.extra["notional_c"]))
        # NAV carries the intraday MV: the PB NAV is 38,700.00 below its value on the overlay
        nav_intraday = rt.accounting.nav(PB).nav_c
        rt.marks.set("ORCL", "127.06", "stale", epoch_of(date(2026, 10, 28), "10:05"))
        pos = rt.accounting.position(PB, cid)
        self.assertEqual((11880900, "as of the last VM", "stale"), (pos.mv_c, pos.mark.source, pos.mark.quality))   # a stale print: the overlay, labelled
        self.assertEqual(-3870000, nav_intraday - rt.accounting.nav(PB).nav_c)
        # the contracts view carries the same figure for the drawer
        v = swaps.contract_view(rt, cid)
        self.assertEqual((11880900, "as of the last VM"), (v["position"]["mv_c"], v["position"]["mark_label"]))

    def test_irs_and_cdx_display_fields(self):
        rt = self.rt
        irs = self.booked("IRS")["contract_id"]
        ig = self.booked("CDX_IG")["contract_id"]
        f = swaps.contract_view(rt, irs)["position"]
        # IRS: notional, the fixed rate vs the par now with the contract curve spread (the 10-26 curve: par 4.790 % - 0.180 % = 4.610 %), DV01 on
        # the 10-26 annuity 2.7690540360 = 7,500,000 x 0.0001 x 2.769054036 = 2,076.79
        self.assertEqual((7500000, "USD notional", "0.04720", "0.04610", 207679), (f["units"], f["unit_label"], f["entry"], f["current"], f["extra"]["dv01_c"]))
        self.assertEqual("-18.0", f["extra"]["curve_spread_bp"])
        g = swaps.contract_view(rt, ig)["position"]
        # CDX: the dealt spread 64 bp and the spread now = the 10-26 proxy 0.90 % = 90 bp + basis -24.5 = 65.5 bp; the coupon; the upfront received
        self.assertEqual(("64.0000 bp", "65.5000 bp", "100", -4849257, "received"), (g["entry"], g["current"], g["extra"]["coupon_bp"], g["extra"]["upfront_c"],
                                                                                    g["extra"]["upfront"]))
        # P8L-02: the position rows carry their quantity in their own units for the pages: the IRS pays fixed on 7,500,000 and the CDX buys
        # protection on 3,000,000 (both the short side, negative), each labelled "USD notional" - "-7,500,000 USD notional", "-3,000,000 USD notional"
        for cid, n in ((irs, 7500000), (ig, 3000000)):
            pos = rt.accounting.position(PB, cid)
            self.assertEqual((-n, n, "USD notional", "USD"), (pos.qty, pos.extra["units"], pos.extra["unit_label"], pos.extra["qty_unit"]))

    def test_vm_payment_may_use_the_same_counterpartys_collateral(self):
        """C21 / P4 task 9: the IRS VM of 10-22 (35,341.95 posted) is a payment to GS, so it may use the cash GS posted as collateral on the TRS the
        same day (138,933.00 received, restricted 138,933.00 - 2,073.36 posted = 136,859.64): paid from the PB's own cash, no Treasury sweep."""
        rt = self.rt
        from finsim.terminal import collateral
        collateral.register(rt.bus, rt)
        collateral.bind(rt)
        rt.collateral = collateral
        trs = self.booked("TRS_ORCL")["contract_id"]
        irs = self.booked("IRS")["contract_id"]
        self.assertEqual(13893300, vm(rt, trs, "2026-10-22").body["delta_c"])
        with rt.db.reader() as conn:
            self.assertEqual(13685964, collateral.restricted_c(conn, PB, "USD", as_of="2026-10-22"))
        r = vm(rt, irs, "2026-10-22")
        self.assertEqual(("DONE", -3534195, "own"), (r.body["status"], r.body["delta_c"], r.body["funding"]["source"]))
        self.assertEqual([], q(rt, "SELECT 1 FROM transfers WHERE linked_id=?", (f"swapvm:{irs}:2026-10-22",)))
        # PB settled cash at the close of 10-22: 138,933.00 received - 35,341.95 paid = 103,591.05
        from finsim.terminal import accounting
        with rt.db.reader() as conn:
            self.assertEqual(10359105, accounting.cash_view_at(conn, PB, "USD", "2026-10-22")["settled_c"])
        assert_integrity(self, rt)


class OfficialCloseTests(unittest.TestCase):
    """POSTCLOSE-01: a TRS VM and reset read the OFFICIAL close - the recorded CLOSE, else a print stamped at or after the session close, never a
    print before it.  On 10-27 the 15:59:59 print was 130.55 and the official close 130.53: a short 38,700 ORCL TRS dealt at 130.55 receives
    -38,700 x (130.53 - 130.55) = +774.00 for 10-27 (the old rule took the 15:59:59 print for the close and posted NO_CHANGE)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-otc-close-")
        self.rt = runtime(self.tmp, at="2026-10-27T16:06:00-04:00")
        self.rt.marks.set("ORCL", "130.55", "delayed", epoch_of(date(2026, 10, 27), "15:59") + 59)
        terms = {"underlying_id": "ORCL", "units": 38700, "receiver": False, "reset_months": 1, "tenor_months": 12}
        r = execute(self.rt, "otc.book", {"account_id": PB, "kind": "TRS", "terms": terms}, "ui:trs-1027-0001")
        assert r.ok, r.body
        self.cid = r.body["contract_id"]
        self.rt.clock.set("2026-10-27T17:10:00-04:00")

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_print_before_the_close_is_not_the_close(self):
        rt, cid = self.rt, self.cid
        self.assertEqual("130.55", one(rt, "SELECT reset_px FROM otc_contracts WHERE contract_id=?", (cid,))["reset_px"])
        r = vm(rt, cid, "2026-10-27")
        self.assertEqual(("SKIPPED", "CLOSE_PENDING"), (r.body["status"], r.body["code"]))
        self.assertEqual([], q(rt, "SELECT 1 FROM otc_vm"))
        # the official close recorded (MARKS_RECORD, 16:00 print 130.53): -38,700 x (130.53 - 130.55) = +774.00 received
        with rt.db.writer() as tx:
            tx.conn.execute("INSERT INTO marks(instrument_id, mark_date, kind, price, currency, quality, source, method, quote_time, fetched_at, note)"
                            " VALUES ('ORCL','2026-10-27','CLOSE','130.53','USD','close','Yahoo','direct',?,?,NULL)",
                            (epoch_of(date(2026, 10, 27), "16:00"), epoch_of(date(2026, 10, 27), "16:20")))
        r = vm(rt, cid, "2026-10-27", ":r1")
        self.assertEqual(("DONE", 77400, 77400), (r.body["status"], r.body["mark_c"], r.body["delta_c"]))
        self.assertEqual([("1010", 77400), ("2330", -77400)], lines(rt, f"swapvm:{cid}:2026-10-27"))
        assert_integrity(self, rt)

    def test_a_print_at_the_close_is_the_close(self):
        rt, cid = self.rt, self.cid
        rt.marks.set("ORCL", "130.53", "delayed", epoch_of(date(2026, 10, 27), "16:00"))
        r = vm(rt, cid, "2026-10-27")
        self.assertEqual(("DONE", 77400), (r.body["status"], r.body["delta_c"]))
        self.assertEqual("the observed print at the session close", r.body["inputs"]["underlying"]["basis"])


class PendingProxyTests(unittest.TestCase):
    """POSTCLOSE-03: a CDX VM waiting for FRED's OAS of its date stays quiet through the evening (no warning from the second attempt of the same
    tick), is not asked for again before 08:00 ET the next business morning, warns once if it is still missing at noon, and says so in words."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-otc-pending-")
        self.rt = runtime(self.tmp, at="2026-10-27T11:00:00-04:00")

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pending_oas_is_quiet_then_warns_once(self):
        rt = self.rt
        r = book(rt, "CDX_IG")
        self.assertTrue(r.ok, r.body)
        cid = r.body["contract_id"]
        for ds in ("2026-10-22", "2026-10-23", "2026-10-24", "2026-10-25", "2026-10-26"):
            self.assertTrue(vm(rt, cid, ds).ok)
        rt.clock.set("2026-10-27T17:10:00-04:00")
        for suffix in ("", ":r1"):                                     # the first attempt and the same tick's second pass: both quiet
            r = vm(rt, cid, "2026-10-27", suffix)
            self.assertEqual(("SKIPPED", "CLOSE_PENDING", None), (r.body["status"], r.body["code"], r.body["attention_id"]))
        self.assertIn("ICE BofA IG OAS for 2026-10-27 not published yet", r.body["message"])
        self.assertNotIn("BAMLC0A0CM", r.body["message"])
        self.assertEqual([], q(rt, "SELECT 1 FROM attention WHERE code='CLOSE_PENDING'"))
        with rt.db.reader() as conn:
            self.assertEqual([], swaps.vm_keys(conn, epoch_of(date(2026, 10, 27), "23:00")))         # not every 5 minutes all night
            self.assertEqual([(cid, "2026-10-27")], swaps.vm_keys(conn, epoch_of(date(2026, 10, 28), "08:30")))
        rt.clock.set("2026-10-28T12:30:00-04:00")
        r = vm(rt, cid, "2026-10-27", ":r2")
        self.assertIsNotNone(r.body["attention_id"])
        self.assertEqual(1, len(q(rt, "SELECT 1 FROM attention WHERE code='CLOSE_PENDING' AND resolved_ts IS NULL")))
        # FRED publishes 0.91 % for 10-27: the VM posts and resolves the warning
        rt.rates.put("BAMLC0A0CM", [(date(2026, 10, 27), D("0.91"))], persist=False)
        r = vm(rt, cid, "2026-10-27", ":r3")
        self.assertEqual("DONE", r.body["status"])
        self.assertEqual([], q(rt, "SELECT 1 FROM attention WHERE code='CLOSE_PENDING' AND resolved_ts IS NULL"))


class WordingTests(_Base):
    def test_trader_wording(self):
        """POSTCLOSE-08: no "unchanged vs the last VM" on a contract that has none, "monthly resets" (not "reset every 1 m"), and VM memos without the
        job code; the contracts view carries the D1 as-of label and the confirmation of an as-of contract."""
        rt = self.rt
        b = self.booked("TRS_ORCL")
        cid = b["contract_id"]
        self.assertIn("equal to the opening reference 0.00 (no variation margin was due before)", b["day0_vm"]["note"])
        self.assertIn("monthly resets", rt.instruments.get(cid).name)
        self.assertNotIn("reset every", rt.instruments.get(cid).name)
        self.assertTrue(vm(rt, cid, "2026-10-22").ok)
        memo = entry(rt, f"swapvm:{cid}:2026-10-22")["memo"]
        self.assertNotIn("SWAP_VM", memo)
        self.assertIn("collateral, not P&L", memo)
        v = swaps.contract_view(rt, cid)
        self.assertEqual(("AS_OF", True, "CONFIRMED", "CONFIRMED - as-of booking at the owner's instruction (no electronic confirmation in the simulator)"),
                         (v["booking"], v["asof"], v["confirm_status"], v["confirm_label"]))
        self.assertTrue(v["source_label"].startswith("As-of booking - simulated OTC trade with Goldman Sachs, value date 2026-10-21, entered 2026-10-28 10:00 ET"
                                                     " by the operator at the owner's instruction (batch IMP-F1-20261027-A)"), v["source_label"])
        for word in ("modeled", "modelled", "hypothetical", "estimated"):
            self.assertNotIn(word, json.dumps(v).lower(), word)


class DatedCurveTests(unittest.TestCase):
    def test_for_date_reads_a_curve_recorded_after_the_store_was_built(self):
        """C12: ``CurveStore.for_date`` reads ``curve_points``, so a UST_PAR curve the importer records after ``build_runtime`` (S2b) is visible without a
        restart; an NYSE business day without a curve values on the last one within 3 business days; none -> None."""
        tmp = tempfile.mkdtemp(prefix="fst-otc-curve-")
        rt = make_runtime(tmp, at=APPLY_AT, books="test")
        try:
            store = CurveStore(rt.db, None, rt.clock, rt.log)
            self.assertIsNone(store.for_date(date(2026, 10, 21), cal=rt.cal))
            from finsim.terminal.quotes import rates as _rates
            rt.bus.register("rates.record", _rates.h_rates_record, light=True)
            r = execute(rt, "rates.record", {"series": "UST_PAR", "as_of": "2026-10-21", "points": dict(CURVES["2026-10-21"]), "source": "test"},
                        "rates:test-curve-1021", actor="import")
            self.assertTrue(r.ok, r.body)
            view = store.for_date(date(2026, 10, 21), cal=rt.cal)
            self.assertEqual(date(2026, 10, 21), view.as_of())
            # the same factors as the live method: par 4.890 % and annuity 2.7644767216 over six semi-annual periods
            dfs, inputs = swaps.dfs_for(types.SimpleNamespace(curve=store, cal=rt.cal), 6, on=date(2026, 10, 21))
            self.assertEqual((D("0.04890"), D("2.7644767216")), swaps.par_annuity(dfs)[:2])
            self.assertEqual("2026-10-21", inputs["curve_as_of"])
            # Fri 10-23 (two business days later, no curve of its own) values on the 10-21 curve; Mon 10-26 is three business days later: still found;
            # Tue 10-27 is four: None
            self.assertEqual([date(2026, 10, 21), date(2026, 10, 21)], [store.for_date(date(2026, 10, d), cal=rt.cal).as_of() for d in (23, 26)])
            self.assertIsNone(store.for_date(date(2026, 10, 27), cal=rt.cal))
        finally:
            rt.close()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
