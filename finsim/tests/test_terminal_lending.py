"""WP-06 lending unit tests (TERMINAL_DESIGN.md §12.2 tiers, §11.2 BORROW_CLEANUP, §11.3 accruals, §13.1, §13.9 repo stub).
Every expected number is worked by hand in a comment.  Scaffolding comes from test_terminal_shorts (imported as a
module so its cases are not loaded twice)."""
from __future__ import annotations

import shutil
import tempfile
import types
import unittest
from datetime import date
from decimal import Decimal

from tests.terminal_helpers import TREASURY, US_EXCHANGE, assert_integrity, bal, execute, fund
from tests import test_terminal_orders as sc
from tests import test_terminal_shorts as sh
from finsim.terminal import lending, repo, tables
from finsim.terminal.clock import epoch_of
from finsim.terminal.ledger import Line

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
        self.assertEqual(lending.MODELLED_LENDER, g.lender)
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
        # the borrow covers the whole order: 1,000 at collateral to_c(1.02 x 1,000 x 50) = 51,000.00; the fill 200 @ 50: gross 10,000.00, fee max(1.00, 1.00) = 1.00
        self.assertEqual([("1400", 5_100_000, "GC1"), ("1010", -5_100_000, None)], sh.lines_of(rt, "collat:B-000001:open"))
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
        # 800 of 1,000 back: collateral to_c(51,000 x 800 / 1,000) = 40,800.00; no accruals yet so nothing to settle; 200 stay borrowed with 10,200.00
        self.assertEqual((800, 4_080_000, 0, 0, False, 200), (ret["qty"], ret["collateral_returned_c"], ret["fees_paid_c"], ret["rebate_received_c"], ret["closed"], ret["outstanding"]))
        self.assertEqual([("1010", 4_080_000, None), ("1400", -4_080_000, "GC1")], sh.lines_of(rt, "collat:B-000001:return:cleanup:2026-10-16"))
        self.assertEqual(n0 + 1, sh.n_entries(rt))
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
        # a sell limit above the last rests; the borrow is opened at submit on the mark: 1.02 x 500 x 50.00 = 25,500.00
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="GC1", side="SELL_SHORT", qty=500, order_type="LIMIT", limit_px="60.00")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("WORKING", 0, "B-000001"), (r.body["order"]["status"], r.body["order"]["filled_qty"], r.body["order"]["borrow_id"]))
        self.assertEqual([("1400", 2_550_000, "GC1"), ("1010", -2_550_000, None)], sh.lines_of(rt, "collat:B-000001:open"))
        self.assertEqual(lending.REASON_PENDING, sh.borrow_row(rt, "B-000001")["reason"])
        assert_integrity(self, rt, "full")                                             # L12: an open borrow with a reason
        with rt.db.reader() as conn:
            self.assertEqual([], lending.cleanup_due(conn))                              # the live order still needs it
        # cancel -> the whole borrow goes back at once (collat:{borrow}:return:cancel:{order})
        n0 = sh.n_entries(rt)
        r = execute(rt, "order.cancel", {"order_id": "O-000001"})
        self.assertTrue(r.ok, r.body)
        self.assertEqual([("1010", 2_550_000, None), ("1400", -2_550_000, "GC1")], sh.lines_of(rt, "collat:B-000001:return:cancel:O-000001"))
        self.assertEqual(n0 + 1, sh.n_entries(rt))
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

    # ---- repo: Build 2 stub ------------------------------------------------------------------------------------------------------
    def test_repo_not_in_this_build(self):
        rt = self.start()
        n0 = sh.n_entries(rt)
        for kind in ("repo.open", "repo.close", "repo.accrue"):
            r = execute(rt, kind, {"account_id": US_EXCHANGE, "side": "REPO", "instrument_id": "UST-10Y", "face": 1_000_000, "term_days": 7})
            self.assertEqual((False, 400, "NOT_IN_THIS_BUILD"), (r.ok, r.http, r.code), kind)
            self.assertEqual(2, r.body["error"]["details"]["spec"]["build"])
        self.assertEqual(n0, sh.n_entries(rt))
        self.assertEqual((2, "0.0005", "0.02"), (repo.BUILD2_SPEC["build"], repo.BUILD2_SPEC["rates"]["ust_spread"], repo.BUILD2_SPEC["rates"]["haircuts"]["UST"]))


if __name__ == "__main__":
    unittest.main()
