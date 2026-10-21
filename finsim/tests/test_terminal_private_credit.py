"""WP-21 private-credit tests (TERMINAL_DESIGN.md §13.14, §19.18, §11.2 PC_PAY, §11.3, §8.6 pc_model, §5.4; TERMINAL_WORKPLAN.md WP-21).
Every expected number is worked by hand in a comment from the inputs; nothing is pasted from the code's output.

Scaffolding: ``make_runtime(books="real")`` (P1 / F1 / I1 as §19.14; F1 = 25,000,000.00), the private-credit handlers, a
``RateStore`` with SOFR 3.87 % (observed 2026-10-19 and, where a scenario spans months, every business day after it) and
the FRED HY OAS ``BAMLH0A0HYM2`` 3.00 %, WP-08's lifecycle handlers + scheduler bound as ``rt.lifecycle`` (what the
integrator does).  Nights are driven either by ``tick(only="ACCRUE_DAILY")`` (the real key derivation) or by the same
``lifecycle.accrue`` command the scheduler issues per key.  No network, FakeClock, FixedMarks (the loan's mark comes from
``services.private_credit.pc_mark`` through the accounting fallback), a temp database; nothing touches ``~/.finsim``."""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tests.terminal_helpers import assert_integrity, bal, execute, make_runtime   # noqa: E402
from finsim.terminal import lifecycle   # noqa: E402
from finsim.terminal.clock import NY   # noqa: E402
from finsim.terminal.commands import Command   # noqa: E402
from finsim.terminal.products import private_credit as pc   # noqa: E402
from finsim.terminal.quotes.rates import RateStore   # noqa: E402

F1_CREDIT = "F1:CREDIT"
F1_TREASURY = "F1:TREASURY"
LOAN = "PC-000001"
STATIC = os.path.join(ROOT, "finsim", "terminal", "static")
# §19.18: Testco Holdings, USD 5,000,000.00 at 98.00, SOFR + 6.00 %, 36 months, quarterly
TESTCO = {"account_id": F1_CREDIT, "borrower": "Testco Holdings", "currency": "USD", "principal": "5000000.00", "issue_price": "98.00", "index": "SOFR",
          "spread": "0.06", "term_months": 36, "payment_months": 3}


# ---- fixture ------------------------------------------------------------------------------------------------------------------------
def business_days(cal, start: date, end: date):
    d = start
    while d <= end:
        if cal.is_business_day(d):
            yield d
        d += timedelta(days=1)


def runtime(tmp, at="2026-10-20T11:00:00-04:00", sofr_through=None, oas="3.00"):
    """make_runtime(real books) + the pc.* handlers, a RateStore (SOFR 3.87 % on 2026-10-19 and, when ``sofr_through`` is
    given, on every NYSE business day up to it — FRED publishes SOFR each business day — plus the HY OAS), the lifecycle."""
    rt = make_runtime(tmp, at=at, books="real")
    pc.register(rt.bus)
    rt.private_credit = pc
    rt.rates = RateStore()
    obs = [(date(2026, 10, 19), Decimal("3.87"))]
    if sofr_through is not None:
        obs += [(d, Decimal("3.87")) for d in business_days(rt.cal, date(2026, 10, 20), sofr_through)]
    rt.rates.put("SOFR", obs, persist=False)
    if oas is not None:
        rt.rates.put(pc.OAS_SERIES, [(date(2026, 10, 19), Decimal(oas))], persist=False)
    lifecycle.register(rt.bus, rt)
    rt.lifecycle = lifecycle.LifecycleScheduler(rt.bus, rt.db, rt, rt.clock, rt.log)
    return rt


def at(d: date, hhmm: str = "00:35") -> datetime:
    hh, mm = hhmm.split(":")
    return datetime.combine(d, time(int(hh), int(mm)), tzinfo=NY)


def commit(rt, request_id=None, **over):
    return execute(rt, "pc.commit", dict(TESTCO, **over), request_id)


def accrue(rt, loan_id, night, kind):
    """The command the scheduler issues for one ACCRUE_DAILY key (``lifecycle.accrue``, request id ``job:ACCRUE_DAILY:{key}``)."""
    key = f"{kind}:{loan_id}:{night}"
    return rt.bus.execute(Command("lifecycle.accrue", {"key": key, "kind": kind, "ref": loan_id, "night": night}, f"job:ACCRUE_DAILY:{key}", "lifecycle"))


def accrue_nights(tc, rt, loan_id, first: date, last: date):
    """Every night first..last (both kinds, date order) with the clock at 00:35 of the day after ``last``; returns the
    coupon amounts posted, in order."""
    rt.clock.set(at(last + timedelta(days=1)))
    coupons = []
    d = first
    while d <= last:
        for kind in (pc.KIND_CPN, pc.KIND_OID):
            r = accrue(rt, loan_id, d.isoformat(), kind)
            tc.assertTrue(r.ok, r.body)
            tc.assertEqual("DONE", r.body["status"], r.body)
            if kind == pc.KIND_CPN:
                coupons.append(int(r.body["amount_c"]))
        d += timedelta(days=1)
    return coupons


def q(rt, sql, params=()):
    with rt.db.reader() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(rt, sql, params=()):
    rows = q(rt, sql, params)
    return rows[0] if rows else None


def lines_of(rt, entry_id):
    return [(r["gl"], r["instrument_id"], r["amount_c"]) for r in q(rt, "SELECT gl, instrument_id, amount_c FROM journal_lines WHERE entry_id=? ORDER BY line_no", (entry_id,))]


def pay_keys(rt):
    with rt.db.reader() as conn:
        return pc.pay_keys(conn, rt.clock.epoch())


def loan_pos(rt, account=F1_CREDIT):
    return [p for p in rt.accounting.positions(account) if p.kind == "PRIVATE_LOAN"]


class PrivateCreditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-pc-")
        self.rt = None

    def tearDown(self):
        if self.rt is not None:
            self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- §19.18 end to end --------------------------------------------------------------------------------------------------------------
    def test_19_18_commit_accrue_pay_fee_mark_repay(self):
        rt = self.rt = runtime(self.tmp, sofr_through=date(2027, 1, 21))
        # commit Tue 2026-10-20 11:00: cash out 5,000,000 × 98 / 100 = 4,900,000.00 (Dr 1130 PC-000001 / Cr 1010), swept from F1:TREASURY
        r = commit(rt, "ui:pc-commit-1")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((LOAN, 490000000, "sweep", 490000000), (r.body["loan_id"], r.body["cash_out_c"], r.body["funding"]["source"], r.body["funding"]["sweep_c"]))
        self.assertEqual([("1130", LOAN, 490000000), ("1010", None, -490000000)], lines_of(rt, r.body["entry_id"]))
        # F1:TREASURY 25,000,000.00 − 4,900,000.00 = 20,100,000.00; F1:CREDIT settled 0 (the sweep covered exactly the cash out)
        self.assertEqual((2010000000, 0, 490000000), (bal(rt, F1_TREASURY, "1010"), bal(rt, F1_CREDIT, "1010"), bal(rt, F1_CREDIT, "1130", "USD", LOAN)))
        loan = r.body["loan"]
        # maturity 2026-10-20 + 36 months = 2029-10-20 (a Saturday, kept unadjusted: 365 + 366 + 365 = 1,096 nights); first payment 2027-01-20 (Wed)
        # the spread is stored in the terminal's canonical qprice form ("0.06"), the issue price with two decimals ("98.00")
        self.assertEqual(("2029-10-20", "2027-01-20", 1096, "OPEN", "2026-10-20", 10000000, "0.06", "98.00"),
                         (loan["maturity_date"], loan["next_payment_date"], loan["nights_total"], loan["status"], loan["start_date"], loan["oid_total_c"], loan["spread"], loan["issue_price"]))
        self.assertEqual(("3.00", Decimal("3")), (loan["oas_issue"], Decimal(loan["oas_issue"])))
        self.assertEqual("2026-10-19", loan["oas_issue_as_of"])
        # the mark with the OAS unchanged = amortised cost 4,900,000.00 (pc_model, "spread unchanged")
        self.assertEqual((490000000, "computed", "pc_model", 0), (loan["mark_c"], loan["mark"]["quality"], loan["mark"]["method"], loan["unrealised_c"]))
        self.assertIn("spread unchanged", loan["mark"]["note"])
        # schedule: 12 quarterly dates; the first covers 92 nights (Oct 20-31 = 12, Nov 30, Dec 31, Jan 1-19 = 19) and projects
        # to_c(5,000,000 × 0.0987 × 92 / 360) = to_c(126,116.6667) = 126,116.67 at the current print; the last is the maturity, paid Mon 2029-10-22
        sched = loan["schedule"]
        self.assertEqual(12, len(sched))
        self.assertEqual(("2027-01-20", "2027-01-20", 92, 12611667, False), (sched[0]["date"], sched[0]["anchor"], sched[0]["nights"], sched[0]["projected_coupon_c"], sched[0]["final"]))
        self.assertEqual(("2029-10-22", "2029-10-20", True), (sched[-1]["date"], sched[-1]["anchor"], sched[-1]["final"]))
        inst = rt.instruments.get(LOAN)
        self.assertEqual(("PRIVATE", "COMPUTED", LOAN, "USD"), (inst.kind, inst.support, inst.spec.get("loan_id"), inst.currency))
        self.assertEqual([("PC_COMMITTED", F1_CREDIT, LOAN)], [(e["type"], e["account_id"], e["ref_id"]) for e in q(rt, "SELECT type, account_id, ref_id FROM events WHERE type LIKE 'PC_%'")])
        assert_integrity(self, rt, "full")                                                                                  # L24: 1130 = 4,900,000 + 0

        # nights 1-3 through the real ACCRUE_DAILY key derivation (Fri 10-23 00:35 -> nights 10-20, 10-21, 10-22):
        # coupon 5,000,000 × 0.0987 / 360 = 1,370.8333 -> 1,370.83; night 2 cum to_c(2,741.6667) = 2,741.67 -> +1,370.84; night 3 cum 4,112.50 -> +1,370.83
        # OID 100,000 × n / 1,096: 91.2409 -> 91.24; 182.4818 -> 182.48 (+91.24); 273.7226 -> 273.72 (+91.24)
        out = rt.lifecycle.tick(at="2026-10-23T00:35:00-04:00", only="ACCRUE_DAILY")
        ran = {i["key"]: i for i in out["ran"]}
        for d in ("2026-10-20", "2026-10-21", "2026-10-22"):
            self.assertEqual("DONE", ran[f"pccpn:{LOAN}:{d}"]["status"], ran[f"pccpn:{LOAN}:{d}"])
            self.assertEqual("DONE", ran[f"pcoid:{LOAN}:{d}"]["status"], ran[f"pcoid:{LOAN}:{d}"])
        cpn = [r["amount_c"] for r in q(rt, "SELECT l.amount_c FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id"
                                           " WHERE e.idem_key LIKE ? AND l.gl='1220' ORDER BY e.idem_key", (f"accr:pccpn:{LOAN}:%",))]
        oid = [r["amount_c"] for r in q(rt, "SELECT l.amount_c FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id"
                                           " WHERE e.idem_key LIKE ? AND l.gl='1130' ORDER BY e.idem_key", (f"accr:pcoid:{LOAN}:%",))]
        self.assertEqual(([137083, 137084, 137083], [9124, 9124, 9124]), (cpn, oid))
        # 1220 = 4,112.50; 1130 = 4,900,000.00 + 273.72; 4300 (loan id) = 4,112.50 + 273.72 = 4,386.22
        self.assertEqual((411250, 490027372, 438622), (bal(rt, F1_CREDIT, "1220", "USD", LOAN), bal(rt, F1_CREDIT, "1130", "USD", LOAN), bal(rt, F1_CREDIT, "4300", "USD", LOAN)))
        self.assertEqual(("2026-10-20", "0.0987", 411250, "2026-10-22"), tuple(one(rt, "SELECT segment_start, rate, cum_c, accrued_through FROM accrual_state WHERE kind='pccpn' AND ref_id=?", (LOAN,)).values()))
        self.assertEqual(27372, one(rt, "SELECT cum_c FROM accrual_state WHERE kind='pcoid' AND ref_id=?", (LOAN,))["cum_c"])
        assert_integrity(self, rt, "full")                                                                                  # L24: 4,900,000 + 273.72
        # a replayed night posts nothing twice (the same key, the same request id -> the stored result)
        rep = accrue(rt, LOAN, "2026-10-22", pc.KIND_CPN)
        self.assertTrue(rep.replayed, rep.body)
        self.assertEqual(411250, bal(rt, F1_CREDIT, "1220", "USD", LOAN))
        # nothing is due to PC_PAY before 2027-01-20
        self.assertEqual([], pay_keys(rt))

        # nights 4-92 (10-23 .. 2027-01-19) with the same SOFR print every business day (the segment never restarts):
        # coupon cum to_c(5,000,000 × 0.0987 × 92 / 360) = to_c(126,116.6667) = 126,116.67; OID cum to_c(100,000 × 92 / 1,096) = to_c(8,394.1606) = 8,394.16
        accrue_nights(self, rt, LOAN, date(2026, 10, 23), date(2027, 1, 19))
        self.assertEqual((12611667, 490839416), (bal(rt, F1_CREDIT, "1220", "USD", LOAN), bal(rt, F1_CREDIT, "1130", "USD", LOAN)))
        assert_integrity(self, rt, "full")                                                                                  # L24: 4,900,000 + 8,394.16 = 4,908,394.16

        # PC_PAY 2027-01-20 00:45: the key is due (coupon accrued through 01-19), pays Dr 1010 / Cr 1220 126,116.67; next payment 2027-04-20 (Tue)
        rt.clock.set(at(date(2027, 1, 20), "00:45"))
        self.assertEqual([(LOAN, "2027-01-20")], pay_keys(rt))
        out = rt.lifecycle.tick(only="PC_PAY")
        self.assertEqual([("PC_PAY", f"{LOAN}:2027-01-20", "DONE", "pc.pay")], [(i["job"], i["key"], i["status"], i["kind"]) for i in out["ran"]])
        pay_entry = one(rt, "SELECT entry_id FROM journal_entries WHERE idem_key=?", (f"pcpay:{LOAN}:2027-01-20",))["entry_id"]
        self.assertEqual([("1010", None, 12611667), ("1220", LOAN, -12611667)], lines_of(rt, pay_entry))
        self.assertEqual((0, 12611667, 490839416, "2027-04-20"), (bal(rt, F1_CREDIT, "1220", "USD", LOAN), bal(rt, F1_CREDIT, "1010"), bal(rt, F1_CREDIT, "1130", "USD", LOAN),
                                                                  one(rt, "SELECT next_payment_date FROM private_loans WHERE loan_id=?", (LOAN,))["next_payment_date"]))
        self.assertEqual("DONE", one(rt, "SELECT status FROM jobs_done WHERE job='PC_PAY' AND key=?", (f"{LOAN}:2027-01-20",))["status"])
        self.assertEqual(([], []), (rt.lifecycle.tick(only="PC_PAY")["ran"], pay_keys(rt)))                                # once, never twice
        assert_integrity(self, rt, "full")                                                                                  # L24 holds after the payment

        # amendment fee 25,000.00 -> Dr 1010 / Cr 4320 (owner-recorded income)
        r = execute(rt, "pc.amend_fee", {"loan_id": LOAN, "amount": "25000.00", "memo": "amendment"}, "ui:pc-fee-1")
        self.assertTrue(r.ok, r.body)
        self.assertEqual([("1010", None, 2500000), ("4320", LOAN, -2500000)], lines_of(rt, r.body["entries"][0]))
        self.assertEqual((15111667, 2500000), (bal(rt, F1_CREDIT, "1010"), bal(rt, F1_CREDIT, "4320", "USD", LOAN)))

        # OAS now 4.00 % (ΔOAS +1.00 %), 1,004 days remaining (2029-10-20 − 2027-01-20): T = 1,004 / 365;
        # 4,908,394.16 × 0.01 × 1,004 / 365 = 49,280,277.3664 / 365 = 135,014.4585 -> mark to_c(4,908,394.16 − 135,014.4585) = 4,773,379.70
        rt.rates.put(pc.OAS_SERIES, [(date(2027, 1, 19), Decimal("4.00"))], persist=False)
        row = one(rt, "SELECT * FROM private_loans WHERE loan_id=?", (LOAN,))
        mark_c, inputs = pc.pc_mark(rt, row)
        self.assertEqual((477337970, 1004, "4.00", "2027-01-19", "3.00", "2026-10-19", "1.00"), (mark_c, inputs["days_remaining"], inputs["oas_now_pct"], inputs["oas_now_as_of"],
                                                                                           inputs["oas_issue_pct"], inputs["oas_issue_as_of"], inputs["delta_oas_pct"]))
        self.assertIn("+1.00 %, 1,004 days remaining", inputs["note"])
        # the position (through the accounting fallback to pc_mark): unrealised 4,773,379.70 − 4,908,394.16 = −135,014.46; qty = the face in whole USD (core §6.2)
        p = loan_pos(rt)
        self.assertEqual(1, len(p))
        self.assertEqual((490839416, 477337970, -13501446, "computed", "pc_model", 5000000), (p[0].cost_c, p[0].mv_c, p[0].unrealised_c, p[0].mark.quality, p[0].mark.method, p[0].qty))
        # P&L ITD of F1:CREDIT after the fee = income (126,116.67 + 8,394.16 + 25,000.00 = 159,510.83) + unrealised −135,014.46; residual 0
        pnl = rt.accounting.pnl(F1_CREDIT, "ITD")
        self.assertEqual((15951083, -13501446, 0, 0, 2449637), (pnl.components["income_c"], pnl.components["unrealised_c"], pnl.components["realised_c"], pnl.residual_c, pnl.pnl_c))
        view = pc.loans_view(rt, F1_CREDIT)[0]
        self.assertEqual((490839416, 0, 12611667, 2500000, 13451083, 839416, 477337970, -13501446, 1004, 92, "2027-01-19"),
                         (view["amortised_cost_c"], view["accrued_c"], view["coupon_paid_c"], view["fees_c"], view["income_c"], view["oid_accreted_c"], view["mark_c"],
                          view["unrealised_c"], view["days_remaining"], view["nights_accrued"], view["accrued_through"]))

        # par repayment 5,000,000.00 after 92 nights (OAS back to 3.00 %): Dr 1010 5,000,000.00 / Cr 1130 4,908,394.16 / Cr 4000 91,605.84 (unaccreted OID realised)
        rt.rates.put(pc.OAS_SERIES, [(date(2027, 1, 20), Decimal("3.00"))], persist=False)
        self.assertEqual(490839416, pc.pc_mark(rt, row)[0])
        r = execute(rt, "pc.repay", {"loan_id": LOAN, "amount": "5000000.00"}, "ui:pc-repay-1")
        self.assertTrue(r.ok, r.body)
        self.assertEqual([("1010", None, 500000000), ("1130", LOAN, -490839416), ("4000", LOAN, -9160584)], lines_of(rt, r.body["entry_id"]))
        self.assertEqual((0, 500000000, 490839416, 9160584, "REPAID"), (r.body["accrued_paid_c"], r.body["principal_received_c"], r.body["amortised_cost_c"], r.body["realised_c"], r.body["status"]))
        self.assertEqual((0, 515111667, 9160584, "REPAID"), (bal(rt, F1_CREDIT, "1130", "USD", LOAN), bal(rt, F1_CREDIT, "1010"), bal(rt, F1_CREDIT, "4000", "USD", LOAN),
                                                             one(rt, "SELECT status FROM private_loans WHERE loan_id=?", (LOAN,))["status"]))
        assert_integrity(self, rt, "full")                                                                                  # L24 zero (no OPEN loan)
        self.assertEqual(([], [], 0), (loan_pos(rt), pay_keys(rt), pc.loans_view(rt, "F1:ALL")[0]["unrealised_c"]))
        self.assertEqual("REPAID", pc.loans_view(rt, "ALL")[0]["status"])
        # a night after the repayment is a terminal no-op (nothing accrues, nothing is defaulted)
        rt.clock.set(at(date(2027, 1, 21)))
        a = accrue(rt, LOAN, "2027-01-20", pc.KIND_CPN)
        self.assertEqual(("DONE", False), (a.body["status"], a.body["posted"]))
        self.assertIn("REPAID", a.body["note"])
        # no default / rating / review is ever generated: only the four pc.* handlers exist and no such event was written
        self.assertEqual({"pc.commit", "pc.amend_fee", "pc.repay", "pc.pay"}, {k for k in rt.bus.kinds() if k.startswith("pc.")})
        self.assertEqual([], q(rt, "SELECT type FROM events WHERE type LIKE '%DEFAULT%' OR type LIKE '%RATING%' OR type LIKE '%REVIEW%' OR type LIKE '%COVENANT%'"))
        with open(pc.__file__, "r", encoding="utf-8") as fh:
            src = fh.read()
        self.assertIsNone(re.search(r"def \w*(default|review|rating|covenant)\w*\(", src))

    # ---- RATE_LAG: a missing SOFR skips the night with a WARN and retries; never a default ----------------------------------------------
    def test_sofr_missing_skips_with_warn_then_retries(self):
        rt = self.rt = runtime(self.tmp)                                       # SOFR observed 10-19 only
        self.assertTrue(commit(rt, "ui:pc-commit-2").ok)
        # Sat 10-31 00:35: nights 10-20 .. 10-29 are within 10 days of the 10-19 print and post; 10-30 is 11 days after it -> SKIPPED RATE_UNAVAILABLE
        out = rt.lifecycle.tick(at="2026-10-31T00:35:00-04:00", only="ACCRUE_DAILY")
        ran = {i["key"]: i for i in out["ran"]}
        self.assertEqual(("SKIPPED", "RATE_UNAVAILABLE"), (ran[f"pccpn:{LOAN}:2026-10-30"]["status"], ran[f"pccpn:{LOAN}:2026-10-30"]["code"]))
        self.assertEqual("DONE", ran[f"pcoid:{LOAN}:2026-10-30"]["status"])    # the OID needs no rate
        self.assertEqual("SKIPPED", one(rt, "SELECT status FROM jobs_done WHERE job='ACCRUE_DAILY' AND key=?", (f"pccpn:{LOAN}:2026-10-30",))["status"])
        att = one(rt, "SELECT severity, code, resolved_ts FROM attention WHERE source='lifecycle' AND ref_type='job' AND ref_id=?", (f"ACCRUE_DAILY:pccpn:{LOAN}:2026-10-30",))
        self.assertEqual(("WARN", "RATE_UNAVAILABLE", None), (att["severity"], att["code"], att["resolved_ts"]))
        # 10 nights at 9.87 %: cum to_c(5,000,000 × 0.0987 × 10 / 360) = to_c(13,708.3333) = 13,708.33; 11 OID nights: to_c(100,000 × 11 / 1,096) = to_c(1,003.6496) = 1,003.65
        self.assertEqual((1370833, 490100365, "OPEN"), (bal(rt, F1_CREDIT, "1220", "USD", LOAN), bal(rt, F1_CREDIT, "1130", "USD", LOAN),
                                                        one(rt, "SELECT status FROM private_loans WHERE loan_id=?", (LOAN,))["status"]))
        self.assertEqual([], q(rt, "SELECT type FROM events WHERE type LIKE '%DEFAULT%'"))
        # FRED prints 3.90 for 10-30 -> the retry (a new request id) posts the night on a NEW segment: to_c(5,000,000 × 0.0990 / 360) = 1,375.00
        rt.rates.put("SOFR", [(date(2026, 10, 30), Decimal("3.90"))], persist=False)
        rt.clock.advance(lifecycle.SKIP_RETRY_S + 1)
        out = rt.lifecycle.tick(only="ACCRUE_DAILY")
        ran = {i["key"]: i for i in out["ran"]}
        # the retry carries a fresh request id (job:...:r{n}; WP-08 may already have re-run the SKIPPED key in a later pass of the first tick)
        self.assertEqual("DONE", ran[f"pccpn:{LOAN}:2026-10-30"]["status"])
        self.assertRegex(ran[f"pccpn:{LOAN}:2026-10-30"]["request_id"], rf"^job:ACCRUE_DAILY:pccpn:{LOAN}:2026-10-30:r\d+$")
        self.assertEqual(1370833 + 137500, bal(rt, F1_CREDIT, "1220", "USD", LOAN))
        self.assertEqual(("2026-10-30", "0.099", 137500, "2026-10-30"), tuple(one(rt, "SELECT segment_start, rate, cum_c, accrued_through FROM accrual_state WHERE kind='pccpn' AND ref_id=?", (LOAN,)).values()))
        self.assertIsNotNone(one(rt, "SELECT resolved_ts FROM attention WHERE source='lifecycle' AND ref_type='job' AND ref_id=?", (f"ACCRUE_DAILY:pccpn:{LOAN}:2026-10-30",))["resolved_ts"])
        assert_integrity(self, rt, "full")

    # ---- refusals, funding, replay -------------------------------------------------------------------------------------------------------
    def test_commit_refusals_funding_and_replay(self):
        rt = self.rt = runtime(self.tmp)
        cases = [({"currency": "EUR"}, "CURRENCY_UNSUPPORTED"), ({"index": "ECBDFR"}, "INDEX_UNSUPPORTED"), ({"account_id": "F1:US_EXCHANGE"}, "ACCOUNT_KIND"),
                 ({"account_id": "F1:NOPE"}, "ACCOUNT_UNKNOWN"), ({"issue_price": "0"}, "ISSUE_PRICE"), ({"issue_price": "101"}, "ISSUE_PRICE"), ({"spread": "6"}, "SPREAD"),
                 ({"spread": ""}, "SPREAD"), ({"term_months": 0}, "TERM"), ({"term_months": "x"}, "TERM"), ({"payment_months": 5}, "PAYMENT_MONTHS"),
                 ({"payment_months": 48}, "PAYMENT_MONTHS"), ({"borrower": ""}, "BORROWER_REQUIRED"), ({"company_id": "PRV-NOSUCHCO", "borrower": ""}, "COMPANY_UNKNOWN"),
                 ({"principal": "0"}, "AMOUNT")]
        for over, code in cases:
            r = commit(rt, **over)
            self.assertEqual((400, code), (r.http, r.code), (over, r.body))
        # P1 (Personal, 1,000,000.00) cannot fund 4,900,000.00: 409 INSUFFICIENT_FUNDS with the three amounts; nothing is written
        r = commit(rt, account_id="P1:CREDIT")
        self.assertEqual((409, "INSUFFICIENT_FUNDS", 490000000, 0, 100000000), (r.http, r.code, r.body["error"]["details"]["need_c"], r.body["error"]["details"]["own_available_c"],
                                                                                r.body["error"]["details"]["treasury_available_c"]))
        self.assertEqual(([], []), (q(rt, "SELECT loan_id FROM private_loans"), q(rt, "SELECT instrument_id FROM instruments WHERE instrument_id LIKE 'PC-%'")))
        # a percent spread is accepted ("6.00%" = 0.06); the replay returns the stored result and no second loan; a reused id with another payload is 409
        r = commit(rt, "ui:pc-commit-3", spread="6.00%")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("0.06", LOAN), (r.body["loan"]["spread"], r.body["loan_id"]))
        rep = commit(rt, "ui:pc-commit-3", spread="6.00%")
        self.assertEqual((True, LOAN, 1), (rep.replayed, rep.body["loan_id"], len(q(rt, "SELECT loan_id FROM private_loans"))))
        self.assertEqual((409, "REQUEST_ID_REUSED"), ((x := commit(rt, "ui:pc-commit-3", spread="0.07")).http, x.code))
        # the second loan takes the next id of the single sequence
        r2 = commit(rt, "ui:pc-commit-4", borrower="Otherco", principal="100000.00", issue_price="100")
        self.assertEqual(("PC-000002", 10000000, 0), (r2.body["loan_id"], r2.body["cash_out_c"], r2.body["loan"]["oid_total_c"]))
        # a fee / repayment needs an existing OPEN loan; a repayment below the accrued coupon is refused
        self.assertEqual((400, "NOT_FOUND"), ((x := execute(rt, "pc.amend_fee", {"loan_id": "PC-000009", "amount": "1.00"})).http, x.code))
        self.assertEqual((400, "AMOUNT"), ((x := execute(rt, "pc.amend_fee", {"loan_id": LOAN, "amount": "-5"})).http, x.code))
        rt.clock.set(at(date(2026, 10, 21)))
        self.assertTrue(accrue(rt, LOAN, "2026-10-20", pc.KIND_CPN).ok)                 # accrued 1,370.83
        r = execute(rt, "pc.repay", {"loan_id": LOAN, "amount": "1000.00"})
        self.assertEqual((400, "AMOUNT_BELOW_ACCRUED", 137083), (r.http, r.code, r.body["error"]["details"]["accrued_c"]))
        # the OID-free loan repaid on Wed 10-21: the night of 10-20 is accrued first (100,000 × 0.0987 / 360 = 27.4167 -> 27.42), so face + accrued
        # = 100,027.42 pays Dr 1010 27.42 / Cr 1220 27.42 then Dr 1010 100,000 / Cr 1130 100,000 — nothing realised; the loan is REPAID
        r = execute(rt, "pc.repay", {"loan_id": "PC-000002", "amount": "100027.42"})
        self.assertTrue(r.ok, r.body)
        self.assertEqual(([("1010", None, 2742), ("1220", "PC-000002", -2742), ("1010", None, 10000000), ("1130", "PC-000002", -10000000)], 0, 2742),
                         (lines_of(rt, r.body["entry_id"]), r.body["realised_c"], r.body["accrued_paid_c"]))
        self.assertEqual("DONE", one(rt, "SELECT 'DONE' AS s FROM journal_entries WHERE idem_key='accr:pccpn:PC-000002:2026-10-20'")["s"])
        self.assertEqual((409, "LOAN_CLOSED"), ((x := execute(rt, "pc.repay", {"loan_id": "PC-000002", "amount": "1.00"})).http, x.code))
        assert_integrity(self, rt, "full")

    # ---- PC_PAY gating, exact coupon per period, maturity repays the face --------------------------------------------------------------------
    def test_pay_keys_gating_quarter_amount_and_maturity(self):
        rt = self.rt = runtime(self.tmp, sofr_through=date(2026, 12, 21))
        # Shortco: 1,000,000.00 at 99.00 (cash out 990,000.00, OID 10,000.00), SOFR + 5.00 % = 8.87 %, 2 months, paid monthly:
        # maturity 2026-12-20 (a Sunday; 11 + 30 + 20 = 61 nights); payment dates Fri 2026-11-20 and modified following of 12-20 = Mon 2026-12-21
        r = commit(rt, "ui:pc-commit-5", borrower="Shortco", principal="1000000.00", issue_price="99.00", spread="0.05", term_months=2, payment_months=1)
        self.assertTrue(r.ok, r.body)
        loan = r.body["loan"]
        self.assertEqual(("2026-12-20", "2026-11-20", 61, 99000000, 1000000), (loan["maturity_date"], loan["next_payment_date"], loan["nights_total"], loan["cash_out_c"], loan["oid_total_c"]))
        self.assertEqual([("2026-11-20", "2026-11-20", False), ("2026-12-21", "2026-12-20", True)], [(s["date"], s["anchor"], s["final"]) for s in loan["schedule"]])
        # not due before the coupon has accrued through 11-19, whatever the time
        rt.clock.set(at(date(2026, 11, 20), "00:45"))
        self.assertEqual([], pay_keys(rt))
        self.assertEqual([], rt.lifecycle.tick(only="PC_PAY")["ran"])
        # accrue 33 nights (10-20 .. 11-21), i.e. PAST the payment date, before the payment runs (a catch-up): coupon cum
        # to_c(1,000,000 × 0.0887 × 33 / 360) = to_c(8,130.8333) = 8,130.83; OID cum to_c(10,000 × 33 / 61) = to_c(5,409.8361) = 5,409.84
        accrue_nights(self, rt, LOAN, date(2026, 10, 20), date(2026, 11, 21))
        self.assertEqual((813083, 99540984), (bal(rt, F1_CREDIT, "1220", "USD", LOAN), bal(rt, F1_CREDIT, "1130", "USD", LOAN)))
        # the payment of 11-20 pays exactly the nights through 11-19: cum_31 = to_c(88,700 × 31 / 360) = to_c(7,638.0556) = 7,638.06; 1220 keeps 8,130.83 − 7,638.06 = 492.77
        self.assertEqual([(LOAN, "2026-11-20")], pay_keys(rt))
        out = rt.lifecycle.tick(only="PC_PAY")
        self.assertEqual([("DONE", f"{LOAN}:2026-11-20")], [(i["status"], i["key"]) for i in out["ran"]])
        self.assertEqual([("1010", None, 763806), ("1220", LOAN, -763806)], lines_of(rt, one(rt, "SELECT entry_id FROM journal_entries WHERE idem_key=?", (f"pcpay:{LOAN}:2026-11-20",))["entry_id"]))
        self.assertEqual((49277, 763806, "2026-12-21"), (bal(rt, F1_CREDIT, "1220", "USD", LOAN), bal(rt, F1_CREDIT, "1010"), one(rt, "SELECT next_payment_date FROM private_loans")["next_payment_date"]))
        self.assertEqual([], rt.lifecycle.tick(only="PC_PAY")["ran"])
        assert_integrity(self, rt, "full")
        # the remaining 28 nights (11-22 .. 12-19): cum_61 = to_c(88,700 × 61 / 360) = to_c(15,029.7222) = 15,029.72 -> 1220 = 15,029.72 − 7,638.06 = 7,391.66;
        # OID cum_61 = 10,000.00 exactly -> 1130 = 1,000,000.00 (fully accreted)
        accrue_nights(self, rt, LOAN, date(2026, 11, 22), date(2026, 12, 19))
        self.assertEqual((739166, 100000000, 1000000), (bal(rt, F1_CREDIT, "1220", "USD", LOAN), bal(rt, F1_CREDIT, "1130", "USD", LOAN),
                                                        one(rt, "SELECT cum_c FROM accrual_state WHERE kind='pcoid' AND ref_id=?", (LOAN,))["cum_c"]))
        # the night of the maturity date itself accrues nothing (terminal no-op, DONE)
        rt.clock.set(at(date(2026, 12, 21)))
        a = accrue(rt, LOAN, "2026-12-20", pc.KIND_CPN)
        self.assertEqual(("DONE", False), (a.body["status"], a.body["posted"]))
        self.assertIn("maturity", a.body["note"])
        # the final payment (Mon 12-21) is due from 00:40: coupon 7,391.66 + the face 1,000,000.00 against amortised cost 1,000,000.00 (realised 0, no 4000 line)
        rt.clock.set(at(date(2026, 12, 21), "00:30"))
        self.assertEqual([], pay_keys(rt))
        rt.clock.set(at(date(2026, 12, 21), "00:45"))
        self.assertEqual([(LOAN, "2026-12-21")], pay_keys(rt))
        out = rt.lifecycle.tick(only="PC_PAY")
        self.assertEqual([("DONE", f"{LOAN}:2026-12-21")], [(i["status"], i["key"]) for i in out["ran"]])
        self.assertEqual([("1010", None, 739166), ("1220", LOAN, -739166), ("1010", None, 100000000), ("1130", LOAN, -100000000)],
                         lines_of(rt, one(rt, "SELECT entry_id FROM journal_entries WHERE idem_key=?", (f"pcpay:{LOAN}:2026-12-21",))["entry_id"]))
        row = one(rt, "SELECT status, closed_ts FROM private_loans WHERE loan_id=?", (LOAN,))
        # cash in F1:CREDIT: 7,638.06 + 7,391.66 + 1,000,000.00 = 1,015,029.72
        self.assertEqual(("REPAID", True, 0, 0, 101502972, 0), (row["status"], row["closed_ts"] is not None, bal(rt, F1_CREDIT, "1130", "USD", LOAN), bal(rt, F1_CREDIT, "1220", "USD", LOAN),
                                                               bal(rt, F1_CREDIT, "1010"), bal(rt, F1_CREDIT, "4000", "USD", LOAN)))
        self.assertEqual(([], []), (pay_keys(rt), rt.lifecycle.tick(only="PC_PAY")["ran"]))
        self.assertEqual("PC_PAID", one(rt, "SELECT type FROM events WHERE ref_id=? ORDER BY id DESC LIMIT 1", (LOAN,))["type"])
        assert_integrity(self, rt, "full")

    # ---- the mark's inputs and edges ------------------------------------------------------------------------------------------------------
    def test_mark_inputs_floor_and_no_spread_observation(self):
        rt = self.rt = runtime(self.tmp)
        self.assertTrue(commit(rt, "ui:pc-commit-6").ok)
        row = one(rt, "SELECT * FROM private_loans WHERE loan_id=?", (LOAN,))
        # Wed 10-21: 1,095 days remaining (2029-10-20 − 2026-10-21) -> T = 3.0 exactly
        rt.clock.set(at(date(2026, 10, 21), "11:00"))
        # OAS 2.50 % (ΔOAS −0.50 %): 4,900,000 × (1 + 0.005 × 3) = 4,900,000 × 1.015 = 4,973,500.00
        rt.rates.put(pc.OAS_SERIES, [(date(2026, 10, 20), Decimal("2.50"))], persist=False)
        mark_c, inputs = pc.pc_mark(rt, row)
        self.assertEqual((497350000, 1095, "-0.50"), (mark_c, inputs["days_remaining"], inputs["delta_oas_pct"]))
        # OAS 60.00 % (ΔOAS +57 %): 1 − 0.57 × 3 < 0 -> the first-order adjustment is floored at zero and says so
        rt.rates.put(pc.OAS_SERIES, [(date(2026, 10, 21), Decimal("60.00"))], persist=False)
        mark_c, inputs = pc.pc_mark(rt, row)
        self.assertEqual(0, mark_c)
        self.assertIn("floored at zero", inputs["note"])
        # no rate store at all -> the amortised cost, labelled
        rates, rt.rates = rt.rates, None
        mark_c, inputs = pc.pc_mark(rt, row)
        self.assertEqual((490000000, "amortised cost (no spread observation)", None), (mark_c, inputs["note"], inputs["oas_now_pct"]))
        rt.rates = rates
        # a loan committed with no OAS print stored: oas_issue null -> amortised cost, however the spread moves later
        rt2tmp = tempfile.mkdtemp(prefix="fst-pc2-")
        try:
            rt2 = runtime(rt2tmp, oas=None)
            try:
                r = commit(rt2, "ui:pc-commit-7")
                self.assertTrue(r.ok, r.body)
                self.assertEqual((None, 490000000, "amortised cost (no spread observation)"), (r.body["loan"]["oas_issue"], r.body["loan"]["mark_c"], r.body["mark"]["note"]))
                rt2.rates.put(pc.OAS_SERIES, [(date(2026, 10, 20), Decimal("9.00"))], persist=False)
                self.assertEqual(490000000, pc.pc_mark(rt2, one(rt2, "SELECT * FROM private_loans"))[0])
                # the views by scope: the account, the lifetime, every book; another lifetime sees nothing
                self.assertEqual(([LOAN], [LOAN], [LOAN], []), tuple([v["loan_id"] for v in pc.loans_view(rt2, s)] for s in (F1_CREDIT, "F1:ALL", "ALL", "P1:ALL")))
            finally:
                rt2.close()
        finally:
            shutil.rmtree(rt2tmp, ignore_errors=True)

    # ---- the schedule roll and the UI component ---------------------------------------------------------------------------------------------
    def test_modified_following_and_component_file(self):
        rt = self.rt = runtime(self.tmp)
        # Sat 2029-10-20 -> Mon 2029-10-22 (same month); Sat 2027-07-31 -> following would be Mon 08-02 (next month) -> back to Fri 2027-07-30
        self.assertEqual((date(2029, 10, 22), date(2027, 7, 30), date(2027, 1, 20)),
                         (pc.modified_following(rt.cal, date(2029, 10, 20)), pc.modified_following(rt.cal, date(2027, 7, 31)), pc.modified_following(rt.cal, date(2027, 1, 20))))
        # a 5-month term paid quarterly: one full quarter (anchor 2027-01-20) and a stub to the maturity 2027-03-20 (Sat -> Mon 2027-03-22)
        self.assertEqual([(date(2027, 1, 20), date(2027, 1, 20)), (date(2027, 3, 20), date(2027, 3, 22))], pc.payment_schedule(rt.cal, date(2026, 10, 20), date(2027, 3, 20), 3))
        # the component: ASCII, registers itself, balanced brackets outside strings / comments, calls only the designed routes
        path = os.path.join(STATIC, "components", "privatecredit.js")
        with open(path, "rb") as fh:
            raw = fh.read()
        src = raw.decode("ascii")
        self.assertIn("T.components.privatecredit = ", src)
        self.assertIn("'/api/credit/private'", src)
        self.assertNotRegex(src, r"cdn|unpkg|jsdelivr|googleapis|cloudflare")
        stripped = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
        stripped = re.sub(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"", "", stripped)
        stripped = re.sub(r"//[^\n]*", "", stripped)
        self.assertEqual((0, 0, 0), (stripped.count("(") - stripped.count(")"), stripped.count("[") - stripped.count("]"), stripped.count("{") - stripped.count("}")))
        with open(os.path.join(STATIC, "index.html"), "r", encoding="utf-8") as fh:
            index_html = fh.read()
        self.assertIn('components/privatecredit.js" data-optional="1"', index_html)        # loaded by the shell as an optional component (WP-12)


if __name__ == "__main__":
    unittest.main()
