"""One business-day calendar for everything, and point-in-time alignment of prices and macro series onto it.

The calendar is the set of dates on which the benchmark (SPY) traded. Price series are forward-filled onto it for at
most `MAX_FILL` sessions (a holiday abroad, a weekend for crypto) and are missing beyond that. Macro observations are
placed on the date they became public and carried forward until the next one: the real publication date for
first-release vintages (US data is released before the close, so it is usable that day), otherwise observation
date + the series' fixed publication lag. Series that are re-estimated wholesale (NFCI) are unusable without
vintages and read as missing.
"""
from __future__ import annotations

import bisect
import math
import threading
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

MAX_FILL = 5
BENCHMARK = "SPY"
# Re-estimated over their whole history every release: latest-vintage values are pure look-ahead, so these series
# are used only when stored as first-release vintages.
VINTAGE_ONLY = frozenset({"NFCI"})
# Guard: should FRED return the earliest vintage it has for observations older than its vintage coverage (all
# "published" on the first vintage date; the series here return nothing before coverage instead), such an observation
# (published on that date, more than lag + this many days after its observation date) falls back to the fixed lag,
# except for VINTAGE_ONLY series (visible only from the vintage date).
PRE_VINTAGE_MARGIN_DAYS = 60


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
    def macro_kind(self, series_id: str) -> Optional[str]:
        """"first_release", "latest_vintage" or None (unknown) - what the store holds for `series_id`."""
        key = ("kind", series_id)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        getter = getattr(self.store, "macro_kind", None)
        kind = getter(series_id) if getter else None
        with self._lock:
            self._cache[key] = kind
        return kind

    def macro_usable(self, series_id: str) -> bool:
        """False for a VINTAGE_ONLY series (NFCI) that is not stored as first-release vintages."""
        return series_id not in VINTAGE_ONLY or self.macro_kind(series_id) == "first_release"

    def _macro_rows(self, series_id: str) -> List[Tuple[str, float, Optional[str]]]:
        rows_fn = getattr(self.store, "macro_rows", None)
        rows = rows_fn(series_id) if rows_fn else [(d, v, None) for d, v in self.store.macro(series_id)]
        return sorted((d, float(v), p) for d, v, p in rows if v is not None)

    def macro(self, series_id: str, lag_days: Optional[int] = None, transform=None, max_age_days: int = 400) -> List[Optional[float]]:
        """A FRED series on the calendar as it was known on each date.

        An observation becomes visible on the first calendar date >= its publication date when the store has one
        (first-release vintages), else on observation date + lag. Visibility is cumulative in observation order (an
        observation counts as visible only once every earlier one is), so `transform(obs_list) -> obs_list`, which
        runs on the observation sequence (e.g. year-on-year) and only looks back, uses only observations visible on
        the date its output is shown. A VINTAGE_ONLY series without vintages is all None."""
        from ..data.universe import FRED_SERIES
        lag = lag_days if lag_days is not None else int((FRED_SERIES.get(series_id) or {}).get("lag_days", 1))
        key = ("macro", series_id, lag, getattr(transform, "__name__", None), max_age_days)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        cal = self.calendar()
        out: List[Optional[float]] = [None] * len(cal)
        if not self.macro_usable(series_id):
            with self._lock:
                self._cache[key] = out
            return out
        rows = self._macro_rows(series_id)
        first_vintage = min((p for _d, _v, p in rows if p), default=None)
        visible: Dict[str, str] = {}
        run = None
        for d, _v, p in rows:
            if p is None or (series_id not in VINTAGE_ONLY and p == first_vintage
                             and (date.fromisoformat(p) - date.fromisoformat(d)).days > lag + PRE_VINTAGE_MARGIN_DAYS):
                when = _iso_add(d, lag)
            else:
                when = p
            run = when if run is None or when > run else run
            visible[d] = run
        obs = [(d, v) for d, v, _p in rows]
        if transform is not None:
            obs = transform(obs)
        avail = sorted((visible[d], d, v) for d, v in obs if v is not None and d in visible)
        j = -1
        best = None  # (observation date, value) of the latest observation visible so far
        for i, d in enumerate(cal):
            while j + 1 < len(avail) and avail[j + 1][0] <= d:
                j += 1
                if best is None or avail[j][1] >= best[0]:
                    best = (avail[j][1], avail[j][2])
            if best is not None and (date.fromisoformat(d) - date.fromisoformat(best[0])).days <= max_age_days:
                out[i] = best[1]
        with self._lock:
            self._cache[key] = out
        return out

    def data_notes(self) -> List[str]:
        """Plain-language statements of what kind of macro data the evidence rests on (for the research bundle)."""
        from ..data.universe import FRED_SERIES
        revised = [s for s, info in FRED_SERIES.items() if info.get("freq") != "daily"]
        getter = getattr(self.store, "last_macro_date", None)
        stored = [s for s in revised if getter is None or getter(s)]
        first = [s for s in stored if self.macro_kind(s) == "first_release"]
        latest = [s for s in stored if s not in first]
        notes = []
        if first:
            since = []
            for sid in first:
                rows = self._macro_rows(sid)
                since.append(f"{sid} (from {rows[0][0][:7]})" if rows else sid)
            notes.append("Macro " + ", ".join(since) + ": first-release values (FRED/ALFRED vintages), each used from "
                         "its actual publication date, so later revisions never reach the past. History starts where "
                         "FRED's vintage coverage starts; earlier dates have no value.")
        if latest:
            notes.append("Macro " + ", ".join(latest) + ": latest revised values (no FRED_API_KEY, so no vintages), "
                         "used after a fixed publication lag - later revisions leak into history.")
        if not self.macro_usable("NFCI"):
            notes.append("NFCI is excluded without first-release vintages (it is re-estimated weekly): the financial "
                         "conditions feature and the liquidity regime are unavailable.")
        notes.append("Daily market series (Treasury yields, spreads, breakevens, VIX, dollar) are not revised in "
                     "practice and are used from the day after each observation.")
        return notes


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
