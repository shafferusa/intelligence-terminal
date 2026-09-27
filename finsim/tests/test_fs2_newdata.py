"""New data sources (finsim2/data: secevents, cftc, eia, cryptoderiv, calendar): parsers on fixtures, point-in-time rules."""
import datetime as dt
import io
import math
import os
import shutil
import tempfile
import unittest
import zipfile

from finsim2.data import calendar as C
from finsim2.data import cftc
from finsim2.data import cryptoderiv as X
from finsim2.data import eia
from finsim2.data import secevents as E


class Time(unittest.TestCase):
    def test_new_york_daylight_saving(self):
        self.assertEqual(E.new_york(dt.datetime(2025, 7, 31, 20, 30)).strftime("%H:%M"), "16:30")       # EDT
        self.assertEqual(E.new_york(dt.datetime(2026, 1, 29, 21, 30)).strftime("%H:%M"), "16:30")       # EST
        self.assertEqual(E.new_york(dt.datetime(2025, 3, 9, 6, 59)).strftime("%H:%M"), "01:59")         # before the switch
        self.assertEqual(E.new_york(dt.datetime(2025, 3, 9, 7, 0)).strftime("%H:%M"), "03:00")          # 2 am jumps to 3 am
        self.assertEqual(E.new_york(dt.datetime(2005, 4, 4, 12, 0)).strftime("%H:%M"), "08:00")         # pre-2007 rules: DST from 3 Apr 2005


def _insider_zip() -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("SUBMISSION.tsv", "ACCESSION_NUMBER\tFILING_DATE\tDOCUMENT_TYPE\tISSUERCIK\tAFF10B5ONE\n"
                   "A1\t16-MAY-2025\t4\t0000731766\t0\nA2\t16-MAY-2025\t4\t0000731766\t0\nA3\t20-MAY-2025\t4/A\t0000731766\t0\n"
                   "A4\t21-MAY-2025\t4\t0000320193\t1\nA5\t22-MAY-2025\t4\t0000999999\t0\n")
        z.writestr("REPORTINGOWNER.tsv", "ACCESSION_NUMBER\tRPTOWNER_RELATIONSHIP\tRPTOWNER_TITLE\tRPTOWNER_TXT\n"
                   "A1\tDirector,Officer\tChief Executive Officer\t\nA2\tDirector\t\t\nA4\tOfficer\tSVP\t\n")
        z.writestr("NONDERIV_TRANS.tsv", "ACCESSION_NUMBER\tTRANS_CODE\tTRANS_SHARES\tTRANS_PRICEPERSHARE\tSHRS_OWND_FOLWNG_TRANS\n"
                   "A1\tP\t1000\t300\t4000\nA1\tA\t500\t0\t4500\nA2\tP\t100\t305\t100\nA3\tP\t99999\t300\t99999\n"
                   "A4\tS\t200\t200\t800\nA4\tM\t50\t10\t850\nA5\tP\t10\t10\t10\n")
    return b.getvalue()


class SecInsider(unittest.TestCase):
    def test_open_market_only_originals_only_and_roles(self):
        agg = E.parse_insider_zip(_insider_zip(), {731766: "UNH", 320193: "AAPL"})
        u = agg[("UNH", "2025-05-16")]
        self.assertAlmostEqual(u["buy_value"], 1000 * 300 + 100 * 305)
        self.assertEqual(u["buyers"], 2.0)
        self.assertAlmostEqual(u["ceo_cfo_buy_value"], 300000)
        self.assertAlmostEqual(u["director_buy_value"], 300000 + 30500)
        self.assertAlmostEqual(u["max_buy_frac"], 1.0)                       # A2 bought its whole holding
        self.assertNotIn(("UNH", "2025-05-20"), agg, "amendments are skipped")
        a = agg[("AAPL", "2025-05-21")]
        self.assertAlmostEqual(a["sell_value"], 40000)
        self.assertAlmostEqual(a["planned_sell_value"], 40000, msg="10b5-1 box")
        self.assertNotIn("buy_value", a, "option exercise (M) is not a purchase")
        self.assertEqual(len(agg), 2, "unmapped issuers are ignored")

    def test_form4_xml_and_entity_refusal(self):
        doc = b"""<?xml version="1.0"?><ownershipDocument><documentType>4</documentType>
        <reportingOwner><reportingOwnerRelationship><isDirector>0</isDirector><isOfficer>1</isOfficer>
        <officerTitle>Chief Financial Officer</officerTitle></reportingOwnerRelationship></reportingOwner><aff10b5One>1</aff10b5One>
        <nonDerivativeTable><nonDerivativeTransaction><transactionCoding><transactionCode>S</transactionCode></transactionCoding>
        <transactionAmounts><transactionShares><value>10</value></transactionShares><transactionPricePerShare><value>50</value></transactionPricePerShare></transactionAmounts>
        <postTransactionAmounts><sharesOwnedFollowingTransaction><value>90</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
        </nonDerivativeTransaction><nonDerivativeTransaction><transactionCoding><transactionCode>A</transactionCode></transactionCoding>
        <transactionAmounts><transactionShares><value>5</value></transactionShares><transactionPricePerShare><value>0</value></transactionPricePerShare></transactionAmounts>
        </nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>"""
        p = E.parse_form4_xml(doc)
        self.assertTrue(p["roles"]["officer"] and p["roles"]["ceo_cfo"] and p["plan"])
        self.assertEqual(p["transactions"], [("S", 10.0, 50.0, 90.0)])
        self.assertIsNone(E.parse_form4_xml(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><ownershipDocument/>'))
        self.assertIsNone(E.parse_form4_xml(b"not xml"))


class Sec8K(unittest.TestCase):
    def test_items_release_time_and_publication(self):
        pages = [{"form": ["8-K", "8-K", "10-Q", "8-K/A", "8-K"],
                  "acceptanceDateTime": ["2025-07-31T20:30:25.000Z", "2025-08-05T13:00:00.000Z", "2025-08-01T12:00:00.000Z",
                                         "2025-08-06T12:00:00.000Z", "2025-08-05T14:10:00.000Z"],
                  "items": ["2.02,9.01", "5.02", "", "5.02", "8.01"]}]
        days = E.parse_8k(pages)
        self.assertEqual(days["2025-07-31"]["release_hhmm"], 1630.0)
        self.assertEqual(days["2025-07-31"]["item_2.02"], 1.0)
        self.assertEqual(days["2025-08-05"]["n_8k"], 2.0)
        self.assertNotIn("2025-08-06", days, "amendments are skipped")
        self.assertEqual(set(days), {"2025-07-31", "2025-08-05"})


class Cftc(unittest.TestCase):
    ROW = {"report_date_as_yyyy_mm_dd": "2024-03-05T00:00:00.000", "market_and_exchange_names": "CRUDE OIL, LIGHT SWEET - NEW YORK MERCANTILE EXCHANGE",
           "open_interest_all": "1700000", "m_money_positions_long_all": "300000", "m_money_positions_short_all": "90000",
           "m_money_positions_long_other": "1", "pct_of_oi_m_money_long_all": "17.6", "swap_positions_long_all": "200000",
           "swap__positions_short_all": "400000", "prod_merc_positions_long": "500000", "prod_merc_positions_short": "600000",
           "change_in_m_money_long_all": "5000"}

    def test_pattern_matched_fields_and_friday_release(self):
        rows, err = cftc.parse_rows([self.ROW], "disaggregated", "067651")
        self.assertIsNone(err)
        got = {f: v for _, _, f, v, _ in rows}
        self.assertEqual(got["mm_long"], 300000.0)
        self.assertEqual(got["mm_short"], 90000.0)
        self.assertEqual(got["swap_short"], 400000.0, "the schema's double-underscore quirk")
        self.assertEqual(got["pm_long"], 500000.0)
        self.assertTrue(all(r[0] == "cot:067651" and r[1] == "2024-03-05" and r[4] == "2024-03-09" for r in rows), "Tuesday -> Saturday")

    def test_wrong_contract_code_is_reported_not_mapped(self):
        rows, err = cftc.parse_rows([{**self.ROW, "market_and_exchange_names": "GOLD - COMMODITY EXCHANGE INC."}], "disaggregated", "067651")
        self.assertEqual(rows, [])
        self.assertIn("matches none of", err)

    def test_shutdown_reports_wait_for_the_catch_up(self):
        self.assertEqual(cftc.published("2019-01-08"), "2019-03-08")
        self.assertEqual(cftc.published("2019-02-05"), "2019-02-09")
        self.assertTrue(all(code in cftc.CONTRACTS for code, _ in cftc.LINKS.values()))


class Eia(unittest.TestCase):
    def test_weekly_rows_published_after_the_report(self):
        payload = {"response": {"total": 2, "data": [{"period": "2024-03-01", "value": 448500}, {"period": "2024-03-08", "value": "bad"}]}}
        rows = eia.parse(payload, "crude_stocks")
        self.assertEqual(rows, [("eia:US", "2024-03-01", "crude_stocks", 448500.0, "2024-03-07")])
        self.assertEqual(eia.parse(payload, "natgas_storage")[0][4], "2024-03-08")
        self.assertEqual(eia.parse({"error": "x"}, "crude_stocks"), [])

    def test_no_key_no_request(self):
        class Dummy:
            def put_alt(self, *a):
                raise AssertionError("nothing to store")
        old = os.environ.pop("EIA_API_KEY", None)
        try:
            self.assertIn("skipped", eia.refresh(Dummy()))
        finally:
            if old is not None:
                os.environ["EIA_API_KEY"] = old


class Crypto(unittest.TestCase):
    def test_parsers(self):
        t0 = 1704067200000          # 2024-01-01 00:00 UTC
        d = X.parse_deribit({"result": [{"timestamp": t0 + 3600000 * h, "interest_8h": 0.0001 * (h + 1)} for h in range(3)] + [{"timestamp": t0, "interest_8h": 5}]})
        self.assertEqual(d["2024-01-01"][1], 3, "absurd prints dropped")
        b = X.parse_binance_funding([{"fundingTime": t0, "fundingRate": "0.0001"}, {"fundingTime": t0 + 8 * 3600000, "fundingRate": "0.0003"}])
        self.assertAlmostEqual(b["2024-01-01"][0], 0.0004)
        p = X.parse_binance_premium([[t0, "0", "0", "0", "0.0012", "0"]])
        self.assertAlmostEqual(p["2024-01-01"], 0.0012)
        y = X.parse_yahoo_close({"chart": {"result": [{"timestamp": [t0 // 1000], "indicators": {"quote": [{"close": [42000.5]}]}}]}})
        self.assertEqual(y, {"2024-01-01": 42000.5})
        self.assertEqual(X.parse_yahoo_close({"chart": {"result": None}}), {})


class Calendar(unittest.TestCase):
    def test_fomc_pages(self):
        cur = ('<h4><a id="1">2026 FOMC Meetings</a></h4><div class="fomc-meeting__month col"><strong>Jan/Feb</strong></div>'
               '<div class="fomc-meeting__date col">31-1</div><div class="fomc-meeting__month col"><strong>March</strong></div>'
               '<div class="fomc-meeting__date col">17-18*</div><div class="fomc-meeting__month col"><strong>April</strong></div>'
               '<div class="fomc-meeting__date col">22 (notation vote)</div>')
        self.assertEqual(C.parse_fomc_current(cur), {"2026-02-01": 1.0, "2026-03-18": 2.0, "2026-04-22": 3.0})
        hist = ('<h5>March 2 (unscheduled) Meeting - 2020</h5><h5>March 17-18 (cancelled) Meeting - 2020</h5>'
                '<h5>April 28-29 Meeting - 2020</h5><h5>October 7 Conference Call - 2020</h5>')
        self.assertEqual(C.parse_fomc_historical(hist, 2020), {"2020-03-02": 3.0, "2020-04-29": 1.0, "2020-10-07": 3.0})

    def test_fred_finnhub_and_expected_next(self):
        self.assertEqual(C.parse_fred_dates({"release_dates": [{"date": "2024-01-11"}, {"date": "junk"}]}), ["2024-01-11"])
        rows = C.parse_finnhub({"earningsCalendar": [{"symbol": "AAPL", "date": "2026-10-29", "hour": "amc", "epsEstimate": 1.8},
                                                     {"symbol": "ZZZZ", "date": "2026-10-29"}, {"symbol": "AAPL", "date": "2026-01-01"}]},
                               {"AAPL": "AAPL"}, "2026-09-27")
        self.assertIn(("AAPL", "2026-10-29", "hour", 2.0, "2026-09-27"), rows)
        self.assertEqual(len([r for r in rows if r[2] == "scheduled"]), 1, "unknown symbols and past dates dropped")
        hist = ["2025-01-30", "2025-05-01", "2025-07-31", "2025-10-30", "2026-01-29"]
        self.assertEqual(C.expected_next(hist, "2026-03-01"), "2026-04-30")
        self.assertIsNone(C.expected_next(hist, "2024-01-01"))


class Orchestrator(unittest.TestCase):
    def test_nothing_stored_with_errors_is_a_failure(self):
        from finsim2.data import newdata as N
        self.assertEqual(N._judge({"rows": 0, "errors": ["HTTP 403"]})[0], "failed")
        self.assertEqual(N._judge({"skipped": "set EIA_API_KEY"})[0], "skipped")
        self.assertEqual(N._judge({"rows": 5, "notes": ["SOL: blocked"]})[0], "partial")
        self.assertEqual(N._judge({"events": {"rows": 3, "failed": []}, "insider": {"rows": 2}})[0], "ok")
        self.assertEqual(N._judge({"macro_rows": 10, "fomc_days": 2, "earnings_rows": 0, "errors": []})[0], "ok")


class Expansion(unittest.TestCase):
    def test_candidates_dollar_volume_and_sectors(self):
        from finsim2.data import expand as X2
        payload = {"fields": ["cik", "name", "ticker", "exchange"], "data": [
            [1, "Big Co", "BIG", "NYSE"], [2, "Big Co", "BIG-PA", "NYSE"], [3, "Blank Check Acquisition Corp", "BCAC", "Nasdaq"],
            [4, "Class B Co", "BRK-B", "NYSE"], [5, "Otc Co", "OTCC", "OTC"], [6, "Warrants", "ABCDW", "Nasdaq"], [7, "Five", "GOOGL", "Nasdaq"]]}
        self.assertEqual([c[0] for c in X2.candidates(payload)], ["BIG", "BRK-B", "GOOGL"])
        q = {"chart": {"result": [{"indicators": {"quote": [{"close": [10.0] * 60, "volume": [1000] * 59 + [None]}]}}]}}
        self.assertEqual(X2.dollar_volume(q), 10000.0)
        self.assertIsNone(X2.dollar_volume({"chart": {"result": [{"indicators": {"quote": [{"close": [1.0] * 10, "volume": [1] * 10}]}}]}}))
        self.assertEqual(X2.sector_of_sic(3674), "Information Technology")
        self.assertEqual(X2.sector_of_sic(6798), "Real Estate")
        self.assertIsNone(X2.sector_of_sic("x"))


class Imports(unittest.TestCase):
    def test_estimates_and_options_csv(self):
        from finsim2.data import imports as I
        ids = {"AAPL": "AAPL", "BRK-B": "BRK-B", "BRK.B": "BRK-B"}
        est = I.estimate_rows(io.StringIO("TICKER,STATPERS,FPI,MEASURE,MEANEST,NUMEST,NUMUP\nAAPL,20240105,6,EPS,2.10,38,4\n"
                                          "BRK.B,2024-01-05,FY1,REVENUE,370000,5,\nZZZ,2024-01-05,FQ1,EPS,1,1,1\nAAPL,2024-01-05,FY9,EPS,1,1,1\n"), ids)
        got = {(r[0], r[2]): (r[3], r[4]) for r in est["rows"]}
        self.assertEqual(got[("AAPL", "eps_fq1_mean")], (2.10, "2024-01-06"))
        self.assertEqual(got[("AAPL", "eps_fq1_n_analysts")][0], 38.0)
        self.assertIn(("BRK-B", "revenue_fy1_mean"), got)
        self.assertEqual(est["skipped"], {"unknown_ticker": 1, "bad_row": 1})
        opt = I.option_rows(io.StringIO("ticker,tradeDate,iv30d,pVolu,cVolu,published\nAAPL,2024-01-05,0.21,1000,2000,2024-01-05\n"), ids)
        self.assertIn(("AAPL", "2024-01-05", "iv30", 0.21, "2024-01-05"), opt["rows"])
        self.assertEqual(len(opt["rows"]), 3)


class Store(unittest.TestCase):
    def test_rows_land_in_alt_data_with_publication_dates(self):
        from finsim2.data.store import Store as St
        tmp = tempfile.mkdtemp()
        st = St(os.path.join(tmp, "x.db"))
        try:
            rows, _ = cftc.parse_rows([Cftc.ROW], "disaggregated", "067651")
            st.put_alt(cftc.DATASET, rows)
            got = st.alt(cftc.DATASET, "cot:067651")
            self.assertEqual(len(got), len(rows))
            self.assertTrue(all(r[4] == "2024-03-09" for r in got))
        finally:
            st.close()
            shutil.rmtree(tmp, ignore_errors=True)



class LaptopRunFixes(unittest.TestCase):
    """What the first real run on the laptop showed (2026-09-27)."""

    def setUp(self):
        from finsim2.data.store import Store as St
        self.tmp = tempfile.mkdtemp()
        self.st = St(os.path.join(self.tmp, "x.db"))

    def tearDown(self):
        self.st.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _equity(self, aid, cik, name="Co", **meta):
        self.st.upsert_asset({"id": aid, "name": name, "asset_class": "EQUITY", "sector": "Health Care", "country": "United States",
                              "currency": "USD", "yahoo": aid, "cik": cik, "meta": meta})

    def test_cftc_accepts_renamed_markets_and_skips_strays(self):
        hist = [{**Cftc.ROW, "report_date_as_yyyy_mm_dd": "2010-03-02T00:00:00.000"},
                {**Cftc.ROW, "market_and_exchange_names": "WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE"},
                {**Cftc.ROW, "report_date_as_yyyy_mm_dd": "2024-03-12T00:00:00.000", "market_and_exchange_names": "GOLD - COMEX"}]
        rows, err = cftc.parse_rows(hist, "disaggregated", "067651")
        self.assertIsNone(err)
        self.assertEqual(sorted({r[1] for r in rows}), ["2010-03-02", "2024-03-05"], "old and new names kept, the stray row dropped")
        for code, name in (("043602", "UST 10Y NOTE - CHICAGO BOARD OF TRADE"), ("098662", "USD INDEX - ICE FUTURES U.S."),
                           ("124603", "DJIA x $5 - CHICAGO BOARD OF TRADE"), ("023651", "NAT GAS NYME - NEW YORK MERCANTILE EXCHANGE")):
            _, err = cftc.parse_rows([{**Cftc.ROW, "market_and_exchange_names": name}], "legacy", code)
            self.assertIsNone(err, name)

    def test_cftc_resumes_per_contract_so_a_failed_contract_gets_its_history(self):
        rows, _ = cftc.parse_rows([Cftc.ROW], "disaggregated", "067651")
        self.st.put_alt(cftc.DATASET, rows)
        self.assertEqual(cftc._last_date(self.st, "067651", "disaggregated"), "2024-03-05")
        self.assertIsNone(cftc._last_date(self.st, "067651", "legacy"))
        self.assertIsNone(cftc._last_date(self.st, "023651", "disaggregated"))

    def test_foreign_filer_without_us_gaap_is_skipped_not_an_error(self):
        from unittest import mock
        from finsim2.data import refresh as R
        self._equity("TSM", 1046179)
        with mock.patch("finsim2.data.sec.fetch_companyfacts", return_value={"facts": {"ifrs-full": {"Revenue": {}}, "dei": {}}}):
            with self.assertRaises(R.NoUSGAAP) as cm:
                R.refresh_sec(self.st, self.st.asset("TSM"))
        self.assertIn("ifrs-full", str(cm.exception))
        with mock.patch("finsim2.data.sec.fetch_companyfacts", return_value={"facts": {"us-gaap": {}}}):
            with self.assertRaises(ValueError):
                R.refresh_sec(self.st, self.st.asset("TSM"))

    def test_new_issuers_get_insider_history_from_stored_quarters(self):
        from unittest import mock
        qs = ["2025q1", "2025q2", "2025q3", "2025q4"]
        seen = []
        real = E.parse_insider_zip

        def spy(blob, cmap):
            seen.append(sorted(set(cmap.values())))
            return real(blob, cmap)
        base = sorted(set(E.issuer_map(self.st).values()))           # the seeded universe
        with mock.patch.object(E, "quarters", return_value=qs), mock.patch.object(E.S, "_get", return_value=_insider_zip()), \
                mock.patch.object(E.S, "_throttle"), mock.patch.object(E, "parse_insider_zip", side_effect=spy):
            E.refresh_insider(self.st)
            self.assertEqual(seen, [base] * 4)
            seen.clear()
            self._equity("NEWCO", 999999)                      # added by universe --expand
            res = E.refresh_insider(self.st)
            self.assertEqual(res["new_issuers"], 1)
            self.assertEqual(seen.count(["NEWCO"]), 2, "stored quarters re-read for the new issuer only")
            self.assertEqual(seen.count(sorted(base + ["NEWCO"])), 2, "the latest two quarters for everyone")
            self.assertTrue(self.st.alt(E.INSIDER, "NEWCO"))
            seen.clear()
            E.refresh_insider(self.st)
            self.assertEqual(seen, [sorted(base + ["NEWCO"])] * 2, "no backfill once done")

    def test_funds_are_not_equities(self):
        from finsim2.data import expand as X2
        payload = {"fields": ["cik", "name", "ticker", "exchange"], "data": [
            [1, "iShares Gold Trust", "IAU", "NYSE"], [2, "SPDR Gold MiniShares Trust", "GLDM", "NYSE"], [3, "Sprott Inc.", "SII", "NYSE"],
            [4, "Vornado Realty Trust", "VNO", "NYSE"], [5, "Northern Trust Corp", "NTRS", "Nasdaq"]]}
        self.assertEqual([c[0] for c in X2.candidates(payload)], ["SII", "VNO", "NTRS"])
        self.assertTrue(X2.is_fund("World Gold Trust", 6221))
        self._equity("IAU", 1278680, "iShares Gold Trust", expanded="2026-09-27")
        self._equity("MU", 723125, "Micron Technology", expanded="2026-09-27")
        self._equity("GLDX", 1, "Gold Trust Holdings")              # not expanded: never touched
        fixed = X2.reclassify_funds(self.st)
        self.assertEqual([f["id"] for f in fixed], ["IAU"])
        self.assertEqual(self.st.asset("IAU")["asset_class"], "ETF")
        self.assertEqual(self.st.asset("IAU")["sector"], "Precious Metals")
        self.assertEqual(self.st.asset("MU")["asset_class"], "EQUITY")

    def test_placeholder_keys_count_as_missing(self):
        from unittest import mock
        from finsim2.data import env_key, placeholder_key
        with mock.patch.dict(os.environ, {"EIA_API_KEY": "your-key-here", "FINNHUB_KEY": "abc123real"}):
            self.assertIsNone(env_key("EIA_API_KEY"))
            self.assertTrue(placeholder_key("EIA_API_KEY"))
            self.assertEqual(env_key("FINNHUB_KEY"), "abc123real")
            res = eia.refresh(self.st)
            self.assertIn("placeholder", res["skipped"])
            self.assertNotIn("your-key-here", res["skipped"])

    def test_binance_refusal_is_reported_once_not_as_errors(self):
        from unittest import mock
        from finsim2.data import FetchError

        def fake(url, *a, **k):
            if "binance" in url:
                raise FetchError("HTTP 451", status=451)
            if "deribit" in url:
                return {"result": []}
            raise FetchError("HTTP 404", status=404)
        with mock.patch.object(X, "_json", side_effect=fake), mock.patch.object(X.time, "sleep"):
            res = X.refresh(self.st)
        self.assertEqual(len(res["unavailable"]), 1)
        self.assertFalse([n for n in res["notes"] if "Binance" in n])
        self.assertEqual(X.ASSETS["SOL"]["deribit"], "SOL_USDC-PERPETUAL")



class _Panel:
    def __init__(self, cal, px):
        self.cal, self.px = cal, px

    def calendar(self):
        return self.cal

    def series(self, a, field="adj_close"):
        return [1e6 if field == "volume" else 100.0] * len(self.cal) if a in self.px else [None] * len(self.cal)

    def macro(self, sid):
        return [None] * len(self.cal)


class _Research:
    def __init__(self, panel):
        self._p = panel

    def panel(self):
        return self._p


class NewDataFamilies(unittest.TestCase):
    """engine/newinfo.py batch 2: features exist only from the publication date."""

    def setUp(self):
        from finsim2.data.store import Store as St
        self.tmp = tempfile.mkdtemp()
        self.st = St(os.path.join(self.tmp, "x.db"))
        d0 = dt.date(2010, 1, 4)
        self.cal = [(d0 + dt.timedelta(days=k)).isoformat() for k in range(0, 800) if (d0 + dt.timedelta(days=k)).weekday() < 5]

    def tearDown(self):
        self.st.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _builder(self, assets=("AAPL",)):
        from finsim2.engine import newinfo as N
        return N.Builder(_Research(_Panel(self.cal, set(assets))), self.st)

    def test_insider_buying_counts_from_the_filing_day(self):
        d, pub = self.cal[300], self.cal[301]
        self.st.put_alt(E.INSIDER, [("AAPL", d, "buy_value", 5e6, pub), ("AAPL", d, "buyers", 2.0, pub),
                                    ("AAPL", d, "officer_buy_value", 5e6, pub)])
        f = self._builder().insider("AAPL")
        i = self.cal.index(pub)
        self.assertEqual(f["ins_buy_126"][i - 1], 0.0, "not visible on the transaction day")
        self.assertGreater(f["ins_buy_126"][i], 0.0)
        self.assertGreater(f["ins_net_63"][i], 0.0)
        self.assertAlmostEqual(f["ins_buyers_126"][i], math.log1p(2.0))
        self.assertEqual(f["ins_buy_126"][i + 126], 0.0, "rolled out of the 126-session window")

    def test_macro_calendar_only_counts_announced_events(self):
        from finsim2.data.calendar import EVENTS as EVCAL
        ev = self.cal[400]
        self.st.put_alt(EVCAL, [("macro:US", ev, "fomc", 1.0, self.cal[398])])     # announced two sessions before
        f = self._builder().event_calendar("SPY")
        self.assertIn(f["fomc_5d"][self.cal.index(ev) - 3], (None, 0.0), "not yet announced")
        self.assertEqual(f["fomc_5d"][self.cal.index(ev) - 2], 1.0)
        self.assertEqual(f["fomc_5d"][self.cal.index(ev)], 0.0, "only the next five sessions, not today")

    def test_cot_is_signed_per_asset_and_public_from_saturday(self):
        rows = []
        for k in range(80):
            d = (dt.date(2010, 1, 5) + dt.timedelta(weeks=k)).isoformat()
            net = 0.1 if k < 79 else 0.5                                   # a jump in speculator longs in the last week
            rows += [("cot:097741", d, "open_interest", 1000.0, cftc.published(d)),
                     ("cot:097741", d, "noncomm_long", 500.0 + net * 500, cftc.published(d)),
                     ("cot:097741", d, "noncomm_short", 500.0 - net * 500, cftc.published(d))]
        self.st.put_alt(cftc.DATASET, rows)
        b = self._builder(("FXY", "USDJPY"))
        last_pub = cftc.published((dt.date(2010, 1, 5) + dt.timedelta(weeks=79)).isoformat())
        i = next(k for k, d in enumerate(self.cal) if d >= last_pub)
        fxy, usdjpy = b.cftc_positioning("FXY")["cot_spec_chg_13w"], b.cftc_positioning("USDJPY")["cot_spec_chg_13w"]
        self.assertGreater(fxy[i], 0.0, "long yen futures = long FXY")
        self.assertLess(usdjpy[i], 0.0, "long yen futures = short USDJPY")
        self.assertAlmostEqual(fxy[i - 1] or 0.0, 0.0, "the jump is not visible before its Saturday")


if __name__ == "__main__":
    unittest.main()
