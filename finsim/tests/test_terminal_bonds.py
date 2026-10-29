"""WP-10 bond tests (TERMINAL_DESIGN.md §13.8, §11.3, §12.1, §19.13; TERMINAL_WORKPLAN.md WP-10).  Every expected number is
worked by hand in a comment; nothing is pasted from the code's output.  No network, no ~/.finsim: FakeClock, FixedMarks (the
computed clean price is pinned by the test), a temp database.  The lifecycle jobs are executed as bus commands with the request
ids the scheduler would use (as the futures tests do); one test wires the ProductJobs the integrator will add to the REGISTRY
onto a real ``LifecycleScheduler`` and ticks it.

The fixture bond ``UST-TEST-2027`` is a 4.500 % semi-annual bond issued 2027-03-01 maturing 2027-09-01 (one 184-day coupon
period: 1 Mar -> 1 Sep 2027), so the §19.13 accrued pin (45 days, 0.5503) and the maturity at 100 sit in one short life.
It is dealt AT its mark (``spec.half_spread`` 0) so the §19.13 numbers stay as written; the Build 2 P07 dealing rule (the
mark +/- the assumed half-spread) is hand-worked on the real 10-year note of the 2026-10-21 snapshot
(``test_treasury_purchase_half_spread_and_one_night``).  Build 2 P07 (2026-10-21): ``build2_bonds`` is ON unless set false,
the monthly model benchmarks are retired (the master is the real Treasury list of products/treasuries.py, tested on the
fiscaldata / FedInvest snapshot in tests/fixtures/terminal/debt).
"""
from __future__ import annotations

import os
import shutil
import tempfile
import types
import unittest
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal

from tests.terminal_helpers import DEBT, T1, TREASURY, US_EXCHANGE, assert_integrity, bal, execute, make_runtime
from finsim.terminal import lifecycle
from finsim.terminal.clock import epoch_of
from finsim.terminal.db import set_setting
from finsim.terminal.instruments import Instrument
from finsim.terminal.lifecycle_eod import ProductJob
from finsim.terminal.marks import MarkProvider
from finsim.terminal.products import bonds, treasuries
from finsim.terminal.quotes.parsers import parse_auctions, parse_fedinvest, parse_mspd
from finsim.terminal.quotes.providers import ParCurve
from finsim.terminal.quotes.rates import CurveStore, RateStore

D = Decimal
BOND = "UST-TEST-2027"
ISSUE, MATURITY = date(2027, 3, 1), date(2027, 9, 1)          # Mon 1 Mar 2027 -> Wed 1 Sep 2027: 184 days
THU_0415 = "2027-04-15T10:00:00-04:00"                          # Thursday; US_BONDS open 08:00-17:00 ET
FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "terminal", "debt")
NOTE10 = "UST-91282CRF0"                                         # US Treasury 4.625 % 15-Aug-2036, the 10-year note of the snapshot


def fixture_bytes(name: str) -> bytes:
    """A debt fixture's bytes, the file closed on return (TEST-09: no ResourceWarning for an unclosed fixture file)."""
    with open(os.path.join(FIX, name), "rb") as f:
        return f.read()


def snapshot_rows(today=date(2026, 10, 21)):
    """The real master of 2026-10-21 (fiscaldata MSPD 2026-09-30 + auctions + FedInvest of the day) and the FedInvest prices."""
    rd, secs = parse_mspd(fixture_bytes("mspd_market_20260930.json"))
    auctions = parse_auctions(fixture_bytes("auctions_20261021.json"))
    fdate, fi = parse_fedinvest(fixture_bytes("fedinvest_20261021.html"))
    return treasuries.treasury_rows(rd, secs, auctions, fi, fdate, today), fdate, fi


def q(rt, sql, params=()):
    with rt.db.reader() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(rt, sql, params=()):
    rows = q(rt, sql, params)
    return rows[0] if rows else None


def curve_2_10() -> ParCurve:
    """A two-point par curve: 2Y 4.00 %, 10Y 4.50 % (linear between, flat beyond)."""
    return ParCurve(date(2027, 2, 26), {D(2): D("4.00"), D(10): D("4.50")}, source="test curve")


def synthetic(spec: dict, expiry=None) -> Instrument:
    return Instrument("X", "BOND", "X", "x", "USD", D("0.01"), D("0.001"), 1000, D(1), "US_BONDS", 1, (), None, None, None, expiry, None, None, None, None, spec, "LIMITED", "ACTIVE")


class BondTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-bond-")
        self.rt = None

    def tearDown(self):
        if self.rt is not None:
            self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- scaffolding ----------------------------------------------------------------------------------------------------
    def start(self, at=THU_0415, flag=True, fixture=True, mark="96.1027"):
        rt = make_runtime(self.tmp, at=at)
        bonds.register(rt.bus, rt)
        rt.bonds = bonds                                     # what the integrator binds: services.bonds
        lifecycle.register(rt.bus, rt)                       # lifecycle.settle for the T+1 cash legs
        with rt.db.writer() as tx:
            if flag:
                set_setting(tx.conn, bonds.SETTING_FLAG, True)
            if fixture:
                bonds.ensure_bond(tx, rt.instruments, BOND, "US Treasury 4.500 % 01-Sep-2027 (test)", "4.5", MATURITY, ISSUE, spec={"half_spread": "0"})
        if fixture and mark is not None:
            rt.marks.set(BOND, mark, "computed")
        self.rt = rt
        return rt

    def trade(self, side, face, account=DEBT, iid=BOND, request_id=None, **extra):
        return execute(self.rt, "bonds.trade", dict(account_id=account, instrument_id=iid, side=side, qty=face, **extra), request_id)

    def accrue(self, ds, request_id=None, account=DEBT, iid=BOND):
        return execute(self.rt, "bonds.accrue", {"account_id": account, "instrument_id": iid, "date": ds}, request_id or f"job:BOND_ACCRUE:{account}:{iid}:{ds}", actor="lifecycle")

    def coupon(self, ds, request_id=None, account=DEBT, iid=BOND):
        return execute(self.rt, "bonds.coupon", {"account_id": account, "instrument_id": iid, "date": ds}, request_id or f"job:BOND_COUPON:{account}:{iid}:{ds}", actor="lifecycle")

    def mature(self, request_id=None, account=DEBT, iid=BOND):
        return execute(self.rt, "bonds.mature", {"account_id": account, "instrument_id": iid}, request_id or f"job:BOND_MATURITY:{account}:{iid}", actor="lifecycle")

    def settle(self, settle_id):
        return execute(self.rt, "lifecycle.settle", {"key": settle_id, "settle_id": settle_id}, f"job:SETTLE_DUE:{settle_id}", actor="lifecycle")

    def lines(self, entry_id):
        return [(l["gl"], l["amount_c"], l["instrument_id"]) for l in q(self.rt, "SELECT gl, amount_c, instrument_id FROM journal_lines WHERE entry_id=? ORDER BY line_no", (entry_id,))]

    def entry_by_idem(self, idem):
        return one(self.rt, "SELECT * FROM journal_entries WHERE idem_key=?", (idem,))

    def n_entries(self):
        return one(self.rt, "SELECT COUNT(*) n FROM journal_entries")["n"]

    def state(self, account=DEBT, iid=BOND):
        return one(self.rt, "SELECT * FROM accrual_state WHERE kind='bond' AND ref_id=?", (f"{account}:{iid}",))

    def keys(self, fn, *args):
        with self.rt.db.reader() as conn:
            return fn(conn, *args)

    def buy_19_13(self):
        """The §19.13 purchase: 1,000,000 face @ 96.1027 clean on 2027-04-15 with 45 days of accrued (0.5503)."""
        r = self.trade("BUY", 1000000)
        self.assertTrue(r.ok, r.body)
        return r

    # ---- pure helpers -------------------------------------------------------------------------------------------------------
    def test_terms_schedule_and_accrued_pins(self):
        t = bonds.bond_terms(synthetic({"coupon_pct": "4.5", "maturity": "2027-09-01", "issue": "2027-03-01"}))
        self.assertEqual((D("4.5"), MATURITY, 2, ISSUE, False), (t.coupon_pct, t.maturity, t.freq, t.issue, t.zero_coupon))
        self.assertEqual(D("0.0225"), t.coupon_per_period)                               # 4.5 / 100 / 2
        # §19.13: after 45 days of the 184-day period 1 Mar -> 1 Sep: 2.25 x 45 / 184 = 0.5503 per 100; on 1,000,000 face 5,503.00
        self.assertEqual((ISSUE, MATURITY), bonds.period_of(t, date(2027, 4, 15)))
        self.assertEqual(D("0.5503"), bonds.accrued_per_100(t, date(2027, 4, 15)))
        self.assertEqual(550300, bonds.accrued_cents(1000000, t, date(2027, 4, 15)))
        self.assertEqual(D("0.5625"), bonds.accrued_per_100(t, date(2027, 4, 16)))       # 2.25 x 46 / 184 = 0.5625 exactly
        self.assertEqual(D("0.0000"), bonds.accrued_per_100(t, ISSUE))                   # a coupon date starts the new period: 0
        self.assertEqual(D("0.0000"), bonds.accrued_per_100(t, MATURITY))                # nothing accrues from maturity on
        self.assertIsNone(bonds.period_of(t, MATURITY))
        self.assertEqual(2250000, bonds.coupon_cents(1000000, t))                         # 1,000,000 x 0.0225 = 22,500.00
        self.assertEqual([MATURITY], bonds.coupon_dates(t, date(2027, 4, 15), MATURITY))
        self.assertEqual([], bonds.coupon_dates(t, date(2027, 4, 15), date(2027, 8, 31)))
        ten = bonds.bond_terms(synthetic({"coupon_pct": "4.5", "maturity": "2037-03-01"}))
        self.assertEqual([date(2027, 9, 1), date(2028, 3, 1)], bonds.coupon_dates(ten, ISSUE, date(2028, 3, 1)))   # semi-annual back from maturity
        # bills: zero coupon, no accrual, no coupon dates
        bill = bonds.bond_terms(synthetic({"coupon_pct": "0.000", "maturity": "2028-03-01", "zero_coupon": True}))
        self.assertTrue(bill.zero_coupon)
        self.assertEqual((D("0.0000"), 0, []), (bonds.accrued_per_100(bill, date(2027, 6, 1)), bonds.coupon_cents(1000000, bill), bonds.coupon_dates(bill, ISSUE, date(2028, 3, 1))))
        # the master's corporate convention: coupon 0.0485 is a fraction -> 4.85 %; years 3.0 -> 2026-09-18 + round(3 x 365.25) = 1096 days = 2029-09-18
        corp = bonds.bond_terms(synthetic({"coupon": 0.0485, "years": 3.0, "rating": "A", "spread_bps": 80.0}))
        self.assertEqual((D("4.85"), date(2029, 9, 18), False), (corp.coupon_pct, corp.maturity, corp.benchmark))
        with self.assertRaises(Exception):
            bonds.bond_terms(synthetic({"rating": "A"}))                                  # no coupon -> BOND_TERMS_MISSING
        # nearest 1/8 %, ties to even: 4.26 -> 4.250; 4.4375 (x8 = 35.5) -> 4.500; 4.3125 (34.5) -> 4.250; 4.0625 (32.5) -> 4.000; 4.1875 (33.5) -> 4.250
        self.assertEqual([D("4.250"), D("4.500"), D("4.250"), D("4.000"), D("4.250"), D("5.000")],
                         [bonds.round_to_eighth(x) for x in ("4.26", "4.4375", "4.3125", "4.0625", "4.1875", "5.00")])
        self.assertEqual("US Treasury 4.500 % 01-Mar-2037 (model benchmark)", bonds.benchmark_name(D("4.5"), date(2037, 3, 1), False))
        self.assertEqual("US Treasury bill 01-Mar-2028 (model benchmark)", bonds.benchmark_name(D(0), date(2028, 3, 1), True))
        self.assertEqual("UST-10Y-202703", bonds.benchmark_id("10Y", ISSUE))
        # nights: from 00:30 ET yesterday is the last night due; before 00:30 the day before
        self.assertEqual(date(2027, 4, 18), bonds.through_night(epoch_of(date(2027, 4, 19), "00:35")))
        self.assertEqual(date(2027, 4, 17), bonds.through_night(epoch_of(date(2027, 4, 19), "00:10")))
        # month-end notes pay on month ends (Treasury convention): 3.500 % maturing Tue 29-Feb-2028 pays 31-Aug and 28/29-Feb; on 21-Oct-2026 the
        # period is 31-Aug-2026 -> 28-Feb-2027 (181 days), 51 days elapsed: 1.75 x 51 / 181 = 0.49309... -> 0.4931 per 100
        eom = bonds.bond_terms(synthetic({"coupon_pct": "3.5", "maturity": "2028-02-29", "eom": True}))
        self.assertEqual((date(2026, 8, 31), date(2027, 2, 28)), bonds.period_of(eom, date(2026, 10, 21)))
        self.assertEqual(D("0.4931"), bonds.accrued_per_100(eom, date(2026, 10, 21)))
        self.assertEqual([date(2027, 2, 28), date(2027, 8, 31), date(2028, 2, 29)], bonds.coupon_dates(eom, date(2026, 10, 21), date(2028, 2, 29)))
        # price / yield: a 4.500 % bond on a coupon date at a 4.500 % yield is exactly par; a 92-day bill at a 3.80 % bond-equivalent yield:
        # 100 / (1 + 0.038 x 92 / 365) = 100 / 1.00957808... = 99.051279 (6 dp), and back to 3.80 % (6 dp: the bisection-free closed form)
        self.assertEqual((D("100.000000"), D("0.0000"), D("100.000000")), bonds.price_at(ten, date(2027, 3, 1), D("4.5")))
        b92 = bonds.bond_terms(synthetic({"coupon_pct": "0", "maturity": "2027-01-21", "zero_coupon": True}))
        self.assertEqual(D("99.051279"), bonds.price_at(b92, date(2026, 10, 21), D("3.80"))[0])
        self.assertEqual(D("3.800000"), bonds.yield_from_clean(b92, date(2026, 10, 21), D("99.051279")).quantize(D("0.0001")).quantize(D("0.000001")))

    # ---- the real Treasury master (fiscaldata + FedInvest snapshot of 2026-10-21) and the BOND mark ------------------------------------
    def test_real_treasury_master_and_the_bond_mark(self):
        rows, fdate, fi = snapshot_rows()
        # every outstanding marketable Treasury on 2026-10-21: 462 = 49 bills + 241 notes + 112 bonds + 52 TIPS + 8 FRNs (the FedInvest list of the day)
        n = Counter(r["spec"]["type"] for r in rows)
        self.assertEqual((462, 49, 241, 112, 52, 8), (len(rows), n["BILL"], n["NOTE"], n["BOND"], n["TIPS"], n["FRN"]))
        note = next(r for r in rows if r["instrument_id"] == NOTE10)
        self.assertEqual(("US Treasury 4.625 % 15-Aug-2036 (91282CRF0)", "91282CRF0", "2036-08-15", "2026-08-17", ["DEBT", "PB"], "FULL", "US_BONDS", 1, 1000),
                         (note["name"], note["symbol"], note["expiry"], note["spec"]["issue"], note["account_kinds"], note["support"], note["session_group"],
                          note["settle_days"], note["lot"]))
        tips = next(r for r in rows if r["spec"]["type"] == "TIPS")
        self.assertEqual("LIMITED", tips["support"])
        self.assertIn("CPI index ratio", tips["spec"]["limited_reason"])
        # listed through the light command the refresher uses; every Build 1 bond row (model benchmarks, universe corporates) nobody holds is DELISTED
        rt = self.start(at="2026-10-21T10:15:00-04:00", fixture=False)
        legacy = [i.instrument_id for i in rt.instruments.all() if i.kind == "BOND" and i.status == "ACTIVE" and treasuries.is_legacy(i)]
        self.assertTrue(legacy)
        r = execute(rt, "debt.sync", {"rows": rows, "delist": legacy, "label": "test"}, "debt:sync:test", actor="worker")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((462, 0, len(legacy)), (r.body["new"], r.body["updated"], len(r.body["delisted"])))
        rt.instruments.load()
        self.assertEqual("DELISTED", rt.instruments.get(legacy[0]).status)
        inst = rt.instruments.get(NOTE10)
        self.assertTrue(rt.instruments.tradeable_in(inst, "DEBT"))
        # the mark: the FedInvest price of the same day, the mid of BUY 96.359375 and SELL 96.343750 = 96.3515625 -> 96.351562 (6 dp, ties to even)
        store = treasuries.DebtStore()
        store.put_fedinvest(fdate, fi, 0, persist=False)
        rt.debt_store = store
        v = bonds.bond_mark(rt, inst)
        self.assertEqual((D("96.351562"), "close", "fedinvest", "TreasuryDirect FedInvest 2026-10-21"), (v.mark, v.quality, v.method, v.source))
        # no FedInvest price: the par-curve model; a 4.500 % note priced on a coupon date at a flat 4.50 % par curve is par (computed)
        rt.curve = CurveStore()
        rt.curve.put(ParCurve(date(2027, 2, 26), {D(2): D("4.50"), D(10): D("4.50")}, source="test curve"), persist=False)
        rt.clock.set("2027-03-01T10:00:00-05:00")
        model = bonds.bond_terms(synthetic({"coupon_pct": "4.5", "maturity": "2037-03-01", "type": "NOTE", "cusip": "000000000"}))
        v = bonds.bond_mark(rt, synthetic({"coupon_pct": "4.5", "maturity": "2037-03-01", "type": "NOTE", "cusip": "000000000"}))
        self.assertEqual((D("100"), "computed", "curve"), (v.mark, v.quality, v.method))
        self.assertEqual(D("100.000000"), bonds.price_at(model, date(2027, 3, 1), D("4.5"))[0])

    # ---- refusals (the flag, the account, the lot, the session, the mark) ------------------------------------------------------------
    def test_flag_off_is_unsupported_and_the_other_refusals(self):
        rt = self.start(flag=False)
        with rt.db.writer() as tx:
            set_setting(tx.conn, bonds.SETTING_FLAG, False)                                         # ON by default (P07): only an explicit false stops it
        n0 = self.n_entries()
        r = self.trade("BUY", 1000000)
        self.assertEqual((False, 400, "UNSUPPORTED", bonds.UNSUPPORTED_TEXT), (r.ok, r.http, r.code, r.message))
        with rt.db.writer() as tx:
            set_setting(tx.conn, bonds.SETTING_FLAG, True)
        r = self.trade("BUY", 1000000, account=US_EXCHANGE)
        self.assertEqual(("NOT_TRADEABLE_HERE", 400), (r.code, r.http))
        r = self.trade("BUY", 1500)
        self.assertEqual(("QTY_INVALID", 400), (r.code, r.http))                                  # lot 1,000
        r = self.trade("SELL_SHORT", 1000)
        self.assertEqual(("NOT_IN_THIS_BUILD", 400), (r.code, r.http))
        r = self.trade("SELL", 1000)
        self.assertEqual(("OVERSELL", 400), (r.code, r.http))                                     # nothing held
        r = self.trade("BUY", 1000, iid="AAPL")
        self.assertEqual(("NOT_A_BOND", 400), (r.code, r.http))
        rt.marks.set(BOND, "96.1027", "stale")
        r = self.trade("BUY", 1000)
        self.assertEqual(("QUOTE_STALE", 409), (r.code, r.http))                                  # a computed mark with stale inputs never executes
        rt.marks.unset(BOND)
        r = self.trade("BUY", 1000)
        self.assertEqual(("MARK_UNAVAILABLE", 409), (r.code, r.http))                             # no curve -> no price -> no fill
        rt.marks.set(BOND, "96.1027", "computed")
        rt.clock.set("2027-04-17T10:00:00-04:00")                                                  # Saturday
        r = self.trade("BUY", 1000)
        self.assertEqual(("SESSION_CLOSED", 400), (r.code, r.http))
        rt.clock.set("2027-04-15T07:30:00-04:00")                                                  # before the 08:00 open
        r = self.trade("BUY", 1000)
        self.assertEqual(("SESSION_CLOSED", 400), (r.code, r.http))
        self.assertTrue(r.message.startswith("bond market closed - reopens 08:00 ET"), r.message)
        rt.clock.set("2027-09-01T10:00:00-04:00")                                                  # the maturity date: no trades
        r = self.trade("BUY", 1000)
        self.assertEqual(("INSTRUMENT_EXPIRED", 400), (r.code, r.http))
        rt.clock.set(THU_0415)
        with rt.db.writer() as tx:                                                                 # a TIPS row: LIMITED, the reason in the refusal
            bonds.ensure_bond(tx, rt.instruments, "UST-TIPSTEST0", "US Treasury TIPS 1.875 % 15-Jul-2035 (test)", "1.875", date(2035, 7, 15), spec={"type": "TIPS",
                              "limited_reason": treasuries.LIMITED_REASON["TIPS"]}, support="LIMITED")
        rt.marks.set("UST-TIPSTEST0", "99.5", "close")
        r = self.trade("BUY", 1000, iid="UST-TIPSTEST0")
        self.assertEqual(("LIMITED", 400), (r.code, r.http))
        self.assertIn("CPI index ratio", r.message)
        self.assertEqual(n0, self.n_entries())                                                     # every refusal left nothing behind
        self.assertEqual([], q(rt, "SELECT fill_id FROM fills"))

    # ---- OTC-R1-01 (1.0.4 fixer, 2026-10-27): the preview refuses what the booking's funding cascade would refuse ----------------------
    def test_preview_refuses_an_unfundable_buy_with_the_largest_face_cash_covers(self):
        rt = self.start()                                   # Treasury 10,000,000.00, F1:DEBT (test book: DEBT) 0; DEBT has no PB financing
        self.assertEqual((1000000000, 0), (bal(rt, TREASURY, "1010"), bal(rt, DEBT, "1010")))
        n0 = self.n_entries()

        def pv(face):
            return bonds.preview(rt, {"account_id": DEBT, "instrument_id": BOND, "side": "BUY", "qty": face})
        # 11,000,000 face @ 96.1027 + accrued 0.5503: clean 10,571,297.00 + accrued 60,533.00 + fee 0.02 x 110,000 = 2,200.00 = 10,634,030.00
        # > own 0.00 + Treasury 10,000,000.00 (+ PB 0) -> refused at the preview, as the booking would refuse it
        p = pv(11000000)
        self.assertEqual((False, ["INSUFFICIENT_FUNDS"]), (p["ok"], [b["code"] for b in p["blockers"]]))
        f = p["funding"]
        self.assertEqual((1063403000, 0, 1000000000, 0, 1000000000, True), (f["need_c"], f["own_available_c"], f["treasury_available_c"], f["pb_headroom_c"], f["covered_c"], f["refused"]))
        # the largest lot that fits 10,000,000.00: 10,346,000 face (10,000,000 / 0.966530 = 10,346,290 -> the lot below) costs
        # 9,942,785.34 + 56,934.04 + 2,069.20 = 10,001,788.58 (no); 10,345,000: 9,941,824.32 (9,941,824.315 half-even) + 56,928.54
        # (56,928.535) + 2,069.00 = 10,000,821.86 (no); 10,344,000: 9,940,863.29 (.288) + 56,923.03 (.032) + 2,068.80 = 9,999,855.12 (yes)
        self.assertEqual(10344000, f["max_face"])
        self.assertIn("buy at most 10,344,000 face now", p["blockers"][0]["message"])
        self.assertIn("own available 0.00 + Treasury available 10,000,000.00 = 10,000,000.00 of the 10,634,030.00 needed", p["blockers"][0]["message"])
        self.assertEqual((False, ["INSUFFICIENT_FUNDS"]), (pv(10345000)["ok"], [b["code"] for b in pv(10345000)["blockers"]]))
        p = pv(10344000)
        self.assertEqual((True, [], 999985512, 999985512), (p["ok"], p["blockers"], p["funding"]["need_c"], p["funding"]["sweep_c"]))
        self.assertEqual(n0, self.n_entries())                                                     # previews write nothing
        # the booking agrees: 11,000,000 is refused (409 INSUFFICIENT_FUNDS, nothing moved); 10,344,000 books with one sweep of 9,999,855.12
        r = self.trade("BUY", 11000000)
        self.assertEqual((False, 409, "INSUFFICIENT_FUNDS"), (r.ok, r.http, r.code))
        self.assertEqual(n0, self.n_entries())
        r = self.trade("BUY", 10344000)
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("sweep", 999985512, -999985512), (r.body["funding"]["source"], r.body["funding"]["sweep_c"], r.body["economics"]["net_cash_c"]))
        self.assertEqual(1000000000 - 999985512, bal(rt, TREASURY, "1010"))                        # 10,000,000.00 - 9,999,855.12 = 144.88 left
        assert_integrity(self, rt, "full")

    # ---- §19.13: the buy, nightly accruals, the coupon, maturity at 100 -----------------------------------------------------------------
    def test_19_13_buy_accrue_coupon_maturity(self):
        rt = self.start()
        self.assertEqual((1000000000, 0), (bal(rt, TREASURY, "1010"), bal(rt, DEBT, "1010")))
        r = self.buy_19_13()
        b = r.body
        fill = b["fill"]
        # clean 1,000,000 x 96.1027 / 100 = 961,027.00; accrued 1,000,000 x 0.5503 / 100 = 5,503.00; fee 0.02 x 10,000 = 200.00 (>= 5.00); total 966,730.00
        self.assertEqual((96102700, 550300, 20000, "96.1027", "model", "2027-04-15", "2027-04-16", 1000000),
                         (fill["gross_c"], fill["accrued_c"], fill["fee_c"], fill["price"], fill["price_basis"], fill["trade_date"], fill["settle_date"], fill["qty"]))
        self.assertEqual(("0.5503", "96.653", -96673000), (b["economics"]["accrued"], b["economics"]["dirty"], b["economics"]["net_cash_c"]))   # 96.1027 + 0.5503
        # Dr 1110 961,027.00 / Dr 1220 5,503.00 / Dr 5000 200.00 / Cr 2100 966,730.00
        self.assertEqual([("1110", 96102700, BOND), ("1220", 550300, BOND), ("5000", 20000, BOND), ("2100", -96673000, None)], self.lines(fill["entry_id"]))
        e = self.entry_by_idem(f"fill:{fill['fill_id']}")
        self.assertEqual(("FILL", fill["entry_id"]), (e["kind"], e["entry_id"]))
        # funding: own 0 -> one Treasury sweep-in of exactly the shortfall 966,730.00, linked to the fill (L19)
        self.assertEqual(("sweep", 96673000, 96673000, 0), (b["funding"]["source"], b["funding"]["sweep_c"], b["funding"]["shortfall_c"], b["funding"]["own_c"]))
        sw = one(rt, "SELECT * FROM transfers WHERE kind='SWEEP_IN'")
        self.assertEqual((96673000, "fill", fill["fill_id"], DEBT), (sw["amount_c"], sw["linked_type"], sw["linked_id"], sw["to_account"]))
        self.assertEqual((96673000, 96673000, 96102700, 550300, 20000, 903327000),
                         (bal(rt, DEBT, "1010"), bal(rt, DEBT, "2100"), bal(rt, DEBT, "1110", inst=BOND), bal(rt, DEBT, "1220", inst=BOND), bal(rt, DEBT, "5000", inst=BOND),
                          bal(rt, TREASURY, "1010")))
        lot = one(rt, "SELECT * FROM lots WHERE account_id=? AND instrument_id=?", (DEBT, BOND))
        self.assertEqual(("LONG", 1000000, 1000000, "96.1027", 96102700), (lot["side"], lot["qty_open"], lot["qty_remaining"], lot["unit_cost"], lot["cost_c"]))
        s = one(rt, "SELECT * FROM settlements WHERE fill_id=?", (fill["fill_id"],))
        self.assertEqual((-96673000, "2100", "2027-04-16", "PENDING"), (s["amount_c"], s["gl"], s["due_date"], s["status"]))
        f = one(rt, "SELECT * FROM fills WHERE fill_id=?", (fill["fill_id"],))
        self.assertEqual(("BUY", 1000000, "96.1027", 96102700, 20000, "ORDER", "model", "2027-04-16", fill["idem_key"]),
                         (f["side"], f["qty"], f["price"], f["gross_c"], f["fee_c"], f["origin"], f["price_basis"], f["settle_date"], f["idem_key"]))
        self.assertTrue(f["idem_key"].startswith("bonds:ui:"))                                     # bonds:{request_id}: one fill per request
        st = self.state()
        # the accrual state: face 1,000,000, 0.0225 per period of 184 days from 2 Mar, accrued through the trade date with the purchased 5,503.00 carried
        self.assertEqual((1000000, "0.0225", 184, "2027-03-02", "2027-04-15", 550300), (st["base_c"], st["rate"], st["day_basis"], st["segment_start"], st["accrued_through"], st["cum_c"]))
        pos = rt.accounting.position(DEBT, BOND)
        self.assertEqual((1000000, 96102700, 96102700, 0), (pos.qty, pos.cost_c, pos.mv_c, pos.unrealised_c))      # mark = cost
        # NAV = 10,000,000 - 200 fee (the position at its model mark, the accrued in 1220): 9,999,800.00
        self.assertEqual(999980000, rt.accounting.nav("T1:ALL").nav_c)
        assert_integrity(self, rt, "full")
        # no nights are due yet (the night of 15 Apr is due from 00:30 of the 16th and was carried by the purchase)
        self.assertEqual([], self.keys(bonds.accrue_keys, rt.clock.epoch(), rt.instruments))
        # T+1: the payable settles from the swept cash
        rt.clock.set("2027-04-16T09:00:00-04:00")
        r = self.settle(s["settle_id"])
        self.assertTrue(r.ok, r.body)
        self.assertEqual((0, 0), (bal(rt, DEBT, "1010"), bal(rt, DEBT, "2100")))
        # night of 16 Apr (due from 00:30 of the 17th): 46 days -> 2.25 x 46/184 = 0.5625 -> 5,625.00 cumulative; posted 5,625.00 - 5,503.00 = 122.00
        rt.clock.set("2027-04-17T00:35:00-04:00")
        self.assertEqual([(DEBT, BOND, "2027-04-16")], self.keys(bonds.accrue_keys, rt.clock.epoch(), rt.instruments))
        r = self.accrue("2027-04-16")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("DONE", 12200, 1000000, "0.5625", 562500, 550300), (r.body["status"], r.body["amount_c"], r.body["face"], r.body["accrued_per_100"], r.body["cum_c"], r.body["cum_before_c"]))
        e = self.entry_by_idem(f"accr:bond:{DEBT}:{BOND}:2027-04-16")
        self.assertEqual([("1220", 12200, BOND), ("4300", -12200, BOND)], self.lines(e["entry_id"]))
        self.assertEqual(("ACCRUAL", "2027-04-16"), (e["kind"], e["business_date"]))
        self.assertEqual(("2027-04-16", 562500), (self.state()["accrued_through"], self.state()["cum_c"]))
        # replay: the same request id answers the stored result; a new id finds the night already accrued and posts nothing
        self.assertTrue(self.accrue("2027-04-16").replayed)
        n = self.n_entries()
        r2 = self.accrue("2027-04-16", request_id=f"job:BOND_ACCRUE:{DEBT}:{BOND}:2027-04-16:r1")
        self.assertEqual(("DONE", "ALREADY_ACCRUED", n), (r2.body["status"], r2.body["code"], self.n_entries()))
        # 17 Apr: 47 days -> 2.25 x 47/184 = 0.57472... -> 0.5747 -> 5,747.00: +122.00; 18 Apr: 48 days -> 0.58695... -> 0.5870 -> 5,870.00: +123.00
        rt.clock.set("2027-04-18T00:35:00-04:00")
        self.assertEqual(12200, self.accrue("2027-04-17").body["amount_c"])
        rt.clock.set("2027-04-19T00:35:00-04:00")
        self.assertEqual(12300, self.accrue("2027-04-18").body["amount_c"])
        self.assertEqual(587000, bal(rt, DEBT, "1220", inst=BOND))
        # a night out of order is never lumped: 20 Apr before 19 Apr -> SKIPPED ACCRUAL_GAP (one WARN attention), then in order 19 Apr +122.00 (0.5992), 20 Apr +122.00 (0.6114)
        rt.clock.set("2027-04-21T00:35:00-04:00")
        r = self.accrue("2027-04-20")
        self.assertEqual(("SKIPPED", "ACCRUAL_GAP"), (r.body["status"], r.body["code"]))
        self.assertEqual(1, len(q(rt, "SELECT id FROM attention WHERE code='ACCRUAL_GAP' AND resolved_ts IS NULL")))
        self.assertEqual([(DEBT, BOND, "2027-04-19"), (DEBT, BOND, "2027-04-20")], self.keys(bonds.accrue_keys, rt.clock.epoch(), rt.instruments))
        self.assertEqual(12200, self.accrue("2027-04-19").body["amount_c"])
        self.assertEqual(12200, self.accrue("2027-04-20", request_id=f"job:BOND_ACCRUE:{DEBT}:{BOND}:2027-04-20:r1").body["amount_c"])
        self.assertEqual([], q(rt, "SELECT id FROM attention WHERE code='ACCRUAL_GAP' AND resolved_ts IS NULL"))
        # a night not yet due (today's) is refused, never posted early
        r = self.accrue("2027-04-21")
        self.assertEqual(("SKIPPED", "NIGHT_NOT_YET"), (r.body["status"], r.body["code"]))
        # the rest of the period: 133 nights 21 Apr .. 31 Aug in date order (10 + 31 + 30 + 31 + 31)
        rt.clock.set("2027-09-01T00:35:00-04:00")
        due = self.keys(bonds.accrue_keys, rt.clock.epoch(), rt.instruments)
        self.assertEqual(133, len(due))
        self.assertEqual(((DEBT, BOND, "2027-04-21"), (DEBT, BOND, "2027-08-31")), (due[0], due[-1]))
        for _, _, ds in due:
            r = self.accrue(ds, request_id=f"job:BOND_ACCRUE:{DEBT}:{BOND}:{ds}:r1" if ds == "2027-04-21" else None)
            self.assertTrue(r.ok and r.body["status"] == "DONE", (ds, r.body))
        # cumulative after 183 days: 2.25 x 183/184 = 2.23777... -> 2.2378 -> 22,378.00; the nights posted 22,378.00 - 5,503.00 = 16,875.00 with no drift
        self.assertEqual(("2027-08-31", 2237800), (self.state()["accrued_through"], self.state()["cum_c"]))
        self.assertEqual(2237800, bal(rt, DEBT, "1220", inst=BOND))
        self.assertEqual(1687500, bal(rt, DEBT, "4300", inst=BOND))
        self.assertEqual(138, len(q(rt, "SELECT key FROM jobs_done WHERE job='BOND_ACCRUE' AND status='DONE'")))
        self.assertEqual([], self.keys(bonds.accrue_keys, rt.clock.epoch(), rt.instruments))       # nothing after 31 Aug: the bond matures 1 Sep
        # the coupon of 1 Sep is due from 00:40 ET of 1 Sep (a Wednesday: no roll); the redemption from 00:45
        self.assertEqual([], self.keys(bonds.coupon_keys, rt.clock.epoch(), rt.cal, rt.instruments))
        rt.clock.set("2027-09-01T00:45:00-04:00")
        self.assertEqual([(DEBT, BOND, "2027-09-01")], self.keys(bonds.coupon_keys, rt.clock.epoch(), rt.cal, rt.instruments))
        self.assertEqual([(DEBT, BOND)], self.keys(bonds.maturity_keys, rt.clock.epoch(), rt.cal, rt.instruments))
        r = self.mature()
        self.assertEqual(("SKIPPED", "MATURITY_PENDING_COUPON"), (r.body["status"], r.body["code"]))    # the final coupon comes first
        # coupon 1,000,000 x 4.5 % / 2 = 22,500.00 = the carried 22,378.00 + 122.00 earned since the last night
        r = self.coupon("2027-09-01")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("DONE", 2250000, 2237800, 12200, "2027-09-01", 1000000), (r.body["status"], r.body["amount_c"], r.body["carried_c"], r.body["income_c"], r.body["pay_date"], r.body["face"]))
        e = self.entry_by_idem(f"coupon:{DEBT}:{BOND}:2027-09-01")
        self.assertEqual([("1010", 2250000, None), ("1220", -2237800, BOND), ("4300", -12200, BOND)], self.lines(e["entry_id"]))
        self.assertEqual((2250000, 0, 1699700), (bal(rt, DEBT, "1010"), bal(rt, DEBT, "1220", inst=BOND), bal(rt, DEBT, "4300", inst=BOND)))
        self.assertEqual((0, "2027-09-01"), (self.state()["cum_c"], self.state()["accrued_through"]))
        self.assertEqual([], self.keys(bonds.coupon_keys, rt.clock.epoch(), rt.cal, rt.instruments))
        # maturity: a MATURITY fill at 100 - Dr 1010 1,000,000.00 / Cr 1110 961,027.00 / Cr 4000 38,973.00 (= 100 - 96.1027 per 100), no fee, cash the same day
        r = self.mature(request_id=f"job:BOND_MATURITY:{DEBT}:{BOND}:r1")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("DONE", 100000000, 96102700, 3897300, 1000000, "EXPIRED"), (r.body["status"], r.body["amount_c"], r.body["cost_relieved_c"], r.body["realised_c"], r.body["face"], r.body["instrument_status"]))
        self.assertEqual([("1010", 100000000, None), ("1110", -96102700, BOND), ("4000", -3897300, BOND)], self.lines(r.body["entry_id"]))
        f = one(rt, "SELECT * FROM fills WHERE fill_id=?", (r.body["fill_id"],))
        self.assertEqual(("SELL", 1000000, "100", 100000000, 0, "MATURITY", "par", None, "2027-09-01", f"maturity:{DEBT}:{BOND}"),
                         (f["side"], f["qty"], f["price"], f["gross_c"], f["fee_c"], f["origin"], f["price_basis"], f["settle_date"], f["trade_date"], f["idem_key"]))
        self.assertEqual("EXPIRED", rt.instruments.get(BOND).status)
        self.assertIsNone(self.state())
        self.assertEqual([], q(rt, "SELECT qty FROM v_positions WHERE account_id=? AND instrument_id=?", (DEBT, BOND)))
        self.assertEqual([], q(rt, "SELECT id FROM attention WHERE code='MATURITY_PENDING_COUPON' AND resolved_ts IS NULL"))
        # cash 22,500.00 + 1,000,000.00 = 1,022,500.00; realised 38,973.00; interest income 16,997.00 = 22,500.00 - 5,503.00; fees 200.00
        self.assertEqual((102250000, 0, 3897300, 1699700, 20000), (bal(rt, DEBT, "1010"), bal(rt, DEBT, "1110", inst=BOND), bal(rt, DEBT, "4000", inst=BOND),
                                                                   bal(rt, DEBT, "4300", inst=BOND), bal(rt, DEBT, "5000", inst=BOND)))
        # NAV = 10,000,000 + 38,973.00 + 16,997.00 - 200.00 = 10,055,770.00
        self.assertEqual(1005577000, rt.accounting.nav("T1:ALL").nav_c)
        self.assertEqual([], self.keys(bonds.maturity_keys, rt.clock.epoch(), rt.cal, rt.instruments))
        self.assertTrue(self.mature(request_id=f"job:BOND_MATURITY:{DEBT}:{BOND}:r2").body["already"])
        assert_integrity(self, rt, "full")

    # ---- a partial sale relieves the carried accrued pro rata and realises clean - cost ----------------------------------------------------------
    def test_partial_sale_accrued_pro_rata_and_fifo(self):
        rt = self.start()
        r = self.buy_19_13()
        settle_id = r.body["settlement"]["settle_id"]
        rt.clock.set("2027-04-16T09:00:00-04:00")
        self.assertTrue(self.settle(settle_id).ok)
        for ds, at in (("2027-04-16", "2027-04-17T00:35:00-04:00"), ("2027-04-17", "2027-04-18T00:35:00-04:00"), ("2027-04-18", "2027-04-19T00:35:00-04:00")):
            rt.clock.set(at)
            self.assertTrue(self.accrue(ds).ok)
        self.assertEqual(587000, self.state()["cum_c"])                                           # 5,870.00 carried after the night of 18 Apr
        rt.clock.set("2027-04-19T10:00:00-04:00")                                                  # Monday, session open
        r = self.trade("SELL", 1100000)                                                            # 1,100,000 > the 1,000,000 held
        self.assertEqual(("OVERSELL", 400), (r.code, r.http))
        rt.marks.set(BOND, "96.50", "computed")
        rt.repo = types.SimpleNamespace(pledged_face=lambda conn, account, iid: 700000)            # C27: 700,000 of the 1,000,000 pledged in an open repo
        r = self.trade("SELL", 400000)
        self.assertEqual(("PLEDGED_IN_REPO", 409), (r.code, r.http))                              # only 300,000 is free: release the repo first
        # FINSIM1 P6-R1 (1.0.6, on purpose): the pledged face may be an open repo or a CSA pledge (repo.pledged_face counts both), so
        # the refusal names both ways out
        self.assertIn("700,000 face is pledged (an open repo or a CSA pledge to the prime broker) and only 300,000 is free", r.message)
        self.assertIn("close the repo or release the pledge (Cash & Collateral) first", r.message)
        rt.repo = None
        n0 = self.n_entries()
        r = self.trade("SELL", 400000)
        self.assertTrue(r.ok, r.body)
        b = r.body
        # accrued on 19 Apr: 49 days -> 2.25 x 49/184 = 0.59918... -> 0.5992 per 100 -> on 400,000 face 2,396.80 received from the buyer;
        # the carried accrued relieved pro rata: 5,870.00 x 400,000 / 1,000,000 = 2,348.00; the rest 48.80 is interest earned since the last night;
        # clean 400,000 x 96.50 / 100 = 386,000.00; fee 0.02 x 4,000 = 80.00; FIFO cost 961,027.00 x 400,000 / 1,000,000 = 384,410.80;
        # realised 386,000.00 - 384,410.80 = 1,589.20; receivable 386,000.00 + 2,396.80 - 80.00 = 388,316.80
        self.assertEqual((38600000, 239680, 8000, 38441080, 158920, 234800, 4880, 38831680),
                         (b["economics"]["clean_c"], b["economics"]["accrued_c"], b["economics"]["fee_c"], b["economics"]["cost_relieved_c"], b["economics"]["realised_c"],
                          b["economics"]["accrued_relieved_c"], b["economics"]["accrued_income_c"], b["economics"]["net_cash_c"]))
        self.assertEqual([("1200", 38831680, None), ("5000", 8000, BOND), ("1110", -38441080, BOND), ("1220", -234800, BOND), ("4300", -4880, BOND), ("4000", -158920, BOND)],
                         self.lines(b["fill"]["entry_id"]))
        self.assertEqual(n0 + 1, self.n_entries())
        s = one(rt, "SELECT * FROM settlements WHERE fill_id=?", (b["fill"]["fill_id"],))
        self.assertEqual((38831680, "1200", "2027-04-20", "PENDING"), (s["amount_c"], s["gl"], s["due_date"], s["status"]))
        lot = one(rt, "SELECT * FROM lots WHERE account_id=? AND instrument_id=?", (DEBT, BOND))
        self.assertEqual((600000, 96102700 - 38441080), (lot["qty_remaining"], lot["cost_c"]))        # 57,661,620 c = 576,616.20
        st = self.state()
        self.assertEqual((600000, 352200, "2027-04-18"), (st["base_c"], st["cum_c"], st["accrued_through"]))     # 5,870.00 - 2,348.00 = 3,522.00 carried on 600,000
        self.assertEqual((600000, 352200), (rt.accounting.position(DEBT, BOND).qty, bal(rt, DEBT, "1220", inst=BOND)))
        # the night of 19 Apr on the remaining 600,000: 0.5992 -> 3,595.20 cumulative; posted 3,595.20 - 3,522.00 = 73.20
        rt.clock.set("2027-04-20T00:35:00-04:00")
        r = self.accrue("2027-04-19")
        self.assertEqual((7320, 600000, 359520), (r.body["amount_c"], r.body["face"], r.body["cum_c"]))
        self.assertTrue(self.settle(s["settle_id"]).ok)
        self.assertEqual((38831680, 0), (bal(rt, DEBT, "1010"), bal(rt, DEBT, "1200")))
        # NAV at the 96.50 mark: realised 1,589.20 + unrealised (600,000 x 96.50 / 100 = 579,000.00 - 576,616.20 = 2,383.80) + income (122 + 122 + 123 + 48.80 + 73.20 = 489.00)
        # - fees (200 + 80 = 280.00) = +4,182.00 -> 10,004,182.00
        self.assertEqual(1000418200, rt.accounting.nav("T1:ALL").nav_c)
        assert_integrity(self, rt, "full")

    # ---- P07: a real Treasury bought at the mark + the disclosed half-spread, then one night of accrual ----------------------------------------------------
    def test_treasury_purchase_half_spread_and_one_night(self):
        rt = self.start(at="2026-10-21T10:15:00-04:00", fixture=False)                       # Wednesday 10:15 ET: US_BONDS open 08:00-17:00
        rows, _fdate, _fi = snapshot_rows()
        r = execute(rt, "debt.sync", {"rows": [x for x in rows if x["instrument_id"] == NOTE10], "label": "test"}, "debt:sync:one", actor="worker")
        self.assertEqual(1, r.body["new"])
        rt.instruments.load()
        rt.marks.set(NOTE10, "96.351562", "close")                                            # the FedInvest mid of the day (see the master test)
        r = self.trade("BUY", 100000, iid=NOTE10)
        self.assertTrue(r.ok, r.body)
        b = r.body
        # 4.625 % 15-Aug-2036 has 9.8 years left: the assumed half-spread of a note over 7 years is 1/32 = 0.03125 -> dealt at 96.351562 + 0.03125 = 96.382812
        # clean 100,000 x 96.382812 / 100 = 96,382.812 -> 96,382.81
        # accrued: period 15-Aug-2026 -> 15-Feb-2027 = 184 days, 67 days elapsed on 21-Oct: 2.3125 x 67 / 184 = 0.842051... -> 0.8421 per 100 -> 842.10
        # fee 0.02 x 100,000 / 100 = 20.00 (above the 5.00 minimum); total 96,382.81 + 842.10 + 20.00 = 97,244.91, settling T+1 = Thu 22-Oct-2026
        self.assertEqual(("96.382812", "96.351562", "0.03125", 9638281, "0.8421", 84210, 2000, -9724491, "2026-10-21", "2026-10-22"),
                         (b["fill"]["price"], b["fill"]["mark"], b["fill"]["half_spread"], b["economics"]["clean_c"], b["economics"]["accrued"],
                          b["economics"]["accrued_c"], b["economics"]["fee_c"], b["economics"]["net_cash_c"], b["fill"]["trade_date"], b["fill"]["settle_date"]))
        # Dr 1110 96,382.81 / Dr 1220 842.10 / Dr 5000 20.00 / Cr 2100 97,244.91; the shortfall 97,244.91 sweeps from Treasury
        self.assertEqual([("1110", 9638281, NOTE10), ("1220", 84210, NOTE10), ("5000", 2000, NOTE10), ("2100", -9724491, None)], self.lines(b["fill"]["entry_id"]))
        self.assertEqual(("sweep", 9724491), (b["funding"]["source"], b["funding"]["sweep_c"]))
        # the purchase carries the accrued of its trade date (night of 21-Oct included); the next night is 22-Oct, due from 00:30 ET on the 23rd:
        # 68 days -> 2.3125 x 68 / 184 = 0.854619... -> 0.8546 -> 854.60 cumulative; posted 854.60 - 842.10 = 12.50
        rt.clock.set("2026-10-22T00:35:00-04:00")
        self.assertEqual([], self.keys(bonds.accrue_keys, rt.clock.epoch(), rt.instruments))
        rt.clock.set("2026-10-23T00:35:00-04:00")
        self.assertEqual([(DEBT, NOTE10, "2026-10-22")], self.keys(bonds.accrue_keys, rt.clock.epoch(), rt.instruments))
        a = self.accrue("2026-10-22", iid=NOTE10)
        self.assertTrue(a.ok, a.body)
        self.assertEqual(("DONE", 1250, "0.8546", 85460, 84210), (a.body["status"], a.body["amount_c"], a.body["accrued_per_100"], a.body["cum_c"], a.body["cum_before_c"]))
        self.assertEqual(85460, bal(rt, DEBT, "1220", inst=NOTE10))
        assert_integrity(self, rt, "full")

    # ---- the ProductJob wiring the WP-15 integration added to lifecycle_eod.REGISTRY, driven by a real scheduler ----------------------------------------
    def test_product_jobs_on_the_scheduler(self):
        rt = self.start()
        rt.curve = None
        sched = lifecycle.LifecycleScheduler(rt.bus, rt.db, rt, rt.clock, rt.log)
        rt.lifecycle = sched
        # the four bond jobs are REGISTRY ProductJobs now (the scheduler copies them): bound to services.bonds and the bonds.* kinds
        by_name = {j.name: j for j in sched.jobs}
        for name, kind in (("BOND_ACCRUE", "bonds.accrue"), ("BOND_COUPON", "bonds.coupon"), ("BOND_MATURITY", "bonds.mature"), ("BOND_LISTING", "bonds.list")):
            self.assertIsInstance(by_name[name], ProductJob, name)
            self.assertEqual((kind, "bonds"), (by_name[name].kind, by_name[name].module), name)
        self.buy_19_13()
        rt.clock.set("2027-04-19T00:35:00-04:00")
        out = sched.tick(only="BOND_ACCRUE")
        ran = [(i["key"], i["status"], i["request_id"]) for i in out["ran"]]
        # three nights, in date order, one command each with the scheduler's id; 122.00 + 122.00 + 123.00 = 367.00 on top of the carried 5,503.00
        self.assertEqual([(f"{DEBT}:{BOND}:2027-04-16", "DONE", f"job:BOND_ACCRUE:{DEBT}:{BOND}:2027-04-16"), (f"{DEBT}:{BOND}:2027-04-17", "DONE", f"job:BOND_ACCRUE:{DEBT}:{BOND}:2027-04-17"),
                          (f"{DEBT}:{BOND}:2027-04-18", "DONE", f"job:BOND_ACCRUE:{DEBT}:{BOND}:2027-04-18")], ran)
        self.assertEqual(587000, bal(rt, DEBT, "1220", inst=BOND))
        self.assertEqual([], sched.tick(only="BOND_ACCRUE")["ran"])                              # nothing twice
        self.assertEqual([], sched.tick(only="BOND_COUPON")["ran"])                              # no coupon before 1 Sep
        self.assertEqual([], sched.tick(only="BOND_MATURITY")["ran"])
        # the listing job (P07): the monthly model benchmarks are retired - April's key is DONE without a curve and lists nothing
        out = sched.tick(only="BOND_LISTING")
        self.assertEqual([("2027-04", "DONE", "job:BOND_LISTING:2027-04")], [(i["key"], i["status"], i["request_id"]) for i in out["ran"]])
        self.assertIsNone(rt.instruments.get("UST-10Y-202704"))
        self.assertEqual("DONE", one(rt, "SELECT status FROM jobs_done WHERE job='BOND_LISTING' AND key='2027-04'")["status"])
        assert_integrity(self, rt, "full")


if __name__ == "__main__":
    unittest.main()
