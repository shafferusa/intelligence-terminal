"""Fixes of 2026-09-24: a purchase paid in another currency counts on the conversion dealt with it and settles; London
history stored in pence by older saves is read in pounds; an option contract can be listed again from its id; a
structured tranche that defaults writes down without a single-name CDS event."""
import unittest

from finsim.api.service import Service
from finsim.store import EventStore


class TicketFixes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.svc = Service(EventStore(":memory:"))
        r = cls.svc.create_world(name="fix", seed=5, start_date="2025-06-02", capital=50_000_000, job="PORTFOLIO_MANAGER", market_source="SIMULATED")
        cls.wid, cls.pid = r["world_id"], r["portfolio_id"]
        cls.w = cls.svc.world(cls.wid)
        cls.pf = cls.w.portfolios[cls.pid]

    def test_pending_fx_counts_in_projected_cash(self):
        before = self.w.trading.projected_cash(self.pf, "CHF")
        t = self.w.fx_spot(self.pid, "CHF", "USD", 1_000_000, "BUY")
        self.assertEqual(t.status, "PENDING")
        self.assertEqual(self.w.trading.projected_cash(self.pf, "CHF") - before, t.buy_amount)

    def test_buy_paid_in_dollars_settles(self):
        o = self.svc.place_order(self.wid, self.pid, "SAP-DE", "BUY", 500, settle_ccy="USD")
        self.assertEqual(o["order"]["status"], "WORKING")
        self.assertIsNotNone(o["fx"])
        self.svc.advance(self.wid, 4)
        trades = [t for t in self.pf.trades.values() if t.security_id == "SAP-DE"]
        self.assertTrue(trades)
        self.assertEqual(trades[0].status, "SETTLED")
        self.assertGreater(float(self.pf.cash_account("EUR").balance), -5_000)     # at most a small overdraft from the fill price
        self.assertTrue(self.w.check_integrity()["ok"])

    def test_pence_history_is_read_in_pounds(self):
        gen = float(self.w.market.history["AZN-L"][-1].close)
        pence = {"equities": {"AZN-L": {"2025-05-30": gen * 100, "2025-06-02": gen * 101}, "SPY": {"2025-06-02": 500.0}}}
        fixed = self.w._fix_quote_scale(pence)
        self.assertAlmostEqual(fixed["equities"]["AZN-L"]["2025-05-30"], gen, places=6)
        self.assertEqual(fixed["equities"]["SPY"], pence["equities"]["SPY"])
        pounds = {"equities": {"AZN-L": {"2025-05-30": gen}}}
        self.assertIs(self.w._fix_quote_scale(pounds), pounds)

    def test_contract_listed_from_its_id(self):
        cid = "AZN-L261016P13500"
        self.w.securities.pop(cid, None)
        sec = self.w.options.list_contract(cid)
        self.assertIsNotNone(sec)
        self.assertEqual((sec.underlying, sec.option_type, sec.strike, sec.expiry, sec.currency, int(sec.multiplier)), ("AZN-L", "P", 13500.0, "2026-10-16", "GBP", 1000))
        self.assertIsNone(self.w.options.list_contract("NOPE991231C5"))
        self.assertIsNone(self.w.options.list_contract("garbage"))

    def test_structured_default_writes_down(self):
        clo = self.w.securities["CLO-2026-1E"]
        self.w.issuer_default(clo.id, 0.2, None)
        self.w.flush()
        self.assertTrue(self.w.securities[clo.id].defaulted)


if __name__ == "__main__":
    unittest.main()
