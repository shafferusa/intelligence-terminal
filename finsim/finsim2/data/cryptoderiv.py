"""Crypto derivatives -> point-in-time alt_data dataset ``crypto_deriv`` (BTC, ETH, SOL).

Fields per asset and UTC day:
    funding_8h      mean perpetual funding rate over the day, expressed per 8 hours (Deribit, else Binance)
    funding_src     1 = Deribit, 2 = Binance (which provider the day came from)
    premium         perpetual premium over the index at the day's close (Binance premium-index klines)
    cme_basis       (CME front-month future − spot) / spot at the day's close (Yahoo BTC=F / ETH=F vs the spot series)
CME bitcoin futures open interest and positioning come weekly from CFTC (``cftc.py``, contract 133741).

Providers, in order, per field. Binance (fapi.binance.com) and Bybit refuse US connections, so from a US machine the
funding history comes from Deribit (www.deribit.com public API, BTC and ETH, no key) and the basis from CME futures
on Yahoo; Binance fills in wherever it answers (and is the only source for SOL and for the premium index).

Point in time: a UTC day ends at 19:00 / 20:00 New York, after the US close, so a UTC day's values are published the
next day. Downloaded content is untrusted and parsed with json.
"""
from __future__ import annotations

import datetime as _dt
import json
import time
from typing import Dict, List, Optional, Tuple

from . import BROWSER_UA, FetchError, http_get, num

DATASET = "crypto_deriv"
DERIBIT = "https://www.deribit.com/api/v2/public/get_funding_rate_history?instrument_name={inst}&start_timestamp={a}&end_timestamp={b}"
BINANCE_FUNDING = "https://fapi.binance.com/fapi/v1/fundingRate?symbol={sym}&startTime={a}&limit=1000"
BINANCE_PREMIUM = "https://fapi.binance.com/fapi/v1/premiumIndexKlines?symbol={sym}&interval=1d&startTime={a}&limit=1500"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1={a}&period2={b}&interval=1d"
ASSETS = {"BTC": {"deribit": "BTC-PERPETUAL", "binance": "BTCUSDT", "cme": "BTC=F"},
          "ETH": {"deribit": "ETH-PERPETUAL", "binance": "ETHUSDT", "cme": "ETH=F"},
          "SOL": {"deribit": None, "binance": "SOLUSDT", "cme": None}}
START = "2019-01-01"
MIN_INTERVAL = 0.25
DAY_MS = 86400000


def _ms(d: str) -> int:
    return int(_dt.datetime.fromisoformat(d).replace(tzinfo=_dt.timezone.utc).timestamp() * 1000)


def _day(ms) -> Optional[str]:
    v = num(ms)
    if v is None:
        return None
    return _dt.datetime.fromtimestamp(v / 1000, _dt.timezone.utc).date().isoformat()


def _next(d: str) -> str:
    return (_dt.date.fromisoformat(d) + _dt.timedelta(days=1)).isoformat()


def _json(url: str, ua: Optional[str] = None):
    return json.loads(http_get(url, {"Accept": "application/json", **({"User-Agent": ua} if ua else {})}, timeout=45).decode("utf-8", "replace"))


# ------------------------------------------------------------------ parsers (pure)
def parse_deribit(payload) -> Dict[str, Tuple[float, int]]:
    """{UTC day: (sum of 8h-equivalent rates, count)} from Deribit's hourly funding history."""
    out: Dict[str, List[float]] = {}
    res = payload.get("result") if isinstance(payload, dict) else None
    for r in res or []:
        if not isinstance(r, dict):
            continue
        d, v = _day(r.get("timestamp")), num(r.get("interest_8h"))
        if d and v is not None and abs(v) < 0.1:
            out.setdefault(d, []).append(v)
    return {d: (sum(v), len(v)) for d, v in out.items()}


def parse_binance_funding(payload) -> Dict[str, Tuple[float, int]]:
    """Binance funds every 8 hours: each print is already an 8h rate."""
    out: Dict[str, List[float]] = {}
    for r in payload if isinstance(payload, list) else []:
        if not isinstance(r, dict):
            continue
        d, v = _day(r.get("fundingTime")), num(r.get("fundingRate"))
        if d and v is not None and abs(v) < 0.1:
            out.setdefault(d, []).append(v)
    return {d: (sum(v), len(v)) for d, v in out.items()}


def parse_binance_premium(payload) -> Dict[str, float]:
    """Daily klines [open time, open, high, low, close, ...] of the premium index -> {day: close}."""
    out = {}
    for k in payload if isinstance(payload, list) else []:
        if isinstance(k, list) and len(k) >= 5:
            d, v = _day(k[0]), num(k[4])
            if d and v is not None and abs(v) < 1:
                out[d] = v
    return out


def parse_yahoo_close(payload) -> Dict[str, float]:
    try:
        r = payload["chart"]["result"][0]
        ts, cl = r["timestamp"], r["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError):
        return {}
    out = {}
    for t, c in zip(ts or [], cl or []):
        v = num(c)
        if v is not None and v > 0:
            out[_dt.datetime.fromtimestamp(int(t), _dt.timezone.utc).date().isoformat()] = v
    return out


# ------------------------------------------------------------------ fetchers
def _deribit(inst: str, start: str, end: str) -> Dict[str, Tuple[float, int]]:
    out: Dict[str, Tuple[float, int]] = {}
    a, b = _ms(start), _ms(end)
    while a < b:
        stop = min(b, a + 30 * DAY_MS)
        out.update(parse_deribit(_json(DERIBIT.format(inst=inst, a=a, b=stop))))
        a = stop
        time.sleep(MIN_INTERVAL)
    return out


def _binance_funding(sym: str, start: str) -> Dict[str, Tuple[float, int]]:
    out: Dict[str, Tuple[float, int]] = {}
    a = _ms(start)
    while True:
        page = _json(BINANCE_FUNDING.format(sym=sym, a=a))
        if not isinstance(page, list) or not page:
            break
        for d, (s, n) in parse_binance_funding(page).items():
            s0, n0 = out.get(d, (0.0, 0))
            out[d] = (s0 + s, n0 + n)
        last = num(page[-1].get("fundingTime")) if isinstance(page[-1], dict) else None
        if last is None or len(page) < 1000:
            break
        a = int(last) + 1
        time.sleep(MIN_INTERVAL)
    return out


def refresh(store, progress=None, start: str = START) -> dict:
    say = progress or (lambda m: None)
    today = _dt.date.today().isoformat()
    n, notes = 0, []
    for asset, src in ASSETS.items():
        rows: List[tuple] = []
        funding, which = {}, 0
        if src["deribit"]:
            try:
                funding, which = _deribit(src["deribit"], start, today), 1
            except (FetchError, ValueError) as e:
                notes.append(f"{asset} Deribit: {e}")
        if not funding and src["binance"]:
            try:
                funding, which = _binance_funding(src["binance"], start), 2
            except (FetchError, ValueError) as e:
                notes.append(f"{asset} Binance funding: {e} (Binance refuses US connections)")
        for d, (s, k) in funding.items():
            rows += [(asset, d, "funding_8h", s / k, _next(d)), (asset, d, "funding_src", float(which), _next(d))]
        if src["binance"]:
            try:
                prem = parse_binance_premium(_json(BINANCE_PREMIUM.format(sym=src["binance"], a=_ms(start))))
                rows += [(asset, d, "premium", v, _next(d)) for d, v in prem.items()]
            except (FetchError, ValueError) as e:
                notes.append(f"{asset} Binance premium: {e}")
        if src["cme"]:
            try:
                a, b = _ms(start) // 1000, _ms(today) // 1000 + 86400
                fut = parse_yahoo_close(_json(YAHOO.format(sym=src["cme"], a=a, b=b), BROWSER_UA))
                spot = {r["date"]: num(r.get("close")) for r in store.prices(asset) if num(r.get("close"))}
                rows += [(asset, d, "cme_basis", f / spot[d] - 1, _next(d)) for d, f in fut.items() if d in spot and abs(f / spot[d] - 1) < 0.5]
            except (FetchError, ValueError) as e:
                notes.append(f"{asset} CME basis: {e}")
        if rows:
            store.put_alt(DATASET, rows)
        n += len(rows)
        say(f"crypto derivatives {asset}: {len(rows)} rows")
    return {"rows": n, "notes": notes}
