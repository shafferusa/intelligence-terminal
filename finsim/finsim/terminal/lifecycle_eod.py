"""Lifecycle jobs, part 2: MARKS_RECORD, SNAPSHOT, EXPIRE_ORDERS, COLLATERAL_MARK, BORROW_CLEANUP, HOUSEKEEPING,
INTEGRITY_FULL, the product-handler jobs (options, futures, swaps, TRS / XCCY, dividends, private credit) and the
REGISTRY in the amended tick order (TERMINAL_DESIGN.md §6.6, §6.8, §11.1, §11.2, §11.4, §13.15; WP-08).

Conventions stated here
- TICK ORDER (§11.1): SETTLE_DUE → ACCRUE_DAILY → PAY_MONTHLY → PB_MONTHLY_MIN → DIVIDEND → DIVPAY → PC_PAY → SWAP_FIX →
  SWAP_PAY → TRS_RESET → XCCY_EVENTS → OPTION_EXPIRY → FUTURES_VM → FUTURES_FINAL → SWAP_VM → OPTION_MARGIN_MARK →
  COLLATERAL_MARK → BORROW_CLEANUP → SWEEP_OUT → MARKS_RECORD → SNAPSHOT → EXPIRE_ORDERS → HOUSEKEEPING → INTEGRITY_FULL.
  The scheduler iterates the phase-1 jobs (up to SNAPSHOT) until a pass completes nothing new, so a catch-up tick
  resolves the dependencies between jobs (a CLOSE recorded late feeds the collateral mark, the mark feeds the night's
  accrual) inside ONE tick; SNAPSHOT then sees the settled state; expiry, housekeeping and integrity run last.
- MARKS (§11.4): the US CLOSE is captured from 16:20 ET (index options 16:35; every group: its session close + 20 min)
  from a record whose ``quote_time`` is on that date and at or after the session end − 60 s; futures SETTLE at the
  group's settle time + 5 min from the print at or before the settle time (``products.futures.select_print``); FX
  at 17:05.  After downtime the missed dates come from ``YahooChart.daily`` bars (method ``daily_close``, note
  "recorded late") — fetched scheduler-side, outside any transaction, and written through the light command
  ``lifecycle.marks_record``; a date with no daily bar (symbol delisted) stays unrecorded with one WARN attention
  MARK_MISSING; a provider failure defers the key an hour.  SPY is always recorded (the §6.9 benchmark).  When the
  next session's ``prev_close`` differs from a recorded CLOSE by more than one tick an INFO CLOSE_REVISED item
  reports it; postings already made are never reversed.  Held and working instruments, the underlyings of held
  options and the FX pairs of foreign cash balances are the recorded set.
- SNAPSHOT (§6.6): at the ``snapshot_time_et`` setting (17:45) every calendar day, per OPEN account and per lifetime
  (``{lifetime}:ALL``), key ``{scope}:{date}``, from ``accounting.snapshot_row`` (NAV, cumulative components,
  ``flows_day_c``, ``pnl_day_c``, ``partial``, ``missing``) — catch-up writes the missed days from the ledger as it
  stands, flagged ``late`` in ``detail_json``; an existing row is never replaced (``nav_snapshots`` PK = idem).  The
  job first calls ``books.unlock_private`` (scheduler-side) so a lifetime that crossed the QP threshold gets its
  Private Markets account before its snapshot.
- EXPIRY: EXPIRE_ORDERS runs ``order.expire`` per order whose ``expires_ts`` has passed (DAY at the session close,
  GTC after 90 days) or whose instrument expired; nothing fills after expiry.  COLLATERAL_MARK runs
  ``credit.collateral_mark`` at 17:10 per OPEN borrow and trading day from the recorded CLOSE (missing → SKIPPED,
  retried); a past day waits until the nights before it are accrued (the interleave of ``lifecycle_jobs``), today's
  never waits.  BORROW_CLEANUP returns borrowed quantity backing nothing (``lending.cleanup_due``).
- HOUSEKEEPING (00:15 daily, key ``{date}``): lists the next futures months and marks contracts past their last trade
  date EXPIRED (``InstrumentMaster.ensure_futures_listings``), marks expired options EXPIRED, re-seeds the private
  company master (new rows only), resolves FUNDING_SHORTFALL items whose cash is back at or above zero, prunes
  ``quote_cache`` rows of instruments nobody holds, works or watches (older than a day) — and never touches
  ``order_events``; the master is reloaded after the commit when listings changed.
- INTEGRITY_FULL (hourly at :05, key ``{YYYY-MM-DDTHH}``, and on the scheduler's first tick): ``accounting.integrity
  ("full")`` runs scheduler-side on a reader connection (it records ``meta.integrity_json``, the per-lifetime
  INTEGRITY_HOLD and its attention rows itself); the light command ``lifecycle.integrity_full`` then records the key.
- PRODUCT JOBS: the product handlers write their own ``jobs_done`` row (DONE, or SKIPPED with a note and a WARN
  attention) — futures (``vm_keys`` / ``final_keys``) and options (``expiry_keys`` / ``margin_mark_keys``) exist;
  swaps, TRS / XCCY, dividends and private credit are later packages: each is skipped with a note until its module is
  bound as ``services.<name>`` and exposes the key helper named below, taking ``(conn, now_ts)`` and returning tuples
  in the key's field order — ``swaps.fix_keys / pay_keys / vm_keys`` → ``(contract_id, date)``; ``swaps_trs.reset_keys``
  → ``(contract_id, date)`` and ``event_keys`` → ``(contract_id, date, event)`` with event ``START | END | FX_RESET``
  (``xccy.exchange``, or ``xccy.fx_reset`` for FX_RESET); ``dividends.ex_keys / pay_keys`` → ``(account_id,
  instrument_id, ex_date)``; ``private_credit.pay_keys`` → ``(loan_id, date)``.  A FUTURES_VM key SKIPPED for a missing
  print is retried at once with the daily-bar close (``futures.daily_fallback``), labelled "recorded late".
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple

from .books import account_row, open_lifetimes
from .clock import epoch_of, ny_of
from .commands import Command, RejectError, TxContext
from .db import canonical_json, dumps, get_setting
from .lifecycle_jobs import (ACTOR, SOURCE, AccrueDaily, Job, PayMonthly, PbMonthlyMin, SettleDue, SweepOut, accrual_state_row, last_trading_day, module, open_accounts,
                             trading_days)
from .units import fmt_money, qprice

CLOSE_DELAY_S = 20 * 60           # US CLOSE at the session close + 20 min (16:20; index options 16:35)
SETTLE_DELAY_S = 5 * 60           # futures SETTLE at settle time + 5 min; FX at 17:05
MARKS_LOOKBACK_DAYS = 10
MISSING_RETRY_S = 3600            # a date whose daily bar is missing or whose provider failed: retried hourly
MISSING_PARK_S = 86400            # after MARK_MISSING was raised: once a day
COLLATERAL_MARK_ET = "17:10"
MARGIN_MARK_ET = "17:10"
HOUSEKEEPING_ET = "00:15"
INTEGRITY_MINUTE = 5
BENCHMARK = "SPY"


# ---- helpers ------------------------------------------------------------------------------------------------------------------
def recorded_set(conn: sqlite3.Connection, master) -> Dict[str, Any]:
    """Instruments whose end-of-day marks are recorded: held / working / borrowed names of OPEN lifetimes, the
    underlyings of held options, the FX pairs of foreign cash balances and the benchmark."""
    lids = open_lifetimes(conn)
    if not lids:
        return {}
    q = ",".join("?" for _ in lids)
    ids = set()
    for r in conn.execute(f"SELECT DISTINCT v.instrument_id FROM v_positions v JOIN accounts a ON a.account_id=v.account_id WHERE v.qty<>0 AND a.lifetime_id IN ({q})", lids).fetchall():
        ids.add(r["instrument_id"])
    for r in conn.execute(f"SELECT DISTINCT o.instrument_id FROM orders o JOIN accounts a ON a.account_id=o.account_id"
                          f" WHERE o.status IN ('WORKING','PARTIAL','SUSPENDED','WAITING_PARENT') AND a.lifetime_id IN ({q})", lids).fetchall():
        ids.add(r["instrument_id"])
    for r in conn.execute(f"SELECT DISTINCT b.instrument_id FROM borrows b JOIN accounts a ON a.account_id=b.account_id WHERE b.status='OPEN' AND a.lifetime_id IN ({q})", lids).fetchall():
        ids.add(r["instrument_id"])
    for r in conn.execute(f"SELECT DISTINCT g.currency FROM gl_balances g JOIN accounts a ON a.account_id=g.account_id WHERE g.gl='1010' AND g.currency<>'USD'"
                          f" AND g.balance_c<>0 AND a.lifetime_id IN ({q})", lids).fetchall():
        pair = r["currency"] + "USD"
        if master.get(pair, conn) is None:
            pair = "USD" + r["currency"]
        ids.add(pair)
    ids.add(BENCHMARK)
    out: Dict[str, Any] = {}
    for iid in sorted(ids):
        inst = master.get(iid, conn)
        if inst is None:
            continue
        out[iid] = inst
        if inst.kind == "OPTION" and inst.underlying_id:
            u = master.get(inst.underlying_id, conn)
            if u is not None:
                out[u.instrument_id] = u
    return out


def mark_kind(inst) -> str:
    return "SETTLE" if inst.kind == "FUTURE" else "CLOSE"


def record_time(sessions, inst, d: date) -> Optional[int]:
    """When the end-of-day mark of ``inst`` for ``d`` can be captured (None: not a trading day of its group)."""
    group = inst.session_group
    try:
        if inst.kind == "FUTURE":
            st = sessions.settle_time(group, d)
            return None if st is None else int(st) + SETTLE_DELAY_S
        close = sessions.session_close(group, d)
    except KeyError:
        return None
    if close is None:
        return None
    return int(close) + (SETTLE_DELAY_S if group == "FX_SPOT" else CLOSE_DELAY_S)


def _quote_record(services, iid: str):
    cache = getattr(services, "cache", None)
    if cache is not None and hasattr(cache, "get"):
        try:
            return cache.get(iid)
        except Exception:   # noqa: BLE001 — a cache fault is "no record", never a number
            return None
    marks = getattr(services, "marks", None)
    if marks is not None and hasattr(marks, "quote"):
        return marks.quote(iid)
    return None


def _daily_provider(services):
    providers = getattr(services, "providers", None)
    yahoo = getattr(providers, "yahoo", None) if providers is not None else None
    if yahoo is not None and hasattr(yahoo, "daily"):
        return yahoo
    fp = getattr(services, "fp", None)
    return fp if fp is not None and hasattr(fp, "daily") else None


# ---- MARKS_RECORD ------------------------------------------------------------------------------------------------------------------
class MarksRecord(Job):
    name = "MARKS_RECORD"
    kind = "lifecycle.marks_record"
    when = "session close + 20 min (US 16:20), futures settle + 5 min, FX 17:05; missed dates from daily bars"
    light = True
    batch = True

    def due(self, conn, clock, cal, sessions) -> List[str]:
        master = getattr(self.services, "instruments", None)
        if master is None:
            return []
        now = clock.epoch()
        today = clock.today()
        starts = [ny_of(int(r["started_ts"])).date() for r in conn.execute("SELECT started_ts FROM lifetimes WHERE status='OPEN'").fetchall()]
        if not starts:
            return []
        since = max(today - timedelta(days=MARKS_LOOKBACK_DAYS), min(starts))      # never before the books existed
        keys: List[str] = []
        for iid, inst in recorded_set(conn, master).items():
            if inst.kind in ("PRIVATE", "SWAP", "BOND", "YIELD"):
                continue
            kind = mark_kind(inst)
            first = conn.execute("SELECT MIN(trade_date) d FROM fills WHERE instrument_id=?", (iid,)).fetchone()
            inst_since = max(since, date.fromisoformat(first["d"])) if first is not None and first["d"] else since
            have = {r["mark_date"] for r in conn.execute("SELECT mark_date FROM marks WHERE instrument_id=? AND kind=? AND mark_date>=?", (iid, kind, inst_since.isoformat())).fetchall()}
            for d in trading_days(sessions, inst.session_group, inst_since, today):
                if d.isoformat() in have:
                    continue
                if inst.expiry and d.isoformat() > inst.expiry:
                    continue
                ready = record_time(sessions, inst, d)
                if ready is None or now < ready:
                    continue
                keys.append(f"{iid}:{d.isoformat()}")
        return keys

    # -- print resolution (scheduler-side, outside any transaction)
    def _from_record(self, services, inst, d: date, now: int) -> Optional[dict]:
        rec = _quote_record(services, inst.instrument_id)
        if rec is None or getattr(rec, "error", None):
            return None
        sessions = services.sessions
        if inst.kind == "FUTURE":
            fut = module(services, "futures", "finsim.terminal.products.futures")
            st = sessions.settle_time(inst.session_group, d)
            if fut is None or st is None or not hasattr(fut, "select_print"):
                return None
            try:
                lo = sessions.sessions_for(inst.session_group, d)[0].open_ts
            except (KeyError, IndexError):
                lo = None
            obs = fut.select_print(rec, d, int(st), lo)
            if obs is None:
                return None
            return {"instrument_id": inst.instrument_id, "mark_date": d.isoformat(), "kind": "SETTLE", "price": str(obs.price), "currency": inst.currency, "quality": "close",
                    "source": obs.source, "method": "direct", "quote_time": obs.quote_time, "fetched_at": obs.fetched_at, "note": obs.note}
        close = sessions.session_close(inst.session_group, d)
        qt = getattr(rec, "quote_time", None)
        px = rec.price() if hasattr(rec, "price") else getattr(rec, "last", None)
        if close is None or qt is None or px is None:
            return None
        if ny_of(int(qt)).date() != d or int(qt) < int(close) - 60:
            return None
        return {"instrument_id": inst.instrument_id, "mark_date": d.isoformat(), "kind": "CLOSE", "price": str(qprice(px)), "currency": inst.currency, "quality": "close",
                "source": getattr(rec, "source", "") or "", "method": "direct", "quote_time": int(qt), "fetched_at": int(getattr(rec, "fetched_at", 0) or now), "note": None,
                "prev_close": None if getattr(rec, "prev_close", None) is None else str(rec.prev_close)}

    def _from_daily(self, services, inst, d: date, now: int, bars_cache: dict, failures: dict) -> Tuple[Optional[dict], str]:
        """``(row, outcome)``; outcome ∈ recorded | no_bar | provider_failed | no_provider | no_symbol."""
        provider = _daily_provider(services)
        if provider is None:
            return None, "no_provider"
        sym = getattr(inst, "yahoo_symbol", None)
        if not sym or inst.kind == "OPTION":
            return None, "no_symbol"
        if sym in failures:
            return None, "provider_failed"
        if sym not in bars_cache:
            days = max(MARKS_LOOKBACK_DAYS, (ny_of(now).date() - d).days + 5)
            try:
                bars_cache[sym] = list(provider.daily(sym, days))
            except Exception as e:   # noqa: BLE001 — FetchError or a provider fault: no bar, no number
                failures[sym] = str(e)
                log = getattr(services, "log", None)
                if log is not None:
                    log.warning("MARKS_RECORD daily %s %s: %s", sym, d, e)
                return None, "provider_failed"
        bar = next((b for b in bars_cache[sym] if b.date == d), None)
        if bar is None or bar.c is None:
            return None, "no_bar"
        sessions = services.sessions
        if inst.kind == "FUTURE":
            qt = sessions.settle_time(inst.session_group, d)
        else:
            qt = sessions.session_close(inst.session_group, d)
        return {"instrument_id": inst.instrument_id, "mark_date": d.isoformat(), "kind": mark_kind(inst), "price": str(qprice(bar.c)), "currency": inst.currency,
                "quality": "close", "source": "Yahoo daily bars (observed close)", "method": "daily_close", "quote_time": None if qt is None else int(qt), "fetched_at": now,
                "note": "recorded late"}, "recorded"

    def _revision(self, conn, inst, row: dict, sessions) -> Optional[dict]:
        """CLOSE_REVISED: the record's prev_close vs the recorded CLOSE of the previous trading day, beyond one tick."""
        pc = row.get("prev_close")
        if pc is None:
            return None
        d = date.fromisoformat(row["mark_date"])
        prev = last_trading_day(sessions, inst.session_group, d - timedelta(days=1))
        if prev is None:
            return None
        rec = conn.execute("SELECT price FROM marks WHERE instrument_id=? AND mark_date=? AND kind='CLOSE'", (inst.instrument_id, prev.isoformat())).fetchone()
        if rec is None:
            return None
        diff = abs(Decimal(str(pc)) - Decimal(rec["price"]))
        if diff <= Decimal(inst.tick or "0.01"):
            return None
        return {"instrument_id": inst.instrument_id, "mark_date": prev.isoformat(), "recorded": rec["price"], "prev_close": str(pc), "diff": str(diff)}

    def run_batch(self, services, keys: List[str], scheduler) -> dict:
        now = services.clock.epoch()
        master = services.instruments
        rows: List[dict] = []
        missing: List[dict] = []
        revised: List[dict] = []
        unresolved: List[dict] = []
        bars_cache: dict = {}
        failures: dict = {}
        with services.db.reader() as conn:
            for key in keys:
                iid, ds = key.rsplit(":", 1)
                d = date.fromisoformat(ds)
                inst = master.get(iid, conn)
                if inst is None:
                    continue
                row = self._from_record(services, inst, d, now)
                outcome = "recorded"
                if row is not None:
                    rev = self._revision(conn, inst, row, services.sessions)
                    if rev is not None:
                        revised.append(rev)
                else:
                    row, outcome = self._from_daily(services, inst, d, now, bars_cache, failures)
                if row is not None:
                    row.pop("prev_close", None)
                    rows.append(row)
                    continue
                unresolved.append({"key": key, "outcome": outcome})
                if outcome == "no_bar" and d < ny_of(now).date():
                    missing.append({"instrument_id": iid, "mark_date": ds, "symbol": getattr(inst, "yahoo_symbol", None)})
                    scheduler.defer(self.name, key, MISSING_PARK_S)
                else:
                    scheduler.defer(self.name, key, MISSING_RETRY_S)
        res = None
        if rows or missing or revised:
            payload = {"rows": rows, "missing": missing, "revised": revised}
            sha = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:16]
            res = services.bus.execute(Command(self.kind, payload, f"job:{self.name}:{sha}", ACTOR))
            if not res.ok:
                raise RuntimeError(f"{self.kind} failed: {res.code} {res.message}")
        note = f"recorded {len(rows)}, unresolved {len(unresolved)}" + (f" (no daily-bar provider bound)" if any(u['outcome'] == 'no_provider' for u in unresolved) else "")
        self.notes["last"] = note
        return {"job": self.name, "status": "DONE" if rows else "SKIPPED", "recorded": [f"{r['instrument_id']}:{r['mark_date']}" for r in rows], "unresolved": unresolved,
                "missing": missing, "revised": revised, "note": note, "request_id": None if res is None else f"job:{self.name}:...", "ok": True, "http": 200 if res is None else res.http}


def h_marks_record(ctx: TxContext, payload: dict) -> dict:
    """Light: the MARKS_RECORD rows through ``quotes.rates.h_marks_record`` plus the MARK_MISSING (WARN) and CLOSE_REVISED
    (INFO) attention items, each once per (instrument, date)."""
    from .quotes import rates as _rates
    out = _rates.h_marks_record(ctx, {"rows": payload.get("rows") or []})
    opened = 0
    for m in payload.get("missing") or []:
        ref = f"{m['instrument_id']}:{m['mark_date']}"
        if ctx.conn.execute("SELECT 1 FROM attention WHERE code='MARK_MISSING' AND ref_type='mark' AND ref_id=? AND resolved_ts IS NULL", (ref,)).fetchone() is None:
            ctx.attention("WARN", SOURCE, "MARK_MISSING", f"{m['instrument_id']}: no daily bar for {m['mark_date']} (symbol {m.get('symbol') or '?'} delisted or unlisted?);"
                          " the close of that date stays unrecorded and accruals / expiries that need it wait", dict(m, ref_type="mark", ref_id=ref, auto_resolve=1))
            opened += 1
    for r in payload.get("revised") or []:
        ref = f"{r['instrument_id']}:{r['mark_date']}"
        if ctx.conn.execute("SELECT 1 FROM attention WHERE code='CLOSE_REVISED' AND ref_type='mark' AND ref_id=?", (ref,)).fetchone() is None:
            ctx.attention("INFO", SOURCE, "CLOSE_REVISED", f"{r['instrument_id']}: the next session's prev_close {r['prev_close']} differs from the recorded CLOSE {r['recorded']}"
                          f" of {r['mark_date']} by {r['diff']}; postings already made are not reversed (disclosed)", dict(r, ref_type="mark", ref_id=ref, auto_resolve=1))
            opened += 1
    return dict(out, attention=opened)


# ---- SNAPSHOT ----------------------------------------------------------------------------------------------------------------------
class Snapshot(Job):
    name = "SNAPSHOT"
    kind = "lifecycle.snapshot"
    when = "the snapshot_time_et setting (17:45) every calendar day, per account and per lifetime ({lifetime}:ALL)"
    phase = 2

    def due(self, conn, clock, cal, sessions) -> List[str]:
        now = clock.epoch()
        today = clock.today()
        hhmm = str(get_setting(conn, "snapshot_time_et", "17:45") or "17:45")
        last = today if now >= epoch_of(today, hhmm) else today - timedelta(days=1)
        keys: List[str] = []
        for lid in open_lifetimes(conn):
            lrow = conn.execute("SELECT started_ts FROM lifetimes WHERE lifetime_id=?", (lid,)).fetchone()
            started = ny_of(int(lrow["started_ts"]))
            start = started.date() if int(lrow["started_ts"]) <= epoch_of(started.date(), hhmm) else started.date() + timedelta(days=1)
            prev = conn.execute("SELECT MAX(snap_date) d FROM nav_snapshots WHERE account_id=?", (f"{lid}:ALL",)).fetchone()
            if prev is not None and prev["d"]:
                start = max(start, date.fromisoformat(prev["d"]) + timedelta(days=1))
            accounts = open_accounts(conn, None, [lid])
            d = start
            while d <= last:
                for a in accounts:
                    opened = ny_of(int(a["opened_ts"])).date()
                    if d >= opened and conn.execute("SELECT 1 FROM nav_snapshots WHERE account_id=? AND snap_date=?", (a["account_id"], d.isoformat())).fetchone() is None:
                        keys.append(f"{a['account_id']}:{d.isoformat()}")
                keys.append(f"{lid}:ALL:{d.isoformat()}")
                d += timedelta(days=1)
        return self.filter_done(conn, keys)

    def before(self, services, keys: List[str]) -> bool:
        books = getattr(services, "books", None)
        if books is None:
            from . import books as books_mod
            books = books_mod
        created = books.unlock_private(services) if hasattr(books, "unlock_private") else []
        return bool(created)

    def payload(self, key: str) -> dict:
        scope, d = key.rsplit(":", 1)
        return {"key": key, "scope": scope, "date": d}

    def run(self, ctx: TxContext, key: str, services) -> dict:
        scope, ds = key.rsplit(":", 1)
        d = date.fromisoformat(ds)
        conn = ctx.conn
        if conn.execute("SELECT 1 FROM nav_snapshots WHERE account_id=? AND snap_date=?", (scope, ds)).fetchone() is not None:
            return {"status": "DONE", "note": "already present", "posted": False}
        row = services.accounting.snapshot_row(scope, d)
        late = d < ny_of(ctx.ts).date()
        if late:
            detail = json.loads(row["detail_json"] or "{}")
            detail["late"] = True
            detail["computed_ts"] = ctx.ts
            row["detail_json"] = dumps(detail)
        services.accounting.insert_snapshot(conn, row)
        missing = json.loads(row["detail_json"] or "{}").get("missing") or []
        aid = scope if not scope.endswith(":ALL") else None
        ctx.event("NAV_SNAPSHOT", account_id=aid, ref_type="snapshot", ref_id=key, scope=scope, snap_date=ds, nav_c=int(row["nav_c"]), pnl_day_c=int(row["pnl_day_c"]),
                  flows_day_c=int(row["flows_day_c"]), partial=int(row["partial"]), late=late)
        note = f"NAV {fmt_money(int(row['nav_c']))}, day P&L {fmt_money(int(row['pnl_day_c']))}" + (f", partial (missing {', '.join(missing)})" if row["partial"] else "") + (" (late)" if late else "")
        return {"status": "DONE", "note": note, "nav_c": int(row["nav_c"]), "pnl_day_c": int(row["pnl_day_c"]), "flows_day_c": int(row["flows_day_c"]),
                "partial": bool(row["partial"]), "missing": missing, "quality": row["quality"], "late": late, "posted": True}


# ---- EXPIRE_ORDERS -------------------------------------------------------------------------------------------------------------------
LIVE = ("WORKING", "PARTIAL", "SUSPENDED", "WAITING_PARENT")


class ExpireOrders(Job):
    name = "EXPIRE_ORDERS"
    kind = "lifecycle.expire_orders"
    when = "at every session close (DAY), every tick for GTC age (90 days) and expired instruments"
    phase = 3

    def due(self, conn, clock, cal, sessions) -> List[str]:
        now = clock.epoch()
        today = clock.today().isoformat()
        rows = conn.execute("SELECT o.order_id FROM orders o JOIN instruments i ON i.instrument_id=o.instrument_id JOIN accounts a ON a.account_id=o.account_id"
                            " JOIN lifetimes l ON l.lifetime_id=a.lifetime_id WHERE o.status IN ('WORKING','PARTIAL','SUSPENDED','WAITING_PARENT') AND l.status='OPEN'"
                            " AND (o.expires_ts < ? OR i.status <> 'ACTIVE' OR (i.expiry IS NOT NULL AND i.expiry < ?)) ORDER BY o.order_id", (now, today)).fetchall()
        return self.filter_done(conn, [r["order_id"] for r in rows])

    def payload(self, key: str) -> dict:
        return {"key": key, "order_id": key}

    def run(self, ctx: TxContext, key: str, services) -> dict:
        orders = module(services, "orders", "finsim.terminal.orders")
        if orders is None or not hasattr(orders, "h_expire"):
            raise RejectError("NOT_IN_THIS_BUILD", "order expiry needs the orders module (WP-02)")
        row = ctx.conn.execute("SELECT status FROM orders WHERE order_id=?", (key,)).fetchone()
        if row is None:
            raise RejectError("NOT_FOUND", f"order {key} does not exist")
        if row["status"] not in LIVE:
            return {"status": "DONE", "note": f"already {row['status']}", "posted": False}
        out = orders.h_expire(ctx, {"order_id": key})
        expired = out.get("expired") or []
        return {"status": "DONE", "note": f"EXPIRED ({len(expired)} order{'s' if len(expired) != 1 else ''})", "expired": expired, "posted": True}


# ---- COLLATERAL_MARK / BORROW_CLEANUP -------------------------------------------------------------------------------------------------
class CollateralMark(Job):
    name = "COLLATERAL_MARK"
    kind = "lifecycle.collateral_mark"
    when = "17:10 ET on trading days per open borrow (the recorded CLOSE of the day)"

    def due(self, conn, clock, cal, sessions) -> List[str]:
        now = clock.epoch()
        today = clock.today()
        lids = open_lifetimes(conn)
        if not lids:
            return []
        q = ",".join("?" for _ in lids)
        rows = conn.execute(f"SELECT b.* FROM borrows b JOIN accounts a ON a.account_id=b.account_id WHERE b.status='OPEN' AND a.status='OPEN' AND a.lifetime_id IN ({q})"
                            " ORDER BY b.borrow_id", lids).fetchall()
        master = getattr(self.services, "instruments", None)
        keys: List[str] = []
        for b in rows:
            inst = master.get(b["instrument_id"], conn) if master is not None else None
            group = inst.session_group if inst is not None else "US_EQUITY"
            opened = date.fromisoformat(b["opened_date"])
            start = opened if not b["last_mark_date"] else max(opened, date.fromisoformat(b["last_mark_date"]) + timedelta(days=1))
            st = accrual_state_row(conn, "bfee", b["borrow_id"])
            through = st["accrued_through"] if st is not None and st["accrued_through"] else None
            for d in trading_days(sessions, group, start, today):
                if now < epoch_of(d, COLLATERAL_MARK_ET):
                    break
                if d < today and d > opened and (through is None or through < (d - timedelta(days=1)).isoformat()):
                    break                                       # a past day waits for the nights before it (interleave)
                keys.append(f"{b['borrow_id']}:{d.isoformat()}")
        return self.filter_done(conn, keys)

    def series_of(self, key: str) -> Optional[str]:
        return key.rsplit(":", 1)[0]

    def payload(self, key: str) -> dict:
        bid, d = key.rsplit(":", 1)
        return {"key": key, "borrow_id": bid, "mark_date": d}

    def run(self, ctx: TxContext, key: str, services) -> dict:
        lending = module(services, "lending", "finsim.terminal.lending")
        if lending is None or not hasattr(lending, "h_collateral_mark"):
            raise RejectError("NOT_IN_THIS_BUILD", "collateral marks need the lending module (WP-06)")
        bid, d = key.rsplit(":", 1)
        out = lending.h_collateral_mark(ctx, {"borrow_id": bid, "mark_date": d})
        if out.get("skipped"):
            return dict(out, status="DONE", note=f"already marked for {d}", posted=False)
        delta = int(out.get("delta_c") or 0)
        return dict(out, status="DONE", note=f"required {fmt_money(int(out.get('required_c') or 0))} at {out.get('mark')}, movement {fmt_money(delta)}", posted=delta != 0)


class BorrowCleanup(Job):
    name = "BORROW_CLEANUP"
    kind = "lifecycle.borrow_cleanup"
    when = "every tick: borrowed quantity backing nothing (cancelled / expired / partly filled short orders)"

    def due(self, conn, clock, cal, sessions) -> List[str]:
        lending = module(self.services, "lending", "finsim.terminal.lending")
        if lending is None or not hasattr(lending, "cleanup_due"):
            self.notes["lending"] = "skipped: lending module not built"
            return []
        lids = open_lifetimes(conn)
        today = clock.today().isoformat()
        keys = [f"{c['borrow_id']}:{today}" for c in lending.cleanup_due(conn, lids)]
        return self.filter_done(conn, keys)

    def payload(self, key: str) -> dict:
        bid, d = key.rsplit(":", 1)
        return {"key": key, "borrow_id": bid, "date": d}

    def run(self, ctx: TxContext, key: str, services) -> dict:
        lending = module(services, "lending", "finsim.terminal.lending")
        if lending is None:
            raise RejectError("NOT_IN_THIS_BUILD", "borrow cleanup needs the lending module (WP-06)")
        bid, d = key.rsplit(":", 1)
        out = lending.cleanup_borrow(ctx, services, bid, d)
        qty = int(out.get("qty") or 0)
        return dict(out, status="DONE", note=out.get("skipped") or f"returned {qty:,} borrowed shares", posted=qty > 0)


# ---- HOUSEKEEPING ------------------------------------------------------------------------------------------------------------------
class Housekeeping(Job):
    name = "HOUSEKEEPING"
    kind = "lifecycle.housekeeping"
    when = "00:15 ET daily: futures listing roll, expired instruments, private master re-seed, shortfall resolution, quote_cache prune"
    phase = 3

    def due(self, conn, clock, cal, sessions) -> List[str]:
        today = clock.today()
        if clock.epoch() < epoch_of(today, HOUSEKEEPING_ET):
            return []
        return self.filter_done(conn, [today.isoformat()])

    def payload(self, key: str) -> dict:
        return {"key": key, "date": key}

    def run(self, ctx: TxContext, key: str, services) -> dict:
        from . import execution
        conn = ctx.conn
        today = date.fromisoformat(key)
        master = services.instruments
        listed = int(master.ensure_futures_listings(ctx.tx, today, ctx.cal))
        expired_fut = [r["instrument_id"] for r in conn.execute("SELECT instrument_id FROM instruments WHERE kind='FUTURE' AND status='EXPIRED' AND updated_ts=?", (ctx.ts,)).fetchall()]
        expired_opt = [r["instrument_id"] for r in conn.execute("SELECT instrument_id FROM instruments WHERE kind='OPTION' AND status='ACTIVE' AND expiry IS NOT NULL AND expiry < ?",
                                                                (today.isoformat(),)).fetchall()]
        if expired_opt:
            conn.execute("UPDATE instruments SET status='EXPIRED', updated_ts=? WHERE kind='OPTION' AND status='ACTIVE' AND expiry IS NOT NULL AND expiry < ?", (ctx.ts, today.isoformat()))
        for iid in expired_fut + expired_opt:
            master.refresh(iid, conn)
        private_rows = int(master._seed_private(conn, ctx.ts)) if hasattr(master, "_seed_private") else 0
        resolved = 0
        for r in conn.execute("SELECT DISTINCT account_id, details_json FROM attention WHERE code='FUNDING_SHORTFALL' AND resolved_ts IS NULL AND account_id IS NOT NULL").fetchall():
            try:
                ccy = str(json.loads(r["details_json"] or "{}").get("currency") or "USD")
            except ValueError:
                ccy = "USD"
            if account_row(conn, r["account_id"]) is not None:
                resolved += execution.auto_resolve_shortfall(ctx, r["account_id"], ccy)
        keep = set(recorded_set(conn, master)) | {r["instrument_id"] for r in conn.execute("SELECT DISTINCT instrument_id FROM watchlists").fetchall()}
        rows = conn.execute("SELECT instrument_id FROM quote_cache WHERE updated_ts < ?", (ctx.ts - 86400,)).fetchall()
        drop = [r["instrument_id"] for r in rows if r["instrument_id"] not in keep]
        for iid in drop:
            conn.execute("DELETE FROM quote_cache WHERE instrument_id=?", (iid,))
        ctx.event("HOUSEKEEPING", ref_type="housekeeping", ref_id=key, listed=listed, expired_futures=expired_fut, expired_options=expired_opt, private_rows=private_rows,
                  shortfalls_resolved=resolved, quote_cache_pruned=len(drop))
        note = f"listed {listed}, expired {len(expired_fut)} futures / {len(expired_opt)} options, private rows {private_rows}, shortfalls resolved {resolved}, cache pruned {len(drop)}"
        return {"status": "DONE", "note": note, "listed": listed, "expired_futures": expired_fut, "expired_options": expired_opt, "private_rows": private_rows,
                "shortfalls_resolved": resolved, "quote_cache_pruned": drop, "reload_master": bool(listed or expired_fut or expired_opt or private_rows), "posted": False}

    def after_commit(self, services, key: str, res) -> None:
        if res is not None and res.ok and (res.body or {}).get("reload_master"):
            master = getattr(services, "instruments", None)
            if master is not None and hasattr(master, "load"):
                master.load()


# ---- INTEGRITY_FULL --------------------------------------------------------------------------------------------------------------------
class IntegrityFull(Job):
    name = "INTEGRITY_FULL"
    kind = "lifecycle.integrity_full"
    when = "hourly at :05 and on the scheduler's first tick"
    phase = 3
    light = True
    batch = True

    def due(self, conn, clock, cal, sessions) -> List[str]:
        ny = clock.ny()
        first = self.scheduler is not None and getattr(self.scheduler, "ticks", 0) == 0
        if ny.minute < INTEGRITY_MINUTE and not first:
            return []
        return self.filter_done(conn, [ny.strftime("%Y-%m-%dT%H")])

    def run_batch(self, services, keys: List[str], scheduler) -> dict:
        key = keys[-1]
        report = services.accounting.integrity("full")
        summary = {"ok": report.ok, "hold": report.hold, "failing": list(report.failing), "warnings": int(getattr(report, "warnings", 0) or 0), "ms": int(report.ms),
                   "holds": sorted(lid for lid, h in (report.holds or {}).items() if h)}
        res = services.bus.execute(Command(self.kind, {"key": key, "summary": summary}, f"job:{self.name}:{key}", ACTOR))
        if not res.ok and not res.replayed:
            raise RuntimeError(f"{self.kind} failed: {res.code} {res.message}")
        note = ("ok" if report.ok else "FAILED: " + ", ".join(report.failing)) + (f", {summary['warnings']} warning(s)" if summary["warnings"] else "") + (" (HOLD)" if report.hold else "")
        return {"job": self.name, "key": key, "status": "DONE", "note": note, "summary": summary, "request_id": f"job:{self.name}:{key}", "ok": True, "http": res.http,
                "replayed": res.replayed}


def h_integrity_full(ctx: TxContext, payload: dict) -> dict:
    """Light: records the INTEGRITY_FULL key with the run's summary (the run itself happened scheduler-side)."""
    from .lifecycle_jobs import write_job_done
    s = payload.get("summary") or {}
    note = ("ok" if s.get("ok") else "FAILED: " + ", ".join(s.get("failing") or [])) + (f", {s.get('warnings')} warning(s)" if s.get("warnings") else "") + (" (HOLD)" if s.get("hold") else "")
    write_job_done(ctx, "INTEGRITY_FULL", str(payload.get("key")), "DONE", note)
    return {"status": "DONE", "key": payload.get("key"), "note": note, "summary": s}


# ---- product-handler jobs ---------------------------------------------------------------------------------------------------------------
class ProductJob(Job):
    """A job whose command is a product handler that writes its own ``jobs_done`` row (module docstring PRODUCT JOBS)."""
    writes_jobs_done = True

    def __init__(self, name: str, kind: str, module_attr: str, keys_fn: str, fields: Tuple[str, ...], when: str, args: Callable, gate: Optional[Callable] = None,
                 kind_of_key: Optional[Callable[[str], str]] = None):
        super().__init__()
        self.name, self.kind, self.module, self.keys_fn, self.fields, self.when = name, kind, module_attr, keys_fn, fields, when
        self._args, self._gate, self._kind_of_key = args, gate, kind_of_key

    def due(self, conn, clock, cal, sessions) -> List[str]:
        mod = getattr(self.services, self.module, None)
        fn = getattr(mod, self.keys_fn, None) if mod is not None else None
        if fn is None:
            self.notes["keys"] = f"skipped: services.{self.module}.{self.keys_fn} is not built"
            return []
        self.notes.pop("keys", None)
        if self._gate is not None and not self._gate(clock, cal, sessions):
            return []
        tuples = fn(*self._args(conn, clock, cal, sessions, self.services))
        return [":".join(str(x) for x in t) for t in tuples]

    def command_kind(self, key: str) -> str:
        return self._kind_of_key(key) if self._kind_of_key is not None else self.kind

    def payload(self, key: str) -> dict:
        parts = key.rsplit(":", len(self.fields) - 1)
        out = dict(zip(self.fields, parts))
        out["key"] = key
        return out

    def series_of(self, key: str) -> Optional[str]:
        return key.rsplit(":", 1)[0] if "date" in self.fields else None

    def retry_payload(self, services, key: str, res) -> Optional[dict]:
        if self.name != "FUTURES_VM" or res is None or not res.ok or (res.body or {}).get("status") != "SKIPPED" or (res.body or {}).get("code") != "SETTLE_PRINT_MISSING":
            return None
        fut = getattr(services, "futures", None)
        if fut is None or not hasattr(fut, "daily_fallback"):
            return None
        p = self.payload(key)
        inst = services.instruments.get(p["instrument_id"])
        if inst is None:
            return None
        daily = fut.daily_fallback(services, inst, date.fromisoformat(p["date"]), services.clock.epoch())
        return dict(p, daily=daily) if daily else None


def _after(hhmm: str, group: Optional[str] = None):
    def gate(clock, cal, sessions) -> bool:
        today = clock.today()
        if group is not None and not sessions.is_trading_day(group, today):
            return False
        return clock.epoch() >= epoch_of(today, hhmm)
    return gate


FUTURES_VM = ProductJob("FUTURES_VM", "futures.vm", "futures", "vm_keys", ("account_id", "instrument_id", "date"), "settle time + 5 min on trading days of the group",
                        lambda conn, clock, cal, sessions, s: (conn, clock.epoch(), sessions, s.instruments))
FUTURES_FINAL = ProductJob("FUTURES_FINAL", "futures.final", "futures", "final_keys", ("account_id", "instrument_id"), "the last trade date, after that day's VM",
                           lambda conn, clock, cal, sessions, s: (conn, clock.epoch(), s.instruments))
OPTION_EXPIRY = ProductJob("OPTION_EXPIRY", "options.expire", "options", "expiry_keys", ("account_id", "instrument_id"), "the expiry date from 16:20 ET (index options 16:35)",
                           lambda conn, clock, cal, sessions, s: (conn, clock.epoch(), sessions))
OPTION_MARGIN_MARK = ProductJob("OPTION_MARGIN_MARK", "options.margin_mark", "options", "margin_mark_keys", ("account_id", "date"), "17:10 ET on trading days",
                                lambda conn, clock, cal, sessions, s: (conn, clock.today().isoformat()), gate=_after(MARGIN_MARK_ET, "US_EQUITY_OPTIONS"))
SWAP_FIX = ProductJob("SWAP_FIX", "swap.fix", "swaps", "fix_keys", ("contract_id", "date"), "each reset date 00:40 ET", lambda conn, clock, cal, sessions, s: (conn, clock.epoch()))
SWAP_PAY = ProductJob("SWAP_PAY", "swap.pay", "swaps", "pay_keys", ("contract_id", "date"), "each payment date 00:45 ET", lambda conn, clock, cal, sessions, s: (conn, clock.epoch()))
SWAP_VM = ProductJob("SWAP_VM", "swap.vm", "swaps", "vm_keys", ("contract_id", "date"), "17:05 ET every calendar day with a curve / underlying mark",
                     lambda conn, clock, cal, sessions, s: (conn, clock.epoch()))
TRS_RESET = ProductJob("TRS_RESET", "trs.reset", "swaps_trs", "reset_keys", ("contract_id", "date"), "each reset date 16:25 ET", lambda conn, clock, cal, sessions, s: (conn, clock.epoch()))
XCCY_EVENTS = ProductJob("XCCY_EVENTS", "xccy.exchange", "swaps_trs", "event_keys", ("contract_id", "date", "event"), "start date, FX-reset dates 17:05 ET, maturity",
                         lambda conn, clock, cal, sessions, s: (conn, clock.epoch()), kind_of_key=lambda key: "xccy.fx_reset" if key.endswith(":FX_RESET") else "xccy.exchange")
DIVIDEND = ProductJob("DIVIDEND", "div.ex", "dividends", "ex_keys", ("account_id", "instrument_id", "ex_date"), "00:40 ET on the ex-date", lambda conn, clock, cal, sessions, s: (conn, clock.epoch()))
DIVPAY = ProductJob("DIVPAY", "div.pay", "dividends", "pay_keys", ("account_id", "instrument_id", "ex_date"), "the pay date (Yahoo) else ex-date + 15 business days",
                    lambda conn, clock, cal, sessions, s: (conn, clock.epoch()))
PC_PAY = ProductJob("PC_PAY", "pc.pay", "private_credit", "pay_keys", ("loan_id", "date"), "next_payment_date 00:40 ET", lambda conn, clock, cal, sessions, s: (conn, clock.epoch()))

REGISTRY: List[Job] = [SettleDue(), AccrueDaily(), PayMonthly(), PbMonthlyMin(), DIVIDEND, DIVPAY, PC_PAY, SWAP_FIX, SWAP_PAY, TRS_RESET, XCCY_EVENTS, OPTION_EXPIRY,
                       FUTURES_VM, FUTURES_FINAL, SWAP_VM, OPTION_MARGIN_MARK, CollateralMark(), BorrowCleanup(), SweepOut(), MarksRecord(), Snapshot(), ExpireOrders(),
                       Housekeeping(), IntegrityFull()]
LIGHT_HANDLERS = {"lifecycle.marks_record": h_marks_record, "lifecycle.integrity_full": h_integrity_full}

__all__ = ["REGISTRY", "LIGHT_HANDLERS", "MarksRecord", "Snapshot", "ExpireOrders", "CollateralMark", "BorrowCleanup", "Housekeeping", "IntegrityFull", "ProductJob",
           "recorded_set", "record_time", "h_marks_record", "h_integrity_full", "BENCHMARK"]
