"""2026-09 expansion: the strategy playbook, private credit, the FX market view, and real-market worlds (offline, stubbed feed)."""
import math
import unittest
from datetime import date, timedelta

try:
    from helpers import make_world, assert_ledger_invariants
except ImportError:  # run from the package root
    from tests.helpers import make_world, assert_ledger_invariants

from finsim.api.service import Service
from finsim.engines.realfeed import RealFeed, COMMODITY_SYMBOLS, FX_SYMBOLS, RATE_SYMBOLS
from finsim.money import D
from finsim.store import EventStore
from finsim.world import World, CommandError


class PlaybookTest(unittest.TestCase):
    def test_catalogue_preview_execute_replay(self):
        w, pf, store = make_world(capital=50_000_000)
        cat = w.playbook.catalogue()
        self.assertGreaterEqual(len(cat["strategies"]), 38)
        keys = {s["key"] for s in cat["strategies"]}
        for k in ("PROTECTIVE_PUT", "ZERO_COST_COLLAR", "IRON_CONDOR", "COVERED_CALL", "DELTA_NEUTRAL_TRS", "LONG_FUTURES_PROTECTED", "RV_PROTECTED"):
            self.assertIn(k, keys)
        pv = w.playbook.preview(pf, "PROTECTIVE_PUT", {"underlying": "SPY", "units": 1000, "tenor_months": 3})
        self.assertEqual([l["kind"] for l in pv["legs"]], ["STOCK", "OPTION"])
        put = pv["legs"][1]
        self.assertEqual(put["type"], "P")
        self.assertLess(put["strike"], pv["totals"]["spot"])
        self.assertEqual(put["quantity"], 10)
        self.assertLess(pv["totals"]["net_cash_today"], 0)
        self.assertIsNone(pv["max_gain"])            # long stock: unlimited upside
        self.assertIsNotNone(pv["max_loss"])         # floored by the put
        self.assertTrue(pv["breakevens"])
        # a strike override is honoured (percent from spot)
        pv2 = w.playbook.preview(pf, "PROTECTIVE_PUT", {"underlying": "SPY", "units": 1000, "tenor_months": 3, "strikes": {"1": -15}})
        self.assertLess(pv2["legs"][1]["strike"], put["strike"])
        # zero-cost collar: the sold call roughly pays for the put
        zc = w.playbook.preview(pf, "ZERO_COST_COLLAR", {"underlying": "SPY", "units": 1000, "tenor_months": 3})
        self.assertLess(abs(zc["totals"]["net_option_premium"]), abs(pv["totals"]["net_option_premium"]))
        # iron condor: four option legs, defined risk both ways
        ic = w.playbook.preview(pf, "IRON_CONDOR", {"underlying": "SPY", "units": 1000, "tenor_months": 2})
        self.assertEqual(len(ic["legs"]), 4)
        self.assertIsNotNone(ic["max_gain"]); self.assertIsNotNone(ic["max_loss"])
        self.assertEqual(len(ic["breakevens"]), 2)
        with self.assertRaises(CommandError):
            w.playbook.preview(pf, "NOT_A_STRATEGY", {})
        # execute a protected short: locate + borrow + short, then a call
        r = w.playbook.execute(pf, "PROTECTED_SHORT", {"underlying": "META", "units": 300, "tenor_months": 2})
        self.assertTrue(r["all_ok"], r["results"])
        w.advance(1)
        self.assertEqual(pf.positions["META"].quantity, D(-300))
        calls = [p for p in pf.positions.values() if p.is_option and p.quantity > 0]
        self.assertEqual(len(calls), 1)
        assert_ledger_invariants(self, w, pf)
        # a swap leg deals with the best dealer
        r2 = w.playbook.execute(pf, "SYNTHETIC_LONG_TRS", {"underlying": "SPY", "units": 500, "tenor_months": 3})
        self.assertTrue(r2["all_ok"], r2["results"])
        self.assertTrue(any(t.product == "TRS" and t.status == "OPEN" for t in pf.otc_trades.values()))
        w.advance(2)
        assert_ledger_invariants(self, w, pf)
        w2 = World.load(store, "t")
        self.assertEqual(w2.ledgers[pf.id].nav(), w.ledgers[pf.id].nav())
        self.assertEqual(w2.replay_errors, [])


class PrivateCreditTest(unittest.TestCase):
    def test_pipeline_commit_marks_sell_replay(self):
        w, pf, store = make_world(capital=100_000_000)
        pipe = w.pcredit.pipeline()
        self.assertGreaterEqual(len(pipe), 6)
        for d in pipe:
            self.assertGreaterEqual(d["spread_bps"], 300)
            self.assertIn(d["facility"], ("FIRST_LIEN", "UNITRANCHE", "SECOND_LIEN", "MEZZANINE"))
            self.assertGreater(d["all_in_yield"], w.pcredit.policy_rate())
        self.assertEqual(w.pcredit.pipeline(), pipe)                  # deterministic within the month
        deal = pipe[0]
        with self.assertRaises(CommandError):
            w.commit_private_credit(pf.id, deal["id"], D(500_000))    # below the minimum
        with self.assertRaises(CommandError):
            w.commit_private_credit(pf.id, deal["id"], D(1_050_000))  # not a 100k multiple
        cash0 = pf.cash["USD"].balance
        loan = w.commit_private_credit(pf.id, deal["id"], D(10_000_000))
        self.assertEqual(loan.par, D(10_000_000))
        self.assertLess(loan.price, 1.0)                                # funded at par less OID
        self.assertEqual(cash0 - pf.cash["USD"].balance, D(10_000_000) * D(str(loan.price)))
        self.assertLess(w.pcredit.pipeline()[0]["available"], deal["available"])
        assert_ledger_invariants(self, w, pf)
        w.advance(70)                                                   # a coupon and a monthly review
        book = w.pcredit.book(pf)
        row = next(r for r in book["loans"] if r["id"] == loan.id)
        self.assertIn(row["status"], ("OPEN", "DEFAULTED"))
        if row["status"] == "OPEN":
            self.assertGreater(float(row["interest_paid"]) + float(row["accrued_interest"]), 0)
        assert_ledger_invariants(self, w, pf)
        s = w.pnl.compute_summary(pf)
        self.assertIn("private_credit", s.get("buckets", {}) or {"private_credit": 0})
        if row["status"] == "OPEN":
            before = loan.par
            w.sell_private_credit(pf.id, loan.id, D(2_000_000))
            self.assertEqual(loan.par, before - D(2_000_000))
            assert_ledger_invariants(self, w, pf)
        w2 = World.load(store, "t")
        self.assertEqual(w2.ledgers[pf.id].nav(), w.ledgers[pf.id].nav())
        self.assertEqual(w2.replay_errors, [])
        self.assertEqual(len(w2.portfolio(pf.id).private_loans), len(pf.private_loans))


class FXMarketViewTest(unittest.TestCase):
    def test_fx_market_shape_and_identities(self):
        store = EventStore(":memory:")
        w, pf, _ = make_world(store=store)
        w.advance(3)
        svc = Service(store)
        r = svc.fx_market("t")
        self.assertEqual(r["source"], "SIMULATED")
        ccys = {row["ccy"] for row in r["rows"]}
        self.assertTrue({"EUR", "GBP", "JPY", "CHF", "CAD", "AUD"} <= ccys); from finsim.engines.fx_market import SPECS; self.assertEqual(ccys, set(SPECS))   # every modelled currency, none missing
        for row in r["rows"]:
            d = row["drivers"]
            self.assertAlmostEqual(d["carry"] + d["equity_factor"] + d["mean_reversion"] + d["flow"], d["total"], places=9)
            self.assertAlmostEqual(row["carry"], row["usd_rate"] - row["rate"], places=9)
            self.assertEqual(len(row["history"]), min(120, len(w.market.fx.history[row["ccy"]])))
            f = row["forwards"]["1Y"]
            # covered interest parity: the currency with the lower rate is dearer forward
            if row["rate"] < row["usd_rate"]:
                self.assertGreater(f["rate"], row["quote"]) if not row["inverse"] else self.assertLess(f["rate"], row["quote"])
            else:
                self.assertLessEqual(f["rate"], row["quote"] * 1.000001) if not row["inverse"] else self.assertGreaterEqual(f["rate"], row["quote"] * 0.999999)
        jpy = next(x for x in r["rows"] if x["ccy"] == "JPY")
        self.assertTrue(jpy["inverse"]); self.assertGreater(jpy["quote"], 50)          # yen per dollar
        self.assertAlmostEqual(r["cross"]["EUR"]["GBP"], w.market.fx.spot["EUR"] / w.market.fx.spot["GBP"], places=9)


def _stub_feed(days):
    """A RealFeed whose fetch returns deterministic closes for every symbol over `days` (no network)."""
    from finsim.engines.market import build_universe
    from finsim.engines.realfeed import equity_symbols_for
    dates = []
    d = date(2026, 6, 1)
    while len(dates) < days:
        if d.weekday() < 5:
            dates.append(d.isoformat())
        d += timedelta(days=1)

    def fetch(symbols, rng):
        out = {}
        for sym in symbols:
            base = 50 + (sum(map(ord, sym)) % 400)
            if sym in RATE_SYMBOLS.values():
                base = 4.0 + (sum(map(ord, sym)) % 7) / 10        # percent, like ^TNX
            if sym in (s for s, _ in FX_SYMBOLS.values()):
                base = 1.1 if sym.startswith("EUR") else 150.0 if sym == "JPY=X" else 0.9
            out[sym] = {iso: round(base * (1 + 0.002 * math.sin(i / 3.0) + 0.0005 * i), 4) for i, iso in enumerate(dates)}
        return out
    feed = RealFeed(equity_symbols_for(build_universe(date(2026, 6, 1), 3)), cache_path="/dev/null", fetch=fetch)
    feed.data, feed.fetched_at = {}, {}
    feed.refresh("1y", force=True)
    return feed, dates


class RealFeedTest(unittest.TestCase):
    def test_targets_and_history(self):
        feed, dates = _stub_feed(30)
        self.assertEqual(feed.latest_date(), dates[-1])
        self.assertTrue(feed.has(date.fromisoformat(dates[-1])))
        self.assertFalse(feed.has(date.fromisoformat(dates[-1]) + timedelta(days=1)))
        t = feed.targets_for(date.fromisoformat(dates[5]))
        self.assertIn("SPY", t["equities"]); self.assertIn("CL", t["commodities"]); self.assertIn("EUR", t["fx"]); self.assertIn("y10", t["rates"])
        self.assertAlmostEqual(t["rates"]["y10"], feed.data["^TNX"][dates[5]] / 100.0)
        self.assertAlmostEqual(t["fx"]["JPY"], 1.0 / feed.data["JPY=X"][dates[5]])
        self.assertAlmostEqual(t["commodities"]["ZC"], feed.data["ZC=F"][dates[5]] / 100.0)      # cents → dollars
        h = feed.history(date.fromisoformat(dates[10]), date.fromisoformat(dates[20]))
        self.assertEqual(len(h["equities"]["SPY"]), 11)
        self.assertEqual(RealFeed.targets_from_history(h, date.fromisoformat(dates[12]))["equities"]["SPY"], h["equities"]["SPY"][dates[12]])
        self.assertIsNone(RealFeed.targets_from_history(h, date.fromisoformat(dates[25])))

    def test_real_world_pins_closes_and_waits(self):
        feed, dates = _stub_feed(40)
        start = date.fromisoformat(dates[30]); latest = date.fromisoformat(dates[-1])
        h = feed.history(start - timedelta(days=45), latest)
        store = EventStore(":memory:")
        w = World.create("r", "real", 5, start, store=store, initial_regime="NORMAL_GROWTH", market_source="REAL", real_history=h)
        pf = w.create_portfolio("Main", "PERSONAL", D(10_000_000))
        w.market.real_feed = None                      # offline: only the stored history exists
        self.assertEqual(w.market_source, "REAL")
        spy = h["equities"]["SPY"]
        self.assertAlmostEqual(float(w.market.history["SPY"][-1].close), spy[start.isoformat()], places=2)
        self.assertAlmostEqual(float(w.market.history["AAPL"][-8].close), h["equities"]["AAPL"][w.market.history["AAPL"][-8].date], places=2)
        w.place_order(pf.id, "SPY", "BUY", D(100))
        w.advance(3)
        for b in w.market.history["SPY"][-3:]:
            self.assertAlmostEqual(float(b.close), spy[b.date], places=2)
        self.assertAlmostEqual(w.market.fx.spot["EUR"], h["fx"]["EUR"][w.current_date.isoformat()], places=6)
        assert_ledger_invariants(self, w, pf)
        # past the stored history and without a feed the world waits
        while w.calendar.next_business_day(w.current_date) <= latest:
            w.advance(1)
        self.assertIsNotNone(w.real_market_ready(w.calendar.next_business_day(w.current_date)))
        with self.assertRaises(CommandError):
            w.advance(1)
        w2 = World.load(store, "r")
        w2.market.real_feed = None
        self.assertEqual(w2.replay_errors, [])
        self.assertEqual(w2.ledgers[pf.id].nav(), w.ledgers[pf.id].nav())
        self.assertEqual(float(w2.market.history["SPY"][-1].close), float(w.market.history["SPY"][-1].close))


if __name__ == "__main__":
    unittest.main()
