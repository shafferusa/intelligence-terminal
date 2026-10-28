"""FINSIM1_PLAN P2 Part A (release 1.0.5): Completed Trades' executions route (C7 / 15.3 E3) and the source labels (15.7 item 7) -
P2 acceptance 1 (a) and (d), acceptance 2 for the executions route; directive section 18 #7 (completed-trade history), gaps OMS-04 /
TEST-02 (the API part).  Every expected number is worked by hand in the comment next to it.

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
