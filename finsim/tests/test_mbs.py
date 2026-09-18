"""Agency MBS: the TBA coupon stack, negative convexity, the dollar roll, forward settlement, monthly coupons and paydowns, repo, replay."""
import unittest
from datetime import date

try:
    from helpers import make_world, assert_ledger_invariants
except ImportError:
    from tests.helpers import make_world, assert_ledger_invariants

from finsim.engines.mbs import PROGRAMS, cpr, mortgage_rate
from finsim.money import D, ZERO
from finsim.world import World


def tbas(w, code="FNCL"):
    """The TBAs still to be delivered (delivered months are pools)."""
    return sorted((s for s in w.securities.values() if s.is_mbs and s.underlying == code and not s.delisted and w.market.history.get(s.id)
                   and date.fromisoformat(s.issue_date) > w.current_date), key=lambda s: (s.contract_month, s.coupon))


class MBSTest(unittest.TestCase):
    def test_coupon_stack_convexity_and_the_roll(self):
        w, pf, _ = make_world()
        front = [s for s in tbas(w) if s.contract_month == min(x.contract_month for x in tbas(w))]
        self.assertEqual(len(front), len(PROGRAMS[0].coupons))
        px = {s.coupon: float(w.market.last_bar(s.id).close) for s in front}
        cps = sorted(px)
        for a, b in zip(cps, cps[1:]):
            self.assertLess(px[a], px[b], "a higher coupon is worth more")
        m = {s.coupon: w.market.mbs_metrics(s) for s in front}
        lo, hi = m[cps[0]], m[cps[-1]]
        self.assertGreater(lo["convexity"], 0, "a deep discount behaves like a bond")
        self.assertLess(hi["convexity"], 0, "a premium coupon is negatively convex: prepayments cap the upside")
        self.assertGreater(lo["effective_duration"], hi["effective_duration"])
        self.assertGreater(hi["cpr"], lo["cpr"]); self.assertLess(hi["wal"], lo["wal"])
        for x in m.values():
            self.assertGreater(x["ytm"], 0.02); self.assertGreater(x["mortgage_rate"], x["benchmark_yield"])
        # the same coupon a month later is cheaper by carry less financing: the drop
        months = sorted({s.contract_month for s in tbas(w)})
        self.assertEqual(len(months), 2)
        f = next(s for s in tbas(w) if s.contract_month == months[0] and s.coupon == 0.055)
        b = next(s for s in tbas(w) if s.contract_month == months[1] and s.coupon == 0.055)
        drop = float(w.market.last_bar(f.id).close) - float(w.market.last_bar(b.id).close)
        self.assertGreater(drop, 0.02); self.assertLess(drop, 0.6)
        # the S-curve: more incentive, faster
        spec = PROGRAMS[0]
        self.assertLess(cpr(spec, 0.05, 0.067, 12), cpr(spec, 0.07, 0.067, 12)); self.assertLess(cpr(spec, 0.07, 0.067, 12), cpr(spec, 0.09, 0.067, 12))
        self.assertAlmostEqual(mortgage_rate(w.market.curve()), w.market.curve().rates[7] + 0.017, places=6)

    def test_forward_settlement_coupons_paydowns_and_replay(self):
        w, pf, store = make_world(capital=50_000_000)
        s = next(x for x in tbas(w) if x.contract_month == min(y.contract_month for y in tbas(w)) and x.coupon == 0.055)
        settle = date.fromisoformat(s.issue_date)
        self.assertGreater(settle, w.current_date)
        cash0 = pf.cash_account("USD").balance
        w.place_order(pf.id, s.id, "BUY", 10_000_000)
        w.advance(1)
        t = [x for x in pf.trades.values() if x.security_id == s.id][0]
        self.assertEqual(t.settlement_date, settle.isoformat(), "a TBA settles on its delivery day, not T+1")
        self.assertEqual(t.accrued_interest, ZERO, "no accrued before delivery")
        self.assertEqual(pf.cash_account("USD").balance, cash0, "cash moves at delivery, not at the trade")
        # a TBA can be sold forward without a borrow
        short = next(x for x in tbas(w) if x.contract_month == s.contract_month and x.coupon == 0.05)
        w.place_order(pf.id, short.id, "SELL", 1_000_000)
        w.advance(1)
        self.assertLess(pf.position(short.id).quantity, 0)
        w.place_order(pf.id, short.id, "BUY", 1_000_000)      # cover before delivery
        # through delivery: cash paid, the pool is in custody, then the coupon and the first paydown
        days = w.calendar.business_days_between(w.current_date, settle) + 1
        w.advance(days)
        pos = pf.position(s.id)
        self.assertEqual(pos.settled_quantity, D(10_000_000)); self.assertLess(pf.cash_account("USD").balance, cash0 - D(9_000_000))
        self.assertEqual(pf.position(short.id).quantity, ZERO)
        assert_ledger_invariants(self, w, pf)
        w.advance(30)
        self.assertGreater(pos.accrued_interest, 0, "a delivered pool accrues")
        cash_before = pf.cash_account("USD").balance
        w.advance(25)
        paid = [e for e in w.events if e.type == "MBS_PAID_DOWN" and e.payload["security_id"] == s.id]
        coupons = [e for e in w.events if e.type == "COUPON_PAID" and e.payload["security_id"] == s.id]
        self.assertTrue(paid, "a month after delivery the pool returned principal"); self.assertTrue(coupons, "and paid its monthly coupon")
        p = paid[0].payload
        self.assertEqual(pos.quantity, D(10_000_000) - sum((D(e.payload["face_paid"]) for e in paid), ZERO), "every paydown came off the face")
        self.assertLess(pos.quantity, D(10_000_000)); self.assertGreater(pos.quantity, D(9_000_000))
        self.assertAlmostEqual(float(D(p["principal"]) - D(p["cost_removed"])), float(D(p["realized"])), places=2)
        self.assertGreater(pf.cash_account("USD").balance, cash_before, "coupon and principal came in")
        self.assertLess(w.market.mbs.pool(s).factor, 1.0)
        assert_ledger_invariants(self, w, pf)
        # repo against the pool at the MBS haircut
        r = w.repo_borrow(pf.id, s.id, 5_000_000, term_days=7) if hasattr(w, "repo_borrow") else None
        if r is not None:
            assert_ledger_invariants(self, w, pf)
        # replay: factors, quantities and the ledger come back exactly
        w2 = World.load(store, w.id)
        self.assertEqual(w2.replay_errors, [])
        p2 = w2.portfolio(pf.id).position(s.id)
        self.assertEqual((p2.quantity, p2.cost_basis, p2.realized_pnl, p2.settled_quantity), (pos.quantity, pos.cost_basis, pos.realized_pnl, pos.settled_quantity))
        self.assertEqual(w2.market.mbs.pool(w2.securities[s.id]).factor, w.market.mbs.pool(s).factor)
        self.assertEqual(w2.ledgers[pf.id].nav(), w.ledgers[pf.id].nav())
        self.assertEqual(w2.market.last_bar(s.id).close, w.market.last_bar(s.id).close)


if __name__ == "__main__":
    unittest.main()
