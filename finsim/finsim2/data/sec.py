"""SEC EDGAR companyfacts -> point-in-time fundamentals (keyed by filing date).

Rules: the LoganTerminal User-Agent on every request, at most ~8 requests/second (0.12 s between calls, enforced
process-wide). The JSON is untrusted and parsed defensively.

Stored row: ``{concept, period_end, filed, value, form, fp}`` with concept one of
``eps, revenue, net_income`` (flows) or ``equity, shares`` (instants).

For flows ``fp`` describes the *fact's* period, not just the filing: ``"FY"`` for a ~12-month value and
``"Q1".."Q4"`` for a ~3-month value (a 3-month value inside a 10-K is Q4). Year-to-date (6/9-month) values are
dropped. For instants ``fp`` is the filing's fiscal period.
"""
from __future__ import annotations

import datetime as _dt
import json
import threading
import time

from . import GOV_UA, FetchError, http_get, num

URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
MIN_INTERVAL = 0.12  # seconds between SEC requests (<= 10 requests / second)
FORMS = {"10-Q", "10-K", "10-Q/A", "10-K/A"}
FLOW_CONCEPTS = ("eps", "revenue", "net_income")
INSTANT_CONCEPTS = ("equity", "shares")
STALE_DAYS = 550  # a value whose period ended longer ago than this is no longer "current"

# concept -> [(taxonomy, tag, unit)] in priority order
TAGS = {
    "eps": [("us-gaap", "EarningsPerShareDiluted", "USD/shares")],
    "revenue": [("us-gaap", "Revenues", "USD"),
                ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax", "USD"),
                ("us-gaap", "SalesRevenueNet", "USD")],
    "net_income": [("us-gaap", "NetIncomeLoss", "USD")],
    "equity": [("us-gaap", "StockholdersEquity", "USD")],
    "shares": [("us-gaap", "CommonStockSharesOutstanding", "shares"),
               ("dei", "EntityCommonStockSharesOutstanding", "shares")],
}


class SecError(FetchError):
    pass


_rate_lock = threading.Lock()
_last_call = [0.0]


def _get(url: str) -> bytes:
    return http_get(url, {"User-Agent": GOV_UA, "Accept": "application/json"}, timeout=60)


def _throttle():
    with _rate_lock:
        wait = MIN_INTERVAL - (time.monotonic() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.monotonic()


def fetch_companyfacts(cik: int) -> dict:
    cik = int(cik)
    if not 0 < cik < 10 ** 10:
        raise SecError("invalid CIK")
    _throttle()
    try:
        raw = _get(URL.format(cik=cik))
    except FetchError as exc:
        raise SecError(f"CIK {cik}: {exc}", status=exc.status) from None
    try:
        payload = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        raise SecError(f"CIK {cik}: invalid JSON") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("facts"), dict):
        raise SecError(f"CIK {cik}: unexpected payload")
    return payload


TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


def lookup_cik(ticker: str) -> int | None:
    """CIK for a US-listed ticker from SEC's company_tickers.json (one request), or None."""
    if not isinstance(ticker, str) or not ticker.strip():
        return None
    _throttle()
    try:
        payload = json.loads(_get(TICKERS_URL).decode("utf-8", "replace"))
    except (FetchError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    want = {ticker.strip().upper(), ticker.strip().upper().replace("-", ".")}
    for v in payload.values():
        if isinstance(v, dict) and str(v.get("ticker", "")).upper() in want:
            try:
                return int(v.get("cik_str"))
            except (TypeError, ValueError):
                return None
    return None


def _date(s):
    try:
        return _dt.date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def extract(facts: dict) -> list[dict]:
    """Normalised fundamentals rows from a companyfacts payload (10-Q / 10-K and amendments only)."""
    root = facts.get("facts") if isinstance(facts, dict) else None
    if not isinstance(root, dict):
        return []
    best: dict[tuple, tuple] = {}  # (concept, end, filed) -> (rank, row)
    for concept, tags in TAGS.items():
        flow = concept in FLOW_CONCEPTS
        for prio, (tax, tag, unit) in enumerate(tags):
            node = root.get(tax)
            node = node.get(tag) if isinstance(node, dict) else None
            units = node.get("units") if isinstance(node, dict) else None
            items = units.get(unit) if isinstance(units, dict) else None
            if not isinstance(items, list):
                continue
            for it in items:
                if not isinstance(it, dict):
                    continue
                form = it.get("form")
                if form not in FORMS:
                    continue
                end, filed, val = _date(it.get("end")), _date(it.get("filed")), num(it.get("val"))
                if end is None or filed is None or val is None or filed < end:
                    continue
                fp = it.get("fp") if isinstance(it.get("fp"), str) else None
                if flow:
                    start = _date(it.get("start"))
                    if start is None:
                        continue
                    days = (end - start).days
                    if 340 <= days <= 380:
                        fp_out, kind_rank = "FY", 0
                    elif 75 <= days <= 105:
                        fp_out = "Q4" if (fp in (None, "FY") or form.startswith("10-K")) else fp
                        if fp_out not in ("Q1", "Q2", "Q3", "Q4"):
                            fp_out = "Q4" if form.startswith("10-K") else "Q"
                        kind_rank = 1
                    else:
                        continue  # year-to-date values
                else:
                    fp_out, kind_rank = (fp[:4] if fp else None), 0
                key = (concept, end.isoformat(), filed.isoformat())
                rank = (kind_rank, prio)
                row = {"concept": concept, "period_end": end.isoformat(), "filed": filed.isoformat(), "value": val,
                       "form": form, "fp": fp_out}
                if key not in best or rank < best[key][0]:
                    best[key] = (rank, row)
    return sorted((r for _, r in best.values()), key=lambda r: (r["concept"], r["filed"], r["period_end"]))


# ---------------------------------------------------------------------------------------------- point in time
def _flow_value(quarters: dict, annual: dict):
    """(ttm value, period end) from the known quarterly and annual values, or (None, None)."""
    q = dict(quarters)
    for e_s, f in annual.items():
        if e_s in q:
            continue
        e = _dt.date.fromisoformat(e_s)
        inside = [k for k in q if e - _dt.timedelta(days=300) < _dt.date.fromisoformat(k) <= e - _dt.timedelta(days=45)]
        if len(inside) == 3:
            q[e_s] = f - sum(q[k] for k in inside)  # Q4 = FY - (Q1 + Q2 + Q3)
    ttm, ttm_end = None, None
    ends = sorted(q)
    if len(ends) >= 4:
        last4 = ends[-4:]
        span = (_dt.date.fromisoformat(last4[-1]) - _dt.date.fromisoformat(last4[0])).days
        if 250 <= span <= 300:
            ttm, ttm_end = sum(q[k] for k in last4), last4[-1]
    if annual:
        fy_end = max(annual)
        if ttm is None or fy_end > ttm_end:
            return annual[fy_end], fy_end
    return ttm, ttm_end


def ttm_series(rows: list[dict], concept: str, price_dates) -> dict:
    """Point-in-time value of ``concept`` on each date in ``price_dates``.

    Only rows with ``filed <= date`` are visible on a date (no lookahead). For a period restated later, the latest
    filing visible on that date wins. Flows (eps, revenue, net_income) give the trailing-twelve-month sum of the last
    four quarters (Q4 derived as FY - Q1..Q3 when only the annual figure is filed), falling back to the latest
    annual value when it is more recent. Instants (equity, shares) give the latest reported balance.
    Returns ``{date: value or None}`` keyed by the objects passed in.
    """
    flow = concept in FLOW_CONCEPTS
    rel = []
    for r in rows:
        if r.get("concept") != concept or num(r.get("value")) is None:
            continue
        try:
            rel.append((str(r["filed"])[:10], str(r["period_end"])[:10], num(r["value"]), r.get("fp") or ""))
        except KeyError:
            continue
    rel.sort()
    keyed = sorted(((str(d)[:10], d) for d in price_dates), key=lambda x: x[0])
    out = {}
    quarters: dict[str, tuple] = {}   # end -> (filed, value)
    annual: dict[str, tuple] = {}
    instant: dict[str, tuple] = {}
    i, cur_val, cur_end, dirty = 0, None, None, False
    for ds, orig in keyed:
        while i < len(rel) and rel[i][0] <= ds:
            filed, end, val, fp = rel[i]
            i += 1
            target = instant if not flow else (annual if fp == "FY" else quarters)
            prev = target.get(end)
            if prev is None or filed >= prev[0]:
                target[end] = (filed, val)
                dirty = True
        if dirty:
            if flow:
                cur_val, cur_end = _flow_value({k: v[1] for k, v in quarters.items()},
                                               {k: v[1] for k, v in annual.items()})
            elif instant:
                cur_end = max(instant)
                cur_val = instant[cur_end][1]
            dirty = False
        if cur_val is None or cur_end is None:
            out[orig] = None
        else:
            age = (_dt.date.fromisoformat(ds) - _dt.date.fromisoformat(cur_end)).days
            out[orig] = cur_val if age <= STALE_DAYS else None
    return out


__all__ = ["fetch_companyfacts", "lookup_cik", "extract", "ttm_series", "SecError", "FLOW_CONCEPTS", "INSTANT_CONCEPTS"]
