"""The trade blotters (FINSIM1_PLAN P2; contracts C7, C8; 15.3 E1 / E3; 15.7 item 7): the executions of Completed Trades and the
source label of every order, fill and OTC contract.

Release 1.0.5 (phase 1) ships, in this module:
- ``source_label(conn, kind, row) -> {source, label, asof, entered_ts, batch_id, booking, badge}`` (15.7 item 7), kind ``order``,
  ``fill`` or ``otc_contract``.  P3's account orders / fills / position / Activity payloads and P4's contracts payload carry it, and
  the executions below use it:
  - booking ``AS_OF`` (an as-of booking at the owner's instruction, D1): "As-of booking - simulated fill at the <trade date> closing
    print, entered <time> by the operator at the owner's instruction (batch <id>)", or for an OTC contract "As-of booking -
    simulated OTC trade with <dealer>, value date <date>, entered <time> by the operator at the owner's instruction (batch <id>)";
    the batch is the booking command's ``as_of.batch_id`` (the command row is the audit trail);
  - booking ``HISTORICAL_MODEL``: "Historical model (not an independent execution)" - never the as-of label;
  - a system fill (exercise, assignment, expiry, final settlement, maturity): "System (<event>)";
  - otherwise "Simulated fill" (an OTC contract: "Simulated OTC trade with <dealer>", since a contract is not a fill): every
    execution in this terminal is simulated and is never claimed as a real exchange execution (D4, directive section 15).
- ``GET /api/blotter/executions`` (C7, E3): ONE row per execution, never merged - every fill (all origins), every FX deal, every
  OTC contract's booking, termination and maturity and each simulated CDX credit event (1.0.6, P4 task 11: one "credit event" row per
  event, its net settlement amount as the gross), and the financing trades (stock borrows opened / returned, securities lent /
  recalled, repos opened / rolled / closed, cash loans drawn / repaid, private-credit commitments and repayments) with asset class
  "Financing".  Automatic PB financing draws are funding, not trades (the cash pages show them).  Every row names its parent: the
  order id, or ``T-<command id>`` of the command that booked an immediate ticket, a system event or a financing trade (D5).
  Query: ``scope`` (``<lifetime>:ALL`` | ``ALL_BOOKS`` | an account id; default the selected book), ``book``, ``account``, ``from`` /
  ``to`` (trade date, inclusive), ``instrument`` (id, symbol or an option's underlying), ``asset_class`` (comma list; ``!Financing``
  excludes), ``counterparty`` (contains), ``status`` and ``source`` (comma lists of the keys below), ``batch``, ``record`` (fill /
  fx_deal / otc / financing: one kind of execution - the account Orders page's fills section), ``limit`` / ``offset`` (paging, newest
  first), ``format=csv`` (every column; all matching rows unless ``limit`` / ``offset`` are given).  A fill row also carries the
  observation its price came from (``quote``, ``price_basis``, ``cap_note``).

Statuses (``status`` key -> words): executed "Executed"; settling "Settling"; settled "Settled"; failed "Failed"; resolved "Resolved"
(a settlement failure and its resolution, C15); daily_vm "daily VM" (futures); collateral "proceeds applied to the borrow collateral"
(an opening short whose proceeds went to the lender); no_cash_leg "no cash leg" (an expiry at 0); otc_margin "IM posted, CTM daily VM"
(an open OTC contract; STM contracts settle daily); open / closed (a financing trade still open / since closed), rolled, returned,
recalled, repaid; terminated / matured (OTC); credit event "credit event (simulated)" (a CDX constituent default the owner entered).
Sources (``source`` key): simulated, as_of, historical_model, system.

Release 1.0.6 (phase 2, 15.3 E1 / E3; P2 tasks 2, 4, 5 and 6) adds:
- ``GET /api/blotter/pending?scope=&book=&account=&asset_class=&status=&instrument=&limit=&offset=`` (C7, OMS-01 / OMS-05 / OMS-06):
  ``status`` open (default: WORKING, PARTIAL, SUSPENDED and WAITING_PARENT orders plus the open OTC quote requests of
  ``swaps.rfq_rows`` once that exists), filled, cancelled, expired, rejected (``order_rejections``, every ticket), ended (filled,
  cancelled or expired: the account Orders page's history) or all (the order history).  Every row carries the directive's Pending-Trades fields (section 3A): id, instrument, asset class, side in words, type with
  its limit / stop / trail, original / filled / open quantity, average fill, the current market price from the quote cache (never
  fetched) with its quality and age and the distance to the limit / stop, submitted time, expiration in words, account and book,
  counterparty, the cash the order reserves (C9a), the reason, and its STAGE (derived, never stored): CREATED (the preview) ->
  SUBMITTED (resting for the session or a fresh quote) / QUEUED (a next-session order) -> WORKING -> PARTIALLY FILLED -> FILLED, or
  HELD (waiting for funds / a refused fill), WAITING PARENT (a hedge leg), AWAITING DEALER QUOTE / AWAITING YOUR ACCEPTANCE (an OTC
  quote request), CANCELLED, EXPIRED, REJECTED.  Package legs stay one row each, grouped by package, with the package's state
  derived from its legs (a package whose legs all ended is never open, whatever its stored status says; nothing is written).
- ``counts(services, scope)`` -> ``{working, partial, held, queued, waiting_parent, rfq_open, rejected_today, settling, failed}`` (the
  Dashboard and sidebar badges); ``GET /api/blotter/counts?book=``.
- ``execution_detail(services, execution_id)`` = ``GET /api/executions/{id}`` (OMS-14): the execution, its parent order and events
  (or the ticket's command), the journal entries by reference (the fill, its settlement, the borrow collateral, the sweeps, an OTC
  contract's IM / upfront / VM), linked transfers, settlement rows with their state (C15), lots opened and relieved with the
  realised P&L, the current position, and the audit trail (command, actor, time, release, payload sha256, booking / entered / batch).
  It is the C6 probe of the "simulated counterparty settlement fails" row: the drawer offers the owner's fail / resolve (C15).
- ``GET /api/orders/{id}`` takes over the order route (``order_detail``): the order with its fills, events, entries, settlements,
  the timeline (created -> submitted -> working -> each fill -> the final state), linked sweeps, borrow, package legs and the stored
  preview's summary; ``?review=1`` adds the pure review (``orders_rules.review``: "would fill N at P now" or why not; OMS-08).
  ``PATCH /api/orders/{id}`` amends (``order.amend``), ``POST /api/orders/{id}/replace`` replaces (``order.replace``; OMS-03).
- The stage vocabulary is published as the ORDER_STAGES / EXECUTION_STAGES conventions (``tables.add_conventions``).

The module has no commands and no schema of its own (C8's columns are added by ``orders.bind``); it reads optional columns and
tables (``booking``, the C15 settlement fail columns, ``sec_loans``, ``rfqs``) only when they exist, so it imports, registers and
serves on any database a 1.0.x release opened (15.2 step 3, S-14).
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import datetime
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import config
from .clock import accounting_day, ny_of, ny_str
from .units import from_c

AS_OF = "AS_OF"
HISTORICAL_MODEL = "HISTORICAL_MODEL"
SIMULATED_LABEL = "Simulated fill"
HISTORICAL_MODEL_LABEL = "Historical model (not an independent execution)"
SYSTEM_EVENTS = {"EXERCISE": "exercise", "ASSIGNMENT": "assignment", "EXPIRY": "expiry", "FUTURES_FINAL": "final settlement", "MATURITY": "maturity"}
SOURCE_KEYS = ("simulated", "as_of", "historical_model", "system")
SOURCE_ALIASES = {"asof": "as_of", "as-of": "as_of", "model": "historical_model", "historical": "historical_model", "sim": "simulated", "live": "simulated"}
STATUS_WORDS = {"executed": "Executed", "settling": "Settling", "settled": "Settled", "failed": "Failed", "resolved": "Resolved", "daily_vm": "daily VM",
                "collateral": "proceeds applied to the borrow collateral", "no_cash_leg": "no cash leg", "otc_margin": "IM posted, CTM daily VM",
                "open": "open", "closed": "closed", "rolled": "rolled", "returned": "returned", "recalled": "recalled", "repaid": "repaid",
                "terminated": "terminated", "matured": "matured", "credit event": "credit event (simulated)"}
SIDE_WORDS = {"BUY": "BUY", "SELL": "SELL", "SELL_SHORT": "SELL SHORT", "BUY_TO_COVER": "BUY TO COVER", "BTO": "BTO", "STO": "STO", "BTC": "BTC", "STC": "STC"}
ASSET_CLASS = {"EQUITY": "Equity", "ADR": "Equity", "REIT": "Equity", "PREFERRED": "Equity", "ETF": "ETF", "OPTION": "Option", "FUTURE": "Future",
               "BOND": "Bond", "FX": "FX", "PRIVATE": "Private", "SWAP": "OTC"}
ASSET_CLASSES = ("Equity", "ETF", "Option", "Future", "Bond", "FX", "OTC", "Private", "Financing")
EQUITY_KINDS = ("EQUITY", "ETF", "ADR", "REIT", "PREFERRED")
#: the financing events that are trades (C7): event type -> (verb for the side column, status key of the row)
FINANCING_EVENTS = {"BORROW_OPENED": ("BORROW", None), "BORROW_RETURNED": ("RETURN", "returned"), "SECURITIES_LENT": ("LEND", None),
                    "SECURITIES_RECALLED": ("RECALL", "recalled"), "REPO_OPENED": (None, None), "REPO_ROLLED": ("ROLL", "rolled"),
                    "REPO_CLOSED": ("CLOSE", "closed"), "LOAN_DRAWN": ("DRAW", None), "LOAN_REPAID": ("REPAY", "repaid"),
                    "PC_COMMITTED": ("COMMIT AND FUND", None), "PC_REPAID": ("REPAYMENT", "repaid"), "PC_PAID": ("REPAYMENT AT MATURITY", "repaid")}
OPENING_EVENTS = ("BORROW_OPENED", "SECURITIES_LENT", "REPO_OPENED", "LOAN_DRAWN", "PC_COMMITTED")
OTC_EVENTS = {"OTC_TERMINATED": "terminated", "SWAP_MATURED": "matured", "OTC_EXPIRED": "matured", "CDX_CREDIT_EVENT": "credit event"}
#: the CSV columns, in order: (header, row key, how) - how: "c" money cents, "ts" New York time, else the value as is
CSV_COLUMNS = (("execution_id", "execution_id", ""), ("parent", "parent_id", ""), ("account_id", "account_id", ""), ("account", "account_name", ""),
               ("book", "book", ""), ("instrument_id", "instrument_id", ""), ("instrument", "symbol", ""), ("asset_class", "asset_class", ""),
               ("side", "side_words", ""), ("quantity", "qty", ""), ("notional", "notional_c", "c"), ("currency", "currency", ""), ("price", "price", ""),
               ("price_kind", "price_label", ""), ("gross", "gross_c", "c"), ("fees", "fee_c", "c"), ("fees_ledger", "fees_journal_c", "c"),
               ("execution_time", "exec_ts", "ts"), ("trade_date", "trade_date", ""), ("settle_date", "settle_date", ""), ("settled_time", "settled_ts", "ts"),
               ("counterparty", "counterparty", ""), ("status", "status_label", ""), ("source", "source_label", ""), ("entered_time", "entered_ts", "ts"),
               ("batch", "batch_id", ""), ("record", "kind", ""), ("origin", "origin", ""), ("booking", "booking", ""), ("note", "note", ""))


# ---- small helpers -----------------------------------------------------------------------------------------------------------
#: C7 (P2 task 2): the derived stages of an order (and an OTC quote request) -> words; the stage chip (C4 ``.stage-*``) of each
STAGE_WORDS = {"CREATED": "created", "SUBMITTED": "submitted", "QUEUED": "queued", "WORKING": "working", "PARTIALLY_FILLED": "partially filled", "HELD": "held",
               "WAITING_PARENT": "waiting for its parent", "AWAITING_DEALER_QUOTE": "awaiting the dealer's quote",
               "AWAITING_ACCEPTANCE": "awaiting your acceptance", "FILLED": "filled", "CANCELLED": "cancelled", "EXPIRED": "expired", "REJECTED": "rejected"}
STAGE_CHIP = {"CREATED": "queued", "SUBMITTED": "queued", "QUEUED": "queued", "WORKING": "working", "PARTIALLY_FILLED": "partial", "HELD": "held",
              "WAITING_PARENT": "queued", "AWAITING_DEALER_QUOTE": "queued", "AWAITING_ACCEPTANCE": "working", "FILLED": "filled", "CANCELLED": "cancelled",
              "EXPIRED": "expired", "REJECTED": "rejected"}
#: the Pending blotter's status filter (C7) -> the order statuses it lists (rejected: order_rejections; all: every order and rejection)
PENDING_FILTERS = {"open": ("WORKING", "PARTIAL", "SUSPENDED", "WAITING_PARENT"), "filled": ("FILLED",), "cancelled": ("CANCELLED",), "expired": ("EXPIRED",),
                   "rejected": (), "all": ("WORKING", "PARTIAL", "SUSPENDED", "WAITING_PARENT", "FILLED", "CANCELLED", "EXPIRED"),
                   # the account Orders page's history section: every order that has ended (filled, cancelled or expired)
                   "ended": ("FILLED", "CANCELLED", "EXPIRED")}
LIVE_ORDER = ("WORKING", "PARTIAL", "SUSPENDED", "WAITING_PARENT")
TYPE_WORDS = {"MARKET": "MKT", "LIMIT": "LMT", "STOP": "STP", "STOP_LIMIT": "STP LMT", "TRAILING_STOP": "TRAIL"}
RFQ_OPEN = ("REQUESTED", "QUOTED")
TICKET_CLASS = {"bonds.trade": "Bond", "fx.spot": "FX", "fx.forward": "FX", "otc.book": "OTC", "otc.terminate": "OTC", "otc.rfq.request": "OTC",
                "otc.rfq.accept": "OTC", "otc.rfq.decline": "OTC", "private.trade": "Private"}
_STAGE_CONVENTIONS = {
    "ORDER_STAGES": "An order goes created (its preview) -> submitted -> working -> partially filled -> filled, or ends cancelled, rejected or expired;"
                    " queued = a next-session order waiting for its session, held = waiting for funds or after a refused fill, waiting parent = a hedge"
                    " leg released when its primary fills; an OTC quote request waits for the dealer's quote, then for your acceptance.",
    "EXECUTION_STAGES": "An execution goes executed -> pending settlement -> settled, or settlement failed -> resolved; futures settle daily through"
                        " variation margin; an OTC contract posts initial margin and settles its value every day (CTM).",
}
try:                                    # the stage vocabulary is published with the conventions (C7); setdefault semantics, never an error at import
    from . import tables as _tables
    _tables.add_conventions(_STAGE_CONVENTIONS)
except Exception:   # noqa: BLE001 - a missing hook leaves the sentences out, never the module
    pass


def _get(row, key: str, default=None):
    """``row[key]`` of a sqlite3.Row or dict, ``default`` when the column does not exist (an older database)."""
    if row is None:
        return default
    try:
        v = row[key]
    except (KeyError, IndexError):
        return default
    return default if v is None else v


def _cols(conn: sqlite3.Connection, table: str) -> set:
    try:
        return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
    except sqlite3.Error:
        return set()


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _json(text) -> dict:
    try:
        v = json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}
    return v if isinstance(v, dict) else {}


def _ny_minute(ts) -> str:
    """'2026-10-28 10:15 ET' - the entered time in a label."""
    return ny_of(int(ts)).strftime("%Y-%m-%d %H:%M ET")


def _iso_epoch(v) -> Optional[int]:
    """An as-of ``exec_ts`` (ISO with offset, or epoch seconds) -> epoch seconds; None when unreadable."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return int(v)
    try:
        dt = datetime.fromisoformat(str(v).strip())
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return int(dt.timestamp())


def pct_text(v) -> Optional[str]:
    """A stored decimal fraction as a percent string with at least two decimals ('0.003' -> '0.30'); None when unreadable."""
    if v is None or v == "":
        return None
    try:
        q = (Decimal(str(v)) * 100).quantize(Decimal("0.0001")).normalize()
    except Exception:   # noqa: BLE001 - not a number: shown as stored
        return str(v)
    s = format(q, "f")
    whole, _dot, frac = s.partition(".")
    return f"{whole}.{(frac + '00')[:max(2, len(frac))]}"


def dealer_name(broker: Optional[str]) -> str:
    """The display name of a prime broker / dealer code ('GS' -> 'Goldman Sachs')."""
    b = str(broker or "").upper()
    return config.PRIME_BROKERS.get(b, b or "the dealer")


def lender_words(services, lender: Optional[str]) -> str:
    """C16: a stored borrow lender as shown (``lending.lender_label``: '... (modelled)' -> '... (assumed terms)', P6); the same
    rule applies while that function is not in the tree.  Stored rows are never changed."""
    lending = getattr(services, "lending", None) if services is not None else None
    fn = getattr(lending, "lender_label", None) if lending is not None else None
    if callable(fn):
        try:
            return str(fn(lender))
        except Exception:   # noqa: BLE001 - the wording falls back to the rule below, never a 500
            pass
    s = str(lender or "")
    if s.endswith("(modelled)"):
        return s[: -len("(modelled)")] + "(assumed terms)"
    if s.endswith("(modeled)"):
        return s[: -len("(modeled)")] + "(assumed terms)"
    return s


# ---- source labels (15.7 item 7) ---------------------------------------------------------------------------------------------
def _command_asof(conn: sqlite3.Connection, command_id: Optional[int], cache: Optional[dict] = None) -> dict:
    """The ``as_of`` block of the booking command's payload ({} when absent) - its ``batch_id`` names the batch."""
    if command_id is None:
        return {}
    cid = int(command_id)
    if cache is not None and cid in cache:
        return cache[cid]
    row = conn.execute("SELECT payload_json FROM commands WHERE id=?", (cid,)).fetchone()
    out = _json(row[0] if row is not None else None).get("as_of") or {}
    out = out if isinstance(out, dict) else {}
    if cache is not None:
        cache[cid] = out
    return out


def _fill_command_id(conn: sqlite3.Connection, fill_row) -> Optional[int]:
    eid = _get(fill_row, "entry_id")
    if eid is None:
        return None
    r = conn.execute("SELECT command_id FROM journal_entries WHERE entry_id=?", (int(eid),)).fetchone()
    return None if r is None else int(r[0])


def _order_fill(conn: sqlite3.Connection, order_id: str):
    return conn.execute("SELECT f.trade_date, f.entry_id, j.command_id FROM fills f LEFT JOIN journal_entries j ON j.entry_id=f.entry_id"
                        " WHERE f.order_id=? ORDER BY f.ts, f.fill_id LIMIT 1", (order_id,)).fetchone()


def source_label(conn: sqlite3.Connection, kind: str, row, _cache: Optional[dict] = None) -> dict:
    """``{source, label, asof, entered_ts, batch_id, booking, badge}`` of an order, fill or OTC contract row (``kind`` = ``order``,
    ``fill`` or ``otc_contract``; a sqlite3.Row or a dict with the table's columns).  ``asof`` is true for booking AS_OF only (the
    ``.badge-asof`` rows); ``badge`` is "AS-OF", "MODEL" (historical model) or None.  ``_cache`` (command id -> as_of block) lets a
    blotter label many rows with one read per booking command.  Trader wording only; nothing is written."""
    kind = str(kind or "").lower()
    booking = str(_get(row, "booking", "") or "").upper() or None
    entered = _get(row, "entered_ts")
    entered = int(entered) if entered is not None else None
    out = {"source": "simulated", "label": SIMULATED_LABEL, "asof": False, "entered_ts": None, "batch_id": None, "booking": booking, "badge": None}
    if booking in (AS_OF, HISTORICAL_MODEL):
        command_id = None
        trade_date = None
        if kind == "fill":
            command_id = _fill_command_id(conn, row)
            trade_date = _get(row, "trade_date")
        elif kind == "order":
            f = _order_fill(conn, str(_get(row, "order_id", "")))
            if f is not None:
                command_id = None if f["command_id"] is None else int(f["command_id"])
                trade_date = f["trade_date"]
        else:
            command_id = _get(row, "command_id")
        asof = _command_asof(conn, command_id, _cache)
        batch = asof.get("batch_id") or None
        trade_date = trade_date or asof.get("trade_date") or _get(row, "trade_date")
        out.update(entered_ts=entered, batch_id=batch)
        if booking == HISTORICAL_MODEL:
            out.update(source="historical_model", label=HISTORICAL_MODEL_LABEL, badge="MODEL")
            return out
        when = _ny_minute(entered) if entered is not None else "at an unrecorded time"
        tail = f"entered {when} by the operator at the owner's instruction" + (f" (batch {batch})" if batch else "")
        if kind == "otc_contract":
            value_date = _get(row, "value_date") or asof.get("value_date") or _get(row, "start_date") or "unknown"
            label = f"As-of booking - simulated OTC trade with {dealer_name(_get(row, 'broker'))}, value date {value_date}, {tail}"
        else:
            label = f"As-of booking - simulated fill at the {trade_date or 'recorded'} closing print, {tail}"
        out.update(source="as_of", label=label, asof=True, badge="AS-OF")
        return out
    origin = str(_get(row, "origin", "") or "").upper()
    if kind == "fill" and origin in SYSTEM_EVENTS:
        out.update(source="system", label=f"System ({SYSTEM_EVENTS[origin]})")
    if kind == "otc_contract":
        # an OTC contract is not a fill (P4-R1): a live contract reads "Simulated OTC trade with <dealer>"
        out.update(label=f"Simulated OTC trade with {dealer_name(_get(row, 'broker'))}")
    return out


# ---- the executions union (C7) -----------------------------------------------------------------------------------------------
def _accounts(conn: sqlite3.Connection, lifetime_ids: Optional[Sequence[str]], account_id: Optional[str]) -> Dict[str, dict]:
    sql = "SELECT a.account_id, a.name, a.kind, a.broker, a.book_code, a.lifetime_id FROM accounts a"
    if account_id:
        rows = conn.execute(sql + " WHERE a.account_id=?", (account_id,)).fetchall()
    elif lifetime_ids:
        q = ",".join("?" for _ in lifetime_ids)
        rows = conn.execute(sql + f" WHERE a.lifetime_id IN ({q}) ORDER BY a.lifetime_id, a.sort_order", tuple(lifetime_ids)).fetchall()
    else:
        rows = []
    return {r["account_id"]: dict(r) for r in rows}


def _base(acct: dict) -> dict:
    return {"account_id": acct["account_id"], "account_name": acct["name"], "book": acct["book_code"], "lifetime_id": acct["lifetime_id"]}


def _listed_counterparty(acct: dict, kind: str, session_group: str) -> str:
    if acct.get("kind") == "PB":
        return f"{dealer_name(acct.get('broker'))} (prime broker)"
    if kind == "OPTION":
        return "exchange (simulated), OCC clearing"
    if kind == "FUTURE":
        return "exchange (simulated), CME clearing"
    if kind == "BOND":
        return "dealer market (simulated)"
    if kind == "PRIVATE":
        return "negotiated secondary (simulated)"
    if str(session_group or "").upper().startswith("FOREIGN"):
        return "exchange (simulated), local clearing"
    return "exchange (simulated), NSCC clearing"


def _memo_side(memo: Optional[str]) -> Optional[str]:
    """The order side a fill's journal memo starts with ('STC 5 ...'): the side of a fill no order produced."""
    head = str(memo or "").split(" ", 1)[0].upper()
    return head if head in SIDE_WORDS else None


def _fill_status(r, settle: Optional[dict], inst_kind: str, side: str) -> Tuple[str, str]:
    if settle is not None:
        fail_state = str(settle.get("fail_state") or "").upper()
        if fail_state == "FAILED":
            return "failed", STATUS_WORDS["failed"]
        if fail_state in ("RESOLVED",) or settle.get("resolved_ts"):
            return "resolved", STATUS_WORDS["resolved"]
        if settle.get("status") == "SETTLED":
            if int(settle.get("amount_c") or 0) == 0 and inst_kind in EQUITY_KINDS and side == "SELL_SHORT":
                return "collateral", STATUS_WORDS["collateral"]
            return "settled", STATUS_WORDS["settled"]
        return "settling", STATUS_WORDS["settling"]
    if inst_kind == "FUTURE":
        return "daily_vm", STATUS_WORDS["daily_vm"]
    if str(r["origin"] or "").upper() == "EXPIRY" or Decimal(str(r["price"] or "0")) == 0:
        return "no_cash_leg", STATUS_WORDS["no_cash_leg"]
    return "executed", STATUS_WORDS["executed"]


def _fill_rows(services, conn: sqlite3.Connection, accts: Dict[str, dict], cache: dict) -> List[dict]:
    if not accts:
        return []
    ids = tuple(accts)
    q = ",".join("?" for _ in ids)
    fcols, scols = _cols(conn, "fills"), _cols(conn, "settlements")
    rows = conn.execute(
        "SELECT f.*, i.kind AS inst_kind, i.symbol AS inst_symbol, i.name AS inst_name, i.underlying_id AS inst_underlying, i.session_group AS inst_group,"
        " o.side AS order_side, o.order_type AS order_type, j.command_id AS command_id, j.memo AS entry_memo"
        " FROM fills f LEFT JOIN instruments i ON i.instrument_id=f.instrument_id LEFT JOIN orders o ON o.order_id=f.order_id"
        f" LEFT JOIN journal_entries j ON j.entry_id=f.entry_id WHERE f.account_id IN ({q})", ids).fetchall()
    if not rows:
        return []
    want = ["settle_id", "fill_id", "leg", "status", "amount_c", "due_date", "settled_ts", "gl"] + [c for c in ("fail_state", "resolved_ts", "resolution") if c in scols]
    settles: Dict[str, dict] = {}
    for s in conn.execute(f"SELECT {', '.join('s.' + c for c in want)} FROM settlements s JOIN fills f ON f.fill_id=s.fill_id WHERE f.account_id IN ({q})"
                          " ORDER BY s.settle_id", ids).fetchall():
        d = dict(s)
        prev = settles.get(d["fill_id"])
        if prev is None or d.get("leg") == "CASH":
            settles[d["fill_id"]] = d
    fees: Dict[int, List[dict]] = {}
    for ln in conn.execute("SELECT jl.entry_id, jl.amount_c, jl.memo FROM journal_lines jl JOIN fills f ON f.entry_id=jl.entry_id"
                           f" WHERE jl.gl='5000' AND f.account_id IN ({q}) ORDER BY jl.line_id", ids).fetchall():
        fees.setdefault(int(ln["entry_id"]), []).append({"label": ln["memo"] or "fee", "amount_c": int(ln["amount_c"])})
    out = []
    for r in rows:
        acct = accts.get(r["account_id"])
        if acct is None:
            continue
        kind = str(r["inst_kind"] or "")
        side = r["order_side"] or _memo_side(r["entry_memo"]) or r["side"]
        settle = settles.get(r["fill_id"])
        status, words = _fill_status(r, settle, kind, side)
        src = source_label(conn, "fill", r, cache)
        lines = fees.get(int(r["entry_id"]), []) if r["entry_id"] is not None else []
        row = _base(acct)
        row.update({
            "execution_id": r["fill_id"], "kind": "fill", "parent_id": r["order_id"] or (f"T-{r['command_id']}" if r["command_id"] is not None else None),
            "parent_kind": "order" if r["order_id"] else "ticket", "instrument_id": r["instrument_id"], "symbol": r["inst_symbol"] or r["instrument_id"],
            "instrument_name": r["inst_name"], "underlying_id": r["inst_underlying"], "asset_class": ASSET_CLASS.get(kind, kind.title() or "Other"),
            "side": side, "side_words": SIDE_WORDS.get(side, str(side or "").replace("_", " ")), "qty": int(r["qty"]), "notional_c": None,
            "currency": r["currency"], "price": str(r["price"]), "price_label": "price", "gross_c": int(r["gross_c"]), "fee_c": int(r["fee_c"] or 0),
            "fees_journal_c": sum(int(x["amount_c"]) for x in lines), "fee_lines": lines, "exec_ts": int(r["ts"]), "trade_date": r["trade_date"],
            "settle_date": (settle or {}).get("due_date") or r["settle_date"], "settled_ts": (settle or {}).get("settled_ts"),
            "settle_id": (settle or {}).get("settle_id"), "counterparty": _listed_counterparty(acct, kind, r["inst_group"]),
            "status": status, "status_label": words, "source": src["source"], "source_label": src["label"], "asof": src["asof"],
            "badge": src["badge"], "entered_ts": src["entered_ts"], "batch_id": src["batch_id"], "booking": src["booking"],
            "import_key": r["import_key"] if "import_key" in fcols else None, "origin": r["origin"], "order_type": r["order_type"],
            "note": r["cap_note"] or "", "price_basis": r["price_basis"], "cap_note": r["cap_note"], "quote": _quote_used(r["quote_json"])})
        out.append(row)
    return out


def _quote_used(text) -> Optional[dict]:
    """The observation a fill took its price from (``fills.quote_json``), in the fields a page shows: last / bid / ask / mid, quality,
    quote time, source, delay label (an as-of booking's provenance: its note)."""
    q = _json(text)
    if not q:
        return None
    keep = ("last", "bid", "ask", "mid", "quality", "quote_time", "fetched_at", "source", "delay_label", "method", "note", "price", "trade_date")
    return {k: q.get(k) for k in keep if q.get(k) is not None}


def _fx_rows(services, conn: sqlite3.Connection, accts: Dict[str, dict]) -> List[dict]:
    if not accts or not _has_table(conn, "fx_deals"):
        return []
    ids = tuple(accts)
    q = ",".join("?" for _ in ids)
    out = []
    for r in conn.execute(f"SELECT * FROM fx_deals WHERE account_id IN ({q})", ids).fetchall():
        acct = accts[r["account_id"]]
        fee = conn.execute("SELECT COALESCE(SUM(amount_c), 0) FROM journal_lines WHERE entry_id=? AND gl='5000'", (r["entry_id"],)).fetchone()[0]
        settled = r["status"] == "SETTLED"
        row = _base(acct)
        row.update({
            "execution_id": r["deal_id"], "kind": "fx_deal", "parent_id": f"T-{r['command_id']}", "parent_kind": "ticket",
            "instrument_id": f"{r['buy_ccy']}{r['sell_ccy']}", "symbol": f"{r['buy_ccy']}/{r['sell_ccy']} {str(r['kind']).lower()}",
            "instrument_name": f"FX {str(r['kind']).lower()}", "underlying_id": None, "asset_class": "FX", "side": f"BUY_{r['buy_ccy']}",
            "side_words": f"BUY {r['buy_ccy']} / SELL {r['sell_ccy']}", "qty": None, "notional_c": int(r["buy_c"]), "currency": r["buy_ccy"],
            "price": str(r["rate"]), "price_label": "rate", "gross_c": int(r["sell_c"]), "fee_c": int(fee or 0), "fees_journal_c": int(fee or 0), "fee_lines": [],
            "cost_of_dealing_c": int(r["spread_cost_c"] or 0), "exec_ts": int(r["trade_ts"]), "trade_date": r["trade_date"], "settle_date": r["value_date"],
            "settled_ts": r["settled_ts"], "settle_id": None,
            "counterparty": f"{dealer_name(acct.get('broker'))} (prime broker)" if acct.get("kind") == "PB" else "FX dealer (simulated)",
            "status": "settled" if settled else "settling", "status_label": STATUS_WORDS["settled" if settled else "settling"],
            "source": "simulated", "source_label": SIMULATED_LABEL, "asof": False, "badge": None, "entered_ts": None, "batch_id": None, "booking": None,
            "import_key": None, "origin": None, "order_type": None,
            "note": f"sold {r['sell_ccy']} {from_c(int(r['sell_c']))}; cost of dealing {from_c(int(r['spread_cost_c'] or 0))}"})
        out.append(row)
    return out


def _otc_describe(kind: str, terms: dict, row) -> Tuple[str, str, Optional[int], Optional[str], str]:
    """``(symbol, side words, quantity, price, price kind)`` of an OTC contract from its stored terms."""
    k = str(kind or "").upper()
    if k == "TRS":
        uid = terms.get("underlying_id") or "?"
        receiver = bool(terms.get("receiver"))
        units = terms.get("units")
        return (f"{uid} total return swap", "RECEIVE TOTAL RETURN" if receiver else "PAY TOTAL RETURN", int(units) if units is not None else None,
                str(_get(row, "reset_px") or terms.get("reset_px") or "") or None, "reset price")
    if k == "CDX":
        name = terms.get("index_name") or terms.get("index") or "CDX"
        series = terms.get("series")
        tenor = terms.get("tenor") or "5Y"
        side = str(terms.get("side") or ("SELL_PROTECTION" if terms.get("receiver") else "BUY_PROTECTION")).upper()
        spread = terms.get("dealt_spread_bp") or terms.get("deal_spread_bp")
        return (f"{name}{' S' + str(series) if series else ''} {tenor}", side.replace("_", " "), None, None if spread is None else str(spread), "spread bp")
    if k == "IRS":
        pay = bool(terms.get("pay_fixed"))
        tenor = terms.get("tenor_months")
        years = f" {int(tenor) // 12}Y" if isinstance(tenor, int) and tenor % 12 == 0 else (f" {tenor}M" if tenor else "")
        return (f"USD SOFR interest rate swap{years}", "PAY FIXED" if pay else "RECEIVE FIXED", None, pct_text(terms.get("fixed_rate")), "fixed rate %")
    if k == "XCCY":
        legs = terms.get("legs") or {}
        recv = (legs.get("receive") or {}).get("currency") or terms.get("recv_ccy") or "?"
        pay = (legs.get("pay") or {}).get("currency") or terms.get("pay_ccy") or "?"
        return (f"{recv}/{pay} cross-currency swap", f"RECEIVE {recv} / PAY {pay}", None, None if terms.get("deal_rate") is None else str(terms.get("deal_rate")), "rate")
    side = str(terms.get("side") or "").upper()
    what = {"EQ_OPTION": "option", "FX_OPTION": "option", "EQ_FORWARD": "forward", "FX_FORWARD": "forward"}.get(k, k.lower())
    right = {"C": " CALL", "P": " PUT"}.get(str(terms.get("right") or "").upper(), "") if what == "option" else ""
    under = terms.get("underlying_id") or terms.get("pair") or ""
    units = terms.get("units")
    return (f"{under} OTC {what}".strip(), f"{side or 'TRADE'}{right}", int(units) if isinstance(units, int) else None,
            None if terms.get("strike") is None else str(terms.get("strike")), "strike")


def _otc_rows(services, conn: sqlite3.Connection, accts: Dict[str, dict], cache: dict) -> List[dict]:
    if not accts or not _has_table(conn, "otc_contracts"):
        return []
    ids = tuple(accts)
    q = ",".join("?" for _ in ids)
    contracts = {r["contract_id"]: r for r in conn.execute(f"SELECT * FROM otc_contracts WHERE account_id IN ({q})", ids).fetchall()}
    if not contracts:
        return []
    booked = {}
    for e in conn.execute(f"SELECT ref_id, MIN(ts) AS ts FROM events WHERE type='OTC_BOOKED' AND ref_type='otc' AND account_id IN ({q}) GROUP BY ref_id", ids).fetchall():
        booked[e["ref_id"]] = int(e["ts"])
    started = {}
    cids = sorted({int(r["command_id"]) for r in contracts.values() if r["command_id"] is not None})
    for i in range(0, len(cids), 500):
        part = cids[i:i + 500]
        for c in conn.execute(f"SELECT id, started_ts, actor FROM commands WHERE id IN ({','.join('?' for _ in part)})", tuple(part)).fetchall():
            started[int(c["id"])] = int(c["started_ts"])
    out = []
    for cid, r in contracts.items():
        acct = accts[r["account_id"]]
        terms = _json(r["terms_json"])
        symbol, side_words, qty, price, price_kind = _otc_describe(r["kind"], terms, r)
        src = source_label(conn, "otc_contract", r, cache)
        asof = _command_asof(conn, r["command_id"], cache) if src["booking"] in (AS_OF, HISTORICAL_MODEL) else {}
        exec_ts = _iso_epoch(asof.get("exec_ts")) or booked.get(cid) or started.get(int(r["command_id"])) or int(_get(r, "entered_ts", 0) or 0)
        trade_date = _get(r, "trade_date") or asof.get("trade_date") or (ny_of(exec_ts).date().isoformat() if exec_ts else r["start_date"])
        mode = str(r["margin_mode"] or "CTM").upper()
        if r["status"] == "OPEN":
            status, words = "otc_margin", (f"IM posted, {mode} daily VM" if int(r["im_c"] or 0) > 0 else f"{mode} daily VM")
        else:
            status, words = "closed", f"closed ({str(r['status']).lower()})"
        fee = conn.execute("SELECT COALESCE(SUM(jl.amount_c), 0) FROM journal_lines jl JOIN journal_entries je ON je.entry_id=jl.entry_id"
                           " WHERE je.command_id=? AND jl.gl='5000' AND jl.account_id=?", (r["command_id"], r["account_id"])).fetchone()[0]
        base = _base(acct)
        common = {"instrument_id": cid, "symbol": symbol, "instrument_name": f"{symbol} with {dealer_name(r['broker'])}", "underlying_id": terms.get("underlying_id"),
                  "asset_class": "OTC", "currency": r["currency"], "counterparty": dealer_name(r["broker"]), "origin": None, "order_type": None,
                  "import_key": _get(r, "import_key"), "fee_lines": []}
        row = dict(base, **common)
        row.update({"execution_id": cid, "kind": "otc", "parent_id": f"T-{r['command_id']}", "parent_kind": "ticket", "side": str(terms.get("side") or side_words),
                    "side_words": side_words, "qty": qty, "notional_c": int(r["notional_c"]), "price": price, "price_label": price_kind, "gross_c": None,
                    "fee_c": int(fee or 0), "fees_journal_c": int(fee or 0), "exec_ts": exec_ts or None, "trade_date": trade_date,
                    "settle_date": _get(r, "value_date") or r["start_date"], "settled_ts": None, "settle_id": None, "status": status, "status_label": words,
                    "source": src["source"], "source_label": src["label"], "asof": src["asof"], "badge": src["badge"], "entered_ts": src["entered_ts"],
                    "batch_id": src["batch_id"], "booking": src["booking"],
                    "note": f"initial margin {from_c(int(r['im_c'] or 0))}; matures {r['maturity_date']}"})
        out.append(row)
    for e in conn.execute(f"SELECT e.id, e.command_id, e.ts, e.type, e.account_id, e.ref_id, e.payload_json, c.actor FROM events e"
                          f" LEFT JOIN commands c ON c.id=e.command_id WHERE e.type IN ('OTC_TERMINATED','SWAP_MATURED','OTC_EXPIRED','CDX_CREDIT_EVENT')"
                          f" AND e.ref_type='otc'"
                          f" AND e.account_id IN ({q}) ORDER BY e.id", ids).fetchall():
        r = contracts.get(e["ref_id"])
        if r is None:
            continue
        acct = accts[r["account_id"]]
        terms = _json(r["terms_json"])
        p = _json(e["payload_json"])
        symbol, side_words, qty, _price, _kind = _otc_describe(r["kind"], terms, r)
        verb = OTC_EVENTS[e["type"]]
        if verb == "credit event":
            out.append(_credit_event_row(acct, r, e, p, symbol, side_words, qty))
            continue
        system = verb == "matured" or str(e["actor"] or "") == "lifecycle"
        value = p.get("termination_value_c") if verb == "terminated" else p.get("value_c")
        row = _base(acct)
        row.update({"execution_id": f"{r['contract_id']}:{verb}", "kind": "otc", "parent_id": f"T-{e['command_id']}", "parent_kind": "ticket",
                    "instrument_id": r["contract_id"], "symbol": symbol, "instrument_name": f"{symbol} with {dealer_name(r['broker'])}",
                    "underlying_id": terms.get("underlying_id"), "asset_class": "OTC", "side": verb.upper(), "side_words": f"{verb.upper()} ({side_words})",
                    "qty": qty, "notional_c": int(r["notional_c"]), "currency": r["currency"], "price": None, "price_label": "value",
                    "gross_c": int(value) if isinstance(value, int) else None, "fee_c": 0, "fees_journal_c": 0, "fee_lines": [], "exec_ts": int(e["ts"]),
                    "trade_date": accounting_day(ny_of(int(e["ts"]))).isoformat() if verb != "matured" else (p.get("date") or ny_of(int(e["ts"])).date().isoformat()),
                    "settle_date": p.get("date") or accounting_day(ny_of(int(e["ts"]))).isoformat(), "settled_ts": int(e["ts"]), "settle_id": None,
                    "counterparty": dealer_name(r["broker"]), "status": verb, "status_label": STATUS_WORDS[verb],
                    "source": "system" if system else "simulated", "source_label": f"System ({'maturity' if verb == 'matured' else 'termination'})" if system else SIMULATED_LABEL,
                    "asof": False, "badge": None, "entered_ts": None, "batch_id": None, "booking": None, "import_key": None, "origin": None, "order_type": None,
                    "note": ""})
        out.append(row)
    return out


def _credit_event_row(acct: dict, r, e, p: dict, symbol: str, side_words: str, qty) -> dict:
    """A simulated CDX credit event (``cdx.credit_event``, P4 task 11; FINSIM1 P4-R1) as ONE execution of its contract: id
    ``<contract>:credit:<event id>``, side CREDIT EVENT, quantity the defaulted notional, gross = the net settlement amount (+ received by a
    protection buyer, - paid by a seller: protection less the accrued premium on the defaulted notional), trade date = the event date,
    settling on the event's settlement date (its settlement row), source the simulated credit-event label; the note says the name, the
    recovery, the defaulted notional, the protection, the accrued premium and the contract's notional before and after."""

    def m(k):
        v = p.get(k)
        return from_c(int(v)) if isinstance(v, int) and not isinstance(v, bool) else "n/a"
    net = p.get("net_c")
    defaulted = p.get("defaulted_c")
    row = _base(acct)
    row.update({"execution_id": f"{r['contract_id']}:credit:{e['id']}", "kind": "otc", "parent_id": f"T-{e['command_id']}", "parent_kind": "ticket",
                "instrument_id": r["contract_id"], "symbol": symbol, "instrument_name": f"{symbol} with {dealer_name(r['broker'])}",
                "underlying_id": None, "asset_class": "OTC", "side": "CREDIT EVENT", "side_words": f"CREDIT EVENT ({side_words})", "qty": qty,
                "notional_c": int(defaulted) if isinstance(defaulted, int) else int(r["notional_c"]), "currency": r["currency"], "price": None,
                "price_label": "recovery %", "gross_c": int(net) if isinstance(net, int) else None, "fee_c": 0, "fees_journal_c": 0, "fee_lines": [],
                "exec_ts": int(e["ts"]), "trade_date": p.get("event_date") or ny_of(int(e["ts"])).date().isoformat(), "settle_date": p.get("settle_date"),
                "settled_ts": None, "settle_id": p.get("settle_id"), "counterparty": dealer_name(r["broker"]), "status": "credit event",
                "status_label": STATUS_WORDS["credit event"], "source": "simulated", "source_label": p.get("label") or "simulated credit event",
                "asof": False, "badge": None, "entered_ts": None, "batch_id": None, "booking": None, "import_key": None, "origin": None, "order_type": None,
                "note": (f"{p.get('reference_entity') or 'a constituent'} at {p.get('recovery_pct') or '?'} % recovery: defaulted {m('defaulted_c')} / protection"
                         f" {m('protection_c')} / accrued premium {m('accrued_c')}; notional {m('notional_before_c')} -> {m('notional_after_c')}")})
    if p.get("recovery_pct") is not None:
        row["price"] = str(p.get("recovery_pct"))
    return row


def _financing_rows(services, conn: sqlite3.Connection, accts: Dict[str, dict]) -> List[dict]:
    if not accts:
        return []
    ids = tuple(accts)
    q = ",".join("?" for _ in ids)
    types = tuple(FINANCING_EVENTS)
    tq = ",".join("?" for _ in types)
    events = conn.execute(f"SELECT e.id, e.command_id, e.ts, e.type, e.account_id, e.instrument_id, e.ref_type, e.ref_id, e.payload_json, c.actor FROM events e"
                          f" LEFT JOIN commands c ON c.id=e.command_id WHERE e.type IN ({tq}) AND e.account_id IN ({q}) ORDER BY e.id", types + ids).fetchall()
    if not events:
        return []
    borrows = {r["borrow_id"]: r for r in conn.execute(f"SELECT * FROM borrows WHERE account_id IN ({q})", ids).fetchall()} if _has_table(conn, "borrows") else {}
    repos = {r["repo_id"]: r for r in conn.execute(f"SELECT * FROM repos WHERE account_id IN ({q})", ids).fetchall()} if _has_table(conn, "repos") else {}
    loans = {r["loan_id"]: r for r in conn.execute(f"SELECT * FROM loans WHERE account_id IN ({q})", ids).fetchall()} if _has_table(conn, "loans") else {}
    sec = ({r["loan_id"]: r for r in conn.execute(f"SELECT * FROM sec_loans WHERE account_id IN ({q})", ids).fetchall()}
           if _has_table(conn, "sec_loans") else {})
    pcl = ({r["loan_id"]: r for r in conn.execute(f"SELECT * FROM private_loans WHERE account_id IN ({q})", ids).fetchall()}
           if _has_table(conn, "private_loans") else {})
    names = _counterparty_names(services, conn)
    symbols = {r["instrument_id"]: r["symbol"] for r in conn.execute("SELECT instrument_id, symbol FROM instruments WHERE instrument_id IN"
                                                                     " (SELECT DISTINCT instrument_id FROM events WHERE type IN (" + tq + "))", types).fetchall()}
    out = []
    known: Dict[str, Tuple[Optional[int], Optional[int], str]] = {}  # ref -> (quantity, notional, counterparty) of its opening, for a later step
    for e in events:
        acct = accts.get(e["account_id"])
        if acct is None:
            continue
        p = _json(e["payload_json"])
        t = e["type"]
        if t in ("LOAN_DRAWN", "LOAN_REPAID") and str(p.get("kind") or "") != "CASH_LOAN":
            continue                      # automatic PB financing draws / repayments are funding, not trades (section 9)
        if t == "PC_PAID" and not p.get("final"):
            continue                      # a coupon is income, not a trade; the final payment repays the face
        verb, status = FINANCING_EVENTS[t]
        ref = str(e["ref_id"] or "")
        iid = e["instrument_id"]
        rp = None
        qty, notional, price, price_kind, counterparty, symbol, ccy = None, None, None, "rate", "", None, "USD"
        note = ref
        if t.startswith("BORROW_"):
            b = borrows.get(ref)
            qty = int(p.get("qty") or (b["qty"] if b is not None else 0) or 0) or None
            if t == "BORROW_OPENED" and qty and p.get("mark"):
                notional = int((Decimal(str(p["mark"])) * qty * 100).quantize(Decimal(1)))      # the borrowed shares at the mark of the open
                note = f"{ref}: {qty:,} shares borrowed at the mark {p['mark']}"
            elif t == "BORROW_RETURNED":
                note = f"{ref}: {qty or 0:,} shares returned ({p.get('reason') or 'returned'}); collateral back {from_c(int(p.get('collateral_returned_c') or 0))}"
            price, price_kind = pct_text(b["fee_rate"] if b is not None else p.get("fee_rate")), "borrow fee %"
            counterparty = lender_words(services, (b["lender"] if b is not None else p.get("lender")))
            ccy = b["currency"] if b is not None else "USD"
            if status is None:
                status = "open" if b is None or b["status"] == "OPEN" else "closed"
        elif t.startswith("SECURITIES_"):
            s = sec.get(ref)
            qty = int(p.get("qty") or 0) or None
            notional = p.get("collateral_c") if t == "SECURITIES_LENT" else p.get("collateral_returned_c")
            price, price_kind = pct_text(p.get("fee_rate") or _get(s, "fee_rate")), "lending fee %"
            counterparty = names.get(str(p.get("counterparty_id") or _get(s, "counterparty") or ""), str(p.get("counterparty_id") or _get(s, "counterparty") or ""))
            if status is None:
                status = "open" if s is None or _get(s, "status") == "OPEN" else "closed"
            if status == "recalled" and p.get("closed"):
                status = "returned"
        elif t.startswith("REPO_"):
            rp = repos.get(ref)
            side = str(p.get("side") or _get(rp, "side") or "REPO").upper()
            if t == "REPO_OPENED":
                verb = "REVERSE REPO (lend cash)" if side.startswith("REVERSE") else "REPO (borrow cash)"
                status = "open" if rp is None or _get(rp, "status") == "OPEN" else "closed"
            qty = p.get("face") if isinstance(p.get("face"), int) else (_get(rp, "face") if t == "REPO_OPENED" else None)
            notional = p.get("cash_c") if t == "REPO_OPENED" else p.get("principal_c")
            price, price_kind = pct_text(p.get("rate") or _get(rp, "rate")), "repo rate %"
            cp = p.get("counterparty_id") or _get(rp, "counterparty")
            counterparty = names.get(str(cp or ""), str(cp or "repo dealer"))
        elif t.startswith("LOAN_"):
            ln = loans.get(ref)
            notional = p.get("amount_c")
            ccy = str(p.get("currency") or _get(ln, "currency") or "USD")
            price, price_kind = pct_text(p.get("rate")), "loan rate %"
            counterparty = str(p.get("lender") or _get(ln, "lender") or "lender")
            symbol = f"{ccy} cash loan"
            if status is None:
                status = "open" if ln is None or _get(ln, "status") == "OPEN" else "closed"
        else:                                                   # private credit
            pl = pcl.get(ref)
            notional = p.get("principal_c") if t == "PC_COMMITTED" else (p.get("amount_c") if t == "PC_REPAID" else p.get("face_c"))
            ccy = str(_get(pl, "currency") or "USD")
            price, price_kind = pct_text(p.get("spread") or _get(pl, "spread")), "spread %"
            counterparty = str(p.get("borrower") or _get(pl, "borrower") or "borrower")
            if status is None:
                status = "open" if pl is None or _get(pl, "status") == "OPEN" else "closed"
        if rp is not None and not isinstance(notional, int) and t == "REPO_ROLLED":
            notional = _get(rp, "principal_c")
        q0, n0, c0 = known.get(ref, (None, None, ""))
        qty = qty if isinstance(qty, int) else q0
        notional = notional if isinstance(notional, int) and not isinstance(notional, bool) else n0
        counterparty = counterparty or c0
        if t in OPENING_EVENTS:
            known[ref] = (qty if isinstance(qty, int) else None, notional if isinstance(notional, int) else None, counterparty)
        system = str(e["actor"] or "") == "lifecycle"
        opening = t in OPENING_EVENTS
        ts = int(e["ts"])
        row = _base(acct)
        row.update({"execution_id": f"{ref}:{t.split('_', 1)[1].lower()}" + ("" if opening else f":{e['id']}"), "kind": "financing",
                    "parent_id": f"T-{e['command_id']}", "parent_kind": "ticket", "instrument_id": iid or ref,
                    "symbol": symbol or (symbols.get(iid) if iid else None) or iid or ref, "instrument_name": None, "underlying_id": None,
                    "asset_class": "Financing", "side": t, "side_words": verb, "qty": int(qty) if isinstance(qty, int) else None,
                    "notional_c": int(notional) if isinstance(notional, int) and not isinstance(notional, bool) else None, "currency": ccy,
                    "price": None if price is None else str(price), "price_label": price_kind, "gross_c": None, "fee_c": 0, "fees_journal_c": 0, "fee_lines": [],
                    "exec_ts": ts, "trade_date": accounting_day(ny_of(ts)).isoformat(), "settle_date": accounting_day(ny_of(ts)).isoformat(),
                    "settled_ts": ts, "settle_id": None, "counterparty": counterparty, "status": status, "status_label": STATUS_WORDS.get(status, status),
                    "source": "system" if system else "simulated",
                    "source_label": f"System ({verb.lower()})" if system else SIMULATED_LABEL, "asof": False, "badge": None, "entered_ts": None,
                    "batch_id": None, "booking": None, "import_key": None, "origin": None, "order_type": None, "note": note})
        out.append(row)
    return out


def _counterparty_names(services, conn: sqlite3.Connection) -> Dict[str, str]:
    """Counterparty id -> display name from the directory table when it exists ({} otherwise)."""
    for table in ("counterparties",):
        if _has_table(conn, table):
            cols = _cols(conn, table)
            idc = "counterparty_id" if "counterparty_id" in cols else ("id" if "id" in cols else None)
            if idc and "name" in cols:
                try:
                    return {str(r[0]): str(r[1]) for r in conn.execute(f"SELECT {idc}, name FROM {table}").fetchall()}
                except sqlite3.Error:
                    return {}
    return {}


def execution_rows(services, conn: sqlite3.Connection, *, lifetime_ids: Optional[Sequence[str]] = None, account_id: Optional[str] = None) -> List[dict]:
    """Every execution of the scope (C7), newest first: fills, FX deals, OTC bookings / terminations / maturities and the
    financing trades, one row each (never merged)."""
    accts = _accounts(conn, lifetime_ids, account_id)
    cache: dict = {}
    rows = _fill_rows(services, conn, accts, cache) + _fx_rows(services, conn, accts) + _otc_rows(services, conn, accts, cache) + _financing_rows(services, conn, accts)
    rows.sort(key=lambda r: (-(int(r.get("exec_ts") or 0)), str(r.get("execution_id") or "")))
    return rows


def _split(v: Optional[str]) -> List[str]:
    return [x.strip() for x in str(v or "").split(",") if x.strip()]


def filter_rows(rows: Iterable[dict], *, date_from: Optional[str] = None, date_to: Optional[str] = None, instrument: Optional[str] = None,
                account: Optional[str] = None, asset_class: Optional[str] = None, counterparty: Optional[str] = None, status: Optional[str] = None,
                source: Optional[str] = None, batch: Optional[str] = None, record: Optional[str] = None) -> List[dict]:
    """The C7 filters (all optional; see the module docstring); ``record`` (comma list of fill / fx_deal / otc / financing) keeps one kind
    of execution (the account Orders page's fills section)."""
    records = {x.lower() for x in _split(record)}
    inc, exc = set(), set()
    for a in _split(asset_class):
        (exc if a.startswith("!") else inc).add(a.lstrip("!").lower())
    statuses = {s.lower() for s in _split(status)}
    sources = {SOURCE_ALIASES.get(s.lower(), s.lower()) for s in _split(source)}
    inst = str(instrument or "").strip().upper()
    cp = str(counterparty or "").strip().lower()
    out = []
    for r in rows:
        td = str(r.get("trade_date") or "")
        if date_from and td < date_from:
            continue
        if date_to and td > date_to:
            continue
        if account and r.get("account_id") != account:
            continue
        ac = str(r.get("asset_class") or "").lower()
        if inc and ac not in inc:
            continue
        if ac in exc:
            continue
        if inst and inst not in {str(r.get(k) or "").upper() for k in ("instrument_id", "symbol", "underlying_id")}:
            continue
        if cp and cp not in str(r.get("counterparty") or "").lower():
            continue
        if statuses and str(r.get("status") or "").lower() not in statuses:
            continue
        if sources and str(r.get("source") or "") not in sources:
            continue
        if batch and str(r.get("batch_id") or "") != batch:
            continue
        if records and str(r.get("kind") or "") not in records:
            continue
        out.append(r)
    return out


def counts_of(rows: Sequence[dict]) -> dict:
    out: Dict[str, Dict[str, int]] = {"status": {}, "source": {}, "asset_class": {}}
    for r in rows:
        for k in out:
            v = str(r.get(k) or "")
            out[k][v] = out[k].get(v, 0) + 1
    return out


RECORD_WORDS = {"fill": "fill", "fx_deal": "FX deal", "otc": "OTC contract", "financing": "financing trade"}


def executions_csv(rows: Sequence[dict]) -> str:
    """Every column of the rows (C7 ``format=csv``): money in decimal units, times in New York time."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([h for h, _k, _how in CSV_COLUMNS])
    for r in rows:
        line = []
        for _h, k, how in CSV_COLUMNS:
            v = r.get(k)
            if v is None:
                line.append("")
            elif how == "c":
                line.append(str(from_c(int(v))))
            elif how == "ts":
                line.append(ny_str(int(v)) if v else "")
            elif k == "kind":
                line.append(RECORD_WORDS.get(str(v), v))
            else:
                line.append(v)
        w.writerow(line)
    return buf.getvalue()


# ---- Pending Trades (C7, P2 task 2; release 1.0.6) -------------------------------------------------------------------------------
def _when(ts) -> str:
    """'Thu 10-29 09:30 ET'."""
    return ny_of(int(ts)).strftime("%a %m-%d %H:%M ET")


def _md_hm(ts) -> str:
    return ny_of(int(ts)).strftime("%m-%d %H:%M ET")


def expiration_words(row) -> str:
    """The order's time in force in words: 'DAY, expires 10-22 16:00 ET', 'GTC to 2027-01-25', 'GTD 11-20 (expires 11-20 16:00 ET)',
    'next session: 10-29 09:30 ET to 16:00 ET'."""
    tif = str(_get(row, "tif", "DAY"))
    exp = _get(row, "expires_ts")
    gtd = _get(row, "gtd_date")
    act = _get(row, "activate_ts")
    if gtd:
        return f"GTD {str(gtd)[5:10]}" + (f" (expires {_md_hm(exp)})" if exp else "")
    if act is not None:
        return f"next session: {_md_hm(act)}" + (f" to {ny_of(int(exp)).strftime('%H:%M ET')}" if exp else "")
    if tif == "GTC":
        return f"GTC to {ny_of(int(exp)).date().isoformat()}" if exp else "GTC"
    return f"DAY, expires {_md_hm(exp)}" if exp else "DAY"


def type_words(row) -> str:
    """'MKT', 'LMT 50.00', 'STP 330.00', 'STP 330.00 LMT 329.00', 'TRAIL 2.00 (stop 331.20)', 'TRAIL 2.00 %' (+ ' AON')."""
    from .orders_rules import px_words
    t = str(_get(row, "order_type", "") or "")
    out = TYPE_WORDS.get(t, t.replace("_", " "))
    lim, stop = _get(row, "limit_px"), _get(row, "stop_px")
    lim = None if lim is None else px_words(lim)
    stop = None if stop is None else px_words(stop)
    if t == "LIMIT" and lim is not None:
        out += f" {lim}"
    elif t == "STOP" and stop is not None:
        out += f" {stop}"
    elif t == "STOP_LIMIT":
        out = f"STP {stop} LMT {lim}" + (" (triggered)" if _get(row, "triggered") else "")
    elif t == "TRAILING_STOP":
        amt, pct = _get(row, "trail_amt"), _get(row, "trail_pct")
        out += (f" {px_words(amt)}" if amt is not None else (f" {pct} %" if pct is not None else "")) + (f" (stop {stop})" if stop is not None else "")
    if _get(row, "fill_mode") == "AON":
        out += " AON"
    return out


def order_stage(row, now: int, session=None) -> dict:
    """The derived stage of an ``orders`` row (C7; never stored): ``{stage, stage_label, stage_words, stage_chip}``.  ``session`` (the
    instrument group's state now) words a SUBMITTED order that rests for the session open."""
    status = str(_get(row, "status", "") or "")
    qty, filled = int(_get(row, "qty", 0) or 0), int(_get(row, "filled_qty", 0) or 0)
    reason = str(_get(row, "reason", "") or "")
    act = _get(row, "activate_ts")
    if status == "WORKING":
        if act is not None and int(now) < int(act):
            stage, label = "QUEUED", f"queued for {_when(act)}"
        elif not int(_get(row, "first_eval_done", 0) or 0):
            if session is not None and not getattr(session, "open", False):
                nxt = getattr(session, "next_open", None)
                stage, label = "SUBMITTED", "submitted: rests until the session opens" + (f" ({_when(nxt)})" if nxt else "")
            else:
                stage, label = "SUBMITTED", "submitted: awaiting a fresh quote"
        else:
            stage, label = "WORKING", "working"
    elif status == "PARTIAL":
        stage, label = "PARTIALLY_FILLED", f"{filled:,} of {qty:,} filled"
    elif status == "SUSPENDED":
        stage = "HELD"
        label = reason if reason.startswith("waiting for funds") else (f"fill refused: {reason}" if reason else "held")
    elif status == "WAITING_PARENT":
        stage, label = "WAITING_PARENT", f"waits for {_get(row, 'parent_order_id') or 'its primary'} to fill"
    elif status == "FILLED":
        stage, label = "FILLED", f"{qty:,} filled"
    elif status in ("CANCELLED", "EXPIRED"):
        stage = status
        part = f"{filled:,} of {qty:,} filled" if 0 < filled < qty else ""
        tail = "; ".join(x for x in (part, reason) if x)
        label = f"{STAGE_WORDS[stage]}: {tail}" if tail else STAGE_WORDS[stage]
    else:
        stage, label = (status or "WORKING"), status.lower().replace("_", " ")
    return {"stage": stage, "stage_label": label, "stage_words": STAGE_WORDS.get(stage, stage.lower().replace("_", " ")), "stage_chip": STAGE_CHIP.get(stage, "queued")}


def package_state(legs: Sequence) -> Tuple[str, str]:
    """``(state, words)`` of a package from its legs' statuses (never its stored status): OPEN while a leg is live, FILLED when every
    leg filled, CANCELLED / EXPIRED when every leg ended unfilled, else ENDED with the count of each ending (PK-000001: the primary
    filled, its hedge leg cancelled -> 'ended: 1 filled, 1 cancelled')."""
    st = [str(_get(l, "status", "")) for l in legs]
    live = sum(1 for s in st if s in LIVE_ORDER)
    if live:
        return "OPEN", f"open: {live} of {len(st)} legs live"
    if st and all(s == "FILLED" for s in st):
        return "FILLED", f"filled: {len(st)} of {len(st)} legs"
    filled_any = any(int(_get(l, "filled_qty", 0) or 0) > 0 for l in legs)
    for end in ("CANCELLED", "EXPIRED"):
        if st and all(s == end for s in st) and not filled_any:
            return end, f"{end.lower()}: {len(st)} legs, nothing filled"
    parts = []
    for end in ("FILLED", "CANCELLED", "EXPIRED"):
        n = sum(1 for s in st if s == end)
        if n:
            parts.append(f"{n} {end.lower()}")
    return "ENDED", "ended: " + ", ".join(parts) if parts else "ended"


def _market(rec, session, now: int) -> Optional[dict]:
    """The observed quote as the blotter shows it (C7: from the quote cache, never fetched): last / bid / ask, quality (``freshness``),
    age and source."""
    if rec is None:
        return None
    try:
        from .quotes import freshness
        quality = freshness(rec, session, now)
    except Exception:   # noqa: BLE001 - the record's own quality
        quality = getattr(rec, "quality", None)
    qt = getattr(rec, "quote_time", None)
    s = lambda v: None if v is None else str(v)   # noqa: E731
    return {"last": s(rec.last), "bid": s(rec.bid), "ask": s(rec.ask), "mid": s(getattr(rec, "mid", None)), "quality": quality,
            "age_s": None if qt is None else max(0, int(now) - int(qt)), "quote_time": qt, "source": getattr(rec, "source", None),
            "delay_label": getattr(rec, "delay_label", None), "error": getattr(rec, "error", None)}


class _Looks:
    """Per-request caches of the quote, session and instrument the blotter reads for its live orders (nothing is fetched)."""

    def __init__(self, services, conn, now: int):
        self.services, self.conn, self.now = services, conn, int(now)
        self._q: dict = {}
        self._s: dict = {}
        self._i: dict = {}

    def inst(self, iid: str):
        if iid not in self._i:
            master = getattr(self.services, "instruments", None)
            try:
                self._i[iid] = master.get(iid, self.conn) if master is not None else None
            except Exception:   # noqa: BLE001
                self._i[iid] = None
        return self._i[iid]

    def quote(self, iid: str):
        if iid not in self._q:
            from .orders_preview import quote_of
            try:
                self._q[iid] = quote_of(self.services, iid, None)
            except Exception:   # noqa: BLE001 - no quote is "no quote", never a failed page
                self._q[iid] = None
        return self._q[iid]

    def session(self, group: str, rec=None):
        key = (group, id(rec) if str(group or "").startswith("FOREIGN") else None)
        if key not in self._s:
            from .orders_preview import session_for
            try:
                self._s[key] = session_for(getattr(self.services, "sessions", None), group, rec, self.now) if group else None
            except Exception:   # noqa: BLE001
                self._s[key] = None
        return self._s[key]


def _reservations(services, conn, rows: Sequence, snapshot) -> Dict[str, int]:
    """C9a per live order, computed exactly as ``accounting.reserved_c`` sums them (WORKING / PARTIAL / WAITING_PARENT of a primary that
    is not SUSPENDED; futures running over the account's earlier working orders of that contract)."""
    from .accounting import reserved_for_order
    master = getattr(services, "instruments", None)
    status_of = {str(r["order_id"]): str(r["status"]) for r in rows}
    out: Dict[str, int] = {}
    fut_pos: Dict[str, Dict[str, int]] = {}
    cache: dict = {}
    for r in sorted(rows, key=lambda x: (str(x["account_id"]), int(x["created_ts"] or 0), str(x["order_id"]))):
        st = str(r["status"])
        if st == "WAITING_PARENT":
            parent = _get(r, "parent_order_id")
            pst = status_of.get(str(parent)) if parent else None
            if pst is None and parent:
                p = conn.execute("SELECT status FROM orders WHERE order_id=?", (parent,)).fetchone()
                pst = p[0] if p is not None else None
            if pst == "SUSPENDED":
                out[str(r["order_id"])] = 0
                continue
        elif st not in ("WORKING", "PARTIAL"):
            out[str(r["order_id"])] = 0
            continue
        try:
            out[str(r["order_id"])] = int(reserved_for_order(conn, r, snapshot, master, fut_pos=fut_pos.setdefault(str(r["account_id"]), {}), cache=cache) or 0)
        except Exception:   # noqa: BLE001 - a reservation that cannot be computed is shown unavailable, never guessed
            out[str(r["order_id"])] = None
    return out


def _actions(row) -> dict:
    """What the Pending blotter offers on an order row (C7 row actions); the server enforces each again."""
    st = str(_get(row, "status", ""))
    aon = _get(row, "fill_mode") == "AON" and bool(_get(row, "package_id"))
    child = bool(_get(row, "parent_order_id"))
    live = st in LIVE_ORDER
    return {"cancel": live, "resume": st == "SUSPENDED", "modify": st in ("WORKING", "PARTIAL", "SUSPENDED") and not aon,
            "replace": st in ("WORKING", "PARTIAL", "SUSPENDED") and not aon and not child, "review": True}


def _order_pending_rows(services, conn, accts: Dict[str, dict], statuses: Sequence[str], now: int, snapshot, src_cache: dict) -> List[dict]:
    if not accts or not statuses:
        return []
    ids = tuple(accts)
    qa = ",".join("?" for _ in ids)
    qs = ",".join("?" for _ in statuses)
    rows = conn.execute(f"SELECT o.*, i.kind AS inst_kind, i.symbol AS inst_symbol, i.name AS inst_name, i.underlying_id AS inst_underlying,"
                        f" i.session_group AS inst_group FROM orders o LEFT JOIN instruments i ON i.instrument_id=o.instrument_id"
                        f" WHERE o.account_id IN ({qa}) AND o.status IN ({qs})", ids + tuple(statuses)).fetchall()
    if not rows:
        return []
    looks = _Looks(services, conn, now)
    live = [r for r in rows if r["status"] in LIVE_ORDER]
    reserved = _reservations(services, conn, live, snapshot) if live else {}
    pkg_ids = sorted({r["package_id"] for r in rows if r["package_id"]})
    pkgs: Dict[str, dict] = {}
    for pid in pkg_ids:
        legs = conn.execute("SELECT order_id, status, filled_qty, qty, created_ts, parent_order_id FROM orders WHERE package_id=? ORDER BY created_ts, order_id",
                            (pid,)).fetchall()
        hp = conn.execute("SELECT strategy_key, candidate_id, fill_mode, status, primary_order_id, created_ts FROM hedge_packages WHERE package_id=?", (pid,)).fetchone()
        state, words = package_state(legs)
        order_ids = [l["order_id"] for l in legs]
        pkgs[pid] = {"package_id": pid, "strategy_key": (_get(hp, "strategy_key") or _get(hp, "candidate_id")) if hp is not None else None,
                     "fill_mode": _get(hp, "fill_mode") if hp is not None else None, "state": state, "state_words": words, "legs": order_ids,
                     "primary_order_id": _get(hp, "primary_order_id") if hp is not None else None,
                     "first_ts": min(int(l["created_ts"] or 0) for l in legs) if legs else 0}
    out = []
    for r in rows:
        acct = accts.get(r["account_id"])
        if acct is None:
            continue
        kind = str(r["inst_kind"] or "")
        status = str(r["status"])
        is_live = status in LIVE_ORDER
        qty, filled = int(r["qty"]), int(r["filled_qty"] or 0)
        rec = looks.quote(r["instrument_id"]) if is_live else None
        session = looks.session(r["inst_group"] or r["session_group"], rec) if is_live else None
        stage = order_stage(r, now, session)
        dist = None
        if is_live and rec is not None:
            try:
                from .orders_rules import OrderRow, distance
                dist = distance(OrderRow.from_row(r, looks.inst(r["instrument_id"])), rec)
            except Exception:   # noqa: BLE001 - no distance rather than a failed page
                dist = None
        pk = pkgs.get(r["package_id"]) if r["package_id"] else None
        row = _base(acct)
        row.update({
            "row_kind": "order", "order_id": r["order_id"], "instrument_id": r["instrument_id"], "symbol": r["inst_symbol"] or r["instrument_id"],
            "instrument_name": r["inst_name"], "underlying_id": r["inst_underlying"], "kind": kind, "asset_class": ASSET_CLASS.get(kind, kind.title() or "Other"),
            "side": r["side"], "side_words": SIDE_WORDS.get(r["side"], str(r["side"]).replace("_", " ")), "order_type": r["order_type"], "type_words": type_words(r),
            "limit_px": r["limit_px"], "stop_px": r["stop_px"], "trail_amt": r["trail_amt"], "trail_pct": r["trail_pct"], "triggered": bool(r["triggered"]),
            "qty": qty, "filled_qty": filled, "open_qty": (qty - filled) if is_live else None, "avg_px": r["avg_px"], "status": status,
            "market": _market(rec, session, now) if is_live else None, "distance": dist, "submitted_ts": int(r["created_ts"]), "updated_ts": int(r["updated_ts"] or 0),
            "expires_ts": int(r["expires_ts"]) if r["expires_ts"] is not None else None, "expiration_words": expiration_words(r), "tif": r["tif"],
            "gtd_date": _get(r, "gtd_date"), "activate_ts": _get(r, "activate_ts"), "counterparty": _listed_counterparty(acct, kind, r["inst_group"]),
            "reserved_c": reserved.get(r["order_id"]) if is_live else None, "reason": r["reason"] or "", "fees_c": int(r["fees_c"] or 0),
            "package_id": r["package_id"], "strategy_key": (pk or {}).get("strategy_key"), "package_state": (pk or {}).get("state"),
            "package_words": (pk or {}).get("state_words"), "parent_order_id": r["parent_order_id"], "fill_mode": r["fill_mode"],
            "borrow_id": r["borrow_id"], "replaces_order_id": _get(r, "replaces_order_id"), "replaced_by_order_id": _get(r, "replaced_by_order_id"),
            "amend_count": int(_get(r, "amend_count", 0) or 0), "actions": _actions(r), "booking": _get(r, "booking")})
        row.update(stage)
        if row["booking"]:
            src = source_label(conn, "order", r, src_cache)
            row.update(source=src["source"], source_label=src["label"], asof=src["asof"], badge=src["badge"], batch_id=src["batch_id"], entered_ts=src["entered_ts"])
        group_ts = (pk or {}).get("first_ts") or int(r["created_ts"])
        row["_sort"] = (-int(group_ts), str(r["package_id"] or r["order_id"]), int(r["created_ts"]), str(r["order_id"]))
        out.append(row)
    return out


def _rfq_pending_rows(services, accts: Dict[str, dict], lifetime_ids: Sequence[str], now: int) -> Tuple[List[dict], Optional[str]]:
    """The open OTC quote requests (C14: ``swaps.rfq_rows(services, scope)``, P4) as Pending rows - AWAITING DEALER QUOTE while REQUESTED,
    AWAITING YOUR ACCEPTANCE while QUOTED and the quote is valid.  ``([], note)`` while the function is not in the build or fails."""
    sw = getattr(services, "swaps", None)
    fn = getattr(sw, "rfq_rows", None) if sw is not None else None
    if not callable(fn):
        return [], None
    raw: List[dict] = []
    scopes = sorted({a["lifetime_id"] for a in accts.values()}) or list(lifetime_ids or [])
    try:
        for lid in scopes:
            got = fn(services, f"{lid}:ALL")
            got = got.get("rows") if isinstance(got, dict) else got
            raw.extend(dict(x) for x in (got or []) if isinstance(x, dict))
    except Exception as e:   # noqa: BLE001 - the quote requests are shown unavailable, the orders still list
        return [], f"OTC quote requests unavailable: {type(e).__name__}"
    out, seen = [], set()
    for r in raw:
        rid = str(r.get("rfq_id") or r.get("id") or "")
        aid = r.get("account_id")
        status = str(r.get("status") or "").upper()
        if not rid or rid in seen or aid not in accts or status not in RFQ_OPEN:
            continue
        seen.add(rid)
        exp = r.get("expires_ts") or r.get("quote_expires_ts") or r.get("valid_until_ts")
        if status == "QUOTED" and exp is not None and int(exp) <= int(now):
            continue                                       # the dealer job expires it on its next tick
        acct = accts[aid]
        terms = r.get("terms") if isinstance(r.get("terms"), dict) else _json(r.get("terms_json"))
        kind = str(r.get("kind") or "")
        symbol, side_words, qty, _px, _pk = _otc_describe(kind, terms, r)
        ts = r.get("requested_ts") or r.get("created_ts") or r.get("ts")
        quote = r.get("quote_words") or r.get("quote_label") or r.get("quote_text")     # the quote in words once (P4's quote_label adds its validity)
        qlab = r.get("quote").get("label") if isinstance(r.get("quote"), dict) else None
        if quote and qlab and r.get("quote_words"):
            quote = f"{quote} - {qlab}"                                    # 'indicative model quote, simulated dealer' (D4: never dealer-confirmed)
        if status == "REQUESTED":
            stage, label = "AWAITING_DEALER_QUOTE", "awaiting the dealer's quote"
        else:
            stage = "AWAITING_ACCEPTANCE"
            label = "awaiting your acceptance" + (f": {quote}" if quote else "") + (f", valid until {ny_of(int(exp)).strftime('%H:%M:%S ET')}" if exp else "")
        broker = r.get("broker") or acct.get("broker")
        row = _base(acct)
        row.update({"row_kind": "rfq", "order_id": rid, "rfq_id": rid, "instrument_id": r.get("instrument_id") or kind, "symbol": r.get("label") or symbol,
                    "instrument_name": r.get("summary") or symbol, "underlying_id": terms.get("underlying_id"), "kind": kind, "asset_class": "OTC",
                    "side": r.get("side") or side_words, "side_words": r.get("side_words") or side_words, "order_type": "RFQ", "type_words": "quote request",
                    "limit_px": None, "stop_px": None, "trail_amt": None, "trail_pct": None, "triggered": False,
                    "qty": qty if qty is not None else r.get("units"), "filled_qty": 0, "open_qty": qty, "notional_c": r.get("notional_c"), "avg_px": None,
                    "status": status, "market": None, "distance": None, "submitted_ts": int(ts) if ts else None, "updated_ts": int(ts) if ts else None,
                    "expires_ts": int(exp) if exp else None, "expiration_words": (f"quote valid until {_md_hm(exp)}" if exp else "until the dealer quotes"),
                    "tif": None, "gtd_date": None, "activate_ts": None, "counterparty": f"{dealer_name(broker)} (simulated dealer)", "reserved_c": None,
                    "reason": "", "fees_c": 0, "package_id": None, "strategy_key": None, "package_state": None, "package_words": None, "parent_order_id": None,
                    "fill_mode": None, "borrow_id": None, "replaces_order_id": None, "replaced_by_order_id": None, "amend_count": 0, "booking": None,
                    "quote": r.get("quote"), "quote_words": quote,
                    "actions": {"cancel": False, "resume": False, "modify": False, "replace": False, "review": False, "accept": status == "QUOTED",
                                "decline": status in RFQ_OPEN},
                    "stage": stage, "stage_label": label, "stage_words": STAGE_WORDS[stage], "stage_chip": STAGE_CHIP[stage]})
        row["_sort"] = (-(int(ts) if ts else 0), rid, 0, rid)
        out.append(row)
    return out, None


def _rejection_rows(services, conn, accts: Dict[str, dict]) -> List[dict]:
    """``order_rejections`` of the scope's accounts as REJECTED rows (every ticket: ``ticket_kind`` in the stored payload)."""
    if not accts or not _has_table(conn, "order_rejections"):
        return []
    try:
        from .orders import TICKET_WORDS
    except Exception:   # noqa: BLE001
        TICKET_WORDS = {}
    ids = tuple(accts)
    q = ",".join("?" for _ in ids)
    looks = _Looks(services, conn, 0)
    out = []
    for r in conn.execute(f"SELECT * FROM order_rejections WHERE account_id IN ({q}) ORDER BY ts DESC, id DESC", ids).fetchall():
        acct = accts[r["account_id"]]
        p = _json(r["payload_json"])
        tk = str(p.get("ticket_kind") or "order.submit")
        iid = r["instrument_id"]
        inst = looks.inst(iid) if iid else None
        kind = str(getattr(inst, "kind", "") or "")
        side = r["side"]
        row = _base(acct)
        row.update({"row_kind": "rejection", "order_id": f"R-{int(r['id'])}", "rejection_id": int(r["id"]), "instrument_id": iid,
                    "symbol": getattr(inst, "symbol", None) or iid or "", "instrument_name": getattr(inst, "name", None), "underlying_id": getattr(inst, "underlying_id", None),
                    "kind": kind, "asset_class": (TICKET_CLASS.get(tk) if tk != "order.submit" else None) or ASSET_CLASS.get(kind) or (kind.title() if kind else "Other"),
                    "side": side, "side_words": SIDE_WORDS.get(str(side), str(side or "").replace("_", " ")), "order_type": r["order_type"],
                    "type_words": TYPE_WORDS.get(str(r["order_type"] or ""), "") if tk == "order.submit" else TICKET_WORDS.get(tk, tk),
                    "ticket": TICKET_WORDS.get(tk, tk), "ticket_kind": tk, "limit_px": p.get("limit_px"), "stop_px": p.get("stop_px"), "trail_amt": None,
                    "trail_pct": None, "triggered": False, "qty": r["qty"], "filled_qty": 0, "open_qty": None, "avg_px": None, "status": "REJECTED",
                    "market": None, "distance": None, "submitted_ts": int(r["ts"]), "updated_ts": int(r["ts"]), "expires_ts": None, "expiration_words": "",
                    "tif": p.get("tif"), "gtd_date": None, "activate_ts": None, "counterparty": "", "reserved_c": None, "reason": r["message"] or "",
                    "code": r["code"], "request_id": r["request_id"], "fees_c": 0, "package_id": None, "strategy_key": None, "package_state": None,
                    "package_words": None, "parent_order_id": None, "fill_mode": None, "borrow_id": None, "replaces_order_id": None, "replaced_by_order_id": None,
                    "amend_count": 0, "booking": None, "actions": {"cancel": False, "resume": False, "modify": False, "replace": False, "review": False},
                    "stage": "REJECTED", "stage_label": f"rejected: {r['message'] or ''}".strip(), "stage_words": STAGE_WORDS["REJECTED"],
                    "stage_chip": STAGE_CHIP["REJECTED"]})
        row["_sort"] = (-int(r["ts"]), f"R-{int(r['id']):012d}", 0, "")
        out.append(row)
    return out


def pending_rows(services, conn, *, lifetime_ids: Optional[Sequence[str]] = None, account_id: Optional[str] = None, status: str = "open",
                 now: Optional[int] = None, snapshot=None) -> Tuple[List[dict], List[str]]:
    """``(rows, notes)`` of the Pending blotter for one status filter (C7; module docstring), newest first with each package's legs
    together.  Read-only: nothing is fetched or written."""
    status = str(status or "open").lower()
    if status not in PENDING_FILTERS:
        raise ValueError(f"status must be one of {', '.join(PENDING_FILTERS)}")
    now = int(now if now is not None else services.clock.epoch())
    accts = _accounts(conn, lifetime_ids, account_id)
    if snapshot is None:
        marks = getattr(services, "marks", None)
        snapshot = marks.snapshot() if marks is not None and hasattr(marks, "snapshot") else marks
    notes: List[str] = []
    rows = _order_pending_rows(services, conn, accts, PENDING_FILTERS[status], now, snapshot, {})
    if status in ("open", "all"):
        rfq, note = _rfq_pending_rows(services, accts, lifetime_ids or [], now)
        rows += rfq
        if note:
            notes.append(note)
    if status in ("rejected", "all"):
        rows += _rejection_rows(services, conn, accts)
    rows.sort(key=lambda r: r.pop("_sort"))
    return rows, notes


def filter_pending(rows: Iterable[dict], *, account: Optional[str] = None, asset_class: Optional[str] = None, instrument: Optional[str] = None) -> List[dict]:
    """The Pending blotter's account / asset class (comma list, ``!`` excludes) / instrument (id, symbol or underlying) filters."""
    inc, exc = set(), set()
    for a in _split(asset_class):
        (exc if a.startswith("!") else inc).add(a.lstrip("!").lower())
    inst = str(instrument or "").strip().upper()
    out = []
    for r in rows:
        if account and r.get("account_id") != account:
            continue
        ac = str(r.get("asset_class") or "").lower()
        if (inc and ac not in inc) or ac in exc:
            continue
        if inst and inst not in {str(r.get(k) or "").upper() for k in ("instrument_id", "symbol", "underlying_id")}:
            continue
        out.append(r)
    return out


def _scope_ids(conn, scope) -> Tuple[Optional[List[str]], Optional[str]]:
    """``(lifetime_ids, account_id)`` of a counts scope: ``<lifetime>:ALL``, ``ALL_BOOKS`` / ``ALL``, an account id, a lifetime id or a
    book code (its open lifetime)."""
    from . import books as _books
    s = str(scope or "ALL_BOOKS")
    if s in ("ALL_BOOKS", "ALL"):
        return list(_books.open_lifetimes(conn)), None
    if s.endswith(":ALL"):
        return [s[:-4]], None
    if ":" in s:
        return None, s
    if _books.lifetime_row(conn, s) is not None:
        return [s], None
    for lid in _books.open_lifetimes(conn):
        try:
            if _books.book_code_of(lid) == s.upper():
                return [lid], None
        except Exception:   # noqa: BLE001
            continue
    return [], None


def counts(services, scope=None) -> dict:
    """C7: ``{working, partial, held, queued, waiting_parent, rfq_open, rejected_today, settling, failed}`` of the scope (``<lifetime>:ALL``,
    ``ALL_BOOKS``, an account id, a lifetime or a book code): orders by derived stage (a WORKING order still waiting for its next session
    is ``queued``, not working), the open OTC quote requests, the refusals of today's accounting day, and the settlements still pending
    (``settling``) or failed (C15).  Read-only."""
    from .marks import shared_reader
    now = int(services.clock.epoch())
    with shared_reader(services.db) as conn:
        lids, account = _scope_ids(conn, scope)
        accts = _accounts(conn, lids, account)
        out = {"working": 0, "partial": 0, "held": 0, "queued": 0, "waiting_parent": 0, "rfq_open": 0, "rejected_today": 0, "settling": 0, "failed": 0}
        if not accts:
            return out
        ids = tuple(accts)
        q = ",".join("?" for _ in ids)
        act_col = "activate_ts" in _cols(conn, "orders")
        sel = "status, activate_ts" if act_col else "status, NULL AS activate_ts"
        for r in conn.execute(f"SELECT {sel} FROM orders WHERE account_id IN ({q}) AND status IN ('WORKING','PARTIAL','SUSPENDED','WAITING_PARENT')", ids).fetchall():
            st = r["status"]
            if st == "WORKING":
                out["queued" if (r["activate_ts"] is not None and now < int(r["activate_ts"])) else "working"] += 1
            elif st == "PARTIAL":
                out["partial"] += 1
            elif st == "SUSPENDED":
                out["held"] += 1
            else:
                out["waiting_parent"] += 1
        rfq, _note = _rfq_pending_rows(services, accts, lids or [], now)
        out["rfq_open"] = len(rfq)
        day = accounting_day(ny_of(now))
        for r in conn.execute(f"SELECT ts FROM order_rejections WHERE account_id IN ({q})", ids).fetchall():
            if accounting_day(ny_of(int(r["ts"]))) == day:
                out["rejected_today"] += 1
        fail = "fail_state" in _cols(conn, "settlements")
        for r in conn.execute(f"SELECT {'fail_state' if fail else 'NULL AS fail_state'} FROM settlements WHERE account_id IN ({q}) AND status='PENDING'", ids).fetchall():
            out["failed" if r["fail_state"] == "FAILED" else "settling"] += 1
        return out


# ---- the order drawer: GET /api/orders/{id} (P2 tasks 4 and 6) ------------------------------------------------------------------------
TIMELINE_WORDS = {"CREATED": "created", "SUBMITTED": "submitted", "WORKING": "working", "PARTIALLY_FILLED": "partially filled", "FILLED": "filled",
                  "CANCELLED": "cancelled", "EXPIRED": "expired", "HELD": "held", "RESUMED": "resumed", "AMENDED": "amended", "TRIGGERED": "triggered",
                  "RATCHET": "trailing stop moved", "WAITING_PARENT": "waiting for its parent", "RESIZED": "resized", "EVENT": "event"}


def _money_words(v) -> str:
    """'33951.00' -> '33,951.00' (a stored decimal string in words); as given when not a number."""
    try:
        from .units import fmt_money, to_c
        return fmt_money(to_c(Decimal(str(v))))
    except Exception:   # noqa: BLE001
        return str(v)


def order_timeline(raw, events: Sequence[dict]) -> List[dict]:
    """The order's lifecycle as steps (P2 task 6): CREATED (the stored preview, at its ``as_of``) -> SUBMITTED (the first event) ->
    WORKING (its first executable look) -> each partial fill -> FILLED / CANCELLED / EXPIRED, with HELD / RESUMED / AMENDED / TRIGGERED /
    trailing moves in between - from ``order_events``, nothing derived beyond the words."""
    pv = _json(_get(raw, "preview_json"))
    as_of = pv.get("as_of") if isinstance(pv.get("as_of"), int) and not isinstance(pv.get("as_of"), bool) else None
    if pv.get("kind") == "as_of":
        note = "as-of booking at the owner's instruction: booked at the owner's dealt price, no live preview"
    else:
        from .orders_rules import px_words
        ex = pv.get("execution") if isinstance(pv.get("execution"), dict) else {}
        eco = pv.get("economics") if isinstance(pv.get("economics"), dict) else {}
        bits = []
        if ex.get("expected_price"):
            bits.append(f"expected {px_words(ex.get('expected_price'))} ({ex.get('price_basis') or 'basis n/a'})")
        if eco.get("fee"):
            bits.append(f"fee {_money_words(eco.get('fee'))}")
        if (pv.get("funding") or {}).get("need") if isinstance(pv.get("funding"), dict) else None:
            bits.append(f"cash need {_money_words(pv['funding']['need'])}")
        if eco.get("settle_date"):
            bits.append(f"settles {eco.get('settle_date')}")
        note = "previewed: " + "; ".join(bits) if bits else "previewed"
    steps = [{"stage": "CREATED", "words": TIMELINE_WORDS["CREATED"], "ts": int(as_of if as_of is not None else (_get(raw, "created_ts") or 0)), "note": note}]
    first = True
    for e in events:
        to, frm, note = str(e.get("to_status") or ""), e.get("from_status"), str(e.get("note") or "")
        if first:
            stage = "SUBMITTED"
            first = False
        elif note.startswith("AMENDED"):
            stage = "AMENDED"
        elif note.startswith("RATCHET"):
            stage = "RATCHET"
        elif note.startswith("triggered"):
            stage = "TRIGGERED"
        elif note.startswith("first executable look"):
            stage = "WORKING"
        elif note.startswith("resized"):
            stage = "RESIZED"
        elif to == "PARTIAL" and frm in ("WORKING", "PARTIAL"):
            stage = "PARTIALLY_FILLED"
        elif to in ("FILLED", "CANCELLED", "EXPIRED"):
            stage = to
        elif to == "SUSPENDED":
            stage = "HELD"
        elif to in ("WORKING", "PARTIAL") and frm == "SUSPENDED":
            stage = "RESUMED"
        elif to == "WAITING_PARENT":
            stage = "WAITING_PARENT"
        else:
            stage = "EVENT"
        steps.append({"stage": stage, "words": TIMELINE_WORDS.get(stage, stage.lower()), "ts": int(e.get("ts") or 0), "note": note, "from_status": frm, "to_status": to})
    return steps


def _settlement_rows(services, conn, where: str, params: Sequence) -> List[dict]:
    """Settlement rows with their state in words (C15's ``collateral.settlement_state`` when that module is bound, else pending / settled)."""
    coll = getattr(services, "collateral", None) if services is not None else None
    state_fn = getattr(coll, "settlement_state", None) if coll is not None else None
    out = []
    for s in conn.execute(f"SELECT * FROM settlements WHERE {where} ORDER BY due_date, settle_id", tuple(params)).fetchall():
        d = dict(s)
        if callable(state_fn):
            try:
                d.update(state_fn(s))
            except Exception:   # noqa: BLE001
                pass
        if "stage" not in d:
            d.update({"stage": "SETTLED", "label": "settled", "chip": "filled"} if d.get("status") == "SETTLED"
                     else {"stage": "SETTLING", "label": f"pending settlement, due {d.get('due_date')}", "chip": "settling"})
        fs = d.get("fail_state")
        d["can_fail"] = d.get("status") == "PENDING" and fs != "FAILED" and not d.get("fx_deal_id")
        d["can_resolve"] = d.get("status") == "PENDING" and fs == "FAILED"
        out.append(d)
    return out


def _borrow_view(services, conn, borrow_id: Optional[str]) -> Optional[dict]:
    if not borrow_id or not _has_table(conn, "borrows"):
        return None
    b = conn.execute("SELECT * FROM borrows WHERE borrow_id=?", (borrow_id,)).fetchone()
    if b is None:
        return None
    d = dict(b)
    d["lender_stored"] = d.get("lender")
    d["lender"] = lender_words(services, d.get("lender"))
    return d


def preview_summary(raw) -> Optional[dict]:
    """The stored preview of an order in a few fields (the drawer's 'Created' line): time, expected price and basis, fee, cash need,
    sweep-in, settle date, session phase; an as-of booking says it had no live preview."""
    pv = _json(_get(raw, "preview_json"))
    if not pv:
        return None
    if pv.get("kind") == "as_of":
        return {"kind": "as_of", "note": pv.get("note") or "as-of booking at the owner's dealt price: no live preview", "price": pv.get("price")}
    ex = pv.get("execution") if isinstance(pv.get("execution"), dict) else {}
    eco = pv.get("economics") if isinstance(pv.get("economics"), dict) else {}
    fu = pv.get("funding") if isinstance(pv.get("funding"), dict) else {}
    ses = ex.get("session") if isinstance(ex.get("session"), dict) else {}
    return {"kind": "preview", "as_of": pv.get("as_of"), "expected_price": ex.get("expected_price"), "price_basis": ex.get("price_basis"),
            "fill_now": ex.get("fill_now"), "decision": ex.get("decision"), "gross": eco.get("gross"), "fee": eco.get("fee"), "net_cash": eco.get("net_cash"),
            "trade_date": eco.get("trade_date"), "settle_date": eco.get("settle_date"), "need": fu.get("need"), "available": fu.get("available"),
            "sweep_in": fu.get("sweep_in"), "session_phase": ses.get("phase"), "warnings": list(pv.get("warnings") or [])[:6]}


def order_detail(services, conn, order_id: str, *, review: bool = False, now: Optional[int] = None) -> dict:
    """GET /api/orders/{id} (C7 takeover; P2 tasks 4 and 6): ``api_views.order_full`` (row, fills, events, entries) plus the stage, the
    timeline, the settlements of its fills with their state, linked sweeps, borrow (lender in trader words), package legs with the
    derived package state, the stored preview's summary, what the row offers, and with ``review`` the pure review (OMS-08)."""
    from .api_views import order_full
    from .orders import _sweeps_for
    now = int(now if now is not None else services.clock.epoch())
    d = order_full(services, conn, order_id, with_events=True)
    raw = conn.execute("SELECT o.*, i.kind AS inst_kind, i.session_group AS inst_group FROM orders o LEFT JOIN instruments i ON i.instrument_id=o.instrument_id"
                       " WHERE o.order_id=?", (order_id,)).fetchone()
    acct = conn.execute("SELECT account_id, name, kind, broker, book_code, lifetime_id FROM accounts WHERE account_id=?", (raw["account_id"],)).fetchone()
    looks = _Looks(services, conn, now)
    live = raw["status"] in LIVE_ORDER
    rec = looks.quote(raw["instrument_id"]) if (live or review) else None
    session = looks.session(raw["inst_group"] or raw["session_group"], rec) if (live or review) else None
    kind = str(raw["inst_kind"] or "")
    d.update(order_stage(raw, now, session))
    d.update({"type_words": type_words(raw), "expiration_words": expiration_words(raw), "side_words": SIDE_WORDS.get(raw["side"], str(raw["side"]).replace("_", " ")),
              "asset_class": ASSET_CLASS.get(kind, kind.title() or "Other"), "kind": kind, "account_name": acct["name"] if acct else None,
              "book": acct["book_code"] if acct else None, "counterparty": _listed_counterparty(dict(acct), kind, raw["inst_group"]) if acct else "",
              "market": _market(rec, session, now) if rec is not None else None, "actions": _actions(raw)})
    d["timeline"] = order_timeline(raw, d.get("events") or [])
    fids = [f["fill_id"] for f in d.get("fills") or []]
    if fids:
        d["settlements"] = _settlement_rows(services, conn, f"fill_id IN ({','.join('?' for _ in fids)})", fids)
    else:
        d["settlements"] = []
    d["sweeps"] = _sweeps_for(conn, fids)
    d["borrow"] = _borrow_view(services, conn, raw["borrow_id"])
    d["preview_summary"] = preview_summary(raw)
    if live:
        try:
            snap = services.marks.snapshot() if hasattr(services.marks, "snapshot") else services.marks
            d["reserved_c"] = _reservations(services, conn, [raw], snap).get(order_id)
        except Exception:   # noqa: BLE001
            d["reserved_c"] = None
    if raw["package_id"]:
        legs = conn.execute("SELECT * FROM orders WHERE package_id=? ORDER BY created_ts, order_id", (raw["package_id"],)).fetchall()
        hp = conn.execute("SELECT * FROM hedge_packages WHERE package_id=?", (raw["package_id"],)).fetchone()
        state, words = package_state(legs)
        d["package"] = {"package_id": raw["package_id"], "strategy_key": _get(hp, "strategy_key") or _get(hp, "candidate_id"), "fill_mode": _get(hp, "fill_mode"),
                        "state": state, "state_words": words, "stored_status": _get(hp, "status"),
                        "legs": [dict({"order_id": l["order_id"], "instrument_id": l["instrument_id"], "side": l["side"], "qty": int(l["qty"]),
                                       "filled_qty": int(l["filled_qty"] or 0), "status": l["status"], "type_words": type_words(l)}, **order_stage(l, now))
                                 for l in legs]}
    else:
        d["package"] = None
    for k in ("replaces_order_id", "replaced_by_order_id"):
        d[k] = _get(raw, k)
    if review:
        from .orders_preview import bar_usage, with_nbbo
        from .orders_rules import OrderRow, review as _review
        inst = looks.inst(raw["instrument_id"])
        orow = OrderRow.from_row(raw, inst)
        r2 = with_nbbo(services, inst, rec, session, now) if inst is not None else rec
        d["review"] = _review(orow, r2, session, now, inst, bar_used=bar_usage(conn))
        d["review"]["market"] = _market(r2, session, now)
    return d


# ---- Completed Trades: the execution drawer, GET /api/executions/{id} (P2 task 4, OMS-14) ----------------------------------------------------
def _execution_account(conn, execution_id: str) -> Optional[str]:
    """The account an execution id belongs to (a fill, an FX deal, an OTC contract or event, a financing trade's reference)."""
    ref = str(execution_id).split(":", 1)[0]
    for table, col in (("fills", "fill_id"), ("fx_deals", "deal_id"), ("otc_contracts", "contract_id"), ("borrows", "borrow_id"), ("repos", "repo_id"),
                       ("loans", "loan_id"), ("sec_loans", "loan_id"), ("private_loans", "loan_id")):
        try:
            r = conn.execute(f"SELECT account_id FROM {table} WHERE {col}=?", (ref,)).fetchone()
        except sqlite3.Error:
            continue
        if r is not None:
            return str(r[0])
    r = conn.execute("SELECT account_id FROM events WHERE ref_id=? AND account_id IS NOT NULL ORDER BY id LIMIT 1", (ref,)).fetchone()
    return None if r is None else str(r[0])


def _entries(conn, where: str, params: Sequence, limit: int = 300) -> List[dict]:
    out = []
    for e in conn.execute(f"SELECT entry_id, command_id, ts, business_date, kind, memo, ref_type, ref_id, idem_key FROM journal_entries WHERE {where}"
                          f" ORDER BY entry_id LIMIT {int(limit)}", tuple(params)).fetchall():
        d = dict(e)
        lines = conn.execute("SELECT gl, currency, amount_c, account_id, instrument_id, memo FROM journal_lines WHERE entry_id=? ORDER BY line_no",
                             (int(e["entry_id"]),)).fetchall()
        d["lines"] = [dict(x) for x in lines]
        d["cash_c"] = sum(int(x["amount_c"]) for x in lines if x["gl"] == "1010")
        out.append(d)
    return out


def _command_audit(conn, command_id) -> Optional[dict]:
    if command_id is None:
        return None
    c = conn.execute("SELECT id, request_id, kind, actor, started_ts, finished_ts, app_version, payload_sha256, http_status FROM commands WHERE id=?",
                     (int(command_id),)).fetchone()
    return None if c is None else dict(c)


def execution_detail(services, execution_id: str, conn=None) -> dict:
    """GET /api/executions/{id} (OMS-14; module docstring).  RejectError NOT_FOUND for an unknown id.  Read-only."""
    from .marks import shared_reader
    from .commands import RejectError
    eid = str(execution_id or "")
    with shared_reader(services.db, conn) as c:
        acct = _execution_account(c, eid)
        row = None
        if acct is not None:
            row = next((r for r in execution_rows(services, c, account_id=acct) if str(r.get("execution_id")) == eid), None)
        if row is None:
            raise RejectError("NOT_FOUND", f"execution {eid} does not exist", {"execution_id": eid})
        kind = row["kind"]
        ref = eid.split(":", 1)[0]
        out = {"execution": row, "parent": None, "entries": [], "transfers": [], "settlements": [], "lots_opened": [], "lots_relieved": [], "realised_c": 0,
               "position": None, "audit": None}
        entry_ids: List[int] = []
        cmd_id = None
        if kind == "fill":
            f = c.execute("SELECT * FROM fills WHERE fill_id=?", (eid,)).fetchone()
            cmd = c.execute("SELECT command_id FROM journal_entries WHERE entry_id=?", (int(f["entry_id"]),)).fetchone()
            cmd_id = None if cmd is None else int(cmd[0])
            settles = _settlement_rows(services, c, "fill_id=?", (eid,))
            out["settlements"] = settles
            transfers = [dict(t) for t in c.execute("SELECT * FROM transfers WHERE linked_id=? ORDER BY ts, transfer_id", (eid,)).fetchall()]
            out["transfers"] = transfers
            where = ["(ref_type='fill' AND ref_id=?)", "idem_key LIKE ?"]
            params: List = [eid, f"collat:%:fill:{eid}"]
            sids = [s["settle_id"] for s in settles]
            if sids:
                where.append(f"(ref_type='settlement' AND ref_id IN ({','.join('?' for _ in sids)}))")
                params += sids
            ents = [int(s["entry_id"]) for s in settles if s.get("entry_id")] + [int(t["entry_id"]) for t in transfers if t.get("entry_id")]
            if ents:
                where.append(f"entry_id IN ({','.join('?' for _ in ents)})")
                params += ents
            out["entries"] = _entries(c, " OR ".join(where), params)
            out["lots_opened"] = [dict(l) for l in c.execute("SELECT * FROM lots WHERE open_fill_id=?", (eid,)).fetchall()]
            rel = [dict(x) for x in c.execute("SELECT lr.*, l.open_fill_id, l.unit_cost, l.side AS lot_side FROM lot_relief lr JOIN lots l ON l.lot_id=lr.lot_id"
                                              " WHERE lr.close_fill_id=? ORDER BY lr.id", (eid,)).fetchall()]
            out["lots_relieved"] = rel
            out["realised_c"] = sum(int(x["realised_c"]) for x in rel)
            if f["order_id"]:
                from .orders import order_view
                p = order_view(c, f["order_id"])
                p["events"] = [dict(e) for e in c.execute("SELECT ts, from_status, to_status, note FROM order_events WHERE order_id=? ORDER BY id",
                                                          (f["order_id"],)).fetchall()]
                raw = c.execute("SELECT * FROM orders WHERE order_id=?", (f["order_id"],)).fetchone()
                p["timeline"] = order_timeline(raw, p["events"])
                p.update(order_stage(raw, int(services.clock.epoch())))
                p["type_words"], p["expiration_words"] = type_words(raw), expiration_words(raw)
                out["parent"] = {"kind": "order", "order": p}
        elif kind == "fx_deal":
            d = c.execute("SELECT * FROM fx_deals WHERE deal_id=?", (ref,)).fetchone()
            cmd_id = int(d["command_id"]) if d is not None and d["command_id"] is not None else None
            out["settlements"] = _settlement_rows(services, c, "fx_deal_id=?", (ref,))
            out["transfers"] = [dict(t) for t in c.execute("SELECT * FROM transfers WHERE linked_id=? ORDER BY ts, transfer_id", (ref,)).fetchall()]
            ids = [int(d["entry_id"])] if d is not None and d["entry_id"] else []
            ids += [int(s["entry_id"]) for s in out["settlements"] if s.get("entry_id")] + [int(t["entry_id"]) for t in out["transfers"] if t.get("entry_id")]
            where, params = "(ref_type IN ('fx', 'fx_deal') AND ref_id=?)", [ref]
            if ids:
                where += f" OR entry_id IN ({','.join('?' for _ in ids)})"
                params += ids
            out["entries"] = _entries(c, where, params)
        elif kind == "otc":
            k = c.execute("SELECT * FROM otc_contracts WHERE contract_id=?", (ref,)).fetchone()
            cmd_id = int(row["parent_id"][2:]) if str(row.get("parent_id") or "").startswith("T-") else (int(k["command_id"]) if k is not None else None)
            out["transfers"] = [dict(t) for t in c.execute("SELECT * FROM transfers WHERE linked_id=? ORDER BY ts, transfer_id", (ref,)).fetchall()]
            scols = _cols(c, "settlements")
            if "ref_id" in scols:
                out["settlements"] = _settlement_rows(services, c, "ref_type='otc' AND ref_id=?", (ref,))
            out["entries"] = _entries(c, "ref_type='otc' AND ref_id=?", (ref,))
            if k is not None:
                out["contract"] = {kk: k[kk] for kk in k.keys() if kk != "terms_json"}
                out["contract"]["terms"] = _json(k["terms_json"])
            # P4-R2 (OMS-10): the contract's life - requested, quoted, accepted, booked, IM, each VM, resets, a credit event, the end
            tl = getattr(getattr(services, "swaps", None), "contract_timeline", None)
            try:
                out["timeline"] = tl(services, ref, conn=c) if callable(tl) else []
            except Exception:   # noqa: BLE001 - the drawer still opens; the timeline is said unavailable
                out["timeline"] = []
                out["timeline_note"] = "the contract timeline could not be read now"
        else:                                                   # a financing trade
            cmd_id = int(row["parent_id"][2:]) if str(row.get("parent_id") or "").startswith("T-") else None
            out["transfers"] = [dict(t) for t in c.execute("SELECT * FROM transfers WHERE linked_id=? ORDER BY ts, transfer_id", (ref,)).fetchall()]
            out["entries"] = _entries(c, "ref_id=? AND ref_type IN ('borrow', 'repo', 'loan', 'sec_loan', 'private_loan')", (ref,))
            if row["side"].startswith("BORROW"):
                out["borrow"] = _borrow_view(services, c, ref)
        if cmd_id is not None and out["parent"] is None:
            a = _command_audit(c, cmd_id)
            out["parent"] = {"kind": "ticket", "ticket_id": f"T-{cmd_id}", "command": a,
                             "words": RECORD_WORDS.get(kind, kind) + (f" booked by command {a['kind'].replace('.', ' ')}" if a else "")}
        audit = _command_audit(c, cmd_id)
        out["audit"] = {"command": audit, "booking": row.get("booking"), "entered_ts": row.get("entered_ts"), "batch_id": row.get("batch_id"),
                        "source": row.get("source"), "source_label": row.get("source_label"), "import_key": row.get("import_key")}
        if kind in ("fill",):
            try:
                snap = services.marks.snapshot() if hasattr(services.marks, "snapshot") else None
                pos = [p for p in services.accounting.positions(row["account_id"], snapshot=snap) if p.instrument_id == row["instrument_id"]]
            except Exception:   # noqa: BLE001 - the position is shown unavailable, never a failed drawer
                pos = None
            if pos is None:
                out["position"] = {"available": False, "note": "the position could not be valued now"}
            elif not pos:
                out["position"] = {"available": True, "open": False, "note": f"no open position in {row['symbol']} now (closed)"}
            else:
                p = pos[0]
                out["position"] = {"available": True, "open": True, "qty": int(p.qty), "avg_px": None if p.avg_px is None else str(p.avg_px),
                                   "cost_c": int(p.cost_c), "mv_c": p.mv_c, "unrealised_c": p.unrealised_c, "realised_itd_c": int(p.realised_itd_c),
                                   "mark": None if p.mark is None else (p.mark.to_dict() if hasattr(p.mark, "to_dict") else None),
                                   "href": f"#/a/{row['account_id']}/positions"}
        out["settlement_actions"] = getattr(services, "collateral", None) is not None
        return out


# ---- the routes (C7; 1.0.5: the executions; 1.0.6: pending, counts, the execution drawer, the order route, amend, replace) -----------
AMEND_BODY = ("qty", "limit_px", "stop_px", "trail_amt", "trail_pct", "tif", "gtd_date", "preview_id", "confirm_large", "order_type")


def register_routes(router) -> None:
    """``GET /api/blotter/executions`` (1.0.5), ``GET /api/blotter/pending``, ``GET /api/blotter/counts``, ``GET /api/executions/{id}``,
    ``GET /api/orders/{id}`` (takes over the order route: an optional module's routes come first), ``PATCH /api/orders/{id}`` (amend)
    and ``POST /api/orders/{id}/replace`` (C7, 15.3 E3)."""
    router.add("GET", r"/api/blotter/executions", executions_route)
    router.add("GET", r"/api/blotter/pending", pending_route)
    router.add("GET", r"/api/blotter/counts", counts_route)
    router.add("GET", r"/api/executions/([^/]+)", execution_route)
    router.add("GET", r"/api/orders/([^/]+)", order_route)
    router.add("PATCH", r"/api/orders/([^/]+)", amend_route)
    router.add("POST", r"/api/orders/([^/]+)/replace", replace_route)


def _counts_scope(lids, account) -> str:
    return account or (f"{lids[0]}:ALL" if lids and len(lids) == 1 else "ALL_BOOKS")


def pending_route(req):
    """GET /api/blotter/pending (C7): ``{scope, status, total, offset, limit, next_offset, rows, counts, notes, statuses, stages,
    asset_classes}``; ``status`` open (default) / filled / cancelled / expired / rejected / all; ``account``, ``asset_class`` (comma list,
    ``!`` excludes), ``instrument``; ``limit`` / ``offset`` paging (default 200, newest first, package legs together)."""
    from .api import ApiError
    from .marks import shared_reader
    rt = req.rt
    lids, account, label = _scope(req)
    status = (req.q("status") or "open").lower()
    if status not in PENDING_FILTERS:
        raise ApiError(400, "BAD_REQUEST", f"status must be one of {', '.join(PENDING_FILTERS)}", {"status": status})
    marks = getattr(rt, "marks", None)
    snap = marks.snapshot() if marks is not None and hasattr(marks, "snapshot") else marks
    with shared_reader(rt.db) as conn:
        rows, notes = pending_rows(rt, conn, lifetime_ids=lids, account_id=account, status=status, now=req.now, snapshot=snap)
    rows = filter_pending(rows, account=None if account else req.q("account"), asset_class=req.q("asset_class"), instrument=req.q("instrument"))
    total = len(rows)
    offset = req.q_int("offset", 0, 0, 10**7)
    limit = req.q_int("limit", 200, 1, 5000)
    page = rows[offset:offset + limit]
    nxt = offset + len(page)
    return {"scope": label, "lifetime_ids": lids or [], "account_id": account, "status": status, "total": total, "offset": offset, "limit": limit,
            "next_offset": nxt if nxt < total else None, "rows": page, "counts": counts(rt, _counts_scope(lids, account)), "notes": notes,
            "statuses": [{"key": k, "label": {"open": "open (working, partly filled, held, queued)", "all": "all (the order history)"}.get(k, k)}
                         for k in PENDING_FILTERS],
            "stages": [{"key": k, "words": w, "chip": STAGE_CHIP[k]} for k, w in STAGE_WORDS.items()], "asset_classes": list(ASSET_CLASSES)}


def counts_route(req):
    """GET /api/blotter/counts?book= (the Dashboard tiles and the sidebar badges): ``{scope, counts}``."""
    lids, account, label = _scope(req)
    return {"scope": label, "counts": counts(req.rt, _counts_scope(lids, account))}


def execution_route(req):
    """GET /api/executions/{id} (OMS-14): ``execution_detail``; 404 for an unknown id."""
    from .api import NotFound
    from .commands import RejectError
    try:
        return execution_detail(req.rt, req.params[0])
    except RejectError as e:
        if e.code == "NOT_FOUND":
            raise NotFound(e.message)
        raise


def order_route(req):
    """GET /api/orders/{id}[?review=1] (C7 takeover): ``order_detail``; 404 for an unknown order."""
    from .api import NotFound
    from .marks import shared_reader
    rt, oid = req.rt, req.params[0]
    with shared_reader(rt.db) as conn:
        if conn.execute("SELECT 1 FROM orders WHERE order_id=?", (oid,)).fetchone() is None:
            raise NotFound(f"order {oid} does not exist")
        return order_detail(rt, conn, oid, review=str(req.q("review") or "").lower() in ("1", "true", "yes"), now=req.now)


def _order_command(req, kind: str, keys: Sequence[str]):
    from .api import NotFound, run_command
    rt, oid = req.rt, req.params[0]
    with rt.db.reader() as conn:
        row = conn.execute("SELECT account_id FROM orders WHERE order_id=?", (oid,)).fetchone()
    if row is None:
        raise NotFound(f"order {oid} does not exist")
    try:
        from .api_products import book_guard
        book_guard(req, row["account_id"])
    except ImportError:   # pragma: no cover - the guard is the API's; without it the command still checks everything
        pass
    payload = {k: req.b(k) for k in keys if req.b(k) is not None}
    payload["order_id"] = oid
    res = run_command(req, kind, payload)
    if res.ok and not res.replayed:
        m = getattr(rt, "matcher", None)
        if m is not None and hasattr(m, "wake"):
            m.wake()
    return res


def amend_route(req):
    """PATCH /api/orders/{id} ``{request_id, qty?, limit_px?, stop_px?, trail_amt? | trail_pct?, tif?, gtd_date?, preview_id?, confirm_large?}``:
    ``order.amend`` (OMS-03)."""
    return _order_command(req, "order.amend", AMEND_BODY)


def replace_route(req):
    """POST /api/orders/{id}/replace ``{request_id, order_type?, qty?, limit_px?, stop_px?, trail_amt? | trail_pct?, tif?, gtd_date?,
    preview_id?, confirm_large?}``: ``order.replace`` (OMS-03)."""
    return _order_command(req, "order.replace", AMEND_BODY)


def _scope(req) -> Tuple[Optional[List[str]], Optional[str], str]:
    """``(lifetime_ids, account_id, scope label)`` of a request: ``account`` or an account-id ``scope`` -> that account;
    ``ALL_BOOKS`` / ``all=1`` -> every open lifetime; ``<lifetime>:ALL`` -> that lifetime; else the selected book."""
    from . import books as _books
    from .api import NotFound
    rt = req.rt
    account = req.q("account")
    scope = req.q("scope") or ""
    with rt.db.reader() as conn:
        if not account and scope and not scope.endswith(":ALL") and scope != "ALL_BOOKS" and ":" in scope:
            account = scope
        if account:
            if conn.execute("SELECT 1 FROM accounts WHERE account_id=?", (account,)).fetchone() is None:
                raise NotFound(f"account {account} does not exist")
            return None, account, account
        if scope == "ALL_BOOKS" or req.q("all") in ("1", "true", "yes"):
            return list(_books.open_lifetimes(conn)), None, "ALL_BOOKS"
        if scope.endswith(":ALL"):
            lid = scope[:-4]
            if _books.lifetime_row(conn, lid) is None:
                raise NotFound(f"lifetime {lid} does not exist")
            return [lid], None, scope
    lid = req.lifetime()
    return [lid], None, f"{lid}:ALL"


def executions_route(req):
    """GET /api/blotter/executions (C7): the scope's executions after the filters, ``limit`` / ``offset`` paged (default 200,
    newest first), or ``format=csv``.  Answer: ``{scope, lifetime_ids, total, offset, limit, next_offset, rows, counts, batches,
    statuses, sources, asset_classes}``; every row carries the directive's Completed-Trades fields (section 3B)."""
    rt = req.rt
    lids, account, label = _scope(req)
    with rt.db.reader() as conn:
        rows = execution_rows(rt, conn, lifetime_ids=lids, account_id=account)
    batches = sorted({str(r["batch_id"]) for r in rows if r.get("batch_id")})
    rows = filter_rows(rows, date_from=req.q("from"), date_to=req.q("to"), instrument=req.q("instrument"), account=req.q("account"),
                       asset_class=req.q("asset_class"), counterparty=req.q("counterparty"), status=req.q("status"), source=req.q("source"),
                       batch=req.q("batch"), record=req.q("record"))
    total = len(rows)
    csv_out = (req.q("format") or "json").lower() == "csv"
    paged = not csv_out or req.q("limit") is not None or req.q("offset") is not None
    offset = req.q_int("offset", 0, 0, 10**7)
    limit = req.q_int("limit", 200, 1, 5000)
    page = rows[offset:offset + limit] if paged else rows
    if csv_out:
        name = (account or label).replace(":", "-")
        return 200, executions_csv(page), {"Content-Type": "text/csv; charset=utf-8", "Content-Disposition": f'attachment; filename="{name}-executions.csv"'}
    nxt = offset + len(page)
    return {"scope": label, "lifetime_ids": lids or [], "account_id": account, "total": total, "offset": offset, "limit": limit,
            "next_offset": nxt if nxt < total else None, "rows": page, "counts": counts_of(rows), "batches": batches,
            "statuses": [{"key": k, "label": v} for k, v in STATUS_WORDS.items()], "sources": list(SOURCE_KEYS), "asset_classes": list(ASSET_CLASSES)}


__all__ = ["AS_OF", "HISTORICAL_MODEL", "SIMULATED_LABEL", "HISTORICAL_MODEL_LABEL", "SYSTEM_EVENTS", "STATUS_WORDS", "SOURCE_KEYS", "ASSET_CLASSES",
           "CSV_COLUMNS", "source_label", "dealer_name", "lender_words", "pct_text", "execution_rows", "filter_rows", "counts_of", "executions_csv",
           "register_routes", "executions_route", "STAGE_WORDS", "STAGE_CHIP", "PENDING_FILTERS", "order_stage", "package_state", "expiration_words",
           "type_words", "pending_rows", "filter_pending", "counts", "order_timeline", "preview_summary", "order_detail", "execution_detail",
           "pending_route", "counts_route", "execution_route", "order_route", "amend_route", "replace_route"]
