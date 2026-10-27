"""Database: DDL, migrations, PRAGMAs, reader pool, writer, backup (TERMINAL_DESIGN.md §2.6, §3).

Conventions
- One SQLite file (``config.db_path()``), WAL, ``synchronous=FULL``, ``foreign_keys=ON``, ``busy_timeout=5000``.
- Integer cents (``*_c``), epoch seconds (``*_ts``), ISO dates (New York), decimal prices as canonical strings
  (never summed in SQL), JSON in TEXT.  No STRICT tables (older SQLite builds still open the file).
- ONE writer connection guarded by a process-wide RLock (``acquire_writer`` / ``release_writer``); only the
  ``CommandBus`` and the short meta writes of ``accounting.integrity`` use it.  Readers come from a pool of four
  ``query_only`` connections and never wait on the writer.
- ``open()``: backup (when the file exists) -> migrate (``meta.schema_version`` gate: a newer file raises
  ``SchemaTooNew`` -> exit 4) -> seed (books, chart, settings, meta) on an empty file, inside one transaction.
- Later packages cannot edit this file: the complete §3 schema lives here, including the swap / private /
  dividend / news tables and the trailing-stop / fill_mode / lock_until / NEGOTIATED / PRIVATE / SWAP columns.
- Build 2 hooks (BUILD2_PLAN C4, 2026-10-21): ``ensure_schema_ext(owner, statements)`` lets a product module add its own
  tables / indexes / columns from its ``bind(rt)`` (idempotent, additive only, logged once); ``open()`` widens the
  ``otc_contracts.kind`` CHECK to ``OTC_KINDS`` (a one-time table rebuild, only when the stored DDL lacks a kind; the file
  stays readable by 1.0.3 and SCHEMA_VERSION does not change); ``reader()`` is re-entrant per thread (ORC-7: a nested
  ``reader()`` on a thread that already holds one gets that same connection, so nested reads never take a second pooled
  connection and can never exhaust the pool of four).
"""
from __future__ import annotations

import json
import os
import queue
import re
import sqlite3
import threading
import time as _time
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator, List, Optional, Tuple

from . import SCHEMA_VERSION, VERSION, config
from .clock import Clock, accounting_day

PRAGMAS = ("PRAGMA journal_mode=WAL", "PRAGMA synchronous=FULL", "PRAGMA foreign_keys=ON", "PRAGMA busy_timeout=5000",
           "PRAGMA temp_store=MEMORY")

_DDL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS commands (
  id            INTEGER PRIMARY KEY,
  request_id    TEXT NOT NULL UNIQUE,
  kind          TEXT NOT NULL, actor TEXT NOT NULL,
  payload_json  TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
  result_json   TEXT NOT NULL, http_status INTEGER NOT NULL DEFAULT 200,
  started_ts    INTEGER NOT NULL, finished_ts INTEGER NOT NULL, duration_ms INTEGER NOT NULL,
  app_version   TEXT NOT NULL, state_version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, command_id INTEGER NOT NULL REFERENCES commands(id),
  ts INTEGER NOT NULL, type TEXT NOT NULL,
  account_id TEXT, instrument_id TEXT, ref_type TEXT, ref_id TEXT, payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_acct ON events(account_id, id);
CREATE INDEX IF NOT EXISTS ix_events_type ON events(type, id);
CREATE TABLE IF NOT EXISTS jobs_done (job TEXT NOT NULL, key TEXT NOT NULL, run_ts INTEGER NOT NULL, command_id INTEGER,
  status TEXT NOT NULL CHECK (status IN ('DONE','SKIPPED')), note TEXT, PRIMARY KEY (job, key));
CREATE TABLE IF NOT EXISTS job_failures (job TEXT NOT NULL, key TEXT NOT NULL, failures INTEGER NOT NULL, last_error TEXT NOT NULL,
  last_ts INTEGER NOT NULL, parked_until_ts INTEGER, PRIMARY KEY (job, key));
CREATE TABLE IF NOT EXISTS attention (
  id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, severity TEXT NOT NULL CHECK (severity IN ('INFO','WARN','ERROR')),
  source TEXT NOT NULL, code TEXT NOT NULL, message TEXT NOT NULL, details_json TEXT,
  account_id TEXT, ref_type TEXT, ref_id TEXT, resolved_ts INTEGER, auto_resolve INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_attention_open ON attention(resolved_ts) WHERE resolved_ts IS NULL;
CREATE TABLE IF NOT EXISTS provider_health (provider TEXT PRIMARY KEY, last_ok_ts INTEGER, last_err_ts INTEGER, last_error TEXT,
  consecutive_errors INTEGER NOT NULL DEFAULT 0, requests_today INTEGER NOT NULL DEFAULT 0, day TEXT, down_until_ts INTEGER);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sequences (name TEXT PRIMARY KEY, next INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS books (book_code TEXT PRIMARY KEY, name TEXT NOT NULL, profile TEXT NOT NULL CHECK (profile IN ('INDIVIDUAL','FUND','INSTITUTION')),
  starting_capital_c INTEGER NOT NULL, sort_order INTEGER NOT NULL, created_ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS lifetimes (
  lifetime_id TEXT PRIMARY KEY, book_code TEXT NOT NULL REFERENCES books(book_code), seq INTEGER NOT NULL, name TEXT NOT NULL,
  started_ts INTEGER NOT NULL, ended_ts INTEGER, starting_capital_c INTEGER NOT NULL, status TEXT NOT NULL CHECK (status IN ('OPEN','CLOSED')),
  final_nav_c INTEGER, final_realised_c INTEGER, final_pnl_c INTEGER, final_quality TEXT, reason TEXT, integrity_json TEXT,
  UNIQUE (book_code, seq)
);
CREATE TABLE IF NOT EXISTS agreements (id INTEGER PRIMARY KEY, lifetime_id TEXT NOT NULL REFERENCES lifetimes(lifetime_id), broker TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('ISDA_MASTER','CSA','PBA')), signed_date TEXT NOT NULL, command_id INTEGER NOT NULL, note TEXT,
  UNIQUE (lifetime_id, broker, kind));
CREATE TABLE IF NOT EXISTS accounts (
  account_id TEXT PRIMARY KEY,
  book_code TEXT NOT NULL REFERENCES books(book_code), lifetime_id TEXT NOT NULL REFERENCES lifetimes(lifetime_id),
  role TEXT NOT NULL,
  name TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('TREASURY','TRADING','PB')),
  broker TEXT, fee_schedule TEXT NOT NULL DEFAULT 'DEFAULT',
  status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','CLOSED')),
  sweep_out INTEGER NOT NULL DEFAULT 1, sweep_buffer_c INTEGER NOT NULL DEFAULT 1000000,
  pb_financing INTEGER NOT NULL DEFAULT 0, pb_limit_c INTEGER NOT NULL DEFAULT 0,
  sort_order INTEGER NOT NULL DEFAULT 100, opened_ts INTEGER NOT NULL, closed_ts INTEGER, settings_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_accounts_lifetime ON accounts(lifetime_id, sort_order);
CREATE TABLE IF NOT EXISTS pb_schedules (account_id TEXT PRIMARY KEY REFERENCES accounts(account_id), schedule_json TEXT NOT NULL, updated_ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS chart (gl TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('ASSET','LIABILITY','EQUITY','INCOME','EXPENSE')),
  per_instrument INTEGER NOT NULL DEFAULT 0, bucket TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS instruments (
  instrument_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('EQUITY','ETF','ADR','REIT','PREFERRED','OPTION','FUTURE','BOND','FX','INDEX','YIELD','PRIVATE','SWAP')),
  symbol TEXT NOT NULL, name TEXT NOT NULL, currency TEXT NOT NULL,
  multiplier TEXT NOT NULL DEFAULT '1', tick TEXT NOT NULL DEFAULT '0.01', lot INTEGER NOT NULL DEFAULT 1,
  price_scale TEXT NOT NULL DEFAULT '1',
  session_group TEXT NOT NULL, settle_days INTEGER NOT NULL DEFAULT 1,
  account_kinds TEXT NOT NULL,
  yahoo_symbol TEXT, yahoo_invert INTEGER NOT NULL DEFAULT 0, cboe_symbol TEXT, underlying_id TEXT,
  expiry TEXT, strike TEXT, right TEXT CHECK (right IN ('C','P')), style TEXT, exercise TEXT,
  spec_json TEXT NOT NULL DEFAULT '{}',
  support TEXT NOT NULL DEFAULT 'FULL' CHECK (support IN ('FULL','LIMITED','COMPUTED','UNSUPPORTED')),
  status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','EXPIRED','DELISTED')),
  created_ts INTEGER NOT NULL, updated_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_instruments_sym ON instruments(symbol);
CREATE INDEX IF NOT EXISTS ix_instruments_under ON instruments(underlying_id, expiry);
CREATE UNIQUE INDEX IF NOT EXISTS ux_instruments_yahoo ON instruments(yahoo_symbol) WHERE yahoo_symbol IS NOT NULL AND kind <> 'OPTION';
CREATE TABLE IF NOT EXISTS exchange_sessions (exchange TEXT NOT NULL, day TEXT NOT NULL, tz TEXT NOT NULL, open_ts INTEGER NOT NULL,
  close_ts INTEGER NOT NULL, fetched_ts INTEGER NOT NULL, PRIMARY KEY (exchange, day));
CREATE TABLE IF NOT EXISTS journal_entries (
  entry_id INTEGER PRIMARY KEY, command_id INTEGER NOT NULL REFERENCES commands(id), event_id INTEGER,
  ts INTEGER NOT NULL, business_date TEXT NOT NULL,
  kind TEXT NOT NULL,
  memo TEXT NOT NULL, ref_type TEXT, ref_id TEXT,
  idem_key TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS journal_lines (
  line_id INTEGER PRIMARY KEY, entry_id INTEGER NOT NULL REFERENCES journal_entries(entry_id), line_no INTEGER NOT NULL,
  account_id TEXT NOT NULL REFERENCES accounts(account_id), gl TEXT NOT NULL REFERENCES chart(gl), currency TEXT NOT NULL,
  amount_c INTEGER NOT NULL CHECK (amount_c <> 0),
  base_c INTEGER NOT NULL,
  fx_rate TEXT NOT NULL, fx_source TEXT NOT NULL,
  instrument_id TEXT, lot_id INTEGER, counterparty TEXT, memo TEXT,
  UNIQUE (entry_id, line_no)
);
CREATE INDEX IF NOT EXISTS ix_lines_acct ON journal_lines(account_id, gl, currency);
CREATE INDEX IF NOT EXISTS ix_lines_inst ON journal_lines(account_id, instrument_id, gl);
CREATE INDEX IF NOT EXISTS ix_lines_entry ON journal_lines(entry_id);
CREATE TABLE IF NOT EXISTS gl_balances (
  account_id TEXT NOT NULL, gl TEXT NOT NULL, currency TEXT NOT NULL, instrument_id TEXT NOT NULL DEFAULT '',
  balance_c INTEGER NOT NULL, base_c INTEGER NOT NULL, PRIMARY KEY (account_id, gl, currency, instrument_id)
);
CREATE TABLE IF NOT EXISTS transfers (
  transfer_id TEXT PRIMARY KEY, command_id INTEGER NOT NULL, ts INTEGER NOT NULL, business_date TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('INITIAL_CAPITAL','DEPOSIT','WITHDRAWAL','TRANSFER','SWEEP_IN','SWEEP_OUT')),
  from_account TEXT, to_account TEXT, currency TEXT NOT NULL, amount_c INTEGER NOT NULL CHECK (amount_c > 0),
  linked_type TEXT, linked_id TEXT, shortfall_c INTEGER,
  entry_id INTEGER NOT NULL REFERENCES journal_entries(entry_id), idem_key TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS orders (
  order_id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, account_id TEXT NOT NULL REFERENCES accounts(account_id),
  instrument_id TEXT NOT NULL REFERENCES instruments(instrument_id),
  side TEXT NOT NULL CHECK (side IN ('BUY','SELL','SELL_SHORT','BUY_TO_COVER','BTO','STO','BTC','STC')),
  qty INTEGER NOT NULL CHECK (qty > 0), filled_qty INTEGER NOT NULL DEFAULT 0, avg_px TEXT,
  order_type TEXT NOT NULL CHECK (order_type IN ('MARKET','LIMIT','STOP','STOP_LIMIT','TRAILING_STOP')), limit_px TEXT, stop_px TEXT,
  trail_amt TEXT, trail_pct TEXT, trail_ref_px TEXT,
  fill_mode TEXT NOT NULL DEFAULT 'SEQUENTIAL' CHECK (fill_mode IN ('SEQUENTIAL','AON')),
  tif TEXT NOT NULL CHECK (tif IN ('DAY','GTC')),
  status TEXT NOT NULL CHECK (status IN ('WORKING','PARTIAL','FILLED','CANCELLED','EXPIRED','SUSPENDED','WAITING_PARENT')),
  triggered INTEGER NOT NULL DEFAULT 0, first_eval_done INTEGER NOT NULL DEFAULT 0, last_bar_ts INTEGER, last_quote_time INTEGER,
  session_group TEXT NOT NULL, created_ts INTEGER NOT NULL, updated_ts INTEGER NOT NULL, expires_ts INTEGER NOT NULL,
  participation TEXT NOT NULL DEFAULT '0.10', borrow_id TEXT, package_id TEXT, parent_order_id TEXT, ratio TEXT,
  fees_c INTEGER NOT NULL DEFAULT 0, reason TEXT, preview_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_orders_open ON orders(status, instrument_id) WHERE status IN ('WORKING','PARTIAL','SUSPENDED','WAITING_PARENT');
CREATE INDEX IF NOT EXISTS ix_orders_acct ON orders(account_id, created_ts);
CREATE TABLE IF NOT EXISTS order_events (id INTEGER PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(order_id), ts INTEGER NOT NULL,
  from_status TEXT, to_status TEXT NOT NULL, note TEXT NOT NULL, quote_json TEXT);
CREATE TABLE IF NOT EXISTS order_rejections (
  id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, request_id TEXT NOT NULL, account_id TEXT, instrument_id TEXT,
  side TEXT, qty INTEGER, order_type TEXT, code TEXT NOT NULL, message TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fills (
  fill_id TEXT PRIMARY KEY, order_id TEXT REFERENCES orders(order_id), account_id TEXT NOT NULL, instrument_id TEXT NOT NULL,
  side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
  qty INTEGER NOT NULL CHECK (qty > 0), price TEXT NOT NULL, gross_c INTEGER NOT NULL, fee_c INTEGER NOT NULL DEFAULT 0,
  currency TEXT NOT NULL, fx_rate TEXT NOT NULL, ts INTEGER NOT NULL, trade_date TEXT NOT NULL, settle_date TEXT,
  origin TEXT NOT NULL CHECK (origin IN ('ORDER','EXERCISE','ASSIGNMENT','EXPIRY','FUTURES_FINAL','MATURITY','NEGOTIATED')),
  price_basis TEXT NOT NULL,
  quote_json TEXT NOT NULL,
  bar_ts INTEGER, cap_qty INTEGER, cap_note TEXT, entry_id INTEGER NOT NULL REFERENCES journal_entries(entry_id),
  idem_key TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS ix_fills_pos ON fills(account_id, instrument_id, ts);
CREATE TABLE IF NOT EXISTS lots (
  lot_id INTEGER PRIMARY KEY, account_id TEXT NOT NULL, instrument_id TEXT NOT NULL, side TEXT NOT NULL CHECK (side IN ('LONG','SHORT')),
  open_fill_id TEXT NOT NULL REFERENCES fills(fill_id), open_ts INTEGER NOT NULL, qty_open INTEGER NOT NULL, qty_remaining INTEGER NOT NULL CHECK (qty_remaining >= 0),
  unit_cost TEXT NOT NULL, cost_c INTEGER NOT NULL,
  currency TEXT NOT NULL, lock_until TEXT
);
CREATE INDEX IF NOT EXISTS ix_lots_open ON lots(account_id, instrument_id, side) WHERE qty_remaining > 0;
CREATE TABLE IF NOT EXISTS lot_relief (id INTEGER PRIMARY KEY, close_fill_id TEXT NOT NULL REFERENCES fills(fill_id), lot_id INTEGER NOT NULL REFERENCES lots(lot_id),
  qty INTEGER NOT NULL, cost_relieved_c INTEGER NOT NULL, proceeds_c INTEGER NOT NULL, realised_c INTEGER NOT NULL);
CREATE VIEW IF NOT EXISTS v_positions AS
  SELECT account_id, instrument_id,
         SUM(CASE WHEN side='LONG' THEN qty_remaining ELSE -qty_remaining END) AS qty,
         SUM(CASE WHEN side='LONG' THEN cost_c ELSE -cost_c END) AS cost_c, MIN(currency) AS currency
  FROM lots WHERE qty_remaining > 0 GROUP BY account_id, instrument_id;
CREATE TABLE IF NOT EXISTS settlements (
  settle_id TEXT PRIMARY KEY, fill_id TEXT REFERENCES fills(fill_id), fx_deal_id TEXT, account_id TEXT NOT NULL,
  leg TEXT NOT NULL,
  currency TEXT NOT NULL, amount_c INTEGER NOT NULL,
  gl TEXT NOT NULL,
  due_date TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','SETTLED')),
  settled_ts INTEGER, entry_id INTEGER, UNIQUE (fill_id, leg), UNIQUE (fx_deal_id, leg)
);
CREATE INDEX IF NOT EXISTS ix_settle_due ON settlements(status, due_date);
CREATE TABLE IF NOT EXISTS borrows (
  borrow_id TEXT PRIMARY KEY, command_id INTEGER NOT NULL, account_id TEXT NOT NULL, instrument_id TEXT NOT NULL,
  qty INTEGER NOT NULL, qty_returned INTEGER NOT NULL DEFAULT 0, fee_rate TEXT NOT NULL, rebate_rate TEXT NOT NULL, tier TEXT NOT NULL,
  lender TEXT NOT NULL, collateral_c INTEGER NOT NULL, currency TEXT NOT NULL, opened_ts INTEGER NOT NULL, opened_date TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','CLOSED')), closed_ts INTEGER, order_id TEXT, reason TEXT,
  last_mark_date TEXT, recall_notice TEXT
);
CREATE TABLE IF NOT EXISTS loans (
  loan_id TEXT PRIMARY KEY, command_id INTEGER NOT NULL, account_id TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('CASH_LOAN','PB_MARGIN')),
  currency TEXT NOT NULL, principal_c INTEGER NOT NULL, index_name TEXT NOT NULL, spread TEXT NOT NULL, day_basis INTEGER NOT NULL,
  opened_date TEXT NOT NULL, maturity_date TEXT, lender TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','CLOSED')), closed_ts INTEGER
);
CREATE TABLE IF NOT EXISTS repos (repo_id TEXT PRIMARY KEY, command_id INTEGER NOT NULL, account_id TEXT NOT NULL, side TEXT NOT NULL,
  instrument_id TEXT NOT NULL, face INTEGER NOT NULL, price TEXT NOT NULL, haircut TEXT NOT NULL, principal_c INTEGER NOT NULL,
  rate TEXT NOT NULL, opened_date TEXT NOT NULL, maturity_date TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'OPEN');
CREATE TABLE IF NOT EXISTS fx_deals (
  deal_id TEXT PRIMARY KEY, command_id INTEGER NOT NULL, account_id TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('SPOT','FORWARD')),
  buy_ccy TEXT NOT NULL, sell_ccy TEXT NOT NULL, buy_c INTEGER NOT NULL, sell_c INTEGER NOT NULL, rate TEXT NOT NULL, mid TEXT NOT NULL,
  spread_cost_c INTEGER NOT NULL, quote_json TEXT NOT NULL, trade_ts INTEGER NOT NULL, trade_date TEXT NOT NULL, value_date TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','SETTLED')), settled_ts INTEGER, entry_id INTEGER NOT NULL, inputs_json TEXT
);
CREATE TABLE IF NOT EXISTS futures_settle (account_id TEXT NOT NULL, instrument_id TEXT NOT NULL, settle_date TEXT NOT NULL,
  settle_px TEXT NOT NULL, qty_after INTEGER NOT NULL, vm_c INTEGER NOT NULL, quote_json TEXT NOT NULL, entry_id INTEGER,
  PRIMARY KEY (account_id, instrument_id, settle_date));
CREATE TABLE IF NOT EXISTS margin_state (account_id TEXT PRIMARY KEY, as_of_ts INTEGER NOT NULL, options_required_c INTEGER NOT NULL,
  options_detail_json TEXT NOT NULL, note TEXT);
CREATE TABLE IF NOT EXISTS accrual_state (
  kind TEXT NOT NULL, ref_id TEXT NOT NULL, segment_start TEXT NOT NULL, base_c INTEGER NOT NULL, rate TEXT NOT NULL,
  day_basis INTEGER NOT NULL, accrued_through TEXT, cum_c INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (kind, ref_id)
);
CREATE TABLE IF NOT EXISTS hedge_packages (package_id TEXT PRIMARY KEY, command_id INTEGER NOT NULL, account_id TEXT NOT NULL, primary_order_id TEXT,
  candidate_id TEXT NOT NULL, strategy_key TEXT, legs_json TEXT NOT NULL, fill_mode TEXT NOT NULL DEFAULT 'SEQUENTIAL',
  status TEXT NOT NULL, created_ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS otc_contracts (
  contract_id TEXT PRIMARY KEY,
  command_id INTEGER NOT NULL, account_id TEXT NOT NULL REFERENCES accounts(account_id), broker TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('IRS','TRS','XCCY','EQ_OPTION','FX_OPTION','EQ_FORWARD','FX_FORWARD','CDX')), terms_json TEXT NOT NULL,
  notional_c INTEGER NOT NULL, currency TEXT NOT NULL, start_date TEXT NOT NULL, maturity_date TEXT NOT NULL,
  margin_mode TEXT NOT NULL CHECK (margin_mode IN ('CTM','STM')), im_c INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','TERMINATED','MATURED')),
  last_reset_date TEXT, reset_px TEXT, mark_c INTEGER NOT NULL DEFAULT 0, mark_ts INTEGER, vm_balance_c INTEGER NOT NULL DEFAULT 0,
  inputs_json TEXT, closed_ts INTEGER
);
CREATE TABLE IF NOT EXISTS otc_vm (contract_id TEXT NOT NULL REFERENCES otc_contracts(contract_id), vm_date TEXT NOT NULL, mark_c INTEGER NOT NULL,
  delta_c INTEGER NOT NULL, mode TEXT NOT NULL, entry_id INTEGER, inputs_json TEXT NOT NULL, PRIMARY KEY (contract_id, vm_date));
CREATE TABLE IF NOT EXISTS otc_resets (contract_id TEXT NOT NULL REFERENCES otc_contracts(contract_id), event_date TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('FIX','PAY','RESET','NOTIONAL_START','NOTIONAL_END','FX_RESET')), amount_c INTEGER NOT NULL,
  detail_json TEXT NOT NULL, entry_id INTEGER, PRIMARY KEY (contract_id, event_date, kind));
CREATE TABLE IF NOT EXISTS private_companies (company_id TEXT PRIMARY KEY, name TEXT NOT NULL, sector TEXT, country TEXT,
  status TEXT NOT NULL CHECK (status IN ('PRIVATE','FILED_IPO','PUBLIC')), ticker TEXT, instrument_id TEXT NOT NULL,
  notes TEXT, updated_ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS private_rounds (id INTEGER PRIMARY KEY, company_id TEXT NOT NULL REFERENCES private_companies(company_id), round_date TEXT NOT NULL,
  round_type TEXT NOT NULL,
  post_money_c INTEGER, price_per_share TEXT, lead_investors TEXT, source_url TEXT NOT NULL, as_of TEXT NOT NULL,
  recorded_by TEXT NOT NULL CHECK (recorded_by IN ('seed','owner')), command_id INTEGER, UNIQUE (company_id, round_date, round_type));
CREATE TABLE IF NOT EXISTS private_loans (
  loan_id TEXT PRIMARY KEY, command_id INTEGER NOT NULL, account_id TEXT NOT NULL REFERENCES accounts(account_id), instrument_id TEXT NOT NULL,
  company_id TEXT, borrower TEXT NOT NULL, currency TEXT NOT NULL, principal_c INTEGER NOT NULL, issue_price TEXT NOT NULL,
  index_name TEXT NOT NULL, spread TEXT NOT NULL, day_basis INTEGER NOT NULL DEFAULT 360, payment_months INTEGER NOT NULL DEFAULT 3,
  start_date TEXT NOT NULL, maturity_date TEXT NOT NULL, next_payment_date TEXT NOT NULL, oas_issue TEXT,
  status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','REPAID')), closed_ts INTEGER
);
CREATE TABLE IF NOT EXISTS dividends (instrument_id TEXT NOT NULL, ex_date TEXT NOT NULL, amount TEXT NOT NULL, pay_date TEXT, source TEXT NOT NULL,
  fetched_ts INTEGER NOT NULL, PRIMARY KEY (instrument_id, ex_date));
CREATE TABLE IF NOT EXISTS news_items (id INTEGER PRIMARY KEY, source TEXT NOT NULL CHECK (source IN ('yahoo','fed','newspaper')), title TEXT NOT NULL,
  publisher TEXT NOT NULL, link TEXT NOT NULL, time_ts INTEGER NOT NULL, tickers_json TEXT NOT NULL DEFAULT '[]', category TEXT,
  fetched_ts INTEGER NOT NULL, UNIQUE (source, link));
CREATE INDEX IF NOT EXISTS ix_news_time ON news_items(time_ts);
CREATE TABLE IF NOT EXISTS marks (
  instrument_id TEXT NOT NULL, mark_date TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('CLOSE','SETTLE','CURVE')),
  price TEXT NOT NULL, currency TEXT NOT NULL, quality TEXT NOT NULL, source TEXT NOT NULL, method TEXT NOT NULL,
  quote_time INTEGER, fetched_at INTEGER NOT NULL, note TEXT, PRIMARY KEY (instrument_id, mark_date, kind)
);
CREATE TABLE IF NOT EXISTS nav_snapshots (
  account_id TEXT NOT NULL, snap_date TEXT NOT NULL, ts INTEGER NOT NULL,
  nav_c INTEGER NOT NULL, settled_c INTEGER NOT NULL, pending_c INTEGER NOT NULL, mv_long_c INTEGER NOT NULL, mv_short_c INTEGER NOT NULL,
  unrealised_c INTEGER NOT NULL, fx_result_c INTEGER NOT NULL, realised_cum_c INTEGER NOT NULL, income_cum_c INTEGER NOT NULL,
  financing_cum_c INTEGER NOT NULL, fees_cum_c INTEGER NOT NULL, capital_cum_c INTEGER NOT NULL, transfers_cum_c INTEGER NOT NULL,
  flows_day_c INTEGER NOT NULL, pnl_day_c INTEGER NOT NULL, quality TEXT NOT NULL, partial INTEGER NOT NULL DEFAULT 0,
  detail_json TEXT NOT NULL, PRIMARY KEY (account_id, snap_date)
);
CREATE TABLE IF NOT EXISTS quote_cache (instrument_id TEXT PRIMARY KEY, record_json TEXT NOT NULL, updated_ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS curve_points (series TEXT NOT NULL, as_of TEXT NOT NULL, tenor TEXT NOT NULL, value TEXT NOT NULL, fetched_ts INTEGER NOT NULL,
  source TEXT NOT NULL, PRIMARY KEY (series, as_of, tenor));
CREATE TABLE IF NOT EXISTS finra_short (symbol TEXT NOT NULL, day TEXT NOT NULL, short_volume INTEGER NOT NULL, total_volume INTEGER NOT NULL, PRIMARY KEY (symbol, day));
CREATE TABLE IF NOT EXISTS watchlists (id INTEGER PRIMARY KEY, account_id TEXT NOT NULL, name TEXT NOT NULL, instrument_id TEXT NOT NULL,
  position INTEGER NOT NULL, UNIQUE (account_id, name, instrument_id));
"""

SCHEMA: List[str] = [s.strip() + ";" for s in _DDL.split(";") if s.strip()]
MIGRATIONS: List[Tuple[int, List[str]]] = [(1, SCHEMA)]

TABLES = ("meta", "commands", "events", "jobs_done", "job_failures", "attention", "provider_health", "settings", "sequences",
          "books", "lifetimes", "agreements", "accounts", "pb_schedules", "chart", "instruments", "exchange_sessions",
          "journal_entries", "journal_lines", "gl_balances", "transfers", "orders", "order_events", "order_rejections", "fills",
          "lots", "lot_relief", "settlements", "borrows", "loans", "repos", "fx_deals", "futures_settle", "margin_state",
          "accrual_state", "hedge_packages", "otc_contracts", "otc_vm", "otc_resets", "private_companies", "private_rounds",
          "private_loans", "dividends", "news_items", "marks", "nav_snapshots", "quote_cache", "curve_points", "finra_short",
          "watchlists")

# Seed settings (§3.1); book_defaults is filled from the seeded books.
DEFAULT_SETTINGS = {
    "participation_rate": "0.10",
    "sweep_out_time_et": "17:30",
    "snapshot_time_et": "17:45",
    "treasury_reserve_c": 0,
    "session_overrides": {},
    "sweep_confirm_threshold_c": 0,                 # SR-12: off unless the owner sets one (the 25 %-of-NAV notional rule still asks)
    "theme": "dark",
    "pb_otc_threshold_c": 2500000000,
    "qp_threshold_c": {"INDIVIDUAL": 500000000, "FUND": 2500000000, "INSTITUTION": 0},
    "private_defaults": {"fee_pct": "0.03", "settle_days": 30, "lockup_months": 6},
    "news_enabled": True,
}

# Id sequences (§3: "order ids O-000001, fill ids F-000001, ...")
PREFIXES = {"order": "O-", "fill": "F-", "transfer": "X-", "settlement": "S-", "borrow": "B-", "loan": "L-", "fx_deal": "D-",
            "swap": "SWP-", "private_loan": "PC-", "package": "PK-", "preview": "pv:"}


#: the OTC contract kinds otc_contracts.kind accepts (C4): swaps (P09) and the PB OTC options / forwards (P12)
OTC_KINDS = ("IRS", "TRS", "XCCY", "EQ_OPTION", "FX_OPTION", "EQ_FORWARD", "FX_FORWARD", "CDX")
_OTC_KIND_CHECK_RE = re.compile(r"kind\s+TEXT\s+NOT\s+NULL\s+CHECK\s*\(\s*kind\s+IN\s*\(([^)]*)\)\s*\)", re.IGNORECASE)
_EXT_ADD_COLUMN_RE = re.compile(r'^\s*ALTER\s+TABLE\s+"?([A-Za-z_][A-Za-z0-9_]*)"?\s+ADD\s+(?:COLUMN\s+)?"?([A-Za-z_][A-Za-z0-9_]*)"?\s', re.IGNORECASE)
_EXT_CREATE_RE = re.compile(r'^\s*CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX|VIEW|TRIGGER)\s+IF\s+NOT\s+EXISTS\s+"?([A-Za-z_][A-Za-z0-9_]*)"?', re.IGNORECASE)


class DatabaseError(Exception):
    pass


class SchemaTooNew(DatabaseError):
    """The file's schema_version is newer than this code (server exit 4)."""


class BusyError(DatabaseError):
    """No reader connection within 2 s (HTTP 503 BUSY)."""


def json_default(o: Any):
    from decimal import Decimal
    from datetime import date as _date
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, (datetime, _date)):
        return o.isoformat()
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    if hasattr(o, "to_dict"):
        return o.to_dict()
    if hasattr(o, "__dataclass_fields__"):
        return {k: getattr(o, k) for k in o.__dataclass_fields__}
    raise TypeError(f"not JSON serialisable: {type(o).__name__}")


def dumps(o: Any) -> str:
    return json.dumps(o, default=json_default, separators=(",", ":"), sort_keys=True)


def canonical_json(o: Any) -> str:
    """Canonical form hashed into ``commands.payload_sha256`` (sorted keys, compact)."""
    return json.dumps(o, default=json_default, separators=(",", ":"), sort_keys=True, ensure_ascii=True)


class Tx:
    """A writer transaction handle: ``conn`` (BEGIN IMMEDIATE already issued), ``now`` (aware UTC), ``today``
    (accounting day, ISO)."""

    def __init__(self, conn: sqlite3.Connection, now: datetime):
        self.conn = conn
        self.now = now
        self.ts = int(now.timestamp())
        self.today = accounting_day(now).isoformat()

    def q(self, sql: str, params=()) -> list:
        return self.conn.execute(sql, params).fetchall()

    def one(self, sql: str, params=()):
        return self.conn.execute(sql, params).fetchone()

    def x(self, sql: str, params=()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)


def next_id(conn: sqlite3.Connection, name: str, width: int = 6) -> str:
    """Allocate the next id of a sequence inside the current transaction: ``O-000001`` ..."""
    prefix = PREFIXES.get(name, name.upper() + "-")
    row = conn.execute("SELECT next FROM sequences WHERE name=?", (name,)).fetchone()
    n = int(row[0]) if row else 1
    conn.execute("INSERT INTO sequences(name, next) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET next=excluded.next", (name, n + 1))
    return f"{prefix}{n:0{width}d}"


def get_setting(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value_json FROM settings WHERE key=?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return default


def set_setting(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute("INSERT INTO settings(key, value_json) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                 (key, dumps(value)))


def futures_margin_override(conn: sqlite3.Connection, root: str) -> Optional[Tuple[int, int]]:
    """SR-11: ``(im_c, mm_c)`` per contract the owner set for a futures root in Settings > Margins & limits
    (``futures_margin_overrides``), else None (the static ``instruments.FUTURES_SPECS`` values apply).  For NEW requirements only."""
    m = get_setting(conn, "futures_margin_overrides", {}) or {}
    d = m.get(str(root or "").upper()) if isinstance(m, dict) else None
    if not isinstance(d, dict):
        return None
    try:
        im, mm = int(d.get("im_c")), int(d.get("mm_c"))
    except (TypeError, ValueError):
        return None
    return (im, mm) if im > 0 and 0 < mm <= im else None


def position_limit_override(conn: sqlite3.Connection, key: str) -> Optional[int]:
    """SR-11: the position limit (contracts) the owner set for a futures root (spot-month limit) or ``OPTIONS`` (the listed-option
    position limit used when no tier is known) in ``position_limit_overrides``, else None (the static table applies)."""
    m = get_setting(conn, "position_limit_overrides", {}) or {}
    v = m.get(str(key or "").upper()) if isinstance(m, dict) else None
    try:
        n = int(v) if v is not None else None
    except (TypeError, ValueError):
        return None
    return n if n is not None and n > 0 else None


def get_meta(conn: sqlite3.Connection, key: str, default: Optional[str] = None) -> Optional[str]:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


class Database:
    def __init__(self, path: str, clock: Clock, log=None):
        self.path = path
        self.clock = clock
        self.log = log
        self._lock = threading.RLock()
        self._owner: Optional[int] = None
        self._depth = 0
        self._writer: Optional[sqlite3.Connection] = None
        self._pool: "queue.Queue[sqlite3.Connection]" = queue.Queue(maxsize=4)
        self._all_readers: List[sqlite3.Connection] = []
        self._opened = False
        self._local = threading.local()          # .conn / .depth: the pooled reader this thread holds (re-entrant reader, ORC-7)
        self._ext_logged: set = set()            # ensure_schema_ext owners already logged

    # ---- connections ------------------------------------------------------------------------------------------------
    def _connect(self, query_only: bool) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        for p in PRAGMAS:
            conn.execute(p)
        if query_only:
            conn.execute("PRAGMA query_only=ON")
        return conn

    def open(self, books: Optional[list] = None) -> None:
        """Backup -> migrate -> PRAGMAs -> seed (empty file).  ``books`` (default ``config.BOOKS``) seeds the books table."""
        if self._opened:
            return
        folder = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(folder, exist_ok=True)
        existed = os.path.exists(self.path) and os.path.getsize(self.path) > 0
        if existed:
            try:
                self.backup(os.path.join(folder, "backups"))
            except Exception as e:   # noqa: BLE001 — a failed backup is logged, never silent
                if self.log:
                    self.log.warning("backup before open failed: %s", e)
        self._writer = self._connect(query_only=False)
        try:
            self._migrate(self._writer, books if books is not None else config.BOOKS)
        except BaseException:
            try:
                self._writer.close()
            finally:
                self._writer = None
            raise
        try:
            self._widen_otc_kinds()
        except Exception:
            try:
                self._writer.close()
            finally:
                self._writer = None
            raise
        if existed:
            self._compact_startup()
        for _ in range(4):
            c = self._connect(query_only=True)
            self._all_readers.append(c)
            self._pool.put(c)
        self._opened = True

    def _widen_otc_kinds(self) -> bool:
        """C4: widen the ``otc_contracts.kind`` CHECK to ``OTC_KINDS`` when the stored DDL lacks one of them (a database
        created by 1.0.3 or earlier).  SQLite cannot alter a CHECK, so the table is rebuilt once with the documented
        procedure (foreign keys off -> new table from the stored DDL with the wider CHECK -> copy every column -> drop ->
        rename -> foreign_key_check -> commit).  The stored DDL is the source, so columns added later stay; nothing else
        changes and SCHEMA_VERSION stays (1.0.3 reads the file as before).  Returns True when it rebuilt the table."""
        conn = self._writer
        row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='otc_contracts'").fetchone()
        if row is None or not row[0]:
            return False
        sql = str(row[0])
        m = _OTC_KIND_CHECK_RE.search(sql)
        if m is None:
            if self.log:
                self.log.warning("otc_contracts: the kind CHECK was not recognised; left as it is")
            return False
        have = [x.strip().strip("'\"") for x in m.group(1).split(",") if x.strip()]
        if all(k in have for k in OTC_KINDS):
            return False
        kinds = list(dict.fromkeys(list(OTC_KINDS) + have))
        new_sql = sql[:m.start()] + "kind TEXT NOT NULL CHECK (kind IN (" + ",".join(f"'{k}'" for k in kinds) + "))" + sql[m.end():]
        tmp_sql, n = re.subn(r'^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"?otc_contracts"?', "CREATE TABLE otc_contracts_widen", new_sql, count=1, flags=re.IGNORECASE)
        if n != 1:
            if self.log:
                self.log.warning("otc_contracts: unexpected stored DDL; the kind CHECK was not widened")
            return False
        cols = ",".join(f'"{r[1]}"' for r in conn.execute("PRAGMA table_info(otc_contracts)").fetchall())
        extra = [r[0] for r in conn.execute("SELECT sql FROM sqlite_master WHERE tbl_name='otc_contracts' AND type IN ('index','trigger') AND sql IS NOT NULL").fetchall()]
        conn.execute("PRAGMA foreign_keys=OFF")
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("DROP TABLE IF EXISTS otc_contracts_widen")
                conn.execute(tmp_sql)
                conn.execute(f"INSERT INTO otc_contracts_widen ({cols}) SELECT {cols} FROM otc_contracts")
                conn.execute("DROP TABLE otc_contracts")
                conn.execute("ALTER TABLE otc_contracts_widen RENAME TO otc_contracts")
                for s in extra:
                    conn.execute(s)
                bad = conn.execute("PRAGMA foreign_key_check").fetchall()
                if bad:
                    raise DatabaseError(f"otc_contracts rebuild: foreign_key_check reports {len(bad)} row(s)")
                conn.execute("COMMIT")
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
        finally:
            conn.execute("PRAGMA foreign_keys=ON")
        if self.log:
            self.log.info("otc_contracts.kind CHECK widened to %s (was %s); rows kept", ",".join(kinds), ",".join(have))
        return True

    def ensure_schema_ext(self, owner: str, statements) -> dict:
        """C4: idempotent, additive DDL for a product module (callable from its ``bind(rt)``): ``CREATE TABLE / INDEX /
        VIEW / TRIGGER IF NOT EXISTS ...`` run as given; ``ALTER TABLE t ADD [COLUMN] c ...`` runs only when ``PRAGMA
        table_info(t)`` lacks ``c``.  Anything else (a DROP, an UPDATE, a CREATE without IF NOT EXISTS) is refused with
        ValueError before anything runs.  One writer transaction (or the caller's own, when this thread already holds the
        writer in one); the first call per owner logs one INFO line.  Returns ``{owner, created, added, present}``."""
        if isinstance(statements, str):
            statements = [statements]
        plan = []
        for s in statements or []:
            s = str(s or "").strip().rstrip(";").strip()
            if not s:
                continue
            m = _EXT_ADD_COLUMN_RE.match(s + " ")
            if m is not None:
                plan.append(("add", s, m.group(1), m.group(2)))
                continue
            m = _EXT_CREATE_RE.match(s)
            if m is not None:
                plan.append(("create", s, m.group(1), None))
                continue
            raise ValueError(f"ensure_schema_ext({owner}): only CREATE ... IF NOT EXISTS and ALTER TABLE ... ADD COLUMN are allowed, got {s[:100]!r}")
        out = {"owner": str(owner), "created": [], "added": [], "present": []}

        def run(conn: sqlite3.Connection) -> None:
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master").fetchall()}
            for kind, s, name, col in plan:
                if kind == "add":
                    cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{name}")').fetchall()}
                    if not cols:
                        raise DatabaseError(f"ensure_schema_ext({owner}): table {name} does not exist ({s[:100]!r})")
                    if col in cols:
                        out["present"].append(f"{name}.{col}")
                        continue
                    conn.execute(s)
                    out["added"].append(f"{name}.{col}")
                else:
                    if name in names:
                        out["present"].append(name)
                        continue
                    conn.execute(s)
                    names.add(name)
                    out["created"].append(name)

        if self.writer_owned_by_current_thread() and self._writer is not None and self._writer.in_transaction:
            run(self._writer)
        else:
            with self.writer() as tx:
                run(tx.conn)
        if self.log and (owner not in self._ext_logged or out["created"] or out["added"]):
            self._ext_logged.add(owner)
            self.log.info("schema ext %s: created %s, added %s, already present %d", owner, out["created"] or "-", out["added"] or "-", len(out["present"]))
        return out

    def _compact_startup(self) -> None:
        """TX-6 (2026-10-21), before any reader, worker or request exists: older builds journaled the whole quote cache as
        ``quotes.persist_batch`` payloads every 60 s; ``quotes.cache.compact_persist_journal`` replaces the payloads older
        than 24 h by a record count (the rows stay) and VACUUMs when that is quick and safe.  A failure is logged, never fatal."""
        try:
            from .quotes.cache import compact_persist_journal
            compact_persist_journal(self._writer, int(self.clock.epoch()), self.path, self.log)
        except Exception as e:   # noqa: BLE001 — the start goes on with the journal as it was
            if self.log:
                self.log.warning("startup compaction of old quotes.persist_batch payloads failed: %s", e)

    def _migrate(self, conn: sqlite3.Connection, books: list) -> None:
        conn.execute("BEGIN IMMEDIATE")
        try:
            has_meta = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='meta'").fetchone() is not None
            version = int(get_meta(conn, "schema_version", "0") or 0) if has_meta else 0
            if version > SCHEMA_VERSION:
                raise SchemaTooNew(f"terminal.db schema_version {version} is newer than this code ({SCHEMA_VERSION})")
            for target, statements in MIGRATIONS:
                if target > version:
                    for s in statements:
                        conn.execute(s)
            now = self.clock.now()
            ts = int(now.timestamp())
            if version == 0:
                set_meta(conn, "schema_version", str(SCHEMA_VERSION))
                set_meta(conn, "state_version", "0")
                set_meta(conn, "created_ts", str(ts))
                set_meta(conn, "instance_id", uuid.uuid4().hex)
                set_meta(conn, "base_ccy", config.BASE_CCY)
                self._seed(conn, books, ts)
            else:
                set_meta(conn, "schema_version", str(SCHEMA_VERSION))
            set_meta(conn, "app_version_last_opened", VERSION)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def _seed(self, conn: sqlite3.Connection, books: list, ts: int) -> None:
        from .ledger import CHART   # lazy: ledger imports nothing from db at module level, but keep the direction explicit
        for gl, spec in CHART.items():
            conn.execute("INSERT OR IGNORE INTO chart(gl, name, kind, per_instrument, bucket) VALUES (?,?,?,?,?)",
                         (gl, spec.name, spec.kind, 1 if spec.per_instrument else 0, spec.bucket))
        defaults = {}
        for i, (code, name, profile, capital_c) in enumerate(books):
            conn.execute("INSERT OR IGNORE INTO books(book_code, name, profile, starting_capital_c, sort_order, created_ts) VALUES (?,?,?,?,?,?)",
                         (code, name, profile, int(capital_c), i * 10, ts))
            defaults[code] = {"name": name, "starting_capital_c": int(capital_c)}
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key, value_json) VALUES (?, ?)", (key, dumps(value)))
        conn.execute("INSERT OR IGNORE INTO settings(key, value_json) VALUES (?, ?)", ("book_defaults", dumps(defaults)))

    def close(self) -> None:
        if not self._opened:
            return
        try:
            if self._writer is not None:
                try:
                    self._writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.Error:
                    pass
                self._writer.close()
        finally:
            for c in self._all_readers:
                try:
                    c.close()
                except sqlite3.Error:
                    pass
            self._all_readers = []
            self._pool = queue.Queue(maxsize=4)
            self._writer = None
            self._opened = False

    # ---- readers --------------------------------------------------------------------------------------------------
    @contextmanager
    def reader(self) -> Iterator[sqlite3.Connection]:
        """A pooled read-only connection (never the writer lock).  If this thread currently holds the writer inside
        a transaction, the writer connection is handed out instead so the read sees the uncommitted state.

        Re-entrant per thread (ORC-7): a ``reader()`` nested inside another one on the same thread gets the connection
        the thread already holds (depth counted) and the connection goes back to the pool only at the outermost exit, so
        nested reads (a view calling accounting calling marks ...) never wait for a second pooled connection."""
        if self.writer_owned_by_current_thread() and self._writer is not None and self._writer.in_transaction:
            yield self._writer
            return
        loc = self._local
        held = getattr(loc, "conn", None)
        if held is not None:
            loc.depth = int(getattr(loc, "depth", 0) or 0) + 1
            try:
                yield held
            finally:
                loc.depth -= 1
            return
        try:
            conn = self._pool.get(timeout=2.0)
        except queue.Empty as e:
            raise BusyError("no reader connection available within 2 s") from e
        loc.conn, loc.depth = conn, 1
        try:
            yield conn
        finally:
            loc.conn, loc.depth = None, 0
            self._pool.put(conn)

    # ---- writer ---------------------------------------------------------------------------------------------------
    def acquire_writer(self, timeout: float = 10.0) -> bool:
        ok = self._lock.acquire(timeout=timeout)
        if ok:
            self._owner = threading.get_ident()
            self._depth += 1
        return ok

    def release_writer(self) -> None:
        self._depth -= 1
        if self._depth == 0:
            self._owner = None
        self._lock.release()

    def writer_owned_by_current_thread(self) -> bool:
        return self._owner == threading.get_ident() and self._depth > 0

    def writer_conn(self) -> sqlite3.Connection:
        if self._writer is None:
            raise DatabaseError("database not open")
        return self._writer

    @contextmanager
    def writer(self, timeout: float = 10.0) -> Iterator[Tx]:
        """Process-wide RLock + BEGIN IMMEDIATE; COMMIT on exit, ROLLBACK on an exception."""
        if not self.acquire_writer(timeout):
            raise BusyError("writer lock busy")
        try:
            conn = self.writer_conn()
            if conn.in_transaction:
                raise DatabaseError("a writer transaction is already open on this thread")
            conn.execute("BEGIN IMMEDIATE")
            tx = Tx(conn, self.clock.now())
            try:
                yield tx
                conn.execute("COMMIT")
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
        finally:
            self.release_writer()

    # ---- misc -----------------------------------------------------------------------------------------------------
    def backup(self, dest_dir: str, keep: int = 10) -> Optional[str]:
        """sqlite3 backup API copy ``terminal-YYYYmmdd-HHMMSS.db``; newest ``keep`` kept."""
        if not os.path.exists(self.path) or os.path.getsize(self.path) == 0:
            return None
        os.makedirs(dest_dir, exist_ok=True)
        stamp = self.clock.ny().strftime("%Y%m%d-%H%M%S")
        dest = os.path.join(dest_dir, f"terminal-{stamp}.db")
        n = 1
        while os.path.exists(dest):
            dest = os.path.join(dest_dir, f"terminal-{stamp}-{n}.db")
            n += 1
        src = sqlite3.connect(self.path, timeout=5.0)
        try:
            dst = sqlite3.connect(dest)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        files = sorted(f for f in os.listdir(dest_dir) if f.startswith("terminal-") and f.endswith(".db"))
        for old in files[:-keep] if len(files) > keep else []:
            try:
                os.remove(os.path.join(dest_dir, old))
            except OSError:
                pass
        return dest

    def state_version(self) -> int:
        with self.reader() as conn:
            return int(get_meta(conn, "state_version", "0") or 0)

    def size(self) -> int:
        try:
            return os.path.getsize(self.path)
        except OSError:
            return 0
