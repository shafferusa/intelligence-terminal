"""FX_CCS_PLAN rev 2 K2 / D12 (release 1.0.6.1, package P-A task 2): the RFR and term-curve data - parsers on recorded samples
(``tests/fixtures/terminal/rfr``), the providers' agent and refusal rules, the worker's ``rfr`` and ``curves`` jobs and the storage.

No network: every provider runs on a test getter (it records the URL, the User-Agent and the timeout of each request) or on the
FakeProvider.  The rules pinned here:
- the Bank of England is asked with ``GOV_UA`` on every request, never another agent; a 403 ("Access Denied", Akamai) is "unavailable"
  with the page's reference, exactly one request is sent and nothing is retried with another agent;
- an ECB HTML body (its error page) is unavailable, never parsed; the 60 s timeout is passed through; a 503 is retried once (2 attempts);
- SONIA comes from the Bank of England first, from FRED only when the Bank fails, and both as-of dates are recorded;
- every value is an exact Decimal from its text (the ESTR index 110.09953057, the BoE curve 3.8993903335967568)."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from datetime import date
from decimal import Decimal

from tests.terminal_helpers import make_runtime
from finsim.terminal.clock import FakeClock
from finsim.terminal.config import BROWSER_UA, GOV_UA, TERMINAL_UA
from finsim.terminal.quotes import parsers as P, providers as PV, worker as W
from finsim.terminal.quotes.cache import ChainCache, QuoteCache
from finsim.terminal.quotes.http import FetchError, ProviderHealth
from finsim.terminal.quotes.providers import BoeData, EcbData, FakeProvider, NyFed, ProviderSet
from finsim.terminal.quotes.rates import FRED_SERIES, CurveStore, FxStore, RateStore
from finsim.terminal.quotes.worker import QuoteWorker, register_all

D = Decimal
FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "terminal", "rfr")


def raw(name: str) -> bytes:
    with open(os.path.join(FIX, name), "rb") as fh:
        return fh.read()


class Getter:
    """A test getter: answers from a list (bytes or an exception), records (url, ua, timeout, accept) of every call."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, url, ua, timeout, accept):
        self.calls.append((url, ua, timeout, accept))
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(a, Exception):
            raise a
        return a


class ParserTests(unittest.TestCase):
    def test_ecb_estr_and_index_are_exact(self):
        got = P.parse_ecb_csv_exact(raw("ecb_estr.csv"))
        self.assertEqual((date(2026, 10, 27), D("2.44")), got["EST.B.EU000A2X2A25.WT"][-1])
        idx = P.parse_ecb_csv_exact(raw("ecb_estr_index.csv"))["EST.B.EU000A2QQF08.CI"]
        self.assertEqual([(date(2026, 10, 26), D("110.08460443")), (date(2026, 10, 27), D("110.09206878")), (date(2026, 10, 28), D("110.09953057"))], idx[-3:])

    def test_ecb_aaa_spot_and_svensson(self):
        spot, sv = PV.ecb_spot_from(P.parse_ecb_csv_exact(raw("ecb_aaa_spot.csv")))
        d = date(2026, 10, 27)
        self.assertEqual({"3M": D("2.5426425335"), "6M": D("2.6935531523"), "9M": D("2.8018255465"), "1Y": D("2.8787598685"), "2Y": D("3.015032229"),
                          "3Y": D("3.0510541181"), "5Y": D("3.1132303106"), "7Y": D("3.2121511745"), "10Y": D("3.3710900506")}, spot[d])
        self.assertEqual(D("13.7690739233"), sv[d]["TAU2"])
        self.assertEqual(6, len(sv[d]))

    def test_nyfed_sofr_index(self):
        self.assertEqual([(date(2026, 10, 26), D("1.26439948")), (date(2026, 10, 27), D("1.26453751")), (date(2026, 10, 28), D("1.26467626"))],
                         P.parse_nyfed_index(raw("nyfed_sofrai.json"))[-3:])

    def test_boe_database_csv(self):
        got = P.parse_boe_iadb(raw("boe_iadb.csv"))
        self.assertEqual((date(2026, 10, 26), D("3.7328")), got["IUDSOIA"][-1])             # 10-27's SONIA cell is blank: no observation
        self.assertEqual((date(2026, 10, 27), D("121.79978731")), got["IUDZOS2"][-1])

    def test_boe_ois_workbook_found_by_name(self):
        # the synthetic workbook keeps the Bank's layout: the target sheet is the second sheet (part sheet7.xml), the "months:" row reads
        # 1.0000000400000015-style values, a "#VALUE!" error row; two dated rows (Excel serials 46321 / 46322 = 2026-10-26 / 10-27)
        got = P.parse_boe_ois_zip(raw("boe_ois_two_rows.zip"))
        self.assertEqual([date(2026, 10, 26), date(2026, 10, 27)], sorted(got))
        row = got[date(2026, 10, 27)]
        self.assertEqual(60, len(row))
        self.assertEqual((D("3.8993903335967568"), D("4.7274467574291172"), D("4.7298572369489031")), (row["1M"], row["24M"], row["60M"]))
        with self.assertRaises(FetchError):
            P.parse_boe_ois_zip(raw("boe_ois_two_rows.zip"), sheet="9. no such sheet")

    def test_html_bodies_are_unavailable_never_parsed(self):
        for fn, name in ((P.parse_boe_iadb, "boe_access_denied.html"), (P.parse_boe_ois_zip, "boe_access_denied.html"),
                         (P.parse_ecb_csv_exact, "ecb_error.html"), (P.parse_nyfed_index, "ecb_error.html")):
            with self.assertRaises(FetchError) as cm:
                fn(raw(name))
            self.assertFalse(cm.exception.retryable, name)
            self.assertIn("HTML", str(cm.exception))
        self.assertEqual("18.2378ce17.1793223672.3a6540d3", P.boe_refusal_reference(raw("boe_access_denied.html")))


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock("2026-10-29T10:00:00-04:00")
        self.health = ProviderHealth(lambda: int(self.clock.epoch()))

    def test_bank_of_england_gets_gov_ua_on_every_request(self):
        g = Getter(raw("boe_iadb.csv"), raw("boe_ois_two_rows.zip"))
        boe = BoeData(self.clock, self.health, getter=g, sleep=lambda s: None)
        got = boe.iadb()
        self.assertEqual("Bank of England database (IUDSOIA)", got["IUDSOIA"].source)
        self.assertEqual(2, len(boe.ois_curve()))
        self.assertEqual([GOV_UA, GOV_UA], [ua for _u, ua, _t, _a in g.calls])
        self.assertNotIn(TERMINAL_UA, [c[1] for c in g.calls])
        self.assertNotIn(BROWSER_UA, [c[1] for c in g.calls])
        self.assertIn("Datefrom=29/Sep/2026", g.calls[0][0])                     # 30 days back, month names never from the locale
        self.assertIn("SeriesCodes=IUDSOIA,IUDZOS2", g.calls[0][0])
        self.assertEqual((PV.RFR_TIMEOUT_S, PV.RFR_TIMEOUT_S), (g.calls[0][2], g.calls[1][2]))
        self.assertTrue(self.health.get("boe")["ok"])

    def test_a_403_is_unavailable_with_its_reference_and_one_request_only(self):
        g = Getter(FetchError("HTTP 403", status=403, body=raw("boe_access_denied.html")))
        boe = BoeData(self.clock, self.health, getter=g, sleep=lambda s: None)
        with self.assertRaises(FetchError) as cm:
            boe.iadb()
        e = cm.exception
        self.assertEqual((403, "refused", False), (e.status, e.kind, e.retryable))
        self.assertIn("reference 18.2378ce17.1793223672.3a6540d3", str(e))
        self.assertIn("no other agent is tried", str(e))
        self.assertEqual(1, len(g.calls))                                        # never retried, never with another agent
        self.assertEqual(GOV_UA, g.calls[0][1])
        row = self.health.get("boe")
        self.assertEqual((False, True), (row["ok"], row["refused_ua"]))
        self.assertIn("18.2378ce17.1793223672.3a6540d3", row["last_error"])
        self.assertEqual(1, row["requests_today"])
        self.assertEqual("18.2378ce17.1793223672.3a6540d3", boe.refused["reference"])

    def test_ecb_html_is_unavailable_the_timeout_and_two_attempts(self):
        g = Getter(raw("ecb_error.html"))
        ecb = EcbData(self.clock, self.health, getter=g, sleep=lambda s: None)
        with self.assertRaises(FetchError):
            ecb.estr()
        self.assertEqual((1, 60.0, TERMINAL_UA), (len(g.calls), g.calls[0][2], g.calls[0][1]))
        g2 = Getter(FetchError("HTTP 503", status=503), raw("ecb_estr.csv"))
        ecb2 = EcbData(self.clock, self.health, getter=g2, sleep=lambda s: None)
        obs = ecb2.estr()
        self.assertEqual(2, len(g2.calls))                                       # the 503 retried once: 2 attempts
        self.assertEqual((PV.ESTR_SOURCE, (date(2026, 10, 27), D("2.44"))), (obs.source, obs[-1]))
        g3 = Getter(FetchError("HTTP 503", status=503))
        with self.assertRaises(FetchError):
            EcbData(self.clock, self.health, getter=g3, sleep=lambda s: None).aaa_spot()
        self.assertEqual(2, len(g3.calls))
        self.assertIn("SR_3M+SR_6M", g3.calls[0][0])

    def test_nyfed_sofr_index_provider(self):
        g = Getter(raw("nyfed_sofrai.json"))
        obs = NyFed(self.clock, self.health, getter=g, sleep=lambda s: None).sofr_index(5)
        self.assertEqual((PV.SOFR_INDEX_SOURCE, D("1.26467626")), (obs.source, obs[-1][1]))
        self.assertIn("/sofrai/last/5.json", g.calls[0][0])


class _FakeWorker:
    """What the jobs read of a QuoteWorker: providers, refresher.rates, clock, _beat."""

    def __init__(self, providers, rates, clock):
        self.providers, self.clock = providers, clock
        self.refresher = type("R", (), {"rates": rates})()
        self.beats = 0

    def _beat(self):
        self.beats += 1


def configured_fake(clock) -> FakeProvider:
    fp = FakeProvider(clock)
    fp.set_rfr("ESTR", [("2026-10-26", "2.441"), ("2026-10-27", "2.440")])
    fp.set_rfr("ESTR_INDEX", [("2026-10-27", "110.09206878"), ("2026-10-28", "110.09953057")])
    fp.set_rfr("SOFR_INDEX", [("2026-10-27", "1.26453751"), ("2026-10-28", "1.26467626")])
    fp.set_rfr("IUDSOIA", [("2026-10-23", "3.7338"), ("2026-10-26", "3.7328")])
    fp.set_rfr("IUDZOS2", [("2026-10-26", "121.7873323"), ("2026-10-27", "121.79978731")])
    fp.set_boe_curve(raw=raw("boe_ois_two_rows.zip"))
    fp.set_ecb_spot(raw=raw("ecb_aaa_spot.csv"))
    return fp


class JobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-rfr-")
        self.rt = make_runtime(self.tmp, at="2026-10-29T09:30:00-04:00")
        register_all(self.rt.bus)
        self.rates = RateStore(self.rt.db, self.rt.bus)
        self.fp = configured_fake(self.rt.clock)
        self.providers = ProviderSet.offline_set(self.fp, self.rt.clock)
        self.w = _FakeWorker(self.providers, self.rates, self.rt.clock)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rfr_stores_every_series_with_its_source_and_fred_is_not_asked(self):
        job = W.RfrJob(self.w)
        out = job(None, self.rt.clock.epoch())
        self.assertIn("stored:", out)
        r = self.rates
        self.assertEqual((date(2026, 10, 27), D("2.44")), r.latest("ESTR"))
        self.assertEqual((date(2026, 10, 28), D("110.09953057")), r.latest("ESTR_INDEX"))
        self.assertEqual((date(2026, 10, 28), D("1.26467626")), r.latest("SOFR_INDEX"))
        self.assertEqual((date(2026, 10, 26), D("3.7328")), r.latest("IUDSOIA"))
        self.assertEqual((date(2026, 10, 27), D("121.79978731")), r.latest("SONIA_INDEX"))
        self.assertEqual("Bank of England database (IUDSOIA)", r.observation("IUDSOIA", date(2026, 10, 29))["source"])
        self.assertEqual(0, self.fp.calls.get("fred", 0))                     # FRED only when a primary fails
        with self.rt.db.reader() as conn:
            rows = {r_["series"]: (r_["n"], r_["source"]) for r_ in conn.execute(
                "SELECT series, COUNT(*) AS n, MAX(source) AS source FROM curve_points WHERE tenor='OBS' GROUP BY series").fetchall()}
        self.assertEqual((2, "Bank of England database (IUDZOS2)"), rows["SONIA_INDEX"])
        self.assertEqual((2, PV.ESTR_SOURCE), rows["ESTR"])
        self.assertTrue(r.fetch_status["SOFR_INDEX"]["ok"])
        # 09:30 ET: ESTR and SONIA of 10-28 (the previous business day) are not in yet -> asked again 30 min later, until 12:00 ET
        self.assertFalse(job.arrived(r, date(2026, 10, 29)))
        self.rt.clock.advance(1800)
        self.fp.set_rfr("ESTR", [("2026-10-27", "2.440"), ("2026-10-28", "2.439")])
        self.fp.set_rfr("IUDSOIA", [("2026-10-26", "3.7328"), ("2026-10-28", "3.7330")])
        self.fp.set_rfr("IUDZOS2", [("2026-10-27", "121.79978731"), ("2026-10-28", "121.81224")])
        self.assertIn("stored:", job(None, self.rt.clock.epoch()))
        self.assertTrue(job.arrived(r, date(2026, 10, 29)))
        self.rt.clock.advance(1800)                                           # the prints are in: not before 3 h after the last fetch
        self.assertIn("not due", job(None, self.rt.clock.epoch()))
        self.rt.clock.advance(3 * 3600)
        self.assertIn("stored:", job(None, self.rt.clock.epoch()))

    def test_sonia_from_fred_only_when_the_bank_fails_and_both_dates_recorded(self):
        job = W.RfrJob(self.w)
        job(None, self.rt.clock.epoch())                                      # the Bank answers: IUDSOIA 10-26 from the Bank
        self.assertEqual(0, self.fp.calls.get("fred", 0))
        self.fp.fail("BOE_IADB", FetchError("Bank of England: HTTP 403 Access Denied (reference 18.x) - unavailable; no other agent is tried",
                                            status=403, retryable=False, kind="refused"))
        self.fp.set_rate("IUDSOIA", [("2026-10-26", "3.7328"), ("2026-10-27", "3.7331")])  # FRED has a newer print
        out = W.fetch_rfr(self.providers.rfr, self.rates, self.rt.clock.epoch(), date(2026, 10, 29), job)
        self.assertEqual(1, self.fp.calls["fred"])
        self.assertIn("IUDSOIA 2026-10-27 (FRED)", out)
        self.assertEqual((date(2026, 10, 27), D("3.7331")), self.rates.latest("IUDSOIA"))           # the newer print wins
        self.assertEqual({"Bank of England database (IUDSOIA)": "2026-10-26", "FRED (IUDSOIA)": "2026-10-27"}, self.rates.sources_seen("IUDSOIA"))
        self.assertIn("HTTP 403", self.rates.fetch_status["IUDSOIA"]["text"])
        self.assertIsNotNone(job.boe_paused_until)                            # the Bank is not asked again for 3 h
        calls = self.fp.calls.get("boe_iadb", 0)
        self.fp.clear_failure("BOE_IADB")
        W.fetch_rfr(self.providers.rfr, self.rates, self.rt.clock.epoch() + 60, date(2026, 10, 29), job)
        self.assertEqual(calls, self.fp.calls.get("boe_iadb", 0))
        self.assertEqual(2, self.fp.calls["fred"])

    def test_estr_and_sofr_index_fall_back_to_fred(self):
        self.fp.fail("ESTR", FetchError("HTTP 503", status=503))
        self.fp.fail("SOFR_INDEX", FetchError("HTTP 503", status=503))
        self.fp.set_rate("ECBESTRVOLWGTTRMDMNRT", [("2026-10-27", "2.440")])
        self.fp.set_rate("SOFRINDEX", [("2026-10-28", "1.26467626")])
        out = W.fetch_rfr(self.providers.rfr, self.rates, self.rt.clock.epoch(), date(2026, 10, 29))
        self.assertIn("ESTR 2026-10-27 (FRED)", out)
        self.assertEqual("FRED (ECBESTRVOLWGTTRMDMNRT)", self.rates.observation("ESTR", date(2026, 10, 29))["source"])
        self.assertEqual("FRED (SOFRINDEX)", self.rates.observation("SOFR_INDEX", date(2026, 10, 29))["source"])
        self.fp.fail("ESTR_INDEX", FetchError("HTTP 503", status=503))
        out = W.fetch_rfr(self.providers.rfr, self.rates, self.rt.clock.epoch(), date(2026, 10, 29))
        self.assertIn("unavailable: ESTR_INDEX", out)                         # no fallback for the ECB index: nothing invented

    def test_curves_store_new_days_once_and_cap_the_retries(self):
        job = W.CurvesJob(self.w)
        out = job(None, self.rt.clock.epoch())
        self.assertIn("boe: latest 2026-10-27, stored 2 day(s)", out)
        self.assertIn("ecb: latest 2026-10-27, stored 1 day(s)", out)
        g = self.rates.latest_grid("BOE_OIS_SPOT")
        self.assertEqual((date(2026, 10, 27), D("3.899390"), 60), (g.as_of, g.points["1M"], len(g.points)))     # 6 decimals of a percent
        self.assertEqual(D("2.542643"), self.rates.latest_grid("ECB_AAA_SPOT").points["3M"])
        self.assertEqual(D("13.76907392"), self.rates.latest_grid("ECB_AAA_SV").points["TAU2"])
        # 09:30 on 10-29: the previous business day is 10-28; neither grid has it -> retried hourly, at most 6 fetches a day each
        for _ in range(7):
            self.rt.clock.advance(3600)
            job(None, self.rt.clock.epoch())
        self.assertEqual(W.CURVES_MAX_TRIES, job.tries[("boe", "2026-10-29")])
        self.assertEqual(W.CURVES_MAX_TRIES, self.fp.calls["boe_curve"])
        # a grid of the previous business day makes the source current: nothing is fetched
        self.fp.set_boe_curve("2026-10-28", {"1M": "3.9"})
        job2 = W.CurvesJob(self.w)
        self.rates.put_grid("BOE_OIS_SPOT", "2026-10-28", {"1M": "3.9"}, "test", persist=False)
        self.assertIn("boe: current (2026-10-28)", job2(None, self.rt.clock.epoch()))

    def test_curve_403_pauses_the_bank_and_is_named_for_the_curve(self):
        job = W.CurvesJob(self.w)
        self.fp.fail("BOE_OIS_SPOT", FetchError("Bank of England: HTTP 403 Access Denied (reference 18.abc) - unavailable; no other agent is tried",
                                                status=403, retryable=False, kind="refused"))
        out = job(None, self.rt.clock.epoch())
        self.assertIn("boe: unavailable", out)
        self.assertIn("HTTP 403", self.rates.fetch_status["BOE_OIS_SPOT"]["text"])
        self.rt.clock.advance(3600)
        self.assertIn("boe: paused after a 403", job(None, self.rt.clock.epoch()))

    def test_offline_without_rfr_data_the_jobs_stay_quiet(self):
        fp = FakeProvider(self.rt.clock)
        w = _FakeWorker(ProviderSet.offline_set(fp, self.rt.clock), RateStore(), self.rt.clock)
        self.assertEqual("offline: no RFR data set", W.RfrJob(w)(None, self.rt.clock.epoch()))
        self.assertEqual("offline: no RFR data set", W.CurvesJob(w)(None, self.rt.clock.epoch()))
        self.assertEqual({}, fp.calls)

    def test_sonia_is_no_longer_a_fred_slot_series(self):
        self.assertNotIn("IUDSOIA", FRED_SERIES)
        self.assertIn("SOFR", FRED_SERIES)


class WorkerIntegrationTests(unittest.TestCase):
    def test_the_worker_runs_both_jobs_and_the_health_rows(self):
        tmp = tempfile.mkdtemp(prefix="fst-rfrw-")
        rt = make_runtime(tmp, at="2026-10-29T09:30:00-04:00")
        try:
            register_all(rt.bus)
            fp = configured_fake(rt.clock)
            providers = ProviderSet.offline_set(fp, rt.clock)
            cache = QuoteCache(rt.db, rt.instruments, rt.sessions, rt.clock, rt.bus, rt.log)
            rates = RateStore(rt.db, rt.bus)
            worker = QuoteWorker(cache, ChainCache(rt.clock), rt.instruments, providers, rt.sessions, rt.bus, None, rt.clock, rt.log, fetch_threads=0,
                                 db=rt.db, services=rt, curve=CurveStore(rt.db, rt.bus, rt.clock), rates=rates, fxstore=FxStore(rt.db, rt.bus), cal=rt.cal)
            rep = worker.tick()
            self.assertTrue({"rfr", "curves"} <= set(rep["refreshers"]))
            self.assertEqual(D("110.09953057"), rates.latest("ESTR_INDEX")[1])
            self.assertEqual(date(2026, 10, 27), rates.latest_grid("BOE_OIS_SPOT").as_of)
            st = worker.refreshers()
            self.assertIn("stored", st["rfr"]["last_result"])
            self.assertEqual(1800, st["rfr"]["interval_s"])
            health = providers.health.snapshot()
            self.assertTrue(all(health[p]["ok"] for p in ("ecb", "nyfed", "boe", "rfr", "curves")))
            self.assertEqual("Bank of England", W.PROVIDER_LABEL["boe"])
        finally:
            rt.close()
            shutil.rmtree(tmp, ignore_errors=True)


class StorageTests(unittest.TestCase):
    def test_grids_and_sources_survive_a_restart(self):
        tmp = tempfile.mkdtemp(prefix="fst-rfrs-")
        rt = make_runtime(tmp)
        try:
            register_all(rt.bus)
            rs = RateStore(rt.db, rt.bus)
            rs.put("IUDSOIA", [(date(2026, 10, 23), D("3.7338"))], "FRED (IUDSOIA)")
            rs.put("IUDSOIA", [(date(2026, 10, 26), D("3.7328"))], "Bank of England database (IUDSOIA)")
            rs.put_grid("BOE_OIS_SPOT", "2026-10-27", {"1M": "3.8993903335967568", "2M": "3.9556859143169798"}, "BoE", 123)
            again = RateStore(rt.db)
            again.load_persisted()
            self.assertEqual("FRED (IUDSOIA)", again.observation("IUDSOIA", date(2026, 10, 23), exact=True)["source"])
            self.assertEqual("Bank of England database (IUDSOIA)", again.observation("IUDSOIA", date(2026, 10, 27))["source"])
            g = again.grid_on_or_before("BOE_OIS_SPOT", date(2026, 10, 29))
            self.assertEqual((date(2026, 10, 27), D("3.899390"), D("3.955686"), 123), (g.as_of, g.points["1M"], g.points["2M"], g.fetched_ts))
            self.assertIsNone(again.grid_on_or_before("BOE_OIS_SPOT", date(2026, 10, 26)))
            with self.assertRaises(ValueError):
                rs.put_grid("UST_PAR", "2026-10-27", {"1Y": "4.5"})
        finally:
            rt.close()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
