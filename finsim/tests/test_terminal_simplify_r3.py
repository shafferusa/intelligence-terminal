"""FinSim 1.0.7.2 simplify review round 3 (on 803baac): the regression tests of its findings, kept in the terminal suite.

The money path (the owner's rule R-4: the labels the owner sees are true at Send; the cover of calls sold as covered stays a server-side
invariant for every order):
  DeltaHedgeOfferedAsSentTests  the Hedges tab's DELTA_HEDGE for a call purchase sells the underlying only as the order route takes it:
      while some shares are held but fewer than the hedge sells, its short sale is offered NOT available (SHORT_WHILE_LONG, with the
      reason), and a sale beyond the shares free to sell (the cover of calls sold as covered, working sales, loans) is offered NOT
      available (OVERSELL); the ticket's preview then names the blocker NOT_AVAILABLE and the Submit refuses it the same way, nothing
      placed (tests/test_reviews3_money_path_cover.py test_a / test_a2: 803baac offered both as available, the preview had no blocker
      and the Submit answered 400 SHORT_WHILE_LONG / OVERSELL).  Controls: flat, the short sale is offered; with the shares free, the
      sale is offered and placed as the order's waiting leg.
  HedgeLegsPreviewedAsSubmittedTests  the order preview validates each hedge leg as ``orders.create_hedge_children`` does at Submit
      (``orders_preview.hedge_legs_check``), each sale counted after the order's legs before it: a sale leg beyond the free shares (400
      OVERSELL), a short-sale leg while long (400 SHORT_WHILE_LONG), two sale legs that fit alone but not together - the preview's
      blocker, the Submit's refusal, the same code; a leg that fits passes both.
The reading:
  ChoiceLabelsPerBookTests  a choice on one book's card of a clause repeated per book carries the label that order's own reading gives
      (``Resolver._keep_others`` shared the first copy's labels with every copy): the three texts of
      tests/test_reviews3_autofill_choice_labels.py, and every instruction of tests/test_props3_autofill_choice_labels.py's seeds
      3330101 and 3330102 (900 cases each) that 803baac got wrong (9 + 9, embedded): L0 (each label is one its own order's reading
      gives) and L4b (a MISSING_SIDE holding names the card's own account and position).
The page (the real offline server on port 0 with the Personal and the Fund books, the real pages in a headless Edge / Chrome):
  SmartTicketPageTests  test_e: a Submit answer that comes while the order ticket is out of sight (the owner went back to the Smart
      Ticket) is also said in a toast naming its order, so opening the next card never clears it unseen; test_f: an open flag's
      alternatives wrap inside their card at the widths where the Dashboard fits; test_g: a fresh hand-off on the same instrument
      writes it into the ticket's symbol box; test_h: two windows - each keeps its own draft, so another window leaving the Smart
      Ticket never drops this window's edit or "opened" mark.
Fictional ACME / WIDG / GIZM only; temp homes; no ~/.finsim, no network, no port 8765 / 8865.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
import unittest
from datetime import date
from decimal import Decimal
from urllib.parse import urlsplit

from tests import test_terminal_browser_smoke as SMK
from tests import test_terminal_simplify_r2 as R2
from tests import test_terminal_smart_ticket as STT
from tests.terminal_cdp import NO_BROWSER, Browser, find_browser
from tests.terminal_helpers import _Log, assert_integrity
from tests.test_terminal_smart_submit import ACCT
from finsim.terminal import config, server as _server, smart_ticket as st
from finsim.terminal.clock import FakeClock, epoch_of
from finsim.terminal.instruments import occ_symbol
from finsim.terminal.quotes import Bar1m, QuoteRecord
from finsim.terminal.quotes.cache import records_from_chain
from finsim.terminal.quotes.providers import ChainRow, ChainSnapshot, ChainUnderlying
from finsim.terminal.units import qprice

C100, C105J = R2.C100, R2.C105J


# ==== the money path ===================================================================================================================
class _MoneyBase(R2._HedgeLegBase):
    """test_terminal_simplify_r2's world (Test Book T1, ACME at 100.00, its invented chain with a delta of 0.50 on the Dec 18 2026 100
    call), driven through the docked ticket's preview, then the order with the preview's id."""

    def rid(self, what: str) -> str:
        self.k += 1
        return f"ui:s3-{what}-{self.k:04d}"

    def bto(self, contracts: int, hedge) -> dict:
        return {"account_id": ACCT, "instrument_id": C100, "side": "BTO", "qty": contracts, "order_type": "LIMIT", "limit_px": "4.00", "tif": "DAY",
                "hedge": hedge}

    def preview_then_send(self, body: dict) -> tuple:
        """(the preview's hedge candidates by id, its blockers (code, message), the Submit's http, its status or code, its answer)."""
        code, pv, _h = self.call("POST", "/api/orders/preview?book=T", dict(body))
        self.assertEqual(200, code, pv)
        cands = {c["id"]: c for c in ((pv.get("hedges") or {}).get("candidates") or []) if isinstance(c, dict)}
        blockers = [(b["code"], b["message"]) for b in pv.get("blockers") or []]
        code, out, _h = self.send(dict(body, preview_id=pv.get("preview_id")), self.rid("send"))
        got = (out.get("order") or {}).get("status") if code == 200 else (out.get("error") or {}).get("code")
        return cands, blockers, code, got, out

    @staticmethod
    def legs(c: dict) -> list:
        return [(l["side"], int(l["qty"]), l["instrument_id"]) for l in c.get("legs") or []]

    def calls_ordered(self) -> list:
        return self.q("SELECT order_id FROM orders WHERE instrument_id=?", (C100,))


class DeltaHedgeOfferedAsSentTests(_MoneyBase):
    def test_a_a_short_sale_hedge_while_shares_are_held_is_offered_not_available(self):
        self.assertEqual((200, "FILLED"), self.order("ACME", "BUY", 50))
        cands, blockers, code, got, out = self.preview_then_send(self.bto(2, {"candidate_id": "DELTA_HEDGE"}))
        d = cands["DELTA_HEDGE"]
        self.assertEqual(([("SELL_SHORT", 100, "ACME")], False), (self.legs(d), d["available"]), d)
        self.assertIn("50 are held long - a short sale is refused while shares are held (SHORT_WHILE_LONG)", d["reason"])
        self.assertEqual(["NOT_AVAILABLE"], [c for c, _m in blockers], blockers)                 # the preview says it: Submit stays disabled
        self.assertEqual((400, "NOT_AVAILABLE"), (code, got), out)                                # the route refuses it the same way
        self.assertEqual(({"ACME": 50}, []), (self.positions(), self.calls_ordered()))           # nothing placed
        assert_integrity(self, self.rt, "full")

    def test_b_a_sale_hedge_beyond_the_shares_free_to_sell_is_offered_not_available(self):
        self.assertEqual((200, "FILLED"), self.order("ACME", "BUY", 300))
        self.assertEqual((200, "FILLED"), self.order(C105J, "STO", 2, covered=True))             # 200 of the 300 cover them
        self.assertEqual(100, self.cover()["free"])
        cands, blockers, code, got, out = self.preview_then_send(self.bto(3, {"candidate_id": "DELTA_HEDGE"}))
        d = cands["DELTA_HEDGE"]
        self.assertEqual(([("SELL", 200, "ACME")], False), (self.legs(d), d["available"]), d)
        self.assertIn("the hedge sells 200 and 100 are free to sell (held 300; 200 covering calls already sold)", d["reason"])
        self.assertIn("(OVERSELL)", d["reason"])
        self.assertEqual(["NOT_AVAILABLE"], [c for c, _m in blockers], blockers)
        self.assertEqual((400, "NOT_AVAILABLE"), (code, got), out)
        self.assertEqual(({"ACME": 300, C105J: -2}, 0, []), (self.positions(), self.uncovered(), self.calls_ordered()))
        assert_integrity(self, self.rt, "full")

    def test_c_controls_flat_the_short_sale_is_offered_and_with_free_shares_the_sale_is_placed_as_the_waiting_leg(self):
        code, pv, _h = self.call("POST", "/api/orders/preview?book=T", self.bto(2, {"candidate_id": "DELTA_HEDGE"}))
        d = {c["id"]: c for c in pv["hedges"]["candidates"]}["DELTA_HEDGE"]
        self.assertEqual(([("SELL_SHORT", 100, "ACME")], True, True), (self.legs(d), d["available"], d["legs"][0]["needs_borrow"]), d)
        self.assertEqual((200, "FILLED"), self.order("ACME", "BUY", 300))
        cands, blockers, code, got, out = self.preview_then_send(self.bto(2, {"candidate_id": "DELTA_HEDGE"}))
        d = cands["DELTA_HEDGE"]
        self.assertEqual(([("SELL", 100, "ACME")], True, ""), (self.legs(d), d["available"], d["reason"]), d)
        self.assertEqual(([], 200, "WORKING"), (blockers, code, got), out)
        legs = self.q("SELECT side, qty, instrument_id, status FROM orders WHERE parent_order_id=?", (out["order"]["order_id"],))
        self.assertEqual([("SELL", 100, "ACME", "WAITING_PARENT")], [(r["side"], int(r["qty"]), r["instrument_id"], r["status"]) for r in legs])
        assert_integrity(self, self.rt, "full")


class HedgeLegsPreviewedAsSubmittedTests(_MoneyBase):
    def leg_order(self, legs: list, held: int, covered_calls: int = 0) -> tuple:
        self.assertEqual((200, "FILLED"), self.order("ACME", "BUY", held))
        if covered_calls:
            self.assertEqual((200, "FILLED"), self.order(C105J, "STO", covered_calls, covered=True))
        return self.preview_then_send(self.bto(3, {"legs": legs}))

    def test_d_a_sale_leg_beyond_the_free_shares_is_the_previews_blocker_and_the_submits_refusal(self):
        _c, blockers, code, got, out = self.leg_order([{"instrument_id": "ACME", "side": "SELL", "qty": 200}], 300, covered_calls=2)
        self.assertEqual(["OVERSELL"], [c for c, _m in blockers], blockers)
        self.assertTrue(blockers[0][1].startswith("hedge leg 1 (SELL 200 ACME): SELL 200 ACME but the long position net of working sells and shares"
                                                  " covering calls is 100"), blockers)
        self.assertEqual((400, "OVERSELL"), (code, got), out)
        self.assertEqual(({"ACME": 300, C105J: -2}, []), (self.positions(), self.calls_ordered()))

    def test_e_a_short_sale_leg_while_long_is_the_previews_blocker_and_the_submits_refusal(self):
        _c, blockers, code, got, out = self.leg_order([{"instrument_id": "ACME", "side": "SELL_SHORT", "qty": 100}], 50)
        self.assertEqual(["SHORT_WHILE_LONG"], [c for c, _m in blockers], blockers)
        self.assertIn("hedge leg 1 (SELL_SHORT 100 ACME): SELL_SHORT 100 ACME: T1:US_EXCHANGE holds 50 ACME long - sell them first (SELL)", blockers[0][1])
        self.assertEqual((400, "SHORT_WHILE_LONG"), (code, got), out)
        self.assertEqual(({"ACME": 50}, []), (self.positions(), self.calls_ordered()))

    def test_f_two_sale_legs_that_fit_alone_but_not_together_are_counted_together(self):
        _c, blockers, code, got, out = self.leg_order([{"instrument_id": "ACME", "side": "SELL", "qty": 200}, {"instrument_id": "ACME", "side": "SELL", "qty": 200}], 300)
        self.assertEqual(["OVERSELL"], [c for c, _m in blockers], blockers)
        self.assertTrue(blockers[0][1].startswith("hedge leg 2 (SELL 200 ACME, with the 200 the order and its legs before it sell (400 in all)): SELL 400 ACME"),
                        blockers)
        self.assertEqual((400, "OVERSELL"), (code, got), out)                                    # at Submit leg 1 is written first: 100 left
        self.assertEqual(({"ACME": 300}, []), (self.positions(), self.calls_ordered()))

    def test_g_control_a_leg_that_fits_passes_the_preview_and_is_placed(self):
        _c, blockers, code, got, out = self.leg_order([{"instrument_id": "ACME", "side": "SELL", "qty": 100}], 300)
        self.assertEqual(([], 200, "WORKING"), (blockers, code, got), out)
        legs = self.q("SELECT side, qty, status FROM orders WHERE parent_order_id=?", (out["order"]["order_id"],))
        self.assertEqual([("SELL", 100, "WAITING_PARENT")], [(r["side"], int(r["qty"]), r["status"]) for r in legs])
        assert_integrity(self, self.rt, "full")


# ==== the reading: a choice's label is its own order's ====================================================================================
#: tests/test_props3_autofill_choice_labels.py's instructions (seed/cases: case, book, text) that 803baac's choice guard labelled from
#: another book's order (L0, and for the MISSING_SIDE holdings L4b), found with its own checks; none on the fixed guard
SEEDS = {
    "3330101/900": [
        (48, "P", "stc 100 WIDG in the Personal and Institutional books"),
        (254, "I", "stc 100 WIDG in the Fund and Personal books"),
        (282, "F", "sell 4 ACME Dec 18 2026 250 calls; stc 100 WIDG and sto 100 WIDG in the Personal and Fund books"),
        (363, "I", "covered call 150 ACME Dec 18 2026 240; buy 100 WIDG gtc; stc 100 WIDG aon in the Personal and Fund books"),
        (367, "I", "stc 100 WIDG, roll 3 ACME Dec 18 2026 240 calls to Jan 15 2027 250 in the Personal and Fund books"),
        (393, "P", "buy 60 GIZM, stc 100 WIDG and sell 1,5 ACME in the Personal and Institutional books"),
        (528, "F", "stc 100 WIDG in the Personal and Fund books"),
        (603, "P", "(stc 100 WIDG) in the Personal and Fund books"),
        (818, "P", "(close 250 WIDG gtc then stc 100 WIDG) in the Personal and Fund books"),
    ],
    "3330102/900": [
        (6, "F", "stc 100 WIDG then buy 200 contracts WIDG in the Personal and Fund books"),
        (41, "P", "(stc 100 WIDG and close 250 WIDG gtc and sell 100 WIDG limit 41) in the Fund and Personal books"),
        (86, "I", "buy 1,5k WIDG and stc 100 WIDG in the Personal and Institutional books"),
        (103, "P", "buy 15 LOTX; stc 100 WIDG in the Fund and Personal books"),
        (142, "I", "sell all ACME Dec 18 2026 240 calls; stc 100 WIDG; close ACME calls limit 2 in the Fund and Personal books"),
        (217, "F", "stc 100 WIDG, short 100 WIDG in the Fund and Personal books"),
        (411, "P", "roll 4 ACME Dec 18 2026 250 calls to Jan 15 2027 260 then stc 100 WIDG in the Fund and Personal books"),
        (596, "I", "(buy 1 SPX Dec 18 2026 6000 call and stc 100 WIDG) in the Personal and Institutional books"),
        (617, "P", "cover 2 ACME Dec 18 2026 240 calls, covered call 200 ACME Dec 18 2026 240; stc 100 WIDG as a package in the Personal and Fund books"),
    ],
}
HOLDING = re.compile(r"(?:sell|buy) \((?:sells the ([\d,]+) held|covers the ([\d,]+) short) in ([A-Z]\d+:[A-Z_]+)\)")


class ChoiceLabelsPerBookTests(unittest.TestCase):
    """tests/terminal_smart_corpus.py's world: the Personal book holds 300 WIDG and is short 200 GIZM; the Fund book holds no WIDG or GIZM."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.rt = STT._ResolverBase._make()
        STT.build_world(unittest.TestCase(), cls.rt)

    @classmethod
    def tearDownClass(cls):
        cls.rt.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def read(self, text: str, book: str) -> dict:
        return st.parse_view(self.rt, {"text": text, "book": book})

    def unguarded(self, text: str, book: str) -> dict:
        """The same reading before the choice guard checks the choices: each order's own labels (the guard only drops a choice or
        widens its words)."""
        orig = st.Resolver._keep_others
        st.Resolver._keep_others = lambda self_, *a, **k: None
        try:
            return self.read(text, book)
        finally:
            st.Resolver._keep_others = orig

    @staticmethod
    def labels(leg: dict, code: str = "MISSING_SIDE") -> list:
        return [str(ch["label"]) for f in leg["flags"] if f["code"] == code for ch in f.get("choices") or []]

    def one(self, out: dict, book: str) -> dict:
        legs = [l for l in out["legs"] if l["book"] == book]
        self.assertEqual(1, len(legs), [(l["book"], l["account_id"]) for l in out["legs"]])
        return legs[0]

    def stray(self, text: str, book: str) -> list:
        """L0 / L4b on one reading: the labels an order offers that its own reading does not give, and the holdings a MISSING_SIDE label
        names that are not the card's own."""
        out, raw = self.read(text, book), self.unguarded(text, book)
        self.assertEqual((len(raw["legs"]), len(raw["combos"])), (len(out["legs"]), len(out["combos"])), text)
        bad = []
        for a, b in list(zip(out["legs"], raw["legs"])) + list(zip(out["combos"], raw["combos"])):
            for f in a["flags"]:
                mine = {str(ch["label"]) for g in b["flags"] if g["code"] == f["code"] for ch in g.get("choices") or []}
                bad += [("L0", a.get("book"), a.get("account_id"), f["code"], str(ch["label"]), sorted(mine)) for ch in f.get("choices") or []
                        if str(ch["label"]) not in mine]
                if f["code"] == "MISSING_SIDE" and "n" in a:
                    held = int((a.get("position") or {}).get("qty") or 0)
                    for ch in f.get("choices") or []:
                        m = HOLDING.fullmatch(str(ch["label"]))
                        if m and (m.group(3) != a["account_id"] or (int(m.group(1).replace(",", "")) if m.group(1) else -int(m.group(2).replace(",", ""))) != held):
                            bad.append(("L4b", a["book"], a["account_id"], held, str(ch["label"])))
        return bad

    def test_a_each_book_copy_of_a_clause_offers_its_own_orders_label(self):
        own = {(t, b): self.labels(self.one(self.read(f"{t} in the {w} book", "P"), b))
               for t in ("stc 100 WIDG", "bto 100 GIZM") for b, w in (("P", "Personal"), ("F", "Fund"))}
        self.assertEqual({("stc 100 WIDG", "P"): ["sell (sells the 300 held in P1:US_EXCHANGE)"], ("stc 100 WIDG", "F"): ["sell"],
                          ("bto 100 GIZM", "P"): ["buy (covers the 200 short in P1:US_EXCHANGE)"], ("bto 100 GIZM", "F"): ["buy"]}, own)
        for t in ("stc 100 WIDG", "bto 100 GIZM"):
            for books in ("Personal and Fund", "Fund and Personal"):
                for sel in ("P", "F"):
                    with self.subTest(text=t, books=books, selected=sel):
                        out = self.read(f"{t} in the {books} books", sel)
                        self.assertEqual((own[(t, "P")], own[(t, "F")]), (self.labels(self.one(out, "P")), self.labels(self.one(out, "F"))))

    def test_b_the_picked_label_reads_as_it_says(self):
        """'bto 100 GIZM in the Fund and Personal books': the Personal card's "buy (covers the 200 short ...)" picked reads BUY_TO_COVER
        100 in P1; the Fund card's bare "buy" reads an opening BUY 100 in F1."""
        text = "bto 100 GIZM in the Fund and Personal books"
        out = self.read(text, "P")
        for book, side, label in (("P", "BUY_TO_COVER", "buy (covers the 200 short in P1:US_EXCHANGE)"), ("F", "BUY", "buy")):
            leg = self.one(out, book)
            (f,) = [f for f in leg["flags"] if f["code"] == "MISSING_SIDE"]
            (ch,) = f["choices"]
            self.assertEqual(label, ch["label"], (book, ch))                  # 803baac: "buy" on both cards (the Fund copy's label)
            sp = ch.get("span") or f.get("span") or leg["span"]
            after = self.one(self.read(out["text"][:sp[0]] + ch["clause"] + out["text"][sp[1]:], "P"), book)
            self.assertEqual((side, 100), (after["side"], after["qty"]), (book, ch))

    def test_c_the_autofill_property_seeds_offer_only_each_orders_own_labels(self):
        self.assertEqual([9, 9], [len(v) for v in SEEDS.values()])
        for seed, cases in SEEDS.items():
            for k, book, text in cases:
                with self.subTest(seed=seed, case=k, book=book):
                    self.assertEqual([], self.stray(text, book), text)


# ==== the page ===========================================================================================================================
PERSONAL, FUND = config.BOOKS[0], config.BOOKS[1]
P_USX = "P1:US_EXCHANGE"
STORE_KEY = "finsim.smart.draft.v2"
READY = "!!(window.T && T.router && T.router.current && T.router.current.page === 'smart' && document.querySelector('#smart-text') && T.pages.smart)"
CARDS = """(function () {
  return Array.prototype.map.call(document.querySelectorAll('#smart-cards [data-i]'), function (c) {
    return { i: Number(c.dataset.i), head: (c.querySelector('.smart-card-head') || {}).innerText || '',
             dropped: Array.prototype.map.call(c.querySelectorAll('.smart-dropped'), function (x) { return x.innerText; }) };
  });
})()"""
LAYOUT = """(function () {
  var m = document.querySelector('#main'), d = document.documentElement, out = [];
  Array.prototype.forEach.call(document.querySelectorAll('#smart-cards .smart-card'), function (c) {
    var cr = c.getBoundingClientRect();
    Array.prototype.forEach.call(c.querySelectorAll('*'), function (x) {
      var r = x.getBoundingClientRect();
      if (r.width > 0 && r.right > cr.right + 0.5) { out.push({ card: c.dataset.i, cls: String(x.className || ''), text: String(x.innerText || '').slice(0, 60),
                                                               past_px: Math.round(r.right - cr.right) }); }
    });
  });
  return { view: d.clientWidth, page: d.scrollWidth, main_client: m.clientWidth, main_scroll: m.scrollWidth, out: out,
           choices: Array.prototype.map.call(document.querySelectorAll('#smart-cards .smart-choices button'), function (b) { return b.innerText; }) };
})()"""
DASH = """(function () { var m = document.querySelector('#main'), d = document.documentElement;
  return { view: d.clientWidth, page: d.scrollWidth, main_client: m.clientWidth, main_scroll: m.scrollWidth }; })()"""
TICKET = """(function () {
  function el(id) { return document.getElementById(id); }
  var s = T.components.ticket.state() || {}; var sb = el('ticket-submit-btn');
  return { box: (el('ticket-instrument') || {}).value, submit: !!(sb && !sb.disabled), preview_body: s.preview_body || null,
           preview_text: ((el('ticket-preview') || {}).innerText || '').slice(0, 400), status: (el('ticket-status') || {}).innerText || '' };
})()"""
TOASTS_ON = """(function () {
  window.__toasts = window.__toasts || [];
  if (!window.__toastObs) {
    window.__toastObs = new MutationObserver(function (ms) {
      ms.forEach(function (m) { Array.prototype.forEach.call(m.addedNodes, function (n) { if (n.nodeType === 1) { window.__toasts.push(n.title || n.textContent || ''); } }); });
    });
    window.__toastObs.observe(document.querySelector('#toast-root'), { childList: true });
  }
  window.__toasts.length = 0;
  return true;
})()"""
SLOW_ORDERS = """(function () {
  if (!window.__f0) { window.__f0 = window.fetch; }
  var f0 = window.__f0;
  window.fetch = function (url, init) {
    var p = f0.apply(this, arguments);
    var path = String(url).replace(/^https?:\\/\\/[^\\/]+/, '');
    if (init && init.method === 'POST' && /^\\/api\\/orders(\\?|$)/.test(path)) {
      return p.then(function (res) { return new Promise(function (ok) { setTimeout(function () { ok(res); }, %d); }); });
    }
    return p;
  };
  return true; })()"""
FETCH_BACK = "(function () { if (window.__f0) { window.fetch = window.__f0; } return true; })()"


def _seed(home: str, clock: FakeClock, fp) -> None:
    """The Personal and the Fund books; the fictional listings ACME / WIDG and their prints; the ACME chain in the provider."""
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
        rt.worker.tick()
        fp.set_chain("ACME", SMK._acme_chain(clock))
    finally:
        rt.close()


def _served_chain(rt, fp) -> None:
    """The smoke test's invented ACME chain observed now in the served runtime (as its quote worker caches a fetched chain)."""
    now = int(rt.clock.epoch())
    rows = [ChainRow(occ_symbol("ACME", date.fromisoformat(e), r, Decimal(str(k))), e, r, qprice(k), qprice(b), qprice(a), 20, 20, None, None, 0.30,
                     None, None, None, None, 100, 10) for e, r, k, b, a in SMK.ACME_CHAIN]
    under = ChainUnderlying(symbol="ACME", last=qprice("60.00"), bid=None, ask=None, bid_size=None, ask_size=None, last_trade_time=now - 60, prev_close=None, iv30=0.30)
    snap = ChainSnapshot(underlying_id="ACME", cboe_symbol="ACME", underlying=under, contracts=rows, fetched_at=now, quote_time=now - 60, n_raw=len(rows))
    fp.set_chain("ACME", snap)
    rt.chains.put(snap)
    for rec in records_from_chain(snap, rt.instruments):
        rt.cache.put(rec, now)


class _TwoBookServer(SMK._Server):
    """``SMK._Server`` with the Personal and the Fund books (port 0, offline, the seeded FakeProvider)."""

    def __init__(self, home, clock, fp):
        super().__init__(home, clock, fp)
        self.thread = threading.Thread(target=self._run2, name="simplify-r3-serve", daemon=True)

    def _run2(self):
        self.rc = _server.serve(port=0, home=self.home, db=os.path.join(self.home, "terminal.db"), offline=True, ready=self._ready, out=self.msgs.append,
                                clock=self.clock, fake_provider=self.fp, books=[PERSONAL, FUND], fetch_threads=0, supervisor_interval=0.5, log=_Log())


@unittest.skipUnless(find_browser(), NO_BROWSER)
class SmartTicketPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from finsim.terminal.quotes.providers import FakeProvider
        cls.home = tempfile.mkdtemp(prefix="fst-simplify-r3-page-")
        cls.env = {k: os.environ.get(k) for k in (config.HOME_ENV, config.DB_ENV)}
        os.environ[config.HOME_ENV] = cls.home
        os.environ[config.DB_ENV] = os.path.join(cls.home, "terminal.db")
        cls.server = cls.browser = None
        try:
            clock = FakeClock(SMK.AT)
            cls.fp = FakeProvider(clock)
            _seed(cls.home, clock, cls.fp)
            cls.server = _TwoBookServer(cls.home, FakeClock(SMK.AT), cls.fp).start()
            _served_chain(cls.server.rt, cls.fp)
            cls.browser = Browser(find_browser(), 1440, 900)
            cls.sid = cls.browser.page("about:blank")
            cls.browser.navigate(cls.sid, cls.server.url)
            cls.browser.wait(cls.sid, "!!(window.T && T.router && T.router.current && document.querySelector('#page') && T.state.books && T.state.books.length === 2"
                                      " && T.book.code)", timeout=45)
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

    # ---- the page, as the owner uses it ---------------------------------------------------------------------------------------------
    def js(self, expr: str, sid=None):
        return self.browser.js(sid or self.sid, expr)

    def wait(self, expr: str, timeout: float = 30.0, sid=None):
        return self.browser.wait(sid or self.sid, expr, timeout=timeout)

    def press(self, key: str, vk: int, mods: int = 0, sid=None) -> None:
        base = {"key": key, "code": key, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk, "modifiers": mods}
        for kind in ("keyDown", "keyUp"):
            self.browser.call("Input.dispatchKeyEvent", dict(base, type=kind), session=sid or self.sid)

    def ctrl_f9(self, sid=None) -> None:
        self.press("F9", 120, mods=2, sid=sid)
        try:
            self.wait(READY, timeout=5, sid=sid)
        except TimeoutError:                                         # the key is the sidebar's link: the same route
            self.js("location.hash = '#/smart'; true", sid)
            self.wait(READY, sid=sid)

    def fresh_smart(self, book: str = "P", sid=None) -> None:
        """#/smart with no draft (this browser's drafts dropped) and ``book`` selected, from the Dashboard."""
        self.js("(function () { localStorage.removeItem(%s); location.hash = '#/dashboard'; return true; })()" % json.dumps(STORE_KEY), sid)
        self.wait("T.router.current && T.router.current.page === 'dashboard'", sid=sid)
        self.js("T.book.set(%s); true" % json.dumps(book), sid)
        self.wait("T.book.code === %s" % json.dumps(book), sid=sid)
        self.js("location.hash = '#/smart'; true", sid)
        self.wait(READY, sid=sid)
        self.browser.pump(0.4)

    def read(self, text: str, cards: int, sid=None) -> None:
        self.js("(function () { var t = document.querySelector('#smart-text'); t.focus(); t.value = %s; t.dispatchEvent(new Event('input', { bubbles: true }));"
                " return true; })()" % json.dumps(text), sid)
        self.press("Enter", 13, sid=sid)
        self.wait("!document.querySelector('#smart-parse').disabled && /^read /.test(document.querySelector('#smart-status').innerText)"
                  " && document.querySelectorAll('#smart-cards [data-i]').length === %d" % cards, sid=sid)

    def enter(self, sid=None) -> None:
        self.js("document.querySelector('#smart-text').focus(); true", sid)
        self.press("Enter", 13, sid=sid)
        self.wait("!document.querySelector('#smart-parse').disabled && /^read /.test(document.querySelector('#smart-status').innerText)", sid=sid)

    def open_card(self, i: int, acct: str, inst: str, sid=None) -> dict:
        self.js("document.querySelector('#smart-cards [data-i=\"%d\"] [data-act=\"open\"]').click(); true" % i, sid)
        self.wait("location.hash.indexOf(%s) === 0 && !!document.querySelector('#ticket-instrument') && document.querySelector('#ticket-instrument').value === %s"
                  % (json.dumps("#/a/" + acct.replace(":", "%3A")), json.dumps(inst)), timeout=30, sid=sid)
        self.wait("!document.querySelector('#ticket-submit-btn').disabled || !!document.querySelector('#ticket-preview .pv-blocker')", timeout=40, sid=sid)
        return self.js(TICKET, sid)

    def order_posts(self, since: int, sid=None) -> list:
        out = []
        for e in self.browser._events(sid or self.sid, since):
            if e.get("method") == "Network.requestWillBeSent":
                r = (e.get("params") or {}).get("request") or {}
                p = urlsplit(r.get("url") or "").path
                if r.get("method") not in ("GET", "HEAD", "OPTIONS") and p.startswith("/api/orders") and p != "/api/orders/preview":
                    out.append((r.get("method"), p))
        return out

    def held(self, acct: str, inst: str) -> int:
        with self.server.rt.db.reader() as conn:
            r = conn.execute("SELECT qty FROM v_positions WHERE account_id=? AND instrument_id=?", (acct, inst)).fetchone()
        return int(Decimal(str(r[0]))) if r else 0

    def set_acme(self, acct: str, book: str, qty: int) -> None:
        """``acct`` holds ``qty`` ACME: the difference bought or sold at market through the order route on a fresh print (another client)."""
        rt = self.server.rt
        held = self.held(acct, "ACME")
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
                "request_id": f"ui:simplify-r3-acme-{os.urandom(6).hex()}"}
        code, out, _h = rt.router.handle("POST", f"/api/orders?book={book}", None, body, "127.0.0.1", None)
        self.assertEqual((200, "FILLED"), (code, (out.get("order") or {}).get("status")), out)
        self.assertEqual(qty, self.held(acct, "ACME"))

    def tidy(self) -> None:
        """When the test ends: every order it left open cancelled, the page's fetch restored, the drafts dropped, the Dashboard, 1440 px."""
        rt = self.server.rt
        with rt.db.reader() as conn:
            first = int(conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM orders").fetchone()[0])
        self.js("(function () { try { if (T.components.ticket && T.components.ticket.state()) { T.components.ticket.clear(); } } catch (e) { /* none yet */ }"
                " return true; })()")

        def undo():
            with rt.db.reader() as conn:
                rows = [(r[0], r[1]) for r in conn.execute("SELECT order_id, account_id FROM orders WHERE rowid > ? AND status NOT IN ('FILLED', 'CANCELLED',"
                                                           " 'REJECTED', 'EXPIRED', 'REPLACED')", (first,))]
            for oid, acct in rows:
                rt.router.handle("DELETE", f"/api/orders/{oid}?book={acct[0]}", None, {"request_id": f"ui:simplify-r3-tidy-{os.urandom(6).hex()}"}, "127.0.0.1", None)
            self.browser.viewport(self.sid, 1440, 900)
            self.js(FETCH_BACK)
            self.js("(function () { localStorage.removeItem(%s); location.hash = '#/dashboard'; return true; })()" % json.dumps(STORE_KEY))
            self.browser.pump(0.6)
        self.addCleanup(undo)

    # ---- the tests --------------------------------------------------------------------------------------------------------------------
    def test_e_an_answer_that_comes_while_the_ticket_is_out_of_sight_is_said_in_a_toast_naming_its_order(self):
        """#1 "covered call 200 ACME Jan 15 2027 60" opened and previewed on 300 ACME held free; another client sells the 300; the owner
        Submits #1 and goes back to the Smart Ticket at once (the page's view of the answer 1.5 s late): the refusal 409 NOT_COVERED comes
        while the dock is hidden - a toast names #1 and says it was refused; opening the next card (fresh) clears the ticket, the toast
        was shown.  Nothing is placed but the set-up's share trades; the only order post is the owner's one Submit."""
        self.tidy()
        rt = self.server.rt
        _served_chain(rt, self.fp)
        self.set_acme(P_USX, "P", 300)
        self.fresh_smart("P")
        self.read("covered call 200 ACME Jan 15 2027 60; buy 5 WIDG limit 249", 2)
        t = self.open_card(0, P_USX, SMK.CALL_JAN60)
        self.assertTrue(t["submit"], t)
        self.js(TOASTS_ON)
        self.js(SLOW_ORDERS % 1500)
        start = self.browser.mark()
        self.set_acme(P_USX, "P", 0)                                   # sold from another client after #1's preview
        self.assertTrue(self.js("!document.querySelector('#ticket-submit-btn').disabled"), "the ticket re-previewed before the Submit: run again")
        self.js("document.querySelector('#ticket-submit-btn').click(); true")      # the owner's Submit of #1
        self.ctrl_f9()
        self.wait("(function () { var s = T.components.ticket.state(); return !!s && !s.submitting && !!(s.submit_err || s.stale || s.result); })()", timeout=20)
        self.browser.pump(0.6)
        self.assertEqual(("smart", False), (self.js("T.router.current.page"), self.js("T.dock.visible()")))
        code = self.js("(function () { var e = T.components.ticket.state().submit_err || {}; return e.code || null; })()")
        self.assertEqual("NOT_COVERED", code)
        toasts = [x for x in self.js("window.__toasts.slice()") if "out of sight" in x]
        self.assertEqual(1, len(toasts), self.js("window.__toasts.slice()"))
        self.assertIn("STO 2 " + SMK.CALL_JAN60, toasts[0])
        self.assertIn("refused", toasts[0])
        self.assertIn("the calls sold as covered need 200 ACME held free in P1:US_EXCHANGE", toasts[0])      # its refusal, in words
        self.enter()                                                   # the plan's flow goes on: Enter, the next card
        widg = [c["i"] for c in self.js(CARDS) if "WIDG" in c["head"]]
        self.assertEqual(1, len(widg))
        self.open_card(widg[0], P_USX, "WIDG")
        self.assertEqual([("POST", "/api/orders")], self.order_posts(start))
        with rt.db.reader() as conn:
            self.assertEqual(0, int(conn.execute("SELECT COUNT(*) FROM orders WHERE instrument_id=?", (SMK.CALL_JAN60,)).fetchone()[0]))
        # control: the same answer while the owner stays on the ticket is shown there, and no toast is added for it
        self.js(TOASTS_ON)
        self.js("location.hash = '#/smart'; true")
        self.wait(READY)
        self.set_acme(P_USX, "P", 300)
        self.enter()
        t = self.open_card(0, P_USX, SMK.CALL_JAN60)
        self.assertTrue(t["submit"], t)
        self.set_acme(P_USX, "P", 0)
        self.js("document.querySelector('#ticket-submit-btn').click(); true")
        self.wait("(function () { var s = T.components.ticket.state(); return !!s && !s.submitting && !!(s.submit_err || s.stale || s.result); })()", timeout=20)
        self.browser.pump(0.6)
        t = self.js(TICKET)
        self.assertIn("SUBMIT REFUSED", t["preview_text"].upper())
        self.assertIn("NOT_COVERED", t["preview_text"])
        self.assertTrue(t["status"].startswith("refused"), t)
        self.assertEqual([], [x for x in self.js("window.__toasts.slice()") if "out of sight" in x])

    def test_f_an_open_flags_alternatives_wrap_inside_the_card_where_the_dashboard_fits(self):
        """"stc 100 ACME; sto 100 WIDG" (300 ACME held), each card's red flag opened: its alternatives ("sell (sells the 300 held in
        P1:US_EXCHANGE)", "sell short (sto on shares opens a short)") stay inside the card and #main never scrolls sideways at the widths
        where the Dashboard fits (803baac: 19 px past at 520 px, 59 px past at 480 px)."""
        self.tidy()
        self.set_acme(P_USX, "P", 300)
        bad = {}
        for width in (640, 520, 480):
            self.browser.viewport(self.sid, width, 900)
            self.js("location.hash = '#/dashboard'; true")
            self.wait("T.router.current && T.router.current.page === 'dashboard' && !!document.querySelector('#page .tiles')", timeout=30)
            self.browser.pump(0.8)
            d = self.js(DASH)
            if d["page"] > d["view"] or d["main_scroll"] > d["main_client"]:
                continue                                               # the Dashboard does not fit here either
            self.fresh_smart("P")
            self.read("stc 100 ACME; sto 100 WIDG", 2)
            for i in (0, 1):
                self.js("document.querySelector('#smart-cards [data-i=\"%d\"] .smart-chip.sev-blocker').click(); true" % i)
                self.wait("!!document.querySelector('#smart-cards [data-i=\"%d\"] .smart-flagbox .smart-choices button')" % i, timeout=10)
                self.browser.pump(0.2)
                m = self.js(LAYOUT)
                if m["out"] or m["main_scroll"] > m["main_client"] or m["page"] > m["view"]:
                    bad.setdefault(width, []).append({"choices": m["choices"], "main": [m["main_client"], m["main_scroll"]], "out": m["out"][:3]})
        self.assertEqual({}, bad)

    def test_g_a_fresh_hand_off_on_the_same_instrument_writes_it_into_the_symbol_box(self):
        """#1 "buy 10 ACME limit 50" opened; the owner types WIDG in the ticket's symbol box (no Enter), goes back (Ctrl+F9), Enter, opens #2
        "buy 20 ACME limit 49": the box says ACME, as the order, its preview and the enabled Submit do (803baac: WIDG)."""
        self.tidy()
        start = self.browser.mark()
        self.fresh_smart("P")
        self.read("buy 10 ACME limit 50; buy 20 ACME limit 49", 2)
        self.open_card(0, P_USX, "ACME")
        self.js("(function () { var x = document.querySelector('#ticket-instrument'); x.focus(); x.select(); return true; })()")
        self.browser.call("Input.insertText", {"text": "WIDG"}, session=self.sid)
        self.browser.pump(0.8)
        self.ctrl_f9()
        self.wait(READY + " && document.querySelectorAll('#smart-cards [data-i]').length === 2")
        self.enter()
        self.js("document.querySelector('#smart-cards [data-i=\"1\"] [data-act=\"open\"]').click(); true")
        self.wait("location.hash.indexOf('#/a/P1%3AUS_EXCHANGE') === 0 && (function () { var s = T.components.ticket.state(); return !!s && s.qty === 20"
                  " && !s.dirty && !s.previewing && !!s.preview_body; })()", timeout=40)
        self.wait("!document.querySelector('#ticket-submit-btn').disabled || !!document.querySelector('#ticket-preview .pv-blocker')", timeout=30)
        self.browser.pump(0.6)
        t = self.js(TICKET)
        body = json.loads(t["preview_body"])
        self.assertEqual(("ACME", "BUY", 20, "49", True, "ACME"), (body["instrument_id"], body["side"], body["qty"], body.get("limit_px"), t["submit"], t["box"]), t)
        self.assertFalse(self.js("!document.querySelector('#dock-body .dd') || !document.querySelector('#dock-body .dd').hidden"))   # no look-up list open
        self.assertEqual([], self.order_posts(start))

    def test_h_another_window_never_drops_this_windows_edit_or_opened_mark(self):
        """Window 1 reads "buy 10 ACME limit 50; buy 5 WIDG limit 249"; window 2 opens #/smart (a copy of that draft, said) and reads it.
        In window 1 #2 is edited to 7 and #1 opened in the ticket; window 2 moves to the Dashboard (and saves its own draft).  Back on the
        Smart Ticket, window 1 still shows #1 opened and #2 BUY 7 WIDG (803baac: window 2's draft over them, unsaid); window 2, back too,
        shows its own: #1 not opened, #2 BUY 5 WIDG.  Nothing is posted to an order route."""
        self.tidy()
        start = self.browser.mark()
        self.fresh_smart("P")
        self.read("buy 10 ACME limit 50; buy 5 WIDG limit 249", 2)
        sid2 = self.browser.page("about:blank")
        try:
            self.browser.navigate(sid2, self.server.url + "#/smart")
            self.wait(READY + " && T.state.books && T.state.books.length === 2 && document.querySelectorAll('#smart-cards [data-i]').length === 2", timeout=45, sid=sid2)
            self.browser.pump(0.6)
            self.assertIn("a new window: a copy of the draft saved last", self.js("document.querySelector('#smart-status').innerText", sid2))
            self.enter(sid=sid2)
            self.js("(function () { var x = document.querySelector('#smart-cards [data-i=\"1\"] [data-edit=\"qty\"]'); x.value = '7';"
                    " x.dispatchEvent(new Event('change', { bubbles: true })); return true; })()")
            self.wait("/BUY 7 WIDG/.test(document.querySelector('#smart-cards [data-i=\"1\"] .smart-card-head').innerText)", timeout=10)
            self.open_card(0, P_USX, "ACME")
            self.js("location.hash = '#/dashboard'; true", sid2)
            self.wait("T.router.current && T.router.current.page === 'dashboard'", sid=sid2)
            self.browser.pump(0.4)
            self.ctrl_f9()
            self.wait(READY + " && document.querySelectorAll('#smart-cards [data-i]').length === 2")
            self.browser.pump(0.4)
            back = self.js(CARDS)
            self.assertIn("opened in the ticket", back[0]["head"], back)
            self.assertIn("BUY 7 WIDG", back[1]["head"], back)
            self.js("location.hash = '#/smart'; true", sid2)
            self.wait(READY + " && document.querySelectorAll('#smart-cards [data-i]').length === 2", sid=sid2)
            self.browser.pump(0.4)
            other = self.js(CARDS, sid2)
            self.assertNotIn("opened in the ticket", other[0]["head"], other)
            self.assertIn("BUY 5 WIDG", other[1]["head"], other)
        finally:
            try:
                self.browser.call("Target.closeTarget", {"targetId": self.browser.call("Target.getTargetInfo", session=sid2)["targetInfo"]["targetId"]})
            except Exception:  # noqa: BLE001 - the window may already be gone
                pass
        self.assertEqual([], self.order_posts(start))


if __name__ == "__main__":
    unittest.main()
