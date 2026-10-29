"""FINSIM1_PLAN P2 Part A (release 1.0.5): Completed Trades' executions route (C7 / 15.3 E3) and the source labels (15.7 item 7) -
P2 acceptance 1 (a) and (d), acceptance 2 for the executions route; directive section 18 #7 (completed-trade history), gaps OMS-04 /
TEST-02 (the API part).  Every expected number is worked by hand in the comment next to it.

Part B (release 1.0.6, ``PendingRoute``): the Pending blotter (C7: every directive 3A field, the derived stages, the status filters,
paging, package legs grouped with their derived state), ``blotters.counts``, the order drawer with the pure review, the execution
drawer, the amend / replace routes and the refusals of every ticket (OMS-01, 05, 06, 08, 13, 14, 15, 17; directive section 18 #1-7).

- Money tests (a) and (d) run the live order path (WP-02 scaffolding of test_terminal_orders: the Test Book T1, FixedMarks, a dict
  quote cache for the Matcher).
- The route tests run the three real books built by ``server.build_runtime`` offline (FakeProvider, FakeClock, a temp home, no
  network): real fills (as-of bookings, a historical-model line, an expiry at 0) plus one row per FX deal, OTC contract event and
  financing event written as their modules write them (the route only reads, so the rows are inserted directly).
"""
from __future__ import annotations

import csv
import io
import json
import shutil
import tempfile
import unittest
from decimal import Decimal

from tests import test_terminal_orders as sc
from tests.terminal_helpers import US_EXCHANGE, fund
from tests.test_terminal_asof_listed import EXEC_TS, NOW, ORCL_C, ep, single, spec
from tests.test_terminal_display_units import build
from finsim.terminal import accounting, blotters, execution
from finsim.terminal.commands import Command

BUILD_AT = "2026-10-21T10:00:00-04:00"
D3B_FIELDS = ("execution_id", "parent_id", "instrument_id", "symbol", "asset_class", "side_words", "qty", "notional", "price", "price_label", "exec_ts",
              "exec_ny", "trade_date", "settle_date", "account_id", "account_name", "counterparty", "fee", "status", "status_label", "source", "source_label")


# ===== (a) and (d): the live order path ===========================================================================================================
class PendingIsNotCompleted(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-blot-")
        self.rt = None

    def tearDown(self):
        if self.rt is not None:
            self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_resting_limit_changes_no_position_and_no_nav_and_reserves_its_cost(self):
        # Tue 2026-10-20 14:00 ET (in session); AAPL last 339.50; US Exchange funded 1,000,000.00
        rt = self.rt = sc.runtime(self.tmp, at="2026-10-20T14:00:00-04:00")
        fund(rt, US_EXCHANGE, 100000000)
        now = rt.clock.epoch()
        rt.marks.set_quote(sc.rec("AAPL", "339.50", now - 60, now, bar=(now - 120, 61900)))
        nav0 = rt.accounting.nav("T1:ALL", snapshot=rt.marks.snapshot()).nav_c
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=100, order_type="LIMIT", limit_px="50.00", tif="DAY")
        self.assertTrue(r.ok, r.body)
        oid = r.body["order"]["order_id"]
        self.assertEqual(("WORKING", 0), (r.body["order"]["status"], r.body["order"]["filled_qty"]))   # 50.00 < 339.50: rests
        self.assertEqual(0, sc.one(rt, "SELECT COUNT(*) n FROM lots")["n"])                              # no position
        self.assertEqual(nav0, rt.accounting.nav("T1:ALL", snapshot=rt.marks.snapshot()).nav_c)           # no NAV change
        # reserved = 100 x 50.00 + the schedule fee (100 x 0.005 = 0.50 -> the 1.00 minimum) = 5,001.00
        with rt.db.reader() as conn:
            row = conn.execute("SELECT * FROM orders WHERE order_id=?", (oid,)).fetchone()
            self.assertEqual(500100, accounting.reserved_for_order(conn, row, rt.marks.snapshot(), rt.instruments))
            self.assertEqual(500100, accounting.reserved_c(conn, US_EXCHANGE, "USD", rt.marks.snapshot(), rt.instruments))
            # a pending order is not an execution: Completed Trades lists nothing
            self.assertEqual([], blotters.execution_rows(rt, conn, lifetime_ids=["T1"]))

    def test_d_one_order_two_executions_one_parent(self):
        # Tue 10:30 ET; AAPL last 10.00 with a 1-minute bar of 4,000 shares: 10 % participation caps the first look at 400
        rt = self.rt = sc.runtime(self.tmp, at="2026-10-20T10:30:00-04:00")
        fund(rt, US_EXCHANGE, 5000000)
        now = rt.clock.epoch()
        rt.marks.set_quote(sc.rec("AAPL", "10.00", now - 60, now, bar=(now - 120, 4000)))
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=1000, order_type="MARKET", tif="DAY")
        self.assertTrue(r.ok, r.body)
        oid = r.body["order"]["order_id"]
        self.assertEqual(("PARTIAL", 400), (r.body["order"]["status"], r.body["order"]["filled_qty"]))
        # the next bar: 6,000 shares at 10.10 -> the remaining 600 fill @ 10.10
        cache = sc.FakeCache()
        cache["AAPL"] = sc.rec("AAPL", "10.10", now, now, bar=(now - 60, 6000))
        self.assertEqual(1, sc.matcher_for(rt, cache).on_quotes(["AAPL"]))
        o = sc.order_row(rt, oid)
        # avg = (400 x 10.00 + 600 x 10.10) / 1,000 = (4,000.00 + 6,060.00) / 1,000 = 10.06; fees 400 x 0.005 = 2.00 and 600 x 0.005 = 3.00
        self.assertEqual(("FILLED", 1000, "10.06", 500), (o["status"], o["filled_qty"], o["avg_px"], o["fees_c"]))
        with rt.db.reader() as conn:
            rows = blotters.execution_rows(rt, conn, lifetime_ids=["T1"])
        self.assertEqual([(oid, "order", 400, "10", 400000, 200, "Settling", "Simulated fill"), (oid, "order", 600, "10.1", 606000, 300, "Settling", "Simulated fill")],
                         sorted((x["parent_id"], x["parent_kind"], x["qty"], x["price"], x["gross_c"], x["fee_c"], x["status_label"], x["source_label"]) for x in rows))
        self.assertEqual(2, len({x["execution_id"] for x in rows}))                    # two executions, never merged
        self.assertEqual([200, 300], sorted(x["fees_journal_c"] for x in rows))         # each fill's own 5000 line


# ===== the executions route over every source ==========================================================================================================
class ExecutionsRoute(unittest.TestCase):
    """One fixture: Fund book F1 with real fills and one row per other execution source."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="fst-blot-route-")
        rt = cls.rt = build(cls.tmp, at=BUILD_AT)
        rt.bus.register("test.expire_option", _h_expire_option)
        cls._ok("book.onboard_pb", {"lifetime_id": "F1", "broker": "GS"}, "test:route-onboard-gs", actor="ui")
        rt.clock.set(NOW)
        ok = lambda kind, payload, rid: cls._ok(kind, payload, rid)   # noqa: E731
        # real fills: an as-of option (as_of), a historical-model equity (historical_model), a second option expired worthless (system)
        ok("trade.book_as_of", single("imp:route-orcl"), "imp:route-orcl")
        hm = single("imp:route-hm", iid="AAPL", side="BUY", qty=100, price="300.00", exec_ts="2026-10-22T15:30:00-04:00", historical_model=True)
        hm["instrument_spec"] = None
        ok("trade.book_as_of", hm, "imp:route-hm")
        meta = single("imp:route-meta", iid="META261120C00660000", qty=2, price="23.59", inst_spec=spec("META261120C00660000", "META", "660", "C"))
        ok("trade.book_as_of", meta, "imp:route-meta")
        ok("test.expire_option", {"account_id": "F1:US_EXCHANGE", "instrument_id": "META261120C00660000", "qty": 2}, "test:route-expire-0001")
        _insert_other_sources(rt)

    @classmethod
    def tearDownClass(cls):
        cls.rt.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def _ok(cls, kind, payload, rid, actor="import"):
        r = cls.rt.bus.execute(Command(kind, payload, rid, actor))
        if not r.ok:
            raise AssertionError(f"{kind} {rid} -> {r.http} {r.body}")
        return r.body

    def get(self, query: str = ""):
        status, body, headers = self.rt.router.handle("GET", "/api/blotter/executions" + query, None, None, "127.0.0.1", None)
        self.assertEqual(200, status, body)
        return body, headers

    def test_the_union_lists_every_execution_once(self):
        body, _h = self.get("?book=F&limit=500")
        rows = body["rows"]
        ids = [r["execution_id"] for r in rows]
        self.assertEqual(len(ids), len(set(ids)))                                        # one row per execution
        kinds = {}
        for r in rows:
            kinds.setdefault(r["kind"], []).append(r["execution_id"])
        # 4 fills (ORCL BTO, AAPL BUY, META BTO, META expiry) + 1 FX deal + 3 OTC bookings + 2 OTC events + 11 financing trades (borrow
        # opened / returned, lent / recalled, repo opened / rolled / closed, cash loan drawn / repaid, private credit committed / repaid) = 21
        self.assertEqual({"fill": 4, "fx_deal": 1, "otc": 5, "financing": 11}, {k: len(v) for k, v in kinds.items()})
        self.assertEqual(21, body["total"])
        fin = {r["execution_id"]: r for r in rows if r["kind"] == "financing"}
        self.assertEqual({"B-T1:opened", "SL-T1:lent", "R-T1:opened", "L-T1:drawn", "PCL-T1:committed"},
                         {k for k in fin if k.count(":") == 1})
        self.assertEqual({"Financing"}, {r["asset_class"] for r in fin.values()})
        # an automatic PB financing draw and a private-credit coupon are not trades
        self.assertFalse([r for r in rows if "L-PB" in r["execution_id"]])
        self.assertEqual(1, len([r for r in fin.values() if r["execution_id"].startswith("PCL-T1:paid")]))
        # the borrow names its lender in trader words (C16) and closes after the full return
        b = fin["B-T1:opened"]
        self.assertEqual(("street lender (assumed terms)", "closed", "BORROW", 500, "0.30", "borrow fee %"),
                         (b["counterparty"], b["status"], b["side_words"], b["qty"], b["price"], b["price_label"]))
        ret = [r for r in rows if r["execution_id"].startswith("B-T1:returned")][0]
        self.assertEqual(("returned", "RETURN"), (ret["status"], ret["side_words"]))
        # every row carries the parent: the order id of a fill, else T-<command id> of the ticket that booked it (D5)
        for r in rows:
            self.assertTrue(r["parent_id"], r)
            if r["kind"] == "fill" and r["origin"] == "ORDER":
                self.assertTrue(r["parent_id"].startswith("O-"), r)
            else:
                self.assertTrue(r["parent_id"].startswith("T-"), r)

    def test_every_directive_field_is_present(self):
        body, _h = self.get("?book=F&limit=500")
        for r in body["rows"]:
            missing = [k for k in D3B_FIELDS if k not in r]
            self.assertEqual([], missing, r["execution_id"])
            self.assertTrue(r["qty"] is not None or r["notional"] is not None, r["execution_id"])
            for k in ("execution_id", "trade_date", "account_id", "counterparty", "status_label", "source_label"):
                self.assertTrue(r[k], f"{r['execution_id']}: {k}")
            # a settlement date (expected or actual) on every row that moves cash; an expiry at 0 has no cash leg
            self.assertTrue(r["settle_date"] or r["status"] in ("no_cash_leg", "daily_vm"), r["execution_id"])
        self.assertNotIn("undefined", json.dumps(body))
        self.assertNotIn("NaN", json.dumps(body))

    def test_statuses_and_sources(self):
        body, _h = self.get("?book=F&limit=500")
        by_id = {r["execution_id"]: r for r in body["rows"]}
        fills = {r["instrument_id"]: r for r in body["rows"] if r["kind"] == "fill" and r["origin"] == "ORDER"}
        exp = [r for r in body["rows"] if r["kind"] == "fill" and r["origin"] == "EXPIRY"][0]
        self.assertEqual(("settling", "as_of", "AS-OF"), (fills[ORCL_C]["status"], fills[ORCL_C]["source"], fills[ORCL_C]["badge"]))
        self.assertEqual(("historical_model", "Historical model (not an independent execution)", "MODEL"),
                         (fills["AAPL"]["source"], fills["AAPL"]["source_label"], fills["AAPL"]["badge"]))
        self.assertEqual(("no_cash_leg", "no cash leg", "system", "System (expiry)", "STC"), (exp["status"], exp["status_label"], exp["source"], exp["source_label"],
                                                                                             exp["side_words"]))
        self.assertEqual(("settled", "BUY EUR / SELL USD", "1.1238", "100000.00"), (by_id["FX-T1"]["status"], by_id["FX-T1"]["side_words"], by_id["FX-T1"]["price"],
                                                                                   by_id["FX-T1"]["notional"]))
        irs = by_id["SWP-T1"]
        self.assertEqual(("otc_margin", "IM posted, CTM daily VM", "PAY FIXED", "4.72", "Goldman Sachs", "7500000.00", "OTC"),
                         (irs["status"], irs["status_label"], irs["side_words"], irs["price"], irs["counterparty"], irs["notional"], irs["asset_class"]))
        self.assertEqual(("closed", "terminated", "simulated"), (by_id["SWP-T2"]["status"], by_id["SWP-T2:terminated"]["status"], by_id["SWP-T2:terminated"]["source"]))
        self.assertEqual(("matured", "System (maturity)"), (by_id["CDX-T3:matured"]["status"], by_id["CDX-T3:matured"]["source_label"]))
        self.assertEqual("BUY PROTECTION", by_id["CDX-T3"]["side_words"])
        self.assertEqual(("rolled", "System (roll)"), tuple(r[k] for r in body["rows"] if r["execution_id"].startswith("R-T1:rolled") for k in ("status", "source_label")))
        counts = body["counts"]
        self.assertEqual(21, sum(counts["status"].values()))
        self.assertEqual({"as_of": 2, "historical_model": 1}, {k: counts["source"][k] for k in ("as_of", "historical_model")})

    def test_filters(self):
        total = self.get("?book=F")[0]["total"]
        # date range on the trade date
        self.assertEqual(3, self.get("?book=F&from=2026-10-22&to=2026-10-22")[0]["total"])     # the 3 as-of fills (the expiry is dated 10-27)
        # instrument: id, symbol or an option's underlying
        self.assertEqual(1, self.get(f"?book=F&instrument={ORCL_C}")[0]["total"])
        self.assertEqual(3, self.get("?book=F&instrument=orcl")[0]["total"])                  # the ORCL call + the ORCL TRS and its termination
        self.assertEqual(2, self.get("?book=F&instrument=META")[0]["total"])                  # the META call's open and expiry
        # account: 4 fills + the borrow opened / returned + the securities lent / recalled = 8
        self.assertEqual(8, self.get("?book=F&account=F1:US_EXCHANGE")[0]["total"])
        # asset class, with an exclusion
        self.assertEqual(11, self.get("?book=F&asset_class=Financing")[0]["total"])
        self.assertEqual(total - 11, self.get("?book=F&asset_class=!Financing")[0]["total"])
        self.assertEqual(3, self.get("?book=F&asset_class=Option,ETF")[0]["total"])
        # counterparty (contains), status, source and batch
        self.assertEqual(5, self.get("?book=F&counterparty=goldman")[0]["total"])            # the 3 OTC bookings + 2 OTC events
        self.assertEqual(4, self.get("?book=F&status=settling,no_cash_leg")[0]["total"])     # ORCL, AAPL, META settling + the expiry
        self.assertEqual(2, self.get("?book=F&source=asof")[0]["total"])
        self.assertEqual(3, self.get("?book=F&batch=IMP-TEST-P2A")[0]["total"])
        self.assertEqual(["IMP-TEST-P2A"], self.get("?book=F")[0]["batches"])

    def test_paging_and_the_csv(self):
        everything = self.get("?book=F&limit=500")[0]["rows"]
        first, _h = self.get("?book=F&limit=10")
        self.assertEqual((21, 0, 10, 10), (first["total"], first["offset"], first["limit"], first["next_offset"]))
        second = self.get("?book=F&limit=10&offset=10")[0]
        third = self.get("?book=F&limit=10&offset=20")[0]
        self.assertIsNone(third["next_offset"])
        paged = [r["execution_id"] for r in first["rows"] + second["rows"] + third["rows"]]
        self.assertEqual([r["execution_id"] for r in everything], paged)                       # newest first, no row twice
        st, text, headers = self.rt.router.handle("GET", "/api/blotter/executions?book=F&format=csv", None, None, "127.0.0.1", None)
        self.assertEqual(200, st)
        self.assertTrue(headers["Content-Type"].startswith("text/csv"))
        text = text.decode("utf-8") if isinstance(text, (bytes, bytearray)) else text
        table = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual([h for h, _k, _how in blotters.CSV_COLUMNS], list(table[0].keys()))
        self.assertEqual([r["execution_id"] for r in everything], [r["execution_id"] for r in table])   # the CSV equals the rows
        for row, js in zip(table, everything):
            self.assertEqual((js["parent_id"], js["side_words"], js["status_label"], js["source_label"], js["trade_date"], js["batch_id"] or ""),
                             (row["parent"], row["side"], row["status"], row["source"], row["trade_date"], row["batch"]))
            self.assertEqual(js["fee"] or "", row["fees"])
            self.assertEqual(js.get("exec_ny") or "", row["execution_time"])
        csv_fin = self.rt.router.handle("GET", "/api/blotter/executions?book=F&format=csv&asset_class=!Financing", None, None, "127.0.0.1", None)[1]
        csv_fin = csv_fin.decode("utf-8") if isinstance(csv_fin, (bytes, bytearray)) else csv_fin
        self.assertEqual(21 - 11, len(list(csv.DictReader(io.StringIO(csv_fin)))))

    def test_scopes(self):
        self.assertEqual(21, self.get("?scope=F1:ALL")[0]["total"])
        self.assertEqual(21, self.get("?scope=ALL_BOOKS")[0]["total"])                         # the other books have no executions
        self.assertEqual(0, self.get("?book=P")[0]["total"])
        acct = self.get("?scope=F1:PB_GS")[0]
        self.assertEqual(("F1:PB_GS", 5), (acct["scope"], acct["total"]))
        st, body, _h = self.rt.router.handle("GET", "/api/blotter/executions?account=F1:NOPE", None, None, "127.0.0.1", None)
        self.assertEqual(404, st)


# ===== phase 2 (1.0.6): the Pending blotter, counts, the order and execution drawers, amend / replace routes, every ticket's rejections =========
D3A_FIELDS = ("order_id", "instrument_id", "symbol", "asset_class", "side_words", "order_type", "type_words", "limit_px", "stop_px", "qty", "filled_qty",
              "open_qty", "avg_px", "market", "distance", "submitted_ts", "expiration_words", "account_id", "account_name", "book", "counterparty", "reserved",
              "reason", "stage", "stage_label", "stage_chip", "actions")


class PendingRoute(unittest.TestCase):
    """GET /api/blotter/pending and the drawers on a real ``build_runtime`` (Test Book T1, offline, FakeProvider; Tue 2026-10-20 14:00 ET,
    AAPL last 339.50 two minutes old on a 61,900-share bar): one order per stage, a package whose legs ended (the PK-000001 shape) and one
    refusal from each ticket."""

    @classmethod
    def setUpClass(cls):
        from tests.test_terminal_api import build as api_build, quote as api_quote
        cls.tmp = tempfile.mkdtemp(prefix="fst-pend-")
        rt = cls.rt = api_build(cls.tmp)
        api_quote(rt, "AAPL", "339.50")
        ok = cls.ok
        ok("POST", "/api/transfers", {"request_id": "ui:pend-fund-0001", "from_account": "T1:TREASURY", "to_account": "T1:US_EXCHANGE", "amount": "1000000.00",
                                      "currency": "USD"})
        order = lambda rid, **kw: ok("POST", "/api/orders", dict({"request_id": rid, "account_id": "T1:US_EXCHANGE", "instrument_id": "AAPL", "side": "BUY"}, **kw))   # noqa: E731
        cls.o_rest = order("ui:pend-o-0001", qty=100, order_type="LIMIT", limit_px="50.00", tif="DAY")["order"]["order_id"]
        cls.o_fill = order("ui:pend-o-0002", qty=10, order_type="MARKET", tif="DAY")["order"]["order_id"]
        cls.o_canc = order("ui:pend-o-0003", qty=10, order_type="LIMIT", limit_px="40.00", tif="DAY")["order"]["order_id"]
        ok("DELETE", f"/api/orders/{cls.o_canc}", {"request_id": "ui:pend-c-0003"})
        cls.o_next = order("ui:pend-o-0004", qty=10, order_type="LIMIT", limit_px="45.00", tif="NEXT_SESSION")["order"]["order_id"]
        cls.o_exp = order("ui:pend-o-0005", qty=10, order_type="LIMIT", limit_px="44.00", tif="DAY")["order"]["order_id"]
        r = rt.bus.execute(Command("order.expire", {"order_id": cls.o_exp, "force": True}, "lc:pend-exp-0005", "lifecycle"))
        assert r.ok, r.body
        cls.o_held = order("ui:pend-o-0006", qty=40000, order_type="LIMIT", limit_px="339.00", tif="DAY", confirm_large=True)["order"]["order_id"]
        # one refusal from each ticket: the order ticket, the bond ticket, FX spot and forward, the OTC booking, the private trade
        bond = sorted(i.instrument_id for i in rt.instruments.all() if i.kind == "BOND")[0]
        for path, body in (("/api/orders", {"account_id": "T1:US_EXCHANGE", "instrument_id": "AAPL", "side": "BUY", "qty": 0, "order_type": "MARKET"}),
                           ("/api/bonds/trade", {"account_id": "T1:DEBT", "instrument_id": bond, "side": "BUY", "qty": 0}),
                           ("/api/fx/spot", {"account_id": "T1:FOREIGN_FX", "buy_ccy": "EUR", "sell_ccy": "USD", "amount": "-5", "amount_ccy": "EUR"}),
                           ("/api/fx/forward", {"account_id": "T1:FOREIGN_FX", "buy_ccy": "EUR", "sell_ccy": "USD", "amount": "-5", "amount_ccy": "EUR", "tenor": "1M"}),
                           ("/api/otc/contracts", {"account_id": "T1:US_EXCHANGE", "kind": "TRS", "terms": {"underlying_id": "AAPL", "units": 100, "receiver": True}}),
                           ("/api/private/trades", {"account_id": "T1:US_EXCHANGE", "instrument_id": "SPACEX", "side": "BUY", "qty": 10, "price": "1.00"})):
            st, b, _ = rt.router.handle("POST", path, None, dict(body, request_id="ui:pend-rej-" + path.strip("/").replace("/", "-")), "127.0.0.1", None)
            assert 400 <= st < 500, (path, st, b)
        _insert_ended_package(rt, cls.o_fill)

    @classmethod
    def tearDownClass(cls):
        cls.rt.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def ok(cls, method, path, body=None):
        st, out, _h = cls.rt.router.handle(method, path, None, body, "127.0.0.1", None)
        if st != 200:
            raise AssertionError(f"{method} {path} -> {st} {out}")
        return out

    def pending(self, query=""):
        return self.ok("GET", "/api/blotter/pending?book=T" + query)

    def test_open_lists_the_live_orders_with_their_stages(self):
        body = self.pending()
        by = {r["order_id"]: r for r in body["rows"]}
        self.assertEqual({self.o_rest, self.o_next, self.o_held}, set(by))                       # filled / cancelled / expired / the ended package: not open
        self.assertEqual(("WORKING", "working", "working"), (by[self.o_rest]["stage"], by[self.o_rest]["stage_label"], by[self.o_rest]["stage_chip"]))
        self.assertEqual(("QUEUED", "queued for Wed 10-21 09:30 ET"), (by[self.o_next]["stage"], by[self.o_next]["stage_label"]))
        self.assertEqual("HELD", by[self.o_held]["stage"])
        # 40,000 x 339.50 (an executable quote prices a marketable limit at the observed last) + fee 40,000 x 0.005 = 200.00 = 13,580,200.00
        self.assertTrue(by[self.o_held]["stage_label"].startswith("waiting for funds: needs USD 13,580,200.00"), by[self.o_held]["stage_label"])
        self.assertEqual({"working": 1, "partial": 0, "held": 1, "queued": 1, "waiting_parent": 0, "rfq_open": 0, "rejected_today": 6, "settling": 1, "failed": 0},
                         body["counts"])

    def test_every_directive_field_is_present(self):
        rows = self.pending("&status=all")["rows"]
        for r in rows:
            self.assertEqual([], [k for k in D3A_FIELDS if k not in r], r["order_id"])
        r = {x["order_id"]: x for x in rows}[self.o_rest]
        # LIMIT BUY 100 @ 50.00: reserves 100 x 50.00 + fee max(0.50, 1.00) = 5,001.00; the market from the cache (no fetch) and the
        # distance (50.00 - 339.50) / 339.50 = -85.27 %
        self.assertEqual(("LMT 50.00", "5001.00", 100, 0, 100, "DAY, expires 10-20 16:00 ET", "exchange (simulated), NSCC clearing", "BUY", "Equity"),
                         (r["type_words"], r["reserved"], r["qty"], r["filled_qty"], r["open_qty"], r["expiration_words"], r["counterparty"], r["side_words"],
                          r["asset_class"]))
        self.assertEqual(("339.5", "delayed", 120), (r["market"]["last"], r["market"]["quality"], r["market"]["age_s"]))     # the cached record as stored
        self.assertEqual(("-85.27", "limit 50.00 is 85.27 % below the observed last 339.50"), (r["distance"]["pct"], r["distance"]["words"]))
        self.assertEqual({"cancel": True, "resume": False, "modify": True, "replace": True, "review": True}, r["actions"])
        self.assertNotIn("undefined", json.dumps(rows))

    def test_status_filters(self):
        filled = self.pending("&status=filled")["rows"]
        self.assertEqual([(self.o_fill, "FILLED", "10 filled")], [(r["order_id"], r["stage"], r["stage_label"]) for r in filled])
        canc = {r["order_id"]: r for r in self.pending("&status=cancelled")["rows"]}
        self.assertEqual("cancelled: cancelled by user", canc[self.o_canc]["stage_label"])
        self.assertIn("O-PKT2", canc)                                                           # the package's cancelled leg
        exp = self.pending("&status=expired")["rows"]
        self.assertEqual([(self.o_exp, "EXPIRED", "expired: DAY order: session closed")], [(r["order_id"], r["stage"], r["stage_label"]) for r in exp])
        ended = self.pending("&status=ended")["rows"]
        self.assertLessEqual({self.o_fill, self.o_canc, self.o_exp, "O-PKT2"}, {r["order_id"] for r in ended})
        self.assertEqual({"FILLED", "CANCELLED", "EXPIRED"}, {r["status"] for r in ended})
        every = self.pending("&status=all")
        with self.rt.db.reader() as conn:                                                         # every order and every refusal of the book, once
            n = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] + conn.execute("SELECT COUNT(*) FROM order_rejections WHERE account_id LIKE 'T1:%'").fetchone()[0]
        self.assertEqual(n, every["total"])
        self.assertEqual(len({r["order_id"] for r in every["rows"]}), len(every["rows"]))
        st, body, _ = self.rt.router.handle("GET", "/api/blotter/pending?book=T&status=nope", None, None, "127.0.0.1", None)
        self.assertEqual(400, st)

    def test_rejections_from_every_ticket(self):
        rows = self.pending("&status=rejected")["rows"]
        self.assertEqual({"order ticket", "bond ticket", "FX spot", "FX forward", "OTC booking", "private trade"}, {r["ticket"] for r in rows})
        for r in rows:
            self.assertEqual(("REJECTED", "rejected"), (r["stage"], r["stage_words"]))
            self.assertTrue(r["reason"], r)
        by = {r["ticket"]: r for r in rows}
        self.assertEqual(("FX", "EURUSD", "BUY EUR / SELL USD"), (by["FX spot"]["asset_class"], by["FX spot"]["instrument_id"], by["FX spot"]["side_words"]))
        self.assertEqual(("OTC", "RECEIVE TOTAL RETURN"), (by["OTC booking"]["asset_class"], by["OTC booking"]["side_words"]))
        self.assertEqual("Bond", by["bond ticket"]["asset_class"])
        self.assertEqual("Private", by["private trade"]["asset_class"])

    def test_package_legs_are_grouped_and_their_state_derived(self):
        rows = self.pending("&status=all")["rows"]
        legs = [i for i, r in enumerate(rows) if r["package_id"] == "PK-T1"]
        self.assertEqual(2, len(legs))
        self.assertEqual(1, legs[1] - legs[0])                                                    # one row per leg, next to each other
        for i in legs:
            self.assertEqual(("ENDED", "ended: 1 filled, 1 cancelled", "BEAR_PUT_SPREAD"),
                             (rows[i]["package_state"], rows[i]["package_words"], rows[i]["strategy_key"]))
        with self.rt.db.reader() as conn:                                                         # derived only: the stored status is untouched
            self.assertEqual("WORKING", conn.execute("SELECT status FROM hedge_packages WHERE package_id='PK-T1'").fetchone()[0])

    def test_paging_and_filters(self):
        every = self.pending("&status=all&limit=500")["rows"]
        p1 = self.pending("&status=all&limit=5")
        p2 = self.pending("&status=all&limit=5&offset=5")
        p3 = self.pending("&status=all&limit=5&offset=10")
        self.assertEqual((5, 10, None), (p1["next_offset"], p2["next_offset"], p3["next_offset"]))
        self.assertEqual([r["order_id"] for r in every], [r["order_id"] for r in p1["rows"] + p2["rows"] + p3["rows"]])
        self.assertEqual({"Equity"}, {r["asset_class"] for r in self.pending("&status=all&asset_class=Equity,Option")["rows"]})
        self.assertEqual(["FX", "FX"], [r["asset_class"] for r in self.pending("&status=all&asset_class=FX")["rows"]])
        self.assertTrue(all(r["account_id"] == "T1:FOREIGN_FX" for r in self.pending("&status=all&account=T1:FOREIGN_FX")["rows"]))
        self.assertEqual({self.o_rest, self.o_next, self.o_held}, {r["order_id"] for r in self.pending("&instrument=aapl")["rows"]})

    def test_open_quote_requests_from_the_otc_desk(self):
        """C14 as P2 consumes it: ``swaps.rfq_rows(services, scope)`` rows (P4) - REQUESTED -> AWAITING DEALER QUOTE, QUOTED and valid ->
        AWAITING YOUR ACCEPTANCE; an expired quote, a final request and another book's request are not listed."""
        import types as _types
        rt = self.rt
        now = rt.clock.epoch()
        seen = []

        def rfq_rows(services, scope):
            seen.append(scope)
            return [{"rfq_id": "RFQ-1", "account_id": "T1:US_EXCHANGE", "kind": "TRS", "status": "REQUESTED", "requested_ts": now - 30,
                     "terms": {"underlying_id": "ORCL", "units": 100, "receiver": False}},
                    {"rfq_id": "RFQ-2", "account_id": "T1:US_EXCHANGE", "kind": "IRS", "status": "QUOTED", "requested_ts": now - 60, "expires_ts": now + 90,
                     "terms": {"pay_fixed": True, "fixed_rate": "0.0472", "tenor_months": 36}, "notional_c": 750000000, "quote_label": "4.7200 %"},
                    {"rfq_id": "RFQ-3", "account_id": "T1:US_EXCHANGE", "kind": "IRS", "status": "QUOTED", "requested_ts": now - 300, "expires_ts": now - 10, "terms": {}},
                    {"rfq_id": "RFQ-4", "account_id": "T1:US_EXCHANGE", "kind": "TRS", "status": "ACCEPTED", "requested_ts": now - 400, "terms": {}},
                    {"rfq_id": "RFQ-5", "account_id": "F1:PB_GS", "kind": "TRS", "status": "REQUESTED", "requested_ts": now - 20, "terms": {}}]
        saved = getattr(rt, "swaps", None)
        rt.swaps = _types.SimpleNamespace(rfq_rows=rfq_rows)
        try:
            rows = {r["order_id"]: r for r in self.pending()["rows"] if r["row_kind"] == "rfq"}
            self.assertEqual({"RFQ-1", "RFQ-2"}, set(rows))
            self.assertEqual(("AWAITING_DEALER_QUOTE", "awaiting the dealer's quote", "OTC", "PAY TOTAL RETURN", 100),
                             (rows["RFQ-1"]["stage"], rows["RFQ-1"]["stage_label"], rows["RFQ-1"]["asset_class"], rows["RFQ-1"]["side_words"], rows["RFQ-1"]["qty"]))
            self.assertEqual("AWAITING_ACCEPTANCE", rows["RFQ-2"]["stage"])
            self.assertTrue(rows["RFQ-2"]["stage_label"].startswith("awaiting your acceptance: 4.7200 %, valid until"), rows["RFQ-2"]["stage_label"])
            self.assertEqual(("PAY FIXED", "7500000.00", True), (rows["RFQ-2"]["side_words"], rows["RFQ-2"]["notional"], rows["RFQ-2"]["actions"]["accept"]))
            self.assertEqual(2, blotters.counts(rt, "T1:ALL")["rfq_open"])
            self.assertIn("T1:ALL", seen)
        finally:
            rt.swaps = saved
        self.assertEqual(0, blotters.counts(rt, "T1:ALL")["rfq_open"])

    def test_counts(self):
        c = self.ok("GET", "/api/blotter/counts?book=T")
        self.assertEqual(c["counts"], blotters.counts(self.rt, "T1:ALL"))
        self.assertEqual(c["counts"], blotters.counts(self.rt, "T"))
        self.assertEqual(0, blotters.counts(self.rt, "T1:DEBT")["working"])

    def test_the_order_drawer_and_the_review(self):
        rt = self.rt
        with rt.db.reader() as conn:
            n0 = conn.execute("SELECT COUNT(*) FROM commands").fetchone()[0]
        d = self.ok("GET", f"/api/orders/{self.o_rest}?review=1")
        with rt.db.reader() as conn:
            self.assertEqual(n0, conn.execute("SELECT COUNT(*) FROM commands").fetchone()[0])     # a review writes nothing
        self.assertEqual(["CREATED", "SUBMITTED", "WORKING"], [s["stage"] for s in d["timeline"]])
        # previewed at the executable last 339.50; fee 1.00; cash need 100 x 339.50 + 1.00 = 33,951.00 (the preview's worst case)
        self.assertEqual("previewed: expected 339.50 (last); fee 1.00; cash need 33,951.00; settles 2026-10-21", d["timeline"][0]["note"])
        self.assertEqual("would not fill now: the limit is not marketable at the observed price (limit 50.00 is 85.27 % below the observed last 339.50)",
                         d["review"]["text"])
        self.assertEqual(("5001.00", "WORKING", "LMT 50.00"), (d["reserved"], d["stage"], d["type_words"]))
        f = self.ok("GET", f"/api/orders/{self.o_fill}")
        self.assertEqual(["CREATED", "SUBMITTED", "FILLED"], [s["stage"] for s in f["timeline"]])
        self.assertEqual([("SETTLING", "pending settlement, due 2026-10-21")], [(s["stage"], s["label"]) for s in f["settlements"]])
        self.assertEqual(404, self.rt.router.handle("GET", "/api/orders/O-999999", None, None, "127.0.0.1", None)[0])

    def test_the_execution_drawer(self):
        f = self.ok("GET", f"/api/orders/{self.o_fill}")
        fid = f["fills"][0]["fill_id"]
        x = self.ok("GET", f"/api/executions/{fid}")
        e = x["execution"]
        self.assertEqual((fid, self.o_fill, "order", "Settling", "Simulated fill"), (e["execution_id"], e["parent_id"], e["parent_kind"], e["status_label"], e["source_label"]))
        self.assertEqual("order", x["parent"]["kind"])
        self.assertEqual(["CREATED", "SUBMITTED", "FILLED"], [s["stage"] for s in x["parent"]["order"]["timeline"]])
        self.assertIn(f"fill:{fid}", [en["idem_key"] for en in x["entries"]])
        self.assertEqual(("order.submit", "ui"), (x["audit"]["command"]["kind"], x["audit"]["command"]["actor"]))
        self.assertEqual((True, 10), (x["position"]["open"], x["position"]["qty"]))
        self.assertEqual(1, len(x["lots_opened"]))
        self.assertEqual(([], "0.00"), (x["lots_relieved"], x["realised"]))
        self.assertTrue(x["settlements"][0]["can_fail"])
        self.assertEqual(404, self.rt.router.handle("GET", "/api/executions/F-999999", None, None, "127.0.0.1", None)[0])

    def test_amend_and_replace_routes_and_the_probes(self):
        rt = self.rt
        o = self.ok("POST", "/api/orders", {"request_id": "ui:pend-ar-0001", "account_id": "T1:US_EXCHANGE", "instrument_id": "AAPL", "side": "BUY", "qty": 10,
                                            "order_type": "LIMIT", "limit_px": "30.00", "tif": "DAY"})["order"]["order_id"]
        a = self.ok("PATCH", f"/api/orders/{o}", {"request_id": "ui:pend-ar-0002", "limit_px": "31.00"})
        self.assertEqual(("31", "AMENDED limit 30.00 -> 31.00"), (a["order"]["limit_px"], a["event"]))
        r = self.ok("POST", f"/api/orders/{o}/replace", {"request_id": "ui:pend-ar-0003", "limit_px": "32.00", "qty": 20})
        self.assertEqual((o, f"replaced by {r['order']['order_id']}", 20), (r["order"]["replaces_order_id"], r["replaced"]["reason"], r["order"]["qty"]))
        self.ok("DELETE", f"/api/orders/{r['order']['order_id']}", {"request_id": "ui:pend-ar-0004"})
        self.assertEqual(404, rt.router.handle("PATCH", "/api/orders/O-999999", None, {"request_id": "ui:pend-ar-0005", "limit_px": "1.00"}, "127.0.0.1", None)[0])
        self.assertEqual(400, rt.router.handle("PATCH", f"/api/orders/{o}", None, {"limit_px": "1.00"}, "127.0.0.1", None)[0])     # no request id
        items = {i["item"] for i in self.ok("GET", "/api/unsupported")["items"]}
        self.assertNotIn("Good-till-date and next-session orders", items)                         # orders.gtd_supported ships with GTD (E1)
        self.assertNotIn("Simulated counterparty settlement fails", items)                        # blotters.execution_detail ships with the drawer
        conv = self.ok("GET", "/api/state")["conventions"]
        self.assertIn("ORDER_STAGES", conv)
        self.assertIn("EXECUTION_STAGES", conv)


class BlotterPagesStatic(unittest.TestCase):
    """UI-07 / UI-08 and the account Orders page (P2 tasks 2, 3, 11): the pages read the blotter routes, gate the optional modules'
    routes on T.hasModule (rule 5), the ticket offers GTD / next session with the extended-hours line, and every file is ASCII."""

    @staticmethod
    def read(*rel):
        import os
        from finsim.terminal import config
        with open(os.path.join(config.package_dir(), "static", *rel), "r", encoding="utf-8") as fh:
            return fh.read()

    def test_pages_and_components(self):
        pend, comp, ords = self.read("pages", "pending.js"), self.read("pages", "completed.js"), self.read("pages", "orders.js")
        tick, prev = self.read("components", "ticket.js"), self.read("components", "preview.js")
        for txt in (pend, comp, ords, tick, prev, self.read("components", "grid.js")):
            txt.encode("ascii")
        self.assertIn("T.pages.pending = {", pend)
        self.assertIn("'/api/blotter/pending'", pend)
        self.assertIn("T.orderDrawer = orderDrawer", pend)
        self.assertIn("T.pages.completed = {", comp)
        self.assertIn("'/api/blotter/executions'", comp)
        self.assertIn("'/api/executions/'", comp)
        # optional modules' routes only when bound (C2): the import batches (C19) and the settlement fail / resolve (C15)
        self.assertIn("T.hasModule('importer')", comp)
        self.assertIn("no import batches (the import tool is not in this build)", comp)
        self.assertIn("T.hasModule('collateral')", comp)
        self.assertIn("status: 'ended'", ords)
        self.assertIn("record: 'fill'", ords)
        self.assertIn("load more", ords)
        self.assertIn("T.h('option', { value: 'GTD' }, 'GTD (date)')", tick)
        self.assertIn("T.h('option', { value: 'NEXT_SESSION' }, 'Next session')", tick)
        self.assertIn("Extended hours: not simulated - an order entered outside the regular session rests until the regular open", tick)
        self.assertIn("'/replace'", tick)
        self.assertIn("T.api.patch('/api/orders/'", tick)
        self.assertIn("pv.tif_label", prev)
        self.assertNotIn("(s.group || '') + ' . '", prev)                  # V104R2-BW-02 (d): no internal group code on the session chip
        # P8R1-04 (rule 5): both blotters stack related fields and fold their secondary columns while the box is narrow (the ticket dock
        # open); Copy and CSV still export one column per field
        grid, css = self.read("components", "grid.js"), self.read("terminal.css")
        for page in (pend, comp):
            self.assertIn("csvColumns: CSV_COLS, narrowBelow: NARROW_BELOW", page)
            self.assertIn("wide-only", page)
            self.assertIn("narrow-only", page)
        self.assertIn("T.csv(spec.csv || 'grid.csv', spec.csvColumns || cols, visible())", grid)
        self.assertIn("root.classList.toggle('grid-narrow', w > 0 && w < spec.narrowBelow)", grid)
        self.assertIn(".grid.grid-narrow th.wide-only, .grid.grid-narrow td.wide-only { display: none; }", css)
        self.assertIn(".grid:not(.grid-narrow) .narrow-only { display: none; }", css)


def _insert_ended_package(rt, primary: str) -> None:
    """The PK-000001 shape (as the 1.0.3 order book left it): a package still stored WORKING whose primary FILLED (the real filled order
    ``primary``) and whose only leg was CANCELLED - the blotter derives "ended" and never lists it as open (it writes nothing)."""
    import json as _json
    with rt.db.writer() as tx:
        c = tx.conn
        ts = rt.clock.epoch()
        cid = int(c.execute("SELECT MIN(id) FROM commands").fetchone()[0])
        c.execute("INSERT INTO hedge_packages(package_id, command_id, account_id, primary_order_id, candidate_id, strategy_key, legs_json, fill_mode, status, created_ts)"
                  " VALUES ('PK-T1', ?, 'T1:US_EXCHANGE', ?, 'BEAR_PUT_SPREAD', 'BEAR_PUT_SPREAD', '[]', 'SEQUENTIAL', 'WORKING', ?)", (cid, primary, ts))
        c.execute("UPDATE orders SET package_id='PK-T1' WHERE order_id=?", (primary,))
        c.execute("INSERT INTO orders(order_id, request_id, account_id, instrument_id, side, qty, filled_qty, avg_px, order_type, limit_px, fill_mode, tif, status,"
                  " session_group, created_ts, updated_ts, expires_ts, package_id, parent_order_id, reason, preview_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  ("O-PKT2", "test:O-PKT2", "T1:US_EXCHANGE", "SPY", "SELL", 5, 0, None, "MARKET", None, "SEQUENTIAL", "DAY", "CANCELLED", "US_EQUITY",
                   ts, ts, ts + 7200, "PK-T1", primary, "owner: not intended", _json.dumps({"as_of": ts})))


# ---- fixture writers ------------------------------------------------------------------------------------------------------------------------------
def _h_expire_option(ctx, p: dict) -> dict:
    """Test-only command: the long call expires worthless (origin EXPIRY at 0, as the OPTION_EXPIRY job books it)."""
    inst = ctx.services.instruments.get(p["instrument_id"], ctx.conn)
    fr = execution.apply_system_fill(ctx, p["account_id"], inst, "STC", int(p["qty"]), Decimal(0), "EXPIRY", None, f"test-expiry:{p['instrument_id']}",
                                     "expired out of the money", services=ctx.services)
    return {"fill_id": fr.fill_id}


def _insert_other_sources(rt) -> None:
    """One row per other execution source, written exactly as fx.py / swaps*.py / lending*.py / repo.py / credit.py / private_credit.py
    write them (tables and events); the route reads them only."""
    ts = ep("2026-10-23T11:00")
    with rt.db.writer() as tx:
        c = tx.conn
        cid = int(c.execute("SELECT MIN(id) FROM commands").fetchone()[0])
        lid_cmd = c.execute("INSERT INTO commands(request_id, kind, actor, payload_json, payload_sha256, result_json, http_status, started_ts, finished_ts,"
                            " duration_ms, app_version, state_version) VALUES ('test:route-lifecycle','lifecycle.tick','lifecycle','{}','x','{}',200,?,?,0,'t',0)",
                            (ts, ts)).lastrowid

        def ev(t, account, ref_type, ref_id, payload, at=ts, instrument=None, command=cid):
            c.execute("INSERT INTO events(command_id, ts, type, account_id, instrument_id, ref_type, ref_id, payload_json) VALUES (?,?,?,?,?,?,?,?)",
                      (command, at, t, account, instrument, ref_type, ref_id, json.dumps(payload)))
        # FX: a settled EUR spot deal (buy EUR 100,000.00 for USD 112,380.00 at 1.1238)
        c.execute("INSERT INTO fx_deals(deal_id, command_id, account_id, kind, buy_ccy, sell_ccy, buy_c, sell_c, rate, mid, spread_cost_c, quote_json, trade_ts,"
                  " trade_date, value_date, status, settled_ts, entry_id) VALUES ('FX-T1',?,'F1:FOREIGN_FX','SPOT','EUR','USD',10000000,11238000,'1.1238','1.1235',"
                  "3000,'{}',?,'2026-10-23','2026-10-27','SETTLED',?,1)", (cid, ts, ts + 4 * 86400))
        # OTC: an open IRS, a terminated TRS and a matured CDX, with Goldman
        for row in (("SWP-T1", "IRS", '{"pay_fixed": true, "fixed_rate": "0.0472", "tenor_months": 36}', 750000000, "CTM", 10366800, "OPEN"),
                    ("SWP-T2", "TRS", '{"underlying_id": "ORCL", "units": 100, "receiver": false, "reset_px": "129.13"}', 1291300, "CTM", 129130, "TERMINATED"),
                    ("CDX-T3", "CDX", '{"side": "BUY_PROTECTION", "index_name": "CDX.NA.IG", "series": 47, "tenor": "5Y", "deal_spread_bp": "64"}', 300000000, "CTM",
                     6000000, "MATURED")):
            c.execute("INSERT INTO otc_contracts(contract_id, command_id, account_id, broker, kind, terms_json, notional_c, currency, start_date, maturity_date,"
                      " margin_mode, im_c, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (row[0], cid, "F1:PB_GS", "GS", row[1], row[2], row[3], "USD", "2026-10-21",
                                                                                 "2029-10-21", row[4], row[5], row[6]))
            ev("OTC_BOOKED", "F1:PB_GS", "otc", row[0], {"kind": row[1]}, at=ts - 3600, instrument=row[0])
        ev("OTC_TERMINATED", "F1:PB_GS", "otc", "SWP-T2", {"termination_value_c": -12345}, instrument="SWP-T2")
        ev("SWAP_MATURED", "F1:PB_GS", "otc", "CDX-T3", {"date": "2026-10-23"}, instrument="CDX-T3", command=lid_cmd)
        # financing: a stock borrow opened and fully returned (stored lender "street lender (modelled)")
        c.execute("INSERT INTO borrows(borrow_id, command_id, account_id, instrument_id, qty, qty_returned, fee_rate, rebate_rate, tier, lender, collateral_c, currency,"
                  " opened_ts, opened_date, status, closed_ts) VALUES ('B-T1',?,'F1:US_EXCHANGE','AAPL',500,500,'0.003','0.0357','GC','street lender (modelled)',0,"
                  "'USD',?,'2026-10-23','CLOSED',?)", (cid, ts, ts + 60))
        ev("BORROW_OPENED", "F1:US_EXCHANGE", "borrow", "B-T1", {"qty": 500, "mark": "300.00", "lender": "street lender (modelled)", "fee_rate": "0.003"}, instrument="AAPL")
        ev("BORROW_RETURNED", "F1:US_EXCHANGE", "borrow", "B-T1", {"qty": 500, "reason": "cover", "collateral_returned_c": 15300000, "closed": True}, at=ts + 60,
           instrument="AAPL")
        # securities lent and fully recalled (-> returned)
        ev("SECURITIES_LENT", "F1:US_EXCHANGE", "sec_loan", "SL-T1", {"qty": 200, "counterparty_id": "CP-1", "fee_rate": "0.0025", "collateral_c": 6120000},
           instrument="AAPL")
        ev("SECURITIES_RECALLED", "F1:US_EXCHANGE", "sec_loan", "SL-T1", {"qty": 200, "collateral_returned_c": 6120000, "closed": True}, at=ts + 120, instrument="AAPL")
        # a repo opened, rolled by the lifecycle, then closed
        ev("REPO_OPENED", "F1:DEBT", "repo", "R-T1", {"side": "REPO", "counterparty_id": "CP-2", "face": 1000000, "cash_c": 99000000, "rate": "0.0405"},
           instrument="UST10Y")
        ev("REPO_ROLLED", "F1:DEBT", "repo", "R-T1", {"rate": "0.0410"}, at=ts + 86400, command=lid_cmd)
        ev("REPO_CLOSED", "F1:DEBT", "repo", "R-T1", {"principal_c": 99000000, "interest_c": 11000}, at=ts + 2 * 86400)
        # a cash loan drawn and repaid; an automatic PB financing draw (not a trade)
        ev("LOAN_DRAWN", "F1:CREDIT", "loan", "L-T1", {"kind": "CASH_LOAN", "currency": "USD", "amount_c": 50000000, "rate": "0.0512", "lender": "Bank (assumed terms)"})
        ev("LOAN_REPAID", "F1:CREDIT", "loan", "L-T1", {"kind": "CASH_LOAN", "currency": "USD", "amount_c": 50010000, "closed": True}, at=ts + 3600)
        ev("LOAN_DRAWN", "F1:PB_GS", "loan", "L-PB", {"kind": "PB_MARGIN", "currency": "USD", "amount_c": 100000})
        # private credit: a commitment, a coupon (income, not a trade) and the final repayment
        ev("PC_COMMITTED", "F1:CREDIT", "private_loan", "PCL-T1", {"borrower": "Acme Holdings", "principal_c": 100000000, "spread": "0.055"}, instrument="PCL-T1")
        ev("PC_PAID", "F1:CREDIT", "private_loan", "PCL-T1", {"final": False, "coupon_c": 120000}, at=ts + 3600, instrument="PCL-T1")
        ev("PC_PAID", "F1:CREDIT", "private_loan", "PCL-T1", {"final": True, "face_c": 100000000, "coupon_c": 120000}, at=ts + 7200, instrument="PCL-T1")


if __name__ == "__main__":
    unittest.main()
