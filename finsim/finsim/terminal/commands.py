"""Command, Result, CommandBus, errors, TxContext, handler registry (TERMINAL_DESIGN.md §2.8, §4).

The contract (§4): every change to money or positions is a command executed inside ``BEGIN IMMEDIATE … COMMIT``
under one process-wide writer lock, with a client-supplied ``request_id`` that is unique in the database:
- a repeat with the same id and the same payload returns the stored result (``replayed=True``);
- the same id with a different payload is 409 ``REQUEST_ID_REUSED``;
- a rejection or a failure rolls everything back and leaves NO row, so the id may be retried after the cause is fixed;
- before COMMIT the scoped ledger-level integrity set (L1-L25 on the touched keys) must pass, else the command
  rolls back with 500 and an ERROR attention item;
- ``light`` handlers (quotes persistence, settings, rejections audit …) skip events and the scoped check because
  they touch no ledger table, but still run inside the lock and the transaction.
Handlers never open transactions, never sleep, never do network I/O; the mark snapshot they use is the immutable
``ctx.marks`` taken at the start of the command.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time as _time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from . import VERSION
from .db import Database, canonical_json, dumps, get_meta, set_meta
from .ledger import DuplicatePosting, Entry, LedgerError, post as ledger_post
from .units import qprice

RID_RE = re.compile(r"^[A-Za-z0-9:_.-]{8,80}$")


@dataclass(frozen=True)
class Command:
    kind: str
    payload: dict
    request_id: str
    actor: str = "ui"      # ui | matcher | lifecycle | cli | init | worker


@dataclass
class Result:
    ok: bool
    http: int
    body: dict
    replayed: bool = False
    command_id: Optional[int] = None
    retry_after: Optional[int] = None

    @property
    def code(self) -> Optional[str]:
        err = self.body.get("error") if isinstance(self.body, dict) else None
        return err.get("code") if isinstance(err, dict) else None

    @property
    def message(self) -> Optional[str]:
        err = self.body.get("error") if isinstance(self.body, dict) else None
        return err.get("message") if isinstance(err, dict) else None


class RejectError(Exception):
    """Validation / business rejection -> HTTP 400 (nothing stored)."""
    http = 400

    def __init__(self, code: str, message: str = "", details: Optional[dict] = None):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message or code
        self.details = details or {}


class ConflictError(RejectError):
    """Stale quote, funds, reuse, hold -> HTTP 409."""
    http = 409


class IntegrityFailure(Exception):
    """The scoped ledger-level check failed before COMMIT -> 500, rolled back, Attention."""

    def __init__(self, report):
        super().__init__("integrity failure: " + ", ".join(p.get("id", "?") for p in report.to_dict().get("problems", [])))
        self.report = report


class FxTable:
    """``{ccy: (rate_to_usd: Decimal, source: str, as_of: int)}`` captured lazily from the command's mark snapshot
    (USD -> 1, 'const').  ``rate(ccy)`` raises ``LedgerError FX_RATE_UNAVAILABLE`` when the snapshot has no rate."""

    def __init__(self, marks):
        self._marks = marks
        self._cache: Dict[str, Tuple[Decimal, str, int]] = {"USD": (Decimal(1), "const", 0)}

    def _lookup(self, ccy: str) -> Tuple[Decimal, str, int]:
        if ccy in self._cache:
            return self._cache[ccy]
        view = self._marks.fx(ccy) if self._marks is not None else None
        if view is None or view.mark is None or view.quality in ("unavailable",):
            raise LedgerError("FX_RATE_UNAVAILABLE", f"no USD rate for {ccy} in the mark snapshot")
        tup = (qprice(view.mark), view.source or "", int(view.quote_time or view.fetched_at or 0))
        self._cache[ccy] = tup
        return tup

    def __getitem__(self, ccy: str) -> Tuple[Decimal, str, int]:
        return self._lookup(ccy)

    def __contains__(self, ccy: str) -> bool:
        try:
            self._lookup(ccy)
            return True
        except LedgerError:
            return False

    def rate(self, ccy: str) -> Decimal:
        return self._lookup(ccy)[0]

    def source(self, ccy: str) -> str:
        return self._lookup(ccy)[1]

    def as_of(self, ccy: str) -> int:
        return self._lookup(ccy)[2]

    def get(self, ccy: str, default=None):
        try:
            return self._lookup(ccy)
        except LedgerError:
            return default


@dataclass
class Touched:
    accounts: Set[str] = field(default_factory=set)
    instruments: Set[Tuple[str, str]] = field(default_factory=set)
    entries: List[int] = field(default_factory=list)


class TxContext:
    """What a handler sees: the open transaction, the clock, calendar, sessions, the immutable mark snapshot, the
    FX table, ids, and the touched-key sets the scoped integrity check uses."""

    def __init__(self, tx, conn: sqlite3.Connection, now: datetime, clock, cal, sessions, marks, fx: FxTable,
                 command_id: int, request_id: str, actor: str, kind: str = "", payload: Optional[dict] = None, services=None):
        self.tx = tx
        self.conn = conn
        self.now = now
        self.ts = int(now.timestamp())
        self.clock = clock
        self.cal = cal
        self.sessions = sessions
        self.marks = marks
        self.fx = fx
        self.command_id = command_id
        self.request_id = request_id
        self.actor = actor
        self.kind = kind
        self.payload = payload or {}
        self.services = services
        self.touched_accounts: Set[str] = set()
        self.touched_instruments: Set[Tuple[str, str]] = set()
        self.touched_entries: List[int] = []

    @property
    def today(self) -> str:
        return self.tx.today

    @property
    def touched(self) -> Touched:
        return Touched(set(self.touched_accounts), set(self.touched_instruments), list(self.touched_entries))

    def post(self, entry: Entry) -> int:
        return ledger_post(self, entry)

    def event(self, type: str, account_id=None, instrument_id=None, ref_type=None, ref_id=None, **payload) -> int:
        cur = self.conn.execute(
            "INSERT INTO events(command_id, ts, type, account_id, instrument_id, ref_type, ref_id, payload_json) VALUES (?,?,?,?,?,?,?,?)",
            (self.command_id, self.ts, type, account_id, instrument_id, ref_type, ref_id, dumps(payload)))
        if account_id:
            self.touched_accounts.add(account_id)
        return int(cur.lastrowid)

    def attention(self, severity: str, source: str, code: str, message: str, details: Optional[dict] = None) -> int:
        details = dict(details or {})
        account_id = details.get("account_id")
        ref_type = details.get("ref_type")
        ref_id = details.get("ref_id")
        auto = 1 if details.get("auto_resolve") else 0
        cur = self.conn.execute(
            "INSERT INTO attention(ts, severity, source, code, message, details_json, account_id, ref_type, ref_id, resolved_ts, auto_resolve)"
            " VALUES (?,?,?,?,?,?,?,?,?,NULL,?)",
            (self.ts, severity, source, code, message, dumps(details), account_id, ref_type, ref_id, auto))
        return int(cur.lastrowid)


Handler = Callable[[TxContext, dict], dict]


def _error_body(code: str, message: str, details: Optional[dict] = None, request_id: Optional[str] = None, **extra) -> dict:
    body = {"error": {"code": code, "message": message, "details": details or {}}, "request_id": request_id}
    body.update(extra)
    return body


class CommandBus:
    def __init__(self, db: Database, clock, cal, sessions, marks_provider, log, services=None):
        self.db = db
        self.clock = clock
        self.cal = cal
        self.sessions = sessions
        self.marks_provider = marks_provider
        self.log = log
        self.services = services
        self._handlers: Dict[str, Tuple[Handler, bool]] = {}
        self._listeners: List[Callable[[Command, Result], None]] = []
        self.lock_timeout = 10.0

    # ---- registry ---------------------------------------------------------------------------------------------------
    def register(self, kind: str, handler: Handler, light: bool = False) -> None:
        self._handlers[kind] = (handler, bool(light))

    def kinds(self) -> List[str]:
        return sorted(self._handlers)

    def is_light(self, kind: str) -> bool:
        return self._handlers.get(kind, (None, False))[1]

    def on_commit(self, listener: Callable[[Command, Result], None]) -> None:
        self._listeners.append(listener)

    # ---- helpers ----------------------------------------------------------------------------------------------------
    def _snapshot(self):
        if self.marks_provider is None:
            from .quotes import FixedMarks
            return FixedMarks({}, as_of=self.clock.epoch())
        return self.marks_provider.snapshot()

    def _attention_after_rollback(self, severity: str, code: str, message: str, details: dict) -> Optional[int]:
        conn = self.db.writer_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "INSERT INTO attention(ts, severity, source, code, message, details_json, account_id, ref_type, ref_id, resolved_ts, auto_resolve)"
                " VALUES (?,?,?,?,?,?,?,?,?,NULL,0)",
                (self.clock.epoch(), severity, "bus", code, message, dumps(details), details.get("account_id"), None, None))
            conn.execute("COMMIT")
            return int(cur.lastrowid)
        except sqlite3.Error as e:   # the attention row itself failed: log loudly, never raise over the original error
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            if self.log:
                self.log.error("could not record attention %s: %s", code, e)
            return None

    # ---- execute ----------------------------------------------------------------------------------------------------
    def execute(self, cmd: Command) -> Result:
        rid = cmd.request_id or ""
        if not RID_RE.match(rid):
            return Result(False, 400, _error_body("BAD_REQUEST_ID", "request_id must match ^[A-Za-z0-9:_.-]{8,80}$", request_id=rid))
        entry = self._handlers.get(cmd.kind)
        if entry is None:
            return Result(False, 400, _error_body("UNKNOWN_COMMAND", f"no handler for {cmd.kind}", request_id=rid))
        handler, light = entry
        if not self.db.acquire_writer(self.lock_timeout):
            return Result(False, 503, _error_body("WRITER_BUSY", "the writer is busy; retry", request_id=rid), retry_after=1)
        committed: Optional[Result] = None
        try:
            conn = self.db.writer_conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as e:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                return Result(False, 503, _error_body("RETRY", f"database busy: {e}", request_id=rid), retry_after=1)
            t0 = _time.monotonic()
            try:
                payload_json = canonical_json(cmd.payload)
                sha = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
                row = conn.execute("SELECT id, kind, payload_sha256, result_json, http_status FROM commands WHERE request_id=?", (rid,)).fetchone()
                if row is not None:
                    conn.execute("ROLLBACK")
                    if row["payload_sha256"] == sha and row["kind"] == cmd.kind:
                        body = json.loads(row["result_json"] or "{}")
                        return Result(int(row["http_status"]) < 400, int(row["http_status"]), body, replayed=True, command_id=int(row["id"]))
                    return Result(False, 409, _error_body("REQUEST_ID_REUSED", "request_id already used with a different payload",
                                                          {"stored_kind": row["kind"]}, request_id=rid))
                now = self.clock.now()
                ts = int(now.timestamp())
                cur = conn.execute(
                    "INSERT INTO commands(request_id, kind, actor, payload_json, payload_sha256, result_json, http_status, started_ts, finished_ts,"
                    " duration_ms, app_version, state_version) VALUES (?,?,?,?,?,'{}',200,?,?,0,?,0)",
                    (rid, cmd.kind, cmd.actor, payload_json, sha, ts, ts, VERSION))
                command_id = int(cur.lastrowid)
                from .db import Tx
                tx = Tx(conn, now)
                marks = self._snapshot()
                ctx = TxContext(tx, conn, now, self.clock, self.cal, self.sessions, marks, FxTable(marks), command_id, rid, cmd.actor,
                                cmd.kind, dict(cmd.payload), self.services)
                body = handler(ctx, dict(cmd.payload))
                if body is None:
                    body = {}
                # the result is stored before the scoped check so the checks that read commands (L6, L20) see the final shape
                conn.execute("UPDATE commands SET result_json=? WHERE id=?", (dumps(body), command_id))
                if not light:
                    from . import accounting as _acc
                    report = _acc.integrity_scoped(ctx)
                    if not report.ok:
                        raise IntegrityFailure(report)
                sv = int(get_meta(conn, "state_version", "0") or 0) + 1
                set_meta(conn, "state_version", str(sv))
                finished = int(self.clock.now().timestamp())
                conn.execute("UPDATE commands SET http_status=200, finished_ts=?, duration_ms=?, state_version=? WHERE id=?",
                             (finished, int((_time.monotonic() - t0) * 1000), sv, command_id))
                conn.execute("COMMIT")
                committed = Result(True, 200, body, command_id=command_id)
            except RejectError as e:
                self._rollback(conn)
                if self.log:
                    self.log.info("command %s %s rejected: %s %s", cmd.kind, rid, e.code, e.message)
                return Result(False, e.http, _error_body(e.code, e.message, e.details, request_id=rid))
            except DuplicatePosting as e:
                self._rollback(conn)
                incident = self._attention_after_rollback("WARN", "DUPLICATE_POSTING", str(e), {"request_id": rid, "kind": cmd.kind, "idem_key": e.idem_key})
                return Result(False, 409, _error_body("DUPLICATE_POSTING", e.message, {"idem_key": e.idem_key, "incident_id": incident}, request_id=rid))
            except sqlite3.IntegrityError as e:
                self._rollback(conn)
                incident = self._attention_after_rollback("WARN", "DUPLICATE_POSTING", f"{cmd.kind}: {e}", {"request_id": rid, "kind": cmd.kind})
                return Result(False, 409, _error_body("DUPLICATE_POSTING", str(e), {"incident_id": incident}, request_id=rid))
            except IntegrityFailure as e:
                self._rollback(conn)
                problems = e.report.to_dict().get("problems", [])
                if self.log:
                    self.log.error("command %s %s failed integrity: %s", cmd.kind, rid, problems)
                incident = self._attention_after_rollback("ERROR", "INTEGRITY", f"{cmd.kind} rolled back: integrity check failed",
                                                          {"request_id": rid, "kind": cmd.kind, "problems": problems})
                return Result(False, 500, _error_body("INTEGRITY", "command rolled back: the ledger-level integrity check failed",
                                                      {"problems": problems, "incident_id": incident}, request_id=rid))
            except Exception as e:   # noqa: BLE001 — every failure is loud: rolled back, logged, surfaced
                self._rollback(conn)
                tb = traceback.format_exc()
                if self.log:
                    self.log.exception("command %s %s failed", cmd.kind, rid)
                code = getattr(e, "code", None) if isinstance(e, LedgerError) else None
                incident = self._attention_after_rollback("ERROR", "COMMAND_FAILED", f"{cmd.kind}: {e}",
                                                          {"request_id": rid, "kind": cmd.kind, "error": str(e), "code": code, "traceback": tb[-2000:]})
                return Result(False, 500, _error_body(code or "COMMAND_FAILED", str(e), {"incident_id": incident}, request_id=rid))
        finally:
            self.db.release_writer()
        for listener in list(self._listeners):
            try:
                listener(cmd, committed)
            except Exception:   # noqa: BLE001 — a listener never breaks a committed command; it is logged
                if self.log:
                    self.log.exception("on_commit listener failed")
        return committed

    @staticmethod
    def _rollback(conn: sqlite3.Connection) -> None:
        try:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass


def require_amount_c(payload: dict, key_c: str = "amount_c", key: str = "amount") -> int:
    """Read a positive cents amount from ``payload[key_c]`` (int) or ``payload[key]`` (decimal string); 400 AMOUNT otherwise."""
    from .units import to_c
    v = payload.get(key_c)
    if v is None and payload.get(key) is not None:
        try:
            v = to_c(str(payload.get(key)))
        except (ValueError, TypeError) as e:
            raise RejectError("AMOUNT", f"amount is not a number: {payload.get(key)!r}") from e
    if v is None:
        raise RejectError("AMOUNT", "amount is required (cents in amount_c or a decimal string in amount)")
    if isinstance(v, bool) or not isinstance(v, int):
        try:
            v = int(Decimal(str(v)))
        except Exception as e:   # noqa: BLE001
            raise RejectError("AMOUNT", f"amount_c must be an integer number of cents, got {v!r}") from e
    if v <= 0:
        raise RejectError("AMOUNT", "amount must be positive")
    return v
