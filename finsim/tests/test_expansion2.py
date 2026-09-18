"""The bigger market (2026-09): financial futures on indices, rates, crypto, the dollar index and the VIX; the Treasury ladder;
crypto spot with fractional coins; 18 currencies; the extra OTC products; #tags; starting capital."""
import math
import unittest
from datetime import date

try:
    from helpers import make_world, assert_ledger_invariants
except ImportError:
    from tests.helpers import make_world, assert_ledger_invariants

from finsim.api.service import Service
from finsim.engines.commodities import FINANCIAL_SOURCES
from finsim.engines.fx_market import dollar_index, CURRENCIES
from finsim.engines.otc_extra import barrier_in, barrier_price, digital_price, asian_price, EXTRA_PRODUCTS
from finsim.engines.options_pricing import bsm
from finsim.money import D
from finsim.store import EventStore
from finsim.world import World, CommandError


class MarketExpansionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w, cls.pf, cls.store = make_world(capital=500_000_000)

    def test_financial_futures_ride_their_sources(self):
        w = self.w
        codes = {s.underlying for s in w.securities.values() if s.is_future}
        for code in ("ES", "NQ", "RTY", "YM", "ZT", "ZF", "ZN", "ZB", "SR3", "BTC", "ETH", "DX", "VX"):
            self.assertIn(code, codes, code)
        nq = w.market.front_contract("NQ")
        self.assertAlmostEqual(float(w.market.last_bar(nq.id).close) / float(w.market.last_bar("QQQ").close), 1.0, delta=0.05, msg="NQ prices off QQQ with carry")
        vx = [s for s in w.securities.values() if s.is_future and s.underlying == "VX" and not s.expired]
        vx.sort(key=lambda s: s.expiry)
        vix = float(w.market.last_bar("VIX").close)
        front, back = float(w.market.last_bar(vx[0].id).close), float(w.market.last_bar(vx[-1].id).close)
        self.assertTrue((front <= back) if vix < 19.5 else (front >= back), "VX term structure pulls toward the long run")
        sr3 = w.market.front_contract("SR3")
        self.assertTrue(90 < float(w.market.last_bar(sr3.id).close) < 100, "SOFR futures quote as 100 minus a rate")
        self.assertGreater(float(w.market.last_bar("DXY").close), 80)
        self.assertEqual(FINANCIAL_SOURCES["VX"], ("VIX", "vol"))

    def test_indices_are_not_tradable_and_dxy_formula(self):
        with self.assertRaises(CommandError):
            self.w.place_order(self.pf.id, "DXY", "BUY", D(1))
        self.assertAlmostEqual(dollar_index({"EUR": 1.0, "JPY": 1 / 100.0, "GBP": 1.0, "CAD": 1.0, "SEK": 1.0, "CHF": 1.0}), 50.14348112 * 100 ** 0.136, places=4)
        self.assertEqual(dollar_index({"EUR": 1.0}), 0.0)

    def test_treasury_ladder_and_crypto_trading(self):
        w, pf = self.w, self.pf
        for sid in ("UST-3M", "UST-6M", "UST-1Y", "UST-3Y", "UST-7Y", "UST-20Y", "STRIP-10Y", "STRIP-30Y", "MUNI-CA-35", "AGY-FHLB-29", "SOV-US-MX-34"):
            self.assertIn(sid, w.securities, sid)
        self.assertLess(float(w.market.last_bar("STRIP-30Y").close), 40)
        self.assertLess(float(w.market.last_bar("UST-3M").close), 100)
        self.assertEqual(w.securities["BTC"].qty_step, D("0.0001"))
        w.place_order(pf.id, "BTC", "BUY", D("0.25"), strategy_tag="coins")
        w.place_order(pf.id, "USDC", "BUY", D(10_000))
        w.place_order(pf.id, w.market.front_contract("ETH").id, "BUY", D(1))
        w.place_order(pf.id, "STRIP-10Y", "BUY", D(100_000))
        w.place_order(pf.id, "MUNI-CA-35", "BUY", D(50_000))
        opt = next(s for s in w.securities.values() if s.is_option and s.underlying == "BTC" and not s.expired and s.option_type == "P")
        self.assertEqual(opt.multiplier, 1)
        w.place_order(pf.id, opt.id, "BUY", D(2))
        w.advance(3)
        self.assertEqual(pf.positions["BTC"].quantity, D("0.25"))
        self.assertEqual(pf.positions["USDC"].quantity, D(10_000))
        self.assertEqual(pf.positions[opt.id].quantity, D(2))
        assert_ledger_invariants(self, w, pf)
        self.assertLess(abs(float(w.market.last_bar("USDT").close) - 1.0), 0.02, "stablecoins hold the peg")
        snap = pf.nav_history[-1]
        self.assertIn("crypto", snap.by_bucket if hasattr(snap, "by_bucket") else {"crypto": 0})
        self.assertEqual(len(CURRENCIES), 19)
        self.assertIn("MXN", w.market.fx.spot)
        w2 = World.load(self.store, "t")
        self.assertEqual(w2.replay_errors, [])
        self.assertEqual(w2.portfolio(pf.id).positions["BTC"].quantity, D("0.25"))
        self.assertEqual(w2.market.last_bar("DXY").close, w.market.last_bar("DXY").close)


class ExoticPricingTest(unittest.TestCase):
    def test_barrier_parity_digital_and_asian_bounds(self):
        S, K, T, r, q, sig = 100.0, 100.0, 0.5, 0.04, 0.01, 0.25
        van = bsm("C", S, K, T, r, q, sig)
        for H in (80.0, 120.0):
            ki = barrier_in(True, S, K, H, T, r, q, sig)
            ko = barrier_price(True, False, S, K, H, T, r, q, sig)
            self.assertAlmostEqual(ki + ko, van, places=6, msg="knock-in plus knock-out is the vanilla")
            self.assertTrue(0 <= ko <= van)
        self.assertAlmostEqual(barrier_price(True, False, S, K, 1e6, T, r, q, sig), van, places=3, msg="a far-away barrier is no barrier")
        dig = digital_price(True, S, K, T, r, q, sig, 1.0)
        self.assertTrue(0.4 < dig < 0.6)
        self.assertAlmostEqual(digital_price(True, S, K, T, r, q, sig, 1.0) + digital_price(False, S, K, T, r, q, sig, 1.0), math.exp(-r * T), places=8)
        asian = asian_price(True, S, K, T, T, r, sig, 0.0, 0, 126)
        self.assertLess(asian, van, "averaging lowers the volatility that matters")
        self.assertGreater(asian, 0)
        self.assertAlmostEqual(asian_price(True, S, K, T, 0.0, r, sig, 110.0, 126, 126), 10.0, places=6, msg="at expiry the payoff is on the average")


class OTCExtraTest(unittest.TestCase):
    def test_every_new_product_deals_marks_and_replays(self):
        w, pf, store = make_world(capital=300_000_000)
        asks = [
            ("OIS", {"notional": 10_000_000, "tenor_years": 2, "pay_fixed": True}),
            ("CDS", {"reference": "CDX_HY", "notional": 10_000_000, "tenor_years": 5, "buyer": True}),
            ("CDS", {"reference": "SOV-US-BR-34", "notional": 5_000_000, "tenor_years": 5, "buyer": False}),
            ("VARIANCE_SWAP", {"security_id": "SPX", "vega_notional": 100_000, "tenor_months": 3, "buyer": True}),
            ("VOL_SWAP", {"security_id": "NVDA", "vega_notional": 50_000, "tenor_months": 2, "buyer": False}),
            ("DIVIDEND_SWAP", {"security_id": "SPY", "units": 100_000, "tenor_months": 12, "buyer": True}),
            ("INFLATION_SWAP", {"notional": 20_000_000, "tenor_years": 5, "pay_fixed": True}),
            ("NDF", {"ccy": "MXN", "units": 50_000_000, "tenor_months": 3, "long": True}),
            ("FX_SWAP", {"ccy": "EUR", "amount": 5_000_000, "tenor_months": 3, "direction": "BUY_SELL"}),
            ("BARRIER_OPTION", {"security_id": "AAPL", "option_type": "C", "barrier_type": "KO", "barrier_pct": 115, "units": 10_000, "tenor_months": 3, "buyer": True}),
            ("BARRIER_OPTION", {"ccy": "JPY", "option_type": "P", "barrier_type": "KI", "barrier_pct": 92, "units": 100_000_000, "tenor_months": 3, "buyer": True}),
            ("DIGITAL_OPTION", {"security_id": "NDX", "option_type": "P", "payout": 100, "units": 1000, "tenor_months": 2, "buyer": True}),
            ("ASIAN_OPTION", {"code": "CL", "option_type": "C", "units": 100_000, "tenor_months": 3, "buyer": True}),
            ("CRYPTO_PERP", {"coin": "ETH", "units": 20, "long": False}),
            ("PPN", {"security_id": "SPX", "notional": 5_000_000, "tenor_years": 3, "participation": 1.2}),
            ("REVERSE_CONVERTIBLE", {"security_id": "NVDA", "notional": 2_000_000, "tenor_years": 1, "barrier_pct": 70}),
            ("AUTOCALLABLE", {"security_id": "RUT", "notional": 3_000_000, "tenor_years": 3}),
        ]
        ids = []
        for product, params in asks:
            r = w.request_quote(pf.id, product, {**params, "tag": "book-" + product.lower()})
            live = [q for q in r.quotes if not q.get("declined")]
            self.assertTrue(live, product)
            best = min(live, key=lambda q: q["cost_vs_mid"])
            t = w.execute_rfq(pf.id, r.id, best["dealer"])
            ids.append(t.id)
            self.assertEqual(t.terms.get("tag"), "book-" + product.lower())
            self.assertTrue(w.otc.describe(t))
        self.assertEqual(sum(1 for t in pf.otc_trades.values() if t.product in EXTRA_PRODUCTS), 13)
        with self.assertRaises(CommandError):
            w.request_quote(pf.id, "NDF", {"ccy": "EUR", "units": 1_000_000})            # deliverable currencies are forwards
        with self.assertRaises(CommandError):
            w.request_quote(pf.id, "CRYPTO_PERP", {"coin": "AAPL", "units": 1})
        assert_ledger_invariants(self, w, pf)
        w.advance(3)
        assert_ledger_invariants(self, w, pf)
        perp = next(t for t in pf.otc_trades.values() if t.product == "CRYPTO_PERP")
        self.assertTrue(any(c["kind"] == "PERP_FUNDING" for c in perp.cashflows), "a short perp receives funding daily")
        var = next(t for t in pf.otc_trades.values() if t.product == "VARIANCE_SWAP")
        self.assertEqual(var.state.get("n"), 3)
        asian = next(t for t in pf.otc_trades.values() if t.product == "ASIAN_OPTION")
        self.assertGreater(asian.state.get("n", 0), 0)
        w.otc.terminate(pf, perp.id)
        w.flush()
        assert_ledger_invariants(self, w, pf)
        w2 = World.load(store, "t")
        self.assertEqual(w2.replay_errors, [])
        p2 = w2.portfolio(pf.id)
        for tid in ids:
            self.assertEqual(p2.otc_trades[tid].mtm, pf.otc_trades[tid].mtm, tid)
            self.assertEqual(p2.otc_trades[tid].status, pf.otc_trades[tid].status, tid)
        self.assertAlmostEqual(w2.market.macro.cpi_index, w.market.macro.cpi_index, places=9)
        upcoming = w.otc.upcoming(pf, 400)
        self.assertTrue(any(u["kind"] == "AUTOCALL_OBSERVATION" for u in upcoming))


class TagsAndCapitalTest(unittest.TestCase):
    def test_tags_gather_every_desk_and_capital_is_free_up_to_a_trillion(self):
        s = Service(EventStore(":memory:"))
        r = s.create_world("t", 42, start_date="2026-01-05", capital=750_000_000_000, job="PORTFOLIO_MANAGER")
        w = s.worlds[r["world_id"]]; pf = w.portfolio(r["portfolio_id"])
        self.assertEqual(pf.contributed_capital, D(750_000_000_000))
        with self.assertRaises(CommandError):
            s.create_world("too big", 1, start_date="2026-01-05", capital=2_000_000_000_000)
        r2 = s.create_portfolio(w.id, "Second", "PERSONAL", None, "PROFESSIONAL", "SANDBOX", "SPY", "GLOBAL_MACRO")
        self.assertEqual(w.portfolio(r2["portfolio_id"]).contributed_capital, D(250_000_000), "blank means the job's standard")
        s.place_order(w.id, pf.id, "AAPL", "BUY", 100, strategy_tag="alpha")
        s.place_order(w.id, pf.id, "MSFT", "BUY", 50, strategy_tag="#alpha")
        s.fx_spot(w.id, pf.id, "EUR", "USD", 100_000, "BUY", None, "alpha")
        rq = s.otc_rfq(w.id, pf.id, {"product": "IRS", "params": {"notional": 5_000_000, "tenor_years": 3, "pay_fixed": True, "tag": "alpha"}})
        best = min((q for q in rq["quotes"] if not q.get("declined")), key=lambda q: q["cost_vs_mid"])
        s.otc_execute(w.id, pf.id, rq["id"], best["dealer"]) if hasattr(s, "otc_execute") else w.execute_rfq(pf.id, rq["id"], best["dealer"])
        w.advance(2)
        t = s.tags(w.id, pf.id)
        self.assertEqual([b["tag"] for b in t["tags"]], ["alpha"])
        b = t["tags"][0]
        self.assertEqual(len(b["fx"]), 1); self.assertEqual(len(b["otc"]), 1); self.assertGreaterEqual(len(b["fills"]), 1)
        self.assertTrue(any(n["security_id"] == "AAPL" for n in b["net"]))


if __name__ == "__main__":
    unittest.main()
