"""WP-12 static checks (TERMINAL_WORKPLAN.md WP-12 tests; TERMINAL_DESIGN.md s17).

- every JS / CSS file index.html references exists (or is an OPTIONAL component of another package), is ASCII, and no
  script or stylesheet is loaded from a CDN (no http(s):// in src/href except the legacy link);
- every `:root` token of finsim/finsim/static/style.css (and the light theme) is present in terminal.css with the SAME
  value, plus the s17.2 aliases;
- index.html carries the mount roots the scripts expect (#bookswitcher, #news, #history, ...);
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
REQUIRED_IDS = ["app", "header", "banner", "attention", "sidebar", "bookswitcher", "nav", "main", "page", "news", "history",
                "dock", "dock-body", "modal-root", "toast-root", "help", "clock", "session-chips", "acct-card", "integrity-dot"]
PAGES = ["markets", "instrument", "positions", "orders", "cash", "transactions", "treasury", "portfolio", "accounting", "risk",
         "activity", "watchlists", "settings", "history"]
COMPONENTS = ["sidebar", "header", "attention", "freshness", "grid", "chart", "chain", "bookswitcher"]
QUALITIES = ["live", "delayed", "close", "computed", "private", "stale", "unavailable"]


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
        for need in ["T.api", "T.state", "T.router", "T.poll", "T.fmt", "T.keys", "T.ui", "T.conv", "T.book", "T.ticket", "visibilitychange", "X-Request-Id", "x-replayed", "status=all", "orders", "book="]:
            self.assertIn(need.replace("status=all", "status: 'all'") if need == "status=all" else need, core, "core.js lacks " + need)
        fresh = read(os.path.join(STATIC, "components", "freshness.js"))
        for q in QUALITIES:
            self.assertIn("'%s'" % q, fresh)


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
