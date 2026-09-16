"""Spec §46: the 17-step first build, asserted end to end."""
import unittest

from tests.helpers import D, assert_ledger_invariants, make_world
from finsim.domain.events import E
from finsim.world import World


class VerticalSliceTest(unittest.TestCase):
    def test_full_flow(self):
        w, pf, store = make_world()
        led = w.ledgers[pf.id]
        # 1. start with $10,000,000
        self.assertEqual(pf.cash["USD"].balance, D("10000000.00"))
        self.assertEqual(led.nav(), D("10000000.00"))
        self.assertEqual(led.balance("3000"), D("10000000.00"))
        # 2. simulated market exists
        self.assertGreater(len(w.securities), 25)
        self.assertGreater(len(w.market.history["NVRA"]), 250)
        ca = sorted((c for c in w.corporate_actions.values() if c.status == "DECLARED"), key=lambda c: c.ex_date)[0]
        tkr = ca.security_id
        bar = w.market.last_bar(tkr)
        # 3-4. buy 10,000 shares: the instruction waits for the next daily update, then fills at the open
        o = w.place_order(pf.id, tkr, "BUY", 10000)
        self.assertEqual(o.status, "WORKING")
        self.assertEqual(pf.trades, {})
        w.advance(1)
        self.assertEqual(o.status, "FILLED")
        t = pf.trades[o.trade_ids[0]]
        self.assertEqual(t.trade_date, w.current_date.isoformat())
        bar = w.market.last_bar(tkr)
        self.assertGreaterEqual(t.price, bar.open)
        self.assertGreater(t.execution_detail["impact_bps"], 0)
        self.assertEqual(t.gross_amount, D(10000) * t.price)
        self.assertEqual(t.net_amount, t.gross_amount + t.commission)
        # ledger: Dr investments, Dr commission, Cr payable
        self.assertEqual(led.balance("1100"), t.gross_amount)
        self.assertEqual(led.balance("2100"), t.net_amount)
        self.assertEqual(led.balance("5000"), t.commission)
        self.assertEqual(pf.cash["USD"].balance, D("10000000.00"), "cash does not move on trade date")
        self.assertIn(t.status, ("SETTLEMENT_PENDING", "CAPTURED"), "post-trade lifecycle runs the same day (CAPTURED only on a seeded match break)")
        # 5. settlement obligation
        si = pf.settlements[t.settlement_instruction_id]
        self.assertEqual(si.status, "PENDING")
        self.assertEqual(si.instruction_type, "RVP")
        self.assertEqual(si.cash_amount, t.net_amount)
        self.assertEqual(si.settlement_date, w.calendar.add_business_days(w.current_date, 1).isoformat())
        pos = pf.positions[tkr]
        self.assertEqual(pos.quantity, D(10000))
        self.assertEqual(pos.settled_quantity, D(0))
        self.assertEqual(pos.pending_receive, D(10000))
        assert_ledger_invariants(self, w, pf)
        # 6-9. advance: settle into custody, cash leaves, position appears in custody
        w.advance(1)
        self.assertEqual(w.current_date.isoformat(), si.settlement_date)
        for _ in range(2):                       # a seeded match break settles one day late
            if t.status == "SETTLED":
                break
            w.advance(1)
        self.assertEqual(t.status, "SETTLED")
        seen = [h["status"] for h in t.status_history]
        chain = ["EXECUTED", "CAPTURED", "MATCHED", "AFFIRMED", "CLEARED", "SETTLEMENT_PENDING", "SETTLED"]
        it = iter(seen)
        self.assertTrue(all(any(x == c for x in it) for c in chain), f"lifecycle {seen} must pass through {chain} in order")
        self.assertEqual(si.status, "SETTLED")
        self.assertEqual(pos.settled_quantity, D(10000))
        self.assertEqual(pos.pending_receive, D(0))
        self.assertEqual(pf.cash["USD"].balance, D("10000000.00") - t.net_amount)
        self.assertEqual(led.balance("2100"), D(0))
        kinds = [m.kind for m in pf.cash_movements]
        self.assertEqual(kinds, ["CAPITAL", "SETTLEMENT"])
        self.assertEqual([m.kind for m in pf.custody_movements], ["RECEIVE"])
        # 10-11. prices move, unrealized updates and is in the ledger
        self.assertNotEqual(pos.mark, t.price)
        self.assertEqual(pos.unrealized_pnl, pos.market_value - pos.cost_basis)
        self.assertEqual(led.balance("4100"), pos.unrealized_pnl)
        for s in pf.nav_history:
            self.assertEqual(s.ledger_nav, s.nav)
        assert_ledger_invariants(self, w, pf)
        # 12-13. dividend: entitlement on ex-date, cash on pay date
        while ca.status != "EX":
            w.advance(1)
        ent = ca.entitlements[pf.id]
        self.assertEqual(ent["amount"], D(10000) * ca.amount_per_unit)
        self.assertEqual(led.balance("1210"), ent["amount"])
        self.assertEqual(led.balance("4200"), ent["amount"])
        while ca.status != "PAID":
            w.advance(1)
        self.assertEqual(led.balance("1210"), D(0))
        divs = [m for m in pf.cash_movements if m.kind == "DIVIDEND"]
        self.assertEqual(len(divs), 1)
        self.assertEqual(divs[0].amount, ent["amount"])
        self.assertEqual(divs[0].date, ca.pay_date)
        # 14-15. sell part: realized P&L via FIFO
        o2 = w.place_order(pf.id, tkr, "SELL", 4000)
        w.advance(1)
        cash_before = pf.cash["USD"].balance
        t2 = pf.trades[o2.trade_ids[0]]
        self.assertEqual(t2.realized_pnl, t2.gross_amount - D(4000) * t.price)
        self.assertEqual(pos.quantity, D(6000))
        self.assertEqual(pos.cost_basis, D(6000) * t.price)
        self.assertEqual(led.balance("4000"), t2.realized_pnl)
        self.assertEqual(led.balance("1200"), t2.net_amount)
        self.assertEqual(pf.cash["USD"].balance, cash_before, "proceeds arrive on settlement, not trade date")
        # 16. accounting entries: every ledger entry links back to a causing event
        for e in led.entries:
            self.assertIn(e.event_id, w.events_by_id)
            self.assertIn(e.cause_id, w.events_by_id)
        w.advance(1)
        self.assertEqual(t2.status, "SETTLED")
        self.assertEqual(pf.cash["USD"].balance, cash_before + t2.net_amount)
        self.assertEqual(pos.settled_quantity, D(6000))
        assert_ledger_invariants(self, w, pf)
        # 17. audit trail
        ev = next(e for e in w.events if e.type == E.TRADE_EXECUTED and e.payload["trade_id"] == t.id)
        chain = w.audit_chain(ev.id)
        self.assertEqual([a.type for a in chain["ancestors"]], [E.DAY_STARTED, E.MARKET_CLOSE])
        self.assertEqual(ev.payload["order_id"], o.id)
        child_types = [c["event"].type for c in chain["tree"]["children"]]
        self.assertEqual(child_types, [E.LEDGER_POSTED, E.SETTLEMENT_INSTRUCTION_CREATED, E.VALUATION_MARKED])
        # NAV explain reconciles every day
        for s in pf.nav_history:
            comps = sum((v for k, v in s.explain.items() if not k.startswith("_")), D(0))
            self.assertEqual(comps, s.day_pnl)
        total_pnl = sum((s.day_pnl for s in pf.nav_history), D(0))
        self.assertEqual(pf.nav_history[-1].nav, pf.contributed_capital + total_pnl)
        # a briefing exists for every processed day and reports the same P&L
        self.assertEqual(pf.briefings[-1]["day_pnl"], pf.nav_history[-1].day_pnl)
        self.assertTrue(any(f["trade_id"] == t2.id for b in pf.briefings for f in b["orders_summary"]["filled_today"]))
        # replay from the store reproduces the state exactly
        w2 = World.load(store, "t")
        pf2 = w2.portfolios[pf.id]
        self.assertEqual(len(w2.events), len(w.events))
        self.assertEqual(w2.ledgers[pf.id].nav(), led.nav())
        self.assertEqual(w2.ledgers[pf.id].trial_balance(), led.trial_balance())
        self.assertEqual(pf2.positions[tkr].quantity, pos.quantity)
        self.assertEqual(pf2.positions[tkr].lots[0].cost_total, pos.lots[0].cost_total)
        self.assertEqual(pf2.cash["USD"].balance, pf.cash["USD"].balance)
        self.assertEqual(w2.market.last_bar(tkr).close, w.market.last_bar(tkr).close)
        self.assertEqual(len(pf2.nav_history), len(pf.nav_history))
        self.assertEqual(pf2.nav_history[-1].explain, pf.nav_history[-1].explain)


if __name__ == "__main__":
    unittest.main()
