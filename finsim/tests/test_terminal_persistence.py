"""WP-08 restart persistence (TERMINAL_DESIGN.md §19.12 "Restart persistence", §19.24 D3): §19.2–19.5 run on a FILE database
with the real lifecycle jobs and a restart between EVERY step — ``db.close()``, a new runtime on the same file — after which
``cash()``, ``positions()``, lots, orders, fills, NAV, the ledger projection and ``state_version`` are identical, the initial
capital is not re-posted, ``integrity("full")`` holds and the persisted quote-cache records come back ``close`` / ``stale``,
never ``delayed``.  Every expected number is worked by hand in a comment.  (The "second server refuses" half of D3 is
``test_terminal_server`` — WP-11.)"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from decimal import Decimal

from tests.terminal_helpers import T1, TREASURY, US_EXCHANGE, bal, execute, fund
from tests import test_terminal_orders as sc
from tests import test_terminal_shorts as sh
from tests.test_terminal_lifecycle import idem_amounts, jobs_done, runtime, tick
from finsim.terminal import lifecycle
from finsim.terminal.quotes.cache import QuoteCache

SPY_BARS = [("2026-10-20", "769.32"), ("2026-10-21", "770.00"), ("2026-10-22", "771.00"), ("2026-10-23", "772.00"), ("2026-10-26", "773.00"), ("2026-10-27", "774.00")]
AAPL_BARS = [("2026-10-21", "339.50"), ("2026-10-22", "335.00"), ("2026-10-23", "345.00"), ("2026-10-26", "345.00"), ("2026-10-27", "345.00")]
ABC_BARS = [("2026-10-23", "50.00"), ("2026-10-26", "52.00"), ("2026-10-27", "51.00")]


def state(rt) -> dict:
    """Everything a restart must preserve, as plain data."""
    out = {}
    with rt.db.reader() as conn:
        for t in ("commands", "journal_entries", "journal_lines", "fills", "orders", "order_events", "transfers", "settlements", "lots", "lot_relief", "borrows", "loans",
                  "jobs_done", "nav_snapshots", "marks", "accrual_state", "attention", "events"):
            out["n_" + t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        out["gl"] = [tuple(r) for r in conn.execute("SELECT account_id, gl, currency, instrument_id, balance_c, base_c FROM gl_balances ORDER BY 1,2,3,4").fetchall()]
        out["orders"] = [tuple(r) for r in conn.execute("SELECT order_id, status, filled_qty, avg_px FROM orders ORDER BY order_id").fetchall()]
        out["fills"] = [tuple(r) for r in conn.execute("SELECT fill_id, account_id, instrument_id, side, qty, price, gross_c, fee_c, settle_date FROM fills ORDER BY fill_id").fetchall()]
        out["lots"] = [tuple(r) for r in conn.execute("SELECT lot_id, side, qty_remaining, unit_cost, cost_c FROM lots ORDER BY lot_id").fetchall()]
        out["settlements"] = [tuple(r) for r in conn.execute("SELECT settle_id, amount_c, due_date, status FROM settlements ORDER BY settle_id").fetchall()]
        out["borrows"] = [tuple(r) for r in conn.execute("SELECT borrow_id, qty, qty_returned, collateral_c, status, last_mark_date FROM borrows ORDER BY borrow_id").fetchall()]
        out["accruals"] = [tuple(r) for r in conn.execute("SELECT kind, ref_id, segment_start, base_c, rate, accrued_through, cum_c FROM accrual_state ORDER BY kind, ref_id").fetchall()]
        out["jobs"] = [tuple(r) for r in conn.execute("SELECT job, key, status FROM jobs_done ORDER BY job, key").fetchall()]
        out["init_commands"] = conn.execute("SELECT COUNT(*) FROM commands WHERE request_id='init:capital:T1'").fetchone()[0]
    out["state_version"] = rt.db.state_version()
    out["cash"] = {a: {c: v.to_dict() for c, v in rt.accounting.cash(a).items()} for a in (TREASURY, US_EXCHANGE)}
    out["positions"] = [(p.account_id, p.instrument_id, p.qty, p.cost_c, p.mv_c, p.unrealised_c) for p in rt.accounting.positions("T1:ALL")]
    out["nav"] = rt.accounting.nav("T1:ALL").nav_c
    out["treasury"] = rt.accounting.treasury(T1).to_dict()
    return out


class RestartPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-persist-")
        self.rt = None
        self.marks = {}                      # the in-memory marks are not state: the restart re-creates them as they stood

    def tearDown(self):
        if self.rt is not None:
            self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- the restart ---------------------------------------------------------------------------------------------------------------
    def boot(self, at):
        rt = runtime(self.tmp, at=at, marks=dict(self.marks) or None)
        rt.fp.set_daily("SPY", SPY_BARS)
        rt.fp.set_daily("AAPL", AAPL_BARS)
        rt.fp.set_daily("ABC", ABC_BARS)
        return rt

    def restart(self, at):
        """Close the runtime (``db.close()``) and open a new one on the same file at ``at``; the state must be identical."""
        before = state(self.rt)
        self.assertTrue(self.rt.accounting.integrity("full").ok)
        was = self.rt.clock.now()
        self.rt.close()
        self.rt = self.boot(was)                         # compared at the same clock (available cash depends on the accounting day), then moved on
        after = state(self.rt)
        self.assertEqual(before, after)
        self.rt.clock.set(at)
        self.assertEqual(1, after["init_commands"])                                                  # the init is never re-posted
        self.assertTrue(self.rt.accounting.integrity("full").ok)
        cache = QuoteCache(db=self.rt.db, instruments=self.rt.instruments, sessions=self.rt.sessions, clock=self.rt.clock)
        n = cache.load_persisted()
        for iid in ("SPY",):
            rec = cache.get(iid)
            if rec is not None:
                self.assertIn(rec.quality, ("close", "stale"), (iid, rec.quality))               # restored records are never "delayed"
        return self.rt, n

    def quote(self, iid, last, volume=120000):
        rt = self.rt
        now = rt.clock.epoch()
        rt.marks.set_quote(sc.rec(iid, last, now - 120, now, bar=(now - 180, volume)))
        rt.marks.set(iid, last)
        self.marks[iid] = (last, "delayed")

    def market(self, side, qty, iid, last, confirm=False):
        self.quote(iid, last)
        req = dict(account_id=US_EXCHANGE, instrument_id=iid, side=side, qty=qty, order_type="MARKET", tif="DAY")
        r = sc.submit(self.rt, **req)
        if confirm:
            self.assertEqual((409, "CONFIRM_REQUIRED"), (r.http, r.code))
            r = sc.submit(self.rt, request_id=r.body["request_id"], confirm_large=True, **req)
        self.assertTrue(r.ok, r.body)
        self.assertEqual("FILLED", r.body["order"]["status"])
        return r

    # ---- D3 ----------------------------------------------------------------------------------------------------------------------------
    def test_restart_between_every_step(self):
        self.rt = self.boot("2026-10-20T14:00:00-04:00")
        rt = self.rt
        A, T = US_EXCHANGE, TREASURY
        # S1 §19.2 Tue 10-20 14:00: transfer 1,000,000.00 -> A: T 9,000,000.00, A 1,000,000.00
        self.assertTrue(fund(rt, A, 100000000).ok)
        self.assertEqual((900000000, 100000000), (bal(rt, T, "1010"), bal(rt, A, "1010")))
        # S2: BUY 5,000 SPY @ 769.32 with confirm: sweep 2,846,625.00; T 6,153,375.00; A settled 3,846,625.00 = payable; NAV(ALL) 9,999,975.00
        rt, _ = self.restart("2026-10-20T14:00:00-04:00")
        self.market("BUY", 5000, "SPY", "769.32", confirm=True)
        self.assertEqual((615337500, 384662500, 384662500, 999997500), (bal(rt, T, "1010"), bal(rt, A, "1010"), bal(rt, A, "2100"), rt.accounting.nav("T1:ALL").nav_c))
        cache = QuoteCache(db=rt.db, instruments=rt.instruments, sessions=rt.sessions, clock=rt.clock)
        now = rt.clock.epoch()
        cache.put(sc.rec("SPY", "769.32", now - 120, now, bar=(now - 180, 120000)))
        self.assertEqual("delayed", cache.get("SPY").quality)                                       # live-ish while the session is open
        self.assertEqual(1, cache.persist())
        # S3 Tue 17:31: SWEEP_OUT -> A: 3,846,625 - 3,846,625 - 0 - 0 - 10,000 = -10,000 -> SKIPPED "nothing idle"; the restored SPY record is close/stale
        rt, n = self.restart("2026-10-20T17:31:00-04:00")
        self.assertEqual(1, n)
        out = lifecycle.run_now(rt, job="SWEEP_OUT")
        self.assertEqual([("T1:US_EXCHANGE:USD:2026-10-20", "SKIPPED")], [(i["key"], i["status"]) for i in out["ran"]])
        self.assertEqual(0, len(sc.q(rt, "SELECT 1 FROM transfers WHERE kind='SWEEP_OUT'")))
        # S4 Wed 00:35: SETTLE_DUE pays 3,846,625.00 from A's own cash (A 0.00); Treasury cash interest night 10-20 on 6,153,375.00 at 3.37 % = 576.02;
        # Tuesday's snapshots (5 accounts + ALL); the SPY close of 10-20 recorded (bars, "recorded late")
        rt, _ = self.restart("2026-10-21T00:35:00-04:00")
        out = tick(rt)
        self.assertEqual([], [i for i in out["ran"] if i["status"] in ("FAILED", "RETRY")])
        self.assertEqual((0, 0, [("accr:cashint:T1:USD:2026-10-20", 57602)]), (bal(rt, A, "1010"), bal(rt, A, "2100"), idem_amounts(rt, "accr:cashint:")))
        self.assertEqual(6, sc.one(rt, "SELECT COUNT(*) n FROM nav_snapshots WHERE snap_date='2026-10-20'")["n"])
        self.assertEqual("769.32", sc.one(rt, "SELECT price FROM marks WHERE instrument_id='SPY' AND mark_date='2026-10-20'")["price"])
        # S5 §19.3 Wed 10:00: BUY 1,000 AAPL @ 339.50 -> gross 339,500.00 + fee 5.00 swept in full (A had 0.00): sweep 339,505.00 linked to the fill; payable due Thu
        rt, _ = self.restart("2026-10-21T10:00:00-04:00")
        r = self.market("BUY", 1000, "AAPL", "339.50")
        self.assertEqual(33950500, r.body["sweeps"][0]["amount_c"])
        self.assertEqual((33950500, 33950500, 615337500 - 33950500), (bal(rt, A, "1010"), bal(rt, A, "2100"), bal(rt, T, "1010")))
        # S6 Thu 00:35: the payable settles (A 0.00); night 10-21 on 6,153,375 - 339,505 = 5,813,870.00: 5,813,870 x 0.0337 = 195,927.419; / 360 = 544.2428 -> 544.24 (new segment)
        rt, _ = self.restart("2026-10-22T00:35:00-04:00")
        tick(rt)
        self.assertEqual((0, [("accr:cashint:T1:USD:2026-10-20", 57602), ("accr:cashint:T1:USD:2026-10-21", 54424)]), (bal(rt, A, "1010"), idem_amounts(rt, "accr:cashint:")))
        # S7 §19.4 Thu 10:00: BUY 500 AAPL @ 335.00 -> 167,500.00 + fee 2.50 = 167,502.50 swept
        rt, _ = self.restart("2026-10-22T10:00:00-04:00")
        r = self.market("BUY", 500, "AAPL", "335.00")
        self.assertEqual((16750000, 250, 16750250), (r.body["fills"][0]["gross_c"], r.body["fills"][0]["fee_c"], r.body["sweeps"][0]["amount_c"]))
        # S8 Fri 00:35: settled; night 10-22 on 5,813,870 - 167,502.50 = 5,646,367.50: x 0.0337 = 190,282.58475; / 360 = 528.5627 -> 528.56
        rt, _ = self.restart("2026-10-23T00:35:00-04:00")
        tick(rt)
        self.assertEqual((0, 52856), (bal(rt, A, "1010"), idem_amounts(rt, "accr:cashint:")[-1][1]))
        # S9 Fri 10:00: SELL 1,200 AAPL @ 345.00: gross 414,000.00, fee 6.00, FIFO cost 339,500 + 67,000 = 406,500.00, realised +7,500.00; receivable 413,994.00 due Mon 10-26;
        # lot B 300 @ 335.00 cost 100,500.00
        rt, _ = self.restart("2026-10-23T10:00:00-04:00")
        r = self.market("SELL", 1200, "AAPL", "345.00")
        f = r.body["fills"][0]
        self.assertEqual((41400000, 600, 40650000, 750000, "2026-10-26"), (f["gross_c"], f["fee_c"], f["cost_relieved_c"], f["realised_c"], f["settle_date"]))
        self.assertEqual((300, 10050000), (rt.accounting.position(A, "AAPL").qty, rt.accounting.position(A, "AAPL").cost_c))
        # S10 §19.5 Fri 10:05: A funded 200,000.00 (T 5,646,367.50 - 200,000 = 5,446,367.50); SELL_SHORT 1,000 ABC @ 50.00: borrow B-000001 collateral 51,000.00
        # (A settled 149,000.00), fill 50,000.00 fee 5.00, receivable 49,995.00 due Mon
        rt, _ = self.restart("2026-10-23T10:05:00-04:00")
        self.marks["ABC"] = ("50.00", "delayed")
        fund(rt, A, 20000000)
        sh.add_equity(rt, "ABC", 5000000)
        sh.finra(rt, "ABC", [("2026-10-20", 20000, 100000), ("2026-10-21", 25000, 100000), ("2026-10-22", 30000, 100000)])    # ratio 0.30 on the last 3 sessions -> GC
        sh.quote(rt, "ABC", "50.00", 100000)
        sh.short_abc(self, rt)
        self.assertEqual((14900000, 5100000, 544636750), (bal(rt, A, "1010"), bal(rt, A, "1400", "USD", "ABC"), bal(rt, T, "1010")))
        # S11 Fri 17:31: SWEEP_OUT A: 149,000 - 0 - 0 - 0 - 10,000 = 139,000.00 back to Treasury (A 10,000.00).  No 17:30 tick ran on Wed or Thu, so their keys
        # are caught up now: the first (10-21) sweeps, the later dates find nothing idle — one key per calendar day
        rt, _ = self.restart("2026-10-23T17:31:00-04:00")
        out = lifecycle.run_now(rt, job="SWEEP_OUT")
        self.assertEqual([("T1:US_EXCHANGE:USD:2026-10-21", "DONE"), ("T1:US_EXCHANGE:USD:2026-10-22", "SKIPPED"), ("T1:US_EXCHANGE:USD:2026-10-23", "SKIPPED")],
                         [(i["key"], i["status"]) for i in out["ran"]])
        self.assertEqual([13900000], [t["amount_c"] for t in sc.q(rt, "SELECT amount_c FROM transfers WHERE kind='SWEEP_OUT'")])
        self.assertEqual((1000000, 544636750 + 13900000), (bal(rt, A, "1010"), bal(rt, T, "1010")))
        # S12 Mon 10-26 00:35, one tick: both receivables settle (A 10,000 + 413,994 + 49,995 = 473,989.00); Friday's ABC close 50.00 from the bars; collateral mark Fri
        # at 50.00 (no movement); the §19.5 nights Fri/Sat/Sun: fee 0.42 / 0.41 / 0.42, rebate 4.77 / 4.78 / 4.77 (2310 = 1.25, 1410 = 14.32); Treasury nights:
        # Fri 10-23 on 5,446,367.50 (the 17:31 sweep belongs to Saturday's accounting day): x 0.0337 = 183,542.58475; / 360 = 509.8405 -> 509.84;
        # Sat 10-24 on 5,585,367.50: x 0.0337 = 188,226.88475; / 360 = 522.8525 -> 522.85; Sun cum 1,045.7049 -> 1,045.70 -> 522.85; the Saturday / Sunday
        # sweep-out keys then return the settled 473,989 - 10,000 = 463,989.00 (catch-up on the current state); snapshots Fri, Sat, Sun
        rt, _ = self.restart("2026-10-26T00:35:00-04:00")
        out = tick(rt)
        self.assertEqual([], [i for i in out["ran"] if i["status"] in ("FAILED", "RETRY")])
        self.assertEqual(["SETTLED", "SETTLED"], [r["status"] for r in sc.q(rt, "SELECT status FROM settlements WHERE due_date='2026-10-26'")])
        self.assertEqual([("accr:bfee:B-000001:2026-10-23", 42), ("accr:bfee:B-000001:2026-10-24", 41), ("accr:bfee:B-000001:2026-10-25", 42)], idem_amounts(rt, "accr:bfee:"))
        self.assertEqual([("accr:rebate:B-000001:2026-10-23", 477), ("accr:rebate:B-000001:2026-10-24", 478), ("accr:rebate:B-000001:2026-10-25", 477)], idem_amounts(rt, "accr:rebate:"))
        self.assertEqual((125, 1432, "2026-10-23", 5100000), (bal(rt, A, "2310", "USD", "ABC"), bal(rt, A, "1410", "USD", "ABC"), sh.borrow_row(rt, "B-000001")["last_mark_date"],
                                                                sh.borrow_row(rt, "B-000001")["collateral_c"]))
        self.assertEqual([50984, 52285, 52285], [a for k, a in idem_amounts(rt, "accr:cashint:") if k.endswith(("10-23", "10-24", "10-25"))])
        self.assertEqual(46398900, sum(t["amount_c"] for t in sc.q(rt, "SELECT amount_c FROM transfers WHERE kind='SWEEP_OUT' AND business_date>='2026-10-26'")))
        self.assertEqual(1000000, bal(rt, A, "1010"))
        self.assertEqual(["2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24", "2026-10-25"], [r["snap_date"] for r in sc.q(rt, "SELECT snap_date FROM nav_snapshots WHERE account_id='T1:ALL' ORDER BY snap_date")])
        # S13 Mon 17:12: the Monday close 52.00 is recorded (bars) and the collateral mark calls to_c(1.02 x 1,000 x 52) = 53,040.00 (+2,040.00 from A's 10,000.00)
        rt, _ = self.restart("2026-10-26T17:12:00-04:00")
        self.marks["ABC"] = ("52.00", "delayed")
        rt.marks.set("ABC", "52.00")
        out = tick(rt)
        self.assertEqual([], [i for i in out["ran"] if i["status"] in ("FAILED", "RETRY")])
        self.assertEqual([("B-000001:2026-10-23", "DONE"), ("B-000001:2026-10-26", "DONE")], [(r["key"], r["status"]) for r in jobs_done(rt, "COLLATERAL_MARK")])
        self.assertEqual((5304000, 5304000, 1000000 - 204000), (sh.borrow_row(rt, "B-000001")["collateral_c"], bal(rt, A, "1400", "USD", "ABC"), bal(rt, A, "1010")))
        self.assertEqual((-5200000, -200000), (rt.accounting.position(A, "ABC").mv_c, rt.accounting.position(A, "ABC").unrealised_c))
        # S14 Tue 00:35: the Monday night on the new bases (new segments): fee to_c(52,000 x 0.003 / 360) = 0.43; rebate to_c(53,040 x 0.0337 / 360) = 4.97; 2310 1.68, 1410 19.29
        rt, _ = self.restart("2026-10-27T00:35:00-04:00")
        tick(rt)
        self.assertEqual(((43, 497), 168, 1929), ((idem_amounts(rt, "accr:bfee:")[-1][1], idem_amounts(rt, "accr:rebate:")[-1][1]), bal(rt, A, "2310", "USD", "ABC"), bal(rt, A, "1410", "USD", "ABC")))
        # S15 Tue 10:00: BUY_TO_COVER 1,000 ABC @ 51.00: realised -1,000.00; the borrow returns in the same command (collateral 53,040.00 back, fees 1.68 paid, rebate
        # 19.29 received); 1400 / 2310 / 1410 all 0; the borrow CLOSED; every ledger check holds after one more restart
        rt, _ = self.restart("2026-10-27T10:00:00-04:00")
        r = self.market("BUY_TO_COVER", 1000, "ABC", "51.00")
        self.assertEqual((-100000, 5000000), (r.body["fills"][0]["realised_c"], r.body["fills"][0]["cost_relieved_c"]))
        self.assertEqual((0, 0, 0, "CLOSED"), (bal(rt, A, "1400", "USD", "ABC"), bal(rt, A, "2310", "USD", "ABC"), bal(rt, A, "1410", "USD", "ABC"), sh.borrow_row(rt, "B-000001")["status"]))
        self.assertIsNone(rt.accounting.position(A, "ABC"))
        rt, _ = self.restart("2026-10-27T10:01:00-04:00")
        self.assertEqual((300, 10050000), (rt.accounting.position(A, "AAPL").qty, rt.accounting.position(A, "AAPL").cost_c))
        self.assertEqual(750000 - 100000, bal(rt, A, "4000"))
        report = rt.accounting.integrity("full")
        self.assertTrue(report.ok, [p.to_dict() for p in report.problems])
        self.assertEqual([], [p.id for p in report.problems if p.id in ("M4", "M5")])
        self.assertEqual([], tick(rt, only="SETTLE_DUE")["ran"])


if __name__ == "__main__":
    unittest.main()
