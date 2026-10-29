"""The Dashboard (owner directive 2026-10-27 sections 13 and 16; FINSIM1_PLAN P1 task 4: UI-05, PORTFOLIO-07, OMS-16).

``GET /api/dashboard?book=`` - the landing page of the selected book in ONE answer:
- ``capital``: the lifetime's starting capital (the book's default when it opened: 25,000,000.00 for the Fund) and the capital
  contributed so far (deposits less withdrawals; internal transfers net to zero across the book);
- ``nav``: the ledger NAV at this answer's marks (``Accounting.nav``: assets - liabilities, quality, unpriced positions);
- ``pnl``: TODAY / MTD / YTD / ITD (``Accounting.pnl``; one ``Accounting.pnl_periods`` call when the build has it) with the explain
  rows the Accounting page shows, each with its return % from the period returns of the daily snapshots when that provider is in this
  build, else ``None`` with the reason;
- ``explain``: the ITD P&L split - realised, unrealised, income, financing, fees, FX result - each with its signed effect;
- ``exposure``: gross / net / long / short (and as % of NAV), leverage, gross notional / NAV, the TRS notional, DV01, excess liquidity
  and encumbrance - the Risk page's own figures: its per-position risk rows of this answer's positions and marks, their totals and
  ratios without the rest of its report (scenarios, financing, Reg T, warnings: the Risk page) - ``risk.exposure_summary`` (in the
  build since 1.0.6: the figures ``risk.report`` serves, built by the same function), else the report's row helpers (``risk._pos_risk``
  / ``_sum_rows`` / ``_ratios``), else the whole ``risk.report``;
- ``cash``: settled, available, reserved, restricted (collateral received that may not be spent), receivables and payables across
  the book's accounts (``Accounting.cash``), per currency and in USD;
- ``margin`` (required, posted, excess, open calls) and ``collateral`` (posted, received, encumbered) from the collateral summary;
- ``accounts``: one row per account (NAV, share of the book, cash, available, reserved, restricted, working orders, settling,
  open attention items);
- ``counters``: the order and settlement counters of the trade blotters (working, partially filled, held, queued, waiting for a
  parent, dealer quotes open, rejected today, settling, failed);
- ``attention``: the "Requires your attention" summary (``attention.attention_summary``), with the risk warnings and compliance
  notices as last counted for this book (below);
- ``sources``: per section where its figures come from (trader words), as of when, their quality and the milliseconds it took
  (``sources.attention.counts``: when the risk warnings and compliance notices were counted); ``timing``: ``own_ms`` (the
  Dashboard's own work - everything but the optional providers; budget ``BUDGET_MS`` of server time), ``providers_ms``,
  ``total_ms``, ``counts`` (how this answer got those two counts) and ``counts_ms`` (the time it spent counting them).

``GET /api/dashboard/counts?book=`` - the sidebar's badges: Pending Trades = working + partially filled + held orders, Completed Trades =
settling, from the trade blotters' counters (``present`` false with the reason while they are not in this build: no badge).

Conventions
- Nothing new is computed: every figure is one the other pages already serve, gathered on ONE mark snapshot handed to every part
  that takes one.
- The risk warnings and compliance notices read the whole book (the whole risk report, every compliance rule), so they are
  counted in the background, never inside an answer: the first answer for a book after a start says they are being counted
  (``timing.counts`` "counting": None with that note, never 0; the page asks again two seconds later) and starts the count once its
  own work is done; later answers use the last counts while they are under ``COUNTS_MAX_AGE_S`` old and the ledger has not changed
  since (state version; "kept"); once older, or after a booking, the answer carries the last counts with their time and a background
  recount replaces them ("recounting").  The time is always shown, and a failed count is said beside the counts ("failed" when there
  is none yet).  ``GET /api/attention/summary`` still counts them on every call.  (A runtime that cannot be weakly referenced - a
  test stand-in - has no cache: each answer counts them itself, "counted now".)
- The optional providers are asked only when their module is bound in this build (FINSIM1_PLAN C2) - the period returns
  (``performance.returns_summary``), margin and collateral (``collateral.summary``) and the counters (``blotters.counts``).  A
  provider that is missing, or one whose call fails, leaves its section ``present: false`` with the reason in plain words (never 0,
  never a guess); a failure is also logged with its traceback.
- Money is integer cents (``*_c``; the API codec sends decimal strings); fractions and ratios are decimal strings.
- An optional module (FINSIM1_PLAN C2, 15.2 step 3, S-14): no commands, no schema, nothing to bind; it imports only the standard
  library at load and every terminal module inside the function that needs it, so its import and registration never fail.
"""
from __future__ import annotations

import threading
import time
import weakref
from typing import Any, Callable, Dict, List, Optional, Tuple

PERIODS = ("TODAY", "MTD", "YTD", "ITD")
#: the period whose return % each P&L tile shows (the provider's keys); ITD also shows the simple return beside the time-weighted one
RETURN_OF = {"TODAY": "today_pct", "MTD": "mtd_pct", "YTD": "ytd_pct", "ITD": "itd_twr_pct"}
BUDGET_MS = 300
#: the risk warnings and compliance notices are recounted in the background once their count is this old (or the ledger changed)
COUNTS_MAX_AGE_S = 60
#: the attention row the cash adjuster of restricted collateral uses (collateral.ADJUSTER_KEY)
RESTRICTED_KEY = "restricted"
#: the exposure definitions the Exposure tiles show (risk.DEFINITIONS keys)
EXPOSURE_DEFS = ("gross_exposure", "gross_notional", "long", "short", "net", "trs")
#: the Risk page's figures the Exposure section carries (the RiskView attributes of the same names)
EXPOSURE_KEYS = ("long_c", "short_c", "gross_c", "net_c", "gross_leverage", "gross_notional_c", "gross_notional_leverage", "long_pct_nav", "short_pct_nav",
                 "net_pct_nav", "trs_notional_c", "trs_pct_nav", "dv01_c", "excess_liquidity_c", "encumbrance_c", "quality", "partial", "notional_missing")
WHY = {
    "returns": "return % is not in this build: the time-weighted returns of the daily snapshots are not available",
    "risk": "exposure is not in this build: the risk report is not available",
    "collateral": "margin and collateral figures are not in this build: the collateral summary is not available",
    "counters": "the order and settlement counters arrive with the trade blotters, which are not in this build",
}
SOURCE_WORDS = {
    "nav": "the ledger: every account's balances, positions at this answer's marks",
    "pnl": "the ledger against the last daily snapshot before each period (restated versions where they exist)",
    "returns": "the daily NAV snapshots, chained day by day (time-weighted) with capital flows removed",
    "exposure": "the Risk page's per-position exposure rows and totals on the same marks (the same definitions); scenarios, financing, "
                "Reg T and the risk warnings are on the Risk page",
    "exposure_report": "the Risk page's report on the same marks (the same definitions)",
    "exposure_summary": "the Risk page's exposure summary on the same marks (the same definitions); scenarios, financing, Reg T and the risk "
                        "warnings are on the Risk page",
    "cash": "each account's cash in the ledger (settled, receivables and payables due, reservations of working orders)",
    "margin": "the collateral and margin summary (requirements, postings and calls)",
    "collateral": "the collateral and margin summary (posted, received and encumbered)",
    "accounts": "the ledger, per account",
    "counters": "the trade blotters (orders by stage, settlements by state)",
    "attention": "the attention items, integrity report, order / settlement tables and collateral events, counted now; the risk warnings "
                 "and compliance notices as last counted for this book (their time is shown)",
}
#: runtime -> {scope: the last counts of the risk warnings and compliance notices} (weakly keyed: a closed runtime's counts go with it)
_COUNTS: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
_COUNTS_LOCK = threading.Lock()


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)


def _provider(rt, attr: str, name: str) -> Optional[Callable]:
    """``rt.<attr>.<name>`` when that optional module is bound and the function exists (callable), else None."""
    mod = getattr(rt, attr, None)
    fn = getattr(mod, name, None) if mod is not None else None
    return fn if callable(fn) else None


def _failed(rt, what: str, e: Exception) -> str:
    """Log a part's failure with its traceback and return the sentence the page shows (no internal code)."""
    log = getattr(rt, "log", None)
    if log:
        log.exception("dashboard: %s failed", what)
    return f"could not be computed ({type(e).__name__}); the details are in the terminal log"


def _lifetime_row(rt, lifetime_id: str) -> dict:
    from . import books as _books
    with rt.db.reader() as conn:
        row = _books.lifetime_row(conn, lifetime_id)
        book = _books.book_of(conn, _books.book_code_of(lifetime_id)) if row is not None else None
    if row is None:
        return {}
    return {"lifetime_id": row["lifetime_id"], "name": row["name"], "starting_capital_c": int(row["starting_capital_c"] or 0), "started_ts": row["started_ts"],
            "book_code": row["book_code"], "book_name": book.name if book else None, "book_default_c": int(book.starting_capital_c) if book else None}


def _counts_sql(rt, account_ids: List[str]) -> Dict[str, Dict[str, int]]:
    """Per account: working orders (the open statuses the sidebar counts), pending settlements, open attention items."""
    from .attention import OPEN_ORDER_STATUSES
    out: Dict[str, Dict[str, int]] = {a: {"working_orders": 0, "pending_settlements": 0, "attention_count": 0} for a in account_ids}
    if not account_ids:
        return out
    q = ",".join("?" for _ in account_ids)
    s = ",".join("?" for _ in OPEN_ORDER_STATUSES)
    with rt.db.reader() as conn:
        for r in conn.execute(f"SELECT account_id, COUNT(*) FROM orders WHERE status IN ({s}) AND account_id IN ({q}) GROUP BY account_id",
                              list(OPEN_ORDER_STATUSES) + account_ids).fetchall():
            out[r[0]]["working_orders"] = int(r[1])
        for r in conn.execute(f"SELECT account_id, COUNT(*) FROM settlements WHERE status='PENDING' AND account_id IN ({q}) GROUP BY account_id", account_ids).fetchall():
            out[r[0]]["pending_settlements"] = int(r[1])
        for r in conn.execute(f"SELECT account_id, COUNT(*) FROM attention WHERE resolved_ts IS NULL AND account_id IN ({q}) GROUP BY account_id", account_ids).fetchall():
            out[r[0]]["attention_count"] = int(r[1])
    return out


def _cash_and_accounts(rt, nav, snap) -> Tuple[dict, List[dict]]:
    """The book's cash (per currency and in USD) and one row per account, from ``Accounting.cash`` of every account of the scope
    (the same figures as each account's Cash & financing page; available is each account's own spendable cash floored at zero)."""
    from .api_views import fx_rate
    from .units import fx_c
    acc = rt.accounting
    with rt.db.reader() as conn:
        rows = [dict(r) for r in conn.execute("SELECT account_id, name, kind, role, broker, status, sort_order, lifetime_id FROM accounts WHERE lifetime_id IN (%s)"
                                              " ORDER BY sort_order, account_id" % ",".join("?" for _ in nav.lifetime_ids), list(nav.lifetime_ids)).fetchall()] if nav.lifetime_ids else []
    rows = [r for r in rows if r["account_id"] in nav.by_account or r["status"] == "OPEN"]
    counts = _counts_sql(rt, [r["account_id"] for r in rows])
    keys = ("settled_c", "available_c", "reserved_c", "restricted_c", "receivable_c", "payable_c")
    by_ccy: Dict[str, Dict[str, int]] = {}
    other_adj: Dict[str, Dict[str, Any]] = {}
    accounts: List[dict] = []
    nav_c = int(nav.nav_c)
    for r in rows:
        aid = r["account_id"]
        views = acc.cash(aid, snapshot=snap)
        usd = views.get("USD")
        row = {"account_id": aid, "name": r["name"], "kind": r["kind"], "role": r["role"], "broker": r["broker"], "status": r["status"],
               "nav_c": int(nav.by_account.get(aid, 0)), "weight": (None if nav_c <= 0 else _share(int(nav.by_account.get(aid, 0)), nav_c)),
               "currencies": sorted(c for c, v in views.items() if c == "USD" or any(int(getattr(v, k, 0) or 0) for k in ("settled_c", "receivable_c", "payable_c")))}
        row.update(counts.get(aid, {}))
        for ccy, cv in views.items():
            restricted = sum(int(a.get("amount_c") or 0) for a in (cv.adjustments or []) if a.get("key") == RESTRICTED_KEY)
            fig = {"settled_c": int(cv.settled_c), "available_c": int(cv.available_c), "reserved_c": int(cv.reserved_c), "restricted_c": restricted,
                   "receivable_c": int(cv.receivable_c), "payable_c": int(cv.payable_c)}
            if ccy != "USD" and not any(fig.values()):
                continue
            t = by_ccy.setdefault(ccy, {k: 0 for k in keys})
            for k in keys:
                t[k] += fig[k]
            for a in cv.adjustments or []:
                if a.get("key") != RESTRICTED_KEY and int(a.get("amount_c") or 0):
                    o = other_adj.setdefault(f"{a.get('key')}|{ccy}", {"key": a.get("key"), "label": a.get("label") or a.get("key"), "currency": ccy, "amount_c": 0})
                    o["amount_c"] += int(a["amount_c"])
            if ccy == "USD":
                row.update(fig)
        if usd is None:
            row.update({k: 0 for k in keys})
        accounts.append(row)
    usd_tot = {k: 0 for k in keys}
    fx_missing: List[str] = []
    rates: Dict[str, Optional[str]] = {}
    for ccy, t in by_ccy.items():
        rate = fx_rate(rt, snap, ccy)
        rates[ccy] = None if rate is None else str(rate)
        if rate is None:
            fx_missing.append(ccy)
            continue
        for k in keys:
            usd_tot[k] += t[k] if ccy == "USD" else fx_c(t[k], rate)
    cash = dict(usd_tot)
    cash.update({"present": True, "currency": "USD",
                 "by_currency": [dict(v, currency=c, usd_rate=rates.get(c)) for c, v in sorted(by_ccy.items(), key=lambda kv: (kv[0] != "USD", kv[0]))],
                 "other_adjustments": sorted(other_adj.values(), key=lambda x: (x["currency"], str(x["label"]))), "fx_missing": sorted(fx_missing),
                 "note": ("available = each account's own spendable cash, floored at zero (working orders' reservations and payables come off; "
                          "restricted collateral received may not be spent); other currencies in USD at this answer's rates")})
    return cash, accounts


def _share(part_c: int, whole_c: int):
    from decimal import Decimal
    return (Decimal(int(part_c)) / Decimal(int(whole_c))).quantize(Decimal("0.0001"))


# ---- P&L ----------------------------------------------------------------------------------------------------------------------------
def _pnl_views(rt, scope: str, snap) -> Tuple[Dict[str, Any], Dict[str, dict]]:
    """The four periods' ``PnlView`` and their API rows (``api_views.pnl_out``): one ``Accounting.pnl_periods(scope, PERIODS,
    snapshot=)`` call when the build has it (the ledger's figures computed once), else ``Accounting.pnl`` per period; one period's
    failure is said in place and never hides the others."""
    from .api_views import pnl_out
    acc = rt.accounting
    views: Dict[str, Any] = {}
    pnl: Dict[str, dict] = {}
    fn = getattr(acc, "pnl_periods", None)
    if callable(fn):
        try:
            got = fn(scope, PERIODS, snapshot=snap)
            views = {p: got[p] for p in PERIODS if p in got}
        except Exception as e:   # noqa: BLE001 - logged; each period is then asked on its own
            _failed(rt, "pnl_periods (each period asked on its own instead)", e)
            views = {}
    for p in PERIODS:
        try:
            if p not in views:
                views[p] = acc.pnl(scope, p, snapshot=snap)
            pnl[p] = pnl_out(views[p])
        except Exception as e:   # noqa: BLE001 - one period's failure must not hide the others; said in place
            views.pop(p, None)
            pnl[p] = {"period": p, "scope": scope, "error": _failed(rt, f"pnl {p}", e)}
    return views, pnl


# ---- exposure (the Risk page's figures) ---------------------------------------------------------------------------------------------
def _exposure_out(f: dict, definitions: Optional[dict]) -> dict:
    """The Exposure section from the Risk page's figures ``f`` (``EXPOSURE_KEYS``: cents, Decimal fractions)."""
    return {"present": True, "long_c": int(f["long_c"]), "short_c": int(f["short_c"]), "gross_c": int(f["gross_c"]), "net_c": int(f["net_c"]),
            "long_pct_nav": f.get("long_pct_nav"), "short_pct_nav": f.get("short_pct_nav"), "net_pct_nav": f.get("net_pct_nav"),
            "gross_pct_nav": f.get("gross_leverage"), "leverage": f.get("gross_leverage"), "gross_notional_c": int(f.get("gross_notional_c") or 0),
            "gross_notional_leverage": f.get("gross_notional_leverage"), "trs_notional_c": int(f.get("trs_notional_c") or 0), "trs_pct_nav": f.get("trs_pct_nav"),
            "dv01_c": int(f.get("dv01_c") or 0), "excess_liquidity_c": int(f.get("excess_liquidity_c") or 0), "encumbrance_c": int(f.get("encumbrance_c") or 0),
            "quality": f.get("quality"), "partial": bool(f.get("partial")), "notional_missing": list(f.get("notional_missing") or []),
            "definitions": {k: v for k, v in (definitions or {}).items() if k in EXPOSURE_DEFS}}


def _exposure_light(rt, risk, scope: str, snap, nav) -> dict:
    """The Risk page's exposure figures without the rest of its report: its per-position risk rows (``risk._pos_risk``) of this
    answer's positions and marks, their totals (``_sum_rows``) and ratios (``_ratios`` / ``_pct_signed``), the DV01 sum and
    ``Accounting.liquidity`` - each the call ``risk.report`` makes for the same figure, on the same snapshot."""
    acc = rt.accounting
    nav_c = int(nav.nav_c)
    now = int(snap.as_of())
    rows = [risk._pos_risk(rt, snap, p, now) for p in acc.positions(scope, snapshot=snap)]
    f = dict(risk._sum_rows(rows))
    f.update(risk._ratios(f["long_c"], f["short_c"], f["gross_c"], f["net_c"], f["gross_notional_c"], nav_c))
    liq = acc.liquidity(scope, snapshot=snap)
    f.update({"trs_pct_nav": risk._pct_signed(f["trs_notional_c"], nav_c), "dv01_c": abs(sum(int(r.dv01_c) for r in rows if r.dv01_c is not None)),
              "excess_liquidity_c": int(liq.excess_liquidity_c), "encumbrance_c": int(liq.encumbrance_c), "quality": nav.quality, "partial": bool(nav.partial),
              "notional_missing": [r.instrument_id for r in rows if r.gross_notional_c is None]})
    return _exposure_out(f, getattr(risk, "DEFINITIONS", None))


def exposure_of_report(view) -> dict:
    """The Exposure section from a whole ``risk.report`` (``RiskView``): the fallback, and what the tests compare the light path with."""
    return _exposure_out({k: getattr(view, k, None) for k in EXPOSURE_KEYS}, getattr(view, "definitions", None))


def _exposure_of(rt, scope: str, snap, nav) -> Tuple[dict, Optional[str]]:
    """(the Exposure section, the SOURCE_WORDS key of where it came from): ``risk.exposure_summary`` when the build has it, else the
    Risk page's row helpers, else the whole ``risk.report``; ``present`` false with the reason without the risk module."""
    risk = getattr(rt, "risk", None)
    if risk is None:
        return {"present": False, "reason": WHY["risk"]}, None
    try:
        fn = getattr(risk, "exposure_summary", None)
        if callable(fn):
            s = dict(fn(rt, scope, snapshot=snap) or {})
            return _exposure_out(s, s.get("definitions") or getattr(risk, "DEFINITIONS", None)), "exposure_summary"
        if all(callable(getattr(risk, n, None)) for n in ("_pos_risk", "_sum_rows", "_ratios", "_pct_signed")):
            return _exposure_light(rt, risk, scope, snap, nav), "exposure"
        fn = getattr(risk, "report", None)
        if callable(fn):
            return exposure_of_report(fn(rt, scope, snapshot=snap)), "exposure_report"
    except Exception as e:   # noqa: BLE001 - said in place, logged with its traceback
        return {"present": False, "reason": _failed(rt, "exposure", e)}, None
    return {"present": False, "reason": WHY["risk"]}, None


def _returns_of(ret: Optional[dict], why: Optional[str]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for p, key in RETURN_OF.items():
        if ret is None:
            out[p] = {"pct": None, "reason": why}
        else:
            v = ret.get(key)
            out[p] = {"pct": v, "reason": None if v is not None else ((ret.get("reasons") or {}).get(key) or "not computable")}
    if ret is not None:
        v = ret.get("itd_simple_pct")
        out["ITD"]["simple_pct"] = v
        out["ITD"]["simple_reason"] = None if v is not None else ((ret.get("reasons") or {}).get("itd_simple_pct") or "not computable")
    return out


# ---- the risk warnings and compliance notices: counted per book, kept, recounted in the background ---------------------------------------
def _state_version(rt) -> Optional[int]:
    fn = getattr(getattr(rt, "db", None), "state_version", None)
    try:
        return int(fn()) if callable(fn) else None
    except Exception:   # noqa: BLE001 - no version: the counts then age out by time alone
        return None


def _count_now(rt, scope: str, snap=None) -> dict:
    """Count the risk warnings and compliance notices of ``scope`` now, as ``GET /api/attention/summary`` counts them
    (``attention.slow_counts``), on ``snap`` (else a fresh snapshot).  The state version is read first, so a booking made while
    counting leaves the result already stale."""
    from .attention import slow_counts
    t0 = time.perf_counter()
    sv = _state_version(rt)
    at = int(rt.clock.epoch())
    snap = snap if snap is not None else rt.marks.snapshot()
    c = slow_counts(rt, scope, snapshot=snap)
    return {"warnings": c.get("warnings"), "compliance": c.get("compliance"), "notes": list(c.get("notes") or []), "state_version": sv, "counted_at": at,
            "marks_as_of": snap.as_of(), "ms": _ms(t0), "error": None, "retry_at": None, "thread": None}


def _counts_cache(rt) -> Optional[dict]:
    try:
        with _COUNTS_LOCK:
            c = _COUNTS.get(rt)
            if c is None:
                c = _COUNTS[rt] = {}
            return c
    except TypeError:   # a runtime that cannot be weakly referenced (a test stand-in): no cache, counted on every answer
        return None


#: the note of an answer whose counts are not there yet (the first count of the book since the start runs in the background)
COUNTING_NOTE = "risk warnings and compliance notices: being counted for this book (the first count since the start); they show on the next refresh"


def _placeholder() -> dict:
    return {"warnings": None, "compliance": None, "notes": [], "state_version": None, "counted_at": None, "marks_as_of": None, "ms": None, "error": None,
            "retry_at": None, "thread": None, "placeholder": True}


def _counts_out(entry: dict, how: str, counted_ms: float) -> dict:
    out = {k: entry.get(k) for k in ("warnings", "compliance", "state_version", "counted_at", "marks_as_of", "ms", "error")}
    notes = list(entry.get("notes") or [])
    if how == "counting":
        notes.append(COUNTING_NOTE)
    out.update({"notes": notes, "how": how, "counted_ms": counted_ms, "max_age_s": COUNTS_MAX_AGE_S})
    return out


def _recount_run(rt, cache: dict, scope: str) -> None:
    try:
        new = _count_now(rt, scope)
    except Exception as e:   # noqa: BLE001 - the last counts stay, with their time and this failure beside them; retried a minute on
        why = _failed(rt, "the background count of the risk warnings and compliance notices", e)
        with _COUNTS_LOCK:
            entry = cache.get(scope)
            if entry is not None:
                entry.update({"error": "the last count " + why, "retry_at": int(rt.clock.epoch()) + COUNTS_MAX_AGE_S, "thread": None})
        return
    with _COUNTS_LOCK:
        cache[scope] = new


def _recount(rt, scope: str) -> None:
    """Start the background (re)count of ``scope`` unless one is running (or the last one failed under COUNTS_MAX_AGE_S ago)."""
    cache = _counts_cache(rt)
    if cache is None:
        return
    now = int(rt.clock.epoch())
    with _COUNTS_LOCK:
        entry = cache.get(scope)
        if entry is None:
            return
        th = entry.get("thread")
        if (th is not None and th.is_alive()) or now < int(entry.get("retry_at") or 0):
            return
        th = threading.Thread(target=_recount_run, args=(rt, cache, scope), name="dashboard-counts", daemon=True)
        entry["thread"] = th
    th.start()


def recount_thread(rt, scope: str) -> Optional[threading.Thread]:
    """The running background recount of ``scope`` (None when none runs) - for the tests, which wait for it."""
    cache = _counts_cache(rt)
    if cache is None:
        return None
    with _COUNTS_LOCK:
        entry = cache.get(scope)
        th = entry.get("thread") if entry else None
    return th if th is not None and th.is_alive() else None


def slow_counts_of(rt, scope: str, snap=None) -> Tuple[dict, bool]:
    """(the risk warnings and compliance notices of ``scope`` for this answer, whether a background count is to start once the
    answer's own work is done).  The first: ``{warnings, compliance, notes, state_version, counted_at, marks_as_of, ms, error, how,
    counted_ms, max_age_s}`` - ``how`` "counting" (the first answer for this book since the start: None with ``COUNTING_NOTE``),
    "failed" (no count yet and the last attempt failed: None with the reason), "kept" (counted under COUNTS_MAX_AGE_S ago and the
    ledger unchanged since), "recounting" (older, or the ledger changed: the last counts with their time) or "counted now" (a runtime
    without the cache: counted on this answer's marks, ``counted_ms`` long)."""
    cache = _counts_cache(rt)
    if cache is None:
        t0 = time.perf_counter()
        return _counts_out(_count_now(rt, scope, snap), "counted now", _ms(t0)), False
    with _COUNTS_LOCK:
        entry = cache.get(scope)
        if entry is None:
            entry = cache[scope] = _placeholder()
    if entry.get("placeholder"):
        return _counts_out(entry, "failed" if entry.get("error") else "counting", 0.0), True
    stale = entry.get("state_version") != _state_version(rt) or int(rt.clock.epoch()) - int(entry.get("counted_at") or 0) >= COUNTS_MAX_AGE_S
    return _counts_out(entry, "recounting" if stale else "kept", 0.0), stale


def counts_view(rt, scope: str) -> dict:
    """The trade blotters' counters (``blotters.counts``) and the sidebar badges; ``present`` false with the reason without them."""
    fn = _provider(rt, "blotters", "counts")
    if fn is None:
        return {"scope": scope, "present": False, "reason": WHY["counters"], "counts": None, "pending_badge": None, "completed_badge": None}
    t0 = time.perf_counter()
    try:
        c = dict(fn(rt, scope) or {})
    except Exception as e:   # noqa: BLE001 - said on the page, logged with its traceback
        return {"scope": scope, "present": False, "reason": _failed(rt, "blotters.counts", e), "counts": None, "pending_badge": None, "completed_badge": None}

    def n(k):
        v = c.get(k)
        return int(v) if isinstance(v, int) and not isinstance(v, bool) else 0
    return {"scope": scope, "present": True, "reason": None, "counts": c, "pending_badge": n("working") + n("partial") + n("held"),
            "completed_badge": n("settling"), "ms": _ms(t0)}


def dashboard_view(rt, scope: str) -> dict:
    """The Dashboard answer for ``scope`` (``{lifetime}:ALL`` of the selected book) - see the module docstring."""
    from .api_views import nav_out
    from .attention import _takes_snapshot, attention_summary
    t_start = time.perf_counter()
    acc = rt.accounting
    sources: Dict[str, dict] = {}
    providers_ms: Dict[str, float] = {}
    t0 = time.perf_counter()
    snap = rt.marks.snapshot()
    sources["marks"] = {"source": "one mark snapshot for the whole answer (delayed quotes, closes and model marks, each labelled)", "as_of": snap.as_of(), "ms": _ms(t0)}

    # ---- NAV and capital -----------------------------------------------------------------------------------------------------------
    t0 = time.perf_counter()
    nav = acc.nav(scope, snapshot=snap)
    lids = list(nav.lifetime_ids)
    life = _lifetime_row(rt, lids[0]) if len(lids) == 1 else {}
    capital = {"starting_c": life.get("starting_capital_c"), "book_default_c": life.get("book_default_c"), "contributed_c": int(nav.capital_c) + int(nav.transfers_c),
               "lifetime_id": life.get("lifetime_id"), "started_ts": life.get("started_ts"),
               "note": "starting capital = the capital the lifetime opened with; contributed = deposits less withdrawals since (internal transfers net to zero)"}
    sources["nav"] = {"source": SOURCE_WORDS["nav"], "as_of": nav.as_of, "quality": nav.quality, "partial": bool(nav.partial), "ms": _ms(t0)}

    # ---- P&L by period (with the explain rows) ---------------------------------------------------------------------------------------
    t0 = time.perf_counter()
    views, pnl = _pnl_views(rt, scope, snap)
    sources["pnl"] = {"source": SOURCE_WORDS["pnl"], "as_of": nav.as_of, "quality": nav.quality, "ms": _ms(t0)}
    explain = list((pnl.get("ITD") or {}).get("explain") or [])

    # ---- exposure (the Risk page's figures) ------------------------------------------------------------------------------------------
    t0 = time.perf_counter()
    exposure, via = _exposure_of(rt, scope, snap, nav)
    sources["exposure"] = {"source": SOURCE_WORDS[via] if via else exposure.get("reason"), "as_of": snap.as_of() if via else None, "quality": exposure.get("quality"),
                           "ms": _ms(t0)}

    # ---- cash and the accounts table ---------------------------------------------------------------------------------------------------
    t0 = time.perf_counter()
    try:
        cash, accounts = _cash_and_accounts(rt, nav, snap)
    except Exception as e:   # noqa: BLE001
        cash, accounts = {"present": False, "reason": _failed(rt, "cash", e)}, []
    sources["cash"] = {"source": SOURCE_WORDS["cash"], "as_of": snap.as_of(), "ms": _ms(t0)}
    sources["accounts"] = {"source": SOURCE_WORDS["accounts"], "as_of": nav.as_of, "quality": nav.quality, "ms": 0.0}

    # ---- the attention summary: counted now, with the risk warnings and compliance notices as last counted for this book -------------
    t0 = time.perf_counter()
    sc: Optional[dict] = None
    start_count = False
    try:
        sc, start_count = slow_counts_of(rt, scope, snap)
        notes = list(sc["notes"]) + ([sc["error"]] if sc.get("error") else [])
        attention = attention_summary(rt, scope, snapshot=snap, counts={"warnings": sc["warnings"], "compliance": sc["compliance"], "notes": notes})
    except Exception as e:   # noqa: BLE001
        attention = {"present": False, "reason": _failed(rt, "attention summary", e)}
    sources["attention"] = {"source": SOURCE_WORDS["attention"], "as_of": attention.get("as_of") if isinstance(attention, dict) else None, "ms": _ms(t0), "counts": sc}
    own_ms = _ms(t_start)

    # ---- optional providers (asked only when their module is bound) ------------------------------------------------------------------
    t0 = time.perf_counter()
    fn = _provider(rt, "performance", "returns_summary")
    ret, why = None, None
    if fn is None:
        why = WHY["returns"]
    else:
        try:
            ret = fn(rt, scope, snapshot=snap, today=views.get("TODAY"), nav=nav)
        except Exception as e:   # noqa: BLE001
            why = _failed(rt, "returns summary", e)
    providers_ms["returns"] = _ms(t0)
    returns = _returns_of(ret, why)
    sources["returns"] = {"source": SOURCE_WORDS["returns"] if ret is not None else why, "as_of": (ret or {}).get("as_of"), "ms": providers_ms["returns"],
                          "present": ret is not None}

    t0 = time.perf_counter()
    fn = _provider(rt, "collateral", "summary")
    summ, why_c = None, None
    if fn is None:
        why_c = WHY["collateral"]
    else:
        try:
            summ = dict((fn(rt, scope, snapshot=snap) if _takes_snapshot(fn) else fn(rt, scope)) or {})
        except Exception as e:   # noqa: BLE001
            why_c = _failed(rt, "collateral summary", e)
    providers_ms["collateral"] = _ms(t0)
    if summ is None:
        margin = {"present": False, "reason": why_c}
        collateral = {"present": False, "reason": why_c}
    else:
        margin = {"present": True, "required_c": summ.get("margin_required_c"), "posted_c": summ.get("margin_posted_c"), "excess_c": summ.get("margin_excess_c"),
                  "open_calls": summ.get("open_calls")}
        collateral = {"present": True, "posted_c": summ.get("collateral_posted_c"), "received_c": summ.get("collateral_received_c"),
                      "encumbered_c": summ.get("encumbered_c"), "restricted_c": summ.get("restricted_c"), "failed_settlements": summ.get("failed_settlements"),
                      "by_counterparty": list(summ.get("by_counterparty") or [])}
    sources["margin"] = {"source": SOURCE_WORDS["margin"] if summ is not None else why_c, "ms": providers_ms["collateral"], "present": summ is not None}
    sources["collateral"] = {"source": SOURCE_WORDS["collateral"] if summ is not None else why_c, "ms": 0.0, "present": summ is not None}

    t0 = time.perf_counter()
    cv = counts_view(rt, scope)
    providers_ms["counters"] = _ms(t0)
    counters = dict(cv.get("counts") or {}) if cv.get("present") else {}
    counters.update({"present": bool(cv.get("present")), "reason": cv.get("reason")})
    sources["counters"] = {"source": SOURCE_WORDS["counters"] if cv.get("present") else cv.get("reason"), "ms": providers_ms["counters"], "present": bool(cv.get("present"))}

    for p in PERIODS:
        if isinstance(pnl.get(p), dict):
            pnl[p]["return"] = returns.get(p)
    total_ms = _ms(t_start)
    if start_count:
        _recount(rt, scope)                  # after the answer's own work and the providers: the count never slows this answer
    return {"scope": scope, "lifetime_ids": lids, "book_code": life.get("book_code"), "book_name": life.get("book_name"), "as_of": snap.as_of(),
            "capital": capital, "nav": nav_out(nav), "pnl": pnl, "returns": ret if ret is not None else {"present": False, "reason": why},
            "explain": explain, "exposure": exposure, "cash": cash, "margin": margin, "collateral": collateral, "accounts": accounts, "counters": counters,
            "attention": attention, "sources": sources,
            "timing": {"own_ms": own_ms, "providers_ms": providers_ms, "total_ms": total_ms, "budget_ms": BUDGET_MS, "within_budget": own_ms <= BUDGET_MS,
                       "counts": (sc or {}).get("how"), "counts_ms": (sc or {}).get("counted_ms")}}


# ---- routes -----------------------------------------------------------------------------------------------------------------------
def _scope(req) -> str:
    """The selected book's open lifetime (``?book=``; else the selected book): ``{lifetime}:ALL``."""
    return f"{req.lifetime()}:ALL"


def register_routes(router) -> None:
    def dashboard(req):
        return dashboard_view(req.rt, _scope(req))

    def counts(req):
        return counts_view(req.rt, _scope(req))
    router.add("GET", r"/api/dashboard", dashboard)
    router.add("GET", r"/api/dashboard/counts", counts)


__all__ = ["dashboard_view", "counts_view", "slow_counts_of", "exposure_of_report", "recount_thread", "register_routes", "PERIODS", "BUDGET_MS",
           "COUNTS_MAX_AGE_S", "COUNTING_NOTE", "WHY"]
