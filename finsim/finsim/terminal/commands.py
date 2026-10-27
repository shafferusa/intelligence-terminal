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

Build 2 (2026-10-21):
- TX-2: a command that rolls back takes its instrument rows with it - rows it inserted (rowid above the start's) and the
  instruments it touched are evicted from the instrument master's memory (``InstrumentMaster.invalidate``), so no process
  keeps serving or trading a row the database does not have and a retry of the same request id works.  A
  ``sqlite3.IntegrityError`` is DUPLICATE_POSTING (409) only for a UNIQUE / PRIMARY KEY violation (a repeated idem key); a
  FOREIGN KEY / CHECK / NOT NULL failure is a 500 with its own code (DB_FOREIGN_KEY / DB_CHECK / DB_NOT_NULL / DB_CONSTRAINT).
- TX-3: the ERROR attention of a failed command (COMMAND_FAILED and the DB_* codes) is ONE open row per (kind, reference) - the
  order / package / contract / account the payload names - updated with the latest error and a count, never one per attempt.
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
#: the payload keys that name what a command is about (the reference its failure attention is de-duplicated on, TX-3)
REF_KEYS = (("order_id", "order"), ("package_id", "package"), ("contract_id", "contract"), ("loan_id", "loan"), ("borrow_id", "borrow"),
            ("repo_id", "repo"), ("account_id", "account"), ("instrument_id", "instrument"), ("job", "job"))


def command_ref(payload: Optional[dict]) -> Tuple[Optional[str], Optional[str]]:
    """``(ref_type, ref_id)`` of a command from its payload (the first of REF_KEYS present), else (None, None)."""
    if isinstance(payload, dict):
        for key, kind in REF_KEYS:
            v = payload.get(key)
            if isinstance(v, (str, int)) and str(v):
                return kind, str(v)[:120]
    return None, None


def integrity_code(e: sqlite3.IntegrityError) -> Optional[str]:
    """None for a UNIQUE / PRIMARY KEY violation (a duplicate posting); else the 500 code of the constraint that failed."""
    msg = str(e).upper()
    if "UNIQUE" in msg or "PRIMARY KEY" in msg:
        return None
    if "FOREIGN KEY" in msg:
        return "DB_FOREIGN_KEY"
    if "CHECK" in msg:
        return "DB_CHECK"
    if "NOT NULL" in msg:
        return "DB_NOT_NULL"
    return "DB_CONSTRAINT"


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

    def _attention_after_rollback(self, severity: str, code: str, message: str, details: dict, dedupe: bool = False,
                                  ref: Tuple[Optional[str], Optional[str]] = (None, None)) -> Optional[int]:
        """The attention row of a rolled-back command, in its own short transaction.  ``dedupe`` (TX-3): an open row with the same
        code, command kind and reference is updated (latest error, ``count`` + 1, time) instead of a new row per attempt."""
        conn = self.db.writer_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            ref_type, ref_id = ref
            if dedupe:
                row = conn.execute("SELECT id, details_json FROM attention WHERE source='bus' AND code=? AND resolved_ts IS NULL AND ref_type IS ? AND ref_id IS ?"
                                   " AND details_json LIKE ? ORDER BY id DESC LIMIT 1", (code, ref_type, ref_id, f'%"kind":{dumps(details.get("kind"))}%')).fetchone()
                if row is not None:
                    try:
                        prev = json.loads(row["details_json"] or "{}")
                    except ValueError:
                        prev = {}
                    merged = dict(details, count=int(prev.get("count") or 1) + 1, first_ts=prev.get("first_ts") or prev.get("ts"))
                    conn.execute("UPDATE attention SET ts=?, message=?, details_json=? WHERE id=?", (self.clock.epoch(), message, dumps(merged), int(row["id"])))
                    conn.execute("COMMIT")
                    return int(row["id"])
                details = dict(details, count=1, first_ts=self.clock.epoch())
            cur = conn.execute(
                "INSERT INTO attention(ts, severity, source, code, message, details_json, account_id, ref_type, ref_id, resolved_ts, auto_resolve)"
                " VALUES (?,?,?,?,?,?,?,?,?,NULL,0)",
                (self.clock.epoch(), severity, "bus", code, message, dumps(details), details.get("account_id"), ref_type, ref_id))
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
            ctx: Optional[TxContext] = None
            inst_rowid0 = self._instrument_rowid(conn)
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
                self._rollback_and_evict(conn, ctx, cmd, inst_rowid0)
                if self.log:
                    self.log.info("command %s %s rejected: %s %s", cmd.kind, rid, e.code, e.message)
                return Result(False, e.http, _error_body(e.code, e.message, e.details, request_id=rid))
            except DuplicatePosting as e:
                self._rollback_and_evict(conn, ctx, cmd, inst_rowid0)
                incident = self._attention_after_rollback("WARN", "DUPLICATE_POSTING", str(e), {"request_id": rid, "kind": cmd.kind, "idem_key": e.idem_key})
                return Result(False, 409, _error_body("DUPLICATE_POSTING", e.message, {"idem_key": e.idem_key, "incident_id": incident}, request_id=rid))
            except sqlite3.IntegrityError as e:
                self._rollback_and_evict(conn, ctx, cmd, inst_rowid0)
                icode = integrity_code(e)
                if icode is None:                    # a UNIQUE / PRIMARY KEY violation: a repeated idem key
                    incident = self._attention_after_rollback("WARN", "DUPLICATE_POSTING", f"{cmd.kind}: {e}", {"request_id": rid, "kind": cmd.kind})
                    return Result(False, 409, _error_body("DUPLICATE_POSTING", str(e), {"incident_id": incident}, request_id=rid))
                if self.log:
                    self.log.error("command %s %s failed on a database constraint: %s", cmd.kind, rid, e)
                incident = self._attention_after_rollback("ERROR", "COMMAND_FAILED", f"{cmd.kind}: the database refused a row ({e}); nothing was written",
                                                          {"request_id": rid, "kind": cmd.kind, "error": str(e), "code": icode}, dedupe=True,
                                                          ref=command_ref(cmd.payload))
                return Result(False, 500, _error_body(icode, f"{cmd.kind}: the database refused a row ({e}); nothing was written",
                                                      {"incident_id": incident}, request_id=rid))
            except IntegrityFailure as e:
                self._rollback_and_evict(conn, ctx, cmd, inst_rowid0)
                problems = e.report.to_dict().get("problems", [])
                if self.log:
                    self.log.error("command %s %s failed integrity: %s", cmd.kind, rid, problems)
                incident = self._attention_after_rollback("ERROR", "INTEGRITY", f"{cmd.kind} rolled back: integrity check failed",
                                                          {"request_id": rid, "kind": cmd.kind, "problems": problems})
                return Result(False, 500, _error_body("INTEGRITY", "command rolled back: the ledger-level integrity check failed",
                                                      {"problems": problems, "incident_id": incident}, request_id=rid))
            except Exception as e:   # noqa: BLE001 — every failure is loud: rolled back, logged, surfaced
                self._rollback_and_evict(conn, ctx, cmd, inst_rowid0)
                tb = traceback.format_exc()
                if self.log:
                    self.log.exception("command %s %s failed", cmd.kind, rid)
                code = getattr(e, "code", None) if isinstance(e, LedgerError) else None
                incident = self._attention_after_rollback("ERROR", "COMMAND_FAILED", f"{cmd.kind}: {e}",
                                                          {"request_id": rid, "kind": cmd.kind, "error": str(e), "code": code, "traceback": tb[-2000:]},
                                                          dedupe=True, ref=command_ref(cmd.payload))
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

    @staticmethod
    def _instrument_rowid(conn: sqlite3.Connection) -> Optional[int]:
        try:
            return int(conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM instruments").fetchone()[0] or 0)
        except sqlite3.Error:
            return None

    def _rollback_and_evict(self, conn: sqlite3.Connection, ctx: Optional[TxContext], cmd: Command, inst_rowid0: Optional[int]) -> List[str]:
        """TX-2: roll back, then evict from the instrument master's memory every instrument the command inserted (rowid above the
        start's, read before the rollback) or touched, so the cache never outlives the rows.  Returns the evicted ids."""
        ids: Set[str] = set()
        if inst_rowid0 is not None:
            try:
                if conn.in_transaction:
                    ids.update(r[0] for r in conn.execute("SELECT instrument_id FROM instruments WHERE rowid > ?", (inst_rowid0,)).fetchall())
            except sqlite3.Error:
                pass
        if ctx is not None:
            ids.update(i for _a, i in ctx.touched_instruments if i)
        if isinstance(cmd.payload, dict) and isinstance(cmd.payload.get("instrument_id"), str):
            ids.add(cmd.payload["instrument_id"])
        self._rollback(conn)
        if ids:
            self._evict_instruments(sorted(ids), conn)
        return sorted(ids)

    def _evict_instruments(self, ids: List[str], conn: Optional[sqlite3.Connection] = None) -> None:
        """``InstrumentMaster.invalidate(ids)`` when the master has it; else the committed rows are read again on the writer
        connection (outside any transaction after the rollback) and replace the cached ones - a row the database does not have
        leaves the cache, a committed row stays listed (``all()`` keeps every instrument)."""
        master = getattr(self.services, "instruments", None) if self.services is not None else None
        if master is None:
            return
        fn = getattr(master, "invalidate", None)
        try:
            if callable(fn):
                fn(ids)
                return
            cache = getattr(master, "_cache", None)
            if not isinstance(cache, dict) or conn is None:
                return
            from .instruments import row_to_instrument
            for i in ids:
                row = conn.execute("SELECT * FROM instruments WHERE instrument_id=?", (i,)).fetchone()
                if row is None:
                    cache.pop(i, None)
                else:
                    cache[i] = row_to_instrument(row)
        except Exception:   # noqa: BLE001 — an eviction failure is logged, never raised over the original error
            if self.log:
                self.log.exception("instrument cache eviction after a rollback failed")


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
