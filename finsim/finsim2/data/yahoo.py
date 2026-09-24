"""Yahoo Finance daily history via the v8 chart endpoint (browser-style UA is acceptable for Yahoo only).

``fetch_history("SPY")`` returns the full daily history (period1=0 .. now); pass ``start_epoch`` for incremental
updates. The payload is untrusted: every field is type-checked and non-finite numbers become None.
"""
from __future__ import annotations

import datetime as _dt
import json
import time
import urllib.parse

from . import BROWSER_UA, FetchError, http_get, num

HOSTS = ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com")
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_TRIES_PER_HOST = 3
BACKOFF = 1.5  # seconds, doubled per retry


class YahooError(FetchError):
    pass


def _get(url: str) -> bytes:
    return http_get(url, {"User-Agent": BROWSER_UA, "Accept": "application/json"}, timeout=30)


def _sleep(seconds: float):
    time.sleep(seconds)


def chart_url(host: str, symbol: str, start_epoch: int, end_epoch: int) -> str:
    sym = urllib.parse.quote(symbol, safe="")
    return (f"{host}/v8/finance/chart/{sym}?period1={int(start_epoch)}&period2={int(end_epoch)}"
            f"&interval=1d&events=div%2Csplit")


def _chart_error(payload) -> str | None:
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


def fetch_chart(symbol: str, start_epoch: int = 0, end_epoch: int | None = None) -> dict:
    """Raw chart JSON (``chart.result[0]``). Tries query1 then query2, retrying 429/5xx with backoff."""
    if not isinstance(symbol, str) or not symbol.strip() or len(symbol) > 40:
        raise YahooError("invalid symbol")
    end_epoch = int(end_epoch if end_epoch is not None else time.time() + 86400)
    last_err = None
    for host in HOSTS:
        delay = BACKOFF
        for attempt in range(MAX_TRIES_PER_HOST):
            try:
                raw = _get(chart_url(host, symbol.strip(), max(0, int(start_epoch)), end_epoch))
            except FetchError as exc:
                last_err = exc
                body = getattr(exc, "body", b"") or b""
                if exc.status == 404 and body:  # unknown symbol: Yahoo answers with a chart.error JSON body
                    try:
                        msg = _chart_error(json.loads(body.decode("utf-8", "replace")))
                    except ValueError:
                        msg = None
                    raise YahooError(f"{symbol}: {msg or 'not found'}", status=404) from None
                if exc.status in RETRY_STATUS or exc.status is None:
                    if attempt < MAX_TRIES_PER_HOST - 1:
                        _sleep(delay)
                        delay *= 2
                    continue
                break  # other 4xx: try the next host
            try:
                payload = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                last_err = YahooError("invalid JSON")
                break
            msg = _chart_error(payload)
            if msg:
                raise YahooError(f"{symbol}: {msg}")
            return payload["chart"]["result"][0]
    status = getattr(last_err, "status", None)
    raise YahooError(f"{symbol}: download failed ({last_err})", status=status)


def _series(block, key, n):
    v = block.get(key) if isinstance(block, dict) else None
    if not isinstance(v, list):
        return [None] * n
    v = v[:n]
    return v + [None] * (n - len(v))


def parse_chart(result: dict) -> tuple[dict, list[dict]]:
    """``(meta, rows)`` from ``chart.result[0]``. Rows are sorted, de-duplicated by date, close never None."""
    if not isinstance(result, dict):
        return {}, []
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    ts = result.get("timestamp")
    if not isinstance(ts, list):
        return meta, []
    n = len(ts)
    ind = result.get("indicators") if isinstance(result.get("indicators"), dict) else {}
    quote = ind.get("quote")
    quote = quote[0] if isinstance(quote, list) and quote and isinstance(quote[0], dict) else {}
    adj = ind.get("adjclose")
    adj = adj[0] if isinstance(adj, list) and adj and isinstance(adj[0], dict) else None
    opens, highs, lows = _series(quote, "open", n), _series(quote, "high", n), _series(quote, "low", n)
    closes, vols = _series(quote, "close", n), _series(quote, "volume", n)
    adjs = _series(adj, "adjclose", n) if adj is not None else [None] * n
    off = meta.get("gmtoffset")
    off = int(off) if isinstance(off, (int, float)) and not isinstance(off, bool) and abs(off) < 86400 else 0
    by_date: dict[str, dict] = {}
    for i in range(n):
        t = ts[i]
        if not isinstance(t, (int, float)) or isinstance(t, bool):
            continue
        close = num(closes[i])
        if close is None:
            continue
        try:
            d = _dt.datetime.fromtimestamp(int(t) + off, _dt.timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            continue
        a = num(adjs[i])
        vol = num(vols[i])
        by_date[d] = {"date": d, "open": num(opens[i]), "high": num(highs[i]), "low": num(lows[i]), "close": close,
                      "adj_close": a if a is not None else close, "volume": vol}
    return meta, [by_date[d] for d in sorted(by_date)]


def fetch_history(symbol: str, start_epoch: int = 0, end_epoch: int | None = None) -> list[dict]:
    """Daily bars ``{date, open, high, low, close, adj_close, volume}`` from ``start_epoch`` to now."""
    return parse_chart(fetch_chart(symbol, start_epoch, end_epoch))[1]


def fetch_history_meta(symbol: str, start_epoch: int = 0) -> tuple[dict, list[dict]]:
    """Like :func:`fetch_history` but also returns the chart ``meta`` block."""
    return parse_chart(fetch_chart(symbol, start_epoch))


def _clean_text(v, limit=120):
    if not isinstance(v, str):
        return None
    s = "".join(ch for ch in v if ch.isprintable()).strip()
    return s[:limit] or None


def fetch_quote_meta(symbol: str) -> dict:
    """Descriptive metadata for a symbol (last few days of chart meta), cleaned of anything unexpected."""
    meta, rows = parse_chart(fetch_chart(symbol, int(time.time()) - 10 * 86400))
    out = {k: _clean_text(meta.get(k)) for k in ("symbol", "currency", "instrumentType", "longName", "shortName",
                                                   "exchangeName", "fullExchangeName", "exchangeTimezoneName")}
    out["regularMarketPrice"] = num(meta.get("regularMarketPrice"))
    out["firstTradeDate"] = meta.get("firstTradeDate") if isinstance(meta.get("firstTradeDate"), int) else None
    out["last_date"] = rows[-1]["date"] if rows else None
    return out


def date_to_epoch(d) -> int:
    """UTC midnight epoch seconds of an ISO date (or date)."""
    if not isinstance(d, _dt.date):
        d = _dt.date.fromisoformat(str(d)[:10])
    return int(_dt.datetime(d.year, d.month, d.day, tzinfo=_dt.timezone.utc).timestamp())


__all__ = ["fetch_history", "fetch_history_meta", "fetch_quote_meta", "fetch_chart", "parse_chart", "chart_url",
           "date_to_epoch", "YahooError"]
