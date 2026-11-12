"""Importer for a historical options dump (SQLite) -> point-in-time alt_data dataset ``options_hist``. Research only.

Written for the free 2008–2025 dump found on GitHub (SaidBahaDev; SPY / QQQ / IWM with IVs and greeks, MIT licence
stated; SQLite files compressed with Zstandard, and Parquet by year). Python's standard library reads SQLite but not
Zstandard or Parquet, so decompress first (``zstd -d file.db.zst``, or 7-Zip with the ZS plugin on Windows) and import
the ``.db``. The schema is not assumed: ``--probe`` lists the tables and columns, and columns are matched by common names
(``ALIASES``; override with ``--map field=column``).

Each (underlying, session) chain is reduced to the same features as the live Cboe snapshots (data/cboe.py
``chain_features``: iv30 / iv60 / iv90, skew25, put / call volume and OI, spot, n_contracts). The chain itself is not
kept. Published = the next calendar day (end-of-day data). An underlying that is not an asset in the store is skipped.

**Quality gate** (SHAFFER_IV_PROTOCOL.md, fixed before import): for SPY, QQQ and IWM the dump's 30-day ATM IV must
correlate ≥ 0.90 in daily levels with VIX, VXN and RVX, with a mean absolute difference under 5 volatility points, and
cover ≥ 90% of the sessions 2008 – 2025. The result is stored under ``QUALITY_KEY``; research reads the dump only when
the gate passed (engine/newinfo.py ``iv_chain``).

The file is untrusted data: read with sqlite3 (read-only URI, parameterised queries; table and column names only from
the file's own schema, quoted), numbers through ``num``.
"""
from __future__ import annotations

import datetime as _dt
import math
import os
import re
import sqlite3
from typing import Dict, Iterable, List, Optional, Tuple

from . import num

DATASET = "options_hist"
QUALITY_KEY = "optionsdump:quality"
ALIASES: Dict[str, Tuple[str, ...]] = {
    "underlying": ("underlying", "underlying_symbol", "symbol", "root", "ticker", "act_symbol", "underlying_ticker"),
    "date": ("date", "quote_date", "trade_date", "tradedate", "quotedate", "asof", "as_of", "data_date"),
    "expiry": ("expiration", "expiry", "expiration_date", "expirationdate", "exdate", "exp_date", "maturity"),
    "strike": ("strike", "strike_price", "strikeprice", "k"),
    "right": ("type", "option_type", "call_put", "cp_flag", "right", "put_call", "putcall", "cp"),
    "bid": ("bid", "bid_price", "best_bid"),
    "ask": ("ask", "ask_price", "best_offer", "offer"),
    "iv": ("iv", "implied_volatility", "impl_volatility", "impliedvolatility", "implied_vol", "mid_iv", "ivmid"),
    "delta": ("delta",),
    "oi": ("open_interest", "openinterest", "oi"),
    "volume": ("volume", "vol", "trade_volume"),
    "spot": ("underlying_price", "underlying_last", "underlyingprice", "spot", "stock_price", "underlying_close", "s"),
    "contract": ("contract", "option_symbol", "contractsymbol", "occ_symbol", "symbol_occ", "option"),
}
REQUIRED = ("date", "iv")
GATE = {"SPY": "VIXCLS", "QQQ": "VXNCLS", "IWM": "RVXCLS"}
GATE_FROM, GATE_TO = "2008-01-01", "2025-12-31"


def _connect(path: str) -> sqlite3.Connection:
    uri = "file:" + os.path.abspath(path).replace("\\", "/") + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def probe(path: str, sample: int = 3) -> List[dict]:
    """Tables, columns, row counts and a few sample rows (for choosing the mapping)."""
    con = _connect(path)
    try:
        out = []
        for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"):
            cols = [r[1] for r in con.execute(f"PRAGMA table_info({_q(t)})")]
            n = con.execute(f"SELECT COUNT(*) FROM {_q(t)}").fetchone()[0]
            rows = [tuple(str(v)[:24] for v in r) for r in con.execute(f"SELECT * FROM {_q(t)} LIMIT ?", (sample,))]
            out.append({"table": t, "rows": n, "columns": cols, "mapping": mapping(cols), "sample": rows})
        return out
    finally:
        con.close()


def mapping(cols: List[str], override: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    low = {c.lower(): c for c in cols}
    m = {}
    for field, names in ALIASES.items():
        for n in names:
            if n in low:
                m[field] = low[n]
                break
    for k, v in (override or {}).items():
        if v in cols:
            m[k] = v
    return m


OCC = re.compile(r"([A-Z.]{1,6})\s*(\d{2})(\d{2})(\d{2})([CP])(\d{8})$")


def _date(v) -> Optional[str]:
    s = str(v or "").strip()
    if re.fullmatch(r"\d{8}", s):
        s = f"{s[:4]}-{s[4:6]}-{s[6:]}"
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return _dt.datetime.strptime(s[:10], fmt).date().isoformat()
        except ValueError:
            continue
    v2 = num(v)
    if v2 and v2 > 1e9:                                    # epoch seconds / milliseconds
        return _dt.datetime.fromtimestamp(v2 / (1000 if v2 > 1e11 else 1), _dt.timezone.utc).date().isoformat()
    return None


def _right(v) -> Optional[str]:
    s = str(v or "").strip().upper()[:1]
    return s if s in ("C", "P") else None


def contract(row: dict, m: Dict[str, str]) -> Optional[dict]:
    """One option from a row, or None."""
    exp, right, k, und = row.get(m.get("expiry")), _right(row.get(m.get("right"))), num(row.get(m.get("strike"))), row.get(m.get("underlying"))
    sym = row.get(m.get("contract")) if "contract" in m else None
    if sym and (exp is None or right is None or k is None):
        o = OCC.search(str(sym).strip().upper())
        if o:
            und = und or o.group(1)
            exp = exp or f"20{o.group(2)}-{o.group(3)}-{o.group(4)}"
            right = right or o.group(5)
            k = k if k is not None else int(o.group(6)) / 1000.0
    exp = _date(exp)
    if not exp or right is None or k is None or k <= 0:
        return None
    bid, ask = num(row.get(m.get("bid"))) or 0.0, num(row.get(m.get("ask"))) or 0.0
    quoted = (ask > 0 and ask >= bid) if "ask" in m else True
    return {"underlying": str(und or "").strip().upper(), "expiry": _dt.date.fromisoformat(exp), "right": right, "strike": k,
            "iv": num(row.get(m.get("iv"))), "delta": num(row.get(m.get("delta"))), "oi": num(row.get(m.get("oi"))) or 0.0,
            "volume": num(row.get(m.get("volume"))) or 0.0, "quoted": quoted, "spot": num(row.get(m.get("spot")))}


def _spot_lookup(store, asset: str) -> Dict[str, float]:
    return {r["date"]: r["close"] for r in store._q("SELECT date, close FROM prices WHERE asset_id = ?", (asset,)) if r["close"]}


def import_file(store, path: str, table: Optional[str] = None, override: Optional[Dict[str, str]] = None,
                underlying: Optional[str] = None, progress=None) -> dict:
    """Stream a table ordered by (underlying, date); one feature row per chain."""
    from .cboe import chain_features
    from .imports import _asset_ids
    say = progress or (lambda m: None)
    info = probe(path, 0)
    tabs = [t for t in info if (table is None or t["table"] == table) and all(f in t["mapping"] for f in REQUIRED)]
    if not tabs:
        return {"error": "no table with a date and an implied-volatility column (use --probe, then --table / --map)", "tables": info}
    t = max(tabs, key=lambda x: x["rows"])
    m = mapping(t["columns"], override)
    ids = _asset_ids(store)
    cols = sorted(set(m.values()))
    order = [m[k] for k in ("underlying", "date") if k in m]
    sql = f"SELECT {', '.join(_q(c) for c in cols)} FROM {_q(t['table'])}" + (f" ORDER BY {', '.join(_q(c) for c in order)}" if order else "")
    con = _connect(path)
    stats = {"table": t["table"], "mapping": m, "rows_read": 0, "chains": 0, "stored": 0, "skipped_unknown": set(), "bad_rows": 0}
    spots: Dict[str, Dict[str, float]] = {}
    cur_key, chain = None, []

    def flush():
        if not chain or cur_key is None:
            return
        und, d = cur_key
        aid = ids.get(und) or (ids.get(underlying.upper()) if underlying else None)
        if not aid:
            stats["skipped_unknown"].add(und)
            return
        ivs = sorted(c["iv"] for c in chain if c["iv"] and c["iv"] > 0)
        if ivs and ivs[len(ivs) // 2] > 3.0:
            for c in chain:
                c["iv"] = c["iv"] / 100.0 if c["iv"] else c["iv"]
        spot = next((c["spot"] for c in chain if c["spot"]), None)
        if not spot:
            if aid not in spots:
                spots[aid] = _spot_lookup(store, aid)
            spot = spots[aid].get(d)
        f = chain_features(spot, chain, d)
        if f:
            pub = (_dt.date.fromisoformat(d) + _dt.timedelta(days=1)).isoformat()
            store.put_alt(DATASET, [(aid, d, k, float(v), pub) for k, v in f.items() if v is not None and math.isfinite(v)])
            stats["stored"] += 1
        stats["chains"] += 1
        if stats["chains"] % 250 == 0:
            say(f"{stats['chains']} chains ({und} {d})")

    try:
        for vals in con.execute(sql):
            stats["rows_read"] += 1
            row = dict(zip(cols, vals))
            d = _date(row.get(m["date"]))
            c = contract(row, m)
            if not d or c is None:
                stats["bad_rows"] += 1
                continue
            key = (c["underlying"] or (underlying or "").upper(), d)
            if key != cur_key:
                flush()
                cur_key, chain = key, []
            chain.append(c)
        flush()
    finally:
        con.close()
    stats["skipped_unknown"] = sorted(stats["skipped_unknown"])[:20]
    return stats


def quality(store) -> dict:
    """The pre-registered quality gate; stored under QUALITY_KEY."""
    out = {"checked": _dt.date.today().isoformat(), "names": {}}
    ok_all = True
    for a, sid in GATE.items():
        iv = {d: v * 100.0 for _, d, f, v, _ in store.alt(DATASET, a) if f == "iv30" and GATE_FROM <= d <= GATE_TO}
        idx = dict(store.macro(sid, GATE_FROM, GATE_TO))
        sessions = [r["date"] for r in store._q("SELECT date FROM prices WHERE asset_id = ? AND date >= ? AND date <= ?", (a, GATE_FROM, GATE_TO))]
        common = sorted(set(iv) & set(idx))
        res = {"sessions": len(sessions), "dump_days": len(iv), "common": len(common),
               "coverage": len([d for d in sessions if d in iv]) / len(sessions) if sessions else 0.0}
        if len(common) >= 250:
            x, y = [iv[d] for d in common], [idx[d] for d in common]
            mx, my = sum(x) / len(x), sum(y) / len(y)
            sx = math.sqrt(sum((v - mx) ** 2 for v in x))
            sy = math.sqrt(sum((v - my) ** 2 for v in y))
            res["corr"] = sum((u - mx) * (v - my) for u, v in zip(x, y)) / (sx * sy) if sx and sy else None
            res["mad"] = sum(abs(u - v) for u, v in zip(x, y)) / len(x)
        res["passed"] = bool(res.get("corr") is not None and res["corr"] >= 0.90 and res.get("mad", 99) < 5.0 and res["coverage"] >= 0.90)
        out["names"][a] = res
        ok_all = ok_all and res["passed"]
    out["passed"] = ok_all
    store.kv_set(QUALITY_KEY, out)
    return out


def gate_passed(store) -> bool:
    q = store.kv_get(QUALITY_KEY) or {}
    return bool(q.get("passed"))
