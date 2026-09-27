"""CFTC Commitments of Traders -> point-in-time alt_data dataset ``cftc_cot``.

Source: the CFTC Public Reporting Environment (Socrata), no key needed:
    https://publicreporting.cftc.gov/resource/{dataset}.json
    legacy futures only       6dca-aqww   (non-commercial / commercial / non-reportable, every market, 1986 on)
    disaggregated futures     72hh-3qpy   (producer-merchant, swap dealers, managed money, other; physical markets, 2006 on)
    financial futures (TFF)   gpe5-46if   (dealers, asset managers, leveraged funds; financial markets, 2006 on)

Rows are stored under a contract key (``cot:<CFTC contract market code>``), one row per report date and field:
    open_interest, noncomm_long/short, comm_long/short (legacy);
    mm_long/short, pm_long/short, swap_long/short (disaggregated);
    lev_long/short, am_long/short, dealer_long/short (TFF).
``LINKS`` says which FinSim2 assets each contract informs and with which sign (long yen futures = short USDJPY).

Point in time: positions are as of Tuesday and released on Friday at 15:30 New York, so a report date's rows are
published on the Saturday after it (usable from the next session). During US government shutdowns the CFTC stopped
publishing and caught up weeks later; report dates inside those windows get the catch-up date (``SHUTDOWNS``).

Field names are matched by pattern, not exact spelling (the Socrata schemas carry quirks such as
``swap__positions_short_all``); every contract's market name is checked against the expected keyword before its rows
are stored, so a wrong code is reported, never silently mapped. Downloaded content is untrusted and parsed with json.
"""
from __future__ import annotations

import datetime as _dt
import json
import time
import urllib.parse
from typing import Dict, List, Optional, Tuple

from . import GOV_UA, FetchError, http_get, num

DATASET = "cftc_cot"
BASE = "https://publicreporting.cftc.gov/resource/{ds}.json"
REPORTS = {"legacy": "6dca-aqww", "disaggregated": "72hh-3qpy", "tff": "gpe5-46if"}
PAGE = 50000
MIN_INTERVAL = 0.5

# CFTC contract market code -> (keyword that must appear in the market name, report types to read)
CONTRACTS: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "067651": ("CRUDE OIL", ("legacy", "disaggregated")),
    "023651": ("NATURAL GAS", ("legacy", "disaggregated")),
    "111659": ("GASOLINE", ("legacy", "disaggregated")),
    "022651": ("HEATING OIL", ("legacy", "disaggregated")),       # NY Harbor ULSD
    "088691": ("GOLD", ("legacy", "disaggregated")),
    "084691": ("SILVER", ("legacy", "disaggregated")),
    "085692": ("COPPER", ("legacy", "disaggregated")),
    "076651": ("PLATINUM", ("legacy", "disaggregated")),
    "002602": ("CORN", ("legacy", "disaggregated")),
    "001602": ("WHEAT", ("legacy", "disaggregated")),
    "005602": ("SOYBEANS", ("legacy", "disaggregated")),
    "083731": ("COFFEE", ("legacy", "disaggregated")),
    "13874A": ("S&P 500", ("legacy", "tff")),
    "209742": ("NASDAQ", ("legacy", "tff")),
    "239742": ("RUSSELL", ("legacy", "tff")),
    "124603": ("DOW JONES", ("legacy", "tff")),
    "1170E1": ("VIX", ("legacy", "tff")),
    "042601": ("2-YEAR", ("legacy", "tff")),
    "044601": ("5-YEAR", ("legacy", "tff")),
    "043602": ("10-YEAR", ("legacy", "tff")),
    "020601": ("BOND", ("legacy", "tff")),
    "099741": ("EURO FX", ("legacy", "tff")),
    "097741": ("JAPANESE YEN", ("legacy", "tff")),
    "096742": ("BRITISH POUND", ("legacy", "tff")),
    "092741": ("SWISS FRANC", ("legacy", "tff")),
    "090741": ("CANADIAN DOLLAR", ("legacy", "tff")),
    "232741": ("AUSTRALIAN DOLLAR", ("legacy", "tff")),
    "112741": ("NZ DOLLAR", ("legacy", "tff")),
    "095741": ("MEXICAN PESO", ("legacy", "tff")),
    "098662": ("DOLLAR INDEX", ("legacy", "tff")),
    "133741": ("BITCOIN", ("legacy", "tff")),
}

# FinSim2 asset -> (contract code, sign): +1 when a long futures position is long the asset
LINKS: Dict[str, Tuple[str, int]] = {
    "WTI": ("067651", 1), "BRENT": ("067651", 1), "USO": ("067651", 1), "NATGAS": ("023651", 1), "UNG": ("023651", 1),
    "GOLD": ("088691", 1), "GLD": ("088691", 1), "SILVER": ("084691", 1), "SLV": ("084691", 1), "COPPER": ("085692", 1),
    "CPER": ("085692", 1), "PLATINUM": ("076651", 1), "CORN": ("002602", 1), "WHEAT": ("001602", 1), "SOYBEANS": ("005602", 1),
    "COFFEE": ("083731", 1), "SPX": ("13874A", 1), "SPY": ("13874A", 1), "VTI": ("13874A", 1), "NDX": ("209742", 1),
    "QQQ": ("209742", 1), "RUT": ("239742", 1), "IWM": ("239742", 1), "DJI": ("124603", 1), "DIA": ("124603", 1),
    "VXX": ("1170E1", 1), "UST2Y": ("042601", 1), "SHY": ("042601", 1), "UST5Y": ("044601", 1), "IEI": ("044601", 1),
    "UST10Y": ("043602", 1), "IEF": ("043602", 1), "UST30Y": ("020601", 1), "TLT": ("020601", 1),
    "EURUSD": ("099741", 1), "FXE": ("099741", 1), "USDJPY": ("097741", -1), "FXY": ("097741", 1), "GBPUSD": ("096742", 1),
    "FXB": ("096742", 1), "USDCHF": ("092741", -1), "USDCAD": ("090741", -1), "AUDUSD": ("232741", 1), "NZDUSD": ("112741", 1),
    "USDMXN": ("095741", -1), "DXY": ("098662", 1), "UUP": ("098662", 1), "BTC": ("133741", 1), "IBIT": ("133741", 1),
    "BITO": ("133741", 1),
}

# report-date windows whose reports were published late (government shutdowns) -> the date they became public.
# Conservative: every report date in the window is treated as public only once the catch-up was complete.
SHUTDOWNS = [("2013-09-30", "2013-10-22", "2013-11-08"),
             ("2018-12-24", "2019-01-29", "2019-03-08"),
             ("2025-09-30", "2025-11-18", "2026-01-23")]

# field name patterns: (stored field, report, tokens that must all appear, tokens that must not)
_EX = ("pct", "chg", "change", "conc", "spread", "old", "other", "trader")
FIELDS = [
    ("open_interest", "legacy", ("open_interest_all",), ("chg", "change", "old", "other", "pct")),
    ("noncomm_long", "legacy", ("noncomm", "long", "all"), _EX),
    ("noncomm_short", "legacy", ("noncomm", "short", "all"), _EX),
    ("comm_long", "legacy", ("comm_positions_long", "all"), _EX + ("noncomm",)),
    ("comm_short", "legacy", ("comm_positions_short", "all"), _EX + ("noncomm",)),
    ("mm_long", "disaggregated", ("m_money", "long"), _EX),
    ("mm_short", "disaggregated", ("m_money", "short"), _EX),
    ("pm_long", "disaggregated", ("prod_merc", "long"), _EX),
    ("pm_short", "disaggregated", ("prod_merc", "short"), _EX),
    ("swap_long", "disaggregated", ("swap", "long"), _EX),
    ("swap_short", "disaggregated", ("swap", "short"), _EX),
    ("lev_long", "tff", ("lev_money", "long"), _EX),
    ("lev_short", "tff", ("lev_money", "short"), _EX),
    ("am_long", "tff", ("asset_mgr", "long"), _EX),
    ("am_short", "tff", ("asset_mgr", "short"), _EX),
    ("dealer_long", "tff", ("dealer", "long"), _EX),
    ("dealer_short", "tff", ("dealer", "short"), _EX),
]


def published(report_date: str) -> str:
    """Tuesday positions are released Friday 15:30 New York -> usable from the Saturday (next session)."""
    for a, b, pub in SHUTDOWNS:
        if a <= report_date <= b:
            return pub
    d = _dt.date.fromisoformat(report_date)
    return (d + _dt.timedelta(days=(5 - d.weekday()) % 7 or 7)).isoformat()


def match_field(keys, report: str, want: str) -> Optional[str]:
    spec = next(f for f in FIELDS if f[0] == want and f[1] == report)
    _, _, must, mustnot = spec
    cands = [k for k in keys if all(t in k for t in must) and not any(t in k for t in mustnot)]
    return min(cands, key=len) if cands else None


def parse_rows(rows: List[dict], report: str, code: str) -> Tuple[List[tuple], Optional[str]]:
    """alt_data tuples for one contract and report type; the second value is an error (name mismatch)."""
    kw = CONTRACTS[code][0]
    out = []
    keys: Dict[str, Optional[str]] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = str(r.get("market_and_exchange_names") or "").upper()
        if kw not in name:
            return [], f"{code}: market name '{name[:60]}' does not contain '{kw}' — code not mapped"
        d = str(r.get("report_date_as_yyyy_mm_dd") or "")[:10]
        try:
            _dt.date.fromisoformat(d)
        except ValueError:
            continue
        if not keys:
            keys = {f[0]: match_field(r.keys(), report, f[0]) for f in FIELDS if f[1] == report}
        pub = published(d)
        for fld, k in keys.items():
            v = num(r.get(k)) if k else None
            if v is not None and v >= 0:
                out.append((f"cot:{code}", d, fld, v, pub))
    return out, None


def _get(url: str) -> bytes:
    return http_get(url, {"User-Agent": GOV_UA, "Accept": "application/json"}, timeout=90)


def fetch(report: str, code: str, since: str = "1986-01-01") -> List[dict]:
    rows: List[dict] = []
    offset = 0
    while True:
        q = {"$where": f"cftc_contract_market_code='{code}' AND report_date_as_yyyy_mm_dd >= '{since}T00:00:00'",
             "$order": "report_date_as_yyyy_mm_dd", "$limit": str(PAGE), "$offset": str(offset)}
        page = json.loads(_get(BASE.format(ds=REPORTS[report]) + "?" + urllib.parse.urlencode(q)).decode("utf-8", "replace"))
        if not isinstance(page, list):
            break
        rows += page
        if len(page) < PAGE:
            break
        offset += PAGE
        time.sleep(MIN_INTERVAL)
    return rows


def refresh(store, progress=None, since: Optional[str] = None) -> dict:
    """Every mapped contract and report type (incremental from the latest stored report date unless `since`)."""
    say = progress or (lambda m: None)
    have = store.alt_dates(DATASET)
    start = since or ((max(have) if have else "1986-01-01"))
    n, errors = 0, []
    for k, (code, (kw, reports)) in enumerate(CONTRACTS.items()):
        for rep in reports:
            try:
                rows = fetch(rep, code, start)
            except (FetchError, ValueError) as e:
                errors.append(f"{code} {rep}: {e}")
                continue
            tuples, err = parse_rows(rows, rep, code)
            if err:
                errors.append(err)
                continue
            if tuples:
                store.put_alt(DATASET, tuples)
            n += len(tuples)
            time.sleep(MIN_INTERVAL)
        say(f"CFTC COT {k + 1}/{len(CONTRACTS)} ({kw})")
    return {"contracts": len(CONTRACTS), "rows": n, "errors": errors, "since": start}
