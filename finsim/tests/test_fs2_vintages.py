"""FinSim2 macro vintages: first-release FRED values with their publication dates, stored and aligned point in time.

Network is never touched: the FRED transport (``fred._get``) and the downloaders are patched with small fixtures.
"""
import datetime as dt
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
import urllib.parse
from unittest import mock

from finsim2.data import FetchError, GOV_UA, fred, refresh
from finsim2.data.store import Store
from finsim2.engine import regimes as rg
from finsim2.engine.align import Panel, sahm_gap
from finsim2.engine.features import macro_features

KEY = "SECRETKEY123"


def weekdays(start, end):
    d, e, out = dt.date.fromisoformat(start), dt.date.fromisoformat(end), []
    while d <= e:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def api_payload(rows):
    """An output_type=4 payload: rows are (date, value, realtime_start)."""
    return json.dumps({"realtime_start": "1776-07-04", "realtime_end": "9999-12-31", "output_type": 4,
                       "observations": [{"realtime_start": p, "realtime_end": "9999-12-31", "date": d, "value": v}
                                        for d, v, p in rows]}).encode()


def query(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fs2vint")
        self.store = Store(os.path.join(self.tmp, "research.db"))

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def calendar(self, start="2020-01-01", end="2020-12-31"):
        self.days = weekdays(start, end)
        self.store.upsert_prices("SPY", [{"date": d, "close": 100.0, "adj_close": 100.0} for d in self.days])


# ============================================================================================ fred.py
class TestFirstReleaseFetch(unittest.TestCase):
    UNRATE = [("2020-03-01", "4.4", "2020-04-03"), ("2020-04-01", "14.7", "2020-05-08"),
              ("2020-05-01", ".", "2020-06-05"), ("2020-06-01", "11.1", "2020-07-02")]

    def test_request_shape_user_agent_and_parse(self):
        calls = []

        def fake_http(url, headers=None, timeout=60):
            calls.append((url, headers))
            return api_payload(self.UNRATE)
        with mock.patch.object(fred, "http_get", fake_http), mock.patch.dict(os.environ, {"FRED_API_KEY": KEY}):
            rows = fred.fetch_first_release("UNRATE")
        self.assertEqual(rows, [("2020-03-01", 4.4, "2020-04-03"), ("2020-04-01", 14.7, "2020-05-08"),
                                ("2020-06-01", 11.1, "2020-07-02")])
        url, headers = calls[0]
        self.assertEqual(headers["User-Agent"], GOV_UA)
        self.assertTrue(url.startswith(fred.API_URL + "?"))
        q = query(url)
        self.assertEqual((q["series_id"], q["output_type"], q["file_type"]), ("UNRATE", "4", "json"))
        self.assertEqual((q["realtime_start"], q["realtime_end"]), ("1776-07-04", "9999-12-31"))
        self.assertEqual(q["api_key"], KEY)
        self.assertNotIn("observation_start", q)

    def test_start_and_duplicate_dates_keep_the_earliest_publication(self):
        payload = api_payload([("2020-03-01", "4.4", "2020-04-03"), ("2020-04-01", "14.9", "2020-06-05"),
                               ("2020-04-01", "14.7", "2020-05-08")])
        with mock.patch.object(fred, "_get", return_value=payload) as g, \
                mock.patch.dict(os.environ, {"FRED_API_KEY": KEY}):
            rows = fred.fetch_first_release("UNRATE", start="2020-04-01")
        self.assertEqual(rows, [("2020-04-01", 14.7, "2020-05-08")])
        self.assertEqual(query(g.call_args[0][0])["observation_start"], "2020-04-01")

    def test_needs_the_key(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FRED_API_KEY", None)
            self.assertFalse(fred.has_api_key())
            with mock.patch.object(fred, "_get", side_effect=AssertionError("no request without a key")):
                with self.assertRaises(fred.FredError):
                    fred.fetch_first_release("UNRATE")

    def test_retry_on_429(self):
        seq = [FetchError("HTTP 429", status=429), FetchError("HTTP 429", status=429), api_payload(self.UNRATE)]
        sleeps = []

        def fake_get(url):
            x = seq.pop(0)
            if isinstance(x, Exception):
                raise x
            return x
        with mock.patch.object(fred, "_get", fake_get), mock.patch.object(fred, "_sleep", sleeps.append), \
                mock.patch.dict(os.environ, {"FRED_API_KEY": KEY}):
            self.assertEqual(len(fred.fetch_first_release("UNRATE")), 3)
        self.assertEqual(len(sleeps), 1)          # first try (no retry) -> 429 -> retried with back-off
        self.assertEqual(seq, [])

    def test_gateway_timeout_falls_back_to_observation_windows(self):
        seen = []
        obs = [(d, "0.1", (dt.date.fromisoformat(d) + dt.timedelta(days=5)).isoformat())
               for d in weekdays("2011-05-27", "2020-12-31")[::5]]

        def fake_get(url):
            q = query(url)
            if url.startswith(fred.VINTAGES_URL):
                self.assertEqual(q["api_key"], KEY)
                return json.dumps({"vintage_dates": ["2011-05-25"]}).encode()
            if "observation_end" not in q:
                raise FetchError("HTTP 504", status=504)
            seen.append((q["observation_start"], q["observation_end"]))
            return api_payload([r for r in obs if q["observation_start"] <= r[0] <= q["observation_end"]])
        with mock.patch.object(fred, "_get", fake_get), mock.patch.object(fred, "_sleep", lambda s: None), \
                mock.patch.dict(os.environ, {"FRED_API_KEY": KEY}):
            rows = fred.fetch_first_release("NFCI", today=dt.date(2020, 12, 31))
        self.assertEqual(len(rows), len(obs))
        self.assertEqual(seen[0][0], "2010-05-24")             # a year before the first vintage
        self.assertEqual(seen[-1][1], "2020-12-31")
        for (_a, b), (c, _d) in zip(seen, seen[1:]):          # contiguous, non-overlapping windows
            self.assertEqual(dt.date.fromisoformat(b) + dt.timedelta(days=1), dt.date.fromisoformat(c))

    def test_key_never_in_exception_text(self):
        def check(fake_get):
            with mock.patch.object(fred, "_get", fake_get), mock.patch.object(fred, "_sleep", lambda s: None), \
                    mock.patch.dict(os.environ, {"FRED_API_KEY": KEY}):
                with self.assertRaises(fred.FredError) as cm:
                    fred.fetch_first_release("UNRATE")
            exc = cm.exception
            text = " ".join([str(exc), repr(exc), str(exc.__cause__ or "")])
            self.assertNotIn(KEY, text)
            self.assertNotIn("api_key", text)
            self.assertIsNone(exc.__cause__)
            self.assertTrue(exc.__suppress_context__)

        def http_400(url):
            raise FetchError("HTTP 400", status=400, body=("bad api_key " + url).encode())
        check(http_400)

        def always_504(url):
            raise FetchError("HTTP 504", status=504)
        check(always_504)

        check(lambda url: b"<html>not json " + url.encode())


# ============================================================================================ store.py
class TestStoreMigration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fs2mig")
        self.path = os.path.join(self.tmp, "research.db")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_old_schema_gains_published_and_keeps_rows(self):
        c = sqlite3.connect(self.path)
        c.execute("CREATE TABLE macro(series TEXT NOT NULL, date TEXT NOT NULL, value REAL, PRIMARY KEY(series, date))")
        c.execute("INSERT INTO macro VALUES('UNRATE', '2020-04-01', 14.8)")
        c.commit()
        c.close()
        s = Store(self.path)
        try:
            cols = [r[1] for r in s._q("PRAGMA table_info(macro)")]
            self.assertIn("published", cols)
            self.assertEqual(s.macro("UNRATE"), [("2020-04-01", 14.8)])
            self.assertEqual(s.macro_rows("UNRATE"), [("2020-04-01", 14.8, None)])
            self.assertIsNone(s.macro_kind("UNRATE"))
            v = s.data_version()
            self.assertEqual(s.upsert_macro("UNRATE", [("2020-04-01", 14.7, "2020-05-08"), ("2020-05-01", 13.3)]), 2)
            self.assertEqual(s.macro_rows("UNRATE"), [("2020-04-01", 14.7, "2020-05-08"), ("2020-05-01", 13.3, None)])
            self.assertEqual(s.macro("UNRATE"), [("2020-04-01", 14.7), ("2020-05-01", 13.3)])
            self.assertNotEqual(s.data_version(), v)
            self.assertEqual(s.upsert_macro("UNRATE", [("2020-04-01", 14.7, dt.date(2020, 5, 8))]), 0)
            with self.assertRaises(ValueError):
                s.upsert_macro("UNRATE", [("2020-04-01",)])
        finally:
            s.close()
        s2 = Store(self.path)                     # re-opening an already migrated DB is a no-op
        try:
            self.assertEqual(len(s2.macro_rows("UNRATE")), 2)
        finally:
            s2.close()

    def test_replace_and_kind(self):
        s = Store(self.path)
        try:
            s.upsert_macro("NFCI", [("1990-01-05", 0.5), ("2020-01-03", -0.4)])
            v = s.data_version()
            s.set_macro_kind("NFCI", "latest_vintage")
            self.assertEqual(s.macro_kind("NFCI"), "latest_vintage")
            self.assertNotEqual(s.data_version(), v)
            v = s.data_version()
            s.set_macro_kind("NFCI", "latest_vintage")              # unchanged kind: no version bump
            self.assertEqual(s.data_version(), v)
            s.replace_macro("NFCI", [("2020-01-03", -0.5, "2020-01-08")], kind="first_release")
            self.assertEqual(s.macro_rows("NFCI"), [("2020-01-03", -0.5, "2020-01-08")])
            self.assertEqual(s.macro_kind("NFCI"), "first_release")
            with self.assertRaises(ValueError):
                s.set_macro_kind("NFCI", "whatever")
        finally:
            s.close()


# ============================================================================================ align.py
class TestPointInTime(StoreCase):
    def test_value_visible_exactly_from_its_publication_date(self):
        self.calendar()
        # published on a Friday (2020-05-08) and on a Saturday (-> visible Monday 2020-06-08)
        self.store.replace_macro("UNRATE", [("2020-03-01", 4.4, "2020-04-03"), ("2020-04-01", 14.7, "2020-05-08"),
                                            ("2020-05-01", 13.3, "2020-06-06")], kind="first_release")
        p = Panel(self.store)
        s = dict(zip(self.days, p.macro("UNRATE")))
        self.assertIsNone(s["2020-04-02"])
        self.assertEqual(s["2020-04-03"], 4.4)
        self.assertEqual(s["2020-05-07"], 4.4)
        self.assertEqual(s["2020-05-08"], 14.7)                  # the jobs report day, not 2020-04-01 + 35 days
        self.assertEqual(s["2020-06-05"], 14.7)
        self.assertEqual(s["2020-06-08"], 13.3)
        # the fixed-lag guess (35 days) would have shown April's value on 2020-05-06, before it was published
        self.store.set_macro_kind("UNRATE", "latest_vintage")
        self.store.upsert_macro("UNRATE", [("2020-04-01", 14.7)])  # published unknown -> lag fallback
        s2 = dict(zip(self.days, Panel(self.store).macro("UNRATE")))
        self.assertEqual(s2["2020-05-06"], 14.7)

    def test_transform_uses_only_visible_observations(self):
        self.calendar("2019-01-01", "2020-12-31")
        months = [f"{y}-{m:02d}-01" for y in (2019, 2020) for m in range(1, 13)]
        # each month published on the 12th of the next month, except 2019-12 which came out late (2020-02-20)
        rows = []
        for i, d in enumerate(months):
            pub = (dt.date.fromisoformat(d) + dt.timedelta(days=40)).replace(day=12).isoformat()
            rows.append((d, 4.0 + 0.1 * (i % 7), "2020-02-20" if d == "2019-12-01" else pub))
        self.store.replace_macro("UNRATE", rows, kind="first_release")
        s = dict(zip(self.days, Panel(self.store).macro("UNRATE", transform=sahm_gap)))
        expected = dict(sahm_gap([(d, v) for d, v, _p in rows]))
        # January 2020 was published on 2020-02-12, but its Sahm gap averages in December 2019, public only on
        # 2020-02-20: the gap is shown from then, never before
        self.assertIsNone(s["2020-02-12"])
        self.assertIsNone(s["2020-02-19"])
        self.assertAlmostEqual(s["2020-02-20"], expected["2020-01-01"])
        self.assertAlmostEqual(s["2020-03-12"], expected["2020-02-01"])
        self.assertAlmostEqual(s["2020-03-11"], expected["2020-01-01"])

    def test_first_release_ignores_later_revisions(self):
        """A revision in the latest vintage changes history in latest-vintage mode, not in first-release mode."""
        self.calendar("2020-01-01", "2020-12-31")
        first = [("2020-03-01", 4.4, "2020-04-03"), ("2020-04-01", 14.7, "2020-05-08"),
                 ("2020-05-01", 13.3, "2020-06-05"), ("2020-06-01", 11.1, "2020-07-02")]
        revised = [("2020-03-01", 4.4), ("2020-04-01", 14.8), ("2020-05-01", 13.2), ("2020-06-01", 11.0)]
        fr = mock.Mock(side_effect=lambda sid, start=None: [r for r in first if start is None or r[0] >= start])
        lv = mock.Mock(side_effect=lambda sid, start=None: [r for r in revised if start is None or r[0] >= start])
        with mock.patch.object(fred, "fetch_first_release", fr), mock.patch.object(fred, "fetch_series", lv):
            with mock.patch.dict(os.environ, {"FRED_API_KEY": KEY}):
                refresh.refresh_fred(self.store, "UNRATE")
            a = dict(zip(self.days, Panel(self.store).macro("UNRATE")))
            other = Store(os.path.join(self.tmp, "nokey.db"))
            try:
                other.upsert_prices("SPY", [{"date": d, "close": 1.0, "adj_close": 1.0} for d in self.days])
                with mock.patch.dict(os.environ, {}, clear=False):
                    os.environ.pop("FRED_API_KEY", None)
                    refresh.refresh_fred(other, "UNRATE")
                self.assertEqual(other.macro_kind("UNRATE"), "latest_vintage")
                b = dict(zip(self.days, Panel(other).macro("UNRATE")))
            finally:
                other.close()
        self.assertEqual(self.store.macro_kind("UNRATE"), "first_release")
        self.assertEqual((a["2020-05-08"], a["2020-06-05"], a["2020-07-02"]), (14.7, 13.3, 11.1))
        self.assertEqual((b["2020-05-08"], b["2020-06-05"], b["2020-07-06"]), (14.8, 13.2, 11.0))  # the leak
        self.assertIsNone(a["2020-04-02"])


# ============================================================================================ refresh.py
class TestRefreshKinds(StoreCase):
    def setUp(self):
        super().setUp()
        self.first = [("2020-03-01", 4.4, "2020-04-03"), ("2020-04-01", 14.7, "2020-05-08")]
        self.calls = []

        def fr(sid, start=None):
            self.calls.append(("first", sid, start))
            return [r for r in self.first if start is None or r[0] >= start]

        def lv(sid, start=None):
            self.calls.append(("latest", sid, start))
            return [("1948-01-01", 3.4), ("2020-03-01", 4.4), ("2020-04-01", 14.8)]
        self.patches = [mock.patch.object(fred, "fetch_first_release", fr),
                        mock.patch.object(fred, "fetch_series", lv), mock.patch.dict(os.environ, {}, clear=False)]
        for p in self.patches:
            p.start()
        os.environ.pop("FRED_API_KEY", None)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        super().tearDown()

    def test_switch_replaces_then_incremental_and_daily_untouched(self):
        refresh.refresh_fred(self.store, "UNRATE")                  # no key: latest vintage, as before
        self.assertEqual(self.store.macro_kind("UNRATE"), "latest_vintage")
        self.assertEqual(len(self.store.macro("UNRATE")), 3)
        os.environ["FRED_API_KEY"] = KEY
        refresh.refresh_fred(self.store, "UNRATE")                  # key: full first-release download ...
        self.assertEqual(self.calls[-1], ("first", "UNRATE", None))
        self.assertEqual(self.store.macro_kind("UNRATE"), "first_release")
        self.assertEqual(self.store.macro_rows("UNRATE"), [("2020-03-01", 4.4, "2020-04-03"),
                                                           ("2020-04-01", 14.7, "2020-05-08")])  # ... no lingering
        self.first.append(("2020-05-01", 13.3, "2020-06-05"))
        self.assertEqual(refresh.refresh_fred(self.store, "UNRATE"), 1)   # incremental afterwards
        self.assertEqual(self.calls[-1], ("first", "UNRATE", "2020-03-02"))
        refresh.refresh_fred(self.store, "DGS10")                   # daily market series: latest vintage
        self.assertEqual(self.calls[-1][0:2], ("latest", "DGS10"))
        self.assertEqual(self.store.macro_kind("DGS10"), "latest_vintage")
        self.assertTrue(refresh.first_release_series("NFCI"))
        self.assertFalse(refresh.first_release_series("VIXCLS"))
        # key gone again: the point-in-time values are kept, not overwritten by revised ones
        os.environ.pop("FRED_API_KEY")
        s = refresh.refresh(self.store, assets=[], macro=True, fundamentals=False)
        self.assertIn({"source": "fred", "key": "UNRATE",
                       "reason": "FRED_API_KEY not set: kept the stored first-release values"}, s["skipped"])
        self.assertEqual(self.store.macro_kind("UNRATE"), "first_release")
        self.assertEqual(self.store.macro("UNRATE")[1], ("2020-04-01", 14.7))


# ============================================================================================ NFCI policy
class TestNfciNeedsVintages(StoreCase):
    def setUp(self):
        super().setUp()
        self.calendar("2019-01-01", "2020-12-31")
        weeks = self.days[::5]
        self.nfci = [(d, -0.5 if i % 20 < 10 else 0.3) for i, d in enumerate(weeks)]

    def test_latest_vintage_nfci_is_not_used(self):
        self.store.upsert_macro("NFCI", self.nfci)
        self.store.set_macro_kind("NFCI", "latest_vintage")
        p = Panel(self.store)
        self.assertEqual(p.macro_kind("NFCI"), "latest_vintage")
        self.assertFalse(p.macro_usable("NFCI"))
        self.assertTrue(all(v is None for v in p.macro("NFCI")))
        m = macro_features(p)
        self.assertTrue(all(v is None for v in m["nfci"]))
        self.assertTrue(all(v is None for v in rg.compute(p, m)["liquidity"]))
        notes = p.data_notes()
        self.assertTrue(any("NFCI is excluded" in n for n in notes))
        self.assertTrue(any("latest revised values" in n and "NFCI" in n for n in notes))

    def test_unknown_kind_is_treated_as_revised(self):
        self.store.upsert_macro("NFCI", self.nfci)                 # a store filled before kinds were recorded
        self.assertTrue(all(v is None for v in Panel(self.store).macro("NFCI")))

    def test_first_release_nfci_is_used(self):
        rows = [(d, v, (dt.date.fromisoformat(d) + dt.timedelta(days=5)).isoformat()) for d, v in self.nfci]
        self.store.replace_macro("NFCI", rows, kind="first_release")
        p = Panel(self.store)
        m = macro_features(p)
        self.assertTrue(any(v is not None for v in m["nfci"]))
        liq = rg.compute(p, m)["liquidity"]
        self.assertEqual({x for x in liq if x}, {"liquidity_expansion", "liquidity_contraction"})
        notes = p.data_notes()
        self.assertTrue(any("first-release values" in n and "NFCI (from 2019-01)" in n for n in notes))
        self.assertFalse(any("NFCI is excluded" in n for n in notes))


if __name__ == "__main__":
    unittest.main()
