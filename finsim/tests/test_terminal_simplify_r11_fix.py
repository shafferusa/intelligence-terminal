"""FinSim 1.0.7.2 simplify review round 11 (on f74f220): the regression tests of its findings, kept in the terminal suite.

R11-P1 (page lens, major).  Round 10 dropped the rest of a hand-off click on the POINT of that click only (4 px around it): a second click
of its own a few pixels away on the same "Open in ticket" (an impatient second click, 0.55 s later), or the second press of a double click
that drifted out of the system's double-click rectangle (Windows' default is 4 x 4 px: a click count of 1 then), moved the pointer more
than 4 px - the rest ended - and landed on the docked ticket's Submit armed there: the order handed over was SENT before its preview was
seen.  Now (pages/smartticket.js dropGestureRest) the rest of a hand-off click covers the BUTTON that was clicked - its box grown by 4 px -
until the pointer is seen outside that grown box or 2 s have passed, and any press inside the double-click time (500 ms) of that click,
wherever it is: swallowed with its release, click and double click, and said ("not taken: ..."), for every hand-off (a card's Open in
ticket, a group's Strategies tab, a flag's own ticket: one handTicket).
  PageTests (real time; tests/test_reviews11_page_near_second_click.py):
    test_a  1280 x 800, card #3 of "buy 10 ACME limit 50; buy 10 WIDG limit 249; buy 12 WIDG stop 251 limit 252 gtd Dec 18 2026": a click
            on the middle of its Open in ticket, then - 0.55 s later at least, the docked Submit for that order armed under the second
            point - the pointer moved 6 px right and a second click of its own there (still on the button, and on Submit): nothing sent,
            said, the order handed over kept;
    test_b  1366 x 768, the same without the GTD date, on the part of the button over the docked Submit, the second click 5 px lower;
    test_e  test_a's page and point as a DOUBLE CLICK whose second press drifts 6 px (click count 1, as the system gives it), as soon as
            Submit is armed under it - inside the double-click time: nothing sent (said when it landed on the armed Submit).
  RestTests (the page's clock held - virtual time - and the mouse events that reach the page's handlers recorded, as
  tests/test_props11_handoff_rest.py does; its harness copied here, that property module is not shipped):
    test_f  the double-click time, anywhere: at 1280 x 800 card #3 handed over by a click on the middle of its Open in ticket, its Submit
            armed; the pointer moved onto the part of that Submit OUTSIDE the button's grown box and pressed there 300 ms after the
            hand-off click: nothing reaches the page, said ("... inside the double-click time ..."); the control: the same press 600 ms
            after the hand-off click (the pointer off the button since) reaches Submit and places the order once.
R11-T1 (property tester, minor; tests/test_props11_handoff_rest.py seed 11110201 hand-off 2, gestures #1-#5; reduced in
tests/test_reviews11_property_tester_rest_release.py).  Inside the rest a press on a SCROLLBAR was swallowed but its release - a mouseup
of click count 0 in Edge / Chrome, and no click - reached the page: the rule decided each event by its own click count.  Now a press is
decided at its mousedown and its release, click and double click follow it; only a click of count 0 (one the keyboard makes) is never
part of a press.
    test_seed_11110201_handoff_2  card #3 of the instruction above handed over at 1440 x 900 by a click at the seed's point (the left end of
            its Open in ticket: on the account page it opens, <main>'s vertical scrollbar) and the walk's gestures #0-#5 replayed on the
            page's clock (14 presses within 4 px of the point, 60-1,400 ms after the click, click counts 1 and 2; all on the scrollbar but
            the first): none reaches the page's handlers and each press off a scrollbar is said; then, the rest over (2,500 ms), a press
            there reaches the page as the browser gives it (mousedown of count 1, mouseup of count 0, no click).
R11-T2 (property tester, minor; tests/test_props11_parse_oracle.py seed 11110101 cases 484 and 1588; reduced in
tests/test_reviews11_property_tester_strategies_tab.py).  A strategy written "in the Personal and Fund books" with a word asking for one
order, after another order, read as two groups - the other order with the Personal copy, and the Fund copy alone - and the Fund copy,
the strategy alone, named no strategy and offered no "Strategies tab" (smart_ticket.Resolver named a group's copies only when the group
was one clause).  Now each copy is named for its own orders: a copy that is one strategy alone reads as that strategy written alone does
(its name, its Strategies tab, its words); a copy that mixes orders stays "these orders", with no tab.  The orders are unchanged.
  StrategiesTabTests (round 7's world, test_terminal_simplify_r7._comma_world):
    test_seed_11110101_case_484   "short 10 WIDG in the Institutional book; sell 2x ACME Dec 18, 2026 90/95 put vertical in the Personal and
                                  Fund books aon";
    test_seed_11110101_case_1588  "short 50 WIDG in the Fund book then buy 3 ACME Dec 18, 2026 105/115 call spread in the Personal and Fund
                                  books together";
    test_control_the_strategy_alone_in_both_books  the same words without the order before them: both copies offer it.
  SourceTests  where these rules live.
The page worlds are round 9's (tests/test_terminal_simplify_r9.py ``_seed9``: an offline server on port 0 in a temp home, the Personal and
the Fund books, the fictional ACME / WIDG and the ACME chain; a headless Edge / Chrome over CDP, trusted input).  Nothing touches
~/.finsim, the network or the ports 8765 / 8865; only test_f places an order, in the temp home.
Checked against f74f220 (2026-11-17, py-3.12; its pages/smartticket.js served from a scratch copy of the static folder, its
smart_ticket.py loaded in place of the fixed one): test_a, test_b, test_e, test_f and test_seed_11110201_handoff_2 fail - the page sent
POST /api/orders (test_e: the second press 0.34 s after the first, on the armed Submit; test_f: at 300 ms), and gesture #1's release
(mouseup, click count 0) reached <main>; test_seed_11110101_case_484 and _1588 fail (the Fund copy names no strategy) and the parse
control passes there too.  On the fix every test passes.
"""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import tempfile
import time
import unittest

from tests import test_terminal_browser_smoke as SMK
from tests import test_terminal_simplify_r6 as R6
from tests import test_terminal_simplify_r7 as R7
from tests import test_terminal_simplify_r8 as R8
from tests import test_terminal_simplify_r9 as R9
from tests import test_terminal_simplify_r10_fix as R10F
from tests.terminal_cdp import NO_BROWSER, Browser, find_browser
from finsim.terminal import config
from finsim.terminal import smart_ticket as st
from finsim.terminal.clock import FakeClock
from finsim.terminal.quotes.providers import FakeProvider

TERMINAL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "finsim", "terminal")
SMART = os.path.join(TERMINAL, "static", "pages", "smartticket.js")
REST_PX = 4                               # the rest covers the button's box grown by this (pages/smartticket.js REST_PX)
MIN_GAP_S = R10F.MIN_GAP_S                # 0.55 s: after Windows' default double-click time, a click of its own
ARM_BY_S = R10F.ARM_BY_S                  # 1.6 s: the second click comes by then, inside the rest's 2 s, Submit armed or not
REST_SAID = R10F.REST_SAID                # said of a press on the button clicked
SOON_SAID = re.compile(r'^not taken: that click came right after "Open in ticket" was pressed \(inside the double-click time, 500 ms\), as its'
                       r" ticket came up - nothing was sent or changed; look at the ticket, then click the button you want$")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _start(cls, prefix: str) -> None:
    """Round 9's world for the class: an offline server on port 0 in a temp home and one headless browser on it."""
    cls.home = tempfile.mkdtemp(prefix=prefix)
    cls.env = {k: os.environ.get(k) for k in (config.HOME_ENV, config.DB_ENV)}
    os.environ[config.HOME_ENV] = cls.home
    os.environ[config.DB_ENV] = os.path.join(cls.home, "terminal.db")
    cls.server = cls.browser = None
    try:
        clock = FakeClock(SMK.AT)
        cls.fp = FakeProvider(clock)
        R9._seed9(cls.home, clock, cls.fp)
        cls.server = R9._Server9(cls.home, FakeClock(SMK.AT), cls.fp).start()
        cls.server.rt.curve.put(R9.CURVE, persist=False)
        SMK.BrowserSmokeTests._served_chain(cls)                         # the chain as the quote worker caches a fetched one
        cls.browser = Browser(find_browser(), 1440, 900)
        cls.sid = cls.browser.page("about:blank")
        cls.browser.navigate(cls.sid, cls.server.url)
        cls.browser.wait(cls.sid, R9.LOADED, timeout=45)
    except Exception:
        _stop(cls)
        raise


def _stop(cls) -> None:
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


@unittest.skipUnless(find_browser(), NO_BROWSER)
class PageTests(unittest.TestCase):
    """R11-P1 in real time, the pointer CDP's (Input.dispatchMouseEvent, with its click count)."""

    @classmethod
    def setUpClass(cls):
        _start(cls, "fst-simplify-r11-fix-")

    @classmethod
    def tearDownClass(cls):
        _stop(cls)

    # the page as the owner uses it (test_terminal_simplify_r6's, _r8's and _r10_fix's helpers)
    js = R6.AnswerFencePageTests.js
    wait = R6.AnswerFencePageTests.wait
    enter_key = R6.AnswerFencePageTests.enter_key
    fresh_smart = R6.AnswerFencePageTests.fresh_smart
    read = R6.AnswerFencePageTests.read
    mouse = R8.PageTests.mouse
    click_xy = R8.PageTests.click_xy
    open_rect = R8.PageTests.open_rect
    hand_once = R8.PageTests.hand_once
    order_posts = R8.PageTests.order_posts
    tidy = R10F.PageTests.tidy
    rows = R10F.PageTests.rows
    orders = R10F.PageTests.orders
    refused_since = R10F.PageTests.refused_since

    def second_click_near(self, text: str, cards: int, idx: int, inst: str, dx: float, dy: float, point=None,
                          min_gap: float = MIN_GAP_S, by: float = ARM_BY_S):
        """``text`` read; a click on card #idx+1's Open in ticket (at ``point(rect)`` or its middle), then the pointer moved by (dx, dy) -
        still on the button - and a second press there (click count 1) at least ``min_gap`` s after the first, once the docked ticket's
        Submit for that order is armed under it; at ``by`` s at the latest, armed or not (never a skip: the rule is that nothing is sent).
        Returns (the button's box, the first point, the second point, the gap, whether Submit was armed under the second point, what was
        under it)."""
        self.fresh_smart("P")
        self.read(text, cards)
        r = self.open_rect(idx)
        x, y = point(r) if point else R8._middle(r)
        x2, y2 = x + dx, y + dy
        self.assertTrue(r["left"] < x2 < r["right"] and r["top"] < y2 < r["bottom"], f"the second point {(x2, y2)} is not on the button {r}")
        gen0 = self.js(R8.GEN)
        self.click_xy(x, y)
        t0, armed_under = time.monotonic(), False
        while time.monotonic() - t0 < by:
            s = self.js(R10F.ARMED_UNDER % (x2, y2, json.dumps(gen0), json.dumps(inst))) or {}
            armed_under = bool(s.get("previewed") and s.get("armed") and s.get("under"))
            if armed_under and time.monotonic() - t0 >= min_gap:
                break
            time.sleep(0.01)
        under = self.js(R8.UNDER % (x2, y2))
        gap = time.monotonic() - t0
        self.mouse("mouseMoved", x2, y2)
        self.mouse("mousePressed", x2, y2, 1)
        self.mouse("mouseReleased", x2, y2, 1)
        self.browser.pump(2.0)                                          # what the second press could start has its answer
        return r, (x, y), (x2, y2), gap, armed_under, under

    def assert_nothing_sent(self, size, p1, p2, gap, armed_under, under, start: int, before: list, n: int, must_say: bool = True) -> None:
        detail = (f"at {size}, a click on {tuple(round(v) for v in p1)}, then a second press of its own (click count 1) on"
                  f" {tuple(round(v) for v in p2)} {gap:.2f} s later, on {under} (Submit armed under it: {armed_under})")
        self.assertEqual([], self.order_posts(start), detail)
        self.assertEqual(before, self.orders(), detail)
        said = [x for x in self.refused_since(n) if x.get("key") == "smart.handoff.rest"]
        if must_say:
            self.assertEqual(1, len(said), f"{detail}: said {said}")
            self.assertRegex(said[0]["msg"], REST_SAID)                 # the press came on the button that was clicked
        else:
            self.assertLessEqual(len(said), 1, f"{detail}: said {said}")
        t = self.js(R8.TICKET)
        self.assertEqual(("BUY", "12", "WIDG", "STOP_LIMIT", "251", "252"), (t["side"], t["qty"], t["inst"], t["type"], t["stop"], t["limit"]),
                         f"{detail}: the order handed over stays: {t}")
        self.assertEqual([], self.browser.errors(self.sid, start))

    def test_a_a_second_click_6px_from_the_first_on_open_in_ticket_sends_nothing(self):
        self.tidy()
        size = (1280, 800)
        self.browser.viewport(self.sid, *size)
        before, start = self.orders(), self.browser.mark()
        self.hand_once(R8.TEXT_A, 3, 2, "WIDG")                         # one hand-off from script (no point: no rest): its preview warm
        self.assertEqual([], self.order_posts(start))
        n = len(self.js(R10F.REFUSED) or [])
        _r, p1, p2, gap, armed_under, under = self.second_click_near(R8.TEXT_A, 3, 2, "WIDG", 6, 0)
        self.assertGreaterEqual(gap, MIN_GAP_S - 0.01)
        self.assert_nothing_sent(size, p1, p2, gap, armed_under, under, start, before, n)

    def test_b_a_second_click_5px_below_the_first_on_the_part_over_submit_sends_nothing(self):
        self.tidy()
        size = (1366, 768)
        self.browser.viewport(self.sid, *size)
        before, start = self.orders(), self.browser.mark()
        sb = self.hand_once(R8.TEXT_B, 3, 2, "WIDG")                    # where the docked Submit is for card #3's order (nothing sent)
        self.assertEqual([], self.order_posts(start))
        n = len(self.js(R10F.REFUSED) or [])

        def over_submit(r):
            ov = R8._overlap(r, sb)
            if not ov:
                return R8._middle(r)
            return R8._middle(ov)[0], max(ov["top"], r["top"]) + 3      # high on the part over Submit: 5 px lower is on both still
        _r, p1, p2, gap, armed_under, under = self.second_click_near(R8.TEXT_B, 3, 2, "WIDG", 0, 5, point=over_submit)
        self.assert_nothing_sent(size, p1, p2, gap, armed_under, under, start, before, n)

    def test_e_a_double_click_whose_second_press_drifts_6px_sends_nothing(self):
        """The second press inside the system's double-click time, 6 px from the first: outside Windows' double-click rectangle the system
        gives it a click count of 1 - so does this test."""
        self.tidy()
        size = (1280, 800)
        self.browser.viewport(self.sid, *size)
        before, start = self.orders(), self.browser.mark()
        self.hand_once(R8.TEXT_A, 3, 2, "WIDG")
        self.assertEqual([], self.order_posts(start))
        n = len(self.js(R10F.REFUSED) or [])
        _r, p1, p2, gap, armed_under, under = self.second_click_near(R8.TEXT_A, 3, 2, "WIDG", 6, 0, min_gap=0.0, by=R8.DOUBLE_CLICK_S - 0.06)
        self.assertLess(gap, R8.DOUBLE_CLICK_S)
        self.assert_nothing_sent(size, p1, p2, gap, armed_under, under, start, before, n, must_say=armed_under)


# ---- tests/test_props11_handoff_rest.py's harness (copied: that property module is not shipped) -------------------------------------
#: the page's clock held (Date.now moved only by STEP), and the mouse events that reach the document's handlers recorded per gesture
#: (a press the Smart Ticket's window listener swallows never reaches them), with the order requests sent and the rests said
INSTALL = r"""(function () {
  if (window.__r11f) { return true; }
  var P = window.__r11f = { off: 0, g: 0, rec: [], sends: [], said: [] };
  var base = Date.now();
  Date.now = function () { return base + P.off; };
  ['mousedown', 'mouseup', 'click', 'dblclick'].forEach(function (t) {
    document.addEventListener(t, function (e) {
      var tg = e.target && e.target.closest ? e.target : null;
      P.rec.push({ g: P.g, t: e.type, d: e.detail, sub: !!(tg && tg.closest('#ticket-submit-btn')), el: tg ? (tg.id || tg.tagName) : null });
    }, true);
  });
  var f0 = window.fetch;
  window.fetch = function (u, init) {
    try {
      var m = String((init && init.method) || 'GET').toUpperCase(), p = String(u && u.url ? u.url : u).split('?')[0];
      if (m !== 'GET' && m !== 'HEAD' && /\/api\/orders(\/|$)/.test(p) && !/\/api\/orders\/preview$/.test(p)) { P.sends.push({ g: P.g, m: m, p: p }); }
    } catch (e) { /* recording only */ }
    return f0.apply(this, arguments);
  };
  var r = T.guard.refused, push0 = r.push;
  r.push = function (x) { if (x && x.key === 'smart.handoff.rest') { P.said.push({ g: P.g, msg: x.msg }); } return push0.apply(this, arguments); };
  return true;
})()"""
#: what gesture ``g`` - 1 left (its recorded events, order requests, rests said), then the page set for gesture ``g`` (its clock moved ``dt`` ms)
STEP = r"""(function (g, dt) {
  var P = window.__r11f;
  var out = { rec: P.rec.filter(function (r) { return r.g === g - 1; }), sends: P.sends.filter(function (s) { return s.g === g - 1; }),
              said: P.said.filter(function (x) { return x.g === g - 1; }).map(function (x) { return x.msg; }) };
  P.rec = P.rec.filter(function (r) { return r.g >= g; });
  P.g = g; P.off += dt;
  return out;
})(%d, %d)"""
READY_TICKET = R8.PREVIEWED + " && " + R8.ARMED
#: per point: on a scrollbar of the element under it or of one of its ancestors (Edge / Chrome dispatch a press there to the page - its
#: mousedown and a mouseup of click count 0 - but make it no click)
SCROLLBAR_AT = r"""(function (pts) {
  return pts.map(function (p) {
    var x = p[0], y = p[1];
    for (var a = document.elementFromPoint(x, y); a && a.nodeType === 1; a = a.parentElement) {
      if (!(a.clientWidth > 0 && a.clientHeight > 0)) { continue; }
      var r = a.getBoundingClientRect(), cs = getComputedStyle(a), root = a === document.documentElement;
      var br = parseFloat(cs.borderRightWidth) || 0, bb = parseFloat(cs.borderBottomWidth) || 0;
      var vx = r.left + a.clientLeft + a.clientWidth, hy = r.top + a.clientTop + a.clientHeight;
      if ((root || /auto|scroll/.test(cs.overflowY)) && r.right - br - vx >= 3 && x >= vx && x < r.right - br && y >= r.top && y < r.bottom) { return true; }
      if ((root || /auto|scroll/.test(cs.overflowX)) && r.bottom - bb - hy >= 3 && y >= hy && y < r.bottom - bb && x >= r.left && x < r.right) { return true; }
    }
    return false;
  });
})(%s)"""
#: tests/test_props11_handoff_rest.py at its seed 11110201, hand-off 2 (card #3 of R8.TEXT_A at 1440 x 900, by the left end of its Open in
#: ticket): the walk's gestures #0-#5 as its streams drew them (random.Random("11110201:2:<i>")), each a "rest" - (ms the page's clock
#: moved since the gesture before, [(dx, dy, click count) of each press from the hand-off point]); #6 is "late": 1,100 ms more, the rest over
SEED = 11110201
SEED_H2 = [(60, [(-4, 3, 2)]), (20, [(-1, -1, 1)]), (60, [(3, -2, 1), (-2, 4, 1)]), (600, [(1, 0, 1), (0, 4, 2), (-3, -1, 1)]),
           (60, [(1, -1, 1), (1, -2, 1), (2, -3, 1)]), (600, [(-1, 2, 1), (2, 2, 2)])]
SEED_H2_LATE = 1100


def _seed_point(r: dict) -> tuple:
    """Hand-off 2's point on the button ``r`` as the walk drew it (random.Random("11110201:2"): its height, then the left end)."""
    rnd = random.Random(f"{SEED}:2")
    y = rnd.randint(int(r["top"]) + 2, int(r["bottom"]) - 3)
    lo, hi = int(r["left"]) + 2, int(r["right"]) - 3
    return rnd.randint(lo, min(hi, lo + 6)), y


def _outside(sb: dict, r: dict, grow: float):
    """A point of the box ``sb`` outside the box ``r`` grown by ``grow`` px (the middle of the widest strip of ``sb`` beside it), or None."""
    if not sb or not r:
        return None
    cx, cy = R8._middle(sb)
    strips = [((r["right"] + grow + 2, sb["right"] - 2), "x"), ((sb["left"] + 2, r["left"] - grow - 2), "x"),
              ((r["bottom"] + grow + 2, sb["bottom"] - 2), "y"), ((sb["top"] + 2, r["top"] - grow - 2), "y")]
    (a, b), axis = max(strips, key=lambda s: s[0][1] - s[0][0])
    if b - a < 2:
        return None
    return (round((a + b) / 2.0), round(cy)) if axis == "x" else (round(cx), round((a + b) / 2.0))


@unittest.skipUnless(find_browser(), NO_BROWSER)
class RestTests(unittest.TestCase):
    """R11-P1's double-click time and R11-T1 on the page's clock held (virtual time): what reaches the page's handlers, gesture by gesture."""

    @classmethod
    def setUpClass(cls):
        cls.g = 0
        _start(cls, "fst-simplify-r11-fix-rest-")

    @classmethod
    def tearDownClass(cls):
        _stop(cls)

    js = R6.AnswerFencePageTests.js
    wait = R6.AnswerFencePageTests.wait
    enter_key = R6.AnswerFencePageTests.enter_key
    fresh_smart = R6.AnswerFencePageTests.fresh_smart
    read = R6.AnswerFencePageTests.read
    mouse = R8.PageTests.mouse
    open_rect = R8.PageTests.open_rect
    rows = R10F.PageTests.rows
    orders = R10F.PageTests.orders
    wait_rows = R10F.PageTests.wait_rows

    def next_g(self) -> int:
        type(self).g += 2                                               # one numbering for the class: a gesture's records are its own
        return type(self).g

    def hand_off(self, size, text: str, cards: int, idx: int, inst: str, point_of):
        """``text`` read at ``size``; card #idx+1's Open in ticket clicked at ``point_of(rect)`` (1 s after the gesture before on the page's
        clock: no double-click window of a gesture before is open), its ticket previewed and armed.  Returns (the button's box, the point)."""
        self.browser.viewport(self.sid, *size)
        self.fresh_smart("P")
        self.js(INSTALL)
        self.read(text, cards)
        r = self.open_rect(idx)
        x, y = point_of(r)
        self.assertTrue(r["left"] < x < r["right"] and r["top"] < y < r["bottom"], f"the point {(x, y)} is not on the button {r}")
        gen0 = self.js(R8.GEN)
        self.js(STEP % (self.next_g(), 1000))
        self.mouse("mouseMoved", x, y)
        self.mouse("mousePressed", x, y, 1)
        self.mouse("mouseReleased", x, y, 1)
        self.wait(READY_TICKET % (json.dumps(gen0), json.dumps(inst)), timeout=30)
        return r, (x, y)

    def gesture(self, dt: int, presses, move=None) -> dict:
        """One gesture ``dt`` ms after the one before on the page's clock: the pointer moved to ``move`` (if given), then each press
        ((x, y, click count)) pressed and released.  Returns what reached the page's handlers ((type, click count, element) each), the
        rests said and the order requests sent."""
        g = self.next_g()
        self.js(STEP % (g, dt))
        if move is not None:
            self.mouse("mouseMoved", move[0], move[1])
        for x, y, c in presses:
            self.mouse("mousePressed", x, y, c)
            self.mouse("mouseReleased", x, y, c)
        self.browser.pump(0.4)
        out = self.js(STEP % (g + 1, 0))
        return {"rec": [(r["t"], r["d"], r["el"]) for r in out["rec"]], "said": out["said"], "sends": out["sends"]}

    def test_f_a_press_anywhere_inside_the_double_click_time_and_the_control_after_it(self):
        start, before = self.browser.mark(), self.orders()
        r, _p = self.hand_off((1280, 800), R8.TEXT_A, 3, 2, "WIDG", R8._middle)
        sb = self.js(R8.RECT % json.dumps("#ticket-submit-btn"))
        pt = _outside(sb, r, REST_PX)
        self.assertIsNotNone(pt, f"the world changed: the docked Submit {sb} lies inside card #3's Open in ticket {r} grown by {REST_PX} px")
        under = self.js(R8.UNDER % pt)
        self.assertEqual(("ticket-submit-btn", True), (under["button"], under["enabled"]), f"the world changed: under {pt} is {under}")
        out = self.gesture(300, [(pt[0], pt[1], 1)], move=pt)
        detail = f"Submit pressed at {pt}, outside the button clicked {r}, 300 ms after the hand-off click"
        self.assertEqual(([], []), (out["rec"], out["sends"]), detail)
        self.assertEqual(1, len(out["said"]), f"{detail}: said {out['said']}")
        self.assertRegex(out["said"][0], SOON_SAID)
        t = self.js(R8.TICKET)
        self.assertEqual(("BUY", "12", "WIDG", "STOP_LIMIT", True), (t["side"], t["qty"], t["inst"], t["type"], t["submit"]), f"{detail}: {t}")
        self.assertEqual(before, self.orders(), detail)
        # the control: the same press 600 ms after the hand-off click - the double-click time over, the pointer off the button since -
        # is a click of its own on Submit: it reaches the page and places the order once
        out = self.gesture(300, [(pt[0], pt[1], 1)])
        self.assertEqual([("mousedown", 1, "ticket-submit-btn"), ("mouseup", 1, "ticket-submit-btn"), ("click", 1, "ticket-submit-btn")], out["rec"])
        self.assertEqual(([], 1), (out["said"], len(out["sends"])), out)
        placed = self.wait_rows(self.orders, before)
        self.assertEqual([("WIDG", "BUY", 12, "STOP_LIMIT")], [(o[1], o[2], int(o[3]), o[4]) for o in placed])
        self.assertEqual([], self.browser.errors(self.sid, start))

    def test_seed_11110201_handoff_2(self):
        start, before = self.browser.mark(), self.orders()
        r, (px, py) = self.hand_off((1440, 900), R8.TEXT_A, 3, 2, "WIDG", _seed_point)
        pts = [[px + dx, py + dy] for _dt, burst in SEED_H2 for dx, dy, _c in burst]
        bars = self.js(SCROLLBAR_AT % json.dumps(pts))
        self.assertTrue(all(bars[1:]), f"the world changed: hand-off 2 at {(px, py)} on card #3's Open in ticket {r} - presses #1-#5 on a scrollbar"
                                       f" of the account page: {list(zip(map(tuple, pts), bars))}")
        i, elapsed = 0, 0
        for k, (dt, burst) in enumerate(SEED_H2):
            elapsed += dt
            presses = [(px + dx, py + dy, c) for dx, dy, c in burst]
            on_bar = bars[i:i + len(burst)]
            i += len(burst)
            out = self.gesture(dt, presses)
            detail = f"gesture #{k}: presses {presses} {elapsed} ms after the hand-off click at {(px, py)} (on a scrollbar: {on_bar})"
            self.assertEqual([], out["rec"], f"{detail}: inside the rest, but the page's handlers received these")
            self.assertEqual([], out["sends"], detail)
            self.assertEqual(sum(1 for b in on_bar if not b), len(out["said"]), f"{detail}: each press off a scrollbar said: {out['said']}")
            for m in out["said"]:
                self.assertRegex(m, REST_SAID)
        # the rest over (the walk's gesture #6, "late"): a press there is the browser's own - on the scrollbar, a mousedown of click count
        # 1 and a mouseup of click count 0, no click
        out = self.gesture(SEED_H2_LATE, [(px - 1, py - 1, 1)])
        self.assertEqual(([("mousedown", 1), ("mouseup", 0)], [], []), ([(t, d) for t, d, _e in out["rec"]], out["said"], out["sends"]),
                         f"{elapsed + SEED_H2_LATE} ms after the hand-off click: {out}")
        self.assertEqual(before, self.orders())
        self.assertEqual([], self.browser.errors(self.sid, start))


class StrategiesTabTests(unittest.TestCase):
    """R11-T2 on the pure parse (round 7's world: the books P / F / I, Personal selected, ACME at 100.00 with the invented chain)."""
    CASE_484 = "short 10 WIDG in the Institutional book; sell 2x ACME Dec 18, 2026 90/95 put vertical in the Personal and Fund books aon"
    CASE_1588 = "short 50 WIDG in the Fund book then buy 3 ACME Dec 18, 2026 105/115 call spread in the Personal and Fund books together"

    @classmethod
    def setUpClass(cls):
        cls.env = {k: os.environ.get(k) for k in (config.HOME_ENV, config.DB_ENV)}
        cls.tmp, cls.rt = R7._comma_world()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.rt.close()
        finally:
            shutil.rmtree(cls.tmp, ignore_errors=True)
            for k, v in cls.env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def read(self, text: str) -> dict:
        return st.parse_view(self.rt, {"text": text, "book": "P"})

    @staticmethod
    def orders(out: dict, combo: dict) -> list:
        legs = {l["n"]: l for l in out["legs"]}
        return [(legs[n]["book"], legs[n]["account_id"], (legs[n]["instrument"] or {}).get("id"), legs[n]["side"], legs[n]["qty"]) for n in combo["legs"]]

    @staticmethod
    def words(combo: dict) -> list:
        return [(f["code"], f["severity"], f["text"], f.get("action")) for f in combo["flags"]]

    def assert_copy_is_the_strategy(self, text: str, alone: str, name: str, word: str) -> None:
        out, out2 = self.read(text), self.read(alone)
        self.assertEqual([], out["unparsed"], text)
        fund = [c for c in out["combos"] if c["account_id"] == "F1:US_EXCHANGE"]
        fund2 = [c for c in out2["combos"] if c["account_id"] == "F1:US_EXCHANGE"]
        self.assertEqual((1, 1), (len(fund), len(fund2)), (out["combos"], out2["combos"]))
        g, g2 = fund[0], fund2[0]
        # the Fund copy: the strategy alone - its orders, its name, its Strategies tab and its words as the strategy written alone reads them
        self.assertEqual(self.orders(out2, g2), self.orders(out, g), f"{text!r}: the Fund copy's orders")
        self.assertEqual((name, name), (g["strategy"], g2["strategy"]), text)
        self.assertEqual(self.words(g2), self.words(g), f"{text!r}: the Fund copy {g['legs']} reads as the strategy written alone")
        tabs = [f["action"] for f in g["flags"] if isinstance(f.get("action"), dict) and f["action"].get("kind") == "strategies_tab"]
        self.assertEqual([{"kind": "strategies_tab", "label": "Strategies tab", "instrument_id": "ACME", "account_id": "F1:US_EXCHANGE"}], tabs, text)
        one = [f for f in g["flags"] if f["code"] == "ONE_ORDER_ASKED"]
        self.assertEqual(1, len(one), g["flags"])
        self.assertEqual(("blocker", f"'{word}' asks for one order; the Smart Ticket fills separate tickets that go and fill on their own; for one"
                                     f" all-or-none order, use the Strategies tab"), (one[0]["severity"], one[0]["text"]))
        # the copy that mixes the other order with the Personal copy stays "these orders": no strategy named, no tab, still held
        mixed = [c for c in out["combos"] if c is not g]
        self.assertEqual(1, len(mixed), out["combos"])
        m = mixed[0]
        self.assertEqual(3, len(m["legs"]), m)
        self.assertIsNone(m["strategy"], m)
        self.assertEqual([], [f for f in m["flags"] if isinstance(f.get("action"), dict) and f["action"].get("kind") == "strategies_tab"])
        self.assertIn("ONE_ORDER_ASKED", [f["code"] for f in m["flags"] if f["severity"] == "blocker"])
        sep = [f["text"] for f in m["flags"] if f["code"] == "SEPARATE_TICKETS"]
        self.assertEqual(1, len(sep), m["flags"])
        self.assertTrue(sep[0].startswith("these orders as 3 separate tickets: "), sep)
        # every card held (the words asking for one order): nothing goes as it stands
        self.assertFalse(out["ok"], text)
        self.assertTrue(all(l["request"] is None for l in out["legs"]), [(l["n"], l["request"]) for l in out["legs"]])

    def test_seed_11110101_case_484(self):
        self.assert_copy_is_the_strategy(self.CASE_484, self.CASE_484.split("; ", 1)[1], "put spread", "aon")

    def test_seed_11110101_case_1588(self):
        self.assert_copy_is_the_strategy(self.CASE_1588, self.CASE_1588.split(" then ", 1)[1], "call spread", "together")

    def test_control_the_strategy_alone_in_both_books(self):
        for text in (self.CASE_484.split("; ", 1)[1], self.CASE_1588.split(" then ", 1)[1]):
            with self.subTest(text=text):
                out = self.read(text)
                tabs = {c["account_id"]: [f["action"]["account_id"] for f in c["flags"] if isinstance(f.get("action"), dict)
                                          and f["action"].get("kind") == "strategies_tab"] for c in out["combos"]}
                self.assertEqual({"P1:US_EXCHANGE": ["P1:US_EXCHANGE"], "F1:US_EXCHANGE": ["F1:US_EXCHANGE"]}, tabs)


class SourceTests(unittest.TestCase):
    def test_the_rest_covers_the_button_clicked_and_a_press_is_decided_at_its_mousedown(self):
        src = _read(SMART)
        for s in ("var REST_MS = 2000, REST_PX = 4, REST_ANY_MS = 500;",
                  "var handGesture = false, hand = null, handRest = null, pressDrop = false, pressSay = null;",
                  "return e.clientX >= b.left - REST_PX && e.clientX <= b.right + REST_PX && e.clientY >= b.top - REST_PX && e.clientY <= b.bottom + REST_PX;",
                  "if (e.type === 'mousemove') { if (handRest && !inRestBox(e)) { handRest.out = true; } return; }",
                  "var inBox = on && !handRest.out && inRestBox(e), soon = on && now - handRest.at < REST_ANY_MS;",
                  "if (on && !inRestBox(e)) { handRest.out = true; }",
                  "pressDrop = inBox || soon || (handGesture && e.detail > 1);",
                  "var drop = e.type === 'mousedown' || e.type === 'mouseup' ? pressDrop : pressDrop && e.detail > 0;",
                  "hand = { ev: e, label: label, box: { left: q.left, top: q.top, right: q.right, bottom: q.bottom } };"):
            self.assertEqual(1, src.count(s), s)
        for gone in ("nearRest", "restPress", "var rest = "):
            self.assertNotIn(gone, src, gone)
        # one hand-off for every button (a card's Open in ticket, a group's Strategies tab, a flag's own ticket): the rest is the button's,
        # set before the page moves
        at = src.index("function handTicket(S, acct, fields) {")
        body = src[at:src.index("\n  }\n", at)]
        self.assertLess(body.index("handRest = ev && ev.type === 'click' && ev.detail > 0 && bx ? { box: {"), body.index("T.router.go(T.router.account(acct, 'markets'));"))
        self.assertEqual(1, src.count("T.router.go("))
        for fn in ("openCard", "openStrategies", "openOwnTicket"):
            at = src.index("function %s(S, " % fn)
            self.assertIn("handTicket(S, ", src[at:src.index("\n  }\n", at)], fn)

    def test_each_copy_of_a_group_is_named_for_its_own_orders(self):
        src = _read(os.path.join(TERMINAL, "smart_ticket.py"))
        for s in ('own = [ci for ci in g["clauses"] if any(inst["rep"] == r and inst["legs"] for inst in insts[ci])]',
                  "one = clauses[own[0]] if len(own) == 1 else None",
                  'name = one["strategy"]["name"] if one is not None and one["kind"] == "strategy" and one.get("strategy") else None',
                  '"strategy": name, "prices": gprices})'):
            self.assertEqual(1, src.count(s), s)
        self.assertNotIn('"strategy": names[0] if len(names) == 1 and len(g["clauses"]) == 1 else None', src)


if __name__ == "__main__":
    unittest.main()
