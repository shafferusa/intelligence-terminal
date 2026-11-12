"""Finnhub free-tier analyst data -> point-in-time alt_data dataset ``analyst_finnhub``. Research only.

Key: ``FINNHUB_KEY`` (never written to disk or logs; errors never carry the URL). Two endpoints, one request each per stock,
paced under the free tier's 60 calls a minute (price targets, estimates and upgrades/downgrades are premium: 403):

``/stock/recommendation`` — the monthly count of strong-buy / buy / hold / sell / strong-sell ratings. Finnhub restates
the current month as ratings change and keeps no vintages, so the series is recorded as *snapshots*: each fetch stores
the latest month's counts under the fetch date (fields ``rec_strong_buy`` … ``rec_strong_sell``, ``rec_n``,
``rec_mean`` = mean rating on 1 strong buy … 5 strong sell, ``rec_period`` = the month as yyyymm). Published the day
after the fetch. The monthly history the endpoint returns is not stored: its months were revised after the fact.

``/stock/earnings`` — the last four quarterly EPS surprises (actual, consensus estimate). Stored under the fiscal
period end (``eps_actual``, ``eps_estimate``, ``eps_surprise``, ``eps_surprise_pct``), published the day after the
company's earnings release when the store has it (SEC 8-K item 2.02, ``calendar.earnings_history``) within 120 days of the
period end, otherwise the day after the fetch. A period already stored is never overwritten (first-seen values are
the point-in-time ones).

History does not exist for free; the snapshot series begins on the first run (DATA_NEEDS.md).
"""
from __future__ import annotations

import datetime as _dt
import json
import time
from typing import Dict, List, Optional

from . import FetchError, env_key, http_get, num, placeholder_key

DATASET = "analyst_finnhub"
REC_URL = "https://finnhub.io/api/v1/stock/recommendation?symbol={sym}&token={key}"
EPS_URL = "https://finnhub.io/api/v1/stock/earnings?symbol={sym}&token={key}"
MIN_INTERVAL = 1.05
KEY_ENV = "FINNHUB_KEY"
RATINGS = (("strongBuy", "rec_strong_buy", 1), ("buy", "rec_buy", 2), ("hold", "rec_hold", 3), ("sell", "rec_sell", 4),
           ("strongSell", "rec_strong_sell", 5))


def _next(d: str) -> str:
    return (_dt.date.fromisoformat(d) + _dt.timedelta(days=1)).isoformat()


def rec_rows(payload, asset: str, fetched: str) -> List[tuple]:
    """The latest month's rating counts as a snapshot dated `fetched`."""
    items = [r for r in payload if isinstance(r, dict)] if isinstance(payload, list) else []
    best = None
    for r in items:
        p = str(r.get("period") or "")[:10]
        try:
            _dt.date.fromisoformat(p)
        except ValueError:
            continue
        if best is None or p > best[0]:
            best = (p, r)
    if best is None:
        return []
    p, r = best
    counts = [(fld, w, num(r.get(src))) for src, fld, w in RATINGS]
    if any(c is None or c < 0 for _, _, c in counts):
        return []
    n = sum(c for _, _, c in counts)
    pub = _next(fetched)
    out = [(asset, fetched, fld, c, pub) for fld, _, c in counts]
    out += [(asset, fetched, "rec_n", n, pub), (asset, fetched, "rec_period", float(p[:4] + p[5:7]), pub)]
    if n > 0:
        out.append((asset, fetched, "rec_mean", sum(w * c for _, w, c in counts) / n, pub))
    return out


def release_after(releases: List[str], period: str, days: int = 120) -> Optional[str]:
    """The first release publication date after the period end, within `days`."""
    lim = (_dt.date.fromisoformat(period) + _dt.timedelta(days=days)).isoformat()
    later = sorted(p for p in releases if period < p <= lim)
    return later[0] if later else None


def eps_rows(payload, asset: str, fetched: str, releases: List[str], stored: set) -> List[tuple]:
    out = []
    for r in payload if isinstance(payload, list) else []:
        if not isinstance(r, dict):
            continue
        p = str(r.get("period") or "")[:10]
        try:
            _dt.date.fromisoformat(p)
        except ValueError:
            continue
        if p > fetched or (p, "eps_actual") in stored:
            continue
        act, est = num(r.get("actual")), num(r.get("estimate"))
        if act is None:
            continue
        rel = release_after(releases, p)
        pub = _next(rel) if rel else _next(fetched)
        vals = {"eps_actual": act, "eps_estimate": est, "eps_surprise": num(r.get("surprise")), "eps_surprise_pct": num(r.get("surprisePercent"))}
        out += [(asset, p, f, v, pub) for f, v in vals.items() if v is not None]
    return out


def symbols(store) -> List[str]:
    """US common stocks, holdings and the watchlist first."""
    eq = [a["id"] for a in store.assets("EQUITY") if a.get("currency", "USD") == "USD" and "." not in str(a.get("yahoo") or "")]
    first = [w["asset_id"] for w in store.watchlist()]
    try:
        first = [t["asset_id"] for t in store.transactions("main") if t.get("asset_id")] + first
    except Exception:  # noqa: BLE001 — no ledger yet
        pass
    s = set(eq)
    return list(dict.fromkeys([a for a in first if a in s] + eq))


def _json(url: str):
    return json.loads(http_get(url, {"Accept": "application/json"}, timeout=45).decode("utf-8", "replace"))


def refresh(store, progress=None, key: Optional[str] = None, limit: Optional[int] = None) -> dict:
    from .calendar import earnings_history
    say = progress or (lambda m: None)
    key = key or env_key(KEY_ENV)
    if not key:
        why = f"{KEY_ENV} is still the placeholder text" if placeholder_key(KEY_ENV) else f"set {KEY_ENV}"
        return {"skipped": f"{why} (free: https://finnhub.io/register)"}
    fetched = _dt.date.today().isoformat()
    names = symbols(store)[:limit] if limit else symbols(store)
    n, errors, denied = 0, [], 0
    for k, aid in enumerate(names):
        sym = aid.replace("-", ".")
        rows: List[tuple] = []
        for which, url in (("recommendation", REC_URL), ("earnings", EPS_URL)):
            try:
                payload = _json(url.format(sym=sym, key=key))
            except FetchError as e:
                if e.status == 429:                     # over the minute's quota: wait once and retry
                    time.sleep(61)
                    try:
                        payload = _json(url.format(sym=sym, key=key))
                    except (FetchError, ValueError) as e2:
                        errors.append(f"{sym} {which}: {e2}")
                        continue
                else:
                    denied += e.status in (401, 403)
                    errors.append(f"{sym} {which}: {e}")
                    continue
            except ValueError:
                errors.append(f"{sym} {which}: invalid JSON")
                continue
            if which == "recommendation":
                rows += rec_rows(payload, aid, fetched)
            else:
                stored = {(d, f) for _, d, f, _, _ in store.alt(DATASET, aid)}
                releases = [p for _, _, p in earnings_history(store, aid)]
                rows += eps_rows(payload, aid, fetched, releases, stored)
            time.sleep(MIN_INTERVAL)
        if rows:
            store.put_alt(DATASET, rows)
            n += len(rows)
        if denied >= 4 and n == 0:
            errors.append("Finnhub refuses the key (HTTP 401/403): stopped")
            break
        if (k + 1) % 50 == 0:
            say(f"analyst {k + 1}/{len(names)}")
    say(f"analyst data: {n} rows for {len(names)} stocks")
    return {"rows": n, "stocks": len(names), "errors": errors}
