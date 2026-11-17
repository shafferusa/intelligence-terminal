"""FinSim 1.0.7.2 simplify review round 12 (on 53b8e0d): the regression tests of its finding, kept in the terminal suite.

R12-P1 (page lens, minor; tests/test_reviews12_page_typed_edit_dropped.py, deterministic: no seed).  The Smart Ticket takes an in-place
edit from a card box's change event (onEdit: the box left by Tab, Enter or a click elsewhere).  Typing in a box that still had the focus
was dropped UNSAID when the page painted the cards again by itself - the look-up or the chain load the owner asked for answered and the
page read the text again (parse paints the cards read-only at once and anew with the answer) - or was left by a page key pressed while
typing (an F-key or a Ctrl binding acts in a box: unmount saved the draft without the box's text): the card showed the quantity as read,
no "edited" tag, no note on it or above the cards, and its Open in ticket handed that quantity to the ticket.  Worse, Chrome fires the
change event of such a box as the repaint removes it, and onEdit (no edit while reading) painted the cards again inside that repaint:
when the page held one card or group (a lone option card whose "Load the chain" the owner asked for), the outer repaint threw
NotFoundError and the page stayed "reading ..." with its Parse button disabled and nothing asked (until Enter read the text again).
Now (pages/smartticket.js noteTyping, and the repainting guard of onEdit) such typing is never taken by itself: it is said on its card -
"your typing: qty 5 - not kept: ..." - and the card's order stays as it was, so the card shows what it hands over; the note is kept in
the draft and through the next readings (above the cards when its card is gone) until the owner edits that field or opens the card; a
reload or a closed window (pagehide) says it in the draft too; the change a box fires as the page's own repaint removes it is no edit and
paints nothing.  A box left first (Tab, Enter) keeps its edit as before.
  PageTests, LookUpControlTests and OneCardTests (the real #/smart in a headless Edge / Chrome against an offline server on port 0 -
  each class a temp home with the Personal and the Fund books, the fictional ACME in the instrument master, its invented option chain
  known only to the FakeProvider (not loaded) and WIDG known only to the FakeProvider, so "Look it up" and "Load the chain" are answered
  through the served workers; the action's answer is held in the page until the typing is done, so no run is inconclusive):
    test_a  "buy 10 WIDG limit 249; buy 10 ACME limit 50": card #1's Look it up clicked, then 5 typed over card #2's quantity of 10 (End,
            Backspace, Backspace, 5; the box keeps the focus); the look-up answers and the page reads the text again: card #2 shows 10
            and says the 5 was not kept, no "edited"; its Open in ticket hands the ticket BUY 10 ACME LIMIT 50 - what the card shows;
    test_b  "buy 10 ACME limit 50": 5 typed over the 10, then F1 (US Exchange) and Ctrl+F9 (back): the restored card shows 10 and says
            the 5 was not kept, and so does the card read again (Enter); 5 typed and Tab then takes it (the note goes);
    test_c  control (a world of its own, WIDG unknown): test_a with Tab after the 5 before the look-up answers: 5 kept, "edited", no
            note, the ticket gets BUY 5 ACME LIMIT 50;
    test_d  control: test_b with Tab before F1: 5 kept, "edited", no note;
    test_i  the page's one card: "buy 1 ACME Dec 18 2026 60 call limit 3.20" (unverified: the chain is not loaded), its Load the chain
            clicked, then 5 typed over its quantity of 1; the chain loads and the page reads the text again - the reading completes
            (verified now, no exception, Parse enabled), the card shows 1 and says the 5 was not kept; its Open in ticket hands the
            ticket 1 of that call at a limit of 3.20 (a world of its own: an ACME hand-off loads the chain).
  StandInTests (the real smartticket.js with test_terminal_simplify_r1's stand-in for window.T; the parse answers computed by
  smart_ticket.parse_view on tests/terminal_smart_corpus.py's world; the text read again by a script click, which moves no focus, as
  the page's own reading after a look-up; no exception in any):
    test_e  one card: the reading completes and its card says the typing; the note stays when the card is read otherwise (read for the
            Fund book: its account "was"), until that field is edited;
    test_f  typing on a card the new reading has no match for (the text changed in the box) is said above the cards;
    test_g  pagehide (a reload, the window closed) writes the note into the draft, no edit; the page left with the box focused
            (unmount) and shown again: the restored card says it, read again it still does, opened in the ticket it goes;
    test_h  control: Enter in the box takes the typing (its change event): read again, 5 kept, "edited", no note.
  SourceTests  where these rules live.
Nothing touches ~/.finsim, the network or the ports 8765 / 8865; no test presses a Submit, and each asserts that no order request was
sent.  Fictional ACME / WIDG only.
Checked against 53b8e0d (2026-11-17, py-3.12; its pages/smartticket.js served from a scratch copy of the static folder and loaded into
the stand-in tab; scratch, deleted after): test_a, test_b, test_f and test_g fail (the 5 typed is gone and nothing says so), test_e and
test_i fail with the page stuck "reading ..." (NotFoundError, no second reading asked), and so do the two source tests; the controls
test_c, test_d and test_h pass; on the fix all 11 pass (py-3.12 and py-3.14).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import unittest

from tests import terminal_smart_corpus as corpus
from tests import test_terminal_browser_smoke as SMK
from tests import test_terminal_simplify_r1 as R1
from tests import test_terminal_simplify_r6 as R6
from tests import test_terminal_simplify_r8 as R8
from tests import test_terminal_simplify_r9 as R9
from tests import test_terminal_simplify_r10_fix as R10F
from tests.terminal_cdp import NO_BROWSER, Browser, find_browser
from tests.terminal_helpers import _Log
from finsim.terminal import config, server as _server
from finsim.terminal.clock import FakeClock, epoch_of
from finsim.terminal.quotes.providers import FakeProvider

SMART = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "finsim", "terminal", "static", "pages", "smartticket.js")
PERSONAL, FUND = config.BOOKS[0], config.BOOKS[1]
TEXT_LOOKUP = "buy 10 WIDG limit 249; buy 10 ACME limit 50"
TEXT_ONE = "buy 10 ACME limit 50"
TEXT_OPTION = "buy 1 ACME Dec 18 2026 60 call limit 3.20"
CALL_DEC60 = "ACME261218C00060000"
WIDG_META = {"symbol": "WIDG", "currency": "USD", "exchangeName": "NYQ", "fullExchangeName": "NYSE", "instrumentType": "EQUITY",
             "longName": "Widget Test Inc", "exchangeTimezoneName": "America/New_York"}
TAKES = " (Tab, Enter or a click elsewhere takes what you type)"
NOT_KEPT_READ = "your typing: qty 5 - not kept: the text was read again before you left the box" + TAKES
NOT_KEPT_LEFT = "your typing: qty 5 - not kept: the page was left before you left the box" + TAKES
#: a card as the owner sees it: its quantity box, its head, its tags and notes, the note above the cards and the status line
CARD = """(function (i) {
  var c = document.querySelector('#smart-cards [data-i="' + i + '"]'); if (!c) { return null; }
  var q = c.querySelector('[data-edit="qty"]'), lost = document.getElementById('smart-lost');
  return { qty: q ? q.value : null, head: (c.querySelector('.smart-card-head') || {}).innerText || '',
           edited: Array.prototype.some.call(c.querySelectorAll('.smart-tag'), function (t) { return /edited/.test(t.innerText); }),
           dropped: Array.prototype.map.call(c.querySelectorAll('.smart-dropped'), function (d) { return d.innerText; }),
           lost: lost && !lost.hidden ? lost.innerText : '', status: (document.getElementById('smart-status') || {}).innerText || '' };
})(%d)"""
QTY_BOX = "#smart-cards [data-i=\"%d\"] [data-edit=\"qty\"]"
#: the box being typed in: whether it still has the focus, what it holds, and whether a reading is in flight
TYPED = """(function (sel) {
  var q = document.querySelector(sel);
  return { focus: !!(q && document.activeElement === q), value: q ? q.value : null, parsing: document.querySelector('#smart-parse').disabled,
           status: (document.getElementById('smart-status') || {}).innerText || '' };
})(%s)"""
READ_AGAIN = ("!document.querySelector('#smart-parse').disabled && /^read /.test(document.querySelector('#smart-status').innerText)"
              " && document.querySelectorAll('#smart-cards [data-i]').length === %d")
#: the look-up's answer held in the page (T.api.getReady: the request goes out and the server resolves it; its answer reaches the page
#: once released), so the typing always comes before it
HOLD = r"""(function () {
  if (window.__r12hold) { return true; }
  var g0 = T.api.getReady, H = window.__r12hold = { open: false, waiting: [], g0: g0, calls: 0 };
  T.api.getReady = function () {
    var p = g0.apply(this, arguments); H.calls++;
    if (H.open) { return p; }
    return new Promise(function (ok, no) { H.waiting.push(function () { p.then(ok, no); }); });
  };
  H.release = function () { H.open = true; H.waiting.splice(0).forEach(function (f) { f(); }); };
  return true;
})()"""
RELEASE = "(function () { window.__r12hold.release(); return true; })()"
UNHOLD = "(function () { var H = window.__r12hold; if (H) { H.release(); T.api.getReady = H.g0; delete window.__r12hold; } return true; })()"


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _seed12(home: str, clock: FakeClock, fp: FakeProvider) -> None:
    """The Personal and the Fund books; ACME in the instrument master with its print, its invented option chain known to the
    FakeProvider only (an ACME option reads OPTION_UNVERIFIED until "Load the chain"); WIDG known to the FakeProvider only (its print and
    its Yahoo meta), so the parse reads it SYMBOL_UNKNOWN and the served worker resolves it on "Look it up"."""
    rt = _server.build_runtime(home, os.path.join(home, "terminal.db"), clock=clock, offline=True, fake_provider=fp, log=_Log(), books=[PERSONAL, FUND],
                               fetch_threads=0)
    try:
        aapl = rt.instruments.get("AAPL")
        name, _last = SMK.SMART_NAMES["ACME"]
        with rt.db.writer() as tx:
            rt.instruments.upsert(tx, instrument_id="ACME", kind="EQUITY", symbol="ACME", name=name, currency="USD", session_group=aapl.session_group,
                                  account_kinds=list(aapl.account_kinds), yahoo_symbol="ACME", cboe_symbol="ACME", spec={"exchange": "NYQ"})
        qt = epoch_of(clock.today(), "13:58")
        for sym, (_name, last) in SMK.SMART_NAMES.items():
            fp.set(sym, last=last, quote_time=qt, bar=(qt - qt % 60 - 60, 61900))
        rt.cache.subscribe(["ACME"], 86400, "PAGE")
        rt.worker.tick()
        fp.set_meta("WIDG", WIDG_META)
        fp.set_chain("ACME", SMK._acme_chain(clock))           # known to the provider only: loaded by "Load the chain"
    finally:
        rt.close()


class _World:
    """One temp home (``_seed12``), its server (port 0, offline) and one headless browser on it, for the class."""

    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp(prefix="fst-simplify-r12-fix-")
        cls.env = {k: os.environ.get(k) for k in (config.HOME_ENV, config.DB_ENV)}
        os.environ[config.HOME_ENV] = cls.home
        os.environ[config.DB_ENV] = os.path.join(cls.home, "terminal.db")
        cls.server = cls.browser = None
        try:
            clock = FakeClock(SMK.AT)
            cls.fp = FakeProvider(clock)
            _seed12(cls.home, clock, cls.fp)
            cls.server = R9._Server9(cls.home, FakeClock(SMK.AT), cls.fp).start()
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

    # the page as the owner uses it (test_terminal_simplify_r6's, _r8's and _r10_fix's helpers)
    js = R6.AnswerFencePageTests.js
    wait = R6.AnswerFencePageTests.wait
    enter_key = R6.AnswerFencePageTests.enter_key
    fresh_smart = R6.AnswerFencePageTests.fresh_smart
    read = R6.AnswerFencePageTests.read
    mouse = R8.PageTests.mouse
    click_xy = R8.PageTests.click_xy
    open_rect = R8.PageTests.open_rect
    order_posts = R8.PageTests.order_posts
    tidy = R10F.PageTests.tidy
    rows = R10F.PageTests.rows
    orders = R10F.PageTests.orders

    def key(self, key: str, code: str, vk: int, text: str = None, modifiers: int = 0) -> None:
        """One real key press (keyDown with its character when it types one, then keyUp), wherever the focus is."""
        base = {"key": key, "code": code, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk, "modifiers": modifiers}
        down = dict(base, type="keyDown")
        if text is not None:
            down.update(text=text, unmodifiedText=text)
        self.browser.call("Input.dispatchKeyEvent", down, session=self.sid)
        self.browser.call("Input.dispatchKeyEvent", dict(base, type="keyUp"), session=self.sid)

    def click_sel(self, sel: str) -> None:
        r = self.js(R8.RECT % json.dumps(sel))
        self.assertTrue(r, f"nothing to click at {sel}")
        self.click_xy(*R8._middle(r))

    def type_5(self, idx: int) -> None:
        """The owner clicks card #idx+1's quantity box and types 5 over its 10 (or its 1): End, Backspace, Backspace, 5 (the box keeps the
        focus)."""
        self.click_sel(QTY_BOX % idx)
        self.key("End", "End", 35)
        self.key("Backspace", "Backspace", 8)
        self.key("Backspace", "Backspace", 8)
        self.key("5", "Digit5", 53, text="5")

    def look_up_widg(self) -> None:
        """Card #1's red flag (SYMBOL_UNKNOWN) opened, then its "Look it up" clicked (one real click each); its request is out."""
        self.click_sel('#smart-cards [data-i="0"] .smart-chip.sev-blocker')
        self.wait("(function () { var b = document.querySelector('#smart-cards [data-i=\"0\"] [data-act=\"action\"]');"
                  " return !!(b && !b.disabled && /Look it up/.test(b.innerText)); })()", timeout=10)
        self.click_sel('#smart-cards [data-i="0"] [data-act="action"]')
        self.wait("window.__r12hold.calls > 0 && /^looking WIDG up/.test(document.getElementById('smart-status').innerText)", timeout=10)

    def load_acme_chain(self) -> None:
        """Card #1's amber flag (OPTION_UNVERIFIED) opened, then its "Load the chain" clicked (one real click each); its request is out."""
        self.click_sel('#smart-cards [data-i="0"] .smart-chip.sev-check')
        self.wait("(function () { var b = document.querySelector('#smart-cards [data-i=\"0\"] [data-act=\"action\"]');"
                  " return !!(b && !b.disabled && /Load the chain/.test(b.innerText)); })()", timeout=10)
        self.click_sel('#smart-cards [data-i="0"] [data-act="action"]')
        self.wait("window.__r12hold.calls > 0 && /^loading the ACME option chain/.test(document.getElementById('smart-status').innerText)", timeout=10)

    def ticket_after_open(self, idx: int, inst: str) -> dict:
        """One real click on card #idx+1's Open in ticket; the docked ticket's fields once it shows ``inst``."""
        self.click_xy(*R8._middle(self.open_rect(idx)))
        self.wait("location.hash.indexOf('#/a/') === 0 && (document.getElementById('ticket-instrument') || {}).value === %s" % json.dumps(inst), timeout=20)
        self.browser.pump(0.8)
        return self.js(R8.TICKET)

    def typed_while_looking(self, idx: int, tab: bool) -> dict:
        """``TEXT_LOOKUP`` read, card #1's look-up clicked (its answer held), 5 typed over card #idx+1's 10 (and Tab when ``tab``); then the
        look-up's answer released and the text read again by the page.  Returns card #idx+1 as the owner sees it then."""
        self.js(HOLD)
        self.addCleanup(self.js, UNHOLD)
        self.read(TEXT_LOOKUP, 2)
        c0 = self.js(CARD % idx)
        self.assertEqual(("10", [], False), (c0["qty"], c0["dropped"], c0["edited"]), c0)
        self.look_up_widg()
        self.type_5(idx)
        typed = self.js(TYPED % json.dumps(QTY_BOX % idx))
        self.assertEqual((True, "5", False), (typed["focus"], typed["value"], typed["parsing"]), typed)
        if tab:
            self.key("Tab", "Tab", 9)                                     # the box left: its change event takes the edit
            self.browser.pump(0.3)
            self.assertTrue(self.js(CARD % idx)["status"].startswith("#%d edited" % (idx + 1)))
        self.assertRegex(self.js(CARD % idx)["status"], r"^(looking WIDG up|#\d edited)")   # the look-up has not answered
        self.js(RELEASE)
        self.wait(READ_AGAIN % 2, timeout=25)                             # the look-up answered; the page read the text again by itself
        self.browser.pump(0.5)
        self.assertEqual(0, self.js("document.querySelectorAll('#smart-cards [data-i=\"0\"] .smart-chip.sev-blocker').length"))     # WIDG found
        return self.js(CARD % idx)

    def leave_by_keys_and_back(self) -> dict:
        """F1 (US Exchange: an F-key acts while typing) and Ctrl+F9 back: the restored card #1 as the owner sees it."""
        self.key("F1", "F1", 112)
        self.wait("location.hash.indexOf('#/a/') === 0 && location.hash.indexOf('/markets') > 0", timeout=15)
        self.browser.pump(0.8)
        self.key("F9", "F9", 120, modifiers=2)
        self.wait(R6.READY, timeout=15)
        self.wait("!document.getElementById('smart-restored').hidden", timeout=10)
        return self.js(CARD % 0)

    def read_again(self) -> dict:
        self.click_sel("#smart-text")
        self.enter_key()                                                  # read again, as the restored note asks
        self.wait(READ_AGAIN % 1, timeout=20)
        self.browser.pump(0.4)
        return self.js(CARD % 0)


@unittest.skipUnless(find_browser(), NO_BROWSER)
class PageTests(_World, unittest.TestCase):

    def test_a_typing_not_left_when_the_look_up_answers_is_said_on_its_card_and_the_ticket_gets_what_the_card_shows(self):
        self.tidy()
        before, start = self.orders(), self.browser.mark()
        self.fresh_smart("P")
        card = self.typed_while_looking(1, tab=False)
        self.assertEqual(("10", False, [NOT_KEPT_READ], ""), (card["qty"], card["edited"], card["dropped"], card["lost"]), card)
        self.assertTrue(card["head"].startswith("#2 BUY 10 ACME"), card)
        t = self.ticket_after_open(1, "ACME")
        self.assertEqual(("BUY", "10", "ACME", "LIMIT", "50"), (t["side"], t["qty"], t["inst"], t["type"], t["limit"]), t)
        self.assertEqual([], self.order_posts(start), "the page sent an order request")
        self.assertEqual(before, self.orders(), "an order was placed")
        self.assertEqual([], self.browser.errors(self.sid, start))

    def test_b_typing_then_a_page_key_is_said_on_the_restored_card(self):
        self.tidy()
        before, start = self.orders(), self.browser.mark()
        self.fresh_smart("P")
        self.read(TEXT_ONE, 1)
        self.type_5(0)
        typed = self.js(TYPED % json.dumps(QTY_BOX % 0))
        self.assertEqual((True, "5"), (typed["focus"], typed["value"]), typed)
        restored = self.leave_by_keys_and_back()
        self.assertEqual(("10", False, [NOT_KEPT_LEFT]), (restored["qty"], restored["edited"], restored["dropped"]), restored)
        card = self.read_again()
        self.assertEqual(("10", False, [NOT_KEPT_LEFT], ""), (card["qty"], card["edited"], card["dropped"], card["lost"]), card)
        # typed again and left (Tab): taken, and the note goes
        self.type_5(0)
        self.key("Tab", "Tab", 9)
        self.browser.pump(0.4)
        card = self.js(CARD % 0)
        self.assertEqual(("5", True, []), (card["qty"], card["edited"], card["dropped"]), card)
        self.assertEqual([], self.order_posts(start), "the page sent an order request")
        self.assertEqual(before, self.orders(), "an order was placed")
        self.assertEqual([], self.browser.errors(self.sid, start))

    def test_d_control_typing_left_with_tab_before_a_page_key_is_kept(self):
        self.tidy()
        before, start = self.orders(), self.browser.mark()
        self.fresh_smart("P")
        self.read(TEXT_ONE, 1)
        self.type_5(0)
        self.key("Tab", "Tab", 9)                                         # the box left: its change event takes the edit
        self.browser.pump(0.4)
        restored = self.leave_by_keys_and_back()
        self.assertEqual(("5", True, []), (restored["qty"], restored["edited"], restored["dropped"]), restored)
        card = self.read_again()
        self.assertEqual(("5", True, [], ""), (card["qty"], card["edited"], card["dropped"], card["lost"]), card)
        self.assertEqual([], self.order_posts(start))
        self.assertEqual(before, self.orders())
        self.assertEqual([], self.browser.errors(self.sid, start))


@unittest.skipUnless(find_browser(), NO_BROWSER)
class LookUpControlTests(_World, unittest.TestCase):
    """A world of its own (WIDG unknown again) for test_a's control."""

    def test_c_control_typing_left_with_tab_while_the_look_up_runs_is_kept(self):
        self.tidy()
        before, start = self.orders(), self.browser.mark()
        self.fresh_smart("P")
        card = self.typed_while_looking(1, tab=True)
        self.assertEqual(("5", True, [], ""), (card["qty"], card["edited"], card["dropped"], card["lost"]), card)
        t = self.ticket_after_open(1, "ACME")
        self.assertEqual(("BUY", "5", "ACME", "LIMIT", "50"), (t["side"], t["qty"], t["inst"], t["type"], t["limit"]), t)
        self.assertEqual([], self.order_posts(start))
        self.assertEqual(before, self.orders())
        self.assertEqual([], self.browser.errors(self.sid, start))


@unittest.skipUnless(find_browser(), NO_BROWSER)
class OneCardTests(_World, unittest.TestCase):
    """A world of its own (the ACME chain not loaded: an ACME hand-off loads it) for the page's one card."""

    def test_i_typing_on_the_pages_one_card_when_its_chain_loads_is_said_and_the_page_reads_on(self):
        self.tidy()
        before, start = self.orders(), self.browser.mark()
        self.fresh_smart("P")
        self.js(HOLD)
        self.addCleanup(self.js, UNHOLD)
        self.read(TEXT_OPTION, 1)
        self.assertEqual(1, self.js("document.getElementById('smart-cards').children.length"))     # the page's one card
        c0 = self.js(CARD % 0)
        self.assertEqual(("1", [], False), (c0["qty"], c0["dropped"], c0["edited"]), c0)
        self.assertIn("(unverified)", c0["head"])
        self.load_acme_chain()
        self.type_5(0)
        typed = self.js(TYPED % json.dumps(QTY_BOX % 0))
        self.assertEqual((True, "5", False), (typed["focus"], typed["value"], typed["parsing"]), typed)
        self.assertRegex(typed["status"], r"^loading the ACME option chain")   # the chain load has not answered
        self.js(RELEASE)
        self.wait(READ_AGAIN % 1, timeout=25)                             # the chain loaded; the page read the text again by itself
        self.browser.pump(0.5)
        card = self.js(CARD % 0)
        self.assertEqual(("1", False, [NOT_KEPT_READ], ""), (card["qty"], card["edited"], card["dropped"], card["lost"]), card)
        self.assertTrue(card["head"].startswith("#1 BTO 1 ACME 2026-12-18 60 call") and "unverified" not in card["head"], card)
        self.assertEqual([], self.browser.errors(self.sid, start))
        t = self.ticket_after_open(0, CALL_DEC60)
        self.assertEqual(("1", CALL_DEC60, "LIMIT", "3.20"), (t["qty"], t["inst"], t["type"], t["limit"]), t)
        self.assertEqual([], self.order_posts(start), "the page sent an order request")
        self.assertEqual(before, self.orders(), "an order was placed")
        self.assertEqual([], self.browser.errors(self.sid, start))


@unittest.skipIf(find_browser() is None, NO_BROWSER)
class StandInTests(unittest.TestCase):
    """The real smartticket.js in a headless tab with test_terminal_simplify_r1's stand-in for window.T (no server, no network)."""

    @classmethod
    def setUpClass(cls):
        cls.browser = Browser(find_browser())

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()

    world = R1.SmartPageCardsTests.world

    def type_in(self, pg, i: int, field: str, text: str) -> None:
        """Card #i+1's box ``field`` focused, its text selected and ``text`` typed (Input.insertText: an input event, no change event)."""
        self.assertTrue(self.browser.js(pg.s, "(function () { var x = document.querySelector('#smart-cards [data-i=\"%d\"] [data-edit=\"%s\"]');"
                                              " x.focus(); x.select(); return document.activeElement === x; })()" % (i, field)))
        self.browser.call("Input.insertText", {"text": text}, session=pg.s)
        time.sleep(0.1)

    def draft(self, pg) -> dict:
        return self.browser.js(pg.s, "T.store.get('smart.draft.v2', null).last")

    def test_e_the_note_stays_when_its_card_is_read_otherwise_until_that_field_is_edited(self):
        rt = self.world()
        a_p, a_f = R1._parse(rt, TEXT_ONE, "P"), R1._parse(rt, TEXT_ONE, "F")
        self.assertEqual(([corpus.P], [corpus.F]), ([l["account_id"] for l in a_p["legs"]], [l["account_id"] for l in a_f["legs"]]))
        pg = R1._Page(self.browser)
        start = self.browser.mark()
        pg.answers(queue=[a_p, a_p, a_f])
        pg.read(TEXT_ONE)
        self.assertEqual(1, self.browser.js(pg.s, "document.getElementById('smart-cards').children.length"))   # the page's one card
        self.type_in(pg, 0, "qty", "5")
        pg.click("#smart-parse", reads=True)                             # read again while the box still has the focus
        self.assertEqual((2, False), (self.browser.js(pg.s, "window.__posts.length"), self.browser.js(pg.s, "document.getElementById('smart-parse').disabled")))
        (card,) = pg.cards()
        self.assertEqual(("10", False, [NOT_KEPT_READ]), (card["qty"], card["edited"], card["dropped"]), card)
        self.assertEqual({}, self.draft(pg)["edits"])                     # no edit made
        pg.click("#smart-parse", reads=True)                             # read again: this answer reads the card in the Fund book
        (card,) = pg.cards()
        self.assertIn("Fund Book", card["where"])
        self.assertIn("was Personal Book . US Exchange", card["was"])
        self.assertEqual(("10", [NOT_KEPT_READ]), (card["qty"], card["dropped"]), card)
        pg.edit(0, "qty", "7")                                            # that field edited: taken, and the note goes
        (card,) = pg.cards()
        self.assertEqual(("7", True, []), (card["qty"], card["edited"], card["dropped"]), card)
        self.assertEqual("", pg.note("smart-lost"))
        self.assertEqual([], self.browser.errors(pg.s, start))

    def test_f_typing_on_a_card_the_new_reading_has_no_match_for_is_said_above_the_cards(self):
        rt = self.world()
        one = "buy 10 WIDG limit 249"
        a2, a1 = R1._parse(rt, TEXT_LOOKUP), R1._parse(rt, one)
        pg = R1._Page(self.browser)
        start = self.browser.mark()
        pg.answers(by_text={TEXT_LOOKUP: a2, one: a1})
        pg.read(TEXT_LOOKUP)
        self.assertEqual(2, len(pg.cards()))
        self.browser.js(pg.s, "(function () { var b = document.getElementById('smart-text'); b.value = %s; b.dispatchEvent(new Event('input', { bubbles: true }));"
                              " return true; })()" % json.dumps(one))
        self.type_in(pg, 1, "qty", "5")
        pg.click("#smart-parse", reads=True)
        (card,) = pg.cards()
        self.assertEqual(("10", []), (card["qty"], card["dropped"]), card)
        self.assertEqual('dropped with the new reading: your typing on #2 ("buy 10 ACME limit 50"): qty 5 (not kept) - no order of the new reading'
                         ' reads as that one did, nor has its words', pg.note("smart-lost"))
        self.assertEqual([{"n": 2, "clause": "buy 10 ACME limit 50", "field": "qty", "edit": "5", "typed": "read"}], self.draft(pg)["lost"])
        self.assertEqual([], self.browser.errors(pg.s, start))

    def test_g_the_page_left_while_typing_says_it_in_the_draft_and_on_the_restored_card(self):
        rt = self.world()
        a1 = R1._parse(rt, TEXT_ONE)
        pg = R1._Page(self.browser)
        start = self.browser.mark()
        pg.answers(by_text={TEXT_ONE: a1})
        pg.read(TEXT_ONE)
        self.type_in(pg, 0, "qty", "5")
        # the browser leaves the page itself (a reload, the window closed): the draft says it, and no edit is made
        self.browser.js(pg.s, "window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: false })); true")
        d = self.draft(pg)
        self.assertEqual(({}, [[{"field": "qty", "edit": "5", "typed": "left"}]]), (d["edits"], [n["dropped"] for n in d["notes"].values()]), d)
        # the page left by a key with the box still focused (the route unmounts it), then shown again: the restored card says it
        pg.unmount_and_back()
        (card,) = pg.cards()
        self.assertEqual(("10", False, [NOT_KEPT_LEFT]), (card["qty"], card["edited"], card["dropped"]), card)
        pg.read()
        (card,) = pg.cards()
        self.assertEqual(("10", False, [NOT_KEPT_LEFT]), (card["qty"], card["edited"], card["dropped"]), card)
        # opened in the ticket with what it shows: its notes go
        pg.click('#smart-cards [data-i="0"] [data-act="open"]')
        self.assertEqual(["#/a/P1:US_EXCHANGE/markets"], self.browser.js(pg.s, "window.__routes"))
        pg.unmount_and_back()
        (card,) = pg.cards()
        self.assertEqual(("10", True, []), (card["qty"], card["opened"], card["dropped"]), card)
        self.assertEqual([], self.browser.errors(pg.s, start))

    def test_h_control_enter_in_the_box_takes_the_typing(self):
        rt = self.world()
        a1 = R1._parse(rt, TEXT_ONE)
        pg = R1._Page(self.browser)
        start = self.browser.mark()
        pg.answers(by_text={TEXT_ONE: a1})
        pg.read(TEXT_ONE)
        self.type_in(pg, 0, "qty", "5")
        base = {"key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13}
        self.browser.call("Input.dispatchKeyEvent", dict(base, type="keyDown", text="\r", unmodifiedText="\r"), session=pg.s)
        self.browser.call("Input.dispatchKeyEvent", dict(base, type="keyUp"), session=pg.s)
        time.sleep(0.2)
        self.assertTrue(self.browser.js(pg.s, "document.getElementById('smart-status').textContent").startswith("#1 edited"))
        pg.click("#smart-parse", reads=True)
        (card,) = pg.cards()
        self.assertEqual(("5", True, []), (card["qty"], card["edited"], card["dropped"]), card)
        self.assertEqual("", pg.note("smart-lost"))
        self.assertEqual([], self.browser.errors(pg.s, start))


class SourceTests(unittest.TestCase):
    def test_typing_not_left_is_said_never_taken(self):
        src = _read(SMART)
        for s in ("var TYPED_WHY = { read: 'the text was read again before you left the box', left: 'the page was left before you left the box' };",
                  "if (!S || !ui || !t || t.tagName !== 'INPUT' || t.type === 'checkbox' || !t.dataset || !t.dataset.edit || !ui.cards.contains(t)) { return false; }",
                  "var f = t.dataset.edit, v = boxValue(t), y = editOf(c, f, v), now = effOf(S, c);",
                  "if (y && Object.keys(y).every(function (k) { return same(y[k], now[k]); })) { return false; }",
                  "c.dropped = c.dropped.filter(function (d) { return !(d.typed && d.field === f); }).concat([{ field: f, edit: v, typed: TYPED_WHY[why] ? why : 'left' }]);",
                  "var f = t.dataset.edit; var y = editOf(c, f, boxValue(t));",
                  "var x = Object.assign({}, S.edits[c.key] || {}, y);",
                  "c.dropped = c.dropped.concat(typedOf(o));",
                  "typedOf(o).forEach(function (d) { lost.push({ n: o.n, clause: String(o.leg.clause || ''), field: d.field, edit: d.edit, typed: d.typed }); });",
                  "window.addEventListener('pagehide', function () { var S = st; if (S && !S.dead && noteTyping(S, 'left')) { saveState(S); } });"):
            self.assertEqual(1, src.count(s), s)
        # noteTyping makes no edit and moves no opened mark: it only says
        at = src.index("function noteTyping(S, why) {")
        body = src[at:src.index("\n  }\n", at)]
        for gone in ("S.edits", "S.opened", "paint(", "saveState("):
            self.assertNotIn(gone, body, gone)
        # where it runs: a reading (before the cards are painted read-only), the page left (before its draft is saved), pagehide
        self.assertEqual(3, len(re.findall(r"(?<!function )noteTyping\(S, ", src)))
        at = src.index("  function parse(textOverride, forBook) {")
        body = src[at:src.index("\n  }\n", at)]
        self.assertLess(body.index("if (noteTyping(S, 'read')) { saveState(S); }"), body.index("S.parsing = true; say(S, 'reading ...'); paint(S);"))
        at = src.index("  function unmount() {")
        body = src[at:src.index("\n  }\n", at)]
        self.assertLess(body.index("if (ui) { S.text = ui.bar.value; noteTyping(S, 'left'); }"), body.index("saveState(S);"))
        # the change event still takes an edit (onEdit), and an edit's repaint still waits while a pointer is down (simplify round 1)
        self.assertEqual(1, src.count("ui.cards.addEventListener('change', onEdit);"))
        self.assertEqual(1, src.count("if (pointerDown) { repaintDue = S; }"))

    def test_the_change_a_box_fires_as_the_pages_own_repaint_removes_it_is_no_edit(self):
        src = _read(SMART)
        self.assertEqual(1, src.count("  var repainting = false;"))
        self.assertEqual(1, src.count("repainting = true;"))
        self.assertEqual(1, src.count("ui.cards.innerHTML = "))              # the one place the cards' boxes are replaced
        at = src.index("  function paintCards(S) {")
        body = src[at:src.index("\n  }\n", at)]
        self.assertLess(body.index("repainting = true;"), body.index("    try {\n      ui.cards.innerHTML = "))
        self.assertLess(body.index("ui.cards.innerHTML = "), body.index("    } finally { repainting = false; }"))
        at = src.index("  function onEdit(e) {")
        body = src[at:src.index("\n  }\n", at)]
        self.assertLess(body.index("if (repainting || !S || S.dead || !t || !t.dataset || !t.dataset.edit) { return; }"), body.index("canEdit(S, c)"))


if __name__ == "__main__":
    unittest.main()
