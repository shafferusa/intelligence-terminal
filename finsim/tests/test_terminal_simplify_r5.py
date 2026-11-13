"""FinSim 1.0.7.2 simplify review round 5 (on afe7aac): the regression tests of its findings, kept in the terminal suite.

The money path (F1).  A short sale placed while the account was flat and still resting when the name has been bought is suspended
SHORT_WHILE_LONG (round 4).  Its reason and its one WARN item used to say "sell them first, then resume the short sale, or cancel it" -
advice FinSim refuses: while the short sale works (suspended included) the shares it is counted against cannot be sold (OVERSELL), and
a resume while they are held suspends it again.  They now say the way out FinSim allows, and no "resume":
  ShortSaleAdviceTests
    test_a  the reason and the item say "cancel it - while it works, the shares it is counted against cannot be sold - then sell them
            and place the short sale again"; following it works: the sale is refused while the short sale works (OVERSELL: cancel it
            first), cancelled the shares sell, the account is flat and the short sale placed again fills
            (tests/test_reviews5_money_path_short_advice.py test_a).
Autofill and the page (F2, F5): a product's own ticket (the FX desk, the bond ticket, the private-markets ticket) opens a NEW trade from
the hand-off's side, amount and account only.  A price, a time in force, an account, a side or a kind of order typed for it was read,
then dropped from that hand-off without a word ("at 1.08" even said "read as a limit"):
  OwnTicketTermsTests (the corpus world of tests/test_terminal_smart_ticket.py, no browser)
    test_a  a price or a time in force typed: the red flag OWN_TICKET_TERMS names them as not carried, never "read as a limit"; its
            choice "without ..." re-reads clean with the same hand-off (tests/test_reviews5_autofill_own_ticket_terms.py test_b,
            tests/test_reviews5_page_own_ticket.py test_d);
    test_b  a side other than buy / sell, "all", a unit, a close or a covered call: the red flag, no choice, "write the order as buy or
            sell with a number";
    test_c  an account named that is not the one the ticket opens in: the red flag, never a choice (the choice guard writes an account
            an order named back into the text, so "without 'at pb'" would have kept it), "remove it"; the account it opens in: nothing;
    test_d  controls: a plain purchase or sale, "market", shares of a private company, another book: no red flag but its own ticket's;
            a standard ticket carries its limit.
The property tester (F6): a collar read on shares held free returned its put and call without the account flags of the shares leg, so a
book holding the shares in two accounts had the collar sent from the first, unasked, where the covered call asks:
  CollarAccountTests (two trading accounts per book, built here as tests/test_props5_parse_oracle.py builds its world)
    test_a  ASK: the Personal book holds ACME in P1:US_EXCHANGE and P1:PB_MS: the collar's put and call carry the ACCOUNT_CHOICE red
            flag, no request (as the covered call), and its choices read sendable in the account named;
    test_b  OFFER: the Fund book holds ACME in F1:PB_GS only: the collar read there offers US Exchange (ACCOUNT_CHOICE check);
    test_c  strikes in the wrong order (STRIKE_ORDER): the same account flags;
    test_d  controls: an account named, a book with one trading account: no ACCOUNT_CHOICE;
    test_seed_5550101_cases_41_60_73_93_95  the property run's first disagreements, each instruction as the seed drew it.
The page (F3, F4; the real offline server on port 0 with the Personal and the Fund books, the real pages in a headless Edge / Chrome):
  OwnTicketPageTests
    test_e  "Open the FX desk" is held as Open in ticket is - unread words ("3 month forward"), a red flag of the card or of its group
            ("as one order"), a term the desk cannot take (OWN_TICKET_TERMS): the button disabled with the reason beside it, no desk
            opened (tests/test_reviews5_page_own_ticket.py test_a / test_b / test_c); the choice "without 'at 1.08' and 'gtc'" then
            the desk opens on the plain deal (the control, test_a2); no amount typed ("how many?") does not hold it (the desk asks);
    test_f  a group's sending order follows the sides its cards hold now: a bought call spread with both sides flipped in place is
            labelled #2 (now the purchase) first, as the sold spread typed is (tests/test_reviews5_autofill_group_order_after_edit.py).
Fictional ACME / WIDG only; temp homes; no ~/.finsim, no network, no port 8765 / 8865; nothing is sent (asserted).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import unittest

from tests import test_terminal_browser_smoke as SMK
from tests import test_terminal_simplify_r4 as R4
from tests import test_terminal_smart_ticket as STT
from tests.terminal_cdp import NO_BROWSER, Browser, find_browser
from tests.terminal_helpers import _Log, assert_integrity
from tests.test_terminal_smart_submit import AT, ROWS, acme, acme_chain, add_name, load_chain, print_acme
from finsim.terminal import books as _books, config, orders, server as _server, smart_ticket as st
from finsim.terminal.clock import FakeClock
from finsim.terminal.products.options import ensure_option
from finsim.terminal.quotes.providers import FakeProvider


# ==== the money path ===================================================================================================================
ADVICE = "a short sale is never booked against shares held: cancel it - while it works, the shares it is counted against cannot be sold" \
         " - then sell them and place the short sale again"


class ShortSaleAdviceTests(R4._ShortBase):
    """test_terminal_simplify_r4's money-path world (Test Book T1, ACME at 100.00, a borrow located for a short sale)."""

    def rid(self, what: str) -> str:
        self.k += 1
        return f"ui:s5-{what}-{self.k:04d}"

    def test_a_the_suspended_short_sale_says_the_way_out_finsim_allows_and_it_works(self):
        self.assertEqual({}, self.positions())
        short = self.short_sale(150)                                      # flat: SELL_SHORT 150 limit 120.00 rests
        self.hold(250)                                                    # then the name is bought
        self.acme_print("120.00")
        r = self.row(short)
        self.assertEqual(("SUSPENDED", 0, {"ACME": 250}), (r["status"], int(r["filled_qty"]), self.positions()), r)
        reason = str(r["reason"])
        self.assertTrue(reason.startswith("SHORT_WHILE_LONG: SELL_SHORT 150 ACME: T1:US_EXCHANGE holds 250 ACME long - " + ADVICE), reason)
        self.assertNotIn("resume", reason)                                # afe7aac: "sell them first, then resume the short sale"
        items = self.q("SELECT severity, code, message FROM attention WHERE resolved_ts IS NULL AND ref_id=?", (short,))
        self.assertEqual([("WARN", "ORDER_SUSPENDED")], [(x["severity"], x["code"]) for x in items], items)
        self.assertTrue(items[0]["message"].endswith(ADVICE), items[0]["message"])
        self.assertNotIn("resume", items[0]["message"])                   # afe7aac: "...; fix the cause and resume it, or cancel it"
        self.assertEqual(("SHORT_WHILE_LONG",), orders.SELF_ADVISED_CODES)   # the one refusal whose item adds no "resume it"
        # the advice, followed: while the short sale works its shares cannot be sold - the preview and the order say to cancel it first
        self.assertEqual(["OVERSELL"], self.sale_blockers(250))
        code, status, out = self.place("ACME", "SELL", 250)
        self.assertEqual((400, "OVERSELL"), (code, status), out)
        self.assertIn(f"150 ACME are counted for the short sale still working ({short}): a short sale is never booked against shares held,"
                      " so cancel it first", out["error"]["message"])
        self.cancel(short)                                                # cancel it ...
        self.assertEqual([], [x for x in self.open_items() if x[2] == short])
        self.assertEqual((200, "FILLED"), self.place("ACME", "SELL", 250)[:2])  # ... then sell them: flat
        self.assertEqual({}, self.positions())
        code, status, out = self.place("ACME", "SELL_SHORT", 150, order_type="LIMIT", limit_px="125.00")   # ... and place it again
        self.assertEqual((200, "WORKING"), (code, status), out)
        self.acme_print("125.00")
        self.assertEqual("FILLED", self.row(out["order"]["order_id"])["status"])
        self.assertEqual({"ACME": -150}, self.positions())
        assert_integrity(self, self.rt, "full")


# ==== autofill: the product's own ticket =================================================================================================
FX = {"kind": "FX", "account_id": "P1:FOREIGN_FX", "instrument_id": "EURUSD", "buy_ccy": "EUR", "sell_ccy": "USD"}
BOND = {"kind": "BOND", "account_id": "P1:DEBT", "instrument_id": "ACME-31"}
PRIVATE = {"kind": "PRIVATE", "account_id": None, "instrument_id": "PRV-WIDGLABS"}
#: (text, the action's label, its ticket fields, the words said not to be carried, the text the choice gives)
PRICED = [("buy 10000 ACME-31 limit 99.5", "Open the bond ticket", dict(BOND, side="BUY", qty=10000), ["'limit 99.5'"], "buy 10000 ACME-31"),
          ("sell 10000 ACME-31 gtc", "Open the bond ticket", dict(BOND, side="SELL", qty=10000), ["'gtc'"], "sell 10000 ACME-31"),
          ("buy 1m EURUSD at 1.08", "Open the FX desk", dict(FX, amount="1000000"), ["'at 1.08'"], "buy 1m EURUSD"),
          ("buy 10k EURUSD limit 1.08", "Open the FX desk", dict(FX, amount="10000"), ["'limit 1.08'"], "buy 10k EURUSD"),
          ("buy 10k EURUSD at 1.08 gtc", "Open the FX desk", dict(FX, amount="10000"), ["'at 1.08'", "'gtc'"], "buy 10k EURUSD"),
          ("buy 10k EURUSD stop 1.2", "Open the FX desk", dict(FX, amount="10000"), ["'stop 1.2'"], "buy 10k EURUSD"),
          ("buy 10k EURUSD gtd dec 20", "Open the FX desk", dict(FX, amount="10000"), ["'gtd dec 20'"], "buy 10k EURUSD"),
          ("buy 10k EURUSD day", "Open the FX desk", dict(FX, amount="10000"), ["'day'"], "buy 10k EURUSD"),
          ("sell 50 PRV-WIDGLABS at 12", "Open the private-markets ticket", dict(PRIVATE, side="SELL", qty=50), ["'at 12'"], "sell 50 PRV-WIDGLABS")]
#: (text, the words said not to be carried): a side, an amount or a kind of order the hand-off cannot take
SHAPED = [("short 10k EURUSD", ["'short'"]), ("sell all ACME-31", ["'all'"]), ("buy 10 contracts EURUSD", ["'contracts'"]), ("close EURUSD", ["'close'"]),
          ("close ACME-31 limit 99", ["'close'", "'limit 99'"]), ("covered call 100 EURUSD Dec 18 2026 1.1", ["'covered call'"])]


def _listed(words):
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def _flags(leg, code):
    return [f for f in leg["flags"] if f["code"] == code]


class OwnTicketTermsTests(unittest.TestCase):
    """The corpus world (fictional ACME / WIDG, the bond ACME-31, the pair EURUSD, the private company PRV-WIDGLABS), a temp home."""

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

    def own(self, leg) -> dict:
        got = _flags(leg, "KIND_OWN_TICKET")
        self.assertEqual(1, len(got), leg["flags"])
        self.assertEqual("blocker", got[0]["severity"])
        return got[0]["action"]

    def terms(self, leg) -> dict:
        got = _flags(leg, "OWN_TICKET_TERMS")
        self.assertEqual(1, len(got), leg["flags"])
        self.assertEqual("blocker", got[0]["severity"])
        return got[0]

    def test_a_a_price_or_time_in_force_typed_is_said_not_carried_and_its_choice_reads_clean(self):
        for text, label, ticket, words, clean in PRICED:
            with self.subTest(text=text):
                out = self.read(text)
                self.assertEqual((False, []), (out["ok"], out["unparsed"]))
                self.assertEqual(1, len(out["legs"]), out)
                leg = out["legs"][0]
                self.assertIsNone(leg["request"])
                act = self.own(leg)
                self.assertEqual(("open_ticket", label, ticket), (act["kind"], act["label"], act["ticket"]))
                self.assertEqual([], _flags(leg, "TYPE_FROM_AT"))             # afe7aac: "'at 1.08' read as a limit 1.08" - nothing carries it
                self.assertFalse(any("read as a limit" in f["text"] for f in leg["flags"]), leg["flags"])
                f = self.terms(leg)
                said = f"so {_listed(words)} {'is' if len(words) == 1 else 'are'} not carried ("
                self.assertIn(said, f["text"])
                self.assertTrue(f["text"].startswith(("the FX desk of P1:FOREIGN_FX opens a new trade", "the bond ticket of P1:DEBT opens a new trade",
                                                      "the private-markets ticket opens a new trade")), f["text"])
                self.assertEqual([{"label": "without " + _listed(words), "clause": clean}], f["choices"])
                # the choice as the page applies it (its words in place of the flag's span), then a new reading: the same hand-off, clean
                a, b = f["span"]
                again = self.read(text[:a] + f["choices"][0]["clause"] + text[b:])
                self.assertEqual(clean, again["text"])
                self.assertEqual(1, len(again["legs"]), again)
                self.assertEqual([], _flags(again["legs"][0], "OWN_TICKET_TERMS"))
                self.assertEqual(act, self.own(again["legs"][0]))

    def test_b_a_side_amount_or_kind_the_hand_off_cannot_take_asks_for_the_order_written_again(self):
        for text, words in SHAPED:
            with self.subTest(text=text):
                out = self.read(text)
                self.assertFalse(out["ok"])
                leg = out["legs"][0]
                self.own(leg)
                f = self.terms(leg)
                self.assertIn(f"so {_listed(words)} {'is' if len(words) == 1 else 'are'} not carried (", f["text"])
                self.assertTrue(f["text"].endswith(": write the order as buy or sell with a number"), f["text"])
                self.assertIsNone(f.get("choices"))
                self.assertTrue(all(l["request"] is None for l in out["legs"] if _flags(l, "KIND_OWN_TICKET")), out["legs"])

    def test_c_an_account_named_is_said_never_offered_as_a_choice_and_its_own_account_is_not_said(self):
        out = self.read("buy 10k EURUSD at pb")
        f = self.terms(out["legs"][0])
        self.assertIn("so 'at pb' is not carried (", f["text"])
        self.assertTrue(f["text"].endswith(": remove it"), f["text"])
        self.assertIsNone(f.get("choices"))
        # two orders, the account phrase on the last: a choice would have had the guard write 'at pb' back ("without 'at pb'" keeping it)
        out = self.read("buy 100 ACME, then sell 10k EURUSD at 1.08 at pb")
        self.assertEqual(2, len(out["legs"]), out)
        req0 = out["legs"][0]["request"] or {}
        self.assertEqual(("P1:US_EXCHANGE", "BUY", 100), (req0.get("account_id"), req0.get("side"), req0.get("qty")))   # the other order: as typed
        f = self.terms(out["legs"][1])
        self.assertIn("so 'at 1.08' and 'at pb' are not carried (", f["text"])
        self.assertTrue(f["text"].endswith(": remove them"), f["text"])
        self.assertIsNone(f.get("choices"))
        # a price on the last of two orders: its choice rewrites that order only
        out = self.read("buy 100 ACME, then sell 10k EURUSD gtc")
        f = self.terms(out["legs"][1])
        self.assertEqual([{"label": "without 'gtc'", "clause": "sell 10k EURUSD"}], f["choices"])
        self.assertIsNotNone(out["legs"][0]["request"])
        # the account the desk opens in, named: nothing to say
        out = self.read("buy 10k EURUSD at P1:FOREIGN_FX")
        self.assertEqual(dict(FX, amount="10000"), self.own(out["legs"][0])["ticket"])
        self.assertEqual([], _flags(out["legs"][0], "OWN_TICKET_TERMS"))

    def test_d_controls_what_the_hand_off_carries_raises_no_flag_and_a_standard_ticket_keeps_its_limit(self):
        for text, ticket, book in (("buy 1m EURUSD", dict(FX, amount="1000000"), "P"), ("sell 1m EURUSD", dict(FX, buy_ccy="USD", sell_ccy="EUR", amount="1000000"), "P"),
                                   ("buy 10k EURUSD market", dict(FX, amount="10000"), "P"),
                                   ("sell 50 shares PRV-WIDGLABS", dict(PRIVATE, side="SELL", qty=50), "P"),
                                   ("buy 10k EURUSD in the fund book", dict(FX, account_id="F1:FOREIGN_FX", amount="10000"), "P")):
            with self.subTest(text=text):
                out = self.read(text, book)
                leg = out["legs"][0]
                self.assertEqual(ticket, self.own(leg)["ticket"])
                self.assertEqual([], _flags(leg, "OWN_TICKET_TERMS"))
        req = self.read("buy 100 ACME limit 230")["legs"][0]["request"] or {}
        self.assertEqual(("LIMIT", "230"), (req.get("order_type"), str(req.get("limit_px"))), req)


# ==== the property tester: a collar's account =============================================================================================
E1 = "2026-12-18"
US = {b: f"{b}1:US_EXCHANGE" for b in "PFI"}
PB = {"P": "P1:PB_MS", "F": "F1:PB_GS"}
#: tests/test_props5_parse_oracle.py's holdings, made with the normal order route and the matcher
SETUP = [(US["P"], "ACME", "BUY", 300), (PB["P"], "ACME", "BUY", 200), (PB["P"], acme("C", 100, E1), "BTO", 2), (PB["F"], "ACME", "BUY", 400),
         (PB["F"], acme("C", 110, E1), "STO", 1), (US["I"], "ACME", "BUY", 100)]
PUT90, CALL105 = acme("P", 90, E1), acme("C", 105, E1)


def _two_account_world():
    """The real books P / F / I (Personal selected), the prime-broker accounts P1:PB_MS and F1:PB_GS opened as onboarding opens them,
    the fictional ACME at 100.00 (an invented ADV) with the invented chain of tests/test_terminal_smart_submit, and the holdings of
    ``SETUP``: P1:US_EXCHANGE 300 ACME; P1:PB_MS 200 ACME and 2 Dec 18 2026 100 calls; F1:PB_GS 400 ACME and 1 Dec 18 2026 110 call
    short; F1:US_EXCHANGE nothing; I1:US_EXCHANGE 100 ACME.  Returns (temp home, runtime)."""
    tmp = tempfile.mkdtemp(prefix="fst-simplify-r5-acct-")
    os.environ[config.HOME_ENV] = tmp
    os.environ[config.DB_ENV] = os.path.join(tmp, "terminal.db")
    clock = FakeClock(AT)
    rt = _server.build_runtime(tmp, os.path.join(tmp, "terminal.db"), clock=clock, offline=True, fake_provider=FakeProvider(clock), log=_Log(),
                               books=list(config.BOOKS), fetch_threads=0, news_enabled=False)
    try:
        add_name(rt, "WIDG")
        with rt.db.writer() as tx:
            rt.instruments.upsert(tx, instrument_id="ACME", kind="EQUITY", symbol="ACME", name="ACME test listing", currency="USD", session_group="US_EQUITY",
                                  account_kinds=["US_EXCHANGE", "PB"], yahoo_symbol="ACME", cboe_symbol="ACME", spec={"exchange": "NYQ", "adv": 5000000})
            ts = int(tx.now.timestamp())
            for b, lid, role, name, broker in (("P", "P1", "PB_MS", "Morgan Stanley PB", "MS"), ("F", "F1", "PB_GS", "Goldman Sachs PB", "GS")):
                _books._insert_account(tx.conn, ts, b, lid, role, name, "PB", 60, broker=broker)
        rt.instruments.load()
        now = rt.clock.epoch()
        print_acme(rt, "100.00", 120, now - 180, 50000)
        snap = acme_chain(now, size=50)
        load_chain(rt, snap)
        for e, r, k, _b, _a in ROWS:
            ensure_option(rt, acme(r, k, e), chain=snap)
        load_chain(rt, snap)
        for i, (aid, iid, side, qty) in enumerate(SETUP):
            body = {"account_id": aid, "instrument_id": iid, "side": side, "qty": qty, "order_type": "MARKET", "tif": "DAY", "confirm_large": True,
                    "request_id": f"ui:s5-acct-{i:03d}"}
            code, out, _h = rt.router.handle("POST", f"/api/orders?book={aid[0]}", None, body, "127.0.0.1", None)
            assert code == 200, (body, out)
            rt.matcher.on_quotes(["ACME"] + [acme(r, k, e) for e, r, k, _b, _a in ROWS])
            rt.matcher.sweep()
        with rt.db.reader() as conn:
            got = {(r["account_id"], r["instrument_id"]): int(r["qty"]) for r in conn.execute("SELECT account_id, instrument_id, qty FROM v_positions").fetchall()
                   if int(r["qty"])}
        assert got == {(US["P"], "ACME"): 300, (PB["P"], "ACME"): 200, (PB["P"], acme("C", 100, E1)): 2, (PB["F"], "ACME"): 400,
                       (PB["F"], acme("C", 110, E1)): -1, (US["I"], "ACME"): 100}, got
    except Exception:
        rt.close()
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return tmp, rt


def _choice(leg, severity=None):
    return [f for f in leg["flags"] if f["code"] == "ACCOUNT_CHOICE" and (severity is None or f["severity"] == severity)]


def _brief(leg):
    return (leg["book"], leg["account_id"], leg["side"], leg["qty"], (leg["instrument"] or {}).get("id"), leg["request"] is not None,
            sorted({f["code"] for f in leg["flags"] if f["severity"] == "blocker"}), sorted({f["severity"] for f in _choice(leg)}))


class CollarAccountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = {k: os.environ.get(k) for k in (config.HOME_ENV, config.DB_ENV)}
        try:
            cls.tmp, cls.rt = _two_account_world()
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

    def read(self, text, book="P"):
        return st.parse_view(self.rt, {"text": text, "book": book})

    def test_a_ask_a_collar_on_shares_held_in_two_accounts_asks_for_the_account_as_the_covered_call_does(self):
        cc = self.read("covered call 100 ACME Dec 18 2026 105")
        self.assertEqual([("P", "P1:US_EXCHANGE", "STO", 1, CALL105, False, ["ACCOUNT_CHOICE"], ["blocker"])], [_brief(l) for l in cc["legs"]])
        out = self.read("collar 100 ACME Dec 18 2026 90/105")
        self.assertFalse(out["ok"])
        self.assertEqual([("P", "P1:US_EXCHANGE", "BTO", 1, PUT90, False, ["ACCOUNT_CHOICE"], ["blocker"]),
                          ("P", "P1:US_EXCHANGE", "STO", 1, CALL105, False, ["ACCOUNT_CHOICE"], ["blocker"])], [_brief(l) for l in out["legs"]])
        f = _choice(out["legs"][1])[0]
        self.assertIn("the Personal Book holds ACME equity (ACME test listing) in 2 accounts (200 shares long in P1:PB_MS, 300 shares long in"
                      " P1:US_EXCHANGE)", f["text"])
        self.assertEqual([("US Exchange (P1:US_EXCHANGE)", "collar 100 ACME Dec 18 2026 90/105 at us exchange"),
                          ("Morgan Stanley PB (P1:PB_MS)", "collar 100 ACME Dec 18 2026 90/105 at pb ms")], [(c["label"], c["clause"]) for c in f["choices"]])
        for clause, acct in ((f["choices"][0]["clause"], US["P"]), (f["choices"][1]["clause"], PB["P"])):
            again = self.read(clause)
            self.assertTrue(again["ok"], again)
            self.assertEqual([(acct, "BTO", PUT90, None), (acct, "STO", CALL105, True)],
                             [(l["request"]["account_id"], l["request"]["side"], l["request"]["instrument_id"], l["request"].get("covered")) for l in again["legs"]])

    def test_b_offer_a_collar_read_in_the_one_holding_account_offers_the_others(self):
        out = self.read("collar 100 ACME Dec 18 2026 90/105", "F")
        self.assertTrue(out["ok"])
        self.assertEqual([("F", "F1:PB_GS", "BTO", 1, PUT90, True, [], ["check"]), ("F", "F1:PB_GS", "STO", 1, CALL105, True, [], ["check"])],
                         [_brief(l) for l in out["legs"]])
        for leg in out["legs"]:
            f = _choice(leg)[0]
            self.assertEqual([("US Exchange (F1:US_EXCHANGE)", "collar 100 ACME Dec 18 2026 90/105 at us exchange")], [(c["label"], c["clause"]) for c in f["choices"]])
            self.assertIn("R-ACCT-1: F1:PB_GS, the account that holds it", leg["inferred"])

    def test_c_strikes_in_the_wrong_order_keep_the_account_flags(self):
        out = self.read("collar 100 ACME Dec 18 2026 105/90")
        self.assertEqual(2, len(out["legs"]), out)
        for leg in out["legs"]:
            self.assertEqual(["blocker"], sorted({f["severity"] for f in _choice(leg)}), leg["flags"])
            self.assertIn("STRIKE_ORDER", [f["code"] for f in leg["flags"]])
        out = self.read("collar 100 ACME Dec 18 2026 105/90", "F")
        for leg in out["legs"]:
            self.assertEqual((["check"], "F1:PB_GS"), (sorted({f["severity"] for f in _choice(leg)}), leg["account_id"]), leg["flags"])

    def test_d_controls_an_account_named_or_one_trading_account_asks_nothing(self):
        for text, book, acct in (("collar 100 ACME Dec 18 2026 90/105 at pb ms", "P", PB["P"]), ("collar 100 ACME Dec 18 2026 90/105 at us exchange", "P", US["P"]),
                                 ("collar 100 ACME Dec 18 2026 90/105 at pb", "F", PB["F"]), ("collar 100 ACME Dec 18 2026 90/105", "I", US["I"])):
            with self.subTest(text=text, book=book):
                out = self.read(text, book)
                self.assertTrue(out["ok"], out)
                self.assertEqual([(acct, "BTO", PUT90), (acct, "STO", CALL105)], [(l["account_id"], l["side"], (l["instrument"] or {}).get("id")) for l in out["legs"]])
                self.assertEqual([], [f for l in out["legs"] for f in _choice(l)])

    def test_seed_5550101_cases_41_60_73_93_95(self):
        """tests/test_props5_parse_oracle.py (seed 5550101, 2,200 instructions): its first disagreements on afe7aac, each instruction as
        the seed drew it - every one a collar read in the first holding account, its account flags dropped.  ASK: the Personal book's
        collar carries the ACCOUNT_CHOICE red flag and no request; OFFER: the Fund book's offers US Exchange."""
        P_US, F_PB = "P1:US_EXCHANGE", "F1:PB_GS"
        cases = {
            41: ("sell 1 ACME Dec 18 2026 100 call at P1:US_EXCHANGE in the Personal book;sell 50 ACME day in the Personal book;collar 100 ACME Dec 18 2026"
                 " 95/120 in the Fund book;buy 1 ACME Dec 18 2026 95 put", {3: (F_PB, "BTO", True, "check"), 4: (F_PB, "STO", True, "check")}),
            60: ("collar 100 ACME Dec 18 2026 95/120 in the Personal book\ncollar 200 ACME Dec 18 2026 95/120 in the Fund book\nbuy 1 ACME Dec 18 2026 95 put",
                 {1: (P_US, "BTO", False, "blocker"), 2: (P_US, "STO", False, "blocker"), 3: (F_PB, "BTO", True, "check"), 4: (F_PB, "STO", True, "check")}),
            73: ("collar 300 ACME Dec 18 2026 90/120 in the Personal book; covered call 100 ACME Dec 18 2026 105 at P1:US_EXCHANGE in the Fund book; collar"
                 " 100 ACME Dec 18 2026 90/105 at pb in the Personal book; buy 2 ACME Dec 18 2026 95 puts",
                 {1: (P_US, "BTO", False, "blocker"), 2: (P_US, "STO", False, "blocker"), 5: ("P1:PB_MS", "BTO", True, None), 6: ("P1:PB_MS", "STO", True, None)}),
            93: ("collar 300 ACME Dec 18 2026 95/105 in the Personal book; sell 3 ACME Dec 18 2026 100 calls at pb ms in the Institutional book; buy 2 ACME"
                 " Dec 18 2026 95 puts", {1: (P_US, "BTO", False, "blocker"), 2: (P_US, "STO", False, "blocker")}),
            95: ("buy 1 ACME Dec 18 2026 110 call in the Institutional book\ncollar 200 ACME Dec 18 2026 95/105 in the Personal book\nsell 1 ACME Dec 18 2026"
                 " 100 call at us exchange in the Fund book\nbuy 2 ACME Dec 18 2026 95 puts", {2: (P_US, "BTO", False, "blocker"), 3: (P_US, "STO", False, "blocker")}),
        }
        for case, (text, want) in cases.items():
            with self.subTest(case=case):
                out = self.read(text)
                self.assertFalse(out["ok"])
                legs = {l["n"]: l for l in out["legs"]}
                got = {n: (legs[n]["account_id"], legs[n]["side"], legs[n]["request"] is not None,
                           (sorted({f["severity"] for f in _choice(legs[n])}) or [None])[0]) for n in want}
                self.assertEqual(want, got, [_brief(l) for l in out["legs"]])


# ==== the page ===========================================================================================================================
BOUGHT = "buy 1 ACME Dec 18 2026 60/65 call spread"
SOLD = "sell 1 ACME Dec 18 2026 60/65 call spread"
#: card ``i``'s own-ticket red flag (the chip naming its kind) opened: its action button as the owner sees it
OWN_ACTION = """(function (i) {
  var c = document.querySelector('#smart-cards [data-i="' + i + '"]'); if (!c) { return null; }
  var box = c.querySelector('.smart-flagbox'); var b = box ? box.querySelector('[data-act="action"]') : null;
  if (!b) { return null; }
  var why = b.parentNode.querySelector('.smart-why');
  return { label: b.innerText, enabled: !b.disabled, title: b.title || '', why: why ? why.innerText : '' };
})(%d)"""
#: the group's labels: its head line; per card its number, the side it now holds, its tags ("C1 . send N of M", "edited"), Open in ticket
LABELS = """(function () {
  var g = document.querySelector('#smart-cards .smart-group-head');
  var cards = Array.prototype.map.call(document.querySelectorAll('#smart-cards [data-i]'), function (c) {
    var head = c.querySelector('.smart-card-head'); var b = head ? head.querySelector('b') : null;
    var m = b ? /side-([A-Z_]+)/.exec(b.className) : null; var open = c.querySelector('[data-act="open"]');
    return { num: (c.querySelector('.smart-num') || {}).textContent || '', side: m ? m[1] : '',
             tags: head ? Array.prototype.map.call(head.querySelectorAll('.smart-tag'), function (t) { return t.textContent; }) : [],
             open: !!(open && !open.disabled) };
  });
  return { group: g ? g.innerText : '', cards: cards };
})()"""


def _sending(labels: dict) -> list:
    """The cards in the order their tags say to send them: [(number, side it now holds), ...]."""
    at = {}
    for c in labels["cards"]:
        for t in c["tags"]:
            m = re.search(r"send (\d+) of (\d+)", t)
            if m:
                at[int(m.group(1))] = (c["num"], c["side"])
    return [at[k] for k in sorted(at)]


def _header_order(labels: dict) -> str:
    m = re.search(r"send them in this order: (.*)$", labels["group"].strip())
    return m.group(1).strip() if m else ""


@unittest.skipUnless(find_browser(), NO_BROWSER)
class OwnTicketPageTests(unittest.TestCase):
    """test_terminal_simplify_r4's page world (the Personal and the Fund books, ACME / WIDG, the invented ACME chain, an invented EURUSD
    print): one offline server (port 0, a temp home) and one headless browser for the class, both closed in tearDownClass."""

    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp(prefix="fst-simplify-r5-page-")
        cls.env = {k: os.environ.get(k) for k in (config.HOME_ENV, config.DB_ENV)}
        os.environ[config.HOME_ENV] = cls.home
        os.environ[config.DB_ENV] = os.path.join(cls.home, "terminal.db")
        cls.server = cls.browser = None
        try:
            clock = FakeClock(SMK.AT)
            cls.fp = FakeProvider(clock)
            R4._seed(cls.home, clock, cls.fp)
            cls.server = R4._Server4(cls.home, FakeClock(SMK.AT), cls.fp).start()
            R4.HandOffPageTests._served_chain.__func__(cls)
            cls.browser = Browser(find_browser(), 1440, 900)
            cls.sid = cls.browser.page("about:blank")
            cls.browser.navigate(cls.sid, cls.server.url)
            cls.browser.wait(cls.sid, R4.LOADED, timeout=45)
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

    # the owner's steps (test_terminal_simplify_r4.HandOffPageTests's, the same page and server)
    js, wait, press, smart, read, sends, tidy, desk_ready, deals = (R4.HandOffPageTests.js, R4.HandOffPageTests.wait, R4.HandOffPageTests.press,
                                                                   R4.HandOffPageTests.smart, R4.HandOffPageTests.read, R4.HandOffPageTests.sends,
                                                                   R4.HandOffPageTests.tidy, R4.HandOffPageTests.desk_ready, R4.HandOffPageTests.deals)
    drafts_gone = staticmethod(R4.HandOffPageTests.drafts_gone)

    def chip(self, i: int, pattern: str) -> None:
        """Card ``i``'s red-flag chip whose title matches ``pattern``, clicked (its box opens)."""
        self.js("(function () { var c = document.querySelector('#smart-cards [data-i=\"%d\"]'); var re = new RegExp(%s);"
                " var chip = Array.prototype.filter.call(c.querySelectorAll('.smart-chip.sev-blocker'), function (b) { return re.test(b.title); })[0];"
                " chip.click(); return true; })()" % (i, json.dumps(pattern)))
        self.wait("!!document.querySelector('#smart-cards [data-i=\"%d\"] .smart-flagbox')" % i, timeout=10)
        self.browser.pump(0.2)

    def chips(self, i: int) -> list:
        return self.js("Array.prototype.map.call(document.querySelectorAll('#smart-cards [data-i=\"%d\"] .smart-chip'), function (x) { return x.title; })" % i)

    def held_desk(self, i: int, why: str) -> dict:
        """Card ``i``'s "Open the FX desk": disabled, ``why`` its reason (title and the words beside it); clicked anyway, nothing opens."""
        self.chip(i, "currency pair")
        act = self.js(OWN_ACTION % i)
        self.assertEqual(("Open the FX desk", False), (act["label"], act["enabled"]), act)
        self.assertIn(why, act["title"])
        self.assertTrue(act["why"] and why.startswith(act["why"].rstrip(".").rstrip()[:40]), act)
        self.js("(function () { var b = document.querySelector('#smart-cards [data-i=\"%d\"] .smart-flagbox [data-act=\"action\"]'); b.click(); return true; })()" % i)
        self.browser.pump(1.0)
        self.assertEqual(("smart", "#/smart"), (self.js("T.router.current.page"), self.js("location.hash")))
        return act

    def labels(self) -> dict:
        return self.js(LABELS)

    def card_edit(self, i: int, field: str, value: str) -> None:
        self.js("(function () { var x = document.querySelector('#smart-cards [data-i=\"%d\"] [data-edit=\"%s\"]'); x.value = %s;"
                " x.dispatchEvent(new Event('change', { bubbles: true })); return true; })()" % (i, field, json.dumps(value)))
        self.wait("/edited/.test(document.querySelector('#smart-cards [data-i=\"%d\"] .smart-card-head').innerText)" % i, timeout=10)
        self.browser.pump(0.3)

    def test_e_the_fx_desk_is_held_as_open_in_ticket_is_and_opens_once_the_reading_is_clean(self):
        self.tidy()
        before, start = self.deals(), self.browser.mark()
        # unread words: "3 month forward" (afe7aac opened a spot deal, Confirm enabled)
        self.smart("P")
        self.read("buy 10k EURUSD 3 month forward", 1)
        self.held_desk(0, "FinSim could not read the underlined words: change or remove them, then press Enter")
        # words asking for one order: a red flag of the card and of its group (afe7aac opened the FX half alone)
        self.smart("P")
        self.read("(buy 10k EURUSD, buy 100 ACME) as one order", 2)
        self.held_desk(0, "'as one order' asks for one order; the Smart Ticket fills separate tickets that go and fill on their own")
        # a price and a time in force the desk cannot take (afe7aac opened a spot deal at the market, DAY, and said "read as a limit")
        self.smart("P")
        self.read("buy 10k EURUSD at 1.08 gtc", 1)
        terms = [t for t in self.chips(0) if "not carried" in t]
        self.assertEqual(1, len(terms), self.chips(0))
        self.assertIn("the FX desk of P1:FOREIGN_FX opens a new trade from the side, the amount and the account only, so 'at 1.08' and 'gtc' are not"
                      " carried", terms[0])
        act = self.held_desk(0, "the FX desk of P1:FOREIGN_FX opens a new trade from the side, the amount and the account only, so 'at 1.08'")
        self.assertIn("'gtc' are not carried", act["title"])
        # its choice: the words removed, the text read again - the desk then opens on the plain deal (the control)
        self.chip(0, "not carried")
        self.js("(function () { var b = Array.prototype.filter.call(document.querySelectorAll('#smart-cards [data-i=\"0\"] .smart-flagbox [data-act=\"choice\"]'),"
                " function (x) { return x.innerText === \"without 'at 1.08' and 'gtc'\"; })[0]; b.click(); return true; })()")
        self.wait("document.querySelector('#smart-text').value === 'buy 10k EURUSD' && !document.querySelector('#smart-parse').disabled"
                  " && /^read /.test(document.querySelector('#smart-status').innerText) && document.querySelectorAll('#smart-cards [data-i]').length === 1"
                  " && !Array.prototype.some.call(document.querySelectorAll('#smart-cards .smart-chip'), function (x) { return /not carried/.test(x.title); })")
        self.chip(0, "currency pair")
        self.assertEqual({"label": "Open the FX desk", "enabled": True, "title": "", "why": ""}, self.js(OWN_ACTION % 0))
        self.js("document.querySelector('#smart-cards [data-i=\"0\"] .smart-flagbox [data-act=\"action\"]').click(); true")
        d = self.desk_ready()
        self.assertEqual(("SPOT", "EUR", "EUR", "USD", "10000"), (d["mode"], d["unit"], d["buy"], d["sell"], d["amount"]), d)
        self.assertTrue(d["deal"].startswith("BUY EUR 10,000.00 / SELL USD"), d)
        # no amount typed: "how many?" does not hold it - the desk opens with its amount box empty and asks for it there
        # (test_terminal_simplify_r4.HandOffPageTests.test_f2 opens it)
        self.smart("P")
        self.read("buy EURUSD", 1)
        self.assertTrue(any(t.startswith("blocks this order: how many?") for t in self.chips(0)), self.chips(0))
        self.chip(0, "currency pair")
        self.assertEqual({"label": "Open the FX desk", "enabled": True, "title": "", "why": ""}, self.js(OWN_ACTION % 0))
        self.assertEqual(([], before), (self.sends(start), self.deals()))

    def test_f_a_groups_sending_order_follows_the_sides_its_cards_hold_now(self):
        self.tidy()
        start = self.browser.mark()
        self.smart("F")
        self.read(SOLD, 2)                                               # typed: the purchase (#2) first
        sold = self.labels()
        self.assertEqual(([("#2", "BTO"), ("#1", "STO")], "#2, then #1"), (_sending(sold), _header_order(sold)), json.dumps(sold))
        self.read(BOUGHT, 2)
        bought = self.labels()
        self.assertEqual(([("#1", "BTO"), ("#2", "STO")], "#1, then #2"), (_sending(bought), _header_order(bought)), json.dumps(bought))
        self.card_edit(0, "side", "STO")                                 # both sides flipped in place: the sold spread's two orders
        self.card_edit(1, "side", "BTO")
        now = self.labels()
        self.assertEqual([("#1", "STO", True), ("#2", "BTO", True)], [(c["num"], c["side"], c["open"]) for c in now["cards"]], json.dumps(now))
        self.assertEqual(([("#2", "BTO"), ("#1", "STO")], "#2, then #1"), (_sending(now), _header_order(now)), json.dumps(now))   # afe7aac: #1 first
        self.assertEqual([], self.sends(start))


if __name__ == "__main__":
    unittest.main()
