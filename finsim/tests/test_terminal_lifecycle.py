"""WP-08 lifecycle tests (TERMINAL_DESIGN.md §11, §19.11, §19.23, §11.5; TERMINAL_WORKPLAN.md WP-08).  Every expected number
is worked by hand in a comment; nothing is pasted from the code's output.  No network: FakeClock, FixedMarks, a FakeProvider
with daily bars, a RateStore with SOFR / ECBDFR prints, a temp database.  The scheduler is driven by ``tick()`` directly
(the thread is never started).

Scaffolding (``runtime``) is shared with ``test_terminal_persistence`` (imported as a module function, so unittest never
loads these cases twice): the orders + lending + credit + futures + options handlers, the lifecycle handlers and a
``LifecycleScheduler`` bound as ``rt.lifecycle`` (what the integrator does)."""
from __future__ import annotations

import json
import shutil
import tempfile
import types
import unittest
from datetime import date
from decimal import Decimal

from tests.terminal_helpers import CREDIT, DEBT, T1, TREASURY, US_EXCHANGE, assert_integrity, bal, buy, deposit, execute, fund
from tests import test_terminal_orders as sc
from tests import test_terminal_shorts as sh
from finsim.terminal import books, credit, execution, lending, lifecycle, tables
from finsim.terminal.ledger import Entry, cr, dr
from finsim.terminal.products import futures, options
from finsim.terminal.quotes.rates import RateStore

D = Decimal
FRI_10 = "2026-10-16T10:00:00-04:00"
TUE_14 = "2026-10-20T14:00:00-04:00"
SOFR_PRINTS = [(date(2026, 10, 15), D("3.87")), (date(2026, 10, 19), D("3.87")), (date(2026, 10, 26), D("3.87")), (date(2026, 10, 30), D("3.87")), (date(2026, 11, 2), D("3.87"))]
ECB_PRINTS = [(date(2026, 10, 15), D("2.00")), (date(2026, 10, 19), D("2.00")), (date(2026, 10, 26), D("2.00"))]


def runtime(tmp, at=FRI_10, marks=None):
    """The §19 runtime + every product handler that exists + the lifecycle (SOFR 3.87 %, ECBDFR 2.00 %, FakeProvider bars)."""
    rt = sc.runtime(tmp, at=at, marks=marks)
    lending.register(rt.bus)
    lending.bind(rt)
    credit.register(rt.bus)
    rt.credit = credit
    futures.register(rt.bus)
    rt.futures = futures
    options.register(rt.bus, rt)
    rt.options = options
    rt.rates = RateStore()
    rt.rates.put("SOFR", SOFR_PRINTS, persist=False)
    rt.rates.put("ECBDFR", ECB_PRINTS, persist=False)
    rt.providers = types.SimpleNamespace(yahoo=rt.fp)
    lifecycle.register(rt.bus, rt)
    rt.lifecycle = lifecycle.LifecycleScheduler(rt.bus, rt.db, rt, rt.clock, rt.log)
    return rt


def tick(rt, at=None, only=None):
    return rt.lifecycle.tick(at=at, only=only)


def ran(out, job=None, status=None):
    return [i for i in out["ran"] if (job is None or i["job"] == job) and (status is None or i["status"] == status)]


def idem_amounts(rt, prefix):
    """``[(idem_key, first line amount_c)]`` of the entries whose idem key starts with ``prefix``, in posting order."""
    return [(r["idem_key"], r["amount_c"]) for r in sc.q(rt, "SELECT e.idem_key, l.amount_c FROM journal_entries e JOIN journal_lines l ON l.entry_id=e.entry_id AND l.line_no=1"
                                                            " WHERE e.idem_key LIKE ? ORDER BY e.entry_id", (prefix + "%",))]


def jobs_done(rt, job, status=None):
    sql, params = "SELECT key, status, note FROM jobs_done WHERE job=?", [job]
    if status:
        sql += " AND status=?"
        params.append(status)
    return sc.q(rt, sql + " ORDER BY key", params)


def _h_vm_loss(ctx, payload):
    """A stubbed variation-margin loss (the futures module's posting shape) under the §11.6 overdraft rule."""
    aid, amount, inst = payload["account_id"], int(payload["amount_c"]), payload["instrument_id"]
    res = execution.fund(ctx, aid, "USD", amount, "vm", payload["key"], None, immediate=True, overdraft=True)
    entry_id = ctx.post(Entry("VM", f"test VM loss {amount}", [dr(aid, "4400", "USD", amount, instrument_id=inst), cr(aid, "1010", "USD", amount)],
                              f"vm:{payload['key']}", ref_type="vm", ref_id=payload["key"]))
    return {"funding": res.to_dict(), "entry_id": entry_id}


class _Boom(lifecycle.Job):
    """A test job whose command fails until ``fail`` is cleared."""
    name = "TEST_BOOM"
    kind = "lifecycle.test_boom"
    when = "test"

    def __init__(self):
        super().__init__()
        self.fail = True

    def due(self, conn, clock, cal, sessions):
        return self.filter_done(conn, ["k1"])

    def run(self, ctx, key, services):
        if self.fail:
            raise RuntimeError("boom")
        return {"status": "DONE", "note": "fixed"}


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-lc-")
        self.rt = None

    def tearDown(self):
        if self.rt is not None:
            self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start(self, at=FRI_10, marks=None):
        self.rt = runtime(self.tmp, at=at, marks=marks)
        return self.rt

    # ---- registry and the API entry point ------------------------------------------------------------------------------------
    def test_registry_order_run_now_and_status(self):
        rt = self.start(at=TUE_14)
        # the bond jobs (WP-10, Build 2) were wired into the REGISTRY by the WP-15 integration: accrual next to ACCRUE_DAILY, coupon / maturity /
        # monthly benchmark listing after the private-credit payments
        # LOAN_MATURE (Build 2 P04, the MONEY-1 follow-up) runs right after the private-credit payments, before SWEEP_OUT can move the repayment cash;
        # jobs a product module added through C13 add_product_job (FX forwards, repo, ...) sit in the order their module chose and are left out here
        from finsim.terminal.lifecycle_eod import PRODUCT_JOBS
        core = [j.name for j in lifecycle.REGISTRY if j.name not in PRODUCT_JOBS]
        snap = [j.name for j in lifecycle.REGISTRY].index("SNAPSHOT")
        self.assertTrue(all([j.name for j in lifecycle.REGISTRY].index(n) < snap for n in PRODUCT_JOBS if n in [j.name for j in lifecycle.REGISTRY]))
        self.assertEqual(["SETTLE_DUE", "ACCRUE_DAILY", "BOND_ACCRUE", "PAY_MONTHLY", "PB_MONTHLY_MIN", "DIVIDEND", "DIVPAY", "PC_PAY", "LOAN_MATURE", "BOND_COUPON", "BOND_MATURITY",
                          "BOND_LISTING", "SWAP_FIX", "SWAP_PAY", "TRS_RESET", "XCCY_EVENTS",
                          "OPTION_EXPIRY", "FUTURES_VM", "FUTURES_FINAL", "SWAP_VM", "OPTION_MARGIN_MARK", "COLLATERAL_MARK", "BORROW_CLEANUP", "SWEEP_OUT", "MARKS_RECORD",
                          "SNAPSHOT", "EXPIRE_ORDERS", "HOUSEKEEPING", "INTEGRITY_FULL"], core)
        self.assertEqual([j.name for j in lifecycle.REGISTRY], [j.name for j in rt.lifecycle.jobs])
        # services.lifecycle.accrue_segment is the §11.3 helper the credit / swap / private-credit modules consume
        self.assertIs(lifecycle.accrue_segment, rt.lifecycle.accrue_segment)
        # run_now for one job: the first tick runs INTEGRITY_FULL at once (key = the current hour), DONE and recorded
        out = lifecycle.run_now(rt, job="INTEGRITY_FULL")
        self.assertEqual(["INTEGRITY_FULL"], [i["job"] for i in out["ran"]])
        self.assertEqual(("DONE", "ok"), (out["ran"][0]["status"], out["ran"][0]["note"]))
        self.assertEqual([("2026-10-20T14", "DONE")], [(r["key"], r["status"]) for r in jobs_done(rt, "INTEGRITY_FULL")])
        self.assertIsNotNone(rt.accounting.last_report())
        with self.assertRaises(ValueError):
            lifecycle.run_now(rt, job="NOPE")
        st = rt.lifecycle.status()
        self.assertEqual(29 + len(PRODUCT_JOBS), len(st["jobs"]))               # 24 + the four bond jobs (WP-15) + LOAN_MATURE (P04) + product-added jobs
        integ = next(j for j in st["jobs"] if j["name"] == "INTEGRITY_FULL")
        self.assertEqual(("2026-10-20T14", "DONE", True, 0), (integ["last_key"], integ["last_status"], integ["available"], integ["failing"]))
        div = next(j for j in st["jobs"] if j["name"] == "DIVIDEND")
        self.assertEqual(False, div["available"])
        self.assertIn("not built", div["note"])

    # ---- §19.11 catch-up ------------------------------------------------------------------------------------------------------
    def test_19_11_catch_up_accruals_settlement_marks_snapshots(self):
        rt = self.start()
        # Fri 10-16 10:00: A funded 200,000.00; the §19.5 short (borrow B-000001; the fill 1,000 ABC @ 50.00 posts the collateral 51,000.00 from
        # the proceeds 49,995.00 + 1,005.00 cash, so its settlement row is SETTLED at 0) and the §19.6 EUR loan 1,000,000.00 at ECBDFR 2.00 + 0.75
        # = 2.75 % (opened 10-16)
        fund(rt, US_EXCHANGE, 20000000)
        sh.setup_abc(rt, fund_c=0)
        sh.short_abc(self, rt)
        r = execute(rt, "credit.loan_draw", {"account_id": CREDIT, "currency": "EUR", "amount_c": 100000000, "term_days": None})
        self.assertTrue(r.ok, r.body)
        self.assertEqual((19899500, 980000000), (bal(rt, US_EXCHANGE, "1010"), bal(rt, TREASURY, "1010")))     # 200,000 - 1,005; 10,000,000 - 200,000
        # the clock stops Fri 16:00 (no tick) and restarts Tue 10-20 09:00; the Friday and Monday closes come from daily bars - the EURUSD close
        # (EURUSD is in the recorded set from the EUR loan's cash, 10-16) from Yahoo's 1-minute bar ending at 17:00 ET, never from a daily bar
        # (V1061R1-02, 1.0.6.1: an FX daily bar's "close" is a snapshot near the London day start; the 1.1201 below is never recorded)
        from finsim.terminal.clock import epoch_of
        rt.fp.set_daily("ABC", [("2026-10-16", "50.00"), ("2026-10-19", "50.00")])
        rt.fp.set_daily("SPY", [("2026-10-16", "769.32"), ("2026-10-19", "770.00")])
        rt.fp.set_daily("EURUSD=X", [("2026-10-16", "1.1201"), ("2026-10-19", "1.1201")])
        rt.fp.set_minute_bars("EURUSD=X", [(epoch_of(date(2026, 10, 16), "16:59"), "1.1238"), (epoch_of(date(2026, 10, 19), "16:59"), "1.1238")])
        out = tick(rt, "2026-10-20T09:00:00-04:00")
        from finsim.terminal.lifecycle_eod import PRODUCT_JOBS          # jobs product modules add (C13) are skipped here when their module is not bound
        self.assertEqual({}, {k: v for k, v in out["skipped_modules"].items() if k not in ("DIVIDEND", "DIVPAY", "PC_PAY", "SWAP_FIX", "SWAP_PAY", "TRS_RESET", "XCCY_EVENTS", "SWAP_VM",
                                                                                         "BOND_ACCRUE", "BOND_COUPON", "BOND_MATURITY", "BOND_LISTING") and k not in PRODUCT_JOBS})
        self.assertEqual([], [i for i in out["ran"] if i["status"] in ("FAILED", "RETRY")])
        # no settlement is due Monday: the short's proceeds went to the lender at the fill (S-000001 SETTLED at 0), so nothing moves twice
        self.assertEqual([], [(r["key"], r["status"]) for r in jobs_done(rt, "SETTLE_DUE")])
        self.assertEqual([], idem_amounts(rt, "settle:"))
        self.assertEqual(0, bal(rt, US_EXCHANGE, "1200"))
        # the Friday and Monday CLOSE marks from daily() bars, note "recorded late"; EURUSD's from the 16:59-17:00 ET 1-minute bars (V1061R1-02)
        marks = sc.q(rt, "SELECT instrument_id, mark_date, price, method, note FROM marks ORDER BY instrument_id, mark_date")
        fx_note = "recorded late: the close of the 1-minute bar 16:59-17:00 ET (the FX day rolls at 17:00)"
        self.assertEqual([("ABC", "2026-10-16", "50", "daily_close", "recorded late"), ("ABC", "2026-10-19", "50", "daily_close", "recorded late"),
                          ("EURUSD", "2026-10-16", "1.1238", "minute_close", fx_note), ("EURUSD", "2026-10-19", "1.1238", "minute_close", fx_note),
                          ("SPY", "2026-10-16", "769.32", "daily_close", "recorded late"), ("SPY", "2026-10-19", "770", "daily_close", "recorded late")],
                         [(m["instrument_id"], m["mark_date"], m["price"], m["method"], m["note"]) for m in marks])
        self.assertEqual([], sc.q(rt, "SELECT 1 FROM attention WHERE code='MARK_MISSING'"))
        # collateral marks Fri and Mon at 50.00: required 51,000.00, no movement, last_mark_date 10-19
        self.assertEqual([("B-000001:2026-10-16", "DONE"), ("B-000001:2026-10-19", "DONE")], [(r["key"], r["status"]) for r in jobs_done(rt, "COLLATERAL_MARK")])
        self.assertEqual((5100000, "2026-10-19"), (sh.borrow_row(rt, "B-000001")["collateral_c"], sh.borrow_row(rt, "B-000001")["last_mark_date"]))
        # 4 nightly keys per object, exactly once, in date order (nights Fri 16, Sat 17, Sun 18, Mon 19):
        #  borrow fee on 1,000 x 50.00 = 50,000.00 at 0.30 % ACT/360, cumulative: 0.42, 0.83 -> 0.41, 1.25 -> 0.42, to_c(1.666667) = 1.67 -> 0.42; total 1.67 = to_c(50,000 x 0.003 x 4/360)
        #  rebate on 51,000.00 at 3.37 %: 4.77, 9.55 -> 4.78, 14.32 -> 4.77, to_c(19.096667) = 19.10 -> 4.78; total 19.10 = to_c(51,000 x 0.0337 x 4/360)
        self.assertEqual([("accr:bfee:B-000001:2026-10-16", 42), ("accr:bfee:B-000001:2026-10-17", 41), ("accr:bfee:B-000001:2026-10-18", 42), ("accr:bfee:B-000001:2026-10-19", 42)],
                         idem_amounts(rt, "accr:bfee:"))
        self.assertEqual([("accr:rebate:B-000001:2026-10-16", 477), ("accr:rebate:B-000001:2026-10-17", 478), ("accr:rebate:B-000001:2026-10-18", 477), ("accr:rebate:B-000001:2026-10-19", 478)],
                         idem_amounts(rt, "accr:rebate:"))
        self.assertEqual((167, 1910), (bal(rt, US_EXCHANGE, "2310", "USD", "ABC"), bal(rt, US_EXCHANGE, "1410", "USD", "ABC")))
        #  loan: 1,000,000 x 0.0275 / 360 = 76.388889 per night: 76.39, 152.78 -> 76.39, 229.17 -> 76.39, 305.56 -> 76.39; total 305.56 = to_c(1,000,000 x 0.0275 x 4/360)
        self.assertEqual([("accr:loan:L-000001:2026-10-16", 7639), ("accr:loan:L-000001:2026-10-17", 7639), ("accr:loan:L-000001:2026-10-18", 7639), ("accr:loan:L-000001:2026-10-19", 7639)],
                         idem_amounts(rt, "accr:loan:"))
        self.assertEqual(30556, bal(rt, CREDIT, "2755", "EUR"))
        #  Treasury cash interest on 9,800,000.00 (the settled USD at the close of each night: 10,000,000 - 200,000) at 3.87 - 0.50 = 3.37 %:
        #  9,800,000 x 0.0337 / 360 = 917.388889: 917.39, 1,834.78 -> 917.39, 2,752.17 -> 917.39, 3,669.56 -> 917.39; total 3,669.56 = to_c(9,800,000 x 0.0337 x 4/360)
        self.assertEqual([("accr:cashint:T1:USD:2026-10-16", 91739), ("accr:cashint:T1:USD:2026-10-17", 91739), ("accr:cashint:T1:USD:2026-10-18", 91739), ("accr:cashint:T1:USD:2026-10-19", 91739)],
                         idem_amounts(rt, "accr:cashint:"))
        self.assertEqual(366956, bal(rt, TREASURY, "1230"))
        self.assertEqual([("CASH:USD", -366956)], [(r["instrument_id"], r["balance_c"]) for r in sc.q(rt, "SELECT instrument_id, balance_c FROM gl_balances WHERE account_id=? AND gl='4300'", (TREASURY,))])
        self.assertEqual(12, len(jobs_done(rt, "ACCRUE_DAILY", "DONE")))
        st = sc.one(rt, "SELECT * FROM accrual_state WHERE kind='cashint' AND ref_id=?", (TREASURY,))
        self.assertEqual(("2026-10-16", 980000000, "0.0337", 360, "2026-10-19", 366956), (st["segment_start"], st["base_c"], st["rate"], st["day_basis"], st["accrued_through"], st["cum_c"]))
        # sweep-out keys once per calendar day (Fri, Sat, Sun, Mon) per account and currency that holds cash:
        #  A Fri: settled 200,000 - 1,005 = 198,995.00 - payables 0 - reserved 0 - maintenance 0 - buffer 10,000.00 = 188,995.00 swept; Sat/Sun/Mon: 10,000 - 10,000 = 0 -> nothing idle
        #  CREDIT EUR Fri: buffer to_c(10,000 / 1.1238) = to_c(8,898.380495) = 8,898.38 -> 1,000,000.00 - 8,898.38 = 991,101.62 swept
        sw = {r["key"]: r["status"] for r in jobs_done(rt, "SWEEP_OUT")}
        self.assertEqual({"T1:US_EXCHANGE:USD:2026-10-16": "DONE", "T1:US_EXCHANGE:USD:2026-10-17": "SKIPPED", "T1:US_EXCHANGE:USD:2026-10-18": "SKIPPED", "T1:US_EXCHANGE:USD:2026-10-19": "SKIPPED",
                          "T1:CREDIT:EUR:2026-10-16": "DONE", "T1:CREDIT:EUR:2026-10-17": "SKIPPED", "T1:CREDIT:EUR:2026-10-18": "SKIPPED", "T1:CREDIT:EUR:2026-10-19": "SKIPPED"}, sw)
        self.assertEqual([("SWEEP_OUT", US_EXCHANGE, "USD", 18899500), ("SWEEP_OUT", CREDIT, "EUR", 99110162)],
                         [(t["kind"], t["from_account"], t["currency"], t["amount_c"]) for t in sc.q(rt, "SELECT kind, from_account, currency, amount_c FROM transfers WHERE kind='SWEEP_OUT' ORDER BY transfer_id")])
        self.assertEqual((1000000, 889838), (bal(rt, US_EXCHANGE, "1010"), bal(rt, CREDIT, "1010", "EUR")))
        self.assertEqual(980000000 + 18899500, bal(rt, TREASURY, "1010"))
        self.assertEqual([], sc.q(rt, "SELECT 1 FROM attention WHERE source='lifecycle' AND resolved_ts IS NULL"))    # the quiet skips open nothing
        # snapshots for Fri, Sat, Sun, Mon for every account and T1:ALL (5 accounts + ALL = 6 x 4 = 24 rows), flagged late; the chain has no gaps
        snaps = sc.q(rt, "SELECT account_id, snap_date, nav_c, pnl_day_c, flows_day_c, partial, detail_json FROM nav_snapshots ORDER BY snap_date, account_id")
        self.assertEqual(24, len(snaps))
        self.assertEqual(["2026-10-16", "2026-10-17", "2026-10-18", "2026-10-19"], sorted({s["snap_date"] for s in snaps}))
        self.assertTrue(all(json.loads(s["detail_json"])["late"] is True for s in snaps))
        self.assertEqual(0, sum(s["partial"] for s in snaps))
        alls = [s for s in snaps if s["account_id"] == "T1:ALL"]
        # FINSIM1 C9d (P3): a LATE (catch-up) snapshot is the NAV AS OF its date - the journal entries with business_date <= that date, never the
        # ledger as it stands.  The catch-up's accruals, rebate, borrow fee, loan interest and sweeps were all posted Tue 10-20 09:00 (business date
        # 10-20: a late posting carries the day it was posted, the NIGHTS rule), so they belong to Tuesday; Fri..Mon read
        # NAV(T1:ALL) = 10,000,000.00 capital - 5.00 short-sale fee (Fri 10-16) = 9,999,995.00 (CREDIT's EUR loan and its cash net to 0 at 1.1238).
        # (Before 1.0.5 every late row read 10,003,338.60, the whole catch-up landing on the Friday row.)
        self.assertEqual([999999500] * 4, [s["nav_c"] for s in alls])
        self.assertEqual([999999500 - 1000000000, 0, 0, 0], [s["pnl_day_c"] for s in alls])          # Fri: NAV - 10,000,000 capital flow; Sat..Mon: no change
        self.assertEqual([1000000000, 0, 0, 0], [s["flows_day_c"] for s in alls])
        for prev, cur in zip(alls, alls[1:]):
            self.assertEqual(cur["pnl_day_c"], cur["nav_c"] - prev["nav_c"] - cur["flows_day_c"])                     # M4 by hand
        # the whole-DB check (M1..M5 included) reports nothing; keys ran in at most the pass budget
        report = rt.accounting.integrity("full")
        self.assertTrue(report.ok, [p.to_dict() for p in report.problems])
        self.assertEqual([], [p.to_dict() for p in report.problems])
        self.assertLessEqual(out["passes"], lifecycle.MAX_PASSES + 2)
        # a second tick posts nothing at all
        n_entries = sc.one(rt, "SELECT COUNT(*) n FROM journal_entries")["n"]
        out2 = tick(rt)
        self.assertEqual(([], n_entries, 24), (out2["ran"], sc.one(rt, "SELECT COUNT(*) n FROM journal_entries")["n"], sc.one(rt, "SELECT COUNT(*) n FROM nav_snapshots")["n"]))
        # replaying a job's request id returns the stored result and posts nothing
        rep = rt.bus.execute(sc.Command("lifecycle.accrue", {"key": "loan:L-000001:2026-10-17", "kind": "loan", "ref": "L-000001", "night": "2026-10-17"},
                                        "job:ACCRUE_DAILY:loan:L-000001:2026-10-17", "lifecycle"))
        self.assertTrue(rep.ok and rep.replayed, rep.body)
        self.assertEqual(n_entries, sc.one(rt, "SELECT COUNT(*) n FROM journal_entries")["n"])

    def test_seven_day_clock_jump_posts_exactly_seven_accruals(self):
        rt = self.start(at="2026-10-20T11:00:00-04:00")
        execute(rt, "credit.loan_draw", {"account_id": CREDIT, "currency": "EUR", "amount_c": 100000000, "term_days": None})
        out = tick(rt, "2026-10-27T11:00:00-04:00")
        # nights 10-20 .. 10-26 at 2.75 %: cumulative 76.39, 152.78, 229.17, 305.56, 381.94 (381.944444), 458.33, 534.72 -> 76.39 x 4, 76.38, 76.39, 76.39; total 534.72
        self.assertEqual([("accr:loan:L-000001:2026-10-%d" % d, a) for d, a in ((20, 7639), (21, 7639), (22, 7639), (23, 7639), (24, 7638), (25, 7639), (26, 7639))],
                         idem_amounts(rt, "accr:loan:"))
        self.assertEqual(53472, bal(rt, CREDIT, "2755", "EUR"))
        # Treasury 10,000,000.00 at 3.37 %: 936.111111 per night -> 936.11, 1,872.22, 2,808.33, 3,744.44, 4,680.56 (+936.12), 5,616.67, 6,552.78; total 6,552.78
        self.assertEqual([93611, 93611, 93611, 93611, 93612, 93611, 93611], [a for _, a in idem_amounts(rt, "accr:cashint:")])
        self.assertEqual(655278, bal(rt, TREASURY, "1230"))
        # one snapshot per calendar day 10-20 .. 10-26 for the 5 accounts and ALL: 42 rows, no gaps (M5), continuity (M4)
        self.assertEqual(42, sc.one(rt, "SELECT COUNT(*) n FROM nav_snapshots")["n"])
        report = rt.accounting.integrity("full")
        self.assertEqual([], [p.to_dict() for p in report.problems])
        self.assertEqual([], tick(rt)["ran"])

    # ---- settlement idempotency and the unfunded path -----------------------------------------------------------------------------
    def test_settle_due_idempotent_and_unfunded(self):
        rt = self.start(at="2026-10-19T10:00:00-04:00")
        fund(rt, US_EXCHANGE, 50000000)
        now = rt.clock.epoch()
        rt.marks.set_quote(sc.rec("AAPL", "339.50", now - 120, now, bar=(now - 180, 120000)))
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=1000, order_type="MARKET")
        self.assertTrue(r.ok, r.body)
        sid = sc.one(rt, "SELECT settle_id FROM settlements WHERE fill_id=?", (r.body["fills"][0]["fill_id"],))["settle_id"]
        # Mon 16:00: the payable (339,505.00, due Tue 10-20) is not due -> nothing runs
        self.assertEqual([], tick(rt, "2026-10-19T16:00:00-04:00", only="SETTLE_DUE")["ran"])
        # Tue 00:35: Dr 2100 / Cr 1010 339,505.00 -> settled 500,000 - 339,505 = 160,495.00; a second run settles nothing; the request id replays
        out = tick(rt, "2026-10-20T00:35:00-04:00", only="SETTLE_DUE")
        self.assertEqual([(sid, "DONE", "job:SETTLE_DUE:" + sid)], [(i["key"], i["status"], i["request_id"]) for i in out["ran"]])
        self.assertEqual((16049500, 0, "SETTLED"), (bal(rt, US_EXCHANGE, "1010"), bal(rt, US_EXCHANGE, "2100"), sc.one(rt, "SELECT status FROM settlements WHERE settle_id=?", (sid,))["status"]))
        self.assertEqual([], tick(rt, only="SETTLE_DUE")["ran"])
        rep = rt.bus.execute(sc.Command("lifecycle.settle", {"key": sid, "settle_id": sid}, "job:SETTLE_DUE:" + sid, "lifecycle"))
        self.assertTrue(rep.replayed)
        self.assertEqual(1, len(sc.q(rt, "SELECT 1 FROM journal_entries WHERE idem_key=?", ("settle:" + sid,))))
        assert_integrity(self, rt, "full")
        # the unfunded path: BUY 100 AAPL (payable 33,951.00 due Wed); the account's cash is then charged away (a 160,495.00 expense) and the
        # Treasury drawn down to 100.00 -> Wed 00:35 the payable cannot be funded: SKIPPED, ERROR SETTLEMENT_UNFUNDED, still PENDING, no posting
        rt.clock.set("2026-10-20T10:00:00-04:00")
        now = rt.clock.epoch()
        rt.marks.set_quote(sc.rec("AAPL", "339.50", now - 120, now, bar=(now - 180, 120000)))
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=100, order_type="MARKET")
        self.assertTrue(r.ok, r.body)
        sid2 = sc.one(rt, "SELECT settle_id FROM settlements WHERE fill_id=?", (r.body["fills"][0]["fill_id"],))["settle_id"]
        self.assertEqual(-3395100, sc.one(rt, "SELECT amount_c FROM settlements WHERE settle_id=?", (sid2,))["amount_c"])
        self.assertTrue(execute(rt, "test.post", {"kind": "FEE", "memo": "charge away", "idem_key": "test:charge", "lines": [[US_EXCHANGE, "5000", "USD", 16049500, "AAPL"], [US_EXCHANGE, "1010", "USD", -16049500]]}).ok)
        self.assertTrue(execute(rt, "treasury.withdraw", {"lifetime_id": T1, "amount_c": 949990000, "currency": "USD"}).ok)     # 9,500,000 - 9,499,900 = 100.00
        self.assertEqual((0, 10000), (bal(rt, US_EXCHANGE, "1010"), bal(rt, TREASURY, "1010")))
        n_entries = sc.one(rt, "SELECT COUNT(*) n FROM journal_entries")["n"]
        out = tick(rt, "2026-10-21T00:35:00-04:00", only="SETTLE_DUE")
        self.assertEqual([(sid2, "SKIPPED", "SETTLEMENT_UNFUNDED")], [(i["key"], i["status"], i["code"]) for i in out["ran"]])
        self.assertEqual(("PENDING", n_entries), (sc.one(rt, "SELECT status FROM settlements WHERE settle_id=?", (sid2,))["status"], sc.one(rt, "SELECT COUNT(*) n FROM journal_entries")["n"]))
        att = sc.q(rt, "SELECT severity, code, ref_id, resolved_ts FROM attention WHERE code='SETTLEMENT_UNFUNDED'")
        self.assertEqual([("ERROR", "SETTLEMENT_UNFUNDED", sid2, None)], [(a["severity"], a["code"], a["ref_id"], a["resolved_ts"]) for a in att])
        self.assertEqual([(sid2, "SKIPPED")], [(r["key"], r["status"]) for r in jobs_done(rt, "SETTLE_DUE") if r["key"] == sid2])
        # a deposit of 40,000.00 (Treasury 40,100.00) and the retry (after the 5-minute skip delay): one sweep-in of exactly 33,951.00 linked to the
        # settlement (L19), the payable settled, the item resolved; the retry used a fresh request id
        deposit(rt, 4000000)
        rt.clock.advance(lifecycle.SKIP_RETRY_S + 1)
        out = tick(rt, only="SETTLE_DUE")
        self.assertEqual([(sid2, "DONE", "job:SETTLE_DUE:" + sid2 + ":r1")], [(i["key"], i["status"], i["request_id"]) for i in out["ran"]])
        sweep = sc.one(rt, "SELECT amount_c, linked_type, linked_id, shortfall_c FROM transfers WHERE kind='SWEEP_IN' AND linked_type='settlement'")
        self.assertEqual((3395100, "settlement", sid2, 3395100), (sweep["amount_c"], sweep["linked_type"], sweep["linked_id"], sweep["shortfall_c"]))
        self.assertEqual((0, 0, "SETTLED", 4010000 - 3395100), (bal(rt, US_EXCHANGE, "1010"), bal(rt, US_EXCHANGE, "2100"),
                                                                 sc.one(rt, "SELECT status FROM settlements WHERE settle_id=?", (sid2,))["status"], bal(rt, TREASURY, "1010")))
        self.assertEqual(0, len(sc.q(rt, "SELECT 1 FROM attention WHERE code='SETTLEMENT_UNFUNDED' AND resolved_ts IS NULL")))
        assert_integrity(self, rt, "full")

    # ---- §11.5 sweep-out ---------------------------------------------------------------------------------------------------------
    def test_11_5_sweep_out_numbers(self):
        rt = self.start(at=TUE_14)
        fund(rt, US_EXCHANGE, 100000000)
        now = rt.clock.epoch()
        rt.marks.set_quote(sc.rec("SPY", "769.32", now - 120, now, bar=(now - 180, 120000)))
        req = dict(account_id=US_EXCHANGE, instrument_id="SPY", side="BUY", qty=5000, order_type="MARKET", tif="DAY")
        r = sc.submit(rt, **req)
        self.assertEqual((409, "CONFIRM_REQUIRED"), (r.http, r.code))
        r = sc.submit(rt, request_id=r.body["request_id"], confirm_large=True, **req)
        self.assertTrue(r.ok, r.body)
        # A after the SPY buy, before settlement: settled 3,846,625.00, payables 3,846,625.00 -> excess = 3,846,625 - 3,846,625 - 0 - 0 - 10,000 = -10,000 -> SKIPPED "nothing idle"
        # DEBT with settled 50,000.00 and no needs -> 40,000.00 returns; CREDIT with 50,000.00 but sweep_out off -> no key; Treasury never swept
        fund(rt, DEBT, 5000000)
        fund(rt, CREDIT, 5000000)
        self.assertTrue(execute(rt, "account.update_settings", {"account_id": CREDIT, "sweep_out": 0}).ok)
        t_before = bal(rt, TREASURY, "1010")
        out = lifecycle.run_now(rt, job="SWEEP_OUT")
        self.assertEqual([], out["ran"])                                                           # 14:00: not yet 17:30
        out = tick(rt, "2026-10-20T17:30:00-04:00", only="SWEEP_OUT")
        got = {i["key"]: (i["status"], i["note"]) for i in out["ran"]}
        self.assertEqual({"T1:US_EXCHANGE:USD:2026-10-20", "T1:DEBT:USD:2026-10-20"}, set(got))
        self.assertEqual("SKIPPED", got["T1:US_EXCHANGE:USD:2026-10-20"][0])
        self.assertTrue(got["T1:US_EXCHANGE:USD:2026-10-20"][1].startswith("nothing idle: 3,846,625.00 - 3,846,625.00 - 0.00 - 0.00 - 10,000.00 = -10,000.00 USD"), got)
        self.assertEqual(("DONE", 4000000, 1000000), (got["T1:DEBT:USD:2026-10-20"][0], sc.one(rt, "SELECT amount_c FROM transfers WHERE kind='SWEEP_OUT'")["amount_c"], bal(rt, DEBT, "1010")))
        self.assertEqual(t_before + 4000000, bal(rt, TREASURY, "1010"))
        self.assertEqual(5000000, bal(rt, CREDIT, "1010"))
        # the quiet skip is recorded (once per calendar day) and opens no attention; the key is terminal for the date
        self.assertEqual([("T1:DEBT:USD:2026-10-20", "DONE"), ("T1:US_EXCHANGE:USD:2026-10-20", "SKIPPED")], [(r["key"], r["status"]) for r in jobs_done(rt, "SWEEP_OUT")])
        self.assertEqual([], sc.q(rt, "SELECT 1 FROM attention WHERE source='lifecycle'"))
        self.assertEqual([], tick(rt, only="SWEEP_OUT")["ran"])
        assert_integrity(self, rt, "full")

    def test_sweep_out_respects_reserved_cash(self):
        rt = self.start(at=TUE_14)
        fund(rt, US_EXCHANGE, 10000000)
        now = rt.clock.epoch()
        rt.marks.set_quote(sc.rec("AAPL", "339.50", now - 60, now))
        # a resting LIMIT BUY 100 AAPL @ 300.00 reserves 100 x 300.00 + fee max(100 x 0.005, 1.00) = 30,001.00 -> excess = 100,000 - 0 - 30,001 - 0 - 10,000 = 59,999.00
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=100, order_type="LIMIT", limit_px="300.00", tif="GTC")
        self.assertTrue(r.ok and r.body["order"]["status"] == "WORKING", r.body)
        out = tick(rt, "2026-10-20T17:30:00-04:00", only="SWEEP_OUT")
        self.assertEqual([("T1:US_EXCHANGE:USD:2026-10-20", "DONE")], [(i["key"], i["status"]) for i in out["ran"]])
        self.assertEqual((5999900, 4000100), (sc.one(rt, "SELECT amount_c FROM transfers WHERE kind='SWEEP_OUT'")["amount_c"], bal(rt, US_EXCHANGE, "1010")))
        stored = json.loads(sc.one(rt, "SELECT result_json FROM commands WHERE request_id=?", (out["ran"][0]["request_id"],))["result_json"])
        # FINSIM1 C9b / D9: nothing restricted here (no collateral received), so the adjustments are 0.00
        self.assertEqual({"settled_c": 10000000, "adjustments_c": 0, "payables_c": 0, "reserved_c": 3000100, "futures_maintenance_shortfall_c": 0, "buffer_c": 1000000,
                          "excess_c": 5999900},
                         {k: v for k, v in stored["formula"].items() if k != "text"})
        assert_integrity(self, rt, "full")

    # ---- §19.23 Treasury cash interest and the monthly payment --------------------------------------------------------------------
    def test_19_23_treasury_cash_interest_576_02_and_pay_monthly(self):
        rt = self.start(at=TUE_14)
        fund(rt, US_EXCHANGE, 100000000)
        now = rt.clock.epoch()
        rt.marks.set_quote(sc.rec("SPY", "769.32", now - 120, now, bar=(now - 180, 120000)))
        req = dict(account_id=US_EXCHANGE, instrument_id="SPY", side="BUY", qty=5000, order_type="MARKET", tif="DAY")
        r = sc.submit(rt, **req)
        r = sc.submit(rt, request_id=r.body["request_id"], confirm_large=True, **req)
        self.assertTrue(r.ok, r.body)
        self.assertEqual(615337500, bal(rt, TREASURY, "1010"))                                     # 10,000,000 - 1,000,000 - 2,846,625 = 6,153,375.00
        # Wed 00:35: the night of Tue 10-20 on 6,153,375.00 at 3.87 - 0.50 = 3.37 %: to_c(6,153,375 x 0.0337 / 360) = to_c(576.0243) = 576.02 Dr 1230 / Cr 4300
        out = tick(rt, "2026-10-21T00:35:00-04:00")
        self.assertEqual([("accr:cashint:T1:USD:2026-10-20", 57602)], idem_amounts(rt, "accr:cashint:"))
        lines = sc.q(rt, "SELECT l.gl, l.amount_c, l.instrument_id, e.memo FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id WHERE e.idem_key=? ORDER BY l.line_no",
                     ("accr:cashint:T1:USD:2026-10-20",))
        self.assertEqual([("1230", 57602, None), ("4300", -57602, "CASH:USD")], [(l["gl"], l["amount_c"], l["instrument_id"]) for l in lines])
        self.assertIn("SOFR 3.87 % (2026-10-19) - 0.50 % = 3.37 %, ACT/360 on 6,153,375.00", lines[0]["memo"])
        self.assertEqual((57602, 615337500), (bal(rt, TREASURY, "1230"), bal(rt, TREASURY, "1010")))
        # the SPY payable settled the same tick from the account's own cash (A settled 0 -> nothing to sweep, no sweep-out transfer)
        self.assertEqual((0, 0), (bal(rt, US_EXCHANGE, "1010"), bal(rt, US_EXCHANGE, "2100")))
        self.assertEqual([], sc.q(rt, "SELECT 1 FROM transfers WHERE kind='SWEEP_OUT'") + jobs_done(rt, "SWEEP_OUT", "DONE"))
        t = rt.accounting.treasury(T1)
        self.assertEqual((57602, True), (t.retained_c, t.identity_ok))                               # retained = the cash interest; NAV = capital + retained - advances
        # Nov 1 00:50: the nights 10-21 .. 10-31 accrue first (12 nights in all: cum to_c(6,153,375 x 0.0337 x 12/360) = to_c(6,912.2913) = 6,912.29), then
        # PAY_MONTHLY 2026-10 pays the accrued cash interest: Dr 1010 / Cr 1230 6,912.29 (pay:cashint:T1:TREASURY:2026-10)
        out = tick(rt, "2026-11-01T00:50:00-04:00")
        self.assertEqual(12, len(idem_amounts(rt, "accr:cashint:")))
        self.assertEqual([("pay:cashint:T1:TREASURY:2026-10", 691229)], idem_amounts(rt, "pay:cashint:"))
        self.assertEqual((0, 615337500 + 691229), (bal(rt, TREASURY, "1230"), bal(rt, TREASURY, "1010")))
        self.assertEqual([("T1:TREASURY:2026-10", "DONE")], [(r["key"], r["status"]) for r in jobs_done(rt, "PAY_MONTHLY")])
        self.assertEqual([], [i for i in out["ran"] if i["status"] == "FAILED"])
        report = rt.accounting.integrity("full")
        self.assertTrue(report.ok, [p.to_dict() for p in report.problems])
        self.assertEqual([], [p.id for p in report.problems if p.id in ("M4", "M5")])
        self.assertEqual([], tick(rt)["ran"])

    def test_pb_credit_interest_and_monthly_minimum(self):
        rt = self.start(at=TUE_14)
        pb_gs = f"{T1}:PB_GS"
        with rt.db.writer() as tx:
            books._insert_account(tx.conn, tx.ts, "T", T1, "PB_GS", "Goldman Sachs (PB)", "PB", 90, broker="GS")
        self.assertTrue(execute(rt, "account.update_settings", {"account_id": pb_gs, "sweep_out": 0}).ok)
        fund(rt, pb_gs, 100000000)
        # WP-07's schedule hook: the seed GS schedule with a 500.00 monthly minimum
        rt.prime = types.SimpleNamespace(schedule=lambda acct: dict(tables.PB_SCHEDULES["GS"], monthly_minimum_c=50000))
        # night of Tue 10-20 on settled USD 1,000,000.00 at 3.87 - 0.75 = 3.12 %: to_c(1,000,000 x 0.0312 / 360) = to_c(86.6667) = 86.67 Dr 1230 / Cr 4300 (accr:pbcredit)
        tick(rt, "2026-10-21T00:35:00-04:00")
        self.assertEqual([("accr:pbcredit:T1:PB_GS:2026-10-20", 8667)], idem_amounts(rt, "accr:pbcredit:"))
        self.assertEqual(8667, bal(rt, pb_gs, "1230"))
        # a 120.00 fee charged on Wed 10-21 (settled USD 999,880.00 from that close on), then Nov 1: PB_MONTHLY_MIN posts max(0, 500.00 - 120.00) = 380.00
        # Dr 5000 / Cr 1010 (pay:pbmin); PAY_MONTHLY pays the credit interest: night 10-20 on 1,000,000.00 = 86.67, then a new segment on 999,880.00 for the 11
        # nights 10-21 .. 10-31: to_c(999,880 x 0.0312 x 11/360) = to_c(953.2189) = 953.22 -> 1,039.89 Dr 1010 / Cr 1230
        self.assertTrue(execute(rt, "test.post", {"kind": "FEE", "memo": "October fees", "idem_key": "test:fee:oct", "lines": [[pb_gs, "5000", "USD", 12000, "AAPL"], [pb_gs, "1010", "USD", -12000]]}).ok)
        tick(rt, "2026-11-01T00:50:00-04:00")
        self.assertEqual([("pay:pbmin:T1:PB_GS:2026-10", 38000)], idem_amounts(rt, "pay:pbmin:"))
        self.assertEqual([("pay:cashint:T1:PB_GS:2026-10", 103989)], [x for x in idem_amounts(rt, "pay:cashint:") if "PB_GS" in x[0]])
        self.assertEqual(12, len(idem_amounts(rt, "accr:pbcredit:")))
        self.assertEqual((0, 100000000 - 12000 - 38000 + 103989), (bal(rt, pb_gs, "1230"), bal(rt, pb_gs, "1010")))
        # November: 600.00 of fees -> the minimum is met, nothing posted (DONE "minimum met")
        rt.clock.set("2026-11-15T10:00:00-05:00")
        self.assertTrue(execute(rt, "test.post", {"kind": "FEE", "memo": "November fees", "idem_key": "test:fee:nov", "lines": [[pb_gs, "5000", "USD", 60000, "AAPL"], [pb_gs, "1010", "USD", -60000]]}).ok)
        out = tick(rt, "2026-12-01T00:50:00-05:00", only="PB_MONTHLY_MIN")
        self.assertEqual([("T1:PB_GS:2026-11", "DONE")], [(i["key"], i["status"]) for i in out["ran"]])
        self.assertIn("minimum met", out["ran"][0]["note"])
        self.assertEqual(1, len(idem_amounts(rt, "pay:pbmin:")))
        assert_integrity(self, rt, "full")

    # ---- snapshots: partial flag and continuity per lifetime ---------------------------------------------------------------------
    def test_snapshot_partial_flag_and_pnl_day_continuity(self):
        rt = self.start(at="2026-10-19T10:00:00-04:00")
        self.assertTrue(execute(rt, "account.update_settings", {"account_id": US_EXCHANGE, "sweep_out": 0}).ok)
        fund(rt, US_EXCHANGE, 50000000)
        self.assertTrue(buy(rt, US_EXCHANGE, "AAPL", 1000, "339.50").ok)                              # payable 339,505.00 due Tue; mark 339.50
        # Mon 17:46: NAV(A) = 500,000 - 339,505 + 339,500 = 499,995.00; flows_day = the 500,000 transfer; pnl_day = -5.00
        #            NAV(T1:ALL) = 10,000,000 - 5 = 9,999,995.00; flows_day = the 10,000,000 capital; pnl_day = -5.00
        tick(rt, "2026-10-19T17:46:00-04:00")
        row = lambda scope, d: sc.one(rt, "SELECT * FROM nav_snapshots WHERE account_id=? AND snap_date=?", (scope, d))   # noqa: E731
        a, all_ = row(US_EXCHANGE, "2026-10-19"), row("T1:ALL", "2026-10-19")
        self.assertEqual((49999500, 50000000, -500, 0), (a["nav_c"], a["flows_day_c"], a["pnl_day_c"], a["partial"]))
        self.assertEqual((999999500, 1000000000, -500, 0), (all_["nav_c"], all_["flows_day_c"], all_["pnl_day_c"], all_["partial"]))
        self.assertEqual(6, sc.one(rt, "SELECT COUNT(*) n FROM nav_snapshots WHERE snap_date='2026-10-19'")["n"])
        # Tue: settlement at 00:30 (settled 160,495.00), mark 342.00 -> NAV(A) = 160,495 + 342,000 = 502,495.00; pnl_day +2,500.00.  The full tick also
        # accrues the Treasury's Monday night on 9,500,000.00 at 3.37 %: to_c(9,500,000 x 0.0337 / 360) = to_c(889.3056) = 889.31 ->
        # ALL = 10,002,495.00 + 889.31 = 10,003,384.31; pnl_day = 2,500.00 + 889.31 = 3,389.31
        rt.clock.set("2026-10-20T10:00:00-04:00")
        rt.marks.set("AAPL", "342.00")
        tick(rt, "2026-10-20T17:46:00-04:00")
        a, all_ = row(US_EXCHANGE, "2026-10-20"), row("T1:ALL", "2026-10-20")
        self.assertEqual((50249500, 0, 250000), (a["nav_c"], a["flows_day_c"], a["pnl_day_c"]))
        self.assertEqual((1000338431, 0, 338931), (all_["nav_c"], all_["flows_day_c"], all_["pnl_day_c"]))
        # Wed: deposit 100,000.00 (a flow, not P&L) and an UNPRICED position: 100 ZZTEST @ 10.00 (fee 1.00, payable 1,001.00) with no mark -> partial, missing ZZTEST;
        # the core carries an unpriced position at cost (U = 0, §6.3 as built): A = 160,495 - 1,001 + 342,000 + 1,000 = 502,494.00 (pnl_day = the fee, -1.00);
        # the Treasury's Tuesday night (same 9,500,000 base, the deposit is Wednesday's) cum to_c(1,778.6111) = 1,778.61 -> 889.30;
        # ALL = 10,003,384.31 + 100,000 - 1 + 889.30 = 10,104,272.61; flows_day 100,000; pnl_day = 889.30 - 1.00 = 888.30
        rt.clock.set("2026-10-21T10:00:00-04:00")
        deposit(rt, 10000000)
        sh.add_equity(rt, "ZZTEST", 1000000)
        self.assertTrue(buy(rt, US_EXCHANGE, "ZZTEST", 100, "10.00").ok)
        tick(rt, "2026-10-21T17:46:00-04:00")
        a, all_ = row(US_EXCHANGE, "2026-10-21"), row("T1:ALL", "2026-10-21")
        self.assertEqual((50249400, 0, -100, 1, ["ZZTEST"]), (a["nav_c"], a["flows_day_c"], a["pnl_day_c"], a["partial"], json.loads(a["detail_json"])["missing"]))
        self.assertEqual((1010427261, 10000000, 88830, 1, ["ZZTEST"]), (all_["nav_c"], all_["flows_day_c"], all_["pnl_day_c"], all_["partial"], json.loads(all_["detail_json"])["missing"]))
        # M4 by hand over the lifetime's chain and no gaps; the whole-DB check reports no M4 / M5 (M1 is skipped while partial)
        rows = sc.q(rt, "SELECT * FROM nav_snapshots WHERE account_id='T1:ALL' ORDER BY snap_date")
        self.assertEqual(["2026-10-19", "2026-10-20", "2026-10-21"], [r["snap_date"] for r in rows])
        for prev, cur in zip(rows, rows[1:]):
            self.assertEqual(cur["pnl_day_c"], cur["nav_c"] - prev["nav_c"] - cur["flows_day_c"])
        report = rt.accounting.integrity("full")
        self.assertTrue(report.ok, [p.to_dict() for p in report.problems])
        self.assertEqual([], [p.id for p in report.problems if p.id in ("M4", "M5")])
        # an existing row is never replaced by a later run
        self.assertEqual([], tick(rt, only="SNAPSHOT")["ran"])
        self.assertEqual(49999500, row(US_EXCHANGE, "2026-10-19")["nav_c"])

    # ---- DAY / GTC expiry keys --------------------------------------------------------------------------------------------------------
    def test_expire_orders_day_and_gtc_keys(self):
        rt = self.start(at=TUE_14)
        fund(rt, US_EXCHANGE, 10000000)
        now = rt.clock.epoch()
        rt.marks.set_quote(sc.rec("AAPL", "339.50", now - 60, now))
        day = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=10, order_type="LIMIT", limit_px="300.00", tif="DAY")
        gtc = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=10, order_type="LIMIT", limit_px="300.00", tif="GTC")
        self.assertTrue(day.ok and gtc.ok, (day.body, gtc.body))
        d_id, g_id = day.body["order"]["order_id"], gtc.body["order"]["order_id"]
        self.assertEqual((sc.TUE_CLOSE, "WORKING"), (sc.order_row(rt, d_id)["expires_ts"], sc.order_row(rt, d_id)["status"]))
        # at the close nothing expires yet (expires_ts < now is the rule); one second after, the DAY order does, the GTC one keeps working
        self.assertEqual([], tick(rt, "2026-10-20T16:00:00-04:00", only="EXPIRE_ORDERS")["ran"])
        out = tick(rt, "2026-10-20T16:00:01-04:00", only="EXPIRE_ORDERS")
        self.assertEqual([(d_id, "DONE")], [(i["key"], i["status"]) for i in out["ran"]])
        self.assertEqual(("EXPIRED", "WORKING"), (sc.order_row(rt, d_id)["status"], sc.order_row(rt, g_id)["status"]))
        self.assertEqual([("WORKING", "EXPIRED")], [(e["from_status"], e["to_status"]) for e in sc.q(rt, "SELECT from_status, to_status FROM order_events WHERE order_id=? AND to_status='EXPIRED'", (d_id,))])
        self.assertEqual([(d_id, "DONE")], [(r["key"], r["status"]) for r in jobs_done(rt, "EXPIRE_ORDERS")])
        # GTC (USX-31, P05): the session close of the 90th calendar day, DST-safe; 2026-10-20 + 90 days = Mon 2027-01-18, Martin Luther King Jr.
        # Day (no session), so the last session close on or before it: Fri 2027-01-15 16:00 ET; one second after it the key appears
        self.assertEqual(sc.ep("2027-01-15T16:00:00"), sc.order_row(rt, g_id)["expires_ts"])
        self.assertEqual([], tick(rt, "2027-01-15T15:59:00-05:00", only="EXPIRE_ORDERS")["ran"])
        out = tick(rt, "2027-01-15T16:00:01-05:00", only="EXPIRE_ORDERS")
        self.assertEqual([(g_id, "DONE")], [(i["key"], i["status"]) for i in out["ran"]])
        self.assertEqual("EXPIRED", sc.order_row(rt, g_id)["status"])
        self.assertIn("90 days", sc.order_row(rt, g_id)["reason"])
        self.assertEqual([], tick(rt, only="EXPIRE_ORDERS")["ran"])

    # ---- housekeeping ------------------------------------------------------------------------------------------------------------------
    def test_housekeeping_prunes_rolls_resolves_and_never_touches_order_events(self):
        rt = self.start(at=TUE_14)
        rt.bus.register("test.vm_loss", _h_vm_loss)
        fund(rt, US_EXCHANGE, 10000000)
        now = rt.clock.epoch()
        rt.marks.set_quote(sc.rec("AAPL", "339.50", now - 60, now))
        self.assertTrue(sc.submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=10, order_type="LIMIT", limit_px="300.00", tif="GTC").ok)
        with rt.db.writer() as tx:
            for iid, ts in (("MSFT", now - 2 * 86400), ("AAPL", now - 2 * 86400), ("NVDA", now - 60)):
                tx.conn.execute("INSERT INTO quote_cache(instrument_id, record_json, updated_ts) VALUES (?,?,?)", (iid, "{}", ts))
        n_events = sc.one(rt, "SELECT COUNT(*) n FROM order_events")["n"]
        n_fut = sc.one(rt, "SELECT COUNT(*) n FROM instruments WHERE kind='FUTURE'")["n"]
        # Tue: MSFT (nobody holds / works / watches it, older than a day) is pruned; AAPL (a working order) and NVDA (fresh) stay; order_events untouched
        out = tick(rt, only="HOUSEKEEPING")
        self.assertEqual([("2026-10-20", "DONE")], [(i["key"], i["status"]) for i in out["ran"]])
        self.assertEqual(["AAPL", "NVDA"], [r["instrument_id"] for r in sc.q(rt, "SELECT instrument_id FROM quote_cache ORDER BY instrument_id")])
        self.assertEqual((n_events, n_fut), (sc.one(rt, "SELECT COUNT(*) n FROM order_events")["n"], sc.one(rt, "SELECT COUNT(*) n FROM instruments WHERE kind='FUTURE'")["n"]))
        self.assertEqual([], tick(rt, only="HOUSEKEEPING")["ran"])                                   # once per date
        # Wed: a FUNDING_SHORTFALL item (a 5,000.00 VM loss with Treasury at 100.00) is resolved by housekeeping once 1010 is back at 0
        fut = sc.one(rt, "SELECT instrument_id FROM instruments WHERE kind='FUTURE' ORDER BY instrument_id LIMIT 1")["instrument_id"]
        r = execute(rt, "treasury.transfer", {"from_account": US_EXCHANGE, "to_account": TREASURY, "currency": "USD", "amount_c": 10000000 - 3000100})   # leave the reserved 30,001.00
        self.assertTrue(r.ok, r.body)
        r = execute(rt, "treasury.withdraw", {"lifetime_id": T1, "amount_c": bal(rt, TREASURY, "1010") - 10000, "currency": "USD"})               # Treasury 100.00
        self.assertTrue(r.ok, r.body)
        r = execute(rt, "test.vm_loss", {"account_id": US_EXCHANGE, "amount_c": 500000 + 3000100, "instrument_id": fut, "key": "t1"})
        self.assertTrue(r.ok, r.body)
        # C12 a: own settled 30,001.00 against 35,001.00 -> shortfall 5,000.00; Treasury sweeps its 100.00, the rest 4,900.00 overdraws:
        # 1010 = 30,001.00 + 100.00 - 35,001.00 = -4,900.00
        self.assertEqual(-490000, bal(rt, US_EXCHANGE, "1010"))
        self.assertEqual(1, len(sc.q(rt, "SELECT 1 FROM attention WHERE code='FUNDING_SHORTFALL' AND resolved_ts IS NULL")))
        deposit(rt, 10000000)
        fund(rt, US_EXCHANGE, 490000)
        self.assertEqual(0, bal(rt, US_EXCHANGE, "1010"))
        out = tick(rt, "2026-10-21T09:00:00-04:00", only="HOUSEKEEPING")
        self.assertEqual([("2026-10-21", "DONE")], [(i["key"], i["status"]) for i in out["ran"]])
        self.assertEqual(0, len(sc.q(rt, "SELECT 1 FROM attention WHERE code='FUNDING_SHORTFALL' AND resolved_ts IS NULL")))
        self.assertEqual(n_events, sc.one(rt, "SELECT COUNT(*) n FROM order_events")["n"])
        # Dec 21: the listing roll expires ESZ26 (last trade 2026-12-18) and lists new months; the master is reloaded after the commit
        self.assertEqual("ACTIVE", rt.instruments.get("ESZ26").status)
        out = tick(rt, "2026-12-21T09:00:00-05:00", only="HOUSEKEEPING")
        self.assertEqual([("2026-12-21", "DONE")], [(i["key"], i["status"]) for i in out["ran"]])
        self.assertEqual("EXPIRED", rt.instruments.get("ESZ26").status)
        stored = json.loads(sc.one(rt, "SELECT result_json FROM commands WHERE request_id=?", (out["ran"][0]["request_id"],))["result_json"])
        self.assertIn("ESZ26", stored["expired_futures"])
        self.assertTrue(stored["reload_master"] and stored["listed"] > 0, stored)
        self.assertGreater(sc.one(rt, "SELECT COUNT(*) n FROM instruments WHERE kind='FUTURE'")["n"], n_fut)
        self.assertEqual(n_events, sc.one(rt, "SELECT COUNT(*) n FROM order_events")["n"])

    # ---- a failing job backs off and surfaces; a missing module is skipped with a note ---------------------------------------------------
    def test_failing_job_backs_off_and_surfaces(self):
        rt = self.start(at=TUE_14)
        job = _Boom()
        job.services, job.scheduler = rt, rt.lifecycle
        rt.bus.register(job.kind, lifecycle._handler_for(job))
        rt.lifecycle.jobs.append(job)
        t0 = rt.clock.epoch()
        out = tick(rt, only="TEST_BOOM")
        self.assertEqual([("k1", "FAILED", 500)], [(i["key"], i["status"], i["http"]) for i in out["ran"]])
        row = lambda: sc.one(rt, "SELECT failures, parked_until_ts, last_error FROM job_failures WHERE job='TEST_BOOM' AND key='k1'")   # noqa: E731
        self.assertEqual((1, t0 + 60), (row()["failures"], row()["parked_until_ts"]))                    # backoff 1 min
        self.assertIn("boom", row()["last_error"])
        att = lambda: sc.one(rt, "SELECT severity, message, resolved_ts FROM attention WHERE source='lifecycle' AND code='JOB_FAILED' AND ref_id='TEST_BOOM:k1'")   # noqa: E731
        self.assertEqual(("WARN", None), (att()["severity"], att()["resolved_ts"]))
        self.assertEqual([], tick(rt, only="TEST_BOOM")["ran"])                                          # parked
        # 2, 4, 8, 16 minutes; ERROR after the 5th failure; one attention row throughout
        for n, delay in ((2, 120), (3, 240), (4, 480), (5, 960)):
            rt.clock.advance((row()["parked_until_ts"] - rt.clock.epoch()) + 1)
            out = tick(rt, only="TEST_BOOM")
            self.assertEqual(["FAILED"], [i["status"] for i in out["ran"]])
            self.assertEqual((n, rt.clock.epoch() + delay), (row()["failures"], row()["parked_until_ts"]))
        self.assertEqual("ERROR", att()["severity"])
        self.assertEqual(1, len(sc.q(rt, "SELECT 1 FROM attention WHERE code='JOB_FAILED'")))
        self.assertEqual(1, next(j for j in rt.lifecycle.status()["jobs"] if j["name"] == "TEST_BOOM")["failing"])
        # the 6th and later failures retry hourly
        rt.clock.advance((row()["parked_until_ts"] - rt.clock.epoch()) + 1)
        tick(rt, only="TEST_BOOM")
        self.assertEqual((6, rt.clock.epoch() + 3600), (row()["failures"], row()["parked_until_ts"]))
        # fixed: the next attempt succeeds, the failure row goes, the item resolves, the key is DONE
        job.fail = False
        rt.clock.advance(3601)
        out = tick(rt, only="TEST_BOOM")
        self.assertEqual([("k1", "DONE")], [(i["key"], i["status"]) for i in out["ran"]])
        self.assertIsNone(row())
        self.assertIsNotNone(att()["resolved_ts"])
        self.assertEqual([("k1", "DONE", "fixed")], [(r["key"], r["status"], r["note"]) for r in jobs_done(rt, "TEST_BOOM")])
        self.assertEqual([], tick(rt, only="TEST_BOOM")["ran"])

    def test_missing_module_is_skipped_with_a_note(self):
        rt = self.start(at=TUE_14)
        self.assertIsNone(getattr(rt, "dividends", None))
        out = tick(rt, only="DIVIDEND")
        self.assertEqual(([], True), (out["ran"], "not built" in out["skipped_modules"]["DIVIDEND"]))
        self.assertEqual([], sc.q(rt, "SELECT 1 FROM jobs_done WHERE job='DIVIDEND'") + sc.q(rt, "SELECT 1 FROM attention"))
        # a bound module without the key helper is skipped with a note too, never an error
        rt.swaps = types.SimpleNamespace()
        out = tick(rt, only="SWAP_FIX")
        self.assertEqual([], out["ran"])
        st = next(j for j in rt.lifecycle.status()["jobs"] if j["name"] == "SWAP_FIX")
        self.assertIn("swap.fix is not registered", st["note"])
        # an accrual kind whose module is absent is a note on ACCRUE_DAILY, not a key
        tick(rt, "2026-10-21T00:35:00-04:00", only="ACCRUE_DAILY")
        self.assertIn("not built", rt.lifecycle.job("ACCRUE_DAILY").notes["swaprcv"])

    # ---- jobs ignore CLOSED lifetimes --------------------------------------------------------------------------------------------------
    def test_jobs_ignore_closed_lifetimes(self):
        rt = self.start(at="2026-10-19T10:00:00-04:00")
        fund(rt, US_EXCHANGE, 50000000)
        self.assertTrue(buy(rt, US_EXCHANGE, "AAPL", 1000, "339.50").ok)                              # T1 payable due Tue 10-20
        # T1 is archived (status CLOSED, its accounts CLOSED) and T2 opened with 10,000,000.00 (what book.reset_lifetime does)
        book = books.Book("T", "Test Book", "FUND", 1000000000)
        rt.clock.set("2026-10-19T11:00:00-04:00")
        lid2 = books.create_lifetime(None, rt.bus, book, 2, 1000000000)
        self.assertEqual("T2", lid2)
        with rt.db.writer() as tx:
            tx.conn.execute("UPDATE lifetimes SET status='CLOSED', ended_ts=? WHERE lifetime_id='T1'", (tx.ts,))
            tx.conn.execute("UPDATE accounts SET status='CLOSED', closed_ts=? WHERE lifetime_id='T1'", (tx.ts,))
        out = tick(rt, "2026-10-20T17:46:00-04:00")
        self.assertEqual([], [i for i in out["ran"] if i["status"] == "FAILED"])
        # T1's settlement stays PENDING; no key of any job names a T1 account; snapshots and cash interest exist for T2 only
        self.assertEqual("PENDING", sc.one(rt, "SELECT status FROM settlements WHERE account_id=?", (US_EXCHANGE,))["status"])
        self.assertEqual([], [r for r in sc.q(rt, "SELECT job, key FROM jobs_done") if "T1:" in r["key"]])
        self.assertEqual({"T2:ALL", "T2:CREDIT", "T2:DEBT", "T2:FOREIGN_FX", "T2:TREASURY", "T2:US_EXCHANGE"}, {r["account_id"] for r in sc.q(rt, "SELECT DISTINCT account_id FROM nav_snapshots")})
        self.assertEqual(["accr:cashint:T2:USD:2026-10-19"], [k for k, _ in idem_amounts(rt, "accr:cashint:")])      # Tuesday's night accrues Wed 00:30
        self.assertEqual(0, bal(rt, TREASURY, "1230"))
        report = rt.accounting.integrity("full")
        self.assertTrue(report.ok, [p.to_dict() for p in report.problems])



class SofrWaitWordingTests(unittest.TestCase):
    """V104R2-BW-02 (e), the job side (FINSIM1 P6, 1.0.6): the reason a SOFR-based night waits reads in trader words - no convention code
    such as "(RATE_LAG; never defaulted)" - and still says that a rate is never assumed; the refusal codes are unchanged."""

    def test_sofr_wait_reasons(self):
        from finsim.terminal import lifecycle_jobs
        from finsim.terminal.commands import RejectError
        with self.assertRaises(RejectError) as cm:
            lifecycle_jobs.sofr_for_night(types.SimpleNamespace(), date(2026, 10, 27))
        self.assertEqual("RATE_STORE_MISSING", cm.exception.code)
        self.assertEqual("no interest-rate data is loaded: SOFR is unknown and a rate is never assumed", cm.exception.message)
        rates = RateStore()
        with self.assertRaises(RejectError) as cm:
            lifecycle_jobs.sofr_for_night(types.SimpleNamespace(rates=rates), date(2026, 10, 27))
        self.assertEqual("RATE_UNAVAILABLE", cm.exception.code)
        self.assertEqual("no SOFR print within 10 days on or before 2026-10-27: the night waits for FRED (it publishes SOFR a business day late;"
                         " a rate is never assumed)", cm.exception.message)
        self.assertNotIn("RATE_LAG", cm.exception.message)

    def test_private_credit_night_and_swap_fixing_wait_reasons(self):
        """The same wording for a private-credit coupon night and a swap fixing (the integrator, 1.0.6: the last two places that still said
        "(RATE_LAG; ...)", one of them in a file no package owned): the refusal codes are unchanged; the reason says that the night or the
        fixing waits for FRED and that a rate is never assumed."""
        from finsim.terminal.commands import ConflictError, RejectError
        from finsim.terminal.products import private_credit, swaps
        loan = {"index_name": "SOFR", "loan_id": "PL-000001"}
        with self.assertRaises(RejectError) as cm:
            private_credit.index_for_night(types.SimpleNamespace(), loan, date(2026, 10, 27))
        self.assertEqual(("RATE_STORE_MISSING", "no interest-rate data is loaded: SOFR is unknown and a rate is never assumed"),
                         (cm.exception.code, cm.exception.message))
        with self.assertRaises(RejectError) as cm:
            private_credit.index_for_night(types.SimpleNamespace(rates=RateStore()), loan, date(2026, 10, 27))
        self.assertEqual(("RATE_UNAVAILABLE", "no SOFR print within 10 days on or before 2026-10-27: the night waits for FRED (it publishes SOFR a"
                                              " business day late; a rate is never assumed)"), (cm.exception.code, cm.exception.message))
        with self.assertRaises(ConflictError) as cm:
            swaps.fixing_for(types.SimpleNamespace(rates=RateStore()), date(2026, 10, 27))
        self.assertEqual(("RATE_UNAVAILABLE", "no SOFR print within 10 days on or before 2026-10-27: the fixing waits for FRED (it publishes SOFR a"
                                              " business day late; a rate is never assumed)"), (cm.exception.code, cm.exception.message))
        with self.assertRaises(ConflictError) as cm:
            swaps.fixing_for(types.SimpleNamespace(), date(2026, 10, 27))
        self.assertEqual(("RATE_UNAVAILABLE", "no interest-rate data is loaded: SOFR is unknown and a rate is never assumed"),
                         (cm.exception.code, cm.exception.message))
        # a print within the 10-day lag is used as it is (unchanged behaviour): SOFR 3.87 % observed 10-19 serves the night / fixing of 10-27
        rates = RateStore()
        rates.put("SOFR", [(date(2026, 10, 19), Decimal("3.87"))], persist=False)
        self.assertEqual((date(2026, 10, 19), Decimal("3.87")), private_credit.index_for_night(types.SimpleNamespace(rates=rates), loan, date(2026, 10, 27)))
        self.assertEqual((date(2026, 10, 19), Decimal("3.87")), swaps.fixing_for(types.SimpleNamespace(rates=rates), date(2026, 10, 27)))


if __name__ == "__main__":
    unittest.main()
