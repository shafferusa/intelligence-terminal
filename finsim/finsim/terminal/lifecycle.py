"""Lifecycle scheduler: the 30-second tick, catch-up by state, failure backoff, the ``lifecycle.*`` bus handlers and
``run_now`` (TERMINAL_DESIGN.md §2.12 lifecycle.py, §4, §11.1, §16, §19.11, §19.12; TERMINAL_WORKPLAN.md WP-08).
The jobs themselves live in ``lifecycle_jobs.py`` (protocol, ``accrue_segment``, money jobs) and ``lifecycle_eod.py``
(end-of-day, product and housekeeping jobs, ``REGISTRY``); both are re-exported here.

Conventions stated here
- TICK (§11.1): every ``interval_s`` (30 s; the first tick at start is the catch-up) the scheduler asks each job of
  ``REGISTRY``, in the amended order, for the keys that should run now and are not done, and executes each key as
  ONE bus command (``Command(kind, payload, request_id="job:{name}:{key}", actor="lifecycle")``) — the product
  handler's own kind, or the job's ``lifecycle.<x>`` handler, which runs ``job.run(ctx, key, services)`` inside a
  SAVEPOINT and writes the ``jobs_done`` row in the same transaction.  Phase-1 jobs are iterated in passes until a
  pass completes nothing new (at most ``MAX_PASSES``), so one tick resolves the dependencies a catch-up creates;
  SNAPSHOT (phase 2) and the closing jobs (phase 3) follow once.  A key runs at most once per pass; a key that
  completed DONE or failed never re-runs within the tick; a SKIPPED key may re-run in a later pass of the same tick
  when something else progressed, and is otherwise deferred ``SKIP_RETRY_S`` (5 min) across ticks.
- IDS: ``job:{name}:{key}`` for the first attempt; a key that comes back as due after a committed command with that
  id (a SKIPPED outcome, or a product no-op) retries as ``job:{name}:{key}:r{n}`` (n = the number of earlier attempts
  stored in ``commands``), because the bus replays an id with its stored result.  A key longer than the id limit is
  hashed.  A rejected or failed command stores nothing, so its id is simply reused.
- FAILURES (§4, §11.1): a key whose command returns an error (4xx / 5xx; a 503 "busy" is retried next tick) is
  recorded in ``job_failures`` (count, last error, ``parked_until_ts``) and as ONE attention item per (job, key)
  (``JOB_FAILED``, WARN; ERROR after ``ERROR_AFTER`` = 5 failures), retried with backoff 1 / 2 / 4 / 8 / 16 min then
  hourly; a later success deletes the row and resolves the item.  A pending outcome a handler reports as SKIPPED
  (missing mark / rate, unfunded settlement) is NOT a failure: it is a ``jobs_done`` SKIPPED row with a note, surfaced
  as a WARN attention (unless the handler surfaced its own ERROR, or the outcome is a quiet terminal no-op such as
  SWEEP_OUT "nothing idle"), and retried.  Nothing is ever posted in place of missing data, and no failure is swallowed:
  every outcome is in ``jobs_done`` / ``job_failures`` / ``attention`` and in ``status()`` (``/api/jobs``).
  TX-4: a failing ``due()`` or ``before()`` is recorded under the pseudo-keys ``(job, 'due')`` / ``(job, 'before')`` and
  a failing batch under its first key; the job is skipped while that row is parked (the backoff holds for the pseudo-keys
  too), and the next success deletes the row(s) and resolves the JOB_FAILED item - a batch success clears every recorded
  failure of its job.
- BUSY (TX-5): a 503 outcome (the database write lock held elsewhere) is RETRIED next tick - never a failure row - but
  it is counted (``errors`` / ``last_error``, so health ``workers.lifecycle.errors_1h`` is real), logged once per
  attempt, and after ``BLOCKED_AFTER`` consecutive RETRYs of one key ONE WARN attention ``LIFECYCLE_BLOCKED`` names the
  busy cause (written as soon as the database takes a write; kept in ``status()['blocked']`` until then); the key's next
  success resolves it.  FAILED outcomes count too.
- SERIES: keys of one object run in date order; when a key skips or fails, the later keys of its series wait (an
  accrual night never jumps over a pending night; a futures settle never jumps over a missing print).
- MODULES: a job whose product module is not bound as ``services.<name>`` (or whose command is not registered) is
  skipped with a note in ``status()`` — never an error, never a fake posting.  The scheduler object exposes
  ``accrue_segment`` so ``services.lifecycle.accrue_segment`` serves the credit / swap / private-credit accruals.
- THREADS (§16): ``LifecycleScheduler`` is a daemon thread; ``tick()`` is serialised by a lock (``run_now`` from the
  API and the thread never overlap); handlers never do network I/O — the one network step (daily bars for marks
  recorded late) happens scheduler-side, outside any transaction, before the light ``lifecycle.marks_record`` command.
  Tests call ``tick(at=...)`` with a ``FakeClock``; nothing here sleeps inside a transaction.
"""
from __future__ import annotations

import copy
import hashlib
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

from .clock import ny_of
from .commands import Command, RejectError, TxContext
from .db import dumps
from .lifecycle_eod import LIGHT_HANDLERS, REGISTRY
from .lifecycle_jobs import ACTOR, SOURCE, Job, accrue_segment, job_row, write_job_done

TICK_S = 30
MAX_PASSES = 8
SKIP_RETRY_S = 300
BACKOFF_S = (60, 120, 240, 480, 960)
HOURLY_S = 3600
ERROR_AFTER = 5
RID_MAX = 80
BLOCKED_AFTER = 3            # TX-5: consecutive 503 RETRYs of one key before the WARN LIFECYCLE_BLOCKED


# ---- the bus handlers -------------------------------------------------------------------------------------------------------------
def run_job(job: Job, ctx: TxContext, payload: dict) -> dict:
    """The ``lifecycle.<x>`` handler body: one key of ``job`` inside a SAVEPOINT; a RejectError from the work (a missing
    mark or rate, an account that is not open …) rolls that key's postings back and records a SKIPPED row with a WARN
    attention; a DONE / SKIPPED result is written to ``jobs_done`` in the same transaction (module docstring FAILURES)."""
    key = str(payload.get("key") or "")
    if not key:
        raise RejectError("KEY_REQUIRED", f"{job.kind} needs a key")
    conn = ctx.conn
    existing = job_row(conn, job.name, key)
    if existing is not None and existing["status"] == "DONE":
        return {"job": job.name, "key": key, "status": "DONE", "note": "already done: " + (existing["note"] or ""), "posted": False}
    saved = (set(ctx.touched_accounts), set(ctx.touched_instruments), list(ctx.touched_entries))
    conn.execute("SAVEPOINT lifecycle_job")
    try:
        body = dict(job.run(ctx, key, ctx.services) or {})
        conn.execute("RELEASE SAVEPOINT lifecycle_job")
    except RejectError as e:
        conn.execute("ROLLBACK TO SAVEPOINT lifecycle_job")
        conn.execute("RELEASE SAVEPOINT lifecycle_job")
        ctx.touched_accounts, ctx.touched_instruments, ctx.touched_entries = saved
        body = {"status": "SKIPPED", "code": e.code, "message": e.message, "details": e.details, "note": f"{e.code}: {e.message}", "posted": False}
    status = "SKIPPED" if str(body.get("status") or "DONE").upper() == "SKIPPED" else "DONE"
    note = str(body.get("note") or (f"{body.get('code')}: {body.get('message')}" if body.get("code") else ""))
    write_job_done(ctx, job.name, key, status, note)
    ref = f"{job.name}:{key}"
    if status == "SKIPPED":
        if not body.get("quiet") and not body.get("surfaced"):
            code = str(body.get("code") or "JOB_SKIPPED")
            message = f"{job.name} {key}: {note}; retried"
            open_row = conn.execute("SELECT id FROM attention WHERE source=? AND code=? AND ref_type='job' AND ref_id=? AND resolved_ts IS NULL", (SOURCE, code, ref)).fetchone()
            if open_row is None:
                details = {k: v for k, v in (body.get("details") or {}).items() if k not in ("ref_type", "ref_id")}
                details.update(job=job.name, key=key, ref_type="job", ref_id=ref, auto_resolve=1)
                ctx.attention("WARN", SOURCE, code, message, details)
            else:
                conn.execute("UPDATE attention SET ts=?, message=? WHERE id=?", (ctx.ts, message, int(open_row["id"])))
        ctx.event("JOB_SKIPPED", ref_type="job", ref_id=ref, job=job.name, key=key, note=note, code=body.get("code"))
    else:
        conn.execute("UPDATE attention SET resolved_ts=? WHERE source=? AND ref_type='job' AND ref_id=? AND resolved_ts IS NULL", (ctx.ts, SOURCE, ref))
    body.update(job=job.name, key=key, status=status, note=note)
    return body


def _handler_for(job: Job):
    def handler(ctx: TxContext, payload: dict) -> dict:
        return run_job(job, ctx, payload)
    handler.__name__ = f"h_{job.name.lower()}"
    return handler


def register(bus, services=None) -> None:
    """Register every ``lifecycle.*`` handler (the product kinds are registered by their own modules)."""
    for job in REGISTRY:
        if job.kind.startswith("lifecycle.") and not job.batch:
            bus.register(job.kind, _handler_for(job), light=job.light)
    for kind, fn in LIGHT_HANDLERS.items():
        bus.register(kind, fn, light=True)


# ---- the scheduler ------------------------------------------------------------------------------------------------------------------
class _TickState:
    def __init__(self, now: int):
        self.now = now
        self.ran: List[dict] = []
        self.done: Set[Tuple[str, str]] = set()
        self.last_status: Dict[Tuple[str, str], str] = {}
        self.skipped_modules: Dict[str, str] = {}
        self.before_done: Set[str] = set()


class LifecycleScheduler(threading.Thread):
    """Module docstring TICK / FAILURES / SERIES / MODULES.  ``tick(at=None, only=None)`` runs one tick now (``at`` moves a
    FakeClock first; ``only`` restricts to one job name); ``catch_up()`` is ``tick()``; ``status()`` is the ``/api/jobs``
    view; ``defer(job, key, seconds)`` parks a key in memory (batch jobs use it for unresolved marks)."""
    accrue_segment = staticmethod(accrue_segment)

    def __init__(self, bus, db, services, clock, log, interval_s: int = TICK_S):
        super().__init__(name="lifecycle", daemon=True)
        self.bus, self.db, self.services, self.clock, self.log = bus, db, services, clock, log
        self.interval_s = int(interval_s)
        self.cal = getattr(services, "cal", None)
        self.sessions = getattr(services, "sessions", None)
        self.jobs: List[Job] = []
        for j in REGISTRY:
            jj = copy.copy(j)
            jj.services = services
            jj.scheduler = self
            jj.notes = dict(j.notes)
            self.jobs.append(jj)
        self._stop_event = threading.Event()
        self._tick_lock = threading.Lock()
        self._deferred: Dict[Tuple[str, str], int] = {}
        self._status: Dict[str, dict] = {}
        self.ticks = 0
        self.last_tick_ts: Optional[int] = None
        self.last_error: Optional[str] = None
        self.last_result: Optional[dict] = None
        self.errors = 0                                   # TX-5: RETRY / FAILED outcomes and job faults (health errors_1h samples it)
        self.retries = 0
        self._busy: Dict[Tuple[str, str], dict] = {}      # (job, key) -> {n, since, cause, attention}

    # ---- thread ------------------------------------------------------------------------------------------------------------------
    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.tick()
            except Exception as e:   # noqa: BLE001 — the thread never dies silently: logged, kept in status, retried next tick
                self.errors += 1
                self.last_error = f"{type(e).__name__}: {e}"
                if self.log is not None:
                    self.log.exception("lifecycle tick failed")
            self._stop_event.wait(self.interval_s)

    def stop(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout)

    def catch_up(self) -> dict:
        return self.tick()

    def defer(self, job: str, key: str, seconds: int) -> None:
        self._deferred[(job, key)] = int(self.clock.epoch()) + int(seconds)

    def job(self, name: str) -> Optional[Job]:
        return next((j for j in self.jobs if j.name == name), None)

    # ---- one tick ------------------------------------------------------------------------------------------------------------------
    def tick(self, at=None, only: Optional[str] = None) -> dict:
        with self._tick_lock:
            if at is not None:
                if not hasattr(self.clock, "set"):
                    raise ValueError("tick(at=...) needs a FakeClock")
                self.clock.set(at)
            now = int(self.clock.epoch())
            for k in [k for k, ts in self._deferred.items() if ts <= now]:
                self._deferred.pop(k, None)
            parked, failing = self._load_failures(now)
            state = _TickState(now)
            jobs = [j for j in self.jobs if only is None or j.name == only]
            if only is not None and not jobs:
                raise ValueError(f"unknown job {only!r}")
            passes = 0
            for phase in (1, 2, 3):
                phase_jobs = [j for j in jobs if j.phase == phase]
                if not phase_jobs:
                    continue
                for _ in range(MAX_PASSES if phase == 1 else 1):
                    passes += 1
                    if self._pass(phase_jobs, state, parked, failing) == 0:
                        break
            for (jname, key), status in state.last_status.items():
                if status == "SKIPPED":
                    self._deferred[(jname, key)] = now + SKIP_RETRY_S
            self.ticks += 1
            self.last_tick_ts = now
            counts: Dict[str, int] = {}
            for item in state.ran:
                counts[item["status"]] = counts.get(item["status"], 0) + 1
            result = {"at": now, "ran": state.ran, "passes": passes, "counts": counts, "skipped_modules": dict(state.skipped_modules)}
            self.last_result = {"at": now, "passes": passes, "counts": counts, "n": len(state.ran)}
            return result

    def _pass(self, jobs: List[Job], state: _TickState, parked: Dict[Tuple[str, str], int], failing: Set[Tuple[str, str]]) -> int:
        progress = 0
        for job in jobs:
            ok, note = job.available(self.services)
            if not ok:
                state.skipped_modules[job.name] = note
                self._status.setdefault(job.name, {}).update(note=note)
                continue
            # TX-4: a due() / before() fault parks the whole job for its backoff (the pseudo-keys honour parked_until)
            if self._parked(parked, job.name, "due", state.now) or self._parked(parked, job.name, "before", state.now):
                continue
            keys = self._due(job, state, failing)
            if keys and job.name not in state.before_done:
                state.before_done.add(job.name)
                try:
                    if job.before(self.services, keys):
                        keys = self._due(job, state, failing)
                except Exception as e:   # noqa: BLE001 — surfaced like a failing key
                    self._record_failure(job.name, "before", f"{type(e).__name__}: {e}", state.now)
                    failing.add((job.name, "before"))
                    parked[(job.name, "before")] = state.now + 1           # parked for the rest of this tick; the stored backoff holds after it
                    continue
                if (job.name, "before") in failing:
                    self._clear_failure(job.name, "before", state.now)
                    failing.discard((job.name, "before"))
            if not keys:
                continue
            if job.batch:
                keys = [k for k in keys if (job.name, k) not in state.done and not self._parked(parked, job.name, k, state.now)]
                if not keys:
                    continue
                item = self._run_batch(job, keys, state)
                if item.get("status") == "DONE":
                    progress += 1
                    stale = [k for (jn, k) in failing if jn == job.name and k not in ("due", "before")]
                    for k in stale:                                         # TX-4: a batch success clears its recorded failures
                        self._clear_failure(job.name, k, state.now)
                        failing.discard((job.name, k))
                continue
            stalled: Set[str] = set()
            for key in keys:
                if (job.name, key) in state.done:
                    continue
                series = job.series_of(key)
                if series is not None and series in stalled:
                    continue
                if self._parked(parked, job.name, key, state.now):
                    if series is not None:
                        stalled.add(series)
                    continue
                item = self._run_key(job, key, state, failing)
                status = item["status"]
                state.last_status[(job.name, key)] = status
                if status == "DONE":
                    progress += 1
                    state.done.add((job.name, key))
                else:
                    if status == "FAILED":
                        state.done.add((job.name, key))
                    if series is not None:
                        stalled.add(series)
        return progress

    def _due(self, job: Job, state: _TickState, failing: Optional[Set[Tuple[str, str]]] = None) -> List[str]:
        try:
            with self.db.reader() as conn:
                keys = list(job.due(conn, self.clock, self.cal, self.sessions))
        except Exception as e:   # noqa: BLE001 — a broken due() is a job failure, surfaced and retried with backoff
            msg = f"due() raised {type(e).__name__}: {e}"
            if self.log is not None:
                self.log.exception("lifecycle %s due() failed", job.name)
            if not self._parked(self._load_failures(state.now)[0], job.name, "due", state.now):
                self._record_failure(job.name, "due", msg, state.now)
                if failing is not None:
                    failing.add((job.name, "due"))
            return []
        if failing is not None and (job.name, "due") in failing:             # TX-4: a due() that works again clears its failure
            self._clear_failure(job.name, "due", state.now)
            failing.discard((job.name, "due"))
        return keys

    def _run_key(self, job: Job, key: str, state: _TickState, failing: Set[Tuple[str, str]]) -> dict:
        kind = job.command_kind(key) if hasattr(job, "command_kind") else job.kind
        payload = job.payload(key)
        rid = self._request_id(job, key)
        res = self.bus.execute(Command(kind, payload, rid, ACTOR))
        item: Dict[str, Any] = {"job": job.name, "key": key, "kind": kind, "request_id": rid, "http": res.http, "ok": res.ok, "replayed": res.replayed}
        if res.ok:
            self._apply_ok(item, res)
            if (job.name, key) in failing:
                self._clear_failure(job.name, key, state.now)
                failing.discard((job.name, key))
            if (job.name, key) in self._busy:
                self._unblock(job.name, key, state.now)
            if item["status"] == "SKIPPED":
                retry = job.retry_payload(self.services, key, res)
                if retry is not None:
                    rid2 = self._request_id(job, key)
                    res2 = self.bus.execute(Command(kind, retry, rid2, ACTOR))
                    item["retry"] = {"request_id": rid2, "http": res2.http, "ok": res2.ok}
                    if res2.ok:
                        self._apply_ok(item, res2)
                        res = res2
            job.after_commit(self.services, key, res)
        elif res.http == 503:
            item.update(status="RETRY", note=f"{res.code}: {res.message}", code=res.code)
            self._busy_retry(job.name, key, f"{res.code}: {res.message}", state.now)
        else:
            item.update(status="FAILED", note=f"{res.code}: {res.message}", code=res.code)
            self._record_failure(job.name, key, f"{res.code}: {res.message}", state.now)
            failing.add((job.name, key))
        self._status[job.name] = {"ts": state.now, "key": key, "status": item["status"], "note": item.get("note")}
        state.ran.append(item)
        return item

    @staticmethod
    def _apply_ok(item: dict, res) -> None:
        body = res.body if isinstance(res.body, dict) else {}
        status = str(body.get("status") or "DONE").upper()
        item.update(status="SKIPPED" if status == "SKIPPED" else "DONE", note=str(body.get("note") or body.get("code") or ""), code=body.get("code"), http=res.http, ok=True,
                    replayed=res.replayed)

    def _run_batch(self, job: Job, keys: List[str], state: _TickState) -> dict:
        try:
            item = dict(job.run_batch(self.services, keys, self) or {})
            item.setdefault("job", job.name)
            item.setdefault("key", keys[-1])
            item.setdefault("keys", len(keys))
            item.setdefault("status", "DONE")
            for k in keys:
                if k in (item.get("recorded") or []) or job.name == "INTEGRITY_FULL":
                    state.done.add((job.name, k))
            self._status[job.name] = {"ts": state.now, "key": keys[-1], "status": item["status"], "note": item.get("note")}
        except Exception as e:   # noqa: BLE001 — a batch fault is a failure of its first key, with backoff
            item = {"job": job.name, "key": keys[0], "status": "FAILED", "note": f"{type(e).__name__}: {e}", "keys": len(keys)}
            if self.log is not None:
                self.log.exception("lifecycle %s batch failed", job.name)
            self._record_failure(job.name, keys[0], item["note"], state.now)
            for k in keys:
                state.done.add((job.name, k))
        state.ran.append(item)
        return item

    # ---- ids, parking, failures -----------------------------------------------------------------------------------------------------
    def _request_id(self, job: Job, key: str) -> str:
        base = f"job:{job.name}:{key}"
        if len(base) > RID_MAX - 6:
            base = f"job:{job.name}:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        with self.db.reader() as conn:
            n = int(conn.execute("SELECT COUNT(*) FROM commands WHERE request_id=? OR request_id LIKE ?", (base, base + ":r%")).fetchone()[0] or 0)
        return base if n == 0 else f"{base}:r{n}"

    def _load_failures(self, now: int) -> Tuple[Dict[Tuple[str, str], int], Set[Tuple[str, str]]]:
        parked: Dict[Tuple[str, str], int] = {}
        failing: Set[Tuple[str, str]] = set()
        with self.db.reader() as conn:
            for r in conn.execute("SELECT job, key, parked_until_ts FROM job_failures").fetchall():
                failing.add((r["job"], r["key"]))
                if r["parked_until_ts"] is not None and int(r["parked_until_ts"]) > now:
                    parked[(r["job"], r["key"])] = int(r["parked_until_ts"])
        return parked, failing

    def _parked(self, parked: Dict[Tuple[str, str], int], job: str, key: str, now: int) -> bool:
        if (job, key) in parked:
            return True
        ts = self._deferred.get((job, key))
        return ts is not None and ts > now

    # ---- TX-5: the database is busy (503) ------------------------------------------------------------------------------------------
    def _busy_retry(self, job: str, key: str, cause: str, now: int) -> None:
        """Count, log and (after BLOCKED_AFTER in a row) surface a key that the database keeps answering busy for."""
        self.errors += 1
        self.retries += 1
        self.last_error = f"{job} {key}: {cause} (retried next tick)"
        b = self._busy.setdefault((job, key), {"n": 0, "since": now, "cause": cause, "attention": None})
        b["n"] += 1
        b["cause"] = cause
        if self.log is not None:
            self.log.warning("lifecycle %s %s RETRY %d (database busy): %s", job, key, b["n"], cause)
        if b["n"] >= BLOCKED_AFTER and b["attention"] is None:
            message = (f"{job} {key}: {b['n']} attempts since {ny_of(int(b['since'])).strftime('%H:%M:%S')} ET were refused because the database is busy ({cause}); "
                       "settlements, accruals and marks wait until it frees - another program (a second server, a database tool) may hold it")
            try:
                with self.db.writer(timeout=1.0) as tx:
                    open_row = tx.one("SELECT id FROM attention WHERE source=? AND code='LIFECYCLE_BLOCKED' AND ref_type='job' AND ref_id=? AND resolved_ts IS NULL",
                                      (SOURCE, f"{job}:{key}"))
                    if open_row is None:
                        tx.x("INSERT INTO attention(ts, severity, source, code, message, details_json, account_id, ref_type, ref_id, resolved_ts, auto_resolve)"
                             " VALUES (?,?,?,?,?,?,NULL,'job',?,NULL,1)",
                             (now, "WARN", SOURCE, "LIFECYCLE_BLOCKED", message, dumps({"job": job, "key": key, "retries": b["n"], "cause": cause, "since": b["since"],
                                                                                          "ref_type": "job", "ref_id": f"{job}:{key}", "auto_resolve": 1}), f"{job}:{key}"))
                b["attention"] = "open"
            except Exception as e:   # noqa: BLE001 — the database is the busy thing: kept in status()['blocked'] and written at a later attempt
                if self.log is not None:
                    self.log.warning("lifecycle could not record LIFECYCLE_BLOCKED for %s %s yet: %s", job, key, e)

    def _unblock(self, job: str, key: str, now: int) -> None:
        b = self._busy.pop((job, key), None)
        if b is None:
            return
        try:
            with self.db.writer(timeout=2.0) as tx:
                tx.x("UPDATE attention SET resolved_ts=? WHERE source=? AND code='LIFECYCLE_BLOCKED' AND ref_type='job' AND ref_id=? AND resolved_ts IS NULL",
                     (now, SOURCE, f"{job}:{key}"))
                if b.get("attention") is None and int(b.get("n") or 0) >= BLOCKED_AFTER:
                    # the database refused every write while it was busy (an outside lock): the delay is still recorded, as resolved history
                    tx.x("INSERT INTO attention(ts, severity, source, code, message, details_json, account_id, ref_type, ref_id, resolved_ts, auto_resolve)"
                         " VALUES (?,?,?,?,?,?,NULL,'job',?,?,1)",
                         (int(b["since"]), "WARN", SOURCE, "LIFECYCLE_BLOCKED",
                          f"{job} {key} waited {int(b['n'])} attempts from {ny_of(int(b['since'])).strftime('%H:%M:%S')} ET for a busy database ({b['cause']}); it ran at "
                          f"{ny_of(int(now)).strftime('%H:%M:%S')} ET", dumps({"job": job, "key": key, "retries": int(b["n"]), "cause": b["cause"], "since": b["since"],
                                                                               "ran_ts": now, "ref_type": "job", "ref_id": f"{job}:{key}"}), f"{job}:{key}", now))
        except Exception as e:   # noqa: BLE001 — resolved at the next success of any key of the job
            if self.log is not None:
                self.log.warning("lifecycle could not resolve LIFECYCLE_BLOCKED for %s %s: %s", job, key, e)
        if self.log is not None:
            self.log.info("lifecycle %s %s ran after %d busy retries", job, key, int(b.get("n") or 0))

    def _record_failure(self, job: str, key: str, error: str, now: int) -> None:
        error = str(error)[:500]
        ref = f"{job}:{key}"
        self.errors += 1                                  # TX-5: every FAILED key, batch, due() and before() fault counts
        self.last_error = f"{job} {key}: {error}"
        with self.db.writer() as tx:
            row = tx.one("SELECT failures FROM job_failures WHERE job=? AND key=?", (job, key))
            n = (int(row["failures"]) if row else 0) + 1
            delay = BACKOFF_S[n - 1] if n <= len(BACKOFF_S) else HOURLY_S
            parked = now + delay
            tx.x("INSERT INTO job_failures(job, key, failures, last_error, last_ts, parked_until_ts) VALUES (?,?,?,?,?,?)"
                 " ON CONFLICT(job, key) DO UPDATE SET failures=excluded.failures, last_error=excluded.last_error, last_ts=excluded.last_ts, parked_until_ts=excluded.parked_until_ts",
                 (job, key, n, error, now, parked))
            severity = "ERROR" if n >= ERROR_AFTER else "WARN"
            message = f"{job} {key} failed {n}x: {error} (next retry in {delay // 60} min)"
            details = dumps({"job": job, "key": key, "failures": n, "error": error, "parked_until_ts": parked, "ref_type": "job", "ref_id": ref, "auto_resolve": 1})
            existing = tx.one("SELECT id FROM attention WHERE source=? AND code='JOB_FAILED' AND ref_type='job' AND ref_id=? AND resolved_ts IS NULL", (SOURCE, ref))
            if existing is not None:
                tx.x("UPDATE attention SET ts=?, severity=?, message=?, details_json=? WHERE id=?", (now, severity, message, details, int(existing["id"])))
            else:
                tx.x("INSERT INTO attention(ts, severity, source, code, message, details_json, account_id, ref_type, ref_id, resolved_ts, auto_resolve)"
                     " VALUES (?,?,?,?,?,?,NULL,'job',?,NULL,1)", (now, severity, SOURCE, "JOB_FAILED", message, details, ref))
        if self.log is not None:
            self.log.error("lifecycle %s %s failed (%d): %s", job, key, n, error)

    def _clear_failure(self, job: str, key: str, now: int) -> None:
        with self.db.writer() as tx:
            tx.x("DELETE FROM job_failures WHERE job=? AND key=?", (job, key))
            tx.x("UPDATE attention SET resolved_ts=? WHERE source=? AND code='JOB_FAILED' AND ref_type='job' AND ref_id=? AND resolved_ts IS NULL", (now, SOURCE, f"{job}:{key}"))

    # ---- views ------------------------------------------------------------------------------------------------------------------------
    def status(self) -> dict:
        """The ``/api/jobs`` view: ``{jobs: [{name, kind, when, phase, available, note, last_run_ts, last_key, last_status, last_note, next_due, failing}], ...}``."""
        last: Dict[str, dict] = {}
        failing: Dict[str, int] = {}
        with self.db.reader() as conn:
            for r in conn.execute("SELECT job, MAX(run_ts) ts FROM jobs_done GROUP BY job").fetchall():
                row = conn.execute("SELECT key, status, note, run_ts FROM jobs_done WHERE job=? AND run_ts=? ORDER BY key DESC LIMIT 1", (r["job"], r["ts"])).fetchone()
                if row is not None:
                    last[r["job"]] = dict(row)
            for r in conn.execute("SELECT job, COUNT(*) n FROM job_failures GROUP BY job").fetchall():
                failing[r["job"]] = int(r["n"])
        jobs = []
        for j in self.jobs:
            ok, note = j.available(self.services)
            notes = "; ".join(f"{k}: {v}" for k, v in sorted(j.notes.items()))
            lr, mem = last.get(j.name), self._status.get(j.name, {})
            jobs.append({"name": j.name, "kind": j.kind, "when": j.when, "phase": j.phase, "available": ok, "note": note or notes or mem.get("note") or "",
                         "last_run_ts": lr["run_ts"] if lr else mem.get("ts"), "last_key": lr["key"] if lr else mem.get("key"),
                         "last_status": lr["status"] if lr else mem.get("status"), "last_note": lr["note"] if lr else mem.get("note"), "next_due": j.when,
                         "failing": failing.get(j.name, 0)})
        return {"jobs": jobs, "ticks": self.ticks, "last_tick_ts": self.last_tick_ts, "interval_s": self.interval_s, "alive": self.is_alive(), "last_error": self.last_error,
                "deferred": len(self._deferred), "last_result": self.last_result, "errors": self.errors, "retries": self.retries,
                "blocked": [{"job": j, "key": k, "retries": b["n"], "since": b["since"], "cause": b["cause"]} for (j, k), b in sorted(self._busy.items())]}


def run_now(services, job: Optional[str] = None) -> dict:
    """``POST /api/jobs/run`` (loopback): one tick now, optionally for one job; ``{ran: [...], at, passes, skipped_modules}``."""
    sched = getattr(services, "lifecycle", None)
    if sched is None or not hasattr(sched, "tick"):
        raise RuntimeError("no lifecycle scheduler is bound (services.lifecycle)")
    out = sched.tick(only=job)
    return {"ran": out["ran"], "at": out["at"], "passes": out["passes"], "counts": out["counts"], "skipped_modules": out["skipped_modules"], "job": job}


__all__ = ["LifecycleScheduler", "REGISTRY", "Job", "accrue_segment", "register", "run_job", "run_now", "TICK_S", "MAX_PASSES", "SKIP_RETRY_S", "BACKOFF_S", "ERROR_AFTER"]
