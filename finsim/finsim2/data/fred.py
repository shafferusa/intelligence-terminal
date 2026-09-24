"""FRED macro series.

Primary source: ``fredgraph.csv`` (no key). Fallback, only when the CSV fails and ``FRED_API_KEY`` is set in the
environment: the FRED API (JSON). The key is read from the environment at call time, never stored, logged or put
in an error message. Both are .gov-style public-data endpoints and get the LoganTerminal User-Agent.
"""
from __future__ import annotations

import csv
import datetime as _dt
import io
import json
import os
import urllib.parse

from . import GOV_UA, FetchError, http_get, num
from .universe import FRED_SERIES

CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
API_URL = "https://api.stlouisfed.org/fred/series/observations"


class FredError(FetchError):
    pass


def _get(url: str) -> bytes:
    return http_get(url, {"User-Agent": GOV_UA}, timeout=60)


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


def fetch_series(series_id: str, start=None) -> list[tuple[str, float]]:
    """Observations ``[(date ISO, value)]`` sorted by date; ``start`` (date/ISO) limits to dates >= start."""
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
    """The date an observation is assumed public: observation date + the series' publication lag (calendar days)."""
    if not isinstance(obs_date, _dt.date):
        obs_date = _dt.date.fromisoformat(str(obs_date)[:10])
    elif isinstance(obs_date, _dt.datetime):
        obs_date = obs_date.date()
    return obs_date + _dt.timedelta(days=lag_days(series_id))


__all__ = ["fetch_series", "available_on", "lag_days", "parse_csv", "parse_api_json", "FredError"]
