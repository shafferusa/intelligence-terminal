"""Clocks and New York time helpers (TERMINAL_DESIGN.md §2.3, §6.6).

Conventions
- ``Clock.now()`` is an aware UTC datetime; ``FakeClock`` is injected by tests and ``--offline`` runs.
- The accounting day closes at 17:00 ET: ``accounting_day(now)`` is today up to and including 17:00:00 and
  tomorrow after it (the day D covers (D-1 17:00, D 17:00]; sentence ACCT_DAY).
- The trade date of a CME futures group after 18:00 ET is the next business day (the evening session belongs to
  the next day); every other group's trade date is the New York calendar date.
- Zones come only from ``finsim.tzfallback.get_zone`` (a stock Windows Python has no tz database).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Union

from finsim.tzfallback import get_zone

NY = get_zone("America/New_York")
UTC = timezone.utc
ACCOUNTING_CLOSE = time(17, 0)
FUTURES_EVENING_OPEN = time(18, 0)
FUTURES_GROUPS = ("CME_EQUITY_INDEX", "CME_RATES", "NYMEX_ENERGY", "COMEX_METALS", "CBOT_GRAINS", "CME_FX")


def _coerce(at: Union[datetime, str, int, float]) -> datetime:
    """Accept an aware datetime, a naive datetime (read as New York wall time), an ISO string or an epoch."""
    if isinstance(at, (int, float)) and not isinstance(at, bool):
        return datetime.fromtimestamp(int(at), UTC)
    if isinstance(at, str):
        at = datetime.fromisoformat(at.replace("Z", "+00:00"))
    if not isinstance(at, datetime):
        raise TypeError(f"not a datetime: {at!r}")
    if at.tzinfo is None:
        at = at.replace(tzinfo=NY)
    return at.astimezone(UTC)


class Clock:
    """Injectable clock.  ``now()`` is aware UTC."""

    def now(self) -> datetime:
        raise NotImplementedError

    def epoch(self) -> int:
        return int(self.now().timestamp())

    def ny(self) -> datetime:
        return self.now().astimezone(NY)

    def today(self) -> date:
        """The New York calendar date."""
        return self.ny().date()


class RealClock(Clock):
    def now(self) -> datetime:
        return datetime.now(UTC)


class FakeClock(Clock):
    """``FakeClock(at)``: ``set(dt)`` and ``advance(seconds)`` move it; nothing else does."""

    def __init__(self, at: Union[datetime, str, int, float]):
        self._now = _coerce(at)

    def now(self) -> datetime:
        return self._now

    def set(self, dt: Union[datetime, str, int, float]) -> None:
        self._now = _coerce(dt)

    def advance(self, seconds: Union[int, float]) -> None:
        self._now = self._now + timedelta(seconds=seconds)


def ny_of(epoch: int) -> datetime:
    """Epoch seconds -> aware New York datetime."""
    return datetime.fromtimestamp(int(epoch), UTC).astimezone(NY)


def epoch_of(d: date, hhmm: str) -> int:
    """New York wall time on date ``d`` -> epoch seconds."""
    h, m = hhmm.split(":")[:2]
    return int(datetime.combine(d, time(int(h), int(m)), tzinfo=NY).timestamp())


def accounting_day(now: datetime) -> date:
    """D = (D-1 17:00 ET, D 17:00 ET]: at or before 17:00 -> today, after -> tomorrow."""
    ny = _coerce(now).astimezone(NY)
    return ny.date() if ny.time() <= ACCOUNTING_CLOSE else ny.date() + timedelta(days=1)


def trade_date(now: datetime, group: str, cal) -> date:
    """CME futures groups after 18:00 ET -> the next business day; else the New York date."""
    ny = _coerce(now).astimezone(NY)
    if group in FUTURES_GROUPS and ny.time() >= FUTURES_EVENING_OPEN:
        return cal.next_business_day(ny.date())
    return ny.date()


def iso(dt: datetime) -> str:
    """'YYYY-MM-DDTHH:MM:SS+00:00'."""
    return _coerce(dt).isoformat(timespec="seconds")


def ny_str(epoch: int) -> str:
    """Display form 'YYYY-MM-DD HH:MM:SS ET' used by memos and the API's ``*_ny`` fields."""
    return ny_of(epoch).strftime("%Y-%m-%d %H:%M:%S ET")
