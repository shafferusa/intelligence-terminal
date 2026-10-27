"""WP-03 listed-option tests (TERMINAL_DESIGN.md §5.4, §11.2, §13.3, §19.7; D8).  Every expected number is worked by hand in a
comment.  The scaffolding of tests/test_terminal_orders.py is reused (runtime with the orders handlers, chain records, the
Matcher cache); the options handlers are registered here and ``rt.options`` is bound to the real module.  The SETTLE_DUE job
belongs to WP-08: a test-local handler posts the same §5.4 settlement lines.  No network, no ~/.finsim.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import types
import unittest
from datetime import date
from decimal import Decimal

from tests import test_terminal_orders as sc
from tests.terminal_helpers import T1, TREASURY, US_EXCHANGE, assert_integrity, bal, buy, execute, fund, rid
from finsim.terminal import hedges
from finsim.terminal.commands import RejectError
from finsim.terminal.db import next_id
from finsim.terminal.instruments import Instrument
from finsim.terminal.ledger import Entry, cr, dr
from finsim.terminal.products import options
from finsim.terminal.quotes import FixedMarks, QuoteRecord
from finsim.terminal.quotes.cache import ChainCache
from finsim.terminal.quotes.providers import ChainRow, ChainSnapshot, ChainUnderlying
from finsim.terminal.units import qprice

C760 = "SPY261120C00760000"
P740 = "SPY261120P00740000"
P780 = "SPY261120P00780000"
FRI_1625 = sc.ep("2026-11-20T16:25:00")        # the expiry day (EST), after the 16:00 close and the 16:20 job time
FRI_CLOSE = sc.ep("2026-11-20T16:00:00")


def D(x) -> Decimal:
    return Decimal(str(x))


def _h_settle_due(ctx, payload):
    """What WP-08's SETTLE_DUE posts: receivable Dr 1010 / Cr 1200; payable Dr 2100 / Cr 1010 (idem settle:{settle_id})."""
    rows = ctx.conn.execute("SELECT * FROM settlements WHERE status='PENDING' AND due_date <= ? ORDER BY settle_id", (payload["as_of"],)).fetchall()
    out = []
    for s in rows:
        amt, aid, ccy = int(s["amount_c"]), s["account_id"], s["currency"]
        lines = [dr(aid, "1010", ccy, amt), cr(aid, s["gl"], ccy, amt)] if amt > 0 else [dr(aid, s["gl"], ccy, -amt), cr(aid, "1010", ccy, -amt)]
        eid = ctx.post(Entry("SETTLE", f"settlement {s['settle_id']} of fill {s['fill_id']}", lines, f"settle:{s['settle_id']}", ref_type="settlement", ref_id=s["settle_id"]))
        ctx.conn.execute("UPDATE settlements SET status='SETTLED', settled_ts=?, entry_id=? WHERE settle_id=?", (ctx.ts, eid, s["settle_id"]))
        out.append(s["settle_id"])
    return {"settled": out}


class FakeLending:
    """WP-06 stand-in for the exercise-needs-a-borrow path: a locate that is always available and a borrows row with no
    collateral (L12 needs the OPEN borrow, L13 ties 1400 = collateral_c = 0)."""

    def locate(self, services, account_id, inst, qty):
        return types.SimpleNamespace(available=True, qty_available=int(qty), basis="fake locate (test)")

    def open_borrow(self, ctx, services, account_id, inst, qty, locate, order_id):
        bid = next_id(ctx.conn, "borrow")
        ctx.conn.execute("INSERT INTO borrows(borrow_id, command_id, account_id, instrument_id, qty, qty_returned, fee_rate, rebate_rate, tier, lender, collateral_c,"
                         " currency, opened_ts, opened_date, status, order_id, reason) VALUES (?,?,?,?,?,0,'0.003','0.03','T1','fake',0,'USD',?,?,'OPEN',NULL,'exercise delivery')",
                         (bid, ctx.command_id, account_id, inst.instrument_id, int(qty), ctx.ts, ctx.clock.today().isoformat()))
        return bid

    def consume_for_short(self, ctx, borrow_id, qty, fill_id):
        ctx.conn.execute("UPDATE borrows SET reason=NULL WHERE borrow_id=?", (borrow_id,))


class _Master:
    def __init__(self, *insts):
        self._d = {i.instrument_id: i for i in insts}

    def get(self, iid, conn=None):
        return self._d.get(iid)


def _inst(iid, kind, **kw) -> Instrument:
    base = dict(instrument_id=iid, kind=kind, symbol=iid, name=iid, currency="USD", multiplier=D(1), tick=D("0.01"), lot=1, price_scale=D(1), session_group="US_EQUITY",
                settle_days=1, account_kinds=("US_EXCHANGE",), yahoo_symbol=iid, cboe_symbol=None, underlying_id=None, expiry=None, strike=None, right=None, style=None,
                exercise=None, spec={}, support="FULL", status="ACTIVE")
    base.update(kw)
    return Instrument(**base)


def _opt(iid, under, right, strike, expiry="2026-11-20", index=False) -> Instrument:
    return _inst(iid, "OPTION", multiplier=D(100), session_group="US_INDEX_OPTIONS" if index else "US_EQUITY_OPTIONS", cboe_symbol=iid, underlying_id=under,
                 expiry=expiry, strike=D(strike), right=right, style="EUROPEAN" if index else "AMERICAN", exercise="CASH" if index else "PHYSICAL", spec={"index": index})


def _pos(iid, qty, cost_c, kind="OPTION"):
    return types.SimpleNamespace(instrument_id=iid, kind=kind, qty=qty, cost_c=cost_c, account_id=US_EXCHANGE)


class MarginPinTests(unittest.TestCase):
    """§13.3 formula pins, pure: positions + marks + a tiny master, no database."""

    def setUp(self):
        self.master = _Master(_inst("SPY", "ETF"), _inst("AAPL", "EQUITY"), _inst("SPX", "INDEX"),
                              _opt(C760, "SPY", "C", 760), _opt(P740, "SPY", "P", 740), _opt("AAPL261120P00330000", "AAPL", "P", 330),
                              _opt("SPY261120P00730000", "SPY", "P", 730), _opt("SPY261120P00700000", "SPY", "P", 700),
                              _opt("SPY261120C00800000", "SPY", "C", 800), _opt("SPY261120C00830000", "SPY", "C", 830),
                              _opt("SPXW261218P05000000", "SPX", "P", 5000, "2026-12-18", index=True))

    def req(self, positions, marks):
        return options.margin_requirement_c(positions, FixedMarks(marks, as_of=sc.TUE_1400), instruments=self.master)

    def test_19_7_and_s7_pins(self):
        # §19.7: 5 short P740, mark 9.80, S 769.32: per share max(9.80 + 0.20 x 769.32 - max(0, 769.32 - 740), 9.80 + 0.10 x 740)
        # = max(9.80 + 153.864 - 29.32, 83.80) = max(134.344, 83.80) = 134.344; x 100 x 5 = 67,172.00
        r, d = self.req([_pos(P740, -5, -490000)], {"SPY": "769.32", P740: "9.80"})
        self.assertEqual(6717200, r)
        item = d["groups"][0]["items"][0]
        self.assertEqual(("naked_put", "134.344", "9.8", 5), (item["rule"], item["per_share"].rstrip("0"), item["premium"], item["n"]))
        self.assertEqual(("sum", 0, 6717200), (d["groups"][0]["sides"]["combine"], d["groups"][0]["sides"]["call_c"], d["groups"][0]["sides"]["put_c"]))
        # product S7: S 339.50, K 330 put, premium 4.00: max(4 + 67.90 - 9.50, 4 + 33.00) = 62.40 x 100 = 6,240.00 per contract
        r, _ = self.req([_pos("AAPL261120P00330000", -1, -40000)], {"AAPL": "339.50", "AAPL261120P00330000": "4.00"})
        self.assertEqual(624000, r)
        # naked call: 1 short C760 mark 15.20, S 769.32: max(15.20 + 153.864 - max(0, 760 - 769.32), 15.20 + 76.932) = 169.064 x 100 = 16,906.40
        r, _ = self.req([_pos(C760, -1, -152000)], {"SPY": "769.32", C760: "15.20"})
        self.assertEqual(1690640, r)
        # a strangle (naked put + naked call) adds: 67,172.00 + 16,906.40 = 84,078.40
        r, d = self.req([_pos(P740, -5, -490000), _pos(C760, -1, -152000)], {"SPY": "769.32", P740: "9.80", C760: "15.20"})
        self.assertEqual((8407840, "sum"), (r, d["groups"][0]["sides"]["combine"]))
        # long options need no margin
        r, d = self.req([_pos(C760, 10, 1520000)], {"SPY": "769.32", C760: "15.20"})
        self.assertEqual((0, []), (r, d["groups"]))

    def test_covered_call_and_reserved_shares(self):
        # 100 long SPY covers 1 short C760 -> 0, the 100 shares reserved
        r, d = self.req([_pos("SPY", 100, 7693200, "ETF"), _pos(C760, -1, -152000)], {"SPY": "769.32", C760: "15.20"})
        self.assertEqual((0, {"SPY": 100}), (r, d["shares_reserved"]))
        self.assertEqual("covered_call", d["groups"][0]["items"][0]["rule"])
        # 150 shares and 2 short calls: 1 covered (0) + 1 naked (16,906.40); 100 reserved
        r, d = self.req([_pos("SPY", 150, 11539800, "ETF"), _pos(C760, -2, -304000)], {"SPY": "769.32", C760: "15.20"})
        self.assertEqual((1690640, {"SPY": 100}), (r, d["shares_reserved"]))
        self.assertEqual(["covered_call", "naked_call"], [i["rule"] for i in d["groups"][0]["items"]])

    def test_spreads_and_iron_condor(self):
        marks = {"SPY": "769.32", "SPY261120P00730000": "4.05", "SPY261120P00700000": "2.05", "SPY261120C00800000": "3.55", "SPY261120C00830000": "1.15"}
        # §19.22 put credit spread: short 10 P730 (proceeds 4,000.00), long 10 P700 (cost 2,100.00): 30 x 100 x 10 - (4,000 - 2,100) = 28,100.00
        r, d = self.req([_pos("SPY261120P00730000", -10, -400000), _pos("SPY261120P00700000", 10, 210000)], marks)
        self.assertEqual(2810000, r)
        self.assertEqual(("vertical_spread", "30", 190000), (lambda i: (i["rule"], i["width"], i["credit_c"]))(d["groups"][0]["items"][0]))
        # the iron condor adds the call spread 30,000 - (3,500 - 1,200) = 27,700.00: both sides spreads -> max = 28,100.00
        r, d = self.req([_pos("SPY261120P00730000", -10, -400000), _pos("SPY261120P00700000", 10, 210000), _pos("SPY261120C00800000", -10, -350000),
                         _pos("SPY261120C00830000", 10, 120000)], marks)
        self.assertEqual((2810000, "max", 2770000, 2810000), (r, d["groups"][0]["sides"]["combine"], d["groups"][0]["sides"]["call_c"], d["groups"][0]["sides"]["put_c"]))
        # a debit spread (long 10 P730 / short 10 P700): the long strike covers the short fully -> 0
        r, d = self.req([_pos("SPY261120P00730000", 10, 400000), _pos("SPY261120P00700000", -10, -210000)], marks)
        self.assertEqual((0, "spread_covered"), (r, d["groups"][0]["items"][0]["rule"]))

    def test_index_option_and_mark_fallbacks(self):
        # index naked put: 1 short SPXW P5000, mark 50.00, S 5,200: max(50 + 0.15 x 5,200 - max(0, 5,200 - 5,000), 50 + 0.10 x 5,000)
        # = max(50 + 780 - 200, 550) = 630 x 100 = 63,000.00
        r, d = self.req([_pos("SPXW261218P05000000", -1, -500000)], {"SPX": "5200", "SPXW261218P05000000": "50.00"})
        self.assertEqual((6300000, "index_put"), (r, d["groups"][0]["items"][0]["rule"]))
        # no mark for the contract -> its cost basis stands in (5 contracts, proceeds 4,900.00 -> 9.80 per share), labelled
        r, d = self.req([_pos(P740, -5, -490000)], {"SPY": "769.32"})
        self.assertEqual(6717200, r)
        self.assertEqual("cost basis (no mark for the contract)", d["groups"][0]["items"][0]["premium_source"])
        # no mark for the underlying -> MARK_UNAVAILABLE, never a number
        with self.assertRaises(RejectError) as cm:
            self.req([_pos(P740, -5, -490000)], {P740: "9.80"})
        self.assertEqual("MARK_UNAVAILABLE", cm.exception.code)


class OptionScenarioTests(unittest.TestCase):
    """§19.7 through the bus: fills, margin, settlement, expiry / exercise / assignment, skips, early exercise."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-opt-")
        self.rt = None

    def tearDown(self):
        if self.rt is not None:
            self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start(self, at="2026-10-20T14:00:00-04:00", marks=None):
        self.rt = sc.runtime(self.tmp, at=at, marks=marks)
        options.register(self.rt.bus)
        self.rt.options = options
        self.rt.hedges = hedges
        self.rt.bus.register("test.settle_due", _h_settle_due)
        return self.rt

    def settle(self, as_of):
        r = execute(self.rt, "test.settle_due", {"as_of": as_of}, actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        return r.body["settled"]

    def expire(self, iid, request_id=None):
        return execute(self.rt, "options.expire", {"account_id": US_EXCHANGE, "instrument_id": iid}, request_id or rid("job:OPTION_EXPIRY"), actor="lifecycle")

    def count(self, sql, params=()):
        return sc.one(self.rt, sql, params)["n"]

    def setup_19_7(self):
        """US_EXCHANGE settled 1,000,000.00; BTO 10 C760 @ 15.20 and STO 5 P740 @ 9.80 on Tue 10-20; settled Wed 00:35."""
        rt = self.start()
        fund(rt, US_EXCHANGE, 100000000)
        now = rt.clock.epoch()
        sc.ensure_option(rt, C760)
        sc.ensure_option(rt, P740)
        rt.marks.set(C760, "15.15")                       # Cboe mid 15.10 / 15.20
        rt.marks.set(P740, "9.80")                        # the test pins bid = ask = 9.80 so the mark is 9.80 (§19.7)
        rt.marks.set_quote(sc.chain_rec(C760, "15.10", "15.20", 50, 50, now - 120, now))
        rt.marks.set_quote(sc.chain_rec(P740, "9.80", "9.90", 50, 50, now - 120, now))
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id=C760, side="BTO", qty=10, order_type="MARKET")
        self.assertTrue(r.ok, r.body)
        # BTO 10 @ ask 15.20: premium 10 x 15.20 x 100 = 15,200.00; fee 10 x 0.65 = 6.50; Dr 1700 15,200 / Dr 5000 6.50 / Cr 2100 15,206.50 (T+1)
        f = r.body["fills"][0]
        self.assertEqual((1520000, 650, "ask", "2026-10-21"), (f["gross_c"], f["fee_c"], f["price_basis"], f["settle_date"]))
        self.assertEqual((1520000, 650, 1520650, 0), (bal(rt, US_EXCHANGE, "1700", inst=C760), bal(rt, US_EXCHANGE, "5000"), bal(rt, US_EXCHANGE, "2100"), f["margin"]["required_c"]))
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id=P740, side="STO", qty=5, order_type="MARKET")
        self.assertTrue(r.ok, r.body)
        # STO 5 @ bid 9.80: premium 4,900.00; fee 3.25; Dr 1200 4,896.75 / Dr 5000 3.25 / Cr 2900 4,900.00
        f = r.body["fills"][0]
        self.assertEqual((490000, 325, "bid"), (f["gross_c"], f["fee_c"], f["price_basis"]))
        self.assertEqual((489675, 975, 490000), (bal(rt, US_EXCHANGE, "1200"), bal(rt, US_EXCHANGE, "5000"), bal(rt, US_EXCHANGE, "2900", inst=P740)))
        # the margin after the STO: 134.344 x 100 x 5 = 67,172.00, RESERVED (decision s7.6, SR-13): margin_state 67,172.00, nothing posted
        # (1310 = 0, settled 1010 still 1,000,000.00); available at the T+1 view = 1,000,000.00 + 4,896.75 - 15,206.50 - 67,172.00 = 922,518.25
        self.assertEqual((6717200, 0), (f["margin"]["required_c"], bal(rt, US_EXCHANGE, "1310")))
        self.assertEqual(6717200, sc.one(rt, "SELECT options_required_c r FROM margin_state WHERE account_id=?", (US_EXCHANGE,))["r"])
        self.assertEqual(100000000, bal(rt, US_EXCHANGE, "1010"))
        self.assertEqual(0, self.count("SELECT COUNT(*) n FROM journal_entries WHERE kind='OPT_MARGIN'"))
        cv = rt.accounting.cash(US_EXCHANGE)["USD"]
        self.assertEqual((6717200, 6717200, 92251825), (cv.options_required_c, cv.reserved_c, cv.available_c))
        assert_integrity(self, rt, "full")
        # after the T+1 settlements: settled = 1,000,000 - 15,206.50 + 4,896.75 = 989,690.25; available = 989,690.25 - 67,172.00 reserved = 922,518.25
        rt.clock.set("2026-10-21T00:35:00-04:00")
        self.assertEqual(2, len(self.settle("2026-10-21")))
        self.assertEqual(98969025, bal(rt, US_EXCHANGE, "1010"))
        self.assertEqual(92251825, rt.accounting.cash(US_EXCHANGE)["USD"].available_c)
        return rt

    def at_expiry(self, close):
        rt = self.rt
        rt.clock.set("2026-11-20T16:25:00-05:00")
        self.assertEqual(FRI_1625, rt.clock.epoch())
        rt.marks.set("SPY", close, "close", quote_time=FRI_CLOSE)          # the observed CLOSE of the expiry date (quote_time 16:00:00)
        return rt

    def test_d8_expiry_exercise_lapse_assignment(self):
        rt = self.setup_19_7()
        # before the close the expiry key is not due; the handler refuses to run while the session is open
        rt.clock.set("2026-11-20T15:00:00-05:00")
        with rt.db.reader() as conn:
            self.assertEqual([], options.expiry_keys(conn, rt.clock.epoch()))
        self.assertEqual((409, "SESSION_OPEN"), (lambda r: (r.http, r.code))(self.expire(P740)))
        rt = self.at_expiry("772.00")
        with rt.db.reader() as conn:   # written contracts first (qty -5 before +10): a spread's long leg keeps covering its short until the short settles
            self.assertEqual([(US_EXCHANGE, P740), (US_EXCHANGE, C760)], options.expiry_keys(conn, rt.clock.epoch()))
        entries_before = self.count("SELECT COUNT(*) n FROM journal_entries")
        # P740: intrinsic max(0, 740 - 772) = 0 -> EXPIRY fill at 0: Dr 2900 4,900.00 / Cr 4000 4,900.00; the reservation drops 67,172.00 -> 0
        r = self.expire(P740, "job:OPTION_EXPIRY:T1:US_EXCHANGE:P740:1")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("DONE", "EXPIRY", True, "0"), (r.body["status"], r.body["kind"], r.body["posted"], r.body["intrinsic"]))
        self.assertEqual((0, 490000, 0, 0), (bal(rt, US_EXCHANGE, "2900", inst=P740), bal(rt, US_EXCHANGE, "4000", inst=P740), bal(rt, US_EXCHANGE, "1310"),
                                            sc.one(rt, "SELECT options_required_c r FROM margin_state WHERE account_id=?", (US_EXCHANGE,))["r"]))
        # the EXPIRY fill defers the fill path's recompute; the handler's single recompute (overdraft rule) lowers the reservation by 0 - 67,172.00 =
        # -67,172.00; no OPT_MARGIN entry exists at all (a reservation moves no cash)
        self.assertEqual((0, -6717200, 0), (r.body["margin"]["required_c"], r.body["margin"]["delta_c"], self.count("SELECT COUNT(*) n FROM journal_entries WHERE kind='OPT_MARGIN'")))
        f = sc.one(rt, "SELECT * FROM fills WHERE idem_key=?", (f"expiry:{US_EXCHANGE}:{P740}",))
        self.assertEqual(("BUY", 5, "0", "EXPIRY", None), (f["side"], f["qty"], f["price"], f["origin"], f["settle_date"]))
        # C760: intrinsic 772 - 760 = 12.00 >= 0.01 -> EXERCISE: BUY 1,000 SPY @ 760, fee 0: Dr 1100 775,200 (760,000 + 15,200) / Cr 1700 15,200 / Cr 2100 760,000
        r = self.expire(C760, "job:OPTION_EXPIRY:T1:US_EXCHANGE:C760:1")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("DONE", "EXERCISE", "12", "BUY", 77520000), (r.body["status"], r.body["kind"], r.body["intrinsic"], r.body["result"]["stock_side"], r.body["result"]["cost_c"]))
        self.assertEqual((77520000, 0, 76000000), (bal(rt, US_EXCHANGE, "1100", inst="SPY"), bal(rt, US_EXCHANGE, "1700", inst=C760), bal(rt, US_EXCHANGE, "2100")))
        self.assertEqual(0, r.body["margin"]["delta_c"])                                           # nothing written remains: 0 -> 0
        self.assertEqual(0, self.count("SELECT COUNT(*) n FROM journal_entries WHERE kind='OPT_MARGIN'"))   # a reservation never posts
        self.assertEqual(0, self.count("SELECT COUNT(*) n FROM transfers WHERE kind='SWEEP_IN'"))          # settled 989,690.25 >= 760,000.00: no sweep
        st = sc.one(rt, "SELECT * FROM settlements WHERE fill_id=?", (r.body["result"]["stock_fill_id"],))
        self.assertEqual((-76000000, "2100", "2026-11-23", "PENDING"), (st["amount_c"], st["gl"], st["due_date"], st["status"]))   # due Mon 11-23
        lot = sc.one(rt, "SELECT * FROM lots WHERE instrument_id='SPY' AND qty_remaining > 0")
        self.assertEqual((1000, "775.2", 77520000, "LONG"), (lot["qty_remaining"], lot["unit_cost"], lot["cost_c"], lot["side"]))      # LONG lot 1,000 @ 775.20
        fo = sc.one(rt, "SELECT * FROM fills WHERE idem_key=?", (f"expiry:{US_EXCHANGE}:{C760}:OPT",))
        fs = sc.one(rt, "SELECT * FROM fills WHERE idem_key=?", (f"expiry:{US_EXCHANGE}:{C760}:UND",))
        self.assertEqual(("SELL", 10, "0", "EXERCISE", None, 0), (fo["side"], fo["qty"], fo["price"], fo["origin"], fo["settle_date"], fo["fee_c"]))
        self.assertEqual(("BUY", 1000, "760", "EXERCISE", "2026-11-23", 76000000, fo["entry_id"]), (fs["side"], fs["qty"], fs["price"], fs["origin"], fs["settle_date"], fs["gross_c"], fs["entry_id"]))
        # result: realised +4,900.00; position 1,000 SPY cost 775,200.00; MV at 772 = 772,000.00; unrealised -3,200.00; fees 9.75; both EXPIRED
        self.assertEqual(490000, bal(rt, US_EXCHANGE, "4000"))
        p = rt.accounting.position(US_EXCHANGE, "SPY")
        self.assertEqual((1000, 77520000, 77200000, -320000), (p.qty, p.cost_c, p.mv_c, p.unrealised_c))
        self.assertEqual(975, bal(rt, US_EXCHANGE, "5000"))
        self.assertEqual(("EXPIRED", "EXPIRED"), (rt.instruments.get(C760).status, rt.instruments.get(P740).status))
        self.assertEqual({f"{US_EXCHANGE}:{C760}": "DONE", f"{US_EXCHANGE}:{P740}": "DONE"},
                         {r_["key"]: r_["status"] for r_ in sc.q(rt, "SELECT key, status FROM jobs_done WHERE job='OPTION_EXPIRY'")})
        self.assertEqual("772", sc.one(rt, "SELECT price FROM marks WHERE instrument_id='SPY' AND mark_date='2026-11-20' AND kind='CLOSE'")["price"])
        # a second run (new request ids) posts nothing
        n = self.count("SELECT COUNT(*) n FROM journal_entries")
        for iid in (C760, P740):
            r2 = self.expire(iid)
            self.assertEqual((True, "DONE", False), (r2.ok, r2.body["status"], r2.body["posted"]))
        self.assertEqual(n, self.count("SELECT COUNT(*) n FROM journal_entries"))
        self.assertEqual(entries_before + 2, n)         # P740 expiry and the C760 exercise, nothing else (no margin entry: a reservation)
        assert_integrity(self, rt, "full")
        # Mon 11-23: the 760,000.00 payable settles: 989,690.25 - 760,000.00 = 229,690.25
        rt.clock.set("2026-11-23T00:35:00-05:00")
        self.settle("2026-11-23")
        self.assertEqual(22969025, bal(rt, US_EXCHANGE, "1010"))
        assert_integrity(self, rt, "full")

    def test_variant_close_735_assignment(self):
        rt = self.setup_19_7()
        rt = self.at_expiry("735.00")
        # P740 ITM 5.00 -> ASSIGNMENT BUY 500 SPY @ 740: Dr 1100 365,100.00 (370,000 - 4,900) / Dr 2900 4,900.00 / Cr 2100 370,000.00; lot 500 @ 730.20
        r = self.expire(P740)
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("ASSIGNMENT", "5", 36510000, "BUY"), (r.body["kind"], r.body["intrinsic"], r.body["result"]["cost_c"], r.body["result"]["stock_side"]))
        self.assertEqual((36510000, 0, 37000000), (bal(rt, US_EXCHANGE, "1100", inst="SPY"), bal(rt, US_EXCHANGE, "2900", inst=P740), bal(rt, US_EXCHANGE, "2100")))
        lot = sc.one(rt, "SELECT * FROM lots WHERE instrument_id='SPY' AND qty_remaining > 0")
        self.assertEqual((500, "730.2", 36510000), (lot["qty_remaining"], lot["unit_cost"], lot["cost_c"]))
        self.assertEqual((0, 0), (bal(rt, US_EXCHANGE, "1310"), sc.one(rt, "SELECT options_required_c r FROM margin_state WHERE account_id=?", (US_EXCHANGE,))["r"]))
        # the 67,172.00 reservation is gone (no written option remains) and nothing was ever posted to 1310
        # C760 OTM -> Dr 4000 15,200.00 / Cr 1700 15,200.00
        r = self.expire(C760)
        self.assertEqual(("EXPIRY", 0, -1520000), (r.body["kind"], bal(rt, US_EXCHANGE, "1700", inst=C760), bal(rt, US_EXCHANGE, "4000", inst=C760)))
        # MV 500 x 735 = 367,500.00; unrealised +2,400.00; realised -15,200.00
        p = rt.accounting.position(US_EXCHANGE, "SPY")
        self.assertEqual((500, 36510000, 36750000, 240000), (p.qty, p.cost_c, p.mv_c, p.unrealised_c))
        self.assertEqual(-1520000, bal(rt, US_EXCHANGE, "4000"))
        self.assertEqual(0, self.count("SELECT COUNT(*) n FROM transfers WHERE kind='SWEEP_IN'"))
        assert_integrity(self, rt, "full")

    def test_missing_close_skips_then_completes_once(self):
        rt = self.setup_19_7()
        rt.clock.set("2026-11-20T16:25:00-05:00")        # no observed close: the SPY mark still carries Tuesday's quote_time
        r = self.expire(P740)
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("SKIPPED", "EXPIRY_PENDING_CLOSE"), (r.body["status"], r.body["code"]))
        key = f"{US_EXCHANGE}:{P740}"
        self.assertEqual("SKIPPED", sc.one(rt, "SELECT status FROM jobs_done WHERE job='OPTION_EXPIRY' AND key=?", (key,))["status"])
        self.assertEqual(1, self.count("SELECT COUNT(*) n FROM attention WHERE code='EXPIRY_PENDING_CLOSE' AND ref_id=? AND resolved_ts IS NULL", (key,)))
        self.assertEqual(-5, rt.accounting.position(US_EXCHANGE, P740).qty)                              # the position stays open
        self.assertEqual(("ACTIVE", 6717200), (rt.instruments.get(P740).status, sc.one(rt, "SELECT options_required_c r FROM margin_state WHERE account_id=?", (US_EXCHANGE,))["r"]))
        r = self.expire(P740)                                                                              # another tick, still no close: one open item
        self.assertEqual("SKIPPED", r.body["status"])
        self.assertEqual(1, self.count("SELECT COUNT(*) n FROM attention WHERE code='EXPIRY_PENDING_CLOSE' AND ref_id=?", (key,)))
        # a later tick with the mark completes it once
        self.at_expiry("772.00")
        r = self.expire(P740)
        self.assertEqual(("DONE", "EXPIRY", True), (r.body["status"], r.body["kind"], r.body["posted"]))
        self.assertEqual(0, self.count("SELECT COUNT(*) n FROM attention WHERE code='EXPIRY_PENDING_CLOSE' AND ref_id=? AND resolved_ts IS NULL", (key,)))
        n = self.count("SELECT COUNT(*) n FROM journal_entries")
        self.assertEqual(False, self.expire(P740).body["posted"])
        self.assertEqual(n, self.count("SELECT COUNT(*) n FROM journal_entries"))
        assert_integrity(self, rt, "full")

    def test_early_exercise_thursday(self):
        rt = self.setup_19_7()
        rt.clock.set("2026-11-19T14:00:00-05:00")
        r = execute(rt, "options.exercise", {"account_id": US_EXCHANGE, "instrument_id": C760, "qty": 10}, "ui:exercise-c760-1")
        self.assertTrue(r.ok, r.body)
        # the same bookings as the expiry exercise, idem exercise:{account}:{inst}:{request_id}: Dr 1100 775,200 / Cr 1700 15,200 / Cr 2100 760,000, due Fri 11-20
        idem = f"exercise:{US_EXCHANGE}:{C760}:ui:exercise-c760-1"
        self.assertEqual(idem, r.body["idem_key"])
        self.assertEqual((77520000, 0, 76000000), (bal(rt, US_EXCHANGE, "1100", inst="SPY"), bal(rt, US_EXCHANGE, "1700", inst=C760), bal(rt, US_EXCHANGE, "2100")))
        self.assertEqual(2, self.count("SELECT COUNT(*) n FROM fills WHERE idem_key IN (?, ?)", (idem + ":OPT", idem + ":UND")))
        self.assertEqual("2026-11-20", r.body["result"]["settle_date"])
        self.assertEqual((0, 1000), (r.body["position"]["qty"], r.body["position"]["underlying_qty"]))
        self.assertEqual([], r.body["warnings"])                                                            # 760 < 769.32: in the money, no warning
        assert_integrity(self, rt, "full")

    def test_early_exercise_refusals(self):
        rt = self.setup_19_7()
        rt.clock.set("2026-11-19T14:00:00-05:00")
        codes = lambda r: (r.http, r.code)   # noqa: E731
        self.assertEqual((409, "NOT_LONG"), codes(execute(rt, "options.exercise", {"account_id": US_EXCHANGE, "instrument_id": C760, "qty": 11})))
        self.assertEqual((409, "NOT_LONG"), codes(execute(rt, "options.exercise", {"account_id": US_EXCHANGE, "instrument_id": P740, "qty": 1})))
        self.assertEqual((400, "QTY_INVALID"), codes(execute(rt, "options.exercise", {"account_id": US_EXCHANGE, "instrument_id": C760, "qty": 0})))
        spx = sc.ensure_option(rt, "SPXW261218C05000000", "SPX")
        self.assertEqual(("EUROPEAN", "CASH", "US_INDEX_OPTIONS"), (spx.style, spx.exercise, spx.session_group))
        self.assertEqual((400, "NOT_EXERCISABLE"), codes(execute(rt, "options.exercise", {"account_id": US_EXCHANGE, "instrument_id": spx.instrument_id, "qty": 1})))
        rt.clock.set("2026-11-19T17:00:00-05:00")
        self.assertEqual((409, "SESSION_CLOSED"), codes(execute(rt, "options.exercise", {"account_id": US_EXCHANGE, "instrument_id": C760, "qty": 10})))
        # an out-of-the-money exercise is allowed and warned: SPY mark 750 < 760
        rt.clock.set("2026-11-19T14:00:00-05:00")
        rt.marks.set("SPY", "750.00")
        r = execute(rt, "options.exercise", {"account_id": US_EXCHANGE, "instrument_id": C760, "qty": 1})
        self.assertTrue(r.ok, r.body)
        self.assertIn("out of the money", r.body["warnings"][0])
        # 1 contract: Dr 1100 100 x 760 + 1,520.00 (one tenth of 15,200) = 77,520.00 / Cr 1700 1,520.00 / Cr 2100 76,000.00; lot 100 @ 775.20
        self.assertEqual((7752000, 1520000 - 152000, 7600000), (bal(rt, US_EXCHANGE, "1100", inst="SPY"), bal(rt, US_EXCHANGE, "1700", inst=C760), bal(rt, US_EXCHANGE, "2100")))
        assert_integrity(self, rt, "full")

    def test_put_exercise_with_and_without_shares(self):
        rt = self.start()
        fund(rt, US_EXCHANGE, 100000000)
        now = rt.clock.epoch()
        sc.ensure_option(rt, P780)
        rt.marks.set(P780, "11.95")
        rt.marks.set_quote(sc.chain_rec(P780, "11.90", "12.00", 50, 50, now - 120, now))
        # BTO 10 P780 @ 12.00: cost 12,000.00, fee 6.50
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id=P780, side="BTO", qty=10, order_type="MARKET")
        self.assertTrue(r.ok, r.body)
        rt.clock.set("2026-10-21T00:35:00-04:00")
        self.settle("2026-10-21")
        rt = self.at_expiry("772.00")                     # intrinsic 780 - 772 = 8.00 -> EXERCISE: SELL 1,000 SPY @ 780
        # no shares and no lending module -> SKIPPED EXERCISE_NEEDS_BORROW; nothing cash-settled
        r = self.expire(P780)
        self.assertEqual(("SKIPPED", "EXERCISE_NEEDS_BORROW"), (r.body["status"], r.body["code"]))
        self.assertEqual((10, 1200000), (rt.accounting.position(US_EXCHANGE, P780).qty, bal(rt, US_EXCHANGE, "1700", inst=P780)))
        # with a borrow (FakeLending): Dr 1200 780,000 / Cr 1700 12,000 / Cr 2600 768,000; SHORT lot 1,000 @ 768.00 (780 - 12,000 / 1,000); realised 0
        rt.lending = FakeLending()
        r = self.expire(P780)
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("DONE", "EXERCISE", "SELL", 1000, 76800000), (r.body["status"], r.body["kind"], r.body["result"]["stock_side"], r.body["result"]["short_qty"], r.body["result"]["proceeds_c"]))
        self.assertEqual((78000000, 0, 76800000, 0), (bal(rt, US_EXCHANGE, "1200"), bal(rt, US_EXCHANGE, "1700", inst=P780), bal(rt, US_EXCHANGE, "2600", inst="SPY"),
                                                      bal(rt, US_EXCHANGE, "4000")))
        lot = sc.one(rt, "SELECT * FROM lots WHERE instrument_id='SPY' AND qty_remaining > 0")
        self.assertEqual(("SHORT", 1000, "768", 76800000), (lot["side"], lot["qty_remaining"], lot["unit_cost"], lot["cost_c"]))
        self.assertEqual(1, self.count("SELECT COUNT(*) n FROM borrows WHERE status='OPEN' AND instrument_id='SPY' AND qty=1000"))
        self.assertEqual(0, self.count("SELECT COUNT(*) n FROM attention WHERE code='EXERCISE_NEEDS_BORROW' AND resolved_ts IS NULL"))
        assert_integrity(self, rt, "full")

    def test_put_exercise_against_long_shares(self):
        rt = self.start()
        fund(rt, US_EXCHANGE, 200000000)
        now = rt.clock.epoch()
        buy(rt, US_EXCHANGE, "SPY", 1000, "769.32")                                              # cost 769,320.00 (fee 5.00 separately)
        sc.ensure_option(rt, P780)
        rt.marks.set(P780, "11.95")
        rt.marks.set_quote(sc.chain_rec(P780, "11.90", "12.00", 50, 50, now - 120, now))
        self.assertTrue(sc.submit(rt, account_id=US_EXCHANGE, instrument_id=P780, side="BTO", qty=10, order_type="MARKET").ok)
        rt.clock.set("2026-10-21T00:35:00-04:00")
        self.settle("2026-10-21")
        rt = self.at_expiry("772.00")
        # long put exercise with shares: Dr 1200 780,000 / Cr 1700 12,000 / Cr 1100 769,320 / Dr 4000 1,320: realised = (780,000 - 12,000) - 769,320 = -1,320.00
        r = self.expire(P780)
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("SELL", 0, 76932000, -132000), (r.body["result"]["stock_side"], r.body["result"]["short_qty"], r.body["result"]["cost_relieved_c"], r.body["result"]["realised_c"]))
        self.assertEqual((78000000, 0, 0, -132000), (bal(rt, US_EXCHANGE, "1200"), bal(rt, US_EXCHANGE, "1700", inst=P780), bal(rt, US_EXCHANGE, "1100", inst="SPY"),
                                                     bal(rt, US_EXCHANGE, "4000", inst="SPY")))
        self.assertIsNone(rt.accounting.position(US_EXCHANGE, "SPY"))
        st = sc.one(rt, "SELECT * FROM settlements WHERE fill_id=?", (r.body["result"]["stock_fill_id"],))
        self.assertEqual((78000000, "1200", "2026-11-23"), (st["amount_c"], st["gl"], st["due_date"]))
        assert_integrity(self, rt, "full")

    def test_index_option_cash_settlement(self):
        rt = self.start()
        fund(rt, US_EXCHANGE, 100000000)
        now = rt.clock.epoch()
        spx = sc.ensure_option(rt, "SPXW261218P05100000", "SPX")
        iid = spx.instrument_id
        rt.marks.set("SPX", "5200.00")
        rt.marks.set(iid, "40.50")
        rt.marks.set_quote(sc.chain_rec(iid, "40.00", "41.00", 50, 50, now - 120, now))
        # STO 2 @ bid 40.00: premium 2 x 40 x 100 = 8,000.00; fee 1.30; index naked put margin per share
        # max(40.50 + 0.15 x 5,200 - max(0, 5,200 - 5,100), 40.50 + 0.10 x 5,100) = max(720.50, 550.50) = 720.50 x 100 x 2 = 144,100.00
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id=iid, side="STO", qty=2, order_type="MARKET")
        self.assertTrue(r.ok, r.body)
        # reserved, not posted (SR-13): margin_state 144,100.00, 1310 0
        self.assertEqual((800000, 130, 14410000, 0), (r.body["fills"][0]["gross_c"], r.body["fills"][0]["fee_c"], r.body["fills"][0]["margin"]["required_c"], bal(rt, US_EXCHANGE, "1310")))
        rt.clock.set("2026-10-21T00:35:00-04:00")
        self.settle("2026-10-21")
        # expiry Fri 2026-12-18 16:40 ET: SPX close 5,050 -> intrinsic 50.00 -> ASSIGNMENT in cash: Dr 2900 8,000 / Cr 2100 10,000 / Dr 4000 2,000
        rt.clock.set("2026-12-18T16:40:00-05:00")
        rt.marks.set("SPX", "5050.00", "close", quote_time=sc.ep("2026-12-18T16:00:00"))
        r = self.expire(iid)
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("ASSIGNMENT", True, "50", 1000000, -200000), (r.body["kind"], r.body["index_cash_settled"], r.body["intrinsic"], r.body["result"]["gross_c"], r.body["result"]["realised_c"]))
        self.assertEqual((0, 1000000, -200000, 0), (bal(rt, US_EXCHANGE, "2900", inst=iid), bal(rt, US_EXCHANGE, "2100"), bal(rt, US_EXCHANGE, "4000", inst=iid), bal(rt, US_EXCHANGE, "1310")))
        f = sc.one(rt, "SELECT * FROM fills WHERE idem_key=?", (f"expiry:{US_EXCHANGE}:{iid}",))
        self.assertEqual(("BUY", 2, "50", "ASSIGNMENT", "2026-12-21"), (f["side"], f["qty"], f["price"], f["origin"], f["settle_date"]))   # T+1 = Mon 12-21
        self.assertEqual("EXPIRED", rt.instruments.get(iid).status)
        assert_integrity(self, rt, "full")

    def test_margin_mark_job_covered_call_and_overdraft(self):
        rt = self.start()
        fund(rt, US_EXCHANGE, 100000000)
        now = rt.clock.epoch()
        buy(rt, US_EXCHANGE, "SPY", 100, "769.32")                                                # 100 shares: payable 76,932.00 + 1.00 fee (T+1)
        sc.ensure_option(rt, C760)
        sc.ensure_option(rt, P740)
        rt.marks.set(C760, "15.15")
        rt.marks.set(P740, "9.80")
        rt.marks.set_quote(sc.chain_rec(C760, "15.10", "15.20", 50, 50, now - 120, now))
        rt.marks.set_quote(sc.chain_rec(P740, "9.80", "9.90", 50, 50, now - 120, now))
        # STO 1 C760 against 100 long SPY: covered call -> requirement 0, the 100 shares reserved
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id=C760, side="STO", qty=1, order_type="MARKET")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((0, {"SPY": 100}), (r.body["fills"][0]["margin"]["required_c"], r.body["fills"][0]["margin"]["detail"]["shares_reserved"]))
        self.assertEqual({"SPY": 100}, options.reserved_shares(rt, US_EXCHANGE))
        self.assertEqual(100, options.reserved_shares(rt, US_EXCHANGE, "SPY"))
        with rt.db.reader() as conn:
            self.assertEqual([(US_EXCHANGE, "2026-10-20")], options.margin_mark_keys(conn, "2026-10-20"))
        r = execute(rt, "options.margin_mark", {"account_id": US_EXCHANGE, "date": "2026-10-20"}, "job:OPTION_MARGIN_MARK:1", actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("DONE", False, 0), (r.body["status"], r.body["posted"], r.body["margin"]["required_c"]))
        self.assertEqual("DONE", sc.one(rt, "SELECT status FROM jobs_done WHERE job='OPTION_MARGIN_MARK' AND key=?", (f"{US_EXCHANGE}:2026-10-20",))["status"])
        self.assertEqual("already done", execute(rt, "options.margin_mark", {"account_id": US_EXCHANGE, "date": "2026-10-20"}, actor="lifecycle").body["note"])
        with rt.db.reader() as conn:
            self.assertEqual([], options.margin_mark_keys(conn, "2026-10-20"))
        # STO 5 P740: + 67,172.00 reserved (the covered call stays 0; the sides add); nothing posted: 1310 0, 1010 still 1,000,000.00
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id=P740, side="STO", qty=5, order_type="MARKET")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((0, 100000000, 6717200), (bal(rt, US_EXCHANGE, "1310"), bal(rt, US_EXCHANGE, "1010"), r.body["fills"][0]["margin"]["required_c"]))
        # drain the cash: 850,000.00 back to Treasury - a transfer moves settled cash net of payables and reservations (MONEY-6):
        # min(1,000,000.00, 1,000,000.00 - payable 76,933.00 - reserved 67,172.00) = 855,895.00 >= 850,000.00 - then withdraw Treasury's
        # 10,000,000 - 1,000,000 + 850,000 = 9,850,000.00 entirely -> the account keeps 150,000.00 settled and Treasury has nothing
        self.assertTrue(execute(rt, "treasury.transfer", {"from_account": US_EXCHANGE, "to_account": TREASURY, "currency": "USD", "amount_c": 85000000}).ok)
        self.assertTrue(execute(rt, "treasury.withdraw", {"lifetime_id": T1, "amount_c": 985000000, "currency": "USD"}).ok)
        self.assertEqual((15000000, 0), (bal(rt, US_EXCHANGE, "1010"), bal(rt, TREASURY, "1010")))
        # the put's mark jumps to 180.00 at S 740: per share max(180 + 148 - 0, 180 + 74) = 328.00 x 100 x 5 = 164,000.00 -> +96,828.00 reserved.
        # Cover at the T+1 view (Wed): settled 150,000.00 + receivables 1,509.35 (STO C760 1 x 15.10 x 100 - 0.65) + 4,896.75 (STO P740) - payable
        # 76,933.00 - reserved 164,000.00 = -84,526.90; Treasury 0, no PB -> 84,526.90 uncovered: ONE ERROR FUNDING_SHORTFALL (no cash moves,
        # no forced liquidation); 1010 stays 150,000.00
        rt.marks.set("SPY", "740.00")
        rt.marks.set(P740, "180.00")
        r = execute(rt, "options.margin_mark", {"account_id": US_EXCHANGE, "date": "2026-10-21"}, actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        m = r.body["margin"]
        self.assertEqual((16400000, 9682800, 8452690, None), (m["required_c"], m["delta_c"], m["shortfall_c"], m["entry_id"]))
        self.assertEqual((0, 15000000), (bal(rt, US_EXCHANGE, "1310"), bal(rt, US_EXCHANGE, "1010")))
        att = sc.one(rt, "SELECT details_json FROM attention WHERE code='FUNDING_SHORTFALL' AND account_id=? AND resolved_ts IS NULL", (US_EXCHANGE,))
        self.assertIn('"kind":"option_margin"', att["details_json"].replace(" ", ""))
        assert_integrity(self, rt, "full")                                                         # L16 holds; L18 untouched (1010 never goes negative)
        # new orders in the account are refused while the shortfall is open (WP-02's validate)
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id=C760, side="BTO", qty=1, order_type="MARKET")
        self.assertEqual((409, "FUNDING_SHORTFALL"), (r.http, r.code))
        # a deposit + transfer of 84,526.90 covers the reservation exactly: the item resolves at the transfer
        self.assertTrue(execute(rt, "treasury.deposit", {"lifetime_id": T1, "amount_c": 8452690, "currency": "USD"}).ok)
        self.assertTrue(execute(rt, "treasury.transfer", {"from_account": TREASURY, "to_account": US_EXCHANGE, "currency": "USD", "amount_c": 8452690}).ok)
        self.assertEqual(0, self.count("SELECT COUNT(*) n FROM attention WHERE code='FUNDING_SHORTFALL' AND account_id=? AND resolved_ts IS NULL", (US_EXCHANGE,)))
        assert_integrity(self, rt, "full")

    def test_chain_outage_never_fills(self):
        rt = self.start()
        fund(rt, US_EXCHANGE, 100000000)
        now = rt.clock.epoch()
        sc.ensure_option(rt, C760)
        rt.marks.set(C760, "15.15")
        rt.marks.set_quote(sc.chain_rec(C760, "15.10", "15.20", 50, 50, now - 1500, now))          # stale: a LIMIT may rest
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id=C760, side="BTO", qty=5, order_type="LIMIT", limit_px="15.20")
        self.assertTrue(r.ok, r.body)
        oid = r.body["order"]["order_id"]
        cache = sc.FakeCache()
        m = sc.matcher_for(rt, cache)
        # a Cboe outage: the record is unavailable (no NBBO, an error) -> nothing fills, no model fill
        cache[C760] = QuoteRecord(instrument_id=C760, provider="cboe", source="Cboe (delayed)", method="chain", fetched_at=now, currency="USD",
                                  session_group="US_EQUITY_OPTIONS", error="HTTP 503", quality="unavailable")
        self.assertEqual(0, m.on_quotes([C760]))
        cache[C760] = sc.chain_rec(C760, "0", "15.20", 50, 50, now - 30, now)                      # bid 0: NO_NBBO
        self.assertEqual(0, m.on_quotes([C760]))
        cache[C760] = QuoteRecord(instrument_id=C760, provider="model", source="option model", method="model", last=D("15.15"), fetched_at=now, quote_time=now,
                                  delay_s=0, currency="USD", session_group="US_EQUITY_OPTIONS", quality="computed")
        self.assertEqual(0, m.on_quotes([C760]))                                                   # a computed mark never executes
        self.assertEqual(("WORKING", 0), (lambda o: (o["status"], o["filled_qty"]))(sc.order_row(rt, oid)))
        self.assertEqual(0, self.count("SELECT COUNT(*) n FROM fills"))
        # the chain view says fetching / refused / no chain rather than inventing rows
        rt.chains = ChainCache(clock=rt.clock)
        self.assertEqual("fetching", options.chain_view(rt, "SPY")["status"])
        rt.chains.fail("SPY", Exception("HTTP 403: refused the User-Agent"))
        with self.assertRaises(RejectError) as cm:
            options.chain_view(rt, "SPY")
        self.assertEqual(("CBOE_REFUSED_UA", 503), (cm.exception.code, cm.exception.http))
        rt.chains.fail("SPY", Exception("HTTP 404: not found"))
        with self.assertRaises(RejectError) as cm:
            options.chain_view(rt, "SPY")
        self.assertEqual(("NO_CHAIN", 409), (cm.exception.code, cm.exception.http))
        # the hedge panel reports the outage too
        self.assertEqual("unavailable", hedges.candidates(rt, US_EXCHANGE, rt.instruments.get("SPY"), "BUY", 1000)["status"])

    def test_chain_view_and_ensure_option(self):
        rt = self.start()
        now = rt.clock.epoch()
        under = ChainUnderlying("SPY", D("769.32"), D("769.30"), D("769.34"), 10, 10, now - 100, D("765.00"), 0.18)
        rows = [ChainRow("SPY261120C00760000", "2026-11-20", "C", D(760), D("15.10"), D("15.20"), 50, 50, D("15.15"), None, 0.19, 0.58, 0.01, -0.1, 0.9, 100, 10),
                ChainRow("SPY261120P00760000", "2026-11-20", "P", D(760), D("5.80"), D("5.90"), 20, 20, None, None, 0.20, -0.42, 0.01, -0.1, 0.9, 100, 10),
                ChainRow("SPY261120C00770000", "2026-11-20", "C", D(770), D("9.50"), D("9.60"), 30, 30, None, None, 0.18, 0.46, 0.01, -0.1, 0.9, 100, 10),
                ChainRow("SPY261120P00770000", "2026-11-20", "P", D(770), D(0), D(0), None, None, None, None, None, None, None, None, None, 0, 0),
                ChainRow("SPY270115C00760000", "2027-01-15", "C", D(760), D("25.00"), D("25.30"), 5, 5, None, None, 0.19, 0.6, 0.01, -0.1, 0.9, 1, 1)]
        snap = ChainSnapshot("SPY", "SPY", under, rows, now - 30, now - 100, 5)
        v = options.chain_view(rt, "SPY", chain=snap)
        self.assertEqual((["2026-11-20", "2027-01-15"], "2026-11-20", "delayed", now - 100, ["760", "770"]), (v["expiries"], v["expiry"], v["quality"], v["as_of"], [r["strike"] for r in v["rows"]]))
        c760 = v["rows"][0]["call"]
        self.assertEqual((C760, False, "delayed", "15.10", "15.20"), (c760["instrument_id"], c760["listed"], c760["quality"], c760["bid"], c760["ask"]))
        # the UI shape (s15 / components/chain.js): kind, the chain's observation time (now - 100 -> age 100 s), provenance; the underlying as a quote record
        self.assertEqual(("call", now - 100, 100, "chain", "Cboe delayed quotes", "15.15"), (c760["kind"], c760["quote_time"], c760["age_s"], c760["method"], c760["source"], c760["mid"]))
        self.assertEqual(("769.32", "delayed", "SPY", now - 100, "769.32"), (v["underlying"]["last"], v["underlying"]["quality"], v["underlying"]["symbol"], v["underlying"]["quote_time"],
                                                                             v["underlying"]["mid"]))   # mid = (769.30 + 769.34) / 2 = 769.32
        self.assertEqual("unavailable", v["rows"][1]["put"]["quality"])                              # bid 0 / ask 0: no NBBO
        self.assertEqual(1, len(options.chain_view(rt, "SPY", expiry="2027-01-15", chain=snap)["rows"]))
        with self.assertRaises(RejectError):
            options.chain_view(rt, "SPY", expiry="2027-03-19", chain=snap)
        # materialise from the chain row (first sight); an unlisted contract is refused, never invented
        inst = options.ensure_option(rt, "SPY261120C00770000", chain=snap)
        self.assertEqual(("OPTION", "C", D(770), "2026-11-20", "SPY", "AMERICAN", "PHYSICAL"), (inst.kind, inst.right, inst.strike, inst.expiry, inst.underlying_id, inst.style, inst.exercise))
        self.assertEqual(True, options.chain_view(rt, "SPY", chain=snap)["rows"][1]["call"]["listed"])
        with self.assertRaises(RejectError) as cm:
            options.ensure_option(rt, "SPY261120C00800000", chain=snap)
        self.assertEqual("NOT_LISTED", cm.exception.code)
        self.assertEqual("unavailable", options.chain_view(rt, "SPY")["status"])                   # no chain source bound: unavailable, not fetching


if __name__ == "__main__":
    unittest.main()
