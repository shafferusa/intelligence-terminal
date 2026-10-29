"""WP-11 API tests (TERMINAL_DESIGN.md §15, §19.12 "API round trip"): ``Router.handle`` without sockets, plus one
``http.client`` test on a temp port.  Every expected number is worked by hand in a comment; nothing touches the network
(FakeProvider, FakeClock, a temp home; FINSIM_HOME / FINSIM_TERMINAL_DB point at the temp folder).

The world: the Test Book ``T1`` (USD 10,000,000.00 in T1:TREASURY, every other account empty) at 2026-10-20 14:00 ET
(Tuesday, US_EQUITY open); AAPL last 339.50 observed 2 minutes ago on a completed 1-minute bar with volume 61,900.
"""
from __future__ import annotations

import csv
import http.client
import io
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from decimal import Decimal

from tests.terminal_helpers import _Log
from finsim.terminal import api as _api, config, server as _server
from finsim.terminal.clock import FakeClock
from finsim.terminal.commands import Command
from finsim.terminal.quotes.providers import FakeProvider

AT = "2026-10-20T14:00:00-04:00"      # Tuesday 14:00 ET: US_EQUITY open (09:30-16:00)
T1 = "T1"
TREASURY = "T1:TREASURY"
USX = "T1:US_EXCHANGE"
AAPL_LAST = "339.50"


def build(tmp: str, at: str = AT, books=None):
    os.environ[config.HOME_ENV] = tmp
    os.environ[config.DB_ENV] = os.path.join(tmp, "terminal.db")
    clock = FakeClock(at)
    fp = FakeProvider(clock)
    return _server.build_runtime(tmp, os.path.join(tmp, "terminal.db"), clock=clock, offline=True, fake_provider=fp, log=_Log(),
                                 books=books or [config.TEST_BOOK], fetch_threads=0, news_enabled=False)


def quote(rt, sym: str, last: str, age: int = 120, vol: int = 61900) -> None:
    """Put a Yahoo-style print into the FakeProvider and let the (inline, fetch_threads=0) worker load it into the cache."""
    now = rt.clock.epoch()
    qt = now - age
    rt.fp.set(sym, last=last, quote_time=qt, bar=(qt - qt % 60 - 60, vol))
    rt.cache.subscribe([sym], 300, "PAGE")
    rt.worker.tick()


class ApiBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-api-")
        self.rt = build(self.tmp)
        quote(self.rt, "AAPL", AAPL_LAST)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def handle(self, method, path, body=None, ip="127.0.0.1", headers=None):
        return self.rt.router.handle(method, path, None, body, ip, headers)

    def get(self, path, **kw):
        status, body, headers = self.handle("GET", path, **kw)
        return status, body, headers

    def ok(self, method, path, body=None, **kw):
        status, out, headers = self.handle(method, path, body, **kw)
        self.assertEqual(200, status, f"{method} {path} -> {status} {out}")
        return out, headers


# ===== the round trip ===================================================================================================================
class RoundTrip(ApiBase):
    def test_health_shape_and_marker(self):
        status, h, headers = self.get("/api/health")
        self.assertEqual(200, status)
        self.assertEqual("finsim-terminal", h["app"])                         # the marker the launcher and the VBS look for
        self.assertEqual(__import__("finsim.terminal", fromlist=["VERSION"]).VERSION, h["version"])
        self.assertEqual(1, h["schema"])
        self.assertTrue(h["offline"])
        self.assertEqual(os.getpid(), h["pid"])
        for key in ("uptime_s", "started_ts", "db_path", "db_size", "state_version", "clock", "integrity", "providers", "workers", "lifecycle", "attention_open",
                    "calendar", "lock", "modules"):
            self.assertIn(key, h, key)
        self.assertEqual({"utc", "ny", "trading_day", "accounting_day", "epoch"}, set(h["clock"]))
        self.assertEqual("2026-10-20", h["clock"]["trading_day"])
        self.assertEqual("2026-10-20", h["clock"]["accounting_day"])             # 14:00 ET is before the 17:00 close
        self.assertTrue(h["integrity"]["ok"])                                   # integrity("ledger") recorded at build
        self.assertFalse(h["integrity"]["hold"])
        self.assertEqual({"quotes", "matcher", "lifecycle", "supervisor"}, set(h["workers"]))
        self.assertFalse(h["workers"]["quotes"]["alive"])                       # not started in this test
        if h["modules"].get("lifecycle") == "bound":                             # WP-08 merged: a real (unstarted) scheduler
            self.assertFalse(h["workers"]["lifecycle"]["alive"])
            self.assertIsNone(h["lifecycle"]["due"] if h["lifecycle"]["due"] is None else None)
        else:                                                                    # WP-08 absent -> declared, never faked
            self.assertIn("NOT_BUILT", h["workers"]["lifecycle"].get("note", ""))
            self.assertIn("NOT_BUILT", h["lifecycle"]["note"])
        self.assertIn("yahoo", h["providers"])
        self.assertEqual("no-store", headers["Cache-Control"])
        self.assertTrue(set(h["modules"].values()) <= {"bound", "missing", "disabled"}, h["modules"])   # every optional desk is declared, none errored
        self.assertEqual("bound", h["modules"]["orders"])
        self.assertEqual(__import__("finsim.terminal", fromlist=["VERSION"]).VERSION, h["lock"] and h["version"])

    def test_accounts_books_and_state_before_any_trade(self):
        rows, _ = self.ok("GET", "/api/accounts?book=T")
        ids = [r["account_id"] for r in rows]
        self.assertEqual([TREASURY, USX, "T1:DEBT", "T1:CREDIT", "T1:FOREIGN_FX"], ids)   # FUND profile: PRIVATE unlocks at 25M, T1 has 10M
        tre = rows[0]
        self.assertEqual("10000000.00", tre["nav"])                               # the cents codec: nav_c 1,000,000,000 -> "10000000.00"
        self.assertEqual("10000000.00", tre["available_usd"])
        self.assertEqual((0, 0, 0), (tre["working_orders"], tre["pending_settlements"], tre["attention_count"]))
        self.assertEqual("0.00", rows[1]["nav"])
        self.assertNotIn("nav_c", tre)
        books, _ = self.ok("GET", "/api/books")
        self.assertEqual(1, len(books))
        b = books[0]
        self.assertEqual(("T", "Test Book", "FUND"), (b["book_code"], b["name"], b["profile"]))
        self.assertEqual("T1", b["lifetime"]["lifetime_id"])
        self.assertEqual("10000000.00", b["lifetime"]["starting_capital"])
        self.assertEqual("10000000.00", b["nav"]["value"])
        e = b["eligibility"]
        # NAV 10,000,000 >= ECP floor 10,000,000 -> ecp True; < PB/OTC threshold 25,000,000 -> pb_otc False; FUND QP threshold 25,000,000 -> private False
        self.assertEqual((True, False, False), (e["ecp"], e["pb_otc"], e["private_markets"]))
        self.assertEqual("25000000.00", e["threshold"])
        self.assertEqual("25000000.00", e["qp_threshold"])
        self.assertEqual(5, len(b["accounts"]))
        self.assertEqual(0, b["past_lifetimes"])
        st, _ = self.ok("GET", "/api/state?book=T")
        for key in ("accounts", "settings", "sessions", "clock", "attention", "integrity", "version", "conventions", "books"):
            self.assertIn(key, st, key)
        self.assertEqual("T", st["book"])
        self.assertIn("FILL_PRICE", st["conventions"])
        self.assertTrue(st["sessions"]["US_EQUITY"]["open"])
        self.assertEqual("0.10", st["settings"]["participation_rate"])
        self.assertEqual("25000000.00", st["settings"]["pb_otc_threshold"])     # settings cents keys go through the same codec
        self.assertEqual({"INDIVIDUAL": "5000000.00", "FUND": "25000000.00", "INSTITUTION": "0.00"}, st["settings"]["qp_threshold"])

    def test_preview_submit_order_fills_transactions(self):
        req = {"account_id": USX, "instrument_id": "AAPL", "side": "BUY", "qty": 100, "order_type": "MARKET", "tif": "DAY"}
        pv, _ = self.ok("POST", "/api/orders/preview", dict(req))                 # previews need no request_id
        self.assertTrue(pv["ok"], pv.get("blockers"))
        self.assertTrue(pv["preview_id"].startswith("pv:"))
        # gross 100 x 339.50 = 33,950.00; fee max(100 x 0.005, 1.00) = max(0.50, 1.00) = 1.00
        self.assertEqual("33950.00", pv["economics"]["gross"])
        self.assertEqual("1.00", pv["economics"]["fee"])
        self.assertEqual(Decimal("339.50"), Decimal(pv["quote"]["last"]))        # prices are canonical Decimal strings ("339.5")
        # 400 without a request id: nothing reaches the bus
        status, body, _ = self.handle("POST", "/api/orders", dict(req))
        self.assertEqual(400, status)
        self.assertEqual("BAD_REQUEST_ID", body["error"]["code"])
        self.assertIsNone(body["request_id"])
        with self.rt.db.reader() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0])
        rid = "ui:api-aapl-buy-0001"
        sub = dict(req, request_id=rid, preview_id=pv["preview_id"])
        out, headers = self.ok("POST", "/api/orders", sub)
        self.assertNotIn("X-Replayed", headers)
        order = out["order"]
        self.assertEqual(("FILLED", 100, "AAPL"), (order["status"], order["filled_qty"], order["symbol"]))
        self.assertEqual(Decimal("339.50"), Decimal(order["avg_px"]))
        self.assertEqual(1, len(out["fills"]))
        fill = out["fills"][0]
        self.assertEqual("33950.00", fill["gross"])                               # fills carry cents under the stripped name
        self.assertEqual("1.00", fill["fee"])
        self.assertEqual(1, len(out["sweeps"]))
        self.assertEqual("33951.00", out["sweeps"][0]["amount"])                  # exactly the shortfall: 33,950.00 + 1.00
        self.assertEqual(TREASURY, out["sweeps"][0]["from_account"])
        oid = order["order_id"]
        # replay: same id + same payload -> the stored body + X-Replayed: 1; reuse: same id + different payload -> 409
        out2, headers2 = self.ok("POST", "/api/orders", sub)
        self.assertEqual("1", headers2.get("X-Replayed"))
        self.assertEqual(oid, out2["order"]["order_id"])
        status, body, _ = self.handle("POST", "/api/orders", dict(sub, qty=200))
        self.assertEqual((409, "REQUEST_ID_REUSED"), (status, body["error"]["code"]))
        self.assertEqual(rid, body["request_id"])
        with self.rt.db.reader() as conn:
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0])
        # the order, its events, fills and entries
        od, _ = self.ok("GET", f"/api/orders/{oid}")
        self.assertEqual("FILLED", od["status"])
        self.assertGreaterEqual(len(od["events"]), 2)
        self.assertEqual("FILLED", od["events"][-1]["to_status"])
        self.assertEqual(1, len(od["fills"]))
        self.assertEqual(1, len(od["entries"]))
        self.assertEqual("FILL", od["entries"][0]["kind"])
        self.assertIn("created_ny", od)                                          # *_ts epochs gain a *_ny display string
        fills, _ = self.ok("GET", f"/api/accounts/{USX}/fills")
        self.assertEqual(1, len(fills))
        self.assertEqual(Decimal("339.50"), Decimal(fills[0]["price"]))
        self.assertEqual("AAPL", fills[0]["quote"]["instrument_id"])
        self.assertEqual(120, fills[0]["quote"]["age_s"])                        # observed 120 s before the clock, which has not moved
        # transactions: the fill entry (no cash line: a payable) and the sweep-in (Dr 1010 33,951.00)
        tx, _ = self.ok("GET", f"/api/accounts/{USX}/transactions")
        kinds = sorted(r["kind"] for r in tx["rows"])
        self.assertEqual(["FILL", "SWEEP_IN"], kinds)
        sweep = next(r for r in tx["rows"] if r["kind"] == "SWEEP_IN")
        fillrow = next(r for r in tx["rows"] if r["kind"] == "FILL")
        self.assertEqual({"USD": "33951.00"}, sweep["net_cash"])
        self.assertEqual({}, fillrow["net_cash"])
        self.assertEqual(("AAPL", 100, Decimal("339.50"), "33950.00", "1.00"), (fillrow["instrument_id"], fillrow["qty"], Decimal(fillrow["price"]), fillrow["gross"], fillrow["fee"]))
        dr1100 = next(l for l in fillrow["lines"] if l["gl"] == "1100")
        self.assertEqual(("33950.00", None, "AAPL"), (dr1100["debit"], dr1100["credit"], dr1100["instrument_id"]))
        cr2100 = next(l for l in fillrow["lines"] if l["gl"] == "2100")
        self.assertEqual("33951.00", cr2100["credit"])
        # CSV export of the same rows
        status, csv_text, headers = self.handle("GET", f"/api/accounts/{USX}/transactions?format=csv")
        self.assertEqual(200, status)
        self.assertTrue(headers["Content-Type"].startswith("text/csv"))
        lines = csv_text.strip().split("\n")
        self.assertEqual("entry_id,ts_ny,business_date,kind,memo,instrument_id,qty,price,gross,fee,net_USD", lines[0])
        self.assertEqual(3, len(lines))
        parsed = {row["kind"]: row for row in csv.DictReader(io.StringIO(csv_text))}
        self.assertEqual("33951.00", parsed["SWEEP_IN"]["net_USD"])
        self.assertEqual(("AAPL", "100", Decimal("339.50"), "33950.00", "1.00", ""),
                         (parsed["FILL"]["instrument_id"], parsed["FILL"]["qty"], Decimal(parsed["FILL"]["price"]), parsed["FILL"]["gross"], parsed["FILL"]["fee"], parsed["FILL"]["net_USD"]))
        # NAV after the trade: US_EXCHANGE = cash 33,951.00 + AAPL 100 x 339.50 = 33,950.00 - payable 33,951.00 = 33,950.00;
        # Treasury = 10,000,000.00 - 33,951.00 = 9,966,049.00; total 9,999,999.00 = 10,000,000.00 - the 1.00 commission
        rows, _ = self.ok("GET", "/api/accounts?book=T")
        by = {r["account_id"]: r for r in rows}
        self.assertEqual("33950.00", by[USX]["nav"])
        self.assertEqual("9966049.00", by[TREASURY]["nav"])
        self.assertEqual(1, by[USX]["pending_settlements"])                      # the T+1 payable
        pf, _ = self.ok("GET", "/api/portfolio?scope=ALL&book=T")
        self.assertEqual("9999999.00", pf["nav"]["nav"])
        self.assertEqual(1, len(pf["positions"]))
        p = pf["positions"][0]
        self.assertEqual(("AAPL", 100, "33950.00", Decimal("339.50"), "delayed"), (p["instrument_id"], p["qty"], p["cost"], Decimal(p["mark"]["value"]), p["mark"]["quality"]))
        self.assertEqual(p["mark"]["value"], p["mark"]["mark"])                   # both spellings for the UI
        self.assertEqual(p["mark"]["as_of"], p["mark"]["quote_time"])
        self.assertEqual("33950.00", {c["klass"]: c for c in pf["by_class"]}["securities"]["mv"])          # P03 BM-04: by_class is a list of class rows
        det, _ = self.ok("GET", f"/api/accounts/{USX}")
        self.assertEqual("33951.00", det["cash"]["USD"]["settled"])
        self.assertEqual("33951.00", det["cash"]["USD"]["payable"])
        self.assertEqual("33950.00", det["nav"]["nav"])
        self.assertEqual(1, det["positions_count"])
        self.assertEqual(1, len(det["pending_settlements"]))
        self.assertEqual(1, len(det["sweeps_today"]))
        pos, _ = self.ok("GET", f"/api/accounts/{USX}/positions")
        self.assertEqual(100, pos[0]["qty"])
        self.assertEqual(1, len(pos[0]["lots"]))
        pd, _ = self.ok("GET", f"/api/accounts/{USX}/positions/AAPL")
        self.assertEqual(100, pd["position"]["qty"])
        self.assertEqual(1, len(pd["fills"]))
        status, csv_text, _ = self.handle("GET", "/api/export/positions.csv?scope=ALL&book=T")
        self.assertEqual(200, status)
        lines = csv_text.strip().split("\n")
        # the fixture columns come first; 1.0.6 (P3, PORTFOLIO-04) appends asset class, sector, counterparty, the to-date figures, total P&L,
        # margin and collateral after them (their values are P3's tests)
        self.assertEqual("account_id,instrument_id,qty,cost,mark,quality,mv,unrealised", ",".join(lines[0].strip().split(",")[:8]))
        prow = next(csv.DictReader(io.StringIO(csv_text)))
        self.assertEqual(("T1:US_EXCHANGE", "AAPL", "100", "33950.00", Decimal("339.50"), "delayed", "33950.00", "0.00"),
                         (prow["account_id"], prow["instrument_id"], prow["qty"], prow["cost"], Decimal(prow["mark"]), prow["quality"], prow["mv"], prow["unrealised"]))
        tre, _ = self.ok("GET", "/api/treasury?book=T")
        self.assertEqual("9966049.00", tre["treasury"]["nav"])
        self.assertEqual("33951.00", tre["treasury"]["advances"])
        self.assertEqual(1, len(tre["sweep_log"]))
        self.assertEqual(1, len(tre["capital_history"]))                        # init:capital:T1
        tb, _ = self.ok("GET", "/api/accounting/trial-balance?scope=ALL&book=T")
        self.assertTrue(tb["balanced"])
        self.assertEqual(tb["totals"]["debit"], tb["totals"]["credit"])
        # debits: 1010 Treasury 9,966,049 + 1010 US 33,951 + 1100 33,950 + 5000 1.00 + 3100 US 33,951 = 10,067,902.00
        self.assertEqual("10067902.00", tb["totals"]["debit"])
        act, _ = self.ok("GET", "/api/activity?limit=50")
        self.assertTrue(any(r["type"] == "fill" for r in act["rows"]))
        self.assertTrue(any(r["type"] == "transfer" for r in act["rows"]))
        self.assertEqual(act["rows"], sorted(act["rows"], key=lambda r: -r["ts"]))

    def test_orders_history_keeps_cancelled_expired(self):
        req = {"account_id": USX, "instrument_id": "AAPL", "side": "BUY", "qty": 10, "order_type": "LIMIT", "limit_px": "300.00", "tif": "DAY"}
        o1, _ = self.ok("POST", "/api/orders", dict(req, request_id="ui:api-limit-0001"))
        self.assertEqual("WORKING", o1["order"]["status"])                       # 300.00 is not marketable against last 339.50: it rests
        o2, _ = self.ok("POST", "/api/orders", dict(req, request_id="ui:api-limit-0002"))
        oid1, oid2 = o1["order"]["order_id"], o2["order"]["order_id"]
        open_, _ = self.ok("GET", f"/api/accounts/{USX}/orders?status=open")
        self.assertEqual({oid1, oid2}, {o["order_id"] for o in open_["orders"]})
        c, _ = self.ok("DELETE", f"/api/orders/{oid1}", {"request_id": "ui:api-cancel-0001"})
        self.assertEqual("CANCELLED", c["order"]["status"])
        status, body, _ = self.handle("DELETE", f"/api/orders/{oid1}", {"request_id": "ui:api-cancel-0002"})
        self.assertEqual((409, "NOT_OPEN"), (status, body["error"]["code"]))
        res = self.rt.bus.execute(Command("order.expire", {"order_id": oid2, "force": True}, "job:expire:test-0001", "lifecycle"))
        self.assertTrue(res.ok, res.body)
        open_, _ = self.ok("GET", f"/api/accounts/{USX}/orders?status=open")
        self.assertEqual([], open_["orders"])
        allo, _ = self.ok("GET", f"/api/accounts/{USX}/orders?status=all")
        got = {o["order_id"]: o["status"] for o in allo["orders"]}
        self.assertEqual({oid1: "CANCELLED", oid2: "EXPIRED"}, got)             # history keeps both
        self.assertEqual([], allo["rejections"])
        rows, _ = self.ok("GET", "/api/accounts?book=T")
        self.assertEqual(0, next(r for r in rows if r["account_id"] == USX)["working_orders"])

    def test_rejected_submit_is_recorded(self):
        req = {"account_id": USX, "instrument_id": "AAPL", "side": "BUY", "qty": 100, "order_type": "STOP", "stop_px": "300.00", "tif": "DAY", "request_id": "ui:api-stop-0001"}
        status, body, _ = self.handle("POST", "/api/orders", req)                # a buy stop below the last is refused (STOP_SIDE)
        self.assertEqual(400, status, body)
        self.assertEqual("STOP_SIDE", body["error"]["code"])
        with self.rt.db.reader() as conn:
            rej = conn.execute("SELECT request_id, code FROM order_rejections").fetchall()
        self.assertEqual([("ui:api-stop-0001", "STOP_SIDE")], [tuple(r) for r in rej])
        o, _ = self.ok("GET", f"/api/accounts/{USX}/orders?status=all")
        self.assertEqual(1, len(o["rejections"]))


# ===== error bodies, 202s, 501s ==========================================================================================================
class Errors(ApiBase):
    def test_every_error_body(self):
        status, body, _ = self.get("/api/accounts/T1:NOPE")
        self.assertEqual(404, status)
        self.assertEqual({"error", "request_id"}, set(body))
        self.assertEqual({"code", "message", "details"}, set(body["error"]))
        self.assertEqual("NOT_FOUND", body["error"]["code"])
        status, body, _ = self.get("/api/nope")
        self.assertEqual((404, "NOT_FOUND"), (status, body["error"]["code"]))
        status, body, _ = self.handle("PUT", "/api/health", {"request_id": "ui:api-put-0001"})
        self.assertEqual(405, status)
        # 409 from the bus keeps the command's own code and the request id
        status, body, _ = self.handle("POST", "/api/treasury/withdraw", {"request_id": "ui:api-wd-0001", "amount": "20000000.00", "currency": "USD", "book": "T"})
        self.assertEqual((409, "INSUFFICIENT_FUNDS", "ui:api-wd-0001"), (status, body["error"]["code"], body["request_id"]))
        status, body, _ = self.handle("POST", "/api/treasury/deposit", {"request_id": "ui:api-dep-0001", "amount": "abc", "currency": "USD"})
        self.assertEqual((400, "AMOUNT"), (status, body["error"]["code"]))
        status, body, _ = self.handle("POST", "/api/treasury/deposit", "{not json")
        self.assertEqual((400, "BAD_JSON"), (status, body["error"]["code"]))
        status, body, _ = self.handle("POST", "/api/shutdown", {}, ip="10.0.0.5")
        self.assertEqual((403, "FORBIDDEN"), (status, body["error"]["code"]))
        status, body, _ = self.handle("POST", "/api/jobs/run", {"request_id": "ui:api-jobs-0001"}, ip="192.168.1.9")
        self.assertEqual(403, status)
        ids = ",".join(f"S{i}" for i in range(201))
        status, body, _ = self.get(f"/api/quotes?ids={ids}")
        self.assertEqual((400, "TOO_MANY"), (status, body["error"]["code"]))
        status, body, _ = self.get("/api/accounting/pnl?scope=ALL&book=T&period=WEEK")
        self.assertEqual((400, "BAD_REQUEST"), (status, body["error"]["code"]))
        status, body, _ = self.get("/api/accounts?book=Z")
        self.assertEqual((404, "UNKNOWN_BOOK"), (status, body["error"]["code"]))
        status, body, _ = self.get("/api/orders/O-999999")
        self.assertEqual(404, status)
        status, body, _ = self.get("/api/instruments/NOPE123")
        self.assertEqual(404, status)
        status, body, _ = self.get("/api/accounts/T1:US_EXCHANGE/markets?tab=zzz")
        self.assertEqual((400, "BAD_REQUEST"), (status, body["error"]["code"]))

    def test_501_not_built_names_the_package(self):
        rt = self.rt
        # other packages land while this one is built: unbind the desks on this runtime so the 501 path is exercised whatever is merged
        for attr in ("swaps", "lifecycle", "private", "private_credit", "lifetimes"):
            setattr(rt, attr, None)
        cases = [("POST", "/api/otc/preview", {"account_id": USX, "kind": "IRS", "terms": {}}, "WP-17"),
                 ("GET", "/api/jobs", None, "WP-08"),
                 ("GET", "/api/private/companies", None, "WP-19"),
                 ("GET", "/api/credit/private?scope=ALL&book=T", None, "WP-21"),
                 ("POST", "/api/books/T/reset", {"request_id": "ui:api-reset-0001", "confirm": "RESET", "starting_capital": "1000000.00"}, "WP-23")]
        if "private.trade" not in rt.bus.kinds():                                 # command routes answer 501 only while the handler is absent
            cases.append(("POST", "/api/private/trades", {"request_id": "ui:api-priv-0001"}, "WP-19"))
        if "pc.commit" not in rt.bus.kinds():
            cases.append(("POST", "/api/credit/private", {"request_id": "ui:api-pc-0001"}, "WP-21"))
        for method, path, body, pkg in cases:
            status, out, _ = self.handle(method, path, body)
            self.assertEqual(501, status, f"{method} {path}: {out}")
            self.assertEqual("NOT_BUILT", out["error"]["code"])
            self.assertEqual(pkg, out["error"]["details"]["package"], path)
        with rt.db.reader() as conn:                                              # nothing was stored by any of them
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM commands WHERE request_id LIKE 'ui:api-%'").fetchone()[0])
        status, out, _ = self.handle("POST", "/api/books/T/reset", {"request_id": "ui:api-reset-0002", "confirm": "nope", "starting_capital": "1.00"})
        self.assertEqual((400, "CONFIRM"), (status, out["error"]["code"]))      # validation before the 501

    def test_503_retry_after_when_the_writer_is_busy(self):
        rt = self.rt
        rt.bus.lock_timeout = 0.05
        held = threading.Event()

        def hold():
            rt.db.acquire_writer()
            held.set()
            time.sleep(0.6)
            rt.db.release_writer()
        t = threading.Thread(target=hold)
        t.start()
        held.wait(2)
        status, body, headers = self.handle("POST", "/api/treasury/deposit", {"request_id": "ui:api-busy-0001", "amount": "1.00", "currency": "USD", "book": "T"})
        t.join()
        self.assertEqual((503, "WRITER_BUSY"), (status, body["error"]["code"]))
        self.assertEqual("1", headers["Retry-After"])
        with rt.db.reader() as conn:                                              # nothing stored: the id may be retried
            self.assertIsNone(conn.execute("SELECT 1 FROM commands WHERE request_id='ui:api-busy-0001'").fetchone())

    def test_202_chain_fetching_and_resolve(self):
        status, body, _ = self.get("/api/instruments/AAPL/chain")
        self.assertEqual(202, status, body)
        self.assertEqual({"status": "fetching", "retry_ms": 1500}, {k: body[k] for k in ("status", "retry_ms")})
        self.assertIn("AAPL", self.rt.chains.pending())                            # queued for the worker, nothing fetched on this thread
        status, body, _ = self.get("/api/instruments/resolve?symbol=ZZZQ")
        self.assertEqual(202, status)
        self.assertEqual("resolving", body["status"])
        self.rt.worker.tick()                                                    # the FakeProvider has no meta for ZZZQ -> 404 recorded by the worker
        status, body, _ = self.get("/api/instruments/resolve?symbol=ZZZQ")
        self.assertEqual((404, "NOT_FOUND"), (status, body["error"]["code"]))
        inst, _ = self.ok("GET", "/api/instruments/resolve?symbol=AAPL")
        self.assertEqual(("AAPL", "EQUITY", True), (inst["instrument_id"], inst["kind"], inst["optionable"]))
        status, body, _ = self.get("/api/instruments/MSFT/bars")
        self.assertEqual(202, status)                                            # not in the cache yet: subscribed and fetching
        # C8: the chart's bars come from ONE worker fetch of the session (request_bars), never from this thread: 202, then the bars
        status, body, _ = self.get("/api/instruments/AAPL/bars?range=1d")
        self.assertEqual((202, "fetching"), (status, body["status"]))
        self.rt.worker.tick()
        bars, _ = self.ok("GET", "/api/instruments/AAPL/bars?range=1d")
        self.assertEqual(1, len(bars["bars"]))                                   # the FakeProvider holds one bar ...
        self.assertEqual(61900, bars["bars"][0]["v"])
        self.assertFalse(bars["full_session"])                                   # ... and says it is not the whole session

    def test_quotes_instruments_and_markets(self):
        q, _ = self.ok("GET", "/api/quotes?ids=AAPL,MSFT")
        self.assertEqual(Decimal("339.50"), Decimal(q["quotes"]["AAPL"]["last"]))
        self.assertEqual(120, q["quotes"]["AAPL"]["age_s"])
        self.assertEqual("delayed", q["quotes"]["AAPL"]["quality"])
        self.assertNotIn("MSFT", q["quotes"])                                    # never fetched: absent, not invented
        self.assertTrue(q["sessions"]["US_EQUITY"]["open"])
        hits, _ = self.ok("GET", "/api/instruments?q=AAP&limit=5")
        self.assertEqual("AAPL", hits[0]["instrument_id"])
        self.assertEqual(Decimal("339.50"), Decimal(hits[0]["quote"]["last"]))
        d, _ = self.ok("GET", "/api/instruments/AAPL")
        self.assertEqual("FULL", d["support"])
        self.assertEqual(Decimal("339.50"), Decimal(d["mark"]["value"]))
        self.assertTrue(d["session"]["open"])
        m, _ = self.ok("GET", f"/api/accounts/{USX}/markets")
        self.assertEqual("equities", m["tab"])
        self.assertEqual(["equities", "options", "futures"], [t["key"] for t in m["tabs"]])
        self.assertTrue(any(r["instrument_id"] == "AAPL" for r in m["rows"]))
        f, _ = self.ok("GET", f"/api/accounts/{USX}/markets?tab=futures")
        self.assertTrue(f["rows"])
        self.assertTrue(all(r["kind"] == "FUTURE" for r in f["rows"]))
        c, _ = self.ok("GET", "/api/curve")
        self.assertEqual("unavailable", c["quality"])                            # nothing fetched offline: declared unavailable
        r, _ = self.ok("GET", "/api/rates")
        self.assertIn("SOFR", r)
        self.assertIsNone(r["SOFR"]["value"])

    def test_history_read_only_after_a_stubbed_reset(self):
        now = self.rt.clock.epoch()
        with self.rt.db.writer() as tx:                                           # WP-23's reset is not built: close the lifetime by hand
            tx.conn.execute("UPDATE lifetimes SET status='CLOSED', ended_ts=?, reason='stubbed reset (test)' WHERE lifetime_id='T1'", (now,))
            tx.conn.execute("UPDATE accounts SET status='CLOSED', closed_ts=? WHERE lifetime_id='T1'", (now,))
        h, _ = self.ok("GET", "/api/history/T1/portfolio")
        self.assertTrue(h["read_only"])
        self.assertEqual("T1:ALL", h["scope"])
        self.assertEqual("2026-10-20", h["frozen_at"])
        self.assertTrue(h["nav"]["archived"])
        self.assertEqual("10000000.00", h["nav"]["capital"])
        self.assertEqual("stubbed reset (test)", h["lifetime"]["reason"])
        p, _ = self.ok("GET", "/api/history/T1/positions")
        self.assertEqual([], p["positions"])
        tbv, _ = self.ok("GET", "/api/history/T1/accounting/trial-balance")
        self.assertTrue(tbv["read_only"] and tbv["balanced"])
        status, csv_text, headers = self.handle("GET", "/api/history/T1/export/transactions.csv")
        self.assertEqual(200, status)
        self.assertTrue(csv_text.startswith("entry_id,ts_ny,business_date,kind,memo"))
        status, body, _ = self.get("/api/history/T1/nope")
        self.assertEqual(404, status)
        status, body, _ = self.get("/api/history/Q9/portfolio")
        self.assertEqual(404, status)
        lt, _ = self.ok("GET", "/api/books/T/lifetimes")
        self.assertEqual("CLOSED", lt["lifetimes"][0]["status"])
        self.assertEqual("2026-10-20", lt["lifetimes"][0]["ended"])


# ===== light commands, settings, watchlists, attention, dividends; news gone (1.0.5) ======================================================
class Lights(ApiBase):
    def test_settings_watchlists_attention(self):
        s, _ = self.ok("PATCH", "/api/settings", {"request_id": "ui:api-set-0001", "participation_rate": "0.20"})
        self.assertEqual("0.20", s["participation_rate"])
        status, body, _ = self.handle("PATCH", "/api/settings", {"request_id": "ui:api-set-0002", "nope": 1})
        self.assertEqual((400, "SETTING_UNKNOWN"), (status, body["error"]["code"]))
        s, _ = self.ok("PATCH", "/api/settings", {"request_id": "ui:api-set-0003", "pb_otc_threshold": "30000000.00"})
        self.assertEqual("30000000.00", s["pb_otc_threshold"])                    # decimal in -> cents stored -> decimal out
        w, _ = self.ok("POST", "/api/watchlists", {"request_id": "ui:api-wl-0001", "account_id": USX, "name": "core", "instrument_id": "AAPL"})
        self.assertEqual(1, len(w["lists"]))
        self.assertEqual(("core", "AAPL", Decimal("339.50")), (w["lists"][0]["name"], w["lists"][0]["items"][0]["instrument_id"], Decimal(w["lists"][0]["items"][0]["quote"]["last"])))
        w, _ = self.ok("DELETE", "/api/watchlists", {"request_id": "ui:api-wl-0002", "account_id": USX, "name": "core", "instrument_id": "AAPL"})
        self.assertEqual([], w["lists"])
        res = self.rt.bus.execute(Command("attention.open", {"severity": "WARN", "source": "test", "code": "TEST_NOTE", "message": "hello"}, "ui:api-att-0001", "ui"))
        self.assertTrue(res.ok, res.body)
        aid = res.body["id"]
        rows, _ = self.ok("GET", "/api/attention")
        self.assertIn(aid, [r["id"] for r in rows])
        r, _ = self.ok("POST", f"/api/attention/{aid}/resolve", {"request_id": "ui:api-att-0002"})
        self.assertIsNotNone(r["resolved_ts"])
        rows, _ = self.ok("GET", "/api/attention")
        self.assertNotIn(aid, [x["id"] for x in rows])
        status, body, _ = self.handle("POST", "/api/attention/999999/resolve", {"request_id": "ui:api-att-0003"})
        self.assertEqual(404, status)
        u, _ = self.ok("GET", "/api/unsupported")
        self.assertGreaterEqual(u["count"], 10)
        cal, _ = self.ok("GET", "/api/calendar?year=2026")
        self.assertIn("2026-11-26", cal["holidays"])                             # Thanksgiving
        self.assertIn("2026-11-27", cal["early_closes"])
        ses, _ = self.ok("GET", "/api/sessions")
        self.assertIn("US_EQUITY", ses["rules"])

    def test_dividends_from_the_table(self):
        req = {"account_id": USX, "instrument_id": "AAPL", "side": "BUY", "qty": 100, "order_type": "MARKET", "tif": "DAY", "request_id": "ui:api-div-buy-0001"}
        self.ok("POST", "/api/orders", req)
        with self.rt.db.writer() as tx:                                           # what the worker's dividends.upsert writes (Yahoo gives no pay date)
            tx.conn.execute("INSERT INTO dividends(instrument_id, ex_date, amount, pay_date, source, fetched_ts) VALUES ('AAPL','2026-11-06','0.26',NULL,'Yahoo chart events',?)",
                            (self.rt.clock.epoch(),))
        d, _ = self.ok("GET", f"/api/dividends?account_id={USX}")
        self.assertEqual(1, len(d))
        row = d[0]
        # 100 shares x 0.26 = 26.00; pay date by convention = ex 2026-11-06 + 15 business days (Thanksgiving 11-26 skipped) = 2026-11-30
        self.assertEqual(("AAPL", 100, "26.00", "2026-11-30", True), (row["instrument_id"], row["position_qty"], row["expected"], row["pay_date"], row["pay_date_convention"]))
        self.assertEqual("dividend", row["kind"])

    def test_news_is_gone(self):
        """UI-02 / UI-03 / UI-15 (directive section 2): the news module is deleted, /api/news answers 404 NOT_FOUND like any unknown route
        (never a 501 that promises it), no news job runs, nothing reports a news state (no runtime attribute, no module entry, no health
        key, no package id), the news setting is gone (a PATCH answers 400 and stores nothing) and the old simulation's archive link
        left with the legacy command; the attention summary the news page carried has its own route."""
        import importlib
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("finsim.terminal.news")
        for path in ("/api/news", "/api/news?limit=10&book=T", "/api/legacy"):
            status, body, _ = self.get(path)
            self.assertEqual((404, "NOT_FOUND"), (status, body["error"]["code"]), path)
        self.assertFalse(hasattr(self.rt, "news"))                                 # 1.0.6: the runtime has no news attribute
        h, _ = self.ok("GET", "/api/health")
        self.assertNotIn("news", h)                                                # 1.0.6: the key left with the rest of the news code
        self.assertNotIn("news", h["modules"])
        self.assertNotIn("news", _api.PACKAGES)
        self.assertNotIn("news_enabled", _api.SETTING_KEYS)
        self.assertFalse({"yahoo_news", "fed", "newspaper"} & set(h["providers"]))
        self.assertFalse([n for n in self.rt.worker.refresher.stats() if str(n).startswith("news_")])   # no news slot on the quote worker
        status, body, _ = self.handle("PATCH", "/api/settings", {"request_id": "ui:api-news-0001", "news_enabled": False})
        self.assertEqual((400, "SETTING_UNKNOWN"), (status, body["error"]["code"]))
        with self.rt.db.reader() as conn:                                          # nothing was journaled for it
            self.assertIsNone(conn.execute("SELECT 1 FROM events WHERE type='SETTINGS_CHANGED' AND ref_id LIKE '%news_enabled%'").fetchone())
        s, _ = self.ok("GET", "/api/attention/summary?book=T")
        self.assertEqual("T1:ALL", s["scope"])
        self.assertNotIn("news", s)
        st, _ = self.ok("GET", "/api/state")
        self.assertNotIn("NEWS", st["conventions"])                                # tables.CONVENTIONS lost its NEWS sentence
        self.assertNotIn("news_enabled", st["settings"])                           # a new database never stores the news setting

    def test_retired_news_providers_skipped_on_load(self):
        """15.7 item 5: the provider_health rows an older release stored for yahoo_news / fed / newspaper / news_* stay in the database,
        inert: a restart never loads them, so /api/health never lists them and a stale news failure never becomes a WARN in the strip."""
        now = self.rt.clock.epoch()
        with self.rt.db.writer() as tx:
            for name in ("yahoo_news", "fed", "newspaper", "news_paper"):
                tx.x("INSERT INTO provider_health(provider, last_ok_ts, last_err_ts, last_error, consecutive_errors, requests_today, day, down_until_ts)"
                     " VALUES (?,?,?,?,?,?,?,?)", (name, now - 3600, now - 60, "offline: no network", 4, 9, "2026-10-20", now + 600))
        self.rt.close()
        self.rt = build(self.tmp)
        h, _ = self.ok("GET", "/api/health")
        self.assertFalse({"yahoo_news", "fed", "newspaper", "news_paper"} & set(h["providers"]), sorted(h["providers"]))
        self.assertIn("yahoo", h["providers"])
        s, _ = self.ok("GET", "/api/attention/summary?book=T")
        self.assertEqual((0, []), (s["providers"]["paused"], s["providers"]["down"]))
        with self.rt.db.reader() as conn:                                          # the stored rows are untouched
            self.assertEqual(4, conn.execute("SELECT COUNT(*) FROM provider_health WHERE provider IN ('yahoo_news','fed','newspaper','news_paper')").fetchone()[0])

    def test_c5_refusal_recorder_records_a_refused_bond_ticket(self):
        """C5: a recorder registered for bonds.trade receives the refused ticket (kind, payload, result) and the HTTP answer is the same
        as without it - also when the recorder raises; an accepted command, another kind and the not-a-rejection codes are never handed over."""
        got = []

        def recorder(req, kind, payload, res):
            got.append((kind, dict(payload), res.http, res.code, req.request_id))

        def broken(req, kind, payload, res):
            raise RuntimeError("recorder failure (test)")
        bond = sorted(i.instrument_id for i in self.rt.instruments.all() if i.kind == "BOND")[0]
        trade = {"account_id": "T1:NOPE", "instrument_id": bond, "side": "BUY", "qty": 1000}
        status0, body0, _ = self.handle("POST", "/api/bonds/trade", dict(trade, request_id="ui:api-c5-0000"))   # no recorder yet
        self.assertEqual(400, status0, body0)
        _api.add_refusal_recorder("bonds.trade", recorder)
        _api.add_refusal_recorder(["bonds.trade", "fx.spot"], broken)
        _api.add_refusal_recorder("bonds.trade", recorder)                        # registering it again is a no-op
        try:
            status1, body1, _ = self.handle("POST", "/api/bonds/trade", dict(trade, request_id="ui:api-c5-0001"))
            self.assertEqual((status0, body0["error"]), (status1, body1["error"]))   # the answer is unchanged (only the request id differs)
            self.assertEqual("ui:api-c5-0001", body1["request_id"])
            self.assertEqual([("bonds.trade", trade, 400, body1["error"]["code"], "ui:api-c5-0001")], got)
            # another kind's refusal is not this recorder's; a deposit of a valid amount is not a refusal at all
            status, body, _ = self.handle("POST", "/api/treasury/deposit", {"request_id": "ui:api-c5-0002", "amount": "abc", "currency": "USD"})
            self.assertEqual((400, "AMOUNT"), (status, body["error"]["code"]))
            self.assertEqual(1, len(got))
            # the "come back with more" codes and replays are not rejections
            req = _api.Request(self.rt, "POST", "/x", (), {}, {}, "127.0.0.1", {})
            for code, replayed in (("REQUEST_ID_REUSED", False), ("CONFIRM_REQUIRED", False), ("PREVIEW_STALE", False), ("BAD_REQUEST_ID", False), ("SOMETHING", True)):
                _api._record_refusal(req, "bonds.trade", {}, _api.Result(False, 409, {"error": {"code": code, "message": code}}, replayed=replayed))
            self.assertEqual(1, len(got))
            _api._record_refusal(req, "bonds.trade", {}, _api.Result(False, 500, {"error": {"code": "COMMAND_FAILED", "message": "x"}}))   # a 5xx is a failure, not a refusal
            self.assertEqual(1, len(got))
        finally:
            self.assertEqual(1, _api.remove_refusal_recorder(recorder))
            self.assertEqual(1, _api.remove_refusal_recorder(broken))
        self.assertFalse([f for _k, f in _api.refusal_recorders() if f in (recorder, broken)])

    def test_c6_unsupported_rows_follow_their_probes(self):
        """C6 / 15.3 E1 / 15.7 item 6: the static rows (extended hours, forced liquidation, CSA interest) and one PLANNED row per capability
        this build adds, shown while its probe function is absent and gone as soon as it exists; the forced-liquidation wording follows
        the margin calls."""
        import types as _types
        rt = self.rt

        def rows():
            u, _ = self.ok("GET", "/api/unsupported")
            return {r["item"]: r for r in u["items"]}
        planned = {"Margin calls": ("collateral", "margin_calls_rows"), "Simulated counterparty settlement fails": ("blotters", "execution_detail"),
                   "OTC dealer quotes (request a quote, then accept or decline it)": ("swaps", "rfq_rows"),
                   "Good-till-date and next-session orders": ("orders", "gtd_supported"), "Securities pledged as CSA collateral": ("collateral", "pledges_view")}
        r = rows()
        self.assertEqual(("NOT_SIMULATED", "an order entered outside the regular session rests until the next regular open"),
                         (r["Extended-hours trading"]["state"], r["Extended-hours trading"]["wording"]))
        self.assertEqual(("NOT_SIMULATED", "variation margin received under a CSA is restricted and earns no PB credit interest; collateral posted earns none"),
                         (r["CSA interest on cash collateral"]["state"], r["CSA interest on cash collateral"]["wording"]))
        self.assertNotIn("Forced liquidation and margin calls that close positions", r)       # the old row is reworded
        calls_built = callable(getattr(getattr(rt, "collateral", None), "margin_calls_rows", None))
        self.assertEqual("NEVER", r["Forced liquidation"]["state"])
        self.assertEqual(_api.FORCED_LIQUIDATION_WITH_CALLS if calls_built else "positions are never sold for you; a margin shortfall raises a warning and an attention item",
                         r["Forced liquidation"]["wording"])
        for item, (attr, fn) in planned.items():                                   # whatever this tree has built: a row iff its probe is absent
            self.assertEqual(not callable(getattr(getattr(rt, attr, None), fn, None)), item in r, item)
        self.assertEqual(getattr(rt, "importer", None) is None, "Historical trade import" in r)
        if "Simulated counterparty settlement fails" in r:
            self.assertEqual("unfunded payables are already marked failed and retried automatically; the fail you trigger yourself, and its resolution, "
                             "are not in this version yet", r["Simulated counterparty settlement fails"]["wording"])
        # every probe present: every PLANNED row of this build is gone and forced liquidation takes the margin-call wording
        saved = {a: getattr(rt, a, None) for a in ("collateral", "blotters", "swaps", "orders", "importer")}
        try:
            rt.collateral = _types.SimpleNamespace(margin_calls_rows=lambda *a: [], pledges_view=lambda *a: [])
            rt.blotters = _types.SimpleNamespace(execution_detail=lambda *a: {})
            rt.swaps = _types.SimpleNamespace(rfq_rows=lambda *a: [])
            rt.orders = _types.SimpleNamespace(gtd_supported=lambda services: True)
            rt.importer = _types.SimpleNamespace()
            r = rows()
            for item in list(planned) + ["Historical trade import"]:
                self.assertNotIn(item, r, item)
            self.assertEqual("positions are never sold for you; margin calls are recorded and met from cash / Treasury / PB headroom or stay open",
                             r["Forced liquidation"]["wording"])
            # a probe that names a non-callable attribute is "absent"
            rt.collateral = _types.SimpleNamespace(margin_calls_rows=None, pledges_view=lambda *a: [])
            r = rows()
            self.assertIn("Margin calls", r)
            self.assertEqual("positions are never sold for you; a margin shortfall raises a warning and an attention item", r["Forced liquidation"]["wording"])
        finally:
            for a, v in saved.items():
                setattr(rt, a, v)

    def test_c1_c2_optional_modules_and_pages_of_this_build(self):
        """C2: the FinSim1 build's optional modules are declared (bound, or missing while their package is not in the tree - tolerated,
        never an error) with their package ids for 501s; C1: the optional pages are stubbed with a 200 while missing, the Dashboard
        page is a required file."""
        h, _ = self.ok("GET", "/api/health")
        for attr, pkg in (("dashboard", "F1-P1"), ("blotters", "F1-P2"), ("navhistory", "F1-P3"), ("collateral", "F1-P6"), ("importer", "F1-P5")):
            self.assertEqual(attr, _server.OPTIONAL_MODULES[attr])
            self.assertEqual(pkg, _api.PACKAGES[attr])
            self.assertIn(h["modules"].get(attr), ("bound", "missing"), attr)
            self.assertTrue(hasattr(self.rt, attr), attr)
        static = os.path.join(config.package_dir(), "static")
        for rel in ("pages/pending.js", "pages/completed.js", "pages/accounts.js", "pages/cash_collateral.js", "pages/derivatives.js", "components/imports.js"):
            self.assertIn(rel, _api.PLANNED_STATIC)
            status, body, headers = self.get("/static/" + rel)
            self.assertEqual(200, status, rel)
            self.assertTrue(headers["Content-Type"].startswith(("application/javascript", "text/javascript")), rel)   # the stub / the real file (mimetypes)
            if not os.path.isfile(os.path.join(static, *rel.split("/"))):
                self.assertIn(b"not built yet", body)
        self.assertNotIn("pages/dashboard.js", _api.PLANNED_STATIC)
        status, body, _ = self.get("/static/pages/dashboard.js")
        self.assertEqual(200, status)
        self.assertIn(b"T.pages.dashboard = ", body)
        status, body, _ = self.get("/static/pages/news.js")
        self.assertEqual(404, status)

    def test_jobs_view_names_every_schedule(self):
        """V104R2-OTC-07: GET /api/jobs is the lifecycle scheduler's own view (status()): every job with its kind, its schedule in words
        and its phase; the clock-time jobs carry their next run time.  At Tuesday 2026-10-20 14:00 ET: MARKS_RECORD at 16:20, SWAP_VM at
        17:05, COLLATERAL_MARK at 17:10, SNAPSHOT at 17:45 (the setting) the same day, SETTLE_DUE / ACCRUE_DAILY at 00:30 and HOUSEKEEPING
        at 00:15 the next day, INTEGRITY_FULL at 14:05; a job's recorded failures are counted with the last error."""
        from finsim.terminal.clock import epoch_of
        from datetime import date as _date
        if self.rt.lifecycle is None:
            self.skipTest("no lifecycle scheduler in this tree")
        with self.rt.db.writer() as tx:
            tx.x("INSERT INTO job_failures(job, key, failures, last_error, last_ts) VALUES ('SETTLE_DUE', '2026-10-20', 2, 'busy (test)', ?)", (self.rt.clock.epoch(),))
        j, _ = self.ok("GET", "/api/jobs")
        rows = {r["name"]: r for r in j["jobs"]}
        self.assertGreaterEqual(len(rows), 10)
        for name, r in rows.items():
            self.assertTrue(r.get("when"), name)                                      # the schedule in words, every job
            self.assertEqual(r["when"], r["schedule"], name)
            self.assertIn("phase", r, name)
        d0, d1 = _date(2026, 10, 20), _date(2026, 10, 21)
        expect = {"MARKS_RECORD": epoch_of(d0, "16:20"), "SWAP_VM": epoch_of(d0, "17:05"), "COLLATERAL_MARK": epoch_of(d0, "17:10"),
                  "SNAPSHOT": epoch_of(d0, "17:45"), "SETTLE_DUE": epoch_of(d1, "00:30"), "ACCRUE_DAILY": epoch_of(d1, "00:30"),
                  "HOUSEKEEPING": epoch_of(d1, "00:15"), "INTEGRITY_FULL": epoch_of(d0, "14:05")}
        for name, ts in expect.items():
            if name in rows:
                self.assertEqual((ts, ts), (rows[name]["next_due_ts"], rows[name]["next_due"]), name)
        self.assertEqual((True, 1, "busy (test)"), (rows["SETTLE_DUE"]["failing"], rows["SETTLE_DUE"]["failures"], rows["SETTLE_DUE"]["last_error"]))
        self.assertEqual((False, 0, None), (rows["SNAPSHOT"]["failing"], rows["SNAPSHOT"]["failures"], rows["SNAPSHOT"]["last_error"]))
        no_clock = [n for n, r in rows.items() if r["next_due_ts"] is None]
        for n in no_clock:                                                            # a job without a clock time keeps its words
            self.assertEqual(rows[n]["when"], rows[n]["next_due"], n)

    def test_performance_and_risk_routes(self):
        p, _ = self.ok("GET", "/api/portfolio/performance?scope=ALL&book=T")
        self.assertEqual("insufficient history", p["status"])                    # no daily snapshot yet: declared, never invented
        r, _ = self.ok("GET", "/api/risk?scope=ALL&book=T")
        self.assertEqual("T1:ALL", r["scope"])
        self.assertEqual("10000000.00", r["nav"])
        c, _ = self.ok("GET", "/api/compliance?scope=ALL&book=T")
        self.assertIsInstance(c, list)


# ===== the owner's keyboard keys (2026-10-21 17:10; docs/KEYBINDS.md) ==================================================================
class Keybinds(ApiBase):
    #: a slice of static/keys.js's defaults, as the client sends them with every save (keybind_defaults)
    DEFAULTS = {"nav.us_exchange": "F1", "nav.debt": "F2", "page.dashboard": "F12", "book.next": None, "ticket.buy": "B", "grid.buy": "B",
                "ticket.clear": "Escape", "dialog.close": "Escape"}

    def kb(self, n, binds, defaults=True):
        body = {"request_id": f"ui:api-kb-{n:04d}", "keybinds": binds}
        if defaults:
            body["keybind_defaults"] = self.DEFAULTS
        return self.handle("PATCH", "/api/settings", body)

    def refused(self, n, binds, **kw):
        status, out, _ = self.kb(n, binds, **kw)
        self.assertEqual((400, "SETTING_VALUE", "keyboard keys"), (status, out["error"]["code"], out["error"]["details"].get("field")), out)
        return out["error"]

    def test_keybinds_checked_saved_journaled_and_kept(self):
        # F1 is US Exchange's by default: giving it to Debt alone would put one key on two actions -> refused, nothing stored
        err = self.refused(1, {"nav.debt": "F1"})
        self.assertEqual(("F1", ["nav.debt", "nav.us_exchange"]), (err["details"]["key"], err["details"]["actions"]))
        s, _ = self.ok("GET", "/api/settings")
        self.assertNotIn("keybinds", s)
        # the swap in one save; only the owner's changes are stored (the defaults live in keys.js)
        status, s, _ = self.kb(2, {"nav.debt": "F1", "nav.us_exchange": "F2"})
        self.assertEqual((200, {"nav.debt": "F1", "nav.us_exchange": "F2"}), (status, s["keybinds"]))
        # null = no key; an unbound action may take a free key; ticket and table may share B, a global action may not
        status, s, _ = self.kb(3, {"page.dashboard": None, "book.next": "Alt+B"})
        self.assertEqual((200, None, "Alt+B"), (status, s["keybinds"]["page.dashboard"], s["keybinds"]["book.next"]))
        err = self.refused(4, {"book.next": "B"})
        self.assertEqual("B", err["details"]["key"])
        # one canonical form (Ctrl+Alt+Shift+Key), browser-reserved keys and malformed action names are refused with the field named
        for n, bad in enumerate(("shift+f1", "Shift+Ctrl+F1", "Shift+?", "Ctrl+", "F25", "Esc", "b", 7), start=5):
            self.refused(n, {"book.next": bad})
        self.assertIn("closes the tab", self.refused(20, {"book.next": "Ctrl+W"})["message"])
        self.assertIn("Windows closes the window", self.refused(21, {"book.next": "Alt+F4"})["message"])
        self.refused(22, {"Nav.Debt": "F3"})
        self.assertIn("not an action of this build", self.refused(23, {"nav.nope": "F3"})["message"])
        self.assertIn("keybind_defaults", self.refused(24, {"book.next": "F2"}, defaults=False)["message"])   # KB-R1-08: never checked without the defaults
        # KB-R1-07: a trailing newline is not the canonical form (Python '$' matches before a final newline: fullmatch)
        self.refused(30, {"page.dashboard": "F1\n"})
        self.refused(31, {"nav.debt\n": "F3"})
        # KB-R1-05: Ctrl+F4 closes the tab, Ctrl+Page Up / Down switch tabs, Ctrl+Esc is the Windows Start menu
        self.assertIn("closes the tab", self.refused(32, {"book.next": "Ctrl+F4"})["message"])
        self.assertIn("switches tabs", self.refused(33, {"book.next": "Ctrl+PageDown"})["message"])
        self.assertIn("Start menu", self.refused(34, {"book.next": "Ctrl+Escape"})["message"])
        # an override equal to its default is dropped (Reset one)
        status, s, _ = self.kb(25, {"page.dashboard": "F12"})
        self.assertEqual((200, {"nav.debt": "F1", "nav.us_exchange": "F2", "book.next": "Alt+B"}), (status, s["keybinds"]))
        with self.rt.db.reader() as conn:                                          # journaled like every setting: before / after
            ev = [json.loads(r[0]) for r in conn.execute("SELECT payload_json FROM events WHERE type='SETTINGS_CHANGED' AND ref_id='keybinds' ORDER BY id").fetchall()]
        self.assertEqual(3, len(ev))
        self.assertEqual(({"nav.debt": "F1", "nav.us_exchange": "F2", "page.dashboard": None, "book.next": "Alt+B"}, {"nav.debt": "F1", "nav.us_exchange": "F2", "book.next": "Alt+B"}),
                         (ev[-1]["before"]["keybinds"], ev[-1]["after"]["keybinds"]))
        # a lifetime reset keeps them; so does a restart of the server on the same database
        if "book.reset_lifetime" in self.rt.bus.kinds():
            self.ok("POST", "/api/books/T/reset", {"request_id": "ui:api-kb-reset-0001", "confirm": "RESET", "starting_capital": "10000000.00"})
        self.rt.close()
        self.rt = build(self.tmp)
        s, _ = self.ok("GET", "/api/settings")
        self.assertEqual({"nav.debt": "F1", "nav.us_exchange": "F2", "book.next": "Alt+B"}, s["keybinds"])
        st, _ = self.ok("GET", "/api/state")
        self.assertEqual(s["keybinds"], st["settings"]["keybinds"])


# ===== the codec =========================================================================================================================
class Codec(unittest.TestCase):
    def test_cents_epochs_decimals(self):
        out = _api.encode({"nav_c": 1_000_000_000, "nav0_c": 5, "fee_c": None, "sweep_in": "1.00", "sweep_in_c": 100, "qp_threshold_c": {"FUND": 2_500_000_000, "X": None},
                           "px": Decimal("339.50"), "created_ts": 1792519200, "ok": True, "n_c": True, "lst": [{"gross_c": 3395000}], "d": {"a": Decimal("1")}})
        self.assertEqual("10000000.00", out["nav"])
        self.assertEqual("0.05", out["nav_0"])
        self.assertIsNone(out["fee"])
        self.assertEqual(("1.00", 100), (out["sweep_in"], out["sweep_in_c"]))       # the stripped name exists: the _c key is kept, nothing overwritten
        self.assertEqual({"FUND": "25000000.00", "X": None}, out["qp_threshold"])
        self.assertEqual("339.50", out["px"])
        self.assertEqual("2026-10-20 14:00:00 ET", out["created_ny"])             # 1792519200 = 2026-10-20 18:00 UTC = 14:00 EDT
        self.assertEqual(1792519200, out["created_ts"])
        self.assertTrue(out["n_c"])                                              # booleans are never money
        self.assertEqual("33950.00", out["lst"][0]["gross"])
        self.assertEqual("1", out["d"]["a"])


# ===== one real socket ===================================================================================================================
class HttpClient(ApiBase):
    def test_http_client_on_a_temp_port(self):
        httpd = _server.TerminalServer(("127.0.0.1", 0), _server.make_handler(self.rt))
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
        t.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request("GET", "/api/health")
            r = conn.getresponse()
            data = json.loads(r.read())
            self.assertEqual(200, r.status)
            self.assertEqual("finsim-terminal", data["app"])
            self.assertEqual("no-store", r.getheader("Cache-Control"))
            conn.request("POST", "/api/orders", body=json.dumps({"account_id": USX, "instrument_id": "AAPL", "side": "BUY", "qty": 1, "order_type": "MARKET", "tif": "DAY"}),
                         headers={"Content-Type": "application/json"})
            r = conn.getresponse()
            data = json.loads(r.read())
            self.assertEqual((400, "BAD_REQUEST_ID"), (r.status, data["error"]["code"]))
            conn.request("GET", "/")
            r = conn.getresponse()
            page = r.read()
            self.assertEqual(200, r.status)
            self.assertTrue(r.getheader("Content-Type").startswith("text/html"))
            self.assertIn(b"<html", page[:200].lower())
            conn.request("GET", "/static/../terminal.db")
            r = conn.getresponse()
            r.read()
            self.assertEqual(404, r.status)
            conn.request("POST", "/api/shutdown", body="{}", headers={"Content-Type": "application/json"})
            r = conn.getresponse()
            self.assertEqual(200, r.status)
            self.assertEqual({"shutting_down": True}, json.loads(r.read()))
            self.assertTrue(self.rt.stop_event.is_set())
            conn.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
            t.join(5)


if __name__ == "__main__":
    unittest.main()
