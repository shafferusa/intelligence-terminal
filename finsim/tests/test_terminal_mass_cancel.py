"""1.0.7.2 mass cancel (CHANGE_PLAN 4.1): ``POST /api/orders/cancel[?book=]`` with explicit order ids - the whole batch is checked before
the first cancel, then one ``order.cancel`` command per target in the given order (an all-or-none package once, a sequential leg that
ended with its primary reported with it), and a replay of the batch cancels nothing twice.  Nothing touches the network or ~/.finsim.

The world: two real books built by ``server.build_runtime`` offline (the Personal Book P1, USD 1,000,000.00 in P1:TREASURY, and the
Fund Book F1, USD 25,000,000.00), Tue 2026-10-20 14:00 ET (US equities and equity options open).  ACME (print 100.00) and WIDG
(print 50.00) are fictional listings, each observed 2 minutes ago on a completed 1-minute bar of 50,000 shares; ACME's Cboe chain is
invented, 20 contracts displayed on every bid and ask.  The orders, all sent through ``POST /api/orders`` as the ticket and the Smart
Ticket send them:
  P1:US_EXCHANGE  pA BUY 10 ACME LIMIT 90.00 and pB BUY 10 WIDG LIMIT 40.00 (below the prints: they rest); an all-or-none package
                  BTO 30 ACME Dec 100 call + STO 30 Dec 105 call (30 > 20 displayed: it works unfilled), legs pL1 / pL2; a sequential
                  package BUY 100 ACME LIMIT 95.00 (the primary pS, resting) + STO 1 Dec 105 call (the leg pH, waiting for it); pF BUY 5
                  WIDG MARKET, filled at 50.00 (5 <= 10 % of the bar).
  F1:US_EXCHANGE  fA BUY 10 ACME LIMIT 91.00 and fB BUY 20 WIDG LIMIT 41.00 (resting).
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from datetime import date
from decimal import Decimal

from tests.terminal_helpers import _Log, assert_integrity
from finsim.terminal import config, server as _server
from finsim.terminal.clock import FakeClock
from finsim.terminal.instruments import occ_symbol
from finsim.terminal.quotes import Bar1m, QuoteRecord
from finsim.terminal.quotes.cache import records_from_chain
from finsim.terminal.quotes.providers import ChainRow, ChainSnapshot, ChainUnderlying, FakeProvider
from finsim.terminal.units import qprice

AT = "2026-10-20T14:00:00-04:00"
P_ACCT, F_ACCT = "P1:US_EXCHANGE", "F1:US_EXCHANGE"
E1 = "2026-12-18"
DEFAULT_REASON = "cancelled by the owner"


def acme(right: str, strike) -> str:
    return occ_symbol("ACME", date.fromisoformat(E1), right, Decimal(str(strike)))


def build(tmp: str):
    os.environ[config.HOME_ENV] = tmp
    os.environ[config.DB_ENV] = os.path.join(tmp, "terminal.db")
    clock = FakeClock(AT)
    return _server.build_runtime(tmp, os.path.join(tmp, "terminal.db"), clock=clock, offline=True, fake_provider=FakeProvider(clock), log=_Log(),
                                 books=[config.BOOKS[0], config.BOOKS[1]], fetch_threads=0, news_enabled=False)


def add_name(rt, sym: str) -> None:
    with rt.db.writer() as tx:
        rt.instruments.upsert(tx, instrument_id=sym, kind="EQUITY", symbol=sym, name=f"{sym} test listing", currency="USD", session_group="US_EQUITY",
                              account_kinds=["US_EXCHANGE", "PB"], yahoo_symbol=sym, cboe_symbol=sym, spec={"exchange": "NYQ"})


def print_of(rt, sym: str, last: str) -> None:
    now = rt.clock.epoch()
    px = qprice(last)
    bar = Bar1m(now - 180, px, px, px, px, 50000)
    rt.cache.put(QuoteRecord(instrument_id=sym, provider="yahoo", source=f"Yahoo Finance chart ({sym})", method="direct", last=px, quote_time=now - 120,
                             fetched_at=now, delay_s=900, delay_label="up to 15 min delayed", currency="USD", session_group="US_EQUITY", bar=bar,
                             bars_tail=[bar]), now)


def load_acme_chain(rt) -> None:
    now = rt.clock.epoch()
    rows = [ChainRow(acme(r, k), E1, r, qprice(k), qprice(b), qprice(a), 20, 20, None, None, 0.30, None, None, None, None, 100, 10)
            for r, k, b, a in (("C", 100, "4.80", "5.00"), ("C", 105, "2.60", "2.75"))]
    snap = ChainSnapshot("ACME", "ACME", ChainUnderlying("ACME", qprice("100.00"), None, None, None, None, now - 60, None, 0.30), rows, now, now - 60, len(rows))
    rt.fp.set_chain("ACME", snap)
    rt.chains.put(snap)
    for r in records_from_chain(snap, rt.instruments):
        rt.cache.put(r, now)


class MassCancelBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-masscancel-")
        rt = self.rt = build(self.tmp)
        for sym, px in (("ACME", "100.00"), ("WIDG", "50.00")):
            add_name(rt, sym)
            print_of(rt, sym, px)
        load_acme_chain(rt)
        self.pA = self.order(P_ACCT, "ACME", "BUY", 10, "90.00", "ui:mc-test-pA", "P")
        self.pB = self.order(P_ACCT, "WIDG", "BUY", 10, "40.00", "ui:mc-test-pB", "P")
        st, out, _h = self.post("/api/orders?book=P", {"account_id": P_ACCT, "tif": "DAY", "package": {"strategy_key": "CUSTOM", "legs": [
            {"instrument_id": acme("C", 100), "side": "BTO", "qty": 30}, {"instrument_id": acme("C", 105), "side": "STO", "qty": 30}]}}, "st:0123456789ab:C1:r1")
        self.assertEqual((200, "WORKING", "AON_SIZE"), (st, out["package"]["status"], out["decision"]["reason"]), out)
        self.aon = out["package"]["package_id"]
        self.pL1, self.pL2 = (o["order_id"] for o in out["orders"])
        st, out, _h = self.post("/api/orders?book=P", {"account_id": P_ACCT, "tif": "DAY", "package": {"strategy_key": "CUSTOM", "legs": [
            {"instrument_id": "ACME", "side": "BUY", "qty": 100, "order_type": "LIMIT", "limit_px": "95.00"},
            {"instrument_id": acme("C", 105), "side": "STO", "qty": 1}]}}, "st:0123456789ab:C2:r1")
        self.assertEqual((200, "SEQUENTIAL"), (st, out["package"]["fill_mode"]), out)
        self.seq = out["package"]["package_id"]
        self.pS = out["order"]["order_id"]
        self.pH = self.one("SELECT order_id FROM orders WHERE parent_order_id=?", (self.pS,))["order_id"]
        st, out, _h = self.post("/api/orders?book=P", {"account_id": P_ACCT, "instrument_id": "WIDG", "side": "BUY", "qty": 5, "order_type": "MARKET",
                                                       "tif": "DAY"}, "ui:mc-test-pF")
        self.assertEqual((200, "FILLED"), (st, out["order"]["status"]), out)
        self.pF = out["order"]["order_id"]
        self.fA = self.order(F_ACCT, "ACME", "BUY", 10, "91.00", "ui:mc-test-fA", "F")
        self.fB = self.order(F_ACCT, "WIDG", "BUY", 20, "41.00", "ui:mc-test-fB", "F")
        self.assertEqual({self.pA: "WORKING", self.pB: "WORKING", self.pL1: "WORKING", self.pL2: "WORKING", self.pS: "WORKING", self.pH: "WAITING_PARENT",
                          self.pF: "FILLED", self.fA: "WORKING", self.fB: "WORKING"}, self.statuses())

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def post(self, path: str, body: dict, rid=None, headers=None):
        b = dict(body)
        if rid is not None:
            b["request_id"] = rid
        return self.rt.router.handle("POST", path, None, b, "127.0.0.1", headers)

    def order(self, acct, sym, side, qty, limit, rid, book) -> str:
        st, out, _h = self.post(f"/api/orders?book={book}", {"account_id": acct, "instrument_id": sym, "side": side, "qty": qty, "order_type": "LIMIT",
                                                             "limit_px": limit, "tif": "DAY"}, rid)
        self.assertEqual((200, "WORKING"), (st, out["order"]["status"]), out)
        return out["order"]["order_id"]

    def cancel(self, ids, batch, book=None, **extra):
        return self.post("/api/orders/cancel" + (f"?book={book}" if book else ""), dict({"order_ids": ids}, **extra), batch)

    def q(self, sql: str, params=()):
        with self.rt.db.reader() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params=()):
        return self.q(sql, params)[0]

    def statuses(self) -> dict:
        return {r["order_id"]: r["status"] for r in self.q("SELECT order_id, status FROM orders")}

    def cancels(self, batch: str) -> list:
        return sorted(r["request_id"] for r in self.q("SELECT request_id FROM commands WHERE kind='order.cancel' AND request_id LIKE ?", (batch + ":%",)))


class MassCancelTests(MassCancelBase):
    def test_a_every_check_comes_before_the_first_cancel(self):
        before = self.statuses()
        batch = "mc:0a0a0a0a0a0a"
        # an unknown id: 404 naming it, nothing cancelled (pA, listed first, is still working)
        st, out, _h = self.cancel([self.pA, "O-999999"], batch, book="P")
        self.assertEqual((404, "NOT_FOUND", ["O-999999"]), (st, out["error"]["code"], out["error"]["details"]["unknown"]))
        # an order of the Fund Book in a Personal Book batch: 409 before anything
        st, out, _h = self.cancel([self.pA, self.fA], batch, book="P")
        self.assertEqual((409, "BOOK_MISMATCH", [self.fA], "P"), (st, out["error"]["code"], out["error"]["details"]["orders"], out["error"]["details"]["selected"]))
        # the shape: duplicates, 501 ids, none, not a list, the reason
        st, out, _h = self.cancel([self.pA, self.pB, self.pA], batch)
        self.assertEqual((400, "BAD_REQUEST", [self.pA]), (st, out["error"]["code"], out["error"]["details"]["duplicates"]))
        st, out, _h = self.cancel([f"O-{n:06d}" for n in range(1, 502)], batch)
        self.assertEqual((400, "BAD_REQUEST", 500, 501), (st, out["error"]["code"], out["error"]["details"]["max"], out["error"]["details"]["got"]))
        for bad in ([], self.pA, [self.pA, 7], [" "]):
            st, out, _h = self.cancel(bad, batch)
            self.assertEqual((400, "BAD_REQUEST"), (st, out["error"]["code"]), bad)
        st, out, _h = self.cancel([self.pA], batch, reason="x" * 121)
        self.assertEqual((400, "BAD_REQUEST"), (st, out["error"]["code"]))
        st, out, _h = self.cancel([self.pA], batch, reason=12)
        self.assertEqual((400, "BAD_REQUEST"), (st, out["error"]["code"]))
        # the batch id: 61 characters leave no room for ":<id>" inside 80; 7 is below the request-id rule; none at all
        for bad_id in ("mc:" + "x" * 58, "mc:abcd"):
            st, out, _h = self.cancel([self.pA], bad_id)
            self.assertEqual((400, "BAD_REQUEST_ID"), (st, out["error"]["code"]), bad_id)
        st, out, _h = self.post("/api/orders/cancel", {"order_ids": [self.pA]})
        self.assertEqual((400, "BAD_REQUEST_ID"), (st, out["error"]["code"]))
        self.assertEqual(before, self.statuses())
        self.assertEqual([], self.q("SELECT request_id FROM commands WHERE kind='order.cancel'"))
        # a 60-character batch id is the longest: its cancel ids fit 80 (60 + ':' + 'O-000001' = 69)
        st, out, _h = self.cancel([self.pA], "mc:" + "y" * 57, book="P")
        self.assertEqual((200, [{"order_id": self.pA, "status": "CANCELLED"}]), (st, out["cancelled"]))
        assert_integrity(self, self.rt, "full")

    def test_b_cancel_selected_in_the_given_order_then_a_replay_cancels_nothing_twice(self):
        batch = "mc:b0b0b0b0b0b0"
        ids = [self.pA, self.pB, self.pL2, self.pS, self.pH, self.pF]
        st, out, h = self.cancel(ids, batch, book="P")
        self.assertEqual(200, st, out)
        self.assertNotIn("X-Replayed", h)
        self.assertEqual((batch, 6, False, []), (out["batch_id"], out["requested"], out["replayed"], out["failed"]))
        # pA, pB themselves; the all-or-none leg pL2 through ONE cancel of its package (pL1 goes with it, and the answer says so: 1.0.7.2
        # review R3, every order the batch ended is reported); the primary pS itself; its waiting leg pH ended with it (unfilled): reported
        # with its primary, no command of its own; pF had filled: skipped
        self.assertEqual([{"order_id": self.pA, "status": "CANCELLED"}, {"order_id": self.pB, "status": "CANCELLED"},
                          {"order_id": self.pL2, "status": "CANCELLED", "package_id": self.aon},
                          {"order_id": self.pL1, "status": "CANCELLED", "package_id": self.aon, "with": "package"},
                          {"order_id": self.pS, "status": "CANCELLED", "package_id": self.seq},
                          {"order_id": self.pH, "status": "CANCELLED", "with": "primary", "parent_order_id": self.pS, "package_id": self.seq}], out["cancelled"])
        self.assertEqual([(self.pF, "NOT_OPEN")], [(x["order_id"], x["code"]) for x in out["skipped"]])
        self.assertIn("FILLED", out["skipped"][0]["message"])
        self.assertEqual({self.pA: "CANCELLED", self.pB: "CANCELLED", self.pL1: "CANCELLED", self.pL2: "CANCELLED", self.pS: "CANCELLED",
                          self.pH: "CANCELLED", self.pF: "FILLED", self.fA: "WORKING", self.fB: "WORKING"}, self.statuses())
        # one command per target, ids <batch>:<order or package id>; the refused one (pF) left no row; pH needed none
        self.assertEqual(sorted(f"{batch}:{x}" for x in (self.pA, self.pB, self.aon, self.pS)), self.cancels(batch))
        reasons = {r["order_id"]: r["reason"] for r in self.q("SELECT order_id, reason FROM orders")}
        self.assertEqual((DEFAULT_REASON, DEFAULT_REASON, DEFAULT_REASON, "primary ended unfilled"),
                         (reasons[self.pA], reasons[self.pL1], reasons[self.pS], reasons[self.pH]))
        self.assertEqual("CANCELLED", self.one("SELECT status FROM hedge_packages WHERE package_id=?", (self.aon,))["status"])
        assert_integrity(self, self.rt, "full")
        # the same batch again (a lost answer resent): every accepted cancel is a replay - the same answer, X-Replayed, nothing new
        n_commands = self.one("SELECT COUNT(*) n FROM commands")["n"]
        st, again, h = self.cancel(ids, batch, book="P")
        self.assertEqual((200, "1", True), (st, h.get("X-Replayed"), again["replayed"]))
        self.assertEqual((out["cancelled"], out["skipped"], out["failed"]), (again["cancelled"], again["skipped"], again["failed"]))
        self.assertEqual(n_commands, self.one("SELECT COUNT(*) n FROM commands")["n"])
        # the same batch id with another reason reuses a stored id with another payload: each such cancel is refused, nothing changes
        st, other, _h = self.cancel([self.pA], batch, book="P", reason="another reason")
        self.assertEqual((200, [(self.pA, "REQUEST_ID_REUSED", 409)]), (st, [(x["order_id"], x["code"], x["http"]) for x in other["failed"]]))
        self.assertEqual(DEFAULT_REASON, self.one("SELECT reason FROM orders WHERE order_id=?", (self.pA,))["reason"])
        assert_integrity(self, self.rt, "full")

    def test_c_cancel_all_open_in_one_book(self):
        # what the Pending page does: every row of the book with a cancel action, then one batch for that book
        st, pend, _h = self.rt.router.handle("GET", "/api/blotter/pending?book=F&status=open&limit=5000", None, None, "127.0.0.1", None)
        self.assertEqual(200, st, pend)
        ids = [r["order_id"] for r in pend["rows"] if (r.get("actions") or {}).get("cancel")]
        self.assertEqual(sorted([self.fA, self.fB]), sorted(ids))
        st, out, _h = self.cancel(ids, "mc:c0c0c0c0c0c0", book="F", reason="flatten the fund book")
        self.assertEqual((200, sorted([self.fA, self.fB]), [], []), (st, sorted(x["order_id"] for x in out["cancelled"]), out["skipped"], out["failed"]))
        s = self.statuses()
        self.assertEqual(("CANCELLED", "CANCELLED"), (s[self.fA], s[self.fB]))
        self.assertEqual({"WORKING"}, {s[x] for x in (self.pA, self.pB, self.pL1, self.pL2, self.pS)})        # the Personal Book is untouched
        self.assertEqual({"flatten the fund book"}, {r["reason"] for r in self.q("SELECT reason FROM orders WHERE order_id IN (?,?)", (self.fA, self.fB))})
        st, pend, _h = self.rt.router.handle("GET", "/api/blotter/pending?book=F&status=open&limit=5000", None, None, "127.0.0.1", None)
        self.assertEqual([], [r for r in pend["rows"] if (r.get("actions") or {}).get("cancel")])
        assert_integrity(self, self.rt, "full")

    def test_d_an_all_or_none_package_is_cancelled_once(self):
        batch = "mc:d0d0d0d0d0d0"
        st, out, _h = self.cancel([self.pL1, self.pL2], batch)            # no ?book=: no book guard
        self.assertEqual(200, st, out)
        self.assertEqual([{"order_id": self.pL1, "status": "CANCELLED", "package_id": self.aon},
                          {"order_id": self.pL2, "status": "CANCELLED", "package_id": self.aon, "with": "package"}], out["cancelled"])
        self.assertEqual([f"{batch}:{self.aon}"], self.cancels(batch))            # ONE command for the package
        # once ended, the package has no open leg: a later batch skips its legs
        st, out, _h = self.cancel([self.pL1], "mc:d1d1d1d1d1d1")
        self.assertEqual((200, [], [(self.pL1, "NOT_OPEN")]), (st, out["cancelled"], [(x["order_id"], x["code"]) for x in out["skipped"]]))
        assert_integrity(self, self.rt, "full")

    def test_e_a_sequential_leg_alone_and_a_leg_listed_before_its_primary(self):
        # the waiting leg alone: cancelled by its own command, its primary keeps working
        st, out, _h = self.cancel([self.pH], "mc:e0e0e0e0e0e0", book="P")
        self.assertEqual((200, [{"order_id": self.pH, "status": "CANCELLED", "package_id": self.seq}]), (st, out["cancelled"]))
        self.assertEqual(("WORKING", "CANCELLED"), (self.statuses()[self.pS], self.statuses()[self.pH]))
        # a new buy-write, its leg listed BEFORE its primary: each cancelled by its own command, in that order
        st, pkg, _h = self.post("/api/orders?book=P", {"account_id": P_ACCT, "tif": "DAY", "package": {"strategy_key": "CUSTOM", "legs": [
            {"instrument_id": "ACME", "side": "BUY", "qty": 200, "order_type": "LIMIT", "limit_px": "94.00"},
            {"instrument_id": acme("C", 105), "side": "STO", "qty": 2}]}}, "st:0123456789ab:C3:r1")
        self.assertEqual(200, st, pkg)
        p2 = pkg["order"]["order_id"]
        h2 = self.one("SELECT order_id FROM orders WHERE parent_order_id=?", (p2,))["order_id"]
        batch = "mc:e1e1e1e1e1e1"
        st, out, _h = self.cancel([h2, p2], batch, book="P")
        self.assertEqual([h2, p2], [x["order_id"] for x in out["cancelled"]])
        self.assertEqual([], [x for x in out["cancelled"] if "with" in x])
        self.assertEqual(sorted([f"{batch}:{h2}", f"{batch}:{p2}"]), self.cancels(batch))
        self.assertEqual((DEFAULT_REASON, DEFAULT_REASON), tuple(self.one("SELECT reason FROM orders WHERE order_id=?", (x,))["reason"] for x in (h2, p2)))
        assert_integrity(self, self.rt, "full")


if __name__ == "__main__":
    unittest.main()
