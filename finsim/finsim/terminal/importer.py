"""The historical import tool: batch validation, report / dry-run / apply, the data pack, backup and restore
(FINSIM1_PLAN package P5 and section 7; contracts C18 / C19; gaps TEST-01, OMS-04, PORTFOLIO-01).

An optional module (``server.OPTIONAL_MODULES["importer"]``): ``bind`` creates ``import_batches`` / ``import_lines`` (additive DDL
through ``Database.ensure_schema_ext``, D2), ``register`` adds the light commands ``import.record_inputs``, ``import.batch_record`` and
``import.line_record`` (actor ``import`` only), ``register_routes`` the read-only C19 routes.  It imports no other new module at load
time (``navhistory``, ``collateral``, ``blotters`` and ``server`` are imported inside the functions that need them), so its import,
``register`` and ``bind`` never fail while the rest of the build is unfinished (section 15.2 step 3).

Conventions (each one a rule the tests pin)
- THE BATCH (format_version 2; records ``batch``, ``setup``, ``line``, ``package``, ``catch_up``, ``summary``).  ``load_batch`` reads a
  JSONL file and ``validate_batch`` refuses it (BatchError, every problem listed) unless: one header first, every record of the same
  batch id; the setup steps S0-S4 (with S2b) present; line numbers 1..N; decision in preserve / import / declined / unresolved and
  equal to the header's lists and counts; ``idem_key`` = "imp:" + sha256(idem_canonical)[:24], recomputed; the canonical key
  ``account_id|product|instrument|side|qty|trade_date[|key terms]`` names the line's account, product (the master kind or OTC_TRS /
  OTC_CDX / OTC_IRS) and quantity, and its trade date is the line's key date (``key_trade_date`` / ``exchange_trade_date`` for a future,
  else the trade date, else the intended one); no price is in a key; an import line carries ONE command of an allowed kind with
  ``request_id`` = its idem key and ``as_of.line_key`` = its idem key, or names the package record whose key is "asofpkg:" +
  sha256(key_a + "|" + key_b)[:24] over its lines' keys in leg order; payloads carry machine fields only.  A ``historical_model``
  line books with ``as_of.historical_model`` true and the label "Historical model (not an independent execution)".  The batch's
  sha256 is recorded raw and over LF-normalised bytes (``content_sha256``): git may rewrite line endings in a checkout or an
  archive; the content hash is what identifies the batch (IMP-F1-20261027-A: 75690c19...).
- REPORT (read-only).  ``report`` opens ``<home>/terminal.db`` through a sqlite3 URI with ``mode=ro`` (never ``Database.open``, which
  backs up, migrates and writes ``meta``), so it writes nothing even beside a running server.  Per line it searches orders (cancelled
  ones included), fills (quantity, VWAP, trade dates, fees), lots, borrows, settlements, ``futures_settle``, ``otc_contracts`` by kind
  and key terms, and the ``import_key`` columns / ``import_lines`` by idem key.  Status: ``declined`` / ``unresolved`` from the decision
  (the search result is kept in ``match``); ``already_imported`` when a row carries the line's idem key; else for an import line
  ``missing_import`` when nothing matches (an existing matching execution is ``present_match`` / ``present_differs`` and blocks the
  booking: never a duplicate); for a preserve line ``present_match`` when quantity, side, key trade date and price (VWAP to 4 decimals)
  equal the workbook's, else ``present_differs`` (``missing_import`` if the execution vanished).  ``match_hash`` = sha256 over the
  identities of the matched rows and of the lifetime's orders, fills, lots, borrows, settlements, OTC contracts, transfers and
  ``nav_snapshots`` plus its journal's max entry id (never the global ``state_version``, which provider health moves).
- DATA PACK (``fetch_data_pack``; the only network step, on the apply day, read-only and gently: Yahoo with the browser user agent
  for Yahoo only, Treasury.gov / FRED with ``GOV_UA``, Cboe with the terminal's honest agent).  Window ``from`` .. L, L = the last
  business day before today: Yahoo daily bars of the TRS underlyings and the option contracts (with volume), the Cboe delayed chain
  rows of the contracts with ``prev_day_close`` and the chain's own timestamp (tied to L only when the chain quotes a session
  after L, i.e. fetched on today's date from 09:30 ET; otherwise L's Yahoo last trade is used and the option figures are estimates),
  FRED BAMLC0A0CM / BAMLH0A0HYM2 / SOFR (NY Fed for SOFR when FRED fails), UST_PAR curves from the database else Treasury.gov,
  dividend events of the underlyings.  Written to ``<home>/imports/<batch>/pack-<ts>.json`` with ``.sha256``.  The dry run and the
  apply read ONLY the pack and the database: the maintenance runtime runs on a provider that answers from the pack and refuses
  every other symbol (no network, no invented price).  The pack keeps the providers' numbers as fetched; S2 records each option
  price as printed (``clean_price``: Yahoo and Cboe send 32-bit floats, 4.52 as 4.51999998), the underlyings' closes on the cent.
- DRY RUN.  Refuses while ``<home>/terminal.lock`` (or the database's own lock) is held by a running terminal; takes a sqlite3-backup
  copy (source opened ``mode=ro``) into a NEW work folder (default ``<home>/imports/<batch>/dryrun-<ts>``) and records on the source
  first: the report (``match_hash``) and ``state_version`` / ``MAX(commands.id)``.  On the copy it builds a maintenance runtime
  (``server.build_runtime``; workers never started) and runs: S0 preflight (the commands registered; the pack window ends at L;
  every date's inputs under the jobs' rules; ex-dates recorded; deadline; ET clock before 16:00; no F1 snapshot dated today; no
  integrity hold and integrity full ok; every import line ``missing_import`` or ``already_imported``); S1 schema; S2 / S2b
  ``import.record_inputs`` then ``services.rates`` / ``services.curve`` reloaded; S3 chain rows; S4 ``cdx.terms`` basis derived from
  the pack (basis = dealt - half-spread x the PB's OTC spread multiplier - proxy of the trade date; skipped when equal, a 400
  NOTHING_TO_UPDATE counts as done); the OTC lines (24-28), then C1a / C2a / C3a (the catch-up, the as-of sweeps and the funding check
  below, run once for the contracts alone), then the listed lines (29-31) and the package, then C1 every lifecycle job except SNAPSHOT
  (``lifecycle.run_now`` per job in registry order) until a round completes nothing new; C2 ``cash.asof_sweep`` per business date and
  import account; C3 the business-date cash table (every account >= 0 at every close from the window start through the day before
  today, every current balance >= 0; a negative is moved by a dated ``cash.asof_transfer`` from the account with the most idle cash
  that date, and back on the first later date it is needed; none possible: refused).  WHY THE OTC PASS COMES FIRST: an as-of listed
  fill is funded on its trade date's business-date cash (C20), and a Treasury that is short on that date until the PB's receipts of
  the contracts (the CDX initial payments, the variation margin received) are swept back (C2) would refuse it.  On the owner's batch
  both orders give the same figures (US Exchange's own cash covers the options on 10-22; their settlement sweeps 436,835.22 on 10-23);
  the test world with a Treasury drained on 10-22 needs this order.  A listed booking still short on its date (409 INSUFFICIENT_FUNDS
  with the date and the shortfall) gets the shortfall moved into Treasury first by a dated ``cash.asof_transfer`` from the account with
  the most idle cash that date (C3's rule applied to the booking's own need) and is retried under the same request id; C4
  ``cash.reaccrue`` per changed Treasury / PB
  account and kind; C5 ``nav.restate`` of the snapshot dates recorded before the first write with one group per line, the
  ``funding`` group (this batch's as-of sweeps and transfers) and, only when the catch-up posted entries no line owns (the server
  was down), a ``catch_up`` group naming them; C5b SNAPSHOT; C6 integrity full, trial balance, ``PRAGMA wal_checkpoint(TRUNCATE)``.
  Request ids: lines = their idem keys, the package = its key, ``impset:<batch>:<step>[:<part>]``, ``impcu:<batch>:<step>:<part>``; a
  re-run replays them (a payload that legitimately changed runs under ``<id>:r<n>``).  The cutoff ``{accounting_day,
  due_keys_sha256, state_version, max_command_id}`` (due keys of every job except INTEGRITY_FULL and HOUSEKEEPING) is taken before
  the first write; ``now_ts`` is information only.  The report (``report.json`` / ``report.txt`` in the work folder): per line the
  created ids and money; every funding move; the business-date cash table of every account and date before the import, after the
  contracts' bookings and their lifecycle (before the as-of sweeps: the overdraft C2 resolves) and at the end; the restated NAV with
  its split (import effect by line) per account and for the lifetime; the bridge from the workbook's reconstructed NAV line by line
  (the reconciliation's estimates, then this run's interest, mark-policy and dating lines, then what they leave unexplained - shown,
  never forced); the cutoff, the pack sha256 and the match_hash.
- APPLY.  Needs ``--owner-go``, the locks (the importer holds ``terminal.lock`` and ``<db>.lock`` itself), the dry run's report (its
  folder, else the newest ``dryrun-*`` under ``<home>/imports/<batch>`` with the same pack), an ET clock before 16:00 and a date on or
  before the deadline; refuses unless the pack sha256, the ``match_hash`` and the four cutoff fields equal the dry run's; writes the
  pre-import backup with the sqlite3 backup API to ``<home>/backups/import/terminal-pre-<batch>-<ts>.db`` (outside the rotation of
  ``Database.backup``) and its sha256 (written and re-read) before the first write; runs the same steps on the live file; records
  ``import_batches`` (owner's words, backup, pack, cutoff, match hash, report) and ``import_lines`` (source_json, source_label - the
  label Completed Trades shows - and created refs); on a failure the batch is PARTIAL and a re-run continues idempotently; ends with
  ``wal_checkpoint(TRUNCATE)`` (no WAL content left).
- AN APPLIED BATCH RUN AGAIN (1.0.6; the owner's batch was applied on 2026-10-28).  A line the report finds ``already_imported`` is
  never sent again (1.0.5 re-sent it under its request id, and a later pack's moved Cboe chain row - part of an option booking's
  payload - turned that replay into a refusal, REQUEST_ID_REUSED).  When EVERY import line is already in the book the run only checks
  and reports (``verify_only``): S0 and S1 as always; the pack's inputs compared read-only (``inputs_compare``: values the book does
  not hold yet, such as a FRED value published after the apply, are counted and never recorded - the live system records them and
  posts its own pending variation margin); S4 compared, never set; no lifecycle job, sweep, transfer, re-accrual, restatement or
  snapshot; C3's table checked without moving cash (a negative makes the run DONE_WITH_PROBLEMS); C6 integrity and the trial
  balance.  So a dry run on a copy of the applied book, or a second apply, posts 0 entries, writes no restated version and sends
  no command at all: the batch's record (its lines, the apply's report, time, go and backup) stays as the apply wrote it, and the
  run's own report is in its work folder.  A run that does book (a PARTIAL apply resumed) refreshes on a line booked earlier only
  what its search found (status, match, differences); the line keeps its action, refs, label and time.
- THE BATCH VIEW (C19, ``GET /api/import/batches/{id}`` ``view``; ``components/imports.js`` shows it as sent): the batch's state and
  summary, every line in words (product, side, decision, what the run found, what it did, the rows it created with the terminal's
  ids), the business-date cash table and the funding moves with account NAMES (never raw account ids), the restated NAV of the
  book with its split (import effect by line naming the booked contract, mark policy by position, dating entries) and by account,
  the bridge to the workbook and the run's notes - all from the stored run (``import_batches.report_json``), nothing recomputed.
  ``GET /api/import/batches`` gives each batch its book and state; ``POST /api/import/report`` adds the words of each line's
  status and differences and the server's time of the check.
- COMMAND LINE: ``python -m finsim import ...`` (C18) and ``python -m finsim.terminal.importer ...`` take the same arguments.
- RESTORE (section 7): with no terminal running (locks free), move ``terminal.db`` / ``-wal`` / ``-shm`` into
  ``<home>/backups/failed-<ts>/``, copy the backup in, verify its sha256 and ``PRAGMA integrity_check``; then start FinSim (1.0.5 or
  later) and confirm.
- Nothing here writes ``%USERPROFILE%\\.finsim`` unless that folder is passed as ``--home`` (no default home in any mode).
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from . import config
from .clock import NY, RealClock, accounting_day, epoch_of, ny_of
from .commands import Command, RejectError, TxContext
from .db import canonical_json, dumps

__all__ = ["BATCH_ID", "EXPECTED_SHA256", "FORMAT_VERSION", "IMPORT_ACTOR", "SCHEMA", "Batch", "BatchError", "ImportRefused", "Pack", "load_batch",
           "validate_batch", "idem_key_of", "package_key_of", "report", "search_lines", "match_hash", "fetch_data_pack", "load_pack", "write_pack",
           "dry_run", "apply", "restore", "restore_procedure", "main", "bind", "register", "register_routes", "h_record_inputs", "h_batch_record",
           "h_line_record", "open_ro", "cutoff_fields", "due_keys", "PackProvider", "HISTORICAL_MODEL_LABEL", "clean_price", "bridge"]

# ---- constants -----------------------------------------------------------------------------------------------------------------
BATCH_ID = "IMP-F1-20261027-A"
#: sha256 of the owner's batch revision 3 (LF line endings, 121,468 bytes): FINSIM1_PLAN section 7 / 15.2 step 1
EXPECTED_SHA256 = {BATCH_ID: "75690c19327713c6648d20b710495f5ef0b5a924ed263748b3f74aa51bd11d2f"}
FORMAT_VERSION = 2
PACK_FORMAT = 1
IMPORT_ACTOR = "import"
CLOSE_ET = "16:00"
CHAIN_TIED_FROM_ET = "09:30"
RECORD_KINDS = ("batch", "setup", "line", "package", "catch_up", "summary")
SETUP_STEPS = ("S0", "S1", "S2", "S2b", "S3", "S4")
DECISIONS = ("preserve", "import", "declined", "unresolved")
STATUSES = ("present_match", "present_differs", "missing_import", "already_imported", "declined", "unresolved")
COMMAND_KINDS = ("import.record_inputs", "cdx.terms", "trade.book_as_of", "trade.book_as_of_package", "otc.book_as_of", "cash.asof_sweep",
                 "cash.asof_transfer", "cash.reaccrue", "nav.restate")
LISTED_PRODUCTS = ("EQUITY", "REIT", "ETF", "ADR", "PREFERRED", "FUTURE", "OPTION", "BOND")
OTC_PRODUCT_KIND = {"OTC_TRS": "TRS", "OTC_CDX": "CDX", "OTC_IRS": "IRS"}
PRODUCTS = LISTED_PRODUCTS + tuple(OTC_PRODUCT_KIND)
#: the commands S0 requires the running code to register (FINSIM1_PLAN 15.4, batch S0)
REQUIRED_COMMANDS = ("trade.book_as_of", "trade.book_as_of_package", "otc.book_as_of", "cdx.terms", "cash.asof_sweep", "cash.asof_transfer",
                     "cash.reaccrue", "nav.restate", "settlement.fail", "settlement.resolve", "import.record_inputs", "import.batch_record",
                     "import.line_record")
CUTOFF_EXCLUDED_JOBS = ("INTEGRITY_FULL", "HOUSEKEEPING")
ASOF_LISTED_KEYS = frozenset({"account_id", "instrument_id", "instrument_spec", "side", "qty", "price", "order_type", "tif", "fee_rule", "as_of"})
ASOF_PACKAGE_KEYS = frozenset({"account_id", "package", "legs", "order_type", "tif", "fee_rule", "as_of"})
ASOF_OTC_KEYS = frozenset({"account_id", "kind", "terms", "as_of"})
CHAIN_ROW_FIELDS = ("option", "bid", "ask", "bid_size", "ask_size", "last_trade_price", "last_trade_time", "prev_day_close", "volume", "open_interest", "iv",
                    "delta", "gamma", "theta", "vega", "theo", "open", "high", "low", "change")
HISTORICAL_MODEL_LABEL = "Historical model (not an independent execution)"
CATCH_UP_LABEL = "the book's own lifecycle caught up during the import (preserved positions; the server had not run it yet)"
FUNDING_LABEL = "as-of funding: the import's sweeps and transfers dated to their business dates"
CANONICAL_TRS_SIDE = {"PAY_TOTAL_RETURN": False, "RECEIVE_TOTAL_RETURN": True}
CURVE_SERIES = "UST_PAR"
OBS_TENOR = "OBS"
FRED_PACK_SERIES = ("BAMLC0A0CM", "BAMLH0A0HYM2", "SOFR")
OAS_SERIES = {"IG": "BAMLC0A0CM", "HY": "BAMLH0A0HYM2"}
SOFR_LAG_DAYS = 10
CURVE_LOOKBACK_BD = 3
MAX_CATCH_UP_ROUNDS = 12
MAX_FUNDING_MOVES = 40

IMPORT_BATCHES_DDL = ("CREATE TABLE IF NOT EXISTS import_batches (batch_id TEXT PRIMARY KEY, created_ts INTEGER NOT NULL, source_json TEXT NOT NULL, "
                      "status TEXT NOT NULL, mode_last TEXT, owner_go TEXT, backup_path TEXT, backup_sha256 TEXT, pack_path TEXT, pack_sha256 TEXT, "
                      "window_from TEXT, window_through TEXT, cutoff_json TEXT, match_hash TEXT, report_json TEXT, applied_ts INTEGER, command_id INTEGER)")
IMPORT_LINES_DDL = ("CREATE TABLE IF NOT EXISTS import_lines (batch_id TEXT NOT NULL, line_no INTEGER NOT NULL, idem_key TEXT UNIQUE, product TEXT NOT NULL, "
                    "instrument_id TEXT, side TEXT, qty TEXT, price TEXT, trade_date TEXT, value_date TEXT, exec_ts INTEGER, settle_date TEXT, account_id TEXT, "
                    "counterparty TEXT, booking TEXT, historical_model INTEGER NOT NULL DEFAULT 0, source_json TEXT NOT NULL, source_label TEXT NOT NULL, "
                    "status TEXT NOT NULL, action TEXT NOT NULL, match_json TEXT, diff_json TEXT, evidence_json TEXT, created_refs_json TEXT, command_id INTEGER, "
                    "applied_ts INTEGER, note TEXT, PRIMARY KEY (batch_id, line_no))")
SCHEMA = [IMPORT_BATCHES_DDL, IMPORT_LINES_DDL]
BATCH_COLUMNS = ("source_json", "status", "mode_last", "owner_go", "backup_path", "backup_sha256", "pack_path", "pack_sha256", "window_from", "window_through",
                 "cutoff_json", "match_hash", "report_json", "applied_ts")
LINE_COLUMNS = ("idem_key", "product", "instrument_id", "side", "qty", "price", "trade_date", "value_date", "exec_ts", "settle_date", "account_id",
                "counterparty", "booking", "historical_model", "source_json", "source_label", "status", "action", "match_json", "diff_json", "evidence_json",
                "created_refs_json", "applied_ts", "note")


class ImportRefused(Exception):
    """The import stops before (or at) a step and says why; nothing of a refused step is written."""

    def __init__(self, code: str, message: str, details: Optional[dict] = None, step: Optional[str] = None):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.details, self.step = code, message, dict(details or {}), step

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "details": self.details, "step": self.step}


class BatchError(ImportRefused):
    def __init__(self, problems: List[str], path: str = ""):
        super().__init__("BATCH_INVALID", f"the batch {path} is not valid: {len(problems)} problem(s): " + "; ".join(problems[:6]),
                         {"problems": list(problems)}, step="validate")
        self.problems = list(problems)


class Forbidden(RejectError):
    http = 403


# ---- small helpers ---------------------------------------------------------------------------------------------------------------
def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _dec(x) -> Optional[Decimal]:
    if x is None or isinstance(x, bool):
        return None
    try:
        d = Decimal(str(x).strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def _num_prefix(x) -> Optional[Decimal]:
    """The leading number of ``3000000 notional`` / ``5000000 face (target)`` / ``387``."""
    if isinstance(x, (int, Decimal)) and not isinstance(x, bool):
        return Decimal(x)
    s = str(x or "").strip().replace(",", "")
    num = ""
    for ch in s:
        if ch.isdigit() or ch in ".-":
            num += ch
        else:
            break
    return _dec(num) if num else None


def _iso(x) -> Optional[str]:
    s = str(x or "").strip()[:10]
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        return None


def _d(x) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x)[:10])


def _ny_text(ts: Optional[int]) -> str:
    return ny_of(int(ts)).strftime("%Y-%m-%d %H:%M ET") if ts else ""


def _money(c: Optional[int]) -> str:
    if c is None:
        return "-"
    sign = "-" if c < 0 else ""
    c = abs(int(c))
    return f"{sign}{c // 100:,}.{c % 100:02d}"


def _stamp(clock) -> str:
    return clock.ny().strftime("%Y%m%d-%H%M%S")


def _dates(a: date, b: date) -> List[date]:
    out, d = [], a
    while d <= b:
        out.append(d)
        d += timedelta(days=1)
    return out


def open_ro(path: str) -> sqlite3.Connection:
    """A read-only connection (sqlite3 URI ``mode=ro``): never ``Database.open`` (backup, migrate, meta writes)."""
    if not os.path.isfile(path):
        raise ImportRefused("NO_DATABASE", f"there is no database at {path}", {"path": path}, step="open")
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _cols(conn, table: str) -> set:
    try:
        return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
    except sqlite3.Error:
        return set()


def _meta(conn, key: str) -> Optional[str]:
    try:
        r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    except sqlite3.Error:
        return None
    return None if r is None else r[0]


class FileLog:
    """A logger writing to one file (never the global logging configuration)."""

    def __init__(self, path: Optional[str] = None):
        self.path = path
        self.records: List[str] = []

    def _add(self, level: str, msg: str, *args) -> None:
        try:
            text = msg % args if args else str(msg)
        except Exception:   # noqa: BLE001
            text = str(msg)
        line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {level} {text}"
        self.records.append(line)
        if self.path:
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass

    def debug(self, msg, *a, **k):
        pass

    def info(self, msg, *a, **k):
        self._add("INFO", msg, *a)

    def warning(self, msg, *a, **k):
        self._add("WARNING", msg, *a)

    def error(self, msg, *a, **k):
        self._add("ERROR", msg, *a)

    def exception(self, msg, *a, **k):
        import traceback
        self._add("EXCEPTION", str(msg) + "\n" + traceback.format_exc(), *a)


# ---- the batch -------------------------------------------------------------------------------------------------------------------
@dataclass
class Batch:
    path: str
    sha256: str
    content_sha256: str
    size: int
    records: List[dict]
    header: dict
    setups: Dict[str, dict]
    lines: Dict[int, dict]
    package: Optional[dict]
    catch_up: Optional[dict]
    summary: Optional[dict]

    @property
    def batch_id(self) -> str:
        return str(self.header.get("batch_id") or "")

    @property
    def lifetime(self) -> str:
        return str(self.header.get("lifetime") or "F1")

    @property
    def window_from(self) -> date:
        w = self.header.get("window") or {}
        got = _iso(w.get("from")) or _iso((self.setups.get("S0") or {}).get("window", {}).get("from"))
        return date.fromisoformat(got or "2026-10-21")

    @property
    def deadline(self) -> date:
        w = self.header.get("window") or {}
        got = _iso(w.get("deadline")) or _iso(((self.setups.get("S0") or {}).get("window") or {}).get("deadline"))
        return date.fromisoformat(got or "2026-11-19")

    def ordered(self) -> List[dict]:
        return [self.lines[n] for n in sorted(self.lines)]

    def expected_sha256(self) -> Optional[str]:
        return EXPECTED_SHA256.get(self.batch_id)


def idem_key_of(canonical: str) -> str:
    return "imp:" + hashlib.sha256(str(canonical).encode("utf-8")).hexdigest()[:24]


def package_key_of(keys: Sequence[str]) -> str:
    return "asofpkg:" + hashlib.sha256("|".join(keys).encode("utf-8")).hexdigest()[:24]


def key_trade_date(line: dict) -> Optional[str]:
    """The trade date a line's canonical key uses: a future's exchange trade date, else the trade date, else the intended one."""
    for k in ("key_trade_date", "exchange_trade_date"):
        got = _iso(line.get(k))
        if got:
            return got
    for k in ("trade_date", "intended_trade_date"):
        got = _iso(line.get(k))
        if got:
            return got
    return None


def load_batch(path: str, validate: bool = True) -> Batch:
    try:
        raw = Path(path).read_bytes()
    except OSError as e:
        raise ImportRefused("BATCH_UNREADABLE", f"the batch file {path} cannot be read: {e}", {"path": path}, step="load") from e
    problems: List[str] = []
    records: List[dict] = []
    for i, ln in enumerate(raw.decode("utf-8-sig").splitlines(), start=1):
        if not ln.strip():
            continue
        try:
            rec = json.loads(ln)
        except ValueError as e:
            problems.append(f"line {i} of the file is not JSON ({e})")
            continue
        if not isinstance(rec, dict):
            problems.append(f"line {i} of the file is not an object")
            continue
        records.append(rec)
    header = records[0] if records and records[0].get("record") == "batch" else {}
    setups, lines, package, catch_up, summary = {}, {}, None, None, None
    for rec in records:
        kind = rec.get("record")
        if kind == "setup":
            setups[str(rec.get("step"))] = rec
        elif kind == "line":
            try:
                n = int(rec.get("line_no"))
            except (TypeError, ValueError):
                problems.append(f"a line record has no line number: {str(rec)[:80]}")
                continue
            if n in lines:
                problems.append(f"line {n} appears twice")
            lines[n] = rec
        elif kind == "package":
            if package is not None:
                problems.append("more than one package record")
            package = rec
        elif kind == "catch_up":
            catch_up = rec
        elif kind == "summary":
            summary = rec
    b = Batch(str(path), _sha256_bytes(raw), _sha256_bytes(raw.replace(b"\r\n", b"\n")), len(raw), records, header, setups, lines, package, catch_up, summary)
    if validate:
        problems += validate_batch(b)
        if problems:
            raise BatchError(problems, str(path))
    return b


def validate_batch(b: Batch) -> List[str]:
    """Every problem of the batch (empty when valid) - module docstring THE BATCH."""
    p: List[str] = []
    if not b.records or b.records[0].get("record") != "batch":
        return ["the first record must be the batch header"]
    h = b.header
    if h.get("format_version") != FORMAT_VERSION:
        p.append(f"format_version must be {FORMAT_VERSION}, got {h.get('format_version')!r}")
    bid = b.batch_id
    if not bid:
        p.append("the header has no batch_id")
    for rec in b.records:
        if rec.get("record") not in RECORD_KINDS:
            p.append(f"unknown record kind {rec.get('record')!r}")
        if rec.get("batch_id") != bid:
            p.append(f"a {rec.get('record')} record names batch {rec.get('batch_id')!r}, not {bid!r}")
    if sum(1 for r in b.records if r.get("record") == "batch") != 1:
        p.append("exactly one batch header is allowed")
    for step in SETUP_STEPS:
        if step not in b.setups:
            p.append(f"the setup step {step} is missing")
    nums = sorted(b.lines)
    if not nums:
        p.append("the batch has no lines")
    elif nums != list(range(1, len(nums) + 1)):
        p.append(f"line numbers must run 1..{len(nums)} without gaps")
    by_decision: Dict[str, List[int]] = {d: [] for d in DECISIONS}
    seen_keys: Dict[str, int] = {}
    for n in nums:
        ln = b.lines[n]
        dec = ln.get("decision")
        if dec not in DECISIONS:
            p.append(f"line {n}: decision {dec!r} is not one of {', '.join(DECISIONS)}")
            continue
        by_decision[dec].append(n)
        key = ln.get("idem_key")
        canon = ln.get("idem_canonical")
        if key is None:
            if dec != "unresolved":
                p.append(f"line {n}: only an unresolved line (a balance, not a transaction) may have no idem key")
            continue
        if not isinstance(canon, str) or idem_key_of(canon) != key:
            p.append(f"line {n}: idem_key {key} is not 'imp:' + sha256(idem_canonical)[:24] ({idem_key_of(str(canon)) if canon else 'no canonical key'})")
        if key in seen_keys:
            p.append(f"line {n}: idem_key {key} repeats line {seen_keys[key]}")
        seen_keys[key] = n
        p += _canonical_problems(n, ln, canon)
        p += _command_problems(b, n, ln)
    hd = h.get("decisions") or {}
    for dec in DECISIONS:
        want = sorted(int(x) for x in (hd.get(dec) or []))
        if want != by_decision[dec]:
            p.append(f"the header lists {dec} lines {want}, the lines say {by_decision[dec]}")
    counts = h.get("decision_counts") or {}
    for dec in DECISIONS:
        if dec in counts and int(counts[dec]) != len(by_decision[dec]):
            p.append(f"decision_counts.{dec} = {counts[dec]}, the lines hold {len(by_decision[dec])}")
    if "total_lines" in counts and int(counts["total_lines"]) != len(nums):
        p.append(f"decision_counts.total_lines = {counts['total_lines']}, the batch holds {len(nums)} lines")
    if b.package is not None:
        p += _package_problems(b)
    for n in nums:
        ln = b.lines[n]
        if ln.get("decision") == "import" and not ln.get("command") and not ln.get("package_key"):
            p.append(f"line {n}: an import line carries a command or names the package")
    return p


def _canonical_problems(n: int, ln: dict, canon) -> List[str]:
    p: List[str] = []
    parts = str(canon or "").split("|")
    if len(parts) < 6:
        return [f"line {n}: the canonical key must be account|product|instrument|side|qty|trade_date[|key terms], got {canon!r}"]
    account, product, _inst, _side, qty, td = parts[:6]
    if account != ln.get("account_id"):
        p.append(f"line {n}: the canonical key names account {account}, the line {ln.get('account_id')}")
    if product not in PRODUCTS:
        p.append(f"line {n}: product {product} is not an instrument-master kind or OTC_TRS / OTC_CDX / OTC_IRS")
    if product != ln.get("product"):
        p.append(f"line {n}: the canonical key names product {product}, the line {ln.get('product')}")
    q = _dec(qty)
    if q is None or q != q.to_integral_value() or q <= 0:
        p.append(f"line {n}: the canonical quantity {qty!r} is not a whole positive number")
    elif _num_prefix(ln.get("qty")) not in (None, q):
        p.append(f"line {n}: the canonical quantity {qty} differs from the line's {ln.get('qty')!r}")
    want_td = key_trade_date(ln)
    if not _iso(td):
        p.append(f"line {n}: the canonical trade date {td!r} is not an ISO date")
    elif want_td and td != want_td:
        p.append(f"line {n}: the canonical trade date {td} is not the line's key trade date {want_td}")
    price = _dec(ln.get("intended_price"))
    if price is not None and any(_dec(x) == price and "." in x for x in parts[6:]):
        p.append(f"line {n}: a price is part of the canonical key (prices are never in the key)")
    return p


def _command_problems(b: Batch, n: int, ln: dict) -> List[str]:
    p: List[str] = []
    dec = ln.get("decision")
    cmd = ln.get("command")
    key = ln.get("idem_key")
    if dec != "import":
        if cmd:
            p.append(f"line {n}: a {dec} line carries no command")
        return p
    if ln.get("package_key"):
        if cmd:
            p.append(f"line {n}: a package leg carries no command of its own (the package record books it)")
        return p
    if not isinstance(cmd, dict):
        return [f"line {n}: an import line carries its command"]
    kind = cmd.get("kind")
    if kind not in COMMAND_KINDS:
        p.append(f"line {n}: command kind {kind!r} is not allowed ({', '.join(COMMAND_KINDS)})")
    if cmd.get("request_id") != key:
        p.append(f"line {n}: the command's request_id must be the line's idem key {key}")
    payload = cmd.get("payload")
    if not isinstance(payload, dict):
        return p + [f"line {n}: the command has no payload object"]
    allowed = ASOF_LISTED_KEYS if kind == "trade.book_as_of" else ASOF_OTC_KEYS if kind == "otc.book_as_of" else None
    if allowed is not None:
        extra = sorted(set(payload) - allowed)
        if extra:
            p.append(f"line {n}: the payload carries fields outside the machine set: {', '.join(extra)}")
        a = payload.get("as_of") or {}
        if a.get("line_key") != key:
            p.append(f"line {n}: as_of.line_key must be the idem key {key}")
        if a.get("batch_id") != b.batch_id:
            p.append(f"line {n}: as_of.batch_id must be {b.batch_id}")
        hm = bool(ln.get("historical_model"))
        if bool(a.get("historical_model", False)) != hm:
            p.append(f"line {n}: as_of.historical_model must equal the line's historical_model ({hm})")
        if payload.get("account_id") != ln.get("account_id"):
            p.append(f"line {n}: the payload's account {payload.get('account_id')} is not the line's {ln.get('account_id')}")
        label = str(ln.get("source_label") or "")
        if hm and not label.startswith(HISTORICAL_MODEL_LABEL):
            p.append(f"line {n}: a historical_model line is labelled '{HISTORICAL_MODEL_LABEL}'")
        if not hm and not label.startswith("As-of booking"):
            p.append(f"line {n}: an as-of line is labelled 'As-of booking - ...'")
    return p


def _package_problems(b: Batch) -> List[str]:
    p: List[str] = []
    pk = b.package or {}
    nums = [int(x) for x in (pk.get("lines") or [])]
    keys = []
    for n in nums:
        ln = b.lines.get(n)
        if ln is None:
            p.append(f"the package names line {n}, which does not exist")
            continue
        if ln.get("decision") != "import":
            p.append(f"the package names line {n}, which is not an import line")
        if ln.get("package_key") != pk.get("package_key"):
            p.append(f"line {n} does not name the package key {pk.get('package_key')}")
        keys.append(str(ln.get("idem_key")))
    if len(nums) < 2:
        p.append("a package books two legs or more")
    want = package_key_of(keys) if keys else None
    if pk.get("package_key") != want:
        p.append(f"the package key must be 'asofpkg:' + sha256(leg keys joined by '|')[:24] = {want}")
    cmd = pk.get("command") or {}
    if cmd.get("kind") != "trade.book_as_of_package":
        p.append("the package command is trade.book_as_of_package")
    if cmd.get("request_id") != pk.get("package_key"):
        p.append("the package command's request_id is the package key")
    payload = cmd.get("payload") or {}
    extra = sorted(set(payload) - ASOF_PACKAGE_KEYS)
    if extra:
        p.append(f"the package payload carries fields outside the machine set: {', '.join(extra)}")
    legs = payload.get("legs") or []
    if [str(l.get("line_key")) for l in legs] != keys:
        p.append("the package legs must carry their lines' idem keys in the listed order")
    if (payload.get("as_of") or {}).get("batch_id") != b.batch_id:
        p.append(f"the package's as_of.batch_id must be {b.batch_id}")
    return p


# ---- the report (read-only search) ------------------------------------------------------------------------------------------------
LISTED_SIDE_ALIASES = {"BUY": ("BUY",), "SELL": ("SELL",), "SELL_SHORT": ("SELL_SHORT", "SHORT"), "BTO": ("BTO",), "STO": ("STO",), "BTC": ("BTC",),
                       "STC": ("STC",)}


def _search_listed(conn, line: dict) -> dict:
    account = str(line.get("account_id") or "")
    iid = str(line.get("instrument_id") or "")
    side = str(line.get("side") or "").upper()
    sides = LISTED_SIDE_ALIASES.get(side, (side,))
    q = ",".join("?" for _ in sides)
    ocols, fcols = _cols(conn, "orders"), _cols(conn, "fills")
    oextra = "".join(f", {c}" for c in ("booking", "import_key", "replaced_by_order_id") if c in ocols)
    fextra = "".join(f", {c}" for c in ("booking", "import_key") if c in fcols)
    orders = [dict(r) for r in conn.execute(f"SELECT order_id, side, qty, filled_qty, status, reason, created_ts{oextra} FROM orders WHERE account_id=? AND"
                                            f" instrument_id=? AND side IN ({q}) ORDER BY created_ts, order_id", (account, iid, *sides)).fetchall()]
    oids = [o["order_id"] for o in orders]
    fsel = f"SELECT fill_id, order_id, qty, price, gross_c, fee_c, ts, trade_date, settle_date, origin{fextra} FROM fills"
    if oids:
        # a short sale's order is SELL_SHORT while its fills are SELL: the fills are found through their orders
        oq = ",".join("?" for _ in oids)
        fills = [dict(r) for r in conn.execute(f"{fsel} WHERE order_id IN ({oq}) ORDER BY ts, fill_id", oids).fetchall()]
    else:
        fsides = ("SELL_SHORT", "SELL") if side in ("SELL_SHORT", "SHORT") else sides
        fq = ",".join("?" for _ in fsides)
        fills = [dict(r) for r in conn.execute(f"{fsel} WHERE account_id=? AND instrument_id=? AND side IN ({fq}) AND order_id IS NULL ORDER BY ts, fill_id",
                                               (account, iid, *fsides)).fetchall()]
    qty = sum(int(f["qty"]) for f in fills)
    notional = sum(Decimal(str(f["price"])) * int(f["qty"]) for f in fills)
    vwap = (notional / qty).quantize(Decimal("0.000001")) if qty else None
    fill_ids = [f["fill_id"] for f in fills]
    settle: Dict[Tuple[str, str], int] = {}
    if fill_ids:
        fq = ",".join("?" for _ in fill_ids)
        for r in conn.execute(f"SELECT status, due_date FROM settlements WHERE fill_id IN ({fq})", fill_ids).fetchall():
            settle[(r["status"], r["due_date"])] = settle.get((r["status"], r["due_date"]), 0) + 1
    lots_open = None
    if fill_ids and _table_exists(conn, "lots"):
        fq = ",".join("?" for _ in fill_ids)
        row = conn.execute(f"SELECT COUNT(*) n, COALESCE(SUM(qty_remaining), 0) rem FROM lots WHERE open_fill_id IN ({fq})", fill_ids).fetchone()
        lots_open = {"lots": int(row["n"]), "qty_remaining": int(row["rem"])}
    borrows = []
    if side in ("SELL_SHORT", "SHORT"):
        borrows = [dict(r) for r in conn.execute("SELECT borrow_id, status, qty, qty_returned, lender FROM borrows WHERE account_id=? AND instrument_id=? ORDER BY borrow_id",
                                                 (account, iid)).fetchall()]
        for b in borrows:
            b["lender"] = _lender_words(b.get("lender"))
    vm = {}
    if line.get("product") == "FUTURE" and _table_exists(conn, "futures_settle"):
        vm = {r["settle_date"]: _money(int(r["vm_c"])) for r in conn.execute("SELECT settle_date, vm_c FROM futures_settle WHERE account_id=? AND instrument_id=?"
                                                                              " ORDER BY settle_date", (account, iid)).fetchall()}
    key = line.get("idem_key")
    hits = []
    if key:
        if "import_key" in ocols:
            hits += [("order", r[0]) for r in conn.execute("SELECT order_id FROM orders WHERE import_key=?", (key,)).fetchall()]
        if "import_key" in fcols:
            hits += [("fill", r[0]) for r in conn.execute("SELECT fill_id FROM fills WHERE import_key=?", (key,)).fetchall()]
    return {"order_ids": [o["order_id"] for o in orders], "orders": [{k: o.get(k) for k in ("order_id", "status", "qty", "filled_qty", "reason")} for o in orders],
            "fill_ids": fill_ids, "n_fills": len(fills), "filled_qty": qty, "vwap": None if vwap is None else str(vwap),
            "gross": _money(sum(int(f["gross_c"]) for f in fills)) if fills else None, "fees": _money(sum(int(f["fee_c"]) for f in fills)) if fills else None,
            "first_fill_et": ny_of(int(fills[0]["ts"])).strftime("%Y-%m-%d %H:%M:%S") if fills else None,
            "last_fill_et": ny_of(int(fills[-1]["ts"])).strftime("%Y-%m-%d %H:%M:%S") if fills else None,
            "trade_dates": sorted({f["trade_date"] for f in fills}), "settlement": [{"status": s, "due": d, "n": k} for (s, d), k in sorted(settle.items())],
            "lots": lots_open, "borrows": borrows, "futures_vm": vm, "import_key_hits": hits,
            "exchange_note": ("a CME future filled after 18:00 ET belongs to the next CME trade date" if line.get("product") == "FUTURE" else None)}


def _lender_words(lender) -> str:
    """A stored lender as shown (C16 ``lending.lender_label``: '... (modelled)' -> '... (assumed terms)'); stored rows are never changed."""
    try:
        from .lending import lender_label
        return str(lender_label(lender))
    except Exception:   # noqa: BLE001 - the same rule without the function
        s = str(lender or "")
        for tail in ("(modelled)", "(modeled)"):
            if s.endswith(tail):
                return s[: -len(tail)] + "(assumed terms)"
        return s


def _otc_terms_match(kind: str, line: dict, terms: dict, row: dict) -> bool:
    payload = ((line.get("command") or {}).get("payload") or {}).get("terms") or {}
    if kind == "TRS":
        return (str(terms.get("underlying_id") or "").upper() == str(payload.get("underlying_id") or "").upper()
                and _dec(terms.get("units")) == _dec(payload.get("units")) and bool(terms.get("receiver")) == bool(payload.get("receiver")))
    if kind == "CDX":
        want = _dec(payload.get("notional"))
        return (str(terms.get("index") or "").upper() == str(payload.get("index") or "").upper() and str(terms.get("side") or "").upper()
                == str(payload.get("side") or "").upper() and want is not None and int(row.get("notional_c") or 0) == int(want * 100))
    if kind == "IRS":
        want = _dec(payload.get("notional"))
        return (bool(terms.get("pay_fixed")) == bool(payload.get("pay_fixed")) and want is not None and int(row.get("notional_c") or 0) == int(want * 100)
                and _dec(terms.get("tenor_months")) == _dec(payload.get("tenor_months")))
    return False


def _search_otc(conn, line: dict) -> dict:
    kind = OTC_PRODUCT_KIND[line["product"]]
    account = str(line.get("account_id") or "")
    cols = _cols(conn, "otc_contracts")
    extra = "".join(f", {c}" for c in ("booking", "import_key", "trade_date", "value_date", "entered_ts") if c in cols)
    rows = [dict(r) for r in conn.execute(f"SELECT contract_id, kind, status, notional_c, start_date, terms_json, command_id{extra} FROM otc_contracts"
                                          " WHERE account_id=? AND kind=? ORDER BY contract_id", (account, kind)).fetchall()]
    found, hits = [], []
    for r in rows:
        try:
            terms = json.loads(r.get("terms_json") or "{}")
        except ValueError:
            terms = {}
        same = _otc_terms_match(kind, line, terms if isinstance(terms, dict) else {}, r)
        if r.get("import_key") and r.get("import_key") == line.get("idem_key"):
            hits.append(("otc_contract", r["contract_id"]))
        if same:
            found.append({"contract_id": r["contract_id"], "status": r["status"], "start_date": r["start_date"], "booking": r.get("booking"),
                          "import_key": r.get("import_key"), "command_id": r.get("command_id")})
    return {"otc_contracts": found, "n_contracts_of_kind": len(rows), "import_key_hits": hits}


def _search_bond(conn, line: dict) -> dict:
    account = str(line.get("account_id") or "")
    rows = [dict(r) for r in conn.execute("SELECT l.instrument_id, SUM(l.qty_remaining) qty FROM lots l JOIN instruments i ON i.instrument_id=l.instrument_id"
                                          " WHERE l.account_id=? AND i.kind='BOND' AND l.qty_remaining<>0 GROUP BY l.instrument_id", (account,)).fetchall()]
    return {"bond_positions": rows, "import_key_hits": []}


def search_line(conn, line: dict) -> dict:
    product = line.get("product")
    if product in OTC_PRODUCT_KIND:
        m = _search_otc(conn, line)
    elif product == "BOND":
        m = _search_bond(conn, line)
    elif product in LISTED_PRODUCTS:
        m = _search_listed(conn, line)
    else:
        m = {"import_key_hits": []}
    if line.get("idem_key") and _table_exists(conn, "import_lines"):
        r = conn.execute("SELECT batch_id, line_no, status, action, applied_ts FROM import_lines WHERE idem_key=?", (line["idem_key"],)).fetchone()
        m["import_line"] = None if r is None else dict(r)
    return m


def line_status(line: dict, m: dict) -> Tuple[str, List[dict]]:
    """``(status, diffs)`` of a line from its search result (module docstring REPORT)."""
    dec = line.get("decision")
    if dec == "declined":
        return "declined", []
    if dec == "unresolved":
        return "unresolved", []
    il = m.get("import_line") or {}
    if m.get("import_key_hits") or (il and il.get("action") in ("booked", "already_imported") and il.get("applied_ts")):
        return "already_imported", []
    product = line.get("product")
    if dec == "import":
        if product in OTC_PRODUCT_KIND:
            if m.get("otc_contracts"):
                return "present_differs", [{"field": "existing_contract", "app": [c["contract_id"] for c in m["otc_contracts"]],
                                            "note": "a contract with these key terms already exists: booking the line would duplicate it"}]
            return "missing_import", []
        if m.get("filled_qty"):
            return "present_differs", [{"field": "existing_fills", "app": m.get("fill_ids"), "note": "fills of this instrument and side already exist"}]
        return "missing_import", []
    # preserve
    if product in OTC_PRODUCT_KIND:
        return ("present_match" if m.get("otc_contracts") else "missing_import"), []
    if not m.get("filled_qty"):
        return "missing_import", [{"field": "execution", "note": "the batch expects an existing execution and none was found"}]
    diffs: List[dict] = []
    want_qty = _num_prefix(line.get("qty"))
    if want_qty is not None and Decimal(m["filled_qty"]) != want_qty:
        diffs.append({"field": "qty", "app": str(m["filled_qty"]), "workbook": str(want_qty)})
    ktd = key_trade_date(line)
    if ktd and m.get("trade_dates") and m["trade_dates"] != [ktd]:
        diffs.append({"field": "trade_date", "app": ",".join(m["trade_dates"]), "workbook": ktd})
    px = _dec(line.get("intended_price"))
    vwap = _dec(m.get("vwap"))
    if px is not None and vwap is not None and vwap.quantize(Decimal("0.0001")) != px.quantize(Decimal("0.0001")):
        diffs.append({"field": "price", "app": str(vwap.quantize(Decimal("0.0001"))), "workbook": str(line.get("intended_price")),
                      "delta_per_unit": str((vwap - px).quantize(Decimal("0.0001")))})
    expect = line.get("match") or {}
    want_fills = expect.get("fill_ids")
    if isinstance(want_fills, list) and want_fills and sorted(want_fills) != sorted(m.get("fill_ids") or []):
        diffs.append({"field": "drift", "note": "the fills differ from the ones the reconciliation matched (reported, never corrected)",
                      "batch": len(want_fills), "app": len(m.get("fill_ids") or [])})
    if line.get("intended_trade_date") and ktd and _iso(line.get("intended_trade_date")) != ktd:
        diffs.append({"field": "trade_date_convention", "app": ktd, "workbook": _iso(line.get("intended_trade_date")),
                      "note": "the same execution: a CME evening fill belongs to the next CME trade date (reported, not corrected)"})
    status = "present_differs" if any(d["field"] in ("qty", "trade_date", "price", "drift") for d in diffs) else "present_match"
    return status, diffs


IDENTITY_SQL = (
    ("orders", "SELECT order_id, status, filled_qty FROM orders WHERE account_id IN ({q}) ORDER BY order_id"),
    ("fills", "SELECT fill_id, qty, price, entry_id FROM fills WHERE account_id IN ({q}) ORDER BY fill_id"),
    ("lots", "SELECT lot_id, qty_remaining FROM lots WHERE account_id IN ({q}) ORDER BY lot_id"),
    ("borrows", "SELECT borrow_id, status, qty, qty_returned FROM borrows WHERE account_id IN ({q}) ORDER BY borrow_id"),
    ("settlements", "SELECT settle_id, status, amount_c FROM settlements WHERE account_id IN ({q}) ORDER BY settle_id"),
    ("otc_contracts", "SELECT contract_id, status, notional_c FROM otc_contracts WHERE account_id IN ({q}) ORDER BY contract_id"),
    ("transfers", "SELECT transfer_id, amount_c, business_date FROM transfers WHERE from_account IN ({q}) OR to_account IN ({q}) ORDER BY transfer_id"),
    ("nav_snapshots", "SELECT account_id, snap_date, nav_c FROM nav_snapshots WHERE account_id IN ({q}) ORDER BY account_id, snap_date"),
)


def match_hash(conn, lifetime: str, results: List[dict]) -> str:
    """sha256 over the matched rows' identities and the lifetime's rows the import reads (module docstring REPORT)."""
    accounts = [r[0] for r in conn.execute("SELECT account_id FROM accounts WHERE lifetime_id=? ORDER BY account_id", (lifetime,)).fetchall()]
    scoped = accounts + [f"{lifetime}:ALL"]
    h = hashlib.sha256()
    ident = [[r["line_no"], r["status"], sorted(r["match"].get("order_ids") or []), sorted(r["match"].get("fill_ids") or []),
              sorted(c["contract_id"] for c in (r["match"].get("otc_contracts") or []))] for r in results]
    h.update(canonical_json(ident).encode("utf-8"))
    for table, sql in IDENTITY_SQL:
        if not _table_exists(conn, table):
            continue
        ids = scoped if table == "nav_snapshots" else accounts
        q = ",".join("?" for _ in ids)
        params = ids + ids if sql.count("{q}") == 2 else ids
        rows = conn.execute(sql.format(q=q), params).fetchall()
        h.update(table.encode("utf-8"))
        h.update(canonical_json([list(r) for r in rows]).encode("utf-8"))
    q = ",".join("?" for _ in accounts)
    row = conn.execute(f"SELECT MAX(l.entry_id) FROM journal_lines l WHERE l.account_id IN ({q})", accounts).fetchone() if accounts else None
    h.update(f"max_entry:{row[0] if row else None}".encode("utf-8"))
    return h.hexdigest()


def search_lines(conn, batch: Batch) -> List[dict]:
    out = []
    for line in batch.ordered():
        m = search_line(conn, line)
        status, diffs = line_status(line, m)
        out.append({"line_no": int(line["line_no"]), "idem_key": line.get("idem_key"), "product": line.get("product"),
                    "instrument_id": line.get("instrument_id"), "account_id": line.get("account_id"), "side": line.get("side"), "qty": line.get("qty"),
                    "intended_price": line.get("intended_price"), "decision": line.get("decision"), "status": status, "batch_status": line.get("match_status"),
                    "match": m, "diffs": diffs, "batch_diffs": line.get("diffs"), "source_label": line.get("source_label"),
                    "product_label": line.get("product_label")})
    return out


def report_on(conn, batch: Batch) -> dict:
    results = search_lines(conn, batch)
    counts: Dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"batch_id": batch.batch_id, "batch_sha256": batch.sha256, "batch_content_sha256": batch.content_sha256,
            "batch_expected_sha256": batch.expected_sha256(), "lines": results, "counts": counts, "match_hash": match_hash(conn, batch.lifetime, results),
            "state_version": _meta(conn, "state_version"), "max_command_id": conn.execute("SELECT MAX(id) FROM commands").fetchone()[0],
            "app_version_last_opened": _meta(conn, "app_version_last_opened")}


def report(batch_path: str, home: str, out: Optional[Callable[[str], None]] = print, json_path: Optional[str] = None) -> dict:
    """Report mode: read-only, at any time (module docstring REPORT)."""
    batch = load_batch(batch_path)
    db_path = os.path.join(home, "terminal.db")
    conn = open_ro(db_path)
    try:
        rep = report_on(conn, batch)
    finally:
        conn.close()
    rep.update(mode="report", home=home, db_path=db_path, generated_et=datetime.now(NY).strftime("%Y-%m-%d %H:%M:%S ET"))
    if json_path:
        Path(json_path).write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    if out is not None:
        for ln in report_text(rep):
            out(ln)
    return rep


def report_text(rep: dict) -> List[str]:
    out = [f"Batch {rep['batch_id']} - report (read-only) on {rep.get('db_path', '')}",
           f"batch sha256 {rep['batch_sha256']} (content {rep['batch_content_sha256']}; expected {rep.get('batch_expected_sha256') or 'n/a'})",
           f"{'line':>4}  {'product':<9} {'instrument':<24} {'side':<12} {'qty':>14} {'intended':>12} {'app qty':>9} {'app vwap':>12}  {'status':<17} decision"]
    for r in rep["lines"]:
        m = r["match"]
        inst = str(r.get("instrument_id") or "")[:24]
        side = str(r.get("side") or "")[:12]
        out.append(f"{r['line_no']:>4}  {str(r.get('product') or ''):<9} {inst:<24} {side:<12} {str(r.get('qty') or ''):>14.14} {str(r.get('intended_price') or ''):>12.12}"
                   f" {str(m.get('filled_qty') if m.get('filled_qty') is not None else ''):>9} {str(m.get('vwap') or ''):>12}  {r['status']:<17} {r['decision']}")
        for d in r.get("diffs") or []:
            out.append(f"{'':>6}diff {_diff_text(d)}")
    out.append("counts: " + ", ".join(f"{k} {v}" for k, v in sorted(rep["counts"].items())))
    out.append(f"match_hash {rep['match_hash']}; state_version {rep.get('state_version')}; max command id {rep.get('max_command_id')}")
    return out


# ---- the data pack -------------------------------------------------------------------------------------------------------------
def _underlyings(batch: Batch) -> List[str]:
    out = []
    for ln in batch.ordered():
        if ln.get("decision") == "import" and ln.get("product") == "OTC_TRS":
            u = ((ln.get("command") or {}).get("payload") or {}).get("terms", {}).get("underlying_id")
            if u and u not in out:
                out.append(str(u))
    return out


def _option_specs(batch: Batch) -> Dict[str, dict]:
    """occ -> {spec, trade_date, line_no} of every option the batch books (single lines and package legs)."""
    out: Dict[str, dict] = {}
    for ln in batch.ordered():
        if ln.get("decision") != "import" or ln.get("product") != "OPTION":
            continue
        payload = (ln.get("command") or {}).get("payload") or {}
        spec = payload.get("instrument_spec") or {}
        td = _iso(ln.get("trade_date")) or _iso((payload.get("as_of") or {}).get("trade_date"))
        if not spec and batch.package is not None:
            for leg in ((batch.package.get("command") or {}).get("payload") or {}).get("legs") or []:
                if leg.get("line_key") == ln.get("idem_key"):
                    spec = leg.get("instrument_spec") or {}
                    td = td or _iso((((batch.package.get("command") or {}).get("payload") or {}).get("as_of") or {}).get("trade_date"))
        occ = str(spec.get("occ") or ln.get("instrument_id") or "")
        if occ:
            out[occ] = {"spec": spec, "trade_date": td, "line_no": int(ln["line_no"]), "underlying_id": spec.get("underlying_id")}
    return out


def clean_price(x) -> Optional[str]:
    """A provider's float-encoded option price as the decimal it stands for.  Yahoo's option daily bars and Cboe's delayed chain publish
    prices as 32-bit floats: 4.52 arrives as 4.51999998, 3.325 as 3.32500004768372, 20.04 as 20.04000092, and a Cboe closing mid
    computed from two of them as 11.6100001335144 ((11.55 + 11.67) / 2).  When the given digits are exactly a 32-bit float's own
    expansion, the shortest decimal with the same 32-bit float is the printed price (4.52, 3.325, 20.04); otherwise a value within 32-bit
    float noise (2^-21 relative) of its 4-decimal rounding is that rounding (11.61; option prices and closing mids carry at most 4
    decimals).  Any other value (a genuine decimal such as 0.123456789012) is kept as given.  None stays None."""
    import struct
    d = _dec(x)
    if d is None:
        return None
    try:
        f = float(d)
        t = struct.unpack("<f", struct.pack("<f", f))[0]
    except (OverflowError, ValueError, struct.error):
        return str(d)
    def canon(c: Decimal) -> str:
        return str(c.quantize(Decimal(1)) if c == c.to_integral_value() else c.normalize())
    digits = len(d.normalize().as_tuple().digits)
    if t != 0 and _dec(format(t, f".{digits}g")) == d:
        for n in range(1, 10):
            cand = format(t, f".{n}g")
            if struct.unpack("<f", struct.pack("<f", float(cand)))[0] == t:
                return canon(Decimal(cand))
    q = d.quantize(Decimal("0.0001"))
    if d != 0 and abs(d - q) <= abs(d) * Decimal(2) ** -21:
        return canon(q)
    return str(d)


def _bars_dict(bars, lo: date, hi: date) -> Dict[str, dict]:
    out = {}
    for b in bars:
        if lo <= b.date <= hi and b.c is not None:
            out[b.date.isoformat()] = {"close": str(b.c), "volume": None if b.v is None else int(b.v), "open": None if b.o is None else str(b.o),
                                       "high": None if b.h is None else str(b.h), "low": None if b.l is None else str(b.l)}
    return out


def _treasury_curves(raw: bytes) -> Dict[str, Dict[str, str]]:
    """Every dated entry of a Treasury.gov par-yield-curve month feed: ``{date: {tenor label: percent}}``."""
    import xml.etree.ElementTree as ET
    from .quotes.parsers import TENORS
    root = ET.fromstring(raw)
    out: Dict[str, Dict[str, str]] = {}
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] != "properties":
            continue
        fields = {ch.tag.rsplit("}", 1)[-1]: (ch.text or "").strip() for ch in el}
        d = _iso(fields.get("NEW_DATE"))
        if d is None:
            continue
        pts = {}
        for xml_field, label, _y in TENORS:
            v = _dec(fields.get(xml_field))
            if v is not None:
                pts[label] = str(v.quantize(Decimal("0.0001")).normalize())
        if len(pts) >= 6:
            out[d] = pts
    return out


def fetch_data_pack(batch_path: str, home: str, clock=None, getter=None, sleep=None, out: Optional[Callable[[str], None]] = print) -> dict:
    """``--fetch-data-pack`` (module docstring DATA PACK): the only network step.  Returns ``{path, sha256, pack}``."""
    from .calendar import TradingCalendar
    from .quotes.http import FetchError, ProviderHealth, http_get
    from .quotes.parsers import et_naive_to_epoch
    from .quotes.providers import CBOE_URL, TREASURY_URL, FredSeries, NyFed, YahooChart
    batch = load_batch(batch_path)
    clock = clock or RealClock()
    cal = TradingCalendar(config.holidays_path())
    today = clock.today()
    L = cal.prev_business_day(today)
    lo = batch.window_from
    if L < lo:
        raise ImportRefused("WINDOW", f"the last business day before today ({L}) is before the window start {lo}", step="fetch")
    health = ProviderHealth(lambda: int(clock.epoch()), day=lambda: clock.today().isoformat())
    kw = {"health": health}
    if getter is not None:
        kw["getter"] = getter
    if sleep is not None:
        kw["sleep"] = sleep
    get = getter or (lambda url, ua, timeout, accept: http_get(url, ua, timeout=timeout, accept=accept))
    yahoo = YahooChart(clock, **kw)
    fred = FredSeries(clock, **kw)
    notes: List[str] = []
    errors: List[str] = []
    days = (today - lo).days + 12
    pack: dict = {"pack_format": PACK_FORMAT, "batch_id": batch.batch_id, "batch_sha256": batch.sha256, "batch_content_sha256": batch.content_sha256,
                  "fetched_ts": int(clock.epoch()), "fetched_et": clock.ny().strftime("%Y-%m-%d %H:%M:%S ET"), "today": today.isoformat(),
                  "window": {"from": lo.isoformat(), "through": L.isoformat()}, "closes": {}, "options": {}, "fred": {}, "fred_sources": {}, "curves": {},
                  "curve_sources": {}, "dividends": {}, "notes": notes, "errors": errors}
    for sym in _underlyings(batch):
        try:
            bars = yahoo.daily(sym, max(days, 23))
            pack["closes"][sym] = {"source": "Yahoo Finance daily bar (observed close)", "fetched_ts": int(clock.epoch()),
                                   "bars": _bars_dict(bars, lo - timedelta(days=7), L)}
        except FetchError as e:
            errors.append(f"Yahoo daily {sym}: {e}")
        got = (pack["closes"].get(sym) or {}).get("bars")
        if got is not None and L.isoformat() not in got:
            # POSTCLOSE-01: until Yahoo finalises L's daily bar (its close reads null for some hours), L's official close is the chart's
            # regularMarketPrice when it was printed on L at or after the 16:00 ET close - never an earlier print
            try:
                m = yahoo.meta(sym)
                px, t = m.get("regularMarketPrice"), m.get("regularMarketTime")
                if px is not None and t and ny_of(int(t)).date() == L and ny_of(int(t)).strftime("%H:%M") >= CLOSE_ET:
                    got[L.isoformat()] = {"close": str(Decimal(str(px)).quantize(Decimal("0.01"))), "volume": m.get("regularMarketVolume"), "open": None,
                                          "high": None, "low": None, "method": "official_close",
                                          "source": f"Yahoo regularMarketPrice (the official close, printed {ny_of(int(t)).strftime('%H:%M:%S')} ET)"}
                    notes.append(f"{sym}: L's daily bar has no close yet; the official close {got[L.isoformat()]['close']} is the chart's regularMarketPrice")
            except FetchError as e:
                errors.append(f"Yahoo meta {sym}: {e}")
        try:
            ev = yahoo.events(sym)
            pack["dividends"][sym] = [[d.ex_date.isoformat(), str(d.amount)] for d in ev.dividends]
        except FetchError as e:
            errors.append(f"Yahoo events {sym}: {e}")
    specs = _option_specs(batch)
    chains: Dict[str, Tuple[Optional[dict], Optional[int]]] = {}
    for occ, info in specs.items():
        u = str(info.get("underlying_id") or "")
        entry = {"trade_date": info.get("trade_date"), "underlying_id": u, "line_no": info.get("line_no")}
        try:
            bars = yahoo.daily(occ, max(days, 23))
            entry["daily"] = {"source": "Yahoo Finance option daily bar (last trade)", "fetched_ts": int(clock.epoch()),
                              "bars": _bars_dict(bars, lo - timedelta(days=7), L)}
        except FetchError as e:
            errors.append(f"Yahoo daily {occ}: {e}")
        if u not in chains:
            try:
                raw = get(CBOE_URL.format(sym=u), config.TERMINAL_UA, 60.0, "application/json")
                payload = json.loads(raw.decode("utf-8", "replace"))
                data = payload.get("data") if isinstance(payload, dict) else None
                ts = et_naive_to_epoch(payload.get("timestamp")) if isinstance(payload, dict) and payload.get("timestamp") else None
                if ts is None and isinstance(data, dict):
                    ts = et_naive_to_epoch(data.get("last_trade_time"))
                rows = {str(o.get("option")): o for o in ((data or {}).get("options") or []) if isinstance(o, dict) and o.get("option") in specs}
                chains[u] = (rows, ts)
                del payload, raw
            except (FetchError, ValueError) as e:
                errors.append(f"Cboe chain {u}: {e}")
                chains[u] = (None, None)
            if sleep is None and getter is None:
                _time.sleep(1.0)                       # Cboe: 1 request per second
        rows, ts = chains.get(u, (None, None))
        row = (rows or {}).get(occ)
        if row is not None:
            chain_row = {k: (None if row.get(k) is None else str(row.get(k))) for k in CHAIN_ROW_FIELDS if k in row}
            chain_row["option"] = occ
            chain_row["source"] = "Cboe delayed quotes"
            chain_row["chain_ts"] = ts
            entry["chain_row"] = chain_row
            tied = ts is not None and ny_of(ts).date() == today and ny_of(ts).strftime("%H:%M") >= CHAIN_TIED_FROM_ET
            pc = _dec(row.get("prev_day_close"))
            entry["official_close"] = {"date": L.isoformat(), "price": None if pc is None else str(pc), "tied_to_L": bool(tied and pc is not None),
                                       "chain_ts": ts, "source": "Cboe delayed quotes prev_close (official close)",
                                       "note": ("the chain quotes the session after L: its previous close is L's official close" if tied else
                                                "the chain does not quote a session after L (fetched before 09:30 ET or on another day): L's Yahoo last trade"
                                                " is used, labelled; the option figures are estimates")}
        else:
            errors.append(f"Cboe chain row {occ}: not found")
        pack["options"][occ] = entry
    look = (today - lo).days + 30
    for sid in FRED_PACK_SERIES:
        try:
            obs = fred.csv(sid, look)
            pack["fred"][sid] = [[d.isoformat(), str(v)] for d, v in obs if d >= lo - timedelta(days=20) and d <= L]
            pack["fred_sources"][sid] = "FRED"
        except FetchError as e:
            if sid == "SOFR":
                try:
                    obs = NyFed(clock, **kw).sofr(30)
                    pack["fred"][sid] = [[d.isoformat(), str(v)] for d, v in obs if d >= lo - timedelta(days=20) and d <= L]
                    pack["fred_sources"][sid] = "NY Fed (markets.newyorkfed.org): SOFR as published by its administrator"
                    notes.append(f"FRED SOFR failed ({e}); SOFR from the NY Fed")
                    continue
                except FetchError as e2:
                    errors.append(f"NY Fed SOFR: {e2}")
            errors.append(f"FRED {sid}: {e}")
        if sleep is None and getter is None:
            _time.sleep(1.0)
    db_path = os.path.join(home, "terminal.db")
    have: Dict[str, Dict[str, str]] = {}
    if os.path.isfile(db_path):
        conn = open_ro(db_path)
        try:
            for r in conn.execute("SELECT as_of, tenor, value FROM curve_points WHERE series=? AND as_of>=? AND as_of<=?",
                                  (CURVE_SERIES, (lo - timedelta(days=7)).isoformat(), L.isoformat())).fetchall():
                have.setdefault(r["as_of"], {})[r["tenor"]] = r["value"]
        finally:
            conn.close()
    missing = [d for d in _dates(lo, L) if cal.is_business_day(d) and d.isoformat() not in have]
    fetched: Dict[str, Dict[str, str]] = {}
    for yyyymm in sorted({d.strftime("%Y%m") for d in missing}):
        try:
            raw = get(TREASURY_URL.format(yyyymm=yyyymm), config.GOV_UA, 45.0, "application/xml, text/xml")
            fetched.update(_treasury_curves(raw))
        except Exception as e:   # noqa: BLE001 - a missing curve is reported; S0 decides
            errors.append(f"Treasury.gov {yyyymm}: {type(e).__name__}: {e}")
    for ds, pts in sorted(have.items()):
        pack["curves"][ds] = pts
        pack["curve_sources"][ds] = "database (treasury.gov daily par yield curve)"
    for d in missing:
        pts = fetched.get(d.isoformat())
        if pts:
            pack["curves"][d.isoformat()] = pts
            pack["curve_sources"][d.isoformat()] = "treasury.gov daily par yield curve (fetched for the pack)"
    path = write_pack(home, batch.batch_id, pack, clock)
    sha = sha256_file(path)
    if out is not None:
        out(f"data pack {path}")
        out(f"sha256 {sha}")
        out(f"window {lo} .. {L} (L = the last business day before {today})")
        for e in errors:
            out(f"  not fetched: {e}")
    return {"path": path, "sha256": sha, "pack": pack}


def write_pack(home_or_path: str, batch_id: Optional[str], pack: dict, clock=None) -> str:
    """Write a pack as ``<home>/imports/<batch>/pack-<ts>.json`` (or to the given ``.json`` path) with its ``.sha256``."""
    if str(home_or_path).lower().endswith(".json"):
        path = str(home_or_path)
    else:
        folder = os.path.join(home_or_path, "imports", str(batch_id))
        os.makedirs(folder, exist_ok=True)
        stamp = _stamp(clock or RealClock())
        path = os.path.join(folder, f"pack-{stamp}.json")
        n = 1
        while os.path.exists(path):
            path = os.path.join(folder, f"pack-{stamp}-{n}.json")
            n += 1
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    data = json.dumps(pack, indent=1, sort_keys=True).encode("utf-8")
    with open(path, "wb") as f:
        f.write(data)
    with open(path + ".sha256", "w", encoding="ascii") as f:
        f.write(_sha256_bytes(data) + "\n")
    return path


@dataclass
class Pack:
    path: str
    sha256: str
    data: dict

    @property
    def window_from(self) -> date:
        return date.fromisoformat(self.data["window"]["from"])

    @property
    def through(self) -> date:
        return date.fromisoformat(self.data["window"]["through"])

    def closes(self, sym: str) -> Dict[str, dict]:
        return ((self.data.get("closes") or {}).get(sym) or {}).get("bars") or {}

    def option(self, occ: str) -> dict:
        return (self.data.get("options") or {}).get(occ) or {}

    def fred(self, sid: str) -> List[Tuple[date, Decimal]]:
        return [(date.fromisoformat(d), Decimal(v)) for d, v in (self.data.get("fred") or {}).get(sid) or []]

    def curves(self) -> Dict[str, Dict[str, str]]:
        return dict(self.data.get("curves") or {})

    def dividends(self, sym: str) -> List[Tuple[str, str]]:
        return [(str(d), str(a)) for d, a in (self.data.get("dividends") or {}).get(sym) or []]


def load_pack(path: str) -> Pack:
    try:
        raw = Path(path).read_bytes()
    except OSError as e:
        raise ImportRefused("PACK_UNREADABLE", f"the data pack {path} cannot be read: {e}", {"path": path}, step="S0") from e
    sha = _sha256_bytes(raw)
    side = Path(str(path) + ".sha256")
    if side.is_file():
        want = side.read_text(encoding="ascii", errors="replace").strip().split()[0] if side.read_text(encoding="ascii", errors="replace").strip() else ""
        if want and want != sha:
            raise ImportRefused("PACK_CHANGED", f"the data pack {path} no longer matches its recorded sha256 ({want[:12]}..., now {sha[:12]}...)",
                                {"recorded": want, "now": sha}, step="S0")
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as e:
        raise ImportRefused("PACK_INVALID", f"the data pack {path} is not JSON: {e}", step="S0") from e
    if not isinstance(data, dict) or data.get("pack_format") != PACK_FORMAT or not isinstance(data.get("window"), dict):
        raise ImportRefused("PACK_INVALID", f"the data pack {path} is not a format-{PACK_FORMAT} pack", step="S0")
    return Pack(str(path), sha, data)


# ---- the pack as a provider (no network in the dry run and the apply) --------------------------------------------------------
def _pack_provider_class():
    from .quotes.http import FetchError
    from .quotes.providers import FakeProvider

    class _PackProvider(FakeProvider):
        """Answers ONLY from the data pack: daily bars and dividend events of its symbols; every other symbol is refused (no network,
        no invented price), so a job that needs another input waits for the live scheduler."""
        name = "import data pack"

        def daily(self, symbol, days: int = 10):
            with self._lock:
                known = symbol in self._daily
            if not known:
                raise FetchError(f"{symbol}: not in the import's data pack (the dry run and the apply never use the network)", status=None, retryable=False)
            return super().daily(symbol, days)

        def intraday(self, symbol):
            raise FetchError(f"{symbol}: no intraday quotes in the import's data pack", status=None, retryable=False)

    return _PackProvider


def PackProvider(pack: Pack, clock):
    cls = _pack_provider_class()
    fp = cls(clock)
    for sym, entry in (pack.data.get("closes") or {}).items():
        bars = entry.get("bars") or {}
        fp.set_daily(sym, [(d, b.get("open") or b["close"], b.get("high") or b["close"], b.get("low") or b["close"], b["close"], b.get("volume"))
                           for d, b in sorted(bars.items())])
    for occ, entry in (pack.data.get("options") or {}).items():
        bars = ((entry.get("daily") or {}).get("bars")) or {}
        if bars:
            fp.set_daily(occ, [(d, b.get("open") or b["close"], b.get("high") or b["close"], b.get("low") or b["close"], b["close"], b.get("volume"))
                               for d, b in sorted(bars.items())])
    for sym, divs in (pack.data.get("dividends") or {}).items():
        fp.set_events(sym, [(d, a) for d, a in divs])
    for sid in FRED_PACK_SERIES:
        obs = pack.fred(sid)
        if obs:
            fp.set_rate(sid, obs)
    return fp


# ---- S2 / S2b rows from the pack -------------------------------------------------------------------------------------------------
def _close_ts(sessions, group: str, d: date) -> Optional[int]:
    try:
        return int(sessions.session_close(group, d)) if sessions.session_close(group, d) is not None else None
    except Exception:   # noqa: BLE001
        return None


def mark_rows(pack: Pack, batch: Batch, cal, sessions=None) -> Tuple[List[dict], List[dict]]:
    """``(rows, notes)``: the CLOSE marks S2 records - the TRS underlyings for every NYSE trading day of the window (the Yahoo daily close,
    on the tick grid) and the options from their trade date (the Cboe official close for L when the chain is tied to L, else the Yahoo
    last trade; quality ``stale`` on a volume-0 day; a day without a bar carries the previous close, labelled)."""
    L = pack.through
    rows: List[dict] = []
    notes: List[dict] = []
    fetched = int(pack.data.get("fetched_ts") or 0)
    for sym in _underlyings(batch):
        bars = pack.closes(sym)
        for d in _dates(batch.window_from, L):
            if not cal.is_business_day(d):
                continue
            b = bars.get(d.isoformat())
            if b is None:
                notes.append({"instrument_id": sym, "date": d.isoformat(), "note": "no daily bar in the pack"})
                continue
            px = (Decimal(str(b["close"])) / Decimal("0.01")).quantize(Decimal(1)) * Decimal("0.01")
            rows.append({"instrument_id": sym, "mark_date": d.isoformat(), "kind": "CLOSE", "price": str(px), "currency": "USD", "quality": "close",
                         "source": b.get("source") or "Yahoo daily bars (observed close)", "method": b.get("method") or "daily_close",
                         "quote_time": epoch_of(d, CLOSE_ET), "fetched_at": fetched, "note": "recorded late (as-of import)"})
    for occ, info in sorted((pack.data.get("options") or {}).items()):
        td = _iso(info.get("trade_date")) or batch.window_from.isoformat()
        bars = ((info.get("daily") or {}).get("bars")) or {}
        oc = info.get("official_close") or {}
        prev: Optional[dict] = None
        for d in _dates(date.fromisoformat(td), L):
            if not cal.is_business_day(d):
                continue
            ds = d.isoformat()
            row = None
            if ds == L.isoformat() and oc.get("tied_to_L") and _dec(oc.get("price")) is not None:
                row = {"price": clean_price(oc["price"]), "quality": "close", "source": "Cboe delayed quotes prev_close (official close)", "method": "official_close",
                       "note": f"the official close of {ds}, from the Cboe chain of the next session"}
            elif ds in bars:
                b = bars[ds]
                vol0 = b.get("volume") is not None and int(b["volume"]) == 0
                row = {"price": clean_price(b["close"]), "quality": "stale" if vol0 else "close", "source": "Yahoo Finance option daily bar (last trade)",
                       "method": "daily_close", "note": ("no trade that day: the last trade of an earlier session" if vol0 else "the day's last trade")
                       + ("" if ds != L.isoformat() else " (the chain is not tied to this date: L's official close unavailable, an estimate)")}
            elif prev is not None:
                row = {"price": prev["price"], "quality": "stale", "source": "carried from the previous session", "method": "carried",
                       "note": "model mark (no trade that day): the previous session's close carried, no bar for this date"}
                notes.append({"instrument_id": occ, "date": ds, "note": "no bar: previous close carried, labelled stale"})
            else:
                notes.append({"instrument_id": occ, "date": ds, "note": "no bar and nothing earlier to carry: no mark recorded"})
                continue
            row.update({"instrument_id": occ, "mark_date": ds, "kind": "CLOSE", "currency": "USD", "quote_time": epoch_of(d, CLOSE_ET), "fetched_at": fetched})
            rows.append(row)
            prev = row
    return rows, notes


def input_rows(pack: Pack, batch: Batch) -> Tuple[List[dict], List[dict]]:
    """``(observations, curves)`` S2b records: the pack's FRED series and its UST_PAR curves."""
    obs = []
    for sid in FRED_PACK_SERIES:
        src = (pack.data.get("fred_sources") or {}).get(sid) or "FRED"
        for d, v in pack.fred(sid):
            obs.append({"series": sid, "as_of": d.isoformat(), "value": str(v), "source": src})
    curves = []
    for ds, pts in sorted(pack.curves().items()):
        src = (pack.data.get("curve_sources") or {}).get(ds) or "treasury.gov daily par yield curve"
        curves.append({"as_of": ds, "points": dict(sorted(pts.items())), "source": src, "fetched_ts": int(pack.data.get("fetched_ts") or 0)})
    return obs, curves


# ---- the light commands ------------------------------------------------------------------------------------------------------------
def _require_import(ctx: TxContext, what: str) -> None:
    if str(getattr(ctx, "actor", "") or "") != IMPORT_ACTOR:
        raise Forbidden("AS_OF_FORBIDDEN", f"{what} belongs to the historical import (run by the import tool), not to the terminal's screens",
                        {"actor": getattr(ctx, "actor", None)})


def h_record_inputs(ctx: TxContext, payload: dict) -> dict:
    """``import.record_inputs {batch_id, marks?, observations?, curves?}`` (light, actor import): INSERT OR IGNORE of the pack's CLOSE marks,
    FRED observations and UST_PAR curve points; a stored value that differs is listed in ``differences`` and never overwritten."""
    _require_import(ctx, "import.record_inputs")
    batch = str(payload.get("batch_id") or "").strip()
    if not batch:
        raise RejectError("BATCH_REQUIRED", "batch_id is required")
    inserted, present, diffs = _inputs(ctx.conn, payload, ctx.ts, write=True)
    ctx.event("IMPORT_INPUTS", ref_type="batch", ref_id=batch, inserted=inserted, present=present, differences=len(diffs))
    return {"batch_id": batch, "inserted": inserted, "present": present, "differences": diffs}


def inputs_compare(conn, payload: dict) -> dict:
    """The pack's inputs against the database, read-only (a fully applied batch run again records nothing): ``absent`` counts the values
    the book does not hold (the live system records them itself), ``differences`` the stored values that differ."""
    absent, present, diffs = _inputs(conn, payload, 0, write=False)
    return {"absent": absent, "present": present, "differences": diffs, "recorded": False}


def _inputs(conn, payload: dict, ts: int, write: bool) -> Tuple[dict, dict, List[dict]]:
    """``import.record_inputs``'s comparison: each value absent (inserted when ``write``), present and equal, or stored and different."""
    inserted = {"marks": 0, "observations": 0, "curve_points": 0}
    present = {"marks": 0, "observations": 0, "curve_points": 0}
    diffs: List[dict] = []
    for r in payload.get("marks") or []:
        kind = str(r.get("kind") or "CLOSE")
        if kind not in ("CLOSE", "SETTLE"):
            raise RejectError("MARK_KIND", f"a recorded input mark is CLOSE or SETTLE, not {kind!r}")
        px = _dec(r.get("price"))
        if px is None or px <= 0:
            raise RejectError("MARK_PRICE", f"{r.get('instrument_id')} {r.get('mark_date')}: price {r.get('price')!r} is not a positive number")
        iid, ds = str(r["instrument_id"]), date.fromisoformat(str(r["mark_date"])).isoformat()
        old = conn.execute("SELECT price, quality, source FROM marks WHERE instrument_id=? AND mark_date=? AND kind=?", (iid, ds, kind)).fetchone()
        if old is None and not write:
            inserted["marks"] += 1
        elif old is None:
            conn.execute("INSERT INTO marks(instrument_id, mark_date, kind, price, currency, quality, source, method, quote_time, fetched_at, note) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (iid, ds, kind, str(px), str(r.get("currency") or "USD"), str(r.get("quality") or "close"), str(r.get("source") or ""),
                          str(r.get("method") or "daily_close"), None if r.get("quote_time") is None else int(r["quote_time"]), int(r.get("fetched_at") or ts),
                          r.get("note")))
            inserted["marks"] += 1
        elif Decimal(str(old["price"])) == px:
            present["marks"] += 1
        else:
            diffs.append({"what": "mark", "instrument_id": iid, "date": ds, "kind": kind, "stored": str(old["price"]), "stored_source": old["source"],
                          "pack": str(px), "pack_source": r.get("source"), "note": "kept the stored value (never overwritten)"})
    for o in payload.get("observations") or []:
        sid, ds, v = str(o["series"]), date.fromisoformat(str(o["as_of"])).isoformat(), _dec(o.get("value"))
        if v is None:
            raise RejectError("OBS_VALUE", f"{sid} {ds}: value {o.get('value')!r} is not a number")
        old = conn.execute("SELECT value, source FROM curve_points WHERE series=? AND as_of=? AND tenor=?", (sid, ds, OBS_TENOR)).fetchone()
        if old is None and not write:
            inserted["observations"] += 1
        elif old is None:
            conn.execute("INSERT INTO curve_points(series, as_of, tenor, value, fetched_ts, source) VALUES (?,?,?,?,?,?)",
                         (sid, ds, OBS_TENOR, str(v), int(o.get("fetched_ts") or ts), str(o.get("source") or "FRED")))
            inserted["observations"] += 1
        elif Decimal(str(old["value"])) == v:
            present["observations"] += 1
        else:
            diffs.append({"what": "observation", "series": sid, "date": ds, "stored": str(old["value"]), "stored_source": old["source"], "pack": str(v),
                          "note": "kept the stored value (never overwritten)"})
    for c in payload.get("curves") or []:
        ds = date.fromisoformat(str(c["as_of"])).isoformat()
        for tenor, val in sorted((c.get("points") or {}).items()):
            v = _dec(val)
            if v is None:
                raise RejectError("CURVE_VALUE", f"{CURVE_SERIES} {ds} {tenor}: {val!r} is not a number")
            old = conn.execute("SELECT value, source FROM curve_points WHERE series=? AND as_of=? AND tenor=?", (CURVE_SERIES, ds, str(tenor))).fetchone()
            if old is None and not write:
                inserted["curve_points"] += 1
            elif old is None:
                conn.execute("INSERT INTO curve_points(series, as_of, tenor, value, fetched_ts, source) VALUES (?,?,?,?,?,?)",
                             (CURVE_SERIES, ds, str(tenor), str(v), int(c.get("fetched_ts") or ts), str(c.get("source") or "treasury.gov daily par yield curve")))
                inserted["curve_points"] += 1
            elif Decimal(str(old["value"])) == v:
                present["curve_points"] += 1
            else:
                diffs.append({"what": "curve point", "series": CURVE_SERIES, "date": ds, "tenor": str(tenor), "stored": str(old["value"]), "pack": str(v),
                              "note": "kept the stored value (never overwritten)"})
    return inserted, present, diffs


def h_batch_record(ctx: TxContext, payload: dict) -> dict:
    """``import.batch_record {batch_id, ...columns}`` (light, actor import): upserts the ``import_batches`` row."""
    _require_import(ctx, "import.batch_record")
    bid = str(payload.get("batch_id") or "").strip()
    if not bid:
        raise RejectError("BATCH_REQUIRED", "batch_id is required")
    vals = {k: payload.get(k) for k in BATCH_COLUMNS if k in payload}
    for k in ("source_json", "cutoff_json", "report_json"):
        if k in vals and not isinstance(vals[k], str) and vals[k] is not None:
            vals[k] = dumps(vals[k])
    conn = ctx.conn
    old = conn.execute("SELECT batch_id FROM import_batches WHERE batch_id=?", (bid,)).fetchone()
    if old is None:
        vals.setdefault("source_json", "{}")
        vals.setdefault("status", "RUNNING")
        cols = ["batch_id", "created_ts", "command_id"] + list(vals)
        conn.execute(f"INSERT INTO import_batches({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})", [bid, ctx.ts, ctx.command_id] + list(vals.values()))
    elif vals:
        conn.execute(f"UPDATE import_batches SET {', '.join(f'{k}=?' for k in vals)} WHERE batch_id=?", list(vals.values()) + [bid])
    return {"batch_id": bid, "updated": sorted(vals)}


def h_line_record(ctx: TxContext, payload: dict) -> dict:
    """``import.line_record {batch_id, line_no, ...columns}`` (light, actor import): upserts one ``import_lines`` row."""
    _require_import(ctx, "import.line_record")
    bid = str(payload.get("batch_id") or "").strip()
    n = payload.get("line_no")
    if not bid or not isinstance(n, int):
        raise RejectError("LINE_REQUIRED", "batch_id and line_no are required")
    vals = {k: payload.get(k) for k in LINE_COLUMNS if k in payload}
    for k in ("source_json", "match_json", "diff_json", "evidence_json", "created_refs_json"):
        if k in vals and vals[k] is not None and not isinstance(vals[k], str):
            vals[k] = dumps(vals[k])
    conn = ctx.conn
    old = conn.execute("SELECT 1 FROM import_lines WHERE batch_id=? AND line_no=?", (bid, n)).fetchone()
    if old is None:
        for k, default in (("product", "UNKNOWN"), ("source_json", "{}"), ("source_label", ""), ("status", "unknown"), ("action", "none")):
            vals.setdefault(k, default)
        vals["historical_model"] = 1 if vals.get("historical_model") else 0
        cols = ["batch_id", "line_no", "command_id"] + list(vals)
        conn.execute(f"INSERT INTO import_lines({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})", [bid, n, ctx.command_id] + list(vals.values()))
    elif vals:
        if "historical_model" in vals:
            vals["historical_model"] = 1 if vals["historical_model"] else 0
        conn.execute(f"UPDATE import_lines SET {', '.join(f'{k}=?' for k in vals)} WHERE batch_id=? AND line_no=?", list(vals.values()) + [bid, n])
    return {"batch_id": bid, "line_no": n, "updated": sorted(vals)}


# ---- module wiring ---------------------------------------------------------------------------------------------------------------------
def bind(rt) -> None:
    """``import_batches`` / ``import_lines`` (additive, idempotent).  Exception-free: a failure is logged."""
    db = getattr(rt, "db", None)
    if db is None or not callable(getattr(db, "ensure_schema_ext", None)):
        return
    try:
        db.ensure_schema_ext("importer", SCHEMA)
    except Exception as e:   # noqa: BLE001 - logged; the import commands refuse until the tables exist
        log = getattr(rt, "log", None)
        if log:
            log.warning("importer.bind: the import tables could not be created: %s", e)


def register(bus, services=None) -> None:
    bus.register("import.record_inputs", h_record_inputs, light=True)
    bus.register("import.batch_record", h_batch_record, light=True)
    bus.register("import.line_record", h_line_record, light=True)


def data_dir() -> str:
    return os.path.join(config.package_dir(), "data", "imports")


def batch_file(batch_id: str) -> Optional[str]:
    """The repo copy of a batch (``data/imports/<id>.jsonl``), None when absent or the id is not a plain name."""
    if not batch_id or any(ch in batch_id for ch in "/\\:") or batch_id.startswith("."):
        return None
    p = os.path.join(data_dir(), f"{batch_id}.jsonl")
    return p if os.path.isfile(p) else None


def register_routes(r) -> None:
    """C19 (read-only): ``GET /api/import/batches``, ``GET /api/import/batches/{id}``, ``POST /api/import/report {batch_id}``."""
    from .api import ApiError, NotFound

    def _rows(conn, sql, params=()):
        return [dict(x) for x in conn.execute(sql, params).fetchall()]

    def batches(req):
        rt = req.rt
        out = []
        with rt.db.reader() as conn:
            stored = {x["batch_id"]: x for x in (_rows(conn, "SELECT batch_id, created_ts, status, mode_last, pack_sha256, window_from, window_through, applied_ts,"
                                                           " match_hash, owner_go, source_json FROM import_batches ORDER BY created_ts")
                                                     if _table_exists(conn, "import_batches") else [])}
            books = {x["lifetime_id"]: x for x in _rows(conn, "SELECT l.lifetime_id, l.book_code, b.name FROM lifetimes l LEFT JOIN books b"
                                                              " ON b.book_code = l.book_code")} if _table_exists(conn, "lifetimes") else {}
        files = sorted(f[:-6] for f in os.listdir(data_dir()) if f.endswith(".jsonl")) if os.path.isdir(data_dir()) else []
        for bid in sorted(set(files) | set(stored)):
            row = dict(stored.get(bid) or {"batch_id": bid, "status": "NOT_RUN"})
            path = batch_file(bid) or _recorded_batch_path(row.pop("source_json", None))
            row.pop("source_json", None)
            if path:
                try:
                    b = load_batch(path)
                    if b.batch_id != bid:
                        raise ImportRefused("BATCH_ID", f"the file recorded for {bid} holds batch {b.batch_id}")
                    row.update(lines=len(b.lines), decisions={d: len((b.header.get("decisions") or {}).get(d) or []) for d in DECISIONS},
                               content_sha256=b.content_sha256, deadline=b.deadline.isoformat(), window_from=row.get("window_from") or b.window_from.isoformat(),
                               lifetime_id=b.lifetime)
                    bk = books.get(b.lifetime) or {}
                    row.update(book_code=bk.get("book_code"), book_name=bk.get("name"))
                except ImportRefused as e:
                    row.update(problem=e.message)
            applied = _et_of(row.get("applied_ts"))
            row["state"] = (f"applied {applied}" if applied else "dry run done (not applied to this book)"
                            if row.get("mode_last") == "dry-run" and row.get("status") == "DONE" else RUN_WORDS.get(str(row.get("status")), str(row.get("status"))))
            out.append(row)
        return {"batches": out}

    def batch_one(req):
        rt = req.rt
        bid = req.params[0]
        with rt.db.reader() as conn:
            row = _rows(conn, "SELECT * FROM import_batches WHERE batch_id=?", (bid,)) if _table_exists(conn, "import_batches") else []
            lines = _rows(conn, "SELECT * FROM import_lines WHERE batch_id=? ORDER BY line_no", (bid,)) if _table_exists(conn, "import_lines") else []
        path = batch_file(bid)
        if not row and not path:
            raise NotFound(f"import batch {bid} does not exist")
        out = {"batch_id": bid, "status": (row[0]["status"] if row else "NOT_RUN"), "batch": row[0] if row else None, "lines": lines}
        rep = None
        if row and row[0].get("report_json"):
            try:
                rep = json.loads(row[0]["report_json"])
                out["report"] = {k: rep.get(k) for k in ("cash_table", "funding_moves", "restated", "bridge", "integrity", "trial_balance", "counts", "status")}
            except ValueError:
                out["report"] = None
            out["batch"] = {k: v for k, v in row[0].items() if k != "report_json"}
        b = None
        if not path and row:
            # a batch run from outside the release's data folder: the file its run recorded, when it is still there and is this batch
            path = _recorded_batch_path(row[0].get("source_json"))
        if path:
            try:
                b = load_batch(path)
            except ImportRefused:
                b = None
            if b is not None and b.batch_id != bid:
                b = None
        if b is not None and not lines:
            out["planned"] = [{"line_no": n, "product": ln.get("product"), "instrument_id": ln.get("instrument_id"), "side": ln.get("side"), "qty": ln.get("qty"),
                               "decision": ln.get("decision"), "status": ln.get("match_status"), "product_label": ln.get("product_label")} for n, ln in sorted(b.lines.items())]
        with rt.db.reader() as conn:
            names = {x["account_id"]: x["name"] for x in _rows(conn, "SELECT account_id, name FROM accounts")}
        out["view"] = batch_view(bid, row[0] if row else None, lines, rep, b, names)
        return out

    def report_route(req):
        rt = req.rt
        bid = str(req.b("batch_id") or BATCH_ID)
        path = batch_file(bid)
        if not path:
            raise NotFound(f"import batch {bid} is not in this build")
        db_path = getattr(rt, "db_path", None) or getattr(rt.db, "path", None)
        if not db_path:
            raise ApiError(409, "NOT_READY", "the database path is unknown")
        b = load_batch(path)
        conn = open_ro(db_path)
        try:
            rep = report_on(conn, b)
        finally:
            conn.close()
        rep["mode"] = "report"
        clock = getattr(rt, "clock", None)
        rep["checked_et"] = clock.ny().strftime("%Y-%m-%d %H:%M:%S ET") if clock is not None else None
        for ln in rep.get("lines") or []:              # the words the import panel shows beside the stored record
            ln["status_text"] = STATUS_WORDS.get(str(ln.get("status")), str(ln.get("status")))
            ln["status_short"] = STATUS_SHORT.get(str(ln.get("status")), str(ln.get("status")))
            ln["differences"] = [_diff_text(d) for d in ln.get("diffs") or [] if isinstance(d, dict)]
        return rep

    r.add("GET", r"/api/import/batches", batches)
    r.add("GET", r"/api/import/batches/([^/]+)", batch_one)
    r.add("POST", r"/api/import/report", report_route, mutating=False)


# ---- the batch view (C19; components/imports.js shows it as sent) ------------------------------------------------------------------
PRODUCT_WORDS = {"EQUITY": "Equity", "REIT": "REIT", "ETF": "ETF", "ADR": "ADR", "PREFERRED": "Preferred", "FUTURE": "Future", "OPTION": "Listed option",
                 "BOND": "Bond / bill", "OTC_TRS": "Total return swap", "OTC_CDX": "CDS index", "OTC_IRS": "Interest-rate swap", "CASH": "Cash"}
DECISION_WORDS = {"preserve": "preserve the existing execution", "import": "import as of its trade date", "declined": "declined by the owner",
                  "unresolved": "unresolved (not a transaction)"}
STATUS_WORDS = {"present_match": "in the book, matches the workbook", "present_differs": "in the book, differs from the workbook (reported, never corrected)",
                "missing_import": "missing from the book", "already_imported": "imported", "declined": "declined", "unresolved": "unresolved"}
ACTION_WORDS = {"booked": "booked as of its trade date", "already_imported": "already in the book (nothing booked again)"}
TRANSFER_WORDS = {"SWEEP_IN": "sweep in (from Treasury)", "SWEEP_OUT": "sweep out (to Treasury)", "TRANSFER": "internal transfer",
                  "DEPOSIT": "deposit", "WITHDRAW": "withdrawal", "PB_DRAW": "prime-broker draw", "PB_REPAY": "prime-broker repayment"}
RUN_WORDS = {"DONE": "done", "DONE_WITH_PROBLEMS": "done with problems", "PARTIAL": "partial (a re-run with the same pack continues it)",
             "REFUSED": "refused (nothing written)", "RUNNING": "running", "NOT_RUN": "not run in this book"}


def _recorded_batch_path(source_json) -> Optional[str]:
    """The batch file a run recorded (``import_batches.source_json.path``), when it still exists."""
    src = str((_json_of(source_json, {}) or {}).get("path") or "")
    return src if src and os.path.isfile(src) else None


def _json_of(v, default):
    if v is None or v == "":
        return default
    if not isinstance(v, str):
        return v
    try:
        return json.loads(v)
    except ValueError:
        return default


def _et_of(ts) -> Optional[str]:
    try:
        return ny_of(int(ts)).strftime("%Y-%m-%d %H:%M ET") if ts else None
    except (TypeError, ValueError, OverflowError):
        return None


def _price_short(price) -> str:
    """The price cell: the batch's price without its remark ('1 (workbook placeholder)' is no price: 'n/a')."""
    t = "" if price is None or str(price) == "None" else str(price)
    if "placeholder" in t:
        return "n/a"
    return t.split(" (")[0]


def _short_et(ts) -> Optional[str]:
    try:
        return ny_of(int(ts)).strftime("%m-%d %H:%M") if ts else None
    except (TypeError, ValueError, OverflowError):
        return None


def _created_words(refs: dict) -> str:
    """The rows a line created, in the ids the terminal shows (contract / order / fill / settlement / transfer) and a count of entries."""
    parts = []
    for key, word in (("contract_ids", "contract"), ("order_ids", "order"), ("fill_ids", "fill"), ("settle_ids", "settlement"), ("transfer_ids", "transfer")):
        ids = [str(x) for x in refs.get(key) or [] if x]
        if ids:
            parts.append(f"{word}{'s' if len(ids) > 1 else ''} {', '.join(ids)}")
    n = len(refs.get("entry_ids") or [])
    if n:
        parts.append(f"{n} journal entr{'ies' if n > 1 else 'y'}")
    return "; ".join(parts)


SIDE_CODES = (("SELL_PROTECTION", "sell protection"), ("BUY_PROTECTION", "buy protection"), ("SELL_SHORT", "sell short"), ("PAY_FIXED", "pay fixed"),
              ("RECEIVE_FIXED", "receive fixed"), ("BTO", "buy to open"), ("STO", "sell to open"), ("BTC", "buy to close"), ("STC", "sell to close"),
              ("BUY", "buy"), ("SELL", "sell"), ("SHORT", "short"), ("LONG", "long"))


def _side_words(side) -> str:
    """A batch side in words: its leading code (BUY, SELL_SHORT, BTO, BUY_PROTECTION, PAY_FIXED ...) spelled out, the rest kept."""
    t = str(side or "").strip()
    for code, words in SIDE_CODES:
        if t == code or t.startswith(code + " "):
            return words + t[len(code):]
    return t


def _instrument_words(src: Optional[dict], ln: dict, refs: dict) -> Tuple[str, str]:
    """(shown, full): the batch's instrument with the contract id it was booked as; a long description cut at its first clause."""
    full = str((src or {}).get("instrument_id") or ln.get("instrument_id") or "")
    cids = [str(x) for x in refs.get("contract_ids") or [] if x]
    if "(new contract id at booking)" in full:
        full = full.replace("(new contract id at booking)", f"({', '.join(cids)})" if cids else "(contract id given at booking)")
    shown = full
    if len(shown) > 40:
        cut = min([i for i in (shown.find(" ("), shown.find(" - ")) if i > 0] or [40])
        shown = shown[:cut]
    return shown, full


def _qty_words(qty) -> str:
    """A batch quantity with thousands separators, its unit words kept ('3000000 notional' -> '3,000,000 notional')."""
    q = str(qty or "").strip()
    head, _sp, tail = q.partition(" ")
    d = _dec(head)
    if d is None or str(d) != head:
        return q
    num = f"{d:,.{len(head.split('.')[1])}f}" if "." in head else f"{int(d):,}"
    return f"{num} {tail}".strip()


PRODUCT_SHORT = {"EQUITY": "Equity", "REIT": "REIT", "ETF": "ETF", "ADR": "ADR", "PREFERRED": "Preferred", "FUTURE": "Future", "OPTION": "Option",
                 "BOND": "Bill / bond", "OTC_TRS": "TRS", "OTC_CDX": "CDX", "OTC_IRS": "IRS", "CASH": "Cash"}
STATUS_SHORT = {"present_match": "matches", "present_differs": "differs", "missing_import": "missing", "already_imported": "imported",
                "declined": "declined", "unresolved": "unresolved"}
ACTION_SHORT = {"preserved (untouched)": "untouched", "declined (never booked)": "not booked", "unresolved (not a transaction)": "not a transaction",
                "already_imported": "already in the book"}


def _short(text: str) -> str:
    t = str(text or "")
    cut = min([i for i in (t.find(" ("), t.find(" / ")) if i > 0] or [len(t)])
    return t[:cut]


def batch_view(bid: str, row: Optional[dict], lines: List[dict], rep: Optional[dict], b: Optional[Batch], names: Dict[str, str]) -> dict:
    """``GET /api/import/batches/{id}``'s ``view``: the batch, its lines, the business-date cash table, the funding moves, the restated
    NAV with its split and the bridge to the workbook, in the words and ids the terminal shows (account names, never raw codes).  Every
    figure is the stored run's (``import_batches.report_json``); nothing is recomputed here."""
    rep = rep or {}
    name = lambda a: names.get(a) or (str(a).split(":", 1)[-1].replace("_", " ").title() if a else "")    # noqa: E731
    status = (row or {}).get("status") or "NOT_RUN"
    applied = _et_of((row or {}).get("applied_ts"))
    if applied:
        state = f"applied {applied}"
    elif (row or {}).get("mode_last") == "dry-run" and status == "DONE":
        state = "dry run done (not applied to this book)"
    else:
        state = RUN_WORDS.get(status, status.lower().replace("_", " "))
    c6 = rep.get("C6") or {}
    integ = (c6.get("integrity") or {})
    tb = (c6.get("trial_balance") or {})
    pack = rep.get("pack") or {}
    summary = [["Status", state],
               ["Last run", f"{rep.get('mode') or (row or {}).get('mode_last') or 'n/a'}: {RUN_WORDS.get(str(rep.get('status')), rep.get('status') or 'n/a')}"
                            + (f" ({rep.get('started_et')} .. {rep.get('finished_et')})" if rep.get("started_et") else "")],
               ["Window", f"{(row or {}).get('window_from') or (b.window_from.isoformat() if b else 'n/a')} .. {(row or {}).get('window_through') or 'n/a'}"],
               ["Inputs fetched", pack.get("fetched_et") or "n/a", "the data pack the run used (prices, FRED observations, curves)"],
               ["Owner's go", (row or {}).get("owner_go") or "n/a"],
               ["Pre-import backup", os.path.basename(str((row or {}).get("backup_path") or "")) or "n/a", str((row or {}).get("backup_path") or "")],
               ["Integrity", ("ok" if integ.get("ok") else "FAILED: " + ", ".join(integ.get("failing") or [])) if integ else "n/a"],
               ["Trial balance", ("balanced" if tb.get("balanced") else "NOT balanced") if tb else "n/a"],
               ["Entries posted by the last run", rep.get("entries_posted") if rep.get("entries_posted") is not None else "n/a"]]
    if b is not None:
        summary.insert(2, ["Lines", ", ".join(f"{len((b.header.get('decisions') or {}).get(d) or [])} {DECISION_WORDS[d].split(' (')[0]}" for d in DECISIONS)])
        summary.append(["Deadline", f"applied no later than {b.deadline.isoformat()}, before 16:00 ET"])
    vlines = []
    for ln in lines:
        refs = _json_of(ln.get("created_refs_json"), {}) or {}
        diffs = _json_of(ln.get("diff_json"), []) or []
        src = b.lines.get(int(ln["line_no"])) if b is not None else None
        prod = str(ln.get("product") or "")
        price = (src or {}).get("intended_price") if src else ln.get("price")
        action = str(ln.get("action") or "")
        shown, full = _instrument_words(src, ln, refs)
        vlines.append({"line_no": ln["line_no"], "product": str((src or {}).get("product_label") or PRODUCT_WORDS.get(prod, prod.title())),
                       "instrument": shown, "instrument_full": full,
                       "side": _side_words((src or {}).get("side") or ln.get("side")), "qty": _qty_words(ln.get("qty")),
                       "price": "" if str(price or "") == "None" else str(price or ""),
                       "trade_date": ln.get("trade_date") or "", "account": name(ln.get("account_id")), "counterparty": ln.get("counterparty") or "",
                       "decision": DECISION_WORDS.get(str(ln.get("decision") or (src or {}).get("decision") or ""), ln.get("decision") or ""),
                       "status": STATUS_WORDS.get(str(ln.get("status") or ""), str(ln.get("status") or "")),
                       "product_short": PRODUCT_SHORT.get(prod, prod.title()), "side_short": _short(_side_words((src or {}).get("side") or ln.get("side"))),
                       "qty_short": _qty_words(ln.get("qty")).split(" ")[0], "price_short": _price_short(price),
                       "date_short": str(ln.get("trade_date") or "")[5:],
                       "decision_short": str(ln.get("decision") or (src or {}).get("decision") or ""),
                       "status_short": STATUS_SHORT.get(str(ln.get("status") or ""), str(ln.get("status") or "")),
                       "action_short": (("booked " + (_short_et(ln.get("applied_ts")) or "in a dry run")) if action == "booked" else ACTION_SHORT.get(action, action)),
                       "action": ACTION_WORDS.get(action, action), "applied_et": _et_of(ln.get("applied_ts")), "created": _created_words(refs),
                       "source": ln.get("source_label") or "", "asof": ln.get("booking") == "AS_OF", "historical_model": bool(ln.get("historical_model")),
                       "differences": [_diff_text(d) for d in diffs if isinstance(d, dict)], "note": ln.get("note") or ""})
    ct = rep.get("cash_table") or {}
    after = ct.get("after") or {}
    dates = sorted(next(iter(after.values()))) if after else []
    stage_words = (("before", "before the import"), ("before_sweeps", "after the bookings, before the as-of sweeps"), ("after", "after the import"))
    cash_rows = []
    for aid in sorted(after, key=lambda a: (a != f"{(b.lifetime if b else 'F1')}:TREASURY", name(a))):
        if not any((ct.get(k) or {}).get(aid, {}).get(d) for k, _w in stage_words for d in dates):
            continue                                   # an account the import never touched and that held no cash: left out
        for key, words in stage_words:
            vals = (ct.get(key) or {}).get(aid)
            if vals is not None:
                cash_rows.append({"account": name(aid), "stage": words, "values_c": {d: vals.get(d) for d in dates},
                                  "below_zero": any((vals.get(d) or 0) < 0 for d in dates)})
    funding = [{"transfer_id": f.get("transfer_id"), "kind": TRANSFER_WORDS.get(str(f.get("kind")), str(f.get("kind") or "").lower().replace("_", " ")),
                "from": name(f.get("from_account")), "to": name(f.get("to_account")), "amount_c": f.get("amount_c"), "business_date": f.get("business_date"),
                "for": _funding_for(f)} for f in rep.get("funding_moves") or []]
    br = rep.get("bridge") or {}
    cids_by_line = {int(x["line_no"]): [str(c) for c in ((_json_of(x.get("created_refs_json"), {}) or {}).get("contract_ids") or []) if c] for x in lines}

    def line_what(e: dict) -> str:
        """A split line's label without its 'line N: ' prefix (the table numbers it) and with the contract it was booked as."""
        t = str(e.get("label") or "")
        n = e.get("line_no")
        if n is not None and t.startswith(f"line {n}: "):
            t = t[len(f"line {n}: "):]
        if "(new contract id at booking)" in t:
            cids = cids_by_line.get(int(n)) if n is not None else None
            t = t.replace("(new contract id at booking)", f"({', '.join(cids)})" if cids else "(contract id given at booking)")
        return t
    restated = []
    for v in br.get("dates") or []:
        restated.append({"date": v.get("date"), "original_c": v.get("original_nav_c"), "import_c": v.get("import_total_c"), "interest_c": v.get("interest_effect_c"),
                         "mark_policy_c": v.get("mark_policy_c"), "dating_c": v.get("dating_c"), "restated_c": v.get("restated_nav_c"),
                         "residual_c": v.get("residual_c"), "added": bool(v.get("added")),
                         "by_line": [{"line_no": e.get("line_no"), "what": line_what(e), "amount_c": e.get("amount_c")} for e in v.get("import_effect") or []
                                     if e.get("amount_c")],
                         "mark_policy": [{"instrument": m.get("instrument_id"), "original_mark": m.get("original_mark"), "dated_mark": m.get("dated_mark"),
                                          "amount_c": m.get("amount_c")} for m in v.get("mark_policy") or [] if isinstance(m, dict)],
                         "dating": [f"entry {x.get('entry_id')} ({T_WORDS.get(str(x.get('kind')), str(x.get('kind') or '').lower().replace('_', ' '))}):"
                                    f" {x.get('why')}" for x in v.get("dating") or [] if isinstance(x, dict)]})
    accounts = {}
    for v in rep.get("restated") or []:
        a = v.get("account_id")
        if not a or a.endswith(":ALL"):
            continue
        cur = accounts.setdefault(a, {})
        if v.get("snap_date") not in cur or v.get("version", 0) > cur[v["snap_date"]]["version"]:
            cur[v["snap_date"]] = {"version": v.get("version", 0), "original_c": v.get("original_nav_c"), "restated_c": v.get("nav_c"),
                                   "added": bool(v.get("added"))}
    acc_rows = [{"account": name(a), "dates": {d: {k: x[k] for k in ("original_c", "restated_c", "added")} for d, x in sorted(rows.items())}}
                for a, rows in sorted(accounts.items(), key=lambda kv: name(kv[0]))
                if any(x["original_c"] or x["restated_c"] for x in rows.values())]
    tw = br.get("to_workbook")
    bridge_v = None
    if tw:
        bridge_v = {"date": tw.get("date"), "workbook_c": tw.get("workbook_nav_c"), "restated_c": tw.get("restated_nav_c"), "difference_c": tw.get("difference_c"),
                    "lines": [{"what": x.get("what"), "amount_c": x.get("amount_c"), "source": x.get("source"),
                               "source_short": "reconciliation estimate" if str(x.get("source") or "").startswith("the reconciliation") else x.get("source")}
                              for x in tw.get("lines") or []],
                    "unexplained_c": tw.get("unexplained_c"), "unexplained_what": tw.get("unexplained_what"), "note": tw.get("note")}
    notes = [str(n) for n in rep.get("notes") or []]
    return {"batch_id": bid, "lifetime_id": b.lifetime if b is not None else None, "state": state, "summary": summary, "lines": vlines, "cash": {"dates": dates, "rows": cash_rows},
            "funding": funding, "restated": {"book": restated, "accounts": acc_rows}, "bridge": bridge_v, "notes": notes,
            "verify_only": bool(rep.get("verify_only"))}


T_WORDS = {"SETTLE": "settlement", "COLLATERAL": "collateral mark", "SWEEP_IN": "sweep in", "SWEEP_OUT": "sweep out", "DIVIDEND": "dividend", "VM": "variation margin",
           "ACCRUAL": "accrual", "ASOF_FUNDING": "as-of funding"}


def _funding_for(f: dict) -> str:
    """What a funding move paid for, from its link: an OTC contract's initial margin, an as-of sweep, an as-of transfer."""
    lt, li = str(f.get("linked_type") or ""), str(f.get("linked_id") or "")
    if lt == "otc" and li.startswith("im:"):
        return f"initial margin of {li.split(':')[1]}"
    if lt == "asof_funding":
        return "as-of funding of the import"
    if str(f.get("idem_key") or "").startswith("asofsweep:"):
        return "as-of sweep of the import's cash"
    if lt == "otc":
        return f"OTC contract {li.split(':')[1] if ':' in li else li}"
    if lt in ("settlement", "settle"):
        return f"settlement {li}"
    return li.replace("_", " ") if li else ""


# ---- locks, clock rules, cutoff ---------------------------------------------------------------------------------------------------
class _Locks:
    """``<home>/terminal.lock`` and ``<db>.lock``, held by the importer for a dry run or an apply (a running terminal holds them)."""

    def __init__(self, home: str, db_path: str):
        from .server import InstanceLock, db_lock_path
        self.home_lock = InstanceLock(os.path.join(home, "terminal.lock"))
        self.db_lock = InstanceLock(db_lock_path(db_path))

    def acquire(self, info: dict) -> Optional[dict]:
        """None when both locks are now held; else the holder's JSON (nothing is kept)."""
        if not self.home_lock.acquire():
            return self.home_lock.read() or {"pid": None}
        if not self.db_lock.acquire():
            holder = self.db_lock.read() or {"pid": None}
            self.home_lock.release()
            return holder
        for lk in (self.home_lock, self.db_lock):
            try:
                lk.write(info)
            except Exception:   # noqa: BLE001 - the lock is held either way
                pass
        return None

    def release(self) -> None:
        for lk in (self.db_lock, self.home_lock):
            try:
                if lk.held:
                    lk.release()
            except Exception:   # noqa: BLE001
                pass


def _lock_info(batch_id: str, mode: str) -> dict:
    from . import VERSION
    return {"pid": os.getpid(), "importer": batch_id, "mode": mode, "started_ts": int(_time.time()), "version": VERSION, "port": None}


def clock_problems(batch: Batch, clock) -> List[str]:
    """The S0 clock rules: on or before the deadline, and before 16:00 ET (the day's closing jobs never fall inside the catch-up)."""
    now = clock.ny()
    out = []
    if now.date() > batch.deadline:
        out.append(f"today ({now.date()}) is after the batch's deadline {batch.deadline}: the batch must be regenerated (the options expire 2026-11-20)")
    if now.strftime("%H:%M:%S") >= CLOSE_ET + ":00":
        out.append(f"it is {now.strftime('%H:%M')} ET: the dry run and the apply run before 16:00 ET (the closing jobs from 16:20 and the 17:45 snapshot must"
                   " never fall inside the catch-up)")
    return out


def due_keys(rt) -> List[str]:
    """The due keys of every lifecycle job except INTEGRITY_FULL and HOUSEKEEPING (they post nothing), sorted ``JOB:key``."""
    sched = getattr(rt, "lifecycle", None)
    if sched is None:
        return []
    out: List[str] = []
    with rt.db.reader() as conn:
        for job in sched.jobs:
            if job.name in CUTOFF_EXCLUDED_JOBS:
                continue
            try:
                ok, _note = job.available(rt)
            except Exception:   # noqa: BLE001
                ok = False
            if not ok:
                continue
            try:
                keys = list(job.due(conn, rt.clock, rt.cal, rt.sessions))
            except Exception as e:   # noqa: BLE001 - a failing due() is part of the state the cutoff pins
                keys = [f"<due failed: {type(e).__name__}>"]
            out += [f"{job.name}:{k}" for k in keys]
    return sorted(out)


CUTOFF_COMPARED = ("accounting_day", "due_keys_sha256", "state_version", "max_command_id")


def cutoff_fields(rt, src_meta: dict) -> dict:
    keys = due_keys(rt)
    return {"accounting_day": accounting_day(rt.clock.now()).isoformat(), "due_keys_sha256": _sha256_bytes(canonical_json(keys).encode("utf-8")),
            "state_version": src_meta.get("state_version"), "max_command_id": src_meta.get("max_command_id"), "due_keys_n": len(keys),
            "due_keys_sample": keys[:40], "now_ts": int(rt.clock.epoch()), "now_et": rt.clock.ny().strftime("%Y-%m-%d %H:%M:%S ET")}


def _source_meta(conn) -> dict:
    return {"state_version": _meta(conn, "state_version"), "max_command_id": conn.execute("SELECT MAX(id) FROM commands").fetchone()[0],
            "app_version_last_opened": _meta(conn, "app_version_last_opened")}


def maintenance_runtime(home: str, db_path: str, pack: Pack, clock, log):
    """``server.build_runtime`` on ``db_path`` with the pack as the only provider; the workers are never started."""
    from . import server
    return server.build_runtime(home, db_path, clock=clock, offline=True, fake_provider=PackProvider(pack, clock), log=log, fetch_threads=0)


# ---- the run ---------------------------------------------------------------------------------------------------------------------
class Run:
    def __init__(self, rt, batch: Batch, pack: Pack, mode: str, clock, log, out):
        self.rt, self.batch, self.pack, self.mode, self.clock, self.log, self.out = rt, batch, pack, mode, clock, log, out
        self.bid, self.lid = batch.batch_id, batch.lifetime
        self.steps: List[dict] = []
        self.notes: List[str] = []
        self.booked: Dict[int, dict] = {}       # the lines' bookings so far (kept when a later step refuses)

    def say(self, msg: str) -> None:
        if self.log is not None:
            self.log.info("%s", msg)
        if self.out is not None:
            self.out(msg)

    def q(self, sql: str, params=()) -> List[dict]:
        with self.rt.db.reader() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def cmd(self, step: str, kind: str, payload: dict, rid: str, ok_codes: Sequence[str] = (), rerun_suffix: bool = False):
        rt = self.rt
        res = rt.bus.execute(Command(kind, payload, rid, IMPORT_ACTOR))
        used = rid
        if not res.ok and res.code == "REQUEST_ID_REUSED" and rerun_suffix:
            n = len(self.q("SELECT 1 FROM commands WHERE request_id LIKE ?", (rid + ":r%",))) + 2
            used = f"{rid}:r{n}"
            res = rt.bus.execute(Command(kind, payload, used, IMPORT_ACTOR))
        rec = {"step": step, "kind": kind, "request_id": used, "ok": res.ok, "http": res.http, "code": res.code, "replayed": bool(res.replayed),
               "command_id": res.command_id}
        if not res.ok:
            rec["message"] = res.message
            rec["details"] = (res.body.get("error") or {}).get("details") if isinstance(res.body, dict) else None
        self.steps.append(rec)
        if not res.ok and res.code not in ok_codes:
            raise ImportRefused(res.code or "COMMAND_FAILED", f"{step}: {kind} was refused: {res.message}",
                                {"request_id": used, "http": res.http, "details": rec.get("details")}, step=step)
        return res


def _import_lines(batch: Batch) -> List[dict]:
    return [ln for ln in batch.ordered() if ln.get("decision") == "import"]


def _cdx_terms(batch: Batch) -> List[dict]:
    out = []
    for ln in _import_lines(batch):
        if ln.get("product") != "OTC_CDX":
            continue
        p = (ln.get("command") or {}).get("payload") or {}
        t = p.get("terms") or {}
        out.append({"index": str(t.get("index") or "").upper().replace("CDX.NA.", ""), "dealt": _dec(t.get("dealt_spread_bp")), "side": str(t.get("side") or "").upper(),
                    "account_id": p.get("account_id"), "trade_date": _iso((p.get("as_of") or {}).get("trade_date")), "line_no": int(ln["line_no"])})
    return out


def input_problems(conn, batch: Batch, pack: Pack, cal) -> Tuple[List[str], List[str]]:
    """S0's input rules over the pack and the database (module docstring DRY RUN)."""
    problems: List[str] = []
    notes: List[str] = []
    lo, L = batch.window_from, pack.through
    bdays = [d for d in _dates(lo, L) if cal.is_business_day(d)]
    for sym in _underlyings(batch):
        have = set(pack.closes(sym)) | {r[0] for r in conn.execute("SELECT mark_date FROM marks WHERE instrument_id=? AND kind='CLOSE'", (sym,)).fetchall()}
        for d in bdays:
            if d.isoformat() not in have:
                problems.append(f"{sym}: no close for {d} in the pack or the database (the TRS variation margin of that date needs it)")
    for occ, info in sorted(_option_specs(batch).items()):
        o = pack.option(occ)
        row = o.get("chain_row") or {}
        if str(row.get("option") or "") != occ:
            problems.append(f"{occ}: the pack holds no Cboe chain row (the booking creates the contract from it: S3)")
        td = info.get("trade_date")
        bars = ((o.get("daily") or {}).get("bars")) or {}
        if td and td not in bars:
            notes.append(f"{occ}: no Yahoo bar for the trade date {td} (evidence only: the fill is booked at the owner's premium)")
    curves = set(pack.curves()) | {r[0] for r in conn.execute("SELECT DISTINCT as_of FROM curve_points WHERE series=?", (CURVE_SERIES,)).fetchall()}
    for d in bdays:
        x, seen, ok = d, 0, False
        for _ in range(CURVE_LOOKBACK_BD * 3 + 7):
            if x.isoformat() in curves:
                ok = True
                break
            x -= timedelta(days=1)
            if cal.is_business_day(x):
                seen += 1
                if seen > CURVE_LOOKBACK_BD:
                    break
        if not ok:
            problems.append(f"no UST_PAR curve for {d} or the {CURVE_LOOKBACK_BD} business days before it")
        elif x != d:
            notes.append(f"{d}: no Treasury curve that day; the curve of {x} values it (the jobs' 3-business-day lookback)")

    def series(sid: str) -> List[date]:
        got = {d for d, _v in pack.fred(sid)}
        got |= {date.fromisoformat(r[0]) for r in conn.execute("SELECT as_of FROM curve_points WHERE series=? AND tenor=?", (sid, OBS_TENOR)).fetchall()}
        return sorted(got)
    sofr = series("SOFR")
    for n in _dates(lo, L):
        prior = [d for d in sofr if d <= n]
        if not prior or (n - prior[-1]).days > SOFR_LAG_DAYS:
            problems.append(f"no SOFR print on or within {SOFR_LAG_DAYS} days before the night of {n}")
    for idx in sorted({t["index"] for t in _cdx_terms(batch)}):
        sid = OAS_SERIES.get(idx)
        if sid is None:
            problems.append(f"CDX index {idx} has no proxy series")
            continue
        obs = series(sid)
        have = {d.isoformat() for d in obs}
        for d in bdays:
            if d.isoformat() in have:
                continue
            later = [x for x in obs if d < x <= L]
            if d == L:
                notes.append(f"{sid} for {d} (L) is not published yet: that CDX VM stays CLOSE_PENDING for the live scheduler")
            elif later:
                notes.append(f"{sid} has no value for {d} but a later one ({later[0]}): FRED skipped it; that date's CDX VM is CAUGHT_UP and its move posts with"
                             " the next VM, as live")
            else:
                problems.append(f"{sid}: no value for {d} and none after it through {L} (only L may be unpublished)")
    for sym in _underlyings(batch):
        for ex, amount in pack.dividends(sym):
            if lo < date.fromisoformat(ex) <= L:
                if conn.execute("SELECT 1 FROM dividends WHERE instrument_id=? AND ex_date=?", (sym, ex)).fetchone() is None:
                    problems.append(f"{sym}: an ex-dividend date {ex} ({amount}) falls in the window and the database has no dividend record for it")
    return problems, notes


def preflight(run: Run, rep: dict, cutoff: dict) -> dict:
    """S0 (and S3): every check, refused before any write when one fails."""
    rt, batch, pack = run.rt, run.batch, run.pack
    problems = list(clock_problems(batch, run.clock))
    kinds = set(rt.bus.kinds())
    missing = [k for k in REQUIRED_COMMANDS if k not in kinds]
    if missing:
        problems.append("the running code does not register " + ", ".join(missing))
    today = run.clock.today()
    want_L = rt.cal.prev_business_day(today)
    if pack.through != want_L:
        problems.append(f"the data pack's window ends at {pack.through}, but L for today ({today}) is {want_L}: fetch the pack today")
    if pack.window_from != batch.window_from:
        problems.append(f"the pack's window starts at {pack.window_from}, the batch's at {batch.window_from}")
    if pack.data.get("batch_content_sha256") and pack.data.get("batch_content_sha256") != batch.content_sha256:
        problems.append("the pack was fetched for another revision of the batch")
    lid = batch.lifetime
    notes: List[str] = []
    with rt.db.reader() as conn:
        if conn.execute("SELECT 1 FROM nav_snapshots WHERE account_id LIKE ? AND snap_date=?", (lid + ":%", today.isoformat())).fetchone() is not None:
            problems.append(f"an {lid} snapshot dated today ({today}) exists: the import runs before the day's closing jobs")
        p2, notes = input_problems(conn, batch, pack, rt.cal)
        problems += p2
    acc = rt.accounting
    if hasattr(acc, "is_held") and acc.is_held(lid):
        problems.append(f"{lid} is on an integrity hold")
    integ = acc.integrity("full", lifetime_id=lid)
    if not integ.ok:
        problems.append(f"integrity full is not ok before the import: {', '.join(integ.failing)}")
    for r in rep["lines"]:
        if r["decision"] == "import" and r["status"] not in ("missing_import", "already_imported"):
            problems.append(f"line {r['line_no']}: status {r['status']} - an existing execution matches it; booking it would duplicate it")
        if r["decision"] == "preserve" and r["status"] == "missing_import":
            problems.append(f"line {r['line_no']}: the preserved execution was not found: the batch must be regenerated")
    out = {"ok": not problems, "problems": problems, "notes": notes, "L": want_L.isoformat(), "today": today.isoformat(),
           "integrity_before": {"ok": integ.ok, "failing": list(integ.failing)}, "commands_missing": missing}
    if problems:
        raise ImportRefused("PREFLIGHT", f"S0 refused before any write: {len(problems)} problem(s): " + "; ".join(problems[:5]), out, step="S0")
    return out


def _chain_row(pack: Pack, occ: str) -> dict:
    row = dict(pack.option(occ).get("chain_row") or {})
    if str(row.get("option") or "") != occ:
        raise ImportRefused("ASOF_CHAIN_ROW", f"{occ}: the pack holds no Cboe chain row", step="S3")
    return row


def calibrate_cdx(run: Run, write: bool = True) -> List[dict]:
    """S4: the CDX basis on the PB account derived from the pack: dealt - half-spread x the PB's OTC spread multiplier - proxy (buy protection).
    ``write`` False (an applied batch run again): the stored basis is kept and compared, never changed."""
    from . import prime
    from .books import account_row
    from .products import swaps_cds as cds
    rt = run.rt
    out = []
    for t in _cdx_terms(run.batch):
        idx, acct_id, td = t["index"], t["account_id"], t["trade_date"]
        sid = OAS_SERIES[idx]
        got = rt.rates.as_of_le(sid, date.fromisoformat(td)) if rt.rates is not None else None
        if got is None:
            raise ImportRefused("ASOF_INPUT_MISSING", f"no {sid} observation on or before {td} after S2b (the CDX basis needs it)", step="S4")
        proxy_bp = (Decimal(got[1]) * 100).quantize(cds.BP_Q)
        with rt.db.reader() as conn:
            mine = cds.terms_for(conn, acct_id, idx)
            sched = prime.read_schedule(conn, account_row(conn, acct_id))
        hs = (Decimal(str(mine["half_spread_bp"])) * Decimal(str(sched.otc_spread_mult))).quantize(cds.BP_Q)
        basis = (t["dealt"] - hs - proxy_bp) if t["side"] != "SELL_PROTECTION" else (t["dealt"] + hs - proxy_bp)
        basis = basis.quantize(cds.BP_Q)
        rec = {"index": idx, "account_id": acct_id, "proxy_bp": str(proxy_bp), "proxy_as_of": got[0].isoformat(), "half_spread_bp": str(hs),
               "dealt_bp": str(t["dealt"]), "basis_bp": str(basis), "stored_basis_bp": str(mine["basis_bp"])}
        if Decimal(str(mine["basis_bp"])) == basis:
            rec["action"] = "skipped: the stored basis already equals the derived one"
        elif not write:
            rec["action"] = (f"skipped: the batch is already applied, so the stored basis {mine['basis_bp']} bp is kept (this pack would derive {basis} bp;"
                             " reported, never changed)")
        else:
            res = run.cmd(f"S4:{idx}", "cdx.terms", {"account_id": acct_id, "index": idx, "basis_bp": str(basis)}, f"impset:{run.bid}:S4:{idx}",
                          ok_codes=("NOTHING_TO_UPDATE",), rerun_suffix=True)
            rec["action"] = "set" if res.ok else "done (nothing to update)"
        out.append(rec)
    return out


def _refs_by_import_key(run: Run, key: str) -> dict:
    refs: Dict[str, List[str]] = {}
    for table, col, name in (("otc_contracts", "contract_id", "contract_ids"), ("orders", "order_id", "order_ids"), ("fills", "fill_id", "fill_ids")):
        try:
            got = [r[col] for r in run.q(f"SELECT {col} FROM {table} WHERE import_key=?", (key,))]
        except sqlite3.Error:
            got = []
        if got:
            refs[name] = got
    return refs


def _donor(run: Run, ds: str, need: int, exclude: Sequence[str]) -> Optional[Tuple[str, int]]:
    """The account of the lifetime with the most idle business-date cash at the close of ``ds`` (settled - restricted - payables) that also
    holds ``need`` today; None when none does."""
    from .accounting import cash_view_at
    from .ledger import balance
    best = None
    with run.rt.db.reader() as conn:
        for a in _accounts(run):
            if a["account_id"] in exclude or a["status"] != "OPEN":
                continue
            cv = cash_view_at(conn, a["account_id"], "USD", ds)
            idle = int(cv["settled_c"]) - _restricted(conn, a["account_id"], ds) - int(cv["payable_c"])
            if idle >= need and balance(conn, a["account_id"], "1010", "USD") >= need and (best is None or idle > best[1]):
                best = (a["account_id"], idle)
    return best


def _book(run: Run, step: str, kind: str, payload: dict, rid: str, line_key: str):
    """One booking command; when the as-of funding of its date is short (409 INSUFFICIENT_FUNDS with the as-of date and the shortfall),
    the shortfall is moved first by a dated ``cash.asof_transfer`` from the account with the most idle cash that date (C3's rule, applied to
    the booking's own need) and the booking is retried under the same request id (a refused command stores nothing)."""
    for attempt in range(3):
        res = run.cmd(step, kind, payload, rid, ok_codes=("DUPLICATE_IMPORT", "INSUFFICIENT_FUNDS"))
        if res.ok or res.code != "INSUFFICIENT_FUNDS":
            return res
        det = ((res.body.get("error") or {}).get("details") or {}) if isinstance(res.body, dict) else {}
        ds, short, acct = det.get("asof"), det.get("shortfall_c"), det.get("account_id")
        if not ds or not isinstance(short, int) or short <= 0 or not acct:
            break
        # the need is swept from Treasury as of that date: Treasury is refilled there (its own deficit of that date included)
        tre = f"{run.lid}:TREASURY"
        need = int(short) + max(0, -int(det.get("treasury_available_c") or 0))
        donor = _donor(run, str(ds), need, (acct, tre))
        if donor is None:
            break
        payload_t = {"from_account": donor[0], "to_account": tre, "amount_c": need, "business_date": str(ds), "batch_id": run.bid,
                     "line_key": line_key if attempt == 0 else f"{line_key}:{attempt + 1}",
                     "reason": f"the as-of cash need of {step} on {ds}: Treasury held too little at the close of that business date"}
        run.cmd(f"{step} funding", "cash.asof_transfer", payload_t, f"impcu:{run.bid}:F:{donor[0]}:{ds}:{line_key[-12:]}:{attempt + 1}", rerun_suffix=True)
    raise ImportRefused(res.code or "INSUFFICIENT_FUNDS", f"{step}: {kind} was refused: {res.message}", {"request_id": rid, "details": (res.body.get("error") or {})
                        .get("details") if isinstance(res.body, dict) else None}, step=step)


def _already(run: Run, n: int, ln: dict, request_id: str, package_id: Optional[str] = None) -> dict:
    """A line the report found already imported (its ``import_key`` is on an order, fill or contract): nothing is sent again.  Re-sending
    would not be a replay once the pack differs (an option's chain row is part of its payload: a dry run on a copy that holds the applied
    import, with a later pack, was refused REQUEST_ID_REUSED at line 29), and the line is in the book whatever the pack says."""
    rec = {"action": "already_imported", "refs": _refs_by_import_key(run, str(ln["idem_key"])), "command_id": None, "request_id": request_id}
    rows = run.q("SELECT id FROM commands WHERE request_id=?", (request_id,))
    if rows:
        rec["command_id"] = rows[0]["id"]
    if package_id:
        rec["package_id"] = package_id
    return rec


def _package_id_of(run: Run, package_key: str) -> Optional[str]:
    try:
        rows = run.q("SELECT h.package_id FROM hedge_packages h JOIN commands c ON c.id = h.command_id WHERE c.request_id=?", (package_key,))
    except sqlite3.Error:
        rows = []
    return rows[0]["package_id"] if rows else None


def book_lines(run: Run, statuses: Dict[int, str], which: str = "all") -> Dict[int, dict]:
    """The import lines' bookings (``which`` = ``otc``: the OTC contracts; ``listed``: the listed lines and the package; ``all``): each its own
    command (request id = the idem key; the package its key).  A line the report found ``already_imported`` is not sent again (``_already``)."""
    out: Dict[int, dict] = {}
    batch = run.batch
    for ln in _import_lines(batch):
        n = int(ln["line_no"])
        if ln.get("package_key"):
            continue
        cmd = ln["command"]
        if which == "otc" and cmd["kind"] != "otc.book_as_of" or which == "listed" and cmd["kind"] == "otc.book_as_of":
            continue
        if statuses.get(n) == "already_imported":
            out[n] = _already(run, n, ln, cmd["request_id"])
            continue
        payload = copy.deepcopy(cmd["payload"])
        if cmd["kind"] == "trade.book_as_of" and isinstance(payload.get("instrument_spec"), dict):
            payload["instrument_spec"]["chain_row"] = _chain_row(run.pack, str(payload["instrument_spec"].get("occ") or payload.get("instrument_id")))
        res = _book(run, f"line {n}", cmd["kind"], payload, cmd["request_id"], str(ln["idem_key"]))
        body = res.body if isinstance(res.body, dict) else {}
        if not res.ok:
            refs = _refs_by_import_key(run, ln["idem_key"])
            action = "already_imported"
        else:
            action = "already_imported" if (res.replayed or statuses.get(n) == "already_imported") else "booked"
            if cmd["kind"] == "otc.book_as_of":
                refs = {"contract_ids": [body.get("contract_id")]}
            else:
                refs = {"order_ids": [(body.get("order") or {}).get("order_id")], "fill_ids": [(body.get("fill") or {}).get("fill_id")]}
        out[n] = {"action": action, "refs": refs, "command_id": res.command_id, "request_id": cmd["request_id"]}
    pk = batch.package
    pk_lines = [int(x) for x in (pk or {}).get("lines") or []]
    if pk is not None and which in ("all", "listed") and pk_lines and all(statuses.get(n) == "already_imported" for n in pk_lines):
        pid = _package_id_of(run, str(pk["package_key"]))
        for n in pk_lines:
            out[n] = _already(run, n, batch.lines[n], str(pk["package_key"]), pid)
    elif pk is not None and which in ("all", "listed"):
        payload = copy.deepcopy(pk["command"]["payload"])
        for leg in payload.get("legs") or []:
            if isinstance(leg.get("instrument_spec"), dict):
                leg["instrument_spec"]["chain_row"] = _chain_row(run.pack, str(leg["instrument_spec"].get("occ") or leg.get("instrument_id")))
        res = _book(run, "package", "trade.book_as_of_package", payload, pk["package_key"], str(pk["package_key"]))
        body = res.body if isinstance(res.body, dict) else {}
        orders = body.get("orders") or []
        fills = body.get("fills") or []
        for i, n in enumerate(int(x) for x in pk.get("lines") or []):
            key = batch.lines[n]["idem_key"]
            if res.ok and i < len(orders) and i < len(fills):
                refs = {"order_ids": [orders[i].get("order_id")], "fill_ids": [fills[i].get("fill_id")]}
                action = "already_imported" if (res.replayed or statuses.get(n) == "already_imported") else "booked"
            else:
                refs = _refs_by_import_key(run, key)
                action = "already_imported"
            out[n] = {"action": action, "refs": refs, "command_id": res.command_id, "request_id": pk["package_key"],
                      "package_id": (body.get("package") or {}).get("package_id")}
    return out


def line_groups(batch: Batch, booked: Dict[int, dict]) -> List[dict]:
    groups = []
    for n in sorted(booked):
        ln = batch.lines[n]
        refs = {k: sorted(str(x) for x in v if x) for k, v in (booked[n].get("refs") or {}).items() if v}
        label = f"line {n}: {ln.get('product_label') or ln.get('product')} {ln.get('instrument_id')}"
        groups.append({"key": str(ln["idem_key"]), "label": label[:160], "refs": refs})
    return groups


def catch_up(run: Run, tag: str = "C1") -> dict:
    """C1: every lifecycle job except SNAPSHOT, one ``run_now`` per job in registry order, until a round completes nothing new."""
    from .lifecycle import run_now
    rt = run.rt
    sched = rt.lifecycle
    if sched is None:
        raise ImportRefused("NO_LIFECYCLE", "the maintenance runtime has no lifecycle scheduler", step=tag)
    names = [j.name for j in sched.jobs if j.name != "SNAPSHOT"]
    rounds, failed, skipped = [], [], {}
    by_job: Dict[str, Dict[str, int]] = {}
    for i in range(MAX_CATCH_UP_ROUNDS):
        done = 0
        for name in names:
            res = run_now(rt, name)
            for item in res.get("ran") or []:
                st = str(item.get("status") or "")
                by_job.setdefault(name, {}).setdefault(st, 0)
                by_job[name][st] += 1
                if st == "DONE" and not item.get("replayed"):
                    done += 1
                elif st == "FAILED":
                    failed.append({k: item.get(k) for k in ("job", "key", "note", "code")})
                elif st == "SKIPPED":
                    skipped[f"{item.get('job')}:{item.get('key')}"] = str(item.get("note") or item.get("code") or "")
        rounds.append({"round": i + 1, "done": done})
        if done == 0:
            break
    out = {"rounds": rounds, "by_job": by_job, "failed": failed, "skipped": [{"key": k, "note": v} for k, v in sorted(skipped.items())]}
    if failed:
        raise ImportRefused("CATCH_UP_FAILED", f"{tag}: {len(failed)} lifecycle key(s) failed: " + "; ".join(f"{f['job']} {f['key']}: {f['note']}" for f in failed[:4]),
                            out, step=tag)
    return out


def _accounts(run: Run) -> List[dict]:
    return run.q("SELECT account_id, kind, status, sweep_out, sweep_buffer_c FROM accounts WHERE lifetime_id=? ORDER BY sort_order, account_id", (run.lid,))


def cash_table(run: Run, through: date) -> Dict[str, Dict[str, int]]:
    """Business-date settled cash (GL 1010, natural cents) of every account of the lifetime at the close of every date of the window."""
    from .accounting import cash_view_at
    out: Dict[str, Dict[str, int]] = {}
    days = _dates(run.batch.window_from, through)
    with run.rt.db.reader() as conn:
        for a in _accounts(run):
            out[a["account_id"]] = {d.isoformat(): int(cash_view_at(conn, a["account_id"], "USD", d)["settled_c"]) for d in days}
    return out


def asof_sweeps(run: Run, groups: List[dict], tag: str = "C2") -> List[dict]:
    """C2: ``cash.asof_sweep`` per business date of the window and per account an import line names (Treasury excluded)."""
    accts = sorted({str(ln.get("account_id")) for ln in _import_lines(run.batch)} - {f"{run.lid}:TREASURY"})
    out = []
    for d in _dates(run.batch.window_from, run.pack.through):
        if not run.rt.cal.is_business_day(d):
            continue
        for a in accts:
            payload = {"lifetime_id": run.lid, "accounts": [a], "from_date": d.isoformat(), "through_date": d.isoformat(), "batch_id": run.bid, "groups": groups}
            res = run.cmd(tag, "cash.asof_sweep", payload, f"impcu:{run.bid}:{tag}:{a}:{d.isoformat()}", rerun_suffix=True)
            body = res.body if isinstance(res.body, dict) else {}
            for p in body.get("posted") or []:
                out.append({k: p.get(k) for k in ("account_id", "date", "currency", "sweep_c", "transfer_id", "settled_c", "restricted_c", "payables_c", "buffer_c",
                                                  "import_cash_c")})
    return out


def _restricted(conn, account_id: str, d: Optional[str]) -> int:
    try:
        from .collateral import restricted_c
        return int(restricted_c(conn, account_id, "USD", as_of=d))
    except Exception:   # noqa: BLE001 - without the collateral module nothing is restricted
        return 0


def funding_check(run: Run, through: date, tag: str = "C3") -> List[dict]:
    """C3: every account's business-date settled cash >= 0 at every close of the window through ``through`` and every current balance
    >= 0; a negative is moved by a dated ``cash.asof_transfer`` from the account with the most idle cash that date; else refused."""
    from .accounting import cash_view_at
    from .ledger import balance
    moves = []
    accts = _accounts(run)
    for _ in range(MAX_FUNDING_MOVES):
        table = cash_table(run, through)
        neg = None
        for d in _dates(run.batch.window_from, through):
            for a in accts:
                v = table[a["account_id"]][d.isoformat()]
                if v < 0:
                    neg = (d.isoformat(), a["account_id"], v)
                    break
            if neg:
                break
        if neg is None:
            break
        ds, aid, v = neg
        need = -v
        best = None
        with run.rt.db.reader() as conn:
            for a in accts:
                if a["account_id"] == aid or a["status"] != "OPEN":
                    continue
                cv = cash_view_at(conn, a["account_id"], "USD", ds)
                idle = int(cv["settled_c"]) - _restricted(conn, a["account_id"], ds) - int(cv["payable_c"])
                now_c = balance(conn, a["account_id"], "1010", "USD")
                if idle >= need and now_c >= need and (best is None or idle > best[1]):
                    best = (a["account_id"], idle)
        if best is None:
            raise ImportRefused("ASOF_OVERDRAFT", f"{tag}: {aid} would close {ds} at {_money(v)} and no account of {run.lid} held {_money(need)} of idle cash at the close"
                                f" of that date and today; nothing more was written", {"account_id": aid, "date": ds, "settled_c": v}, step=tag)
        # one line key per move (the transfer's idem key is built on it): a later move of the same pair and date is a new transfer, never a rerun
        k = sum(1 for m in moves if (m["from_account"], m["to_account"], m["business_date"]) == (best[0], aid, ds))
        tail = f":{k + 1}" if k else ""
        payload = {"from_account": best[0], "to_account": aid, "amount_c": need, "business_date": ds, "batch_id": run.bid,
                   "line_key": f"funding:{tag}:{aid}:{ds}{tail}", "reason": f"{aid} would close {ds} at {_money(v)}: the import's as-of cash need of that business date"}
        res = run.cmd(tag, "cash.asof_transfer", payload, f"impcu:{run.bid}:{tag}:{best[0]}:{aid}:{ds}{tail}", rerun_suffix=True)
        body = res.body if isinstance(res.body, dict) else {}
        moves.append({"from_account": best[0], "to_account": aid, "amount_c": need, "business_date": ds, "transfer_id": body.get("transfer_id")})
    else:
        raise ImportRefused("ASOF_OVERDRAFT", f"{tag}: more than {MAX_FUNDING_MOVES} funding moves would be needed; refused", step=tag)
    with run.rt.db.reader() as conn:
        short = [(a["account_id"], balance(conn, a["account_id"], "1010", "USD")) for a in accts]
    short = [(a, v) for a, v in short if v < 0]
    if short:
        raise ImportRefused("ASOF_CURRENT_FUNDS", f"{tag}: current settled cash below zero: " + ", ".join(f"{a} {_money(v)}" for a, v in short)
                            + f"; move {_money(-sum(v for _a, v in short))} into Treasury first (an ordinary transfer), then re-run the dry run",
                            {"short": [{"account_id": a, "settled_c": v} for a, v in short]}, step=tag)
    return moves


def reaccrue(run: Run, before: Dict[str, Dict[str, int]], after: Dict[str, Dict[str, int]]) -> List[dict]:
    """C4: ``cash.reaccrue`` per Treasury / PB account whose business-date balance changed, per kind."""
    out = []
    for a in _accounts(run):
        aid = a["account_id"]
        if before.get(aid) == after.get(aid):
            continue
        kinds = ["cashint"] if a["kind"] == "TREASURY" else ["pbcredit", "pb"] if a["kind"] == "PB" else []
        for k in kinds:
            payload = {"lifetime_id": run.lid, "accounts": [aid], "kinds": [k], "from_night": run.batch.window_from.isoformat(),
                       "reason": f"as-of import {run.bid}: business-date balances", "batch_id": run.bid}
            res = run.cmd("C4", "cash.reaccrue", payload, f"impcu:{run.bid}:C4:{aid}:{k}", rerun_suffix=True)
            body = res.body if isinstance(res.body, dict) else {}
            out.append({"account_id": aid, "kind": k, "total_c": body.get("total_c"), "corrections": len(body.get("corrections") or [])})
    return out


def funding_group(run: Run) -> dict:
    rows = run.q("SELECT transfer_id FROM transfers WHERE idem_key LIKE ? OR idem_key LIKE ? ORDER BY transfer_id",
                 (f"asofsweep:{run.bid}:%", f"asofxfer:{run.bid}:%"))
    return {"key": "funding", "label": FUNDING_LABEL, "refs": {"transfer_ids": [r["transfer_id"] for r in rows]}}


def catch_up_group(run: Run, groups: List[dict]) -> Optional[dict]:
    """The entries the catch-up posted for preserved positions (the server had not run them): named so the restatement explains them."""
    try:
        from . import navhistory as nh
    except Exception:   # noqa: BLE001
        return None
    if not all(hasattr(nh, n) for n in ("AsOfBook", "assign_groups", "batch_window")):
        return None
    rt = run.rt
    with rt.db.reader() as conn:
        accounts = [r[0] for r in conn.execute("SELECT account_id FROM accounts WHERE lifetime_id=?", (run.lid,)).fetchall()]
        try:
            book = nh.AsOfBook(conn, rt.instruments, accounts, rt.sessions)
            owner, _ = nh.assign_groups(conn, book, groups)
            window = nh.batch_window(conn, run.bid, 1 << 60)
        except Exception as e:   # noqa: BLE001 - the restatement itself then reports any residual
            run.notes.append(f"catch-up group not computed: {type(e).__name__}: {e}")
            return None
    interest = {eid for eid, e in book.entries.items() if str(e.get("idem_key") or "").startswith(f"reaccrue:{run.bid}:")}
    left = sorted(eid for eid, e in book.entries.items()
                  if (e.get("actor") == IMPORT_ACTOR or (window is not None and e.get("command_id") is not None and int(e["command_id"]) >= window))
                  and eid not in owner and eid not in interest)
    if not left:
        return None
    return {"key": "catch_up", "label": CATCH_UP_LABEL, "refs": {"entry_ids": left}}


def restate(run: Run, recorded: List[Tuple[str, str]], groups: List[dict]) -> Optional[dict]:
    """C5: ``nav.restate`` of every snapshot date recorded before the first write (the window start .. the last one before today)."""
    today = run.clock.today().isoformat()
    dates = sorted({d for a, d in recorded if a.startswith(run.lid + ":") and run.batch.window_from.isoformat() <= d < today})
    if not dates:
        run.notes.append("C5: no snapshot of the window was recorded before the import: nothing to restate")
        return None
    through = dates[-1]
    payload = {"lifetime_id": run.lid, "from_date": run.batch.window_from.isoformat(), "through_date": through, "batch_id": run.bid, "groups": groups,
               "reason": f"as-of import {run.bid} at the owner's instruction: the imported lines and their funding restated from their value dates"}
    res = run.cmd("C5", "nav.restate", payload, f"impcu:{run.bid}:C5:{run.lid}:{through}", rerun_suffix=True)
    body = res.body if isinstance(res.body, dict) else {}
    return {"through_date": through, "written": body.get("written"), "unchanged": body.get("unchanged"), "replayed": res.replayed}


def funding_status(run: Run, through: date) -> dict:
    """C3 without moving anything (an applied batch run again): every business-date close of the window and every current balance."""
    from .ledger import balance
    table = cash_table(run, through)
    neg = [{"account_id": aid, "date": d, "settled_c": v} for aid, row in sorted(table.items()) for d, v in sorted(row.items()) if v < 0]
    with run.rt.db.reader() as conn:
        short = [{"account_id": a["account_id"], "settled_c": balance(conn, a["account_id"], "1010", "USD")} for a in _accounts(run)]
    return {"negatives": neg, "current_short": [x for x in short if x["settled_c"] < 0], "moves": []}


VERIFY_WHY = ("every import line is already in the book (the batch was applied), so this run only checks and reports: it records no input, sets no"
              " basis, runs no lifecycle job, sweeps, moves or re-accrues no cash and writes no restatement or snapshot - the server runs the book's"
              " own lifecycle from here")


def verify_applied(run: Run, result: dict, statuses: Dict[int, str], rows: List[dict], mnotes: List[str], obs: List[dict], curves: List[dict],
                   through: date) -> Dict[int, dict]:
    """An applied batch run again (a dry run on a copy of the book after the apply, or a second apply): nothing is written to the book.
    Without this, a later pack's newer FRED values would be recorded and the catch-up would post the live scheduler's own pending variation
    margin as the import's; the import's lines, sweeps, re-accruals and restatement are already in the book."""
    rt = run.rt
    with rt.db.reader() as conn:
        result["S2"] = {"rows": len(rows), "notes": mnotes, "result": inputs_compare(conn, {"marks": rows})}
        result["S2b"] = {"observations": len(obs), "curves": len(curves), "result": inputs_compare(conn, {"observations": obs, "curves": curves})}
    result["S3"] = {occ: bool((run.pack.option(occ).get("chain_row") or {}).get("option")) for occ in sorted(_option_specs(run.batch))}
    result["S4"] = calibrate_cdx(run, write=False)
    booked = book_lines(run, statuses, "all")
    result["C1"] = {"rounds": [], "by_job": {}, "failed": [], "skipped": [], "not_run": VERIFY_WHY}
    result["C2"] = []
    result["C3"] = funding_status(run, through)
    result["C4"] = []
    result["C5"] = None
    result["C5b"] = {"ran": [], "not_run": VERIFY_WHY}
    absent_m = result["S2"]["result"]["absent"]
    absent_o = result["S2b"]["result"]["absent"]
    run.notes.append("the batch is already applied: " + VERIFY_WHY)
    if absent_m.get("marks") or absent_o.get("observations") or absent_o.get("curve_points"):
        run.notes.append(f"the pack holds {absent_m.get('marks', 0)} mark(s), {absent_o.get('observations', 0)} observation(s) and"
                         f" {absent_o.get('curve_points', 0)} curve point(s) the book does not hold yet (published after the apply); not recorded here")
    c3 = result["C3"]
    for x in c3["negatives"]:
        run.notes.append(f"{x['account_id']} closes {x['date']} at {_money(x['settled_c'])} (business-date cash below zero)")
    for x in c3["current_short"]:
        run.notes.append(f"{x['account_id']} current settled cash {_money(x['settled_c'])} (below zero)")
    run.say(f"applied batch: {len(booked)} line(s) already in the book; nothing written")
    return booked


def _trial_balance(conn, lid: str) -> dict:
    ids = [r[0] for r in conn.execute("SELECT account_id FROM accounts WHERE lifetime_id=?", (lid,)).fetchall()]
    q = ",".join("?" for _ in ids)
    by = {r["currency"]: {"net_c": int(r["n"] or 0), "base_c": int(r["b"] or 0)} for r in
          conn.execute(f"SELECT currency, SUM(balance_c) n, SUM(base_c) b FROM gl_balances WHERE account_id IN ({q}) GROUP BY currency", ids).fetchall()}
    return {"by_currency": by, "balanced": all(v["net_c"] == 0 for v in by.values()) and sum(v["base_c"] for v in by.values()) == 0}


def _created_refs(run: Run, refs: dict) -> dict:
    """Every row a line created: contract / order / fill ids, their entries, settlements, transfers (linked sweeps) and VM dates."""
    out = {k: list(v) for k, v in refs.items()}
    try:
        from .collateral import group_entry_ids
        with run.rt.db.reader() as conn:
            out["entry_ids"] = sorted(group_entry_ids(conn, [{"refs": refs}]))
    except Exception:   # noqa: BLE001
        pass
    cids = refs.get("contract_ids") or []
    fids = refs.get("fill_ids") or []
    settles, transfers = set(), set()
    if fids:
        q = ",".join("?" for _ in fids)
        settles |= {r["settle_id"] for r in run.q(f"SELECT settle_id FROM settlements WHERE fill_id IN ({q})", fids)}
    with run.rt.db.reader() as conn:
        if cids and "ref_id" in _cols(conn, "settlements"):
            q = ",".join("?" for _ in cids)
            settles |= {r[0] for r in conn.execute(f"SELECT settle_id FROM settlements WHERE ref_id IN ({q})", cids).fetchall()}
    linked = set(cids) | set(fids) | settles
    for r in run.q("SELECT transfer_id, linked_id FROM transfers WHERE linked_id IS NOT NULL"):
        toks = set(str(r["linked_id"]).replace("(", " ").replace(")", " ").replace(",", " ").replace(":", " ").split()) | {str(r["linked_id"])}
        if toks & linked:
            transfers.add(r["transfer_id"])
    out["settle_ids"] = sorted(settles)
    out["transfer_ids"] = sorted(transfers)
    if cids:
        q = ",".join("?" for _ in cids)
        vm: Dict[str, List[str]] = {c: [] for c in cids}
        for r in run.q(f"SELECT contract_id, vm_date FROM otc_vm WHERE contract_id IN ({q}) ORDER BY vm_date", cids):
            vm.setdefault(r["contract_id"], []).append(r["vm_date"])
        out["vm_dates"] = vm
    return out


def _source_label(run: Run, line: dict, refs: dict) -> str:
    """The label Completed Trades shows for the booked row (``blotters.source_label``), else the batch's template with the entry time."""
    try:
        from .blotters import source_label
        with run.rt.db.reader() as conn:
            if refs.get("contract_ids"):
                row = conn.execute("SELECT * FROM otc_contracts WHERE contract_id=?", (refs["contract_ids"][0],)).fetchone()
                if row is not None:
                    return str(source_label(conn, "otc_contract", row)["label"])
            if refs.get("fill_ids"):
                row = conn.execute("SELECT * FROM fills WHERE fill_id=?", (refs["fill_ids"][0],)).fetchone()
                if row is not None:
                    return str(source_label(conn, "fill", row)["label"])
    except Exception:   # noqa: BLE001 - the template below is the same wording
        pass
    return str(line.get("source_label") or "").replace("<apply time>", run.clock.ny().strftime("%Y-%m-%d %H:%M ET"))


def _line_money(run: Run, line: dict, refs: dict) -> dict:
    out: dict = {}
    for cid in refs.get("contract_ids") or []:
        rows = run.q("SELECT * FROM otc_contracts WHERE contract_id=?", (cid,))
        if not rows:
            continue
        c = rows[0]
        try:
            t = json.loads(c.get("terms_json") or "{}")
        except ValueError:
            t = {}
        out = {"contract_id": cid, "kind": c["kind"], "notional_c": c["notional_c"], "im_c": c["im_c"], "start_date": c["start_date"],
               "maturity_date": c["maturity_date"], "reset_px": c.get("reset_px"), "booking": c.get("booking"), "import_key": c.get("import_key"),
               "trade_date": c.get("trade_date"), "value_date": c.get("value_date"), "confirm_status": c.get("confirm_status"),
               "vm_balance_c": c.get("vm_balance_c"), "contract_status": c.get("status"), "terms": {k: t.get(k) for k in ("deal_spread_bp", "upfront_c", "rebate_c", "initial_payment_c", "mark0_c",
                                                                                     "fixed_rate", "curve_spread_bp", "par_rate", "dv01_0_c", "units",
                                                                                     "receiver", "index", "side") if k in t},
               "vm": [{"date": r["vm_date"], "delta_c": r["delta_c"], "mark_c": r["mark_c"]} for r in
                      run.q("SELECT vm_date, delta_c, mark_c FROM otc_vm WHERE contract_id=? ORDER BY vm_date", (cid,))]}
    for fid in refs.get("fill_ids") or []:
        rows = run.q("SELECT f.*, s.status AS settle_status, s.due_date FROM fills f LEFT JOIN settlements s ON s.fill_id=f.fill_id WHERE f.fill_id=?", (fid,))
        if rows:
            f = rows[0]
            out = {"fill_id": fid, "order_id": f.get("order_id"), "side": f["side"], "qty": f["qty"], "price": f["price"], "gross_c": f["gross_c"],
                   "fee_c": f["fee_c"], "trade_date": f["trade_date"], "settle_date": f["settle_date"], "settle_status": f.get("settle_status"),
                   "booking": f.get("booking"), "import_key": f.get("import_key"), "exec_et": _ny_text(f.get("ts"))}
    return out


def _vs_expected(line: dict, money: dict) -> List[dict]:
    """The dry run's figures against the batch's estimates (``expected``): a difference is reported, never forced."""
    exp = line.get("expected") or {}
    out = []

    def cmp(what, want, got_c):
        w = _dec(want)
        if w is None or got_c is None:
            return
        got = Decimal(int(got_c)) / 100
        out.append({"line_no": int(line["line_no"]), "what": what, "estimate": str(w), "computed": str(got), "difference": str(got - w), "equal": got == w})
    if money.get("contract_id"):
        cmp("initial margin", exp.get("im"), money.get("im_c"))
        t = money.get("terms") or {}
        if exp.get("upfront_received") is not None and t.get("upfront_c") is not None:
            cmp("upfront received", exp["upfront_received"], -int(t["upfront_c"]))
        if exp.get("initial_payment_received") is not None and t.get("initial_payment_c") is not None:
            cmp("initial payment received", exp["initial_payment_received"], int(t["initial_payment_c"]))
    if money.get("fill_id"):
        cmp("premium", exp.get("premium"), money.get("gross_c"))
        cmp("commission", exp.get("commission"), money.get("fee_c"))
    return out


def run_import(rt, batch: Batch, pack: Pack, mode: str, clock, log, out, rep: dict, cutoff: dict, extra: dict) -> dict:
    """S0 .. C6 on the runtime's database (a dry-run copy or the live file); returns the report.  ImportRefused stops it."""
    run = Run(rt, batch, pack, mode, clock, log, out)
    today = clock.today()
    through_check = today - timedelta(days=1)
    result: dict = {"batch_id": batch.batch_id, "mode": mode, "started_et": clock.ny().strftime("%Y-%m-%d %H:%M:%S ET"), "status": "RUNNING",
                    "pack": {"path": pack.path, "sha256": pack.sha256, "window": dict(pack.data.get("window") or {}), "fetched_et": pack.data.get("fetched_et")},
                    "cutoff": cutoff, "match_hash": rep["match_hash"], "batch_sha256": batch.sha256, "batch_content_sha256": batch.content_sha256,
                    "batch_expected_sha256": batch.expected_sha256(), "steps": run.steps, "notes": run.notes}
    result.update(extra)
    statuses = {r["line_no"]: r["status"] for r in rep["lines"]}
    with rt.db.reader() as conn:
        recorded = [(r[0], r[1]) for r in conn.execute("SELECT account_id, snap_date FROM nav_snapshots WHERE account_id LIKE ?", (batch.lifetime + ":%",)).fetchall()]
        accrued = {r[0] + ":" + r[1]: r[2] for r in conn.execute("SELECT kind, ref_id, accrued_through FROM accrual_state WHERE ref_id LIKE ?",
                                                                 (batch.lifetime + ":%",)).fetchall()}
        max_entry0 = int(conn.execute("SELECT COALESCE(MAX(entry_id), 0) FROM journal_entries").fetchone()[0])
        transfers0 = {r[0] for r in conn.execute("SELECT transfer_id FROM transfers").fetchall()}
    result["recorded_before"] = {"snapshots": len(recorded), "snapshot_dates": sorted({d for _a, d in recorded}), "accrued_through": accrued,
                                 "max_entry_id": max_entry0}
    cash_before = cash_table(run, through_check)
    result["preflight"] = preflight(run, rep, cutoff)
    run.say(f"S0 ok: L {result['preflight']['L']}, {len(result['preflight']['notes'])} note(s)")
    pending = sorted(int(ln["line_no"]) for ln in _import_lines(batch) if statuses.get(int(ln["line_no"])) != "already_imported")
    verify = not pending
    result["verify_only"] = verify
    batch_row = {"batch_id": batch.batch_id, "status": "RUNNING", "mode_last": mode, "pack_path": pack.path, "pack_sha256": pack.sha256,
                 "source_json": {"path": batch.path, "sha256": batch.sha256, "content_sha256": batch.content_sha256, "lines": len(batch.lines)},
                 "window_from": batch.window_from.isoformat(), "window_through": pack.through.isoformat(), "cutoff_json": cutoff, "match_hash": rep["match_hash"]}
    for k in ("owner_go", "backup_path", "backup_sha256"):
        if extra.get(k) and not verify:          # an applied batch keeps the apply's go and backup on its row; this run's are in its report
            batch_row[k] = extra[k]
    run_tag = clock.ny().strftime("%Y%m%dT%H%M%S")
    if not verify:                     # an applied batch run again writes no row at all: the apply's record stays whole
        run.cmd("begin", "import.batch_record", batch_row, f"impbk:{batch.batch_id}:{mode}:{run_tag}:begin", rerun_suffix=True)
    booked: Dict[int, dict] = {}
    try:
        with rt.db.reader() as conn:
            schema = {"orders.import_key": "import_key" in _cols(conn, "orders"), "fills.import_key": "import_key" in _cols(conn, "fills"),
                      "otc_contracts.import_key": "import_key" in _cols(conn, "otc_contracts"), "settlements.fail_state": "fail_state" in _cols(conn, "settlements"),
                      "nav_snapshot_versions": _table_exists(conn, "nav_snapshot_versions"), "import_lines": _table_exists(conn, "import_lines"),
                      "chart 4451/4453/2345": all(conn.execute("SELECT 1 FROM chart WHERE gl=?", (g,)).fetchone() is not None for g in ("4451", "4453", "2345"))}
        result["S1"] = schema
        if not all(schema.values()):
            raise ImportRefused("SCHEMA", "S1: the schema is not complete: " + ", ".join(k for k, v in schema.items() if not v), schema, step="S1")
        rows, mnotes = mark_rows(pack, batch, rt.cal)
        if verify:
            obs, curves = input_rows(pack, batch)
            booked = verify_applied(run, result, statuses, rows, mnotes, obs, curves, through_check)
            cash_mid = cash_before
            problems = bool(result["C3"]["negatives"] or result["C3"]["current_short"])
        else:
            booked, cash_mid = _import_steps(run, result, statuses, rows, mnotes, recorded, cash_before, through_check)
            problems = False
        integ = rt.accounting.integrity("full", lifetime_id=batch.lifetime)
        with rt.db.reader() as conn:
            tb = _trial_balance(conn, batch.lifetime)
        result["C6"] = {"integrity": {"ok": integ.ok, "failing": list(integ.failing),
                                      "warnings": [p.to_dict() for p in integ.problems if p.severity != "ERROR"][:20]}, "trial_balance": tb}
        result["status"] = "DONE" if integ.ok and tb["balanced"] and not problems else "DONE_WITH_PROBLEMS"
        result["cash_table"] = {"before": cash_before, "before_sweeps": cash_mid, "after": cash_table(run, through_check)}
        result["cash_table_what"] = {"before": "before the import", "before_sweeps": "after the contracts' bookings and their lifecycle (C1a), before the"
                                     " as-of sweeps (C2)", "after": "at the end of the run"}
    except ImportRefused as e:
        result["status"] = "PARTIAL" if mode == "apply" else "REFUSED"
        result["refused"] = e.to_dict()
    return _finish(run, result, rep, booked, max_entry0, transfers0, run_tag, verify)


def _import_steps(run: Run, result: dict, statuses: Dict[int, str], rows: List[dict], mnotes: List[str], recorded: List[Tuple[str, str]],
                  cash_before: Dict[str, Dict[str, int]], through_check: date) -> Tuple[Dict[int, dict], Dict[str, Dict[str, int]]]:
    """S2 .. C5b of a batch with lines still to import (module docstring DRY RUN); returns the bookings and the mid cash table.  A refusal
    propagates (ImportRefused); the bookings made before it are recorded through ``run.booked``."""
    rt, batch, pack = run.rt, run.batch, run.pack
    booked = run.booked
    res = run.cmd("S2", "import.record_inputs", {"batch_id": batch.batch_id, "marks": rows}, f"impset:{batch.batch_id}:S2", rerun_suffix=True)
    result["S2"] = {"rows": len(rows), "result": res.body, "notes": mnotes}
    obs, curves = input_rows(pack, batch)
    res = run.cmd("S2b", "import.record_inputs", {"batch_id": batch.batch_id, "observations": obs, "curves": curves}, f"impset:{batch.batch_id}:S2b",
                  rerun_suffix=True)
    result["S2b"] = {"observations": len(obs), "curves": len(curves), "result": res.body,
                     "reloaded": {"rates": rt.rates.load_persisted() if rt.rates is not None else None,
                                  "curve": rt.curve.load_persisted() if rt.curve is not None else None}}
    result["S3"] = {occ: bool((pack.option(occ).get("chain_row") or {}).get("option")) for occ in sorted(_option_specs(batch))}
    result["S4"] = calibrate_cdx(run)
    run.say("S1-S4 done")
    # the OTC contracts first, then their lifecycle, the as-of sweeps and the business-date funding, so the listed bookings of a later
    # date are funded on that date's real Treasury cash (the CDX initial payments and the variation margin received are swept first)
    booked.update(book_lines(run, statuses, "otc"))
    if booked:
        result["C1a"] = catch_up(run, "C1a")
    # the business-date table after the contracts' bookings and their lifecycle, before any as-of sweep (the overdraft C2 resolves)
    cash_mid = cash_table(run, through_check)
    if booked:
        result["C2a"] = asof_sweeps(run, line_groups(batch, booked), "C2a")
        result["C3a"] = funding_check(run, through_check, "C3a")
    booked.update(book_lines(run, statuses, "listed"))
    run.say(f"bookings: {sum(1 for b in booked.values() if b['action'] == 'booked')} booked, "
            f"{sum(1 for b in booked.values() if b['action'] != 'booked')} already imported")
    groups = line_groups(batch, booked)
    result["C1"] = catch_up(run)
    run.say(f"C1 done in {len(result['C1']['rounds'])} round(s), {len(result['C1']['skipped'])} key(s) waiting")
    result["C2"] = asof_sweeps(run, groups)
    result["C3"] = funding_check(run, through_check)
    cash_after = cash_table(run, through_check)
    result["C4"] = reaccrue(run, cash_before, cash_after)
    all_groups = groups + [funding_group(run)]
    cgroup = catch_up_group(run, all_groups)
    if cgroup is not None:
        all_groups.append(cgroup)
        run.notes.append(f"the catch-up posted {len(cgroup['refs']['entry_ids'])} entr(y/ies) for preserved positions; shown as their own group")
    result["groups"] = [{"key": g["key"], "label": g["label"], "refs": g["refs"]} for g in all_groups]
    result["C5"] = restate(run, recorded, all_groups)
    from .lifecycle import run_now
    snap = run_now(rt, "SNAPSHOT")
    result["C5b"] = {"ran": [{k: x.get(k) for k in ("key", "status", "note")} for x in snap.get("ran") or []]}
    return booked, cash_mid


def _finish(run: Run, result: dict, rep: dict, booked: Dict[int, dict], max_entry0: int, transfers0: set, run_tag: str, verify: bool) -> dict:
    """The line records, the report's figures and the batch row, after the steps (or after a refusal)."""
    rt, batch, clock, mode = run.rt, run.batch, run.clock, run.mode
    booked = booked or run.booked
    result["finished_et"] = clock.ny().strftime("%Y-%m-%d %H:%M:%S ET")
    lines_out, vs = [], []
    for ln in batch.ordered():
        n = int(ln["line_no"])
        r = next((x for x in rep["lines"] if x["line_no"] == n), {})
        b = booked.get(n) or {}
        refs = b.get("refs") or {}
        action = b.get("action") or {"preserve": "preserved (untouched)", "declined": "declined (never booked)",
                                     "unresolved": "unresolved (not a transaction)"}.get(ln.get("decision"), "not booked")
        created = _created_refs(run, refs) if refs else {}
        money = _line_money(run, ln, refs) if refs else {}
        # the batch's booking estimates are checked on the lines this run booked; an already-imported line shows its contract or fill as it
        # stands now (a contract the owner terminated since reads IM 0), which is no booking figure to compare
        vs += _vs_expected(ln, money) if money and action == "booked" else []
        label = _source_label(run, ln, refs) if refs else str(ln.get("source_label") or "")
        lines_out.append({"line_no": n, "idem_key": ln.get("idem_key"), "product": ln.get("product"), "instrument_id": ln.get("instrument_id"),
                          "decision": ln.get("decision"), "status": r.get("status"), "action": action, "refs": created, "money": money, "source_label": label,
                          "diffs": r.get("diffs"), "match": r.get("match")})
        payload = {"batch_id": batch.batch_id, "line_no": n, "idem_key": ln.get("idem_key"), "product": str(ln.get("product") or "CASH"),
                   "instrument_id": money.get("contract_id") or str(ln.get("instrument_id") or "")[:80], "side": str(ln.get("side") or "")[:60],
                   "qty": str(ln.get("qty") or ""), "price": str(ln.get("intended_price") or ""),
                   "trade_date": _iso(ln.get("trade_date") or ln.get("intended_trade_date")), "value_date": _iso(ln.get("value_date")),
                   "settle_date": _iso(ln.get("settle_date")), "account_id": ln.get("account_id"), "counterparty": str(ln.get("counterparty") or "")[:200],
                   "booking": ln.get("booking"), "historical_model": bool(ln.get("historical_model")), "source_json": ln.get("source") or {},
                   "source_label": label, "status": r.get("status") or "unknown", "action": action, "match_json": r.get("match"), "diff_json": r.get("diffs"),
                   "evidence_json": ln.get("evidence"), "created_refs_json": created,
                   "applied_ts": int(clock.epoch()) if action == "booked" and mode == "apply" and result["status"] in ("DONE", "DONE_WITH_PROBLEMS") else None,
                   "note": ln.get("note") or ln.get("decision_reason")}
        if payload["idem_key"] is None:
            payload.pop("idem_key")
        if payload["applied_ts"] is None:
            payload.pop("applied_ts")            # never blanks the time an earlier apply booked the line
        if verify:
            continue                         # the lines keep the apply's record; this run's view of them is in its report
        if action != "booked" and _booked_before(run, n):
            # the run that booked the line keeps its record (action, refs, label, time); this run refreshes only what its search found
            payload = {k: payload[k] for k in ("batch_id", "line_no", "status", "match_json", "diff_json") if k in payload}
        try:
            run.cmd(f"record line {n}", "import.line_record", payload, f"impbk:{batch.batch_id}:{mode}:{run_tag}:L{n}", rerun_suffix=True)
        except ImportRefused as e:
            run.notes.append(f"line {n} not recorded: {e.message}")
    result["lines"] = lines_out
    result["vs_expected"] = vs
    from .ledger import balance
    with rt.db.reader() as conn:
        result["entries_posted"] = int(conn.execute("SELECT COALESCE(MAX(entry_id), 0) FROM journal_entries").fetchone()[0]) - max_entry0
        result["funding_moves"] = [dict(r) for r in conn.execute("SELECT transfer_id, kind, from_account, to_account, amount_c, business_date, linked_type, linked_id,"
                                                                 " idem_key FROM transfers ORDER BY transfer_id").fetchall() if r["transfer_id"] not in transfers0]
        if _table_exists(conn, "nav_snapshot_versions"):
            vrows = [dict(r) for r in conn.execute("SELECT account_id, snap_date, version, nav_c, original_nav_c, added, import_effect_json, interest_effect_c,"
                                                   " mark_policy_c, mark_policy_json, dating_c, dating_json, residual_c FROM nav_snapshot_versions WHERE batch_id=?"
                                                   " ORDER BY snap_date, account_id, version", (batch.batch_id,)).fetchall()]
            for v in vrows:
                for k in ("import_effect_json", "mark_policy_json", "dating_json"):
                    try:
                        v[k[:-5]] = json.loads(v.pop(k) or "[]")
                    except ValueError:
                        v[k[:-5]] = []
            result["restated"] = vrows
        accts = [dict(r) for r in conn.execute("SELECT account_id, kind FROM accounts WHERE lifetime_id=? ORDER BY sort_order", (batch.lifetime,)).fetchall()]
        result["balances"] = [{"account_id": a["account_id"], "settled_c": balance(conn, a["account_id"], "1010", "USD"),
                               "im_posted_c": balance(conn, a["account_id"], "1330", "USD"), "restricted_c": _restricted(conn, a["account_id"], None)} for a in accts]
    result["bridge"] = bridge(batch, result.get("restated") or [])
    if verify:
        run.notes.append("the batch's own record (its lines, the apply's report, the owner's go and the backup) is kept as the apply wrote it;"
                         " this run's report is in its work folder")
        return result
    final = {"batch_id": batch.batch_id, "status": result["status"], "mode_last": mode, "report_json": _compact(result)}
    if mode == "apply" and result["status"] in ("DONE", "DONE_WITH_PROBLEMS"):
        final["applied_ts"] = int(clock.epoch())
    try:
        run.cmd("finish", "import.batch_record", final, f"impbk:{batch.batch_id}:{mode}:{run_tag}:end", rerun_suffix=True)
    except ImportRefused as e:
        run.notes.append(f"the batch row was not updated: {e.message}")
    return result


def _booked_before(run: Run, n: int) -> bool:
    try:
        rows = run.q("SELECT action FROM import_lines WHERE batch_id=? AND line_no=?", (run.bid, n))
    except sqlite3.Error:
        return False
    return bool(rows) and rows[0]["action"] == "booked"


def _compact(result: dict) -> dict:
    """The report stored in ``import_batches.report_json`` (the full match blocks left out)."""
    keep = {k: v for k, v in result.items() if k not in ("lines", "steps")}
    keep["lines"] = [{k: v for k, v in ln.items() if k != "match"} for ln in result.get("lines") or []]
    keep["steps"] = [{k: s.get(k) for k in ("step", "kind", "request_id", "ok", "code", "replayed")} for s in result.get("steps") or []]
    return keep


def _cents_of_text(v) -> Optional[int]:
    """A figure as the batch writes it ('-101,731.44', '+638.16', '25,770,700.86') in cents; None when it is not a number."""
    d = _dec(v)
    return None if d is None else int((d * 100).to_integral_value())


def _effect_names(batch: Batch) -> Dict[str, dict]:
    """Group key -> {line_no, label} of the restated split (each import line is its own group, keyed by its idem key)."""
    out: Dict[str, dict] = {"funding": {"line_no": None, "label": FUNDING_LABEL}, "catch_up": {"line_no": None, "label": CATCH_UP_LABEL}}
    for n, ln in batch.lines.items():
        if ln.get("idem_key"):
            out[str(ln["idem_key"])] = {"line_no": n, "label": f"line {n}: {ln.get('product_label') or ln.get('product')} {ln.get('instrument_id')}"[:160]}
    return out


#: the bridge's own lines after the reconciliation's estimates (each computed by this run)
BRIDGE_RUN_LINES = (("interest_effect_c", "interest restated on business-date balances (Treasury cash interest, PB credit interest)"),
                    ("mark_policy_c", "mark policy: the preserved positions at the day's official closes / settles, not the snapshot's marks"),
                    ("dating_c", "dating: entries the original snapshot counted on another business date (listed in the date's split)"))


def bridge(batch: Batch, restated: List[dict]) -> dict:
    """The restated lifetime row of every date with its split (the import effect by line) and, on the workbook's date (2026-10-23), the
    bridge from the workbook's reconstructed NAV to the restated figure line by line: the reconciliation's own explanation of the gap (the
    app's real executions and models against the workbook's assumptions, estimated before interest and mark policy, batch summary), then
    this run's interest, mark-policy and dating lines; what those leave is its own line ("not explained by the estimates": this run's
    import effect against the estimate the reconciliation used, and rounding).  Nothing is forced (directive section 13)."""
    lid = batch.lifetime
    summ = batch.summary or {}
    wb = (summ.get("restated_nav_10_23") or {}).get("workbook")
    wb_c = _cents_of_text(wb)
    names = _effect_names(batch)
    latest: Dict[str, dict] = {}
    for v in restated:
        if v["account_id"] == f"{lid}:ALL" and (v["snap_date"] not in latest or v["version"] > latest[v["snap_date"]]["version"]):
            latest[v["snap_date"]] = v
    out: dict = {"workbook_nav": wb, "workbook_date": "2026-10-23" if wb else None, "dates": [], "to_workbook": None}
    for ds in sorted(latest):
        v = latest[ds]
        effects = []
        for x in v.get("import_effect") or []:
            nm = names.get(str(x.get("key")), {})
            effects.append({"key": x.get("key"), "line_no": nm.get("line_no"), "label": nm.get("label") or x.get("label") or str(x.get("key")),
                            "amount_c": int(x.get("amount_c") or 0)})
        item = {"date": ds, "original_nav_c": v["original_nav_c"], "restated_nav_c": v["nav_c"], "added": v.get("added"), "import_effect": effects,
                "import_total_c": sum(e["amount_c"] for e in effects), "interest_effect_c": v["interest_effect_c"], "mark_policy_c": v["mark_policy_c"],
                "mark_policy": v.get("mark_policy") or [], "dating_c": v["dating_c"], "dating": v.get("dating") or [], "residual_c": v["residual_c"]}
        out["dates"].append(item)
        if wb_c is None or ds != out["workbook_date"]:
            continue
        gap = summ.get("gap_explained_10_23_A_before_interest_and_mark_policy") or {}
        lines = [{"what": k, "amount_c": c, "source": "the reconciliation's estimate (2026-10-27): the app's executions and models against the workbook's"
                  " assumptions"} for k, c in ((k, _cents_of_text(x)) for k, x in gap.items() if k != "total") if c is not None]
        lines += [{"what": what, "amount_c": int(v[k] or 0), "source": "this run"} for k, what in BRIDGE_RUN_LINES]
        explained = sum(x["amount_c"] for x in lines)
        diff = int(v["nav_c"]) - wb_c
        out["to_workbook"] = {"date": ds, "workbook_nav_c": wb_c, "restated_nav_c": int(v["nav_c"]), "difference_c": diff, "lines": lines,
                              "reconciliation_total_c": _cents_of_text(gap.get("total")), "explained_c": explained, "unexplained_c": diff - explained,
                              "unexplained_what": "this run's import effect vs the estimate the reconciliation used; rounding",
                              "note": "the workbook's figure is a reconstruction on assumed prices: it is never forced and no balancing entry exists"}
    return out


def _signed(c: Optional[int]) -> str:
    if c is None:
        return "-"
    return ("+" if int(c) >= 0 else "") + _money(c)


def _diff_text(d: dict) -> str:
    """One difference in words: what the book holds against the workbook (or the reconciliation), never 'None'."""
    f = str(d.get("field") or "")
    app = d.get("app")
    if isinstance(app, (list, tuple)):
        app = ", ".join(str(x) for x in app)
    if f == "price":
        return f"price: app {app} vs workbook {d.get('workbook')} ({d.get('delta_per_unit')} per unit)"
    if f == "drift":
        return (f"fills: the book holds {app} where the reconciliation matched {d.get('batch')} (fills added or changed since; reported, never"
                " corrected)")
    what = DIFF_WORDS.get(f, f.replace("_", " "))
    if d.get("workbook") is None:
        body = f"{what}: {app}" if app not in (None, "") else what
    else:
        body = f"{what}: app {app} vs workbook {d.get('workbook')}"
    return f"{body} ({d.get('note')})" if d.get("note") else body


DIFF_WORDS = {"qty": "quantity", "trade_date": "trade date", "trade_date_convention": "trade date convention", "existing_contract": "existing contract",
              "existing_fills": "existing fills", "execution": "execution"}


def report_lines(result: dict) -> List[str]:
    """The dry-run / apply report in plain words (the JSON holds everything): the lines, the funding moves, the business-date cash table
    of every account and date, the restated NAV with its split (import effect by line), every account's restated rows and the bridge to
    the workbook."""
    out = [f"{result['batch_id']} - {result['mode']} - {result['status']} ({result.get('started_et')} .. {result.get('finished_et')})",
           f"pack {result['pack']['path']} sha256 {result['pack']['sha256']} window {result['pack']['window'].get('from')} .. {result['pack']['window'].get('through')}",
           f"match_hash {result.get('match_hash')}",
           "cutoff " + ", ".join(f"{k} {(result.get('cutoff') or {}).get(k)}" for k in CUTOFF_COMPARED)]
    if result.get("refused"):
        r = result["refused"]
        out.append(f"REFUSED at {r.get('step')}: {r.get('code')} - {r.get('message')}")
    out.append("lines (decision, status, what the run did):")
    for ln in result.get("lines") or []:
        m = ln.get("money") or {}
        extra = ""
        if m.get("contract_id"):
            now = f" ({str(m['contract_status']).lower()} since)" if m.get("contract_status") not in (None, "OPEN") else ""
            extra = f" {m['contract_id']}{now} IM {_money(m.get('im_c'))} VM balance {_money(m.get('vm_balance_c'))}"
        elif m.get("fill_id"):
            extra = (f" {m['fill_id']} {m.get('side')} {m.get('qty')} @ {m.get('price')} premium {_money(m.get('gross_c'))} fee {_money(m.get('fee_c'))}"
                     f" settles {m.get('settle_date')}")
        out.append(f"  {ln['line_no']:>2} {str(ln.get('product')):<9} {str(ln.get('instrument_id'))[:28]:<28} {str(ln.get('decision')):<10} {str(ln.get('status')):<17}"
                   f" {ln.get('action')}{extra}")
        for d in ln.get("diffs") or []:
            out.append(f"       {_diff_text(d)}")
    out.append("funding moves (every transfer this run created; as-of sweeps and transfers carry their business date):")
    for f in result.get("funding_moves") or []:
        out.append(f"  {f['transfer_id']} {f['kind']} {f['from_account']} -> {f['to_account']} {_money(f['amount_c'])} dated {f['business_date']}"
                   f" ({f.get('linked_type') or ''})")
    ct = result.get("cash_table") or {}
    if ct.get("after"):
        dates = sorted(next(iter(ct["after"].values())).keys())
        out.append("business-date settled cash at the close of each date (USD; before = before the import; mid = after the contracts' bookings and their"
                   " lifecycle, before the as-of sweeps; after = at the end; every account must close every date at or above zero):")
        out.append("  " + f"{'account':<16}{'':<7}" + "".join(f"{d[5:]:>16}" for d in dates))
        for aid in sorted(ct["after"]):
            for tag, key in (("before", "before"), ("mid", "before_sweeps"), ("after", "after")):
                row = (ct.get(key) or {}).get(aid)
                if row is None:
                    continue
                out.append("  " + f"{aid if tag == 'before' else '':<16}{tag:<7}" + "".join(f"{_money(row.get(d)):>16}" for d in dates))
    br = result.get("bridge") or {}
    if br.get("dates"):
        out.append("restated NAV of the lifetime (all accounts): original + import by line + interest + mark policy + dating = restated (residual must be 0):")
        for v in br["dates"]:
            out.append(f"  {v['date']}: original {_money(v['original_nav_c'])} + import {_signed(v.get('import_total_c'))} + interest {_signed(v['interest_effect_c'])}"
                       f" + mark policy {_signed(v['mark_policy_c'])} + dating {_signed(v['dating_c'])} = {_money(v['restated_nav_c'])}"
                       f" (residual {_money(v['residual_c'])})")
            parts = [f"line {e['line_no']} {_signed(e['amount_c'])}" if e.get("line_no") else f"{e.get('key')} {_signed(e['amount_c'])}"
                     for e in v.get("import_effect") or [] if e.get("amount_c")]
            if parts:
                out.append("      import by line: " + "; ".join(parts))
            if v.get("dating"):
                out.append("      dating entries: " + "; ".join(f"{x.get('entry_id')} {x.get('kind')} ({x.get('why')})" for x in v["dating"][:8])
                           + (f"; and {len(v['dating']) - 8} more" if len(v["dating"]) > 8 else ""))
    rs = result.get("restated") or []
    if rs:
        latest: Dict[Tuple[str, str], dict] = {}
        for v in rs:
            k = (v["account_id"], v["snap_date"])
            if k not in latest or v["version"] > latest[k]["version"]:
                latest[k] = v
        dates = sorted({d for _a, d in latest})
        out.append("restated NAV by account (orig = the original snapshot, 0 where none existed; new = the restated version):")
        out.append("  " + f"{'account':<16}{'':<5}" + "".join(f"{d[5:]:>16}" for d in dates))
        for aid in sorted({a for a, _d in latest}):
            for tag, col in (("orig", "original_nav_c"), ("new", "nav_c")):
                out.append("  " + f"{aid if tag == 'orig' else '':<16}{tag:<5}" + "".join(
                    f"{(_money(latest[(aid, d)][col]) if (aid, d) in latest else '-'):>16}" for d in dates))
    tw = br.get("to_workbook")
    if tw:
        out.append(f"bridge from the workbook's reconstructed NAV of {tw['date']} to the restated NAV (never forced):")
        out.append(f"    {'the workbook (reconstructed on assumed prices)':<110}{_money(tw['workbook_nav_c']):>16}")
        for x in tw["lines"]:
            out.append(f"  + {x['what'][:110]:<110}{_signed(x['amount_c']):>16}")
        out.append(f"  + {('not explained by the estimates (' + tw['unexplained_what'] + ')')[:110]:<110}{_signed(tw['unexplained_c']):>16}")
        out.append(f"  = {'the restated NAV (this run)':<110}{_money(tw['restated_nav_c']):>16}")
        out.append(f"    difference to the workbook {_signed(tw['difference_c'])}; the reconciliation's lines are its estimates of 2026-10-27, the other lines"
                   " are this run's")
    c6 = result.get("C6") or {}
    if c6:
        out.append(f"integrity full {'ok' if c6['integrity']['ok'] else 'FAILED ' + ', '.join(c6['integrity']['failing'])}; trial balance "
                   f"{'balanced' if c6['trial_balance']['balanced'] else 'NOT balanced'}; entries posted {result.get('entries_posted')}; WAL bytes {result.get('wal_bytes')}")
    for x in result.get("vs_expected") or []:
        if not x.get("equal"):
            out.append(f"  line {x['line_no']} {x['what']}: computed {x['computed']} vs the plan's estimate {x['estimate']} (difference {x['difference']})")
    for n in result.get("notes") or []:
        out.append(f"note: {n}")
    return out


# ---- dry run, apply, restore ----------------------------------------------------------------------------------------------------
def _work_dir(home: str, batch_id: str, kind: str, clock, given: Optional[str]) -> str:
    path = given or os.path.join(home, "imports", batch_id, f"{kind}-{_stamp(clock)}")
    if os.path.exists(path) and os.listdir(path):
        raise ImportRefused("WORK_DIR_NOT_EMPTY", f"the work folder {path} is not empty: a dry run writes a NEW folder", {"path": path}, step="S0")
    os.makedirs(path, exist_ok=True)
    return path


def _wal_bytes(db_path: str) -> int:
    p = db_path + "-wal"
    return os.path.getsize(p) if os.path.exists(p) else 0


def _write_report(folder: str, result: dict) -> None:
    Path(folder, "report.json").write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    Path(folder, "report.txt").write_text("\n".join(report_lines(result)) + "\n", encoding="utf-8")


def dry_run(batch_path: str, home: str, pack_path: str, work_dir: Optional[str] = None, clock=None, out: Optional[Callable[[str], None]] = print) -> dict:
    """Module docstring DRY RUN.  Returns the report (also written to the work folder)."""
    clock = clock or RealClock()
    batch = load_batch(batch_path)
    pack = load_pack(pack_path)
    src = os.path.join(home, "terminal.db")
    if not os.path.isfile(src):
        raise ImportRefused("NO_DATABASE", f"there is no database at {src}", step="S0")
    locks = _Locks(home, src)
    holder = locks.acquire(_lock_info(batch.batch_id, "dry-run"))
    if holder is not None:
        raise ImportRefused("TERMINAL_RUNNING", f"a FinSim terminal (PID {holder.get('pid') or '?'}) has {home} open: stop it first (python -m finsim stop); nothing"
                            " was read or written", {"holder": holder}, step="S0")
    try:
        problems = clock_problems(batch, clock)
        if problems:
            raise ImportRefused("CLOCK", "; ".join(problems), step="S0")
        work = _work_dir(home, batch.batch_id, "dryrun", clock, work_dir)
        log = FileLog(os.path.join(work, "import.log"))
        src_sha_before = sha256_file(src)
        ro = open_ro(src)
        try:
            src_meta = _source_meta(ro)
            rep = report_on(ro, batch)
            dst = sqlite3.connect(os.path.join(work, "terminal.db"))
            try:
                ro.backup(dst)
            finally:
                dst.close()
        finally:
            ro.close()
        copy_path = os.path.join(work, "terminal.db")
        rt = maintenance_runtime(work, copy_path, pack, clock, log)
        cutoff: dict = {}
        try:
            cutoff = cutoff_fields(rt, src_meta)
            result = run_import(rt, batch, pack, "dry-run", clock, log, out, rep, cutoff, {"home": home, "source_db": src, "work_dir": work, "source_meta": src_meta})
        except ImportRefused as e:
            result = {"batch_id": batch.batch_id, "mode": "dry-run", "status": "REFUSED", "refused": e.to_dict(), "lines": [], "work_dir": work, "home": home,
                      "pack": {"path": pack.path, "sha256": pack.sha256, "window": dict(pack.data.get("window") or {})}, "match_hash": rep["match_hash"],
                      "cutoff": cutoff}
        finally:
            rt.close()
        result["wal_bytes"] = _wal_bytes(copy_path)
        result["source_sha256_before"] = src_sha_before
        result["source_sha256_after"] = sha256_file(src)
        result["source_unchanged"] = result["source_sha256_before"] == result["source_sha256_after"]
        result["report"] = {k: rep[k] for k in ("counts", "match_hash", "state_version", "max_command_id")}
        result["report_lines"] = [{k: r[k] for k in ("line_no", "status", "decision", "diffs")} for r in rep["lines"]]
        result["restore_procedure"] = restore_procedure(home, batch.batch_id)
        _write_report(work, result)
        if out is not None:
            for ln in report_lines(result):
                out(ln)
            out(f"report: {os.path.join(work, 'report.txt')}")
        return result
    finally:
        locks.release()


def find_dry_run(home: str, batch_id: str, pack_sha256: str, given: Optional[str] = None) -> Tuple[str, dict]:
    candidates = [given] if given else sorted((str(p) for p in Path(home, "imports", batch_id).glob("dryrun-*") if p.is_dir()), reverse=True)
    for c in candidates:
        rp = Path(c, "report.json")
        if not rp.is_file():
            continue
        try:
            rep = json.loads(rp.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if (rep.get("pack") or {}).get("sha256") == pack_sha256 or given:
            return str(c), rep
    raise ImportRefused("NO_DRY_RUN", f"no dry run of {batch_id} with this data pack was found under {os.path.join(home, 'imports', batch_id)}: run the dry run first",
                        step="S0")


def backup_live(home: str, batch_id: str, src: str, clock) -> Tuple[str, str]:
    """The pre-import backup (sqlite3 backup API) to ``<home>/backups/import/terminal-pre-<batch>-<ts>.db`` and its sha256, written and re-read."""
    folder = os.path.join(home, "backups", "import")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"terminal-pre-{batch_id}-{_stamp(clock)}.db")
    n = 1
    while os.path.exists(path):
        path = os.path.join(folder, f"terminal-pre-{batch_id}-{_stamp(clock)}-{n}.db")
        n += 1
    ro = open_ro(src)
    try:
        dst = sqlite3.connect(path)
        try:
            ro.backup(dst)
        finally:
            dst.close()
    finally:
        ro.close()
    sha = sha256_file(path)
    Path(path + ".sha256").write_text(sha + "\n", encoding="ascii")
    again = Path(path + ".sha256").read_text(encoding="ascii").strip()
    if again != sha or sha256_file(path) != sha:
        raise ImportRefused("BACKUP_UNVERIFIED", f"the pre-import backup {path} could not be verified", step="S0")
    chk = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        ok = chk.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        chk.close()
    if ok != "ok":
        raise ImportRefused("BACKUP_UNVERIFIED", f"the pre-import backup {path} fails integrity_check: {ok}", step="S0")
    return path, sha


def apply(batch_path: str, home: str, pack_path: str, owner_go: Optional[str], dryrun_dir: Optional[str] = None, clock=None,
          out: Optional[Callable[[str], None]] = print) -> dict:
    """Module docstring APPLY."""
    clock = clock or RealClock()
    if not str(owner_go or "").strip():
        raise ImportRefused("OWNER_GO_REQUIRED", "the apply needs --owner-go with the owner's words; nothing was read or written", step="S0")
    batch = load_batch(batch_path)
    pack = load_pack(pack_path)
    src = os.path.join(home, "terminal.db")
    if not os.path.isfile(src):
        raise ImportRefused("NO_DATABASE", f"there is no database at {src}", step="S0")
    locks = _Locks(home, src)
    holder = locks.acquire(_lock_info(batch.batch_id, "apply"))
    if holder is not None:
        raise ImportRefused("TERMINAL_RUNNING", f"a FinSim terminal (PID {holder.get('pid') or '?'}) has {home} open: stop it first (python -m finsim stop); nothing"
                            " was written", {"holder": holder}, step="S0")
    rt = None
    try:
        problems = clock_problems(batch, clock)
        if problems:
            raise ImportRefused("CLOCK", "; ".join(problems), step="S0")
        ddir, dry = find_dry_run(home, batch.batch_id, pack.sha256, dryrun_dir)
        if dry.get("status") != "DONE":
            raise ImportRefused("DRY_RUN_NOT_CLEAN", f"the dry run in {ddir} ended {dry.get('status')}: fix it and re-run the dry run", step="S0")
        if (dry.get("pack") or {}).get("sha256") != pack.sha256:
            raise ImportRefused("PACK_CHANGED", "the data pack differs from the dry run's: re-run the dry run", step="S0")
        ro = open_ro(src)
        try:
            src_meta = _source_meta(ro)
            rep = report_on(ro, batch)
        finally:
            ro.close()
        if rep["match_hash"] != dry.get("match_hash"):
            raise ImportRefused("MATCH_CHANGED", "the database's matched rows changed since the dry run (match_hash): re-run the dry run", step="S0")
        dc = dry.get("cutoff") or {}
        for k in ("state_version", "max_command_id"):
            if str(src_meta.get(k)) != str(dc.get(k)):
                raise ImportRefused("CUTOFF_CHANGED", f"the database moved since the dry run ({k} {dc.get(k)} -> {src_meta.get(k)}): re-run the dry run", step="S0")
        folder = os.path.join(home, "imports", batch.batch_id, f"apply-{_stamp(clock)}")
        os.makedirs(folder, exist_ok=True)
        log = FileLog(os.path.join(folder, "import.log"))
        bpath, bsha = backup_live(home, batch.batch_id, src, clock)
        if out is not None:
            out(f"pre-import backup {bpath} sha256 {bsha} (verified)")
        # the cutoff is taken on a throwaway copy of the backup, so a refused apply never opens (and so never writes) the live file
        check_home = os.path.join(folder, "cutoff-check")
        os.makedirs(check_home, exist_ok=True)
        check_db = os.path.join(check_home, "terminal.db")
        bsrc = sqlite3.connect(Path(bpath).resolve().as_uri() + "?mode=ro", uri=True)
        try:
            cdst = sqlite3.connect(check_db)
            try:
                bsrc.backup(cdst)
            finally:
                cdst.close()
        finally:
            bsrc.close()
        rt0 = maintenance_runtime(check_home, check_db, pack, clock, FileLog(None))
        try:
            cutoff = cutoff_fields(rt0, src_meta)
        finally:
            rt0.close()
        shutil.rmtree(check_home, ignore_errors=True)
        for k in CUTOFF_COMPARED:
            if str(cutoff.get(k)) != str(dc.get(k)):
                raise ImportRefused("CUTOFF_CHANGED", f"the catch-up cutoff changed since the dry run ({k}): re-run the dry run; nothing was written",
                                    {"dry_run": {x: dc.get(x) for x in CUTOFF_COMPARED}, "now": {x: cutoff.get(x) for x in CUTOFF_COMPARED}}, step="S0")
        rt = maintenance_runtime(home, src, pack, clock, log)
        result = run_import(rt, batch, pack, "apply", clock, log, out, rep, cutoff,
                            {"home": home, "source_db": src, "work_dir": folder, "dry_run": ddir, "owner_go": str(owner_go).strip(), "backup_path": bpath,
                             "backup_sha256": bsha, "source_meta": src_meta})
        rt.close()
        rt = None
        result["wal_bytes"] = _wal_bytes(src)
        result["restore_procedure"] = restore_procedure(home, batch.batch_id, bpath)
        _write_report(folder, result)
        if out is not None:
            for ln in report_lines(result):
                out(ln)
            out(f"report: {os.path.join(folder, 'report.txt')}")
        return result
    finally:
        if rt is not None:
            try:
                rt.close()
            except Exception:   # noqa: BLE001
                pass
        locks.release()


def restore_procedure(home: str, batch_id: str, backup: Optional[str] = None) -> List[str]:
    b = backup or os.path.join(home, "backups", "import", f"terminal-pre-{batch_id}-<ts>.db")
    return ["1. python -m finsim stop; confirm in tasklist that no FinSim python / pythonw process runs and that terminal.lock and terminal.db.lock are gone.",
            f"2. Move terminal.db, terminal.db-wal and terminal.db-shm into {os.path.join(home, 'backups', 'failed-<ts>')} (kept as evidence); a leftover WAL must"
            " never be replayed onto the restored file.",
            f"3. Copy {b} to {os.path.join(home, 'terminal.db')} and verify its sha256 against {b}.sha256.",
            "4. Run PRAGMA integrity_check (must be ok), start FinSim (python -m finsim open; 1.0.5 or later) and confirm the app's integrity check.",
            "5. After an apply this backup, with a 1.0.5 or later release, is the only rollback: 1.0.4 / 1.0.3.1 cannot read the imported rows.",
            f"(python -m finsim.terminal.importer --mode restore --home {home} --backup {b} runs steps 2-4 with the locks held.)"]


def restore(home: str, backup: str, clock=None, out: Optional[Callable[[str], None]] = print) -> dict:
    """Section 7 Restore, steps 2-4, with both locks held (refused while a terminal runs)."""
    clock = clock or RealClock()
    db = os.path.join(home, "terminal.db")
    if not os.path.isfile(backup):
        raise ImportRefused("NO_BACKUP", f"the backup {backup} does not exist", step="restore")
    side = Path(backup + ".sha256")
    want = side.read_text(encoding="ascii").strip() if side.is_file() else None
    got = sha256_file(backup)
    if want and want != got:
        raise ImportRefused("BACKUP_CHANGED", f"the backup's sha256 {got} differs from the recorded {want}", step="restore")
    locks = _Locks(home, db)
    holder = locks.acquire(_lock_info("restore", "restore"))
    if holder is not None:
        raise ImportRefused("TERMINAL_RUNNING", f"a FinSim terminal (PID {holder.get('pid') or '?'}) has {home} open: stop it first; nothing was moved", step="restore")
    try:
        failed = os.path.join(home, "backups", f"failed-{_stamp(clock)}")
        os.makedirs(failed, exist_ok=True)
        moved = []
        for suffix in ("", "-wal", "-shm"):
            p = db + suffix
            if os.path.exists(p):
                shutil.move(p, os.path.join(failed, os.path.basename(p)))
                moved.append(os.path.basename(p))
        shutil.copyfile(backup, db)
        after = sha256_file(db)
        if after != got:
            raise ImportRefused("RESTORE_UNVERIFIED", f"the restored file's sha256 {after} differs from the backup's {got}", step="restore")
        chk = sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True)
        try:
            ok = chk.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            chk.close()
        res = {"restored": db, "from": backup, "sha256": after, "integrity_check": ok, "moved_aside": moved, "failed_folder": failed,
               "next": "start FinSim (python -m finsim open; 1.0.5 or later) and confirm the app's integrity check"}
        if out is not None:
            out(f"restored {db} from {backup} (sha256 {after}, integrity_check {ok}); the previous files are in {failed}")
            out(res["next"])
        if ok != "ok":
            raise ImportRefused("RESTORE_INTEGRITY", f"the restored file fails integrity_check: {ok}", res, step="restore")
        return res
    finally:
        locks.release()


# ---- CLI ----------------------------------------------------------------------------------------------------------------------------
def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m finsim import", description=(
        "FinSim historical import (also python -m finsim.terminal.importer). Modes: report (read-only, any time), dry-run (a copy; before 16:00 ET), "
        "apply (the live file, after a clean dry run with the same pack; before 16:00 ET; a batch already applied is checked and nothing is written), "
        "restore (put the pre-import backup back). --fetch-data-pack fetches the inputs (network, read-only) on the apply day. --home is required: "
        "there is no default home."),
        epilog="Restore: " + " ".join(restore_procedure("<home>", "<batch>")))
    p.add_argument("--batch", help="the batch file (JSONL), e.g. finsim/terminal/data/imports/IMP-F1-20261027-A.jsonl")
    p.add_argument("--home", required=True, help="the FinSim home folder (the owner's procedure passes %%USERPROFILE%%\\.finsim explicitly)")
    p.add_argument("--mode", choices=("report", "dry-run", "apply", "restore"), default=None)
    p.add_argument("--fetch-data-pack", action="store_true", help="fetch the data pack into <home>/imports/<batch>/pack-<ts>.json")
    p.add_argument("--pack", help="the data pack file (dry-run and apply)")
    p.add_argument("--work-dir", help="the dry run's NEW work folder (default <home>/imports/<batch>/dryrun-<ts>)")
    p.add_argument("--dryrun", help="the dry run's folder the apply compares with (default: the newest with the same pack)")
    p.add_argument("--owner-go", help="the owner's words (apply)")
    p.add_argument("--backup", help="the pre-import backup to restore (restore)")
    p.add_argument("--json", help="write the report mode's JSON to this file")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    """``python -m finsim.terminal.importer ...`` (and C18's ``python -m finsim import ...``).  Exit 0 done, 2 refused / usage, 1 failed."""
    p = _parser()
    try:
        args = p.parse_args(list(argv) if argv is not None else None)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 2
    home = os.path.abspath(args.home)
    try:
        if args.fetch_data_pack:
            if not args.batch:
                print("--batch is required")
                return 2
            fetch_data_pack(args.batch, home)
            return 0
        if args.mode is None:
            print("give --mode report | dry-run | apply | restore, or --fetch-data-pack")
            return 2
        if args.mode == "restore":
            if not args.backup:
                print("--backup is required for restore")
                return 2
            restore(home, args.backup)
            return 0
        if not args.batch:
            print("--batch is required")
            return 2
        if args.mode == "report":
            report(args.batch, home, json_path=args.json)
            return 0
        if not args.pack:
            print("--pack is required for the dry run and the apply (fetch it with --fetch-data-pack)")
            return 2
        if args.mode == "dry-run":
            res = dry_run(args.batch, home, args.pack, args.work_dir)
        else:
            res = apply(args.batch, home, args.pack, args.owner_go, args.dryrun)
        return 0 if res.get("status") == "DONE" else 1
    except ImportRefused as e:
        print(f"refused ({e.code}): {e.message}")
        for prob in (e.details or {}).get("problems") or []:
            print(f"  - {prob}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
