"""Time zones without a time-zone database.

`zoneinfo` needs the operating system's tz database or the `tzdata` package; a stock Windows Python has neither
("No time zone found with key America/New_York"), and FinSim is standard-library only. `get_zone(name)` returns
`ZoneInfo(name)` when it is available and otherwise a built-in rule-based zone for the zones FinSim uses:

    US (New York, Chicago, Denver, Los Angeles)   DST from the 2nd Sunday of March 02:00 local to the 1st Sunday of
                                                  November 02:00 (2007 on); 1st Sunday of April – last Sunday of
                                                  October before 2007
    UK / EU (London, Paris, Berlin, Frankfurt, Zurich, Madrid, Rome, Amsterdam, Dublin)
                                                  DST from the last Sunday of March to the last Sunday of October,
                                                  01:00 UTC (the harmonised EU rule, from 1996)
    fixed offsets (Tokyo, Hong Kong, Shanghai, Singapore, Kolkata, UTC)

An unknown name raises `ZoneNotFound` (a KeyError, like zoneinfo's own error).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Callable, Optional, Tuple


class ZoneNotFound(KeyError):
    pass


def _nth_sunday(y: int, m: int, n: int) -> date:
    d = date(y, m, 1)
    d += timedelta(days=(6 - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_sunday(y: int, m: int) -> date:
    d = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def _us_rule(y: int, std_h: int) -> Tuple[datetime, datetime]:
    """DST [start, end) in naive UTC for a US zone with standard offset std_h (e.g. −5)."""
    if y >= 2007:
        a, b = _nth_sunday(y, 3, 2), _nth_sunday(y, 11, 1)
    else:
        a, b = _nth_sunday(y, 4, 1), _last_sunday(y, 10)
    start = datetime(a.year, a.month, a.day, 2) - timedelta(hours=std_h)          # 02:00 standard time
    end = datetime(b.year, b.month, b.day, 2) - timedelta(hours=std_h + 1)        # 02:00 daylight time
    return start, end


def _eu_rule(y: int, std_h: int) -> Tuple[datetime, datetime]:
    a, b = _last_sunday(y, 3), _last_sunday(y, 10)
    return datetime(a.year, a.month, a.day, 1), datetime(b.year, b.month, b.day, 1)


class RuleZone(tzinfo):
    def __init__(self, key: str, std_h: int, rule: Optional[Callable[[int, int], Tuple[datetime, datetime]]], names=("STD", "DST")):
        self.key, self.std, self.rule, self.names = key, timedelta(hours=std_h), rule, names
        self._std_h = std_h

    def _dst_utc(self, u: datetime) -> bool:
        if not self.rule:
            return False
        a, b = self.rule(u.year, self._std_h)
        return a <= u < b

    def utcoffset(self, dt):
        if dt is None:
            return self.std
        u = dt.replace(tzinfo=None) - self.std          # local wall time read as standard time
        return self.std + (timedelta(hours=1) if self._dst_utc(u) else timedelta(0))

    def dst(self, dt):
        return self.utcoffset(dt) - self.std if dt is not None else timedelta(0)

    def tzname(self, dt):
        return self.names[1] if dt is not None and self.dst(dt) else self.names[0]

    def fromutc(self, dt):
        u = dt.replace(tzinfo=None)
        off = self.std + (timedelta(hours=1) if self._dst_utc(u) else timedelta(0))
        return (u + off).replace(tzinfo=self)

    def __repr__(self):
        return f"RuleZone({self.key!r})"

    def __str__(self):
        return self.key


_ZONES = {
    "America/New_York": (-5, _us_rule, ("EST", "EDT")), "US/Eastern": (-5, _us_rule, ("EST", "EDT")),
    "America/Chicago": (-6, _us_rule, ("CST", "CDT")), "US/Central": (-6, _us_rule, ("CST", "CDT")),
    "America/Denver": (-7, _us_rule, ("MST", "MDT")), "America/Los_Angeles": (-8, _us_rule, ("PST", "PDT")),
    "US/Pacific": (-8, _us_rule, ("PST", "PDT")),
    "Europe/London": (0, _eu_rule, ("GMT", "BST")), "Europe/Dublin": (0, _eu_rule, ("GMT", "IST")),
    "Europe/Paris": (1, _eu_rule, ("CET", "CEST")), "Europe/Berlin": (1, _eu_rule, ("CET", "CEST")),
    "Europe/Frankfurt": (1, _eu_rule, ("CET", "CEST")), "Europe/Zurich": (1, _eu_rule, ("CET", "CEST")),
    "Europe/Madrid": (1, _eu_rule, ("CET", "CEST")), "Europe/Rome": (1, _eu_rule, ("CET", "CEST")),
    "Europe/Amsterdam": (1, _eu_rule, ("CET", "CEST")),
    "Asia/Tokyo": (9, None, ("JST", "JST")), "Asia/Hong_Kong": (8, None, ("HKT", "HKT")),
    "Asia/Shanghai": (8, None, ("CST", "CST")), "Asia/Singapore": (8, None, ("SGT", "SGT")),
    "Asia/Kolkata": (5.5, None, ("IST", "IST")),
}
_CACHE: dict = {}


def builtin_zone(name: str) -> tzinfo:
    if name in ("UTC", "Etc/UTC", "GMT", "Z"):
        return timezone.utc
    spec = _ZONES.get(name)
    if spec is None:
        raise ZoneNotFound(name)
    if name not in _CACHE:
        std, rule, names = spec
        _CACHE[name] = RuleZone(name, std, rule, names) if float(std).is_integer() else timezone(timedelta(hours=std), names[0])
    return _CACHE[name]


def get_zone(name: str) -> tzinfo:
    """ZoneInfo(name) when the tz database is available, else the built-in zone (KeyError if unknown)."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 — ZoneInfoNotFoundError, missing tzdata, bad key
        return builtin_zone(name)
