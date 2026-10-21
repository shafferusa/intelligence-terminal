"""WP-23 lifetimes tests (TERMINAL_DESIGN.md §13.10, §6.0, §15 /api/books/* + /api/history/*, §19.14 reset / onboarding / book_defaults).

Every expected number is worked by hand in a comment.  No network: FakeClock, FixedMarks (or the FakeProvider through the
server runtime for the route tests), a temp database under FINSIM_HOME.  The runtime is make_runtime(books="real") with
orders (WP-02), prime (WP-07) and lifetimes registered; the route tests build the server runtime (WP-11) so the real
registration path (``register(bus, rt)`` + the on-commit capital follow-up) and the codec are exercised.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from tests.terminal_helpers import _Log, assert_integrity, bal, buy, deposit, execute, fund, make_runtime
from tests import test_terminal_orders as sc
from finsim.terminal import books, config, lifetimes, orders, prime, server as _server
from finsim.terminal.clock import FakeClock
from finsim.terminal.commands import RejectError
from finsim.terminal.quotes.providers import FakeProvider

AT = "2026-10-20T14:00:00-04:00"          # Tuesday 14:00 ET: US_EQUITY open
P1_USX = "P1:US_EXCHANGE"
RESET_RID = "ui:reset-p1-00001"
TABLES = ("journal_entries", "journal_lines", "fills", "lots", "orders", "order_events", "transfers", "settlements", "commands", "events", "instruments",
          "quote_cache", "marks", "nav_snapshots", "accounts", "lifetimes", "agreements", "attention")


def runtime(tmp, at=AT):
    rt = make_runtime(tmp, at=at, books="real")
    orders.register(rt.bus)
    prime.register(rt.bus)
    prime.bind(rt)
    lifetimes.register(rt.bus, rt)
    return rt


def counts(rt):
    with rt.db.reader() as conn:
        return {t: int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]) for t in TABLES}


def q(rt, sql, params=()):
    with rt.db.reader() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(rt, sql, params=()):
    rows = q(rt, sql, params)
    return rows[0] if rows else None


def reset(rt, code="P", cap_c=100000000, confirm="RESET", request_id=None, **extra):
    payload = {"book_code": code, "confirm": confirm}
    if cap_c is not None:
        payload["starting_capital_c"] = cap_c
    payload.update(extra)
    return execute(rt, "book.reset_lifetime", payload, request_id)


def onboard(rt, broker, lifetime=None, request_id=None, **extra):
    payload = {"broker": broker}
    if lifetime:
        payload["lifetime_id"] = lifetime
    payload.update(extra)
    return execute(rt, "book.onboard_pb", payload, request_id)


class LifetimeResetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-lt-")
        self.rt = runtime(self.tmp)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _buy_and_rest(self):
        """§19.14 preconditions in P1: fund 100,000.00 to US_EXCHANGE, BUY 100 AAPL @ 339.50 (fee max(0.50, 1.00) = 1.00),
        a resting LIMIT BUY 100 AAPL @ 300.00 (WORKING), then the mark moves to 340.00."""
        rt = self.rt
        self.assertTrue(fund(rt, P1_USX, 10000000).ok)
        now = rt.clock.epoch()
        rt.marks.set_quote(sc.rec("AAPL", "339.50", now - 120, now, bar=(now - 180, 120000)))
        rt.marks.set("AAPL", "339.50")
        r = sc.submit(rt, account_id=P1_USX, instrument_id="AAPL", side="BUY", qty=100, order_type="MARKET")
        self.assertTrue(r.ok, r.body)
        self.assertEqual("FILLED", r.body["order"]["status"])
        fill = r.body["fills"][0]
        # gross 100 x 339.50 = 33,950.00; fee = max(100 x 0.005 = 0.50, minimum 1.00) = 1.00
        self.assertEqual((3395000, 100), (fill["gross_c"], fill["fee_c"]))
        r = sc.submit(rt, account_id=P1_USX, instrument_id="AAPL", side="BUY", qty=100, order_type="LIMIT", limit_px="300.00")
        self.assertTrue(r.ok, r.body)
        self.assertEqual("WORKING", r.body["order"]["status"])
        rt.marks.set("AAPL", "340.00")
        return r.body["order"]["order_id"]

    def test_19_14_reset_exact(self):
        rt = self.rt
        oid = self._buy_and_rest()
        # NAV(P1:ALL) before: Treasury 1,000,000 - 100,000 = 900,000; US_EXCHANGE settled 100,000 - payable 33,951 + MV 100 x 340 = 34,000 -> 100,049;
        # total 1,000,049.00 = 1,000,000 - 1.00 fee + 100 x (340 - 339.50)
        self.assertEqual(100004900, rt.accounting.nav("P1:ALL").nav_c)
        before = counts(rt)
        r = reset(rt, request_id=RESET_RID)
        self.assertTrue(r.ok, r.body)
        self.assertFalse(r.replayed)
        body = r.body
        # (1) the resting LIMIT order is CANCELLED "lifetime reset" with its order event and the ORDER_CANCELLED event
        o = one(rt, "SELECT status, reason FROM orders WHERE order_id=?", (oid,))
        self.assertEqual(("CANCELLED", "lifetime reset"), (o["status"], o["reason"]))
        ev = one(rt, "SELECT from_status, to_status, note FROM order_events WHERE order_id=? ORDER BY id DESC LIMIT 1", (oid,))
        self.assertEqual(("WORKING", "CANCELLED", "lifetime reset"), (ev["from_status"], ev["to_status"], ev["note"]))
        self.assertEqual(1, one(rt, "SELECT COUNT(*) n FROM events WHERE type='ORDER_CANCELLED' AND ref_id=?", (oid,))["n"])
        self.assertEqual([oid], [c["order_id"] for c in body["cancelled_orders"]])
        # (2) the lifetime row: ended now, final NAV 1,000,049.00, realised 0.00, P&L 49.00, CLOSED, integrity ok, reason
        lt = one(rt, "SELECT * FROM lifetimes WHERE lifetime_id='P1'")
        self.assertEqual((rt.clock.epoch(), 100004900, 0, 4900, "CLOSED", "lifetime reset", "delayed"),
                         (lt["ended_ts"], lt["final_nav_c"], lt["final_realised_c"], lt["final_pnl_c"], lt["status"], lt["reason"], lt["final_quality"]))
        rep = json.loads(lt["integrity_json"])
        self.assertTrue(rep["ok"], rep)
        self.assertEqual("P1", rep["lifetime_id"])
        self.assertEqual("full", rep["mode"])
        self.assertTrue(body["integrity_ok"])
        # the final figures tie to NAV - contributed capital: 1,000,049 - 1,000,000 = 49.00
        self.assertEqual(body["closed"]["final_pnl_c"], body["closed"]["final_nav_c"] - lt["starting_capital_c"] - body["closed"]["deposits_c"] + body["closed"]["withdrawals_c"])
        # a nav_snapshots row P1:ALL for today at 1,000,049.00 (plus one per account) marked final
        snap = one(rt, "SELECT * FROM nav_snapshots WHERE account_id='P1:ALL' AND snap_date='2026-10-20'")
        self.assertEqual(100004900, snap["nav_c"])
        self.assertTrue(json.loads(snap["detail_json"])["final"])
        self.assertEqual(5 + 1, one(rt, "SELECT COUNT(*) n FROM nav_snapshots WHERE snap_date='2026-10-20' AND (account_id LIKE 'P1:%')")["n"])
        # (3) every P1 account CLOSED (five catalogue accounts: no PRIVATE below 5,000,000, no PB)
        acc = q(rt, "SELECT account_id, status, closed_ts FROM accounts WHERE lifetime_id='P1' ORDER BY sort_order")
        self.assertEqual(["P1:TREASURY", "P1:US_EXCHANGE", "P1:DEBT", "P1:CREDIT", "P1:FOREIGN_FX"], [a["account_id"] for a in acc])
        self.assertTrue(all(a["status"] == "CLOSED" and a["closed_ts"] == rt.clock.epoch() for a in acc), acc)
        self.assertEqual(5, one(rt, "SELECT COUNT(*) n FROM events WHERE type='ACCOUNT_CLOSED' AND ref_id='P1'")["n"])
        # (4) P2: OPEN, seq 2, Treasury 1010 1,000,000.00 posted by init:capital:P2 (its own treasury.init_capital command), five accounts, no PB
        p2 = one(rt, "SELECT * FROM lifetimes WHERE lifetime_id='P2'")
        self.assertEqual(("P", 2, "OPEN", 100000000, "Personal Book #2"), (p2["book_code"], p2["seq"], p2["status"], p2["starting_capital_c"], p2["name"]))
        self.assertEqual(100000000, bal(rt, "P2:TREASURY", "1010"))
        self.assertEqual(100000000, bal(rt, "P2:TREASURY", "3000"))
        cmd = one(rt, "SELECT kind, actor, http_status FROM commands WHERE request_id='init:capital:P2'")
        self.assertEqual(("treasury.init_capital", "init", 200), (cmd["kind"], cmd["actor"], cmd["http_status"]))
        self.assertEqual(["P2:TREASURY", "P2:US_EXCHANGE", "P2:DEBT", "P2:CREDIT", "P2:FOREIGN_FX"], body["opened"]["accounts"])
        self.assertEqual(["P2:TREASURY", "P2:US_EXCHANGE", "P2:DEBT", "P2:CREDIT", "P2:FOREIGN_FX"],
                         [a["account_id"] for a in q(rt, "SELECT account_id FROM accounts WHERE lifetime_id='P2' ORDER BY sort_order")])
        self.assertTrue(all(a["sweep_out"] == 1 and a["status"] == "OPEN" for a in q(rt, "SELECT sweep_out, status FROM accounts WHERE lifetime_id='P2'")))
        self.assertEqual("init:capital:P2", body["opened"]["capital_request_id"])
        # (5) frozen marks: nav(P1:ALL) stays 1,000,049.00 after the mark moves to 350.00; P2 1,000,000.00; ALL_BOOKS excludes P1:
        #     1,000,000 + 25,000,000 + 1,000,000,000 = 1,026,000,000.00
        rt.marks.set("AAPL", "350.00")
        n1 = rt.accounting.nav("P1:ALL")
        self.assertEqual((100004900, True), (n1.nav_c, n1.archived))
        self.assertIn("frozen at reset", n1.note)
        self.assertEqual(100000000, rt.accounting.nav("P2:ALL").nav_c)
        n = rt.accounting.nav("ALL_BOOKS")
        self.assertEqual((102600000000, ["P2", "F1", "I1"]), (n.nav_c, n.lifetime_ids))
        # (6) writes into the archive are refused: deposit -> LIFETIME_CLOSED; order.submit -> LIFETIME_CLOSED (checked before the
        #     account); a direct posting into a CLOSED account -> the ledger's ACCOUNT_CLOSED (read-only by construction)
        r = deposit(rt, 100, lifetime="P1")
        self.assertEqual((400, "LIFETIME_CLOSED"), (r.http, r.code))
        r = sc.submit(rt, account_id=P1_USX, instrument_id="AAPL", side="BUY", qty=1, order_type="MARKET")
        self.assertEqual((400, "LIFETIME_CLOSED"), (r.http, r.code))
        r = buy(rt, P1_USX, "AAPL", 1, "350.00")
        self.assertEqual((500, "ACCOUNT_CLOSED"), (r.http, r.code))
        r = execute(rt, "treasury.transfer", {"from_account": "P1:TREASURY", "to_account": P1_USX, "currency": "USD", "amount_c": 100})
        self.assertEqual((400, "LIFETIME_CLOSED"), (r.http, r.code))
        # (7) L23 holds (whole-DB, both modes) and the closing lifetime's own ledger run is clean
        assert_integrity(self, rt, "ledger")
        full = rt.accounting.integrity("full")
        self.assertTrue(full.ok, [p.to_dict() for p in full.problems])
        self.assertEqual([], [p.to_dict() for p in full.problems if p.id == "L23"])
        self.assertTrue(rt.accounting.integrity("ledger", lifetime_id="P1").ok)
        self.assertFalse(rt.accounting.is_held("P2"))
        # (8) lower-case confirm -> 400 CONFIRM and nothing changes; a replay of the reset id returns the stored result: no P3
        r = reset(rt, confirm="reset")
        self.assertEqual((400, "CONFIRM"), (r.http, r.code))
        r = reset(rt, request_id=RESET_RID)
        self.assertTrue(r.ok, r.body)
        self.assertTrue(r.replayed)
        self.assertEqual(body, r.body)
        self.assertEqual(["F1", "I1", "P1", "P2"], [l["lifetime_id"] for l in q(rt, "SELECT lifetime_id FROM lifetimes ORDER BY lifetime_id")])
        self.assertEqual(1, one(rt, "SELECT COUNT(*) n FROM commands WHERE request_id='init:capital:P2'")["n"])
        # (9) nothing was deleted: every table keeps its rows (the reset only adds)
        after = counts(rt)
        for t in TABLES:
            self.assertGreaterEqual(after[t], before[t], t)
        # the only ledger rows added are the P2 capital entry (1 entry, 2 lines, 1 INITIAL_CAPITAL transfer); the cancel adds 1 order event;
        # fills, lots, orders, settlements and the shared reference data (instruments, quote_cache, marks) are untouched
        for t in ("fills", "lots", "orders", "settlements", "instruments", "quote_cache", "marks"):
            self.assertEqual(before[t], after[t], t)
        self.assertEqual((before["journal_entries"] + 1, before["journal_lines"] + 2, before["transfers"] + 1, before["order_events"] + 1),
                         (after["journal_entries"], after["journal_lines"], after["transfers"], after["order_events"]))
        self.assertEqual((before["accounts"] + 5, before["lifetimes"] + 1, before["nav_snapshots"] + 6), (after["accounts"], after["lifetimes"], after["nav_snapshots"]))
        # the reset and the follow-up capital command: +2 commands; the INFO attention LIFETIME_RESET
        self.assertEqual(before["commands"] + 2, after["commands"])
        self.assertEqual(1, one(rt, "SELECT COUNT(*) n FROM attention WHERE code='LIFETIME_RESET' AND severity='INFO'")["n"])
        # the only ERROR attention is the bus's COMMAND_FAILED for the ACCOUNT_CLOSED probe above (a refused write into the archive is loud);
        # the reset itself raised none
        errs = q(rt, "SELECT code, message FROM attention WHERE severity='ERROR'")
        self.assertEqual(["COMMAND_FAILED"], [e["code"] for e in errs], errs)
        self.assertIn("ACCOUNT_CLOSED", errs[0]["message"])

    def test_history_read_model_and_capital_flows(self):
        rt = self.rt
        # P1: deposit 50,000.00 then withdraw 20,000.00 -> NAV 1,030,000.00; deposits 50,000.00, withdrawals 20,000.00;
        # final P&L = 1,030,000 - 1,000,000 - 50,000 + 20,000 = 0.00
        self.assertTrue(deposit(rt, 5000000, lifetime="P1").ok)
        self.assertTrue(execute(rt, "treasury.withdraw", {"lifetime_id": "P1", "amount_c": 2000000}).ok)
        self.assertEqual(103000000, rt.accounting.nav("P1:ALL").nav_c)
        h = lifetimes.history(rt, "P1")
        self.assertEqual(("OPEN", False, None, None, 5000000, 2000000, 0), (h.status, h.read_only, h.final_nav_c, h.ended, h.deposits_c, h.withdrawals_c, h.orders_cancelled_at_reset))
        r = reset(rt, cap_c=100000000, request_id="ui:reset-p1-hist-01", name="Second run")
        self.assertTrue(r.ok, r.body)
        h = lifetimes.history(rt, "P1")
        self.assertEqual(("CLOSED", True, 103000000, 0, 0, 5000000, 2000000, "2026-10-20", "2026-10-20", "lifetime reset", True, []),
                         (h.status, h.read_only, h.final_nav_c, h.final_pnl_c, h.final_realised_c, h.deposits_c, h.withdrawals_c, h.ended, h.snapshot_date, h.reason,
                          h.integrity_ok, h.integrity_failing))
        self.assertEqual(5, len(h.accounts))
        self.assertTrue(all(a["status"] == "CLOSED" for a in h.accounts))
        self.assertEqual("#/h/P1/portfolio", h.links["portfolio"])
        self.assertEqual("/api/history/P1/export/transactions.csv", h.links["api"]["export_transactions"])
        self.assertEqual("/api/history/P1/accounting/pnl", h.links["api"]["accounting_pnl"])
        d = h.to_dict()
        self.assertEqual(("P1", "P", 1), (d["lifetime_id"], d["book_code"], d["seq"]))
        # the new lifetime carries the typed name; the listing is newest first
        p2 = lifetimes.history(rt, "P2")
        self.assertEqual(("Second run", "OPEN", False, None, 100000000), (p2.name, p2.status, p2.read_only, p2.final_nav_c, p2.starting_capital_c))
        self.assertEqual(["P2", "P1"], [v.lifetime_id for v in lifetimes.lifetimes_of(rt, "P")])
        with self.assertRaises(RejectError) as cm:
            lifetimes.history(rt, "P9")
        self.assertEqual("LIFETIME_UNKNOWN", cm.exception.code)
        with self.assertRaises(RejectError) as cm:
            lifetimes.lifetimes_of(rt, "Z")
        self.assertEqual("BOOK_UNKNOWN", cm.exception.code)
        # reset refusals: unknown book, missing amount, no open lifetime after a manual close
        r = reset(rt, code="Z")
        self.assertEqual((400, "BOOK_UNKNOWN"), (r.http, r.code))
        r = reset(rt, cap_c=0)
        self.assertEqual((400, "AMOUNT"), (r.http, r.code))
        with rt.db.writer() as tx:
            tx.x("UPDATE lifetimes SET status='CLOSED', ended_ts=? WHERE lifetime_id='P2'", (tx.ts,))
            tx.x("UPDATE accounts SET status='CLOSED', closed_ts=? WHERE lifetime_id='P2'", (tx.ts,))
        r = reset(rt)
        self.assertEqual((409, "NO_OPEN_LIFETIME"), (r.http, r.code))

    def test_integrity_failure_at_reset_is_recorded_not_blocking(self):
        rt = self.rt
        # a corrupted P1 ledger (gl_balances one cent off, written outside any command): L3 / L4 fail for P1 only
        with rt.db.writer() as tx:
            tx.x("UPDATE gl_balances SET balance_c = balance_c + 1, base_c = base_c + 1 WHERE account_id='P1:TREASURY' AND gl='1010' AND currency='USD'")
        self.assertFalse(rt.accounting.integrity("ledger", lifetime_id="P1").ok)
        before = counts(rt)
        r = reset(rt, request_id="ui:reset-p1-corrupt-01")
        self.assertTrue(r.ok, r.body)                                     # recorded, not blocking
        self.assertFalse(r.body["integrity_ok"])
        self.assertIn("L3", r.body["integrity_failing"])
        lt = one(rt, "SELECT status, integrity_json, final_nav_c FROM lifetimes WHERE lifetime_id='P1'")
        rep = json.loads(lt["integrity_json"])
        self.assertEqual(("CLOSED", False), (lt["status"], rep["ok"]))
        self.assertIn("L3", {p["id"] for p in rep["problems"] if p["severity"] == "ERROR"})
        # the NAV is still the ledger's (1,000,000.01 on the corrupted cash row: the figure is reported as it stands, never adjusted)
        self.assertEqual(100000001, lt["final_nav_c"])
        att = q(rt, "SELECT severity, code, ref_id, resolved_ts FROM attention WHERE code='LIFETIME_RESET_INTEGRITY'")
        self.assertEqual([("ERROR", "LIFETIME_RESET_INTEGRITY", "P1", None)], [(a["severity"], a["code"], a["ref_id"], a["resolved_ts"]) for a in att])
        h = lifetimes.history(rt, "P1")
        self.assertEqual((False, True), (h.integrity_ok, "L3" in h.integrity_failing))
        # P2 opened with its capital and is not held; the whole-DB run keeps reporting P1 (never hidden) while P2 stays clean
        self.assertEqual(100000000, bal(rt, "P2:TREASURY", "1010"))
        self.assertEqual("OPEN", one(rt, "SELECT status FROM lifetimes WHERE lifetime_id='P2'")["status"])
        self.assertFalse(rt.accounting.is_held("P2"))
        rep = rt.accounting.integrity("ledger")
        self.assertFalse(rep.ok)
        self.assertEqual({"P1"}, {p.lifetime_id for p in rep.problems if p.severity == "ERROR"})
        self.assertFalse(rt.accounting.is_held("P2"))
        after = counts(rt)
        for t in TABLES:
            self.assertGreaterEqual(after[t], before[t], t)
        self.assertEqual(before["accounts"] + 5, after["accounts"])


class OnboardingAndSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-lt-ob-")
        self.rt = runtime(self.tmp)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_onboarding_agreements(self):
        rt = self.rt
        # P1 (NAV 1,000,000 < 25,000,000, INDIVIDUAL) -> NOT_ELIGIBLE, nothing written
        r = onboard(rt, "GS", "P1")
        self.assertEqual((400, "NOT_ELIGIBLE"), (r.http, r.code))
        self.assertEqual(0, one(rt, "SELECT COUNT(*) n FROM agreements")["n"])
        self.assertIsNone(one(rt, "SELECT 1 FROM accounts WHERE account_id='P1:PB_GS'"))
        # F1 (FUND, 25,000,000) -> three agreements signed today (2026-10-20) + F1:PB_GS with its schedule copy
        r = onboard(rt, "GS", "F1", request_id="ui:onboard-f1-gs-01")
        self.assertTrue(r.ok, r.body)
        rows = q(rt, "SELECT lifetime_id, broker, kind, signed_date, command_id FROM agreements ORDER BY id")
        self.assertEqual([("F1", "GS", "ISDA_MASTER", "2026-10-20", r.command_id), ("F1", "GS", "CSA", "2026-10-20", r.command_id), ("F1", "GS", "PBA", "2026-10-20", r.command_id)],
                         [(a["lifetime_id"], a["broker"], a["kind"], a["signed_date"], a["command_id"]) for a in rows])
        self.assertEqual(["ISDA_MASTER", "CSA", "PBA"], [a["kind"] for a in r.body["agreements"]])
        self.assertTrue(all(a["meaning"] for a in r.body["agreements"]))
        acct = one(rt, "SELECT kind, broker, status, lifetime_id FROM accounts WHERE account_id='F1:PB_GS'")
        self.assertEqual(("PB", "GS", "OPEN", "F1"), (acct["kind"], acct["broker"], acct["status"], acct["lifetime_id"]))
        self.assertIsNotNone(one(rt, "SELECT 1 FROM pb_schedules WHERE account_id='F1:PB_GS'"))
        self.assertEqual(("F1:PB_GS", True, "2026-10-20"), (r.body["account_id"], r.body["created"], r.body["signed_date"]))
        self.assertTrue(books.eligibility(rt, "F1").has_relationships)
        self.assertEqual(1, one(rt, "SELECT COUNT(*) n FROM events WHERE type='PB_ONBOARDED' AND account_id='F1:PB_GS'")["n"])
        # replay (same request id) -> the stored result; a new request id for the same broker -> the same rows, created False, no 409
        r2 = onboard(rt, "GS", "F1", request_id="ui:onboard-f1-gs-01")
        self.assertTrue(r2.replayed)
        self.assertEqual(r.body, r2.body)
        r3 = onboard(rt, "gs", "F1")
        self.assertTrue(r3.ok, r3.body)
        self.assertFalse(r3.body["created"])
        self.assertEqual([a["id"] for a in r.body["agreements"]], [a["id"] for a in r3.body["agreements"]])
        self.assertEqual(3, one(rt, "SELECT COUNT(*) n FROM agreements")["n"])
        self.assertEqual(1, one(rt, "SELECT COUNT(*) n FROM accounts WHERE lifetime_id='F1' AND kind='PB'")["n"])
        # book_code form, lower-case broker: I1 (INSTITUTION) -> I1:PB_JPM; unknown broker -> UNKNOWN_BROKER before any write
        r = onboard(rt, "jpm", book_code="I")
        self.assertTrue(r.ok, r.body)
        self.assertEqual("I1:PB_JPM", r.body["account_id"])
        self.assertEqual(3, one(rt, "SELECT COUNT(*) n FROM agreements WHERE lifetime_id='I1' AND broker='JPM'")["n"])
        r = onboard(rt, "XYZ", "I1")
        self.assertEqual((400, "UNKNOWN_BROKER"), (r.http, r.code))
        self.assertEqual(6, one(rt, "SELECT COUNT(*) n FROM agreements")["n"])
        # P1 reaches the threshold by a 24,000,000.00 deposit -> NAV 25,000,000.00 -> onboarding allowed
        self.assertTrue(deposit(rt, 2400000000, lifetime="P1").ok)
        r = onboard(rt, "MS", "P1")
        self.assertTrue(r.ok, r.body)
        self.assertEqual("P1:PB_MS", r.body["account_id"])
        # a reset closes the PB account with the lifetime; the new lifetime has no PB accounts and no relationships
        r = reset(rt, code="F", cap_c=2500000000, request_id="ui:reset-f1-00001")
        self.assertTrue(r.ok, r.body)
        self.assertEqual("CLOSED", one(rt, "SELECT status FROM accounts WHERE account_id='F1:PB_GS'")["status"])
        self.assertEqual(0, one(rt, "SELECT COUNT(*) n FROM accounts WHERE lifetime_id='F2' AND kind='PB'")["n"])
        e = books.eligibility(rt, "F2")
        self.assertEqual((2500000000, True, False, True), (e.nav_c, e.pb_otc, e.has_relationships, e.can_onboard))
        r = onboard(rt, "MS", "F1")
        self.assertEqual((400, "LIFETIME_CLOSED"), (r.http, r.code))
        assert_integrity(self, rt, "ledger")

    def test_book_defaults_apply_to_the_next_lifetime_only(self):
        rt = self.rt
        # 2,000,000.00 default for P: P1 keeps 1,000,000.00; the next reset of P (no typed capital) starts at 2,000,000.00
        r = execute(rt, "book.update_settings", {"book_defaults": {"P": {"starting_capital_c": 200000000}}})
        self.assertTrue(r.ok, r.body)
        # the seed holds a row per book (name + seed capital); only P's capital changes, its seeded name stays
        self.assertEqual({"name": "Personal Book", "starting_capital_c": 200000000}, r.body["after"]["book_defaults"]["P"])
        self.assertEqual({"name": "Fund Book", "starting_capital_c": 2500000000}, r.body["after"]["book_defaults"]["F"])
        self.assertEqual(100000000, bal(rt, "P1:TREASURY", "1010"))
        self.assertEqual(1, one(rt, "SELECT COUNT(*) n FROM events WHERE type='SETTINGS_CHANGED'")["n"])
        r = reset(rt, cap_c=None, request_id="ui:reset-p1-default-1")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(200000000, r.body["opened"]["starting_capital_c"])
        self.assertEqual(200000000, bal(rt, "P2:TREASURY", "1010"))
        self.assertEqual(200000000, one(rt, "SELECT starting_capital_c c FROM lifetimes WHERE lifetime_id='P2'")["c"])
        self.assertEqual(100000000, bal(rt, "P1:TREASURY", "1010"))
        # a new default (3,000,000.00, decimal form) and a name: the book's display name changes at once, P2 is untouched
        r = execute(rt, "book.update_settings", {"book_defaults": {"P": {"starting_capital": "3000000.00", "name": "Personal Book (v3)"}}})
        self.assertTrue(r.ok, r.body)
        self.assertEqual({"P": "Personal Book (v3)"}, r.body["after"]["book_names"])
        self.assertEqual("Personal Book (v3)", one(rt, "SELECT name FROM books WHERE book_code='P'")["name"])
        p2 = one(rt, "SELECT starting_capital_c c, name FROM lifetimes WHERE lifetime_id='P2'")
        self.assertEqual((200000000, "Personal Book #2"), (p2["c"], p2["name"]))
        self.assertEqual(200000000, bal(rt, "P2:TREASURY", "1010"))
        r = reset(rt, cap_c=None, request_id="ui:reset-p2-default-1")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("P3", "Personal Book (v3) #3", 300000000), (r.body["opened"]["lifetime_id"], r.body["opened"]["name"], r.body["opened"]["starting_capital_c"]))
        self.assertEqual(300000000, bal(rt, "P3:TREASURY", "1010"))
        # a typed capital wins over the default: P4 with 1,500,000.00; ALL_BOOKS = 1,500,000 + 25,000,000 + 1,000,000,000 = 1,026,500,000.00
        r = reset(rt, cap_c=150000000, request_id="ui:reset-p3-typed-1")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(150000000, bal(rt, "P4:TREASURY", "1010"))
        self.assertEqual(102650000000, rt.accounting.nav("ALL_BOOKS").nav_c)
        # thresholds: PB/OTC 5,000,000.00 and the INDIVIDUAL QP threshold 1,000,000.00 are read by eligibility at once
        r = execute(rt, "book.update_settings", {"pb_otc_threshold": "5000000.00", "qp_threshold": {"INDIVIDUAL": "1000000.00"}})
        self.assertTrue(r.ok, r.body)
        self.assertEqual((500000000, 2500000000), (r.body["after"]["pb_otc_threshold_c"], r.body["before"]["pb_otc_threshold_c"]))
        self.assertEqual({"INDIVIDUAL": 100000000, "FUND": 2500000000, "INSTITUTION": 0}, r.body["after"]["qp_threshold_c"])
        e = books.eligibility(rt, "P4")
        self.assertEqual((150000000, 500000000, False, 100000000, True), (e.nav_c, e.threshold_c, e.pb_otc, e.qp_threshold_c, e.private_markets))
        # refusals: nothing given, unknown book, unknown profile, a non-amount
        for payload, code in (({}, "NOTHING_TO_UPDATE"), ({"book_defaults": {"Z": {"starting_capital_c": 1}}}, "BOOK_UNKNOWN"),
                              ({"qp_threshold": {"TRUST": "1.00"}}, "SETTING_VALUE"), ({"pb_otc_threshold": "lots"}, "SETTING_VALUE"),
                              ({"book_defaults": {"P": {"starting_capital_c": 0}}}, "AMOUNT")):
            r = execute(rt, "book.update_settings", payload)
            self.assertEqual((400, code), (r.http, r.code), payload)
        assert_integrity(self, rt, "ledger")


class LifetimeRouteTests(unittest.TestCase):
    """The §15 routes through the server runtime (WP-11): the real registration path, the codec, read_only history."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-lt-api-")
        os.environ[config.HOME_ENV] = self.tmp
        os.environ[config.DB_ENV] = os.path.join(self.tmp, "terminal.db")
        clock = FakeClock(AT)
        self.rt = _server.build_runtime(self.tmp, os.path.join(self.tmp, "terminal.db"), clock=clock, offline=True, fake_provider=FakeProvider(clock), log=_Log(),
                                        books=list(config.BOOKS), fetch_threads=0, news_enabled=False)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def handle(self, method, path, body=None, headers=None):
        return self.rt.router.handle(method, path, None, body, "127.0.0.1", headers)

    def test_reset_onboard_lifetimes_and_history_routes(self):
        rt = self.rt
        self.assertEqual("bound", rt.modules.get("lifetimes"), rt.modules)
        status, out, _ = self.handle("POST", "/api/books/P/reset", {"request_id": "ui:api-reset-p-0001", "confirm": "reset", "starting_capital": "1000000.00"})
        self.assertEqual((400, "CONFIRM"), (status, out["error"]["code"]))
        status, out, headers = self.handle("POST", "/api/books/P/reset", {"request_id": "ui:api-reset-p-0002", "confirm": "RESET", "starting_capital": "1000000.00"})
        self.assertEqual(200, status, out)
        # P1 at 1,000,000.00 with nothing traded: final NAV 1,000,000.00, P&L 0.00; P2 opened with 1,000,000.00 (decimal strings through the codec)
        self.assertEqual(("P1", "CLOSED", "1000000.00", "0.00", True), (out["closed"]["lifetime_id"], out["closed"]["status"], out["closed"]["final_nav"], out["closed"]["final_pnl"], out["closed"]["read_only"]))
        self.assertEqual(("P2", ["P2:TREASURY", "P2:US_EXCHANGE", "P2:DEBT", "P2:CREDIT", "P2:FOREIGN_FX"], "1000000.00"),
                         (out["opened"]["lifetime_id"], out["opened"]["accounts"], out["opened"]["starting_capital"]))
        self.assertNotIn("X-Replayed", headers)
        with rt.db.reader() as conn:
            self.assertEqual(100000000, conn.execute("SELECT balance_c FROM gl_balances WHERE account_id='P2:TREASURY' AND gl='1010' AND currency='USD'").fetchone()[0])
            self.assertIsNotNone(conn.execute("SELECT 1 FROM commands WHERE request_id='init:capital:P2'").fetchone())
        status, out2, headers = self.handle("POST", "/api/books/P/reset", {"request_id": "ui:api-reset-p-0002", "confirm": "RESET", "starting_capital": "1000000.00"})
        self.assertEqual((200, "1"), (status, headers.get("X-Replayed")))
        self.assertEqual(out, out2)
        # the listing: newest first, P1 with its stored figures and integrity verdict; the sidebar row points at P2 with one past lifetime
        status, lt, _ = self.handle("GET", "/api/books/P/lifetimes")
        self.assertEqual(200, status, lt)
        self.assertEqual(["P2", "P1"], [r["lifetime_id"] for r in lt["lifetimes"]])
        p1 = lt["lifetimes"][1]
        self.assertEqual(("CLOSED", "2026-10-20", "1000000.00", "0.00", "lifetime reset", True), (p1["status"], p1["ended"], p1["final_nav"], p1["final_pnl"], p1["reason"], p1["integrity_ok"]))
        status, bk, _ = self.handle("GET", "/api/books")
        self.assertEqual(200, status, bk)
        prow = [b for b in bk if b["book_code"] == "P"][0]
        self.assertEqual(("P2", 1), (prow["lifetime"]["lifetime_id"], prow["past_lifetimes"]))
        # every History route of the archived lifetime answers read_only: true (the CSV exports answer text)
        for path in ("positions", "portfolio", "accounting/balance-sheet", "accounting/pnl", "accounting/trial-balance", "accounting/journal", "accounting/integrity", "activity"):
            status, body, _ = self.handle("GET", f"/api/history/P1/{path}")
            self.assertEqual(200, status, (path, body))
            self.assertTrue(body.get("read_only"), path)
            self.assertEqual("P1:ALL", body.get("scope"), path)
            self.assertEqual("2026-10-20", body.get("frozen_at"), path)
        for path in ("export/transactions.csv", "export/positions.csv"):
            status, body, headers = self.handle("GET", f"/api/history/P1/{path}")
            self.assertEqual(200, status, (path, body))
            self.assertIsInstance(body, str)
            self.assertIn("text/csv", headers.get("Content-Type", ""))
        status, body, _ = self.handle("GET", "/api/history/P1/accounting/pnl?period=ITD")
        self.assertEqual((200, True), (status, body["read_only"]))
        # onboarding through the route: F1 ok with three agreements; P2 refused NOT_ELIGIBLE; unknown broker refused before any write
        status, ob, _ = self.handle("POST", "/api/books/F/onboard", {"request_id": "ui:api-onboard-f-0001", "broker": "GS"})
        self.assertEqual(200, status, ob)
        self.assertEqual((["ISDA_MASTER", "CSA", "PBA"], "F1:PB_GS", True), ([a["kind"] for a in ob["agreements"]], ob["account_id"], ob["created"]))
        status, out, _ = self.handle("POST", "/api/books/P/onboard", {"request_id": "ui:api-onboard-p-0001", "broker": "GS"})
        self.assertEqual((400, "NOT_ELIGIBLE"), (status, out["error"]["code"]))
        status, out, _ = self.handle("POST", "/api/books/P/onboard", {"request_id": "ui:api-onboard-p-0002", "broker": "XYZ"})
        self.assertEqual((400, "UNKNOWN_BROKER"), (status, out["error"]["code"]))
        with rt.db.reader() as conn:
            self.assertEqual(3, conn.execute("SELECT COUNT(*) FROM agreements").fetchone()[0])
        # the archived lifetime refuses writes through the API too
        status, out, _ = self.handle("POST", "/api/transfers", {"request_id": "ui:api-xfer-p1-0001", "from_account": "P1:TREASURY", "to_account": "P1:US_EXCHANGE",
                                                                "currency": "USD", "amount": "1.00"})
        self.assertEqual((400, "LIFETIME_CLOSED"), (status, out["error"]["code"]))
        rep = rt.accounting.integrity("ledger")
        self.assertTrue(rep.ok, [p.to_dict() for p in rep.problems])


if __name__ == "__main__":
    unittest.main()
