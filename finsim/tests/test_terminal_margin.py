"""FINSIM1 P6 Part B (1.0.6) margin tests: account-level Reg T (task 4, PORTFOLIO-11, acceptance 1 (c) and (c')), the margin calls and
the MARGIN_CALL job (task 5, PORTFOLIO-12) and its start-forward rule (15.9 item 8), the Reg T terms command.

Every expected number is worked by hand in a comment.  No network: FakeClock, FixedMarks, a temp database.  The positions are real:
the long is a fill (``test.buy``, no fee) settled through the SETTLE_DUE posting, the physical short is a real SELL_SHORT through the
order engine (locate, borrow, 102 % collateral at the fill); the marks are then set so the short value and the long value are round
figures, and a transfer leaves the account exactly the free cash of the acceptance case.

The Reg T rule (collateral module docstring PART B, REG T): requirement = 50 % of the physical short value; eligible = 50 % of the
fully paid longs + free cash (settled + receivables - payables - restricted - option reservation); the cash the requirement needs
beyond the longs' loan value, min(free cash, max(0, requirement - loan value)), is reserved (the C9b adjuster "margin requirement
(Reg T)"); a requirement beyond the eligible collateral is a deficit and a margin call."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from datetime import date
from decimal import Decimal

from tests.terminal_helpers import T1, TREASURY, US_EXCHANGE, assert_integrity, bal, buy, execute, fund
from tests import test_terminal_lifecycle as lc
from tests import test_terminal_orders as sc
from tests import test_terminal_shorts as sh
from finsim.terminal import collateral, execution, lending, lifecycle, prime, repo
from finsim.terminal.clock import epoch_of
from finsim.terminal.ledger import Entry, cr, dr

D = Decimal
TUE_10 = "2026-10-20T10:00:00-04:00"         # Tuesday, a NYSE business day; the runtime (and the job's start marker) begins here
TUE_1721 = "2026-10-20T17:21:00-04:00"       # after the 17:20 ET margin-call time of Tue 10-20
# around the call's due time (T+1 10:00 ET): a re-check that finds a call still open is deferred 5 minutes by the scheduler (SKIP_RETRY_S),
# so after the 09:59 check the next one runs from 10:04
WED_0959, WED_1005 = "2026-10-21T09:59:00-04:00", "2026-10-21T10:05:00-04:00"


def _h_fut_vm(ctx, payload):
    """A futures variation-margin loss posted exactly as ``products.futures.h_vm`` posts it: funded first (own cash -> one Treasury sweep
    linked ``vm`` / the idem -> the overdraft rule of section 11.6), then ``Dr 4400 / Cr 1010`` with idem ``vm:{account}:{iid}:{date}``
    and business date = the settle date."""
    aid, iid, ds, loss = payload["account_id"], payload["instrument_id"], payload["date"], int(payload["loss_c"])
    idem = f"vm:{aid}:{iid}:{ds}"
    funding = execution.fund(ctx, aid, "USD", loss, "vm", idem, None, immediate=True, overdraft=True, services=ctx.services)
    eid = ctx.post(Entry("VM", f"test futures VM {iid} {ds}: {loss}", [dr(aid, "4400", "USD", loss, instrument_id=iid, memo="variation margin paid"),
                                                                       cr(aid, "1010", "USD", loss)], idem, ref_type="vm", ref_id=f"{aid}:{iid}:{ds}",
                         business_date=ds))
    return {"entry_id": eid, "funding": funding.to_dict()}


def runtime(tmp, at=TUE_10):
    """The lifecycle runtime (orders, lending, credit, futures, options, SOFR, the scheduler) + prime + repo (C27 pledged face) + the
    collateral module; the scheduler is rebuilt after ``collateral.bind`` so it carries the MARGIN_CALL job (as ``build_runtime`` does:
    every module binds before the scheduler is built)."""
    rt = lc.runtime(tmp, at=at)
    prime.register(rt.bus)
    prime.bind(rt)
    repo.register(rt.bus)
    rt.repo = repo
    collateral.register(rt.bus)
    collateral.bind(rt)
    rt.collateral = collateral
    lifecycle.register(rt.bus, rt)
    rt.lifecycle = lifecycle.LifecycleScheduler(rt.bus, rt.db, rt, rt.clock, rt.log)
    rt.bus.register("test.settle_due", sh._h_settle_due)
    rt.bus.register("test.fut_vm", _h_fut_vm)
    return rt


def ok(tc, r):
    tc.assertTrue(r.ok, r.body)
    return r.body


def regt(rt, account=US_EXCHANGE):
    with rt.db.reader() as conn:
        return collateral.regt_view(conn, account, rt.marks.snapshot(), rt.instruments)


def adjustments(rt, account=US_EXCHANGE):
    return {a["key"]: int(a["amount_c"]) for a in rt.accounting.cash(account)["USD"].adjustments}


def calls(rt):
    return sc.q(rt, "SELECT * FROM margin_calls ORDER BY call_id")


def open_attention(rt, code="MARGIN_CALL"):
    return sc.q(rt, "SELECT * FROM attention WHERE code=? AND resolved_ts IS NULL ORDER BY id", (code,))


class RegTBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-margin-")
        self.rt = runtime(self.tmp)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def book_positions(self):
        """US Exchange: long 60,000 LONGX (fully paid), short 20,000 SHRT (a real SELL_SHORT), then exactly 100,000.00 of free cash.
        Marks 50.00 / 50.00 -> long value 3,000,000.00, short value 1,000,000.00 (acceptance (c))."""
        rt = self.rt
        sh.add_equity(rt, "LONGX", 5000000)
        sh.add_equity(rt, "SHRT", 5000000)
        sh.finra(rt, "SHRT", [("2026-10-15", 30000, 100000), ("2026-10-16", 30000, 100000), ("2026-10-19", 30000, 100000)])
        # the long: BUY 60,000 LONGX @ 50.00, no fee -> payable 3,000,000.00 (T+1); funded from Treasury and settled (Dr 2100 / Cr 1010)
        ok(self, buy(rt, US_EXCHANGE, "LONGX", 60000, "50.00", fee_c=0))
        ok(self, fund(rt, US_EXCHANGE, 300000000))
        ok(self, execute(rt, "test.settle_due", {"as_of": "2026-10-21"}, actor="lifecycle"))
        self.assertEqual((0, 0), (bal(rt, US_EXCHANGE, "1010"), bal(rt, US_EXCHANGE, "2100")))
        # the short: SELL_SHORT 20,000 SHRT MARKET at 50.00 (GC borrow; the lender takes 102 % = 1,020,000.00 of collateral at the fill:
        # the sale's own proceeds less the fee, the rest swept from Treasury for it)
        sh.quote(rt, "SHRT", "50.00", 1000000)
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="SHRT", side="SELL_SHORT", qty=20000, order_type="MARKET")
        self.assertTrue(r.ok, r.body)
        self.assertEqual("FILLED", r.body["order"]["status"])
        self.assertEqual(102000000, bal(rt, US_EXCHANGE, "1400", "USD", "SHRT"))       # 1.02 x 20,000 x 50.00
        rt.marks.set("LONGX", "50.00")
        rt.marks.set("SHRT", "50.00")
        # exactly 100,000.00 of free cash: whatever the short's funding left in the account, topped up to 100,000.00
        free = regt(rt)["free_cash_c"]
        if free < 10000000:
            ok(self, fund(rt, US_EXCHANGE, 10000000 - free))
        elif free > 10000000:
            ok(self, execute(rt, "treasury.transfer", {"from_account": US_EXCHANGE, "to_account": TREASURY, "currency": "USD", "amount_c": free - 10000000}))
        self.assertEqual(10000000, regt(rt)["free_cash_c"])


class RegTTests(RegTBase):
    def test_c_excess_case_reserves_nothing(self):
        rt = self.rt
        self.book_positions()
        v = regt(rt)
        # requirement 50 % x 1,000,000.00 = 500,000.00; maintenance 30 % = 300,000.00; loan value 50 % x 3,000,000.00 = 1,500,000.00;
        # eligible 1,500,000.00 + free cash 100,000.00 = 1,600,000.00; excess 1,100,000.00; the longs alone cover the requirement:
        # nothing reserved (min(100,000.00, max(0, 500,000.00 - 1,500,000.00)) = 0)
        self.assertEqual((100000000, 300000000, 50000000, 30000000, 150000000, 10000000, 160000000, 110000000, 0, 0, False),
                         (v["short_mv_c"], v["long_mv_c"], v["requirement_c"], v["maintenance_c"], v["long_loan_value_c"], v["free_cash_c"],
                          v["eligible_c"], v["excess_c"], v["deficit_c"], v["margin_cash_c"], v["below_maintenance"]))
        self.assertEqual(("0.50", "0.30", "0.50", "assumed (default)"), (v["terms"]["initial_pct"], v["terms"]["maintenance_pct"],
                                                                         v["terms"]["long_loan_value_pct"], v["terms"]["source"]))
        self.assertEqual(0, adjustments(rt).get("margin", 0))
        # available = settled 100,000.00 - restricted 0 - margin 0 = 100,000.00 (no receivable, payable or working order)
        self.assertEqual(10000000, rt.accounting.cash(US_EXCHANGE)["USD"].available_c)
        assert_integrity(self, rt)

    def test_c_prime_deficit_reserved_and_called_then_met_by_the_owner(self):
        rt = self.rt
        self.book_positions()
        avail_before = rt.accounting.cash(US_EXCHANGE)["USD"].available_c                     # 100,000.00
        rt.marks.set("LONGX", "10.00")                                                           # longs 60,000 x 10.00 = 600,000.00
        v = regt(rt)
        # loan value 50 % x 600,000.00 = 300,000.00; eligible 300,000.00 + 100,000.00 = 400,000.00 < requirement 500,000.00 -> deficit
        # 100,000.00; the cash the requirement needs beyond the longs = 500,000.00 - 300,000.00 = 200,000.00, of which the account has
        # 100,000.00: reserved min(100,000.00, 200,000.00) = 100,000.00
        self.assertEqual((60000000, 30000000, 40000000, 10000000, 10000000, 20000000), (v["long_mv_c"], v["long_loan_value_c"], v["eligible_c"],
                                                                                        v["deficit_c"], v["margin_cash_c"], v["needed_cash_c"]))
        self.assertEqual(10000000, adjustments(rt)["margin"])
        cv = rt.accounting.cash(US_EXCHANGE)["USD"]
        self.assertEqual((10000000, 0), (avail_before, cv.available_c))                         # 100,000.00 - 100,000.00 reserved = 0.00
        bp = rt.accounting.buying_power(US_EXCHANGE, "USD")
        self.assertEqual(0, bp.available_c)
        # the reserved cash cannot be moved out either; the refusal adds up: settled 100,000.00 less the Reg T margin cash 100,000.00
        r = execute(rt, "treasury.transfer", {"from_account": US_EXCHANGE, "to_account": TREASURY, "currency": "USD", "amount_c": 5000000})
        self.assertEqual((False, "INSUFFICIENT_FUNDS"), (r.ok, r.body["error"]["code"]))
        self.assertIn("can move USD 0.00 now (settled cash 100,000.00, less margin requirement (Reg T) 100,000.00)", r.body["error"]["message"])
        # the call at 17:20 ET: REG_T 100,000.00, OPEN (the job never moves cash: the account's own 100,000.00 is already reserved and the
        # owner meets the rest), with one ERROR attention, due the next business day 10:00 ET; nothing moved
        tre_before = bal(rt, TREASURY, "1010")
        out = rt.lifecycle.tick(at=TUE_1721, only="MARGIN_CALL")
        # the day's key issues the call; the next pass of the same tick re-checks it (still open: quiet, retried)
        self.assertEqual([("MARGIN_CALL", f"{US_EXCHANGE}:2026-10-20", "DONE"), ("MARGIN_CALL", f"{US_EXCHANGE}:call:MC-000001", "SKIPPED")],
                         [(i["job"], i["key"], i["status"]) for i in out["ran"]])
        c = calls(rt)
        self.assertEqual([("REG_T", 10000000, "OPEN", 0, "2026-10-20")], [(x["kind"], x["amount_c"], x["status"], x["funded_c"], x["business_date"]) for x in c])
        self.assertEqual("2026-10-21 10:00 ET", collateral.margin_calls_rows(rt, US_EXCHANGE)[0]["due"])      # T+1 10:00 ET
        self.assertEqual([], sc.q(rt, "SELECT * FROM transfers WHERE ts >= ?", (rt.clock.epoch() - 60,)))
        self.assertEqual(tre_before, bal(rt, TREASURY, "1010"))
        self.assertEqual([("ERROR", c[0]["call_id"])], [(a["severity"], a["ref_id"]) for a in open_attention(rt)])
        s = collateral.summary(rt, f"{T1}:ALL")
        self.assertEqual((1, 10000000), (s["open_calls"], s["open_calls_c"]))
        # the 17:30 sweep leaves the reserved margin cash in the account: 100,000.00 - 100,000.00 reserved - buffer < 0, nothing idle
        rt.lifecycle.tick(at="2026-10-20T17:31:00-04:00", only="SWEEP_OUT")
        self.assertEqual([], sc.q(rt, "SELECT * FROM transfers WHERE kind='SWEEP_OUT'"))
        self.assertEqual(10000000, bal(rt, US_EXCHANGE, "1010"))
        # the owner meets it: 100,000.00 from Treasury to US Exchange (an ordinary transfer); the next check (the key was deferred 5 minutes
        # after the 17:21 run) finds no deficit and marks it MET, linking that transfer; the attention resolves
        x = ok(self, fund(rt, US_EXCHANGE, 10000000))
        rt.lifecycle.tick(at="2026-10-20T17:40:00-04:00", only="MARGIN_CALL")
        c = calls(rt)
        self.assertEqual(("MET", 10000000), (c[0]["status"], c[0]["funded_c"]))
        self.assertEqual([(x["transfer_id"], "TRANSFER", 10000000)], [(t["transfer_id"], t["kind"], t["amount_c"]) for t in json.loads(c[0]["transfers_json"])])
        self.assertEqual([], open_attention(rt))
        v = regt(rt)
        # after: free cash 200,000.00, eligible 300,000.00 + 200,000.00 = 500,000.00 = requirement; deficit 0; the cash the requirement
        # needs, 200,000.00, is reserved (so the 17:30 sweep never moves it back to Treasury)
        self.assertEqual((20000000, 50000000, 0, 20000000), (v["free_cash_c"], v["eligible_c"], v["deficit_c"], v["margin_cash_c"]))
        self.assertEqual(0, rt.accounting.cash(US_EXCHANGE)["USD"].available_c)
        rt.lifecycle.tick(at="2026-10-21T17:31:00-04:00", only="SWEEP_OUT")
        self.assertEqual([], sc.q(rt, "SELECT * FROM transfers WHERE kind='SWEEP_OUT'"))
        self.assertEqual(20000000, bal(rt, US_EXCHANGE, "1010"))
        # a rerun of the day's key posts nothing (DONE in jobs_done; the call's idem key is unique)
        out = rt.lifecycle.tick(at="2026-10-20T17:50:00-04:00", only="MARGIN_CALL")
        self.assertEqual([], [i for i in out["ran"] if i["key"] == f"{US_EXCHANGE}:2026-10-20"])
        self.assertEqual(1, len(calls(rt)))
        # the C16 reads: no open call, the Activity row carries the transfer
        rows = collateral.margin_calls_rows(rt, f"{T1}:ALL")
        self.assertEqual(("Reg T margin deficit", "MET", [10000000]), (rows[0]["kind_label"], rows[0]["status"], [t["amount_c"] for t in rows[0]["transfers"]]))
        s = collateral.summary(rt, f"{T1}:ALL")
        self.assertEqual((0, 20000000), (s["open_calls"], s["margin_reserved_c"]))
        assert_integrity(self, rt)

    def test_c_prime_call_unmet_after_the_due_time_then_covered(self):
        rt = self.rt
        self.book_positions()
        rt.marks.set("LONGX", "10.00")                                                           # deficit 100,000.00 (as above)
        out = rt.lifecycle.tick(at=TUE_1721, only="MARGIN_CALL")
        c = calls(rt)
        self.assertEqual([("REG_T", 10000000, "OPEN", 0)], [(x["kind"], x["amount_c"], x["status"], x["funded_c"]) for x in c])
        att = open_attention(rt)
        self.assertEqual([("ERROR", "margin_call", c[0]["call_id"])], [(a["severity"], a["ref_type"], a["ref_id"]) for a in att])
        self.assertIn("due 2026-10-21 10:00 ET", att[0]["message"])
        # before the due time the re-check finds it still open (quiet, retried)
        out = rt.lifecycle.tick(at=WED_0959, only="MARGIN_CALL")
        self.assertEqual([("SKIPPED",)], [(i["status"],) for i in out["ran"] if ":call:" in i["key"]])
        self.assertEqual("OPEN", calls(rt)[0]["status"])
        # after it: UNMET, the ERROR attention stays (now saying unmet; positions are never sold)
        out = rt.lifecycle.tick(at=WED_1005, only="MARGIN_CALL")
        c = calls(rt)
        self.assertEqual(("UNMET", None), (c[0]["status"], c[0]["cured_ts"]))
        att = open_attention(rt)
        self.assertEqual(1, len(att))
        self.assertIn("UNMET", att[0]["message"])
        self.assertIn("never sold", att[0]["message"])
        self.assertEqual(1, collateral.summary(rt, f"{T1}:ALL")["unmet_calls"])
        # the owner moves cash in: 100,000.00 from Treasury to US Exchange -> eligible 500,000.00, the condition is cured: the attention
        # resolves, the call stays UNMET (it was not met by its due time) with its cured time
        ok(self, fund(rt, US_EXCHANGE, 10000000))
        rt.lifecycle.tick(at="2026-10-21T10:20:00-04:00", only="MARGIN_CALL")
        c = calls(rt)
        self.assertEqual("UNMET", c[0]["status"])
        self.assertIsNotNone(c[0]["cured_ts"])
        self.assertEqual([], open_attention(rt))
        assert_integrity(self, rt)

    def test_margin_terms_command(self):
        rt = self.rt
        self.book_positions()
        r = execute(rt, "margin.terms", {"account_id": US_EXCHANGE, "initial_pct": "0.60"})
        b = ok(self, r)
        self.assertEqual(({"initial_pct": "0.50", "maintenance_pct": "0.30", "long_loan_value_pct": "0.50"}, "0.60"), (b["before"], b["after"]["initial_pct"]))
        # requirement 60 % x 1,000,000.00 = 600,000.00; eligible 1,600,000.00 unchanged
        v = regt(rt)
        self.assertEqual((60000000, 160000000, "edited (assumed terms)"), (v["requirement_c"], v["eligible_c"], v["terms"]["source"]))
        ev = sc.q(rt, "SELECT payload_json FROM events WHERE type='SETTINGS_CHANGED' AND ref_type='margin_terms'")
        self.assertEqual({"initial_pct": "0.60", "maintenance_pct": "0.30", "long_loan_value_pct": "0.50"}, json.loads(ev[0]["payload_json"])["after"])
        for body, code in (({"account_id": US_EXCHANGE, "maintenance_pct": "0.70"}, "TERMS_VALUE"),           # above the initial 60 %
                           ({"account_id": US_EXCHANGE, "initial_pct": 0.5}, "TERMS_VALUE"),                  # a float is refused
                           ({"account_id": US_EXCHANGE, "initial_pct": "1.5"}, "TERMS_VALUE"),
                           ({"account_id": US_EXCHANGE, "initial_pct": "0.60"}, "NOTHING_TO_UPDATE"),
                           ({"account_id": US_EXCHANGE}, "NOTHING_TO_UPDATE"),
                           ({"account_id": TREASURY, "initial_pct": "0.40"}, "NOT_MARGIN_ACCOUNT")):
            r = execute(rt, "margin.terms", body)
            self.assertEqual((False, code), (r.ok, r.body["error"]["code"]), body)


class MarginCallTests(unittest.TestCase):
    """The VM-type calls (futures variation margin, stock-borrow collateral) and the start-forward rule."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-mcall-")
        self.rt = runtime(self.tmp)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_futures_vm_call_met_with_its_sweep_and_an_overdraft_call_met_later(self):
        rt = self.rt
        # Tue 15:30: a futures VM loss of 25,000.00 on an account with no cash: one Treasury sweep of 25,000.00 linked to the VM (L19)
        ok(self, execute(rt, "test.fut_vm", {"account_id": US_EXCHANGE, "instrument_id": "GCZ26", "date": "2026-10-20", "loss_c": 2500000}, actor="lifecycle"))
        rt.lifecycle.tick(at=TUE_1721, only="MARGIN_CALL")
        c = calls(rt)
        self.assertEqual([("FUTURES_VM", 2500000, "MET", collateral.FCM_NAME)], [(x["kind"], x["amount_c"], x["status"], x["counterparty"]) for x in c])
        tr = json.loads(c[0]["transfers_json"])
        self.assertEqual([("SWEEP_IN", 2500000, "vm", f"vm:{US_EXCHANGE}:GCZ26:2026-10-20")], [(t["kind"], t["amount_c"], t["linked_type"], t["linked_id"]) for t in tr])
        # Wed: Treasury emptied, a 40,000.00 loss is paid as an overdraft (FUNDING_SHORTFALL): the call is OPEN with an ERROR attention
        rt.clock.set("2026-10-21T14:00:00-04:00")
        ok(self, execute(rt, "treasury.withdraw", {"lifetime_id": T1, "amount_c": bal(rt, TREASURY, "1010"), "currency": "USD"}))
        ok(self, execute(rt, "test.fut_vm", {"account_id": US_EXCHANGE, "instrument_id": "GCZ26", "date": "2026-10-21", "loss_c": 4000000}, actor="lifecycle"))
        self.assertEqual(-4000000, bal(rt, US_EXCHANGE, "1010"))
        rt.lifecycle.tick(at="2026-10-21T17:21:00-04:00", only="MARGIN_CALL")
        c = calls(rt)
        self.assertEqual(("FUTURES_VM", 4000000, "OPEN"), (c[1]["kind"], c[1]["amount_c"], c[1]["status"]))
        self.assertEqual(4000000, json.loads(c[1]["detail_json"])["overdraft_c"])
        self.assertEqual([c[1]["call_id"]], [a["ref_id"] for a in open_attention(rt)])
        # nothing moves by itself: still OPEN after a later check
        rt.lifecycle.tick(at="2026-10-21T17:30:00-04:00", only="MARGIN_CALL")
        self.assertEqual("OPEN", calls(rt)[1]["status"])
        # the owner deposits 100,000.00 into Treasury and moves the 40,000.00 overdraft to US Exchange: the transfer resolves the
        # FUNDING_SHORTFALL item (settled cash back at 0.00) and the next check marks the call MET, linking that transfer
        ok(self, execute(rt, "treasury.deposit", {"lifetime_id": T1, "amount_c": 10000000, "currency": "USD"}))
        x = ok(self, fund(rt, US_EXCHANGE, 4000000))
        rt.lifecycle.tick(at="2026-10-21T17:40:00-04:00", only="MARGIN_CALL")
        c = calls(rt)
        self.assertEqual(("MET", 4000000), (c[1]["status"], c[1]["funded_c"]))
        self.assertEqual(0, bal(rt, US_EXCHANGE, "1010"))
        self.assertEqual([], open_attention(rt))
        self.assertEqual([], open_attention(rt, "FUNDING_SHORTFALL"))
        self.assertEqual([x["transfer_id"]], [t["transfer_id"] for t in json.loads(c[1]["transfers_json"]) if t["kind"] == "TRANSFER"])
        assert_integrity(self, rt)

    def test_borrow_collateral_call_is_recorded_against_the_lender(self):
        rt = self.rt
        sh.add_equity(rt, "SHRT", 5000000)
        sh.finra(rt, "SHRT", [("2026-10-15", 30000, 100000), ("2026-10-16", 30000, 100000), ("2026-10-19", 30000, 100000)])
        sh.quote(rt, "SHRT", "50.00", 1000000)
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="SHRT", side="SELL_SHORT", qty=1000, order_type="MARKET")
        self.assertTrue(r.ok, r.body)
        # Tue 17:10: the collateral mark on a CLOSE of 55.00: required 1.02 x 1,000 x 55.00 = 56,100.00 vs 51,000.00 -> call 5,100.00, paid
        sh.close_mark(rt, "SHRT", "2026-10-20", "55.00")
        rt.clock.set("2026-10-20T17:10:00-04:00")
        mk = ok(self, execute(rt, "credit.collateral_mark", {"borrow_id": "B-000001", "mark_date": "2026-10-20"}, request_id="job:cmark:B-000001:2026-10-20",
                               actor="lifecycle"))
        self.assertEqual(510000, mk["delta_c"])
        rt.marks.set("SHRT", "55.00")
        rt.lifecycle.tick(at=TUE_1721, only="MARGIN_CALL")
        bc = [x for x in calls(rt) if x["kind"] == "BORROW_COLLATERAL"]
        lender = lending.lender_label(sh.borrow_row(rt, "B-000001")["lender"])     # the directory's agent lender, "(assumed terms)"
        self.assertTrue(lender.endswith("(agent lender, assumed terms)"), lender)
        self.assertEqual([(510000, "MET", lender)], [(x["amount_c"], x["status"], x["counterparty"]) for x in bc])
        tr = json.loads(bc[0]["transfers_json"])
        self.assertEqual([("borrow", "B-000001")], sorted({(t["linked_type"], t["linked_id"]) for t in tr}))
        # the same day's Reg T test of the account: short 1,000 x 55.00 = 55,000.00 -> requirement 27,500.00, no long and no free cash (the
        # account's cash went to the lender) -> deficit 27,500.00: a REG_T call, OPEN until the owner funds it
        v = regt(rt)
        self.assertEqual((5500000, 2750000, 0, 2750000), (v["short_mv_c"], v["requirement_c"], v["free_cash_c"], v["deficit_c"]))
        self.assertEqual([("REG_T", 2750000, "OPEN")], [(x["kind"], x["amount_c"], x["status"]) for x in calls(rt) if x["kind"] == "REG_T"])
        # the Cash & Collateral read of the short: the proceeds (2600) vs the lender's collateral (1400) and the fund's own cash between
        # them, each equal to the ledger; the lender holds 51,000.00 + the 5,100.00 call = 56,100.00 = 102 % x 1,000 x 55.00
        cc = collateral.cash_collateral_view(rt, f"{T1}:ALL")
        s = cc["shorts"]
        proceeds, coll = bal(rt, US_EXCHANGE, "2600", "USD", "SHRT"), bal(rt, US_EXCHANGE, "1400", "USD", "SHRT")      # natural signs
        # proceeds 1,000 x 50.00 = 50,000.00; the fund's own cash at the lender = 56,100.00 - 50,000.00 = 6,100.00
        self.assertEqual((5000000, 5610000, 610000), (proceeds, coll, coll - proceeds))
        self.assertEqual((proceeds, coll, coll - proceeds), (s["proceeds_c"], s["collateral_c"], s["fund_cash_c"]))
        self.assertEqual([("SHRT", -1000, 5500000, 5610000)], [(r["symbol"], r["qty"], r["short_mv_c"], r["required_c"]) for r in s["rows"]])
        # the stock lender in the counterparty view: cash posted 56,100.00, requirement 102 % of the short value 55,000.00 = 56,100.00
        g = [x for x in cc["summary"]["by_counterparty"] if x["type"] == "STOCK_LENDER"]
        self.assertEqual([(lender, 5610000, 5610000, 5500000)], [(x["counterparty"], x["cash_posted_c"], x["requirement_c"], x["exposure_c"]) for x in g])
        # the position's requirement (C16): Reg T 50 % of 55,000.00 = 27,500.00; the lender's collateral 56,100.00
        req = collateral.position_requirements(rt, US_EXCHANGE)
        self.assertEqual((2750000, 5610000), (req["SHRT"]["margin_c"], req["SHRT"]["collateral_c"]))
        assert_integrity(self, rt)

    def test_custody_map_and_statement(self):
        """PORTFOLIO-18: the custodian of each account kind (the simulator's assumed map) and the statement grouped by custodian."""
        rt = self.rt
        with rt.db.reader() as conn:
            rows = {r["role"]: dict(r) for r in conn.execute("SELECT * FROM accounts WHERE lifetime_id='T1'").fetchall()}
        self.assertEqual({"TREASURY": "bank", "US_EXCHANGE": "PB custody, DVP", "DEBT": "custodian bank, DVP", "CREDIT": "bank",
                          "FOREIGN_FX": "global custodian"},
                         {k: collateral.custodian_of(v)["custodian"] for k, v in rows.items() if k in ("TREASURY", "US_EXCHANGE", "DEBT", "CREDIT", "FOREIGN_FX")})
        self.assertEqual("Goldman Sachs: bilateral ISDA / CSA",
                         collateral.custodian_of({"kind": "PB", "role": "PB_GS", "name": "Goldman Sachs (PB)", "broker": "GS"})["custodian"])
        # a long equity at PB custody, cash at the bank
        sh.add_equity(rt, "LONGX", 5000000)
        ok(self, buy(rt, US_EXCHANGE, "LONGX", 100, "50.00", fee_c=0))
        rt.marks.set("LONGX", "52.00")
        st = collateral.custody_statement(rt, f"{T1}:ALL")
        groups = {g["custodian"]: g for g in st["groups"]}
        self.assertEqual([("LONGX", 100, 520000)], [(r["symbol"], r["qty"], r["mv_c"]) for r in groups["PB custody, DVP"]["rows"]])
        self.assertEqual(bal(rt, TREASURY, "1010"), sum(r["mv_c"] for r in groups["bank"]["rows"] if r["account_id"] == TREASURY))

    def test_start_forward_no_call_for_a_day_whose_1720_came_before_the_first_bind(self):
        """15.9 item 8: the runtime first binds at Wed 18:00 ET (after Wed's 17:20): no call key for Wed; Thu 17:20 is the first."""
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.tmp = tempfile.mkdtemp(prefix="fst-mcall-")
        rt = self.rt = runtime(self.tmp, at="2026-10-21T18:00:00-04:00")
        ok(self, execute(rt, "test.fut_vm", {"account_id": US_EXCHANGE, "instrument_id": "GCZ26", "date": "2026-10-21", "loss_c": 100000}, actor="lifecycle"))
        out = rt.lifecycle.tick(at="2026-10-21T18:01:00-04:00", only="MARGIN_CALL")
        self.assertEqual([], out["ran"])
        out = rt.lifecycle.tick(at="2026-10-22T12:00:00-04:00", only="MARGIN_CALL")
        self.assertEqual([], out["ran"])
        out = rt.lifecycle.tick(at="2026-10-22T17:21:00-04:00", only="MARGIN_CALL")
        self.assertEqual([f"{US_EXCHANGE}:2026-10-22"], [i["key"] for i in out["ran"]])
        self.assertEqual([], calls(rt))           # Wed's VM is outside Thu's window (after Wed through Thu): never called
        start = sc.q(rt, "SELECT value FROM collateral_state WHERE key='margin_call_start_ts'")[0]["value"]
        self.assertEqual(str(epoch_of(date(2026, 10, 21), "18:00")), start)          # the first bind: Wed 18:00 ET


class ImportedBookTests(unittest.TestCase):
    """15.9 item 8 on a copy that holds an applied import (P5's 9-line mini batch, applied on Tue 10-27 10:00 ET): the owner's import ran
    under 1.0.5, which had no MARGIN_CALL job and no start marker; 1.0.6 is installed on Wed 10-28 at 18:00 ET.  The job then creates no
    call for any date before its install - not for the import's as-of variation margin (business dates 10-21 .. 10-26), not for 10-27 /
    10-28 - and runs forward from Thu 10-29 17:20 ET."""

    def setUp(self):
        from tests import test_terminal_import as tim
        self.tim = tim
        self.tmp = tempfile.mkdtemp(prefix="fst-mcall-import-")
        self.home = os.path.join(self.tmp, "home")
        tim.seed(os.path.join(self.tmp, "seed"))                          # the seeded world through 10-26 (FakeClock, FakeProvider)
        self.db = tim.clone(os.path.join(self.tmp, "seed"), self.home)
        self._as_105()                                                     # the world as 1.0.5 left it: no 1.0.6 job history, no marker
        pack = tim.write_pack_for(os.path.join(self.tmp, "pack.json"))
        clock = tim.FakeClock(tim.APPLY_AT)
        with tim.quiet():
            r = tim.im.dry_run(tim.MINI_BATCH, self.home, pack, work_dir=os.path.join(self.tmp, "dry1"), clock=clock)
            self.assertEqual("DONE", r["status"], r.get("refused"))
            r = tim.im.apply(tim.MINI_BATCH, self.home, pack, "test: the owner's go", dryrun_dir=os.path.join(self.tmp, "dry1"), clock=clock)
        self.assertEqual("DONE", r["status"], r.get("refused"))
        self._as_105()                                                     # the apply ran on 1.0.5 (its runtime wrote no 1.0.6 marker)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _as_105(self):
        """Test surgery on the temp copy only: the seed and the import tool run this tree's code (1.0.6), so the rows its new job and its
        start marker wrote are removed to stand for a database 1.0.5 left behind (its schema has none of them)."""
        con = sqlite3.connect(self.db)
        try:
            for sql in ("DELETE FROM collateral_state", "DELETE FROM margin_calls", "DELETE FROM jobs_done WHERE job='MARGIN_CALL'"):
                con.execute(sql)
            con.commit()
        finally:
            con.close()

    def test_no_call_before_the_install_then_forward(self):
        tim = self.tim
        q = lambda sql, p=(): tim.db_q(self.db, sql, p)   # noqa: E731
        # the import's as-of variation margin is in the ledger, dated 10-21 .. 10-26
        vm = q("SELECT business_date FROM journal_entries WHERE idem_key LIKE 'swapvm:%' ORDER BY business_date")
        self.assertTrue(vm and vm[0]["business_date"] == "2026-10-21", vm)
        rt = tim.build(self.home, "2026-10-28T18:00:00-04:00")            # 1.0.6 installed: the first bind
        try:
            self.assertEqual("bound", rt.modules.get("collateral"))
            out = rt.lifecycle.tick(at="2026-10-28T18:01:00-04:00")
            self.assertEqual([], [i for i in out["ran"] if i["job"] == "MARGIN_CALL"])
            self.assertEqual([], q("SELECT * FROM margin_calls"))
            out = rt.lifecycle.tick(at="2026-10-29T12:00:00-04:00")
            self.assertEqual([], [i for i in out["ran"] if i["job"] == "MARGIN_CALL"])
            out = rt.lifecycle.tick(at="2026-10-29T17:30:00-04:00")
            keys = sorted(i["key"] for i in out["ran"] if i["job"] == "MARGIN_CALL")
            # every US Exchange / prime-broker account of the three books, for Thu only
            self.assertEqual(["F1:PB_GS:2026-10-29", "F1:US_EXCHANGE:2026-10-29", "I1:US_EXCHANGE:2026-10-29", "P1:US_EXCHANGE:2026-10-29"], keys)
            self.assertEqual([], [c for c in q("SELECT business_date FROM margin_calls") if c["business_date"] < "2026-10-29"])
            self.assertEqual([], q("SELECT key FROM jobs_done WHERE job='MARGIN_CALL' AND substr(key, -10) < '2026-10-29'"))
            rep = rt.accounting.integrity("full")
            self.assertTrue(rep.ok, rep.failing)
        finally:
            rt.close()


if __name__ == "__main__":
    unittest.main()
