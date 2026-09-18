"""The daily clock.

Career worlds run in REAL_TIME: one real calendar day is one simulated business
day, and the simulated day is processed at the world's update time (default
17:00 New York, after the real close) whether or not the player logs in.
Instructions are taken only while the market is shut — from the update until
09:29 New York the next session — so nothing is entered with the session's
prices already on the screen. The
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
    update_time: str = "17:00"            # HH:MM local

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


MARKET_TZ = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)
SESSION_FINAL = time(16, 15)          # the official closes are final a little after the 16:00 bell


def market_now(at: Optional[datetime] = None) -> datetime:
    return (at or now_utc()).astimezone(MARKET_TZ)


def session_closed(d: date, at: Optional[datetime] = None) -> bool:
    """Has the real session of business day `d` closed (New York time)? Earlier days: yes; today: after 16:15; later: no."""
    ny = market_now(at)
    if d < ny.date():
        return True
    if d > ny.date():
        return False
    return ny.time() >= SESSION_FINAL


def trading_window(cfg: ClockConfig, cal: BusinessCalendar, at: Optional[datetime] = None) -> dict:
    """When instructions may be entered in a career world.

    Open from the day's update (or the close, whichever is later) until 09:29 New York on the next business day;
    locked while a session is running, from 09:30 until the update that processes it. Sandbox worlds are always open.
    """
    if cfg.mode != "REAL_TIME":
        return {"open": True, "mode": "SANDBOX", "reason": "sandbox: instructions execute when you advance the day"}
    ny = market_now(at)
    upd_local = datetime.combine(ny.date(), cfg.update_t(), tzinfo=cfg.tz()).astimezone(MARKET_TZ)
    lock_end = max(upd_local.time(), SESSION_FINAL)
    business = cal.is_business_day(ny.date())
    if business and MARKET_OPEN <= ny.time() < lock_end:
        opens = datetime.combine(ny.date(), lock_end, tzinfo=MARKET_TZ)
        return {"open": False, "mode": "REAL_TIME", "opens_at": opens.isoformat(),
                "reason": f"the session is running: instructions reopen at {opens.strftime('%H:%M')} New York, once today's close is in"}
    # open now: until 09:30 New York on the next business day (today, if it has not opened yet)
    nxt = ny.date() if (business and ny.time() < MARKET_OPEN) else cal.next_business_day(ny.date())
    closes = datetime.combine(nxt, MARKET_OPEN, tzinfo=MARKET_TZ)
    return {"open": True, "mode": "REAL_TIME", "closes_at": closes.isoformat(),
            "reason": f"instructions are taken until {closes.strftime('%a %H:%M')} New York; they execute at the next update against that session"}
