"""FINRA Reg SHO daily short-sale volume (cdn.finra.org) -> alt_data dataset "finra_shvol".

One pipe-delimited file per trading day (consolidated NMS): Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market.
FINRA publishes each file after the close, so a day's values are stored with published = the next calendar day (they are
never used for a score computed at that day's close). Files exist on the CDN from 2019 on; earlier dates return 403.

This is *short-sale volume* (every short-marked trade, including market-maker hedging), not short *interest*: a noisy
positioning proxy. The files are public; no key is needed. Downloads are paced (MIN_INTERVAL between requests).
"""
from __future__ import annotations

import datetime as _dt
import time
from typing import Dict, Iterable, List, Optional, Tuple

from . import GOV_UA, FetchError, http_get

URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{d}.txt"
DATASET = "finra_shvol"
MIN_INTERVAL = 0.35
FIRST_AVAILABLE = "2019-01-01"


def finra_symbol(asset_id: str) -> str:
    return asset_id.replace("-", ".")


def parse(text: str, wanted: Dict[str, str]) -> Dict[str, Tuple[float, float]]:
    """{asset_id: (short volume, total volume)} for the wanted FINRA symbols (untrusted text, parsed defensively)."""
    out: Dict[str, Tuple[float, float]] = {}
    for line in text.splitlines()[1:]:
        parts = line.split("|")
        if len(parts) < 5:
            continue
        a = wanted.get(parts[1].strip())
        if a is None:
            continue
        try:
            sv, tv = float(parts[2]), float(parts[4])
        except ValueError:
            continue
        if tv > 0 and 0 <= sv <= tv * 1.05:
            out[a] = (sv, tv)
    return out


def fetch_day(date: str, wanted: Dict[str, str]) -> Optional[Dict[str, Tuple[float, float]]]:
    """None when FINRA has no file for the date (holiday, before 2019)."""
    try:
        body = http_get(URL.format(d=date.replace("-", "")), headers={"User-Agent": GOV_UA}, timeout=60)
    except FetchError as e:
        if e.status in (403, 404):
            return None
        raise
    return parse(body.decode("utf-8", "replace"), wanted)


def refresh(store, asset_ids: Iterable[str], dates: Iterable[str], progress=None) -> dict:
    """Download the given trading days (skipping ones already stored) for the given US-listed assets."""
    wanted = {finra_symbol(a): a for a in asset_ids}
    have = store.alt_dates(DATASET)
    todo = sorted(d for d in set(dates) if d >= FIRST_AVAILABLE and d not in have)
    got = missing = 0
    last = 0.0
    for k, d in enumerate(todo):
        wait = MIN_INTERVAL - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        last = time.time()
        try:
            day = fetch_day(d, wanted)
        except FetchError as e:
            if progress:
                progress(f"FINRA {d}: {e}")
            continue
        if day is None:
            missing += 1
            continue
        pub = (_dt.date.fromisoformat(d) + _dt.timedelta(days=1)).isoformat()
        rows: List[tuple] = []
        for a, (sv, tv) in day.items():
            rows += [(a, d, "short", sv, pub), (a, d, "total", tv, pub)]
        store.put_alt(DATASET, rows)
        got += 1
        if progress and (k % 25 == 0 or k == len(todo) - 1):
            progress(f"FINRA short volume {k + 1}/{len(todo)} ({d}, {len(day)} assets)")
    return {"days": got, "missing": missing, "requested": len(todo)}
