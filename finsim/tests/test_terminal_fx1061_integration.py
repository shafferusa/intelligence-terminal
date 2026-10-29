"""FX_CCS_PLAN rev 2, integration step 2 (release 1.0.6.1, the integrator): the owner's booking program against the REAL terminal API.

``otcplan`` (package P-D) drives a real ``build_runtime`` (every module bound as ``serve`` binds it: Fund Book F1 with its USD
25,000,000.00, the Goldman Sachs PB opened, Thu 2026-10-29 10:00 ET, the worked example's curves - ``fxcurves.fixture_curves``, the
2026-10-27 rows - and its prints EURUSD 1.1158, GBPUSD 1.3323, EURGBP 0.83725) through ``Router.handle``: the same routes and the same
JSON codec as HTTP (cents as decimal strings without ``_c``), with the committed plan file ``docs\\plans\\OWNER_FX_TRADES_2026-10-28.plan.json``.
For each trade: the preview, the quote request, the simulated dealer's quote (RFQ_DEALER, ticked by the injected sleep), the
independent re-derivation of every price from the SERVED pricing record with exact equality (10.2, K9) and the accept.  This is the
contract between package P-B's and P-C's records, the API codec and P-D's validator: a shape mismatch there would decline the owner's
quotes on the live run.  Then a second run books nothing, and ``otcplan check`` passes on the database.

Hand-worked (FX_CCS_PLAN 3.2 / 3.3, the same world):
- Trade A (XCCY float / float, pay EUR ESTR + spread / receive USD SOFR flat, EUR 2,000,000.00, 24 months, inception conversion): X0 =
  1.1158; N_USD = 2,000,000.00 x 1.1158 = 2,231,600.00; par -0.9214 bp + the 1.0 bp half-width = +0.0786 -> +0.1 bp (up on the 0.1 bp
  grid); mark0 = PV(0) -403.98 - 438.43 x 0.1 = -447.82; IM 2 % x 2,231,600.00 = 44,632.00; the conversion SELL EUR 2,000,000.00 at
  1.1158 - 0.00002 = 1.11578 -> USD 2,231,560.00 (spread 40.00), value 2026-11-02.
- Trade B (SELL EUR 2,000,000.00 / BUY GBP 2Y): S = 1.1158 / 1.3323 = 0.837499; F = 0.837499 x 0.9413073853 / 0.9094909816 = 0.866797;
  K = 0.866797 - 0.000173 = 0.866624; GBP 2,000,000 x 0.866624 = 1,733,248.00; IM 6 % x 2,231,600.00 = 133,896.00; mark0 -419.25.
- IM sweeps from Treasury: 44,632.00 + 133,896.00 = 178,528.00 (the PB account holds no USD).
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from decimal import Decimal
from urllib.parse import urlencode

from finsim.terminal import config, fxcurves, otcplan
from finsim.terminal.commands import Command
from finsim.terminal.quotes import QuoteRecord
from tests.terminal_helpers import assert_integrity, bal

HERE = os.path.dirname(os.path.abspath(__file__))
PLAN_PATH = os.path.join(os.path.dirname(HERE), "docs", "plans", "OWNER_FX_TRADES_2026-10-28.plan.json")
PB = "F1:PB_GS"
PRINTS = (("EURUSD", "1.1158"), ("GBPUSD", "1.3323"), ("EURGBP", "0.83725"))


class RouterHttp:
    """``otcplan.Http``'s interface on ``Router.handle`` (no sockets): every body goes through a JSON round trip, as on the wire."""

    def __init__(self, rt):
        self.rt = rt
        self.calls = []

    def get(self, path, params=None):
        q = ("?" + urlencode(params)) if params else ""
        st, body, _h = self.rt.router.handle("GET", path + q, None, None, "127.0.0.1", None)
        self.calls.append(("GET", path))
        return st, json.loads(json.dumps(body))

    def post(self, path, body):
        st, out, _h = self.rt.router.handle("POST", path, None, json.loads(json.dumps(body)), "127.0.0.1", None)
        self.calls.append(("POST", path))
        return st, json.loads(json.dumps(out))


class OtcplanEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_terminal_api import build as api_build
        cls.tmp = tempfile.mkdtemp(prefix="fst-fx1061-e2e-")
        rt = cls.rt = api_build(cls.tmp, at="2026-10-29T10:00:00-04:00", books=[config.BOOKS[1]])
        fxcurves.fixture_curves(rt, persist=True)
        cls.put_prints()
        r = rt.bus.execute(Command("account.open_pb", {"lifetime_id": "F1", "broker": "GS"}, "ui:fx1061-open-pb", "ui"))
        assert r.ok, r.body
        cls.tre0 = bal(rt, "F1:TREASURY", "1010")
        cls.plan, cls.sha = otcplan.load_plan(PLAN_PATH)
        cls.before = otcplan.snapshot(rt.db_path)
        cls.http = RouterHttp(rt)
        cls.report = cls.run_plan("20261029T100000")
        cls.second = cls.run_plan("20261029T101500")

    @classmethod
    def tearDownClass(cls):
        cls.rt.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def put_prints(cls):
        rt = cls.rt
        now = rt.clock.epoch()
        for iid, px in PRINTS:
            rt.cache.put(QuoteRecord(iid, "yahoo", "test print", "direct", last=Decimal(px), quote_time=now, fetched_at=now, session_group="FX_SPOT"), at=now)

    @classmethod
    def sleep(cls, s):
        """The injected sleep: the clock moves on, the prints stay at their last value (re-put at the new time) and the simulated dealer
        answers on its tick, as the server's lifecycle thread would."""
        rt = cls.rt
        rt.clock.set(rt.clock.epoch() + int(max(1, round(float(s)))))
        cls.put_prints()
        rt.lifecycle.tick(only="RFQ_DEALER")

    @classmethod
    def run_plan(cls, run_id):
        runner = otcplan.Runner(cls.plan, cls.sha, cls.http, os.path.join(cls.tmp, "report-" + run_id), sleep=cls.sleep, out=lambda s: None)
        runner.run_id = run_id
        trades = [runner.trade(t) for t in cls.plan["trades"]]
        report = {"plan_id": cls.plan["plan_id"], "plan_sha256": cls.sha, "run_id": run_id, "account_id": cls.plan["account_id"], "trades": trades,
                  "views": runner.views(), "plan": cls.plan}
        report["exit_code"] = 0 if all(t["status"] in ("EXECUTED", "ALREADY_BOOKED") for t in trades) else 1
        return json.loads(json.dumps(report, default=str))

    def trade(self, report, key):
        return next(t for t in report["trades"] if t["key"] == key)

    def test_both_trades_execute_through_the_rfq_path_with_the_quote_checks_passing(self):
        a, b = self.trade(self.report, "A"), self.trade(self.report, "B")
        self.assertEqual(("EXECUTED", "EXECUTED"), (a["status"], b["status"]), (a.get("reason"), b.get("reason"), a.get("attempts"), b.get("attempts")))
        # the accepted attempt's validation passed: every check of 10.2, the exact re-derivation from the served record included
        self.assertEqual(["passed"], [x.get("validation") for x in a["attempts"] if x.get("validation")][-1:])
        self.assertEqual(["passed"], [x.get("validation") for x in b["attempts"] if x.get("validation")][-1:])
        ra, rb = a["reprice"], b["reprice"]
        # Trade A: X0 1.1158, N_USD 2,231,600.00, dealt +0.1 bp, mark0 -447.82, IM 44,632.00, the conversion at 1.11578 for USD 2,231,560.00
        self.assertEqual(("1.1158", 223160000, "0.1", -44782, 4463200, "1.11578", 223156000, 4000),
                         (ra["X0"], ra["n_usd_c"], ra["dealt_bp"], ra["mark0_c"], ra["im_c"], ra["conversion"]["rate"], ra["conversion"]["usd_c"],
                          ra["conversion"]["spread_cost_c"]))
        # Trade B: F 0.866797, K 0.866624, GBP 1,733,248.00, IM 133,896.00, mark0 -419.25, value date 2028-11-02
        self.assertEqual(("0.866797", "0.866624", 173324800, 13389600, -41925, "2028-11-02"),
                         (rb["F"], rb["K"], rb["quote_amount_c"], rb["im_c"], rb["mark0_c"], rb["value_date"]))

    def test_the_contracts_carry_the_references_and_the_quoted_terms(self):
        a, b = self.trade(self.report, "A"), self.trade(self.report, "B")
        with self.rt.db.reader() as conn:
            rows = {r["client_ref"]: dict(r) for r in conn.execute("SELECT * FROM otc_contracts WHERE client_ref IS NOT NULL")}
        self.assertEqual({"OWNER-FX-2026-10-28-A", "OWNER-FX-2026-10-28-B"}, set(rows))
        ca, cb = rows["OWNER-FX-2026-10-28-A"], rows["OWNER-FX-2026-10-28-B"]
        self.assertEqual((a["contract_id"], b["contract_id"]), (ca["contract_id"], cb["contract_id"]))
        ta, tb = json.loads(ca["terms_json"]), json.loads(cb["terms_json"])
        self.assertEqual(("XCCY", "FLOAT_FLOAT", "1.1158", "2026-11-02", "2028-11-06", 4463200, -44782),
                         (ca["kind"], ta["variant"], ta["deal_rate"], ca["start_date"], ca["maturity_date"], ca["im_c"], ca["mark_c"]))
        self.assertEqual(("FX_FORWARD", "0.866624", 173324800, "2028-11-02", 13389600, -41925),
                         (cb["kind"], tb["strike"], tb["buy_c"], cb["maturity_date"], cb["im_c"], cb["mark_c"]))
        # the conversion is linked both ways and its two settlement rows wait for the START (tagged xccy_start)
        conv = ta["start_conversion"]["deal_id"]
        self.assertEqual(a.get("conversion_deal_id") or conv, conv)
        with self.rt.db.reader() as conn:
            legs = sorted((r["currency"], r["amount_c"], r["ref_type"], r["ref_id"], r["due_date"])
                          for r in conn.execute("SELECT * FROM settlements WHERE fx_deal_id=?", (conv,)))
        self.assertEqual([("EUR", -200000000, "xccy_start", ca["contract_id"], "2026-11-02"), ("USD", 223156000, "xccy_start", ca["contract_id"], "2026-11-02")],
                         legs)
        # the IM came from Treasury: 44,632.00 + 133,896.00 = 178,528.00
        self.assertEqual(self.tre0 - 17852800, bal(self.rt, "F1:TREASURY", "1010"))
        assert_integrity(self, self.rt, "full")

    def test_a_second_run_books_nothing(self):
        self.assertEqual(["ALREADY_BOOKED", "ALREADY_BOOKED"], [t["status"] for t in self.second["trades"]])
        with self.rt.db.reader() as conn:
            self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM otc_contracts WHERE client_ref IS NOT NULL").fetchone()[0])
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM commands WHERE request_id LIKE ?", (f"{self.plan['plan_id']}:20261029T101500:%",)).fetchone()[0])

    def test_the_checker_passes(self):
        chk = otcplan.Checker(self.rt.db_path, json.loads(json.dumps(self.before, default=str)), self.report, self.second)
        try:
            res = chk.result()
        finally:
            chk.close()
        failed = {k: v.get("evidence") for k, v in res["items"].items() if not v.get("ok")}
        self.assertEqual({}, failed)
        self.assertTrue(res["ok"])

    def test_settle_due_leaves_the_conversion_rows_to_the_start(self):
        """P-D-R1 (FX_CCS_PLAN D7 / K4): on the start date 2026-11-02 at 00:31 ET (after SETTLE_DUE's 00:30 cut-in, before the swap's 00:45
        START) the conversion's two rows (BUY USD 2,231,560.00 / SELL EUR 2,000,000.00, tagged xccy_start) are due, but SETTLE_DUE does not
        even list them: the START settles them PvP with the exchange (no SKIPPED key, no WARN retried every 5 minutes)."""
        from finsim.terminal.clock import FakeClock
        from finsim.terminal.lifecycle_jobs import SettleDue
        a = self.trade(self.report, "A")
        with self.rt.db.reader() as conn:
            linked = [r["settle_id"] for r in conn.execute("SELECT settle_id FROM settlements WHERE ref_type='xccy_start' AND ref_id=? AND status='PENDING'",
                                                           (a["contract_id"],))]
            keys = SettleDue().due(conn, FakeClock("2026-11-02T00:31:00-05:00"), self.rt.cal, self.rt.sessions)
            from finsim.terminal import fx
            held = fx.held_settle_ids(conn, linked)              # due 11-02, PENDING, the contract OPEN and its START not run: held
        self.assertEqual((2, set(linked)), (len(linked), held))
        self.assertEqual([], [k for k in keys if k in linked])

    def test_the_funding_plan_says_each_obligation_once(self):
        """The served settlement-funding plan: the first EUR purchase (Tue 2027-02-02, value Thu 02-04) funds the swap's coupon 1 - projected,
        the index decides - and the words say "the index decides" once (the coupon's own words already carry it)."""
        st, body = self.http.get("/api/otc/funding_plan", {"account_id": PB})
        self.assertEqual(200, st, body)
        first = body["purchases"][0]
        self.assertEqual(("2027-02-04", "2027-02-02", "EUR"), (first["value_date"], first["funding_trade_date"], first["ccy"]))
        self.assertTrue(all(w.count("index decides") <= 1 for p in body["purchases"] for w in p["what"]), [p["what"] for p in body["purchases"]])


if __name__ == "__main__":
    unittest.main()
