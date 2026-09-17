import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from tests.helpers import D  # noqa: F401  (path setup)
from finsim.api.server import Router, make_handler
from finsim.api.service import Service
from finsim.store import EventStore


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = Service(EventStore(":memory:"))
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Router(cls.service)))
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def call(self, method, path, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method, data=json.dumps(body).encode() if body else None,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_end_to_end_over_http(self):
        st, r = self.call("POST", "/api/worlds", {"name": "http", "seed": 5, "start_date": "2026-02-02", "capital": 10_000_000})
        self.assertEqual(st, 200)
        wid, pid = r["world_id"], r["portfolio_id"]
        st, info = self.call("GET", f"/api/worlds/{wid}")
        self.assertEqual(info["current_date"], "2026-02-02")
        st, secs = self.call("GET", f"/api/worlds/{wid}/securities")
        self.assertGreater(len(secs), 25)
        st, o = self.call("POST", f"/api/worlds/{wid}/portfolios/{pid}/orders", {"security_id": "ORCL", "side": "BUY", "quantity": 10000})
        self.assertEqual(st, 200)
        self.assertEqual(o["order"]["status"], "WORKING")
        oid = o["order"]["id"]
        st, adv = self.call("POST", f"/api/worlds/{wid}/advance", {"days": 2})
        self.assertEqual(len(adv["closed_days"]), 2)
        st, bad = self.call("POST", f"/api/worlds/{wid}/portfolios/{pid}/orders", {"security_id": "ORCL", "side": "SELL", "quantity": 99999})
        self.assertEqual(st, 400)
        self.assertIn("exceeds", bad["error"])
        st, o = self.call("GET", f"/api/worlds/{wid}/portfolios/{pid}/orders/{oid}")
        self.assertEqual(o["order"]["status"], "FILLED")
        tid = o["trades"][0]["id"]
        st, d = self.call("GET", f"/api/worlds/{wid}/portfolios/{pid}/dashboard")
        self.assertAlmostEqual(d["nav"], d["ledger_nav"], places=2)
        self.assertEqual(d["positions"][0]["security_id"], "ORCL")
        self.assertEqual(d["positions"][0]["settled_quantity"], 10000)
        st, t = self.call("GET", f"/api/worlds/{wid}/portfolios/{pid}/trades/{tid}")
        self.assertEqual(t["trade"]["status"], "SETTLED")
        self.assertEqual(t["settlement"]["status"], "SETTLED")
        self.assertGreaterEqual(len(t["ledger_entries"]), 2)
        st, pos = self.call("GET", f"/api/worlds/{wid}/portfolios/{pid}/positions/ORCL")
        self.assertEqual(len(pos["lots"]), 1)
        st, led = self.call("GET", f"/api/worlds/{wid}/portfolios/{pid}/ledger")
        self.assertTrue(led["trial_balance"]["balanced"])
        st, ex = self.call("GET", f"/api/worlds/{wid}/portfolios/{pid}/pnl-explain")
        self.assertTrue(ex["reconciles"])
        st, ev = self.call("GET", f"/api/worlds/{wid}/events?type=TRADE_EXECUTED")
        self.assertEqual(ev["total"], 1)
        st, one = self.call("GET", f"/api/worlds/{wid}/events/{ev['events'][0]['id']}")
        self.assertEqual(one["ancestors"][0]["type"], "DAY_STARTED")
        st, br = self.call("GET", f"/api/worlds/{wid}/portfolios/{pid}/briefing")
        self.assertEqual(br["date"], adv["current_date"])
        self.assertIn("attention", br)
        st, cm = self.call("GET", f"/api/worlds/{wid}/commodities")
        self.assertTrue(any(c["code"] == "CL" for c in cm))
        st, cd = self.call("GET", f"/api/worlds/{wid}/commodities/CL")
        self.assertGreater(len(cd["contracts"]), 5)
        st, car = self.call("GET", f"/api/worlds/{wid}/portfolios/{pid}/career")
        self.assertEqual(car["job"]["key"], "SANDBOX")
        st, nf = self.call("GET", f"/api/worlds/{wid}/events/EV-999999")
        self.assertEqual(st, 404)
        st, yc = self.call("GET", f"/api/worlds/{wid}/yield-curve")
        self.assertEqual(len(yc["rates"]), 10)
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/") as r:
            self.assertIn(b"FINSIM", r.read())
        # world persists across service instances (event store replay)
        svc2 = Service(self.service.store)
        w2 = svc2.world(wid)
        self.assertEqual(w2.ledgers[pid].nav(), self.service.world(wid).ledgers[pid].nav())


if __name__ == "__main__":
    unittest.main()


class OrderPreviewTest(unittest.TestCase):
    def test_preview_prices_each_instrument_and_converts_an_amount_to_quantity(self):
        from finsim.api.service import Service
        from finsim.store import EventStore
        from finsim.money import D
        s = Service(EventStore(":memory:"), strict_replay=True)
        r = s.create_world("p", 42, "2026-01-05", capital=10_000_000, job="SANDBOX", clock_mode="SANDBOX")
        wid, pid = r["world_id"], r["portfolio_id"]
        w = s.world(wid)
        eq = s.order_preview(wid, pid, {"security_id": "NVDA", "side": "BUY", "quantity": 1000})
        self.assertEqual(eq["kind"], "cash")
        self.assertEqual(D(str(eq["gross"])), (D("1000") * D(str(eq["price"]))).quantize(D("0.01")))
        self.assertEqual(D(str(eq["cash_needed"])), D(str(eq["gross"])) + D(str(eq["commission"])))
        self.assertEqual(eq["price_source"], "ask")
        sell = s.order_preview(wid, pid, {"security_id": "NVDA", "side": "SELL", "quantity": 1000})
        self.assertLess(sell["cash_needed"], 0, "a sale brings cash in")
        self.assertEqual(sell["price_source"], "bid")
        by_amt = s.order_preview(wid, pid, {"security_id": "NVDA", "side": "BUY", "amount": 250_000})
        self.assertLessEqual(by_amt["gross"], 250_000)
        self.assertGreater(by_amt["gross"], 250_000 - 2 * float(by_amt["price"]))
        bond = s.order_preview(wid, pid, {"security_id": "UST-10Y", "side": "BUY", "amount": 1_000_000})
        self.assertEqual(bond["kind"], "bond")
        self.assertEqual(int(bond["quantity"]) % 1000, 0, "bonds trade in 1,000 face")
        self.assertLessEqual(bond["gross"] + bond["accrued_interest"], 1_000_000)
        self.assertGreater(bond["accrued_interest"], 0)
        fut = next(x for x in w.securities.values() if x.is_future and x.underlying == "CL" and not x.expired)
        f = s.order_preview(wid, pid, {"security_id": fut.id, "side": "SELL", "quantity": 3})
        self.assertEqual(f["kind"], "future")
        self.assertGreater(f["initial_margin"], 0)
        self.assertEqual(D(str(f["cash_needed"])), D(str(f["initial_margin"])) + D(str(f["commission"])))
        opt = next(x for x in w.securities.values() if x.is_option and x.underlying == "NVDA")
        o = s.order_preview(wid, pid, {"security_id": opt.id, "side": "BUY", "quantity": 2})
        self.assertEqual(o["kind"], "option")
        self.assertEqual(o["multiplier"], 100.0)
        lim = s.order_preview(wid, pid, {"security_id": "NVDA", "side": "BUY", "quantity": 10, "limit_price": 100})
        self.assertEqual((lim["price_source"], float(lim["price"])), ("limit", 100.0))
        from finsim.api.service import NotFound
        with self.assertRaises(NotFound):
            s.order_preview(wid, pid, {"security_id": "NOPE", "side": "BUY", "quantity": 1})
