"""Pure parsers and shapes for the providers (TERMINAL_DESIGN.md §8.1, §8.4) — no network, no clock, no database.

Conventions
- Every payload is UNTRUSTED DATA: parsed with ``json`` / ``csv`` / ``xml.etree`` only, numbers through ``num()``
  (finite floats or None) and ``dec()`` (``Decimal`` from ``repr``, 8 dp), OCC symbols by a fixed pattern.
- Yahoo: ``last`` = ``meta.regularMarketPrice``, ``quote_time`` = ``meta.regularMarketTime`` (the provider's own clock),
  ``provider_session`` = ``currentTradingPeriod.regular``; the ``bar`` is the latest COMPLETED 1-minute bar
  (``t ≤ fetched_at − 60``), ``bars_tail`` the last five bars, never more (copied from finsim2 ``parse_chart``).
- Cboe (copied from finsim2 ``contracts``): reduced at parse to expiries ≤ ``CHAIN_MAX_DAYS`` and strikes within
  ± ``CHAIN_STRIKE_BAND`` of spot; implied vols in percent are normalised to decimals when the median exceeds 3; the
  chain's ``quote_time`` is the UNDERLYING's ``last_trade_time`` (naive Eastern → epoch).
- Treasury.gov: the latest ``NEW_DATE`` entry of the monthly par-curve feed, tenors 1M…30Y as years (8 dp).
- FRED: blank and "." observations are skipped.  Frankfurter: units of currency per USD.  FINRA: pipe file, values
  rounded HALF_EVEN to whole shares, rows with total ≤ 0 or short > 1.05 × total dropped.
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Dict, Iterable, List, Optional, Tuple

from ..clock import NY, UTC
from ..config import CHAIN_MAX_DAYS, CHAIN_STRIKE_BAND
from ..units import qprice
from . import Bar1m
from .http import FetchError

OCC_TAIL = re.compile(r"(\d{2})(\d{2})(\d{2})([CP])(\d{8})$")
TENORS: List[Tuple[str, str, Decimal]] = [   # (XML field, label, years)
    ("BC_1MONTH", "1M", Decimal(1) / Decimal(12)), ("BC_2MONTH", "2M", Decimal(2) / Decimal(12)), ("BC_3MONTH", "3M", Decimal("0.25")),
    ("BC_4MONTH", "4M", Decimal(4) / Decimal(12)), ("BC_6MONTH", "6M", Decimal("0.5")), ("BC_1YEAR", "1Y", Decimal(1)),
    ("BC_2YEAR", "2Y", Decimal(2)), ("BC_3YEAR", "3Y", Decimal(3)), ("BC_5YEAR", "5Y", Decimal(5)), ("BC_7YEAR", "7Y", Decimal(7)),
    ("BC_10YEAR", "10Y", Decimal(10)), ("BC_20YEAR", "20Y", Decimal(20)), ("BC_30YEAR", "30Y", Decimal(30))]
TENOR_Q = Decimal("0.00000001")
TENOR_YEARS: Dict[str, Decimal] = {label: years.quantize(TENOR_Q) for _, label, years in TENORS}
YAHOO_DELAY_S = 900
YAHOO_DELAY_LABEL = "up to 15 min delayed"
DAILY_DELAY_LABEL = "daily (prior business day)"


# ---- numbers ------------------------------------------------------------------------------------------------------------
def num(x) -> Optional[float]:
    """``x`` as a finite float, or None (strings, NaN, inf, bools are rejected)."""
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        v = float(x)
    elif isinstance(x, str):
        try:
            v = float(x.strip())
        except ValueError:
            return None
    else:
        return None
    return v if math.isfinite(v) else None


def dec(x) -> Optional[Decimal]:
    """``x`` as a canonical ``Decimal`` price (8 dp, from ``repr`` so 769.32 stays 769.32), or None."""
    v = num(x)
    if v is None:
        return None
    try:
        return qprice(Decimal(repr(v)))
    except (ValueError, ArithmeticError):
        return None


def intv(x) -> Optional[int]:
    v = num(x)
    if v is None or v < 0:
        return None
    return int(Decimal(repr(v)).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def _iso_date(s) -> Optional[date]:
    if not isinstance(s, str):
        return None
    try:
        return date.fromisoformat(s.strip()[:10])
    except ValueError:
        return None


def et_naive_to_epoch(s) -> Optional[int]:
    """Cboe ``last_trade_time`` ('2026-10-20T15:46:47', naive Eastern) → epoch seconds."""
    if not isinstance(s, str) or len(s) < 19:
        return None
    try:
        return int(datetime.fromisoformat(s[:19]).replace(tzinfo=NY).timestamp())
    except ValueError:
        return None


# ---- shapes -------------------------------------------------------------------------------------------------------------
@dataclass
class YahooIntraday:
    symbol: str
    meta: dict
    last: Optional[Decimal]
    quote_time: Optional[int]
    prev_close: Optional[Decimal]
    day_high: Optional[Decimal]
    day_low: Optional[Decimal]
    day_volume: Optional[int]
    currency: Optional[str]
    exchange_tz: Optional[str]
    provider_session: Optional[Tuple[int, int]]
    bar: Optional[Bar1m]
    bars_tail: List[Bar1m]
    fetched_at: int


@dataclass(frozen=True)
class DailyBar:
    date: date
    o: Optional[Decimal]
    h: Optional[Decimal]
    l: Optional[Decimal]
    c: Decimal
    v: Optional[int]


@dataclass(frozen=True)
class DivEvent:
    ex_date: date
    amount: Decimal


@dataclass(frozen=True)
class SplitEvent:
    date: date
    numerator: int
    denominator: int

    @property
    def ratio(self) -> str:
        return f"{self.numerator}:{self.denominator}"


@dataclass(frozen=True)
class Events:
    dividends: List[DivEvent]
    splits: List[SplitEvent]


@dataclass
class ChainUnderlying:
    symbol: str
    last: Optional[Decimal]
    bid: Optional[Decimal]
    ask: Optional[Decimal]
    bid_size: Optional[int]
    ask_size: Optional[int]
    last_trade_time: Optional[int]
    prev_close: Optional[Decimal]
    iv30: Optional[float]


class ChainRow:
    """One reduced contract (~ a few hundred bytes): money as Decimal, model inputs as floats."""
    __slots__ = ("occ", "expiry", "right", "strike", "bid", "ask", "bid_size", "ask_size", "last", "last_time", "iv", "delta", "gamma",
                 "theta", "vega", "oi", "volume")

    def __init__(self, occ, expiry, right, strike, bid, ask, bid_size, ask_size, last, last_time, iv, delta, gamma, theta, vega, oi, volume):
        self.occ, self.expiry, self.right, self.strike = occ, expiry, right, strike
        self.bid, self.ask, self.bid_size, self.ask_size = bid, ask, bid_size, ask_size
        self.last, self.last_time, self.iv = last, last_time, iv
        self.delta, self.gamma, self.theta, self.vega, self.oi, self.volume = delta, gamma, theta, vega, oi, volume

    @property
    def quoted(self) -> bool:
        return self.bid is not None and self.ask is not None and self.bid > 0 and self.ask >= self.bid

    @property
    def mid(self) -> Optional[Decimal]:
        return qprice((self.bid + self.ask) / 2) if self.quoted else None

    def to_dict(self) -> dict:
        return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in ((s, getattr(self, s)) for s in self.__slots__)}


@dataclass
class ChainSnapshot:
    underlying_id: str
    cboe_symbol: str
    underlying: ChainUnderlying
    contracts: List[ChainRow]
    fetched_at: int
    quote_time: Optional[int]
    n_raw: int
    source: str = "Cboe delayed quotes"
    delay_s: int = YAHOO_DELAY_S
    delay_label: str = YAHOO_DELAY_LABEL
    _by_occ: Optional[Dict[str, ChainRow]] = field(default=None, repr=False, compare=False)

    @property
    def spot(self) -> Optional[Decimal]:
        return self.underlying.last

    @property
    def iv30(self) -> Optional[float]:
        return self.underlying.iv30

    def expiries(self) -> List[str]:
        return sorted({c.expiry for c in self.contracts})

    def rows_for(self, expiry: str) -> List[ChainRow]:
        return [c for c in self.contracts if c.expiry == expiry]

    def by_occ(self, occ: str) -> Optional[ChainRow]:
        if self._by_occ is None:
            self._by_occ = {c.occ: c for c in self.contracts}
        return self._by_occ.get(occ)

    def n_kept(self) -> int:
        return len(self.contracts)


@dataclass
class ParCurve:
    as_of: date
    points: Dict[Decimal, Decimal]          # tenor years (8 dp) -> par yield in percent
    source: str = "treasury.gov daily par yield curve"
    fetched_at: int = 0

    def labels(self) -> Dict[str, Decimal]:
        inv = {years: label for label, years in TENOR_YEARS.items()}
        return {inv.get(t, str(t)): v for t, v in sorted(self.points.items())}

    def tenors(self) -> List[Decimal]:
        return sorted(self.points)


@dataclass
class FxRates:
    as_of: date
    per_usd: Dict[str, Decimal]            # units of currency per 1 USD (ECB reference, via Frankfurter)
    source: str = "Frankfurter (ECB reference rates)"
    fetched_at: int = 0


# ---- Yahoo parsing (copied from finsim2/data/yahoo.py::parse_chart, adapted to Decimal and 1-minute bars) -------------------
def _chart_error(payload) -> Optional[str]:
    if not isinstance(payload, dict):
        return "malformed response"
    chart = payload.get("chart")
    if not isinstance(chart, dict):
        return "malformed response (no chart)"
    err = chart.get("error")
    if err:
        if isinstance(err, dict):
            code = str(err.get("code") or "error")[:40]
            desc = str(err.get("description") or "")[:160]
            return f"{code}: {desc}".strip(": ")
        return str(err)[:200]
    res = chart.get("result")
    if not isinstance(res, list) or not res or not isinstance(res[0], dict):
        return "no result"
    return None


def chart_result(payload) -> dict:
    """``chart.result[0]`` of a Yahoo payload, or ``FetchError`` with the chart error text."""
    msg = _chart_error(payload)
    if msg:
        raise FetchError(msg, status=404 if "Not Found" in msg or "No data" in msg else None, retryable=False)
    return payload["chart"]["result"][0]


def _series(block, key, n):
    v = block.get(key) if isinstance(block, dict) else None
    if not isinstance(v, list):
        return [None] * n
    v = v[:n]
    return v + [None] * (n - len(v))


def chart_bars(result: dict) -> List[Bar1m]:
    """Every bar of ``chart.result[0]`` with a close (sorted by time; Decimal prices; volume int or None)."""
    ts = result.get("timestamp") if isinstance(result, dict) else None
    if not isinstance(ts, list):
        return []
    n = len(ts)
    ind = result.get("indicators") if isinstance(result.get("indicators"), dict) else {}
    quote = ind.get("quote")
    quote = quote[0] if isinstance(quote, list) and quote and isinstance(quote[0], dict) else {}
    opens, highs, lows = _series(quote, "open", n), _series(quote, "high", n), _series(quote, "low", n)
    closes, vols = _series(quote, "close", n), _series(quote, "volume", n)
    out: List[Bar1m] = []
    for i in range(n):
        t = ts[i]
        if not isinstance(t, (int, float)) or isinstance(t, bool):
            continue
        c = dec(closes[i])
        if c is None:
            continue
        o, h, lo = dec(opens[i]), dec(highs[i]), dec(lows[i])
        out.append(Bar1m(int(t), o if o is not None else c, h if h is not None else c, lo if lo is not None else c, c, intv(vols[i])))
    out.sort(key=lambda b: b.t)
    return out


def _clean_text(v, limit=120) -> Optional[str]:
    if not isinstance(v, str):
        return None
    s = "".join(ch for ch in v if ch.isprintable()).strip()
    return s[:limit] or None


def clean_meta(meta: dict) -> dict:
    """The meta fields the master and the record use, type-checked (strings cleaned, numbers finite)."""
    if not isinstance(meta, dict):
        return {}
    out: dict = {}
    for k in ("symbol", "currency", "instrumentType", "longName", "shortName", "exchangeName", "fullExchangeName", "exchangeTimezoneName"):
        out[k] = _clean_text(meta.get(k))
    for k in ("regularMarketPrice", "previousClose", "chartPreviousClose", "regularMarketDayHigh", "regularMarketDayLow"):
        out[k] = num(meta.get(k))
    for k in ("regularMarketTime", "regularMarketVolume", "gmtoffset", "firstTradeDate"):
        v = meta.get(k)
        out[k] = int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None
    tp = meta.get("currentTradingPeriod")
    reg = tp.get("regular") if isinstance(tp, dict) else None
    if isinstance(reg, dict):
        s, e = num(reg.get("start")), num(reg.get("end"))
        out["currentTradingPeriod"] = {"regular": {"start": int(s) if s is not None else None, "end": int(e) if e is not None else None}}
    else:
        out["currentTradingPeriod"] = None
    return out


def parse_intraday(result: dict, symbol: str, fetched_at: int) -> YahooIntraday:
    """``YahooIntraday`` from ``chart.result[0]`` of a ``range=1d&interval=1m`` chart."""
    meta = clean_meta(result.get("meta") if isinstance(result, dict) else {})
    bars = chart_bars(result)
    completed = [b for b in bars if b.t <= fetched_at - 60]
    bar = completed[-1] if completed else None
    tp = meta.get("currentTradingPeriod") or {}
    reg = tp.get("regular") or {}
    ps = (reg["start"], reg["end"]) if reg.get("start") is not None and reg.get("end") is not None else None
    last = dec(meta.get("regularMarketPrice"))
    prev = dec(meta.get("previousClose"))
    if prev is None:
        prev = dec(meta.get("chartPreviousClose"))
    return YahooIntraday(symbol, meta, last, meta.get("regularMarketTime"), prev, dec(meta.get("regularMarketDayHigh")), dec(meta.get("regularMarketDayLow")),
                         meta.get("regularMarketVolume"), (meta.get("currency") or None), meta.get("exchangeTimezoneName"), ps, bar, bars[-5:], fetched_at)


def _offset(meta) -> int:
    off = meta.get("gmtoffset") if isinstance(meta, dict) else None
    return int(off) if isinstance(off, (int, float)) and not isinstance(off, bool) and abs(off) < 86400 else 0


def _day_of(t, off: int) -> Optional[date]:
    try:
        return datetime.fromtimestamp(int(t) + off, UTC).date()
    except (OverflowError, OSError, ValueError, TypeError):
        return None


def parse_daily(result: dict) -> List[DailyBar]:
    """Daily bars (exchange-local dates via ``gmtoffset``), de-duplicated by date, close never None."""
    meta = result.get("meta") if isinstance(result, dict) and isinstance(result.get("meta"), dict) else {}
    off = _offset(meta)
    by_date: Dict[date, DailyBar] = {}
    for b in chart_bars(result):
        d = _day_of(b.t, off)
        if d is not None:
            by_date[d] = DailyBar(d, b.o, b.h, b.l, b.c, b.v)
    return [by_date[d] for d in sorted(by_date)]


def parse_events(result: dict) -> Events:
    """Dividends (ex-date, amount) and splits from ``chart.result[0].events`` (copied from finsim2 ``parse_events``)."""
    if not isinstance(result, dict):
        return Events([], [])
    off = _offset(result.get("meta"))
    ev = result.get("events") if isinstance(result.get("events"), dict) else {}
    divs: List[DivEvent] = []
    splits: List[SplitEvent] = []
    dv = ev.get("dividends")
    for e in (dv.values() if isinstance(dv, dict) else []):
        if not isinstance(e, dict):
            continue
        a, when = dec(e.get("amount")), _day_of(e.get("date"), off)
        if a is not None and a > 0 and when is not None:
            divs.append(DivEvent(when, a))
    sp = ev.get("splits")
    for e in (sp.values() if isinstance(sp, dict) else []):
        if not isinstance(e, dict):
            continue
        n, d, when = intv(e.get("numerator")), intv(e.get("denominator")), _day_of(e.get("date"), off)
        if n and d and when is not None and n > 0 and d > 0:
            splits.append(SplitEvent(when, n, d))
    divs.sort(key=lambda x: x.ex_date)
    splits.sort(key=lambda x: x.date)
    return Events(divs, splits)


# ---- Cboe parsing (copied from finsim2/data/cboe.py::contracts, reduced at parse) -------------------------------------------
def parse_chain(payload, cboe_symbol: str, underlying_id: str, today: date, fetched_at: int,
                max_days: int = CHAIN_MAX_DAYS, band: Decimal = CHAIN_STRIKE_BAND) -> ChainSnapshot:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise FetchError("malformed chain payload (no data)", retryable=False)
    spot = dec(data.get("current_price"))
    if spot is None:
        spot = dec(data.get("close"))
    iv30 = num(data.get("iv30"))
    if iv30 is not None and iv30 > 3.0:
        iv30 /= 100.0
    under = ChainUnderlying(str(data.get("symbol") or cboe_symbol)[:16], spot, dec(data.get("bid")), dec(data.get("ask")), intv(data.get("bid_size")),
                            intv(data.get("ask_size")), et_naive_to_epoch(data.get("last_trade_time")), dec(data.get("prev_day_close")), iv30)
    options = data.get("options")
    rows: List[ChainRow] = []
    n_raw = 0
    lo = hi = None
    if spot is not None and spot > 0:
        lo, hi = spot * (1 - band), spot * (1 + band)
    for o in (options if isinstance(options, list) else []):
        if not isinstance(o, dict):
            continue
        n_raw += 1
        occ = str(o.get("option") or "").strip()
        m = OCC_TAIL.search(occ)
        if not m or len(occ) > 24:
            continue
        yy, mm, dd, right, k = m.groups()
        try:
            exp = date(2000 + int(yy), int(mm), int(dd))
        except ValueError:
            continue
        days = (exp - today).days
        if days < 0 or days > max_days:
            continue
        strike = (Decimal(int(k)) / Decimal(1000)).normalize()
        if strike == strike.to_integral_value():
            strike = strike.quantize(Decimal(1))
        if lo is not None and (strike < lo or strike > hi):
            continue
        rows.append(ChainRow(occ, exp.isoformat(), right, strike, dec(o.get("bid")), dec(o.get("ask")), intv(o.get("bid_size")), intv(o.get("ask_size")),
                             dec(o.get("last_trade_price")), et_naive_to_epoch(o.get("last_trade_time")), num(o.get("iv")), num(o.get("delta")),
                             num(o.get("gamma")), num(o.get("theta")), num(o.get("vega")), intv(o.get("open_interest")), intv(o.get("volume"))))
    ivs = sorted(c.iv for c in rows if c.iv is not None and c.iv > 0)
    if ivs and ivs[len(ivs) // 2] > 3.0:          # quoted in percent: convert to decimal
        for c in rows:
            if c.iv is not None:
                c.iv = c.iv / 100.0
    return ChainSnapshot(underlying_id, cboe_symbol, under, rows, fetched_at, under.last_trade_time, n_raw)


# ---- Treasury / FRED / Frankfurter / FINRA parsing -----------------------------------------------------------------------------
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def parse_treasury_xml(text, fetched_at: int = 0) -> Optional[ParCurve]:
    """The LATEST ``NEW_DATE`` entry of the par-yield-curve feed as a ``ParCurve`` (tenors in years), or None."""
    try:
        root = ET.fromstring(text if isinstance(text, (bytes, bytearray)) else str(text))
    except ET.ParseError:
        raise FetchError("treasury XML did not parse", retryable=False) from None
    best: Optional[ParCurve] = None
    for el in root.iter():
        if _local(el.tag) != "properties":
            continue
        fields = {_local(ch.tag): (ch.text or "").strip() for ch in el}
        d = _iso_date(fields.get("NEW_DATE"))
        if d is None:
            continue
        pts: Dict[Decimal, Decimal] = {}
        for xml_field, label, _years in TENORS:
            v = fields.get(xml_field)
            if v in (None, ""):
                continue
            try:
                pts[TENOR_YEARS[label]] = Decimal(v).quantize(Decimal("0.0001"))
            except ArithmeticError:
                continue
        if len(pts) >= 6 and (best is None or d > best.as_of):
            best = ParCurve(d, pts, fetched_at=fetched_at)
    return best


def parse_fred_csv(text) -> List[Tuple[date, Decimal]]:
    """``[(date, value)]`` sorted; blank and "." observations skipped (copied from finsim2 ``parse_csv``)."""
    if isinstance(text, (bytes, bytearray)):
        text = text.decode("utf-8", "replace")
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header or len(header) < 2:
        raise FetchError("unexpected FRED CSV header", retryable=False)
    out: Dict[date, Decimal] = {}
    for row in reader:
        if len(row) < 2:
            continue
        d = _iso_date(row[0])
        raw = row[1].strip()
        if d is None or raw in (".", ""):
            continue
        v = dec(raw)
        if v is None:
            continue
        out[d] = v
    return sorted(out.items())


def parse_frankfurter(text, fetched_at: int = 0) -> FxRates:
    try:
        payload = json.loads(text.decode("utf-8", "replace") if isinstance(text, (bytes, bytearray)) else text)
    except ValueError:
        raise FetchError("frankfurter JSON did not parse", retryable=False) from None
    if not isinstance(payload, dict) or str(payload.get("base") or "").upper() != "USD" or not isinstance(payload.get("rates"), dict):
        raise FetchError("unexpected frankfurter payload", retryable=False)
    d = _iso_date(payload.get("date"))
    if d is None:
        raise FetchError("frankfurter payload without a date", retryable=False)
    rates: Dict[str, Decimal] = {}
    for k, v in payload["rates"].items():
        if isinstance(k, str) and len(k) == 3 and k.isalpha():
            r = dec(v)
            if r is not None and r > 0:
                rates[k.upper()] = r
    return FxRates(d, rates, fetched_at=fetched_at)


def parse_finra(text, wanted: Optional[Iterable[str]] = None) -> Dict[str, Tuple[int, int]]:
    """``{symbol: (short_volume, total_volume)}`` from the pipe file (values rounded HALF_EVEN to whole shares; rows
    with total ≤ 0 or short > 1.05 × total are dropped)."""
    if isinstance(text, (bytes, bytearray)):
        text = text.decode("utf-8", "replace")
    want = set(s.upper() for s in wanted) if wanted is not None else None
    out: Dict[str, Tuple[int, int]] = {}
    for line in text.splitlines()[1:]:
        parts = line.split("|")
        if len(parts) < 5:
            continue
        sym = parts[1].strip().upper()
        if not sym or (want is not None and sym not in want):
            continue
        sv, tv = intv(parts[2]), intv(parts[4])
        if sv is None or tv is None or tv <= 0 or sv > tv * 1.05:
            continue
        out[sym] = (min(sv, tv), tv)
    return out


__all__ = ["num", "dec", "intv", "et_naive_to_epoch", "YahooIntraday", "DailyBar", "DivEvent", "SplitEvent", "Events", "ChainUnderlying", "ChainRow",
           "ChainSnapshot", "ParCurve", "FxRates", "chart_result", "chart_bars", "clean_meta", "parse_intraday", "parse_daily", "parse_events", "parse_chain",
           "parse_treasury_xml", "parse_fred_csv", "parse_frankfurter", "parse_finra", "OCC_TAIL", "TENORS", "TENOR_Q", "TENOR_YEARS", "YAHOO_DELAY_S",
           "YAHOO_DELAY_LABEL", "DAILY_DELAY_LABEL"]
