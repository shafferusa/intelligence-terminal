"""Download whatever is stale into the research store.

``refresh(store)`` walks four task lists, each item isolated (an error is logged to ``fetch_log`` and reported in
the summary, never raised):

1. Yahoo daily bars for every asset with a ``yahoo`` symbol - incremental. The request starts a week before the
   last stored bar so the overlap can be compared with what is stored: if Yahoo has re-adjusted history since
   (split, or a dividend that rescales ``adj_close``), the whole history is re-downloaded and replaced, so returns
   never jump at the seam. No stored history -> full history.
2. FRED series (all of ``FRED_SERIES`` plus every synthetic asset's yield series). Revised (non-daily) series -
   CPI, unemployment, industrial production, M2, NFCI, the Fed balance sheet - are stored as **first releases with
   their publication dates** when ``FRED_API_KEY`` is set: the whole series is re-downloaded when it switches kind
   (so revised values cannot linger), incrementally afterwards (first releases never change). Without a key, and
   for daily market series (not revised in practice), the latest vintage is used - incremental with a 30-day
   overlap. A series already stored as first releases is left untouched when the key disappears rather than
   mixing revised values into it (reported under ``skipped``). The kind is recorded per series
   (``Store.macro_kind``).
3. Synthetic TREASURY / CORP_BOND total-return indices rebuilt from their stored FRED yield.
4. SEC companyfacts for EQUITY assets with a CIK, at most once every 7 days per asset.
"""
from __future__ import annotations

import datetime as _dt
import re
import time

from . import fred, sec, yahoo
from .universe import FRED_SERIES

YAHOO_OVERLAP_DAYS = 7
FRED_OVERLAP_DAYS = 30
SEC_MIN_AGE_DAYS = 7
REL_TOL = 1e-4
SYNTHETIC_CLASSES = ("TREASURY", "CORP_BOND")

_INSTRUMENT_CLASS = {"EQUITY": "EQUITY", "ETF": "ETF", "MUTUALFUND": "ETF", "INDEX": "INDEX", "CURRENCY": "FX",
                     "CRYPTOCURRENCY": "CRYPTO", "FUTURE": "FUTURE"}
_SYMBOL_RE = re.compile(r"^[A-Za-z0-9^=.\-]{1,24}$")


def _sleep(seconds: float):
    if seconds > 0:
        time.sleep(seconds)


def _today() -> _dt.date:
    return _dt.date.today()


# ----------------------------------------------------------------------------------------------- synthetic bonds
def _weekdays_between(a: _dt.date, b: _dt.date) -> int:
    """Number of weekdays in (a, b]."""
    days = (b - a).days
    if days <= 0:
        return 0
    full, rem = divmod(days, 7)
    n = full * 5
    wd = a.weekday()
    for k in range(1, rem + 1):
        if (wd + k) % 7 < 5:
            n += 1
    return n


def total_return_index(yields, duration: float, convexity: float, base: float = 100.0) -> list[tuple[str, float]]:
    """Constant-maturity total-return index from a yield series in percent.

    ``r_t = y_{t-1} * n_t / 252 - D * dy + 0.5 * C * dy^2`` with yields as decimals and ``n_t`` the number of
    business days since the previous observation (1 on consecutive trading days, which is the ARCHITECTURE formula;
    larger across holidays and data gaps so carry is not lost). The index starts at ``base`` on the first date.
    """
    out = []
    prev_y = prev_d = None
    level = float(base)
    for d, v in yields:
        if v is None:
            continue
        dd = d if isinstance(d, _dt.date) else _dt.date.fromisoformat(str(d)[:10])
        y = float(v) / 100.0
        if prev_y is not None:
            n = max(1, _weekdays_between(prev_d, dd))
            dy = y - prev_y
            r = prev_y * n / 252.0 - duration * dy + 0.5 * convexity * dy * dy
            level *= max(1.0 + r, 1e-9)
        out.append((dd.isoformat(), level))
        prev_y, prev_d = y, dd
    return out


def build_synthetic(store, asset: dict) -> int:
    """Rebuild one synthetic index from its stored FRED yield; returns rows changed."""
    series = asset.get("fred")
    dur, cvx = asset.get("duration"), asset.get("convexity")
    if not series or dur is None:
        raise ValueError(f"{asset['id']}: synthetic asset needs fred and duration")
    base = (asset.get("meta") or {}).get("base", 100.0)
    idx = total_return_index(store.macro(series), float(dur), float(cvx or 0.0), base)
    rows = [{"date": d, "open": None, "high": None, "low": None, "close": v, "adj_close": v, "volume": None}
            for d, v in idx]
    return store.upsert_prices(asset["id"], rows)


# ----------------------------------------------------------------------------------------------- yahoo
def _close_enough(a, b) -> bool:
    if a is None or b is None:
        return a is b
    return abs(a - b) <= REL_TOL * max(abs(a), abs(b), 1e-12)


def _store_actions(store, aid: str, rows, complete: bool) -> None:
    """Keep the splits and dividends that came with a download; `complete` = it covered the whole history."""
    events = getattr(rows, "events", None)
    if events is None:
        return
    store.upsert_actions(aid, events)
    if complete:
        store.kv_set(f"actions_complete:{aid}", True)


def refresh_yahoo(store, asset: dict, full: bool = False) -> dict:
    """Incremental (or full) Yahoo download for one asset. Returns {"rows", "changed", "mode"}."""
    aid, sym = asset["id"], asset["yahoo"]
    last = None if full else store.last_price_date(aid)
    if last is None:
        rows = yahoo.fetch_history(sym, 0)
        if not rows:
            raise ValueError("no price history returned")
        changed = store.replace_prices(aid, rows) if full else store.upsert_prices(aid, rows)
        _store_actions(store, aid, rows, True)
        return {"rows": len(rows), "changed": changed, "mode": "full"}
    start = _dt.date.fromisoformat(last) - _dt.timedelta(days=YAHOO_OVERLAP_DAYS)
    rows = yahoo.fetch_history(sym, yahoo.date_to_epoch(start))
    if getattr(rows, "events", None) is not None and not store.kv_get(f"actions_complete:{aid}"):
        # a store filled before corporate actions were kept: fetch the whole history once for its splits and dividends
        _store_actions(store, aid, yahoo.fetch_history(sym, 0), True)
    _store_actions(store, aid, rows, False)
    stored = {r["date"]: r for r in store.prices(aid, start=start)}
    readjusted = False
    for r in rows:
        old = stored.get(r["date"])
        if old is None or r["date"] >= last:  # the last stored bar may have been a partial (live) bar
            continue
        if not (_close_enough(old["close"], r["close"]) and _close_enough(old["adj_close"], r["adj_close"])):
            readjusted = True
            break
    if readjusted:
        full_rows = yahoo.fetch_history(sym, 0)
        if not full_rows:
            raise ValueError("re-adjusted history could not be re-downloaded")
        store.replace_prices(aid, full_rows)
        _store_actions(store, aid, full_rows, True)
        return {"rows": len(full_rows), "changed": len(full_rows), "mode": "readjusted"}
    changed = store.upsert_prices(aid, rows) if rows else 0
    return {"rows": len(rows), "changed": changed, "mode": "incremental"}


# ----------------------------------------------------------------------------------------------- fred / sec
class KeptFirstRelease(Exception):
    """A first-release series was not refreshed because FRED_API_KEY is not set (its point-in-time data is kept)."""


def first_release_series(series: str) -> bool:
    """True for FRED_SERIES that are revised after publication (every non-daily one)."""
    info = FRED_SERIES.get(series)
    return bool(info) and info.get("freq") != "daily"


def refresh_fred(store, series: str, full: bool = False) -> int:
    """Refresh one FRED series; returns rows changed. See the module docstring for which kind is stored."""
    stored_kind = store.macro_kind(series)
    if first_release_series(series) and fred.has_api_key():
        last = None if full or stored_kind != "first_release" else store.last_macro_date(series)
        if last is None:  # first download, --full, or switching from latest-vintage: replace everything
            rows = fred.fetch_first_release(series)
            if not rows:
                raise ValueError("no first-release observations returned")
            return store.replace_macro(series, rows, kind="first_release")
        start = (_dt.date.fromisoformat(last) - _dt.timedelta(days=FRED_OVERLAP_DAYS)).isoformat()
        return store.upsert_macro(series, fred.fetch_first_release(series, start))
    if stored_kind == "first_release":
        raise KeptFirstRelease("FRED_API_KEY not set: kept the stored first-release values")
    last = None if full else store.last_macro_date(series)
    start = None
    if last:
        start = (_dt.date.fromisoformat(last) - _dt.timedelta(days=FRED_OVERLAP_DAYS)).isoformat()
    rows = fred.fetch_series(series, start)
    if not rows and last is None:
        raise ValueError("no observations returned")
    n = store.upsert_macro(series, rows)
    store.set_macro_kind(series, "latest_vintage")
    return n


def _sec_recent(store, asset_id: str, today: _dt.date) -> bool:
    ts = store.last_fetch("sec", asset_id, "ok")
    if not ts:
        return False
    try:
        when = _dt.date.fromisoformat(ts[:10])
    except ValueError:
        return False
    return (today - when).days < SEC_MIN_AGE_DAYS


def refresh_sec(store, asset: dict) -> int:
    ciks = [asset["cik"]] + list((asset.get("meta") or {}).get("extra_ciks") or [])
    rows, errors = [], []
    for cik in ciks:
        try:
            rows.extend(sec.extract(sec.fetch_companyfacts(cik)))
        except Exception as exc:  # noqa: BLE001 - one bad CIK must not lose the other
            errors.append(f"CIK {cik}: {exc}")
    if not rows:
        raise ValueError("; ".join(errors) or "no fundamentals in companyfacts")
    return store.upsert_fundamentals(asset["id"], rows)


# ----------------------------------------------------------------------------------------------- orchestration
def refresh(store, assets=None, progress=None, full: bool = False, macro=None, fundamentals: bool = True,
            pause: float = 0.25) -> dict:
    """Download everything stale. Returns a summary dict; never raises for a single asset's failure.

    ``assets``: None (every asset in the store) or a list of asset ids / asset dicts.
    ``macro``: None -> every FRED_SERIES when ``assets`` is None, otherwise only the yields the selected synthetic
    assets need; True -> always every series; False -> none (synthetic indices are still rebuilt from the store).
    ``progress(done, total, message)`` is called before each task and once at the end.
    ``pause``: seconds between Yahoo requests (politeness).
    """
    t0 = time.time()
    today = _today()
    summary = {"prices": {}, "macro": {}, "synthetic": {}, "fundamentals": {}, "skipped": [], "errors": [],
               "started": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()}
    if assets is None:
        selected = store.assets()
    else:
        selected = []
        for a in assets:
            aid = a.get("id") if isinstance(a, dict) else a
            found = store.asset(aid) if aid else None
            if found is None:
                summary["errors"].append({"source": "store", "key": str(aid), "error": "unknown asset"})
            else:
                selected.append(found)

    ylist = [a for a in selected if a.get("yahoo")]
    synth = [a for a in selected if not a.get("yahoo") and a.get("fred") and a.get("asset_class") in SYNTHETIC_CLASSES]
    series = []
    if macro is True or (macro is None and assets is None):
        series = list(FRED_SERIES)
    for a in synth:
        if macro is not False and a["fred"] not in series:
            series.append(a["fred"])
    slist = [a for a in selected if a.get("asset_class") == "EQUITY" and a.get("cik")] if fundamentals else []

    total = len(ylist) + len(series) + len(synth) + len(slist)
    done = 0

    def tick(msg):
        if progress:
            try:
                progress(done, total, msg)
            except Exception:  # noqa: BLE001 - a broken callback must not stop the refresh
                pass

    def fail(source, key, exc):
        msg = str(exc)[:300] or type(exc).__name__
        summary["errors"].append({"source": source, "key": key, "error": msg})
        try:
            store.log_fetch(source, key, "error", msg)
        except Exception:  # noqa: BLE001
            pass

    for i, a in enumerate(ylist):
        tick(f"prices {a['id']}")
        try:
            if i:
                _sleep(pause)
            res = refresh_yahoo(store, a, full=full)
            summary["prices"][a["id"]] = res["changed"]
            store.log_fetch("yahoo", a["id"], "ok", f"{res['mode']} {res['rows']} rows, {res['changed']} changed")
        except Exception as exc:  # noqa: BLE001
            fail("yahoo", a["id"], exc)
        done += 1

    for s in series:
        tick(f"macro {s}")
        try:
            n = refresh_fred(store, s, full=full)
            summary["macro"][s] = n
            store.log_fetch("fred", s, "ok", f"{store.macro_kind(s)} {n} changed")
        except KeptFirstRelease as exc:
            summary["skipped"].append({"source": "fred", "key": s, "reason": str(exc)})
        except Exception as exc:  # noqa: BLE001
            fail("fred", s, exc)
        done += 1

    for a in synth:
        tick(f"index {a['id']}")
        try:
            n = build_synthetic(store, a)
            summary["synthetic"][a["id"]] = n
            store.log_fetch("synthetic", a["id"], "ok", f"{n} changed")
        except Exception as exc:  # noqa: BLE001
            fail("synthetic", a["id"], exc)
        done += 1

    for a in slist:
        tick(f"fundamentals {a['id']}")
        try:
            if not full and _sec_recent(store, a["id"], today):
                summary["skipped"].append({"source": "sec", "key": a["id"], "reason": "fetched < 7 days ago"})
            else:
                n = refresh_sec(store, a)
                summary["fundamentals"][a["id"]] = n
                store.log_fetch("sec", a["id"], "ok", f"{n} changed")
        except Exception as exc:  # noqa: BLE001
            fail("sec", a["id"], exc)
        done += 1

    tick("done")
    summary["finished"] = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()
    summary["seconds"] = round(time.time() - t0, 2)
    summary["data_version"] = store.data_version()
    return summary



def last_business_day(today: _dt.date | None = None) -> _dt.date:
    """The most recent weekday strictly before ``today`` (NYSE holidays are not modelled)."""
    d = (today or _today()) - _dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= _dt.timedelta(days=1)
    return d


def stale(store, today: _dt.date | None = None, benchmark: str = "SPY") -> bool:
    """True when the benchmark's last stored bar is older than the last business day (or there is none)."""
    last = store.last_price_date(benchmark)
    if last is None:
        return True
    return _dt.date.fromisoformat(last) < last_business_day(today)


# ----------------------------------------------------------------------------------------------- user symbols
def _derive_id(symbol: str) -> str:
    s = symbol.upper().lstrip("^")
    if s.endswith("=X"):
        s = s[:-2]
    s = s.replace("=", "_")
    return re.sub(r"[^A-Z0-9._\-]", "", s) or "SYM"


def add_symbol(store, yahoo_symbol: str, asset_class: str | None = None, lookup_cik: bool = True) -> dict:
    """Add any symbol Yahoo has (with its full daily history) to the store; returns the asset dict.

    If an asset with that Yahoo symbol already exists it is returned (and its history filled if empty).
    """
    if not isinstance(yahoo_symbol, str) or not _SYMBOL_RE.match(yahoo_symbol.strip()):
        raise ValueError("invalid Yahoo symbol")
    sym = yahoo_symbol.strip().upper()
    for a in store.assets():
        if (a.get("yahoo") or "").upper() == sym:
            if store.price_count(a["id"]) == 0:
                refresh_yahoo(store, a)
            return store.asset(a["id"])
    meta, rows = yahoo.fetch_history_meta(sym, 0)
    if not rows:
        raise ValueError(f"{sym}: Yahoo returned no price history")
    itype = yahoo._clean_text(meta.get("instrumentType")) or ""
    cls = asset_class or _INSTRUMENT_CLASS.get(itype.upper(), "EQUITY")
    aid = _derive_id(sym)
    existing = store.asset(aid)
    if existing is not None and (existing.get("yahoo") or "").upper() != sym:
        aid = "Y_" + aid
    name = yahoo._clean_text(meta.get("longName")) or yahoo._clean_text(meta.get("shortName")) or sym
    cik = None
    if cls == "EQUITY" and lookup_cik:
        try:
            cik = sec.lookup_cik(sym)
        except Exception:  # noqa: BLE001
            cik = None
    asset = {"id": aid, "name": name, "asset_class": cls, "sector": None, "country": None,
             "currency": yahoo._clean_text(meta.get("currency"), 8), "yahoo": sym, "cik": cik, "duration": None,
             "convexity": None, "fred": None, "benchmark": "SPY",
             "meta": {"user_added": True, "instrument_type": itype or None,
                      "exchange": yahoo._clean_text(meta.get("exchangeName"), 40),
                      "exchange_tz": yahoo._clean_text(meta.get("exchangeTimezoneName"), 60)}}
    store.upsert_asset(asset)
    store.upsert_prices(aid, rows)
    store.log_fetch("yahoo", aid, "ok", f"added {sym}: {len(rows)} rows")
    return store.asset(aid)


__all__ = ["refresh", "stale", "add_symbol", "total_return_index", "build_synthetic", "refresh_yahoo",
           "refresh_fred", "refresh_sec", "last_business_day", "first_release_series", "KeptFirstRelease"]
