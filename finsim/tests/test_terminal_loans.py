"""WP-05 currency-loan and PB-financing tests (TERMINAL_DESIGN.md §13.6, §13.7, §11.3, §12.2, §19.6 last bullet, §19.23).
Every expected number is worked by hand in a comment.  The fixture (``runtime``) and the local ``accrue_segment`` copy
come from ``tests.test_terminal_fx``; no network, FakeClock, FixedMarks, a temp database."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import date
from decimal import Decimal

from tests.terminal_helpers import CREDIT, FOREIGN_FX, T1, TREASURY, US_EXCHANGE, assert_integrity, bal, buy, deposit, execute, fund, pb
from tests.test_terminal_fx import accrue, lines_of, one, q, runtime
from finsim.terminal import books, credit, execution


def _h_fund(ctx, payload):
    """A test-only call of execution.fund (what a fill or a call does) so the PB-draw hook runs inside a command."""
    res = execution.fund(ctx, payload["account_id"], "USD", int(payload["need_c"]), payload.get("link_type", "fill"), payload.get("link_id", "x"), None,
                         immediate=True, overdraft=bool(payload.get("overdraft")))
    return res.to_dict()


class LoanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-loan-")
        self.rt = runtime(self.tmp)
        self.rt.bus.register("test.fund", _h_fund)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def draw(self, ccy, amount_c, account=CREDIT, term_days=None, request_id=None):
        return execute(self.rt, "credit.loan_draw", {"account_id": account, "currency": ccy, "amount_c": int(amount_c), "term_days": term_days}, request_id)

    def repay(self, loan_id, amount_c, request_id=None):
        return execute(self.rt, "credit.loan_repay", {"loan_id": loan_id, "amount_c": int(amount_c)}, request_id)

    # ---- draw, accrue, refusal, partial repay ---------------------------------------------------------------------------------------------
    def test_draw_accrue_repay_refusal_and_partial(self):
        rt = self.rt
        # Tue 10-20 11:00: EUR 1,000,000.00 for 30 days at ECBDFR 2.00 + 0.75 = 2.75 %; maturity 10-20 + 30 = Thu 2026-11-19 (a business day)
        r = self.draw("EUR", 100000000, term_days=30)
        self.assertTrue(r.ok, r.body)
        loan_id = r.body["loan_id"]
        self.assertEqual(("2026-11-19", 100000000, 0, 100000000, "OPEN"), (r.body["loan"]["maturity_date"], r.body["loan"]["principal_c"], r.body["loan"]["accrued_c"],
                                                                         r.body["loan"]["total_due_c"], r.body["loan"]["status"]))
        self.assertEqual([("LOAN_DRAWN", CREDIT, loan_id)], [(e["type"], e["account_id"], e["ref_id"]) for e in q(rt, "SELECT type, account_id, ref_id FROM events WHERE type='LOAN_DRAWN'")])
        # nights Tue and Wed at 2.75 %: 76.39 then cum to_c(152.777778) = 152.78 -> 76.39
        rt.clock.set("2026-10-22T00:35:00-04:00")
        self.assertEqual([7639, 7639], [accrue(rt, loan_id, d).body["amount_c"] for d in ("2026-10-20", "2026-10-21")])
        self.assertEqual(15278, credit_accrued(rt, loan_id))
        view = credit_view(rt, loan_id)
        self.assertEqual((100000000, 15278, 100015278, "2026-10-21", "2026-10-20", "0.0275"), (view["principal_c"], view["accrued_c"], view["total_due_c"], view["accrued_through"],
                                                                                              view["segment_start"], view["segment_rate"]))
        # repay 1,000,152.78 with EUR settled 1,000,000.00 -> 409 INSUFFICIENT_CASH_CCY, short 152.78
        r = self.repay(loan_id, 100015278)
        self.assertEqual((409, "INSUFFICIENT_CASH_CCY", "buy EUR 152.78 with Convert", 15278), (r.http, r.code, r.body["error"]["details"]["hint"], r.body["error"]["details"]["shortfall_c"]))
        # partial repay 500,152.78: accrued 152.78 first, then principal 500,000.00; principal left 500,000.00; settled EUR 1,000,000 − 500,152.78 = 499,847.22
        r = self.repay(loan_id, 50015278)
        self.assertTrue(r.ok, r.body)
        self.assertEqual((15278, 50000000, 50000000, False, 49984722), (r.body["interest_c"], r.body["principal_c"], r.body["principal_left_c"], r.body["closed"], r.body["settled_c"]))
        # base at 1.1238, plug on the largest line (1010): 152.78 × 1.1238 = to_c(171.694164) = 171.69; 500,000 × 1.1238 = 561,900.00; 1010 = −(171.69 + 561,900.00)
        self.assertEqual([("2755", "EUR", 15278, 17169, "1.1238"), ("2750", "EUR", 50000000, 56190000, "1.1238"), ("1010", "EUR", -50015278, -56207169, "1.1238")],
                         lines_of(rt, r.body["entry_id"]))
        self.assertEqual((50000000, 0, "OPEN", 50000000), (bal(rt, CREDIT, "2750", "EUR"), bal(rt, CREDIT, "2755", "EUR"), one(rt, "SELECT status FROM loans WHERE loan_id=?", (loan_id,))["status"],
                                                           one(rt, "SELECT principal_c FROM loans WHERE loan_id=?", (loan_id,))["principal_c"]))
        assert_integrity(self, rt, "full")                                                           # L14: Σ OPEN principal 500,000 = balance(2750)
        # night Thu on the new base: the segment restarts (n = 1): to_c(500,000 × 0.0275 / 360) = to_c(38.194444) = 38.19
        rt.clock.set("2026-10-23T00:35:00-04:00")
        a = accrue(rt, loan_id, "2026-10-22")
        self.assertEqual((3819, 3819, "2026-10-22"), (a.body["amount_c"], a.body["cum_c"], a.body["segment_start"]))
        # more than due (500,000.00 + 38.19 = 500,038.19) -> 400 AMOUNT_EXCEEDS; exactly due with 499,847.22 settled -> short 190.97
        r = self.repay(loan_id, 50003820)
        self.assertEqual((400, "AMOUNT_EXCEEDS", 50003819), (r.http, r.code, r.body["error"]["details"]["total_due_c"]))
        r = self.repay(loan_id, 50003819)
        self.assertEqual((409, "buy EUR 190.97 with Convert"), (r.http, r.body["error"]["details"]["hint"]))
        # Treasury deposits EUR 190.97 and advances it: settled 500,038.19 -> full repayment, loan CLOSED, EUR 0
        deposit(rt, 19097, "EUR")
        self.assertTrue(fund(rt, CREDIT, 19097, "EUR").ok)
        self.assertEqual(50003819, bal(rt, CREDIT, "1010", "EUR"))
        r = self.repay(loan_id, 50003819)
        self.assertTrue(r.ok, r.body)
        self.assertEqual((3819, 50000000, 0, True, 0), (r.body["interest_c"], r.body["principal_c"], r.body["principal_left_c"], r.body["closed"], r.body["settled_c"]))
        self.assertEqual(("CLOSED", 0, 0, 0), (one(rt, "SELECT status FROM loans WHERE loan_id=?", (loan_id,))["status"], bal(rt, CREDIT, "2750", "EUR"), bal(rt, CREDIT, "2755", "EUR"),
                                               credit_accrued(rt, loan_id)))
        # a closed loan accrues nothing and cannot be repaid again
        a = accrue(rt, loan_id, "2026-10-23")
        self.assertEqual((409, "LOAN_CLOSED"), (a.http, a.code))
        r = self.repay(loan_id, 1)
        self.assertEqual((409, "LOAN_CLOSED"), (r.http, r.code))
        # total financing posted = 76.39 + 76.39 + 38.19 = 190.97
        self.assertEqual(19097, bal(rt, CREDIT, "5150", "EUR"))
        with rt.db.reader() as conn:
            self.assertEqual([], credit.loans_of(conn, CREDIT))
        assert_integrity(self, rt, "full")

    # ---- unsupported currency, missing observation, missing FX rate, monthly proxy, wrong account --------------------------------------------------
    def test_unsupported_currency_and_missing_rate(self):
        rt = self.rt
        r = self.draw("MXN", 100000)
        self.assertEqual((400, "CURRENCY_UNSUPPORTED"), (r.http, r.code))
        self.assertEqual(sorted(["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD"]), r.body["error"]["details"]["supported"])
        # GBP has a series (IUDSOIA) but no observation loaded -> 409 RATE_UNAVAILABLE, nothing defaulted, nothing stored
        r = self.draw("GBP", 10000000)
        self.assertEqual((409, "RATE_UNAVAILABLE", "IUDSOIA"), (r.http, r.code, r.body["error"]["details"]["series"]))
        self.assertEqual(0, one(rt, "SELECT COUNT(*) n FROM loans")["n"])
        # with SONIA 3.75 (obs 10-19): GBP 100,000.00 at 4.50 % ACT/365; night Tue = to_c(100,000 × 0.045 / 365) = to_c(12.328767) = 12.33
        rt.rates.put("IUDSOIA", [(date(2026, 10, 19), Decimal("3.75"))], persist=False)
        r = self.draw("GBP", 10000000)
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("0.045", 365), (r.body["loan"]["rate"], r.body["loan"]["day_basis"]))
        rt.clock.set("2026-10-21T00:35:00-04:00")
        a = accrue(rt, r.body["loan_id"], "2026-10-20")
        self.assertEqual(1233, a.body["amount_c"])
        self.assertIn("IUDSOIA 3.75 % (2026-10-19) + 0.75 % = 4.50 %, ACT/365, night of 2026-10-20", one(rt, "SELECT memo FROM journal_entries WHERE entry_id=?", (a.body["entry_id"],))["memo"])
        # CHF: a policy series with an observation but no USD rate in the snapshot -> 409 FX_RATE_UNAVAILABLE (the loan cannot be valued in base)
        rt.rates.put("IRSTCI01CHM156N", [(date(2026, 9, 1), Decimal("0.25"))], persist=False)
        r = self.draw("CHF", 100000)
        self.assertEqual((409, "FX_RATE_UNAVAILABLE"), (r.http, r.code))
        # JPY: the monthly proxy is labelled; JPY 10,000,000 at 0.50 + 0.75 = 1.25 %; base at fx(JPY) = 1/158.10 -> 0.00632511: to_c(10,000,000 × 0.00632511) = 63,251.10
        rt.rates.put("IRSTCI01JPM156N", [(date(2026, 9, 1), Decimal("0.50"))], persist=False)
        r = self.draw("JPY", 1000000000)
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("0.0125", True), (r.body["loan"]["rate"], r.body["loan"]["monthly_proxy"]))
        self.assertIn("(monthly proxy)", r.body["memo"])
        self.assertEqual([("1010", "JPY", 1000000000, 6325110, "0.00632511"), ("2750", "JPY", -1000000000, -6325110, "0.00632511")], lines_of(rt, r.body["entry_id"]))
        # only the Credit Instruments account draws cash loans
        r = self.draw("USD", 100000, account=US_EXCHANGE)
        self.assertEqual((400, "NOT_CREDIT_ACCOUNT"), (r.http, r.code))
        r = self.draw("USD", 100000, account=TREASURY)
        self.assertEqual((400, "NOT_CREDIT_ACCOUNT"), (r.http, r.code))
        r = execute(rt, "credit.loan_draw", {"account_id": CREDIT, "currency": "USD", "amount_c": 100000, "term_days": -3})
        self.assertEqual((400, "TERM"), (r.http, r.code))
        assert_integrity(self, rt, "full")

    # ---- USD loan, same-day stock buy (§19.6 last bullet) ------------------------------------------------------------------------------------
    def test_usd_loan_same_day_stock_buy(self):
        rt = self.rt
        # SOFR 3.87 (obs 10-19) + 0.75 = 4.62 %: USD 500,000.00 drawn in CREDIT is available at once
        r = self.draw("USD", 50000000)
        self.assertTrue(r.ok, r.body)
        loan_id = r.body["loan_id"]
        self.assertEqual(("0.0462", "SOFR"), (r.body["loan"]["rate"], r.body["loan"]["index_name"]))
        self.assertEqual((50000000, 50000000), (bal(rt, CREDIT, "1010", "USD"), rt.accounting.available(CREDIT, "USD")))
        # transfer to US_EXCHANGE and buy 1,000 AAPL @ 339.50 the same day: gross 339,500.00, fee max(1,000 × 0.005, 1.00) = 5.00, payable 339,505.00 due 10-21
        t = execute(rt, "treasury.transfer", {"from_account": CREDIT, "to_account": US_EXCHANGE, "currency": "USD", "amount_c": 50000000})
        self.assertTrue(t.ok, t.body)
        b = buy(rt, US_EXCHANGE, "AAPL", 1000, "339.50")
        self.assertTrue(b.ok, b.body)
        self.assertEqual((33950000, 500, "2026-10-21"), (b.body["gross_c"], b.body["fee_c"], b.body["settle_date"]))
        # available = 500,000 − 339,505 = 160,495.00; the loan is a liability from the draw date
        self.assertEqual((16049500, 50000000, 0), (rt.accounting.available(US_EXCHANGE, "USD"), bal(rt, CREDIT, "2750", "USD"), bal(rt, CREDIT, "1010", "USD")))
        # interest accrues from the draw night: to_c(500,000 × 0.0462 / 360) = to_c(64.166667) = 64.17 (USD: base = amount)
        rt.clock.set("2026-10-21T00:35:00-04:00")
        a = accrue(rt, loan_id, "2026-10-20")
        self.assertEqual(6417, a.body["amount_c"])
        self.assertEqual([("5150", "USD", 6417, 6417, "1"), ("2755", "USD", -6417, -6417, "1")], lines_of(rt, a.body["entry_id"]))
        # NAV(T1:ALL) = 10,000,000 − 5.00 − 64.17 = 9,999,930.83; residual 0
        nav = rt.accounting.nav("T1:ALL")
        self.assertEqual((999993083, 6417, 500, 0, 0), (nav.nav_c, nav.financing_c, nav.fees_c, nav.unrealised_c, nav.fx_result_c))
        pnl = rt.accounting.pnl("T1:ALL", "ITD")
        self.assertEqual((-6917, 0), (pnl.pnl_c, pnl.residual_c))
        assert_integrity(self, rt, "full")

    # ---- §19.23 floating reset and early repayment -------------------------------------------------------------------------------------------
    def test_floating_reset_and_early_repayment(self):
        rt = self.rt
        r = self.draw("EUR", 100000000)
        loan_id = r.body["loan_id"]
        # nights 10-20 and 10-21 at 2.75 %: 76.39 / 76.39 (cum 152.78)
        rt.clock.set("2026-10-22T00:35:00-04:00")
        self.assertEqual([7639, 7639], [accrue(rt, loan_id, d).body["amount_c"] for d in ("2026-10-20", "2026-10-21")])
        # a new ECBDFR observation 2.25 % dated D3 = 10-22 -> rate 3.00 % from the night of 10-22 (RATE_LAG: latest observation ≤ the night)
        rt.rates.put("ECBDFR", [(date(2026, 10, 22), Decimal("2.25"))], persist=False)
        rt.clock.set("2026-10-23T00:35:00-04:00")
        a3 = accrue(rt, loan_id, "2026-10-22")
        # segment restart: night 3 = to_c(1,000,000 × 0.03 / 360) = to_c(83.333333) = 83.33
        self.assertEqual((8333, "0.03", "2026-10-22", "2.25", 8333, "2026-10-22"), (a3.body["amount_c"], a3.body["rate"], a3.body["index"]["obs_date"], a3.body["index"]["value"],
                                                                                a3.body["cum_c"], a3.body["segment_start"]))
        self.assertIn("ECBDFR 2.25 % (2026-10-22) + 0.75 % = 3.00 %, ACT/360, night of 2026-10-22", one(rt, "SELECT memo FROM journal_entries WHERE entry_id=?", (a3.body["entry_id"],))["memo"])
        # night 4: cum to_c(166.666667) = 166.67 -> posts 83.34
        rt.clock.set("2026-10-24T00:35:00-04:00")
        a4 = accrue(rt, loan_id, "2026-10-23")
        self.assertEqual((8334, 16667), (a4.body["amount_c"], a4.body["cum_c"]))
        self.assertEqual(("2026-10-22", "0.03", 16667, "2026-10-23"), tuple(one(rt, "SELECT segment_start, rate, cum_c, accrued_through FROM accrual_state WHERE kind='loan' AND ref_id=?", (loan_id,)).values()))
        # accrued 152.78 + 166.67 = 319.45; early repayment with EUR cash: Treasury deposits EUR 319.45 and advances it (settled 1,000,319.45)
        self.assertEqual(31945, credit_accrued(rt, loan_id))
        deposit(rt, 31945, "EUR")
        fund(rt, CREDIT, 31945, "EUR")
        rt.clock.set("2026-10-24T10:00:00-04:00")
        r = self.repay(loan_id, 100031945)
        self.assertTrue(r.ok, r.body)
        # Dr 2755 319.45 / Dr 2750 1,000,000.00 / Cr 1010:EUR 1,000,319.45; base at 1.1238 with the plug on 1010:
        # 319.45 × 1.1238 = to_c(358.99791) = 359.00; 1,000,000 × 1.1238 = 1,123,800.00; 1010 = −(359.00 + 1,123,800.00) = −1,124,159.00
        self.assertEqual([("2755", "EUR", 31945, 35900, "1.1238"), ("2750", "EUR", 100000000, 112380000, "1.1238"), ("1010", "EUR", -100031945, -112415900, "1.1238")],
                         lines_of(rt, r.body["entry_id"]))
        self.assertEqual((True, 0, "CLOSED", 0, 0), (r.body["closed"], r.body["settled_c"], one(rt, "SELECT status FROM loans WHERE loan_id=?", (loan_id,))["status"],
                                                   bal(rt, CREDIT, "2750", "EUR"), bal(rt, CREDIT, "2755", "EUR")))
        assert_integrity(self, rt, "full")

    # ---- maturity: repaid when funded, rolled with one WARN when not --------------------------------------------------------------------------
    def test_maturity_repays_or_rolls(self):
        rt = self.rt
        # EUR 1,000.00 for 2 days: maturity 10-22; the EUR is moved to the FX account so CREDIT holds none
        r = self.draw("EUR", 100000, term_days=2)
        loan_id = r.body["loan_id"]
        self.assertEqual("2026-10-22", r.body["loan"]["maturity_date"])
        self.assertTrue(execute(rt, "treasury.transfer", {"from_account": CREDIT, "to_account": FOREIGN_FX, "currency": "EUR", "amount_c": 100000}).ok)
        r = execute(rt, "credit.loan_mature", {"loan_id": loan_id}, actor="lifecycle")
        self.assertEqual((400, "NOT_DUE"), (r.http, r.code))
        # nights 10-20 and 10-21: to_c(1,000 × 0.0275 / 360) = to_c(0.076389) = 0.08; cum to_c(0.152778) = 0.15 -> 0.07; due 1,000.15
        rt.clock.set("2026-10-22T00:40:00-04:00")
        self.assertEqual([8, 7], [accrue(rt, loan_id, d).body["amount_c"] for d in ("2026-10-20", "2026-10-21")])
        r = execute(rt, "credit.loan_mature", {"loan_id": loan_id}, actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        # short of cash: rolled to the next business day (Fri 10-23) with ONE open WARN LOAN_ROLLED; nothing posted
        self.assertEqual((True, "2026-10-23", 100015), (r.body["rolled"], r.body["maturity_date"], r.body["total_due_c"]))
        self.assertEqual([("WARN", loan_id, None)], [(a["severity"], a["ref_id"], a["resolved_ts"]) for a in q(rt, "SELECT severity, ref_id, resolved_ts FROM attention WHERE code='LOAN_ROLLED'")])
        self.assertEqual((100000, "OPEN"), (bal(rt, CREDIT, "2750", "EUR"), one(rt, "SELECT status FROM loans WHERE loan_id=?", (loan_id,))["status"]))
        rt.clock.set("2026-10-23T00:40:00-04:00")
        r = execute(rt, "credit.loan_mature", {"loan_id": loan_id}, actor="lifecycle")
        self.assertEqual((True, "2026-10-26"), (r.body["rolled"], r.body["maturity_date"]))                     # Fri -> Mon
        self.assertEqual(1, one(rt, "SELECT COUNT(*) n FROM attention WHERE code='LOAN_ROLLED'")["n"])
        # the EUR comes back (1,000.00 from the FX account, 0.46 deposited and advanced): Mon the maturity repays and closes, the item resolves.
        # Since MONEY-1 the payoff first posts every night owed: nights 10-20..10-25 = 6 nights, cumulative
        # to_c(6 x 1,000 x 0.0275 / 360) = to_c(0.458333) = 0.46 (8, 7, 8, 8, 7, 8 cents by night), so 1,000.46 is due.
        self.assertTrue(execute(rt, "treasury.transfer", {"from_account": FOREIGN_FX, "to_account": CREDIT, "currency": "EUR", "amount_c": 100000}).ok)
        deposit(rt, 46, "EUR")
        fund(rt, CREDIT, 46, "EUR")
        rt.clock.set("2026-10-26T00:40:00-04:00")
        r = execute(rt, "credit.loan_mature", {"loan_id": loan_id}, actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((False, 46, 100000, True, 0), (r.body["rolled"], r.body["interest_c"], r.body["principal_c"], r.body["closed"], r.body["settled_c"]))
        self.assertEqual(0, one(rt, "SELECT COUNT(*) n FROM attention WHERE code='LOAN_ROLLED' AND resolved_ts IS NULL")["n"])
        r = execute(rt, "credit.loan_mature", {"loan_id": self.draw("EUR", 100000).body["loan_id"]}, actor="lifecycle")
        self.assertEqual((400, "OPEN_ENDED"), (r.http, r.code))
        assert_integrity(self, rt, "full")

    # ---- prime-broker financing: draw within headroom, repay, the execution.fund hook, the limit ---------------------------------------------
    def test_pb_draw_repay_and_funding_hook(self):
        rt = self.rt
        pb_gs = pb("GS")
        with rt.db.writer() as tx:
            books._insert_account(tx.conn, tx.ts, "T", T1, "PB_GS", "Goldman Sachs (PB)", "PB", 90, broker="GS")
        # financing off -> refused; on -> headroom 0 without positions (50 % of long MV − 2700)
        r = execute(rt, "credit.pb_draw", {"account_id": pb_gs, "amount_c": 10000000})
        self.assertEqual((400, "PB_FINANCING_OFF"), (r.http, r.code))
        self.assertTrue(execute(rt, "account.update_settings", {"account_id": pb_gs, "pb_financing": 1}).ok)
        r = execute(rt, "credit.pb_draw", {"account_id": pb_gs, "amount_c": 10000000})
        self.assertEqual((409, "PB_HEADROOM", 0), (r.http, r.code, r.body["error"]["details"]["headroom_c"]))
        r = execute(rt, "credit.pb_draw", {"account_id": CREDIT, "amount_c": 10000000})
        self.assertEqual((400, "NOT_PB"), (r.http, r.code))
        # 1,000 AAPL @ 339.50 in the PB account (mark 339.50): long MV 339,500.00 -> headroom to_c(339,500 × 0.50) = 169,750.00
        self.assertTrue(buy(rt, pb_gs, "AAPL", 1000, "339.50").ok)
        r = execute(rt, "credit.pb_draw", {"account_id": pb_gs, "amount_c": 10000000})
        self.assertTrue(r.ok, r.body)
        # Dr 1010 / Cr 2700 100,000.00; loans row PB-T1:PB_GS (PB_MARGIN, SOFR + 1.25 %, Goldman Sachs); headroom after 69,750.00
        self.assertEqual((16975000, 6975000, 10000000, "0.0125", "Goldman Sachs", f"PB-{pb_gs}"), (r.body["headroom_before_c"], r.body["headroom_after_c"], r.body["financed_c"], r.body["spread"],
                                                                                                    r.body["lender"], r.body["loan_id"]))
        self.assertEqual([("1010", "USD", 10000000, 10000000, "1"), ("2700", "USD", -10000000, -10000000, "1")], lines_of(rt, r.body["entry_id"]))
        self.assertEqual(("PB_MARGIN", 10000000, "SOFR", "0.0125", 360, "OPEN"), tuple(one(rt, "SELECT kind, principal_c, index_name, spread, day_basis, status FROM loans WHERE loan_id=?", (f"PB-{pb_gs}",)).values()))
        assert_integrity(self, rt, "full")                                                                       # L14: PB_MARGIN principal = balance(2700)
        r = execute(rt, "credit.pb_draw", {"account_id": pb_gs, "amount_c": 10000000})
        self.assertEqual((409, "PB_HEADROOM", 6975000), (r.http, r.code, r.body["error"]["details"]["headroom_c"]))
        # repay 60,000.00 -> 2700 40,000.00, settled 40,000.00; repaying 50,000.00 exceeds the financing
        r = execute(rt, "credit.pb_repay", {"account_id": pb_gs, "amount_c": 6000000})
        self.assertTrue(r.ok, r.body)
        self.assertEqual((4000000, False, 4000000), (r.body["financed_left_c"], r.body["closed"], r.body["settled_c"]))
        self.assertEqual(4000000, one(rt, "SELECT principal_c FROM loans WHERE loan_id=?", (f"PB-{pb_gs}",))["principal_c"])
        r = execute(rt, "credit.pb_repay", {"account_id": pb_gs, "amount_c": 5000000})
        self.assertEqual((400, "AMOUNT_EXCEEDS"), (r.http, r.code))
        # the execution.fund hook with Treasury empty: need 90,000.00, own settled 40,000.00 -> shortfall 50,000.00; headroom 169,750 − 40,000 = 129,750 -> drawn 50,000.00 (source pb)
        execute(rt, "treasury.withdraw", {"lifetime_id": T1, "amount_c": 1000000000, "currency": "USD"})
        fill_id = one(rt, "SELECT fill_id FROM fills")["fill_id"]
        r = execute(rt, "test.fund", {"account_id": pb_gs, "need_c": 9000000, "link_type": "fill", "link_id": fill_id})
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("pb", 5000000, 5000000, True, None), (r.body["source"], r.body["shortfall_c"], r.body["pb_draw_c"], r.body["funded"], r.body["sweep_id"]))
        self.assertEqual((9000000, 9000000, 9000000), (bal(rt, pb_gs, "2700", "USD"), bal(rt, pb_gs, "1010", "USD"), one(rt, "SELECT principal_c FROM loans WHERE loan_id=?", (f"PB-{pb_gs}",))["principal_c"]))
        # a need beyond the headroom: 300,000 − 90,000 own = 210,000 > headroom 79,750 -> 409 INSUFFICIENT_FUNDS and NOTHING drawn (rolled back)
        r = execute(rt, "test.fund", {"account_id": pb_gs, "need_c": 30000000, "link_type": "fill", "link_id": fill_id})
        self.assertEqual((409, "INSUFFICIENT_FUNDS", 7975000), (r.http, r.code, r.body["error"]["details"]["pb_draw_c"]))
        self.assertEqual(9000000, bal(rt, pb_gs, "2700", "USD"))
        # pb_limit_c 100,000.00 caps the headroom at limit − financed = 10,000.00 (L14: 2700 ≤ pb_limit_c)
        self.assertTrue(execute(rt, "account.update_settings", {"account_id": pb_gs, "pb_limit_c": 10000000}).ok)
        r = execute(rt, "credit.pb_draw", {"account_id": pb_gs, "amount_c": 2000000})
        self.assertEqual((409, "PB_HEADROOM", 1000000), (r.http, r.code, r.body["error"]["details"]["headroom_c"]))
        r = execute(rt, "credit.pb_draw", {"account_id": pb_gs, "amount_c": 1000000})
        self.assertTrue(r.ok, r.body)
        self.assertEqual(10000000, bal(rt, pb_gs, "2700", "USD"))
        self.assertEqual([("LOAN_DRAWN", 3), ("LOAN_REPAID", 1)], q_counts(rt, ("LOAN_DRAWN", "LOAN_REPAID")))
        assert_integrity(self, rt, "full")
        # the loans view lists the PB financing row
        with rt.db.reader() as conn:
            rows = credit.loans_of(conn, pb_gs)
        self.assertEqual([(f"PB-{pb_gs}", "PB_MARGIN", 10000000, 0)], [(x["loan_id"], x["kind"], x["principal_c"], x["accrued_c"]) for x in rows])

    # ---- the accrual needs WP-08's helper ---------------------------------------------------------------------------------------------------------
    def test_accrual_without_lifecycle_helper_is_refused(self):
        rt = self.rt
        loan_id = self.draw("EUR", 100000).body["loan_id"]
        rt.lifecycle = None
        rt.clock.set("2026-10-21T00:35:00-04:00")
        r = accrue(rt, loan_id, "2026-10-20")
        self.assertEqual((400, "NOT_IN_THIS_BUILD"), (r.http, r.code))
        self.assertEqual(0, bal(rt, CREDIT, "2755", "EUR"))
        # a night that has not ended yet is refused too (accruals post from 00:30 the next day)
        from tests.test_terminal_fx import accrue_segment
        import types
        rt.lifecycle = types.SimpleNamespace(accrue_segment=accrue_segment)
        r = accrue(rt, loan_id, "2026-10-21")
        self.assertEqual((400, "NIGHT_NOT_ENDED"), (r.http, r.code))
        r = accrue(rt, loan_id, "2026-10-19")
        self.assertEqual((400, "NIGHT_BEFORE_DRAW"), (r.http, r.code))

    # ---- Build 2 P08: the lender is a bank of the counterparty directory (assumed terms), the C26 view, the full payoff ---------------------
    def test_lender_choice_view_and_full_payoff(self):
        rt = self.rt
        # Tue 10-20 11:00: EUR 1,000,000.00 from Deutsche Bank AG, whose ASSUMED EUR spread is 0.60 %: ECBDFR 2.00 (obs 10-19) + 0.60 = 2.60 %;
        # tenor 1M = 30 days -> 10-20 + 30 = Thu 2026-11-19 (a business day)
        r = execute(rt, "credit.loan_draw", {"account_id": CREDIT, "currency": "EUR", "amount_c": 100000000, "counterparty_id": "BANK_DB", "tenor": "1M"})
        self.assertTrue(r.ok, r.body)
        loan_id = r.body["loan_id"]
        self.assertEqual(("Deutsche Bank AG", "BANK_DB", "Deutsche Bank AG (assumed terms)", "0.006", "0.026", "2026-11-19"),
                         (r.body["loan"]["lender"], r.body["loan"]["counterparty_id"], r.body["loan"]["lender_label"], r.body["loan"]["spread"], r.body["loan"]["rate"],
                          r.body["loan"]["maturity_date"]))
        self.assertIn("from Deutsche Bank AG (assumed terms) at ECBDFR 2.00 % (2026-10-19) + 0.60 % = 2.60 %, ACT/360, matures 2026-11-19", r.body["memo"])
        # refusals, nothing written: Deutsche Bank does not lend AUD in its terms; 400 days is beyond its longest tenor (12M = 365 days);
        # an agent lender is not a bank; an unknown id
        for over, code in (({"currency": "AUD"}, "CURRENCY_NOT_LENT"), ({"tenor": None, "term_days": 400}, "TERM"), ({"counterparty_id": "AGENT_BNY"}, "COUNTERPARTY_KIND"),
                           ({"counterparty_id": "BANK_NOPE"}, "COUNTERPARTY_UNKNOWN")):
            p = dict({"account_id": CREDIT, "currency": "EUR", "amount_c": 100000, "counterparty_id": "BANK_DB", "tenor": "1M"}, **over)
            x = execute(rt, "credit.loan_draw", {k: v for k, v in p.items() if v is not None})
            self.assertEqual((400, code), (x.http, x.code), over)
        # the facility maximum (assumed USD 250,000,000 for Deutsche Bank): the open EUR 1,000,000.00 is USD to_c(1,000,000 x 1.1238) = 1,123,800.00;
        # EUR 222,000,000.00 more is USD 249,483,600.00 -> 250,607,400.00 > 250,000,000.00 -> 409 FACILITY_LIMIT naming what is left (248,876,200.00)
        x = execute(rt, "credit.loan_draw", {"account_id": CREDIT, "currency": "EUR", "amount_c": 22200000000, "counterparty_id": "BANK_DB", "tenor": "1M"})
        self.assertEqual((409, "FACILITY_LIMIT", 112380000, 24948360000), (x.http, x.code, x.body["error"]["details"]["used_usd_c"], x.body["error"]["details"]["draw_usd_c"]))
        self.assertIn("248,876,200.00", x.body["error"]["message"])
        self.assertEqual(1, one(rt, "SELECT COUNT(*) n FROM loans")["n"])
        # Thu 10-22 00:10 (before the 00:30 run): the view shows the two nights a repayment would post first, posting nothing:
        # night 10-20 = to_c(1,000,000 x 0.026 / 360) = to_c(72.2222) = 72.22; cum night 2 = to_c(144.4444) = 144.44 -> 72.22; payoff 1,000,144.44
        rt.clock.set("2026-10-22T00:10:00-04:00")
        v = credit.loans_view(rt, account_id=CREDIT)["rows"][0]
        self.assertEqual((0, [("2026-10-20", 7222), ("2026-10-21", 7222)], 14444, 14444, 100014444, "2", "0.6", "2.6", "2026-10-19", True),
                         (v["accrued_posted_c"], [(n["night"], n["amount_c"]) for n in v["pending_nights"]], v["pending_c"], v["accrued_c"], v["payoff_c"],
                          v["index_value_pct"], v["spread_pct"], v["all_in_pct"], v["index_as_of"], v["repayable"]))
        self.assertEqual(0, bal(rt, CREDIT, "2755", "EUR"))
        # the full payoff needs EUR 1,000,144.44 settled; the account holds EUR 1,000,000.00 -> 409, "buy EUR 144.44 with Convert", nothing posted
        r = execute(rt, "credit.loan_repay", {"loan_id": loan_id, "full": True})
        self.assertEqual((409, "INSUFFICIENT_CASH_CCY", "buy EUR 144.44 with Convert"), (r.http, r.code, r.body["error"]["details"]["hint"]))
        self.assertEqual(0, bal(rt, CREDIT, "5150", "EUR"))
        # EUR 144.44 deposited into the Treasury (not moved by hand): the repayment sweeps exactly the shortfall 144.44 in, one SWEEP_IN linked
        # to the loan (the same currency, no FX)
        deposit(rt, 14444, "EUR")
        r = execute(rt, "credit.loan_repay", {"loan_id": loan_id, "full": True})
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("sweep", 14444, 14444), (r.body["funding"]["source"], r.body["funding"]["sweep_c"], r.body["funding"]["shortfall_c"]))
        self.assertEqual([("SWEEP_IN", 14444, "EUR", "loan", loan_id)], [(t["kind"], t["amount_c"], t["currency"], t["linked_type"], t["linked_id"])
                                                                         for t in q(rt, "SELECT * FROM transfers WHERE kind='SWEEP_IN'")])
        # the two nights posted first (72.22 + 72.22), then interest 144.44 + principal 1,000,000.00 = 1,000,144.44 repaid; CLOSED; EUR 0 left
        self.assertEqual((14444, 100000000, 0, True, 0, ["2026-10-20", "2026-10-21"]), (r.body["paid_interest_c"], r.body["paid_principal_c"], r.body["principal_left_c"],
                                                                                          r.body["closed"], r.body["settled_c"], [n["night"] for n in r.body["accrued_nights"]]))
        self.assertEqual((14444, 0, 0), (bal(rt, CREDIT, "5150", "EUR"), bal(rt, CREDIT, "2750", "EUR"), bal(rt, CREDIT, "2755", "EUR")))
        self.assertEqual([], credit.loans_view(rt, account_id=CREDIT)["rows"])
        assert_integrity(self, rt, "full")


# ---- small readers ---------------------------------------------------------------------------------------------------------------------------------
def credit_accrued(rt, loan_id):
    with rt.db.reader() as conn:
        return credit.accrued_c(conn, loan_id)


def credit_view(rt, loan_id):
    with rt.db.reader() as conn:
        return credit.loan_view(conn, loan_id)


def q_counts(rt, types_):
    return [(t, one(rt, "SELECT COUNT(*) n FROM events WHERE type=?", (t,))["n"]) for t in types_]


if __name__ == "__main__":
    unittest.main()
