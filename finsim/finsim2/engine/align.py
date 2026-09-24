"""One business-day calendar for everything, and point-in-time alignment of prices and macro series onto it.

The calendar is the set of dates on which the benchmark (SPY) traded. Price series are forward-filled onto it for at
most `MAX_FILL` sessions (a holiday abroad, a weekend for crypto) and are missing beyond that. Macro observations are
placed on the date they became public (observation date + publication lag) and carried forward until the next one.
"""
from __future__ import annotations

import bisect
import math
import threading
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

MAX_FILL = 5
BENCHMARK = "SPY"


def _iso_add(iso: str, days: int) -> str:
    return (date.fromisoformat(iso) + timedelta(days=days)).isoformat()


class Panel:
    """Aligned access to the research store. Cheap to create; caches aligned series per instance."""

    def __init__(self, store, benchmark: str = BENCHMARK):
        self.store = store
        self.benchmark = benchmark
        self._cal: Optional[List[str]] = None
        self._pos: Dict[str, int] = {}
        self._cache: Dict[Tuple, object] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ calendar
    def calendar(self) -> List[str]:
        if self._cal is None:
            rows = self.store.prices(self.benchmark)
            cal = [r["date"] for r in rows]
            if not cal:
                raise LookupError(f"no {self.benchmark} history in the store: run a data refresh first")
            self._cal = cal
            self._pos = {d: i for i, d in enumerate(cal)}
        return self._cal

    def index_of(self, iso: str) -> int:
        """Position of the last calendar date <= iso (or 0)."""
        cal = self.calendar()
        return max(0, bisect.bisect_right(cal, iso) - 1)

    # ------------------------------------------------------------------ prices
    def _price_rows(self, asset_id: str) -> List[dict]:
        key = ("rows", asset_id)
        with self._lock:
            if key not in self._cache:
                self._cache[key] = self.store.prices(asset_id)
            return self._cache[key]

    def series(self, asset_id: str, field: str = "adj_close", max_fill: int = MAX_FILL) -> List[Optional[float]]:
        """`field` of `asset_id` on the calendar: exact date, else the last value within `max_fill` sessions."""
        key = ("series", asset_id, field, max_fill)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        cal = self.calendar()
        rows = self._price_rows(asset_id)
        dates = [r["date"] for r in rows]
        vals = [r.get(field) if r.get(field) is not None else (r.get("close") if field == "adj_close" else None) for r in rows]
        out: List[Optional[float]] = [None] * len(cal)
        j = -1
        last_i = -10 ** 9
        last_v = None
        for i, d in enumerate(cal):
            while j + 1 < len(dates) and dates[j + 1] <= d:
                j += 1
                if vals[j] is not None and not (isinstance(vals[j], float) and math.isnan(vals[j])):
                    last_v = float(vals[j])
                    last_i = i
            if last_v is not None and i - last_i <= max_fill:
                out[i] = last_v
        with self._lock:
            self._cache[key] = out
        return out

    def first_index(self, asset_id: str) -> Optional[int]:
        s = self.series(asset_id)
        return next((i for i, v in enumerate(s) if v is not None), None)

    # ------------------------------------------------------------------ macro (point in time)
    def macro(self, series_id: str, lag_days: Optional[int] = None, transform=None, max_age_days: int = 400) -> List[Optional[float]]:
        """A FRED series on the calendar as it was known on each date: an observation dated d is usable from
        d + lag. `transform(obs_list) -> obs_list` runs on the observation dates first (e.g. year-on-year)."""
        from ..data.universe import FRED_SERIES
        lag = lag_days if lag_days is not None else int((FRED_SERIES.get(series_id) or {}).get("lag_days", 1))
        key = ("macro", series_id, lag, getattr(transform, "__name__", None), max_age_days)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        obs = [(d, float(v)) for d, v in self.store.macro(series_id) if v is not None]
        if transform is not None:
            obs = transform(obs)
        avail = sorted((_iso_add(d, lag), v, d) for d, v in obs if v is not None)
        cal = self.calendar()
        out: List[Optional[float]] = [None] * len(cal)
        j = -1
        for i, d in enumerate(cal):
            while j + 1 < len(avail) and avail[j + 1][0] <= d:
                j += 1
            if j >= 0 and (date.fromisoformat(d) - date.fromisoformat(avail[j][2])).days <= max_age_days:
                out[i] = avail[j][1]
        with self._lock:
            self._cache[key] = out
        return out


# ------------------------------------------------------------------ transforms on observation lists
def yoy_log(obs: Sequence[Tuple[str, float]]) -> List[Tuple[str, float]]:
    """Year-on-year log change of a monthly (or finer) series, on its own observation dates."""
    out = []
    dates = [d for d, _ in obs]
    for i, (d, v) in enumerate(obs):
        target = _iso_add(d, -365)
        j = bisect.bisect_right(dates, target) - 1
        if j >= 0 and v > 0 and obs[j][1] > 0 and (date.fromisoformat(d) - date.fromisoformat(dates[j])).days <= 400:
            out.append((d, math.log(v / obs[j][1])))
    return out


def sahm_gap(obs: Sequence[Tuple[str, float]]) -> List[Tuple[str, float]]:
    """Sahm-style gap: 3-observation average unemployment minus its low over the prior 12 observations."""
    vals = [v for _, v in obs]
    avg3 = [sum(vals[max(0, i - 2):i + 1]) / len(vals[max(0, i - 2):i + 1]) for i in range(len(vals))]
    out = []
    for i, (d, _) in enumerate(obs):
        if i >= 12:
            out.append((d, avg3[i] - min(avg3[i - 12:i])))
    return out


def growth_26w(obs: Sequence[Tuple[str, float]]) -> List[Tuple[str, float]]:
    """Log growth over 26 weeks (182 days) of a weekly series."""
    out = []
    dates = [d for d, _ in obs]
    for d, v in obs:
        j = bisect.bisect_right(dates, _iso_add(d, -182)) - 1
        if j >= 0 and v > 0 and obs[j][1] > 0:
            out.append((d, math.log(v / obs[j][1])))
    return out
