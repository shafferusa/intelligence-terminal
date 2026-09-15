import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from finsim.money import D  # noqa: E402
from finsim.store import EventStore  # noqa: E402
from finsim.world import World  # noqa: E402


def make_world(seed=42, start="2026-01-05", capital=10_000_000, store=None, regime="NORMAL_GROWTH"):
    store = store or EventStore(":memory:")
    w = World.create("t", "test", seed, date.fromisoformat(start), store=store, initial_regime=regime)
    pf = w.create_portfolio("Test", "PERSONAL", D(capital))
    return w, pf, store


def assert_ledger_invariants(tc, w, pf):
    led = w.ledgers[pf.id]
    tb = led.trial_balance()
    tc.assertTrue(tb["balanced"], "trial balance must balance")
    s = w.pnl.compute_summary(pf)
    tc.assertEqual(led.nav(), s["nav"], "ledger NAV must equal economic NAV")
    # sub-ledgers reconcile to the GL
    for pos in pf.positions.values():
        sec = w.securities[pos.security_id]
        acct = "1110" if sec.is_bond else "1100"
        tc.assertEqual(led.security_balance(pos.security_id, acct), pos.cost_basis, f"cost basis {pos.security_id}")
        tc.assertEqual(led.security_balance(pos.security_id, "1150"), pos.valuation_adjustment)
        tc.assertEqual(pos.valuation_adjustment, pos.market_value - pos.cost_basis)
        tc.assertEqual(sum((l.quantity for l in pos.lots), D(0)), pos.quantity)
        tc.assertEqual(sum((l.cost_total for l in pos.lots), D(0)), pos.cost_basis)
        tc.assertEqual(pos.settled_quantity + pos.pending_receive - pos.pending_deliver, pos.quantity)
    for ccy, ca in pf.cash.items():
        tc.assertEqual(led.balance(f"1010:{ccy}"), ca.balance)
