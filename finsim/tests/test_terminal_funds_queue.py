"""Hotfix 2026-10-21 (the owner: "fix the blocked somehow so that they queue"): FUNDS_WAIT and the ADV of names resolved
on demand.  Every expected number is worked by hand in the comment next to it.  Live as 1.0.3.1 since 2026-10-21; ported to
1.0.4 on 2026-10-27 (the only change: the AON package's short-option margin is RESERVED in 1.0.4, SR-13, not posted to 1310).

FUNDS_WAIT (``orders.py``): a NEW order whose only blocker is funding is accepted SUSPENDED "waiting for funds: needs X,
available Y" (one ORDER_SUBMITTED event, one INFO attention item, no reservation); the preview answers ok with the
"will queue" warning; the matcher's sweep resumes the waiting orders that fit, oldest first, against a running budget
(``orders.plan_funds_resume`` / ``order.resume_funded``) and fills them by the normal rules; an order resumed but
unfundable at its fill goes back to SUSPENDED without a second attention item and is retried after a back-off.

ADV (``instruments.adv_from_daily``, ``quotes.worker``, ``lending.locate``): a ticker resolved through Yahoo has no
ADV in the master, so its borrow tier was NONE and every short was refused; the worker now stores the mean volume of
3 months of Yahoo daily bars with its basis, when the name is resolved and when ``locate`` finds it missing.

The world: the three real books built by ``server.build_runtime`` offline (FakeProvider, FakeClock, a temp home, no
network) on Tue 2026-10-20 16:30 ET (US equities closed; a market order rests until Wed 09:30); AAPL last 339.50.  The
Personal Book P1: Treasury 1,000,000.00; P1:US_EXCHANGE holds no cash.  Equity fee: 0.005 / share, min 1.00 (100
shares -> 1.00).  A resting MARKET buy reserves remaining x last x 1.02 + fee (339.50 x 1.02 = 346.29).
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import date, datetime, time as dtime

from tests.terminal_helpers import T1, TREASURY, US_EXCHANGE, assert_integrity, bal, deposit, execute
from tests.test_terminal_display_units import D, SOFR_DATE, build, quote
from tests import test_terminal_orders as sc
from finsim.terminal import lending, orders
from finsim.terminal.api_views import trial_balance
from finsim.terminal.clock import NY, FakeClock
from finsim.terminal.commands import Command
from finsim.terminal.instruments import ADV_DAYS, adv_from_daily
from finsim.terminal.quotes.providers import YahooChart

ACCT = "P1:US_EXCHANGE"
MKT = {"account_id": ACCT, "instrument_id": "AAPL", "side": "BUY", "order_type": "MARKET", "tif": "DAY"}
# VMRK (a name resolved on demand): 10 completed sessions Tue 10-06 .. Mon 10-19 (NYSE open on Columbus Day 10-12), volumes summing
# to 120,000 + 80,000 + 95,000 + 110,000 + 105,000 + 90,000 + 100,000 + 130,000 + 70,000 + 100,000 = 1,000,000 -> ADV 100,000; plus
# Fri 10-02 with NO volume (skipped, never zero-filled) and today's still-forming 10-20 bar of 5,000 (not a completed session)
VMRK_DAYS = ["2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19"]
VMRK_VOLS = [120000, 80000, 95000, 110000, 105000, 90000, 100000, 130000, 70000, 100000]
VMRK_BARS = [("2026-10-02", None)] + list(zip(VMRK_DAYS, VMRK_VOLS)) + [("2026-10-20", 5000)]
VMRK_BASIS = "ADV from Yahoo daily bars, 3 months (10 sessions 2026-10-06 to 2026-10-19)"
VMRK_META = {"symbol": "VMRK", "currency": "USD", "exchangeName": "NMS", "fullExchangeName": "NasdaqGS", "instrumentType": "EQUITY",
             "longName": "VMRK test listing", "exchangeTimezoneName": "America/New_York"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-fundsq-")
        self.rt = build(self.tmp)
        self.rt.rates.put("SOFR", [(SOFR_DATE, D("3.87"))], persist=False)
        quote(self.rt, "AAPL", "339.50")

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, method: str, path: str, body=None):
        status, out, _headers = self.rt.router.handle(method, path, None, body, "127.0.0.1", None)
        return status, out

    def ok(self, method: str, path: str, body=None):
        status, out = self.call(method, path, body)
        self.assertEqual(200, status, f"{method} {path} -> {status} {out}")
        return out

    def xfer(self, src: str, dst: str, amount: str, rid: str) -> None:
        self.ok("POST", "/api/transfers", {"from_account": src, "to_account": dst, "currency": "USD", "amount": amount, "request_id": rid})

    def submit(self, body: dict, rid: str) -> dict:
        return self.ok("POST", "/api/orders", dict(body, request_id=rid))

    def q(self, sql: str, params=()):
        with self.rt.db.reader() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def order(self, oid: str) -> dict:
        return self.ok("GET", f"/api/orders/{oid}")

    def bp(self) -> dict:
        return self.ok("GET", f"/api/accounts/{ACCT}")["buying_power"]["USD"]

    def items(self, oid: str):
        return [(r["severity"], r["code"], r["resolved_ts"] is None) for r in self.q("SELECT * FROM attention WHERE ref_type='order' AND ref_id=? ORDER BY id", (oid,))]

    def resume_commands(self) -> int:
        return self.q("SELECT COUNT(*) n FROM commands WHERE kind='order.resume_funded'")[0]["n"]

    def sweeps(self):
        return [int(r["amount_c"]) for r in self.q("SELECT amount_c FROM transfers WHERE kind='SWEEP_IN' AND to_account=? ORDER BY transfer_id", (ACCT,))]


# ===== FUNDS_WAIT: the queue, the INFO item, the preview ==================================================================================
class QueueForFunds(Base):
    def test_preview_ok_with_the_queue_warning_and_the_order_queues_reserving_nothing(self):
        # Treasury 1,000,000.00 - 990,000.00 moved to P1:DEBT = 10,000.00; P1:US_EXCHANGE own cash 0.00
        self.xfer("P1:TREASURY", "P1:DEBT", "990000.00", "fq:xfer-out-0001")
        # BUY 100 AAPL: need 100 x 339.50 + fee 1.00 = 33,951.00 > own 0.00 + Treasury 10,000.00 + PB 0.00 = 10,000.00
        pv = self.ok("POST", "/api/orders/preview", dict(MKT, qty=100))
        f = pv["funding"]
        self.assertEqual((True, []), (pv["ok"], pv["blockers"]))
        self.assertEqual(("33951.00", "0.00", "10000.00", "33951.00", "10000.00", False, True, "waiting for funds: needs USD 33,951.00, available USD 10,000.00"),
                         (f["need"], f["available"], f["treasury_available"], f["shortfall"], f["sweep_in"], f["ok"], f["queued"], f["wait"]))
        self.assertIn("will queue: waiting for funds (needs USD 33,951.00, available USD 10,000.00); it executes automatically when cash frees up", pv["warnings"])
        self.assertEqual((False, 0, True), (pv["execution"]["fill_now"], pv["execution"]["fill_qty_now"], pv["execution"]["queued"]))
        # the submit (with that preview) is accepted and stored SUSPENDED, waiting for funds
        out = self.submit(dict(MKT, qty=100, preview_id=pv["preview_id"]), "fq:a-0001")
        o = out["order"]
        oid = o["order_id"]
        self.assertEqual(("SUSPENDED", 0, "waiting for funds: needs USD 33,951.00, available USD 10,000.00", "FUNDS_WAIT", []),
                         (o["status"], o["filled_qty"], o["reason"], out["decision"]["reason"], out["fills"]))
        # journaled: one order event (None -> SUSPENDED), ONE event (ORDER_SUBMITTED, status SUSPENDED), one INFO item (not WARN)
        ev = self.q("SELECT type, payload_json FROM events WHERE ref_type='order' AND ref_id=?", (oid,))
        self.assertEqual(["ORDER_SUBMITTED"], [e["type"] for e in ev])
        self.assertEqual(("SUSPENDED", o["reason"]), (json.loads(ev[0]["payload_json"])["status"], json.loads(ev[0]["payload_json"])["reason"]))
        self.assertEqual([(None, "SUSPENDED")], [(e["from_status"], e["to_status"]) for e in self.order(oid)["events"]])
        self.assertEqual([("INFO", "ORDER_WAITING_FUNDS", True)], self.items(oid))
        msg = self.q("SELECT message FROM attention WHERE ref_id=?", (oid,))[0]["message"]
        self.assertIn("queued: waiting for funds: needs USD 33,951.00, available USD 10,000.00; it executes automatically when cash frees up", msg)
        # it reserves nothing: committed 0.00, buying power still the Treasury's 10,000.00; the orders list shows the reason
        bp = self.bp()
        self.assertEqual(("0.00", "10000.00", "10000.00"), (bp["committed"], bp["treasury_available"], bp["total"]))
        listed = self.ok("GET", f"/api/accounts/{ACCT}/orders?status=open")["orders"]
        self.assertEqual([(oid, "SUSPENDED", o["reason"])], [(x["order_id"], x["status"], x["reason"]) for x in listed])
        # and it blocks no other order: BUY 20 AAPL needs 20 x 339.50 + 1.00 = 6,791.00 <= 10,000.00 -> WORKING
        c = self.submit(dict(MKT, qty=20), "fq:c-0001")
        self.assertEqual("WORKING", c["order"]["status"])
        # a refusal that is not funding still refuses: a SELL of a position the account does not hold -> 400 OVERSELL
        st, err = self.call("POST", "/api/orders", dict(MKT, side="SELL", qty=10, request_id="fq:sell-0001"))
        self.assertEqual((400, "OVERSELL"), (st, err["error"]["code"]))
        self.assertTrue(self.rt.accounting.integrity("ledger").ok)

    def test_two_queued_orders_one_budget_only_the_first_resumes_then_fills(self):
        self.xfer("P1:TREASURY", "P1:DEBT", "990000.00", "fq:xfer-out-0002")                     # Treasury 10,000.00
        a = self.submit(dict(MKT, qty=100), "fq:a-0002")["order"]                                 # needs 33,951.00 -> queued
        b = self.submit(dict(MKT, qty=80), "fq:b-0002")["order"]                                  # 80 x 339.50 + 1.00 = 27,161.00 -> queued
        c = self.submit(dict(MKT, qty=20), "fq:c-0002")["order"]                                  # 6,791.00 -> WORKING
        self.assertEqual(("SUSPENDED", "SUSPENDED", "WORKING"), (a["status"], b["status"], c["status"]))
        self.assertEqual("waiting for funds: needs USD 27,161.00, available USD 10,000.00", b["reason"])
        # C reserves 20 x 346.29 + 1.00 = 6,926.80 -> committed 6,926.80; Treasury left 10,000.00 - 6,926.80 = 3,073.20
        bp = self.bp()
        self.assertEqual(("6926.80", "3073.20", "3073.20"), (bp["committed"], bp["treasury_available"], bp["total"]))
        # nothing fits 3,073.20: the sweep issues no resume command
        self.rt.matcher.sweep()
        self.assertEqual(0, self.resume_commands())
        # cash frees up: 40,000.00 back from P1:DEBT -> Treasury 50,000.00, left for the account 50,000.00 - 6,926.80 = 43,073.20
        self.xfer("P1:DEBT", "P1:TREASURY", "40000.00", "fq:xfer-back-0002")
        with self.rt.db.reader() as conn:
            plan = orders.plan_funds_resume(self.rt, conn, self.rt.marks.snapshot(), self.rt.clock.epoch())
        # oldest first against the running budget: A 33,951.00 <= 43,073.20 -> resumes, 9,122.20 left; B 27,161.00 > 9,122.20 -> waits
        self.assertEqual([([a["order_id"]], 3395100, 4307320)], [(p["order_ids"], p["need_c"], p["available_c"]) for p in plan])
        self.rt.matcher.sweep()
        self.assertEqual(1, self.resume_commands())
        oa, ob = self.order(a["order_id"]), self.order(b["order_id"])
        self.assertEqual(("WORKING", None, "resumed: funds available (needs USD 33,951.00, available USD 43,073.20)"),
                         (oa["status"], oa["reason"], oa["events"][-1]["note"]))
        self.assertEqual(("SUSPENDED", b["reason"]), (ob["status"], ob["reason"]))
        # A now reserves 100 x 346.29 + 1.00 = 34,630.00 -> committed 41,556.80, buying power 8,443.20 < B's 27,161.00: no second command
        bp = self.bp()
        self.assertEqual(("41556.80", "8443.20"), (bp["committed"], bp["total"]))
        self.rt.matcher.sweep()
        self.assertEqual(1, self.resume_commands())
        # Wed 09:35 ET, a fresh 339.50 print: A and C fill, each sweeping exactly its need (33,951.00 and 6,791.00); Treasury
        # 50,000.00 - 40,742.00 = 9,258.00 < B's 27,161.00: B keeps waiting; A's INFO item resolves with its fill
        self.rt.clock.set("2026-10-21T09:35:00-04:00")
        quote(self.rt, "AAPL", "339.50", age=30)
        self.rt.matcher.sweep()
        self.assertEqual(("FILLED", "SUSPENDED", "FILLED"), tuple(self.order(x["order_id"])["status"] for x in (a, b, c)))
        self.assertEqual([679100, 3395100], sorted(self.sweeps()))
        self.assertEqual(({"USD": "9258.00"}, 1), (self.ok("GET", "/api/treasury?book=P")["treasury"]["cash_by_ccy"], self.resume_commands()))
        self.assertEqual(([("INFO", "ORDER_WAITING_FUNDS", False)], [("INFO", "ORDER_WAITING_FUNDS", True)]), (self.items(a["order_id"]), self.items(b["order_id"])))
        # 20,000.00 more at 09:36 -> Treasury 29,258.00 >= 27,161.00: B resumes and fills in the same sweep; Treasury 2,097.00
        self.rt.clock.set("2026-10-21T09:36:00-04:00")
        self.xfer("P1:DEBT", "P1:TREASURY", "20000.00", "fq:xfer-back-0003")
        quote(self.rt, "AAPL", "339.50", age=30)
        self.rt.matcher.sweep()
        ob = self.order(b["order_id"])
        self.assertEqual(("FILLED", 80, "resumed: funds available (needs USD 27,161.00, available USD 29,258.00)"),
                         (ob["status"], ob["filled_qty"], [e["note"] for e in ob["events"] if e["note"].startswith("resumed")][0]))
        self.assertEqual(([679100, 2716100, 3395100], {"USD": "2097.00"}, 2), (sorted(self.sweeps()), self.ok("GET", "/api/treasury?book=P")["treasury"]["cash_by_ccy"],
                                                                                self.resume_commands()))
        self.assertEqual([("INFO", "ORDER_WAITING_FUNDS", False)], self.items(b["order_id"]))
        report = self.rt.accounting.integrity("full")
        self.assertTrue(report.ok, report.failing)
        tb = trial_balance(self.rt, "P1:ALL")
        self.assertTrue(tb["balanced"], tb["by_currency"])

    def test_resumed_but_unfundable_at_its_fill_goes_back_without_a_second_item_and_backs_off(self):
        self.xfer("P1:TREASURY", "P1:DEBT", "990000.00", "fq:xfer-out-0003")                     # Treasury 10,000.00
        a = self.submit(dict(MKT, qty=100), "fq:a-0003")["order"]                                 # needs 33,951.00 -> queued
        self.xfer("P1:DEBT", "P1:TREASURY", "40000.00", "fq:xfer-back-0004")                     # Treasury 50,000.00
        self.rt.matcher.sweep()                                                                     # A resumes (rests until Wed 09:30)
        self.assertEqual("WORKING", self.order(a["order_id"])["status"])
        # before the open the cash leaves again: 45,000.00 to P1:DEBT -> Treasury 5,000.00
        self.xfer("P1:TREASURY", "P1:DEBT", "45000.00", "fq:xfer-out-0004")
        # Wed 09:35, a 339.50 print: the fill needs 33,951.00, own 0.00 + Treasury 5,000.00 -> back to SUSPENDED (waiting for funds),
        # nothing posted, and NO second attention item (the INFO item of the queue is still open)
        self.rt.clock.set("2026-10-21T09:35:00-04:00")
        quote(self.rt, "AAPL", "339.50", age=30)
        self.rt.matcher.sweep()
        oa = self.order(a["order_id"])
        self.assertEqual(("SUSPENDED", "waiting for funds: needs USD 33,951.00, available USD 5,000.00", 0), (oa["status"], oa["reason"], oa["filled_qty"]))
        self.assertEqual([("INFO", "ORDER_WAITING_FUNDS", True)], self.items(a["order_id"]))
        self.assertEqual(([], 0), (self.sweeps(), self.q("SELECT COUNT(*) n FROM fills")[0]["n"]))
        # the cash is back at 09:35:10, but the order was resumed once already: it waits 60 s from its suspension (09:35:00)
        self.xfer("P1:DEBT", "P1:TREASURY", "45000.00", "fq:xfer-back-0005")                     # Treasury 50,000.00
        self.rt.clock.set("2026-10-21T09:35:40-04:00")
        quote(self.rt, "AAPL", "339.50", age=5)
        self.rt.matcher.sweep()
        self.assertEqual(("SUSPENDED", 1), (self.order(a["order_id"])["status"], self.resume_commands()))
        # 09:36:05 (65 s after): resumed and filled in the same sweep; one sweep of 33,951.00; the item resolves with the fill
        self.rt.clock.set("2026-10-21T09:36:05-04:00")
        quote(self.rt, "AAPL", "339.50", age=5)
        self.rt.matcher.sweep()
        oa = self.order(a["order_id"])
        self.assertEqual(("FILLED", 100, [3395100], 2), (oa["status"], oa["filled_qty"], self.sweeps(), self.resume_commands()))
        self.assertEqual([("INFO", "ORDER_WAITING_FUNDS", False)], self.items(a["order_id"]))
        report = self.rt.accounting.integrity("full")
        self.assertTrue(report.ok, report.failing)
        self.assertTrue(trial_balance(self.rt, "P1:ALL")["balanced"])

    def test_cancel_of_a_queued_order_resolves_its_item(self):
        self.xfer("P1:TREASURY", "P1:DEBT", "990000.00", "fq:xfer-out-0005")
        a = self.submit(dict(MKT, qty=100), "fq:a-0005")["order"]
        st, out = self.call("DELETE", f"/api/orders/{a['order_id']}", {"request_id": "fq:cancel-0005"})
        self.assertEqual((200, "CANCELLED"), (st, out["order"]["status"]))
        self.assertEqual([("INFO", "ORDER_WAITING_FUNDS", False)], self.items(a["order_id"]))


# ===== FUNDS_WAIT for packages (the WP-02 harness: Test Book T1, Treasury 10,000,000.00, Tue 2026-10-20 14:00 ET, session open) ==========
class QueuedPackages(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-fundsq-pkg-")
        self.rt = sc.runtime(self.tmp)
        self.rt.options = sc.FakeOptions(self.rt)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def items(self, oid: str):
        return [(r["severity"], r["code"], r["resolved_ts"] is None) for r in sc.q(self.rt, "SELECT * FROM attention WHERE ref_type='order' AND ref_id=? ORDER BY id", (oid,))]

    def test_hedge_package_queues_its_primary_and_counts_the_leg_when_it_resumes(self):
        rt = self.rt
        execute(rt, "treasury.withdraw", {"lifetime_id": T1, "amount_c": 990000000, "currency": "USD"})        # Treasury 100,000.00
        occ = sc.ensure_option(rt, "SPY261120P00715000").instrument_id
        rt.marks.set(occ, "9.50")
        rt.hedges = sc.FakeHedges([{"instrument_id": occ, "side": "BTO", "qty": 2}])
        now = rt.clock.epoch()
        rt.marks.set_quote(sc.rec("SPY", "769.32", now - 60, now))
        # primary BUY 200 SPY: 200 x 769.32 + 1.00 = 153,865.00 > 100,000.00 -> queued; the BTO 2 puts (ratio 2 / 200 = 0.01) wait for it
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="SPY", side="BUY", qty=200, order_type="MARKET", confirm_large=True, hedge={"candidate_id": "PUT_7PCT"})
        self.assertTrue(r.ok, r.body)
        oid = r.body["order"]["order_id"]
        child = sc.one(rt, "SELECT * FROM orders WHERE parent_order_id=?", (oid,))
        self.assertEqual(("SUSPENDED", "waiting for funds: needs USD 153,865.00, available USD 100,000.00", "WAITING_PARENT", 2),
                         (r.body["order"]["status"], r.body["order"]["reason"], child["status"], child["qty"]))
        # neither the queued primary nor its waiting leg reserves anything
        self.assertEqual((0, 10000000), (rt.accounting.cash(US_EXCHANGE)["USD"].reserved_c, rt.accounting.buying_power(US_EXCHANGE, "USD").total_c))
        # the resume counts the leg: 153,865.00 + 2 x 9.50 x 100 + fee 2 x 0.65 = 1,901.30 -> 155,766.30; Treasury 154,000.00 is not enough
        deposit(rt, 5400000)
        with rt.db.reader() as conn:
            self.assertEqual([], orders.plan_funds_resume(rt, conn, rt.marks.snapshot(), now))
        deposit(rt, 200000)                                                                                     # Treasury 156,000.00
        res = execute(rt, "order.resume_funded", {}, actor="matcher")
        self.assertTrue(res.ok, res.body)
        self.assertEqual([(oid, "WORKING", 15576630, 15600000)], [(x["order_id"], x["status"], x["need_c"], x["available_c"]) for x in res.body["resumed"]])
        self.assertEqual("WAITING_PARENT", sc.one(rt, "SELECT status FROM orders WHERE order_id=?", (child["order_id"],))["status"])
        # the matcher fills the primary (one sweep of exactly 153,865.00, Treasury 2,135.00); the leg is sized floor(200 x 0.01) = 2 and released
        self.assertEqual(1, sc.matcher_for(rt, sc.FakeCache({"SPY": sc.rec("SPY", "769.32", now - 30, now)})).on_quotes(["SPY"]))
        self.assertEqual(("FILLED", "WORKING", 2), (sc.order_row(rt, oid)["status"], sc.order_row(rt, child["order_id"])["status"], sc.order_row(rt, child["order_id"])["qty"]))
        self.assertEqual([15386500], [t["amount_c"] for t in sc.q(rt, "SELECT amount_c FROM transfers WHERE kind='SWEEP_IN'")])
        self.assertEqual(213500, bal(rt, TREASURY, "1010"))
        self.assertEqual([("INFO", "ORDER_WAITING_FUNDS", False)], self.items(oid))
        assert_integrity(self, rt, "full")

    def test_aon_package_queues_as_a_unit_and_resumes_as_a_unit(self):
        # the iron condor of test_terminal_orders: BTO P700 @ ask 2.10, STO P730 @ bid 4.00, STO C800 @ bid 3.50, BTO C830 @ ask 1.20, x10
        rt = self.rt
        execute(rt, "treasury.withdraw", {"lifetime_id": T1, "amount_c": 999900000, "currency": "USD"})        # Treasury 1,000.00
        p700, p730 = sc.ensure_option(rt, "SPY261120P00700000").instrument_id, sc.ensure_option(rt, "SPY261120P00730000").instrument_id
        c800, c830 = sc.ensure_option(rt, "SPY261120C00800000").instrument_id, sc.ensure_option(rt, "SPY261120C00830000").instrument_id
        legs = [{"instrument_id": p700, "side": "BTO", "qty": 10}, {"instrument_id": p730, "side": "STO", "qty": 10},
                {"instrument_id": c800, "side": "STO", "qty": 10}, {"instrument_id": c830, "side": "BTO", "qty": 10}]
        for occ, mid in ((p700, "2.05"), (p730, "4.05"), (c800, "3.55"), (c830, "1.15")):
            rt.marks.set(occ, mid)
        rt.strategies = sc.FakeStrategies(legs)
        now = rt.clock.epoch()

        def chain(qt):
            return {p700: sc.chain_rec(p700, "2.00", "2.10", 50, 50, qt, now), p730: sc.chain_rec(p730, "4.00", "4.10", 50, 50, qt, now),
                    c800: sc.chain_rec(c800, "3.50", "3.60", 50, 50, qt, now), c830: sc.chain_rec(c830, "1.10", "1.20", 50, 50, qt, now)}
        for r_ in chain(now - 60).values():
            rt.marks.set_quote(r_)
        # BTO P700 needs 10 x 2.10 x 100 + fee 6.50 = 2,106.50 > 1,000.00: the whole package queues; its need is the sum of the legs':
        # 2,106.50 + 0 + 0 + (1,200.00 + 6.50) = 3,313.00
        r = sc.submit(rt, account_id=US_EXCHANGE, package={"strategy_key": "IRON_CONDOR", "units": 1000}, confirm_large=True)
        self.assertTrue(r.ok, r.body)
        pid = r.body["package"]["package_id"]
        wait = "waiting for funds: needs USD 3,313.00, available USD 1,000.00"
        self.assertEqual(("SUSPENDED", ["SUSPENDED"] * 4, [wait] * 4, "FUNDS_WAIT"), (r.body["package"]["status"], [o["status"] for o in r.body["orders"]],
                                                                                    [o["reason"] for o in r.body["orders"]], r.body["decision"]["reason"]))
        self.assertEqual("SUSPENDED", sc.one(rt, "SELECT status FROM hedge_packages WHERE package_id=?", (pid,))["status"])
        oids = [o["order_id"] for o in r.body["orders"]]
        self.assertEqual(([("INFO", "ORDER_WAITING_FUNDS", True)], [], 0), (self.items(oids[0]), self.items(oids[1]), sc.one(rt, "SELECT COUNT(*) n FROM fills")["n"]))
        # 39,000.00 deposited -> Treasury 40,000.00 >= 3,313.00: the four legs resume together, the package works again
        deposit(rt, 3900000)
        res = execute(rt, "order.resume_funded", {}, actor="matcher")
        self.assertEqual(sorted(oids), sorted(x["order_id"] for x in res.body["resumed"]))
        self.assertEqual("WORKING", sc.one(rt, "SELECT status FROM hedge_packages WHERE package_id=?", (pid,))["status"])
        # the next observation fills all four legs (sells first): the payables 2,106.50 + 1,206.50 = 3,313.00 are covered by the receivables
        # 3,993.50 + 3,493.50 = 7,487.00 due the same day, so nothing is swept; the option margin, put spread 30 x 100 x 10 - (4,000 - 2,100)
        # = 28,100.00, is RESERVED (1.0.4, SR-13 / decision s7.6: margin_state, no cash moves, 1310 stays 0), covered by Treasury 40,000.00
        self.assertEqual(1, sc.matcher_for(rt, sc.FakeCache(chain(now - 30))).on_quotes([c800]))
        self.assertEqual(["FILLED"] * 4, [o["status"] for o in sc.q(rt, "SELECT status FROM orders WHERE package_id=? ORDER BY order_id", (pid,))])
        self.assertEqual((0, 2810000, 4000000, 0), (bal(rt, US_EXCHANGE, "1310"), sc.one(rt, "SELECT options_required_c r FROM margin_state WHERE account_id=?", (US_EXCHANGE,))["r"],
                                                   bal(rt, TREASURY, "1010"), sc.one(rt, "SELECT COUNT(*) n FROM transfers WHERE kind='SWEEP_IN'")["n"]))
        self.assertEqual([("INFO", "ORDER_WAITING_FUNDS", False)], self.items(oids[0]))
        assert_integrity(self, rt, "full")


# ===== ADV of names resolved on demand (VMRK) ==============================================================================================
def _chart_payload(bars) -> bytes:
    """A Yahoo ``range=3mo&interval=1d`` chart for VMRK: one bar per (day, volume) at 09:30 ET, gmtoffset -14400 (EDT)."""
    ts = [int(datetime.combine(date.fromisoformat(d), dtime(9, 30), NY).timestamp()) for d, _v in bars]
    n = len(bars)
    quote_block = {"open": [4.0] * n, "high": [4.2] * n, "low": [3.9] * n, "close": [4.1] * n, "volume": [v for _d, v in bars]}
    meta = dict(VMRK_META, gmtoffset=-14400, dataGranularity="1d", range="3mo", regularMarketPrice=4.12)
    return json.dumps({"chart": {"result": [{"meta": meta, "timestamp": ts, "indicators": {"quote": [quote_block]}}], "error": None}}).encode("utf-8")


class AdvFromYahooDailyBars(unittest.TestCase):
    def test_adv_from_a_fed_yahoo_daily_payload(self):
        seen = []

        def getter(url, ua, timeout, accept):
            seen.append(url)
            return _chart_payload(VMRK_BARS)
        yc = YahooChart(FakeClock("2026-10-20T16:30:00-04:00"), getter=getter, sleep=lambda s: None)
        bars = yc.daily("VMRK", ADV_DAYS)
        self.assertEqual(1, len(seen))
        self.assertIn("/v8/finance/chart/VMRK?range=3mo&interval=1d", seen[0])
        self.assertEqual(12, len(bars))
        # completed sessions with a volume: the 10 days 10-06 .. 10-19 (10-02 has none, 10-20 is today) -> 1,000,000 / 10 = 100,000
        self.assertEqual({"adv": 100000, "sessions": 10, "first": "2026-10-06", "last": "2026-10-19", "basis": VMRK_BASIS},
                         adv_from_daily(bars, date(2026, 10, 20)))
        # HALF_EVEN to whole shares: the first 5 sessions 120,000 + 80,000 + 95,000 + 110,000 + 105,000 = 510,000 / 5 = 102,000
        self.assertEqual(102000, adv_from_daily(bars, date(2026, 10, 13))["adv"])
        # fewer than 5 completed sessions with a volume -> no ADV (no borrow); a series of zeros -> no ADV
        self.assertIsNone(adv_from_daily(bars, date(2026, 10, 12)))
        zeros = YahooChart(FakeClock("2026-10-20T16:30:00-04:00"), getter=lambda *a: _chart_payload([(d, 0) for d in VMRK_DAYS]), sleep=lambda s: None)
        self.assertIsNone(adv_from_daily(zeros.daily("VMRK", ADV_DAYS), date(2026, 10, 20)))


class ResolvedNameBorrow(Base):
    def set_vmrk_daily(self, vols=None):
        bars = VMRK_BARS if vols is None else [(d, v) for d, v in zip(VMRK_DAYS, vols)]
        self.rt.fp.set_daily("VMRK", [(d, "4.00", "4.20", "3.90", "4.10", v) for d, v in bars])

    def test_resolved_name_gets_its_adv_and_can_be_borrowed(self):
        self.rt.fp.set_meta("VMRK", VMRK_META)
        self.set_vmrk_daily()
        self.rt.worker.resolve("VMRK")
        rep = self.rt.worker.tick()                      # resolve (instruments.upsert) then the ADV (instruments.set_adv) in the same tick
        self.assertEqual((["VMRK"], ["VMRK"]), (rep["resolved"], rep["adv"]))
        inst = self.rt.instruments.get("VMRK")
        self.assertEqual((100000, VMRK_BASIS, 10, "2026-10-06", "2026-10-19"), tuple(inst.spec.get(k) for k in ("adv", "adv_basis", "adv_sessions", "adv_from", "adv_to")))
        quote(self.rt, "VMRK", "4.12")
        # no FINRA row in the last 3 sessions -> tier T3, availability min(qty, 10 % x 100,000 = 10,000)
        loc = lending.locate(self.rt, ACCT, inst, 5000)
        self.assertEqual((True, 5000, "T3"), (loc.available, loc.qty_available, loc.tier))
        self.assertIn(f"ADV 100,000 ({VMRK_BASIS})", loc.basis)
        self.assertEqual(10000, lending.locate(self.rt, ACCT, inst, 20000).qty_available)

    def test_name_in_the_master_without_adv_gets_it_when_locate_asks(self):
        # VMRK resolved before the hotfix: in the master with no ADV -> NO borrow, and locate asks the worker for one
        res = self.rt.bus.execute(Command("instruments.upsert", {"symbol": "VMRK", "meta": VMRK_META}, "fq:upsert-vmrk-0001", "worker"))
        self.assertTrue(res.ok, res.body)
        self.rt.instruments.refresh("VMRK")
        quote(self.rt, "VMRK", "4.12")
        inst = self.rt.instruments.get("VMRK")
        self.assertNotIn("adv", inst.spec)
        loc = lending.locate(self.rt, ACCT, inst, 5000)
        self.assertEqual((False, "NONE"), (loc.available, loc.tier))
        self.assertEqual("VMRK: no average daily volume in the master; fetching it from Yahoo daily bars (3 months): preview again in a few seconds;"
                         " no borrow (BORROW_AVAIL)", loc.basis)
        self.set_vmrk_daily()
        self.assertEqual(["VMRK"], self.rt.worker.tick()["adv"])
        inst = self.rt.instruments.get("VMRK")
        self.assertEqual((100000, VMRK_BASIS), (inst.spec.get("adv"), inst.spec.get("adv_basis")))
        loc = lending.locate(self.rt, ACCT, inst, 5000)
        self.assertEqual((True, 5000, "T3"), (loc.available, loc.qty_available, loc.tier))

    def test_no_volume_no_adv_no_borrow(self):
        self.rt.fp.set_meta("VMRK", VMRK_META)
        self.set_vmrk_daily([None] * len(VMRK_DAYS))
        self.rt.worker.resolve("VMRK")
        rep = self.rt.worker.tick()
        self.assertEqual((["VMRK"], []), (rep["resolved"], rep["adv"]))
        inst = self.rt.instruments.get("VMRK")
        self.assertNotIn("adv", inst.spec)
        quote(self.rt, "VMRK", "4.12")
        loc = lending.locate(self.rt, ACCT, inst, 5000)
        self.assertEqual((False, "NONE", 0), (loc.available, loc.tier, loc.qty_available))
        self.assertTrue(loc.basis.startswith("VMRK: no average daily volume in the master; Yahoo daily bars gave none"), loc.basis)
        self.assertEqual([], self.rt.worker.tick()["adv"])               # not fetched again within ADV_RETRY_S


if __name__ == "__main__":
    unittest.main()
