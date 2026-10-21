"""WP-06 short-sale scenarios (TERMINAL_DESIGN.md §19.5, §19.24 D4 / D5; §13.1).  Every expected number is worked by
hand in a comment.  The SETTLE_DUE / ACCRUE_DAILY jobs belong to the lifecycle (WP-08): test-local helpers post the
same §5.4 settlement lines and run ``credit.accrue_borrow`` per due night so the ledger numbers can be checked here.

Scaffolding shared with test_terminal_lending lives here (imported as a module, so unittest does not load these cases
twice): a runtime with the orders + lending + repo handlers, a RateStore with SOFR, instrument / FINRA / CLOSE-mark
fixtures and journal helpers.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import date
from decimal import Decimal

from tests.terminal_helpers import TREASURY, US_EXCHANGE, assert_integrity, bal, execute, fund
from tests import test_terminal_orders as sc
from finsim.terminal import lending, repo
from finsim.terminal.ledger import Entry, cr, dr
from finsim.terminal.quotes.rates import RateStore

FRI_10 = "2026-10-16T10:00:00-04:00"     # Friday, NYSE open; §19.5 starts here


def _h_settle_due(ctx, payload):
    """What WP-08's SETTLE_DUE posts: receivable Dr 1010 / Cr 1200; payable Dr 2100 / Cr 1010 (idem settle:{settle_id})."""
    rows = ctx.conn.execute("SELECT * FROM settlements WHERE status='PENDING' AND due_date <= ? ORDER BY settle_id", (payload["as_of"],)).fetchall()
    out = []
    for s in rows:
        amt, aid, ccy = int(s["amount_c"]), s["account_id"], s["currency"]
        if amt > 0:
            lines = [dr(aid, "1010", ccy, amt), cr(aid, s["gl"], ccy, amt)]
        else:
            lines = [dr(aid, s["gl"], ccy, -amt), cr(aid, "1010", ccy, -amt)]
        eid = ctx.post(Entry("SETTLE", f"settlement {s['settle_id']} of fill {s['fill_id']}", lines, f"settle:{s['settle_id']}", ref_type="settlement", ref_id=s["settle_id"]))
        ctx.conn.execute("UPDATE settlements SET status='SETTLED', settled_ts=?, entry_id=? WHERE settle_id=?", (ctx.ts, eid, s["settle_id"]))
        out.append(s["settle_id"])
    return {"settled": out}


def runtime(tmp, at=FRI_10, sofr=("2026-10-15", "3.87")):
    """Orders (WP-02) + lending + repo handlers on the test runtime; ``rt.rates`` holds SOFR 3.87 observed 2026-10-15."""
    rt = sc.runtime(tmp, at=at)
    lending.register(rt.bus)
    lending.bind(rt)
    repo.register(rt.bus)
    rt.rates = RateStore()
    if sofr is not None:
        rt.rates.put("SOFR", [(date.fromisoformat(sofr[0]), Decimal(sofr[1]))], persist=False)
    rt.bus.register("test.settle_due", _h_settle_due)
    return rt


def add_equity(rt, iid, adv, kind="EQUITY", **spec):
    """Insert a US-listed equity-like row with ``spec.adv`` (None = no ADV in the master) and refresh the master."""
    with rt.db.writer() as tx:
        s = {"adv": adv}
        s.update(spec)
        inserted = rt.instruments._insert(tx.conn, tx.ts, instrument_id=iid, kind=kind, symbol=iid, name=f"{iid} Test Co", currency="USD", session_group="US_EQUITY",
                                          settle_days=1, account_kinds=["US_EXCHANGE", "PB"], yahoo_symbol=iid, cboe_symbol=iid, spec=s, support="FULL")
    if not inserted:
        raise AssertionError(f"{iid} is already in the seeded master (a real ticker): pick a test-only id")
    return rt.instruments.refresh(iid)


def finra(rt, symbol, rows):
    """``rows = [(day, short_volume, total_volume)]`` into ``finra_short`` (what WP-01's refresher writes)."""
    with rt.db.writer() as tx:
        for d, sv, tv in rows:
            tx.conn.execute("INSERT OR REPLACE INTO finra_short(symbol, day, short_volume, total_volume) VALUES (?,?,?,?)", (symbol, d, int(sv), int(tv)))


def close_mark(rt, iid, d, price):
    """A recorded CLOSE mark (what MARKS_RECORD writes) for the accruals and collateral marks."""
    with rt.db.writer() as tx:
        tx.conn.execute("INSERT OR REPLACE INTO marks(instrument_id, mark_date, kind, price, currency, quality, source, method, quote_time, fetched_at, note)"
                        " VALUES (?,?,'CLOSE',?,'USD','close','test fixture','direct',?,?,NULL)", (iid, d, str(price), tx.ts, tx.ts))


def quote(rt, iid, last, volume=100000):
    """An executable Yahoo-style print 2 minutes old with a completed 1-minute bar of ``volume`` (10 % cap), plus the mark."""
    now = rt.clock.epoch()
    rt.marks.set_quote(sc.rec(iid, last, now - 120, now, bar=(now - 180, volume)))
    rt.marks.set(iid, last)


def accrue_due(rt, through):
    """What ACCRUE_DAILY does for borrows: one ``credit.accrue_borrow`` per (borrow, night) due up to ``through``."""
    with rt.db.reader() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM borrows ORDER BY borrow_id").fetchall()]
        plan = [(b["borrow_id"], d) for b in rows for d in lending.nights_due(conn, b, date.fromisoformat(through))]
    out = []
    for bid, d in plan:
        r = execute(rt, "credit.accrue_borrow", {"borrow_id": bid, "night": d}, request_id=f"job:accrue:{bid}:{d}", actor="lifecycle")
        if not r.ok:
            raise AssertionError(f"accrual {bid} {d} failed: {r.body}")
        out.append((bid, d, r.body))
    return out


def lines_of(rt, idem):
    rows = sc.q(rt, "SELECT l.gl, l.amount_c, l.instrument_id FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id WHERE e.idem_key=? ORDER BY l.line_no", (idem,))
    return [(r["gl"], r["amount_c"], r["instrument_id"]) for r in rows]


def n_entries(rt):
    return sc.q(rt, "SELECT COUNT(*) AS n FROM journal_entries")[0]["n"]


def borrow_row(rt, bid):
    return sc.one(rt, "SELECT * FROM borrows WHERE borrow_id=?", (bid,))


def setup_abc(rt, fund_c=20000000):
    """§19.5 setup: A settled 200,000.00; ABC last 50.00 (bar volume 100,000 -> cap 10,000); FINRA ratio 0.30 on the
    last session (Thu 10-15), ADV 5,000,000 -> tier GC 0.30 %; SOFR 3.87 (obs 10-15) -> rebate 3.37 %."""
    if fund_c:
        fund(rt, US_EXCHANGE, fund_c)
    add_equity(rt, "ABC", 5000000)
    finra(rt, "ABC", [("2026-10-13", 20000, 100000), ("2026-10-14", 25000, 100000), ("2026-10-15", 30000, 100000)])
    quote(rt, "ABC", "50.00", 100000)


def short_abc(tc, rt):
    """SELL_SHORT 1,000 ABC MARKET at Fri 10:00: borrow first (collateral 51,000.00), then the fill at 50.00."""
    r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="ABC", side="SELL_SHORT", qty=1000, order_type="MARKET")
    tc.assertTrue(r.ok, r.body)
    tc.assertEqual("FILLED", r.body["order"]["status"])
    tc.assertEqual("B-000001", r.body["borrow"]["borrow_id"])
    return r


class ShortSaleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-shorts-")
        self.rt = None

    def tearDown(self):
        if self.rt is not None:
            self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start(self, at=FRI_10):
        self.rt = runtime(self.tmp, at=at)
        return self.rt

    def settle(self, as_of):
        r = execute(self.rt, "test.settle_due", {"as_of": as_of}, actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        return r.body["settled"]

    # ---- §19.5 end to end ----------------------------------------------------------------------------------------------
    def test_19_5_short_borrow_accruals_mark_cover(self):
        rt = self.start()
        setup_abc(rt)
        # --- Fri 10-16 10:00: SELL_SHORT 1,000 ABC MARKET.  Locate: GC (ratio 0.30 < 0.60, ADV 5M >= 1M), fee 0.30 %,
        # rebate 3.87 - 0.50 = 3.37 %; collateral to_c(1.02 x 1,000 x 50.00) = 51,000.00
        r = short_abc(self, rt)
        pb = r.body["preview"]["borrow"]
        self.assertEqual((True, 1000, 1000, "GC", "0.003", "0.0337", 5100000), (pb["needed"], pb["qty"], pb["qty_available"], pb["tier"], pb["fee_rate"], pb["rebate_rate"], pb["collateral_c"]))
        self.assertEqual([], r.body["warnings"])
        self.assertEqual([], r.body["sweeps"])                                                  # settled 200,000 covers the collateral: no sweep
        # borrow first: Dr 1400 ABC 51,000.00 / Cr 1010 51,000.00
        self.assertEqual([("1400", 5100000, "ABC"), ("1010", -5100000, None)], lines_of(rt, "collat:B-000001:open"))
        # the fill at 50.00: gross 50,000.00; fee 1,000 x 0.005 = 5.00; Dr 1200 49,995.00 / Dr 5000 5.00 / Cr 2600 ABC 50,000.00
        fill = r.body["fills"][0]
        self.assertEqual(("F-000001", 5000000, 500, "SELL", "2026-10-16", "2026-10-19"), (fill["fill_id"], fill["gross_c"], fill["fee_c"], fill["effect"], fill["trade_date"], fill["settle_date"]))
        self.assertEqual([("1200", 4999500, None), ("5000", 500, "ABC"), ("2600", -5000000, "ABC")], lines_of(rt, "fill:F-000001"))
        lot = sc.one(rt, "SELECT * FROM lots WHERE account_id=? AND instrument_id='ABC'", (US_EXCHANGE,))
        self.assertEqual(("SHORT", 1000, 1000, "50", 5000000), (lot["side"], lot["qty_open"], lot["qty_remaining"], lot["unit_cost"], lot["cost_c"]))
        st = sc.one(rt, "SELECT * FROM settlements WHERE fill_id='F-000001'")
        self.assertEqual(("2026-10-19", 4999500, "1200", "PENDING"), (st["due_date"], st["amount_c"], st["gl"], st["status"]))
        b = borrow_row(rt, "B-000001")
        self.assertEqual((1000, 0, "0.003", "0.0337", "GC", 5100000, "OPEN", "O-000001", None, "2026-10-16"),
                         (b["qty"], b["qty_returned"], b["fee_rate"], b["rebate_rate"], b["tier"], b["collateral_c"], b["status"], b["order_id"], b["reason"], b["opened_date"]))
        # --- reads Fri: settled 149,000.00; receivable 49,995.00; held at lender 51,000.00; available (settle Mon) 198,995.00;
        # position -1,000; MV -50,000.00; unrealised 0; NAV = 149,000 + 49,995 + 51,000 - 50,000 = 199,995.00 = 200,000 - 5
        rt.clock.set("2026-10-16T10:01:00-04:00")
        cash = rt.accounting.cash(US_EXCHANGE)["USD"]
        self.assertEqual((14900000, 4999500, 5100000, 0), (cash.settled_c, cash.receivable_c, cash.held_at_lender_c, cash.reserved_c))
        self.assertEqual(19899500, rt.accounting.available(US_EXCHANGE, "USD", date(2026, 10, 19)))
        pos = rt.accounting.position(US_EXCHANGE, "ABC")
        self.assertEqual((-1000, -5000000, 0), (pos.qty, pos.mv_c, pos.unrealised_c))
        self.assertEqual((["B-000001"], 1000, 5100000, "GC"), (pos.borrow["borrow_ids"], pos.borrow["qty"], pos.borrow["collateral_c"], pos.borrow["tier"]))
        self.assertEqual(19999500, rt.accounting.nav(US_EXCHANGE).nav_c)
        assert_integrity(self, rt, "full")                                                      # L12 (1,000 >= 1,000) and L13 hold
        # --- Mon 10-19 00:35: settlement Dr 1010 / Cr 1200 49,995.00 -> settled 198,995.00
        rt.clock.set("2026-10-19T00:35:00-04:00")
        self.assertEqual(["S-000001"], self.settle("2026-10-19"))
        self.assertEqual((19899500, 0), (bal(rt, US_EXCHANGE, "1010"), bal(rt, US_EXCHANGE, "1200")))
        # accruals for the nights of Fri 16, Sat 17, Sun 18 (mark = Friday's CLOSE 50.00 for all three):
        # fee base 1,000 x 50.00 = 50,000.00 at 0.30 % ACT/360 cumulative: n=1 to_c(0.416667) = 0.42 -> 0.42; n=2 to_c(0.833333) = 0.83 -> 0.41;
        # n=3 to_c(1.25) = 1.25 -> 0.42.  rebate base 51,000.00 at 3.37 %: to_c(4.774167) = 4.77 -> 4.77; to_c(9.548333) = 9.55 -> 4.78;
        # to_c(14.3225) = 14.32 -> 4.77
        close_mark(rt, "ABC", "2026-10-16", "50.00")
        n0 = n_entries(rt)
        done = accrue_due(rt, "2026-10-18")
        self.assertEqual([("B-000001", "2026-10-16"), ("B-000001", "2026-10-17"), ("B-000001", "2026-10-18")], [(b, d) for b, d, _ in done])
        self.assertEqual([(42, 477), (41, 478), (42, 477)], [(body["fee_c"], body["rebate_c"]) for _, _, body in done])
        self.assertEqual([("5300", 42, "ABC"), ("2310", -42, "ABC")], lines_of(rt, "accr:bfee:B-000001:2026-10-16"))
        self.assertEqual([("5300", 41, "ABC"), ("2310", -41, "ABC")], lines_of(rt, "accr:bfee:B-000001:2026-10-17"))
        self.assertEqual([("5300", 42, "ABC"), ("2310", -42, "ABC")], lines_of(rt, "accr:bfee:B-000001:2026-10-18"))
        self.assertEqual([("1410", 477, "ABC"), ("4350", -477, "ABC")], lines_of(rt, "accr:rebate:B-000001:2026-10-16"))
        self.assertEqual([("1410", 478, "ABC"), ("4350", -478, "ABC")], lines_of(rt, "accr:rebate:B-000001:2026-10-17"))
        self.assertEqual([("1410", 477, "ABC"), ("4350", -477, "ABC")], lines_of(rt, "accr:rebate:B-000001:2026-10-18"))
        self.assertEqual(n0 + 6, n_entries(rt))
        # 2310 = 5300 = 1.25; 1410 = 4350 = 14.32
        self.assertEqual((125, 125, 1432, 1432), (bal(rt, US_EXCHANGE, "2310", "USD", "ABC"), bal(rt, US_EXCHANGE, "5300", "USD", "ABC"),
                                                  bal(rt, US_EXCHANGE, "1410", "USD", "ABC"), bal(rt, US_EXCHANGE, "4350", "USD", "ABC")))
        st_fee = sc.one(rt, "SELECT * FROM accrual_state WHERE kind='bfee' AND ref_id='B-000001'")
        self.assertEqual(("2026-10-16", 5000000, "0.003", 360, "2026-10-18", 125), (st_fee["segment_start"], st_fee["base_c"], st_fee["rate"], st_fee["day_basis"], st_fee["accrued_through"], st_fee["cum_c"]))
        st_reb = sc.one(rt, "SELECT * FROM accrual_state WHERE kind='rebate' AND ref_id='B-000001'")
        self.assertEqual(("2026-10-16", 5100000, "0.0337", "2026-10-18", 1432), (st_reb["segment_start"], st_reb["base_c"], st_reb["rate"], st_reb["accrued_through"], st_reb["cum_c"]))
        # a second tick posts nothing; a replay of a night's request id returns the stored result without a new entry
        self.assertEqual([], accrue_due(rt, "2026-10-18"))
        rep = execute(rt, "credit.accrue_borrow", {"borrow_id": "B-000001", "night": "2026-10-18"}, request_id="job:accrue:B-000001:2026-10-18", actor="lifecycle")
        self.assertTrue(rep.ok and rep.replayed, rep.body)
        self.assertEqual(n0 + 6, n_entries(rt))
        assert_integrity(self, rt, "full")
        # --- Mon 17:10 COLLATERAL_MARK with CLOSE 52.00: required to_c(1.02 x 1,000 x 52) = 53,040.00; delta +2,040.00 ->
        # Dr 1400 / Cr 1010 2,040.00 (settled 196,955.00); borrows.collateral_c 53,040.00; no sweep (settled covers it)
        rt.clock.set("2026-10-19T17:10:00-04:00")
        r = execute(rt, "credit.collateral_mark", {"borrow_id": "B-000001", "mark_date": "2026-10-19", "mark": "52.00", "mark_note": "CLOSE 2026-10-19 (test)"},
                    request_id="job:cmark:B-000001:2026-10-19", actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((204000, 5304000, "52"), (r.body["delta_c"], r.body["required_c"], r.body["mark"]))
        self.assertEqual("own", r.body["funding"]["source"])
        self.assertEqual([("1400", 204000, "ABC"), ("1010", -204000, None)], lines_of(rt, "cmark:B-000001:2026-10-19"))
        self.assertEqual((19695500, 5304000), (bal(rt, US_EXCHANGE, "1010"), bal(rt, US_EXCHANGE, "1400", "USD", "ABC")))
        b = borrow_row(rt, "B-000001")
        self.assertEqual((5304000, "2026-10-19"), (b["collateral_c"], b["last_mark_date"]))
        self.assertEqual(0, len(sc.q(rt, "SELECT 1 FROM transfers WHERE kind='SWEEP_IN'")))
        # a repeat of the same key is a no-op (already marked for the date)
        r2 = execute(rt, "credit.collateral_mark", {"borrow_id": "B-000001", "mark_date": "2026-10-19", "mark": "52.00"}, actor="lifecycle")
        self.assertTrue(r2.ok and r2.body.get("skipped"), r2.body)
        # --- position at 52.00: MV -52,000.00; unrealised 50,000 - 52,000 = -2,000.00
        # NAV Mon close = 196,955.00 + 53,040.00 + 14.32 - 52,000.00 - 1.25 = 198,008.07 = 200,000 - 5 - 2,000 + 14.32 - 1.25
        rt.marks.set("ABC", "52.00")
        pos = rt.accounting.position(US_EXCHANGE, "ABC")
        self.assertEqual((-5200000, -200000), (pos.mv_c, pos.unrealised_c))
        self.assertEqual(19800807, rt.accounting.nav(US_EXCHANGE).nav_c)
        # P&L ITD (funding 200,000 is a flow) = -1,991.93 = income 14.32 - financing 1.25 - fees 5.00 + unrealised -2,000.00; residual 0
        p = rt.accounting.pnl(US_EXCHANGE, "ITD")
        self.assertEqual((-199193, 0), (p.pnl_c, p.residual_c))
        self.assertEqual((1432, 125, 500, -200000, 0), (p.components["income_c"], p.components["financing_c"], p.components["fees_c"], p.components["unrealised_c"], p.components["realised_c"]))
        assert_integrity(self, rt, "full")
        # --- Mon night accruals (posted Tue 00:35; new segments because both bases changed): fee to_c(52,000 x 0.003 / 360) =
        # to_c(0.433333) = 0.43; rebate to_c(53,040 x 0.0337 / 360) = to_c(4.965133) = 4.97.  2310 = 1.68; 1410 = 19.29
        rt.clock.set("2026-10-20T00:35:00-04:00")
        close_mark(rt, "ABC", "2026-10-19", "52.00")
        done = accrue_due(rt, "2026-10-19")
        self.assertEqual([("B-000001", "2026-10-19", 43, 497)], [(b, d, body["fee_c"], body["rebate_c"]) for b, d, body in done])
        self.assertEqual((168, 1929), (bal(rt, US_EXCHANGE, "2310", "USD", "ABC"), bal(rt, US_EXCHANGE, "1410", "USD", "ABC")))
        st_fee = sc.one(rt, "SELECT * FROM accrual_state WHERE kind='bfee' AND ref_id='B-000001'")
        self.assertEqual(("2026-10-19", 5200000, "2026-10-19", 43), (st_fee["segment_start"], st_fee["base_c"], st_fee["accrued_through"], st_fee["cum_c"]))
        # --- Tue 10:00 BUY_TO_COVER 1,000 @ last 51.00: gross 51,000.00; fee 5.00; relief of the SHORT lot: proceeds 50,000 - cost 51,000 ->
        # realised -1,000.00; entry Dr 2600 ABC 50,000.00 / Dr 5000 5.00 / Cr 2100 51,005.00 / Dr 4000 ABC 1,000.00
        rt.clock.set("2026-10-20T10:00:00-04:00")
        quote(rt, "ABC", "51.00", 100000)
        n0 = n_entries(rt)
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="ABC", side="BUY_TO_COVER", qty=1000, order_type="MARKET")
        self.assertTrue(r.ok, r.body)
        self.assertEqual("FILLED", r.body["order"]["status"])
        fill = r.body["fills"][0]
        self.assertEqual(("F-000002", 5100000, 500, -100000, 5000000), (fill["fill_id"], fill["gross_c"], fill["fee_c"], fill["realised_c"], fill["cost_relieved_c"]))
        self.assertEqual([("2600", 5000000, "ABC"), ("5000", 500, "ABC"), ("2100", -5100500, None), ("4000", 100000, "ABC")], lines_of(rt, "fill:F-000002"))
        self.assertEqual([], r.body["sweeps"])
        # borrow return in the same command: Dr 1010 53,040.00 / Cr 1400 53,040.00; fees Dr 2310 1.68 / Cr 1010 1.68; rebate Dr 1010 19.29 / Cr 1410 19.29
        self.assertEqual([("1010", 5304000, None), ("1400", -5304000, "ABC")], lines_of(rt, "collat:B-000001:return:F-000002"))
        self.assertEqual([("2310", 168, "ABC"), ("1010", -168, None), ("1010", 1929, None), ("1410", -1929, "ABC")], lines_of(rt, "pay:bfee:B-000001:return:F-000002"))
        self.assertEqual(n0 + 3, n_entries(rt))                                                 # fill + collateral return + settlement of accruals
        b = borrow_row(rt, "B-000001")
        self.assertEqual(("CLOSED", 1000, 0, None), (b["status"], b["qty_returned"], b["collateral_c"], b["reason"]))
        self.assertIsNotNone(b["closed_ts"])
        # settled = 196,955 + 53,040 - 1.68 + 19.29 = 250,012.61; payable 51,005.00 (Wed); NAV flat = 250,012.61 - 51,005.00 = 199,007.61
        # = 200,000 - 5 - 5 - 1,000 + 19.29 - 1.68
        self.assertEqual((25001261, 5100500, 0, 0, 0), (bal(rt, US_EXCHANGE, "1010"), bal(rt, US_EXCHANGE, "2100"), bal(rt, US_EXCHANGE, "1400", "USD", "ABC"),
                                                        bal(rt, US_EXCHANGE, "2310", "USD", "ABC"), bal(rt, US_EXCHANGE, "1410", "USD", "ABC")))
        self.assertEqual(19900761, rt.accounting.nav(US_EXCHANGE).nav_c)
        self.assertIsNone(rt.accounting.position(US_EXCHANGE, "ABC"))
        self.assertEqual(0, len(sc.q(rt, "SELECT 1 FROM borrows WHERE status='OPEN'")))
        self.assertEqual(0, len(sc.q(rt, "SELECT 1 FROM journal_lines WHERE gl='5700'")))        # no buy-in, ever
        self.assertEqual(0, len(sc.q(rt, "SELECT 1 FROM attention WHERE resolved_ts IS NULL AND severity IN ('WARN','ERROR')")))
        assert_integrity(self, rt, "full")                                                      # L12 holds with no short and no borrow

    # ---- D4: collateral marks both ways, the call paid by a Treasury sweep ---------------------------------------------------------
    def test_d4_collateral_marks_both_ways(self):
        rt = self.start()
        setup_abc(rt)
        short_abc(self, rt)
        # the account's cash is swept out first (what SWEEP_OUT would do): Fri settled 149,000.00 -> Treasury; available at the Fri
        # accounting day = settled 149,000 (the receivable is due Mon) so the transfer is allowed
        r = execute(rt, "treasury.transfer", {"from_account": US_EXCHANGE, "to_account": TREASURY, "currency": "USD", "amount_c": 14900000})
        self.assertTrue(r.ok, r.body)
        self.assertEqual(0, bal(rt, US_EXCHANGE, "1010"))
        # Mon 00:35: settlement +49,995.00; the three weekend nights accrue per §19.5 (0.42 / 0.41 / 0.42 and 4.77 / 4.78 / 4.77)
        rt.clock.set("2026-10-19T00:35:00-04:00")
        self.settle("2026-10-19")
        close_mark(rt, "ABC", "2026-10-16", "50.00")
        done = accrue_due(rt, "2026-10-18")
        self.assertEqual([(42, 477), (41, 478), (42, 477)], [(body["fee_c"], body["rebate_c"]) for _, _, body in done])
        self.assertEqual((125, 1432), (bal(rt, US_EXCHANGE, "2310", "USD", "ABC"), bal(rt, US_EXCHANGE, "1410", "USD", "ABC")))
        # sweep the settled proceeds out as well -> the account holds no cash before the call
        r = execute(rt, "treasury.transfer", {"from_account": US_EXCHANGE, "to_account": TREASURY, "currency": "USD", "amount_c": 4999500})
        self.assertTrue(r.ok, r.body)
        self.assertEqual(0, bal(rt, US_EXCHANGE, "1010"))
        # +10 %: 50.00 -> 55.00: required to_c(1.02 x 1,000 x 55) = 56,100.00; call +5,100.00 = 1.02 x 1,000 x 5.00, paid by ONE sweep of
        # exactly 5,100.00 linked to the borrow; settled stays 0; 1400 = collateral_c = 56,100.00
        rt.clock.set("2026-10-19T17:10:00-04:00")
        r = execute(rt, "credit.collateral_mark", {"borrow_id": "B-000001", "mark_date": "2026-10-19", "mark": "55.00"}, request_id="job:cmark:B-000001:2026-10-19", actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((510000, 5610000, "sweep", 510000), (r.body["delta_c"], r.body["required_c"], r.body["funding"]["source"], r.body["funding"]["sweep_c"]))
        self.assertEqual([("1400", 510000, "ABC"), ("1010", -510000, None)], lines_of(rt, "cmark:B-000001:2026-10-19"))
        sweeps = sc.q(rt, "SELECT kind, from_account, to_account, amount_c, linked_type, linked_id, shortfall_c FROM transfers WHERE kind='SWEEP_IN'")
        self.assertEqual([("SWEEP_IN", TREASURY, US_EXCHANGE, 510000, "borrow", "B-000001", 510000)],
                         [(s["kind"], s["from_account"], s["to_account"], s["amount_c"], s["linked_type"], s["linked_id"], s["shortfall_c"]) for s in sweeps])
        self.assertEqual((0, 5610000, 5610000), (bal(rt, US_EXCHANGE, "1010"), bal(rt, US_EXCHANGE, "1400", "USD", "ABC"), borrow_row(rt, "B-000001")["collateral_c"]))
        self.assertEqual(0, len(sc.q(rt, "SELECT 1 FROM attention WHERE code='FUNDING_SHORTFALL' AND resolved_ts IS NULL")))
        assert_integrity(self, rt, "full")                                                      # L13 and L19 (sweep linked to the borrow)
        # -5 %: 55.00 -> 52.25: required to_c(1.02 x 1,000 x 52.25) = 53,295.00; delta -2,805.00 returns Dr 1010 / Cr 1400
        rt.clock.set("2026-10-20T17:10:00-04:00")
        r = execute(rt, "credit.collateral_mark", {"borrow_id": "B-000001", "mark_date": "2026-10-20", "mark": "52.25"}, request_id="job:cmark:B-000001:2026-10-20", actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((-280500, 5329500, None), (r.body["delta_c"], r.body["required_c"], r.body["funding"]))
        self.assertEqual([("1010", 280500, None), ("1400", -280500, "ABC")], lines_of(rt, "cmark:B-000001:2026-10-20"))
        self.assertEqual((280500, 5329500, 5329500), (bal(rt, US_EXCHANGE, "1010"), bal(rt, US_EXCHANGE, "1400", "USD", "ABC"), borrow_row(rt, "B-000001")["collateral_c"]))
        self.assertEqual(1, len(sc.q(rt, "SELECT 1 FROM transfers WHERE kind='SWEEP_IN'")))      # the return needs no sweep
        # a mark for an earlier date than the last one is refused; the recorded CLOSE is used when no mark is given
        r = execute(rt, "credit.collateral_mark", {"borrow_id": "B-000001", "mark_date": "2026-10-19", "mark": "50.00"}, actor="lifecycle")
        self.assertEqual((False, 400, "MARK_ORDER"), (r.ok, r.http, r.code))
        r = execute(rt, "credit.collateral_mark", {"borrow_id": "B-000001", "mark_date": "2026-10-21"}, actor="lifecycle")
        self.assertEqual((False, 400, "MARK_UNAVAILABLE"), (r.ok, r.http, r.code))              # no CLOSE recorded for 10-21: never defaulted
        close_mark(rt, "ABC", "2026-10-21", "52.25")
        r = execute(rt, "credit.collateral_mark", {"borrow_id": "B-000001", "mark_date": "2026-10-21"}, actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((0, None), (r.body["delta_c"], r.body["entry_id"]))                      # unchanged close: no movement, no entry
        self.assertEqual("2026-10-21", borrow_row(rt, "B-000001")["last_mark_date"])
        assert_integrity(self, rt, "full")

    # ---- D5: no borrow -> rejected; partial availability -> scaled; BACKS_SHORT ------------------------------------------------------
    def test_d5_no_borrow_and_partial(self):
        rt = self.start()
        setup_abc(rt)
        # a name with no ADV in the master -> tier NONE -> 409 NO_BORROW, nothing posted, no order row, no borrow row
        add_equity(rt, "NOB", None)
        quote(rt, "NOB", "20.00")
        n0 = n_entries(rt)
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="NOB", side="SELL_SHORT", qty=100, order_type="MARKET")
        self.assertEqual((False, 409, "NO_BORROW"), (r.ok, r.http, r.code))
        self.assertIn("no average daily volume", r.message)
        self.assertEqual(n0, n_entries(rt))
        self.assertEqual((0, 0), (len(sc.q(rt, "SELECT 1 FROM orders")), len(sc.q(rt, "SELECT 1 FROM borrows"))))
        # not in the master at all -> the locate itself says unavailable
        loc = lending.locate(rt, US_EXCHANGE, None, 100)
        self.assertEqual((False, 0, "NONE"), (loc.available, loc.qty_available, loc.tier))
        self.assertIn("not in the master", loc.basis)
        # partial: tier T3 (no FINRA rows for 3 sessions) with ADV 8,000 -> qty_available = int(0.10 x 8,000) = 800 -> the order is scaled to 800
        add_equity(rt, "T3X", 8000)
        quote(rt, "T3X", "10.00")
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="T3X", side="SELL_SHORT", qty=1000, order_type="MARKET")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((800, 800, "FILLED"), (r.body["order"]["qty"], r.body["order"]["filled_qty"], r.body["order"]["status"]))
        self.assertTrue(any("scaled down to 800" in w for w in r.body["warnings"]), r.body["warnings"])
        pb = r.body["preview"]["borrow"]
        self.assertEqual((1000, 800, "T3", "0.25", "0"), (pb["qty"], pb["qty_available"], pb["tier"], pb["fee_rate"], pb["rebate_rate"]))
        # collateral to_c(1.02 x 800 x 10.00) = 8,160.00; fill 800 @ 10.00: gross 8,000.00, fee max(800 x 0.005 = 4.00, 1.00) = 4.00
        self.assertEqual([("1400", 816000, "T3X"), ("1010", -816000, None)], lines_of(rt, "collat:B-000001:open"))
        self.assertEqual((800000, 400), (r.body["fills"][0]["gross_c"], r.body["fills"][0]["fee_c"]))
        b = borrow_row(rt, "B-000001")
        self.assertEqual((800, "T3", "0.25", "0", 816000, None), (b["qty"], b["tier"], b["fee_rate"], b["rebate_rate"], b["collateral_c"], b["reason"]))
        assert_integrity(self, rt, "full")
        # a manual return while the short is open -> 409 BACKS_SHORT, nothing posted, the borrow stays
        n0 = n_entries(rt)
        r = execute(rt, "credit.return_stock", {"borrow_id": "B-000001"})
        self.assertEqual((False, 409, "BACKS_SHORT"), (r.ok, r.http, r.code))
        self.assertEqual(n0, n_entries(rt))
        self.assertEqual(("OPEN", 0), (borrow_row(rt, "B-000001")["status"], borrow_row(rt, "B-000001")["qty_returned"]))
        # T3 accrues the fee at 25 % and no rebate: night of Fri 16 on 800 x 10.00 = 8,000.00: to_c(8,000 x 0.25 / 360) = to_c(5.555556) = 5.56
        rt.clock.set("2026-10-17T00:35:00-04:00")
        close_mark(rt, "T3X", "2026-10-16", "10.00")
        done = accrue_due(rt, "2026-10-16")
        self.assertEqual([("B-000001", "2026-10-16", 556, 0)], [(b, d, body["fee_c"], body["rebate_c"]) for b, d, body in done])
        self.assertEqual([], lines_of(rt, "accr:rebate:B-000001:2026-10-16"))
        assert_integrity(self, rt, "full")


if __name__ == "__main__":
    unittest.main()
