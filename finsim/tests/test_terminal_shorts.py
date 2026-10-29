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
    """SELL_SHORT 1,000 ABC MARKET at Fri 10:00: the borrow is located and recorded first (no collateral yet), then the fill at
    50.00 posts the collateral 51,000.00 (SHORT_COLLATERAL: 49,995.00 of the sale's own proceeds + 1,005.00 cash)."""
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
        self.assertEqual([], r.body["sweeps"])                                                  # settled 200,000 covers the 1,005.00 cash part: no sweep
        # the borrow is located and recorded at submit with NO collateral (SHORT_COLLATERAL: the collateral goes with the fill)
        self.assertEqual([], lines_of(rt, "collat:B-000001:open"))
        # the fill at 50.00: gross 50,000.00; fee 1,000 x 0.005 = 5.00; Dr 1200 49,995.00 / Dr 5000 5.00 / Cr 2600 ABC 50,000.00
        fill = r.body["fills"][0]
        self.assertEqual(("F-000001", 5000000, 500, "SELL", "2026-10-16", "2026-10-19"), (fill["fill_id"], fill["gross_c"], fill["fee_c"], fill["effect"], fill["trade_date"], fill["settle_date"]))
        self.assertEqual([("1200", 4999500, None), ("5000", 500, "ABC"), ("2600", -5000000, "ABC")], lines_of(rt, "fill:F-000001"))
        # ... and its collateral in the same command: to_c(1.02 x 1,000 x 50.00) = 51,000.00 to the lender, funded by the sale's own proceeds
        # 49,995.00 (Cr 1200: the receivable goes to the lender) and 51,000 - 49,995 = 1,005.00 cash = 2 % excess 1,000.00 + fee 5.00
        self.assertEqual([("1400", 5100000, "ABC"), ("1200", -4999500, None), ("1010", -100500, None)], lines_of(rt, "collat:B-000001:fill:F-000001"))
        lot = sc.one(rt, "SELECT * FROM lots WHERE account_id=? AND instrument_id='ABC'", (US_EXCHANGE,))
        self.assertEqual(("SHORT", 1000, 1000, "50", 5000000), (lot["side"], lot["qty_open"], lot["qty_remaining"], lot["unit_cost"], lot["cost_c"]))
        # the settlement row carries what the proceeds leave (49,995.00 - 49,995.00 = 0): SETTLED at once by the collateral entry, so T+1
        # never moves the proceeds a second time
        st = sc.one(rt, "SELECT * FROM settlements WHERE fill_id='F-000001'")
        coll_entry = sc.one(rt, "SELECT entry_id FROM journal_entries WHERE idem_key='collat:B-000001:fill:F-000001'")["entry_id"]
        self.assertEqual(("2026-10-19", 0, "1200", "SETTLED", coll_entry), (st["due_date"], st["amount_c"], st["gl"], st["status"], st["entry_id"]))
        b = borrow_row(rt, "B-000001")
        self.assertEqual((1000, 0, "0.003", "0.0337", "GC", 5100000, "OPEN", "O-000001", None, "2026-10-16"),
                         (b["qty"], b["qty_returned"], b["fee_rate"], b["rebate_rate"], b["tier"], b["collateral_c"], b["status"], b["order_id"], b["reason"], b["opened_date"]))
        # --- reads Fri: settled 200,000 - 1,005 = 198,995.00; receivable 0 (the proceeds went to the lender); held at lender 51,000.00;
        # available (settle Mon) 198,995.00; position -1,000; MV -50,000.00; unrealised 0; NAV = 198,995 + 0 + 51,000 - 50,000 = 199,995.00 = 200,000 - 5
        rt.clock.set("2026-10-16T10:01:00-04:00")
        cash = rt.accounting.cash(US_EXCHANGE)["USD"]
        self.assertEqual((19899500, 0, 5100000, 0), (cash.settled_c, cash.receivable_c, cash.held_at_lender_c, cash.reserved_c))
        self.assertEqual(19899500, rt.accounting.available(US_EXCHANGE, "USD", date(2026, 10, 19)))
        pos = rt.accounting.position(US_EXCHANGE, "ABC")
        self.assertEqual((-1000, -5000000, 0), (pos.qty, pos.mv_c, pos.unrealised_c))
        self.assertEqual((["B-000001"], 1000, 5100000, "GC"), (pos.borrow["borrow_ids"], pos.borrow["qty"], pos.borrow["collateral_c"], pos.borrow["tier"]))
        self.assertEqual(19999500, rt.accounting.nav(US_EXCHANGE).nav_c)
        assert_integrity(self, rt, "full")                                                      # L12 (1,000 >= 1,000) and L13 hold
        # --- Mon 10-19 00:35: nothing is due (S-000001 was settled at 0 by the collateral entry): no double count, settled stays 198,995.00
        rt.clock.set("2026-10-19T00:35:00-04:00")
        self.assertEqual([], self.settle("2026-10-19"))
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

    # ---- SUITES-R1-02 (1.0.4 fixer): a 1.0.3-shape ORDER borrow (its whole 102 % posted at the borrow, collat:{b}:open - the owner's
    # live STT B-000001 / C B-000002) beside a 1.0.4 short on the same name: partial cover, COLLATERAL_MARK, full cover; L13 at each step
    def test_legacy_order_borrow_collateral_at_borrow_beside_a_new_rule_short(self):
        rt = self.start()
        setup_abc(rt)                                                                           # settled 200,000.00; ABC last 50.00

        def legacy_open(ctx, payload):
            """What 1.0.3's open_borrow posted for a short ORDER at submit: the whole 102 % at the locate mark, Dr 1400 / Cr 1010,
            idem collat:{borrow}:open, and borrows.collateral_c = it (the 1.0.4 code keeps such a borrow on that rule)."""
            bid, coll = payload["borrow_id"], int(payload["collateral_c"])
            eid = ctx.post(Entry("COLLATERAL", f"borrow 1,000 ABC (1.0.3 rule): collateral 102 % at the borrow = {coll}",
                                 [dr(US_EXCHANGE, "1400", "USD", coll, instrument_id="ABC", memo=bid), cr(US_EXCHANGE, "1010", "USD", coll, memo=f"cash collateral for {bid}")],
                                 f"collat:{bid}:open", ref_type="borrow", ref_id=bid))
            ctx.conn.execute("UPDATE borrows SET collateral_c=? WHERE borrow_id=?", (coll, bid))
            return {"entry_id": eid}
        rt.bus.register("test.legacy_open_collateral", legacy_open)

        def l13():
            with rt.db.reader() as conn:
                held = conn.execute("SELECT COALESCE(SUM(collateral_c), 0) s FROM borrows WHERE status='OPEN'").fetchone()["s"]
            return bal(rt, US_EXCHANGE, "1400", "USD", "ABC"), held

        # Fri 10:00: SELL_SHORT 1,000 ABC LIMIT 55.00 rests (last 50.00); B-000001 is located for O-000001; the 1.0.3 rule then posted
        # to_c(1.02 x 1,000 x 50.00) = 51,000.00 at the borrow from settled cash: 200,000.00 - 51,000.00 = 149,000.00
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="ABC", side="SELL_SHORT", qty=1000, order_type="LIMIT", limit_px="55.00", tif="DAY")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("WORKING", "B-000001"), (r.body["order"]["status"], r.body["borrow"]["borrow_id"]))
        r = execute(rt, "test.legacy_open_collateral", {"borrow_id": "B-000001", "collateral_c": 5100000})
        self.assertTrue(r.ok, r.body)
        self.assertEqual(((5100000, 5100000), 14900000), (l13(), bal(rt, US_EXCHANGE, "1010")))
        with rt.db.reader() as conn:
            self.assertTrue(lending.collateral_at_borrow(conn, sc.one(rt, "SELECT * FROM borrows WHERE borrow_id='B-000001'")))
        # Fri 10:02: the print 55.00 fills the limit: 1,000 @ 55.00, gross 55,000.00, fee 5.00; the borrow already holds its collateral, so the
        # fill posts none (no collat:B-000001:fill:*) and the proceeds 54,995.00 stay receivable T+1 (Mon 10-19)
        rt.clock.set("2026-10-16T10:02:00-04:00")
        now = rt.clock.epoch()
        rt.marks.set("ABC", "55.00")
        self.assertEqual(1, sc.matcher_for(rt, sc.FakeCache({"ABC": sc.rec("ABC", "55.00", now - 30, now, bar=(now - 60, 100000))})).on_quotes(["ABC"]))
        self.assertEqual([("1200", 5499500, None), ("5000", 500, "ABC"), ("2600", -5500000, "ABC")], lines_of(rt, "fill:F-000001"))
        self.assertEqual([], lines_of(rt, "collat:B-000001:fill:F-000001"))
        self.assertEqual((5499500, "PENDING", "2026-10-19"), tuple(sc.one(rt, "SELECT amount_c, status, due_date FROM settlements WHERE fill_id='F-000001'").values()))
        self.assertEqual((5100000, 5100000), l13())
        assert_integrity(self, rt, "full")
        # Fri 10:03: a 1.0.4 short on the same name, SELL_SHORT 500 MARKET @ 55.00 (B-000002): gross 27,500.00, fee 2.50, proceeds 27,497.50;
        # collateral at the fill to_c(1.02 x 500 x 55.00) = 28,050.00 = proceeds 27,497.50 + cash 552.50 -> settled 149,000.00 - 552.50 = 148,447.50
        rt.clock.set("2026-10-16T10:03:00-04:00")
        quote(rt, "ABC", "55.00", 100000)
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="ABC", side="SELL_SHORT", qty=500, order_type="MARKET")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("FILLED", "B-000002", "F-000002"), (r.body["order"]["status"], r.body["borrow"]["borrow_id"], r.body["fills"][0]["fill_id"]))
        self.assertEqual([("1400", 2805000, "ABC"), ("1200", -2749750, None), ("1010", -55250, None)], lines_of(rt, "collat:B-000002:fill:F-000002"))
        self.assertEqual((7905000, 7905000), l13())                                             # 51,000.00 + 28,050.00
        self.assertEqual(14844750, bal(rt, US_EXCHANGE, "1010"))
        with rt.db.reader() as conn:
            # the 1.0.3 borrow carries its whole outstanding 1,000; the new one the rest of the 1,500 short
            self.assertEqual({"B-000001": 1000, "B-000002": 500}, lending.collateral_qty_map(conn, US_EXCHANGE, "ABC"))
        assert_integrity(self, rt, "full")
        # Fri 10:05: BUY_TO_COVER 600 @ 54.00: FIFO 600 of the F-000001 lot at 55.00 -> realised 33,000 - 32,400 = +600.00, fee 3.00, payable 32,403.00.
        # return_for_cover: borrowed 1,500 - short 900 = 600 back from the oldest borrow B-000001 (its order ended); a borrow collateralised at the
        # borrow returns pro rata of its outstanding: to_c(51,000.00 x 600 / 1,000) = 30,600.00 (Dr 1010 / Cr 1400); 20,400.00 stays for its 400
        rt.clock.set("2026-10-16T10:05:00-04:00")
        quote(rt, "ABC", "54.00", 100000)
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="ABC", side="BUY_TO_COVER", qty=600, order_type="MARKET")
        self.assertTrue(r.ok, r.body)
        fill = r.body["fills"][0]
        self.assertEqual(("F-000003", 3240000, 300, 60000, 3300000), (fill["fill_id"], fill["gross_c"], fill["fee_c"], fill["realised_c"], fill["cost_relieved_c"]))
        self.assertEqual([("1010", 3060000, None), ("1400", -3060000, "ABC")], lines_of(rt, "collat:B-000001:return:F-000003"))
        self.assertEqual([], lines_of(rt, "collat:B-000002:return:F-000003"))                   # the new-rule borrow still backs its 500 sold
        self.assertEqual((("OPEN", 600, 2040000), ("OPEN", 0, 2805000)),
                         tuple((b["status"], b["qty_returned"], b["collateral_c"]) for b in sc.q(rt, "SELECT * FROM borrows ORDER BY borrow_id")))
        self.assertEqual((4845000, 4845000), l13())                                             # 20,400.00 + 28,050.00
        self.assertEqual(17904750, bal(rt, US_EXCHANGE, "1010"))                                 # 148,447.50 + 30,600.00
        with rt.db.reader() as conn:
            self.assertEqual({"B-000001": 400, "B-000002": 500}, lending.collateral_qty_map(conn, US_EXCHANGE, "ABC"))
        assert_integrity(self, rt, "full")
        # Fri 17:10 COLLATERAL_MARK at the CLOSE 56.00: B-000001 (1.0.3 rule) on its whole outstanding 400: to_c(1.02 x 400 x 56.00) = 22,848.00,
        # call +2,448.00; B-000002 on the 500 it backs: to_c(1.02 x 500 x 56.00) = 28,560.00, call +510.00; both paid from settled cash
        rt.clock.set("2026-10-16T17:10:00-04:00")
        for bid, delta, req in (("B-000001", 244800, 2284800), ("B-000002", 51000, 2856000)):
            m = execute(rt, "credit.collateral_mark", {"borrow_id": bid, "mark_date": "2026-10-16", "mark": "56.00"}, request_id=f"job:cmark:{bid}:2026-10-16", actor="lifecycle")
            self.assertTrue(m.ok, m.body)
            self.assertEqual((delta, req, "own"), (m.body["delta_c"], m.body["required_c"], m.body["funding"]["source"]))
            self.assertEqual([("1400", delta, "ABC"), ("1010", -delta, None)], lines_of(rt, f"cmark:{bid}:2026-10-16"))
        self.assertEqual((5140800, 5140800), l13())                                             # 22,848.00 + 28,560.00
        self.assertEqual(17608950, bal(rt, US_EXCHANGE, "1010"))                                 # 179,047.50 - 2,448.00 - 510.00
        assert_integrity(self, rt, "full")
        # Mon 10-19 00:35: F-000001's proceeds 54,995.00 arrive and F-000003's payable 32,403.00 leaves: 176,089.50 + 54,995.00 - 32,403.00 = 198,681.50
        rt.clock.set("2026-10-19T00:35:00-04:00")
        self.assertEqual(2, len(self.settle("2026-10-19")))
        self.assertEqual(19868150, bal(rt, US_EXCHANGE, "1010"))
        # Mon 10:00: BUY_TO_COVER 900 @ 56.00: FIFO 400 + 500 at 55.00 -> realised 49,500 - 50,400 = -900.00, fee 4.50, payable 50,404.50;
        # both borrows close and give back everything they hold: 22,848.00 + 28,560.00 = 51,408.00 -> settled 250,089.50
        rt.clock.set("2026-10-19T10:00:00-04:00")
        quote(rt, "ABC", "56.00", 100000)
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="ABC", side="BUY_TO_COVER", qty=900, order_type="MARKET")
        self.assertTrue(r.ok, r.body)
        fill = r.body["fills"][0]
        self.assertEqual((5040000, 450, -90000), (fill["gross_c"], fill["fee_c"], fill["realised_c"]))
        self.assertEqual([("1010", 2284800, None), ("1400", -2284800, "ABC")], lines_of(rt, f"collat:B-000001:return:{fill['fill_id']}"))
        self.assertEqual([("1010", 2856000, None), ("1400", -2856000, "ABC")], lines_of(rt, f"collat:B-000002:return:{fill['fill_id']}"))
        self.assertEqual([("CLOSED", 1000, 0), ("CLOSED", 500, 0)], [(b["status"], b["qty_returned"], b["collateral_c"]) for b in sc.q(rt, "SELECT * FROM borrows ORDER BY borrow_id")])
        self.assertEqual(((0, 0), 25008950, 5040450), (l13(), bal(rt, US_EXCHANGE, "1010"), bal(rt, US_EXCHANGE, "2100")))
        # NAV flat = 250,089.50 - 50,404.50 = 199,685.00 = 200,000 + realised (600.00 - 900.00) - fees (5.00 + 2.50 + 3.00 + 4.50)
        self.assertEqual(19968500, rt.accounting.nav(US_EXCHANGE).nav_c)
        self.assertIsNone(rt.accounting.position(US_EXCHANGE, "ABC"))
        assert_integrity(self, rt, "full")

    # ---- D4: collateral marks both ways, the call paid by a Treasury sweep ---------------------------------------------------------
    def test_d4_collateral_marks_both_ways(self):
        rt = self.start()
        setup_abc(rt)
        short_abc(self, rt)
        # the account's cash is swept out first (what SWEEP_OUT would do): Fri settled 200,000 - 1,005 (the cash part of the collateral)
        # = 198,995.00 -> Treasury; nothing is receivable (the sale proceeds went to the lender at the fill)
        r = execute(rt, "treasury.transfer", {"from_account": US_EXCHANGE, "to_account": TREASURY, "currency": "USD", "amount_c": 19899500})
        self.assertTrue(r.ok, r.body)
        self.assertEqual(0, bal(rt, US_EXCHANGE, "1010"))
        # Mon 00:35: nothing settles (S-000001 was settled at 0 by the collateral entry); the three weekend nights accrue per §19.5
        # (0.42 / 0.41 / 0.42 and 4.77 / 4.78 / 4.77)
        rt.clock.set("2026-10-19T00:35:00-04:00")
        self.assertEqual([], self.settle("2026-10-19"))
        close_mark(rt, "ABC", "2026-10-16", "50.00")
        done = accrue_due(rt, "2026-10-18")
        self.assertEqual([(42, 477), (41, 478), (42, 477)], [(body["fee_c"], body["rebate_c"]) for _, _, body in done])
        self.assertEqual((125, 1432), (bal(rt, US_EXCHANGE, "2310", "USD", "ABC"), bal(rt, US_EXCHANGE, "1410", "USD", "ABC")))
        # no proceeds ever reach the account (they are the lender's collateral), so it holds no cash before the call
        self.assertEqual((0, 0), (bal(rt, US_EXCHANGE, "1010"), bal(rt, US_EXCHANGE, "1200")))
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
        # fill 800 @ 10.00: gross 8,000.00, fee max(800 x 0.005 = 4.00, 1.00) = 4.00; nothing at the borrow; the fill posts the collateral
        # to_c(1.02 x 800 x 10.00) = 8,160.00 = the proceeds 8,000 - 4 = 7,996.00 + cash 164.00 (2 % excess 160.00 + fee 4.00)
        self.assertEqual([], lines_of(rt, "collat:B-000001:open"))
        self.assertEqual([("1400", 816000, "T3X"), ("1200", -799600, None), ("1010", -16400, None)], lines_of(rt, "collat:B-000001:fill:F-000001"))
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

    # ---- SHORT_COLLATERAL per fill: partial fills, a cover while the order still works, a mark, cleanup of the unsold rest ------------
    def test_partial_fills_cover_while_working_mark_cleanup(self):
        rt = self.start()
        setup_abc(rt)
        # Fri 10:00: SELL_SHORT 1,000 ABC MARKET on a 3,000-share bar -> cap 10 % = 300: PARTIAL 300 @ 50.00, fee max(300 x 0.005 = 1.50, 1.00) = 1.50;
        # the collateral of the 300 sold: to_c(1.02 x 300 x 50.00) = 15,300.00 = proceeds 15,000 - 1.50 = 14,998.50 + cash 301.50 (2 % 300.00 + fee 1.50)
        quote(rt, "ABC", "50.00", 3000)
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="ABC", side="SELL_SHORT", qty=1000, order_type="MARKET")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("PARTIAL", 300, "B-000001"), (r.body["order"]["status"], r.body["order"]["filled_qty"], r.body["borrow"]["borrow_id"]))
        self.assertEqual([], lines_of(rt, "collat:B-000001:open"))
        self.assertEqual([("1400", 1530000, "ABC"), ("1200", -1499850, None), ("1010", -30150, None)], lines_of(rt, "collat:B-000001:fill:F-000001"))
        # Fri 10:02: the next print 52.00 on a 3,000-share bar fills 300 more: to_c(1.02 x 300 x 52.00) = 15,912.00 = proceeds 15,600 - 1.50 = 15,598.50 + cash 313.50
        rt.clock.set("2026-10-16T10:02:00-04:00")
        now = rt.clock.epoch()
        rt.marks.set("ABC", "52.00")
        self.assertEqual(1, sc.matcher_for(rt, sc.FakeCache({"ABC": sc.rec("ABC", "52.00", now - 30, now, bar=(now - 60, 3000))})).on_quotes(["ABC"]))
        self.assertEqual([("1400", 1591200, "ABC"), ("1200", -1559850, None), ("1010", -31350, None)], lines_of(rt, "collat:B-000001:fill:F-000002"))
        # the borrow holds 15,300 + 15,912 = 31,212.00 for the 600 sold while 400 still pend (reason stays); settled 200,000 - 301.50 - 313.50 = 199,385.00;
        # both fills' settlement rows are SETTLED at 0, nothing is receivable
        b = borrow_row(rt, "B-000001")
        self.assertEqual((1000, 0, 3121200, lending.REASON_PENDING), (b["qty"], b["qty_returned"], b["collateral_c"], b["reason"]))
        self.assertEqual((19938500, 0), (bal(rt, US_EXCHANGE, "1010"), bal(rt, US_EXCHANGE, "1200")))
        self.assertEqual([(0, "SETTLED"), (0, "SETTLED")], [(s["amount_c"], s["status"]) for s in sc.q(rt, "SELECT amount_c, status FROM settlements ORDER BY settle_id")])
        with rt.db.reader() as conn:
            self.assertEqual({"B-000001": 600}, lending.collateral_qty_map(conn, US_EXCHANGE, "ABC"))     # min(1,000 - 400 pending, short 600)
        assert_integrity(self, rt, "full")
        # Fri 10:05: BUY_TO_COVER 200 @ 51.00 while 400 still pend: FIFO 200 of the 50.00 lot -> proceeds 10,000 - cost 10,200 = -200.00, fee 1.00.
        # return_for_cover: 1,000 borrowed - 400 short - 400 pending = 200 back, all sold shares: collateralised min(1,000 - 400, 600) = 600 before and
        # min(800 - 400, 400) = 400 after -> to_c(31,212 x 200 / 600) = 10,404.00 back (Dr 1010 / Cr 1400); 20,808.00 stays for the 400 still short
        rt.clock.set("2026-10-16T10:05:00-04:00")
        quote(rt, "ABC", "51.00", 100000)
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="ABC", side="BUY_TO_COVER", qty=200, order_type="MARKET")
        self.assertTrue(r.ok, r.body)
        fill = r.body["fills"][0]
        self.assertEqual(("F-000003", 1020000, 100, -20000), (fill["fill_id"], fill["gross_c"], fill["fee_c"], fill["realised_c"]))
        self.assertEqual([("1010", 1040400, None), ("1400", -1040400, "ABC")], lines_of(rt, "collat:B-000001:return:F-000003"))
        b = borrow_row(rt, "B-000001")
        self.assertEqual(("OPEN", 200, 2080800, lending.REASON_PENDING), (b["status"], b["qty_returned"], b["collateral_c"], b["reason"]))
        assert_integrity(self, rt, "full")
        # Fri 17:10 mark at 50.00: collateralised min(800 - 400 pending, 400 short) = 400 -> to_c(1.02 x 400 x 50.00) = 20,400.00; 20,808.00 held -> 408.00 back
        rt.clock.set("2026-10-16T17:10:00-04:00")
        r = execute(rt, "credit.collateral_mark", {"borrow_id": "B-000001", "mark_date": "2026-10-16", "mark": "50.00"}, actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((-40800, 2040000), (r.body["delta_c"], r.body["required_c"]))
        self.assertEqual([("1010", 40800, None), ("1400", -40800, "ABC")], lines_of(rt, "cmark:B-000001:2026-10-16"))
        # cancel the 400 still working (600 filled: the cancel returns nothing); BORROW_CLEANUP finds 800 - 400 short = 400 free, never sold: no collateral
        # moves (collateralised min(800, 400) = 400 before, min(400, 400) = 400 after) and no entry is posted; the order ended, so the reason clears
        r = execute(rt, "order.cancel", {"order_id": "O-000001"})
        self.assertTrue(r.ok, r.body)
        n0 = n_entries(rt)
        r = execute(rt, "credit.borrow_cleanup", {"date": "2026-10-16", "borrow_id": "B-000001"}, request_id="job:cleanup:B-000001:2026-10-16", actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        ret = r.body["returns"][0]
        self.assertEqual((400, 0, False, 400), (ret["qty"], ret["collateral_returned_c"], ret["closed"], ret["outstanding"]))
        self.assertEqual(n0, n_entries(rt))
        b = borrow_row(rt, "B-000001")
        self.assertEqual((600, 2040000, None), (b["qty_returned"], b["collateral_c"], b["reason"]))
        assert_integrity(self, rt, "full")
        # Mon 10-19 10:00: BUY_TO_COVER the last 400 @ 50.00: FIFO 100 of the 50.00 lot + 300 of the 52.00 lot = proceeds 5,000 + 15,600 = 20,600.00 - cost
        # 20,000.00 = +600.00, fee 2.00; the borrow closes and its 20,400.00 comes back
        rt.clock.set("2026-10-19T10:00:00-04:00")
        quote(rt, "ABC", "50.00", 100000)
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="ABC", side="BUY_TO_COVER", qty=400, order_type="MARKET")
        self.assertTrue(r.ok, r.body)
        fill = r.body["fills"][0]
        self.assertEqual((2000000, 200, 60000), (fill["gross_c"], fill["fee_c"], fill["realised_c"]))
        self.assertEqual([("1010", 2040000, None), ("1400", -2040000, "ABC")], lines_of(rt, f"collat:B-000001:return:{fill['fill_id']}"))
        b = borrow_row(rt, "B-000001")
        self.assertEqual(("CLOSED", 1000, 0), (b["status"], b["qty_returned"], b["collateral_c"]))
        # settled 199,385.00 + 10,404.00 + 408.00 + 20,400.00 = 230,597.00; payables 10,201.00 + 20,002.00 = 30,203.00; NAV 200,394.00 = 200,000
        # + realised (-200.00 + 600.00) - fees (1.50 + 1.50 + 1.00 + 2.00); every collateral cent posted (31,212.00) came back
        self.assertEqual((23059700, 3020300, 0, 0), (bal(rt, US_EXCHANGE, "1010"), bal(rt, US_EXCHANGE, "2100"), bal(rt, US_EXCHANGE, "1200"),
                                                    bal(rt, US_EXCHANGE, "1400", "USD", "ABC")))
        self.assertEqual(20039400, rt.accounting.nav(US_EXCHANGE).nav_c)
        assert_integrity(self, rt, "full")


class ProveShortRegressionTests(unittest.TestCase):
    """TEST-10 (FINSIM1 P6 task 10): the 1.0.3.1 hotfix proof ``prove_short.py`` (its 22 checks), kept as a regression test.  The owner's
    Fund-book short of 8,880 STT at 168.95 in F1:PB_GS with 23.5M of long orders queued and only 1,467,580.80 left in Treasury: the
    preview needs 30,032.16 (2 % excess + fee), the fill posts 1,530,281.52 of collateral (the sale's own proceeds first), a +10 % mark
    calls 153,073.44, a -5 % mark returns 84,145.10, the cover realises -67,576.80 and returns 1,599,209.86.  The real runtime as the
    tests build it: FakeClock, FixedMarks, the real order / lending / credit / prime handlers, the real lifecycle SETTLE_DUE job and the
    real matcher for the open.  Every number is worked in the comment beside its check (numbered as in the script)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-proveshort-")
        self.rt = None

    def tearDown(self):
        if self.rt is not None:
            self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_prove_short_regression(self):
        import types
        from tests.terminal_helpers import make_runtime
        from finsim.terminal import credit, lifecycle, orders, prime
        from finsim.terminal.api_views import trial_balance
        from finsim.terminal.clock import epoch_of
        PB = "F1:PB_GS"
        rt = self.rt = make_runtime(self.tmp, at="2026-10-22T08:00:00-04:00", books="real")
        orders.register(rt.bus)
        lending.register(rt.bus)
        lending.bind(rt)
        credit.register(rt.bus)
        rt.credit = credit
        prime.register(rt.bus)
        prime.bind(rt)
        repo.register(rt.bus)
        rt.rates = RateStore()
        rt.rates.put("SOFR", [(date(2026, 10, 21), Decimal("3.87")), (date(2026, 10, 26), Decimal("3.87"))], persist=False)
        rt.providers = types.SimpleNamespace(yahoo=rt.fp)
        lifecycle.register(rt.bus, rt)
        rt.lifecycle = lifecycle.LifecycleScheduler(rt.bus, rt.db, rt, rt.clock, rt.log)

        def quote_at(iid, last, qt, vol):
            rt.marks.set_quote(sc.rec(iid, last, qt, rt.clock.epoch(), bar=(qt - 60, vol)))
            rt.marks.set(iid, last)

        # Thu 10-22 08:00 (pre-open): open F1:PB_GS, queue the longs, leave exactly 1,467,580.80 for new orders in Treasury
        r = execute(rt, "account.open_pb", {"lifetime_id": "F1", "broker": "GS"})
        self.assertTrue(r.ok, r.body)
        wed_close = epoch_of(date(2026, 10, 21), "16:00")
        quote_at("AAPL", "339.50", wed_close - 60, 1_000_000)
        quote_at("STT", "168.95", wed_close - 60, 100_000)
        finra(rt, "STT", [("2026-10-19", 30000, 100000), ("2026-10-20", 30000, 100000), ("2026-10-21", 30000, 100000)])   # ratio 0.30 -> GC
        r = sc.submit(rt, account_id=PB, instrument_id="AAPL", side="BUY", qty=67000, order_type="MARKET", tif="DAY", confirm_large=True)
        self.assertTrue(r.ok and r.body["order"]["status"] == "WORKING", r.body)
        bp = rt.accounting.buying_power(PB, "USD")
        self.assertTrue(execute(rt, "treasury.withdraw", {"lifetime_id": "F1", "amount_c": int(bp.treasury_available_c) - 146758080, "currency": "USD"}).ok)
        # the preview of SELL_SHORT 8,880 STT MARKET DAY at the last 168.95: gross 8,880 x 168.95 = 1,500,276.00, GS fee 8,880 x 0.0030 = 26.64
        req = {"account_id": PB, "instrument_id": "STT", "side": "SELL_SHORT", "qty": 8880, "order_type": "MARKET", "tif": "DAY"}
        pv = orders.preview(rt, req)
        f = pv.get("funding") or {}
        self.assertIs(True, pv.get("ok"), pv.get("blockers"))                                                       # check 1
        # need = collateral to_c(1.02 x 1,500,276.00) = 1,530,281.52 - (1,500,276.00 - 26.64 = 1,500,249.36) = 30,032.16 (2 % 30,005.52 + fee)
        self.assertEqual("30032.16", f.get("need"))                                                                  # check 2
        r = sc.submit(rt, **req)
        self.assertTrue(r.ok and r.body["order"]["status"] == "WORKING" and r.body["borrow"]["borrow_id"] == "B-000001", r.body)   # check 3
        oid_short = r.body["order"]["order_id"]
        b = borrow_row(rt, "B-000001")
        self.assertEqual((8880, "GC", 0, lending.REASON_PENDING, []),
                         (b["qty"], b["tier"], b["collateral_c"], b["reason"], lines_of(rt, "collat:B-000001:open")))   # check 4
        # the working short reserves only its cash need at the reservation's default fee: 1,530,281.52 - (1,500,276.00 - 44.40) = 30,049.92
        self.assertEqual(2320176500 + 3004992, rt.accounting.cash(PB)["USD"].reserved_c)                            # check 5 (+ AAPL 23,201,765.00)
        # Thu 09:32 the open: the matcher fills the queued orders from fresh prints
        rt.clock.set("2026-10-22T09:32:00-04:00")
        now = rt.clock.epoch()
        cache = sc.FakeCache({"AAPL": sc.rec("AAPL", "339.50", now - 60, now, bar=(now - 120, 1_000_000)),
                              "STT": sc.rec("STT", "168.95", now - 60, now, bar=(now - 120, 100_000))})
        rt.marks.set("AAPL", "339.50")
        rt.marks.set("STT", "168.95")
        sc.matcher_for(rt, cache).sweep()
        o = sc.order_row(rt, oid_short)
        fs = sc.one(rt, "SELECT * FROM fills WHERE order_id=?", (oid_short,))
        self.assertTrue(o["status"] == "FILLED" and fs is not None and (fs["qty"], fs["price"]) == (8880, "168.95"))   # check 6
        fid = fs["fill_id"]
        # collateral 1,530,281.52 at the fill: 1,500,249.36 (= 1,500,276.00 - fee 26.64) from the proceeds, 30,032.16 from cash
        self.assertEqual([("1400", 153028152, "STT"), ("1200", -150024936, None), ("1010", -3003216, None)],
                         lines_of(rt, f"collat:B-000001:fill:{fid}"))                                                  # check 7
        st = sc.one(rt, "SELECT * FROM settlements WHERE fill_id=?", (fid,))
        ce = sc.one(rt, "SELECT entry_id FROM journal_entries WHERE idem_key=?", (f"collat:B-000001:fill:{fid}",))["entry_id"]
        self.assertEqual((0, "SETTLED", ce, "2026-10-23"), (st["amount_c"], st["status"], st["entry_id"], st["due_date"]))   # check 8
        b = borrow_row(rt, "B-000001")
        self.assertEqual((153028152, 153028152, None), (b["collateral_c"], bal(rt, PB, "1400", "USD", "STT"), b["reason"]))   # check 9
        self.assertEqual(0, bal(rt, PB, "1200"))                                                                    # check 10: proceeds at the lender
        self.assertTrue(rt.accounting.integrity("full").ok)                                                        # check 11
        # Thu 17:10 mark on the recorded CLOSE 168.95: 102 % x 8,880 x 168.95 = 1,530,281.52 -> no movement
        rt.clock.set("2026-10-22T17:10:00-04:00")
        close_mark(rt, "STT", "2026-10-22", "168.95")
        r = execute(rt, "credit.collateral_mark", {"borrow_id": "B-000001", "mark_date": "2026-10-22"}, request_id="job:cmark:B-000001:2026-10-22",
                    actor="lifecycle")
        self.assertTrue(r.ok and r.body["delta_c"] == 0, r.body)                                                     # check 12
        # Fri 10-23 00:35 T+1: the real SETTLE_DUE settles the AAPL payable only; the short's row carries 0 and is never settled again
        out = rt.lifecycle.tick(at="2026-10-23T00:35:00-04:00", only="SETTLE_DUE")
        ran = [i["key"] for i in out["ran"]]
        settled_entries = [e["idem_key"] for e in sc.q(rt, "SELECT idem_key FROM journal_entries WHERE idem_key LIKE 'settle:%'")]
        self.assertTrue(st["settle_id"] not in ran and f"settle:{st['settle_id']}" not in settled_entries and bal(rt, PB, "1200") == 0,
                        settled_entries)                                                                             # check 13
        accrue_due(rt, "2026-10-22")
        # Fri 17:10 +10 %: CLOSE 185.85 (168.95 x 1.10 = 185.845): to_c(1.02 x 8,880 x 185.85) = 1,683,354.96 -> call 153,073.44 (Dr 1400 / Cr 1010)
        rt.clock.set("2026-10-23T17:10:00-04:00")
        close_mark(rt, "STT", "2026-10-23", "185.85")
        r = execute(rt, "credit.collateral_mark", {"borrow_id": "B-000001", "mark_date": "2026-10-23"}, request_id="job:cmark:B-000001:2026-10-23",
                    actor="lifecycle")
        self.assertTrue(r.ok and (r.body["delta_c"], r.body["required_c"]) == (15307344, 168335496), r.body)
        self.assertEqual([("1400", 15307344, "STT"), ("1010", -15307344, None)], lines_of(rt, "cmark:B-000001:2026-10-23"))   # check 14
        rt.clock.set("2026-10-26T00:35:00-04:00")
        accrue_due(rt, "2026-10-25")
        # Mon 10-26 17:10 -5 %: CLOSE 176.56 (185.85 x 0.95 = 176.5575): to_c(1.02 x 8,880 x 176.56) = 1,599,209.86 -> 84,145.10 back (Dr 1010 / Cr 1400)
        rt.clock.set("2026-10-26T17:10:00-04:00")
        close_mark(rt, "STT", "2026-10-26", "176.56")
        r = execute(rt, "credit.collateral_mark", {"borrow_id": "B-000001", "mark_date": "2026-10-26"}, request_id="job:cmark:B-000001:2026-10-26",
                    actor="lifecycle")
        self.assertTrue(r.ok and (r.body["delta_c"], r.body["required_c"]) == (-8414510, 159920986), r.body)
        self.assertEqual([("1010", 8414510, None), ("1400", -8414510, "STT")], lines_of(rt, "cmark:B-000001:2026-10-26"))       # check 15
        rt.clock.set("2026-10-27T00:35:00-04:00")
        accrue_due(rt, "2026-10-26")
        self.assertTrue(rt.accounting.integrity("full").ok)                                                        # check 16
        # Tue 10-27 10:00 BUY_TO_COVER 8,880 STT MARKET at 176.56: realised 1,500,276.00 - 8,880 x 176.56 (1,567,852.80) = -67,576.80; the borrow
        # returns in the fill's command with all its collateral, 1,599,209.86
        rt.clock.set("2026-10-27T10:00:00-04:00")
        now = rt.clock.epoch()
        quote_at("STT", "176.56", now - 60, 100_000)
        r = sc.submit(rt, account_id=PB, instrument_id="STT", side="BUY_TO_COVER", qty=8880, order_type="MARKET", tif="DAY")
        self.assertTrue(r.ok, r.body)
        fc = r.body["fills"][0]
        self.assertEqual(-6757680, fc["realised_c"])                                                                # check 17
        b = borrow_row(rt, "B-000001")
        self.assertEqual([("1010", 159920986, None), ("1400", -159920986, "STT")], lines_of(rt, f"collat:B-000001:return:{fc['fill_id']}"))
        self.assertEqual(("CLOSED", 0, 8880), (b["status"], b["collateral_c"], b["qty_returned"]))
        self.assertEqual((0, 0, 0), (bal(rt, PB, "1400", "USD", "STT"), bal(rt, PB, "2310", "USD", "STT"), bal(rt, PB, "1410", "USD", "STT")))   # check 18
        rt.lifecycle.tick(at="2026-10-28T00:35:00-04:00", only="SETTLE_DUE")
        self.assertTrue(rt.accounting.integrity("full").ok)                                                        # check 19
        for scope in ("ALL", "F1:ALL"):                                                                             # checks 20, 21
            self.assertTrue(trial_balance(rt, scope)["balanced"], scope)
        self.assertEqual([], sc.q(rt, "SELECT settle_id FROM settlements WHERE status='PENDING'"))                 # check 22


if __name__ == "__main__":
    unittest.main()
