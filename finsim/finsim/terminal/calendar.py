"""TradingCalendar: the NYSE holiday file plus the rule fallback (TERMINAL_DESIGN.md §2.4, §9).

Conventions
- The file ``<repo>/data/nyse-holidays.json`` (``years[YYYY].holidays[].date`` and ``early_closes[].date``) is the
  source for the years it covers; a year missing from the file uses ``finsim.calendar.us_equity_holidays`` (the
  rule) and is listed in ``source()["fallback_years"]`` (shown in ``/api/health.calendar``).
- A business day is Mon-Fri and not a full-closure holiday.  Early closes are 13:00 ET (the file's
  ``early_close_time_et``); other groups' early-close times are derived by ``sessions.py``.
- CME holidays are approximated from this NYSE calendar (sentence CME_CAL): ``full_closure(d)`` names the days
  the futures groups are closed all day (Good Friday, Christmas, New Year's Day); on other NYSE holidays they
  close early.
- Settlement dates are ``add_business_days(trade_date, n)``; month_end is the last business day of the month.
"""
from __future__ import annotations

import json
import os
from datetime import date, time, timedelta
from typing import Dict, List, Optional, Set

from finsim.calendar import us_equity_holidays


def _easter(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _observed(d: date) -> date:
    if d.weekday() == 6:
        return d + timedelta(days=1)
    if d.weekday() == 5:
        return d - timedelta(days=1)
    return d


def _thanksgiving(year: int) -> date:
    d = date(year, 11, 1)
    return d + timedelta(days=(3 - d.weekday()) % 7 + 21)


class TradingCalendar:
    def __init__(self, holidays_path: Optional[str], fallback=us_equity_holidays):
        self._path = holidays_path
        self._fallback = fallback
        self._file_years: Dict[int, dict] = {}
        self._holidays: Dict[int, Set[date]] = {}
        self._early: Dict[int, Dict[date, time]] = {}
        self._full_closure: Dict[int, Set[date]] = {}
        self._fallback_years: Set[int] = set()
        self._early_time = time(13, 0)
        if holidays_path and os.path.exists(holidays_path):
            with open(holidays_path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            hh, mm = (raw.get("early_close_time_et") or "13:00").split(":")[:2]
            self._early_time = time(int(hh), int(mm))
            for y, spec in (raw.get("years") or {}).items():
                try:
                    self._file_years[int(y)] = spec
                except (TypeError, ValueError):
                    continue

    # ---- loading per year ---------------------------------------------------------------------------------
    def _load(self, year: int) -> None:
        if year in self._holidays:
            return
        spec = self._file_years.get(year)
        hols: Set[date] = set()
        early: Dict[date, time] = {}
        full: Set[date] = set()
        if spec is not None:
            for h in spec.get("holidays") or []:
                try:
                    d = date.fromisoformat(h["date"])
                except (KeyError, TypeError, ValueError):
                    continue
                hols.add(d)
                name = str(h.get("name") or "").lower()
                # the file names them "Good Friday", "Christmas Day", "Christmas Day (observed)", "New Year's Day";
                # "christmas day" (not "christmas") so an early-close-style "Christmas Eve" row could never count
                if name.startswith("good friday") or name.startswith("christmas day") or name.startswith("new year"):
                    full.add(d)
            for e in spec.get("early_closes") or []:
                try:
                    d = date.fromisoformat(e["date"])
                except (KeyError, TypeError, ValueError):
                    continue
                t = self._early_time
                ce = e.get("close_et") or e.get("close_time_et")
                if ce:
                    hh, mm = str(ce).split(":")[:2]
                    t = time(int(hh), int(mm))
                early[d] = t
        else:
            self._fallback_years.add(year)
            hols = set(self._fallback(year))
            full = {_easter(year) - timedelta(days=2), _observed(date(year, 12, 25)), _observed(date(year, 1, 1))} & hols
            # NYSE early-close rule fallback: the day after Thanksgiving; 24 Dec when Mon-Thu; 3 Jul when both 3 and 4 Jul
            # are weekdays.
            early[_thanksgiving(year) + timedelta(days=1)] = self._early_time
            if date(year, 12, 24).weekday() <= 3:
                early[date(year, 12, 24)] = self._early_time
            if date(year, 7, 3).weekday() < 5 and date(year, 7, 4).weekday() < 5:
                early[date(year, 7, 3)] = self._early_time
            early = {d: t for d, t in early.items() if d not in hols}
        self._holidays[year] = hols
        self._early[year] = early
        self._full_closure[year] = full

    # ---- queries --------------------------------------------------------------------------------------------
    def is_holiday(self, d: date) -> bool:
        self._load(d.year)
        return d in self._holidays[d.year]

    def is_business_day(self, d: date) -> bool:
        return d.weekday() < 5 and not self.is_holiday(d)

    def early_close(self, d: date) -> Optional[time]:
        """13:00 ET on the file's early-close dates (None otherwise)."""
        self._load(d.year)
        return self._early[d.year].get(d)

    def full_closure(self, d: date) -> bool:
        """Good Friday, Christmas Day and New Year's Day: the futures groups are closed all day (CME_CAL approximation)."""
        self._load(d.year)
        return d in self._full_closure[d.year]

    def holidays(self, year: int) -> List[date]:
        self._load(year)
        return sorted(self._holidays[year])

    def early_closes(self, year: int) -> Dict[date, time]:
        self._load(year)
        return dict(sorted(self._early[year].items()))

    def next_business_day(self, d: date) -> date:
        n = d + timedelta(days=1)
        while not self.is_business_day(n):
            n += timedelta(days=1)
        return n

    def prev_business_day(self, d: date) -> date:
        n = d - timedelta(days=1)
        while not self.is_business_day(n):
            n -= timedelta(days=1)
        return n

    def add_business_days(self, d: date, n: int) -> date:
        cur = d
        for _ in range(abs(int(n))):
            cur = self.next_business_day(cur) if n > 0 else self.prev_business_day(cur)
        return cur

    def roll_following(self, d: date) -> date:
        """The first business day on or after ``d`` (following convention)."""
        while not self.is_business_day(d):
            d += timedelta(days=1)
        return d

    def roll_back(self, d: date) -> date:
        """The last business day on or before ``d``."""
        while not self.is_business_day(d):
            d -= timedelta(days=1)
        return d

    def settlement_date(self, trade_date: date, settle_days: int) -> date:
        """``trade_date`` + ``settle_days`` business days (T+1 US equities, T+2 FX); 0 -> the trade date itself."""
        return self.add_business_days(trade_date, int(settle_days))

    def business_days_between(self, start: date, end: date) -> int:
        """Count of business days in (start, end]."""
        if end <= start:
            return 0
        n, cur = 0, start
        while cur < end:
            cur += timedelta(days=1)
            if self.is_business_day(cur):
                n += 1
        return n

    def month_end(self, d: date) -> date:
        """The last business day of ``d``'s month."""
        y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
        return self.prev_business_day(date(y, m, 1))

    def source(self) -> dict:
        return {"file": self._path, "years": sorted(self._file_years), "fallback_years": sorted(self._fallback_years),
                "early_close_time_et": self._early_time.strftime("%H:%M"), "note": "CME holidays are approximated from the NYSE calendar."}
