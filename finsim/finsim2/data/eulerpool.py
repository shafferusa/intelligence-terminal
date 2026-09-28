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
import urllib.parse
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


UA = "FinSim2/1.0 (personal research client; python)"    # honest client name: Python's default UA is often filtered


def _get(path: str, key: str, auth: str = "header") -> Tuple[Optional[int], object, str]:
    """(HTTP status, JSON payload or None, a short readable note on an error body). auth = "header" (Bearer) or "query"
    (?token=, Eulerpool's other documented form; errors never carry the URL, so the key is not logged either way)."""
    url = path if path.startswith("http") else BASE + path
    headers = {"Accept": "application/json", "User-Agent": UA}
    if auth == "query":
        url += ("&" if "?" in url else "?") + "token=" + urllib.parse.quote(key, safe="")
    else:
        headers["Authorization"] = f"Bearer {key}"
    try:
        body = http_get(url, headers, timeout=45)
    except FetchError as e:
        raw = (e.body or b"").decode("utf-8", "replace")
        try:
            payload = json.loads(raw) if raw else None
        except ValueError:
            payload = None
        return e.status, payload, _note(raw, payload)
    try:
        return 200, json.loads(body.decode("utf-8", "replace")), ""
    except ValueError:
        return 200, None, "not JSON"


def _note(raw: str, payload) -> str:
    """What an error body says, in a few words (untrusted: truncated, one line, HTML reduced to its title)."""
    import re
    if isinstance(payload, dict):
        msg = payload.get("error") or payload.get("message") or payload.get("detail") or payload
        return "JSON: " + re.sub(r"\s+", " ", str(msg))[:160]
    low = raw.lower()
    if "<html" in low or "<!doctype" in low:
        t = re.search(r"<title[^>]*>(.*?)</title>", raw, re.I | re.S)
        title = re.sub(r"\s+", " ", t.group(1)).strip()[:80] if t else ""
        return "HTML page" + (f' "{title}"' if title else "") + (" (Cloudflare)" if "cloudflare" in low else "")
    return re.sub(r"\s+", " ", raw)[:160] if raw else "empty body"


def diagnose(notes: List[Tuple[str, Optional[int], str]]) -> str:
    """Why every access attempt failed, from what the server said."""
    text = " ".join(n for _, _, n in notes).lower()
    if any(n.startswith("HTML page") for _, _, n in notes) and not any(n.startswith("JSON") for _, _, n in notes):
        return ("the requests were stopped before the API (an HTML page, not an API answer: a firewall or bot check), so "
                "the key was never checked. Try again later or from another network; if it persists, ask Eulerpool support "
                "whether API access from scripts needs anything beyond the key")
    if any(w in text for w in ("plan", "subscription", "upgrade", "tier")):
        return "the key works but these endpoints are outside the free plan"
    if any(w in text for w in ("token", "key", "unauthor", "invalid", "forbidden", "activate", "verify", "confirm")):
        return ("the key itself is refused: confirm the registration e-mail, check the key on eulerpool.com/developers "
                "(copy it again, no spaces or quotes), then re-run")
    return "every attempt failed without a readable reason (see the table)"


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

    mode = "header"

    def get(path):
        nonlocal calls
        calls += 1
        time.sleep(MIN_INTERVAL)
        return _get(path, key, mode)

    # access: the documented header form first, then other endpoints, then the documented ?token= form
    L += ["## Access", "| Attempt | HTTP | Server said |", "|---|---|---|"]
    attempts = [("header", "/api/1/equity/overview/AAPL", "overview by ticker, Bearer header"),
                ("header", f"/api/1/equity/profile/{APPLE}", "profile by ISIN, Bearer header"),
                ("header", "/api/1/forex/list", "forex list, Bearer header"),
                ("query", f"/api/1/equity/overview/{APPLE}", "overview by ISIN, ?token= form")]
    notes, ok = [], None
    for auth, path, label in attempts:
        calls += 1
        time.sleep(MIN_INTERVAL)
        st, body, note = _get(path, key, auth)
        L.append(f"| {label} | {st} | {'ok, fields: ' + _keys(body) if st == 200 else note} |")
        notes.append((label, st, note))
        if st == 200:
            ok = auth
            break
    if ok is None:
        L += ["", f"**No access: {diagnose(notes)}.** Nothing else was tried ({calls} requests)."]
        text = "\n".join(L) + "\n"
        with open(os.path.join(home, "eulerpool_probe.md"), "w", encoding="utf-8") as f:
            f.write(text)
        return text
    mode = ok
    L.append(f"Access works with the {'Bearer header' if ok == 'header' else '?token= form'}; the probe uses it from here on.")
    try:
        spec_url = BASE + SPEC_URL + (("?token=" + urllib.parse.quote(key, safe="")) if mode == "query" else "")
        raw = http_get(spec_url, {"User-Agent": UA, "Accept": "application/yaml, text/yaml, */*",
                                  **({"Authorization": f"Bearer {key}"} if mode == "header" else {})}, timeout=60)
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
        st, body, _n = get(f"/api/1/equity/overview/{isin}")
        ok = st == 200 and bool(body)
        alive += ok
        L.append(f"| {name} {isin} | {st} | {_keys(body) if ok else '—'} |")
    L.append(f"**{alive} of {len(DEAD)} delisted companies answer.**")

    section = None
    for sec, label, path, meaning in CHECKS:
        if sec != section:
            L += ["", f"## {sec}", "| Test | HTTP | What came back | Fields | A pass would be |", "|---|---|---|---|---|"]
            section = sec
        st, body, note = get(path)
        if st == 200:
            L.append(f"| {label} | 200 | {_span(body)} | {_keys(body)} | {meaning} |")
        else:
            L.append(f"| {label} | {st} | {note[:90] or '—'} | — | {meaning} |")
        say(f"{label}: HTTP {st}")

    L += ["", "## Requests used", f"{calls} (the free tier allows 100,000 a month)", "",
          "Licence: non-commercial use; \"Data by Eulerpool\" on anything public."]
    text = "\n".join(L) + "\n"
    with open(os.path.join(home, "eulerpool_probe.md"), "w", encoding="utf-8") as f:
        f.write(text)
    return text


# ================================================================== collector (research only)
# The 2026-09-28 probe found real history in five endpoints; only these are collected. Everything else the free tier
# offers either lacks history (options, the futures gap, survivorship: Lehman / Enron absent, SVB without prices) or
# adds nothing FinSim2 lacks (fundamentals without filing dates, Fama-French from 2013). Data by Eulerpool; non-commercial.
EST, SURPRISE, GRADES, TARGETS, VIXFUT = "ep_estimates", "ep_surprises", "ep_grades", "ep_targets", "vix_futures"
DATASETS = [EST, SURPRISE, GRADES, TARGETS, VIXFUT]
LAST_SPAN = "eulerpool:depth"


def _day(v) -> Optional[str]:
    s = str(v or "")[:10]
    try:
        _dt.date.fromisoformat(s)
        return s
    except ValueError:
        return None


def _plus(d: str, n: int = 1) -> str:
    return (_dt.date.fromisoformat(d) + _dt.timedelta(days=n)).isoformat()


def _num(v) -> Optional[float]:
    from . import num
    return num(v)


def _field(s) -> Optional[str]:
    import re
    s = re.sub(r"[^A-Za-z0-9_]", "", str(s or ""))[:40]
    return s or None


def parse_estimates(rows, asset: str) -> List[tuple]:
    """Point-in-time consensus snapshots: (asset, as-of date, "<field>@<period yyyy-mm>", value, published next day)."""
    out = {}
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        d, per, f, v = _day(r.get("as_of_date")), _day(r.get("period")), _field(r.get("field")), _num(r.get("value"))
        if d and per and f and v is not None:
            out[(d, f"{f}@{per[:7]}")] = v
    return [(asset, d, f, v, _plus(d)) for (d, f), v in sorted(out.items())]


def parse_surprises(rows, asset: str, releases: List[str]) -> List[tuple]:
    """EPS vs the analyst consensus per fiscal period (date = period end), published the day after the company's release
    (SEC 8-K item 2.02 within 120 days of the period end) or, without one, 45 days after a quarter / 90 after fiscal Q4."""
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        d = _day(r.get("date"))
        act, est = _num(r.get("epsActual")), _num(r.get("epsEstimate"))
        if not d or act is None or est is None:
            continue
        lim = _plus(d, 120)
        rel = sorted(p for p in releases if d < p <= lim)
        pub = _plus(rel[0]) if rel else _plus(d, 90 if _num(r.get("quarter")) == 4 else 45)
        vals = {"eps_consensus": est, "eps_actual": act, "eps_surprise": act - est, "eps_surprise_pct": _num(r.get("surprisePercent"))}
        out += [(asset, d, f, v, pub) for f, v in vals.items() if v is not None]
    return out


GRADE = {"upgrade": "grade_up", "downgrade": "grade_down", "init": "grade_init", "initiate": "grade_init", "initiated": "grade_init"}


def parse_grades(rows, asset: str) -> List[tuple]:
    """Dated rating actions, counted per day: upgrades, downgrades, initiations, others (maintain / reiterate)."""
    by: Dict[Tuple[str, str], float] = {}
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        d = _day(r.get("date"))
        if not d:
            continue
        f = GRADE.get(str(r.get("action") or "").strip().lower(), "grade_other")
        by[(d, f)] = by.get((d, f), 0.0) + 1.0
    return [(asset, d, f, v, _plus(d)) for (d, f), v in sorted(by.items())]


def parse_targets(rows, asset: str) -> List[tuple]:
    out = {}
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        d = _day(r.get("last_updated"))
        if not d:
            continue
        for src, f in (("target_mean", "pt_mean"), ("target_median", "pt_median"), ("target_high", "pt_high"), ("target_low", "pt_low"),
                       ("num_analysts", "pt_n")):
            v = _num(r.get(src))
            if v is not None:
                out[(d, f)] = v
    return [(asset, d, f, v, _plus(d)) for (d, f), v in sorted(out.items())]


def parse_vix_futures(rows) -> List[tuple]:
    """VIX futures by expiry, ranked per day: vx1 = nearest (value, days to maturity)."""
    by: Dict[str, List[Tuple[float, float]]] = {}
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        d, days, v = _day(r.get("date")), _num(r.get("maturity_days")), _num(r.get("value"))
        if d and days is not None and v is not None and v > 0:
            by.setdefault(d, []).append((days, v))
    out = []
    for d, pts in sorted(by.items()):
        for k, (days, v) in enumerate(sorted(pts)[:8], start=1):
            out += [("vix:futures", d, f"vx{k}", v, _plus(d)), ("vix:futures", d, f"vx{k}_days", days, _plus(d))]
    return out


def symbols(store) -> List[str]:
    eq = [a["id"] for a in store.assets("EQUITY") if a.get("currency", "USD") == "USD" and "." not in str(a.get("yahoo") or "")]
    return eq


def refresh(store, progress=None, key: Optional[str] = None, limit: Optional[int] = None) -> dict:
    """Weekly: estimate snapshots, consensus surprises, rating actions and price targets for the US stocks, and the VIX
    futures curve. A stock without coverage (404) is counted, not an error. Stops at the first refused key."""
    from .calendar import earnings_history
    say = progress or (lambda m: None)
    key = key or env_key(KEY_ENV)
    if not key:
        why = f"{KEY_ENV} is still the placeholder text" if placeholder_key(KEY_ENV) else f"set {KEY_ENV}"
        return {"skipped": f"{why} (free key: https://eulerpool.com/developers/register)"}
    n, errors, missing, calls = 0, [], 0, 0

    def get(path):
        nonlocal calls
        calls += 1
        time.sleep(MIN_INTERVAL)
        return _get(path, key)

    st, body, note = get("/api/1/market/vix/term-structure?days=365")
    if st in (401, 403):
        return {"rows": 0, "errors": [f"Eulerpool refused the key (HTTP {st}: {note})"]}
    if st == 200:
        rows = parse_vix_futures(body)
        if rows:
            store.put_alt(VIXFUT, rows)
            n += len(rows)
    names = symbols(store)[:limit] if limit else symbols(store)
    for k, aid in enumerate(names):
        sym = aid.replace("-", ".")
        releases = [p for _, _, p in earnings_history(store, aid)]
        for path, parse, ds in ((f"/api/1/equity/pit/estimates/{sym}?limit=5000", lambda b: parse_estimates(b, aid), EST),
                                (f"/api/1/calendar/earnings-surprises/{sym}", lambda b: parse_surprises(b, aid, releases), SURPRISE),
                                (f"/api/1/equity/analyst-grades/{sym}?limit=1000", lambda b: parse_grades(b, aid), GRADES),
                                (f"/api/1/equity-extended/price-target-history/{sym}", lambda b: parse_targets(b, aid), TARGETS)):
            st, body, note = get(path)
            if st == 200:
                rows = parse(body)
                if rows:
                    store.put_alt(ds, rows)
                    n += len(rows)
            elif st == 404:
                missing += 1
            elif st in (401, 403) and not n:
                return {"rows": 0, "errors": [f"Eulerpool refused the key (HTTP {st}: {note})"]}
            else:
                errors.append(f"{sym} {ds}: HTTP {st} {note[:60]}")
        if (k + 1) % 50 == 0:
            say(f"Eulerpool {k + 1}/{len(names)} stocks ({calls} requests)")
    depth = {ds: store._q("SELECT COUNT(*) AS n, COUNT(DISTINCT asset_id) AS a, MIN(date) AS lo, MAX(date) AS hi FROM alt_data WHERE dataset = ?",
                          (ds,))[0] for ds in DATASETS}
    depth = {ds: {"rows": r["n"], "assets": r["a"], "first": r["lo"], "last": r["hi"]} for ds, r in depth.items()}
    store.kv_set(LAST_SPAN, depth)
    for ds, r in depth.items():
        say(f"{ds}: {r['rows']} rows, {r['assets']} series, {r['first'] or '—'} → {r['last'] or '—'}")
    say(f"Eulerpool: {calls} requests (free tier: 100,000 a month); {missing} not covered")
    return {"rows": n, "requests": calls, "not_covered": missing, "errors": errors, "depth": depth}
