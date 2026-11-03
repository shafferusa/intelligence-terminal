"""FinSim Terminal 1.0.7.1: the owner's fill mode (Settings > General, setting ``fill_mode``).

The owner on 2026-11-03: "autofill these and give me the option" ("this is a simulation and you can edit the code").  REALISTIC is
the default and exactly 1.0.7: each new 1-minute bar lets at most the participation rate of its volume fill, an option fills at most
the displayed size.  AUTOFILL lifts only those two caps: an order that is executable now fills its whole remaining quantity in one
fill, and the fill's cap note starts with "autofill (owner setting): no size or volume cap".  Prices, limits, stops, sessions and
staleness are untouched: no price is invented, a stale or missing quote still blocks, a limit that is not marketable still rests.

Every expected number is worked by hand in a comment.  Run from the folder that holds the ``finsim`` and ``tests`` packages
(``r1071/src/finsim``):

    py -3.12 -m unittest tests.test_fill_mode_1071 -v
    py -3.14 -m unittest tests.test_fill_mode_1071 -v

Nothing here binds a port, touches the network or ``~/.finsim``: every runtime lives in a temp folder (FINSIM_HOME and
FINSIM_TERMINAL_DB point at it) with the FakeClock and the FakeProvider.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from decimal import Decimal

from tests.terminal_helpers import US_EXCHANGE, _Log, assert_integrity, deposit, execute, fund
from tests.test_terminal_orders import (TUE_1400, TUE_CLOSE, FakeCache, FakeOptions, chain_rec, ensure_option, matcher_for, one, order_row, q, rec, row,
                                        runtime, sess, submit)
from finsim.terminal import api as _api, config, orders, orders_rules, server as _server, tables
from finsim.terminal.clock import FakeClock
from finsim.terminal.db import get_setting, set_setting
from finsim.terminal.orders_preview import BarUsage, bar_usage, fill_mode_of
from finsim.terminal.orders_rules import (AUTOFILL_NOTE, FILL_MODE_AUTOFILL, FILL_MODE_REALISTIC, FILL_MODES, evaluate, evaluate_package, fill_cap,
                                          fill_mode_value, resolve_fill_mode, review)
from finsim.terminal.quotes.providers import FakeProvider

P = Decimal("0.10")                     # the default participation rate
A = FILL_MODE_AUTOFILL
R = FILL_MODE_REALISTIC
OCC = "SPY261120P00700000"
HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_JS = os.path.join(os.path.dirname(HERE), "finsim", "terminal", "static", "pages", "settings.js")


def note(remaining_words: str, realistic: str) -> str:
    """The AUTOFILL cap note: the disclosure, the whole remaining quantity in one fill, what Realistic would fill now."""
    return f"{AUTOFILL_NOTE}; the whole remaining {remaining_words} in one fill (Realistic would fill {realistic} now)"


class Carrier:
    """What ``orders_preview.bar_usage(conn)`` hands over: the bar usage when called, the owner's fill mode as an attribute."""

    def __init__(self, fill_mode, used=0):
        self.fill_mode, self.used = fill_mode, used

    def __call__(self, instrument_id, bar_t):
        return self.used


# ===== the pure rules: no runtime, no database ==========================================================================================
class FillCapTests(unittest.TestCase):
    now = TUE_1400

    def r(self, last, **kw):
        return rec("AAPL", last, self.now - 120, self.now, **kw)

    def cases(self):
        """(order, record, bar_used) -> the 1.0.7 cap, worked by hand: 10 % x 120,000 = 12,000; the same bar again -> 0 NO_NEW_BAR;
        a zero-volume bar -> 0; 12,000 already taken from the bar -> 0 BAR_CAP_USED; no bar -> all 50,000; lot 100 with 61,900 ->
        floor(6,190 / 100) x 100 = 6,100; a BTO 25 against an ask size of 10 -> 10; an ask size of 0 -> 0; FX -> all 100,000."""
        t1 = self.now - 120
        fx = rec("EURUSD", "1.1238", self.now - 60, self.now, mid="1.1238", group="FX_SPOT")
        return [
            (row(qty=50000), self.r("769.32", bar=(t1, 120000)), None, 12000),
            (row(qty=50000, filled=12000, last_bar_ts=t1), self.r("769.32", bar=(t1, 120000)), None, 0),
            (row(qty=50000), self.r("769.32", bar=(t1, 0)), None, 0),
            (row(qty=50000), self.r("769.32", bar=(t1, 120000)), 12000, 0),
            (row(qty=50000), self.r("769.32"), None, 50000),
            (row(qty=50000, lot=100), self.r("339.50", bar=(t1, 61900)), None, 6100),
            (row(side="BTO", qty=25, kind="OPTION", iid=OCC), chain_rec(OCC, "2.00", "2.10", 50, 10, self.now - 60, self.now), None, 10),
            (row(side="BTO", qty=5, kind="OPTION", iid=OCC), chain_rec(OCC, "2.00", "2.10", 50, 0, self.now - 60, self.now), None, 0),
            (row(side="BUY", qty=100000, kind="FX", iid="EURUSD", tick="0.0001", expires=self.now + 3600), fx, None, 100000),
        ]

    def test_realistic_is_1_0_7_exactly(self):
        for o, rc, used, want in self.cases():
            base = fill_cap(o, rc, None, P, used)
            self.assertEqual(want, base[0], base[1])
            self.assertFalse(base[1].startswith(AUTOFILL_NOTE))
            self.assertEqual(base, orders_rules._realistic_cap(o, rc, None, P, used))         # the 1.0.7 body, unchanged
            for mode in (R, "realistic", "SEQUENTIAL", "AON", "", "AUTO"):                       # never AUTOFILL: an order's package mode, a typo
                self.assertEqual(base, fill_cap(o, rc, None, P, used, fill_mode=mode), mode)
            self.assertEqual(base, fill_cap(o, rc, None, P, Carrier(R, used or 0)))              # a carrier saying REALISTIC
            self.assertEqual(base, fill_cap(o, rc, None, P, lambda i, t, u=used: u or 0))        # a bare function carries no mode

    def test_autofill_lifts_the_participation_cap(self):
        t1 = self.now - 120
        bar = self.r("769.32", bar=(t1, 120000))
        # 50,000 against a 120,000 bar: Realistic 12,000 (10 %), Autofill all 50,000
        self.assertEqual((50000, note("50,000 shares", "12,000")), fill_cap(row(qty=50000), bar, None, P, fill_mode=A))
        # 12,000 filled on this bar already: Realistic 0 (NO_NEW_BAR), Autofill the remaining 50,000 - 12,000 = 38,000
        self.assertEqual((38000, AUTOFILL_NOTE + "; the whole remaining 38,000 shares in one fill (Realistic would fill 0 now: no new 1-minute bar since the"
                                  " last fill)"), fill_cap(row(qty=50000, filled=12000, last_bar_ts=t1), bar, None, P, fill_mode=A))
        # a completed bar with no volume: Realistic 0 (NO_VOLUME_THIS_BAR), Autofill 50,000
        cap, n = fill_cap(row(qty=50000), self.r("769.32", bar=(t1, 0)), None, P, fill_mode=A)
        self.assertEqual((50000, AUTOFILL_NOTE + "; the whole remaining 50,000 shares in one fill (Realistic would fill 0 now: the last completed 1-minute bar"
                                  " traded no volume)"), (cap, n))
        # other fills took the whole 12,000 of this bar: Realistic 0 (BAR_CAP_USED), Autofill 50,000; 5,000 taken: Realistic 7,000
        cap, n = fill_cap(row(qty=50000), bar, None, P, 12000, fill_mode=A)
        self.assertEqual(50000, cap)
        self.assertIn("(Realistic would fill 0 now: this 1-minute bar's participation cap is used up by other fills)", n)
        self.assertEqual((50000, note("50,000 shares", "7,000")), fill_cap(row(qty=50000), bar, None, P, 5000, fill_mode=A))
        # no bar at all: Realistic already fills in full (NO_VOLUME); Autofill says so with its disclosure
        self.assertEqual((50000, note("50,000 shares", "50,000")), fill_cap(row(qty=50000), self.r("769.32"), None, P, fill_mode=A))
        # lots: 50,000 in lots of 100 -> 50,000 (Realistic 6,100); futures are counted in contracts: 20 against a 50-lot bar (Realistic 5)
        self.assertEqual(50000, fill_cap(row(qty=50000, lot=100), self.r("339.50", bar=(t1, 61900)), None, P, fill_mode=A)[0])
        self.assertEqual((20, note("20 contracts", "5")), fill_cap(row(qty=20, kind="FUTURE", iid="ESZ26"), self.r("6900.25", bar=(t1, 50)), None, P, fill_mode=A))
        self.assertEqual((1, AUTOFILL_NOTE + "; the whole remaining 1 share in one fill (Realistic would fill 0 now: the last completed 1-minute bar traded no"
                             " volume)"), fill_cap(row(qty=1), self.r("769.32", bar=(t1, 0)), None, P, fill_mode=A))
        # nothing left to fill: nothing to autofill
        self.assertEqual(0, fill_cap(row(qty=100, filled=100), bar, None, P, fill_mode=A)[0])

    def test_autofill_lifts_the_displayed_size_cap_only(self):
        c = chain_rec(OCC, "2.00", "2.10", 5, 5, self.now - 60, self.now)                    # bid 2.00 x 5, ask 2.10 x 5
        self.assertEqual((40, note("40 contracts", "5")), fill_cap(row(side="BTO", qty=40, kind="OPTION", iid=OCC), c, None, P, fill_mode=A))
        self.assertEqual((40, note("40 contracts", "5")), fill_cap(row(side="STC", qty=40, kind="OPTION", iid=OCC), c, None, P, fill_mode=A))
        zero = chain_rec(OCC, "2.00", "2.10", 0, 0, self.now - 60, self.now)
        self.assertEqual((40, AUTOFILL_NOTE + "; the whole remaining 40 contracts in one fill (Realistic would fill 0 now: no displayed size on the executable"
                              " side)"), fill_cap(row(side="STC", qty=40, kind="OPTION", iid=OCC), zero, None, P, fill_mode=A))
        none = chain_rec(OCC, "2.00", "2.10", None, None, self.now - 60, self.now)
        self.assertEqual((40, note("40 contracts", "40")), fill_cap(row(side="BTO", qty=40, kind="OPTION", iid=OCC), none, None, P, fill_mode=A))
        # an option never fills twice on one observation (SAME_OBSERVATION): 5 filled on this chain time -> still 0 in Autofill
        o = row(side="BTO", qty=40, filled=5, kind="OPTION", iid=OCC)
        o.last_quote_time = self.now - 60
        self.assertEqual((0, "this observation already filled the order (no newer chain time): SAME_OBSERVATION"), fill_cap(o, c, None, P, fill_mode=A))
        # FX is unchanged (no volume concept: it fills in full either way)
        fx = rec("EURUSD", "1.1238", self.now - 60, self.now, mid="1.1238", group="FX_SPOT")
        f = row(side="BUY", qty=100000, kind="FX", iid="EURUSD", tick="0.0001", expires=self.now + 3600)
        self.assertEqual((100000, "FX has no volume concept: fills in full (disclosed)"), fill_cap(f, fx, None, P, fill_mode=A))

    def test_the_mode_a_cap_applies(self):
        for v in ("AUTOFILL", "autofill", " Autofill "):
            self.assertEqual(A, fill_mode_value(v), v)
        for v in (None, "", "REALISTIC", "realistic", "SEQUENTIAL", "AON", "AUTO", "AUTO FILL", True, 1, ["AUTOFILL"], {"fill_mode": "AUTOFILL"}):
            self.assertEqual(R, fill_mode_value(v), repr(v))
        self.assertEqual((R, A), FILL_MODES)
        bar = self.r("769.32", bar=(self.now - 120, 120000))
        self.assertEqual(A, resolve_fill_mode(None, Carrier(A)))
        self.assertEqual(R, resolve_fill_mode(R, Carrier(A)))                                    # an explicit mode wins over the carrier
        self.assertEqual(R, resolve_fill_mode(None, 12000))
        self.assertEqual(50000, fill_cap(row(qty=50000), bar, None, P, Carrier(A))[0])           # the carrier's mode applies
        self.assertEqual(12000, fill_cap(row(qty=50000), bar, None, P, Carrier(A), fill_mode=R)[0])
        self.assertEqual(0, fill_cap(row(qty=50000), bar, None, P, Carrier(R, 12000))[0])         # its usage still counts in Realistic

    def test_evaluate_autofill_keeps_every_price_and_gate(self):
        s = sess()
        t1 = self.now - 120
        bar = self.r("769.32", bar=(t1, 120000))
        d = evaluate(row(qty=50000), bar, s, self.now, fill_mode=A)
        self.assertEqual(("FILL", Decimal("769.32"), 50000, "last", "MARKET", t1), (d.action, d.price, d.qty, d.price_basis, d.reason, d.bar_ts))
        self.assertEqual(note("50,000 shares", "12,000"), d.cap_note)
        self.assertEqual(12000, evaluate(row(qty=50000), bar, s, self.now).qty)                  # Realistic, as in 1.0.7
        d = evaluate(row(qty=50000, filled=12000, last_bar_ts=t1), bar, s, self.now, fill_mode=A)
        self.assertEqual(("FILL", 38000), (d.action, d.qty))
        self.assertEqual(("NONE", "NO_NEW_BAR"), (lambda x: (x.action, x.reason))(evaluate(row(qty=50000, filled=12000, last_bar_ts=t1), bar, s, self.now)))
        # the gates never move: stale, computed, a previous session's print, no quote, a closed session, past expiry
        stale = rec("AAPL", "769.32", self.now - 1500, self.now, bar=(self.now - 1560, 120000))   # 1,500 s > 900 + 300
        self.assertEqual(("NONE", "STALE", 0), (lambda x: (x.action, x.reason, x.qty))(evaluate(row(qty=50000), stale, s, self.now, fill_mode=A)))
        self.assertEqual("COMPUTED_NOT_EXECUTABLE", evaluate(row(), self.r("339.50", method="model"), s, self.now, fill_mode=A).reason)
        self.assertEqual("NO_QUOTE", evaluate(row(), None, s, self.now, fill_mode=A).reason)
        self.assertEqual("AWAITING_SESSION", evaluate(row(), self.r("339.50"), sess(False), self.now, fill_mode=A).reason)
        self.assertEqual("EXPIRED_PENDING", evaluate(row(expires=self.now - 1), self.r("339.50"), s, self.now, fill_mode=A).reason)
        # LIMIT_RULE: BUY 5,000 limit 339.00; a 1,000-share bar (Realistic cap 100)
        small = (t1, 1000)
        d = evaluate(row(qty=5000, otype="LIMIT", limit="339.00"), self.r("339.20", bar=small), s, self.now, fill_mode=A)
        self.assertEqual(("NONE", "NOT_MARKETABLE", 0, True), (d.action, d.reason, d.qty, d.rested))          # 339.20 > 339.00: it rests
        d = evaluate(row(qty=5000, otype="LIMIT", limit="339.00", first=True), self.r("339.20", bar=small), s, self.now, fill_mode=A)
        self.assertEqual(("NONE", "NOT_MARKETABLE"), (d.action, d.reason))
        d = evaluate(row(qty=5000, otype="LIMIT", limit="339.00"), self.r("338.90", bar=small), s, self.now, fill_mode=A)
        self.assertEqual(("FILL", Decimal("338.9"), 5000, "LIMIT_MARKETABLE"), (d.action, d.price, d.qty, d.reason))   # at the observed price
        d = evaluate(row(qty=5000, otype="LIMIT", limit="339.00", first=True), self.r("338.95", bar=small), s, self.now, fill_mode=A)
        self.assertEqual(("FILL", Decimal("339"), 5000, "limit", "LIMIT_CROSSED"), (d.action, d.price, d.qty, d.price_basis, d.reason))  # resting: the limit
        # STOP_RULE: BUY STOP 340.00 - not reached at 339.50; 340.10 triggers and fills all 5,000 at that print (Realistic 100)
        self.assertEqual("STOP_NOT_REACHED", evaluate(row(qty=5000, otype="STOP", stop="340.00"), self.r("339.50", bar=small), s, self.now, fill_mode=A).reason)
        d = evaluate(row(qty=5000, otype="STOP", stop="340.00"), self.r("340.10", bar=small), s, self.now, fill_mode=A)
        self.assertEqual(("FILL", Decimal("340.1"), 5000, "stop_print", True), (d.action, d.price, d.qty, d.price_basis, d.triggered))
        self.assertIn("gap through the stop 340", d.note)

    def test_evaluate_autofill_options_at_the_bid_and_ask(self):
        s = sess(group="US_EQUITY_OPTIONS")
        c = chain_rec(OCC, "2.00", "2.10", 5, 5, self.now - 60, self.now)
        d = evaluate(row(side="STC", qty=40, kind="OPTION", iid=OCC), c, s, self.now, fill_mode=A)
        self.assertEqual(("FILL", Decimal("2"), 40, "bid"), (d.action, d.price, d.qty, d.price_basis))           # in full at the bid
        self.assertEqual(5, evaluate(row(side="STC", qty=40, kind="OPTION", iid=OCC), c, s, self.now).qty)       # Realistic: the bid size
        d = evaluate(row(side="BTO", qty=40, kind="OPTION", iid=OCC), c, s, self.now, fill_mode=A)
        self.assertEqual(("FILL", Decimal("2.1"), 40, "ask"), (d.action, d.price, d.qty, d.price_basis))         # in full at the ask
        # no usable NBBO still blocks; a second fill on the same chain time still waits; no opening trade after 15:30 ET on expiry day
        bad = chain_rec(OCC, "0", "2.10", 5, 5, self.now - 60, self.now)
        self.assertEqual(("NONE", "NO_NBBO"), (lambda x: (x.action, x.reason))(evaluate(row(side="BTO", qty=40, kind="OPTION", iid=OCC), bad, s, self.now, fill_mode=A)))
        o = row(side="BTO", qty=40, filled=5, kind="OPTION", iid=OCC, status="PARTIAL")
        o.last_quote_time = self.now - 60
        self.assertEqual(("NONE", "SAME_OBSERVATION"), (lambda x: (x.action, x.reason))(evaluate(o, c, s, self.now, fill_mode=A)))
        late = TUE_CLOSE - 1740                                                                 # 15:31 ET on 2026-10-20
        o = row(side="STO", qty=10, kind="OPTION", iid=OCC)
        o.expiry = "2026-10-20"
        self.assertEqual("LAST_SESSION", evaluate(o, chain_rec(OCC, "2.00", "2.10", 50, 50, late - 60, late), s, late, fill_mode=A).reason)

    def test_review_and_packages(self):
        s = sess()
        bar = self.r("769.32", bar=(self.now - 120, 120000))
        rv = review(row(qty=50000), bar, s, self.now, fill_mode=A)
        self.assertEqual(("would fill 50,000 at 769.32 now (last)", note("50,000 shares", "12,000")), (rv["text"], rv["cap_note"]))
        self.assertEqual("would fill 12,000 at 769.32 now (last)", review(row(qty=50000), bar, s, self.now)["text"])
        self.assertEqual("would fill 50,000 at 769.32 now (last)", review(row(qty=50000), bar, s, self.now, bar_used=Carrier(A))["text"])
        # an all-or-none package still needs the displayed size for every leg (AON_SIZE): it is not a fill_cap decision
        so = sess(group="US_EQUITY_OPTIONS")
        legs = [row(side="BTO", qty=10, kind="OPTION", iid="P700"), row(side="STO", qty=10, kind="OPTION", iid="P730")]
        legs[0].order_id, legs[1].order_id = "O-1", "O-2"
        qt = self.now - 60
        short = {"P700": chain_rec("P700", "2.00", "2.10", 50, 50, qt, self.now), "P730": chain_rec("P730", "4.00", "4.10", 5, 50, qt, self.now)}
        self.assertEqual(("NONE", "AON_SIZE", "O-2"), (lambda x: (x.action, x.reason, x.leg))(evaluate_package({"package_id": "PK"}, legs, short, so, self.now)))

    def test_the_autofill_convention(self):
        text = tables.CONVENTIONS["AUTOFILL"]
        text.encode("ascii")
        self.assertNotIn("\n", text)
        self.assertIn("'" + AUTOFILL_NOTE + "'", text)
        self.assertIn("Realistic is the default", text)
        self.assertEqual("autofill (owner setting): no size or volume cap", AUTOFILL_NOTE)        # the wording the blotter shows


# ===== through the bus and the matcher (a temp home; the WP-02 scaffolding of test_terminal_orders) =====================================
class FillModeScenarioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-fm1071-")
        self.rt = None

    def tearDown(self):
        if self.rt is not None:
            self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start(self, at="2026-10-20T14:00:00-04:00"):
        self.rt = runtime(self.tmp, at=at)
        self.rt.options = FakeOptions(self.rt)
        orders.bind(self.rt)                             # the order-book columns the server adds (limit_rested, ...), as on the live file
        _api.register(self.rt.bus)                       # settings.set, as the server registers it
        return self.rt

    def set_mode(self, mode):
        res = execute(self.rt, "settings.set", {"fill_mode": mode})
        self.assertTrue(res.ok, res.body)
        return res

    def test_the_setting_on_a_connection(self):
        rt = self.start()
        now = rt.clock.epoch()
        with rt.db.reader() as conn:
            self.assertEqual(R, get_setting(conn, "fill_mode"))              # a new file is seeded REALISTIC
            self.assertEqual((R, R), (fill_mode_of(conn), bar_usage(conn).fill_mode))
        self.set_mode("autofill")
        with rt.db.reader() as conn:
            self.assertEqual(A, get_setting(conn, "fill_mode"))              # stored upper-case
            u = bar_usage(conn)
            self.assertIsInstance(u, BarUsage)
            self.assertEqual((A, 0), (u.fill_mode, u("AAPL", now - 180)))
            bar = rec("AAPL", "339.50", now - 120, now, bar=(now - 180, 120000))
            self.assertEqual(50000, fill_cap(row(qty=50000), bar, None, P, u)[0])           # Realistic would be 12,000
            self.assertEqual("would fill 50,000 at 339.50 now (last)", review(row(qty=50000), bar, sess(), now, bar_used=u)["text"])
        # a database from before 1.0.7.1 (no row), a stored value Settings would refuse, a JSON true: all REALISTIC
        for stored in (None, "maybe", True, "SEQUENTIAL"):
            with rt.db.writer() as tx:
                if stored is None:
                    tx.conn.execute("DELETE FROM settings WHERE key='fill_mode'")
                else:
                    set_setting(tx.conn, "fill_mode", stored)
            with rt.db.reader() as conn:
                self.assertEqual(R, fill_mode_of(conn), repr(stored))
        c = sqlite3.connect(":memory:")                                   # no settings table at all
        try:
            self.assertEqual(R, fill_mode_of(c))
        finally:
            c.close()

    def test_partial_then_autofill_fills_the_rest_in_one_fill(self):
        # MARKET BUY 50,000 SPY (38,466,000 notional: deposit 40,000,000 first -> Treasury 50,000,000; > 25 % of NAV -> confirm_large)
        rt = self.start()
        deposit(rt, 4000000000)
        now = rt.clock.epoch()
        t1, t2 = now - 180, now - 120
        first = rec("SPY", "769.32", now - 90, now, bar=(t1, 120000))
        rt.marks.set_quote(first)
        r = submit(rt, account_id=US_EXCHANGE, instrument_id="SPY", side="BUY", qty=50000, order_type="MARKET", confirm_large=True)
        self.assertTrue(r.ok, r.body)
        oid = r.body["order"]["order_id"]
        # Realistic: 10 % x 120,000 = 12,000 -> PARTIAL 12,000 on bar t1
        self.assertEqual(("PARTIAL", 12000, t1), (r.body["order"]["status"], r.body["order"]["filled_qty"], r.body["order"]["last_bar_ts"]))
        self.assertFalse(one(rt, "SELECT cap_note FROM fills WHERE order_id=?", (oid,))["cap_note"].startswith(AUTOFILL_NOTE))
        self.set_mode(A)
        cache = FakeCache({"SPY": first})
        m = matcher_for(rt, cache)
        # the very observation the 12,000 filled on: never a second fill on it, and no command that could only say ALREADY_APPLIED
        self.assertEqual(0, m.on_quotes(["SPY"]))
        self.assertEqual((1, "PARTIAL", 0), (one(rt, "SELECT COUNT(*) n FROM fills")["n"], order_row(rt, oid)["status"], m.errors))
        # a newer observation on the same bar (Realistic: NO_NEW_BAR) at 769.40 -> ONE fill of the whole 38,000 at that print
        cache["SPY"] = rec("SPY", "769.40", now - 30, now, bar=(t1, 120000))
        self.assertEqual(1, m.on_quotes(["SPY"]))
        fills = q(rt, "SELECT qty, price, price_basis, cap_note, bar_ts, idem_key FROM fills WHERE order_id=? ORDER BY fill_id", (oid,))
        self.assertEqual([12000, 38000], [f["qty"] for f in fills])
        self.assertEqual((Decimal("769.40"), "last", t1, f"match:{oid}:{now - 30}:{t1}"),
                         (Decimal(fills[1]["price"]), fills[1]["price_basis"], fills[1]["bar_ts"], fills[1]["idem_key"]))
        self.assertEqual(AUTOFILL_NOTE + "; the whole remaining 38,000 shares in one fill (Realistic would fill 0 now: no new 1-minute bar since the last fill)",
                         fills[1]["cap_note"])
        # avg = (12,000 x 769.32 + 38,000 x 769.40) / 50,000 = (9,231,840 + 29,237,200) / 50,000 = 769.3808
        o = order_row(rt, oid)
        self.assertEqual(("FILLED", 50000, Decimal("769.3808")), (o["status"], o["filled_qty"], Decimal(o["avg_px"])))
        ev = one(rt, "SELECT note FROM order_events WHERE order_id=? ORDER BY id DESC LIMIT 1", (oid,))["note"]
        self.assertTrue(ev.startswith("filled 38,000 @ "), ev)
        self.assertIn(AUTOFILL_NOTE, ev)                                   # the order timeline says so too
        # the same observation again: replay-safe, nothing more (the order is FILLED)
        self.assertEqual(0, m.on_quotes(["SPY"]))
        self.assertEqual((2, 1, 0), (one(rt, "SELECT COUNT(*) n FROM fills")["n"], one(rt, "SELECT COUNT(*) n FROM commands WHERE kind='order.match'")["n"], m.errors))
        assert_integrity(self, rt, "full")
        # back to Realistic: the caps are back.  BUY 10,000 on bar t1, whose 12,000 cap the 50,000 above used up -> BAR_CAP_USED, nothing
        self.set_mode(R)
        r = submit(rt, account_id=US_EXCHANGE, instrument_id="SPY", side="BUY", qty=10000, order_type="MARKET")
        self.assertTrue(r.ok, r.body)
        oid2 = r.body["order"]["order_id"]
        self.assertEqual(("WORKING", 0, "BAR_CAP_USED"), (r.body["order"]["status"], r.body["order"]["filled_qty"], r.body["decision"]["reason"]))
        # a new bar t2 of 50,000 -> 10 % = 5,000 -> PARTIAL 5,000 with the participation note
        cache["SPY"] = rec("SPY", "769.50", now - 20, now, bar=(t2, 50000))
        self.assertEqual(1, m.on_quotes(["SPY"]))
        o2 = order_row(rt, oid2)
        f2 = one(rt, "SELECT qty, cap_note FROM fills WHERE order_id=?", (oid2,))
        self.assertEqual(("PARTIAL", 5000, 5000), (o2["status"], o2["filled_qty"], f2["qty"]))
        self.assertIn("10 % participation caps it at 5,000 shares", f2["cap_note"])
        assert_integrity(self, rt, "full")

    def test_options_fill_in_full_at_the_ask_and_the_bid(self):
        rt = self.start()
        fund(rt, US_EXCHANGE, 10000000)                                    # 100,000.00
        occ = ensure_option(rt, OCC).instrument_id
        rt.marks.set(occ, "2.05")                                          # valuation mark (mid)
        now = rt.clock.epoch()
        self.set_mode(A)
        rt.marks.set_quote(chain_rec(occ, "2.00", "2.10", 5, 5, now - 60, now))         # bid 2.00 x 5, ask 2.10 x 5
        r = submit(rt, account_id=US_EXCHANGE, instrument_id=occ, side="BTO", qty=40, order_type="MARKET")
        self.assertTrue(r.ok, r.body)
        # Realistic would take 5 at the ask; Autofill takes all 40 at the ask 2.10: 40 x 2.10 x 100 = 8,400.00 gross, fee 40 x 0.65 = 26.00
        self.assertEqual(("FILLED", 40, 1), (r.body["order"]["status"], r.body["order"]["filled_qty"], len(r.body["fills"])))
        self.assertEqual((840000, 2600), (r.body["fills"][0]["gross_c"], r.body["fills"][0]["fee_c"]))
        f = one(rt, "SELECT price, price_basis, cap_note FROM fills WHERE order_id=?", (r.body["order"]["order_id"],))
        self.assertEqual((Decimal("2.10"), "ask", note("40 contracts", "5")), (Decimal(f["price"]), f["price_basis"], f["cap_note"]))
        # STC 40 against a displayed bid of 5: all 40 at the bid 2.00 in one fill: 40 x 2.00 x 100 = 8,000.00
        rt.marks.set_quote(chain_rec(occ, "2.00", "2.10", 5, 5, now - 30, now))
        r = submit(rt, account_id=US_EXCHANGE, instrument_id=occ, side="STC", qty=40, order_type="MARKET")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("FILLED", 40, 1, 800000), (r.body["order"]["status"], r.body["order"]["filled_qty"], len(r.body["fills"]), r.body["fills"][0]["gross_c"]))
        f = one(rt, "SELECT price, price_basis, cap_note FROM fills WHERE order_id=?", (r.body["order"]["order_id"],))
        self.assertEqual((Decimal("2.00"), "bid", note("40 contracts", "5")), (Decimal(f["price"]), f["price_basis"], f["cap_note"]))
        assert_integrity(self, rt, "full")

    def test_stale_quote_blocks_and_a_limit_still_rests(self):
        rt = self.start(at="2026-10-20T10:30:00-04:00")
        fund(rt, US_EXCHANGE, 50000000)                                    # 500,000.00
        self.set_mode(A)
        now = rt.clock.epoch()
        stale = rec("AAPL", "339.50", now - 1500, now)                     # 1,500 s > 900 + 300: stale
        rt.marks.set_quote(stale)
        r = submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=1000, order_type="MARKET")
        self.assertEqual((409, "QUOTE_STALE"), (r.http, r.code))          # a stale quote still refuses a market order
        r = submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=1000, order_type="LIMIT", limit_px="339.00")
        self.assertTrue(r.ok, r.body)
        oid = r.body["order"]["order_id"]
        self.assertEqual(("WORKING", 0, "STALE"), (r.body["order"]["status"], r.body["order"]["filled_qty"], r.body["decision"]["reason"]))
        cache = FakeCache({"AAPL": stale})
        m = matcher_for(rt, cache)
        self.assertEqual(0, m.on_quotes(["AAPL"]))                         # still stale: no command, no fill
        # fresh, 339.20 > 339.00: not marketable -> it rests (the first look is recorded; nothing fills)
        cache["AAPL"] = rec("AAPL", "339.20", now - 60, now, bar=(now - 120, 500))
        self.assertEqual(1, m.on_quotes(["AAPL"]))
        self.assertEqual(("WORKING", 0, 1), (lambda o: (o["status"], o["filled_qty"], o["first_eval_done"]))(order_row(rt, oid)))
        # 338.95 crosses the resting limit: all 1,000 at the LIMIT 339.00 in one fill (Realistic: 10 % of 500 = 50)
        cache["AAPL"] = rec("AAPL", "338.95", now - 30, now, bar=(now - 60, 500))
        self.assertEqual(1, m.on_quotes(["AAPL"]))
        f = q(rt, "SELECT qty, price, price_basis, cap_note FROM fills WHERE order_id=?", (oid,))
        self.assertEqual([(1000, Decimal("339"), "limit", note("1,000 shares", "50"))], [(x["qty"], Decimal(x["price"]), x["price_basis"], x["cap_note"]) for x in f])
        self.assertEqual("FILLED", order_row(rt, oid)["status"])
        assert_integrity(self, rt, "full")

    def test_matcher_waits_for_a_new_observation_after_a_realistic_look(self):
        # a LIMIT rests unseen (stale at submit); its first look comes from the matcher on a zero-volume bar: a command, no fill.  Switched to
        # Autofill on that same observation, the matcher must not re-use the spent request id (409 REQUEST_ID_REUSED, a back-off)
        rt = self.start(at="2026-10-20T10:30:00-04:00")
        fund(rt, US_EXCHANGE, 50000000)
        now = rt.clock.epoch()
        rt.marks.set_quote(rec("AAPL", "339.50", now - 1500, now))
        r = submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=1000, order_type="LIMIT", limit_px="340.00")
        oid = r.body["order"]["order_id"]
        cache = FakeCache({"AAPL": rec("AAPL", "339.50", now - 60, now, bar=(now - 120, 0))})
        m = matcher_for(rt, cache)
        self.assertEqual(1, m.on_quotes(["AAPL"]))                         # Realistic: NO_VOLUME_THIS_BAR on the first look
        self.assertEqual((0, 1), (lambda o: (o["filled_qty"], o["first_eval_done"]))(order_row(rt, oid)))
        self.set_mode(A)
        self.assertEqual(0, m.on_quotes(["AAPL"]))                         # the same observation: its id is spent -> wait, quietly
        self.assertEqual((0, 0, {}), (order_row(rt, oid)["filled_qty"], m.errors, m.backoff()))
        cache["AAPL"] = rec("AAPL", "339.55", now - 30, now, bar=(now - 120, 0))     # the next observation, the same empty bar
        self.assertEqual(1, m.on_quotes(["AAPL"]))
        f = one(rt, "SELECT qty, price, price_basis, cap_note FROM fills WHERE order_id=?", (oid,))
        # marketable and not resting (its first look was marketable): the observed 339.55, all 1,000
        self.assertEqual((1000, Decimal("339.55"), "last"), (f["qty"], Decimal(f["price"]), f["price_basis"]))
        self.assertEqual(AUTOFILL_NOTE + "; the whole remaining 1,000 shares in one fill (Realistic would fill 0 now: the last completed 1-minute bar traded no"
                         " volume)", f["cap_note"])
        self.assertEqual((0, "FILLED"), (m.errors, order_row(rt, oid)["status"]))
        assert_integrity(self, rt, "full")

    def test_the_preview_says_autofill(self):
        rt = self.start()
        now = rt.clock.epoch()
        rt.marks.set_quote(rec("AAPL", "339.50", now - 120, now, bar=(now - 180, 20000)))         # Realistic cap 10 % x 20,000 = 2,000
        req = {"account_id": US_EXCHANGE, "instrument_id": "AAPL", "side": "BUY", "qty": 5000, "order_type": "MARKET", "tif": "DAY"}
        pv = orders.preview(rt, dict(req))
        ex = pv["execution"]
        self.assertTrue(pv["ok"], pv.get("blockers"))
        # Realistic is 1.0.7's preview: 2,000 now, the participation rate shown, no fill_mode key, PARTICIPATION
        self.assertEqual((True, 2000, "0.10"), (ex["fill_now"], ex["fill_qty_now"], ex["participation"]))
        self.assertNotIn("fill_mode", ex)
        self.assertNotIn(AUTOFILL_NOTE, ex["cap_note"] + ex["assumption"])
        self.assertIn("PARTICIPATION", pv["conventions"])
        self.assertNotIn("AUTOFILL", pv["conventions"])
        self.set_mode(A)
        pv = orders.preview(rt, dict(req))
        ex = pv["execution"]
        self.assertEqual((True, 5000, None, A), (ex["fill_now"], ex["fill_qty_now"], ex["participation"], ex["fill_mode"]))
        self.assertEqual(note("5,000 shares", "2,000"), ex["cap_note"])
        self.assertTrue(ex["assumption"].startswith("fills at the last observed trade price"), ex["assumption"])
        self.assertIn(AUTOFILL_NOTE + ": the whole remaining quantity fills at once whenever the order is executable", ex["assumption"])
        self.assertEqual(["FILL_PRICE", "AUTOFILL"], pv["conventions"][:2])
        self.assertFalse({"PARTICIPATION", "NO_VOLUME"} & set(pv["conventions"]))
        # an option's preview no longer says it is capped by the displayed size: BTO 40 against an ask size of 5 -> all 40 now
        occ = ensure_option(rt, OCC).instrument_id
        rt.marks.set(occ, "2.05")
        rt.marks.set_quote(chain_rec(occ, "2.00", "2.10", 5, 5, now - 60, now))
        pv = orders.preview(rt, {"account_id": US_EXCHANGE, "instrument_id": occ, "side": "BTO", "qty": 40, "order_type": "MARKET", "tif": "DAY"})
        ex = pv["execution"]
        self.assertTrue(pv["ok"], pv.get("blockers"))
        self.assertEqual((40, note("40 contracts", "5")), (ex["fill_qty_now"], ex["cap_note"]))
        self.assertNotIn("capped by the displayed size", ex["assumption"])
        self.assertIn(AUTOFILL_NOTE, ex["assumption"])
        self.assertIn("AUTOFILL", pv["conventions"])


# ===== the API: GET / PATCH /api/settings, /api/state, an order, the blotter (Router.handle, no socket) ================================
def build(tmp: str):
    os.environ[config.HOME_ENV] = tmp
    os.environ[config.DB_ENV] = os.path.join(tmp, "terminal.db")
    clock = FakeClock("2026-10-20T14:00:00-04:00")                         # Tuesday 14:00 ET: US_EQUITY open
    fp = FakeProvider(clock)
    return _server.build_runtime(tmp, os.path.join(tmp, "terminal.db"), clock=clock, offline=True, fake_provider=fp, log=_Log(),
                                 books=[config.TEST_BOOK], fetch_threads=0, news_enabled=False)


def quote(rt, sym: str, last: str, age: int = 120, vol: int = 61900) -> None:
    """A Yahoo-style print in the FakeProvider, loaded into the cache by the inline worker (as test_terminal_api does)."""
    now = rt.clock.epoch()
    qt = now - age
    rt.fp.set(sym, last=last, quote_time=qt, bar=(qt - qt % 60 - 60, vol))
    rt.cache.subscribe([sym], 300, "PAGE")
    rt.worker.tick()


class FillModeApiTests(unittest.TestCase):
    USX = "T1:US_EXCHANGE"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-fm1071-api-")
        self.rt = build(self.tmp)
        quote(self.rt, "AAPL", "339.50")                                  # last 339.50, bar volume 61,900 (Realistic cap 6,190)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def handle(self, method, path, body=None):
        return self.rt.router.handle(method, path, None, body, "127.0.0.1", None)

    def ok(self, method, path, body=None):
        status, out, _ = self.handle(method, path, body)
        self.assertEqual(200, status, f"{method} {path} -> {status} {out}")
        return out

    def patch(self, n, value):
        return self.handle("PATCH", "/api/settings", {"request_id": f"ui:fm1071-set-{n:04d}", "fill_mode": value})

    def test_setting_default_validation_and_journal(self):
        self.assertEqual(R, self.ok("GET", "/api/settings")["fill_mode"])
        st = self.ok("GET", "/api/state")
        self.assertEqual(R, st["settings"]["fill_mode"])                   # what the deploy after-check reads (gate1071_orders.py)
        self.assertIn("AUTOFILL", st["conventions"])
        status, s, _ = self.patch(1, "AUTOFILL")
        self.assertEqual((200, A), (status, s["fill_mode"]))
        for n, (value, want) in enumerate((("realistic", R), (" Autofill ", A)), start=2):
            status, s, _ = self.patch(n, value)
            self.assertEqual((200, want), (status, s["fill_mode"]), value)
        for n, bad in enumerate(("maybe", "AUTO", "", True, 1, None, ["AUTOFILL"]), start=10):
            status, body, _ = self.patch(n, bad)
            self.assertEqual((400, "SETTING_VALUE", "fill mode"), (status, body["error"]["code"], body["error"]["details"].get("field")), repr(bad))
        self.assertEqual(A, self.ok("GET", "/api/settings")["fill_mode"])     # the refusals stored nothing
        with self.rt.db.reader() as conn:                                     # journaled like every setting: before / after
            ev = [json.loads(r[0]) for r in conn.execute("SELECT payload_json FROM events WHERE type='SETTINGS_CHANGED' AND ref_id='fill_mode' ORDER BY id")]
        self.assertEqual([(R, A), (A, R), (R, A)], [(e["before"]["fill_mode"], e["after"]["fill_mode"]) for e in ev])
        # a database from before 1.0.7.1 has no row: REALISTIC is shown and applied
        with self.rt.db.writer() as tx:
            tx.conn.execute("DELETE FROM settings WHERE key='fill_mode'")
        self.assertEqual((R, R), (self.ok("GET", "/api/settings")["fill_mode"], self.ok("GET", "/api/state")["settings"]["fill_mode"]))

    def test_an_autofilled_order_and_its_disclosure_in_the_blotter(self):
        self.patch(1, "AUTOFILL")
        # BUY 20,000 AAPL: 20,000 x 339.50 = 6,790,000.00 > 25 % of the 10,000,000.00 NAV -> confirm_large; Realistic would fill 6,190
        req = {"account_id": self.USX, "instrument_id": "AAPL", "side": "BUY", "qty": 20000, "order_type": "MARKET", "tif": "DAY", "confirm_large": True}
        pv = self.ok("POST", "/api/orders/preview", dict(req))
        self.assertEqual((A, 20000, None, note("20,000 shares", "6,190")),
                         (pv["execution"]["fill_mode"], pv["execution"]["fill_qty_now"], pv["execution"]["participation"], pv["execution"]["cap_note"]))
        out = self.ok("POST", "/api/orders", dict(req, request_id="ui:fm1071-buy-0001", preview_id=pv["preview_id"]))
        self.assertEqual(("FILLED", 20000, 1), (out["order"]["status"], out["order"]["filled_qty"], len(out["fills"])))
        oid = out["order"]["order_id"]
        od = self.ok("GET", f"/api/orders/{oid}")
        self.assertEqual([note("20,000 shares", "6,190")], [f["cap_note"] for f in od["fills"]])
        self.assertIn(AUTOFILL_NOTE, od["events"][-1]["note"])
        fid = od["fills"][0]["fill_id"]
        rows = self.ok("GET", "/api/blotter/executions?book=T")["rows"]
        mine = [x for x in rows if x["execution_id"] == fid]
        self.assertEqual(1, len(mine))
        self.assertTrue(mine[0]["cap_note"].startswith(AUTOFILL_NOTE), mine[0])          # Completed Trades shows it
        # back to Realistic: BUY 1,000 on the same bar, whose 6,190 cap the 20,000 above used up -> BAR_CAP_USED, it works unfilled
        self.patch(2, "REALISTIC")
        out = self.ok("POST", "/api/orders", {"account_id": self.USX, "instrument_id": "AAPL", "side": "BUY", "qty": 1000, "order_type": "MARKET", "tif": "DAY",
                                              "request_id": "ui:fm1071-buy-0002"})
        self.assertEqual(("WORKING", 0, "BAR_CAP_USED"), (out["order"]["status"], out["order"]["filled_qty"], out["decision"]["reason"]))
        assert_integrity(self, self.rt, "full")


# ===== the Settings page: a two-option control with the warning (static; ASCII like every terminal file) ================================
class SettingsPageTests(unittest.TestCase):
    def test_fill_mode_control(self):
        with open(SETTINGS_JS, "rb") as fh:
            src = fh.read().decode("ascii")                                  # raises on a non-ASCII byte
        self.assertIn("['REALISTIC', 'AUTOFILL'].forEach(function (m) { sel.appendChild(T.h('option', { value: m, selected: m === cur }, FILL_MODE_WORDS[m])); });", src)
        self.assertIn("var FILL_MODE_WORDS = { REALISTIC: 'Realistic', AUTOFILL: 'Autofill' };", src)
        self.assertIn('"autofill (owner setting): no size or volume cap"', src)               # the warning quotes the blotter's words
        self.assertIn("T.ui.confirm(AUTOFILL_WARNING,", src)                                  # switching to Autofill asks first
        self.assertIn("patch({ fill_mode: 'AUTOFILL' }, 'fill mode Autofill')", src)
        self.assertIn("patch({ fill_mode: 'REALISTIC' }, 'fill mode Realistic')", src)
        self.assertIn("body.appendChild(fillModePanel());", src)                              # in General, after the execution defaults
        self.assertIn("T.convNote(['AUTOFILL'])", src)
        self.assertIn("['fill mode', T.h('span', null, FILL_MODE_WORDS[fillModeNow()]", src)  # Market data lists it, linked to General


if __name__ == "__main__":
    unittest.main()
