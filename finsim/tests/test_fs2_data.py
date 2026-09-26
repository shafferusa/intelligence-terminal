"""FinSim2 data layer: universe, store, Yahoo / FRED / SEC parsing, synthetic bond indices, refresh.

Network is never touched: every downloader is patched with small fixtures.
"""
import datetime as dt
import json
import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock

from finsim2.data import FetchError, GOV_UA, BROWSER_UA, num
from finsim2.data import fred, refresh, sec, store as store_mod, universe, yahoo
from finsim2.data.store import Store


class TmpStoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fs2data")
        self.store = Store(os.path.join(self.tmp, "research.db"))

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


def bar(d, close, adj=None, vol=1000):
    return {"date": d, "open": close, "high": close, "low": close, "close": close,
            "adj_close": close if adj is None else adj, "volume": vol}


# ============================================================================================ universe
class TestUniverse(unittest.TestCase):
    def test_shape(self):
        ids = [a["id"] for a in universe.UNIVERSE]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(ids), 130)
        keys = {"id", "name", "asset_class", "sector", "country", "currency", "yahoo", "cik", "duration",
                "convexity", "fred", "benchmark"}
        for a in universe.UNIVERSE:
            self.assertTrue(keys <= set(a), a["id"])
            self.assertIn(a["asset_class"], universe.ASSET_CLASSES)
            if a["asset_class"] == "EQUITY":
                self.assertIsInstance(a["cik"], int)
            if a["asset_class"] in ("TREASURY", "CORP_BOND"):
                self.assertIsNone(a["yahoo"])
                self.assertIn(a["fred"], universe.FRED_SERIES)
                self.assertIsNotNone(a["duration"])

    def test_specific_entries(self):
        self.assertEqual(universe.by_id("SPX")["yahoo"], "^GSPC")
        self.assertEqual(universe.by_id("N225")["currency"], "JPY")
        self.assertEqual(universe.by_id("USDJPY")["currency"], "JPY")
        self.assertEqual(universe.by_id("EURUSD")["currency"], "USD")
        self.assertEqual(universe.by_id("DXY")["yahoo"], "DX-Y.NYB")
        self.assertEqual(universe.by_id("TLT")["duration"], 16.5)
        self.assertEqual(universe.by_id("TLT")["sector"], "Fixed Income")
        self.assertEqual(universe.by_id("UST10Y")["fred"], "DGS10")
        self.assertEqual(universe.by_id("CORP_BAA")["asset_class"], "CORP_BOND")
        self.assertEqual(universe.by_id("GOLD")["yahoo"], "GC=F")
        self.assertEqual(universe.by_id("AAPL")["cik"], 320193)
        self.assertEqual(universe.by_id("BRK-B")["cik"], 1067983)
        self.assertIsNone(universe.by_id("NOPE"))
        a = universe.by_id("SPY")
        a["meta"]["x"] = 1  # by_id returns a copy
        self.assertNotIn("x", universe.by_id("SPY")["meta"])

    def test_constants(self):
        self.assertEqual(universe.HORIZONS, [("1D", 1), ("1W", 5), ("1M", 21), ("3M", 63), ("6M", 126),
                                             ("12M", 252), ("3Y", 756), ("5Y", 1260), ("10Y", 2520)])
        self.assertEqual(universe.BENCHMARK, "SPY")
        self.assertEqual(universe.FRED_SERIES["CPIAUCSL"]["lag_days"], 45)
        self.assertEqual(universe.FRED_SERIES["NFCI"]["freq"], "weekly")
        self.assertEqual(universe.FRED_SERIES["DGS10"]["lag_days"], 1)
        self.assertEqual(len(universe.FRED_SERIES), 56)          # 47 + the 9 new-information series (credit, term premium, real yields, foreign rates)
        for sid in ("DAAA", "AAA10Y", "THREEFYTP10", "DFII5", "DFII30", "T10YIE", "IR3TIB01EZM156N", "IR3TIB01GBM156N", "IR3TIB01JPM156N"):
            self.assertIn(sid, universe.FRED_SERIES)
        self.assertEqual(universe.FRED_SERIES["THREEFYTP10"]["lag_days"], 7)
        self.assertEqual(universe.FRED_SERIES["IR3TIB01EZM156N"]["lag_days"], 45)
        self.assertIn("TREASURY", universe.asset_classes())


# ============================================================================================ store
class TestStore(TmpStoreCase):
    def test_default_path_env(self):
        with mock.patch.dict(os.environ, {"FINSIM2_HOME": "/x/y"}, clear=False):
            os.environ.pop("FINSIM2_RESEARCH_DB", None)
            self.assertEqual(store_mod.default_path(), "/x/y/research.db")
        with mock.patch.dict(os.environ, {"FINSIM2_RESEARCH_DB": "/a/b.db"}):
            self.assertEqual(store_mod.default_path(), "/a/b.db")

    def test_assets_seeded_and_upsert(self):
        self.assertEqual(len(self.store.assets()), len(universe.UNIVERSE))
        spy = self.store.asset("SPY")
        self.assertEqual(spy["asset_class"], "ETF")
        self.assertEqual(spy["benchmark"], "SPY")
        self.assertIsInstance(spy["meta"], dict)
        self.assertEqual(len(self.store.assets("TREASURY")), 4)
        self.store.upsert_asset({"id": "ZZZ", "name": "Zed", "asset_class": "EQUITY", "yahoo": "ZZZ",
                                 "meta": {"a": 1}})
        self.store.upsert_asset({"id": "ZZZ", "name": "Zed2", "asset_class": "EQUITY", "yahoo": "ZZZ",
                                 "meta": {"b": 2}})
        z = self.store.asset("ZZZ")
        self.assertEqual(z["name"], "Zed2")
        self.assertEqual(z["meta"]["a"], 1)
        self.assertEqual(z["meta"]["b"], 2)
        self.assertIsNone(self.store.asset("nope"))
        # reopening re-seeds without clobbering user assets
        self.store.close()
        self.store = Store(os.path.join(self.tmp, "research.db"))
        self.assertEqual(self.store.asset("ZZZ")["name"], "Zed2")

    def test_prices_and_version(self):
        v0 = self.store.data_version()
        self.assertIsNone(self.store.last_price_date("SPY"))
        n = self.store.upsert_prices("SPY", [bar("2024-01-03", 101.0), bar("2024-01-02", 100.0),
                                             {"date": dt.date(2024, 1, 4), "close": 102.0, "open": None,
                                              "high": None, "low": None, "adj_close": None, "volume": None}])
        self.assertEqual(n, 3)
        v1 = self.store.data_version()
        self.assertNotEqual(v0, v1)
        rows = self.store.prices("SPY")
        self.assertEqual([r["date"] for r in rows], ["2024-01-02", "2024-01-03", "2024-01-04"])
        self.assertEqual(rows[2]["adj_close"], 102.0)  # falls back to close
        self.assertIsNone(rows[2]["volume"])
        self.assertEqual(self.store.last_price_date("SPY"), "2024-01-04")
        self.assertEqual(self.store.price_count("SPY"), 3)
        self.assertEqual(len(self.store.prices("SPY", start="2024-01-03")), 2)
        self.assertEqual(len(self.store.prices("SPY", end=dt.date(2024, 1, 3))), 2)
        # identical upsert changes nothing and keeps the version
        self.assertEqual(self.store.upsert_prices("SPY", [bar("2024-01-03", 101.0)]), 0)
        self.assertEqual(self.store.data_version(), v1)
        self.assertEqual(self.store.upsert_prices("SPY", [bar("2024-01-03", 101.5)]), 1)
        self.assertNotEqual(self.store.data_version(), v1)
        # NaN / junk become None
        self.store.upsert_prices("SPY", [{"date": "2024-01-05", "close": 103.0, "open": float("nan"),
                                          "high": "x", "low": None, "adj_close": 103.0, "volume": float("inf")}])
        r = self.store.prices("SPY", start="2024-01-05")[0]
        self.assertIsNone(r["open"])
        self.assertIsNone(r["high"])
        self.assertIsNone(r["volume"])
        self.store.replace_prices("SPY", [bar("2024-02-01", 5.0)])
        self.assertEqual(self.store.price_count("SPY"), 1)

    def test_macro(self):
        v0 = self.store.data_version()
        self.store.upsert_macro("DGS10", [("2024-01-03", 4.0), (dt.date(2024, 1, 2), 3.9)])
        self.assertEqual(self.store.macro("DGS10"), [("2024-01-02", 3.9), ("2024-01-03", 4.0)])
        self.assertEqual(self.store.macro_rows("DGS10"), [("2024-01-02", 3.9, None), ("2024-01-03", 4.0, None)])
        self.assertEqual(self.store.last_macro_date("DGS10"), "2024-01-03")
        self.assertIsNone(self.store.last_macro_date("NOPE"))
        self.assertNotEqual(self.store.data_version(), v0)

    def test_fundamentals(self):
        rows = [{"concept": "eps", "period_end": "2023-12-31", "filed": "2024-02-01", "value": 1.5, "form": "10-K",
                 "fp": "FY"},
                {"concept": "revenue", "period_end": "2023-12-31", "filed": "2024-02-01", "value": 1e9,
                 "form": "10-K", "fp": "FY"}]
        v0 = self.store.data_version()
        self.assertEqual(self.store.upsert_fundamentals("AAPL", rows), 2)
        self.assertNotEqual(self.store.data_version(), v0)
        self.assertEqual(len(self.store.fundamentals("AAPL")), 2)
        f = self.store.fundamentals("AAPL", "eps")
        self.assertEqual(f, [{"concept": "eps", "period_end": "2023-12-31", "filed": "2024-02-01", "value": 1.5,
                              "form": "10-K", "fp": "FY"}])
        self.assertEqual(self.store.upsert_fundamentals("AAPL", rows), 0)

    def test_kv_and_fetch_log(self):
        self.assertIsNone(self.store.kv_get("a"))
        self.assertEqual(self.store.kv_get("a", 7), 7)
        self.store.kv_set("res:SPY:1", {"x": [1, 2, None], "d": dt.date(2024, 1, 1)})
        self.store.kv_set("res:QQQ:1", 3)
        self.store.kv_set("other", "s")
        self.assertEqual(self.store.kv_get("res:SPY:1"), {"x": [1, 2, None], "d": "2024-01-01"})
        self.assertEqual(self.store.kv_keys("res:"), ["res:QQQ:1", "res:SPY:1"])
        self.assertEqual(len(self.store.kv_keys("")), 3)
        self.store.kv_delete("other")
        self.assertIsNone(self.store.kv_get("other"))
        self.store.log_fetch("yahoo", "SPY", "ok", "10 rows")
        self.store.log_fetch("yahoo", "SPY", "error", "boom")
        log = self.store.fetch_log()
        self.assertEqual(log[0]["status"], "error")
        self.assertEqual(len(self.store.fetch_log(limit=1)), 1)
        self.assertIsNotNone(self.store.last_fetch("yahoo", "SPY"))
        self.assertIsNone(self.store.last_fetch("sec", "SPY"))

    def test_predictions_runs_backtests(self):
        pid = self.store.add_prediction({"asset_id": "SPY", "horizon": "1M", "model": "ridge", "model_version": "1",
                                         "made_on": "2024-01-02", "target_date": "2024-02-01", "predicted": 0.01,
                                         "error_band": 0.03, "confidence": 0.6, "score": 55,
                                         "detail": {"features": ["a"]}})
        self.store.add_prediction({"asset_id": "QQQ", "horizon": "1W", "made_on": "2024-01-02"})
        self.assertEqual(len(self.store.predictions()), 2)
        self.assertEqual(len(self.store.predictions(asset_id="SPY", horizon="1M")), 1)
        self.store.score_prediction(pid, 0.02, 0.01, dt.date(2024, 2, 1))
        un = self.store.predictions(unscored_only=True)
        self.assertEqual([p["asset_id"] for p in un], ["QQQ"])
        p = self.store.predictions(asset_id="SPY")[0]
        self.assertEqual(p["realized"], 0.02)
        self.assertEqual(p["scored_on"], "2024-02-01")
        self.assertEqual(p["detail"], {"features": ["a"]})
        rid = self.store.add_model_run({"level": "asset", "key": "SPY", "horizon": "1M", "model": "gbm",
                                        "features": ["f1"], "params": {"depth": 3}, "metrics": {"ic": 0.1}})
        self.store.add_model_run({"level": "asset", "key": "QQQ", "horizon": "1M", "model": "gbm"})
        runs = self.store.model_runs(key="SPY")
        self.assertEqual(runs[0]["id"], rid)
        self.assertEqual(runs[0]["params"], {"depth": 3})
        self.assertIsNotNone(runs[0]["created_at"])
        self.assertEqual(len(self.store.model_runs(horizon="1M")), 2)
        bid = self.store.add_backtest({"signal": "mom"}, {"sharpe": 0.5})
        b = self.store.backtests()
        self.assertEqual(b[0]["id"], bid)
        self.assertEqual(b[0]["result"], {"sharpe": 0.5})

    def test_portfolio_and_watchlist(self):
        self.store.create_portfolio("main", "Main book")
        self.store.create_portfolio("eur", "Euro", base_currency="EUR")
        self.assertEqual({p["id"]: p["base_currency"] for p in self.store.portfolios()}, {"main": "USD", "eur": "EUR"})
        t1 = self.store.add_transaction({"portfolio_id": "main", "date": "2024-01-02", "kind": "BUY",
                                         "asset_id": "SPY", "quantity": 10, "price": 470.5, "currency": "USD"})
        self.store.add_transaction({"portfolio_id": "main", "date": "2024-01-01", "kind": "DEPOSIT",
                                    "quantity": 10000, "price": 1})
        txs = self.store.transactions("main")
        self.assertEqual([t["kind"] for t in txs], ["DEPOSIT", "BUY"])
        self.assertEqual(txs[1]["fee"], 0.0)
        self.store.delete_transaction(t1)
        self.assertEqual(len(self.store.transactions("main")), 1)
        self.store.save_snapshot("main", "2024-01-02", 10000.0, {"cash": 10000})
        self.store.save_snapshot("main", "2024-01-02", 10001.0, {"cash": 10001})
        snaps = self.store.snapshots("main")
        self.assertEqual(len(snaps), 1)
        self.assertEqual(snaps[0]["nav"], 10001.0)
        self.assertEqual(snaps[0]["detail"], {"cash": 10001})
        self.store.watch("NVDA", "AI")
        self.store.watch("AAPL")
        self.store.watch("NVDA", "AI2")
        wl = {w["asset_id"]: w["note"] for w in self.store.watchlist()}
        self.assertEqual(wl, {"NVDA": "AI2", "AAPL": ""})
        self.store.unwatch("NVDA")
        self.assertEqual([w["asset_id"] for w in self.store.watchlist()], ["AAPL"])

    def test_threads(self):
        errors = []

        def work(i):
            try:
                self.store.upsert_prices("QQQ", [bar(f"2024-03-{i + 1:02d}", 100.0 + i)])
                self.store.kv_set(f"t{i}", i)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
        ts = [threading.Thread(target=work, args=(i,)) for i in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(errors, [])
        self.assertEqual(self.store.price_count("QQQ"), 8)

    def test_memory_store_shared_between_threads(self):
        s = Store(":memory:")
        try:
            s.upsert_macro("X", [("2024-01-01", 1.0)])
            out = []
            t = threading.Thread(target=lambda: out.append(s.macro("X")))
            t.start()
            t.join()
            self.assertEqual(out[0], [("2024-01-01", 1.0)])
        finally:
            s.close()


# ============================================================================================ yahoo
def chart_payload(ts, closes, adj=None, gmtoffset=-18000, meta_extra=None):
    n = len(ts)
    quote = {"open": [c for c in closes], "high": closes, "low": closes, "close": closes,
             "volume": [100 * (i + 1) for i in range(n)]}
    ind = {"quote": [quote]}
    if adj is not None:
        ind["adjclose"] = [{"adjclose": adj}]
    meta = {"currency": "USD", "symbol": "SPY", "gmtoffset": gmtoffset, "instrumentType": "ETF",
            "longName": "SPDR S&P 500", "exchangeName": "PCX"}
    meta.update(meta_extra or {})
    return {"chart": {"result": [{"meta": meta, "timestamp": ts, "indicators": ind}], "error": None}}


class TestYahoo(unittest.TestCase):
    def test_parse_adjclose_and_gmtoffset(self):
        # 2024-01-02 14:30 UTC (09:30 New York) and 2024-01-03 14:30 UTC; a None close row is skipped
        t1 = int(dt.datetime(2024, 1, 2, 14, 30, tzinfo=dt.timezone.utc).timestamp())
        t2 = t1 + 86400
        t3 = t2 + 86400
        res = chart_payload([t1, t2, t3], [470.0, None, 472.5], adj=[460.0, None, 462.5])["chart"]["result"][0]
        meta, rows = yahoo.parse_chart(res)
        self.assertEqual(meta["currency"], "USD")
        self.assertEqual([r["date"] for r in rows], ["2024-01-02", "2024-01-04"])
        self.assertEqual(rows[0]["adj_close"], 460.0)
        self.assertEqual(rows[1]["close"], 472.5)
        self.assertEqual(rows[1]["volume"], 300.0)

    def test_gmtoffset_moves_the_date(self):
        # 2024-01-04 00:00 UTC is 09:00 on 2024-01-04 in Tokyo, but 19:00 on 2024-01-03 in New York
        t = int(dt.datetime(2024, 1, 4, 0, 0, tzinfo=dt.timezone.utc).timestamp())
        res = chart_payload([t], [33000.0], gmtoffset=32400)["chart"]["result"][0]
        self.assertEqual(yahoo.parse_chart(res)[1][0]["date"], "2024-01-04")
        res = chart_payload([t], [33000.0], gmtoffset=-18000)["chart"]["result"][0]
        self.assertEqual(yahoo.parse_chart(res)[1][0]["date"], "2024-01-03")

    def test_no_adjclose_falls_back_to_close(self):
        t = 1704205800
        res = chart_payload([t], [1.1])["chart"]["result"][0]
        rows = yahoo.parse_chart(res)[1]
        self.assertEqual(rows[0]["adj_close"], 1.1)

    def test_malformed_is_tolerated(self):
        self.assertEqual(yahoo.parse_chart({"timestamp": "x"})[1], [])
        self.assertEqual(yahoo.parse_chart(None), ({}, []))
        res = {"meta": {}, "timestamp": [1704205800, "bad", None],
               "indicators": {"quote": [{"close": ["1", 2.0, 3.0]}]}}
        rows = yahoo.parse_chart(res)[1]
        self.assertEqual(len(rows), 1)  # "1" is a string close in position 0 -> accepted as number via num()
        self.assertEqual(rows[0]["close"], 1.0)

    def test_fetch_uses_encoded_url_browser_ua_and_handles_errors(self):
        calls = []
        payload = json.dumps(chart_payload([1704205800], [4700.0])).encode()

        def fake_http(url, headers=None, timeout=30):
            calls.append((url, headers))
            return payload
        with mock.patch.object(yahoo, "http_get", fake_http):
            rows = yahoo.fetch_history("^GSPC", 100)
        self.assertEqual(len(rows), 1)
        self.assertIn("/v8/finance/chart/%5EGSPC?period1=100&", calls[0][0])
        self.assertIn("events=div%2Csplit", calls[0][0])
        self.assertTrue(calls[0][0].startswith("https://query1.finance.yahoo.com"))
        self.assertEqual(calls[0][1]["User-Agent"], BROWSER_UA)
        err = json.dumps({"chart": {"result": None, "error": {"code": "Not Found", "description": "No data"}}})
        with mock.patch.object(yahoo, "_get", return_value=err.encode()):
            with self.assertRaises(yahoo.YahooError) as cm:
                yahoo.fetch_history("NOPE")
            self.assertIn("Not Found", str(cm.exception))

    def test_retry_then_fallback_host(self):
        seq = []
        good = json.dumps(chart_payload([1704205800], [1.0])).encode()

        def fake_get(url):
            seq.append(url.split("/v8")[0])
            if "query1" in url:
                raise FetchError("HTTP 429", status=429)
            return good
        sleeps = []
        with mock.patch.object(yahoo, "_get", fake_get), mock.patch.object(yahoo, "_sleep", sleeps.append):
            rows = yahoo.fetch_history("SPY")
        self.assertEqual(len(rows), 1)
        self.assertEqual(seq.count("https://query1.finance.yahoo.com"), yahoo.MAX_TRIES_PER_HOST)
        self.assertEqual(seq[-1], "https://query2.finance.yahoo.com")
        self.assertEqual(sleeps, [1.5, 3.0])  # exponential backoff between query1 attempts

    def test_404_with_error_body(self):
        body = json.dumps({"chart": {"result": None, "error": {"code": "Not Found", "description": "delisted"}}})

        def fake_get(url):
            raise FetchError("HTTP 404", status=404, body=body.encode())
        with mock.patch.object(yahoo, "_get", fake_get):
            with self.assertRaises(yahoo.YahooError) as cm:
                yahoo.fetch_history("ZZZZ")
        self.assertIn("delisted", str(cm.exception))


# ============================================================================================ fred
class TestFred(unittest.TestCase):
    CSV = b"observation_date,DGS10\n2024-01-01,.\n2024-01-02,3.95\n2024-01-03,3.91\nbad,1\n2024-01-04,\n"

    def test_csv_parse_skips_missing(self):
        calls = []

        def fake_http(url, headers=None, timeout=60):
            calls.append((url, headers))
            return self.CSV
        with mock.patch.object(fred, "http_get", fake_http):
            rows = fred.fetch_series("DGS10")
        self.assertEqual(rows, [("2024-01-02", 3.95), ("2024-01-03", 3.91)])
        self.assertEqual(calls[0][1]["User-Agent"], GOV_UA)
        self.assertTrue(calls[0][0].startswith("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10"))

    def test_start_filters(self):
        with mock.patch.object(fred, "_get", return_value=self.CSV) as g:
            rows = fred.fetch_series("DGS10", start="2024-01-03")
        self.assertEqual(rows, [("2024-01-03", 3.91)])
        self.assertIn("cosd=2024-01-03", g.call_args[0][0])

    def test_api_fallback_only_with_key_and_key_never_in_errors(self):
        api = json.dumps({"observations": [{"date": "2024-01-02", "value": "3.95"},
                                           {"date": "2024-01-03", "value": "."}]}).encode()

        def fake_get(url):
            if "fredgraph" in url:
                raise FetchError("HTTP 503", status=503)
            self.assertIn("api_key=SECRETKEY", url)
            return api
        with mock.patch.object(fred, "_get", fake_get):
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("FRED_API_KEY", None)
                with self.assertRaises(fred.FredError):
                    fred.fetch_series("DGS10")
            with mock.patch.dict(os.environ, {"FRED_API_KEY": "SECRETKEY"}):
                self.assertEqual(fred.fetch_series("DGS10"), [("2024-01-02", 3.95)])

        def all_fail(url):
            raise FetchError("HTTP 400", status=400)
        with mock.patch.object(fred, "_get", all_fail), mock.patch.dict(os.environ, {"FRED_API_KEY": "SECRETKEY"}):
            with self.assertRaises(fred.FredError) as cm:
                fred.fetch_series("DGS10")
            self.assertNotIn("SECRETKEY", str(cm.exception))

    def test_invalid_id(self):
        with self.assertRaises(fred.FredError):
            fred.fetch_series("DGS10&x=1")

    def test_available_on(self):
        self.assertEqual(fred.available_on("CPIAUCSL", "2024-01-01"), dt.date(2024, 2, 15))
        self.assertEqual(fred.available_on("DGS10", dt.date(2024, 1, 2)), dt.date(2024, 1, 3))
        self.assertEqual(fred.available_on("NFCI", dt.datetime(2024, 1, 5, 12)), dt.date(2024, 1, 10))


# ============================================================================================ sec
def fact(start, end, val, fp, form, filed):
    d = {"end": end, "val": val, "fp": fp, "form": form, "filed": filed, "accn": "x"}
    if start:
        d["start"] = start
    return d


def companyfacts():
    ni = [
        fact("2023-01-01", "2023-03-31", 10, "Q1", "10-Q", "2023-05-01"),
        fact("2023-04-01", "2023-06-30", 20, "Q2", "10-Q", "2023-08-01"),
        fact("2023-01-01", "2023-06-30", 30, "Q2", "10-Q", "2023-08-01"),   # YTD: dropped
        fact("2023-07-01", "2023-09-30", 30, "Q3", "10-Q", "2023-11-01"),
        fact("2023-01-01", "2023-12-31", 100, "FY", "10-K", "2024-02-15"),  # Q4 = 100 - 60 = 40
        fact("2024-01-01", "2024-03-31", 50, "Q1", "10-Q", "2024-05-02"),
        fact("2023-01-01", "2023-03-31", 12, "Q1", "10-Q", "2024-05-02"),  # restated comparative
        fact("2024-01-01", "2024-03-31", 999, "Q1", "8-K", "2024-04-20"),  # wrong form: ignored
    ]
    return {"cik": 1, "entityName": "Test Co", "facts": {
        "us-gaap": {
            "NetIncomeLoss": {"units": {"USD": ni}},
            "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
                fact("2023-01-01", "2023-12-31", 1000, "FY", "10-K", "2024-02-15")]}},
            "Revenues": {"units": {"USD": [fact("2023-01-01", "2023-12-31", 1100, "FY", "10-K", "2024-02-15")]}},
            "EarningsPerShareDiluted": {"units": {"USD/shares": [
                fact("2023-10-01", "2023-12-31", 0.4, "FY", "10-K", "2024-02-15"),
                fact("2023-01-01", "2023-12-31", 1.0, "FY", "10-K", "2024-02-15")]}},
            "StockholdersEquity": {"units": {"USD": [
                fact(None, "2023-12-31", 500, "FY", "10-K", "2024-02-15"),
                fact(None, "2024-03-31", 520, "Q1", "10-Q", "2024-05-02")]}},
        },
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            fact(None, "2024-04-25", 1e9, "Q1", "10-Q", "2024-05-02")]}}},
    }}


class TestSec(unittest.TestCase):
    def test_extract(self):
        rows = sec.extract(companyfacts())
        ni = [r for r in rows if r["concept"] == "net_income"]
        self.assertEqual(len(ni), 6)  # YTD and 8-K dropped
        self.assertNotIn(("2023-06-30", 30.0), [(r["period_end"], r["value"]) for r in ni])  # YTD dropped
        self.assertEqual({r["fp"] for r in ni}, {"Q1", "Q2", "Q3", "FY"})
        fy = [r for r in ni if r["fp"] == "FY"]
        self.assertEqual([(r["period_end"], r["value"]) for r in fy], [("2023-12-31", 100.0)])
        rev = [r for r in rows if r["concept"] == "revenue"]
        self.assertEqual([r["value"] for r in rev], [1100.0])  # "Revenues" has priority
        eps = [r for r in rows if r["concept"] == "eps"]
        self.assertEqual([(r["fp"], r["value"]) for r in eps], [("FY", 1.0)])  # FY preferred over Q4 on same key
        self.assertEqual({r["concept"] for r in rows}, {"net_income", "revenue", "eps", "equity", "shares"})
        self.assertEqual(sec.extract({"facts": "junk"}), [])
        self.assertEqual(sec.extract(None), [])

    def test_ttm_point_in_time(self):
        rows = sec.extract(companyfacts())
        dates = ["2023-04-30", "2023-11-01", "2024-02-14", "2024-02-15", "2024-05-01", "2024-05-02"]
        ttm = sec.ttm_series(rows, "net_income", dates)
        self.assertIsNone(ttm["2023-04-30"])            # nothing filed yet
        self.assertIsNone(ttm["2023-11-01"])            # only three quarters known
        self.assertIsNone(ttm["2024-02-14"])            # the 10-K is filed tomorrow: no lookahead
        self.assertEqual(ttm["2024-02-15"], 100.0)      # Q1..Q3 + derived Q4 (= FY)
        self.assertEqual(ttm["2024-05-01"], 100.0)      # Q1 2024 10-Q filed 2024-05-02 must not appear yet
        # 2024-05-02: Q2'23 20 + Q3'23 30 + Q4'23 derived (100 - (12 + 20 + 30) = 38) + Q1'24 50
        self.assertEqual(ttm["2024-05-02"], 138.0)

    def test_ttm_q4_derivation_and_fy_fallback(self):
        rows = [
            {"concept": "revenue", "period_end": "2022-12-31", "filed": "2023-02-01", "value": 400, "fp": "FY"},
            {"concept": "revenue", "period_end": "2023-03-31", "filed": "2023-05-01", "value": 110, "fp": "Q1"},
        ]
        ttm = sec.ttm_series(rows, "revenue", [dt.date(2023, 3, 1), dt.date(2023, 6, 1)])
        self.assertEqual(ttm[dt.date(2023, 3, 1)], 400)   # annual value
        self.assertEqual(ttm[dt.date(2023, 6, 1)], 400)   # a lone quarter is not a TTM: stay on FY
        ttm = sec.ttm_series(rows, "revenue", ["2025-01-01"])
        self.assertIsNone(ttm["2025-01-01"])             # too stale

    def test_ttm_instant(self):
        rows = sec.extract(companyfacts())
        eq = sec.ttm_series(rows, "equity", ["2024-03-01", "2024-05-02"])
        self.assertEqual(eq, {"2024-03-01": 500.0, "2024-05-02": 520.0})
        sh = sec.ttm_series(rows, "shares", ["2024-05-01", "2024-05-02"])
        self.assertEqual(sh, {"2024-05-01": None, "2024-05-02": 1e9})

    def test_fetch_companyfacts_ua_url_and_throttle(self):
        calls = []

        def fake_http(url, headers=None, timeout=60):
            calls.append((url, headers))
            return json.dumps(companyfacts()).encode()
        with mock.patch.object(sec, "http_get", fake_http), mock.patch.object(sec.time, "sleep") as slp:
            sec._last_call[0] = 0.0
            sec.fetch_companyfacts(320193)
            sec.fetch_companyfacts(320193)
        self.assertEqual(calls[0][0], "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json")
        self.assertEqual(calls[0][1]["User-Agent"], GOV_UA)
        self.assertTrue(slp.called)  # the second call had to wait
        self.assertLessEqual(slp.call_args[0][0], sec.MIN_INTERVAL)
        with mock.patch.object(sec, "_get", return_value=b"[1,2]"):
            with self.assertRaises(sec.SecError):
                sec.fetch_companyfacts(1)


# ============================================================================================ synthetic bonds
class TestSynthetic(unittest.TestCase):
    def test_index_math(self):
        ys = [("2024-01-02", 4.0), ("2024-01-03", 4.1), ("2024-01-04", 4.1), ("2024-01-08", 4.0)]
        D, C = 8.5, 0.85
        idx = refresh.total_return_index(ys, D, C)
        self.assertEqual(idx[0], ("2024-01-02", 100.0))
        r1 = 0.04 / 252 - D * 0.001 + 0.5 * C * 0.001 ** 2
        r2 = 0.041 / 252
        # 2024-01-04 (Thu) -> 2024-01-08 (Mon): Fri 5th is missing -> two business days of carry
        r3 = 0.041 * 2 / 252 - D * (-0.001) + 0.5 * C * 0.001 ** 2
        self.assertAlmostEqual(idx[1][1], 100 * (1 + r1), places=10)
        self.assertAlmostEqual(idx[2][1], 100 * (1 + r1) * (1 + r2), places=10)
        self.assertAlmostEqual(idx[3][1], 100 * (1 + r1) * (1 + r2) * (1 + r3), places=10)
        self.assertEqual(refresh._weekdays_between(dt.date(2024, 1, 5), dt.date(2024, 1, 8)), 1)
        self.assertEqual(refresh.total_return_index([], D, C), [])


# ============================================================================================ refresh
class FakeYahoo:
    """Serves a fixed daily history; records the start dates requested."""

    def __init__(self, rows):
        self.rows = rows
        self.starts = []

    def __call__(self, symbol, start_epoch=0, end_epoch=None):
        self.starts.append((symbol, start_epoch))
        d0 = dt.datetime.fromtimestamp(start_epoch, dt.timezone.utc).date().isoformat()
        return [dict(r) for r in self.rows if r["date"] >= d0]


def weekdays(start, n):
    d, out = dt.date.fromisoformat(start), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


class TestRefresh(TmpStoreCase):
    def setUp(self):
        super().setUp()
        self.days = weekdays("2024-01-01", 30)
        self.yrows = [bar(d, 100.0 + i, 90.0 + i) for i, d in enumerate(self.days)]
        self.fake_y = FakeYahoo(self.yrows[:20])
        self.fred_calls = []
        self.yields = [(d, 4.0 + 0.01 * i) for i, d in enumerate(self.days)]

        def fake_fred(series_id, start=None):
            self.fred_calls.append((series_id, start))
            return [(d, v) for d, v in self.yields if start is None or d >= start]
        self.fake_fred = fake_fred
        self.sec_calls = []

        def fake_cf(cik):
            self.sec_calls.append(cik)
            return companyfacts()
        self.patches = [mock.patch.object(yahoo, "fetch_history", self.fake_y),
                        mock.patch.object(fred, "fetch_series", fake_fred),
                        mock.patch.object(fred, "fetch_first_release",
                                          mock.Mock(side_effect=AssertionError("no key in these tests"))),
                        mock.patch.object(sec, "fetch_companyfacts", fake_cf),
                        mock.patch.object(refresh, "_sleep", lambda s: None),
                        mock.patch.dict(os.environ, {}, clear=False)]
        for p in self.patches:
            p.start()
        os.environ.pop("FRED_API_KEY", None)  # latest-vintage mode; first releases are tested in test_fs2_vintages

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        super().tearDown()

    def test_incremental_refresh(self):
        progress = []
        s1 = refresh.refresh(self.store, assets=["SPY", "UST10Y", "AAPL"],
                             progress=lambda d, t, m: progress.append((d, t, m)))
        self.assertEqual(s1["errors"], [])
        self.assertEqual(self.fake_y.starts, [("SPY", 0), ("AAPL", 0)])  # AAPL: prices and fundamentals
        self.assertEqual(self.fred_calls, [("DGS10", None)])
        self.assertEqual(self.sec_calls, [320193])
        self.assertEqual(self.store.price_count("SPY"), 20)
        self.assertEqual(self.store.price_count("UST10Y"), 30)
        self.assertEqual(self.store.prices("UST10Y")[0]["close"], 100.0)
        self.assertGreater(len(self.store.fundamentals("AAPL")), 0)
        self.assertEqual(progress[-1], (5, 5, "done"))
        self.assertEqual(progress[0][:2], (0, 5))

        # second call: yahoo asks from (last stored date - overlap), FRED from last - 30d, SEC skipped (< 7 days)
        self.fake_y.rows = self.yrows
        v = self.store.data_version()
        s2 = refresh.refresh(self.store, assets=["SPY", "UST10Y", "AAPL"])
        last = dt.date.fromisoformat(self.days[19])
        exp = yahoo.date_to_epoch(last - dt.timedelta(days=refresh.YAHOO_OVERLAP_DAYS))
        self.assertEqual(self.fake_y.starts[-2], ("SPY", exp))
        self.assertEqual(self.fred_calls[-1], ("DGS10", (dt.date.fromisoformat(self.days[-1])
                                                         - dt.timedelta(days=30)).isoformat()))
        self.assertEqual(self.sec_calls, [320193])
        self.assertEqual(s2["skipped"][0]["key"], "AAPL")
        self.assertEqual(s2["prices"]["SPY"], 10)  # only the 10 new bars changed
        self.assertEqual(self.store.price_count("SPY"), 30)
        self.assertNotEqual(self.store.data_version(), v)
        s3 = refresh.refresh(self.store, assets=["SPY"], fundamentals=False)
        self.assertEqual(s3["prices"]["SPY"], 0)

    def test_readjusted_history_is_refetched(self):
        refresh.refresh(self.store, assets=["SPY"])
        # a dividend rescales every earlier adj_close: the overlap no longer matches -> full re-download
        self.fake_y.rows = [dict(r, adj_close=r["adj_close"] * 0.99) for r in self.yrows]
        s = refresh.refresh(self.store, assets=["SPY"])
        self.assertEqual(self.fake_y.starts[-1], ("SPY", 0))
        self.assertEqual(self.store.price_count("SPY"), 30)
        self.assertAlmostEqual(self.store.prices("SPY")[0]["adj_close"], 90.0 * 0.99)
        self.assertTrue(self.store.fetch_log(1)[0]["note"].startswith("readjusted"))
        self.assertEqual(s["errors"], [])

    def test_errors_do_not_stop_the_run(self):
        def boom(symbol, start_epoch=0, end_epoch=None):
            if symbol == "QQQ":
                raise yahoo.YahooError("QQQ: HTTP 500", status=500)
            return self.fake_y(symbol, start_epoch)
        with mock.patch.object(yahoo, "fetch_history", boom):
            s = refresh.refresh(self.store, assets=["QQQ", "SPY", "NOPE"], fundamentals=False)
        self.assertEqual({e["key"] for e in s["errors"]}, {"QQQ", "NOPE"})
        self.assertEqual(self.store.price_count("SPY"), 20)
        self.assertEqual(self.store.fetch_log(10)[-1]["status"], "error")  # QQQ was logged first

    def test_full_universe_task_list(self):
        with mock.patch.object(refresh, "refresh_yahoo", return_value={"rows": 0, "changed": 0, "mode": "x"}), \
                mock.patch.object(refresh, "refresh_sec", return_value=0):
            s = refresh.refresh(self.store)
        self.assertEqual(set(s["macro"]), set(universe.FRED_SERIES))
        self.assertEqual({self.store.macro_kind(k) for k in universe.FRED_SERIES}, {"latest_vintage"})
        self.assertEqual(set(s["synthetic"]), {"UST2Y", "UST5Y", "UST10Y", "UST30Y", "CORP_BAA"})
        self.assertEqual(len(s["fundamentals"]), 45)
        self.assertEqual(len(s["prices"]), len([a for a in universe.UNIVERSE if a["yahoo"]]))

    def test_stale(self):
        self.assertTrue(refresh.stale(self.store, today=dt.date(2024, 1, 10)))
        self.store.upsert_prices("SPY", [bar("2024-01-05", 1.0)])  # Friday
        self.assertFalse(refresh.stale(self.store, today=dt.date(2024, 1, 8)))   # Monday: last bday = Fri
        self.assertFalse(refresh.stale(self.store, today=dt.date(2024, 1, 7)))   # Sunday
        self.assertTrue(refresh.stale(self.store, today=dt.date(2024, 1, 9)))    # Tuesday: Monday missing
        self.assertEqual(refresh.last_business_day(dt.date(2024, 1, 8)), dt.date(2024, 1, 5))

    def test_add_symbol(self):
        t = int(dt.datetime(2024, 1, 2, 14, 30, tzinfo=dt.timezone.utc).timestamp())
        meta, rows = yahoo.parse_chart(chart_payload([t, t + 86400], [20.0, 21.0], meta_extra={
            "instrumentType": "INDEX", "longName": "CBOE Volatility\x00 Index", "currency": "USD",
            "exchangeName": "CXI"})["chart"]["result"][0])
        with mock.patch.object(yahoo, "fetch_history_meta", return_value=(meta, rows)):
            a = refresh.add_symbol(self.store, "^VIX")
        self.assertEqual(a["id"], "VIX")
        self.assertEqual(a["asset_class"], "INDEX")
        self.assertEqual(a["name"], "CBOE Volatility Index")
        self.assertEqual(a["yahoo"], "^VIX")
        self.assertTrue(a["meta"]["user_added"])
        self.assertEqual(self.store.price_count("VIX"), 2)
        # existing Yahoo symbol returns the universe asset
        self.store.upsert_prices("SPY", [bar("2024-01-02", 1.0)])
        self.assertEqual(refresh.add_symbol(self.store, "spy")["id"], "SPY")
        with self.assertRaises(ValueError):
            refresh.add_symbol(self.store, "bad symbol/../")
        with mock.patch.object(yahoo, "fetch_history_meta", return_value=(meta, [])):
            with self.assertRaises(ValueError):
                refresh.add_symbol(self.store, "EMPTY")

    def test_add_symbol_equity_gets_cik_and_id_collision(self):
        meta = {"instrumentType": "EQUITY", "shortName": "Palantir", "currency": "USD"}
        with mock.patch.object(yahoo, "fetch_history_meta", return_value=(meta, [bar("2024-01-02", 20.0)])), \
                mock.patch.object(sec, "lookup_cik", return_value=1321655):
            a = refresh.add_symbol(self.store, "PLTR")
            self.assertEqual((a["id"], a["asset_class"], a["cik"]), ("PLTR", "EQUITY", 1321655))
            b = refresh.add_symbol(self.store, "GOLD")  # the id GOLD is taken by gold futures (GC=F)
            self.assertEqual(b["id"], "Y_GOLD")


class TestHelpers(unittest.TestCase):
    def test_num(self):
        self.assertEqual(num("1.5"), 1.5)
        self.assertIsNone(num(float("nan")))
        self.assertIsNone(num(True))
        self.assertIsNone(num("x"))
        self.assertIsNone(num([1]))


if __name__ == "__main__":
    unittest.main()
