"""Career saves on the real day: the 17:00 update, the instruction window, real macro and headlines (stubbed), currency-aware tickets."""
import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

try:
    from helpers import make_world, assert_ledger_invariants
except ImportError:
    from tests.helpers import make_world, assert_ledger_invariants
try:
    from test_expansion import _stub_feed
except ImportError:
    from tests.test_expansion import _stub_feed

from finsim.api.service import Service
from finsim.clock import ClockConfig, trading_window, session_closed, instruction_session
from finsim.calendar import BusinessCalendar
from finsim.engines.realmacro import state_as_of, releases_on, upcoming, fomc_dates
from finsim.engines.realnews import RealNews
from finsim.money import D
from finsim.store import EventStore
from finsim.world import World, CommandError

NY = ZoneInfo("America/New_York")


def ny(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=NY)


class TradingWindowTest(unittest.TestCase):
    def test_window_is_always_open_unless_the_session_lock_is_on(self):
        cal = BusinessCalendar()
        cfg = ClockConfig("REAL_TIME", "America/New_York", "17:00")            # default: no lock, trade at any hour
        for hh in (8, 11, 16, 18):
            w = trading_window(cfg, cal, ny(2026, 9, 17, hh, 0))
            self.assertTrue(w["open"]); self.assertFalse(w["lock"])
        self.assertTrue(trading_window(cfg, cal, ny(2026, 9, 17, 11, 0))["session_running"])
        self.assertFalse(trading_window(cfg, cal, ny(2026, 9, 17, 18, 0))["session_running"])
        # an instruction entered while the session runs executes at its close, otherwise at the next open
        self.assertEqual(instruction_session(cfg, cal, date(2026, 9, 16), ny(2026, 9, 17, 11, 0)), "CLOSE")
        self.assertEqual(instruction_session(cfg, cal, date(2026, 9, 16), ny(2026, 9, 17, 8, 0)), "OPEN")
        self.assertEqual(instruction_session(cfg, cal, date(2026, 9, 16), ny(2026, 9, 16, 18, 0)), "OPEN")
        self.assertEqual(instruction_session(cfg, cal, date(2026, 9, 15), ny(2026, 9, 17, 8, 0)), "CLOSE", "a session already behind the world is history")
        self.assertEqual(instruction_session(ClockConfig("SANDBOX"), cal, date(2026, 9, 16), ny(2026, 9, 17, 11, 0)), "OPEN")

    def test_window_with_the_lock_opens_after_the_close_and_shuts_at_the_open(self):
        cal = BusinessCalendar()
        cfg = ClockConfig("REAL_TIME", "America/New_York", "17:00", lock_session=True)
        self.assertTrue(trading_window(cfg, cal, ny(2026, 9, 17, 8, 0))["open"])
        self.assertTrue(trading_window(cfg, cal, ny(2026, 9, 17, 9, 29))["open"])
        self.assertFalse(trading_window(cfg, cal, ny(2026, 9, 17, 9, 30))["open"])
        self.assertFalse(trading_window(cfg, cal, ny(2026, 9, 17, 16, 30))["open"], "closed until the update processes the day")
        self.assertTrue(trading_window(cfg, cal, ny(2026, 9, 17, 17, 0))["open"])
        self.assertTrue(trading_window(cfg, cal, ny(2026, 9, 19, 12, 0))["open"], "weekends are open")
        self.assertTrue(trading_window(ClockConfig("SANDBOX"), cal, ny(2026, 9, 17, 12, 0))["open"])
        # a Chicago player at 16:00 local is 17:00 New York
        w = trading_window(ClockConfig("REAL_TIME", "America/Chicago", "16:00", lock_session=True), cal, ny(2026, 9, 17, 16, 59))
        self.assertFalse(w["open"]); self.assertTrue(w["opens_at"].startswith("2026-09-17T17:00"))
        self.assertFalse(session_closed(date(2026, 9, 17), ny(2026, 9, 17, 15, 59)))
        self.assertTrue(session_closed(date(2026, 9, 17), ny(2026, 9, 17, 16, 0)))
        self.assertTrue(session_closed(date(2026, 9, 16), ny(2026, 9, 17, 8, 0)))

    def test_service_refuses_instructions_while_the_session_runs(self):
        store = EventStore(":memory:")
        s = Service(store)
        now = [ny(2026, 1, 9, 18, 0)]
        s.now = lambda: now[0]
        r = s.create_world("career", 42, job="PORTFOLIO_MANAGER", clock_mode="REAL_TIME", timezone="America/New_York", at=now[0], market_source="SIMULATED", lock_session=True)
        w = s.worlds[r["world_id"]]
        self.assertEqual(w.clock.update_time, "17:00"); self.assertTrue(w.clock.lock_session)
        wid, pid = r["world_id"], r["portfolio_id"]
        self.assertTrue(s.world_info(wid)["trading_window"]["open"])
        s.place_order(wid, pid, "SPY", "BUY", 100)
        now[0] = ny(2026, 1, 12, 11, 0)
        self.assertFalse(s.world_info(wid)["trading_window"]["open"])
        with self.assertRaises(CommandError):
            s.place_order(wid, pid, "SPY", "BUY", 100)
        with self.assertRaises(CommandError):
            s.fx_spot(wid, pid, "EUR", "USD", 1000)
        with self.assertRaises(CommandError):
            s.playbook_execute(wid, pid, {"key": "LONG_CALL", "params": {"underlying": "SPY", "units": 100}})
        s.cancel_order(wid, pid, s.orders(wid, pid)[0]["id"])           # cancelling is always allowed
        now[0] = ny(2026, 1, 12, 17, 30)
        self.assertTrue(s.world_info(wid)["trading_window"]["open"])
        s.place_order(wid, pid, "SPY", "BUY", 100)
        # the update time can move, but a real-market save must stay after the close
        with self.assertRaises(CommandError):
            s.create_world("c2", 1, clock_mode="REAL_TIME", update_time="09:00", at=now[0])
        s.set_clock(wid, "18:30", "America/Chicago")
        self.assertEqual((w.clock.update_time, w.clock.timezone), ("18:30", "America/Chicago"))
        w2 = World.load(store, wid)
        self.assertEqual((w2.clock.update_time, w2.clock.timezone, w2.clock.lock_session), ("18:30", "America/Chicago", True))
        # the lock is a setting: off, the same moment takes instructions (at the close) and the header pill says so
        now[0] = ny(2026, 1, 13, 11, 0)
        self.assertFalse(s.world_info(wid)["trading_window"]["open"])
        s.set_clock(wid, lock_session=False)
        tw = s.world_info(wid)["trading_window"]
        self.assertTrue(tw["open"]); self.assertFalse(tw["lock"]); self.assertTrue(tw["session_running"])
        self.assertEqual(s.world_info(wid)["live"]["instruction_session"], "CLOSE")
        o = s.place_order(wid, pid, "SPY", "BUY", 100)
        self.assertEqual(o["order"]["execute_at"], "CLOSE")
        self.assertFalse(World.load(store, wid).clock.lock_session)


def _stub_series():
    cpi = {}
    v = 300.0
    for y in (2024, 2025, 2026):
        for m in range(1, 13):
            if (y, m) > (2026, 8):
                break
            v *= 1.0025
            cpi[f"{y}-{m:02d}-01"] = round(v, 3)
    un = {k: 4.0 + (i % 5) * 0.1 for i, k in enumerate(cpi)}
    pay = {k: 150_000 + 120 * i for i, k in enumerate(cpi)}
    gdp = {"2025-10-01": 2.4, "2026-01-01": 1.9, "2026-04-01": 2.6}
    fed = {"2025-12-11": 3.75, "2026-01-29": 3.75, "2026-03-19": 3.50, "2026-09-17": 3.50}
    return {"cpi": cpi, "core_cpi": cpi, "unemployment": un, "payrolls": pay, "gdp": gdp, "fed_upper": fed, "fed_lower": {k: v - 0.25 for k, v in fed.items()}, "retail": {}, "core_pce": {}}


class RealMacroTest(unittest.TestCase):
    def test_state_releases_and_calendar(self):
        cal = BusinessCalendar(); s = _stub_series()
        st = state_as_of(s, date(2026, 9, 17), cal.roll)
        self.assertAlmostEqual(st["inflation"], (1.0025 ** 12 - 1) * 100, places=1)
        self.assertEqual(st["inflation_period"], "2026-08-01")            # August CPI is out by mid-September
        self.assertEqual(state_as_of(s, date(2026, 9, 10), cal.roll)["inflation_period"], "2026-07-01", "not before its release date")
        self.assertEqual(st["policy_rate"], 0.035); self.assertEqual(st["growth"], 2.6); self.assertEqual(st["next_meeting"], "2026-10-28")
        rel = releases_on(s, date(2026, 9, 14), cal.roll)             # 12 Sep 2026 is a Saturday: CPI rolls to Monday
        self.assertEqual([r["kind"] for r in rel], ["CPI"]); self.assertEqual(rel[0]["period"], "2026-08"); self.assertIsNone(rel[0]["consensus"])
        jobs = releases_on(s, date(2026, 9, 4), cal.roll)
        self.assertEqual(jobs[0]["kind"], "JOBS"); self.assertEqual(jobs[0]["payrolls_k"], 120)
        fomc = releases_on(s, date(2026, 9, 16), cal.roll)
        self.assertTrue(any(r["kind"] == "FOMC" and "holds" in r["headline"] for r in fomc))
        self.assertEqual(fomc_dates(2026)[-1], date(2026, 12, 9))
        up = upcoming(s, date(2026, 9, 17), cal.roll)
        self.assertIn("FOMC", [u["kind"] for u in up]); self.assertIn("CPI", [u["kind"] for u in up])

    def test_career_world_pins_the_real_economy_and_stores_headlines(self):
        feed, dates = _stub_feed(40)
        start = date.fromisoformat(dates[30]); latest = date.fromisoformat(dates[-1])
        h = feed.history(start - timedelta(days=45), latest)
        store = EventStore(":memory:")
        w = World.create("c", "career", 5, start, store=store, initial_regime="NORMAL_GROWTH", market_source="REAL", real_history=h, real_macro=_stub_series())
        pf = w.create_portfolio("Main", "PERSONAL", D(10_000_000))
        w.market.real_feed = None; w.market.real_macro_feed = None
        told = []
        w.real_news = RealNews(fetch_yahoo=lambda q, n: (told.append(q) or [{"title": f"Wire: {q} moves", "publisher": "Test Wire", "link": f"https://x/{q}/{w.current_date}", "time": int(datetime.combine(w.current_date, datetime.min.time(), tzinfo=NY).timestamp()) + 3600 * 12, "tickers": [q] if q in w.securities else []}]),
                              fetch_fed=lambda: [])       # the fetch happens while the session date is current_date
        m = w.market.macro
        self.assertIsNotNone(m.real_series)
        self.assertEqual(m.state.policy_rate, 0.035)                       # the real target, not the seeded one
        self.assertEqual(m.meeting_dates(2026)[0], date(2026, 1, 28))
        w.place_order(pf.id, "NVDA", "BUY", D(100))
        w.advance(2)
        self.assertIn("NVDA", told, "headlines are fetched for the names in the book")
        wire = [n for n in w.news if n.publisher == "Test Wire"]
        self.assertTrue(wire); self.assertTrue(all(n.date == w.current_date.isoformat() or n.date < w.current_date.isoformat() for n in wire))
        self.assertTrue(any("NVDA" in n.refs for n in wire))
        self.assertFalse(any(n.category in ("EARNINGS", "COMMODITY", "LENDING") for n in w.news), "no invented news in a career world")
        self.assertEqual(w.market.cevents.announced, [], "no invented corporate events either")
        assert_ledger_invariants(self, w, pf)
        w2 = World.load(store, "c")
        w2.market.real_feed = None; w2.market.real_macro_feed = None
        self.assertEqual(w2.replay_errors, [])
        self.assertEqual(len([n for n in w2.news if n.publisher == "Test Wire"]), len(wire))
        self.assertEqual(w2.market.macro.state.policy_rate, m.state.policy_rate)
        self.assertEqual(w2.market.macro.meeting_dates(2026)[0], date(2026, 1, 28))


class CurrencyTicketTest(unittest.TestCase):
    def test_pay_with_euros_and_receive_in_yen(self):
        s = Service(EventStore(":memory:"))
        r = s.create_world("t", 42, start_date="2026-01-05", capital=10_000_000)
        w = s.worlds[r["world_id"]]; pf = w.portfolio(r["portfolio_id"])
        s.fx_spot(w.id, pf.id, "EUR", "USD", 2_000_000)          # some euros to spend
        w.advance(3)
        eur0 = pf.cash["EUR"].balance
        pv = s.order_preview(w.id, pf.id, {"security_id": "AAPL", "side": "BUY", "quantity": 1000, "settle_ccy": "EUR"})
        self.assertEqual(pv["settle_ccy"], "EUR"); self.assertEqual(pv["fx"]["direction"], "pay")
        self.assertAlmostEqual(float(pv["fx"]["buy_amount"]), float(pv["cash_needed"]), places=2)
        r = s.place_order(w.id, pf.id, "AAPL", "BUY", 1000, settle_ccy="EUR")
        self.assertIsNotNone(r["fx"]); self.assertEqual(r["fx"]["sell_ccy"], "EUR"); self.assertEqual(r["fx"]["buy_ccy"], "USD")
        w.advance(3)
        self.assertLess(pf.cash["EUR"].balance, eur0)
        self.assertEqual(pf.positions["AAPL"].quantity, D(1000))
        r2 = s.place_order(w.id, pf.id, "AAPL", "SELL", 500, settle_ccy="JPY")
        self.assertEqual(r2["fx"]["buy_ccy"], "JPY"); self.assertEqual(r2["fx"]["sell_ccy"], "USD")
        w.advance(3)
        self.assertGreater(pf.cash["JPY"].balance, 0)
        assert_ledger_invariants(self, w, pf)
        with self.assertRaises(CommandError):
            s.place_order(w.id, pf.id, "AAPL", "BUY", 100000, settle_ccy="GBP")   # no pounds


if __name__ == "__main__":
    unittest.main()
