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
        st, o = self.call("POST", f"/api/worlds/{wid}/portfolios/{pid}/orders", {"security_id": "CLDX", "side": "BUY", "quantity": 10000})
        self.assertEqual(st, 200)
        self.assertEqual(o["order"]["status"], "WORKING")
        oid = o["order"]["id"]
        st, adv = self.call("POST", f"/api/worlds/{wid}/advance", {"days": 2})
        self.assertEqual(len(adv["closed_days"]), 2)
        st, bad = self.call("POST", f"/api/worlds/{wid}/portfolios/{pid}/orders", {"security_id": "CLDX", "side": "SELL", "quantity": 99999})
        self.assertEqual(st, 400)
        self.assertIn("exceeds", bad["error"])
        st, o = self.call("GET", f"/api/worlds/{wid}/portfolios/{pid}/orders/{oid}")
        self.assertEqual(o["order"]["status"], "FILLED")
        tid = o["trades"][0]["id"]
        st, d = self.call("GET", f"/api/worlds/{wid}/portfolios/{pid}/dashboard")
        self.assertAlmostEqual(d["nav"], d["ledger_nav"], places=2)
        self.assertEqual(d["positions"][0]["security_id"], "CLDX")
        self.assertEqual(d["positions"][0]["settled_quantity"], 10000)
        st, t = self.call("GET", f"/api/worlds/{wid}/portfolios/{pid}/trades/{tid}")
        self.assertEqual(t["trade"]["status"], "SETTLED")
        self.assertEqual(t["settlement"]["status"], "SETTLED")
        self.assertGreaterEqual(len(t["ledger_entries"]), 2)
        st, pos = self.call("GET", f"/api/worlds/{wid}/portfolios/{pid}/positions/CLDX")
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
