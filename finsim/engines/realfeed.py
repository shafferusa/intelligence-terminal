"""Real-market feed: daily closes from Yahoo Finance for a world that tracks the actual market.

A world created with market_source REAL uses real closes instead of simulated ones: its prehistory is the last
year of real sessions (stored in the save as one event, so replay never touches the network), and every session
it advances into is pinned to that day's real closes for equities, ETFs, ADRs, Treasury yields, commodity front
months and FX. Everything the simulator derives from prices — bonds off the curve, options off the vol surface,
futures off spot, OTC valuations, P&L — follows. Days the market has not closed yet cannot be advanced into.

Source: Yahoo's spark endpoint (many symbols per request, daily closes), browser-style User-Agent as the project
runbook allows for Yahoo. Standard library only. Results are cached under FINSIM_HOME so repeated advances and
several saves share one fetch.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

YAHOO_UA = "Mozilla/5.0"
SPARK = "https://query2.finance.yahoo.com/v7/finance/spark"
BATCH = 20          # Yahoo answers 400 to bigger spark batches
RATE_SYMBOLS = {"bill_13w": "^IRX", "y5": "^FVX", "y10": "^TNX", "y30": "^TYX"}
COMMODITY_SYMBOLS = {"CL": "CL=F", "BRN": "BZ=F", "NG": "NG=F", "RB": "RB=F", "HO": "HO=F", "GC": "GC=F", "SI": "SI=F", "HG": "HG=F", "PL": "PL=F", "PA": "PA=F",
                     "ALI": "ALI=F", "ZC": "ZC=F", "ZW": "ZW=F", "ZS": "ZS=F", "KC": "KC=F", "SB": "SB=F", "CT": "CT=F", "CC": "CC=F", "LE": "LE=F", "GF": "GF=F", "HE": "HE=F"}
CENTS_QUOTED = {"ZC", "ZW", "ZS", "KC", "SB", "CT", "LE", "GF", "HE"}
FX_SYMBOLS = {"EUR": ("EURUSD=X", False), "GBP": ("GBPUSD=X", False), "JPY": ("JPY=X", True), "CHF": ("CHF=X", True), "CAD": ("CAD=X", True), "AUD": ("AUDUSD=X", False)}


def _home() -> str:
    return os.environ.get("FINSIM_HOME") or os.path.join(os.path.expanduser("~"), ".finsim")


class RealFeed:
    """Daily closes by symbol and date, with a disk cache. `equity_symbols` maps security id -> Yahoo symbol."""

    def __init__(self, equity_symbols: Dict[str, str], cache_path: Optional[str] = None, fetch=None):
        self.equities = dict(equity_symbols)
        self.cache_path = cache_path or os.path.join(_home(), "realfeed-cache.json")
        self.data: Dict[str, Dict[str, float]] = {}        # symbol -> {date: close}
        self.fetched_at: Dict[str, float] = {}             # symbol -> epoch of the last fetch
        self._fetch = fetch or self._spark
        self._load_cache()

    # ------------------------------------------------------------------ cache
    def _load_cache(self) -> None:
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                j = json.load(f)
            self.data = j.get("data", {})
            self.fetched_at = j.get("fetched_at", {})
        except (OSError, ValueError):
            self.data, self.fetched_at = {}, {}

    def _save_cache(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump({"data": self.data, "fetched_at": self.fetched_at}, f)
        except OSError:
            pass

    # ------------------------------------------------------------------ fetching
    @staticmethod
    def _spark(symbols: List[str], rng: str) -> Dict[str, Dict[str, float]]:
        out: Dict[str, Dict[str, float]] = {}
        for i in range(0, len(symbols), BATCH):
            chunk = symbols[i:i + BATCH]
            url = f"{SPARK}?symbols={urllib.parse.quote(','.join(chunk))}&range={rng}&interval=1d"
            req = urllib.request.Request(url, headers={"User-Agent": YAHOO_UA})
            j = None
            for attempt in range(4):
                try:
                    with urllib.request.urlopen(req, timeout=30) as r:
                        j = json.loads(r.read().decode("utf-8"))
                    break
                except urllib.error.HTTPError as e:
                    if e.code in (400, 404) and len(chunk) > 1:
                        # one bad symbol fails the whole batch: split and keep the good ones
                        half = len(chunk) // 2
                        out.update(RealFeed._spark(chunk[:half], rng))
                        out.update(RealFeed._spark(chunk[half:], rng))
                        j = {}
                        break
                    if e.code in (400, 404):
                        j = {}
                        break
                    if attempt == 3:
                        raise
                    time.sleep(2.0 * (attempt + 1))
                except Exception:
                    if attempt == 3:
                        raise
                    time.sleep(2.0 * (attempt + 1))
            if not j:
                continue
            for res in j.get("spark", {}).get("result", []):
                sym = res.get("symbol")
                resp = (res.get("response") or [{}])[0]
                ts = resp.get("timestamp") or []
                closes = ((resp.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
                off = (resp.get("meta") or {}).get("gmtoffset", 0) or 0
                series: Dict[str, float] = {}
                for t, c in zip(ts, closes):
                    if c is None:
                        continue
                    d = datetime.fromtimestamp(t + off, tz=timezone.utc).date().isoformat()
                    series[d] = float(c)
                if series:
                    out[sym] = series
            time.sleep(0.5)
        return out

    def all_symbols(self) -> List[str]:
        return sorted(set(list(self.equities.values()) + list(RATE_SYMBOLS.values()) + list(COMMODITY_SYMBOLS.values()) + [s for s, _ in FX_SYMBOLS.values()]))

    def refresh(self, rng: str = "1mo", max_age_s: float = 1800.0, force: bool = False) -> None:
        """Fetch `rng` of closes for every symbol whose cache is older than `max_age_s`."""
        now = time.time()
        need = [s for s in self.all_symbols() if force or now - self.fetched_at.get(s, 0) > max_age_s or s not in self.data]
        if not need:
            return
        got = self._fetch(need, rng)
        for s, series in got.items():
            self.data.setdefault(s, {}).update(series)
            self.fetched_at[s] = now
        for s in need:
            self.fetched_at.setdefault(s, now)
        self._save_cache()

    # ------------------------------------------------------------------ views
    def latest_date(self) -> Optional[str]:
        """The last date the market (SPY) has a close for."""
        spy = self.data.get(self.equities.get("SPY", "SPY"), {})
        return max(spy) if spy else None

    def has(self, d: date) -> bool:
        return d.isoformat() in self.data.get(self.equities.get("SPY", "SPY"), {})

    def _value(self, sym: str, d: str, carry: bool = True) -> Optional[float]:
        s = self.data.get(sym)
        if not s:
            return None
        if d in s:
            return s[d]
        if not carry:
            return None
        earlier = [k for k in s if k < d]
        return s[max(earlier)] if earlier else None

    def targets_for(self, d: date) -> Optional[Dict]:
        """The pins for one session: equities, commodities, FX and rates. None when the market has no close for `d`."""
        iso = d.isoformat()
        if not self.has(d):
            return None
        eq = {sid: v for sid, sym in self.equities.items() if (v := self._value(sym, iso)) is not None}
        cm = {}
        for code, sym in COMMODITY_SYMBOLS.items():
            v = self._value(sym, iso)
            if v is not None:
                cm[code] = v / 100.0 if code in CENTS_QUOTED else v
        fx = {}
        for ccy, (sym, invert) in FX_SYMBOLS.items():
            v = self._value(sym, iso)
            if v:
                fx[ccy] = (1.0 / v) if invert else v
        rates = {k: v / 100.0 for k, sym in RATE_SYMBOLS.items() if (v := self._value(sym, iso)) is not None}
        return {"date": iso, "equities": eq, "commodities": cm, "fx": fx, "rates": rates}

    def history(self, start: date, end: date) -> Dict:
        """Everything between `start` and `end` inclusive, as {section: {key: {date: value}}} — what a save stores."""
        def sl(sym: str, transform=None) -> Dict[str, float]:
            s = self.data.get(sym, {})
            return {k: (transform(v) if transform else v) for k, v in s.items() if start.isoformat() <= k <= end.isoformat()}
        return {"start": start.isoformat(), "end": end.isoformat(),
                "equities": {sid: sl(sym) for sid, sym in self.equities.items() if self.data.get(sym)},
                "commodities": {code: sl(sym, (lambda v, c=code: v / 100.0 if c in CENTS_QUOTED else v)) for code, sym in COMMODITY_SYMBOLS.items() if self.data.get(sym)},
                "fx": {ccy: sl(sym, (lambda v, inv=invert: (1.0 / v) if inv else v)) for ccy, (sym, invert) in FX_SYMBOLS.items() if self.data.get(sym)},
                "rates": {k: sl(sym, lambda v: v / 100.0) for k, sym in RATE_SYMBOLS.items() if self.data.get(sym)}}

    @staticmethod
    def targets_from_history(h: Dict, d: date) -> Optional[Dict]:
        """Pins for `d` out of a stored history block (carrying the last value forward within the block)."""
        iso = d.isoformat()
        spy = h.get("equities", {}).get("SPY", {})
        if iso not in spy:
            return None

        def pick(series: Dict[str, float]) -> Optional[float]:
            if iso in series:
                return series[iso]
            earlier = [k for k in series if k < iso]
            return series[max(earlier)] if earlier else None
        out = {"date": iso}
        for sec in ("equities", "commodities", "fx", "rates"):
            out[sec] = {k: v for k, s in h.get(sec, {}).items() if (v := pick(s)) is not None}
        return out


def equity_symbols_for(securities: Dict) -> Dict[str, str]:
    """Yahoo symbols for the cash equities, ETFs, ADRs, REITs and preferreds in a universe."""
    out = {}
    for sid, sec in securities.items():
        if sec.asset_class in ("EQUITY", "ETF", "ADR", "REIT", "PREFERRED"):
            out[sid] = sid          # tickers match Yahoo's (BRK-B, BAC-PL use the same hyphen form)
    return out
