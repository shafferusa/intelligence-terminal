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
Ticket; and, last (``test_z_...``), the Smart Ticket's whole flow: nothing posted before its confirm, one order at a time after it, the
first refusal stops the run.

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
#: three legs: a small resting limit, one above 25 % of the Fund Book's NAV (30,000 x 250.00 = 7,500,000.00 > 25 % of about 25,000,000.00:
#: the large-order confirm), another small limit; ";" always ends a clause
SMART_TEXT = "buy 10 ACME limit 50; buy 30000 WIDG limit 250; buy 5 ACME limit 49"


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

    # ---- the Smart Ticket (1.0.7.2, r1072 CHANGE_PLAN 5.7) -----------------------------------------------------------------------
    def _press(self, key: str, vk: int, ctrl: bool = False, shift: bool = False) -> None:
        """One key press with Ctrl and / or Shift (Browser.key knows Shift only)."""
        base = {"key": key, "code": key, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk, "modifiers": (2 if ctrl else 0) | (8 if shift else 0)}
        for kind in ("keyDown", "keyUp"):
            self.browser.call("Input.dispatchKeyEvent", dict(base, type=kind), session=self.sid)

    def _smart_grid_fits(self, when: str) -> None:
        """Plan rule 5 (new tables fit 1440 px): the leg grid never scrolls sideways (the dock is hidden on #/smart)."""
        m = self.browser.js(self.sid, "(function () { var s = document.querySelector('#smart-grid .scroll'); return s ? { client: s.clientWidth, scroll: s.scrollWidth } : null; })()")
        self.assertIsNotNone(m, when)
        self.assertLessEqual(m["scroll"], m["client"], f"#/smart {when}: the leg grid scrolls sideways at 1440 px ({m})")

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

    def test_y_smart_ticket_records_a_sequential_package_as_sent_only_with_its_terms(self):
        """1.0.7.2 third review: the page records an item as sent under its own request id (a reload or a second window got there first)
        only when the orders under that id have the item's terms - a sequential package's option legs included: their type, limit and
        size (through the ratio stored at 8 decimals, which a release never changes), so another call quantity under the same id is
        another order, never "already submitted"."""
        b, sid = self.browser, self.sid
        b.wait(sid, "!!(window.T && T.pages && T.pages.smart && T.pages.smart.sameTerms)", timeout=30)
        got = b.js(sid, """(function () {
          var rid = 'st:a1b2c3d4e5f6:C1:r2', acct = 'F1:US_EXCHANGE', call = 'ACME261218C00065000';
          var req = { account_id: acct, tif: 'DAY', package: { strategy_key: 'CUSTOM', legs: [
            { instrument_id: 'ACME', side: 'BUY', qty: 200, order_type: 'LIMIT', limit_px: '50' },
            { instrument_id: call, side: 'STO', qty: 3, order_type: 'MARKET' }] } };
          function orders(calls, status, extra) {
            return [Object.assign({ request_id: rid + ':H1', account_id: acct, instrument_id: call, side: 'STO', qty: status === 'WAITING_PARENT' ? calls : 1,
                                    order_type: 'MARKET', limit_px: null, status: status, ratio: (calls / 200).toFixed(8), tif: 'DAY' }, extra || {}),
                    { request_id: rid, account_id: acct, instrument_id: 'ACME', side: 'BUY', qty: 200, order_type: 'LIMIT', limit_px: '50.00', status: 'PARTIAL', tif: 'DAY' }];
          }
          var f = T.pages.smart.sameTerms;
          return [f(req, orders(3, 'WAITING_PARENT'), rid), f(req, orders(2, 'WAITING_PARENT'), rid), f(req, orders(3, 'WORKING'), rid),
                  f(req, orders(2, 'WORKING'), rid), f(req, orders(3, 'WAITING_PARENT', { order_type: 'LIMIT', limit_px: '1.00' }), rid)];
        })()""")
        # the same package (waiting, or 1 call released); 2 calls under the id (waiting or released); a limit on the call that was not sent
        self.assertEqual([True, False, True, False, False], got)

    def test_z_smart_ticket_sends_only_after_the_confirm(self):
        """The Smart Ticket on the real server (named to run last: it leaves a working order and a draft behind).  A three-leg instruction
        (fictional ACME / WIDG, read on Enter) renders three legs with their flags; nothing is posted to /api/orders before the confirm -
        Ctrl+Enter (the docked ticket's submit key) sends nothing here, Ctrl+Shift+Enter opens the confirm with Back focused; after "Send 3
        orders" leg 1 goes with its st: request id and smart record, leg 2 (7,500,000.00, above 25 % of the Fund Book's NAV) answers 409
        CONFIRM_REQUIRED and the run waits for the owner, who stops it: leg 3 is never sent.  0 console errors (the browser's own line for
        that 409 aside), no HTTP response >= 400 but that 409, no undefined / NaN / [object Object] on screen."""
        b, sid = self.browser, self.sid
        since = b.mark()
        state = self.visit("#/smart", "text")
        self.assertEqual([], state["notbuilt"])
        self.assertTrue(b.js(sid, "T.hasModule('smart_ticket')"), "the Smart Ticket's reader (smart_ticket) is not bound in this build")
        b.js(sid, "(function () { var t = document.querySelector('#smart-text'); t.focus(); t.value = %s; t.dispatchEvent(new Event('input', { bubbles: true })); return true; })()"
             % json.dumps(SMART_TEXT))
        self._press("Enter", 13)
        b.wait(sid, "document.querySelectorAll('#smart-grid tbody tr[data-i]').length === 3", timeout=20)
        rows = b.js(sid, "Array.prototype.map.call(document.querySelectorAll('#smart-grid tbody tr[data-i]'), function (tr) {"
                         " return { text: tr.innerText, chips: tr.querySelectorAll('.smart-chip').length }; })")
        self.assertEqual(3, len(rows))
        for i, (row, sym) in enumerate(zip(rows, ("ACME", "WIDG", "ACME"))):
            self.assertIn(sym, row["text"], f"leg {i + 1}")
            self.assertGreater(row["chips"], 0, f"leg {i + 1} shows no flag")
        # every leg previewed without a blocker: Submit selected opens
        b.wait(sid, "!document.querySelector('#smart-submit').disabled", timeout=40)
        self.assertEqual([], self._order_posts(since))
        self._smart_grid_fits("staged")
        shots = os.environ.get("FINSIM_SMOKE_SHOTS")
        if shots:
            b.screenshot(sid, os.path.join(shots, "smart_staged.png"))
        # Ctrl+Enter is the docked ticket's submit: the dock never shows on #/smart, so it sends nothing and opens nothing
        self._press("Enter", 13, ctrl=True)
        b.pump(0.8)
        self.assertEqual(0, b.js(sid, "T.ui.dialogs()"))
        self.assertFalse(b.js(sid, "T.dock.visible()"))
        # Ctrl+Shift+Enter opens the confirm, every order in words with its book; Back is focused; still nothing posted
        self._press("Enter", 13, ctrl=True, shift=True)
        b.wait(sid, "!!document.querySelector('#smart-send')", timeout=10)
        conf = b.js(sid, "(function () { var m = document.querySelector('#modal-root .modal'); return { text: m.innerText, focus: document.activeElement && document.activeElement.id,"
                         " send: document.querySelector('#smart-send').innerText }; })()")
        self.assertEqual("smart-back", conf["focus"])
        self.assertEqual("Send 3 orders", conf["send"])
        for s in ("L1", "L2", "L3", "ACME", "WIDG", "Fund"):
            self.assertIn(s, conf["text"])
        self.assertIn("a large order", conf["text"])                                # leg 2's preview says it will ask again
        b.pump(0.3)
        self.assertEqual([], self._order_posts(since))
        # Send 3 orders: one at a time; leg 2 is refused 409 CONFIRM_REQUIRED and the run waits for the owner's click
        if shots:
            b.screenshot(sid, os.path.join(shots, "smart_confirm.png"))
        b.js(sid, "document.querySelector('#smart-send').click(); true")
        b.wait(sid, "!!document.querySelector('#smart-confirm-large')", timeout=40)
        if shots:
            b.screenshot(sid, os.path.join(shots, "smart_large_order.png"))
        ticket = b.js(sid, "JSON.parse(localStorage.getItem('finsim.smart.draft')).ticket")
        self.assertRegex(ticket, r"^[0-9a-f]{12}$")
        posts = self._order_posts(since)
        self.assertEqual([(f"st:{ticket}:L1:r1", 200), (f"st:{ticket}:L2:r1", 409)], [(p["rid"], p["status"]) for p in posts])
        self.assertEqual((ticket, "L1"), (posts[0]["body"]["smart"]["ticket"], posts[0]["body"]["smart"]["item"]))
        self.assertIn("ACME", posts[0]["body"]["smart"]["clause"])
        self.assertEqual(("ACME", 10, "LIMIT"), (posts[0]["body"]["instrument_id"], posts[0]["body"]["qty"], posts[0]["body"]["order_type"]))
        self.assertTrue(posts[0]["body"].get("preview_id"))                            # the preview the confirm listed (second review)
        self.assertEqual(("WIDG", 30000, False), (posts[1]["body"]["instrument_id"], posts[1]["body"]["qty"], posts[1]["body"].get("confirm_large")))
        # Stop: the run ends at leg 2; leg 3 is never sent
        self.assertEqual("smart-stop", b.js(sid, "document.activeElement && document.activeElement.id"))
        b.js(sid, "document.querySelector('#smart-stop').click(); true")
        b.wait(sid, "!document.querySelector('#smart-stop') && /stopped/.test(document.querySelector('#smart-status').innerText)", timeout=15)
        b.pump(1.0)
        self.assertEqual([f"st:{ticket}:L1:r1", f"st:{ticket}:L2:r1"], [p["rid"] for p in self._order_posts(since)])
        res = b.js(sid, "Array.prototype.map.call(document.querySelectorAll('#smart-grid tbody tr[data-i]'), function (tr) { return tr.lastElementChild.innerText; })")
        self.assertRegex(res[0].lower(), r"\S+ working")                             # leg 1: its order, resting at its limit (the chip is upper case on screen)
        self.assertIn("not sent", res[1])
        self.assertNotIn("working", res[2].lower())
        self._smart_grid_fits("after the run")
        # "Cancel what this ticket sent": its one open order (leg 1, found by the ticket's request-id prefix) is listed, confirmed and
        # cancelled in one batch of the Fund Book (request id mc:<12 hex>)
        leg1 = res[0].split()[0]
        b.js(sid, "document.querySelector('#smart-cancel-sent').click(); true")
        b.wait(sid, "Array.prototype.some.call(document.querySelectorAll('#modal-root .modal .btn.primary'), function (x) { return x.innerText === 'Cancel 1 order'; })",
               timeout=15)
        listed = b.js(sid, "document.querySelector('#modal-root .modal').innerText")
        self.assertIn(leg1, listed)
        self.assertIn("ACME", listed)
        b.js(sid, "Array.prototype.filter.call(document.querySelectorAll('#modal-root .modal .btn.primary'), function (x) { return x.innerText === 'Cancel 1 order'; })[0].click(); true")
        b.wait(sid, "/^cancelled 1/.test(document.querySelector('#smart-status').innerText)", timeout=20)
        batches = self._posts(since, "/api/orders/cancel")
        self.assertEqual(1, len(batches), batches)
        self.assertRegex(batches[0]["rid"], r"^mc:[0-9a-f]{12}$")
        self.assertEqual(("book=F", [leg1], 200), (batches[0]["query"], batches[0]["body"]["order_ids"], batches[0]["status"]))
        self.assertEqual([f"st:{ticket}:L1:r1", f"st:{ticket}:L2:r1"], [p["rid"] for p in self._order_posts(since)])   # nothing else was sent
        # the record: no console error but the browser's own line for that 409; no other HTTP >= 400; nothing unrendered on screen
        self.assertEqual([], [e for e in b.errors(sid, since) if not ("409" in e and "/api/orders" in e)])
        bad = b.http_errors(sid, since)
        self.assertEqual(1, len(bad), bad)
        self.assertTrue(bad[0].startswith("409 ") and "/api/orders?" in bad[0], bad)
        text = _clean(b.js(sid, "document.body.innerText"))
        for rx in BAD_WORDS:
            self.assertIsNone(rx.search(text), f"#/smart: {rx.pattern!r} on screen")
        if shots:
            b.screenshot(sid, os.path.join(shots, "smart_stopped.png"))
        # 1.0.7.2 second review: the POST carries the preview the CONFIRM listed, so a price that moves against the owner while the confirm
        # is open comes back 409 PREVIEW_STALE and asks again (Accept & continue / Skip / Stop) - it is never sent at the moved price
        # unasked.  "buy 10 WIDG" at market: the confirm lists gross 2,500.00 (10 x 250.00); WIDG then prints 257.50 (3 % against the buy;
        # the server's guard is 0.5 %); "Send 1 order" -> the moved-price dialog; Stop -> nothing placed.
        rt, fp = self.server.rt, self.server.fp
        now = int(rt.clock.epoch())

        def widg(last: str, age: int) -> None:
            px = qprice(last)
            bar = Bar1m(now - 60, px, px, px, px, 61900)
            fp.set("WIDG", last=last, quote_time=now - age, bar=(now - 60, 61900))
            rt.cache.put(QuoteRecord(instrument_id="WIDG", provider="yahoo", source="Yahoo Finance chart (WIDG)", method="direct", last=px, quote_time=now - age,
                                     fetched_at=now, delay_s=900, delay_label="up to 15 min delayed", currency="USD", session_group="US_EQUITY", bar=bar,
                                     bars_tail=[bar]), now)
        widg("250.00", 30)                                                         # a live print: a market order fills now
        since = b.mark()
        b.js(sid, "(function () { var t = document.querySelector('#smart-text'); t.focus(); t.value = 'buy 10 WIDG'; t.dispatchEvent(new Event('input', { bubbles: true }));"
                  " return true; })()")
        self._press("Enter", 13)
        b.wait(sid, "document.querySelectorAll('#smart-grid tbody tr[data-i]').length === 1 && /fills now 10/.test(document.querySelector('#smart-grid').innerText)",
               timeout=40)
        b.wait(sid, "!document.querySelector('#smart-submit').disabled", timeout=40)
        self._press("Enter", 13, ctrl=True, shift=True)
        b.wait(sid, "!!document.querySelector('#smart-send')", timeout=10)
        conf = b.js(sid, "document.querySelector('#modal-root .modal').innerText")
        self.assertIn("2,500.00", conf)
        self.assertIn("a moved price or a large order asks you again", conf)
        previews = [x for x in self._posts(since, "/api/orders/preview") if x["body"].get("instrument_id") == "WIDG"]
        self.assertTrue(previews, "no preview of the WIDG order was taken before the confirm")
        widg("257.50", 10)
        b.js(sid, "document.querySelector('#smart-send').click(); true")
        b.wait(sid, "!!document.querySelector('#smart-accept')", timeout=40)
        sent = self._order_posts(since)
        self.assertEqual([(f"st:{ticket}:L1:r2", 409)], [(x["rid"], x["status"]) for x in sent])     # the item's revision 2 (leg 1 read again)
        self.assertTrue(sent[0]["body"].get("preview_id"))
        # third review: the dialog shows the server's figures at 257.50 - a STORED preview, whose id "Accept & continue" sends - so a price
        # that moves again while the dialog is open (270.00: 4.85 % more against the buy) comes back stale and asks again
        self.assertIn("2,575.00", b.js(sid, "document.querySelector('#modal-root .modal').innerText"))
        widg("270.00", 5)
        b.js(sid, "document.querySelector('#smart-accept').click(); true")
        b.wait(sid, "!!document.querySelector('#smart-accept') && /moved/.test(document.querySelector('#modal-root .modal').innerText)", timeout=40)
        for _ in range(100):                                                      # the second answer is in (the dialog asks again)
            sent = self._order_posts(since)
            if len(sent) == 2 and sent[1]["status"]:
                break
            b.pump(0.2)
        self.assertEqual([(f"st:{ticket}:L1:r2", 409)] * 2, [(x["rid"], x["status"]) for x in sent])
        self.assertTrue(sent[1]["body"].get("preview_id"))
        self.assertNotEqual(sent[0]["body"]["preview_id"], sent[1]["body"]["preview_id"])          # the preview the dialog showed
        b.js(sid, "document.querySelector('#smart-stop').click(); true")
        b.wait(sid, "!document.querySelector('#smart-stop') && /stopped/.test(document.querySelector('#smart-status').innerText)", timeout=15)
        b.pump(0.5)
        self.assertEqual([(f"st:{ticket}:L1:r2", 409)] * 2, [(x["rid"], x["status"]) for x in self._order_posts(since)])
        with rt.db.reader() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM orders WHERE request_id=?", (f"st:{ticket}:L1:r2",)).fetchone()[0])
        self.assertEqual([], [e for e in b.errors(sid, since) if not ("409" in e and "/api/orders" in e)])

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
