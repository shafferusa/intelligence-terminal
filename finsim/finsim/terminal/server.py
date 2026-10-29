"""Server, threading and operations: ``TerminalServer``, ``Runtime``, the single-instance lock, ``Supervisor``, ``serve``
(TERMINAL_DESIGN.md §16; WP-11).

Conventions
- ONE process, no child processes: the quote worker (scheduler + fetch pool), the matcher, the lifecycle scheduler and
  the HTTP request threads are threads of this process; the supervisor runs on the main thread.  Nothing here spawns
  a shell or an interpreter (TERMINAL_DECISIONS §17).
- Single instance: ``<home>/terminal.lock`` AND ``<db_path>.lock`` (the database's own lock, TX-1 2026-10-21) are
  byte-locked (``msvcrt.locking(LK_NBLCK)`` on Windows, ``fcntl.flock`` elsewhere) for the process lifetime — the OS
  releases them on death, so there are no stale-lock heuristics and never an ``os.kill(pid, 0)``.  The locked byte sits
  beyond the JSON (offset ``LOCK_OFFSET``), so ``{pid, port, started_ts, version, home, db}`` stays readable by
  ``status`` / ``stop`` / a second server while it is held.  A second ``serve`` on the same home, OR on any home with
  the same ``--db`` / ``FINSIM_TERMINAL_DB`` (or a database whose own folder's ``terminal.lock`` is held by a running
  terminal, e.g. 1.0.3), exits 3 naming the holder's pid / port BEFORE anything opens the database; so does a port
  that is already bound (``SO_EXCLUSIVEADDRUSE`` on Windows + ``allow_reuse_address = False``: a second bind fails with
  WinError 10048 / ``OSError``) — the port is bound before the database is opened, backed up or migrated, so a refused
  start never writes a backup or a meta row.
- STARTUP-03 (FINSIM1_PLAN P1 task 6): the lock JSON also carries ``code`` (the code tree that runs: the folder holding the
  ``finsim`` package).  A held home lock prints ``already_running_message``: "FinSim is already running: finsim-terminal
  <version>, PID <pid>, since <ET time>, http://127.0.0.1:<port>/ (code <tree>). Open it with `python -m finsim open` or the
  FinSim desktop icon. To run this copy instead: `python -m finsim stop`, then `python -m finsim serve`." (exit 3; a lock
  written by an older release has no ``code`` and the clause is left out).  A port another program holds prints
  ``port_taken_message``: "port <p> is used by another program (no FinSim lock in <home>); run `python -m finsim status`
  or pass --port" - never the refused process's own pid.
- Provider health is restored from ``provider_health`` at start EXCEPT the retired news providers (``attention.retired_provider``:
  yahoo_news, fed, newspaper, news_*): their stored rows stay in the database, inert, and never show (FINSIM1_PLAN 15.7 item 5).
- The news feature is gone (owner directive 2026-10-27 section 2; 1.0.5 / 1.0.6): no news store, no news fetcher, no ``news``
  module or health entry.  ``build_runtime(news_enabled=...)`` is still accepted and ignored (removed in 1.0.5), so older callers
  and tests keep working; the stored ``news_enabled`` setting and ``news_items`` table stay in an older database, inert.
- Startup order (exit codes): offline guard [5] -> home lock [3] -> database lock [3] -> bind [3] -> log -> DB open /
  migrate [4 newer schema] / seed / first-run lifetimes (``books.ensure_first_run``: P1 / F1 / I1 with their capital,
  idempotent) / ``integrity("ledger")`` recorded (a failure holds that lifetime and opens an ERROR attention; the server
  still starts) -> master, persisted quotes (``close`` / ``stale``, never ``delayed``), curve / rates / FX stores,
  provider health -> workers (quote worker, matcher, lifecycle when WP-08 is merged) -> HTTP thread -> ``ready`` ->
  supervisor loop.  Connections that arrive while the database opens wait in the listen backlog; health answers only
  from the ready point.  Any other failure is logged with its traceback and exits 5.
- A client that drops its connection (the browser aborting a request on navigation: ConnectionAbortedError /
  ConnectionResetError / BrokenPipeError) is one DEBUG line in the log, never a traceback on stderr; every other
  exception while serving keeps its full traceback (stderr and the log).
- ``Runtime`` is the ``services`` bundle every handler sees (§2.12): ``db, clock, cal, sessions, instruments, cache,
  chains, providers, worker, marks, accounting, bus, matcher, lifecycle, router, httpd, log, lock, settings,
  started_ts, books, rates, curve, fxstore, previews`` plus one attribute per product module (``orders, options,
  futures, fx, credit, lending, hedges, risk, compliance, strategies, performance, prime, lifetimes, swaps, swaps_trs,
  private, private_credit, dividends, bonds, repo``) — ``None`` when that package is not merged, so the API answers
  501 NOT_BUILT instead of inventing anything.  ``rt.modules`` records bound / missing / broken for health.
- ``--offline``: ``FakeProvider`` instead of the network; refused (exit 5) on the default home
  (``%USERPROFILE%\\.finsim``) or whenever the database would be the default one — fake prices never reach the real
  book; ``health.offline = true`` and the UI paints the permanent red banner.
- Graceful shutdown (``POST /api/shutdown`` on loopback, Ctrl-C, SIGTERM): the stop event is set -> workers finish
  their current unit (<= 3 s; a command in flight commits or rolls back) -> ``cache.persist()`` -> ``httpd.shutdown()``
  -> ``server_close()`` -> ``db.close()`` (WAL checkpoint) -> the lock is released and the file removed -> exit 0.
- ``Supervisor.check()`` every 30 s: a dead worker thread is restarted from its factory and reported as an ERROR
  attention (``WORKER_DIED``); a worker silent for more than 3 of its cycles - no tick ended and, for the quote worker, no progress
  inside its tick either (``last_progress_ts``, V104R2-BW-10) - is a WARN attention (``WORKER_STALLED``);
  error counters are sampled so health can show ``errors_1h``.  The checks never touch the network.
"""
from __future__ import annotations

import importlib
import json
import os
import signal
import socket
import sys
import threading
import time as _time
import traceback
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer
from typing import Callable, Deque, Dict, Optional, Tuple

from . import APP_ID, VERSION, config
from . import accounting as _accounting, books as _books, treasury as _treasury
from .api import Router, register as _register_api, thread_alive
from .attention import retired_provider
from .calendar import TradingCalendar
from .clock import Clock, RealClock
from .commands import Command, CommandBus
from .db import Database, SchemaTooNew, get_setting
from .instruments import InstrumentMaster
from .sessions import SessionTable

try:
    import msvcrt   # Windows byte-range locks
except ImportError:   # pragma: no cover - POSIX
    msvcrt = None
    import fcntl

EXIT_OK, EXIT_INSTANCE, EXIT_SCHEMA, EXIT_STARTUP = 0, 3, 4, 5
CLIENT_GONE = (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)
LOCK_OFFSET = 1 << 20              # the locked byte sits far beyond the JSON so the file stays readable while held
MAX_BODY = 2 << 20
SUPERVISOR_INTERVAL_S = 30.0
STOP_TIMEOUT_S = 3.0
WORKER_CYCLE_S = {"quotes": 15, "matcher": 15, "lifecycle": 30}

# optional product modules: attribute on the Runtime -> module path (relative to this package)
def _join(th, timeout: float) -> None:
    """``Thread.join`` that tolerates the shadowed ``Thread._stop`` (see ``api.thread_alive``): a TypeError means the
    thread has already finished."""
    try:
        th.join(timeout)
    except TypeError:
        pass


OPTIONAL_MODULES = {"orders": "orders", "options": "products.options", "futures": "products.futures", "fx": "fx", "credit": "credit", "lending": "lending",
                    "hedges": "hedges", "risk": "risk", "compliance": "compliance", "strategies": "strategies", "performance": "performance", "prime": "prime",
                    "lifetimes": "lifetimes", "swaps": "products.swaps", "swaps_trs": "products.swaps_trs", "private": "products.private",
                    "private_credit": "products.private_credit", "dividends": "products.dividends", "bonds": "products.bonds", "repo": "repo",
                    # Build 2 (BUILD2_PLAN C2): PB OTC options / forwards (P12), the counterparty directory (P08), real Treasuries (P07, optional);
                    # a module that is not in the tree stays "missing" (tolerated), its routes answer 501 / are simply absent
                    "otc_options": "products.otc_options", "counterparties": "counterparties", "treasuries": "products.treasuries",
                    "swaps_cds": "products.swaps_cds",
                    # FINSIM1_PLAN C2: the FinSim1 completion build - the Dashboard (P1), the two trade blotters (P2), NAV history and
                    # restatements (P3), cash / settlement / collateral (P6), the historical import (P5); each stays "missing" until it lands
                    "dashboard": "dashboard", "blotters": "blotters", "navhistory": "navhistory", "collateral": "collateral", "importer": "importer",
                    # FX_CCS_PLAN 1.0.6.1 (P-D): the standing settlement-funding instruction of OTC contracts (K8)
                    "ccyfunding": "ccyfunding"}


def code_tree() -> str:
    """STARTUP-03: the code tree this process runs (the folder that holds the ``finsim`` package), written into the lock."""
    return os.path.dirname(os.path.dirname(config.package_dir()))


def already_running_message(info: Optional[dict], host: str = config.HOST) -> str:
    """STARTUP-03: what a second ``serve`` on a held home prints (exit 3), from the holder's lock JSON (``{}`` when unreadable)."""
    info = info if isinstance(info, dict) else {}
    version = info.get("version")
    pid = info.get("pid")
    started = info.get("started_ts")
    port = info.get("port")
    parts = [f"{APP_ID} {version}" if version else f"{APP_ID} (version unknown)", f"PID {pid if pid is not None else '?'}"]
    if isinstance(started, (int, float)) and not isinstance(started, bool) and started > 0:
        from .clock import ny_str
        parts.append(f"since {ny_str(int(started))}")
    if port:
        parts.append(f"http://{host}:{port}/")
    head = "FinSim is already running: " + ", ".join(parts)
    if info.get("code"):
        head += f" (code {info['code']})"
    return (head + ". Open it with `python -m finsim open` or the FinSim desktop icon. To run this copy instead: `python -m finsim stop`, "
            "then `python -m finsim serve`.")


def port_taken_message(port, home: str) -> str:
    """STARTUP-03: a port bound by another program (this home's lock is ours, so it is no FinSim of this home)."""
    return f"port {port} is used by another program (no FinSim lock in {home}); run `python -m finsim status` or pass --port"


# ---- the single-instance lock ---------------------------------------------------------------------------------------------------
class InstanceLock:
    """``<home>/terminal.lock``: ``acquire()`` takes the byte lock (False when another process — or another handle in
    this process — holds it); ``write(info)`` stores the JSON; ``release()`` unlocks, closes and removes the file."""

    def __init__(self, path: str):
        self.path = path
        self._fh = None

    def acquire(self) -> bool:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)) or ".", exist_ok=True)
        fh = open(self.path, "a+b")
        try:
            if msvcrt is not None:
                fh.seek(LOCK_OFFSET)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:   # pragma: no cover - POSIX
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True

    @property
    def held(self) -> bool:
        return self._fh is not None

    def write(self, info: dict) -> None:
        if self._fh is None:
            raise RuntimeError("lock not held")
        self._fh.seek(0)
        self._fh.truncate(0)
        self._fh.write(json.dumps(info, sort_keys=True).encode("utf-8"))
        self._fh.flush()
        try:
            os.fsync(self._fh.fileno())
        except OSError:
            pass

    def read(self) -> Optional[dict]:
        """The holder's JSON (None when absent / unreadable)."""
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                text = f.read()
            data = json.loads(text) if text.strip() else None
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            if msvcrt is not None:
                fh.seek(LOCK_OFFSET)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:   # pragma: no cover - POSIX
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            fh.close()
        except OSError:
            pass
        try:
            os.remove(self.path)
        except OSError:
            pass


def default_home() -> str:
    return os.path.join(os.path.expanduser("~"), ".finsim")


def db_lock_path(db_path: str) -> str:
    """TX-1: the database's own lock file, next to it (``<db_path>.lock``)."""
    return os.path.abspath(db_path) + ".lock"


def probe_lock(path: str) -> Optional[dict]:
    """The holder's JSON (``{"pid": None}`` when unreadable) when ANOTHER handle holds the byte lock of an existing lock file,
    else None.  Nothing is created or written (an absent file is simply not held); used for the ``terminal.lock`` of the
    database's own folder, which a terminal on that home (1.0.3 included) holds while it has the database open."""
    if not os.path.isfile(path):
        return None
    try:
        fh = open(path, "rb")
    except OSError:
        return None
    try:
        try:
            if msvcrt is not None:
                fh.seek(LOCK_OFFSET)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                fh.seek(LOCK_OFFSET)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:   # pragma: no cover - POSIX
                fcntl.flock(fh.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            return InstanceLock(path).read() or {"pid": None}
        return None
    finally:
        fh.close()


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def offline_refusal(home: str, db_path: str) -> Optional[str]:
    """Why ``--offline`` is refused: the default home, or a database under it (fake prices never reach the real book)."""
    dh = default_home()
    if _same_path(home, dh):
        return f"--offline is refused on the default home {dh}: pass --home <scratch folder> (fake prices never reach the real book)"
    if _same_path(os.path.dirname(os.path.abspath(db_path)), dh) or _same_path(db_path, os.path.join(dh, "terminal.db")):
        return f"--offline is refused with the database {db_path} under the default home: unset FINSIM_TERMINAL_DB or pass --db under --home"
    return None


# ---- the HTTP server -------------------------------------------------------------------------------------------------------------
class TerminalServer(ThreadingHTTPServer):
    """Exclusive bind: ``allow_reuse_address = False`` plus ``SO_EXCLUSIVEADDRUSE`` on Windows, so a second bind of the
    port fails with ``OSError`` (WinError 10048) instead of silently sharing it.  ``server_bind`` skips the reverse
    DNS lookup ``HTTPServer`` does (``getfqdn``), which can stall a start on a laptop without a resolver."""

    allow_reuse_address = False
    daemon_threads = True
    request_queue_size = 64

    log = None                       # the runtime's logger (set by serve); handle_error writes there

    def server_bind(self) -> None:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)

    def handle_error(self, request, client_address) -> None:   # noqa: ARG002 - stdlib signature
        """SHELL-24 / USX-37: a client that went away (the browser aborting a keep-alive or in-flight request) is one DEBUG
        line, never a stderr traceback that buries real errors; any other exception keeps its full traceback (log + the
        stdlib's stderr report)."""
        exc = sys.exc_info()[1]
        if isinstance(exc, CLIENT_GONE):
            if self.log is not None:
                self.log.debug("client %s dropped the connection: %s", client_address, exc)
            return
        if self.log is not None:
            self.log.error("exception while serving %s\n%s", client_address, traceback.format_exc())
        super().handle_error(request, client_address)


class _StartingHandler(BaseHTTPRequestHandler):
    """The bound socket's handler until the runtime is built (``serve`` swaps in ``make_handler(rt)`` before it serves);
    never reached in practice because nothing is served before then."""
    protocol_version = "HTTP/1.1"

    def do_GET(self):   # noqa: N802
        body = b'{"error":{"code":"STARTING","message":"the terminal is starting","details":{}},"request_id":null}'
        self.send_response(503)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Retry-After", "1")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = do_PATCH = do_PUT = do_DELETE = do_HEAD = do_GET

    def log_message(self, fmt, *args):   # noqa: D401 - quiet
        pass


def make_handler(rt) -> type:
    """A ``BaseHTTPRequestHandler`` bound to ``rt.router``; logs through the runtime's logger (stderr may be None)."""
    router: Router = rt.router
    log = rt.log

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = f"{APP_ID}/{VERSION}"
        sys_version = ""

        def log_message(self, fmt, *args):   # noqa: N802 - stdlib name
            if log is not None:
                log.debug("http %s " + fmt, self.address_string(), *args)

        def _dispatch(self, method: str) -> None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length > MAX_BODY:
                self._send(413, {"error": {"code": "TOO_LARGE", "message": f"body over {MAX_BODY} bytes", "details": {}}, "request_id": None}, {})
                return
            raw = self.rfile.read(length) if length > 0 else b""
            try:
                status, body, headers = router.handle(method, self.path, None, raw if raw else None, self.client_address[0], dict(self.headers.items()))
            except Exception as e:   # noqa: BLE001 — the router maps everything; this is the last net
                if log is not None:
                    log.exception("unhandled error in %s %s", method, self.path)
                status, body, headers = 500, {"error": {"code": "INTERNAL", "message": f"{type(e).__name__}: {e}", "details": {}}, "request_id": None}, {}
            self._send(status, body, headers, head=(method == "HEAD"))

        def _send(self, status: int, body, headers: dict, head: bool = False) -> None:
            if isinstance(body, (bytes, bytearray)):
                data = bytes(body)
            elif isinstance(body, str):
                data = body.encode("utf-8")
            else:
                data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            try:
                self.send_response(int(status))
                hdrs = dict(headers or {})
                hdrs.setdefault("Content-Type", "application/json; charset=utf-8")
                hdrs.setdefault("Cache-Control", "no-store")
                for k, v in hdrs.items():
                    self.send_header(k, str(v))
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if not head:
                    self.wfile.write(data)
            except (ConnectionError, OSError) as e:   # the client went away: logged, never fatal
                if log is not None:
                    log.debug("client dropped %s: %s", self.path, e)

        def do_GET(self):   # noqa: N802
            self._dispatch("GET")

        def do_HEAD(self):   # noqa: N802
            self._dispatch("HEAD")

        def do_POST(self):   # noqa: N802
            self._dispatch("POST")

        def do_PATCH(self):   # noqa: N802
            self._dispatch("PATCH")

        def do_PUT(self):   # noqa: N802
            self._dispatch("PUT")

        def do_DELETE(self):   # noqa: N802
            self._dispatch("DELETE")

    return Handler


# ---- the runtime -----------------------------------------------------------------------------------------------------------------
class Runtime:
    """Everything the process owns (§16), passed to every handler as ``services``."""

    def __init__(self):
        for name in ("db", "clock", "cal", "sessions", "instruments", "cache", "chains", "providers", "worker", "marks", "accounting", "bus", "matcher", "lifecycle",
                     "router", "httpd", "log", "lock", "db_lock", "rates", "curve", "fxstore", "previews", "supervisor", "fp", "port", "home", "db_path"):
            setattr(self, name, None)
        for name in OPTIONAL_MODULES:
            setattr(self, name, None)
        self.books = _books
        self.settings: dict = {}
        self.modules: Dict[str, str] = {}
        self.modules_loaded: dict = {}
        self.started_ts: int = 0
        self.offline = False
        self.ready = False
        self.stop_event = threading.Event()
        self.fetch_threads = 2
        self._stopped = False

    # ---- workers ---------------------------------------------------------------------------------------------------
    def make_worker(self, name: str):
        """Build (and start) a fresh worker thread of ``name`` (quotes | matcher | lifecycle); rebinds the references."""
        from .matcher import Matcher
        from .quotes.worker import QuoteWorker
        if name == "matcher":
            m = Matcher(self.db, self.bus, self.cache, self.sessions, self.instruments, self.clock, self.log, services=self, interval_s=15)
            self.matcher = m
            if self.worker is not None:
                self.worker.set_matcher(m)
            m.start()
            return m
        if name == "quotes":
            w = QuoteWorker(self.cache, self.chains, self.instruments, self.providers, self.sessions, self.bus, self.matcher, self.clock, self.log,
                            fetch_threads=self.fetch_threads, db=self.db, services=self, curve=self.curve, rates=self.rates, fxstore=self.fxstore, cal=self.cal)
            self.worker = w
            w.start()
            return w
        if name == "lifecycle":
            mod = self.modules_loaded.get("lifecycle")
            if mod is None:
                return None
            lc = mod.LifecycleScheduler(self.bus, self.db, self, self.clock, self.log, interval_s=30)
            self.lifecycle = lc
            lc.start()
            return lc
        raise ValueError(f"unknown worker {name}")

    def start_workers(self) -> None:
        """§16 step 6 order: quote worker, matcher, lifecycle (first tick = catch-up, in its own thread)."""
        if self.worker is not None and self.worker.ident is None:
            self.worker.start()
        if self.matcher is not None and self.matcher.ident is None:
            self.matcher.start()
        if self.lifecycle is not None and hasattr(self.lifecycle, "start") and getattr(self.lifecycle, "ident", 1) is None:
            self.lifecycle.start()

    def stop_workers(self, timeout: float = STOP_TIMEOUT_S) -> None:
        """Stop every worker (each finishes its current unit, <= ``timeout``), then persist the quote cache once more:
        the worker's own ``stop`` persists too, but it cannot finish on 3.12 when its thread shadows ``Thread._stop``."""
        self.stop_event.set()
        w = self.worker
        if w is not None:
            try:
                w.stop(timeout)
            except TypeError:                     # the shadowed Thread._stop: the thread has finished; the persist below covers it
                pass
            except Exception as e:   # noqa: BLE001 — a stop failure is logged; shutdown goes on
                if self.log:
                    self.log.error("quote worker stop failed: %s", e)
        m = self.matcher
        if m is not None:
            try:
                m.stop()
                if thread_alive(m):
                    _join(m, timeout)
            except Exception as e:   # noqa: BLE001
                if self.log:
                    self.log.error("matcher stop failed: %s", e)
        lc = self.lifecycle
        if lc is not None:
            try:
                if hasattr(lc, "stop"):
                    lc.stop()
                if thread_alive(lc):
                    _join(lc, timeout)
            except Exception as e:   # noqa: BLE001
                if self.log:
                    self.log.error("lifecycle stop failed: %s", e)
        if self.cache is not None and self.db is not None and getattr(self.db, "_opened", True):
            try:
                self.cache.persist()
            except Exception as e:   # noqa: BLE001
                if self.log:
                    self.log.error("final quote persist failed: %s", e)

    def shutdown(self) -> None:
        """The graceful sequence (§16): workers -> persist -> httpd -> db -> lock."""
        if self._stopped:
            return
        self._stopped = True
        self.ready = False
        self.stop_workers()
        httpd = self.httpd
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception as e:   # noqa: BLE001
                if self.log:
                    self.log.error("httpd shutdown failed: %s", e)
            try:
                httpd.server_close()
            except Exception:   # noqa: BLE001
                pass
            self.httpd = None
        if self.db is not None:
            try:
                self.db.close()
            except Exception as e:   # noqa: BLE001
                if self.log:
                    self.log.error("db close failed: %s", e)
        if self.db_lock is not None:
            self.db_lock.release()
        if self.lock is not None:
            self.lock.release()

    close = shutdown


def _bind_optional(rt: Runtime, bus: CommandBus, log) -> None:
    """Import every optional product module that exists, register its handlers and bind it on the runtime."""
    rt.modules_loaded = {}
    for attr, rel in OPTIONAL_MODULES.items():
        full = f"{__package__}.{rel}"
        try:
            mod = importlib.import_module(full)
        except ModuleNotFoundError as e:
            if e.name and (e.name == full or full.startswith(e.name + ".") or e.name.endswith(rel.split(".")[-1])):
                rt.modules[attr] = "missing"
                continue
            rt.modules[attr] = f"error: {e}"
            if log:
                log.exception("optional module %s failed to import", full)
            continue
        except Exception as e:   # noqa: BLE001 — a broken module is reported, never hidden
            rt.modules[attr] = f"error: {type(e).__name__}: {e}"
            if log:
                log.exception("optional module %s failed to import", full)
            continue
        reg = getattr(mod, "register", None)
        if callable(reg):
            try:
                reg(bus, rt)
            except TypeError:
                reg(bus)
        setattr(rt, attr, mod)
        bind = getattr(mod, "bind", None)
        if callable(bind):                      # lending exposes itself; prime installs its Prime service + the accounting headroom hook
            try:
                bind(rt)
            except Exception as e:   # noqa: BLE001 — a failed bind is reported in health, never hidden
                rt.modules[attr] = f"error: bind: {type(e).__name__}: {e}"
                if log:
                    log.exception("optional module %s failed to bind", full)
                continue
        rt.modules[attr] = "bound"
    # lifecycle (WP-08) is a worker, not a product module: its lifecycle.* handlers are registered here, the thread is built later
    try:
        lc_mod = importlib.import_module(f"{__package__}.lifecycle")
        reg = getattr(lc_mod, "register", None)
        if callable(reg):
            reg(bus, rt)                        # after the product modules: their job kinds are already on the bus
        rt.modules_loaded["lifecycle"] = lc_mod
        rt.modules["lifecycle"] = "bound"
    except ModuleNotFoundError:
        rt.modules["lifecycle"] = "missing"
    except Exception as e:   # noqa: BLE001
        rt.modules["lifecycle"] = f"error: {type(e).__name__}: {e}"
        if log:
            log.exception("lifecycle failed to import")


def build_runtime(home: str, db_path: str, *, clock: Optional[Clock] = None, offline: bool = False, fake_provider=None, log=None, books=None,
                  fetch_threads: int = 2, port: Optional[int] = None, news_enabled: Optional[bool] = None, lock: Optional[InstanceLock] = None) -> Runtime:
    """Assemble the services bundle (§16 steps 3-4 and the handler registry).  Nothing is started; ``start_workers``
    does that.  ``books`` (default ``config.BOOKS``) seeds an empty database; tests pass ``[config.TEST_BOOK]``.
    ``news_enabled`` is accepted and ignored: the news feature was removed in 1.0.5 (owner directive 2026-10-27 section 2)."""
    from .marks import MarkProvider
    from .quotes.cache import ChainCache, QuoteCache
    from .quotes.providers import FakeProvider, ProviderSet
    from .quotes.rates import CurveStore, FxStore, RateStore
    from .quotes.worker import QuoteWorker, register_all as _register_quotes
    from .matcher import Matcher
    from .orders_preview import PreviewStore

    rt = Runtime()
    rt.home, rt.db_path, rt.port, rt.log, rt.lock, rt.offline, rt.fetch_threads = home, db_path, port, log, lock, bool(offline), int(fetch_threads)
    rt.clock = clock or RealClock()
    rt.started_ts = int(rt.clock.epoch())
    rt.cal = TradingCalendar(config.holidays_path())
    rt.sessions = SessionTable(rt.cal, rt.clock)
    rt.db = Database(db_path, rt.clock, log)
    rt.db.open(books=books)                                   # backup -> migrate (SchemaTooNew -> exit 4) -> seed
    with rt.db.reader() as conn:
        rt.settings = {k: get_setting(conn, k) for k in ("participation_rate", "session_overrides", "pb_otc_threshold_c", "qp_threshold_c")}
    if rt.settings.get("session_overrides"):
        rt.sessions.set_overrides(rt.settings["session_overrides"])
    rt.instruments = InstrumentMaster(rt.db, log)
    rt.instruments.load()
    if rt.instruments.count() == 0:
        with rt.db.writer() as tx:
            rt.instruments.seed(tx, rt.cal)
        rt.instruments.load()
    # Every start: the idempotent repair of foreign listings that a pre-2026-10-21 seed stored as USD / US_EQUITY
    # (InstrumentMaster.repair_foreign_listings logs one INFO line; a no-op once the rows are right).
    with rt.db.writer() as tx:
        repaired = rt.instruments.repair_foreign_listings(tx)
    if (repaired or {}).get("repaired"):
        rt.instruments.load()
    if offline:
        rt.fp = fake_provider or FakeProvider(rt.clock)
        rt.providers = ProviderSet.offline_set(rt.fp, rt.clock)
    else:
        rt.providers = ProviderSet.live(rt.clock, log)
    rt.cache = QuoteCache(rt.db, rt.instruments, rt.sessions, rt.clock, None, log)
    rt.chains = ChainCache(rt.clock, log=log)
    rt.curve, rt.rates, rt.fxstore = CurveStore(rt.db, None, rt.clock, log), RateStore(rt.db, None, log), FxStore(rt.db, None)
    rt.marks = MarkProvider(rt.cache, rt.chains, rt.instruments, rt.curve, rt.rates, rt.sessions, rt.clock, services=rt, fxstore=rt.fxstore, log=log)
    bus = CommandBus(rt.db, rt.clock, rt.cal, rt.sessions, rt.marks, log, services=rt)
    rt.bus = bus
    for store in (rt.cache, rt.curve, rt.rates, rt.fxstore):
        store.bus = bus
    rt.accounting = _accounting.Accounting(rt.db, rt.instruments, rt.marks, rt.clock, rt.cal, rt.sessions)
    rt.accounting.services = rt
    rt.previews = PreviewStore()
    # handlers: core, quotes, api light commands, then every optional product module that exists
    _treasury.register(bus)
    _books.register(bus)
    _register_quotes(bus)
    _register_api(bus)
    _bind_optional(rt, bus, log)
    # first run (§3.1) and the recorded ledger-level integrity run
    _books.ensure_first_run(rt)
    try:                                                      # BM-01: the old global Treasury reserve becomes every book's, once
        _books.migrate_treasury_reserve(rt)
    except Exception:   # noqa: BLE001 — logged; the per-book map is created by the next Settings save anyway
        if log:
            log.exception("treasury reserve migration failed")
    try:
        report = rt.accounting.integrity("ledger")
        if not report.ok and log:
            log.error("integrity(ledger) failed at start: %s", report.failing)
    except Exception:   # noqa: BLE001 — the start goes on so the owner can see the problem; it is logged loudly
        if log:
            log.exception("integrity(ledger) at start failed")
    # persisted state back into memory
    rt.cache.load_persisted()
    for store in (rt.curve, rt.rates, rt.fxstore):
        try:
            store.load_persisted()
        except Exception as e:   # noqa: BLE001
            if log:
                log.warning("%s load_persisted failed: %s", type(store).__name__, e)
    with rt.db.reader() as conn:
        rows = conn.execute("SELECT * FROM provider_health").fetchall()
    rows = [r for r in rows if not retired_provider(r["provider"])]          # 15.7 item 5: the retired news providers stay inert
    if rows and rt.providers.health is not None:
        rt.providers.health.load(rows)
    # workers (not started here)
    rt.matcher = Matcher(rt.db, bus, rt.cache, rt.sessions, rt.instruments, rt.clock, log, services=rt, interval_s=15)
    rt.worker = QuoteWorker(rt.cache, rt.chains, rt.instruments, rt.providers, rt.sessions, bus, rt.matcher, rt.clock, log, fetch_threads=int(fetch_threads),
                            db=rt.db, services=rt, curve=rt.curve, rates=rt.rates, fxstore=rt.fxstore, cal=rt.cal)
    lc_mod = rt.modules_loaded.get("lifecycle")
    if lc_mod is not None:
        try:
            rt.lifecycle = lc_mod.LifecycleScheduler(bus, rt.db, rt, rt.clock, log, interval_s=30)
        except Exception as e:   # noqa: BLE001
            rt.lifecycle = None
            rt.modules["lifecycle"] = f"error: {type(e).__name__}: {e}"
            if log:
                log.exception("LifecycleScheduler could not be built")

    def _on_commit(cmd: Command, res) -> None:
        if res is not None and res.ok and cmd.kind in ("order.submit", "order.resume") and rt.matcher is not None:
            rt.matcher.wake()
    bus.on_commit(_on_commit)
    rt.router = Router(rt)
    rt.supervisor = Supervisor(rt)
    return rt


# ---- the supervisor ----------------------------------------------------------------------------------------------------------
def _latest(*ts) -> Optional[int]:
    """The latest of the given epochs (None ignored; None when all are None)."""
    vals = [int(t) for t in ts if t is not None]
    return max(vals) if vals else None


class Supervisor:
    """Main-thread watchdog: restarts dead worker threads (ERROR attention ``WORKER_DIED``), flags stalled ones (WARN
    ``WORKER_STALLED``) and samples error counters for ``health.workers.errors_1h``."""

    def __init__(self, rt: Runtime, interval_s: float = SUPERVISOR_INTERVAL_S):
        self.rt = rt
        self.interval_s = float(interval_s)
        self.alive = False
        self.last_tick_ts: Optional[int] = None
        self.last_error: Optional[str] = None
        self.restarts: Dict[str, int] = {}
        self._errors: Deque[Tuple[int, str]] = deque(maxlen=2000)
        self._seen: Dict[str, int] = {}
        self._stalled: Dict[str, bool] = {}
        self._n = 0

    # ---- what each worker exposes ----------------------------------------------------------------------------------
    def _workers(self):
        rt = self.rt
        yield "quotes", rt.worker, (lambda: (rt.worker.stats() if rt.worker is not None else {})), "loop_errors", "last_tick_ts"
        yield "matcher", rt.matcher, (lambda: {"errors": getattr(rt.matcher, "errors", 0), "last_tick_ts": getattr(rt.matcher, "last_tick_ts", None)}), "errors", "last_tick_ts"
        if rt.lifecycle is not None:
            yield "lifecycle", rt.lifecycle, (lambda: {"errors": getattr(rt.lifecycle, "errors", 0), "last_tick_ts": getattr(rt.lifecycle, "last_tick_ts", None)}), "errors", "last_tick_ts"

    def _attention(self, severity: str, code: str, message: str, name: str) -> None:
        rt = self.rt
        self._n += 1
        try:
            rt.bus.execute(Command("attention.open", {"severity": severity, "source": "supervisor", "code": code, "message": message,
                                                      "details": {"worker": name, "ref_type": "worker", "ref_id": name}},
                                   f"sup:{code}:{name}:{int(rt.clock.epoch())}:{self._n}", "supervisor"))
        except Exception as e:   # noqa: BLE001 — the watchdog never dies because the bus is busy; it logs
            self.last_error = f"attention failed: {e}"
            if rt.log:
                rt.log.error("supervisor could not record %s for %s: %s", code, name, e)

    def check(self, now: Optional[int] = None) -> dict:
        """One pass; returns what it did (``restarted``, ``stalled``, ``eligibility`` notices opened / resolved)."""
        rt = self.rt
        now = int(now if now is not None else rt.clock.epoch())
        self.last_tick_ts = now
        out = {"restarted": [], "stalled": [], "recovered": []}
        if rt.stop_event.is_set():
            return out
        try:                                                  # SR-09: ELIGIBILITY_BELOW_THRESHOLD notices (light; no network)
            if getattr(rt, "bus", None) is not None and getattr(rt, "accounting", None) is not None:
                el = _books.eligibility_notices(rt)
                if el.get("opened") or el.get("resolved"):
                    out["eligibility"] = el
        except Exception as e:   # noqa: BLE001 — the watchdog's worker checks still run; the failure is logged
            self.last_error = f"eligibility notices: {type(e).__name__}: {e}"
            if rt.log:
                rt.log.exception("eligibility notices failed")
        for name, th, stats_fn, err_key, tick_key in list(self._workers()):
            if th is None:
                continue
            started = getattr(th, "ident", None) is not None
            alive = thread_alive(th)
            try:
                st = stats_fn() or {}
            except Exception as e:   # noqa: BLE001
                st = {}
                self.last_error = f"{name}: {e}"
            errs = int(st.get(err_key) or 0)
            prev = self._seen.get(name)
            if prev is not None and errs > prev:
                for _ in range(min(errs - prev, 50)):
                    self._errors.append((now, name))
            self._seen[name] = errs
            if started and not alive:
                try:
                    rt.make_worker(name)
                    self.restarts[name] = self.restarts.get(name, 0) + 1
                    out["restarted"].append(name)
                    if rt.log:
                        rt.log.error("worker %s died and was restarted (restart #%d)", name, self.restarts[name])
                    self._attention("ERROR", "WORKER_DIED", f"the {name} worker thread died and was restarted (restart #{self.restarts[name]}); last error: {st.get('last_error')}", name)
                except Exception as e:   # noqa: BLE001
                    self.last_error = f"restart {name}: {e}"
                    if rt.log:
                        rt.log.exception("worker %s could not be restarted", name)
                    self._attention("ERROR", "WORKER_DEAD", f"the {name} worker thread died and could not be restarted: {e}", name)
                continue
            last = _latest(st.get(tick_key), st.get("last_progress_ts"))      # V104R2-BW-10: progress inside a long tick counts
            cycle = WORKER_CYCLE_S.get(name, 30)
            stalled = bool(alive and last is not None and now - int(last) > 3 * cycle)
            if stalled and not self._stalled.get(name):
                self._stalled[name] = True
                out["stalled"].append(name)
                self._attention("WARN", "WORKER_STALLED", f"the {name} worker has been silent for {now - int(last)} s (more than 3 x {cycle} s)", name)
            elif not stalled and self._stalled.get(name):
                self._stalled[name] = False
                out["recovered"].append(name)
        return out

    def errors_1h(self, now: Optional[int] = None) -> Dict[str, int]:
        now = int(now if now is not None else self.rt.clock.epoch())
        out: Dict[str, int] = {}
        for ts, name in self._errors:
            if ts >= now - 3600:
                out[name] = out.get(name, 0) + 1
        return out

    def run(self, stop_event: threading.Event) -> None:
        self.alive = True
        try:
            while not stop_event.wait(self.interval_s):
                try:
                    self.check()
                except Exception as e:   # noqa: BLE001 — the watchdog survives its own failures; they are logged
                    self.last_error = f"{type(e).__name__}: {e}"
                    if self.rt.log:
                        self.rt.log.exception("supervisor check failed")
        finally:
            self.alive = False


# ---- serve -----------------------------------------------------------------------------------------------------------------------
def _say(msg: str, out: Optional[Callable[[str], None]]) -> None:
    if out is None:
        return
    try:
        if out is print:
            print(msg, flush=True)        # V104R1-08: a launcher reading a pipe / file sees 'ready http://...' at once, not at shutdown
        else:
            out(msg)
    except Exception:   # noqa: BLE001 — stdout may be None under pythonw
        pass


def serve(port: int = config.DEFAULT_PORT, home: Optional[str] = None, db: Optional[str] = None, offline: bool = False, *, ready: Optional[Callable] = None,
          out: Optional[Callable[[str], None]] = print, clock: Optional[Clock] = None, fake_provider=None, books=None, fetch_threads: int = 2,
          supervisor_interval: float = SUPERVISOR_INTERVAL_S, log=None, host: str = config.HOST) -> int:
    """The foreground server (§16).  Returns the exit code: 0 ok, 3 another instance / port in use, 4 newer schema, 5 startup error.
    ``ready(rt)`` is called once the HTTP thread serves (tests read the bound port from ``rt.httpd.server_address``)."""
    home = home or config.home()
    db_path = db or os.environ.get(config.DB_ENV) or os.path.join(home, "terminal.db")
    if offline:
        why = offline_refusal(home, db_path)
        if why:
            _say(why, out)
            return EXIT_STARTUP
    os.makedirs(home, exist_ok=True)
    lock = InstanceLock(os.path.join(home, "terminal.lock"))
    if not lock.acquire():
        _say(already_running_message(lock.read() or {}, host), out)
        return EXIT_INSTANCE
    # TX-1: the database's own lock (<db_path>.lock) and the lock of the home that holds the database file, BEFORE the database is
    # opened, backed up or migrated: two homes can never run two writers on one terminal.db
    db_lock = InstanceLock(db_lock_path(db_path))
    holder: Optional[dict] = None
    if not db_lock.acquire():
        holder = db_lock.read() or {"pid": None}
    else:
        other = os.path.join(os.path.dirname(os.path.abspath(db_path)), "terminal.lock")
        if not _same_path(other, lock.path):
            holder = probe_lock(other)
            if holder is not None:
                db_lock.release()
    if holder is not None:
        _say(f"another FinSim Terminal (PID {holder.get('pid') or '?'}, port {holder.get('port') or '?'}) has the database {db_path} open; "
             f"refusing to open it a second time (nothing was opened or written)", out)
        lock.release()
        return EXIT_INSTANCE
    # the port before the database (TX-1): a refused bind leaves no backup, no migration and no meta row behind
    try:
        httpd = TerminalServer((host, int(port)), _StartingHandler)
    except OSError as e:
        _say(port_taken_message(port, home), out)
        if log is not None:
            log.warning("port %s is in use: %s", port, e)
        db_lock.release()
        lock.release()
        return EXIT_INSTANCE
    bound_port = int(httpd.server_address[1])
    early = {"pid": os.getpid(), "port": bound_port, "started_ts": int(_time.time()), "version": VERSION, "home": home, "db": db_path, "starting": True,
             "code": code_tree()}
    lock.write(early)
    db_lock.write(early)
    if log is None:
        try:
            from finsim.log import get_logger
            log = get_logger("terminal", os.path.join(home, "terminal.log"), os.environ.get("FINSIM_LOG_LEVEL") or "INFO")
        except Exception:   # noqa: BLE001 — a logger that cannot open its file must not stop the server
            log = None
    httpd.log = log
    rt: Optional[Runtime] = None
    try:
        try:
            rt = build_runtime(home, db_path, clock=clock, offline=offline, fake_provider=fake_provider, log=log, books=books, fetch_threads=fetch_threads, port=bound_port, lock=lock)
        except SchemaTooNew as e:
            _say(str(e), out)
            if log:
                log.error("%s", e)
            httpd.server_close()
            db_lock.release()
            lock.release()
            return EXIT_SCHEMA
        except Exception:   # noqa: BLE001
            tb = traceback.format_exc()
            if log:
                log.error("startup failed\n%s", tb)
            _say("startup failed; the traceback is in " + os.path.join(home, "terminal.log"), out)
            httpd.server_close()
            db_lock.release()
            lock.release()
            return EXIT_STARTUP
        rt.db_lock = db_lock
        httpd.RequestHandlerClass = make_handler(rt)
        rt.httpd = httpd
        rt.port = bound_port
        info = {"pid": os.getpid(), "port": rt.port, "started_ts": rt.started_ts, "version": VERSION, "home": home, "db": db_path, "code": code_tree()}
        lock.write(info)
        db_lock.write(info)
        rt.start_workers()
        stop = rt.stop_event

        def _signal(signum, frame):   # noqa: ARG001
            stop.set()
        for sig in ("SIGINT", "SIGTERM", "SIGBREAK"):
            s = getattr(signal, sig, None)
            if s is not None:
                try:
                    signal.signal(s, _signal)
                except (ValueError, OSError):   # not the main thread (tests) - the stop event still works
                    pass
        t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.5}, name="http", daemon=True)
        t.start()
        rt.ready = True
        _say(f"ready http://{host}:{rt.port}/ ({APP_ID} {VERSION}, db {db_path}{', OFFLINE' if offline else ''})", out)
        if log:
            log.info("ready on %s:%s pid %s db %s offline=%s", host, rt.port, os.getpid(), db_path, offline)
        if ready is not None:
            try:
                ready(rt)
            except Exception:   # noqa: BLE001
                if log:
                    log.exception("ready callback failed")
        rt.supervisor.interval_s = float(supervisor_interval)
        rt.supervisor.run(stop)
        if log:
            log.info("stopping")
        rt.shutdown()
        _say("stopped", out)
        return EXIT_OK
    except KeyboardInterrupt:
        if rt is not None:
            rt.shutdown()
        else:
            try:
                httpd.server_close()
            except Exception:   # noqa: BLE001
                pass
            db_lock.release()
            lock.release()
        return EXIT_OK
    finally:
        if db_lock.held:
            db_lock.release()
        if lock.held:
            lock.release()


__all__ = ["TerminalServer", "Runtime", "Supervisor", "InstanceLock", "build_runtime", "serve", "make_handler", "offline_refusal", "default_home",
           "EXIT_OK", "EXIT_INSTANCE", "EXIT_SCHEMA", "EXIT_STARTUP", "LOCK_OFFSET", "OPTIONAL_MODULES", "db_lock_path", "probe_lock", "CLIENT_GONE",
           "code_tree", "already_running_message", "port_taken_message"]
