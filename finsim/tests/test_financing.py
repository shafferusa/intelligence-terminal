"""Phase 2: securities lending, repo, collateral, prime brokerage, FX — and the
definition-of-done scenario, deterministic under a fixed seed."""
import unittest
from datetime import date

from tests.helpers import D, assert_ledger_invariants, make_world
from finsim.domain.events import E
from finsim.store import EventStore
from finsim.world import CommandError, World


def explain_reconciles(pf):
    return all(sum((v for k, v in s.explain.items() if not k.startswith("_")), D(0)) == s.day_pnl for s in pf.nav_history)


def nearest_dividend_payer(w):
    cas = sorted((c for c in w.corporate_actions.values() if c.status == "DECLARED" and w.market.lending.state.get(c.security_id)), key=lambda c: c.ex_date)
    return cas[0]


class ShortSaleTest(unittest.TestCase):
    def test_short_sale_lifecycle(self):
        w, pf, store = make_world(capital=100_000_000)
        led = w.ledgers[pf.id]
        with self.assertRaises(CommandError):
            w.place_order(pf.id, "NVRA", "SELL", 1000)                    # nothing to deliver, no borrow
        loc = w.request_locate(pf.id, "NVRA", 100_000)
        self.assertEqual(loc.status, "OPEN")
        self.assertGreater(loc.available, 0)
        self.assertEqual(loc.valid_through, w.calendar.next_business_day(w.current_date).isoformat())
        self.assertEqual(pf.loans, {}, "a locate is not a borrow")
        with self.assertRaises(CommandError):
            w.borrow_securities(pf.id, loc.id, loc.available + 1)
        loan = w.borrow_securities(pf.id, loc.id, loc.available)
        pos = pf.positions["NVRA"]
        self.assertEqual(pos.settled_quantity, loan.quantity, "borrowed shares arrive in custody")
        self.assertEqual(pos.quantity, D(0), "no economic position yet")
        self.assertEqual(pos.borrowed_quantity, loan.quantity)
        self.assertEqual(led.balance("1400"), loan.collateral_amount)
        self.assertEqual(loan.collateral_amount, (loan.market_value * D("1.02")).quantize(D("0.01")))
        assert_ledger_invariants(self, w, pf)
        o = w.place_order(pf.id, "NVRA", "SELL", loan.quantity)
        w.advance(1)
        t = pf.trades[o.trade_ids[0]]
        self.assertEqual(o.status, "FILLED")
        self.assertEqual(pos.quantity, -loan.quantity)
        self.assertEqual(pos.cost_basis, -t.gross_amount, "short cost basis is minus proceeds")
        self.assertEqual(led.security_balance("NVRA", "2600"), t.gross_amount)
        self.assertEqual(led.security_balance("NVRA", "1200"), t.net_amount, "proceeds are a receivable until settlement")
        self.assertEqual(pos.unrealized_pnl, pos.market_value - pos.cost_basis)
        assert_ledger_invariants(self, w, pf)
        cash_before = pf.cash["USD"].balance
        w.advance(1)
        self.assertEqual(t.status, "SETTLED")
        self.assertEqual(pos.settled_quantity, D(0), "borrowed shares delivered to the buyer")
        self.assertGreater(pf.cash["USD"].balance, cash_before, "short-sale proceeds settle into cash")
        self.assertGreater(loan.accrued_fee, 0)
        self.assertEqual(led.security_balance("NVRA", "5300"), pos.borrow_fees)
        self.assertTrue(any(m.kind == "COLLATERAL" for m in pf.cash_movements))
        assert_ledger_invariants(self, w, pf)
        # cover and return
        o2 = w.place_order(pf.id, "NVRA", "BUY", loan.quantity)
        w.advance(1)
        t2 = pf.trades[o2.trade_ids[0]]
        self.assertEqual(pos.quantity, D(0))
        self.assertEqual(t2.realized_pnl, t.gross_amount - t2.gross_amount)
        self.assertEqual(led.security_balance("NVRA", "4000"), t2.realized_pnl)
        self.assertEqual(led.security_balance("NVRA", "2600"), D(0))
        self.assertEqual(led.security_balance("NVRA", "2610"), D(0))
        w.advance(2)
        self.assertEqual(loan.status, "RETURNED", "excess borrow returned automatically once the cover settles")
        self.assertEqual(pos.borrowed_quantity, D(0))
        self.assertEqual(pos.settled_quantity, D(0))
        self.assertEqual(led.balance("1400"), D(0), "collateral came back")
        self.assertEqual(led.balance("2310"), D(0), "fees settled on return")
        self.assertGreater(loan.fees_paid, 0)
        assert_ledger_invariants(self, w, pf)
        self.assertTrue(explain_reconciles(pf))
        w2 = World.load(store, "t")
        self.assertEqual(len(w2.events), len(w.events))
        self.assertEqual(w2.portfolios[pf.id].loans[loan.id].fees_paid, loan.fees_paid)

    def test_partial_and_failed_locate_and_expiry(self):
        w, pf, _ = make_world(capital=1_000_000_000)
        big = w.request_locate(pf.id, "ZYNQ", 50_000_000)          # far more than the lendable supply
        self.assertLess(big.available, big.requested)
        self.assertGreater(big.available, 0)
        loan = w.borrow_securities(pf.id, big.id, big.available)
        none = None
        for _ in range(8):                                             # exhaust the lendable supply
            none = w.request_locate(pf.id, "ZYNQ", 1_000_000)
            if none.status == "NONE":
                break
            w.borrow_securities(pf.id, none.id, none.available)
        self.assertEqual(none.status, "NONE")
        self.assertEqual(none.available, D(0))
        with self.assertRaises(CommandError):
            w.borrow_securities(pf.id, none.id, 1)
        loc = w.request_locate(pf.id, "MRDN", 1000)
        w.advance(2)
        with self.assertRaises(CommandError):
            w.borrow_securities(pf.id, loc.id, 1000)                # expired locate
        assert_ledger_invariants(self, w, pf)

    def test_borrow_fee_accrual_and_rerating(self):
        w, pf, _ = make_world(capital=200_000_000)
        loc = w.request_locate(pf.id, "PLSR", 200_000)
        loan = w.borrow_securities(pf.id, loc.id, loc.available)
        w.place_order(pf.id, "PLSR", "SELL", loan.quantity)
        fees = []
        for _ in range(15):
            w.advance(1)
            fees.append(loan.accrued_fee + loan.fees_paid)
        self.assertTrue(all(b >= a for a, b in zip(fees, fees[1:])), "fees accrue monotonically")
        self.assertGreater(len(loan.rate_history), 1, "the loan is re-rated to the market")
        self.assertAlmostEqual(loan.borrow_rate, w.market.lending.state["PLSR"].rate, places=6)
        self.assertGreater(loan.accrued_rebate + sum((m.amount for m in pf.cash_movements if m.kind == "BORROW_FEE"), D(0)) + D(1), 0)
        assert_ledger_invariants(self, w, pf)

    def test_manufactured_dividend_while_short(self):
        w, pf, store = make_world(capital=200_000_000)
        led = w.ledgers[pf.id]
        ca = nearest_dividend_payer(w)
        loc = w.request_locate(pf.id, ca.security_id, 20_000)
        loan = w.borrow_securities(pf.id, loc.id, loc.available)
        w.place_order(pf.id, ca.security_id, "SELL", loan.quantity)
        while ca.status != "PAID":
            w.advance(1)
        pos = pf.positions[ca.security_id]
        ent = ca.entitlements[pf.id]
        self.assertTrue(ent["obligation"])
        self.assertEqual(ent["amount"], loan.quantity * ca.amount_per_unit)
        self.assertEqual(pos.manufactured_dividends, ent["amount"])
        self.assertEqual(led.security_balance(ca.security_id, "5400"), ent["amount"])
        self.assertEqual(led.balance("2320"), D(0))
        self.assertTrue(any(m.kind == "MANUFACTURED_DIVIDEND" and m.amount == -ent["amount"] for m in pf.cash_movements))
        snap = next(s for s in pf.nav_history if s.date == ca.ex_date)
        self.assertEqual(snap.explain["manufactured_dividends"], -ent["amount"])
        assert_ledger_invariants(self, w, pf)
        w2 = World.load(store, "t")
        self.assertEqual(w2.portfolios[pf.id].positions[ca.security_id].manufactured_dividends, ent["amount"])

    def test_recall_replacement_borrow_and_buy_in(self):
        w, pf, _ = make_world(capital=200_000_000)
        loc = w.request_locate(pf.id, "HLXB", 50_000)
        loan = w.borrow_securities(pf.id, loc.id, loc.available)
        w.place_order(pf.id, "HLXB", "SELL", loan.quantity)
        w.advance(2)
        # lender recalls half (injected deterministically)
        half = loan.quantity // 2
        due = w.calendar.add_business_days(w.current_date, 2).isoformat()
        w.emit(E.SECURITY_LOAN_RECALLED, {"portfolio_id": pf.id, "loan_id": loan.id, "security_id": "HLXB", "quantity": half, "due": due, "lender": loan.lender, "reason": "test recall"}, portfolio_id=pf.id)
        w.flush()
        self.assertEqual(loan.recall_status, "RECALLED")
        w.advance(1)
        self.assertTrue(any("SECURITY RECALL" in a["text"] for a in pf.briefings[-1]["attention"]))
        # replacement borrow from a new locate, then return the recalled shares
        loc2 = w.request_locate(pf.id, "HLXB", half)
        loan2 = w.borrow_securities(pf.id, loc2.id, half)
        w.return_securities(pf.id, loan.id, half)
        self.assertEqual(loan.recall_status, "SATISFIED")
        self.assertEqual(loan.quantity, loan2.quantity)
        self.assertEqual(pf.positions["HLXB"].borrowed_quantity, loan.quantity + loan2.quantity)
        assert_ledger_invariants(self, w, pf)
        # a second recall left unanswered ends in a buy-in
        due2 = w.calendar.add_business_days(w.current_date, 2).isoformat()
        w.emit(E.SECURITY_LOAN_RECALLED, {"portfolio_id": pf.id, "loan_id": loan2.id, "security_id": "HLXB", "quantity": loan2.quantity, "due": due2, "lender": loan2.lender, "reason": "test recall"}, portfolio_id=pf.id)
        w.flush()
        w.advance(3)
        self.assertEqual(loan2.recall_status, "BOUGHT_IN")
        self.assertTrue(any(t.execution_detail.get("forced") and "buy-in" in t.execution_detail.get("note", "") for t in pf.trades.values()))
        self.assertGreater(w.ledgers[pf.id].balance("5700"), 0, "buy-in penalty charged")
        w.advance(2)
        self.assertEqual(loan2.status, "RETURNED")
        self.assertEqual(pf.positions["HLXB"].quantity, -loan.quantity)
        assert_ledger_invariants(self, w, pf)

    def test_collateral_marks_with_price(self):
        w, pf, _ = make_world(capital=300_000_000)
        loc = w.request_locate(pf.id, "QNTM", 200_000)
        loan = w.borrow_securities(pf.id, loc.id, loc.available)
        w.place_order(pf.id, "QNTM", "SELL", loan.quantity)
        for _ in range(10):
            w.advance(1)
            mv = loan.quantity * w.market.last_bar("QNTM").close
            self.assertLessEqual(abs(loan.collateral_amount - mv * D("1.02")), max(D(1000), (mv * D("0.002")).quantize(D("0.01"))))
            self.assertEqual(w.ledgers[pf.id].balance("1400"), sum(pf.cash_collateral.values(), D(0)))
        moves = [m for m in pf.cash_movements if m.kind == "COLLATERAL"]
        self.assertGreater(len(moves), 2, "collateral moves as the price moves")
        assert_ledger_invariants(self, w, pf)

    def test_treasury_collateral_for_borrow(self):
        w, pf, _ = make_world(capital=300_000_000)
        w.place_order(pf.id, "UST-2Y", "BUY", 50_000_000)
        w.advance(2)
        loc = w.request_locate(pf.id, "MRDN", 100_000)
        loan = w.borrow_securities(pf.id, loc.id, loc.available, collateral_type="UST-2Y")
        self.assertEqual(loan.collateral_type, "UST-2Y")
        self.assertGreater(pf.pledged_quantity("UST-2Y"), 0)
        self.assertGreaterEqual(loan.collateral_value, loan.market_value * D("1.02") - D(1000))
        with self.assertRaises(CommandError):
            w.place_order(pf.id, "UST-2Y", "SELL", 50_000_000)      # pledged Treasuries cannot be sold
        w.advance(3)
        self.assertEqual(loan.status, "OPEN", "unused borrows are held for five sessions before automatic return")
        assert_ledger_invariants(self, w, pf)
        w.return_securities(pf.id, loan.id)
        self.assertEqual(pf.pledged_quantity("UST-2Y"), D(0))
        assert_ledger_invariants(self, w, pf)


class RepoTest(unittest.TestCase):
    def _bonds(self, capital=200_000_000, sec="UST-10Y", face=50_000_000):
        w, pf, store = make_world(capital=capital)
        w.place_order(pf.id, sec, "BUY", face)
        w.advance(2)
        return w, pf, store

    def test_repo_open_interest_haircut_maturity(self):
        w, pf, store = self._bonds()
        led = w.ledgers[pf.id]
        q = w.repo_quote("REPO", "UST-10Y", 50_000_000, "TERM", 5)
        self.assertAlmostEqual(q["haircut"], 0.02, places=4)
        self.assertEqual(q["principal"], (q["market_value"] * D("0.98")).quantize(D("0.01")))
        cash0 = pf.cash["USD"].balance
        r = w.open_repo(pf.id, "REPO", "UST-10Y", 50_000_000, "TERM", 5)
        self.assertEqual(pf.cash["USD"].balance, cash0 + r.principal)
        self.assertEqual(led.balance("2500"), r.principal)
        self.assertEqual(pf.pledged_quantity("UST-10Y"), D(50_000_000))
        self.assertEqual(pf.positions["UST-10Y"].quantity, D(50_000_000), "economic exposure retained")
        with self.assertRaises(CommandError):
            w.place_order(pf.id, "UST-10Y", "SELL", 1_000_000)          # encumbered
        with self.assertRaises(CommandError):
            w.open_repo(pf.id, "REPO", "UST-10Y", 1_000_000)             # cannot pledge twice
        w.advance(2)
        self.assertGreater(r.accrued_interest, 0)
        self.assertEqual(led.balance("2330"), r.accrued_interest)
        self.assertEqual(led.security_balance("UST-10Y", "5500"), r.accrued_interest)
        assert_ledger_invariants(self, w, pf)
        while r.status == "OPEN":
            w.advance(1)
        self.assertEqual(r.status, "CLOSED")
        self.assertEqual(led.balance("2500"), D(0))
        self.assertEqual(pf.pledged_quantity("UST-10Y"), D(0))
        self.assertGreater(r.interest_paid, 0)
        self.assertTrue(any(h["note"] == "matured" for h in r.history))
        assert_ledger_invariants(self, w, pf)
        self.assertTrue(explain_reconciles(pf))
        w2 = World.load(store, "t")
        self.assertEqual(w2.portfolios[pf.id].repos[r.id].interest_paid, r.interest_paid)

    def test_overnight_rollover(self):
        w, pf, _ = self._bonds()
        r = w.open_repo(pf.id, "REPO", "UST-10Y", 30_000_000, "OVERNIGHT")
        w.advance(3)
        self.assertEqual(r.status, "OPEN")
        self.assertEqual(r.rolls, 3)
        self.assertGreater(r.interest_paid, 0, "interest is paid at each roll")
        self.assertEqual(r.maturity, w.calendar.next_business_day(w.current_date).isoformat())
        assert_ledger_invariants(self, w, pf)
        w.close_repo(pf.id, r.id)
        assert_ledger_invariants(self, w, pf)

    def test_open_repo_reduce_and_cash_margin(self):
        w, pf, _ = self._bonds()
        r = w.open_repo(pf.id, "REPO", "UST-10Y", 50_000_000, "OPEN")
        w.advance(2)
        self.assertEqual(r.status, "OPEN")
        self.assertIsNone(r.maturity)
        w.repo_reduce(pf.id, r.id, 10_000_000)
        self.assertLess(r.principal, D(45_000_000))
        w.repo_post_cash(pf.id, r.id, 1_000_000)
        self.assertEqual(r.cash_margin, D(1_000_000))
        self.assertEqual(w.ledgers[pf.id].balance("1400"), D(1_000_000))
        w.advance(1)
        assert_ledger_invariants(self, w, pf)
        w.close_repo(pf.id, r.id)
        self.assertEqual(w.ledgers[pf.id].balance("1400"), D(0), "cash margin returned on close")
        assert_ledger_invariants(self, w, pf)

    def test_stress_haircut_margin_call_post_collateral(self):
        w, pf, _ = make_world(capital=300_000_000)
        w.place_order(pf.id, "UST-10Y", "BUY", 60_000_000)
        w.place_order(pf.id, "UST-5Y", "BUY", 50_000_000)
        w.advance(2)
        r = w.open_repo(pf.id, "REPO", "UST-10Y", 60_000_000, "OPEN")
        w.force_regime("LIQUIDITY_STRESS")
        w.advance(1)
        self.assertGreater(r.haircut, 0.03, "haircut rises in the stress regime")
        call = w.collateral.open_call(pf, "REPO", r.id)
        self.assertIsNotNone(call, "collateral shortfall generates a call")
        self.assertGreater(call.amount, 0)
        self.assertTrue(any("Repo collateral call" in a["text"] for a in pf.briefings[-1]["attention"]))
        w.repo_post_collateral(pf.id, r.id, "UST-5Y", 20_000_000)
        w.advance(1)
        self.assertEqual(call.status, "MET")
        assert_ledger_invariants(self, w, pf)

    def test_unmet_repo_call_is_unwound_by_counterparty(self):
        w, pf, _ = make_world(capital=300_000_000)
        w.place_order(pf.id, "UST-10Y", "BUY", 60_000_000)
        w.advance(2)
        r = w.open_repo(pf.id, "REPO", "UST-10Y", 60_000_000, "OPEN")
        p0 = r.principal
        w.force_regime("LIQUIDITY_STRESS")
        w.advance(1)
        call = w.collateral.open_call(pf, "REPO", r.id)
        self.assertIsNotNone(call)
        w.advance(1)
        self.assertEqual(call.status, "FORCED")
        self.assertLess(r.principal, p0)
        assert_ledger_invariants(self, w, pf)

    def test_substitution(self):
        w, pf, _ = make_world(capital=300_000_000)
        w.place_order(pf.id, "UST-10Y", "BUY", 50_000_000)
        w.place_order(pf.id, "UST-5Y", "BUY", 50_000_000)
        w.advance(2)
        r = w.open_repo(pf.id, "REPO", "UST-10Y", 50_000_000, "OPEN")
        with self.assertRaises(CommandError):
            w.repo_substitute(pf.id, r.id, "UST-10Y", 50_000_000, "UST-5Y", 10_000_000)   # not enough value
        w.repo_substitute(pf.id, r.id, "UST-10Y", 20_000_000, "UST-5Y", 25_000_000)
        self.assertEqual(pf.pledged_quantity("UST-10Y"), D(30_000_000))
        self.assertEqual(pf.pledged_quantity("UST-5Y"), D(25_000_000))
        w.place_order(pf.id, "UST-10Y", "SELL", 20_000_000)          # released face is sellable again
        w.advance(1)
        assert_ledger_invariants(self, w, pf)

    def test_reverse_repo(self):
        w, pf, store = make_world(capital=200_000_000)
        led = w.ledgers[pf.id]
        r = w.open_repo(pf.id, "REVERSE", "UST-5Y", 50_000_000, "TERM", 7)
        self.assertEqual(led.balance("1500"), r.principal)
        self.assertIn(r.id, pf.collateral_received)
        w.advance(2)
        self.assertGreater(r.accrued_interest, 0)
        self.assertEqual(led.security_balance("UST-5Y", "4500"), r.accrued_interest)
        assert_ledger_invariants(self, w, pf)
        while r.status == "OPEN":
            w.advance(1)
        self.assertEqual(led.balance("1500"), D(0))
        self.assertNotIn(r.id, pf.collateral_received)
        self.assertGreater(r.interest_paid, 0)
        assert_ledger_invariants(self, w, pf)


class CollateralTest(unittest.TestCase):
    def test_dashboard_and_double_pledge_prevention(self):
        w, pf, _ = make_world(capital=200_000_000)
        w.place_order(pf.id, "UST-10Y", "BUY", 40_000_000)
        w.place_order(pf.id, "NVRA", "BUY", 20_000)
        w.advance(2)
        d = w.collateral.dashboard(pf)
        row = next(r for r in d["available"] if r["security_id"] == "UST-10Y")
        self.assertEqual(row["available"], D(40_000_000))
        self.assertAlmostEqual(row["haircuts"]["REPO"], 0.02, places=4)
        r = w.open_repo(pf.id, "REPO", "UST-10Y", 30_000_000, "OPEN")
        d = w.collateral.dashboard(pf)
        row = next(r for r in d["available"] if r["security_id"] == "UST-10Y")
        self.assertEqual(row["available"], D(10_000_000))
        self.assertEqual(row["pledged"], D(30_000_000))
        self.assertEqual(d["treasury_encumbrance_pct"], 0.75)
        with self.assertRaises(CommandError):
            w.open_repo(pf.id, "REPO", "UST-10Y", 20_000_000)
        with self.assertRaises(CommandError):
            w.place_order(pf.id, "UST-10Y", "SELL", 20_000_000)
        w.place_order(pf.id, "UST-10Y", "SELL", 10_000_000)          # the unencumbered part sells
        w.advance(1)
        self.assertTrue(any("COLLATERAL CONCENTRATION" in a["text"] for a in pf.briefings[-1]["attention"]))
        assert_ledger_invariants(self, w, pf)


class PrimeTest(unittest.TestCase):
    def test_margin_borrowing_interest_and_repayment(self):
        w, pf, _ = make_world(capital=100_000_000)
        led = w.ledgers[pf.id]
        w.place_order(pf.id, "SPXE", "BUY", 150_000)
        w.advance(2)
        fin = w.prime.financing(pf)
        self.assertGreater(fin["excess_liquidity"], 0)
        with self.assertRaises(CommandError):
            w.margin_draw(pf.id, fin["excess_liquidity"] * 2)
        w.margin_draw(pf.id, 20_000_000)
        self.assertEqual(pf.margin_loan, D(20_000_000))
        self.assertEqual(led.balance("2700"), D(20_000_000))
        w.place_order(pf.id, "HLXB", "BUY", 150_000)               # spend the drawn cash so the loan persists
        w.place_order(pf.id, "NVRA", "BUY", 100_000)
        w.advance(3)
        self.assertGreater(pf.margin_loan, D(0))
        self.assertGreater(pf.margin_interest_accrued, 0)
        self.assertEqual(led.balance("2340"), pf.margin_interest_accrued)
        self.assertTrue(any(m.kind == "MARGIN_LOAN" and m.amount < 0 for m in pf.cash_movements), "idle cash repays automatically")
        w.place_order(pf.id, "HLXB", "SELL", 150_000)
        w.place_order(pf.id, "NVRA", "SELL", 100_000)
        w.advance(2)
        self.assertEqual(pf.margin_loan, D(0), "sale proceeds repay the loan")
        assert_ledger_invariants(self, w, pf)
        # buying on margin: a purchase beyond settled cash is accepted when excess liquidity covers it
        cash = pf.cash["USD"].balance
        px = w.market.last_bar("SPXE").ask
        w.place_order(pf.id, "SPXE", "BUY", int(cash / px * D("1.3")))
        w.advance(2)
        self.assertGreater(pf.margin_loan, D(0), "the shortfall was financed at settlement")
        assert_ledger_invariants(self, w, pf)

    def test_overdraft_becomes_loan_never_negative_cash(self):
        w, pf, _ = make_world(capital=50_000_000)
        w.place_order(pf.id, "SPXE", "BUY", 100_000)
        w.advance(2)
        loc = w.request_locate(pf.id, "NVRA", 60_000)
        loan = w.borrow_securities(pf.id, loc.id, loc.available)       # cash collateral exceeds settled cash: financed by the prime broker
        self.assertGreater(pf.margin_loan + max(D(0), -pf.cash["USD"].balance), D(0))
        w.place_order(pf.id, "NVRA", "SELL", loan.quantity)
        for _ in range(6):
            w.advance(1)
            self.assertGreaterEqual(pf.cash["USD"].balance, D(0))
        self.assertTrue(any(m.kind == "MARGIN_LOAN" for m in pf.cash_movements))
        assert_ledger_invariants(self, w, pf)

    def test_margin_call_then_forced_liquidation(self):
        w, pf, _ = make_world(capital=100_000_000)
        for t in ("LUXA", "NGSL", "CPRX", "URBN"):
            w.place_order(pf.id, t, "BUY", 23_000_000 // int(w.market.last_bar(t).close))
        w.advance(2)
        excess = w.prime.financing(pf)["excess_liquidity"]
        w.margin_draw(pf.id, excess * D("0.9"))
        for t in ("LUXA", "NGSL", "CPRX", "URBN"):
            w.place_order(pf.id, t, "BUY", int(excess * D("0.22") / w.market.last_bar(t).close), time_in_force="GTC")
        w.advance(2)
        self.assertGreater(pf.margin_loan, D(0))
        w.force_regime("LIQUIDITY_STRESS")
        w.advance(1)
        call = w.collateral.open_call(pf, "PRIME", "PRIME")
        self.assertIsNotNone(call, "haircuts up and prices down: excess liquidity goes negative")
        self.assertTrue(any("Prime-broker margin call" in a["text"] for a in pf.briefings[-1]["attention"]))
        w.advance(1)          # grace cycle
        w.advance(1)
        self.assertEqual(call.status, "FORCED")
        forced = [t for t in pf.trades.values() if t.execution_detail.get("forced")]
        self.assertTrue(forced, "forced liquidation creates ordinary trades")
        self.assertTrue(all(t.settlement_instruction_id for t in forced if not w.securities[t.security_id].is_future))
        w.advance(2)
        assert_ledger_invariants(self, w, pf)


class FXTest(unittest.TestCase):
    def test_spot_settlement_translation_and_realised(self):
        w, pf, store = make_world(capital=50_000_000)
        led = w.ledgers[pf.id]
        t = w.fx_spot(pf.id, "EUR", "USD", 10_000_000)
        self.assertEqual(t.status, "PENDING")
        self.assertEqual(led.balance("1250"), t.usd_value)
        self.assertEqual(led.balance("2350"), t.usd_value)
        self.assertEqual(t.settlement_date, w.calendar.add_business_days(w.current_date, 2).isoformat())
        w.advance(2)
        self.assertEqual(t.status, "SETTLED")
        self.assertAlmostEqual(float(pf.cash["EUR"].balance), 10_000_000, delta=5000)   # plus daily EUR interest
        self.assertEqual(led.balance("1010:EUR"), pf.cash["EUR"].base_value)
        assert_ledger_invariants(self, w, pf)
        w.advance(3)
        self.assertNotEqual(led.balance("4600"), D(0), "daily retranslation")
        self.assertAlmostEqual(float(pf.cash["EUR"].base_value), float(pf.cash["EUR"].balance * D(repr(w.market.fx.spot["EUR"]))), delta=0.05)
        s = w.fx_spot(pf.id, "USD", "EUR", 4_000_000, "SELL")
        w.advance(2)
        self.assertAlmostEqual(float(pf.cash["EUR"].balance), 6_000_000, delta=10_000)
        self.assertNotEqual(s.realized_fx, D(0), "selling EUR realises against carrying value")
        self.assertEqual(led.balance("4650"), sum((x.realized_fx for x in pf.fx_trades.values()), D(0)))
        self.assertGreater(len([m for m in pf.cash_movements if m.currency == "EUR" and m.kind == "INTEREST"]), 0, "EUR cash earns EUR rates")
        assert_ledger_invariants(self, w, pf)
        self.assertTrue(explain_reconciles(pf))
        w2 = World.load(store, "t")
        self.assertEqual(w2.portfolios[pf.id].cash["EUR"].base_value, pf.cash["EUR"].base_value)

    def test_forward_pricing_valuation_and_settlement(self):
        w, pf, store = make_world(capital=50_000_000)
        led = w.ledgers[pf.id]
        mat = w.calendar.add_business_days(w.current_date, 30)
        f = w.fx_forward(pf.id, "GBP", "USD", 5_000_000, mat.isoformat())
        cip = w.market.fx.forward("GBP", "USD", w.current_date, mat)
        self.assertAlmostEqual(f.forward_rate, cip * (1 + w.market.fx.spread_bps("GBP", "USD") / 1e4), places=8)
        with self.assertRaises(CommandError):
            w.fx_forward(pf.id, "GBP", "USD", 1, w.current_date.isoformat())
        w.advance(5)
        self.assertNotEqual(f.mtm, D(0))
        self.assertEqual(led.balance("1600"), f.mtm)
        assert_ledger_invariants(self, w, pf)
        while f.status == "OPEN":
            w.advance(1)
        self.assertAlmostEqual(float(pf.cash["GBP"].balance), 5_000_000, delta=5000)
        self.assertEqual(led.balance("1600"), D(0))
        self.assertEqual(led.balance("4700"), D(0), "forward P&L reclassified to realised at settlement")
        assert_ledger_invariants(self, w, pf)
        self.assertTrue(explain_reconciles(pf))
        w2 = World.load(store, "t")
        self.assertEqual(w2.portfolios[pf.id].cash["GBP"].base_value, pf.cash["GBP"].base_value)
        self.assertEqual(len(w2.events), len(w.events))


class DefinitionOfDoneTest(unittest.TestCase):
    """The 23-step scenario, deterministic under seed 42."""

    def test_scenario(self):
        w, pf, store = make_world(seed=42, start="2026-01-05", capital=100_000_000)
        led = w.ledgers[pf.id]
        # 1-2: $100MM, buy $40MM Treasuries
        self.assertEqual(led.nav(), D(100_000_000))
        w.place_order(pf.id, "UST-10Y", "BUY", 40_000_000)
        w.advance(2)
        self.assertEqual(pf.positions["UST-10Y"].settled_quantity, D(40_000_000))
        # 3: repo them
        repo = w.open_repo(pf.id, "REPO", "UST-10Y", 40_000_000, "OPEN")
        self.assertGreater(repo.principal, D(38_000_000))
        self.assertEqual(pf.pledged_quantity("UST-10Y"), D(40_000_000))
        # 4: use financing for equities
        w.place_order(pf.id, "SPXE", "BUY", 60_000)
        # 5-7: locate, borrow, short
        ca = nearest_dividend_payer(w)
        tkr = ca.security_id
        loc = w.request_locate(pf.id, tkr, 100_000)
        self.assertGreater(loc.available, 0)
        loan = w.borrow_securities(pf.id, loc.id, min(loc.available, D(100_000)))
        short_qty = loan.quantity
        o = w.place_order(pf.id, tkr, "SELL", short_qty)
        w.advance(1)
        t = pf.trades[o.trade_ids[0]]
        self.assertEqual(pf.positions[tkr].quantity, -short_qty)
        # 8: proceeds settle
        w.advance(1)
        self.assertEqual(t.status, "SETTLED")
        self.assertTrue(any(m.kind == "SETTLEMENT" and m.amount == t.net_amount for m in pf.cash_movements))
        # 9-10: fees accrue, collateral adjusts
        fee0, coll0 = loan.accrued_fee, loan.collateral_amount
        w.advance(2)
        self.assertGreater(loan.accrued_fee, fee0)
        self.assertNotEqual(loan.collateral_amount, coll0)
        assert_ledger_invariants(self, w, pf)
        # 11-12: dividend while short, manufactured dividend paid
        while ca.status != "PAID":
            w.advance(1)
        self.assertTrue(ca.entitlements[pf.id]["obligation"])
        self.assertTrue(any(m.kind == "MANUFACTURED_DIVIDEND" for m in pf.cash_movements))
        # 13-14: partial recall, replacement borrow
        half = short_qty // 2
        w.emit(E.SECURITY_LOAN_RECALLED, {"portfolio_id": pf.id, "loan_id": loan.id, "security_id": tkr, "quantity": half, "lender": loan.lender,
                                          "due": w.calendar.add_business_days(w.current_date, 2).isoformat(), "reason": "lender recall"}, portfolio_id=pf.id)
        w.flush()
        loc2 = w.request_locate(pf.id, tkr, half)
        loan2 = w.borrow_securities(pf.id, loc2.id, half)
        w.return_securities(pf.id, loan.id, half)
        self.assertEqual(loan.recall_status, "SATISFIED")
        self.assertEqual(pf.positions[tkr].borrowed_quantity, short_qty)
        # 15-18: market stress: equity and financing consequences, haircut up, margin call
        w.force_regime("LIQUIDITY_STRESS")
        w.advance(1)
        self.assertGreater(repo.haircut, 0.03, "Treasury repo haircut widens in a liquidity crisis")
        call = w.collateral.open_call(pf, "REPO", repo.id)
        self.assertIsNotNone(call)
        self.assertTrue(any(a["severity"] == "HIGH" and "Repo collateral call" in a["text"] for a in pf.briefings[-1]["attention"]))
        # 19: post additional collateral (cash margin), call met next cycle
        w.repo_post_cash(pf.id, repo.id, call.amount + D(50_000))
        w.advance(1)
        self.assertEqual(call.status, "MET")
        w.advance(2)
        snaps = pf.nav_history[-4:]
        self.assertTrue(any(s.explain["equities"] != 0 for s in snaps))
        self.assertTrue(all(s.explain["repo_financing"] < 0 for s in snaps), "repo interest every day")
        self.assertTrue(all(s.explain["borrow_fees"] != 0 for s in snaps))
        self.assertTrue(any(s.explain["equities"] < 0 or s.explain["rates"] != 0 for s in snaps))
        # 20-21: close the short, shares returned
        cover = w.place_order(pf.id, tkr, "BUY", short_qty)
        w.advance(1)
        self.assertEqual(pf.positions[tkr].quantity, D(0))
        self.assertNotEqual(pf.trades[cover.trade_ids[0]].realized_pnl, D(0))
        w.advance(2)
        self.assertEqual(loan.status, "RETURNED")
        self.assertEqual(loan2.status, "RETURNED")
        self.assertEqual(pf.positions[tkr].borrowed_quantity, D(0))
        self.assertEqual(led.balance("1400"), pf.cash_collateral.get(repo.id, D(0)))
        # 22: repay the repo
        w.close_repo(pf.id, repo.id)
        self.assertEqual(repo.status, "CLOSED")
        self.assertEqual(led.balance("2500"), D(0))
        self.assertEqual(pf.pledged_quantity("UST-10Y"), D(0))
        self.assertEqual(led.balance("1400"), D(0))
        w.advance(1)
        # 23: everything auditable and identical on replay
        assert_ledger_invariants(self, w, pf)
        self.assertTrue(explain_reconciles(pf))
        kinds = {m.kind for m in pf.cash_movements}
        for k in ("REPO", "COLLATERAL", "SETTLEMENT", "MANUFACTURED_DIVIDEND", "REPO_INTEREST"):
            self.assertIn(k, kinds)
        for e in led.entries:
            self.assertIn(e.cause_id, w.events_by_id)
        b = pf.briefings[-1]
        self.assertIn("financing", b)
        self.assertIn("short_book", b)
        w2 = World.load(store, "t")
        pf2 = w2.portfolios[pf.id]
        self.assertEqual(len(w2.events), len(w.events))
        self.assertEqual(w2.ledgers[pf.id].trial_balance(), led.trial_balance())
        self.assertEqual(pf2.cash["USD"].balance, pf.cash["USD"].balance)
        self.assertEqual(pf2.loans[loan.id].fees_paid, loan.fees_paid)
        self.assertEqual(pf2.repos[repo.id].interest_paid, repo.interest_paid)
        self.assertEqual(pf2.briefings[-1]["attention"], b["attention"])
        self.assertEqual(pf2.nav_history[-1].explain, pf.nav_history[-1].explain)


if __name__ == "__main__":
    unittest.main()
