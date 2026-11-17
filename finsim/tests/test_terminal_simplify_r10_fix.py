"""FinSim 1.0.7.2 simplify review round 10 (on 4d157f8, "one guard for every send"): the regression tests of its findings, kept in the
terminal suite.

R10-P1 (page lens, major).  A click on a card's "Open in ticket", then a SECOND CLICK OF ITS OWN on the same point after the system's
double-click time (two clicks of click count 1, 0.55 s apart - a slower or impatient double click): the hand-off moves to the account's
Markets page, whose docked order ticket - the order filled in whole and previewed at once - arms its Submit right under the pointer about
0.35 s after the first click, and the second click SENT the order (round 8 only dropped a click count above 1; T.guard's double-click
window is per control, and Submit had never been pressed).  Now the rest of a hand-off by a click is dropped by the Smart Ticket page
(pages/smartticket.js dropGestureRest): on the account page it opened, a press within 4 px of where that click was - whatever its click
count - until the pointer has moved away or 2 s have passed, with its release, click and double click, and said ("not taken: ...").
  test_a  1280 x 800 (tests/test_reviews10_page_slow_second_click.py test_a): card #3 of "buy 10 ACME limit 50; buy 10 WIDG limit 249; buy 12
          WIDG stop 251 limit 252 gtd Dec 18 2026", its Open in ticket clicked, then clicked again on the same point (click count 1) at
          least 0.55 s later, once the ticket's Submit for that order is armed: nothing is sent, said;
  test_b  1366 x 768, the part of the button over the docked Submit (test_b): the same;
  test_c  the control (test_c): one click on Open in ticket, Submit clicked 2.5 s after it is armed: that one order is placed;
  test_d  the control of the rule's end: the pointer moved away after the hand-off - a click on Submit then is a gesture of its own: placed
          (simplify round 11 fixes: moved off the BUTTON clicked, and the click once the double-click time is over -
          tests/test_terminal_simplify_r11_fix.py).
R10-T1 (property tester, major).  A key held on a focused button reaches the page as clicks of their own (Edge / Chrome: each repeat of
Enter is a keydown with repeat: true, then a click with detail 0 and no repeat), so the question a press opened (its focus on its button)
was answered yes by the key's first repeat - Pending's Cancel cancelled, Treasury's Add funds deposited - and a direct send whose answer
came back inside the repeat delay acted twice.  Now (core.js, after T.guard) a repeat of Enter has no default action (no button pressed,
nothing submitted; a textarea keeps its lines) and Enter acts only where it went down (a keypress whose keydown moved the focus presses
nothing); one fresh press of Enter on a question's button still answers it.
  test_e  Pending: BUY 10 ACME LIMIT 50.00 resting in the Fund book, Enter held 0.7 s on its Cancel (real CDP keys, Windows' default repeat
          delay 0.5 s and rate 33 ms; tests/test_reviews10_property_tester_held_enter.py test_a): the question waits, the order WORKING;
  test_f  the control (a2): Enter pressed once asks and waits; a fresh Enter on "Cancel order" cancels it, once;
  test_g  Treasury: Add funds, 1,234.00 in its amount box, Enter held there 0.7 s (test_b): the question waits, nothing deposited;
  test_h  the control (b2): Enter pressed once asks and waits; a fresh Enter on "Add USD 1,234.00" deposits once;
  test_seed_10100401_cases  tests/test_props10_held_keys.py at its seed 10100401: the first findings of each flow (ask 7, 30, 34, 62; box
          11, 18, 25, 27; direct 15) replayed on the real core.js with its harness (virtual time; the browser's handling of a key held on a
          focused button as Edge / Chrome do it): K1 no send from a repeat, K2 one gesture at most one send, K3 a question answered only
          by an activation that began after it was shown.
R10-T2 (property tester, minor).  The hand-off's guard keyed a Smart Ticket button by its place (card #1's "Open the bond ticket"), so
card #1's button of a NEW reading pressed inside the double-click time of card #1's button of the reading before it was refused
(tests/test_props6_no_send.py seed 6660201: 70 HN findings; tests/test_reviews6_property_tester_own_desks.py a and b).  Each reading's
buttons are their own controls now (the key names the reading, S.pseq: each reading of the page load has its own number, the Smart
Ticket shown again included):
  test_seed_6660201_a_new_readings_button_is_its_own_control  "buy 1k ACME-31" opened in the bond ticket, then - the Smart Ticket shown
          again - "sell 2,000 ACME-31" read and opened with the page's clock held still (inside any double-click time): it opens, SELL 2,000;
  test_i  the control: the same reading's button pressed twice at once hands over once; the second press is refused, said.
  SourceTests  where these rules live.
The world is round 9's (tests/test_terminal_simplify_r9.py ``_seed9``: an offline server on port 0 in a temp home, the Personal and the
Fund books, the fictional ACME / WIDG, the bond ACME-31 and the ACME chain; a headless Edge / Chrome over CDP).  Nothing touches ~/.finsim,
the network or the ports 8765 / 8865; only test_c, test_d, test_f and test_h place or book anything, in the temp home.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import unittest
from urllib.parse import urlsplit

from tests import test_terminal_browser_smoke as SMK
from tests import test_terminal_simplify_r6 as R6
from tests import test_terminal_simplify_r8 as R8
from tests import test_terminal_simplify_r9 as R9
from tests.terminal_cdp import NO_BROWSER, Browser, find_browser
from finsim.terminal import config
from finsim.terminal.clock import FakeClock
from finsim.terminal.quotes.providers import FakeProvider

TERMINAL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "finsim", "terminal")
STATIC = os.path.join(TERMINAL, "static")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


MIN_GAP_S = 0.55                          # after Windows' default double-click time (500 ms): the second click is a click of its own
ARM_BY_S = 1.6                            # the second click comes by then, inside the rest's 2 s, Submit armed or not
REST_SAID = re.compile(r'^not taken: that click came where "Open in ticket" was just pressed, as its ticket came up under the pointer - nothing was'
                       r" sent or changed; move to the button you want and click it$")
#: the docked ticket shows a NEW order (not ``gen0``) on ``inst``, its preview back with no blocker, and whether its Submit is armed under (x, y)
ARMED_UNDER = """(function (x, y, gen0, inst) {
  var s = T.components.ticket && T.components.ticket.state ? T.components.ticket.state() : null; var p = s && s.preview;
  var ok = !!(location.hash.indexOf('#/a/') === 0 && s && s.gen !== gen0 && s.instrument_id === inst && p && !s.previewing && !s.dirty && p.ok !== false
              && !(p.blockers && p.blockers.length));
  var b = document.getElementById('ticket-submit-btn'), e = document.elementFromPoint(x, y);
  return { previewed: ok, armed: !!(b && !b.disabled), under: !!(b && e && (e === b || b.contains(e))) };
})(%f, %f, %s, %s)"""
REFUSED = "(T.guard.refused || []).slice()"
ENTER = {"key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13, "text": "\r", "unmodifiedText": "\r"}
DELAY_S = 0.5                             # Windows' default keyboard repeat delay
EVERY_S = 0.033                           # Windows' default repeat rate (about 30 a second)
OWN_BOND = ("(function () { var c = document.querySelector('#smart-cards [data-i=\"0\"]'); if (!c) { return null; }"
            " return Array.prototype.filter.call(c.querySelectorAll('[data-act=\"action\"]'), function (x) { return /^Open the /.test(x.textContent || ''); })[0] || null; })()")
BOND_STATE = ("(function () { var s = T.components.bondticket && T.components.bondticket.state ? T.components.bondticket.state() : null;"
              " return s && s.ui && s.ui.face ? [s.side, s.ui.face.value, s.instrument_id] : null; })()")

# ---- tests/test_props10_held_keys.py's harness (the real core.js; four controls wired as the page wires its sends; virtual time) ----------
HARNESS = r"""(function () {
  if (window.__p10) { return true; }
  var box = document.createElement('div');
  box.id = 'p10-box'; box.style.cssText = 'position:fixed;left:8px;bottom:8px;z-index:5;background:#fff';
  document.body.appendChild(box);
  var P = window.__p10 = { vt: 0, pending: [], sends: [], cause: null, opened: [] };
  var realNow = Date.now;
  P.on = function () { P.vt = realNow(); Date.now = function () { return P.vt; }; };
  P.off = function () { Date.now = realNow; };
  function send(tag) {
    P.sends.push({ tag: tag, cause: P.cause, at: P.vt });
    return new Promise(function (res) { P.pending.push({ due: P.vt + P.latency, res: res }); });
  }
  function ask(tag, focus) {
    return T.ui.confirm('p10 ' + tag + '?', focus ? { focus: 'cancel', ok: 'Go' } : { ok: 'Go' }).then(function (yes) { return yes ? send(tag) : null; });
  }
  P.make = function (flow, key) {
    while (box.firstChild) { box.removeChild(box.firstChild); }
    var el;
    if (flow === 'box') {
      el = document.createElement('input'); el.type = 'text'; el.value = '1234.00';
      el.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); T.guard(key, e, function () { P.opened.push(P.vt); return ask(flow, false); }, { label: 'Add' }); } });
    } else {
      el = document.createElement('button'); el.className = 'btn'; el.textContent = flow;
      if (flow === 'direct') { el.onclick = T.guard.on(key, { label: 'Book' }, function () { return send(flow); }); }
      else { el.onclick = function (ev) { T.guard(key, ev, function () { P.opened.push(P.vt); return ask(flow, flow === 'ask-cancel'); }, { label: 'Cancel it' }); }; }
    }
    box.appendChild(el); el.focus();
    return document.activeElement === el;
  };
  async function flush() { for (var i = 0; i < 25; i++) { await null; } }
  async function until(t) {
    for (;;) {
      P.pending.sort(function (a, b) { return a.due - b.due; });
      if (!P.pending.length || P.pending[0].due > t) { break; }
      var p = P.pending.shift(); P.vt = Math.max(P.vt, p.due); P.cause = { kind: 'answer', t: P.vt }; p.res({ ok: true }); await flush();
    }
    P.vt = Math.max(P.vt, t);
  }
  function key(type, repeat, kind) {
    var el = document.activeElement || document.body;
    P.cause = { kind: kind, t: P.vt, tag: el.tagName + ':' + (el.textContent || el.value || '').slice(0, 12) };
    var ev = new KeyboardEvent(type, { key: 'Enter', code: 'Enter', bubbles: true, cancelable: true, repeat: !!repeat });
    var go = el.dispatchEvent(ev);
    if (type === 'keydown' && go && el.tagName === 'BUTTON' && !el.disabled) { el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, detail: 0 })); }
  }
  P.run = async function (cs) {
    var out = [];
    P.on();
    try {
      for (var c = 0; c < cs.length; c++) {
        var g = cs[c];
        while (T.ui.dialogs()) { T.ui.closeTop(); }
        await flush();
        P.pending = []; P.sends = []; P.opened = []; P.latency = g.latency;
        P.vt += 20000;
        var t0 = P.vt;
        var focused = P.make(g.flow, 'p10fix:' + g.n + ':' + g.flow);
        for (var i = 0; i < g.events.length; i++) {
          var e = g.events[i];
          await until(t0 + e[0]);
          if (e[1] === 'down') { key('keydown', false, 'press'); }
          else if (e[1] === 'rep') { key('keydown', true, 'repeat'); }
          else if (e[1] === 'up') { key('keyup', false, 'release'); }
          await flush();
        }
        await until(t0 + g.end);
        out.push({ n: g.n, focused: focused, sends: P.sends.slice(), opened: P.opened.slice().map(function (x) { return x - t0; }), dialogs: T.ui.dialogs() });
        out[out.length - 1].sends.forEach(function (s) { s.at -= t0; if (s.cause) { s.cause.t -= t0; } });
      }
    } finally {
      P.off();
      while (T.ui.dialogs()) { T.ui.closeTop(); }
      var b = document.getElementById('p10-box'); if (b) { b.parentNode.removeChild(b); }
      window.__p10 = null;
    }
    return out;
  };
  return true;
})()"""
#: seed 10100401's first findings per flow on 4d157f8: (case, flow, answer latency ms, repeat delay ms, repeat every ms, held ms)
SEED_10100401 = [(7, "ask", 500, 750, 33, 800), (30, "ask", 500, 500, 33, 2500), (34, "ask", 20, 250, 33, 480), (62, "ask", 500, 500, 50, 1600),
                 (11, "box", 20, 750, 100, 1100), (18, "box", 800, 500, 400, 800), (25, "box", 500, 500, 33, 800), (27, "box", 300, 250, 33, 2500),
                 (15, "direct", 150, 1000, 50, 2500)]


def _held(n: int, flow: str, latency: int, delay: int, every: int, hold: int) -> dict:
    """The gesture as the property draws it: Enter pressed at 0, its repeats from ``delay`` every ``every`` ms until ``hold``, released."""
    ev, t = [[0, "down"]], delay
    while t < hold:
        ev.append([t, "rep"])
        t += every
    ev.append([hold, "up"])
    return {"n": n, "flow": flow, "latency": latency, "events": ev, "end": hold + 1200}


def _judge(g: dict, r: dict) -> list:
    """K1 / K2 / K3 of tests/test_props10_held_keys.py for one gesture's result."""
    bad, sends = [], r["sends"]
    for s in sends:
        c = s.get("cause") or {}
        if c.get("kind") == "repeat":
            bad.append(("K1", f"a send at {s['at']} ms caused by a repeat of the key held (at {c.get('t')} ms, on {c.get('tag')})"))
    if len(sends) > 1:
        bad.append(("K2", f"{len(sends)} sends for one gesture, at {[s['at'] for s in sends]} ms"))
    if g["flow"] != "direct" and sends:
        first = r["opened"][0] if r["opened"] else None
        for s in sends:
            c = s.get("cause") or {}
            if first is None or c.get("t", -1) <= first or c.get("kind") in ("repeat", "click2", "click3"):
                bad.append(("K3", f"the question shown at {first} ms answered yes by the gesture that opened it ({c.get('kind')} at {c.get('t')} ms)"))
                break
    return bad


@unittest.skipUnless(find_browser(), NO_BROWSER)
class PageTests(unittest.TestCase):
    """One offline server (port 0, a temp home, round 9's world) with the smoke test's invented ACME chain cached, and one headless browser
    for the class, both closed in tearDownClass."""

    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp(prefix="fst-simplify-r10-fix-")
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
            SMK.BrowserSmokeTests._served_chain(cls)                     # the chain as the quote worker caches a fetched one
            cls.browser = Browser(find_browser(), 1440, 900)
            cls.sid = cls.browser.page("about:blank")
            cls.browser.navigate(cls.sid, cls.server.url)
            cls.browser.wait(cls.sid, R9.LOADED, timeout=45)
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

    # the page as the owner uses it (test_terminal_simplify_r6's, _r8's and _r9's helpers)
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
    own_action = R9.PageTests.own_action

    def tidy(self) -> None:
        def undo():
            self.js("(function () { while (T.ui.dialogs()) { T.ui.closeTop(); } localStorage.removeItem(%s); sessionStorage.removeItem(%s);"
                    " location.hash = '#/dashboard'; return true; })()" % (json.dumps(R6.STORE_KEY), json.dumps(R6.WIN_KEY)))
            self.browser.pump(0.6)
            self.browser.viewport(self.sid, 1440, 900)
        self.addCleanup(undo)

    def rows(self, sql: str, args=()) -> list:
        with self.server.rt.db.reader() as conn:
            return [tuple(r) for r in conn.execute(sql, args).fetchall()]

    def orders(self) -> list:
        return self.rows("SELECT order_id, instrument_id, side, qty, order_type, status FROM orders ORDER BY rowid")

    def requests(self, since: int, method: str, path: str) -> list:
        out = []
        for e in self.browser._events(self.sid, since):
            if e.get("method") == "Network.requestWillBeSent":
                r = (e.get("params") or {}).get("request") or {}
                if r.get("method") == method and urlsplit(r.get("url") or "").path == path:
                    out.append(r.get("postData"))
        return out

    def wait_rows(self, rows, before: list, n: int = 1, timeout: float = 15.0) -> list:
        deadline = time.time() + timeout
        while time.time() < deadline and len(rows()) - len(before) < n:
            self.browser.pump(0.2)
        self.browser.pump(1.5)                                          # what a second activation could have started
        return rows()[len(before):]

    def refused_since(self, n: int) -> list:
        return (self.js(REFUSED) or [])[n:]

    # ---- R10-P1: a second click of its own on the point that handed an order over ---------------------------------------------------
    def second_click_of_its_own(self, text: str, cards: int, idx: int, inst: str, point=None):
        """``text`` read; a click on card #idx+1's Open in ticket (at ``point(rect)`` or its middle), then a second click of its own (click
        count 1) on the same point at least MIN_GAP_S later, once the docked ticket's Submit for that order is armed (by ARM_BY_S at the
        latest).  Returns ((x, y), the gap, whether Submit was armed under the point, what was under it)."""
        self.fresh_smart("P")
        self.read(text, cards)
        r = self.open_rect(idx)
        x, y = point(r) if point else R8._middle(r)
        self.assertTrue(r["left"] < x < r["right"] and r["top"] < y < r["bottom"], f"the point {(x, y)} is not on the button {r}")
        gen0 = self.js(R8.GEN)
        self.click_xy(x, y)
        t0, armed_under = time.monotonic(), False
        while time.monotonic() - t0 < ARM_BY_S:
            s = self.js(ARMED_UNDER % (x, y, json.dumps(gen0), json.dumps(inst))) or {}
            if s.get("previewed") and s.get("armed"):
                armed_under = bool(s.get("under"))
                if time.monotonic() - t0 >= MIN_GAP_S:
                    break
            time.sleep(0.01)
        under = self.js(R8.UNDER % (x, y))
        gap = time.monotonic() - t0
        self.mouse("mousePressed", x, y, 1)
        self.mouse("mouseReleased", x, y, 1)
        self.browser.pump(2.0)                                          # what the second click could start has its answer
        return (x, y), gap, armed_under, under

    def assert_rest_dropped(self, size, pt, gap, armed_under, under, start: int, before: list, n: int) -> None:
        detail = f"at {size}, a second click of its own on {tuple(round(v) for v in pt)} {gap:.2f} s after the hand-off, on {under} (Submit armed under it: {armed_under})"
        self.assertEqual([], self.order_posts(start), detail)
        self.assertEqual(before, self.orders(), detail)
        said = [x for x in self.refused_since(n) if x.get("key") == "smart.handoff.rest"]
        self.assertEqual(1, len(said), f"{detail}: said {said}")
        self.assertRegex(said[0]["msg"], REST_SAID)
        t = self.js(R8.TICKET)
        self.assertEqual(("BUY", "12", "WIDG", "STOP_LIMIT", "251", "252", True), (t["side"], t["qty"], t["inst"], t["type"], t["stop"], t["limit"], t["submit"]),
                         f"{detail}: the order handed over stays, Submit armed: {t}")
        self.assertEqual([], self.browser.errors(self.sid, start))

    def test_a_a_second_click_of_its_own_on_open_in_ticket_sends_nothing(self):
        self.tidy()
        size = (1280, 800)
        self.browser.viewport(self.sid, *size)
        before, start = self.orders(), self.browser.mark()
        self.hand_once(R8.TEXT_A, 3, 2, "WIDG")                         # one hand-off from script (no point): its preview warm, nothing sent
        self.assertEqual([], self.order_posts(start))
        n = len(self.js(REFUSED) or [])
        pt, gap, armed_under, under = self.second_click_of_its_own(R8.TEXT_A, 3, 2, "WIDG")
        self.assert_rest_dropped(size, pt, gap, armed_under, under, start, before, n)

    def test_b_a_second_click_of_its_own_on_the_part_over_submit_sends_nothing(self):
        self.tidy()
        size = (1366, 768)
        self.browser.viewport(self.sid, *size)
        before, start = self.orders(), self.browser.mark()
        sb = self.hand_once(R8.TEXT_B, 3, 2, "WIDG")                    # where the docked Submit is for card #3's order (nothing sent)
        self.assertEqual([], self.order_posts(start))
        n = len(self.js(REFUSED) or [])

        def over_submit(r):
            ov = R8._overlap(r, sb)
            return R8._middle(ov) if ov else R8._middle(r)
        pt, gap, armed_under, under = self.second_click_of_its_own(R8.TEXT_B, 3, 2, "WIDG", point=over_submit)
        self.assert_rest_dropped(size, pt, gap, armed_under, under, start, before, n)

    def test_c_control_submit_clicked_once_its_preview_seen_places_one_order(self):
        self.tidy()
        self.browser.viewport(self.sid, 1280, 800)
        self.hand_once(R8.TEXT_A, 3, 2, "WIDG")
        before, start = self.orders(), self.browser.mark()
        self.fresh_smart("P")
        self.read(R8.TEXT_A, 3)
        x, y = R8._middle(self.open_rect(2))
        gen0 = self.js(R8.GEN)
        self.click_xy(x, y)
        self.wait(R8.PREVIEWED % (json.dumps(gen0), json.dumps("WIDG")), timeout=20)
        self.wait(R8.ARMED, timeout=20)
        self.browser.pump(2.5)                                          # the owner reads the ticket and its preview
        self.assertEqual([], self.order_posts(start), "nothing is sent before Submit")
        self.click_xy(*R8._middle(self.js(R8.RECT % json.dumps("#ticket-submit-btn"))))
        placed = self.wait_rows(self.orders, before)
        self.assertEqual([("WIDG", "BUY", 12, "STOP_LIMIT")], [(o[1], o[2], int(o[3]), o[4]) for o in placed], self.js(R8.TICKET))
        self.assertEqual(1, len(self.order_posts(start)))
        self.assertEqual([], self.browser.errors(self.sid, start))

    def test_d_control_the_pointer_moved_away_a_click_on_submit_is_its_own(self):
        """The rest ends when the pointer leaves the button clicked (simplify round 11 fixes: its box and 4 px around it; before, 4 px
        around the point): the hand-off clicked, Submit armed, the pointer moved 30 px below the button, then - once the double-click time
        (500 ms) is over, inside the rest's 2 s - Submit clicked: placed once, nothing refused."""
        self.tidy()
        self.browser.viewport(self.sid, 1280, 800)
        self.hand_once(R8.TEXT_C, 1, 0, "ACME")
        before, start = self.orders(), self.browser.mark()
        n = len(self.js(REFUSED) or [])
        self.fresh_smart("P")
        self.read(R8.TEXT_C, 1)
        r = self.open_rect(0)
        x, y = R8._middle(r)
        gen0 = self.js(R8.GEN)
        t0 = time.monotonic()
        self.click_xy(x, y)
        self.wait(R8.PREVIEWED % (json.dumps(gen0), json.dumps("ACME")), timeout=20)
        self.wait(R8.ARMED, timeout=20)
        self.mouse("mouseMoved", x, r["bottom"] + 30)                  # off the button: out of its box and the 4 px around it
        time.sleep(max(0.0, 0.6 - (time.monotonic() - t0)))           # the double-click time over
        self.click_xy(*R8._middle(self.js(R8.RECT % json.dumps("#ticket-submit-btn"))))
        clicked = time.monotonic() - t0
        placed = self.wait_rows(self.orders, before)
        self.assertEqual([("ACME", "BUY", 10, "LIMIT")], [(o[1], o[2], int(o[3]), o[4]) for o in placed], f"Submit clicked {clicked:.2f} s after the hand-off")
        self.assertEqual([], [f for f in self.refused_since(n) if f.get("key") == "smart.handoff.rest"])
        self.assertEqual([], self.browser.errors(self.sid, start))

    # ---- R10-T1: a key held on a focused button ---------------------------------------------------------------------------------------
    def key(self, typ: str, repeat: bool = False) -> None:
        p = dict(ENTER, type=typ)
        if typ == "keyUp":
            p.pop("text"), p.pop("unmodifiedText")
        if repeat:
            p["autoRepeat"] = True
        self.browser.call("Input.dispatchKeyEvent", p, session=self.sid)

    def hold_enter(self, hold_s: float) -> int:
        """Enter pressed where the focus is and held ``hold_s`` s as Windows repeats it by default; returns the repeats sent."""
        t0 = time.time()
        self.key("keyDown")
        n = 0
        time.sleep(DELAY_S)
        while time.time() - t0 < hold_s:
            self.key("keyDown", repeat=True)
            n += 1
            time.sleep(EVERY_S)
        self.key("keyUp")
        return n

    def press_enter(self) -> None:
        self.key("keyDown")
        self.key("keyUp")

    def close_dialogs(self) -> None:
        self.js("(function () { while (T.ui.dialogs()) { T.ui.closeTop(); } return true; })()")
        self.browser.pump(0.3)

    def resting(self, n: int) -> str:
        body = {"account_id": "F1:US_EXCHANGE", "instrument_id": "ACME", "side": "BUY", "qty": 10, "order_type": "LIMIT", "limit_px": "50.00", "tif": "DAY",
                "request_id": f"ui:r10fix-held-{n:04d}"}
        st, out, _h = self.server.rt.router.handle("POST", "/api/orders?book=F", None, body, "127.0.0.1", None)
        self.assertEqual((200, "WORKING"), (st, out["order"]["status"]), out)
        return out["order"]["order_id"]

    def focus_cancel(self, oid: str) -> None:
        self.js("(function () { T.book.set('F'); location.hash = '#/pending?status=open&r=' + Date.now(); return true; })()")
        btn = ("Array.prototype.map.call(document.querySelectorAll('#page tr[data-i]'), function (tr) { return tr.innerText.indexOf(%s) >= 0 ? "
               "tr.querySelector('button[data-act=\"x\"]') : null; }).filter(Boolean)[0]" % json.dumps(oid))
        self.wait("!!(%s)" % btn, timeout=45)
        self.browser.pump(1.0)                                          # past the page's first paint and the guard's window of any earlier press
        self.assertTrue(self.js("(function () { var b = %s; b.focus(); return document.activeElement === b; })()" % btn))

    def status(self, oid: str) -> str:
        return self.rows("SELECT status FROM orders WHERE order_id=?", (oid,))[0][0]

    def test_e_a_held_enter_never_answers_the_cancel_question(self):
        self.tidy()
        oid = self.resting(1)
        self.focus_cancel(oid)
        since = self.browser.mark()
        repeats = self.hold_enter(0.7)
        self.browser.pump(2.0)
        dels, dialogs, st = self.requests(since, "DELETE", "/api/orders/" + oid), self.js("T.ui.dialogs()"), self.status(oid)
        self.close_dialogs()
        self.assertEqual(("WORKING", 0, 1), (st, len(dels), dialogs),
                         f"Enter held 0.7 s on {oid}'s Cancel ({repeats} repeats, the first 0.5 s after the press): the order is {st}, {len(dels)} DELETE,"
                         f" {dialogs} dialog(s) open")
        self.assertEqual("WORKING", self.status(oid))

    def test_f_control_one_press_asks_and_a_fresh_press_answers_once(self):
        self.tidy()
        oid = self.resting(2)
        self.focus_cancel(oid)
        since = self.browser.mark()
        self.press_enter()
        self.browser.pump(1.5)
        self.assertEqual(1, self.js("T.ui.dialogs()"))
        self.assertEqual("Cancel order", self.js("(document.activeElement && document.activeElement.textContent) || ''"))
        self.assertEqual(("WORKING", 0), (self.status(oid), len(self.requests(since, "DELETE", "/api/orders/" + oid))))
        self.press_enter()                                              # a fresh press on the question's button: yes, once
        deadline = time.time() + 15
        while time.time() < deadline and self.status(oid) != "CANCELLED":
            self.browser.pump(0.2)
        self.browser.pump(1.0)
        self.assertEqual(("CANCELLED", 1, 0), (self.status(oid), len(self.requests(since, "DELETE", "/api/orders/" + oid)), self.js("T.ui.dialogs()")))
        self.assertEqual([], self.browser.errors(self.sid, since))

    def deposits(self) -> list:
        return self.rows("SELECT transfer_id FROM transfers WHERE kind='DEPOSIT' ORDER BY rowid") if self.rows(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='transfers'") else []

    def open_add_funds(self) -> None:
        self.close_dialogs()
        self.js("(function () { T.book.set('P'); location.hash = '#/dashboard'; return true; })()")
        self.browser.pump(0.8)
        self.js("(function () { location.hash = '#/treasury'; return true; })()")
        add = "Array.prototype.filter.call(document.querySelectorAll('#page button.btn.primary'), function (b) { return b.textContent === 'Add funds'; })[0]"
        self.wait("!!(%s)" % add, timeout=45)
        self.js("(function () { %s.click(); return true; })()" % add)
        self.wait("!!document.getElementById('funds-amount')", timeout=15)
        self.js("(function () { var a = document.getElementById('funds-amount'); a.value = '1234.00'; a.dispatchEvent(new Event('input', { bubbles: true }));"
                " a.focus(); return document.activeElement === a; })()")
        self.browser.pump(0.7)

    def test_g_a_held_enter_never_answers_the_deposit_question(self):
        self.tidy()
        self.open_add_funds()
        before = self.deposits()
        since = self.browser.mark()
        repeats = self.hold_enter(0.7)
        self.browser.pump(2.0)
        posts, dialogs, made = self.requests(since, "POST", "/api/treasury/deposit"), self.js("T.ui.dialogs()"), self.deposits()[len(before):]
        self.close_dialogs()
        self.assertEqual((0, 0, 2), (len(posts), len(made), dialogs),
                         f"Enter held 0.7 s in Add funds' amount box ({repeats} repeats): {len(posts)} deposit POST(s), {len(made)} deposit(s), {dialogs} dialog(s)")

    def test_h_control_one_press_asks_and_a_fresh_press_deposits_once(self):
        self.tidy()
        self.open_add_funds()
        before = self.deposits()
        since = self.browser.mark()
        self.press_enter()
        self.browser.pump(1.5)
        self.assertEqual((2, 0), (self.js("T.ui.dialogs()"), len(self.requests(since, "POST", "/api/treasury/deposit"))))
        self.assertEqual("Add USD 1,234.00", self.js("(document.activeElement && document.activeElement.textContent) || ''"))
        self.press_enter()                                              # a fresh press on the question's button: yes, once
        made = self.wait_rows(self.deposits, before)
        self.assertEqual((1, 1, 0), (len(made), len(self.requests(since, "POST", "/api/treasury/deposit")), self.js("T.ui.dialogs()")))
        self.assertEqual([], self.browser.errors(self.sid, since))

    def test_seed_10100401_cases(self):
        """tests/test_props10_held_keys.py, seed 10100401: the first findings of each flow on 4d157f8, replayed on the real core.js."""
        self.tidy()
        self.js("location.hash = '#/dashboard'; true")
        self.wait("T.router.current && T.router.current.page === 'dashboard'")
        self.assertTrue(self.js(HARNESS))
        cases = [_held(*c) for c in SEED_10100401]
        results = self.js("window.__p10.run(%s)" % json.dumps(cases), timeout=120)
        self.assertEqual(len(cases), len(results))
        found = []
        for g, r in zip(cases, results):
            self.assertTrue(r["focused"], f"case {g['n']}: the control could not take the focus")
            found += [f"case {g['n']} {g['flow']} {code}: {msg}" for code, msg in _judge(g, r)]
            if g["flow"] != "direct":
                self.assertEqual(([0], []), (r["opened"], r["sends"]), f"case {g['n']} {g['flow']}: the question asked once and never answered by the key held")
            else:
                self.assertEqual([0], [s["at"] for s in r["sends"]], f"case {g['n']}: one send, at the press")
        self.assertEqual([], found)

    # ---- R10-T2: each reading's hand-off buttons are their own ------------------------------------------------------------------------
    def bond_button(self, text: str) -> None:
        self.fresh_smart("P")
        self.read(text, 1)
        a = self.own_action(0)
        self.assertTrue(a and a["enabled"] and a["label"] == "Open the bond ticket", f"{text!r}: {a}")

    def test_seed_6660201_a_new_readings_button_is_its_own_control(self):
        """tests/test_props6_no_send.py seed 6660201 (rule HN; tests/test_reviews6_property_tester_own_desks.py a and b): "buy 1k ACME-31"
        opened in the bond ticket, then "sell 2,000 ACME-31" read and its card #1's "Open the bond ticket" pressed with the page's clock
        held still since the first press (so inside any double-click time): it opens the bond ticket, SELL 2,000 ACME-31."""
        self.tidy()
        self.addCleanup(lambda: self.js("(function () { if (window.__r10now) { Date.now = window.__r10now; window.__r10now = null; } return true; })()"))
        n = len(self.js(REFUSED) or [])
        self.bond_button("buy 1k " + R9.BOND)
        self.js("(function () { window.__r10now = Date.now; var t = Date.now(); Date.now = function () { return t; }; return true; })()")
        self.assertTrue(self.js("(function () { var b = %s; b.click(); return true; })()" % OWN_BOND))
        self.wait("location.hash.indexOf('#/a/') === 0 && !!document.querySelector(%s) && T.dock.visible()" % json.dumps(R9.BOND_ROOT), timeout=20)
        self.wait("(function () { var s = %s; return !!(s && s[0] === 'BUY' && s[1] === '1000' && s[2] === %s); })()" % (BOND_STATE, json.dumps(R9.BOND)), timeout=20)
        self.bond_button("sell 2,000 " + R9.BOND)
        self.assertTrue(self.js("(function () { var b = %s; b.click(); return true; })()" % OWN_BOND))
        self.wait("location.hash.indexOf('#/a/') === 0 && !!document.querySelector(%s) && T.dock.visible()" % json.dumps(R9.BOND_ROOT), timeout=20)
        self.wait("(function () { var s = %s; return !!(s && s[0] === 'SELL' && s[1] === '2000' && s[2] === %s); })()" % (BOND_STATE, json.dumps(R9.BOND)), timeout=20)
        self.assertEqual([], [x for x in self.refused_since(n) if str(x.get("key", "")).startswith("smart.handoff")])

    def test_i_control_the_same_readings_button_pressed_twice_at_once_hands_over_once(self):
        self.tidy()
        n = len(self.js(REFUSED) or [])
        self.bond_button("buy 1k " + R9.BOND)
        self.assertTrue(self.js("(function () { var b = %s; b.click(); b.click(); return true; })()" % OWN_BOND))
        self.wait("location.hash.indexOf('#/a/') === 0 && !!document.querySelector(%s) && T.dock.visible()" % json.dumps(R9.BOND_ROOT), timeout=20)
        refused = [x for x in self.refused_since(n) if str(x.get("key", "")).startswith("smart.handoff:")]
        self.assertEqual(["not handed over: Open the bond ticket was pressed again inside the double-click time (500 ms) - press it once to open it"],
                         [x["msg"] for x in refused], refused)
        self.assertRegex(refused[0]["key"], r"^smart\.handoff:\d+:action:c0:\d+$")


class SourceTests(unittest.TestCase):
    def test_a_key_held_down_presses_nothing_and_enter_acts_where_it_went_down(self):
        core = _read(os.path.join(STATIC, "core.js"))
        rule = core[core.index("  /* simplify round 10 fixes (a key held down presses nothing)"):core.index("/* ---------------------------------------------------------------- conventions")]
        for s in ("if (!e.repeat) { downOn = e.target; return; }",
                  "if (!lines(e.target)) { e.preventDefault(); }",
                  "window.addEventListener('keypress', function (e) { if (e.key === 'Enter' && downOn && e.target !== downOn) { e.preventDefault(); } }, true);",
                  "window.addEventListener('keyup', function (e) { if (e.key === 'Enter') { downOn = null; } }, true);"):
            self.assertEqual(1, rule.count(s), s)
        # registered when core.js loads, after the guard: before the keyboard's own listener (K.install, at boot) and every page's
        self.assertLess(core.index("T.guard = (function () {"), core.index("var downOn = null;"))
        self.assertLess(core.index("var downOn = null;"), core.index("window.addEventListener('keydown', onKeyCapture, true);"))

    def test_the_rest_of_a_hand_off_by_a_click_and_each_readings_buttons(self):
        src = _read(os.path.join(STATIC, "pages", "smartticket.js"))
        # simplify round 11 fixes: the rest covers the button clicked (its box) and any press inside the double-click time, and a press's
        # release follows its press (test_terminal_simplify_r11_fix pins those lines)
        for s in ("var REST_MS = 2000, REST_PX = 4, REST_ANY_MS = 500;",
                  "if (e.type === 'mousemove') { if (handRest && !inRestBox(e)) { handRest.out = true; } return; }",
                  "if (handRest && (now - handRest.at > REST_MS || (r && r.page !== 'smart' && r.account !== handRest.acct))) { handRest = null; }",
                  "var inBox = on && !handRest.out && inRestBox(e), soon = on && now - handRest.at < REST_ANY_MS;",
                  "handRest = ev && ev.type === 'click' && ev.detail > 0 && bx ? { box: { left: Math.min(bx.left, ev.clientX), top: Math.min(bx.top, ev.clientY),",
                  "var reads = 0;", "var seq = S.pseq = ++reads;", "S.pseq = ++reads; S.parsing = false;",
                  "return T.guard('smart.handoff:' + (st ? st.pseq : 0) + ':' + (b.dataset.act || '') + ':' + (b.dataset.w || '') + ':' + (b.dataset.f || ''), e, run,"):
            self.assertEqual(1, src.count(s), s)
        self.assertEqual((0, 0), (src.count("S.pseq++"), src.count("++S.pseq")), "a reading is numbered from the page load's count, never the page's own")
        at = src.index("function handTicket(S, acct, fields) {")
        body = src[at:src.index("\n  }\n", at)]
        self.assertLess(body.index("handRest = "), body.index("T.router.go(T.router.account(acct, 'markets'));"))


if __name__ == "__main__":
    unittest.main()
