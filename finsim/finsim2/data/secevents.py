"""SEC EDGAR events -> point-in-time alt_data datasets.

``sec_insider`` — Form 4 open-market insider transactions, from SEC's quarterly "Insider Transactions Data Sets"
(https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{YYYY}q{Q}_form345.zip, 2006 Q1 on,
about 10 MB a quarter). Only original Form 4s (amendments skipped, so nothing is counted twice) and only open-market
purchases (code P) and sales (code S): grants, option exercises, tax withholding and gifts carry no view. One row set
per issuer and filing date:

    buy_value, sell_value (USD), buy_shares, sell_shares, buyers, sellers (distinct filings), officer_buy_value,
    director_buy_value, ceo_cfo_buy_value, tenpct_buy_value, officer_sell_value, ceo_cfo_sell_value,
    planned_sell_value (sales in filings that tick the Rule 10b5-1 box — reported only since April 2023),
    max_buy_frac (largest purchase as a share of the buyer's holding after it)

The data set carries the filing date but not the time, so every row is published the next calendar day.

``sec_8k`` — 8-K current reports from the EDGAR submissions API (data.sec.gov/submissions/CIK##########.json and its
older pages): per issuer and New York acceptance date, ``n_8k`` and one count per item code (``item_2.02`` = results of
operations, ``item_5.02`` = officer / director changes, ``item_1.01`` = material agreement, …) plus ``release_hhmm``
(New York time of the first item-2.02 filing that day, e.g. 1630). Acceptance times are UTC in the API; a filing
accepted before 16:00 New York is usable at that day's close, later ones from the next day.

Rules (CLAUDE.md): the LoganTerminal User-Agent on every request, at most ~8 requests / second (sec.py's process-wide
throttle). Everything downloaded is untrusted: parsed with csv / json, numbers through ``num``.
"""
from __future__ import annotations

import csv
import datetime as _dt
import io
import json
import zipfile
from typing import Dict, Iterable, List, Optional, Tuple

from . import FetchError, num
from . import sec as S

INSIDER = "sec_insider"
EVENTS = "sec_8k"
ZIP_URL = "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{q}_form345.zip"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/{name}"
FIRST_QUARTER = (2006, 1)
DONE_KEY = "secevents:insider_quarters"
CIKS_KEY = "secevents:insider_ciks"          # issuers whose full quarterly history has been read
MARKET_CLOSE_ET = 16 * 60                     # minutes after midnight, New York
_MON = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


# ------------------------------------------------------------------ time
def _nth_sunday(y: int, m: int, n: int) -> _dt.date:
    d = _dt.date(y, m, 1)
    d += _dt.timedelta(days=(6 - d.weekday()) % 7)
    return d + _dt.timedelta(weeks=n - 1)


def _last_sunday(y: int, m: int) -> _dt.date:
    d = _dt.date(y, m + 1, 1) - _dt.timedelta(days=1)
    return d - _dt.timedelta(days=(d.weekday() + 1) % 7)


def new_york(utc: _dt.datetime) -> _dt.datetime:
    """UTC -> New York local time with the US daylight-saving rules (1987–2006 and 2007 on). No tz database needed
    (Windows Python has none without the tzdata package)."""
    y = utc.year
    if y >= 2007:
        start, end = _nth_sunday(y, 3, 2), _nth_sunday(y, 11, 1)
    else:
        start, end = _nth_sunday(y, 4, 1), _last_sunday(y, 10)
    # DST runs from 02:00 local standard time (07:00 UTC) on `start` to 02:00 local daylight time (06:00 UTC) on `end`
    dst = _dt.datetime(start.year, start.month, start.day, 7) <= utc.replace(tzinfo=None) < _dt.datetime(end.year, end.month, end.day, 6)
    return utc.replace(tzinfo=None) - _dt.timedelta(hours=4 if dst else 5)


def _parse_utc(s) -> Optional[_dt.datetime]:
    if not isinstance(s, str) or len(s) < 19:
        return None
    try:
        return _dt.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def _sec_date(s) -> Optional[str]:
    """'30-MAY-2025' -> '2025-05-30'."""
    if not isinstance(s, str):
        return None
    p = s.strip().split("-")
    if len(p) != 3 or p[1].upper() not in _MON:
        return None
    try:
        return _dt.date(int(p[2]), _MON[p[1].upper()], int(p[0])).isoformat()
    except ValueError:
        return None


def _next_day(d: str) -> str:
    return (_dt.date.fromisoformat(d) + _dt.timedelta(days=1)).isoformat()


# ------------------------------------------------------------------ issuers
def issuer_map(store) -> Dict[int, str]:
    """SEC CIK -> asset id for every equity in the store (including predecessor / successor CIKs)."""
    out: Dict[int, str] = {}
    for a in store.assets("EQUITY"):
        ciks = [a.get("cik")] + list((a.get("meta") or {}).get("extra_ciks") or [])
        for c in ciks:
            try:
                if c:
                    out[int(c)] = a["id"]
            except (TypeError, ValueError):
                continue
    return out


# ------------------------------------------------------------------ Form 4 (quarterly data sets)
def quarters(until: Optional[_dt.date] = None) -> List[str]:
    until = until or _dt.date.today()
    y, q = FIRST_QUARTER
    out = []
    while (y, q) <= (until.year, (until.month - 1) // 3 + 1):
        out.append(f"{y}q{q}")
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    return out


def _tsv(z: zipfile.ZipFile, name: str):
    with z.open(name) as fh:
        rd = csv.reader(io.TextIOWrapper(fh, encoding="utf-8", errors="replace", newline=""), delimiter="\t", quoting=csv.QUOTE_NONE)
        head = next(rd, None)
        if not head:
            return
        ix = {h.strip(): i for i, h in enumerate(head)}
        for row in rd:
            yield ix, row


def _g(ix, row, key) -> str:
    i = ix.get(key)
    return row[i].strip() if i is not None and i < len(row) else ""


def _add(out: dict, seen: dict, key: Tuple[str, str], acc: str, code: str, sh: float, px: float, after: Optional[float],
         roles: dict, plan: bool):
    """Accumulate one open-market transaction into its (asset, filing date) row — shared by the bulk and recent paths."""
    f = out.setdefault(key, {})
    s = seen.setdefault(key, {"buy": set(), "sell": set()})
    v = sh * px
    side = "buy" if code == "P" else "sell"
    f[f"{side}_value"] = f.get(f"{side}_value", 0.0) + v
    f[f"{side}_shares"] = f.get(f"{side}_shares", 0.0) + sh
    s[side].add(acc)
    if side == "buy":
        for role in ("officer", "director", "ceo_cfo", "tenpct"):
            if roles.get(role):
                f[f"{role}_buy_value"] = f.get(f"{role}_buy_value", 0.0) + v
        if after and after > 0:
            f["max_buy_frac"] = max(f.get("max_buy_frac", 0.0), min(1.0, sh / after))
    else:
        for role in ("officer", "ceo_cfo"):
            if roles.get(role):
                f[f"{role}_sell_value"] = f.get(f"{role}_sell_value", 0.0) + v
        if plan:
            f["planned_sell_value"] = f.get("planned_sell_value", 0.0) + v


def _finish(out: dict, seen: dict) -> dict:
    for key, s in seen.items():
        out[key]["buyers"] = float(len(s["buy"]))
        out[key]["sellers"] = float(len(s["sell"]))
    return out


def _valid(code: str, sh, px) -> bool:
    return code in ("P", "S") and sh is not None and sh > 0 and px is not None and 0 < px <= 1e6


def parse_insider_zip(blob: bytes, ciks: Dict[int, str]) -> Dict[Tuple[str, str], Dict[str, float]]:
    """{(asset, filing date): {field: value}} for the wanted issuers in one quarterly zip."""
    z = zipfile.ZipFile(io.BytesIO(blob))
    subs: Dict[str, Tuple[str, str, bool]] = {}
    for ix, row in _tsv(z, "SUBMISSION.tsv"):
        if _g(ix, row, "DOCUMENT_TYPE") != "4":
            continue
        try:
            cik = int(_g(ix, row, "ISSUERCIK") or 0)
        except ValueError:
            continue
        a = ciks.get(cik)
        d = _sec_date(_g(ix, row, "FILING_DATE"))
        if a and d:
            subs[_g(ix, row, "ACCESSION_NUMBER")] = (a, d, _g(ix, row, "AFF10B5ONE") == "1")
    if not subs:
        return {}
    roles: Dict[str, dict] = {}
    for ix, row in _tsv(z, "REPORTINGOWNER.tsv"):
        acc = _g(ix, row, "ACCESSION_NUMBER")
        if acc not in subs:
            continue
        rel = _g(ix, row, "RPTOWNER_RELATIONSHIP").lower()
        title = (_g(ix, row, "RPTOWNER_TITLE") + " " + _g(ix, row, "RPTOWNER_TXT")).lower()
        r = roles.setdefault(acc, {"officer": False, "director": False, "tenpct": False, "ceo_cfo": False})
        r["officer"] |= "officer" in rel
        r["director"] |= "director" in rel
        r["tenpct"] |= "tenpercent" in rel.replace(" ", "")
        r["ceo_cfo"] |= _is_ceo_cfo(title)
    out: Dict[Tuple[str, str], Dict[str, float]] = {}
    seen: Dict[Tuple[str, str], Dict[str, set]] = {}
    for ix, row in _tsv(z, "NONDERIV_TRANS.tsv"):
        acc = _g(ix, row, "ACCESSION_NUMBER")
        if acc not in subs:
            continue
        code = _g(ix, row, "TRANS_CODE").upper()
        sh, px = num(_g(ix, row, "TRANS_SHARES")), num(_g(ix, row, "TRANS_PRICEPERSHARE"))
        if not _valid(code, sh, px):
            continue
        a, d, plan = subs[acc]
        _add(out, seen, (a, d), acc, code, sh, px, num(_g(ix, row, "SHRS_OWND_FOLWNG_TRANS")), roles.get(acc) or {}, plan)
    return _finish(out, seen)


def _is_ceo_cfo(title: str) -> bool:
    return any(k in title for k in ("ceo", "chief executive", "cfo", "chief financial"))


def _x(el, path: str) -> str:
    """Text of `path` (the <value> child when the element wraps one)."""
    n = el.find(path)
    if n is None:
        return ""
    v = n.find("value")
    return ((v.text if v is not None else n.text) or "").strip()


def parse_form4_xml(body: bytes) -> Optional[dict]:
    """One Form 4 ownershipDocument -> {roles, plan, transactions: [(code, shares, price, owned after)]}; None when the
    document is not a plain Form 4 or cannot be read. Documents declaring entities are refused outright."""
    import xml.etree.ElementTree as ET
    if b"<!ENTITY" in body[:4096] or b"<!DOCTYPE" in body[:4096]:
        return None
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None
    if root.tag != "ownershipDocument" or _x(root, "documentType") != "4":
        return None
    roles = {"officer": False, "director": False, "tenpct": False, "ceo_cfo": False}
    for o in root.findall("reportingOwner"):
        rel = o.find("reportingOwnerRelationship")
        if rel is None:
            continue
        flag = lambda t: _x(rel, t).lower() in ("1", "true")  # noqa: E731
        roles["officer"] |= flag("isOfficer")
        roles["director"] |= flag("isDirector")
        roles["tenpct"] |= flag("isTenPercentOwner")
        roles["ceo_cfo"] |= _is_ceo_cfo((_x(rel, "officerTitle") + " " + _x(rel, "otherText")).lower())
    tx = []
    for t in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = _x(t, "transactionCoding/transactionCode").upper()
        sh, px = num(_x(t, "transactionAmounts/transactionShares")), num(_x(t, "transactionAmounts/transactionPricePerShare"))
        if _valid(code, sh, px):
            tx.append((code, sh, px, num(_x(t, "postTransactionAmounts/sharesOwnedFollowingTransaction"))))
    return {"roles": roles, "plan": _x(root, "aff10b5One").lower() in ("1", "true"), "transactions": tx}


def _quarter_end(q: str) -> str:
    y, n = int(q[:4]), int(q[5])
    return (_dt.date(y + (n == 4), 1 if n == 4 else 3 * n + 1, 1) - _dt.timedelta(days=1)).isoformat()


def refresh_insider_recent(store, progress=None, assets: Optional[Iterable[str]] = None) -> dict:
    """Form 4s filed after the last quarterly data set (which SEC publishes with a lag), read one filing at a time from
    each issuer's submissions list. The next quarterly data set supersedes these rows."""
    say = progress or (lambda m: None)
    done = sorted(store.kv_get(DONE_KEY) or [])
    after = _quarter_end(done[-1]) if done else "2006-01-01"
    by_asset: Dict[str, List[int]] = {}
    for cik, a in issuer_map(store).items():
        by_asset.setdefault(a, []).append(cik)
    if assets:
        by_asset = {a: c for a, c in by_asset.items() if a in set(assets)}
    out: Dict[Tuple[str, str], Dict[str, float]] = {}
    seen: Dict[Tuple[str, str], Dict[str, set]] = {}
    n_filings = 0
    for a, ciks in sorted(by_asset.items()):
        for cik in ciks:
            S._throttle()
            try:
                first = json.loads(S._get(SUBMISSIONS_URL.format(name=f"CIK{cik:010d}.json")).decode("utf-8", "replace"))
            except (FetchError, ValueError):
                continue
            r = ((first.get("filings") or {}).get("recent") or {}) if isinstance(first, dict) else {}
            for form, fd, acc, doc in zip(r.get("form") or [], r.get("filingDate") or [], r.get("accessionNumber") or [], r.get("primaryDocument") or []):
                if form != "4" or not isinstance(fd, str) or fd <= after or not isinstance(doc, str) or not isinstance(acc, str):
                    continue
                name = doc.split("/")[-1]                                  # the raw XML, not the xsl-rendered view
                if not name.endswith(".xml") or "/" in name or ".." in name:
                    continue
                S._throttle()
                try:
                    body = S._get(f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{name}")
                except FetchError:
                    continue
                doc4 = parse_form4_xml(body)
                n_filings += 1
                if not doc4:
                    continue
                for code, sh, px, own in doc4["transactions"]:
                    _add(out, seen, (a, fd[:10]), acc, code, sh, px, own, doc4["roles"], doc4["plan"])
        say(f"SEC insider (recent filings) {a}")
    _finish(out, seen)
    rows = [(a, d, fld, val, _next_day(d)) for (a, d), f in out.items() for fld, val in f.items()]
    if rows:
        store.put_alt(INSIDER, rows)
    return {"after": after, "filings": n_filings, "issuer_days": len(out), "rows": len(rows)}


def _with_history(store, ciks: Dict[int, str], before: str) -> set:
    """CIKs whose stock has any insider row dated before `before` (i.e. from the stored quarterly data sets, not only
    from the always re-read latest quarters or the recent-filings pass)."""
    has = {a for a in set(ciks.values()) if any(str(d)[:10] < before for _, d, *_ in store.alt(INSIDER, a))}
    return {c for c, a in ciks.items() if a in has}


def refresh_insider(store, progress=None, until: Optional[_dt.date] = None, force: bool = False, recheck: bool = False) -> dict:
    """Download every quarter not yet stored (the latest two are always re-read: SEC adds late filings). Issuers added
    since the last full pass (e.g. by ``universe --expand``) get their history from every stored quarter too — read for
    those issuers only, so existing rows are not rewritten.

    Which issuers already have their history: the CIKs recorded after the last complete pass. A store from before that
    record — and `recheck=True` — counts only issuers that actually have rows from before the latest quarters, so an
    issuer that so far only has recent filings is backfilled (the 2026-09-28 repair: stocks added by the expansion had
    been counted as covered with only their 2026 filings)."""
    say = progress or (lambda m: None)
    ciks = issuer_map(store)
    done = set(store.kv_get(DONE_KEY) or [])
    qs = quarters(until)
    recent = set(qs[-2:])
    known = store.kv_get(CIKS_KEY)
    if done and (known is None or recheck):
        hist = _with_history(store, ciks, _quarter_end(qs[-3]) if len(qs) >= 3 else "0000")
        known = hist if known is None else set(known) & hist
    known = set(known) if known is not None else set()
    new = {c: a for c, a in ciks.items() if c not in known}
    todo = [(q, ciks) for q in qs if force or q not in done or q in recent]
    if new and not force:
        todo += [(q, new) for q in qs if q in done and q not in recent]
        todo.sort()
        say(f"SEC insider: backfilling {len(set(new.values()))} new issuers over {sum(1 for _, m in todo if m is new)} stored quarters")
    rows_total, missing, failed = 0, [], 0
    for k, (q, cmap) in enumerate(todo):
        S._throttle()
        try:
            blob = S._get(ZIP_URL.format(q=q))
        except FetchError as e:
            if e.status in (403, 404):
                missing.append(q)
                continue
            say(f"SEC insider {q}: {e}")
            failed += 1
            continue
        try:
            agg = parse_insider_zip(blob, cmap)
        except (zipfile.BadZipFile, KeyError) as e:
            say(f"SEC insider {q}: unreadable ({type(e).__name__})")
            failed += 1
            continue
        rows = [(a, d, fld, val, _next_day(d)) for (a, d), f in agg.items() for fld, val in f.items()]
        if rows:
            store.put_alt(INSIDER, rows)
        rows_total += len(rows)
        done.add(q)
        store.kv_set(DONE_KEY, sorted(done))
        say(f"SEC insider {q}: {len(agg)} issuer-days ({k + 1}/{len(todo)})")
    if not failed:
        store.kv_set(CIKS_KEY, sorted(ciks))
    return {"quarters": len(todo) - len(missing), "missing": missing, "rows": rows_total, "issuers": len(set(ciks.values())),
            "new_issuers": len(set(new.values()))}


# ------------------------------------------------------------------ 8-K events (submissions API)
def _submission_pages(cik: int) -> List[dict]:
    S._throttle()
    first = json.loads(S._get(SUBMISSIONS_URL.format(name=f"CIK{cik:010d}.json")).decode("utf-8", "replace"))
    if not isinstance(first, dict):
        return []
    pages = [((first.get("filings") or {}).get("recent") or {})]
    for f in (first.get("filings") or {}).get("files") or []:
        name = f.get("name") if isinstance(f, dict) else None
        if not isinstance(name, str) or not name.startswith(f"CIK{cik:010d}-submissions-") or not name.endswith(".json"):
            continue
        S._throttle()
        try:
            p = json.loads(S._get(SUBMISSIONS_URL.format(name=name)).decode("utf-8", "replace"))
        except (FetchError, ValueError):
            continue
        if isinstance(p, dict):
            pages.append(p)
    return pages


def parse_8k(pages: List[dict]) -> Dict[str, Dict[str, float]]:
    """{New York acceptance date: {field: value}} from submissions pages (original 8-Ks only)."""
    out: Dict[str, Dict[str, float]] = {}
    for p in pages:
        forms, accs, items = p.get("form") or [], p.get("acceptanceDateTime") or [], p.get("items") or []
        for i, form in enumerate(forms):
            if form != "8-K" or i >= len(accs):
                continue
            t = _parse_utc(accs[i])
            if t is None:
                continue
            ny = new_york(t)
            d = ny.date().isoformat()
            f = out.setdefault(d, {})
            f["n_8k"] = f.get("n_8k", 0.0) + 1
            its = items[i] if i < len(items) and isinstance(items[i], str) else ""
            codes = [c.strip() for c in its.split(",") if c.strip()]
            for c in codes:
                if len(c) <= 6 and all(ch.isdigit() or ch == "." for ch in c):
                    f[f"item_{c}"] = f.get(f"item_{c}", 0.0) + 1
            hhmm = ny.hour * 100 + ny.minute
            f["_last_minute"] = max(f.get("_last_minute", 0.0), ny.hour * 60 + ny.minute)
            if "2.02" in codes:
                f["release_hhmm"] = min(f.get("release_hhmm", 9999.0), float(hhmm))
    return out


def refresh_events(store, progress=None, assets: Optional[Iterable[str]] = None) -> dict:
    say = progress or (lambda m: None)
    by_asset: Dict[str, List[int]] = {}
    for cik, a in issuer_map(store).items():
        by_asset.setdefault(a, []).append(cik)
    if assets:
        keep = set(assets)
        by_asset = {a: c for a, c in by_asset.items() if a in keep}
    n_rows, failed = 0, []
    for k, (a, ciks) in enumerate(sorted(by_asset.items())):
        pages: List[dict] = []
        for c in ciks:
            try:
                pages += _submission_pages(c)
            except (FetchError, ValueError) as e:
                failed.append(f"{a}: {e}")
        days = parse_8k(pages)
        rows = []
        for d, f in days.items():
            # accepted before the close -> usable at that day's close; after the close -> the next day
            pub = d if f.get("_last_minute", 0) < MARKET_CLOSE_ET else _next_day(d)
            rows += [(a, d, fld, val, pub) for fld, val in f.items() if not fld.startswith("_")]
        if rows:
            store.put_alt(EVENTS, rows)
        n_rows += len(rows)
        if k % 10 == 0 or k == len(by_asset) - 1:
            say(f"SEC 8-K events {k + 1}/{len(by_asset)} ({a}: {len(days)} days)")
    return {"issuers": len(by_asset), "rows": n_rows, "failed": failed}
