"""FinSim2 portfolio ledger: one validated replay (no overdraft, no phantom cash, holdings = NAV history), splits,
dividends, dollar-first FX pairs, time- and money-weighted returns."""
import math
import os
import random
import shutil
import tempfile
import unittest

from finsim2.data.store import Store
from finsim2.engine.align import Panel
from finsim2.engine.portfolio import Ledger, LedgerError, performance, xirr
from test_finsim2 import business_days


class LedgerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "r.db"))
        self.days = business_days("2020-01-01", 300)
        rnd = random.Random(5)
        p = 100.0
        rows = []
        for d in self.days:
            p *= math.exp(rnd.gauss(0.0004, 0.01))
            rows.append({"date": d, "close": p, "adj_close": p})
        self.store.upsert_prices("SPY", rows)
        self.store.upsert_prices("QQQ", [{"date": d, "close": 50.0 + i * 0.1, "adj_close": 50.0 + i * 0.1} for i, d in enumerate(self.days)])
        self.jpy = [150.0 if i < 150 else 160.0 for i in range(len(self.days))]
        self.store.upsert_prices("USDJPY", [{"date": d, "close": v, "adj_close": v} for d, v in zip(self.days, self.jpy)])
        self.panel = Panel(self.store)
        self.led = Ledger(self.store, self.panel)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def close(self, a, k):
        return self.store.prices(a, self.days[k], self.days[k])[0]["close"]


class Integrity(LedgerCase):
    def test_buy_beyond_cash_is_rejected(self):
        self.led.deposit(1000, self.days[10])
        with self.assertRaises(LedgerError):
            self.led.trade("SPY", 1000, "BUY", date=self.days[20])
        self.assertEqual(len(self.store.transactions("main")), 1)

    def test_backdated_sell_that_breaks_a_later_sell_is_rejected(self):
        self.led.deposit(1e6, self.days[0])
        self.led.trade("SPY", 10, "BUY", date=self.days[10])
        self.led.trade("SPY", 10, "SELL", date=self.days[200])
        with self.assertRaises(LedgerError) as cm:
            self.led.trade("SPY", 10, "SELL", date=self.days[100])
        self.assertIn("only 0", str(cm.exception))

    def test_backdated_buy_that_starves_a_later_buy_is_rejected(self):
        self.led.deposit(10_000, self.days[0])
        self.led.trade("QQQ", 100, "BUY", date=self.days[200])          # ≈ 7,000
        with self.assertRaises(LedgerError):
            self.led.trade("SPY", 50, "BUY", date=self.days[50])          # ≈ 5,000 earlier: the later buy would overdraw

    def test_void_is_validated_soft_and_audited(self):
        self.led.deposit(1e6, self.days[0])
        buy = self.led.trade("SPY", 10, "BUY", date=self.days[10])
        self.led.trade("SPY", 5, "SELL", date=self.days[50])
        with self.assertRaises(LedgerError):
            self.led.void(buy)                                            # the later sell depends on it
        extra = self.led.deposit(500, self.days[60])
        self.led.void(extra)
        self.assertEqual(len(self.store.transactions("main")), 3)
        self.assertEqual(len(self.store.transactions("main", include_voided=True)), 4)
        actions = [a["action"] for a in self.store.audit_log()]
        self.assertIn("transaction.void", actions)
        self.assertEqual(actions.count("transaction.add"), 4)

    def test_holdings_and_nav_history_agree_on_every_date(self):
        rnd = random.Random(9)
        self.led.deposit(100_000, self.days[0])
        for k in range(5, 290, 7):
            a = rnd.choice(["SPY", "QQQ"])
            held = self.led.holdings(self.days[k])["positions"].get(a, {}).get("quantity", 0.0)
            if held > 5 and rnd.random() < 0.4:
                self.led.trade(a, held * rnd.uniform(0.1, 0.9), "SELL", date=self.days[k])
            else:
                cash = self.led.holdings()["cash"]
                q = min(rnd.uniform(1, 20), 0.2 * cash / self.close(a, k))
                if q > 0.01:
                    self.led.trade(a, q, "BUY", date=self.days[k], fee=1.0)
        hist = self.led.nav_history()
        self.assertEqual(hist["issues"], [])
        for k in range(0, len(hist["dates"]), 23):
            d = hist["dates"][k]
            h = self.led.holdings(d)
            i = self.panel.index_of(d)
            v = h["cash"] + sum(p["quantity"] * self.close(a, i) for a, p in h["positions"].items())
            self.assertAlmostEqual(v, hist["nav"][k], places=6)
            self.assertGreaterEqual(h["cash"], -1e-6)

    def test_realized_pnl_partial_sells_and_reentry(self):
        self.led.deposit(1e6, self.days[0])
        self.led.trade("QQQ", 10, "BUY", price=50.0, date=self.days[0])
        self.led.trade("QQQ", 10, "BUY", price=60.0, date=self.days[1])    # average cost 55
        self.led.trade("QQQ", 5, "SELL", price=70.0, date=self.days[2], fee=2.0)
        h = self.led.holdings()
        self.assertAlmostEqual(h["positions"]["QQQ"]["realized"], 5 * (70 - 55) - 2.0)
        self.assertAlmostEqual(h["positions"]["QQQ"]["cost"], 15 * 55)
        self.led.trade("QQQ", 15, "SELL", price=40.0, date=self.days[3])
        self.led.trade("QQQ", 1, "BUY", price=45.0, date=self.days[4])      # re-entry starts a fresh cost basis
        h = self.led.holdings()
        self.assertAlmostEqual(h["positions"]["QQQ"]["cost"], 45.0)
        self.assertAlmostEqual(h["realized"], 5 * 15 - 2.0 + 15 * (40 - 55))
        self.assertAlmostEqual(h["cash"], 1e6 - 500 - 600 + 350 - 2 + 600 - 45)

    def test_withdrawal_checks_cash_at_that_point(self):
        self.led.deposit(1000, self.days[0])
        with self.assertRaises(LedgerError):
            self.led.withdraw(1500, self.days[5])


class ContinuousSeries(LedgerCase):
    def test_a_continuous_futures_series_cannot_be_opened_but_an_old_position_can_be_closed(self):
        self.store.upsert_prices("WTI", [{"date": d, "close": 70.0, "adj_close": 70.0} for d in self.days])
        self.led = Ledger(self.store, Panel(self.store))
        self.led.deposit(1e6, self.days[0])
        for side in ("BUY", "SHORT"):
            with self.assertRaises(ValueError) as cm:
                self.led.trade("WTI", 10, side, date=self.days[10])
            self.assertIn("continuous front-month", str(cm.exception))
            self.assertIn("USO", str(cm.exception))
        # a position booked before the rule (inserted directly) still replays and can be sold
        self.store.add_transactions([{"portfolio_id": "main", "date": self.days[10], "kind": "BUY", "asset_id": "WTI", "quantity": 10.0,
                                      "price": 70.0, "fee": 0.0, "currency": "USD", "note": "", "basis_date": self.days[10]}])
        self.assertAlmostEqual(self.led.holdings()["positions"]["WTI"]["quantity"], 10.0)
        self.led.trade("WTI", 10, "SELL", date=self.days[20])
        self.assertAlmostEqual(self.led.holdings()["positions"].get("WTI", {}).get("quantity", 0.0), 0.0)


class CashInterest(LedgerCase):
    def _with(self, **settings):
        from finsim2.engine import cash as cashmod
        self.store.upsert_macro("DGS3MO", [(d, 4.0) for d in self.days])
        cashmod.save(self.store, "main", settings)
        self.panel = Panel(self.store)
        self.led = Ledger(self.store, self.panel)

    def test_default_idle_cash_earns_nothing(self):
        self.led.deposit(1e6, self.days[0])
        self.assertAlmostEqual(self.led.holdings()["cash"], 1e6, places=6)

    def test_bill_rate_accrues_daily_into_cash_nav_and_returns(self):
        self._with(cash_mode="bill")
        self.led.deposit(1e6, self.days[0])
        h = self.led.holdings()
        n = len(self.days) - 2        # the bill is visible from the day after its date (FRED lag): one fewer accrual
        self.assertAlmostEqual(h["cash"], 1e6 * (1 + 0.04 / 252) ** n, delta=1e-3 * n)       # daily accrual, compounding in cash
        self.assertAlmostEqual(h["interest"], h["cash"] - 1e6, places=6)

    def test_short_proceeds_earn_only_under_their_setting(self):
        out = {}
        for mode in ("none", "institutional"):
            self._with(cash_mode="none", short_mode=mode)
            self.led.deposit(1e6, self.days[0])
            self.led.trade("SPY", 100, "SHORT", date=self.days[10])
            out[mode] = self.led.holdings()["interest"]
            self.tearDown(); self.setUp()
        self.assertEqual(out["none"], 0.0)
        self.assertGreater(out["institutional"], 0.0)

    def test_settings_are_validated(self):
        from finsim2.engine import cash as cashmod
        with self.assertRaises(ValueError):
            cashmod.save(self.store, "main", {"cash_mode": "moon"})
        with self.assertRaises(ValueError):
            cashmod.save(self.store, "main", {"short_mode": "partial", "short_share": 1.5})


class CorporateActions(LedgerCase):
    def test_split_after_a_trade_at_the_brokers_price_keeps_its_value(self):
        # closes are split-adjusted (today's basis); a 4:1 split happened on day 100
        self.store.upsert_actions("SPY", [(self.days[100], "SPLIT", 4.0)])
        self.led.deposit(1e6, self.days[0])
        raw_price = self.close("SPY", 50) * 4                                 # what the broker showed before the split
        self.led.trade("SPY", 10, "BUY", price=raw_price, date=self.days[50])
        h = self.led.holdings()
        self.assertAlmostEqual(h["positions"]["SPY"]["quantity"], 40.0)       # today's share basis
        hist = self.led.nav_history()
        k = hist["dates"].index(self.days[50])
        self.assertAlmostEqual(hist["nav"][k], 1e6, places=4)                # no false loss on the trade date

    def test_default_price_is_already_in_todays_basis(self):
        self.store.upsert_actions("SPY", [(self.days[100], "SPLIT", 4.0)])
        self.led.deposit(1e6, self.days[0])
        self.led.trade("SPY", 10, "BUY", date=self.days[50])                  # FinSim2's (adjusted) close: no factor
        self.assertAlmostEqual(self.led.holdings()["positions"]["SPY"]["quantity"], 10.0)

    def test_dividends_are_credited_to_holders_on_the_ex_date(self):
        self.store.upsert_actions("QQQ", [(self.days[30], "DIVIDEND", 0.5), (self.days[80], "DIVIDEND", 0.5)])
        self.led.deposit(1e6, self.days[0])
        self.led.trade("QQQ", 100, "BUY", date=self.days[10])
        self.led.trade("QQQ", 100, "SELL", date=self.days[60])                # sold before the second ex-date
        h = self.led.holdings()
        self.assertAlmostEqual(h["income"], 50.0)
        self.assertAlmostEqual(h["positions"]["QQQ"]["income"], 50.0)
        before = self.led.holdings(self.days[29])["cash"]
        self.assertAlmostEqual(self.led.holdings(self.days[30])["cash"] - before, 50.0)


class SplitAdjustedFundamentals(unittest.TestCase):
    def test_eps_filed_before_a_split_is_put_in_todays_basis(self):
        from finsim2.engine.features import split_adjust
        rows = [{"concept": "eps", "period_end": "2020-03-31", "filed": "2020-05-01", "value": 4.0},
                {"concept": "eps", "period_end": "2020-12-31", "filed": "2021-02-01", "value": 1.2},     # after the split
                {"concept": "revenue", "period_end": "2020-03-31", "filed": "2020-05-01", "value": 100.0},
                {"concept": "shares", "period_end": "2020-03-31", "filed": "2020-05-01", "value": 10.0}]
        out = split_adjust(rows, [{"date": "2020-08-31", "value": 4.0}])
        self.assertEqual([r["value"] for r in out], [1.0, 1.2, 100.0, 40.0])

    def test_earnings_yield_has_no_step_at_a_split(self):
        from finsim2.engine.features import fundamental_features
        tmp = tempfile.mkdtemp()
        try:
            st = Store(os.path.join(tmp, "r.db"))
            days = business_days("2019-01-01", 700)
            k = 420                                                          # 4:1 split on day k (Aug 2020); price is split-adjusted
            st.upsert_prices("AAPL", [{"date": d, "close": 50.0, "adj_close": 50.0} for d in days])
            st.upsert_prices("SPY", [{"date": d, "close": 100.0, "adj_close": 100.0} for d in days])
            st.upsert_actions("AAPL", [(days[k], "SPLIT", 4.0)])
            rows = []
            for q, (end, filed) in enumerate([("2019-03-31", "2019-05-01"), ("2019-06-30", "2019-08-01"), ("2019-09-30", "2019-11-01"),
                                              ("2019-12-31", "2020-02-01"), ("2020-03-31", "2020-05-01"), ("2020-06-30", "2020-07-31")]):
                rows.append({"concept": "eps", "period_end": end, "filed": filed, "value": 2.0, "form": "10-Q", "fp": "Q"})
            for q, (end, filed) in enumerate([("2020-09-30", "2020-11-01"), ("2020-12-31", "2021-02-01"), ("2021-03-31", "2021-05-01"),
                                              ("2021-06-30", "2021-08-01")]):
                rows.append({"concept": "eps", "period_end": end, "filed": filed, "value": 0.5, "form": "10-Q", "fp": "Q"})
            st.upsert_fundamentals("AAPL", rows)
            panel = Panel(st)
            ey = fundamental_features(panel, "AAPL", panel.series("AAPL"))["earnings_yield"]
            vals = [round(v, 6) for v in ey if v is not None]
            self.assertTrue(vals)
            self.assertEqual(set(vals), {round(4 * 0.5 / 50.0, 6)})          # the same $2 a year in today's basis throughout
            st.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class DollarFirstPairs(LedgerCase):
    def test_long_usdjpy_gains_when_the_dollar_rises(self):
        self.store.upsert_asset({**self.store.asset("USDJPY")})
        self.led.deposit(10_000, self.days[0])
        self.led.trade("USDJPY", 1000, "BUY", date=self.days[100])            # 1,000 dollars against yen at 150
        hist = self.led.nav_history()
        start = hist["nav"][hist["dates"].index(self.days[100])]
        self.assertAlmostEqual(start, 10_000, places=6)
        self.assertAlmostEqual(hist["nav"][-1] - 10_000, 1000 * (1 - 150 / 160), places=6)   # +6.25% on the 1,000
        self.led.trade("USDJPY", 1000, "SELL", date=self.days[200])
        h = self.led.holdings()
        self.assertAlmostEqual(h["realized"], 1000 * (1 - 150 / 160), places=6)
        self.assertAlmostEqual(h["cash"], 10_000 + 1000 * (1 - 150 / 160), places=6)


class Performance(unittest.TestCase):
    def test_xirr_known_answer(self):
        self.assertAlmostEqual(xirr([("2020-01-01", -100.0), ("2020-12-31", 110.0)]), 0.10, places=2)

    def test_twr_ignores_the_size_and_timing_of_flows(self):
        # the assets double over the period; a large deposit arrives just before the second half
        nav = [100.0, 150.0, 150.0 + 1000.0, (150.0 + 1000.0) * 200 / 150]
        flows = [0.0, 0.0, 1000.0, 0.0]
        p = performance({"dates": ["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-06"], "nav": nav, "flows": flows})
        self.assertAlmostEqual(p["twr"], 1.0, places=9)
        self.assertAlmostEqual(p["daily_pnl"], nav[-1] - nav[-2], places=9)


if __name__ == "__main__":
    unittest.main()
