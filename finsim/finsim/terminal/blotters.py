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
  OTC contract's booking, termination and maturity, and the financing trades (stock borrows opened / returned, securities lent /
  recalled, repos opened / rolled / closed, cash loans drawn / repaid, private-credit commitments and repayments) with asset class
  "Financing".  Automatic PB financing draws are funding, not trades (the cash pages show them).  Every row names its parent: the
  order id, or ``T-<command id>`` of the command that booked an immediate ticket, a system event or a financing trade (D5).
  Query: ``scope`` (``<lifetime>:ALL`` | ``ALL_BOOKS`` | an account id; default the selected book), ``book``, ``account``, ``from`` /
  ``to`` (trade date, inclusive), ``instrument`` (id, symbol or an option's underlying), ``asset_class`` (comma list; ``!Financing``
  excludes), ``counterparty`` (contains), ``status`` and ``source`` (comma lists of the keys below), ``batch``, ``limit`` / ``offset``
  (paging, newest first), ``format=csv`` (every column; all matching rows unless ``limit`` / ``offset`` are given).

Statuses (``status`` key -> words): executed "Executed"; settling "Settling"; settled "Settled"; failed "Failed"; resolved "Resolved"
(a settlement failure and its resolution, C15); daily_vm "daily VM" (futures); collateral "proceeds applied to the borrow collateral"
(an opening short whose proceeds went to the lender); no_cash_leg "no cash leg" (an expiry at 0); otc_margin "IM posted, CTM daily VM"
(an open OTC contract; STM contracts settle daily); open / closed (a financing trade still open / since closed), rolled, returned,
recalled, repaid; terminated / matured (OTC).
Sources (``source`` key): simulated, as_of, historical_model, system.

Not in 1.0.5 (15.3 E1 / E3, phase 2): the pending blotter route, ``counts``, the stage vocabulary and ``execution_detail`` (the
GET /api/executions/{id} drawer with the settlement fail / resolve actions).  ``execution_detail`` is the probe of the "simulated
counterparty settlement fails" Unsupported row, so it is deliberately absent here.

The module has no commands and no schema of its own (C8's columns are added by ``orders.bind``); it reads optional columns and
tables (``booking``, the C15 settlement fail columns, ``sec_loans``) only when they exist, so it imports, registers and serves on
any database a 1.0.x release opened (15.2 step 3, S-14).
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
                "terminated": "terminated", "matured": "matured"}
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
OTC_EVENTS = {"OTC_TERMINATED": "terminated", "SWAP_MATURED": "matured", "OTC_EXPIRED": "matured"}
#: the CSV columns, in order: (header, row key, how) - how: "c" money cents, "ts" New York time, else the value as is
CSV_COLUMNS = (("execution_id", "execution_id", ""), ("parent", "parent_id", ""), ("account_id", "account_id", ""), ("account", "account_name", ""),
               ("book", "book", ""), ("instrument_id", "instrument_id", ""), ("instrument", "symbol", ""), ("asset_class", "asset_class", ""),
               ("side", "side_words", ""), ("quantity", "qty", ""), ("notional", "notional_c", "c"), ("currency", "currency", ""), ("price", "price", ""),
               ("price_kind", "price_label", ""), ("gross", "gross_c", "c"), ("fees", "fee_c", "c"), ("fees_ledger", "fees_journal_c", "c"),
               ("execution_time", "exec_ts", "ts"), ("trade_date", "trade_date", ""), ("settle_date", "settle_date", ""), ("settled_time", "settled_ts", "ts"),
               ("counterparty", "counterparty", ""), ("status", "status_label", ""), ("source", "source_label", ""), ("entered_time", "entered_ts", "ts"),
               ("batch", "batch_id", ""), ("record", "kind", ""), ("origin", "origin", ""), ("booking", "booking", ""), ("note", "note", ""))


# ---- small helpers -----------------------------------------------------------------------------------------------------------
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
            "note": r["cap_note"] or ""})
        out.append(row)
    return out


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
                          f" LEFT JOIN commands c ON c.id=e.command_id WHERE e.type IN ('OTC_TERMINATED','SWAP_MATURED','OTC_EXPIRED') AND e.ref_type='otc'"
                          f" AND e.account_id IN ({q}) ORDER BY e.id", ids).fetchall():
        r = contracts.get(e["ref_id"])
        if r is None:
            continue
        acct = accts[r["account_id"]]
        terms = _json(r["terms_json"])
        p = _json(e["payload_json"])
        symbol, side_words, qty, _price, _kind = _otc_describe(r["kind"], terms, r)
        verb = OTC_EVENTS[e["type"]]
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
                source: Optional[str] = None, batch: Optional[str] = None) -> List[dict]:
    """The C7 filters (all optional; see the module docstring)."""
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


# ---- the route (C7, 15.3 E3) -----------------------------------------------------------------------------------------------------
def register_routes(router) -> None:
    """``GET /api/blotter/executions`` (C7).  The pending route, ``counts`` and the execution detail are phase 2 (E1 / E3)."""
    router.add("GET", r"/api/blotter/executions", executions_route)


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
                       batch=req.q("batch"))
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
           "register_routes", "executions_route"]
