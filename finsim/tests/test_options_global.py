"""Options where the real market lists them: every US stock, big funds, foreign names in local currency with the home
exchange's contract size, and options on foreign futures in the future's currency."""
import unittest
from decimal import Decimal

from finsim.api.service import Service
from finsim.store import EventStore


class GlobalOptions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.svc = Service(EventStore(":memory:"))
        r = cls.svc.create_world(name="opt", seed=5, start_date="2025-03-03", capital=50_000_000, market_source="SIMULATED")
        cls.wid, cls.pid = r["world_id"], r["portfolio_id"]
        cls.w = cls.svc.world(cls.wid)

    def chain(self, under):
        return [s for s in self.w.securities.values() if s.is_option and s.underlying == under]

    def test_us_mid_caps_have_options(self):
        mids = [s for s in self.w.securities.values() if s.asset_class == "EQUITY" and s.currency == "USD" and s.liquidity_tier == "MID"]
        self.assertTrue(mids)
        self.assertTrue(all(self.chain(s.id) for s in mids[:10]))

    def test_foreign_chain_local_currency_and_size(self):
        for sid, ccy, size in (("SAP-DE", "EUR", 100), ("AZN-L", "GBP", 1000), ("7203-T", "JPY", 100), ("005930-KS", "KRW", 10), ("2330-TW", "TWD", 2000)):
            ch = self.chain(sid)
            self.assertTrue(ch, sid)
            self.assertEqual({c.currency for c in ch}, {ccy})
            self.assertEqual({int(c.multiplier) for c in ch}, {size})
            self.assertEqual(ch[0].deliverable["quantity"], size)

    def test_saudi_has_none(self):
        self.assertEqual(self.chain("2222-SR"), [])

    def test_high_priced_strikes_are_not_too_fine(self):
        strikes = sorted({c.strike for c in self.chain("005930-KS")})
        self.assertGreaterEqual(strikes[1] - strikes[0], 1000)

    def test_futures_options_take_the_future_currency(self):
        for s in self.w.securities.values():
            if s.is_option and (s.deliverable or {}).get("future"):
                self.assertEqual(s.currency, self.w.securities[s.underlying].currency, s.id)

    def test_short_foreign_put_margin_in_dollars(self):
        lvl = self.w.options.underlying_level("AZN-L")
        put = sorted([s for s in self.chain("AZN-L") if s.option_type == "P"], key=lambda s: (s.expiry, abs(s.strike - lvl)))[0]
        self.svc.place_order(self.wid, self.pid, put.id, "SELL", 5)
        self.svc.advance(self.wid, 1)                      # the instruction fills at the next update
        pf = self.w.portfolios[self.pid]
        rows = [d for d in pf.options_margin_detail if d["contract"] == put.id]
        self.assertTrue(rows)
        self.assertEqual(rows[0]["currency"], "GBP")
        local = sum((Decimal(str(d["requirement"])) for d in pf.options_margin_detail), Decimal(0))
        k = self.w.fx.k("GBP")
        self.assertAlmostEqual(float(pf.options_margin), float(local * k), delta=1.0)
        self.assertTrue(self.w.check_integrity()["ok"])


if __name__ == "__main__":
    unittest.main()
