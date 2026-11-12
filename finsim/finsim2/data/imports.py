"""Importers for licensed data (analyst estimates, historical options) -> point-in-time alt_data datasets.

FinSim2 holds no licence for these; when you buy one (FactSet, LSEG I/B/E/S, Zacks; ORATS, Cboe DataShop,
OptionMetrics), export CSV and import it:

    python -m finsim2 import-estimates estimates.csv --source ibes
    python -m finsim2 import-options options.csv --source orats

``analyst_estimates`` — one row per asset, as-of date (the day the consensus was observed — point in time), horizon and
metric. Required columns (case-insensitive; common vendor names are recognised, see ``EST_ALIASES``):
    ticker, asof, horizon (FQ1 / FQ2 / FY1 / FY2 — next quarter … next-but-one fiscal year), metric (EPS / REVENUE), mean
optional: median, high, low, stdev, n_analysts, up_30d, down_30d (revisions in the last 30 days), published.
Stored fields: ``<metric>_<horizon>_<stat>`` (e.g. ``eps_fq1_mean``, ``revenue_fy1_n_analysts``).

``options_summary`` — one row per asset and trading day. Required: ticker, date; any of
    iv30, iv60, iv90 (at-the-money implied volatility), skew25 (25-delta put IV − 25-delta call IV, 30 days),
    put_volume, call_volume, put_oi, call_oi, iv_rank
(``OPT_ALIASES`` maps ORATS-style names such as iv30d / pVolu / cOi).

Publication: ``published`` when the file has it, otherwise the next calendar day (end-of-day vendor data is not usable
at the same close). A row whose ticker is not an asset in the store is counted and skipped, never guessed.
Files are untrusted: parsed with csv, numbers through ``num``; nothing is evaluated.
"""
from __future__ import annotations

import csv
import datetime as _dt
from typing import Dict, Iterable, List, Optional

from . import num

ESTIMATES = "analyst_estimates"
OPTIONS = "options_summary"
HORIZONS = {"FQ1", "FQ2", "FY1", "FY2"}
METRICS = {"EPS": "eps", "REVENUE": "revenue", "SALES": "revenue"}
EST_ALIASES = {"symbol": "ticker", "ticker_symbol": "ticker", "date": "asof", "as_of": "asof", "statpers": "asof",
               "fpi": "horizon", "period": "horizon", "measure": "metric", "meanest": "mean", "medest": "median",
               "highest": "high", "lowest": "low", "stdev": "stdev", "numest": "n_analysts", "numup": "up_30d", "numdown": "down_30d",
               "estimate_count": "n_analysts", "consensus_mean": "mean"}
EST_STATS = ("mean", "median", "high", "low", "stdev", "n_analysts", "up_30d", "down_30d")
IBES_FPI = {"6": "FQ1", "7": "FQ2", "1": "FY1", "2": "FY2"}
OPT_ALIASES = {"symbol": "ticker", "tradedate": "date", "trade_date": "date", "iv30d": "iv30", "iv60d": "iv60", "iv90d": "iv90",
               "pvolu": "put_volume", "cvolu": "call_volume", "poi": "put_oi", "coi": "call_oi", "ivrank": "iv_rank",
               "iv_rank_1y": "iv_rank", "skew_25d": "skew25"}
OPT_FIELDS = ("iv30", "iv60", "iv90", "skew25", "put_volume", "call_volume", "put_oi", "call_oi", "iv_rank")


def _date(s) -> Optional[str]:
    s = str(s or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%m/%d/%Y"):
        try:
            return _dt.datetime.strptime(s if fmt != "%Y%m%d" else s[:8], fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _norm(row: dict, aliases: Dict[str, str]) -> dict:
    out = {}
    for k, v in row.items():
        key = str(k or "").strip().lower()
        out[aliases.get(key, key)] = v
    return out


def _asset_ids(store) -> Dict[str, str]:
    m = {}
    for a in store.assets():
        m[a["id"].upper()] = a["id"]
        if a.get("yahoo"):
            m[str(a["yahoo"]).upper()] = a["id"]
        m[a["id"].upper().replace("-", ".")] = a["id"]
    return m


def _pub(row: dict, d: str) -> str:
    p = _date(row.get("published"))
    return p if p and p >= d else (_dt.date.fromisoformat(d) + _dt.timedelta(days=1)).isoformat()


def estimate_rows(lines: Iterable[str], ids: Dict[str, str]) -> Dict[str, object]:
    rows: List[tuple] = []
    skipped = {"unknown_ticker": 0, "bad_row": 0}
    for raw in csv.DictReader(lines):
        r = _norm(raw, EST_ALIASES)
        a = ids.get(str(r.get("ticker") or "").strip().upper())
        d = _date(r.get("asof"))
        hz = str(r.get("horizon") or "").strip().upper()
        hz = IBES_FPI.get(hz, hz)
        met = METRICS.get(str(r.get("metric") or "").strip().upper())
        if not a:
            skipped["unknown_ticker"] += 1
            continue
        if not d or hz not in HORIZONS or not met:
            skipped["bad_row"] += 1
            continue
        pub = _pub(r, d)
        for st in EST_STATS:
            v = num(r.get(st))
            if v is not None:
                rows.append((a, d, f"{met}_{hz.lower()}_{st}", v, pub))
    return {"rows": rows, "skipped": skipped}


def option_rows(lines: Iterable[str], ids: Dict[str, str]) -> Dict[str, object]:
    rows: List[tuple] = []
    skipped = {"unknown_ticker": 0, "bad_row": 0}
    for raw in csv.DictReader(lines):
        r = _norm(raw, OPT_ALIASES)
        a = ids.get(str(r.get("ticker") or "").strip().upper())
        d = _date(r.get("date"))
        if not a:
            skipped["unknown_ticker"] += 1
            continue
        if not d:
            skipped["bad_row"] += 1
            continue
        pub = _pub(r, d)
        got = 0
        for f in OPT_FIELDS:
            v = num(r.get(f))
            if v is not None:
                rows.append((a, d, f, v, pub))
                got += 1
        if not got:
            skipped["bad_row"] += 1
    return {"rows": rows, "skipped": skipped}


def import_file(store, path: str, kind: str, source: str = "import") -> dict:
    """kind 'estimates' or 'options'. Returns counts; rows are stored under the dataset for that kind."""
    ids = _asset_ids(store)
    with open(path, newline="", encoding="utf-8-sig") as fh:
        res = (estimate_rows if kind == "estimates" else option_rows)(fh, ids)
    ds = ESTIMATES if kind == "estimates" else OPTIONS
    if res["rows"]:
        store.put_alt(ds, res["rows"])
    log = store.kv_get(f"imports:{ds}") or []
    log.append({"file": path.replace("\\", "/").split("/")[-1], "source": source, "rows": len(res["rows"]),
                "skipped": res["skipped"], "imported": _dt.datetime.now().isoformat(timespec="seconds")})
    store.kv_set(f"imports:{ds}", log[-50:])
    return {"dataset": ds, "rows": len(res["rows"]), "skipped": res["skipped"]}
