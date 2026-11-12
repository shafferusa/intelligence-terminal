"""FinSim 1.0.7.2 simplify review round 1 (on 7bc2832): the regression tests of its findings, kept in the terminal suite.

The money path (the owner's rule: a call is never sold as covered unless its shares are free when it is accepted AND stay reserved
until it is closed - a server-side invariant for every order):
  CoverKeptAfterAcceptanceTests  an early put exercise is refused (409 SHARES_RESERVED) when it would deliver shares that are not free
      (held - working sales - the calls' cover - lent): a resting sale can no longer sell a covered call's cover after it; the delivery
      of an exercise at expiry cuts the working sales to the shares left free (``_physical``); the call legs a shares order still waits
      to release keep the cover they will need beyond the shares it still buys (``cover_state``'s reserved_waiting), so a call sold as
      covered, a sale or a lending is refused on them (NOT_COVERED / OVERSELL); the order preview of a Hedges-tab package and the
      strategies preview show the SHARES_RESERVED the order route answers.
  CoverKeptSeedTests  the reductions of tests/test_props1_cover_walk.py's seeds: 1110005 step 128 and 1110001 steps 207 / 305 (an
      exercise while a sale works), 1110006 steps 184 (legs O-000076 of O-000075 and O-000080; claimed O-000077) and 501 (O-000223 of
      O-000222; claimed O-000225) (a package's waiting calls released onto a claimed call's cover).
The reading (the owner's rule: never split words asking for one order silently):
  OneOrderAcrossNamesTests  words asking for one order hold every order of the run they end, whatever its names; the 53 instructions of
      tests/test_props1_parse_oracle.py's seed 1110101 that 7bc2832 read with the first order a plain card.
The page (the REAL static/pages/smartticket.js in a headless Edge / Chrome on about:blank with a small stand-in for window.T, the parse
answers computed here by ``smart_ticket.parse_view`` on tests/terminal_smart_corpus.py's world; no network, no server):
  SmartPageCardsTests  a new reading keeps the edit and the opened mark of a card that reads exactly as before when an order before it
      is gone or a choice on another card wrote its words out, and says an edit it drops; the text is read for the book of the last
      reading after another book is selected (said, with "Read for the <book>"); a real click on Open in ticket right after typing in a
      card opens it.
The order ticket's late answers (a Submit's answer after the ticket moved on to another order) are in test_terminal_browser_smoke.py
(test_xe).  Fictional ACME / WIDG only; temp homes; no ~/.finsim, no port.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest

from tests import terminal_smart_corpus as corpus
from tests import test_terminal_smart_submit as _ss
from tests import test_terminal_smart_ticket as STT
from tests.terminal_cdp import NO_BROWSER, Browser, find_browser
from tests.terminal_helpers import assert_integrity, buy, execute
from tests.test_terminal_smart_submit import (ACCT, E2, ROWS, SmartSubmitBase, acme, acme_chain, add_name, build, leg, load_chain,
                                               print_acme)
from finsim.terminal import smart_ticket as st
from finsim.terminal.orders_preview import cover_state

C105J, C110, P95 = acme("C", 105, E2), acme("C", 110), acme("P", 95)
E1 = "2026-12-18"
CALL_DEC110, CALL_JAN105, CALL_DEC105, PUT_DEC100 = acme("C", 110, E1), acme("C", 105, E2), acme("C", 105, E1), acme("P", 100, E1)


# ==== the money path ===================================================================================================================
class _CoverBase(SmartSubmitBase):
    row = _ss.ReleaseAfterAmendOrReplaceTests.row
    positions = _ss.ReleaseAfterAmendOrReplaceTests.positions
    margin_rules = _ss.CoverCountedOnceTests.margin_rules

    def setUp(self):
        super().setUp()
        self.k = 0

    def rid(self, what: str) -> str:
        self.k += 1
        return f"ui:s1fix-{what}-{self.k:04d}"

    def order(self, iid: str, side: str, qty: int, **kw) -> tuple:
        """An ordinary order from the docked ticket (its preview, then the order with the preview's id): (http, status or code, answer)."""
        req = dict({"account_id": ACCT, "instrument_id": iid, "side": side, "qty": qty, "order_type": "MARKET", "tif": "DAY"}, **kw)
        code, out = self.ticket(req, self.rid(side.lower()))
        return code, ((out.get("order") or {}).get("status") if code == 200 else (out.get("error") or {}).get("code")), out

    def hold(self, n: int) -> None:
        code, status, out = self.order("ACME", "BUY", n)
        self.assertEqual((200, "FILLED"), (code, status), out)

    def covered(self, occ: str, n: int) -> tuple:
        return self.order(occ, "STO", n, covered=True)

    def acme_bar(self, last: str, vol: int) -> None:
        self.rt.clock.advance(60)
        now = self.rt.clock.epoch()
        print_acme(self.rt, last, 10, now - 60, vol)
        self.rt.matcher.on_quotes(["ACME"])

    def option_pass(self) -> None:
        self.rt.clock.advance(30)
        load_chain(self.rt, acme_chain(self.rt.clock.epoch()))
        self.rt.matcher.on_quotes([C105J, C110, P95])

    def uncovered(self) -> int:
        """The ACME the book's short ACME calls need beyond the ACME it holds (0: every short call is covered)."""
        pos = self.positions()
        short = -sum(q for iid, q in pos.items() if iid.startswith("ACME2") and iid[10] == "C" and q < 0)
        return max(0, 100 * short - max(0, pos.get("ACME", 0)))

    def cover(self) -> dict:
        with self.rt.db.reader() as conn:
            return cover_state(self.rt, conn, ACCT, "ACME", claims=True)


class CoverKeptAfterAcceptanceTests(_CoverBase):
    def test_a_an_early_put_exercise_never_delivers_the_shares_a_resting_sale_and_a_covered_call_hold(self):
        """200 ACME held, a sale of 100 resting at 120.00, a collar's call sold as covered on the other 100: the owner's early exercise of
        the put is refused 409 SHARES_RESERVED (0 free), naming the call, the sale and what may be exercised; ACME rebounds, the sale fills
        and the call stays covered (7bc2832 delivered the 100 the call holds and the sale then sold the rest: the call was naked)."""
        self.hold(200)
        code, status, out = self.order("ACME", "SELL", 100, order_type="LIMIT", limit_px="120.00")
        self.assertEqual((200, "WORKING"), (code, status), out)
        sale = out["order"]["order_id"]
        self.assertEqual((200, "FILLED"), self.order(P95, "BTO", 1)[:2])
        self.assertEqual((200, "FILLED"), self.covered(C110, 1)[:2])
        self.assertEqual((400, "OVERSELL"), self.order("ACME", "SELL", 1)[:2])
        self.rt.clock.advance(60)
        print_acme(self.rt, "90.00", 60, self.rt.clock.epoch() - 120, 50000)
        code, out, _h = self.call("POST", "/api/options/exercise?book=T", {"account_id": ACCT, "instrument_id": P95, "qty": 1}, self.rid("exercise"))
        self.assertEqual((409, "SHARES_RESERVED"), (code, (out.get("error") or {}).get("code")), out)
        msg = out["error"]["message"]
        for words in (sale, ": 0 are free", C110):
            self.assertIn(words, msg)
        self.assertEqual({"ACME": 200, P95: 1, C110: -1}, self.positions())
        self.acme_bar("121.00", 50000)                                  # the resting sale fills: the 100 it held, never the call's
        self.assertEqual(("FILLED", {"ACME": 100, P95: 1, C110: -1}, 0), (self.row(sale)["status"], self.positions(), self.uncovered()))
        self.assertEqual("covered_call", self.margin_rules().get(C110))
        assert_integrity(self, self.rt, "full")

    def test_a2_an_expiry_exercise_cuts_a_resting_sale_to_the_shares_left_free(self):
        """The same with the automatic exercise at expiry and a GTC sale: the delivery of 100 of the 200 held leaves 100, all of them the
        cover of the call sold as covered, so the sale is cancelled (nothing of it can sell free shares any more); ACME's later 121.00
        sells nothing and the call stays covered (7bc2832 trimmed the sale to the 100 left only: it sold the call's cover)."""
        self.hold(200)
        code, status, out = self.order("ACME", "SELL", 100, order_type="LIMIT", limit_px="120.00", tif="GTC")
        self.assertEqual((200, "WORKING"), (code, status), out)
        sale = out["order"]["order_id"]
        self.assertEqual((200, "FILLED"), self.order(P95, "BTO", 1)[:2])
        self.assertEqual((200, "FILLED"), self.covered(C105J, 1)[:2])
        self.rt.clock.set("2026-12-18T16:30:00-05:00")
        self.assertIsNotNone(self.rt.lifecycle.tick(at="2026-12-18T16:30:00-05:00", only="SETTLE_DUE")["ran"])
        ts = self.rt.clock.epoch()
        with self.rt.db.writer() as tx:
            tx.conn.execute("INSERT INTO marks(instrument_id, mark_date, kind, price, currency, quality, source, method, quote_time, fetched_at, note)"
                            " VALUES ('ACME', '2026-12-18', 'CLOSE', '90.00', 'USD', 'close', 'test', 'direct', ?, ?, 'fixture')", (ts - 1800, ts))
        r = execute(self.rt, "options.expire", {"account_id": ACCT, "instrument_id": P95}, "job:s1fix-expiry-0001", actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("CANCELLED", {"ACME": 100, C105J: -1}), (self.row(sale)["status"], self.positions()))
        self.assertIn("cover calls", self.row(sale)["reason"] or "")
        self.rt.clock.set("2026-12-21T10:00:00-05:00")
        self.acme_bar("121.00", 50000)
        self.assertEqual(({"ACME": 100, C105J: -1}, 0), (self.positions(), self.uncovered()))
        assert_integrity(self, self.rt, "full")

    def test_b_a_call_sold_as_covered_is_refused_on_shares_a_waiting_package_s_calls_will_need(self):
        """A shares order with its hedge legs (BUY 100 ACME limit 99.00 resting + 2 Dec 110 calls waiting for it), placed while no call
        on ACME is sold as covered, keeps 100 of the 100 held for the second call it will release: a Jan 105 call sold as covered on
        them is refused NOT_COVERED (7bc2832 accepted it and the released calls took its cover).  The shares fill, the 2 calls are
        released and sold, every call covered."""
        self.hold(100)
        primary = {"account_id": ACCT, "instrument_id": "ACME", "side": "BUY", "qty": 100, "order_type": "LIMIT", "limit_px": "99.00", "tif": "DAY"}
        code, out, p, (h,) = self.seq_package(primary, [leg(C110, "STO", 2)], rid="ui:s1fix-seq-b-0001")
        self.assertEqual((200, "WORKING", "WAITING_PARENT", 2), (code, p["status"], h["status"], int(h["qty"])), out)
        cs = self.cover()
        self.assertEqual((100, 100, 0), (cs["held"], cs["reserved_waiting"], cs["free"]), cs)
        self.assertEqual([p["order_id"]], [w["waiting_for"] for w in cs["waiting"]])
        code, status, out = self.covered(C105J, 1)
        self.assertEqual((409, "NOT_COVERED"), (code, status), out)
        code, status, out = self.order("ACME", "SELL", 1)               # nor sold: the calls to come keep them
        self.assertEqual((400, "OVERSELL"), (code, status), out)
        self.assertIn("SHARES_RESERVED", out["error"]["message"])
        self.acme_bar("98.90", 50000)
        self.option_pass()
        self.assertEqual(({"ACME": 200, C110: -2}, "FILLED", "FILLED", 0), (self.positions(), self.row(p["order_id"])["status"],
                                                                              self.row(h["order_id"])["status"], self.uncovered()))
        assert_integrity(self, self.rt, "full")

    def test_c_the_shares_a_buy_write_bought_for_its_later_calls_are_not_free(self):
        """With a Jan 105 call sold as covered on the 100 ACME held, a buy write through the Hedges tab (BUY 200 ACME limit 99.00 + 2 Dec
        110 calls; INV-9 allows it: 2 x 100 <= 200 bought): 150 fill and one call is released; the 50 bought and not yet behind a released
        call are kept for the second one, so a sale of 50 is refused OVERSELL (7bc2832 sold them and the call sold as covered ended naked);
        the last 50 fill and release the second call: every call covered."""
        self.hold(100)
        self.assertEqual((200, "FILLED"), self.covered(C105J, 1)[:2])
        primary = {"account_id": ACCT, "instrument_id": "ACME", "side": "BUY", "qty": 200, "order_type": "LIMIT", "limit_px": "99.00", "tif": "DAY"}
        code, out, p, (h,) = self.seq_package(primary, [leg(C110, "STO", 2)], rid="ui:s1fix-seq-c-0001")
        self.assertEqual((200, "WORKING", "WAITING_PARENT"), (code, p["status"], h["status"]), out)
        self.acme_bar("98.90", 1500)                                     # 10 % of 1,500: 150 bought, floor(150 x 2 / 200) = 1 call
        self.option_pass()
        self.assertEqual({"ACME": 250, C105J: -1, C110: -1}, self.positions())
        cs = self.cover()
        self.assertEqual((250, 50, 0), (cs["held"], cs["reserved_waiting"], cs["free"]), cs)
        self.acme_bar("99.50", 50000)
        self.assertEqual((400, "OVERSELL"), self.order("ACME", "SELL", 50)[:2])
        self.acme_bar("98.90", 50000)
        self.option_pass()
        self.assertEqual(({"ACME": 300, C105J: -1, C110: -2}, 0), (self.positions(), self.uncovered()))
        self.assertEqual("covered_call", self.margin_rules().get(C105J))
        assert_integrity(self, self.rt, "full")

    def test_d_control_the_calls_released_first_then_the_claim_and_the_sale_are_refused(self):
        self.hold(100)
        primary = {"account_id": ACCT, "instrument_id": "ACME", "side": "BUY", "qty": 100, "order_type": "LIMIT", "limit_px": "99.00", "tif": "DAY"}
        code, out, p, (h,) = self.seq_package(primary, [leg(C110, "STO", 2)], rid="ui:s1fix-seq-d-0001")
        self.assertEqual(200, code, out)
        self.acme_bar("98.90", 50000)
        self.option_pass()
        self.assertEqual({"ACME": 200, C110: -2}, self.positions())
        self.assertEqual((409, "NOT_COVERED"), self.covered(C105J, 1)[:2])
        self.assertEqual((400, "OVERSELL"), self.order("ACME", "SELL", 1)[:2])
        self.assertEqual(0, self.uncovered())
        assert_integrity(self, self.rt, "full")

    def test_e_the_previews_show_the_shares_reserved_refusal_of_a_package_s_calls(self):
        """With a Jan 105 call sold as covered and no ACME free, the order preview of a Hedges-tab package whose calls need more than the
        shares it buys and the strategies preview of a call spread carry the SHARES_RESERVED blocker the order route answers, so Submit
        stays disabled with the reason (7bc2832's previews showed none)."""
        self.hold(100)
        self.assertEqual((200, "FILLED"), self.covered(C105J, 1)[:2])
        body = {"account_id": ACCT, "instrument_id": "ACME", "side": "BUY", "qty": 100, "order_type": "LIMIT", "limit_px": "99.00", "tif": "DAY",
                "hedge": {"legs": [leg(C110, "STO", 2)]}}
        code, pv, _h = self.call("POST", "/api/orders/preview?book=T", dict(body))
        self.assertEqual((200, ["SHARES_RESERVED"]), (code, [b["code"] for b in pv.get("blockers") or []]), pv)
        code, out, _h = self.send(dict(body, preview_id=pv.get("preview_id")), "ui:s1fix-seq-e-0001")
        self.assertEqual((409, "SHARES_RESERVED"), (code, (out.get("error") or {}).get("code")), out)
        params = {"underlying": "ACME", "units": 100, "tenor_months": 1, "strikes": {"0": 100, "1": 105}}
        code, spec, _h = self.call("POST", "/api/strategies/preview?book=T", {"account_id": ACCT, "key": "BULL_CALL_SPREAD", "params": params})
        self.assertEqual((200, ["SHARES_RESERVED"]), (code, [b.get("code") if isinstance(b, dict) else b for b in (spec.get("blockers") or [])]), spec)
        code, out = self.template_package("BULL_CALL_SPREAD", 100, {0: 100, 1: 105}, rid="ui:s1fix-tpl-e-0001")
        self.assertEqual((409, "SHARES_RESERVED"), (code, (out.get("error") or {}).get("code")), out)
        code, pv, _h = self.call("POST", "/api/orders/preview?book=T", dict(body, qty=200))   # a buy write its own shares cover: no blocker
        self.assertEqual((200, []), (code, [b["code"] for b in pv.get("blockers") or []]), pv)


class CoverKeptSeedTests(unittest.TestCase):
    """The walk's world (tests/test_props1_cover_walk.py): the Test Book T1, ACME printed at 100.00, the invented chain."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-s1fix-seed-")
        self.rt = build(self.tmp)
        with self.rt.db.writer() as tx:
            self.rt.instruments.upsert(tx, instrument_id="ACME", kind="EQUITY", symbol="ACME", name="ACME test listing", currency="USD", session_group="US_EQUITY",
                                       account_kinds=["US_EXCHANGE", "PB"], yahoo_symbol="ACME", cboe_symbol="ACME", spec={"exchange": "NYQ", "adv": 5000000})
        self.n = 0
        self.observe("100.00", 50000)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def observe(self, last: str, vol: int) -> None:
        now = self.rt.clock.epoch()
        print_acme(self.rt, last, 60, (now // 60) * 60 - 120, vol)
        load_chain(self.rt, acme_chain(now, size=50))

    def gap(self, last: str = "100.00", vol: int = 50000, s: int = 61) -> None:
        self.rt.clock.advance(s)
        self.observe(last, vol)
        self.rt.matcher.on_quotes(["ACME"] + [acme(r, k, e) for e, r, k, _b, _a in ROWS])
        self.rt.matcher.sweep()

    def call(self, method: str, path: str, body: dict):
        self.n += 1
        return self.rt.router.handle(method, path, None, dict(body, request_id=f"ui:s1fix-seed-{self.n:04d}"), "127.0.0.1", None)

    def order(self, **b) -> tuple:
        code, out, _h = self.call("POST", "/api/orders?book=T", dict({"account_id": ACCT, "tif": "DAY", "order_type": "MARKET"}, **b))
        self.assertLess(code, 500, out)
        return code, (out.get("error") or {}).get("code") if code != 200 else out["order"]["status"]

    def cover(self) -> dict:
        with self.rt.db.reader() as conn:
            return cover_state(self.rt, conn, ACCT, "ACME", claims=True)

    def assert_claims_kept(self, when: str) -> None:
        """Every claimed call keeps its cover: covered in cover_state's reading, and held - working sales - lent covers every claimed contract."""
        cs = self.cover()
        claimed = {}
        for c in cs.get("claimed") or []:
            claimed[c["instrument_id"]] = claimed.get(c["instrument_id"], 0) + int(c["contracts"])
        short_cover = {c["instrument_id"]: (int(c["contracts"]), int(c["covered"])) for c in cs["calls"] if c["order_id"] is None}
        for iid, n in sorted(claimed.items()):
            if iid in short_cover:
                self.assertGreaterEqual(short_cover[iid][1], min(n, short_cover[iid][0]), f"{when}: {cs['calls']}")
        self.assertGreaterEqual(cs["held"] - cs["working_sells"] - cs["lent"], 100 * sum(claimed.values()), f"{when}: {cs}")

    def test_seed_1110005_step_128_and_1110001_steps_207_305_an_exercise_while_a_sale_works(self):
        self.assertEqual(200, self.order(instrument_id="ACME", side="BUY", qty=200)[0])
        self.gap()
        self.assertEqual(200, self.order(instrument_id=CALL_DEC105, side="STO", qty=1, covered=True)[0])
        self.assertEqual(200, self.order(instrument_id="ACME", side="SELL", qty=100, order_type="LIMIT", limit_px="150.00")[0])
        self.assertEqual(200, self.order(instrument_id=PUT_DEC100, side="BTO", qty=1)[0])
        self.gap()
        self.assertEqual(0, self.cover()["free"], self.cover())
        code, out, _h = self.call("POST", "/api/options/exercise?book=T", {"account_id": ACCT, "instrument_id": PUT_DEC100, "qty": 1})
        self.assertEqual((409, "SHARES_RESERVED"), (code, (out.get("error") or {}).get("code")), out)
        self.assert_claims_kept("after the refused exercise")
        self.gap(last="151.00")                     # the SELL at 150 fills
        self.assert_claims_kept("after the working SELL filled")
        # control (the walk's seed 1110005 step 128 without the sale): 100 held, the claim, the put - refused as before
        code, out, _h = self.call("POST", "/api/options/exercise?book=T", {"account_id": ACCT, "instrument_id": PUT_DEC100, "qty": 1})
        self.assertEqual((409, "SHARES_RESERVED"), (code, (out.get("error") or {}).get("code")), out)

    def test_seed_1110006_steps_184_501_a_package_waiting_when_a_call_is_sold_as_covered(self):
        self.assertEqual(200, self.order(instrument_id="ACME", side="BUY", qty=100)[0])
        self.gap()
        self.assertEqual((200, "WORKING"), self.order(instrument_id="ACME", side="BUY", qty=100, order_type="LIMIT", limit_px="90.00",
                                                      hedge={"legs": [{"instrument_id": CALL_DEC110, "side": "STO", "qty": 2, "order_type": "MARKET"}]}))
        self.assertEqual((409, "NOT_COVERED"), self.order(instrument_id=CALL_JAN105, side="STO", qty=1, covered=True))
        self.gap(last="89.50")
        self.gap(last="89.50")                      # the package's shares fill; its 2 calls are released and sold on the 200 held
        cs = self.cover()
        self.assertEqual((200, 0, 0), (cs["held"], cs["free"], cs["reserved_waiting"]), cs)
        self.assertEqual([(CALL_DEC110, 2, 2)], [(c["instrument_id"], int(c["contracts"]), int(c["covered"])) for c in cs["calls"]])

    def test_seed_1110006_one_share_bought_keeps_the_cover_of_its_call(self):
        self.assertEqual(200, self.order(instrument_id="ACME", side="BUY", qty=100)[0])
        self.gap()
        self.assertEqual(200, self.order(instrument_id="ACME", side="BUY", qty=1, order_type="LIMIT", limit_px="90.00",
                                         hedge={"legs": [{"instrument_id": CALL_DEC110, "side": "STO", "qty": 1, "order_type": "MARKET"}]})[0])
        self.assertEqual((99, 1), (self.cover()["reserved_waiting"], self.cover()["free"]))
        self.assertEqual((409, "NOT_COVERED"), self.order(instrument_id=CALL_JAN105, side="STO", qty=1, covered=True))
        self.gap()
        self.gap(last="89.50")
        self.gap(last="89.50")
        self.assert_claims_kept("after the 1-share order filled and its call was released")

    def test_seed_1110006_a_sale_of_a_partly_filled_package_s_shares_is_refused(self):
        self.assertEqual(200, self.order(instrument_id="ACME", side="BUY", qty=100)[0])
        self.gap()
        self.assertEqual(200, self.order(instrument_id=CALL_JAN105, side="STO", qty=1, covered=True)[0])
        self.gap()
        self.gap(vol=500)
        self.assertEqual(200, self.order(instrument_id="ACME", side="BUY", qty=100, order_type="LIMIT", limit_px="101.00",
                                         hedge={"legs": [{"instrument_id": CALL_DEC110, "side": "STO", "qty": 1, "order_type": "MARKET"}]})[0])
        cs = self.cover()
        self.assertEqual((150, 50, 0), (cs["held"], cs["reserved_waiting"], cs["free"]), cs)     # 50 of 100 bought on the 500-share bar
        self.assertEqual((400, "OVERSELL"), self.order(instrument_id="ACME", side="SELL", qty=50))
        self.gap(vol=500)
        self.gap()
        self.gap()
        self.assert_claims_kept("after the rest of the package's shares filled and its call was released")
        self.assertEqual(200, self.cover()["held"])


# ==== the reading: words asking for one order ===========================================================================================
#: tests/test_props1_parse_oracle.py, seed 1110101: the 53 instructions 7bc2832 read with only the last order held (world, text)
ORACLE_1110101 = [
    ("W3", "sell 150 WIDG stop 110 day and buy 100 ACME 99.5 limit good till Dec 18 as one"),
    ("W2", "sell 250 WIDG market and cover 50 ACME market gtd 2026-12-18 as one order"),
    ("W1", "sell 461 WIDG limit 41.20 gtd 2026-12-18 and buy 50 ACME @ 0.05 as a package"),
    ("W2", "cover 1,200 WIDG $1,234.50 limit day and buy 1 ACME 100c 01/15/27 limit 0.05 good till Dec 18 aon"),
    ("W0", "cover 200 ACME trail 1.5% day and short 2k WIDG 101 limit day aon"),
    ("W1", "bto 2 ACME Dec 18 2026 100 call limit 12.345 and cover 100 WIDG lmt $1,234.50 day together"),
    ("W0", "sell short 1,000 WIDG trail 0.25 and sell 1k ACME gtd 2026-11-20 all or none"),
    ("W1", "sell short 300 WIDG good till cancelled and sell 2 ACME 2026-12-18 115 C at the bid day aon"),
    ("W0", "bto 1 ACME 2026-12-18 105 C at the ask gtd 2026-12-18 and short 1.5k WIDG lmt 99.5 as one order"),
    ("W2", "btc 2 ACME 2026-12-18 105 C at the ask good until Nov 20 and cover 1,000 WIDG 99.5 limit good till Dec 18 as a package"),
    ("W1", "sell 100 shares WIDG at 0.05 day and stc 2 ACME Dec 18 2026 110 calls at the bid together"),
    ("W2", "sell 2 ACME Dec 18 2026 115 calls at 5 good till cancelled and cover 1,200 WIDG as a package"),
    ("W1", "buy 1 ACME 2026-12-18 100 C limit 5 and buy to cover 250 WIDG stop 110 limit 109.50 good till cancelled as one"),
    ("W1", "sell short 1,000 WIDG stop 110 limit 111 gtd 2026-12-18 and sell 1 ACME 2026-12-18 120 C at 12.345 as a package"),
    ("W3", "sell short 2k WIDG trail 1.50 good till Dec 18 and stc 3 ACME 2026-12-18 90 P good till Dec 18 aon"),
    ("W1", "sell 100 shares WIDG @ $98.40 good till Dec 18 and stc 1 ACME 2026-12-18 100 C day all or none"),
    ("W1", "buy 1 ACME Dec 18 2026 110 call 2.70 limit gtd 2026-12-18 and sell short 200 shares WIDG at 99.5 good until Nov 20 all or none"),
    ("W0", "buy 1k WIDG at 0.05 gtc and stc 1 ACME Jan 15 2027 100 call all or none"),
    ("W3", "buy 200 WIDG at market day and sell 40 shares ACME limit 99.5 good until Nov 20 as a package"),
    ("W2", "buy 100 WIDG stop 95.50 gtc and sell 3 ACME Dec 18 2026 100 call 5 limit aon"),
    ("W3", "sell 300 WIDG market and sell short 200 ACME stop 95.50 limit 111 as one order"),
    ("W2", "buy 3 contracts ACME Dec 18 2026 95 puts and short 1k WIDG stop 95.50 limit 96 gtd 2026-12-18 together"),
    ("W0", "sell short 250 ACME lmt $1,234.50 gtd 2026-12-18 and buy to cover 40 shares WIDG trail 1.5% good until Nov 20 as a package"),
    ("W2", "sell 1.5k WIDG trail 1.5% gtc and buy 1 ACME 90p 12/18/26 limit 5 gtd 2026-12-18 as a package"),
    ("W0", "buy 300 WIDG stop 95.50 limit 96 and buy 200 ACME at 101 gtd 2026-11-20 together"),
    ("W1", "sto 1 ACME Jan 15 2027 105 call @ 12.345 and buy 100 WIDG limit $98.40 good till cancelled aon"),
    ("W2", "short 200 shares WIDG market gtd 2026-11-20 and buy 1 ACME 95p 12/18/26 at the bid good until Nov 20 together"),
    ("W3", "sell 3 ACME 2026-12-18 105 C @ 12.345 day and short 228 WIDG mkt day together"),
    ("W2", "buy 2k WIDG at market gtd 2026-11-20 and sell 1 ACME 105c 12/18/26 at the ask aon"),
    ("W2", "stc 2 ACME Dec 18 2026 90 put gtd 2026-12-18 and short 200 WIDG market gtd 2026-12-18 as one"),
    ("W3", "short 1,200 WIDG @ $1,234.50 good till cancelled and sell 1 contracts ACME 2026-12-18 120 C at 0.05 gtd 2026-12-18 as a combo"),
    ("W0", "buy 1 ACME Jan 15 2027 100 call and buy 2,500 WIDG stop 110 limit 96 gtc together"),
    ("W1", "buy to cover 200 shares ACME @ 41.20 and short 100 WIDG at market gtd 2026-12-18 as one"),
    ("W1", "sell 100 WIDG stop 95.50 limit 96 gtd 2026-11-20 and sell short 786 ACME stop 110 limit 111 gtd 2026-12-18 as one order"),
    ("W3", "sell short 300 WIDG @ 100.125 gtd 2026-12-18 and sell 1 ACME Jan 15 2027 105 call 0.05 limit day all or none"),
    ("W0", "buy 2.5k WIDG mkt good until Nov 20 and sell short 2,500 ACME day as a package"),
    ("W2", "buy 2 ACME 105c 12/18/26 good until Nov 20 and cover 200 shares WIDG @ $98.40 good till Dec 18 aon"),
    ("W0", "cover 200 shares WIDG gtd 2026-11-20 and buy to cover 250 ACME as a package"),
    ("W3", "buy 2 ACME Jan 15 2027 105 calls limit 5 day and buy to cover 2,500 WIDG at 41.20 good till cancelled as one order"),
    ("W2", "sell 941 WIDG trail 2 good till Dec 18 and buy 2,500 ACME together"),
    ("W3", "sell short 300 ACME at market day and buy 999 WIDG good till cancelled aon"),
    ("W3", "sell 529 WIDG 0.05 limit gtd 2026-11-20 and sell 1 ACME 105c 12/18/26 limit 2.70 gtc as a combo"),
    ("W3", "buy 3 ACME 105c 01/15/27 at the bid day and cover 40 shares WIDG 100.125 limit good till Dec 18 together"),
    ("W2", "buy to cover 2.5k WIDG @ 100.125 gtd 2026-11-20 and sell 1 contracts ACME 105c 01/15/27 at 12.345 good till cancelled as a combo"),
    ("W0", "buy 100 shares WIDG and short 2.5k ACME mkt as one"),
    ("W0", "sell 1 ACME Dec 18 2026 100 call limit 0.05 good until Nov 20 and buy 100 shares WIDG limit $1,234.50 as a combo"),
    ("W0", "sell 1 ACME 95p 12/18/26 mkt and buy 1,000 WIDG market gtd 2026-11-20 as a combo"),
    ("W3", "buy 1 ACME 105c 01/15/27 and short 2.5k WIDG trail 0.25 as a combo"),
    ("W3", "sell 100 WIDG 100.125 limit day and cover 150 ACME trail 2% gtc as one"),
    ("W0", "short 150 ACME and sell 810 WIDG lmt $1,234.50 gtd 2026-11-20 aon"),
    ("W1", "short 300 WIDG trail 1.50 and buy 300 ACME market good till cancelled together"),
    ("W1", "short 992 WIDG $98.40 limit and stc 1 ACME Jan 15 2027 105 call limit 0.05 good until Nov 20 as one order"),
    ("W0", "buy 1k WIDG at $98.40 gtd 2026-11-20 and sell 2 ACME 95p 12/18/26 at the bid day as one"),
]
SPLIT = ["buy 200 WIDG limit 41 and sell 2 ACME Dec 18 2026 105 calls as a package",
         "buy 200 ACME and buy 100 WIDG as a package",
         "buy 200 WIDG and buy 100 ACME all or none",
         "sell 1 ACME Dec 18 2026 105 call and buy 100 WIDG as one order"]
HELD = ["buy 200 ACME limit 99 and sell 2 ACME Dec 18 2026 105 calls as a package",
        "buy 1 ACME Dec 18 2026 100 call and sell 1 ACME Jan 15 2027 105 call as a package",
        "(buy 200 WIDG and buy 100 ACME) as a package"]
C_DEC100, C_DEC110, P_DEC95 = acme("C", 100, E1), acme("C", 110, E1), acme("P", 95, E1)
#: the oracle's worlds: W0 nothing held; W1 300 ACME, 2 Dec 100 calls long, 1 Dec 110 call short; W2 200 ACME short, 1 Dec 95 put
#: long; W3 250 ACME, a working SELL 100 at 150, 1 Jan 105 call short
WORLD_ORDERS = {"W0": [],
                "W1": [("ACME", "BUY", 300, {}), (C_DEC100, "BTO", 2, {}), (C_DEC110, "STO", 1, {})],
                "W2": [("ACME", "SELL_SHORT", 200, {"borrow": True}), (P_DEC95, "BTO", 1, {})],
                "W3": [("ACME", "BUY", 250, {}), ("ACME", "SELL", 100, {"order_type": "LIMIT", "limit_px": "150.00"}), (CALL_JAN105, "STO", 1, {})]}


class OneOrderAcrossNamesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.worlds = {}
        try:
            for name, orders in WORLD_ORDERS.items():
                tmp = tempfile.mkdtemp(prefix=f"fst-s1fix-one{name}-")
                rt = build(tmp)
                cls.worlds[name] = (tmp, rt)
                add_name(rt, "WIDG")
                with rt.db.writer() as tx:
                    rt.instruments.upsert(tx, instrument_id="ACME", kind="EQUITY", symbol="ACME", name="ACME test listing", currency="USD",
                                          session_group="US_EQUITY", account_kinds=["US_EXCHANGE", "PB"], yahoo_symbol="ACME", cboe_symbol="ACME",
                                          spec={"exchange": "NYQ", "adv": 5000000})
                now = rt.clock.epoch()
                print_acme(rt, "100.00", 120, now - 180, 50000)
                load_chain(rt, acme_chain(now, size=50))
                for i, (iid, side, qty, extra) in enumerate(orders):
                    body = dict({"account_id": ACCT, "instrument_id": iid, "side": side, "qty": qty, "order_type": "MARKET", "tif": "DAY",
                                 "request_id": f"ui:s1fix-one-{name}-{i:03d}"}, **extra)
                    code, out, _h = rt.router.handle("POST", "/api/orders?book=T", None, body, "127.0.0.1", None)
                    assert code == 200, (name, body, out)
                rt.matcher.on_quotes(["ACME", C_DEC100, C_DEC110, CALL_JAN105, P_DEC95])
                rt.matcher.sweep()
        except Exception:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        for tmp, rt in cls.worlds.values():
            rt.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def held_whole(self, world: str, text: str) -> None:
        code, out, _h = self.worlds[world][1].router.handle("POST", "/api/smart-ticket/parse?book=T", None, {"text": text, "book": "T"}, "127.0.0.1", None)
        self.assertEqual(200, code, out)
        legs = out["legs"]
        self.assertEqual(2, len(legs), text)
        for l in legs:
            codes = [f["code"] for f in l["flags"] if f["severity"] == "blocker"]
            self.assertIn("ONE_ORDER_ASKED", codes, f"{world} {text!r}: card {l['n']} ({l['side']} {l['qty']} {l['instrument']['id']}) is not held: {codes}")
            self.assertIsNone(l["request"], f"{world} {text!r}: card {l['n']} can be opened on its own")
        self.assertEqual(1, len({l["combo"] for l in legs}), f"{text!r}: {[l['combo'] for l in legs]}")
        (g,) = out["combos"]
        self.assertIn("ONE_ORDER_ASKED", [f["code"] for f in g["flags"]], g)
        self.assertIn("as separate tickets", json.dumps([f.get("choices") for f in g["flags"]]), g)

    def test_words_asking_for_one_order_after_orders_on_two_names_hold_every_card(self):
        for text in SPLIT + HELD:
            with self.subTest(text=text):
                self.held_whole("W0", text)

    def test_oracle_seed_1110101_the_53_instructions(self):
        self.assertEqual(53, len(ORACLE_1110101))
        for world, text in ORACLE_1110101:
            with self.subTest(world=world, text=text):
                self.held_whole(world, text)


# ==== the page ===========================================================================================================================
PAGE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "finsim", "terminal", "static", "pages", "smartticket.js")
#: a stand-in for the shell's window.T: only what smartticket.js calls (no network; the parse route answers from window.__queue or
#: window.__answers); its accounts are named as the real shell names them ("US Exchange" in every book) and owned by their book
STUB = r"""(function () {
  var store = {}, listeners = {};
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function add(el, c) {
    if (c == null || c === false) { return; }
    if (Array.isArray(c)) { c.forEach(function (x) { add(el, x); }); return; }
    el.appendChild(typeof c === 'object' && c.nodeType ? c : document.createTextNode(String(c)));
  }
  function h(tag, attrs) {
    var el = document.createElement(tag); var a = attrs || {};
    Object.keys(a).forEach(function (k) {
      var v = a[k]; if (v == null || v === false) { return; }
      if (k === 'class') { el.className = v; } else if (k === 'html') { el.innerHTML = v; } else if (k === 'hidden') { el.hidden = !!v; }
      else if (k.slice(0, 2) === 'on' && typeof v === 'function') { el.addEventListener(k.slice(2), v); } else { el.setAttribute(k, v === true ? '' : v); }
    });
    for (var i = 2; i < arguments.length; i++) { add(el, arguments[i]); }
    return el;
  }
  var BOOKS = [{ book_code: 'P', name: 'Personal Book' }, { book_code: 'F', name: 'Fund Book' }, { book_code: 'I', name: 'Institutional Book' }];
  window.__answers = {}; window.__queue = []; window.__posts = []; window.__routes = [];
  window.T = {
    esc: esc, h: h, pages: {},
    clear: function (el) { while (el.firstChild) { el.removeChild(el.firstChild); } return el; },
    pageHead: function (page, title, icon, buttons) { var hd = h('div', { class: 'head' }, h('h1', null, title), h('div', { class: 'sub' }), buttons || []); page.appendChild(hd); return hd; },
    convNote: function () { return h('div', { class: 'conv' }); },
    state: { books: BOOKS, on: function (ev, fn) { (listeners[ev] = listeners[ev] || []).push(fn); return function () { listeners[ev] = (listeners[ev] || []).filter(function (x) { return x !== fn; }); }; } },
    fmt: { hm: function () { return '14:00'; }, qty: function (n) { return String(n); }, time: function () { return '14:00:00'; } },
    clock: { now: function () { return 1792519200; } },
    store: { get: function (k, d) { return store[k] != null ? JSON.parse(store[k]) : d; }, set: function (k, v) { store[k] = v == null ? null : JSON.stringify(v); } },
    api: { post: function (url, body) {
             window.__posts.push({ url: url, body: body });
             var a = window.__queue.length ? window.__queue.shift() : window.__answers[body.text];
             return a ? Promise.resolve(JSON.parse(JSON.stringify(a))) : Promise.reject({ status: 500, code: 'NO_SCRIPTED_ANSWER' });
           },
           getReady: function () { return Promise.reject({}); }, describe: function (e) { return String(e && e.code); } },
    ui: { toast: function () {} },
    modulesKnown: function () { return true; }, hasModule: function () { return true; },
    book: { code: 'P' },
    accountRefusal: function () { return null; },
    accountName: function (id) { return /US_EXCHANGE$/.test(String(id)) ? 'US Exchange' : String(id); },
    accountOwner: function (id) { var c = String(id || '').charAt(0); return BOOKS.filter(function (b) { return b.book_code === c; })[0] || null; },
    router: { go: function (x) { window.__routes.push(x); }, account: function (id, sub) { return '#/a/' + id + '/' + (sub || 'markets'); } },
    ticket: { open: function () {} }
  };
  document.body.innerHTML = '<div id="root"></div>';
  return true;
})()"""
CARDS = r"""(function () {
  return Array.prototype.map.call(document.querySelectorAll('#smart-cards [data-i]'), function (c) {
    function v(f) { var e = c.querySelector('[data-edit="' + f + '"]'); return e ? e.value : null; }
    return { i: Number(c.dataset.i), head: (c.querySelector('.smart-card-head') || {}).textContent || '', qty: v('qty'), type: v('order_type'), limit: v('limit_px'),
             where: (c.querySelector('.smart-where') || {}).textContent || '',
             was: Array.prototype.map.call(c.querySelectorAll('.smart-was'), function (x) { return x.textContent; }),
             dropped: Array.prototype.map.call(c.querySelectorAll('.smart-dropped'), function (x) { return x.textContent; }),
             edited: c.textContent.indexOf('edited') >= 0, opened: !!c.querySelector('.smart-opened') };
  });
})()"""


def _parse(rt, text, book="P"):
    return st.parse_view(rt, {"text": text, "book": book})


class _Page:
    """The real smartticket.js in one headless tab."""

    def __init__(self, browser):
        self.b = browser
        self.s = browser.page("about:blank")
        self.b.js(self.s, STUB)
        with open(PAGE, encoding="ascii") as f:
            self.b.js(self.s, f.read() + "\n;true")
        self.render()

    def render(self):
        self.b.js(self.s, "T.pages.smart.render(document.getElementById('root'), {}); true")

    def unmount_and_back(self):
        """Open in ticket leaves #/smart (the route unmounts the page); Ctrl+F9 renders it again from the draft."""
        self.b.js(self.s, "T.pages.smart.unmount(); document.getElementById('root').innerHTML = ''; true")
        self.render()

    def answers(self, by_text=None, queue=()):
        self.b.js(self.s, f"window.__answers = {json.dumps(by_text or {})}; window.__queue = {json.dumps(list(queue))}; true")

    def _wait_read(self, n0: int) -> None:
        self.b.wait(self.s, f"window.__posts.length > {n0} && document.getElementById('smart-status').textContent.indexOf('reading') < 0")
        time.sleep(0.15)

    def read(self, text=None):
        """Type ``text`` (or keep the box) and press Enter; wait until the reading is painted."""
        n0 = self.b.js(self.s, "window.__posts.length")
        set_text = f"b.value = {json.dumps(text)}; b.dispatchEvent(new Event('input', {{ bubbles: true }}));" if text is not None else ""
        self.b.js(self.s, "(function () { var b = document.getElementById('smart-text'); " + set_text +
                  " b.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })); return true; })()")
        self._wait_read(n0)

    def edit(self, i, field, value):
        self.b.js(self.s, "(function () { var e = document.querySelector('#smart-cards [data-i=\"%d\"] [data-edit=\"%s\"]'); e.value = %s;"
                          " e.dispatchEvent(new Event('change', { bubbles: true })); return true; })()" % (i, field, json.dumps(value)))
        time.sleep(0.15)

    def click(self, selector, reads=False):
        n0 = self.b.js(self.s, "window.__posts.length")
        self.b.js(self.s, "(function () { var e = document.querySelector(%s); if (!e) { throw new Error('no ' + %s); } e.click(); return true; })()"
                  % (json.dumps(selector), json.dumps(selector)))
        if reads:
            self._wait_read(n0)
        else:
            time.sleep(0.15)

    def choose(self, i, code_words, label):
        """Open card ``i``'s chip whose title holds ``code_words``, then pick the alternative ``label``."""
        n0 = self.b.js(self.s, "window.__posts.length")
        self.b.js(self.s, "(function () { var c = document.querySelector('#smart-cards [data-i=\"%d\"]');"
                          " var chip = Array.prototype.filter.call(c.querySelectorAll('.smart-chip'), function (x) { return x.title.indexOf(%s) >= 0; })[0];"
                          " chip.click(); return true; })()" % (i, json.dumps(code_words)))
        time.sleep(0.1)
        self.b.js(self.s, "(function () { var c = document.querySelector('#smart-cards [data-i=\"%d\"]');"
                          " var btn = Array.prototype.filter.call(c.querySelectorAll('[data-act=\"choice\"]'), function (x) { return x.textContent === %s; })[0];"
                          " btn.click(); return true; })()" % (i, json.dumps(label)))
        self._wait_read(n0)

    def cards(self):
        return self.b.js(self.s, CARDS)

    def note(self, el_id: str) -> str:
        return self.b.js(self.s, "(function () { var e = document.getElementById(%s); return e && !e.hidden ? e.textContent : ''; })()" % json.dumps(el_id))


@unittest.skipIf(find_browser() is None, NO_BROWSER)
class SmartPageCardsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.browser = Browser(find_browser())

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()

    def world(self, w=None):
        tmp, rt = STT._ResolverBase._make()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.addCleanup(rt.close)
        STT.build_world(self, rt, w or corpus.WORLD)
        return rt

    def test_a_the_call_of_a_covered_call_keeps_its_edit_once_the_shares_are_bought(self):
        """Read again after the shares card was sent and filled: the call card reads exactly as before (an order before it is gone) and
        keeps the owner's limit, with no "was" of the shares order (7bc2832 matched it with the shares card by position)."""
        w = dict(corpus.WORLD)
        w["positions"] = [p for p in corpus.WORLD["positions"] if not (p[0] == corpus.P and p[1] == corpus.C240)]
        rt = self.world(w)
        text = "covered call 100 ACME Dec 18 2026 260"
        a1 = _parse(rt, text)
        self.assertEqual([("BUY", "ACME"), ("STO", "ACME261218C00260000")], [(l["side"], l["instrument"]["id"]) for l in a1["legs"]])
        self.assertTrue(buy(rt, corpus.P, "ACME", 100, "230").ok)
        a2 = _parse(rt, text)
        self.assertEqual([("STO", "ACME261218C00260000")], [(l["side"], l["instrument"]["id"]) for l in a2["legs"]])
        pg = _Page(self.browser)
        pg.answers(queue=[a1, a2])
        pg.read(text)
        pg.edit(1, "order_type", "LIMIT")
        pg.edit(1, "limit_px", "1.50")
        pg.read()
        (call,) = pg.cards()
        self.assertEqual(("LIMIT", "1.50", True, [], []), (call["type"], call["limit"], call["edited"], call["was"], call["dropped"]), call)
        self.assertEqual("", pg.note("smart-lost"))

    def test_b_a_close_of_two_contracts_keeps_the_edit_of_the_one_left_and_says_the_one_gone(self):
        rt = self.world()
        text = "close ACME calls"
        a1 = _parse(rt, text)
        self.assertEqual([("BTC", corpus.C240), ("STC", corpus.C250)], [(l["side"], l["instrument"]["id"]) for l in a1["legs"]])
        w2 = dict(corpus.WORLD)
        w2["positions"] = [p for p in corpus.WORLD["positions"] if not (p[0] == corpus.P and p[1] == corpus.C240)]
        a2 = _parse(self.world(w2), text)
        self.assertEqual([("STC", corpus.C250, 3)], [(l["side"], l["instrument"]["id"], l["qty"]) for l in a2["legs"]])
        pg = _Page(self.browser)
        pg.answers(queue=[a1, a2])
        pg.read(text)
        pg.edit(0, "qty", "1")                                              # an edit on the order the new reading no longer has
        pg.edit(1, "order_type", "LIMIT")
        pg.edit(1, "limit_px", "1.10")
        pg.read()
        (left,) = pg.cards()
        self.assertEqual(("LIMIT", "1.10", [], []), (left["type"], left["limit"], left["was"], left["dropped"]), left)
        lost = pg.note("smart-lost")
        self.assertIn('your edit on #1 ("close ACME calls"): qty 1', lost)    # said above the cards, never dropped unsaid

    def test_c_d_a_choice_on_one_card_keeps_another_card_s_edit_and_opened_mark(self):
        """A choice on card 1 makes the choice guard write card 2's carried side out; card 2 reads exactly as before, so its quantity
        edit, and in the second run its opened mark, stay."""
        rt = self.world()
        text = "sell 4 ACME Dec 18 2026 250 calls and 100 WIDG limit 41"
        a1 = _parse(rt, text)
        f = next(x for x in a1["legs"][0]["flags"] if x["code"] == "SIDE_SPLIT")
        ch = next(x for x in f["choices"] if x["label"] == "stc 3 only")
        text2 = a1["text"][:f["span"][0]] + ch["clause"] + a1["text"][f["span"][1]:]
        a2 = _parse(rt, text2)
        self.assertNotEqual(a1["legs"][1]["clause"], a2["legs"][1]["clause"])
        pg = _Page(self.browser)
        pg.answers(by_text={text: a1, text2: a2})
        pg.read(text)
        pg.edit(1, "qty", "50")
        pg.choose(0, "close them and open the rest", "stc 3 only")
        card2 = pg.cards()[1]
        self.assertEqual(("50", True, [], []), (card2["qty"], card2["edited"], card2["was"], card2["dropped"]), card2)
        pg2 = _Page(self.browser)
        pg2.answers(by_text={text: a1, text2: a2})
        pg2.read(text)
        pg2.click('#smart-cards [data-i="1"] [data-act="open"]')
        self.assertEqual(["#/a/P1:US_EXCHANGE/markets"], self.browser.js(pg2.s, "window.__routes"))
        pg2.unmount_and_back()
        self.assertTrue(pg2.cards()[1]["opened"])
        pg2.choose(0, "close them and open the rest", "stc 3 only")
        self.assertTrue(pg2.cards()[1]["opened"], pg2.cards())

    def test_e_control_a_reading_that_changed_nothing_keeps_the_edit(self):
        rt = self.world()
        text = "close ACME calls"
        a1 = _parse(rt, text)
        pg = _Page(self.browser)
        pg.answers(queue=[a1, a1])
        pg.read(text)
        pg.edit(1, "order_type", "LIMIT")
        pg.edit(1, "limit_px", "1.10")
        pg.read()
        cards = pg.cards()
        self.assertEqual(("LIMIT", "1.10", True, []), (cards[1]["type"], cards[1]["limit"], cards[1]["edited"], cards[1]["was"]), cards)

    def test_f_the_text_is_read_for_the_book_of_the_last_reading(self):
        """Open in ticket on an order of another book selects that book (SHELL-02).  Back on the page, Enter reads the text for the book
        of the last reading: the order that names no book stays in the Personal Book with its edit, and the page says another book is
        selected.  "Read for the Fund Book" reads it there: the card says its account was the Personal Book's and its edit is dropped,
        said on it (7bc2832 moved it to the Fund Book with no "was" and lost the edit unsaid)."""
        rt = self.world()
        text = "buy 5 WIDG limit 249 in the Fund book; buy 10 ACME limit 50"
        a_p, a_f = _parse(rt, text, "P"), _parse(rt, text, "F")
        self.assertEqual([corpus.F, corpus.P], [l["account_id"] for l in a_p["legs"]])
        self.assertEqual([corpus.F, corpus.F], [l["account_id"] for l in a_f["legs"]])
        pg = _Page(self.browser)
        pg.answers(queue=[a_p, a_p, a_f])
        pg.read(text)
        pg.edit(1, "qty", "12")
        pg.click('#smart-cards [data-i="0"] [data-act="open"]')
        self.browser.js(pg.s, "T.book.code = 'F'; true")                    # the router selected the Fund Book
        pg.unmount_and_back()
        self.assertIn("the last reading was for the Personal Book; the Fund Book is selected now", pg.note("smart-book"))
        pg.read()
        self.assertEqual(["P", "P"], self.browser.js(pg.s, "window.__posts.map(function (p) { return p.body.book; })"))
        cards = pg.cards()
        self.assertEqual((True, "12", [], []), (cards[0]["opened"], cards[1]["qty"], cards[1]["was"], cards[1]["dropped"]), cards)
        self.assertIn("Personal Book", cards[1]["where"])
        pg.click("#smart-rebook", reads=True)
        self.assertEqual("F", self.browser.js(pg.s, "window.__posts[2].body.book"))
        self.assertEqual("", pg.note("smart-book"))
        cards = pg.cards()
        self.assertIn("Fund Book", cards[1]["where"])
        self.assertIn("was Personal Book . US Exchange", cards[1]["was"])
        self.assertEqual(["your edit: qty 12 - dropped, read again as 10"], cards[1]["dropped"])
        self.assertTrue(cards[0]["opened"])                                  # the WIDG order reads as before: its mark stays

    def test_g_a_click_on_open_in_ticket_right_after_typing_in_a_card_opens_it(self):
        """A real press and release on Open in ticket right after typing 12 in the card's quantity (no Tab): the change event comes on
        the press; the cards are painted again only once the pointer is up, so the click lands and opens the card with the edit."""
        rt = self.world()
        text = "buy 10 ACME limit 50"
        a1 = _parse(rt, text)
        pg = _Page(self.browser)
        pg.answers(by_text={text: a1})
        pg.read(text)
        b, s = self.browser, pg.s
        b.js(s, "(function () { var x = document.querySelector('#smart-cards [data-i=\"0\"] [data-edit=\"qty\"]'); x.focus(); x.select(); return true; })()")
        b.call("Input.insertText", {"text": "12"}, session=s)
        r = b.js(s, "(function () { var e = document.querySelector('#smart-cards [data-i=\"0\"] [data-act=\"open\"]'); e.scrollIntoView(); var r = e.getBoundingClientRect();"
                    " return { x: r.left + r.width / 2, y: r.top + r.height / 2, disabled: e.disabled }; })()")
        self.assertFalse(r["disabled"], r)
        ev = {"x": r["x"], "y": r["y"], "button": "left", "clickCount": 1}
        b.call("Input.dispatchMouseEvent", dict(ev, type="mouseMoved", button="none", clickCount=0), session=s)
        b.call("Input.dispatchMouseEvent", dict(ev, type="mousePressed", buttons=1), session=s)
        time.sleep(0.12)
        b.call("Input.dispatchMouseEvent", dict(ev, type="mouseReleased", buttons=0), session=s)
        time.sleep(0.3)
        self.assertEqual(["#/a/P1:US_EXCHANGE/markets"], b.js(s, "window.__routes"))
        card = pg.cards()[0]
        self.assertEqual(("12", True, True), (card["qty"], card["edited"], card["opened"]), card)


if __name__ == "__main__":
    unittest.main()
