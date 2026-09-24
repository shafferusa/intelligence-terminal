"""FRED macro series.

Two kinds of history:

* **Latest vintage** (:func:`fetch_series`): ``fredgraph.csv`` (no key), falling back to the FRED API when the CSV
  fails and ``FRED_API_KEY`` is set. Every observation carries its *current, revised* value, so used as history it
  leaks later revisions backward in time; the publication date is only a fixed guess (``lag_days``).
* **First release** (:func:`fetch_first_release`, needs ``FRED_API_KEY``): the ALFRED vintages via the API with
  ``output_type=4`` - each observation's value as first published, with the date it was first published
  (``realtime_start``). This is point-in-time: a value is known from its publication date and never changes.
  Vintage coverage starts in the 1990s for many series (later for some); for observations older than that FRED
  returns the earliest vintage it has, published on the first vintage date (see ``engine/align.py``).

The key is read from the environment at call time, never stored, logged or put in an error message (errors are
raised ``from None`` and name only the series and the HTTP status). Both endpoints are .gov-style public data and
get the LoganTerminal User-Agent.
"""
from __future__ import annotations

import csv
import datetime as _dt
import io
import json
import os
import time
import urllib.parse

from . import GOV_UA, FetchError, http_get, num
from .universe import FRED_SERIES

CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
API_URL = "https://api.stlouisfed.org/fred/series/observations"
VINTAGES_URL = "https://api.stlouisfed.org/fred/series/vintagedates"


REALTIME_START = "1776-07-04"   # FRED's "all vintages" bounds
REALTIME_END = "9999-12-31"
RETRY_STATUSES = (429, 500, 502, 503, 504)
RETRIES = 3                      # attempts after the first one, for 429 / 5xx / network errors
RETRY_BASE_SECONDS = 2.0
CHUNK_YEARS = 5                  # observation window per request when a whole-series request times out


class FredError(FetchError):
    pass


def _get(url: str) -> bytes:
    return http_get(url, {"User-Agent": GOV_UA}, timeout=60)


def _sleep(seconds: float):
    if seconds > 0:
        time.sleep(seconds)


def _get_retry(url: str) -> bytes:
    """``_get`` with a small exponential back-off on 429, 5xx and network errors (status None)."""
    for attempt in range(RETRIES + 1):
        try:
            return _get(url)
        except FetchError as exc:
            if attempt >= RETRIES or not (exc.status is None or exc.status in RETRY_STATUSES):
                raise
        _sleep(RETRY_BASE_SECONDS * (2 ** attempt))
    raise FredError("unreachable")  # pragma: no cover


def has_api_key() -> bool:
    """True when ``FRED_API_KEY`` is set (the value itself is never returned)."""
    return bool(os.environ.get("FRED_API_KEY"))


def _valid_id(series_id: str) -> str:
    if not isinstance(series_id, str) or not series_id.isascii() or not series_id.replace("_", "").isalnum() \
            or len(series_id) > 40:
        raise FredError("invalid FRED series id")
    return series_id


def _date(s) -> str | None:
    try:
        return _dt.date.fromisoformat(str(s).strip()[:10]).isoformat()
    except ValueError:
        return None


def parse_csv(text: str) -> list[tuple[str, float]]:
    """``[(date, value)]`` from fredgraph.csv; "." (missing) and malformed lines are skipped."""
    out = {}
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header or len(header) < 2:
        raise FredError("unexpected CSV header")
    for row in reader:
        if len(row) < 2:
            continue
        d = _date(row[0])
        v = num(row[1]) if row[1].strip() not in (".", "") else None
        if d is None or v is None:
            continue
        out[d] = v
    return sorted(out.items())


def parse_api_json(text: str) -> list[tuple[str, float]]:
    payload = json.loads(text)
    obs = payload.get("observations") if isinstance(payload, dict) else None
    if not isinstance(obs, list):
        raise FredError("unexpected API payload")
    out = {}
    for o in obs:
        if not isinstance(o, dict):
            continue
        d = _date(o.get("date"))
        raw = o.get("value")
        v = None if raw in (".", "", None) else num(raw)
        if d is None or v is None:
            continue
        out[d] = v
    return sorted(out.items())


def parse_first_release_json(text: str) -> list[tuple[str, float, str]]:
    """``[(obs date, first-release value, published date)]`` from an ``output_type=4`` API payload.

    ``date`` is the observation period, ``value`` its first release, ``realtime_start`` the day it was first
    published. "." (missing) values and rows without a valid publication date are skipped; if a date appears twice
    the earliest publication wins.
    """
    payload = json.loads(text)
    obs = payload.get("observations") if isinstance(payload, dict) else None
    if not isinstance(obs, list):
        raise FredError("unexpected API payload")
    out: dict[str, tuple[float, str]] = {}
    for o in obs:
        if not isinstance(o, dict):
            continue
        d = _date(o.get("date"))
        pub = _date(o.get("realtime_start"))
        raw = o.get("value")
        v = None if raw in (".", "", None) else num(raw)
        if d is None or v is None or pub is None:
            continue
        if d not in out or pub < out[d][1]:
            out[d] = (v, pub)
    return [(d, v, p) for d, (v, p) in sorted(out.items())]


def _api_key(sid: str) -> str:
    key = os.environ.get("FRED_API_KEY")
    if not key:
        raise FredError(f"{sid}: first-release vintages need FRED_API_KEY")
    return key


def _first_release_window(sid: str, key: str, obs_start=None, obs_end=None, retry: bool = True):
    q = {"series_id": sid, "api_key": key, "file_type": "json", "output_type": "4",
         "realtime_start": REALTIME_START, "realtime_end": REALTIME_END}
    if obs_start:
        q["observation_start"] = obs_start
    if obs_end:
        q["observation_end"] = obs_end
    url = API_URL + "?" + urllib.parse.urlencode(q)
    body = _get_retry(url) if retry else _get(url)
    try:
        return parse_first_release_json(body.decode("utf-8", "replace"))
    except (ValueError, AttributeError):
        raise FredError(f"{sid}: API returned malformed first-release data") from None


def first_vintage_date(series_id: str) -> str:
    """The date of the series' first ALFRED vintage (``fred/series/vintagedates``, first entry)."""
    sid = _valid_id(series_id)
    q = {"series_id": sid, "api_key": _api_key(sid), "file_type": "json", "limit": "1", "sort_order": "asc"}
    try:
        payload = json.loads(_get_retry(VINTAGES_URL + "?" + urllib.parse.urlencode(q)).decode("utf-8", "replace"))
    except FetchError as exc:
        raise FredError(f"{sid}: vintage dates download failed ({exc})", status=exc.status) from None
    except (ValueError, AttributeError):
        raise FredError(f"{sid}: API returned malformed vintage dates") from None
    vds = payload.get("vintage_dates") if isinstance(payload, dict) else None
    first = _date(vds[0]) if isinstance(vds, list) and vds else None
    if first is None:
        raise FredError(f"{sid}: no vintage dates")
    return first


def _first_release_chunks(sid: str, key: str, start_s, today: _dt.date) -> list[tuple[str, float, str]]:
    if start_s:
        lo = _dt.date.fromisoformat(start_s)
    else:
        lo = _dt.date.fromisoformat(first_vintage_date(sid)) - _dt.timedelta(days=366)
    out: dict[str, tuple[float, str]] = {}
    while lo <= today:
        hi = min(today, _dt.date(lo.year + CHUNK_YEARS, lo.month, 1) - _dt.timedelta(days=1))
        for d, v, p in _first_release_window(sid, key, lo.isoformat(), hi.isoformat()):
            if d not in out or p < out[d][1]:
                out[d] = (v, p)
        lo = hi + _dt.timedelta(days=1)
    return [(d, v, p) for d, (v, p) in sorted(out.items())]


def fetch_first_release(series_id: str, start=None, today=None) -> list[tuple[str, float, str]]:
    """First-release observations ``[(date, value, published)]`` sorted by date, via the FRED API (ALFRED vintages).

    Needs ``FRED_API_KEY`` in the environment (raises :class:`FredError` without it). ``start`` limits to observation
    dates >= start. A 429 is retried with back-off, as is every windowed request on 429 / 5xx. First releases never
    change, so incremental fetches are safe.

    Observations older than the series' vintage coverage are not returned by FRED, so first-release history starts
    at the first vintage (e.g. CPI 1972, M2 1980, NFCI and the Fed balance sheet 2011). A long request that the API
    cannot answer in time (NFCI: a gateway timeout) is split into ``CHUNK_YEARS`` observation windows from one year
    before the first vintage date.
    """
    sid = _valid_id(series_id)
    key = _api_key(sid)
    start_s = _date(start) if start is not None else None
    try:
        try:
            rows = _first_release_window(sid, key, start_s, retry=False)
        except FredError:
            raise
        except FetchError as exc:
            if exc.status == 429:                             # rate limited: back off and retry
                rows = _first_release_window(sid, key, start_s)
            elif exc.status is None or exc.status >= 500:     # timeout / gateway timeout: smaller requests
                rows = _first_release_chunks(sid, key, start_s, today or _dt.date.today())
            else:
                raise
    except FredError:
        raise
    except FetchError as exc:  # message holds only the status, never the URL / key
        raise FredError(f"{sid}: first-release download failed ({exc})", status=exc.status) from None
    if start_s:
        rows = [r for r in rows if r[0] >= start_s]
    return rows


def fetch_series(series_id: str, start=None) -> list[tuple[str, float]]:
    """Latest-vintage observations ``[(date ISO, value)]`` sorted by date; ``start`` limits to dates >= start.

    These are today's revised values; for revised (non-daily) series prefer :func:`fetch_first_release`."""
    sid = _valid_id(series_id)
    start_s = _date(start) if start is not None else None
    params = {"id": sid}
    if start_s:
        params["cosd"] = start_s
    try:
        rows = parse_csv(_get(CSV_URL + "?" + urllib.parse.urlencode(params)).decode("utf-8", "replace"))
    except (FetchError, ValueError, UnicodeError) as exc:
        key = os.environ.get("FRED_API_KEY")
        if not key:
            raise FredError(f"{sid}: CSV download failed ({exc})") from None
        q = {"series_id": sid, "api_key": key, "file_type": "json"}
        if start_s:
            q["observation_start"] = start_s
        try:
            rows = parse_api_json(_get(API_URL + "?" + urllib.parse.urlencode(q)).decode("utf-8", "replace"))
        except FetchError as exc2:  # message holds only the status, never the URL / key
            raise FredError(f"{sid}: CSV and API downloads failed ({exc2})") from None
        except ValueError:
            raise FredError(f"{sid}: API returned malformed data") from None
    if start_s:
        rows = [(d, v) for d, v in rows if d >= start_s]
    return rows


def lag_days(series_id: str) -> int:
    info = FRED_SERIES.get(series_id)
    return int(info["lag_days"]) if info else 1


def available_on(series_id: str, obs_date) -> _dt.date:
    """The date an observation is assumed public: observation date + the series' publication lag (calendar days).

    Only a fallback: first-release rows carry their real publication date."""
    if not isinstance(obs_date, _dt.date):
        obs_date = _dt.date.fromisoformat(str(obs_date)[:10])
    elif isinstance(obs_date, _dt.datetime):
        obs_date = obs_date.date()
    return obs_date + _dt.timedelta(days=lag_days(series_id))


__all__ = ["fetch_series", "fetch_first_release", "has_api_key", "available_on", "lag_days", "parse_csv",
           "parse_api_json", "parse_first_release_json", "first_vintage_date", "FredError"]
