"""WP-02 order tests (TERMINAL_DESIGN.md §10, §19.9, §19.22; D6, D7).  Every expected number is worked by hand in a comment.

Scaffolding shared with test_terminal_equities / test_terminal_sweeps lives here (imported as a module, so unittest does
not load these cases twice): a runtime with the orders handlers registered, quote-record builders, a dict cache for the
Matcher, and the WP-03 / WP-24 stand-ins (FakeOptions margin, FakeStrategies package legs, FakeHedges package).
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import types
import unittest
from datetime import date, datetime
from decimal import Decimal

from tests.terminal_helpers import (T1, TREASURY, US_EXCHANGE, assert_integrity, bal, buy, deposit, execute, fund, make_runtime, rid, snapshot)
from finsim.terminal import execution, orders
from finsim.terminal.clock import NY, epoch_of
from finsim.terminal.commands import Command
from finsim.terminal.orders import Decision, Matcher, OrderRow, evaluate, evaluate_package, fill_cap
from finsim.terminal.quotes import Bar1m, QuoteRecord
from finsim.terminal.sessions import SessionState

YAHOO = "Yahoo Finance (up to 15 min delayed)"
TUE_1400 = 1792519200          # 2026-10-20 14:00 EDT = 18:00 UTC (the probe's session end 16:00 EDT is 1792526400; minus 2 h)
TUE_OPEN = 1792503000          # 2026-10-20 09:30 EDT (= 1792526400 - 6.5 h = 1792526400 - 23400)
TUE_CLOSE = 1792526400         # 2026-10-20 16:00 EDT
WED_OPEN = 1792589400          # 2026-10-21 09:30 EDT (= TUE_CLOSE + 17.5 h = 1792526400 + 63000)
WED_CLOSE = 1792612800         # 2026-10-21 16:00 EDT (= WED_OPEN + 23400)


def ep(iso_ny: str) -> int:
    """New York wall time 'YYYY-MM-DDTHH:MM[:SS]' -> epoch."""
    return int(datetime.fromisoformat(iso_ny).replace(tzinfo=NY).timestamp())


def rec(iid, last, quote_time, now, *, bar=None, bid=None, ask=None, bid_size=None, ask_size=None, method="direct", group="US_EQUITY",
        provider="yahoo", source=YAHOO, provider_session=None, mid=None, fetched_at=None, error=None) -> QuoteRecord:
    b = None
    if bar is not None:
        t, v = bar
        c = Decimal(str(last)) if last is not None else Decimal(0)
        b = Bar1m(int(t), c, c, c, c, v)
    d = lambda x: None if x is None else Decimal(str(x))   # noqa: E731
    return QuoteRecord(instrument_id=iid, provider=provider, source=source, method=method, last=d(last), bid=d(bid), ask=d(ask), bid_size=bid_size,
                       ask_size=ask_size, mid=d(mid), quote_time=quote_time, fetched_at=now if fetched_at is None else fetched_at, delay_s=900,
                       delay_label="up to 15 min delayed", currency="USD", session_group=group, provider_session=provider_session, bar=b, error=error,
                       quality="delayed")


def chain_rec(occ, bid, ask, bid_size, ask_size, quote_time, now) -> QuoteRecord:
    b, a = Decimal(str(bid)), Decimal(str(ask))
    return QuoteRecord(instrument_id=occ, provider="cboe", source="Cboe (delayed)", method="chain", last=None, bid=b, ask=a, bid_size=bid_size, ask_size=ask_size,
                       mid=(b + a) / 2, quote_time=quote_time, fetched_at=now, delay_s=900, delay_label="up to 15 min delayed", currency="USD",
                       session_group="US_EQUITY_OPTIONS", quality="delayed")


class FakeCache(dict):
    """What the Matcher reads: ``get(instrument_id) -> QuoteRecord | None``."""


class FakeOptions:
    """WP-03 stand-in: the §13.3 vertical-spread rule only (width x 100 x n - net credit, per right; max over rights);
    long-only books need no margin.  Bound as ``rt.options``."""

    def __init__(self, rt):
        self.rt = rt

    def margin_requirement_c(self, positions, marks):
        by_right = {}
        for p in positions:
            if p.kind != "OPTION":
                continue
            inst = self.rt.instruments.get(p.instrument_id)
            by_right.setdefault((inst.underlying_id, inst.expiry, inst.right), []).append((p, inst))
        req = 0
        detail = []
        for key, rows in by_right.items():
            shorts = [(p, i) for p, i in rows if p.qty < 0]
            longs = [(p, i) for p, i in rows if p.qty > 0]
            for sp, si in shorts:
                if not longs:
                    continue
                lp, li = longs[0]
                n = min(-sp.qty, lp.qty)
                width_c = int(abs(si.strike - li.strike) * 100 * 100) * n          # width x 100 shares x n contracts, in cents
                credit_c = (-sp.cost_c) - lp.cost_c                                # short proceeds - long cost (gross premiums)
                r = width_c - credit_c
                detail.append({"key": list(key), "width_c": width_c, "credit_c": credit_c, "requirement_c": r})
                req = max(req, r)
        return req, {"rule": "vertical spread: width x 100 x n - net credit (test stand-in for WP-03)", "detail": detail}


class FakeStrategies:
    """WP-24 stand-in: ``package_legs`` returns the legs the test pins."""

    def __init__(self, legs, fill_mode="AON"):
        self.legs, self.fill_mode = legs, fill_mode

    def package_legs(self, services, account_id, key, params):
        return types.SimpleNamespace(legs=[types.SimpleNamespace(**l) for l in self.legs], fill_mode=self.fill_mode, strategy_key=key)


class FakeHedges:
    """WP-03 stand-in: ``package`` resolves the candidate to the pinned legs and creates the children through orders."""

    def __init__(self, legs):
        self.legs = legs

    def package(self, ctx, services, primary_order_id, candidate_id, legs, ratio):
        return orders.create_hedge_children(ctx, services, primary_order_id, self.legs, candidate_id or "PUT_7PCT")


def runtime(tmp, at="2026-10-20T14:00:00-04:00", marks=None):
    rt = make_runtime(tmp, at=at, marks=marks)
    orders.register(rt.bus)
    return rt


def ensure_option(rt, occ, underlying="SPY"):
    with rt.db.writer() as tx:
        u = rt.instruments.get(underlying, tx.conn)
        rt.instruments.ensure_option(tx, occ, u, {"option": occ})
    return rt.instruments.refresh(occ)


def matcher_for(rt, cache) -> Matcher:
    return Matcher(rt.db, rt.bus, cache, rt.sessions, rt.instruments, rt.clock, rt.log, services=rt)


def submit(rt, **req):
    req.setdefault("request_id", rid())
    return rt.bus.execute(Command("order.submit", req, req["request_id"]))


def q(rt, sql, params=()):
    with rt.db.reader() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(rt, sql, params=()):
    rows = q(rt, sql, params)
    return rows[0] if rows else None


def order_row(rt, oid):
    return one(rt, "SELECT * FROM orders WHERE order_id=?", (oid,))


def sess(open_=True, opened_at=TUE_OPEN, closes_at=TUE_CLOSE, group="US_EQUITY") -> SessionState:
    return SessionState(group, open_, "OPEN" if open_ else "CLOSED", opened_at if open_ else None, closes_at if open_ else None, None if open_ else WED_OPEN,
                        "2026-10-20", False, "")


def row(side="BUY", qty=1000, otype="MARKET", limit=None, stop=None, trail_amt=None, trail_pct=None, ref=None, status="WORKING", filled=0, first=False,
        triggered=False, last_bar_ts=None, expires=TUE_CLOSE, kind="EQUITY", iid="AAPL", lot=1, tick="0.01") -> OrderRow:
    d = lambda x: None if x is None else Decimal(str(x))   # noqa: E731
    return OrderRow("O-TEST", US_EXCHANGE, iid, side, qty, filled, otype, d(limit), d(stop), d(trail_amt), d(trail_pct), d(ref), "SEQUENTIAL", "DAY", status,
                    triggered, first, last_bar_ts, None, "US_EQUITY_OPTIONS" if kind == "OPTION" else "US_EQUITY", expires, Decimal("0.10"), kind=kind, lot=lot,
                    tick=Decimal(tick), multiplier=Decimal(100 if kind == "OPTION" else 1))


class EvaluateTableTests(unittest.TestCase):
    """§10.5 / §10.6 cells, pure: no runtime, no database."""

    now = TUE_1400

    def r(self, last, **kw):
        return rec("AAPL", last, self.now - 120, self.now, **kw)

    def test_market_and_gates(self):
        s = sess()
        d = evaluate(row(), self.r("339.50"), s, self.now)
        self.assertEqual(("FILL", Decimal("339.5"), 1000, "last", "MARKET"), (d.action, d.price, d.qty, d.price_basis, d.reason))
        self.assertTrue(d.first_look)
        # gates: not open / expired / closed session / stale / computed / previous session / no quote
        self.assertEqual("NOT_OPEN", evaluate(row(status="FILLED"), self.r("339.50"), s, self.now).reason)
        self.assertEqual("EXPIRED_PENDING", evaluate(row(expires=self.now - 1), self.r("339.50"), s, self.now).reason)
        self.assertEqual("AWAITING_SESSION", evaluate(row(), self.r("339.50"), sess(False), self.now).reason)
        stale = rec("AAPL", "339.50", self.now - 1500, self.now)        # 1,500 s > 900 + 300
        self.assertEqual("STALE", evaluate(row(), stale, s, self.now).reason)
        self.assertEqual("COMPUTED_NOT_EXECUTABLE", evaluate(row(), self.r("339.50", method="model"), s, self.now).reason)
        prev = rec("AAPL", "339.50", TUE_OPEN - 60, self.now, fetched_at=self.now)   # a print from before this session's open
        self.assertEqual("QUOTE_FROM_PREVIOUS_SESSION", evaluate(row(), prev, s, self.now).reason)
        self.assertEqual("NO_QUOTE", evaluate(row(), None, s, self.now).reason)
        # a record fetched too long ago is stale even if its print is recent (FETCH_GRACE 300 s)
        self.assertEqual("STALE", evaluate(row(), rec("AAPL", "339.50", self.now - 60, self.now, fetched_at=self.now - 400), s, self.now).reason)

    def test_limit_rule_cells(self):
        s = sess()
        # BUY limit 339.00: first look marketable at 338.90 -> fills at 338.90 (at or better)
        d = evaluate(row(otype="LIMIT", limit="339.00"), self.r("338.90"), s, self.now)
        self.assertEqual(("FILL", Decimal("338.9"), "last", "LIMIT_MARKETABLE"), (d.action, d.price, d.price_basis, d.reason))
        # first look not marketable at 339.20 -> NONE NOT_MARKETABLE, first_look flagged
        d = evaluate(row(otype="LIMIT", limit="339.00"), self.r("339.20"), s, self.now)
        self.assertEqual(("NONE", "NOT_MARKETABLE", True), (d.action, d.reason, d.first_look))
        # later, first_eval_done: last 338.95 crosses -> fills AT THE LIMIT 339.00 (LIMIT_RULE)
        d = evaluate(row(otype="LIMIT", limit="339.00", first=True), self.r("338.95"), s, self.now)
        self.assertEqual(("FILL", Decimal("339"), "limit", "LIMIT_CROSSED", False), (d.action, d.price, d.price_basis, d.reason, d.first_look))
        d = evaluate(row(otype="LIMIT", limit="339.00", first=True), self.r("339.20"), s, self.now)
        self.assertEqual("NOT_MARKETABLE", d.reason)
        # SELL limit 340.00: first look 340.50 -> 340.50; afterwards crossed -> 340.00; 339.90 -> not marketable
        self.assertEqual(Decimal("340.5"), evaluate(row(side="SELL", otype="LIMIT", limit="340.00"), self.r("340.50"), s, self.now).price)
        self.assertEqual(Decimal("340"), evaluate(row(side="SELL", otype="LIMIT", limit="340.00", first=True), self.r("340.50"), s, self.now).price)
        self.assertEqual("NOT_MARKETABLE", evaluate(row(side="SELL", otype="LIMIT", limit="340.00", first=True), self.r("339.90"), s, self.now).reason)

    def test_stop_cells(self):
        s = sess()
        # BUY STOP 340.00: last 339.50 -> not reached; 340.10 -> triggers and fills at that print (stop_print)
        self.assertEqual("STOP_NOT_REACHED", evaluate(row(otype="STOP", stop="340.00"), self.r("339.50"), s, self.now).reason)
        d = evaluate(row(otype="STOP", stop="340.00"), self.r("340.10"), s, self.now)
        self.assertEqual(("FILL", Decimal("340.1"), "stop_print", "STOP_TRIGGERED", True), (d.action, d.price, d.price_basis, d.reason, d.triggered))
        self.assertIn("gap through the stop 340", d.note)                      # 340.10 > 340.00: disclosed
        # SELL STOP 339.00 with last 339.00 exactly -> triggers (last <= stop), fills at 339.00, no gap note
        d = evaluate(row(side="SELL", otype="STOP", stop="339.00"), self.r("339.00"), s, self.now)
        self.assertEqual((Decimal("339"), ""), (d.price, d.note))
        # an already-triggered stop behaves like a market order
        d = evaluate(row(otype="STOP", stop="340.00", triggered=True), self.r("339.50"), s, self.now)
        self.assertEqual(("FILL", Decimal("339.5"), False), (d.action, d.price, d.triggered))
        # STOP_LIMIT BUY stop 340 limit 340.50: 340.10 triggers and is marketable (340.10 <= 340.50) -> fills at 340.10
        d = evaluate(row(otype="STOP_LIMIT", stop="340.00", limit="340.50"), self.r("340.10"), s, self.now)
        self.assertEqual(("FILL", Decimal("340.1"), "last", True), (d.action, d.price, d.price_basis, d.triggered))
        # 341.00 triggers but is not marketable -> NONE NOT_MARKETABLE with triggered flagged (the handler persists the trigger)
        d = evaluate(row(otype="STOP_LIMIT", stop="340.00", limit="340.50"), self.r("341.00"), s, self.now)
        self.assertEqual(("NONE", "NOT_MARKETABLE", True), (d.action, d.reason, d.triggered))

    def test_trailing_cells(self):
        s = sess()
        # SELL trail 2.00 from 339.50: stop 337.50; last 341.00 -> RATCHET ref 341.00 stop 339.00
        d = evaluate(row(side="SELL", otype="TRAILING_STOP", trail_amt="2.00", stop="337.50", ref="339.50"), self.r("341.00"), s, self.now)
        self.assertEqual(("RATCHET", Decimal("339"), Decimal("341")), (d.action, d.stop_px, d.trail_ref_px))
        # last 340.00 against ref 341 / stop 339 -> unchanged, not reached
        d = evaluate(row(side="SELL", otype="TRAILING_STOP", trail_amt="2.00", stop="339.00", ref="341.00"), self.r("340.00"), s, self.now)
        self.assertEqual(("NONE", "STOP_NOT_REACHED"), (d.action, d.reason))
        # last 338.90 <= 339.00 -> TRIGGER then fill at the print 338.90
        d = evaluate(row(side="SELL", otype="TRAILING_STOP", trail_amt="2.00", stop="339.00", ref="341.00"), self.r("338.90"), s, self.now)
        self.assertEqual(("FILL", Decimal("338.9"), "stop_print", "TRAIL_TRIGGERED", True), (d.action, d.price, d.price_basis, d.reason, d.triggered))
        # trail_pct 1.00 initialised at last 341.00: stop = 341.00 x 0.99 = 337.59
        d = evaluate(row(side="SELL", otype="TRAILING_STOP", trail_pct="1.00"), self.r("341.00"), s, self.now)
        self.assertEqual(("RATCHET", "TRAIL_INIT", Decimal("337.59"), Decimal("341")), (d.action, d.reason, d.stop_px, d.trail_ref_px))
        # BUY trail 2.00 from 339.50: stop 341.50; last 338.00 -> stop 340.00; then 340.10 >= 340.00 -> fill at 340.10
        d = evaluate(row(side="BUY", otype="TRAILING_STOP", trail_amt="2.00", stop="341.50", ref="339.50"), self.r("338.00"), s, self.now)
        self.assertEqual(("RATCHET", Decimal("340"), Decimal("338")), (d.action, d.stop_px, d.trail_ref_px))
        d = evaluate(row(side="BUY", otype="TRAILING_STOP", trail_amt="2.00", stop="340.00", ref="338.00"), self.r("340.10"), s, self.now)
        self.assertEqual(("FILL", Decimal("340.1"), True), (d.action, d.price, d.triggered))
        # a print that neither improves nor reaches: 339.00 with stop 340 / ref 338 (buy) -> nothing
        d = evaluate(row(side="BUY", otype="TRAILING_STOP", trail_amt="2.00", stop="340.00", ref="338.00"), self.r("339.00"), s, self.now)
        self.assertEqual("STOP_NOT_REACHED", d.reason)

    def test_options_fx_and_caps(self):
        s = sess(group="US_EQUITY_OPTIONS")
        occ = "SPY261120P00700000"
        c = chain_rec(occ, "2.00", "2.10", 50, 10, self.now - 60, self.now)
        # BTO 25 with ask_size 10 -> 10 at the ask 2.10
        d = evaluate(row(side="BTO", qty=25, kind="OPTION", iid=occ), c, s, self.now)
        self.assertEqual(("FILL", Decimal("2.1"), 10, "ask"), (d.action, d.price, d.qty, d.price_basis))
        # STO 25 with bid_size 50 -> 25 at the bid 2.00
        d = evaluate(row(side="STO", qty=25, kind="OPTION", iid=occ), c, s, self.now)
        self.assertEqual((Decimal("2"), 25, "bid"), (d.price, d.qty, d.price_basis))
        # no usable NBBO (bid 0) -> NO_NBBO; ask < bid -> NO_NBBO
        self.assertEqual("NO_NBBO", evaluate(row(side="BTO", qty=1, kind="OPTION", iid=occ), chain_rec(occ, "0", "2.10", 1, 1, self.now - 60, self.now), s, self.now).reason)
        self.assertEqual("NO_NBBO", evaluate(row(side="BTO", qty=1, kind="OPTION", iid=occ), chain_rec(occ, "2.20", "2.10", 1, 1, self.now - 60, self.now), s, self.now).reason)
        # displayed size 0 -> NO_SIZE; None -> fills in full
        self.assertEqual("NO_SIZE", evaluate(row(side="BTO", qty=5, kind="OPTION", iid=occ), chain_rec(occ, "2.00", "2.10", 50, 0, self.now - 60, self.now), s, self.now).reason)
        self.assertEqual(5, evaluate(row(side="BTO", qty=5, kind="OPTION", iid=occ), chain_rec(occ, "2.00", "2.10", None, None, self.now - 60, self.now), s, self.now).qty)
        # FX BUY: mid 1.1238 + 0.2 pip (0.00002) = 1.12382; SELL: 1.12378
        fx = rec("EURUSD", "1.1238", self.now - 60, self.now, mid="1.1238", group="FX_SPOT")
        d = evaluate(row(side="BUY", qty=100000, kind="FX", iid="EURUSD", tick="0.0001", expires=self.now + 3600), fx, sess(group="FX_SPOT"), self.now)
        self.assertEqual((Decimal("1.12382"), "mid+0.2pip", 100000), (d.price, d.price_basis, d.qty))
        d = evaluate(row(side="SELL", qty=100000, kind="FX", iid="EURUSD", tick="0.0001", expires=self.now + 3600), fx, sess(group="FX_SPOT"), self.now)
        self.assertEqual(Decimal("1.12378"), d.price)
        # equities with an NBBO carried on the record (cached chain underlying): BUY at the ask, SELL at the bid
        nb = rec("AAPL", "339.50", self.now - 60, self.now, bid="339.48", ask="339.52")
        self.assertEqual((Decimal("339.52"), "ask"), (lambda d: (d.price, d.price_basis))(evaluate(row(), nb, sess(), self.now)))
        self.assertEqual((Decimal("339.48"), "bid"), (lambda d: (d.price, d.price_basis))(evaluate(row(side="SELL"), nb, sess(), self.now)))

    def test_fill_cap_table(self):
        t1 = self.now - 120
        # bar volume 120,000 at 10 % -> 12,000; 61,900 -> 6,190; 5 -> max(lot 1, 0) = 1; lot 100 with 61,900 -> floor(6,190/100) x 100 = 6,100
        self.assertEqual(12000, fill_cap(row(qty=50000), self.r("769.32", bar=(t1, 120000)), None, Decimal("0.10"))[0])
        self.assertEqual(6190, fill_cap(row(qty=50000), self.r("339.50", bar=(t1, 61900)), None, Decimal("0.10"))[0])
        self.assertEqual(1, fill_cap(row(qty=50000), self.r("339.50", bar=(t1, 5)), None, Decimal("0.10"))[0])
        self.assertEqual(6100, fill_cap(row(qty=50000, lot=100), self.r("339.50", bar=(t1, 61900)), None, Decimal("0.10"))[0])
        # the cap never exceeds the remaining quantity
        self.assertEqual(1000, fill_cap(row(qty=1000), self.r("339.50", bar=(t1, 120000)), None, Decimal("0.10"))[0])
        # same bar again -> 0 NO_NEW_BAR; zero-volume bar -> 0; no bar / no volume figure -> remaining
        cap, note = fill_cap(row(qty=50000, filled=12000, last_bar_ts=t1), self.r("769.32", bar=(t1, 120000)), None, Decimal("0.10"))
        self.assertEqual((0, True), (cap, "NO_NEW_BAR" in note))
        cap, note = fill_cap(row(qty=50000), self.r("769.32", bar=(t1, 0)), None, Decimal("0.10"))
        self.assertEqual((0, True), (cap, "NO_VOLUME_THIS_BAR" in note))
        self.assertEqual(50000, fill_cap(row(qty=50000), self.r("769.32"), None, Decimal("0.10"))[0])
        self.assertEqual(38000, fill_cap(row(qty=50000, filled=12000), self.r("769.32", bar=(t1, None)), None, Decimal("0.10"))[0])
        # the Decision carries the bar: a partial of 12,000 with bar t1, then NO_NEW_BAR on the same bar
        d = evaluate(row(qty=50000), self.r("769.32", bar=(t1, 120000)), sess(), self.now)
        self.assertEqual(("FILL", 12000, t1), (d.action, d.qty, d.bar_ts))
        d = evaluate(row(qty=50000, filled=12000, last_bar_ts=t1), self.r("769.32", bar=(t1, 120000)), sess(), self.now)
        self.assertEqual(("NONE", "NO_NEW_BAR"), (d.action, d.reason))

    def test_evaluate_package_pure(self):
        s = sess(group="US_EQUITY_OPTIONS")
        qt = self.now - 60
        legs = [row(side="BTO", qty=10, kind="OPTION", iid="P700"), row(side="STO", qty=10, kind="OPTION", iid="P730")]
        legs[0].order_id, legs[1].order_id = "O-1", "O-2"
        good = {"P700": chain_rec("P700", "2.00", "2.10", 50, 50, qt, self.now), "P730": chain_rec("P730", "4.00", "4.10", 50, 50, qt, self.now)}
        d = evaluate_package({"package_id": "PK"}, legs, good, s, self.now)
        self.assertEqual(("FILL", "AON_OK", qt), (d.action, d.reason, d.quote_time))
        self.assertEqual([("O-1", Decimal("2.1"), 10, "ask"), ("O-2", Decimal("4"), 10, "bid")], [(l.order_id, l.price, l.qty, l.price_basis) for l in d.legs])
        # displayed size short on one leg -> AON_SIZE, nothing
        short = dict(good, P730=chain_rec("P730", "4.00", "4.10", 5, 50, qt, self.now))
        d = evaluate_package({"package_id": "PK"}, legs, short, s, self.now)
        self.assertEqual(("NONE", "AON_SIZE", "O-2"), (d.action, d.reason, d.leg))
        # a stale leg -> AON_STALE; two observations -> AON_OBSERVATION; closed session; a non-open leg
        stale = dict(good, P700=chain_rec("P700", "2.00", "2.10", 50, 50, qt - 1500, self.now))
        self.assertEqual("AON_STALE", evaluate_package({}, legs, stale, s, self.now).reason)
        two = dict(good, P700=chain_rec("P700", "2.00", "2.10", 50, 50, qt - 30, self.now))
        self.assertEqual("AON_OBSERVATION", evaluate_package({}, legs, two, s, self.now).reason)
        self.assertEqual("AWAITING_SESSION", evaluate_package({}, legs, good, sess(False), self.now).reason)
        legs[1].status = "CANCELLED"
        self.assertEqual("NOT_OPEN", evaluate_package({}, legs, good, s, self.now).reason)


class OrdersScenarioTests(unittest.TestCase):
    """§19.9 / §19.22 through the bus: submit, matcher, handlers, persistence."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-ord-")
        self.rt = None

    def tearDown(self):
        if self.rt is not None:
            self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start(self, at="2026-10-20T14:00:00-04:00", marks=None):
        self.rt = runtime(self.tmp, at=at, marks=marks)
        self.rt.options = FakeOptions(self.rt)
        return self.rt

    def set_quote(self, r):
        self.rt.marks.set_quote(r)

    def test_preview_fixture_frozen(self):
        # §10.3 shape: BUY 5,000 AAPL MARKET at Tue 14:00, last 339.50 (age 245 s), bar volume 61,900, account funded 1,000,000
        rt = self.start()
        fund(rt, US_EXCHANGE, 100000000)
        now = rt.clock.epoch()
        self.assertEqual(TUE_1400, now)
        self.set_quote(rec("AAPL", "339.50", now - 245, now, bar=(now - 300, 61900)))
        pv = orders.preview(rt, {"account_id": US_EXCHANGE, "instrument_id": "AAPL", "side": "BUY", "qty": 5000, "order_type": "MARKET", "tif": "DAY"})
        path = os.path.join(os.path.dirname(__file__), "fixtures", "terminal", "preview_aapl.json")
        with open(path, "r", encoding="utf-8") as fh:
            frozen = json.load(fh)
        got = json.loads(json.dumps(pv, default=str))
        self.assertTrue(got.pop("preview_id").startswith("pv:"))
        frozen.pop("preview_id")
        # Build 2 (2026-10-21) on top of the frozen Build-1 shape, every change on purpose: the fee lines (C25) and the trade date of the
        # first session the order can trade in (EXEC-8: Tue 14:00 is in session -> 2026-10-20); the cap note says the cap is per bar across
        # all orders and books (EXEC-6; bar 13:55 = now - 300); the hedge-leg cash, the FX conversion plan and the confirm rules (USX-17,
        # C15, USX-18) - none here; the sweep confirm threshold is shown only when that rule fires (SR-12: off by default -> None)
        frozen["economics"].update(fee_lines=[{"label": "commission", "amount": "25.00", "basis": "USD 0.005/share, min 1.00"}], trade_date="2026-10-20")
        frozen["execution"].update(cap_note="latest 1-minute bar (13:55) volume 61,900; 10 % participation caps it at 6,190 shares across all orders and books",
                                   decision_note="")
        frozen["funding"].update(confirm_rules=[], fx_conversion=None, fx_convert=False, hedge_legs=None, order_need="1697525.00", threshold=None)
        frozen["quote"]["note"] = ""
        self.assertEqual(frozen, got)
        # gross 5,000 x 339.50 = 1,697,500.00; fee 5,000 x 0.005 = 25.00; need 1,697,525.00; available 1,000,000.00 -> shortfall / sweep 697,525.00
        self.assertEqual(("1697500.00", "25.00", "697525.00", "697525.00"), (pv["economics"]["gross"], pv["economics"]["fee"], pv["funding"]["shortfall"], pv["funding"]["sweep_in"]))
        self.assertEqual(pv, orders.preview_store(rt).get(pv["preview_id"], now))
        self.assertIsNone(orders.preview_store(rt).get(pv["preview_id"], now + 121))      # TTL 120 s

    def test_stale_quote_then_limit_fills(self):
        # Tue 10:30 ET (OPEN); AAPL quote_time = now - 1,500 (> 900 + 300) -> stale: MARKET refused 409, LIMIT may rest
        rt = self.start(at="2026-10-20T10:30:00-04:00")
        fund(rt, US_EXCHANGE, 50000000)
        now = rt.clock.epoch()
        stale = rec("AAPL", "339.50", now - 1500, now)
        self.set_quote(stale)
        pv = orders.preview(rt, {"account_id": US_EXCHANGE, "instrument_id": "AAPL", "side": "BUY", "qty": 1000, "order_type": "MARKET"})
        self.assertEqual(("STALE", False), (pv["blockers"][0]["details"]["reason"], pv["ok"]))
        r = submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=1000, order_type="MARKET")
        self.assertEqual((409, "QUOTE_STALE"), (r.http, r.code))
        self.assertEqual(0, one(rt, "SELECT COUNT(*) n FROM orders")["n"])
        r = submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=1000, order_type="LIMIT", limit_px="339.00")
        self.assertTrue(r.ok, r.body)
        oid = r.body["order"]["order_id"]
        self.assertEqual(("WORKING", 0, "STALE"), (r.body["order"]["status"], r.body["order"]["first_eval_done"], r.body["decision"]["reason"]))
        # fresh record last 338.90, bar volume 50,000 (cap 5,000): marketable on the first executable look -> fills 1,000 @ 338.90
        cache = FakeCache()
        m = matcher_for(rt, cache)
        fresh = rec("AAPL", "338.90", now - 60, now, bar=(now - 120, 50000))
        cache["AAPL"] = fresh
        self.assertEqual(1, m.on_quotes(["AAPL"]))
        o = order_row(rt, oid)
        self.assertEqual(("FILLED", 1000, "338.9"), (o["status"], o["filled_qty"], o["avg_px"]))
        f = one(rt, "SELECT * FROM fills WHERE order_id=?", (oid,))
        # gross 1,000 x 338.90 = 338,900.00; fee 1,000 x 0.005 = 5.00; idem match:<oid>:<quote_time>:<bar_ts>
        self.assertEqual((33890000, 500, "last", f"match:{oid}:{now - 60}:{now - 120}"), (f["gross_c"], f["fee_c"], f["price_basis"], f["idem_key"]))
        # the same record again -> the same request id -> replay, no second fill, no second command
        self.assertEqual(0, m.on_quotes(["AAPL"]))
        self.assertEqual(1, one(rt, "SELECT COUNT(*) n FROM fills")["n"])
        self.assertEqual(1, one(rt, "SELECT COUNT(*) n FROM commands WHERE kind='order.match'")["n"])
        # variant: first look last 339.20 (not marketable -> first_eval_done = 1); later last 338.95 -> fills at the LIMIT 339.00
        r = submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=1000, order_type="LIMIT", limit_px="339.00")
        oid2 = r.body["order"]["order_id"]
        cache["AAPL"] = rec("AAPL", "339.20", now - 30, now, bar=(now - 60, 50000))
        self.assertEqual(1, m.on_quotes(["AAPL"]))                       # the first-look command persists first_eval_done
        self.assertEqual((1, "WORKING", 0), (lambda o: (o["first_eval_done"], o["status"], o["filled_qty"]))(order_row(rt, oid2)))
        cache["AAPL"] = rec("AAPL", "338.95", now, now, bar=(now - 60, 50000))
        self.assertEqual(1, m.on_quotes(["AAPL"]))
        o = order_row(rt, oid2)
        f = one(rt, "SELECT * FROM fills WHERE order_id=?", (oid2,))
        self.assertEqual(("FILLED", "339", "limit"), (o["status"], f["price"], f["price_basis"]))
        # a computed mark never fills
        r = submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=100, order_type="LIMIT", limit_px="400.00")
        oid3 = r.body["order"]["order_id"]
        cache["AAPL"] = rec("AAPL", "339.00", now, now, method="model")
        self.assertEqual(0, m.on_quotes(["AAPL"]))
        self.assertEqual("WORKING", order_row(rt, oid3)["status"])
        assert_integrity(self, rt, "full")

    def test_session_rests_until_open(self):
        # Sat 2026-10-24 12:00 ET: MARKET BUY accepted WORKING AWAITING_SESSION; expires_ts = Mon 10-26 16:00 ET
        rt = self.start(at="2026-10-24T12:00:00-04:00")
        fund(rt, US_EXCHANGE, 10000000)
        now = rt.clock.epoch()
        fri_close = ep("2026-10-23T16:00:00")
        self.set_quote(rec("AAPL", "339.50", fri_close, fri_close))
        r = submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=100, order_type="MARKET", tif="DAY")
        self.assertTrue(r.ok, r.body)
        oid = r.body["order"]["order_id"]
        mon_open, mon_close = ep("2026-10-26T09:30:00"), ep("2026-10-26T16:00:00")
        self.assertEqual(("WORKING", "AWAITING_SESSION", mon_close), (r.body["order"]["status"], r.body["decision"]["reason"], r.body["order"]["expires_ts"]))
        self.assertEqual((mon_open, "WEEKEND"), (r.body["preview"]["execution"]["rests_until"], r.body["preview"]["execution"]["session"]["phase"]))
        # Mon 09:31 with a fresh quote (quote_time >= opened_at) -> fills
        rt.clock.set("2026-10-26T09:31:00-04:00")
        now = rt.clock.epoch()
        cache = FakeCache({"AAPL": rec("AAPL", "339.50", mon_open + 30, now)})
        self.assertEqual(1, matcher_for(rt, cache).sweep())
        self.assertEqual("FILLED", order_row(rt, oid)["status"])
        # DAY placed Tue 17:30 -> expires Wed 16:00; Fri 2026-11-27 -> 13:00 (early close); Thu 2026-11-26 (Thanksgiving) -> rests until Fri 09:30
        rt.clock.set("2026-10-20T17:30:00-04:00")
        r = submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=10, order_type="LIMIT", limit_px="300.00")
        self.assertEqual(WED_CLOSE, r.body["order"]["expires_ts"])
        rt.clock.set("2026-11-27T10:00:00-05:00")
        r = submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=10, order_type="LIMIT", limit_px="300.00")
        self.assertEqual(epoch_of(date(2026, 11, 27), "13:00"), r.body["order"]["expires_ts"])
        rt.clock.set("2026-11-26T10:00:00-05:00")
        r = submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=10, order_type="LIMIT", limit_px="300.00")
        self.assertEqual(("HOLIDAY", epoch_of(date(2026, 11, 27), "09:30"), epoch_of(date(2026, 11, 27), "13:00")),
                         (r.body["preview"]["execution"]["session"]["phase"], r.body["preview"]["execution"]["rests_until"], r.body["order"]["expires_ts"]))
        # GTC (USX-31): the close of the session of the 90th calendar day, in New York wall time: 2026-10-20 + 90 days = Mon 2027-01-18,
        # Martin Luther King Jr. Day (NYSE closed) -> the last session on or before it, Fri 2027-01-15 16:00 EST (= 21:00 UTC, 1800046800;
        # the old rule, created + 90 x 24 h, gave Mon 2027-01-18 13:00 EST across the DST change)
        rt.clock.set("2026-10-20T14:00:00-04:00")
        r = submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=10, order_type="LIMIT", limit_px="300.00", tif="GTC")
        self.assertEqual(epoch_of(date(2027, 1, 15), "16:00"), r.body["order"]["expires_ts"])
        self.assertEqual(1800046800, r.body["order"]["expires_ts"])

    def test_d6_session_limit_day_expiry(self):
        # D6: outside hours queues; a buy limit fills only when the price is at or below the limit; DAY expires; cancelled / expired stay
        rt = self.start(at="2026-10-20T17:30:00-04:00")
        fund(rt, US_EXCHANGE, 10000000)
        self.set_quote(rec("AAPL", "339.50", TUE_CLOSE, TUE_CLOSE))
        r = submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=100, order_type="LIMIT", limit_px="339.00", tif="DAY")
        oid = r.body["order"]["order_id"]
        self.assertEqual(("WORKING", "AWAITING_SESSION", WED_CLOSE), (r.body["order"]["status"], r.body["decision"]["reason"], r.body["order"]["expires_ts"]))
        r2 = submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=100, order_type="LIMIT", limit_px="300.00", tif="DAY")
        oid_rest = r2.body["order"]["order_id"]
        rt.clock.set("2026-10-21T09:35:00-04:00")
        now = rt.clock.epoch()
        cache = FakeCache({"AAPL": rec("AAPL", "339.20", now - 30, now)})
        m = matcher_for(rt, cache)
        m.on_quotes(["AAPL"])                                       # 339.20 > 339.00: no fill (first look recorded)
        self.assertEqual(("WORKING", 0, 1), (lambda o: (o["status"], o["filled_qty"], o["first_eval_done"]))(order_row(rt, oid)))
        cache["AAPL"] = rec("AAPL", "338.95", now, now)
        m.on_quotes(["AAPL"])                                       # 338.95 <= 339.00 -> fills at the limit 339.00
        self.assertEqual(("FILLED", "339"), (lambda o: (o["status"], o["avg_px"]))(order_row(rt, oid)))
        # a cancel of the resting one and an expiry of another at the close: both rows stay, with their events
        r3 = submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=100, order_type="LIMIT", limit_px="300.00", tif="DAY")
        oid_cancel = r3.body["order"]["order_id"]
        rc = execute(rt, "order.cancel", {"order_id": oid_cancel})
        self.assertTrue(rc.ok, rc.body)
        self.assertEqual("CANCELLED", rc.body["order"]["status"])
        self.assertEqual((409, "NOT_OPEN"), (lambda x: (x.http, x.code))(execute(rt, "order.cancel", {"order_id": oid_cancel})))
        rt.clock.set("2026-10-21T16:00:01-04:00")
        self.assertEqual(0, m.sweep())                                                          # EXPIRED_PENDING: never fills after expiry
        re_ = execute(rt, "order.expire", {}, actor="lifecycle")
        self.assertEqual([oid_rest], [e["order_id"] for e in re_.body["expired"]])
        self.assertEqual("EXPIRED", order_row(rt, oid_rest)["status"])
        self.assertEqual({"CANCELLED", "EXPIRED", "FILLED"}, {o["status"] for o in q(rt, "SELECT status FROM orders")})
        self.assertEqual(2, one(rt, "SELECT COUNT(*) n FROM events WHERE type IN ('ORDER_CANCELLED','ORDER_EXPIRED')")["n"])
        assert_integrity(self, rt, "full")

    def test_laptop_sleep_and_previous_session(self):
        # a DAY order placed Tue 10:00; the clock jumps to Thu 09:40 with a fresh quote -> EXPIRED_PENDING; the lifecycle expires it; no fill
        rt = self.start(at="2026-10-20T10:00:00-04:00")
        fund(rt, US_EXCHANGE, 10000000)
        now = rt.clock.epoch()
        self.set_quote(rec("AAPL", "339.50", now - 60, now))
        r = submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=100, order_type="LIMIT", limit_px="330.00", tif="DAY")
        oid = r.body["order"]["order_id"]
        rg = submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=100, order_type="LIMIT", limit_px="300.00", tif="GTC")   # not marketable at 320
        oid_gtc = rg.body["order"]["order_id"]
        rt.clock.set("2026-10-22T09:40:00-04:00")
        now = rt.clock.epoch()
        o = OrderRow.from_row(one_row(rt, oid), rt.instruments.get("AAPL"))
        d = evaluate(o, rec("AAPL", "320.00", now - 30, now), rt.sessions.state("US_EQUITY", now), now)
        self.assertEqual(("NONE", "EXPIRED_PENDING"), (d.action, d.reason))
        cache = FakeCache({"AAPL": rec("AAPL", "320.00", now - 30, now)})
        m = matcher_for(rt, cache)
        m.on_quotes(["AAPL"])
        self.assertEqual(("WORKING", 0), (lambda o: (o["status"], o["filled_qty"]))(order_row(rt, oid)))
        execute(rt, "order.expire", {}, actor="lifecycle")
        self.assertEqual("EXPIRED", order_row(rt, oid)["status"])
        self.assertEqual(0, one(rt, "SELECT COUNT(*) n FROM fills WHERE order_id=?", (oid,))["n"])
        # the GTC one with a record whose quote_time is Wed 15:59, evaluated Thu 09:31 -> QUOTE_FROM_PREVIOUS_SESSION, no fill
        rt.clock.set("2026-10-22T09:31:00-04:00")
        now = rt.clock.epoch()
        wed_1559 = ep("2026-10-21T15:59:00")
        og = OrderRow.from_row(one_row(rt, oid_gtc), rt.instruments.get("AAPL"))
        d = evaluate(og, rec("AAPL", "320.00", wed_1559, now), rt.sessions.state("US_EQUITY", now), now)
        self.assertEqual("QUOTE_FROM_PREVIOUS_SESSION", d.reason)
        cache["AAPL"] = rec("AAPL", "320.00", wed_1559, now)
        self.assertEqual(0, m.on_quotes(["AAPL"]))
        self.assertEqual(0, one(rt, "SELECT COUNT(*) n FROM fills")["n"])

    def test_stops_through_the_bus(self):
        # BUY STOP 340.00 with last 339.50 accepted; last 340.10 -> TRIGGER then fill at 340.10; a sell stop above last -> 400 STOP_SIDE
        rt = self.start()
        fund(rt, US_EXCHANGE, 50000000)
        now = rt.clock.epoch()
        self.set_quote(rec("AAPL", "339.50", now - 60, now))
        r = submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=100, order_type="STOP", stop_px="340.00")
        self.assertTrue(r.ok, r.body)
        oid = r.body["order"]["order_id"]
        self.assertEqual(("WORKING", "STOP_NOT_REACHED", 0), (r.body["order"]["status"], r.body["decision"]["reason"], r.body["order"]["triggered"]))
        cache = FakeCache({"AAPL": rec("AAPL", "340.10", now, now)})
        self.assertEqual(1, matcher_for(rt, cache).on_quotes(["AAPL"]))
        o = order_row(rt, oid)
        f = one(rt, "SELECT * FROM fills WHERE order_id=?", (oid,))
        self.assertEqual(("FILLED", 1, "340.1", "stop_print"), (o["status"], o["triggered"], f["price"], f["price_basis"]))
        self.assertEqual(1, one(rt, "SELECT COUNT(*) n FROM events WHERE type='ORDER_TRIGGERED'")["n"])
        buy(rt, US_EXCHANGE, "AAPL", 100, "339.50")
        r = submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="SELL", qty=100, order_type="STOP", stop_px="341.00")
        self.assertEqual((400, "STOP_SIDE"), (r.http, r.code))
        r = submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=100, order_type="STOP", stop_px="339.00")
        self.assertEqual((400, "STOP_SIDE"), (r.http, r.code))

    def test_partial_fills_cancel_and_avg(self):
        # MARKET BUY 50,000 SPY (38,466,000 notional needs capital: deposit 40,000,000 first; > 25 % of NAV -> confirm_large)
        rt = self.start()
        deposit(rt, 4000000000)
        now = rt.clock.epoch()
        t1, t2, t3 = now - 180, now - 120, now - 60
        self.set_quote(rec("SPY", "769.32", now - 90, now, bar=(t1, 120000)))
        r = submit(rt, account_id=US_EXCHANGE, instrument_id="SPY", side="BUY", qty=50000, order_type="MARKET")
        self.assertEqual((409, "CONFIRM_REQUIRED"), (r.http, r.code))
        r = submit(rt, request_id=r.body["request_id"], account_id=US_EXCHANGE, instrument_id="SPY", side="BUY", qty=50000, order_type="MARKET", confirm_large=True)
        self.assertTrue(r.ok, r.body)
        oid = r.body["order"]["order_id"]
        # bar (t1, v 120,000) -> cap 12,000 -> PARTIAL 12,000, last_bar_ts t1
        self.assertEqual(("PARTIAL", 12000, t1), (r.body["order"]["status"], r.body["order"]["filled_qty"], r.body["order"]["last_bar_ts"]))
        cache = FakeCache({"SPY": rec("SPY", "769.32", now - 30, now, bar=(t1, 120000))})
        m = matcher_for(rt, cache)
        self.assertEqual(0, m.on_quotes(["SPY"]))                                     # same bar -> NO_NEW_BAR, no command
        # bar (t2, v 90,000) at 769.67 -> 9,000 more (21,000 filled)
        cache["SPY"] = rec("SPY", "769.67", now - 20, now, bar=(t2, 90000))
        self.assertEqual(1, m.on_quotes(["SPY"]))
        self.assertEqual((21000, "PARTIAL", t2), (lambda o: (o["filled_qty"], o["status"], o["last_bar_ts"]))(order_row(rt, oid)))
        # bar (t3, v 0) -> NO_VOLUME_THIS_BAR
        cache["SPY"] = rec("SPY", "769.67", now - 10, now, bar=(t3, 0))
        self.assertEqual(0, m.on_quotes(["SPY"]))
        self.assertEqual(21000, order_row(rt, oid)["filled_qty"])
        # cancel -> CANCELLED with filled_qty 21,000; avg = (12,000 x 769.32 + 9,000 x 769.67) / 21,000 = (9,231,840 + 6,927,030) / 21,000 = 769.47
        rc = execute(rt, "order.cancel", {"order_id": oid})
        o = order_row(rt, oid)
        self.assertEqual(("CANCELLED", 21000, "769.47"), (o["status"], o["filled_qty"], o["avg_px"]))
        self.assertEqual(2, one(rt, "SELECT COUNT(*) n FROM fills WHERE order_id=?", (oid,))["n"])
        # fees: 12,000 x 0.005 = 60.00 and 9,000 x 0.005 = 45.00 -> 105.00 on the order
        self.assertEqual(10500, o["fees_c"])
        assert_integrity(self, rt, "full")

    def test_option_partial_by_displayed_size(self):
        # BTO 25 with ask_size 10 -> fills 10; the same observation -> nothing; a new observation -> 10 more
        rt = self.start()
        fund(rt, US_EXCHANGE, 10000000)
        occ = ensure_option(rt, "SPY261120P00700000").instrument_id
        rt.marks.set(occ, "2.05")                                                      # valuation mark (mid) so NAV is not partial
        now = rt.clock.epoch()
        self.set_quote(chain_rec(occ, "2.00", "2.10", 50, 10, now - 60, now))
        r = submit(rt, account_id=US_EXCHANGE, instrument_id=occ, side="BTO", qty=25, order_type="MARKET")
        self.assertTrue(r.ok, r.body)
        oid = r.body["order"]["order_id"]
        # 10 x 2.10 x 100 = 2,100.00 gross; fee 10 x 0.65 = 6.50
        self.assertEqual(("PARTIAL", 10, 210000, 650), (r.body["order"]["status"], r.body["order"]["filled_qty"], r.body["fills"][0]["gross_c"], r.body["fills"][0]["fee_c"]))
        cache = FakeCache({occ: chain_rec(occ, "2.00", "2.10", 50, 10, now - 60, now)})
        m = matcher_for(rt, cache)
        self.assertEqual(0, m.on_quotes([occ]))                                        # same observation: replay / idem
        self.assertEqual(10, order_row(rt, oid)["filled_qty"])
        cache[occ] = chain_rec(occ, "2.00", "2.10", 50, 10, now - 30, now)
        self.assertEqual(1, m.on_quotes([occ]))
        self.assertEqual(20, order_row(rt, oid)["filled_qty"])
        self.assertEqual(0, one(rt, "SELECT options_required_c r FROM margin_state WHERE account_id=?", (US_EXCHANGE,))["r"])
        assert_integrity(self, rt, "full")

    def test_d7_hedge_package_resize(self):
        # D7: MARKET BUY 50,000 SPY with hedge PUT_7PCT ratio 0.01: after 12,000 -> child 120 WORKING; after 21,000 -> 210
        rt = self.start()
        deposit(rt, 4000000000)
        occ = ensure_option(rt, "SPY261120P00715000").instrument_id
        rt.hedges = FakeHedges([{"instrument_id": occ, "side": "BTO", "qty": 500}])
        now = rt.clock.epoch()
        t1, t2 = now - 180, now - 120
        self.set_quote(rec("SPY", "769.32", now - 90, now, bar=(t1, 120000)))
        r = submit(rt, account_id=US_EXCHANGE, instrument_id="SPY", side="BUY", qty=50000, order_type="MARKET", confirm_large=True,
                   hedge={"candidate_id": "PUT_7PCT", "ratio": "0.01"})
        self.assertTrue(r.ok, r.body)
        oid, pid = r.body["order"]["order_id"], r.body["hedge_package"]
        child = one(rt, "SELECT * FROM orders WHERE parent_order_id=?", (oid,))
        # ratio = 500 / 50,000 = 0.01; after the 12,000 partial: floor(12,000 x 0.01) = 120 contracts, WAITING_PARENT -> WORKING
        self.assertEqual((pid, "0.01", "WORKING", 120, "BTO"), (child["package_id"], child["ratio"], child["status"], child["qty"], child["side"]))
        cache = FakeCache({"SPY": rec("SPY", "769.67", now - 20, now, bar=(t2, 90000))})
        matcher_for(rt, cache).on_quotes(["SPY"])
        self.assertEqual(21000, order_row(rt, oid)["filled_qty"])
        self.assertEqual((210, "WORKING"), (lambda c: (c["qty"], c["status"]))(one(rt, "SELECT * FROM orders WHERE parent_order_id=?", (oid,))))
        self.assertEqual("SEQUENTIAL", one(rt, "SELECT fill_mode FROM hedge_packages WHERE package_id=?", (pid,))["fill_mode"])
        # cancelling the primary after a partial keeps the sized child; a primary ended unfilled cancels its child
        execute(rt, "order.cancel", {"order_id": oid})
        self.assertEqual("WORKING", one(rt, "SELECT status FROM orders WHERE parent_order_id=?", (oid,))["status"])
        r2 = submit(rt, account_id=US_EXCHANGE, instrument_id="SPY", side="BUY", qty=1000, order_type="LIMIT", limit_px="700.00", hedge={"candidate_id": "PUT_7PCT"})
        oid2 = r2.body["order"]["order_id"]
        self.assertEqual("WAITING_PARENT", one(rt, "SELECT status FROM orders WHERE parent_order_id=?", (oid2,))["status"])
        execute(rt, "order.cancel", {"order_id": oid2})
        self.assertEqual("CANCELLED", one(rt, "SELECT status FROM orders WHERE parent_order_id=?", (oid2,))["status"])
        assert_integrity(self, rt, "full")

    def test_sequential_strategy_package(self):
        # a strategy with a stock primary (PROTECTIVE_PUT-like) is SEQUENTIAL: the primary works, the option leg waits for it with ratio 10 / 1,000 = 0.01
        rt = self.start()
        fund(rt, US_EXCHANGE, 100000000)
        occ = ensure_option(rt, "SPY261120P00715000").instrument_id
        rt.marks.set(occ, "9.50")
        rt.strategies = FakeStrategies([{"instrument_id": "SPY", "side": "BUY", "qty": 1000, "order_type": "LIMIT", "limit_px": "700.00"},
                                        {"instrument_id": occ, "side": "BTO", "qty": 10}], "SEQUENTIAL")
        now = rt.clock.epoch()
        self.set_quote(rec("SPY", "769.32", now - 60, now))
        r = submit(rt, account_id=US_EXCHANGE, package={"strategy_key": "PROTECTIVE_PUT", "units": 1000})
        self.assertTrue(r.ok, r.body)
        oid, pid = r.body["order"]["order_id"], r.body["package"]["package_id"]
        self.assertEqual(("WORKING", "SEQUENTIAL", "LIMIT", "700"), (r.body["order"]["status"], r.body["package"]["fill_mode"], r.body["order"]["order_type"], r.body["order"]["limit_px"]))
        child = one(rt, "SELECT * FROM orders WHERE parent_order_id=?", (oid,))
        self.assertEqual(("WAITING_PARENT", 10, "0.01", pid, occ), (child["status"], child["qty"], child["ratio"], child["package_id"], child["instrument_id"]))
        self.assertEqual(("SEQUENTIAL", oid, "PROTECTIVE_PUT"), (lambda p: (p["fill_mode"], p["primary_order_id"], p["strategy_key"]))(one(rt, "SELECT * FROM hedge_packages WHERE package_id=?", (pid,))))
        # the primary fills at 699.50 -> the child is sized floor(1,000 x 0.01) = 10 and released WORKING
        cache = FakeCache({"SPY": rec("SPY", "699.50", now, now)})
        self.assertEqual(1, matcher_for(rt, cache).on_quotes(["SPY"]))
        self.assertEqual("FILLED", order_row(rt, oid)["status"])
        self.assertEqual(("WORKING", 10), (lambda c: (c["status"], c["qty"]))(one(rt, "SELECT * FROM orders WHERE parent_order_id=?", (oid,))))
        assert_integrity(self, rt, "full")

    def test_confirm_and_preview_stale(self):
        rt = self.start()
        fund(rt, US_EXCHANGE, 100000000)
        now = rt.clock.epoch()
        self.set_quote(rec("AAPL", "339.50", now - 60, now))
        base = {"account_id": US_EXCHANGE, "instrument_id": "AAPL", "side": "BUY", "qty": 100, "order_type": "MARKET"}
        # unknown preview_id -> 409 PREVIEW_STALE carrying a fresh preview
        r = submit(rt, preview_id="pv:nope", **base)
        self.assertEqual((409, "PREVIEW_STALE"), (r.http, r.code))
        self.assertEqual("339.5", r.body["error"]["details"]["preview"]["execution"]["expected_price"])
        # price moved 0.6 % against the buyer since the preview: 339.50 x 1.006 = 341.537 -> 341.54 (+0.60 %) -> 409 with diff
        pv = orders.preview(rt, base)
        self.set_quote(rec("AAPL", "341.54", now - 30, now))
        r = submit(rt, preview_id=pv["preview_id"], **base)
        self.assertEqual((409, "PREVIEW_STALE", "0.60"), (r.http, r.code, r.body["error"]["details"]["diff"]["moved_pct"]))
        # a move of 0.4 % (339.50 x 1.004 = 340.858 -> 340.86) passes and fills
        self.set_quote(rec("AAPL", "340.86", now - 20, now))
        r = submit(rt, preview_id=pv["preview_id"], **base)
        self.assertTrue(r.ok, r.body)
        self.assertEqual("FILLED", r.body["order"]["status"])
        # an expired preview (121 s later) -> PREVIEW_STALE
        rt.clock.advance(121)
        now = rt.clock.epoch()
        self.set_quote(rec("AAPL", "340.86", now - 20, now))
        r = submit(rt, preview_id=pv["preview_id"], **base)
        self.assertEqual((409, "PREVIEW_STALE"), (r.http, r.code))

    def test_validation_codes(self):
        rt = self.start()
        fund(rt, US_EXCHANGE, 10000000)
        now = rt.clock.epoch()
        self.set_quote(rec("AAPL", "339.50", now - 60, now))
        codes = lambda r: (r.http, r.code)   # noqa: E731
        base = dict(account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=100, order_type="MARKET")
        self.assertEqual((400, "ACCOUNT_UNKNOWN"), codes(submit(rt, **dict(base, account_id="T1:NOPE"))))
        self.assertEqual((400, "NOT_TRADEABLE_HERE"), codes(submit(rt, **dict(base, account_id=TREASURY))))
        self.assertEqual((400, "INSTRUMENT_UNKNOWN"), codes(submit(rt, **dict(base, instrument_id="ZZZZ9"))))
        self.assertEqual((400, "QTY_INVALID"), codes(submit(rt, **dict(base, qty=0))))
        self.assertEqual((400, "SIDE_INVALID"), codes(submit(rt, **dict(base, side="BTO"))))
        self.assertEqual((400, "PRICE_REQUIRED"), codes(submit(rt, **dict(base, order_type="LIMIT"))))
        self.assertEqual((400, "PRICE_TICK"), codes(submit(rt, **dict(base, order_type="LIMIT", limit_px="339.001"))))
        self.assertEqual((400, "TRAIL_INVALID"), codes(submit(rt, **dict(base, order_type="TRAILING_STOP", trail_amt="2.00", trail_pct="1.00"))))
        self.assertEqual((400, "TRAIL_INVALID"), codes(submit(rt, **dict(base, order_type="TRAILING_STOP", trail_pct="100"))))
        self.assertEqual((400, "TRAIL_INVALID"), codes(submit(rt, **dict(base, order_type="TRAILING_STOP"))))
        self.assertEqual((400, "OVERSELL"), codes(submit(rt, **dict(base, side="SELL"))))
        self.assertEqual((400, "NOT_IN_THIS_BUILD"), codes(submit(rt, **dict(base, side="SELL_SHORT"))))
        self.assertEqual((400, "BAD_REQUEST_ID"), codes(submit(rt, request_id="x", **base)))
        self.assertEqual((400, "NOT_TRADEABLE_HERE"), codes(submit(rt, **dict(base, instrument_id="SPX"))))   # an index reference row: no account kinds
        with rt.db.writer() as tx:
            tx.conn.execute("UPDATE instruments SET support='UNSUPPORTED' WHERE instrument_id='SPY'")
        rt.instruments.refresh("SPY")
        self.assertEqual((400, "UNSUPPORTED"), codes(submit(rt, **dict(base, instrument_id="SPY"))))
        with rt.db.writer() as tx:
            tx.conn.execute("UPDATE instruments SET support='FULL' WHERE instrument_id='SPY'")
        rt.instruments.refresh("SPY")
        # a package without strategies -> 501 NOT_BUILT; an unknown command id is left untouched (no rows)
        r = submit(rt, account_id=US_EXCHANGE, package={"strategy_key": "IRON_CONDOR", "units": 1000})
        self.assertEqual((501, "NOT_BUILT"), codes(r))
        self.assertEqual(0, one(rt, "SELECT COUNT(*) n FROM orders")["n"])
        # the rejection audit row
        rr = orders.record_rejection(rt, r.body["request_id"], dict(base, package={"strategy_key": "IRON_CONDOR"}), r.code, r.message)
        self.assertTrue(rr.ok, rr.body)
        self.assertEqual(("NOT_BUILT", US_EXCHANGE), (lambda x: (x["code"], x["account_id"]))(one(rt, "SELECT * FROM order_rejections")))
        # a closed lifetime refuses (LIFETIME_CLOSED)
        with rt.db.writer() as tx:
            tx.conn.execute("UPDATE lifetimes SET status='CLOSED' WHERE lifetime_id=?", (T1,))
        self.assertEqual((400, "LIFETIME_CLOSED"), codes(submit(rt, **base)))
        with rt.db.writer() as tx:
            tx.conn.execute("UPDATE lifetimes SET status='OPEN' WHERE lifetime_id=?", (T1,))
        # resume of a non-suspended order -> 409 NOT_SUSPENDED
        r = submit(rt, **dict(base, order_type="LIMIT", limit_px="300.00"))
        self.assertEqual((409, "NOT_SUSPENDED"), codes(execute(rt, "order.resume", {"order_id": r.body["order"]["order_id"]})))

    def test_suspend_and_resume(self):
        # a resting order whose fill cannot be funded is SUSPENDED (attention WARN); a deposit + resume re-queues it and it fills
        rt = self.start()
        now = rt.clock.epoch()
        self.set_quote(rec("AAPL", "339.50", now - 60, now))
        r = submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=1000, order_type="LIMIT", limit_px="339.00")
        oid = r.body["order"]["order_id"]
        # Treasury 10,000,000 -> withdraw 9,999,900 leaves 100.00; the fill needs 1,000 x 339.00 + 5.00 = 339,005.00
        execute(rt, "treasury.withdraw", {"lifetime_id": T1, "amount_c": 999990000, "currency": "USD"})
        cache = FakeCache({"AAPL": rec("AAPL", "338.90", now - 30, now)})
        m = matcher_for(rt, cache)
        self.assertEqual(1, m.on_quotes(["AAPL"]))
        o = order_row(rt, oid)
        self.assertEqual(("SUSPENDED", 0), (o["status"], o["filled_qty"]))
        self.assertIn("339,005.00", o["reason"])
        self.assertEqual(1, one(rt, "SELECT COUNT(*) n FROM attention WHERE code='ORDER_SUSPENDED' AND resolved_ts IS NULL")["n"])
        self.assertEqual(0, one(rt, "SELECT COUNT(*) n FROM fills")["n"])
        self.assertEqual(0, one(rt, "SELECT COUNT(*) n FROM transfers WHERE kind='SWEEP_IN'")["n"])
        self.assertEqual(0, m.on_quotes(["AAPL"]))                                        # suspended orders are not evaluated
        deposit(rt, 100000000)
        rr = execute(rt, "order.resume", {"order_id": oid})
        self.assertEqual("WORKING", rr.body["order"]["status"])
        self.assertEqual(0, one(rt, "SELECT COUNT(*) n FROM attention WHERE code='ORDER_SUSPENDED' AND resolved_ts IS NULL")["n"])
        cache["AAPL"] = rec("AAPL", "338.90", now, now)
        self.assertEqual(1, m.on_quotes(["AAPL"]))
        self.assertEqual("FILLED", order_row(rt, oid)["status"])
        assert_integrity(self, rt, "full")

    def test_trailing_stop_sell_ratchets_and_fills(self):
        # §19.22: TRAILING_STOP SELL 1,000 AAPL trail_amt 2.00, last 339.50 at submit -> stop 337.50, ref 339.50
        rt = self.start()
        fund(rt, US_EXCHANGE, 50000000)
        buy(rt, US_EXCHANGE, "AAPL", 1000, "339.50")
        now = rt.clock.epoch()
        self.set_quote(rec("AAPL", "339.50", now - 60, now))
        r = submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="SELL", qty=1000, order_type="TRAILING_STOP", trail_amt="2.00", tif="GTC")
        self.assertTrue(r.ok, r.body)
        oid = r.body["order"]["order_id"]
        self.assertEqual(("337.5", "339.5", "WORKING"), (r.body["order"]["stop_px"], r.body["order"]["trail_ref_px"], r.body["order"]["status"]))
        cache = FakeCache()
        m = matcher_for(rt, cache)
        # last 341.00 -> RATCHET: ref 341.00, stop 339.00 (one order_event)
        cache["AAPL"] = rec("AAPL", "341.00", now - 30, now)
        self.assertEqual(1, m.on_quotes(["AAPL"]))
        o = order_row(rt, oid)
        self.assertEqual(("339", "341"), (o["stop_px"], o["trail_ref_px"]))
        self.assertEqual(1, one(rt, "SELECT COUNT(*) n FROM order_events WHERE order_id=? AND note LIKE 'RATCHET%'", (oid,))["n"])
        # last 340.00 -> unchanged (no command)
        cache["AAPL"] = rec("AAPL", "340.00", now - 20, now)
        self.assertEqual(0, m.on_quotes(["AAPL"]))
        self.assertEqual(("339", "341"), (lambda o: (o["stop_px"], o["trail_ref_px"]))(order_row(rt, oid)))
        # the ratchet survives a restart: reopen the same file and read the persisted stop
        rt.close()
        self.rt = rt = runtime(self.tmp, at="2026-10-20T14:00:00-04:00")
        rt.options = FakeOptions(rt)
        self.assertEqual(("339", "341", "WORKING"), (lambda o: (o["stop_px"], o["trail_ref_px"], o["status"]))(order_row(rt, oid)))
        # last 338.90 <= 339.00 -> TRIGGER -> fill 1,000 @ 338.90 (basis stop_print)
        m = matcher_for(rt, FakeCache({"AAPL": rec("AAPL", "338.90", now, now)}))
        self.assertEqual(1, m.on_quotes(["AAPL"]))
        o = order_row(rt, oid)
        f = one(rt, "SELECT * FROM fills WHERE order_id=?", (oid,))
        self.assertEqual(("FILLED", 1, "338.9", "stop_print", 1000), (o["status"], o["triggered"], f["price"], f["price_basis"], f["qty"]))
        # gross 1,000 x 338.90 = 338,900.00; fee 5.00; FIFO cost 339,500.00 -> realised -600.00
        self.assertEqual((33890000, 500), (f["gross_c"], f["fee_c"]))
        self.assertEqual(-60000, bal(rt, US_EXCHANGE, "4000", inst="AAPL"))
        # trail_pct 1.00 at last 341.00 -> stop 341.00 x 0.99 = 337.59
        buy(rt, US_EXCHANGE, "AAPL", 100, "341.00")
        self.set_quote(rec("AAPL", "341.00", now, now))
        r = submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="SELL", qty=100, order_type="TRAILING_STOP", trail_pct="1.00")
        self.assertEqual(("337.59", "341"), (r.body["order"]["stop_px"], r.body["order"]["trail_ref_px"]))
        assert_integrity(self, rt, "full")

    def test_trailing_stop_buy_variant(self):
        # BUY trail_amt 2.00 at 339.50 -> stop 341.50; last 338.00 -> stop 340.00; last 340.10 -> TRIGGER, fill @ 340.10
        rt = self.start()
        fund(rt, US_EXCHANGE, 50000000)
        now = rt.clock.epoch()
        self.set_quote(rec("AAPL", "339.50", now - 60, now))
        r = submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=1000, order_type="TRAILING_STOP", trail_amt="2.00")
        oid = r.body["order"]["order_id"]
        self.assertEqual("341.5", r.body["order"]["stop_px"])
        cache = FakeCache({"AAPL": rec("AAPL", "338.00", now - 30, now)})
        m = matcher_for(rt, cache)
        m.on_quotes(["AAPL"])
        self.assertEqual(("340", "338"), (lambda o: (o["stop_px"], o["trail_ref_px"]))(order_row(rt, oid)))
        cache["AAPL"] = rec("AAPL", "340.10", now, now)
        m.on_quotes(["AAPL"])
        o = order_row(rt, oid)
        f = one(rt, "SELECT * FROM fills WHERE order_id=?", (oid,))
        self.assertEqual(("FILLED", "340.1", "stop_print"), (o["status"], f["price"], f["price_basis"]))
        assert_integrity(self, rt, "full")

    def test_aon_iron_condor(self):
        # §19.22: SPY 2026-11-20 iron condor x10: BUY P700 ask 2.10 (ask_size 50), SELL P730 bid 4.00 (bid_size 50),
        # SELL C800 bid 3.50 (bid_size 5), BUY C830 ask 1.20 (ask_size 50)
        rt = self.start()
        fund(rt, US_EXCHANGE, 10000000)
        p700, p730 = ensure_option(rt, "SPY261120P00700000").instrument_id, ensure_option(rt, "SPY261120P00730000").instrument_id
        c800, c830 = ensure_option(rt, "SPY261120C00800000").instrument_id, ensure_option(rt, "SPY261120C00830000").instrument_id
        legs = [{"instrument_id": p700, "side": "BTO", "qty": 10}, {"instrument_id": p730, "side": "STO", "qty": 10},
                {"instrument_id": c800, "side": "STO", "qty": 10}, {"instrument_id": c830, "side": "BTO", "qty": 10}]
        for occ, mid in ((p700, "2.05"), (p730, "4.05"), (c800, "3.55"), (c830, "1.15")):
            rt.marks.set(occ, mid)                                                      # valuation marks (mids) so NAV is not partial
        rt.strategies = FakeStrategies(legs)
        now = rt.clock.epoch()
        qt1 = now - 60

        def chain(qt, c800_bid_size):
            return {p700: chain_rec(p700, "2.00", "2.10", 50, 50, qt, now), p730: chain_rec(p730, "4.00", "4.10", 50, 50, qt, now),
                    c800: chain_rec(c800, "3.50", "3.60", c800_bid_size, 50, qt, now), c830: chain_rec(c830, "1.10", "1.20", 50, 50, qt, now)}
        for r_ in chain(qt1, 5).values():
            self.set_quote(r_)
        r = submit(rt, account_id=US_EXCHANGE, package={"strategy_key": "IRON_CONDOR", "units": 1000, "tenor_months": 1})
        self.assertTrue(r.ok, r.body)
        pid = r.body["package"]["package_id"]
        # AON_SIZE on C800 (bid_size 5 < 10): nothing fills, every leg WORKING
        self.assertEqual(("NONE", "AON_SIZE"), (r.body["decision"]["action"], r.body["decision"]["reason"]))
        self.assertEqual(["WORKING"] * 4, [o["status"] for o in r.body["orders"]])
        self.assertEqual(["AON"] * 4, [o["fill_mode"] for o in r.body["orders"]])
        self.assertEqual(0, one(rt, "SELECT COUNT(*) n FROM fills")["n"])
        # next observation: C800 bid_size 20 -> one order.match_package (matchpkg:{pkg}:{quote_time})
        qt2 = now - 30
        cache = FakeCache(chain(qt2, 20))
        m = matcher_for(rt, cache)
        self.assertEqual(1, m.on_quotes([c800]))
        self.assertEqual(1, one(rt, "SELECT COUNT(*) n FROM commands WHERE kind='order.match_package'")["n"])
        self.assertEqual(f"matchpkg:{pid}:{qt2}", one(rt, "SELECT request_id FROM commands WHERE kind='order.match_package'")["request_id"])
        self.assertEqual(4, one(rt, "SELECT COUNT(*) n FROM fills")["n"])
        self.assertEqual(["FILLED"] * 4, [o["status"] for o in q(rt, "SELECT status FROM orders WHERE package_id=?", (pid,))])
        self.assertEqual("FILLED", one(rt, "SELECT status FROM hedge_packages WHERE package_id=?", (pid,))["status"])
        # premiums -2,100.00 + 4,000.00 + 3,500.00 - 1,200.00 = +4,200.00 credit; fees 4 x 10 x 0.65 = 26.00
        self.assertEqual(210000, bal(rt, US_EXCHANGE, "1700", inst=p700))        # Dr 1700 2,100 (10 x 2.10 x 100)
        self.assertEqual(120000, bal(rt, US_EXCHANGE, "1700", inst=c830))        # Dr 1700 1,200
        self.assertEqual(400000, bal(rt, US_EXCHANGE, "2900", inst=p730))        # Cr 2900 4,000 (10 x 4.00 x 100)
        self.assertEqual(350000, bal(rt, US_EXCHANGE, "2900", inst=c800))        # Cr 2900 3,500
        self.assertEqual(2600, bal(rt, US_EXCHANGE, "5000"))                     # 4 x 6.50
        self.assertEqual(331300, bal(rt, US_EXCHANGE, "2100"))                   # payables 2,106.50 + 1,206.50 = 3,313.00
        self.assertEqual(748700, bal(rt, US_EXCHANGE, "1200"))                   # receivables 3,993.50 + 3,493.50 = 7,487.00
        self.assertEqual(748700 - 331300, 420000 - 2600)                         # net cash = credit 4,200.00 - fees 26.00 = 4,174.00
        # margin once for the package: put spread 30 x 100 x 10 - (4,000 - 2,100) = 28,100.00; call spread 30,000 - (3,500 - 1,200) = 27,700.00 -> 28,100.00
        # SR-13 (decision s7.6): the 28,100.00 is RESERVED (margin_state), nothing is posted: 1310 0, no OPT_MARGIN entry, settled 100,000.00 untouched
        self.assertEqual(0, bal(rt, US_EXCHANGE, "1310"))
        self.assertEqual(2810000, one(rt, "SELECT options_required_c r FROM margin_state WHERE account_id=?", (US_EXCHANGE,))["r"])
        self.assertEqual(0, one(rt, "SELECT COUNT(*) n FROM journal_entries WHERE kind='OPT_MARGIN'")["n"])
        self.assertEqual(10000000, bal(rt, US_EXCHANGE, "1010"))
        # the same observation again -> no second fill
        self.assertEqual(0, m.on_quotes([p700, c800]))
        self.assertEqual(4, one(rt, "SELECT COUNT(*) n FROM fills")["n"])
        assert_integrity(self, rt, "full")
        # a stale chain -> AON_STALE on the pure rule; a package cancel cancels all four legs
        r2 = submit(rt, account_id=US_EXCHANGE, package={"strategy_key": "IRON_CONDOR", "units": 1000})
        pid2 = r2.body["package"]["package_id"]
        rows = orders.package_legs_rows(rt, rt.db.writer_conn(), pid2) if False else None
        with rt.db.reader() as conn:
            rows = orders.package_legs_rows(rt, conn, pid2)
        d = evaluate_package({"package_id": pid2}, rows, chain(qt2 - 1500, 50), rt.sessions.state("US_EQUITY_OPTIONS", now), now)
        self.assertEqual("AON_STALE", d.reason)
        rc = execute(rt, "order.cancel", {"package_id": pid2})
        self.assertEqual(4, len(rc.body["cancelled"]))
        self.assertEqual(["CANCELLED"] * 4, [o["status"] for o in q(rt, "SELECT status FROM orders WHERE package_id=?", (pid2,))])
        # AON_LEGS: a package marked AON with a stock leg
        rt.strategies = FakeStrategies([{"instrument_id": "SPY", "side": "BUY", "qty": 100}, {"instrument_id": p700, "side": "BTO", "qty": 1}], "AON")
        r3 = submit(rt, account_id=US_EXCHANGE, package={"strategy_key": "BROKEN", "units": 100})
        self.assertEqual((400, "AON_LEGS"), (r3.http, r3.code))


def one_row(rt, oid):
    with rt.db.reader() as conn:
        return conn.execute("SELECT * FROM orders WHERE order_id=?", (oid,)).fetchone()


if __name__ == "__main__":
    unittest.main()
