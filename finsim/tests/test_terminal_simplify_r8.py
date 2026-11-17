"""FinSim 1.0.7.2 simplify review round 8 (on 0ec05bc): the regression tests of its findings, kept in the terminal suite.

The page (R8-P1, blocker; R8-P2).  A REAL pointer double click on a card's "Open in ticket": its first click hands the order to the
account's Markets page, whose docked order ticket (380 px on the right) then lies under the pointer - its Submit armed once the preview
of the order handed over answers, about 0.35 s later - and its second click landed there: on that Submit (the order was sent, though the
owner never pressed Submit) or on a Markets row (its double click filled the ticket with that row's instrument: the order handed over
was gone).  The Smart Ticket page now drops the rest of the gesture that handed an order over (every press, release, click and double
click with a click count above 1, until the next press of a new gesture) and acts on a click of its own only; the order ticket's Submit
takes a click of its own (the second click of a double click never sends; one that began elsewhere is said).  The real offline server
on port 0 (test_terminal_simplify_r6's world: the three books, the fictional ACME / WIDG printed, the smoke test's invented ACME chain)
and the real pages in a headless Edge / Chrome; the pointer is CDP's (Input.dispatchMouseEvent, with its click count):
  PageTests
    test_a  1280 x 800, "buy 10 ACME limit 50; buy 10 WIDG limit 249; buy 12 WIDG stop 251 limit 252 gtd Dec 18 2026": a double click
            on the middle of card #3's Open in ticket, its second click once the docked ticket's preview of that order is back (inside
            a 500 ms double click): nothing is sent, the ticket holds the order handed over (tests/test_reviews8_page_double_click_open.py
            test_a);
    test_b  1366 x 768, the same without the GTD date, on the part of the button that lies over the docked Submit (test_b);
    test_c  1440 x 900, "buy 10 ACME limit 50": the second click and the double click land on the Markets grid: the ticket keeps BUY 10
            ACME limit 50 (test_c);
    test_d  whatever lies under the pointer: after a one-click hand-off, the rest of a double click (click count 2) on the armed Submit,
            then on a Markets row: nothing sent, the order handed over kept (a and c without the layout);
    test_e  the control: one click hands the order over - Submit armed, nothing sent (test_a2) - and the next click, one of its own on
            Submit, places it once (simplify round 11 fixes: once the 2 s rest of the hand-off click is over - the docked Submit lies
            under the button that click pressed);
    test_f  the order ticket's own rule: a second click (click count 2) on an armed Submit with no click on it before: not sent, said;
            then a click of its own sends it;
    test_g  the control: a double click on Submit itself places the order once and says nothing of its second click.
The property tester (R8-T1 major, R8-T2; tests/test_props8_ticket_send.py at PROPS8_TS_SEED=8880201, property S: the order a Submit
places is the one the boxes show at the click; reduced in tests/test_reviews8_property_tester_ticket_boxes.py).  The trail mode
switched after a trail was typed left the box's number beside the other mode while the other mode's stored value was sent; a quantity
typed with decimals was floored into the order while the box showed it as typed:
    test_h  "buy 10 ACME trail 2%": the mode set to amount keeps 2 as the amount; 1.5 typed and the mode set back to %: the order placed
            is TRAIL 1.5 %, nothing of an amount (test_a; its control test_a2 is the trail typed in its own mode);
    test_i  "buy 10 ACME limit 59", 1.5 typed in the quantity: the box keeps 1.5, Submit is held with the reason, a click, Ctrl+Enter
            and Preview send nothing; 2 typed: BUY 2 ACME limit 59 placed (test_b; its control test_b2);
    test_seed_8880201_actions_1535_1543  the run's first finding: a 2 % trailing stop on ACME, 1.5 set in its quantity box (BUY 1 ACME
            placed on 0ec05bc): held, said, nothing placed;
    test_seed_8880201_actions_1902_1918  its second: BTO ACME Dec 18 2026 55 put, its limit set to 59.50 and 1.5 in its quantity (BTO 1
            placed): held, said.
  TicketSourceTests  pages/smartticket.js and components/ticket.js: where these rules live.
The cover rule (R8-S1; the owner's rule: a call is sold "covered" only on free shares reserved until it is closed, server-side for all
orders; tests/test_props8_cover_walk.py seeds 8880301-8880310, property A on the fractional series only, reduced in tests/test_reviews8_
property_tester_fractional_cover.py).  A call of a fractional multiplier (an adjusted series in the instrument master, ACME4 Dec 18 2026
110 call x 33.5) needed 0 shares (``orders_preview._per_contract``: shares never cover it), so ``covered: true`` was placed and claimed
with no share reserved for it; it is now 400 COVERED_INVALID with the reason:
  FractionalCoverTests (tests/test_terminal_smart_submit's world: T1:US_EXCHANGE, ACME at 100.00 and its invented chain; ACME4 x 33.5
  and ACME1 Dec 18 2026 100 call x 150 put in the master and quoted from the chain row of their terms)
    test_a  no share held: refused at the order route and in the ticket's preview, naming the multiplier, nothing stored (test_a);
    test_b  300 ACME held: refused as covered; sent as a plain call it is placed, never marked covered, and claims none (test_b);
    test_c  the control: the whole multiplier 150 needs 150 free shares, then keeps them (test_c);
    test_seeds_8880301_8880310  the states the walks sent it in (no share; shares free; under a working sale; under calls sold as
            covered of 100 and of 150 shares): refused in each, every claim of a whole multiplier counted as before.
The parse rules (R8-S2; tests/test_props8_parse_oracle.py at PROPS8_PO_SEED=8880101, class U: on 0ec05bc 932 of its 1,454 instructions
with one unusual character were read into an order or had nothing underlined; reduced in tests/test_reviews8_property_tester_unusual_
digits.py).  NFKC made a superscript, subscript, circled or full-width digit an ASCII digit joined to the number beside it, and full-
width letters a ticker.  It is gone, and a number typed against a character not read is one unread token with it:
  UnusualCharacterTests (tests/test_props7_parse_forms.py's world, rebuilt in test_terminal_simplify_r7)
    test_a  "buy 100 ACME limit 99" with superscript cents: underlined, never a limit of 9950 (test_a);
    test_b  "buy 10" with a superscript 2: underlined, never 102 shares (test_b);
    test_c  the controls: a symbol is underlined; dashes, the minus sign, non-breaking spaces and CRLF read as plain text (test_c,
            test_d);
    test_d  every superscript, subscript, circled and full-width digit (and five other digits) in ten places of a number, full-width
            letters in the ticker and the side: underlined where they stand, no order;
    test_seed_8880101_cases  the run's first twelve findings as the seed drew them, and the three that the first fix (no NFKC alone)
            still read as orders - a digit right after a decimal point, where "." ends a clause: each underlined, no order; the clean
            instruction each was drawn from reads with its request.
Fictional ACME / WIDG only; temp homes; no ~/.finsim, no network, no port 8765 / 8865; nothing is sent but where a test says so.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import unittest
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from tests import test_terminal_browser_smoke as SMK
from tests import test_terminal_simplify_r6 as R6
from tests import test_terminal_simplify_r7 as R7
from tests import test_terminal_smart_submit as SUB
from tests.terminal_cdp import NO_BROWSER, Browser, find_browser
from finsim.terminal import config
from finsim.terminal.clock import FakeClock
from finsim.terminal.orders_preview import order_claimed
from finsim.terminal.products import options as _options
from finsim.terminal.quotes.cache import record_from_chain_row
from finsim.terminal.quotes.providers import FakeProvider

TERMINAL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "finsim", "terminal")
STATIC = os.path.join(TERMINAL, "static")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _dec(x):
    try:
        return None if x is None or str(x).strip() == "" else Decimal(str(x).strip())
    except InvalidOperation:
        return str(x)


# ==== the parse rules: unusual characters ==============================================================================================
SUP = "".join(chr(c) for c in (0x2070, 0x00B9, 0x00B2, 0x00B3, 0x2074, 0x2075, 0x2076, 0x2077, 0x2078, 0x2079))     # superscript 0-9
SUBSCRIPT = "".join(chr(0x2080 + i) for i in range(10))                                                            # subscript 0-9
FULL = "".join(chr(0xFF10 + i) for i in range(10))                                                                 # full-width 0-9
CIRCLED = chr(0x24EA) + "".join(chr(0x2460 + i) for i in range(20))                                                # circled 0, 1-20
#: an Arabic-Indic 5, a Devanagari 5, one half, the Roman numeral eight and a mathematical bold 9 (outside the BMP)
OTHER_DIGITS = chr(0x0665) + chr(0x096B) + chr(0x00BD) + chr(0x2167) + chr(0x1D7D7)
UNUSUAL_DIGITS = SUP + SUBSCRIPT + FULL + CIRCLED + OTHER_DIGITS
#: ten places of a number, "{c}" the unusual character: after the quantity and in its place, after a limit's whole part, after its
#: decimal point, after its cents and in its first digit, after a stop, in a price of the Fund book's sale, after a strike, in a
#: future's code
DIGIT_PLACES = ["buy 10{c} ACME", "buy {c}0 ACME", "buy 100 ACME limit 99{c}", "buy 100 ACME limit 99.{c}0 day", "buy 100 ACME limit 99.5{c}",
                "buy 100 ACME limit {c}9.50", "buy 10 ACME stop 9{c}", "sell 10 ACME limit 102.{c}5 in the Fund book",
                "buy 2 ACME Dec 18 2026 115{c} call limit 2.50", "buy 1 ESZ2{c}"]


def _full_width(s: str) -> str:
    return "".join(chr(ord(c) - 0x21 + 0xFF01) if 0x21 <= ord(c) <= 0x7E else c for c in s)


#: full-width letters: the ticker whole, with a limit, in the Fund book's sale; the side; each capital in place of the M of ACME
LETTER_PLACES = ["buy 10 " + _full_width("ACME"), "buy 10 " + _full_width("ACME") + " limit 99.50", "sell 100 " + _full_width("ACME") + " limit 101 in the Fund book",
                 _full_width("buy") + " 10 ACME"] + ["buy 10 AC%sE limit 99" % chr(0xFF21 + i) for i in range(26)]
#: tests/test_props8_parse_oracle.py at PROPS8_PO_SEED=8880101 on 0ec05bc: (case, the instruction drawn, the clean instruction it was
#: drawn from) - the run's first twelve findings as the seed drew them, then cases 1790, 1794 and 2325: a digit right after a decimal
#: point, which the first fix (NFKC removed alone) still read as an order of the price's whole part ("102." ends a clause)
SEED_8880101 = [
    (1, "buy 2 ACME Dec 18 2026 115⁹ call limit 2.50", "buy 2 ACME Dec 18 2026 115 call limit 2.50"),
    (2, "buy 1 ESZ26₉", "buy 1 ESZ26"),
    (3, "sell 100 ＡＣＭＥ limit 101 in the Fund book", "sell 100 ACME limit 101 in the Fund book"),
    (4, "buy 5 ACME Dec 18 2026 95④ call gtc", "buy 5 ACME Dec 18 2026 95 call gtc"),
    (5, "buy 2 NQM27⁴⁴ stop 6400", "buy 2 NQM27 stop 6400"),
    (6, "sell 300 ＡＣＭＥ limit 101 gtc in the Fund book", "sell 300 ACME limit 101 gtc in the Fund book"),
    (10, "buy 25 ACME at 97⑦", "buy 25 ACME at 97"),
    (12, "buy 25 ACME limit 9₉", "buy 25 ACME limit 99"),
    (21, "buy 5 ＡＣＭＥ Dec 18 2026 95 call limit 2.50 day", "buy 5 ACME Dec 18 2026 95 call limit 2.50 day"),
    (23, "sell 100₂₃ ACME in the Fund book", "sell 100 ACME in the Fund book"),
    (30, "buy 10１ ACME gtc", "buy 10 ACME gtc"),
    (31, "buy 10④ ACME", "buy 10 ACME"),
    (1790, "sell 10 ACME limit 102.②5 in the Fund book", "sell 10 ACME limit 102.25 in the Fund book"),
    (1794, "buy 100 ACME limit 99.５0 day", "buy 100 ACME limit 99.50 day"),
    (2325, "buy 100 ACME limit 99.₅0 gtc", "buy 100 ACME limit 99.50 gtc"),
]
#: the documented characters (docs/SMART_TICKET.md "Normalising"): (typed with them, the same in plain text)
DOCUMENTED = [("buy 1 ACME 2026–12—18 100 call limit 4.90", "buy 1 ACME 2026-12-18 100 call limit 4.90"),
              ("buy 1 ACME 2026−12−18 100 call", "buy 1 ACME 2026-12-18 100 call"),
              ("buy 100 ACME limit 99.50", "buy 100 ACME limit 99.50"),
              ("buy 100 ACME limit 99.50\r\nbuy 10 ACME\tlimit 98", "buy 100 ACME limit 99.50\nbuy 10 ACME limit 98")]


class UnusualCharacterTests(unittest.TestCase):
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
        """The pure parse route the page and the Shaffer System unified app call."""
        code, out, _h = self.rt.router.handle("POST", "/api/smart-ticket/parse?book=P", None, {"text": text}, "127.0.0.1", None)
        self.assertEqual(200, code, out)
        return out

    @staticmethod
    def reqs(out: dict) -> list:
        return [json.dumps(l.get("request"), sort_keys=True, default=str) for l in out["legs"]]

    def not_underlined(self, text: str, at: int) -> list:
        """[] when the character at ``at`` is underlined as not read and no order of the instruction has a request (what the unified
        app's relay, which blocks an instruction with anything unparsed, and the page, which opens no card then, both rely on)."""
        out = self.read(text)
        spans = [u["span"] for u in out["unparsed"]]
        sendable = [l["request"] for l in out["legs"] if l.get("request") is not None]
        if any(s[0] <= at < s[1] for s in spans) and not sendable and out["ok"] is False:
            return []
        return [f"{text!r} read as {out['text']!r}: unparsed {spans}, ok {out['ok']}, sendable {json.dumps(sendable)[:200]}"]

    def test_a_superscript_cents_are_underlined_never_a_limit_of_9950(self):
        text = "buy 100 ACME limit 99" + SUP[5] + SUP[0]
        self.assertEqual([], self.not_underlined(text, 21))
        out = self.read(text)
        self.assertEqual("buy 100 ACME limit 99??", out["text"])
        self.assertIn([19, 23], [u["span"] for u in out["unparsed"]])                 # the price with its unread cents, one token
        plain = self.read("buy 100 ACME limit 99.50")
        self.assertEqual([("BUY", 100, "LIMIT", "99.50")], [(l["request"]["side"], l["request"]["qty"], l["request"]["order_type"], l["request"]["limit_px"])
                                                             for l in plain["legs"]])

    def test_b_a_superscript_digit_after_a_quantity_is_underlined_never_102_shares(self):
        text = "buy 10" + SUP[2] + " ACME"
        self.assertEqual([], self.not_underlined(text, 6))
        self.assertIn([4, 7], [u["span"] for u in self.read(text)["unparsed"]])
        plain = self.read("buy 10 ACME")
        self.assertEqual([("BUY", 10)], [(l["request"]["side"], l["request"]["qty"]) for l in plain["legs"]])

    def test_c_controls_a_symbol_is_underlined_and_the_documented_characters_read_as_plain_text(self):
        self.assertEqual([], self.not_underlined("buy 10 ACME ® limit 99", 12))
        for odd, plain in DOCUMENTED:
            o, p = self.read(odd), self.read(plain)
            self.assertEqual([], o["unparsed"], odd)
            self.assertTrue(o["legs"] and all(l.get("request") is not None for l in o["legs"]), (odd, o["legs"]))
            self.assertEqual(self.reqs(p), self.reqs(o), odd)

    def test_d_every_unusual_digit_and_full_width_letter_is_underlined_where_it_stands(self):
        bad, n = [], 0
        for place in DIGIT_PLACES:
            at = place.index("{c}")
            for c in UNUSUAL_DIGITS:
                n += 1
                bad += self.not_underlined(place.replace("{c}", c), at)
        for text in LETTER_PLACES:
            n += 1
            bad += self.not_underlined(text, next(i for i, ch in enumerate(text) if ord(ch) > 127))
        self.assertGreater(n, 500)
        self.assertEqual([], bad[:8], f"{len(bad)} of {n} instructions read an unusual character into an order or left it unread unsaid")

    def test_seed_8880101_cases(self):
        bad = []
        for case, text, base in SEED_8880101:
            at = next(i for i, ch in enumerate(text) if ord(ch) > 127)
            bad += [f"case {case}: {m}" for m in self.not_underlined(text, at)]
            clean = self.read(base)
            if not clean["legs"] or any(l.get("request") is None for l in clean["legs"]) or clean["unparsed"]:
                bad.append(f"case {case}: the clean {base!r} does not read with its request")
        self.assertEqual([], bad)


# ==== the cover rule: a call of a fractional multiplier ================================================================================
FRACTIONAL = "ACME4261218C00110000"          # ACME4 Dec 18 2026 110 call, x 33.5
WHOLE = "ACME1261218C00100000"               # ACME1 Dec 18 2026 100 call, x 150
#: the master's adjusted ACME series: id -> (multiplier, strike); each quoted from the chain row of its terms
ADJUSTED = {FRACTIONAL: (Decimal("33.5"), Decimal("110")), WHOLE: (Decimal("150"), Decimal("100"))}
FRACTION_SAID = (f"not placed as covered: {FRACTIONAL} has the multiplier 33.5, and shares never cover a contract with a fractional multiplier"
                 " (FinSim's margin gives it none) - send it as a plain call, or sell a contract of whole shares as covered")


class FractionalCoverTests(SUB.SmartSubmitBase):
    def setUp(self):
        super().setUp()
        base = _options.ensure_option(self.rt, SUB.acme("C", 100))
        with self.rt.db.writer() as tx:
            for iid, (mult, strike) in ADJUSTED.items():
                self.rt.instruments.upsert(tx, instrument_id=iid, kind="OPTION", symbol=iid, name=f"ACME adjusted series {iid[4]}", currency="USD",
                                           multiplier=mult, session_group=base.session_group, underlying_id="ACME", expiry=SUB.E1, strike=strike, right="C",
                                           style=base.style, exercise=base.exercise, account_kinds=list(base.account_kinds), cboe_symbol=iid,
                                           spec=dict(base.spec or {}))
        snap, now = self.rt.chains.peek("ACME"), self.rt.clock.epoch()
        for iid, (_m, strike) in ADJUSTED.items():
            self.rt.cache.put(record_from_chain_row(self.rt.instruments.get(iid), snap, snap.by_occ(SUB.acme("C", strike))), now)
        self.n = 0

    def post(self, **b) -> tuple:
        """An order through the normal order route (its own request id), as the order ticket or the unified app sends it."""
        self.n += 1
        b = dict({"account_id": SUB.ACCT, "tif": "DAY", "order_type": "MARKET"}, **b)
        code, out, _h = self.send(b, f"ui:simplify-r8-cover-{self.n:04d}")
        return code, (out.get("order") or {}) if code == 200 else (out.get("error") or {})

    def preview(self, **b) -> dict:
        code, out, _h = self.call("POST", "/api/orders/preview?book=T", dict({"account_id": SUB.ACCT, "tif": "DAY", "order_type": "MARKET"}, **b))
        self.assertEqual(200, code, out)
        return out

    def match(self) -> None:
        self.rt.matcher.on_quotes(["ACME"] + list(ADJUSTED) + [SUB.acme("C", 100)])
        self.rt.matcher.sweep()

    def held(self) -> int:
        rows = self.q("SELECT qty FROM v_positions WHERE account_id=? AND instrument_id='ACME'", (SUB.ACCT,))
        return int(rows[0]["qty"]) if rows else 0

    def hold(self, n: int) -> None:
        h0 = self.held()
        code, o = self.post(instrument_id="ACME", side="BUY", qty=n)
        self.assertEqual(200, code, o)
        self.match()
        self.assertEqual(h0 + n, self.held())

    def claimed(self, oid: str) -> bool:
        with self.rt.db.reader() as conn:
            row = conn.execute("SELECT * FROM orders WHERE order_id=?", (oid,)).fetchone()
            return bool(order_claimed(conn, row))

    def refused_as_covered(self, qty: int, where: str) -> None:
        n0 = len(self.q("SELECT order_id FROM orders"))
        code, e = self.post(instrument_id=FRACTIONAL, side="STO", qty=qty, order_type="LIMIT", limit_px="9.00", tif="GTC", covered=True)
        self.assertEqual((400, "COVERED_INVALID", FRACTION_SAID), (code, e.get("code"), e.get("message")), where)
        self.assertEqual(n0, len(self.q("SELECT order_id FROM orders")), f"{where}: nothing is stored")
        pv = self.preview(instrument_id=FRACTIONAL, side="STO", qty=qty, order_type="LIMIT", limit_px="9.00", tif="GTC", covered=True)
        self.assertEqual((False, ["COVERED_INVALID"]), (pv.get("ok"), [b["code"] for b in pv.get("blockers") or []]), f"{where}: the ticket's preview")

    def test_a_a_fractional_call_is_never_placed_as_covered_with_no_share_held(self):
        self.assertEqual(0, self.held())
        self.refused_as_covered(1, "no ACME held")

    def test_b_with_shares_held_it_is_refused_as_covered_and_a_plain_call_claims_nothing(self):
        self.hold(300)
        self.refused_as_covered(2, "300 ACME held")
        code, o = self.post(instrument_id=FRACTIONAL, side="STO", qty=2, order_type="LIMIT", limit_px="9.00", tif="GTC")
        self.assertEqual(200, code, o)
        self.assertFalse(self.claimed(o["order_id"]), "a plain call is never marked covered")
        code, s = self.post(instrument_id="ACME", side="SELL", qty=300, order_type="LIMIT", limit_px="150.00", tif="GTC")
        self.assertEqual(200, code, f"no share is reserved for the plain call: {s}")

    def test_c_control_a_whole_multiplier_reserves_its_shares(self):
        self.hold(100)
        code, e = self.post(instrument_id=WHOLE, side="STO", qty=1, order_type="LIMIT", limit_px="9.00", tif="GTC", covered=True)
        self.assertEqual((409, "NOT_COVERED"), (code, e.get("code")), "1 contract x 150 needs 150 shares; 100 are held")
        self.hold(50)
        code, o = self.post(instrument_id=WHOLE, side="STO", qty=1, order_type="LIMIT", limit_px="9.00", tif="GTC", covered=True)
        self.assertEqual(200, code, o)
        self.assertTrue(self.claimed(o["order_id"]))
        code, e = self.post(instrument_id="ACME", side="SELL", qty=1)
        self.assertEqual(400, code, "the 150 held are reserved for the call sold as covered")
        self.assertIn("SHARES_RESERVED", str(e.get("message")))

    def test_seeds_8880301_8880310_every_state_the_walks_sent_it_in(self):
        """The walks sent ``covered: true`` on ACME4 43 times (accepted 43 on 0ec05bc): with no share, with shares free, under a working
        sale and under calls sold as covered of whole multipliers.  Each refused; the whole multipliers' claims count as before."""
        self.refused_as_covered(1, "no share")
        self.hold(400)
        self.refused_as_covered(3, "400 free")
        code, std = self.post(instrument_id=SUB.acme("C", 100), side="STO", qty=1, order_type="LIMIT", limit_px="9.00", tif="GTC", covered=True)
        self.assertEqual(200, code, std)                                                  # 100 shares claimed by a standard call
        code, sale = self.post(instrument_id="ACME", side="SELL", qty=100, order_type="LIMIT", limit_px="150.00", tif="GTC")
        self.assertEqual(200, code, sale)                                                 # 100 more in a working sale
        self.refused_as_covered(1, "under a covered call of 100 and a working sale")
        code, e = self.post(instrument_id=WHOLE, side="STO", qty=2, order_type="LIMIT", limit_px="9.00", tif="GTC", covered=True)
        self.assertEqual((409, "NOT_COVERED"), (code, e.get("code")), "2 x 150 = 300 needed, 200 free")
        code, w = self.post(instrument_id=WHOLE, side="STO", qty=1, order_type="LIMIT", limit_px="9.00", tif="GTC", covered=True)
        self.assertEqual(200, code, w)                                                    # 150 of the 200 free
        self.refused_as_covered(5, "under covered calls of 100 and of 150 and a working sale")
        self.assertEqual([True, True], [self.claimed(std["order_id"]), self.claimed(w["order_id"])])
        code, e = self.post(instrument_id="ACME", side="SELL", qty=51)
        self.assertEqual(400, code, "50 free: 400 held - 100 in the sale - 100 and 150 for the calls")
        self.assertIn("SHARES_RESERVED", str(e.get("message")))
        code, o = self.post(instrument_id="ACME", side="SELL", qty=50)
        self.assertEqual(200, code, o)


# ==== the page: a double click on Open in ticket, the order ticket's boxes ============================================================
TEXT_A = "buy 10 ACME limit 50; buy 10 WIDG limit 249; buy 12 WIDG stop 251 limit 252 gtd Dec 18 2026"
TEXT_B = "buy 10 ACME limit 50; buy 10 WIDG limit 249; buy 12 WIDG stop 251 limit 252"
TEXT_C = "buy 10 ACME limit 50"
DOUBLE_CLICK_S = 0.5                     # Windows' default double-click time (500 ms): two clicks closer than this are a double click
SUBMIT_ALONE = "not sent: Submit takes a click of its own (that was the second click of a double click) - click Submit once to send"
ORDER_COLS = "order_id, account_id, instrument_id, side, qty, order_type, limit_px, stop_px, trail_amt, trail_pct, tif, status"
#: the order ticket as the owner sees it
TICKET = r"""(function () {
  function v(id) { var e = document.getElementById(id); return e ? e.value : null; }
  var sb = document.getElementById('ticket-submit-btn');
  return { hash: location.hash, inst: v('ticket-instrument'), side: v('ticket-side'), qty: v('ticket-qty'), type: v('ticket-type'), stop: v('ticket-stop'),
           limit: v('ticket-limit'), trail_mode: v('ticket-trail-mode'), trail: v('ticket-trail'), tif: v('ticket-tif'), gtd: v('ticket-gtd'),
           submit: !!(sb && !sb.disabled), title: sb ? sb.title : '', status: (document.getElementById('ticket-status') || {}).textContent || '' };
})()"""
#: what is under the point (x, y): the element, its button (id or words, enabled or not), its grid row, the page
UNDER = r"""(function (x, y) {
  var e = document.elementFromPoint(x, y); if (!e) { return null; }
  var b = e.closest('button'), tr = e.closest('tr[data-i]');
  return { el: e.id || e.tagName, button: b ? (b.id || String(b.textContent || '').trim()) : null, enabled: b ? !b.disabled : null,
           row: tr ? String((tr.querySelector('td') || {}).innerText || '').trim() : null, page: T.router.current ? T.router.current.page : null };
})(%f, %f)"""
OPEN_RECT = r"""(function () {
  var b = document.querySelector('#smart-cards [data-i="%d"] [data-act="open"]'); if (!b) { return null; } var q = b.getBoundingClientRect();
  return { left: q.left, top: q.top, right: q.right, bottom: q.bottom, disabled: !!b.disabled };
})()"""
RECT = r"""(function () { var e = document.querySelector(%s); if (!e) { return null; } var q = e.getBoundingClientRect();
  return { left: q.left, top: q.top, right: q.right, bottom: q.bottom }; })()"""
#: the number of the order the ticket shows (ticket.js st.gen: a new order - fresh, a new instrument ... - takes the next number)
GEN = "(function () { var s = T.components.ticket && T.components.ticket.state ? T.components.ticket.state() : null; return s ? s.gen : null; })()"
#: the ticket shows a NEW order (not the number ``gen0``) on ``inst`` on an account page and its preview is back with no blocker (what the
#: ticket arms Submit with; whether Submit is armed is not asked)
PREVIEWED = r"""(function () { var s = T.components.ticket && T.components.ticket.state ? T.components.ticket.state() : null; var p = s && s.preview;
  return !!(location.hash.indexOf('#/a/') === 0 && s && s.gen !== %s && s.instrument_id === %s && p && !s.previewing && !s.dirty && p.ok !== false
            && !(p.blockers && p.blockers.length)); })()"""
GRID_THERE = "location.hash.indexOf('#/a/') === 0 && location.hash.indexOf('/markets') > 0 && document.querySelectorAll('#page tr[data-i]').length > 3"
FIRST_ROW_CELL = "(function () { var c = document.querySelector('#page tr[data-i] td'); if (!c) { return null; } var q = c.getBoundingClientRect();" \
                 " return { left: q.left, top: q.top, right: q.right, bottom: q.bottom }; })()"
TRAIL_STATE = "(function () { var s = T.components.ticket.state(); return [s.trail_mode, s.trail_amt, s.trail_pct]; })()"
ARMED = "!!document.querySelector('#ticket-submit-btn') && !document.querySelector('#ticket-submit-btn').disabled"


def _overlap(a, b):
    """The box two boxes share ({left, top, right, bottom}), or None."""
    if not a or not b:
        return None
    x0, y0, x1, y1 = max(a["left"], b["left"]), max(a["top"], b["top"]), min(a["right"], b["right"]), min(a["bottom"], b["bottom"])
    return {"left": x0, "top": y0, "right": x1, "bottom": y1} if x0 < x1 and y0 < y1 else None


def _middle(r):
    return (r["left"] + r["right"]) / 2.0, (r["top"] + r["bottom"]) / 2.0


@unittest.skipUnless(find_browser(), NO_BROWSER)
class PageTests(unittest.TestCase):
    """One offline server (port 0, a temp home, test_terminal_simplify_r6's world) with the smoke test's invented ACME chain cached, and
    one headless browser for the class, both closed in tearDownClass."""
    last_submit = 0.0                    # when a test last clicked Submit (the ticket's own rule looks 5 s back)

    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp(prefix="fst-simplify-r8-page-")
        cls.env = {k: os.environ.get(k) for k in (config.HOME_ENV, config.DB_ENV)}
        os.environ[config.HOME_ENV] = cls.home
        os.environ[config.DB_ENV] = os.path.join(cls.home, "terminal.db")
        cls.server = cls.browser = None
        try:
            clock = FakeClock(SMK.AT)
            cls.fp = FakeProvider(clock)
            R6._seed6(cls.home, clock, cls.fp)
            cls.server = R6._Server6(cls.home, FakeClock(SMK.AT), cls.fp).start()
            SMK.BrowserSmokeTests._served_chain(cls)                     # the chain as the quote worker caches a fetched one
            cls.browser = Browser(find_browser(), 1440, 900)
            cls.sid = cls.browser.page("about:blank")
            cls.browser.navigate(cls.sid, cls.server.url)
            cls.browser.wait(cls.sid, R6.LOADED, timeout=45)
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

    # the page as the owner uses it (test_terminal_simplify_r6's helpers)
    js = R6.AnswerFencePageTests.js
    wait = R6.AnswerFencePageTests.wait
    enter_key = R6.AnswerFencePageTests.enter_key
    fresh_smart = R6.AnswerFencePageTests.fresh_smart
    read = R6.AnswerFencePageTests.read

    def tidy(self) -> None:
        def undo():
            self.js("(function () { localStorage.removeItem(%s); sessionStorage.removeItem(%s); location.hash = '#/dashboard'; return true; })()"
                    % (json.dumps(R6.STORE_KEY), json.dumps(R6.WIN_KEY)))
            self.browser.pump(0.6)
            self.browser.viewport(self.sid, 1440, 900)
        self.addCleanup(undo)

    def orders(self) -> list:
        with self.server.rt.db.reader() as conn:
            return [dict(r) for r in conn.execute(f"SELECT {ORDER_COLS} FROM orders ORDER BY rowid").fetchall()]

    def order_posts(self, since: int) -> list:
        """The requests the page sent since ``since`` that place, change or cancel an order (under /api/orders, but its preview)."""
        out = []
        for e in self.browser._events(self.sid, since):
            if e.get("method") == "Network.requestWillBeSent":
                r = (e.get("params") or {}).get("request") or {}
                p = urlsplit(r.get("url") or "").path
                if r.get("method") not in ("GET", "HEAD", "OPTIONS") and p.startswith("/api/orders") and p != "/api/orders/preview":
                    out.append((r.get("method"), p))
        return out

    def no_orders(self, start: int, before: list, why: str = "") -> None:
        self.assertEqual([], self.order_posts(start), f"the page sent an order request {why}")
        self.assertEqual(before, self.orders(), f"an order was placed {why}")
        self.assertEqual([], self.browser.errors(self.sid, start))

    def wait_orders(self, before: list, timeout: float = 10.0) -> list:
        deadline = time.time() + timeout
        while time.time() < deadline and len(self.orders()) == len(before):
            self.browser.pump(0.2)
        self.browser.pump(0.3)
        return self.orders()[len(before):]

    # ---- the pointer and the keyboard, as CDP sends them -----------------------------------------------------------------------------
    def mouse(self, typ: str, x: float, y: float, count: int = 0) -> None:
        """One pointer event at (x, y): mouseMoved, or a left mousePressed / mouseReleased with its click count."""
        p = {"type": typ, "x": x, "y": y}
        if typ == "mouseMoved":
            p.update(button="none", buttons=0)
        else:
            p.update(button="left", buttons=1 if typ == "mousePressed" else 0, clickCount=count)
        self.browser.call("Input.dispatchMouseEvent", p, session=self.sid)

    def click_xy(self, x: float, y: float, count: int = 1) -> None:
        """A click at (x, y): the pointer moved there, pressed and released (click count ``count``)."""
        self.mouse("mouseMoved", x, y)
        self.mouse("mousePressed", x, y, count)
        self.mouse("mouseReleased", x, y, count)

    def click_sel(self, sel: str, count: int = 1) -> None:
        r = self.js(RECT % json.dumps(sel))
        self.assertIsNotNone(r, sel)
        if sel == "#ticket-submit-btn":
            type(self).last_submit = time.time()
        self.click_xy(*_middle(r), count=count)

    def double_click(self, x: float, y: float, ready: str, min_gap: float = 0.0):
        """A double click at (x, y): one click, then the second press and release (click count 2) once the JS condition ``ready`` holds
        and at least ``min_gap`` s after the first - at the latest just inside DOUBLE_CLICK_S, ready or not (never a skip: the rule
        is that nothing is sent).  Returns (seconds between the two clicks, whether ``ready`` held, what was under the pointer)."""
        self.click_xy(x, y)
        t0 = time.monotonic()
        ok = False
        while time.monotonic() - t0 < DOUBLE_CLICK_S - 0.06:
            if time.monotonic() - t0 >= min_gap and self.js(ready):
                ok = True
                break
            time.sleep(0.005)
        under = self.js(UNDER % (x, y))
        gap = time.monotonic() - t0
        self.mouse("mousePressed", x, y, 2)
        self.mouse("mouseReleased", x, y, 2)
        return gap, ok, under

    def key(self, key: str, code: str, vk: int, mods: int = 0) -> None:
        base = {"key": key, "code": code, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk, "modifiers": mods}
        self.browser.call("Input.dispatchKeyEvent", dict(base, type="rawKeyDown"), session=self.sid)
        self.browser.call("Input.dispatchKeyEvent", dict(base, type="keyUp"), session=self.sid)

    def type_into(self, sel: str, text: str) -> None:
        """The box emptied and ``text`` typed into it (CDP Input.insertText: one input event, as a paste or the last key typed)."""
        self.js("(function () { var b = document.querySelector(%s); b.focus(); if (b.type === 'number') { b.value = ''; } else { b.select(); } return true; })()"
                % json.dumps(sel))
        self.browser.call("Input.insertText", {"text": text}, session=self.sid)
        self.browser.pump(0.15)

    def set_box(self, sel: str, value: str) -> None:
        """A box set as tests/test_props8_ticket_send.py's walk sets it: the value, then its input and change events."""
        self.js("(function () { var f = document.querySelector(%s); f.focus(); f.value = %s; f.dispatchEvent(new Event('input', { bubbles: true }));"
                " f.dispatchEvent(new Event('change', { bubbles: true })); return true; })()" % (json.dumps(sel), json.dumps(value)))
        self.browser.pump(0.15)

    def select(self, sel: str, value: str) -> None:
        self.js("(function () { var m = document.querySelector(%s); m.focus(); m.value = %s; m.dispatchEvent(new Event('change', { bubbles: true })); return true; })()"
                % (json.dumps(sel), json.dumps(value)))
        self.browser.pump(0.15)

    def armed(self, timeout: float = 30.0) -> bool:
        try:
            self.wait(ARMED, timeout=timeout)
            return True
        except Exception:
            return False

    # ---- the Smart Ticket's hand-off -----------------------------------------------------------------------------------------------
    def open_rect(self, idx: int) -> dict:
        r = self.js(OPEN_RECT % idx)
        self.assertTrue(r and not r["disabled"], f"card #{idx + 1}'s Open in ticket is not there to click: {r}")
        return r

    def hand_once(self, text: str, cards: int, idx: int, inst: str):
        """Card #idx+1's order handed over once (Open in ticket clicked from script, nothing sent) until its preview is back with no blocker
        - the fictional quotes are seeded 2 minutes old, so the first preview of a listing in a new temp home can answer QUOTE_STALE until
        the ticket previews it again; returns where the docked ticket's Submit is for that order."""
        self.fresh_smart("P")
        self.read(text, cards)
        gen0 = self.js(GEN)
        self.js("document.querySelector('#smart-cards [data-i=\"%d\"] [data-act=\"open\"]').click(); true" % idx)
        self.wait(PREVIEWED % (json.dumps(gen0), json.dumps(inst)), timeout=40)
        self.browser.pump(0.5)
        return self.js(RECT % json.dumps("#ticket-submit-btn"))

    def double_click_open(self, text: str, cards: int, idx: int, ready_for, point=None, min_gap: float = 0.0):
        """``text`` read; a double click on card #idx+1's Open in ticket - at ``point(rect)`` or the middle of the button - whose second click
        comes once ``ready_for(gen0)`` holds.  Returns (the button's box, (x, y), gap, ready, under)."""
        self.fresh_smart("P")
        self.read(text, cards)
        r = self.open_rect(idx)
        x, y = point(r) if point else _middle(r)
        self.assertTrue(r["left"] < x < r["right"] and r["top"] < y < r["bottom"], f"the point {(x, y)} is not on the button {r}")
        gap, ok, under = self.double_click(x, y, ready_for(self.js(GEN)), min_gap=min_gap)
        self.browser.pump(2.0)                                         # what the second click could start has its answer
        return r, (x, y), gap, ok, under

    def open_card(self, text: str, cards: int, inst: str) -> None:
        """``text`` read on #/smart and its first card opened in the ticket (docked, filled, previewed: Submit armed)."""
        self.fresh_smart("P")
        self.read(text, cards)
        self.js("document.querySelector('#smart-cards [data-i=\"0\"] [data-act=\"open\"]').click(); true")
        self.wait("location.hash.indexOf('#/a/') === 0 && !!(T.dock && T.dock.visible()) && !!document.querySelector('#ticket-instrument')"
                  " && document.querySelector('#ticket-instrument').value === %s" % json.dumps(inst), timeout=30)
        self.assertTrue(self.armed(40), self.js(TICKET))
        self.browser.pump(0.3)

    # ---- a double click on Open in ticket ------------------------------------------------------------------------------------------
    def test_a_a_double_click_on_open_in_ticket_never_sends_the_order(self):
        self.tidy()
        size = (1280, 800)
        self.browser.viewport(self.sid, *size)
        before, start = self.orders(), self.browser.mark()
        self.hand_once(TEXT_A, 3, 2, "WIDG")
        r, pt, gap, ok, under = self.double_click_open(TEXT_A, 3, 2, lambda gen0: PREVIEWED % (json.dumps(gen0), json.dumps("WIDG")))
        self.no_orders(start, before, f"by a double click at {size} (second click {gap:.2f} s after the first, the page ready: {ok}) landing on {under}")
        t = self.js(TICKET)
        self.assertEqual(("BUY", "12", "WIDG", "STOP_LIMIT", "251", "252", "GTD"), (t["side"], t["qty"], t["inst"], t["type"], t["stop"], t["limit"], t["tif"]), t)

    def test_b_a_double_click_on_the_part_of_open_in_ticket_over_submit_never_sends_the_order(self):
        self.tidy()
        size = (1366, 768)
        self.browser.viewport(self.sid, *size)
        before, start = self.orders(), self.browser.mark()
        sb = self.hand_once(TEXT_B, 3, 2, "WIDG")                       # where Submit is for card #3's order (nothing sent)

        def over_submit(r):
            ov = _overlap(r, sb)
            return _middle(ov) if ov else _middle(r)
        r, pt, gap, ok, under = self.double_click_open(TEXT_B, 3, 2, lambda gen0: PREVIEWED % (json.dumps(gen0), json.dumps("WIDG")), point=over_submit)
        self.no_orders(start, before, f"by a double click at {size} on {tuple(round(v) for v in pt)} (second click {gap:.2f} s after the first, the"
                                      f" page ready: {ok}) landing on {under}")

    def test_c_a_double_click_on_open_in_ticket_keeps_the_order_it_handed_over(self):
        self.tidy()
        self.browser.viewport(self.sid, 1440, 900)
        before, start = self.orders(), self.browser.mark()
        r, pt, gap, ok, under = self.double_click_open(TEXT_C, 1, 0, lambda gen0: GRID_THERE, min_gap=0.12)
        self.browser.pump(1.0)
        t = self.js(TICKET)
        self.assertEqual(("ACME", "BUY", "10", "LIMIT", "50"), (t["inst"], t["side"], t["qty"], t["type"], t["limit"]),
                         f"the second click ({gap:.2f} s after the first, the grid there: {ok}) landed on {under}; the ticket shows {t}")
        self.no_orders(start, before)

    def test_d_the_rest_of_the_hand_off_gesture_never_reaches_the_account_page(self):
        """Without the layout: after a one-click hand-off, the second press and release of a double click (click count 2) on the armed
        Submit, then on a Markets row - its double click too: nothing reaches them."""
        self.tidy()
        self.browser.viewport(self.sid, 1440, 900)
        before, start = self.orders(), self.browser.mark()
        self.hand_once(TEXT_C, 1, 0, "ACME")
        self.fresh_smart("P")
        self.read(TEXT_C, 1)
        gen0 = self.js(GEN)
        self.click_xy(*_middle(self.open_rect(0)))                       # one click: the order handed over
        self.wait(PREVIEWED % (json.dumps(gen0), json.dumps("ACME")), timeout=40)
        self.assertTrue(self.armed(20), self.js(TICKET))
        sx, sy = _middle(self.js(RECT % json.dumps("#ticket-submit-btn")))
        self.mouse("mousePressed", sx, sy, 2)                            # the rest of that click's gesture, on the armed Submit
        self.mouse("mouseReleased", sx, sy, 2)
        self.browser.pump(1.5)
        self.no_orders(start, before, "by the second click of the hand-off's gesture on the armed Submit")
        self.wait(GRID_THERE, timeout=20)
        rx, ry = _middle(self.js(FIRST_ROW_CELL))
        self.mouse("mousePressed", rx, ry, 2)                            # and on a Markets row (a press, a release, a double click)
        self.mouse("mouseReleased", rx, ry, 2)
        self.browser.pump(1.5)
        t = self.js(TICKET)
        self.assertEqual(("ACME", "BUY", "10", "LIMIT", "50", True), (t["inst"], t["side"], t["qty"], t["type"], t["limit"], t["submit"]), t)
        self.assertTrue(t["hash"].endswith("/markets"), t)
        self.no_orders(start, before)

    def test_e_control_one_click_hands_the_order_over_and_the_next_click_on_submit_places_it(self):
        self.tidy()
        self.browser.viewport(self.sid, 1280, 800)
        before, start = self.orders(), self.browser.mark()
        self.hand_once(TEXT_A, 3, 2, "WIDG")
        self.fresh_smart("P")
        self.read(TEXT_A, 3)
        gen0 = self.js(GEN)
        self.click_xy(*_middle(self.open_rect(2)))
        self.wait(PREVIEWED % (json.dumps(gen0), json.dumps("WIDG")), timeout=20)
        self.assertTrue(self.armed(20), self.js(TICKET))
        # the owner reads the ticket and its preview: simplify round 11 fixes - for 2 s a press on the button the hand-off clicked (here
        # the docked Submit lies under it) is the rest of that click, unless the pointer left the button first (test_terminal_simplify_r11_fix)
        self.browser.pump(2.2)
        t = self.js(TICKET)
        self.assertEqual(("BUY", "12", "WIDG", "STOP_LIMIT", "251", "252", "GTD"), (t["side"], t["qty"], t["inst"], t["type"], t["stop"], t["limit"], t["tif"]), t)
        self.no_orders(start, before)
        self.click_sel("#ticket-submit-btn")                              # a click of its own: a new gesture
        placed = self.wait_orders(before)
        self.browser.pump(1.0)
        self.assertEqual([("WIDG", "BUY", 12, "STOP_LIMIT")], [(o["instrument_id"], o["side"], o["qty"], o["order_type"]) for o in placed], self.js(TICKET))
        self.assertEqual(1, len(self.orders()) - len(before))

    def test_f_a_second_click_on_an_armed_submit_never_sends_and_says_why(self):
        self.tidy()
        self.open_card(TEXT_C, 1, "ACME")
        self.click_sel("#ticket-qty")                                     # a press of a new gesture: the hand-off's is over
        time.sleep(max(0.0, 5.3 - (time.time() - type(self).last_submit)))
        self.assertTrue(self.armed(20), self.js(TICKET))
        before, start = self.orders(), self.browser.mark()
        sx, sy = _middle(self.js(RECT % json.dumps("#ticket-submit-btn")))
        self.mouse("mouseMoved", sx, sy)
        self.mouse("mousePressed", sx, sy, 2)                             # the second click of a double click begun elsewhere
        self.mouse("mouseReleased", sx, sy, 2)
        self.browser.pump(1.5)
        self.no_orders(start, before, "by a second click on Submit")
        self.assertEqual(SUBMIT_ALONE, self.js(TICKET)["status"])
        self.assertTrue(self.armed(20), self.js(TICKET))
        self.browser.pump(0.6)
        self.click_sel("#ticket-submit-btn")                              # one click of its own: placed
        placed = self.wait_orders(before)
        self.assertEqual([("ACME", "BUY", 10, "LIMIT")], [(o["instrument_id"], o["side"], o["qty"], o["order_type"]) for o in placed], self.js(TICKET))

    def test_g_control_a_double_click_on_submit_itself_places_the_order_once(self):
        self.tidy()
        self.open_card(TEXT_C, 1, "ACME")
        before = self.orders()
        sx, sy = _middle(self.js(RECT % json.dumps("#ticket-submit-btn")))
        type(self).last_submit = time.time()
        self.mouse("mouseMoved", sx, sy)
        for count in (1, 2):
            self.mouse("mousePressed", sx, sy, count)
            self.mouse("mouseReleased", sx, sy, count)
        placed = self.wait_orders(before)
        self.browser.pump(2.0)
        self.assertEqual([("ACME", "BUY", 10, "LIMIT")], [(o["instrument_id"], o["side"], o["qty"], o["order_type"]) for o in self.orders()[len(before):]],
                         self.js(TICKET))
        self.assertEqual(1, len(placed))
        self.assertNotEqual(SUBMIT_ALONE, self.js(TICKET)["status"])

    # ---- the order ticket's boxes at Send ------------------------------------------------------------------------------------------
    def test_h_the_trail_typed_stays_the_trail_when_its_mode_changes(self):
        self.tidy()
        self.open_card("buy 10 ACME trail 2%", 1, "ACME")
        t = self.js(TICKET)
        self.assertEqual(("TRAILING_STOP", "pct", "2"), (t["type"], t["trail_mode"], t["trail"]), t)
        self.select("#ticket-trail-mode", "amt")                          # the box's 2 becomes the amount
        t = self.js(TICKET)
        self.assertEqual(("amt", "2", ["amt", "2", ""]), (t["trail_mode"], t["trail"], self.js(TRAIL_STATE)), t)
        self.type_into("#ticket-trail", "1.5")
        self.assertTrue(self.armed(20), self.js(TICKET))
        self.select("#ticket-trail-mode", "pct")                          # and 1.5 the percent
        t = self.js(TICKET)
        self.assertEqual(("pct", "1.5", ["pct", "", "1.5"]), (t["trail_mode"], t["trail"], self.js(TRAIL_STATE)), t)
        self.assertTrue(self.armed(20), self.js(TICKET))
        self.browser.pump(0.4)
        before = self.orders()
        self.click_sel("#ticket-submit-btn")
        placed = self.wait_orders(before)
        self.assertEqual([("ACME", "BUY", 10, "TRAILING_STOP", Decimal("1.5"), None)],
                         [(o["instrument_id"], o["side"], o["qty"], o["order_type"], _dec(o["trail_pct"]), _dec(o["trail_amt"])) for o in placed])

    def held_with_decimal_qty(self, unit: str, setter) -> None:
        """1.5 put in the quantity box: the box keeps it, Submit is held with the reason; a click on Submit, Ctrl+Enter in the ticket and
        Preview send nothing and say it again."""
        before, start = self.orders(), self.browser.mark()
        setter("#ticket-qty", "1.5")
        self.browser.pump(0.6)
        said = f"whole {unit} only: 1.5 typed (a quantity is never rounded) - type a whole number"
        t = self.js(TICKET)
        self.assertEqual(("1.5", False, said), (t["qty"], t["submit"], t["status"]), t)
        self.click_sel("#ticket-submit-btn")                              # disabled: nothing
        self.js("document.querySelector('#ticket-qty').focus(); true")
        self.key("Enter", "Enter", 13, mods=2)                            # the submit key in the ticket
        self.js("document.querySelector('#ticket-preview-btn').click(); true")
        self.browser.pump(1.0)
        self.no_orders(start, before, "with 1.5 in the quantity box")
        t = self.js(TICKET)
        self.assertEqual(("1.5", False, said), (t["qty"], t["submit"], t["status"]), t)

    def test_i_a_quantity_typed_with_decimals_holds_submit_with_the_reason(self):
        self.tidy()
        self.open_card("buy 10 ACME limit 59", 1, "ACME")
        self.held_with_decimal_qty("shares", self.type_into)
        before = self.orders()
        self.type_into("#ticket-qty", "2")                                # a whole number typed: previewed, then placed as shown
        self.assertTrue(self.armed(20), self.js(TICKET))
        self.browser.pump(0.4)
        self.click_sel("#ticket-submit-btn")
        placed = self.wait_orders(before)
        self.assertEqual([("ACME", "BUY", 2, "LIMIT", Decimal("59"))], [(o["instrument_id"], o["side"], o["qty"], o["order_type"], _dec(o["limit_px"])) for o in placed])

    def test_seed_8880201_actions_1535_1543(self):
        """tests/test_props8_ticket_send.py at PROPS8_TS_SEED=8880201, actions 1535-1543 (its first finding on 0ec05bc): a 2 % trailing stop
        on ACME in the ticket, 1.5 set in its quantity box, Preview, Submit - BUY 1 ACME TRAILING_STOP 2 % was placed under a box showing
        1.5.  Held now, said; nothing placed."""
        self.tidy()
        self.open_card("buy 10 ACME trail 2%", 1, "ACME")
        self.held_with_decimal_qty("shares", self.set_box)

    def test_seed_8880201_actions_1902_1918(self):
        """The run's second finding (actions 1902-1918): BTO ACME Dec 18 2026 55 put in the ticket, its limit set to 59.50 and 1.5 set in
        its quantity box - BTO 1 was placed under a box showing 1.5.  Held now, said; nothing placed."""
        self.tidy()
        self.open_card("buy 1 ACME Dec 18 2026 55 put limit 1.50", 1, "ACME261218P00055000")
        self.set_box("#ticket-limit", "59.50")
        self.held_with_decimal_qty("contracts", self.set_box)


class TicketSourceTests(unittest.TestCase):
    def test_the_rest_of_a_hand_off_gesture_is_dropped_and_a_click_acts_alone(self):
        src = _read(os.path.join(STATIC, "pages", "smartticket.js"))
        # (simplify round 10 fixes: the same rule, beside the rest of a hand-off by a click on its own point - test_terminal_simplify_r10_fix;
        # round 11 fixes: on the button clicked, decided at the press for its release - test_terminal_simplify_r11_fix)
        for s in ("var handGesture = false, hand = null, handRest = null, pressDrop = false, pressSay = null;",
                  "if (!(e.detail > 1)) { handGesture = false; }",
                  "pressDrop = inBox || soon || (handGesture && e.detail > 1);",
                  "e.preventDefault(); e.stopImmediatePropagation();",
                  "['mousedown', 'mouseup', 'click', 'dblclick', 'mousemove'].forEach(function (t) { window.addEventListener(t, dropGestureRest, true); });"):
            self.assertEqual(1, src.count(s), s)
        at = src.index("function handTicket(S, acct, fields) {")
        body = src[at:src.index("\n  }\n", at)]
        self.assertLess(body.index("handGesture = true;"), body.index("T.router.go(T.router.account(acct, 'markets'));"))
        at = src.index("function onClick(e) {")
        body = src[at:src.index("\n  }\n", at)]
        self.assertLess(body.index("if (e.detail > 1) { return; }"), body.index("var b = e.target.closest('[data-act]');"))

    def test_submit_takes_a_click_of_its_own_and_the_boxes_are_what_is_sent(self):
        src = _read(os.path.join(STATIC, "components", "ticket.js"))
        # simplify round 10: Submit goes through the shared guard (core.js T.guard) - the second click of a double click never sends and
        # says SUBMIT_ALONE (the guard's words for label 'Submit', 'not sent', 'send') on the ticket's status line
        self.assertIn("ui.submitBtn.addEventListener('click', function (e) { guardedSubmit(e); });", src)
        self.assertIn("function guardedSubmit(ev) { return T.guard('ticket.submit', ev || null, function () { return submit(); }, SUBMIT_GUARD); }", src)
        self.assertIn("var SUBMIT_GUARD = { label: 'Submit', say: function (m) { paintStatus(m, 'err'); } };", src)
        core = _read(os.path.join(STATIC, "core.js"))
        for s in ("not = opts.not || 'not sent', act = opts.act || 'send';",
                  "else if (ev && ev.detail > 1) { msg = not + ': ' + label + ' takes a click of its own (that was the second click of a double click) - click ' + label + ' once to ' + act; }"):
            self.assertEqual(1, core.count(s), s)
        self.assertEqual(SUBMIT_ALONE, "not sent: Submit takes a click of its own (that was the second click of a double click) - click Submit once to send")
        # the trail mode: the box's number becomes the trail of the mode chosen, the other mode's value dropped
        self.assertIn("if (st.trail_mode === 'pct') { st.trail_pct = v; st.trail_amt = ''; } else { st.trail_amt = v; st.trail_pct = ''; }", src)
        # a quantity typed that is not whole: never rounded into the order - validity holds it first, the box is never repainted over it
        v = src.index("function validity() {")
        self.assertLess(src.index("if (st.qty_bad != null) { return 'whole ' + unitLabel() + ' only: ' + st.qty_bad", v), src.index("if (!(st.qty > 0)) { return 'enter a quantity'; }", v))
        self.assertIn("if (st.qty_bad == null && (document.activeElement !== ui.qty || num(ui.qty.value) !== st.qty)) {", src)
        self.assertIn("st.qty_bad = raw !== '' && n != null && n !== Math.floor(n) ? raw : null;", src)
        # every other quantity set clears it: a new order (fresh, resetOrder), a quantity handed over (apply), the steps
        for s in ("qty: 0, qty_bad: null,", "st.qty = 0; st.qty_bad = null; }", "st.qty = Math.max(0, Math.floor(Math.abs(num(f.qty)))); st.qty_bad = null; }",
                  "st.qty = Math.round(st.qty / l) * l; st.qty_bad = null;"):
            self.assertIn(s, src)
        at = src.index("function resetOrder() {")
        self.assertIn("st.qty_bad = null;", src[at:src.index("\n  }\n", at)])

    def test_the_server_rules(self):
        smart = _read(os.path.join(TERMINAL, "smart_ticket.py"))
        self.assertNotIn("unicodedata", smart)                            # no compatibility folding (NFKC) before the map
        tok = smart.index("def tokenize(")
        self.assertLess(smart.index("gm = _GLUED_UNREAD.match(text, i)", tok), smart.index("bm = _BADNUM.match(text, i)", tok))
        cover = _read(os.path.join(TERMINAL, "orders_preview.py"))
        at = cover.index("def cover_check(")
        body = cover[at:cover.index("\ndef ", at + 10)]
        self.assertLess(body.index("frac = [inst for inst, _n in calls if _per_contract(inst.multiplier) == 0]"), body.index("if not calls:\n        return None"))


if __name__ == "__main__":
    unittest.main()
