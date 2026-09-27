"""EIA weekly energy fundamentals -> point-in-time alt_data dataset ``eia_weekly``.

Source: EIA Open Data API v2 (free key: https://www.eia.gov/opendata/register.php), environment variable
``EIA_API_KEY`` — never written to disk or logs (errors never carry the URL). Endpoint:
    https://api.eia.gov/v2/seriesid/{SERIES}?api_key=...        (the v1 series ids, served by v2)

Rows are stored under ``eia:US`` (or ``eia:CUSHING``) with the series' short name as the field and the week-ending
date as the date. ``LINKS`` says which FinSim2 assets each series informs.

Point in time: the Weekly Petroleum Status Report for the week ending Friday is released the following Wednesday at
10:30 New York (Thursday in holiday weeks); the natural-gas storage report on Thursday at 10:30 (Friday or Wednesday
around holidays). Rows are published Thursday (petroleum, +6 days) and Friday (gas, +7 days) — conservative for
holiday weeks. EIA occasionally revises weekly values; the API returns the latest vintage, so a revised week carries
its revised value (flagged in NEW_DATA_SOURCES.md; revisions to these series are rare and small).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import time
from typing import Dict, List, Optional, Tuple

from . import FetchError, http_get, num

DATASET = "eia_weekly"
URL = "https://api.eia.gov/v2/seriesid/{sid}?api_key={key}&length=5000&offset={off}"
MIN_INTERVAL = 0.3
KEY_ENV = "EIA_API_KEY"

# short name -> (series id, location key, publication lag in days after the week-ending date, units)
SERIES: Dict[str, Tuple[str, str, int, str]] = {
    "crude_stocks": ("PET.WCESTUS1.W", "eia:US", 6, "thousand barrels (excl. SPR)"),
    "spr_stocks": ("PET.WCSSTUS1.W", "eia:US", 6, "thousand barrels"),
    "gasoline_stocks": ("PET.WGTSTUS1.W", "eia:US", 6, "thousand barrels"),
    "distillate_stocks": ("PET.WDISTUS1.W", "eia:US", 6, "thousand barrels"),
    "cushing_stocks": ("PET.W_EPC0_SAX_YCUOK_MBBL.W", "eia:CUSHING", 6, "thousand barrels"),
    "refinery_util": ("PET.WPULEUS3.W", "eia:US", 6, "percent"),
    "crude_production": ("PET.WCRFPUS2.W", "eia:US", 6, "thousand barrels / day"),
    "crude_imports": ("PET.WCRIMUS2.W", "eia:US", 6, "thousand barrels / day"),
    "crude_exports": ("PET.WCREXUS2.W", "eia:US", 6, "thousand barrels / day"),
    "product_supplied": ("PET.WRPUPUS2.W", "eia:US", 6, "thousand barrels / day"),
    "natgas_storage": ("NG.NW2_EPG0_SWO_R48_BCF.W", "eia:US", 7, "billion cubic feet (lower 48 working gas)"),
}

LINKS: Dict[str, List[str]] = {
    "WTI": ["crude_stocks", "cushing_stocks", "refinery_util", "crude_production", "crude_imports", "crude_exports", "product_supplied", "spr_stocks"],
    "BRENT": ["crude_stocks", "crude_exports", "refinery_util"],
    "USO": ["crude_stocks", "cushing_stocks", "refinery_util", "crude_production"],
    "XLE": ["crude_stocks", "refinery_util", "crude_production"],
    "NATGAS": ["natgas_storage"],
    "UNG": ["natgas_storage"],
}


def parse(payload: dict, field: str) -> List[tuple]:
    sid, loc, lag, _ = SERIES[field]
    resp = payload.get("response") if isinstance(payload, dict) else None
    data = (resp or {}).get("data") if isinstance(resp, dict) else None
    out = []
    for r in data or []:
        if not isinstance(r, dict):
            continue
        d = str(r.get("period") or "")[:10]
        try:
            day = _dt.date.fromisoformat(d)
        except ValueError:
            continue
        v = num(r.get("value"))
        if v is None:
            continue
        out.append((loc, d, field, v, (day + _dt.timedelta(days=lag)).isoformat()))
    return out


def fetch(field: str, key: str) -> List[tuple]:
    sid = SERIES[field][0]
    out: List[tuple] = []
    off = 0
    while True:
        try:
            body = http_get(URL.format(sid=sid, key=key, off=off), {"Accept": "application/json"}, timeout=60)
        except FetchError as e:
            raise FetchError(f"EIA {field}: HTTP {e.status}" if e.status else f"EIA {field}: network error", e.status) from None
        payload = json.loads(body.decode("utf-8", "replace"))
        rows = parse(payload, field)
        out += rows
        total = num(((payload.get("response") or {}).get("total"))) if isinstance(payload, dict) else None
        off += 5000
        if not rows or total is None or off >= total:
            break
        time.sleep(MIN_INTERVAL)
    return out


def refresh(store, progress=None, key: Optional[str] = None) -> dict:
    say = progress or (lambda m: None)
    key = key or os.environ.get(KEY_ENV)
    if not key:
        return {"skipped": f"set {KEY_ENV} (free key: https://www.eia.gov/opendata/register.php)"}
    n, errors = 0, []
    for k, field in enumerate(SERIES):
        try:
            rows = fetch(field, key)
        except (FetchError, ValueError) as e:
            errors.append(str(e))
            continue
        if rows:
            store.put_alt(DATASET, rows)
        n += len(rows)
        say(f"EIA {field}: {len(rows)} weeks ({k + 1}/{len(SERIES)})")
        time.sleep(MIN_INTERVAL)
    return {"series": len(SERIES), "rows": n, "errors": errors}
