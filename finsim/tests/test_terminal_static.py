"""WP-12 static checks (TERMINAL_WORKPLAN.md WP-12 tests; TERMINAL_DESIGN.md s17).

- every JS / CSS file index.html references exists (or is an OPTIONAL component of another package), is ASCII, and no
  script or stylesheet is loaded from a CDN (no http(s):// in src/href except the legacy link);
- every `:root` token of finsim/finsim/static/style.css (and the light theme) is present in terminal.css with the SAME
  value, plus the s17.2 aliases;
- index.html carries the mount roots the scripts expect (#bookswitcher, #history, ...) and no #news root (news was removed in 1.0.5);
- the shell of FINSIM1_PLAN C1 / 15.7 / P1b: the routes (the Dashboard is the default and the fallback, #/news redirects, the new
  pages, the old Settings > Data Status address), the 74-action key registry with the 1.0.6 default map (Q10) and its hiding rule,
  the optional pages listed in index.html, api.py and the fixture server alike, the sidebar in the directive's order with its keys,
  the meta description, no news string left in the shell's files;
- the fixture server serves every s15 route with its fixture (WP-13's fixtures may be absent -> reported as skipped),
  substitutes the time placeholders, honours scenarios, serves the static UI with no-store and stubs optional files;
- no glossary (owner amendment 18:28); every page / component registers itself; brackets balance in every JS file
  (a heuristic tokenizer: strings, comments and regex literals are skipped).
Runs on py -3.12 and py -3.14; binds 127.0.0.1:0 only; touches no network and no ~/.finsim.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import sys
import time
import unittest
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from tests import terminal_fixture_server as fx  # noqa: E402

STATIC = os.path.join(ROOT, "finsim", "terminal", "static")
OLD_CSS = os.path.join(ROOT, "finsim", "static", "style.css")
NEW_CSS = os.path.join(STATIC, "terminal.css")
INDEX = os.path.join(STATIC, "index.html")
ALIASES = {"--panel": "var(--bg2)", "--panel-2": "var(--bg3)", "--text": "var(--fg)", "--muted": "var(--fg2)",
           "--up": "var(--green)", "--down": "var(--red)", "--warn": "var(--accent)", "--stale": "var(--purple)"}
REQUIRED_IDS = ["app", "header", "banner", "attention", "sidebar", "bookswitcher", "nav", "main", "page", "history",
                "dock", "dock-body", "modal-root", "toast-root", "help", "clock", "session-chips", "acct-card", "integrity-dot"]
PAGES = ["markets", "instrument", "positions", "orders", "cash", "transactions", "treasury", "portfolio", "accounting", "risk",
         "activity", "watchlists", "settings", "history", "dashboard"]
COMPONENTS = ["sidebar", "header", "attention", "freshness", "grid", "chart", "chain", "bookswitcher"]
QUALITIES = ["live", "delayed", "close", "computed", "private", "stale", "unavailable"]
# BUILD2_PLAN C3 / C11: the product plug-ins of Build 2 - loaded by index.html as optional scripts and stubbed by the fixture
# server (and by api.py on the real server) until their packages build them, so no 404 reaches the console
PLANNED_OPTIONAL = ["pages/markets_debt.js", "components/bondticket.js", "pages/markets_fx.js", "components/fxdesk.js",
                    "pages/markets_credit.js", "components/creditdesk.js", "pages/markets_otc.js", "components/otcticket.js",
                    # FINSIM1_PLAN C1: the pages of the new navigation and the import panel (NOT YET BUILT until their packages land)
                    "pages/pending.js", "pages/completed.js", "pages/accounts.js", "pages/cash_collateral.js", "pages/derivatives.js",
                    "components/imports.js"]
#: the strings of the removed news feature that no page of the shell may show (P1 acceptance 3; the allowlist is not news)
NEWS_STRINGS = ["Daily News", "Newspaper", "briefing", "headlines", "news feed", "/api/news"]


def read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def css_block(text: str, selector: str) -> dict:
    """{token: value} of the FIRST `selector { ... }` block whose selector matches exactly."""
    i = text.find(selector + " {")
    if i < 0:
        i = text.find(selector + "{")
    assert i >= 0, "no block for " + selector
    j = text.find("}", i)
    body = text[text.find("{", i) + 1:j]
    out = {}
    for decl in body.split(";"):
        if ":" in decl:
            k, v = decl.split(":", 1)
            out[k.strip()] = " ".join(v.split())
    return out


class _Refs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts, self.links, self.ids, self.optional = [], [], set(), set()
        self.text = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id"):
            self.ids.add(a["id"])
        if tag == "script" and a.get("src"):
            self.scripts.append(a["src"])
            if a.get("data-optional"):
                self.optional.add(a["src"])
        if tag == "link" and a.get("href"):
            self.links.append((a.get("rel"), a["href"]))
        if tag == "a" and a.get("href"):
            self.links.append(("a", a["href"]))

    def handle_data(self, data):
        self.text.append(data)


def js_balance(src: str):
    """Depth of (), [], {} after skipping strings, comments and regex literals; returns (paren, bracket, brace)."""
    i, n = 0, len(src)
    depth = {"(": 0, "[": 0, "{": 0}
    close = {")": "(", "]": "[", "}": "{"}
    last_sig = ""                                              # last significant char (for the regex heuristic)
    last_word = ""
    while i < n:
        c = src[i]
        if c in " \t\r\n":
            i += 1
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j + 1
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c in "'\"`":
            q = c
            i += 1
            while i < n and src[i] != q:
                if src[i] == "\\":
                    i += 1
                i += 1
            i += 1
            last_sig = "s"
            continue
        if c == "/":
            regex_ok = last_sig in ("", "(", ",", "=", ":", "[", "!", "&", "|", "?", "{", "}", ";", "+", "-", "*", "<", ">", "~", "^", "%") or last_word in ("return", "typeof", "case", "in", "of")
            if regex_ok:
                i += 1
                in_class = False
                while i < n:
                    ch = src[i]
                    if ch == "\\":
                        i += 2
                        continue
                    if ch == "[":
                        in_class = True
                    elif ch == "]":
                        in_class = False
                    elif ch == "/" and not in_class:
                        break
                    elif ch == "\n":
                        break
                    i += 1
                i += 1
                while i < n and src[i].isalpha():
                    i += 1
                last_sig = "r"
                continue
        if c in depth:
            depth[c] += 1
        elif c in close:
            depth[close[c]] -= 1
            if depth[close[c]] < 0:
                return (-1, -1, -1)
        if c.isalnum() or c in "_$":
            j = i
            while j < n and (src[j].isalnum() or src[j] in "_$"):
                j += 1
            last_word = src[i:j]
            last_sig = "w"
            i = j
            continue
        last_sig = c
        last_word = ""
        i += 1
    return (depth["("], depth["["], depth["{"])


class StaticFilesTest(unittest.TestCase):
    def test_referenced_files_exist_ascii_no_cdn(self):
        p = _Refs()
        p.feed(read(INDEX))
        self.assertTrue(p.scripts, "index.html loads no scripts")
        for src in p.scripts:
            self.assertTrue(src.startswith("/static/"), "script not under /static/: " + src)
            rel = src[len("/static/"):]
            exists = os.path.isfile(os.path.join(STATIC, rel))
            if src in p.optional:
                self.assertIn(rel, fx.OPTIONAL_STATIC, "optional script not in the fixture server's optional list: " + rel)
            else:
                self.assertTrue(exists, "referenced script missing: " + rel)
            self.assertFalse(src.startswith("http"), "CDN script: " + src)
        for rel in PLANNED_OPTIONAL:
            self.assertIn("/static/" + rel, p.optional, "index.html does not load the planned plug-in " + rel + " (data-optional)")
            self.assertIn(rel, fx.OPTIONAL_STATIC, "the fixture server does not stub the planned plug-in " + rel)
        self.assertNotIn("8766", read(INDEX), "the sidebar links the legacy simulation (SHELL-20)")
        for rel_kind, href in p.links:
            if rel_kind == "stylesheet":
                self.assertEqual(href, "/static/terminal.css")
            elif rel_kind == "a":
                self.assertTrue(href.startswith("#") or href == "http://127.0.0.1:8766/", "unexpected link " + href)
        for dirpath, _dirs, files in os.walk(STATIC):
            for fn in files:
                if fn.endswith((".js", ".css", ".html")):
                    full = os.path.join(dirpath, fn)
                    with open(full, "rb") as fh:
                        raw = fh.read()
                    try:
                        raw.decode("ascii")
                    except UnicodeDecodeError as e:
                        self.fail("%s is not ASCII: %s" % (fn, e))
                    text = raw.decode("ascii")
                    for m in re.finditer(r"https?://[^\s'\"<>)]+", text):
                        url = m.group(0)
                        self.assertFalse(re.search(r"cdn|unpkg|jsdelivr|googleapis|cloudflare", url), "CDN reference in %s: %s" % (fn, url))

    def test_root_tokens_copied_verbatim_plus_aliases(self):
        old, new = read(OLD_CSS), read(NEW_CSS)
        old_root, new_root = css_block(old, ":root"), css_block(new, ":root")
        self.assertGreaterEqual(len(old_root), 15)
        for k, v in old_root.items():
            self.assertIn(k, new_root, "missing token " + k)
            self.assertEqual(new_root[k], v, "token %s differs: %r != %r" % (k, new_root[k], v))
        old_light, new_light = css_block(old, ':root[data-theme="light"]'), css_block(new, ':root[data-theme="light"]')
        for k, v in old_light.items():
            self.assertEqual(new_light.get(k), v, "light token %s differs" % k)
        for k, v in ALIASES.items():
            m = re.search(re.escape(k) + r"\s*:\s*([^;]+);", new)
            self.assertIsNotNone(m, "alias missing " + k)
            self.assertEqual(m.group(1).strip(), v, "alias %s differs" % k)
        for q in QUALITIES:
            self.assertIn(".q-%s" % q, new, "no CSS for freshness state " + q)

    def test_index_has_mount_roots_and_no_glossary(self):
        p = _Refs()
        p.feed(read(INDEX))
        for i in REQUIRED_IDS:
            self.assertIn(i, p.ids, "index.html lacks #" + i)
        self.assertIn("PAPER", "".join(p.text))
        self.assertNotIn("glossary", read(INDEX).lower())
        self.assertFalse(os.path.exists(os.path.join(STATIC, "pages", "glossary.js")))
        self.assertFalse(os.path.exists(os.path.join(STATIC, "glossary-data.js")))
        for fn in PAGES + ["core"]:
            path = os.path.join(STATIC, "core.js") if fn == "core" else os.path.join(STATIC, "pages", fn + ".js")
            src = read(path)
            self.assertNotIn("#/glossary", src, fn + " links the glossary page")
            self.assertNotIn("T.gl(", src, fn + " uses glossary term links")

    def test_pages_and_components_register_and_balance(self):
        for name in PAGES:
            src = read(os.path.join(STATIC, "pages", name + ".js"))
            self.assertIn("T.pages.%s = " % name, src, "pages/%s.js does not register T.pages.%s" % (name, name))
            self.assertEqual(js_balance(src), (0, 0, 0), "unbalanced brackets in pages/%s.js" % name)
        for name in COMPONENTS:
            src = read(os.path.join(STATIC, "components", name + ".js"))
            self.assertIn("T.components.%s = " % name, src, "components/%s.js does not register" % name)
            self.assertEqual(js_balance(src), (0, 0, 0), "unbalanced brackets in components/%s.js" % name)
        core = read(os.path.join(STATIC, "core.js"))
        self.assertEqual(js_balance(core), (0, 0, 0))
        for need in ["T.api", "T.state", "T.router", "T.poll", "T.fmt", "T.keys", "T.ui", "T.conv", "T.book", "T.ticket", "visibilitychange", "X-Request-Id", "x-replayed", "status=all", "orders", "book=",
                     # the C11 hooks other packages register into / call
                     "T.marketPlugins", "T.ticketKinds", "T.providerLabel", "renderKeys:", "T.attention", "incident_id", "accountBar", "T.pageStatus"]:
            self.assertIn(need.replace("status=all", "status: 'all'") if need == "status=all" else need, core, "core.js lacks " + need)
        fresh = read(os.path.join(STATIC, "components", "freshness.js"))
        for q in QUALITIES:
            self.assertIn("'%s'" % q, fresh)

    def test_keybind_registry_defaults(self):
        """static/keys.js (the owner's ask 2026-10-21 17:10, docs/KEYBINDS.md): the F-key map is the default, no Alt / g default is
        left, every default is in api.py's one canonical form and not browser-reserved, no key is on two actions that can act together
        (api.keybind_clash), both refused lists agree, every global action has its handler in core.js, keys.js loads right after core.js."""
        from finsim.terminal import api
        src = read(os.path.join(STATIC, "keys.js"))
        self.assertEqual(js_balance(src), (0, 0, 0))
        acts = {m.group(1): (None if m.group(3) == "null" else m.group(3)[1:-1]) for m in re.finditer(r"act\('([a-z0-9_.]+)', '([A-Za-z ]+)', (null|'[^']*')", src)}
        acts.update({"nav.account_%d" % n: None for n in range(1, 10)})              # the two loops of keys.js
        acts.update({"ticket.hedge_%d" % n: str(n) for n in range(1, 6)})
        self.assertEqual(74, len(acts))                                         # FINSIM1_PLAN C1: 68 - page.news + 7 page actions
        self.assertNotIn("page.news", acts)
        # the 1.0.6 default map (FINSIM1_PLAN C1 / Q10): the new navigation's pages on the keys the account pages held, F12 the Dashboard
        owner = {"F1": "nav.us_exchange", "F2": "nav.debt", "F3": "nav.credit", "F4": "nav.foreign_fx", "F5": "nav.private", "F6": "nav.prime_broker",
                 "F7": "page.portfolio", "F8": "page.pending", "F9": "dock.toggle", "F10": "page.treasury", "F11": "page.risk", "F12": "page.dashboard",
                 "Shift+F1": "book.personal", "Shift+F2": "book.fund", "Shift+F3": "book.institutional", "Shift+F4": "page.accounts", "Shift+F5": "page.cash_collateral",
                 "Shift+F6": "page.derivatives", "Shift+F7": "page.accounting", "Shift+F8": "page.completed", "Shift+F9": "page.watchlists",
                 "Shift+F10": "page.history", "Shift+F11": "page.settings", "Shift+F12": "app.help_2", "/": "app.search", "?": "app.help", "Escape": "dialog.close"}
        for key, aid in owner.items():
            self.assertEqual(key, acts.get(aid), aid)
        fkeys = {k: a for a, k in acts.items() if k and re.fullmatch(r"(Shift\+)?F([1-9]|1[0-2])", k)}
        self.assertEqual({k: v for k, v in owner.items() if re.fullmatch(r"(Shift\+)?F([1-9]|1[0-2])", k)}, fkeys)   # exactly the 24 F-key slots above
        # the new page actions carry their Q10 key and name the page that must be loaded for them to show (the hiding rule); the
        # account pages' tabs, Activity and Trading keep their actions without a default key (bindable in Settings > Keyboard)
        new_pages = {"page.pending": ("pending", "F8"), "page.completed": ("completed", "Shift+F8"), "page.accounts": ("accounts", "Shift+F4"),
                     "page.cash_collateral": ("cash_collateral", "Shift+F5"), "page.derivatives": ("derivatives", "Shift+F6")}
        for aid in ("page.trading", "page.activity", "nav.orders", "nav.positions", "nav.cash", "nav.transactions", "nav.markets"):
            self.assertIn(aid, acts)
            self.assertIsNone(acts[aid], aid)
        for aid, (page, key) in new_pages.items():                            # a label may hold an escaped quote (that account\'s)
            self.assertEqual(key, acts[aid], aid)
            self.assertRegex(src, r"act\('%s', 'Pages', '%s', '(?:[^'\\]|\\.)*', \{ page: '%s' \}\);" % (re.escape(aid), re.escape(key), page))
        self.assertRegex(src, r"act\('page\.dashboard', 'Pages', 'F12', 'Dashboard', \{ page: 'dashboard' \}\);")
        self.assertRegex(src, r"act\('page\.trading', 'Pages', null, '[^']*'\);")                       # Trading is always available
        self.assertRegex(src, r"act\('page\.history', 'Pages', 'Shift\+F10', 'History: [^']*'\);")       # the hub (core.js: goHistoryHub)
        # the Pages group follows the sidebar's order
        order = [m.group(1) for m in re.finditer(r"act\('(page\.[a-z_]+)', 'Pages'", src)]
        self.assertEqual(["page.dashboard", "page.portfolio", "page.trading", "page.watchlists", "page.pending", "page.completed", "page.accounts", "page.treasury",
                          "page.cash_collateral", "page.derivatives", "page.history", "page.settings", "page.risk", "page.accounting", "page.activity"], order)
        # the help ('?') and Settings > Keyboard list only actions that work: T.keys.groups() keeps the available ones
        self.assertIn("return !a.page || !!(T.pages && T.pages[a.page]);", src)
        self.assertIn("return a.group === g && available(a.id);", src)
        self.assertIn("K.available = available", src)
        for aid, key in acts.items():
            self.assertRegex(aid, api.KEYBIND_ID_RE)
            if key is not None:
                self.assertEqual(key, api.keybind_canonical(key), aid)
                self.assertNotIn(key, api.KEYBIND_REFUSED, aid)
                self.assertFalse(key.startswith("Alt+"), aid)
        self.assertIsNone(api.keybind_clash(acts))
        refused = set(re.findall(r"^\s+'([^']+)': '[^']*',?$", src[src.index("var REFUSED"):src.index("var MAY_KEEP")], re.M))
        self.assertEqual(set(api.KEYBIND_REFUSED), refused)
        core = read(os.path.join(STATIC, "core.js"))
        for aid in acts:
            if api.keybind_scope(aid) in ("global", "dialog"):
                self.assertIn("'%s':" % aid, core, "core.js has no handler for " + aid)
        p = _Refs()
        p.feed(read(INDEX))
        self.assertEqual(p.scripts.index("/static/core.js") + 1, p.scripts.index("/static/keys.js"))


class ShellTests(unittest.TestCase):
    """FINSIM1_PLAN C1 / C2 / 15.7 (release 1.0.5): the routes and redirects, the optional pages of the new navigation, the sidebar's
    first row and the news feature gone from the shell's own files."""

    @staticmethod
    def routes():
        """core.js ROUTES as (compiled regex, the returned object's source), in order (first match wins, like the router)."""
        core = read(os.path.join(STATIC, "core.js"))
        start = core.index("var ROUTES = [")
        block = core[start:core.index("\n  ];", start)]
        out = [(re.compile(m.group(1)), m.group(2)) for m in re.finditer(r"\[/(.+?)/, function \((?:m)?\) \{ return (\{[^}]*\}); \}\]", block)]
        return core, out

    @staticmethod
    def page_of(routes, h):
        for rx, ret in routes:
            if rx.match(h):
                return ret
        return None

    def test_routes_default_fallback_redirects_and_new_pages(self):
        core, routes = self.routes()
        self.assertGreaterEqual(len(routes), 20)
        expect = {"#/dashboard": "{ page: 'dashboard' }", "#/news": "{ page: 'dashboard', removed: true }", "#/news?x=1": "{ page: 'dashboard', removed: true }",
                  "#/trading": "{ page: 'trading' }", "#/accounts": "{ page: 'accounts' }",
                  "#/pending": "{ page: 'pending', query: parseQuery(m[1]) }", "#/pending?account=F1:US_EXCHANGE": "{ page: 'pending', query: parseQuery(m[1]) }",
                  "#/completed?status=settling": "{ page: 'completed', query: parseQuery(m[1]) }",
                  "#/cash": "{ page: 'cash_collateral', query: parseQuery(m[1]) }", "#/cash?account=F1:PB_GS": "{ page: 'cash_collateral', query: parseQuery(m[1]) }",
                  "#/derivatives": "{ page: 'derivatives', tab: m[1] || null }", "#/derivatives/otc": "{ page: 'derivatives', tab: m[1] || null }",
                  "#/history": "{ page: 'history', hub: true, tab: m[1] || null }", "#/history/nav": "{ page: 'history', hub: true, tab: m[1] || null }",
                  "#/h/P1/portfolio": "{ page: 'history', lifetime: dec(m[1]), sub: m[2], tab: m[3] || null }",
                  "#/a/F1%3AUS_EXCHANGE/markets": "{ page: 'markets', account: dec(m[1]), tab: m[2] || null }",
                  "#/a/F1%3AUS_EXCHANGE/cash": "{ page: m[2], account: dec(m[1]) }", "#/portfolio": "{ page: 'portfolio', tab: m[1] || 'positions' }",
                  "#/activity?type=orders": "{ page: 'activity', query: parseQuery(m[1]) }", "#/settings/data": "{ page: 'settings', tab: m[1] || 'general' }"}
        for h, ret in expect.items():
            self.assertEqual(ret, self.page_of(routes, h), h)
        for h in ("#/nope", "#/newsletter", "#/pending/x", "#/dashboard/x", "#/"):
            self.assertIsNone(self.page_of(routes, h), h)                     # unknown: the router's fallback
        # the default route and the unknown-route fallback are the Dashboard; the old news address redirects with its one-time toast
        self.assertIn("var DEFAULT_ROUTE = '#/dashboard';", core)
        self.assertIn("hash = hash || DEFAULT_ROUTE;", core)
        self.assertIn("return { page: 'dashboard', hash: DEFAULT_ROUTE, unknown: hash };", core)
        self.assertIn("T.ui.toast('no page ' + r.unknown + ' - showing the Dashboard', '', 3500); replaceHash(DEFAULT_ROUTE);", core)
        self.assertIn("T.ui.toast('That page was removed - showing the Dashboard', '', 4000); replaceHash(DEFAULT_ROUTE);", core)
        self.assertIn("if (!location.hash) { replaceHash(DEFAULT_ROUTE); }", core)
        self.assertNotIn("#news", core)
        # P1b (UI-15): the retired news sources have no provider label any more; no news string is left in core.js
        start = core.index("T.PROVIDER_LABEL = {")
        labels = core[start:core.index("};", start)]
        for gone in ("yahoo_news", "newspaper", "news_fed", "news_yahoo", "news_paper"):
            self.assertNotIn(gone, labels, gone)
        self.assertIsNone(re.search(r"(?<![a-z_])fed:", labels))                       # 'fed' (news) gone; 'nyfed' (SOFR fallback) stays
        for s in NEWS_STRINGS:
            self.assertNotIn(s.lower(), core.lower(), s)
        # UI-20: the old Settings > Data Status address opens Settings > Market data (the address bar is corrected)
        self.assertIn("var TAB_ALIASES = { settings: { data: 'market-data' } };", core)
        self.assertIn(r"if (alias) { r.tab = alias; r.hash = r.hash.replace(/\/[^\/?]*$/, '/' + alias); replaceHash(r.hash); }", core)
        # C1 / P1b: Shift+F10 opens the History hub (the last past lifetime's pages only while the hub is not in the build)
        self.assertIn("'page.history': goHistoryHub,", core)
        self.assertIn("if (historyHub()) { T.router.go('#/history'); return; }", core)
        # V104R2-BW-04: an error is said in the server's words, never led by its internal code
        self.assertNotIn("(e.code + ': ' + e.message)", core)
        self.assertIn("else if (e && e.code && (!e.message || e.message === e.code)) { msg = T.codeWords(e.code); }", core)
        # #/trading: the last venue used (remembered on a Markets / instrument page), else US Exchange Markets
        self.assertIn("T.store.get('last_venue', null)", core)
        self.assertIn("T.store.set('last_venue', r.account)", core)
        self.assertIn("accountsOf('US_EXCHANGE')[0]", core)
        # the new pages: their T.pages entry, else the NOT YET BUILT panel (no API call); the hub is T.pages.history.hub
        for page in ("pending", "completed", "accounts", "cash_collateral", "derivatives", "history"):
            self.assertRegex(core, r"\n    %s: \['(?:[^'\\]|\\.)+', '(?:[^'\\]|\\.)+'\]" % page)
        self.assertIn("var h = T.pages.history && T.pages.history.hub;", core)
        # C2: T.hasModule reads /api/health .modules, which the boot fetches before the first route renders
        self.assertIn("T.hasModule = function (name)", core)
        self.assertIn("m[name] === 'bound'", core)
        self.assertIn("var bootHealth = T.api.get('/api/health'", core)
        # C1: on an account page Pending / Completed open filtered to it, Cash & Collateral opens its Cash & financing
        self.assertIn("'page.pending': function () { var id = routeAccount(); T.router.go(id ? '#/pending?account=' + encodeURIComponent(id) : '#/pending'); }", core)
        self.assertIn("'page.completed': function () { var id = routeAccount(); T.router.go(id ? '#/completed?account=' + encodeURIComponent(id) : '#/completed'); }", core)
        self.assertIn("'page.cash_collateral': function () { var id = routeAccount(); T.router.go(id ? T.router.account(id, 'cash') : '#/cash'); }", core)
        self.assertNotIn("'page.news'", core)

    def test_optional_pages_listed_alike_and_the_dashboard_required(self):
        from finsim.terminal import api
        p = _Refs()
        p.feed(read(INDEX))
        for rel in api.PLANNED_STATIC:                                         # api.py, index.html and the fixture server list the same optional files
            self.assertIn("/static/" + rel, p.optional, rel)
            self.assertIn(rel, fx.OPTIONAL_STATIC, rel)
        for rel in ("pages/pending.js", "pages/completed.js", "pages/accounts.js", "pages/cash_collateral.js", "pages/derivatives.js", "components/imports.js"):
            self.assertIn(rel, api.PLANNED_STATIC, rel)
        self.assertIn("/static/pages/dashboard.js", p.scripts)                 # 15.7 item 1: required in 1.0.5
        self.assertNotIn("/static/pages/dashboard.js", p.optional)
        self.assertNotIn("pages/dashboard.js", api.PLANNED_STATIC)
        self.assertNotIn("pages/dashboard.js", fx.OPTIONAL_STATIC)
        self.assertTrue(os.path.isfile(os.path.join(STATIC, "pages", "dashboard.js")))
        self.assertLess(p.scripts.index("/static/pages/history.js"), p.scripts.index("/static/pages/dashboard.js"))
        # news is gone from the shell: no page file, no tag, no root, no fixture route
        self.assertFalse(os.path.exists(os.path.join(STATIC, "pages", "news.js")))
        self.assertNotIn("/static/pages/news.js", p.scripts)
        self.assertNotIn("news", p.ids)
        self.assertNotIn("pages/news.js", fx.OPTIONAL_STATIC)
        self.assertIsNone(fx.match_route("GET", "/api/news"))
        self.assertEqual("attention_summary", fx.match_route("GET", "/api/attention/summary"))
        # the meta description says what this is (F15)
        html = read(INDEX)
        m = re.search(r'<meta name="description" content="([^"]+)">', html)
        self.assertEqual("Ledger-backed paper-trading simulator: real delayed market data, simulated executions, fictional capital.", m.group(1))
        self.assertNotIn("no simulation", html)
        # P8R1-01: the page declares its own (empty, inline) icon in <head>, so the browser never asks the server for /favicon.ico - the
        # server has none, and that 404 is a console error on the owner's first page load (rule 5: 0 console errors)
        head = html.split("</head>")[0]
        self.assertIn('<link rel="icon" href="data:,">', head)
        self.assertNotIn("favicon.ico", html)

    def test_sidebar_order_keys_and_no_news_strings(self):
        """P1 task 3 (UI-06): the sidebar in the directive's order, each row with its key action; Treasury inside Accounts; History lit
        on the hub and on an archived lifetime's pages (Settings no longer is); the badges only from the trade blotters' counters; the PB
        onboarding row and the Activity row only as fallbacks while the Accounts page / the History hub are not in the build."""
        side = read(os.path.join(STATIC, "components", "sidebar.js"))
        rows = [m.groups() for m in re.finditer(r"nav\.appendChild\(item\('(#/[^']+)', '([^']+)', \{ key: keyOf\('([a-z_.]+)'\)", side)]
        self.assertEqual([("#/dashboard", "Dashboard", "page.dashboard"), ("#/portfolio", "Portfolio", "page.portfolio"), ("#/watchlists", "Watchlists", "page.watchlists"),
                          ("#/pending", "Pending Trades", "page.pending"), ("#/completed", "Completed Trades", "page.completed"), ("#/accounts", "Accounts", "page.accounts"),
                          ("#/treasury", "Treasury", "page.treasury"), ("#/cash", "Cash & Collateral", "page.cash_collateral"),
                          ("#/derivatives", "Derivatives", "page.derivatives"), ("#/history", "History", "page.history"), ("#/settings", "Settings", "page.settings"),
                          ("#/risk", "Risk", "page.risk"), ("#/accounting", "Accounting", "page.accounting"), ("#/activity", "Activity", "page.activity")], rows)
        body = side[side.index("function render()"):side.index("/* PO-15: the dealers")]
        # TRADING: the group title (#/trading) comes after Portfolio, the venue rows after it, Watchlists closes the group
        self.assertLess(body.index("item('#/portfolio'"), body.index("nav.appendChild(tradingTitle());"))
        self.assertLess(body.index("nav.appendChild(tradingTitle());"), body.index("accts.forEach(function (a, i) {"))
        self.assertLess(body.index("accts.forEach(function (a, i) {"), body.index("item('#/watchlists'"))
        self.assertIn("T.h('a', { href: '#/trading', class: 'nav-section nav-group-link'", side)
        # Treasury is a sub-row of Accounts; History is lit on the hub and on #/h/... (r.page === 'history'); Settings only on its own pages
        self.assertIn("item('#/treasury', 'Treasury', { key: keyOf('page.treasury'), sub: true })", side)
        self.assertIn("isActive: function (r) { return r.page === 'history'; }", side)
        self.assertIn("item('#/settings', 'Settings', { key: keyOf('page.settings'), isActive: function (r) { return r.page === 'settings'; } })", side)
        # the badges: GET /api/dashboard/counts only while the dashboard module is bound (C2), never a guessed count
        self.assertIn("if (!T.hasModule('dashboard')) {", side)
        self.assertIn("T.api.get('/api/dashboard/counts')", side)
        self.assertIn("if (!c || !c.present) { return { pending: 0, completed: 0, pendingTitle: '', completedTitle: '' }; }", side)
        # the fallbacks: the PB onboarding row while the Accounts page is not built, Activity while the History hub is not built
        self.assertIn("if (!T.pages.accounts) {", side)
        self.assertIn("if (!historyHubBuilt()) { nav.appendChild(item('#/activity'", side)
        self.assertIn("openPbModal: openPbModal", side)                               # P6's Accounts page header opens it
        for name, path in (("sidebar.js", os.path.join(STATIC, "components", "sidebar.js")), ("dashboard.js", os.path.join(STATIC, "pages", "dashboard.js")),
                           ("index.html", INDEX), ("settings.js", os.path.join(STATIC, "pages", "settings.js")), ("keys.js", os.path.join(STATIC, "keys.js")),
                           ("attention.js", os.path.join(STATIC, "components", "attention.js")), ("core.js", os.path.join(STATIC, "core.js"))):
            src = read(path)
            for s in NEWS_STRINGS:
                self.assertNotIn(s.lower(), src.lower(), "%s still says %r" % (name, s))
        # UI-15 / UI-20: Settings > Market data (status first, the preferences linked) with no news block, no news provider texts and no
        # link to the retired game's archive (its command left the CLI); attention.js shows no news row
        settings = read(os.path.join(STATIC, "pages", "settings.js"))
        self.assertIn("['market-data', 'Market data']", settings)
        self.assertNotIn("['data', 'Data Status']", settings)
        for gone in ("h.news", "yahoo_news", "newspaper:", "/api/legacy", "injection_flags"):
            self.assertNotIn(gone, settings, gone)
        self.assertIsNone(re.search(r"(?<![a-z_])fed: '", settings))                    # the Federal Reserve news feed's row; 'nyfed' stays
        self.assertIn("p.appendChild(T.h('h2', null, 'market-data and execution preferences'));", settings)
        for link in ("#/settings/general", "#/settings/calendar", "#/settings/margins"):
            self.assertIn(link, settings)
        att = read(os.path.join(STATIC, "components", "attention.js"))
        self.assertNotIn("INJECTION_FLAGGED", att)
        self.assertNotIn("h.news", att)

    def test_c4_shell_classes(self):
        css = read(NEW_CSS)
        for cls in ("filterbar", "stage-chip", "stage-working", "stage-partial", "stage-held", "stage-queued", "stage-filled", "stage-cancelled",
                    "stage-expired", "stage-rejected", "stage-settling", "stage-failed", "badge-asof", "badge-model", "drawer", "drawer-tabs", "kv-grid"):
            self.assertRegex(css, r"\.%s[\s,.{:>]" % re.escape(cls), cls)
        core = read(os.path.join(STATIC, "core.js"))
        self.assertIn("T.kvGrid = function (pairs)", core)
        self.assertIn("T.stageChip = function (stage, label, title)", core)


class FixtureServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = fx.FixtureServer().start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=10)
        h = {"Accept": "application/json"}
        h.update(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            h["Content-Type"] = "application/json"
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        raw = r.read()
        hdrs = {k.lower(): v for k, v in r.getheaders()}
        c.close()
        return r.status, raw, hdrs

    def test_refusals_lead_with_words_never_an_internal_code(self):
        """1.0.4 leftovers V104R2-BW-04 / BW-02 / BW-05 / OTC-02 and FINSIM1 P1b-R3 / P6-R2 / P4-R3 (the integrator, 1.0.6): no ticket, desk
        or page leads a refusal, blocker or warning with its internal code (the message is shown, the code only in the hover; an error goes
        through ``T.api.describe``); the bond and private ticket heads name the account without its id; the bond fill line drops the stored
        memo's conventions group and its funding hover is words, not JSON; the instrument page's session chip is in words; the repo desk
        asks for the soft preview (a refusal answers 200, no failed request); a price chip reads CLOSE only at or after its session's end;
        the Completed Trades drawer shows an OTC contract's timeline."""
        lead = re.compile(r"T\.h\('b', null, (?:b|w|e)\.code|(?:b|w|r|e|x)\.code \+ ': '|\((?:e|err)\.code \|\| 'ERROR'\) \+ ': '"
                          r"|\((?:e|err)\.code \|\| ''\) \+ ' ' \+|e\.code \+ ': ' : ''")
        hits = []
        for folder in ("components", "pages"):
            for name in sorted(os.listdir(os.path.join(STATIC, folder))):
                if name.endswith(".js"):
                    for i, line in enumerate(read(os.path.join(STATIC, folder, name)).splitlines(), 1):
                        if lead.search(line):
                            hits.append(f"{folder}/{name}:{i}: {line.strip()[:120]}")
        self.assertEqual([], hits)
        bond, priv = read(os.path.join(STATIC, "components", "bondticket.js")), read(os.path.join(STATIC, "components", "privateticket.js"))
        self.assertNotIn("' (' + account_id + ')'", bond)
        self.assertNotIn("' (' + opts.account_id + ')", priv)
        self.assertIn("memoWords(fill.memo)", bond)
        self.assertNotIn("JSON.stringify(fund)", bond)
        self.assertIn("groupWords(s.group)", read(os.path.join(STATIC, "pages", "instrument.js")))
        self.assertIn("Object.assign({ soft: true }, b)", read(os.path.join(STATIC, "components", "creditdesk.js")))
        self.assertIn("beforeSessionEnd(rec, qt) ? 'LAST ' : 'CLOSE '", read(os.path.join(STATIC, "components", "freshness.js")))
        self.assertIn("T.components.swapticket.timelineTable(d.timeline)", read(os.path.join(STATIC, "pages", "completed.js")))

    def test_every_route_is_served(self):
        missing, served = [], 0
        for method, rx, name, example, body in fx.ROUTES:
            self.assertEqual(fx.match_route(method, example.split("?")[0]), name, "example %s %s does not map to %s" % (method, example, name))
            if body is not None:
                body = dict(body, request_id="ui:test-%s-0001" % name)
            status, raw, hdrs = self.req(method, example, body)
            self.assertIn("no-store", hdrs.get("cache-control", ""), example)
            if status == 404:
                err = json.loads(raw)
                self.assertEqual(err["error"]["code"], "FIXTURE_MISSING", example)
                missing.append(name)
                continue
            served += 1
            if example.split("?")[0].endswith(".csv") or "format=csv" in example:
                self.assertTrue(hdrs["content-type"].startswith("text/csv"), example)
                continue
            data = json.loads(raw)
            self.assertNotIn("$NOW", raw.decode("utf-8"), "placeholder left in " + example)
            self.assertNotIn("$TODAY", raw.decode("utf-8"), "placeholder left in " + example)
            if status == 200:
                self.assertIsInstance(data, (dict, list), example)
            else:
                self.assertIn("error", data, example)
                self.assertIn(status, (202, 400, 409), example)
        self.assertTrue(set(missing) <= fx.WP13_FIXTURES, "fixtures missing that WP-12 owns: %s" % sorted(set(missing) - fx.WP13_FIXTURES))
        self.assertGreaterEqual(served, len(fx.ROUTES) - len(fx.WP13_FIXTURES))

    def test_placeholders_variants_and_scenarios(self):
        now = int(time.time())
        status, raw, _ = self.req("GET", "/api/quotes?ids=AAPL")
        self.assertEqual(status, 200)
        q = json.loads(raw)
        # AAPL's fixture quote_time is "$NOW-245": 245 s before the serve time (a few seconds of slack for the request)
        self.assertTrue(now - 245 - 5 <= q["quotes"]["AAPL"]["quote_time"] <= now - 245 + 5)
        self.assertEqual(q["quotes"]["AAPL"]["quality"], "delayed")
        self.assertEqual(q["quotes"]["XYZ"]["quality"], "unavailable")
        self.assertEqual(q["quotes"]["UST10Y"]["quality"], "computed")
        # role variant: the Credit account's markets page has its own fixture
        status, raw, _ = self.req("GET", "/api/accounts/P2:CREDIT/markets")
        self.assertEqual(json.loads(raw)["role"], "CREDIT")
        status, raw, _ = self.req("GET", "/api/accounts/P2:US_EXCHANGE/markets?tab=futures")
        self.assertEqual(json.loads(raw)["tab"], "futures")
        # query variant: ALL_BOOKS consolidates and carries the private (q-private) position
        status, raw, _ = self.req("GET", "/api/portfolio?scope=ALL_BOOKS")
        pall = json.loads(raw)
        self.assertEqual(pall["scope"], "ALL_BOOKS")
        self.assertTrue(any(p["mark"]["quality"] == "private" for p in pall["positions"]))
        # the positions fixture carries one of every freshness state the renderer knows (except the reserved 'live')
        status, raw, _ = self.req("GET", "/api/accounts/P2:US_EXCHANGE/positions")
        qualities = {p["mark"]["quality"] for p in json.loads(raw)}
        self.assertTrue({"delayed", "close", "stale", "unavailable"} <= qualities, qualities)
        # scenario: a forced provider failure
        status, raw, _ = self.req("GET", "/__fixture/scenario?name=provider_down")
        self.assertEqual(json.loads(raw)["scenario"], "provider_down")
        status, raw, _ = self.req("GET", "/api/health")
        h = json.loads(raw)
        self.assertFalse(h["providers"]["yahoo"]["ok"])
        self.assertTrue(h["workers"]["quotes"]["stalled"])
        status, raw, _ = self.req("GET", "/api/quotes?ids=AAPL")
        self.assertEqual(json.loads(raw)["quotes"]["AAPL"]["quality"], "stale")
        status, raw, _ = self.req("GET", "/__fixture/scenario?name=")
        self.assertIsNone(json.loads(raw)["scenario"])
        status, raw, _ = self.req("GET", "/api/health")
        self.assertTrue(json.loads(raw)["providers"]["yahoo"]["ok"])
        # the "$status"/"$body" wrapper: a 400 NOT_IN_THIS_BUILD answer
        status, raw, _ = self.req("POST", "/api/fx/forward", {"request_id": "ui:test-fwd-0001"})
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(raw)["error"]["code"], "NOT_IN_THIS_BUILD")
        # request-id discipline: a mutating route without request_id is refused like the real API
        status, raw, _ = self.req("POST", "/api/treasury/deposit", {"currency": "USD", "amount": "1.00"})
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(raw)["error"]["code"], "BAD_REQUEST_ID")
        status, raw, _ = self.req("POST", "/api/treasury/deposit", {"currency": "USD", "amount": "1.00"}, {"X-Request-Id": "ui:test-dep-0001"})
        self.assertEqual(status, 200)

    def test_static_ui_served(self):
        status, raw, hdrs = self.req("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"<title>FinSim Paper Terminal</title>", raw)
        self.assertIn("no-store", hdrs["cache-control"])
        status, raw, hdrs = self.req("GET", "/static/core.js")
        self.assertEqual(status, 200)
        self.assertTrue(hdrs["content-type"].startswith("application/javascript"))
        status, raw, hdrs = self.req("GET", "/static/components/ticket.js")
        if os.path.exists(os.path.join(STATIC, "components", "ticket.js")):
            self.assertEqual(status, 200)
        else:
            self.assertEqual(status, 200)
            self.assertEqual(hdrs.get("x-fixture-stub"), "1")
        status, raw, hdrs = self.req("GET", "/static/nope.js")
        self.assertEqual(status, 404)
        status, raw, hdrs = self.req("GET", "/static/../terminal_fixture_server.py")
        self.assertIn(status, (403, 404))
        status, raw, hdrs = self.req("GET", "/api/accounts/P2:US_EXCHANGE/transactions?format=csv")
        self.assertEqual(status, 200)
        self.assertTrue(hdrs["content-type"].startswith("text/csv"))

    def test_fixture_numbers_tie(self):
        """The hand-worked fixture world (gen comments): US_EXCHANGE NAV 402,361.50 = advances 400,000 - fees 11.00 + unrealised
        2,492.50 - XYZ at cost 120.00; P&L TODAY 2,083.50 = sum of the position day P&Ls 797.50 + 69.00 + 985.25 + 121.75 + 110.00."""
        status, raw, _ = self.req("GET", "/api/accounts/P2:US_EXCHANGE")
        d = json.loads(raw)
        self.assertEqual(d["nav"]["nav"], "402361.50")
        self.assertEqual(d["pnl"]["TODAY"]["pnl"], "2083.50")
        self.assertEqual(d["pnl"]["TODAY"]["residual"], "0.00")
        status, raw, _ = self.req("GET", "/api/accounts/P2:US_EXCHANGE/positions")
        pos = json.loads(raw)
        day = sum(int(p["day_pnl"].replace(".", "")) for p in pos if p["day_pnl"] is not None)   # cents: 79750 + 6900 + 98525 + 12175 + 11000
        self.assertEqual(day, 208350)
        unreal = sum(int(p["unrealised"].replace(".", "")) for p in pos if p["unrealised"] is not None)   # 80000 + 7000 + 98750 + 12500 + 51000
        self.assertEqual(unreal, 249250)
        fees = sum(int(p["fees_itd"].replace(".", "")) for p in pos)                                     # 250 + 100 + 225 + 325 + 100 + 100
        self.assertEqual(fees, 1100)
        self.assertEqual(40000000 - fees + unreal - 12000, 40236150)
        status, raw, _ = self.req("GET", "/api/treasury")
        t = json.loads(raw)["treasury"]
        # Treasury NAV 435,012.50 = capital 1,000,000 + retained 12.50 - advances 565,000
        self.assertEqual(t["nav"], "435012.50")
        self.assertEqual(100000000 + 1250 - 56500000, 43501250)
        self.assertTrue(t["identity_ok"])


if __name__ == "__main__":
    unittest.main()
