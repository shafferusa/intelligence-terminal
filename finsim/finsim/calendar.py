"""Business-day calendar and settlement conventions.

Settlement cycles are configuration, not code: `SettlementConfig` maps a market
code to a cycle in business days and a holiday calendar. Adding a market with a
T+2 cycle is a config change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, Iterable, Set


def _observed(d: date) -> date:
    """NYSE observation rule: Sunday -> Monday, Saturday -> preceding Friday."""
    if d.weekday() == 6:
        return d + timedelta(days=1)
    if d.weekday() == 5:
        return d - timedelta(days=1)
    return d


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    offset = (d.weekday() - weekday) % 7
    return d - timedelta(days=offset)


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


def us_equity_holidays(year: int) -> Set[date]:
    """Rule-generated NYSE full-closure holidays (good enough for a simulation)."""
    hols = {
        _observed(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),      # MLK Day
        _nth_weekday(year, 2, 0, 3),      # Presidents' Day
        _easter(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),        # Memorial Day
        _observed(date(year, 6, 19)),     # Juneteenth
        _observed(date(year, 7, 4)),      # Independence Day
        _nth_weekday(year, 9, 0, 1),      # Labor Day
        _nth_weekday(year, 11, 3, 4),     # Thanksgiving
        _observed(date(year, 12, 25)),    # Christmas
    }
    # New Year's Day on a Saturday is not observed on the preceding Friday.
    if date(year, 1, 1).weekday() == 5:
        hols.discard(date(year - 1, 12, 31))
    return hols


class BusinessCalendar:
    def __init__(self, name: str = "US", holiday_fn=us_equity_holidays):
        self.name = name
        self._holiday_fn = holiday_fn
        self._cache: Dict[int, Set[date]] = {}

    def holidays(self, year: int) -> Set[date]:
        if year not in self._cache:
            self._cache[year] = set(self._holiday_fn(year))
        return self._cache[year]

    def is_business_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays(d.year)

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
        step = 1 if n >= 0 else -1
        for _ in range(abs(n)):
            cur = self.next_business_day(cur) if step > 0 else self.prev_business_day(cur)
        return cur

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

    def roll_back(self, d: date) -> date:
        """Latest business day on or before `d`."""
        while not self.is_business_day(d):
            d -= timedelta(days=1)
        return d

    def month_end(self, d: date) -> date:
        """Last business day of d's month."""
        y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
        return self.prev_business_day(date(y, m, 1))

    def is_month_end(self, d: date) -> bool:
        return self.next_business_day(d).month != d.month

    def roll(self, d: date) -> date:
        """Move to the next business day if `d` is not one (following convention)."""
        while not self.is_business_day(d):
            d += timedelta(days=1)
        return d


@dataclass
class SettlementConfig:
    """Per-market settlement cycles, in business days after trade date."""
    cycles: Dict[str, int] = field(default_factory=lambda: {
        "US_EQUITY": 1,       # T+1 since May 2024
        "US_TREASURY": 1,
        "US_CORP_BOND": 1,
        "US_MONEY_MARKET": 0,
        "EU_EQUITY": 2,
        "JP_EQUITY": 2,
        "FX_SPOT": 2,
        "FUTURES": 0,          # cleared: margined daily, no DVP settlement
        "US_OPTIONS": 1,       # listed option premium settles T+1 through the clearinghouse
        "PHYSICAL": 2,         # physical commodity inventory: title and cash two sessions after the deal
    })
    calendars: Dict[str, str] = field(default_factory=lambda: {"default": "US"})

    def cycle_for(self, market: str) -> int:
        if market not in self.cycles:
            raise KeyError(f"No settlement cycle configured for market {market!r}")
        return self.cycles[market]


def settlement_date(cal: BusinessCalendar, cfg: SettlementConfig, market: str, trade_date: date) -> date:
    return cal.add_business_days(trade_date, cfg.cycle_for(market))
