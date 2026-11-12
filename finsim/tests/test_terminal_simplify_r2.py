"""FinSim 1.0.7.2 simplify review round 2 (on f153cca): the regression tests of its findings, kept in the terminal suite.

The money path (the owner's rule: a call is never sold as covered unless its shares are free when it is accepted AND stay reserved
until it is closed - a server-side invariant for every order):
  HedgeLegCoverTests  a sale leg of a sequential package counts, as a working sale, what it will sell in all - its share of the
      primary's whole replace chain (``orders_preview.left_in_all``) - so a partial release no longer frees the shares it sells later
      (an ordinary sale of them is refused 400 OVERSELL); an amend or a replace that raises the primary, and the ticket's preview of
      it, is refused when the legs it grows do not fit (``hedge_growth_check``): 400 OVERSELL for a sale leg, 409 SHARES_RESERVED for
      calls released onto the cover of a call sold as covered; a raise that fits is accepted and the leg follows the ratio.
  ClaimsFromFillsTests  the calls sold as covered still open are rebuilt from the account's fills in order (the reductions of
      tests/test_props2_cover_walk.py's seeds 2220301 steps 88 / 208 / 365 / 410 and 2220304 step 436): a call bought back ends its
      claim, a covered sale that closed a long call opens none, a buy-back closes a plain call before a claimed one - a later call sold
      without covered is never "sold as covered".
  ExerciseWhileShortTests  an early exercise of a long call while the account is short the shares is refused 409 UNDERLYING_SHORT
      (the walk's seed 2220304 steps 78 / 112: 500 INTEGRITY before); once the short is covered it exercises.
The reading:
  SellAllOfAShortTests  "sell all" / "sell half" of a short holding is refused ADDS_TO_POSITION with "close them" offered, never a
      short sale offered (the nine instructions of tests/test_props2_parse_oracle.py's seed 2220101, embedded, on its world WA).
  OneOrderAfterACloseTests  words asking for one order hold a close written before them (AFTER_A_CLOSE; the corpus has them too), and
      every instruction of tests/test_props2_autofill_runs.py's seeds 2220101 (600 cases) and 2220102 (1,500 cases) that f153cca read
      with a card of that run free (33 and 95, embedded): no card of the run can be opened or relayed on its own (each has a group, a
      red flag and no request - the unified app's relay offers none of them).
The page (the REAL static/pages/smartticket.js in a headless Edge / Chrome with test_terminal_simplify_r1's stand-in for window.T):
  SmartPageNotesTests  a card's notes (an edit a reading dropped on it, its "was" marks) stay in the draft across a navigation and
      through the next readings that read the card as before, until the owner edits that field or opens the card; the note above the
      cards stays through the next reading of the same text.
The order ticket's covered line, the Strategies tab hand-off and the narrow widths are in test_terminal_browser_smoke.py (test_xf,
test_xg, test_xh).  Fictional ACME / WIDG / GIZM only; temp homes; no ~/.finsim, no port.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import unittest

from tests import terminal_smart_corpus as corpus
from tests import test_terminal_simplify_r1 as R1
from tests import test_terminal_smart_ticket as STT
from tests.terminal_cdp import NO_BROWSER, Browser, find_browser
from tests.terminal_helpers import _Log, assert_integrity
from tests.test_terminal_smart_submit import (ACCT, AT, E2, ROWS, SmartSubmitBase, acme, acme_chain, add_name, build, leg, load_chain,
                                               print_acme)
from finsim.terminal import config, server as _server, smart_ticket as st
from finsim.terminal.clock import FakeClock
from finsim.terminal.orders_preview import cover_state
from finsim.terminal.quotes import Bar1m, QuoteRecord
from finsim.terminal.quotes.providers import ChainRow, ChainSnapshot, ChainUnderlying, FakeProvider
from finsim.terminal.units import qprice

E1 = "2026-12-18"
C100, C110, C105J = acme("C", 100), acme("C", 110), acme("C", 105, E2)
C105, C115, P100 = acme("C", 105, E1), acme("C", 115, E1), acme("P", 100, E1)


# ==== the money path: hedge legs ======================================================================================================
def delta_chain(now: int, c100=("4.80", "5.00"), c100_ask_size: int = 20) -> ChainSnapshot:
    """The suite's invented ACME chain with a delta of 0.50 on the Dec 100 call (the Hedges tab's DELTA_HEDGE needs one) and that call's
    bid / ask and displayed ask size as given; observed one minute ago, fetched now."""
    qt = now - 60
    rows = []
    for e, r, k, b, a in ROWS:
        occ = acme(r, k, e)
        if occ == C100:
            rows.append(ChainRow(occ, e, r, qprice(k), qprice(c100[0]), qprice(c100[1]), 20, c100_ask_size, None, None, 0.30, 0.50, None, None, None, 100, 10))
        else:
            rows.append(ChainRow(occ, e, r, qprice(k), qprice(b), qprice(a), 20, 20, None, None, 0.30, None, None, None, None, 100, 10))
    return ChainSnapshot("ACME", "ACME", ChainUnderlying("ACME", qprice("100.00"), None, None, None, None, qt, None, 0.30), rows, now, qt, len(rows))


def print_widg(rt, last: str, age: int, bar_t: int, vol: int) -> None:
    """A Yahoo print of WIDG observed ``age`` seconds ago on the completed 1-minute bar starting at ``bar_t`` (``vol`` shares)."""
    now = rt.clock.epoch()
    px = qprice(last)
    bar = Bar1m(int(bar_t), px, px, px, px, int(vol))
    rt.cache.put(QuoteRecord(instrument_id="WIDG", provider="yahoo", source="Yahoo Finance chart (WIDG)", method="direct", last=px, quote_time=now - age,
                             fetched_at=now, delay_s=900, delay_label="up to 15 min delayed", currency="USD", session_group="US_EQUITY", bar=bar,
                             bars_tail=[bar]), now)


class _HedgeLegBase(SmartSubmitBase):
    """test_terminal_smart_submit's Test Book T1 (ACME at 100.00 on a 50,000-share bar, its invented chain with a delta of 0.50 on the Dec
    18 2026 100 call, WIDG at 50.00), driven through the docked ticket's preview then order, its amend and replace and the matcher."""

    def setUp(self):
        super().setUp()
        self.k = 0
        load_chain(self.rt, delta_chain(self.now))
        print_widg(self.rt, "50.00", 120, self.now - 180, 50000)

    def rid(self, what: str) -> str:
        self.k += 1
        return f"ui:s2-{what}-{self.k:04d}"

    def row(self, oid: str) -> dict:
        return self.q("SELECT * FROM orders WHERE order_id=?", (oid,))[0]

    def positions(self) -> dict:
        return {r["instrument_id"]: int(r["qty"]) for r in self.q("SELECT instrument_id, qty FROM v_positions WHERE account_id=? AND qty != 0", (ACCT,))}

    def order(self, iid: str, side: str, qty: int, **kw) -> tuple:
        """An ordinary order from the docked ticket (its preview, then the order with the preview's id): (http, status or code)."""
        req = dict({"account_id": ACCT, "instrument_id": iid, "side": side, "qty": qty, "order_type": "MARKET", "tif": "DAY"}, **kw)
        code, out = self.ticket(req, self.rid(side.lower()))
        return code, ((out.get("order") or {}).get("status") if code == 200 else (out.get("error") or {}).get("code"))

    def change(self, oid: str, how: str, body: dict) -> tuple:
        """The docked ticket's amend (``amend_of``, then PATCH) or re-price by replace (``replace_of``, then POST .../replace), each after
        its preview and with the preview's id: (the preview's blocker codes, http, the answer's error code or None)."""
        r = self.row(oid)
        pv_req = {"account_id": r["account_id"], "instrument_id": r["instrument_id"], "side": r["side"], "order_type": r["order_type"],
                  "limit_px": r["limit_px"], "tif": r["tif"], ("amend_of" if how == "amend" else "replace_of"): oid}
        pv_req.update(body)
        code, pv, _h = self.call("POST", "/api/orders/preview?book=T", pv_req)
        self.assertEqual(200, code, pv)
        body = dict(body, preview_id=pv.get("preview_id"))
        if how == "amend":
            code, out, _h = self.call("PATCH", f"/api/orders/{oid}?book=T", body, self.rid("amend"))
        else:
            code, out, _h = self.call("POST", f"/api/orders/{oid}/replace?book=T", body, self.rid("replace"))
        return [b["code"] for b in pv.get("blockers") or []], code, (out.get("error") or {}).get("code")

    def c100_obs(self, bid: str, ask: str, ask_size: int) -> None:
        """The next chain observation with the Dec 100 call at ``bid`` / ``ask`` (``ask_size`` displayed), then a matcher pass on it."""
        self.rt.clock.advance(30)
        load_chain(self.rt, delta_chain(self.rt.clock.epoch(), (bid, ask), ask_size))
        self.rt.matcher.on_quotes([C100])

    def option_pass(self) -> None:
        """The next chain observation (the invented prices): working ACME calls sold to open fill at the bid."""
        self.rt.clock.advance(30)
        load_chain(self.rt, delta_chain(self.rt.clock.epoch()))
        self.rt.matcher.on_quotes([C110, C105J])

    def acme_bar(self) -> None:
        """The next completed 1-minute ACME bar at 100.00 on 50,000 shares (10 % of it can fill), then a matcher pass."""
        self.rt.clock.advance(60)
        print_acme(self.rt, "100.00", 10, self.rt.clock.epoch() - 60, 50000)
        self.rt.matcher.on_quotes(["ACME"])

    def widg_bar(self, last: str) -> None:
        self.rt.clock.advance(60)
        print_widg(self.rt, last, 10, self.rt.clock.epoch() - 60, 50000)
        self.rt.matcher.on_quotes(["WIDG"])

    def uncovered(self) -> int:
        """The ACME the account's short ACME calls need beyond the ACME it holds (0: the shares cover every short call)."""
        pos = self.positions()
        short = -sum(q for iid, q in pos.items() if iid.startswith("ACME2") and iid[10] == "C" and q < 0)
        return max(0, 100 * short - max(0, pos.get("ACME", 0)))

    def cover(self) -> dict:
        with self.rt.db.reader() as conn:
            return cover_state(self.rt, conn, ACCT, "ACME", claims=True)

    def margin_rule(self, occ: str):
        """The margin engine's rule for the short call ``occ`` (covered_call / naked_call ...), from the stored margin detail."""
        rows = self.q("SELECT options_detail_json FROM margin_state WHERE account_id=?", (ACCT,))
        d = json.loads(rows[0]["options_detail_json"]) if rows else {}
        return next((it["rule"] for g in d.get("groups") or [] for it in g.get("items") or [] if it["instrument_id"] == occ), None)

    def delta_package(self, contracts: int = 2) -> tuple:
        """BTO ``contracts`` ACME Dec 100 calls limit 4.00 (rests: ask 5.00) with the Hedges tab's DELTA_HEDGE pick, after the ticket's
        preview: (the primary's row, its leg's row: SELL 50 ACME per contract, waiting)."""
        primary = {"account_id": ACCT, "instrument_id": C100, "side": "BTO", "qty": contracts, "order_type": "LIMIT", "limit_px": "4.00", "tif": "DAY"}
        code, out, p, legs = self.seq_package(primary, {"candidate_id": "DELTA_HEDGE"}, rid=self.rid("delta"))
        self.assertEqual(200, code, out)
        self.assertEqual(1, len(legs), legs)
        h = legs[0]
        self.assertEqual(("WORKING", "WAITING_PARENT", "SELL", "ACME", 50 * contracts), (p["status"], h["status"], h["side"], h["instrument_id"], int(h["qty"])))
        return p, h

    def covered_on_the_leg(self, held: int = 300) -> tuple:
        """``held`` ACME, the delta package of 2 calls (its leg will sell 100) and 2 Jan 105 calls sold as covered on the rest."""
        self.assertEqual((200, "FILLED"), self.order("ACME", "BUY", held))
        p, h = self.delta_package(2)
        self.assertEqual((200, "FILLED"), self.order(C105J, "STO", 2, covered=True))
        self.assertEqual((400, "OVERSELL"), self.order("ACME", "SELL", 1))          # no ACME is free
        return p, h


class HedgeLegCoverTests(_HedgeLegBase):
    def test_a_a_sale_leg_counts_what_it_sells_after_a_partial_release(self):
        """1 of the 2 calls bought: the leg is released for 50 and sells them (FILLED 50 of 50); it will sell 50 more when the second
        contract fills, so those 50 are not free - an ordinary sale of them is refused (f153cca accepted it, and the leg then sold the
        cover of a call sold as covered)."""
        p, h = self.covered_on_the_leg()
        self.c100_obs("3.90", "4.00", 1)
        self.acme_bar()
        self.assertEqual(("FILLED", 50), (self.row(h["order_id"])["status"], int(self.row(h["order_id"])["qty"])))
        cov = self.cover()
        self.assertEqual((250, 50, 200, 0), (cov["held"], cov["working_sells"], cov["reserved_sold"], cov["free"]), cov)
        self.assertEqual((400, "OVERSELL"), self.order("ACME", "SELL", 50))
        self.c100_obs("3.90", "4.00", 1)                                             # the second contract: the leg re-opened, 50 more sold
        self.acme_bar()
        self.assertEqual(({"ACME": 200, C100: 2, C105J: -2}, 0), (self.positions(), self.uncovered()))
        assert_integrity(self, self.rt, "full")

    def test_b_an_amend_raising_the_primary_is_refused_when_its_sale_leg_would_sell_reserved_shares(self):
        p, h = self.covered_on_the_leg()
        self.assertEqual((["OVERSELL"], 400, "OVERSELL"), self.change(p["order_id"], "amend", {"qty": 4}))   # 2 -> 4: the leg 100 -> 200
        self.assertEqual(2, int(self.row(p["order_id"])["qty"]))
        self.c100_obs("3.90", "4.00", 20)
        self.acme_bar()
        self.assertEqual(({"ACME": 200, C100: 2, C105J: -2}, 0), (self.positions(), self.uncovered()))
        assert_integrity(self, self.rt, "full")

    def test_b2_a_replace_raising_the_primary_is_refused_the_same_way(self):
        p, h = self.covered_on_the_leg()
        self.assertEqual((["OVERSELL"], 400, "OVERSELL"),
                         self.change(p["order_id"], "replace", {"qty": 4, "order_type": "LIMIT", "limit_px": "4.00"}))
        self.assertEqual("WORKING", self.row(p["order_id"])["status"])
        self.c100_obs("3.90", "4.00", 20)
        self.acme_bar()
        self.assertEqual(({"ACME": 200, C100: 2, C105J: -2}, 0), (self.positions(), self.uncovered()))
        assert_integrity(self, self.rt, "full")

    def test_c_an_amend_releasing_more_calls_onto_the_cover_of_a_call_sold_as_covered_is_refused(self):
        """200 ACME; BUY 100 WIDG with its own leg STO 1 ACME Dec 110 call (waiting); a Jan 105 call sold as covered on the 100 left:
        raising the WIDG purchase to 200 would release a second Dec call onto the covered call's shares - 409 SHARES_RESERVED, as a
        further plain call is refused."""
        self.assertEqual((200, "FILLED"), self.order("ACME", "BUY", 200))
        primary = {"account_id": ACCT, "instrument_id": "WIDG", "side": "BUY", "qty": 100, "order_type": "LIMIT", "limit_px": "49.00", "tif": "DAY"}
        code, out, p, legs = self.seq_package(primary, [leg(C110, "STO", 1)], rid=self.rid("seq"))
        self.assertEqual((200, "WORKING", ["WAITING_PARENT"]), (code, p and p["status"], [x["status"] for x in legs]), out)
        self.assertEqual((200, "FILLED"), self.order(C105J, "STO", 1, covered=True))
        self.assertEqual((409, "SHARES_RESERVED"), self.order(C110, "STO", 1))
        self.assertEqual((["SHARES_RESERVED"], 409, "SHARES_RESERVED"), self.change(p["order_id"], "amend", {"qty": 200}))
        self.widg_bar("48.90")
        self.option_pass()
        self.assertEqual((0, "covered_call"), (self.uncovered(), self.margin_rule(C105J)), self.positions())
        assert_integrity(self, self.rt, "full")

    def test_d_controls_a_raise_that_fits_is_accepted_and_the_leg_follows_the_ratio(self):
        # without the amend every call stays covered
        p, h = self.covered_on_the_leg()
        self.c100_obs("3.90", "4.00", 20)
        self.acme_bar()
        self.assertEqual(({"ACME": 200, C100: 2, C105J: -2}, 0), (self.positions(), self.uncovered()))
        self.assertEqual((400, "OVERSELL"), self.order("ACME", "SELL", 1))
        # 300 more ACME and a second package with no claim on its shares: raising it 2 -> 4 fits; its leg then sells 200
        self.assertEqual((200, "FILLED"), self.order("ACME", "BUY", 300))
        self.c100_obs("4.80", "5.00", 20)                                            # the call's ask back at 5.00: a limit of 4.00 rests
        p2, h2 = self.delta_package(2)
        self.assertEqual(([], 200, None), self.change(p2["order_id"], "amend", {"qty": 4}))
        self.c100_obs("3.90", "4.00", 20)
        self.acme_bar()
        self.assertEqual(("FILLED", 200), (self.row(h2["order_id"])["status"], int(self.row(h2["order_id"])["qty"])))
        self.assertEqual(({"ACME": 300, C100: 6, C105J: -2}, 0), (self.positions(), self.uncovered()))
        assert_integrity(self, self.rt, "full")


# ==== the money path: claims, exercise ===============================================================================================
class _World(unittest.TestCase):
    """test_terminal_smart_submit's Test Book T1 with ACME at 100.00 and its invented chain; orders straight to the order route."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-s2-")
        self.rt = build(self.tmp)
        with self.rt.db.writer() as tx:
            self.rt.instruments.upsert(tx, instrument_id="ACME", kind="EQUITY", symbol="ACME", name="ACME test listing", currency="USD", session_group="US_EQUITY",
                                       account_kinds=["US_EXCHANGE", "PB"], yahoo_symbol="ACME", cboe_symbol="ACME", spec={"exchange": "NYQ", "adv": 5000000})
        now = self.rt.clock.epoch()
        print_acme(self.rt, "100.00", 120, now - 180, 50000)
        load_chain(self.rt, acme_chain(now, size=50))
        self.n = 0

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, method: str, path: str, body: dict) -> tuple:
        self.n += 1
        code, out, _h = self.rt.router.handle(method, path, None, dict(body, request_id=f"ui:s2-w-{self.n:04d}"), "127.0.0.1", None)
        self.rt.matcher.on_quotes(["ACME"] + [acme(r, k, e) for e, r, k, _b, _a in ROWS])
        self.rt.matcher.sweep()
        return code, out

    def order(self, **b) -> tuple:
        code, out = self.call("POST", "/api/orders?book=T", dict({"account_id": ACCT, "tif": "DAY", "order_type": "MARKET"}, **b))
        return code, ((out.get("order") or {}).get("status") if code == 200 else (out.get("error") or {}).get("code"))

    def filled(self, **b) -> None:
        self.assertEqual((200, "FILLED"), self.order(**b), b)

    def claims(self) -> list:
        with self.rt.db.reader() as conn:
            return cover_state(self.rt, conn, ACCT, "ACME", claims=True)["claimed"]

    def positions(self) -> dict:
        with self.rt.db.reader() as conn:
            return {r["instrument_id"]: int(r["qty"]) for r in conn.execute("SELECT instrument_id, qty FROM v_positions WHERE account_id=?", (ACCT,)) if int(r["qty"])}

    def assert_plain_call_unclaimed(self) -> None:
        self.assertEqual({"ACME": 50, C105: -1}, self.positions())
        self.assertEqual([], self.claims())
        self.assertNotEqual("SHARES_RESERVED", self.order(instrument_id=C115, side="STO", qty=1)[1])


class ClaimsFromFillsTests(_World):
    def test_a_a_call_sold_as_covered_and_bought_back_leaves_no_claim_on_a_later_plain_call(self):
        """Walk seed 2220301 steps 88 / 208 / 365 / 410 (property C)."""
        self.filled(instrument_id="ACME", side="BUY", qty=100)
        self.filled(instrument_id=C105, side="STO", qty=1, covered=True)
        self.assertEqual([{"instrument_id": C105, "order_id": None, "contracts": 1}], self.claims())
        self.filled(instrument_id=C105, side="BTC", qty=1)
        self.assertEqual([], self.claims())
        self.filled(instrument_id="ACME", side="SELL", qty=50)
        self.filled(instrument_id=C105, side="STO", qty=1)                      # a plain call: 50 shares held
        self.assert_plain_call_unclaimed()

    def test_b_a_covered_sale_that_closes_a_long_call_opens_no_claim(self):
        """Walk seed 2220304 step 436 (property C)."""
        self.filled(instrument_id="ACME", side="BUY", qty=200)
        self.filled(instrument_id=C105, side="BTO", qty=2)
        self.filled(instrument_id=C105, side="STO", qty=1, covered=True)        # closes one of the 2 long calls, opens no short
        self.assertEqual([], self.claims())
        self.filled(instrument_id="ACME", side="SELL", qty=150)
        self.filled(instrument_id=C105, side="STC", qty=1)
        self.filled(instrument_id=C105, side="STO", qty=1)                      # a plain call
        self.assert_plain_call_unclaimed()

    def test_c_a_buy_back_closes_a_plain_call_before_a_claimed_one(self):
        self.filled(instrument_id="ACME", side="BUY", qty=200)
        self.filled(instrument_id=C105, side="STO", qty=1, covered=True)
        self.filled(instrument_id=C105, side="STO", qty=1)                      # plain, on the 100 shares left free
        self.assertEqual([{"instrument_id": C105, "order_id": None, "contracts": 1}], self.claims())
        self.filled(instrument_id=C105, side="BTC", qty=1)                      # one of the two bought back: the plain one
        self.assertEqual(({"ACME": 200, C105: -1}, [{"instrument_id": C105, "order_id": None, "contracts": 1}]), (self.positions(), self.claims()))
        self.filled(instrument_id=C105, side="BTC", qty=1)
        self.assertEqual([], self.claims())


class ExerciseWhileShortTests(_World):
    def exercise(self, occ: str) -> tuple:
        code, out = self.call("POST", "/api/options/exercise?book=T", {"account_id": ACCT, "instrument_id": occ, "qty": 1})
        return code, ((out.get("error") or {}).get("code") if code != 200 else None)

    def test_d_an_early_call_exercise_while_short_the_shares_is_refused_until_the_short_is_covered(self):
        """Walk seed 2220304 steps 78 / 112 (P4: 500 INTEGRITY "open borrow backs nothing" on f153cca)."""
        self.filled(instrument_id="ACME", side="SELL_SHORT", qty=100, borrow=True)
        self.filled(instrument_id=C105, side="BTO", qty=1)
        self.assertEqual((409, "UNDERLYING_SHORT"), self.exercise(C105))
        self.assertEqual({"ACME": -100, C105: 1}, self.positions())
        assert_integrity(self, self.rt, "full")
        self.filled(instrument_id="ACME", side="BUY_TO_COVER", qty=100)
        self.assertEqual((200, None), self.exercise(C105))
        self.assertEqual({"ACME": 100}, self.positions())
        assert_integrity(self, self.rt, "full")

    def test_e_control_an_early_put_exercise_while_short_the_shares_delivers_them_short_with_their_own_borrow(self):
        self.filled(instrument_id="ACME", side="SELL_SHORT", qty=100, borrow=True)
        self.filled(instrument_id=P100, side="BTO", qty=1)
        self.assertEqual((200, None), self.exercise(P100))
        self.assertEqual({"ACME": -200}, self.positions())
        with self.rt.db.reader() as conn:
            self.assertEqual(200, int(conn.execute("SELECT SUM(qty - qty_returned) FROM borrows WHERE account_id=? AND instrument_id='ACME' AND status='OPEN'",
                                                   (ACCT,)).fetchone()[0]))
        assert_integrity(self, self.rt, "full")


# ==== the reading ======================================================================================================================
#: tests/test_props2_parse_oracle.py's world WA (its SETUP, through the order route): the Personal book holds 300 ACME, long 2 Dec 100
#: calls and short 1 Dec 110; the Fund book holds 200 ACME and is short 1 Jan 105 call; the Institutional book is short 200 ACME
WA_SETUP = [("P", "ACME", "BUY", 300, {}), ("P", acme("C", 100, E1), "BTO", 2, {}), ("P", acme("C", 110, E1), "STO", 1, {}), ("F", "ACME", "BUY", 200, {}),
            ("F", acme("C", 105, E2), "STO", 1, {}), ("I", "ACME", "SELL_SHORT", 200, {"borrow": True})]
#: the parse oracle's seed 2220101 cases 92 / 158 / 1236 / 1528 / 1574 / 1674 / 1860 / 1946 / 2120 (read for the Personal book)
SELL_ALL_OF_A_SHORT = [
    "buy all ACME then sell half ACME in the Institutional book",
    "cover 100 WIDG limit 99.50;sell all ACME\ncollar 300 ACME Dec 18 2026 90/105 in the Institutional book",
    "sell half ACME\nbuy 150 ACME in the Institutional book",
    "in the Institutional book: sell half ACME in the Institutional book then buy half ACME in the Fund book",
    "in the Personal book: sell 1 ACME Dec 18/Jan 15 2027 105 call calendar\nsell all ACME in the Institutional book",
    "in the Personal book: sell all ACME in the Institutional book\nbuy 150 WIDG",
    "in the Institutional book: roll 1 ACME Dec 18 2026 115 call to Jan 15 2027 105; sell all ACME",
    "in the Institutional book: roll 1 ACME 2027-01-15 105 call to Dec 18 2026 95 in the Fund book\nsell all ACME",
    "sell all ACME in the Institutional book",
]


class SellAllOfAShortTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="fst-s2-wa-")
        cls.env = {k: os.environ.get(k) for k in (config.HOME_ENV, config.DB_ENV)}
        os.environ[config.HOME_ENV] = cls.tmp
        os.environ[config.DB_ENV] = os.path.join(cls.tmp, "terminal.db")
        clock = FakeClock(AT)
        cls.rt = _server.build_runtime(cls.tmp, os.path.join(cls.tmp, "terminal.db"), clock=clock, offline=True, fake_provider=FakeProvider(clock), log=_Log(),
                                       books=list(config.BOOKS), fetch_threads=0, news_enabled=False)
        try:
            rt = cls.rt
            add_name(rt, "WIDG")
            with rt.db.writer() as tx:
                rt.instruments.upsert(tx, instrument_id="ACME", kind="EQUITY", symbol="ACME", name="ACME test listing", currency="USD", session_group="US_EQUITY",
                                      account_kinds=["US_EXCHANGE", "PB"], yahoo_symbol="ACME", cboe_symbol="ACME", spec={"exchange": "NYQ", "adv": 5000000})
            now = rt.clock.epoch()
            print_acme(rt, "100.00", 120, now - 180, 50000)
            load_chain(rt, acme_chain(now, size=50))
            ids = {"ACME"}
            for i, (bk, iid, side, qty, extra) in enumerate(WA_SETUP):
                body = dict({"account_id": f"{bk}1:US_EXCHANGE", "instrument_id": iid, "side": side, "qty": qty, "order_type": "MARKET", "tif": "DAY",
                             "request_id": f"ui:s2-wa-{i:03d}"}, **extra)
                code, out, _h = rt.router.handle("POST", f"/api/orders?book={bk}", None, body, "127.0.0.1", None)
                assert code == 200, (body, out)
                ids.add(iid)
            rt.matcher.on_quotes(sorted(ids))
            rt.matcher.sweep()
        except Exception:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            cls.rt.close()
        finally:
            for k, v in cls.env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_c_sell_all_or_half_of_a_short_is_refused_with_close_offered_never_a_short_sale(self):
        with self.rt.db.reader() as conn:
            self.assertEqual(-200, int(conn.execute("SELECT qty FROM v_positions WHERE account_id='I1:US_EXCHANGE' AND instrument_id='ACME'").fetchone()[0]))
        for text in SELL_ALL_OF_A_SHORT:
            out = st.parse_view(self.rt, {"text": text, "book": "P"})
            mine = [l for l in out["legs"] if l["account_id"] == "I1:US_EXCHANGE" and (l["instrument"] or {}).get("id") == "ACME"
                    and re.search(r"\bsell (all|half)\b", l["clause"] or "")]
            self.assertTrue(mine, (text, [(l["account_id"], l["clause"]) for l in out["legs"]]))
            for leg_ in mine:
                blockers = [f["code"] for f in leg_["flags"] if f["severity"] == "blocker"]
                clauses = [ch["clause"] for f in leg_["flags"] for ch in f.get("choices") or []]
                self.assertIsNone(leg_["request"], text)
                self.assertIn("ADDS_TO_POSITION", blockers, (text, [(f["code"], f["text"]) for f in leg_["flags"]]))
                self.assertNotIn("WOULD_OVERSELL", blockers, text)
                self.assertFalse([c for c in clauses if re.search(r"\bshort\b", c)], (text, clauses))
                self.assertTrue([c for c in clauses if re.search(r"\bclose\b", c)], (text, clauses))


#: words asking for one order after a close (the corpus's ref-one-order-after-close-*), and the controls that held already
AFTER_A_CLOSE = ["close WIDG and buy 100 ACME as a package", "close ACME calls and buy 100 WIDG together", "close GIZM and buy 100 ACME aon",
                 "close WIDG and sell 1 ACME Dec 18 2026 260 call as one order", "sell 100 WIDG, close ACME calls, buy 100 ACME all or none"]
CONTROLS = ["buy 100 ACME and close WIDG as one order", "(close WIDG and buy 100 ACME) as a package", "sell 100 WIDG and buy 100 ACME as a package",
            "sell 100 WIDG and close ACME calls together"]


#: tests/test_props2_autofill_runs.py's instructions that f153cca read with a card of the run its words asking for one order end free:
#: (text, book, the clause the run starts at) - seed 2220101 (600 cases: 33) and seed 2220102 (1,500 cases: 95)
AUTOFILL_2220101 = [
    ('bto 2 ACME Jan 15 2027 250 calls and sto 2 ACME Dec 18 2026 260 calls, close half WIDG limit 41 and roll 2 ACME Dec 18 2026 240 calls to Jan 15 2027 250 together', 'F', 0),
    ('covered call 200 ACME Dec 18 2026 250 gtc then close ACME limit 41 gtc then buy 300 GIZM day as a package', 'P', 0),
    ('cover 200 GIZM trail 2%, close ACME and cover 200 WIDG stop 38 as one', 'F', 0),
    ('bto 2 ACME Jan 15 2027 250 call, close ACME limit 41; sell 200 GIZM trail 2% as one order', 'I', 0),
    ('buy 1 ACME Dec 18 2026 240/250 call spread day then sell 200 GIZM limit 41 day and close WIDG limit 41 and sto 3 ACME Jan 15 2027 230 call as one', 'F', 0),
    ('cover all GIZM limit 41; close WIDG limit 41 and close ACME gtc and sell 200 GIZM stop 38 day aon', 'P', 0),
    ('close half WIDG then cover half ACME at 225 as one', 'P', 0),
    ('roll 3 ACME Dec 18 2026 250 calls to Jan 15 2027 260 then sell 100 ACME day; close GIZM and sell 1 ACME Dec 18 2026 230/240 put spread as a combo', 'F', 0),
    ('close ACME calls limit 41 day and sell 3 ACME Dec 18 2026 260 call as a package', 'I', 0),
    ('buy write 100 ACME Dec 18 2026 260 then close half WIDG day, roll 2 ACME Dec 18 2026 240 calls to Jan 15 2027 250 then close ACME day aon', 'P', 0),
    ('close WIDG and buy 1 ACME Dec 18 2026 240 straddle as one order', 'P', 0),
    ('close ACME calls day and buy 1 ACME Jan 15 2027 240 calls together', 'P', 0),
    ('buy 2 ACME Jan 15 2027 240 calls day then close GIZM limit 41 gtc then bto 1 ACME Dec 18 2026 260 call limit 2.10 as one', 'P', 0),
    ('close ACME calls then covered call 200 ACME Dec 18 2026 250 gtc as one order', 'P', 0),
    ('close ACME, bto 3 ACME Jan 15 2027 240 puts; buy 1 ACME Jan 15 2027 250 call day, buy half WIDG at 225 gtc as one order', 'F', 0),
    ('close GIZM gtc; stc 3 ACME Jan 15 2027 240 puts and buy 1 ACME Dec 18 2026 240 straddle gtc as one order', 'P', 0),
    ('close half WIDG gtc and buy write 100 ACME Dec 18 2026 260 gtc as one', 'P', 0),
    ('close ACME limit 41 day, buy 100 ACME trail 2% gtc as one', 'F', 0),
    ('short 300 WIDG day then close GIZM limit 41 then collar 100 ACME Dec 18 2026 220/260 gtc as a package', 'P', 0),
    ('close GIZM gtc, short 300 ACME trail 2% gtc, roll 2 ACME Dec 18 2026 240 calls to Jan 15 2027 250, short half WIDG as a package', 'I', 0),
    ('short all WIDG stop 38; sell 2 ACME Jan 15 2027 240 call day, close half WIDG gtc and close ACME calls gtc all or none', 'P', 0),
    ('stc 1 ACME Dec 18 2026 250 calls day; close GIZM gtc and buy 1 ACME Dec 18 2026 240/250 call spread and sell 2 ACME Dec 18 2026 260 calls limit 2.10 as a combo', 'P', 0),
    ('stc 2 ACME Dec 18 2026 260 calls day and close half WIDG day; close WIDG together', 'I', 0),
    ('close WIDG limit 41 then cover 200 ACME at 225 day as one', 'P', 0),
    ('sell 100 WIDG trail 2%; close ACME puts gtc; sell half WIDG stop 38 day together', 'P', 0),
    ('sto 3 ACME Jan 15 2027 230 puts gtc and close ACME calls gtc then close ACME calls and sell 1 ACME Dec 18 2026 220/230/250/260 iron condor as a package', 'P', 0),
    ('close half WIDG gtc; close ACME calls limit 41 gtc then covered call 100 ACME Dec 18 2026 260 and cover all WIDG aon', 'P', 0),
    ('close GIZM limit 41; bto 2 ACME Jan 15 2027 250 calls; sell 300 ACME as a package', 'I', 0),
    ('buy 300 WIDG trail 2% day then close GIZM and sell 300 GIZM as a combo', 'P', 0),
    ('btc 3 ACME Jan 15 2027 260 call then close WIDG day and covered call 200 ACME Dec 18 2026 250 gtc as a package', 'P', 0),
    ('close half WIDG limit 41; buy 1 ACME Dec 18 2026 240 straddle gtc as a combo', 'P', 0),
    ('buy 1 ACME Dec 18 2026 250 calls limit 2.10, close GIZM, buy half WIDG stop 38 day as one', 'F', 0),
    ('buy all WIDG stop 38 day and roll 3 ACME Dec 18 2026 250 calls to Jan 15 2027 260; close GIZM limit 41 day then sell half GIZM at 225 day all or none', 'P', 0),
]
AUTOFILL_2220102 = [
    ('close GIZM gtc then buy 1 ACME Dec 18 2026 240/250 call spread as one order', 'P', 0),
    ('close ACME calls gtc and sell 1 ACME Dec 18 2026 230 calls all or none', 'P', 0),
    ('close GIZM, sell all ACME all or none', 'I', 0),
    ('sell 1 ACME Dec 18 2026 220/230/250/260 iron condor then close half WIDG then roll 2 ACME Dec 18 2026 240 calls to Jan 15 2027 250 and close ACME puts limit 41 day as one', 'I', 0),
    ('close half WIDG limit 41; sell 1 ACME Dec 18 2026 230/240 put spread as one', 'P', 0),
    ('(sell 1 ACME Jan 15 2027 250 calls) and close GIZM limit 41 gtc and buy 1 ACME Dec 18 2026 240/250 call spread gtc all or none', 'I', 1),
    ('close WIDG gtc then sto 1 ACME Jan 15 2027 240 calls gtc all or none', 'P', 0),
    ('close ACME day; cover 300 GIZM at 225 and buy 300 WIDG trail 2% gtc then sell 100 ACME gtc as a combo', 'F', 0),
    ('sell 3 ACME Jan 15 2027 260 calls limit 2.10 day then close ACME puts then close WIDG as one order', 'F', 0),
    ('buy half WIDG; close GIZM limit 41, buy 300 ACME and buy write 100 ACME Dec 18 2026 260 all or none', 'I', 0),
    ('close ACME calls and sell half WIDG trail 2% gtc and close WIDG all or none', 'P', 0),
    ('sto 2 ACME Jan 15 2027 240 calls and close ACME calls day, close ACME puts aon', 'I', 0),
    ('bto 2 ACME Jan 15 2027 260 call; close ACME calls limit 41 gtc then sto 2 ACME Jan 15 2027 250 puts day together', 'F', 0),
    ('buy 1 ACME Dec 18 2026 240/250 call spread gtc; close ACME calls gtc and roll 2 ACME Dec 18 2026 240 calls to Jan 15 2027 250 as one', 'I', 0),
    ('close GIZM; close GIZM all or none', 'P', 0),
    ('sell 1 ACME Dec 18 2026 220/230/250/260 iron condor gtc then close ACME and close half WIDG limit 41 gtc aon', 'P', 0),
    ('covered call 200 ACME Dec 18 2026 250 day and close ACME; buy all GIZM; buy half WIDG trail 2% day aon', 'F', 0),
    ('close half WIDG and sell 1 ACME Jan 15 2027 230 call limit 2.10 day together', 'P', 0),
    ('buy 3 ACME Jan 15 2027 250 puts and sell 3 ACME Jan 15 2027 260 calls day, close half WIDG limit 41 day; close ACME puts aon', 'P', 0),
    ('sto 3 ACME Dec 18 2026 260 puts, close ACME puts limit 41, cover all WIDG then covered call 100 ACME Dec 18 2026 260 together', 'P', 0),
    ('close GIZM limit 41 then sell all GIZM at 225 gtc; close ACME calls limit 41 as a package', 'I', 0),
    ('close half WIDG then close GIZM gtc; roll 2 ACME Dec 18 2026 240 calls to Jan 15 2027 250 as one order', 'P', 0),
    ('short 300 ACME, close half WIDG gtc and sell 3 ACME Dec 18 2026 260 calls all or none', 'P', 0),
    ('close ACME calls gtc; bto 3 ACME Dec 18 2026 240 puts gtc together', 'P', 0),
    ('(buy all WIDG stop 38) then close GIZM gtc then roll 3 ACME Dec 18 2026 250 calls to Jan 15 2027 260 all or none', 'I', 1),
    ('close ACME gtc and (buy 1 ACME Dec 18 2026 240 calls); close GIZM then covered call 200 ACME Dec 18 2026 250 aon', 'P', 2),
    ('cover all ACME gtc then close half WIDG limit 41 gtc then close ACME; close WIDG limit 41 gtc aon', 'F', 0),
    ('btc 3 ACME Dec 18 2026 230 calls limit 2.10 then close ACME puts day then buy 1 ACME Dec 18 2026 240/250 call spread gtc as a package', 'I', 0),
    ('sell 3 ACME Jan 15 2027 260 calls and close ACME puts limit 41 then sell 1 ACME Dec 18 2026 230/240 put spread day all or none', 'P', 0),
    ('close GIZM day and buy 200 ACME trail 2% gtc together', 'I', 0),
    ('(sto 1 ACME Dec 18 2026 260 calls gtc) then close GIZM gtc then buy 1 ACME Jan 15 2027 260 call limit 2.10 gtc together', 'P', 1),
    ('close GIZM limit 41 and sell 300 ACME stop 38 day and sell 300 WIDG together', 'P', 0),
    ('close GIZM limit 41 then buy all ACME limit 41 day as a combo', 'I', 0),
    ('buy 1 ACME Dec 18 2026 240 straddle, sell 1 ACME Dec 18 2026 230/240 put spread; close WIDG, collar 100 ACME Dec 18 2026 220/260 gtc as one', 'I', 0),
    ('close WIDG day, sto 2 ACME Jan 15 2027 230 call limit 2.10 day and close ACME limit 41 gtc then roll 2 ACME Dec 18 2026 240 calls to Jan 15 2027 250 as one order', 'P', 0),
    ('stc 2 ACME Jan 15 2027 250 call; close ACME gtc and close ACME as one order', 'F', 0),
    ('buy all ACME at 225 gtc; sell 1 ACME Dec 18 2026 230/240 put spread then close WIDG gtc then btc 2 ACME Jan 15 2027 260 calls as one', 'F', 0),
    ('buy 1 ACME Dec 18 2026 240 straddle; collar 100 ACME Dec 18 2026 220/260; close ACME limit 41 gtc then short all GIZM limit 41 all or none', 'P', 0),
    ('close half WIDG; buy 200 WIDG at 225, sell 1 ACME Dec 18 2026 220/230/250/260 iron condor then covered call 100 ACME Dec 18 2026 260 day as a combo', 'P', 0),
    ('close GIZM limit 41 gtc; short 300 GIZM trail 2% and close ACME as one order', 'I', 0),
    ('stc 3 ACME Jan 15 2027 240 puts gtc; sell half ACME at 225 day, close half WIDG; sto 2 ACME Jan 15 2027 260 puts limit 2.10 day as a package', 'P', 0),
    ('(sell half GIZM gtc) then close ACME limit 41 gtc; close GIZM and sell half ACME limit 41 all or none', 'P', 1),
    ('close ACME calls, buy half GIZM, roll 3 ACME Dec 18 2026 250 calls to Jan 15 2027 260 all or none', 'P', 0),
    ('buy write 100 ACME Dec 18 2026 260 gtc; close ACME then buy 100 GIZM gtc as one order', 'P', 0),
    ('buy 100 ACME limit 41 and close ACME day then roll 2 ACME Dec 18 2026 240 calls to Jan 15 2027 250 and sell all ACME as one', 'I', 0),
    ('sell 2 ACME Jan 15 2027 250 calls then close ACME puts limit 41; close WIDG all or none', 'F', 0),
    ('sell 1 ACME Dec 18 2026 260 call; close half WIDG, sell 1 ACME Dec 18 2026 230/240 put spread, btc 3 ACME Dec 18 2026 240 puts as one', 'P', 0),
    ('sell 2 ACME Dec 18 2026 260 calls gtc, short all ACME limit 41 gtc, close ACME day then roll 2 ACME Dec 18 2026 240 calls to Jan 15 2027 250 as one', 'F', 0),
    ('cover all GIZM trail 2% day; close ACME gtc then roll 2 ACME Dec 18 2026 240 calls to Jan 15 2027 250 as a package', 'I', 0),
    ('close WIDG limit 41 then sell 200 GIZM and sell 1 ACME Dec 18 2026 230/240 put spread as one', 'P', 0),
    ('covered call 100 ACME Dec 18 2026 260; close ACME puts gtc then buy write 100 ACME Dec 18 2026 260 all or none', 'I', 0),
    ('close ACME calls then sell 1 ACME Dec 18 2026 230/240 put spread, sell 2 ACME Dec 18 2026 230 puts limit 2.10 gtc and collar 100 ACME Dec 18 2026 220/260 day as one order', 'P', 0),
    ('close WIDG day and stc 1 ACME Jan 15 2027 240 puts limit 2.10 day as one order', 'P', 0),
    ('cover 200 WIDG day then close ACME puts limit 41, buy write 100 ACME Dec 18 2026 260; sell 200 GIZM trail 2% together', 'P', 0),
    ('sell 1 ACME Dec 18 2026 230/240 put spread day; buy half GIZM limit 41 gtc; close WIDG gtc then buy 1 ACME Dec 18 2026 240/250 call spread gtc as a combo', 'P', 0),
    ('close half WIDG limit 41 gtc then roll 2 ACME Dec 18 2026 240 calls to Jan 15 2027 250; sto 3 ACME Dec 18 2026 240 call day together', 'P', 0),
    ('buy 1 ACME Dec 18 2026 240/250 call spread gtc and close GIZM limit 41; covered call 200 ACME Dec 18 2026 250 then close ACME aon', 'F', 0),
    ('covered call 200 ACME Dec 18 2026 250 day and bto 3 ACME Dec 18 2026 230 call; close GIZM limit 41 then close ACME calls day as a package', 'F', 0),
    ('collar 100 ACME Dec 18 2026 220/260 and close ACME puts limit 41 gtc; buy 100 GIZM limit 41 and buy 2 ACME Jan 15 2027 240 puts gtc as a package', 'P', 0),
    ('(cover 200 ACME day); btc 3 ACME Jan 15 2027 250 calls day and close ACME puts limit 41 day, btc 1 ACME Dec 18 2026 230 calls gtc all or none', 'P', 1),
    ('(sell 1 ACME Dec 18 2026 220/230/250/260 iron condor day), close WIDG day then sell half GIZM limit 41 day aon', 'P', 1),
    ('buy 3 ACME Jan 15 2027 250 call day then close WIDG limit 41; stc 1 ACME Dec 18 2026 240 call day as one', 'F', 0),
    ('(sto 3 ACME Jan 15 2027 260 call gtc and bto 2 ACME Dec 18 2026 260 call day) then close WIDG gtc, btc 3 ACME Dec 18 2026 250 puts limit 2.10 aon', 'P', 2),
    ('close GIZM limit 41 day then buy 1 ACME Dec 18 2026 240 straddle and collar 100 ACME Dec 18 2026 220/260 aon', 'P', 0),
    ('close ACME calls day, sell 2 ACME Jan 15 2027 230 puts limit 2.10 together', 'I', 0),
    ('buy 1 ACME Dec 18 2026 240/250 call spread day and close GIZM limit 41 then roll 2 ACME Dec 18 2026 240 calls to Jan 15 2027 250 as one order', 'F', 0),
    ('cover 300 ACME gtc then close GIZM limit 41 gtc and buy 1 ACME Dec 18 2026 240 straddle as a package', 'I', 0),
    ('close half WIDG day and sell 300 GIZM trail 2%; roll 3 ACME Dec 18 2026 250 calls to Jan 15 2027 260 all or none', 'P', 0),
    ('sto 1 ACME Jan 15 2027 240 calls, close GIZM limit 41 gtc and sell 100 WIDG stop 38 gtc and buy 1 ACME Dec 18 2026 240/250 call spread gtc as a package', 'F', 0),
    ('close half WIDG gtc then sell 2 ACME Jan 15 2027 230 call limit 2.10 gtc all or none', 'P', 0),
    ('close GIZM limit 41 gtc, sell 3 ACME Dec 18 2026 230 call together', 'P', 0),
    ('short 100 GIZM at 225 and close ACME puts gtc and sell half WIDG aon', 'P', 0),
    ('close GIZM, roll 2 ACME Dec 18 2026 240 calls to Jan 15 2027 250 all or none', 'P', 0),
    ('close half WIDG; sell 300 GIZM day together', 'P', 0),
    ('btc 3 ACME Dec 18 2026 260 calls day, close GIZM then buy 1 ACME Dec 18 2026 240 calls, btc 2 ACME Dec 18 2026 240 call limit 2.10 gtc aon', 'F', 0),
    ('roll 3 ACME Dec 18 2026 250 calls to Jan 15 2027 260 and close half WIDG; buy all ACME stop 38 gtc; roll 3 ACME Dec 18 2026 250 calls to Jan 15 2027 260 as one order', 'P', 0),
    ('close WIDG limit 41 then sell 200 ACME together', 'P', 0),
    ('buy half GIZM stop 38 and close ACME calls day, stc 2 ACME Jan 15 2027 260 call as a combo', 'P', 0),
    ('close half WIDG; close half WIDG limit 41 gtc then sell 1 ACME Jan 15 2027 250 call gtc then buy 1 ACME Dec 18 2026 250 calls limit 2.10 all or none', 'P', 0),
    ('buy half GIZM trail 2%; close ACME puts limit 41 gtc; close half WIDG all or none', 'P', 0),
    ('buy 1 ACME Dec 18 2026 240/250 call spread gtc; close ACME calls limit 41, buy 1 ACME Dec 18 2026 240/250 call spread day; sell 1 ACME Dec 18 2026 230/240 put spread gtc as a combo', 'P', 0),
    ('sell half ACME stop 38 and close half WIDG then cover 100 GIZM gtc as a package', 'P', 0),
    ('collar 100 ACME Dec 18 2026 220/260 gtc, sell half ACME trail 2%; close WIDG day and buy 3 ACME Dec 18 2026 240 puts limit 2.10 gtc as one', 'P', 0),
    ('close half WIDG; covered call 200 ACME Dec 18 2026 250 gtc; sell 3 ACME Jan 15 2027 250 puts and sell 200 GIZM as one', 'P', 0),
    ('close ACME calls day then sell 200 GIZM as a package', 'P', 0),
    ('close ACME calls limit 41 day; buy 1 ACME Dec 18 2026 240/250 call spread gtc and short all ACME limit 41 together', 'I', 0),
    ('sell 1 ACME Dec 18 2026 220/230/250/260 iron condor; buy 1 ACME Dec 18 2026 240 straddle gtc then close ACME calls gtc and stc 1 ACME Jan 15 2027 240 calls gtc as one order', 'I', 0),
    ('buy 2 ACME Dec 18 2026 240 calls then btc 1 ACME Jan 15 2027 250 calls limit 2.10, close ACME day; sto 1 ACME Jan 15 2027 250 calls all or none', 'P', 0),
    ('short 300 GIZM limit 41, close GIZM limit 41 day; close GIZM together', 'I', 0),
    ('close WIDG day then covered call 100 ACME Dec 18 2026 260 gtc then roll 2 ACME Dec 18 2026 240 calls to Jan 15 2027 250 aon', 'P', 0),
    ('close half WIDG; short 300 WIDG trail 2% as one', 'P', 0),
    ('close half WIDG limit 41 gtc and buy 1 ACME Dec 18 2026 240 straddle and buy 1 ACME Dec 18 2026 240 straddle gtc, buy 300 GIZM limit 41 all or none', 'P', 0),
    ('btc 3 ACME Dec 18 2026 240 call; cover 100 WIDG stop 38 gtc and close ACME puts, buy 300 ACME stop 38 day aon', 'P', 0),
    ('close WIDG limit 41, sell 1 ACME Dec 18 2026 230/240 put spread day and sell half GIZM and close WIDG limit 41 as one order', 'P', 0),
    ('close half WIDG day; buy 300 ACME all or none', 'P', 0),
]


class OneOrderAfterACloseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.rt = STT._ResolverBase._make()
        try:
            STT.build_world(unittest.TestCase(), cls.rt)
        except Exception:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.rt.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def held(self, text: str, book: str, start: int = 0) -> None:
        """Words asking for one order hold every order of the run they end (from clause ``start``): none has a request, each has a red
        flag - so none can be opened in the ticket or relayed on its own (the unified app's relay takes a leg only with no group, no red
        flag and a request) - unless the instruction itself is blocked."""
        out = st.parse_view(self.rt, {"text": text, "book": book})
        if any(f["severity"] == "blocker" for f in out["flags"]) or out["unparsed"]:
            return
        lo = st.parse(out["text"])["clauses"][start]["span"][0]
        run = [l for l in out["legs"] if l["span"][0] >= lo]
        self.assertTrue(run, (text, book))
        free = [(l["n"], l["side"], l["qty"], (l["instrument"] or {}).get("id")) for l in run if l["request"] is not None]
        self.assertEqual([], free, f"{text!r} [{book}]: a card of the run asking for one order can be sent on its own")
        self.assertTrue(all(any(f["severity"] == "blocker" for f in l["flags"]) for l in run), (text, book))

    def test_a_words_asking_for_one_order_hold_a_close_written_before_them(self):
        for text in AFTER_A_CLOSE + CONTROLS:
            out = st.parse_view(self.rt, {"text": text, "book": "P"})
            self.assertEqual(1, len(out["combos"]), text)
            self.assertIn("ONE_ORDER_ASKED", [f["code"] for f in out["combos"][0]["flags"]], text)
            self.assertEqual([("C1", None, True)] * len(out["legs"]),
                             [(l["combo"], l["request"], "ONE_ORDER_ASKED" in [f["code"] for f in l["flags"] if f["severity"] == "blocker"]) for l in out["legs"]], text)
            self.held(text, "P")

    def test_b_the_autofill_property_seeds_hold_every_card_of_the_run(self):
        self.assertEqual((33, 95), (len(AUTOFILL_2220101), len(AUTOFILL_2220102)))
        for text, book, start in AUTOFILL_2220101 + AUTOFILL_2220102:
            with self.subTest(text=text, book=book):
                self.held(text, book, start)


# ==== the page =========================================================================================================================
@unittest.skipIf(find_browser() is None, NO_BROWSER)
class SmartPageNotesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.browser = Browser(find_browser())

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()

    def world(self):
        tmp, rt = STT._ResolverBase._make()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.addCleanup(rt.close)
        STT.build_world(self, rt)
        return rt

    def test_a_a_cards_notes_stay_across_a_navigation_and_the_next_reading_until_it_is_edited_or_opened(self):
        """"buy 5 WIDG limit 249; buy 10 ACME limit 50" read in the Personal Book, #2 edited to 12; the Fund Book selected and the text
        read for it: #2 says the edit was dropped and "was" on its account and quantity.  Those notes stay when the page is left and
        rendered again from the draft, and through the next reading (Enter), and when #1 is opened; an edit of #2's quantity takes that
        field's notes, opening #2 the rest (f153cca lost them on the first navigation)."""
        rt = self.world()
        text = "buy 5 WIDG limit 249; buy 10 ACME limit 50"
        a_p, a_f = R1._parse(rt, text, "P"), R1._parse(rt, text, "F")
        self.assertEqual([corpus_acct("F")] * 2, [l["account_id"] for l in a_f["legs"]])
        pg = R1._Page(self.browser)
        pg.answers(queue=[a_p, a_p, a_f, a_f, a_f])
        pg.read(text)
        pg.edit(1, "qty", "12")
        self.browser.js(pg.s, "T.book.code = 'F'; true")                    # another book selected (the switcher renders the page again)
        pg.unmount_and_back()
        pg.read()                                                            # still for the Personal Book: the edit kept
        self.assertEqual(("12", []), (pg.cards()[1]["qty"], pg.cards()[1]["dropped"]))
        pg.click("#smart-rebook", reads=True)                                # "Read for the Fund Book"

        def notes(c):
            return {"dropped": c["dropped"], "was": c["was"]}
        said = notes(pg.cards()[1])
        self.assertEqual(["your edit: qty 12 - dropped, read again as 10"], said["dropped"])
        self.assertIn("was Personal Book . US Exchange", said["was"])
        self.assertIn("was 12", said["was"])
        pg.unmount_and_back()                                                # a navigation: rendered again from the draft
        self.assertEqual(said, notes(pg.cards()[1]))
        pg.read()                                                            # Enter: the same text read again, #2 reads as before
        self.assertEqual(said, notes(pg.cards()[1]))
        self.assertTrue(pg.cards()[0]["was"])                                # #1 moved to the Fund Book too
        pg.click('#smart-cards [data-i="0"] [data-act="open"]')              # #1 opened: its own notes go, #2's stay
        pg.unmount_and_back()
        pg.read()
        cards = pg.cards()
        self.assertEqual(({"dropped": [], "was": []}, said, True), (notes(cards[0]), notes(cards[1]), cards[0]["opened"]))
        pg.edit(1, "qty", "11")                                              # the quantity edited: its notes go, the account's stays
        self.assertEqual({"dropped": [], "was": ["was Personal Book . US Exchange"]}, notes(pg.cards()[1]))
        pg.click('#smart-cards [data-i="1"] [data-act="open"]')
        self.assertEqual(2, len(self.browser.js(pg.s, "window.__routes")))
        pg.unmount_and_back()                                                # the ticket's page, then back: #2's notes are gone
        self.assertEqual({"dropped": [], "was": []}, notes(pg.cards()[1]))

    def test_b_the_note_above_the_cards_stays_through_the_next_reading_of_the_same_text(self):
        """An edit on an order the new reading has no match for is said above the cards; the page left and rendered again, then Enter
        on the same text: still said; another text read: gone."""
        rt = self.world()
        text = "close ACME calls"
        a1 = R1._parse(rt, text)
        w2 = dict(corpus.WORLD)
        w2["positions"] = [p for p in corpus.WORLD["positions"] if not (p[0] == corpus.P and p[1] == corpus.C240)]
        tmp2, rt2 = STT._ResolverBase._make()
        self.addCleanup(shutil.rmtree, tmp2, True)
        self.addCleanup(rt2.close)
        STT.build_world(self, rt2, w2)
        a2 = R1._parse(rt2, text)
        a3 = R1._parse(rt, "buy 10 ACME limit 50")
        pg = R1._Page(self.browser)
        pg.answers(queue=[a1, a2, a2, a3])
        pg.read(text)
        pg.edit(0, "qty", "1")
        pg.read()
        lost = pg.note("smart-lost")
        self.assertIn('your edit on #1 ("close ACME calls"): qty 1', lost)
        pg.unmount_and_back()
        self.assertEqual(lost, pg.note("smart-lost"))
        pg.read()
        self.assertEqual(lost, pg.note("smart-lost"))
        pg.read("buy 10 ACME limit 50")
        self.assertEqual("", pg.note("smart-lost"))


def corpus_acct(book: str) -> str:
    return {"P": corpus.P, "F": corpus.F, "I": corpus.I}[book]


if __name__ == "__main__":
    unittest.main()
