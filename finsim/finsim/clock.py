"""The daily clock.

Career worlds run in REAL_TIME: one real calendar day is one simulated business
day, and the simulated day is processed at the world's update time (default
09:00 in the player's timezone) whether or not the player logs in. The
"target" simulated date at any real moment is the latest business day whose
update time has already passed. A world that is behind its target (server was
off, player away for a week) catches up by running each missed day in order.

SANDBOX worlds ignore real time and advance only when the player asks.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from .calendar import BusinessCalendar


@dataclass
class ClockConfig:
    mode: str = "SANDBOX"                 # REAL_TIME | SANDBOX
    timezone: str = "America/New_York"
    update_time: str = "09:00"            # HH:MM local

    def tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except Exception:
            return ZoneInfo("America/New_York")

    def update_t(self) -> time:
        h, m = self.update_time.split(":")
        return time(int(h), int(m))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def target_sim_date(cfg: ClockConfig, cal: BusinessCalendar, at: Optional[datetime] = None) -> date:
    """Latest business day whose update time has passed, in the player's timezone."""
    at = at or now_utc()
    local = at.astimezone(cfg.tz())
    d = local.date()
    if local.time() < cfg.update_t():
        d -= timedelta(days=1)
    return cal.roll_back(d)


def next_update(cfg: ClockConfig, cal: BusinessCalendar, at: Optional[datetime] = None) -> datetime:
    """Next real moment at which a business day will be processed."""
    at = at or now_utc()
    local = at.astimezone(cfg.tz())
    d = local.date()
    if local.time() >= cfg.update_t() or not cal.is_business_day(d):
        d = cal.next_business_day(d)
    return datetime.combine(d, cfg.update_t(), tzinfo=cfg.tz())
