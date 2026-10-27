"""The Matcher thread (TERMINAL_DESIGN.md §2.12, §10.5): evaluates open orders against cached observations and turns
decisions into ``order.match`` / ``order.match_package`` commands (one command per order per decision, actor
``matcher``; AON packages evaluated as a unit).  It never posts directly and never fetches; ``orders.py`` re-exports it.

Build 2 (2026-10-21): a FOREIGN listing is judged on its venue session from the quote record (C15 ``session_for``); a decision
that would fill against a 1-minute bar first reads what every other fill already took from that bar (EXEC-6, a short read only
for fills); an order whose ``order.match`` keeps failing (500 / a refusal) is backed off per order - 1, 2, 4 ... 60 minutes -
instead of being retried on every pass (TX-3; the bus keeps ONE open COMMAND_FAILED attention per order).

FUNDS_WAIT (hotfix 1.0.3.1, 2026-10-21, ported to 1.0.4): each sweep first plans, read-only, which orders waiting for funds fit the cash
now (``orders.plan_funds_resume``) and issues ONE ``order.resume_funded`` command only when one does; the resumed orders are evaluated in
the same sweep by the normal rules.  A failing resume command backs off like an order (key ``order.resume_funded``).
"""
from __future__ import annotations

import hashlib
import threading
from typing import Dict, Iterable, Optional, Tuple

from .commands import Command
from .orders_preview import session_for, with_nbbo
from .orders_rules import OrderRow, evaluate, evaluate_package, match_idem
from .quotes import QuoteRecord

BACKOFF_S = (60, 120, 240, 480, 960, 1920, 3600)        # an order whose match command failed waits this long (1st, 2nd ... failure in a row)
TRANSIENT_CODES = ("WRITER_BUSY", "RETRY", "BUSY")       # the writer / database was busy: no back-off, the next pass retries


# ---- the Matcher (worker side) ----------------------------------------------------------------------------------------------------
class Matcher(threading.Thread):
    """Evaluates open orders against cached observations and turns decisions into ``order.match`` / ``order.match_package``
    commands (one command per order per decision, actor ``matcher``).  ``on_quotes(ids)`` is called by the quote worker
    after a refresh, ``on_submit(order_id)`` by the API, and the thread sweeps every open order whose session is open
    every ``interval_s`` (15 s).  Reads use a pooled reader; nothing here posts directly.  Memory: one pass's rows only."""

    def __init__(self, db, bus, cache, sessions, instruments, clock, log, services=None, interval_s: int = 15):
        super().__init__(name="matcher", daemon=True)
        self.db, self.bus, self.cache, self.sessions, self.instruments, self.clock, self.log = db, bus, cache, sessions, instruments, clock, log
        self.services = services
        self.interval_s = int(interval_s)
        self._stop_event = threading.Event()
        self._wake = threading.Event()
        self.last_tick_ts: Optional[int] = None
        self.commands = 0
        self.errors = 0
        self.last_error: Optional[str] = None
        self._backoff: Dict[str, Tuple[int, int]] = {}   # order / package id -> (failures in a row, retry at)
        self._lock = threading.Lock()

    # ---- quotes ----------------------------------------------------------------------------------------------------
    def _quote(self, instrument_id: str) -> Optional[QuoteRecord]:
        if self.cache is not None and hasattr(self.cache, "get"):
            rec = self.cache.get(instrument_id)
            if rec is not None:
                return rec
        marks = getattr(self.services, "marks", None) if self.services is not None else None
        if marks is not None and hasattr(marks, "quote"):
            return marks.quote(instrument_id)
        return None

    # ---- entry points -----------------------------------------------------------------------------------------------
    def on_quotes(self, instrument_ids: Iterable[str]) -> int:
        ids = sorted({str(i) for i in instrument_ids})
        if not ids:
            return 0
        q = ",".join("?" for _ in ids)
        with self.db.reader() as conn:
            rows = conn.execute(f"SELECT * FROM orders WHERE status IN ('WORKING','PARTIAL') AND instrument_id IN ({q}) ORDER BY created_ts, order_id", ids).fetchall()
            rows = self._with_package_mates(conn, rows)
        return self._evaluate_rows(rows)

    def on_submit(self, order_id: str) -> int:
        with self.db.reader() as conn:
            rows = conn.execute("SELECT * FROM orders WHERE order_id=? AND status IN ('WORKING','PARTIAL')", (order_id,)).fetchall()
            rows = self._with_package_mates(conn, rows)
        return self._evaluate_rows(rows)

    def sweep(self) -> int:
        resumed = self.resume_funded()
        with self.db.reader() as conn:
            rows = conn.execute("SELECT * FROM orders WHERE status IN ('WORKING','PARTIAL') ORDER BY created_ts, order_id").fetchall()
        self.last_tick_ts = self.clock.epoch()
        return resumed + self._evaluate_rows(rows)

    RESUME_KEY = "order.resume_funded"

    def resume_funded(self) -> int:
        """FUNDS_WAIT: plan on a reader (nothing waiting -> one indexed query); ONE ``order.resume_funded`` command when an
        order waiting for funds fits now (the command plans again inside its transaction).  A planning failure is counted
        and logged; it never stops the sweep.  Returns the commands issued (0 / 1)."""
        svc = self.services
        if svc is None or getattr(svc, "accounting", None) is None or "order.resume_funded" not in self.bus.kinds():
            return 0
        now = self.clock.epoch()
        if self._backed_off(self.RESUME_KEY, now):
            return 0
        try:
            from . import orders as _orders                 # orders imports this module
            from .marks import shared_reader
            with self.db.reader() as conn:
                if conn.execute("SELECT 1 FROM orders WHERE status='SUSPENDED' AND reason LIKE ? LIMIT 1", (_orders.FUNDS_WAIT + "%",)).fetchone() is None:
                    return 0
            marks = getattr(svc, "marks", None)
            snap = marks.snapshot() if marks is not None and hasattr(marks, "snapshot") else marks
            with shared_reader(self.db) as conn:
                plan = _orders.plan_funds_resume(svc, conn, snap, now)
        except Exception as e:   # noqa: BLE001 - the sweep goes on; the failure is counted and visible in health
            self.errors += 1
            self.last_error = f"resume_funded plan: {type(e).__name__}: {e}"
            if self.log:
                self.log.exception("matcher: funds-wait plan failed")
            return 0
        if not plan:
            return 0
        ids = [oid for p in plan for oid in p["order_ids"]]
        rid = f"fundresume:{now}:{hashlib.sha256(','.join(ids).encode('utf-8')).hexdigest()[:10]}"
        return 1 if self._execute(Command("order.resume_funded", {"planned": ids}, rid, "matcher"), self.RESUME_KEY) else 0

    def wake(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            self._wake.wait(self.interval_s)
            self._wake.clear()
            if self._stop_event.is_set():
                break
            try:
                self.sweep()
            except Exception as e:   # noqa: BLE001 - the thread survives; the failure is counted and visible in health
                self.errors += 1
                self.last_error = f"{type(e).__name__}: {e}"
                if self.log:
                    self.log.exception("matcher sweep failed")

    # ---- evaluation -------------------------------------------------------------------------------------------------
    @staticmethod
    def _with_package_mates(conn, rows) -> list:
        pids = sorted({r["package_id"] for r in rows if r["package_id"] and r["fill_mode"] == "AON"})
        if not pids:
            return list(rows)
        seen = {r["order_id"] for r in rows}
        out = list(rows)
        for pid in pids:
            for r in conn.execute("SELECT * FROM orders WHERE package_id=? AND fill_mode='AON' AND status IN ('WORKING','PARTIAL')", (pid,)).fetchall():
                if r["order_id"] not in seen:
                    out.append(r)
                    seen.add(r["order_id"])
        return out

    def _execute(self, cmd: Command, key: Optional[str] = None) -> bool:
        res = self.bus.execute(cmd)
        if res.ok:
            if key is not None:
                with self._lock:
                    self._backoff.pop(key, None)
            if not res.replayed:
                self.commands += 1
                return True
            return False
        self.errors += 1
        self.last_error = f"{cmd.kind} {cmd.request_id}: {res.code} {res.message}"
        if key is not None and res.code not in TRANSIENT_CODES and int(res.http or 0) != 503:
            now = int(self.clock.epoch())
            with self._lock:
                n = self._backoff.get(key, (0, 0))[0] + 1
                self._backoff[key] = (n, now + BACKOFF_S[min(n, len(BACKOFF_S)) - 1])
            if self.log:
                self.log.warning("matcher: %s %s -> %s %s (%d failure(s) in a row; next try in %d s)", cmd.kind, cmd.request_id, res.code, res.message, n,
                                 BACKOFF_S[min(n, len(BACKOFF_S)) - 1])
        elif self.log:
            self.log.warning("matcher: %s %s -> %s %s", cmd.kind, cmd.request_id, res.code, res.message)
        return False

    def _backed_off(self, key: str, now: int) -> bool:
        with self._lock:
            got = self._backoff.get(key)
        return got is not None and now < got[1]

    def backoff(self) -> Dict[str, dict]:
        """Orders (or AON packages) whose match keeps failing: failures in a row and the next try (health / tests)."""
        with self._lock:
            return {k: {"failures": n, "retry_at": t} for k, (n, t) in self._backoff.items()}

    def _bar_used(self, instrument_id: str, bar_t: int) -> int:
        """EXEC-6: what every fill already took from this 1-minute bar (one short read)."""
        with self.db.reader() as conn:
            row = conn.execute("SELECT COALESCE(SUM(qty), 0) FROM fills WHERE instrument_id=? AND bar_ts=?", (instrument_id, int(bar_t))).fetchone()
        return int(row[0] or 0)

    def _evaluate_rows(self, rows) -> int:
        now = self.clock.epoch()
        issued = 0
        packages: Dict[str, list] = {}
        for r in rows:
            if r["package_id"] and r["fill_mode"] == "AON":
                packages.setdefault(r["package_id"], []).append(r)
                continue
            if self._backed_off(r["order_id"], now):
                continue
            inst = self.instruments.get(r["instrument_id"])
            orow = OrderRow.from_row(r, inst)
            rec = self._quote(orow.instrument_id)
            session = session_for(self.sessions, orow.session_group, rec, now)
            if inst is not None and self.services is not None:
                rec = with_nbbo(self.services, inst, rec, session, now)
            dec = evaluate(orow, rec, session, now, orow.participation, inst)
            if dec.action == "FILL" and dec.bar_ts is not None and orow.kind not in ("OPTION", "FX"):
                used = self._bar_used(orow.instrument_id, dec.bar_ts)
                if used:
                    dec = evaluate(orow, rec, session, now, orow.participation, inst, bar_used=used)
            if not dec.wants_command or rec is None:
                continue
            cmd = Command("order.match", {"order_id": orow.order_id, "quote": rec.to_dict(), "hint": dec.action, "reason": dec.reason},
                          match_idem(orow.order_id, rec.quote_time, dec.bar_ts), "matcher")
            if self._execute(cmd, orow.order_id):
                issued += 1
        for pid, legs_raw in packages.items():
            if self._backed_off(pid, now):
                continue
            legs = [OrderRow.from_row(r, self.instruments.get(r["instrument_id"])) for r in legs_raw]
            recs = {l.instrument_id: self._quote(l.instrument_id) for l in legs}
            session = session_for(self.sessions, legs[0].session_group, recs.get(legs[0].instrument_id), now)
            dec = evaluate_package({"package_id": pid}, legs, recs, session, now)
            if dec.action != "FILL":
                continue
            quotes = {iid: rec.to_dict() for iid, rec in recs.items() if rec is not None}
            cmd = Command("order.match_package", {"package_id": pid, "quotes": quotes}, f"matchpkg:{pid}:{dec.quote_time}", "matcher")
            if self._execute(cmd, pid):
                issued += 1
        return issued
