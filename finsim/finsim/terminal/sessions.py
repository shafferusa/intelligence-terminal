"""SessionTable: per-group session state, DAY expiry, settle times (TERMINAL_DESIGN.md §2.5, §9).

Conventions (New York time; the rules live in ``tables.SESSIONS``)
- US_EQUITY / US_EQUITY_OPTIONS Mon-Fri 09:30-16:00 (13:00 on NYSE early-close dates); US_INDEX_OPTIONS to 16:15
  (13:15); US_BONDS 08:00-17:00 (14:00) with SIFMA approximated by the NYSE calendar.
- CME groups (equity index, rates, energy, metals, FX): Sun 18:00 -> Fri 17:00 with a 17:00-18:00 daily break
  Mon-Thu; full closure on Good Friday, Christmas and New Year's Day; on other NYSE holidays an early close at
  13:00 and the 18:00 reopen (CME_CAL approximation, disclosed in ``SessionState.note``).  The trade date of the
  evening session is the next business day.
- CBOT_GRAINS: Sun-Thu 20:00 -> next day 08:45, and Mon-Fri 09:30-14:20 (13:05 on early-close dates).
- FX_SPOT: Sun 17:00 -> Fri 17:00 continuous; 1 Jan and 25 Dec closed (assumption).
- ``day_order_expiry(group, at)`` = the close of the current session if open, else of the next session (a DAY
  order placed Tue 17:30 expires Wed 16:00).
- ``session_overrides`` (settings): ``{"2026-12-24": {"CME_EQUITY_INDEX": "13:00" | "CLOSED"}}`` adds an early close
  or a closure without a code change.
- FOREIGN (Build 2, OA-12): a foreign listing's session is its venue's, from the quote record's Yahoo currentTradingPeriod
  (``state("FOREIGN", now, foreign={open_ts, close_ts[, tz]})``); its trade date is the VENUE's local date of the open
  (``venue_date``), and after today's session ended the next open is estimated from today's hours on the next weekday
  (``next_venue_session``; Yahoo publishes only the current period, venue holidays are not modelled - said in the note).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, List, Optional

from . import tables
from .clock import Clock, epoch_of, ny_of

GROUPS = ("US_EQUITY", "US_EQUITY_OPTIONS", "US_INDEX_OPTIONS", "US_BONDS", "CME_EQUITY_INDEX", "CME_RATES",
          "NYMEX_ENERGY", "COMEX_METALS", "CBOT_GRAINS", "CME_FX", "FX_SPOT", "FOREIGN")
PHASES = ("OPEN", "CLOSED", "BREAK", "HOLIDAY", "WEEKEND")


@dataclass(frozen=True)
class SessionState:
    group: str
    open: bool
    phase: str                  # OPEN | CLOSED | BREAK | HOLIDAY | WEEKEND
    opened_at: Optional[int]
    closes_at: Optional[int]
    next_open: Optional[int]
    trade_date: Optional[str]
    early_close: bool
    note: str


@dataclass(frozen=True)
class Session:
    open_ts: int
    close_ts: int
    trade_date: date
    early: bool
    note: str


def _t(hhmm: str) -> time:
    h, m = hhmm.split(":")[:2]
    return time(int(h), int(m))


VENUE_DATE_SHIFT_S = 8 * 3600       # a venue opens 07:00-10:30 local at UTC-6 .. UTC+13: open + 8 h (UTC) falls on the local date


def venue_date(open_ts: int, tz: Optional[str] = None) -> date:
    """The trade date of a foreign venue's session = the venue's LOCAL calendar date of its open (a Tokyo session opening
    Wed 20:00 ET trades on Thursday in Tokyo).  With the venue's time zone (``tz``, Yahoo exchangeTimezoneName) it is exact;
    without it, the UTC date of open + 8 h: venues open between 07:00 and 10:30 local time at offsets UTC-6 .. UTC+13, so open
    + 8 h in UTC always falls on the local date (London 08:00 BST -> 15:00 UTC same day; Sydney 10:00 AEDT = 23:00 UTC the
    day before -> 07:00 UTC on the local date; Mexico 08:30 CST = 14:30 UTC -> 22:30 UTC the same day)."""
    o = int(open_ts)
    if tz:
        try:
            from finsim.tzfallback import get_zone
            return datetime.fromtimestamp(o, timezone.utc).astimezone(get_zone(str(tz))).date()
        except Exception:   # noqa: BLE001 - an unknown zone falls back to the rule below
            pass
    return datetime.fromtimestamp(o + VENUE_DATE_SHIFT_S, timezone.utc).date()


def next_venue_session(open_ts: int, close_ts: int, now: int) -> Optional[tuple]:
    """``(open_ts, close_ts)`` of a foreign venue's next session after ``now`` when today's has ended: the same hours on the
    next weekday of the venue's local date (an estimate: Yahoo publishes only the current trading period; venue holidays
    are not modelled).  None when nothing is found within 10 days."""
    o, c = int(open_ts), int(close_ts)
    if c > now:
        return o, c
    for k in range(1, 11):
        o2, c2 = o + 86400 * k, c + 86400 * k
        if c2 > now and venue_date(o2).weekday() < 5:
            return o2, c2
    return None


class SessionTable:
    def __init__(self, cal, clock: Clock, overrides: Optional[dict] = None):
        self.cal = cal
        self.clock = clock
        self.overrides: Dict[str, Dict[str, str]] = dict(overrides or {})

    def set_overrides(self, overrides: Optional[dict]) -> None:
        self.overrides = dict(overrides or {})

    # ---- rule evaluation -------------------------------------------------------------------------------------------
    def _override(self, group: str, d: date) -> Optional[str]:
        day = self.overrides.get(d.isoformat()) or {}
        return day.get(group) or day.get("*")

    def sessions_for(self, group: str, d: date) -> List[Session]:
        """The sessions attributed to trade date ``d`` (a CME session starts the evening before)."""
        spec = tables.SESSIONS.get(group)
        if spec is None:
            raise KeyError(f"unknown session group {group!r}")
        kind = spec["kind"]
        ov = self._override(group, d)
        if ov == "CLOSED" or kind in ("FOREIGN", "NONE"):
            return []
        cal = self.cal
        if kind == "US":
            if not cal.is_business_day(d):
                return []
            close, early, note = spec["close"], False, ""
            if cal.early_close(d) is not None:
                close, early, note = spec["early"], True, "NYSE early close"
            if ov:
                close, early, note = ov, True, "early close (settings override)"
            return [Session(epoch_of(d, spec["open"]), epoch_of(d, close), d, early, note)]
        if kind == "CME":
            if d.weekday() >= 5 or cal.full_closure(d):
                return []
            close, early, note = spec["close"], False, spec["note"]
            if cal.is_holiday(d):
                close, early, note = spec["early"], True, "NYSE holiday: CME approximated as an early close at 13:00, reopen 18:00"
            elif cal.early_close(d) is not None:
                close, early, note = spec["early"], True, "NYSE early close: futures close 13:00 (approximation)"
            if ov:
                close, early, note = ov, True, "early close (settings override)"
            return [Session(epoch_of(d - timedelta(days=1), spec["open"]), epoch_of(d, close), d, early, note)]
        if kind == "GRAINS":
            if d.weekday() >= 5 or cal.full_closure(d):
                return []
            close, early, note = spec["close"], False, spec["note"]
            if cal.is_holiday(d) or cal.early_close(d) is not None:
                close, early, note = spec["early"], True, "NYSE holiday / early close: grains day session closes 13:05 (approximation)"
            if ov:
                close, early, note = ov, True, "early close (settings override)"
            night = Session(epoch_of(d - timedelta(days=1), spec["night_open"]), epoch_of(d, spec["night_close"]), d, False, "night session")
            day = Session(epoch_of(d, spec["open"]), epoch_of(d, close), d, early, note)
            return [night, day]
        if kind == "FX":
            if d.weekday() >= 5 or (d.month, d.day) in ((1, 1), (12, 25)):
                return []
            close = ov or spec["close"]
            return [Session(epoch_of(d - timedelta(days=1), spec["open"]), epoch_of(d, close), d, bool(ov), spec["note"])]
        return []

    def _window(self, group: str, at: int) -> List[Session]:
        d0 = ny_of(at).date() - timedelta(days=1)
        out: List[Session] = []
        for i in range(0, 16):
            out.extend(self.sessions_for(group, d0 + timedelta(days=i)))
        out.sort(key=lambda s: s.open_ts)
        return out

    def _group_holiday(self, group: str, d: date) -> bool:
        kind = tables.SESSIONS[group]["kind"]
        if kind == "FX":
            return (d.month, d.day) in ((1, 1), (12, 25))
        if kind in ("CME", "GRAINS"):
            return self.cal.full_closure(d) or self.cal.is_holiday(d)
        return self.cal.is_holiday(d)

    # ---- public --------------------------------------------------------------------------------------------------
    def state(self, group: str, at: Optional[int] = None, foreign: Optional[dict] = None) -> SessionState:
        now = int(at if at is not None else self.clock.epoch())
        if group == "FOREIGN" or group.startswith("FOREIGN:") or group == "PRIVATE_MARKETS":
            if foreign and foreign.get("open_ts") is not None and foreign.get("close_ts") is not None:
                o, c = int(foreign["open_ts"]), int(foreign["close_ts"])
                td = str(foreign.get("trade_date") or venue_date(o, foreign.get("tz")).isoformat())
                if o <= now < c:
                    return SessionState(group, True, "OPEN", o, c, None, td, False, "session from Yahoo currentTradingPeriod")
                if o > now:
                    return SessionState(group, False, "CLOSED", None, None, o, td, False, "session from Yahoo currentTradingPeriod (opens later today)")
                nxt = next_venue_session(o, c, now)
                if nxt is None:
                    return SessionState(group, False, "CLOSED", None, None, None, td, False, "session from Yahoo currentTradingPeriod (ended)")
                return SessionState(group, False, "CLOSED", None, None, nxt[0], venue_date(nxt[0], foreign.get("tz")).isoformat(), False,
                                    "today's venue session ended; the next open is estimated from today's hours on the next weekday"
                                    " (venue holidays are not modelled)")
            note = tables.SESSIONS.get("PRIVATE_MARKETS" if group == "PRIVATE_MARKETS" else "FOREIGN", {}).get("note", "")
            return SessionState(group, False, "CLOSED", None, None, None, None, False, note or "no session information")
        sessions = self._window(group, now)
        nyd = ny_of(now).date()
        for i, s in enumerate(sessions):
            if s.open_ts <= now < s.close_ts:
                nxt = sessions[i + 1].open_ts if i + 1 < len(sessions) else None
                return SessionState(group, True, "OPEN", s.open_ts, s.close_ts, nxt, s.trade_date.isoformat(), s.early, s.note)
        nxt = next((s for s in sessions if s.open_ts > now), None)
        prev = None
        for s in sessions:
            if s.close_ts <= now:
                prev = s
        if nyd.weekday() >= 5:
            phase = "WEEKEND"
        elif self._group_holiday(group, nyd):
            phase = "HOLIDAY"
        elif prev is not None and nxt is not None and ny_of(prev.close_ts).date() == nyd and ny_of(nxt.open_ts).date() == nyd:
            phase = "BREAK"
        else:
            phase = "CLOSED"
        note = tables.SESSIONS[group].get("note", "")
        if phase == "HOLIDAY":
            note = "closed: holiday" + (f" ({note})" if note else "")
        elif nxt is not None and nxt.note:
            note = nxt.note
        return SessionState(group, False, phase, None, None, nxt.open_ts if nxt else None,
                            nxt.trade_date.isoformat() if nxt else None, bool(nxt and nxt.early), note)

    def states(self, at: Optional[int] = None) -> Dict[str, SessionState]:
        return {g: self.state(g, at) for g in GROUPS}

    def next_session(self, group: str, at: int) -> Optional[Session]:
        """The session an order submitted at ``at`` can first trade in (the current one if open)."""
        now = int(at)
        for s in self._window(group, now):
            if s.close_ts > now:
                return s
        return None

    def day_order_expiry(self, group: str, at: int) -> int:
        """``closes_at`` of the session the order can first trade in: the current session if open, else the next one."""
        s = self.next_session(group, at)
        if s is None:
            raise ValueError(f"no session within 16 days for {group}")
        return s.close_ts

    def settle_time(self, group: str, d: date) -> Optional[int]:
        """Futures daily settlement time of trade date ``d`` (``tables.SETTLE_TIME_ET``), capped at the session close
        on early-close days; None for non-futures groups and non-trading days."""
        hhmm = tables.SETTLE_TIME_ET.get(group)
        if hhmm is None:
            return None
        sessions = self.sessions_for(group, d)
        if not sessions:
            return None
        return min(epoch_of(d, hhmm), sessions[-1].close_ts)

    def is_trading_day(self, group: str, d: date) -> bool:
        return bool(self.sessions_for(group, d))

    def session_close(self, group: str, d: date) -> Optional[int]:
        sessions = self.sessions_for(group, d)
        return sessions[-1].close_ts if sessions else None

    def rules(self) -> dict:
        """Rule sources for ``/api/sessions``."""
        return {g: dict(tables.SESSIONS[g], overrides={k: v[g] for k, v in self.overrides.items() if g in v}) for g in GROUPS}
