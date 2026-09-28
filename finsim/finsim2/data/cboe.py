"""Cboe public data (cdn.cboe.com, which redirects to cdn-api.cboe.com). No key; plain GETs, paced. Research only.

1. **Volatility-index history** -> macro series (``store.upsert_macro``) ``CBOE_VIX9D``, ``CBOE_VIX6M``, ``CBOE_VVIX``
   (vol of VIX), ``CBOE_SKEW``: the full daily history CSVs Cboe publishes for its indices
   (``/api/global/us_indices/daily_prices/{INDEX}_History.csv``; columns DATE, OPEN, HIGH, LOW, CLOSE, or DATE plus one
   value column). VIX, VIX3M (= VXV), VXN, RVX, OVX, GVZ … come from FRED (``FRED_SERIES``). A close is public at the
   close, so each value is published the next calendar day.

2. **Daily option-chain snapshots** -> alt_data dataset ``options_cboe`` (the delayed-quotes JSON behind Cboe's own
   quote pages, ``/api/global/delayed_quotes/options/{SYM}.json``, index symbols with a leading underscore), reduced to
   one row of features per underlying and session — the chain itself is not kept:

       iv30 / iv60 / iv90   at-the-money implied volatility (decimal) at constant 30 / 60 / 90 calendar days
                            (call/put average at the spot, total variance interpolated between expiries)
       skew25               25-delta put IV − 25-delta call IV at 30 days (needs Cboe's deltas)
       put_volume, call_volume, put_oi, call_oi                     totals over the whole chain
       iv30_cboe            Cboe's own 30-day IV for the name when the payload carries one (decimal)
       spot, n_contracts    the underlying price and the number of quoted contracts used

   The field names match ``options_summary`` (licensed imports, data/imports.py) so research code reads either, but the
   datasets stay separate: a vendor's IV and this one are computed differently.

Point in time: Cboe's quotes are delayed ~15 minutes, so a snapshot taken after 16:15 New York on a weekday is that
session's close (published the next calendar day). Before 09:30 the chain still shows the previous session and is
stored under that session. During the session nothing is stored: the source reports ``deferred`` and the daily loop tries
again later. History does not exist for free — the series begins on the first run (DATA_NEEDS.md).

Payloads are untrusted: parsed with csv / json, numbers through ``num``; option symbols by a fixed OCC pattern.
"""
from __future__ import annotations

import csv
import datetime as _dt
import io
import json
import math
import re
import time
from typing import Dict, Iterable, List, Optional, Tuple

from . import BROWSER_UA, FetchError, http_get, num

HIST_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{index}_History.csv"
OPT_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json"
HISTORY = {"VIX9D": "Cboe S&P 500 9-Day Volatility Index", "VIX6M": "Cboe S&P 500 6-Month Volatility Index",
           "VVIX": "Cboe VIX of VIX Index", "SKEW": "Cboe S&P 500 Skew Index"}
DATASET = "options_cboe"
MIN_INTERVAL = 1.0
MAX_SYMBOLS = 120
INDEX_SYMBOLS = {"SPX": "_SPX", "NDX": "_NDX", "RUT": "_RUT", "VIX": "_VIX", "DJI": "_DJX"}
# liquid underlyings snapshotted every day, besides the holdings and the watchlist
LIQUID = ["SPX", "NDX", "RUT", "SPY", "QQQ", "IWM", "DIA", "TLT", "IEF", "HYG", "LQD", "GLD", "SLV", "USO", "UNG",
          "XLE", "XLF", "XLK", "XLV", "XLY", "XLP", "XLI", "XLU", "XLB", "XLRE", "XLC", "SMH", "EEM", "EFA", "FXI", "EWZ", "GDX",
          "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "BRK-B", "JPM", "V", "UNH", "XOM", "LLY", "JNJ",
          "WMT", "MA", "PG", "HD", "COST", "NFLX", "AMD", "BAC", "CVX", "KO", "PEP", "MRK", "ABBV", "ORCL", "CRM", "INTC"]
OCC = re.compile(r"(\d{2})(\d{2})(\d{2})([CP])(\d{8})$")
FIELDS = ("iv30", "iv60", "iv90", "skew25", "put_volume", "call_volume", "put_oi", "call_oi", "iv30_cboe", "spot", "n_contracts")


def _next(d: str) -> str:
    return (_dt.date.fromisoformat(d) + _dt.timedelta(days=1)).isoformat()


def _get(url: str) -> bytes:
    return http_get(url, {"User-Agent": BROWSER_UA, "Accept": "application/json, text/csv"}, timeout=90)


# ------------------------------------------------------------------ 1. index history
def parse_history(text: str, index: str) -> List[tuple]:
    """[(date, close, published)] from a Cboe history CSV (MM/DD/YYYY or ISO dates)."""
    rd = csv.reader(io.StringIO(text))
    header = [h.strip().upper() for h in next(rd, [])]
    if not header or header[0] != "DATE":
        return []
    col = header.index("CLOSE") if "CLOSE" in header else (header.index(index.upper()) if index.upper() in header else len(header) - 1)
    out = []
    for r in rd:
        if len(r) <= col:
            continue
        s = r[0].strip()
        d = None
        for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
            try:
                d = _dt.datetime.strptime(s, fmt).date().isoformat()
                break
            except ValueError:
                continue
        v = num(r[col])
        if d and v is not None and v > 0:
            out.append((d, v, _next(d)))
    return out


def refresh_history(store, progress=None) -> dict:
    say = progress or (lambda m: None)
    n, errors = 0, []
    for index in HISTORY:
        try:
            rows = parse_history(_get(HIST_URL.format(index=index)).decode("utf-8", "replace"), index)
        except FetchError as e:
            errors.append(f"Cboe {index} history: {e}")
            continue
        if rows:
            store.upsert_macro(f"CBOE_{index}", rows)
        n += len(rows)
        say(f"Cboe {index}: {len(rows)} days" + (f" ({rows[0][0]} → {rows[-1][0]})" if rows else ""))
        time.sleep(MIN_INTERVAL)
    return {"rows": n, "errors": errors}


# ------------------------------------------------------------------ 2. option-chain features
def session_for(now_ny: _dt.datetime) -> Optional[str]:
    """The session a snapshot taken at New York time `now_ny` shows, or None while the session is open."""
    d, t = now_ny.date(), now_ny.hour * 60 + now_ny.minute
    if d.weekday() < 5 and t >= 16 * 60 + 15:
        return d.isoformat()
    if d.weekday() < 5 and t >= 9 * 60 + 30:
        return None
    d -= _dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= _dt.timedelta(days=1)
    return d.isoformat()


def contracts(payload) -> Tuple[Optional[float], Optional[float], List[dict]]:
    """(spot, Cboe 30-day IV, [contract dicts: expiry, right, strike, iv, delta, oi, volume, quoted])."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None, None, []
    spot = num(data.get("current_price")) or num(data.get("close"))
    iv30c = num(data.get("iv30"))
    out = []
    for o in data.get("options") or []:
        if not isinstance(o, dict):
            continue
        m = OCC.search(str(o.get("option") or "").strip())
        if not m:
            continue
        yy, mm, dd, right, k = m.groups()
        try:
            exp = _dt.date(2000 + int(yy), int(mm), int(dd))
        except ValueError:
            continue
        bid, ask = num(o.get("bid")) or 0.0, num(o.get("ask")) or 0.0
        out.append({"expiry": exp, "right": right, "strike": int(k) / 1000.0, "iv": num(o.get("iv")), "delta": num(o.get("delta")),
                    "oi": num(o.get("open_interest")) or 0.0, "volume": num(o.get("volume")) or 0.0, "quoted": ask > 0 and ask >= bid})
    ivs = sorted(c["iv"] for c in out if c["iv"] and c["iv"] > 0)
    if ivs and ivs[len(ivs) // 2] > 3.0:               # quoted in percent: convert to decimal
        for c in out:
            c["iv"] = c["iv"] / 100.0 if c["iv"] else c["iv"]
    if iv30c is not None and iv30c > 3.0:
        iv30c /= 100.0
    return spot, iv30c, out


def _interp(pts: List[Tuple[float, float]], x: float) -> Optional[float]:
    pts = sorted(pts)
    if not pts:
        return None
    lo = [p for p in pts if p[0] <= x]
    hi = [p for p in pts if p[0] >= x]
    if not lo or not hi:
        return None                                     # never extrapolate across the strike or delta range
    (x0, y0), (x1, y1) = lo[-1], hi[0]
    return y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def _good(c) -> bool:
    return c["quoted"] and c["iv"] is not None and 0.01 < c["iv"] < 5.0


def atm_iv(chain: List[dict], spot: float) -> Optional[float]:
    sides = []
    for right in ("C", "P"):
        v = _interp([(c["strike"], c["iv"]) for c in chain if c["right"] == right and _good(c)], spot)
        if v is not None:
            sides.append(v)
    return sum(sides) / len(sides) if sides else None


def delta_iv(chain: List[dict], right: str, target: float = 0.25) -> Optional[float]:
    """IV at |delta| = target on one side (puts' deltas may be quoted negative or positive)."""
    pts = [(abs(c["delta"]), c["iv"]) for c in chain if c["right"] == right and _good(c) and c["delta"] is not None and 0.03 < abs(c["delta"]) < 0.6]
    return _interp(pts, target)


def constant_maturity(points: List[Tuple[int, float]], days: int, var: bool = True) -> Optional[float]:
    """Value at `days` from [(days to expiry, value)]: IVs by total variance, other values linearly; no extrapolation
    beyond the listed expiries except to the first expiry when it lies within twice the target."""
    pts = sorted(p for p in points if p[1] is not None)
    if not pts:
        return None
    lo = [p for p in pts if p[0] <= days]
    hi = [p for p in pts if p[0] >= days]
    if not hi:
        return None
    if not lo:
        return hi[0][1] if hi[0][0] <= 2 * days else None
    (t0, v0), (t1, v1) = lo[-1], hi[0]
    if t1 == t0:
        return v0
    u = (days - t0) / (t1 - t0)
    if not var:
        return v0 + u * (v1 - v0)
    w = v0 * v0 * t0 + u * (v1 * v1 * t1 - v0 * v0 * t0)
    return math.sqrt(w / days) if w > 0 else None


def features(payload, session: str) -> Dict[str, float]:
    spot, iv30c, chain = contracts(payload)
    if not chain or not spot or spot <= 0:
        return {}
    d0 = _dt.date.fromisoformat(session)
    by_exp: Dict[_dt.date, List[dict]] = {}
    for c in chain:
        by_exp.setdefault(c["expiry"], []).append(c)
    atm, skew = [], []
    for exp, cs in by_exp.items():
        t = (exp - d0).days
        if t < 5 or t > 800:                            # expiring this week: noise, not a 30-day view
            continue
        a = atm_iv(cs, spot)
        if a is not None:
            atm.append((t, a))
        p25, c25 = delta_iv(cs, "P"), delta_iv(cs, "C")
        if p25 is not None and c25 is not None:
            skew.append((t, p25 - c25))
    out = {"spot": spot, "n_contracts": float(sum(1 for c in chain if _good(c))),
           "put_volume": sum(c["volume"] for c in chain if c["right"] == "P"), "call_volume": sum(c["volume"] for c in chain if c["right"] == "C"),
           "put_oi": sum(c["oi"] for c in chain if c["right"] == "P"), "call_oi": sum(c["oi"] for c in chain if c["right"] == "C")}
    for days in (30, 60, 90):
        v = constant_maturity(atm, days)
        if v is not None:
            out[f"iv{days}"] = v
    s = constant_maturity(skew, 30, var=False)
    if s is not None:
        out["skew25"] = s
    if iv30c is not None and iv30c > 0:
        out["iv30_cboe"] = iv30c
    return out


def cboe_symbol(asset: dict) -> Optional[str]:
    aid = asset["id"]
    if aid in INDEX_SYMBOLS:
        return INDEX_SYMBOLS[aid]
    if asset.get("asset_class") not in ("EQUITY", "ETF") or asset.get("currency") != "USD" or ":" in aid or "." in str(asset.get("yahoo") or ""):
        return None                                     # US-listed stocks and ETFs only (no foreign natives)
    return aid.replace("-", ".")


def universe(store, extra: Iterable[str] = ()) -> List[Tuple[str, str]]:
    """[(asset id, Cboe symbol)]: the holdings, the watchlist, then the liquid list; at most MAX_SYMBOLS."""
    held = []
    try:
        pos: Dict[str, float] = {}
        for t in store.transactions("main"):
            if t.get("asset_id"):
                pos[t["asset_id"]] = pos.get(t["asset_id"], 0.0) + float(t.get("quantity") or 0.0)
        held = [a for a, q in pos.items() if abs(q) > 1e-9]
    except Exception:  # noqa: BLE001 — no ledger yet
        held = []
    ids = list(dict.fromkeys(list(extra) + held + [w["asset_id"] for w in store.watchlist()] + LIQUID))
    out = []
    for aid in ids:
        a = store.asset(aid)
        sym = cboe_symbol(a) if a else None
        if sym:
            out.append((aid, sym))
    return out[:MAX_SYMBOLS]


def snapshot(store, progress=None, now_utc: Optional[_dt.datetime] = None, symbols: Optional[List[Tuple[str, str]]] = None) -> dict:
    from .secevents import new_york
    say = progress or (lambda m: None)
    session = session_for(new_york(now_utc or _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)))
    if session is None:
        return {"deferred": "the US session is open: option snapshots are taken after 16:15 New York (or before 09:30)"}
    names = symbols if symbols is not None else universe(store)
    n, errors, done = 0, [], 0
    for k, (aid, sym) in enumerate(names):
        try:
            f = features(json.loads(_get(OPT_URL.format(sym=sym)).decode("utf-8", "replace")), session)
        except FetchError as e:
            errors.append(f"{sym}: {e}")
            f = None
        except ValueError:
            errors.append(f"{sym}: invalid JSON")
            f = None
        if f:
            pub = _next(session)
            store.put_alt(DATASET, [(aid, session, fld, float(v), pub) for fld, v in f.items() if v is not None and math.isfinite(v)])
            n += len(f)
            done += 1
        if (k + 1) % 10 == 0:
            say(f"options {k + 1}/{len(names)} ({done} stored)")
        time.sleep(MIN_INTERVAL)
    say(f"option snapshots for {session}: {done}/{len(names)} underlyings")
    return {"rows": n, "underlyings": done, "session": session, "errors": errors}


def refresh(store, progress=None) -> dict:
    return {"history": refresh_history(store, progress), "options": snapshot(store, progress)}
