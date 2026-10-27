"""WP-26 TRS and cross-currency swap tests (TERMINAL_DESIGN.md s13.12, s11.2 TRS_RESET / XCCY_EVENTS, s11.3, s5.4, s19.16;
TERMINAL_WORKPLAN.md WP-26; the owner amendments "variation margin on swaps too" and "swap variation margin AND resets").
Every expected number is worked by hand in a comment from the inputs (and was re-derived with exact rationals apart from the
code, scratchpad wp26/hand.py); nothing is pasted from the code's output.  No network: FakeClock, FixedMarks, a CurveStore with
fixed discount factors, a RateStore with SOFR / ECBDFR prints, a temp database, the real lifecycle scheduler for the wiring.

The s19.16 figures run in the Fund Book's F1:PB_GS (books="real": F1 starts at 25,000,000.00 = the PB/OTC threshold).

TRS (receiver, 10,000 AAPL at 339.50, GS trs_spread 0.0060 x otc_spread_mult 1.0, SOFR 3.87 %), booked Mon 2026-11-02 so the
first reset (Wed 2026-12-02) comes "after 30 nights": notional 10,000 x 339.50 = 3,395,000.00; IM 10 % = 339,500.00; the
financing rate 0.0387 + 0.0060 = 0.0447 -> 3,395,000 x 0.0447 = 151,756.50 a year, /360 = 421.5458 a night: cumulative
to_c(421.5458) = 421.55, to_c(843.0917) = 843.09 (+421.54) ... to_c(151,756.50 x 30 / 360) = to_c(12,646.375) = 12,646.38
(HALF_EVEN, ...7|5 -> 8).  Dividend 0.26 ex 2026-11-09: 10,000 x 0.26 = 2,600.00.  Reset at the 12-02 close 345.00: return
(345.00 - 339.50) x 10,000 = 55,000.00; net cash 55,000 + 2,600 - 12,646.38 - 55,000 (VM returned) = -10,046.38; period P&L
55,000 + 2,600 - 12,646.38 = 44,953.62; new notional 10,000 x 345.00 = 3,450,000.00.

XCCY curves (the PB quotes each leg at its model par -/+ the GS half-width 1.0 bp):
- USD flat 4.01 % given as factors 1/1.02005^k to 8 dp: 0.98034410, 0.96107456, 0.94218377, 0.92366430.  4 periods: sum
  3.80726673, annuity 1.903633365, par = 2 x (1 - 0.92366430) / 3.80726673 = 0.15267140 / 3.80726673 = 0.0401000011 -> 0.04010;
  2 periods: sum 1.94141866, annuity 0.97070933, par = 2 x 0.03892544 / 1.94141866 = 0.0401000 -> 0.04010.  Receive quote
  0.04010 - 0.00010 = 0.04000.
- EUR: ECBDFR 2.49 -> a flat 2.49 % semi-annual curve, 1/1.01245^k at 1e-10: 0.9877030964, 0.9755574067, 0.9635610714,
  0.9517122538.  4 periods: sum 3.8785338283, annuity 1.93926691415 -> 1.9392669142 (HALF_EVEN at 1e-10), par = 2 x
  0.0482877462 / 3.8785338283 = 0.0249000000 -> 0.02490; 2 periods: annuity (0.9877030964 + 0.9755574067) / 2 = 0.98163025155
  -> 0.9816302516.  Pay quote 0.02490 + 0.00010 = 0.02500.  So the scenario's 4.00 % / 2.50 % ARE the dealer's quotes.
- Mirrored (receive EUR / pay USD): USD flat 3.99 % (1/1.01995^k: 0.98044022, 0.96126302, ...; 2 periods annuity 0.97085162,
  par 0.03990 -> pay quote 0.04000) and ECBDFR 2.51 (1/1.01255^k: 0.9876055503, 0.9753647231; annuity 0.9814851367, par 0.02510
  -> receive quote 0.02500).
"""
from __future__ import annotations

import json
import shutil
import tempfile
import types
import unittest
from datetime import date, timedelta
from decimal import Decimal

from tests.terminal_helpers import assert_integrity, bal, deposit, execute, make_runtime, snapshot
from finsim.terminal import lifecycle, prime, risk
from finsim.terminal.clock import epoch_of
from finsim.terminal.commands import ConflictError, RejectError
from finsim.terminal.products import swaps, swaps_trs
from finsim.terminal.quotes import MarkView
from finsim.terminal.quotes.rates import CurveStore, RateStore
from finsim.terminal.units import fx_c

D = Decimal
PB = "F1:PB_GS"
SWP1 = "SWP-000001"
TRS_AT = "2026-11-02T14:00:00-05:00"
XCCY_AT = "2026-05-20T14:00:00-04:00"
SOFR_PRINTS = [(date(2026, 5, 15) + timedelta(days=7 * i), D("3.87")) for i in range(30)]   # every Friday 2026-05-15 .. 2026-12-04: 3.87 %
USD401 = {"0.5": "0.98034410", "1.0": "0.96107456", "1.5": "0.94218377", "2.0": "0.92366430"}
USD399 = {"0.5": "0.98044022", "1.0": "0.96126302", "1.5": "0.94246092", "2.0": "0.92402659"}
TRS_TERMS = {"underlying_id": "AAPL", "units": 10000, "receiver": True, "reset_months": 1, "tenor_months": 12}
XCCY_TERMS = {"pay_ccy": "EUR", "recv_ccy": "USD", "pay_notional_c": 1000000000, "recv_notional_c": None, "pay_rate": None, "recv_rate": None,
              "pay_basis": "30/360", "recv_basis": "30/360", "months": 24, "mtm_reset": True}


def _h_open_pb_unchecked(ctx, payload):
    """What WP-23's onboarding does: open the PB account without the eligibility gate (the OTC gate is tested apart)."""
    return prime.open_pb(ctx, payload["lifetime_id"], payload["broker"], check_eligibility=False)


def runtime(tmp, at):
    """books="real" + prime + swaps (WP-17) + swaps_trs (WP-26) + the lifecycle scheduler; SOFR 3.87 % every Friday; F1:PB_GS open."""
    rt = make_runtime(tmp, at=at, books="real")
    prime.register(rt.bus)
    prime.bind(rt)
    rt.rates = RateStore()
    rt.rates.put("SOFR", SOFR_PRINTS, persist=False)
    rt.curve = CurveStore()
    swaps.register(rt.bus, rt)
    rt.swaps = swaps
    swaps_trs.register(rt.bus, rt)
    rt.swaps_trs = swaps_trs
    rt.providers = types.SimpleNamespace(yahoo=rt.fp)
    lifecycle.register(rt.bus, rt)
    rt.lifecycle = lifecycle.LifecycleScheduler(rt.bus, rt.db, rt, rt.clock, rt.log)
    rt.bus.register("test.open_pb_unchecked", _h_open_pb_unchecked)
    r = execute(rt, "account.open_pb", {"lifetime_id": "F1", "broker": "GS"})
    assert r.ok, r.body
    return rt


def q(rt, sql, params=()):
    with rt.db.reader() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(rt, sql, params=()):
    rows = q(rt, sql, params)
    return rows[0] if rows else None


def lines_of(rt, entry_id, base=False):
    """``[(gl, currency, instrument_id, amount_c[, base_c])]`` in line order (storage sign: debit +, credit -)."""
    rows = q(rt, "SELECT gl, currency, instrument_id, amount_c, base_c FROM journal_lines WHERE entry_id=? ORDER BY line_no", (entry_id,))
    return [(r["gl"], r["currency"], r["instrument_id"], r["amount_c"]) + ((r["base_c"],) if base else ()) for r in rows]


def entry_by_idem(rt, idem):
    return one(rt, "SELECT * FROM journal_entries WHERE idem_key=?", (idem,))


def lines_by_idem(rt, idem, base=False):
    e = entry_by_idem(rt, idem)
    assert e is not None, idem
    return lines_of(rt, e["entry_id"], base)


def contract(rt, cid=SWP1):
    return one(rt, "SELECT * FROM otc_contracts WHERE contract_id=?", (cid,))


def sweeps(rt):
    return [(r["currency"], r["amount_c"], r["linked_type"], r["linked_id"]) for r in
            q(rt, "SELECT currency, amount_c, linked_type, linked_id FROM transfers WHERE kind='SWEEP_IN' ORDER BY transfer_id")]


def book(rt, terms=None, request_id="ui:trs-book-0001", kind="TRS", account=PB):
    return execute(rt, "otc.book", {"account_id": account, "kind": kind, "terms": dict(TRS_TERMS if terms is None else terms)}, request_id)


def vm(rt, ds, cid=SWP1, suffix=""):
    return execute(rt, "swap.vm", {"contract_id": cid, "date": ds, "key": f"{cid}:{ds}"}, f"job:SWAP_VM:{cid}:{ds}{suffix}", actor="lifecycle")


def accrue(rt, kind, night, cid=SWP1, suffix=""):
    key = f"{kind}:{cid}:{night}"
    return execute(rt, "lifecycle.accrue", {"key": key, "kind": kind, "ref": cid, "night": night}, f"job:ACCRUE_DAILY:{key}{suffix}", actor="lifecycle")


def accrue_nights(tc, rt, first: date, n: int, kinds, cid=SWP1):
    for i in range(n):
        night = (first + timedelta(days=i)).isoformat()
        for kind in kinds:
            res = accrue(rt, kind, night, cid)
            tc.assertTrue(res.ok and res.body["status"] == "DONE", (night, kind, res.body))


def reset(rt, ds, cid=SWP1, rid=None):
    return execute(rt, "trs.reset", {"contract_id": cid, "date": ds, "key": f"{cid}:{ds}"}, rid or f"test:trs.reset:{cid}:{ds}", actor="lifecycle")


def event(rt, ds, ev, cid=SWP1, rid=None):
    kind = "xccy.fx_reset" if ev == "FX_RESET" else "xccy.exchange"
    return execute(rt, kind, {"contract_id": cid, "date": ds, "event": ev, "key": f"{cid}:{ds}:{ev}"}, rid or f"test:{kind}:{cid}:{ds}:{ev}", actor="lifecycle")


def set_close(rt, iid, px, ds, quality="close", hhmm="16:00"):
    rt.marks.set(iid, px, quality, epoch_of(date.fromisoformat(ds), hhmm))


def add_dividend(rt, iid, ex_date, amount):
    """The row the quote worker's dividends.upsert writes from Yahoo's chart events (WP-18 / WP-01)."""
    with rt.db.writer() as tx:
        tx.conn.execute("INSERT INTO dividends(instrument_id, ex_date, amount, pay_date, source, fetched_ts) VALUES (?,?,?,NULL,'Yahoo chart events (test)',0)",
                        (iid, ex_date, amount))


def ran(out, job):
    return [(i["key"], i["status"]) for i in out["ran"] if i["job"] == job]


class TrsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-swaps-trs-")
        self.rt = None

    def tearDown(self):
        if self.rt is not None:
            self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start(self, at=TRS_AT):
        self.rt = runtime(self.tmp, at)
        return self.rt

    # ---- s19.16 TRS: preview and booking ---------------------------------------------------------------------------------------------
    def test_trs_preview_and_booking_im_sweep_contract(self):
        rt = self.start()
        # preview through WP-17's gate and delegation: AAPL 339.50 delayed -> notional 3,395,000.00, IM 339,500.00, spread 0.0060 x 1.0 = 0.006,
        # financing SOFR 3.87 (obs Fri 2026-10-30) + 0.60 % = 0.0447 -> to_c(3,395,000 x 0.0447 / 360) = to_c(421.5458) = 421.55 a night (paid)
        p = swaps.preview(rt, PB, "TRS", TRS_TERMS)
        self.assertEqual(("TRS", "RECEIVE_TOTAL_RETURN", "339.5", 339500000, 33950000, "0.006", "0.6000 %"),
                         (p["kind"], p["side"], p["reset_px"], p["notional_c"], p["im_c"], p["spread"], p["spread_pct"]))
        f = p["financing"]
        self.assertEqual(("0.0447", "4.4700 %", 42155, "2026-10-30", "paid"), (f["rate"], f["rate_pct"], f["per_night_c"], f["sofr_obs_date"], f["direction"]))
        # dates: start Mon 2026-11-02; monthly resets modified following: Wed 12-02, 2027-01-02 is a Saturday -> Mon 2027-01-04, Tue 2027-02-02 ...;
        # maturity Tue 2027-11-02; 12 resets
        self.assertEqual(("2026-11-02", "2027-11-02", ["2026-12-02", "2027-01-04", "2027-02-02"], 12), (p["start_date"], p["maturity_date"], p["reset_dates"][:3],
                                                                                                      len(p["reset_dates"])))
        # indicative financing per period on today's notional: 30 nights -> to_c(151,756.50 x 30 / 360) = to_c(12,646.375) = 12,646.38;
        # 33 nights (12-02 .. 01-04) -> to_c(151,756.50 x 33 / 360) = to_c(13,911.0125) = 13,911.01
        self.assertEqual([(30, 1264638), (33, 1391101)], [(r["nights"], r["financing_c"]) for r in p["schedule"][:2]])
        self.assertEqual((0, 0, 0, "10.00 %", "CTM"), (p["mark0_c"], p["cost_of_dealing_c"], p["dv01_c"], p["im_pct"], p["margin_mode"]))
        self.assertEqual((33950000, 0, 33950000), (p["funding"]["need_c"], p["funding"]["own_settled_c"], p["funding"]["sweep_c"]))
        self.assertEqual(list(swaps_trs.CONVENTIONS), p["conventions"])
        self.assertTrue(p["eligibility"]["pb_otc"])
        # the payer receives SOFR - spread: 0.0387 - 0.0060 = 0.0327 -> to_c(3,395,000 x 0.0327 / 360) = to_c(111,016.50 / 360) = to_c(308.3792) = 308.38
        pp = swaps.preview(rt, PB, "TRS", dict(TRS_TERMS, receiver=False))
        self.assertEqual(("PAY_TOTAL_RETURN", "0.0327", 30838, "received"), (pp["side"], pp["financing"]["rate"], pp["financing"]["per_night_c"],
                                                                             pp["financing"]["direction"]))
        # the strategies module's TRS leg terms (WP-24): 1,000 x 339.50 = 339,500.00, IM 33,950.00; "side" is accepted too
        ps = swaps.preview(rt, PB, "TRS", {"underlying_id": "AAPL", "units": 1000, "receiver": False, "tenor_months": 12, "reset_months": 1})
        self.assertEqual((33950000, 3395000), (ps["notional_c"], ps["im_c"]))
        self.assertEqual("PAY_TOTAL_RETURN", swaps.preview(rt, PB, "TRS", {"underlying_id": "AAPL", "units": 1000, "side": "PAY"})["side"])
        # booking: IM Dr 1330 SWP-000001 339,500.00 / Cr 1010 after ONE Treasury sweep of exactly 339,500.00 (the PB account held 0)
        r = book(rt)
        self.assertTrue(r.ok, r.body)
        self.assertEqual((SWP1, "OPEN", "TRS"), (r.body["contract_id"], r.body["contract"]["status"], r.body["contract"]["kind"]))
        self.assertEqual([("1330", "USD", SWP1, 33950000), ("1010", "USD", None, -33950000)], lines_by_idem(rt, f"im:{SWP1}:open"))
        self.assertEqual([("USD", 33950000, "otc", f"im:{SWP1}:open")], sweeps(rt))
        self.assertEqual((0, 33950000), (bal(rt, PB, "1010"), bal(rt, PB, "1330", inst=SWP1)))
        c = contract(rt)
        self.assertEqual(("TRS", "OPEN", 339500000, "USD", "339.5", "2026-11-02", "2026-11-02", "2027-11-02", 33950000, 0, 0, "CTM", "GS"),
                         (c["kind"], c["status"], c["notional_c"], c["currency"], c["reset_px"], c["last_reset_date"], c["start_date"], c["maturity_date"], c["im_c"],
                          c["mark_c"], c["vm_balance_c"], c["margin_mode"], c["broker"]))
        t = json.loads(c["terms_json"])
        self.assertEqual(("AAPL", 10000, True, "0.006", 1, 12, ["2026-11-02", "2026-12-02", "2026-12-02"]),
                         (t["underlying_id"], t["units"], t["receiver"], t["spread"], t["reset_months"], t["tenor_months"], t["periods"][0]))
        self.assertEqual(t["periods"], t["fixed_periods"])
        inst = rt.instruments.get(SWP1)
        self.assertEqual(("SWAP", "AAPL", "COMPUTED", ("PB",), "2027-11-02"), (inst.kind, inst.underlying_id, inst.support, inst.account_kinds, inst.expiry))
        # the position: qty = + the notional for a receiver, mark 0 (S = reset_px), DV01 0; NAV(F1:PB_GS) = 1330 339,500.00 (transferred in);
        # NAV(F1:ALL) = 25,000,000.00 unchanged (the IM is an internal move and the mark is 0)
        # APIUI-07: qty = the notional in whole currency units (339,500,000 cents // 100 = 3,395,000), + for a receiver
        pos = rt.accounting.position(PB, SWP1)
        self.assertEqual((3395000, 0, "TRS", 0), (pos.qty, pos.mv_c, pos.extra["kind"], pos.extra["dv01_c"]))
        self.assertEqual((33950000, 2500000000), (rt.accounting.nav(PB).nav_c, rt.accounting.nav("F1:ALL").nav_c))
        mark_c, inputs = swaps.mark(rt, SWP1)
        self.assertEqual((0, "339.5", "delayed"), (mark_c, inputs["S"], inputs["underlying"]["quality"]))
        self.assertEqual(0, swaps.dv01_c(rt, SWP1))
        ev = swaps.next_events(rt, SWP1)
        self.assertEqual([("2026-12-02", "RESET"), ("2027-01-04", "RESET")], [(e["date"], e["event"]) for e in ev[:2]])
        # replay answers the stored result: one contract, one sweep
        r2 = book(rt)
        self.assertTrue(r2.ok and r2.replayed)
        self.assertEqual((1, 1), (len(q(rt, "SELECT 1 FROM otc_contracts")), len(sweeps(rt))))
        assert_integrity(self, rt, "full")

    # ---- s19.16 TRS: financing, dividend, VM, the reset -------------------------------------------------------------------------------
    def test_trs_19_16_financing_dividend_vm_and_reset(self):
        rt = self.start()
        self.assertTrue(book(rt).ok)
        add_dividend(rt, "AAPL", "2026-11-09", "0.26")
        # VM (WP-17's swap.vm valuing through mark_trs): day 9, Tue 11-10.  LC-3: with only a 14:00 print of 340.20 the VM of 11-10 never posts at an
        # intraday price - SKIPPED CLOSE_PENDING (retried; quiet on its first attempt, one WARN attention when it persists)
        rt.marks.set("AAPL", "340.20", "delayed", epoch_of(date(2026, 11, 10), "14:00"))
        rt.clock.set("2026-11-10T17:10:00-05:00")
        r = vm(rt, "2026-11-10")
        self.assertEqual(("SKIPPED", "CLOSE_PENDING", True, None), (r.body["status"], r.body["code"], r.body["retry"], r.body["attention_id"]))
        r = vm(rt, "2026-11-10", suffix=":r1")
        self.assertEqual(("SKIPPED", "CLOSE_PENDING"), (r.body["status"], r.body["code"]))
        self.assertEqual(1, len(q(rt, "SELECT 1 FROM attention WHERE code='CLOSE_PENDING' AND ref_id=? AND resolved_ts IS NULL", (f"{SWP1}:2026-11-10",))))
        self.assertEqual([], q(rt, "SELECT 1 FROM otc_vm"))
        # AAPL closes 341.00 -> mark 10,000 x (341.00 - 339.50) = 15,000.00, reference 0 -> we receive 15,000.00; the attention resolves
        set_close(rt, "AAPL", "341.00", "2026-11-10")
        r = vm(rt, "2026-11-10", suffix=":r2")
        self.assertEqual(("DONE", 1500000, 1500000), (r.body["status"], r.body["mark_c"], r.body["delta_c"]))
        self.assertEqual([], q(rt, "SELECT 1 FROM attention WHERE code='CLOSE_PENDING' AND resolved_ts IS NULL"))
        # day 10, 11-11, 342.00 -> mark 25,000.00, delta 10,000.00 -> Dr 1010 / Cr 2330 10,000.00 (s19.16)
        set_close(rt, "AAPL", "342.00", "2026-11-11")
        rt.clock.set("2026-11-11T17:10:00-05:00")
        r = vm(rt, "2026-11-11")
        self.assertEqual((2500000, 1000000), (r.body["mark_c"], r.body["delta_c"]))
        self.assertEqual([("1010", "USD", None, 1000000), ("2330", "USD", SWP1, -1000000)], lines_by_idem(rt, f"swapvm:{SWP1}:2026-11-11"))
        # day 30, 12-01, 345.00 -> mark 55,000.00, delta 30,000.00; VM balance 55,000.00 = 2330 = settled cash
        set_close(rt, "AAPL", "345.00", "2026-12-01")
        rt.clock.set("2026-12-01T17:10:00-05:00")
        self.assertEqual(3000000, vm(rt, "2026-12-01").body["delta_c"])
        self.assertEqual((5500000, 5500000, 5500000), (contract(rt)["vm_balance_c"], bal(rt, PB, "2330", inst=SWP1), bal(rt, PB, "1010")))
        # financing nights 11-02 .. 11-30 through ACCRUE_DAILY (29 of the period's 30; 12-01 below)
        rt.clock.set("2026-12-02T10:00:00-05:00")
        accrue_nights(self, rt, date(2026, 11, 2), 29, ("trsfin",))
        # night 1: Dr 4450 / Cr 2340 421.55; night 2: cumulative 843.09 -> +421.54
        self.assertEqual([("4450", "USD", SWP1, 42155), ("2340", "USD", SWP1, -42155)], lines_by_idem(rt, f"accr:trsfin:{SWP1}:2026-11-02"))
        self.assertEqual([("4450", "USD", SWP1, 42154), ("2340", "USD", SWP1, -42154)], lines_by_idem(rt, f"accr:trsfin:{SWP1}:2026-11-03"))
        # the dividend 0.26 ex 11-09 accrues with that night: 10,000 x 0.26 = 2,600.00 Dr 1225 / Cr 4450 (receiver), business date the ex-date
        e = entry_by_idem(rt, f"trsdiv:{SWP1}:2026-11-09")
        self.assertEqual(([("1225", "USD", SWP1, 260000), ("4450", "USD", SWP1, -260000)], "2026-11-09"), (lines_of(rt, e["entry_id"]), e["business_date"]))
        # the reset key exists from 16:25 ET of the reset date
        rt.clock.set("2026-12-02T16:20:00-05:00")
        with rt.db.reader() as conn:
            self.assertEqual([], swaps_trs.reset_keys(conn, rt.clock.epoch()))
        rt.clock.set("2026-12-02T16:30:00-05:00")
        with rt.db.reader() as conn:
            self.assertEqual([(SWP1, "2026-12-02")], swaps_trs.reset_keys(conn, rt.clock.epoch()))
        # pending 1: the last AAPL print is the 12-01 close (not 12-02's) -> SKIPPED CLOSE_PENDING with one WARN attention, nothing posted
        r = reset(rt, "2026-12-02", rid="test:reset-1")
        self.assertEqual(("SKIPPED", "CLOSE_PENDING"), (r.body["status"], r.body["code"]))
        self.assertEqual(1, len(q(rt, "SELECT 1 FROM attention WHERE code='CLOSE_PENDING' AND resolved_ts IS NULL")))
        # pending 2: the 12-02 close is 345.00 but the financing is accrued through 11-30 only -> SKIPPED ACCRUALS_PENDING
        set_close(rt, "AAPL", "345.00", "2026-12-02")
        r = reset(rt, "2026-12-02", rid="test:reset-2")
        self.assertEqual(("SKIPPED", "ACCRUALS_PENDING"), (r.body["status"], r.body["code"]))
        self.assertIsNone(entry_by_idem(rt, f"trsreset:{SWP1}:2026-12-02"))
        # night 12-01 (the 30th): cumulative to_c(12,646.375) = 12,646.38 = 2340
        self.assertTrue(accrue(rt, "trsfin", "2026-12-01").ok)
        self.assertEqual((1264638, 260000), (bal(rt, PB, "2340", inst=SWP1), bal(rt, PB, "1225", inst=SWP1)))
        st = one(rt, "SELECT * FROM accrual_state WHERE kind='trsfin' AND ref_id=?", (SWP1,))
        self.assertEqual(("2026-11-02", "2026-12-01", 1264638, 339500000, "0.0447"), (st["segment_start"], st["accrued_through"], st["cum_c"], st["base_c"], st["rate"]))
        # pending 3: the night of 12-02 belongs to the next period, whose notional the reset sets -> SKIPPED RESET_PENDING (the night waits)
        r = accrue(rt, "trsfin", "2026-12-02")
        self.assertEqual(("SKIPPED", "RESET_PENDING"), (r.body["status"], r.body["code"]))
        self.assertIsNone(entry_by_idem(rt, f"accr:trsfin:{SWP1}:2026-12-02"))
        # the reset through the scheduler: ONE entry - return leg (345.00 - 339.50) x 10,000 = 55,000.00 Dr 1010 / Cr 4450; the dividend accrual
        # settled Dr 1010 2,600.00 / Cr 1225; the financing paid Dr 2340 12,646.38 / Cr 1010; the VM collateral received against the 55,000.00
        # mark returned Dr 2330 55,000.00 / Cr 1010; net cash 55,000 + 2,600 - 12,646.38 - 55,000 = -10,046.38 (nothing is paid twice)
        out = rt.lifecycle.tick(only="TRS_RESET")
        self.assertEqual([(f"{SWP1}:2026-12-02", "DONE")], ran(out, "TRS_RESET"))
        e = entry_by_idem(rt, f"trsreset:{SWP1}:2026-12-02")
        self.assertEqual([("1010", "USD", None, 5500000), ("4450", "USD", SWP1, -5500000), ("1010", "USD", None, 260000), ("1225", "USD", SWP1, -260000),
                          ("2340", "USD", SWP1, 1264638), ("1010", "USD", None, -1264638), ("2330", "USD", SWP1, 5500000), ("1010", "USD", None, -5500000)],
                         lines_of(rt, e["entry_id"]))
        self.assertEqual("2026-12-02", e["business_date"])
        # reset_px 345.00, notional 10,000 x 345.00 = 3,450,000.00, mark 0, VM balance 0 (L22); the RESET row re-bases the VM reference to 0
        c = contract(rt)
        self.assertEqual(("345", 345000000, 0, 0, "2026-12-02"), (c["reset_px"], c["notional_c"], c["mark_c"], c["vm_balance_c"], c["last_reset_date"]))
        rr = one(rt, "SELECT * FROM otc_resets WHERE contract_id=? AND kind='RESET'", (SWP1,))
        d = json.loads(rr["detail_json"])
        self.assertEqual(("2026-12-02", -1004638, 0, 5500000, 339500000, e["entry_id"]), (rr["event_date"], rr["amount_c"], d["post_mark_c"], d["return_c"],
                                                                                          d["notional_old_c"], rr["entry_id"]))
        # balances: 1225 = 2340 = 1320 = 2330 = 0; 1330 339,500.00; cash 55,000.00 - 10,046.38 = 44,953.62; 4450 = the period P&L
        # 55,000 + 2,600 - 12,646.38 = 44,953.62
        self.assertEqual((0, 0, 0, 0, 33950000, 4495362, 4495362),
                         (bal(rt, PB, "1225", inst=SWP1), bal(rt, PB, "2340", inst=SWP1), bal(rt, PB, "1320", inst=SWP1), bal(rt, PB, "2330", inst=SWP1),
                          bal(rt, PB, "1330", inst=SWP1), bal(rt, PB, "1010"), bal(rt, PB, "4450", inst=SWP1)))
        # NAV(F1:PB_GS) = 44,953.62 + 339,500.00 = 384,453.62; NAV(F1:ALL) = 25,000,000.00 + 44,953.62
        self.assertEqual((38445362, 2504495362), (rt.accounting.nav(PB).nav_c, rt.accounting.nav("F1:ALL").nav_c))
        self.assertEqual([], q(rt, "SELECT 1 FROM attention WHERE code IN ('CLOSE_PENDING','ACCRUALS_PENDING') AND resolved_ts IS NULL"))
        assert_integrity(self, rt, "full")
        # the VM of 12-02 (17:05) values at the close 345.00 against the new reset_px: mark 0, reference 0 -> NO_CHANGE (quiet, nothing posted)
        rt.clock.set("2026-12-02T17:10:00-05:00")
        r = vm(rt, "2026-12-02")
        self.assertEqual(("SKIPPED", "NO_CHANGE", 0), (r.body["status"], r.body["code"], r.body["mark_c"]))
        # the night of 12-02 now accrues on the new notional: to_c(3,450,000 x 0.0447 / 360) = to_c(428.375) = 428.38, a new segment
        rt.clock.set("2026-12-03T00:40:00-05:00")
        r = accrue(rt, "trsfin", "2026-12-02", suffix=":r1")
        self.assertTrue(r.ok and r.body["status"] == "DONE", r.body)
        self.assertEqual([("4450", "USD", SWP1, 42838), ("2340", "USD", SWP1, -42838)], lines_by_idem(rt, f"accr:trsfin:{SWP1}:2026-12-02"))
        st = one(rt, "SELECT * FROM accrual_state WHERE kind='trsfin' AND ref_id=?", (SWP1,))
        self.assertEqual(("2026-12-02", 42838, 345000000), (st["segment_start"], st["cum_c"], st["base_c"]))
        # idempotency: no reset key is due any more, a retry answers "already", nothing is posted twice
        with rt.db.reader() as conn:
            self.assertEqual([], swaps_trs.reset_keys(conn, rt.clock.epoch()))
        self.assertEqual([], ran(rt.lifecycle.tick(only="TRS_RESET"), "TRS_RESET"))
        r = reset(rt, "2026-12-02", rid="test:reset-3")
        self.assertTrue(r.ok and r.body["already"] and not r.body["posted"], r.body)
        self.assertEqual(1, len(q(rt, "SELECT 1 FROM journal_entries WHERE idem_key LIKE 'trsreset:%'")))
        self.assertEqual(1, len(q(rt, "SELECT 1 FROM journal_entries WHERE idem_key LIKE 'trsdiv:%'")))
        assert_integrity(self, rt, "full")

    # ---- s19.16 payer variant -----------------------------------------------------------------------------------------------------
    def test_trs_payer_variant_signs(self):
        rt = self.start()
        self.assertTrue(book(rt, dict(TRS_TERMS, receiver=False)).ok)
        add_dividend(rt, "AAPL", "2026-11-09", "0.26")
        # the payer's mark is -(S - 339.50) x 10,000: -15,000.00, -25,000.00, -55,000.00; each fall is VM POSTED (Dr 1320 / Cr 1010), funded by a
        # Treasury sweep of exactly the delta (the PB account holds no cash): 15,000.00, 10,000.00, 30,000.00
        for ds, px, delta in (("2026-11-10", "341.00", -1500000), ("2026-11-11", "342.00", -1000000), ("2026-12-01", "345.00", -3000000)):
            set_close(rt, "AAPL", px, ds)
            rt.clock.set(f"{ds}T17:10:00-05:00")
            r = vm(rt, ds)
            self.assertEqual(("DONE", delta), (r.body["status"], r.body["delta_c"]), ds)
        self.assertEqual([("1320", "USD", SWP1, 1500000), ("1010", "USD", None, -1500000)], lines_by_idem(rt, f"swapvm:{SWP1}:2026-11-10"))
        self.assertEqual([("USD", 33950000, "otc", f"im:{SWP1}:open"), ("USD", 1500000, "swapvm", f"swapvm:{SWP1}:2026-11-10"),
                          ("USD", 1000000, "swapvm", f"swapvm:{SWP1}:2026-11-11"), ("USD", 3000000, "swapvm", f"swapvm:{SWP1}:2026-12-01")], sweeps(rt))
        self.assertEqual((-5500000, 5500000, 0), (contract(rt)["vm_balance_c"], bal(rt, PB, "1320", inst=SWP1), bal(rt, PB, "1010")))
        # financing RECEIVED at SOFR - spread = 0.0327: night 1 to_c(308.3792) = 308.38 Dr 1225 / Cr 4450; night 2 cumulative to_c(616.7583) = 616.76
        # -> +308.38; 30 nights to_c(111,016.50 x 30 / 360) = to_c(9,251.375) = 9,251.38 (HALF_EVEN ...7|5 -> 8)
        rt.clock.set("2026-12-02T10:00:00-05:00")
        accrue_nights(self, rt, date(2026, 11, 2), 30, ("trsfin",))
        self.assertEqual([("1225", "USD", SWP1, 30838), ("4450", "USD", SWP1, -30838)], lines_by_idem(rt, f"accr:trsfin:{SWP1}:2026-11-02"))
        self.assertEqual([("1225", "USD", SWP1, 30838), ("4450", "USD", SWP1, -30838)], lines_by_idem(rt, f"accr:trsfin:{SWP1}:2026-11-03"))
        # the dividend is PAYABLE by the payer: Dr 4450 / Cr 2340 2,600.00
        self.assertEqual([("4450", "USD", SWP1, 260000), ("2340", "USD", SWP1, -260000)], lines_by_idem(rt, f"trsdiv:{SWP1}:2026-11-09"))
        self.assertEqual((925138, 260000), (bal(rt, PB, "1225", inst=SWP1), bal(rt, PB, "2340", inst=SWP1)))
        # the reset at 345.00: return leg -55,000.00 (Dr 4450 / Cr 1010), financing received Dr 1010 9,251.38 / Cr 1225, dividend paid Dr 2340 2,600.00 /
        # Cr 1010, the VM posted comes back Dr 1010 55,000.00 / Cr 1320; net cash -55,000 + 9,251.38 - 2,600 + 55,000 = +6,651.38
        set_close(rt, "AAPL", "345.00", "2026-12-02")
        rt.clock.set("2026-12-02T16:30:00-05:00")
        r = reset(rt, "2026-12-02")
        self.assertTrue(r.ok and r.body["status"] == "DONE", r.body)
        self.assertEqual((-5500000, 665138), (r.body["return_c"], r.body["cash_c"]))
        self.assertEqual([("1010", "USD", None, -5500000), ("4450", "USD", SWP1, 5500000), ("1010", "USD", None, 925138), ("1225", "USD", SWP1, -925138),
                          ("2340", "USD", SWP1, 260000), ("1010", "USD", None, -260000), ("1010", "USD", None, 5500000), ("1320", "USD", SWP1, -5500000)],
                         lines_by_idem(rt, f"trsreset:{SWP1}:2026-12-02"))
        # period P&L -55,000 + 9,251.38 - 2,600 = -48,348.62 = 4450; cash 0 + 6,651.38; NAV(F1:ALL) = 25,000,000.00 - 48,348.62
        self.assertEqual((-4834862, 665138, 0, 0), (bal(rt, PB, "4450", inst=SWP1), bal(rt, PB, "1010"), bal(rt, PB, "1320", inst=SWP1), contract(rt)["vm_balance_c"]))
        self.assertEqual(2495165138, rt.accounting.nav("F1:ALL").nav_c)
        assert_integrity(self, rt, "full")

    # ---- STM: the daily VM is realised, the reset settles only what the VM has not ------------------------------------------------------
    def test_trs_stm_reset_settles_only_the_unsettled_return(self):
        rt = self.start()
        self.assertTrue(execute(rt, "account.update_schedule", {"account_id": PB, "swap_margin_mode": "STM"}).ok)
        self.assertTrue(book(rt).ok)
        self.assertEqual("STM", contract(rt)["margin_mode"])
        add_dividend(rt, "AAPL", "2026-11-09", "0.26")
        # STM VM: 11-10 at 341.00 -> mark 15,000.00 - reference 0 = +15,000.00 settled as P&L (Dr 1010 / Cr 4450), mark_c back to 0;
        # 12-01 at 345.00 -> mark 55,000.00 - reference 15,000.00 (the 11-10 model mark) = +40,000.00
        for ds, px, delta in (("2026-11-10", "341.00", 1500000), ("2026-12-01", "345.00", 4000000)):
            set_close(rt, "AAPL", px, ds)
            rt.clock.set(f"{ds}T17:10:00-05:00")
            self.assertEqual(delta, vm(rt, ds).body["delta_c"], ds)
        self.assertEqual([("1010", "USD", None, 4000000), ("4450", "USD", SWP1, -4000000)], lines_by_idem(rt, f"swapvm:{SWP1}:2026-12-01"))
        self.assertEqual((0, 0), (contract(rt)["mark_c"], contract(rt)["vm_balance_c"]))
        rt.clock.set("2026-12-02T10:00:00-05:00")
        accrue_nights(self, rt, date(2026, 11, 2), 30, ("trsfin",))
        set_close(rt, "AAPL", "345.00", "2026-12-02")
        rt.clock.set("2026-12-02T16:30:00-05:00")
        r = reset(rt, "2026-12-02")
        self.assertTrue(r.ok and r.body["status"] == "DONE", r.body)
        # the return 55,000.00 was already settled by the STM VMs (15,000 + 40,000 = the reference 55,000): no return line; the dividend 2,600.00 and the
        # financing 12,646.38 settle -> the same net cash -10,046.38 and the same period P&L 15,000 + 40,000 + 2,600 - 12,646.38 = 44,953.62 as CTM
        self.assertEqual((5500000, 0, -1004638), (r.body["return_c"], r.body["settled_return_c"], r.body["cash_c"]))
        self.assertEqual([("1010", "USD", None, 260000), ("1225", "USD", SWP1, -260000), ("2340", "USD", SWP1, 1264638), ("1010", "USD", None, -1264638)],
                         lines_by_idem(rt, f"trsreset:{SWP1}:2026-12-02"))
        self.assertEqual((4495362, 4495362), (bal(rt, PB, "4450", inst=SWP1), bal(rt, PB, "1010")))
        # the RESET row re-bases the reference to 0: 12-02 at 345.00 -> NO_CHANGE; 12-03 at 346.00 -> 10,000 x (346.00 - 345.00) = +10,000.00 settled
        rt.clock.set("2026-12-02T17:10:00-05:00")
        r = vm(rt, "2026-12-02")
        self.assertEqual(("SKIPPED", "NO_CHANGE", 0), (r.body["status"], r.body["code"], r.body["mark_c"]))
        set_close(rt, "AAPL", "346.00", "2026-12-03")
        rt.clock.set("2026-12-03T17:10:00-05:00")
        r = vm(rt, "2026-12-03")
        self.assertEqual((1000000, 0, 1000000), (r.body["mark_c"], r.body["reference_c"], r.body["delta_c"]))
        self.assertEqual([("1010", "USD", None, 1000000), ("4450", "USD", SWP1, -1000000)], lines_by_idem(rt, f"swapvm:{SWP1}:2026-12-03"))
        assert_integrity(self, rt, "full")

    # ---- termination ---------------------------------------------------------------------------------------------------------------
    def test_trs_terminate_mid_period(self):
        rt = self.start()
        self.assertTrue(book(rt).ok)
        # VM on Thu 11-05 at the 341.00 close: mark 15,000.00 received; nights 11-02 .. 11-05 accrued (cumulative to_c(151,756.50 x 4 / 360)
        # = to_c(1,686.1833) = 1,686.18)
        set_close(rt, "AAPL", "341.00", "2026-11-05")
        rt.clock.set("2026-11-05T17:10:00-05:00")
        self.assertEqual(1500000, vm(rt, "2026-11-05").body["delta_c"])
        rt.clock.set("2026-11-06T10:00:00-05:00")
        accrue_nights(self, rt, date(2026, 11, 2), 4, ("trsfin",))
        self.assertEqual(168618, bal(rt, PB, "2340", inst=SWP1))
        # Saturday 11-07: a stale AAPL price refuses the termination (409 QUOTE_STALE) and nothing is written
        rt.clock.set("2026-11-07T10:00:00-05:00")
        set_close(rt, "AAPL", "342.50", "2026-11-06", quality="stale")
        r = execute(rt, "otc.terminate", {"contract_id": SWP1}, "ui:trs-term-0001")
        self.assertEqual((409, "QUOTE_STALE"), (r.http, r.code))
        self.assertEqual(("OPEN", None), (contract(rt)["status"], entry_by_idem(rt, f"accr:trsfin:{SWP1}:2026-11-06")))
        # at the 342.50 CLOSE: the night owed through last night (11-06, the 5th) accrues first: to_c(151,756.50 x 5 / 360) = to_c(2,107.7292) = 2,107.73
        # (+421.55); value = 10,000 x (342.50 - 339.50) = 30,000.00; one close entry: VM received unwound Dr 2330 15,000.00 / Cr 1010, the value
        # Dr 1010 30,000.00 / Cr 4450, the accrued financing paid Dr 2340 2,107.73 / Cr 1010; then the IM 339,500.00 released
        set_close(rt, "AAPL", "342.50", "2026-11-06")
        r = execute(rt, "otc.terminate", {"contract_id": SWP1}, "ui:trs-term-0002")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((3000000, 3000000, 1289227, 33950000, 210773, 0, "TERMINATED"),
                         (r.body["termination_value_c"], r.body["result_c"], r.body["cash_c"], r.body["im_released_c"], r.body["accrued_pay_c"],
                          r.body["accrued_receive_c"], r.body["contract"]["status"]))
        self.assertEqual([("4450", "USD", SWP1, 42155), ("2340", "USD", SWP1, -42155)], lines_by_idem(rt, f"accr:trsfin:{SWP1}:2026-11-06"))
        self.assertEqual([("2330", "USD", SWP1, 1500000), ("1010", "USD", None, -1500000), ("1010", "USD", None, 3000000), ("4450", "USD", SWP1, -3000000),
                          ("2340", "USD", SWP1, 210773), ("1010", "USD", None, -210773)], lines_by_idem(rt, f"otc:{SWP1}:terminate"))
        self.assertEqual([("1010", "USD", None, 33950000), ("1330", "USD", SWP1, -33950000)], lines_by_idem(rt, f"im:{SWP1}:release"))
        self.assertEqual(3, len(r.body["entries"]))
        # cash 15,000.00 - 15,000.00 + 30,000.00 - 2,107.73 + 339,500.00 = 367,392.27; 4450 = 30,000.00 - 2,107.73 = 27,892.27; every margin balance 0
        self.assertEqual((36739227, 2789227, 0, 0, 0, 0), (bal(rt, PB, "1010"), bal(rt, PB, "4450", inst=SWP1), bal(rt, PB, "2330", inst=SWP1),
                                                           bal(rt, PB, "1330", inst=SWP1), bal(rt, PB, "2340", inst=SWP1), bal(rt, PB, "1225", inst=SWP1)))
        self.assertEqual((2502789227, []), (rt.accounting.nav("F1:ALL").nav_c, rt.accounting.positions(PB)))
        c = contract(rt)
        self.assertEqual(("TERMINATED", 0, 0, 0), (c["status"], c["mark_c"], c["vm_balance_c"], c["im_c"]))
        self.assertEqual(("EXPIRED", []), (rt.instruments.get(SWP1).status, q(rt, "SELECT 1 FROM accrual_state WHERE ref_id=?", (SWP1,))))
        with rt.db.reader() as conn:
            self.assertEqual(([], []), (swaps_trs.reset_keys(conn, rt.clock.epoch()), swaps.vm_keys(conn, rt.clock.epoch())))
        r2 = execute(rt, "otc.terminate", {"contract_id": SWP1}, "ui:trs-term-0003")
        self.assertEqual((409, "NOT_OPEN"), (r2.http, r2.code))
        assert_integrity(self, rt, "full")

    # ---- maturity at the last reset ---------------------------------------------------------------------------------------------------
    def test_trs_matures_at_its_last_reset(self):
        rt = self.start()
        # one period: tenor 1 month -> maturity = the first reset date 2026-12-02; no dividend row, no VM run
        r = book(rt, dict(TRS_TERMS, tenor_months=1))
        self.assertTrue(r.ok, r.body)
        self.assertEqual("2026-12-02", contract(rt)["maturity_date"])
        rt.clock.set("2026-12-02T10:00:00-05:00")
        accrue_nights(self, rt, date(2026, 11, 2), 30, ("trsfin",))
        set_close(rt, "AAPL", "345.00", "2026-12-02")
        rt.clock.set("2026-12-02T16:30:00-05:00")
        out = rt.lifecycle.tick(only="TRS_RESET")
        self.assertEqual([(f"{SWP1}:2026-12-02", "DONE")], ran(out, "TRS_RESET"))
        # the last reset: return 55,000.00 received, financing 12,646.38 paid -> net +42,353.62; then the maturity releases the IM 339,500.00
        self.assertEqual([("1010", "USD", None, 5500000), ("4450", "USD", SWP1, -5500000), ("2340", "USD", SWP1, 1264638), ("1010", "USD", None, -1264638)],
                         lines_by_idem(rt, f"trsreset:{SWP1}:2026-12-02"))
        self.assertEqual([("1010", "USD", None, 33950000), ("1330", "USD", SWP1, -33950000)], lines_by_idem(rt, f"im:{SWP1}:release"))
        self.assertIsNone(entry_by_idem(rt, f"otc:{SWP1}:mature"))          # nothing left to unwind: VM 0, value 0
        c = contract(rt)
        self.assertEqual(("MATURED", 0, 0, 0), (c["status"], c["mark_c"], c["vm_balance_c"], c["im_c"]))
        # cash 42,353.62 + 339,500.00 = 381,853.62 = NAV(F1:PB_GS); 4450 = 42,353.62; the contract is gone from positions and keys
        self.assertEqual((38185362, 4235362, 38185362), (bal(rt, PB, "1010"), bal(rt, PB, "4450", inst=SWP1), rt.accounting.nav(PB).nav_c))
        self.assertEqual(("EXPIRED", [], []), (rt.instruments.get(SWP1).status, rt.accounting.positions(PB), q(rt, "SELECT 1 FROM accrual_state WHERE ref_id=?", (SWP1,))))
        with rt.db.reader() as conn:
            self.assertEqual([], swaps_trs.reset_keys(conn, rt.clock.epoch()))
        self.assertEqual([], swaps_trs.next_events(rt, SWP1))
        assert_integrity(self, rt, "full")

    # ---- refusals and pending outcomes ----------------------------------------------------------------------------------------------
    def test_trs_refusals_and_pending_outcomes(self):
        rt = self.start()
        # an observed price is required: stale -> 409 QUOTE_STALE; none -> 409 NO_QUOTE; nothing is written
        rt.marks.set("AAPL", "339.50", "stale", rt.clock.epoch() - 3600)
        r = book(rt, request_id="ui:trs-book-stale")
        self.assertEqual((409, "QUOTE_STALE"), (r.http, r.code))
        rt.marks.unset("AAPL")
        r = book(rt, request_id="ui:trs-book-none")
        self.assertEqual((409, "NO_QUOTE"), (r.http, r.code))
        self.assertEqual(([], []), (q(rt, "SELECT 1 FROM otc_contracts"), sweeps(rt)))
        rt.marks.set("AAPL", "339.50", "delayed", rt.clock.epoch())
        # terms and underlyings
        for terms, code in ((dict(TRS_TERMS, underlying_id="ZZZZ"), "UNKNOWN_UNDERLYING"), (dict(TRS_TERMS, underlying_id="EURUSD"), "UNDERLYING_UNSUPPORTED"),
                            (dict(TRS_TERMS, underlying_id="0700-HK"), "CURRENCY_UNSUPPORTED"), (dict(TRS_TERMS, units=0), "TERMS"),
                            (dict(TRS_TERMS, units="1.5"), "TERMS"), (dict(TRS_TERMS, reset_months=2), "TERMS"),
                            (dict(TRS_TERMS, reset_months=3, tenor_months=13), "TERMS"), (dict(TRS_TERMS, spread="0.001"), "TERMS"),
                            ({"underlying_id": "AAPL", "units": 100}, "TERMS"), (dict(TRS_TERMS, receiver="maybe"), "TERMS")):
            with self.assertRaises(RejectError) as cm:
                swaps.preview(rt, PB, "TRS", terms)
            self.assertEqual(code, cm.exception.code, terms)
        # WP-17's gates run first: a non-PB account -> 400 NOT_TRADEABLE_HERE; a PB account in the Personal Book (1,000,000 NAV) -> 400 NOT_ELIGIBLE
        r = book(rt, account="F1:US_EXCHANGE", request_id="ui:trs-book-usx")
        self.assertEqual((400, "NOT_TRADEABLE_HERE"), (r.http, r.code))
        self.assertTrue(execute(rt, "test.open_pb_unchecked", {"lifetime_id": "P1", "broker": "GS"}).ok)
        r = book(rt, account="P1:PB_GS", request_id="ui:trs-book-p1")
        self.assertEqual((400, "NOT_ELIGIBLE"), (r.http, r.code))
        self.assertTrue(book(rt).ok)
        # no SOFR within 10 days of the night -> the ACCRUE_DAILY key is SKIPPED RATE_UNAVAILABLE (never defaulted), nothing posted
        saved = rt.rates
        rt.rates = RateStore()
        rt.clock.set("2026-11-03T00:40:00-05:00")
        r = accrue(rt, "trsfin", "2026-11-02")
        self.assertEqual(("SKIPPED", "RATE_UNAVAILABLE"), (r.body["status"], r.body["code"]))
        self.assertIsNone(entry_by_idem(rt, f"accr:trsfin:{SWP1}:2026-11-02"))
        rt.rates = saved
        r = accrue(rt, "trsfin", "2026-11-02", suffix=":r1")
        self.assertEqual(("DONE", 42155), (r.body["status"], r.body["amount_c"]))
        # no AAPL price -> the mark is the stored one labelled stale, and the VM of 11-03 (a trading day whose close is neither recorded nor observed)
        # SKIPs CLOSE_PENDING (retried; LC-3: never valued at another day's price), nothing posted
        rt.marks.unset("AAPL")
        mv = swaps.mark(rt, SWP1)
        self.assertIsInstance(mv, MarkView)
        self.assertEqual(("stale", "0.00"), (mv.quality, str(mv.mark)))
        rt.clock.set("2026-11-03T17:10:00-05:00")
        r = vm(rt, "2026-11-03")
        self.assertEqual(("SKIPPED", "CLOSE_PENDING", True), (r.body["status"], r.body["code"], r.body["retry"]))
        self.assertEqual([], q(rt, "SELECT 1 FROM otc_vm"))
        # a reset date that is not one of the contract's -> 400 KEY_INVALID
        r = reset(rt, "2026-12-03")
        self.assertEqual((400, "KEY_INVALID"), (r.http, r.code))
        assert_integrity(self, rt, "full")

    # ---- the hooks WP-17 / WP-08 / WP-09 call ------------------------------------------------------------------------------------------
    def test_trs_wiring_scheduler_risk_views_and_delegation(self):
        rt = self.start()
        self.assertTrue(book(rt).ok)
        # ACCRUE_DAILY finds the trsfin key through services.swaps_trs.accrue_night (no "not built" note)
        out = rt.lifecycle.tick(at="2026-11-03T00:40:00-05:00", only="ACCRUE_DAILY")
        self.assertIn((f"trsfin:{SWP1}:2026-11-02", "DONE"), ran(out, "ACCRUE_DAILY"))
        self.assertNotIn("trsfin", rt.lifecycle.job("ACCRUE_DAILY").notes)
        self.assertEqual(42155, bal(rt, PB, "2340", inst=SWP1))
        # the product jobs are bound: TRS_RESET / XCCY_EVENTS carry no "not built" note
        rt.lifecycle.tick(only="TRS_RESET")
        rt.lifecycle.tick(only="XCCY_EVENTS")
        self.assertNotIn("keys", rt.lifecycle.job("TRS_RESET").notes)
        self.assertNotIn("keys", rt.lifecycle.job("XCCY_EVENTS").notes)
        # risk (WP-09): the TRS is "TRS (underlying exposure)" at + the notional 3,395,000.00 for a receiver
        v = risk.report(rt, PB)
        row = next(p for p in v.positions if p.instrument_id == SWP1)
        self.assertEqual(("TRS (underlying exposure)", 339500000), (row.bucket, row.exposure_c))
        # WP-17 delegation: swap.pay / swap.fix on a TRS are no-ops (it settles at its resets); the views carry the mark and the next events
        r = execute(rt, "swap.pay", {"contract_id": SWP1, "date": "2026-12-02", "key": f"{SWP1}:2026-12-02"}, "test:swap.pay:trs", actor="lifecycle")
        self.assertEqual(("DONE", "TRS_PAYS_AT_RESET", False), (r.body["status"], r.body["code"], r.body["posted"]))
        r = execute(rt, "swap.fix", {"contract_id": SWP1, "date": "2026-11-02", "key": f"{SWP1}:2026-11-02"}, "test:swap.fix:trs", actor="lifecycle")
        self.assertEqual(("DONE", "NO_FIXING"), (r.body["status"], r.body["code"]))
        view = swaps.contracts_view(rt, account_id=PB, detail=True)[0]
        self.assertEqual(("TRS", "0.00", "computed", 0, "2026-12-02", 12), (view["kind"], view["mark"]["value"], view["mark"]["quality"], view["dv01_c"],
                                                                           view["next_events"][0]["date"], view["remaining_periods"]))
        det = swaps_trs.contract_detail(rt, SWP1)
        self.assertEqual(("AAPL", 10000, "339.5", 339500000, "0.0447", 0, 42155), (det["underlying_id"], det["units"], det["reset_px"], det["notional_c"],
                                                                                  det["financing_now"]["rate"], det["accrued_receivable_c"], det["accrued_payable_c"]))
        assert_integrity(self, rt, "full")


class XccyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-swaps-xccy-")
        self.rt = None

    def tearDown(self):
        if self.rt is not None:
            self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start(self, usd_dfs=USD401, ecb="2.49"):
        rt = self.rt = runtime(self.tmp, XCCY_AT)
        rt.curve.set_fixed_dfs(usd_dfs, date(2026, 5, 19))
        rt.rates.put("ECBDFR", [(date(2026, 5, 19), D(ecb))], persist=False)
        return rt

    def book(self, terms=None, request_id="ui:xccy-book-0001"):
        return book(self.rt, dict(XCCY_TERMS, **(terms or {})), request_id, kind="XCCY")

    # ---- s19.16 XCCY: quotes, booking, the start exchange, coupons, VM ----------------------------------------------------------------
    def test_xccy_19_16_quotes_booking_start_exchange_coupons_vm(self):
        rt = self.start()
        # receive USD / pay EUR 10,000,000.00, 2 years, MTM; the USD notional at the deal rate 1.1238: 10,000,000 x 1.1238 = 11,238,000.00
        p = swaps.preview(rt, PB, "XCCY", XCCY_TERMS)
        self.assertEqual(("XCCY", "1.1238", "EURUSD", 1123800000, 1000000000, "RECEIVE_USD_PAY_EUR"),
                         (p["kind"], p["deal_rate"], p["deal_pair"], p["usd_notional_c"], p["foreign_notional_c"], p["side"]))
        lr, lp = p["legs"]["receive"], p["legs"]["pay"]
        # the quotes (module docstring of this file): USD par 0.04010 - 0.00010 = 0.04000; EUR par 0.02490 + 0.00010 = 0.02500
        self.assertEqual(("0.04010", "0.04000", "0.04000", "1.9036333650", False), (lr["par_rate"], lr["quote"], lr["rate"], lr["annuity"], lr["rate_given"]))
        self.assertEqual(("0.02490", "0.02500", "0.02500", "1.9392669142"), (lp["par_rate"], lp["quote"], lp["rate"], lp["annuity"]))
        # mark0 = USD +11,238,000 x (0.04000 - 0.04010) x 1.903633365 = -1,123.80 x 1.903633365 = -2,139.3032
        #       + EUR -10,000,000 x (0.02500 - 0.02490) x 1.9392669142 x 1.1238 = -1,939.2669142 x 1.1238 = -2,179.3482  -> -4,318.6514 -> -4,318.65
        # DV01 (USD par curve, a received fixed leg loses): -11,238,000 x 0.0001 x 1.903633365 = -2,139.30; the EUR leg on its own curve (paid
        # fixed gains): +10,000,000 x 0.0001 x 1.9392669142 x 1.1238 = +2,179.35; IM = 2 % x 11,238,000.00 = 224,760.00
        self.assertEqual((-431865, 431865, -213930, 217935, 22476000), (p["mark0_c"], p["cost_of_dealing_c"], p["dv01_c"], p["dv01_foreign_c"], p["im_c"]))
        # semi-annual from Wed 2026-05-20: Fri 2026-11-20, Thu 2027-05-20, 2027-11-20 is a Saturday -> Mon 2027-11-22, 2028-05-20 a Saturday -> Mon 2028-05-22;
        # the first coupons (30/360, 180 days): USD 11,238,000 x 0.04 x 180/360 = 224,760.00, EUR 10,000,000 x 0.025 x 180/360 = 125,000.00
        self.assertEqual(["2026-11-20", "2027-05-20", "2027-11-22", "2028-05-22"], [r["date"] for r in p["schedule"]])
        self.assertEqual((22476000, "USD", 12500000, "EUR", False), (p["schedule"][0]["receive_c"], p["schedule"][0]["receive_ccy"], p["schedule"][0]["pay_c"],
                                                                     p["schedule"][0]["pay_ccy"], p["schedule"][0]["indicative"]))
        self.assertEqual((["2026-11-20", "2027-05-20", "2027-11-22"], "2028-05-22"), (p["fx_reset_dates"], p["maturity_date"]))
        # the start exchange pays USD 11,238,000.00 (lent: the receive leg) and receives EUR 10,000,000.00 (borrowed); funding USD 11,238,000 + IM
        # 224,760 = 11,462,760.00 against 0 settled -> a sweep within Treasury's 25,000,000.00
        se = p["start_exchange"]
        self.assertEqual((("USD", 1123800000, "1360"), ("EUR", 1000000000, "2360")), ((se["we_pay"]["currency"], se["we_pay"]["amount_c"], se["we_pay"]["gl"]),
                                                                                       (se["we_receive"]["currency"], se["we_receive"]["amount_c"], se["we_receive"]["gl"])))
        self.assertEqual(("USD", 1146276000, 0, 1146276000, True), (p["funding"]["currency"], p["funding"]["need_c"], p["funding"]["own_settled_c"],
                                                                   p["funding"]["sweep_c"], p["funding"]["ok"]))
        # given rates equal to the quotes deal the same; a WORSE receive rate is accepted: USD leg 11,238,000 x (0.03990 - 0.04010) x 1.903633365
        # = -4,278.6064, total -4,278.6064 - 2,179.3482 = -6,457.9545 -> -6,457.95
        self.assertEqual(-431865, swaps.preview(rt, PB, "XCCY", dict(XCCY_TERMS, recv_rate="0.04", pay_rate="0.025"))["mark0_c"])
        self.assertEqual(-645795, swaps.preview(rt, PB, "XCCY", dict(XCCY_TERMS, recv_rate="0.0399"))["mark0_c"])
        # a rate better than the dealer's quote is refused: receive 4.001 % > 4.000 %, pay 2.499 % < 2.500 % -> 400 OFF_MARKET
        for bad in ({"recv_rate": "0.04001"}, {"pay_rate": "0.02499"}):
            with self.assertRaises(RejectError) as cm:
                swaps.preview(rt, PB, "XCCY", dict(XCCY_TERMS, **bad))
            self.assertEqual("OFF_MARKET", cm.exception.code, bad)
        for bad, code in (({"pay_ccy": "EUR", "recv_ccy": "GBP"}, "CURRENCY_UNSUPPORTED"), ({"pay_ccy": "KRW"}, "CURRENCY_UNSUPPORTED"),
                          ({"pay_ccy": "USD"}, "TERMS"), ({"months": 9}, "TERMS"), ({"months": None}, "TERMS"), ({"pay_index": "EURIBOR"}, "NOT_IN_THIS_BUILD"),
                          ({"recv_notional_c": 1123800001}, "TERMS"), ({"pay_rate": 0.025}, "TERMS"), ({"pay_notional_c": None}, "TERMS"),
                          ({"pay_basis": "ACT/365"}, "TERMS")):
            with self.assertRaises(RejectError) as cm:
                swaps.preview(rt, PB, "XCCY", dict(XCCY_TERMS, **bad))
            self.assertEqual(code, cm.exception.code, bad)
        # no EUR policy rate -> 409 NO_CURVE; a stale EURUSD -> 409 QUOTE_STALE (nothing is dealt without its inputs)
        saved = rt.rates
        rt.rates = RateStore()
        with self.assertRaises(ConflictError) as cm:
            swaps.preview(rt, PB, "XCCY", XCCY_TERMS)
        self.assertEqual("NO_CURVE", cm.exception.code)
        rt.rates = saved
        rt.marks.set("EURUSD", "1.1238", "stale", rt.clock.epoch() - 3600)
        r = self.book(request_id="ui:xccy-book-stale")
        self.assertEqual((409, "QUOTE_STALE"), (r.http, r.code))
        rt.marks.set("EURUSD", "1.1238", "delayed", rt.clock.epoch())
        # booking at the quotes (rates blank)
        r = self.book()
        self.assertTrue(r.ok, r.body)
        self.assertEqual([("1330", "USD", SWP1, 22476000), ("1010", "USD", None, -22476000)], lines_by_idem(rt, f"im:{SWP1}:open"))
        # the start exchange, each leg balanced in its own currency, the EUR lines posted at the deal rate (base 10,000,000 x 1.1238 = 11,238,000.00):
        # we LEND USD (Dr 1360:USD / Cr 1010:USD 11,238,000.00) and BORROW EUR (Dr 1010:EUR / Cr 2360:EUR 10,000,000.00) - L1: base sums to 0
        start = lines_by_idem(rt, f"xccy:{SWP1}:start", base=True)
        self.assertEqual([("1360", "USD", SWP1, 1123800000, 1123800000), ("1010", "USD", None, -1123800000, -1123800000),
                          ("1010", "EUR", None, 1000000000, 1123800000), ("2360", "EUR", SWP1, -1000000000, -1123800000)], start)
        self.assertEqual(0, sum(x[4] for x in start))
        self.assertEqual([("USD", 22476000, "otc", f"im:{SWP1}:open"), ("USD", 1123800000, "xccy", f"xccy:{SWP1}:start")], sweeps(rt))
        c = contract(rt)
        self.assertEqual(("XCCY", 1123800000, "USD", -431865, "1.1238", "2026-05-20", "2028-05-22", 22476000),
                         (c["kind"], c["notional_c"], c["currency"], c["mark_c"], c["reset_px"], c["start_date"], c["maturity_date"], c["im_c"]))
        ns = one(rt, "SELECT * FROM otc_resets WHERE contract_id=? AND kind='NOTIONAL_START'", (SWP1,))
        self.assertEqual(("2026-05-20", 1123800000), (ns["event_date"], ns["amount_c"]))
        self.assertEqual((0, 1000000000, 1123800000, 1000000000), (bal(rt, PB, "1010"), bal(rt, PB, "1010", "EUR"), bal(rt, PB, "1360", "USD", SWP1),
                                                                  bal(rt, PB, "2360", "EUR", SWP1)))
        # NAV(F1:PB_GS) = 224,760.00 + 11,238,000.00 (transferred in) - 4,318.65 (the mark) = 11,458,441.35: the exchange itself is worth 0 at the deal
        # rate (EUR 10,000,000 cash = EUR 10,000,000 payable); NAV(F1:ALL) = 25,000,000.00 - 4,318.65
        self.assertEqual((1145844135, 2499568135), (rt.accounting.nav(PB).nav_c, rt.accounting.nav("F1:ALL").nav_c))
        mark_c, inputs = swaps.mark(rt, SWP1)
        self.assertEqual((-431865, 4, "1.1238"), (mark_c, inputs["remaining_periods"], inputs["fx"]["usd_per_unit"]))
        self.assertEqual(-213930, swaps.dv01_c(rt, SWP1))
        # VM day 1 (17:05) at the pair's 17:00 ET close 1.1238 (LC-3: the 14:00 print is not a close): the mark -4,318.65 against the reference 0 ->
        # VM posted Dr 1320 / Cr 1010 4,318.65 after a sweep of exactly that
        rt.marks.set("EURUSD", "1.1238", "delayed", epoch_of(date(2026, 5, 20), "17:00"))
        rt.clock.set("2026-05-20T17:10:00-04:00")
        r = vm(rt, "2026-05-20")
        self.assertEqual(("DONE", -431865, -431865), (r.body["status"], r.body["mark_c"], r.body["delta_c"]))
        self.assertEqual(("1.1238", "2026-05-20"), (r.body["inputs"]["fx"]["usd_per_unit"], r.body["inputs"]["valuation_date"]))
        self.assertEqual([("1320", "USD", SWP1, 431865), ("1010", "USD", None, -431865)], lines_by_idem(rt, f"swapvm:{SWP1}:2026-05-20"))
        # night 1 through the scheduler (ACCRUE_DAILY -> swaps.accrue_night -> this module): USD receive 11,238,000 x 0.04 / 360 = 1,248.6667 -> 1,248.67
        # Dr 1225:USD / Cr 4450; EUR pay 10,000,000 x 0.025 / 360 = 694.4444 -> 694.44 Dr 4450:EUR / Cr 2340:EUR, base at this command's rate
        # 694.44 x 1.1238 = 780.4117 -> 780.41
        out = rt.lifecycle.tick(at="2026-05-21T00:40:00-04:00", only="ACCRUE_DAILY")
        self.assertEqual([(f"swappay:{SWP1}:2026-05-20", "DONE"), (f"swaprcv:{SWP1}:2026-05-20", "DONE")],
                         sorted(x for x in ran(out, "ACCRUE_DAILY") if x[0].startswith("swap")))
        self.assertEqual([("1225", "USD", SWP1, 124867, 124867), ("4450", "USD", SWP1, -124867, -124867)], lines_by_idem(rt, f"accr:swaprcv:{SWP1}:2026-05-20", base=True))
        self.assertEqual([("4450", "EUR", SWP1, 69444, 78041), ("2340", "EUR", SWP1, -69444, -78041)], lines_by_idem(rt, f"accr:swappay:{SWP1}:2026-05-20", base=True))
        # nights 2 and 3: cumulative USD to_c(2,497.3333) = 2,497.33 (+1,248.66), to_c(3,746.00) = 3,746.00 (+1,248.67); EUR to_c(1,388.8889) = 1,388.89
        # (+694.45), to_c(2,083.3333) = 2,083.33 (+694.44)
        rt.clock.set("2026-05-23T00:40:00-04:00")
        accrue_nights(self, rt, date(2026, 5, 21), 2, ("swaprcv", "swappay"))
        self.assertEqual([124866, 124867], [lines_by_idem(rt, f"accr:swaprcv:{SWP1}:2026-05-{d}")[0][3] for d in (21, 22)])
        self.assertEqual([69445, 69444], [lines_by_idem(rt, f"accr:swappay:{SWP1}:2026-05-{d}")[0][3] for d in (21, 22)])
        self.assertEqual((374600, 208333), (bal(rt, PB, "1225", "USD", SWP1), bal(rt, PB, "2340", "EUR", SWP1)))
        # the next events: the 2026-11-20 coupons (both known), the FX reset that day, the 2027-05-20 coupons (USD unknown until its FX reset)
        ev = swaps.next_events(rt, SWP1)
        self.assertEqual([("2026-11-20", "PAY"), ("2026-11-20", "FX_RESET"), ("2027-05-20", "PAY"), ("2027-05-20", "FX_RESET")], [(e["date"], e["event"]) for e in ev])
        self.assertEqual([("receive", "USD", 22476000), ("pay", "EUR", 12500000)], [(l["side"], l["currency"], l["amount_c"]) for l in ev[0]["legs"]])
        self.assertEqual([None, 12500000], [l["amount_c"] for l in ev[2]["legs"]])
        # nothing else is due now (the start exchange happened at booking); swap.fix answers a no-op
        with rt.db.reader() as conn:
            self.assertEqual([], swaps_trs.event_keys(conn, rt.clock.epoch()))
        r = execute(rt, "swap.fix", {"contract_id": SWP1, "date": "2026-05-20", "key": f"{SWP1}:2026-05-20"}, "test:swap.fix:xccy", actor="lifecycle")
        self.assertEqual(("DONE", "NO_FIXING"), (r.body["status"], r.body["code"]))
        assert_integrity(self, rt, "full")

    # ---- s19.16 XCCY: coupons paid, the MTM FX reset (neutral), maturity --------------------------------------------------------------
    def test_xccy_19_16_payment_fx_reset_neutral_and_maturity(self):
        rt = self.start()
        # 12 months (two periods) so the whole life runs: mark0 = USD -11,238,000 x 0.0001 x 0.97070933 = -1,090.8831 + EUR -1,000 x 0.9816302516 x 1.1238
        # = -1,103.1561 -> -2,194.04; DV01 -11,238,000 x 0.0001 x 0.97070933 = -1,090.88
        r = self.book({"months": 12})
        self.assertTrue(r.ok, r.body)
        self.assertEqual((-219404, -109088, "2027-05-20"), (r.body["preview"]["mark0_c"], r.body["preview"]["dv01_c"], contract(rt)["maturity_date"]))
        # the first period's 184 nights (05-20 .. 11-19): each leg trues up to its 30/360 coupon on the last night - USD cumulative after 183 nights
        # to_c(449,520 x 183 / 360) = 228,506.00 -> -3,746.00 to land on 224,760.00 (Dr 4450 / Cr 1225); EUR to_c(250,000 x 183 / 360) = 127,083.33
        # -> -2,083.33 to land on 125,000.00 (Dr 2340 / Cr 4450)
        rt.clock.set("2026-11-20T00:35:00-05:00")
        accrue_nights(self, rt, date(2026, 5, 20), 184, ("swaprcv", "swappay"))
        self.assertEqual([("4450", "USD", SWP1, 374600), ("1225", "USD", SWP1, -374600)], lines_by_idem(rt, f"accr:swaprcv:{SWP1}:2026-11-19"))
        self.assertEqual([("2340", "EUR", SWP1, 208333), ("4450", "EUR", SWP1, -208333)], lines_by_idem(rt, f"accr:swappay:{SWP1}:2026-11-19"))
        self.assertEqual((22476000, 12500000), (bal(rt, PB, "1225", "USD", SWP1), bal(rt, PB, "2340", "EUR", SWP1)))
        # the payment of 2026-11-20 through WP-17's swap.pay (delegated here): each leg settles in its own currency - Dr 1010:USD / Cr 1225:USD
        # 224,760.00 and Dr 2340:EUR / Cr 1010:EUR 125,000.00 (from the EUR held since the start)
        rt.clock.set("2026-11-20T00:50:00-05:00")
        r = execute(rt, "swap.pay", {"contract_id": SWP1, "date": "2026-11-20", "key": f"{SWP1}:2026-11-20"}, f"job:SWAP_PAY:{SWP1}:2026-11-20", actor="lifecycle")
        self.assertTrue(r.ok and r.body["status"] == "DONE", r.body)
        self.assertEqual([("1010", "USD", None, 22476000), ("1225", "USD", SWP1, -22476000), ("2340", "EUR", SWP1, 12500000), ("1010", "EUR", None, -12500000)],
                         lines_by_idem(rt, f"xccypay:{SWP1}:2026-11-20"))
        self.assertEqual(("DONE", 22476000, 987500000), (one(rt, "SELECT status FROM jobs_done WHERE job='SWAP_PAY' AND key=?", (f"{SWP1}:2026-11-20",))["status"],
                                                         bal(rt, PB, "1010"), bal(rt, PB, "1010", "EUR")))
        with rt.db.reader() as conn:
            self.assertEqual([], swaps_trs.event_keys(conn, rt.clock.epoch()))        # PAY recorded; the FX reset is due from 17:05
        snapshot(rt, PB, date(2026, 11, 20))
        # the FX close 1.1300 (17:00 ET): the USD notional -> 10,000,000 x 1.13 = 11,300,000.00; we lent USD, so the difference 62,000.00 is PAID
        # (Dr 1360:USD / Cr 1010:USD), from the 224,760.00 coupon cash (no sweep); NAV does not move with the reset itself
        rt.marks.set("EURUSD", "1.13", "delayed", epoch_of(date(2026, 11, 20), "17:00"))
        rt.clock.set("2026-11-20T17:10:00-05:00")
        nav_before = rt.accounting.nav(PB).nav_c
        out = rt.lifecycle.tick(only="XCCY_EVENTS")
        self.assertEqual([(f"{SWP1}:2026-11-20:FX_RESET", "DONE")], ran(out, "XCCY_EVENTS"))
        self.assertEqual([("1360", "USD", SWP1, 6200000), ("1010", "USD", None, -6200000)], lines_by_idem(rt, f"xccyfx:{SWP1}:2026-11-20"))
        self.assertEqual((1130000000, "1.13", 16276000), (contract(rt)["notional_c"], contract(rt)["reset_px"], bal(rt, PB, "1010")))
        self.assertEqual(nav_before, rt.accounting.nav(PB).nav_c)
        # neutral: at the reset rate the notionals are worth the same - 1360:USD 11,300,000.00 vs 2360:EUR 10,000,000 x 1.13 = 11,300,000.00
        self.assertEqual(bal(rt, PB, "1360", "USD", SWP1), fx_c(bal(rt, PB, "2360", "EUR", SWP1), "1.13"))
        # P&L since the snapshot: the net EUR position after the coupon (9,875,000 cash - 10,000,000 payable = -125,000) x (1.1300 - 1.1238) = -775.00,
        # all FX result; the reset moved cash into the receivable; residual 0
        pl = rt.accounting.pnl(PB, "TODAY")
        self.assertEqual((-77500, -77500, 0, 0), (pl.pnl_c, pl.components["fx_result_c"], pl.flows_c, pl.residual_c))
        assert_integrity(self, rt, "full")
        # the next night accrues on the re-struck notional: 11,300,000 x 0.04 / 360 = 1,255.5556 -> 1,255.56
        rt.clock.set("2026-11-21T00:40:00-05:00")
        self.assertEqual(125556, accrue(rt, "swaprcv", "2026-11-20").body["amount_c"])
        self.assertEqual(69444, accrue(rt, "swappay", "2026-11-20").body["amount_c"])
        # the second period's remaining 180 nights (11-21 .. 2027-05-19): after 180 of its 181 nights both legs sit exactly on their 30/360 coupons
        # (USD to_c(452,000 x 180 / 360) = 226,000.00, EUR 125,000.00), so the last night posts nothing
        rt.clock.set("2027-05-20T00:35:00-04:00")
        accrue_nights(self, rt, date(2026, 11, 21), 180, ("swaprcv", "swappay"))
        self.assertIsNone(entry_by_idem(rt, f"accr:swaprcv:{SWP1}:2027-05-19"))
        self.assertEqual((22600000, 12500000), (bal(rt, PB, "1225", "USD", SWP1), bal(rt, PB, "2340", "EUR", SWP1)))
        # the EUR to repay: 10,000,000 borrowed - 2 x 125,000 coupons paid from it = 9,750,000 held -> a 250,000.00 EUR capital deposit covers the rest
        self.assertTrue(deposit(rt, 25000000, "EUR", lifetime="F1", request_id="ui:eur-deposit-0001").ok)
        # maturity (END, 00:45 ET): the last coupons - USD 11,300,000 x 0.04 x 180/360 = 226,000.00, EUR 125,000.00 - then the reverse exchange
        # (receive back USD 11,300,000.00, repay EUR 10,000,000.00 after a 250,000.00 EUR sweep), then the IM 224,760.00 released
        rt.clock.set("2027-05-20T00:50:00-04:00")
        with rt.db.reader() as conn:
            self.assertEqual([(SWP1, "2027-05-20", "END")], swaps_trs.event_keys(conn, rt.clock.epoch()))
        out = rt.lifecycle.tick(only="XCCY_EVENTS")
        self.assertEqual([(f"{SWP1}:2027-05-20:END", "DONE")], ran(out, "XCCY_EVENTS"))
        self.assertEqual([("1010", "USD", None, 22600000), ("1225", "USD", SWP1, -22600000), ("2340", "EUR", SWP1, 12500000), ("1010", "EUR", None, -12500000)],
                         lines_by_idem(rt, f"xccypay:{SWP1}:2027-05-20"))
        self.assertEqual([("1010", "USD", None, 1130000000), ("1360", "USD", SWP1, -1130000000), ("2360", "EUR", SWP1, 1000000000), ("1010", "EUR", None, -1000000000)],
                         lines_by_idem(rt, f"xccy:{SWP1}:end"))
        self.assertEqual([("1010", "USD", None, 22476000), ("1330", "USD", SWP1, -22476000)], lines_by_idem(rt, f"im:{SWP1}:release"))
        self.assertEqual(("EUR", 25000000, "xccy", f"xccy:{SWP1}:end"), sweeps(rt)[-1])
        # USD cash 162,760.00 + 226,000.00 + 11,300,000.00 + 224,760.00 = 11,913,520.00; EUR 0; every contract balance 0; MATURED; nothing due
        self.assertEqual((1191352000, 0), (bal(rt, PB, "1010"), bal(rt, PB, "1010", "EUR")))
        self.assertEqual((0, 0, 0, 0, 0), (bal(rt, PB, "1225", "USD", SWP1), bal(rt, PB, "2340", "EUR", SWP1), bal(rt, PB, "1360", "USD", SWP1),
                                           bal(rt, PB, "2360", "EUR", SWP1), bal(rt, PB, "1330", "USD", SWP1)))
        self.assertEqual("MATURED", contract(rt)["status"])
        with rt.db.reader() as conn:
            self.assertEqual([], swaps_trs.event_keys(conn, rt.clock.epoch()))
        self.assertEqual([], q(rt, "SELECT 1 FROM attention WHERE code='FUNDING_SHORTFALL' AND resolved_ts IS NULL"))
        assert_integrity(self, rt, "full")

    # ---- the design's start / FX-reset lines: receive EUR / pay USD ------------------------------------------------------------------
    def test_xccy_receive_eur_pay_usd_design_lines_and_eur_funding(self):
        rt = self.start(usd_dfs=USD399, ecb="2.51")
        terms = {"pay_ccy": "USD", "recv_ccy": "EUR", "pay_notional_c": None, "recv_notional_c": 1000000000, "months": 12}
        # the EUR leg is lent: with no EUR anywhere the start exchange is refused 409 INSUFFICIENT_FUNDS_CCY with the Convert hint (never automatic
        # FX) and nothing is booked
        r = self.book(terms, request_id="ui:xccy-book-noeur")
        self.assertEqual((409, "INSUFFICIENT_FUNDS_CCY"), (r.http, r.code))
        self.assertEqual("buy EUR 10,000,000.00 with Convert", r.body["error"]["details"]["hint"])
        self.assertEqual(([], []), (q(rt, "SELECT 1 FROM otc_contracts"), q(rt, "SELECT 1 FROM journal_entries WHERE idem_key LIKE 'im:%'")))
        # F1:TREASURY holds EUR 10,000,000.00 (a EUR capital deposit, base 10,000,000 x 1.1238 = 11,238,000.00)
        self.assertTrue(deposit(rt, 1000000000, "EUR", lifetime="F1", request_id="ui:eur-deposit-0002").ok)
        r = self.book(terms)
        self.assertTrue(r.ok, r.body)
        # quotes: EUR receive 0.02510 - 0.00010 = 0.02500; USD pay 0.03990 + 0.00010 = 0.04000; mark0 = USD -11,238,000 x 0.0001 x 0.97085162 = -1,091.0431
        # + EUR -10,000,000 x 0.0001 x 0.9814851367 x 1.1238 = -1,102.9930 -> -2,194.04; DV01 (USD paid fixed gains) +1,091.04; EUR leg -1,102.99
        pv = r.body["preview"]
        self.assertEqual(("0.02500", "0.04000", -219404, 109104, -110299, "RECEIVE_EUR_PAY_USD"),
                         (pv["legs"]["receive"]["rate"], pv["legs"]["pay"]["rate"], pv["mark0_c"], pv["dv01_c"], pv["dv01_foreign_c"], pv["side"]))
        # s19.16's start lines: Dr 1360:EUR 10,000,000.00 / Cr 1010:EUR (EUR swept in from Treasury's EUR) and Dr 1010:USD 11,238,000.00 / Cr 2360:USD
        self.assertEqual([("1360", "EUR", SWP1, 1000000000, 1123800000), ("1010", "EUR", None, -1000000000, -1123800000),
                          ("1010", "USD", None, 1123800000, 1123800000), ("2360", "USD", SWP1, -1123800000, -1123800000)],
                         lines_by_idem(rt, f"xccy:{SWP1}:start", base=True))
        self.assertEqual([("USD", 22476000, "otc", f"im:{SWP1}:open"), ("EUR", 1000000000, "xccy", f"xccy:{SWP1}:start")], sweeps(rt))
        # night 1: EUR receive 694.44 Dr 1225:EUR / Cr 4450; USD pay 1,248.67 Dr 4450 / Cr 2340:USD
        rt.clock.set("2026-05-21T00:40:00-04:00")
        accrue_nights(self, rt, date(2026, 5, 20), 1, ("swaprcv", "swappay"))
        self.assertEqual([("1225", "EUR", SWP1, 69444), ("4450", "EUR", SWP1, -69444)], lines_by_idem(rt, f"accr:swaprcv:{SWP1}:2026-05-20"))
        self.assertEqual([("4450", "USD", SWP1, 124867), ("2340", "USD", SWP1, -124867)], lines_by_idem(rt, f"accr:swappay:{SWP1}:2026-05-20"))
        # the FX reset at the 1.1300 close (xccyfx:SWP-000001:2026-11-20): USD notional -> 11,300,000.00; USD is the borrowed leg, so the 62,000.00
        # difference is RECEIVED - Dr 1010:USD 62,000.00 / Cr 2360:USD 62,000.00, s19.16's line
        rt.marks.set("EURUSD", "1.13", "delayed", epoch_of(date(2026, 11, 20), "17:00"))
        rt.clock.set("2026-11-20T17:10:00-05:00")
        r = event(rt, "2026-11-20", "FX_RESET")
        self.assertTrue(r.ok and r.body["status"] == "DONE", r.body)
        self.assertEqual((6200000, 6200000, 1123800000, 1130000000), (r.body["diff_c"], r.body["cash_c"], r.body["old_usd_notional_c"], r.body["usd_notional_c"]))
        self.assertEqual([("1010", "USD", None, 6200000), ("2360", "USD", SWP1, -6200000)], lines_by_idem(rt, f"xccyfx:{SWP1}:2026-11-20"))
        # neutral: 1360:EUR 10,000,000 x 1.13 = 11,300,000.00 = 2360:USD
        self.assertEqual(fx_c(bal(rt, PB, "1360", "EUR", SWP1), "1.13"), bal(rt, PB, "2360", "USD", SWP1))
        self.assertEqual(1130000000, bal(rt, PB, "2360", "USD", SWP1))
        # a retry answers "already"; an FX-reset key on a date that does not start a period is refused
        r = event(rt, "2026-11-20", "FX_RESET", rid="test:fx-retry")
        self.assertTrue(r.ok and r.body["already"], r.body)
        r = event(rt, "2026-11-19", "FX_RESET")
        self.assertEqual((400, "KEY_INVALID"), (r.http, r.code))
        assert_integrity(self, rt, "full")

    # ---- a delayed start exchange (weekend booking) that first cannot be funded --------------------------------------------------------
    def test_xccy_delayed_start_waits_for_funding_never_converts(self):
        rt = self.start(usd_dfs=USD399, ecb="2.51")
        # booked Saturday 2026-05-23: the start rolls FOLLOWING past Sunday and Memorial Day (Mon 05-25) to Tue 2026-05-26; only the IM moves now
        rt.clock.set("2026-05-23T10:00:00-04:00")
        terms = {"pay_ccy": "USD", "recv_ccy": "EUR", "pay_notional_c": None, "recv_notional_c": 1000000000, "months": 12}
        r = self.book(terms)
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("2026-05-26", None), (contract(rt)["start_date"], r.body["start_exchange"]))
        self.assertEqual([("USD", 22476000, "otc", f"im:{SWP1}:open")], sweeps(rt))
        with rt.db.reader() as conn:
            self.assertEqual([], swaps_trs.event_keys(conn, rt.clock.epoch()))
        # Tue 00:50: START is due; the lent EUR 10,000,000.00 is nowhere -> SKIPPED INSUFFICIENT_FUNDS_CCY with an ERROR attention, nothing posted
        rt.clock.set("2026-05-26T00:50:00-04:00")
        with rt.db.reader() as conn:
            self.assertEqual([(SWP1, "2026-05-26", "START")], swaps_trs.event_keys(conn, rt.clock.epoch()))
        out = rt.lifecycle.tick(only="XCCY_EVENTS")
        self.assertEqual([(f"{SWP1}:2026-05-26:START", "SKIPPED")], ran(out, "XCCY_EVENTS"))
        att = q(rt, "SELECT severity, code FROM attention WHERE ref_id=? AND resolved_ts IS NULL", (f"{SWP1}:2026-05-26:START",))
        self.assertEqual([{"severity": "ERROR", "code": "INSUFFICIENT_FUNDS_CCY"}], att)
        self.assertIsNone(entry_by_idem(rt, f"xccy:{SWP1}:start"))
        # nothing accrues before the start exchange: the night of 05-26 is SKIPPED START_PENDING
        rt.clock.set("2026-05-27T00:40:00-04:00")
        r = accrue(rt, "swaprcv", "2026-05-26")
        self.assertEqual(("SKIPPED", "START_PENDING"), (r.body["status"], r.body["code"]))
        # a EUR capital deposit funds it (never an automatic conversion): the retry posts s19.16's start lines, business date the start date
        self.assertTrue(deposit(rt, 1000000000, "EUR", lifetime="F1", request_id="ui:eur-deposit-0004").ok)
        r = event(rt, "2026-05-26", "START")
        self.assertTrue(r.ok and r.body["status"] == "DONE", r.body)
        e = entry_by_idem(rt, f"xccy:{SWP1}:start")
        self.assertEqual(([("1360", "EUR", SWP1, 1000000000), ("1010", "EUR", None, -1000000000), ("1010", "USD", None, 1123800000), ("2360", "USD", SWP1, -1123800000)],
                          "2026-05-26"), (lines_of(rt, e["entry_id"]), e["business_date"]))
        self.assertEqual([], q(rt, "SELECT 1 FROM attention WHERE code='INSUFFICIENT_FUNDS_CCY' AND resolved_ts IS NULL"))
        self.assertEqual(69444, accrue(rt, "swaprcv", "2026-05-26", suffix=":r1").body["amount_c"])
        with rt.db.reader() as conn:
            self.assertEqual([], swaps_trs.event_keys(conn, rt.clock.epoch()))
        assert_integrity(self, rt, "full")

    # ---- termination ---------------------------------------------------------------------------------------------------------------
    def test_xccy_terminate_settles_accrued_and_re_exchanges_notionals(self):
        rt = self.start()
        self.assertTrue(self.book().ok)
        rt.marks.set("EURUSD", "1.1238", "delayed", epoch_of(date(2026, 5, 20), "17:00"))      # the 17:00 ET close (LC-3)
        rt.clock.set("2026-05-20T17:10:00-04:00")
        self.assertEqual(-431865, vm(rt, "2026-05-20").body["delta_c"])
        rt.clock.set("2026-05-23T00:40:00-04:00")
        accrue_nights(self, rt, date(2026, 5, 20), 3, ("swaprcv", "swappay"))
        # Saturday 05-23: the value is the model mark on unchanged inputs, -4,318.65 (4 periods remain); the termination repays EUR 10,000,000.00 plus the
        # accrued 2,083.33 but the account holds only the EUR 10,000,000.00 it borrowed -> 409 INSUFFICIENT_FUNDS_CCY (buy EUR 2,083.33), nothing written
        rt.clock.set("2026-05-23T10:00:00-04:00")
        r = execute(rt, "otc.terminate", {"contract_id": SWP1}, "ui:xccy-term-0001")
        self.assertEqual((409, "INSUFFICIENT_FUNDS_CCY", "buy EUR 2,083.33 with Convert"), (r.http, r.code, r.body["error"]["details"]["hint"]))
        self.assertEqual("OPEN", contract(rt)["status"])
        self.assertTrue(deposit(rt, 1000000, "EUR", lifetime="F1", request_id="ui:eur-deposit-0003").ok)
        r = execute(rt, "otc.terminate", {"contract_id": SWP1}, "ui:xccy-term-0002")
        self.assertTrue(r.ok, r.body)
        # the exchange entry: accrued USD 3,746.00 received, USD 11,238,000.00 lent back, accrued EUR 2,083.33 paid, EUR 10,000,000.00 repaid
        self.assertEqual([("1010", "USD", None, 374600), ("1225", "USD", SWP1, -374600), ("1010", "USD", None, 1123800000), ("1360", "USD", SWP1, -1123800000),
                          ("2340", "EUR", SWP1, 208333), ("1010", "EUR", None, -208333), ("2360", "EUR", SWP1, 1000000000), ("1010", "EUR", None, -1000000000)],
                         lines_by_idem(rt, f"xccy:{SWP1}:terminate"))
        # the close: the VM posted (4,318.65) comes back and the value -4,318.65 is paid - Dr 1010 / Cr 1320 and Dr 4450 / Cr 1010 4,318.65; IM released
        self.assertEqual([("1010", "USD", None, 431865), ("1320", "USD", SWP1, -431865), ("4450", "USD", SWP1, 431865), ("1010", "USD", None, -431865)],
                         lines_by_idem(rt, f"otc:{SWP1}:terminate"))
        self.assertEqual((-431865, -431865, 0, 22476000, "TERMINATED"), (r.body["termination_value_c"], r.body["result_c"], r.body["cash_c"], r.body["im_released_c"],
                                                                         r.body["contract"]["status"]))
        self.assertEqual(("EUR", 208333, "xccy", f"xccy:{SWP1}:terminate"), sweeps(rt)[-1])
        # 4450 USD = 3,746.00 accrued - 4,318.65 value = -572.65; 4450 EUR = -2,083.33; every contract balance 0 (L22)
        self.assertEqual((-57265, -208333), (bal(rt, PB, "4450", "USD", SWP1), bal(rt, PB, "4450", "EUR", SWP1)))
        self.assertEqual((0, 0, 0, 0, 0, 0), (bal(rt, PB, "1225", "USD", SWP1), bal(rt, PB, "2340", "EUR", SWP1), bal(rt, PB, "1360", "USD", SWP1),
                                              bal(rt, PB, "2360", "EUR", SWP1), bal(rt, PB, "1320", "USD", SWP1), bal(rt, PB, "1330", "USD", SWP1)))
        ne = one(rt, "SELECT * FROM otc_resets WHERE contract_id=? AND kind='NOTIONAL_END'", (SWP1,))
        self.assertEqual(("2026-05-23", True), (ne["event_date"], json.loads(ne["detail_json"])["terminated"]))
        with rt.db.reader() as conn:
            self.assertEqual([], swaps_trs.event_keys(conn, rt.clock.epoch()))
        assert_integrity(self, rt, "full")


if __name__ == "__main__":
    unittest.main()
