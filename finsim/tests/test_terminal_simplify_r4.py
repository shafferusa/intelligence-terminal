"""FinSim 1.0.7.2 simplify review round 4 (on d509563): the regression tests of its findings, kept in the terminal suite.

The money path (the owner's rule R-2: a call is never sold as "covered" unless its shares are free when the order is accepted AND stay
reserved until the call is closed - for every order and every interleaving).  A short sale placed while the account was flat can still
be working when the account has bought the name; it is now counted against the shares held by EVERY route that lets them leave
(``orders_preview.cover_state``'s ``free``: a sale, an early put exercise, a loan, new calls), and ``execution.apply_fill`` never books
a short sale while the name is held long (SHORT_WHILE_LONG: the matcher suspends the resting order with that reason and one item):
  ShortSaleWorkingWhileHeldTests
    test_a  a sale of the shares the short sale is counted for is refused - the preview's blocker and the order's 400 OVERSELL, saying
            to cancel the short sale first; the short sale reaching its limit is suspended, never booked against the shares held, and
            the call sold as covered keeps them (tests/test_reviews4_money_path_short_sale.py test_a: d509563 accepted the sale and the
            short sale's fill netted the position to -50 under the call);
    test_a2 an early put exercise that would deliver them is refused (409 SHARES_RESERVED, naming the short sale) - the same review's
            test_a2;
    test_b  without a sale: the short sale reaching its limit is SUSPENDED SHORT_WHILE_LONG with one WARN item (d509563: the fill rolled
            back 500 INTEGRITY, one ERROR item per matcher pass), the call still covered; resumed while still long it is suspended
            again, one open item; cancelled, the shares are free to sell;
    test_c  holding fewer shares than the short sale sells: its fill is not booked either (d509563 booked it: a short lot opened beside
            the long ones, the account net short);
    test_d  a loan never lends them (``lending_accruals.free_to_lend``: NOT_ENOUGH_FREE names the short sales).
  ShortWalkSeedTests  the property walk's findings (tests/test_props4_money_path_short_walk.py, B2: the shares held net of the working
            sales and short sales cover every call sold as covered), each reduced to its orders: seed 4440403 step 103 (an early put
            exercise), seed 4440412 step 284 (a sale after an early call exercise) and seed 4440413 step 228 (a resting sale while the
            calls claim the rest) - each now refused, the property kept.
The page (the real offline server on port 0 with the Personal and the Fund books, the real pages in a headless Edge / Chrome):
  HandOffPageTests
    test_e  a covered call on a root with a digit (ACME1, an adjusted contract: FinSim's OCC grammar) opens in the order ticket sold as
            covered on the FIRST open - the card hands over the instrument's facts (right, underlying, expiry, strike) and the ticket
            reads FinSim's OCC grammar (tests/test_reviews4_autofill_covered_root_browser.py test_b: d509563 dropped the claim and its
            Submit would have sent a plain call); test_e2 the standard root (control);
    test_f  the FX desk opened from a Smart Ticket card starts a new deal: the amount in the pair's base currency, whatever unit was
            picked in the desk before (tests/test_reviews4_page_browser.py test_a: d509563 kept the USD unit); test_f2 a spot deal at the
            default tenor, the amount box empty when the card names none (test_b, test_c: d509563 kept the 6-month forward and the 5,000
            typed); test_f3 a desk built afresh and FinSim's own FX page (controls);
    test_g  the bond and the private-markets tickets start a new trade on a fresh hand-off too (the face / quantity, the price, the
            preview and the result of the trade they held are dropped).
Fictional ACME / WIDG only; temp homes; no ~/.finsim, no network, no port 8765 / 8865.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import unittest
from datetime import date
from decimal import Decimal
from urllib.parse import urlsplit

from tests import test_terminal_browser_smoke as SMK
from tests import test_terminal_simplify_r2 as R2
from tests.terminal_cdp import NO_BROWSER, Browser, find_browser
from tests.terminal_helpers import _Log, assert_integrity, execute
from tests.test_terminal_smart_submit import ACCT, acme, print_acme
from finsim.terminal import config, server as _server
from finsim.terminal.clock import FakeClock, epoch_of
from finsim.terminal.commands import Command, RejectError
from finsim.terminal.instruments import occ_symbol
from finsim.terminal.quotes import Bar1m, QuoteRecord
from finsim.terminal.quotes.cache import records_from_chain
from finsim.terminal.quotes.providers import ChainRow, ChainSnapshot, ChainUnderlying, FakeProvider
from finsim.terminal.units import qprice

C105J = R2.C105J                                   # ACME Jan 15 2027 105 call (bid 3.90)
C105, C110, C115 = R2.C105, R2.C110, R2.C115       # ACME Dec 18 2026 calls (bids 2.60 / 1.20 / 0.50)
P95 = acme("P", 95)                                # ACME Dec 18 2026 95 put (ask 2.25)


# ==== the money path ===================================================================================================================
class _ShortBase(R2._HedgeLegBase):
    """test_terminal_simplify_r2's world (Test Book T1, ACME at 100.00 on a 50,000-share bar, its invented chain), ACME with an invented
    ADV of 5,000,000 so a short sale is located; driven through the docked ticket's preview then order, the exercise route and the
    matcher."""

    def setUp(self):
        super().setUp()
        with self.rt.db.writer() as tx:
            self.rt.instruments.upsert(tx, instrument_id="ACME", kind="EQUITY", symbol="ACME", name="ACME test listing", currency="USD",
                                       session_group="US_EQUITY", account_kinds=["US_EXCHANGE", "PB"], yahoo_symbol="ACME", cboe_symbol="ACME",
                                       spec={"exchange": "NYQ", "adv": 5000000})

    def rid(self, what: str) -> str:
        self.k += 1
        return f"ui:s4-{what}-{self.k:04d}"

    def place(self, iid: str, side: str, qty: int, **kw) -> tuple:
        """An ordinary order from the docked ticket (its preview, then the order with the preview's id): (http, status or code, answer)."""
        req = dict({"account_id": ACCT, "instrument_id": iid, "side": side, "qty": qty, "order_type": "MARKET", "tif": "DAY"}, **kw)
        code, out = self.ticket(req, self.rid(side.lower()))
        return code, ((out.get("order") or {}).get("status") if code == 200 else (out.get("error") or {}).get("code")), out

    def hold(self, n: int) -> None:
        self.assertEqual((200, "FILLED"), self.place("ACME", "BUY", n)[:2])

    def short_sale(self, n: int, limit: str = "120.00") -> str:
        """SELL_SHORT ``n`` ACME limit ``limit`` (above the 100.00 print: it rests, its borrow located); its order id."""
        code, status, out = self.place("ACME", "SELL_SHORT", n, order_type="LIMIT", limit_px=limit)
        self.assertEqual((200, "WORKING"), (code, status), out)
        return out["order"]["order_id"]

    def covered(self, occ: str, n: int) -> tuple:
        return self.place(occ, "STO", n, covered=True)

    def acme_print(self, last: str) -> None:
        """The next completed 1-minute ACME bar printing ``last`` on 50,000 shares, then a matcher pass."""
        self.rt.clock.advance(60)
        print_acme(self.rt, last, 10, self.rt.clock.epoch() - 60, 50000)
        self.rt.matcher.on_quotes(["ACME"])

    def sale_blockers(self, qty: int) -> list:
        code, pv, _h = self.call("POST", "/api/orders/preview?book=T", {"account_id": ACCT, "instrument_id": "ACME", "side": "SELL", "qty": qty,
                                                                        "order_type": "MARKET", "tif": "DAY"})
        self.assertEqual(200, code, pv)
        return [b.get("code") for b in pv.get("blockers") or []]

    def exercise(self, occ: str, n: int) -> tuple:
        code, out, _h = self.call("POST", "/api/options/exercise?book=T", {"account_id": ACCT, "instrument_id": occ, "qty": n}, self.rid("exercise"))
        return code, out

    def cancel(self, oid: str) -> None:
        code, out, _h = self.call("DELETE", f"/api/orders/{oid}?book=T", {}, self.rid("cancel"))
        self.assertEqual(200, code, out)
        self.assertEqual("CANCELLED", self.row(oid)["status"])

    def margin_now(self) -> dict:
        """FinSim's margin of the short ACME calls recomputed now (the 17:10 OPTION_MARGIN_MARK, forced): {call: rule}."""
        r = execute(self.rt, "options.margin_mark", {"account_id": ACCT, "force": True}, request_id=self.rid("omargin").replace("ui:", "job:"), actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        rows = self.q("SELECT options_detail_json FROM margin_state WHERE account_id=?", (ACCT,))
        d = json.loads(rows[0]["options_detail_json"]) if rows else {}
        return {it["instrument_id"]: it["rule"] for g in d.get("groups") or [] for it in g.get("items") or [] if it["instrument_id"].startswith("ACME2")
                and it["instrument_id"][10] == "C"}

    def open_items(self) -> list:
        return [(r["severity"], r["code"], r["ref_id"]) for r in self.q("SELECT severity, code, ref_id FROM attention WHERE resolved_ts IS NULL ORDER BY id")]

    def kept(self) -> dict:
        """The walk's property B2: the shares held net of the working sales AND the working short sales cover every call sold as covered
        (whole contracts, one series here).  Returns the reading."""
        st = self.cover()
        claimed = sum(int(c["contracts"]) for c in st.get("claimed") or [])
        net = int(st["held"]) - int(st["working_sells"]) - int(st["working_shorts"])
        self.assertGreaterEqual(net // 100, claimed, st)
        return st

    def short_then_claim(self, held: int, short: int = 150, occ: str = C105J, calls: int = 1) -> str:
        """Flat: a resting short sale of ``short``, then ``held`` bought and ``calls`` sold as covered on what the short sale leaves free.
        Returns the short sale's order id."""
        self.assertEqual({}, self.positions())
        oid = self.short_sale(short)
        self.hold(held)
        st = self.cover()
        self.assertEqual((held, short, max(0, held - short)), (st["held"], st["working_shorts"], st["free"]))   # the short sale counted
        code, status, out = self.covered(occ, calls)
        self.assertEqual((200, "FILLED"), (code, status), out)
        self.assertEqual([(occ, calls)], [(c["instrument_id"], int(c["contracts"])) for c in self.cover().get("claimed") or []])
        return oid


class ShortSaleWorkingWhileHeldTests(_ShortBase):
    def test_a_a_sale_of_the_shares_a_working_short_sale_is_counted_for_is_refused(self):
        short = self.short_then_claim(250)
        st = self.cover()
        self.assertEqual((250, 150, 100, 0, [short]), (st["held"], st["working_shorts"], st["reserved"], st["free"], st["short_sales"]))
        self.assertEqual(["OVERSELL"], self.sale_blockers(150))           # the ticket's preview names the refusal
        code, status, out = self.place("ACME", "SELL", 150)
        self.assertEqual((400, "OVERSELL"), (code, status), out)
        msg = out["error"]["message"]
        self.assertIn("net of working sells and working short sales and shares covering calls is 0", msg)
        self.assertIn(f"; 150 ACME are counted for the short sale still working ({short}): a short sale is never booked against shares held,"
                      " so cancel it first", msg)
        self.assertEqual((150, [short]), (out["error"]["details"]["working_shorts"], out["error"]["details"]["short_sales"]))
        # ACME prints 120.00 (the short sale's limit): never booked against the shares held - suspended, the call keeps its shares
        self.acme_print("120.00")
        r = self.row(short)
        self.assertEqual(("SUSPENDED", 0), (r["status"], int(r["filled_qty"])), r)
        self.assertTrue(str(r["reason"]).startswith("SHORT_WHILE_LONG: SELL_SHORT 150 ACME: T1:US_EXCHANGE holds 250 ACME long"), r["reason"])
        self.assertEqual(({"ACME": 250, C105J: -1}, 0, {C105J: "covered_call"}), (self.positions(), self.uncovered(), self.margin_now()))
        self.kept()
        # cancelled, its shares are free to sell again (control)
        self.cancel(short)
        self.assertEqual((200, "FILLED"), self.place("ACME", "SELL", 150)[:2])
        self.assertEqual(({"ACME": 100, C105J: -1}, 0, {C105J: "covered_call"}), (self.positions(), self.uncovered(), self.margin_now()))
        assert_integrity(self, self.rt, "full")

    def test_a2_an_early_put_exercise_that_would_deliver_them_is_refused(self):
        short = self.short_then_claim(300)
        self.assertEqual((200, "FILLED"), self.place(P95, "BTO", 2)[:2])
        code, out = self.exercise(P95, 2)
        self.assertEqual((409, "SHARES_RESERVED"), (code, (out.get("error") or {}).get("code")), out)
        msg = out["error"]["message"]
        self.assertIn("would deliver 200 ACME, and of the 300 held 100 cover calls sold or being sold", msg)
        self.assertIn(f"150 are counted for the short sale still working ({short})", msg)
        self.assertIn(": 50 are free - buy back or cancel those calls or cancel the short sales first", msg)
        self.assertEqual((150, [short]), (out["error"]["details"]["working_shorts"], out["error"]["details"]["short_sales"]))
        self.assertEqual(({"ACME": 300, C105J: -1, P95: 2}, 0), (self.positions(), self.uncovered()))
        self.acme_print("120.00")
        self.assertEqual("SUSPENDED", self.row(short)["status"])
        self.assertEqual(({"ACME": 300, C105J: -1, P95: 2}, {C105J: "covered_call"}), (self.positions(), self.margin_now()))
        self.cancel(short)                                                # control: the short sale cancelled, the delivery fits
        code, out = self.exercise(P95, 2)
        self.assertEqual(200, code, out)
        self.assertEqual(({"ACME": 100, C105J: -1}, 0, {C105J: "covered_call"}), (self.positions(), self.uncovered(), self.margin_now()))
        assert_integrity(self, self.rt, "full")

    def test_b_a_short_sale_reaching_its_limit_while_the_name_is_held_is_suspended_never_booked(self):
        short = self.short_then_claim(250)
        self.assertEqual([], self.open_items())
        self.acme_print("120.00")
        r = self.row(short)
        self.assertEqual(("SUSPENDED", 0), (r["status"], int(r["filled_qty"])), r)
        # simplify round 5: the way out it says is the one FinSim allows (test_terminal_simplify_r5.ShortSaleAdviceTests)
        self.assertIn("a short sale is never booked against shares held: cancel it - while it works, the shares it is counted against cannot be sold"
                      " - then sell them and place the short sale again", r["reason"])
        self.assertEqual([("WARN", "ORDER_SUSPENDED", short)], self.open_items())     # one item - no ERROR INTEGRITY, no item per pass
        self.acme_print("120.00")
        self.assertEqual([("WARN", "ORDER_SUSPENDED", short)], self.open_items())
        self.assertEqual(({"ACME": 250, C105J: -1}, 0, {C105J: "covered_call"}), (self.positions(), self.uncovered(), self.margin_now()))
        self.assertEqual((0, 150), (self.cover()["free"], self.cover()["working_shorts"]))   # still counted while suspended
        code, out, _h = self.call("POST", f"/api/orders/{short}/resume?book=T", {}, self.rid("resume"))
        self.assertEqual(200, code, out)
        self.acme_print("120.00")                                         # resumed while still long: suspended again, one open item
        self.assertEqual(("SUSPENDED", 0), (self.row(short)["status"], int(self.row(short)["filled_qty"])))
        self.assertEqual([("WARN", "ORDER_SUSPENDED", short)], self.open_items())
        self.assertEqual({"ACME": 250, C105J: -1}, self.positions())
        assert_integrity(self, self.rt, "full")

    def test_c_holding_fewer_shares_than_the_short_sale_sells_its_fill_is_not_booked_either(self):
        short = self.short_sale(150)
        self.hold(100)
        code, status, out = self.covered(C105J, 1)                        # no share is free for a call: refused, nothing placed
        self.assertEqual((409, "NOT_COVERED"), (code, status), out)
        self.acme_print("120.00")
        r = self.row(short)
        self.assertEqual(("SUSPENDED", 0), (r["status"], int(r["filled_qty"])), r)
        self.assertTrue(str(r["reason"]).startswith("SHORT_WHILE_LONG: SELL_SHORT 150 ACME: T1:US_EXCHANGE holds 100 ACME long"), r["reason"])
        self.assertEqual({"ACME": 100}, self.positions())                 # d509563: a short lot of 150 beside the 100 long, net -50
        self.assertEqual([], self.q("SELECT lot_id FROM lots WHERE account_id=? AND instrument_id='ACME' AND side='SHORT'", (ACCT,)))
        assert_integrity(self, self.rt, "full")

    def test_d_a_loan_never_lends_the_shares_a_working_short_sale_is_counted_for(self):
        from finsim.terminal.lending_accruals import free_to_lend, lend_preview
        self.short_then_claim(250)
        with self.rt.db.reader() as conn:
            f = free_to_lend(self.rt, conn, ACCT, "ACME")
        self.assertEqual((250, 150, 100, 0), (f["position"], f["working_shorts"], f["reserved"], f["free"]))
        with self.assertRaises(RejectError) as cm:
            lend_preview(self.rt, {"account_id": ACCT, "instrument_id": "ACME", "qty": 100, "counterparty_id": "AGENT_BNY"})
        self.assertEqual("NOT_ENOUGH_FREE", cm.exception.code)
        self.assertIn("150 counted for short sales still working,", cm.exception.message)


class ShortWalkSeedTests(_ShortBase):
    """tests/test_props4_money_path_short_walk.py's findings on d509563 (B2), each reduced to the orders that made it."""

    def test_seed_4440403_step_103_an_early_put_exercise_with_a_short_sale_working(self):
        # 95 SELL_SHORT 100 limit (flat); 100 BUY 200; 101 STO 1 Dec 110 call covered (free for calls 100); 102 STO 3 more: NOT_COVERED;
        # 103 the early exercise of a Dec 95 put - d509563 accepted it: 100 held, the short sale's 100 counted against them, the call bare
        short = self.short_then_claim(200, short=100, occ=C110)
        self.assertEqual((409, "NOT_COVERED"), self.covered(C110, 3)[:2])
        self.assertEqual((200, "FILLED"), self.place(P95, "BTO", 1)[:2])
        code, out = self.exercise(P95, 1)
        self.assertEqual((409, "SHARES_RESERVED"), (code, (out.get("error") or {}).get("code")), out)
        self.assertIn(f"100 are counted for the short sale still working ({short})", out["error"]["message"])
        self.assertEqual({"ACME": 200, C110: -1, P95: 1}, self.positions())
        self.kept()
        assert_integrity(self, self.rt, "full")

    def test_seed_4440412_step_284_a_sale_after_an_early_call_exercise_with_a_short_sale_working(self):
        # SELL_SHORT 100 limit (flat); BUY 150 + 200; the early exercise of a long Dec 115 call (450 held); 281 STO 3 Dec 115 calls covered
        # (free for calls 350); 282 STO 1 Jan 105 covered: NOT_COVERED; 284 SELL 150 - d509563 accepted it (cover_state free 150): 300
        # held, 100 counted for the short sale, the 3 calls on 200
        short = self.short_sale(100)
        self.hold(150)
        self.hold(200)
        self.assertEqual((200, "FILLED"), self.place(C115, "BTO", 1)[:2])
        code, out = self.exercise(C115, 1)
        self.assertEqual(200, code, out)
        self.assertEqual(450, self.cover()["held"])
        self.assertEqual((200, "FILLED"), self.covered(C115, 3)[:2])
        self.assertEqual((409, "NOT_COVERED"), self.covered(C105J, 1)[:2])
        self.assertEqual(["OVERSELL"], self.sale_blockers(150))
        code, status, out = self.place("ACME", "SELL", 150)
        self.assertEqual((400, "OVERSELL"), (code, status), out)
        self.assertIn(f"is 50: sell 50; 300 ACME cover calls already sold ({C115}): buy them back first (SHARES_RESERVED); 100 ACME are counted for"
                      f" the short sale still working ({short})", out["error"]["message"])
        self.assertEqual((200, "FILLED"), self.place("ACME", "SELL", 50)[:2])   # the 50 free sell (control)
        self.assertEqual(({"ACME": 400, C115: -3}, 0), (self.positions(), self.uncovered()))
        self.kept()
        self.acme_print("120.00")
        self.assertEqual(("SUSPENDED", {"ACME": 400, C115: -3}), (self.row(short)["status"], self.positions()))
        self.assertEqual({C115: "covered_call"}, self.margin_now())
        assert_integrity(self, self.rt, "full")

    def test_seed_4440413_step_228_a_resting_sale_while_calls_claim_the_rest(self):
        # SELL_SHORT 150 limit (flat); BUY 150 + 300; STO 3 Dec 105 calls covered (free for calls 300); SELL_SHORT 200: SHORT_WHILE_LONG;
        # STO 2 more: NOT_COVERED; 228 SELL 100 (it rests) - d509563 accepted it: 450 held, 100 in the sale and 150 for the short sale,
        # the 3 calls on 200
        short = self.short_sale(150)
        self.hold(150)
        self.hold(300)
        self.assertEqual((200, "FILLED"), self.covered(C105, 3)[:2])
        self.assertEqual((400, "SHORT_WHILE_LONG"), self.place("ACME", "SELL_SHORT", 200)[:2])
        self.assertEqual((409, "NOT_COVERED"), self.covered(C105, 2)[:2])
        code, status, out = self.place("ACME", "SELL", 100, order_type="LIMIT", limit_px="125.00")
        self.assertEqual((400, "OVERSELL"), (code, status), out)
        self.assertIn(f"is 0; 300 ACME cover calls already sold ({C105}): buy them back first (SHARES_RESERVED); 150 ACME are counted for the short"
                      f" sale still working ({short}): a short sale is never booked against shares held, so cancel it first", out["error"]["message"])
        self.assertEqual([], self.q("SELECT order_id FROM orders WHERE side='SELL'"))
        self.kept()
        self.cancel(short)                                                # control: once the short sale is cancelled, 150 are free
        code, status, out = self.place("ACME", "SELL", 100, order_type="LIMIT", limit_px="125.00")
        self.assertEqual((200, "WORKING"), (code, status), out)
        self.assertEqual((450, 100, 0, 50), (self.cover()["held"], self.cover()["working_sells"], self.cover()["working_shorts"], self.cover()["free"]))
        self.kept()
        assert_integrity(self, self.rt, "full")


# ==== the page ===========================================================================================================================
PERSONAL, FUND = config.BOOKS[0], config.BOOKS[1]
F_USX, P_FX = "F1:US_EXCHANGE", "P1:FOREIGN_FX"
FX_HASH = "#/a/" + P_FX.replace(":", "%3A")
STORE_KEY = "finsim.smart.draft.v2"
WIN_KEY = "finsim.smart.draft.window"
READY = "!!(window.T && T.router && T.router.current && T.router.current.page === 'smart' && document.querySelector('#smart-text') && T.pages.smart)"
LOADED = "!!(window.T && T.router && T.router.current && document.querySelector('#page') && T.state.books && T.state.books.length === 2 && T.book.code)"
CALL_STD = SMK.CALL_JAN60                                                    # ACME270115C00060000: the standard root
ADJ_ROW = ("2027-01-15", "C", "57.5", "4.90", "5.20")                        # listed on the adjusted root only
CALL_ADJ = occ_symbol("ACME1", date(2027, 1, 15), "C", Decimal("57.5"))      # ACME1270115C00057500
EURUSD_LAST = "1.0850"
#: the docked order ticket: the claim in its state, the covered line and the body its preview (and Submit) carries
TICKET = """(function () {
  function el(id) { return document.getElementById(id); }
  var s = T.components.ticket.state() || {}; var cov = el('ticket-covered');
  var body = null; try { body = s.preview_body ? JSON.parse(s.preview_body) : null; } catch (e) { body = null; }
  return { box: (el('ticket-instrument') || {}).value || '', right: (s.inst && s.inst.right) || null, side: (el('ticket-side') || {}).value,
           qty: (el('ticket-qty') || {}).value, covered: !!s.covered, covered_shown: !!(cov && cov.offsetParent !== null), covered_box: !!(cov && cov.checked),
           covered_words: (el('ticket-covered-words') || {}).textContent || '', body: body };
})()"""
#: the FX desk in the dock: Spot / Forward, the tenor, the currencies, the amount, its currency (the highlighted unit button), the deal line
DESK = """(function () {
  var r = document.querySelector('#dock-body .fxd'); if (!r) { return null; }
  function on(b) { return !!(b && b.classList.contains('on')); }
  var modes = r.querySelectorAll('.modes .btn'), units = r.querySelectorAll('.units .btn');
  var amt = r.querySelector('input[aria-label="amount"]'), ten = r.querySelector('select[aria-label="tenor"]');
  var buy = r.querySelector('select[aria-label="currency you buy"]'), sell = r.querySelector('select[aria-label="currency you sell"]');
  var deal = r.querySelector('.pv .deal');
  return { mode: on(modes[0]) ? 'SPOT' : (on(modes[1]) ? 'FORWARD' : '?'), tenor: ten ? ten.value : null,
           unit: on(units[0]) ? units[0].textContent : (on(units[1]) ? units[1].textContent : '?'), amount: amt ? amt.value : null,
           buy: buy ? buy.value : null, sell: sell ? sell.value : null, deal: deal ? deal.innerText : '',
           pv: ((r.querySelector('.pv') || {}).innerText || '').slice(0, 240) };
})()"""
#: test_g: the bond ticket mounted in a box of its own, a trade typed there, then a fresh hand-off with no face
BOND_FRESH = """(function () {
  var box = document.createElement('div'); box.hidden = true; document.body.appendChild(box); window.__s4bt = box;
  var s = T.ticketKinds.BOND.open(box, { account_id: 'P1:DEBT', instrument_id: 'UST-TEST', side: 'SELL', qty: 5000 });
  var before = { face: s.ui.face.value, sell: s.ui.sell.classList.contains('on') };
  s.ui.result.appendChild(document.createTextNode('an earlier trade'));
  var t = T.ticketKinds.BOND.open(box, { account_id: 'P1:DEBT', instrument_id: 'UST-TEST', fresh: true });
  return { same: s === t, before: before, face: t.ui.face.value, buy: t.ui.buy.classList.contains('on'), result: t.ui.result.textContent };
})()"""
#: test_g: the private-markets ticket mounted in a box of its own (its companies loaded first)
PRIVATE_MOUNT = """(function () {
  var box = document.createElement('div'); box.hidden = true; document.body.appendChild(box); window.__s4pt = box;
  T.ticketKinds.PRIVATE.open(box, { account_id: 'F1:PRIVATE', side: 'SELL', qty: 40 }); return true;
})()"""
#: ... a trade typed there, then a fresh hand-off with no quantity
PRIVATE_FRESH = """(function () {
  var box = window.__s4pt; function v(id) { return box.querySelector('#' + id).value; }
  function set(id, x) { var e = box.querySelector('#' + id); e.value = x; e.dispatchEvent(new Event(e.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); }
  set('pt-kind', 'ROUND'); set('pt-price', '12.5'); set('pt-fee', '1'); set('pt-settle', '5'); set('pt-lock', '1');
  var before = { qty: v('pt-qty'), side: v('pt-side'), kind: v('pt-kind'), fee: v('pt-fee'), settle: v('pt-settle'), lock: v('pt-lock') };
  T.ticketKinds.PRIVATE.open(box, { account_id: 'F1:PRIVATE', fresh: true });
  return { before: before, qty: v('pt-qty'), side: v('pt-side'), kind: v('pt-kind'), fee: v('pt-fee'), settle: v('pt-settle'), lock: v('pt-lock') };
})()"""
BOXES_GONE = "(function () { [window.__s4bt, window.__s4pt].forEach(function (b) { if (b) { b.remove(); } }); window.__s4bt = window.__s4pt = null; return true; })()"


def _chain(now: int) -> ChainSnapshot:
    """The smoke test's invented ACME chain and the adjusted root's one contract, observed a minute ago."""
    def row(occ, e, r, k, b, a):
        return ChainRow(occ, e, r, qprice(k), qprice(b), qprice(a), 20, 20, None, None, 0.30, None, None, None, None, 100, 10)
    rows = [row(occ_symbol("ACME", date.fromisoformat(e), r, Decimal(str(k))), e, r, k, b, a) for e, r, k, b, a in SMK.ACME_CHAIN]
    rows.append(row(CALL_ADJ, *ADJ_ROW))
    under = ChainUnderlying(symbol="ACME", last=qprice("60.00"), bid=None, ask=None, bid_size=None, ask_size=None, last_trade_time=now - 60, prev_close=None, iv30=0.30)
    return ChainSnapshot(underlying_id="ACME", cboe_symbol="ACME", underlying=under, contracts=rows, fetched_at=now, quote_time=now - 60, n_raw=len(rows))


def _seed(home: str, clock: FakeClock, fp: FakeProvider) -> None:
    """The Personal and the Fund books; the fictional listings ACME / WIDG and their prints; an invented EURUSD print; the ACME chain."""
    rt = _server.build_runtime(home, os.path.join(home, "terminal.db"), clock=clock, offline=True, fake_provider=fp, log=_Log(), books=[PERSONAL, FUND],
                               fetch_threads=0)
    try:
        aapl = rt.instruments.get("AAPL")
        with rt.db.writer() as tx:
            for sym, (name, _last) in SMK.SMART_NAMES.items():
                rt.instruments.upsert(tx, instrument_id=sym, kind="EQUITY", symbol=sym, name=name, currency="USD", session_group=aapl.session_group,
                                      account_kinds=list(aapl.account_kinds), yahoo_symbol=sym, cboe_symbol=sym, spec={"exchange": "NYQ"})
        qt = epoch_of(clock.today(), "13:58")
        for sym, (_name, last) in SMK.SMART_NAMES.items():
            fp.set(sym, last=last, quote_time=qt, bar=(qt - qt % 60 - 60, 61900))
            rt.cache.subscribe([sym], 86400, "PAGE")
        fx = rt.instruments.get("EURUSD")
        fp.set(fx.yahoo_symbol, last=EURUSD_LAST, quote_time=qt, bar=(qt - qt % 60 - 60, 61900))
        rt.cache.subscribe(["EURUSD"], 86400, "PAGE")
        rt.worker.tick()
        fp.set_chain("ACME", _chain(int(clock.epoch())))
    finally:
        rt.close()


class _Server4(SMK._Server):
    """``SMK._Server`` with the Personal and the Fund books (port 0, offline, the seeded FakeProvider)."""

    def __init__(self, home, clock, fp):
        super().__init__(home, clock, fp)
        self.thread = threading.Thread(target=self._run4, name="simplify-r4-serve", daemon=True)

    def _run4(self):
        self.rc = _server.serve(port=0, home=self.home, db=os.path.join(self.home, "terminal.db"), offline=True, ready=self._ready, out=self.msgs.append,
                                clock=self.clock, fake_provider=self.fp, books=[PERSONAL, FUND], fetch_threads=0, supervisor_interval=0.5, log=_Log())


@unittest.skipUnless(find_browser(), NO_BROWSER)
class HandOffPageTests(unittest.TestCase):
    """One offline server (port 0, a temp home) and one headless browser for the class, both closed in tearDownClass."""

    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp(prefix="fst-simplify-r4-page-")
        cls.env = {k: os.environ.get(k) for k in (config.HOME_ENV, config.DB_ENV)}
        os.environ[config.HOME_ENV] = cls.home
        os.environ[config.DB_ENV] = os.path.join(cls.home, "terminal.db")
        cls.server = cls.browser = None
        try:
            clock = FakeClock(SMK.AT)
            cls.fp = FakeProvider(clock)
            _seed(cls.home, clock, cls.fp)
            cls.server = _Server4(cls.home, FakeClock(SMK.AT), cls.fp).start()
            cls._served_chain()
            # the adjusted root's contract in the master (ACME its underlying), as the Strategies tab's light command lists a package leg
            rt = cls.server.rt
            res = rt.bus.execute(Command("strategies.ensure_instruments", {"underlying_id": "ACME", "occs": [CALL_ADJ]}, "test:simplify-r4-acme1", "ui"))
            assert res.ok, res.body
            inst = rt.instruments.get(CALL_ADJ)
            assert inst is not None and inst.underlying_id == "ACME" and inst.right == "C", inst
            cls._served_chain()                                         # its quote cached from the chain, as for any listed contract
            cls.browser = Browser(find_browser(), 1440, 900)
            cls.sid = cls.browser.page("about:blank")
            cls.browser.navigate(cls.sid, cls.server.url)
            cls.browser.wait(cls.sid, LOADED, timeout=45)
        except Exception:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            if cls.browser is not None:
                cls.browser.close()
        finally:
            try:
                if cls.server is not None:
                    cls.server.stop()
            finally:
                for k, v in cls.env.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
                shutil.rmtree(cls.home, ignore_errors=True)

    @classmethod
    def _served_chain(cls) -> None:
        """The chain observed now in the served runtime (as its quote worker caches a fetched chain)."""
        rt, fp = cls.server.rt, cls.fp
        now = int(rt.clock.epoch())
        snap = _chain(now)
        fp.set_chain("ACME", snap)
        rt.chains.put(snap)
        for rec in records_from_chain(snap, rt.instruments):
            rt.cache.put(rec, now)

    # ---- the page, as the owner uses it ---------------------------------------------------------------------------------------------
    def js(self, expr: str):
        return self.browser.js(self.sid, expr)

    def wait(self, expr: str, timeout: float = 30.0):
        return self.browser.wait(self.sid, expr, timeout=timeout)

    def press(self, key: str, vk: int, mods: int = 0) -> None:
        base = {"key": key, "code": key, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk, "modifiers": mods}
        for kind in ("keyDown", "keyUp"):
            self.browser.call("Input.dispatchKeyEvent", dict(base, type=kind), session=self.sid)

    @staticmethod
    def drafts_gone() -> str:
        return ("(function () { try { localStorage.removeItem(%s); sessionStorage.removeItem(%s); } catch (e) { /* none */ } return true; })()"
                % (json.dumps(STORE_KEY), json.dumps(WIN_KEY)))

    def smart(self, book: str, reload: bool = False) -> None:
        """#/smart with no draft and ``book`` selected, from the Dashboard; ``reload``: the app loaded anew first (so the order ticket has
        looked nothing up yet)."""
        self.js(self.drafts_gone())
        if reload:
            self.browser.navigate(self.sid, "about:blank")
            self.browser.navigate(self.sid, self.server.url + "#/dashboard")
            self.wait(LOADED, timeout=45)
        self.js("location.hash = '#/dashboard'; true")
        self.wait("T.router.current && T.router.current.page === 'dashboard'")
        self.js("T.book.set(%s); true" % json.dumps(book))
        self.wait("T.book.code === %s" % json.dumps(book))
        self.js("location.hash = '#/smart'; true")
        self.wait(READY)
        self.browser.pump(0.4)

    def read(self, text: str, cards: int) -> None:
        self.js("(function () { var t = document.querySelector('#smart-text'); t.focus(); t.value = %s; t.dispatchEvent(new Event('input', { bubbles: true }));"
                " return true; })()" % json.dumps(text))
        self.press("Enter", 13)
        self.wait("!document.querySelector('#smart-parse').disabled && /^read /.test(document.querySelector('#smart-status').innerText)"
                  " && document.querySelectorAll('#smart-cards [data-i]').length === %d" % cards)

    def cards(self) -> list:
        return [(c["num"], c["side"], c["covered"], c["open"]) for c in self.js(SMK.CARDS)]

    def open_card(self, i: int, inst: str) -> dict:
        """Open in ticket on card ``i``: F1's page with its docked ticket on ``inst``, once its look-up of the contract and its own preview
        have answered."""
        self.js("document.querySelector('#smart-cards [data-i=\"%d\"] [data-act=\"open\"]').click(); true" % i)
        self.wait("location.hash.indexOf('#/a/F1%%3AUS_EXCHANGE') === 0 && !!document.querySelector('#ticket-instrument')"
                  " && document.querySelector('#ticket-instrument').value === %s" % json.dumps(inst))
        self.wait("(function () { var s = T.components.ticket.state(); return !!(s && s.instrument_id === %s && s.inst && s.inst.right && !s.previewing"
                  " && !s.dirty && s.preview_body); })()" % json.dumps(inst), timeout=40)
        self.wait("!document.querySelector('#ticket-submit-btn').disabled || !!document.querySelector('#ticket-preview .pv-blocker')", timeout=40)
        self.browser.pump(0.5)
        return self.js(TICKET)

    def sends(self, since: int) -> list:
        """Every request the page sent since ``since`` that books, places, changes or cancels anything (a POST / PATCH / PUT / DELETE other
        than a preview or the Smart Ticket's parse)."""
        out = []
        for e in self.browser._events(self.sid, since):
            if e.get("method") == "Network.requestWillBeSent":
                r = (e.get("params") or {}).get("request") or {}
                p = urlsplit(r.get("url") or "").path
                if r.get("method") not in ("GET", "HEAD", "OPTIONS") and not p.endswith("/preview") and p != "/api/smart-ticket/parse":
                    out.append((r.get("method"), p))
        return out

    def held(self, acct: str) -> int:
        with self.server.rt.db.reader() as conn:
            r = conn.execute("SELECT qty FROM v_positions WHERE account_id=? AND instrument_id='ACME'", (acct,)).fetchone()
        return int(Decimal(str(r[0]))) if r else 0

    def set_acme(self, acct: str, book: str, qty: int) -> None:
        """``acct`` holds ``qty`` ACME: the difference bought or sold at market through the order route on a fresh print (another client)."""
        rt = self.server.rt
        held = self.held(acct)
        if held == qty:
            return
        now = int(rt.clock.epoch())
        t0 = epoch_of(rt.clock.today(), "13:58")
        px = qprice("60.00")
        bar = Bar1m(t0 - 60, px, px, px, px, 1000000)
        rt.cache.put(QuoteRecord(instrument_id="ACME", provider="yahoo", source="Yahoo Finance chart (ACME)", method="direct", last=px, quote_time=t0 + 30,
                                 fetched_at=now, delay_s=900, delay_label="up to 15 min delayed", currency="USD", session_group=rt.instruments.get("ACME").session_group,
                                 bar=bar, bars_tail=[bar]), now)
        body = {"account_id": acct, "instrument_id": "ACME", "side": "BUY" if qty > held else "SELL", "qty": abs(qty - held), "order_type": "MARKET", "tif": "DAY",
                "request_id": f"ui:simplify-r4-acme-{os.urandom(6).hex()}"}
        code, out, _h = rt.router.handle("POST", f"/api/orders?book={book}", None, body, "127.0.0.1", None)
        self.assertEqual((200, "FILLED"), (code, (out.get("order") or {}).get("status")), out)
        self.assertEqual(qty, self.held(acct))

    def tidy(self) -> None:
        def undo():
            self.browser.viewport(self.sid, 1440, 900)
            self.js(BOXES_GONE)
            self.js(self.drafts_gone())
            self.js("location.hash = '#/dashboard'; true")
            self.browser.pump(0.6)
        self.addCleanup(undo)

    # ---- the FX desk ------------------------------------------------------------------------------------------------------------------
    def desk_ready(self) -> dict:
        self.wait("location.hash.indexOf(%s) === 0 && !!document.querySelector('#dock-body .fxd') && T.dock.visible()" % json.dumps(FX_HASH))
        self.wait("(function () { var r = document.querySelector('#dock-body .fxd .pv'); return !!r && !/the preview appears here|previewing/.test(r.innerText)"
                  " && (!!r.querySelector('.deal') || !!r.querySelector('.blk') || /enter the amount/.test(r.innerText)); })()")
        self.browser.pump(0.6)
        return self.js(DESK)

    def fx_page_b(self) -> dict:
        """FinSim's own FX page (Foreign Markets & FX > FX spot): the b button on the EURUSD row - the desk docked there, prefilled."""
        self.js("location.hash = %s; true" % json.dumps(FX_HASH + "/markets/fx"))
        self.wait("(function () { return Array.prototype.some.call(document.querySelectorAll('#page tr[data-i]'), function (tr) {"
                  " return /^EURUSD/.test((tr.cells[0] || {}).innerText || '') && !!tr.querySelector('button[data-act=\"b\"]'); }); })()")
        self.js("(function () { var tr = Array.prototype.filter.call(document.querySelectorAll('#page tr[data-i]'), function (tr) {"
                " return /^EURUSD/.test((tr.cells[0] || {}).innerText || ''); })[0]; tr.querySelector('button[data-act=\"b\"]').click(); return true; })()")
        return self.desk_ready()

    def desk_unit(self, ccy: str) -> None:
        self.js("(function () { var b = Array.prototype.filter.call(document.querySelectorAll('#dock-body .fxd .units .btn'), function (x) {"
                " return x.textContent === %s; })[0]; b.click(); return true; })()" % json.dumps(ccy))
        self.browser.pump(0.2)

    def desk_amount(self, text: str) -> None:
        self.js("(function () { var a = document.querySelector('#dock-body .fxd input[aria-label=\"amount\"]'); a.focus(); a.value = %s;"
                " a.dispatchEvent(new Event('input', { bubbles: true })); return true; })()" % json.dumps(text))
        self.browser.pump(0.2)

    def desk_forward(self, tenor: str) -> None:
        self.js("(function () { var b = Array.prototype.filter.call(document.querySelectorAll('#dock-body .fxd .modes .btn'), function (x) {"
                " return x.textContent === 'Forward'; })[0]; b.click(); var t = document.querySelector('#dock-body .fxd select[aria-label=\"tenor\"]');"
                " t.value = %s; t.dispatchEvent(new Event('change', { bubbles: true })); return true; })()" % json.dumps(tenor))
        self.browser.pump(0.2)

    def open_fx_desk_from_card(self, i: int = 0) -> dict:
        """Card ``i``'s KIND_OWN_TICKET chip (the currency pair's red flag), then its action "Open the FX desk"; the desk once it answered."""
        self.js("(function () { var c = document.querySelector('#smart-cards [data-i=\"%d\"]');"
                " var chip = Array.prototype.filter.call(c.querySelectorAll('.smart-chip.sev-blocker'), function (b) { return /currency pair/.test(b.title); })[0];"
                " chip.click(); return true; })()" % i)
        self.wait("!!document.querySelector('#smart-cards [data-i=\"%d\"] .smart-flagbox [data-act=\"action\"]')" % i, timeout=10)
        self.assertEqual("Open the FX desk", self.js("document.querySelector('#smart-cards [data-i=\"%d\"] .smart-flagbox [data-act=\"action\"]').innerText" % i))
        self.js("document.querySelector('#smart-cards [data-i=\"%d\"] .smart-flagbox [data-act=\"action\"]').click(); true" % i)
        return self.desk_ready()

    def deals(self) -> int:
        with self.server.rt.db.reader() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM fx_deals").fetchone()[0])

    # ---- the tests ------------------------------------------------------------------------------------------------------------------
    def _covered_first_open(self, text: str, occ: str) -> dict:
        self.set_acme(F_USX, "F", 100)
        code, ans, _h = self.server.rt.router.handle("POST", "/api/smart-ticket/parse?book=F", None, {"text": text, "book": "F"}, "127.0.0.1", None)
        self.assertEqual(200, code, ans)
        req = ans["legs"][0]["request"] or {}
        self.assertEqual((1, occ, "STO", 1, True), (len(ans["legs"]), req.get("instrument_id"), req.get("side"), req.get("qty"), req.get("covered")), req)
        self.smart("F", reload=True)                                     # a page loaded anew: the order ticket has looked nothing up yet
        start = self.browser.mark()
        self.read(text, 1)
        self.assertEqual([("#1", "STO", True, True)], self.cards())
        t = self.open_card(0, occ)
        self.assertEqual([], self.sends(start))
        return t

    def test_e_a_covered_call_on_a_root_with_a_digit_opens_sold_as_covered_on_the_first_open(self):
        self.tidy()
        t = self._covered_first_open("covered call 100 ACME Jan 15 2027 57.5", CALL_ADJ)
        self.assertEqual(("C", "STO", "1", True, True, True, True), (t["right"], t["side"], t["qty"], t["covered"], t["covered_box"], t["covered_shown"],
                                                                     (t["body"] or {}).get("covered")), t)
        self.assertIn("sold as covered: FinSim places it only while 100 ACME are free in this account", t["covered_words"])

    def test_e2_control_a_covered_call_on_the_standard_root_opens_sold_as_covered(self):
        self.tidy()
        t = self._covered_first_open("covered call 100 ACME Jan 15 2027 60", CALL_STD)
        self.assertEqual(("C", "STO", "1", True, True, True, True), (t["right"], t["side"], t["qty"], t["covered"], t["covered_box"], t["covered_shown"],
                                                                     (t["body"] or {}).get("covered")), t)

    def test_f_the_fx_desk_opened_from_a_card_prices_the_amount_in_the_pairs_base_currency(self):
        """Earlier in the desk the owner priced a spot deal in USD (the USD amount button, 5,000; not booked); "buy 10k EURUSD" then opens
        the desk on EUR 10,000 (d509563: USD 10,000 - BUY EUR 9,216.42)."""
        self.tidy()
        before, start = self.deals(), self.browser.mark()
        d0 = self.fx_page_b()
        self.assertEqual(("SPOT", "EUR", "EUR", "USD"), (d0["mode"], d0["unit"], d0["buy"], d0["sell"]), d0)
        self.desk_unit("USD")
        self.desk_amount("5000")
        d1 = self.desk_ready()
        self.assertEqual(("SPOT", "USD", "5000"), (d1["mode"], d1["unit"], d1["amount"]), d1)
        self.smart("P")
        self.read("buy 10k EURUSD", 1)
        d = self.open_fx_desk_from_card(0)
        self.assertEqual(("SPOT", "EUR", "EUR", "USD", "10000"), (d["mode"], d["unit"], d["buy"], d["sell"], d["amount"]), d)
        self.assertTrue(d["deal"].startswith("BUY EUR 10,000.00 / SELL USD"), d)
        self.assertEqual(([], before), (self.sends(start), self.deals()))

    def test_f2_the_fx_desk_opened_from_a_card_is_a_spot_deal_with_no_amount_carried_over(self):
        """Earlier the owner typed 5,000 and looked at a 6-month forward in the desk; "buy 10k EURUSD" opens a spot deal (d509563: the
        6-month forward), and "buy EURUSD" (no amount) opens the desk with its amount box empty (d509563: the 5,000)."""
        self.tidy()
        before, start = self.deals(), self.browser.mark()
        self.fx_page_b()
        self.desk_amount("5000")
        self.desk_forward("6M")
        d1 = self.desk_ready()
        self.assertEqual(("FORWARD", "6M"), (d1["mode"], d1["tenor"]), d1)
        self.smart("P")
        self.read("buy 10k EURUSD", 1)
        d = self.open_fx_desk_from_card(0)
        self.assertEqual(("SPOT", "3M", "EUR", "10000"), (d["mode"], d["tenor"], d["unit"], d["amount"]), d)
        self.desk_amount("5000")
        self.assertEqual("5000", self.desk_ready()["amount"])
        self.smart("P")
        self.read("buy EURUSD", 1)
        d = self.open_fx_desk_from_card(0)
        self.assertEqual(("SPOT", "EUR", ""), (d["mode"], d["unit"], d["amount"]), d)
        self.assertIn("enter the amount in EUR", d["pv"])
        self.assertEqual(([], before), (self.sends(start), self.deals()))

    def test_f3_controls_a_desk_built_afresh_and_finsims_own_fx_page(self):
        self.tidy()
        before, start = self.deals(), self.browser.mark()
        self.fx_page_b()
        self.desk_unit("USD")
        self.desk_amount("5000")
        self.desk_forward("6M")
        d = self.fx_page_b()                                             # b on EURUSD again: SPOT and the base as the unit (as before)
        self.assertEqual(("SPOT", "EUR", "EUR", "USD"), (d["mode"], d["unit"], d["buy"], d["sell"]), d)
        self.js("location.hash = %s; true" % json.dumps("#/a/" + F_USX.replace(":", "%3A") + "/markets"))
        self.wait("T.router.current && T.router.current.account === %s && !!document.querySelector('#dock-body .ticket') && !document.querySelector('#dock-body .fxd')"
                  % json.dumps(F_USX))
        self.smart("P")                                                  # the dock held the order ticket: the desk is built again
        self.read("buy 10k EURUSD", 1)
        d = self.open_fx_desk_from_card(0)
        self.assertEqual(("SPOT", "EUR", "EUR", "USD", "10000"), (d["mode"], d["unit"], d["buy"], d["sell"], d["amount"]), d)
        self.assertEqual(([], before), (self.sends(start), self.deals()))

    def test_g_the_bond_and_private_tickets_start_a_new_trade_on_a_fresh_hand_off(self):
        """Each ticket mounted in a box of its own (not the dock): a trade typed there, then a fresh hand-off as the Smart Ticket's "Open the
        bond ticket" / "Open the private-markets ticket" sends it, with no quantity: the face / quantity, the price, the terms, the preview
        and the result of the trade it held are dropped."""
        self.tidy()
        start = self.browser.mark()
        got = self.js(BOND_FRESH)
        self.assertEqual((True, {"face": "5000", "sell": True}, "", True, ""), (got["same"], got["before"], got["face"], got["buy"], got["result"]), got)
        self.js(PRIVATE_MOUNT)
        self.wait("!!(window.__s4pt && window.__s4pt.querySelector('#pt-qty'))")
        got = self.js(PRIVATE_FRESH)
        self.assertEqual(({"qty": "40", "side": "SELL", "kind": "ROUND", "fee": "1", "settle": "5", "lock": "1"}, "", "BUY", "SECONDARY", "3", "30", "6"),
                         (got["before"], got["qty"], got["side"], got["kind"], got["fee"], got["settle"], got["lock"]), got)
        self.assertEqual([], self.sends(start))


if __name__ == "__main__":
    unittest.main()
