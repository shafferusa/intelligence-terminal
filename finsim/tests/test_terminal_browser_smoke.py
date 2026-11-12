"""FINSIM1 P8 task 2 (TEST-06): the browser smoke test - the real pages in a real headless browser (owner directive section 18:
"Verify the actual interface in a browser. Do not claim completion based only on backend tests").

ONE temp home with the Fund Book (F1, 25,000,000.00).  It is seeded through the HTTP routes the UI uses on a ``build_runtime`` with a
FakeClock (Tue 2026-10-20 14:00 ET) and a FakeProvider (no network):
  - a filled market BUY of 100 AAPL in US Exchange (a Completed Trades row, settling T+1, with its Treasury sweep);
  - a resting LIMIT BUY of 50 SPY at 700.00, far below the 769.32 print (an open Pending Trades row);
  - a LIMIT BUY of 10 MSFT at 400.00, then cancelled (the Cancelled filter);
  - a filled market BUY of 1 GCZ26 (a futures position for the Derivatives > Futures tab);
  - a refused SELL of 100 MSFT the account does not hold (the Rejected filter: the order ticket's refusal is recorded);
  - 1.0.7.2: two fictional listings for the Smart Ticket, ACME and WIDG (master rows as Yahoo resolution stores them, a print each, an
    ACME option chain on the FakeProvider; invented prices), with no order in them, so every count the other tests read is unchanged.
The runtime is closed and ``serve(offline=True)`` serves the same database on port 0 with the same FakeProvider (its prints) and a
FakeClock at the same instant.  A headless Edge / Chrome (``terminal_cdp``, standard library CDP) opens every main route of the
directive's navigation at 1440 x 900: Dashboard, Portfolio, Trading, Pending Trades (each status filter), Completed Trades (an
execution drawer, every tab), Accounts, Cash & Collateral, Derivatives, History, Settings, Risk, Accounting, Activity and the Smart
Ticket; then (``test_x_...`` to ``test_xd_...``, 1.0.7.2 simplified on the owner's decision of 2026-11-11) the Smart Ticket's flow: it
fills standard order tickets and sends nothing itself - #/smart posts only the parse, Open in ticket fills the account page's docked
ticket as a new order, and the owner previews and sends it there.

For each route it asserts:
  - 0 console errors, uncaught exceptions or browser log errors, and 0 HTTP responses >= 400 (and no failed request);
  - a rendered main table or tiles in ``#page``, and no NOT YET BUILT panel (every page of 1.0.6 is in the build);
  - none of the removed news feature's strings (P1's ``NEWS_STRINGS``, with P1's allowlist), no "AI Research", and no "undefined",
    "NaN" or "[object Object]" on screen (plan section 3 rule 5).

Skipped, with that reason, where no Chromium-family browser exists (``terminal_cdp.find_browser``).  Set FINSIM_SMOKE_SHOTS to a folder
to keep a PNG of every route.
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

from tests.terminal_cdp import NO_BROWSER, Browser, find_browser
from tests.terminal_helpers import _Log
from tests.test_terminal_static import NEWS_STRINGS
from finsim.terminal import config, server as _server
from finsim.terminal.clock import FakeClock, epoch_of
from finsim.terminal.instruments import occ_symbol
from finsim.terminal.quotes import Bar1m, QuoteRecord
from finsim.terminal.quotes.cache import records_from_chain
from finsim.terminal.quotes.providers import ChainRow, ChainSnapshot, ChainUnderlying, FakeProvider
from finsim.terminal.units import qprice

AT = "2026-10-20T14:00:00-04:00"                        # Tuesday 14:00 ET: US equities open
FUND = config.BOOKS[1]                                  # ("F", "Fund Book", "FUND", 25,000,000.00)
USX = "F1:US_EXCHANGE"
PRINTS = {"AAPL": "339.50", "SPY": "769.32", "MSFT": "500.00", "QQQ": "690.00", "ORCL": "129.13", "GCZ26": "4140.00"}
#: P1's allowlist (P1 acceptance 3): not news - the private-company master's source URLs and Risk's "not in the headline gross"
ALLOW = [re.compile(r"https?://\S*/news/\S*", re.I), re.compile(r"\bnot in the headline gross\b", re.I)]
BAD_WORDS = [re.compile(r"\bundefined\b"), re.compile(r"\bNaN\b"), re.compile(r"\[object Object\]"), re.compile(r"AI Research", re.I)]
PENDING_FILTERS = ("open", "filled", "cancelled", "expired", "rejected", "all")
#: (route, what must be rendered) - "table" = a table in #page, "tiles" = a tile row in #page, "text" = the page's own words (an empty
#: state such as "no OTC contracts in this book yet" is a rendered page; the Futures tab carries the seeded GCZ26 position's table)
ROUTES = [("#/dashboard", "tiles"), ("#/portfolio", "tiles"), ("#/trading", "table")] + \
         [(f"#/pending?status={s}", "table") for s in PENDING_FILTERS] + \
         [("#/completed", "table"), ("#/accounts", "table"), ("#/cash", "table"), ("#/derivatives", "text"), ("#/derivatives/options", "text"),
          ("#/derivatives/futures", "table"), ("#/derivatives/servicing", "text"), ("#/history", "table"), ("#/settings", "table"), ("#/risk", "table"),
          ("#/accounting", "table"), ("#/activity", "table"), ("#/smart", "text")]
#: 1.0.7.2: the Smart Ticket's fictional listings (name, last print) and an invented ACME chain (expiry, right, strike, bid, ask)
SMART_NAMES = {"ACME": ("Acme Test Corp", "60.00"), "WIDG": ("Widget Test Inc", "250.00")}
ACME_CHAIN = [("2026-12-18", "C", 60, "3.10", "3.30"), ("2026-12-18", "C", 65, "1.20", "1.35"), ("2026-12-18", "P", 55, "1.40", "1.55"),
              ("2027-01-15", "C", 60, "4.20", "4.45")]
#: 1.0.7.2 simplified: a shares order (a 2 % trailing stop), a covered call on 100 ACME held (the calls only) and a call spread (two
#: tickets in a group): four cards
X_TEXT = "buy 10 ACME trailing stop 2%; covered call 100 ACME Jan 15 2027 60; buy 1 ACME Dec 18 2026 60/65 call spread"
CALL_JAN60 = occ_symbol("ACME", date(2027, 1, 15), "C", Decimal("60"))


def _acme_chain(clock: FakeClock) -> ChainSnapshot:
    """The invented ACME chain as the live provider would reduce it (a Cboe snapshot observed a minute ago)."""
    now = int(clock.epoch())
    qt = now - 60
    rows = [ChainRow(occ_symbol("ACME", date.fromisoformat(e), r, Decimal(str(k))), e, r, qprice(k), qprice(b), qprice(a), 20, 20, None, None, 0.30, None, None,
                     None, None, 100, 10) for e, r, k, b, a in ACME_CHAIN]
    under = ChainUnderlying(symbol="ACME", last=qprice("60.00"), bid=None, ask=None, bid_size=None, ask_size=None, last_trade_time=qt, prev_close=None, iv30=0.30)
    return ChainSnapshot(underlying_id="ACME", cboe_symbol="ACME", underlying=under, contracts=rows, fetched_at=now, quote_time=qt, n_raw=len(rows))

# the page state the checks read in one evaluation
PAGE_STATE = """(function () {
  var page = document.querySelector('#page'); var text = document.body ? document.body.innerText : '';
  var pt = page ? page.innerText : '';
  return { hash: location.hash, text: text, page_text: pt,
           tables: page ? page.querySelectorAll('table').length : 0, tiles: page ? page.querySelectorAll('.tiles .tile').length : 0,
           notbuilt: page ? Array.prototype.map.call(page.querySelectorAll('.notbuilt'), function (n) { return n.innerText; }) : [],
           errors: page ? Array.prototype.filter.call(page.querySelectorAll('.error'), function (n) { return !n.hidden && n.offsetParent !== null && n.innerText.trim(); })
                          .map(function (n) { return n.innerText; }) : [],
           loading: /\\bloading\\b/i.test(pt), ready: !!(window.T && T.router && T.router.current) };
})()"""


#: the Smart Ticket's cards as the page shows them: number, side, head line, whole text, group, whether Open in ticket is enabled (and why
#: not), the "opened in the ticket" mark and the "sold as covered" tick
CARDS = """(function () {
  return Array.prototype.map.call(document.querySelectorAll('#smart-cards [data-i]'), function (c) {
    var open = c.querySelector('[data-act="open"]'); var side = c.querySelector('.smart-card-head b'); var g = c.closest('.smart-group');
    var cov = c.querySelector('[data-edit="covered"]');
    return { i: Number(c.dataset.i), num: (c.querySelector('.smart-num') || {}).textContent || '', side: side ? side.textContent : '',
             head: (c.querySelector('.smart-card-head') || {}).innerText || '', text: c.innerText, group: g ? g.dataset.g : null,
             open: !!(open && !open.disabled), why: open ? (open.title || '') : '', opened: !!c.querySelector('.smart-opened'), covered: !!(cov && cov.checked) };
  });
})()"""

#: the docked order ticket as the owner sees it (its fields, its hedge pick, its Submit and its preview's blockers)
TICKET_FIELDS = """(function () {
  function el(id) { return document.getElementById(id); }
  function v(id) { var e = el(id); return e ? (e.type === 'checkbox' ? e.checked : e.value) : null; }
  function shown(id) { var e = el(id); return !!(e && e.offsetParent !== null); }
  var sb = el('ticket-submit-btn');
  return { hash: location.hash, dock: T.dock.visible(), account: (el('ticket-account') || {}).title || '', inst: v('ticket-instrument'), side: v('ticket-side'),
           qty: v('ticket-qty'), type: v('ticket-type'), limit: v('ticket-limit'), stop: v('ticket-stop'), trail_mode: v('ticket-trail-mode'), trail: v('ticket-trail'),
           trail_shown: shown('ticket-trail'), tif: v('ticket-tif'), covered: v('ticket-covered'), covered_shown: shown('ticket-covered'),
           covered_words: (el('ticket-covered-words') || {}).textContent || '', picked: document.querySelectorAll('#ticket-hedge .hg-cand.picked').length,
           submit: !!(sb && !sb.disabled),
           blockers: Array.prototype.map.call(document.querySelectorAll('#ticket-preview .pv-blocker'), function (x) { return x.dataset.code + ': ' + x.innerText; }) };
})()"""


def _seed(home: str, clock: FakeClock, fp: FakeProvider) -> dict:
    """The Fund Book's few rows, booked through the routes the UI uses on a runtime that is then closed; returns the ids."""
    rt = _server.build_runtime(home, os.path.join(home, "terminal.db"), clock=clock, offline=True, fake_provider=fp, log=_Log(), books=[FUND],
                               fetch_threads=0)
    try:
        qt = epoch_of(clock.today(), "13:58")
        aapl = rt.instruments.get("AAPL")
        with rt.db.writer() as tx:                       # the Smart Ticket's fictional listings, stored as Yahoo resolution stores a US listing
            for sym, (name, _last) in SMART_NAMES.items():
                rt.instruments.upsert(tx, instrument_id=sym, kind="EQUITY", symbol=sym, name=name, currency="USD", session_group=aapl.session_group,
                                      account_kinds=list(aapl.account_kinds), yahoo_symbol=sym, cboe_symbol=sym, spec={"exchange": "NYQ"})
        fp.set_chain("ACME", _acme_chain(clock))
        for iid, last in dict(PRINTS, **{s: v[1] for s, v in SMART_NAMES.items()}).items():
            inst = rt.instruments.get(iid)
            fp.set(inst.yahoo_symbol, last=last, quote_time=qt, bar=(qt - qt % 60 - 60, 61900))
            rt.cache.subscribe([iid], 86400, "PAGE")
        rt.worker.tick()

        def call(method, path, body=None):
            return rt.router.handle(method, path, None, body, "127.0.0.1", None)

        st, filled, _ = call("POST", "/api/orders", {"account_id": USX, "instrument_id": "AAPL", "side": "BUY", "qty": 100, "order_type": "MARKET",
                                                     "tif": "DAY", "request_id": "ui:smoke-aapl"})
        assert st == 200 and filled["order"]["status"] == "FILLED", (st, filled)
        st, working, _ = call("POST", "/api/orders", {"account_id": USX, "instrument_id": "SPY", "side": "BUY", "qty": 50, "order_type": "LIMIT",
                                                      "limit_px": "700.00", "tif": "DAY", "request_id": "ui:smoke-spy"})
        assert st == 200 and working["order"]["status"] == "WORKING", (st, working)
        st, gone, _ = call("POST", "/api/orders", {"account_id": USX, "instrument_id": "MSFT", "side": "BUY", "qty": 10, "order_type": "LIMIT",
                                                   "limit_px": "400.00", "tif": "GTC", "request_id": "ui:smoke-msft"})
        assert st == 200, (st, gone)
        st, out, _ = call("DELETE", f"/api/orders/{gone['order']['order_id']}", {"request_id": "ui:smoke-msft-cancel"})
        assert st == 200, (st, out)
        st, fut, _ = call("POST", "/api/orders", {"account_id": USX, "instrument_id": "GCZ26", "side": "BUY", "qty": 1, "order_type": "MARKET",
                                                  "tif": "DAY", "request_id": "ui:smoke-gc"})
        assert st == 200 and fut["order"]["status"] == "FILLED", (st, fut)
        st, refused, _ = call("POST", "/api/orders", {"account_id": USX, "instrument_id": "MSFT", "side": "SELL", "qty": 100, "order_type": "MARKET",
                                                      "tif": "DAY", "request_id": "ui:smoke-msft-sell"})
        assert st >= 400, (st, refused)
        with rt.db.reader() as conn:
            rejected = conn.execute("SELECT COUNT(*) FROM order_rejections").fetchone()[0]
        return {"filled": filled["order"]["order_id"], "working": working["order"]["order_id"], "cancelled": gone["order"]["order_id"],
                "rejections": int(rejected)}
    finally:
        rt.close()


class _Server:
    """``serve`` on a thread (port 0, offline, the seeded FakeProvider); ``stop`` sets the stop event and joins."""

    def __init__(self, home: str, clock: FakeClock, fp: FakeProvider):
        self.home, self.clock, self.fp = home, clock, fp
        self.rt, self.rc, self.msgs = None, None, []
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, name="smoke-serve", daemon=True)

    def _run(self):
        self.rc = _server.serve(port=0, home=self.home, db=os.path.join(self.home, "terminal.db"), offline=True, ready=self._ready, out=self.msgs.append,
                                clock=self.clock, fake_provider=self.fp, books=[FUND], fetch_threads=0, supervisor_interval=0.5, log=_Log())

    def _ready(self, rt):
        self.rt = rt
        self.ready.set()

    def start(self, timeout: float = 90.0) -> "_Server":
        self.thread.start()
        if not self.ready.wait(timeout):
            raise RuntimeError(f"the server did not become ready: {self.msgs}")
        return self

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{int(self.rt.httpd.server_address[1])}/"

    def stop(self, timeout: float = 30.0) -> None:
        if self.rt is not None:
            self.rt.stop_event.set()
        self.thread.join(timeout)


def _clean(text: str) -> str:
    for rx in ALLOW:
        text = rx.sub(" ", text)
    return text


@unittest.skipUnless(find_browser(), NO_BROWSER)
class BrowserSmokeTests(unittest.TestCase):
    """One server and one headless browser for the whole class (RAM: one of each at a time, both closed in tearDownClass)."""

    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp(prefix="fst-smoke-")
        cls.env = {k: os.environ.get(k) for k in (config.HOME_ENV, config.DB_ENV)}
        os.environ[config.HOME_ENV] = cls.home
        os.environ[config.DB_ENV] = os.path.join(cls.home, "terminal.db")
        cls.server = cls.browser = None
        try:
            clock = FakeClock(AT)
            fp = FakeProvider(clock)
            cls.seeded = _seed(cls.home, clock, fp)
            cls.server = _Server(cls.home, FakeClock(AT), fp).start()
            cls.browser = Browser(find_browser(), 1440, 900)
            cls.sid = cls.browser.page("about:blank")
            cls.browser.navigate(cls.sid, cls.server.url)
            cls.browser.wait(cls.sid, "!!(window.T && T.router && T.router.current && document.querySelector('#page'))", timeout=45)
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

    # ---- one route ---------------------------------------------------------------------------------------------------------
    def visit(self, route: str, want: str = "table", timeout: float = 25.0) -> dict:
        """Open ``route`` and wait until its page has rendered its content (no "loading" left, a table / tiles / an error shown)."""
        b, sid = self.browser, self.sid
        b.js(sid, f"location.hash = {route!r}; true")
        state = None
        for _ in range(int(timeout / 0.25)):
            state = b.js(sid, PAGE_STATE)
            if state["ready"] and not state["loading"] and (state["tables"] or state["tiles"] or state["errors"] or state["notbuilt"]
                                                            or (want == "text" and len(state["page_text"].strip()) > 40)):
                break
            b.pump(0.2)
        b.pump(0.6)                                       # late answers of the page's own polls
        return b.js(sid, PAGE_STATE)

    def check(self, route: str, want: str, state: dict, since: int) -> None:
        b, sid = self.browser, self.sid
        self.assertEqual([], b.errors(sid, since), f"{route}: console errors")
        self.assertEqual([], b.http_errors(sid, since), f"{route}: HTTP >= 400")
        self.assertEqual([], state["notbuilt"], f"{route}: a NOT YET BUILT panel")
        self.assertEqual([], state["errors"], f"{route}: an error box")
        if want == "tiles":
            self.assertGreater(state["tiles"], 0, f"{route}: no tiles rendered")
        elif want == "table":
            self.assertGreater(state["tables"], 0, f"{route}: no table rendered")
        else:
            self.assertGreater(len(state["page_text"].strip()), 40, f"{route}: the page is empty")
        text = _clean(state["text"])
        for s in NEWS_STRINGS:
            self.assertNotIn(s.lower(), text.lower(), f"{route}: the removed news feature's {s!r} is on screen")
        for rx in BAD_WORDS:
            self.assertIsNone(rx.search(text), f"{route}: {rx.pattern!r} on screen")
        shots = os.environ.get("FINSIM_SMOKE_SHOTS")
        if shots:
            b.screenshot(sid, os.path.join(shots, re.sub(r"[^A-Za-z0-9]+", "_", route).strip("_") + ".png"))

    # ---- the routes --------------------------------------------------------------------------------------------------------------
    def test_every_main_route_renders_without_errors(self):
        self.assertEqual(1, self.seeded["rejections"])                       # the refused SELL is recorded (the Rejected filter's row)
        seen = []
        for route, want in ROUTES:
            with self.subTest(route=route):
                since = self.browser.mark()
                state = self.visit(route, want)
                self.check(route, want, state, since)
                seen.append(state["hash"])
        # #/trading is the last venue used, else US Exchange Markets: the address became that venue's
        self.assertIn("#/a/F1%3AUS_EXCHANGE/markets", seen)
        self.assertNotIn("#/trading", seen)

    def test_pending_filters_list_the_seeded_orders(self):
        rows = {}
        for status in ("open", "cancelled", "rejected"):
            self.visit(f"#/pending?status={status}")
            rows[status] = self.browser.js(self.sid, "document.querySelector('#page').innerText")
        self.assertIn(self.seeded["working"], rows["open"])
        self.assertNotIn(self.seeded["filled"], rows["open"])
        self.assertIn(self.seeded["cancelled"], rows["cancelled"])
        self.assertIn("MSFT", rows["rejected"])

    def test_completed_trades_drawer_opens_with_every_tab(self):
        b, sid = self.browser, self.sid
        since = b.mark()
        self.visit("#/completed")
        self.assertIn(self.seeded["filled"], b.js(sid, "document.querySelector('#page').innerText"))
        b.js(sid, """(function () { var tr = document.querySelector('#page .grid table tbody tr[data-i]');
                      tr.dispatchEvent(new MouseEvent('dblclick', { bubbles: true })); return true; })()""")
        b.wait(sid, "!!document.querySelector('#page .drawer .drawer-tabs')", timeout=20)
        tabs = b.js(sid, "Array.prototype.map.call(document.querySelectorAll('#page .drawer .drawer-tabs .tab'), function (t) { return t.innerText; })")
        self.assertGreaterEqual(len(tabs), 4, tabs)
        for i, name in enumerate(tabs):
            b.js(sid, f"document.querySelectorAll('#page .drawer .drawer-tabs .tab')[{i}].click(); true")
            b.pump(0.6)
            text = b.js(sid, "document.querySelector('#page .drawer').innerText")
            self.assertNotIn("loading", text.lower(), f"drawer tab {name}")
            for rx in BAD_WORDS:
                self.assertIsNone(rx.search(text), f"drawer tab {name}: {rx.pattern!r}")
        self.assertEqual([], b.errors(sid, since), "the execution drawer: console errors")
        self.assertEqual([], b.http_errors(sid, since), "the execution drawer: HTTP >= 400")

    def test_the_blotters_fit_1440_with_the_ticket_dock_closed_and_open(self):
        """P8R1-04 (plan rule 5: new tables fit 1440 px with the ticket dock open): at 1440 x 900 the Pending Trades history and Completed
        Trades fit their box with the dock closed (every column) and open (the narrow layout folds the secondary columns into the cells
        beside them) - nothing behind a sideways scroll, nothing clipped."""
        b, sid = self.browser, self.sid
        since = b.mark()
        seen = {}
        for route in ("#/pending?status=all", "#/completed"):
            self.visit(route)
            for dock in (False, True):
                b.js(sid, f"T.dock.show({'true' if dock else 'false'}); true")
                b.pump(0.8)
                m = b.js(sid, """(function () { var g = document.querySelector('#page .grid'); var s = g && g.querySelector('.scroll');
                                  return s ? { client: s.clientWidth, scroll: s.scrollWidth, narrow: g.classList.contains('grid-narrow'),
                                               cols: Array.prototype.filter.call(g.querySelectorAll('thead th'), function (th) { return th.offsetWidth > 0; }).length } : null; })()""")
                self.assertIsNotNone(m, route)
                seen[(route, dock)] = m
                self.assertLessEqual(m["scroll"], m["client"], f"{route} with the dock {'open' if dock else 'closed'}: {m}")
                self.assertEqual(dock, m["narrow"], f"{route}: the narrow layout follows the dock ({m})")
            b.js(sid, "T.dock.show(false); true")
            b.pump(0.3)
        # the wide layout shows every column; the narrow one folds the stage, account / counterparty and reservation (Pending) and the
        # account / counterparty and source (Completed)
        self.assertEqual((9, 6), (seen[("#/pending?status=all", False)]["cols"], seen[("#/pending?status=all", True)]["cols"]))
        self.assertEqual((8, 6), (seen[("#/completed", False)]["cols"], seen[("#/completed", True)]["cols"]))
        self.assertEqual([], b.errors(sid, since))
        self.assertEqual([], b.http_errors(sid, since))

    def test_the_browser_never_asks_for_a_favicon(self):
        """P8R1-01: index.html declares its own empty icon, so the browser never requests /favicon.ico (the server has none: its 404 was a
        console error that turned this smoke test red now and then).  Read over the whole session, from the first page load on: the
        browser fetches a page's icon a moment after the load, outside any one route's window."""
        b, sid = self.browser, self.sid
        self.visit("#/dashboard", "tiles")
        b.pump(1.5)                                       # time for a late icon request, had there been one
        seen = [e for e in b.errors(None, 0) if "favicon" in e.lower()]
        seen += [f"{r['status']} {r['url']}" for r in b.responses(None, 0) if "favicon" in str(r["url"] or "").lower()]
        self.assertEqual([], seen)

    # ---- the Smart Ticket (1.0.7.2 simplified, SIMPLIFY_PLAN s3 / s9): it fills standard order tickets and sends nothing itself -------
    def _press(self, key: str, vk: int, ctrl: bool = False, shift: bool = False) -> None:
        """One key press with Ctrl and / or Shift (Browser.key knows Shift only)."""
        base = {"key": key, "code": key, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk, "modifiers": (2 if ctrl else 0) | (8 if shift else 0)}
        for kind in ("keyDown", "keyUp"):
            self.browser.call("Input.dispatchKeyEvent", dict(base, type=kind), session=self.sid)

    def _smart_grid_fits(self, when: str) -> None:
        """Plan rule 5 (new tables fit 1440 px): the card grid and the page never scroll sideways; the dock is hidden on #/smart."""
        m = self.browser.js(self.sid, "(function () { var g = document.querySelector('#smart-cards'); var d = document.documentElement;"
                                      " return g ? { client: g.clientWidth, scroll: g.scrollWidth, page: d.scrollWidth, view: d.clientWidth, dock: T.dock.visible() } : null; })()")
        self.assertIsNotNone(m, when)
        self.assertFalse(m["dock"], f"#/smart {when}: the ticket dock shows")
        self.assertLessEqual(m["scroll"], m["client"], f"#/smart {when}: the card grid scrolls sideways at 1440 px ({m})")
        self.assertLessEqual(m["page"], m["view"], f"#/smart {when}: the page scrolls sideways at 1440 px ({m})")

    def _posts(self, since: int, path: str) -> list:
        """Every POST to ``path`` (exactly; the query aside) the page sent since ``since``: {rid, body, query, status}."""
        status, out = {}, []
        for e in self.browser._events(self.sid, since):
            m, p = e.get("method"), e.get("params") or {}
            if m == "Network.responseReceived":
                status[p.get("requestId")] = int((p.get("response") or {}).get("status") or 0)
            elif m == "Network.requestWillBeSent":
                r = p.get("request") or {}
                u = urlsplit(r.get("url") or "")
                if r.get("method") == "POST" and u.path == path:
                    body = json.loads(r.get("postData") or "{}")
                    hdr = {k.lower(): v for k, v in (r.get("headers") or {}).items()}
                    out.append({"id": p.get("requestId"), "rid": hdr.get("x-request-id") or body.get("request_id"), "body": body, "query": u.query})
        for x in out:
            x["status"] = status.get(x.pop("id"))
        return out

    def _order_posts(self, since: int) -> list:
        """Every POST /api/orders (an order submit; not its preview, not a cancel) the page sent since ``since``."""
        return self._posts(since, "/api/orders")

    def _writes(self, since: int) -> list:
        """Every request other than a GET the page sent since ``since``: (method, path)."""
        out = []
        for e in self.browser._events(self.sid, since):
            if e.get("method") == "Network.requestWillBeSent":
                r = (e.get("params") or {}).get("request") or {}
                if r.get("method") not in ("GET", "HEAD", "OPTIONS"):
                    out.append((r.get("method"), urlsplit(r.get("url") or "").path))
        return out

    def _served_chain(self, moved=None, size: int = 20, extra=()) -> None:
        """The invented ACME chain observed now in the served runtime (as its quote worker caches a fetched chain), ``moved`` {(expiry,
        right, strike): (bid, ask)} replacing those rows' quotes, ``size`` contracts displayed on every bid and ask, ``extra`` rows added."""
        rt, fp = self.server.rt, self.server.fp
        now = int(rt.clock.epoch())
        rows = []
        for e, r, k, bid, ask in list(ACME_CHAIN) + list(extra):
            bid, ask = (moved or {}).get((e, r, k), (bid, ask))
            rows.append(ChainRow(occ_symbol("ACME", date.fromisoformat(e), r, Decimal(str(k))), e, r, qprice(k), qprice(bid), qprice(ask), size, size, None, None, 0.30,
                                 None, None, None, None, 100, 10))
        under = ChainUnderlying(symbol="ACME", last=qprice("60.00"), bid=None, ask=None, bid_size=None, ask_size=None, last_trade_time=now - 60, prev_close=None, iv30=0.30)
        snap = ChainSnapshot(underlying_id="ACME", cboe_symbol="ACME", underlying=under, contracts=rows, fetched_at=now, quote_time=now - 60, n_raw=len(rows))
        fp.set_chain("ACME", snap)
        rt.chains.put(snap)
        for rec in records_from_chain(snap, rt.instruments):
            rt.cache.put(rec, now)

    def _acme_held(self) -> int:
        with self.server.rt.db.reader() as conn:
            r = conn.execute("SELECT qty FROM v_positions WHERE account_id=? AND instrument_id='ACME'", (USX,)).fetchone()
        return int(Decimal(str(r[0]))) if r else 0

    def _set_acme(self, qty: int) -> None:
        """F1 holds ``qty`` ACME: the difference bought or sold at market through the order route on a fresh print (60.00 observed at
        13:58:30 on the 13:57 bar of 1,000,000 shares: its 10 % participation cap is never reached; older than the 13:59 print on the
        13:58 bar ``test_zy_...`` puts, so that one still replaces it and its cap is its own)."""
        rt = self.server.rt
        held = self._acme_held()
        if held == qty:
            return
        now = int(rt.clock.epoch())
        t0 = epoch_of(rt.clock.today(), "13:58")
        px = qprice("60.00")
        bar = Bar1m(t0 - 60, px, px, px, px, 1000000)
        rt.cache.put(QuoteRecord(instrument_id="ACME", provider="yahoo", source="Yahoo Finance chart (ACME)", method="direct", last=px, quote_time=t0 + 30,
                                 fetched_at=now, delay_s=900, delay_label="up to 15 min delayed", currency="USD", session_group=rt.instruments.get("ACME").session_group,
                                 bar=bar, bars_tail=[bar]), now)
        body = {"account_id": USX, "instrument_id": "ACME", "side": "BUY" if qty > held else "SELL", "qty": abs(qty - held), "order_type": "MARKET", "tif": "DAY",
                "request_id": f"ui:smoke-acme-{os.urandom(6).hex()}"}
        st_, out, _h = rt.router.handle("POST", "/api/orders?book=F", None, body, "127.0.0.1", None)
        self.assertEqual((200, "FILLED"), (st_, (out.get("order") or {}).get("status")), out)
        self.assertEqual(qty, self._acme_held())

    def _tidy_after(self) -> None:
        """When this test ends (passed or not): every order it left open is cancelled and the Smart Ticket's draft dropped, so the tests
        after it (Pending's, the seeded rows' counts) see the book as seeded."""
        rt = self.server.rt
        with rt.db.reader() as conn:
            first = int(conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM orders").fetchone()[0])

        def tidy():
            with rt.db.reader() as conn:
                ids = [r[0] for r in conn.execute("SELECT order_id FROM orders WHERE rowid > ? AND status NOT IN ('FILLED', 'CANCELLED', 'REJECTED', 'EXPIRED',"
                                                  " 'REPLACED')", (first,))]
            for oid in ids:
                rt.router.handle("DELETE", f"/api/orders/{oid}?book=F", None, {"request_id": f"ui:smoke-tidy-{os.urandom(6).hex()}"}, "127.0.0.1", None)
            self.browser.js(self.sid, "localStorage.removeItem('finsim.smart.draft.v2'); location.hash = '#/dashboard'; true")
            self.browser.pump(0.8)
        self.addCleanup(tidy)

    def _smart_type(self, text: str) -> None:
        """``text`` typed in the box (an input event; not read)."""
        self.browser.js(self.sid, "(function () { var t = document.querySelector('#smart-text'); t.focus(); t.value = %s;"
                                  " t.dispatchEvent(new Event('input', { bubbles: true })); return true; })()" % json.dumps(text))

    def _smart_read(self, text: str, cards: int) -> None:
        """``text`` typed in the box and read with Enter; returns once its reading shows ``cards`` cards."""
        self._smart_type(text)
        self._press("Enter", 13)
        self.browser.wait(self.sid, "!document.querySelector('#smart-parse').disabled && /^read /.test(document.querySelector('#smart-status').innerText)"
                                    " && document.querySelectorAll('#smart-cards [data-i]').length === %d" % cards, timeout=30)

    def _smart_enter(self) -> None:
        """Enter in the box: the text in it read again (the status said, the reading in)."""
        self.browser.js(self.sid, "document.querySelector('#smart-text').focus(); true")
        self._press("Enter", 13)
        self.browser.wait(self.sid, "!document.querySelector('#smart-parse').disabled && /^read /.test(document.querySelector('#smart-status').innerText)", timeout=30)

    def _card_edit(self, i: int, field: str, value: str) -> None:
        """An in-place edit of card ``i``: ``value`` in its ``field`` box, then the change event (as a Tab out of the box)."""
        self.browser.js(self.sid, "(function () { var x = document.querySelector('#smart-cards [data-i=\"%d\"] [data-edit=\"%s\"]'); x.value = %s;"
                                  " x.dispatchEvent(new Event('change', { bubbles: true })); return true; })()" % (i, field, json.dumps(value)))
        self.browser.wait(self.sid, "/edited/.test(document.querySelector('#smart-cards [data-i=\"%d\"] .smart-card-head').innerText)" % i, timeout=10)

    def _open_card(self, i: int, inst: str) -> dict:
        """Open in ticket on card ``i``: the account page with its docked ticket on ``inst``, once the ticket's own preview answered."""
        b, sid = self.browser, self.sid
        b.js(sid, "document.querySelector('#smart-cards [data-i=\"%d\"] [data-act=\"open\"]').click(); true" % i)
        b.wait(sid, "location.hash.indexOf('#/a/F1%%3AUS_EXCHANGE') === 0 && !!document.querySelector('#ticket-instrument')"
                    " && document.querySelector('#ticket-instrument').value === %s" % json.dumps(inst), timeout=30)
        b.wait(sid, "!document.querySelector('#ticket-submit-btn').disabled || !!document.querySelector('#ticket-preview .pv-blocker')", timeout=40)
        return b.js(sid, TICKET_FIELDS)

    def _ticket_left_with_a_limit_and_a_hedge_pick(self) -> str:
        """F1's docked ticket on ACME left as an owner may leave it: BUY 300 LIMIT 55.00 with a hedge candidate picked; returns its id."""
        b, sid = self.browser, self.sid
        self.visit("#/a/F1%3AUS_EXCHANGE/markets")
        b.js(sid, "T.ticket.open({ account_id: %s, instrument_id: 'ACME', side: 'BUY', qty: 300, order_type: 'LIMIT', limit_px: '55.00', hedge: true }); true"
             % json.dumps(USX))
        pick = "Array.prototype.filter.call(document.querySelectorAll('#ticket-hedge .hg-act'), function (x) { return /^pick \\(\\d\\)/.test(x.innerText); })"
        b.wait(sid, pick + ".length > 0", timeout=40)
        b.js(sid, pick + "[0].click(); true")
        b.wait(sid, "document.querySelectorAll('#ticket-hedge .hg-cand.picked').length === 1", timeout=20)
        # the ticket's own preview with the pick answered (Submit waits for it), so nothing of it lands in the next test window
        b.wait(sid, "!document.querySelector('#ticket-submit-btn').disabled || !!document.querySelector('#ticket-preview .pv-blocker')", timeout=40)
        b.pump(0.5)
        t = b.js(sid, TICKET_FIELDS)
        self.assertEqual(("ACME", "BUY", "300", "LIMIT", "55.00", 1), (t["inst"], t["side"], t["qty"], t["type"], t["limit"], t["picked"]))
        return b.js(sid, "document.querySelector('#ticket-hedge .hg-cand.picked').dataset.id")

    def test_x_smart_ticket_fills_standard_tickets_and_sends_nothing_itself(self):
        """1.0.7.2 simplified (the owner's decision of 2026-11-11), on the real server.  With 100 ACME held free in F1, X_TEXT reads as
        four cards in the order written: the shares order (a 2 % trailing stop), the call sold as covered (the calls only: its "sold as
        covered" line ticked) and the call spread's two tickets in their group, the bought call numbered first; #/smart posts nothing but
        the parse, and the card grid fits 1440 px with the dock hidden.  An in-place edit (quantity 12) changes that card only.  Open in
        ticket lands on F1's page with the docked ticket filled exactly - the trail as a percent - as a NEW order: the limit and the hedge
        pick left in that ACME ticket before are not carried (``fresh``).  Nothing is posted to the order route until the ticket's own
        Submit, which sends the card's order under the ticket's own request id (never ``st:``) and nothing else.  Back on #/smart the card
        says "opened in the ticket", no card opens until Enter reads the text again, and then the edit and the mark are kept.  The covered
        call opens with its "sold as covered" line ticked.  The order is cancelled afterwards for the tests after it."""
        b, sid = self.browser, self.sid
        rt = self.server.rt
        self._tidy_after()
        b.js(sid, "localStorage.removeItem('finsim.smart.draft.v2'); true")
        self._served_chain(extra=[("2027-01-15", "P", 55, "2.10", "2.30")])     # a put far enough out for the ticket's hedge candidates
        self._set_acme(100)                                                     # held free: the covered call is the calls only
        start = b.mark()
        self.assertTrue(self._ticket_left_with_a_limit_and_a_hedge_pick())
        # four cards in the order written; the only request #/smart sends is the parse
        since = b.mark()
        self.visit("#/smart", "text")
        self._smart_read(X_TEXT, 4)
        cards = b.js(sid, CARDS)
        self.assertEqual(["#1", "#2", "#3", "#4"], [c["num"] for c in cards])
        self.assertEqual(["BUY", "STO", "BTO", "STO"], [c["side"] for c in cards])
        self.assertEqual([None, None, "C1", "C1"], [c["group"] for c in cards])
        self.assertIn("1 ACME 2026-12-18 60 call", cards[2]["head"])                # the spread's bought call numbered first
        self.assertIn("1 ACME 2026-12-18 65 call", cards[3]["head"])
        self.assertIn("send them in this order: #3, then #4", b.js(sid, "document.querySelector('#smart-cards .smart-group-head').innerText"))
        self.assertEqual([True] * 4, [c["open"] for c in cards])
        self.assertEqual([False, True, False, False], [c["covered"] for c in cards])
        self.assertIn("sold as covered: the ticket places it only while ACME shares held in this account are free", cards[1]["text"])
        self._smart_grid_fits("with four cards")
        self.assertEqual([("POST", "/api/smart-ticket/parse")], self._writes(since))
        # an in-place edit changes that card only (no new reading)
        before = [c["text"] for c in cards]
        self._card_edit(0, "qty", "12")
        cards = b.js(sid, CARDS)
        self.assertIn("BUY 12 ACME", cards[0]["head"])
        self.assertEqual(before[1:], [c["text"] for c in cards[1:]])
        self.assertEqual([("POST", "/api/smart-ticket/parse")], self._writes(since))
        # Open in ticket: the account page's docked ticket, filled exactly as a new order; nothing sent before its own Submit
        since = b.mark()
        t = self._open_card(0, "ACME")
        self.assertEqual({"dock": True, "account": USX, "side": "BUY", "qty": "12", "type": "TRAILING_STOP", "limit": "", "trail_mode": "pct", "trail": "2",
                          "trail_shown": True, "tif": "DAY", "covered_shown": False, "picked": 0, "submit": True, "blockers": []},
                         {k: t[k] for k in ("dock", "account", "side", "qty", "type", "limit", "trail_mode", "trail", "trail_shown", "tif", "covered_shown",
                                            "picked", "submit", "blockers")})
        self.assertTrue(self._posts(since, "/api/orders/preview"))                  # the ticket's own preview
        self.assertEqual([], self._order_posts(since))
        b.js(sid, "document.querySelector('#ticket-submit-btn').click(); true")
        sent = []
        for _ in range(150):
            sent = self._order_posts(since)
            if sent and sent[0]["status"]:
                break
            b.pump(0.2)
        self.assertEqual(1, len(sent), sent)
        body, rid = sent[0]["body"], sent[0]["rid"] or ""
        self.assertEqual(200, sent[0]["status"], sent)
        self.assertEqual({"account_id": USX, "instrument_id": "ACME", "side": "BUY", "qty": "12", "order_type": "TRAILING_STOP", "trail_pct": "2", "tif": "DAY"},
                         {k: str(body.get(k)) for k in ("account_id", "instrument_id", "side", "qty", "order_type", "trail_pct", "tif")})
        self.assertEqual([], [k for k in ("limit_px", "stop_px", "trail_amt", "hedge", "covered", "smart", "package") if k in body])
        self.assertTrue(body.get("preview_id"))
        self.assertRegex(rid, r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
        with rt.db.reader() as conn:
            row = dict(conn.execute("SELECT order_id, status, side, qty, order_type, limit_px FROM orders WHERE request_id=?", (rid,)).fetchone())
        self.assertEqual(("WORKING", "BUY", 12, "TRAILING_STOP", None), (row["status"], row["side"], int(row["qty"]), row["order_type"], row["limit_px"]))
        # back on #/smart: the card says it was opened; nothing opens until Enter reads the text again; then the edit and the mark stay
        b.js(sid, "location.hash = '#/smart'; true")
        b.wait(sid, "document.querySelectorAll('#smart-cards [data-i]').length === 4 && !document.querySelector('#smart-restored').hidden", timeout=20)
        cards = b.js(sid, CARDS)
        self.assertEqual([True, False, False, False], [c["opened"] for c in cards])
        self.assertIn("opened in the ticket at", cards[0]["head"])
        self.assertEqual([False] * 4, [c["open"] for c in cards])
        self.assertIn("restored from this browser: press Enter to read the text again first", cards[0]["why"])
        self._smart_grid_fits("restored")
        self._smart_enter()
        cards = b.js(sid, CARDS)
        self.assertEqual([True] * 4, [c["open"] for c in cards])
        self.assertEqual([True, False, False, False], [c["opened"] for c in cards])
        self.assertIn("BUY 12 ACME", cards[0]["head"])
        # the covered call: its "sold as covered" line ticked in the ticket; not sent
        since = b.mark()
        t = self._open_card(1, CALL_JAN60)
        self.assertEqual({"side": "STO", "qty": "1", "type": "MARKET", "tif": "DAY", "covered": True, "covered_shown": True, "picked": 0, "blockers": []},
                         {k: t[k] for k in ("side", "qty", "type", "tif", "covered", "covered_shown", "picked", "blockers")})
        self.assertIn("sold as covered: FinSim places it only while", t["covered_words"])
        self.assertEqual([], self._order_posts(since))
        self.assertEqual([], b.errors(sid, start))
        self.assertEqual([], b.http_errors(sid, start))

    def test_xb_a_choice_needs_the_text_as_last_read(self):
        """1.0.7.2 simplified (SIMPLIFY_PLAN s3.1 items 4, 7 and 8), on the real server, 100 ACME held.  "buy 1,5k WIDG; sell 100 ACME limit
        70": #1's red flag offers "1.5k" and "15k"; #2 is edited in place (limit 71).  The text edited and not read ("1,6k"): the
        alternatives are disabled, a click puts nothing in the box and reads nothing, and no card opens.  After Enter the alternative
        goes in and the text is read again: #1 becomes BUY 1,600 WIDG while #2 is unchanged, its edit kept.  Once the shares are sold
        elsewhere, a new reading of the same words reads #2 again with no side (nothing held to sell): its edit is dropped and said, and
        what changed shows "was SELL" and "was 71".  #/smart posts nothing but the parse."""
        b, sid = self.browser, self.sid
        self._tidy_after()
        b.js(sid, "localStorage.removeItem('finsim.smart.draft.v2'); true")
        self._set_acme(100)
        start = b.mark()
        self.visit("#/smart", "text")
        self._smart_read("buy 1,5k WIDG; sell 100 ACME limit 70", 2)
        cards = b.js(sid, CARDS)
        self.assertEqual([("", False), ("SELL", True)], [(c["side"], c["open"]) for c in cards])
        self._card_edit(1, "limit_px", "71")
        edited = b.js(sid, CARDS)[1]["text"]
        self.assertIn("edited", edited)
        # the text changed and was not read: nothing is put in, nothing opens
        since = b.mark()
        self._smart_type("buy 1,6k WIDG; sell 100 ACME limit 70")
        b.wait(sid, "!document.querySelector('#smart-dirty').hidden", timeout=10)
        cards = b.js(sid, CARDS)
        self.assertEqual([False, False], [c["open"] for c in cards])
        self.assertIn("the text changed since it was read", cards[1]["why"])
        choices = "document.querySelectorAll('#smart-cards [data-i=\"0\"] [data-act=\"choice\"]')"
        b.js(sid, "document.querySelector('#smart-cards [data-i=\"0\"] .smart-chip.sev-blocker').click(); true")
        b.wait(sid, choices + ".length === 2", timeout=10)
        self.assertEqual([True, True], b.js(sid, "Array.prototype.map.call(%s, function (x) { return x.disabled; })" % choices))
        b.js(sid, choices + "[0].click(); true")
        b.pump(0.6)
        self.assertEqual("buy 1,6k WIDG; sell 100 ACME limit 70", b.js(sid, "document.querySelector('#smart-text').value"))
        self.assertEqual([], self._writes(since))
        # Enter reads it; then the alternative goes in and the text is read again; #2 reads as before and keeps its edit
        self._smart_enter()
        self.assertEqual(edited, b.js(sid, CARDS)[1]["text"])
        b.js(sid, "document.querySelector('#smart-cards [data-i=\"0\"] .smart-chip.sev-blocker').click(); true")
        b.wait(sid, "Array.prototype.some.call(%s, function (x) { return !x.disabled && /^1\\.6k/.test(x.innerText); })" % choices, timeout=10)
        b.js(sid, "Array.prototype.filter.call(%s, function (x) { return /^1\\.6k/.test(x.innerText); })[0].click(); true" % choices)
        b.wait(sid, "document.querySelector('#smart-text').value === 'buy 1.6k WIDG; sell 100 ACME limit 70' && !document.querySelector('#smart-parse').disabled"
                    " && /^read /.test(document.querySelector('#smart-status').innerText)", timeout=30)
        cards = b.js(sid, CARDS)
        self.assertIn("BUY 1,600 WIDG", cards[0]["head"])
        self.assertEqual([True, True], [c["open"] for c in cards])
        self.assertEqual(edited, cards[1]["text"])
        # the shares sold elsewhere: the same words read again give #2 no side; its edit is dropped and said; "was X" marks what changed
        self._set_acme(0)
        self._smart_enter()
        cards = b.js(sid, CARDS)
        self.assertEqual(("", False), (cards[1]["side"], cards[1]["open"]))
        self.assertNotIn("edited", cards[1]["head"])
        for s in ("your edit: limit 71 - dropped, read again as 70", "was SELL", "was 71"):
            self.assertIn(s, cards[1]["text"])
        self.assertNotIn("was", cards[0]["text"])                                   # #1 reads as before
        self.assertEqual({("POST", "/api/smart-ticket/parse")}, set(self._writes(start)))
        b.js(sid, "localStorage.removeItem('finsim.smart.draft.v2'); location.hash = '#/dashboard'; true")
        b.pump(0.8)
        self.assertEqual([], b.errors(sid, start))
        self.assertEqual([], b.http_errors(sid, start))

    def test_xc_a_covered_call_ticket_is_refused_once_its_shares_are_gone(self):
        """1.0.7.2 simplified (owner rule 2, INV-1), on the real server.  "covered call 100 ACME Jan 15 2027 60" read while 100 ACME are
        held free is one card, the call sold as covered.  The shares are sold elsewhere before it is opened: the docked ticket, filled
        with "sold as covered" ticked, shows the server's NOT_COVERED in its own preview and Submit stays disabled; Ctrl+Enter sends
        nothing and nothing is placed."""
        b, sid = self.browser, self.sid
        rt = self.server.rt
        self._tidy_after()
        b.js(sid, "localStorage.removeItem('finsim.smart.draft.v2'); true")
        self._served_chain()
        self._set_acme(100)
        start = b.mark()
        self.visit("#/smart", "text")
        self._smart_read("covered call 100 ACME Jan 15 2027 60", 1)
        self.assertEqual([("#1", "STO", True, True)], [(c["num"], c["side"], c["covered"], c["open"]) for c in b.js(sid, CARDS)])
        self._set_acme(0)                                                       # sold elsewhere (another window, the API) after the reading
        since = b.mark()
        t = self._open_card(0, CALL_JAN60)
        self.assertEqual(("STO", "1", True, True, False), (t["side"], t["qty"], t["covered"], t["covered_shown"], t["submit"]))
        self.assertEqual(["NOT_COVERED"], [x.split(":")[0] for x in t["blockers"]], t["blockers"])
        self.assertIn("100 ACME", t["blockers"][0])
        self._press("Enter", 13, ctrl=True)                                     # the ticket's Submit key: refused while blocked
        b.pump(0.8)
        self.assertEqual([], self._order_posts(since))
        with rt.db.reader() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM orders WHERE instrument_id=?", (CALL_JAN60,)).fetchone()[0])
        b.js(sid, "localStorage.removeItem('finsim.smart.draft.v2'); location.hash = '#/dashboard'; true")
        b.pump(0.8)
        self.assertEqual([], b.errors(sid, start))
        self.assertEqual([], b.http_errors(sid, start))

    def test_xd_a_group_with_a_blocker_and_an_unread_word_open_nothing(self):
        """1.0.7.2 simplified (SIMPLIFY_PLAN s3.1 item 5; s14: ONE_ORDER_ASKED a blocker), on the real server.  "all or none" on a call
        spread asks for one order: its group's red flag keeps every card of it from opening (the group offers the Strategies tab, where
        one all-or-none order is built).  An unread word anywhere in the text ("pronto") keeps EVERY card from opening, a complete one
        too.  A disabled Open in ticket does nothing: the page stays and nothing but the parse is posted."""
        b, sid = self.browser, self.sid
        self._tidy_after()
        b.js(sid, "localStorage.removeItem('finsim.smart.draft.v2'); true")
        self._served_chain()
        start = b.mark()
        self.visit("#/smart", "text")
        self._smart_read("buy 1 ACME Dec 18 2026 60/65 call spread all or none", 2)
        cards = b.js(sid, CARDS)
        self.assertEqual([("BTO", "C1", False), ("STO", "C1", False)], [(c["side"], c["group"], c["open"]) for c in cards])
        for c in cards:
            self.assertIn("'all or none' asks for one order", c["why"])
        self.assertIn("Strategies tab", b.js(sid, "document.querySelector('#smart-cards .smart-group').innerText"))
        self._smart_grid_fits("with a group")
        self._smart_read("buy 10 ACME limit 50 pronto; buy 5 WIDG limit 249", 2)
        cards = b.js(sid, CARDS)
        self.assertEqual([False, False], [c["open"] for c in cards])
        self.assertIn("FinSim could not read the underlined words", cards[1]["why"])
        self.assertEqual(["pronto"], b.js(sid, "Array.prototype.map.call(document.querySelectorAll('#smart-readback .smart-unparsed'), function (u) { return u.textContent; })"))
        b.js(sid, "Array.prototype.forEach.call(document.querySelectorAll('#smart-cards [data-act=\"open\"]'), function (x) { x.click(); }); true")
        b.pump(0.8)
        self.assertEqual("#/smart", b.js(sid, "location.hash"))
        self.assertEqual({("POST", "/api/smart-ticket/parse")}, set(self._writes(start)))
        b.js(sid, "localStorage.removeItem('finsim.smart.draft.v2'); location.hash = '#/dashboard'; true")
        b.pump(0.8)
        self.assertEqual([], b.errors(sid, start))
        self.assertEqual([], b.http_errors(sid, start))

    def test_zy_pending_reads_a_hedge_legs_primary_over_its_replace(self):
        """1.0.7.2 seventh review: Pending's "Cancel selected" after a shares order was re-priced by a replace.  BUY 300 ACME limit 59 with
        its hedge's own legs, BTO 3 Dec 18 2026 55 puts limit 1.00 (as the order ticket sends a shares order and its hedge; 1.0.7.2
        simplified: no CUSTOM package), fills 100 shares on a 1,000-share bar at 58.90 and releases 1 put (resting at 1.00); the
        rest of the shares is re-priced (the put stays with the replaced order).  Ticked alone while the replacement works, the put is said
        kept (the server keeps it); ticked with the replacement - the replacement first - it is listed and sent BEFORE it, so the server
        cancels it after the replacement has ended: both cancelled.  (The 100 shares bought stay; named to run after the Smart Ticket tests.)"""
        b, sid = self.browser, self.sid
        rt = self.server.rt
        grp = rt.instruments.get("ACME").session_group
        body = {"account_id": USX, "instrument_id": "ACME", "side": "BUY", "qty": 300, "order_type": "LIMIT", "limit_px": "59.00", "tif": "DAY",
                "request_id": "ui:smoke7-pkg-0001",
                "hedge": {"legs": [{"instrument_id": occ_symbol("ACME", date(2026, 12, 18), "P", Decimal("55")), "side": "BTO", "qty": 3,
                                    "order_type": "LIMIT", "limit_px": "1.00"}]}}
        self._served_chain()
        st_, out, _h = rt.router.handle("POST", "/api/orders?book=F", None, body, "127.0.0.1", None)
        self.assertEqual(200, st_, out)
        now = int(rt.clock.epoch())
        t0 = epoch_of(rt.clock.today(), "13:58")
        px = qprice("58.90")
        bar1 = Bar1m(t0, px, px, px, px, 1000)
        rt.cache.put(QuoteRecord(instrument_id="ACME", provider="yahoo", source="Yahoo Finance chart (ACME)", method="direct", last=px, quote_time=t0 + 60,
                                 fetched_at=now, delay_s=900, delay_label="up to 15 min delayed", currency="USD", session_group=grp, bar=bar1, bars_tail=[bar1]), now)
        rt.matcher.on_quotes(["ACME"])
        with rt.db.reader() as conn:
            a = dict(conn.execute("SELECT order_id, status, filled_qty FROM orders WHERE request_id=?", ("ui:smoke7-pkg-0001",)).fetchone())
            put = dict(conn.execute("SELECT order_id, status, qty FROM orders WHERE request_id=?", ("ui:smoke7-pkg-0001:H1",)).fetchone())
        self.assertEqual(("PARTIAL", 100, "WORKING", 1), (a["status"], int(a["filled_qty"]), put["status"], int(put["qty"])))
        st_, out, _h = rt.router.handle("POST", f"/api/orders/{a['order_id']}/replace?book=F", None, {"limit_px": "59.00", "request_id": "ui:smoke7-rp-0001"},
                                        "127.0.0.1", None)
        self.assertEqual(200, st_, out)
        a2, h = out["order"]["order_id"], put["order_id"]
        b.js(sid, "location.hash = '#/pending?status=open&r=' + Date.now(); true")
        b.wait(sid, " && ".join("Array.prototype.some.call(document.querySelectorAll('#page tr[data-i]'), function (tr) { return tr.innerText.indexOf(%s) >= 0"
                                " && !!tr.querySelector('input[data-act=\"sel\"]'); })" % json.dumps(i) for i in (a2, h)), timeout=45)

        def tick(oid: str, other: str) -> str:
            return b.js(sid, """(function () {
              var rows = Array.prototype.filter.call(document.querySelectorAll('#page tr[data-i]'), function (tr) {
                return tr.innerText.indexOf(%s) >= 0 && tr.innerText.indexOf(%s) < 0 && !!tr.querySelector('input[data-act="sel"]'); });
              if (rows.length !== 1) { return 'rows ' + rows.length; }
              rows[0].querySelector('input[data-act="sel"]').click(); return document.querySelector('#pending-cancel-selected').textContent;
            })()""" % (json.dumps(oid), json.dumps(other)))

        def listed() -> list:
            items = b.js(sid, "Array.prototype.map.call(document.querySelectorAll('#modal-root .modal li'), function (li) { return li.innerText; })") or []
            return [i for t in items for i in (a2, h) if t.startswith(i + ":")]
        since = b.mark()
        # the put alone: said kept while the replacement works
        self.assertEqual("Cancel selected (1)", tick(h, "\u0000"))
        b.js(sid, "document.querySelector('#pending-cancel-selected').click(); true")
        b.wait(sid, "!!document.querySelector('#modal-root .modal button.btn.primary')", timeout=30)
        conf = b.js(sid, "document.querySelector('#modal-root .modal').innerText")
        self.assertIn(f"kept while its primary {a2} keeps working", conf)
        b.js(sid, "Array.prototype.filter.call(document.querySelectorAll('#modal-root .modal button'), function (x) { return x.textContent.trim() === 'Keep'; })[0].click();"
                  " true")
        b.pump(0.5)
        # the replacement ticked too (after the put was ticked first, then unticked and ticked again after it): the put goes before it
        self.assertEqual("Cancel selected (0)", tick(h, "\u0000"))
        self.assertEqual("Cancel selected (1)", tick(a2, h))
        self.assertEqual("Cancel selected (2)", tick(h, "\u0000"))
        b.js(sid, "document.querySelector('#pending-cancel-selected').click(); true")
        b.wait(sid, "!!document.querySelector('#modal-root .modal button.btn.primary')", timeout=30)
        self.assertEqual([h, a2], listed())
        b.js(sid, "document.querySelector('#modal-root .modal button.btn.primary').click(); true")
        b.wait(sid, "/Cancel selected: cancelled 2/.test(document.querySelector('#page').innerText)", timeout=30)
        self.assertEqual([[h, a2]], [x["body"]["order_ids"] for x in self._posts(since, "/api/orders/cancel")])
        with rt.db.reader() as conn:
            got = {r["order_id"]: r["status"] for r in conn.execute("SELECT order_id, status FROM orders WHERE order_id IN (?,?)", (a2, h))}
        self.assertEqual({a2: "CANCELLED", h: "CANCELLED"}, got)
        self.assertEqual([], b.errors(sid, since))
        self.assertEqual([], b.http_errors(sid, since))

    def test_zz_pending_cancel_selected(self):
        """1.0.7.2 mass cancel on Pending Trades (named to run after every other test: it cancels the seeded working SPY order).  The
        order's box is ticked; "Cancel selected (1)" lists it in words (side, quantity, instrument, account, book) and, once confirmed,
        sends it as ONE cancel batch of the Fund Book (request id mc:<12 hex>); the answer is said on the page and the order leaves the open
        filter.  0 console errors, no HTTP response >= 400."""
        b, sid = self.browser, self.sid
        since = b.mark()
        working = self.seeded["working"]
        self.visit("#/pending?status=open")
        self.assertTrue(b.js(sid, "document.querySelector('#pending-cancel-selected').disabled"))         # nothing ticked yet
        self.assertIn("Cancel all open in Fund Book", b.js(sid, "document.querySelector('#pending-cancel-all').innerText"))
        ticked = b.js(sid, "(function () { var trs = document.querySelectorAll('#page .grid table tbody tr[data-i]'); for (var i = 0; i < trs.length; i++) {"
                           " if (trs[i].innerText.indexOf(%s) >= 0) { var x = trs[i].querySelector('input[data-act=\"sel\"]'); if (x) { x.click(); return true; } } } return false; })()"
                      % json.dumps(working))
        self.assertTrue(ticked, "the working order has no selection box")
        b.wait(sid, "document.querySelector('#pending-cancel-selected').innerText === 'Cancel selected (1)' && !document.querySelector('#pending-cancel-selected').disabled",
               timeout=10)
        b.js(sid, "document.querySelector('#pending-cancel-selected').click(); true")
        b.wait(sid, "!!document.querySelector('#modal-root .modal')", timeout=10)
        listed = b.js(sid, "document.querySelector('#modal-root .modal').innerText")
        for s in (working, "BUY", "50", "SPY", "US Exchange", "Fund Book"):
            self.assertIn(s, listed)
        self.assertEqual([], self._posts(since, "/api/orders/cancel"))                     # nothing before the confirm
        b.js(sid, "Array.prototype.filter.call(document.querySelectorAll('#modal-root .modal .btn.primary'), function (x) { return x.innerText === 'Cancel 1 order'; })[0].click(); true")
        b.wait(sid, "/Cancel selected: cancelled 1/.test(document.querySelector('#page').innerText)", timeout=20)
        b.wait(sid, "document.querySelector('#page .grid').innerText.indexOf(%s) < 0" % json.dumps(working), timeout=20)
        batches = self._posts(since, "/api/orders/cancel")
        self.assertEqual(1, len(batches), batches)
        self.assertRegex(batches[0]["rid"], r"^mc:[0-9a-f]{12}$")
        self.assertEqual(("book=F", [working], 200), (batches[0]["query"], batches[0]["body"]["order_ids"], batches[0]["status"]))
        self.assertEqual("Cancel selected (0)", b.js(sid, "document.querySelector('#pending-cancel-selected').innerText"))
        self.assertEqual([], b.errors(sid, since))
        self.assertEqual([], b.http_errors(sid, since))
        text = _clean(b.js(sid, "document.body.innerText"))
        for rx in BAD_WORDS:
            self.assertIsNone(rx.search(text), f"#/pending: {rx.pattern!r} on screen")


if __name__ == "__main__":
    unittest.main()
