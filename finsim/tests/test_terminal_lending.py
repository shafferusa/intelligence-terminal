"""WP-06 lending unit tests (TERMINAL_DESIGN.md §12.2 tiers, §11.2 BORROW_CLEANUP, §11.3 accruals, §13.1) and the Build 2 P08
money paths: repo / reverse repo against a Treasury (§13.9 made real) and lending shares out through an agent lender.
Every expected number is worked by hand in a comment.  Scaffolding comes from test_terminal_shorts (imported as a
module so its cases are not loaded twice)."""
from __future__ import annotations

import shutil
import tempfile
import types
import unittest
from datetime import date
from decimal import Decimal

from tests.terminal_helpers import CREDIT, DEBT, TREASURY, US_EXCHANGE, assert_integrity, bal, execute, fund
from tests import test_terminal_orders as sc
from tests import test_terminal_shorts as sh
from finsim.terminal import lending, lifecycle, repo, tables
from finsim.terminal.clock import epoch_of
from finsim.terminal.db import set_setting
from finsim.terminal.ledger import Line
from finsim.terminal.products import bonds

D = Decimal


def _h_accrue_segment(ctx, payload):
    """Test-only: drive ``lending.accrue_segment`` through the bus (touches accrual_state only, so light)."""
    posted = lending.accrue_segment(ctx, payload["kind"], payload["ref"], int(payload["base_c"]), D(payload["rate"]), int(payload["basis"]), payload["night"])
    return {"posted": posted}


class LendingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-lend-")
        self.rt = None

    def tearDown(self):
        if self.rt is not None:
            self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start(self, at=sh.FRI_10, sofr=("2026-10-15", "3.87")):
        self.rt = sh.runtime(self.tmp, at=at, sofr=sofr)
        self.rt.bus.register("test.accrue_segment", _h_accrue_segment, light=True)
        return self.rt

    # ---- §12.2 tier table ---------------------------------------------------------------------------------------------------
    def test_tier_table_cells(self):
        # the pure rule first (ratio, ADV, FINRA rows in the last 3 sessions, kind, shortable)
        bt = tables.borrow_tier
        self.assertEqual(("GC", None), bt(True, 5_000_000, D("0.30"), 2))
        self.assertEqual(("T1", None), bt(True, 5_000_000, D("0.60"), 2))            # ratio >= 0.60
        self.assertEqual(("T1", None), bt(True, 999_999, D("0.10"), 1))              # ADV in [250k, 1M)
        self.assertEqual(("T1", None), bt(True, 250_000, D("0.10"), 1))
        self.assertEqual(("T2", 62_499), bt(True, 249_999, D("0.10"), 1))            # int(249,999 x 0.25) = int(62,499.75) = 62,499
        self.assertEqual(("T3", 800), bt(True, 8_000, None, 0))                      # no FINRA row for 3 sessions: int(8,000 x 0.10) = 800
        self.assertEqual(("T3", 9_000), bt(True, 90_000, D("0.30"), 2, "PREFERRED"))  # preferred with ADV < 100k: int(90,000 x 0.10)
        self.assertEqual(("T2", 25_000), bt(True, 100_000, D("0.30"), 2, "ADR"))     # ADR with ADV 100k is not < 100k -> ADV < 250k -> T2: 25,000
        self.assertEqual(("NONE", 0), bt(False, 5_000_000, D("0.30"), 2))
        self.assertEqual(("NONE", 0), bt(True, None, D("0.30"), 2))
        self.assertEqual(("NONE", 0), bt(True, 5_000_000, D("0.30"), 2, "EQUITY", False))
        # the locate over instruments in the master at Fri 10-16 10:00 (window of the last 3 sessions: Wed 14, Thu 15, Fri 16)
        rt = self.start()
        for iid, adv, kind in (("GC1", 5_000_000, "EQUITY"), ("T1R", 5_000_000, "EQUITY"), ("T1A", 600_000, "EQUITY"), ("T2A", 200_000, "ETF"),
                               ("T3N", 8_000, "EQUITY"), ("T3P", 90_000, "PREFERRED"), ("STL", 5_000_000, "EQUITY"), ("NOA", None, "EQUITY")):
            sh.add_equity(rt, iid, adv, kind=kind)
            rt.marks.set(iid, "10.00")
        sh.add_equity(rt, "NSH", 5_000_000, shortable=False)
        rt.marks.set("NSH", "10.00")
        sh.finra(rt, "GC1", [("2026-10-15", 30_000, 100_000)])
        sh.finra(rt, "T1R", [("2026-10-15", 65_000, 100_000)])
        sh.finra(rt, "T1A", [("2026-10-15", 30_000, 100_000)])
        sh.finra(rt, "T2A", [("2026-10-15", 30_000, 100_000)])
        sh.finra(rt, "T3P", [("2026-10-15", 30_000, 100_000)])
        sh.finra(rt, "STL", [("2026-10-09", 30_000, 100_000)])                       # only a stale row, outside the window -> T3
        sh.finra(rt, "NSH", [("2026-10-15", 30_000, 100_000)])
        now = epoch_of(date(2026, 10, 16), "10:00")
        got = {iid: lending.locate(rt, US_EXCHANGE, rt.instruments.get(iid), 80_000) for iid in ("GC1", "T1R", "T1A", "T2A", "T3N", "T3P", "STL", "NOA", "NSH")}
        # GC1: full, fee 0.30 %, rebate 3.87 - 0.50 = 3.37 %, collateral to_c(1.02 x 80,000 x 10.00) = 816,000.00
        g = got["GC1"]
        self.assertEqual((True, 80_000, "GC", D("0.003"), D("0.0337"), 81_600_000, D("10"), now + 120), (g.available, g.qty_available, g.tier, g.fee_rate, g.rebate_rate, g.collateral_c, g.mark, g.valid_until))
        self.assertEqual((D("0.3"), "2026-10-15", 1, 5_000_000), (g.finra_ratio, g.finra_day, g.finra_rows_last3, g.adv))
        self.assertIn("tier GC", g.basis)
        # Build 2 P08 (OA-17): outside a prime broker the supply comes from the directory's default agent lender, labelled as assumed terms
        self.assertEqual(("The Bank of New York Mellon (BNY) (agent lender, assumed terms)", "AGENT_BNY"), (g.lender, g.counterparty_id))
        self.assertEqual("0.0337", g.to_dict()["rebate_rate"])
        self.assertEqual(("T1", 80_000, D("0.01")), (got["T1R"].tier, got["T1R"].qty_available, got["T1R"].fee_rate))
        self.assertEqual(("T1", 80_000, D("0.01")), (got["T1A"].tier, got["T1A"].qty_available, got["T1A"].fee_rate))
        # T2A: min(80,000, 0.25 x 200,000 = 50,000) = 50,000 at 5 %; collateral 1.02 x 50,000 x 10 = 510,000.00
        self.assertEqual(("T2", 50_000, D("0.05"), 51_000_000), (got["T2A"].tier, got["T2A"].qty_available, got["T2A"].fee_rate, got["T2A"].collateral_c))
        # T3N: min(80,000, 0.10 x 8,000 = 800) at 25 %, rebate 0; collateral 1.02 x 800 x 10 = 8,160.00
        self.assertEqual(("T3", 800, D("0.25"), D("0"), 816_000), (got["T3N"].tier, got["T3N"].qty_available, got["T3N"].fee_rate, got["T3N"].rebate_rate, got["T3N"].collateral_c))
        self.assertEqual(("T3", 9_000), (got["T3P"].tier, got["T3P"].qty_available))
        self.assertEqual(("T3", 80_000, 0), (got["STL"].tier, got["STL"].qty_available, got["STL"].finra_rows_last3))   # cap int(0.10 x 5M) = 500,000 > 80,000
        self.assertEqual((False, 0, "NONE"), (got["NOA"].available, got["NOA"].qty_available, got["NOA"].tier))
        self.assertIn("no average daily volume", got["NOA"].basis)
        self.assertEqual((False, "NONE"), (got["NSH"].available, got["NSH"].tier))
        self.assertIn("not shortable", got["NSH"].basis)
        # an FX pair is in the master but is not a borrowable kind; None is "not in the master"
        fx = lending.locate(rt, US_EXCHANGE, rt.instruments.get("EURUSD"), 10)
        self.assertEqual((False, "NONE"), (fx.available, fx.tier))
        self.assertIn("cannot be borrowed", fx.basis)
        self.assertFalse(lending.locate(rt, US_EXCHANGE, None, 10).available)
        # no mark -> no collateral can be sized -> unavailable (never a default price)
        rt.marks.unset("GC1")
        nm = lending.locate(rt, US_EXCHANGE, rt.instruments.get("GC1"), 100)
        self.assertEqual((False, "NONE"), (nm.available, nm.tier))
        self.assertIn("no mark", nm.basis)
        # without a SOFR print the rebate is unavailable (not 0): the ticket says so; T3 stays 0 by rule
        rt.rates = None
        rt.marks.set("GC1", "10.00")
        g2 = lending.locate(rt, US_EXCHANGE, rt.instruments.get("GC1"), 100)
        self.assertIsNone(g2.rebate_rate)
        self.assertIn("rebate unavailable", g2.basis)
        self.assertEqual(D("0"), lending.locate(rt, US_EXCHANGE, rt.instruments.get("T3N"), 100).rebate_rate)
        # the rate helpers
        self.assertEqual(D("0.0337"), lending.rebate_rate(D("3.87"), "GC"))
        self.assertEqual(D("0"), lending.rebate_rate(D("0.30"), "GC"))                 # 0.30 - 0.50 < 0 -> floor 0
        self.assertEqual(D("0"), lending.rebate_rate(D("3.87"), "T3"))
        self.assertEqual(D("0.004"), lending.fee_rate("GC", {"gc_rate": D("0.0040"), "htb_add": D("0.0010")}))   # PB schedule: GC uses gc_rate
        self.assertEqual(D("0.011"), lending.fee_rate("T1", {"gc_rate": D("0.0040"), "htb_add": D("0.0010")}))   # other tiers add htb_add
        self.assertIsNone(lending.fee_rate("NONE"))

    # ---- collateral rounding, pro-rata shares, mark lines (pure) ---------------------------------------------------------------
    def test_collateral_rounding_and_mark_lines(self):
        c = lending.collateral_required_c
        self.assertEqual(5_100_000, c(1000, "50"))                 # 1.02 x 1,000 x 50 = 51,000.00
        self.assertEqual(8814, c(7, "12.345"))                      # 7 x 12.345 = 86.415; x 1.02 = 88.1433 -> 88.14
        self.assertEqual(638, c(25, "0.25"))                        # 25 x 0.25 = 6.25; x 1.02 = 6.375 -> tie -> even -> 6.38
        self.assertEqual(382, c(5, "0.75"))                         # 5 x 0.75 = 3.75; x 1.02 = 3.825 -> tie -> even -> 3.82
        self.assertEqual(13, c(1, "0.125"))                         # 1.02 x 0.125 = 0.1275 -> 0.13
        pence = types.SimpleNamespace(multiplier=D(1), price_scale=D("0.01"))
        self.assertEqual(25_500, c(100, "250", pence))              # 100 x 250p x 0.01 = 250.00; x 1.02 = 255.00
        # pro-rata shares: the last unit takes the residual
        self.assertEqual(1_591_200, lending.pro_rata_c(5_304_000, 300, 1000))     # 53,040 x 300 / 1,000 = 15,912.00
        self.assertEqual(5_304_000, lending.pro_rata_c(5_304_000, 1000, 1000))
        self.assertEqual(33, lending.pro_rata_c(100, 1, 3))                        # 1.00 / 3 = 0.3333 -> 0.33
        self.assertEqual(100, lending.pro_rata_c(100, 3, 3))
        self.assertEqual(62, lending.pro_rata_c(125, 1, 2))                        # 0.625 -> tie -> even -> 0.62
        self.assertEqual(125, lending.pro_rata_c(125, 2, 2))
        # §19.5 collateral mark lines on the borrow row (1,000 ABC, collateral 51,000.00): 52.00 -> call 2,040.00; 48.00 -> 2,040.00 back; 50.00 -> nothing
        row = {"borrow_id": "B-000001", "account_id": US_EXCHANGE, "instrument_id": "ABC", "currency": "USD", "qty": 1000, "qty_returned": 0, "collateral_c": 5_100_000}
        ctx = types.SimpleNamespace(services=None, conn=None)
        lines, delta = lending.collateral_mark_lines(ctx, row, D("52.00"), "2026-10-19")
        self.assertEqual(204_000, delta)
        self.assertEqual([(US_EXCHANGE, "1400", "USD", 204_000, "ABC"), (US_EXCHANGE, "1010", "USD", -204_000, None)], [(l.account_id, l.gl, l.currency, l.amount_c, l.instrument_id) for l in lines])
        lines, delta = lending.collateral_mark_lines(ctx, row, D("48.00"), "2026-10-19")
        self.assertEqual(-204_000, delta)                                          # required to_c(1.02 x 1,000 x 48) = 48,960.00
        self.assertEqual([(US_EXCHANGE, "1010", "USD", 204_000, None), (US_EXCHANGE, "1400", "USD", -204_000, "ABC")], [(l.account_id, l.gl, l.currency, l.amount_c, l.instrument_id) for l in lines])
        self.assertEqual(([], 0), lending.collateral_mark_lines(ctx, row, D("50.00"), "2026-10-19"))
        self.assertTrue(all(isinstance(l, Line) for l in lines))
        # a partly returned borrow marks on its outstanding quantity: 700 left of 1,000 at 52.00 -> required 37,128.00
        row2 = dict(row, qty_returned=300, collateral_c=3_712_800)                  # 53,040 - 15,912 = 37,128.00 already held
        self.assertEqual(([], 0), lending.collateral_mark_lines(ctx, row2, D("52.00"), "2026-10-20"))

    # ---- §11.3 cumulative-rounding segments ---------------------------------------------------------------------------------------
    def test_accrual_segments(self):
        rt = self.start()
        # the pure step on an empty state: night 1 of 50,000.00 at 0.30 % = to_c(0.416667) = 0.42
        self.assertEqual((42, "2026-10-16", 42), lending.segment_amount(None, 5_000_000, D("0.003"), 360, "2026-10-16"))

        def post(kind, ref, base_c, rate, night):
            r = execute(rt, "test.accrue_segment", {"kind": kind, "ref": ref, "base_c": base_c, "rate": rate, "basis": 360, "night": night}, actor="lifecycle")
            return r if not r.ok else r.body["posted"]
        # fee: 0.42 / 0.41 / 0.42 (cumulative 0.42, 0.83, 1.25); a repeated night posts 0 and leaves the state alone
        self.assertEqual([42, 41, 42], [post("bfee", "X", 5_000_000, "0.003", d) for d in ("2026-10-16", "2026-10-17", "2026-10-18")])
        self.assertEqual(0, post("bfee", "X", 5_000_000, "0.003", "2026-10-18"))
        st = sc.one(rt, "SELECT * FROM accrual_state WHERE kind='bfee' AND ref_id='X'")
        self.assertEqual(("2026-10-16", 5_000_000, "0.003", 360, "2026-10-18", 125), (st["segment_start"], st["base_c"], st["rate"], st["day_basis"], st["accrued_through"], st["cum_c"]))
        # a night out of order is refused (never lumped into a later night)
        r = post("bfee", "X", 5_000_000, "0.003", "2026-10-20")
        self.assertEqual((False, 400, "ACCRUAL_GAP"), (r.ok, r.http, r.code))
        # a changed base restarts the segment: 52,000.00 -> to_c(0.433333) = 0.43 on the 19th; then n=2 -> to_c(0.866667) = 0.87 -> 0.44
        self.assertEqual(43, post("bfee", "X", 5_200_000, "0.003", "2026-10-19"))
        st = sc.one(rt, "SELECT * FROM accrual_state WHERE kind='bfee' AND ref_id='X'")
        self.assertEqual(("2026-10-19", 5_200_000, "2026-10-19", 43), (st["segment_start"], st["base_c"], st["accrued_through"], st["cum_c"]))
        self.assertEqual(44, post("bfee", "X", 5_200_000, "0.003", "2026-10-20"))
        # a changed rate restarts too: 0.0035 on the 21st -> to_c(52,000 x 0.0035 / 360) = to_c(0.505556) = 0.51
        self.assertEqual(51, post("bfee", "X", 5_200_000, "0.0035", "2026-10-21"))
        # rebate 51,000.00 at 3.37 %: 4.77 / 4.78 / 4.77 (cumulative 4.77, 9.55, 14.32)
        self.assertEqual([477, 478, 477], [post("rebate", "X", 5_100_000, "0.0337", d) for d in ("2026-10-16", "2026-10-17", "2026-10-18")])
        # a zero rate (T3 rebate) accrues nothing but still advances the segment
        self.assertEqual(0, post("rebate", "Y", 5_100_000, "0", "2026-10-16"))
        self.assertEqual("2026-10-16", sc.one(rt, "SELECT accrued_through FROM accrual_state WHERE kind='rebate' AND ref_id='Y'")["accrued_through"])

    # ---- BORROW_CLEANUP: a partly filled, then cancelled short returns the unused quantity -------------------------------------------
    def test_cleanup_returns_unused_borrow(self):
        rt = self.start()
        fund(rt, US_EXCHANGE, 20_000_000)
        sh.add_equity(rt, "GC1", 5_000_000)
        sh.finra(rt, "GC1", [("2026-10-15", 30_000, 100_000)])
        sh.quote(rt, "GC1", "50.00", volume=2_000)                                   # cap 10 % x 2,000 = 200 shares this pass
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="GC1", side="SELL_SHORT", qty=1000, order_type="MARKET")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("PARTIAL", 200), (r.body["order"]["status"], r.body["order"]["filled_qty"]))
        # the borrow covers the whole order (1,000, recorded at submit with no collateral); the fill 200 @ 50: gross 10,000.00, fee max(1.00, 1.00) = 1.00,
        # posts the collateral of the 200 sold: to_c(1.02 x 200 x 50) = 10,200.00 = proceeds 10,000 - 1 = 9,999.00 + cash 201.00 (2 % excess 200.00 + fee 1.00)
        self.assertEqual([], sh.lines_of(rt, "collat:B-000001:open"))
        self.assertEqual([("1400", 1_020_000, "GC1"), ("1200", -999_900, None), ("1010", -20_100, None)], sh.lines_of(rt, "collat:B-000001:fill:F-000001"))
        self.assertEqual((10_000_00, 100), (r.body["fills"][0]["gross_c"], r.body["fills"][0]["fee_c"]))
        self.assertEqual(lending.REASON_PENDING, sh.borrow_row(rt, "B-000001")["reason"])   # 800 still pend: the reason stays after a PARTIAL fill
        with rt.db.reader() as conn:
            self.assertEqual([], lending.cleanup_due(conn))                             # excess = 1,000 - 200 short - 800 still working = 0
        assert_integrity(self, rt, "full")
        # cancel the remainder: filled_qty > 0 so the cancel itself returns nothing; the cleanup finds 800 free
        r = execute(rt, "order.cancel", {"order_id": "O-000001"})
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("OPEN", 0, lending.REASON_PENDING), tuple(sh.borrow_row(rt, "B-000001")[k] for k in ("status", "qty_returned", "reason")))
        with rt.db.reader() as conn:
            due = lending.cleanup_due(conn)
        self.assertEqual([("B-000001", 800, "CANCELLED")], [(c["borrow_id"], c["qty"], c["order_status"]) for c in due])
        n0 = sh.n_entries(rt)
        r = execute(rt, "credit.borrow_cleanup", {"date": "2026-10-16", "borrow_id": "B-000001"}, request_id="job:cleanup:B-000001:2026-10-16", actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        ret = r.body["returns"][0]
        # 800 of 1,000 back: never sold, so they carry no collateral (collateralised quantity min(200 short, 1,000 - 0 pending) = 200 before and
        # min(200, 200) = 200 after: nothing back, no entry); no accruals yet so nothing to settle; 200 stay borrowed with their 10,200.00
        self.assertEqual((800, 0, 0, 0, False, 200), (ret["qty"], ret["collateral_returned_c"], ret["fees_paid_c"], ret["rebate_received_c"], ret["closed"], ret["outstanding"]))
        self.assertEqual([], sh.lines_of(rt, "collat:B-000001:return:cleanup:2026-10-16"))
        self.assertEqual(n0, sh.n_entries(rt))
        b = sh.borrow_row(rt, "B-000001")
        self.assertEqual(("OPEN", 800, 1_020_000, None), (b["status"], b["qty_returned"], b["collateral_c"], b["reason"]))   # the order ended: the short backs the 200
        self.assertEqual(1_020_000, bal(rt, US_EXCHANGE, "1400", "USD", "GC1"))        # L13: 1.02 x 200 x 50 = 10,200.00
        assert_integrity(self, rt, "full")
        # a second cleanup finds nothing; a manual return of what still backs the short is BACKS_SHORT
        r = execute(rt, "credit.borrow_cleanup", {"date": "2026-10-16"}, actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        self.assertEqual([], r.body["returns"])
        r = execute(rt, "credit.return_stock", {"borrow_id": "B-000001"})
        self.assertEqual((False, 409, "BACKS_SHORT"), (r.ok, r.http, r.code))
        # covering the 200 returns the rest in the fill's transaction: collateral 10,200.00 back, borrow CLOSED
        sh.quote(rt, "GC1", "50.00", volume=100_000)
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="GC1", side="BUY_TO_COVER", qty=200, order_type="MARKET")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("FILLED", 0), (r.body["order"]["status"], r.body["fills"][0]["realised_c"]))
        self.assertEqual([("1010", 1_020_000, None), ("1400", -1_020_000, "GC1")], sh.lines_of(rt, "collat:B-000001:return:F-000002"))
        self.assertEqual(("CLOSED", 1000, 0), (sh.borrow_row(rt, "B-000001")["status"], sh.borrow_row(rt, "B-000001")["qty_returned"], sh.borrow_row(rt, "B-000001")["collateral_c"]))
        self.assertEqual(0, bal(rt, US_EXCHANGE, "1400", "USD", "GC1"))
        assert_integrity(self, rt, "full")

    def test_cancel_unfilled_short_releases_borrow(self):
        rt = self.start()
        fund(rt, US_EXCHANGE, 20_000_000)
        sh.add_equity(rt, "GC1", 5_000_000)
        sh.finra(rt, "GC1", [("2026-10-15", 30_000, 100_000)])
        sh.quote(rt, "GC1", "50.00")
        # a sell limit above the last rests; the borrow is located and recorded at submit with no collateral (the 102 % goes with a fill)
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="GC1", side="SELL_SHORT", qty=500, order_type="LIMIT", limit_px="60.00")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("WORKING", 0, "B-000001"), (r.body["order"]["status"], r.body["order"]["filled_qty"], r.body["order"]["borrow_id"]))
        self.assertEqual([], sh.lines_of(rt, "collat:B-000001:open"))
        self.assertEqual((20_000_000, 0, 0), (bal(rt, US_EXCHANGE, "1010"), bal(rt, US_EXCHANGE, "1400", "USD", "GC1"), sh.borrow_row(rt, "B-000001")["collateral_c"]))
        # the working short reserves the cash its fill will take: 1.02 x 500 x 50.00 = 25,500.00 less the proceeds 25,000.00 - fee
        # max(500 x 0.005, 1.00) = 2.50 -> 24,997.50 = 502.50 (2 % excess 500.00 + fee 2.50)
        self.assertEqual(50_250, rt.accounting.cash(US_EXCHANGE)["USD"].reserved_c)
        self.assertEqual(lending.REASON_PENDING, sh.borrow_row(rt, "B-000001")["reason"])
        assert_integrity(self, rt, "full")                                             # L12: an open borrow with a reason
        with rt.db.reader() as conn:
            self.assertEqual([], lending.cleanup_due(conn))                              # the live order still needs it
        # cancel -> the whole borrow goes back at once; nothing was sold, so no collateral moves (no collat:{borrow}:return:cancel:{order} entry)
        n0 = sh.n_entries(rt)
        r = execute(rt, "order.cancel", {"order_id": "O-000001"})
        self.assertTrue(r.ok, r.body)
        self.assertEqual([], sh.lines_of(rt, "collat:B-000001:return:cancel:O-000001"))
        self.assertEqual(n0, sh.n_entries(rt))
        self.assertEqual(("CLOSED", 500, 0, None), tuple(sh.borrow_row(rt, "B-000001")[k] for k in ("status", "qty_returned", "collateral_c", "reason")))
        self.assertEqual((20_000_000, 0), (bal(rt, US_EXCHANGE, "1010"), bal(rt, US_EXCHANGE, "1400", "USD", "GC1")))
        assert_integrity(self, rt, "full")

    # ---- recall notices: INFO only, never a recall or a buy-in ----------------------------------------------------------------------
    def test_recall_notice_after_three_sessions(self):
        rt = self.start()
        sh.setup_abc(rt)
        sh.short_abc(self, rt)
        # the three most recent FINRA sessions up to Fri 10-16 (Wed 14, Thu 15, Fri 16) all >= 0.80 -> one INFO item
        sh.finra(rt, "ABC", [("2026-10-14", 85_000, 100_000), ("2026-10-15", 90_000, 100_000), ("2026-10-16", 82_000, 100_000)])
        r = execute(rt, "credit.recall_check", {}, actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        self.assertEqual([(US_EXCHANGE, "ABC", True)], [(n["account_id"], n["instrument_id"], n.get("opened")) for n in r.body["notices"]])
        self.assertEqual([{"day": "2026-10-16", "ratio": "0.82"}, {"day": "2026-10-15", "ratio": "0.9"}, {"day": "2026-10-14", "ratio": "0.85"}], r.body["notices"][0]["sessions"])
        items = sc.q(rt, "SELECT severity, source, code, message, account_id, ref_id, resolved_ts FROM attention WHERE code='HTB_TIGHTENING'")
        self.assertEqual(1, len(items))
        self.assertEqual(("INFO", "lending", US_EXCHANGE, "ABC", None), (items[0]["severity"], items[0]["source"], items[0]["account_id"], items[0]["ref_id"], items[0]["resolved_ts"]))
        self.assertIn(lending.HTB_MESSAGE, items[0]["message"])
        self.assertIn("hard-to-borrow tightening", sh.borrow_row(rt, "B-000001")["recall_notice"])
        # idempotent: a second check opens nothing new
        r = execute(rt, "credit.recall_check", {}, actor="lifecycle")
        self.assertEqual([], r.body["notices"])
        self.assertEqual(1, len(sc.q(rt, "SELECT 1 FROM attention WHERE code='HTB_TIGHTENING'")))
        # the ratio falls back -> the item resolves and the note clears
        sh.finra(rt, "ABC", [("2026-10-16", 50_000, 100_000)])
        r = execute(rt, "credit.recall_check", {}, actor="lifecycle")
        self.assertEqual([True], [n.get("resolved") for n in r.body["notices"]])
        self.assertIsNotNone(sc.one(rt, "SELECT resolved_ts FROM attention WHERE code='HTB_TIGHTENING'")["resolved_ts"])
        self.assertIsNone(sh.borrow_row(rt, "B-000001")["recall_notice"])
        # two tight sessions that are not consecutive with the third (Wed missing) -> no notice
        with rt.db.writer() as tx:
            tx.conn.execute("DELETE FROM finra_short WHERE symbol='ABC' AND day='2026-10-14'")
        sh.finra(rt, "ABC", [("2026-10-16", 82_000, 100_000)])
        r = execute(rt, "credit.recall_check", {}, actor="lifecycle")
        self.assertEqual([], r.body["notices"])
        self.assertEqual(1, len(sc.q(rt, "SELECT 1 FROM attention WHERE code='HTB_TIGHTENING'")))
        # nothing else ever happened: no buy-in line, no synthetic fill or order, the borrow still open
        self.assertEqual(0, len(sc.q(rt, "SELECT 1 FROM journal_lines WHERE gl='5700'")))
        self.assertEqual((1, 1, "OPEN"), (len(sc.q(rt, "SELECT 1 FROM fills")), len(sc.q(rt, "SELECT 1 FROM orders")), sh.borrow_row(rt, "B-000001")["status"]))
        assert_integrity(self, rt, "full")

    # ---- manual borrows, the sweep for collateral, a late accrual on a closed borrow ---------------------------------------------------
    def test_manual_borrow_sweep_and_late_accrual(self):
        rt = self.start()
        sh.add_equity(rt, "GC1", 5_000_000)
        sh.finra(rt, "GC1", [("2026-10-15", 30_000, 100_000)])
        rt.marks.set("GC1", "50.00")
        # the account holds no cash: the 102 % collateral 1.02 x 500 x 50 = 25,500.00 arrives as ONE sweep linked to the borrow
        r = execute(rt, "credit.borrow_stock", {"account_id": US_EXCHANGE, "instrument_id": "GC1", "qty": 500})
        self.assertTrue(r.ok, r.body)
        b = r.body["borrow"]
        self.assertEqual(("B-000001", 500, 500, lending.REASON_MANUAL, 2_550_000, "GC", None), (b["borrow_id"], b["qty"], b["outstanding"], b["reason"], b["collateral_c"], b["tier"], b["order_status"]))
        self.assertEqual((2_550_000, "borrow", "B-000001", 2_550_000), (r.body["collateral_transfer"]["amount_c"], r.body["collateral_transfer"]["linked_type"],
                                                                        r.body["collateral_transfer"]["linked_id"], r.body["collateral_transfer"]["shortfall_c"]))
        self.assertEqual((0, 2_550_000), (bal(rt, US_EXCHANGE, "1010"), bal(rt, US_EXCHANGE, "1400", "USD", "GC1")))
        self.assertEqual(10_000_000_00 - 2_550_000, bal(rt, TREASURY, "1010"))
        with rt.db.reader() as conn:
            self.assertEqual([], lending.cleanup_due(conn))                              # manual pre-borrows are never cleaned up automatically
        assert_integrity(self, rt, "full")                                             # L12 (reason set), L13, L19 (sweep linked to the borrow)
        # a locate that scales: 80,000 asked of a T3 name with ADV 8,000 -> borrow 800
        sh.add_equity(rt, "T3N", 8_000)
        rt.marks.set("T3N", "10.00")
        fund(rt, US_EXCHANGE, 1_000_000)
        r = execute(rt, "credit.borrow_stock", {"account_id": US_EXCHANGE, "instrument_id": "T3N", "qty": 80_000})
        self.assertTrue(r.ok, r.body)
        self.assertEqual((800, 80_000, 816_000), (r.body["borrow"]["qty"], r.body["scaled_from"], r.body["borrow"]["collateral_c"]))
        r = execute(rt, "credit.return_stock", {"borrow_id": "B-000002"})
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("CLOSED", 816_000), (r.body["borrow"]["status"], r.body["return"]["collateral_returned_c"]))
        # Sat 10-17 10:00: return the GC1 borrow -> closed on the Sat accounting day, so its last night is Fri 10-16
        rt.clock.set("2026-10-17T10:00:00-04:00")
        r = execute(rt, "credit.return_stock", {"borrow_id": "B-000001"}, request_id="ui:return-gc1-0001")
        self.assertTrue(r.ok, r.body)
        self.assertEqual([("1010", 2_550_000, None), ("1400", -2_550_000, "GC1")], sh.lines_of(rt, "collat:B-000001:return:manual:ui:return-gc1-0001"))
        self.assertEqual((0, 0), (r.body["return"]["fees_paid_c"], r.body["return"]["rebate_received_c"]))   # nothing had accrued yet
        with rt.db.reader() as conn:
            self.assertEqual(["2026-10-16"], lending.nights_due(conn, sh.borrow_row(rt, "B-000001"), date(2026, 10, 17)))
            self.assertEqual([], lending.nights_due(conn, sh.borrow_row(rt, "B-000002"), date(2026, 10, 17)))    # opened and closed the same day
        # the late accrual for Fri on the CLOSED borrow: fee to_c(500 x 50.00 x 0.003 / 360) = to_c(0.208333) = 0.21; rebate to_c(25,500 x 0.0337 / 360)
        # = to_c(2.387083) = 2.39; both settle at once (pay:bfee:{borrow}:late:{night}), so 2310 / 1410 end at 0
        sh.close_mark(rt, "GC1", "2026-10-16", "50.00")
        n0 = sh.n_entries(rt)
        done = sh.accrue_due(rt, "2026-10-16")
        self.assertEqual([("B-000001", "2026-10-16", 21, 239)], [(b, d, body["fee_c"], body["rebate_c"]) for b, d, body in done])
        self.assertEqual([("2310", 21, "GC1"), ("1010", -21, None), ("1010", 239, None), ("1410", -239, "GC1")], sh.lines_of(rt, "pay:bfee:B-000001:late:2026-10-16"))
        self.assertEqual(n0 + 3, sh.n_entries(rt))
        self.assertEqual((0, 0, 21, 239), (bal(rt, US_EXCHANGE, "2310", "USD", "GC1"), bal(rt, US_EXCHANGE, "1410", "USD", "GC1"),
                                           bal(rt, US_EXCHANGE, "5300", "USD", "GC1"), bal(rt, US_EXCHANGE, "4350", "USD", "GC1")))
        # settled: 25,500 back + 10,000 funding - 8,160 + 8,160 (T3N in and out) - 0.21 + 2.39 = 35,502.18
        self.assertEqual(3_550_218, bal(rt, US_EXCHANGE, "1010"))
        self.assertEqual([], sh.accrue_due(rt, "2026-10-16"))
        # a night the borrow was not open for is refused; a night without a mark is refused (never defaulted)
        r = execute(rt, "credit.accrue_borrow", {"borrow_id": "B-000001", "night": "2026-10-17"}, actor="lifecycle")
        self.assertEqual((False, 400, "NIGHT_INVALID"), (r.ok, r.http, r.code))
        sh.add_equity(rt, "GC2", 5_000_000)
        sh.finra(rt, "GC2", [("2026-10-15", 30_000, 100_000)])
        rt.marks.set("GC2", "20.00")
        r = execute(rt, "credit.borrow_stock", {"account_id": US_EXCHANGE, "instrument_id": "GC2", "qty": 100})
        self.assertTrue(r.ok, r.body)
        rt.clock.set("2026-10-18T00:35:00-04:00")
        r = execute(rt, "credit.accrue_borrow", {"borrow_id": "B-000003", "night": "2026-10-17"}, actor="lifecycle")
        self.assertEqual((False, 400, "MARK_UNAVAILABLE"), (r.ok, r.http, r.code))
        # ... and without a SOFR print within 10 days of the night the rebate is refused, not defaulted
        sh.close_mark(rt, "GC2", "2026-10-16", "20.00")
        rt.rates = sh.RateStore()
        rt.rates.put("SOFR", [(date(2026, 10, 1), D("3.87"))], persist=False)
        r = execute(rt, "credit.accrue_borrow", {"borrow_id": "B-000003", "night": "2026-10-17"}, actor="lifecycle")
        self.assertEqual((False, 400, "RATE_UNAVAILABLE"), (r.ok, r.http, r.code))
        assert_integrity(self, rt, "full")

    # ---- repo and reverse repo (Build 2 P08; §13.9 made real) --------------------------------------------------------------------------
    def test_repo_and_reverse_repo_hand_worked(self):
        BOND = "UST-TEST-2027"
        # Thu 2027-04-15 10:00 (US bonds open 08:00-17:00); SOFR 3.87 % observed 2027-04-15; the fixture note: 4.500 % semi-annual, issued 2027-03-01,
        # maturing 2027-09-01 (one 184-day period), type NOTE, priced 96.1027 (computed) and dealt at its mark (half-spread 0)
        rt = self.start(at="2027-04-15T10:00:00-04:00", sofr=("2027-04-15", "3.87"))
        bonds.register(rt.bus, rt)
        rt.bonds = bonds
        rt.repo = repo
        repo.bind(rt)                                                   # the repos columns (C4) and the three jobs (C13)
        lifecycle.register(rt.bus, rt)                                  # lifecycle.settle and the lifecycle.repo_* job kinds
        with rt.db.writer() as tx:
            set_setting(tx.conn, bonds.SETTING_FLAG, True)
            bonds.ensure_bond(tx, rt.instruments, BOND, "US Treasury 4.500 % 01-Sep-2027 (test)", "4.5", date(2027, 9, 1), date(2027, 3, 1),
                              spec={"half_spread": "0", "type": "NOTE"})
        rt.marks.set(BOND, "96.1027", "computed")
        b = execute(rt, "bonds.trade", {"account_id": DEBT, "instrument_id": BOND, "side": "BUY", "qty": 5_000_000})
        self.assertTrue(b.ok, b.body)
        # the face is bought but settles Fri 04-16 (T+1): nothing can be pledged yet
        r = execute(rt, "repo.open", {"account_id": CREDIT, "side": "REPO", "counterparty_id": "PD_GS", "instrument_id": BOND, "face": 5_000_000, "term": "1W"})
        self.assertEqual((409, "COLLATERAL_SHORT", 5_000_000, 0), (r.http, r.code, r.body["error"]["details"]["pending"], r.body["error"]["details"]["free"]))
        rt.clock.set("2027-04-16T00:35:00-04:00")
        sid = sc.one(rt, "SELECT settle_id FROM settlements WHERE status='PENDING'")["settle_id"]
        self.assertTrue(execute(rt, "lifecycle.settle", {"key": sid, "settle_id": sid}, f"job:SETTLE_DUE:{sid}", actor="lifecycle").ok)
        rt.clock.set("2027-04-16T10:00:00-04:00")
        r = execute(rt, "repo.open", {"account_id": CREDIT, "side": "REPO", "counterparty_id": "PD_GS", "instrument_id": BOND, "face": 6_000_000, "term": "1W"})
        self.assertEqual((409, "COLLATERAL_SHORT", 5_000_000), (r.http, r.code, r.body["error"]["details"]["free"]))
        # REPO 5,000,000 face for 1 week with Goldman Sachs & Co. LLC (assumed repo spread 0.08 %, note haircut 2 %), Fri 04-16:
        #   accrued 46 / 184 of the 2.25 coupon = 0.5625; dirty 96.1027 + 0.5625 = 96.6652; value to_c(5,000,000 x 96.6652 / 100) = 4,833,260.00
        #   cash = to_c(4,833,260.00 x 0.98) = 4,736,594.80; rate 3.87 % + 0.08 % = 3.95 % fixed; maturity 04-16 + 7 = Fri 2027-04-23 (7 nights)
        #   interest for the term to_c(4,736,594.80 x 0.0395 x 7 / 360) = to_c(3,637.96791) = 3,637.97 -> repay 4,740,232.77
        pv = repo.preview(rt, {"account_id": CREDIT, "side": "REPO", "counterparty_id": "PD_GS", "instrument_id": BOND, "face": 5_000_000, "term": "1W"})
        self.assertEqual(("0.5625", "96.6652", 483326000, "0.02", 473659480, "0.0395", "2027-04-23", 7, 363797, 474023277),
                         (pv["collateral"]["accrued"], pv["collateral"]["dirty"], pv["collateral"]["value_c"], pv["haircut"], pv["cash_c"], pv["rate"], pv["maturity_date"],
                          pv["nights"], pv["interest_c"], pv["repay_c"]))
        r = execute(rt, "repo.open", {"account_id": CREDIT, "side": "REPO", "counterparty_id": "PD_GS", "instrument_id": BOND, "face": 5_000_000, "term": "1W"})
        self.assertTrue(r.ok, r.body)
        rid = r.body["repo_id"]
        self.assertEqual("REPO-000001", rid)
        # cash in: Dr 1010 / Cr 2500 4,736,594.80 in Credit Instruments; the face stays the Debt account's position and is PLEDGED (C27)
        self.assertEqual([("1010", 473659480, None), ("2500", -473659480, None)], sh.lines_of(rt, f"repo:{rid}:open"))
        with rt.db.reader() as conn:
            self.assertEqual((5_000_000, 5_000_000), (repo.pledged_face(conn, DEBT, BOND), sc.one(rt, "SELECT qty FROM v_positions WHERE account_id=? AND instrument_id=?", (DEBT, BOND))["qty"]))
        s = execute(rt, "bonds.trade", {"account_id": DEBT, "instrument_id": BOND, "side": "SELL", "qty": 1_000})
        self.assertEqual((409, "PLEDGED_IN_REPO"), (s.http, s.code))
        # Fri 17:20 collateral mark at 95.1027: dirty 95.6652 -> value 4,783,260.00 -> cash to_c(4,783,260.00 x 0.98) = 4,687,594.80;
        # delta 4,687,594.80 - 4,736,594.80 = -49,000.00: a margin call, Dr 2500 / Cr 1010 49,000.00 (business day 04-17: after the 17:00 close)
        rt.clock.set("2027-04-16T17:20:00-04:00")
        rt.marks.set(BOND, "95.1027", "computed")
        m = execute(rt, "lifecycle.repo_mark", {"key": f"{rid}:2027-04-16"}, f"job:REPO_COLLATERAL_MARK:{rid}:2027-04-16", actor="lifecycle")
        self.assertTrue(m.ok, m.body)
        self.assertEqual((478326000, 468759480, -4900000), (m.body["collateral_value_c"], m.body["required_c"], m.body["delta_c"]))
        self.assertEqual([("2500", 4900000, None), ("1010", -4900000, None)], sh.lines_of(rt, f"rmark:{rid}:2027-04-16"))
        # night 04-16 accrues on the cash standing at that 17:00 close (4,736,594.80, before the mark):
        #   to_c(4,736,594.80 x 0.0395 / 360) = to_c(519.709707) = 519.71 (Dr 5500 / Cr 2500)
        rt.clock.set("2027-04-17T00:35:00-04:00")
        a = execute(rt, "lifecycle.repo_accrue", {"key": f"{rid}:2027-04-16"}, f"job:REPO_ACCRUE:{rid}:2027-04-16", actor="lifecycle")
        self.assertTrue(a.ok, a.body)
        self.assertEqual([("5500", 51971, None), ("2500", -51971, None)], sh.lines_of(rt, f"accr:repo:{rid}:2027-04-16"))
        # night 04-17 on the re-sized cash 4,687,594.80 (a new segment): to_c(4,687,594.80 x 0.0395 / 360) = to_c(514.333318) = 514.33
        rt.clock.set("2027-04-18T00:35:00-04:00")
        a = execute(rt, "lifecycle.repo_accrue", {"key": f"{rid}:2027-04-17"}, f"job:REPO_ACCRUE:{rid}:2027-04-17", actor="lifecycle")
        self.assertEqual(51433, a.body["amount_c"])
        # Mon 04-19 10:00 closed early: night 04-18 is posted first (cum n=2 to_c(1,028.666637) = 1,028.67 -> 514.34); interest owed
        # 519.71 + 514.33 + 514.34 = 1,548.38; repay principal 4,687,594.80 + interest 1,548.38 = 4,689,143.18 with 4,687,594.80 in the account:
        # one Treasury sweep-in of 1,548.38
        rt.clock.set("2027-04-19T10:00:00-04:00")
        with rt.db.reader() as conn:
            v = repo.repo_view(rt, conn, repo.repo_row(conn, rid))
        self.assertEqual((468759480, 103404, 154838, [("2027-04-18", 51434)], 468914318), (v["principal_c"], v["interest_posted_c"], v["interest_owed_c"],
                                                                                             [(n["night"], n["amount_c"]) for n in v["pending_nights"]], v["payoff_c"]))
        c = execute(rt, "repo.close", {"repo_id": rid})
        self.assertTrue(c.ok, c.body)
        self.assertEqual((468759480, 154838, ["2027-04-18"]), (c.body["principal_c"], c.body["interest_c"], [n["night"] for n in c.body["accrued_nights"]]))
        self.assertEqual([("2500", 468759480, None), ("1010", -468759480, None)], sh.lines_of(rt, f"repo:{rid}:close"))
        self.assertEqual([("2500", 154838, None), ("1010", -154838, None)], sh.lines_of(rt, f"repo:{rid}:close:interest"))
        self.assertEqual((0, 154838, 0), (bal(rt, CREDIT, "2500"), bal(rt, CREDIT, "5500"), bal(rt, CREDIT, "1010")))
        self.assertEqual([("SWEEP_IN", 154838, "repo", rid)], [(x["kind"], x["amount_c"], x["linked_type"], x["linked_id"]) for x in sc.q(rt, "SELECT * FROM transfers WHERE to_account=?", (CREDIT,))])
        with rt.db.reader() as conn:
            self.assertEqual(0, repo.pledged_face(conn, DEBT, BOND))
        # REVERSE repo Mon 04-19 10:30: lend cash overnight to J.P. Morgan Securities LLC (assumed reverse spread 0.04 %) against 1,000,000 face of the
        # same note the dealer delivers: accrued 49 / 184 of 2.25 = 0.59918... -> 0.5992; dirty 95.1027 + 0.5992 = 95.7019; value 957,019.00;
        # cash to_c(957,019.00 x 0.98) = 937,878.62 lent (Dr 1500 / Cr 1010, one sweep-in of 937,878.62); rate 3.87 - 0.04 = 3.83 %; matures Tue 04-20
        rt.clock.set("2027-04-19T10:30:00-04:00")
        r = execute(rt, "repo.open", {"account_id": CREDIT, "side": "REVERSE", "counterparty_id": "PD_JPM", "instrument_id": BOND, "face": 1_000_000, "term": "ON"})
        self.assertTrue(r.ok, r.body)
        rid2 = r.body["repo_id"]
        self.assertEqual(("0.5992", 95701900, 93787862, "0.0383", "2027-04-20"), (r.body["preview"]["collateral"]["accrued"], r.body["preview"]["collateral"]["value_c"],
                                                                                  r.body["preview"]["cash_c"], r.body["preview"]["rate"], r.body["preview"]["maturity_date"]))
        self.assertEqual([("1500", 93787862, None), ("1010", -93787862, None)], sh.lines_of(rt, f"repo:{rid2}:open"))
        # the maturity job Tue 04-20 15:05: night 04-19 first, to_c(937,878.62 x 0.0383 / 360) = to_c(99.779863) = 99.78, then the cash and the interest come back
        rt.clock.set("2027-04-20T15:05:00-04:00")
        mt = execute(rt, "lifecycle.repo_maturity", {"key": f"{rid2}:2027-04-20"}, f"job:REPO_MATURITY:{rid2}:2027-04-20", actor="lifecycle")
        self.assertTrue(mt.ok, mt.body)
        self.assertEqual(("DONE", 93787862, 9978), (mt.body["status"], mt.body["principal_c"], mt.body["interest_c"]))
        self.assertEqual([("1500", 9978, None), ("4500", -9978, None)], sh.lines_of(rt, f"accr:repo:{rid2}:2027-04-19"))
        self.assertEqual([("1010", 9978, None), ("1500", -9978, None)], sh.lines_of(rt, f"repo:{rid2}:close:interest"))
        self.assertEqual((0, 9978, 93797840), (bal(rt, CREDIT, "1500"), bal(rt, CREDIT, "4500"), bal(rt, CREDIT, "1010")))
        self.assertEqual(("CLOSED", "CLOSED"), (sc.one(rt, "SELECT status FROM repos WHERE repo_id=?", (rid,))["status"], sc.one(rt, "SELECT status FROM repos WHERE repo_id=?", (rid2,))["status"]))
        assert_integrity(self, rt, "full")

    # ---- securities lending out (Build 2 P08): needs the ledger's securities-lending codes ---------------------------------------------------
    @unittest.skipUnless(lending.ledger_ready()[0], "lending out waits for the ledger's securities-lending codes (requested from the accounting package)")
    def test_lend_out_fee_rebate_mark_recall_hand_worked(self):
        # Thu 10-15 10:00: buy 1,000 ABC at 50.00 (settles Fri 10-16); ABC tier GC (FINRA ratio 0.30, ADV 5,000,000)
        rt = self.start(at="2026-10-15T10:00:00-04:00", sofr=("2026-10-14", "3.87"))
        lifecycle.register(rt.bus, rt)
        sh.setup_abc(rt, fund_c=10000000)
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="ABC", side="BUY", qty=1000, order_type="MARKET")
        self.assertEqual("FILLED", r.body["order"]["status"], r.body)
        lend = {"account_id": US_EXCHANGE, "instrument_id": "ABC", "qty": 1000, "counterparty_id": "AGENT_BNY"}
        x = execute(rt, "credit.lend_securities", lend)
        self.assertEqual((409, "NOT_ENOUGH_FREE", 1000), (x.http, x.code, x.body["error"]["details"]["unsettled"]))   # bought, not yet settled
        rt.clock.set("2026-10-16T00:35:00-04:00")
        self.assertTrue(execute(rt, "test.settle_due", {"as_of": "2026-10-16"}).ok)
        # Fri 10:00 lend 1,000 ABC through BNY (assumed: your share 85 % of the fee, rebate SOFR - 0.50 %): tier GC fee 0.30 % -> yours
        # 0.30 % x 0.85 = 0.255 %; cash collateral to_c(1.02 x 1,000 x 50.00) = 51,000.00 received (Dr 1010 / Cr 2520); rebate 3.87 - 0.50 = 3.37 %
        rt.clock.set("2026-10-16T10:00:00-04:00")
        sh.quote(rt, "ABC", "50.00")
        x = execute(rt, "credit.lend_securities", lend)
        self.assertTrue(x.ok, x.body)
        lid = x.body["loan_id"]
        self.assertEqual(("GC", "0.003", "0.85", "0.00255", 5100000, "0.0337"), (x.body["preview"]["tier"], x.body["preview"]["fee_rate"], x.body["preview"]["owner_split"],
                                                                               x.body["preview"]["own_fee_rate"], x.body["preview"]["collateral_c"], x.body["preview"]["rebate_rate"]))
        self.assertEqual([("1010", 5100000, None), ("2520", -5100000, "ABC")], sh.lines_of(rt, f"seclend:{lid}:open"))
        with rt.db.reader() as conn:
            self.assertEqual(1000, lending.lent_qty(conn, US_EXCHANGE, "ABC"))
        s = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="ABC", side="SELL", qty=10, order_type="MARKET")        # C27: recall first
        self.assertEqual((400, "SHARES_ON_LOAN"), (s.http, s.body["error"]["code"]))
        # Fri 17:10 mark at the CLOSE 50.00: 102 % = 51,000.00, no movement; Mon 17:15 at the CLOSE 52.00: to_c(1.02 x 1,000 x 52.00) = 53,040.00,
        # +2,040.00 more collateral in (Dr 1010 / Cr 2520; business day 10-19: FINSIM1_PLAN 15.13 POSTCLOSE-06, the evening mark of D is dated D,
        # like the stock-borrow mark - before 1.0.5 it took the accounting day 10-20)
        sh.close_mark(rt, "ABC", "2026-10-16", "50.00")
        rt.clock.set("2026-10-16T17:12:00-04:00")
        mk = execute(rt, "lifecycle.seclend_mark", {"key": f"{lid}:2026-10-16"}, f"job:SECLEND_COLLATERAL_MARK:{lid}:2026-10-16", actor="lifecycle")
        self.assertEqual((True, 0), (mk.ok, mk.body.get("delta_c")))
        # night 10-16 (Sat 00:35): fee to_c(50,000.00 x 0.00255 / 360) = to_c(0.354167) = 0.35 (Dr 1420 / Cr 4360); rebate on the collateral at
        # that close to_c(51,000.00 x 0.0337 / 360) = to_c(4.774167) = 4.77 (Dr 5350 / Cr 2420)
        rt.clock.set("2026-10-17T00:35:00-04:00")
        a = execute(rt, "lifecycle.seclend_accrue", {"key": f"{lid}:2026-10-16"}, f"job:SECLEND_ACCRUE:{lid}:2026-10-16", actor="lifecycle")
        self.assertEqual((True, 35, 477), (a.ok, a.body.get("fee_c"), a.body.get("rebate_c")))
        self.assertEqual([("1420", 35, "ABC"), ("4360", -35, "ABC")], sh.lines_of(rt, f"accr:slfee:{lid}:2026-10-16"))
        self.assertEqual([("5350", 477, "ABC"), ("2420", -477, "ABC")], sh.lines_of(rt, f"accr:slreb:{lid}:2026-10-16"))
        sh.close_mark(rt, "ABC", "2026-10-19", "52.00")
        rt.clock.set("2026-10-19T17:15:00-04:00")
        mk = execute(rt, "lifecycle.seclend_mark", {"key": f"{lid}:2026-10-19"}, f"job:SECLEND_COLLATERAL_MARK:{lid}:2026-10-19", actor="lifecycle")
        self.assertEqual((True, 204000), (mk.ok, mk.body.get("delta_c")))
        # Tue 10-20 10:00 recall: nights 10-17 .. 10-19 first -
        #   fee (CLOSE 50.00 carried Sat / Sun): cum 2 to_c(0.708333) = 0.71 -> 0.36; cum 3 to_c(1.0625) = 1.06 -> 0.35; night 10-19 at the CLOSE 52.00
        #   restarts the segment: to_c(52,000.00 x 0.00255 / 360) = to_c(0.368333) = 0.37 -> fee total 0.35 + 0.36 + 0.35 + 0.37 = 1.43
        #   rebate on 51,000.00 for nights 10-17 / 10-18: cum 2 to_c(9.548333) = 9.55 -> 4.78; cum 3 to_c(14.3225) = 14.32 -> 4.77; night 10-19 on the
        #   collateral at the close of 10-19, 53,040.00 (the Mon 17:15 mark of 10-19 is dated 10-19, POSTCLOSE-06), restarts the segment:
        #   to_c(53,040.00 x 0.0337 / 360) = to_c(4.965133) = 4.97 -> rebate total 4.77 + 4.78 + 4.77 + 4.97 = 19.29
        #   (it was 19.10, with night 10-19 on 51,000.00, while the mark took business date 10-20); collateral 53,040.00 back to the borrower;
        #   fee 1.43 received, rebate 19.29 paid
        rt.clock.set("2026-10-20T10:00:00-04:00")
        rc = execute(rt, "credit.recall_securities", {"loan_id": lid})
        self.assertTrue(rc.ok, rc.body)
        self.assertEqual((1000, True, 5304000, 143, 1929), (rc.body["qty"], rc.body["closed"], rc.body["collateral_returned_c"], rc.body["fee_received_c"], rc.body["rebate_paid_c"]))
        self.assertEqual((0, 0, 0, 143, 1929), (bal(rt, US_EXCHANGE, "2520", "USD", "ABC"), bal(rt, US_EXCHANGE, "1420", "USD", "ABC"), bal(rt, US_EXCHANGE, "2420", "USD", "ABC"),
                                                 bal(rt, US_EXCHANGE, "4360", "USD", "ABC"), bal(rt, US_EXCHANGE, "5350", "USD", "ABC")))
        with rt.db.reader() as conn:
            self.assertEqual(0, lending.lent_qty(conn, US_EXCHANGE, "ABC"))
        assert_integrity(self, rt, "full")


if __name__ == "__main__":
    unittest.main()
