"""The "Requires your attention" summary (``finsim.terminal.attention``; FINSIM1_PLAN C3, 15.7 item 1; D12 of §19.24).

The two summary tests moved here verbatim from the retired ``test_terminal_news.py`` (news was removed from FinSim1 in 1.0.5); only
the news state left them (the summary carries no news key any more, C3).  New: provider pauses ignore the retired news providers,
``GET /api/attention/summary`` filters by book, and the 1.0.5 Dashboard's tiles print fields the summary carries.

No network: the ledger state behind the summary is built by hand through the bus.  Every expected number is worked in a comment.
Epochs used (New York is EDT, UTC-4, on these dates):
  2026-10-19: 17:00 ET = 1792443600; 17:15 ET = 1792444500
"""
from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import tempfile
import types
import unittest

from tests.terminal_fixture_server import FixtureServer
from tests.terminal_helpers import TREASURY, US_EXCHANGE, _Log, bal, buy, execute, fund, make_runtime
from tests.test_terminal_static import js_balance
from finsim.terminal import config, server as _server
from finsim.terminal.attention import RETIRED_PROVIDERS, attention_summary, retired_provider
from finsim.terminal.clock import FakeClock
from finsim.terminal.commands import Command
from finsim.terminal.ledger import Entry, cr, dr
from finsim.terminal.quotes.providers import FakeProvider

_HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(os.path.dirname(_HERE), "finsim", "terminal", "static")


def _h_attention_state(ctx, payload):
    """Test-only handler: the hand-built state behind the attention counts (§19.5 numbers): the collateral at the lender
    after the Monday mark ``Dr 1400 AAPL 53,040.00 / Cr 1010 53,040.00`` (1.02 x 1,000 x 52.00 = 53,040.00) with the
    OPEN borrow that explains it (``reason`` set, so L12 holds without a short; a recall notice), COLLATERAL_MARKED
    events with the given signed deltas, orders rows, one WARN attention."""
    aid = payload["account_id"]
    ts = ctx.ts
    if payload.get("collateral_c"):
        c = int(payload["collateral_c"])
        ctx.post(Entry("COLLATERAL", "collateral at the lender after the mark (test)", [dr(aid, "1400", "USD", c, instrument_id="AAPL"), cr(aid, "1010", "USD", c)],
                       payload["idem_key"], "borrow", "b-test-1"))
        ctx.conn.execute("INSERT INTO borrows(borrow_id, command_id, account_id, instrument_id, qty, qty_returned, fee_rate, rebate_rate, tier, lender, collateral_c,"
                         " currency, opened_ts, opened_date, status, closed_ts, order_id, reason, last_mark_date, recall_notice) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'OPEN',NULL,NULL,?,?,?)",
                         ("b-test-1", ctx.command_id, aid, "AAPL", 1000, 0, "0.0030", "0.0337", "GC", "test lender", c, "USD", ts, "2026-10-16",
                          "test fixture: borrow kept open without a short", "2026-10-19", "recall notice: return by 2026-10-21"))
        ctx.touched_instruments.add((aid, "AAPL"))
    for d in payload.get("deltas") or []:
        ctx.event("COLLATERAL_MARKED", account_id=aid, instrument_id="AAPL", ref_type="borrow", ref_id="b-test-1", delta_c=int(d), required_c=5304000, mark="52.00")
    for o in payload.get("orders") or []:
        ctx.conn.execute("INSERT INTO orders(order_id, request_id, account_id, instrument_id, side, qty, filled_qty, order_type, limit_px, tif, status, session_group,"
                         " created_ts, updated_ts, expires_ts, preview_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (o["order_id"], o["request_id"], aid, o.get("instrument_id", "AAPL"), "BUY", 10, 0, "LIMIT", "300.00", "DAY", o["status"], "NYSE", ts, ts, ts + 86400, "{}"))
    if payload.get("attention"):
        ctx.attention("WARN", "test", "TEST_WARN", "a sample warning (test)", {"account_id": aid})
    return {"ok": True}


def _h_swap_vm(ctx, payload):
    """Test-only handler: one swap variation margin recorded the way SWAP_VM records it (``products/swaps.py`` ``_vm_core``): its
    entry carries the VM's business date ``vm_date`` (a payment ``Dr 1320 / Cr 1010``, a receipt ``Dr 1010 / Cr 2330``), the
    ``otc_vm`` row links that entry, and the contract's ``vm_balance_c`` / ``mark_c`` follow the ledger (a minimal CTM contract row on
    first use, one sign per contract, so L22 holds).  ``no_entry`` records the ``otc_vm`` row alone (the summary's fallback branch for
    a row without an entry; the contract's balance is left as it is)."""
    aid, cid, ds, delta = payload["account_id"], payload["contract_id"], payload["vm_date"], int(payload["delta_c"])
    row = ctx.conn.execute("SELECT vm_balance_c FROM otc_contracts WHERE contract_id=?", (cid,)).fetchone()
    if row is None:
        ctx.conn.execute("INSERT INTO otc_contracts(contract_id, command_id, account_id, broker, kind, terms_json, notional_c, currency, start_date, maturity_date,"
                         " margin_mode, im_c, status, mark_c, vm_balance_c) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (cid, ctx.command_id, aid, "GS", "TRS", "{}", 500000000, "USD", "2026-10-21", "2027-10-21", "CTM", 0, "OPEN", 0, 0))
        old = 0
    else:
        old = int(row["vm_balance_c"])
    if payload.get("no_entry"):
        ctx.conn.execute("INSERT INTO otc_vm(contract_id, vm_date, mark_c, delta_c, mode, entry_id, inputs_json) VALUES (?,?,?,?,?,?,?)",
                         (cid, ds, old, delta, "CTM", None, "{}"))
        return {"entry_id": None}
    if delta > 0:
        lines = [dr(aid, "1010", "USD", delta, memo="VM received"), cr(aid, "2330", "USD", delta, instrument_id=cid, memo="VM received")]
    else:
        lines = [dr(aid, "1320", "USD", -delta, instrument_id=cid, memo="VM posted"), cr(aid, "1010", "USD", -delta, memo="VM posted")]
    eid = ctx.post(Entry("VM", f"test VM {cid} {ds} {delta}", lines, f"testvm:{cid}:{ds}", ref_type="otc", ref_id=cid, business_date=ds))
    ctx.conn.execute("INSERT INTO otc_vm(contract_id, vm_date, mark_c, delta_c, mode, entry_id, inputs_json) VALUES (?,?,?,?,?,?,?)",
                     (cid, ds, old + delta, delta, "CTM", eid, "{}"))
    ctx.conn.execute("UPDATE otc_contracts SET vm_balance_c=?, mark_c=? WHERE contract_id=?", (old + delta, old + delta, cid))
    return {"entry_id": eid}


class AttentionSummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="finsim-attention-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_attention_summary_counts_on_the_short_book(self):
        rt = make_runtime(self.tmp, at="2026-10-16T10:00:00-04:00")           # Friday 10:00 ET
        try:
            rt.bus.register("test.attention_state", _h_attention_state)
            self.assertTrue(fund(rt, US_EXCHANGE, 200_000_00).ok)
            # an old collateral call (Friday): outside Monday evening's accounting day
            self.assertTrue(execute(rt, "test.attention_state", {"account_id": US_EXCHANGE, "deltas": [100]}).ok)
            rt.clock.set("2026-10-19T17:10:00-04:00")                         # Monday 17:10 ET: the COLLATERAL_MARK of §19.5
            r = execute(rt, "test.attention_state", {"account_id": US_EXCHANGE, "collateral_c": 5304000, "idem_key": "test:coll:1", "deltas": [204000, -204000],
                                                      "orders": [{"order_id": "o-test-1", "request_id": "ui:att-order-00001", "instrument_id": "SPY", "status": "WORKING"},
                                                                 {"order_id": "o-test-2", "request_id": "ui:att-order-00002", "status": "CANCELLED"}],   # not working: not counted
                                                      "attention": True})
            self.assertTrue(r.ok, r.body)
            self.assertTrue(buy(rt, US_EXCHANGE, "AAPL", 100, "339.50").ok)   # settles T+1 = Tue 2026-10-20: PENDING at 17:15
            rt.clock.set("2026-10-19T17:15:00-04:00")
            self.assertTrue(rt.accounting.integrity("full").ok)               # L12 (borrow with a reason), L13 (1400 = 53,040.00 = Σ collateral_c) hold
            s = attention_summary(rt, "ALL")
            self.assertEqual(("2026-10-20", 1792443600, "LIFETIME", ["T1"]), (s["accounting_day"], s["window_start"], s["scope_kind"], s["lifetimes"]))
            self.assertEqual((1, 1, 0), (s["collateral_calls_today"], s["collateral_marks_today"], s["swap_vm_payments_today"]))   # +2,040.00 counts; -2,040.00 and Friday's do not
            self.assertEqual((1, 1, 1), (s["working_orders"], s["pending_settlements"], s["recalls"]))
            self.assertEqual({"ERROR": 0, "WARN": 1, "INFO": 0}, s["open"])
            self.assertEqual(1, s["attention_open"])
            self.assertEqual(("TEST_WARN", "WARN", US_EXCHANGE), (s["items"][0]["code"], s["items"][0]["severity"], s["items"][0]["account_id"]))
            self.assertEqual((True, [], "full"), (s["integrity"]["ok"], s["integrity"]["failing"], s["integrity"]["mode"]))   # the recorded report of the full run above
            self.assertEqual(1792444500, s["integrity"]["checked_at"])
            self.assertEqual({"held": False, "lifetimes": [], "since": {}, "failing": {}}, s["hold"])
            self.assertIsNone(s["warnings"])
            self.assertIsNone(s["compliance"])
            self.assertEqual(["risk module not loaded", "compliance module not loaded"], s["notes"])
            self.assertEqual({"down": [], "refused_ua": [], "paused": 0}, s["providers"])
            self.assertNotIn("news", s)                                         # C3: the summary carries no news state any more
            # per account: the Treasury sees none of the account's items
            t = attention_summary(rt, TREASURY)
            self.assertEqual((0, 0, 0, 0, {"ERROR": 0, "WARN": 0, "INFO": 0}), (t["collateral_calls_today"], t["working_orders"], t["pending_settlements"], t["recalls"], t["open"]))
            a = attention_summary(rt, US_EXCHANGE)
            self.assertEqual((1, 1, 1, 1, 1), (a["collateral_calls_today"], a["working_orders"], a["pending_settlements"], a["recalls"], a["open"]["WARN"]))
            self.assertEqual(s["collateral_calls_today"], attention_summary(rt, "ALL_BOOKS")["collateral_calls_today"])
            self.assertEqual(s["working_orders"], attention_summary(rt, "T1:ALL")["working_orders"])
            # optional modules are counted when bound (a notice with status ok is not a notice)
            rt.risk = types.SimpleNamespace(warnings=lambda services, scope: ["w1", "w2"])
            rt.compliance = types.SimpleNamespace(holdings=lambda services, scope: [{"status": "ok"}, {"status": "notice"}, {"status": "breach"}])
            s2 = attention_summary(rt, "ALL")
            self.assertEqual((2, 2, []), (s2["warnings"], s2["compliance"], s2["notes"]))
            # the window follows the accounting day: still "today" at Tue 17:00, gone at Tue 17:30 (the next accounting day)
            rt.clock.set("2026-10-20T17:00:00-04:00")
            self.assertEqual(1, attention_summary(rt, "ALL")["collateral_calls_today"])
            rt.clock.set("2026-10-20T17:30:00-04:00")
            s3 = attention_summary(rt, "ALL")
            self.assertEqual(("2026-10-21", 0), (s3["accounting_day"], s3["collateral_calls_today"]))
        finally:
            rt.close()

    def test_attention_shows_integrity_failure(self):
        """D12: a corrupted ledger projection fails integrity and the failure shows under "Requires your attention"."""
        rt = make_runtime(self.tmp)
        try:
            self.assertTrue(fund(rt, US_EXCHANGE, 50_000_00).ok)
            self.assertTrue(buy(rt, US_EXCHANGE, "AAPL", 100, "339.50").ok)   # gross 33,950.00 + fee: rows 1100 AAPL, 5000 AAPL, 2100, 1010
            self.assertTrue(rt.accounting.integrity("full").ok)
            s = attention_summary(rt, "ALL")
            self.assertEqual((True, [], False, 0), (s["integrity"]["ok"], s["integrity"]["failing"], s["hold"]["held"], s["open"]["ERROR"]))
            # one cent added to the 1010 projection: L3 (gl_balances ≠ Σ lines) and L4 (Σ gl_balances of T1 USD = +0.01 ≠ 0)
            with rt.db.writer() as tx:
                tx.x("UPDATE gl_balances SET balance_c = balance_c + 1 WHERE account_id=? AND gl='1010' AND currency='USD'", (US_EXCHANGE,))
            rep = rt.accounting.integrity("full")                              # what the INTEGRITY job / server start records
            self.assertEqual((False, ["L3", "L4"]), (rep.ok, rep.failing))
            s = attention_summary(rt, "ALL")
            self.assertEqual((False, ["L3", "L4"]), (s["integrity"]["ok"], s["integrity"]["failing"]))
            self.assertEqual((True, ["T1"]), (s["hold"]["held"], s["hold"]["lifetimes"]))
            self.assertEqual({"L3", "L4"}, set(s["hold"]["failing"]["T1"]))
            self.assertEqual(1, s["open"]["ERROR"])
            self.assertEqual(("INTEGRITY", "integrity", "lifetime", "T1"), (s["items"][0]["code"], s["items"][0]["source"], s["items"][0]["ref_type"], s["items"][0]["ref_id"]))
            probs = s["integrity"]["problems"]
            self.assertEqual(["L3", "L4"], sorted({p["id"] for p in probs if p["severity"] == "ERROR"}))
            # the lone cent also breaks the NAV identity by 0.01 (NAV moved, no P&L component did): M1 "P&L explain residual" is a WARN,
            # not a failure, reported once per period (TODAY, MTD, YTD, ITD) = 4 warnings
            self.assertEqual(["M1"], sorted({p["id"] for p in probs if p["severity"] == "WARN"}))
            self.assertEqual(4, s["integrity"]["warnings"])
            # the same cent taken from the 5000 fee row: the lifetime sums to zero again, so L3 alone fails (two rows differ from their lines)
            with rt.db.writer() as tx:
                tx.x("UPDATE gl_balances SET balance_c = balance_c - 1 WHERE account_id=? AND gl='5000' AND currency='USD' AND instrument_id='AAPL'", (US_EXCHANGE,))
            rep = rt.accounting.integrity("full")
            self.assertEqual((False, ["L3"]), (rep.ok, rep.failing))
            s = attention_summary(rt, "ALL")
            self.assertEqual((False, ["L3"], True), (s["integrity"]["ok"], s["integrity"]["failing"], s["hold"]["held"]))
            # two projection rows differ from their lines (ERROR L3 twice); the period components come from the lines while NAV comes
            # from the projection, so the explain residual is still 0.01 per period (WARN M1 x 4) - a WARN never enters `failing`
            self.assertEqual(["L3", "L3"], [p["id"] for p in s["integrity"]["problems"] if p["severity"] == "ERROR"])
            self.assertEqual(["M1"] * 4, [p["id"] for p in s["integrity"]["problems"] if p["severity"] == "WARN"])
            self.assertEqual(["L3"], attention_summary(rt, TREASURY)["integrity"]["failing"])   # the lifetime's failure shows on every scope of T1
            # repaired: the next clean run clears the hold and resolves the attention row
            with rt.db.writer() as tx:
                tx.x("UPDATE gl_balances SET balance_c = balance_c - 1 WHERE account_id=? AND gl='1010' AND currency='USD'", (US_EXCHANGE,))
                tx.x("UPDATE gl_balances SET balance_c = balance_c + 1 WHERE account_id=? AND gl='5000' AND currency='USD' AND instrument_id='AAPL'", (US_EXCHANGE,))
            rt.clock.advance(60)
            s = attention_summary(rt, "ALL", check=True)                       # check=True runs the ledger set now
            self.assertEqual((True, [], "ledger", False, 0), (s["integrity"]["ok"], s["integrity"]["failing"], s["integrity"]["mode"], s["hold"]["held"], s["open"]["ERROR"]))
            self.assertEqual(rt.clock.epoch(), s["integrity"]["checked_at"])
            self.assertTrue(rt.accounting.integrity("full").ok)
        finally:
            rt.close()

    def test_provider_pauses_exclude_the_retired_news_providers(self):
        """C3 / 15.7 item 5: yahoo_news, fed, newspaper and news_* are never counted as provider pauses - neither from the stored
        provider_health rows (an older release's) nor from an in-memory health snapshot; a real provider still counts."""
        self.assertEqual(("yahoo_news", "fed", "newspaper"), RETIRED_PROVIDERS)
        self.assertEqual([True, True, True, True, True, False, False, False],
                         [retired_provider(p) for p in ("yahoo_news", "fed", "newspaper", "news_fed", "news_paper", "fred", "yahoo", "federal")])
        rt = make_runtime(self.tmp)
        try:
            now = rt.clock.epoch()
            with rt.db.writer() as tx:                                         # what 1.0.4 left behind: news sources failing, Cboe down too
                for name in ("yahoo_news", "fed", "newspaper", "news_fed", "cboe"):
                    tx.x("INSERT INTO provider_health(provider, last_ok_ts, last_err_ts, last_error, consecutive_errors, requests_today, day, down_until_ts)"
                         " VALUES (?,?,?,?,?,?,?,?)", (name, now - 600, now - 60, "unreachable (test)", 3, 5, "2026-10-20", None))
            s = attention_summary(rt, "ALL")                                   # no in-memory health on this runtime: the stored rows
            self.assertEqual({"down": ["cboe"], "refused_ua": [], "paused": 1}, s["providers"])
            snap = {"fed": {"ok": False, "down": True}, "yahoo_news": {"ok": False, "refused_ua": True}, "news_paper": {"ok": False},
                    "yahoo": {"ok": True}, "fred": {"ok": False}, "treasury": {"ok": True, "refused_ua": True}}
            rt.providers = types.SimpleNamespace(health=types.SimpleNamespace(snapshot=lambda: snap))
            s = attention_summary(rt, "ALL")                                   # the in-memory snapshot: fred down, treasury refused
            self.assertEqual({"down": ["fred"], "refused_ua": ["treasury"], "paused": 1}, s["providers"])
        finally:
            rt.close()

    def test_swap_vm_payments_count_by_business_date(self):
        """P8L-01: a swap VM payment is one of today's collateral calls by its BUSINESS date (``otc_vm.vm_date``), not by the time its
        entry was posted.  The historical import's catch-up posts the VM of 10-21 .. 10-27 together at 00:37 on 10-28; only the 10-27
        payment is a call of accounting day 10-28 (the VM of D runs from 17:05 ET on D, inside accounting day D+1)."""
        from tests import test_terminal_collateral as tcoll
        pb = tcoll.PB                                                           # T1:PB_GS
        rt = tcoll.runtime(self.tmp, at="2026-10-21T10:00:00-04:00")            # Wed 10:00 ET
        try:
            rt.bus.register("test.swap_vm", _h_swap_vm)
            tcoll.open_pb(self, rt)                                             # deposit 15,000,000.00; open the Goldman PB account
            self.assertTrue(fund(rt, pb, 100_000_000).ok)                       # Treasury -> PB 1,000,000.00 (pays every VM below)

            def vm(cid, ds, delta_c, **kw):
                r = execute(rt, "test.swap_vm", dict({"account_id": pb, "contract_id": cid, "vm_date": ds, "delta_c": delta_c}, **kw))
                self.assertTrue(r.ok, r.body)

            # Wed 10-28 00:37:20 ET: an as-of catch-up posts every business date's VM at once (the dry run's figures).  Payments
            # (delta < 0): 2,073.36 (10-21) + 35,341.95 (10-22) + 23,994.00 (10-23) + 1,276.48 (10-26) + 172,989.00 (10-27) = 235,674.79,
            # within the 1,000,000.00 funded; one receipt +74,664.00 (10-27) on its own contract (one sign per contract, L22)
            rt.clock.set("2026-10-28T00:37:20-04:00")
            for cid, ds, delta in (("SWP-P1", "2026-10-21", -207336), ("SWP-P1", "2026-10-22", -3534195), ("SWP-P2", "2026-10-23", -2399400),
                                   ("SWP-P2", "2026-10-26", -127648), ("SWP-P3", "2026-10-27", -17298900), ("SWP-R1", "2026-10-27", 7466400)):
                vm(cid, ds, delta)
            self.assertEqual(100_000_000 - 23_567_479 + 7_466_400, bal(rt, pb, "1010"))   # 1,000,000.00 - 235,674.79 + 74,664.00 = 838,989.21
            self.assertTrue(rt.accounting.integrity("full").ok)                  # L22: 1320 / 2330 tie to each contract's VM balance
            rt.clock.set("2026-10-28T00:49:00-04:00")
            s = attention_summary(rt, "ALL")
            # accounting day 10-28 = (10-27 17:00, 10-28 17:00]: all five payments were POSTED inside it (what the tile counted before)
            with rt.db.reader() as conn:
                posted = conn.execute("SELECT COUNT(*) FROM otc_vm v JOIN journal_entries e ON e.entry_id=v.entry_id WHERE v.delta_c < 0 AND e.ts > ? AND e.ts <= ?",
                                      (s["window_start"], s["as_of"])).fetchone()[0]
            self.assertEqual(5, posted)
            # ... but by business date only the 10-27 payment (172,989.00) is today's call; the receipt is not a call; no collateral mark
            self.assertEqual(("2026-10-28", 1, 0, 1), (s["accounting_day"], s["swap_vm_payments_today"], s["collateral_marks_today"], s["collateral_calls_today"]))
            # Wed 10-28 17:05 ET: the 17:05 VM of 10-28 pays 10,000.00 (accounting day 10-29); two rows recorded without an entry (the
            # fallback: the business date alone decides): 10-28 (500.00) counts tonight, 10-26 (500.00) never does
            rt.clock.set("2026-10-28T17:05:00-04:00")
            vm("SWP-P3", "2026-10-28", -1000000)
            vm("SWP-N1", "2026-10-28", -50000, no_entry=True)
            vm("SWP-N2", "2026-10-26", -50000, no_entry=True)
            rt.clock.set("2026-10-28T17:30:00-04:00")
            s = attention_summary(rt, "ALL")
            # accounting day 10-29 = (10-28 17:00, 10-29 17:00]: the 10-28 VM (posted 17:05) and the 10-28 row without an entry = 2; the
            # 10-27 payment (business date 10-27, posted 00:37 on 10-28) and the 10-26 row are not
            self.assertEqual(("2026-10-29", 2, 2), (s["accounting_day"], s["swap_vm_payments_today"], s["collateral_calls_today"]))
            rt.clock.set("2026-10-29T17:00:00-04:00")                           # still accounting day 10-29
            self.assertEqual(2, attention_summary(rt, "ALL")["swap_vm_payments_today"])
            rt.clock.set("2026-10-29T17:30:00-04:00")                           # accounting day 10-30: the business dates are 10-28 at the latest
            self.assertEqual(("2026-10-30", 0), (lambda x: (x["accounting_day"], x["swap_vm_payments_today"]))(attention_summary(rt, "ALL")))
            self.assertEqual(0, attention_summary(rt, TREASURY)["swap_vm_payments_today"])   # the Treasury scope holds no contract
            # Mon 11-02 09:30 EST (DST ended 11-01): the VM of Fri 10-30 is posted only now (its input was published on Monday) and pays
            # 2,500.00; a VM of Thu 10-29 caught up at the same time pays 1,500.00.  Accounting day 11-02, window (11-01 17:00, 11-02 17:00]:
            # the last business day before it is Fri 10-30, so the Friday payment is today's call and the Thursday one is not
            rt.clock.set("2026-11-02T09:30:00-05:00")
            vm("SWP-P4", "2026-10-29", -150000)
            vm("SWP-P5", "2026-10-30", -250000)
            rt.clock.set("2026-11-02T09:45:00-05:00")
            s = attention_summary(rt, "ALL")
            self.assertEqual(("2026-11-02", 1, 1), (s["accounting_day"], s["swap_vm_payments_today"], s["collateral_calls_today"]))
        finally:
            rt.close()


def build_three_books(tmp: str):
    """A server runtime with the three books (P1 / F1 / I1), offline, nothing started."""
    os.environ[config.HOME_ENV] = tmp
    os.environ[config.DB_ENV] = os.path.join(tmp, "terminal.db")
    clock = FakeClock("2026-10-20T14:00:00-04:00")
    return _server.build_runtime(tmp, os.path.join(tmp, "terminal.db"), clock=clock, offline=True, fake_provider=FakeProvider(clock), log=_Log(),
                                 books=list(config.BOOKS), fetch_threads=0)


class AttentionSummaryRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="finsim-attention-route-")
        self.rt = build_three_books(self.tmp)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def get(self, path):
        status, body, _headers = self.rt.router.handle("GET", path, None, None, "127.0.0.1", None)
        return status, body

    def test_attention_summary_route_filters_by_book(self):
        """15.7 item 1: GET /api/attention/summary?book= is the selected book's summary - its rows plus the global ones (the same
        set as GET /api/attention for that book); ?all=1 consolidates every book; an unknown book is 404."""
        rt = self.rt
        base = {}
        for code in ("P", "F", "I"):
            st, s = self.get(f"/api/attention/summary?book={code}")
            self.assertEqual(200, st, s)
            base[code] = s["open"]["WARN"]
        st, s_all = self.get("/api/attention/summary?all=1")
        self.assertEqual(200, st, s_all)
        base_all = s_all["open"]["WARN"]
        rows = [("F1:US_EXCHANGE", "F row"), ("F1:PB_GS", "F second row"), ("P1:US_EXCHANGE", "P row"), (None, "global row")]
        for i, (acct, msg) in enumerate(rows):
            payload = {"severity": "WARN", "source": "test", "code": "TEST_ROUTE", "message": msg}
            if acct:
                payload["account_id"] = acct
            res = rt.bus.execute(Command("attention.open", payload, f"ui:att-route-{i:04d}", "ui"))
            self.assertTrue(res.ok, res.body)
        st, f = self.get("/api/attention/summary?book=F")
        self.assertEqual((200, "F1:ALL", ["F1"]), (st, f["scope"], f["lifetimes"]))
        self.assertEqual(base["F"] + 3, f["open"]["WARN"])                    # F's two rows + the global one
        self.assertEqual({"F row", "F second row", "global row"}, {x["message"] for x in f["items"] if x["code"] == "TEST_ROUTE"})
        st, p = self.get("/api/attention/summary?book=P")
        self.assertEqual(base["P"] + 2, p["open"]["WARN"])                    # P's row + the global one
        self.assertEqual({"P row", "global row"}, {x["message"] for x in p["items"] if x["code"] == "TEST_ROUTE"})
        st, i = self.get("/api/attention/summary?book=I")
        self.assertEqual(base["I"] + 1, i["open"]["WARN"])                    # the global row only
        st, a = self.get("/api/attention/summary?all=1")
        self.assertEqual((200, "ALL_BOOKS"), (st, a["scope"]))
        self.assertEqual(base_all + 4, a["open"]["WARN"])                     # every row
        # the same set as the strip's GET /api/attention of that book
        st, strip = self.get("/api/attention?book=F")
        self.assertEqual({"F row", "F second row", "global row"}, {x["message"] for x in strip if x["code"] == "TEST_ROUTE"})
        st, err = self.get("/api/attention/summary?book=Z")
        self.assertEqual((404, "UNKNOWN_BOOK"), (st, err["error"]["code"]))
        self.assertNotIn("news", f)


class DashboardPageTests(unittest.TestCase):
    """The 1.0.5 Dashboard prints the summary's fields as the API sends them: its tiles' keys are summary keys, nothing is computed
    on the page, it reads GET /api/attention/summary and never the route the full Dashboard brings later."""

    def test_dashboard_tiles_tie_to_the_summary(self):
        path = os.path.join(STATIC, "pages", "dashboard.js")
        with open(path, "r", encoding="utf-8") as fh:
            src = fh.read()
        src.encode("ascii")                                                   # ASCII only (test_terminal_static's rule)
        self.assertIn("T.pages.dashboard = ", src)
        self.assertEqual((0, 0, 0), js_balance(src))
        keys = re.findall(r"\{ key: '([a-z_]+)'", src)
        self.assertEqual(["integrity", "hold", "open", "warnings", "compliance", "collateral_calls_today", "recalls", "working_orders", "pending_settlements", "providers"], keys)
        self.assertIsNone(re.search(r"att\.[A-Za-z_.]+\s*[-+*/%]\s*[\w(]", src))   # no arithmetic on a summary field
        self.assertIn("'/api/attention/summary'", src)
        self.assertNotIn("/api/dashboard", src.split("*/", 1)[1])            # the 1.0.5 page calls no /api/dashboard (only its comment names it)
        self.assertNotIn("/api/news", src)
        # each tile leads where 15.7 item 1 says
        links = dict(re.findall(r"key: '([a-z_]+)', label: '[^']*', (?:href|act): '?([^',]+)'?", src))
        self.assertEqual({"integrity": "#/accounting/integrity", "hold": "#/accounting/integrity", "open": "expandStrip", "warnings": "#/risk",
                          "compliance": "#/risk?section=compliance", "collateral_calls_today": "#/risk?section=financing", "recalls": "#/risk?section=financing",
                          "working_orders": "#/activity?type=orders", "pending_settlements": "#/activity?type=settlements", "providers": "#/settings/data"}, links)
        server = FixtureServer(0).start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
            conn.request("GET", "/api/attention/summary?book=P")
            res = conn.getresponse()
            self.assertEqual(200, res.status)
            att = json.loads(res.read().decode("utf-8"))
            for k in keys:
                self.assertIn(k, att, k)
            conn.request("GET", "/api/news?limit=50")
            res = conn.getresponse()
            res.read()
            self.assertEqual(404, res.status)                                  # the fixture server has no news route
            conn.close()
        finally:
            server.stop()
        tmp = tempfile.mkdtemp(prefix="finsim-dashboard-page-")
        rt = make_runtime(tmp)
        try:
            s = attention_summary(rt, "ALL")
            for k in keys:
                self.assertIn(k, s, k)
            self.assertEqual({"ERROR", "WARN", "INFO"}, set(s["open"]))
            self.assertEqual({"down", "refused_ua", "paused"}, set(s["providers"]))
        finally:
            rt.close()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
