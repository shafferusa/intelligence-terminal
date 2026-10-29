"""Currency settlement calendars, FX value dates and swap schedules (FX_CCS_PLAN rev 2 D10, K1; release 1.0.6.1, package P-A).

Pure: no database, no network, no clock.  Every function that takes ``services`` reads only ``services.cal`` (the NYSE trading
calendar), and only for a currency that has no calendar here.

Calendars (rule-based, 2000-2099; each a ``Calendar`` with ``is_business_day``, ``roll_following``, ``roll_back``,
``modified_following``, ``add_business_days``, ``month_end``, ``holidays(year)``, ``name``, ``limited`` and ``note``)
- ``TARGET`` (TARGET2, the euro's settlement days): New Year's Day, Good Friday, Easter Monday, 1 May, 25 and 26 December.  The days
  are fixed: one that falls on a weekend is not moved (``holidays(year)`` lists it as published).  One-off: 31 December 2001 (the euro
  cash changeover).
- ``USNY`` (New York, the Federal Reserve's holidays): New Year's Day, Martin Luther King Jr. Day, Washington's Birthday, Memorial Day,
  Juneteenth (from 2021), Independence Day, Labor Day, Columbus Day, Veterans Day, Thanksgiving Day, Christmas Day.  A holiday on a
  Sunday is observed on the Monday; one on a Saturday is NOT observed (the Federal Reserve Banks open on the Friday before).
- ``GBLO`` (London, the bank holidays of England and Wales): New Year's Day, Good Friday, Easter Monday, the early-May, spring and
  summer bank holidays, Christmas Day and Boxing Day, with the substitute-day rules (a holiday on a weekend moves to the next weekday
  that is not already a holiday).  One-off changes (jubilees, royal weddings, a state funeral, the moved May 2020 holiday) come from the
  hand-maintained ``GBLO_ONE_OFF`` list; none is known for 2026-2030 (the label says so).
- ``USGS`` (US government securities business days, SIFMA's recommendations; SOFR and the SOFR Index are published on these days
  only): New York plus Good Friday, plus the Friday before an Independence Day, Juneteenth or Christmas Day that falls on a Saturday,
  plus SIFMA's one-off full closes, minus the years in which SIFMA recommended only an early close on Good Friday.  The lists
  (``USGS_ONE_OFF``, ``USGS_SATURDAY_TO_FRIDAY``, ``USGS_GOOD_FRIDAY_OPEN``) are hand-maintained; they reproduce every SOFR publication
  day 2018-04-03 .. 2026-10-27 (FRED ``SOFR``), and the label says the lists are hand-maintained.  The calendars were checked the same
  way: TARGET2 against every ESTR publication day since 2019-10-01, London against every SONIA day since 2000, New York against every
  EFFR day since 2016.
- ``for_ccy(ccy)`` -> the calendar of USD / EUR / GBP, else None.  A currency without a calendar uses the NYSE calendar
  (``services.cal``) through ``legacy(services)``, ``limited`` True, labelled "local holidays not modelled".
- ``joint(*cals)``: a day is a business day only when it is one in every calendar.

FX value dates (D10)
- ``value_calendar(services, pair)``: both currencies for a USD pair (EURUSD: TARGET + New York); both currencies AND New York for a
  cross (EURGBP: TARGET + London + New York: the SFI buys the delivered currency against USD for the same value date).  A pair with a
  currency outside USD / EUR / GBP keeps the NYSE calendar (``limited``) - the pre-1.0.6.1 dates exactly.
- ``spot_date(services, pair, trade_date)``: ``instruments.fx_spot_days(pair)`` business days.  A USD pair: the first day (T+1) need only
  be a business day of the non-USD currency (a New York holiday on T+1 does not delay spot), every later day is good in both
  (EURUSD traded 2026-11-10 settles 2026-11-12, not 11-13: 11-11 is Veterans Day).  A cross: T+1 is a business day of both
  currencies, every later day is good in both and in New York (EURGBP traded 2026-11-24 settles 2026-11-27: 11-26 is Thanksgiving).
  A legacy pair: ``services.cal.settlement_date`` as before.
- ``tenor_date(services, pair, tenor, trade_date)``: ON = spot + 1 business day (spot-next); 1W / 2W = spot + 7 / 14 days rolled
  following; months (1M..12M, 18M, 1Y = 12M, 2Y = 24M = 24M) = spot + n months, modified following on the value calendar, with the
  end-of-month rule (a spot date on its month's last business day goes to the target month's last business day).
- ``value_date_problem(services, pair, d)`` names the calendar a value date fails ("not a business day in EUR (TARGET2)"; for a
  cross's New York check "not a business day in USD (New York): the EUR is bought against USD for the same value date");
  ``require_value_date`` raises it as 400 VALUE_DATE.

Schedules
- ``schedule(cal, anchor, months, step_months, pay_delay_bd=0, sofr=False)`` -> ``[(s, e, p)]``: e_k = MF(anchor + k x step months)
  (each end computed from the anchor, so dates never drift), s_1 = anchor, s_k = e_(k-1), p_k = e_k + ``pay_delay_bd`` business days of
  ``cal``.  With ``sofr`` every s and e must be a USGS day (the SOFR Index exists for USGS days only), else 400 SCHEDULE "SOFR is not
  published on <date> (SIFMA close)".
"""
from __future__ import annotations

import calendar as _pycal
import threading
from datetime import date, timedelta
from typing import Callable, Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

from .commands import RejectError

YEAR_MIN, YEAR_MAX = 2000, 2099
OTC_TENORS: Tuple[str, ...] = ("1W", "2W", "1M", "2M", "3M", "6M", "9M", "1Y", "18M", "2Y")
#: every tenor ``tenor_date`` understands: ON, 1W, 2W, 1M-12M, 18M, 1Y, 2Y and 24M (24M is an alias of 2Y)
TENOR_WEEKS: Dict[str, int] = {"1W": 1, "2W": 2}
TENOR_MONTHS: Dict[str, int] = dict({f"{n}M": n for n in range(1, 13)}, **{"18M": 18, "1Y": 12, "2Y": 24, "24M": 24})
TENORS: Tuple[str, ...] = ("ON",) + tuple(TENOR_WEEKS) + tuple(TENOR_MONTHS)
LIMITED_NOTE = "local holidays not modelled: value dates use the NYSE calendar"
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


# ---- date helpers (pure) ---------------------------------------------------------------------------------------------------------
def easter(year: int) -> date:
    """Easter Sunday (Gregorian; the anonymous algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th ``weekday`` (Monday 0) of a month."""
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year, month, _pycal.monthrange(year, month)[1])
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def add_months(d: date, months: int) -> date:
    """``d`` plus ``months`` calendar months, the day clamped to the target month's length (31 Jan + 1M = 28 / 29 Feb)."""
    m = d.month - 1 + int(months)
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, _pycal.monthrange(y, m)[1]))


def _as_date(d) -> date:
    return d if isinstance(d, date) else date.fromisoformat(str(d)[:10])


# ---- holiday rules ---------------------------------------------------------------------------------------------------------------
def _target_rule(year: int) -> List[date]:
    e = easter(year)
    days = {date(year, 1, 1), e - timedelta(days=2), e + timedelta(days=1), date(year, 5, 1), date(year, 12, 25), date(year, 12, 26)}
    return sorted(days | TARGET_ONE_OFF.get(year, frozenset()))


def _fed_observed(d: date) -> Optional[date]:
    """The Federal Reserve's observance: a Sunday holiday on the Monday, a Saturday holiday not at all."""
    if d.weekday() == 6:
        return d + timedelta(days=1)
    if d.weekday() == 5:
        return None
    return d


def _usny_rule(year: int) -> List[date]:
    fixed = [date(year, 1, 1), date(year, 7, 4), date(year, 11, 11), date(year, 12, 25)] + ([date(year, 6, 19)] if year >= 2021 else [])
    days = {o for o in (_fed_observed(d) for d in fixed) if o is not None}
    days |= {nth_weekday(year, 1, 0, 3), nth_weekday(year, 2, 0, 3), last_weekday(year, 5, 0), nth_weekday(year, 9, 0, 1),
             nth_weekday(year, 10, 0, 2), nth_weekday(year, 11, 3, 4)}
    return sorted(days)


def _gblo_rule(year: int) -> List[date]:
    e = easter(year)
    days = {e - timedelta(days=2), e + timedelta(days=1), nth_weekday(year, 5, 0, 1), last_weekday(year, 5, 0), last_weekday(year, 8, 0)}
    ny = date(year, 1, 1)
    days.add(ny if ny.weekday() < 5 else ny + timedelta(days=7 - ny.weekday()))          # Saturday / Sunday -> the Monday
    xmas = date(year, 12, 25)
    if xmas.weekday() == 5:                       # Saturday: Christmas on Monday 27, Boxing Day on Tuesday 28
        days |= {date(year, 12, 27), date(year, 12, 28)}
    elif xmas.weekday() == 6:                     # Sunday: Boxing Day on Monday 26, Christmas on Tuesday 27
        days |= {date(year, 12, 26), date(year, 12, 27)}
    elif xmas.weekday() == 4:                     # Friday: Boxing Day (Saturday 26) on Monday 28
        days |= {xmas, date(year, 12, 28)}
    else:
        days |= {xmas, date(year, 12, 26)}
    moved = GBLO_MOVED.get(year, {})
    days = {moved.get(d, d) for d in days}
    return sorted(days | GBLO_ONE_OFF.get(year, frozenset()))


def _usgs_rule(year: int) -> List[date]:
    days = set(_usny_rule(year))
    gf = easter(year) - timedelta(days=2)
    if year not in USGS_GOOD_FRIDAY_OPEN:
        days.add(gf)
    for month, day in USGS_SATURDAY_TO_FRIDAY:          # SIFMA closes the Friday before these holidays when they fall on a Saturday
        if (month, day) == (6, 19) and year < 2022:
            continue
        d = date(year, month, day)
        if d.weekday() == 5:
            days.add(d - timedelta(days=1))
    days |= USGS_ONE_OFF.get(year, frozenset())
    days -= USGS_OPEN_ONE_OFF.get(year, frozenset())
    return sorted(days)


def _by_year(dates: Iterable[str]) -> Dict[int, FrozenSet[date]]:
    out: Dict[int, set] = {}
    for s in dates:
        d = date.fromisoformat(s)
        out.setdefault(d.year, set()).add(d)
    return {y: frozenset(v) for y, v in out.items()}


#: TARGET one-off closing days (hand-maintained): 31 December 2001, the euro cash changeover
TARGET_ONE_OFF: Dict[int, FrozenSet[date]] = _by_year(["2001-12-31"])
#: England and Wales one-off bank holidays (hand-maintained; none known for 2026-2030): the Golden Jubilee (2002), the royal wedding
#: (2011), the Diamond Jubilee (2012), VE Day 75 (2020), the Platinum Jubilee and the state funeral (2022), the coronation (2023)
GBLO_ONE_OFF: Dict[int, FrozenSet[date]] = _by_year(["2002-06-03", "2011-04-29", "2012-06-05", "2022-06-03", "2022-09-19", "2023-05-08"])
#: England and Wales holidays moved in those years (rule date -> the day it moved to): the spring bank holiday in 2002, 2012 and
#: 2022, the early-May bank holiday in 2020
GBLO_MOVED: Dict[int, Dict[date, date]] = {
    2002: {date(2002, 5, 27): date(2002, 6, 4)}, 2012: {date(2012, 5, 28): date(2012, 6, 4)},
    2020: {date(2020, 5, 4): date(2020, 5, 8)}, 2022: {date(2022, 5, 30): date(2022, 6, 2)}}
#: SIFMA one-off full closes of the US bond market (hand-maintained): 11-12 September 2001, Hurricane Sandy (30 October 2012) and the
#: national day of mourning for President Bush (5 December 2018; SOFR was not published).  President Carter's day (9 January 2025) was an
#: early close only (SOFR was published).
USGS_ONE_OFF: Dict[int, FrozenSet[date]] = _by_year(["2001-09-11", "2001-09-12", "2012-10-30", "2018-12-05"])
#: New York holidays on which SIFMA nevertheless recommended trading (hand-maintained; none known)
USGS_OPEN_ONE_OFF: Dict[int, FrozenSet[date]] = {}
#: (month, day) of the holidays SIFMA closes on the Friday before when they fall on a Saturday: Independence Day and Christmas Day (no SOFR
#: on 2020-07-03, 2021-12-24 and 2026-07-03) and Juneteenth (from 2022; ASSUMED by the same practice - no Saturday Juneteenth yet in the
#: SOFR record).  New Year's Day and Veterans Day on a Saturday close nothing (SOFR was published on 2021-12-31 and 2023-11-10).
USGS_SATURDAY_TO_FRIDAY: Tuple[Tuple[int, int], ...] = ((7, 4), (12, 25), (6, 19))
#: years in which SIFMA recommended only an EARLY close on Good Friday, so SOFR was published that day (hand-maintained).  None in the
#: SOFR record (2018-04-03 .. 2026-10-27): no SOFR was published on any Good Friday, 2021-04-02 and 2023-04-07 included.
USGS_GOOD_FRIDAY_OPEN: FrozenSet[int] = frozenset()


# ---- calendars -------------------------------------------------------------------------------------------------------------------
class Calendar:
    """A rule-based holiday calendar (business day = Monday to Friday and not a holiday of the year)."""

    def __init__(self, code: str, name: str, short: str, rule: Callable[[int], List[date]], *, limited: bool = False, note: str = ""):
        self.code, self.name, self.short, self.limited, self.note = code, name, short, bool(limited), note
        self._rule = rule
        self._cache: Dict[int, FrozenSet[date]] = {}
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"<Calendar {self.code}>"

    def _year(self, year: int) -> FrozenSet[date]:
        with self._lock:
            got = self._cache.get(year)
        if got is None:
            got = frozenset(self._rule(int(year)))
            with self._lock:
                self._cache[year] = got
        return got

    # ---- the K1 interface -----------------------------------------------------------------------------------------------
    def holidays(self, year: int) -> List[date]:
        return sorted(self._year(int(year)))

    def is_holiday(self, d) -> bool:
        d = _as_date(d)
        return d in self._year(d.year)

    def is_business_day(self, d) -> bool:
        d = _as_date(d)
        return d.weekday() < 5 and not self.is_holiday(d)

    def next_business_day(self, d) -> date:
        n = _as_date(d) + timedelta(days=1)
        while not self.is_business_day(n):
            n += timedelta(days=1)
        return n

    def prev_business_day(self, d) -> date:
        n = _as_date(d) - timedelta(days=1)
        while not self.is_business_day(n):
            n -= timedelta(days=1)
        return n

    def roll_following(self, d) -> date:
        d = _as_date(d)
        while not self.is_business_day(d):
            d += timedelta(days=1)
        return d

    def roll_back(self, d) -> date:
        """Preceding: the last business day on or before ``d``."""
        d = _as_date(d)
        while not self.is_business_day(d):
            d -= timedelta(days=1)
        return d

    roll_preceding = roll_back

    def modified_following(self, d) -> date:
        """Following, unless that crosses into the next month: then preceding."""
        d = _as_date(d)
        f = self.roll_following(d)
        return f if f.month == d.month else self.roll_back(d)

    def add_business_days(self, d, n: int) -> date:
        cur = _as_date(d)
        for _ in range(abs(int(n))):
            cur = self.next_business_day(cur) if n > 0 else self.prev_business_day(cur)
        return cur

    def month_end(self, d) -> date:
        """The last business day of ``d``'s month."""
        d = _as_date(d)
        return self.roll_back(date(d.year, d.month, _pycal.monthrange(d.year, d.month)[1]))

    def business_days_between(self, start, end) -> int:
        """The number of business days in (start, end]."""
        start, end = _as_date(start), _as_date(end)
        n, cur = 0, start
        while cur < end:
            cur += timedelta(days=1)
            if self.is_business_day(cur):
                n += 1
        return n

    def why_closed(self, d) -> Optional[str]:
        """None on a business day, else "a Saturday" / "a Sunday" / "a holiday"."""
        d = _as_date(d)
        if d.weekday() >= 5:
            return f"a {WEEKDAYS[d.weekday()]}"
        return "a holiday" if self.is_holiday(d) else None

    def parts(self) -> Tuple["Calendar", ...]:
        return (self,)

    def to_dict(self) -> dict:
        return {"code": self.code, "name": self.name, "short": self.short, "limited": self.limited, "note": self.note,
                "parts": [c.code for c in self.parts()]}


class JointCalendar(Calendar):
    """Several calendars at once: a business day of every one of them (``joint``)."""

    def __init__(self, cals: Sequence[Calendar]):
        flat: List[Calendar] = []
        for c in cals:
            for p in c.parts():
                if all(p is not q for q in flat):
                    flat.append(p)
        self._parts = tuple(flat)
        code = "+".join(c.code for c in flat)
        name = " + ".join(c.name for c in flat)
        short = " + ".join(c.short for c in flat)
        notes = "; ".join(dict.fromkeys(c.note for c in flat if c.note))
        super().__init__(code, name, short, lambda y: sorted(set().union(*(set(c.holidays(y)) for c in flat))) if flat else [],
                         limited=any(c.limited for c in flat), note=notes)

    def is_business_day(self, d) -> bool:
        d = _as_date(d)
        return d.weekday() < 5 and all(c.is_business_day(d) for c in self._parts)

    def is_holiday(self, d) -> bool:
        d = _as_date(d)
        return any(c.is_holiday(d) for c in self._parts)

    def parts(self) -> Tuple[Calendar, ...]:
        return self._parts


class LegacyCalendar(Calendar):
    """The NYSE trading calendar (``services.cal``) for a currency this module has no calendar for: ``limited``, labelled."""

    def __init__(self, cal, ccy: str = ""):
        self._cal = cal
        label = f"NYSE calendar ({ccy} holidays not modelled)" if ccy else "NYSE calendar"
        super().__init__("NYSE", label, "NYSE", lambda y: list(cal.holidays(y)) if hasattr(cal, "holidays") else [], limited=True,
                         note=LIMITED_NOTE if not ccy else f"local holidays not modelled: {ccy} holidays are not in this terminal; value dates use the NYSE calendar")

    def is_business_day(self, d) -> bool:
        return bool(self._cal.is_business_day(_as_date(d)))

    def is_holiday(self, d) -> bool:
        d = _as_date(d)
        return d.weekday() < 5 and not self._cal.is_business_day(d)


TARGET = Calendar("TARGET", "TARGET2", "TARGET2", _target_rule,
                  note="TARGET2 closing days: New Year's Day, Good Friday, Easter Monday, 1 May, 25 and 26 December")
USNY = Calendar("USNY", "New York (Federal Reserve holidays)", "New York", _usny_rule,
                note="Federal Reserve holidays: a Sunday holiday is observed on the Monday, a Saturday holiday is not observed")
GBLO = Calendar("GBLO", "London (England and Wales bank holidays)", "London", _gblo_rule,
                note=("England and Wales bank holidays with the substitute-day rules; one-off holidays (jubilees, funerals) come from a "
                      "hand-maintained list - none is known for 2026-2030"))
USGS = Calendar("USGS", "US government securities business days (SIFMA)", "SIFMA", _usgs_rule,
                note=("New York holidays plus Good Friday and SIFMA's one-off closes; the SIFMA lists (one-off closes, early-close-only Good "
                      "Fridays) are hand-maintained"))
CALENDARS: Dict[str, Calendar] = {"TARGET": TARGET, "USNY": USNY, "GBLO": GBLO, "USGS": USGS}
CCY_CALENDAR: Dict[str, Calendar] = {"USD": USNY, "EUR": TARGET, "GBP": GBLO}
_LEGACY_LOCK = threading.Lock()
_LEGACY_DEFAULT: Dict[str, object] = {}


def for_ccy(ccy) -> Optional[Calendar]:
    """The settlement calendar of USD (New York), EUR (TARGET2) or GBP (London); None for any other currency."""
    return CCY_CALENDAR.get(str(ccy or "").upper())


def has_calendars(pair: str) -> bool:
    """True when both currencies of ``pair`` have a calendar here (USD / EUR / GBP)."""
    p = str(pair or "").upper()
    return len(p) == 6 and for_ccy(p[:3]) is not None and for_ccy(p[3:]) is not None


def joint(*cals: Calendar) -> Calendar:
    """The joint calendar: a business day of every calendar given."""
    cals = tuple(c for c in cals if c is not None)
    if len(cals) == 1:
        return cals[0]
    return JointCalendar(cals)


def _nyse(services):
    cal = getattr(services, "cal", None) if services is not None else None
    if cal is not None:
        return cal
    with _LEGACY_LOCK:
        got = _LEGACY_DEFAULT.get("cal")
        if got is None:
            from . import config
            from .calendar import TradingCalendar
            got = TradingCalendar(config.holidays_path())
            _LEGACY_DEFAULT["cal"] = got
    return got


def legacy(services, ccy: str = "") -> Calendar:
    """The NYSE calendar of ``services.cal`` as a ``Calendar`` (``limited``: "local holidays not modelled")."""
    return LegacyCalendar(_nyse(services), ccy)


def _split(pair: str) -> Tuple[str, str]:
    p = str(pair or "").upper()
    if len(p) != 6 or not p.isalpha():
        raise RejectError("PAIR", f"a currency pair is six letters such as EURUSD, got {pair!r}", {"field": "pair"})
    return p[:3], p[3:]


def value_calendar(services, pair: str) -> Calendar:
    """The calendar a value date of ``pair`` must be good in: both currencies for a USD pair, both currencies and New York for a
    cross; the NYSE calendar (``limited``) for a pair with a currency outside USD / EUR / GBP."""
    base, quote = _split(pair)
    cb, cq = for_ccy(base), for_ccy(quote)
    if cb is None or cq is None:
        missing = base if cb is None else quote
        return legacy(services, missing)
    if "USD" in (base, quote):
        return joint(cb, cq)
    return joint(cb, cq, USNY)


def _spot_days(pair: str) -> int:
    from .instruments import fx_spot_days
    return int(fx_spot_days(pair))


def spot_date(services, pair: str, trade_date) -> date:
    """The spot date of ``pair`` for ``trade_date`` (D10; module docstring)."""
    base, quote = _split(pair)
    td = _as_date(trade_date)
    n = _spot_days(base + quote)
    if not has_calendars(base + quote):
        return _nyse(services).settlement_date(td, n)
    vcal = value_calendar(services, base + quote)
    if "USD" in (base, quote):
        first = for_ccy(quote if base == "USD" else base)       # T+1: a business day of the non-USD currency only
    else:
        first = joint(for_ccy(base), for_ccy(quote))           # a cross: T+1 is a business day of both currencies
    d = first.next_business_day(td) if n >= 1 else vcal.roll_following(td)
    if n == 1:
        return vcal.roll_following(d)
    for _ in range(n - 1):
        d = vcal.next_business_day(d)
    return d


def tenor_date(services, pair: str, tenor: str, trade_date) -> date:
    """A tenor's value date counted from the spot date (D10; module docstring).  400 TENOR for an unknown tenor."""
    t = str(tenor or "").strip().upper()
    base, quote = _split(pair)
    sd = spot_date(services, base + quote, trade_date)
    if t not in TENORS:
        raise RejectError("TENOR", f"tenor {tenor!r}: use one of {', '.join(TENORS)} or a value date", {"field": "tenor", "tenors": list(TENORS)})
    if not has_calendars(base + quote):
        cal = _nyse(services)
        vcal: Calendar = legacy(services)
    else:
        vcal = value_calendar(services, base + quote)
        cal = vcal
    if t == "ON":
        return cal.add_business_days(sd, 1)
    if t in TENOR_WEEKS:
        return cal.roll_following(sd + timedelta(days=7 * TENOR_WEEKS[t]))
    target = add_months(sd, TENOR_MONTHS[t])
    if sd == vcal.month_end(sd):
        return vcal.month_end(target)
    return vcal.modified_following(target)


def value_date_problem(services, pair: str, value_date, deliver_ccy: Optional[str] = None) -> Optional[str]:
    """None when ``value_date`` is a business day of the pair's value calendar, else the reason in trader words, naming the
    calendar: "not a business day in EUR (TARGET2)", "not a business day in USD (New York): the EUR is bought against USD for the same
    value date" (a cross; ``deliver_ccy`` names the currency bought, default the base), "2026-11-28 is a Saturday: not a value date"."""
    base, quote = _split(pair)
    d = _as_date(value_date)
    if d.weekday() >= 5:
        return f"{d.isoformat()} is a {WEEKDAYS[d.weekday()]}: not a value date"
    if not has_calendars(base + quote):
        cal = _nyse(services)
        if not cal.is_business_day(d):
            return f"not a business day on the NYSE calendar ({LIMITED_NOTE})"
        return None
    for ccy in (base, quote):
        c = for_ccy(ccy)
        if not c.is_business_day(d):
            return f"not a business day in {ccy} ({c.short})"
    if "USD" not in (base, quote) and not USNY.is_business_day(d):
        bought = str(deliver_ccy or base).upper()
        return f"not a business day in USD ({USNY.short}): the {bought} is bought against USD for the same value date"
    return None


def require_value_date(services, pair: str, value_date, deliver_ccy: Optional[str] = None) -> date:
    """``value_date`` when it is a business day of the pair's value calendar, else 400 VALUE_DATE naming the calendar
    (``details.field`` = "value_date", ``details.calendar``)."""
    why = value_date_problem(services, pair, value_date, deliver_ccy)
    d = _as_date(value_date)
    if why is not None:
        vcal = value_calendar(services, pair)
        raise RejectError("VALUE_DATE", f"{str(pair).upper()} value date {d.isoformat()}: {why}",
                          {"field": "value_date", "value_date": d.isoformat(), "calendar": vcal.name, "pair": str(pair).upper()})
    return d


def schedule(cal: Calendar, anchor, months: int, step_months: int, pay_delay_bd: int = 0, sofr: bool = False) -> List[Tuple[date, date, date]]:
    """``[(s, e, p)]`` for k = 1 .. months / step_months: e_k = MF(anchor + k x step months) on ``cal``, s_1 = anchor, s_k = e_(k-1),
    p_k = e_k + ``pay_delay_bd`` business days of ``cal``.  With ``sofr`` a start or end that is not a USGS day is 400 SCHEDULE."""
    a = _as_date(anchor)
    months, step = int(months), int(step_months)
    if step <= 0 or months <= 0 or months % step:
        raise RejectError("SCHEDULE", f"{months} months is not a whole number of {step}-month periods", {"months": months, "step_months": step})
    out: List[Tuple[date, date, date]] = []
    s = a
    for k in range(1, months // step + 1):
        e = cal.modified_following(add_months(a, k * step))
        p = cal.add_business_days(e, int(pay_delay_bd)) if pay_delay_bd else e
        out.append((s, e, p))
        s = e
    if sofr:
        for s_, e_, _p in out:
            for d in (s_, e_):
                if not USGS.is_business_day(d):
                    raise RejectError("SCHEDULE", f"SOFR is not published on {d.isoformat()} (SIFMA close): a SOFR period cannot start or end on it",
                                      {"date": d.isoformat(), "calendar": USGS.name})
    return out


def describe(services, pair: str) -> dict:
    """The value calendar of a pair for a ticket or a record: ``{pair, calendar, short, parts, limited, note}``."""
    vcal = value_calendar(services, pair)
    return {"pair": str(pair).upper(), "calendar": vcal.name, "short": vcal.short, "parts": [c.code for c in vcal.parts()], "limited": vcal.limited,
            "note": vcal.note}


__all__ = ["Calendar", "JointCalendar", "LegacyCalendar", "TARGET", "USNY", "GBLO", "USGS", "CALENDARS", "CCY_CALENDAR", "OTC_TENORS", "TENORS",
           "TENOR_MONTHS", "TENOR_WEEKS", "LIMITED_NOTE", "YEAR_MIN", "YEAR_MAX", "TARGET_ONE_OFF", "GBLO_ONE_OFF", "GBLO_MOVED", "USGS_ONE_OFF",
           "USGS_OPEN_ONE_OFF", "USGS_GOOD_FRIDAY_OPEN", "easter", "nth_weekday", "last_weekday", "add_months", "for_ccy", "has_calendars", "joint",
           "legacy", "value_calendar", "spot_date", "tenor_date", "value_date_problem", "require_value_date", "schedule", "describe"]
