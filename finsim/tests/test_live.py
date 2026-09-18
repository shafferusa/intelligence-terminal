"""Live quotes and live tickets in a save that tracks the real market (quotes stubbed: no network in tests)."""
import time
import unittest
from datetime import date, timedelta

try:
    from helpers import make_world, assert_ledger_invariants
except ImportError:
    from tests.helpers import make_world, assert_ledger_invariants
try:
    from test_expansion import _stub_feed
    from test_career import _stub_series
except ImportError:
    from tests.test_expansion import _stub_feed
    from tests.test_career import _stub_series

from finsim.api.service import Service
from finsim.money import D
from finsim.store import EventStore
from finsim.world import World, CommandError

LIVE_UP = 1.01           # the stub quotes every symbol 1% above its stubbed base
LIVE_MULT = {}           # per-symbol override of that multiplier: tests move the "live" market with it


def _live_fetch(symbols):
    now = int(time.time())
    out = {}
    for s in symbols:
        base = 50 + (sum(map(ord, s)) % 400)
        if s == "EURUSD=X":
            base = 1.1
        elif s == "JPY=X":
            base = 150.0
        elif s.startswith("^"):
            base = 4.0
        out[s] = {"price": round(base * LIVE_MULT.get(s, LIVE_UP), 4), "time": now - 120, "prev_close": base, "high": base * 1.02, "low": base * 0.99, "volume": 1000, "currency": "USD"}
    return out


def career_world(store=None):
    """A REAL-market world on stubbed history with stubbed live quotes."""
    feed, dates = _stub_feed(40)
    start = date.fromisoformat(dates[30]); latest = date.fromisoformat(dates[-1])
    h = feed.history(start - timedelta(days=45), latest)
    store = store or EventStore(":memory:")
    w = World.create("c", "career", 5, start, store=store, initial_regime="NORMAL_GROWTH", market_source="REAL", real_history=h, real_macro=_stub_series())
    pf = w.create_portfolio("Main", "PERSONAL", D(10_000_000))
    feed._fetch_live = _live_fetch
    w.market.real_feed = feed
    w.market.real_macro_feed = None
    return w, pf, store


class LiveTicketTest(unittest.TestCase):
    def test_live_fills_across_instruments_and_replays(self):
        w, pf, store = career_world()
        self.assertTrue(w.live.available())
        last = w.market.last_bar("NVDA").close
        q = w.live.quote(w.securities["NVDA"])
        self.assertEqual(q["method"], "direct"); self.assertAlmostEqual(q["price"] / float(last), LIVE_UP * float(q["ref_price"]) / (LIVE_UP * float(last)), places=6)
        o = w.place_order(pf.id, "NVDA", "BUY", D(100), execution="LIVE")
        self.assertEqual((o.status, o.execution), ("FILLED", "LIVE"))
        t = pf.trades[o.trade_ids[-1]]
        self.assertEqual(t.execution_detail["session"], "LIVE")
        self.assertGreater(t.price, D(str(q["price"])))                       # a buy pays the spread and impact over the quote
        self.assertLess(t.price, D(str(q["price"])) * D("1.01"))
        self.assertIn("quote_time_ny", t.execution_detail); self.assertEqual(t.execution_detail["quote_ref"], "NVDA")
        assert_ledger_invariants(self, w, pf)
        # an option is repriced off the underlying's live price; margin is posted at once
        opt = next(s for s in w.securities.values() if s.is_option and s.underlying == "SPY" and not s.expired and s.option_type == "C")
        o2 = w.place_order(pf.id, opt.id, "SELL", D(5), execution="LIVE")
        self.assertEqual(o2.status, "FILLED"); self.assertEqual(pf.trades[o2.trade_ids[-1]].execution_detail["quote_method"], "underlying")
        self.assertGreater(pf.options_margin, 0)
        assert_ledger_invariants(self, w, pf)
        # a future moves with its underlying's live quote (ratio to the pinned spot); initial margin is swept now
        fut = next(s for s in w.securities.values() if s.is_future and s.underlying == "CL" and not s.expired and w.market.history.get(s.id))
        o3 = w.place_order(pf.id, fut.id, "BUY", D(2), execution="LIVE")
        t3 = pf.trades[o3.trade_ids[-1]]
        self.assertEqual(t3.execution_detail["quote_method"], "ratio")
        self.assertAlmostEqual(float(t3.execution_detail["quote_ref_price"]) / w.market.spot("CL"), float(t3.execution_detail["base_price"]) / float(w.market.last_bar(fut.id).close), places=3)
        self.assertGreater(w.futures.required_margin(pf), 0)
        assert_ledger_invariants(self, w, pf)
        # bonds have no live quote: the last close, said so
        o4 = w.place_order(pf.id, "UST-10Y", "BUY", D(1_000_000), execution="LIVE")
        self.assertEqual(o4.status, "FILLED"); self.assertEqual(pf.trades[o4.trade_ids[-1]].execution_detail["quote_method"], "last_close")
        # a limit away from the market rests against the live quote (and the next update); a conditional order is refused live
        o5 = w.place_order(pf.id, "AAPL", "BUY", D(100), "LIMIT", D(1), execution="LIVE")
        self.assertEqual(o5.status, "WORKING"); self.assertIn("not marketable at the live quote", o5.reason); self.assertIn("rests against the live quote", o5.reason)
        with self.assertRaises(CommandError):
            w.place_order(pf.id, "AAPL", "BUY", D(100), condition={"ref": "NVDA", "op": "<=", "value": 1}, execution="LIVE")
        # FX at the live rate
        fx = w.fx_spot(pf.id, "EUR", "USD", 1000, "BUY", "LIVE")
        self.assertAlmostEqual(fx.rate / (1 + w.market.fx.spread_bps("EUR", "USD") / 2e4), 1.1 * LIVE_UP, places=6)
        # replay: the fills, the margin and the working order come back exactly, without a feed
        w2 = World.load(store, "c"); w2.market.real_feed = None
        self.assertEqual(w2.replay_errors, [])
        p2 = w2.portfolio(pf.id)
        self.assertEqual(p2.positions["NVDA"].quantity, D(100)); self.assertEqual(p2.options_margin, pf.options_margin)
        self.assertEqual(p2.orders[o5.id].status, "WORKING"); self.assertEqual(p2.orders[o.id].execution, "LIVE")
        # the daily update then marks everything at the official close and expires the day order
        w.advance(2)
        self.assertEqual(pf.orders[o5.id].status, "EXPIRED")
        assert_ledger_invariants(self, w, pf)
        qs = w.live.quotes(["SPY", "NVDA", fut.id, "UST-10Y", opt.id, "NOPE"])
        self.assertEqual(set(qs), {"SPY", "NVDA", fut.id, "UST-10Y", opt.id})

    def test_resting_live_orders_fill_when_the_quote_reaches_them(self):
        """A live limit, stop, stop-limit and trailing stop rest against the quote stream: each sweep (a quote refresh, the
        server's minute tick) fills what the latest quote has reached; replay needs no feed; the update still evaluates them."""
        LIVE_MULT.clear()
        store = EventStore(":memory:")
        w, pf, _ = career_world(store)
        aapl = float(w.live.quote(w.securities["AAPL"])["price"]); nvda = float(w.live.quote(w.securities["NVDA"])["price"])
        w.place_order(pf.id, "NVDA", "BUY", D(100), execution="LIVE")                                   # something to trail out of
        lim = w.place_order(pf.id, "AAPL", "BUY", D(100), "LIMIT", D(str(round(aapl * 0.98, 2))), time_in_force="GTC", execution="LIVE")
        stp = w.place_order(pf.id, "AAPL", "BUY", D(50), "STOP", None, D(str(round(aapl * 1.03, 2))), time_in_force="GTC", execution="LIVE")
        stl = w.place_order(pf.id, "AAPL", "BUY", D(50), "STOP_LIMIT", D(str(round(aapl * 1.031, 2))), D(str(round(aapl * 1.03, 2))), time_in_force="GTC", execution="LIVE")
        trl = w.place_order(pf.id, "NVDA", "SELL", D(100), "TRAILING_STOP", None, None, time_in_force="GTC", trail_pct=0.05, execution="LIVE")
        for o in (lim, stp, stl, trl):
            self.assertEqual((o.status, o.execution), ("WORKING", "LIVE"))
        self.assertEqual({o.id for o in w.live.resting()}, {lim.id, stp.id, stl.id, trl.id})
        self.assertIn("rests against the live quote", lim.reason); self.assertIn("not touched", stp.reason)
        first_level = trl.trail_level
        # nothing moved: the sweep is quiet and emits nothing
        n = len(w.events)
        r = w.work_live(max_age_s=0)
        self.assertEqual((r["checked"], r["filled"], r["triggered"], r["ratcheted"]), (4, [], [], []))
        self.assertEqual(len(w.events), n)
        # AAPL drops 3%: the limit fills at the quote (capped at its limit), the stops stay quiet
        LIVE_MULT["AAPL"] = LIVE_UP * 0.97
        r = w.work_live(max_age_s=0)
        self.assertEqual([f["order_id"] for f in r["filled"]], [lim.id]); self.assertEqual(lim.status, "FILLED")
        t = pf.trades[lim.trade_ids[-1]]
        self.assertEqual(t.execution_detail["session"], "LIVE"); self.assertLessEqual(t.price, lim.limit_price)
        sweep = next(e for e in w.events if e.type == "LIVE_SWEEP")
        self.assertEqual(w.events[w.events.index(sweep) + 1].cause_id, sweep.id)
        self.assertIn("AAPL", sweep.payload["quotes"])
        # NVDA rises 4%: the trailing stop ratchets (an event, so replay knows) but does not fire
        LIVE_MULT["NVDA"] = LIVE_UP * 1.04
        r = w.work_live(max_age_s=0)
        self.assertEqual(r["ratcheted"], [trl.id]); self.assertGreater(trl.trail_level, first_level); self.assertEqual(trl.status, "WORKING")
        # AAPL jumps 5%: the stop triggers and fills at the live quote; the stop-limit triggers but its limit is below the quote, so it rests
        LIVE_MULT["AAPL"] = LIVE_UP * 1.05
        r = w.work_live(max_age_s=0)
        self.assertEqual([f["order_id"] for f in r["filled"]], [stp.id]); self.assertEqual(stp.status, "FILLED"); self.assertTrue(stp.triggered)
        self.assertEqual(stl.status, "WORKING"); self.assertTrue(stl.triggered); self.assertIn("limit", stl.reason)
        self.assertGreater(pf.trades[stp.trade_ids[-1]].price, D(str(aapl)) * D("1.04"))
        # NVDA falls 8% from its high: the trailing stop fires and the 100 shares go
        LIVE_MULT["NVDA"] = LIVE_UP * 1.04 * 0.92
        r = w.work_live(max_age_s=0)
        self.assertEqual([f["order_id"] for f in r["filled"]], [trl.id]); self.assertEqual(trl.status, "FILLED")
        self.assertEqual(pf.positions["NVDA"].quantity, D(0))
        assert_ledger_invariants(self, w, pf)
        # replay without a feed: every fill, trigger and ratchet comes back from the log
        w2 = World.load(store, "c"); w2.market.real_feed = None
        self.assertEqual(w2.replay_errors, [])
        p2 = w2.portfolio(pf.id)
        for o in (lim, stp, stl, trl):
            self.assertEqual((p2.orders[o.id].status, p2.orders[o.id].triggered, p2.orders[o.id].trail_level), (o.status, o.triggered, o.trail_level))
        self.assertEqual(len(p2.trades), len(pf.trades)); self.assertEqual(p2.positions["NVDA"].quantity, D(0))
        # the daily update still evaluates the resting stop-limit against the session, and settles the live fills
        w.advance(3)
        self.assertIn(stl.status, ("WORKING", "FILLED", "PARTIALLY_FILLED"))
        assert_ledger_invariants(self, w, pf)
        LIVE_MULT.clear()

    def test_quote_refresh_and_scheduler_work_the_resting_book(self):
        from finsim.api.server import Router, Scheduler
        LIVE_MULT.clear()
        store = EventStore(":memory:")
        w, pf, _ = career_world(store)
        s = Service(store); s.worlds[w.id] = w
        aapl = float(w.live.quote(w.securities["AAPL"])["price"])
        r = s.place_order(w.id, pf.id, "AAPL", "BUY", 100, "LIMIT", round(aapl * 0.99, 2), execution="LIVE", time_in_force="GTC")
        self.assertEqual(r["order"]["status"], "WORKING"); self.assertIsNone(r["trade"])
        oid = r["order"]["id"]
        # a quote refresh (what every page asks for once a minute) sweeps the book first
        LIVE_MULT["AAPL"] = LIVE_UP * 0.98; w.market.real_feed.live_cache.clear()
        live = s.live(w.id, ["AAPL"])
        self.assertEqual([f["order_id"] for f in live["worked"]["filled"]], [oid])
        self.assertEqual(s.order(w.id, pf.id, oid)["order"]["status"], "FILLED")
        self.assertNotIn("worked", s.live(w.id, ["AAPL"]), "nothing resting: no sweep reported")
        # the server's minute tick does the same for every loaded save while nobody is looking
        r2 = s.place_order(w.id, pf.id, "AAPL", "SELL", 100, "STOP", stop_price=round(aapl * 0.9, 2), execution="LIVE", time_in_force="GTC")
        self.assertEqual(r2["order"]["status"], "WORKING")
        sched = Scheduler(Router(s), interval=60, clock=lambda: None, sleep=lambda _: None)
        LIVE_MULT["AAPL"] = LIVE_UP * 0.85; w.market.real_feed.live_cache.clear()
        sched.tick()
        self.assertEqual(s.order(w.id, pf.id, r2["order"]["id"])["order"]["status"], "FILLED")
        self.assertIn(w.id, s.scheduler_state["last_live"])
        self.assertEqual(s.work_live(w.id)["checked"], 0)
        assert_ledger_invariants(self, w, pf)
        LIVE_MULT.clear()

    def test_simulated_saves_have_no_live_market(self):
        w, pf, _ = make_world()
        self.assertFalse(w.live.available())
        with self.assertRaises(CommandError):
            w.place_order(pf.id, "AAPL", "BUY", D(100), execution="LIVE")
        s = Service(EventStore(":memory:"))
        r = s.create_world("t", 42, start_date="2026-01-05")
        self.assertFalse(s.live(r["world_id"])["available"])
        self.assertFalse(s.world_info(r["world_id"])["live"]["available"])

    def test_service_live_endpoint_preview_and_order(self):
        store = EventStore(":memory:")
        w, pf, _ = career_world(store)
        s = Service(store); s.worlds[w.id] = w
        live = s.live(w.id, ["NVDA", "SPY"])
        self.assertTrue(live["available"]); self.assertEqual(set(live["quotes"]), {"NVDA", "SPY"})
        self.assertIn("time_ny", live["quotes"]["NVDA"])
        held = s.live(w.id)                                    # no ids: the index ETFs and whatever is held
        self.assertTrue({"SPY", "QQQ", "IWM"} <= set(held["quotes"]))
        pv = s.order_preview(w.id, pf.id, {"security_id": "NVDA", "side": "BUY", "quantity": 100, "execution": "LIVE"})
        self.assertEqual(pv["execution"], "LIVE"); self.assertEqual(pv["price_source"], "live ask"); self.assertIsNotNone(pv["live"])
        pv2 = s.order_preview(w.id, pf.id, {"security_id": "NVDA", "side": "BUY", "quantity": 100})
        self.assertEqual(pv2["execution"], "NEXT_UPDATE"); self.assertIsNone(pv2["live"])
        s.fx_spot(w.id, pf.id, "EUR", "USD", 200_000, "BUY", "LIVE")          # some euros first (settle T+2)
        w.advance(3)
        r = s.place_order(w.id, pf.id, "NVDA", "BUY", 100, execution="LIVE", settle_ccy="EUR")
        self.assertEqual(r["order"]["status"], "FILLED"); self.assertIsNotNone(r["trade"]); self.assertEqual(r["execution"], "LIVE")
        self.assertEqual(r["fx"]["sell_ccy"], "EUR")
        r2 = s.place_order(w.id, pf.id, "NVDA", "SELL", 50, execution="LIVE", settle_ccy="JPY")
        self.assertEqual(r2["order"]["status"], "FILLED"); self.assertEqual(r2["fx"]["buy_ccy"], "JPY")
        self.assertAlmostEqual(float(r2["fx"]["sell_amount"]), float(r2["trade"]["price"]) * 50, delta=1.0)
        r3 = s.place_order(w.id, pf.id, "NVDA", "BUY", 10)
        self.assertEqual(r3["order"]["status"], "WORKING"); self.assertIsNone(r3["trade"]); self.assertEqual(r3["execution"], "NEXT_UPDATE")
        assert_ledger_invariants(self, w, pf)


class CloseSessionInstructionTest(unittest.TestCase):
    def test_an_instruction_entered_mid_session_executes_at_the_close(self):
        w, pf, _ = make_world()
        o = w.trading.enter_order(pf.id, "AAPL", "BUY", D(100), "MARKET", None, None, "DAY", None, execute_at="CLOSE")
        o_open = w.trading.enter_order(pf.id, "MSFT", "BUY", D(100), "MARKET", None, None, "DAY", None)
        w.flush()
        w.advance(1)
        t = pf.trades[pf.orders[o.id].trade_ids[0]]; t2 = pf.trades[pf.orders[o_open.id].trade_ids[0]]
        self.assertEqual(t.execution_detail["session"], "CLOSE"); self.assertEqual(t.execution_detail["base_price"], t.execution_detail["reference_close"])
        self.assertEqual(t2.execution_detail["session"], "OPEN"); self.assertEqual(t2.execution_detail["base_price"], t2.execution_detail["reference_open"])
        self.assertIn("executes at its close", pf.orders[o.id].history[0]["note"])
        # a stop entered mid-session triggers only on the close, never on a high that printed before it was entered
        bar = w.market.last_bar("AAPL")
        stop = w.trading.enter_order(pf.id, "AAPL", "BUY", D(100), "STOP", None, bar.close * D("1.5"), "GTC", None, execute_at="CLOSE")
        w.flush(); w.advance(1)
        self.assertEqual(pf.orders[stop.id].status, "WORKING")
        assert_ledger_invariants(self, w, pf)


if __name__ == "__main__":
    unittest.main()
