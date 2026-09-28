"""Commodity futures curves (contracts 1–4) -> point-in-time alt_data dataset ``futures_curve``. Research only.

Two providers, one layout (asset ``fut:<ROOT>``, fields ``c1``..``c4`` = settlement / close of the n-th listed contract,
``cN_ym`` = its delivery month as yyyymm, ``cN_vol`` = its volume, ``src`` = 1 EIA / 2 Yahoo):

* **History — EIA** (api.eia.gov, ``EIA_API_KEY``): daily NYMEX prices of contracts 1–4 for WTI (from 1983), natural gas
  (1994), No. 2 heating oil (1980s) and RBOB gasoline (2005). EIA stopped publishing these series on 5 April 2024
  (they end there; checked 28 Sep 2026), so they are training history only. Re-checked every 30 days in case EIA resumes.
* **Forward — Yahoo Finance chart endpoint** (the same endpoint and browser-style UA as the price history; Yahoo is
  the one host where that UA is acceptable): one request per candidate contract month (``CLX26.NYM``, ``ZCZ26.CBT``,
  ``GCZ26.CMX``…). The first four listed months whose last bar is the root's latest session are contracts 1–4 — the
  same definition as EIA's (an expired month is either delisted or stale). Collected daily from the first run on;
  there is no free source for the April 2024 – first-run gap.

Point in time: a settlement is public at the session's close, so each row is published the next calendar day.
A bar for today is kept only after 17:00 New York (later settlements, e.g. grains, are posted by then); before that
the previous complete session is stored. Payloads are untrusted and parsed with json; numbers go through ``num``.
"""
from __future__ import annotations

import datetime as _dt
import json
import time
from typing import Dict, List, Optional, Tuple

from . import BROWSER_UA, FetchError, env_key, http_get, num, placeholder_key

DATASET = "futures_curve"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=5d&interval=1d"
CODES = "FGHJKMNQUVXZ"
MIN_INTERVAL = 0.4
EIA_RECHECK_DAYS = 30
EIA_KEY = "futcurve:eia_checked"
EIA_ENDED = "2024-04-05"

# asset -> (Yahoo root, exchange suffix, listed delivery months used as the curve)
ROOTS: Dict[str, Tuple[str, str, str]] = {
    "WTI": ("CL", "NYM", CODES), "BRENT": ("BZ", "NYM", CODES), "NATGAS": ("NG", "NYM", CODES),
    "HEATOIL": ("HO", "NYM", CODES), "RBOB": ("RB", "NYM", CODES),
    "GOLD": ("GC", "CMX", "GJMQVZ"), "SILVER": ("SI", "CMX", "HKNUZ"), "COPPER": ("HG", "CMX", "HKNUZ"),
    "CORN": ("ZC", "CBT", "HKNUZ"), "SOY": ("ZS", "CBT", "FHKNQUX"), "WHEAT": ("ZW", "CBT", "HKNUZ"),
}
# EIA v1 series ids of contracts 1–4 (history to 2024-04-05)
EIA_SERIES: Dict[str, List[str]] = {
    "WTI": [f"PET.RCLC{n}.D" for n in range(1, 5)],
    "NATGAS": [f"NG.RNGC{n}.D" for n in range(1, 5)],
    "HEATOIL": [f"PET.EER_EPD2F_PE{n}_Y35NY_DPG.D" for n in range(1, 5)],
    "RBOB": [f"PET.EER_EPMRR_PE{n}_Y35NY_DPG.D" for n in range(1, 5)],
}
# which FinSim2 assets each curve informs (research features: slope, roll yield)
LINKS: Dict[str, List[str]] = {"WTI": ["WTI", "USO", "XLE"], "BRENT": ["BRENT", "BNO"], "NATGAS": ["NATGAS", "UNG"],
                               "HEATOIL": ["HEATOIL"], "RBOB": ["RBOB", "UGA"], "GOLD": ["GOLD", "GLD", "IAU"],
                               "SILVER": ["SILVER", "SLV"], "COPPER": ["COPPER", "CPER"], "CORN": ["CORN"], "SOY": ["SOYBEANS", "SOYB"],
                               "WHEAT": ["WHEAT", "WEAT"]}


def _next(d: str) -> str:
    return (_dt.date.fromisoformat(d) + _dt.timedelta(days=1)).isoformat()


def candidates(root: str, suffix: str, months: str, today: _dt.date, n: int = 8) -> List[Tuple[str, int]]:
    """The next ``n`` listed delivery months from the current one: [(Yahoo symbol, yyyymm)]."""
    out, y, m = [], today.year, today.month
    while len(out) < n:
        code = CODES[m - 1]
        if code in months:
            out.append((f"{root}{code}{y % 100:02d}.{suffix}", y * 100 + m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def last_bar(result, now_ny: _dt.datetime) -> Optional[Tuple[str, float, Optional[float]]]:
    """(session date, close, volume) of the last complete daily bar in a chart result, or None."""
    if not isinstance(result, dict):
        return None
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    ts = result.get("timestamp") if isinstance(result.get("timestamp"), list) else []
    ind = result.get("indicators") if isinstance(result.get("indicators"), dict) else {}
    q = (ind.get("quote") or [{}])[0] if isinstance(ind.get("quote"), list) else {}
    closes = q.get("close") if isinstance(q, dict) and isinstance(q.get("close"), list) else []
    vols = q.get("volume") if isinstance(q, dict) and isinstance(q.get("volume"), list) else []
    off = num(meta.get("gmtoffset")) or 0.0
    best = None
    for i, t in enumerate(ts):
        tv, c = num(t), num(closes[i]) if i < len(closes) else None
        if tv is None or c is None or c <= 0:
            continue
        d = _dt.datetime.fromtimestamp(tv + off, _dt.timezone.utc).date()
        if d > now_ny.date() or (d == now_ny.date() and now_ny.hour < 17):
            continue                                   # today's session is not settled yet
        best = (d.isoformat(), c, num(vols[i]) if i < len(vols) else None)
    return best


def curve_rows(asset: str, bars: List[Tuple[int, Tuple[str, float, Optional[float]]]]) -> List[tuple]:
    """Rows for one root from [(yyyymm, last bar)] in delivery order: the first four months on the latest session."""
    if not bars:
        return []
    latest = max(b[0] for _, b in bars)
    live = [(ym, b) for ym, b in bars if b[0] == latest][:4]
    pub, loc = _next(latest), f"fut:{asset}"
    rows = [(loc, latest, "src", 2.0, pub)]
    for n, (ym, (_, close, vol)) in enumerate(live, start=1):
        rows += [(loc, latest, f"c{n}", close, pub), (loc, latest, f"c{n}_ym", float(ym), pub)]
        if vol is not None:
            rows.append((loc, latest, f"c{n}_vol", vol, pub))
    return rows


def _yahoo(sym: str):
    body = http_get(YAHOO.format(sym=sym), {"User-Agent": BROWSER_UA, "Accept": "application/json"}, timeout=30)
    res = (json.loads(body.decode("utf-8", "replace")).get("chart") or {}).get("result")
    return res[0] if isinstance(res, list) and res else None


def snapshot(store, progress=None, now_utc: Optional[_dt.datetime] = None) -> dict:
    from .secevents import new_york
    say = progress or (lambda m: None)
    now_ny = new_york(now_utc or _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None))
    n, errors, missing = 0, [], []
    for asset, (root, suffix, months) in ROOTS.items():
        bars = []
        for sym, ym in candidates(root, suffix, months, now_ny.date()):
            try:
                b = last_bar(_yahoo(sym), now_ny)
            except FetchError as e:
                if e.status != 404:                    # 404 = a delisted (expired) month: expected
                    errors.append(f"{sym}: {e}")
                b = None
            except ValueError:
                errors.append(f"{sym}: invalid JSON")
                b = None
            if b:
                bars.append((ym, b))
            time.sleep(MIN_INTERVAL)
            if len(bars) >= 5:                          # four live months plus one to spot a stale front month
                break
        rows = curve_rows(asset, bars)
        if rows:
            store.put_alt(DATASET, rows)
            n += len(rows)
        else:
            missing.append(asset)
        say(f"{asset} curve: {sum(1 for r in rows if r[2] in ('c1', 'c2', 'c3', 'c4'))} contracts" + (f" on {rows[0][1]}" if rows else ""))
    if missing and not n:
        errors.append("no curve stored: " + ", ".join(missing))
    return {"rows": n, "roots": len(ROOTS) - len(missing), "errors": errors}


def eia_rows(payload, asset: str, n: int) -> List[tuple]:
    resp = payload.get("response") if isinstance(payload, dict) else None
    out = []
    for r in ((resp or {}).get("data") if isinstance(resp, dict) else None) or []:
        if not isinstance(r, dict):
            continue
        d = str(r.get("period") or "")[:10]
        try:
            _dt.date.fromisoformat(d)
        except ValueError:
            continue
        v = num(r.get("value"))
        if v is None or v <= 0:
            continue
        out.append((f"fut:{asset}", d, f"c{n}", v, _next(d)))
        if n == 1:
            out.append((f"fut:{asset}", d, "src", 1.0, _next(d)))
    return out


def eia_history(store, progress=None, key: Optional[str] = None, force: bool = False) -> dict:
    """The EIA contract 1–4 history, fetched when never stored or every EIA_RECHECK_DAYS days."""
    from . import eia
    say = progress or (lambda m: None)
    last = store.kv_get(EIA_KEY)
    if not force and last and (_dt.date.today() - _dt.date.fromisoformat(str(last)[:10])).days < EIA_RECHECK_DAYS:
        return {"rows": 0, "note": f"EIA futures history checked {last}; next check after {EIA_RECHECK_DAYS} days"}
    key = key or env_key(eia.KEY_ENV)
    if not key:
        why = f"{eia.KEY_ENV} is still the placeholder text" if placeholder_key(eia.KEY_ENV) else f"set {eia.KEY_ENV}"
        return {"skipped": f"EIA futures history: {why}"}
    n, errors, latest = 0, [], None
    for asset, sids in EIA_SERIES.items():
        for k, sid in enumerate(sids, start=1):
            try:
                rows = [r for p in eia.pages(sid, key, f"{asset} contract {k}") for r in eia_rows(p, asset, k)]
            except (FetchError, ValueError) as e:
                errors.append(str(e))
                continue
            if rows:
                store.put_alt(DATASET, rows)
                latest = max(latest or "", max(r[1] for r in rows))
            n += len(rows)
            say(f"EIA {asset} contract {k}: {len(rows)} rows")
            time.sleep(eia.MIN_INTERVAL)
    if not errors:
        store.kv_set(EIA_KEY, _dt.date.today().isoformat())
    return {"rows": n, "latest": latest, "errors": errors}


def refresh(store, progress=None) -> dict:
    return {"curve": snapshot(store, progress), "eia_history": eia_history(store, progress)}
