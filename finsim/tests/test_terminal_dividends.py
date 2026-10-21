"""WP-18 dividends, payments in lieu and split notices (TERMINAL_DESIGN.md §13.15, §11.2 DIVIDEND / DIVPAY, §5.4, §19.19).
Every expected number is worked by hand in a comment.  Scaffolding: the shorts runtime (orders + lending; §19.5's ABC
short) with the dividends handlers registered and the quote worker's ``dividends.upsert`` light command, so the
``dividends`` rows come only from the refresher's Yahoo events slot against a ``FakeProvider`` (no network)."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import date
from decimal import Decimal

from tests.terminal_helpers import US_EXCHANGE, assert_integrity, bal, buy, execute, snapshot
from tests import test_terminal_orders as sc
from tests import test_terminal_shorts as sh
from finsim.terminal import lifecycle
from finsim.terminal.products import dividends as dv
from finsim.terminal.quotes.providers import FakeProvider, ProviderSet
from finsim.terminal.quotes import worker as qworker

D = Decimal
AAPL_KEY = "T1:US_EXCHANGE:AAPL:2026-11-09"
ABC_KEY = "T1:US_EXCHANGE:ABC:2026-11-09"
EX_0040 = "2026-11-09T00:40:00-05:00"      # Mon 2026-11-09 00:40 ET (EST: DST ended 2026-11-01)
PAY_0040 = "2026-12-01T00:40:00-05:00"
SRC = "Yahoo chart events"


class DividendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-div-")
        self.rt = None

    def tearDown(self):
        if self.rt is not None:
            self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- scaffolding ----------------------------------------------------------------------------------------------------
    def start(self, at=sh.FRI_10):
        rt = sh.runtime(self.tmp, at=at)
        dv.register(rt.bus, rt)
        rt.dividends = dv                                    # what the integrator binds: services.dividends
        qworker.register(rt.bus)                             # the worker's light commands (dividends.upsert)
        self.fp = FakeProvider(rt.clock)
        self.refresher = qworker.RatesRefresher(ProviderSet.offline_set(self.fp, rt.clock), None, None, None, None, rt.instruments, rt.db, rt.bus, rt.clock,
                                                rt.cal, rt, rt.log)
        self.rt = rt
        return rt

    def positions(self):
        """§19.19 setup: short 1,000 ABC at 50.00 on Fri 10-16 (§19.5: borrow B-000001, collateral 1.02 × 1,000 × 50 = 51,000.00,
        fee 1,000 × 0.005 = 5.00, proceeds 49,995.00 receivable T+1), long 1,000 AAPL at 339.50 on Mon 10-19 (gross 339,500.00,
        fee 5.00, payable 339,505.00 T+1 = Tue 10-20); both settled on Tue 10-20.  US_EXCHANGE was funded 600,000.00, so after
        the settlements 1010 = 600,000 − 51,000 + 49,995 − 339,505 = 259,490.00."""
        rt = self.rt
        sh.setup_abc(rt, fund_c=60_000_000)
        sh.short_abc(self, rt)
        rt.clock.set("2026-10-19T10:00:00-04:00")
        self.assertTrue(buy(rt, US_EXCHANGE, "AAPL", 1000, "339.50").ok)
        rt.clock.set("2026-10-20T10:00:00-04:00")
        r = execute(rt, "test.settle_due", {"as_of": "2026-10-20"}, actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(2, len(r.body["settled"]))
        self.assertEqual(25_949_000, bal(rt, US_EXCHANGE, "1010"))

    def events(self, aapl=(("2026-11-09", "0.26"),), abc=(("2026-11-09", "0.10"),), splits=()):
        """The worker path: FakeProvider events -> the refresher's dividends slot -> ``dividends.upsert`` rows."""
        rt = self.rt
        self.fp.set_events("AAPL", dividends=list(aapl), splits=list(splits))
        self.fp.set_events("ABC", dividends=list(abc))
        slot = self.refresher.slot("dividends")
        self.assertTrue(self.refresher.run_slot(slot, rt.clock.epoch()), slot.last_error)
        return sc.q(rt, "SELECT instrument_id, ex_date, amount, pay_date, source FROM dividends ORDER BY instrument_id, ex_date")

    def ex(self, key=AAPL_KEY, request_id=None):
        account, iid, ex = key.rsplit(":", 2)
        return execute(self.rt, "div.ex", {"account_id": account, "instrument_id": iid, "ex_date": ex, "key": key}, request_id or f"job:DIVIDEND:{key}", actor="lifecycle")

    def pay(self, key=AAPL_KEY, request_id=None):
        account, iid, ex = key.rsplit(":", 2)
        return execute(self.rt, "div.pay", {"account_id": account, "instrument_id": iid, "ex_date": ex, "key": key}, request_id or f"job:DIVPAY:{key}", actor="lifecycle")

    def keys(self, which="ex"):
        with self.rt.db.reader() as conn:
            fn = dv.ex_keys if which == "ex" else dv.pay_keys
            return fn(conn, self.rt.clock.epoch())

    def entry(self, idem):
        return sc.one(self.rt, "SELECT entry_id, kind, memo, business_date, ref_type, ref_id FROM journal_entries WHERE idem_key=?", (idem,))

    def n_entries(self):
        return sc.q(self.rt, "SELECT COUNT(*) AS n FROM journal_entries")[0]["n"]

    def jobs(self, job):
        return [(r["key"], r["status"]) for r in sc.q(self.rt, "SELECT key, status FROM jobs_done WHERE job=? ORDER BY key", (job,))]

    def notes(self, iid, today):
        with self.rt.db.reader() as conn:
            return dv.position_notes(conn, self.rt.cal, US_EXCHANGE, iid, today)

    def pending(self, iid):
        with self.rt.db.reader() as conn:
            return dv.income_pending_c(conn, US_EXCHANGE, iid)

    # ---- §19.19: the long dividend, ex-date to pay date -------------------------------------------------------------------
    def test_19_19_long_dividend_ex_date_notes_and_pay_date(self):
        rt = self.start()
        self.positions()
        # the refresher asks Yahoo (the fake) for the two held names and upserts ex-date + amount only: pay_date NULL
        rows = self.events()
        self.assertEqual(2, self.fp.calls["events"])
        # amounts are stored in the canonical price form (qprice: trailing zeros dropped), so "0.10" is "0.1"
        self.assertEqual([("AAPL", "2026-11-09", "0.26", None, SRC), ("ABC", "2026-11-09", "0.1", None, SRC)], [tuple(r.values()) for r in rows])
        # Mon 2026-11-09 00:39: not due yet (DUE_ET 00:40); 00:40: both keys, AAPL first
        rt.clock.set("2026-11-09T00:39:00-05:00")
        self.assertEqual([], self.keys())
        rt.clock.set(EX_0040)
        self.assertEqual([(US_EXCHANGE, "AAPL", "2026-11-09"), (US_EXCHANGE, "ABC", "2026-11-09")], self.keys())
        self.assertEqual([], self.keys("pay"))
        n0 = self.n_entries()
        r = self.ex()
        self.assertTrue(r.ok, r.body)
        b = r.body
        # 1,000 × 0.26 = 260.00 -> 26000 cents; pay = ex + 15 NYSE business days = 2026-12-01 (11-26 Thanksgiving skipped)
        self.assertEqual(("DONE", True, "dividend", "LONG", 1000, 0, "0.26", 26000, "USD", "2026-11-09", "2026-12-01", True),
                         (b["status"], b["posted"], b["kind"], b["side"], b["qty"], b["fills_since_ex"], b["amount"], b["amount_c"], b["currency"], b["ex_date"],
                          b["pay_date"], b["pay_date_convention"]))
        self.assertEqual("pays 2026-12-01 (convention: ex-date + 15 business days)", b["pay_label"])
        self.assertEqual(dv.CONVENTION, b["convention"])
        self.assertEqual([("1210", 26000, "AAPL"), ("4200", -26000, "AAPL")], sh.lines_of(rt, "div:" + AAPL_KEY))
        e = self.entry("div:" + AAPL_KEY)
        self.assertEqual(("DIVIDEND", "2026-11-09", "dividend", AAPL_KEY), (e["kind"], e["business_date"], e["ref_type"], e["ref_id"]))
        # the convention is labelled on the row: the pay-date source and the DIVIDEND sentence are in the memo
        self.assertIn("AAPL dividend 0.26 × 1,000 held at the prior close (2026-11-06) = 260.00 receivable, ex 2026-11-09", e["memo"])
        self.assertIn("pays 2026-12-01 (convention: ex-date + 15 business days); DIVIDEND: Dividends come from Yahoo's chart events", e["memo"])
        self.assertEqual(n0 + 1, self.n_entries())
        self.assertEqual([(AAPL_KEY, "DONE")], self.jobs("DIVIDEND"))
        note = sc.one(rt, "SELECT note FROM jobs_done WHERE job='DIVIDEND' AND key=?", (AAPL_KEY,))["note"]
        self.assertIn("dividend 260.00 on 1,000 AAPL at 0.26 (ex 2026-11-09; pays 2026-12-01 (convention: ex-date + 15 business days); source Yahoo chart events)", note)
        # the position row: income_pending_c 26000 (1210 − 2320), income to date 26000; the note with the business-day count
        self.assertEqual(26000, self.pending("AAPL"))
        p = rt.accounting.position(US_EXCHANGE, "AAPL")
        self.assertEqual((1000, 26000, 26000, 0), (p.qty, p.income_pending_c, p.income_itd_c, p.financing_itd_c))
        self.assertEqual(["dividend 0.26 ex 2026-11-09, pays 2026-12-01 (convention: ex + 15 business days: 11-10…11-13, 11-16…11-20, 11-23…11-25, 11-27, 11-30, "
                          "12-01; 11-26 is Thanksgiving Day) — accrued 260.00 receivable [Yahoo chart events]"], self.notes("AAPL", date(2026, 11, 9)))
        # the AAPL key is no longer due; ABC (not run here) still is
        self.assertEqual([(US_EXCHANGE, "ABC", "2026-11-09")], self.keys())
        # rerun: the same request id is replayed; a new id (the scheduler's :r1) finds the key DONE and posts nothing
        again = self.ex()
        self.assertTrue(again.replayed)
        self.assertEqual(b, again.body)
        r1 = self.ex(request_id=f"job:DIVIDEND:{AAPL_KEY}:r1")
        self.assertTrue(r1.ok, r1.body)
        self.assertEqual((True, False, "DONE"), (r1.body["already"], r1.body["posted"], r1.body["status"]))
        self.assertEqual(n0 + 1, self.n_entries())
        # a buy of 500 more at 10:00 on the ex-date: the position is 1,500 but the prior close held 1,000 -> nothing more is earned
        rt.clock.set("2026-11-09T10:00:00-05:00")
        self.assertTrue(buy(rt, US_EXCHANGE, "AAPL", 500, "340.00").ok)
        with rt.db.reader() as conn:
            self.assertEqual((1000, 500), dv.position_at_prior_close(conn, US_EXCHANGE, "AAPL", "2026-11-09"))
            self.assertEqual(1500, conn.execute("SELECT qty FROM v_positions WHERE account_id=? AND instrument_id='AAPL'", (US_EXCHANGE,)).fetchone()["qty"])
        self.assertEqual([(US_EXCHANGE, "ABC", "2026-11-09")], self.keys())
        self.assertEqual(26000, self.pending("AAPL"))
        # the buy settles T+1 (Tue 11-10): 500 × 340.00 + fee 2.50 = 170,002.50 leaves cash (L21 would flag a settlement left pending for weeks)
        rt.clock.set("2026-11-10T10:00:00-05:00")
        self.assertEqual(1, len(execute(rt, "test.settle_due", {"as_of": "2026-11-10"}, actor="lifecycle").body["settled"]))
        # DIVPAY: nothing on 11-30, nothing at 00:39 on 12-01, due at 00:40; ABC is not in the pay keys (its ex-date entry was never posted)
        rt.clock.set("2026-11-30T09:00:00-05:00")
        self.assertEqual([], self.keys("pay"))
        rt.clock.set("2026-12-01T00:39:00-05:00")
        self.assertEqual([], self.keys("pay"))
        rt.clock.set(PAY_0040)
        self.assertEqual([(US_EXCHANGE, "AAPL", "2026-11-09")], self.keys("pay"))
        cash0 = bal(rt, US_EXCHANGE, "1010")
        r = self.pay()
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("DONE", True, "dividend", "LONG", 26000, "2026-12-01", True, None, 0), (r.body["status"], r.body["posted"], r.body["kind"], r.body["side"],
                                                                                                  r.body["amount_c"], r.body["pay_date"], r.body["pay_date_convention"],
                                                                                                  r.body["funded"], r.body["unfunded_c"]))
        self.assertEqual([("1010", 26000, None), ("1210", -26000, "AAPL")], sh.lines_of(rt, "divpay:" + AAPL_KEY))
        e = self.entry("divpay:" + AAPL_KEY)
        self.assertEqual(("DIVIDEND", "2026-12-01", AAPL_KEY), (e["kind"], e["business_date"], e["ref_id"]))
        self.assertIn("AAPL dividend 260.00 received (ex 2026-11-09; pays 2026-12-01 (convention: ex-date + 15 business days); DIVIDEND:", e["memo"])
        self.assertEqual(cash0 + 26000, bal(rt, US_EXCHANGE, "1010"))
        self.assertEqual(0, self.pending("AAPL"))
        self.assertEqual([(AAPL_KEY, "DONE")], self.jobs("DIVPAY"))
        self.assertEqual([], self.keys("pay"))
        self.assertEqual(["dividend 0.26 ex 2026-11-09, pays 2026-12-01 (convention: ex + 15 business days: 11-10…11-13, 11-16…11-20, 11-23…11-25, 11-27, 11-30, "
                          "12-01; 11-26 is Thanksgiving Day) — 260.00 received [Yahoo chart events]"], self.notes("AAPL", date(2026, 12, 1)))
        r1 = self.pay(request_id=f"job:DIVPAY:{AAPL_KEY}:r1")
        self.assertTrue(r1.ok, r1.body)
        self.assertEqual((True, False), (r1.body["already"], r1.body["posted"]))
        self.assertEqual(n0 + 4, self.n_entries())                       # the ex entry, the 500-share buy, its settlement, the pay entry
        assert_integrity(self, rt, "full")

    # ---- §19.19: the short's payment in lieu and the P&L explain --------------------------------------------------------------
    def test_19_19_payment_in_lieu_and_pnl_explain(self):
        rt = self.start()
        self.positions()
        self.events()
        # a baseline snapshot of the account at Sun 11-08 17:46 (marks unchanged afterwards, so Δunrealised = 0 below)
        rt.clock.set("2026-11-08T17:46:00-05:00")
        snapshot(rt, US_EXCHANGE, date(2026, 11, 8))
        rt.clock.set(EX_0040)
        n0 = self.n_entries()
        for account, iid, ex in self.keys():
            r = self.ex(f"{account}:{iid}:{ex}")
            self.assertTrue(r.ok, r.body)
        # ABC: short 1,000 at the prior close × 0.10 = 100.00 payment in lieu: Dr 5400 / Cr 2320 (a financing cost)
        self.assertEqual([("5400", 10000, "ABC"), ("2320", -10000, "ABC")], sh.lines_of(rt, "div:" + ABC_KEY))
        e = self.entry("div:" + ABC_KEY)
        self.assertIn("ABC payment in lieu 0.1 × 1,000 short at the prior close (2026-11-06) = 100.00 payable, ex 2026-11-09; pays 2026-12-01 (convention", e["memo"])
        self.assertEqual("2026-11-09", e["business_date"])
        self.assertEqual(n0 + 2, self.n_entries())
        self.assertEqual([(AAPL_KEY, "DONE"), (ABC_KEY, "DONE")], self.jobs("DIVIDEND"))
        self.assertEqual(-10000, self.pending("ABC"))
        p = rt.accounting.position(US_EXCHANGE, "ABC")
        self.assertEqual((-1000, -10000, 0, -10000), (p.qty, p.income_pending_c, p.income_itd_c, p.financing_itd_c))
        self.assertEqual(["payment in lieu 0.1 ex 2026-11-09, pays 2026-12-01 (convention: ex + 15 business days: 11-10…11-13, 11-16…11-20, 11-23…11-25, 11-27, "
                          "11-30, 12-01; 11-26 is Thanksgiving Day) — accrued 100.00 payable [Yahoo chart events]"], self.notes("ABC", date(2026, 11, 9)))
        # P&L explain (§6.5) against the 11-08 snapshot: Δincome +260.00, Δfinancing −100.00 (a positive cost of 100.00 in the components,
        # subtracted in the explain); NAV moved by the receivable 260.00 less the payable 100.00 = +160.00; flows 0; residual 0
        v = rt.accounting.pnl(US_EXCHANGE, "TODAY")
        self.assertEqual((16000, 16000, 0, 0, False), (v.pnl_c, v.explained_c, v.residual_c, v.flows_c, v.degraded))
        self.assertEqual({"realised_c": 0, "income_c": 26000, "financing_c": 10000, "fees_c": 0, "unrealised_c": 0, "fx_result_c": 0}, v.components)
        self.assertEqual("2026-11-08", v.baseline["snap_date"])
        # pay date: both keys due at 00:40 on 12-01; the short pays 100.00 from settled cash (funded, no shortfall), the long receives 260.00
        rt.clock.set(PAY_0040)
        self.assertEqual([(US_EXCHANGE, "AAPL", "2026-11-09"), (US_EXCHANGE, "ABC", "2026-11-09")], self.keys("pay"))
        cash0 = bal(rt, US_EXCHANGE, "1010")
        for account, iid, ex in self.keys("pay"):
            r = self.pay(f"{account}:{iid}:{ex}")
            self.assertTrue(r.ok, r.body)
            self.assertTrue(r.body["posted"], r.body)
        self.assertEqual([("2320", 10000, "ABC"), ("1010", -10000, None)], sh.lines_of(rt, "divpay:" + ABC_KEY))
        self.assertEqual(("DONE", True, "payment in lieu", "SHORT", 10000, True, 0), tuple(r.body[k] for k in ("status", "posted", "kind", "side", "amount_c", "funded", "unfunded_c")))
        self.assertEqual("2026-12-01", self.entry("divpay:" + ABC_KEY)["business_date"])
        self.assertEqual(cash0 + 26000 - 10000, bal(rt, US_EXCHANGE, "1010"))
        self.assertEqual((0, 0), (self.pending("AAPL"), self.pending("ABC")))
        self.assertEqual([(AAPL_KEY, "DONE"), (ABC_KEY, "DONE")], self.jobs("DIVPAY"))
        self.assertEqual([], self.keys("pay"))
        self.assertEqual([], self.keys())
        # the pays move cash against the receivable / payable: the explain is unchanged against the same baseline
        v = rt.accounting.pnl(US_EXCHANGE, "TODAY")
        self.assertEqual((16000, 16000, 0, 26000, 10000), (v.pnl_c, v.explained_c, v.residual_c, v.components["income_c"], v.components["financing_c"]))
        self.assertEqual(n0 + 4, self.n_entries())
        self.assertEqual([], sc.q(rt, "SELECT 1 FROM attention WHERE code IN ('FUNDING_SHORTFALL', ?) AND resolved_ts IS NULL", (dv.CODE_EX_NOT_POSTED,)))
        assert_integrity(self, rt, "full")

    # ---- nothing posts without a Yahoo row; the guards ------------------------------------------------------------------------------
    def test_nothing_posts_without_a_row_and_the_guards(self):
        rt = self.start()
        self.positions()
        rt.clock.set(EX_0040)
        n0 = self.n_entries()
        self.assertEqual([], self.keys())
        r = self.ex()
        self.assertEqual((400, "NO_DIVIDEND_ROW"), (r.http, r.code))
        self.assertIn("rows come only from Yahoo chart events", r.message)
        self.assertEqual((n0, [], []), (self.n_entries(), self.jobs("DIVIDEND"), sc.q(rt, "SELECT 1 FROM attention")))
        # a row for a non-dividend kind (an FX pair) is refused, never posted
        r = execute(rt, "dividends.upsert", {"instrument_id": "EURUSD", "source": SRC, "dividends": [["2026-11-09", "0.01"]], "splits": [], "held_accounts": []}, actor="worker")
        self.assertTrue(r.ok, r.body)
        r = self.ex("T1:US_EXCHANGE:EURUSD:2026-11-09")
        self.assertEqual((400, "NOT_A_DIVIDEND_KIND"), (r.http, r.code))
        # the AAPL row through the worker path, then the date guards: nothing before the ex-date, no cash before the pay date
        self.events(abc=())
        rt.clock.set("2026-11-08T12:00:00-05:00")
        r = self.ex()
        self.assertEqual((400, "EX_DATE_NOT_REACHED"), (r.http, r.code))
        r = self.pay()
        self.assertEqual((400, "PAY_DATE_NOT_REACHED"), (r.http, r.code))
        self.assertIn("pays 2026-12-01 (convention: ex-date + 15 business days)", r.message)
        self.assertEqual((n0, []), (self.n_entries(), self.jobs("DIVPAY")))
        # unknown account / bad payload
        r = self.ex("T1:NOPE:AAPL:2026-11-09")
        self.assertEqual((400, "ACCOUNT_UNKNOWN"), (r.http, r.code))
        r = execute(rt, "div.ex", {"account_id": US_EXCHANGE, "instrument_id": "AAPL"}, actor="lifecycle")
        self.assertEqual((400, "BAD_PAYLOAD"), (r.http, r.code))
        # on the pay date without the ex-date entry: SKIPPED EX_NOT_POSTED, one open WARN, retried (a second attempt adds no second item)
        rt.clock.set(PAY_0040)
        self.assertEqual([], self.keys("pay"))
        r = self.pay()
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("SKIPPED", True, dv.CODE_EX_NOT_POSTED), (r.body["status"], r.body["retry"], r.body["code"]))
        self.assertEqual([(AAPL_KEY, "SKIPPED")], self.jobs("DIVPAY"))
        att = sc.q(rt, "SELECT severity, source, code, account_id, ref_id, resolved_ts FROM attention WHERE code=?", (dv.CODE_EX_NOT_POSTED,))
        self.assertEqual([("WARN", "dividends", dv.CODE_EX_NOT_POSTED, US_EXCHANGE, AAPL_KEY, None)], [tuple(a.values()) for a in att])
        r = self.pay(request_id=f"job:DIVPAY:{AAPL_KEY}:r1")
        self.assertEqual("SKIPPED", r.body["status"])
        self.assertEqual(1, len(sc.q(rt, "SELECT 1 FROM attention WHERE code=?", (dv.CODE_EX_NOT_POSTED,))))
        # the ex-date key catches up (the ex-date has passed; 1,000 held at the prior close -> 260.00 on business date 11-09), then the pay completes
        r = self.ex()
        self.assertTrue(r.ok, r.body)
        self.assertEqual((26000, 1000), (r.body["amount_c"], r.body["qty"]))
        self.assertEqual("2026-11-09", self.entry("div:" + AAPL_KEY)["business_date"])
        self.assertEqual([(US_EXCHANGE, "AAPL", "2026-11-09")], self.keys("pay"))
        r = self.pay(request_id=f"job:DIVPAY:{AAPL_KEY}:r2")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((True, 26000), (r.body["posted"], r.body["amount_c"]))
        self.assertEqual([(AAPL_KEY, "DONE")], self.jobs("DIVPAY"))
        self.assertIsNotNone(sc.one(rt, "SELECT resolved_ts FROM attention WHERE code=?", (dv.CODE_EX_NOT_POSTED,))["resolved_ts"])
        # an ex-date before the first fill (bought 10-19; ex 10-15): no key is derived, a direct call is a DONE no-op, nothing posts
        r = execute(rt, "dividends.upsert", {"instrument_id": "AAPL", "source": SRC, "dividends": [["2026-10-15", "0.25"]], "splits": [], "held_accounts": [US_EXCHANGE]},
                    actor="worker")
        self.assertTrue(r.ok, r.body)
        self.assertEqual([], self.keys())
        n1 = self.n_entries()
        r = self.ex("T1:US_EXCHANGE:AAPL:2026-10-15")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("DONE", False, "NO_POSITION_AT_PRIOR_CLOSE"), (r.body["status"], r.body["posted"], r.body["code"]))
        self.assertEqual(n1, self.n_entries())
        # ... and its pay key is a no-op too (nothing was accrued)
        r = self.pay("T1:US_EXCHANGE:AAPL:2026-10-15")
        self.assertEqual(("DONE", False, "NOTHING_ACCRUED"), (r.body["status"], r.body["posted"], r.body["code"]))
        self.assertEqual(n1, self.n_entries())
        assert_integrity(self, rt, "full")

    # ---- splits: Attention WARN only -----------------------------------------------------------------------------------------------------
    def test_split_event_is_a_warning_only(self):
        rt = self.start()
        self.positions()
        n0 = self.n_entries()
        lots0 = sc.q(rt, "SELECT qty_remaining, unit_cost FROM lots WHERE account_id=? AND instrument_id='AAPL'", (US_EXCHANGE,))
        self.assertEqual([(1000, D("339.50"))], [(l["qty_remaining"], D(l["unit_cost"])) for l in lots0])
        # the worker path: a 4:1 split event on 11-12 -> one WARN per holding account, the dividend row as before
        rows = self.events(splits=(("2026-11-12", "4:1"),))
        self.assertEqual(2, len(rows))
        att = sc.q(rt, "SELECT severity, source, code, account_id, ref_id, resolved_ts FROM attention WHERE code=?", (dv.SPLIT_CODE,))
        self.assertEqual([("WARN", "dividends", dv.SPLIT_CODE, US_EXCHANGE, "AAPL:2026-11-12", None)], [tuple(a.values()) for a in att])
        self.assertIn("manual", sc.one(rt, "SELECT message FROM attention WHERE code=?", (dv.SPLIT_CODE,))["message"])
        # no posting, lots unchanged (disclosed), no job row
        self.assertEqual(n0, self.n_entries())
        self.assertEqual(lots0, sc.q(rt, "SELECT qty_remaining, unit_cost FROM lots WHERE account_id=? AND instrument_id='AAPL'", (US_EXCHANGE,)))
        self.assertEqual([], sc.q(rt, "SELECT 1 FROM jobs_done"))
        # div.split_notice for the same event adds no second open item; a new event adds one; a name nobody holds gets none
        r = execute(rt, "div.split_notice", {"instrument_id": "AAPL", "date": "2026-11-12", "ratio": "4:1"}, actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((0, [US_EXCHANGE], False), (r.body["notices"], r.body["accounts"], r.body["posted"]))
        r = execute(rt, "div.split_notice", {"instrument_id": "AAPL", "date": "2026-11-20", "ratio": "2:1"}, actor="lifecycle")
        self.assertEqual((1, [US_EXCHANGE]), (r.body["notices"], r.body["accounts"]))
        r = execute(rt, "div.split_notice", {"instrument_id": "SPY", "date": "2026-11-20", "ratio": "2:1"}, actor="lifecycle")
        self.assertEqual((0, []), (r.body["notices"], r.body["accounts"]))
        self.assertEqual(2, len(sc.q(rt, "SELECT 1 FROM attention WHERE code=? AND resolved_ts IS NULL", (dv.SPLIT_CODE,))))
        self.assertEqual(n0, self.n_entries())
        r = execute(rt, "div.split_notice", {"instrument_id": "AAPL"}, actor="lifecycle")
        self.assertEqual((400, "BAD_PAYLOAD"), (r.http, r.code))
        # the position notes carry the dividend (upcoming) and both split notices
        notes = self.notes("AAPL", date(2026, 11, 2))
        self.assertEqual(3, len(notes))
        self.assertTrue(notes[0].startswith("dividend 0.26 ex 2026-11-09, pays 2026-12-01 (convention"))
        self.assertTrue(notes[0].endswith("— upcoming [Yahoo chart events]"))
        self.assertEqual(["split notice: AAPL: split 4:1 on 2026-11-12 — lots are not adjusted in this build; review the position manually",
                          "split notice: AAPL: split 2:1 on 2026-11-20 — lots are not adjusted in this build; review the position manually"], notes[1:])
        assert_integrity(self, rt, "full")

    # ---- catch-up after downtime --------------------------------------------------------------------------------------------------------
    def test_catch_up_after_downtime(self):
        rt = self.start()
        self.positions()
        self.events()
        # the terminal was down on the ex-date; a buy of 500 AAPL on Tue 11-10 happened before it came back on Thu 11-12
        rt.clock.set("2026-11-10T10:00:00-05:00")
        self.assertTrue(buy(rt, US_EXCHANGE, "AAPL", 500, "340.00").ok)
        rt.clock.set("2026-11-12T09:00:00-05:00")
        # its T+1 settlement (Wed 11-11: 500 × 340.00 + fee 2.50 = 170,002.50) is caught up first, as SETTLE_DUE would
        self.assertEqual(1, len(execute(rt, "test.settle_due", {"as_of": "2026-11-12"}, actor="lifecycle").body["settled"]))
        self.assertEqual([(US_EXCHANGE, "AAPL", "2026-11-09"), (US_EXCHANGE, "ABC", "2026-11-09")], self.keys())
        self.assertEqual([], self.keys("pay"))
        n0 = self.n_entries()
        out = {}
        for account, iid, ex in self.keys():
            r = self.ex(f"{account}:{iid}:{ex}")
            self.assertTrue(r.ok, r.body)
            out[iid] = r.body
        # AAPL: 1,500 held now, 1,000 at the prior close -> 260.00 (not 1,500 × 0.26 = 390.00); ABC: 100.00; both on business date 11-09
        self.assertEqual((26000, 1000, 500), (out["AAPL"]["amount_c"], out["AAPL"]["qty"], out["AAPL"]["fills_since_ex"]))
        self.assertEqual((10000, -1000, 0), (out["ABC"]["amount_c"], out["ABC"]["qty"], out["ABC"]["fills_since_ex"]))
        self.assertEqual(("2026-11-09", "2026-11-09"), (self.entry("div:" + AAPL_KEY)["business_date"], self.entry("div:" + ABC_KEY)["business_date"]))
        self.assertEqual(n0 + 2, self.n_entries())
        self.assertEqual([], self.keys())
        # down again across the pay date; back on Thu 12-03: both pays are due, posted on business date 12-01, then nothing is due
        rt.clock.set("2026-12-03T09:00:00-05:00")
        self.assertEqual([(US_EXCHANGE, "AAPL", "2026-11-09"), (US_EXCHANGE, "ABC", "2026-11-09")], self.keys("pay"))
        cash0 = bal(rt, US_EXCHANGE, "1010")
        for account, iid, ex in self.keys("pay"):
            r = self.pay(f"{account}:{iid}:{ex}")
            self.assertTrue(r.ok, r.body)
            self.assertEqual("2026-12-01", r.body["pay_date"])
        self.assertEqual(("2026-12-01", "2026-12-01"), (self.entry("divpay:" + AAPL_KEY)["business_date"], self.entry("divpay:" + ABC_KEY)["business_date"]))
        self.assertEqual(cash0 + 26000 - 10000, bal(rt, US_EXCHANGE, "1010"))
        self.assertEqual(([], []), (self.keys(), self.keys("pay")))
        self.assertEqual(4, sc.q(rt, "SELECT COUNT(*) AS n FROM journal_entries WHERE kind='DIVIDEND'")[0]["n"])
        self.assertEqual((0, 0), (self.pending("AAPL"), self.pending("ABC")))
        assert_integrity(self, rt, "full")

    # ---- through the WP-08 scheduler (ProductJob -> div.ex / div.pay) ---------------------------------------------------------------
    def test_scheduler_runs_the_dividend_jobs(self):
        rt = self.start()
        self.positions()
        self.events()
        lifecycle.register(rt.bus, rt)
        rt.lifecycle = lifecycle.LifecycleScheduler(rt.bus, rt.db, rt, rt.clock, rt.log)
        out = rt.lifecycle.tick(at="2026-11-09T00:39:00-05:00", only="DIVIDEND")
        self.assertEqual(([], {}), (out["ran"], out["skipped_modules"]))
        out = rt.lifecycle.tick(at=EX_0040, only="DIVIDEND")
        self.assertEqual([(AAPL_KEY, "DONE", "div.ex", f"job:DIVIDEND:{AAPL_KEY}"), (ABC_KEY, "DONE", "div.ex", f"job:DIVIDEND:{ABC_KEY}")],
                         [(i["key"], i["status"], i["kind"], i["request_id"]) for i in out["ran"]])
        self.assertEqual([("1210", 26000, "AAPL"), ("4200", -26000, "AAPL")], sh.lines_of(rt, "div:" + AAPL_KEY))
        self.assertEqual([("5400", 10000, "ABC"), ("2320", -10000, "ABC")], sh.lines_of(rt, "div:" + ABC_KEY))
        self.assertEqual([], rt.lifecycle.tick(only="DIVIDEND")["ran"])
        self.assertEqual([], rt.lifecycle.tick(at="2026-11-30T09:00:00-05:00", only="DIVPAY")["ran"])
        out = rt.lifecycle.tick(at=PAY_0040, only="DIVPAY")
        self.assertEqual([(AAPL_KEY, "DONE", "div.pay"), (ABC_KEY, "DONE", "div.pay")], [(i["key"], i["status"], i["kind"]) for i in out["ran"]])
        self.assertEqual([("1010", 26000, None), ("1210", -26000, "AAPL")], sh.lines_of(rt, "divpay:" + AAPL_KEY))
        self.assertEqual([("2320", 10000, "ABC"), ("1010", -10000, None)], sh.lines_of(rt, "divpay:" + ABC_KEY))
        self.assertEqual([], rt.lifecycle.tick(only="DIVPAY")["ran"])
        self.assertEqual([(AAPL_KEY, "DONE"), (ABC_KEY, "DONE")], self.jobs("DIVPAY"))
        st = {j["name"]: j for j in rt.lifecycle.status()["jobs"]}
        self.assertEqual((True, ABC_KEY, "DONE"), (st["DIVPAY"]["available"], st["DIVPAY"]["last_key"], st["DIVPAY"]["last_status"]))
        self.assertEqual((True, "DONE"), (st["DIVIDEND"]["available"], st["DIVIDEND"]["last_status"]))
        assert_integrity(self, rt, "full")

    # ---- the pure helpers -------------------------------------------------------------------------------------------------------------
    def test_pay_date_convention_and_pure_helpers(self):
        rt = self.start()
        cal = rt.cal
        # 1,000 × 0.26 = 260.00; a short's 1,000 × 0.10 = 100.00; 3 × 0.125 = 0.375 -> 0.38 (HALF_EVEN); 1 × 0.125 = 0.125 -> tie -> even -> 0.12
        self.assertEqual((26000, 10000, 38, 12), (dv.entitled_c(1000, "0.26"), dv.entitled_c(-1000, "0.10"), dv.entitled_c(3, "0.125"), dv.entitled_c(1, "0.125")))
        self.assertEqual((1000, -1000), (dv.signed_fill("BUY", 1000), dv.signed_fill("SELL", 1000)))
        # Yahoo's pay date wins when recorded; else ex + 15 NYSE business days: 11-10…13 (4), 11-16…20 (9), 11-23…25 (12), [11-26 Thanksgiving],
        # 11-27 (13), 11-30 (14), 12-01 (15)
        self.assertEqual((date(2026, 11, 30), False), dv.pay_date_for(cal, date(2026, 11, 9), "2026-11-30"))
        self.assertEqual((date(2026, 12, 1), True), dv.pay_date_for(cal, date(2026, 11, 9), None))
        self.assertEqual(15, cal.business_days_between(date(2026, 11, 9), date(2026, 12, 1)))
        self.assertEqual((date(2026, 12, 1), "11-10…11-13, 11-16…11-20, 11-23…11-25, 11-27, 11-30, 12-01", "11-26 is Thanksgiving Day"),
                         dv.business_day_listing(cal, date(2026, 11, 9)))
        self.assertEqual((date(2026, 12, 1), True, "pays 2026-12-01 (convention: ex-date + 15 business days)"), dv.pay_label(cal, date(2026, 11, 9), None))
        self.assertEqual((date(2026, 11, 30), False, "pays 2026-11-30 (pay date from Yahoo)"), dv.pay_label(cal, date(2026, 11, 9), "2026-11-30"))
        # ex Fri 2026-12-18: 12-21…24 (4; 12-24 is an early close, still a business day), [12-25 Christmas], 12-28…31 (8), [01-01 New Year's],
        # 01-04…08 (13), 01-11 (14), 01-12 (15) -> 2027-01-12
        self.assertEqual((date(2027, 1, 12), "12-21…12-24, 12-28…12-31, 01-04…01-08, 01-11, 01-12", "12-25 is Christmas Day; 01-01 is New Year's Day"),
                         dv.business_day_listing(cal, date(2026, 12, 18)))
        self.assertEqual("Thanksgiving Day", dv.holiday_name(cal, date(2026, 11, 26)))
        self.assertEqual("a holiday", dv.holiday_name(cal, date(2026, 11, 25)))
        row = {"ex_date": "2026-11-09", "amount": "0.26", "pay_date": "2026-11-30"}
        self.assertEqual("dividend 0.26 ex 2026-11-09, pays 2026-11-30 (pay date from Yahoo)", dv.row_note(cal, row, 1000))
        self.assertEqual("payment in lieu 0.26 ex 2026-11-09, pays 2026-11-30 (pay date from Yahoo) — upcoming", dv.row_note(cal, row, -1000, "upcoming"))
        self.assertEqual(dv.CONVENTION, "Dividends come from Yahoo's chart events: income on the ex-date for long positions, a payment in lieu for shorts; "
                                        "cash on the pay date when Yahoo gives one, else 15 business days after the ex-date (convention).")
        # events_for reads the bound fake provider (worker side only): the one AAPL event; nothing for an FX pair or an unknown name
        self.fp.set_events("AAPL", dividends=[("2026-11-09", "0.26")])
        rt.providers = ProviderSet.offline_set(self.fp, rt.clock)
        ev = dv.events_for(rt, rt.instruments.get("AAPL"))
        self.assertEqual([(date(2026, 11, 9), D("0.26"))], [(e.ex_date, e.amount) for e in ev])
        self.assertEqual([], dv.events_for(rt, rt.instruments.get("EURUSD")))
        self.assertEqual([], dv.events_for(rt, None))


if __name__ == "__main__":
    unittest.main()
