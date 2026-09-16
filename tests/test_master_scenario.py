"""The master scenario: 45 numbered steps through every subsystem in one world, with the accounting
invariants checked at each checkpoint, replay equality at the end, and a bit-for-bit determinism check
against a second run of the same script. This is the regression test for the simulator as a whole."""
import unittest
from datetime import date

from helpers import assert_ledger_invariants
from test_financing import explain_reconciles
from finsim.careers import JOBS
from finsim.domain.events import E
from finsim.money import D, ZERO
from finsim.store import EventStore
from finsim.world import World, CommandError


class Script:
    """Runs the scenario against a fresh store and records what happened, so two runs can be compared."""

    def __init__(self, tc, seed=42):
        self.tc = tc
        self.seed = seed
        self.log = []
        self.store = EventStore(":memory:")

    # ------------------------------------------------------------------ helpers
    def check(self, step, text):
        w = self.w
        for pf in w.portfolios.values():
            assert_ledger_invariants(self.tc, w, pf)
        self.log.append((step, text, w.current_date.isoformat(), {pid: str(w.ledgers[pid].nav()) for pid in w.portfolios}))

    def adv(self, n=1):
        self.w.advance(n)

    # ------------------------------------------------------------------ the 45 steps
    def run(self):
        tc = self.tc
        w = self.w = World.create("m", "master", self.seed, date(2026, 1, 5), store=self.store)
        # 1 — a sandbox book, a bank desk and a risk manager (with four AI desks) live in the same world
        pf = w.create_portfolio("Main", "INSTITUTIONAL", D(100_000_000))
        bank = w.create_portfolio("Bank desk", "INSTITUTIONAL", JOBS["BANK_TRADER"].capital, job="BANK_TRADER")
        rm = w.create_portfolio("Risk", "INSTITUTIONAL", JOBS["RISK_MANAGER"].capital, job="RISK_MANAGER")
        tc.assertEqual(len(w.institutions.desks()), 4)
        tc.assertTrue(pf.briefings and bank.missions and rm.missions)
        self.check(1, "world, portfolios, briefing")
        # 2 — equities and Treasuries
        names = [s.id for s in w.securities.values() if s.asset_class == "EQUITY" and s.liquidity_tier == "LARGE"][:3]
        for n in names:
            w.place_order(pf.id, n, "BUY", 5_000)
        w.place_order(pf.id, "UST-10Y", "BUY", 20_000_000)
        gtc = w.place_order(pf.id, names[0], "BUY", 100, "LIMIT", limit_price=D("0.01"), time_in_force="GTC")
        self.adv(1)
        tc.assertTrue(all(pf.positions[n].quantity == 5_000 for n in names))
        tc.assertEqual(gtc.status, "WORKING")
        self.check(2, "fills at the session; a far-off GTC limit keeps working")
        # 3 — settlement T+1
        self.adv(1)
        tc.assertEqual(pf.positions["UST-10Y"].settled_quantity, D(20_000_000))
        self.check(3, "settlement")
        # 4 — repo the Treasuries (term), 5 — locate/borrow/short a dividend payer
        repo = w.open_repo(pf.id, "REPO", "UST-10Y", 20_000_000, "TERM", 10)
        tc.assertGreater(repo.principal, 0)
        cas = sorted((c for c in w.corporate_actions.values() if c.status == "DECLARED" and w.market.lending.state.get(c.security_id) and c.security_id not in names),
                     key=lambda c: c.ex_date)
        ca = cas[0]
        loc = w.request_locate(pf.id, ca.security_id, 20_000)
        loan = w.borrow_securities(pf.id, loc.id, min(loc.available, D(20_000)))
        w.place_order(pf.id, ca.security_id, "SELL", loan.quantity)
        # 6 — index futures, 7 — an equity call and a covered call, 8 — a vertical spread ticket
        fut = next(s for s in w.securities.values() if s.is_future and s.underlying_class == "EQUITY_INDEX" and not s.expired)
        w.place_order(pf.id, fut.id, "BUY", 5)
        under = names[0]
        chain = w.options.chain(under)
        exp = chain["expiries"][1]
        rows = w.options.chain(under, exp)["rows"]
        atm = min(rows, key=lambda r: abs(r["strike"] - chain["level"]))
        step = rows[1]["strike"] - rows[0]["strike"]
        w.place_order(pf.id, atm["C"]["id"], "BUY", 5)
        cc = w.place_strategy(pf.id, "COVERED_CALL", under, exp, [atm["strike"] + step], 10)
        spread = w.place_strategy(pf.id, "BULL_CALL_SPREAD", under, exp, [atm["strike"], atm["strike"] + step], 5)
        self.adv(1)
        short_qty = -pf.positions[ca.security_id].quantity
        tc.assertTrue(0 < short_qty <= loan.quantity, "the short fills up to the session's depth against the borrow")
        tc.assertEqual(pf.positions[fut.id].quantity, D(5))
        tc.assertIn(cc.status, ("FILLED", "WORKING"))
        tc.assertIn(spread.status, ("FILLED", "WORKING"))
        self.check(8, "short, futures, options, strategies")
        # 9 — OTC: a 5y payer swap and CDS protection on the weakest issuer, best quote wins
        r = w.request_quote(pf.id, "IRS", {"notional": 25_000_000, "tenor_years": 5, "pay_fixed": True})
        best = min((q for q in r.quotes if not q.get("declined")), key=lambda q: q["cost_vs_mid"])
        irs = w.execute_rfq(pf.id, r.id, best["dealer"])
        r2 = w.request_quote(pf.id, "CDS", {"reference": "NGSL-30", "notional": 5_000_000, "tenor_years": 5, "buyer": True})
        best2 = min((q for q in r2.quotes if not q.get("declined")), key=lambda q: q["cost_vs_mid"])
        cds = w.execute_rfq(pf.id, r2.id, best2["dealer"])
        # 10 — FX: buy EUR spot, sell EUR forward
        w.fx_spot(pf.id, "EUR", "USD", 2_000_000)
        w.fx_forward(pf.id, "USD", "EUR", 1_000_000, w.calendar.add_business_days(w.current_date, 60).isoformat())
        # 11 — commodities: a calendar spread, an option on the front crude contract, physical mode on
        cls = sorted([s for s in w.securities.values() if s.is_future and s.underlying == "CL" and not s.expired], key=lambda s: s.expiry)
        sp = w.place_spread(pf.id, cls[0].id, cls[2].id, "BUY", 5)
        fo_under = next(u for u in w.options.optionable_futures() if u.startswith("CL"))
        fo_calls = sorted([s for s in w.securities.values() if s.is_option and s.underlying == fo_under and s.option_type == "C"], key=lambda s: s.strike)
        fo = min(fo_calls, key=lambda s: abs(s.strike - w.options.underlying_level(fo_under)))
        w.place_order(pf.id, fo.id, "BUY", 2)
        w.set_physical_delivery(pf.id, True)
        w.place_order(pf.id, cls[0].id, "BUY", 2)
        # 12 — lend inventory from the sandbox book
        lend = w.lend_out(pf.id, names[1], 5_000)
        self.adv(1)
        tc.assertEqual(sp.status, "FILLED")
        tc.assertEqual(pf.positions[fo.id].quantity, D(2))
        tc.assertEqual(irs.status, "OPEN")
        tc.assertEqual(cds.status, "OPEN")
        tc.assertTrue(any(t.status in ("PENDING", "SETTLED") for t in pf.fx_trades.values()), "spot FX settles T+2")
        tc.assertEqual(lend["status"], "OPEN")
        self.check(12, "OTC, FX, commodity spread, option on future, lend")
        # 13 — the bank desk quotes every client request at mid; 14 — the risk manager decides a desk request
        for _ in range(3):
            for req in bank.client_rfqs.values():
                if req["status"] == "OPEN":
                    w.quote_client(bank.id, req["id"], float(req["mid"]))
            pend = [(d, x) for d in w.institutions.desks() for x in d.desk_requests.values() if x["status"] == "PENDING"]
            if pend:
                d0, x0 = pend[0]
                w.decide_request(rm.id, d0.id, x0["id"], True, "ok")
            self.adv(1)
        tc.assertGreater(bank.client_stats.get("requests", 0), 0)
        tc.assertTrue(any(x["status"] == "APPROVED" for d in w.institutions.desks() for x in d.desk_requests.values()))
        self.check(14, "client flow and desk oversight")
        # 15 — accruals and daily settlement: VM on futures, CSA margin, repo interest, borrow fees, lend fees
        tc.assertNotEqual(pf.positions[fut.id].variation_margin_total, ZERO)
        tc.assertGreater(repo.accrued_interest + repo.interest_paid, 0)
        tc.assertGreater(loan.accrued_fee + loan.fees_paid, 0)
        tc.assertGreater(D(lend["accrued_fee"]) + D(lend["fees_earned"]), 0)
        tc.assertGreater(pf.cash["EUR"].balance, 0)
        tc.assertTrue(any(f.status == "OPEN" for f in pf.fx_forwards.values()))
        snap = pf.nav_history[-1]
        tc.assertTrue(explain_reconciles(pf))
        # 16 — risk: VaR, stress (built-in and custom), liquidity, limits
        rep = w.risk.report(pf)
        tc.assertGreater(rep["var"]["var99"], 0)
        st = w.risk.stress(pf, custom={"equity": -0.2, "rates_bp": 100, "spreads_bp": 200, "vol_pts": 15, "commodity": -0.15, "fx": -0.05, "label": "Custom"})
        tc.assertTrue(any(s["label"] == "Custom" for s in st.values()))
        tc.assertTrue(w.risk.stress(pf), "the built-in scenarios run too")
        tc.assertIn("cash_ladder", rep["liquidity"])
        self.check(16, "accruals and risk")
        # 17 — corporate actions: a forced tender offer with an election, and a split on a held name
        eff = w.calendar.add_business_days(w.current_date, 4)
        px = float(w.market.last_bar(names[2]).close)
        tev = w.force_corporate_event(names[2], "TENDER_OFFER", {"offer_price": round(px * 1.2, 2), "max_pct": 0.5, "deadline": w.calendar.add_business_days(w.current_date, 2).isoformat(),
                                                                "bidder": "Atlas Capital Group"}, eff.isoformat())
        tender_id = tev.payload["id"]
        self.adv(1)
        r_el = w.elect(pf.id, tender_id, 2_000)
        tc.assertEqual(r_el["quantity"], D(2_000))
        held_before_split = pf.positions[names[1]].quantity
        w.force_split(names[1], 2.0)
        self.adv(1)
        tc.assertEqual(pf.positions[names[1]].quantity, held_before_split * 2, "2-for-1 split doubles the position")
        self.check(18, "tender election and split")
        # 19 — macro: releases and earnings are on the calendar and arrive as events
        up = w.market.macro.upcoming(w.current_date)
        tc.assertTrue(up)
        while not any(e.type == E.ECONOMIC_RELEASE for e in w.events[-400:]):
            self.adv(1)
        while w.current_date <= eff:
            self.adv(1)
        tc.assertLess(pf.positions[names[2]].quantity, D(5_000), "the tendered shares were bought by the bidder")
        self.check(19, "macro releases, tender settled")
        # 20 — credit event on the CDS reference: protection pays, bonds mark at recovery, equity collapses
        nav_before = w.ledgers[pf.id].nav()
        w.credit_event("NGSL-30", 0.3)
        tc.assertEqual(cds.status, "SETTLED_DEFAULT")
        tc.assertTrue(w.securities["NGSL-30"].defaulted)
        tc.assertTrue(any(cf["kind"] == "PROTECTION" for cf in cds.cashflows), "protection bought pays (1 − recovery)")
        self.adv(1)
        self.check(20, "credit event")
        # 21 — counterparty default of the swap dealer: close-out at recovery, CSA collateral applied
        w.default_counterparty(irs.counterparty, 0.4)
        tc.assertEqual(irs.status, "TERMINATED")
        tc.assertTrue(w.market.dealers.state[irs.counterparty].defaulted)
        with tc.assertRaises(CommandError):
            r3 = w.request_quote(pf.id, "IRS", {"notional": 1_000_000})
            w.execute_rfq(pf.id, r3.id, irs.counterparty)
        self.adv(1)
        self.check(21, "counterparty default")
        # 22 — liquidity crisis plus a rates sell-off: repo haircut widens; meet the call with cash
        if repo.status != "OPEN":            # the 10-day term repo has matured by now: fund the Treasuries on open repo instead
            tc.assertEqual(repo.status, "CLOSED")
            tc.assertGreater(repo.interest_paid, 0)
            repo = w.open_repo(pf.id, "REPO", "UST-10Y", 10_000_000, "OPEN")
        w.force_regime("LIQUIDITY_STRESS")
        w.force_rates(60, "dash for cash")
        self.adv(1)
        tc.assertEqual(w.market.state.regime, "LIQUIDITY_STRESS")
        tc.assertGreater(repo.haircut, 0.03)
        call = w.collateral.open_call(pf, "REPO", repo.id)
        if call is not None:
            w.repo_post_cash(pf.id, repo.id, call.amount + D(10_000))
            self.adv(1)
            tc.assertEqual(call.status, "MET")
        self.check(22, "crisis regime and collateral call")
        # 23 — prime brokerage: draw and repay on the margin line
        w.margin_draw(pf.id, 1_000_000)
        tc.assertEqual(pf.margin_loan, D(1_000_000))
        self.adv(1)
        # the prime broker sweeps excess cash against the line every night; whatever is left is repaid by hand
        tc.assertTrue(pf.margin_loan == ZERO or pf.margin_interest_accrued > ZERO)
        if pf.margin_loan > 0:
            w.margin_repay(pf.id, pf.margin_loan)
        tc.assertEqual(pf.margin_loan, ZERO)
        tc.assertTrue(any(m.kind.startswith("MARGIN") for m in pf.cash_movements), "the draw and its repayment are cash movements")
        # 24 — exercise the equity call early (American): shares delivered at the strike
        call_pos = next(p for p in pf.positions.values() if p.is_option and p.quantity > 0 and not w.securities[p.security_id].underlying.startswith("CL"))
        before = pf.positions[under].quantity
        w.exercise_option(pf.id, call_pos.security_id, 1)
        self.adv(1)
        tc.assertEqual(pf.positions[under].quantity, before + 100)
        self.check(24, "margin line and exercise")
        # 25 — the crude front month delivers into inventory; storage accrues; sell half at spot
        while w.current_date.isoformat() < cls[0].expiry:
            self.adv(1)
        inv = pf.positions.get("PHYS-CL")
        tc.assertIsNotNone(inv)
        tc.assertTrue(any(x["direction"] == "TAKE" for x in pf.physical_deliveries))
        self.adv(3)
        tc.assertGreater(w.ledgers[pf.id].balance("5320"), 0)
        w.place_order(pf.id, "PHYS-CL", "SELL", inv.quantity / 2)
        self.adv(1)
        self.check(25, "physical delivery, storage, spot sale")
        # 26 — recall the lent shares; 27 — return the borrow after covering the short
        w.recall_lent(pf.id, lend["id"])
        cover = w.place_order(pf.id, ca.security_id, "BUY", short_qty)
        self.adv(1)
        tc.assertEqual(pf.positions[ca.security_id].quantity, ZERO)
        self.adv(2)
        if loan.status == "OPEN":            # borrowed shares no longer needed for a short are otherwise returned automatically
            w.return_securities(pf.id, loan.id)
        tc.assertEqual(loan.status, "RETURNED")
        tc.assertEqual(lend["status"], "RETURNED")
        self.check(27, "recall and return")
        # 28 — close the repo, 29 — cancel the GTC order, 30 — terminate a remaining OTC trade (if any)
        if repo.status == "OPEN":
            w.close_repo(pf.id, repo.id)
        tc.assertEqual(repo.status, "CLOSED")
        w.cancel_order(pf.id, gtc.id)
        tc.assertEqual(gtc.status, "CANCELLED")
        r4 = w.request_quote(pf.id, "IRS", {"notional": 10_000_000, "tenor_years": 2, "pay_fixed": False})
        ok = [q for q in r4.quotes if not q.get("declined")]
        t4 = w.execute_rfq(pf.id, r4.id, min(ok, key=lambda q: q["cost_vs_mid"])["dealer"])
        self.adv(1)
        w.terminate_otc(pf.id, t4.id)
        tc.assertEqual(t4.status, "TERMINATED")
        self.check(30, "unwinds")
        # 31 — the dividend on the (formerly shorted) name pays: manufactured while short, real once covered
        while ca.status != "PAID" and w.current_date < date(2026, 4, 30):
            self.adv(1)
        tc.assertEqual(ca.status, "PAID")
        # 32 — month-end reviews for the job portfolios; missions progress
        while not any(bank.reviews) and w.current_date < date(2026, 4, 30):
            self.adv(1)
        tc.assertTrue(bank.reviews)
        tc.assertTrue(rm.reviews)
        tc.assertTrue(any(m["progress"] > 0 or m["status"] != "ACTIVE" for m in rm.missions))
        self.check(32, "dividends, reviews, missions")
        # 33 — the options and futures books have been through an expiry
        tc.assertTrue(any(e.type in (E.OPTION_EXPIRED, E.CONTRACT_EXPIRED) for e in w.events))
        # 34 — every snapshot's explain reconciles and NAV bridges hold
        tc.assertTrue(explain_reconciles(pf))
        for p in (bank, rm):
            tc.assertTrue(explain_reconciles(p))
        # 35 — briefings carry the new sections
        b = pf.briefings[-1]
        for k in ("derivatives", "otc", "risk", "macro", "corporate", "desk", "missions"):
            tc.assertIn(k, b)
        # 36 — audit drill-down: a trade links to its order and its settlement through cause ids
        tr = next(t for t in pf.trades.values() if t.status == "SETTLED")
        ev_trade = next(e for e in w.events if e.type == E.TRADE_EXECUTED and e.payload.get("trade_id") == tr.id)
        tc.assertIsNotNone(ev_trade.cause_id)
        chain_ok = any(e.cause_id == ev_trade.id for e in w.events)
        tc.assertTrue(chain_ok)
        # 37 — integrity and health
        tc.assertTrue(w.check_integrity()["ok"])
        self.check(37, "integrity")
        # 38 — the AI desks trade, 39 — the bank's client trades booked at the desk's levels
        tc.assertTrue(any(p.quantity for d in w.institutions.desks() for p in d.positions.values()))
        won = [x for x in bank.client_rfqs.values() if x["outcome"] == "WON"]
        tc.assertTrue(won)
        # 40 — risk limits were evaluated for every job portfolio
        tc.assertTrue(rm.risk_history and bank.risk_history)
        # 41 — macro state and central bank are live
        tc.assertTrue(w.market.macro.releases)
        tc.assertTrue(w.market.macro.went_live)
        # 42 — the lend and storage accounts are flat when nothing is out or held
        tc.assertEqual(w.ledgers[pf.id].balance("2460"), ZERO)
        # 43 — capital accounting: contributed capital equals the sum of contributions less withdrawals
        contrib = sum((D(e.payload["amount"]) for e in w.events if e.type == E.CAPITAL_CONTRIBUTED and e.payload["portfolio_id"] == pf.id), ZERO)
        tc.assertEqual(pf.contributed_capital, contrib)
        self.final_nav = {pid: str(w.ledgers[pid].nav()) for pid in w.portfolios}
        self.final_events = len(w.events)
        self.check(43, "books flat, capital reconciles")
        return self


class MasterScenarioTest(unittest.TestCase):
    def test_45_steps_replay_and_determinism(self):
        a = Script(self).run()
        w = a.w
        # 44 — replay: a fresh load reproduces every ledger, book and briefing
        w2 = World.load(a.store, "m")
        for pid in w.portfolios:
            self.assertEqual(w2.ledgers[pid].nav(), w.ledgers[pid].nav())
            self.assertEqual(len(w2.portfolios[pid].nav_history), len(w.portfolios[pid].nav_history))
            self.assertEqual(w2.portfolios[pid].briefings[-1]["attention"], w.portfolios[pid].briefings[-1]["attention"])
            self.assertEqual(w2.portfolios[pid].missions, w.portfolios[pid].missions)
        self.assertEqual(len(w2.events), len(w.events))
        self.assertTrue(w2.integrity["ok"])
        # 45 — determinism: the same script on a fresh store produces the same NAVs at every checkpoint and the same event count
        b = Script(self).run()
        self.assertEqual(a.log, b.log)
        self.assertEqual(a.final_nav, b.final_nav)
        self.assertEqual(a.final_events, b.final_events)


if __name__ == "__main__":
    unittest.main()
