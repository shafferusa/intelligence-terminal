"""The new data sources, together: one refresh entry point with a cadence per source, and a status summary.

    python -m finsim2 data                      # every source that is due (news: python -m finsim2 news)
    python -m finsim2 data sec cftc --force     # these sources now
    python -m finsim2 data --status

The daily loop (server.App.daily_learning) calls ``refresh_due`` after the close, so an installed FinSim2 keeps them
current without anyone running a command. A source whose host or key is unavailable is reported and skipped; it never
stops the others.
"""
from __future__ import annotations

import datetime as _dt
import time
from typing import Callable, Dict, List, Optional

LAST_KEY = "newdata:last"

# source -> (cadence in days, description)
SOURCES: Dict[str, tuple] = {
    "sec": (7, "SEC Form 4 insider transactions (quarterly data sets + recent filings) and 8-K event types"),
    "calendar": (7, "event calendar: FOMC, CPI / jobs / GDP / PPI / retail sales release days, Finnhub earnings dates"),
    "cftc": (7, "CFTC Commitments of Traders (legacy, disaggregated, financial futures)"),
    "eia": (7, "EIA weekly petroleum and natural-gas storage (needs EIA_API_KEY)"),
    "crypto": (1, "crypto derivatives: perpetual funding, premium, CME basis"),
    "news": (1, "news headlines: WSJ / MarketWatch public RSS feeds (Dow Jones, one GET per feed), research only (data/news.py)"),
    "cboe": (1, "Cboe volatility-index history: VIX9D, VIX6M, VVIX, SKEW (cdn.cboe.com), research only"),
    "options": (1, "daily option-chain snapshots -> IV30/60/90, 25-delta skew, put/call volume and OI (cdn.cboe.com; after 16:15 NY), research only"),
    "futures": (1, "commodity futures curves, contracts 1–4 (Yahoo contract months; EIA history to April 2024), research only"),
    "analyst": (7, "Finnhub analyst ratings snapshots and EPS surprises (needs FINNHUB_KEY), research only"),
    "eulerpool": (7, "Eulerpool: point-in-time estimate snapshots, consensus EPS surprises (1997 →), rating actions, price targets, "
                     "VIX futures curve (needs EULERPOOL_API_KEY; non-commercial, \"Data by Eulerpool\"), research only"),
}


def _runner(name: str) -> Callable:
    if name == "sec":
        from . import secevents as E

        def run(store, say):
            return {"events": E.refresh_events(store, say), "insider": E.refresh_insider(store, say),
                    "insider_recent": E.refresh_insider_recent(store, say)}
        return run
    if name == "calendar":
        from . import calendar as C
        return lambda store, say: C.refresh(store, say)
    if name == "cftc":
        from . import cftc
        return lambda store, say: cftc.refresh(store, say)
    if name == "eia":
        from . import eia
        return lambda store, say: eia.refresh(store, say)
    if name == "crypto":
        from . import cryptoderiv
        return lambda store, say: cryptoderiv.refresh(store, say)
    if name == "news":
        from . import news
        return lambda store, say: news.refresh(store, say)
    if name == "cboe":
        from . import cboe
        return lambda store, say: cboe.refresh_history(store, say)
    if name == "options":
        from . import cboe
        return lambda store, say: cboe.snapshot(store, say)
    if name == "futures":
        from . import futcurve
        return lambda store, say: futcurve.refresh(store, say)
    if name == "analyst":
        from . import analyst
        return lambda store, say: analyst.refresh(store, say)
    if name == "eulerpool":
        from . import eulerpool
        return lambda store, say: eulerpool.refresh(store, say)
    raise KeyError(name)


def due(store, today: Optional[str] = None) -> List[str]:
    today = today or _dt.date.today().isoformat()
    last = store.kv_get(LAST_KEY) or {}
    out = []
    for name, (days, _) in SOURCES.items():
        prev = last.get(name)
        if not prev or (_dt.date.fromisoformat(today) - _dt.date.fromisoformat(prev[:10])).days >= days:
            out.append(name)
    return out


def refresh(store, names: Optional[List[str]] = None, progress=None) -> Dict[str, dict]:
    say = progress or (lambda m: None)
    names = names or list(SOURCES)
    last = store.kv_get(LAST_KEY) or {}
    out: Dict[str, dict] = {}
    for name in names:
        t0 = time.time()
        try:
            res = _runner(name)(store, lambda m, n=name: say(f"[{n}] {m}"))
            state, note = _judge(res)
            out[name] = {"ok": state == "ok", "state": state, "seconds": round(time.time() - t0, 1), "result": res, "error": note}
            if state not in ("failed", "deferred"):     # a deferred source runs again at the next opportunity
                last[name] = _dt.date.today().isoformat()
                store.kv_set(LAST_KEY, last)
        except Exception as e:  # noqa: BLE001 — one source never stops the others
            out[name] = {"ok": False, "state": "failed", "error": f"{type(e).__name__}: {e}"}
        o = out[name]
        say(f"{name}: {o['state']}" + (f" — {o['error']}" if o.get("error") else ""))
    return out


def _judge(res) -> tuple:
    """('ok' | 'partial' | 'skipped' | 'deferred' | 'failed', note) from a source's result: nothing stored plus errors is a
    failure, not success; a missing key is 'skipped'; a source that cannot run yet today (option snapshots while the
    session is open) is 'deferred'."""
    if isinstance(res, dict) and res.get("deferred"):
        return "deferred", res["deferred"]
    parts = res.values() if isinstance(res, dict) and all(isinstance(v, dict) for v in res.values()) and res else [res]
    rows = sum(int(p.get("rows") or 0) + int(p.get("macro_rows") or 0) + int(p.get("fomc_days") or 0) + int(p.get("earnings_rows") or 0)
               for p in parts if isinstance(p, dict))
    errs = [e for p in parts if isinstance(p, dict) for e in (p.get("errors") or []) + (p.get("notes") or []) + (p.get("failed") or [])]
    skipped = [p.get("skipped") for p in parts if isinstance(p, dict) and p.get("skipped")]
    if skipped and not rows:
        return "skipped", "; ".join(skipped)
    if errs and not rows:
        return "failed", f"{len(errs)} errors, nothing stored — first: {errs[0]}"
    if errs:
        return "partial", f"{len(errs)} errors — first: {errs[0]}"
    return "ok", None


def refresh_due(store, progress=None) -> Dict[str, dict]:
    names = due(store)
    return refresh(store, names, progress) if names else {}


DATASETS = ["sec_insider", "sec_8k", "event_calendar", "earnings_calendar", "cftc_cot", "eia_weekly", "crypto_deriv",
            "analyst_estimates", "options_summary", "finra_shvol", "news_dj", "options_cboe", "futures_curve", "analyst_finnhub",
            "ep_estimates", "ep_surprises", "ep_grades", "ep_targets", "vix_futures"]
MACRO = ["CBOE_VIX9D", "CBOE_VIX6M", "CBOE_VVIX", "CBOE_SKEW"]


def status(store) -> List[dict]:
    rows = []
    for ds in DATASETS:
        r = store._q("SELECT COUNT(*) AS n, COUNT(DISTINCT asset_id) AS a, MIN(date) AS lo, MAX(date) AS hi, MAX(published) AS pub "
                     "FROM alt_data WHERE dataset = ?", (ds,))[0]
        rows.append({"dataset": ds, "rows": r["n"], "series": r["a"], "first": r["lo"], "last": r["hi"], "last_published": r["pub"]})
    for ms in MACRO:
        r = store._q("SELECT COUNT(*) AS n, MIN(date) AS lo, MAX(date) AS hi, MAX(published) AS pub FROM macro WHERE series = ?", (ms,))[0]
        rows.append({"dataset": ms, "rows": r["n"], "series": 1 if r["n"] else 0, "first": r["lo"], "last": r["hi"], "last_published": r["pub"]})
    return rows
