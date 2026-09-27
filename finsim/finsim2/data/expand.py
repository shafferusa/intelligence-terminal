"""Widen the research universe to the N most liquid US-listed common stocks (e.g. 500 – 1,500).

    python -m finsim2 universe --expand 500          # rank, add the new equities, then `python -m finsim2 refresh`

1. Candidates: SEC's company_tickers_exchange.json (one request) — NYSE and Nasdaq common stocks; preferreds, units,
   warrants, rights, SPACs and funds are excluded by ticker shape and name.
2. Liquidity: each candidate's median daily dollar volume (close × volume) over the last three months from Yahoo's chart
   endpoint, one paced request per ticker, cached in the store so an interrupted ranking resumes (about an hour for the
   ~5,000 candidates the first time; a re-rank only asks for tickers not ranked in the last 30 days).
3. The top N not already in the universe are added as EQUITY assets with their CIK (fundamentals, insider and 8-K data
   work at once) and a sector from their SEC SIC code (``SIC_SECTOR``); the SIC description becomes the industry.

Survivorship: the list is today's listings ranked by today's liquidity, so stocks that were delisted, acquired or went
bankrupt are missing from the past. Every added asset carries ``meta.expanded = <date>``; research on the widened
universe must read its pre-selection history with that bias in mind (NEW_DATA_SOURCES.md).
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import time
from typing import Dict, List, Optional, Tuple

from . import BROWSER_UA, FetchError, http_get, num
from . import sec as S

SEC_LIST = "https://www.sec.gov/files/company_tickers_exchange.json"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=3mo&interval=1d"
CACHE_KEY = "expand:liquidity"
MIN_INTERVAL = 0.6
RERANK_DAYS = 30
_EXCLUDE_NAME = re.compile(r"\b(ACQUISITION|ACQUISITIONS|CAPITAL TRUST|ETF|FUND|FUNDS|TRUST UNITS|SPAC|WARRANT|RIGHTS|UNITS?)\b", re.I)
# exchange-traded trusts and funds that SEC lists like companies (IAU "iShares Gold Trust", GLDM "SPDR Gold MiniShares")
_FUND_NAME = re.compile(r"\b(ISHARES|SPDR|PROSHARES|DIREXION|GRAYSCALE|WISDOMTREE|VANECK|INVESCO DB|GLOBAL X|ABRDN|SPROTT PHYSICAL|"
                        r"TEUCRIUM|21SHARES|BITWISE|WISE ORIGIN|MINISHARES|(GOLD|SILVER|PLATINUM|PALLADIUM|BITCOIN|ETHER|ETHEREUM|"
                        r"SOLANA|XRP|OIL|GAS|COMMODITY) (TRUST|SHARES|FUND|ETF))\b", re.I)
FUND_SIC = {6221}                     # "commodity contracts brokers & dealers": what SEC files commodity and crypto trusts under


def is_fund(name: str, sic=None) -> bool:
    try:
        if sic is not None and int(sic) in FUND_SIC:
            return True
    except (TypeError, ValueError):
        pass
    return bool(_FUND_NAME.search(name or ""))


def candidates(payload) -> List[Tuple[str, int, str, str]]:
    """[(ticker, cik, name, exchange)] of plausible common stocks."""
    out = []
    fields = payload.get("fields") if isinstance(payload, dict) else None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not fields or not isinstance(data, list):
        return out
    ix = {f: i for i, f in enumerate(fields)}
    seen = set()
    for r in data:
        if not isinstance(r, list) or len(r) < len(fields):
            continue
        cik, name, tic, exch = r[ix["cik"]], str(r[ix["name"]] or ""), str(r[ix["ticker"]] or "").upper(), r[ix["exchange"]]
        if exch not in ("NYSE", "Nasdaq") or tic in seen:
            continue
        common = re.fullmatch(r"[A-Z]{1,4}", tic) or re.fullmatch(r"[A-Z]{5}", tic) and tic[-1] not in "WURQ" \
            or re.fullmatch(r"[A-Z]{1,4}-[AB]", tic)
        if not common or _EXCLUDE_NAME.search(name) or is_fund(name):
            continue
        try:
            out.append((tic, int(cik), name, exch))
        except (TypeError, ValueError):
            continue
        seen.add(tic)
    return out


def dollar_volume(payload) -> Optional[float]:
    """Median close × volume over the returned sessions (≥ 40 of them), from a Yahoo chart payload."""
    try:
        r = payload["chart"]["result"][0]
        q = r["indicators"]["quote"][0]
        closes, vols = q["close"], q["volume"]
    except (KeyError, IndexError, TypeError):
        return None
    dv = sorted(c * v for c, v in zip(closes or [], vols or []) if num(c) and num(v))
    if len(dv) < 40:
        return None
    return dv[len(dv) // 2]


# SEC SIC code ranges -> the universe's sector names (first match wins)
SIC_SECTOR: List[Tuple[int, int, str]] = [
    (1311, 1389, "Energy"), (2900, 2999, "Energy"), (5170, 5172, "Energy"),
    (2830, 2836, "Health Care"), (3841, 3851, "Health Care"), (5122, 5122, "Health Care"), (8000, 8099, "Health Care"),
    (3570, 3579, "Information Technology"), (3600, 3629, "Information Technology"), (3640, 3699, "Information Technology"),
    (3810, 3812, "Industrials"), (3820, 3829, "Information Technology"), (7370, 7379, "Information Technology"),
    (3630, 3639, "Consumer Discretionary"), (3711, 3716, "Consumer Discretionary"), (3751, 3751, "Consumer Discretionary"),
    (2840, 2844, "Consumer Staples"), (5140, 5159, "Consumer Staples"), (5400, 5499, "Consumer Staples"), (5912, 5912, "Consumer Staples"),
    (7311, 7319, "Communication Services"), (2700, 2799, "Communication Services"), (4800, 4899, "Communication Services"),
    (7810, 7829, "Communication Services"),
    (6798, 6798, "Real Estate"), (6500, 6599, "Real Estate"),
    (4900, 4999, "Utilities"),
    (6000, 6499, "Financials"), (6700, 6799, "Financials"),
    (100, 999, "Consumer Staples"), (1000, 1499, "Materials"), (1500, 1799, "Industrials"), (2000, 2199, "Consumer Staples"),
    (2200, 2399, "Consumer Discretionary"), (2400, 2499, "Materials"), (2500, 2599, "Consumer Discretionary"),
    (2600, 2699, "Materials"), (2800, 2899, "Materials"), (3000, 3099, "Materials"), (3100, 3199, "Consumer Discretionary"),
    (3200, 3399, "Materials"), (3400, 3569, "Industrials"), (3580, 3599, "Industrials"), (3700, 3799, "Industrials"),
    (3800, 3899, "Information Technology"), (3900, 3999, "Consumer Discretionary"), (4000, 4799, "Industrials"),
    (5000, 5199, "Industrials"), (5200, 5999, "Consumer Discretionary"), (7000, 7299, "Consumer Discretionary"),
    (7300, 7399, "Industrials"), (7500, 7999, "Consumer Discretionary"), (8200, 8299, "Consumer Discretionary"),
    (8100, 8999, "Industrials"),
]


def sector_of_sic(sic) -> Optional[str]:
    try:
        c = int(sic)
    except (TypeError, ValueError):
        return None
    return next((s for a, b, s in SIC_SECTOR if a <= c <= b), None)


def rank(store, cands: List[Tuple[str, int, str, str]], progress=None, max_requests: Optional[int] = None) -> Dict[str, list]:
    """{ticker: [ranked on, median dollar volume or None]} — cached; tickers ranked in the last 30 days are reused."""
    say = progress or (lambda m: None)
    cache: Dict[str, list] = store.kv_get(CACHE_KEY) or {}
    fresh = (_dt.date.today() - _dt.timedelta(days=RERANK_DAYS)).isoformat()
    todo = [c for c in cands if not (c[0] in cache and cache[c[0]][0] >= fresh)]
    if max_requests is not None:
        todo = todo[:max_requests]
    last = 0.0
    for k, (tic, _, _, _) in enumerate(todo):
        wait = MIN_INTERVAL - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        last = time.time()
        try:
            dv = dollar_volume(json.loads(http_get(YAHOO.format(sym=tic), {"User-Agent": BROWSER_UA, "Accept": "application/json"}, timeout=30).decode("utf-8", "replace")))
        except FetchError as e:
            if e.status == 429:                      # rate limited: stop, keep what we have, resume later
                say(f"Yahoo rate limit after {k} tickers — progress saved; run again later to continue")
                break
            dv = None
        except ValueError:
            dv = None
        cache[tic] = [_dt.date.today().isoformat(), dv]
        if k % 100 == 0:
            store.kv_set(CACHE_KEY, cache)
            say(f"liquidity ranking {k + 1}/{len(todo)}")
    store.kv_set(CACHE_KEY, cache)
    return cache


def expand(store, n: int, progress=None, max_requests: Optional[int] = None, dry_run: bool = False, allow_partial: bool = False) -> dict:
    """Add the top-n liquid common stocks that are not in the universe yet — only once every candidate has been ranked
    (a partial ranking is not a top-n), unless `allow_partial`."""
    say = progress or (lambda m: None)
    S._throttle()
    cands = candidates(json.loads(S._get(SEC_LIST).decode("utf-8", "replace")))
    say(f"{len(cands)} NYSE / Nasdaq common-stock candidates")
    have = {a["id"] for a in store.assets()} | {str(a.get("yahoo") or "") for a in store.assets()}
    cache = rank(store, cands, say, max_requests)
    done = sum(1 for t, _, _, _ in cands if t in cache)
    if done < len(cands) and not allow_partial:
        return {"candidates": len(cands), "ranked": done, "added": [], "complete": False,
                "note": f"liquidity ranked for {done} of {len(cands)} candidates — run again to continue (nothing added yet)"}
    fixed = reclassify_funds(store, dry_run)
    for f in fixed:
        say(f"reclassified {f['id']} as {f['asset_class']} ({f['reason']})")
    ranked = sorted(((cache[t][1], t, cik, name) for t, cik, name, _ in cands if t in cache and cache[t][1]), reverse=True)
    existing_equities = len(store.assets("EQUITY"))
    need = max(0, n - existing_equities)
    added, funds = [], []
    today = _dt.date.today().isoformat()
    for dv, t, cik, name in ranked:
        if len(added) >= need:
            break
        if t in have:
            continue
        S._throttle()
        try:
            sub = json.loads(S._get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json").decode("utf-8", "replace"))
        except (FetchError, ValueError):
            sub = {}
        sic = sub.get("sic") if isinstance(sub, dict) else None
        if is_fund(name, sic):
            funds.append(t)
            continue
        sector = sector_of_sic(sic) or "Unclassified"
        asset = {"id": t, "name": name.title() if name.isupper() else name, "asset_class": "EQUITY", "sector": sector,
                 "country": "United States", "currency": "USD", "yahoo": t, "cik": cik, "duration": None, "convexity": None,
                 "fred": None, "benchmark": "SPY",
                 "meta": {"expanded": today, "liquidity_usd": round(dv), "sic": sic, "industry": (sub.get("sicDescription") if isinstance(sub, dict) else None)}}
        if not dry_run:
            store.upsert_asset(asset)
        added.append({"id": t, "sector": sector, "liquidity_usd": round(dv)})
    ranked_n = sum(1 for v in cache.values() if v[1])
    return {"candidates": len(cands), "ranked": ranked_n, "equities_before": existing_equities, "added": added,
            "skipped_funds": funds, "reclassified": fixed,
            "complete": ranked_n + sum(1 for v in cache.values() if not v[1]) >= len(cands)}


def reclassify_funds(store, dry_run: bool = False) -> List[dict]:
    """Expanded 'equities' that are really exchange-traded trusts or funds (added before the fund filter existed) become
    ETF assets, like GLD and SLV, so they leave the equity cross-section."""
    out = []
    for a in store.assets("EQUITY"):
        m = a.get("meta") or {}
        if not m.get("expanded") or not is_fund(a.get("name") or "", m.get("sic")):
            continue
        metal = re.search(r"GOLD|SILVER|PLATINUM|PALLADIUM", a.get("name") or "", re.I)
        fixed = dict(a, asset_class="ETF", sector="Precious Metals" if metal else "Commodities",
                     meta=dict(m, reclassified=_dt.date.today().isoformat(), industry=m.get("industry")))
        if not dry_run:
            store.upsert_asset(fixed)
        out.append({"id": a["id"], "asset_class": "ETF", "reason": f"fund: {a.get('name')}"})
    return out
