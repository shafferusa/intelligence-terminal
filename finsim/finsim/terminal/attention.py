"""The "Requires your attention" summary and the book of an attention row (TERMINAL_DESIGN.md §17.9; FINSIM1_PLAN C3).

FinSim1 1.0.5 retired the Daily News feature (owner directive 2026-10-27 section 2).  The operational attention code it carried
lives here, copied verbatim from the old ``news.py`` (``attention_lifetime``, ``attention_book``, ``attention_in_books``,
``attention_summary`` and their helpers) with two changes only:
- the summary has no news state (no ``news`` key);
- provider pauses ignore the retired news providers ``yahoo_news``, ``fed``, ``newspaper`` and ``news_*``
  (``retired_provider``); their stored ``provider_health`` rows stay in the database, inert, and the server skips them on load.

Conventions (unchanged)
- ``attention_summary(services, scope)``: every count comes from the ledger tables (``orders``, ``settlements``,
  ``borrows``, ``events``, ``otc_vm``, ``attention``, ``provider_health``), from ``Accounting.last_report()`` /
  ``hold_state()`` and from the optional ``risk`` / ``compliance`` modules - nothing is computed from marks and the page
  computes nothing.  "Today" is the accounting day (D-1 17:00 ET, D 17:00 ET], so a 17:10 collateral call still shows
  the next morning.  A missing optional module answers ``None`` (unavailable) with a note, never 0.
- Served by ``GET /api/attention/summary?book=`` (api.py); the global strip keeps ``GET /api/attention``.
"""
from __future__ import annotations

import json
import re
from datetime import timedelta
from typing import List, Optional, Tuple

from .clock import accounting_day, epoch_of

COMPLIANCE_QUIET = ("ok", "n/a", "na", "")    # compliance statuses that are not notices (SHELL-11: never counted)
OPEN_ORDER_STATUSES = ("WORKING", "PARTIAL", "SUSPENDED", "WAITING_PARENT")
SEVERITIES = ("ERROR", "WARN", "INFO")
ATTENTION_ITEMS = 25
PROBLEMS_SHOWN = 20
#: the providers of the retired news feature (1.0.4 and earlier): never counted, never shown, their stored rows left inert
RETIRED_PROVIDERS = ("yahoo_news", "fed", "newspaper")
RETIRED_PROVIDER_PREFIX = "news_"


def retired_provider(name) -> bool:
    """True for a provider of the retired news feature (``yahoo_news``, ``fed``, ``newspaper``, ``news_*``)."""
    n = str(name or "")
    return n in RETIRED_PROVIDERS or n.startswith(RETIRED_PROVIDER_PREFIX)


# ---- which book an attention row belongs to (SHELL-03) -------------------------------------------------------------------------
_LIFETIME_RE = re.compile(r"^[A-Z]+[0-9]+$")


def attention_lifetime(row) -> Optional[str]:
    """The lifetime an attention row belongs to: its account's lifetime (``P1:US_EXCHANGE`` -> ``P1``), a ``lifetime`` ref
    (``ref_type='lifetime'``), or ``details.lifetime_id`` / ``details.closed``; None for a global row (providers, the
    whole-database integrity run, the curve, workers, API incidents)."""
    get = row.get if isinstance(row, dict) else (lambda k: row[k] if k in row.keys() else None)
    aid = get("account_id")
    if aid and ":" in str(aid):
        lid = str(aid).split(":", 1)[0]
        if _LIFETIME_RE.match(lid):
            return lid
    if get("ref_type") == "lifetime" and get("ref_id") and _LIFETIME_RE.match(str(get("ref_id"))):
        return str(get("ref_id"))
    raw = get("details_json")
    if raw:
        try:
            d = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (TypeError, ValueError):
            d = {}
        for k in ("lifetime_id", "closed"):
            v = d.get(k) if isinstance(d, dict) else None
            if isinstance(v, str) and _LIFETIME_RE.match(v):
                return v
    return None


def attention_book(row) -> Optional[str]:
    """The book code of an attention row (``P`` / ``F`` / ``I`` ...), None for a global row."""
    lid = attention_lifetime(row)
    return re.match(r"^([A-Z]+)", lid).group(1) if lid else None


def attention_in_books(row, books) -> bool:
    """True when the row is global or belongs to one of ``books`` (book codes; None = every book, the consolidated view)."""
    if books is None:
        return True
    b = attention_book(row)
    return b is None or b in books


# ---- the attention summary -----------------------------------------------------------------------------------------------
def _in(col: str, values: List[str]) -> Tuple[str, list]:
    return f"{col} IN ({','.join('?' for _ in values)})", list(values)


def _resolve_scope(accounting, conn, scope: str):
    resolver = getattr(accounting, "scope", None) or getattr(accounting, "_scope")
    return resolver(conn, scope)


def _count(conn, sql: str, params=()) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def attention_summary(services, scope: str = "ALL", check: bool = False) -> dict:
    """The "Requires your attention" strip (§17.9): integrity (ok / the failing checks, from the last recorded report —
    ``check=True`` runs the ledger-level set first), INTEGRITY_HOLD, open attention items by severity (+ the newest
    rows), risk warnings and compliance notices (``services.risk`` / ``services.compliance`` when loaded, else None),
    collateral calls today (COLLATERAL_MARKED events with ``delta_c > 0`` in the accounting day plus swap VM payments
    ``otc_vm.delta_c < 0`` posted in the accounting day whose business date ``vm_date`` is the last business day before it or
    later - an as-of VM of an older business date posted today is not today's call, P8L-01), recall notices, working orders,
    pending settlements, provider pauses (the retired news providers never count).  Every number is a row count or a recorded
    report; nothing here touches a mark."""
    acc = services.accounting
    db = services.db
    clock = services.clock
    now = int(clock.epoch())
    aday = accounting_day(clock.now())
    day_start = epoch_of(aday - timedelta(days=1), "17:00")
    notes: List[str] = []
    # integrity first: the last recorded whole-DB report, or the ledger-level set run now (check=True / nothing recorded yet) -
    # before the counts, so a hold cleared by this run is not still counted as an open ERROR below
    report = acc.last_report()
    if check or report is None:
        report = acc.integrity("ledger").to_dict()
    with db.reader() as conn:
        sc = _resolve_scope(acc, conn, scope)
        accounts = [a["account_id"] for a in sc.accounts]
        lids = list(sc.lifetime_ids)
        working = pending = recalls = marks = vm = 0
        open_by = {s: 0 for s in SEVERITIES}
        items: List[dict] = []
        if accounts:
            w, p = _in("account_id", accounts)
            sts, ps = _in("status", list(OPEN_ORDER_STATUSES))
            working = _count(conn, f"SELECT COUNT(*) FROM orders WHERE {sts} AND {w}", ps + p)
            pending = _count(conn, f"SELECT COUNT(*) FROM settlements WHERE status='PENDING' AND {w}", p)
            recalls = _count(conn, f"SELECT COUNT(*) FROM borrows WHERE status='OPEN' AND recall_notice IS NOT NULL AND {w}", p)
            for r in conn.execute(f"SELECT payload_json FROM events WHERE type='COLLATERAL_MARKED' AND ts > ? AND ts <= ? AND {w}", [day_start, now] + p).fetchall():
                try:
                    delta = int((json.loads(r["payload_json"] or "{}") or {}).get("delta_c") or 0)
                except (ValueError, TypeError):
                    delta = 0
                if delta > 0:
                    marks += 1
            wc, pc = _in("c.account_id", accounts)
            # P8L-01: a swap VM payment posted within the window is a call of the accounting day only when its BUSINESS date (otc_vm.vm_date,
            # the date its entry carries) is the last business day before the accounting day or later: the VM of D runs from 17:05 ET on D,
            # inside accounting day D+1, and a VM whose input is published late (a Friday CDX VM posted on Monday morning) is still that
            # day's.  An as-of catch-up that posts older dates' VM now (the historical import's VM of 10-21 .. 10-26, posted at 00:37 on
            # 10-28) does not make them today's calls.  A row without an entry is dated by its business date alone (aday-1 or aday).
            cal = getattr(services, "cal", None)
            floor = cal.prev_business_day(aday) if cal is not None and hasattr(cal, "prev_business_day") else aday - timedelta(days=1)
            vm = _count(conn, "SELECT COUNT(*) FROM otc_vm v JOIN otc_contracts c ON c.contract_id=v.contract_id LEFT JOIN journal_entries e ON e.entry_id=v.entry_id"
                              f" WHERE v.delta_c < 0 AND {wc} AND ((e.ts IS NOT NULL AND e.ts > ? AND e.ts <= ? AND v.vm_date >= ?)"
                              " OR (e.ts IS NULL AND v.vm_date IN (?, ?)))",
                        pc + [day_start, now, floor.isoformat(), (aday - timedelta(days=1)).isoformat(), aday.isoformat()])
        # open attention: the SAME set as GET /api/attention for this book (SHELL-03): global rows plus every row that belongs to a
        # lifetime of the scope's books (account, lifetime ref or details.lifetime_id); the consolidated scope counts every row
        books = None if sc.kind == "ALL_BOOKS" else {re.match(r"^([A-Z]+)", lid).group(1) for lid in lids}
        acct_set = set(accounts)

        def keep(x) -> bool:
            if sc.kind == "ACCOUNT" and x["account_id"]:
                return x["account_id"] in acct_set                # an account scope: that account's rows (+ the book's / global ones below)
            return attention_in_books(x, books)
        rows_all = conn.execute("SELECT id, ts, severity, source, code, message, account_id, ref_type, ref_id, details_json FROM attention"
                                " WHERE resolved_ts IS NULL ORDER BY id DESC").fetchall()
        for r in (x for x in rows_all if keep(x)):
            sev = r["severity"] if r["severity"] in open_by else "INFO"
            open_by[sev] += 1
            if len(items) < ATTENTION_ITEMS:
                items.append({"id": int(r["id"]), "ts": int(r["ts"]), "severity": r["severity"], "source": r["source"], "code": r["code"], "message": r["message"],
                              "account_id": r["account_id"], "ref_type": r["ref_type"], "ref_id": r["ref_id"]})
        providers = _provider_pauses(services, conn, now)
    # the report filtered to the scope's lifetimes (a problem without a lifetime belongs to every scope)
    problems = [p for p in (report.get("problems") or []) if p.get("lifetime_id") in (None, *lids)]
    failing = sorted({p["id"] for p in problems if p.get("severity") == "ERROR"})
    integrity = {"ok": not failing, "checked_at": report.get("checked_at"), "mode": report.get("mode"), "failing": failing,
                 "warnings": sum(1 for p in problems if p.get("severity") == "WARN"), "problems": problems[:PROBLEMS_SHOWN]}
    holds = acc.hold_state()
    held = [lid for lid in lids if lid in holds]
    hold = {"held": bool(held), "lifetimes": held, "since": {lid: holds[lid].get("since") for lid in held}, "failing": {lid: holds[lid].get("failing") for lid in held}}
    warnings = _optional_count(services, "risk", ("warnings", "report"), scope, notes, "warnings")
    compliance = _optional_count(services, "compliance", ("notices", "holdings"), scope, notes, "notices")
    return {"scope": scope, "scope_kind": sc.kind, "lifetimes": lids, "accounts": accounts, "as_of": now, "accounting_day": aday.isoformat(), "window_start": day_start,
            "integrity": integrity, "hold": hold, "open": open_by, "attention_open": sum(open_by.values()), "items": items,
            "warnings": warnings, "compliance": compliance,
            "collateral_calls_today": marks + vm, "collateral_marks_today": marks, "swap_vm_payments_today": vm,
            "recalls": recalls, "working_orders": working, "pending_settlements": pending,
            "providers": providers, "notes": notes}


def _optional_count(services, module_attr: str, fn_names: Tuple[str, ...], scope: str, notes: List[str], list_attr: str) -> Optional[int]:
    """Count from an optional module: ``risk.warnings(services, scope)`` / ``risk.report(services, scope).warnings`` or
    ``compliance.notices(...)`` / ``compliance.holdings(...)``.  Absent module → None + a note (unavailable, never 0)."""
    mod = getattr(services, module_attr, None)
    if mod is None:
        notes.append(f"{module_attr} module not loaded")
        return None
    for name in fn_names:
        fn = getattr(mod, name, None)
        if fn is None:
            continue
        try:
            res = fn(services, scope)
        except Exception as e:   # noqa: BLE001 — the strip must still answer; the failure is named
            notes.append(f"{module_attr}.{name} failed: {type(e).__name__}")
            return None
        if isinstance(res, (list, tuple)):
            if module_attr == "compliance":
                # SHELL-11: only notices count (info / notice / warn / breach); 'ok' and 'n/a' rows ("no short positions") never do
                return sum(1 for n in res if str(getattr(n, "status", None) or (n.get("status") if isinstance(n, dict) else None) or "").lower() not in COMPLIANCE_QUIET)
            return len(res)
        seq = getattr(res, list_attr, None)
        if seq is None and isinstance(res, dict):
            seq = res.get(list_attr)
        if seq is not None:
            return len(seq)
        return 0
    notes.append(f"{module_attr} module has no {' / '.join(fn_names)}")
    return None


def _provider_pauses(services, conn, now: int) -> dict:
    """Providers currently down / refused: the in-memory ``ProviderHealth`` snapshot when the runtime has one, else the
    persisted ``provider_health`` rows.  The retired news providers (``retired_provider``) are never counted (C3): their
    stored rows stay in the database, inert."""
    down: List[str] = []
    refused: List[str] = []
    providers = getattr(services, "providers", None)
    health = getattr(providers, "health", None) if providers is not None else None
    if health is not None and hasattr(health, "snapshot"):
        for name, s in sorted(health.snapshot().items()):
            if retired_provider(name):
                continue
            if s.get("down") or s.get("ok") is False:
                down.append(name)
            if s.get("refused_ua"):
                refused.append(name)
    else:
        for r in conn.execute("SELECT provider, last_ok_ts, last_err_ts, down_until_ts FROM provider_health ORDER BY provider").fetchall():
            if retired_provider(r["provider"]):
                continue
            if (r["down_until_ts"] or 0) > now or (r["last_err_ts"] is not None and (r["last_ok_ts"] is None or int(r["last_err_ts"]) > int(r["last_ok_ts"]))):
                down.append(r["provider"])
    return {"down": down, "refused_ua": refused, "paused": len(down)}


__all__ = ["attention_summary", "attention_lifetime", "attention_book", "attention_in_books", "retired_provider", "RETIRED_PROVIDERS",
           "OPEN_ORDER_STATUSES", "COMPLIANCE_QUIET", "SEVERITIES", "ATTENTION_ITEMS", "PROBLEMS_SHOWN"]
