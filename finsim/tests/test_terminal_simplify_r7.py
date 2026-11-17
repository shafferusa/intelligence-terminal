"""FinSim 1.0.7.2 simplify review round 7 (on 49e6598): the regression tests of its findings, kept in the terminal suite.

Autofill (R7-A1).  An adjusted option series (a root with a digit, such as ACME1) could never be filled, yet the Smart Ticket offered it:
the ROOT_CHOICE alternative "ACME1261218P00200000 (root ACME1)" re-read as SYMBOL_UNKNOWN, and its OCC symbol typed whole failed even
when the contract is in the instrument master with ACME as its underlying.  The normal order route lists a contract only when its root
names its underlying (``products.options.underlying_of``) or the contract is in the master, so an adjusted series outside the master is
now named and refused (NOT_TRADEABLE_HERE: it cannot be filled here), never offered; one in the master reads on its own underlying; and
the choice guard never offers an alternative whose order re-reads with no instrument:
  AdjustedRootTests (the corpus world of tests/test_terminal_smart_ticket.py, no browser)
    test_a  the root choice offers only the contract the order ticket can list, and says why the other is not offered; the standard
            root's alternative reads that contract (tests/test_reviews7_autofill_adjusted_root.py test_a, test_e);
    test_b  the adjusted contract typed whole (alone, or in parentheses beside a standard one) is NOT_TRADEABLE_HERE with the reason,
            no "Look it up" of a root that names no instrument; the order route refuses it too (test_b, test_f);
    test_c  the choice guard: were the adjusted contract offered, its re-read (no instrument) would never be kept;
    test_d  the controls: the weekly index root SPXW and an unknown root (test_d).
  AdjustedRootInTheMasterTests (the same world, the ACME1 put in the master with ACME as its underlying)
    test_a  typed whole, it reads on ACME with its request; the root choice offers it and its alternative reads it (test_c).
Autofill (R7-A2).  The group's "Strategies tab" ("for one all-or-none order, use the Strategies tab") was offered by the strategy's NAME
alone: a call butterfly sold and a calendar whose near expiry is bought have no template there.  It is now offered only for a group
with the shape of one of the tab's option templates:
  StrategiesTabDirectionTests (the corpus world)  every direction of spreads, straddles, strangles, iron condors and iron butterflies,
            the call butterfly bought and the calendars bought (their near expiry sold: offered), the call butterfly sold and the calendars sold (not
            offered, nor said) - tests/test_reviews7_autofill_strategies_tab.py.
The property tester (R7-P1).  "Dec 18, 2026" was cut at its comma when the word after the year was typed in capitals (GTC, IN, AT ...):
the year read as a quantity and the capitalised grammar word as a ticker, so the first card lost its year and the words after it:
  CommaDateTests (tests/test_props7_parse_forms.py's world, rebuilt here: P / F / I, F1:US_EXCHANGE 300 ACME and 2 ACME Dec 18 2026 100
  calls)
    test_a  the four reduced cases read as one order with the terms typed, as the same words in lower case (the control) do; the unified
            app's relay rule offers no close with a GTD date never typed (tests/test_reviews7_property_tester_comma_date.py);
    test_b  "good till Dec 18, 50 WIDG" is still two orders; "Dec 18, 2026 WIDG" is a red flag, never a card of 2026 WIDG;
    test_seed_7770101_cases  the parse oracle at PROPS7_PF_SEED=7770101: its findings, cases 35, 47, 553, 638, 713, 1531, 1639, 1702
            and 2214, each read as the oracle's orders;
    test_seed_7770101_rewritten  its rewritten cases 111, 453, 714, 957, 1947 and 2214: both writings read the same cards.
The page (R7-P2 and R7-P3; the real offline server on port 0 with the three books, the fictional ACME / WIDG and the smoke test's
invented ACME chain; the real pages in a headless Edge / Chrome):
  PageTests
    test_a  "... call spread as one order (fund)" ("fund" not read): the group's "Strategies tab" is held with the reason, as its cards
            are, and its click (made pressable by force) opens nothing (tests/test_reviews7_page_strategies_tab.py test_a);
    test_b  the same reading, its ONE_ORDER_ASKED flag box: the action "Strategies tab" disabled with the reason beside it (test_b);
    test_c  "... in my IRA" (no such account: not read): held (test_c);
    test_d  the controls: "... as one order in the Fund book" opens the Fund book's ticket on its Strategies tab, a clean spread the
            Personal book's (the group's own ONE_ORDER_ASKED never holds the tab) (test_d, test_e);
    test_e  a symbol typed in the order ticket's box and not resolved (the X2 finding of tests/test_props7_no_send.py, PROPS7_NS_SEED=
            7770201 action 1332, reduced in tests/test_reviews7_property_tester_typed_symbol.py): Submit is disabled with the reason, a
            click and Ctrl+Enter send nothing (said), typing the symbol back gives the previewed order back (test_a, test_b);
    test_f  the control: the symbol resolved with Enter is the order sent (test_c).
  TicketSourceTests  components/ticket.js: every send is held while the box names another symbol than the order filled in, and a mode
            entered on the instrument filled in (amend / replace, hedge this position) names it in the box.
Fictional ACME / WIDG only; temp homes; no ~/.finsim, no network, no port 8765 / 8865; nothing is sent but where a test says so.
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import tempfile
import time
import unittest
from decimal import Decimal
from unittest import mock
from urllib.parse import urlsplit

from tests import terminal_smart_corpus as corpus
from tests import test_terminal_browser_smoke as SMK
from tests import test_terminal_simplify_r6 as R6
from tests import test_terminal_smart_submit as SUB
from tests import test_terminal_smart_ticket as STT
from tests.terminal_cdp import NO_BROWSER, Browser, find_browser
from tests.terminal_helpers import _Log
from finsim.terminal import config, server as _server, smart_ticket as st
from finsim.terminal.clock import FakeClock
from finsim.terminal.commands import RejectError
from finsim.terminal.products import options as _options
from finsim.terminal.quotes.providers import FakeProvider

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "finsim", "terminal", "static")
P = corpus.P
ADJ = "ACME1261218P00200000"          # the adjusted root's Dec 18 2026 200 put (listed by the corpus world's ACME chain)
STD = "ACME261218P00200000"           # the standard root's


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _flag(leg, code):
    return next((f for f in leg["flags"] if f["code"] == code), None)


def _blockers(leg) -> list:
    return sorted(f["code"] for f in leg["flags"] if f["severity"] == "blocker")


def _apply(text: str, f: dict, ch: dict) -> str:
    """The page's ``choose``: the choice's clause in place of the flag's span."""
    sp = f["span"]
    return text[:sp[0]] + ch["clause"] + text[sp[1]:]


# ==== the property tester: the comma of a date =========================================================================================
#: tests/test_props7_parse_forms.py at PROPS7_PF_SEED=7770101 on 49e6598: (case, text, then per order typed the oracle's (syntactic
#: blockers, blockers of the holding, the order body when there is none)) - every finding of the run
SEED_7770101 = [
    (35, 'IN THE INSTITUTIONAL BOOK: SELL 50 WIDG LMT 1,099.50 GOOD UNTIL DEC 18 2026, THEN STO 5x ACME DEC 18 2026 CALLS 115 GOOD TILL JAN 15, 2027 PLUS BUY 200x ACME GTD 12/18/2026 TRAIL 2%, AND SELL TO OPEN 2 ACME DECEMBER 18 2026 115 CALL UNTIL MAR 19 @ THE ASK',
     ((), ('WOULD_OVERSELL',), {'account_id': 'I1:US_EXCHANGE', 'instrument_id': 'WIDG', 'side': 'SELL', 'qty': 50, 'order_type': 'LIMIT', 'tif': 'GTD', 'limit_px': '1099.50', 'gtd_date': '2026-12-18'}),
     ((), (), {'account_id': 'I1:US_EXCHANGE', 'instrument_id': 'ACME261218C00115000', 'side': 'STO', 'qty': 5, 'order_type': 'MARKET', 'tif': 'GTD', 'gtd_date': '2027-01-15'}),
     ((), (), {'account_id': 'I1:US_EXCHANGE', 'instrument_id': 'ACME', 'side': 'BUY', 'qty': 200, 'order_type': 'TRAILING_STOP', 'tif': 'GTD', 'trail_pct': '2', 'gtd_date': '2026-12-18'}),
     ((), (), {'account_id': 'I1:US_EXCHANGE', 'instrument_id': 'ACME261218C00115000', 'side': 'STO', 'qty': 2, 'order_type': 'LIMIT', 'tif': 'GTD', 'limit_px': '0.60', 'gtd_date': '2027-03-19'}),
    ),
    (47, 'in The fund book:  Buy 1,000 \xd7 Widg trail 2% Good Till january 15, 2027 AT 4.10 IN the FUND book\nbuy 300 ACME Market Good till CANCELED, Then Sto 5 ACME261218P00095000 AT us Exchange trail 5%',
     (('DUPLICATE_FIELD',), (), {'account_id': 'F1:US_EXCHANGE', 'instrument_id': 'WIDG', 'side': 'BUY', 'qty': 1000, 'order_type': 'TRAILING_STOP', 'tif': 'GTD', 'trail_pct': '2', 'gtd_date': '2027-01-15'}),
     ((), (), {'account_id': 'F1:US_EXCHANGE', 'instrument_id': 'ACME', 'side': 'BUY', 'qty': 300, 'order_type': 'MARKET', 'tif': 'GTC'}),
     ((), (), {'account_id': 'F1:US_EXCHANGE', 'instrument_id': 'ACME261218P00095000', 'side': 'STO', 'qty': 5, 'order_type': 'TRAILING_STOP', 'tif': 'DAY', 'trail_pct': '5'}),
    ),
    (553, 'Bto\xa03x\u202fACME 100P dec 18 2026 At Us Exchange GOOD\xa0TILL\u2007march 19, 2027 IN the PERSONAL Book.\nSell 10 X Acme\xa0100 CALL dec 18 2026 Good UNTIL CANCELLED\u2007Trail 2%',
     ((), (), {'account_id': 'P1:US_EXCHANGE', 'instrument_id': 'ACME261218P00100000', 'side': 'BTO', 'qty': 3, 'order_type': 'MARKET', 'tif': 'GTD', 'gtd_date': '2027-03-19'}),
     ((), (), {'account_id': 'P1:US_EXCHANGE', 'instrument_id': 'ACME261218C00100000', 'side': 'STO', 'qty': 10, 'order_type': 'TRAILING_STOP', 'tif': 'GTC', 'trail_pct': '2'}),
    ),
    (638, 'Short 2.5k ACME GOOD till November 27, 2026 AT US EXCHANGE 100.25 limit',
     ((), (), {'account_id': 'P1:US_EXCHANGE', 'instrument_id': 'ACME', 'side': 'SELL_SHORT', 'qty': 2500, 'order_type': 'LIMIT', 'tif': 'GTD', 'limit_px': '100.25', 'gtd_date': '2026-11-27', 'borrow': True}),
    ),
    (713, 'SELL 1 X ACME DEC 18, 2026 100 PUTS GOOD TILL DEC 18, 2026 AT US EXCHANGE; SELL 12.5k ACME AT $1,099.50 UNTIL DECEMBER 18',
     ((), (), {'account_id': 'P1:US_EXCHANGE', 'instrument_id': 'ACME261218P00100000', 'side': 'STO', 'qty': 1, 'order_type': 'MARKET', 'tif': 'GTD', 'gtd_date': '2026-12-18'}),
     ((), ('WOULD_OVERSELL',), {'account_id': 'P1:US_EXCHANGE', 'instrument_id': 'ACME', 'side': 'SELL', 'qty': 12500, 'order_type': 'LIMIT', 'tif': 'GTD', 'limit_px': '1099.50', 'gtd_date': '2026-12-18'}),
    ),
    (1531, 'STO 2x ACME DECEMBER 18 2026 115 CALL 5 LIMIT AT US EXCHANGE IN THE PERSONAL BOOK\nSELL 10\xd7 ACME GOOD TILL DEC 18, 2026 LIMIT 99.5\nSELL 1 ACME IN THE FUND BOOK GOOD UNTIL DECEMBER 18 2026 MARKET',
     ((), (), {'account_id': 'P1:US_EXCHANGE', 'instrument_id': 'ACME261218C00115000', 'side': 'STO', 'qty': 2, 'order_type': 'LIMIT', 'tif': 'DAY', 'limit_px': '5'}),
     ((), (), {'account_id': 'F1:US_EXCHANGE', 'instrument_id': 'ACME', 'side': 'SELL', 'qty': 10, 'order_type': 'LIMIT', 'tif': 'GTD', 'limit_px': '99.5', 'gtd_date': '2026-12-18'}),
     ((), (), {'account_id': 'F1:US_EXCHANGE', 'instrument_id': 'ACME', 'side': 'SELL', 'qty': 1, 'order_type': 'MARKET', 'tif': 'GTD', 'gtd_date': '2026-12-18'}),
    ),
    (1639, 'SHORT 250 X ACME AT US EXCHANGE IN THE INSTITUTIONAL BOOK.\nBUY 1k ACME GOOD TILL NOV 27, 2026 STOP 105 LIMIT 105.25 THEN 1.5k WIDG DAY',
     ((), (), {'account_id': 'I1:US_EXCHANGE', 'instrument_id': 'ACME', 'side': 'SELL_SHORT', 'qty': 250, 'order_type': 'MARKET', 'tif': 'DAY', 'borrow': True}),
     ((), (), {'account_id': 'P1:US_EXCHANGE', 'instrument_id': 'ACME', 'side': 'BUY', 'qty': 1000, 'order_type': 'STOP_LIMIT', 'tif': 'GTD', 'stop_px': '105', 'limit_px': '105.25', 'gtd_date': '2026-11-27'}),
     ((), (), {'account_id': 'P1:US_EXCHANGE', 'instrument_id': 'WIDG', 'side': 'BUY', 'qty': 1500, 'order_type': 'MARKET', 'tif': 'DAY'}),
    ),
    (1702, 'BUY TO OPEN 2 ACME DEC 18, 2026 95 CALL @ 4.9 GOOD TILL DECEMBER 18, 2026 AT I1:US_EXCHANGE IN THE INSTITUTIONAL book;sell 1.000 ACME261218C00120000 AT US EXCHANGE TRAIL 2 NEXT SESSION. BUY TO OPEN 1 \xd7 ACME 2026-12-18 P 95 TRAIL 0.5',
     ((), (), {'account_id': 'I1:US_EXCHANGE', 'instrument_id': 'ACME261218C00095000', 'side': 'BTO', 'qty': 2, 'order_type': 'LIMIT', 'tif': 'GTD', 'limit_px': '4.9', 'gtd_date': '2026-12-18'}),
     (('TOKEN_AMBIGUOUS',), (), {'account_id': 'P1:US_EXCHANGE', 'instrument_id': 'ACME261218C00120000', 'side': 'STO', 'qty': 1, 'order_type': 'TRAILING_STOP', 'tif': 'NEXT_SESSION', 'trail_amt': '2'}),
     ((), (), {'account_id': 'P1:US_EXCHANGE', 'instrument_id': 'ACME261218P00095000', 'side': 'BTO', 'qty': 1, 'order_type': 'TRAILING_STOP', 'tif': 'DAY', 'trail_amt': '0.5'}),
    ),
    (2214, 'SELL TO OPEN 3 \xd7 ACME 2027-01-15 100 C GOOD TILL DEC 18, 2026 STOP 6.5 AT P1:US_EXCHANGE, THEN BUY 5 ACME DEC 18,2026 90 PUT AT I1:US_EXCHANGE IN THE INSTITUTIONAL BOOK STOP 6.5, BUY 500 ACME TRAIL 2% AT P1:US_EXCHANGE AT P1:US_EXCHANGE IN THE PERSONAL BOOK THEN BUY 5 CONTRACTS ACME DEC 18, 2026 120 CALLS IN THE PERSONAL BOOK GOOD TIL CANCELED AT 4.90',
     ((), (), {'account_id': 'P1:US_EXCHANGE', 'instrument_id': 'ACME270115C00100000', 'side': 'STO', 'qty': 3, 'order_type': 'STOP', 'tif': 'GTD', 'stop_px': '6.5', 'gtd_date': '2026-12-18'}),
     ((), (), {'account_id': 'I1:US_EXCHANGE', 'instrument_id': 'ACME261218P00090000', 'side': 'BTO', 'qty': 5, 'order_type': 'STOP', 'tif': 'DAY', 'stop_px': '6.5'}),
     (('DUPLICATE_FIELD',), (), {'account_id': 'P1:US_EXCHANGE', 'instrument_id': 'ACME', 'side': 'BUY', 'qty': 500, 'order_type': 'TRAILING_STOP', 'tif': 'DAY', 'trail_pct': '2'}),
     ((), (), {'account_id': 'P1:US_EXCHANGE', 'instrument_id': 'ACME261218C00120000', 'side': 'BTO', 'qty': 5, 'order_type': 'LIMIT', 'tif': 'GTC', 'limit_px': '4.90'}),
    ),
]
SEED_7770101_M = [
    (111, 'BTO 3 X ACME DEC 18 2026 110 CALLS UNTIL JANUARY 15 2027 AT P1:US_EXCHANGE',
     'BTO 3 \xd7\u202fAcme DECEMBER 18,2026 110 Calls Good TILL january\xa015,\xa02027\u202fAT\u202fP1:US_EXCHANGE'),
    (453, 'Buy 2,500 Acme Shares @ 99.50;\nBuy To Open 10 Contracts Acme 105 Call January 15 2027 In The Personal Book Gtd 03/19/2027 Then Buy 300 Widg Stop 95.50 In The Fund Book At Us Exchange',
     'BUY 2.5k ACME AT 99.50 AND BUY TO OPEN 10 CONTRACTS ACME270115C00105000 GOOD TILL MAR 19, 2027 IN THE PERSONAL BOOK\nBUY 300 WIDG AT US EXCHANGE STOP 95.50 IN THE FUND BOOK'),
    (714, 'Bto 1.5 Acme 2026-12-18 100\xa0C In The\xa0Fund Book Until March 19 2027 Trail 0.5 At\u2007Us\u202fExchange Plus Bto\xa03 Acme 2026-12-18 115 C\u2007Stop 6.5 & Bto 5\u202fAcme January 15 100 Calls\u2007In The\u2007Institutional Book Gtd\u202f11/27/2026',
     'BTO 0.5 ACME 100C 12/18/2026 TRAIL 0.5 GOOD TILL MARCH 19, 2027 AT US EXCHANGE IN THE FUND BOOK ;  BTO 3 ACME DEC 18, 2026 115 CALLS STOP 6.5 ;  BTO 5 ACME JANUARY 15, 2027 100 CALLS GTD NOV 27 2026 IN THE INSTITUTIONAL BOOK'),
    (957, 'in the personal book:\nbto 2x acme 100c 01/15/2027 @ 0.55 at us exchange, and buy 1 acme 90p 12/18/26 stop 3 gtd nov 27 2026 at us exchange. sell 100 \xd7 acme lmt 1,099.50\nsell short 1.5 widg @ 99.50 at P1:US_EXCHANGE',
     'In the personal Book:  Bto 2 X Acme JAN 15 2027 CALLS 100 At US EXCHANGE 0.55 limit. buy 1 acme 2026\u201312\u201318 p 90 GOOD TILL nov 27, 2026 STOP 3 AT us EXCHANGE Plus SELL 100\xd7 ACME At 1,099.50 Plus Sell Short 1.5 Widg at 99.50 At P1:US_EXCHANGE'),
    (1947, 'in the Institutional book: short 300 WIDG good till canceled in the Fund book & buy 5 ACME at us exchange good till Mar 19 2027 in the Institutional book; sell to open 10 ACME December 18, 2026 100 calls gtd 2027-01-15 @ 1.05 plus sto 1 ACME 95P Dec 18 2026 good till Nov 27 trail 5%',
     'IN THE INSTITUTIONAL BOOK: SHORT 300 X WIDG IN THE FUND BOOK GOOD TILL CANCELLED PLUS BUY 5 ACME GOOD TILL MAR 19, 2027 IN THE INSTITUTIONAL BOOK AT US EXCHANGE, SELL TO OPEN 10 X ACME261218C00100000 1.05 LIMIT GOOD TILL JANUARY 15 2027, THEN STO 1x ACME261218P00095000 GTD NOVEMBER 27 TRAIL 5%'),
    (2214, 'SELL TO OPEN 3 \xd7 ACME 2027-01-15 100 C GOOD TILL DEC 18, 2026 STOP 6.5 AT P1:US_EXCHANGE, THEN BUY 5 ACME DEC 18,2026 90 PUT AT I1:US_EXCHANGE IN THE INSTITUTIONAL BOOK STOP 6.5, BUY 500 ACME TRAIL 2% AT P1:US_EXCHANGE AT P1:US_EXCHANGE IN THE PERSONAL BOOK THEN BUY 5 CONTRACTS ACME DEC 18, 2026 120 CALLS IN THE PERSONAL BOOK GOOD TIL CANCELED AT 4.90',
     'sell to open 3 ACME Jan 15 2027 calls 100 at P1:US_EXCHANGE good until Dec 18 2026 stop 6.5, buy 5x ACME December 18,2026 90 put stop 6.5 in the Institutional book at I1:US_EXCHANGE & buy 500x ACME at P1:US_EXCHANGE at P1:US_EXCHANGE trail 2% in the Personal book, then buy 5 ACME Dec 18 120 call at 4.90 in the Personal book good till canceled'),
]


PX_KEYS = ("limit_px", "stop_px", "trail_amt", "trail_pct")


def _same_req(got, want) -> bool:
    """The order body the oracle gives (prices compared as numbers: "0.60" is 0.60)."""
    if got is None or set(got) != set(want):
        return False
    for k, v in want.items():
        if k in PX_KEYS:
            try:
                if Decimal(str(got[k])) != Decimal(str(v)):
                    return False
            except Exception:   # noqa: BLE001 - not a number: not the price typed
                return False
        elif got[k] != v or type(got[k]) is not type(v):
            return False
    return True


def _key(out: dict) -> list:
    """What must not depend on how the same orders are written (tests/test_props7_parse_forms.py ``key``)."""
    k = []
    for l in out["legs"]:
        k.append((l["book"], (l["instrument"] or {}).get("id"), tuple(_blockers(l)), l["request"] is None,
                  json.dumps(l["request"], sort_keys=True, default=str) if l["request"] is not None and "limit_px" not in l["request"] else None))
    return k


def _comma_world():
    """tests/test_props7_parse_forms.py's world, rebuilt here (that property module is not shipped): one fresh offline runtime with the
    books P / F / I, Personal selected, FakeClock Tue 2026-10-20 14:00 ET; ACME printed at 100.00 with tests/test_terminal_smart_submit's
    invented chain, WIDG never held; F1:US_EXCHANGE holds 300 ACME and 2 ACME Dec 18 2026 100 calls, bought through the normal order
    route and the matcher."""
    tmp = tempfile.mkdtemp(prefix="fst-simplify-r7-comma-")
    os.environ[config.HOME_ENV] = tmp
    os.environ[config.DB_ENV] = os.path.join(tmp, "terminal.db")
    clock = FakeClock(SUB.AT)
    rt = _server.build_runtime(tmp, os.path.join(tmp, "terminal.db"), clock=clock, offline=True, fake_provider=FakeProvider(clock), log=_Log(),
                               books=list(config.BOOKS), fetch_threads=0)
    try:
        SUB.add_name(rt, "ACME")
        SUB.add_name(rt, "WIDG")
        now = rt.clock.epoch()
        SUB.print_acme(rt, "100.00", 120, now - 180, 50000)
        snap = SUB.acme_chain(now, size=50)
        SUB.load_chain(rt, snap)
        for e, r, k, _b, _a in SUB.ROWS:
            _options.ensure_option(rt, SUB.acme(r, k, e), chain=snap)
        SUB.load_chain(rt, snap)
        held = {"ACME": 300, SUB.acme("C", 100): 2}
        for i, (iid, side, qty) in enumerate((("ACME", "BUY", 300), (SUB.acme("C", 100), "BTO", 2))):
            body = {"account_id": "F1:US_EXCHANGE", "instrument_id": iid, "side": side, "qty": qty, "order_type": "MARKET", "tif": "DAY", "confirm_large": True,
                    "request_id": f"ui:simplify-r7-{i:03d}"}
            code, out, _h = rt.router.handle("POST", "/api/orders?book=F", None, body, "127.0.0.1", None)
            assert code == 200, (body, out)
            rt.matcher.on_quotes(["ACME"] + [SUB.acme(r, k, e) for e, r, k, _b, _a in SUB.ROWS])
            rt.matcher.sweep()
        with rt.db.reader() as conn:
            got = {(r["account_id"], r["instrument_id"]): int(r["qty"]) for r in conn.execute("SELECT account_id, instrument_id, qty FROM v_positions").fetchall()
                   if int(r["qty"])}
        assert got == {("F1:US_EXCHANGE", i): q for i, q in held.items()}, got
    except Exception:
        rt.close()
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return tmp, rt


# ==== autofill: adjusted roots =========================================================================================================
class _CorpusWorld(unittest.TestCase):
    """tests/test_terminal_smart_ticket.py's corpus world (fictional ACME / WIDG / GIZM / SPX; its ACME chain lists the Dec 18 2026 200
    put on the roots ACME and ACME1); pure readings (``smart_ticket.parse_view``)."""
    WORLD = corpus.WORLD

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.rt = STT._ResolverBase._make()
        STT.build_world(unittest.TestCase(), cls.rt, world=cls.WORLD)

    @classmethod
    def tearDownClass(cls):
        cls.rt.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def parse(self, text: str, book: str = "P") -> dict:
        return st.parse_view(self.rt, {"text": text, "book": book})

    def order_of(self, text: str) -> tuple:
        leg = self.parse(text)["legs"][0]
        return leg["account_id"], leg["side"], leg["qty"], (leg["request"] or {}).get("instrument_id"), _blockers(leg)


UNLISTED = "the order ticket lists a contract only when its root names its underlying (ACME) or the contract is in the instrument master: it cannot be filled here"


class AdjustedRootTests(_CorpusWorld):
    def test_a_the_root_choice_offers_only_a_contract_the_order_ticket_lists(self):
        text = "buy 1 ACME Dec 18 2026 200 put"
        leg = self.parse(text)["legs"][0]
        f = _flag(leg, "ROOT_CHOICE")
        self.assertEqual(("blocker", None), (f["severity"], leg["request"]))
        self.assertEqual([f"{STD} (root ACME, the standard root)"], [c["label"] for c in f["choices"]])
        self.assertIn(f"(roots ACME, ACME1): pick one; {ADJ} (root ACME1) is not offered: " + UNLISTED, f["text"])
        new = _apply(text, f, f["choices"][0])
        self.assertEqual("buy 1 " + STD, new)
        self.assertEqual((P, "BTO", 1, STD, []), self.order_of(new))
        # a strategy leg on the two roots: the same words, no choice there (typed as OCC symbols in parentheses)
        sp = _flag(self.parse("buy 1 ACME Dec 18 2026 200/210 put spread")["legs"][0], "ROOT_CHOICE")
        self.assertEqual(("blocker", []), (sp["severity"], sp.get("choices") or []))
        self.assertIn("is not offered: " + UNLISTED, sp["text"])

    def test_b_the_adjusted_contract_typed_whole_is_refused_with_the_reason(self):
        for text in ("buy 1 " + ADJ, "(sell 1 " + ADJ + " and buy 1 ACME261218P00210000)"):
            with self.subTest(text=text):
                leg = self.parse(text)["legs"][0]
                f = _flag(leg, "NOT_TRADEABLE_HERE")
                self.assertIsNotNone(f, leg["flags"])
                self.assertEqual(("blocker", None, None, None, None), (f["severity"], f.get("action"), f.get("choices"), leg["request"], _flag(leg, "SYMBOL_UNKNOWN")))
                self.assertEqual(f"{ADJ} has the root ACME1, an adjusted ACME series listed on the ACME chain: " + UNLISTED, f["text"])
        # why: the order route (look-up, preview, submit) reads a root's underlying as products.options.underlying_of does - ACME1 names none
        self.assertIsNone(_options.underlying_of(self.rt, ADJ))
        self.assertEqual("ACME", _options.underlying_of(self.rt, STD).instrument_id)
        with self.assertRaises(RejectError) as cm:
            _options.ensure_option(self.rt, ADJ)
        self.assertEqual("INSTRUMENT_UNKNOWN", cm.exception.code)
        # a root with a digit on an underlying whose chain is not loaded: refused the same way, no chain named
        f = _flag(self.parse("buy 1 WIDG1261218C00040000")["legs"][0], "NOT_TRADEABLE_HERE")
        self.assertEqual("WIDG1261218C00040000 has the root WIDG1, an adjusted WIDG series: " + UNLISTED.replace("(ACME)", "(WIDG)"), f["text"])

    def test_c_the_choice_guard_never_keeps_an_alternative_that_reads_no_instrument(self):
        text = "buy 1 ACME Dec 18 2026 200 put"
        anyroot = mock.patch.object(st.Resolver, "_fillable", lambda self, occ, under: True)       # as 49e6598 offered both roots
        with anyroot, mock.patch.object(st.Resolver, "_keep_others", lambda self, *a: None):
            raw = _flag(self.parse(text)["legs"][0], "ROOT_CHOICE")
        self.assertEqual(["buy 1 " + STD, "buy 1 " + ADJ], [c["clause"] for c in raw["choices"]])
        with anyroot:
            kept = _flag(self.parse(text)["legs"][0], "ROOT_CHOICE")
        self.assertEqual(["buy 1 " + STD], [c["clause"] for c in kept["choices"]])     # "buy 1 ACME1..." re-reads with no instrument

    def test_d_controls_the_weekly_index_root_and_an_unknown_root(self):
        text = "buy 1 SPX Dec 18 2026 6000 call"
        f = _flag(self.parse(text)["legs"][0], "ROOT_CHOICE")
        self.assertEqual(["buy 1 SPX261218C06000000", "buy 1 SPXW261218C06000000"], [c["clause"] for c in f["choices"]])
        self.assertNotIn("not offered", f["text"])
        _acct, side, qty, iid, blk = self.order_of(_apply(text, f, f["choices"][1]))
        self.assertEqual(("SPXW261218C06000000", "BTO", 1, []), (iid, side, qty, blk))
        leg = self.parse("buy 1 ZZQQ1261218C00040000")["legs"][0]
        self.assertEqual((None, None), (leg["request"], _flag(leg, "NOT_TRADEABLE_HERE")))
        self.assertIsNotNone(_flag(leg, "SYMBOL_UNKNOWN"))


def _with_adjusted_in_master() -> dict:
    w = copy.deepcopy(corpus.WORLD)
    w["instruments"].append(corpus._opt("ACME", ADJ))        # as InstrumentMaster.ensure_option writes it: underlying ACME, root ACME1
    return w


class AdjustedRootInTheMasterTests(_CorpusWorld):
    WORLD = _with_adjusted_in_master()

    def test_a_an_adjusted_contract_in_the_master_reads_on_its_underlying(self):
        row = self.rt.instruments.get(ADJ)
        self.assertEqual(("OPTION", "ACME"), (row.kind, row.underlying_id))
        self.assertEqual((P, "BTO", 1, ADJ, []), self.order_of("buy 1 " + ADJ))
        self.assertEqual("ACME 2026-12-18 200 put (root ACME1)", self.parse("buy 1 " + ADJ)["legs"][0]["instrument"]["words"])
        self.assertEqual(ADJ, _options.ensure_option(self.rt, ADJ).instrument_id)               # the order route lists it (in the master)
        text = "buy 1 ACME Dec 18 2026 200 put"
        f = _flag(self.parse(text)["legs"][0], "ROOT_CHOICE")
        self.assertEqual(["buy 1 " + STD, "buy 1 " + ADJ], [c["clause"] for c in f["choices"]])
        self.assertNotIn("not offered", f["text"])
        self.assertEqual((P, "BTO", 1, ADJ, []), self.order_of(_apply(text, f, f["choices"][1])))


# ==== autofill: the Strategies tab, in the direction read ==============================================================================
#: (text, the Strategies tab has a template of that direction) - the Personal book, one account
TAB_CASES = [("buy 1 ACME Dec 18 2026 240/250 call spread", True), ("sell 1 ACME Dec 18 2026 240/250 call spread", True),
             ("buy 1 ACME Dec 18 2026 220/230 put spread", True), ("sell 1 ACME Dec 18 2026 220/230 put spread", True),
             ("buy 1 ACME Dec 18 2026 230 straddle", True), ("sell 1 ACME Dec 18 2026 230 straddle", True),
             ("buy 1 ACME Dec 18 2026 220/240 strangle", True), ("sell 1 ACME Dec 18 2026 220/240 strangle", True),
             ("buy 1 ACME Dec 18 2026 210/220/240/250 iron condor", True), ("sell 1 ACME Dec 18 2026 210/220/240/250 iron condor", True),
             ("sell 1 ACME Dec 18 2026 220/230/240 iron butterfly", True), ("buy 1 ACME Dec 18 2026 220/230/240 iron butterfly", True),
             ("buy 1 ACME Dec 18 2026 220/230/240 call butterfly", True), ("buy 2 ACME Dec 18 2026 220/230/240 call butterfly", True),
             ("buy 1 ACME Dec 18/Jan 15 2027 240 call calendar", True), ("buy 1 ACME Dec 18/Jan 15 2027 240 put calendar", True),
             ("sell 1 ACME Dec 18 2026 220/230/240 call butterfly", False),
             ("sell 1 ACME Dec 18/Jan 15 2027 240 call calendar", False), ("sell 1 ACME Dec 18/Jan 15 2027 240 put calendar", False)]


class StrategiesTabDirectionTests(_CorpusWorld):
    def test_a_the_strategies_tab_is_offered_and_said_only_for_a_template_of_the_direction_read(self):
        bad = []
        for text, has in TAB_CASES:
            doc = self.parse(text)
            g = doc["combos"][0]
            tab = [f for f in g["flags"] if (f.get("action") or {}).get("kind") == "strategies_tab"]
            said = [f["code"] for f in g["flags"] if "Strategies tab" in f["text"]]
            if (bool(tab), bool(said), all(l["request"] for l in doc["legs"])) != (has, has, True):
                bad.append((text, bool(tab), said, [(l["side"], l["qty"], (l["instrument"] or {}).get("id"), _blockers(l)) for l in doc["legs"]]))
        self.assertEqual([], bad)

    def test_b_words_asking_for_one_order_follow_the_same_rule(self):
        for text, has in (("buy 1 ACME Dec 18 2026 220/230/240 call butterfly as one order", True),
                          ("sell 1 ACME Dec 18 2026 220/230/240 call butterfly as one order", False),
                          ("sell 1 ACME Dec 18/Jan 15 2027 240 put calendar as one order", False)):
            with self.subTest(text=text):
                g = self.parse(text)["combos"][0]
                f = next(x for x in g["flags"] if x["code"] == "ONE_ORDER_ASKED")
                self.assertEqual((has, has), ((f.get("action") or {}).get("kind") == "strategies_tab", "use the Strategies tab" in f["text"]), f)


# ==== the property tester: the comma of a date =========================================================================================
class CommaDateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = {k: os.environ.get(k) for k in (config.HOME_ENV, config.DB_ENV)}
        cls.tmp, cls.rt = _comma_world()

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
        return st.parse_view(self.rt, {"text": text, "book": "P"})

    def one_order(self, text: str, want: dict) -> dict:
        out = self.read(text)
        got = [(l["clause"], l["request"], _blockers(l)) for l in out["legs"]]
        self.assertEqual(([], []), (out["unparsed"], [f["code"] for f in out["flags"] if f["severity"] == "blocker"]), text)
        self.assertEqual(1, len(out["legs"]), f"{text!r}: one order typed, the cards are {got}")
        leg = out["legs"][0]
        self.assertIsNotNone(leg["request"], f"{text!r}: {got}")
        self.assertEqual(want, {k: leg["request"].get(k) for k in want}, text)
        self.assertNotIn("YEAR_INFERRED", [f["code"] for f in leg["flags"]], text)                 # the year typed is the year read
        return leg

    def test_a_a_date_written_with_its_comma_stays_whole_before_words_in_capitals(self):
        c100 = SUB.acme("C", 100)
        for text, want in (("BUY 1 ACME 100 CALL DEC 18, 2026 GTC", {"instrument_id": c100, "side": "BTO", "tif": "GTC"}),
                           ("BUY 100 ACME GOOD TILL JAN 15, 2028 IN THE FUND BOOK", {"account_id": "F1:US_EXCHANGE", "tif": "GTD", "gtd_date": "2028-01-15"}),
                           ("SELL 1 ACME 100 CALL DEC 18, 2026 IN THE FUND BOOK", {"account_id": "F1:US_EXCHANGE", "instrument_id": c100, "side": "STC"}),
                           ("SELL 100 ACME GOOD TILL JAN 15, 2028 IN THE FUND BOOK", {"account_id": "F1:US_EXCHANGE", "side": "SELL", "tif": "GTD",
                                                                                     "gtd_date": "2028-01-15"}),
                           ("BUY 1 ACME 100 CALL DEC 18, 26 GTC", {"instrument_id": c100, "side": "BTO", "tif": "GTC"})):
            with self.subTest(text=text):
                self.one_order(text, want)
                self.one_order(text.lower(), want)                                                # the control: the same words in lower case
        # the unified app's relay (adapters/finsim_trade.py ``_instruction_blocked`` / ``_leg_item``, read only) offers the close of the
        # 300 ACME held in F1 with the GTD date typed, never another
        out = self.read("SELL 100 ACME GOOD TILL JAN 15, 2028 IN THE FUND BOOK")
        legs = out["legs"]
        held_whole = bool([f for f in out["flags"] if f["severity"] == "blocker"] or out["unparsed"]
                          or (out["ok"] is not True and legs and not any(_blockers(l) for l in legs)))
        self.assertEqual([("SELL", 100, "GTD", "2028-01-15")], [(l["side"], l["qty"], l["tif"], l["gtd_date"]) for l in legs if not held_whole
                                                                 and l.get("combo") is None and not _blockers(l) and l.get("request") is not None])

    def test_b_a_comma_still_ends_an_order_where_one_follows_and_a_year_never_becomes_a_quantity(self):
        out = self.read("buy 100 ACME good till Dec 18, 50 WIDG")
        self.assertEqual([("BUY", 100, "ACME", "GTD", "2026-12-18"), ("BUY", 50, "WIDG", "DAY", None)],
                         [(l["side"], l["qty"], (l["instrument"] or {}).get("id"), l["tif"], l["gtd_date"]) for l in out["legs"]])
        for text in ("buy 1 ACME Dec 18, 2026 WIDG", "BUY 1 ACME DEC 18, 2026 WIDG"):
            with self.subTest(text=text):
                out = self.read(text)
                self.assertEqual(1, len(out["legs"]), [(l["clause"], l["qty"]) for l in out["legs"]])     # never a card of 2026 WIDG
                self.assertIn("UNPARSED_TEXT", _blockers(out["legs"][0]))
        for text in ("BUY 1 ACME 100 CALL 18 DEC, 2026 GTC", "BUY 1 ACME 100 CALL DEC 18, 2026 AND BUY 100 WIDG"):
            with self.subTest(text=text):
                self.assertEqual(_key(self.read(text.lower())), _key(self.read(text)))       # capitals read as lower case
        self.assertEqual(1, len(st.parse("buy 1 ACME 240 call Dec 18, 2026 GTC")["clauses"]))
        self.assertEqual(2, len(st.parse("buy 1 ACME 240 call Dec 18, 2026 GTC, 50 WIDG")["clauses"]))

    def test_seed_7770101_cases(self):
        bad = []
        for case, text, *orders in SEED_7770101:
            out = self.read(text)
            legs = out["legs"]
            top = [f["code"] for f in out["flags"] if f["severity"] == "blocker"]
            if out["unparsed"] or top or len(legs) != len(orders):
                bad.append((case, out["unparsed"], top, [(l["clause"], _blockers(l)) for l in legs]))
                continue
            for i, ((syn, held, req), l) in enumerate(zip(orders, legs)):
                blk = set(_blockers(l))
                if syn:
                    ok = set(syn) <= blk <= set(syn) | set(held) and l["request"] is None
                elif held:
                    ok = blk == set(held) and l["request"] is None
                else:
                    ok = not blk and _same_req(l["request"], req)
                if not ok:
                    bad.append((case, i + 1, l["clause"], sorted(blk), l["request"], (syn, held, req)))
        self.assertEqual([], bad)

    def test_seed_7770101_rewritten(self):
        self.assertEqual([], [(case, _key(self.read(a)), _key(self.read(b))) for case, a, b in SEED_7770101_M if _key(self.read(a)) != _key(self.read(b))])


# ==== the page: the Strategies tab's gate, a symbol typed in the order ticket ==========================================================
SPREAD = "buy 1 ACME Dec 18 2026 60/65 call spread"
F_USX_HASH = "#/a/F1%3AUS_EXCHANGE"
#: the reading as the owner sees it: the words not read, each card's Open in ticket and why not, the group's Strategies tab and why not
GROUP_SEEN = r"""(function () {
  var g = document.querySelector('#smart-cards .smart-group'); var sb = g ? g.querySelector('[data-act="strategies"]') : null;
  return { unread: Array.prototype.map.call(document.querySelectorAll('#smart-readback .smart-unparsed'), function (x) { return x.textContent; }),
           cards: Array.prototype.map.call(document.querySelectorAll('#smart-cards [data-i]'), function (c) {
             var o = c.querySelector('[data-act="open"]'); return { open: !!(o && !o.disabled), why: o ? (o.title || '') : '' }; }),
           tab: sb ? { enabled: !sb.disabled, title: sb.title || '' } : null, status: (document.querySelector('#smart-status') || {}).innerText || '' };
})()"""
#: the group's ONE_ORDER_ASKED flag box opened: its action as the owner sees it
FLAG_ACTION = r"""(function () {
  var g = document.querySelector('#smart-cards .smart-group');
  var c = Array.prototype.filter.call(g.querySelectorAll(':scope > .smart-flags .smart-chip'), function (b) { return /one order/.test(b.title); })[0];
  if (!c) { return null; } c.click(); return true; })()"""
FLAG_ACTION_SEEN = r"""(function () {
  var b = document.querySelector('#smart-cards .smart-group .smart-flagbox [data-act="action"]'); if (!b) { return null; }
  var why = b.parentNode.querySelector('.smart-why');
  return { label: b.innerText, enabled: !b.disabled, title: b.title || '', why: why ? why.innerText : '' };
})()"""
#: the order ticket as the owner sees it
TICKET = r"""(function () {
  var q = function (s) { return document.querySelector(s); }; var b = q('#ticket-submit-btn');
  return { hash: location.hash, box: (q('#ticket-instrument') || {}).value, submit: !!(b && !b.disabled), title: b ? b.title : '',
           status: (q('#ticket-status') || {}).textContent || '', meta: (q('.inst-meta') || {}).textContent || '' };
})()"""


@unittest.skipUnless(find_browser(), NO_BROWSER)
class PageTests(unittest.TestCase):
    """One offline server (port 0, a temp home, test_terminal_simplify_r6's world: the three books, ACME / WIDG printed) with the smoke
    test's invented ACME chain cached, and one headless browser for the class, both closed in tearDownClass."""

    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp(prefix="fst-simplify-r7-page-")
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
        self.addCleanup(undo)

    def orders(self) -> list:
        with self.server.rt.db.reader() as conn:
            return [tuple(r) for r in conn.execute("SELECT order_id, instrument_id, side, qty FROM orders ORDER BY rowid").fetchall()]

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

    def no_orders(self, start: int, before: list) -> None:
        self.assertEqual([], self.order_posts(start), "the page sent an order request")
        self.assertEqual(before, self.orders(), "an order was placed")
        self.assertEqual([], self.browser.errors(self.sid, start))

    def press_held(self, sel: str) -> dict:
        """A held button made pressable by force, then clicked: the page's own click checks the gate again and opens nothing."""
        self.js("(function (s) { var b = document.querySelector(s); b.disabled = false; b.click(); return true; })(%s)" % json.dumps(sel))
        self.browser.pump(1.5)
        return self.js(GROUP_SEEN) or {}

    def land(self, sel: str, acct_hash: str) -> None:
        """The Strategies tab pressed: the order ticket of that account, on ACME, its hedge panel on the Strategies tab."""
        self.js("document.querySelector(%s).click(); true" % json.dumps(sel))
        self.wait("location.hash.indexOf(%s) === 0 && !!(T.dock && T.dock.visible()) && !!document.querySelector('#ticket-instrument')"
                  " && document.querySelector('#ticket-instrument').value === 'ACME' && !!document.querySelector('#hg-strategy')" % json.dumps(acct_hash), timeout=40)

    # ---- the Strategies tab ---------------------------------------------------------------------------------------------------------
    def test_a_the_groups_strategies_tab_is_held_while_words_are_unread(self):
        self.tidy()
        before, start = self.orders(), self.browser.mark()
        self.fresh_smart("P")
        self.read(SPREAD + " as one order (fund)", 2)
        seen = self.js(GROUP_SEEN)
        self.assertIn("fund", " ".join(seen["unread"]), seen)
        why = self.held_as_the_cards(seen)
        got = self.press_held("#smart-cards .smart-group [data-act=\"strategies\"]")
        self.assertTrue(self.js("location.hash").startswith("#/smart"), got)
        self.assertEqual(why, got.get("status"), got)                                      # the click checks again and says why
        self.no_orders(start, before)

    def held_as_the_cards(self, seen: dict) -> str:
        """Every card's Open in ticket and the group's Strategies tab held, for the one reason of the instruction (textBlock)."""
        whys = {c["why"] for c in seen["cards"]}
        self.assertTrue(seen["cards"] and not any(c["open"] for c in seen["cards"]) and len(whys) == 1, seen)
        why = whys.pop()
        self.assertTrue(why.startswith("FinSim could not read the underlined words") or why.startswith("the instruction has a red flag"), why)
        self.assertEqual({"enabled": False, "title": why}, seen["tab"], seen)
        return why

    def test_b_the_flag_boxs_strategies_tab_is_held_with_the_reason_beside_it(self):
        self.tidy()
        before, start = self.orders(), self.browser.mark()
        self.fresh_smart("P")
        self.read(SPREAD + " as one order (fund)", 2)
        why = self.held_as_the_cards(self.js(GROUP_SEEN))
        self.assertTrue(self.js(FLAG_ACTION))
        self.browser.pump(0.3)
        act = self.js(FLAG_ACTION_SEEN)
        self.assertEqual(("Strategies tab", False, why), (act["label"], act["enabled"], act["title"]), act)
        self.assertTrue(act["why"] and why.startswith(act["why"].rstrip(" .")), act)                # the reason beside it
        got = self.press_held("#smart-cards .smart-group .smart-flagbox [data-act=\"action\"]")
        self.assertTrue(self.js("location.hash").startswith("#/smart"), got)
        self.assertEqual(why, got.get("status"), got)
        self.no_orders(start, before)

    def test_c_an_account_not_read_holds_the_strategies_tab(self):
        self.tidy()
        before, start = self.orders(), self.browser.mark()
        self.fresh_smart("P")
        self.read(SPREAD + " in my IRA", 2)
        seen = self.js(GROUP_SEEN)
        self.assertTrue(seen["unread"], seen)
        why = self.held_as_the_cards(seen)
        got = self.press_held("#smart-cards .smart-group [data-act=\"strategies\"]")
        self.assertTrue(self.js("location.hash").startswith("#/smart"), got)
        self.assertEqual(why, got.get("status"), got)
        self.no_orders(start, before)

    def test_d_controls_a_reading_that_holds_opens_the_strategies_tab_of_its_account(self):
        self.tidy()
        before, start = self.orders(), self.browser.mark()
        for text, acct_hash in ((SPREAD + " as one order in the Fund book", F_USX_HASH), (SPREAD + " as one order", R6.P_USX_HASH), (SPREAD, R6.P_USX_HASH)):
            with self.subTest(text=text):
                self.fresh_smart("P")
                self.read(text, 2)
                seen = self.js(GROUP_SEEN)
                self.assertEqual(([], True), (seen["unread"], (seen["tab"] or {}).get("enabled")), seen)    # the group's own flags never hold it
                self.land("#smart-cards .smart-group [data-act=\"strategies\"]", acct_hash)
        self.no_orders(start, before)

    # ---- a symbol typed in the order ticket's box ------------------------------------------------------------------------------------
    def key(self, key: str, code: str, vk: int, mods: int = 0, char: bool = False) -> None:
        base = {"key": key, "code": code, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk, "modifiers": mods}
        self.browser.call("Input.dispatchKeyEvent", dict(base, type="rawKeyDown"), session=self.sid)
        if char:
            self.browser.call("Input.dispatchKeyEvent", dict(base, type="char", text="\r"), session=self.sid)
        self.browser.call("Input.dispatchKeyEvent", dict(base, type="keyUp"), session=self.sid)

    def click(self, sel: str) -> None:
        """A real mouse click (the box loses the focus first, as for the owner)."""
        box = self.js("(function () { var r = document.querySelector(%s).getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()" % json.dumps(sel))
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            self.browser.call("Input.dispatchMouseEvent", {"type": kind, "x": box[0], "y": box[1], "button": "left" if kind != "mouseMoved" else "none",
                                                           "clickCount": 1 if kind != "mouseMoved" else 0}, session=self.sid)

    def type_in_box(self, text: str) -> None:
        self.js("(function () { var b = document.querySelector('#ticket-instrument'); b.focus(); b.select(); return true; })()")
        self.browser.call("Input.insertText", {"text": text}, session=self.sid)
        time.sleep(0.6)                                   # the type-ahead answers
        self.browser.pump(0.2)

    def previewed_acme(self) -> None:
        """'buy 10 ACME' read, its card opened in the ticket and previewed: Submit enabled for BUY 10 ACME."""
        self.fresh_smart("P")
        self.read("buy 10 ACME", 1)
        self.js("document.querySelector('#smart-cards [data-i=\"0\"] [data-act=\"open\"]').click(); true")
        self.wait("location.hash.indexOf(%s) === 0 && !!(T.dock && T.dock.visible()) && document.querySelector('#ticket-instrument').value === 'ACME'"
                  % json.dumps(R6.P_USX_HASH), timeout=30)
        self.wait("!document.querySelector('#ticket-submit-btn').disabled", timeout=40)
        self.browser.pump(0.3)

    def wait_orders(self, before: list, timeout: float = 10.0) -> list:
        deadline = time.time() + timeout
        while time.time() < deadline and len(self.orders()) == len(before):
            self.browser.pump(0.2)
        self.browser.pump(0.3)
        return self.orders()[len(before):]

    def test_e_a_symbol_typed_and_not_resolved_holds_every_send(self):
        self.tidy()
        self.previewed_acme()
        before = self.orders()
        self.type_in_box("WIDG")
        t = self.js(TICKET)
        self.assertEqual(("WIDG", False), (t["box"], t["submit"]), t)
        said = "the instrument box shows WIDG and the order filled in is for ACME: press Enter in the box to resolve it (or type ACME back)"
        self.assertEqual(("submit (Ctrl+Enter) - " + said, said), (t["title"], t["status"]), t)
        self.click("#ticket-submit-btn")                                         # disabled: nothing
        self.assertEqual([], self.wait_orders(before, 3.0))
        self.js("document.querySelector('#ticket-instrument').focus(); true")
        self.key("Enter", "Enter", 13, mods=2)                                   # Ctrl+Enter in the box: refused, said
        self.assertEqual([], self.wait_orders(before, 3.0))
        t = self.js(TICKET)
        self.assertEqual("not sent: " + said, t["status"], t)
        self.type_in_box("ACME")                                                 # typed back: the previewed order again
        self.wait("!document.querySelector('#ticket-submit-btn').disabled", timeout=20)
        self.assertEqual(("ACME", ""), (self.js(TICKET)["box"], self.js(TICKET)["status"]))
        self.assertEqual(before, self.orders())

    def test_f_control_the_symbol_resolved_with_enter_is_the_order_sent(self):
        self.tidy()
        self.previewed_acme()
        self.type_in_box("WIDG")
        self.key("Enter", "Enter", 13, char=True)                                # Enter resolves it: a new, empty WIDG order
        self.wait("(function () { var s = T.components.ticket.state(); return !!s && s.instrument_id === 'WIDG' && document.querySelector('#ticket-instrument').value"
                  " === 'WIDG' && document.querySelector('#ticket-submit-btn').disabled; })()", timeout=20)
        self.js("(function () { var q = document.querySelector('#ticket-qty'); q.focus(); q.value = '5'; q.dispatchEvent(new Event('input', { bubbles: true })); return true; })()")
        self.js("document.querySelector('#ticket-preview-btn').click(); true")
        self.wait("!document.querySelector('#ticket-submit-btn').disabled", timeout=30)
        before = self.orders()
        self.click("#ticket-submit-btn")
        placed = self.wait_orders(before, 15.0)
        self.assertEqual([("WIDG", "BUY", 5)], [o[1:] for o in placed], self.js(TICKET))


class TicketSourceTests(unittest.TestCase):
    def test_every_send_is_held_while_the_box_names_another_symbol(self):
        src = _read(os.path.join(STATIC, "components", "ticket.js"))
        for s in ("function typedPending() {",
                  "return txt.toUpperCase() === String(st.instrument_id || '').toUpperCase() ? null : txt;",
                  "if (typedPending() !== null) { return false; }",
                  "var sk = kl('ticket.submit'); var tp = typedPending();",
                  "(tp !== null ? typedWords(tp) :",
                  "var tp = typedPending(); paintSubmit(); paintStatus(tp !== null ? typedWords(tp) : '');",
                  "st.submitting || st.dirty || st.stale || typedPending() !== null) { scheduleReprev(); return; }"):
            self.assertIn(s, src, s)
        # a mode entered on the instrument filled in (amend / replace, hedge this position) names it in the box, as a fresh hand-off does
        self.assertIn("function syncBox(id) { clearTimeout(st.taTimer); st.taTimer = null; ui.inst.value = id || ''; ui.dd.hidden = true; }", src)
        for fn in ("function enterPositionMode(id, f) {", "function enterOrderMode(mode, o) {"):
            at = src.index(fn)
            self.assertIn("syncBox(id);", src[at:src.index("\n  function ", at + 10)], fn)
        guard = "var tp = typedPending(); if (tp !== null) { paintStatus('not sent: ' + typedWords(tp), 'err'); paintSubmit(); return"
        self.assertEqual(2, src.count(guard))                   # (simplify round 10: send's "return null;" - nothing sent, nothing to wait for)
        sub, snd = src.index("function submit(opts) {"), src.index("function send(body, rid, kind) {")
        self.assertLess(src.index(guard, sub), src.index("if (!opts.force && !canSubmit())", sub))          # before a forced submit too
        self.assertLess(src.index(guard, snd), src.index("var gen = st.gen, words = orderWords(kind);", snd))
        # send is the one place an order is placed, amended or replaced from the ticket
        end = src.index("\n  function ", snd + 10)
        calls = [m.start() for m in re.finditer(r"T\.api\.(post|patch)\('/api/orders(', body|/' \+ encodeURIComponent)", src)]
        self.assertTrue(calls and all(snd < c < end for c in calls), calls)


if __name__ == "__main__":
    unittest.main()
