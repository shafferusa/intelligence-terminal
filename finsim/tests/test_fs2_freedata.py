"""Free data additions (finsim2/data: futcurve, cboe, analyst): parsers on fixtures, point-in-time rules, no network."""
import datetime as dt
import json
import math
import os
import shutil
import tempfile
import unittest
from unittest import mock

from finsim2.data import FetchError
from finsim2.data import analyst as A
from finsim2.data import cboe as B
from finsim2.data import futcurve as F
from finsim2.data import newdata
from finsim2.data.store import Store


def _store():
    tmp = tempfile.mkdtemp()
    return tmp, Store(os.path.join(tmp, "x.db"))


def _bars(days, closes, off=-14400):
    ts = [int(dt.datetime(*d, tzinfo=dt.timezone.utc).timestamp()) - off for d in days]
    return {"meta": {"gmtoffset": off}, "timestamp": ts, "indicators": {"quote": [{"close": closes, "volume": [100] * len(closes)}]}}


class FuturesCurve(unittest.TestCase):
    def test_candidate_months_follow_each_cycle(self):
        c = F.candidates("CL", "NYM", F.CODES, dt.date(2026, 9, 28), 3)
        self.assertEqual(c, [("CLU26.NYM", 202609), ("CLV26.NYM", 202610), ("CLX26.NYM", 202611)])
        z = F.candidates("ZC", "CBT", "HKNUZ", dt.date(2026, 9, 28), 3)
        self.assertEqual([s for s, _ in z], ["ZCU26.CBT", "ZCZ26.CBT", "ZCH27.CBT"])

    def test_todays_bar_waits_for_the_settlement(self):
        res = _bars([(2026, 9, 25), (2026, 9, 28)], [92.41, 92.72])
        self.assertEqual(F.last_bar(res, dt.datetime(2026, 9, 28, 15, 0)), ("2026-09-25", 92.41, 100.0))
        self.assertEqual(F.last_bar(res, dt.datetime(2026, 9, 28, 17, 30))[:2], ("2026-09-28", 92.72))
        self.assertIsNone(F.last_bar({"meta": {}}, dt.datetime(2026, 9, 28, 18)))

    def test_a_stale_front_month_is_not_contract_one(self):
        stale = ("2026-09-22", 90.0, 5.0)
        live = [(202611, ("2026-09-25", 92.4, 9.0)), (202612, ("2026-09-25", 88.7, 8.0)), (202701, ("2026-09-25", 86.0, 7.0)),
                (202702, ("2026-09-25", 84.0, 6.0)), (202703, ("2026-09-25", 82.0, 5.0))]
        rows = F.curve_rows("WTI", [(202610, stale)] + live)
        got = {r[2]: r[3] for r in rows}
        self.assertEqual(got["c1"], 92.4)
        self.assertEqual(got["c1_ym"], 202611.0)
        self.assertEqual(got["c4"], 84.0)
        self.assertNotIn("c5", got)
        self.assertEqual(got["src"], 2.0)
        self.assertTrue(all(r[0] == "fut:WTI" and r[1] == "2026-09-25" and r[4] == "2026-09-26" for r in rows))

    def test_eia_history_rows_and_recheck(self):
        payload = {"response": {"total": 2, "data": [{"period": "2024-04-05", "value": 86.91}, {"period": "2024-04-04", "value": None}]}}
        self.assertEqual(F.eia_rows(payload, "WTI", 1), [("fut:WTI", "2024-04-05", "c1", 86.91, "2024-04-06"),
                                                         ("fut:WTI", "2024-04-05", "src", 1.0, "2024-04-06")])
        self.assertEqual(F.eia_rows(payload, "WTI", 2)[0][2], "c2")
        tmp, st = _store()
        try:
            with mock.patch.dict(os.environ, {"EIA_API_KEY": ""}):
                self.assertIn("skipped", F.eia_history(st))
            st.kv_set(F.EIA_KEY, dt.date.today().isoformat())
            self.assertIn("note", F.eia_history(st, key="k"), "checked recently: no request")
        finally:
            st.close()
            shutil.rmtree(tmp, ignore_errors=True)


def _chain(spot=100.0, iv=0.20, put_skew=0.0, pct=False, session="2026-09-25"):
    """Calls and puts at strikes 80..120 for expiries 20 and 50 days out; deltas from Black-Scholes with the given IV."""
    d0 = dt.date.fromisoformat(session)
    opts = []
    for days in (3, 20, 50):
        exp = d0 + dt.timedelta(days=days)
        t = days / 365
        for k in range(80, 121, 5):
            for right in "CP":
                d1 = (math.log(spot / k) + 0.5 * iv * iv * t) / (iv * math.sqrt(t))
                nd = 0.5 * (1 + math.erf(d1 / math.sqrt(2)))
                delta = nd if right == "C" else nd - 1
                v = iv + (put_skew * max(0.0, (spot - k) / 20) if right == "P" else 0.0)
                opts.append({"option": f"XYZ{exp:%y%m%d}{right}{k * 1000:08d}", "bid": 1.0, "ask": 1.1, "iv": v * (100 if pct else 1),
                             "delta": delta, "open_interest": 10 if right == "P" else 5, "volume": 3 if right == "P" else 2})
    return {"timestamp": "x", "data": {"symbol": "XYZ", "current_price": spot, "iv30": 21.0 if pct else 0.21, "options": opts}}


class Cboe(unittest.TestCase):
    def test_history_csv_both_layouts(self):
        a = B.parse_history("DATE,OPEN,HIGH,LOW,CLOSE\n01/02/2024,13.1,14.0,12.9,13.5\n01/03/2024,bad,1,1,\n", "VIX9D")
        self.assertEqual(a, [("2024-01-02", 13.5, "2024-01-03")])
        b = B.parse_history("DATE,SKEW\n2024-01-02,142.3\n", "SKEW")
        self.assertEqual(b, [("2024-01-02", 142.3, "2024-01-03")])
        self.assertEqual(B.parse_history("<html>blocked</html>", "VVIX"), [])

    def test_the_session_a_snapshot_shows(self):
        self.assertEqual(B.session_for(dt.datetime(2026, 9, 28, 16, 20)), "2026-09-28")
        self.assertIsNone(B.session_for(dt.datetime(2026, 9, 28, 11, 0)), "the session is open: nothing is stored")
        self.assertEqual(B.session_for(dt.datetime(2026, 9, 28, 8, 0)), "2026-09-25", "Monday morning shows Friday")
        self.assertEqual(B.session_for(dt.datetime(2026, 9, 27, 12, 0)), "2026-09-25", "weekend shows Friday")

    def test_features_from_a_flat_and_a_skewed_chain(self):
        f = B.features(_chain(), "2026-09-25")
        self.assertAlmostEqual(f["iv30"], 0.20, places=6)
        self.assertAlmostEqual(f["skew25"], 0.0, places=6)
        self.assertNotIn("iv60", f, "no expiry beyond 50 days: 60 days is not extrapolated")
        self.assertEqual((f["put_oi"], f["call_oi"], f["put_volume"], f["call_volume"]), (270.0, 135.0, 81.0, 54.0))
        self.assertAlmostEqual(f["iv30_cboe"], 0.21)
        s = B.features(_chain(put_skew=0.05), "2026-09-25")
        self.assertGreater(s["skew25"], 0.005, "downside puts richer: positive skew")
        self.assertAlmostEqual(s["iv30"], 0.20 + 0.0, delta=0.02)
        p = B.features(_chain(pct=True), "2026-09-25")
        self.assertAlmostEqual(p["iv30"], 0.20, places=6, msg="percent IVs are converted to decimals")
        self.assertEqual(B.features({"data": {"options": []}}, "2026-09-25"), {})

    def test_total_variance_interpolation(self):
        v = B.constant_maturity([(20, 0.2), (50, 0.3)], 30)
        self.assertAlmostEqual(v * v * 30, 0.04 * 20 + (10 / 30) * (0.09 * 50 - 0.04 * 20))
        self.assertIsNone(B.constant_maturity([(20, 0.2)], 60))
        self.assertEqual(B.constant_maturity([(45, 0.25)], 30), 0.25)

    def test_symbols_and_snapshot(self):
        self.assertEqual(B.cboe_symbol({"id": "SPX", "asset_class": "INDEX"}), "_SPX")
        self.assertEqual(B.cboe_symbol({"id": "BRK-B", "asset_class": "EQUITY", "currency": "USD", "yahoo": "BRK-B"}), "BRK.B")
        self.assertIsNone(B.cboe_symbol({"id": "SHEL.L", "asset_class": "EQUITY", "currency": "GBP", "yahoo": "SHEL.L"}))
        self.assertIsNone(B.cboe_symbol({"id": "EURUSD", "asset_class": "FX", "currency": "USD"}))
        tmp, st = _store()
        try:
            open_ = B.snapshot(st, now_utc=dt.datetime(2026, 9, 28, 15, 0), symbols=[("XYZ", "XYZ")])
            self.assertIn("deferred", open_)
            calls = []

            def fake(url):
                calls.append(url)
                if "BAD" in url:
                    raise FetchError("HTTP 403", 403)
                return json.dumps(_chain(session="2026-09-28")).encode()
            with mock.patch.object(B, "_get", fake), mock.patch.object(B.time, "sleep"):
                r = B.snapshot(st, now_utc=dt.datetime(2026, 9, 28, 21, 0), symbols=[("XYZ", "XYZ"), ("BAD", "BAD")])
            self.assertEqual((r["underlyings"], r["session"], len(r["errors"])), (1, "2026-09-28", 1))
            rows = {f: (d, p) for _, d, f, _, p in st.alt(B.DATASET, "XYZ")}
            self.assertEqual(rows["iv30"], ("2026-09-28", "2026-09-29"), "a close is published the next day")
            self.assertTrue(calls[0].startswith("https://cdn.cboe.com/api/global/delayed_quotes/options/XYZ.json"))
        finally:
            st.close()
            shutil.rmtree(tmp, ignore_errors=True)


class Analyst(unittest.TestCase):
    REC = [{"symbol": "AAPL", "period": "2026-08-01", "strongBuy": 13, "buy": 24, "hold": 14, "sell": 3, "strongSell": 0},
           {"symbol": "AAPL", "period": "2026-09-01", "strongBuy": 12, "buy": 22, "hold": 15, "sell": 3, "strongSell": 1}]
    EPS = [{"period": "2026-06-30", "actual": 1.91, "estimate": 1.9271, "surprise": -0.0171, "surprisePercent": -0.8873},
           {"period": "2026-03-31", "actual": 2.01, "estimate": 1.9884, "surprise": 0.0216, "surprisePercent": 1.0863}]

    def test_ratings_are_a_snapshot_of_the_latest_month(self):
        rows = {f: (d, v, p) for _, d, f, v, p in A.rec_rows(self.REC, "AAPL", "2026-09-28")}
        self.assertEqual(rows["rec_strong_buy"], ("2026-09-28", 12, "2026-09-29"))
        self.assertEqual(rows["rec_n"][1], 53)
        self.assertEqual(rows["rec_period"][1], 202609.0)
        self.assertAlmostEqual(rows["rec_mean"][1], (12 + 44 + 45 + 12 + 5) / 53)
        self.assertEqual(A.rec_rows({"error": "x"}, "AAPL", "2026-09-28"), [])

    def test_surprises_are_published_after_the_release_and_never_overwritten(self):
        rows = A.eps_rows(self.EPS, "AAPL", "2026-09-28", ["2026-05-01", "2026-07-31"], set())
        pub = {(d, f): p for _, d, f, _, p in rows}
        self.assertEqual(pub[("2026-06-30", "eps_actual")], "2026-08-01")
        self.assertEqual(pub[("2026-03-31", "eps_surprise_pct")], "2026-05-02")
        no_rel = A.eps_rows(self.EPS, "AAPL", "2026-09-28", [], set())
        self.assertTrue(all(r[4] == "2026-09-29" for r in no_rel), "no release on file: public from the fetch")
        again = A.eps_rows(self.EPS, "AAPL", "2026-09-28", [], {("2026-06-30", "eps_actual")})
        self.assertEqual({r[1] for r in again}, {"2026-03-31"})

    def test_no_key_no_request(self):
        tmp, st = _store()
        try:
            with mock.patch.dict(os.environ, {"FINNHUB_KEY": ""}), mock.patch.object(A, "http_get", side_effect=AssertionError):
                self.assertIn("skipped", A.refresh(st))
        finally:
            st.close()
            shutil.rmtree(tmp, ignore_errors=True)


def _dump(path, days, iv=0.20, pct=False, occ=False):
    """A SQLite options dump in a vendor-like layout: one row per contract and day."""
    import sqlite3
    con = sqlite3.connect(path)
    if occ:
        con.execute("CREATE TABLE opts (quote_date TEXT, option_symbol TEXT, implied_volatility REAL, delta REAL, open_interest REAL, "
                    "volume REAL, bid REAL, ask REAL, underlying_last REAL)")
    else:
        con.execute("CREATE TABLE opts (act_symbol TEXT, date TEXT, expiration TEXT, strike REAL, call_put TEXT, iv REAL, delta REAL, "
                    "open_interest REAL, volume REAL, bid REAL, ask REAL, underlying_price REAL)")
    for sym in ("SPY", "ZZZZ"):
        for d in days:
            ch = _chain(iv=iv, session=d)["data"]["options"]
            for o in ch:
                m = B.OCC.search(o["option"])
                exp = f"20{m.group(1)}-{m.group(2)}-{m.group(3)}"
                v = o["iv"] * (100 if pct else 1)
                if occ:
                    con.execute("INSERT INTO opts VALUES (?,?,?,?,?,?,?,?,?)", (d, sym + o["option"][3:], v, o["delta"], o["open_interest"],
                                                                               o["volume"], o["bid"], o["ask"], 100.0))
                else:
                    con.execute("INSERT INTO opts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (sym, d, exp, int(m.group(5)) / 1000, m.group(4), v,
                                                                                     o["delta"], o["open_interest"], o["volume"], o["bid"], o["ask"], 100.0))
    con.commit()
    con.close()


class OptionsDump(unittest.TestCase):
    def test_probe_import_and_quality_gate(self):
        from finsim2.data import optionsdump as OD
        tmp, st = _store()
        try:
            st.upsert_asset({"id": "SPY", "name": "SPY", "asset_class": "ETF", "sector": "Broad Market", "country": "United States",
                             "currency": "USD", "yahoo": "SPY", "meta": {}})
            days = ["2026-09-21", "2026-09-22", "2026-09-23"]
            for layout in ({"pct": True}, {"occ": True}):
                path = os.path.join(tmp, f"d{len(layout)}{list(layout)[0]}.db")
                _dump(path, days, **layout)
                info = OD.probe(path)
                self.assertEqual(info[0]["mapping"]["iv"] in ("iv", "implied_volatility"), True)
                res = OD.import_file(st, path)
                self.assertEqual((res["stored"], res["skipped_unknown"]), (3, ["ZZZZ"]), res)
                rows = {(d, f): (v, p) for _, d, f, v, p in st.alt(OD.DATASET, "SPY")}
                self.assertAlmostEqual(rows[("2026-09-22", "iv30")][0], 0.20, places=6)
                self.assertEqual(rows[("2026-09-22", "iv30")][1], "2026-09-23", "end-of-day data: public the next day")
            q = OD.quality(st)
            self.assertFalse(q["passed"], "three days and no VIX: the gate cannot pass")
            self.assertFalse(OD.gate_passed(st))
        finally:
            st.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_quality_gate_passes_only_on_agreement(self):
        from finsim2.data import optionsdump as OD
        import random as _r
        tmp, st = _store()
        try:
            rng = _r.Random(1)
            d0 = dt.date(2008, 1, 2)
            days = [(d0 + dt.timedelta(days=k)).isoformat() for k in range(0, 700) if (d0 + dt.timedelta(days=k)).weekday() < 5]
            for a, sid in OD.GATE.items():
                st.upsert_asset({"id": a, "name": a, "asset_class": "ETF", "sector": "Broad Market", "country": "United States",
                                 "currency": "USD", "yahoo": a, "meta": {}})
                st.upsert_prices(a, [{"date": d, "open": 1, "high": 1, "low": 1, "close": 1, "adj_close": 1, "volume": 1} for d in days])
                lvl = [15 + 10 * math.sin(k / 40) + rng.gauss(0, 0.5) for k in range(len(days))]
                st.upsert_macro(sid, [(d, v) for d, v in zip(days, lvl)])
                st.put_alt(OD.DATASET, [(a, d, "iv30", (v + rng.gauss(0, 1)) / 100, d) for d, v in zip(days, lvl)])
            self.assertTrue(OD.quality(st)["passed"])
            st.put_alt(OD.DATASET, [("QQQ", d, "iv30", rng.uniform(0.1, 0.4), d) for d in days])     # garbage for one name
            q = OD.quality(st)
            self.assertFalse(q["passed"])
            self.assertFalse(q["names"]["QQQ"]["passed"])
        finally:
            st.close()
            shutil.rmtree(tmp, ignore_errors=True)


class EulerpoolProbe(unittest.TestCase):
    def test_probe_reports_each_claim_without_the_key(self):
        from finsim2.data import eulerpool as EP
        seen = []

        def fake(url, headers=None, timeout=30):
            seen.append((url, headers))
            if "documentation/yaml" in url:
                return b"openapi: 3.0.0\npaths:\n  /api/1/equity/overview/{identifier}:\n    get: {}\n  /api/1/equity/pit/estimates/{ticker}:\n    get: {}\n"
            if "overview/US5249081002" in url or "overview/US2935611069" in url:
                raise FetchError("HTTP 404", 404, b'{"error": "not found"}')
            if "overview" in url:
                return b'{"name": "X", "isin": "Y"}'
            if "quotes/US5249081002" in url:
                return b'[{"timestamp": 1167782400000, "price": 60.0}, {"timestamp": 1221177600000, "price": 3.65}]'
            if "pit/estimates/AAPL" in url:
                return b'[{"asof": "2019-03-01T00:00:00.000Z", "epsEstimate": 2.1}, {"asof": "2026-09-01T00:00:00.000Z", "epsEstimate": 7.9}]'
            if "incomestatement" in url:
                return b'[{"period": "2024-09-28T00:00:00.000Z", "revenue": 391035}]'
            if "market/options" in url:
                raise FetchError("HTTP 403", 403, b'{"error": "not in your plan"}')
            raise FetchError("HTTP 404", 404)
        tmp = tempfile.mkdtemp()
        try:
            with mock.patch.object(EP, "http_get", fake), mock.patch.object(EP.time, "sleep"):
                text = EP.probe(tmp, key="SECRET-KEY-123")
            self.assertIn("4 of 6 delisted companies answer", text)
            self.assertIn("2007-01-03 → 2008-09-12", text)
            self.assertIn("2019-03-01 → 2026-09-01", text, "point-in-time estimate snapshots are dated")
            self.assertIn("| SPY option chain | 403 | JSON: not in your plan |", text)
            self.assertIn("(2 paths)", text)
            self.assertNotIn("SECRET-KEY-123", text)
            self.assertTrue(all("SECRET" not in u for u, _ in seen), "the key never goes in a URL")
            self.assertTrue(all(h["Authorization"] == "Bearer SECRET-KEY-123" for _, h in seen))
            for f in ("eulerpool_probe.md", "eulerpool_openapi.yaml"):
                self.assertTrue(os.path.exists(os.path.join(tmp, f)))
            with mock.patch.dict(os.environ, {"EULERPOOL_API_KEY": ""}):
                self.assertIn("skipped", EP.probe(tmp))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _run(self, fake):
        from finsim2.data import eulerpool as EP
        tmp = tempfile.mkdtemp()
        try:
            with mock.patch.object(EP, "http_get", fake), mock.patch.object(EP.time, "sleep"):
                return EP.probe(tmp, key="SECRET-KEY-123")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_access_failures_are_diagnosed_not_guessed(self):
        calls = []

        def html(url, headers=None, timeout=30):
            calls.append(url)
            raise FetchError("HTTP 403", 403, b"<!DOCTYPE html><html><head><title>Attention Required! | Cloudflare</title></head></html>")
        text = self._run(html)
        self.assertIn('HTML page "Attention Required! | Cloudflare" (Cloudflare)', text)
        self.assertIn("stopped before the API", text)
        self.assertEqual(len(calls), 4, "four documented attempts, then stop")
        self.assertNotIn("SECRET-KEY-123", text)

        def bad_key(url, headers=None, timeout=30):
            raise FetchError("HTTP 403", 403, b'{"error": "Invalid API token"}')
        self.assertIn("the key itself is refused", self._run(bad_key))

        def plan(url, headers=None, timeout=30):
            raise FetchError("HTTP 403", 403, b'{"message": "Upgrade your plan to access this endpoint"}')
        self.assertIn("outside the free plan", self._run(plan))

    def test_the_query_form_is_used_when_only_it_works(self):
        seen = []

        def fake(url, headers=None, timeout=30):
            seen.append((url, headers))
            if "token=" not in url:
                raise FetchError("HTTP 403", 403, b'{"error": "missing token"}')
            if "overview" in url:
                return b'{"name": "Apple", "isin": "US0378331005"}'
            raise FetchError("HTTP 404", 404)
        text = self._run(fake)
        self.assertIn("Access works with the ?token= form", text)
        self.assertNotIn("SECRET-KEY-123", text, "the key is in the request, never in the report")
        self.assertTrue(all(h.get("User-Agent", "").startswith("FinSim2/") for _, h in seen))


class EulerpoolCollector(unittest.TestCase):
    def test_parsers_are_point_in_time(self):
        from finsim2.data import eulerpool as EP
        est = EP.parse_estimates([{"field": "epsAvg", "period": "2027-09-30T00:00:00.000Z", "value": 8.1, "as_of_date": "2025-03-03T00:00:00.000Z"},
                                  {"field": "eps avg!", "period": "2027-09-30", "value": "x", "as_of_date": "2025-03-04"}], "AAPL")
        self.assertEqual(est, [("AAPL", "2025-03-03", "epsAvg@2027-09", 8.1, "2025-03-04")])
        rows = [{"date": "2026-06-30T00:00:00.000Z", "epsEstimate": 1.93, "epsActual": 1.91, "surprisePercent": -0.9, "quarter": 3},
                {"date": "1997-09-30T00:00:00.000Z", "epsEstimate": 0.1, "epsActual": 0.2, "quarter": 4},
                {"date": "1998-03-31", "epsEstimate": 0.1, "epsActual": 0.12, "quarter": 2}]
        sur = {(d, f): (v, p) for _, d, f, v, p in EP.parse_surprises(rows, "AAPL", ["2026-07-30"])}
        self.assertEqual(sur[("2026-06-30", "eps_actual")], (1.91, "2026-07-31"), "public the day after the 8-K release")
        self.assertAlmostEqual(sur[("2026-06-30", "eps_surprise")][0], -0.02)
        self.assertEqual(sur[("1997-09-30", "eps_actual")][1], "1997-12-29", "no release on file: fiscal Q4 + 90 days")
        self.assertEqual(sur[("1998-03-31", "eps_actual")][1], "1998-05-15", "other quarters + 45 days")
        g = {(d, f): v for _, d, f, v, _ in EP.parse_grades([{"date": "2026-08-10", "action": "upgrade"}, {"date": "2026-08-10", "action": "Upgrade"},
                                                             {"date": "2026-08-10", "action": "maintain"}, {"date": "bad", "action": "downgrade"}], "AAPL")}
        self.assertEqual(g, {("2026-08-10", "grade_up"): 2.0, ("2026-08-10", "grade_other"): 1.0})
        vx = EP.parse_vix_futures([{"date": "2026-09-24", "maturity_days": 52, "value": 19.5}, {"date": "2026-09-24", "maturity_days": 22, "value": 18.0}])
        self.assertEqual([(f, v) for _, _, f, v, _ in vx], [("vx1", 18.0), ("vx1_days", 22.0), ("vx2", 19.5), ("vx2_days", 52.0)])

    def test_refresh_stores_and_stops_on_a_refused_key(self):
        from finsim2.data import eulerpool as EP
        tmp, st = _store()
        try:
            st.upsert_asset({"id": "AAPL", "name": "Apple", "asset_class": "EQUITY", "sector": "Tech", "country": "United States",
                             "currency": "USD", "yahoo": "AAPL", "meta": {}})

            def fake(url, headers=None, timeout=30):
                if "vix/term-structure" in url:
                    return b'[{"date": "2026-09-24", "maturity_days": 22, "value": 18.0}]'
                if "pit/estimates" in url:
                    return b'[{"field": "epsAvg", "period": "2027-09-30", "value": 8.1, "as_of_date": "2025-03-03"}]'
                if "earnings-surprises" in url:
                    return b'[{"date": "2026-06-30", "epsEstimate": 1.93, "epsActual": 1.91, "quarter": 3}]'
                raise FetchError("HTTP 404", 404, b'{"error": "not found"}')
            with mock.patch.object(EP, "http_get", fake), mock.patch.object(EP.time, "sleep"), \
                    mock.patch.object(EP, "symbols", return_value=["AAPL"]):
                r = EP.refresh(st, key="k")
            self.assertEqual((r["not_covered"], r["errors"]), (2, []))
            self.assertEqual(r["depth"]["ep_surprises"]["first"], "2026-06-30")
            self.assertEqual(len(st.alt(EP.VIXFUT)), 2)

            def refused(url, headers=None, timeout=30):
                raise FetchError("HTTP 403", 403, b'{"error": "Invalid API token"}')
            with mock.patch.object(EP, "http_get", refused), mock.patch.object(EP.time, "sleep"):
                r = EP.refresh(st, key="k")
            self.assertIn("refused the key", r["errors"][0])
            with mock.patch.dict(os.environ, {"EULERPOOL_API_KEY": ""}):
                self.assertIn("skipped", EP.refresh(st))
        finally:
            st.close()
            shutil.rmtree(tmp, ignore_errors=True)


class Orchestration(unittest.TestCase):
    def test_a_deferred_source_is_retried_and_status_lists_the_new_datasets(self):
        tmp, st = _store()
        try:
            with mock.patch.object(newdata, "_runner", return_value=lambda s, say: {"deferred": "session open"}):
                out = newdata.refresh(st, ["options"])
            self.assertEqual(out["options"]["state"], "deferred")
            self.assertIn("options", newdata.due(st), "not stamped: runs again after the close")
            names = {r["dataset"] for r in newdata.status(st)}
            self.assertTrue({"options_cboe", "futures_curve", "analyst_finnhub", "CBOE_VVIX"} <= names)
            self.assertTrue({"cboe", "options", "futures", "analyst"} <= set(newdata.SOURCES))
        finally:
            st.close()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
