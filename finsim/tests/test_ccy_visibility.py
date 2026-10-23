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


class ForeignCapitalAtItsDollarValue(unittest.TestCase):
    """Euros put into the Treasury (or drawn by a book) are booked at their dollar value, so NAV rises by the dollar
    value at once and no currency gain appears the next day."""

    def test_euro_capital_booked_at_spot(self):
        svc = Service(EventStore(":memory:"))
        r = svc.create_world(name="eur", seed=3, start_date="2025-03-03", capital=10_000_000, treasury=True, market_source="SIMULATED")
        wid = r["world_id"]
        w = svc.world(wid)
        tb = w.treasury_book()
        k = float(w.fx.k("EUR"))
        nav0 = float(w.pnl.compute_summary(tb)["nav"])
        svc.treasury_command(wid, "fund", {"currency": "EUR", "amount": 2_000_000})
        s = w.pnl.compute_summary(tb)
        self.assertAlmostEqual(float(s["nav"]) - nav0, 2_000_000 * k, delta=1.0)
        self.assertAlmostEqual(float(s["nav"]), float(s["ledger_nav"]), delta=0.01)
        self.assertAlmostEqual(float(tb.contributed_capital), 10_000_000 + 2_000_000 * k, delta=1.0)
        p = svc.create_portfolio(wid, "Book", "PERSONAL", 0, "PROFESSIONAL", "MANUAL", None, from_treasury=True)
        svc.treasury_command(wid, "allocate", {"currency": "EUR", "amount": 500_000, "portfolio_id": p["portfolio_id"]})
        book = w.portfolios[p["portfolio_id"]]
        self.assertAlmostEqual(float(book.cash_account("EUR").base_value), 500_000 * k, delta=1.0)
        self.assertTrue(w.check_integrity()["ok"])
