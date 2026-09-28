"""Eulerpool free-tier probe: test the claims before building on them (no data enters the research store).

    python -m finsim2 probe eulerpool          # needs EULERPOOL_API_KEY (free: eulerpool.com/developers/register)

Eulerpool advertises a free tier of 100,000 requests a month (non-commercial; "Data by Eulerpool" attribution in anything
public), 100+ years of history across stocks, fundamentals, options / futures, bonds, FX, commodities, crypto and macro,
and historical equity data that is **point in time and survivorship-bias free**. FinSim2 cannot reach the host from the
cloud research environment, so this probe runs on your machine and checks each claim with a handful of requests
(about 30 of the monthly 100,000), using only paths from Eulerpool's own API reference (llms-full.txt):

  * point-in-time analyst estimate snapshots, price-target history, dated rating actions, earnings surprises — the
    analyst-revision history FinSim2 lacks;
  * survivorship: delisted companies (Lehman, Enron, SVB, First Republic, Bed Bath & Beyond, Twitter) and their price
    histories, S&P 500 additions / removals;
  * point-in-time fundamentals: filing dates on as-reported and standardised statements;
  * options (the documented chain is today only), VVIX / SKEW / VIX9D and VIX futures (≤ 365 days);
  * futures settlements for the April 2024 gap in the EIA curves, short-interest history, Fama-French factors.

The API description (OpenAPI 3, YAML) is saved as ``eulerpool_openapi.yaml`` in the FinSim2 home for building a client.
The report is printed and saved as ``eulerpool_probe.md`` in the FinSim2 home; paste it back to decide what to build.
Responses are untrusted data: parsed with json, strings truncated, nothing evaluated. The key goes only in the
Authorization header and never into a message, file or log.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import time
from typing import Dict, List, Optional, Tuple

from . import FetchError, env_key, http_get, placeholder_key

KEY_ENV = "EULERPOOL_API_KEY"
BASE = "https://api.eulerpool.com"
SPEC_URL = "/api/1/documentation/yaml"          # the OpenAPI 3 description (YAML; saved, not parsed)
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
                elif k in ("timestamp", "t", "date", "open_time") and isinstance(y, (int, float)) and 1e11 < y < 1e13:
                    out.append(_dt.datetime.fromtimestamp(y / 1000, _dt.timezone.utc).date().isoformat())
                walk(y, d + 1)
        elif isinstance(x, list):
            for y in x:
                walk(y, d + 1)
    walk(v)
    return sorted(out)


def _ms(d: str) -> int:
    return int(_dt.datetime.fromisoformat(d).replace(tzinfo=_dt.timezone.utc).timestamp() * 1000)


def _span(body) -> str:
    ds = _dates_in(body)
    n = len(body) if isinstance(body, list) else (len(body.get("data") or []) if isinstance(body, dict) and isinstance(body.get("data"), list) else None)
    return (f"{n} rows; " if n is not None else "") + (f"{len(ds)} dated values, {ds[0]} → {ds[-1]}" if ds else "no dates")


def _keys(body) -> str:
    x = body[0] if isinstance(body, list) and body else body
    if isinstance(x, dict) and isinstance(x.get("data"), list) and x["data"]:
        x = x["data"][0]
    return ", ".join(str(k)[:28] for k in list(x)[:18]) if isinstance(x, dict) else _shape(body)


# (section, label, path, what a pass means) — every path is from Eulerpool's own reference (llms-full.txt)
CHECKS: List[Tuple[str, str, str, str]] = [
    ("Point-in-time analyst estimates", "AAPL snapshots", "/api/1/equity/pit/estimates/AAPL?limit=100",
     "many dated snapshots going back years = revision history (the #1 missing input)"),
    ("Point-in-time analyst estimates", "a mid-cap (DECK) snapshots", "/api/1/equity/pit/estimates/DECK?limit=100", "coverage beyond mega caps"),
    ("Point-in-time analyst estimates", "price-target history (weekly, up to 5 years)", "/api/1/equity-extended/price-target-history/US0378331005",
     "weekly snapshots back ~5 years"),
    ("Point-in-time analyst estimates", "dated rating actions", "/api/1/equity/analyst-grades/US0378331005?limit=50", "dated upgrades / downgrades"),
    ("Point-in-time analyst estimates", "earnings surprises", "/api/1/calendar/earnings-surprises/AAPL", "many quarters of actual vs estimate"),
    ("Survivorship", "S&P 500 additions / removals", "/api/1/equity-extended/index-history/sp500",
     "dated adds / removes back decades = survivorship-free index membership"),
    ("Survivorship", "Lehman price history 2007–08", f"/api/1/equity/quotes/US5249081002?startdate={_ms('2007-01-01')}&enddate={_ms('2008-12-31')}",
     "daily prices ending Sep 2008"),
    ("Survivorship", "Enron price history 2000–01", f"/api/1/equity/quotes/US2935611069?startdate={_ms('2000-01-01')}&enddate={_ms('2001-12-31')}",
     "daily prices ending late 2001"),
    ("Survivorship", "SVB Financial price history 2022–23", f"/api/1/equity/quotes/US78486Q1013?startdate={_ms('2022-01-01')}&enddate={_ms('2023-12-31')}",
     "daily prices ending Mar 2023"),
    ("Survivorship", "size of the stock list", "/api/1/equity/list?offset=0&limit=1", "the total count"),
    ("Point-in-time fundamentals", "as-reported 10-K with filing dates", "/api/1/equity-extended/financials-reported/US0378331005?form=10-K&limit=3",
     "filedDate on every filing (same information as SEC, which FinSim2 already has)"),
    ("Point-in-time fundamentals", "standardised income statement", "/api/1/equity/incomestatement/US0378331005",
     "a filing / publication date per period (without one: latest restated figures, not point in time)"),
    ("Options and volatility", "SPY option chain", "/api/1/market/options/SPY", "today only (no date parameter is documented)"),
    ("Options and volatility", "VVIX history (max 365 days)", "/api/1/market/cboe/indices?symbol=VVIX&days=365", "a year of VVIX / SKEW / VIX9D"),
    ("Options and volatility", "VIX futures term structure", "/api/1/market/vix/term-structure?days=365", "a year of VIX futures by expiry"),
    ("Futures curves", "CL settlements Apr–Jun 2024 (the EIA gap)", "/api/1/commodity/futures-settlements?product=CL&start_date=2024-04-01&end_date=2024-06-30&limit=5000",
     "per-contract settlements (contract month in each row) = the April 2024 → now gap can be filled"),
    ("Futures curves", "CL contango history", "/api/1/commodity/futures-curve/CL/history?days=1000", "how far back the curve analytics go"),
    ("Positioning", "AAPL short interest (up to 120 periods)", "/api/1/equity/short-interest-positions/US0378331005?limit=120",
     "bi-monthly history back ~5 years (FINRA public data covers only the last year)"),
    ("Factors", "Fama-French daily (max 5000 days)", "/api/1/analytics/fama-french?days=5000", "~20 years of daily factor returns"),
]


def probe(home: str, progress=None, key: Optional[str] = None) -> str:
    say = progress or (lambda m: None)
    key = key or env_key(KEY_ENV)
    if not key:
        why = f"{KEY_ENV} is still the placeholder text" if placeholder_key(KEY_ENV) else f"set {KEY_ENV}"
        return f"Eulerpool probe skipped: {why} (free key: https://eulerpool.com/developers/register)"
    L = ["# Eulerpool free-tier probe", "", f"Run {time.strftime('%Y-%m-%d %H:%M')} from this machine. Every path is from Eulerpool's "
         "own API reference; nothing is scraped (their terms forbid HTML scraping).", ""]
    calls = 0

    def get(path):
        nonlocal calls
        calls += 1
        time.sleep(MIN_INTERVAL)
        return _get(path, key)

    # the key, and the API description (saved for building a client)
    st, body, _ = get(f"/api/1/equity/overview/{APPLE}")
    L += ["## Key", f"Apple overview: HTTP {st}; fields: {_keys(body) if st == 200 else '—'}"]
    if st in (401, 403):
        L.append("**The key is refused** — check it at eulerpool.com/developers (nothing else was tried).")
        return "\n".join(L) + "\n"
    try:
        raw = http_get(BASE + SPEC_URL, {"Authorization": f"Bearer {key}", "Accept": "application/yaml, text/yaml, */*"}, timeout=60)
        calls += 1
        with open(os.path.join(home, "eulerpool_openapi.yaml"), "wb") as f:
            f.write(raw)
        n_paths = sum(1 for line in raw.decode("utf-8", "replace").splitlines() if line.startswith("  /"))
        L.append(f"API description saved to eulerpool_openapi.yaml in the FinSim2 home ({n_paths} paths).")
    except FetchError as e:
        L.append(f"API description: {e}")

    # survivorship: do delisted companies answer at all
    L += ["", "## Survivorship: companies that no longer trade", "| Company | HTTP | Fields |", "|---|---|---|"]
    alive = 0
    for name, isin in DEAD.items():
        st, body, _ = get(f"/api/1/equity/overview/{isin}")
        ok = st == 200 and bool(body)
        alive += ok
        L.append(f"| {name} {isin} | {st} | {_keys(body) if ok else '—'} |")
    L.append(f"**{alive} of {len(DEAD)} delisted companies answer.**")

    section = None
    for sec, label, path, meaning in CHECKS:
        if sec != section:
            L += ["", f"## {sec}", "| Test | HTTP | What came back | Fields | A pass would be |", "|---|---|---|---|---|"]
            section = sec
        st, body, _ = get(path)
        if st == 200:
            L.append(f"| {label} | 200 | {_span(body)} | {_keys(body)} | {meaning} |")
        else:
            err = body.get("error") if isinstance(body, dict) else None
            L.append(f"| {label} | {st} | {str(err)[:80] if err else '—'} | — | {meaning} |")
        say(f"{label}: HTTP {st}")

    L += ["", "## Requests used", f"{calls} (the free tier allows 100,000 a month)", "",
          "Licence: non-commercial use; \"Data by Eulerpool\" on anything public."]
    text = "\n".join(L) + "\n"
    with open(os.path.join(home, "eulerpool_probe.md"), "w", encoding="utf-8") as f:
        f.write(text)
    return text
