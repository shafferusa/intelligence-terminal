"""Providers: Yahoo chart, Cboe delayed chains, Treasury par curve, FRED, Frankfurter, FINRA, FakeProvider, ProviderSet (§8.4).

The pure parsers and the shapes (``YahooIntraday``, ``ChainSnapshot`` / ``ChainRow``, ``ParCurve``, ``FxRates``, ``DivEvent`` /
``SplitEvent`` …) live in ``quotes/parsers.py`` and are re-exported here, so ``from finsim.terminal.quotes.providers import
parse_chain`` keeps working.

Conventions
- Yahoo (``BROWSER_UA``, hosts query1 then query2, 3 attempts per host with 1/2/4 s backoff on 429/5xx/timeouts, ≤ 2 rps)
  is the last-trade feed; a 404 with a ``chart.error`` body is an unknown symbol (``FetchError`` status 404, not
  retryable, no provider failure).
- Cboe (``TERMINAL_UA``, 1 rps, 60 s timeout) answers the FULL chain (SPY 5.7 MB, _SPX 13 MB): the JSON is parsed and
  reduced at once (``parse_chain``) and the dict released; a 403 to the honest UA sets ``refused_ua`` — never a bypass.
- Treasury.gov (``GOV_UA``, 45 s): the daily par yield curve XML feed of this month, else last month's.
- FRED (``GOV_UA``): ``fredgraph.csv`` for one series with a 60-day look-back.  Frankfurter (``TERMINAL_UA``): ECB
  reference rates, fallback FX only.  FINRA (``GOV_UA``): the daily consolidated short volume pipe file (None when the
  file is not published).
- FRED MAINTENANCE (ORC-8, 2026-10-21): ``FredRoute`` wraps the FRED fetch.  An HTML answer (FRED's "Site Maintenance"
  page, HTTP 200) is recognised by the parser (``FetchError.kind == "maintenance"``): the ``fred`` health row then reads
  "FRED unavailable (maintenance)" (``ProviderHealth.mark_unavailable``), FRED is not asked again for ``FRED_PAUSE_S``
  (one request per pause, never one per series), the ``FRED_API_KEY`` route (api.stlouisfed.org; the key comes from the
  environment only and is never logged, echoed or put in an error text - ``http_get`` errors never carry the URL) is
  tried as a secondary route, and two series have FREE fallbacks with their own source label: ``SOFR`` from the NY Fed
  (markets.newyorkfed.org, the publisher of SOFR) and ``ECBDFR`` from the ECB data API (FM deposit facility rate).
  Every other series raises "FRED unavailable (maintenance)" and the rate store keeps its last good observations with
  their true dates.  Results are ``Observations`` (a list of ``(date, percent)`` carrying ``.source``), so the store
  labels a fallback value with where it came from.
- DEBT SOURCES (Build 2 P07, every one verified from this machine on 2026-10-21; ``ProviderSet.debt``): ``FiscalData``
  (api.fiscaldata.treasury.gov MSPD table 3 market + auctions, ``GOV_UA``), ``TreasuryDirect`` (FedInvest today's
  prices, ``GOV_UA``), ``TreasuryCurve.real_latest`` (the daily real yield curve, ``GOV_UA``), ``EcbData`` (euro-area AAA
  par yields and the deposit rate, ``TERMINAL_UA``), ``MofJgb`` (JGB yields, ``TERMINAL_UA``), ``NyFed`` (SOFR,
  ``TERMINAL_UA``).  (Gilts stay LIMITED, monthly from FRED's OECD series: out of the 1.0.6.1 scope.)
- RFR AND TERM CURVES (FX_CCS_PLAN rev 2 K2 / D12, 1.0.6.1; ``ProviderSet.rfr``, the ``rfr`` and ``curves`` jobs of the worker):
  ``EcbData.estr`` / ``estr_index`` / ``aaa_spot`` (the ECB data portal, ESTR ``EST B.EU000A2X2A25.WT``, the compounded ESTR index
  ``EST B.EU000A2QQF08.CI``, the AAA spot rates ``YC ... SR_3M .. SR_10Y`` with the six Svensson parameters) with a 60 s timeout and 2
  attempts - an HTML body (the ECB's error page) is unavailable, never parsed; ``NyFed.sofr_index`` (the SOFR Index, ``sofrai``);
  ``BoeData`` (the Bank of England: the database CSV of IUDSOIA / IUDZOS2 and the yield-curve zip).  THE BANK OF ENGLAND RULE: every
  request sends ``GOV_UA`` - the terminal's honest project identity with the owner's e-mail, the one it also sends to FRED and FINRA -
  never ``TERMINAL_UA`` and never a browser agent; the Bank's edge refuses some agents (an Akamai "Access Denied", HTTP 403): a 403 is
  recorded as "unavailable" with the page's reference and is FINAL - no other agent is ever tried, nothing is retried until the next
  scheduled run.  FRED is the fallback of ESTR (``ECBESTRVOLWGTTRMDMNRT``), the SOFR Index (``SOFRINDEX``) and SONIA (``IUDSOIA``),
  asked only when the primary source failed; each observation keeps the source it came from.
- Every live provider takes an injectable ``getter(url, ua, timeout, accept) -> bytes`` and ``sleep`` so tests run
  without a network; with an injected sleep the rate limiter's clock advances with it, never with wall time.
- ``FakeProvider`` serves tests and ``serve --offline``: quotes, daily bars, meta, chains (raw payloads reduced exactly
  like the live parser), curves (par points or direct discount factors), FRED observations, FX, FINRA rows and
  dividend / split events set by the test; every method counts its calls and can be told to fail, so error paths are
  testable.  Nothing is invented by the provider itself.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time as _time
import urllib.parse
from datetime import date, timedelta
from decimal import Decimal
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from ..config import BROWSER_UA, CBOE_RPS, FRED_RPS, GOV_UA, TERMINAL_UA, YAHOO_RPS
from ..units import qprice
from . import Bar1m
from .http import FetchError, ProviderHealth, RateLimiter, http_get, retrying
from .parsers import (DAILY_DELAY_LABEL, FRED_MAINTENANCE, OCC_TAIL, TENOR_Q, TENOR_YEARS, TENORS, YAHOO_DELAY_LABEL, YAHOO_DELAY_S, ChainRow,  # noqa: F401
                      ChainSnapshot, ChainUnderlying, DailyBar, DivEvent, Events, FxRates, ParCurve, SplitEvent, YahooIntraday, _chart_error, chart_bars,
                      chart_result, clean_meta, dec, et_naive_to_epoch, intv, num, parse_auctions, parse_chain, parse_daily, parse_ecb_csv, parse_events,
                      parse_minute_bars,
                      parse_fedinvest, parse_finra, parse_frankfurter, parse_fred_csv, parse_fred_json, parse_intraday, parse_mof_jgb, parse_mspd,
                      parse_nyfed_rates, parse_treasury_real_xml, parse_treasury_xml, boe_refusal_reference, parse_boe_iadb, parse_boe_ois_zip,
                      parse_ecb_csv_exact, parse_nyfed_index)

YAHOO_HOSTS = ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com")
CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json"
TREASURY_URL = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
                "?data=daily_treasury_yield_curve&field_tdr_date_value_month={yyyymm}")
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={start}"
FRED_KEY_URL = "https://api.stlouisfed.org/fred/series/observations?series_id={sid}&api_key={key}&file_type=json&observation_start={start}"
FRED_PAUSE_S = 1800                 # after a maintenance page FRED is not asked again for 30 min (one request per pause)
TREASURY_REAL_URL = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
                     "?data=daily_treasury_real_yield_curve&field_tdr_date_value_month={yyyymm}")
FISCALDATA = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
MSPD_LATEST_URL = FISCALDATA + "/v1/debt/mspd/mspd_table_3_market?sort=-record_date&page[size]=1&fields=record_date"
MSPD_URL = FISCALDATA + "/v1/debt/mspd/mspd_table_3_market?filter=record_date:eq:{d}&page[size]=10000"
AUCTIONS_URL = (FISCALDATA + "/v1/accounting/od/auctions_query?filter=issue_date:gte:{d}&sort=-issue_date&page[size]=1000"
                "&fields=cusip,security_type,security_term,auction_date,issue_date,maturity_date,int_rate,high_yield,high_investment_rate,"
                "high_discnt_rate,inflation_index_security,floating_rate,reopening,first_int_payment_date,spread,cash_management_bill_cmb")
FEDINVEST_URL = "https://www.treasurydirect.gov/GA-FI/FedInvest/todaySecurityPriceDetail"
NYFED_SOFR_URL = "https://markets.newyorkfed.org/api/rates/secured/sofr/last/{n}.json"
NYFED_SOFRAI_URL = "https://markets.newyorkfed.org/api/rates/secured/sofrai/last/{n}.json"
BOE_IADB_URL = ("https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp?csv.x=yes&Datefrom={since}&Dateto=now"
                "&SeriesCodes={codes}&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N")
BOE_CURVE_URL = "https://www.bankofengland.co.uk/-/media/boe/files/statistics/yield-curves/latest-yield-curve-data.zip"
ECB_ESTR = ("EST", "B.EU000A2X2A25.WT")
ECB_ESTR_INDEX = ("EST", "B.EU000A2QQF08.CI")
ECB_AAA_SPOT_TENORS = ("3M", "6M", "9M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y")
ECB_AAA_SV_PARAMS = ("BETA0", "BETA1", "BETA2", "BETA3", "TAU1", "TAU2")
ECB_AAA_SPOT_KEY = "B.U2.EUR.4F.G_N_A.SV_C_YM." + "+".join(["SR_" + t for t in ECB_AAA_SPOT_TENORS] + list(ECB_AAA_SV_PARAMS))
RFR_TIMEOUT_S = 60.0                  # FX-C-18: the ECB's 20-30 s answers are a delay, not a failure
RFR_ATTEMPTS = 2
ESTR_SOURCE = "ECB data portal (EST B.EU000A2X2A25.WT, euro short-term rate)"
ESTR_INDEX_SOURCE = "ECB data portal (EST B.EU000A2QQF08.CI, compounded euro short-term rate index)"
SOFR_INDEX_SOURCE = "NY Fed (markets.newyorkfed.org, SOFR Index)"
BOE_IADB_SOURCE = "Bank of England database ({code})"
BOE_CURVE_SOURCE = "Bank of England yield curves (latest-yield-curve-data.zip, OIS daily data current month, 3. spot, short end)"
ECB_SPOT_SOURCE = "ECB data portal (YC euro-area AAA government spot rates, Svensson model, continuous compounding)"
FRED_RFR = {"ESTR": "ECBESTRVOLWGTTRMDMNRT", "SOFR_INDEX": "SOFRINDEX", "IUDSOIA": "IUDSOIA"}
ECB_URL = "https://data-api.ecb.europa.eu/service/data/{flow}/{key}?lastNObservations={n}&format=csvdata"
ECB_DFR = ("FM", "D.U2.EUR.4F.KR.DFR.LEV")
ECB_AAA_PAR = ("YC", "B.U2.EUR.4F.G_N_A.SV_C_YM.{tenors}")
ECB_AAA_TENORS = ("1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y", "30Y")
MOF_JGB_URL = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv"
SOFR_FALLBACK_SOURCE = "NY Fed (markets.newyorkfed.org): SOFR as published by its administrator"
ECBDFR_FALLBACK_SOURCE = "ECB data API (FM deposit facility rate)"
FRANKFURTER_URL = "https://api.frankfurter.app/latest?from=USD"
FINRA_URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{yyyymmdd}.txt"
Getter = Callable[[str, str, float, str], bytes]


# ---- live providers -------------------------------------------------------------------------------------------------------------
class _Base:
    name = "base"

    def __init__(self, clock, health: Optional[ProviderHealth] = None, getter: Optional[Getter] = None, sleep: Callable[[float], None] = _time.sleep,
                 rps: float = 1.0, log=None):
        self.clock = clock
        self.health = health
        self.log = log
        self._getter = getter or (lambda url, ua, timeout, accept: http_get(url, ua, timeout=timeout, accept=accept))
        self._sleep = sleep
        if sleep is _time.sleep:
            self.limiter = RateLimiter(rps, sleep=sleep)
        else:                                   # an injected (fake) sleep: the limiter's clock must advance with it, not with wall time
            slept = [0.0]

            def _vsleep(s: float) -> None:
                slept[0] += float(s)
                sleep(s)
            self._sleep = _vsleep
            self.limiter = RateLimiter(rps, sleep=_vsleep, now=lambda: _time.monotonic() + slept[0])

    def _now(self) -> int:
        return int(self.clock.epoch())

    def _get(self, url: str, ua: str, timeout: float, accept: str = "application/json", attempts: int = 3) -> bytes:
        if self.health is not None and not self.health.allow(self.name):
            raise FetchError(f"{self.name} circuit open", retryable=False)

        def once() -> bytes:
            self.limiter.wait()
            try:
                raw = self._getter(url, ua, timeout, accept)
            except FetchError as e:
                if self.health is not None:
                    self.health.record(self.name, False, e.status, str(e))
                raise
            if self.health is not None:
                self.health.record(self.name, True, 200)
            return raw
        return retrying(once, attempts=attempts, backoff=1.0, sleep=self._sleep)


class YahooChart(_Base):
    name = "yahoo"

    def __init__(self, clock, health=None, getter=None, sleep=_time.sleep, log=None):
        super().__init__(clock, health, getter, sleep, rps=YAHOO_RPS, log=log)

    @staticmethod
    def _valid(symbol: str) -> str:
        if not isinstance(symbol, str) or not symbol.strip() or len(symbol) > 40:
            raise FetchError("invalid symbol", status=400, retryable=False)
        return symbol.strip()

    def _chart(self, symbol: str, query: str) -> dict:
        sym = urllib.parse.quote(self._valid(symbol), safe="")
        last: Optional[FetchError] = None
        for host in YAHOO_HOSTS:
            url = f"{host}/v8/finance/chart/{sym}?{query}"
            try:
                raw = self._get(url, BROWSER_UA, 20.0)
            except FetchError as e:
                last = e
                if e.status == 404 and e.body:
                    try:
                        msg = _chart_error(json.loads(e.body.decode("utf-8", "replace")))
                    except ValueError:
                        msg = None
                    raise FetchError(f"{symbol}: {msg or 'not found'}", status=404, retryable=False) from None
                if e.status in (400, 404):
                    raise FetchError(f"{symbol}: HTTP {e.status}", status=e.status, retryable=False) from None
                continue          # retryable failures exhausted on this host: try the next host
            try:
                payload = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                last = FetchError("invalid JSON", retryable=False)
                continue
            return chart_result(payload)
        raise FetchError(f"{symbol}: download failed ({last})", status=getattr(last, "status", None))

    def intraday(self, symbol: str) -> YahooIntraday:
        res = self._chart(symbol, "range=1d&interval=1m&includePrePost=false")
        return parse_intraday(res, symbol, self._now())

    def daily(self, symbol: str, days: int = 10) -> List[DailyBar]:
        rng = "1mo" if days <= 22 else "3mo"
        bars = parse_daily(self._chart(symbol, f"range={rng}&interval=1d"))
        return bars[-days:] if days > 0 else bars

    def minute_bars(self, symbol: str, start: int, end: int) -> List[Bar1m]:
        """V1061R1-02: the 1-minute bars that start in ``[start, end)`` (epoch seconds), one small request (``period1`` / ``period2``,
        ``interval=1m``; Yahoo keeps about 30 days of 1-minute history).  MARKS_RECORD reads a missed FX close from the bar that ends at
        the 17:00 ET close - never from the daily bar, whose FX "close" is a snapshot near the London day start."""
        start, end = int(start), int(end)
        if end <= start or end - start > 86400:
            raise FetchError("invalid minute-bar window", status=400, retryable=False)
        res = self._chart(symbol, f"period1={start}&period2={end}&interval=1m&includePrePost=false")
        return parse_minute_bars(res, start, end)

    def meta(self, symbol: str) -> dict:
        res = self._chart(symbol, "range=5d&interval=1d")
        return clean_meta(res.get("meta") if isinstance(res, dict) else {})

    def events(self, symbol: str) -> Events:
        return parse_events(self._chart(symbol, "range=3mo&interval=1d&events=div%2Csplits"))


class CboeChain(_Base):
    name = "cboe"

    def __init__(self, clock, health=None, getter=None, sleep=_time.sleep, log=None):
        super().__init__(clock, health, getter, sleep, rps=CBOE_RPS, log=log)

    @property
    def refused_ua(self) -> bool:
        return bool(self.health is not None and self.health.refused_ua(self.name))

    def chain(self, cboe_symbol: str, underlying_id: Optional[str] = None) -> ChainSnapshot:
        if not isinstance(cboe_symbol, str) or not re.match(r"^_?[A-Z][A-Z0-9.\-]{0,9}$", cboe_symbol):
            raise FetchError("invalid Cboe symbol", status=400, retryable=False)
        url = CBOE_URL.format(sym=urllib.parse.quote(cboe_symbol, safe=""))
        try:
            raw = self._get(url, TERMINAL_UA, 60.0, attempts=2)
        except FetchError as e:
            if e.status == 403 and self.health is not None:
                self.health.set_refused_ua(self.name, True)
            raise
        try:
            payload = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            raise FetchError("invalid chain JSON", retryable=False) from None
        del raw
        snap = parse_chain(payload, cboe_symbol, underlying_id or cboe_symbol.lstrip("_"), self.clock.today(), self._now())
        del payload
        return snap


class TreasuryCurve(_Base):
    name = "treasury"

    def __init__(self, clock, health=None, getter=None, sleep=_time.sleep, log=None):
        super().__init__(clock, health, getter, sleep, rps=0.5, log=log)

    def latest(self) -> Optional[ParCurve]:
        today = self.clock.today()
        months = [today.strftime("%Y%m"), (today.replace(day=1) - timedelta(days=1)).strftime("%Y%m")]
        for yyyymm in months:
            raw = self._get(TREASURY_URL.format(yyyymm=yyyymm), GOV_UA, 45.0, accept="application/xml, text/xml", attempts=2)
            curve = parse_treasury_xml(raw, self._now())
            if curve is not None:
                return curve
        return None

    def real_latest(self) -> Optional[ParCurve]:
        """The daily REAL (TIPS) yield curve of this month, else last month's."""
        today = self.clock.today()
        months = [today.strftime("%Y%m"), (today.replace(day=1) - timedelta(days=1)).strftime("%Y%m")]
        for yyyymm in months:
            raw = self._get(TREASURY_REAL_URL.format(yyyymm=yyyymm), GOV_UA, 45.0, accept="application/xml, text/xml", attempts=2)
            curve = parse_treasury_real_xml(raw, self._now())
            if curve is not None:
                return curve
        return None


class Observations(list):
    """``[(date, value)]`` that knows where it came from (``source``): the rate store labels the series with it."""

    def __init__(self, rows=(), source: str = "FRED"):
        super().__init__(rows)
        self.source = source


class FredSeries(_Base):
    name = "fred"
    _ID = re.compile(r"^[A-Z0-9]{1,30}$")

    def __init__(self, clock, health=None, getter=None, sleep=_time.sleep, log=None, api_key: Optional[str] = None):
        super().__init__(clock, health, getter, sleep, rps=FRED_RPS, log=log)
        self._key = api_key                    # the FRED_API_KEY route: never logged, never echoed

    def _valid(self, series_id: str) -> str:
        if not self._ID.match(str(series_id)):
            raise FetchError("invalid FRED series id", status=400, retryable=False)
        return str(series_id)

    def csv(self, series_id: str, lookback_days: int = 60) -> List[Tuple[date, Decimal]]:
        """fredgraph.csv (the free route); an HTML maintenance page raises ``FetchError(kind="maintenance")``."""
        sid = self._valid(series_id)
        start = (self.clock.today() - timedelta(days=int(lookback_days))).isoformat()
        raw = self._get(FRED_URL.format(sid=sid, start=start), GOV_UA, 20.0, accept="text/csv")
        return parse_fred_csv(raw)

    @property
    def has_key(self) -> bool:
        return bool(self._key)

    def keyed(self, series_id: str, lookback_days: int = 60) -> List[Tuple[date, Decimal]]:
        """api.stlouisfed.org with the FRED_API_KEY (the secondary route).  The URL holds the key: it is never formatted into
        a message (``http_get`` errors name the status only)."""
        if not self._key:
            raise FetchError("no FRED_API_KEY in the environment", retryable=False)
        sid = self._valid(series_id)
        start = (self.clock.today() - timedelta(days=int(lookback_days))).isoformat()
        raw = self._get(FRED_KEY_URL.format(sid=sid, key=urllib.parse.quote(self._key, safe=""), start=start), GOV_UA, 20.0, accept="application/json",
                        attempts=1)
        return parse_fred_json(raw)

    def latest(self, series_id: str, lookback_days: int = 60) -> List[Tuple[date, Decimal]]:
        return self.csv(series_id, lookback_days)


class FredRoute:
    """FRED as the refresher sees it (module docstring FRED MAINTENANCE): ``latest(series_id, lookback_days)`` ->
    ``Observations`` with its source; maintenance pauses FRED, tries the key route, then the free fallbacks."""

    name = "fred"

    def __init__(self, clock, health: Optional[ProviderHealth], primary: Callable[[str, int], List[Tuple[date, Decimal]]],
                 keyed: Optional[Callable[[str, int], List[Tuple[date, Decimal]]]] = None,
                 fallbacks: Optional[Dict[str, Tuple[Callable[[int], List[Tuple[date, Decimal]]], str]]] = None, log=None):
        self.clock, self.health, self.log = clock, health, log
        self._primary, self._keyed = primary, keyed
        self.fallbacks = dict(fallbacks or {})
        self._lock = threading.Lock()
        self._pause_until = 0
        self.maintenance_since: Optional[int] = None
        self.key_route_state: Optional[str] = None

    def _now(self) -> int:
        return int(self.clock.epoch()) if self.clock is not None else int(_time.time())

    @property
    def in_maintenance(self) -> bool:
        with self._lock:
            return self.maintenance_since is not None

    def status(self) -> dict:
        with self._lock:
            return {"maintenance": self.maintenance_since is not None, "since_ts": self.maintenance_since, "paused_until_ts": self._pause_until or None,
                    "key_route": self.key_route_state, "fallbacks": {k: v[1] for k, v in self.fallbacks.items()}}

    def _enter_maintenance(self, now: int) -> None:
        with self._lock:
            first = self.maintenance_since is None
            if first:
                self.maintenance_since = now
            self._pause_until = now + FRED_PAUSE_S
        if self.health is not None:
            self.health.mark_unavailable("fred", FRED_MAINTENANCE)
        if first and self.log:
            self.log.info("FRED unavailable (maintenance): the site serves its maintenance page; FRED is asked again every %d min, "
                          "SOFR / ECBDFR come from their free fallbacks meanwhile", FRED_PAUSE_S // 60)

    def _left_maintenance(self) -> None:
        with self._lock:
            was = self.maintenance_since is not None
            self.maintenance_since = None
            self._pause_until = 0
        if was and self.log:
            self.log.info("FRED is back (no maintenance page)")

    def latest(self, series_id: str, lookback_days: int = 60) -> Observations:
        now = self._now()
        with self._lock:
            paused = self._pause_until and now < self._pause_until
        err: Optional[FetchError] = None
        if not paused:
            maint = False
            try:
                rows = self._primary(series_id, lookback_days)
                self._left_maintenance()
                return Observations(rows, "FRED")
            except FetchError as e:
                if e.status == 400:
                    raise
                err = e
                maint = e.kind == "maintenance"
            if self._keyed is not None:
                try:
                    rows = self._keyed(series_id, lookback_days)
                    self.key_route_state = "ok"
                    return Observations(rows, "FRED (api.stlouisfed.org key route)")
                except FetchError as e2:
                    self.key_route_state = FRED_MAINTENANCE if e2.kind == "maintenance" else f"failed: {e2}"[:120]
            if maint:
                self._enter_maintenance(now)          # after the key route: the health row ends on the maintenance verdict
        if err is None:
            err = FetchError(FRED_MAINTENANCE, retryable=False, kind="maintenance")
        fb = self.fallbacks.get(str(series_id))
        if fb is not None:
            fn, source = fb
            rows = fn(lookback_days)
            if rows:
                return Observations(rows, source)
        raise err


# ---- the debt sources (module docstring DEBT SOURCES) ---------------------------------------------------------------
class FiscalData(_Base):
    name = "fiscaldata"

    def __init__(self, clock, health=None, getter=None, sleep=_time.sleep, log=None):
        super().__init__(clock, health, getter, sleep, rps=1.0, log=log)

    def mspd(self) -> Tuple[Optional[date], List[dict]]:
        """The latest month of MSPD table 3 (market): one small request for the record date, one for its rows."""
        head = json.loads(self._get(MSPD_LATEST_URL, GOV_UA, 30.0).decode("utf-8", "replace"))
        rows = head.get("data") if isinstance(head, dict) else None
        d = None
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            try:
                d = date.fromisoformat(str(rows[0].get("record_date"))[:10])
            except ValueError:
                d = None
        if d is None:
            raise FetchError("fiscaldata: no MSPD record date", retryable=False)
        return parse_mspd(self._get(MSPD_URL.format(d=d.isoformat()), GOV_UA, 60.0))

    def auctions(self, since: date) -> List[dict]:
        return parse_auctions(self._get(AUCTIONS_URL.format(d=since.isoformat()), GOV_UA, 60.0))


class TreasuryDirect(_Base):
    name = "treasurydirect"

    def __init__(self, clock, health=None, getter=None, sleep=_time.sleep, log=None):
        super().__init__(clock, health, getter, sleep, rps=0.5, log=log)

    def fedinvest(self) -> Tuple[Optional[date], Dict[str, dict]]:
        return parse_fedinvest(self._get(FEDINVEST_URL, GOV_UA, 45.0, accept="text/html", attempts=2))


class NyFed(_Base):
    name = "nyfed"

    def __init__(self, clock, health=None, getter=None, sleep=_time.sleep, log=None):
        super().__init__(clock, health, getter, sleep, rps=1.0, log=log)

    def sofr(self, n: int = 10) -> List[Tuple[date, Decimal]]:
        return parse_nyfed_rates(self._get(NYFED_SOFR_URL.format(n=max(1, min(60, int(n)))), TERMINAL_UA, 20.0))

    def sofr_index(self, n: int = 10) -> "Observations":
        """FX_CCS_PLAN K2: the SOFR Index (``sofrai``), exact levels, labelled with the NY Fed as source."""
        raw = self._get(NYFED_SOFRAI_URL.format(n=max(1, min(60, int(n)))), TERMINAL_UA, 20.0, attempts=RFR_ATTEMPTS)
        return Observations(parse_nyfed_index(raw), SOFR_INDEX_SOURCE)


class EcbData(_Base):
    name = "ecb"

    def __init__(self, clock, health=None, getter=None, sleep=_time.sleep, log=None):
        super().__init__(clock, health, getter, sleep, rps=1.0, log=log)

    def series(self, flow: str, key: str, n: int = 5) -> Dict[str, List[Tuple[date, Decimal]]]:
        url = ECB_URL.format(flow=flow, key=key, n=max(1, min(60, int(n))))
        return parse_ecb_csv(self._get(url, TERMINAL_UA, 30.0, accept="text/csv"))

    def deposit_rate(self, n: int = 10) -> List[Tuple[date, Decimal]]:
        got = self.series(ECB_DFR[0], ECB_DFR[1], n)
        return next(iter(got.values()), [])

    def aaa_par_curve(self) -> Optional[Tuple[date, Dict[Decimal, Decimal]]]:
        """The euro-area AAA government PAR yield curve (Svensson; ECB YC dataflow) at its latest common date."""
        key = ECB_AAA_PAR[1].format(tenors="+".join("PY_" + t for t in ECB_AAA_TENORS))
        return ecb_curve_from(self.series(ECB_AAA_PAR[0], key, 3))

    # ---- FX_CCS_PLAN K2 (1.0.6.1): 60 s timeout, 2 attempts, exact values ----------------------------------------------
    def _exact(self, flow: str, key: str, n: int) -> Dict[str, List[Tuple[date, Decimal]]]:
        url = ECB_URL.format(flow=flow, key=key, n=max(1, min(60, int(n))))
        return parse_ecb_csv_exact(self._get(url, TERMINAL_UA, RFR_TIMEOUT_S, accept="text/csv", attempts=RFR_ATTEMPTS))

    def estr(self, n: int = 10) -> "Observations":
        """ESTR (percent, the rate FOR each TARGET2 day, published 08:00 CET the next one)."""
        got = self._exact(ECB_ESTR[0], ECB_ESTR[1], n)
        return Observations(next((v for k, v in got.items() if k.endswith(".WT")), []), ESTR_SOURCE)

    def estr_index(self, n: int = 10) -> "Observations":
        """The ECB's compounded ESTR index (1 Oct 2019 = 100), an index LEVEL."""
        got = self._exact(ECB_ESTR_INDEX[0], ECB_ESTR_INDEX[1], n)
        return Observations(next((v for k, v in got.items() if k.endswith(".CI")), []), ESTR_INDEX_SOURCE)

    def aaa_spot(self, n: int = 3) -> Tuple[Dict[date, Dict[str, Decimal]], Dict[date, Dict[str, Decimal]]]:
        """The euro-area AAA government SPOT rates (``SR_<tenor>``, percent, continuously compounded) and the six Svensson parameters,
        each as ``{date: {label: Decimal}}`` (``3M`` .. ``10Y``; ``BETA0`` .. ``TAU2``), exact."""
        got = self._exact("YC", ECB_AAA_SPOT_KEY, n)
        return ecb_spot_from(got)


def ecb_spot_from(got: Dict[str, List[Tuple[date, Decimal]]]) -> Tuple[Dict[date, Dict[str, Decimal]], Dict[date, Dict[str, Decimal]]]:
    """``{key: [(date, value)]}`` of ``SR_<tenor>`` / Svensson keys -> ``({date: {tenor: value}}, {date: {param: value}})``."""
    spot: Dict[date, Dict[str, Decimal]] = {}
    sv: Dict[date, Dict[str, Decimal]] = {}
    for key, obs in got.items():
        last = key.rsplit(".", 1)[-1]
        if last.startswith("SR_") and last[3:] in ECB_AAA_SPOT_TENORS:
            for d, v in obs:
                spot.setdefault(d, {})[last[3:]] = v
        elif last in ECB_AAA_SV_PARAMS:
            for d, v in obs:
                sv.setdefault(d, {})[last] = v
    return spot, sv


def ecb_curve_from(got: Dict[str, List[Tuple[date, Decimal]]]) -> Optional[Tuple[date, Dict[Decimal, Decimal]]]:
    """``{key: [(date, value)]}`` of PY_<n>Y series -> ``(latest date every tenor has, {years: percent})``."""
    by_t: Dict[Decimal, Dict[date, Decimal]] = {}
    for key, obs in got.items():
        m = re.search(r"PY_(\d{1,2})Y$", key)
        if not m:
            continue
        by_t[Decimal(int(m.group(1))).quantize(TENOR_Q)] = dict(obs)
    if not by_t:
        return None
    common = set.intersection(*(set(v) for v in by_t.values())) if by_t else set()
    if not common:
        return None
    d = max(common)
    return d, {t: by_t[t][d].quantize(Decimal("0.0001")) for t in sorted(by_t)}


class MofJgb(_Base):
    name = "mof"

    def __init__(self, clock, health=None, getter=None, sleep=_time.sleep, log=None):
        super().__init__(clock, health, getter, sleep, rps=0.5, log=log)

    def jgb_curve(self) -> Optional[Tuple[date, Dict[Decimal, Decimal]]]:
        return parse_mof_jgb(self._get(MOF_JGB_URL, TERMINAL_UA, 30.0, accept="text/csv"))


class BoeData(_Base):
    """The Bank of England (module docstring RFR AND TERM CURVES): ``GOV_UA`` on every request, never another agent; a 403 is
    "unavailable" with the page's reference and final (``refused`` names it; nothing is retried with another agent)."""

    name = "boe"

    def __init__(self, clock, health=None, getter=None, sleep=_time.sleep, log=None):
        super().__init__(clock, health, getter, sleep, rps=0.5, log=log)
        self.refused: Optional[dict] = None          # {status, reference, at_ts} of the last 403, cleared by a success

    def _boe_get(self, url: str, accept: str) -> bytes:
        try:
            raw = self._get(url, GOV_UA, RFR_TIMEOUT_S, accept=accept, attempts=RFR_ATTEMPTS)
        except FetchError as e:
            if e.status == 403:
                ref = boe_refusal_reference(e.body)
                self.refused = {"status": 403, "reference": ref, "at_ts": self._now()}
                msg = f"Bank of England: HTTP 403 Access Denied{f' (reference {ref})' if ref else ''} - unavailable; no other agent is tried"
                if self.health is not None:
                    self.health.mark_unavailable(self.name, msg, 403)        # the request was counted once; the row names the reference
                    self.health.set_refused_ua(self.name, True)
                raise FetchError(msg, status=403, retryable=False, kind="refused") from None
            raise
        self.refused = None
        return raw

    def iadb(self, codes=("IUDSOIA", "IUDZOS2"), since: Optional[date] = None) -> Dict[str, "Observations"]:
        """The database CSV of ``codes`` from ``since`` (default 30 days back) -> ``{code: Observations}`` (exact, each labelled with the
        Bank of England database and its code)."""
        since = since or (self.clock.today() - timedelta(days=30))
        codes = [str(c).upper() for c in codes if re.fullmatch(r"[A-Za-z0-9]{3,12}", str(c))]
        mon = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")[since.month - 1]      # never the locale's %b
        url = BOE_IADB_URL.format(since=f"{since.day:02d}/{mon}/{since.year}", codes=",".join(codes))
        got = parse_boe_iadb(self._boe_get(url, "text/csv, text/plain, */*"))
        return {code: Observations(got.get(code, []), BOE_IADB_SOURCE.format(code=code)) for code in codes}

    def ois_curve(self) -> Dict[date, Dict[str, Decimal]]:
        """The UK OIS spot curve of every day in the yield-curve zip (``parse_boe_ois_zip``)."""
        return parse_boe_ois_zip(self._boe_get(BOE_CURVE_URL, "application/zip, application/octet-stream, */*"))


class RfrSources:
    """``ProviderSet.rfr`` (live): what the worker's ``rfr`` and ``curves`` jobs call - ``estr``, ``estr_index``, ``sofr_index``,
    ``sonia`` (the BoE database), ``boe_ois_curve``, ``ecb_aaa_spot`` and ``fred`` (the fallback, through the FRED route)."""

    offline = False

    def __init__(self, ecb: EcbData, nyfed: NyFed, boe: BoeData, fred_latest: Callable[[str, int], List[Tuple[date, Decimal]]]):
        self.ecb, self.nyfed, self.boe, self._fred = ecb, nyfed, boe, fred_latest

    def estr(self, n: int = 10):
        return self.ecb.estr(n)

    def estr_index(self, n: int = 10):
        return self.ecb.estr_index(n)

    def sofr_index(self, n: int = 10):
        return self.nyfed.sofr_index(n)

    def sonia(self, since: Optional[date] = None) -> Dict[str, "Observations"]:
        return self.boe.iadb(("IUDSOIA", "IUDZOS2"), since)

    def boe_ois_curve(self) -> Dict[date, Dict[str, Decimal]]:
        return self.boe.ois_curve()

    def ecb_aaa_spot(self, n: int = 3):
        return self.ecb.aaa_spot(n)

    def fred(self, series_id: str, lookback_days: int = 30) -> "Observations":
        rows = self._fred(series_id, lookback_days)
        own = getattr(rows, "source", None)
        return Observations(list(rows), f"FRED ({series_id})" if not own or own == "FRED" else own)

    def configured(self) -> bool:
        return True


class DebtSources:
    """``ProviderSet.debt`` (live): the calls the debt refresher makes, one provider each (their own health rows)."""

    def __init__(self, fiscal: FiscalData, tdirect: TreasuryDirect, treasury: "TreasuryCurve", ecb: EcbData, mof: MofJgb):
        self.fiscal, self.tdirect, self.treasury, self.ecb, self.mof = fiscal, tdirect, treasury, ecb, mof

    def mspd(self):
        return self.fiscal.mspd()

    def auctions(self, since: date):
        return self.fiscal.auctions(since)

    def fedinvest(self):
        return self.tdirect.fedinvest()

    def real_curve(self):
        return self.treasury.real_latest()

    def ecb_aaa_curve(self):
        return self.ecb.aaa_par_curve()

    def jgb_curve(self):
        return self.mof.jgb_curve()


class Frankfurter(_Base):
    name = "frankfurter"

    def __init__(self, clock, health=None, getter=None, sleep=_time.sleep, log=None):
        super().__init__(clock, health, getter, sleep, rps=1.0, log=log)

    def latest(self) -> FxRates:
        return parse_frankfurter(self._get(FRANKFURTER_URL, TERMINAL_UA, 20.0), self._now())


class FinraShortVolume(_Base):
    name = "finra"

    def __init__(self, clock, health=None, getter=None, sleep=_time.sleep, log=None):
        super().__init__(clock, health, getter, sleep, rps=1.0, log=log)

    def daily(self, d: date, wanted: Optional[Iterable[str]] = None) -> Optional[Dict[str, Tuple[int, int]]]:
        """None when FINRA has no file for the date (holiday / not yet published)."""
        try:
            raw = self._get(FINRA_URL.format(yyyymmdd=d.strftime("%Y%m%d")), GOV_UA, 60.0, accept="text/plain", attempts=2)
        except FetchError as e:
            if e.status in (403, 404):
                return None
            raise
        return parse_finra(raw, wanted)


# ---- the fake -------------------------------------------------------------------------------------------------------------------
class FakeProvider:
    """One object that answers for every provider (tests, ``serve --offline``).  It returns exactly what the test set
    and ``FetchError`` 404 otherwise."""

    name = "fake"

    def __init__(self, clock=None, health: Optional[ProviderHealth] = None):
        self.clock = clock
        self.health = health
        self._lock = threading.RLock()
        self._quotes: Dict[str, YahooIntraday] = {}
        self._daily: Dict[str, List[DailyBar]] = {}
        self._minute: Dict[str, List[Bar1m]] = {}           # V1061R1-02: 1-minute bars a test sets (``set_minute_bars``)
        self._meta: Dict[str, dict] = {}
        self._events: Dict[str, Events] = {}
        self._chains: Dict[str, ChainSnapshot] = {}
        self._rates: Dict[str, List[Tuple[date, Decimal]]] = {}
        self._curve: Optional[ParCurve] = None
        self._dfs: Optional[Tuple[Dict[Decimal, Decimal], date]] = None
        self._fx: Optional[FxRates] = None
        self._finra: Dict[date, Dict[str, Tuple[int, int]]] = {}
        self._errors: Dict[str, Exception] = {}
        self.calls: Dict[str, int] = {}
        self.refused_ua = False
        # debt sources (set by a test or the offline walk; raw payloads go through the live parsers)
        self._mspd: Optional[Tuple[Optional[date], List[dict]]] = None
        self._auctions: List[dict] = []
        self._fedinvest: Optional[Tuple[Optional[date], Dict[str, dict]]] = None
        self._real_curve: Optional[ParCurve] = None
        self._ecb_curve: Optional[Tuple[date, Dict[Decimal, Decimal]]] = None
        self._jgb_curve: Optional[Tuple[date, Dict[Decimal, Decimal]]] = None
        self._nyfed: List[Tuple[date, Decimal]] = []
        self._ecb_dfr: List[Tuple[date, Decimal]] = []
        self._fred_maintenance = False
        # FX_CCS_PLAN K2 (1.0.6.1): the RFR prints, indices and term curves a test sets (``set_rfr`` / ``set_boe_curve`` /
        # ``set_ecb_spot``); ``configured()`` is False until one is set, and the worker's rfr / curves jobs then stay quiet offline
        self._rfr: Dict[str, List[Tuple[date, Decimal]]] = {}
        self._boe_curve: Dict[date, Dict[str, Decimal]] = {}
        self._ecb_spot: Dict[date, Dict[str, Decimal]] = {}
        self._ecb_sv: Dict[date, Dict[str, Decimal]] = {}
        self._rfr_set = False
        self.fred_route = FredRoute(None, None, self._fred_primary, None, {"SOFR": (self._fb_sofr, SOFR_FALLBACK_SOURCE),
                                                                           "ECBDFR": (self._fb_ecbdfr, ECBDFR_FALLBACK_SOURCE)})

    # ---- setters ----------------------------------------------------------------------------------------------------
    def _now(self) -> int:
        return int(self.clock.epoch()) if self.clock is not None else int(_time.time())

    def set(self, symbol: str, last=None, quote_time: Optional[int] = None, bar: Optional[Tuple[int, Optional[int]]] = None, bid=None, ask=None,
            prev_close=None, day_high=None, day_low=None, day_volume: Optional[int] = None, provider_session: Optional[Tuple[int, int]] = None,
            currency: str = "USD", exchange_tz: Optional[str] = "America/New_York", bars: Optional[List[Bar1m]] = None, meta: Optional[dict] = None) -> None:
        """``bar=(t, v)`` is the latest COMPLETED 1-minute bar (o/h/l/c = last)."""
        px = None if last is None else qprice(last)
        tail = list(bars or [])
        b = None
        if bar is not None:
            b = Bar1m(int(bar[0]), px, px, px, px, None if bar[1] is None else int(bar[1]))
            if not tail:
                tail = [b]
        elif tail:
            b = tail[-1]
        m = dict(meta or {})
        m.setdefault("symbol", symbol)
        m.setdefault("currency", currency)
        with self._lock:
            self._quotes[symbol] = YahooIntraday(symbol, m, px, quote_time, None if prev_close is None else qprice(prev_close),
                                                 None if day_high is None else qprice(day_high), None if day_low is None else qprice(day_low), day_volume,
                                                 currency, exchange_tz, provider_session, b, tail[-5:], self._now())
            self._errors.pop(symbol, None)
            if bid is not None or ask is not None:
                self._quotes[symbol].meta["bid"] = None if bid is None else str(qprice(bid))
                self._quotes[symbol].meta["ask"] = None if ask is None else str(qprice(ask))

    def set_daily(self, symbol: str, bars: Iterable[Tuple]) -> None:
        """``[(date|iso, close) | (date, o, h, l, c, v)]``."""
        out = []
        for b in bars:
            d = b[0] if isinstance(b[0], date) else date.fromisoformat(str(b[0]))
            if len(b) == 2:
                out.append(DailyBar(d, qprice(b[1]), qprice(b[1]), qprice(b[1]), qprice(b[1]), None))
            else:
                out.append(DailyBar(d, qprice(b[1]), qprice(b[2]), qprice(b[3]), qprice(b[4]), None if b[5] is None else int(b[5])))
        with self._lock:
            self._daily[symbol] = sorted(out, key=lambda x: x.date)

    def set_minute_bars(self, symbol: str, bars: Iterable[Tuple]) -> None:
        """V1061R1-02: ``[(t, close) | (t, o, h, l, c, v)]``, ``t`` the bar's start (epoch seconds, minute-aligned)."""
        out = []
        for b in bars:
            if len(b) == 2:
                px = qprice(b[1])
                out.append(Bar1m(int(b[0]), px, px, px, px, None))
            else:
                out.append(Bar1m(int(b[0]), qprice(b[1]), qprice(b[2]), qprice(b[3]), qprice(b[4]), None if b[5] is None else int(b[5])))
        with self._lock:
            self._minute[symbol] = sorted(out, key=lambda x: x.t)

    def set_meta(self, symbol: str, meta: dict) -> None:
        with self._lock:
            self._meta[symbol] = clean_meta(dict(meta, symbol=meta.get("symbol", symbol)))

    def set_events(self, symbol: str, dividends: Iterable[Tuple] = (), splits: Iterable[Tuple] = ()) -> None:
        """``dividends=[("2026-11-09", "0.26")]``, ``splits=[("2026-11-12", "4:1")]``."""
        divs = [DivEvent(date.fromisoformat(str(d)), qprice(a)) for d, a in dividends]
        sps = []
        for d, r in splits:
            n, m = (str(r).split(":") + ["1"])[:2]
            sps.append(SplitEvent(date.fromisoformat(str(d)), int(n), int(m)))
        with self._lock:
            self._events[symbol] = Events(sorted(divs, key=lambda x: x.ex_date), sorted(sps, key=lambda x: x.date))

    def set_chain(self, cboe_symbol: str, snapshot=None, underlying_id: Optional[str] = None, payload: Optional[dict] = None) -> None:
        """A ``ChainSnapshot`` or a raw Cboe-shaped ``payload`` (parsed and reduced exactly like the live provider)."""
        if snapshot is None:
            if payload is None:
                raise ValueError("snapshot or payload required")
            today = self.clock.today() if self.clock is not None else date.today()
            snapshot = parse_chain(payload, cboe_symbol, underlying_id or cboe_symbol.lstrip("_"), today, self._now())
        with self._lock:
            self._chains[cboe_symbol] = snapshot

    def set_rate(self, series_id: str, observations=None, value=None, as_of=None) -> None:
        """``set_rate("SOFR", [("2026-10-19", "3.87")])`` or ``set_rate("SOFR", value="3.87", as_of="2026-10-19")``."""
        obs = list(observations or [])
        if value is not None:
            obs.append((as_of, value))
        rows = [(d if isinstance(d, date) else date.fromisoformat(str(d)), qprice(v)) for d, v in obs]
        with self._lock:
            self._rates[series_id] = sorted(rows)

    def set_curve(self, points: Dict, as_of) -> None:
        """``points`` keyed by label ('2Y') or years; values in percent."""
        pts: Dict[Decimal, Decimal] = {}
        for k, v in points.items():
            years = TENOR_YEARS.get(k) if isinstance(k, str) else Decimal(str(k)).quantize(TENOR_Q)
            if years is None:
                years = Decimal(str(k)).quantize(TENOR_Q)
            pts[years] = Decimal(str(v)).quantize(Decimal("0.0001"))
        with self._lock:
            self._curve = ParCurve(as_of if isinstance(as_of, date) else date.fromisoformat(str(as_of)), pts, source="fixed par curve (test)", fetched_at=self._now())

    def set_curve_dfs(self, dfs: Dict, as_of) -> None:
        """The §19.15 FixedCurve: semi-annual discount factors given directly ``{0.5: "0.98039216", 1: ...}``."""
        out = {Decimal(str(t)).quantize(Decimal("0.0001")): Decimal(str(v)) for t, v in dfs.items()}
        with self._lock:
            self._dfs = (out, as_of if isinstance(as_of, date) else date.fromisoformat(str(as_of)))

    def set_fx(self, per_usd: Dict[str, str], as_of) -> None:
        with self._lock:
            self._fx = FxRates(as_of if isinstance(as_of, date) else date.fromisoformat(str(as_of)), {k: qprice(v) for k, v in per_usd.items()},
                               source="fixed ECB reference (test)", fetched_at=self._now())

    def set_finra(self, d, rows: Dict[str, Tuple[int, int]]) -> None:
        with self._lock:
            self._finra[d if isinstance(d, date) else date.fromisoformat(str(d))] = {k.upper(): (int(a), int(b)) for k, (a, b) in rows.items()}

    # ---- debt sources -----------------------------------------------------------------------------------------------------
    def set_mspd(self, raw=None, record_date=None, securities: Optional[List[dict]] = None) -> None:
        """Raw MSPD JSON (parsed like the live provider) or parsed ``securities`` with their ``record_date``."""
        with self._lock:
            self._mspd = parse_mspd(raw) if raw is not None else (None if record_date is None else date.fromisoformat(str(record_date)), list(securities or []))

    def set_auctions(self, raw=None, rows: Optional[List[dict]] = None) -> None:
        with self._lock:
            self._auctions = parse_auctions(raw) if raw is not None else list(rows or [])

    def set_fedinvest(self, raw=None, price_date=None, rows: Optional[Dict[str, dict]] = None) -> None:
        with self._lock:
            if raw is not None:
                self._fedinvest = parse_fedinvest(raw)
            else:
                self._fedinvest = (None if price_date is None else date.fromisoformat(str(price_date)), dict(rows or {}))

    def set_real_curve(self, raw=None, points: Optional[Dict] = None, as_of=None) -> None:
        with self._lock:
            if raw is not None:
                self._real_curve = parse_treasury_real_xml(raw, self._now())
            else:
                pts = {Decimal(str(k)).quantize(TENOR_Q): Decimal(str(v)) for k, v in (points or {}).items()}
                self._real_curve = ParCurve(as_of if isinstance(as_of, date) else date.fromisoformat(str(as_of)), pts,
                                            source="treasury.gov daily real yield curve (test)", fetched_at=self._now())

    def set_ecb_curve(self, raw=None, points: Optional[Dict] = None, as_of=None) -> None:
        with self._lock:
            if raw is not None:
                self._ecb_curve = ecb_curve_from(parse_ecb_csv(raw))
            else:
                self._ecb_curve = (as_of if isinstance(as_of, date) else date.fromisoformat(str(as_of)),
                                   {Decimal(str(k)).quantize(TENOR_Q): Decimal(str(v)) for k, v in (points or {}).items()})

    def set_jgb_curve(self, raw=None, points: Optional[Dict] = None, as_of=None) -> None:
        with self._lock:
            if raw is not None:
                self._jgb_curve = parse_mof_jgb(raw)
            else:
                self._jgb_curve = (as_of if isinstance(as_of, date) else date.fromisoformat(str(as_of)),
                                   {Decimal(str(k)).quantize(TENOR_Q): Decimal(str(v)) for k, v in (points or {}).items()})

    def set_nyfed_sofr(self, raw=None, observations=None) -> None:
        with self._lock:
            self._nyfed = parse_nyfed_rates(raw) if raw is not None else [(d if isinstance(d, date) else date.fromisoformat(str(d)), qprice(v))
                                                                          for d, v in (observations or [])]

    def set_ecb_dfr(self, raw=None, observations=None) -> None:
        with self._lock:
            if raw is not None:
                self._ecb_dfr = next(iter(parse_ecb_csv(raw).values()), [])
            else:
                self._ecb_dfr = [(d if isinstance(d, date) else date.fromisoformat(str(d)), qprice(v)) for d, v in (observations or [])]

    # ---- RFR and term curves (FX_CCS_PLAN K2, 1.0.6.1) ---------------------------------------------------------------
    def set_rfr(self, series: str, observations=None) -> None:
        """``set_rfr("ESTR", [("2026-10-27", "2.440")])``: ESTR, ESTR_INDEX, SOFR_INDEX, IUDSOIA or IUDZOS2 (the BoE database codes),
        exact values.  A FRED fallback reads ``set_rate`` as before."""
        rows = [(d if isinstance(d, date) else date.fromisoformat(str(d)), Decimal(str(v))) for d, v in (observations or [])]
        with self._lock:
            self._rfr[str(series).upper()] = sorted(rows)
            self._rfr_set = True

    def set_boe_curve(self, as_of=None, points: Optional[Dict] = None, raw: Optional[bytes] = None) -> None:
        """One day's UK OIS spot curve ``{"1M": "3.899390", ...}`` (or a raw yield-curve zip through the live parser)."""
        with self._lock:
            if raw is not None:
                self._boe_curve.update(parse_boe_ois_zip(raw))
            else:
                d = as_of if isinstance(as_of, date) else date.fromisoformat(str(as_of))
                self._boe_curve[d] = {str(k): Decimal(str(v)) for k, v in (points or {}).items()}
            self._rfr_set = True

    def set_ecb_spot(self, as_of=None, points: Optional[Dict] = None, sv: Optional[Dict] = None, raw=None) -> None:
        """One day's ECB AAA spot rates ``{"3M": "2.542643", ...}`` and Svensson parameters (or a raw ECB CSV through the live parser)."""
        with self._lock:
            if raw is not None:
                spot_, sv_ = ecb_spot_from(parse_ecb_csv_exact(raw))
                self._ecb_spot.update(spot_)
                self._ecb_sv.update(sv_)
            else:
                d = as_of if isinstance(as_of, date) else date.fromisoformat(str(as_of))
                self._ecb_spot[d] = {str(k): Decimal(str(v)) for k, v in (points or {}).items()}
                if sv:
                    self._ecb_sv[d] = {str(k): Decimal(str(v)) for k, v in sv.items()}
            self._rfr_set = True

    def configured(self) -> bool:
        """True once a test set an RFR print, an index or a term curve (the worker's rfr / curves jobs are quiet offline before)."""
        with self._lock:
            return self._rfr_set

    def _rfr_rows(self, series: str, what: str, provider: str, source: str) -> "Observations":
        self._count(what, provider, series)
        with self._lock:
            rows = self._rfr.get(series)
        if rows is None:
            raise FetchError(f"no {series} set", status=404, retryable=False)
        return Observations(list(rows), source)

    def estr(self, n: int = 10):
        return self._rfr_rows("ESTR", "estr", "ecb", ESTR_SOURCE)

    def estr_index(self, n: int = 10):
        return self._rfr_rows("ESTR_INDEX", "estr_index", "ecb", ESTR_INDEX_SOURCE)

    def sofr_index(self, n: int = 10):
        return self._rfr_rows("SOFR_INDEX", "sofr_index", "nyfed", SOFR_INDEX_SOURCE)

    def sonia(self, since: Optional[date] = None) -> Dict[str, "Observations"]:
        self._count("boe_iadb", "boe", "BOE_IADB")              # its own failure key (FRED's IUDSOIA fallback is keyed "IUDSOIA")
        with self._lock:
            got = {c: self._rfr.get(c) for c in ("IUDSOIA", "IUDZOS2")}
        if all(v is None for v in got.values()):
            raise FetchError("no Bank of England database series set", status=404, retryable=False)
        return {c: Observations([r for r in (v or []) if since is None or r[0] >= since], BOE_IADB_SOURCE.format(code=c)) for c, v in got.items()}

    def boe_ois_curve(self) -> Dict[date, Dict[str, Decimal]]:
        self._count("boe_curve", "boe", "BOE_OIS_SPOT")
        with self._lock:
            if not self._boe_curve:
                raise FetchError("no Bank of England OIS curve set", status=404, retryable=False)
            return {d: dict(v) for d, v in self._boe_curve.items()}

    def ecb_aaa_spot(self, n: int = 3):
        self._count("ecb_spot", "ecb", "ECB_AAA_SPOT")
        with self._lock:
            if not self._ecb_spot:
                raise FetchError("no ECB AAA spot curve set", status=404, retryable=False)
            return {d: dict(v) for d, v in self._ecb_spot.items()}, {d: dict(v) for d, v in self._ecb_sv.items()}

    def fred(self, series_id: str, lookback_days: int = 30) -> "Observations":
        rows = self.latest(series_id, lookback_days)
        own = getattr(rows, "source", None)
        return Observations(list(rows), f"FRED ({series_id})" if not own or own == "FRED" else own)

    def set_fred_maintenance(self, on: bool = True) -> None:
        """FRED answers its HTML maintenance page (ORC-8) until switched off."""
        with self._lock:
            self._fred_maintenance = bool(on)
        if not on:
            self.fred_route._left_maintenance()

    def mspd(self):
        self._count("mspd", "fiscaldata", "mspd")
        with self._lock:
            if self._mspd is None:
                raise FetchError("no MSPD set", status=404, retryable=False)
            return self._mspd[0], list(self._mspd[1])

    def auctions(self, since: date):
        self._count("auctions", "fiscaldata", "auctions")
        with self._lock:
            return [a for a in self._auctions if a.get("issue") is None or a["issue"] >= since]

    def fedinvest(self):
        self._count("fedinvest", "treasurydirect", "fedinvest")
        with self._lock:
            if self._fedinvest is None:
                raise FetchError("no FedInvest prices set", status=404, retryable=False)
            return self._fedinvest[0], dict(self._fedinvest[1])

    def real_curve(self):
        self._count("real_curve", "treasury", "real_curve")
        with self._lock:
            return self._real_curve

    def ecb_aaa_curve(self):
        self._count("ecb_curve", "ecb", "ecb_curve")
        with self._lock:
            return self._ecb_curve

    def jgb_curve(self):
        self._count("jgb_curve", "mof", "jgb_curve")
        with self._lock:
            return self._jgb_curve

    def _fred_primary(self, series_id: str, lookback_days: int):
        self._count("fred", "fred", series_id)
        with self._lock:
            maint = self._fred_maintenance
            rows = list(self._rates.get(series_id) or [])
        if maint:
            raise FetchError(FRED_MAINTENANCE, retryable=False, kind="maintenance")
        return rows

    def _fb_sofr(self, lookback_days: int):
        self._count("nyfed", "nyfed", "nyfed")
        with self._lock:
            return list(self._nyfed)

    def _fb_ecbdfr(self, lookback_days: int):
        self._count("ecb_dfr", "ecb", "ecb_dfr")
        with self._lock:
            return list(self._ecb_dfr)

    def fail(self, key: str, exc: Optional[Exception] = None) -> None:
        """Make ``key`` (a symbol, a Cboe symbol, a series id, or a provider name) raise ``exc`` (default a 503)."""
        with self._lock:
            self._errors[key] = exc or FetchError("HTTP 503", status=503)

    def clear_failure(self, key: str) -> None:
        with self._lock:
            self._errors.pop(key, None)

    # ---- provider interface ------------------------------------------------------------------------------------------
    def _count(self, what: str, provider: str, key: Optional[str] = None) -> None:
        with self._lock:
            self.calls[what] = self.calls.get(what, 0) + 1
            err = self._errors.get(key) if key is not None else None
            if err is None:
                err = self._errors.get(provider)
        if err is not None:
            if self.health is not None:
                self.health.record(provider, False, getattr(err, "status", None), str(err))
            raise err
        if self.health is not None:
            self.health.record(provider, True, 200)

    def intraday(self, symbol: str) -> YahooIntraday:
        self._count("intraday", "yahoo", symbol)
        with self._lock:
            q = self._quotes.get(symbol)
        if q is None:
            raise FetchError(f"{symbol}: not found", status=404, retryable=False)
        return YahooIntraday(q.symbol, dict(q.meta), q.last, q.quote_time, q.prev_close, q.day_high, q.day_low, q.day_volume, q.currency, q.exchange_tz,
                             q.provider_session, q.bar, list(q.bars_tail), self._now())

    def daily(self, symbol: str, days: int = 10) -> List[DailyBar]:
        self._count("daily", "yahoo", symbol)
        with self._lock:
            bars = list(self._daily.get(symbol) or [])
        return bars[-days:] if days > 0 else bars

    def minute_bars(self, symbol: str, start: int, end: int) -> List[Bar1m]:
        self._count("minute_bars", "yahoo", symbol)
        with self._lock:
            bars = list(self._minute.get(symbol) or [])
        return [b for b in bars if int(start) <= int(b.t) < int(end)]

    def meta(self, symbol: str) -> dict:
        self._count("meta", "yahoo", symbol)
        with self._lock:
            m = self._meta.get(symbol)
            if m is None and symbol in self._quotes:
                m = clean_meta(self._quotes[symbol].meta)
        if m is None:
            raise FetchError(f"{symbol}: not found", status=404, retryable=False)
        return dict(m)

    def events(self, symbol: str) -> Events:
        self._count("events", "yahoo", symbol)
        with self._lock:
            return self._events.get(symbol) or Events([], [])

    def chain(self, cboe_symbol: str, underlying_id: Optional[str] = None) -> ChainSnapshot:
        self._count("chain", "cboe", cboe_symbol)
        with self._lock:
            snap = self._chains.get(cboe_symbol)
        if snap is None:
            raise FetchError(f"no chain for {cboe_symbol}", status=404, retryable=False)
        return snap

    def latest(self, series_id: str, lookback_days: int = 60) -> List[Tuple[date, Decimal]]:
        """FRED through the same ``FredRoute`` as live (maintenance -> NY Fed / ECB fallbacks with their source)."""
        route = self.fred_route
        if route.clock is None and self.clock is not None:
            route.clock = self.clock
        if route.health is None and self.health is not None:
            route.health = self.health
        return route.latest(series_id, lookback_days)

    def curve(self) -> Optional[ParCurve]:
        self._count("curve", "treasury")
        with self._lock:
            return self._curve

    def curve_dfs(self) -> Optional[Tuple[Dict[Decimal, Decimal], date]]:
        with self._lock:
            return self._dfs

    def fx_latest(self) -> FxRates:
        self._count("frankfurter", "frankfurter")
        with self._lock:
            if self._fx is None:
                raise FetchError("no FX rates set", status=404, retryable=False)
            return self._fx

    def finra_daily(self, d: date, wanted: Optional[Iterable[str]] = None) -> Optional[Dict[str, Tuple[int, int]]]:
        self._count("finra", "finra")
        with self._lock:
            rows = self._finra.get(d)
        if rows is None:
            return None
        if wanted is not None:
            want = {s.upper() for s in wanted}
            return {k: v for k, v in rows.items() if k in want}
        return dict(rows)


class ProviderSet:
    """The bundle the worker and the refresher use: ``yahoo`` (intraday/daily/meta/events), ``cboe`` (chain),
    ``treasury`` (curve / curve_dfs), ``fred`` (latest; a ``FredRoute`` live), ``frankfurter`` (fx_latest), ``finra``
    (finra_daily), ``debt`` (mspd / auctions / fedinvest / real_curve / ecb_aaa_curve / jgb_curve), ``health``."""

    def __init__(self, yahoo, cboe, treasury, fred, frankfurter, finra, health: ProviderHealth, fake: Optional[FakeProvider] = None, debt=None,
                 rfr=None):
        self.yahoo, self.cboe, self.treasury, self.fred, self.frankfurter, self.finra = yahoo, cboe, treasury, fred, frankfurter, finra
        self.health = health
        self.fake = fake
        self.debt = debt if debt is not None else fake
        self.rfr = rfr if rfr is not None else fake            # FX_CCS_PLAN K2: RfrSources live, the FakeProvider offline

    @property
    def offline(self) -> bool:
        return self.fake is not None

    @classmethod
    def live(cls, clock, log=None, getter: Optional[Getter] = None, sleep: Callable[[float], None] = _time.sleep, fred_api_key: Optional[str] = None) -> "ProviderSet":
        health = ProviderHealth(lambda: int(clock.epoch()), day=lambda: clock.today().isoformat())
        kw = dict(health=health, getter=getter, sleep=sleep, log=log)
        treasury = TreasuryCurve(clock, **kw)
        key = fred_api_key if fred_api_key is not None else (os.environ.get("FRED_API_KEY") or None)
        fred = FredSeries(clock, api_key=key, **kw)
        nyfed, ecb = NyFed(clock, **kw), EcbData(clock, **kw)
        route = FredRoute(clock, health, fred.csv, fred.keyed if fred.has_key else None,
                          {"SOFR": (lambda n: nyfed.sofr(min(30, max(5, int(n) // 2))), SOFR_FALLBACK_SOURCE),
                           "ECBDFR": (lambda n: ecb.deposit_rate(min(30, max(5, int(n) // 2))), ECBDFR_FALLBACK_SOURCE)}, log=log)
        debt = DebtSources(FiscalData(clock, **kw), TreasuryDirect(clock, **kw), treasury, ecb, MofJgb(clock, **kw))
        rfr = RfrSources(ecb, nyfed, BoeData(clock, **kw), route.latest)
        return cls(YahooChart(clock, **kw), CboeChain(clock, **kw), _TreasuryAdapter(treasury), route, _FrankfurterAdapter(Frankfurter(clock, **kw)),
                   _FinraAdapter(FinraShortVolume(clock, **kw)), health, debt=debt, rfr=rfr)

    @classmethod
    def offline_set(cls, fp: FakeProvider, clock=None) -> "ProviderSet":
        if fp.health is None:
            fp.health = ProviderHealth(lambda: int(clock.epoch()) if clock is not None else int(_time.time()))
        if fp.clock is None and clock is not None:
            fp.clock = clock
        return cls(fp, fp, fp, fp, fp, fp, fp.health, fake=fp)


class _TreasuryAdapter:
    def __init__(self, p: TreasuryCurve):
        self._p = p

    def curve(self) -> Optional[ParCurve]:
        return self._p.latest()

    @staticmethod
    def curve_dfs():
        return None


class _FrankfurterAdapter:
    def __init__(self, p: Frankfurter):
        self._p = p

    def fx_latest(self) -> FxRates:
        return self._p.latest()


class _FinraAdapter:
    def __init__(self, p: FinraShortVolume):
        self._p = p

    def finra_daily(self, d: date, wanted=None):
        return self._p.daily(d, wanted)


__all__ = ["YahooChart", "CboeChain", "TreasuryCurve", "FredSeries", "FredRoute", "Observations", "Frankfurter", "FinraShortVolume", "FakeProvider",
           "ProviderSet", "Getter", "FiscalData", "TreasuryDirect", "NyFed", "EcbData", "MofJgb", "DebtSources", "ecb_curve_from", "ecb_spot_from",
           "BoeData", "RfrSources", "NYFED_SOFRAI_URL", "BOE_IADB_URL", "BOE_CURVE_URL", "ECB_ESTR", "ECB_ESTR_INDEX", "ECB_AAA_SPOT_KEY",
           "ECB_AAA_SPOT_TENORS", "ECB_AAA_SV_PARAMS", "RFR_TIMEOUT_S", "RFR_ATTEMPTS", "ESTR_SOURCE", "ESTR_INDEX_SOURCE", "SOFR_INDEX_SOURCE",
           "BOE_IADB_SOURCE", "BOE_CURVE_SOURCE", "ECB_SPOT_SOURCE", "FRED_RFR",
           "YAHOO_HOSTS", "CBOE_URL", "TREASURY_URL", "FRED_URL", "FRED_KEY_URL", "FRED_PAUSE_S", "FRANKFURTER_URL", "FINRA_URL", "TREASURY_REAL_URL",
           "MSPD_URL", "MSPD_LATEST_URL", "AUCTIONS_URL", "FEDINVEST_URL", "NYFED_SOFR_URL", "ECB_URL", "MOF_JGB_URL", "SOFR_FALLBACK_SOURCE",
           "ECBDFR_FALLBACK_SOURCE",
           # re-exported from parsers
           "num", "dec", "intv", "et_naive_to_epoch", "YahooIntraday", "DailyBar", "DivEvent", "SplitEvent", "Events", "ChainUnderlying", "ChainRow",
           "ChainSnapshot", "ParCurve", "FxRates", "chart_result", "chart_bars", "clean_meta", "parse_intraday", "parse_daily", "parse_events", "parse_chain",
           "parse_treasury_xml", "parse_fred_csv", "parse_frankfurter", "parse_finra", "OCC_TAIL", "TENORS", "TENOR_Q", "TENOR_YEARS", "YAHOO_DELAY_S",
           "YAHOO_DELAY_LABEL", "DAILY_DELAY_LABEL"]
