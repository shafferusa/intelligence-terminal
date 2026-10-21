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
- Every live provider takes an injectable ``getter(url, ua, timeout, accept) -> bytes`` and ``sleep`` so tests run
  without a network; with an injected sleep the rate limiter's clock advances with it, never with wall time.
- ``FakeProvider`` serves tests and ``serve --offline``: quotes, daily bars, meta, chains (raw payloads reduced exactly
  like the live parser), curves (par points or direct discount factors), FRED observations, FX, FINRA rows and
  dividend / split events set by the test; every method counts its calls and can be told to fail, so error paths are
  testable.  Nothing is invented by the provider itself.
"""
from __future__ import annotations

import json
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
from .parsers import (DAILY_DELAY_LABEL, OCC_TAIL, TENOR_Q, TENOR_YEARS, TENORS, YAHOO_DELAY_LABEL, YAHOO_DELAY_S, ChainRow, ChainSnapshot, ChainUnderlying,  # noqa: F401
                      DailyBar, DivEvent, Events, FxRates, ParCurve, SplitEvent, YahooIntraday, _chart_error, chart_bars, chart_result, clean_meta, dec,
                      et_naive_to_epoch, intv, num, parse_chain, parse_daily, parse_events, parse_finra, parse_frankfurter, parse_fred_csv, parse_intraday,
                      parse_treasury_xml)

YAHOO_HOSTS = ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com")
CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json"
TREASURY_URL = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
                "?data=daily_treasury_yield_curve&field_tdr_date_value_month={yyyymm}")
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={start}"
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


class FredSeries(_Base):
    name = "fred"
    _ID = re.compile(r"^[A-Z0-9]{1,30}$")

    def __init__(self, clock, health=None, getter=None, sleep=_time.sleep, log=None):
        super().__init__(clock, health, getter, sleep, rps=FRED_RPS, log=log)

    def latest(self, series_id: str, lookback_days: int = 60) -> List[Tuple[date, Decimal]]:
        if not self._ID.match(str(series_id)):
            raise FetchError("invalid FRED series id", status=400, retryable=False)
        start = (self.clock.today() - timedelta(days=int(lookback_days))).isoformat()
        raw = self._get(FRED_URL.format(sid=series_id, start=start), GOV_UA, 20.0, accept="text/csv")
        return parse_fred_csv(raw)


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
        self._count("fred", "fred", series_id)
        with self._lock:
            return list(self._rates.get(series_id) or [])

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
    ``treasury`` (curve / curve_dfs), ``fred`` (latest), ``frankfurter`` (fx_latest), ``finra`` (finra_daily), ``health``."""

    def __init__(self, yahoo, cboe, treasury, fred, frankfurter, finra, health: ProviderHealth, fake: Optional[FakeProvider] = None):
        self.yahoo, self.cboe, self.treasury, self.fred, self.frankfurter, self.finra = yahoo, cboe, treasury, fred, frankfurter, finra
        self.health = health
        self.fake = fake

    @property
    def offline(self) -> bool:
        return self.fake is not None

    @classmethod
    def live(cls, clock, log=None, getter: Optional[Getter] = None, sleep: Callable[[float], None] = _time.sleep) -> "ProviderSet":
        health = ProviderHealth(lambda: int(clock.epoch()), day=lambda: clock.today().isoformat())
        kw = dict(health=health, getter=getter, sleep=sleep, log=log)
        return cls(YahooChart(clock, **kw), CboeChain(clock, **kw), _TreasuryAdapter(TreasuryCurve(clock, **kw)), FredSeries(clock, **kw),
                   _FrankfurterAdapter(Frankfurter(clock, **kw)), _FinraAdapter(FinraShortVolume(clock, **kw)), health)

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


__all__ = ["YahooChart", "CboeChain", "TreasuryCurve", "FredSeries", "Frankfurter", "FinraShortVolume", "FakeProvider", "ProviderSet", "Getter",
           "YAHOO_HOSTS", "CBOE_URL", "TREASURY_URL", "FRED_URL", "FRANKFURTER_URL", "FINRA_URL",
           # re-exported from parsers
           "num", "dec", "intv", "et_naive_to_epoch", "YahooIntraday", "DailyBar", "DivEvent", "SplitEvent", "Events", "ChainUnderlying", "ChainRow",
           "ChainSnapshot", "ParCurve", "FxRates", "chart_result", "chart_bars", "clean_meta", "parse_intraday", "parse_daily", "parse_events", "parse_chain",
           "parse_treasury_xml", "parse_fred_csv", "parse_frankfurter", "parse_finra", "OCC_TAIL", "TENORS", "TENOR_Q", "TENOR_YEARS", "YAHOO_DELAY_S",
           "YAHOO_DELAY_LABEL", "DAILY_DELAY_LABEL"]
