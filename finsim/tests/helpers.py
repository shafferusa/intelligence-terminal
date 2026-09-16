import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from finsim.money import D  # noqa: E402
from finsim.store import EventStore  # noqa: E402
from finsim.world import World  # noqa: E402
from finsim.engines.ledger import cost_account, adj_account  # noqa: E402


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
        if pos.is_future:
            tc.assertEqual(pos.settled_quantity, pos.quantity)
            tc.assertEqual(pos.market_value, D(0))
            tc.assertEqual(pos.valuation_adjustment, D(0))
            tc.assertEqual(led.security_balance(pos.security_id, "4400"), pos.variation_margin_total)
            continue
        acct, sacct = cost_account(sec, False), cost_account(sec, True)
        ladj, sadj = adj_account(sec, False), adj_account(sec, True)
        tc.assertEqual(sum((l.quantity for l in pos.lots), D(0)), pos.quantity, f"lots {pos.security_id}")
        tc.assertEqual(sum((l.cost_total for l in pos.lots), D(0)), pos.cost_basis, f"lot cost {pos.security_id}")
        tc.assertEqual(pos.valuation_adjustment, pos.market_value - pos.cost_basis, f"valuation {pos.security_id}")
        if pos.quantity < 0:
            tc.assertEqual(led.security_balance(pos.security_id, sacct), -pos.cost_basis, f"short proceeds {pos.security_id}")
            tc.assertEqual(led.security_balance(pos.security_id, sadj), -pos.valuation_adjustment, f"short MTM {pos.security_id}")
            tc.assertEqual(led.security_balance(pos.security_id, acct), D(0))
            tc.assertEqual(led.security_balance(pos.security_id, ladj), D(0))
            tc.assertGreaterEqual(pos.borrowed_quantity, D(0))
        else:
            tc.assertEqual(led.security_balance(pos.security_id, acct), pos.cost_basis, f"cost basis {pos.security_id}")
            tc.assertEqual(led.security_balance(pos.security_id, ladj), pos.valuation_adjustment, f"long MTM {pos.security_id}")
            tc.assertEqual(led.security_balance(pos.security_id, sacct), D(0))
            tc.assertEqual(led.security_balance(pos.security_id, sadj), D(0))
        if sec.is_option:
            tc.assertEqual(pos.borrowed_quantity, D(0))
        # custody box = economic position + borrowed shares (in transit adjustments)
        tc.assertEqual(pos.settled_quantity + pos.pending_receive - pos.pending_deliver, pos.quantity + pos.borrowed_quantity, f"custody {pos.security_id}")
        if not sec.is_option:
            tc.assertLessEqual(pf.pledged_quantity(pos.security_id), pos.settled_quantity, f"pledged more than in custody {pos.security_id}")
        tc.assertEqual(pos.borrowed_quantity, sum((l.quantity for l in pf.loans.values() if l.security_id == pos.security_id and l.status == "OPEN"), D(0)))
    for ccy, ca in pf.cash.items():
        tc.assertEqual(led.balance(f"1010:{ccy}"), ca.base_value, f"cash carrying value {ccy}")
    tc.assertEqual(led.balance("1300"), w.futures.total_required(pf) if any((p.is_future or p.is_option) and p.quantity for p in pf.positions.values()) else D(0),
                   "clearing margin = futures IM + options margin")
    tc.assertEqual(led.balance("1300"), w.futures.required_margin(pf) + pf.options_margin)
    tc.assertEqual(led.balance("1400"), sum(pf.cash_collateral.values(), D(0)), "cash collateral posted")
    tc.assertEqual(led.balance("2310"), sum((l.accrued_fee for l in pf.loans.values()), D(0)), "accrued borrow fees")
    tc.assertEqual(led.balance("1410"), sum((l.accrued_rebate for l in pf.loans.values()), D(0)), "accrued rebates")
    tc.assertEqual(led.balance("2320"), pf.manufactured_payable, "manufactured dividends payable")
    tc.assertEqual(led.balance("2500"), sum((r.principal for r in pf.repos.values() if r.status == "OPEN" and r.side == "REPO"), D(0)), "repo borrowing")
    tc.assertEqual(led.balance("1500"), sum((r.principal for r in pf.repos.values() if r.status == "OPEN" and r.side == "REVERSE"), D(0)), "reverse repo")
    tc.assertEqual(led.balance("2330"), sum((r.accrued_interest for r in pf.repos.values() if r.status == "OPEN" and r.side == "REPO"), D(0)), "accrued repo interest")
    tc.assertEqual(led.balance("1240"), sum((r.accrued_interest for r in pf.repos.values() if r.status == "OPEN" and r.side == "REVERSE"), D(0)), "accrued reverse repo interest")
    tc.assertEqual(led.balance("2700"), pf.margin_loan, "margin loan")
    tc.assertEqual(led.balance("2340"), pf.margin_interest_accrued, "accrued margin interest")
    tc.assertEqual(led.balance("1600"), sum((f.mtm for f in pf.fx_forwards.values() if f.status == "OPEN"), D(0)), "fx forward MTM")
    pending_fx = sum((t.usd_value for t in pf.fx_trades.values() if t.status == "PENDING"), D(0))
    tc.assertEqual(led.balance("1250"), pending_fx, "fx receivable")
    tc.assertEqual(led.balance("2350"), pending_fx, "fx payable")
    open_otc = [t for t in pf.otc_trades.values() if t.status == "OPEN"]
    tc.assertEqual(led.balance("1800"), sum((t.mtm for t in open_otc if t.mtm > 0), D(0)), "OTC derivative assets = positive PVs")
    tc.assertEqual(led.balance("2800"), sum((-t.mtm for t in open_otc if t.mtm < 0), D(0)), "OTC derivative liabilities = negative PVs")
    for t in pf.otc_trades.values():
        if t.status != "OPEN":
            tc.assertEqual(led.security_balance(t.id, "1800"), D(0), f"closed trade {t.id} still on the balance sheet")
            tc.assertEqual(led.security_balance(t.id, "2800"), D(0))
    tc.assertEqual(led.balance("2450"), sum((c.vm_received for c in pf.csas.values()), D(0)), "VM received")
    tc.assertEqual(sum((v for k, v in pf.cash_collateral.items() if k.startswith(("CSA:", "IM:"))), D(0)),
                   sum((c.vm_posted + c.im_posted for c in pf.csas.values()), D(0)), "VM/IM posted under CSAs")
    for c in pf.csas.values():
        tc.assertFalse(c.vm_posted > 0 and c.vm_received > 0, f"VM cannot be posted and received at once under CSA {c.counterparty}")
    tc.assertGreaterEqual(pf.cash_account(pf.base_currency).balance, D(0), "base cash must never stay negative: shortfalls become margin loans")
    for p in pf.pledges:
        tc.assertGreater(p.quantity, D(0))
