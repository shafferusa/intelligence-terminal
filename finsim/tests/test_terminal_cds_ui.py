"""CDS_SINGLE_NAME_PLAN rev 2 (release 1.0.7) package B4: static checks of the single-name CDS desk, components/cdsnames.js, and of its
UI hook edits (hooks_b4.py, Appendix D H4-1..H4-14).

On every tree (no hook needed):
  - the file is ASCII with no CDN reference, its brackets balance, it registers T.components.cdsnames with every helper the hooked pages
    call (plan 6.7) and its keys follow the registry (T.keys.uses);
  - no refusal, blocker or warning is led by its internal code (the V104R2-BW-04 pattern of test_terminal_static: the words are shown, the
    code only in the hover);
  - every route it calls is one of plan 6.5 (desk, preview, terms, basis, basis effect, credit event; B2's entities; B3's model) and every
    call goes through those route constants; each call sits behind T.hasModule of the module that serves it (the desk, the preview, the
    terms, the levels and the credit event behind swaps_cds_names; the list behind credit_entities; the model behind cds_model);
  - the words the plan fixes are there: the MODEL label of the desk header, the deal-at-the-MODEL words, the credit-event label and rules,
    the fetching line with its 20 tries and "still fetching - try again", the first-run loading line, the "known so far" flag caption;
  - the fixture server serves the file.
On a tree with the hook edits (applied by the orchestrator, or by rehearse.py on a scratch copy): index.html, api.PLANNED_STATIC and the
fixture server list the optional file alike; api.PACKAGES names the three CDS modules; the OTC tab has the Single-name CDS sub-tab after
Credit (CDX), its notes and its mount; the shared contracts table, Derivatives, Positions, Portfolio, Risk, Cash and core.js dispatch a CDS
row to the helpers, each guarded by the component's presence, while every CDX line stays as it was; the hooked files still balance.
Without the hooks those tests SKIP with that reason.  Standard library only; no network; py -3.12 and py -3.14.
"""
from __future__ import annotations

import http.client
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from tests import terminal_fixture_server as fx  # noqa: E402
from tests.test_terminal_static import js_balance  # noqa: E402
from finsim.terminal import api  # noqa: E402

STATIC = os.path.join(ROOT, "finsim", "terminal", "static")
FILE = os.path.join(STATIC, "components", "cdsnames.js")
REL = "components/cdsnames.js"
#: plan 6.5: the routes the desk may call (the credit event's path is built from its prefix and the contract id)
ROUTES = {"/api/otc/cds/desk", "/api/otc/cds/preview", "/api/otc/cds/terms", "/api/otc/cds/basis", "/api/otc/cds/basis/effect",
          "/api/otc/cds/entities", "/api/otc/cds/model", "/api/otc/cds/contracts/"}
HELPERS = ("mount", "kindShort", "sideText", "nowText", "dealtNow", "sensitivity", "swapSide", "creditDialog", "creditSection", "riskPanel", "footer")
#: test_terminal_static's V104R2-BW-04 pattern (a refusal led by its code)
LEAD = re.compile(r"T\.h\('b', null, (?:b|w|e)\.code|(?:b|w|r|e|x)\.code \+ ': '|\((?:e|err)\.code \|\| 'ERROR'\) \+ ': '"
                  r"|\((?:e|err)\.code \|\| ''\) \+ ' ' \+|e\.code \+ ': ' : ''")


def read(*parts: str) -> str:
    with open(os.path.join(*parts), "r", encoding="utf-8") as f:
        return f.read()


def src() -> str:
    return read(FILE)


def _hooks_state():
    """Which hook edits are in this tree: {name: bool} (each marker is in a file hooks_b4.py edits)."""
    def has(rel, needle):
        try:
            return needle in read(STATIC, *rel.split("/"))
        except OSError:
            return False
    index = read(STATIC, "index.html")
    return {"api.PLANNED_STATIC": REL in api.PLANNED_STATIC, "fixture OPTIONAL_STATIC": REL in fx.OPTIONAL_STATIC,
            "index.html": "/static/components/cdsnames.js" in index, "markets_otc sub-tab": has("pages/markets_otc.js", "key: 'cds'"),
            "swapticket dispatch": has("components/swapticket.js", "T.components.cdsnames.kindShort(c)"),
            "derivatives dispatch": has("pages/derivatives.js", "T.components.cdsnames.dealtNow(c)"),
            "risk panel": has("pages/risk.js", "T.components.cdsnames.riskPanel"), "core acronyms": has("core.js", "CDS: 1, JTD: 1")}


STATE = _hooks_state()
HOOKS = all(STATE.values())
SKIP_WHY = ("the B4 hook edits (hooks_b4.py: api.py, index.html, the fixture server, markets_otc, swapticket, derivatives, positions, portfolio,"
            " risk, cash, core.js) are not applied to this tree; they run on a rehearsal copy and after integration (missing: "
            + ", ".join(k for k, v in STATE.items() if not v) + ")")


def _functions(text: str):
    """[(start, end, header)] of every function body in the file (strings, comments and regex literals skipped like js_balance), so a call can
    be located in the functions that enclose it."""
    out, stack, i, n = [], [], 0, len(text)
    last_sig, pending = "", None
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
            continue
        if text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j + 1
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c in "'\"`":
            q = c
            i += 1
            while i < n and text[i] != q:
                if text[i] == "\\":
                    i += 1
                i += 1
            i += 1
            last_sig = "s"
            continue
        if c == "/" and last_sig in ("", "(", ",", "=", ":", "[", "!", "&", "|", "?", "{", "}", ";", "+", "-"):
            i += 1
            in_class = False
            while i < n:
                ch = text[i]
                if ch == "\\":
                    i += 2
                    continue
                if ch == "[":
                    in_class = True
                elif ch == "]":
                    in_class = False
                elif ch == "/" and not in_class:
                    break
                i += 1
            i += 1
            last_sig = "r"
            continue
        if text.startswith("function", i) and not (text[i - 1:i].isalnum() or text[i - 1:i] in "_$"):
            m = re.match(r"function\s*([A-Za-z0-9_$]*)\s*\(", text[i:])
            if m:
                pending = (i, m.group(1) or "(anonymous)")
        if c == "{":
            stack.append(pending)
            pending = None
        elif c == "}":
            fr = stack.pop() if stack else None
            if fr is not None:
                out.append((fr[0], i, fr[1]))
        last_sig = c if not (c.isalnum() or c in "_$") else "w"
        i += 1
    return out


class DeskFileTests(unittest.TestCase):
    """components/cdsnames.js itself: needs no hook."""

    def test_ascii_no_cdn_balanced_registered_and_keys(self):
        with open(FILE, "rb") as fh:
            raw = fh.read()
        text = raw.decode("ascii")                                            # raises on a non-ASCII byte
        self.assertIsNone(re.search(r"https?://", text), "the desk loads or links nothing from the network")
        self.assertEqual((0, 0, 0), js_balance(text))
        self.assertIn("T.components.cdsnames = { mount: mount,", text)
        reg = text[text.index("T.components.cdsnames = {"):]
        reg = reg[:reg.index("};")]
        for h in HELPERS:                                                    # plan 6.7: the helpers the hooked pages call
            self.assertRegex(reg, r"\b%s: %s\b" % (h, h), h)
        self.assertIn("T.keys.uses('components/cdsnames.js')", text)
        self.assertIn("T.keys.is(ev, 'ticket.submit')", text)
        self.assertIn("T.keys.is(ev, 'ticket.preview')", text)
        for bad in ("innerHTML", "eval(", "document.write", "localStorage"):
            self.assertNotIn(bad, text, bad)

    def test_no_refusal_led_by_its_code(self):
        hits = [f"{i}: {line.strip()[:120]}" for i, line in enumerate(src().splitlines(), 1) if LEAD.search(line)]
        self.assertEqual([], hits)
        text = src()
        # blockers and warnings: the message in words, the code only in the hover
        self.assertIn("T.h('div', { class: 'cn-bad', title: s(b.code) }, s(b.message || words(b.code)))", text)
        self.assertIn("T.h('div', { class: 'cn-warn', title: s(w.code) }, s(w.message || words(w.code)))", text)
        self.assertIn("function describe(e) { return T.api && T.api.describe ? T.api.describe(e) : s(e && e.message || e); }", text)

    def test_every_route_is_a_plan_route_behind_its_module(self):
        text = src()
        literals = set(re.findall(r"'(/api/[^'?]*)'", text))
        self.assertTrue(literals, "no route found")
        self.assertEqual(set(), literals - ROUTES, "routes outside plan 6.5")
        self.assertEqual(ROUTES, literals, "every plan route is declared once")
        # every call goes through a route constant (no inline path), and the credit event is the contract route of 6.5
        calls = re.findall(r"T\.api\.(get|post)\(([^,]+),", text)
        self.assertGreaterEqual(len(calls), 9)
        for verb, first in calls:
            self.assertRegex(first.strip(), r"^R_[A-Z]+( \+ .*)?$", first)
        self.assertIn("T.api.post(R_CONTRACTS + encodeURIComponent(c.contract_id) + '/credit-event', ", text)
        # the module names and the gate
        self.assertIn("var MOD = 'swaps_cds_names';", text)
        self.assertIn("var ENT = 'credit_entities';", text)
        self.assertIn("var MDL = 'cds_model';", text)
        self.assertIn("function modOk(name) { return !!(T.hasModule && T.hasModule(name)); }", text)
        route_module = {"R_DESK": "MOD", "R_PREVIEW": "MOD", "R_TERMS": "MOD", "R_BASIS": "MOD", "R_EFFECT": "MOD", "R_CONTRACTS": "MOD",
                        "R_ENTITIES": "ENT", "R_MODEL": "MDL"}
        funcs = _functions(text)
        gate_of = {"MOD": ("modOk(MOD)",), "ENT": ("modOk(ENT)",), "MDL": ("modOk(MDL)",)}
        # every call: one of the functions that enclose it checks the module of its route BEFORE the call, or it is inside mount(), whose
        # build() refuses to render anything (so nothing can call) when swaps_cds_names is not bound
        build = text[text.index("    function build() {"):text.index("    /* ---- header")]
        self.assertIn("if (!modOk(MOD)) {", build)
        self.assertIn("'unavailable: the single-name CDS desk is not running in this build (no request is made)'", build)
        for m in re.finditer(r"T\.api\.(?:get|post)\((R_[A-Z]+)", text):
            pos, route = m.start(), m.group(1)
            encl = [f for f in funcs if f[0] < pos < f[1]]
            self.assertTrue(encl, route)
            gates = gate_of[route_module[route]]
            checked = any(any(g in text[f[0]:pos] for g in gates) for f in encl)
            in_mount = any(f[2] == "mount" for f in encl)
            if route_module[route] == "MOD":
                self.assertTrue(checked or in_mount, "%s at %d is not behind T.hasModule('swaps_cds_names')" % (route, pos))
            else:
                self.assertTrue(checked, "%s at %d is not behind T.hasModule of its module" % (route, pos))
        # the helpers outside mount that call a route check the module themselves
        dialog = text[text.index("  function creditDialog(st, c, onDone) {"):text.index("  function eventsOf(c, st) {")]
        self.assertIn("if (!modOk(MOD)) { T.ui.toast('unavailable: the single-name CDS module is not running in this build', 'err', 5000); return; }", dialog)
        self.assertIn("if (!account || !modOk(MOD)) { return Promise.resolve(); }", text)            # loadDesk
        self.assertIn("if (!account || !modOk(ENT)) { renderList(); return Promise.resolve(); }", text)   # loadList
        self.assertIn("if (!state.sel || state.blocked || !modOk(MOD)) { return; }", text)              # the two level dialogs

    def test_the_words_the_plan_fixes(self):
        text = src()
        for words in ("MODEL - equity-implied CreditGrades spread plus the ICE BofA AAA OAS floor; not a dealer quote",
                      "deal at the MODEL - no market level known",
                      "Its spread is a MODEL and has not been validated against traded levels; design-time checks missed traded names by about 2 times",
                      "simulated credit event - recovery entered by the owner; no ISDA Determinations Committee ruling or auction in the simulator",
                      "the contract closes: protection = notional x (1 - recovery), less the accrued premium, settles the next business day; the IM is",
                      "released and the upfront cost is realised",
                      "\\'s SEC filings and 1,000 daily closes (first time today only)",
                      "'still fetching - try again'", "var DESK_MS = 60000, SEARCH_MS = 250, EFFECT_MS = 400, PAGE = 50, RETRY_MAX = 20;",
                      "loading the reference-entity list from SEC EDGAR (first run, about 2-3 minutes)",
                      "from filing tags; SIC not checked yet", "flags known so far", "(model spread bucket - not a credit rating)",
                      "not set - not bookable: set your negotiated mid, or confirm dealing at the MODEL",
                      "'Negotiated mid ...'", "'Deal at the MODEL ...'", "'Request quote'", "'Preview'", "'Assumptions'", "'Reset terms'",
                      "'Simulate credit event ...'", "\">single-name CDS credit long / short '", "'Single-name CDS'",
                      "sw.rfqRequest(account, 'CDS', p.terms)", "a dealing level never changes it",
                      "type your mid: its effect is shown here before you save", "total change (full revaluation; one name has one mark per account)",
                      "senior unsecured; credit events XR14 (bankruptcy and failure to pay, no restructuring); no reference-obligation ISIN",
                      "FIN_CAVEAT", "unavailable</span>"):
            self.assertIn(words, text, words)
        # the tenors, tiers, filters and coupons of the plan (2.1, 4.2, 5.1)
        self.assertIn("var TENORS = [1, 2, 3, 4, 5, 7, 10];", text)
        self.assertIn("var TIERS = ['A_OR_BETTER', 'BBB', 'BB', 'B', 'CCC'];", text)
        self.assertIn("var LADDER = ['AAA', 'AA', 'A', 'BBB', 'BB', 'B', 'CCC'];", text)
        for f in ("'all'", "'non-financial'", "'financial'", "'REIT'", "'my contracts'"):
            self.assertIn(f, text, f)
        self.assertIn("cpn.appendChild(T.h('option', { value: '100' }, '100 bp'));", text)
        self.assertIn("cpn.appendChild(T.h('option', { value: '500' }, '500 bp'));", text)
        # the guard: Request quote stays disabled while the preview is not bookable or carries a blocker
        self.assertIn("ui.quoteBtn.disabled = !!state.blocked || !pv || p.stale || pv.bookable === false || !!blockers.length || state.busy;", text)
        self.assertIn("if (b.code === 'DEALING_LEVEL_UNCONFIRMED') { guard = true; }", text)
        self.assertIn("if (b.code === 'DISTRESSED_NEEDS_NEGOTIATED_MID') { distressed = true; }", text)
        # a distressed name is offered only the negotiated mid (a MODEL confirmation is not enough: plan 3.5, Q4)
        dis = text[text.index("      if (distressed) {"):text.index("      } else if (guard) {")]
        self.assertIn("'Negotiated mid ...'", dis)
        self.assertNotIn("modelDialog", dis)
        # a level is saved only once its effect for exactly that value is on screen
        self.assertIn("var w = want(), ok = !!(st.eff && w && st.eff.w === JSON.stringify(w));", text)
        self.assertIn("if (save) { save.disabled = st.action !== 'set' || !ok || !!state.blocked; }", text)
        # never a defaulted number: a missing money figure reads unavailable / n/a
        self.assertIn("function usd(row, key) { return hasMoney(row, key) ? 'USD ' + money(row, key) : 'n/a'; }", text)
        self.assertIsNone(re.search(r"money\([^)]*\|\| ?0", text), "a money figure defaulted to 0")

    def test_fixture_server_serves_the_file(self):
        srv = fx.FixtureServer().start()
        try:
            c = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
            c.request("GET", "/static/" + REL)
            r = c.getresponse()
            body = r.read()
            c.close()
        finally:
            srv.stop()
        self.assertEqual(200, r.status)
        self.assertIn(b"T.components.cdsnames = { mount: mount,", body)


@unittest.skipUnless(HOOKS, SKIP_WHY)
class HookTests(unittest.TestCase):
    """The H4 hook edits, on a tree where hooks_b4.py has been applied."""

    def test_listed_alike_and_package_ids(self):
        index = read(STATIC, "index.html")
        self.assertIn('<script src="/static/components/cdsnames.js" data-optional="1"></script>', index)
        self.assertLess(index.index("/static/components/cdsticket.js"), index.index("/static/components/cdsnames.js"))
        self.assertIn(REL, api.PLANNED_STATIC)
        self.assertIn(REL, fx.OPTIONAL_STATIC)
        self.assertEqual({"credit_entities": "CDS-B2", "cds_model": "CDS-B3", "swaps_cds_names": "CDS-B1"},
                         {k: api.PACKAGES.get(k) for k in ("credit_entities", "cds_model", "swaps_cds_names")})

    def test_otc_tab_sub_tab_notes_and_mount(self):
        otc = read(STATIC, "pages", "markets_otc.js")
        self.assertIn("{ key: 'credit', label: 'Credit (CDX)' },\n                 { key: 'cds', label: 'Single-name CDS' }];", otc)
        self.assertIn("'CDS_MODEL', 'CDS_DEAL', 'CDS_COUPON', 'CDS_CREDIT', 'ELIGIBILITY'];", otc)
        self.assertIn(".otc-desk.sub-cds .otc-swaps .swapticket > :not(.contracts)", otc)
        self.assertIn("cdsnH = T.mountOptional('cdsnames', cdsnBox, { account_id: acct, blocked: state.blocked, note: false,", otc)
        self.assertIn("if (cdsnH && cdsnH.setBlocked) { cdsnH.setBlocked(state.blocked); }", otc)
        self.assertIn("if (cdsnH && cdsnH.unmount) { try { cdsnH.unmount(); } catch (e) { console.error('cdsnames unmount', e); } }", otc)
        # the CDX sub-tab is untouched: its mount, its box, its notes and the four earlier sub-tabs as they were
        self.assertIn("cdsH = T.mountOptional('cdsticket', cdsBox, { account_id: acct, blocked: state.blocked, note: false,", otc)
        self.assertIn("var SUBTABS = [{ key: 'swaps', label: 'Swaps' }, { key: 'options', label: 'Options' }, { key: 'forwards', label: 'Forwards' }, { key: 'credit', label: 'Credit (CDX)' },", otc)
        self.assertIn("'CDX_PROXY', 'CDX_DEAL', 'CDX_COUPON',", otc)
        self.assertEqual((0, 0, 0), js_balance(otc))

    def test_pages_dispatch_a_cds_row_to_the_helpers_guarded(self):
        expect = {
            ("components", "swapticket.js"): ["if (row && row.kind === 'CDS' && T.components.cdsnames) { return T.components.cdsnames.sideText(s, row); }",
                                              "if (c.kind === 'CDS' && T.components.cdsnames) { return T.components.cdsnames.kindShort(c); }",
                                              "if (c.kind === 'CDS' && T.components.cdsnames) { return T.components.cdsnames.nowText(c); }",
                                              "if (row && row.kind === 'CDX') {", "if (c.kind === 'CDX' && (d.index_name"],
            ("pages", "derivatives.js"): ["if (c.kind === 'CDS' && T.components.cdsnames) { return T.components.cdsnames.dealtNow(c); }",
                                          "if (c.kind === 'CDS' && T.components.cdsnames) { return T.components.cdsnames.sensitivity(c); }",
                                          "if (c.kind === 'CDS' && st.data && st.data.credit_event_offered_cds && T.components.cdsnames) {",
                                          "actions.appendChild(T.components.cdsnames.creditButton(st, c, function () { load(st); }));",
                                          "if (c.kind === 'CDS' && T.components.cdsnames) { box.appendChild(T.components.cdsnames.creditSection(c, st)); }",
                                          "if (c.kind === 'CDX' && st.data && st.data.credit_event_offered) {"],
            ("pages", "positions.js"): ["if (k === 'CDS') { return T.components.cdsnames && T.components.cdsnames.swapSide ? T.components.cdsnames.swapSide(r) : (q < 0 ? 'buy protection' : 'sell protection'); }",
                                        "if (k === 'CDX') { return q < 0 ? 'buy protection' : 'sell protection'; }"],
            ("pages", "portfolio.js"): ["if (T.components.cdsnames && T.components.cdsnames.footer) { var cf = T.components.cdsnames.footer(x, def); if (cf) { ex.push(cf); } }",
                                        "if (T.num(x, 'cdx_long_credit') || T.num(x, 'cdx_short_credit'))"],
            ("pages", "risk.js"): ["S.cdsn = T.components.cdsnames && T.components.cdsnames.riskPanel ? T.components.cdsnames.riskPanel(body, { panel: panel, gridIn: gridIn, money: money, pct: pct,",
                                   "if (S.cdsn && S.cdsn.update) { S.cdsn.update(r.single_name_cds || null, names); }",
                                   "S.cdx = panel('credit', 'Credit derivatives (CDX)'"],
            ("pages", "cash.js"): ["cdxcpn: 'CDX coupon', cdscpn: 'CDS coupon',", "/^(SWP|OTC|CDX|CDS)-/.test(core)"],
            ("core.js",): ["var CODE_ACRONYMS = { FX: 1, OTC: 1, CDX: 1, CDS: 1, JTD: 1,"],
        }
        for parts, needles in expect.items():
            text = read(STATIC, *parts)
            for n in needles:
                self.assertIn(n, text, "/".join(parts) + ": " + n)
            self.assertEqual((0, 0, 0), js_balance(text), "/".join(parts))
            # every call into the component is guarded by its presence (a page works unchanged without the file)
            for i, line in enumerate(text.splitlines(), 1):
                if "T.components.cdsnames." in line:
                    guarded = ("T.components.cdsnames &&" in line or "&& T.components.cdsnames)" in line or "&& T.components.cdsnames " in line
                               or line.strip().startswith("actions.appendChild(T.components.cdsnames.creditButton"))
                    self.assertTrue(guarded, "/".join(parts) + ":%d unguarded: %s" % (i, line.strip()[:120]))


if __name__ == "__main__":
    unittest.main()
