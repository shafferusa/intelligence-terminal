"""Phase 6 — operations depth: corporate events (stock/special dividends, tender offers, rights issues, cash mergers,
spin-offs, bond calls), settlement fail charges and buy-ins, custody view."""
import unittest
from datetime import date

from helpers import make_world, assert_ledger_invariants
from finsim.money import D
from finsim.world import CommandError, World


def announce(w, sid, kind, terms, days_ahead=3):
    """Sandbox control: announce a corporate event with a chosen effective date."""
    eff = w.calendar.add_business_days(w.current_date, days_ahead)
    return w.force_corporate_event(sid, kind, terms, eff.isoformat())


class CorporateEventsTest(unittest.TestCase):
    def test_stock_and_special_dividends(self):
        w, pf, store = make_world(capital=20_000_000)
        w.place_order(pf.id, "PG", "BUY", 10_000)
        w.advance(2)
        ev = announce(w, "PG", "STOCK_DIVIDEND", {"ratio": 0.10}, 2)
        ca = w.corporate_actions[ev.payload["id"]]
        self.assertEqual(ca.action_type, "STOCK_DIVIDEND")
        nav0 = w.ledgers[pf.id].nav()
        w.advance(2)
        self.assertEqual(pf.positions["PG"].quantity, D(11_000), "10% stock dividend adds shares")
        self.assertEqual(ca.status, "PAID")
        assert_ledger_invariants(self, w, pf)
        ev2 = announce(w, "PG", "SPECIAL_DIVIDEND", {"amount": 2.50}, 2)
        w.advance(2)
        div = [c for c in w.corporate_actions.values() if c.security_id == "PG" and c.action_type == "CASH_DIVIDEND" and c.id.endswith("SPECIAL")]
        self.assertEqual(len(div), 1)
        self.assertEqual(div[0].entitlements[pf.id]["amount"], D("27500.00"))
        self.assertEqual(w.ledgers[pf.id].security_balance("PG", "1210"), D("27500.00"), "special dividend receivable")
        w.advance(10)
        self.assertTrue(div[0].entitlements[pf.id]["paid"])
        assert_ledger_invariants(self, w, pf)

    def test_tender_offer_election_and_settlement(self):
        w, pf, store = make_world(capital=20_000_000)
        w.place_order(pf.id, "WMT", "BUY", 10_000)
        w.advance(2)
        ev = announce(w, "WMT", "TENDER_OFFER", {"offer_price": 250.0, "max_pct": 0.25, "deadline": w.calendar.add_business_days(w.current_date, 2).isoformat(), "bidder": "KKR"}, 4)
        ca_id = ev.payload["id"]
        with self.assertRaises(CommandError):
            w.elect(pf.id, ca_id, 5_000)                      # above the 25% cap
        r = w.elect(pf.id, ca_id, 2_000)
        self.assertEqual(r["quantity"], D(2_000))
        att = " ".join(a["text"] for a in pf.briefings[-1]["attention"])
        w.advance(1)
        self.assertIn("TENDER", " ".join(a["text"] for a in pf.briefings[-1]["attention"]))
        w.advance(3)
        self.assertEqual(pf.positions["WMT"].quantity, D(8_000))
        t = [pf.trades[t] for t in pf.positions["WMT"].trade_ids if pf.trades[t].side == "SELL"][0]
        self.assertEqual(t.price, D("250.0000"))
        self.assertEqual(t.commission, D(0))
        self.assertTrue(t.execution_detail["contractual"])
        assert_ledger_invariants(self, w, pf)
        with self.assertRaises(CommandError):
            w.elect(pf.id, ca_id, 100)                        # closed

    def test_rights_issue_subscription(self):
        w, pf, store = make_world(capital=20_000_000)
        w.place_order(pf.id, "NEE", "BUY", 20_000)
        w.advance(2)
        px = float(w.market.last_bar("NEE").close)
        ev = announce(w, "NEE", "RIGHTS_ISSUE", {"subscription_price": round(px * 0.8, 2), "ratio": 0.25, "deadline": w.calendar.add_business_days(w.current_date, 1).isoformat()}, 3)
        with self.assertRaises(CommandError):
            w.elect(pf.id, ev.payload["id"], 6_000)          # 1 per 4 held = 5,000 max
        w.elect(pf.id, ev.payload["id"], 5_000)
        cash0 = pf.cash_account("USD").balance
        w.advance(3)
        self.assertEqual(pf.positions["NEE"].quantity, D(25_000))
        t = [pf.trades[t] for t in pf.positions["NEE"].trade_ids if pf.trades[t].execution_detail.get("contractual")][0]
        self.assertEqual(t.price, D(repr(round(px * 0.8, 2))).quantize(D("0.0001")))
        w.advance(2)
        self.assertLess(pf.cash_account("USD").balance, cash0, "subscription paid at settlement")
        assert_ledger_invariants(self, w, pf)

    def test_cash_merger_cashes_out_holders_settles_options_and_delists(self):
        w, pf, store = make_world(capital=30_000_000)
        ch = w.options.chain("CAT", w.options.chain("CAT")["expiries"][2])
        atm = min(ch["rows"], key=lambda r: abs(r["strike"] - ch["level"]))
        w.place_order(pf.id, "CAT", "BUY", 5_000)
        w.place_order(pf.id, atm["C"]["id"], "BUY", 10)
        w.advance(2)
        px = float(w.market.last_bar("CAT").close)
        deal = round(px * 1.3, 2)
        ev = announce(w, "CAT", "CASH_MERGER", {"deal_price": deal, "acquirer": "KKR"}, 3)
        w.advance(1)
        self.assertGreater(float(w.market.last_bar("CAT").close), px * 1.1, "the stock jumps toward the deal price on the announcement")
        w.advance(2)
        self.assertEqual(pf.positions["CAT"].quantity, D(0))
        t = [pf.trades[t] for t in pf.positions["CAT"].trade_ids if pf.trades[t].side == "SELL"][0]
        self.assertEqual(t.price, D(repr(deal)).quantize(D("0.0001")))
        self.assertEqual(pf.positions[atm["C"]["id"]].quantity, D(0))
        intrinsic = max(0.0, deal - atm["strike"])
        close = [pf.trades[t] for t in pf.positions[atm["C"]["id"]].trade_ids if pf.trades[t].side == "SELL" and pf.trades[t].execution_detail.get("contractual")][0]
        self.assertAlmostEqual(float(close.gross_amount), intrinsic * 1000, delta=1.0, msg="calls settle at intrinsic against the deal price")
        self.assertTrue(w.securities["CAT"].delisted)
        with self.assertRaises(CommandError):
            w.place_order(pf.id, "CAT", "BUY", 100)
        w.advance(2)
        self.assertEqual(pf.positions["CAT"].settled_quantity, D(0))
        assert_ledger_invariants(self, w, pf)
        w2 = World.load(store, "t")
        self.assertTrue(w2.securities["CAT"].delisted)
        self.assertEqual(w2.ledgers[pf.id].nav(), w.ledgers[pf.id].nav())

    def test_spin_off_creates_a_new_security(self):
        w, pf, store = make_world(capital=30_000_000)
        w.place_order(pf.id, "BA", "BUY", 4_000)
        w.advance(2)
        cost0 = pf.positions["BA"].cost_basis
        ev = announce(w, "BA", "SPIN_OFF", {"fraction": 0.2, "ratio": 0.5, "new_id": "AERS", "new_name": "Aerion Spinco", "sector": "Industrials"}, 3)
        nav_before = w.ledgers[pf.id].nav()
        w.advance(3)
        self.assertIn("AERS", w.securities)
        self.assertEqual(pf.positions["AERS"].quantity, D(2_000))
        self.assertEqual(pf.positions["AERS"].settled_quantity, D(2_000))
        self.assertEqual(pf.positions["AERS"].cost_basis + pf.positions["BA"].cost_basis, cost0, "cost basis is carved out, not created")
        assert_ledger_invariants(self, w, pf)
        w.advance(1)
        self.assertGreaterEqual(len(w.market.history["AERS"]), 2, "the spun-off company trades")
        self.assertGreater(pf.positions["AERS"].market_value, D(0))
        assert_ledger_invariants(self, w, pf)
        w.place_order(pf.id, "AERS", "SELL", 1_000)
        w.advance(2)
        self.assertEqual(pf.positions["AERS"].quantity, D(1_000))
        assert_ledger_invariants(self, w, pf)
        w2 = World.load(store, "t")
        self.assertIn("AERS", w2.securities)
        self.assertEqual(w2.portfolios[pf.id].positions["AERS"].quantity, D(1_000))
        self.assertEqual([b.close for b in w2.market.history["AERS"]], [b.close for b in w.market.history["AERS"]])

    def test_bond_call_redeems_at_the_call_price(self):
        w, pf, store = make_world(capital=30_000_000)
        w.place_order(pf.id, "AAL-28", "BUY", 250_000)
        w.advance(2)
        held = pf.positions["AAL-28"].quantity
        self.assertEqual(held, D(250_000))
        ev = announce(w, "AAL-28", "BOND_CALL", {"call_price": 102.0}, 3)
        w.advance(3)
        pos = pf.positions["AAL-28"]
        self.assertEqual(pos.quantity, D(0))
        red = [m for m in pf.cash_movements if m.kind == "MATURITY"]
        self.assertEqual(red[-1].amount, D("1020000.00"), "redeemed at the call price")
        self.assertTrue(w.securities["AAL-28"].delisted)
        assert_ledger_invariants(self, w, pf)


class FailChargesTest(unittest.TestCase):
    def test_failed_delivery_is_charged_and_bought_in(self):
        w, pf, store = make_world(capital=20_000_000)
        # a short delivery with nothing borrowed: assignment on a deep ITM short put? simpler: a system sell without shares
        ch = w.options.chain("JPM", w.options.chain("JPM")["expiries"][0])
        deep_call = ch["rows"][0]["C"]["id"]
        w.place_order(pf.id, deep_call, "SELL", 2)          # naked deep ITM call: assignment delivers shares we do not have
        w.advance(1)
        days = 0
        while not any(si.status == "FAILED" for si in pf.settlements.values() if si.instruction_type == "DVP" and si.security_id == "JPM") and days < 30:
            w.advance(1)
            days += 1
        fails = [si for si in pf.settlements.values() if si.instruction_type == "DVP" and si.security_id == "JPM" and si.status == "FAILED"]
        self.assertTrue(fails, "assignment without borrowed shares fails to deliver")
        w.advance(1)
        charges = [m for m in pf.cash_movements if m.kind == "FAIL_CHARGE"]
        self.assertTrue(charges)
        self.assertEqual(w.ledgers[pf.id].balance("5800"), pf.fail_charges)
        w.advance(6)
        self.assertTrue(any(e.type == "BUY_IN" for e in w.events), "after five failed days the receiving party buys in")
        self.assertTrue(any("buy-in penalty" in m.reference for m in pf.cash_movements))
        assert_ledger_invariants(self, w, pf)
        w2 = World.load(store, "t")
        self.assertEqual(w2.ledgers[pf.id].nav(), w.ledgers[pf.id].nav())


if __name__ == "__main__":
    unittest.main()
