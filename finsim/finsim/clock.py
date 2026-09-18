"""The daily clock.

Career worlds run in REAL_TIME: one real calendar day is one simulated business
day, and the simulated day is processed at the world's update time (default
17:00 New York, after the real close) whether or not the player logs in.
Instructions may be entered at any hour. One entered while a session is
running executes at that session's close (never at an open that has already
printed); one entered while the market is shut executes at the next open. A
save that tracks the real market can also fill a ticket immediately at the
latest quote (see engines/live.py). The optional session lock (`lock_session`)
restores the stricter rule: no instructions from 09:30 New York until the
update. The
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
    lock_session: bool = False            # refuse instructions from 09:30 New York until the update (off: trade at any hour)

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
SESSION_FINAL = time(16, 0)           # the session closes at the 16:00 bell


def market_now(at: Optional[datetime] = None) -> datetime:
    return (at or now_utc()).astimezone(MARKET_TZ)


def session_closed(d: date, at: Optional[datetime] = None) -> bool:
    """Has the real session of business day `d` closed (New York time)? Earlier days: yes; today: from 16:00; later: no."""
    ny = market_now(at)
    if d < ny.date():
        return True
    if d > ny.date():
        return False
    return ny.time() >= SESSION_FINAL


QUOTE_FIRST = time(9, 45)             # the first quote update of a session: the 09:30 open, seen 15 minutes late
QUOTE_LAST = time(16, 15)             # the last: the 16:00 close, seen 15 minutes late
QUOTE_STEP = timedelta(minutes=15)


def quote_ticks(d: date) -> list:
    """The quote updates of business day `d` (New York): every 15 minutes from 09:45 to 16:15, 27 in all."""
    t = datetime.combine(d, QUOTE_FIRST, tzinfo=MARKET_TZ)
    end = datetime.combine(d, QUOTE_LAST, tzinfo=MARKET_TZ)
    out = []
    while t <= end:
        out.append(t)
        t += QUOTE_STEP
    return out


def next_quote_tick(cal: BusinessCalendar, at: Optional[datetime] = None) -> datetime:
    """The next quote update strictly after `at`: later today if the session still has one, else 09:45 on the next business day."""
    ny = market_now(at)
    d = ny.date()
    if cal.is_business_day(d):
        for t in quote_ticks(d):
            if t > ny:
                return t
    return quote_ticks(cal.next_business_day(d))[0]


def latest_quote_tick(cal: BusinessCalendar, at: Optional[datetime] = None) -> datetime:
    """The most recent quote update at or before `at`: today's last one that has passed, else 16:15 on the previous business day."""
    ny = market_now(at)
    d = ny.date()
    if cal.is_business_day(d):
        passed = [t for t in quote_ticks(d) if t <= ny]
        if passed:
            return passed[-1]
    return quote_ticks(cal.prev_business_day(d))[-1]


def trading_window(cfg: ClockConfig, cal: BusinessCalendar, at: Optional[datetime] = None) -> dict:
    """When instructions may be entered in a career world.

    Default: always — a live ticket fills now at the latest quote, an instruction executes at the next update (at the
    session's close if it is already running, at the open otherwise). With the session lock on: open from the day's
    update (or the close, whichever is later) until 09:29 New York on the next business day, locked while a session
    runs. Sandbox worlds are always open.
    """
    if cfg.mode != "REAL_TIME":
        return {"open": True, "mode": "SANDBOX", "lock": False, "reason": "sandbox: instructions execute when you advance the day"}
    ny = market_now(at)
    business = cal.is_business_day(ny.date())
    upd_local = datetime.combine(ny.date(), cfg.update_t(), tzinfo=cfg.tz()).astimezone(MARKET_TZ)
    lock_end = max(upd_local.time(), SESSION_FINAL)
    running = business and MARKET_OPEN <= ny.time() < lock_end
    if not cfg.lock_session:
        return {"open": True, "mode": "REAL_TIME", "lock": False, "session_running": running,
                "reason": ("the session is running: a live ticket fills now at the latest quote (up to 15 minutes delayed); an instruction executes at the next quote update (every 15 minutes, 09:45 to 16:15 New York)"
                           if running else "the market is shut: a live ticket fills now at the latest quote (up to 15 minutes delayed); an instruction executes at the next session's first quote update, 09:45 New York")}
    if running:
        opens = datetime.combine(ny.date(), lock_end, tzinfo=MARKET_TZ)
        return {"open": False, "mode": "REAL_TIME", "lock": True, "session_running": True, "opens_at": opens.isoformat(),
                "reason": f"the session is running: instructions reopen at {opens.strftime('%H:%M')} New York, once today's close is in"}
    # open now: until 09:30 New York on the next business day (today, if it has not opened yet)
    nxt = ny.date() if (business and ny.time() < MARKET_OPEN) else cal.next_business_day(ny.date())
    closes = datetime.combine(nxt, MARKET_OPEN, tzinfo=MARKET_TZ)
    return {"open": True, "mode": "REAL_TIME", "lock": True, "session_running": False, "closes_at": closes.isoformat(),
            "reason": f"instructions are taken until {closes.strftime('%a %H:%M')} New York; they execute at the next update against that session"}


def instruction_session(cfg: ClockConfig, cal: BusinessCalendar, current: date, at: Optional[datetime] = None) -> str:
    """Where an instruction entered now executes at the next update: "OPEN" of the next session, or its "CLOSE" when that
    session has already opened (its open has printed and can be seen, so the fill waits for the close). Sandbox: OPEN."""
    if cfg.mode != "REAL_TIME":
        return "OPEN"
    ny = market_now(at)
    nxt = cal.next_business_day(current)
    if ny.date() > nxt or (ny.date() == nxt and ny.time() >= MARKET_OPEN):
        return "CLOSE"
    return "OPEN"
