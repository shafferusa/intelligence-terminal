"""FINSIM1 P6 (phase 1, Part A) collateral tests: restricted cash net per CSA (C21, D9, PORTFOLIO-10), the as-of funding
commands of the historical import (C15: ``cash.asof_sweep``, ``cash.asof_transfer``, Treasury stamping and ``sweep_in`` under
``ctx.asof_date``), the re-accrual (C17), the lender wording (C16 ``lending.lender_label``) and the dating of the 17:10
collateral mark (POSTCLOSE-06).  Acceptance items 1 (b), (e), (h), (i), (j) and 2 of FINSIM1_PLAN P6 (15.3).

Every expected number is worked by hand in a comment.  No network: FakeClock, FixedMarks, a RateStore with SOFR prints, a temp
database.  The OTC contracts here are minimal CTM rows whose variation margin is posted the way SWAP_VM posts it (received
``Dr 1010 / Cr 2330``, posted ``Dr 1320 / Cr 1010``, ``vm_balance_c`` = the ledger, so L22 holds); the swap modules are not
bound."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from datetime import date
from decimal import Decimal

from tests.terminal_helpers import DEBT, T1, TREASURY, US_EXCHANGE, assert_integrity, bal, deposit, execute, fund, make_runtime, pb
from tests import test_terminal_api as ta
from tests import test_terminal_lifecycle as lc
from tests import test_terminal_orders as sc
from tests import test_terminal_shorts as sh
from finsim.terminal import accounting, collateral, execution, lending, prime, repo, treasury
from finsim.terminal.db import next_id
from finsim.terminal.ledger import Entry, cr, dr
from finsim.terminal.lifecycle_jobs import with_asof

D = Decimal
TUE_10 = "2026-10-20T10:00:00-04:00"
TUE_14 = "2026-10-20T14:00:00-04:00"
MON_12 = "2026-10-27T12:00:00-04:00"
PB = pb("GS")
SCHEMA_1031 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "terminal", "schema_1031.sql")


# ---- test-only handlers -------------------------------------------------------------------------------------------------
def _h_otc_vm(ctx, payload):
    """A CTM OTC contract row (created on first use) and one variation-margin movement on ``business_date``: ``delta_c`` > 0
    received ``Dr 1010 / Cr 2330``, < 0 posted ``Dr 1320 / Cr 1010`` (one-signed per contract here); ``vm_balance_c`` / ``mark_c``
    follow the ledger so L22 holds."""
    aid, cid, d, delta = payload["account_id"], payload["contract_id"], payload["business_date"], int(payload["delta_c"])
    row = ctx.conn.execute("SELECT vm_balance_c FROM otc_contracts WHERE contract_id=?", (cid,)).fetchone()
    if row is None:
        ctx.conn.execute("INSERT INTO otc_contracts(contract_id, command_id, account_id, broker, kind, terms_json, notional_c, currency, start_date, maturity_date,"
                         " margin_mode, im_c, status, mark_c, vm_balance_c) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (cid, ctx.command_id, aid, "GS", payload.get("kind", "TRS"), "{}", 500000000, "USD", d, "2027-10-21", "CTM", 0, "OPEN", 0, 0))
        old = 0
    else:
        old = int(row["vm_balance_c"])
    if delta > 0:
        lines = [dr(aid, "1010", "USD", delta, memo="VM received"), cr(aid, "2330", "USD", delta, instrument_id=cid, memo="VM received")]
    else:
        lines = [dr(aid, "1320", "USD", -delta, instrument_id=cid, memo="VM posted"), cr(aid, "1010", "USD", -delta, memo="VM posted")]
    eid = ctx.post(Entry("SWAP_VM", f"test VM {cid} {d} {delta}", lines, f"testvm:{cid}:{d}:{ctx.request_id}", ref_type="otc", ref_id=cid, business_date=d))
    ctx.conn.execute("UPDATE otc_contracts SET vm_balance_c=?, mark_c=? WHERE contract_id=?", (old + delta, old + delta, cid))
    return {"entry_id": eid}


def _h_asof_xfer(ctx, payload):
    """An internal transfer dated ``business_date`` (what an as-of booking's own funding looks like): Treasury stamping (C15)."""
    return with_asof(ctx, payload["business_date"], lambda: treasury._post_transfer(ctx, "TRANSFER", payload["from_account"], payload["to_account"], "USD",
                                                                                     int(payload["amount_c"]), payload.get("memo", "test as-of transfer")))


def _h_sweep_asof(ctx, payload):
    """``treasury.sweep_in`` under ``ctx.asof_date`` = ``business_date`` and the payment it funds (idem = the sweep's link, L19)."""
    d, aid, amt = payload["business_date"], payload["account_id"], int(payload["amount_c"])
    link = f"testpay:{ctx.request_id}"
    tid = with_asof(ctx, d, lambda: treasury.sweep_in(ctx, aid, "USD", amt, "test", link, amt))
    eid = ctx.post(Entry("TEST", f"test payment {amt}", [dr(aid, "5000", "USD", amt, instrument_id="TEST:PAY"), cr(aid, "1010", "USD", amt)], link,
                         business_date=d))
    return {"transfer_id": tid, "entry_id": eid}


def _h_pay(ctx, payload):
    """An immediate payment of ``amount_c`` funded through ``execution.fund`` (``csa_netting`` as given): a VM payment to the CSA
    counterparty passes True, any other need False.  Posted ``Dr 5000 / Cr 1010`` with idem = the funding link (L19)."""
    aid, amt = payload["account_id"], int(payload["amount_c"])
    link = f"testpay:{ctx.request_id}"
    res = execution.fund(ctx, aid, "USD", amt, "test", link, None, immediate=True, csa_netting=bool(payload.get("csa_netting")), services=ctx.services)
    ctx.post(Entry("TEST", f"test payment {amt}", [dr(aid, "5000", "USD", amt, instrument_id="TEST:PAY"), cr(aid, "1010", "USD", amt)], link))
    return {"funding": res.to_dict()}


def _h_cash_settlement(ctx, payload):
    """A cash settlement row that is not a fill's: ``amount_c`` < 0 a payable (``Dr 5000 / Cr 2100``), > 0 a receivable
    (``Dr 1200 / Cr 4450``), due ``due_date``, with ``ref_type`` / ``ref_id`` (an OTC payment: the CDX initial payment's shape)
    or ``fx_deal_id``; L10 holds (the pending row equals its balance)."""
    aid, amt, ref = payload["account_id"], int(payload["amount_c"]), payload.get("ref_id") or "TEST"
    gl = payload.get("gl") or ("2100" if amt < 0 else "1200")
    if amt < 0:
        lines = [dr(aid, "5000", "USD", -amt, instrument_id=ref), cr(aid, gl, "USD", -amt)]
    else:
        lines = [dr(aid, gl, "USD", amt), cr(aid, "4450", "USD", amt, instrument_id=ref)]
    eid = ctx.post(Entry("TEST", f"test cash settlement {amt}", lines, f"testsettle:{ctx.request_id}", ref_type=payload.get("ref_type"), ref_id=ref))
    sid = next_id(ctx.conn, "settlement")
    ctx.conn.execute("INSERT INTO settlements(settle_id, fill_id, fx_deal_id, account_id, leg, currency, amount_c, gl, due_date, status, ref_type, ref_id)"
                     " VALUES (?,?,?,?,?,?,?,?,?,'PENDING',?,?)", (sid, None, payload.get("fx_deal_id"), aid, "CASH", "USD", amt, gl, payload["due_date"],
                                                                 payload.get("ref_type"), payload.get("ref_id")))
    if payload.get("contract_booking"):
        ctx.conn.execute("INSERT INTO otc_contracts(contract_id, command_id, account_id, broker, kind, terms_json, notional_c, currency, start_date, maturity_date,"
                         " margin_mode, im_c, status, mark_c, vm_balance_c, booking) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (ref, ctx.command_id, aid, "GS", "CDX", "{}", 300000000, "USD", "2026-10-21", "2031-12-20", "CTM", 0, "OPEN", 0, 0, payload["contract_booking"]))
    return {"settle_id": sid, "entry_id": eid}


def runtime(tmp, at=TUE_10):
    """The lifecycle runtime (orders, lending, credit, futures, options, SOFR prints, the scheduler) + prime + collateral."""
    rt = lc.runtime(tmp, at=at)
    prime.register(rt.bus)
    prime.bind(rt)
    collateral.register(rt.bus)
    collateral.bind(rt)
    rt.collateral = collateral
    for kind, fn in (("test.otc_vm", _h_otc_vm), ("test.asof_xfer", _h_asof_xfer), ("test.cash_settlement", _h_cash_settlement),
                     ("test.sweep_asof", _h_sweep_asof), ("test.pay", _h_pay)):
        rt.bus.register(kind, fn)
    return rt


def ok(tc, r):
    tc.assertTrue(r.ok, r.body)
    return r.body


def open_pb(tc, rt, deposit_c=1500000000):
    """T1 (a FUND at 10,000,000.00) needs NAV at the 25,000,000.00 PB / OTC threshold: a deposit of 15,000,000.00 first."""
    ok(tc, deposit(rt, deposit_c))
    return ok(tc, execute(rt, "account.open_pb", {"lifetime_id": T1, "broker": "GS"}))


def vm(tc, rt, cid, delta_c, d, kind="TRS", account=PB):
    return ok(tc, execute(rt, "test.otc_vm", {"account_id": account, "contract_id": cid, "delta_c": int(delta_c), "business_date": d, "kind": kind}))["entry_id"]


def one(rt, sql, params=()):
    return sc.one(rt, sql, params)


class RestrictedCashTests(unittest.TestCase):
    """C21 / D9 / acceptance 1 (b): restricted = max(0, VM received - VM posted) of the PB account + securities-lending cash collateral."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-coll-")
        self.rt = None

    def tearDown(self):
        if self.rt is not None:
            self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _gs_with_vm(self):
        # Tue 10-20 10:00: PB GS funded 400,000.00 from Treasury; TRS VM received 600,921.00 (SWP-T); CDX / IRS VM posted 309,331.86 (SWP-C)
        # -> PB settled 400,000.00 + 600,921.00 - 309,331.86 = 691,589.14; restricted 600,921.00 - 309,331.86 = 291,589.14
        rt = self.rt = runtime(self.tmp)
        open_pb(self, rt)
        ok(self, fund(rt, PB, 40000000))
        vm(self, rt, "SWP-T", 60092100, "2026-10-20")
        vm(self, rt, "SWP-C", -30933186, "2026-10-20", kind="CDX")
        self.assertEqual(69158914, bal(rt, PB, "1010"))
        return rt

    def test_b_restricted_net_per_csa_available_sweep_and_credit_base(self):
        rt = self._gs_with_vm()
        with rt.db.reader() as conn:
            parts = collateral.restricted_parts(conn, PB, "USD")
            self.assertEqual((60092100, 30933186, 29158914, False, 29158914, 0, 29158914),
                             (parts["vm_received_c"], parts["vm_posted_c"], parts["csa_net_c"], parts["reuse_allowed"], parts["csa_restricted_c"],
                              parts["seclend_cash_c"], parts["restricted_c"]))
            self.assertEqual(29158914, collateral.restricted_c(conn, PB, "USD"))
            self.assertEqual(0, collateral.restricted_c(conn, PB, "USD", csa_netting=True))          # a payment to GS may use GS's collateral
            self.assertEqual(0, collateral.restricted_c(conn, US_EXCHANGE, "USD"))
            # as of the day before (10-19) nothing was received yet
            self.assertEqual(0, collateral.restricted_c(conn, PB, "USD", as_of="2026-10-19"))
        # C9b: the CashView lists the restriction and available excludes it: 691,589.14 - 291,589.14 = 400,000.00
        cv = rt.accounting.cash(PB)["USD"]
        self.assertEqual((69158914, 40000000), (cv.settled_c, cv.available_c))
        self.assertEqual([{"key": "restricted", "label": "restricted (collateral received)", "amount_c": 29158914}],
                         [a for a in cv.adjustments if a["key"] == "restricted"])
        self.assertEqual(40000000, rt.accounting.buying_power(PB, "USD").available_c)
        # 17:30 SWEEP_OUT: excess = 691,589.14 - restricted 291,589.14 - payables 0 - reserved 0 - maintenance 0 - buffer 10,000.00 = 390,000.00
        # (without the rule it would have swept 681,589.14: the counterparty's collateral is never swept)
        out = lc.tick(rt, "2026-10-20T17:30:00-04:00", only="SWEEP_OUT")
        got = {i["key"]: i["status"] for i in out["ran"]}
        self.assertEqual("DONE", got[f"{PB}:USD:2026-10-20"])
        self.assertEqual(39000000, one(rt, "SELECT amount_c FROM transfers WHERE kind='SWEEP_OUT' AND from_account=?", (PB,))["amount_c"])
        self.assertEqual(29158914 + 1000000, bal(rt, PB, "1010"))                                    # restricted + the buffer stay
        item = next(i for i in out["ran"] if i["key"] == f"{PB}:USD:2026-10-20")
        stored = json.loads(one(rt, "SELECT result_json FROM commands WHERE request_id=?", (item["request_id"],))["result_json"])
        self.assertEqual((69158914, 29158914, 39000000), (stored["formula"]["settled_c"], stored["formula"]["adjustments_c"], stored["formula"]["excess_c"]))
        # night 10-20 PB credit interest (Wed 00:35): base = max(0, settled 691,589.14 at the close of 10-20 - restricted 291,589.14) = 400,000.00 (the
        # 17:30 sweep is business date 10-21); rate SOFR 3.87 (obs 10-19) - 0.75 = 3.12 %; to_c(400,000.00 x 0.0312 / 360) = to_c(34.6666) = 34.67
        # (on the whole 691,589.14 it would have been to_c(59.937) = 59.94)
        out = lc.tick(rt, "2026-10-21T00:35:00-04:00", only="ACCRUE_DAILY")
        self.assertIn(f"pbcredit:{PB}:2026-10-20", [i["key"] for i in out["ran"]])
        self.assertEqual([("1230", 3467, None), ("4300", -3467, "CASH:USD")], sh.lines_of(rt, f"accr:pbcredit:{PB}:2026-10-20"))
        ev = [json.loads(r["payload_json"]) for r in sc.q(rt, "SELECT payload_json FROM events WHERE type='ACCRUED' AND account_id=?", (PB,))]
        self.assertEqual([(40000000, "0.0312")], [(e["base_c"], e["rate"]) for e in ev if e["kind"] == "pbcredit"])
        assert_integrity(self, rt, "full")

    def test_b_csa_netting_vm_payment_may_use_collateral_other_needs_may_not(self):
        rt = self._gs_with_vm()
        t0 = bal(rt, TREASURY, "1010")
        # a VM payment of 250,000.00 to GS (csa_netting): own cash = settled 691,589.14 - only securities-lending collateral 0.00 -> paid from own cash
        f = ok(self, execute(rt, "test.pay", {"account_id": PB, "amount_c": 25000000, "csa_netting": True}))["funding"]
        self.assertEqual(("own", 0, 69158914), (f["source"], f["sweep_c"], f["own_c"]))
        self.assertEqual(44158914, bal(rt, PB, "1010"))
        # any other immediate need of 250,000.00 (a borrow collateral top-up, a settlement): own = 441,589.14 - restricted 291,589.14 = 150,000.00
        # -> one Treasury sweep of 250,000.00 - 150,000.00 = 100,000.00
        f = ok(self, execute(rt, "test.pay", {"account_id": PB, "amount_c": 25000000}))["funding"]
        self.assertEqual(("sweep", 10000000, 15000000), (f["source"], f["sweep_c"], f["own_c"]))
        self.assertEqual((29158914, t0 - 10000000), (bal(rt, PB, "1010"), bal(rt, TREASURY, "1010")))
        # what is left is exactly GS's collateral: available 0.00, and the 17:30 sweep keeps it: 291,589.14 - 291,589.14 - 10,000.00 = -10,000.00
        self.assertEqual(0, rt.accounting.cash(PB)["USD"].available_c)
        out = lc.tick(rt, "2026-10-20T17:30:00-04:00", only="SWEEP_OUT")
        note = {i["key"]: i["note"] for i in out["ran"]}[f"{PB}:USD:2026-10-20"]
        self.assertTrue(note.startswith("nothing idle: 291,589.14 - 0.00 - 0.00 - 0.00 - 10,000.00 - restricted 291,589.14 = -10,000.00 USD"), note)
        assert_integrity(self, rt, "full")

    def test_b_otc_settlement_payment_to_the_csa_counterparty_may_use_its_collateral(self):
        rt = self._gs_with_vm()
        rt.db.ensure_schema_ext("test", ["ALTER TABLE otc_contracts ADD COLUMN booking TEXT"])
        # two payables due Wed 10-21 in PB GS: an OTC payment of 500,000.00 to GS (ref_type otc, a CDX upfront's shape) and a trade settlement of
        # 450,000.00; settled 691,589.14 of which GS's collateral 291,589.14
        o = ok(self, execute(rt, "test.cash_settlement", {"account_id": PB, "amount_c": -50000000, "due_date": "2026-10-21", "ref_type": "otc", "ref_id": "SWP-T"}))
        t = ok(self, execute(rt, "test.cash_settlement", {"account_id": PB, "amount_c": -45000000, "due_date": "2026-10-21"}))
        t0 = bal(rt, TREASURY, "1010")
        lc.tick(rt, "2026-10-21T00:35:00-04:00", only="SETTLE_DUE")
        # the OTC payment may use GS's collateral: own 691,589.14 >= 500,000.00 -> no sweep, PB 191,589.14;
        # the trade settlement may not: own 191,589.14 - 291,589.14 = -100,000.00 -> one sweep of 450,000.00 + 100,000.00 = 550,000.00
        sweeps = {s["linked_id"]: s["amount_c"] for s in sc.q(rt, "SELECT linked_id, amount_c FROM transfers WHERE kind='SWEEP_IN' AND linked_type='settlement'")}
        self.assertEqual({t["settle_id"]: 55000000}, sweeps)
        self.assertEqual((29158914, t0 - 55000000), (bal(rt, PB, "1010"), bal(rt, TREASURY, "1010")))     # what is left is exactly GS's collateral
        self.assertEqual(("SETTLED", "SETTLED"), (one(rt, "SELECT status FROM settlements WHERE settle_id=?", (o["settle_id"],))["status"],
                                                  one(rt, "SELECT status FROM settlements WHERE settle_id=?", (t["settle_id"],))["status"]))
        assert_integrity(self, rt, "full")

    def test_b_reuse_flag_off_by_default_and_editable(self):
        rt = self._gs_with_vm()
        with rt.db.reader() as conn:
            self.assertFalse(collateral.reuse_allowed(conn, PB))
        r = execute(rt, "collateral.csa_terms", {"account_id": PB, "reuse_allowed": True})
        self.assertEqual({"account_id": PB, "reuse_allowed": True, "before": False}, ok(self, r))
        # reuse allowed: only securities-lending collateral (none) stays restricted -> available = settled 691,589.14
        self.assertEqual((0, 69158914), (rt.accounting.cash(PB)["USD"].adjustments_total_c, rt.accounting.cash(PB)["USD"].available_c))
        ok(self, execute(rt, "collateral.csa_terms", {"account_id": PB, "reuse_allowed": False}))
        self.assertEqual(40000000, rt.accounting.cash(PB)["USD"].available_c)
        r = execute(rt, "collateral.csa_terms", {"account_id": US_EXCHANGE, "reuse_allowed": True})
        self.assertEqual((400, "NOT_PB"), (r.http, r.code))
        ev = sc.q(rt, "SELECT payload_json FROM events WHERE type='SETTINGS_CHANGED' AND ref_type='csa'")
        self.assertEqual([({"reuse_allowed": False}, {"reuse_allowed": True}), ({"reuse_allowed": True}, {"reuse_allowed": False})],
                         [(json.loads(e["payload_json"])["before"], json.loads(e["payload_json"])["after"]) for e in ev])

    def test_b_securities_lending_cash_collateral_stays_restricted(self):
        # Thu 10-15: buy 1,000 ABC at 50.00 (settles Fri); Fri 10:00 lend them through BNY: 51,000.00 cash collateral received (Dr 1010 / Cr 2520).
        # Without the rule US Exchange's available rose by the 51,000.00 the borrower posted; with it, available is unchanged.
        rt = self.rt = runtime(self.tmp, at="2026-10-15T10:00:00-04:00")
        rt.bus.register("test.settle_due", sh._h_settle_due)
        sh.setup_abc(rt, fund_c=10000000)
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="ABC", side="BUY", qty=1000, order_type="MARKET")
        self.assertEqual("FILLED", r.body["order"]["status"], r.body)
        rt.clock.set("2026-10-16T00:35:00-04:00")
        ok(self, execute(rt, "test.settle_due", {"as_of": "2026-10-16"}))
        rt.clock.set("2026-10-16T10:00:00-04:00")
        sh.quote(rt, "ABC", "50.00")
        before = rt.accounting.cash(US_EXCHANGE)["USD"]
        # settled 100,000.00 - 50,000.00 - fee 5.00 = 49,995.00; nothing restricted yet
        self.assertEqual((4999500, 4999500, 0), (before.settled_c, before.available_c, before.adjustments_total_c))
        x = execute(rt, "credit.lend_securities", {"account_id": US_EXCHANGE, "instrument_id": "ABC", "qty": 1000, "counterparty_id": "AGENT_BNY"})
        self.assertTrue(x.ok, x.body)
        after = rt.accounting.cash(US_EXCHANGE)["USD"]
        # settled 49,995.00 + 51,000.00 = 100,995.00; restricted 51,000.00 (2520, never netted) -> available 49,995.00, unchanged
        self.assertEqual((10099500, 4999500, 5100000), (after.settled_c, after.available_c, after.adjustments_total_c))
        with rt.db.reader() as conn:
            self.assertEqual((5100000, 5100000), (collateral.restricted_c(conn, US_EXCHANGE, "USD"), collateral.restricted_c(conn, US_EXCHANGE, "USD", csa_netting=True)))
        assert_integrity(self, rt, "full")

    def test_adjuster_reads_zero_on_a_database_where_collateral_never_bound(self):
        # the C9b registry is process-wide: a runtime that never bound this module (no csa_terms table) restricts nothing
        rt = self.rt = make_runtime(self.tmp)
        collateral._register_adjuster(rt)
        with rt.db.reader() as conn:
            self.assertFalse(collateral.schema_ready(conn))
            self.assertEqual(0, collateral.restricted_adjuster(conn, US_EXCHANGE, "USD"))
        self.assertIn(("restricted", "restricted (collateral received)"), accounting.cash_adjusters())


class AsOfFundingTests(unittest.TestCase):
    """C15 as-of dating, ``cash.asof_sweep`` (h), ``cash.asof_transfer`` (i) and ``treasury.sweep_in`` under ``ctx.asof_date`` (j)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-asof-")
        self.rt = None

    def tearDown(self):
        if self.rt is not None:
            self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_h_asof_sweep_hand_worked(self):
        rt = self.rt = runtime(self.tmp, at=TUE_14)
        open_pb(self, rt)
        rt.clock.set(MON_12)
        # the import's own postings in PB GS (the line groups), dated D = 10-22 unless said:
        #   e1 its own funding: Treasury -> PB 606,223.95 (10-22); e2 VM received 489,357.00 (SWP-T, 10-22); e3 VM posted 309,027.55 (SWP-C, 10-22);
        #   e4 VM received 111,259.69 (SWP-T, 10-23)
        # close of 10-22: settled 606,223.95 + 489,357.00 - 309,027.55 = 786,553.40; restricted 489,357.00 - 309,027.55 = 180,329.45
        # today: 2330 600,616.69 - 1320 309,027.55 = restricted 291,589.14
        x1 = ok(self, execute(rt, "test.asof_xfer", {"from_account": TREASURY, "to_account": PB, "amount_c": 60622395, "business_date": "2026-10-22"}))
        e2 = vm(self, rt, "SWP-T", 48935700, "2026-10-22")
        e3 = vm(self, rt, "SWP-C", -30902755, "2026-10-22", kind="CDX")
        e4 = vm(self, rt, "SWP-T", 11125969, "2026-10-23")
        self.assertEqual("2026-10-22", one(rt, "SELECT business_date FROM transfers WHERE transfer_id=?", (x1["transfer_id"],))["business_date"])
        # the owner's own idle cash: Treasury -> US Exchange 519,392.94 dated 10-22 (not the import's): excess 519,392.94 - buffer 10,000.00 = 509,392.94
        ok(self, execute(rt, "test.asof_xfer", {"from_account": TREASURY, "to_account": US_EXCHANGE, "amount_c": 51939294, "business_date": "2026-10-22"}))
        with rt.db.reader() as conn:
            self.assertEqual((78655340, 18032945, 29158914), (collateral.bd_balance_c(conn, PB, "1010", "USD", "2026-10-22"),
                                                              collateral.restricted_c(conn, PB, "USD", as_of="2026-10-22"), collateral.restricted_c(conn, PB, "USD")))
        groups = [{"key": "L24-L28", "label": "the OTC lines", "refs": {"entry_ids": [x1["entry_id"], e2, e3, e4]}}]
        payload = {"lifetime_id": T1, "accounts": [PB, US_EXCHANGE, TREASURY], "from_date": "2026-10-21", "through_date": "2026-10-23", "batch_id": "IMP-T-1",
                   "groups": groups}
        r = execute(rt, "cash.asof_sweep", payload)
        self.assertEqual((403, "AS_OF_FORBIDDEN"), (r.http, r.code))                                 # the terminal's own screens never run it
        t_before = bal(rt, TREASURY, "1010")
        body = ok(self, execute(rt, "cash.asof_sweep", payload, actor="import"))
        # 10-22, PB: excess = 786,553.40 - restricted as of 10-22 180,329.45 - payables 0.00 - buffer 10,000.00 = 596,223.95; the import's cash 786,553.40
        # -> one SWEEP_OUT of min(596,223.95, 786,553.40) = 596,223.95 dated 10-22 (today's restricted 291,589.14 would have swept only 484,964.26)
        self.assertEqual([(PB, "2026-10-22", 59622395)], [(p["account_id"], p["date"], p["sweep_c"]) for p in body["posted"]])
        p = body["posted"][0]
        self.assertEqual((78655340, 18032945, 0, 1000000, 59622395, 78655340), (p["settled_c"], p["restricted_c"], p["payables_c"], p["buffer_c"], p["excess_c"],
                                                                              p["import_cash_c"]))
        t = one(rt, "SELECT * FROM transfers WHERE idem_key=?", (f"asofsweep:IMP-T-1:{PB}:USD:2026-10-22",))
        self.assertEqual(("SWEEP_OUT", PB, TREASURY, 59622395, "2026-10-22", "asof_sweep", "IMP-T-1"),
                         (t["kind"], t["from_account"], t["to_account"], t["amount_c"], t["business_date"], t["linked_type"], t["linked_id"]))
        e = one(rt, "SELECT * FROM journal_entries WHERE entry_id=?", (t["entry_id"],))
        self.assertEqual(("2026-10-22", f"asofsweep:IMP-T-1:{PB}:USD:2026-10-22"), (e["business_date"], e["idem_key"]))
        self.assertIn("as-of sweep", e["memo"])
        # 10-21: PB held nothing; 10-23: settled 786,553.40 - 596,223.95 + 111,259.69 = 301,589.14 = restricted 291,589.14 + buffer 10,000.00 -> nothing;
        # US Exchange: 509,392.94 of excess but no import cash -> never swept (pre-import cash stays where real time left it)
        skipped = {(s["account_id"], s["date"]): s for s in body["skipped"] if s.get("currency") == "USD"}
        self.assertEqual((0, 0), (skipped[(PB, "2026-10-23")]["excess_c"], skipped[(PB, "2026-10-23")]["sweep_c"]))
        self.assertEqual((50939294, 0), (skipped[(US_EXCHANGE, "2026-10-22")]["excess_c"], skipped[(US_EXCHANGE, "2026-10-22")]["import_cash_c"]))
        self.assertEqual(t_before + 59622395, bal(rt, TREASURY, "1010"))
        # PB at the close of 10-22 now: 786,553.40 - 596,223.95 = 190,329.45 = restricted 180,329.45 + buffer 10,000.00; at the close of 10-23
        # 301,589.14 = restricted 291,589.14 + buffer 10,000.00
        with rt.db.reader() as conn:
            self.assertEqual((19032945, 1000000), (collateral.bd_balance_c(conn, PB, "1010", "USD", "2026-10-22"),
                                                   collateral.bd_balance_c(conn, PB, "1010", "USD", "2026-10-23") - 29158914))
        # a rerun (a new request id: the second dry run on the result) posts nothing
        n = one(rt, "SELECT COUNT(*) n FROM journal_entries")["n"]
        again = ok(self, execute(rt, "cash.asof_sweep", payload, actor="import"))
        self.assertEqual(([], n), (again["posted"], one(rt, "SELECT COUNT(*) n FROM journal_entries")["n"]))
        # the same groups named by their contracts and transfer resolve to the same entries
        with rt.db.reader() as conn:
            self.assertEqual({x1["entry_id"], e2, e3, e4}, collateral.group_entry_ids(conn, [{"refs": {"contract_ids": ["SWP-T", "SWP-C"],
                                                                                                         "transfer_ids": [x1["transfer_id"]]}}]))
        assert_integrity(self, rt, "full")

    def test_h_asof_sweep_refuses_to_overdraw_today(self):
        # the import's cash at the close of 10-22 left the account again on 10-23 (business date): sweeping it as of 10-22 would leave
        # today's settled cash at 200,000.00 - 200,000.00 + ... below zero -> 409 ASOF_CURRENT_FUNDS, nothing written
        rt = self.rt = runtime(self.tmp, at=TUE_14)
        open_pb(self, rt)
        rt.clock.set(MON_12)
        x1 = ok(self, execute(rt, "test.asof_xfer", {"from_account": TREASURY, "to_account": PB, "amount_c": 20000000, "business_date": "2026-10-22"}))
        x2 = ok(self, execute(rt, "test.asof_xfer", {"from_account": PB, "to_account": TREASURY, "amount_c": 19500000, "business_date": "2026-10-23"}))
        payload = {"lifetime_id": T1, "accounts": [PB], "from_date": "2026-10-22", "through_date": "2026-10-22", "batch_id": "IMP-T-2",
                   "groups": [{"refs": {"entry_ids": [x1["entry_id"], x2["entry_id"]]}}]}
        n = one(rt, "SELECT COUNT(*) n FROM journal_entries")["n"]
        # close of 10-22: settled 200,000.00 - 10,000.00 buffer = 190,000.00 to sweep; today PB holds 5,000.00 -> 5,000.00 - 190,000.00 < 0
        r = execute(rt, "cash.asof_sweep", payload, actor="import")
        self.assertEqual((409, "ASOF_CURRENT_FUNDS", 500000), (r.http, r.code, r.body["error"]["details"]["current_settled_c"]))
        self.assertEqual(n, one(rt, "SELECT COUNT(*) n FROM journal_entries")["n"])

    def test_i_asof_transfer(self):
        rt = self.rt = runtime(self.tmp, at=TUE_14)
        rt.clock.set(MON_12)
        ok(self, execute(rt, "test.asof_xfer", {"from_account": TREASURY, "to_account": US_EXCHANGE, "amount_c": 50000000, "business_date": "2026-10-22"}))
        nav0 = rt.accounting.nav(f"{T1}:ALL").nav_c
        payload = {"from_account": US_EXCHANGE, "to_account": TREASURY, "amount": "100000.00", "business_date": "2026-10-22",
                   "reason": "Treasury below zero at the close of 10-22", "batch_id": "IMP-T-3", "line_key": "C3:T1:TREASURY:2026-10-22"}
        self.assertEqual((403, "AS_OF_FORBIDDEN"), (execute(rt, "cash.asof_transfer", payload).http, execute(rt, "cash.asof_transfer", payload).code))
        body = ok(self, execute(rt, "cash.asof_transfer", payload, actor="import"))
        t = one(rt, "SELECT * FROM transfers WHERE transfer_id=?", (body["transfer_id"],))
        self.assertEqual(("TRANSFER", US_EXCHANGE, TREASURY, 10000000, "2026-10-22", "asof_funding", "C3:T1:TREASURY:2026-10-22"),
                         (t["kind"], t["from_account"], t["to_account"], t["amount_c"], t["business_date"], t["linked_type"], t["linked_id"]))
        e = one(rt, "SELECT * FROM journal_entries WHERE entry_id=?", (t["entry_id"],))
        self.assertEqual(("ASOF_FUNDING", "as-of funding", "2026-10-22"), (e["kind"], e["memo"], e["business_date"]))
        lines = sc.q(rt, "SELECT account_id, gl, amount_c, memo FROM journal_lines WHERE entry_id=? ORDER BY line_no", (t["entry_id"],))
        # Dr 3100 / Cr 1010 at US Exchange, Dr 1010 / Cr 3100 at Treasury; 3100 nets to zero across the lifetime (L5)
        self.assertEqual([(US_EXCHANGE, "3100", 10000000), (US_EXCHANGE, "1010", -10000000), (TREASURY, "1010", 10000000), (TREASURY, "3100", -10000000)],
                         [(x["account_id"], x["gl"], x["amount_c"]) for x in lines])
        self.assertTrue(all(x["memo"].startswith("as-of funding: Treasury below zero") for x in lines))
        self.assertEqual(nav0, rt.accounting.nav(f"{T1}:ALL").nav_c)                                  # NAV-neutral
        again = ok(self, execute(rt, "cash.asof_transfer", payload, actor="import"))                  # a rerun returns the stored transfer
        self.assertEqual((True, body["transfer_id"]), (again["skipped"], again["transfer"]["transfer_id"]))
        big = dict(payload, amount="400001.00", line_key="C3:T1:TREASURY:2026-10-22:b")            # US Exchange held 400,000.00 at the close of 10-22
        r = execute(rt, "cash.asof_transfer", big, actor="import")
        self.assertEqual((409, "INSUFFICIENT_FUNDS"), (r.http, r.code))
        assert_integrity(self, rt, "full")

    def test_i_asof_transfer_on_a_1031_database(self):
        # a database built from the 1.0.3.1 schema (the transfers CHECK lists six kinds, never widened): no CHECK error
        db_path = os.path.join(self.tmp, "terminal.db")
        with open(SCHEMA_1031, "r", encoding="utf-8") as f:
            sql = f.read()
        conn = sqlite3.connect(db_path)
        conn.executescript(sql)
        conn.close()
        rt = self.rt = make_runtime(self.tmp, at=TUE_14)
        collateral.register(rt.bus)
        collateral.bind(rt)
        rt.collateral = collateral
        rt.bus.register("test.asof_xfer", _h_asof_xfer)
        rt.clock.set(MON_12)
        ok(self, execute(rt, "test.asof_xfer", {"from_account": "F1:TREASURY", "to_account": "F1:US_EXCHANGE", "amount_c": 1000000, "business_date": "2026-10-22"}))
        body = ok(self, execute(rt, "cash.asof_transfer", {"from_account": "F1:US_EXCHANGE", "to_account": "F1:TREASURY", "amount_c": 500000,
                                                           "business_date": "2026-10-22", "reason": "test", "batch_id": "IMP-T-4", "line_key": "C3:x"}, actor="import"))
        self.assertEqual(("TRANSFER", "asof_funding"), (one(rt, "SELECT kind FROM transfers WHERE transfer_id=?", (body["transfer_id"],))["kind"],
                                                         one(rt, "SELECT linked_type FROM transfers WHERE transfer_id=?", (body["transfer_id"],))["linked_type"]))
        assert_integrity(self, rt, "full")

    def test_j_sweep_in_refuses_on_the_business_date_figure(self):
        rt = self.rt = runtime(self.tmp, at=TUE_14)
        rt.clock.set(MON_12)
        # Treasury 10,000,000.00 from 10-20; 9,100,000.00 to Debt Instruments dated 10-22 -> 900,000.00 at the close of 10-22; 4,100,000.00 back
        # dated 10-23 -> 5,000,000.00 now.  An as-of sweep of 950,000.00 dated 10-22 is refused on the business-date figure (today's 5,000,000.00
        # would have covered it): 409 INSUFFICIENT_FUNDS, nothing written
        ok(self, execute(rt, "test.asof_xfer", {"from_account": TREASURY, "to_account": DEBT, "amount_c": 910000000, "business_date": "2026-10-22"}))
        ok(self, execute(rt, "test.asof_xfer", {"from_account": DEBT, "to_account": TREASURY, "amount_c": 410000000, "business_date": "2026-10-23"}))
        self.assertEqual(500000000, bal(rt, TREASURY, "1010"))
        with rt.db.reader() as conn:
            self.assertEqual((90000000, 500000000), (treasury.asof_sweepable_c(conn, T1, "USD", "2026-10-22"), treasury.asof_sweepable_c(conn, T1, "USD", "2026-10-23")))
        n = one(rt, "SELECT COUNT(*) n FROM journal_entries")["n"]
        r = execute(rt, "test.sweep_asof", {"account_id": US_EXCHANGE, "amount_c": 95000000, "business_date": "2026-10-22"})
        self.assertEqual((409, "INSUFFICIENT_FUNDS", 90000000, "2026-10-22"), (r.http, r.code, r.body["error"]["details"]["treasury_available_c"],
                                                                             r.body["error"]["details"]["as_of"]))
        self.assertEqual(n, one(rt, "SELECT COUNT(*) n FROM journal_entries")["n"])
        # the same sweep without an as-of date reads today's 5,000,000.00 and posts
        ok(self, execute(rt, "test.pay", {"account_id": US_EXCHANGE, "amount_c": 95000000}))
        assert_integrity(self, rt, "full")

    def test_j_sweep_in_posts_dated_d_on_the_business_date_figure(self):
        rt = self.rt = runtime(self.tmp, at=TUE_14)
        # Wed 10-21 10:00 a deposit of 15,000,000.00: Treasury 25,000,000.00 at the close of 10-21; then 23,000,000.00 to Debt Instruments dated
        # 10-23 -> 2,000,000.00 now.  An as-of sweep of 1,701,918.50 dated 10-21 posts with business date 10-21 (today's figure is not read)
        rt.clock.set("2026-10-21T10:00:00-04:00")
        ok(self, deposit(rt, 1500000000))
        rt.clock.set(MON_12)
        ok(self, execute(rt, "test.asof_xfer", {"from_account": TREASURY, "to_account": DEBT, "amount_c": 2300000000, "business_date": "2026-10-23"}))
        self.assertEqual(200000000, bal(rt, TREASURY, "1010"))
        with rt.db.reader() as conn:
            self.assertEqual(2500000000, treasury.asof_sweepable_c(conn, T1, "USD", "2026-10-21"))
        body = ok(self, execute(rt, "test.sweep_asof", {"account_id": US_EXCHANGE, "amount_c": 170191850, "business_date": "2026-10-21"}))
        t = one(rt, "SELECT * FROM transfers WHERE transfer_id=?", (body["transfer_id"],))
        self.assertEqual(("SWEEP_IN", 170191850, "2026-10-21"), (t["kind"], t["amount_c"], t["business_date"]))
        self.assertEqual("2026-10-21", one(rt, "SELECT business_date FROM journal_entries WHERE entry_id=?", (t["entry_id"],))["business_date"])
        # Treasury closes 10-21 at 25,000,000.00 - 1,701,918.50 = 23,298,081.50 and 10-23 at 23,298,081.50 - 23,000,000.00 = 298,081.50; holds 298,081.50
        self.assertEqual(29808150, bal(rt, TREASURY, "1010"))
        with rt.db.reader() as conn:
            self.assertEqual((2329808150, 29808150), (treasury.settled_at_close_c(conn, TREASURY, "USD", "2026-10-21"),
                                                      treasury.settled_at_close_c(conn, TREASURY, "USD", "2026-10-23")))
        assert_integrity(self, rt, "full")

    def test_treasury_rows_carry_the_asof_date_only_when_set(self):
        rt = self.rt = runtime(self.tmp, at=TUE_14)
        r = ok(self, fund(rt, US_EXCHANGE, 100000))                                                   # a live transfer: the accounting day 10-20
        self.assertEqual("2026-10-20", one(rt, "SELECT business_date FROM transfers WHERE transfer_id=?", (r["transfer_id"],))["business_date"])
        rt.clock.set("2026-10-20T18:00:00-04:00")                                                     # after 17:00 ET: 10-21
        r = ok(self, fund(rt, US_EXCHANGE, 100000))
        self.assertEqual(("2026-10-21", "2026-10-21"), (one(rt, "SELECT business_date FROM transfers WHERE transfer_id=?", (r["transfer_id"],))["business_date"],
                                                        one(rt, "SELECT business_date FROM journal_entries WHERE entry_id=?", (r["entry_id"],))["business_date"]))
        r = ok(self, execute(rt, "test.asof_xfer", {"from_account": TREASURY, "to_account": US_EXCHANGE, "amount_c": 100000, "business_date": "2026-10-19"}))
        self.assertEqual(("2026-10-19", "2026-10-19"), (one(rt, "SELECT business_date FROM transfers WHERE transfer_id=?", (r["transfer_id"],))["business_date"],
                                                        one(rt, "SELECT business_date FROM journal_entries WHERE entry_id=?", (r["entry_id"],))["business_date"]))


class ReaccrueTests(unittest.TestCase):
    """C17 / acceptance 1 (e): the balance-based nightly accruals restated on business-date balances, each night at its original rate."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-reacc-")
        self.rt = runtime(self.tmp, at=TUE_14)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_e_reaccrue_hand_worked(self):
        rt = self.rt
        # SOFR 3.88 (obs 10-20) and 3.90 (obs 10-21): Treasury cash interest SOFR - 0.50 % = 3.38 % (night 10-20), 3.40 % (nights 10-21, 10-22);
        # PB credit interest SOFR - 0.75 % = 3.13 %, 3.15 %, 3.15 %
        rt.rates.put("SOFR", [(date(2026, 10, 20), D("3.88")), (date(2026, 10, 21), D("3.90"))], persist=False)
        # 10,000,000.00 + a deposit of 15,010,000.00 (the PB threshold) - 10,000.00 to US Exchange = Treasury 25,000,000.00 from 10-20
        open_pb(self, rt, deposit_c=1501000000)
        ok(self, fund(rt, US_EXCHANGE, 1000000))
        self.assertEqual(2500000000, bal(rt, TREASURY, "1010"))
        out = lc.tick(rt, "2026-10-23T00:35:00-04:00", only="ACCRUE_DAILY")
        self.assertTrue(all(i["status"] == "DONE" for i in out["ran"]), out["ran"])
        # original Treasury nights: to_c(25,000,000 x 0.0338 / 360) = to_c(2,347.2222) = 2,347.22; 10-21 a new segment (the rate changed):
        # to_c(25,000,000 x 0.034 / 360) = to_c(2,361.1111) = 2,361.11; 10-22 cum 2 to_c(4,722.2222) = 4,722.22 -> 2,361.11.
        # PB credit: settled 0.00 -> nothing posted on all three nights
        self.assertEqual([("accr:cashint:T1:USD:2026-10-20", 234722), ("accr:cashint:T1:USD:2026-10-21", 236111), ("accr:cashint:T1:USD:2026-10-22", 236111)],
                         lc.idem_amounts(rt, "accr:cashint:"))
        self.assertEqual([], lc.idem_amounts(rt, "accr:pbcredit:"))
        rt.clock.set(MON_12)
        # the import (as of 10-20): Treasury 1,000,000.00 lower (moved to Debt Instruments, dated 10-20); PB credit base +10,000.00 (US Exchange -> PB dated 10-20)
        ok(self, execute(rt, "test.asof_xfer", {"from_account": TREASURY, "to_account": DEBT, "amount_c": 100000000, "business_date": "2026-10-20"}))
        ok(self, execute(rt, "test.asof_xfer", {"from_account": US_EXCHANGE, "to_account": PB, "amount_c": 1000000, "business_date": "2026-10-20"}))
        # SOFR for 10-21 is later revised: the corrections keep each night's ORIGINAL rate (from its ACCRUED event)
        rt.rates.put("SOFR", [(date(2026, 10, 21), D("4.50"))], persist=False)
        base = {"lifetime_id": T1, "from_night": "2026-10-20", "reason": "as-of import", "batch_id": "IMP-T-5"}
        r = execute(rt, "cash.reaccrue", dict(base, accounts=[TREASURY], kinds=["cashint"], through_night="2026-10-21"))
        self.assertEqual((403, "AS_OF_FORBIDDEN"), (r.http, r.code))
        body = ok(self, execute(rt, "cash.reaccrue", dict(base, accounts=[TREASURY], kinds=["cashint"], through_night="2026-10-21"), actor="import"))
        # restated on 24,000,000.00: to_c(24,000,000 x 0.0338 / 360) = to_c(2,253.3333) = 2,253.33 -> 2,253.33 - 2,347.22 = -93.89 (business date 10-21);
        #                           to_c(24,000,000 x 0.034 / 360) = to_c(2,266.6667) = 2,266.67 -> 2,266.67 - 2,361.11 = -94.44 (business date 10-22)
        self.assertEqual([("2026-10-20", "2026-10-21", -9389, "0.0338"), ("2026-10-21", "2026-10-22", -9444, "0.034")],
                         [(c["night"], c["business_date"], c["correction_c"], c["rate"]) for c in body["corrections"]])
        self.assertEqual([("1230", -9389, None), ("4300", 9389, "CASH:USD")], sh.lines_of(rt, f"reaccrue:IMP-T-5:{TREASURY}:cashint:2026-10-20"))
        self.assertEqual("2026-10-21", one(rt, "SELECT business_date FROM journal_entries WHERE idem_key=?", (f"reaccrue:IMP-T-5:{TREASURY}:cashint:2026-10-20",))["business_date"])
        # PB credit (through its accrued_through 10-22): base +10,000.00 -> night 10-20 at 3.13 %: to_c(0.8694) = 0.87; nights 10-21 / 10-22 at 3.15 %
        # are one segment: to_c(0.875) = 0.88 (half-even), cum 2 to_c(1.75) = 1.75 -> 0.87
        body = ok(self, execute(rt, "cash.reaccrue", dict(base, accounts=[PB], kinds=["pbcredit", "pb"]), actor="import"))
        self.assertEqual([("2026-10-20", 87, "0.0313"), ("2026-10-21", 88, "0.0315"), ("2026-10-22", 87, "0.0315")],
                         [(c["night"], c["correction_c"], c["rate"]) for c in body["corrections"]])
        self.assertEqual([("2026-10-21", "2026-10-22", "2026-10-23")], [tuple(c["business_date"] for c in body["corrections"])])
        # the accrual cursor follows the restated segment: 10-21 .. 10-22, base 10,000.00 at 0.0315, cumulative 1.75
        st = one(rt, "SELECT * FROM accrual_state WHERE kind='pbcredit' AND ref_id=?", (PB,))
        self.assertEqual(("2026-10-21", 1000000, "0.0315", 175, "2026-10-22"), (st["segment_start"], st["base_c"], st["rate"], st["cum_c"], st["accrued_through"]))
        # a rerun posts nothing
        n = one(rt, "SELECT COUNT(*) n FROM journal_entries")["n"]
        again = ok(self, execute(rt, "cash.reaccrue", dict(base, accounts=[TREASURY, PB], kinds=["cashint", "pbcredit", "pb"]), actor="import"))
        # (night 10-22 of Treasury was not in the first range: through its accrued_through it is corrected now, once: restated cum 2 of the 10-21
        #  segment to_c(24,000,000 x 0.034 x 2 / 360) = 4,533.33 - 2,266.67 = 2,266.66 vs 2,361.11 posted -> -94.45; nights 10-20 / 10-21 and the
        #  PB nights already carry this batch's corrections and post nothing)
        self.assertEqual([(TREASURY, "2026-10-22", -9445)], [(c["account_id"], c["night"], c["correction_c"]) for c in again["corrections"]])
        self.assertEqual(n + 1, one(rt, "SELECT COUNT(*) n FROM journal_entries")["n"])
        third = ok(self, execute(rt, "cash.reaccrue", dict(base, accounts=[TREASURY, PB], kinds=["cashint", "pbcredit", "pb"]), actor="import"))
        self.assertEqual([], third["corrections"])
        st = one(rt, "SELECT * FROM accrual_state WHERE kind='cashint' AND ref_id=?", (TREASURY,))
        self.assertEqual(("2026-10-21", 2400000000, "0.034", 453333), (st["segment_start"], st["base_c"], st["rate"], st["cum_c"]))
        assert_integrity(self, rt, "full")


class LenderWordingTests(unittest.TestCase):
    """C16 / 15.7 item 8: stored lender names stay untouched; every payload shows "(assumed terms)", never "(modelled)"."""

    def test_lender_label(self):
        self.assertEqual("street lender (assumed terms)", lending.lender_label("street lender (modelled)"))
        self.assertEqual("GS (prime broker, assumed terms)", lending.lender_label("GS (prime broker, modelled)"))
        self.assertEqual("BNY Agency Lending (agent lender, assumed terms)", lending.lender_label("BNY Agency Lending (agent lender, assumed terms)"))
        self.assertEqual(("", "street lender (assumed terms)"), (lending.lender_label(None), lending.MODELLED_LENDER))

    def test_stored_modelled_rows_read_assumed_terms(self):
        tmp = tempfile.mkdtemp(prefix="fst-lender-")
        rt = sh.runtime(tmp)
        try:
            sh.setup_abc(rt)
            sh.short_abc(self, rt)
            # a borrow stored by an earlier release: the row keeps "street lender (modelled)", every payload shows "(assumed terms)"
            with rt.db.writer() as tx:
                tx.conn.execute("UPDATE borrows SET lender=? WHERE borrow_id='B-000001'", (lending.LEGACY_LENDER,))
            self.assertEqual(lending.LEGACY_LENDER, sc.one(rt, "SELECT lender FROM borrows WHERE borrow_id='B-000001'")["lender"])
            with rt.db.reader() as conn:
                self.assertEqual("street lender (assumed terms)", lending.borrow_view(conn, "B-000001")["lender"])
                items = lending.exposure_items(rt, conn, [US_EXCHANGE], rt.marks)
            self.assertEqual(["street lender (assumed terms)"], [i["name"] for i in items if i["product"] == "stock borrow"])
            rows = lending.borrows_view(rt, T1)["rows"]
            self.assertEqual(["street lender (assumed terms)"], [r["lender"] for r in rows])
            self.assertFalse(any("modelled" in str(r) for r in rows))
        finally:
            rt.close()
            shutil.rmtree(tmp, ignore_errors=True)


class CollateralMarkDatingTests(unittest.TestCase):
    """POSTCLOSE-06: the 17:10 mark of trading day D posts after the 17:00 ET cut-off; it and its funding sweep carry business date D."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-cmark-")
        self.rt = sh.runtime(self.tmp)
        collateral.register(self.rt.bus)
        collateral.bind(self.rt)
        self.rt.collateral = collateral

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_postclose_06_mark_and_sweep_dated_d(self):
        rt = self.rt
        # Fri 10-16 10:00: short 1,000 ABC at 50.00 with no cash in US Exchange: collateral 51,000.00 (49,995.00 of the proceeds + 1,005.00 swept)
        sh.setup_abc(rt, fund_c=0)
        sh.short_abc(self, rt)
        self.assertEqual(0, bal(rt, US_EXCHANGE, "1010"))
        # 17:10 the CLOSE 52.00: 102 % x 1,000 x 52.00 = 53,040.00 -> a call of 2,040.00, funded by one Treasury sweep; the accounting day is
        # already 10-17 (after 17:00 ET), yet the mark "of 10-16" and its sweep are dated 10-16
        sh.close_mark(rt, "ABC", "2026-10-16", "52.00")
        rt.clock.set("2026-10-16T17:10:00-04:00")
        r = execute(rt, "credit.collateral_mark", {"borrow_id": "B-000001", "mark_date": "2026-10-16"}, request_id="job:cmark:B-000001:2026-10-16", actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(204000, r.body["delta_c"])
        self.assertEqual("2026-10-16", one(rt, "SELECT business_date FROM journal_entries WHERE idem_key='cmark:B-000001:2026-10-16'")["business_date"])
        sw = one(rt, "SELECT * FROM transfers WHERE kind='SWEEP_IN' AND linked_type='borrow' ORDER BY transfer_id DESC LIMIT 1")
        self.assertEqual((204000, "2026-10-16"), (sw["amount_c"], sw["business_date"]))
        self.assertEqual("2026-10-16", one(rt, "SELECT business_date FROM journal_entries WHERE entry_id=?", (sw["entry_id"],))["business_date"])
        # Mon 10-19 the CLOSE 52.25 marked on Tue 10:00 (a catch-up on a later day): the posting day, as before
        sh.close_mark(rt, "ABC", "2026-10-19", "52.25")
        rt.clock.set("2026-10-20T10:00:00-04:00")
        r = execute(rt, "credit.collateral_mark", {"borrow_id": "B-000001", "mark_date": "2026-10-19"}, request_id="job:cmark:B-000001:2026-10-19", actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        self.assertEqual("2026-10-20", one(rt, "SELECT business_date FROM journal_entries WHERE idem_key='cmark:B-000001:2026-10-19'")["business_date"])
        assert_integrity(self, rt, "full")


class RoutesTests(unittest.TestCase):
    """The collateral routes on a real ``build_runtime`` (offline): the restricted parts and a settlement's state."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-coll-api-")
        self.rt = ta.build(self.tmp)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_routes(self):
        rt = self.rt
        self.assertEqual("bound", rt.modules.get("collateral"))
        status, body, _h = rt.router.handle("GET", f"/api/collateral/restricted?account_id={US_EXCHANGE}", None, None, "127.0.0.1", None)
        self.assertEqual(200, status, body)
        self.assertEqual(("0.00", "0.00", False), (body["restricted"], body["csa_net"], body["reuse_allowed"]))
        status, body, _h = rt.router.handle("GET", "/api/collateral/restricted?account_id=T1:NOPE", None, None, "127.0.0.1", None)
        self.assertEqual(404, status)
        status, body, _h = rt.router.handle("GET", "/api/settlements/S-999999", None, None, "127.0.0.1", None)
        self.assertEqual(404, status)
        # 1.0.6 (Part B): the margin calls and the CSA pledges ship with their features (15.3 E1), so their probes are callable and the
        # Unsupported page no longer lists them; the C16 reads answer on a real runtime
        self.assertTrue(callable(getattr(collateral, "margin_calls_rows", None)))
        self.assertTrue(callable(getattr(collateral, "pledges_view", None)))
        for path in ("/api/collateral/summary?book=T", "/api/collateral/accounts?book=T", "/api/collateral/cash?book=T", "/api/collateral/pledges?book=T",
                     "/api/collateral/treasury_securities?book=T", "/api/margin/calls?book=T", f"/api/collateral/regt?account_id={US_EXCHANGE}",
                     f"/api/collateral/requirements?account_id={US_EXCHANGE}", "/api/collateral/custody?book=T"):
            status, body, _h = rt.router.handle("GET", path, None, None, "127.0.0.1", None)
            self.assertEqual(200, status, (path, body))
        status, body, _h = rt.router.handle("GET", "/api/collateral/regt?account_id=T1:NOPE", None, None, "127.0.0.1", None)
        self.assertEqual(404, status)

    def test_repo_preview_soft_mode(self):
        """V104R2-BW-05 (the server part, repo.py): a repo preview asked with ``soft`` answers a refusal as 200 ``{blocked, blockers}`` (so a
        correctly refused preview leaves no failed request in the browser console); without ``soft`` the 4xx with its code is unchanged."""
        rt = self.rt
        bad = {"account_id": "T1:CREDIT", "side": "SIDEWAYS", "instrument_id": "UST-NOPE", "face": "1000000"}
        status, body, _h = rt.router.handle("POST", "/api/credit/repos/preview", None, dict(bad), "127.0.0.1", None)
        self.assertTrue(400 <= status < 500, (status, body))
        self.assertEqual("SIDE", body["error"]["code"])
        for flag in (True, "1", "yes"):
            status, body, _h = rt.router.handle("POST", "/api/credit/repos/preview", None, dict(bad, soft=flag), "127.0.0.1", None)
            self.assertEqual(200, status, body)
            self.assertEqual((False, True, ["SIDE"]), (body["ok"], body["blocked"], [b["code"] for b in body["blockers"]]))
            self.assertIn("side must be REPO", body["message"])
        # soft is never a field of the preview itself: a valid-looking request with soft=false still answers the 4xx
        status, body, _h = rt.router.handle("POST", "/api/credit/repos/preview", None, dict(bad, soft=False), "127.0.0.1", None)
        self.assertTrue(400 <= status < 500, (status, body))


BILL = "UST-TESTBILL27"


def _h_bond_buy(ctx, payload):
    """A test-only bond BUY fill (the bond engine's posting shape, no fee): fills row, ``Dr 1110 gross / Cr 2100 gross``, a LONG lot, a T+1
    settlement row.  ``{account_id, instrument_id, qty (face), price (per 100)}``."""
    from finsim.terminal.ledger import open_lot
    aid, iid, qty, px = payload["account_id"], payload["instrument_id"], int(payload["qty"]), Decimal(str(payload["price"]))
    inst = ctx.services.instruments.get(iid, ctx.conn)
    gross = inst.contract_value_c(px, qty)
    fill_id = next_id(ctx.conn, "fill")
    trade_date = ctx.clock.today()
    settle = ctx.cal.settlement_date(trade_date, inst.settle_days)
    entry_id = ctx.post(Entry("FILL", f"BUY {qty:,} {iid} @ {px}", [dr(aid, "1110", "USD", gross, instrument_id=iid), cr(aid, "2100", "USD", gross)], f"fill:{fill_id}",
                              ref_type="fill", ref_id=fill_id))
    ctx.conn.execute("INSERT INTO fills(fill_id, order_id, account_id, instrument_id, side, qty, price, gross_c, fee_c, currency, fx_rate, ts, trade_date, settle_date,"
                     " origin, price_basis, quote_json, bar_ts, cap_qty, cap_note, entry_id, idem_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (fill_id, None, aid, iid, "BUY", qty, str(px), gross, 0, "USD", "1", ctx.ts, trade_date.isoformat(), settle.isoformat(), "ORDER", "model",
                      "{}", None, None, None, entry_id, f"test:{fill_id}"))
    open_lot(ctx.tx, aid, iid, "LONG", fill_id, qty, px, gross, "USD")
    sid = next_id(ctx.conn, "settlement")
    ctx.conn.execute("INSERT INTO settlements(settle_id, fill_id, fx_deal_id, account_id, leg, currency, amount_c, gl, due_date, status) VALUES (?,?,?,?,?,?,?,?,?,'PENDING')",
                     (sid, fill_id, None, aid, "CASH", "USD", -gross, "2100", settle.isoformat()))
    ctx.touched_instruments.add((aid, iid))
    return {"fill_id": fill_id, "gross_c": gross}


class PledgeTests(unittest.TestCase):
    """P6 Part B (1.0.6) acceptance 1 (d) - CSA securities pledges (task 6, PORTFOLIO-09): 1,000,000 face of a Treasury bill at 99.00
    pledged to Goldman Sachs under its CSA (the GS schedule's assumed bill haircut 2 %) -> market value 1,000,000 x 99.00 / 100 =
    990,000.00 (a bill accrues nothing), eligible 990,000.00 x (1 - 0.02) = 970,200.00; the pledged face cannot be sold (the bond SELL's
    C27 check through ``repo.pledged_face``) or pledged again; a pledge moves no GL balance; release frees it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-pledge-")
        self.rt = rt = runtime(self.tmp, at=TUE_10)
        rt.repo = repo
        rt.bus.register("test.settle_due", sh._h_settle_due)
        open_pb(self, rt)
        with rt.db.writer() as tx:
            rt.instruments._insert(tx.conn, tx.ts, instrument_id=BILL, kind="BOND", symbol="912797ZZ9", name="US Treasury bill 21-Jan-2027 (912797ZZ9)",
                                   currency="USD", multiplier="0.01", lot=1000, session_group="US_BONDS", settle_days=1, account_kinds=["DEBT", "PB"],
                                   spec={"type": "BILL", "coupon_pct": "0", "maturity": "2027-01-21", "freq": 0, "zero_coupon": True}, support="FULL")
        rt.instruments.refresh(BILL)
        # BUY 1,000,000 face at 99.00, no fee: 1,000,000 x 99.00 x 0.01 = 990,000.00 payable T+1; funded from Treasury and settled
        rt.bus.register("test.bond_buy", _h_bond_buy)
        ok(self, execute(rt, "test.bond_buy", {"account_id": DEBT, "instrument_id": BILL, "qty": 1000000, "price": "99.00"}))
        ok(self, fund(rt, DEBT, 99000000))
        ok(self, execute(rt, "test.settle_due", {"as_of": "2026-10-21"}, actor="lifecycle"))
        rt.marks.set(BILL, "99.00", "close")

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def gl_snapshot(self):
        return sorted((r["account_id"], r["gl"], r["currency"], r["instrument_id"], r["balance_c"]) for r in sc.q(self.rt, "SELECT * FROM gl_balances"))

    def test_d_pledge_eligible_value_encumbrance_and_release(self):
        rt = self.rt
        before = self.gl_snapshot()
        body = ok(self, execute(rt, "collateral.pledge", {"account_id": DEBT, "instrument_id": BILL, "face": "1000000", "pledged_to": PB}))
        p = body["pledge"]
        self.assertEqual(("PL-000001", 1000000, "0.02", 99000000, 97020000, "PLEDGED", "BILL"),
                         (p["pledge_id"], p["face"], p["haircut"], p["market_value_c"], p["eligible_c"], p["status"], p["type"]))
        self.assertEqual(before, self.gl_snapshot())                                # a pledge moves no GL balance
        with rt.db.reader() as conn:
            self.assertEqual(1000000, repo.pledged_face(conn, DEBT, BILL))
        v = collateral.pledges_view(rt, f"{T1}:ALL")
        self.assertEqual((1000000, 99000000, 97020000), (v["pledged_face"], v["market_value_c"], v["eligible_c"]))
        self.assertEqual([("0.02", "0.04", "TREASURIES")], [(t["bill_haircut"], t["bond_haircut"], t["securities"]) for t in v["terms"]])
        s = collateral.summary(rt, f"{T1}:ALL")
        self.assertEqual((97020000, 99000000), (s["collateral_posted_c"], s["encumbered_c"]))      # eligible value posted; market value encumbered
        gs = [g for g in s["by_counterparty"] if g["type"] == "CSA"]
        self.assertEqual([97020000], [g["securities_posted_c"] for g in gs])
        req = collateral.position_requirements(rt, DEBT)
        self.assertEqual((0, 97020000), (req[BILL]["margin_c"], req[BILL]["collateral_c"]))
        # the pledged face cannot be pledged again ...
        r = execute(rt, "collateral.pledge", {"account_id": DEBT, "instrument_id": BILL, "face": "1000", "pledged_to": PB})
        self.assertEqual((False, "COLLATERAL_SHORT"), (r.ok, r.body["error"]["code"]))
        # ... nor sold: the bond SELL's C27 check counts it (the preview lists the refusal)
        from finsim.terminal.products import bonds
        with rt.db.reader() as conn:
            _t, blocked, _w = bonds.check_trade(rt, {"account_id": DEBT, "instrument_id": BILL, "side": "SELL", "qty": 1000}, conn, rt.clock.epoch(),
                                                rt.marks.snapshot(), collect=True)
        self.assertIn("PLEDGED_IN_REPO", [b.code for b in blocked])
        # refusals: an ineligible instrument, a non-PB account, more than the free face
        for body, code in (({"account_id": DEBT, "instrument_id": "AAPL", "face": "100", "pledged_to": PB}, "NOT_ELIGIBLE"),
                           ({"account_id": DEBT, "instrument_id": BILL, "face": "1000", "pledged_to": US_EXCHANGE}, "NOT_PB"),
                           ({"account_id": DEBT, "instrument_id": BILL, "face": "0", "pledged_to": PB}, "FACE")):
            r = execute(rt, "collateral.pledge", body)
            self.assertEqual((False, code), (r.ok, r.body["error"]["code"]), body)
        # release: the face is free again, nothing moved in the ledger
        body = ok(self, execute(rt, "collateral.release", {"pledge_id": "PL-000001"}))
        self.assertEqual("RELEASED", body["pledge"]["status"])
        with rt.db.reader() as conn:
            self.assertEqual(0, repo.pledged_face(conn, DEBT, BILL))
        self.assertEqual(before, self.gl_snapshot())
        r = execute(rt, "collateral.release", {"pledge_id": "PL-000001"})
        self.assertEqual((False, "NOT_PLEDGED"), (r.ok, r.body["error"]["code"]))
        # the bills of the book, as cash equivalents: face 1,000,000, market 990,000.00, accreted straight-line from the 10-20 purchase
        # (10,000.00 of discount over 93 days to 2027-01-21; 1 day held at the view's accounting day 10-20 -> 0 days: 0.00)
        tb = collateral.treasury_securities(rt, f"{T1}:ALL")
        self.assertEqual([(DEBT, 1000000, 99000000, "2027-01-21")], [(r["account_id"], r["face"], r["market_value_c"], r["maturity"]) for r in tb["rows"]])
        assert_integrity(self, rt)


if __name__ == "__main__":
    unittest.main()
