"""FINSIM1_PLAN P1 task 4 (UI-05, PORTFOLIO-07, OMS-16): ``GET /api/dashboard`` and ``GET /api/dashboard/counts`` (dashboard.py).

Every expected figure is worked by hand below; nothing touches the network (FakeProvider, FakeClock, a temp home; FINSIM_HOME /
FINSIM_TERMINAL_DB point at the temp folder).

The world (the API tests' world): the Test Book ``T1`` - USD 10,000,000.00 in T1:TREASURY - at 2026-10-20 14:00 ET (Tuesday, US_EQUITY
open).  A MARKET BUY 100 AAPL in T1:US_EXCHANGE fills at the observed last 339.50: gross 33,950.00, fee max(100 x 0.005, 1.00) = 1.00,
payable 33,951.00 due T+1, swept in from Treasury at the fill (Treasury 9,966,049.00, US Exchange settled 33,951.00 against the
33,951.00 payable).  AAPL then prints 349.50: unrealised (349.50 - 339.50) x 100 = 1,000.00.
  NAV = Treasury 9,966,049.00 + US Exchange (33,951.00 - 33,951.00 + 34,950.00 = 34,950.00) = 10,000,999.00
  ITD P&L = 10,000,999.00 - 10,000,000.00 (capital) = 999.00 = unrealised 1,000.00 - fees 1.00 (realised, income, financing, FX 0)
  no daily snapshot yet: every period measures from inception (TODAY P&L 999.00 too); the TODAY return is not computable ("no daily
  snapshot before today yet"); MTD / YTD / ITD = the simple return 999.00 / 10,000,000.00 = 0.0000999 -> 0.000100 (6 dp, half-even)
  exposure (the Risk page's figures): long 34,950.00 = gross = net, short 0; gross / NAV = 34,950.00 / 10,000,999.00 = 0.0034946 -> 0.0035
  cash (USD): settled 9,966,049.00 + 33,951.00 = 10,000,000.00; payables 33,951.00; receivables 0; available 9,966,049.00 + 0.00
  (US Exchange: 33,951.00 settled - 33,951.00 payable = 0) = 9,966,049.00; nothing reserved, nothing restricted
  accounts: Treasury 9,966,049.00 (9,966,049.00 / 10,000,999.00 = 0.996505 -> 0.9965), US Exchange 34,950.00 (0.0035); they add up
  to the NAV

The risk warnings and compliance notices are counted in the background (dashboard.py): the first answer of a book says "counting"
and starts the count after its own work; the tests wait for that count (``recount_thread``) before reading the counts.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import types
import unittest
from decimal import Decimal

from tests.test_terminal_api import AAPL_LAST, TREASURY, USX, build, quote
from finsim.terminal import dashboard as _dashboard

SCOPE = "T1:ALL"
DASH = "/api/dashboard?book=T"


class DashboardBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-dash-")
        self.rt = build(self.tmp)
        quote(self.rt, "AAPL", AAPL_LAST)

    def tearDown(self):
        self.join()
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def join(self):
        """Wait for the background count of the risk warnings and compliance notices, if one runs."""
        th = _dashboard.recount_thread(self.rt, SCOPE)
        if th is not None:
            th.join(30)
            self.assertFalse(th.is_alive(), "the background count did not finish")

    def handle(self, method, path, body=None):
        return self.rt.router.handle(method, path, None, body, "127.0.0.1", None)

    def ok(self, method, path, body=None):
        status, out, headers = self.handle(method, path, body)
        self.assertEqual(200, status, f"{method} {path} -> {status} {out}")
        return out

    def counted(self):
        """The Dashboard once the background count of the book has landed (the first answer starts it)."""
        self.ok("GET", DASH)
        self.join()
        return self.ok("GET", DASH)

    def trade(self):
        req = {"account_id": USX, "instrument_id": "AAPL", "side": "BUY", "qty": 100, "order_type": "MARKET", "tif": "DAY", "request_id": "ui:dash-aapl-0001"}
        out = self.ok("POST", "/api/orders", req)
        self.assertEqual(("FILLED", "33951.00"), (out["order"]["status"], out["sweeps"][0]["amount"]))
        self.rt.clock.advance(60)                                              # 14:01 ET: the next quote poll is due
        quote(self.rt, "AAPL", "349.50")                                       # +10.00 on 100 shares: unrealised 1,000.00
        self.assertEqual(Decimal("349.50"), Decimal(str(self.rt.marks.snapshot().mark("AAPL").mark)))


class DashboardFigures(DashboardBase):
    def test_hand_worked_fund_figures(self):
        self.trade()
        first = self.ok("GET", DASH)
        # the first answer of the book: the risk warnings and compliance notices are being counted (never 0), within the budget
        self.assertEqual(("counting", 0.0), (first["timing"]["counts"], first["timing"]["counts_ms"]))
        self.assertEqual((None, None), (first["attention"]["warnings"], first["attention"]["compliance"]))
        self.assertIn(_dashboard.COUNTING_NOTE, first["attention"]["notes"])
        self.assertLessEqual(float(first["timing"]["own_ms"]), 300.0, first["timing"])
        self.join()
        d = self.ok("GET", DASH)
        self.assertEqual(("T1:ALL", ["T1"], "T", "Test Book"), (d["scope"], d["lifetime_ids"], d["book_code"], d["book_name"]))
        self.assertEqual(("10000000.00", "10000000.00", "T1"), (d["capital"]["starting"], d["capital"]["contributed"], d["capital"]["lifetime_id"]))
        self.assertEqual("10000999.00", d["nav"]["nav"])
        self.assertEqual({"TODAY": "999.00", "MTD": "999.00", "YTD": "999.00", "ITD": "999.00"}, {p: d["pnl"][p]["pnl"] for p in ("TODAY", "MTD", "YTD", "ITD")})
        # the ITD split: each row's signed effect; they add up to the ITD P&L (residual 0)
        eff = {r["key"]: r["effect"] for r in d["explain"]}
        self.assertEqual({"realised": "0.00", "income": "0.00", "financing": "0.00", "fees": "-1.00", "unrealised": "1000.00", "fx_result": "0.00"}, eff)
        self.assertEqual(Decimal("999.00"), sum(Decimal(v) for v in eff.values()))
        self.assertEqual("0.00", d["pnl"]["ITD"]["residual"])
        # returns: TODAY needs a snapshot; MTD / YTD / ITD = the simple return 0.000100 (no snapshot, no flows)
        ret = {p: d["pnl"][p]["return"] for p in ("TODAY", "MTD", "YTD", "ITD")}
        self.assertIsNone(ret["TODAY"]["pct"])
        self.assertEqual("no daily snapshot before today yet", ret["TODAY"]["reason"])
        self.assertEqual(("0.000100", "0.000100", "0.000100", "0.000100"), (ret["MTD"]["pct"], ret["YTD"]["pct"], ret["ITD"]["pct"], ret["ITD"]["simple_pct"]))
        # exposure = the Risk page's figures on the same marks
        x = d["exposure"]
        self.assertTrue(x["present"])
        self.assertEqual(("34950.00", "0.00", "34950.00", "34950.00"), (x["long"], x["short"], x["gross"], x["net"]))
        self.assertEqual(("0.0035", "0.0035", "0.00"), (x["gross_pct_nav"], x["leverage"], x["trs_notional"]))
        r = self.ok("GET", "/api/risk?scope=ALL&book=T")
        self.assertEqual((r["long"], r["short"], r["gross"], r["net"]), (x["long"], x["short"], x["gross"], x["net"]))
        # FINSIM1 P1b-R2 (1.0.6, on purpose): the risk module's public exposure summary is in the build, so the section says it came from there
        self.assertEqual(_dashboard.SOURCE_WORDS["exposure_summary"], d["sources"]["exposure"]["source"])
        # cash across the book's accounts (USD)
        c = d["cash"]
        self.assertTrue(c["present"])
        self.assertEqual(("10000000.00", "9966049.00", "0.00", "0.00", "0.00", "33951.00"),
                         (c["settled"], c["available"], c["reserved"], c["restricted"], c["receivable"], c["payable"]))
        # the accounts: NAV, share of the book, cash, counts; they add up to the book's NAV
        acc = {a["account_id"]: a for a in d["accounts"]}
        self.assertEqual(("9966049.00", "0.9965", "9966049.00", 0), (acc[TREASURY]["nav"], acc[TREASURY]["weight"], acc[TREASURY]["available"], acc[TREASURY]["working_orders"]))
        self.assertEqual(("34950.00", "0.0035", "33951.00", "0.00", "33951.00", 0, 1),
                         (acc[USX]["nav"], acc[USX]["weight"], acc[USX]["settled"], acc[USX]["available"], acc[USX]["payable"], acc[USX]["working_orders"], acc[USX]["pending_settlements"]))
        self.assertEqual(Decimal(d["nav"]["nav"]), sum(Decimal(a["nav"]) for a in d["accounts"]))
        self.assertEqual(Decimal(c["settled"]), sum(Decimal(a["settled"]) for a in d["accounts"]))
        # the sidebar's account rows show the same available / NAV figures
        side = {a["account_id"]: a for a in self.ok("GET", "/api/accounts?book=T")}
        for aid in (TREASURY, USX):
            self.assertEqual((side[aid]["nav"], side[aid]["available_usd"]), (acc[aid]["nav"], acc[aid]["available"]), aid)
        # once counted, the attention block is the attention summary of the book (the same counts as its own route, which counts live)
        s = self.ok("GET", "/api/attention/summary?book=T")
        self.assertEqual({k: v for k, v in s.items() if k != "as_of"}, {k: v for k, v in d["attention"].items() if k != "as_of"})
        self.assertEqual((0, 1), (d["attention"]["working_orders"], d["attention"]["pending_settlements"]))
        cnt = d["sources"]["attention"]["counts"]
        self.assertEqual(("kept", self.rt.clock.epoch(), self.rt.db.state_version(), None), (cnt["how"], cnt["counted_at"], cnt["state_version"], cnt["error"]))
        # every section names its source; the own work is timed against the 300 ms budget
        for k in ("marks", "nav", "pnl", "exposure", "cash", "attention", "returns", "margin", "collateral", "counters"):
            self.assertIn(k, d["sources"], k)
            self.assertIsInstance(d["sources"][k].get("ms"), (int, float), k)
        t = d["timing"]
        self.assertEqual((300, "kept", 0.0), (t["budget_ms"], t["counts"], t["counts_ms"]))
        self.assertLessEqual(float(t["own_ms"]), float(t["total_ms"]))
        self.assertLessEqual(float(t["own_ms"]), 300.0, t)                      # a small book: well inside the budget
        self.assertTrue(t["within_budget"])
        self.assertNotIn("news", json.dumps(d).lower())

    def test_unavailable_sections_say_why_and_never_a_number(self):
        """The optional providers (period returns, the collateral summary, the trade blotters' counters) and the risk module: while a
        module is not bound, or its function does not exist yet, its section is present: false with the reason - never 0."""
        self.trade()
        rt = self.rt
        saved = {a: getattr(rt, a, None) for a in ("performance", "collateral", "blotters", "risk")}
        try:
            rt.performance, rt.collateral, rt.blotters, rt.risk = None, types.SimpleNamespace(), None, None
            d = self.counted()
            self.assertEqual({"present": False, "reason": _dashboard.WHY["risk"]}, d["exposure"])
            self.assertEqual({"present": False, "reason": _dashboard.WHY["collateral"]}, d["margin"])
            self.assertEqual({"present": False, "reason": _dashboard.WHY["collateral"]}, d["collateral"])
            self.assertEqual({"present": False, "reason": _dashboard.WHY["counters"]}, d["counters"])
            self.assertEqual({"present": False, "reason": _dashboard.WHY["returns"]}, d["returns"])
            for p in ("TODAY", "MTD", "YTD", "ITD"):
                self.assertEqual({"pct": None, "reason": _dashboard.WHY["returns"]}, d["pnl"][p]["return"], p)
                self.assertEqual("999.00", d["pnl"][p]["pnl"], p)                # the ledger's own figures stay
            self.assertEqual("10000999.00", d["nav"]["nav"])
            self.assertEqual("kept", d["timing"]["counts"])
            self.assertIsNone(d["attention"]["warnings"])                        # the risk module is not loaded: unavailable, not 0
            self.assertIn("risk module not loaded", d["attention"]["notes"])
            c = self.ok("GET", "/api/dashboard/counts?book=T")
            self.assertEqual((False, _dashboard.WHY["counters"], None, None), (c["present"], c["reason"], c["pending_badge"], c["completed_badge"]))
        finally:
            for a, v in saved.items():
                setattr(rt, a, v)

    def test_a_failing_provider_is_said_and_logged_never_a_500(self):
        rt = self.rt

        def boom(*a, **k):
            raise ZeroDivisionError("division by zero")
        saved = {a: getattr(rt, a, None) for a in ("collateral", "blotters")}
        try:
            rt.collateral = types.SimpleNamespace(summary=boom)
            rt.blotters = types.SimpleNamespace(counts=boom)
            d = self.ok("GET", DASH)
            why = "could not be computed (ZeroDivisionError); the details are in the terminal log"
            self.assertEqual({"present": False, "reason": why}, d["margin"])
            self.assertEqual({"present": False, "reason": why}, d["counters"])
            self.assertTrue(any("dashboard: collateral summary failed" in m for _lvl, m in rt.log.records), rt.log.records[-5:])
        finally:
            for a, v in saved.items():
                setattr(rt, a, v)

    def test_providers_present_figures_pass_through_and_the_badges_add_up(self):
        """With the providers present: the collateral summary's cents become the margin / collateral sections; the trade blotters'
        counters become the counters and the sidebar badges - Pending Trades = working 3 + partially filled 1 + held 2 = 6, Completed
        Trades = settling 4 (queued, waiting for a parent, dealer quotes, rejections and failures are not in the badges).  A summary
        that takes a snapshot gets the answer's own."""
        rt = self.rt
        counts = {"working": 3, "partial": 1, "held": 2, "queued": 5, "waiting_parent": 1, "rfq_open": 2, "rejected_today": 7, "settling": 4, "failed": 1}
        summ = {"margin_required_c": 50000000, "margin_posted_c": 60000000, "margin_excess_c": 10000000, "collateral_posted_c": 1234500, "collateral_received_c": 99,
                "restricted_c": 99, "encumbered_c": 1234500, "open_calls": 2, "failed_settlements": 1, "by_counterparty": [{"counterparty": "GS"}]}
        got = []

        def summary(services, scope, snapshot=None):
            got.append(snapshot is not None)
            return dict(summ)
        saved = {a: getattr(rt, a, None) for a in ("collateral", "blotters")}
        try:
            rt.collateral = types.SimpleNamespace(summary=summary)
            rt.blotters = types.SimpleNamespace(counts=lambda services, scope: dict(counts))
            d = self.ok("GET", DASH)
            self.assertEqual([True], got)
            self.assertEqual({"present": True, "required": "500000.00", "posted": "600000.00", "excess": "100000.00", "open_calls": 2}, d["margin"])
            self.assertEqual(("12345.00", "0.99", "12345.00", 1), (d["collateral"]["posted"], d["collateral"]["received"], d["collateral"]["encumbered"],
                                                                   d["collateral"]["failed_settlements"]))
            self.assertEqual(dict(counts, present=True, reason=None), d["counters"])
            c = self.ok("GET", "/api/dashboard/counts?book=T")
            self.assertEqual((True, 6, 4, counts), (c["present"], c["pending_badge"], c["completed_badge"], c["counts"]))
            self.assertEqual("T1:ALL", c["scope"])
        finally:
            for a, v in saved.items():
                setattr(rt, a, v)

    def test_unknown_book_and_module_registration(self):
        status, body, _ = self.handle("GET", "/api/dashboard?book=Z")
        self.assertEqual((404, "UNKNOWN_BOOK"), (status, body["error"]["code"]))
        h = self.ok("GET", "/api/health")
        self.assertEqual("bound", h["modules"]["dashboard"])                    # C2: the optional module of P1
        self.assertIs(self.rt.dashboard, _dashboard)


class DashboardBudget(DashboardBase):
    """The 300 ms budget: one snapshot per answer, the Risk page's position rows instead of its whole report, and the two counts that
    read the whole book (risk warnings, compliance notices) counted in the background, never inside an answer."""

    def test_exposure_is_the_risk_pages_figures_by_every_path(self):
        """The Dashboard's exposure is the Risk page's figures on one snapshot, for the book and each account, by every path: the risk
        module's public ``exposure_summary`` (preferred; it is in the build since 1.0.6, FINSIM1 P1b-R2 - so the real module's path is
        now "exposure_summary", on purpose), its row helpers (the light path, for a risk module without the summary) and the whole report."""
        self.trade()
        rt = self.rt
        snap = rt.marks.snapshot()
        for scope in (SCOPE, USX, TREASURY):
            nav = rt.accounting.nav(scope, snapshot=snap)
            whole = _dashboard.exposure_of_report(rt.risk.report(rt, scope, snapshot=snap))
            summ, via = _dashboard._exposure_of(rt, scope, snap, nav)
            self.assertEqual("exposure_summary", via, scope)
            self.assertEqual(whole, summ, scope)
            self.assertEqual(whole, _dashboard._exposure_light(rt, rt.risk, scope, snap, nav), scope)
        nav = rt.accounting.nav(SCOPE, snapshot=snap)
        light, _via = _dashboard._exposure_of(rt, SCOPE, snap, nav)
        self.assertEqual((3495000, 0, 3495000, 3495000, Decimal("0.0035")), (light["long_c"], light["short_c"], light["gross_c"], light["net_c"], light["leverage"]))
        saved = rt.risk
        try:
            rt.risk = types.SimpleNamespace(**{n: getattr(saved, n) for n in ("_pos_risk", "_sum_rows", "_ratios", "_pct_signed", "DEFINITIONS")},
                                            report=lambda *a, **k: self.fail("the whole report is not needed"))   # without the summary: the row helpers
            lite, via = _dashboard._exposure_of(rt, SCOPE, snap, nav)
            self.assertEqual(("exposure", light), (via, lite))
            rt.risk = types.SimpleNamespace(report=saved.report)              # an older risk module: the whole report, the same figures
            full, via = _dashboard._exposure_of(rt, SCOPE, snap, nav)
            self.assertEqual(("exposure_report", light), (via, full))
            view = saved.report(rt, SCOPE, snapshot=snap)
            figures = {k: getattr(view, k) for k in _dashboard.EXPOSURE_KEYS}
            rt.risk = types.SimpleNamespace(exposure_summary=lambda services, scope, snapshot=None: dict(figures, definitions=saved.DEFINITIONS),
                                            report=lambda *a, **k: self.fail("the whole report is not needed"))
            summ, via = _dashboard._exposure_of(rt, SCOPE, snap, nav)
            self.assertEqual(("exposure_summary", light), (via, summ))
        finally:
            rt.risk = saved

    def test_pnl_periods_computes_the_ledger_once_and_equals_pnl_of_each_period(self):
        """FINSIM1 P1b-R1: ``Accounting.pnl_periods`` answers TODAY / MTD / YTD / ITD on one snapshot with the scope's current figures
        computed ONCE (one ``_components`` pass instead of four) and each period's view equals ``pnl`` of that period on the same snapshot.
        Hand-worked (the module docstring): no daily snapshot yet, so every period measures from inception: NAV 10,000,999.00 - capital
        10,000,000.00 = 999.00 = unrealised 1,000.00 - fees 1.00, residual 0; only ITD is not degraded.  The Dashboard asks it once."""
        self.trade()
        rt = self.rt
        acc = rt.accounting
        snap = rt.marks.snapshot()
        calls = []
        real = acc._components

        def counting(conn, sc, s):
            calls.append(sc.key)
            return real(conn, sc, s)
        acc._components = counting
        try:
            got = acc.pnl_periods(SCOPE, ("TODAY", "MTD", "YTD", "ITD"), snapshot=snap)
            self.assertEqual([SCOPE], calls)                                   # one pass over the positions for the four periods
            views, _rows = _dashboard._pnl_views(rt, SCOPE, snap)              # the Dashboard asks it once (no per-period fallback)
            self.assertEqual([SCOPE, SCOPE], calls)
        finally:
            del acc._components                                                # the class's method again
        for p in ("TODAY", "MTD", "YTD", "ITD"):
            self.assertEqual(acc.pnl(SCOPE, p, snapshot=snap), got[p], p)
            self.assertEqual(got[p], views[p], p)
            self.assertEqual((1000099900, 1000000000, 99900, 0, p != "ITD"), (got[p].nav_c, got[p].flows_c, got[p].pnl_c, got[p].residual_c, got[p].degraded), p)
        self.assertEqual({"realised_c": 0, "income_c": 0, "financing_c": 0, "fees_c": 100, "unrealised_c": 100000, "fx_result_c": 0}, got["ITD"].components)
        self.assertEqual(["itd", "today"], sorted(acc.pnl_periods(SCOPE, ("itd", "today"), snapshot=snap)))   # keyed as given
        with self.assertRaises(ValueError):
            acc.pnl_periods(SCOPE, ("TODAY", "WEEK"), snapshot=snap)

    def test_one_answer_reads_one_snapshot_and_never_rebuilds_the_risk_report(self):
        self.trade()
        rt = self.rt
        real_report, real_holdings, real_snapshot, real_recount = rt.risk.report, rt.compliance.holdings, rt.marks.snapshot, _dashboard._recount
        calls = {"report": 0, "holdings": [], "snapshot": 0, "recount": 0}

        def report(*a, **k):
            calls["report"] += 1
            return real_report(*a, **k)

        def holdings(services, scope, snapshot=None):
            calls["holdings"].append(snapshot is not None)
            return real_holdings(services, scope, snapshot=snapshot)

        def snapshot(*a, **k):
            calls["snapshot"] += 1
            return real_snapshot(*a, **k)

        def recount(rt_, scope):
            calls["recount"] += 1
        saved = {a: getattr(rt, a, None) for a in ("performance", "collateral", "blotters")}
        try:
            rt.risk.report, rt.compliance.holdings, rt.marks.snapshot = report, holdings, snapshot
            rt.performance, rt.collateral, rt.blotters = None, None, None          # only the Dashboard's own reads are counted
            _dashboard._recount = recount
            d = self.ok("GET", DASH)                                              # the first answer: the count is asked for, after the answer
            self.assertEqual({"report": 0, "holdings": [], "snapshot": 1, "recount": 1}, calls)
            self.assertEqual("counting", d["timing"]["counts"])
            _dashboard._recount = real_recount
            _dashboard._recount(rt, SCOPE)                                        # the background count: one report, one compliance pass, its own snapshot
            self.join()
            self.assertEqual({"report": 1, "holdings": [True], "snapshot": 2, "recount": 1}, calls)
            d = self.ok("GET", DASH)                                              # a later answer: the counts as counted, nothing recounted
            self.assertEqual({"report": 1, "holdings": [True], "snapshot": 3, "recount": 1}, calls)
            self.assertEqual("kept", d["timing"]["counts"])
            self.assertEqual(len(real_report(rt, SCOPE).warnings), d["attention"]["warnings"])
        finally:
            rt.risk.report, rt.compliance.holdings, rt.marks.snapshot = real_report, real_holdings, real_snapshot
            _dashboard._recount = real_recount
            for a, v in saved.items():
                setattr(rt, a, v)

    def test_counts_are_kept_then_recounted_after_a_booking_or_a_minute(self):
        rt = self.rt
        d1 = self.counted()
        c1 = d1["sources"]["attention"]["counts"]
        self.assertEqual(("kept", rt.clock.epoch(), rt.db.state_version()), (c1["how"], c1["counted_at"], c1["state_version"]))
        s = self.ok("GET", "/api/attention/summary?book=T")
        self.assertEqual((s["warnings"], s["compliance"]), (d1["attention"]["warnings"], d1["attention"]["compliance"]))
        # a booking changes the ledger: the next answer carries the last counts with their time while a recount runs behind it
        req = {"account_id": USX, "instrument_id": "AAPL", "side": "BUY", "qty": 10, "order_type": "LIMIT", "limit_px": "300.00", "tif": "DAY",
               "request_id": "ui:dash-limit-0001"}
        self.assertEqual("WORKING", self.ok("POST", "/api/orders", req)["order"]["status"])
        d2 = self.ok("GET", DASH)
        c2 = d2["sources"]["attention"]["counts"]
        self.assertEqual(("recounting", c1["counted_at"], c1["state_version"]), (c2["how"], c2["counted_at"], c2["state_version"]))
        self.assertEqual(1, d2["attention"]["working_orders"])                    # the rest of the strip is counted now
        self.join()
        d3 = self.ok("GET", DASH)
        c3 = d3["sources"]["attention"]["counts"]
        self.assertEqual(("kept", rt.db.state_version()), (c3["how"], c3["state_version"]))
        self.assertNotEqual(c1["state_version"], c3["state_version"])
        # a minute later they are recounted even without a booking
        rt.clock.advance(_dashboard.COUNTS_MAX_AGE_S)
        self.assertEqual("recounting", self.ok("GET", DASH)["timing"]["counts"])
        self.join()
        c5 = self.ok("GET", DASH)["sources"]["attention"]["counts"]
        self.assertEqual(("kept", rt.clock.epoch()), (c5["how"], c5["counted_at"]))
        # a failed recount keeps the last counts with their time, says why beside them and is retried a minute later, not before
        real = _dashboard._count_now

        def boom(*a, **k):
            raise ZeroDivisionError("division by zero")
        try:
            _dashboard._count_now = boom
            rt.clock.advance(_dashboard.COUNTS_MAX_AGE_S)
            self.ok("GET", DASH)
            self.join()
            d6 = self.ok("GET", DASH)
            c6 = d6["sources"]["attention"]["counts"]
            why = "the last count could not be computed (ZeroDivisionError); the details are in the terminal log"
            self.assertEqual(("recounting", c5["counted_at"], why), (c6["how"], c6["counted_at"], c6["error"]))
            self.assertEqual((c5["warnings"], c5["compliance"]), (d6["attention"]["warnings"], d6["attention"]["compliance"]))
            self.assertIn(why, d6["attention"]["notes"])
            self.assertIsNone(_dashboard.recount_thread(rt, SCOPE))               # not retried within the minute
            self.assertTrue(any("dashboard: the background count of the risk warnings and compliance notices failed" in m for _lvl, m in rt.log.records))
            # the first count of a book failing: "failed", None with the reason (never 0)
            _dashboard._COUNTS.pop(rt, None)
            self.ok("GET", DASH)
            self.join()
            d7 = self.ok("GET", DASH)
            self.assertEqual(("failed", None, None), (d7["timing"]["counts"], d7["attention"]["warnings"], d7["attention"]["compliance"]))
            self.assertIn(why, d7["attention"]["notes"])
        finally:
            _dashboard._count_now = real

    def test_a_runtime_without_the_cache_counts_in_the_answer(self):
        """A stand-in runtime that cannot be weakly referenced has no cache: its answer counts them itself ("counted now")."""
        rt = self.rt
        ns = types.SimpleNamespace(**vars(rt))
        out, start = _dashboard.slow_counts_of(ns, SCOPE)
        s = self.ok("GET", "/api/attention/summary?book=T")
        self.assertEqual(("counted now", False, s["warnings"], s["compliance"]), (out["how"], start, out["warnings"], out["compliance"]))
        self.assertGreater(out["counted_ms"], 0.0)


if __name__ == "__main__":
    unittest.main()
