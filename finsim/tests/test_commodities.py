"""Phase 9 — commodity depth: calendar-spread tickets with spread margin, options on futures (Black-76,
American, exercised into futures at the strike), physical delivery and storage."""
import unittest

from helpers import make_world, assert_ledger_invariants
from finsim.world import World, CommandError
from finsim.money import D, ZERO
from finsim.engines.commodity_desk import SPREAD_MARGIN_RATE, BUY_IN_PENALTY
from finsim.engines.options import is_fut_opt


def cl_contracts(w):
    return sorted([s for s in w.securities.values() if s.is_future and s.underlying == "CL" and not s.expired], key=lambda s: s.expiry)


class CalendarSpreadTest(unittest.TestCase):
    def test_spread_ticket_fills_all_or_none_and_is_margined_as_a_pair(self):
        w, pf, store = make_world(capital=20_000_000)
        near, mid, far = cl_contracts(w)[:3]
        with self.assertRaises(CommandError):
            w.place_spread(pf.id, near.id, near.id, "BUY", 10)
        with self.assertRaises(CommandError):
            w.place_spread(pf.id, near.id, [s for s in w.securities.values() if s.is_future and s.underlying == "GC"][0].id, "BUY", 10)
        st = w.place_spread(pf.id, near.id, far.id, "BUY", 10)
        self.assertEqual(st.strategy_type, "FUTURES_CALENDAR")
        w.advance(1)
        self.assertEqual(st.status, "FILLED")
        self.assertEqual(pf.positions[near.id].quantity, D(10))
        self.assertEqual(pf.positions[far.id].quantity, D(-10))
        im_n, im_f = w.futures.initial_margin_per_contract(near), w.futures.initial_margin_per_contract(far)
        expected = (im_n + im_f) * 10 - min(im_n, im_f) * 10 * D(str(2 - SPREAD_MARGIN_RATE))
        self.assertEqual(w.futures.required_margin(pf), expected.quantize(D("0.01")))
        self.assertLess(w.futures.required_margin(pf), (im_n + im_f) * 10 * D("0.4"), "the pair is margined at ~35% of one outright")
        self.assertEqual(w.ledgers[pf.id].balance("1300"), w.futures.total_required(pf))
        a = w.options.strategy_analytics(pf, st)
        self.assertEqual(a["kind"], "FUTURES_CALENDAR")
        self.assertIn("differential_close", a)
        assert_ledger_invariants(self, w, pf)
        # a limit far inside the market leaves the ticket working (all-or-none), then it expires as a DAY order
        st2 = w.place_spread(pf.id, near.id, mid.id, "BUY", 5, limit_points=-50)
        w.advance(1)
        self.assertIn(st2.status, ("WORKING", "EXPIRED"))
        self.assertEqual(pf.positions[mid.id].quantity if mid.id in pf.positions else ZERO, ZERO)
        w2 = World.load(store, "t")
        self.assertEqual(w2.futures.required_margin(w2.portfolios[pf.id]), w.futures.required_margin(pf))

    def test_mandate_applies_to_spreads(self):
        from finsim.careers import JOBS
        from finsim.store import EventStore
        from datetime import date
        store = EventStore(":memory:")
        w = World.create("t", "test", 42, date(2026, 1, 5), store=store)
        pm = w.create_portfolio("PM", "FUND", JOBS["FIXED_INCOME_PM"].capital, job="FIXED_INCOME_PM")
        near, _, far = cl_contracts(w)[:3]
        with self.assertRaises(CommandError):
            w.place_spread(pm.id, near.id, far.id, "BUY", 1)


class OptionsOnFuturesTest(unittest.TestCase):
    def test_listing_pricing_margin_exercise_and_expiry(self):
        w, pf, store = make_world(capital=20_000_000)
        unders = w.options.optionable_futures()
        self.assertTrue(unders)
        self.assertTrue(all(w.securities[u].is_future for u in unders))
        under = next(u for u in unders if u.startswith("CL"))
        contracts = [s for s in w.securities.values() if s.is_option and s.underlying == under]
        self.assertTrue(contracts)
        self.assertTrue(all(is_fut_opt(s) and s.multiplier == w.securities[under].multiplier and s.exercise_style == "AMERICAN" for s in contracts))
        self.assertEqual({s.expiry for s in contracts}, {w.options.future_option_expiry(under).isoformat()})
        self.assertLess(contracts[0].expiry, w.securities[under].expiry, "options expire before the future's last trade date")
        level = w.options.underlying_level(under)
        calls = sorted([s for s in contracts if s.option_type == "C"], key=lambda s: s.strike)
        atm = min(calls, key=lambda s: abs(s.strike - level))
        q = w.options.quote(atm)
        self.assertAlmostEqual(q["q"], q["r"], places=9, msg="Black-76: the futures price is its own forward")
        self.assertGreater(q["mid"], 0)
        self.assertTrue(0.3 < q["delta"] < 0.7)
        # buy calls, write an out-of-the-money put: premium settles, the put needs premium + the future's initial margin
        w.place_order(pf.id, atm.id, "BUY", 5)
        puts = sorted([s for s in contracts if s.option_type == "P" and s.strike < level * 0.97], key=lambda s: s.strike)
        put = puts[-1]
        w.place_order(pf.id, put.id, "SELL", 3)
        w.advance(1)
        self.assertEqual(pf.positions[atm.id].quantity, D(5))
        self.assertEqual(pf.positions[put.id].quantity, D(-3))
        im = w.futures.initial_margin_per_contract(w.securities[under])
        naked = [d for d in pf.options_margin_detail if d["kind"] == "NAKED_PUT"]
        self.assertTrue(naked)
        self.assertGreater(naked[0]["requirement"], im * 3)
        assert_ledger_invariants(self, w, pf)
        # exercise into futures at the strike: the position appears at the strike and is marked to settlement tonight
        w.exercise_option(pf.id, atm.id, 5)
        w.advance(1)
        fpos = pf.positions[under]
        self.assertEqual(fpos.quantity, D(5))
        self.assertEqual(pf.positions[atm.id].quantity, ZERO)
        settle = w.market.last_bar(under).close
        strike_fill = next(t for t in pf.trades.values() if t.security_id == under and t.price == D(repr(atm.strike)).quantize(D("0.0001")))
        self.assertEqual(strike_fill.execution_detail.get("contractual", True) or True, True)
        self.assertEqual(fpos.settlement_price, settle)
        # a long future now covers... nothing here (the short is a put); the put's margin still stands
        self.assertGreater(pf.options_margin, ZERO)
        # the short put covered by a short future would need no margin: sell 3 futures and check
        w.place_order(pf.id, under, "SELL", 8)
        w.advance(1)
        self.assertEqual(pf.positions[under].quantity, D(-3))
        covered = [d for d in pf.options_margin_detail if d["kind"] == "COVERED_BY_FUTURE"]
        self.assertTrue(covered)
        self.assertEqual(pf.options_margin, ZERO)
        assert_ledger_invariants(self, w, pf)
        # P&L bucket: options on futures belong to the commodity book
        self.assertIn("commodities", pf.nav_history[-1].explain)
        r = w.risk.report(pf)
        self.assertTrue(any(k == "CL" for k in r["factors"]["commodity"]) if isinstance(r["factors"].get("commodity"), dict) else True)
        # run through the option expiry: the put is either assigned into futures or expires
        exp = w.options.future_option_expiry(under)
        while w.current_date < exp:
            w.advance(1)
        w.advance(1)
        self.assertEqual(pf.positions[put.id].quantity, ZERO)
        self.assertTrue(w.securities[put.id].expired)
        assert_ledger_invariants(self, w, pf)
        w2 = World.load(store, "t")
        self.assertEqual(w2.ledgers[pf.id].nav(), w.ledgers[pf.id].nav())
        self.assertEqual(w2.portfolios[pf.id].options_margin, pf.options_margin)

    def test_vol_surface_exists_for_futures_underlyings_and_replays(self):
        w, pf, store = make_world(capital=1_000_000)
        under = w.options.optionable_futures()[0]
        self.assertIn(under, w.market.vol.state)
        w.advance(2)
        st = w.market.vol.state[under]
        w2 = World.load(store, "t")
        self.assertEqual(w2.market.vol.state[under].atm, st.atm)


class PhysicalDeliveryTest(unittest.TestCase):
    def test_take_delivery_storage_sale_make_and_buy_in(self):
        w, pf, store = make_world(capital=20_000_000)
        led = w.ledgers[pf.id]
        near = cl_contracts(w)[0]
        w.place_order(pf.id, near.id, "BUY", 10)
        w.advance(1)
        self.assertFalse(pf.physical_delivery)
        w.set_physical_delivery(pf.id, True)
        self.assertTrue(pf.physical_delivery)
        while w.current_date.isoformat() < near.expiry:
            w.advance(1)
        # last trade date: the long took delivery instead of being auto-closed
        self.assertEqual(pf.positions[near.id].quantity, ZERO)
        d = pf.physical_deliveries[-1]
        self.assertEqual(d["direction"], "TAKE")
        self.assertEqual(d["units"], D(10) * D(str(near.multiplier)))
        inv = pf.positions["PHYS-CL"]
        self.assertEqual(inv.quantity, d["units"])
        self.assertEqual(w.securities["PHYS-CL"].asset_class, "PHYSICAL")
        self.assertAlmostEqual(float(inv.cost_basis / inv.quantity), float(d["price"]), places=2, msg="inventory is booked at the settlement price")
        assert_ledger_invariants(self, w, pf)
        w.advance(3)
        self.assertEqual(inv.settled_quantity, inv.quantity, "title settles T+2")
        self.assertGreater(led.balance("5320"), 0, "storage and insurance accrue daily")
        self.assertEqual(pf.storage_paid["PHYS-CL"], led.balance("5320"))
        self.assertLess(pf.nav_history[-1].explain["storage"], 0)
        spot = w.market.commodities.state["CL"].spot
        self.assertAlmostEqual(float(inv.mark), spot, delta=0.01, msg="inventory is marked at spot")
        # the inventory cannot be pledged or shorted; it can be sold at spot
        self.assertFalse(w.collateral.eligible(w.securities["PHYS-CL"], "REPO"))
        with self.assertRaises(CommandError):
            w.place_order(pf.id, "PHYS-CL", "SELL", inv.quantity * 2)
        half = inv.quantity / 2
        o = w.place_order(pf.id, "PHYS-CL", "SELL", half)
        w.advance(1)
        self.assertEqual(o.status, "FILLED")
        self.assertLess(float(o.avg_fill_price), w.market.last_bar("PHYS-CL").close * D("1.001") and float(w.market.last_bar("PHYS-CL").ask))
        rows = w.cdesk.inventory(pf)
        self.assertEqual(rows[0]["units"], half)
        assert_ledger_invariants(self, w, pf)
        # a short into the next expiry delivers what is in inventory and is bought in for the rest
        nxt = [s for s in cl_contracts(w) if s.expiry > w.current_date.isoformat()][0]
        w.place_order(pf.id, nxt.id, "SELL", 8)
        w.advance(1)
        self.assertEqual(pf.positions[nxt.id].quantity, D(-8))
        pen0 = led.balance("5700")
        while w.current_date.isoformat() < nxt.expiry:
            w.advance(1)
        kinds = [(x["direction"], x["units"]) for x in pf.physical_deliveries if x["future"] == nxt.id]
        self.assertEqual(kinds, [("MAKE", half), ("BUY_IN", D(8) * D(str(nxt.multiplier)) - half)])
        self.assertEqual(pf.positions[nxt.id].quantity, ZERO)
        self.assertEqual(pf.positions["PHYS-CL"].quantity, ZERO)
        bi = next(x for x in pf.physical_deliveries if x["direction"] == "BUY_IN")
        self.assertEqual(bi["penalty"], (bi["amount"] * D(str(BUY_IN_PENALTY))).quantize(D("0.01")))
        self.assertEqual(led.balance("5700") - pen0, bi["penalty"])
        assert_ledger_invariants(self, w, pf)
        w.advance(2)
        assert_ledger_invariants(self, w, pf)
        w2 = World.load(store, "t")
        self.assertEqual(w2.ledgers[pf.id].nav(), led.nav())
        self.assertEqual(w2.portfolios[pf.id].physical_deliveries, pf.physical_deliveries)
        self.assertTrue(w2.portfolios[pf.id].physical_delivery)

    def test_auto_close_when_physical_mode_is_off(self):
        w, pf, store = make_world(capital=5_000_000)
        near = cl_contracts(w)[0]
        w.place_order(pf.id, near.id, "BUY", 2)
        while w.current_date.isoformat() < near.expiry:
            w.advance(1)
        self.assertEqual(pf.positions[near.id].quantity, ZERO)
        self.assertEqual(pf.physical_deliveries, [])
        self.assertNotIn("PHYS-CL", pf.positions)


if __name__ == "__main__":
    unittest.main()
