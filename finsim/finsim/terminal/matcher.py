"""The Matcher thread (TERMINAL_DESIGN.md §2.12, §10.5): evaluates open orders against cached observations and turns
decisions into ``order.match`` / ``order.match_package`` commands (one command per order per decision, actor
``matcher``; AON packages evaluated as a unit).  It never posts directly and never fetches; ``orders.py`` re-exports it.
"""
from __future__ import annotations

import threading
from typing import Dict, Iterable, Optional

from .commands import Command
from .orders_preview import with_nbbo
from .orders_rules import OrderRow, evaluate, evaluate_package, match_idem
from .quotes import QuoteRecord


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
        with self.db.reader() as conn:
            rows = conn.execute("SELECT * FROM orders WHERE status IN ('WORKING','PARTIAL') ORDER BY created_ts, order_id").fetchall()
        self.last_tick_ts = self.clock.epoch()
        return self._evaluate_rows(rows)

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

    def _execute(self, cmd: Command) -> bool:
        res = self.bus.execute(cmd)
        if res.ok and not res.replayed:
            self.commands += 1
            return True
        if not res.ok:
            self.errors += 1
            self.last_error = f"{cmd.kind} {cmd.request_id}: {res.code} {res.message}"
            if self.log:
                self.log.warning("matcher: %s %s -> %s %s", cmd.kind, cmd.request_id, res.code, res.message)
        return False

    def _evaluate_rows(self, rows) -> int:
        now = self.clock.epoch()
        issued = 0
        packages: Dict[str, list] = {}
        for r in rows:
            if r["package_id"] and r["fill_mode"] == "AON":
                packages.setdefault(r["package_id"], []).append(r)
                continue
            inst = self.instruments.get(r["instrument_id"])
            orow = OrderRow.from_row(r, inst)
            rec = self._quote(orow.instrument_id)
            session = self.sessions.state(orow.session_group, now)
            if inst is not None and self.services is not None:
                rec = with_nbbo(self.services, inst, rec, session, now)
            dec = evaluate(orow, rec, session, now, orow.participation, inst)
            if not dec.wants_command or rec is None:
                continue
            cmd = Command("order.match", {"order_id": orow.order_id, "quote": rec.to_dict(), "hint": dec.action, "reason": dec.reason},
                          match_idem(orow.order_id, rec.quote_time, dec.bar_ts), "matcher")
            if self._execute(cmd):
                issued += 1
        for pid, legs_raw in packages.items():
            legs = [OrderRow.from_row(r, self.instruments.get(r["instrument_id"])) for r in legs_raw]
            recs = {l.instrument_id: self._quote(l.instrument_id) for l in legs}
            session = self.sessions.state(legs[0].session_group, now)
            dec = evaluate_package({"package_id": pid}, legs, recs, session, now)
            if dec.action != "FILL":
                continue
            quotes = {iid: rec.to_dict() for iid, rec in recs.items() if rec is not None}
            cmd = Command("order.match_package", {"package_id": pid, "quotes": quotes}, f"matchpkg:{pid}:{dec.quote_time}", "matcher")
            if self._execute(cmd):
                issued += 1
        return issued
