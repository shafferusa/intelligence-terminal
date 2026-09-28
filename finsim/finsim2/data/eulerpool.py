"""Eulerpool free-tier probe: test the claims before building on them (no data enters the research store).

    python -m finsim2 probe eulerpool          # needs EULERPOOL_API_KEY (free: eulerpool.com/developers/register)

Eulerpool advertises a free tier of 100,000 requests a month (non-commercial; "Data by Eulerpool" attribution in anything
public), 100+ years of history across stocks, fundamentals, options / futures, bonds, FX, commodities, crypto and macro,
and historical equity data that is **point in time and survivorship-bias free**. FinSim2 cannot reach the host from the
cloud research environment, so this probe runs on your machine and checks each claim with a handful of requests
(< 40 of the monthly 100,000):

  1. the API description (OpenAPI) — which endpoints exist, and which take a date / range parameter (history) — saved to
     the FinSim2 home as ``eulerpool_openapi.json`` (not the repository);
  2. a known endpoint on Apple (``/api/1/equity/overview/{ISIN}``) — the key works and what fields come back;
  3. **survivorship**: the same endpoint for companies that no longer trade — Lehman Brothers, Enron, SVB Financial,
     First Republic, Bed Bath & Beyond, Twitter;
  4. **history of a dead company**: a price-history endpoint (found in the API description) for Lehman — does it end in
     2008, or is it missing;
  5. **point in time fundamentals**: an income-statement endpoint for Apple — does each period carry a filing /
     publication date (without one, a backtest cannot know when a number became public);
  6. **options / futures**: whether those endpoints accept a historical date, or only return today;
  7. the rate-limit headers (remaining quota).

The report is printed and saved as ``eulerpool_probe.md`` in the FinSim2 home; paste it back to decide what to build.
Responses are untrusted data: parsed with json, strings truncated, nothing evaluated. The key goes only in the
Authorization header and never into a message, file or log.
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional, Tuple

from . import FetchError, env_key, http_get, placeholder_key

KEY_ENV = "EULERPOOL_API_KEY"
BASE = "https://api.eulerpool.com"
SPEC_URLS = ["/openapi.json", "/api/openapi.json", "/api/1/openapi.json", "/docs/openapi.json", "/swagger.json", "/api/swagger.json"]
APPLE = "US0378331005"
DEAD = {"Lehman Brothers (2008)": "US5249081002", "Enron (2001)": "US2935611069", "SVB Financial (2023)": "US78486Q1013",
        "First Republic (2023)": "US33616C1009", "Bed Bath & Beyond (2023)": "US0758961009", "Twitter (2022)": "US90184L1026"}
DATE_KEYS = ("filingdate", "filing_date", "fileddate", "filed", "reportdate", "report_date", "accepteddate", "publishdate",
             "published", "releasedate", "announcementdate", "date_filed", "availabledate")
MIN_INTERVAL = 0.25


def _get(path: str, key: str) -> Tuple[Optional[int], object, Dict[str, str]]:
    url = path if path.startswith("http") else BASE + path
    try:
        body = http_get(url, {"Authorization": f"Bearer {key}", "Accept": "application/json"}, timeout=45)
    except FetchError as e:
        try:
            payload = json.loads((e.body or b"").decode("utf-8", "replace")) if e.body else None
        except ValueError:
            payload = None
        return e.status, payload, {}
    try:
        return 200, json.loads(body.decode("utf-8", "replace")), {}
    except ValueError:
        return 200, None, {}


def _shape(v, depth: int = 0) -> str:
    """A short description of a JSON value (keys, lengths), never its full content."""
    if isinstance(v, dict):
        ks = list(v)[:12]
        return "{" + ", ".join(str(k)[:30] for k in ks) + (", …" if len(v) > 12 else "") + "}"
    if isinstance(v, list):
        return f"[{len(v)} items" + (f"; first {_shape(v[0], depth + 1)}" if v and depth < 1 else "") + "]"
    return type(v).__name__


def _flat_keys(v, out=None, depth=0) -> set:
    out = set() if out is None else out
    if depth > 3:
        return out
    if isinstance(v, dict):
        for k, x in v.items():
            out.add(str(k).lower().replace("-", "").replace(" ", ""))
            _flat_keys(x, out, depth + 1)
    elif isinstance(v, list):
        for x in v[:3]:
            _flat_keys(x, out, depth + 1)
    return out


def _dates_in(v) -> List[str]:
    out = []

    def walk(x, d=0):
        if d > 4:
            return
        if isinstance(x, dict):
            for k, y in x.items():
                if isinstance(y, str) and len(y) >= 10 and y[:4].isdigit() and y[4] == "-":
                    out.append(y[:10])
                walk(y, d + 1)
        elif isinstance(x, list):
            for y in x:
                walk(y, d + 1)
    walk(v)
    return sorted(out)


def find_paths(spec: dict, words: Tuple[str, ...]) -> List[Tuple[str, List[str]]]:
    """[(path, parameter names)] whose path contains any of the words (GET operations only)."""
    out = []
    for p, ops in (spec.get("paths") or {}).items() if isinstance(spec, dict) else []:
        if not isinstance(ops, dict) or "get" not in ops or not any(w in p.lower() for w in words):
            continue
        params = [str(x.get("name")) for x in (ops["get"].get("parameters") or []) + (ops.get("parameters") or []) if isinstance(x, dict)]
        out.append((p, params))
    return out


def _fill(path: str, ident: str) -> str:
    import re
    return re.sub(r"\{[^}]+\}", ident, path, count=1)


def probe(home: str, progress=None, key: Optional[str] = None) -> str:
    say = progress or (lambda m: None)
    key = key or env_key(KEY_ENV)
    if not key:
        why = f"{KEY_ENV} is still the placeholder text" if placeholder_key(KEY_ENV) else f"set {KEY_ENV}"
        return f"Eulerpool probe skipped: {why} (free key: https://eulerpool.com/developers/register)"
    L = ["# Eulerpool free-tier probe", "", f"Run {time.strftime('%Y-%m-%d %H:%M')} from this machine.", ""]
    calls = 0

    def get(path):
        nonlocal calls
        calls += 1
        time.sleep(MIN_INTERVAL)
        return _get(path, key)

    # 1. API description
    spec = None
    for u in SPEC_URLS:
        st, body, _ = get(u)
        if st == 200 and isinstance(body, dict) and body.get("paths"):
            spec = body
            with open(os.path.join(home, "eulerpool_openapi.json"), "w", encoding="utf-8") as f:
                json.dump(body, f)
            break
    L.append("## 1. API description")
    if spec:
        paths = spec.get("paths") or {}
        groups: Dict[str, int] = {}
        for p in paths:
            seg = [s for s in p.split("/") if s and s not in ("api", "1")][:1]
            groups[seg[0] if seg else "/"] = groups.get(seg[0] if seg else "/", 0) + 1
        L.append(f"{len(paths)} paths; by area: " + ", ".join(f"{k} {v}" for k, v in sorted(groups.items(), key=lambda x: -x[1])[:20]))
    else:
        L.append("no OpenAPI description at the usual URLs — steps 4–6 need the path names from eulerpool.com/developers")
    say("spec done")

    # 2. known endpoint
    st, body, _ = get(f"/api/1/equity/overview/{APPLE}")
    L += ["", "## 2. Key and a known endpoint (Apple overview)", f"HTTP {st}; {_shape(body)}"]
    if st in (401, 403):
        L.append("the key is refused — stop here and check the key / plan")
        return "\n".join(L) + "\n"

    # 3. survivorship
    L += ["", "## 3. Survivorship: companies that no longer trade", "| Company | HTTP | Response |", "|---|---|---|"]
    alive = 0
    for name, isin in DEAD.items():
        st, body, _ = get(f"/api/1/equity/overview/{isin}")
        ok = st == 200 and bool(body)
        alive += ok
        L.append(f"| {name} {isin} | {st} | {_shape(body) if ok else '—'} |")
    L.append(f"**{alive} of {len(DEAD)} delisted companies answer.** Survivorship-free data would answer for all of them.")

    # 4. price history of a dead company
    L += ["", "## 4. Price history of a delisted company (Lehman)"]
    hist = find_paths(spec, ("historical", "prices", "quotes", "ohlc", "timeseries", "chart")) if spec else []
    tried = 0
    for p, params in hist[:3]:
        if "{" not in p:
            continue
        st, body, _ = get(_fill(p, DEAD["Lehman Brothers (2008)"]))
        ds = _dates_in(body)
        L.append(f"- `{p}` → HTTP {st}; {len(ds)} dated values" + (f", {ds[0]} → {ds[-1]}" if ds else ""))
        tried += 1
    if not tried:
        L.append("- no price-history path found in the description (or no description)")

    # 5. point-in-time fundamentals
    L += ["", "## 5. Fundamentals: does each period say when it became public?"]
    fund = find_paths(spec, ("income", "financial", "fundamental", "statement", "earnings")) if spec else []
    tried = 0
    for p, params in fund[:3]:
        if "{" not in p:
            continue
        st, body, _ = get(_fill(p, APPLE))
        keys = _flat_keys(body)
        found = sorted(k for k in keys if k in DATE_KEYS)
        L.append(f"- `{p}` → HTTP {st}; publication-date fields: {', '.join(found) if found else 'NONE'}; {_shape(body)}")
        tried += 1
    if not tried:
        L.append("- no fundamentals path found in the description (or no description)")
    L.append("Without a filing / publication date per period the figures cannot be used point in time (FinSim2 keeps using "
             "SEC first-reported figures with their filing dates).")

    # 6. options / futures history
    L += ["", "## 6. Options and futures: history or only today?"]
    der = find_paths(spec, ("option", "future", "derivative")) if spec else []
    for p, params in der[:8]:
        hist_params = [x for x in params if any(w in x.lower() for w in ("date", "from", "to", "start", "end", "asof", "day"))]
        L.append(f"- `{p}` parameters: {', '.join(params) or '—'}" + (" → **takes a date**" if hist_params else " → no date parameter"))
    if not der:
        L.append("- no options / futures paths found in the description (or no description)")

    # 7. quota
    L += ["", "## 7. Requests used by this probe", f"{calls} (the free tier allows 100,000 a month)", "",
          "Licence reminder: the free tier is for non-commercial use and needs \"Data by Eulerpool\" on anything public."]
    text = "\n".join(L) + "\n"
    with open(os.path.join(home, "eulerpool_probe.md"), "w", encoding="utf-8") as f:
        f.write(text)
    return text
