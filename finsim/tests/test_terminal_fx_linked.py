"""FX_CCS_PLAN rev 2 (release 1.0.6.1, package P-D) money tests of the K4 hooks in ``fx.py``: the linked spot deal (the
cross-currency swap's inception conversion), the scheduled OTC flows in the funding check (D-T1), the linked-settlement hold
of SETTLE_DUE and ``due_deals`` (D-T2, D7) and ``settle_linked``'s receive and pay phases inside the START (D-T3).

Every expected number is worked by hand in a comment; nothing is pasted from the code's output.  No network: FakeClock,
FixedMarks (EURUSD 1.1158, GBPUSD 1.3323: the plan's 10-28 17:42 EDT prints), a temp database, books="real" (the Fund
Book's F1:PB_GS, F1 Treasury USD 25,000,000.00).

The swap itself is P-C's: here a fixture XCCY row (OPEN, start 2026-11-02, we borrow EUR 2,000,000.00 and lend USD
2,231,600.00 = 2,000,000.00 x 1.1158) stands for it, and the START's exchange is a stand-in entry with the lines of FX_CCS_PLAN
4.2, so the hooks are tested apart from the product code.

Worked conversion (D6): SELL EUR 2,000,000.00 / BUY USD at the bid on the pinned mid X0 = 1.1158: 1.1158 - 0.2 pip (0.00002) =
1.11578; USD = 2,000,000.00 x 1.11578 = 2,231,560.00 exactly; at the mid it would be 2,000,000.00 x 1.1158 = 2,231,600.00, so the
spread cost is 2,231,600.00 - 2,231,560.00 = 40.00.  Trade date Thu 2026-10-29 (FX day, 10:00 ET); the EURUSD spot date is Mon
2026-11-02 (T+1 Fri 10-30 is a TARGET2 day, T+2 skips the weekend; 11-02 is good in TARGET2 and New York).
"""
from __future__ import annotations

import json
import shutil
import tempfile
import types
import unittest
from datetime import date

from tests.terminal_helpers import bal, execute, make_runtime
from finsim.terminal import execution, fx, lifecycle, prime
from finsim.terminal.commands import ConflictError
from finsim.terminal.db import dumps
from finsim.terminal.ledger import Entry, cr, dr

PB = "F1:PB_GS"
CID = "SWP-000007"
AT = "2026-10-29T10:00:00-04:00"
MARKS = {"EURUSD": ("1.1158", "delayed"), "GBPUSD": ("1.3323", "delayed")}
XCCY_TERMS = {"kind": "XCCY", "pay_ccy": "EUR", "recv_ccy": "USD", "foreign_ccy": "EUR", "deal_rate": "1.1158", "start_date": "2026-11-02",
              "maturity_date": "2028-11-06", "legs": {"pay": {"currency": "EUR", "notional_c": 200000000},
                                                    "receive": {"currency": "USD", "notional_c": 223160000}}}


def _h_open_pb_unchecked(ctx, payload):
    return prime.open_pb(ctx, payload["lifetime_id"], payload["broker"], check_eligibility=False)


def _h_linked_spot(ctx, payload):
    """Test command: ``fx.book_linked_spot`` with the payload's arguments (what P-C's booking does inside its own command)."""
    ref = payload.get("settle_ref")
    return fx.book_linked_spot(ctx, payload["account_id"], payload["buy_ccy"], payload["sell_ccy"], int(payload["amount_c"]), payload["amount_ccy"],
                               payload["value_date"], payload.get("mid"), payload["link"], tuple(ref) if ref else None,
                               check_funding=payload.get("check_funding", True))


def _h_start_pvp(ctx, payload):
    """Test command: the XCCY START's order of FX_CCS_PLAN 4.2 with a stand-in exchange: (1) settle_linked receive, (2) fund the USD
    lent (only the net shortfall), (3) the exchange lines, (4) settle_linked pay, (5) the NOTIONAL_START row.  ``skip_exchange``
    leaves out (2)-(3) so the EUR payable cannot be funded."""
    cid, aid, d = payload["contract_id"], payload["account_id"], payload["date"]
    out = {"receive": fx.settle_linked(ctx, "xccy_start", cid, d, "receive")}
    if not payload.get("skip_exchange"):
        out["funding"] = execution.fund(ctx, aid, "USD", 223160000, "xccy", cid, immediate=True, services=ctx.services).to_dict()
        lines = [dr(aid, "1360", "USD", 223160000, instrument_id=cid, memo="notional lent"), cr(aid, "1010", "USD", 223160000, memo="notional paid"),
                 dr(aid, "1010", "EUR", 200000000, fx_rate="1.1158", memo="notional received"),
                 cr(aid, "2360", "EUR", 200000000, instrument_id=cid, fx_rate="1.1158", memo="notional borrowed")]
        out["exchange_entry"] = ctx.post(Entry("XCCY_EXCHANGE", f"test start exchange {cid}", lines, f"xccy:{cid}:start", ref_type="otc", ref_id=cid,
                                               business_date=d))
    out["pay"] = fx.settle_linked(ctx, "xccy_start", cid, d, "pay")
    ctx.conn.execute("INSERT INTO otc_resets(contract_id, event_date, kind, amount_c, detail_json, entry_id) VALUES (?,?,'NOTIONAL_START',?,?,?)",
                     (cid, d, 223160000, dumps({"test": True}), out.get("exchange_entry")))
    return out


def runtime(tmp, at=AT):
    rt = make_runtime(tmp, at=at, books="real", marks=MARKS)
    prime.register(rt.bus)
    prime.bind(rt)
    fx.register(rt.bus)
    fx.bind(rt)                                   # the settlement reference columns + FX_FORWARD_DUE
    rt.fx = fx
    lifecycle.register(rt.bus, rt)
    rt.lifecycle = lifecycle.LifecycleScheduler(rt.bus, rt.db, rt, rt.clock, rt.log)
    rt.bus.register("test.open_pb_unchecked", _h_open_pb_unchecked)
    rt.bus.register("test.linked_spot", _h_linked_spot)
    rt.bus.register("test.start_pvp", _h_start_pvp)
    # the product hook answers nothing yet (P-C's float / float flows are built apart): fx reads the swap's exchanges from its row
    rt.swaps_trs = types.SimpleNamespace(scheduled_flows=lambda conn, row: None)
    r = execute(rt, "account.open_pb", {"lifetime_id": "F1", "broker": "GS"})
    assert r.ok, r.body
    return rt


def q(rt, sql, params=()):
    with rt.db.reader() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(rt, sql, params=()):
    rows = q(rt, sql, params)
    return rows[0] if rows else None


def insert_xccy(rt, cid=CID, status="OPEN", terms=None):
    """The fixture swap row (what P-C's otc.book writes; only the columns the hooks read)."""
    with rt.db.writer() as tx:
        tx.conn.execute("INSERT INTO otc_contracts(contract_id, command_id, account_id, broker, kind, terms_json, notional_c, currency, start_date, maturity_date,"
                        " margin_mode, im_c, status, mark_c, vm_balance_c) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,0)",
                        (cid, 0, PB, "GS", "XCCY", json.dumps(terms or XCCY_TERMS), 223160000, "USD", "2026-11-02", "2028-11-06", "CTM", 0, status))


def set_status(rt, cid, status):
    with rt.db.writer() as tx:
        tx.conn.execute("UPDATE otc_contracts SET status=? WHERE contract_id=?", (status, cid))


def conversion(rt, request_id="ui:conv-0001", **over):
    p = {"account_id": PB, "buy_ccy": "USD", "sell_ccy": "EUR", "amount_c": 200000000, "amount_ccy": "EUR", "value_date": "2026-11-02", "mid": "1.1158",
         "link": {"type": "xccy_start", "id": CID, "text": f"inception conversion of {CID}"}, "settle_ref": ["xccy_start", CID]}
    p.update(over)
    return execute(rt, "test.linked_spot", p, request_id)


class LinkedSpotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-fxl-")
        self.rt = runtime(self.tmp)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dt1_conversion_is_fundable_only_with_the_swap_start_receipt(self):
        rt = self.rt
        # ---- without the swap: F1:PB_GS holds no EUR and F1 Treasury holds no EUR -> SELL EUR 2,000,000.00 value 11-02 is unfundable (OA-11)
        r = conversion(rt)
        self.assertFalse(r.ok)
        self.assertEqual((409, "SETTLEMENT_UNFUNDABLE"), (r.http, r.code))
        self.assertEqual(200000000, r.body["error"]["details"]["shortfall_c"])                 # short EUR 2,000,000.00: nothing to deliver
        self.assertEqual(0, one(rt, "SELECT COUNT(*) n FROM fx_deals")["n"])            # nothing written
        # ---- with the swap's start receipt (we borrow EUR 2,000,000.00 on 11-02): fundable, the OTC flow counted separately
        insert_xccy(rt)
        with rt.db.reader() as conn:
            self.assertEqual((200000000, 0), fx.scheduled_otc_flows(conn, PB, "EUR", date(2026, 11, 2), rt))     # + EUR 2,000,000.00 in, nothing out
            self.assertEqual((0, 223160000), fx.scheduled_otc_flows(conn, PB, "USD", date(2026, 11, 2), rt))     # USD 2,231,600.00 lent out
            self.assertEqual((0, 0), fx.scheduled_otc_flows(conn, PB, "EUR", date(2026, 11, 1), rt))             # not due by 11-01
            # by the final exchange (2028-11-06): + EUR 2,000,000.00 at the start and - EUR 2,000,000.00 repaid
            self.assertEqual((200000000, 200000000), fx.scheduled_otc_flows(conn, PB, "EUR", date(2028, 11, 6), rt))
        chk = fx.funding_check(rt, PB, "EUR", 200000000, date(2026, 11, 2), snapshot=rt.marks)
        self.assertEqual((True, 0, 200000000), (chk["fundable"], chk["shortfall_c"], chk["otc_flows_c"]))
        r = conversion(rt)
        self.assertTrue(r.ok, r.body)
        d = r.body["deal"]
        # 1.1158 - 0.00002 = 1.11578; USD 2,000,000.00 x 1.11578 = 2,231,560.00; spread 2,231,600.00 - 2,231,560.00 = 40.00
        self.assertEqual(("SPOT", "USD", 223156000, "EUR", 200000000, "1.11578", "1.1158", 4000, "2026-10-29", "2026-11-02"),
                         (d["kind"], d["buy_ccy"], d["buy_c"], d["sell_ccy"], d["sell_c"], d["rate_quoted"], d["mid"], d["spread_cost_c"], d["trade_date"], d["value_date"]))
        # the entry (FX_CCS_PLAN 4.2 step 3): Dr 1250 USD 2,231,560.00 / Cr 3900 USD; Dr 3900 EUR 2,000,000.00 / Cr 2350 EUR, the EUR lines at 1.11578
        lines = q(rt, "SELECT gl, currency, amount_c, base_c, fx_rate FROM journal_lines WHERE entry_id=? ORDER BY line_no", (r.body["entry_id"],))
        self.assertEqual([("1250", "USD", 223156000, 223156000), ("3900", "USD", -223156000, -223156000), ("3900", "EUR", 200000000, 223156000),
                          ("2350", "EUR", -200000000, -223156000)], [(x["gl"], x["currency"], x["amount_c"], x["base_c"]) for x in lines])
        self.assertEqual({"1.11578"}, {x["fx_rate"] for x in lines if x["currency"] == "EUR"})
        # the two settlement rows: due 11-02, tagged for the swap's START
        rows = q(rt, "SELECT leg, currency, amount_c, due_date, status, ref_type, ref_id FROM settlements WHERE fx_deal_id=? ORDER BY leg", (d["deal_id"],))
        self.assertEqual([("BUY_CCY", "USD", 223156000, "2026-11-02", "PENDING", "xccy_start", CID), ("SELL_CCY", "EUR", -200000000, "2026-11-02", "PENDING", "xccy_start", CID)],
                         [tuple(x.values()) for x in rows])
        inputs = json.loads(one(rt, "SELECT inputs_json FROM fx_deals WHERE deal_id=?", (d["deal_id"],))["inputs_json"])
        self.assertEqual(({"type": "xccy_start", "id": CID, "text": f"inception conversion of {CID}"}, {"ref_type": "xccy_start", "ref_id": CID}),
                         (inputs["link"], inputs["settle_ref"]))

    def test_dt1_product_hook_flows_count_exchanges_not_coupons(self):
        """K5: with the product's ``scheduled_flows`` answering, only exchanges and deliveries count: the last coupon is dated with
        the final exchange and carries the END event too, and is left out (it is not an exchange)."""
        rt = self.rt
        insert_xccy(rt)
        flows = [{"date": "2026-11-02", "ccy": "EUR", "amount_c": 200000000, "what": "start exchange: notional borrowed", "event": "START", "known": True},
                 {"date": "2026-11-02", "ccy": "USD", "amount_c": -223160000, "what": "start exchange: notional lent", "event": "START", "known": True},
                 {"date": "2027-02-04", "ccy": "EUR", "amount_c": -1294583, "what": "pay leg coupon", "event": "PAY", "known": True},
                 {"date": "2028-11-06", "ccy": "EUR", "amount_c": -1300000, "what": "pay leg coupon 2028-08-02..2028-11-02", "event": "END", "known": True},
                 {"date": "2028-11-06", "ccy": "EUR", "amount_c": -200000000, "what": "final exchange: notional borrowed repaid", "event": "END", "known": True},
                 {"date": "2028-11-06", "ccy": "EUR", "amount_c": None, "what": "an unknown flow", "event": "START", "known": False}]
        rt.swaps_trs = types.SimpleNamespace(scheduled_flows=lambda conn, row: flows)
        with rt.db.reader() as conn:
            # EUR by 2028-11-06: + 2,000,000.00 (start) - 2,000,000.00 (final exchange); the two coupons and the unknown flow are not counted
            self.assertEqual((200000000, 200000000), fx.scheduled_otc_flows(conn, PB, "EUR", date(2028, 11, 6), rt))
            self.assertEqual((200000000, 0), fx.scheduled_otc_flows(conn, PB, "EUR", date(2027, 2, 4), rt))
        rt.swaps_trs = types.SimpleNamespace(scheduled_flows=lambda conn, row: [])     # an empty answer for an OPEN swap: the row's exchanges
        with rt.db.reader() as conn:
            self.assertEqual((200000000, 0), fx.scheduled_otc_flows(conn, PB, "EUR", date(2026, 11, 2), rt))

    def test_linked_spot_value_date_must_be_the_spot_date(self):
        rt = self.rt
        insert_xccy(rt)
        r = conversion(rt, value_date="2026-11-03")                     # 10-29's EURUSD spot date is 11-02
        self.assertEqual((409, "FX_VALUE_DATE"), (r.http, r.code))
        r = conversion(rt, settle_ref=["otc", CID])                      # only the linked types are accepted as a settle reference
        self.assertEqual((400, "SETTLE_REF"), (r.http, r.code))
        self.assertEqual(0, one(rt, "SELECT COUNT(*) n FROM fx_deals")["n"])

    def test_dt2_tagged_rows_wait_for_the_start_no_next_day_fallback(self):
        rt = self.rt
        insert_xccy(rt)
        r = conversion(rt)
        self.assertTrue(r.ok, r.body)
        deal_id = r.body["deal"]["deal_id"]
        with rt.db.reader() as conn:
            self.assertEqual([], fx.due_deals(conn, "2026-11-02"))      # the due date: held (OPEN, no NOTIONAL_START)
            self.assertEqual([], fx.due_deals(conn, "2026-11-03"))      # and the day after: no next-day fallback
            self.assertEqual(2, len(fx.held_settle_ids(conn)))
        # SETTLE_DUE at 00:31 ET on 11-02 and again on 11-03 leaves both rows alone (the swap's START has not run)
        for at in ("2026-11-02T00:31:00-05:00", "2026-11-03T00:31:00-05:00"):
            rt.clock.set(at)
            rt.lifecycle.tick()
            self.assertEqual(["PENDING", "PENDING"], [x["status"] for x in q(rt, "SELECT status FROM settlements WHERE fx_deal_id=? ORDER BY leg", (deal_id,))])
        self.assertEqual((0, 0), (bal(rt, PB, "1010", "USD"), bal(rt, PB, "1010", "EUR")))
        # the owner's manual settle is refused the same way: nothing settles the conversion alone
        r = execute(rt, "fx.settle", {"deal_id": deal_id, "force": True})
        self.assertEqual((409, "LINKED_SETTLEMENT"), (r.http, r.code))
        # once the contract closes (MATURED / TERMINATED) the rows are trades again: returned like any other
        set_status(rt, CID, "TERMINATED")
        with rt.db.reader() as conn:
            self.assertEqual([deal_id], fx.due_deals(conn, "2026-11-03"))
            self.assertEqual(set(), fx.held_settle_ids(conn))

    def test_dt2_event_ran_releases_the_rows(self):
        rt = self.rt
        insert_xccy(rt)
        r = conversion(rt)
        self.assertTrue(r.ok, r.body)
        with rt.db.writer() as tx:                                      # the START's otc_resets row exists: the event ran
            tx.conn.execute("INSERT INTO otc_resets(contract_id, event_date, kind, amount_c, detail_json, entry_id) VALUES (?,?,'NOTIONAL_START',0,'{}',NULL)",
                            (CID, "2026-11-02"))
        with rt.db.reader() as conn:
            self.assertEqual([r.body["deal"]["deal_id"]], fx.due_deals(conn, "2026-11-02"))

    def test_dt3_settle_linked_receive_then_pay_inside_the_start(self):
        rt = self.rt
        insert_xccy(rt)
        r = conversion(rt)
        self.assertTrue(r.ok, r.body)
        deal_id = r.body["deal"]["deal_id"]
        t_before = bal(rt, "F1:TREASURY", "1010", "USD")
        rt.clock.set("2026-11-02T00:45:00-05:00")                        # the START key's time on S
        r = execute(rt, "test.start_pvp", {"contract_id": CID, "account_id": PB, "date": "2026-11-02"}, "job:test:start", actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        # (1) receive: Dr 1010 USD 2,231,560.00 / Cr 1250 USD -> the conversion's USD in first
        self.assertEqual([("USD", 223156000)], [(x["currency"], x["amount_c"]) for x in r.body["receive"]["settled"]])
        # (2) the USD lent is 2,231,600.00 against 2,231,560.00 settled: one Treasury sweep of 40.00 (the conversion spread)
        self.assertEqual(4000, r.body["funding"]["sweep_c"])
        self.assertEqual(t_before - 4000, bal(rt, "F1:TREASURY", "1010", "USD"))
        # (4) pay: Dr 2350 EUR 2,000,000.00 / Cr 1010 EUR from the EUR just borrowed
        self.assertEqual([("EUR", -200000000)], [(x["currency"], x["amount_c"]) for x in r.body["pay"]["settled"]])
        # end balances (FX_CCS_PLAN 4.4 stage 2): 1010 USD 0, 1010 EUR 0, 1250 / 2350 at 0, 1360 USD 2,231,600.00, 2360 EUR 2,000,000.00
        self.assertEqual((0, 0, 0, 0, 223160000, 200000000),
                         (bal(rt, PB, "1010", "USD"), bal(rt, PB, "1010", "EUR"), bal(rt, PB, "1250", "USD"), bal(rt, PB, "2350", "EUR"),
                          bal(rt, PB, "1360", "USD", CID), bal(rt, PB, "2360", "EUR", CID)))
        self.assertEqual("SETTLED", one(rt, "SELECT status FROM fx_deals WHERE deal_id=?", (deal_id,))["status"])

    def test_dt2_settle_due_waits_then_the_start_resolves_its_item(self):
        """Until SETTLE_DUE leaves the held rows out of its keys (the orchestrator's hook), it is refused on them with a WARN that names
        the start exchange; the START that settles the rows resolves that item."""
        rt = self.rt
        insert_xccy(rt)
        self.assertTrue(conversion(rt).ok)
        rt.clock.set("2026-11-02T00:31:00-05:00")
        rt.lifecycle.tick()
        open_items = lambda: [a["ref_id"] for a in q(rt, "SELECT ref_id FROM attention WHERE code='LINKED_SETTLEMENT' AND resolved_ts IS NULL")]   # noqa: E731
        held = sorted(f"SETTLE_DUE:{s['settle_id']}" for s in q(rt, "SELECT settle_id FROM settlements WHERE ref_type='xccy_start'"))
        self.assertTrue(set(open_items()) <= set(held))
        rt.clock.set("2026-11-02T00:45:00-05:00")
        r = execute(rt, "test.start_pvp", {"contract_id": CID, "account_id": PB, "date": "2026-11-02"}, "job:test:start-3", actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        self.assertEqual([], open_items())

    def test_dt3_strict_start_rolls_back_when_the_eur_is_not_there(self):
        rt = self.rt
        insert_xccy(rt)
        r = conversion(rt)
        self.assertTrue(r.ok, r.body)
        deal_id = r.body["deal"]["deal_id"]
        rt.clock.set("2026-11-02T00:45:00-05:00")
        # without the exchange the EUR payable has no EUR: strict (xccy_start) -> 409, and the receive phase is rolled back with it
        r = execute(rt, "test.start_pvp", {"contract_id": CID, "account_id": PB, "date": "2026-11-02", "skip_exchange": True}, "job:test:start-2", actor="lifecycle")
        self.assertEqual((409, "SETTLEMENT_UNFUNDED"), (r.http, r.code))
        self.assertEqual(["PENDING", "PENDING"], [x["status"] for x in q(rt, "SELECT status FROM settlements WHERE fx_deal_id=? ORDER BY leg", (deal_id,))])
        self.assertEqual((0, 0), (bal(rt, PB, "1010", "USD"), bal(rt, PB, "1250", "USD") - 223156000))
        self.assertIsNone(one(rt, "SELECT * FROM otc_resets WHERE contract_id=?", (CID,)))

    def test_settle_linked_non_strict_leaves_an_unfunded_leg_waiting(self):
        """An SFI purchase (xccy_event) whose USD leg cannot be funded: the leg waits SETTLEMENT_UNFUNDED with its ERROR (D9)."""
        rt = self.rt
        insert_xccy(rt)
        # empty F1 Treasury's USD into the US exchange account, so the PB account and Treasury hold no USD
        t_usd = bal(rt, "F1:TREASURY", "1010", "USD")
        r = execute(rt, "treasury.transfer", {"from_account": "F1:TREASURY", "to_account": "F1:US_EXCHANGE", "currency": "USD", "amount_c": t_usd})
        self.assertTrue(r.ok, r.body)
        # BUY EUR 12,945.83 / SELL USD at 1.1158 + 0.2 pip = 1.11582 -> USD 12,945.83 x 1.11582 = 12,945.83 + 12,945.83 x 0.11582
        # = 12,945.83 + 1,499.3860306 = 14,445.2160306 -> 14,445.22
        r = conversion(rt, buy_ccy="EUR", sell_ccy="USD", amount_c=1294583, amount_ccy="EUR", link={"type": "otc_funding", "contracts": [CID], "due": "2026-11-02"},
                       settle_ref=["xccy_event", CID], check_funding=False)
        self.assertTrue(r.ok, r.body)
        self.assertEqual((1294583, 1444522, "1.11582"), (r.body["deal"]["buy_c"], r.body["deal"]["sell_c"], r.body["deal"]["rate_quoted"]))
        self.assertFalse(r.body["funding"]["fundable"])                  # booked anyway: the USD leg will wait (D9)
        rt.clock.set("2026-11-02T00:45:00-05:00")

        def _h_pay(ctx, payload):
            return {"receive": fx.settle_linked(ctx, "xccy_event", CID, "2026-11-02", "receive"), "pay": fx.settle_linked(ctx, "xccy_event", CID, "2026-11-02", "pay")}
        rt.bus.register("test.pay_linked", _h_pay)
        r = execute(rt, "test.pay_linked", {}, "job:test:pay", actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        self.assertEqual([("EUR", 1294583)], [(x["currency"], x["amount_c"]) for x in r.body["receive"]["settled"]])
        self.assertEqual([("USD", 1444522)], [(x["currency"], x["amount_c"]) for x in r.body["pay"]["unfunded"]])
        self.assertEqual(1, one(rt, "SELECT COUNT(*) n FROM attention WHERE code='SETTLEMENT_UNFUNDED' AND resolved_ts IS NULL")["n"])


if __name__ == "__main__":
    unittest.main()
