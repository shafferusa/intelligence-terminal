"""Phase 10 — AI institutions: rule-based desks that trade through the same engines as the player, and the risk
manager who oversees them (requests, limits, forced reductions, missions)."""
import unittest
from datetime import date

from helpers import assert_ledger_invariants
from finsim.world import World, CommandError
from finsim.store import EventStore
from finsim.money import D, ZERO
from finsim.engines.institutions import AI_JOBS, REQUEST_TTL


def rm_world(seed=42, start="2026-01-05", scenario="NONE"):
    from finsim.careers import JOBS
    store = EventStore(":memory:")
    w = World.create("t", "test", seed, date.fromisoformat(start), store=store, scenario=scenario)
    rm = w.create_portfolio("Risk", "INSTITUTIONAL", JOBS["RISK_MANAGER"].capital, job="RISK_MANAGER")
    return w, rm, store


def check_all(tc, w):
    for p in w.portfolios.values():
        assert_ledger_invariants(tc, w, p)


class DesksTest(unittest.TestCase):
    def test_risk_manager_world_creates_four_desks_that_trade_and_replay(self):
        w, rm, store = rm_world()
        desks = w.institutions.desks()
        self.assertEqual(sorted(d.job for d in desks), sorted(AI_JOBS))
        self.assertTrue(all(d.desk_limits == {"gross_mult": 1.0, "request_mult": 1.0, "var_mult": 1.0} for d in desks))
        with self.assertRaises(CommandError):
            w.create_portfolio("X", "FUND", D(1), job="AI_CARRY")     # desks are created by the world, not by hand
        for _ in range(REQUEST_TTL + 3):
            w.advance(1)
            check_all(self, w)
        reqs = [r for d in desks for r in d.desk_requests.values()]
        self.assertTrue(reqs, "large desk orders are escalated to the risk manager")
        lapsed = [r for r in reqs if r["status"] == "LAPSED"]
        self.assertTrue(lapsed, "undecided requests lapse after the TTL")
        self.assertTrue(any(r.get("clips", 0) > 0 for r in lapsed), "lapsed requests are worked in clips")
        traded = [d for d in desks if any(p.quantity for p in d.positions.values())]
        self.assertGreaterEqual(len(traded), 2, "desks end up with positions through the ordinary order flow")
        self.assertTrue(all(o.strategy_tag and o.strategy_tag.startswith("AI:") for d in desks for o in d.orders.values()))
        self.assertFalse(any(r["status"] == "PENDING" and r["security_id"] == r2["security_id"] and r is not r2 and r2["status"] == "PENDING"
                             for d in desks for r in d.desk_requests.values() for r2 in d.desk_requests.values()), "one pending request per security")
        w2 = World.load(store, "t")
        for d in desks:
            self.assertEqual(w2.ledgers[d.id].nav(), w.ledgers[d.id].nav())
            self.assertEqual({k: (r["status"], str(r.get("remaining", ""))) for k, r in w2.portfolios[d.id].desk_requests.items()},
                             {k: (r["status"], str(r.get("remaining", ""))) for k, r in d.desk_requests.items()})

    def test_no_requests_without_a_risk_manager(self):
        from finsim.careers import JOBS
        store = EventStore(":memory:")
        w = World.create("t", "test", 42, date(2026, 1, 5), store=store)
        pm = w.create_portfolio("PM", "FUND", JOBS["PORTFOLIO_MANAGER"].capital, job="PORTFOLIO_MANAGER")
        self.assertEqual(w.institutions.desks(), [])
        w.advance(2)
        self.assertEqual(pm.desk_requests, {})


class OversightTest(unittest.TestCase):
    def test_decide_limits_force_reduce_and_missions(self):
        w, rm, store = rm_world()
        desks = {d.job: d for d in w.institutions.desks()}
        w.advance(1)
        pending = [(d, r) for d in desks.values() for r in d.desk_requests.values() if r["status"] == "PENDING"]
        self.assertTrue(pending)
        d0, r0 = pending[0]
        orders_before = len(d0.orders)
        out = w.decide_request(rm.id, d0.id, r0["id"], True, "fine")
        self.assertEqual(out["status"], "APPROVED")
        self.assertEqual(out["decision"]["by"], rm.id)
        self.assertEqual(len(d0.orders), orders_before + 1, "an approved request is placed at once")
        with self.assertRaises(CommandError):
            w.decide_request(rm.id, d0.id, r0["id"], True)          # already decided
        if len(pending) > 1:
            d1, r1 = pending[1]
            n1 = len(d1.orders)
            w.decide_request(rm.id, d1.id, r1["id"], False, "too big")
            self.assertEqual(d1.desk_requests[r1["id"]]["status"], "REJECTED")
            self.assertEqual(len(d1.orders), n1)
        w.advance(1)
        o = next(o for o in d0.orders.values() if o.strategy_tag and r0["id"] in o.strategy_tag)
        self.assertIn(o.status, ("FILLED", "PARTIALLY_FILLED", "WORKING"))
        check_all(self, w)
        # limits: a higher request multiplier lets the desk self-execute bigger clips
        with self.assertRaises(CommandError):
            w.set_desk_limit(rm.id, d0.id, "nonsense", 1.0)
        lim = w.set_desk_limit(rm.id, d0.id, "request_mult", 3.0)
        self.assertEqual(lim["request_mult"], 3.0)
        self.assertEqual(World.load(store, "t").portfolios[d0.id].desk_limits["request_mult"], 3.0)
        # forced reduction: a market order on the desk's behalf
        holder = next((d for d in desks.values() if any(p.quantity > 0 and not p.is_future for p in d.positions.values())), None)
        if holder is None:
            for _ in range(REQUEST_TTL + 2):
                w.advance(1)
            holder = next(d for d in desks.values() if any(p.quantity > 0 and not p.is_future for p in d.positions.values()))
        pos = next(p for p in holder.positions.values() if p.quantity > 0 and not p.is_future)
        q0 = pos.quantity
        res = w.force_reduce(rm.id, holder.id, pos.security_id, 0.5)
        self.assertEqual(res["side"], "SELL")
        w.advance(1)
        self.assertLess(holder.positions[pos.security_id].quantity, q0)
        self.assertTrue(any("risk manager ordered" in n["note"] for n in holder.desk_notes))
        ov = w.institutions.oversight(rm)
        self.assertEqual(len(ov["desks"]), 4)
        self.assertEqual(ov["firm_nav"], sum((w.ledgers[d.id].nav() for d in desks.values()), ZERO))
        # the risk manager's mission counts decisions
        ms = {m["id"]: m for m in rm.missions}
        self.assertGreater(ms["RM_DECIDE"]["progress"], 0.0)
        self.assertEqual(ms["RM_DECIDE"]["progress"], min(1.0, sum(1 for d in desks.values() for r in d.desk_requests.values() if r["status"] in ("APPROVED", "REJECTED")) / 8))
        check_all(self, w)

    def test_desk_breaches_reach_the_risk_manager_briefing(self):
        w, rm, store = rm_world(seed=11)
        mom = next(d for d in w.institutions.desks() if d.job == "AI_MOMENTUM")
        # let the desk size up beyond its concentration limit
        w.set_desk_limit(rm.id, mom.id, "gross_mult", 3.0)
        w.set_desk_limit(rm.id, mom.id, "request_mult", 3.0)
        for _ in range(8):
            w.advance(1)
        self.assertTrue(mom.breaches, "an oversized desk breaches its mandate")
        att = " ".join(a["text"] for a in rm.briefings[-1]["attention"])
        self.assertIn(mom.name, att, "desk breaches are surfaced to the risk manager")
        clean = next(m for m in rm.missions if m["id"] == "RM_CLEAN")
        self.assertNotEqual(clean["status"], "COMPLETED")
        check_all(self, w)


if __name__ == "__main__":
    unittest.main()
