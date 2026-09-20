"""
ShafferFinEval -- macro and non-equity market data.

Retrieval only; no scoring. Two sources, both already available to this
project:

    FRED   (FRED_API_KEY)  yields, inflation, policy rate, labour
    Yahoo  (no key)        FX spot, rate indices, commodity futures, VIX

Wiring rates has the most leverage in the asset-class ordering: a live rates
engine feeds Treasuries, Treasury futures, IRS and swaptions at once. FX
unlocks forwards and NDFs; oil unlocks crude futures and options.

Anything a source does not supply is returned as None so the scoring engine
drops the factor and renormalizes. Nothing here invents a value.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
from dataclasses import dataclass, field
from typing import Optional

import requests

from market_data import BROWSER_UA, HTTP_TIMEOUT, YAHOO_HOSTS, _get_json, _num

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

#: FRED series the engines use. Keyed by our own name so a series swap is a
#: one-line change.
FRED_SERIES = {
    "ust_10y": "DGS10",            # 10-year Treasury constant maturity
    "ust_2y": "DGS2",              # 2-year
    "ust_3m": "DTB3",              # 3-month bill
    "ust_30y": "DGS30",
    "real_10y": "DFII10",          # 10-year TIPS -- the real yield
    "breakeven_10y": "T10YIE",     # 10-year inflation expectation
    "fed_funds": "DFF",            # effective fed funds
    "cpi": "CPIAUCSL",             # headline CPI index
    "core_pce": "PCEPILFE",
    "unemployment": "UNRATE",
    "payrolls": "PAYEMS",
    "dollar_index": "DTWEXBGS",    # broad trade-weighted dollar
    "hy_oas": "BAMLH0A0HYM2",      # high-yield OAS
    "ig_oas": "BAMLC0A0CM",        # investment-grade OAS
    "mortgage_30y": "MORTGAGE30US",
}

#: Yahoo symbols for instruments FRED does not carry.
YAHOO_MACRO = {
    "vix": "^VIX", "vix_3m": "^VIX3M", "tnx": "^TNX", "irx": "^IRX",
    "wti": "CL=F", "brent": "BZ=F", "natgas": "NG=F", "gold": "GC=F",
    "silver": "SI=F", "copper": "HG=F", "corn": "ZC=F", "wheat": "ZW=F",
    "soybeans": "ZS=F", "dxy": "DX-Y.NYB",
}


@dataclass
class Series:
    """One macro series with its observations, newest last."""

    key: str
    source: str
    identifier: str
    dates: list = field(default_factory=list)
    values: list = field(default_factory=list)
    note: str = ""

    @property
    def latest(self) -> Optional[float]:
        return self.values[-1] if self.values else None

    @property
    def available(self) -> bool:
        return bool(self.values)

    def change(self, periods: int = 1) -> Optional[float]:
        if len(self.values) <= periods:
            return None
        return self.values[-1] - self.values[-1 - periods]

    def pct_change(self, periods: int = 1) -> Optional[float]:
        if len(self.values) <= periods:
            return None
        base = self.values[-1 - periods]
        if not base:
            return None
        return self.values[-1] / base - 1.0

    def history(self, n: Optional[int] = None) -> list:
        return self.values[-n:] if n else list(self.values)


def _fred_key() -> Optional[str]:
    return os.environ.get("FRED_API_KEY") or None


def _scrub(text) -> str:
    """Remove the API key from anything that might be stored or displayed.

    A `requests` exception carries the full request URL, and that URL carries
    the key. Notes end up in the database and on screen, so the key is
    redacted before it can travel anywhere.
    """
    message = str(text)
    key = _fred_key()
    if key:
        message = message.replace(key, "[REDACTED]")
    return re.sub(r"(api_key=)[^&\s]+", r"\1[REDACTED]", message)


def fetch_fred(key: str, observations: int = 520) -> Series:
    """One FRED series. Returns an empty Series when unavailable."""
    series_id = FRED_SERIES.get(key, key)
    result = Series(key=key, source="FRED", identifier=series_id)

    api_key = _fred_key()
    if not api_key:
        result.note = "FRED_API_KEY is not set, so macro series are unavailable."
        return result

    try:
        response = requests.get(
            FRED_BASE,
            params={"series_id": series_id, "api_key": api_key,
                    "file_type": "json", "sort_order": "desc",
                    "limit": observations},
            timeout=HTTP_TIMEOUT,
        )
    except requests.RequestException as exc:
        result.note = _scrub(f"FRED request failed: {exc}")
        return result

    if response.status_code != 200:
        result.note = f"FRED returned HTTP {response.status_code} for {series_id}."
        return result

    try:
        payload = response.json()
    except ValueError as exc:
        result.note = _scrub(f"FRED returned unparseable JSON: {exc}")
        return result

    rows = []
    for observation in payload.get("observations") or []:
        value = _num(observation.get("value"))
        if value is None:
            continue                       # FRED writes "." for missing
        try:
            day = _dt.date.fromisoformat(observation["date"])
        except (KeyError, TypeError, ValueError):
            continue
        rows.append((day, value))

    rows.sort()
    result.dates = [d for d, _ in rows]
    result.values = [v for _, v in rows]
    if not rows:
        result.note = f"FRED returned no usable observations for {series_id}."
    return result


def fetch_yahoo_series(key: str, range_: str = "1y") -> Series:
    """A Yahoo price series for a macro instrument."""
    symbol = YAHOO_MACRO.get(key, key)
    result = Series(key=key, source="Yahoo", identifier=symbol)

    payload = _get_json(f"v8/finance/chart/{symbol}",
                        {"range": range_, "interval": "1d"})
    if not payload:
        result.note = f"Yahoo returned no data for {symbol}."
        return result
    try:
        block = payload["chart"]["result"][0]
    except (KeyError, IndexError, TypeError):
        result.note = f"Yahoo returned no chart for {symbol}."
        return result

    stamps = block.get("timestamp") or []
    indicators = block.get("indicators") or {}
    closes = None
    if indicators.get("adjclose"):
        closes = (indicators["adjclose"][0] or {}).get("adjclose")
    if not closes and indicators.get("quote"):
        closes = (indicators["quote"][0] or {}).get("close")
    if not closes:
        result.note = f"Yahoo returned no closes for {symbol}."
        return result

    rows = []
    for stamp, value in zip(stamps, closes):
        price = _num(value)
        if price is None:
            continue
        try:
            day = _dt.datetime.fromtimestamp(int(stamp), _dt.timezone.utc).date()
        except (TypeError, ValueError, OSError):
            continue
        rows.append((day, price))
    rows.sort()
    result.dates = [d for d, _ in rows]
    result.values = [v for _, v in rows]
    return result


@dataclass
class MacroSnapshot:
    """Everything the non-equity engines need, fetched once."""

    series: dict = field(default_factory=dict)
    fetched_at: Optional[str] = None
    missing: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def get(self, key: str) -> Optional[Series]:
        found = self.series.get(key)
        return found if found and found.available else None

    def latest(self, key: str) -> Optional[float]:
        found = self.get(key)
        return found.latest if found else None

    def history(self, key: str, n: Optional[int] = None) -> list:
        found = self.get(key)
        return found.history(n) if found else []

    @property
    def coverage(self) -> float:
        if not self.series:
            return 0.0
        return sum(1 for s in self.series.values() if s.available) / len(self.series)


#: The series actually needed by the engines that can go live now.
CORE_SERIES = (
    "ust_10y", "ust_2y", "ust_3m", "real_10y", "breakeven_10y", "fed_funds",
    "cpi", "unemployment", "dollar_index", "hy_oas", "ig_oas",
)
#: `vix_3m` is here because the volatility engine's term-structure factor asks
#: for it; without it that factor could never fill.
CORE_YAHOO = ("vix", "vix_3m", "wti", "natgas", "gold", "copper")


def fetch_macro_snapshot(
    fred_keys=CORE_SERIES, yahoo_keys=CORE_YAHOO
) -> MacroSnapshot:
    """Fetch every macro input once, for a whole refresh run."""
    snapshot = MacroSnapshot(
        fetched_at=_dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"))

    for key in fred_keys:
        series = fetch_fred(key)
        snapshot.series[key] = series
        if not series.available:
            snapshot.missing.append(f"FRED:{key}")
            if series.note:
                snapshot.notes.append(series.note)

    for key in yahoo_keys:
        series = fetch_yahoo_series(key)
        snapshot.series[key] = series
        if not series.available:
            snapshot.missing.append(f"Yahoo:{key}")
            if series.note:
                snapshot.notes.append(series.note)

    return snapshot
