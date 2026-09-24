"""The Treasury and every book show each currency they hold, valued in dollars, and capital moves in any currency."""
import unittest

from finsim.api.service import Service
from finsim.store import EventStore


class CurrencyVisibility(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.svc = Service(EventStore(":memory:"))
        r = cls.svc.create_world(name="ccy", seed=3, start_date="2025-03-03", capital=10_000_000, treasury=True, market_source="SIMULATED")
        cls.wid = r["world_id"]
        cls.svc.treasury_command(cls.wid, "fund", {"currency": "EUR", "amount": 2_000_000})
        p = cls.svc.create_portfolio(cls.wid, "Book", "PERSONAL", 1_000_000, "PROFESSIONAL", "MANUAL", None, from_treasury=True)
        cls.pid = p["portfolio_id"]
        cls.svc.treasury_command(cls.wid, "allocate", {"currency": "EUR", "amount": 500_000, "portfolio_id": cls.pid})

    def test_treasury_lists_each_currency(self):
        t = self.svc.treasury(self.wid)
        by = {c["currency"]: c for c in t["currencies"]}
        self.assertEqual(float(by["USD"]["settled"]), 9_000_000)
        self.assertIn(float(by["EUR"]["settled"]), (1_500_000, 1_600_000))
        self.assertGreater(float(by["EUR"]["base_value"]), 1_500_000 * 0.5)
        self.assertAlmostEqual(float(t["total_base"]), sum(float(c["base_value"]) for c in t["currencies"]), places=2)
        self.assertIn("EUR", t["held"])

    def test_book_cash_accounts(self):
        d = self.svc.dashboard(self.wid, self.pid)
        by = {c["currency"]: c for c in d["cash_accounts"]}
        self.assertEqual(float(by["EUR"]["settled"]), 500_000)
        self.assertTrue(by["USD"]["is_base"])
        self.assertEqual(d["cash_accounts"][0]["currency"], "USD")

    def test_return_in_euros(self):
        self.svc.treasury_command(self.wid, "return", {"currency": "EUR", "amount": 100_000, "portfolio_id": self.pid})
        by = {c["currency"]: c for c in self.svc.treasury(self.wid)["currencies"]}
        self.assertEqual(float(by["EUR"]["settled"]), 1_600_000)


if __name__ == "__main__":
    unittest.main()
