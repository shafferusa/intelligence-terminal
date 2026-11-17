"""FinSim 1.0.7.2 simplify review round 9 (on f021388): the regression tests of its findings, kept in the terminal suite.

The page (R9-P1, R9-P2, R9-P3, major).  A REAL pointer double click on a ticket's own send button other than the order ticket's Submit
sent twice: the FX desk's Confirm and the bond ticket's Submit (the Smart Ticket's "Open the FX desk" / "Open the bond ticket") booked on
every click, and a booking's success path previewed the SAME deal again at once (fxdesk.js / bondticket.js ``invalidate();
runPreview(true)``), whose answer armed the button for the second click of the double click: two deals, two fills.  The order ticket's
"Submit package" (its Strategies tab; a group's "Strategies tab") sent on every click and its priced package stayed armed after it was
sent: two packages.  Each now takes a click of its own (the second click of a double click never sends; one that began elsewhere is
said), the submit key held down sends once (a repeat never sends), a deal or a trade booked holds its desk's button - said on it and in
the preview - until a field is changed or Preview is pressed (the desk never previews the same deal again by itself), and a priced
package is sent once (its button waits, saying so, for a new preview of the package).  The real offline server on port 0 (a temp home:
the Personal and the Fund books, the fictional ACME / WIDG printed, an invented EURUSD print, the fictional bond ACME-31 listed by
products/bonds.ensure_bond and priced on an invented par curve, the smoke test's invented ACME chain) and the real pages in a headless
Edge / Chrome; the pointer and the keys are CDP's (Input.dispatchMouseEvent with its click count, Input.dispatchKeyEvent with autoRepeat):
  PageTests
    test_a  "buy 10k EURUSD", "Open the FX desk": a real double click on Confirm (its second press 0.15 s after the first, inside a 500
            ms double click) books ONE deal; 2 s on Confirm is still held (the old immediate preview armed it about 0.1 s after the
            booking), said on it and in the preview; the submit key books nothing and says why; Preview pressed, a click of its own books
            the next deal (tests/test_reviews9_page_own_desk_double_click.py test_a; test_a2 its control);
    test_b  the FX desk's own rule: a second click (click count 2) on an armed Confirm with no click on it before (its gesture begun on
            the amount box): not booked, said (for 20 s: the 10 s refresh paints no "ready" over it); the submit key: a repeat alone (a
            key held from before) books nothing, a press and its repeats book once;
    test_c  "buy 1k ACME-31", "Open the bond ticket": the double click on Submit fills ONE trade; Submit held, said; the face changed, a
            click of its own fills the next (test_b; test_b2 its control);
    test_d  the bond ticket's own rule: a second click alone (its gesture begun on the face box) trades nothing, said; the key held
            down trades once;
    test_e  "buy 1 ACME Dec 18 2026 60/65 call spread", its group's "Strategies tab", a put template priced: a double click on Submit
            package sends ONE package; its button then reads "Package sent" and stays held; "Preview package" pressed, a second click
            alone sends nothing, said, and a click of its own sends the next (test_c; test_c2 its control);
    test_f  the order ticket: the submit key held down places one order; a repeat alone places nothing.
  DeskSourceTests  fxdesk.js, bondticket.js, hedge.js and ticket.js: where these rules live (the 10 s refresh skipped while a booking
            holds the button, no preview of the same deal after a booking, the click count and the key repeat read, the refusal of a
            second click kept readable).
The parse (R9-T1 major, R9-T2 minor; tests/test_props9_parse_chain_cache.py at PROPS9_PC_SEED=9990101: 2,000 instructions read with the
name's option chain cached and not cached - after a restart, or the name dropped from the option chains kept live; reduced in
tests/test_reviews9_property_tester_chain_cache.py).  With the chain not cached the master's option rows of the typed terms were read as
the first row by instrument id - no ROOT_CHOICE, the side read on that one contract: "sell 2 SPX Dec 18 2026 6000 calls" while the
Personal book holds 2 SPXW of them was a sendable SELL TO OPEN of 2 SPX calls, "buy 2 ACME Dec 18 2026 240 calls" while short 2 of them
a BUY TO OPEN of 2 calls of the adjusted series ACME1; and an OCC symbol typed whole of a contract in the master whose expiry had passed
was a ready card that the order preview refuses (INSTRUMENT_EXPIRED).  The master's rows are now read by the chain's root rule (the
standard root first, two roots ROOT_CHOICE; only a contract the master has as active), and an expiry that has passed is EXPIRY_NOT_LISTED
before any contract is read:
  ChainCacheTests (that property module's world, rebuilt here from the shipped corpus world: test_terminal_smart_ticket's books P / F /
  I, Personal selected, Tue 2026-10-20 14:00 ET, ACME at 230 and SPX at 6000 with their cached chains; in the master the ACME, SPX and
  SPXW contracts an owner who traded them has, and from an as-of import the adjusted ACME1 Dec 18 2026 240 call and 200 put and the ACME
  Oct 16 2026 230 call of a trade that expired; P1 short 2 ACME Dec 240 calls, long 3 Dec 250 calls, 2 SPXW Dec 6000 calls and 1 ACME1
  Dec 200 put, F1 100 ACME and short 1 SPX Dec 6000 call)
    test_a  "sell 2 SPX Dec 18 2026 6000 calls": ROOT_CHOICE with the chain cached and not cached, never a sell to open; each choice
            reads its own side (test_a);
    test_b  "buy 2 ACME Dec 18 2026 240 calls": BTC 2 cached; not cached ROOT_CHOICE, its first choice (the standard root) the BTC
            (test_b);
    test_c  "buy 1 ACME261016C00230000" on 2026-10-20: EXPIRY_NOT_LISTED in both readings, as its terms typed are; the order preview
            refuses that request (test_c);
    test_d  a contract the master has as expired (its expiry job ran after its last close, the chain cached before): NOT_TRADEABLE_HERE
            by its terms and typed whole, cached or not - never a card the order preview refuses;
    test_e  the controls: terms on one root read alike with the chain cached or not (the same request); the adjusted series typed whole
            reads;
    test_f  every form, side word and book of the terms on two roots: never sendable with the chain not cached, and with it only on the
            standard root; terms on one root: the same request in both readings;
    test_g  a covered call on terms of two roots (the Fund book's 100 ACME): the root choice too with the chain not cached, never the
            adjusted series sold as covered (case 240 of the seed);
    test_seed_9990101_cases  the run's findings, the first card of each (side word, contract, form) as the seed drew it: 54 K-PICK (the
            chain not cached), the covered call, and 6 K-EXP (both readings).
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
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from urllib.parse import urlsplit

from tests import terminal_smart_corpus as corpus
from tests import test_terminal_browser_smoke as SMK
from tests import test_terminal_simplify_r6 as R6
from tests import test_terminal_simplify_r8 as R8
from tests import test_terminal_smart_ticket as STK
from tests.terminal_cdp import NO_BROWSER, Browser, find_browser
from tests.terminal_helpers import _Log, execute
from finsim.terminal import config, orders_preview, server as _server, smart_ticket as st
from finsim.terminal.clock import FakeClock, epoch_of
from finsim.terminal.instruments import occ_symbol
from finsim.terminal.products import bonds as _bonds
from finsim.terminal.products.options import ensure_option
from finsim.terminal.quotes.cache import ChainCache
from finsim.terminal.quotes.providers import FakeProvider, ParCurve

TERMINAL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "finsim", "terminal")
STATIC = os.path.join(TERMINAL, "static")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# ==== the parse: the master's option rows by the chain's root rule; an expiry that has passed ==========================================
DEC, JAN, PAST = "2026-12-18", "2027-01-15", "2026-10-16"
P, F, I, PB = "P1:US_EXCHANGE", "F1:US_EXCHANGE", "I1:US_EXCHANGE", "I1:PB_MS"
BOOK_WORDS = {"P": "in the Personal book", "F": "in the Fund book", "I": "in the Institutional book"}
ACME_IN_MASTER = (200, 220, 230, 240, 250, 260)
SPX_K = (5900, 6000, 6100)
#: the as-of import's own listing of an imported trade's contract (``orders._asof_instrument``: the spec's underlying, multiplier 100)
IMPORTED = ("ACME1261218C00240000", "ACME1261218P00200000", "ACME261016C00230000")
EXPIRED = "ACME261016C00230000"
EXTRA_HOLD = [(P, "SPXW261218C06000000", "LONG", 2, "81"), (P, "ACME1261218P00200000", "LONG", 1, "0.55"), (F, "SPX261218C06000000", "SHORT", 1, "80")]
#: the terms this world's master lists on two roots: (underlying typed, expiry, right, strike) -> (the standard root's contract, the other's)
TWO_ROOTS = {("ACME", DEC, "C", 240): ("ACME261218C00240000", "ACME1261218C00240000"),
             ("ACME", DEC, "P", 200): ("ACME261218P00200000", "ACME1261218P00200000"),
             ("SPX", DEC, "C", 6000): ("SPX261218C06000000", "SPXW261218C06000000")}
#: terms on one root in the master (SPXW typed: the weekly root only): (typed, expiry, right, strike) -> the one contract
ONE_ROOT = {("ACME", DEC, "C", 250): "ACME261218C00250000", ("ACME", JAN, "C", 240): "ACME270115C00240000",
            ("SPX", DEC, "C", 5900): "SPX261218C05900000", ("SPXW", DEC, "C", 6000): "SPXW261218C06000000"}
#: tests/test_props9_parse_chain_cache.py at PROPS9_PC_SEED=9990101 on f021388: (case, the card's clause, what was wrong) - the first card
#: of each (side word, contract, form) the seed drew that was wrong: K-PICK a card sendable on one of two roots with the chain not cached
#: (the master's first row by id: ACME1 before ACME, SPX before SPXW, the side read on it), K-EXP the expired contract typed whole sendable
#: in both readings
SEED_9990101 = [
    (164, "btc 3 ACME 2026-12-18 240 C limit 1.05 in the Personal book", "K-PICK"),
    (123, "btc 3 ACME 240c 12/18/26 limit 1.05 in the Institutional book", "K-PICK"),
    (243, "btc 2 ACME Dec 18 2026 240 calls gtc in the Personal book", "K-PICK"),
    (254, "btc 1 ACME 2026-12-18 200 P limit 1.05 in the Institutional book", "K-PICK"),
    (95, "btc 1 ACME 200p 12/18/26 limit 2.50 gtc in the Personal book", "K-PICK"),
    (43, "btc 1 ACME Dec 18 2026 200 put limit 2.50 in the Personal book", "K-PICK"),
    (4, "btc 2 SPX 2026-12-18 6000 C in the Institutional book", "K-PICK"),
    (112, "btc 2 SPX 6000c 12/18/26 limit 1.05 gtc in the Fund book", "K-PICK"),
    (102, "btc 1 SPX Dec 18 2026 6000 call limit 2.50 in the Personal book", "K-PICK"),
    (103, "bto 1 ACME 2026-12-18 240 C in the Personal book", "K-PICK"),
    (11, "bto 1 ACME 240c 12/18/26 in the Personal book", "K-PICK"),
    (30, "bto 1 ACME Dec 18 2026 240 call gtc in the Personal book", "K-PICK"),
    (249, "bto 3 ACME 2026-12-18 200 P in the Personal book", "K-PICK"),
    (306, "bto 1 ACME 200p 12/18/26 in the Institutional book", "K-PICK"),
    (301, "bto 2 ACME Dec 18 2026 200 puts in the Personal book", "K-PICK"),
    (427, "bto 2 SPX 2026-12-18 6000 C limit 1.05 gtc in the Fund book", "K-PICK"),
    (55, "bto 2 SPX 6000c 12/18/26 in the Personal book", "K-PICK"),
    (190, "bto 1 SPX Dec 18 2026 6000 call gtc in the Personal book", "K-PICK"),
    (41, "buy 1 ACME 2026-12-18 240 C limit 2.50 in the Personal book", "K-PICK"),
    (16, "buy 1 ACME 240c 12/18/26 limit 2.50 gtc in the Personal book", "K-PICK"),
    (162, "buy 2 ACME Dec 18 2026 240 calls limit 1.05 in the Fund book", "K-PICK"),
    (3, "buy 2 ACME 2026-12-18 200 P gtc in the Personal book", "K-PICK"),
    (175, "buy 1 ACME 200p 12/18/26 in the Institutional book", "K-PICK"),
    (146, "buy 1 ACME Dec 18 2026 200 put gtc in the Institutional book", "K-PICK"),
    (45, "buy 3 SPX 2026-12-18 6000 C limit 2.50 in the Institutional book", "K-PICK"),
    (10, "buy 2 SPX 6000c 12/18/26 in the Personal book", "K-PICK"),
    (79, "buy 3 SPX Dec 18 2026 6000 calls gtc in the Personal book", "K-PICK"),
    (240, "covered call 100 ACME Dec 18 2026 240 in the Fund book", "K-PICK"),
    (81, "sell 2 ACME 2026-12-18 240 C in the Institutional book", "K-PICK"),
    (70, "sell 3 ACME 240c 12/18/26 limit 2.50 in the Fund book", "K-PICK"),
    (58, "sell 2 ACME Dec 18 2026 240 calls in the Institutional book", "K-PICK"),
    (50, "sell 1 ACME 2026-12-18 200 P in the Institutional book", "K-PICK"),
    (745, "sell 2 ACME 200p 12/18/26 limit 1.05 gtc in the Fund book", "K-PICK"),
    (12, "sell 2 ACME Dec 18 2026 200 puts in the Institutional book", "K-PICK"),
    (64, "sell 2 SPX 2026-12-18 6000 C limit 2.50 in the Fund book", "K-PICK"),
    (117, "sell 3 SPX 6000c 12/18/26 limit 2.50 in the Institutional book", "K-PICK"),
    (98, "sell 3 SPX Dec 18 2026 6000 calls gtc in the Personal book", "K-PICK"),
    (375, "stc 3 ACME 2026-12-18 240 C in the Fund book", "K-PICK"),
    (1043, "stc 1 ACME 240c 12/18/26 in the Institutional book", "K-PICK"),
    (128, "stc 3 ACME Dec 18 2026 240 calls in the Institutional book", "K-PICK"),
    (243, "stc 2 ACME 2026-12-18 200 P limit 1.05 in the Personal book", "K-PICK"),
    (172, "stc 1 ACME 200p 12/18/26 gtc in the Institutional book", "K-PICK"),
    (622, "stc 1 ACME Dec 18 2026 200 put in the Institutional book", "K-PICK"),
    (25, "stc 1 SPX 2026-12-18 6000 C limit 2.50 gtc in the Personal book", "K-PICK"),
    (174, "stc 1 SPX 6000c 12/18/26 limit 2.50 in the Fund book", "K-PICK"),
    (71, "stc 1 SPX Dec 18 2026 6000 call limit 2.50 in the Fund book", "K-PICK"),
    (1, "sto 2 ACME 2026-12-18 240 C limit 1.05 in the Fund book", "K-PICK"),
    (753, "sto 2 ACME 240c 12/18/26 limit 1.05 in the Fund book", "K-PICK"),
    (19, "sto 2 ACME Dec 18 2026 240 calls limit 2.50 in the Personal book", "K-PICK"),
    (741, "sto 3 ACME 2026-12-18 200 P limit 2.50 gtc in the Personal book", "K-PICK"),
    (238, "sto 3 ACME 200p 12/18/26 limit 1.05 in the Fund book", "K-PICK"),
    (56, "sto 1 ACME Dec 18 2026 200 put in the Fund book", "K-PICK"),
    (253, "sto 3 SPX 2026-12-18 6000 C limit 1.05 in the Fund book", "K-PICK"),
    (248, "sto 3 SPX 6000c 12/18/26 limit 2.50 in the Fund book", "K-PICK"),
    (348, "sto 3 SPX Dec 18 2026 6000 calls in the Personal book", "K-PICK"),
    (11, "btc 3 ACME261016C00230000 limit 2.50 in the Personal book", "K-EXP"),
    (39, "bto 2 ACME261016C00230000 in the Institutional book", "K-EXP"),
    (3, "buy 2 ACME261016C00230000 limit 1.05 in the Personal book", "K-EXP"),
    (14, "sell 1 ACME261016C00230000 in the Fund book", "K-EXP"),
    (80, "stc 1 ACME261016C00230000 in the Institutional book", "K-EXP"),
    (23, "sto 1 ACME261016C00230000 limit 1.05 in the Institutional book", "K-EXP"),
]


def _occ(root: str, e: str, r: str, k) -> str:
    return occ_symbol(root, date.fromisoformat(e), r, Decimal(str(k)))


def _world9():
    """``(tmp, rt)``: the world of the class docstring (tests/test_props9_parse_chain_cache.py's, which is not shipped); the caller closes
    ``rt`` and removes ``tmp``."""
    tmp, rt = STK._ResolverBase._make()
    try:
        STK.build_world(unittest.TestCase(), rt, dict(corpus.WORLD))
        for e in (DEC, JAN):
            for k in ACME_IN_MASTER:
                for r in ("C", "P"):
                    ensure_option(rt, _occ("ACME", e, r, k))          # listed from the cached chain, as a preview or an order lists it
        for k in SPX_K:
            for r in ("C", "P"):
                ensure_option(rt, _occ("SPX", DEC, r, k))
        ensure_option(rt, "SPXW261218C06000000")
        ensure_option(rt, "SPXW261211C06000000")
        under = rt.instruments.get("ACME")
        with rt.db.writer() as tx:
            for x in IMPORTED:
                rt.instruments.ensure_option(tx, x, under, {"option": x})
        rt.instruments.load()
        for aid, iid, side, qty, px in EXTRA_HOLD:
            res = execute(rt, "test.smart.open", {"account_id": aid, "instrument_id": iid, "side": side, "qty": qty, "price": px})
            assert res.ok, (aid, iid, res.body)
        missing = [x for x in IMPORTED + tuple(c for pair in TWO_ROOTS.values() for c in pair) + tuple(ONE_ROOT.values()) if rt.instruments.get(x) is None]
        assert not missing, missing
    except BaseException:
        rt.close()
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return tmp, rt


@contextmanager
def _no_chains(rt):
    """An empty chain cache: FinSim after a restart, or with the names dropped from its "option chains kept live"."""
    keep = rt.chains
    rt.chains = ChainCache(rt.clock, max_entries=12)
    try:
        yield
    finally:
        rt.chains = keep


def _card(leg: dict) -> str:
    req = leg.get("request")
    return (f"{(leg.get('instrument') or {}).get('id')} request {None if req is None else (req.get('side'), req.get('qty'), req.get('instrument_id'), req.get('account_id'))}"
            f" flags {[(f['code'], f['severity']) for f in leg['flags']]}")


class ChainCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.rt = _world9()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.rt.close()
        finally:
            shutil.rmtree(cls.tmp, ignore_errors=True)

    def read(self, text: str, cached: bool = True) -> dict:
        """The one card of ``text`` as the parse route answers it (``smart_ticket.parse_view``, pure), the chain cached or not."""
        if cached:
            out = st.parse_view(self.rt, {"text": text, "book": "P"})
        else:
            with _no_chains(self.rt):
                out = st.parse_view(self.rt, {"text": text, "book": "P"})
        self.assertEqual(1, len(out["legs"]), out)
        return out["legs"][0]

    @staticmethod
    def req(leg: dict):
        r = leg.get("request")
        return None if r is None else (r["side"], int(r["qty"]), r["instrument_id"], r["account_id"])

    @staticmethod
    def blockers(leg: dict) -> list:
        return [f["code"] for f in leg["flags"] if f["severity"] == "blocker"]

    def flag(self, leg: dict, code: str) -> dict:
        got = [f for f in leg["flags"] if f["code"] == code]
        self.assertTrue(got, f"no {code}: {_card(leg)}")
        return got[0]

    def test_a_sell_spx_while_holding_spxw_is_a_root_choice_with_or_without_the_chain(self):
        text = "sell 2 SPX Dec 18 2026 6000 calls"
        labels = ["SPX261218C06000000 (root SPX, the standard root)", "SPXW261218C06000000 (root SPXW)"]
        for cached in (True, False):
            leg = self.read(text, cached)
            self.assertIsNone(leg["request"], f"cached {cached}: {_card(leg)}")
            f = self.flag(leg, "ROOT_CHOICE")
            self.assertEqual("blocker", f["severity"])
            self.assertEqual(labels, [c["label"] for c in f["choices"]], cached)
            self.assertEqual(["sell 2 SPX261218C06000000", "sell 2 SPXW261218C06000000"], [c["clause"] for c in f["choices"]], cached)
            if not cached:
                self.assertIn("the instrument master lists 2 SPX contracts for 2026-12-18 6000 call (roots SPX, SPXW): pick one", f["text"])
            # each choice reads its own side: the SPXW calls held sell to close, the SPX calls not held sell to open (its check says so)
            self.assertEqual(("STC", 2, "SPXW261218C06000000", P), self.req(self.read("sell 2 SPXW261218C06000000", cached)))
            spx = self.read("sell 2 SPX261218C06000000", cached)
            self.assertEqual(("STO", 2, "SPX261218C06000000", P), self.req(spx))
            self.assertIn("SIDE_INFERRED", [x["code"] for x in spx["flags"]])

    def test_b_buy_while_short_buys_to_close_or_is_the_choice_never_a_buy_of_the_adjusted_root(self):
        text = "buy 2 ACME Dec 18 2026 240 calls"
        self.assertEqual(("BTC", 2, "ACME261218C00240000", P), self.req(self.read(text, True)))   # the chain lists the standard root only
        leg = self.read(text, False)
        self.assertIsNone(leg["request"], _card(leg))
        f = self.flag(leg, "ROOT_CHOICE")
        self.assertEqual([("ACME261218C00240000 (root ACME, the standard root)", "buy 2 ACME261218C00240000"),
                          ("ACME1261218C00240000 (root ACME1)", "buy 2 ACME1261218C00240000")], [(c["label"], c["clause"]) for c in f["choices"]])
        for cached in (True, False):
            self.assertEqual(("BTC", 2, "ACME261218C00240000", P), self.req(self.read(f["choices"][0]["clause"], cached)), cached)
            self.assertEqual(("BTO", 2, "ACME1261218C00240000", P), self.req(self.read(f["choices"][1]["clause"], cached)), cached)

    def test_c_an_expired_contract_typed_whole_is_refused_as_its_terms_are(self):
        for cached in (True, False):
            by_terms = self.read("buy 1 ACME Oct 16 2026 230 call", cached)
            self.assertIsNone(by_terms["request"], _card(by_terms))
            self.assertIn("EXPIRY_NOT_LISTED", self.blockers(by_terms))
            leg = self.read("buy 1 " + EXPIRED, cached)
            self.assertIsNone(leg["request"], f"cached {cached}: {_card(leg)}")
            self.assertEqual(["EXPIRY_NOT_LISTED"], self.blockers(leg), _card(leg))
            self.assertEqual(EXPIRED + " expired on " + PAST, self.flag(leg, "EXPIRY_NOT_LISTED")["text"])
        # what the card would have sent: the order preview refuses it - so a ready card would have said what Send cannot do
        pv = orders_preview.preview(self.rt, {"account_id": P, "instrument_id": EXPIRED, "side": "BTO", "qty": 1, "order_type": "MARKET", "tif": "DAY",
                                              "request_id": "ui:simplify-r9-c-0001"})
        self.assertIn("INSTRUMENT_EXPIRED", [b.get("code") for b in pv.get("blockers") or []], pv)

    def test_d_a_contract_the_master_has_as_expired_is_never_a_ready_card(self):
        iid = "ACME270115P00260000"                 # on the cached chain and in the master, held by no one
        text = "buy 1 ACME Jan 15 2027 260 put"
        for cached in (True, False):
            self.assertEqual(("BTO", 1, iid, P), self.req(self.read(text, cached)), cached)           # the control: ACTIVE
        self.set_status(iid, "EXPIRED")
        self.addCleanup(self.set_status, iid, "ACTIVE")
        for cached in (True, False):
            for words in (text, "buy 1 " + iid):
                leg = self.read(words, cached)
                self.assertIsNone(leg["request"], f"{words!r} cached {cached}: {_card(leg)}")
                self.assertEqual(["NOT_TRADEABLE_HERE"], self.blockers(leg), _card(leg))
                self.assertIn(iid + " is expired in the instrument master", self.flag(leg, "NOT_TRADEABLE_HERE")["text"])
        pv = orders_preview.preview(self.rt, {"account_id": P, "instrument_id": iid, "side": "BTO", "qty": 1, "order_type": "MARKET", "tif": "DAY",
                                              "request_id": "ui:simplify-r9-d-0001"})
        self.assertIn("INSTRUMENT_EXPIRED", [b.get("code") for b in pv.get("blockers") or []], pv)

    def set_status(self, iid: str, status: str) -> None:
        with self.rt.db.writer() as tx:
            tx.conn.execute("UPDATE instruments SET status=? WHERE instrument_id=?", (status, iid))
            self.rt.instruments.refresh(iid, tx.conn)
        self.assertEqual(status, self.rt.instruments.get(iid).status)

    def test_e_controls_one_root_reads_alike_and_the_adjusted_series_typed_whole_reads(self):
        for text, want in (("sell 1 ACME Dec 18 2026 250 call", ("STC", 1, "ACME261218C00250000", P)),
                           ("buy 1 SPXW Dec 18 2026 6000 call", ("BTO", 1, "SPXW261218C06000000", P)),
                           ("buy 1 ACME Jan 15 2027 240 call", ("BTO", 1, "ACME270115C00240000", P)),
                           ("buy 1 ACME1261218C00240000", ("BTO", 1, "ACME1261218C00240000", P)),
                           ("sell 1 ACME1261218P00200000", ("STC", 1, "ACME1261218P00200000", P))):
            for cached in (True, False):
                self.assertEqual(want, self.req(self.read(text, cached)), f"{text!r} cached {cached}")

    def test_g_a_covered_call_on_terms_of_two_roots_is_never_a_silent_pick_either(self):
        text = "covered call 100 ACME Dec 18 2026 240 in the Fund book"                  # the chain lists the standard root only
        self.assertEqual(("STO", 1, "ACME261218C00240000", F), self.req(self.read(text, True)))
        leg = self.read(text, False)
        self.assertIsNone(leg["request"], _card(leg))
        self.assertIn("ROOT_CHOICE", self.blockers(leg))

    def test_f_every_form_side_and_book_reads_alike_with_or_without_the_chain(self):
        mon = {DEC: ("Dec 18 2026", "12/18/26"), JAN: ("Jan 15 2027", "1/15/27")}
        bad, n = [], 0
        for terms in list(TWO_ROOTS) + list(ONE_ROOT):
            u, e, r, k = terms
            words, us = mon[e]
            forms = [f"{u} {words} {k} {'call' if r == 'C' else 'put'}", f"{u} {k}{r.lower()} {us}", f"{u} {e} {k} {r}"]
            for side in ("buy", "sell", "bto", "sto", "btc", "stc"):
                for form in forms:
                    for b in ("P", "F", "I"):
                        text = f"{side} 1 {form} {BOOK_WORDS[b]}"
                        n += 1
                        c, x = self.read(text, True), self.read(text, False)
                        if terms in TWO_ROOTS:
                            std = TWO_ROOTS[terms][0]
                            if x["request"] is not None or "ROOT_CHOICE" not in self.blockers(x):
                                bad.append(f"{text!r} not cached: {_card(x)}")
                            if c["request"] is not None and c["request"]["instrument_id"] != std:
                                bad.append(f"{text!r} cached on another root than the standard one: {_card(c)}")
                        else:
                            if c["request"] is not None and x["request"] is not None and self.req(c) != self.req(x):
                                bad.append(f"{text!r} reads otherwise with the chain cached {_card(c)} and not {_card(x)}")
                            if x["request"] is not None and x["request"]["instrument_id"] != ONE_ROOT[terms]:
                                bad.append(f"{text!r} not cached on {_card(x)}")
                            if (c["request"] is None) != (x["request"] is None):
                                bad.append(f"{text!r} sendable in one reading only: cached {_card(c)} / not {_card(x)}")
        self.assertGreater(n, 350)
        self.assertEqual([], bad[:8], f"{len(bad)} of {n}")

    def test_seed_9990101_cases(self):
        bad = []
        for case, text, kind in SEED_9990101:
            c, x = self.read(text, True), self.read(text, False)
            if kind == "K-EXP":
                for cached, leg in ((True, c), (False, x)):
                    if leg["request"] is not None or "EXPIRY_NOT_LISTED" not in self.blockers(leg):
                        bad.append(f"case {case} {text!r} cached {cached}: {_card(leg)}")
                continue
            terms = next(t for t in TWO_ROOTS if re.search(r"\b%s\b" % t[0], text) and re.search(r"\b%d" % t[3], text))
            std, other = TWO_ROOTS[terms]
            roots = "(roots %s, %s)" % tuple(re.sub(r"\d{6}[CP]\d{8}$", "", cn) for cn in (std, other))
            if x["request"] is not None or "ROOT_CHOICE" not in self.blockers(x):
                bad.append(f"case {case} {text!r} not cached: {_card(x)}")
            elif roots not in self.flag(x, "ROOT_CHOICE")["text"]:
                bad.append(f"case {case} {text!r}: the choice does not name {roots}: {self.flag(x, 'ROOT_CHOICE')['text']}")
            if c["request"] is not None and c["request"]["instrument_id"] != std:
                bad.append(f"case {case} {text!r} cached on another root than the standard one: {_card(c)}")
        self.assertEqual([], bad)


# ==== the page: a double click on a desk's own send button; the submit key held down ===================================================
PERSONAL, FUND = config.BOOKS[0], config.BOOKS[1]
EURUSD_LAST = "1.0850"
BOND = "ACME-31"
SPREAD = "buy 1 ACME Dec 18 2026 60/65 call spread"
LOADED = "!!(window.T && T.router && T.router.current && document.querySelector('#page') && T.state.books && T.state.books.length === 2 && T.book.code)"
#: an invented Treasury par curve of the day before (2Y 4.00 %, 10Y 4.50 %, as the bond tests make one): what prices the bond
CURVE = ParCurve(date(2026, 10, 19), {Decimal(2): Decimal("4.00"), Decimal(10): Decimal("4.50")}, source="test curve (invented)")
GAP_S = 0.15                              # a brisk double click: the second press 150 ms after the first (Windows takes up to 500 ms)
FX_ROOT, BOND_ROOT = "#dock-body .fxd", "#dock-body .bt"
FX_BTN = "document.querySelector('#dock-body .fxd .acts .btn.primary')"
BOND_BTN = ("(Array.prototype.filter.call(document.querySelectorAll('#dock-body .bt button.btn.primary'),"
            " function (b) { return String(b.textContent).trim() === 'Submit'; })[0] || null)")
PKG_BTN = "document.querySelector('#hg-submit')"
TICKET_BTN = "document.querySelector('#ticket-submit-btn')"
FX_AMOUNT, BOND_FACE = FX_ROOT + ' input[aria-label="amount"]', BOND_ROOT + ' input[aria-label="face amount"]'
ARMED = "(function () { var b = %s; return !!(b && !b.disabled && T.dock && T.dock.visible()); })()"
BTN = "(function () { var b = %s; return b ? { disabled: !!b.disabled, title: b.title || '', text: String(b.textContent).trim() } : null; })()"
TEXT_OF = "(function () { var s = document.querySelector(%s); return s ? s.textContent : null; })()"
TICKET_STATUS = "(document.getElementById('ticket-status') || {}).textContent || ''"
RECT = """(function () { var b = %s; if (!b) { return null; } b.scrollIntoView({ block: 'center' }); var q = b.getBoundingClientRect();
  var x = (q.left + q.right) / 2, y = (q.top + q.bottom) / 2, e = document.elementFromPoint(x, y);
  return { x: x, y: y, hit: !!(e && (e === b || b.contains(e))) }; })()"""
CLICK_TEXT = ("(function (sel, words) { var b = Array.prototype.filter.call(document.querySelectorAll(sel), function (x) {"
              " return String(x.textContent).trim() === words; })[0]; if (!b) { return false; } b.click(); return true; })(%s, %s)")
FX_ALONE = "not booked: Confirm takes a click of its own (that was the second click of a double click) - click Confirm once to book the deal"
BOND_ALONE = "not traded: Submit takes a click of its own (that was the second click of a double click) - click Submit once to trade"
PKG_ALONE = "not sent: Submit package takes a click of its own (that was the second click of a double click) - click Submit package once to send"
FX_HELD = ("booked: Confirm is held - change a field or press Preview to deal again", "deal booked (above): change a field or press Preview to deal again")
BOND_HELD = ("filled: Submit is held - change the bond, the side or the face, or press Preview, to trade again",
             "trade filled (above): change the bond, the side or the face, or press Preview, to trade again")
PKG_SENT = "Package sent - preview it again to send another"


def _seed9(home: str, clock: FakeClock, fp: FakeProvider) -> None:
    """The Personal and the Fund books; ACME / WIDG and their prints; an invented EURUSD print; the fictional bond ACME-31 (the bond tests'
    own listing helper, priced by the par-curve model); the ACME chain."""
    rt = _server.build_runtime(home, os.path.join(home, "terminal.db"), clock=clock, offline=True, fake_provider=fp, log=_Log(), books=[PERSONAL, FUND],
                               fetch_threads=0)
    try:
        aapl = rt.instruments.get("AAPL")
        with rt.db.writer() as tx:
            for sym, (name, _last) in SMK.SMART_NAMES.items():
                rt.instruments.upsert(tx, instrument_id=sym, kind="EQUITY", symbol=sym, name=name, currency="USD", session_group=aapl.session_group,
                                      account_kinds=list(aapl.account_kinds), yahoo_symbol=sym, cboe_symbol=sym, spec={"exchange": "NYQ"})
            _bonds.ensure_bond(tx, rt.instruments, BOND, "ACME Corp 5.000 % 15-Jun-2031 (fictional)", "5.0", date(2031, 6, 15), date(2021, 6, 15),
                               spec={"issuer": "ACME Corp"})
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


class _Server9(SMK._Server):
    """``SMK._Server`` with the Personal and the Fund books (port 0, offline, the seeded FakeProvider)."""

    def __init__(self, home, clock, fp):
        super().__init__(home, clock, fp)
        self.thread = threading.Thread(target=self._run9, name="simplify-r9-serve", daemon=True)

    def _run9(self):
        self.rc = _server.serve(port=0, home=self.home, db=os.path.join(self.home, "terminal.db"), offline=True, ready=self._ready, out=self.msgs.append,
                                clock=self.clock, fake_provider=self.fp, books=[PERSONAL, FUND], fetch_threads=0, supervisor_interval=0.5, log=_Log())


@unittest.skipUnless(find_browser(), NO_BROWSER)
class PageTests(unittest.TestCase):
    """One offline server (port 0, a temp home, the world of ``_seed9``) with the smoke test's invented ACME chain cached and the invented
    par curve, and one headless browser for the class, both closed in tearDownClass."""
    last_click: dict = {}                # what last took a click of its own: "fx" / "bond" / "package" -> when (each rule looks 5 s back)

    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp(prefix="fst-simplify-r9-page-")
        cls.env = {k: os.environ.get(k) for k in (config.HOME_ENV, config.DB_ENV)}
        os.environ[config.HOME_ENV] = cls.home
        os.environ[config.DB_ENV] = os.path.join(cls.home, "terminal.db")
        cls.server = cls.browser = None
        cls.last_click = {}
        try:
            clock = FakeClock(SMK.AT)
            cls.fp = FakeProvider(clock)
            _seed9(cls.home, clock, cls.fp)
            cls.server = _Server9(cls.home, FakeClock(SMK.AT), cls.fp).start()
            cls.server.rt.curve.put(CURVE, persist=False)
            SMK.BrowserSmokeTests._served_chain(cls)                     # the chain as the quote worker caches a fetched one
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

    # the page as the owner uses it (test_terminal_simplify_r6's and _r8's helpers)
    js = R6.AnswerFencePageTests.js
    wait = R6.AnswerFencePageTests.wait
    enter_key = R6.AnswerFencePageTests.enter_key
    fresh_smart = R6.AnswerFencePageTests.fresh_smart
    read = R6.AnswerFencePageTests.read
    mouse = R8.PageTests.mouse
    click_xy = R8.PageTests.click_xy
    set_box = R8.PageTests.set_box
    order_posts = R8.PageTests.order_posts

    def tidy(self) -> None:
        def undo():
            self.js("(function () { localStorage.removeItem(%s); sessionStorage.removeItem(%s); location.hash = '#/dashboard'; return true; })()"
                    % (json.dumps(R6.STORE_KEY), json.dumps(R6.WIN_KEY)))
            self.browser.pump(0.6)
        self.addCleanup(undo)

    # ---- the rows each send books, and the requests the page sent ----------------------------------------------------------------------
    def rows(self, sql: str, args=()) -> list:
        with self.server.rt.db.reader() as conn:
            return [tuple(r) for r in conn.execute(sql, args).fetchall()]

    def fx_deals(self) -> list:
        return self.rows("SELECT * FROM fx_deals ORDER BY rowid")

    def bond_fills(self) -> list:
        return self.rows("SELECT fill_id, side, qty, price FROM fills WHERE instrument_id=? ORDER BY rowid", (BOND,))

    def packages(self) -> list:
        return self.rows("SELECT package_id, account_id, primary_order_id FROM hedge_packages ORDER BY rowid")

    def orders(self) -> list:
        return self.rows("SELECT order_id, instrument_id, side, qty, order_type, limit_px, status FROM orders ORDER BY rowid")

    def posts(self, since: int, path: str) -> list:
        """The request ids of every POST the page sent to ``path`` since ``since``."""
        out = []
        for e in self.browser._events(self.sid, since):
            if e.get("method") == "Network.requestWillBeSent":
                r = (e.get("params") or {}).get("request") or {}
                if r.get("method") == "POST" and urlsplit(r.get("url") or "").path == path:
                    try:
                        out.append(json.loads(r.get("postData") or "{}").get("request_id"))
                    except ValueError:
                        out.append("?")
        return out

    def wait_more(self, rows, before: list, n: int = 1, timeout: float = 15.0) -> list:
        """The rows booked since ``before`` once there are ``n`` (or the timeout), and 2 s more: what a second click could have started."""
        deadline = time.time() + timeout
        while time.time() < deadline and len(rows()) - len(before) < n:
            self.browser.pump(0.2)
        self.browser.pump(2.0)
        return rows()[len(before):]

    # ---- the pointer and the keyboard, as CDP sends them -------------------------------------------------------------------------------
    def point(self, btn: str) -> tuple:
        r = self.js(RECT % btn)
        self.assertTrue(r and r["hit"], f"the button is not under its own middle: {r}")
        return r["x"], r["y"]

    def double_click(self, btn: str, what: str) -> None:
        """A real double click on ``btn``: one click, then the second press and release (click count 2) GAP_S later."""
        x, y = self.point(btn)
        self.click_xy(x, y)
        type(self).last_click[what] = time.time()
        time.sleep(GAP_S)
        self.mouse("mousePressed", x, y, 2)
        self.mouse("mouseReleased", x, y, 2)

    def click_once(self, btn: str, what: str) -> None:
        x, y = self.point(btn)
        self.click_xy(x, y)
        type(self).last_click[what] = time.time()

    def second_click_alone(self, btn: str, what: str, elsewhere: str) -> None:
        """The second press and release of a double click (click count 2) on ``btn`` with no click on it before: the pointer first clicks
        the box ``elsewhere`` (a press of a new gesture: the rest of the hand-off's gesture is over - the Smart Ticket page drops it), then
        past the double-click time and at least 5 s after the button's last click of its own (the rule looks 5 s back) the second press."""
        self.click_xy(*self.point("document.querySelector(%s)" % json.dumps(elsewhere)))
        time.sleep(max(0.6, 5.3 - (time.time() - type(self).last_click.get(what, 0.0))))
        x, y = self.point(btn)
        self.mouse("mouseMoved", x, y)
        self.mouse("mousePressed", x, y, 2)
        self.mouse("mouseReleased", x, y, 2)

    def focus(self, sel: str) -> None:
        self.js("(function () { var b = document.querySelector(%s); b.focus(); return document.activeElement === b; })()" % json.dumps(sel))

    def submit_key(self, repeat: bool = False, up: bool = True) -> None:
        """Ctrl+Enter (the submit key's default) where the focus is: a press (autoRepeat: a repeat of a key held down), then its release."""
        base = {"key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13, "modifiers": 2}
        self.browser.call("Input.dispatchKeyEvent", dict(base, type="rawKeyDown", autoRepeat=repeat), session=self.sid)
        if up:
            self.browser.call("Input.dispatchKeyEvent", dict(base, type="keyUp"), session=self.sid)

    def hold_submit_key(self, repeats: int = 8, every: float = 0.3) -> None:
        """The submit key held down: its press, ``repeats`` repeats ``every`` s apart (as the keyboard repeats it), its release."""
        self.submit_key(up=False)
        for _ in range(repeats):
            time.sleep(every)
            self.submit_key(repeat=True, up=False)
        base = {"key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13, "modifiers": 2}
        self.browser.call("Input.dispatchKeyEvent", dict(base, type="keyUp"), session=self.sid)

    # ---- the Smart Ticket's hand-offs --------------------------------------------------------------------------------------------------
    def own_action(self, i: int):
        """Card ``i``'s red-flag chips opened one at a time until its flag box offers "Open the ..."; returns that action."""
        act = ("(function () { var c = document.querySelector('#smart-cards [data-i=\"%d\"]'); if (!c) { return null; }"
               " var b = Array.prototype.filter.call(c.querySelectorAll('[data-act=\"action\"]'), function (x) { return /^Open the /.test(x.textContent || ''); })[0];"
               " return b ? { label: String(b.textContent).trim(), enabled: !b.disabled } : null; })()" % i)
        n = self.js("document.querySelectorAll('#smart-cards [data-i=\"%d\"] .smart-chip.sev-blocker').length" % i) or 0
        for k in range(n):
            if self.js(act):
                break
            chip = "document.querySelectorAll('#smart-cards [data-i=\"%d\"] .smart-chip.sev-blocker')[%d]" % (i, k)
            self.js("(function () { var c = %s; if (c) { c.click(); } return true; })()" % chip)
            self.browser.pump(0.25)
            if not self.js(act):
                self.js("(function () { var c = %s; if (c) { c.click(); } return true; })()" % chip)
                self.browser.pump(0.2)
        return self.js(act)

    def open_desk(self, text: str, root: str, btn: str) -> None:
        """``text`` read (one card) in the Personal book, its "Open the ..." pressed, the desk's button armed (its first clean preview:
        the fictional prints are seeded two minutes old, so it can take the desk's next preview)."""
        self.fresh_smart("P")
        self.read(text, 1)
        a = self.own_action(0)
        self.assertTrue(a and a["enabled"], f"{text!r}: the card's own-ticket action is not there to press: {a}")
        self.js("(function () { var c = document.querySelector('#smart-cards [data-i=\"0\"]');"
                " var b = Array.prototype.filter.call(c.querySelectorAll('[data-act=\"action\"]'), function (x) { return /^Open the /.test(x.textContent || ''); })[0];"
                " b.click(); return true; })()")
        self.wait("location.hash.indexOf('#/a/') === 0 && !!document.querySelector(%s) && T.dock.visible()" % json.dumps(root), timeout=10)
        self.wait(ARMED % btn, timeout=60)
        self.browser.pump(0.6)

    def open_strategies(self) -> None:
        """SPREAD read, its group's "Strategies tab" pressed, a put template (else the first) picked with a tenor of 1 month (the
        invented chain's 2026-12-18 expiry, as the smoke test picks it), priced: Submit package armed."""
        self.fresh_smart("P")
        self.read(SPREAD, 2)
        strat = '#smart-cards .smart-group [data-act="strategies"]'
        self.wait("!!document.querySelector(%s) && !document.querySelector(%s).disabled" % (json.dumps(strat), json.dumps(strat)), timeout=10)
        self.js("document.querySelector(%s).click(); true" % json.dumps(strat))
        self.wait("location.hash.indexOf('#/a/') === 0 && !!document.querySelector('#hg-strategy') && document.querySelector('#hg-strategy').options.length > 1",
                  timeout=40)
        keys = self.js("Array.prototype.map.call(document.querySelector('#hg-strategy').options, function (o) { return [o.value, o.textContent]; })")
        pick = next((k for k, title in keys if k and not title.startswith("[n/a]") and "PUT" in k.upper()), None) or next(
            k for k, title in keys if k and not title.startswith("[n/a]"))
        self.js("(function () { var s = document.querySelector('#hg-strategy'); s.value = %s; s.dispatchEvent(new Event('change', { bubbles: true }));"
                " var x = document.querySelector('#ticket-hedge input[aria-label=\"tenor months\"]'); x.value = '1';"
                " x.dispatchEvent(new Event('input', { bubbles: true })); return true; })()" % json.dumps(pick))
        self.wait("!!document.querySelector('#hg-submit') && !document.querySelector('#hg-submit').disabled", timeout=40)
        self.browser.pump(0.6)

    # ---- the FX desk -------------------------------------------------------------------------------------------------------------------
    def test_a_a_double_click_on_the_fx_desks_confirm_books_one_deal_and_holds_confirm(self):
        self.tidy()
        self.open_desk("buy 10k EURUSD", FX_ROOT, FX_BTN)
        before, start = self.fx_deals(), self.browser.mark()
        self.double_click(FX_BTN, "fx")
        new = self.wait_more(self.fx_deals, before)
        ids = self.posts(start, "/api/fx/deal")
        self.assertEqual((1, 1), (len(new), len(ids)), f"one double click on Confirm: {len(ids)} POST /api/fx/deal {ids}, booked {new}")
        self.assertRegex(self.js(TEXT_OF % json.dumps(FX_ROOT + " .st")), r"^booked ")
        b = self.js(BTN % FX_BTN)
        self.assertEqual((True, FX_HELD[0]), (b["disabled"], b["title"]), "Confirm is held after the booking (2 s on: no preview of the same deal armed it)")
        self.assertEqual(FX_HELD[1], self.js(TEXT_OF % json.dumps(FX_ROOT + " .pv")))
        # the submit key while held: nothing booked, said
        self.focus(FX_AMOUNT)
        self.submit_key()
        self.browser.pump(1.0)
        self.assertEqual(1, len(self.fx_deals()) - len(before))
        self.assertRegex(self.js(TEXT_OF % json.dumps(FX_ROOT + " .st")), r"^not booked again: D-\S+ \(FX spot BUY EUR / SELL USD \S+ EUR in .+\) was booked"
                                                                          r" - change a field or press Preview to deal again$")
        # the control: Preview pressed ends the hold - a click of its own books the next deal, once
        self.assertTrue(self.js(CLICK_TEXT % (json.dumps(FX_ROOT + " .acts button"), json.dumps("Preview"))))
        self.wait(ARMED % FX_BTN, timeout=45)
        self.assertNotEqual(FX_HELD[0], self.js(BTN % FX_BTN)["title"])
        self.browser.pump(0.4)
        self.click_once(FX_BTN, "fx")
        new = self.wait_more(self.fx_deals, before, 2)
        self.assertEqual((2, 2), (len(new), len(self.posts(start, "/api/fx/deal"))), new)
        self.assertEqual([], self.browser.errors(self.sid, start))

    def test_b_the_fx_desks_confirm_takes_a_click_of_its_own_and_the_held_key_books_once(self):
        self.tidy()
        self.open_desk("buy 10k EURUSD", FX_ROOT, FX_BTN)
        before, start = self.fx_deals(), self.browser.mark()
        self.second_click_alone(FX_BTN, "fx", FX_AMOUNT)
        self.browser.pump(1.5)
        self.assertEqual(([], before), (self.posts(start, "/api/fx/deal"), self.fx_deals()), "the second click of a double click alone books nothing")
        self.assertEqual(FX_ALONE, self.js(TEXT_OF % json.dumps(FX_ROOT + " .st")))
        self.assertFalse(self.js(BTN % FX_BTN)["disabled"], "nothing was booked: Confirm is still armed")
        # the submit key: a repeat alone (a key held down from before) books nothing; a press and its repeats book once
        self.focus(FX_AMOUNT)
        self.submit_key(repeat=True)
        self.browser.pump(1.2)
        self.assertEqual(([], before), (self.posts(start, "/api/fx/deal"), self.fx_deals()), "a repeat alone books nothing")
        self.hold_submit_key()
        new = self.wait_more(self.fx_deals, before)
        self.assertEqual((1, 1), (len(new), len(self.posts(start, "/api/fx/deal"))), new)
        self.assertEqual([], self.browser.errors(self.sid, start))

    # ---- the bond ticket -----------------------------------------------------------------------------------------------------------------
    def test_c_a_double_click_on_the_bond_tickets_submit_fills_one_trade_and_holds_submit(self):
        self.tidy()
        self.open_desk("buy 1k " + BOND, BOND_ROOT, BOND_BTN)
        before, start = self.bond_fills(), self.browser.mark()
        self.double_click(BOND_BTN, "bond")
        new = self.wait_more(self.bond_fills, before)
        ids = self.posts(start, "/api/bonds/trade")
        self.assertEqual((1, 1), (len(new), len(ids)), f"one double click on Submit: {len(ids)} POST /api/bonds/trade {ids}, filled {new}")
        self.assertRegex(self.js(TEXT_OF % json.dumps(BOND_ROOT + " .st")), r"^filled: BUY 1,000 ACME-31 @ ")
        b = self.js(BTN % BOND_BTN)
        self.assertEqual((True, BOND_HELD[0]), (b["disabled"], b["title"]))
        self.assertEqual(BOND_HELD[1], self.js(TEXT_OF % json.dumps(BOND_ROOT + " .pv")))
        self.focus(BOND_FACE)
        self.submit_key()
        self.browser.pump(1.0)
        self.assertEqual(1, len(self.bond_fills()) - len(before))
        self.assertRegex(self.js(TEXT_OF % json.dumps(BOND_ROOT + " .st")), r"^not traded again: \S+ \(bond BUY 1,000 ACME-31 in .+\) filled - change the bond, the"
                                                                            r" side or the face, or press Preview, to trade again$")
        # the control: the face changed ends the hold - a click of its own fills the next trade, once
        self.set_box(BOND_FACE, "2000")
        self.wait(ARMED % BOND_BTN, timeout=45)
        self.browser.pump(0.4)
        self.click_once(BOND_BTN, "bond")
        new = self.wait_more(self.bond_fills, before, 2)
        self.assertEqual([1000, 2000], [int(Decimal(str(r[2]))) for r in new])
        self.assertEqual(2, len(self.posts(start, "/api/bonds/trade")))
        self.assertEqual([], self.browser.errors(self.sid, start))

    def test_d_the_bond_tickets_submit_takes_a_click_of_its_own_and_the_held_key_trades_once(self):
        self.tidy()
        self.open_desk("buy 1k " + BOND, BOND_ROOT, BOND_BTN)
        before, start = self.bond_fills(), self.browser.mark()
        self.second_click_alone(BOND_BTN, "bond", BOND_FACE)
        self.browser.pump(1.5)
        self.assertEqual(([], before), (self.posts(start, "/api/bonds/trade"), self.bond_fills()))
        self.assertEqual(BOND_ALONE, self.js(TEXT_OF % json.dumps(BOND_ROOT + " .st")))
        self.assertFalse(self.js(BTN % BOND_BTN)["disabled"])
        self.focus(BOND_FACE)
        self.submit_key(repeat=True)
        self.browser.pump(1.2)
        self.assertEqual(([], before), (self.posts(start, "/api/bonds/trade"), self.bond_fills()), "a repeat alone trades nothing")
        self.hold_submit_key()
        new = self.wait_more(self.bond_fills, before)
        self.assertEqual((1, 1), (len(new), len(self.posts(start, "/api/bonds/trade"))), new)
        self.assertEqual([], self.browser.errors(self.sid, start))

    # ---- the order ticket's Strategies tab (a group's "Strategies tab") ------------------------------------------------------------------
    def test_e_a_double_click_on_submit_package_sends_one_package_and_holds_it(self):
        self.tidy()
        self.open_strategies()
        before, start = self.packages(), self.browser.mark()
        self.double_click(PKG_BTN, "package")
        new = self.wait_more(self.packages, before)
        ids = self.posts(start, "/api/orders")
        self.assertEqual((1, 1), (len(new), len(ids)), f"one double click on Submit package: {len(ids)} POST /api/orders {ids}, packages {new};"
                                                       f" ticket status {self.js(TICKET_STATUS)!r}")
        b = self.js(BTN % PKG_BTN)
        self.assertEqual((True, PKG_SENT), (b["disabled"], b["text"]), "the package sent holds its button")
        # a new preview of the package arms it again: the second click of a double click alone sends nothing, said; a click of its own sends
        self.assertTrue(self.js(CLICK_TEXT % (json.dumps("#ticket-hedge button"), json.dumps("Preview package"))))
        self.wait("(function () { var b = %s; return !!(b && !b.disabled && String(b.textContent).trim() !== %s); })()" % (PKG_BTN, json.dumps(PKG_SENT)),
                  timeout=40)
        self.browser.pump(0.4)
        self.second_click_alone(PKG_BTN, "package", "#ticket-qty")
        self.browser.pump(1.5)
        self.assertEqual(1, len(self.posts(start, "/api/orders")), "the second click of a double click alone sends nothing")
        self.assertEqual(PKG_ALONE, self.js(TICKET_STATUS))
        self.assertFalse(self.js(BTN % PKG_BTN)["disabled"])
        self.click_once(PKG_BTN, "package")
        new = self.wait_more(self.packages, before, 2)
        self.assertEqual((2, 2), (len(new), len(self.posts(start, "/api/orders"))), (new, self.js(TICKET_STATUS)))
        self.assertEqual([], self.browser.errors(self.sid, start))

    # ---- the order ticket: the submit key held down --------------------------------------------------------------------------------------
    def test_f_the_order_tickets_submit_key_held_down_places_one_order(self):
        self.tidy()
        self.fresh_smart("P")
        self.read("buy 10 ACME limit 59", 1)
        self.js("document.querySelector('#smart-cards [data-i=\"0\"] [data-act=\"open\"]').click(); true")
        self.wait("location.hash.indexOf('#/a/') === 0 && !!(T.dock && T.dock.visible()) && !!document.querySelector('#ticket-instrument')"
                  " && document.querySelector('#ticket-instrument').value === 'ACME'", timeout=30)
        self.wait(ARMED % TICKET_BTN, timeout=45)
        self.browser.pump(0.4)
        before, start = self.orders(), self.browser.mark()
        self.focus("#ticket-qty")
        self.submit_key(repeat=True)
        self.browser.pump(1.2)
        self.assertEqual(([], before), (self.order_posts(start), self.orders()), "a repeat alone places nothing")
        self.hold_submit_key()
        new = self.wait_more(self.orders, before)
        self.assertEqual([("ACME", "BUY", 10, "LIMIT")], [(r[1], r[2], int(r[3]), r[4]) for r in new])
        self.assertEqual([("POST", "/api/orders")], self.order_posts(start))
        self.assertEqual([], self.browser.errors(self.sid, start))


class DeskSourceTests(unittest.TestCase):
    """Where the page rules live (a file read): the desks' own send buttons take a click of their own, the submit key's repeats never
    send, a booking holds the desk's button (no preview of the same deal after it, the 10 s refresh skipped while held), a priced package
    is sent once."""

    def test_the_own_desks_take_a_click_of_their_own_and_hold_after_a_booking(self):
        fx = _read(os.path.join(STATIC, "components", "fxdesk.js"))
        bond = _read(os.path.join(STATIC, "components", "bondticket.js"))
        # simplify round 10: each desk's button, its box's submit key and the desk's own key act through the shared guard (core.js
        # T.guard) as one control: a second click of a double click / a key repeat / a press while the deal is in flight never books; a
        # refusal that began elsewhere is said on the desk (FX_ALONE / BOND_ALONE: the guard's words) and held readable 20 s
        for src, btn, fn, key, words in ((fx, "confirm", "guardedDeal", "fx.confirm", "{ label: 'Confirm', not: 'not booked', act: 'book the deal',"),
                                         (bond, "submit", "guardedTrade", "bond.submit", "{ label: 'Submit', not: 'not traded', act: 'trade',")):
            self.assertIn(btn + ".addEventListener('click', function (e) { %s(e); });" % fn, src)
            guard = src[src.index("function %s(e) {" % fn):]
            guard = guard[:guard.index("\n  }\n")]
            self.assertIn("return T.guard('%s', e || null, doSubmit, %s" % (key, words), guard)
            self.assertIn("say: function (m) { if (me && me === st) { setStatus(m, 'err'); st.holdUntil = Date.now() + 20000; } } });", guard)
            self.assertEqual(2, src.count("{ e.preventDefault(); e.stopPropagation(); %s(e); }" % fn), "the box's submit key and the desk's own")
            self.assertNotIn("doSubmit(); }", src.replace("function doSubmit() {", ""))       # nothing books but through the guard
            self.assertNotIn("invalidate(); runPreview(true);", src)                     # no preview of the same deal after a booking
        self.assertTrue(FX_ALONE.startswith("not booked: Confirm takes a click of its own") and FX_ALONE.endswith("click Confirm once to book the deal"))
        self.assertTrue(BOND_ALONE.startswith("not traded: Submit takes a click of its own") and BOND_ALONE.endswith("click Submit once to trade"))
        self.assertIn("if (!document.hidden && st && !st.booked && document.body.contains(root)", fx)
        self.assertIn("if (!st || seq !== st.seq || st.booked) { return; }", fx)
        self.assertIn("if (st.booked) { setStatus('not booked again: '", fx)
        self.assertIn("st.instrument_id && !st.filled && document.body.contains(root)", bond)
        self.assertIn("if (!st || seq !== st.seq || st.filled) { return; }", bond)
        self.assertIn("if (st.filled) { setStatus('not traded again: '", bond)
        for src in (fx, bond):
            self.assertIn("function invalidate() { st.preview = null; release();", src)
            self.assertIn("function previewNow() { release(); runPreview(); }", src)

    def test_submit_package_takes_a_click_of_its_own_and_a_priced_package_is_sent_once(self):
        hedge = _read(os.path.join(STATIC, "components", "hedge.js"))
        ticket = _read(os.path.join(STATIC, "components", "ticket.js"))
        self.assertIn("btn.addEventListener('click', function (e) {", hedge)
        self.assertIn("if (!opts.onSubmitPackage || spec.$sent) { return; }", hedge)
        self.assertIn("spec, e) === true) {", hedge)
        self.assertIn("disabled: !canSubmit || !!spec.$sent", hedge)
        fn = ticket[ticket.index("function submitPackage(body, spec, click) {"):]
        fn = fn[:fn.index("\n  }\n")]
        # simplify round 10: Submit package goes through the shared guard (core.js T.guard; PKG_ALONE: the guard's words)
        self.assertIn("T.guard('ticket.package', click || null, function () {", fn)
        self.assertIn("var call = send(body, rid, 'package');", fn)
        self.assertIn("}, PACKAGE_GUARD);", fn)
        self.assertIn("return sent;", fn)
        self.assertIn("var PACKAGE_GUARD = { label: 'Submit package', say: function (m) { paintStatus(m, 'err'); } };", ticket)
        self.assertTrue(PKG_ALONE.startswith("not sent: Submit package takes a click of its own"))
        # the order ticket's submit key: a repeat never sends (the ticket's own keys and the page-wide one: the guard refuses ev.repeat)
        self.assertIn("if (K.is(e, 'ticket.submit')) { e.preventDefault(); e.stopPropagation(); guardedSubmit(e); return; }", ticket)
        self.assertIn("if (T.keys.is(e, 'ticket.submit') && !ui.root.contains(e.target)) { e.preventDefault(); guardedSubmit(e); }", ticket)


if __name__ == "__main__":
    unittest.main()
