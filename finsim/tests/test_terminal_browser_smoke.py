"""FINSIM1 P8 task 2 (TEST-06): the browser smoke test - the real pages in a real headless browser (owner directive section 18:
"Verify the actual interface in a browser. Do not claim completion based only on backend tests").

ONE temp home with the Fund Book (F1, 25,000,000.00).  It is seeded through the HTTP routes the UI uses on a ``build_runtime`` with a
FakeClock (Tue 2026-10-20 14:00 ET) and a FakeProvider (no network):
  - a filled market BUY of 100 AAPL in US Exchange (a Completed Trades row, settling T+1, with its Treasury sweep);
  - a resting LIMIT BUY of 50 SPY at 700.00, far below the 769.32 print (an open Pending Trades row);
  - a LIMIT BUY of 10 MSFT at 400.00, then cancelled (the Cancelled filter);
  - a filled market BUY of 1 GCZ26 (a futures position for the Derivatives > Futures tab);
  - a refused SELL of 100 MSFT the account does not hold (the Rejected filter: the order ticket's refusal is recorded).
The runtime is closed and ``serve(offline=True)`` serves the same database on port 0 with the same FakeProvider (its prints) and a
FakeClock at the same instant.  A headless Edge / Chrome (``terminal_cdp``, standard library CDP) opens every main route of the
directive's navigation at 1440 x 900: Dashboard, Portfolio, Trading, Pending Trades (each status filter), Completed Trades (an
execution drawer, every tab), Accounts, Cash & Collateral, Derivatives, History, Settings, Risk, Accounting and Activity.

For each route it asserts:
  - 0 console errors, uncaught exceptions or browser log errors, and 0 HTTP responses >= 400 (and no failed request);
  - a rendered main table or tiles in ``#page``, and no NOT YET BUILT panel (every page of 1.0.6 is in the build);
  - none of the removed news feature's strings (P1's ``NEWS_STRINGS``, with P1's allowlist), no "AI Research", and no "undefined",
    "NaN" or "[object Object]" on screen (plan section 3 rule 5).

Skipped, with that reason, where no Chromium-family browser exists (``terminal_cdp.find_browser``).  Set FINSIM_SMOKE_SHOTS to a folder
to keep a PNG of every route.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
import unittest

from tests.terminal_cdp import NO_BROWSER, Browser, find_browser
from tests.terminal_helpers import _Log
from tests.test_terminal_static import NEWS_STRINGS
from finsim.terminal import config, server as _server
from finsim.terminal.clock import FakeClock, epoch_of
from finsim.terminal.quotes.providers import FakeProvider

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
          ("#/accounting", "table"), ("#/activity", "table")]

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


def _seed(home: str, clock: FakeClock, fp: FakeProvider) -> dict:
    """The Fund Book's few rows, booked through the routes the UI uses on a runtime that is then closed; returns the ids."""
    rt = _server.build_runtime(home, os.path.join(home, "terminal.db"), clock=clock, offline=True, fake_provider=fp, log=_Log(), books=[FUND],
                               fetch_threads=0)
    try:
        qt = epoch_of(clock.today(), "13:58")
        for iid, last in PRINTS.items():
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


if __name__ == "__main__":
    unittest.main()
