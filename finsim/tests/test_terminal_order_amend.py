"""FINSIM1_PLAN P2 Part B (release 1.0.6): amend and replace (OMS-03, task 5), good-till-date and next-session orders (OMS-11, task 7)
and the pure review (OMS-08, task 6) - P2 acceptance 1 (b), (c), (e), (f); directive section 3A ("Modify; Cancel; Replace; Review";
"Orders scheduled for the next market session"), section 15 ("Next-session instructions"), section 18 #1-5.  Every expected number is
worked by hand in the comment next to it.

Scaffolding: the WP-02 order runtime of test_terminal_orders (Test Book T1, FixedMarks with a quote record per test, the Matcher on a
dict cache) with ``orders.bind`` run, so the 1.0.6 order columns exist; the short-sale cases use test_terminal_shorts' runtime (orders +
lending, ABC with FINRA rows: tier GC, borrow fee 0.30 %).
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import datetime

from tests import test_terminal_orders as sc
from tests import test_terminal_shorts as sh
from tests.terminal_helpers import US_EXCHANGE, assert_integrity, fund
from finsim.terminal import accounting, orders, orders_rules
from finsim.terminal.clock import NY
from finsim.terminal.commands import Command

TUE_1400 = "2026-10-20T14:00:00-04:00"


def ep(iso_ny: str) -> int:
    return int(datetime.fromisoformat(iso_ny).replace(tzinfo=NY).timestamp())


def runtime(tmp, at=TUE_1400):
    rt = sc.runtime(tmp, at=at)
    orders.bind(rt)                        # the 1.0.6 columns (amend_count, replaces / replaced_by, gtd_date, activate_ts) and the C5 recorder
    return rt


def cmd(rt, kind, payload, rid, actor="ui"):
    return rt.bus.execute(Command(kind, payload, rid, actor))


def reserved(rt, account=US_EXCHANGE):
    with rt.db.reader() as conn:
        return accounting.reserved_c(conn, account, "USD", rt.marks.snapshot(), rt.instruments)


def events(rt, oid):
    return [r["note"] for r in sc.q(rt, "SELECT note FROM order_events WHERE order_id=? ORDER BY id", (oid,))]


def aapl(rt, last="339.50", age=60, vol=61900):
    now = rt.clock.epoch()
    rt.marks.set_quote(sc.rec("AAPL", last, now - age, now, bar=(now - 120, vol)))


# ===== amend (OMS-03; acceptance 1 b) ==============================================================================================================
class AmendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-amend-")
        self.rt = runtime(self.tmp)
        fund(self.rt, US_EXCHANGE, 100000000)              # US Exchange 1,000,000.00; Treasury keeps 9,000,000.00
        aapl(self.rt)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def resting(self, px="50.00", qty=100):
        r = sc.submit(self.rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=qty, order_type="LIMIT", limit_px=px, tif="DAY")
        self.assertTrue(r.ok, r.body)
        self.assertEqual("WORKING", r.body["order"]["status"])
        return r.body["order"]["order_id"]

    def test_b_amend_re_reserves_and_resets_the_first_look(self):
        rt = self.rt
        oid = self.resting()
        nav0 = rt.accounting.nav("T1:ALL", snapshot=rt.marks.snapshot()).nav_c
        # LIMIT BUY 100 @ 50.00 reserves 100 x 50.00 + the schedule fee max(100 x 0.005 = 0.50, 1.00) = 5,000.00 + 1.00 = 5,001.00
        self.assertEqual(500100, reserved(rt))
        r = cmd(rt, "order.amend", {"order_id": oid, "limit_px": "51.00"}, "ui:amend-b-0001")
        self.assertTrue(r.ok, r.body)
        # amended to 51.00: 100 x 51.00 + 1.00 = 5,101.00 (the 5,001.00 it held was released into the check, never counted twice)
        self.assertEqual(510100, reserved(rt))
        row = sc.order_row(rt, oid)
        # prices are stored as the engine stores them (qprice: 51.00 -> "51")
        self.assertEqual(("WORKING", "51", 1, 100, 0), (row["status"], row["limit_px"], row["amend_count"], row["qty"], row["filled_qty"]))
        self.assertEqual("AMENDED limit 50.00 -> 51.00", r.body["event"])
        ev = events(rt, oid)
        self.assertIn("AMENDED limit 50.00 -> 51.00", ev)
        # EXEC-5: first_eval_done was reset, so the amended order took a FIRST look again (not marketable: 51.00 < 339.50)
        self.assertTrue(ev[-1].startswith("first executable look: NOT_MARKETABLE"), ev[-1])
        self.assertEqual(ev.index("AMENDED limit 50.00 -> 51.00"), len(ev) - 2)
        # a resting order changes no position and no NAV
        self.assertEqual(0, sc.one(rt, "SELECT COUNT(*) n FROM lots")["n"])
        self.assertEqual(nav0, rt.accounting.nav("T1:ALL", snapshot=rt.marks.snapshot()).nav_c)
        self.assertEqual(1, sc.one(rt, "SELECT COUNT(*) n FROM events WHERE type='ORDER_AMENDED' AND ref_id=?", (oid,))["n"])
        assert_integrity(self, rt)

    def test_amend_quantity_and_time_in_force(self):
        rt = self.rt
        oid = self.resting(px="51.00")
        r = cmd(rt, "order.amend", {"order_id": oid, "qty": 300, "tif": "GTD", "gtd_date": "2026-11-20"}, "ui:amend-q-0001")
        self.assertTrue(r.ok, r.body)
        # 300 x 51.00 + max(300 x 0.005 = 1.50, 1.00) = 15,300.00 + 1.50 = 15,301.50
        self.assertEqual(1530150, reserved(rt))
        row = sc.order_row(rt, oid)
        # GTD is GTC + gtd_date (the column's CHECK lists DAY / GTC, D2); it works until Fri 2026-11-20 16:00 ET
        self.assertEqual((300, "GTC", "2026-11-20", ep("2026-11-20T16:00")), (row["qty"], row["tif"], row["gtd_date"], row["expires_ts"]))
        self.assertEqual("AMENDED qty 100 -> 300; tif DAY -> GTD 2026-11-20", r.body["event"])

    def test_amend_refusals_write_nothing(self):
        rt = self.rt
        oid = self.resting()
        before = (sc.order_row(rt, oid), events(rt, oid), reserved(rt))
        cases = [({"limit_px": "50.00"}, 400, "NOTHING_TO_AMEND"),                 # the order's own terms
                 ({"order_type": "MARKET"}, 400, "AMEND_FIELDS"),                  # the type changes only by a replace
                 ({"stop_px": "40.00"}, 400, "AMEND_FIELDS"),                      # a limit order has no stop
                 ({"limit_px": "50.001"}, 400, "PRICE_TICK"),                      # off the 0.01 tick
                 # 300,000 x 50.00 = 15,000,000.00 + fee: own 1,000,000.00 less nothing else + Treasury 9,000,000.00 cannot fund it
                 ({"qty": 300000}, 409, "INSUFFICIENT_FUNDS")]
        for i, (change, http, code) in enumerate(cases):
            r = cmd(rt, "order.amend", dict(change, order_id=oid), f"ui:amend-x-{i:04d}")
            self.assertEqual((False, http, code), (r.ok, r.http, r.code), (change, r.body))
        self.assertEqual(before, (sc.order_row(rt, oid), events(rt, oid), reserved(rt)))
        # an order that has ended is history: cancelled -> 409 NOT_AMENDABLE
        self.assertTrue(cmd(rt, "order.cancel", {"order_id": oid}, "ui:amend-x-cancel").ok)
        r = cmd(rt, "order.amend", {"order_id": oid, "limit_px": "52.00"}, "ui:amend-x-0100")
        self.assertEqual((409, "NOT_AMENDABLE"), (r.http, r.code))
        # an unknown order: the command says NOT_FOUND (the HTTP route answers 404 before the command; GET / PATCH / replace)
        self.assertEqual((400, "NOT_FOUND"), (lambda x: (x.http, x.code))(cmd(rt, "order.amend", {"order_id": "O-999999", "limit_px": "1.00"}, "ui:amend-x-0101")))

    def test_amend_keeps_what_has_filled(self):
        rt = self.rt
        # AAPL last 10.00 on a 4,000-share bar: a MARKET BUY 1,000 fills 400 (10 % participation) and stays PARTIAL
        aapl(rt, last="10.00", vol=4000)
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=1000, order_type="MARKET", tif="DAY")
        oid = r.body["order"]["order_id"]
        self.assertEqual(("PARTIAL", 400), (r.body["order"]["status"], r.body["order"]["filled_qty"]))
        r = cmd(rt, "order.amend", {"order_id": oid, "qty": 300}, "ui:amend-f-0001")
        self.assertEqual((400, "QTY_INVALID"), (r.http, r.code))                # 300 is below the 400 already filled
        r = cmd(rt, "order.amend", {"order_id": oid, "qty": 500}, "ui:amend-f-0002")
        self.assertTrue(r.ok, r.body)
        row = sc.order_row(rt, oid)
        # the new total 500 keeps the 400 filled: 100 still to fill (no new bar yet, so nothing fills on the amend's look)
        self.assertEqual(("PARTIAL", 500, 400), (row["status"], row["qty"], row["filled_qty"]))

    def test_a_held_order_stays_held(self):
        rt = self.rt
        # BUY 40,000 AAPL LIMIT 339.00: 13,560,000.00 + fee 200.00 > own 1,000,000.00 + Treasury 9,000,000.00 -> queued SUSPENDED (FUNDS_WAIT)
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=40000, order_type="LIMIT", limit_px="339.00", tif="DAY", confirm_large=True)
        oid = r.body["order"]["order_id"]
        self.assertEqual("SUSPENDED", r.body["order"]["status"])
        # the large-notional guard applies to an amend too (13,520,000.00 > 25 % of NAV): confirmed here
        r = cmd(rt, "order.amend", {"order_id": oid, "limit_px": "338.00"}, "ui:amend-h-0000")
        self.assertEqual((409, "CONFIRM_REQUIRED"), (r.http, r.code))
        r = cmd(rt, "order.amend", {"order_id": oid, "limit_px": "338.00", "confirm_large": True}, "ui:amend-h-0001")
        self.assertTrue(r.ok, r.body)
        row = sc.order_row(rt, oid)
        self.assertEqual(("SUSPENDED", "338", 0), (row["status"], row["limit_px"], reserved(rt)))   # a held order reserves nothing and stays held


# ===== replace (OMS-03; acceptance 1 c) ==============================================================================================================
class ReplaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-replace-")

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_c_replace_of_a_working_short_keeps_its_borrow_and_links_both_orders(self):
        rt = self.rt = sh.runtime(self.tmp)
        orders.bind(rt)
        sh.setup_abc(rt)                                     # ABC last 50.00, tier GC, US Exchange 200,000.00
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="ABC", side="SELL_SHORT", qty=1000, order_type="LIMIT", limit_px="55.00", tif="DAY")
        self.assertTrue(r.ok, r.body)
        old = r.body["order"]["order_id"]
        bid = r.body["borrow"]["borrow_id"]
        self.assertEqual(("WORKING", bid), (r.body["order"]["status"], sc.order_row(rt, old)["borrow_id"]))
        # a replacement above what the borrow covers is refused and writes nothing
        r = cmd(rt, "order.replace", {"order_id": old, "qty": 1200}, "ui:repl-c-0000")
        self.assertEqual((409, "BORROW_LIMIT"), (r.http, r.code))
        self.assertEqual("WORKING", sc.order_row(rt, old)["status"])
        r = cmd(rt, "order.replace", {"order_id": old, "limit_px": "56.00"}, "ui:repl-c-0001")
        self.assertTrue(r.ok, r.body)
        new = r.body["order"]["order_id"]
        o, n, b = sc.order_row(rt, old), sc.order_row(rt, new), sh.borrow_row(rt, bid)
        self.assertEqual(("CANCELLED", f"replaced by {new}", new), (o["status"], o["reason"], o["replaced_by_order_id"]))
        self.assertEqual(("WORKING", old, bid, 1000, "56", "SELL_SHORT"), (n["status"], n["replaces_order_id"], n["borrow_id"], n["qty"], n["limit_px"], n["side"]))
        # the borrow follows the order it backs and nothing was returned to the lender (release_for_cancel was not called)
        self.assertEqual((new, "OPEN", 0, 1000), (b["order_id"], b["status"], b["qty_returned"], b["qty"]))
        self.assertEqual(0, sc.one(rt, "SELECT COUNT(*) n FROM events WHERE type='BORROW_RETURNED'")["n"])
        # the replacement's later cancel (the chain filled nothing) returns the whole borrow
        self.assertTrue(cmd(rt, "order.cancel", {"order_id": new}, "ui:repl-c-0002").ok)
        b = sh.borrow_row(rt, bid)
        self.assertEqual((1000, "CLOSED"), (b["qty_returned"], b["status"]))
        assert_integrity(self, rt)

    def test_replace_after_a_partial_fill_never_returns_the_borrowed_shares_it_sold(self):
        rt = self.rt = sh.runtime(self.tmp)
        orders.bind(rt)
        sh.setup_abc(rt)
        sh.quote(rt, "ABC", "50.00", 3000)                   # a 3,000-share bar: 10 % participation fills 300 of a MARKET 1,000
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="ABC", side="SELL_SHORT", qty=1000, order_type="MARKET", tif="DAY")
        old = r.body["order"]["order_id"]
        bid = r.body["borrow"]["borrow_id"]
        self.assertEqual(("PARTIAL", 300), (r.body["order"]["status"], r.body["order"]["filled_qty"]))
        r = cmd(rt, "order.replace", {"order_id": old, "order_type": "LIMIT", "limit_px": "51.00"}, "ui:repl-p-0001")
        self.assertTrue(r.ok, r.body)
        new = r.body["order"]["order_id"]
        # the new order is for the unfilled 700 (its default), under the same borrow; the old one ended "300 of 1,000 filled"
        self.assertEqual((700, bid, "LIMIT"), (sc.order_row(rt, new)["qty"], sc.order_row(rt, new)["borrow_id"], sc.order_row(rt, new)["order_type"]))
        self.assertEqual(("CANCELLED", 300), (sc.order_row(rt, old)["status"], sc.order_row(rt, old)["filled_qty"]))
        # cancelling the replacement must not return the 300 shares that back the short: the chain filled 300
        self.assertTrue(cmd(rt, "order.cancel", {"order_id": new}, "ui:repl-p-0002").ok)
        b = sh.borrow_row(rt, bid)
        self.assertEqual(("OPEN", 0), (b["status"], b["qty_returned"]))
        assert_integrity(self, rt)

    def test_replace_moves_the_waiting_hedge_legs_to_the_new_primary(self):
        rt = self.rt = runtime(self.tmp)
        fund(rt, US_EXCHANGE, 100000000)
        aapl(rt)
        rt.hedges = sc.FakeHedges([{"instrument_id": "SPY", "side": "BUY", "qty": 50, "order_type": "MARKET"}])
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=100, order_type="LIMIT", limit_px="300.00", tif="DAY",
                      hedge={"candidate_id": "TEST_HEDGE"})
        self.assertTrue(r.ok, r.body)
        old = r.body["order"]["order_id"]
        pid = r.body["hedge_package"]
        leg = sc.one(rt, "SELECT order_id FROM orders WHERE parent_order_id=?", (old,))["order_id"]
        r = cmd(rt, "order.replace", {"order_id": old, "limit_px": "301.00"}, "ui:repl-h-0001")
        self.assertTrue(r.ok, r.body)
        new = r.body["order"]["order_id"]
        self.assertEqual((new, "WAITING_PARENT"), (sc.order_row(rt, leg)["parent_order_id"], sc.order_row(rt, leg)["status"]))
        self.assertEqual(new, sc.one(rt, "SELECT primary_order_id FROM hedge_packages WHERE package_id=?", (pid,))["primary_order_id"])
        self.assertEqual(pid, sc.order_row(rt, new)["package_id"])
        self.assertEqual([leg], r.body["hedge_legs_moved"])


# ===== GTD and next session (OMS-11; acceptance 1 e, f) ==============================================================================================
class TifTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-tif-")
        self.rt = runtime(self.tmp)
        fund(self.rt, US_EXCHANGE, 100000000)
        aapl(self.rt)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_e_gtd_expires_at_the_close_of_its_date(self):
        rt = self.rt
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=100, order_type="LIMIT", limit_px="50.00", tif="GTD", gtd_date="2026-10-22")
        self.assertTrue(r.ok, r.body)
        oid = r.body["order"]["order_id"]
        row = sc.order_row(rt, oid)
        # placed Tue 10-20 14:00: good till Thu 10-22, i.e. until Thu 16:00 ET; stored as GTC + gtd_date
        self.assertEqual(("GTC", "2026-10-22", ep("2026-10-22T16:00")), (row["tif"], row["gtd_date"], row["expires_ts"]))
        self.assertEqual("GTD 10-22 (expires 10-22 16:00 ET)", __import__("finsim.terminal.blotters", fromlist=["x"]).expiration_words(row))
        # Wed 16:01: a DAY order would have expired; the GTD order is still working
        rt.clock.set("2026-10-21T16:01:00-04:00")
        cmd(rt, "order.expire", {}, "lc:expire-e-0001", actor="lifecycle")
        self.assertEqual("WORKING", sc.order_row(rt, oid)["status"])
        # Thu 16:01: past its date's close -> EXPIRED "GTD date reached"
        rt.clock.set("2026-10-22T16:01:00-04:00")
        r = cmd(rt, "order.expire", {}, "lc:expire-e-0002", actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        row = sc.order_row(rt, oid)
        self.assertEqual(("EXPIRED", "GTD date reached (good till 2026-10-22)"), (row["status"], row["reason"]))
        self.assertEqual(0, reserved(rt))

    def test_gtd_date_rules(self):
        rt = self.rt
        base = dict(account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=100, order_type="LIMIT", limit_px="50.00", tif="GTD")
        for gtd, code in ((None, "GTD_DATE"), ("2026-10-19", "GTD_DATE"), ("2027-01-19", "GTD_DATE"), ("20-11-2026", "GTD_DATE")):
            r = sc.submit(rt, **dict(base, **({"gtd_date": gtd} if gtd else {})))
            self.assertEqual((False, code), (r.ok, r.code), (gtd, r.body))
        # Sat 2026-10-24 is no session: the order works until Fri 10-23 16:00 ET (the last close on or before the date)
        r = sc.submit(rt, **dict(base, gtd_date="2026-10-24"))
        self.assertTrue(r.ok, r.body)
        self.assertEqual(ep("2026-10-23T16:00"), sc.order_row(rt, r.body["order"]["order_id"])["expires_ts"])

    def test_f_a_next_session_order_does_not_fill_before_its_session(self):
        rt = self.rt
        # Tue 14:00, the session is open and AAPL is executable: a MARKET BUY 100 for the NEXT session is queued until Wed 09:30
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=100, order_type="MARKET", tif="NEXT_SESSION")
        self.assertTrue(r.ok, r.body)
        oid = r.body["order"]["order_id"]
        row = sc.order_row(rt, oid)
        self.assertEqual(("WORKING", "DAY", sc.WED_OPEN, sc.WED_CLOSE, 0), (row["status"], row["tif"], row["activate_ts"], row["expires_ts"], row["filled_qty"]))
        self.assertEqual("AWAITING_SESSION", r.body["decision"]["reason"])
        self.assertIn("queued for the next session", events(rt, oid)[0])
        # Tue 15:00 with a fresh executable print: the matcher leaves it alone (no command at all)
        rt.clock.set("2026-10-20T15:00:00-04:00")
        cache = sc.FakeCache()
        now = rt.clock.epoch()
        cache["AAPL"] = sc.rec("AAPL", "340.00", now - 30, now, bar=(now - 90, 61900))
        self.assertEqual(0, sc.matcher_for(rt, cache).on_quotes(["AAPL"]))
        self.assertEqual((0, "WORKING"), (sc.order_row(rt, oid)["filled_qty"], sc.order_row(rt, oid)["status"]))
        # Wed 09:35, a print of the new session: it fills (100 x 341.00, the 10 % cap of a 61,900 bar is 6,190 >= 100)
        rt.clock.set("2026-10-21T09:35:00-04:00")
        now = rt.clock.epoch()
        cache["AAPL"] = sc.rec("AAPL", "341.00", now - 30, now, bar=(now - 90, 61900))
        self.assertEqual(1, sc.matcher_for(rt, cache).on_quotes(["AAPL"]))
        row = sc.order_row(rt, oid)
        self.assertEqual(("FILLED", 100, "341"), (row["status"], row["filled_qty"], row["avg_px"]))

    def test_pure_rules_for_queued_orders_and_the_review(self):
        o = sc.row(otype="LIMIT", limit="50.00", first=True)
        o.activate_ts = sc.WED_OPEN
        rec = sc.rec("AAPL", "339.50", sc.TUE_1400 - 60, sc.TUE_1400)
        d = orders_rules.evaluate(o, rec, sc.sess(), sc.TUE_1400)
        self.assertEqual(("NONE", "AWAITING_SESSION"), (d.action, d.reason))
        self.assertFalse(d.wants_command)
        o.activate_ts = None
        rv = orders_rules.review(o, rec, sc.sess(), sc.TUE_1400)
        # a BUY limit 50.00 against the observed last 339.50: (50.00 - 339.50) / 339.50 = -85.27 %
        self.assertEqual(("NONE", "NOT_MARKETABLE", "-85.27"), (rv["action"], rv["reason"], rv["distance"]["pct"]))
        self.assertEqual("would not fill now: the limit is not marketable at the observed price (limit 50.00 is 85.27 % below the observed last 339.50)",
                         rv["text"])
        m = sc.row(otype="MARKET", qty=100)
        rv = orders_rules.review(m, sc.rec("AAPL", "339.50", sc.TUE_1400 - 60, sc.TUE_1400, bar=(sc.TUE_1400 - 120, 61900)), sc.sess(), sc.TUE_1400)
        self.assertEqual(("FILL", "would fill 100 at 339.50 now (last)"), (rv["action"], rv["text"]))


if __name__ == "__main__":
    unittest.main()
