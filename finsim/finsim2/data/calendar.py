"""Event calendar -> point-in-time alt_data datasets ``event_calendar`` and ``earnings_calendar``.

``event_calendar`` (asset ``macro:US``), one row per event day:
    cpi, jobs, gdp, ppi, retail      release days from the FRED release calendar (api.stlouisfed.org, FRED_API_KEY),
                                     including scheduled future dates
    fomc                             FOMC decision day (1; 2 = with a Summary of Economic Projections; 3 = unscheduled
                                     call or notation vote), parsed from the Federal Reserve's calendar pages
Scheduled events are known well in advance (the BLS / BEA calendars for the year and the FOMC calendar are published
before it starts), so their rows are published 30 days before the event; an unscheduled FOMC action is published the
day after the call (the announcement can come the next morning). Announcements (08:30 data, 14:00 FOMC) come before the close, so the day itself is usable at the close.

``earnings_calendar`` — scheduled earnings dates from the Finnhub calendar (FINNHUB_KEY; free tier) with the release
hour (1 = before the open, 2 = after the close, 3 = during the day) and the consensus EPS / revenue estimates at the time
of collection. It is only point in time from the day it is collected (published = collection date), so it builds a
forward history. The past earnings calendar comes from SEC 8-K item 2.02 (``secevents.py``: ``release_hhmm``), and
``expected_next`` gives a point-in-time estimate of the next report date from that history for backtests.

Keys come from the environment only and never appear in errors or logs. Pages are untrusted and parsed with regular
expressions over fixed markup; nothing is evaluated.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import time
from typing import Dict, List, Optional, Tuple

from . import GOV_UA, FetchError, http_get, num

EVENTS = "event_calendar"
EARNINGS = "earnings_calendar"
FRED_RELEASES = {"cpi": 10, "jobs": 50, "gdp": 53, "ppi": 46, "retail": 9}
FRED_URL = "https://api.stlouisfed.org/fred/release/dates?release_id={rid}&api_key={key}&file_type=json&include_release_dates_with_no_data=true&sort_order=asc&limit=10000"
FOMC_CURRENT = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FOMC_HISTORICAL = "https://www.federalreserve.gov/monetarypolicy/fomchistorical{y}.htm"
FINNHUB_URL = "https://finnhub.io/api/v1/calendar/earnings?from={a}&to={b}&token={key}"
SCHEDULED_LEAD = 30
FOMC_FIRST_YEAR = 2000
_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august", "september",
                                        "october", "november", "december"], 1)}
_ABBR = {m[:3]: i for m, i in _MONTHS.items()}


def _pub(d: str, lead: int) -> str:
    return (_dt.date.fromisoformat(d) - _dt.timedelta(days=lead)).isoformat()


# ------------------------------------------------------------------ FRED release calendar
def parse_fred_dates(payload) -> List[str]:
    out = []
    for r in (payload.get("release_dates") if isinstance(payload, dict) else None) or []:
        d = str((r or {}).get("date") or "")[:10] if isinstance(r, dict) else ""
        try:
            _dt.date.fromisoformat(d)
            out.append(d)
        except ValueError:
            continue
    return out


def macro_rows(store=None, key: Optional[str] = None) -> Tuple[List[tuple], List[str]]:
    key = key or os.environ.get("FRED_API_KEY")
    if not key:
        return [], ["FRED release calendar skipped: set FRED_API_KEY"]
    rows, errs = [], []
    for name, rid in FRED_RELEASES.items():
        try:
            body = http_get(FRED_URL.format(rid=rid, key=key), {"User-Agent": GOV_UA}, timeout=60)
            dates = parse_fred_dates(json.loads(body.decode("utf-8", "replace")))
        except (FetchError, ValueError) as e:
            errs.append(f"FRED {name}: {e}")
            continue
        rows += [("macro:US", d, name, 1.0, _pub(d, SCHEDULED_LEAD)) for d in dates]
        time.sleep(0.3)
    return rows, errs


# ------------------------------------------------------------------ FOMC
def _day(y: int, m: int, d: int) -> Optional[str]:
    try:
        return _dt.date(y, m, d).isoformat()
    except ValueError:
        return None


def _month_num(s: str) -> Optional[int]:
    s = s.strip().lower()
    return _MONTHS.get(s) or _ABBR.get(s[:3])


def _last_day(y: int, month_txt: str, days_txt: str) -> Optional[str]:
    """'Apr/May' + '30-1' -> the decision day (the last day, in the second month when the range wraps)."""
    months = [m for m in month_txt.split("/") if m.strip()]
    nums = [int(x) for x in re.findall(r"\d+", days_txt)]
    if not months or not nums:
        return None
    wraps = len(months) > 1 and len(nums) > 1 and nums[-1] < nums[0]
    m = _month_num(months[-1] if wraps else months[0])
    return _day(y, m, nums[-1]) if m else None


def parse_fomc_current(html: str) -> Dict[str, float]:
    """The current calendar page: sections '<YYYY> FOMC Meetings' with month / date cells ('27-28', '16-17*', '22 (notation
    vote)'). '*' = with projections (2), 'notation vote' / 'unscheduled' = 3."""
    out: Dict[str, float] = {}
    heads = [(m.start(), int(m.group(1))) for m in re.finditer(r"(\d{4}) FOMC Meetings", html)]
    for k, (pos, year) in enumerate(heads):
        end = heads[k + 1][0] if k + 1 < len(heads) else len(html)
        sec = html[pos:end]
        months = re.findall(r'fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>', sec)
        dates = re.findall(r'fomc-meeting__date[^>]*>([^<]+)<', sec)
        for mt, dt_ in zip(months, dates):
            d = _last_day(year, mt, dt_)
            if not d:
                continue
            low = dt_.lower()
            if "cancel" in low:
                continue
            kind = 3.0 if ("notation" in low or "unscheduled" in low or "call" in low) else (2.0 if "*" in dt_ else 1.0)
            out[d] = max(out.get(d, 0.0), kind)
    return out


def parse_fomc_historical(html: str, year: int) -> Dict[str, float]:
    """Historical year pages: headings like 'January 27-28 Meeting - 2015', 'March 15 (unscheduled) Meeting - 2020',
    'March 19 (notation vote) - 2020', 'October 7 Conference Call - 2008'; cancelled meetings are skipped."""
    out: Dict[str, float] = {}
    pat = r"<h5[^>]*>\s*([A-Za-z/]+)\s+([\d\-–]+)\s*(\([^)]*\))?\s*(Meeting|Conference Call|Unscheduled Meeting)?\s*-\s*(\d{4})\s*</h5>"
    for m in re.finditer(pat, html):
        mt, days, qual, kind, y = m.group(1), m.group(2).replace("–", "-"), (m.group(3) or "").lower(), m.group(4) or "", int(m.group(5))
        if y != year or "cancel" in qual:
            continue
        d = _last_day(year, mt, days)
        if d:
            unscheduled = bool(qual) or kind != "Meeting"
            out[d] = max(out.get(d, 0.0), 3.0 if unscheduled else 1.0)
    return out


def fomc_rows(until_year: Optional[int] = None) -> Tuple[List[tuple], List[str]]:
    until_year = until_year or _dt.date.today().year
    days: Dict[str, float] = {}
    errs = []
    try:
        cur = parse_fomc_current(http_get(FOMC_CURRENT, {"User-Agent": GOV_UA}, timeout=60).decode("utf-8", "replace"))
        days.update(cur)
        first_current = min(int(d[:4]) for d in cur) if cur else until_year + 1
    except FetchError as e:
        errs.append(f"FOMC calendar: {e}")
        first_current = until_year + 1
    for y in range(FOMC_FIRST_YEAR, first_current):
        try:
            days.update(parse_fomc_historical(http_get(FOMC_HISTORICAL.format(y=y), {"User-Agent": GOV_UA}, timeout=60).decode("utf-8", "replace"), y))
        except FetchError as e:
            errs.append(f"FOMC {y}: {e}")
        time.sleep(0.3)
    # an unscheduled action is public only from its announcement, which can come the morning after the call
    # (e.g. the 2 March 2020 call, announced 3 March): published the next day
    rows = [("macro:US", d, "fomc", k, _pub(d, -1) if k == 3.0 else _pub(d, SCHEDULED_LEAD)) for d, k in days.items()]
    return rows, errs


# ------------------------------------------------------------------ earnings (Finnhub forward calendar)
HOUR = {"bmo": 1.0, "amc": 2.0, "dmh": 3.0}


def parse_finnhub(payload, wanted: Dict[str, str], collected: str) -> List[tuple]:
    out = []
    for r in (payload.get("earningsCalendar") if isinstance(payload, dict) else None) or []:
        if not isinstance(r, dict):
            continue
        a = wanted.get(str(r.get("symbol") or "").upper())
        d = str(r.get("date") or "")[:10]
        try:
            _dt.date.fromisoformat(d)
        except ValueError:
            continue
        if not a or d < collected:
            continue
        out.append((a, d, "scheduled", 1.0, collected))
        h = HOUR.get(str(r.get("hour") or "").lower())
        if h:
            out.append((a, d, "hour", h, collected))
        for src, fld in (("epsEstimate", "eps_estimate"), ("revenueEstimate", "revenue_estimate")):
            v = num(r.get(src))
            if v is not None:
                out.append((a, d, fld, v, collected))
    return out


def earnings_rows(store, key: Optional[str] = None, days_ahead: int = 90) -> Tuple[List[tuple], List[str]]:
    key = key or os.environ.get("FINNHUB_KEY")
    if not key:
        return [], ["earnings calendar skipped: set FINNHUB_KEY (free: https://finnhub.io/register)"]
    wanted = {a["id"].replace("-", ".").upper(): a["id"] for a in store.assets("EQUITY")}
    today = _dt.date.today()
    rows, errs = [], []
    a = today
    while a < today + _dt.timedelta(days=days_ahead):
        b = min(a + _dt.timedelta(days=30), today + _dt.timedelta(days=days_ahead))
        try:
            body = http_get(FINNHUB_URL.format(a=a.isoformat(), b=b.isoformat(), key=key), {"Accept": "application/json"}, timeout=60)
            rows += parse_finnhub(json.loads(body.decode("utf-8", "replace")), wanted, today.isoformat())
        except (FetchError, ValueError) as e:
            errs.append(f"Finnhub calendar {a}: {e}")
        a = b + _dt.timedelta(days=1)
        time.sleep(1.1)
    return rows, errs


# ------------------------------------------------------------------ point-in-time helpers for research
def earnings_history(store, asset: str) -> List[Tuple[str, Optional[float], str]]:
    """Past earnings releases from SEC 8-K item 2.02: [(New York date, release hhmm, published)]."""
    from .secevents import EVENTS as SEC8K
    return sorted({(d, v, p) for _, d, f, v, p in store.alt(SEC8K, asset) if f == "release_hhmm"})


def expected_next(history: List[str], asof: str) -> Optional[str]:
    """Point-in-time estimate of the next report date: the report one year before the next one due (companies keep
    their quarterly rhythm), i.e. the earliest past release + 364 days that is still after `asof`; else last + 91."""
    past = sorted(d for d in history if d <= asof)
    if not past:
        return None
    a = _dt.date.fromisoformat(asof)
    cands = [_dt.date.fromisoformat(d) + _dt.timedelta(days=364) for d in past[-5:]]
    cands = [c for c in cands if c > a]
    if cands:
        return min(cands).isoformat()
    return (_dt.date.fromisoformat(past[-1]) + _dt.timedelta(days=91)).isoformat()


def refresh(store, progress=None) -> dict:
    say = progress or (lambda m: None)
    out = {}
    rows, e1 = macro_rows(store)
    if rows:
        store.put_alt(EVENTS, rows)
    say(f"macro release calendar: {len(rows)} rows")
    frows, e2 = fomc_rows()
    if frows:
        store.put_alt(EVENTS, frows)
    say(f"FOMC calendar: {len(frows)} decision days")
    erows, e3 = earnings_rows(store)
    if erows:
        store.put_alt(EARNINGS, erows)
    say(f"earnings calendar: {len(erows)} rows")
    out.update({"macro_rows": len(rows), "fomc_days": len(frows), "earnings_rows": len(erows), "errors": e1 + e2 + e3})
    return out
