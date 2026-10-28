"""Cash, settlement and collateral (FINSIM1_PLAN P6; phase 1 = Part A, release 1.0.5; contracts C15, C17, C21, and C16's
lender wording, which lives in ``lending.lender_label``).

Optional module ``collateral`` (``server.OPTIONAL_MODULES``).  ``bind(rt)`` adds the settlement fail columns and the CSA
terms table (``Database.ensure_schema_ext``, additive only, D2) and registers the restricted-cash adjuster with the
accounting views (C9b, when that hook is in the build); ``register(bus)`` adds the commands; ``register_routes(router)``
the settlement and restricted-cash routes.  Nothing here is a lifecycle job in 1.0.5 (the MARGIN_CALL job is phase 2), so
the import's catch-up cutoff never moves with this module.  Phase 2 (1.0.6) adds the margin calls (``margin_calls_rows``),
the CSA pledges (``pledges_view``), the account-level Reg T margin and ``summary`` / ``position_requirements`` /
``custodian_of``; none of them exists here, not even as a stub (15.3 E1 / E4), so the Unsupported page keeps listing them.

Conventions stated here (each one a rule the tests pin)
- SETTLEMENT FAIL STATE (C15, OMS-09, TEST-03): ``settlements`` gains ``fail_state`` (NULL | FAILED | RESOLVED),
  ``fail_kind`` (UNFUNDED | SIMULATED), ``fail_reason``, ``failed_ts``, ``resolved_ts``, ``resolution`` (SETTLED | RETRY |
  CASH_COMPENSATION | BUY_IN) and ``ref_type`` / ``ref_id`` (the OTC payment a settlement belongs to; P4 writes them).
  ``status`` keeps its CHECK (PENDING | SETTLED): a FAILED row is still PENDING, so L10 (pending settlements = the
  receivable / payable balances) holds and no cash moves while it is FAILED.
  * UNFUNDED: a payable that SETTLE_DUE cannot fund after its due date has passed (the accounting day is later than the
    due date: the 17:00 ET cut-off of the due date went by with the cash leg unpaid) becomes FAILED "not funded on its due
    date", keeps its ERROR attention SETTLEMENT_UNFUNDED and keeps retrying; when it settles it becomes RESOLVED
    (``resolution`` SETTLED, ``settled_ts`` / ``resolved_ts`` = when it settled, its entry dated the day it settled).
  * SIMULATED (Q11): ``settlement.fail {settle_id, reason}`` is the owner's own simulation of a counterparty fail - off by
    default, it never happens by itself - labelled "simulated counterparty fail".  While FAILED, SETTLE_DUE skips the row
    and nothing moves.  ``settlement.resolve {settle_id, action}`` ends it:
      - ``retry``: the row goes back to normal settlement (SETTLE_DUE settles it on or after its due date) and becomes
        RESOLVED (RETRY) when it settles;
      - ``cash_compensation {amount}``: the cash leg settles now in full and the failing counterparty pays ``amount`` for the
        late settlement: the settlement entry as usual plus ``Dr 1010 / Cr 4300`` (interest income: compensation for the
        delay; instrument = the trade's instrument), idem ``settlefail:comp:{settle_id}``;
      - ``buy_in {cost}`` (a purchase the selling counterparty failed to deliver): the obligation settles now (the payable
        funded and paid as usual) and the extra cost of buying the securities in elsewhere posts separately ``Dr 5700 / Cr
        1010`` (buy-in penalties; funded like any payment), idem ``settlefail:buyin:{settle_id}``.  A receivable cannot be
        bought in (409 BUY_IN_NOT_APPLICABLE).
    A fail of the fund's own (UNFUNDED) resolves only by funding: cash compensation and buy-in are the counterparty's
    remedies (409 NOT_A_COUNTERPARTY_FAIL).  FX deal legs settle both legs together: they cannot be failed by hand
    (409 FX_DEAL_LEG).  Worked (test a): a BUY of 100 AAPL @ 339.50 + fee 1.00 -> payable 33,951.00 due Wed; the owner
    fails it Tue -> Wed 00:35 nothing moves; retry -> Wed settles 33,951.00, RESOLVED (RETRY).
  * ``settlement_state(row) -> {stage, label, chip}``: SETTLING / FAILED / SETTLED / RESOLVED in trader words (P2's
    Completed Trades status column and drawer read it).
- RESTRICTED CASH (C21, D9, PORTFOLIO-10): ``restricted_c(conn, account, ccy, csa_netting=False, as_of=None)`` = the
  account's net credit support received under its CSA, ``max(0, VM received (2330) - VM posted (1320))`` (one CSA per PB
  relationship, every contract of the account netted), plus the securities-lending cash collateral received (2520, per
  lending agreement, never netted); natural signs.  ``csa_netting=True`` (a VM / swap payment to the same CSA
  counterparty) leaves only the 2520 part: a payment to GS may use GS's collateral, nothing else may.  ``as_of`` = D reads
  business-date balances (journal lines with business_date <= D, C9h): the as-of funding and the as-of sweep use the
  restricted amount of THAT date, never today's.  A per-CSA "reuse allowed" flag (``csa_terms``, default off, Q7; the
  editor ships in 1.0.6) releases the CSA part when the owner switches it on.  Worked (test b): TRS VM received 600,921.00,
  CDX / IRS VM posted 309,331.86 -> restricted 291,589.14; the 10-22 close of the import: 489,357.00 - 309,027.55 =
  180,329.45.
- The C9b adjuster "restricted (collateral received)" subtracts it from available, spendable, immediate available and
  buying power (and SWEEP_OUT subtracts ``accounting.adjustments_c``).  It reads 0 on a database where this module never
  bound (no ``csa_terms`` table), so a process-wide adjuster registry never changes another runtime's figures.  PB credit
  interest is earned on ``max(0, settled - restricted)`` (``prime.accrual_lines_pb_credit``); CSA interest on cash
  collateral is not simulated (the Unsupported row).
- AS-OF FUNDING SUPPORT (C15, C17; actor ``import`` only - ``ui`` and every other actor get 403 AS_OF_FORBIDDEN):
  * ``cash.asof_sweep {lifetime_id, accounts, from_date, through_date, batch_id, groups}``: for each date d ascending and
    each listed account (Treasury and accounts with the sweep switched off are never swept) and currency (USD; a buffer
    is in USD), ``sweep = min(excess, import cash)`` with ``excess = settled - restricted(as_of=d) - payables - buffer`` at
    the close of d (business date) and ``import cash`` = the account's settled cash the import's groups created (their
    1010 lines with business_date <= d; ``group_entry_ids``) less this batch's earlier as-of sweeps of that account;
    ``sweep > 0`` -> one SWEEP_OUT to Treasury dated d, labelled "as-of sweep", idem
    ``asofsweep:{batch}:{account}:{ccy}:{d}`` (a rerun posts nothing).  The account's current settled cash must stay at
    or above zero (L18): a sweep that would overdraw it today is refused 409 ASOF_CURRENT_FUNDS before anything is written.
    Worked (test h): settled 786,553.40, 2330 489,357.00, 1320 309,027.55 (restricted 180,329.45), buffer 10,000.00 ->
    596,223.95 dated D; an account with 509,392.94 of pre-import excess and no import cash is not swept.
  * ``cash.asof_transfer {from_account, to_account, amount, business_date, reason, batch_id, line_key}``: an internal
    transfer dated ``business_date``, stored as ``transfers.kind`` TRANSFER (the CHECK is not widened, D2) with
    ``linked_type`` "asof_funding" / ``linked_id`` = the line key; its journal entry kind ASOF_FUNDING, memo "as-of
    funding" (the reason is on the lines and the event); NAV-neutral, L5 nets it; the source must hold the amount at the
    close of that date and today (409 INSUFFICIENT_FUNDS / ASOF_CURRENT_FUNDS); idem
    ``asofxfer:{batch}:{line_key}:{from}:{to}:{ccy}:{date}`` (a rerun returns the stored transfer).
  * ``cash.reaccrue {lifetime_id, accounts, kinds, from_night, through_night?, reason, batch_id}`` (C17): for every listed
    account and kind (``cashint`` Treasury; ``pbcredit`` / ``pb`` prime-broker accounts) it recomputes each night's base
    on business-date balances (cashint: settled; pbcredit: max(0, settled - restricted as of that night); pb: the 2700
    balance) at the rate that night's original accrual used (its ACCRUED event), with ``accrue_segment``'s rounding (a
    segment = consecutive nights with the same base and rate; cumulative = round half-even(base x rate x n / 360)), and
    posts per night ``restated - (original + earlier corrections)`` with business date night + 1, idem
    ``reaccrue:{batch}:{account}:{kind}:{night}``.  ``through_night`` defaults to the account's ``accrued_through``; the
    accrual cursor is moved onto the restated segment so the next live night continues it.  Worked (test e): Treasury base
    1,000,000.00 lower on nights at 3.38 % and 3.40 % -> -93.89 and -94.44; PB credit base +10,000.00 at 3.13 % -> +0.87;
    two nights at 3.15 % -> +0.88 then +0.87.
- As-of dating of SETTLE_DUE (C15) lives in ``lifecycle_jobs.SettleDue``: a settlement whose fill or contract carries
  booking AS_OF posts with business_date = its due date and ``ctx.asof_date`` = the due date (its funding sweep too).
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .books import account_row, lifetime_row, split_account_id
from .clock import ny_of
from .commands import ConflictError, RejectError, TxContext
from .ledger import Entry, balance, cr, dr, natural
from .units import fmt_money, from_c, qprice, to_c

#: the settlement columns C15 adds (any writer may repeat the identical statements: ``ensure_schema_ext`` is idempotent)
SETTLEMENT_COLUMNS = [
    "ALTER TABLE settlements ADD COLUMN fail_state TEXT",
    "ALTER TABLE settlements ADD COLUMN fail_reason TEXT",
    "ALTER TABLE settlements ADD COLUMN failed_ts INTEGER",
    "ALTER TABLE settlements ADD COLUMN resolved_ts INTEGER",
    "ALTER TABLE settlements ADD COLUMN resolution TEXT",
    "ALTER TABLE settlements ADD COLUMN ref_type TEXT",
    "ALTER TABLE settlements ADD COLUMN ref_id TEXT",
    "ALTER TABLE settlements ADD COLUMN fail_kind TEXT",
]
#: one row per CSA (= per PB account): the "reuse allowed" flag of Q7, default off (no row = off)
CSA_TERMS_TABLE = ("CREATE TABLE IF NOT EXISTS csa_terms (account_id TEXT PRIMARY KEY, reuse_allowed INTEGER NOT NULL DEFAULT 0"
                   " CHECK (reuse_allowed IN (0,1)), updated_ts INTEGER NOT NULL, command_id INTEGER)")
SCHEMA = SETTLEMENT_COLUMNS + [CSA_TERMS_TABLE]

ADJUSTER_KEY = "restricted"
ADJUSTER_LABEL = "restricted (collateral received)"
RESTRICTED_TEXT = ("restricted = variation margin received under the CSA net of variation margin posted (floored at 0; one CSA per prime"
                   " broker) + cash collateral received for securities lent; it is the counterparty's collateral: excluded from available,"
                   " buying power and the sweep, earns no credit interest; a variation-margin payment to the same counterparty may use it")
FAIL_STATES = ("FAILED", "RESOLVED")
FAIL_KINDS = ("UNFUNDED", "SIMULATED")
RESOLUTIONS = ("SETTLED", "RETRY", "CASH_COMPENSATION", "BUY_IN")
ACTIONS = ("retry", "cash_compensation", "buy_in")
SIMULATED_LABEL = "simulated counterparty fail"
UNFUNDED_LABEL = "not funded on its due date"
IMPORT_ACTOR = "import"
REACCRUE_KINDS = ("cashint", "pbcredit", "pb")
ACCRUAL_BASIS = 360
SWEEP_CCYS = ("USD",)


class ForbiddenError(RejectError):
    """403: the as-of commands belong to the historical import (actor ``import``); the terminal's own UI never runs them."""
    http = 403


# ---- small helpers ----------------------------------------------------------------------------------------------------------
def _iso(d) -> str:
    if isinstance(d, date):
        return d.isoformat()
    s = str(d or "").strip()[:10]
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError as e:
        raise RejectError("DATE_INVALID", f"a date must be YYYY-MM-DD, got {d!r}") from e


def _date(d) -> date:
    return date.fromisoformat(_iso(d))


def _keys(row) -> Set[str]:
    try:
        return set(row.keys())
    except AttributeError:
        return set(row or {})


def _get(row, key: str, default=None):
    return row[key] if key in _keys(row) and row[key] is not None else default


def _table_exists(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def has_fail_columns(conn) -> bool:
    """True once ``bind`` (or another writer of C15's statements) added the fail columns to ``settlements``."""
    return "fail_state" in {r[1] for r in conn.execute("PRAGMA table_info(settlements)").fetchall()}


def schema_ready(conn) -> bool:
    """True on a database where this module bound (its ``csa_terms`` table exists)."""
    return _table_exists(conn, "csa_terms")


def _require_import(ctx: TxContext, what: str) -> None:
    if str(getattr(ctx, "actor", "") or "") != IMPORT_ACTOR:
        raise ForbiddenError("AS_OF_FORBIDDEN", f"{what} belongs to the historical import (run by the import tool), not to the terminal's screens",
                             {"actor": getattr(ctx, "actor", None)})


def _services(ctx: TxContext, services=None):
    s = services if services is not None else getattr(ctx, "services", None)
    if s is None:
        raise RejectError("NO_SERVICES", "the command context carries no services bundle")
    return s


def _amount(payload: dict, key: str) -> int:
    """A positive money amount from ``payload[key]`` (decimal string, read strictly) or ``payload[key + '_c']`` (cents)."""
    from .units import parse_money
    raw_c = payload.get(key + "_c")
    if raw_c is not None:
        if isinstance(raw_c, bool) or not isinstance(raw_c, int) or raw_c <= 0:
            raise RejectError("AMOUNT", f"{key}_c must be a positive whole number of cents, got {raw_c!r}")
        return int(raw_c)
    raw = payload.get(key)
    if raw is None or str(raw).strip() == "":
        raise RejectError("AMOUNT", f"{key} is required")
    try:
        v = parse_money(str(raw).strip().replace(",", ""), key)
    except ValueError as e:
        raise RejectError("AMOUNT", str(e), {key: str(raw)}) from e
    if v <= 0:
        raise RejectError("AMOUNT", f"{key} must be positive")
    return v


# ---- business-date balances (C9h shape, read here) -----------------------------------------------------------------------
def bd_balance_c(conn, account_id: str, gl: str, ccy: str, d) -> int:
    """Natural-sign balance of (account, gl, ccy) over every instrument at the close of business date ``d``: the journal lines
    whose entry has ``business_date <= d``."""
    row = conn.execute("SELECT COALESCE(SUM(l.amount_c), 0) FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id"
                       " WHERE l.account_id=? AND l.gl=? AND l.currency=? AND e.business_date<=?", (account_id, gl, ccy, _iso(d))).fetchone()
    return natural(gl, int(row[0] or 0))


def gl_c(conn, account_id: str, gl: str, ccy: str, as_of=None) -> int:
    """Today's natural balance (``ledger.balance``) or, with ``as_of``, the business-date one."""
    if as_of is None:
        return balance(conn, account_id, gl, ccy)
    return bd_balance_c(conn, account_id, gl, ccy, as_of)


def settled_at_close_c(conn, account_id: str, ccy: str, d) -> int:
    """Settled cash at the close of business date ``d`` (``treasury.settled_at_close_c``: C9h ``cash_view_at`` when present)."""
    from .treasury import settled_at_close_c as _s
    return _s(conn, account_id, ccy, d)


# ---- C21: restricted cash, net per CSA ---------------------------------------------------------------------------------------
def reuse_allowed(conn, account_id: str) -> bool:
    """Q7: the CSA's "reuse allowed" flag of a PB account (off by default; off when the table does not exist)."""
    if not schema_ready(conn):
        return False
    row = conn.execute("SELECT reuse_allowed FROM csa_terms WHERE account_id=?", (account_id,)).fetchone()
    return bool(row is not None and int(row["reuse_allowed"] or 0))


def restricted_parts(conn, account_id: str, ccy: str, as_of=None) -> dict:
    """Every component of ``restricted_c`` (natural cents): VM received (2330), VM posted (1320), their CSA net floored at 0,
    the reuse flag, the CSA part counted, securities-lending cash collateral received (2520) and the total."""
    received = gl_c(conn, account_id, "2330", ccy, as_of)
    posted = gl_c(conn, account_id, "1320", ccy, as_of)
    seclend = max(0, gl_c(conn, account_id, "2520", ccy, as_of))
    net = max(0, received - posted)
    reuse = reuse_allowed(conn, account_id)
    csa = 0 if reuse else net
    return {"account_id": account_id, "currency": ccy, "as_of": None if as_of is None else _iso(as_of), "vm_received_c": received, "vm_posted_c": posted,
            "csa_net_c": net, "reuse_allowed": reuse, "csa_restricted_c": csa, "seclend_cash_c": seclend, "restricted_c": csa + seclend,
            "definition": RESTRICTED_TEXT}


def restricted_c(conn, account_id: str, ccy: str, csa_netting: bool = False, as_of=None) -> int:
    """C21: cents of the account's cash that are the counterparties' collateral (module docstring RESTRICTED CASH).
    ``csa_netting=True``: only the securities-lending part (a payment to the CSA counterparty may use its collateral)."""
    p = restricted_parts(conn, account_id, ccy, as_of)
    return p["seclend_cash_c"] if csa_netting else p["restricted_c"]


def restricted_adjuster(conn, account_id: str, ccy: str, snapshot=None, master=None) -> int:
    """The C9b adjuster "restricted (collateral received)": ``restricted_c`` on a database where this module bound, else 0 (the
    registry is process-wide; a runtime that never bound this module has no restriction).  A read that fails propagates
    (C9b: a figure that cannot be computed is never guessed as 0)."""
    if not schema_ready(conn):
        return 0
    return max(0, restricted_c(conn, account_id, ccy))


def _register_adjuster(rt) -> bool:
    """Register the adjuster through C9b (``accounting.add_cash_adjuster``; the Accounting object's method as a fallback).
    False when neither exists yet (an older tree): restricted then shows in the parts route only."""
    from . import accounting as _acc
    for fn in (getattr(_acc, "add_cash_adjuster", None), getattr(getattr(rt, "accounting", None), "add_cash_adjuster", None)):
        if callable(fn):
            fn(ADJUSTER_KEY, ADJUSTER_LABEL, restricted_adjuster)
            return True
    return False


# ---- C15: settlement fail state ---------------------------------------------------------------------------------------------
def settlement_state(row) -> dict:
    """``{stage, label, chip}`` of a settlement row in trader words: SETTLING (pending, due D), FAILED (with why, and what
    happens next), SETTLED (on D), RESOLVED (settled after a fail, and how).  ``chip`` is the C4 stage-chip class."""
    status = str(_get(row, "status", "PENDING"))
    fs = _get(row, "fail_state")
    kind = _get(row, "fail_kind")
    due = _get(row, "due_date", "")
    reason = str(_get(row, "fail_reason", "") or "")
    resolution = _get(row, "resolution")

    def day(ts):
        return ny_of(int(ts)).date().isoformat() if ts else ""

    if status == "SETTLED":
        when = day(_get(row, "settled_ts"))
        if fs == "RESOLVED":
            how = {"SETTLED": "settled after the fail", "RETRY": "settled on retry", "CASH_COMPENSATION": "settled with cash compensation",
                   "BUY_IN": "bought in elsewhere"}.get(str(resolution or ""), "resolved")
            return {"stage": "RESOLVED", "label": f"resolved {when}: {how}".strip(), "chip": "filled"}
        return {"stage": "SETTLED", "label": f"settled {when}".strip(), "chip": "filled"}
    if fs == "FAILED":
        if kind == "SIMULATED":
            nxt = "retry requested: it settles at the next settlement run" if resolution == "RETRY" else "resolve it: retry, cash compensation or buy-in"
            return {"stage": "FAILED", "label": f"failed - {reason or SIMULATED_LABEL}; no cash moves until resolved; {nxt}", "chip": "failed"}
        return {"stage": "FAILED", "label": f"failed - {UNFUNDED_LABEL} ({due}); retried automatically until it is funded", "chip": "failed"}
    return {"stage": "SETTLING", "label": f"pending settlement, due {due}", "chip": "settling"}


def _settlement(conn, settle_id: str):
    s = conn.execute("SELECT * FROM settlements WHERE settle_id=?", (settle_id,)).fetchone()
    if s is None:
        raise RejectError("NOT_FOUND", f"settlement {settle_id} does not exist", {"settle_id": settle_id})
    return s


def settlement_view(conn, s) -> dict:
    """The row (API shape) with its state and what it settles (the fill's instrument / side / quantity when it has one)."""
    d = dict(s)
    d.update(settlement_state(s))
    if s["fill_id"]:
        f = conn.execute("SELECT instrument_id, side, qty, price, trade_date FROM fills WHERE fill_id=?", (s["fill_id"],)).fetchone()
        if f is not None:
            d.update(instrument_id=f["instrument_id"], fill_side=f["side"], fill_qty=int(f["qty"]), fill_price=f["price"], trade_date=f["trade_date"])
    return d


def mark_failed(ctx: TxContext, settle_id: str, kind: str, reason: str) -> bool:
    """FAILED (``kind`` UNFUNDED | SIMULATED) once; False when the columns are absent or the row is already FAILED."""
    if not has_fail_columns(ctx.conn):
        return False
    s = _settlement(ctx.conn, settle_id)
    if s["status"] != "PENDING" or s["fail_state"] == "FAILED":
        return False
    ctx.conn.execute("UPDATE settlements SET fail_state='FAILED', fail_kind=?, fail_reason=?, failed_ts=?, resolved_ts=NULL, resolution=NULL WHERE settle_id=?",
                     (kind, reason[:300], ctx.ts, settle_id))
    ctx.event("SETTLEMENT_FAILED", account_id=s["account_id"], ref_type="settlement", ref_id=settle_id, fail_kind=kind, reason=reason[:300],
              due_date=s["due_date"], amount_c=int(s["amount_c"]), currency=s["currency"])
    return True


def mark_resolved(ctx: TxContext, settle_id: str, resolution: Optional[str] = None) -> bool:
    """A FAILED row that has just settled becomes RESOLVED (``resolution`` given, else the one requested, else SETTLED);
    its SETTLEMENT_FAILED attention resolves.  False when the row was never FAILED."""
    if not has_fail_columns(ctx.conn):
        return False
    s = _settlement(ctx.conn, settle_id)
    if s["fail_state"] != "FAILED":
        return False
    res = resolution or s["resolution"] or "SETTLED"
    ctx.conn.execute("UPDATE settlements SET fail_state='RESOLVED', resolved_ts=?, resolution=? WHERE settle_id=?", (ctx.ts, res, settle_id))
    ctx.conn.execute("UPDATE attention SET resolved_ts=? WHERE code='SETTLEMENT_FAILED' AND ref_type='settlement' AND ref_id=? AND resolved_ts IS NULL",
                     (ctx.ts, settle_id))
    ctx.event("SETTLEMENT_RESOLVED", account_id=s["account_id"], ref_type="settlement", ref_id=settle_id, resolution=res, fail_kind=s["fail_kind"],
              failed_ts=s["failed_ts"])
    return True


def _open_account(conn, account_id: str):
    acct = account_row(conn, account_id)
    if acct is None:
        raise RejectError("ACCOUNT_UNKNOWN", f"account {account_id} does not exist")
    lrow = lifetime_row(conn, acct["lifetime_id"])
    if lrow is None or lrow["status"] != "OPEN":
        raise RejectError("LIFETIME_CLOSED", f"lifetime {acct['lifetime_id']} is closed (History is read-only)")
    if acct["status"] != "OPEN":
        raise RejectError("ACCOUNT_CLOSED", f"account {account_id} is closed")
    return acct


def _require_columns(conn) -> None:
    if not has_fail_columns(conn):
        raise ConflictError("NOT_READY", "settlement fail tracking is not set up on this database yet (the collateral module did not bind)")


def h_settlement_fail(ctx: TxContext, payload: dict) -> dict:
    """``settlement.fail {settle_id, reason?}`` - the owner's simulated counterparty fail (Q11; module docstring)."""
    conn = ctx.conn
    _require_columns(conn)
    sid = str(payload.get("settle_id") or "")
    s = _settlement(conn, sid)
    _open_account(conn, s["account_id"])
    if s["status"] != "PENDING":
        raise ConflictError("NOT_PENDING", f"settlement {sid} has already settled; only a pending settlement can fail", {"settle_id": sid})
    if s["fail_state"] == "FAILED":
        raise ConflictError("ALREADY_FAILED", f"settlement {sid} has already failed ({s['fail_reason']})", {"settle_id": sid})
    if s["fx_deal_id"]:
        raise ConflictError("FX_DEAL_LEG", "an FX deal settles both legs together; a simulated fail is offered on trade settlements", {"settle_id": sid})
    typed = " ".join(str(payload.get("reason") or "").split())[:200]
    reason = SIMULATED_LABEL + (f": {typed}" if typed else "")
    mark_failed(ctx, sid, "SIMULATED", reason)
    what = "receive" if int(s["amount_c"]) > 0 else "pay"
    ctx.attention("WARN", "collateral", "SETTLEMENT_FAILED",
                  f"{s['account_id']}: settlement {sid} ({what} {s['currency']} {fmt_money(abs(int(s['amount_c'])))}, due {s['due_date']}) - {reason};"
                  " no cash moves until you resolve it (retry, cash compensation or buy-in)",
                  {"account_id": s["account_id"], "ref_type": "settlement", "ref_id": sid, "auto_resolve": 1, "due_date": s["due_date"]})
    ctx.touched_accounts.add(s["account_id"])
    s = _settlement(conn, sid)
    return {"settlement": settlement_view(conn, s), "state": settlement_state(s)}


def _trade_instrument(conn, s) -> str:
    """The instrument id of the compensation / buy-in lines (4300 / 5700 are per instrument): the fill's instrument, else the
    OTC contract the payment belongs to, else a synthetic ``SETTLE:{id}`` (never a position)."""
    if s["fill_id"]:
        f = conn.execute("SELECT instrument_id FROM fills WHERE fill_id=?", (s["fill_id"],)).fetchone()
        if f is not None and f["instrument_id"]:
            return str(f["instrument_id"])
    ref = _get(s, "ref_id")
    return str(ref) if ref else f"SETTLE:{s['settle_id']}"


def h_settlement_resolve(ctx: TxContext, payload: dict) -> dict:
    """``settlement.resolve {settle_id, action: retry | cash_compensation {amount} | buy_in {cost}}`` (module docstring)."""
    from . import execution, lifecycle_jobs
    conn = ctx.conn
    _require_columns(conn)
    services = _services(ctx)
    sid = str(payload.get("settle_id") or "")
    action = str(payload.get("action") or "").strip().lower()
    if action not in ACTIONS:
        raise RejectError("ACTION", f"action must be one of {', '.join(ACTIONS)}, got {payload.get('action')!r}")
    s = _settlement(conn, sid)
    acct = _open_account(conn, s["account_id"])
    if s["fail_state"] != "FAILED" or s["status"] != "PENDING":
        raise ConflictError("NOT_FAILED", f"settlement {sid} is not a failed pending settlement ({settlement_state(s)['label']})", {"settle_id": sid})
    aid, ccy, amt = s["account_id"], s["currency"], int(s["amount_c"])
    out: dict = {"settle_id": sid, "action": action}
    if action == "retry":
        conn.execute("UPDATE settlements SET resolution='RETRY' WHERE settle_id=?", (sid,))
        ctx.event("SETTLEMENT_RETRY", account_id=aid, ref_type="settlement", ref_id=sid, fail_kind=s["fail_kind"])
        out["note"] = ("retry requested: the settlement run settles it on its due date or at the next run (about 30 s)" if s["fail_kind"] == "SIMULATED"
                       else "it is already retried automatically; it settles as soon as the account or Treasury can fund it")
    else:
        if s["fail_kind"] != "SIMULATED":
            raise ConflictError("NOT_A_COUNTERPARTY_FAIL", f"settlement {sid} failed because it was not funded; it resolves by funding (move cash in), not by "
                                f"{action.replace('_', ' ')}", {"settle_id": sid})
        if s["fx_deal_id"]:
            raise ConflictError("FX_DEAL_LEG", "an FX deal settles both legs together", {"settle_id": sid})
        iid = _trade_instrument(conn, s)
        if action == "buy_in" and amt >= 0:
            raise ConflictError("BUY_IN_NOT_APPLICABLE", f"settlement {sid} is a receipt: a buy-in replaces securities a seller failed to deliver on a purchase",
                                {"settle_id": sid})
        extra_c = _amount(payload, "amount" if action == "cash_compensation" else "cost")
        settled = lifecycle_jobs.post_settlement(ctx, s, services)
        out["settled"] = settled
        if action == "cash_compensation":
            memo = (f"cash compensation for failed settlement {sid} ({SIMULATED_LABEL}): {ccy} {fmt_money(extra_c)} received from the counterparty"
                    f" for the late settlement")
            entry_id = ctx.post(Entry("SETTLE_FAIL", memo, [dr(aid, "1010", ccy, extra_c, memo=f"compensation for {sid}"),
                                                             cr(aid, "4300", ccy, extra_c, instrument_id=iid, memo=f"late-settlement compensation {sid}")],
                                      f"settlefail:comp:{sid}", ref_type="settlement", ref_id=sid))
            resolution = "CASH_COMPENSATION"
        else:
            funding = execution.fund(ctx, aid, ccy, extra_c, "settlement", sid, None, immediate=True, services=services)
            memo = (f"buy-in cost of failed settlement {sid} ({SIMULATED_LABEL}): {ccy} {fmt_money(extra_c)} paid above the original price to buy the"
                    f" securities in elsewhere")
            entry_id = ctx.post(Entry("SETTLE_FAIL", memo, [dr(aid, "5700", ccy, extra_c, instrument_id=iid, memo=f"buy-in cost {sid}"),
                                                             cr(aid, "1010", ccy, extra_c, memo=f"buy-in {sid}")],
                                      f"settlefail:buyin:{sid}", ref_type="settlement", ref_id=sid))
            out["funding"] = funding.to_dict()
            resolution = "BUY_IN"
        mark_resolved(ctx, sid, resolution)
        ctx.touched_accounts.add(aid)
        if iid and not iid.startswith("SETTLE:"):
            ctx.touched_instruments.add((aid, iid))
        out.update(entry_id=entry_id, amount_c=extra_c, resolution=resolution)
    s = _settlement(conn, sid)
    out.update(settlement=settlement_view(conn, s), state=settlement_state(s), account_kind=acct["kind"])
    return out


# ---- C15: the import's groups -> journal entries -----------------------------------------------------------------------------
def _ids(v) -> List[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple, set)):
        return [str(x) for x in v if x is not None and str(x) != ""]
    return [str(v)]


def _in(col: str, vals: List) -> Tuple[str, list]:
    return f"{col} IN ({','.join('?' for _ in vals)})", list(vals)


def group_entry_ids(conn, groups: Iterable) -> Set[int]:
    """The journal entries the import's ``groups`` (``[{key, label, refs: {contract_ids, order_ids, fill_ids, settle_ids,
    transfer_ids, entry_ids}}]``, the C9d shape) created: the listed entries; every entry of the listed fills (and of the
    orders' fills), settlements (and the fills' settlements, and the contracts' payments) and contracts (any entry whose
    reference is the contract); the collateral entries of those fills; the listed transfers and every sweep linked to one
    of those fills, settlements or contracts."""
    entry_ids: Set[int] = set()
    fills: Set[str] = set()
    settles: Set[str] = set()
    contracts: Set[str] = set()
    transfers: Set[str] = set()
    orders: Set[str] = set()
    for g in groups or []:
        refs = (g or {}).get("refs") if isinstance(g, dict) else None
        refs = refs if isinstance(refs, dict) else (g if isinstance(g, dict) else {})
        for x in _ids(refs.get("entry_ids")):
            try:
                entry_ids.add(int(x))
            except ValueError:
                continue
        fills.update(_ids(refs.get("fill_ids")))
        settles.update(_ids(refs.get("settle_ids")))
        contracts.update(_ids(refs.get("contract_ids")))
        transfers.update(_ids(refs.get("transfer_ids")))
        orders.update(_ids(refs.get("order_ids")))
    if orders:
        w, p = _in("order_id", sorted(orders))
        fills.update(r[0] for r in conn.execute(f"SELECT fill_id FROM fills WHERE {w}", p).fetchall())
    if fills:
        w, p = _in("fill_id", sorted(fills))
        settles.update(r[0] for r in conn.execute(f"SELECT settle_id FROM settlements WHERE {w}", p).fetchall())
        entry_ids.update(int(r[0]) for r in conn.execute(f"SELECT entry_id FROM fills WHERE {w}", p).fetchall() if r[0] is not None)
        w2, p2 = _in("ref_id", sorted(fills))
        entry_ids.update(int(r[0]) for r in conn.execute(f"SELECT entry_id FROM journal_entries WHERE ref_type='fill' AND {w2}", p2).fetchall())
        for fid in sorted(fills):
            entry_ids.update(int(r[0]) for r in conn.execute("SELECT entry_id FROM journal_entries WHERE idem_key LIKE ?", (f"%:fill:{fid}",)).fetchall())
    if contracts:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(settlements)").fetchall()}
        if "ref_id" in cols:
            w, p = _in("ref_id", sorted(contracts))
            settles.update(r[0] for r in conn.execute(f"SELECT settle_id FROM settlements WHERE {w}", p).fetchall())
        w, p = _in("ref_id", sorted(contracts))
        entry_ids.update(int(r[0]) for r in conn.execute(f"SELECT entry_id FROM journal_entries WHERE {w}", p).fetchall())
    if settles:
        w, p = _in("ref_id", sorted(settles))
        entry_ids.update(int(r[0]) for r in conn.execute(f"SELECT entry_id FROM journal_entries WHERE ref_type='settlement' AND {w}", p).fetchall())
    trows = []
    if transfers:
        w, p = _in("transfer_id", sorted(transfers))
        trows += conn.execute(f"SELECT entry_id FROM transfers WHERE {w}", p).fetchall()
    for lt, vals in (("fill", fills), ("settlement", settles)):
        if vals:
            w, p = _in("linked_id", sorted(vals))
            trows += conn.execute(f"SELECT entry_id FROM transfers WHERE linked_type=? AND {w}", [lt] + p).fetchall()
    for cid in sorted(contracts):
        trows += conn.execute("SELECT entry_id FROM transfers WHERE linked_id=? OR linked_id LIKE ? OR linked_id LIKE ? OR linked_id LIKE ?",
                              (cid, f"%:{cid}", f"%:{cid}:%", f"% ({cid} %")).fetchall()
    entry_ids.update(int(r[0]) for r in trows if r[0] is not None)
    return entry_ids


def _is_funding_entry(conn, entry_id: int) -> bool:
    r = conn.execute("SELECT kind, idem_key FROM journal_entries WHERE entry_id=?", (entry_id,)).fetchone()
    return r is not None and (str(r["idem_key"]).startswith("asofsweep:") or str(r["idem_key"]).startswith("asofxfer:") or r["kind"] == "ASOF_FUNDING")


def _import_cash_c(conn, entries: Set[int], account_id: str, ccy: str, d: str) -> int:
    """Natural 1010 of ``entries`` in (account, ccy) with business_date <= d (the import's cash in that account by then)."""
    if not entries:
        return 0
    total = 0
    ids = sorted(entries)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        w, p = _in("l.entry_id", chunk)
        row = conn.execute(f"SELECT COALESCE(SUM(l.amount_c), 0) FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id WHERE {w}"
                           " AND l.account_id=? AND l.gl='1010' AND l.currency=? AND e.business_date<=?", p + [account_id, ccy, d]).fetchone()
        total += int(row[0] or 0)
    return natural("1010", total)


def _dates(a: date, b: date) -> List[date]:
    out, d = [], a
    while d <= b:
        out.append(d)
        d += timedelta(days=1)
    return out


def _with_asof(ctx: TxContext, d: Optional[str], fn):
    """Run ``fn()`` with ``ctx.asof_date`` = d (restored afterwards)."""
    had = hasattr(ctx, "asof_date")
    prev = getattr(ctx, "asof_date", None)
    ctx.asof_date = d
    try:
        return fn()
    finally:
        if had:
            ctx.asof_date = prev
        else:
            try:
                del ctx.asof_date
            except AttributeError:
                pass


def _lifetime_accounts(conn, lifetime_id: str, ids) -> List:
    out = []
    for aid in _ids(ids):
        a = account_row(conn, aid)
        if a is None:
            raise RejectError("ACCOUNT_UNKNOWN", f"account {aid} does not exist")
        if a["lifetime_id"] != lifetime_id:
            raise RejectError("CROSS_BOOK", f"{aid} is not an account of {lifetime_id}")
        out.append(a)
    if not out:
        raise RejectError("ACCOUNTS_REQUIRED", "accounts is required (the accounts to work on)")
    return out


def h_asof_sweep(ctx: TxContext, payload: dict) -> dict:
    """``cash.asof_sweep`` (C15; module docstring AS-OF FUNDING SUPPORT)."""
    from . import treasury
    _require_import(ctx, "cash.asof_sweep")
    conn = ctx.conn
    lid = str(payload.get("lifetime_id") or "")
    if lifetime_row(conn, lid) is None:
        raise RejectError("LIFETIME_UNKNOWN", f"lifetime {lid!r} does not exist")
    batch = str(payload.get("batch_id") or "").strip()
    if not batch:
        raise RejectError("BATCH_REQUIRED", "batch_id is required")
    d0, d1 = _date(payload.get("from_date")), _date(payload.get("through_date"))
    if d1 < d0:
        raise RejectError("DATE_RANGE", f"through_date {d1} is before from_date {d0}")
    accts = _lifetime_accounts(conn, lid, payload.get("accounts"))
    tre = f"{lid}:TREASURY"
    entries = {e for e in group_entry_ids(conn, payload.get("groups") or []) if not _is_funding_entry(conn, e)}
    posted: List[dict] = []
    skipped: List[dict] = []
    for d in _dates(d0, d1):
        ds = d.isoformat()
        for a in accts:
            aid = a["account_id"]
            if a["kind"] == "TREASURY" or aid == tre:
                continue
            if not int(a["sweep_out"] or 0):
                skipped.append({"account_id": aid, "date": ds, "note": "sweep-out is switched off for this account: real time would not have swept it"})
                continue
            for ccy in SWEEP_CCYS:
                idem = f"asofsweep:{batch}:{aid}:{ccy}:{ds}"
                if conn.execute("SELECT 1 FROM journal_entries WHERE idem_key=?", (idem,)).fetchone() is not None:
                    skipped.append({"account_id": aid, "date": ds, "currency": ccy, "note": "already swept (rerun)"})
                    continue
                settled = bd_balance_c(conn, aid, "1010", ccy, ds)
                restricted = restricted_c(conn, aid, ccy, as_of=ds)
                payables = bd_balance_c(conn, aid, "2100", ccy, ds) + bd_balance_c(conn, aid, "2350", ccy, ds)
                buffer_c = int(a["sweep_buffer_c"] or 0)
                excess = settled - restricted - payables - buffer_c
                earlier = int(conn.execute("SELECT COALESCE(SUM(amount_c), 0) FROM transfers WHERE from_account=? AND currency=? AND idem_key LIKE ?"
                                           " AND business_date < ?", (aid, ccy, f"asofsweep:{batch}:{aid}:{ccy}:%", ds)).fetchone()[0] or 0)
                imp = _import_cash_c(conn, entries, aid, ccy, ds) - earlier
                sweep = min(excess, imp)
                figures = {"settled_c": settled, "restricted_c": restricted, "payables_c": payables, "buffer_c": buffer_c, "excess_c": excess,
                           "import_cash_c": imp, "earlier_asof_sweeps_c": earlier, "sweep_c": max(0, sweep)}
                if sweep <= 0:
                    why = "no idle cash at the close" if excess <= 0 else "no cash of the import's own left to sweep (pre-import cash is never swept)"
                    skipped.append(dict(figures, account_id=aid, date=ds, currency=ccy, note=why))
                    continue
                now_settled = balance(conn, aid, "1010", ccy)
                if now_settled - sweep < 0:
                    raise ConflictError("ASOF_CURRENT_FUNDS", f"the as-of sweep of {ccy} {fmt_money(sweep)} from {aid} dated {ds} would leave its settled cash today at"
                                        f" {fmt_money(now_settled - sweep)}; nothing was written", dict(figures, account_id=aid, date=ds, current_settled_c=now_settled))
                memo = f"as-of sweep of the import's idle {ccy} cash on {ds} (batch {batch})"
                text = (f"as-of sweep {ccy} {fmt_money(sweep)} {aid} -> {tre} dated {ds} (batch {batch}): settled {fmt_money(settled)} - restricted"
                        f" {fmt_money(restricted)} - payables {fmt_money(payables)} - buffer {fmt_money(buffer_c)} = {fmt_money(excess)}; the import's cash"
                        f" {fmt_money(imp)}")
                res = _with_asof(ctx, ds, lambda: treasury._post_transfer(ctx, "SWEEP_OUT", aid, tre, ccy, sweep, memo, linked_type="asof_sweep",
                                                                           linked_id=batch, event="SWEEP_OUT", idem_key=idem, entry_memo=text))
                posted.append(dict(figures, account_id=aid, date=ds, currency=ccy, transfer_id=res["transfer_id"], entry_id=res["entry_id"]))
    ctx.event("ASOF_SWEEP", ref_type="batch", ref_id=batch, lifetime_id=lid, from_date=d0.isoformat(), through_date=d1.isoformat(),
              posted=[{k: p[k] for k in ("account_id", "date", "currency", "sweep_c", "transfer_id")} for p in posted])
    return {"batch_id": batch, "lifetime_id": lid, "posted": posted, "skipped": skipped, "total_c": sum(p["sweep_c"] for p in posted)}


def h_asof_transfer(ctx: TxContext, payload: dict) -> dict:
    """``cash.asof_transfer`` (C15; module docstring AS-OF FUNDING SUPPORT)."""
    from . import treasury
    _require_import(ctx, "cash.asof_transfer")
    conn = ctx.conn
    src, dst = str(payload.get("from_account") or ""), str(payload.get("to_account") or "")
    if not src or not dst:
        raise RejectError("ACCOUNT_REQUIRED", "from_account and to_account are required")
    if src == dst:
        raise RejectError("SAME_ACCOUNT", "from_account and to_account are the same")
    sa, da = _open_account(conn, src), _open_account(conn, dst)
    if sa["lifetime_id"] != da["lifetime_id"]:
        raise RejectError("CROSS_BOOK", f"books never transfer to each other: {src} and {dst} belong to different lifetimes")
    batch = str(payload.get("batch_id") or "").strip()
    line_key = str(payload.get("line_key") or "").strip()
    reason = " ".join(str(payload.get("reason") or "").split())[:200]
    if not batch or not line_key:
        raise RejectError("BATCH_REQUIRED", "batch_id and line_key are required")
    if not reason:
        raise RejectError("REASON_REQUIRED", "reason is required (why the cash moves on that date)")
    bd = _iso(payload.get("business_date"))
    ccy = str(payload.get("currency") or "USD").upper()
    amount = _amount(payload, "amount")
    idem = f"asofxfer:{batch}:{line_key}:{src}:{dst}:{ccy}:{bd}"
    old = conn.execute("SELECT * FROM transfers WHERE idem_key=?", (idem,)).fetchone()
    if old is not None:
        return {"skipped": True, "note": "already transferred (rerun)", "transfer": dict(old)}
    at_date = settled_at_close_c(conn, src, ccy, bd)
    if at_date < amount:
        raise ConflictError("INSUFFICIENT_FUNDS", f"{src} held {ccy} {fmt_money(at_date)} at the close of {bd}; the as-of transfer needs {fmt_money(amount)}",
                            {"account_id": src, "settled_at_date_c": at_date, "amount_c": amount, "business_date": bd})
    now_settled = balance(conn, src, "1010", ccy)
    if now_settled < amount:
        raise ConflictError("ASOF_CURRENT_FUNDS", f"{src} holds {ccy} {fmt_money(now_settled)} today; the as-of transfer needs {fmt_money(amount)}",
                            {"account_id": src, "current_settled_c": now_settled, "amount_c": amount, "business_date": bd})
    memo = f"as-of funding: {reason} (batch {batch}, line {line_key})"
    res = _with_asof(ctx, bd, lambda: treasury._post_transfer(ctx, "TRANSFER", src, dst, ccy, amount, memo, linked_type="asof_funding", linked_id=line_key,
                                                              event="TRANSFER", idem_key=idem, entry_kind="ASOF_FUNDING", entry_memo="as-of funding"))
    ctx.event("ASOF_FUNDING", account_id=dst, ref_type="transfer", ref_id=res["transfer_id"], batch_id=batch, line_key=line_key, reason=reason,
              from_account=src, to_account=dst, amount_c=amount, currency=ccy, business_date=bd)
    return dict(res, batch_id=batch, line_key=line_key, reason=reason, skipped=False)


# ---- C17: cash.reaccrue -----------------------------------------------------------------------------------------------------
def _accrued_events(conn, account_id: str, kind: str) -> Dict[str, dict]:
    """night -> the first ACCRUED event payload of (account, kind) for that night (its rate, base and amount)."""
    out: Dict[str, dict] = {}
    for r in conn.execute("SELECT payload_json FROM events WHERE type='ACCRUED' AND account_id=? ORDER BY id", (account_id,)).fetchall():
        try:
            p = json.loads(r["payload_json"] or "{}")
        except ValueError:
            continue
        if p.get("kind") != kind or not p.get("night"):
            continue
        out.setdefault(str(p["night"]), p)
    return out


def _accrual_idem(kind: str, account_id: str, night: str) -> str:
    if kind == "cashint":
        return f"accr:cashint:{split_account_id(account_id)[0]}:USD:{night}"
    return f"accr:{kind}:{account_id}:{night}"


def _posted_for_night(conn, kind: str, account_id: str, night: str) -> Tuple[int, Optional[str]]:
    """(natural cents posted for the night: the original accrual + every earlier correction, the 4300 instrument id it used)."""
    gl = "5100" if kind == "pb" else "1230"
    total, inst = 0, None
    rows = conn.execute("SELECT e.entry_id FROM journal_entries e WHERE e.idem_key=? OR e.idem_key LIKE ?",
                        (_accrual_idem(kind, account_id, night), f"reaccrue:%:{account_id}:{kind}:{night}")).fetchall()
    for r in rows:
        for ln in conn.execute("SELECT gl, amount_c, instrument_id FROM journal_lines WHERE entry_id=? AND account_id=?", (r["entry_id"], account_id)).fetchall():
            if ln["gl"] == gl:
                total += natural(gl, int(ln["amount_c"]))
            if ln["gl"] == "4300" and inst is None:
                inst = ln["instrument_id"]
    return total, inst


def _restated_base(conn, kind: str, account_id: str, night: str) -> int:
    if kind == "cashint":
        return max(0, settled_at_close_c(conn, account_id, "USD", night))
    if kind == "pbcredit":
        return max(0, settled_at_close_c(conn, account_id, "USD", night) - restricted_c(conn, account_id, "USD", as_of=night))
    return max(0, bd_balance_c(conn, account_id, "2700", "USD", night))


def restate_accruals(conn, kind: str, account_id: str, through: str) -> List[dict]:
    """The restated sequence of (account, kind) from its first recorded night through ``through``: per night its business-date
    base, the rate of its original accrual, the segment start / n / cumulative and the restated amount (``accrue_segment``'s
    rule).  A night without an ACCRUED event (its rate unknown) is left out and breaks the segment."""
    events = _accrued_events(conn, account_id, kind)
    nights = sorted(n for n in events if n <= through)
    out: List[dict] = []
    prev = None                       # (night, base, rate, start, n, cum)
    for n in nights:
        p = events[n]
        rate = qprice(str(p.get("rate") or "0"))
        base = _restated_base(conn, kind, account_id, n)
        nd = date.fromisoformat(n)
        if prev is not None and prev[1] == base and str(prev[2]) == str(rate) and date.fromisoformat(prev[0]) + timedelta(days=1) == nd:
            start, k, cum_prev = prev[3], prev[4] + 1, prev[5]
        else:
            start, k, cum_prev = n, 1, 0
        cum = to_c(from_c(base) * rate * k / ACCRUAL_BASIS)
        out.append({"night": n, "base_c": base, "rate": str(rate), "segment_start": start, "n": k, "cum_c": cum, "amount_c": cum - cum_prev,
                    "original_base_c": p.get("base_c"), "original_amount_c": p.get("amount_c")})
        prev = (n, base, rate, start, k, cum)
    return out


def _accrual_ref(kind: str, account_id: str) -> str:
    return account_id


def h_reaccrue(ctx: TxContext, payload: dict) -> dict:
    """``cash.reaccrue`` (C17; module docstring AS-OF FUNDING SUPPORT)."""
    from .lifecycle_jobs import CASH_INSTRUMENT
    _require_import(ctx, "cash.reaccrue")
    conn = ctx.conn
    services = getattr(ctx, "services", None)
    lid = str(payload.get("lifetime_id") or "")
    if lifetime_row(conn, lid) is None:
        raise RejectError("LIFETIME_UNKNOWN", f"lifetime {lid!r} does not exist")
    batch = str(payload.get("batch_id") or "").strip()
    if not batch:
        raise RejectError("BATCH_REQUIRED", "batch_id is required")
    kinds = [str(k) for k in (payload.get("kinds") or list(REACCRUE_KINDS))]
    bad = [k for k in kinds if k not in REACCRUE_KINDS]
    if bad:
        raise RejectError("KIND", f"kinds must be among {', '.join(REACCRUE_KINDS)}, got {', '.join(bad)}")
    d_from = _iso(payload["from_night"]) if payload.get("from_night") else None
    d_thru_in = _iso(payload["through_night"]) if payload.get("through_night") else None
    reason = " ".join(str(payload.get("reason") or "").split())[:200] or "as-of import"
    accts = _lifetime_accounts(conn, lid, payload.get("accounts"))
    corrections: List[dict] = []
    skipped: List[dict] = []
    cursors: List[dict] = []
    for a in accts:
        aid = a["account_id"]
        for kind in kinds:
            if (kind == "cashint") != (a["kind"] == "TREASURY") or (kind in ("pbcredit", "pb") and a["kind"] != "PB"):
                continue
            st = conn.execute("SELECT * FROM accrual_state WHERE kind=? AND ref_id=?", (kind, _accrual_ref(kind, aid))).fetchone()
            accrued_through = st["accrued_through"] if st is not None else None
            through = d_thru_in or accrued_through
            if not through:
                skipped.append({"account_id": aid, "kind": kind, "note": "nothing accrued yet"})
                continue
            seq = restate_accruals(conn, kind, aid, through)
            for r in seq:
                n = r["night"]
                if d_from and n < d_from:
                    continue
                idem = f"reaccrue:{batch}:{aid}:{kind}:{n}"
                if conn.execute("SELECT 1 FROM journal_entries WHERE idem_key=?", (idem,)).fetchone() is not None:
                    skipped.append({"account_id": aid, "kind": kind, "night": n, "note": "already corrected by this batch (rerun)"})
                    continue
                posted, inst = _posted_for_night(conn, kind, aid, n)
                corr = int(r["amount_c"]) - posted
                if corr == 0:
                    continue
                bd = (date.fromisoformat(n) + timedelta(days=1)).isoformat()
                pct = f"{(Decimal(r['rate']) * 100).quantize(Decimal('0.0001')).normalize():f} %"
                memo = (f"re-accrual ({reason}, batch {batch}) {kind} {aid} night of {n}: base {fmt_money(int(r['original_base_c'] or 0))} ->"
                        f" {fmt_money(r['base_c'])} (business-date balance) at {pct} ACT/360; posted {fmt_money(posted)}, restated {fmt_money(r['amount_c'])}:"
                        f" correction {'+' if corr > 0 else ''}{fmt_money(corr)}")
                if kind == "pb":
                    lines = [dr(aid, "5100", "USD", corr, memo=f"re-accrual night of {n}"), cr(aid, "2300", "USD", corr, memo=f"re-accrual night of {n}")]
                else:
                    if inst is None:
                        inst = CASH_INSTRUMENT if kind == "cashint" or getattr(services, "prime", None) is None else "USD"
                    lines = [dr(aid, "1230", "USD", corr, memo=f"re-accrual night of {n}"),
                             cr(aid, "4300", "USD", corr, instrument_id=inst, memo=f"re-accrual night of {n}")]
                entry_id = ctx.post(Entry("ACCRUAL", memo, lines, idem, ref_type="account", ref_id=aid, business_date=bd))
                ctx.event("REACCRUED", account_id=aid, ref_type="account", ref_id=aid, kind=kind, night=n, business_date=bd, batch_id=batch, posted_c=posted,
                          restated_c=int(r["amount_c"]), correction_c=corr, base_c=r["base_c"], rate=r["rate"], entry_id=entry_id)
                corrections.append({"account_id": aid, "kind": kind, "night": n, "business_date": bd, "posted_c": posted, "restated_c": int(r["amount_c"]),
                                    "correction_c": corr, "base_c": r["base_c"], "original_base_c": r["original_base_c"], "rate": r["rate"], "entry_id": entry_id})
            # the accrual cursor follows the restated segment, so the next live night continues it exactly
            if st is not None and seq and seq[-1]["night"] == accrued_through:
                last = seq[-1]
                if (int(st["base_c"]), str(st["rate"]), st["segment_start"], int(st["cum_c"])) != (int(last["base_c"]), last["rate"], last["segment_start"], int(last["cum_c"])):
                    conn.execute("UPDATE accrual_state SET segment_start=?, base_c=?, rate=?, day_basis=?, cum_c=? WHERE kind=? AND ref_id=?",
                                 (last["segment_start"], int(last["base_c"]), last["rate"], ACCRUAL_BASIS, int(last["cum_c"]), kind, _accrual_ref(kind, aid)))
                    cursors.append({"account_id": aid, "kind": kind, "segment_start": last["segment_start"], "base_c": last["base_c"], "rate": last["rate"],
                                    "cum_c": last["cum_c"], "was": {"segment_start": st["segment_start"], "base_c": int(st["base_c"]), "rate": str(st["rate"]),
                                                                    "cum_c": int(st["cum_c"])}})
    ctx.event("REACCRUE", ref_type="batch", ref_id=batch, lifetime_id=lid, kinds=kinds, corrections=len(corrections), cursors=cursors)
    return {"batch_id": batch, "corrections": corrections, "skipped": skipped, "cursors": cursors, "total_c": sum(c["correction_c"] for c in corrections)}


# ---- the CSA reuse flag (Q7; the editor ships in 1.0.6) --------------------------------------------------------------------
def h_csa_terms(ctx: TxContext, payload: dict) -> dict:
    """``collateral.csa_terms {account_id, reuse_allowed}`` (light): the per-CSA "reuse allowed" flag, default off."""
    conn = ctx.conn
    aid = str(payload.get("account_id") or "")
    acct = _open_account(conn, aid)
    if acct["kind"] != "PB":
        raise RejectError("NOT_PB", f"{aid} is not a prime-broker account: a CSA belongs to a prime-broker relationship")
    if "reuse_allowed" not in payload or payload.get("reuse_allowed") is None:
        raise RejectError("NOTHING_TO_UPDATE", "reuse_allowed is required (true or false)")
    want = 1 if payload.get("reuse_allowed") in (True, 1, "1", "true", "on", "yes") else 0
    if not schema_ready(conn):
        raise ConflictError("NOT_READY", "the CSA terms are not set up on this database yet (the collateral module did not bind)")
    before = 1 if reuse_allowed(conn, aid) else 0
    conn.execute("INSERT INTO csa_terms(account_id, reuse_allowed, updated_ts, command_id) VALUES (?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET"
                 " reuse_allowed=excluded.reuse_allowed, updated_ts=excluded.updated_ts, command_id=excluded.command_id", (aid, want, ctx.ts, ctx.command_id))
    ctx.event("SETTINGS_CHANGED", account_id=aid, ref_type="csa", ref_id=aid, before={"reuse_allowed": bool(before)}, after={"reuse_allowed": bool(want)})
    return {"account_id": aid, "reuse_allowed": bool(want), "before": bool(before)}


# ---- module wiring ------------------------------------------------------------------------------------------------------------
def ensure_schema(db) -> dict:
    return db.ensure_schema_ext("collateral", SCHEMA)


def register(bus, services=None) -> None:
    bus.register("settlement.fail", h_settlement_fail)
    bus.register("settlement.resolve", h_settlement_resolve)
    bus.register("cash.asof_sweep", h_asof_sweep)
    bus.register("cash.asof_transfer", h_asof_transfer)
    bus.register("cash.reaccrue", h_reaccrue)
    bus.register("collateral.csa_terms", h_csa_terms, light=True)


def bind(rt) -> None:
    """The C15 columns and the CSA terms table (additive, idempotent), then the C9b adjuster."""
    db = getattr(rt, "db", None)
    if db is not None and callable(getattr(db, "ensure_schema_ext", None)):
        ensure_schema(db)
    rt.collateral_adjuster = _register_adjuster(rt)


def _reader(rt):
    from .marks import shared_reader
    return shared_reader(rt.db)


def register_routes(r) -> None:
    """GET /api/settlements/{id}; POST /api/settlements/{id}/fail {reason}; POST /api/settlements/{id}/resolve {action, amount | cost};
    GET /api/collateral/restricted?account_id=&currency= (the parts of the restricted figure)."""
    from .api import ApiError, NotFound, run_command

    def settlement_get(req):
        with _reader(req.rt) as conn:
            if not has_fail_columns(conn):
                raise ApiError(409, "NOT_READY", "settlement fail tracking is not set up on this database yet")
            s = conn.execute("SELECT * FROM settlements WHERE settle_id=?", (req.params[0],)).fetchone()
            if s is None:
                raise NotFound(f"settlement {req.params[0]} does not exist")
            return settlement_view(conn, s)

    def settlement_fail(req):
        return run_command(req, "settlement.fail", {"settle_id": req.params[0], "reason": req.b("reason")})

    def settlement_resolve(req):
        payload = {"settle_id": req.params[0], "action": req.b("action")}
        for k in ("amount", "amount_c", "cost", "cost_c"):
            if req.b(k) not in (None, ""):
                payload[k] = req.b(k)
        return run_command(req, "settlement.resolve", payload)

    def restricted_get(req):
        aid = req.q("account_id") or ""
        ccy = (req.q("currency") or "USD").upper()
        with _reader(req.rt) as conn:
            if account_row(conn, aid) is None:
                raise NotFound(f"account {aid} does not exist")
            return restricted_parts(conn, aid, ccy)

    r.add("GET", r"/api/settlements/([^/]+)", settlement_get)
    r.add("POST", r"/api/settlements/([^/]+)/fail", settlement_fail)
    r.add("POST", r"/api/settlements/([^/]+)/resolve", settlement_resolve)
    r.add("GET", r"/api/collateral/restricted", restricted_get)


__all__ = ["SETTLEMENT_COLUMNS", "CSA_TERMS_TABLE", "SCHEMA", "ADJUSTER_KEY", "ADJUSTER_LABEL", "RESTRICTED_TEXT", "ForbiddenError", "has_fail_columns",
           "schema_ready", "bd_balance_c", "gl_c", "settled_at_close_c", "reuse_allowed", "restricted_parts", "restricted_c", "restricted_adjuster",
           "settlement_state", "settlement_view", "mark_failed", "mark_resolved", "group_entry_ids", "restate_accruals", "h_settlement_fail",
           "h_settlement_resolve", "h_asof_sweep", "h_asof_transfer", "h_reaccrue", "h_csa_terms", "ensure_schema", "register", "bind", "register_routes"]
