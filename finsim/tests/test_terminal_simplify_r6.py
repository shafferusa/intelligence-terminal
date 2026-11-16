"""FinSim 1.0.7.2 simplify review round 6 (on f682f5f): the regression tests of its findings, kept in the terminal suite.

Autofill (F1).  A group holding two or more priced strategies, rolls, collars or buy writes quoted only the FIRST price in its NET_PRICE
red flag, and its "without the price" removed only that one: the reading it gave was held again by a price no flag had named.  The flag
now quotes every net price of the group and "without the price" removes all of them:
  NetPriceGroupTests (the corpus world of tests/test_terminal_smart_ticket.py, no browser)
    test_a  five groups of two priced strategies (parentheses, "as a package"; a roll, a collar, the same spread twice): every price
            quoted, one pick of "without the price" reads with no NET_PRICE and changes no order but by its price
            (tests/test_reviews6_autofill_net_price.py test_b);
    test_b  the control: one priced spread beside a priced share order - the flag as before, the share order keeps its limit (test_a);
    test_seed_6660601_first_cases  the property run (tests/test_props6_autofill_group_choices.py, PROPS6_AF_SEED=6660601, 300 cases):
            its 90 disagreements are its groups with two or three net prices; the first twelve, as the seed drew them;
    test_seed_6660201_6660202_cases  the round-3 choice-label property at the round's seeds (PROPS3_CL_SEED=6660201 cases 1093 / 1490,
            6660202 cases 571 / 1362): the flag that offered "without the price" is gone after it.
The property tester (F6): a strategy's legs were placed without the holders of their contract, so an offered account that holds it was
not said ("one that holds the contract says what it holds"):
  StrategyLegHoldingTests (two trading accounts per book: test_terminal_simplify_r5's world - P1:PB_MS holds 2 ACME Dec 18 2026 100
  calls, F1:PB_GS 1 Dec 18 2026 110 call short)
    test_a  a straddle and call spreads read in the Personal and the Fund books: the leg on the held contract offers the PB account
            "(it holds ...)" (tests/test_reviews6_property_tester_findings.py test_a);
    test_b  the control: the same contract as a single order (test_b);
    test_seed_6660101_cases  the parse oracle (tests/test_props6_parse_oracle.py, PROPS6_PO_SEED=6660101): its 20 G-HOLD
            disagreements, cases 63, 623, 681, 1041, 1341, 1493, 1585, 1725, 2037 and 2173, both texts of each.
  OwnTicketAccountTests (F5, the corpus world): a product with its own ticket read in a book without the account that ticket opens in
            (a private company in the Personal or the Fund book: only the Institutional book has a Private Markets account) is the
            red flag ACCOUNT_NOT_IN_BOOK naming that account, so "Open the private-markets ticket" is held with the reason, never a
            pressable button that opens nothing; controls: the Institutional book, FX and bonds in every book.
The page (F2, F3, F4, F5; the real offline server on port 0 with the three books, the fictional ACME / WIDG, an invented EURUSD print,
the fictional bond ACME-31 and private company WIDGCO; the real pages in a headless Edge / Chrome):
  AnswerFencePageTests
    test_a  a large order's question (CONFIRM_REQUIRED) that comes while its ticket is out of sight (the owner went back to the Smart
            Ticket, as the plan says) is never asked there: no dialog, NOT placed, said on the ticket and in a toast naming the order;
            the Enter the page asks for then reads the text again and places nothing (tests/test_reviews6_page_confirm_out_of_sight.py
            test_a, fenced; test_a2 its control);
    test_b  in sight, the question's focus is on Cancel: a stray Enter answers no, nothing is placed;
    test_c  an FX deal refused while its desk is out of sight is said in a toast naming the deal (test_b); in sight the desk says
            "refused: ..." (test_b2);
    test_d  an FX deal's answer that comes after the next card opened the desk (a new deal) is never painted on that deal: a toast
            names the deal it was for;
    test_e  a fresh hand-off puts the bond it names in the bond ticket's box, never words typed there before and not looked up
            (tests/test_props6_no_send.py seed 6660201 rule HO, reduced in tests/test_reviews6_property_tester_own_desks.py test_b;
            test_a its control);
    test_f  a private company read in a book without a Private Markets account (the Personal book): "Open the private-markets ticket"
            disabled with the reason beside it, its click opens nothing (rule HN; test_d); the Institutional book opens it on I1:PRIVATE
            (test_c).
  AnswerFenceSourceTests  the bond ticket and the private-markets ticket fence their answers as the FX desk does; the fill line names
            the bond submitted.
Fictional ACME / WIDG only; temp homes; no ~/.finsim, no network, no port 8765 / 8865; nothing is sent but where a test says so.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
import time
import unittest

from tests import test_terminal_browser_smoke as SMK
from tests import test_terminal_simplify_r5 as R5
from tests import test_terminal_smart_ticket as STT
from tests.terminal_cdp import NO_BROWSER, Browser, find_browser
from tests.terminal_helpers import _Log
from finsim.terminal import books as _books, config, server as _server, smart_ticket as st
from finsim.terminal.clock import FakeClock, epoch_of
from finsim.terminal.quotes.providers import FakeProvider

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "finsim", "terminal", "static")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# ==== autofill: a group's net prices ===================================================================================================
SPREAD = "buy 1 ACME Dec 18 2026 240/250 call spread limit 2"
CONDOR = "sell 1 ACME Dec 18 2026 220/230/250/260 iron condor at 1.50"
ROLL = "roll 2 ACME Dec 18 2026 240 calls to Jan 15 2027 250 at 1.20"
COLLAR = "collar 100 ACME Dec 18 2026 220/260 at 0.50"
#: (book, text, the net prices typed in its one group)
TWO = [("P", f"({SPREAD} and {CONDOR})", ["limit 2", "at 1.50"]),
       ("P", f"{SPREAD} and {CONDOR} as a package", ["limit 2", "at 1.50"]),
       ("P", f"({ROLL} and {SPREAD})", ["at 1.20", "limit 2"]),
       ("F", f"({COLLAR} and {SPREAD})", ["at 0.50", "limit 2"]),
       ("P", f"({SPREAD} and {SPREAD})", ["limit 2", "limit 2"])]
#: tests/test_props6_autofill_group_choices.py at PROPS6_AF_SEED=6660601: (case, book, text, the net prices typed), its first twelve
#: disagreements on f682f5f (all 90 are groups with two or three net prices)
SEED_6660601 = [
    (0, "F", "buy 1 ACME Dec 18 2026 240/250 call spread limit 2 and buy 100 ACME and buy 1 ACME Dec 18 2026 230/240/250 butterfly at 3 as a package",
     ["limit 2", "at 3"]),
    (2, "F", "(buy 1 ACME Dec 18 2026 230/240/250 butterfly limit 2, collar 100 ACME Dec 18 2026 220/260 at 1.50)", ["limit 2", "at 1.50"]),
    (10, "P", "buy 1 ACME Dec 18 2026 240/250 call spread limit 0.75, buy 1 ACME Dec 18 2026 230/240/250 butterfly limit 0.75, buy 1 ACME Dec 18 2026"
              " 230/240/250 butterfly limit 0.75 all or none", ["limit 0.75", "limit 0.75", "limit 0.75"]),
    (16, "P", "sell 1 ACME Dec 18 2026 220/230/250/260 iron condor at 3, buy 1 ACME Dec 18 2026 240/250 call spread at 3 as a package", ["at 3", "at 3"]),
    (18, "F", "collar 100 ACME Dec 18 2026 220/260 at 1.50 and sell 1 ACME Dec 18 2026 220/230/250/260 iron condor and buy 1 ACME Dec 18 2026 230/240/250"
              " butterfly limit 0.75 as one order", ["at 1.50", "limit 0.75"]),
    (25, "P", "sell 1 ACME Dec 18 2026 220/230/250/260 iron condor at 3, collar 100 ACME Dec 18 2026 220/260 limit 2, sell 1 ACME Dec 18 2026"
              " 220/230/250/260 iron condor at 3 as one order", ["at 3", "limit 2", "at 3"]),
    (26, "F", "(buy write 100 ACME Dec 18 2026 240 at 1.50, buy 1 ACME Dec 18 2026 240 straddle at 1.50)", ["at 1.50", "at 1.50"]),
    (29, "P", "(buy 1 ACME Dec 18 2026 240/250 call spread at 1.50 and sell 1 ACME Dec 18 2026 220/230/250/260 iron condor at 3)", ["at 1.50", "at 3"]),
    (31, "F", "buy 1 ACME Dec 18 2026 240/250 call spread at 3, buy 1 ACME Dec 18 2026 230/240/250 butterfly at 3 and collar 100 ACME Dec 18 2026 220/260"
              " limit 2 as a package", ["at 3", "at 3", "limit 2"]),
    (34, "P", "roll 2 ACME Dec 18 2026 240 calls to Jan 15 2027 250 at 3 and buy 2 ACME Dec 18 2026 240 calls limit 3.20 and roll 2 ACME Dec 18 2026 240"
              " calls to Jan 15 2027 250 limit 2 as one order", ["at 3", "limit 2"]),
    (42, "P", "(sell 1 ACME Dec 18 2026 220/230/250/260 iron condor at 3 and buy 1 ACME Dec 18 2026 240/250 call spread limit 0.75 and buy 1 ACME"
              " Dec 18 2026 240/250 call spread at 3)", ["at 3", "limit 0.75", "at 3"]),
    (43, "P", "buy 1 ACME Dec 18 2026 230/240/250 butterfly limit 2, buy 1 ACME Dec 18 2026 240 straddle limit 2 all or none", ["limit 2", "limit 2"])]
#: tests/test_props3_autofill_choice_labels.py: (seed:case, book, text) - property G NET_PRICE on f682f5f
SEED_PROPS3 = [
    ("6660201:1093", "P", "(buy 1 ACME Dec 18 2026 240/250 call spread limit 2 and buy 1 ACME Dec 18 2026 240/250 call spread limit 2)"),
    ("6660201:1490", "P", "sell 1 ACME Dec 18 2026 220/230/250/260 iron condor at 1.50, sell half GIZM, sell 1 ACME Dec 18 2026 220/230/250/260 iron condor"
                          " at 1.50 as a package in the Fund book"),
    ("6660202:571", "P", "(buy write 100 ACME Dec 18 2026 240 limit 228 and buy write 100 ACME Dec 18 2026 240 limit 228) in the Fund and Personal books"),
    ("6660202:1362", "F", "(buy write 100 ACME Dec 18 2026 240 limit 228, sell 1 ACME Dec 18 2026 220/230/250/260 iron condor at 1.50, sell 1,5 ACME)")]
KEEP = ("book", "account_id", "side", "qty", "tif", "gtd_date")
PRICE_KEYS = ("order_type", "limit_px", "stop_px", "trail_amt", "trail_pct")


def _net_flags(out) -> list:
    return [f for g in out["combos"] for f in g["flags"] if f["code"] == "NET_PRICE"]


def _netty(leg) -> bool:
    """A card of a strategy, a roll, a collar or a buy write (whose price is the net price removed)."""
    clause = str(leg["clause"]).lower()
    return leg["combo"] is not None and clause.split()[0] in ("roll", "collar") or any(w in clause for w in ("spread", "condor", "butterfly", "straddle", "buy write"))


class NetPriceGroupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.rt = STT._ResolverBase._make()
        STT.build_world(unittest.TestCase(), cls.rt)

    @classmethod
    def tearDownClass(cls):
        cls.rt.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def read(self, text, book="P"):
        return st.parse_view(self.rt, {"text": text, "book": book})

    def pick(self, text, book, f, label="without the price"):
        """The choice ``label`` of flag ``f`` as the page applies it (its words in place of its span, else the flag's), read again."""
        ch = next((c for c in f.get("choices") or [] if c.get("label") == label), None)
        self.assertIsNotNone(ch, f"{text!r}: {f['code']} offers no {label!r}: {f.get('choices')}")
        a, b = ch.get("span") or f["span"]
        new = text[:a] + ch["clause"] + text[b:]
        return new, self.read(new, book)

    def same_orders(self, out, out2, text):
        """Every card keeps its book, account, side, quantity, contract, time in force and GTD date; a plain order its type and prices."""
        self.assertEqual(len(out["legs"]), len(out2["legs"]), (text, out2["text"]))
        for x, y in zip(out["legs"], out2["legs"]):
            keys = KEEP + (() if _netty(x) else PRICE_KEYS)
            self.assertEqual([(k, x.get(k)) for k in keys] + [("instrument", (x["instrument"] or {}).get("id"))],
                             [(k, y.get(k)) for k in keys] + [("instrument", (y["instrument"] or {}).get("id"))], (text, x["n"], x["clause"]))

    def check_group(self, book, text, prices):
        out = self.read(text, book)
        nets = _net_flags(out)
        self.assertEqual(1, len(nets), (text, [g["flags"] for g in out["combos"]]))
        f = nets[0]
        for p in dict.fromkeys(prices):
            self.assertIn(f"'{p}'", f["text"], (text, f["text"]))
        if len(prices) > 1:
            self.assertIn("are prices for the whole", f["text"])
            self.assertIn('("without the price" removes all of them)', f["text"])
        self.assertEqual(["without the price"], [c["label"] for c in f["choices"]])
        new, out2 = self.pick(text, book, f)
        self.assertEqual([], _net_flags(out2), (text, new, [g["flags"] for g in out2["combos"]]))
        for p in prices:                                    # the words of every net price removed (a plain order's own price kept)
            self.assertIsNone(re.search(re.escape(" " + p) + r"(?![\d.])", new), (text, new))
        self.assertEqual({u["text"] for u in out["unparsed"]}, {u["text"] for u in out2["unparsed"]}, (text, new))
        self.same_orders(out, out2, text)
        return out, out2

    def test_a_every_net_price_of_a_group_is_quoted_and_without_the_price_removes_them_all(self):
        for book, text, prices in TWO:
            with self.subTest(text=text):
                self.check_group(book, text, prices)

    def test_b_control_one_priced_strategy_in_a_group_is_said_as_before(self):
        text = f"({SPREAD} and buy 100 WIDG limit 40)"
        out = self.read(text)
        nets = _net_flags(out)
        self.assertEqual(1, len(nets), out["combos"])
        self.assertTrue(nets[0]["text"].startswith("'limit 2' on "), nets[0]["text"])
        self.assertIn("is a price for the whole", nets[0]["text"])
        _new, out2 = self.pick(text, "P", nets[0])
        self.assertEqual([], _net_flags(out2), out2["combos"])
        self.assertTrue(out2["ok"], [(l["n"], [f["code"] for f in l["flags"] if f["severity"] == "blocker"]) for l in out2["legs"]])
        widg = next(l for l in out2["legs"] if l["instrument"]["id"] == "WIDG")
        self.assertEqual(("LIMIT", "40"), (widg["order_type"], str(widg["limit_px"])))

    def test_seed_6660601_first_cases(self):
        for case, book, text, prices in SEED_6660601:
            with self.subTest(case=case):
                self.check_group(book, text, prices)

    def test_seed_6660201_6660202_cases(self):
        for case, book, text in SEED_PROPS3:
            with self.subTest(case=case):
                out = self.read(text, book)
                nets = _net_flags(out)
                self.assertTrue(nets, (text, out["combos"]))
                for f in nets:
                    new, out2 = self.pick(text, book, f)
                    ch = next(c for c in f["choices"] if c["label"] == "without the price")
                    lo = (ch.get("span") or f["span"])[0]
                    hi = lo + len(ch["clause"])
                    # property G: the flag that offered the choice is gone from the groups its words make
                    still = [g["id"] for g in out2["combos"] if lo <= int(g["span"][0]) < max(hi, lo + 1) and any(h["code"] == "NET_PRICE" for h in g["flags"])]
                    self.assertEqual([], still, (text, new))
                    self.assertFalse({u["text"] for u in out2["unparsed"]} - {u["text"] for u in out["unparsed"]}, (text, new))


# ==== the property tester: a strategy leg's offered account says what it holds ===========================================================
C100, C110 = R5.acme("C", 100, R5.E1), R5.acme("C", 110, R5.E1)
#: tests/test_props6_parse_oracle.py at PROPS6_PO_SEED=6660101: (case, [the texts read]) - two G-HOLD disagreements each on f682f5f
SEED_6660101 = [
    (63, ["buy 1 ACME Dec 18 2026 100 straddle in the Personal and Fund books",
          "buy 1 ACME Dec 18 2026 100 straddle in the Personal book;\nbuy 1 ACME Dec 18 2026 100 straddle in the Fund book"]),
    (623, ["buy 1 ACME Dec 18 2026 100/105 call spread in the Personal and Fund books",
           "buy 1 ACME Dec 18 2026 100/105 call spread in the Personal book; buy 1 ACME Dec 18 2026 100/105 call spread in the Fund book"]),
    (681, ["buy 1 ACME Dec 18 2026 100/105 call spread in the Personal and Fund books",
           "buy 1 ACME Dec 18 2026 100/105 call spread in the Personal book;\nbuy 1 ACME Dec 18 2026 100/105 call spread in the Fund book"]),
    (1041, ["in the Personal book at pb ms: buy 1 ACME Dec 18 2026 100/105 call spread in the Personal book;\nclose ACME calls",
            "buy 1 ACME Dec 18 2026 100/105 call spread in the Personal book;\nclose ACME calls at pb ms in the Personal book"]),
    (1341, ["buy 1 ACME Dec 18 2026 100/105 call spread in the Personal and Fund books",
            "buy 1 ACME Dec 18 2026 100/105 call spread in the Personal book\nbuy 1 ACME Dec 18 2026 100/105 call spread in the Fund book"]),
    (1493, ["buy 1 ACME Dec 18 2026 100/105 call spread in the Personal and Fund books",
            "buy 1 ACME Dec 18 2026 100/105 call spread in the Personal book; buy 1 ACME Dec 18 2026 100/105 call spread in the Fund book"]),
    (1585, ["in the Institutional book at P1:PB_MS: buy 1 ACME Dec 18 2026 100/105 call spread in the Personal book; short 100 WIDG",
            "buy 1 ACME Dec 18 2026 100/105 call spread in the Personal book; short 100 WIDG at P1:PB_MS in the Institutional book"]),
    (1725, ["buy 1 ACME Dec 18 2026 100 straddle in the Personal and Fund books",
            "buy 1 ACME Dec 18 2026 100 straddle in the Personal book\nbuy 1 ACME Dec 18 2026 100 straddle in the Fund book"]),
    (2037, ["buy 1 ACME Dec 18 2026 100 straddle in the Personal and Fund books",
            "buy 1 ACME Dec 18 2026 100 straddle in the Personal book;\nbuy 1 ACME Dec 18 2026 100 straddle in the Fund book"]),
    (2173, ["buy 1 ACME Dec 18 2026 100 straddle in the Personal and Fund books",
            "buy 1 ACME Dec 18 2026 100 straddle in the Personal book\nbuy 1 ACME Dec 18 2026 100 straddle in the Fund book"])]


class StrategyLegHoldingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = {k: os.environ.get(k) for k in (config.HOME_ENV, config.DB_ENV)}
        try:
            cls.tmp, cls.rt = R5._two_account_world()
            with cls.rt.db.reader() as conn:
                cls.held = {}
                for r in conn.execute("SELECT account_id, instrument_id, qty FROM v_positions").fetchall():
                    if int(r["qty"]):
                        cls.held.setdefault(r["account_id"], {})[r["instrument_id"]] = int(r["qty"])
        except Exception:
            cls._restore()
            raise

    @classmethod
    def _restore(cls):
        for k, v in cls.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    @classmethod
    def tearDownClass(cls):
        try:
            cls.rt.close()
            shutil.rmtree(cls.tmp, ignore_errors=True)
        finally:
            cls._restore()

    def legs(self, text, book="P"):
        return st.parse_view(self.rt, {"text": text, "book": book})["legs"]

    @staticmethod
    def offers(leg, account_id):
        """The ACCOUNT_CHOICE check of ``leg`` that offers ``account_id``, or None."""
        return next((f for f in leg["flags"] if f["code"] == "ACCOUNT_CHOICE" and any((c.get("label") or "").endswith(f"({account_id})")
                                                                                     for c in f.get("choices") or [])), None)

    def says_holding(self, text, book, iid, home, offered, words):
        legs = [l for l in self.legs(text, book) if (l.get("instrument") or {}).get("id") == iid and l["book"] == home.split("1:", 1)[0]]
        self.assertEqual(1, len(legs), text)
        self.assertEqual(home, legs[0]["account_id"], text)
        f = self.offers(legs[0], offered)
        self.assertIsNotNone(f, (text, [(x["code"], x["text"]) for x in legs[0]["flags"]]))
        self.assertIn(words, f["text"], text)

    def test_a_a_strategy_leg_offering_the_holding_account_says_what_it_holds(self):
        for text, book, iid, home, offered, words in [
                ("buy 1 ACME Dec 18 2026 100 straddle in the Personal and Fund books", "P", C100, "P1:US_EXCHANGE", "P1:PB_MS", "(it holds 2 contracts long in P1:PB_MS)"),
                ("buy 1 ACME Dec 18 2026 100 straddle", "P", C100, "P1:US_EXCHANGE", "P1:PB_MS", "(it holds 2 contracts long in P1:PB_MS)"),
                ("buy 1 ACME Dec 18 2026 100/105 call spread", "P", C100, "P1:US_EXCHANGE", "P1:PB_MS", "(it holds 2 contracts long in P1:PB_MS)"),
                ("sell 1 ACME Dec 18 2026 100/105 call spread", "P", C100, "P1:US_EXCHANGE", "P1:PB_MS", "(it holds 2 contracts long in P1:PB_MS)"),
                ("buy 1 ACME Dec 18 2026 105/110 call spread in the Fund book", "P", C110, "F1:US_EXCHANGE", "F1:PB_GS", "(it holds 1 contracts short in F1:PB_GS)")]:
            with self.subTest(text=text):
                self.says_holding(text, book, iid, home, offered, words)

    def test_b_control_a_single_order_says_what_the_offered_account_holds(self):
        for text, book, iid, home, offered, words in [
                ("bto 1 ACME Dec 18 2026 100 call", "P", C100, "P1:US_EXCHANGE", "P1:PB_MS", "(it holds 2 contracts long in P1:PB_MS)"),
                ("sto 1 ACME Dec 18 2026 110 call in the Fund book", "P", C110, "F1:US_EXCHANGE", "F1:PB_GS", "(it holds 1 contracts short in F1:PB_GS)")]:
            with self.subTest(text=text):
                self.says_holding(text, book, iid, home, offered, words)

    def test_seed_6660101_cases(self):
        """The oracle's rule G-HOLD on every card: an ACCOUNT_CHOICE check offering an account that holds the card's contract names it."""
        for case, texts in SEED_6660101:
            for text in texts:
                with self.subTest(case=case, text=text):
                    said = 0
                    for l in self.legs(text):
                        iid = (l["instrument"] or {}).get("id")
                        for f in l["flags"]:
                            if f["code"] != "ACCOUNT_CHOICE" or f["severity"] != "check":
                                continue
                            for ch in f.get("choices") or []:
                                m = re.search(r"\(([A-Z]\d+:[A-Z_]+)\)\s*$", str(ch.get("label") or ""))
                                if m and self.held.get(m.group(1), {}).get(iid):
                                    self.assertIn(m.group(1), f["text"], (case, text, l["n"], ch["label"], f["text"]))
                                    said += 1
                    self.assertGreater(said, 0, (case, text))          # not vacuous: a holding account is offered and said


# ==== autofill: a product's own ticket in a book without its account ===================================================================
class OwnTicketAccountTests(unittest.TestCase):
    """The corpus world (the three real books: only the Institutional book has a Private Markets account)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.rt = STT._ResolverBase._make()
        STT.build_world(unittest.TestCase(), cls.rt)

    @classmethod
    def tearDownClass(cls):
        cls.rt.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def read(self, text, book="P"):
        return st.parse_view(self.rt, {"text": text, "book": book})

    @staticmethod
    def flags(leg, code):
        return [f for f in leg["flags"] if f["code"] == code]

    def test_a_a_private_company_read_in_a_book_without_a_private_markets_account_is_held_with_the_reason(self):
        for text, book, n, name in (("buy 100 PRV-WIDGLABS", "P", 0, "Personal Book"), ("buy 100 PRV-WIDGLABS", "F", 0, "Fund Book"),
                                    ("sell 50 shares PRV-WIDGLABS", "P", 0, "Personal Book"),
                                    ("buy 2 ACME Dec 18 2026 240 calls\nbuy 100 PRV-WIDGLABS", "P", 1, "Personal Book")):
            with self.subTest(text=text, book=book):
                out = self.read(text, book)
                leg = out["legs"][n]
                own = self.flags(leg, "KIND_OWN_TICKET")
                self.assertEqual(1, len(own), leg["flags"])
                self.assertIsNone(own[0]["action"]["ticket"]["account_id"])
                f = self.flags(leg, "ACCOUNT_NOT_IN_BOOK")
                self.assertEqual(1, len(f), leg["flags"])
                self.assertEqual("blocker", f[0]["severity"])
                self.assertEqual(f"the {name} has no Private Markets account, so the private-markets ticket cannot open for it: trade it in a book"
                                 f" that has one", f[0]["text"])
                self.assertIsNone(f[0].get("choices"))
                self.assertIsNone(leg["request"])
                if n:
                    self.assertIsNotNone(out["legs"][0]["request"], out["legs"][0]["flags"])    # the other order: as read

    def test_b_controls_a_book_with_the_account_and_products_every_book_has(self):
        for text, book, acct in (("buy 100 PRV-WIDGLABS", "I", "I1:PRIVATE"), ("buy 100 PRV-WIDGLABS in the Institutional book", "P", "I1:PRIVATE"),
                                 ("buy 1m EURUSD", "P", "P1:FOREIGN_FX"), ("buy 1m EURUSD", "I", "I1:FOREIGN_FX"),
                                 ("buy 10000 ACME-31", "F", "F1:DEBT"), ("buy 10000 ACME-31", "I", "I1:DEBT")):
            with self.subTest(text=text, book=book):
                leg = self.read(text, book)["legs"][0]
                self.assertEqual(acct, self.flags(leg, "KIND_OWN_TICKET")[0]["action"]["ticket"]["account_id"])
                self.assertEqual(acct, leg["account_id"])
                self.assertEqual([], self.flags(leg, "ACCOUNT_NOT_IN_BOOK"))


# ==== the page ===========================================================================================================================
BOND, PRIV, COMPANY = "ACME-31", "PRV-WIDGCO", "WIDGCO"
BOND_NAME, PRIV_NAME = "ACME Corp 5.00% 2031 (fictional)", "WIDG Co (fictional private company)"
EURUSD_LAST = "1.0850"
STORE_KEY, WIN_KEY = "finsim.smart.draft.v2", "finsim.smart.draft.window"
READY = "!!(window.T && T.router && T.router.current && T.router.current.page === 'smart' && document.querySelector('#smart-text') && T.pages.smart)"
LOADED = "!!(window.T && T.router && T.router.current && document.querySelector('#page') && T.state.books && T.state.books.length === 3 && T.book.code)"
P_USX_HASH, FX_HASH = "#/a/P1%3AUS_EXCHANGE", "#/a/P1%3AFOREIGN_FX"
LARGE = "buy 6000 ACME limit 50; buy 5 WIDG limit 249"            # #1: 300,000 > 25 % of the Personal book's NAV
FX_TEXT = "buy 10k EURUSD; buy 20k EURUSD"
#: the page's fetch holds the answer of a POST whose path matches ``rx`` for ``ms`` ms (the request reaches the server at once);
#: window.__arrived counts the server's answers, window.__answered the answers handed on to the page
SLOW = r"""(function (rx, ms) {
  if (!window.__f0) { window.__f0 = window.fetch; }
  var f0 = window.__f0, re = new RegExp(rx); window.__arrived = 0; window.__answered = 0;
  window.fetch = function (url, init) {
    var p = f0.apply(this, arguments);
    var path = String(url).replace(/^https?:\/\/[^\/]+/, '');
    if (init && String(init.method || '').toUpperCase() === 'POST' && re.test(path)) {
      return p.then(function (res) { window.__arrived++; return new Promise(function (ok) { setTimeout(function () { window.__answered++; ok(res); }, ms); }); });
    }
    return p;
  };
  return true; })(%s, %d)"""
#: every toast from now on, its whole message (T.ui.toast wrapped; put back by tidy)
TOASTS_ON = r"""(function () {
  if (!window.__toast0) { window.__toast0 = T.ui.toast; }
  var t0 = window.__toast0; window.__toasts = [];
  T.ui.toast = function (m) { try { window.__toasts.push(String(m)); } catch (e) { /* ignore */ } return t0.apply(this, arguments); };
  return true; })()"""
UNDO = r"""(function () {
  if (window.__f0) { window.fetch = window.__f0; } if (window.__toast0) { T.ui.toast = window.__toast0; }
  Array.prototype.forEach.call(document.querySelectorAll('#modal-root .modal-bg'), function (m) { m.parentNode.removeChild(m); });
  return true; })()"""
#: the dialog, where the focus is
SEEN = r"""(function () {
  var m = document.querySelector('#modal-root .modal'), a = document.activeElement;
  return { hash: location.hash, dock: !!(T.dock && T.dock.visible && T.dock.visible()), modal: m ? m.innerText : null,
           focus: a ? (a.tagName + ' ' + String(a.className || '') + ' ' + String(a.innerText || a.id || '').trim()).slice(0, 80) : null,
           focus_cancel: !!(m && a && m.contains(a) && a.classList.contains('cancel')),
           ticket_status: (document.querySelector('#ticket-status') || {}).textContent || '',
           status: (document.querySelector('#smart-status') || {}).innerText || '' };
})()"""
#: the FX desk in the dock
DESK = r"""(function () {
  var r = document.querySelector('#dock-body .fxd'); if (!r) { return null; }
  var amt = r.querySelector('input[aria-label="amount"]'), buy = r.querySelector('select[aria-label="currency you buy"]');
  var sell = r.querySelector('select[aria-label="currency you sell"]'), cf = r.querySelector('.acts .btn.primary');
  return { hash: location.hash, dock: T.dock.visible(), amount: amt ? amt.value : null, buy: buy ? buy.value : null, sell: sell ? sell.value : null,
           confirm: !!(cf && !cf.disabled), status: (r.querySelector('.st') || {}).textContent || '', text: (r.innerText || '').slice(0, 2000) };
})()"""
#: the bond ticket in the dock
BONDT = r"""(function () {
  var r = document.querySelector('#dock-body .bt'); if (!r) { return null; }
  var on = Array.prototype.filter.call(r.querySelectorAll('.sides .btn'), function (b) { return b.classList.contains('on'); }).map(function (b) { return b.textContent; });
  var dd = r.querySelector('.dd');
  return { acct: T.router.current ? T.router.current.account || null : null, inst: (r.querySelector('#ticket-instrument') || {}).value,
           side: on.join('/'), face: (r.querySelector('input[aria-label="face amount"]') || {}).value, list_hidden: !dd || dd.hidden,
           meta: (r.querySelectorAll('.meta')[1] || {}).textContent || '' };
})()"""
#: the private-markets ticket in the dock
PRIVT = r"""(function () {
  var b = document.querySelector('#dock-body'); var c = b && b.querySelector('#pt-company'); if (!c) { return null; }
  return { acct: T.router.current ? T.router.current.account || null : null, company: c.value, side: (b.querySelector('#pt-side') || {}).value,
           qty: (b.querySelector('#pt-qty') || {}).value };
})()"""
#: card ``i``'s own-ticket action as the owner sees it (its flag box open)
OWN_ACTION = r"""(function (i) {
  var b = document.querySelector('#smart-cards [data-i="' + i + '"] .smart-flagbox [data-act="action"]'); if (!b) { return null; }
  var why = b.parentNode.querySelector('.smart-why');
  return { label: b.innerText, enabled: !b.disabled, title: b.title || '', why: why ? why.innerText : '' };
})(%d)"""


def _seed6(home: str, clock: FakeClock, fp: FakeProvider) -> None:
    """The three books; the prime-broker accounts P1:PB_MS / F1:PB_GS; the fictional ACME / WIDG and their prints, an invented EURUSD print,
    the ACME chain; the fictional bond ACME-31 and private company WIDGCO (copies of a seeded row's trading fields, fictional names)."""
    rt = _server.build_runtime(home, os.path.join(home, "terminal.db"), clock=clock, offline=True, fake_provider=fp, log=_Log(), books=list(config.BOOKS),
                               fetch_threads=0)
    try:
        aapl = rt.instruments.get("AAPL")
        bond0 = next(i for i in rt.instruments.all() if i.kind == "BOND")
        priv0 = next(i for i in rt.instruments.all() if i.kind == "PRIVATE")
        with rt.db.writer() as tx:
            for sym, (name, _last) in SMK.SMART_NAMES.items():
                rt.instruments.upsert(tx, instrument_id=sym, kind="EQUITY", symbol=sym, name=name, currency="USD", session_group=aapl.session_group,
                                      account_kinds=list(aapl.account_kinds), yahoo_symbol=sym, cboe_symbol=sym, spec={"exchange": "NYQ"})
            rt.instruments.upsert(tx, instrument_id=BOND, kind="BOND", symbol=BOND, name=BOND_NAME, currency=bond0.currency, session_group=bond0.session_group,
                                  account_kinds=list(bond0.account_kinds), spec=dict(bond0.spec or {}, issuer="ACME Corp"))
            rt.instruments.upsert(tx, instrument_id=PRIV, kind="PRIVATE", symbol=PRIV, name=PRIV_NAME, currency=priv0.currency, session_group=priv0.session_group,
                                  account_kinds=list(priv0.account_kinds), spec=dict(priv0.spec or {}))
            ts = int(tx.now.timestamp())
            row = tx.conn.execute("SELECT * FROM private_companies WHERE instrument_id=?", (priv0.instrument_id,)).fetchone()
            tx.conn.execute("INSERT INTO private_companies (company_id, name, sector, country, status, ticker, instrument_id, notes, updated_ts)"
                            " VALUES (?,?,?,?,?,?,?,?,?)", (COMPANY, PRIV_NAME, row["sector"], row["country"], "PRIVATE", None, PRIV, "a fictional company for the tests", ts))
            for rr in tx.conn.execute("SELECT * FROM private_rounds WHERE company_id=?", (row["company_id"],)).fetchall():
                tx.conn.execute("INSERT INTO private_rounds (company_id, round_date, round_type, post_money_c, price_per_share, lead_investors, source_url,"
                                " as_of, recorded_by) VALUES (?,?,?,?,?,?,?,?,?)",
                                (COMPANY, rr["round_date"], rr["round_type"], rr["post_money_c"], rr["price_per_share"], None, "https://example.invalid/fictional",
                                 rr["as_of"], "seed"))
            for b, lid, role, name, broker in (("P", "P1", "PB_MS", "Morgan Stanley PB", "MS"), ("F", "F1", "PB_GS", "Goldman Sachs PB", "GS")):
                _books._insert_account(tx.conn, ts, b, lid, role, name, "PB", 60, broker=broker)
        rt.instruments.load()
        qt = epoch_of(clock.today(), "13:58")
        for sym, (_name, last) in SMK.SMART_NAMES.items():
            fp.set(sym, last=last, quote_time=qt, bar=(qt - qt % 60 - 60, 61900))
            rt.cache.subscribe([sym], 86400, "PAGE")
        fx = rt.instruments.get("EURUSD")
        fp.set(fx.yahoo_symbol, last=EURUSD_LAST, quote_time=qt, bar=(qt - qt % 60 - 60, 61900))
        rt.cache.subscribe(["EURUSD"], 86400, "PAGE")
        rt.worker.tick()
        fp.set_chain("ACME", SMK._acme_chain(clock))
    finally:
        rt.close()


class _Server6(SMK._Server):
    """``SMK._Server`` with the three books (port 0, offline, the seeded FakeProvider)."""

    def __init__(self, home, clock, fp):
        super().__init__(home, clock, fp)
        self.thread = threading.Thread(target=self._run6, name="simplify-r6-serve", daemon=True)

    def _run6(self):
        self.rc = _server.serve(port=0, home=self.home, db=os.path.join(self.home, "terminal.db"), offline=True, ready=self._ready, out=self.msgs.append,
                                clock=self.clock, fake_provider=self.fp, books=list(config.BOOKS), fetch_threads=0, supervisor_interval=0.5, log=_Log())


@unittest.skipUnless(find_browser(), NO_BROWSER)
class AnswerFencePageTests(unittest.TestCase):
    """One offline server (port 0, a temp home) and one headless browser for the class, both closed in tearDownClass."""

    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp(prefix="fst-simplify-r6-page-")
        cls.env = {k: os.environ.get(k) for k in (config.HOME_ENV, config.DB_ENV)}
        os.environ[config.HOME_ENV] = cls.home
        os.environ[config.DB_ENV] = os.path.join(cls.home, "terminal.db")
        cls.server = cls.browser = None
        try:
            clock = FakeClock(SMK.AT)
            cls.fp = FakeProvider(clock)
            _seed6(cls.home, clock, cls.fp)
            cls.server = _Server6(cls.home, FakeClock(SMK.AT), cls.fp).start()
            cls.browser = Browser(find_browser(), 1440, 900)
            cls.sid = cls.browser.page("about:blank")
            cls.browser.navigate(cls.sid, cls.server.url)
            cls.browser.wait(cls.sid, LOADED, timeout=45)
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

    # ---- the page, as the owner uses it ---------------------------------------------------------------------------------------------
    def js(self, expr: str, timeout: float = 30.0):
        return self.browser.js(self.sid, expr, timeout=timeout)

    def wait(self, expr: str, timeout: float = 30.0):
        return self.browser.wait(self.sid, expr, timeout=timeout)

    def press(self, key: str, vk: int, mods: int = 0) -> None:
        base = {"key": key, "code": key, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk, "modifiers": mods}
        for kind in ("keyDown", "keyUp"):
            self.browser.call("Input.dispatchKeyEvent", dict(base, type=kind), session=self.sid)

    def enter_key(self) -> None:
        """One real Enter as a keyboard types it (keydown with its character, then keyup), wherever the focus is."""
        base = {"key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13}
        self.browser.call("Input.dispatchKeyEvent", dict(base, type="keyDown", text="\r", unmodifiedText="\r"), session=self.sid)
        self.browser.call("Input.dispatchKeyEvent", dict(base, type="keyUp"), session=self.sid)

    def fresh_smart(self, book: str = "P") -> None:
        """#/smart with no draft and ``book`` selected, from the Dashboard (the drafts removed once the Smart Ticket left the screen: leaving,
        it saves its own)."""
        self.js("location.hash = '#/dashboard'; true")
        self.wait("T.router.current && T.router.current.page === 'dashboard'")
        self.js("(function () { localStorage.removeItem(%s); sessionStorage.removeItem(%s); return true; })()" % (json.dumps(STORE_KEY), json.dumps(WIN_KEY)))
        self.js("T.book.set(%s); true" % json.dumps(book))
        self.wait("T.book.code === %s" % json.dumps(book))
        self.js("location.hash = '#/smart'; true")
        self.wait(READY)
        self.browser.pump(0.4)

    def back(self, cards: int) -> None:
        """Back to the Smart Ticket (Ctrl+F9, else its link): the draft restored, held until Enter."""
        self.press("F9", 120, mods=2)
        try:
            self.wait(READY, timeout=5)
        except TimeoutError:
            self.js("location.hash = '#/smart'; true")
            self.wait(READY)
        self.wait(READY + " && document.querySelectorAll('#smart-cards [data-i]').length === %d && !document.querySelector('#smart-restored').hidden" % cards)
        self.browser.pump(0.3)

    def read(self, text: str, cards: int) -> None:
        """The owner types ``text`` and presses Enter: the reading answers with ``cards`` cards."""
        self.js("(function () { var t = document.querySelector('#smart-text'); t.focus(); t.value = %s; t.dispatchEvent(new Event('input', { bubbles: true }));"
                " return true; })()" % json.dumps(text))
        self.enter_key()
        self.wait("!document.querySelector('#smart-parse').disabled && /^read /.test(document.querySelector('#smart-status').innerText)"
                  " && document.querySelectorAll('#smart-cards [data-i]').length === %d" % cards)
        self.browser.pump(0.2)

    def read_again(self) -> None:
        """The Enter the restored page asks for: the text read again."""
        self.js("document.querySelector('#smart-text').focus(); true")
        self.enter_key()
        self.wait("document.querySelector('#smart-restored').hidden && /^read /.test(document.querySelector('#smart-status').innerText)", timeout=20)
        self.browser.pump(0.2)

    def open_card_in_ticket(self, i: int, inst: str) -> None:
        self.js("document.querySelector('#smart-cards [data-i=\"%d\"] [data-act=\"open\"]').click(); true" % i)
        self.wait("location.hash.indexOf(%s) === 0 && !!document.querySelector('#ticket-instrument') && document.querySelector('#ticket-instrument').value === %s"
                  % (json.dumps(P_USX_HASH), json.dumps(inst)))
        self.wait("!document.querySelector('#ticket-submit-btn').disabled", timeout=40)          # its own preview answered, clean
        self.browser.pump(0.3)

    def own_action(self, i: int, words: str) -> dict:
        """Card ``i``'s own-ticket red flag (the chip whose title holds ``words``) opened: its action as the owner sees it."""
        self.js("(function (i, w) { var c = document.querySelector('#smart-cards [data-i=\"' + i + '\"]');"
                " var chip = Array.prototype.filter.call(c.querySelectorAll('.smart-chip.sev-blocker'), function (b) { return b.title.indexOf(w) >= 0; })[0];"
                " chip.click(); return true; })(%d, %s)" % (i, json.dumps(words)))
        self.wait("!!document.querySelector('#smart-cards [data-i=\"%d\"] .smart-flagbox [data-act=\"action\"]')" % i, timeout=10)
        return self.js(OWN_ACTION % i)

    def press_action(self, i: int) -> None:
        self.js("document.querySelector('#smart-cards [data-i=\"%d\"] .smart-flagbox [data-act=\"action\"]').click(); true" % i)

    def fx_desk(self, i: int) -> dict:
        """Card ``i``'s "Open the FX desk": the desk once its preview answered (a clean one, as the invented print allows)."""
        act = self.own_action(i, "currency pair")
        self.assertEqual(("Open the FX desk", True), (act["label"], act["enabled"]), act)
        self.press_action(i)
        self.wait("location.hash.indexOf(%s) === 0 && !!document.querySelector('#dock-body .fxd') && T.dock.visible()" % json.dumps(FX_HASH), timeout=15)
        self.wait("(function () { var r = document.querySelector('#dock-body .fxd'); var cf = r && r.querySelector('.acts .btn.primary');"
                  " return !!cf && !cf.disabled; })()", timeout=45)
        self.browser.pump(0.4)
        return self.js(DESK)

    def bond_from_smart(self, text: str) -> dict:
        """``text`` read (one card) and its "Open the bond ticket" pressed: the bond ticket once it looked the bond up."""
        self.read(text, 1)
        act = self.own_action(0, BOND_NAME)
        self.assertEqual(("Open the bond ticket", True), (act["label"], act["enabled"]), act)
        self.press_action(0)
        self.wait("!!document.querySelector('#dock-body .bt') && T.dock.visible()", timeout=15)
        self.wait("(function () { var m = document.querySelectorAll('#dock-body .bt .meta')[1]; return !!m && !/^loading /.test(m.textContent); })()", timeout=15)
        self.browser.pump(0.5)
        return self.js(BONDT)

    def orders(self) -> list:
        with self.server.rt.db.reader() as conn:
            return [dict(r) for r in conn.execute("SELECT order_id, instrument_id, side, qty, status FROM orders ORDER BY rowid")]

    def fx_deals(self) -> int:
        with self.server.rt.db.reader() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM fx_deals").fetchone()[0])

    def tidy(self) -> None:
        def undo():
            self.server.clock.set(SMK.AT)
            self.js(UNDO)
            self.js("(function () { localStorage.removeItem(%s); sessionStorage.removeItem(%s); location.hash = '#/dashboard'; return true; })()"
                    % (json.dumps(STORE_KEY), json.dumps(WIN_KEY)))
            self.browser.pump(0.6)
        self.addCleanup(undo)

    # ---- the order ticket's large-order question -----------------------------------------------------------------------------------
    def test_a_a_large_orders_question_is_never_asked_while_its_ticket_is_out_of_sight(self):
        self.tidy()
        before = self.orders()
        self.fresh_smart("P")
        self.read(LARGE, 2)
        self.open_card_in_ticket(0, "ACME")
        self.js(TOASTS_ON)
        self.js(SLOW % (json.dumps(r"^/api/orders(\?|$)"), 4000))
        self.js("document.querySelector('#ticket-submit-btn').click(); true")             # the owner's Submit of #1
        t0 = time.time()
        self.wait("!!(T.components.ticket.state() || {}).submitting", timeout=5)
        self.back(2)                                                                       # Ctrl+F9, as the plan says
        self.assertLess(time.time() - t0, 3.5, "the steps took longer than the answer's delay")
        self.wait("window.__answered >= 1 && !(T.components.ticket.state() || {}).submitting", timeout=20)
        self.browser.pump(0.5)
        seen = self.js(SEEN)
        self.assertTrue(seen["hash"].startswith("#/smart"), seen)
        self.assertIsNone(seen["modal"], seen)                                             # never asked out of sight
        self.assertTrue(seen["ticket_status"].startswith("NOT placed (BUY 6000 ACME limit 50"), seen)
        toasts = self.js("window.__toasts.slice()")
        self.assertTrue(any(re.search(r"^the answer for BUY 6000 ACME limit 50 .* came while its ticket was out of sight: NOT placed: a large order needs your"
                                      r" confirmation", t) for t in toasts), toasts)
        self.read_again()                                                                  # the Enter the page asks for: the text read again
        self.browser.pump(0.5)
        self.assertIsNone(self.js("(document.querySelector('#modal-root .modal') || {}).innerText || null"))
        self.assertEqual(before, self.orders())                                            # nothing placed

    def test_b_in_sight_the_large_orders_question_puts_the_focus_on_cancel(self):
        self.tidy()
        before = self.orders()
        self.fresh_smart("P")
        self.read(LARGE, 2)
        self.open_card_in_ticket(0, "ACME")
        self.js("document.querySelector('#ticket-submit-btn').click(); true")
        self.wait("!!document.querySelector('#modal-root .modal')", timeout=20)
        self.browser.pump(0.3)
        seen = self.js(SEEN)
        self.assertIn("Confirm large order", seen["modal"])
        self.assertTrue(seen["focus_cancel"], seen)
        self.enter_key()                                                                   # a stray Enter answers no
        self.wait("!document.querySelector('#modal-root .modal') && document.querySelector('#ticket-status').textContent === 'not confirmed; nothing was submitted'",
                  timeout=10)
        self.browser.pump(1.0)
        self.assertEqual(before, self.orders())

    # ---- the FX desk's answers ------------------------------------------------------------------------------------------------------
    def _deal_one_refused(self, ms: int) -> int:
        """Cards #1 / #2 read; #1's FX desk ready; Confirm while the server refuses the deal (its clock 30 minutes on: the print is stale
        there) and the page's fetch holds the answer ``ms``; the server's clock back once it answered.  Returns the deals before."""
        before = self.fx_deals()
        self.fresh_smart("P")
        self.read(FX_TEXT, 2)
        d = self.fx_desk(0)
        self.assertEqual(("EUR", "USD", "10000"), (d["buy"], d["sell"], d["amount"]), d)
        self.js(TOASTS_ON)
        self.js(SLOW % (json.dumps(r"^/api/fx/deal(\?|$)"), ms))
        self.server.clock.advance(1800)
        self.js("document.querySelector('#dock-body .fxd .acts .btn.primary').click(); true")   # the owner's Confirm of deal #1
        self.wait("window.__arrived >= 1", timeout=10)
        self.server.clock.set(SMK.AT)
        return before

    def test_c_an_fx_refusal_out_of_sight_is_said_in_a_toast_naming_its_deal(self):
        self.tidy()
        before = self._deal_one_refused(6000)
        self.back(2)
        self.assertEqual(0, self.js("window.__answered"), "the deal's answer came before the desk was out of sight")
        self.wait("window.__answered >= 1", timeout=20)
        self.browser.pump(1.0)
        toasts = self.js("window.__toasts.slice()")
        self.assertTrue(any(re.search(r"^the answer for FX spot BUY EUR / SELL USD 10,000(\.00)? EUR in .* came while the desk was out of sight: refused, nothing"
                                      r" booked: .*(stale|STALE)", t) for t in toasts), toasts)
        self.assertEqual(before, self.fx_deals())
        # the control: the same refusal with the desk in sight is said there, as before
        before = self._deal_one_refused(1500)
        self.wait("window.__answered >= 1", timeout=20)
        self.browser.pump(1.0)
        d = self.js(DESK)
        self.assertRegex(d["status"], r"^refused: .*(stale|STALE)")
        self.assertFalse(any(t.startswith("the answer for FX spot") for t in self.js("window.__toasts.slice()")))
        self.assertEqual(before, self.fx_deals())

    def test_d_an_fx_answer_that_comes_after_the_next_card_opened_the_desk_is_never_painted_on_that_deal(self):
        self.tidy()
        before = self._deal_one_refused(25000)
        self.back(2)
        self.read_again()
        d2 = self.fx_desk(1)                                                               # card #2: a new deal on the same desk
        self.assertEqual(("EUR", "USD", "20000"), (d2["buy"], d2["sell"], d2["amount"]), d2)
        self.assertEqual(0, self.js("window.__answered"), "deal #1's answer came before card #2 opened the desk")
        self.wait("window.__answered >= 1", timeout=40)
        self.browser.pump(1.0)
        d2 = self.js(DESK)
        self.assertEqual("20000", d2["amount"], d2)
        self.assertNotRegex(d2["status"], r"refused|stale|STALE")
        self.assertNotRegex(d2["text"], r"refused|stale|STALE")
        toasts = self.js("window.__toasts.slice()")
        self.assertTrue(any(re.search(r"^the answer for FX spot BUY EUR / SELL USD 10,000(\.00)? EUR in .* came after the desk moved on to another deal: refused,"
                                      r" nothing booked", t) for t in toasts), toasts)
        self.assertEqual(before, self.fx_deals())

    # ---- the bond ticket's box and the private-markets ticket's account -----------------------------------------------------------------
    def type_in_bond_box(self, words: str) -> None:
        got = self.js("(function (v) { var el = document.querySelector('#dock-body #ticket-instrument'); if (!el) { return null; } el.focus(); el.value = v;"
                      " el.dispatchEvent(new Event('input', { bubbles: true })); return el.value; })(%s)" % json.dumps(words))
        self.assertEqual(words, got)
        self.browser.pump(0.5)

    def back_to_smart(self) -> None:
        self.js("location.hash = '#/smart'; true")
        self.wait(READY)
        self.browser.pump(0.4)

    def test_e_a_fresh_hand_off_puts_the_bond_it_names_in_the_bond_box(self):
        self.tidy()
        self.fresh_smart("P")
        b = self.bond_from_smart("buy 1k ACME-31")                                         # the control: nothing typed
        self.assertEqual((BOND, "BUY", "1000", True), (b["inst"], b["side"], b["face"], b["list_hidden"]), b)
        for typed, text, side, face in (("2,000", "buy 10000 ACME-31", "BUY", "10000"), ("2,000", "sell 2,000 ACME-31", "SELL", "2000"),
                                        ("-5", "buy 1k ACME-31", "BUY", "1000")):
            with self.subTest(typed=typed, text=text):
                self.type_in_bond_box(typed)                                               # typed in the bond box, never looked up
                self.back_to_smart()
                b = self.bond_from_smart(text)
                self.assertEqual(("P1:DEBT", BOND, side, face, True), (b["acct"], b["inst"], b["side"], b["face"], b["list_hidden"]), b)
                self.browser.pump(0.6)                                                     # a look-up of the typed words, if one was due
                self.assertEqual((BOND, True), (self.js(BONDT)["inst"], self.js(BONDT)["list_hidden"]))

    def test_f_a_private_company_in_a_book_without_a_private_markets_account_is_held(self):
        self.tidy()
        # the Personal book has no Private Markets account (the served Fund book opens one; the corpus world's Fund book has none:
        # OwnTicketAccountTests reads it there)
        for book, text, cards, i, name in (("P", "buy 100 PRV-WIDGCO", 1, 0, "Personal Book"),
                                           ("P", "buy 2 ACME Dec 18 2026 65 calls at pb ms\nbuy 100 PRV-WIDGCO", 2, 1, "Personal Book")):
            with self.subTest(book=book, text=text):
                self.fresh_smart(book)
                self.read(text, cards)
                act = self.own_action(i, PRIV_NAME)
                self.assertEqual(("Open the private-markets ticket", False), (act["label"], act["enabled"]), act)
                self.assertIn(f"the {name} has no Private Markets account", act["title"])
                self.assertTrue(act["why"].startswith(f"the {name} has no Private Markets account"), act)
                self.press_action(i)                                                       # pressed anyway: nothing opens
                self.browser.pump(1.0)
                self.assertEqual(("smart", "#/smart"), (self.js("T.router.current.page"), self.js("location.hash")))
        # the control: the Institutional book opens it on I1:PRIVATE, the company, the side and the number of shares handed off
        self.fresh_smart("P")
        self.read("buy 100 PRV-WIDGCO in the Institutional book", 1)
        act = self.own_action(0, PRIV_NAME)
        self.assertEqual(("Open the private-markets ticket", True), (act["label"], act["enabled"]), act)
        self.press_action(0)
        self.wait("!!document.querySelector('#dock-body #pt-company') && T.dock.visible() && document.querySelector('#dock-body #pt-qty').value === '100'", timeout=20)
        self.browser.pump(0.4)
        self.assertEqual({"acct": "I1:PRIVATE", "company": COMPANY, "side": "BUY", "qty": "100"}, self.js(PRIVT))


class AnswerFenceSourceTests(unittest.TestCase):
    """The bond ticket and the private-markets ticket fence their answers as the FX desk does (its fence is shown working in test_c /
    test_d above): a counter of the trade shown, moved on by a fresh hand-off (which frees the Submit), an answer for another trade said
    in a toast naming it, one that comes out of sight painted AND said in a toast; the bond fill line names the bond submitted."""

    def test_the_own_desks_say_each_answer_for_its_own_trade(self):
        fx = _read(os.path.join(STATIC, "components", "fxdesk.js"))
        bond = _read(os.path.join(STATIC, "components", "bondticket.js"))
        priv = _read(os.path.join(STATIC, "components", "privateticket.js"))
        ticket = _read(os.path.join(STATIC, "components", "ticket.js"))
        core = _read(os.path.join(STATIC, "core.js"))
        for src in (fx, bond):
            self.assertIn("st.deal++; st.busy = false;", src)
        self.assertIn("var me = st, deal = st.deal, words = dealWords(f);", fx)
        self.assertIn("if (me !== st || deal !== me.deal) { lateDeal(words, head, 'ok');", fx)
        self.assertIn("unseenDeal(away, words, 'refused, nothing booked: ' + T.api.describe(e), 'err');", fx)
        self.assertIn("if (me !== st || deal !== me.deal) { lateTrade(words, head, 'ok'); return; }", bond)
        self.assertIn("if (me !== st || deal !== me.deal) { lateTrade(words, 'refused, nothing traded: ' + T.api.describe(e), 'err'); return; }", bond)
        self.assertIn("unseenTrade(away, words, 'refused, nothing traded: ' + T.api.describe(e), 'err');", bond)
        self.assertIn("' ' + bond + ' @ ' + fill.price", bond)
        self.assertNotIn("(st.ui.inst.value || '')", bond)
        self.assertIn("if (fields.instrument_id && (fields.fresh || fields.instrument_id !== st.instrument_id)) { setInstrument(fields.instrument_id, null); }", bond)
        self.assertIn("state.trade++; state.busy = false;", priv)
        self.assertEqual(2, priv.count("if (mine !== state.trade) {"))
        self.assertIn("var msg = away ? T.api.describe(e) : T.api.fail(e, 'private trade');", priv)
        # the order ticket: the large-order question never asked out of sight, asked with the focus on Cancel
        branch = ticket[ticket.index("if (e.code === 'CONFIRM_REQUIRED') {"):]
        self.assertLess(branch.index("if (away) {"), branch.index("T.ui.confirm("))
        self.assertIn("T.ui.confirm(txt, { title: 'Confirm large order', ok: 'Confirm', focus: 'cancel' })", ticket)
        self.assertIn("if (opts.focus === 'cancel') {", core)


if __name__ == "__main__":
    unittest.main()
