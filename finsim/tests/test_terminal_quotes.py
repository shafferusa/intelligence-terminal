"""WP-01 quotes tests: providers parse the probe's real shapes, the UA policy, breaker, limiter, cache scheduling with a FakeClock,
chain single-flight, refresher slots, persist/load, the §8.2 freshness table and the worker's light commands (TERMINAL_WORKPLAN WP-01).

Every expected number is worked by hand from the fixture in a comment; nothing here touches the network or ~/.finsim.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from datetime import date
from decimal import Decimal

from tests.terminal_helpers import US_EXCHANGE, buy, execute, make_runtime
from finsim.terminal.config import BROWSER_UA, GOV_UA, TERMINAL_UA
from finsim.terminal.clock import FakeClock, epoch_of
from finsim.terminal.instruments import fx_yahoo
from finsim.terminal.quotes import Bar1m, QuoteRecord, freshness, is_executable
from finsim.terminal.quotes.cache import ChainCache, QuoteCache, record_from_intraday
from finsim.terminal.quotes.http import CircuitBreaker, FetchError, ProviderHealth, RateLimiter, ua_for
from finsim.terminal.quotes.providers import (CboeChain, FakeProvider, FinraShortVolume, Frankfurter, FredSeries, ProviderSet, TENOR_YEARS, TreasuryCurve,
                                              YahooChart, chart_result, parse_chain, parse_daily, parse_events, parse_finra, parse_frankfurter, parse_fred_csv,
                                              parse_intraday, parse_treasury_xml)
from finsim.terminal.quotes.rates import CurveStore, FRED_SERIES, FxStore, RateStore
from finsim.terminal.quotes.worker import QuoteWorker, register_all
from finsim.terminal.sessions import SessionState

_HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(_HERE, "fixtures", "terminal")
TODAY = date(2026, 10, 20)
# 2026-10-20 16:00:00 ET (EDT, UTC-4) = 20:00:00Z = 1792526400 (the probe's regularMarketTime)
T_CLOSE = 1792526400


def load(name: str) -> bytes:
    with open(os.path.join(FIX, name), "rb") as fh:
        return fh.read()


def D(x) -> Decimal:
    return Decimal(str(x))


class _Recorder:
    def __init__(self):
        self.batches = []

    def on_quotes(self, ids):
        self.batches.append(list(ids))


# ===== providers parse the fixtures ===================================================================================================
class ProviderParseTests(unittest.TestCase):
    def test_yahoo_intraday_meta_and_last_completed_bar(self):
        result = chart_result(json.loads(load("yahoo_spy_1m.json")))
        # fetched_at 1792526430: completed bars have t <= 1792526430 - 60 = 1792526370 -> the 16:59:00 bar at 1792526340 (c 769.28, v 50000)
        iq = parse_intraday(result, "SPY", 1792526430)
        self.assertEqual(D("769.32"), iq.last)
        self.assertEqual(T_CLOSE, iq.quote_time)
        self.assertEqual(D("766.48"), iq.prev_close)
        self.assertEqual((D("772.275"), D("764.26"), 30979608), (iq.day_high, iq.day_low, iq.day_volume))
        self.assertEqual(("USD", "America/New_York"), (iq.currency, iq.exchange_tz))
        self.assertEqual((1792503000, T_CLOSE), iq.provider_session)
        self.assertEqual(1792526340, iq.bar.t)
        self.assertEqual((D("769.28"), 50000), (iq.bar.c, iq.bar.v))
        # 7 timestamps, the last one has a null close (skipped) -> 6 bars; the tail keeps the last 5 of them
        self.assertEqual([1792526160, 1792526220, 1792526280, 1792526340, 1792526400], [b.t for b in iq.bars_tail])
        self.assertEqual(0, iq.bars_tail[-1].v)          # the 16:00 bar: volume 0, not yet completed by the rule above
        self.assertEqual("ETF", iq.meta["instrumentType"])

    def test_yahoo_jpy_is_quoted_per_usd_and_inverted_by_the_master(self):
        iq = parse_intraday(chart_result(json.loads(load("yahoo_jpy_1m.json"))), "JPY=X", 1792526500)
        self.assertEqual(D("158.1"), iq.last)
        self.assertEqual("JPY", iq.currency)
        self.assertEqual(1792526489, iq.quote_time)
        self.assertEqual((1792450800, 1792537140), iq.provider_session)
        self.assertEqual(("JPY=X", True), fx_yahoo("USDJPY"))          # the pair's own mark stays 158.10; fx("JPY") inverts (marks test)
        self.assertEqual(("EURUSD=X", False), fx_yahoo("EURUSD"))

    def test_yahoo_daily_bars(self):
        bars = parse_daily(chart_result(json.loads(load("yahoo_daily_spy.json"))))
        # six 09:30 ET timestamps (13:30Z, gmtoffset -14400) -> exchange-local dates 10-13 .. 10-20
        self.assertEqual([date(2026, 10, 13), date(2026, 10, 14), date(2026, 10, 15), date(2026, 10, 16), date(2026, 10, 19), date(2026, 10, 20)], [b.date for b in bars])
        self.assertEqual(D("760.11"), bars[0].c)
        self.assertEqual((D("769.32"), 30979608, D("772.275")), (bars[-1].c, bars[-1].v, bars[-1].h))

    def test_yahoo_events_dividend_and_split(self):
        ev = parse_events(chart_result(json.loads(load("yahoo_events_aapl.json"))))
        # dividend at 1794234600 = 2026-11-09 14:30Z; minus 4 h -> 2026-11-09; split 1794839400 = 2026-11-16 14:30Z
        self.assertEqual([(date(2026, 11, 9), D("0.26"))], [(d.ex_date, d.amount) for d in ev.dividends])
        self.assertEqual([(date(2026, 11, 16), 4, 1, "4:1")], [(s.date, s.numerator, s.denominator, s.ratio) for s in ev.splits])

    def test_cboe_chain_reduction_counts(self):
        snap = parse_chain(json.loads(load("cboe_aapl_trimmed.json")), "AAPL", "AAPL", TODAY, 1792526500)
        # 13 raw rows: band = 339.485 x [0.65, 1.35] = [220.66525, 458.30475] drops the 200 call and the 500 put;
        # 2027-12-17 is 423 days > 400 -> dropped; "AAPLXX" fails the OCC pattern -> dropped; 9 kept.
        self.assertEqual(13, snap.n_raw)
        self.assertEqual(9, snap.n_kept())
        self.assertEqual(["2026-11-20", "2027-01-15"], snap.expiries())
        self.assertEqual(D("339.485"), snap.spot)
        u = snap.underlying
        self.assertEqual((D("339.48"), D("339.5"), 10, 12, D("342.94")), (u.bid, u.ask, u.bid_size, u.ask_size, u.prev_close))
        self.assertAlmostEqual(0.26267, snap.iv30, places=6)              # 26.267 % -> decimal
        # last_trade_time 2026-10-20T15:46:47 ET = 19:46:47Z = 1792526400 - 13 min 13 s = 1792526400 - 793
        self.assertEqual(T_CLOSE - 793, snap.quote_time)
        row = snap.by_occ("AAPL261120C00340000")
        self.assertEqual((D("340"), D("8.6"), D("8.85"), 60, 44), (row.strike, row.bid, row.ask, row.bid_size, row.ask_size))
        self.assertEqual(D("8.725"), row.mid)                              # (8.60 + 8.85) / 2
        self.assertTrue(row.quoted)
        self.assertAlmostEqual(0.258, row.iv, places=6)                   # already a decimal: no normalisation (median 0.26 < 3)
        unq = snap.by_occ("AAPL261120C00360000")
        self.assertFalse(unq.quoted)
        self.assertIsNone(unq.mid)
        self.assertIsNone(snap.by_occ("AAPL261120C00200000"))
        self.assertIsNone(snap.by_occ("AAPL271217C00340000"))
        self.assertEqual(6, len(snap.rows_for("2026-11-20")) - 1)          # 7 rows kept for Nov incl. the unquoted 360 call

    def test_treasury_curve_tenors(self):
        curve = parse_treasury_xml(load("treasury_curve.xml"), 1792526500)
        self.assertEqual(date(2026, 10, 19), curve.as_of)                   # the latest NEW_DATE of the two entries
        self.assertEqual(13, len(curve.points))
        self.assertEqual(D("5.1200"), curve.points[Decimal(10)])
        self.assertEqual(D("0.08333333"), TENOR_YEARS["1M"])              # 1/12 at 8 dp
        self.assertEqual(D("4.1200"), curve.labels()["1M"])
        self.assertEqual(D("5.5000"), curve.labels()["30Y"])
        self.assertEqual(["1M", "2M", "3M", "4M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"], list(curve.labels()))

    def test_fred_blanks_and_dots_skipped(self):
        sofr = parse_fred_csv(load("fred_sofr.csv"))
        self.assertEqual(7, len(sofr))                                       # 8 rows, 2026-10-12 blank
        self.assertEqual((date(2026, 10, 19), D("3.87")), sofr[-1])
        self.assertNotIn(date(2026, 10, 12), dict(sofr))
        hy = parse_fred_csv(load("fred_hyoas.csv"))
        self.assertEqual(4, len(hy))                                         # 2026-10-16 is "."
        self.assertEqual((date(2026, 10, 19), D("3.30")), hy[-1])

    def test_finra_ratio(self):
        rows = parse_finra(load("finra_short.txt"))
        # A: 388242.313377 -> 388242, 732703.386995 -> 732703; ratio 388242 / 732703 = 0.5299 (4 dp)
        self.assertEqual((388242, 732703), rows["A"])
        self.assertEqual(0.5299, round(rows["A"][0] / rows["A"][1], 4))
        self.assertEqual((12345678, 30933066), rows["AAPL"])               # 12345678.5 rounds half-even to 12345678
        self.assertEqual((120000, 400000), rows["BRK.B"])
        for bad in ("BAD", "OVER", "ZERO"):                                  # non-numeric; short > 1.05 x total; total 0
            self.assertNotIn(bad, rows)
        self.assertEqual({"A", "SPY"}, set(parse_finra(load("finra_short.txt"), wanted=["A", "SPY"])))

    def test_frankfurter(self):
        fx = parse_frankfurter(load("frankfurter.json"), 1792526500)
        self.assertEqual(date(2026, 10, 20), fx.as_of)
        self.assertEqual(D("0.89166"), fx.per_usd["EUR"])
        self.assertEqual(D("158.1"), fx.per_usd["JPY"])
        self.assertEqual(29, len(fx.per_usd))

    def test_chart_error_is_a_404(self):
        with self.assertRaises(FetchError) as cm:
            chart_result({"chart": {"result": None, "error": {"code": "Not Found", "description": "No data found, symbol may be delisted"}}})
        self.assertEqual(404, cm.exception.status)
        self.assertFalse(cm.exception.retryable)


# ===== UA policy, breaker, limiter, retries ===========================================================================================
class _Getter:
    """A stub for ``http_get``: records (url, ua) and answers from a script of bytes / exceptions."""

    def __init__(self, script=None, default=None):
        self.calls = []
        self.script = list(script or [])
        self.default = default

    def __call__(self, url, ua, timeout, accept):
        self.calls.append((url, ua, timeout, accept))
        item = self.script.pop(0) if self.script else self.default
        if isinstance(item, Exception):
            raise item
        if item is None:
            raise FetchError("HTTP 404", status=404, body=b'{"chart":{"result":null,"error":{"code":"Not Found","description":"no data"}}}')
        return item


class HttpPolicyTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock("2026-10-20T14:00:00-04:00")
        self.sleeps = []
        self.health = ProviderHealth(lambda: self.clock.epoch(), day=lambda: self.clock.today().isoformat())

    def _sleep(self, s):
        self.sleeps.append(s)
        self.clock.advance(s)

    def test_ua_for_each_host(self):
        self.assertEqual(BROWSER_UA, ua_for("query1.finance.yahoo.com"))
        self.assertEqual(BROWSER_UA, ua_for("https://query2.finance.yahoo.com/v8/finance/chart/SPY?range=1d"))
        self.assertEqual(GOV_UA, ua_for("home.treasury.gov"))
        self.assertEqual(GOV_UA, ua_for("cdn.finra.org"))
        self.assertEqual(GOV_UA, ua_for("fred.stlouisfed.org"))
        self.assertEqual(TERMINAL_UA, ua_for("cdn.cboe.com"))
        self.assertEqual(TERMINAL_UA, ua_for("api.frankfurter.app"))
        self.assertEqual(TERMINAL_UA, ua_for("example.com"))

    def test_each_provider_sends_its_policy_ua(self):
        g = _Getter(default=load("yahoo_spy_1m.json"))
        YahooChart(self.clock, self.health, getter=g, sleep=self._sleep).intraday("SPY")
        self.assertIn("/v8/finance/chart/SPY?range=1d&interval=1m", g.calls[-1][0])
        self.assertEqual(BROWSER_UA, g.calls[-1][1])
        g = _Getter(default=load("cboe_aapl_trimmed.json"))
        snap = CboeChain(self.clock, self.health, getter=g, sleep=self._sleep).chain("AAPL", "AAPL")
        self.assertEqual("https://cdn.cboe.com/api/global/delayed_quotes/options/AAPL.json", g.calls[-1][0])
        self.assertEqual(TERMINAL_UA, g.calls[-1][1])
        self.assertEqual(9, snap.n_kept())
        g = _Getter(default=load("treasury_curve.xml"))
        curve = TreasuryCurve(self.clock, self.health, getter=g, sleep=self._sleep).latest()
        self.assertIn("field_tdr_date_value_month=202610", g.calls[-1][0])
        self.assertEqual(GOV_UA, g.calls[-1][1])
        self.assertEqual(date(2026, 10, 19), curve.as_of)
        g = _Getter(default=load("fred_sofr.csv"))
        obs = FredSeries(self.clock, self.health, getter=g, sleep=self._sleep).latest("SOFR", 60)
        self.assertIn("fredgraph.csv?id=SOFR&cosd=2026-08-21", g.calls[-1][0])      # 2026-10-20 - 60 days = 2026-08-21
        self.assertEqual(GOV_UA, g.calls[-1][1])
        self.assertEqual(7, len(obs))
        g = _Getter(default=load("frankfurter.json"))
        fx = Frankfurter(self.clock, self.health, getter=g, sleep=self._sleep).latest()
        self.assertEqual(TERMINAL_UA, g.calls[-1][1])
        self.assertEqual(D("0.74925"), fx.per_usd["GBP"])
        g = _Getter(default=load("finra_short.txt"))
        rows = FinraShortVolume(self.clock, self.health, getter=g, sleep=self._sleep).daily(date(2026, 10, 19), ["A"])
        self.assertIn("CNMSshvol20261019.txt", g.calls[-1][0])
        self.assertEqual(GOV_UA, g.calls[-1][1])
        self.assertEqual({"A": (388242, 732703)}, rows)
        self.assertTrue(all(self.health.get(p)["ok"] for p in ("yahoo", "cboe", "treasury", "fred", "frankfurter", "finra")))

    def test_circuit_breaker_opens_after_5_failures_and_closes_on_success(self):
        cb = CircuitBreaker(lambda: self.clock.epoch())
        t0 = self.clock.epoch()
        for _ in range(4):
            cb.record("yahoo", False, 503)
        self.assertTrue(cb.allow("yahoo"))
        cb.record("yahoo", False, None)                       # the 5th consecutive failure (a network error) opens it
        self.assertFalse(cb.allow("yahoo"))
        self.assertEqual(t0 + 120, cb.down_until("yahoo"))
        self.clock.advance(120)
        self.assertTrue(cb.allow("yahoo"))
        cb.record("yahoo", False, 503)                        # the 6th: still open, the penalty doubles 120 -> 240
        self.assertEqual(self.clock.epoch() + 240, cb.down_until("yahoo"))
        cb.record("yahoo", True, 200)
        self.assertTrue(cb.allow("yahoo"))
        self.assertEqual(0, cb.consecutive_failures("yahoo"))
        cb.record("cboe", False, 429)                         # 429 -> 60 s
        self.assertEqual(self.clock.epoch() + 60, cb.down_until("cboe"))
        cb.record("fred", False, 404)                         # an unknown symbol is not a provider failure
        self.assertTrue(cb.allow("fred"))
        self.assertEqual(0, cb.consecutive_failures("fred"))

    def test_yahoo_retries_with_backoff_then_fails_over_hosts(self):
        g = _Getter(script=[FetchError("HTTP 503", status=503), FetchError("HTTP 503", status=503), load("yahoo_spy_1m.json")])
        iq = YahooChart(self.clock, self.health, getter=g, sleep=self._sleep).intraday("SPY")
        self.assertEqual(D("769.32"), iq.last)
        self.assertEqual([1.0, 2.0], self.sleeps)                             # backoff 1 s then 2 s before the third attempt
        self.assertEqual(3, len(g.calls))
        self.assertTrue(all("query1" in c[0] for c in g.calls))
        self.sleeps.clear()
        g = _Getter(default=FetchError("HTTP 502", status=502))
        with self.assertRaises(FetchError):
            YahooChart(self.clock, self.health, getter=g, sleep=self._sleep).intraday("SPY")
        self.assertEqual(6, len(g.calls))                                     # 3 attempts on query1, 3 on query2
        self.assertTrue(any("query2" in c[0] for c in g.calls))
        self.assertEqual([1.0, 2.0, 1.0, 2.0], self.sleeps)
        self.assertEqual(6, self.health.get("yahoo")["consecutive_errors"])
        self.assertFalse(self.health.allow("yahoo"))                           # opened after the 5th failure

    def test_yahoo_404_is_final_and_names_the_reason(self):
        g = _Getter(default=None)
        with self.assertRaises(FetchError) as cm:
            YahooChart(self.clock, self.health, getter=g, sleep=self._sleep).intraday("ZZZZ")
        self.assertEqual(404, cm.exception.status)
        self.assertIn("Not Found", str(cm.exception))
        self.assertEqual(1, len(g.calls))
        self.assertTrue(self.health.get("yahoo")["ok"])

    def test_cboe_403_sets_refused_ua(self):
        g = _Getter(default=FetchError("HTTP 403", status=403))
        with self.assertRaises(FetchError):
            CboeChain(self.clock, self.health, getter=g, sleep=self._sleep).chain("AAPL", "AAPL")
        self.assertTrue(self.health.refused_ua("cboe"))
        self.assertTrue(self.health.snapshot()["cboe"]["refused_ua"])
        self.assertFalse(self.health.snapshot()["yahoo"]["refused_ua"])

    def test_rate_limiter_token_bucket(self):
        t = [0.0]
        waits = []

        def sleep(s):
            waits.append(round(s, 3))
            t[0] += s
        rl = RateLimiter(2.0, sleep=sleep, now=lambda: t[0])
        rl.wait()
        rl.wait()                                                             # the bucket holds 2 tokens at 2 rps
        self.assertEqual([], waits)
        rl.wait()                                                             # the third waits for one token: 1 / 2 rps = 0.5 s
        self.assertEqual([0.5], waits)


# ===== cache, worker, chains, slots, persistence =======================================================================================
class CacheWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-quotes-")
        self.rt = make_runtime(self.tmp)                                       # 2026-10-20 14:00 ET: US_EQUITY open
        rt = self.rt
        register_all(rt.bus)
        self.fp = FakeProvider(rt.clock)
        self.providers = ProviderSet.offline_set(self.fp, rt.clock)
        self.cache = QuoteCache(rt.db, rt.instruments, rt.sessions, rt.clock, rt.bus, rt.log)
        self.chains = ChainCache(rt.clock)
        self.curve, self.rates, self.fxs = CurveStore(rt.db, rt.bus, rt.clock), RateStore(rt.db, rt.bus), FxStore(rt.db, rt.bus)
        self.rec = _Recorder()
        self.worker = QuoteWorker(self.cache, self.chains, rt.instruments, self.providers, rt.sessions, rt.bus, self.rec, rt.clock, rt.log,
                                  fetch_threads=0, db=rt.db, services=rt, curve=self.curve, rates=self.rates, fxstore=self.fxs, cal=rt.cal)
        self.now = rt.clock.epoch()

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _set(self, sym, last, age=120, **kw):
        qt = self.rt.clock.epoch() - age
        self.fp.set(sym, last=last, quote_time=qt, bar=(qt - qt % 60 - 60, 61900), **kw)

    def test_schedule_order_15_beats_held_30_and_closed_300(self):
        rt = self.rt
        self._set("AAPL", "339.50")
        self._set("SPY", "769.32")
        self.assertTrue(buy(rt, US_EXCHANGE, "AAPL", 100, "339.50").ok)        # AAPL becomes HELD
        self.cache.pin(["SPY"], "ORDER")                                        # SPY as if a working order existed
        rep = self.worker.tick()
        self.assertEqual({"AAPL", "SPY"}, set(rep["fetched"]))
        self.assertEqual(2, self.fp.calls["intraday"])
        self.assertIn("AAPL", self.cache.pinned("HELD"))                        # found by the database scan
        self.assertEqual(self.now + 30, self.cache.next_due("AAPL"))            # HELD 30 s
        self.assertEqual(self.now + 15, self.cache.next_due("SPY"))             # ORDER 15 s
        self.assertEqual({"AAPL", "SPY"}, set(self.rec.batches[0]))             # the matcher hook saw both changes
        rec = self.cache.get("AAPL")
        self.assertEqual(("delayed", D("339.5"), "yahoo", "direct"), (rec.quality, rec.last, rec.provider, rec.method))
        self.assertTrue(is_executable(rec, rt.sessions.state("US_EQUITY"), rt.clock.epoch())[0])
        rt.clock.advance(15)
        self.worker.tick()
        self.assertEqual(3, self.fp.calls["intraday"])                          # only SPY was due
        rt.clock.advance(15)
        self.worker.tick()
        self.assertEqual(5, self.fp.calls["intraday"])                          # both due at t+30
        self.assertEqual(1, len(self.rec.batches))                              # unchanged prints: no further matcher calls
        rt.clock.set("2026-10-20T20:00:00-04:00")                              # after the close
        self.worker.tick()
        now = rt.clock.epoch()
        self.assertEqual(now + 300, self.cache.next_due("AAPL"))                # CLOSED 300 s
        self.assertEqual(now + 300, self.cache.next_due("SPY"))
        self.assertEqual("close", self.cache.get("AAPL").quality)

    def test_chain_single_flight_shares_one_fetch(self):
        rt = self.rt
        self.fp.set_chain("AAPL", payload=json.loads(load("cboe_aapl_trimmed.json")), underlying_id="AAPL")
        under = rt.instruments.get("AAPL")
        snap0 = self.fp.chain("AAPL")
        with rt.db.writer() as tx:
            rt.instruments.ensure_option(tx, "AAPL261120C00340000", under, snap0.by_occ("AAPL261120C00340000"))
        rt.instruments.load()
        self.fp.calls.clear()
        f1 = self.worker.request_chain("AAPL")
        f2 = self.worker.request_chain("AAPL")
        self.assertIs(f1, f2)                                                   # one in-flight future for both requesters
        self.assertFalse(f1.done())
        rep = self.worker.tick()
        self.assertEqual(1, self.fp.calls["chain"])
        self.assertTrue(rep["chain"]["ok"])
        self.assertEqual(9, rep["chain"]["contracts"])
        self.assertIs(f1.result(timeout=0), self.chains.get("AAPL"))
        f3 = self.chains.request("AAPL")
        self.assertTrue(f3.done())                                              # fresh (< 90 s): answered at once, no new fetch
        self.worker.tick()
        self.assertEqual(1, self.fp.calls["chain"])
        rec = self.cache.get("AAPL261120C00340000")                              # the materialised option got its chain record
        self.assertEqual(("chain", "cboe", D("8.725"), D("8.6"), D("8.85"), 60, 44), (rec.method, rec.provider, rec.mid, rec.bid, rec.ask, rec.bid_size, rec.ask_size))
        self.assertEqual(T_CLOSE - 793, rec.quote_time)                         # the underlying's last trade time, not the contract's
        self.assertAlmostEqual(0.258, rec.inputs["iv"], places=6)
        self.assertEqual(1, rep["chain"]["records"])
        rt.clock.advance(91)
        self.chains.keep_on_screen("AAPL")
        self.worker.tick()                                                      # on screen + older than CHAIN 90 s -> refetched
        self.assertEqual(2, self.fp.calls["chain"])

    def test_refresher_slots_run_at_startup_and_on_their_intervals(self):
        rt = self.rt
        self._set("AAPL", "339.50")
        self.assertTrue(buy(rt, US_EXCHANGE, "AAPL", 100, "339.50").ok)
        self.fp.set_rate("SOFR", [("2026-10-16", "3.88"), ("2026-10-19", "3.87")])
        self.fp.set_curve({"2Y": "4.00", "10Y": "5.00"}, "2026-10-19")
        self.fp.set_fx({"EUR": "0.89166", "JPY": "158.1"}, "2026-10-20")
        self.fp.set_events("AAPL", dividends=[("2026-11-09", "0.26")], splits=[("2026-11-16", "4:1")])
        rep = self.worker.tick()
        self.assertEqual({"curve", "fred", "finra", "frankfurter", "dividends"}, set(rep["slots"]))   # startup; persist waits 60 s
        self.assertEqual(1, self.fp.calls["curve"])
        self.assertEqual(len(FRED_SERIES), self.fp.calls["fred"])
        self.assertEqual(1, self.fp.calls["events"])                            # one held equity: AAPL
        self.assertEqual((date(2026, 10, 19), D("3.87")), self.rates.sofr())
        self.assertEqual(date(2026, 10, 19), self.curve.as_of())
        # 1 / 0.89166: 0.89166 × 1.1215 = 0.99999669; remainder 0.00000331 / 0.89166 = 0.00000371 -> 1.12150371
        self.assertEqual((D("1.12150371"), date(2026, 10, 20)), self.fxs.usd_per("EUR")[:2])
        with rt.db.reader() as conn:
            self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM curve_points WHERE series='UST_PAR' AND as_of='2026-10-19'").fetchone()[0])
            self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM curve_points WHERE series='SOFR' AND tenor='OBS'").fetchone()[0])
            div = conn.execute("SELECT instrument_id, ex_date, amount, pay_date, source FROM dividends").fetchall()
            self.assertEqual([("AAPL", "2026-11-09", "0.26", None, "Yahoo chart events")], [tuple(r) for r in div])
            att = conn.execute("SELECT severity, code, account_id FROM attention WHERE code='SPLIT_MANUAL_REVIEW'").fetchall()
            self.assertEqual([("WARN", "SPLIT_MANUAL_REVIEW", US_EXCHANGE)], [tuple(r) for r in att])
        slots = self.worker.refresher.stats()
        self.assertEqual(self.now + 3600, slots["finra"]["retry_at"])          # no FINRA file set -> "retry" -> again in an hour
        rt.clock.advance(100)
        self.worker.tick()
        self.assertEqual(len(FRED_SERIES), self.fp.calls["fred"])              # not due again after 100 s
        rt.clock.advance(21500)                                                 # t + 21600 = the 6 h FRED interval
        rep = self.worker.tick()
        self.assertIn("fred", rep["slots"])
        self.assertEqual(2 * len(FRED_SERIES), self.fp.calls["fred"])
        self.assertIn("persist", rep["slots"])
        with rt.db.reader() as conn:
            self.assertGreaterEqual(conn.execute("SELECT COUNT(*) FROM quote_cache").fetchone()[0], 1)
            # one row per known provider: the six of Build 1, plus the Build 2 sources (quotes.http.PROVIDERS) and any product
            # refresher this worker runs (C14 register_refresher records its outcome under its own name)
            from finsim.terminal.quotes.http import PROVIDERS as _PROVIDERS
            names = {r[0] for r in conn.execute("SELECT provider FROM provider_health").fetchall()}
            self.assertTrue({"yahoo", "cboe", "treasury", "fred", "finra", "frankfurter"} <= names, names)
            self.assertTrue(names <= set(_PROVIDERS) | set(self.worker.refreshers()), names)

    def test_persist_and_load_round_trip_returns_close_or_stale_never_delayed(self):
        rt = self.rt
        self._set("AAPL", "339.50")
        self.cache.pin(["AAPL"], "HELD")
        self.worker.tick()
        self.assertEqual("delayed", self.cache.get("AAPL").quality)
        self.assertEqual(1, self.cache.persist())
        fresh = QuoteCache(rt.db, rt.instruments, rt.sessions, rt.clock, rt.bus, rt.log)
        self.assertEqual(1, fresh.load_persisted())
        rec = fresh.get("AAPL")
        self.assertEqual(D("339.5"), rec.last)                                  # the last valid mark survives the restart
        self.assertEqual("stale", rec.quality)                                  # session open, not yet refetched: stale, never delayed
        self.assertTrue(fresh.is_restored("AAPL"))
        rt.clock.set("2026-10-20T20:00:00-04:00")
        self.assertEqual("close", fresh.get("AAPL").quality)
        rt.clock.set("2026-10-20T14:05:00-04:00")
        fresh.put(record_from_intraday(rt.instruments.get("AAPL"), self.fp.intraday("AAPL")))   # the first live fetch clears the flag
        self.assertEqual("delayed", fresh.get("AAPL").quality)

    def test_unknown_symbol_is_unavailable_and_retried_hourly(self):
        rt = self.rt
        self.cache.pin(["KO"], "HELD")                                          # seeded, but the fake has no quote -> 404
        rep = self.worker.tick()
        self.assertEqual([("KO", "KO: not found")], rep["errors"])
        rec = self.cache.get("KO")
        self.assertEqual(("unavailable", "KO: not found"), (rec.quality, rec.error))
        self.assertEqual((False, "NO_QUOTE"), is_executable(rec, rt.sessions.state("US_EQUITY"), rt.clock.epoch()))
        rt.clock.advance(60)
        self.assertEqual([], self.cache.due())
        rt.clock.advance(3600)
        self.assertEqual([("KO", "HELD")], self.cache.due())
        self.assertTrue(self.providers.health.get("yahoo")["ok"])              # a 404 is not a provider failure

    def test_subscription_expires_after_ttl(self):
        self._set("SPY", "769.32")
        self.cache.subscribe(["SPY"], ttl_s=90, priority="WATCH")
        self.assertEqual([("SPY", "WATCH")], self.cache.due())
        self.assertEqual(self.now + 60, self.cache.next_due("SPY"))              # WATCH 60 s
        self.rt.clock.advance(91)
        self.assertEqual([], self.cache.due())                                  # the UI stopped polling: dropped
        self.assertEqual([], self.cache.pinned())

    def test_provider_health_update_opens_and_resolves_one_attention(self):
        rt = self.rt
        bad = {"last_ok_ts": None, "last_err_ts": self.now - 30, "last_error": "HTTP 503", "consecutive_errors": 5, "requests_today": 9, "day": "2026-10-20",
               "down_until_ts": self.now + 90, "down": True, "refused_ua": False}
        self.assertTrue(execute(rt, "provider_health.update", {"providers": {"yahoo": bad}}, "health:test-1", actor="worker").ok)
        self.assertTrue(execute(rt, "provider_health.update", {"providers": {"yahoo": bad}}, "health:test-2", actor="worker").ok)
        with rt.db.reader() as conn:
            self.assertEqual(5, conn.execute("SELECT consecutive_errors FROM provider_health WHERE provider='yahoo'").fetchone()[0])
            rows = conn.execute("SELECT severity, code, ref_id, resolved_ts FROM attention WHERE source='provider'").fetchall()
        self.assertEqual([("WARN", "PROVIDER_DOWN", "yahoo", None)], [tuple(r) for r in rows])   # one open row, not two
        good = dict(bad, last_ok_ts=self.now, consecutive_errors=0, down=False, down_until_ts=None)
        self.assertTrue(execute(rt, "provider_health.update", {"providers": {"yahoo": good}}, "health:test-3", actor="worker").ok)
        with rt.db.reader() as conn:
            self.assertIsNotNone(conn.execute("SELECT resolved_ts FROM attention WHERE source='provider'").fetchone()[0])

    def test_resolve_unknown_ticker_through_instruments_upsert(self):
        rt = self.rt
        self.fp.set_meta("ZZTEST", {"symbol": "ZZTEST", "currency": "USD", "exchangeName": "NMS", "fullExchangeName": "NasdaqGS", "instrumentType": "EQUITY",
                                    "longName": "ZZ Test Corp", "exchangeTimezoneName": "America/New_York"})
        self.fp.set_meta("VOD.L", {"symbol": "VOD.L", "currency": "GBp", "exchangeName": "LSE", "instrumentType": "EQUITY", "longName": "Vodafone"})
        self.assertEqual({"status": "resolving"}, self.worker.resolve("ZZTEST"))
        self.worker.resolve("VOD.L")
        self.worker.resolve("NOPE")
        self.worker.tick()
        self.worker.tick()
        self.worker.tick()
        inst = rt.instruments.get("ZZTEST")
        self.assertEqual(("EQUITY", "FULL", "US_EQUITY", "ZZ Test Corp"), (inst.kind, inst.support, inst.session_group, inst.name))
        self.assertEqual("FULL", rt.instruments.get("VOD.L").support)   # Build 2 P06 (OA-12): a London listing on a known venue in GBP trades
        self.assertEqual("error", self.worker.resolve_status("NOPE")["status"])
        self.assertEqual(404, self.worker.resolve_status("NOPE")["http_status"])


# ===== the §8.2 freshness table (§19.12) ==============================================================================================
class FreshnessTableTests(unittest.TestCase):
    OPEN = SessionState("US_EQUITY", True, "OPEN", epoch_of(TODAY, "09:30"), epoch_of(TODAY, "16:00"), None, "2026-10-20", False, "")
    CLOSED = SessionState("US_EQUITY", False, "CLOSED", None, None, epoch_of(date(2026, 10, 21), "09:30"), "2026-10-21", False, "")

    _AGE = object()      # sentinel: quote_time derived from quote_age unless given explicitly (None = no provider time)

    @classmethod
    def rec(cls, now, quote_age=100, fetch_age=10, error=None, method="direct", last="339.50", mid=None, bid=None, ask=None, inputs=None, provider_session=None,
            quote_time=_AGE):
        qt = (now - quote_age) if quote_time is cls._AGE else quote_time
        return QuoteRecord("AAPL", "yahoo", "test", method, last=None if last is None else D(last), bid=None if bid is None else D(bid), ask=None if ask is None else D(ask),
                           mid=None if mid is None else D(mid), quote_time=qt, fetched_at=now - fetch_age,
                           delay_s=900 if method in ("direct", "chain") else 0, error=error, inputs=inputs, provider_session=provider_session)

    def test_table(self):
        now = epoch_of(TODAY, "14:00")
        early = epoch_of(TODAY, "09:35")
        cases = [
            # (description, record, session, now, expected quality, expected (ok, reason))
            ("fresh delayed print", self.rec(now), self.OPEN, now, "delayed", (True, "OK")),
            ("print older than delay 900 + grace 300", self.rec(now, quote_age=1201), self.OPEN, now, "stale", (False, "STALE")),
            ("print exactly at the limit", self.rec(now, quote_age=1200), self.OPEN, now, "delayed", (True, "OK")),
            ("nothing fetched for > 300 s", self.rec(now, fetch_age=301), self.OPEN, now, "stale", (False, "STALE")),
            ("failing provider ages the record after 60 s", self.rec(now, error="HTTP 503", fetch_age=61), self.OPEN, now, "stale", (False, "STALE")),
            ("a fresh error keeps the print for 60 s", self.rec(now, error="HTTP 503", fetch_age=30), self.OPEN, now, "delayed", (True, "OK")),
            ("no provider time", self.rec(now, quote_time=None), self.OPEN, now, "stale", (False, "STALE")),
            ("session closed", self.rec(now), self.CLOSED, now, "close", (False, "SESSION_CLOSED")),
            ("model with sound inputs", self.rec(now, method="model", inputs={"qualities": ["delayed", "close"]}), self.OPEN, now, "computed", (False, "COMPUTED_NOT_EXECUTABLE")),
            ("model with a stale input", self.rec(now, method="model", inputs={"qualities": ["stale"]}), self.OPEN, now, "stale", (False, "STALE")),
            ("curve mark when the session is closed is still computed", self.rec(now, method="curve", inputs={"qualities": ["close"]}), self.CLOSED, now, "computed", (False, "COMPUTED_NOT_EXECUTABLE")),
            ("private round mark never ages", self.rec(now, method="private_round", quote_age=10 ** 7), self.OPEN, now, "private", (False, "COMPUTED_NOT_EXECUTABLE")),
            ("no price at all", self.rec(now, last=None), self.OPEN, now, "unavailable", (False, "NO_QUOTE")),
            ("print from before the open", self.rec(early, quote_time=epoch_of(TODAY, "09:25")), self.OPEN, early, "delayed", (False, "QUOTE_FROM_PREVIOUS_SESSION")),
            ("provider's session ended (early close the table missed)", self.rec(now, provider_session=(epoch_of(TODAY, "09:30"), epoch_of(TODAY, "13:00"))), self.OPEN, now,
             "delayed", (False, "PROVIDER_SESSION_ENDED")),
            ("chain record without an NBBO", self.rec(now, method="chain", last="8.70", bid="0", ask="2.30", mid=None), self.OPEN, now, "delayed", (False, "NO_NBBO")),
            ("chain record with an NBBO", self.rec(now, method="chain", last="8.70", bid="8.60", ask="8.85", mid="8.725"), self.OPEN, now, "delayed", (True, "OK")),
        ]
        for desc, rec, session, at, quality, exe in cases:
            with self.subTest(desc):
                self.assertEqual(quality, freshness(rec, session, at))
                self.assertEqual(exe, is_executable(rec, session, at))

    def test_bar_rule_helpers(self):
        # the completed-bar rule lives in the parser; a Bar1m round-trips through the record's dict form
        rec = self.rec(1000)
        rec.bar = Bar1m(900, D("1"), D("2"), D("0.5"), D("1.5"), 10)
        back = QuoteRecord.from_dict(json.loads(json.dumps(rec.to_dict())))
        self.assertEqual((900, D("1.5"), 10), (back.bar.t, back.bar.c, back.bar.v))
        self.assertEqual(rec.last, back.last)


if __name__ == "__main__":
    unittest.main()
