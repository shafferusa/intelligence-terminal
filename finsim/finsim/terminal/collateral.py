"""Cash, settlement and collateral (FINSIM1_PLAN P6; Part A = release 1.0.5: contracts C15, C17, C21 and C16's lender wording,
which lives in ``lending.lender_label``; Part B = release 1.0.6: C16's ``summary`` / ``position_requirements`` / ``custodian_of`` /
``margin_calls_rows`` / ``pledges_view``, account-level Reg T margin, margin calls, CSA securities pledges, Treasury bills, custody and
the Accounts / Cash & Collateral pages' reads).

Optional module ``collateral`` (``server.OPTIONAL_MODULES``).  ``bind(rt)`` adds the settlement fail columns, the CSA terms table and
(1.0.6) the margin terms, margin calls, CSA pledges and state tables (``Database.ensure_schema_ext``, additive only, D2), registers
the two C9b cash adjusters (restricted collateral received; the Reg T margin requirement) and adds the MARGIN_CALL lifecycle job (C13,
in front of SWEEP_OUT); ``register(bus)`` adds the commands; ``register_routes(router)`` the routes.  The MARGIN_CALL job adds a due
key only from 17:20 ET of a business day (and for open calls), so an import's catch-up cutoff, taken before 16:00 ET, does not move
with its ticks.

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
- PART B (1.0.6) - REG T (task 4, PORTFOLIO-11, Q6; account level, assumed terms editable per account with the light command
  ``margin.terms {account_id, initial_pct, maintenance_pct, long_loan_value_pct}``, fractions, kept in ``margin_terms``): for US
  Exchange and every prime-broker account, requirement = 50 % initial of the physical short market value (the short sale's proceeds and
  the lender's 102 % cash collateral cover the short value itself; maintenance 30 % is shown beside it), eligible collateral = fully
  paid USD equity / ETF longs at a 50 % loan value (shares out on loan and PB financing drawn left out) + free cash at 100 % (settled +
  receivables - payables - restricted collateral - the short-option margin reservation).  Positions are priced at the view's mark, else
  the last recorded close, else (a short) the lender's last collateral mark, else cost - each labelled.  The C9b adjuster "margin
  requirement (Reg T)" reserves the account's cash the requirement needs beyond the longs' loan value, ``min(free cash, max(0,
  requirement - loan value))``, so it is never spent or swept (SWEEP_OUT keeps every adjuster); nothing moves in cash.  A requirement
  beyond the eligible collateral is a DEFICIT and a margin call.  Worked (tests c, c'): shorts 1,000,000.00, longs 3,000,000.00, cash
  100,000.00 -> requirement 500,000.00, eligible 1,500,000.00 + 100,000.00 = 1,600,000.00, nothing reserved; with longs 600,000.00 ->
  eligible 300,000.00 + 100,000.00 = 400,000.00, deficit 100,000.00, reserved min(100,000.00, 500,000.00 - 300,000.00) = 100,000.00 and a
  call of 100,000.00.  (Reserving only the deficit, the plan's first wording, gives the same figures in both cases but would let the 17:30
  sweep move a met call's cash straight back to Treasury, re-opening the deficit every day; the cash the requirement needs is reserved
  instead.)
- MARGIN CALLS (task 5, PORTFOLIO-12): the MARGIN_CALL job runs from 17:20 ET on NYSE business days (after the day's VM at 17:05, the
  collateral marks and the option margin recompute at 17:10), key ``{account}:{date}`` per US Exchange / prime-broker account.  Per
  business day D (window: after the previous business day through D, by business date) it records: futures variation margin paid (net
  per account, CME Clearing via the FCM); OTC variation margin delivered under the CSA (net per prime-broker account, all contracts);
  an initial-margin shortfall under the CSA (the open contracts' IM above the 1330 balance); stock-borrow collateral calls (net per
  lender); a Reg T deficit.  Each call: counterparty, amount, due time = the next business day 10:00 ET.  The job itself never moves
  cash.  The VM-type calls were paid by their own jobs (own cash -> a linked Treasury sweep -> PB financing -> an overdraft under
  FUNDING_SHORTFALL): MET at once with the linked sweeps, or OPEN while an overdraft is open on one of them.  A Reg T deficit is OPEN:
  the account's own cash the requirement needs is already reserved, and the owner meets the rest (a transfer from Treasury, a deposit,
  a smaller short).  An OPEN call carries one ERROR attention MARGIN_CALL; its re-check key ``{account}:call:{call_id}`` marks it MET
  once cured (linking the cash moved into the account since the call), or UNMET once its due time passed (the attention stays until
  the condition is cured: key ``{account}:cure:{call_id}``).  Positions are never sold for you (C6).  START FORWARD (15.9 item 8): ``bind`` records once per database
  when this release first bound (``collateral_state``); no call is ever issued for a business day whose 17:20 ET came before it, so the
  owner's imported history (10-21 .. 10-27) and every day before the 1.0.6 install get no call.  Idem ``mcall:{account}:{date}:{kind}...``.
- CSA PLEDGES AND TREASURY BILLS (task 6, PORTFOLIO-09): ``collateral.pledge {account_id, instrument_id, face, pledged_to, contract_id?}``
  pledges SETTLED face of a US Treasury bill / note / bond held in a book account to a prime broker under its CSA; eligibility and
  haircuts are that broker's schedule (``prime`` FIELD_SPECS ``csa_securities`` / ``csa_bill_haircut`` / ``csa_bond_haircut``, assumed,
  editable; GS seed: Treasuries, bills 2 %, notes / bonds 4 %); ``collateral.release {pledge_id}`` frees it.  Pledges move no GL balance;
  ``repo.pledged_face`` adds them (a pledged face cannot be sold or pledged in a repo, C27).  Eligible value = face x (clean + accrued) /
  100 x (1 - haircut) at the bond mark.  Worked (test d): 1,000,000 face of a bill at 99.00 with a 2 % haircut -> market value
  990,000.00, eligible 970,200.00.  ``treasury_securities`` lists the bills of every book account as cash equivalents (market value,
  maturity, the purchase discount accreted straight-line to today: a display figure).  Releasing cash IM against pledged securities is
  not in this version (section 9.10).
- C16 READS: ``summary(services, scope)`` (margin required vs posted / eligible per component - futures IM at the FCM, the short-option
  reservation covered by own cash, OTC IM under the CSA, Reg T; collateral posted / received; restricted; encumbered = CSA- and
  repo-pledged Treasuries and shares lent, at market; open calls; failed settlements; by counterparty: per CSA (IM vs VM, cash vs
  securities, net VM), the FCM, the stock lenders and the securities-lending agents), ``position_requirements(services, account)``
  (``{iid: {margin_c, collateral_c, basis}}``), ``custodian_of(account_row)`` (the simulator's custody map: Treasury bank, US Exchange PB
  custody DVP with listed derivatives through OCC / CME via the FCM, Debt custodian bank, Credit bank, Foreign & FX global custodian,
  Private issuer register, a prime-broker account bilateral under the ISDA / CSA), ``margin_calls_rows`` and ``pledges_view``.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .books import account_row, lifetime_row, split_account_id
from .clock import epoch_of, ny_of
from .commands import ConflictError, RejectError, TxContext
from .db import next_id
from .ledger import Entry, balance, cr, dr, natural
from .lifecycle_jobs import Job as _JobBase
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
#: 1.0.6 (Part B): the Reg T terms per account, the margin calls, the CSA securities pledges and the job's start marker
MARGIN_TERMS_TABLE = ("CREATE TABLE IF NOT EXISTS margin_terms (account_id TEXT PRIMARY KEY, initial_pct TEXT NOT NULL, maintenance_pct TEXT NOT NULL,"
                      " long_loan_value_pct TEXT NOT NULL, updated_ts INTEGER NOT NULL, command_id INTEGER)")
MARGIN_CALLS_TABLE = ("CREATE TABLE IF NOT EXISTS margin_calls (call_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, kind TEXT NOT NULL, counterparty TEXT NOT NULL,"
                      " counterparty_id TEXT, business_date TEXT NOT NULL, currency TEXT NOT NULL, amount_c INTEGER NOT NULL, issued_ts INTEGER NOT NULL,"
                      " due_ts INTEGER NOT NULL, status TEXT NOT NULL, met_ts INTEGER, unmet_ts INTEGER, cured_ts INTEGER, funded_c INTEGER NOT NULL DEFAULT 0,"
                      " transfers_json TEXT NOT NULL DEFAULT '[]', detail_json TEXT NOT NULL DEFAULT '{}', note TEXT, command_id INTEGER,"
                      " idem_key TEXT NOT NULL UNIQUE)")
MARGIN_CALLS_INDEX = "CREATE INDEX IF NOT EXISTS ix_margin_calls_status ON margin_calls(status, account_id)"
PLEDGES_TABLE = ("CREATE TABLE IF NOT EXISTS collateral_pledges (pledge_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, instrument_id TEXT NOT NULL,"
                 " face INTEGER NOT NULL, pledged_to TEXT NOT NULL, contract_id TEXT, haircut TEXT NOT NULL, status TEXT NOT NULL, pledged_ts INTEGER NOT NULL,"
                 " released_ts INTEGER, command_id INTEGER, release_command_id INTEGER, note TEXT)")
PLEDGES_INDEX = "CREATE INDEX IF NOT EXISTS ix_collateral_pledges ON collateral_pledges(account_id, instrument_id, status)"
STATE_TABLE = "CREATE TABLE IF NOT EXISTS collateral_state (key TEXT PRIMARY KEY, value TEXT NOT NULL, ts INTEGER NOT NULL)"
SCHEMA = SETTLEMENT_COLUMNS + [CSA_TERMS_TABLE, MARGIN_TERMS_TABLE, MARGIN_CALLS_TABLE, MARGIN_CALLS_INDEX, PLEDGES_TABLE, PLEDGES_INDEX, STATE_TABLE]

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
    acct = _open_account(conn, s["account_id"])
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
    who = (acct["name"] if acct is not None and "name" in acct.keys() and acct["name"] else None) or s["account_id"]     # P8B-13: the account's name
    ctx.attention("WARN", "collateral", "SETTLEMENT_FAILED",
                  f"{who}: settlement {sid} ({what} {s['currency']} {fmt_money(abs(int(s['amount_c'])))}, due {s['due_date']}) - {reason};"
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
        # P8R1-06: the owner has chosen - the "resolve it" item no longer asks anything of him, so it closes now (the settlement keeps
        # saying "retry requested: it settles at the next settlement run" until it settles; a later fail raises its own item)
        conn.execute("UPDATE attention SET resolved_ts=? WHERE code='SETTLEMENT_FAILED' AND ref_type='settlement' AND ref_id=? AND resolved_ts IS NULL",
                     (ctx.ts, sid))
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


# ==============================================================================================================================
# Part B (FINSIM1_PLAN P6 tasks 4-8, release 1.0.6): account-level Reg T margin (PORTFOLIO-11), margin calls and the MARGIN_CALL
# job (PORTFOLIO-12), Treasury bills and CSA securities pledges (PORTFOLIO-09), custody (PORTFOLIO-18) and the C16 functions the
# Dashboard, Risk, Activity, position rows, the Accounts page and the Cash & Collateral page read (module docstring PART B).
# ==============================================================================================================================
REGT_DEFAULTS = {"initial_pct": Decimal("0.50"), "maintenance_pct": Decimal("0.30"), "long_loan_value_pct": Decimal("0.50")}
REGT_ROLES = ("US_EXCHANGE", "PB")                          # the accounts that hold US equities (physical shorts included)
REGT_KINDS = ("EQUITY", "ETF", "ADR", "REIT", "PREFERRED")  # tables.EQUITY_KINDS: what Reg T margins here (USD listings)
MARGIN_ADJUSTER_KEY = "margin"
MARGIN_ADJUSTER_LABEL = "margin requirement (Reg T)"
REGT_TEXT = ("Reg T, account level (assumed terms, editable per account): requirement = 50 % initial (30 % maintenance shown beside it) of the"
             " physical short market value - the short sale's proceeds and the lender's 102 % cash collateral already cover the short value itself;"
             " eligible collateral = fully paid US equity / ETF longs at a 50 % loan value (less PB financing drawn) + free cash at 100 % (settled +"
             " receivables - payables - restricted collateral - the short-option margin reservation).  The cash the requirement needs beyond the"
             " longs' loan value is reserved (never spent or swept); a requirement beyond the eligible collateral is a deficit and a margin call."
             " Positions are never sold for you.")
SHORT_COLLATERAL_PCT = Decimal("1.02")
GOOD_QUALITIES = ("delayed", "close", "realtime", "live", "computed", "stale", "settle", "official")

CALL_KINDS = ("FUTURES_VM", "OTC_VM", "OTC_IM", "BORROW_COLLATERAL", "REG_T")
CALL_KIND_LABEL = {"FUTURES_VM": "futures variation margin", "OTC_VM": "OTC variation margin (CSA)", "OTC_IM": "OTC initial margin shortfall (CSA)",
                   "BORROW_COLLATERAL": "stock-borrow collateral top-up", "REG_T": "Reg T margin deficit"}
CALL_STATUSES = ("OPEN", "MET", "UNMET")
MARGIN_CALL_ET = "17:20"
CALL_DUE_ET = "10:00"
CALL_LOOKBACK_DAYS = 14
CALL_CODE = "MARGIN_CALL"
CALL_ROLES = ("US_EXCHANGE", "PB")
FCM_NAME = "CME Clearing (via the futures commission merchant)"
FCM_ID = "CME"
CALLS_TEXT = ("margin calls: what a counterparty asked the fund to deliver after the day's close (17:20 ET) - futures variation margin to the"
              " clearing house, variation margin and any initial-margin shortfall under a prime broker's CSA, stock-borrow collateral top-ups to the"
              " lender, a Reg T deficit; due the next business day 10:00 ET; MET once funded - the variation margin by its own payment (own cash,"
              " a Treasury sweep, PB financing), a Reg T deficit by cash you move into the account - with the funding transfers listed, else OPEN"
              " with an ERROR attention and UNMET after the due time; nothing moves by itself and positions are never sold for you")
START_KEY = "margin_call_start_ts"

PLEDGE_STATUSES = ("PLEDGED", "RELEASED")
PLEDGE_TYPES = ("BILL", "NOTE", "BOND")                    # US Treasuries a CSA accepts here (TIPS and FRNs are not priced for booking)
PLEDGE_TEXT = ("CSA securities collateral: US Treasuries held in the book's accounts pledged to a prime broker under its CSA - eligibility and"
               " haircuts are the prime broker's CSA terms (assumed, editable on its schedule); the securities stay the fund's position (P&L,"
               " accrual) and move no GL balance; a pledged face cannot be sold or pledged again until released; eligible value = face x (clean +"
               " accrued) / 100 x (1 - haircut)")
TBILL_TEXT = ("Treasury bills held in any account of the book: cash equivalents at market (the bond mark: FedInvest or computed), maturity and the"
              " purchase discount accreted straight-line to today (a display figure: the ledger carries the bill at cost)")

#: account role -> (custodian, how the positions are held) - PORTFOLIO-18 (the simulator's custody map, assumed; not a custody agreement)
CUSTODIANS = {
    "TREASURY": ("bank", "the fund's cash at its bank (no securities)"),
    "US_EXCHANGE": ("PB custody, DVP", "US equities and ETFs at the prime broker's custody, settled delivery versus payment; listed options and"
                                       " futures cleared through OCC / CME via the FCM"),
    "DEBT": ("custodian bank, DVP", "US Treasuries in Fed book-entry (Fedwire Securities) at the custodian bank; corporates and foreign"
                                    " government bonds through DTC / the local depository"),
    "CREDIT": ("bank", "cash, loans and repo cash at the bank; repo collateral moves through the dealer"),
    "FOREIGN_FX": ("global custodian", "foreign listings at the global custodian's local sub-custodians; FX cash at the bank"),
    "PRIVATE": ("issuer register", "private company shares recorded on the issuer's share register (transfer agent)"),
}
KIND_CUSTODIAN = {"OPTION": ("OCC via the clearing broker", "listed options cleared by the Options Clearing Corporation"),
                  "FUTURE": (FCM_NAME, "futures cleared by CME Clearing; initial and variation margin held by the FCM")}
CUSTODY_ORDER = ["PB custody, DVP", "OCC via the clearing broker", FCM_NAME, "custodian bank, DVP", "global custodian", "issuer register", "bank"]


def _role_of(acct) -> str:
    kind = _get(acct, "kind")
    if kind == "TREASURY":
        return "TREASURY"
    if kind == "PB":
        return "PB"
    return str(_get(acct, "role") or "")


def _account_row_dict(conn, account_id: str) -> Optional[dict]:
    r = conn.execute("SELECT * FROM accounts WHERE account_id=?", (account_id,)).fetchone()
    return dict(r) if r is not None else None


def _dec(v, default: Optional[Decimal] = None) -> Optional[Decimal]:
    if v is None or v == "":
        return default
    try:
        d = Decimal(str(v))
    except Exception:   # noqa: BLE001 - an unreadable stored value reads as the default
        return default
    return d if d.is_finite() else default


def _pct_text(f) -> str:
    return f"{(Decimal(f) * 100).quantize(Decimal('0.01')).normalize():f} %"


def _has_tables(conn, *names: str) -> bool:
    return all(_table_exists(conn, n) for n in names)


def phase2_ready(conn) -> bool:
    """True on a database where the 1.0.6 collateral tables exist (``bind`` ran with this release)."""
    return _has_tables(conn, "margin_terms", "margin_calls", "collateral_pledges", "collateral_state")


# ---- instruments, prices ---------------------------------------------------------------------------------------------------
class _Inst:
    """What these views need of an instrument: kind, currency, symbol, name, multiplier, spec, and the contract value of qty at px."""

    def __init__(self, iid: str, obj=None, row=None):
        self.instrument_id = iid
        self.obj = obj
        if obj is not None:
            def g(k, d=None):
                v = getattr(obj, k, d)
                return d if v is None else v
        else:
            def g(k, d=None):
                return row[k] if row is not None and k in row.keys() and row[k] is not None else d
        self.kind = str(g("kind", "") or "")
        self.currency = str(g("currency", "USD") or "USD")
        self.symbol = str(g("symbol", iid) or iid)
        self.name = str(g("name", iid) or iid)
        self.multiplier = _dec(g("multiplier", "1"), Decimal(1)) or Decimal(1)
        self.price_scale = _dec(g("price_scale", "1"), Decimal(1)) or Decimal(1)
        spec = g("spec", None)
        if spec is None and row is not None and "spec_json" in row.keys():
            try:
                spec = json.loads(row["spec_json"] or "{}")
            except ValueError:
                spec = {}
        self.spec = spec if isinstance(spec, dict) else {}
        self.expiry = g("expiry", None)

    def value_c(self, px: Decimal, qty: int) -> int:
        if self.obj is not None and callable(getattr(self.obj, "contract_value_c", None)):
            return int(self.obj.contract_value_c(px, int(qty)))
        return to_c(Decimal(int(qty)) * Decimal(px) * self.multiplier * self.price_scale)


def _inst(conn, master, iid: str) -> Optional[_Inst]:
    obj = None
    if master is not None:
        try:
            obj = master.get(iid, conn)
        except TypeError:
            obj = master.get(iid)
        except Exception:   # noqa: BLE001 - fall back to the stored row
            obj = None
    if obj is not None:
        return _Inst(iid, obj=obj)
    row = conn.execute("SELECT * FROM instruments WHERE instrument_id=?", (iid,)).fetchone()
    return _Inst(iid, row=row) if row is not None else None


def _snap_mark(snapshot, iid: str):
    if snapshot is None:
        return None
    try:
        v = snapshot.mark(iid)
    except Exception:   # noqa: BLE001 - a snapshot without the instrument: no current mark
        return None
    if v is None or getattr(v, "mark", None) is None or str(getattr(v, "quality", "")) == "unavailable":
        return None
    return v


def position_price(conn, snapshot, account_id: str, iid: str, qty: int, cost_c: int, inst: Optional[_Inst]) -> Tuple[Optional[Decimal], str, str]:
    """``(price, basis, quality)`` of a position for the margin figures: the view's / command's mark when it has one, else the last
    recorded CLOSE (or SETTLE), else (a short) the lender's last collateral mark (1400 / 102 %), else the cost per unit - each labelled;
    never invented (None only when not even a cost exists)."""
    v = _snap_mark(snapshot, iid)
    if v is not None:
        return Decimal(v.mark), f"{v.quality} mark", str(v.quality)
    r = conn.execute("SELECT price, mark_date FROM marks WHERE instrument_id=? AND kind IN ('CLOSE','SETTLE') ORDER BY mark_date DESC LIMIT 1", (iid,)).fetchone()
    if r is not None:
        return qprice(r["price"]), f"last recorded close {r['mark_date']} (no current mark)", "close"
    unit = (inst.multiplier * inst.price_scale) if inst is not None else Decimal(1)
    if qty < 0:
        coll = balance(conn, account_id, "1400", "USD", iid)
        if coll > 0 and unit:
            return (from_c(coll) / (SHORT_COLLATERAL_PCT * abs(qty) * unit)).quantize(Decimal("0.0001")), "the lender's last collateral mark (102 %)", "collateral"
    if cost_c and qty and unit:
        return (from_c(abs(int(cost_c))) / (abs(qty) * unit)).quantize(Decimal("0.0001")), "cost (no mark recorded)", "cost"
    return None, "no price", "unavailable"


# ---- Reg T (task 4, PORTFOLIO-11) ----------------------------------------------------------------------------------------------
def margin_terms(conn, account_id: str) -> dict:
    """The account's Reg T terms (fractions): its ``margin_terms`` row, else the assumed defaults 50 % / 30 % / 50 %."""
    out = dict(REGT_DEFAULTS)
    src, ts = "assumed (default)", None
    if _table_exists(conn, "margin_terms"):
        r = conn.execute("SELECT * FROM margin_terms WHERE account_id=?", (account_id,)).fetchone()
        if r is not None:
            for k in REGT_DEFAULTS:
                out[k] = _dec(r[k], REGT_DEFAULTS[k])
            src, ts = "edited (assumed terms)", int(r["updated_ts"])
    return {"initial_pct": out["initial_pct"], "maintenance_pct": out["maintenance_pct"], "long_loan_value_pct": out["long_loan_value_pct"],
            "source": src, "updated_ts": ts}


def _terms_json(t: dict) -> dict:
    return {"initial_pct": str(t["initial_pct"]), "maintenance_pct": str(t["maintenance_pct"]), "long_loan_value_pct": str(t["long_loan_value_pct"]),
            "initial_text": _pct_text(t["initial_pct"]), "maintenance_text": _pct_text(t["maintenance_pct"]),
            "long_loan_value_text": _pct_text(t["long_loan_value_pct"]), "source": t["source"], "updated_ts": t["updated_ts"]}


def _lent_map(conn, account_id: str) -> Dict[str, int]:
    """``{instrument_id: shares out on loan}`` of the account (``lending_accruals.lent_qty`` for every instrument in one read)."""
    if not _table_exists(conn, "sec_loans"):
        return {}
    return {r[0]: int(r[1] or 0) for r in conn.execute("SELECT instrument_id, COALESCE(SUM(qty - qty_recalled), 0) FROM sec_loans WHERE account_id=?"
                                                         " AND status='OPEN' GROUP BY instrument_id", (account_id,)).fetchall()}


def _lent_qty(conn, account_id: str, iid: str) -> int:
    try:
        from .lending_accruals import lent_qty, seclend_tables_ready
    except Exception:   # noqa: BLE001 - a build without securities lending: nothing is lent
        return 0
    try:
        return int(lent_qty(conn, account_id, iid) or 0) if seclend_tables_ready(conn) else 0
    except Exception:   # noqa: BLE001
        return 0


def _options_reserved_c(conn, account_id: str) -> int:
    if not _table_exists(conn, "margin_state"):
        return 0
    r = conn.execute("SELECT options_required_c FROM margin_state WHERE account_id=?", (account_id,)).fetchone()
    return max(0, int(r["options_required_c"] or 0)) if r is not None else 0


def free_cash_parts(conn, account_id: str, ccy: str = "USD") -> dict:
    """Free cash of the Reg T test (natural cents): settled + receivables - payables - restricted collateral - the short-option margin
    reservation, floored at 0."""
    settled = balance(conn, account_id, "1010", ccy)
    recv = balance(conn, account_id, "1200", ccy) + balance(conn, account_id, "1250", ccy)
    pay = balance(conn, account_id, "2100", ccy) + balance(conn, account_id, "2350", ccy)
    restricted = restricted_c(conn, account_id, ccy) if schema_ready(conn) else 0
    opt = _options_reserved_c(conn, account_id) if ccy == "USD" else 0
    return {"settled_c": settled, "receivable_c": recv, "payable_c": pay, "restricted_c": restricted, "options_reserved_c": opt,
            "free_cash_c": max(0, settled + recv - pay - restricted - opt)}


def _has_physical_short(conn, account_id: str) -> bool:
    q = ",".join("?" for _ in REGT_KINDS)
    return conn.execute(f"SELECT 1 FROM v_positions p JOIN instruments i ON i.instrument_id=p.instrument_id WHERE p.account_id=? AND p.qty < 0"
                        f" AND i.kind IN ({q}) AND i.currency='USD' LIMIT 1", (account_id, *REGT_KINDS)).fetchone() is not None


def regt_view(conn, account_id: str, snapshot=None, master=None, acct: Optional[dict] = None) -> dict:
    """The account's Reg T test (module docstring PART B, REG T): requirement, maintenance, eligible collateral, excess / deficit, the
    reserved margin cash and every position's part, with the terms and the price basis of each position."""
    a = acct if acct is not None else _account_row_dict(conn, account_id)
    if a is None:
        raise RejectError("ACCOUNT_UNKNOWN", f"account {account_id} does not exist")
    role = _role_of(a)
    terms = margin_terms(conn, account_id)
    out = {"account_id": account_id, "name": a.get("name"), "role": role, "applies": role in REGT_ROLES, "terms": _terms_json(terms), "definition": REGT_TEXT,
           "positions": [], "notes": [], "short_mv_c": 0, "long_mv_c": 0, "long_loan_value_c": 0, "pb_financed_c": 0, "requirement_c": 0, "maintenance_c": 0,
           "eligible_c": 0, "excess_c": 0, "deficit_c": 0, "margin_cash_c": 0, "needed_cash_c": 0, "below_maintenance": False, "free_cash_c": 0, "cash": {}}
    if role not in REGT_ROLES:
        out["notes"].append("Reg T applies to the accounts that hold US equities (US Exchange and the prime-broker accounts)")
        return out
    short_mv = long_mv = 0
    rows = conn.execute("SELECT instrument_id, qty, cost_c, currency FROM v_positions WHERE account_id=? ORDER BY instrument_id", (account_id,)).fetchall()
    lent_map = _lent_map(conn, account_id)
    for p in rows:
        iid, qty = p["instrument_id"], int(p["qty"])
        inst = _inst(conn, master, iid)
        if inst is None or inst.kind not in REGT_KINDS:
            continue
        if inst.currency != "USD":
            out["notes"].append(f"{inst.symbol}: a {inst.currency} listing is outside Reg T here")
            continue
        price, basis, quality = position_price(conn, snapshot, account_id, iid, qty, int(p["cost_c"] or 0), inst)
        row = {"instrument_id": iid, "symbol": inst.symbol, "qty": qty, "side": "SHORT" if qty < 0 else "LONG", "price": None if price is None else str(price),
               "price_basis": basis, "quality": quality, "mv_c": None, "requirement_c": 0, "loan_value_c": 0, "lent_qty": 0}
        if price is None:
            out["notes"].append(f"{inst.symbol}: no price at all (no mark, close or cost): left out of the Reg T figures")
            out["positions"].append(row)
            continue
        if qty < 0:
            mv = inst.value_c(price, -qty)
            short_mv += mv
            row.update(mv_c=mv, requirement_c=to_c(from_c(mv) * terms["initial_pct"]))
        else:
            lent = min(qty, max(0, int(lent_map.get(iid, 0))))
            mv = inst.value_c(price, qty - lent)
            long_mv += mv
            row.update(mv_c=mv, lent_qty=lent, loan_value_c=to_c(from_c(mv) * terms["long_loan_value_pct"]))
            if lent:
                out["notes"].append(f"{inst.symbol}: {lent:,} shares out on loan are not counted (the borrower's cash collateral secures them)")
        if quality not in GOOD_QUALITIES:
            out["notes"].append(f"{inst.symbol}: priced at {basis}")
        out["positions"].append(row)
    pb_financed = max(0, balance(conn, account_id, "2700", "USD")) if role == "PB" else 0
    loan_value = max(0, to_c(from_c(long_mv) * terms["long_loan_value_pct"]) - pb_financed)
    cash = free_cash_parts(conn, account_id, "USD")
    free = int(cash["free_cash_c"])
    requirement = to_c(from_c(short_mv) * terms["initial_pct"])
    maintenance = to_c(from_c(short_mv) * terms["maintenance_pct"])
    eligible = loan_value + free
    needed_cash = max(0, requirement - loan_value)
    out.update(short_mv_c=short_mv, long_mv_c=long_mv, long_loan_value_c=loan_value, pb_financed_c=pb_financed, free_cash_c=free, cash=cash,
               requirement_c=requirement, maintenance_c=maintenance, eligible_c=eligible, excess_c=eligible - requirement,
               deficit_c=max(0, requirement - eligible), margin_cash_c=min(free, needed_cash), needed_cash_c=needed_cash,
               below_maintenance=eligible < maintenance)
    return out


def margin_adjuster(conn, account_id: str, ccy: str, snapshot=None, master=None) -> int:
    """The C9b adjuster "margin requirement (Reg T)": the account's cash the Reg T requirement needs beyond the loan value of its longs
    (``regt_view`` ``margin_cash_c``) - available, spendable, buying power and SWEEP_OUT keep it.  0 on a database where the 1.0.6
    tables do not exist (the registry is process-wide), for another currency, another kind of account or an account without a
    physical short."""
    if ccy != "USD" or not _table_exists(conn, "margin_terms"):
        return 0
    a = _account_row_dict(conn, account_id)
    if a is None or _role_of(a) not in REGT_ROLES or not _has_physical_short(conn, account_id):
        return 0
    return max(0, int(regt_view(conn, account_id, snapshot, master, a)["margin_cash_c"]))


def _register_margin_adjuster() -> bool:
    from . import accounting as _acc
    fn = getattr(_acc, "add_cash_adjuster", None)
    if not callable(fn):
        return False
    fn(MARGIN_ADJUSTER_KEY, MARGIN_ADJUSTER_LABEL, margin_adjuster)
    return True


def _pct_arg(payload: dict, key: str, lo: Decimal, hi: Decimal) -> Optional[Decimal]:
    raw = payload.get(key)
    if raw is None or str(raw).strip() == "":
        return None
    if isinstance(raw, (bool, float)):
        raise RejectError("TERMS_VALUE", f"{key}: send a decimal string such as \"0.50\" (a fraction), not {raw!r}", {"field": key})
    try:
        v = Decimal(str(raw).strip())
    except Exception as e:   # noqa: BLE001
        raise RejectError("TERMS_VALUE", f"{key}: {raw!r} is not a number (a fraction: 0.50 = 50 %)", {"field": key}) from e
    if not v.is_finite() or v < lo or v > hi or v != v.quantize(Decimal("0.0001")):
        raise RejectError("TERMS_VALUE", f"{key}: {raw} must be a fraction between {lo} and {hi} with at most 4 decimals (0.50 = 50 %)", {"field": key})
    return v


def h_margin_terms(ctx: TxContext, payload: dict) -> dict:
    """``margin.terms {account_id, initial_pct, maintenance_pct, long_loan_value_pct}`` (light, SETTINGS_CHANGED): the account's Reg T
    terms as fractions (0.50 = 50 %); a field left out keeps its value; maintenance may not exceed initial."""
    conn = ctx.conn
    aid = str(payload.get("account_id") or "")
    acct = _open_account(conn, aid)
    if _role_of(acct) not in REGT_ROLES:
        raise RejectError("NOT_MARGIN_ACCOUNT", f"{aid} holds no US equities: Reg T terms belong to US Exchange and the prime-broker accounts")
    if not _table_exists(conn, "margin_terms"):
        raise ConflictError("NOT_READY", "the margin terms are not set up on this database yet (the collateral module did not bind)")
    before = margin_terms(conn, aid)
    new = {"initial_pct": _pct_arg(payload, "initial_pct", Decimal("0.01"), Decimal("1")),
           "maintenance_pct": _pct_arg(payload, "maintenance_pct", Decimal("0"), Decimal("1")),
           "long_loan_value_pct": _pct_arg(payload, "long_loan_value_pct", Decimal("0"), Decimal("0.99"))}
    if all(v is None for v in new.values()):
        raise RejectError("NOTHING_TO_UPDATE", "give initial_pct, maintenance_pct or long_loan_value_pct (fractions: 0.50 = 50 %)")
    after = {k: (new[k] if new[k] is not None else before[k]) for k in REGT_DEFAULTS}
    if after["maintenance_pct"] > after["initial_pct"]:
        raise RejectError("TERMS_VALUE", f"maintenance {_pct_text(after['maintenance_pct'])} may not exceed initial {_pct_text(after['initial_pct'])}",
                          {"field": "maintenance_pct"})
    if all(after[k] == before[k] for k in REGT_DEFAULTS):
        raise RejectError("NOTHING_TO_UPDATE", "the terms are already these")
    conn.execute("INSERT INTO margin_terms(account_id, initial_pct, maintenance_pct, long_loan_value_pct, updated_ts, command_id) VALUES (?,?,?,?,?,?)"
                 " ON CONFLICT(account_id) DO UPDATE SET initial_pct=excluded.initial_pct, maintenance_pct=excluded.maintenance_pct,"
                 " long_loan_value_pct=excluded.long_loan_value_pct, updated_ts=excluded.updated_ts, command_id=excluded.command_id",
                 (aid, str(after["initial_pct"]), str(after["maintenance_pct"]), str(after["long_loan_value_pct"]), ctx.ts, ctx.command_id))
    b = {k: str(before[k]) for k in REGT_DEFAULTS}
    f = {k: str(after[k]) for k in REGT_DEFAULTS}
    ctx.event("SETTINGS_CHANGED", account_id=aid, ref_type="margin_terms", ref_id=aid, before=b, after=f)
    return {"account_id": aid, "before": b, "after": f, "terms": _terms_json(margin_terms(conn, aid))}


# ---- CSA securities pledges and Treasury bills (task 6, PORTFOLIO-09) ---------------------------------------------------------
def csa_haircut(conn, pb_account_id: str, bond_type: str) -> Optional[Decimal]:
    """The CSA haircut of a Treasury of ``bond_type`` under ``pb_account_id``'s schedule (``prime`` FIELD_SPECS ``csa_bill_haircut`` /
    ``csa_bond_haircut``; assumed, editable), or None when its CSA takes cash only (``csa_securities`` CASH_ONLY) or the type is not
    eligible."""
    t = str(bond_type or "").upper()
    if t not in PLEDGE_TYPES:
        return None
    try:
        from .prime import read_schedule
        s = read_schedule(conn, pb_account_id).to_dict()
    except Exception:   # noqa: BLE001 - no schedule: no CSA terms
        return None
    if str(s.get("csa_securities") or "TREASURIES").upper() != "TREASURIES":
        return None
    return _dec(s.get("csa_bill_haircut") if t == "BILL" else s.get("csa_bond_haircut"))


def _bond_type(inst: Optional[_Inst]) -> str:
    if inst is None:
        return ""
    return str((inst.spec or {}).get("type") or "").upper()


def csa_pledged_face(conn, account_id: str, instrument_id: str) -> int:
    """Face of ``instrument_id`` in ``account_id`` pledged under a CSA and not released (``repo.pledged_face`` adds it, so a pledged face
    cannot be sold or pledged in a repo; C27)."""
    if not _table_exists(conn, "collateral_pledges"):
        return 0
    r = conn.execute("SELECT COALESCE(SUM(face), 0) FROM collateral_pledges WHERE account_id=? AND instrument_id=? AND status='PLEDGED'",
                     (account_id, instrument_id)).fetchone()
    return int(r[0] or 0)


def _bond_value(services, conn, inst: _Inst, face: int, snapshot, on: date) -> dict:
    """``{value_c, clean, accrued, dirty, quality, basis, note}`` of ``face`` of a Treasury: ``repo.collateral`` (face x (clean + accrued) /
    100 at the bond mark) when it can value it, else ``value_c`` None with the reason."""
    try:
        from . import repo as _repo
        obj = inst.obj if inst.obj is not None else (services.instruments.get(inst.instrument_id) if getattr(services, "instruments", None) is not None else None)
        c = _repo.collateral(services, obj, int(face), snapshot, on, allow_stale=True)
        return {"value_c": int(c.value_c), "clean": str(c.clean), "accrued": str(c.accrued), "dirty": str(c.dirty), "quality": c.quality,
                "basis": f"{c.source} ({c.quality})", "note": c.note}
    except RejectError as e:
        return {"value_c": None, "clean": None, "accrued": None, "dirty": None, "quality": "unavailable", "basis": "no price", "note": e.message}
    except Exception as e:   # noqa: BLE001 - a view never fails on one bond
        return {"value_c": None, "clean": None, "accrued": None, "dirty": None, "quality": "unavailable", "basis": "no price", "note": f"{type(e).__name__}: {e}"}


def _pledge_view(services, conn, r, snapshot, on: date) -> dict:
    inst = _inst(conn, getattr(services, "instruments", None), r["instrument_id"])
    haircut = _dec(r["haircut"], Decimal(0)) or Decimal(0)
    v = _bond_value(services, conn, inst, int(r["face"]), snapshot, on) if inst is not None else {"value_c": None, "note": "instrument unknown"}
    mv = v.get("value_c")
    eligible = None if mv is None else to_c(from_c(mv) * (Decimal(1) - haircut))
    to_row = _account_row_dict(conn, r["pledged_to"]) or {}
    return {"pledge_id": r["pledge_id"], "account_id": r["account_id"], "account_name": (_account_row_dict(conn, r["account_id"]) or {}).get("name"),
            "instrument_id": r["instrument_id"], "symbol": inst.symbol if inst else r["instrument_id"], "name": inst.name if inst else r["instrument_id"],
            "type": _bond_type(inst), "face": int(r["face"]), "price": v.get("dirty"), "clean": v.get("clean"), "accrued": v.get("accrued"),
            "price_basis": v.get("basis"), "price_note": v.get("note"), "market_value_c": mv, "haircut": str(haircut), "haircut_text": _pct_text(haircut),
            "eligible_c": eligible, "pledged_to": r["pledged_to"], "pledged_to_name": to_row.get("name"), "counterparty": to_row.get("broker"),
            "contract_id": r["contract_id"], "status": r["status"], "pledged_ts": r["pledged_ts"], "released_ts": r["released_ts"], "note": r["note"]}


def h_pledge(ctx: TxContext, payload: dict) -> dict:
    """``collateral.pledge {account_id, instrument_id, face, pledged_to, contract_id?}``: pledge settled face of a US Treasury held in
    ``account_id`` to the prime broker of ``pledged_to`` under its CSA (eligibility and haircut from its schedule).  No GL moves; the face
    becomes encumbered (``repo.pledged_face``)."""
    conn = ctx.conn
    services = _services(ctx)
    if not _table_exists(conn, "collateral_pledges"):
        raise ConflictError("NOT_READY", "CSA pledges are not set up on this database yet (the collateral module did not bind)")
    aid, iid, to = str(payload.get("account_id") or ""), str(payload.get("instrument_id") or ""), str(payload.get("pledged_to") or "")
    acct = _open_account(conn, aid)
    pb = _open_account(conn, to)
    if pb["kind"] != "PB":
        raise RejectError("NOT_PB", f"{to} is not a prime-broker account: securities are pledged under a prime broker's CSA")
    if pb["lifetime_id"] != acct["lifetime_id"]:
        raise RejectError("CROSS_BOOK", f"{aid} and {to} belong to different books")
    raw = payload.get("face")
    if isinstance(raw, (bool, float)):
        raise RejectError("FACE", f"face must be a whole number, got {raw!r}")
    try:
        face = int(str(raw if raw is not None else "").replace(",", "").strip())
    except ValueError as e:
        raise RejectError("FACE", f"face must be a whole number, got {raw!r}") from e
    if face <= 0:
        raise RejectError("FACE", "face must be positive")
    inst = _inst(conn, getattr(services, "instruments", None), iid)
    if inst is None:
        raise RejectError("INSTRUMENT_UNKNOWN", f"{iid} is not in the instrument master")
    btype = _bond_type(inst)
    if inst.kind != "BOND" or btype not in PLEDGE_TYPES or inst.currency != "USD":
        raise ConflictError("NOT_ELIGIBLE", f"{inst.symbol}: the CSA takes US Treasury bills, notes and bonds only", {"instrument_id": iid, "type": btype})
    haircut = csa_haircut(conn, to, btype)
    if haircut is None:
        raise ConflictError("NOT_ELIGIBLE", f"the CSA of {pb['name']} takes cash only (its schedule's CSA securities setting)", {"pledged_to": to})
    cid = str(payload.get("contract_id") or "") or None
    if cid:
        c = conn.execute("SELECT account_id, status FROM otc_contracts WHERE contract_id=?", (cid,)).fetchone()
        if c is None or c["account_id"] != to or c["status"] != "OPEN":
            raise RejectError("CONTRACT", f"contract {cid} is not an open contract of {to}")
    from . import repo as _repo
    held, unsettled, due = _repo.settled_face(conn, aid, iid)
    pledged = int(_repo.pledged_face(conn, aid, iid) or 0)
    free = held - unsettled - pledged
    if face > free:
        why = (f"{acct['name']} holds {held:,} face of {inst.symbol}" + (f"; {unsettled:,} bought and settling {due}" if unsettled else "")
               + (f"; {pledged:,} already pledged (repo or CSA)" if pledged else "") + f": at most {max(free, 0):,} can be pledged now")
        raise ConflictError("COLLATERAL_SHORT", why, {"position": held, "unsettled": unsettled, "pledged": pledged, "face": face})
    pid = next_id(conn, "PL")
    note = " ".join(str(payload.get("note") or "").split())[:200] or None
    conn.execute("INSERT INTO collateral_pledges(pledge_id, account_id, instrument_id, face, pledged_to, contract_id, haircut, status, pledged_ts, command_id, note)"
                 " VALUES (?,?,?,?,?,?,?,?,?,?,?)", (pid, aid, iid, face, to, cid, str(haircut), "PLEDGED", ctx.ts, ctx.command_id, note))
    ctx.event("COLLATERAL_PLEDGED", account_id=aid, instrument_id=iid, ref_type="pledge", ref_id=pid, face=face, pledged_to=to, haircut=str(haircut), contract_id=cid)
    ctx.touched_accounts.add(aid)
    r = conn.execute("SELECT * FROM collateral_pledges WHERE pledge_id=?", (pid,)).fetchone()
    return {"pledge": _pledge_view(services, conn, r, ctx.marks, date.fromisoformat(ctx.today))}


def h_release(ctx: TxContext, payload: dict) -> dict:
    """``collateral.release {pledge_id}``: the pledged face is free again (sellable, pledgeable).  Releasing cash initial margin against
    pledged securities (collateral substitution) is not in this version (FINSIM1_PLAN section 9.10)."""
    conn = ctx.conn
    if not _table_exists(conn, "collateral_pledges"):
        raise ConflictError("NOT_READY", "CSA pledges are not set up on this database yet")
    pid = str(payload.get("pledge_id") or "")
    r = conn.execute("SELECT * FROM collateral_pledges WHERE pledge_id=?", (pid,)).fetchone()
    if r is None:
        raise RejectError("NOT_FOUND", f"pledge {pid} does not exist")
    if r["status"] != "PLEDGED":
        raise ConflictError("NOT_PLEDGED", f"pledge {pid} was already released")
    _open_account(conn, r["account_id"])
    conn.execute("UPDATE collateral_pledges SET status='RELEASED', released_ts=?, release_command_id=? WHERE pledge_id=?", (ctx.ts, ctx.command_id, pid))
    ctx.event("COLLATERAL_RELEASED", account_id=r["account_id"], instrument_id=r["instrument_id"], ref_type="pledge", ref_id=pid, face=int(r["face"]),
              pledged_to=r["pledged_to"])
    ctx.touched_accounts.add(r["account_id"])
    r = conn.execute("SELECT * FROM collateral_pledges WHERE pledge_id=?", (pid,)).fetchone()
    return {"pledge": _pledge_view(_services(ctx), conn, r, ctx.marks, date.fromisoformat(ctx.today))}


# ---- scopes, readers -----------------------------------------------------------------------------------------------------------
def _scope_accounts(conn, scope) -> Tuple[Optional[str], List[dict]]:
    """``scope`` = an account id, ``{lifetime}:ALL``, a lifetime id or a book code -> (lifetime id, the account rows in sort order)."""
    s = str(scope or "").strip()
    if not s:
        raise RejectError("SCOPE", "a scope is required (an account, {lifetime}:ALL, a lifetime or a book)")
    if s.endswith(":ALL"):
        lid = s[:-4]
        if lifetime_row(conn, lid) is None:
            raise RejectError("LIFETIME_UNKNOWN", f"lifetime {lid!r} does not exist")
    elif ":" in s:
        a = _account_row_dict(conn, s)
        if a is None:
            raise RejectError("ACCOUNT_UNKNOWN", f"account {s} does not exist")
        return a["lifetime_id"], [a]
    elif lifetime_row(conn, s) is not None:
        lid = s
    else:
        from .books import open_lifetime
        row = open_lifetime(conn, s)
        if row is None:
            raise RejectError("SCOPE", f"{s!r} is neither an account, a lifetime nor a book with an open lifetime")
        lid = row["lifetime_id"]
    rows = [dict(r) for r in conn.execute("SELECT * FROM accounts WHERE lifetime_id=? ORDER BY sort_order, account_id", (lid,)).fetchall()]
    return lid, rows


def _reader_of(services):
    from .marks import shared_reader
    return shared_reader(services.db)


def _snapshot_of(services, snapshot=None, ids=None):
    """The mark snapshot of a read: the given one, else ``services.marks.snapshot(ids)`` - only the instruments the read needs (a snapshot
    of the whole quote cache costs about 0.6 s on the owner's book); None when there is no mark provider (the stored closes are then
    used, labelled)."""
    if snapshot is not None:
        return snapshot
    marks = getattr(services, "marks", None)
    try:
        return (marks.snapshot(sorted(set(ids))) if ids is not None else marks.snapshot()) if marks is not None else None
    except Exception:   # noqa: BLE001 - no snapshot: the stored closes are used, labelled
        return None


def _scope_ids(conn, account_ids: List[str]) -> List[str]:
    """Every instrument the collateral reads of ``account_ids`` may price: positions, open borrows and loans of shares, CSA pledges, working
    orders (their reservations) and OTC contracts."""
    ids = set()
    if not account_ids:
        return []
    q = ",".join("?" for _ in account_ids)
    ids |= {r[0] for r in conn.execute(f"SELECT DISTINCT instrument_id FROM v_positions WHERE account_id IN ({q})", account_ids).fetchall()}
    for table, cond in (("borrows", "status='OPEN'"), ("sec_loans", "status='OPEN'"), ("orders", "status IN ('WORKING','PARTIAL','SUSPENDED','WAITING_PARENT')")):
        if _table_exists(conn, table):
            ids |= {r[0] for r in conn.execute(f"SELECT DISTINCT instrument_id FROM {table} WHERE {cond} AND account_id IN ({q})", account_ids).fetchall()}
    if _table_exists(conn, "collateral_pledges"):
        ids |= {r[0] for r in conn.execute(f"SELECT DISTINCT instrument_id FROM collateral_pledges WHERE status='PLEDGED' AND (account_id IN ({q}) OR pledged_to IN ({q}))",
                                           list(account_ids) * 2).fetchall()}
    if _table_exists(conn, "otc_contracts"):
        ids |= {r[0] for r in conn.execute(f"SELECT contract_id FROM otc_contracts WHERE status='OPEN' AND account_id IN ({q})", account_ids).fetchall()}
    return sorted(i for i in ids if i)


def _today(services) -> date:
    from .clock import accounting_day
    clock = getattr(services, "clock", None)
    return accounting_day(clock.now()) if clock is not None else date.today()


def _now_ts(services) -> Optional[int]:
    clock = getattr(services, "clock", None)
    return int(clock.epoch()) if clock is not None else None


# ---- custody (C16 custodian_of, PORTFOLIO-18) ----------------------------------------------------------------------------------
def custodian_of(account_row) -> dict:
    """C16 / PORTFOLIO-18: ``{custodian, label, basis}`` of an account (the simulator's custody map, assumed): Treasury -> bank, US
    Exchange -> PB custody (DVP; listed derivatives through OCC / CME via the FCM), Debt -> custodian bank (Fed book-entry), Credit ->
    bank, Foreign & FX -> global custodian, Private -> issuer register, a prime-broker account -> bilateral with the dealer under the
    ISDA / CSA (securities at its PB custody)."""
    role = _role_of(account_row)
    if role == "PB":
        b = str(_get(account_row, "name") or _get(account_row, "broker") or "the prime broker").replace(" (PB)", "")
        return {"custodian": f"{b}: bilateral ISDA / CSA", "label": f"{b}: OTC bilateral under the ISDA / CSA; securities at its PB custody",
                "basis": "OTC contracts are bilateral with the dealer (initial and variation margin under its CSA); any securities held here sit in the"
                         " prime broker's custody, DVP"}
    c, basis = CUSTODIANS.get(role, ("bank", "cash at the bank"))
    return {"custodian": c, "label": c, "basis": basis}


def custody_statement(services, scope) -> dict:
    """PORTFOLIO-18: the scope's positions grouped by custodian (quantity, market value at the view's marks, the price basis) and each
    account's settled cash at the bank; OTC contracts under their dealer at the model mark of the last VM."""
    master = getattr(services, "instruments", None)
    groups: Dict[str, dict] = {}

    def group(name: str) -> dict:
        return groups.setdefault(name, {"custodian": name, "rows": [], "mv_c": 0, "mv_missing": 0})
    with _reader_of(services) as conn:
        lid, accts = _scope_accounts(conn, scope)
        snap = _snapshot_of(services, ids=_scope_ids(conn, [a["account_id"] for a in accts]))
        has_otc = _table_exists(conn, "otc_contracts")
        for a in accts:
            if a["status"] != "OPEN":
                continue
            for p in conn.execute("SELECT instrument_id, qty, cost_c FROM v_positions WHERE account_id=? ORDER BY instrument_id", (a["account_id"],)).fetchall():
                inst = _inst(conn, master, p["instrument_id"])
                kind = inst.kind if inst else ""
                if kind == "SWAP":
                    continue                                   # the OTC contracts are listed below with their dealer
                cust = KIND_CUSTODIAN[kind][0] if kind in KIND_CUSTODIAN else custodian_of(a)["custodian"]
                qty = int(p["qty"])
                price, basis, _q = position_price(conn, snap, a["account_id"], p["instrument_id"], qty, int(p["cost_c"] or 0), inst)
                mv = None if price is None or inst is None else inst.value_c(price, abs(qty)) * (1 if qty > 0 else -1)
                g = group(cust)
                g["rows"].append({"account_id": a["account_id"], "account_name": a["name"], "instrument_id": p["instrument_id"],
                                  "symbol": inst.symbol if inst else p["instrument_id"], "name": inst.name if inst else "", "kind": kind, "qty": qty,
                                  "price": None if price is None else str(price), "price_basis": basis, "mv_c": mv})
                if mv is None:
                    g["mv_missing"] += 1
                else:
                    g["mv_c"] += mv
            if has_otc:
                for c in conn.execute("SELECT contract_id, kind, notional_c, mark_c, currency FROM otc_contracts WHERE account_id=? AND status='OPEN' ORDER BY contract_id",
                                      (a["account_id"],)).fetchall():
                    g = group(custodian_of(a)["custodian"])
                    g["rows"].append({"account_id": a["account_id"], "account_name": a["name"], "instrument_id": c["contract_id"], "symbol": c["contract_id"],
                                      "name": f"OTC {c['kind']}", "kind": f"OTC {c['kind']}", "qty": None, "notional_c": int(c["notional_c"] or 0), "price": None,
                                      "price_basis": "model value as of the last variation margin", "mv_c": int(c["mark_c"] or 0)})
                    g["mv_c"] += int(c["mark_c"] or 0)
            cash = balance(conn, a["account_id"], "1010", "USD")
            if cash:
                g = group("bank")
                g["rows"].append({"account_id": a["account_id"], "account_name": a["name"], "instrument_id": "USD", "symbol": "USD cash", "name": "settled cash",
                                  "kind": "CASH", "qty": None, "price": None, "price_basis": "settled cash (GL 1010)", "mv_c": cash})
                g["mv_c"] += cash
    out = sorted(groups.values(), key=lambda g: (CUSTODY_ORDER.index(g["custodian"]) if g["custodian"] in CUSTODY_ORDER else 50, g["custodian"]))
    return {"lifetime_id": lid, "groups": out, "as_of": _now_ts(services),
            "note": "the simulator's custody map (assumed): which institution holds each position; OTC contracts are bilateral with the dealer"}


# ---- margin calls (task 5, PORTFOLIO-12) -----------------------------------------------------------------------------------------
def _call_start_ts(conn) -> Optional[int]:
    """When this release first bound on the database (``collateral_state`` START_KEY): no call is ever issued for a business day whose
    17:20 ET came before it (15.9 item 8: new lifecycle jobs start forward)."""
    if not _table_exists(conn, "collateral_state"):
        return None
    r = conn.execute("SELECT value FROM collateral_state WHERE key=?", (START_KEY,)).fetchone()
    try:
        return int(r["value"]) if r is not None else None
    except (TypeError, ValueError):
        return None


def call_dates(cal, now_ts: int, start_ts: int) -> List[date]:
    """The NYSE business days D whose 17:20 ET lies between the job's start and now (at most CALL_LOOKBACK_DAYS back)."""
    today = ny_of(int(now_ts)).date()
    out: List[date] = []
    d = today - timedelta(days=CALL_LOOKBACK_DAYS)
    while d <= today:
        biz = cal.is_business_day(d) if cal is not None else d.weekday() < 5
        if biz:
            t = epoch_of(d, MARGIN_CALL_ET)
            if int(start_ts) <= t <= int(now_ts):
                out.append(d)
        d += timedelta(days=1)
    return out


def _call_row(conn, call_id: str):
    r = conn.execute("SELECT * FROM margin_calls WHERE call_id=?", (call_id,)).fetchone()
    if r is None:
        raise RejectError("NOT_FOUND", f"margin call {call_id} does not exist")
    return r


def _prev_business_day(cal, d: date) -> date:
    if cal is not None:
        return cal.prev_business_day(d)
    x = d - timedelta(days=1)
    while x.weekday() >= 5:
        x -= timedelta(days=1)
    return x


def _next_business_day(cal, d: date) -> date:
    if cal is not None:
        return cal.next_business_day(d)
    x = d + timedelta(days=1)
    while x.weekday() >= 5:
        x += timedelta(days=1)
    return x


def _cash_moves(conn, account_id: str, prefix: str, lo: str, hi: str) -> List[dict]:
    """``[{entry_id, idem_key, ref_id, business_date, cash_c}]``: the entries whose idem key starts with ``prefix`` and whose business date is
    in ``(lo, hi]``, with their net settled-cash movement (natural 1010) in ``account_id`` (USD)."""
    rows = conn.execute("SELECT e.entry_id, e.idem_key, e.ref_id, e.business_date, COALESCE(SUM(l.amount_c), 0) AS a FROM journal_entries e"
                        " JOIN journal_lines l ON l.entry_id=e.entry_id WHERE e.idem_key LIKE ? AND l.account_id=? AND l.gl='1010' AND l.currency='USD'"
                        " AND e.business_date > ? AND e.business_date <= ? GROUP BY e.entry_id ORDER BY e.entry_id", (prefix + "%", account_id, lo, hi)).fetchall()
    return [{"entry_id": int(r["entry_id"]), "idem_key": r["idem_key"], "ref_id": r["ref_id"], "business_date": r["business_date"],
             "cash_c": natural("1010", int(r["a"] or 0))} for r in rows]


def _open_shortfalls(conn, links: List[Tuple[str, str]]) -> List[dict]:
    out = []
    for lt, li in links:
        for r in conn.execute("SELECT id, details_json FROM attention WHERE code='FUNDING_SHORTFALL' AND resolved_ts IS NULL AND ref_type=? AND ref_id=?",
                              (lt, li)).fetchall():
            try:
                d = json.loads(r["details_json"] or "{}")
            except ValueError:
                d = {}
            out.append({"attention_id": int(r["id"]), "link_type": lt, "link_id": li, "amount_c": int(d.get("amount_c") or 0)})
    return out


def _linked_transfers(conn, links: List[Tuple[str, str]], lo: Optional[str] = None, hi: Optional[str] = None) -> List[dict]:
    out, seen = [], set()
    for lt, li in links:
        sql, p = "SELECT * FROM transfers WHERE linked_type=? AND linked_id=?", [lt, li]
        if lo is not None:
            sql += " AND business_date > ? AND business_date <= ?"
            p += [lo, hi]
        for r in conn.execute(sql + " ORDER BY transfer_id", p).fetchall():
            if r["transfer_id"] in seen:
                continue
            seen.add(r["transfer_id"])
            out.append({"transfer_id": r["transfer_id"], "kind": r["kind"], "from_account": r["from_account"], "to_account": r["to_account"],
                        "amount_c": int(r["amount_c"]), "business_date": r["business_date"], "ts": r["ts"], "linked_type": lt, "linked_id": li})
    return out


def _broker_name(acct: dict) -> str:
    return str(acct.get("name") or acct.get("broker") or "the prime broker").replace(" (PB)", "")


def _regt_counterparty(conn, acct: dict) -> Tuple[str, Optional[str]]:
    """Who asks for a Reg T deficit: the prime broker of a PB account; for US Exchange the book's prime broker (its custodian), else
    the clearing broker (assumed terms)."""
    if acct.get("kind") == "PB":
        return f"{_broker_name(acct)} (prime broker, Reg T)", f"PB_{acct.get('broker') or ''}".rstrip("_")
    pb = conn.execute("SELECT * FROM accounts WHERE lifetime_id=? AND kind='PB' AND status='OPEN' ORDER BY sort_order LIMIT 1", (acct["lifetime_id"],)).fetchone()
    if pb is not None:
        return f"{_broker_name(dict(pb))} (prime broker, Reg T)", f"PB_{pb['broker'] or ''}".rstrip("_")
    return "clearing broker (Reg T, assumed terms)", None


def _transfers_into(conn, account_id: str, since_ts: int) -> List[dict]:
    """The cash moved INTO the account since ``since_ts`` (internal transfers and sweeps, USD): what funded a call after it was issued."""
    return [{"transfer_id": r["transfer_id"], "kind": r["kind"], "from_account": r["from_account"], "to_account": r["to_account"], "amount_c": int(r["amount_c"]),
             "business_date": r["business_date"], "ts": r["ts"], "linked_type": r["linked_type"], "linked_id": r["linked_id"]}
            for r in conn.execute("SELECT * FROM transfers WHERE to_account=? AND currency='USD' AND kind IN ('TRANSFER','SWEEP_IN') AND ts >= ?"
                                  " ORDER BY ts, transfer_id", (account_id, int(since_ts))).fetchall()]


def _insert_call(ctx: TxContext, acct: dict, kind: str, counterparty: str, cp_id: Optional[str], ds: str, amount_c: int, status: str, detail: dict,
                 transfers: List[dict], funded_c: int, note: str, idem: str, due_ts: int, call_id: Optional[str] = None) -> dict:
    conn = ctx.conn
    old = conn.execute("SELECT * FROM margin_calls WHERE idem_key=?", (idem,)).fetchone()
    if old is not None:
        return dict(old)
    cid = call_id or next_id(conn, "MC")
    met_ts = ctx.ts if status == "MET" else None
    conn.execute("INSERT INTO margin_calls(call_id, account_id, kind, counterparty, counterparty_id, business_date, currency, amount_c, issued_ts, due_ts, status,"
                 " met_ts, funded_c, transfers_json, detail_json, note, command_id, idem_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (cid, acct["account_id"], kind, counterparty, cp_id, ds, "USD", int(amount_c), ctx.ts, int(due_ts), status, met_ts, int(funded_c),
                  json.dumps(transfers), json.dumps(detail, default=str), note[:500], ctx.command_id, idem))
    ctx.event("MARGIN_CALL", account_id=acct["account_id"], ref_type="margin_call", ref_id=cid, kind=kind, counterparty=counterparty, amount_c=int(amount_c),
              business_date=ds, status=status, due_ts=int(due_ts))
    if status == "OPEN":
        _call_attention(ctx, cid, acct["account_id"], kind, counterparty, int(amount_c), int(due_ts), note)
    return dict(conn.execute("SELECT * FROM margin_calls WHERE call_id=?", (cid,)).fetchone())


def _call_attention(ctx: TxContext, call_id: str, account_id: str, kind: str, counterparty: str, amount_c: int, due_ts: int, note: str,
                    unmet: bool = False) -> None:
    due = ny_of(int(due_ts)).strftime("%Y-%m-%d %H:%M ET")
    row = ctx.conn.execute("SELECT name FROM accounts WHERE account_id=?", (account_id,)).fetchone()
    who = row["name"] if row is not None and row["name"] else account_id
    msg = (f"{who}: margin call {call_id} - {CALL_KIND_LABEL.get(kind, kind)} USD {fmt_money(amount_c)} from {counterparty}, "
           + (f"UNMET (due {due} passed); fund the account - positions are never sold for you" if unmet else f"due {due}; {note}"))
    row = ctx.conn.execute("SELECT id FROM attention WHERE code=? AND ref_type='margin_call' AND ref_id=? AND resolved_ts IS NULL", (CALL_CODE, call_id)).fetchone()
    if row is None:
        ctx.attention("ERROR", "collateral", CALL_CODE, msg[:600], {"account_id": account_id, "ref_type": "margin_call", "ref_id": call_id, "auto_resolve": 1,
                                                                "call_kind": kind, "amount_c": int(amount_c), "due_ts": int(due_ts)})
    else:
        ctx.conn.execute("UPDATE attention SET ts=?, message=? WHERE id=?", (ctx.ts, msg[:600], int(row["id"])))


def _resolve_call_attention(ctx: TxContext, call_id: str) -> None:
    ctx.conn.execute("UPDATE attention SET resolved_ts=? WHERE code=? AND ref_type='margin_call' AND ref_id=? AND resolved_ts IS NULL", (ctx.ts, CALL_CODE, call_id))


def _vm_calls(ctx: TxContext, acct: dict, lo: str, hi: str) -> List[dict]:
    """The VM-type demands of business days (lo, hi]: futures VM paid (net per account, the clearing house), OTC VM delivered under the
    CSA (net per prime-broker account), stock-borrow collateral calls (net per lender)."""
    conn = ctx.conn
    aid = acct["account_id"]
    out = []
    fut = _cash_moves(conn, aid, "vm:", lo, hi)
    net = sum(m["cash_c"] for m in fut)
    if net < 0:
        out.append({"kind": "FUTURES_VM", "counterparty": FCM_NAME, "counterparty_id": FCM_ID, "amount_c": -net, "links": [("vm", m["idem_key"]) for m in fut],
                    "window": None, "moves": fut})
    if acct.get("kind") == "PB":
        otc = _cash_moves(conn, aid, "swapvm:", lo, hi)
        net = sum(m["cash_c"] for m in otc)
        if net < 0:
            out.append({"kind": "OTC_VM", "counterparty": f"{_broker_name(acct)} (CSA)", "counterparty_id": f"PB_{acct.get('broker') or ''}".rstrip("_"),
                        "amount_c": -net, "links": [("swapvm", m["idem_key"]) for m in otc], "window": None, "moves": otc})
    marks = _cash_moves(conn, aid, "cmark:", lo, hi)
    by_lender: Dict[str, dict] = {}
    for m in marks:
        b = conn.execute("SELECT borrow_id, lender, counterparty FROM borrows WHERE borrow_id=?", (m["ref_id"],)).fetchone() if m["ref_id"] else None
        try:
            from .lending import lender_label
            name = lender_label(b["lender"]) if b is not None else "stock lender"
        except Exception:   # noqa: BLE001
            name = (b["lender"] if b is not None else None) or "stock lender"
        cp = (b["counterparty"] if b is not None and "counterparty" in b.keys() else None) or None
        g = by_lender.setdefault(name, {"kind": "BORROW_COLLATERAL", "counterparty": name, "counterparty_id": cp, "amount_c": 0, "links": [], "window": (lo, hi),
                                        "moves": []})
        g["amount_c"] -= m["cash_c"]
        g["moves"].append(m)
        if b is not None and ("borrow", b["borrow_id"]) not in g["links"]:
            g["links"].append(("borrow", b["borrow_id"]))
    out += [g for g in by_lender.values() if g["amount_c"] > 0]
    return out


def issue_calls(ctx: TxContext, services, account_id: str, ds: str) -> dict:
    """MARGIN_CALL for one account and business day ``ds`` (module docstring PART B, MARGIN CALLS)."""
    conn = ctx.conn
    acct = _account_row_dict(conn, account_id)
    if acct is None or acct["status"] != "OPEN":
        return {"status": "DONE", "note": f"{account_id} is not open", "posted": False, "calls": []}
    cal = getattr(services, "cal", None) or getattr(ctx, "cal", None)
    d = date.fromisoformat(ds)
    lo = _prev_business_day(cal, d).isoformat()
    due_ts = epoch_of(_next_business_day(cal, d), CALL_DUE_ET)
    made: List[dict] = []
    for dem in _vm_calls(ctx, acct, lo, ds):
        shortfalls = _open_shortfalls(conn, dem["links"])
        transfers = _linked_transfers(conn, dem["links"], *(dem["window"] or (None, None)))
        tre_c = sum(t["amount_c"] for t in transfers if t["kind"] == "SWEEP_IN")
        over_c = sum(s["amount_c"] for s in shortfalls)
        status = "OPEN" if shortfalls else "MET"
        detail = {"basis": f"{CALL_KIND_LABEL[dem['kind']]} of the business day(s) after {lo} through {ds}: net settled cash paid {fmt_money(dem['amount_c'])}",
                  "entries": [{"entry_id": m["entry_id"], "idem_key": m["idem_key"], "business_date": m["business_date"], "cash_c": m["cash_c"]} for m in dem["moves"]],
                  "from_treasury_c": tre_c, "overdraft_c": over_c, "own_or_pb_c": max(0, dem["amount_c"] - tre_c - over_c), "links": [list(x) for x in dem["links"]]}
        note = ("paid at once (own cash" + (f", Treasury {fmt_money(tre_c)}" if tre_c else "") + ")" if status == "MET"
                else f"paid with an overdraft of {fmt_money(over_c)} (Treasury and PB financing could not cover it); met once the account's cash is back")
        made.append(_insert_call(ctx, acct, dem["kind"], dem["counterparty"], dem["counterparty_id"], ds, dem["amount_c"], status, detail, transfers,
                                 dem["amount_c"] - over_c, note, f"mcall:{account_id}:{ds}:{dem['kind']}:{dem['counterparty']}", due_ts))
    if acct.get("kind") == "PB" and _table_exists(conn, "otc_contracts"):
        req = int(conn.execute("SELECT COALESCE(SUM(im_c), 0) FROM otc_contracts WHERE account_id=? AND status='OPEN'", (account_id,)).fetchone()[0] or 0)
        posted = balance(conn, account_id, "1330", "USD")
        if req > posted:
            open_im = conn.execute("SELECT 1 FROM margin_calls WHERE account_id=? AND kind='OTC_IM' AND status='OPEN'", (account_id,)).fetchone()
            if open_im is None:
                made.append(_insert_call(ctx, acct, "OTC_IM", f"{_broker_name(acct)} (CSA)", f"PB_{acct.get('broker') or ''}".rstrip("_"), ds, req - posted, "OPEN",
                                         {"basis": f"initial margin required {fmt_money(req)} (the open contracts' schedule IM) vs posted {fmt_money(posted)}",
                                          "required_c": req, "posted_c": posted}, [], 0, "the initial margin posted is below the contracts' requirement",
                                         f"mcall:{account_id}:{ds}:OTC_IM", due_ts))
    regt = None
    if _role_of(acct) in REGT_ROLES:
        regt = regt_view(conn, account_id, ctx.marks, getattr(services, "instruments", None), acct)
        cured_unmet = conn.execute("SELECT call_id FROM margin_calls WHERE account_id=? AND kind='REG_T' AND status='UNMET' AND cured_ts IS NULL", (account_id,)).fetchall()
        if regt["deficit_c"] <= 0:
            for r in cured_unmet:
                conn.execute("UPDATE margin_calls SET cured_ts=? WHERE call_id=?", (ctx.ts, r["call_id"]))
                _resolve_call_attention(ctx, r["call_id"])
        elif conn.execute("SELECT 1 FROM margin_calls WHERE account_id=? AND kind='REG_T' AND status='OPEN'", (account_id,)).fetchone() is None:
            cp, cp_id = _regt_counterparty(conn, acct)
            deficit = int(regt["deficit_c"])
            idem = f"mcall:{account_id}:{ds}:REG_T"
            if conn.execute("SELECT 1 FROM margin_calls WHERE idem_key=?", (idem,)).fetchone() is None:
                detail = {"basis": (f"Reg T requirement {fmt_money(regt['requirement_c'])} ({regt['terms']['initial_text']} of the short value "
                                    f"{fmt_money(regt['short_mv_c'])}) vs eligible {fmt_money(regt['eligible_c'])} (longs' loan value "
                                    f"{fmt_money(regt['long_loan_value_c'])} + free cash {fmt_money(regt['free_cash_c'])}): deficit {fmt_money(deficit)}"),
                          "requirement_c": regt["requirement_c"], "maintenance_c": regt["maintenance_c"], "eligible_c": regt["eligible_c"],
                          "short_mv_c": regt["short_mv_c"], "long_loan_value_c": regt["long_loan_value_c"], "free_cash_c": regt["free_cash_c"],
                          "margin_cash_c": regt["margin_cash_c"], "below_maintenance": regt["below_maintenance"], "terms": regt["terms"]}
                note = (f"the account's own cash the requirement needs ({fmt_money(regt['margin_cash_c'])}) is reserved; move {fmt_money(deficit)} into the"
                        " account (from Treasury or a deposit), or reduce the short, by the due time")
                made.append(_insert_call(ctx, acct, "REG_T", cp, cp_id, ds, deficit, "OPEN", detail, [], 0, note, idem, due_ts))
    if made:
        ctx.touched_accounts.add(account_id)
    n_open = sum(1 for c in made if c["status"] == "OPEN")
    note = (f"{len(made)} call(s): " + "; ".join(f"{c['call_id']} {CALL_KIND_LABEL.get(c['kind'], c['kind'])} {fmt_money(int(c['amount_c']))} {c['status']}" for c in made)
            if made else "no margin call" + (f" (Reg T excess {fmt_money(int(regt['excess_c']))})" if regt is not None and regt["requirement_c"] else ""))
    return {"status": "DONE", "note": note, "posted": False, "calls": [c["call_id"] for c in made], "open": n_open}


def _call_cured(ctx: TxContext, services, c, acct: dict) -> Tuple[bool, dict]:
    """(cured, details) of a call now: Reg T -> no deficit; VM / collateral -> no open funding shortfall on its links; IM -> posted covers
    the requirement."""
    conn = ctx.conn
    detail = json.loads(c["detail_json"] or "{}")
    if c["kind"] == "REG_T":
        v = regt_view(conn, c["account_id"], ctx.marks, getattr(services, "instruments", None), acct)
        return v["deficit_c"] <= 0, {"deficit_c": v["deficit_c"]}
    if c["kind"] == "OTC_IM":
        req = int(conn.execute("SELECT COALESCE(SUM(im_c), 0) FROM otc_contracts WHERE account_id=? AND status='OPEN'", (c["account_id"],)).fetchone()[0] or 0)
        posted = balance(conn, c["account_id"], "1330", "USD")
        return posted >= req, {"required_c": req, "posted_c": posted}
    links = [tuple(x) for x in detail.get("links") or []]
    sf = _open_shortfalls(conn, links)
    return not sf, {"overdraft_c": sum(s["amount_c"] for s in sf)}


def check_call(ctx: TxContext, services, call_id: str, cure_only: bool = False) -> dict:
    """An OPEN call: MET once cured (the Reg T deficit gone, the overdraft of its payment repaid, the IM posted) - linking the cash moved into
    the account since the call - UNMET once its due time passed, else SKIPPED (quiet; re-checked).  The job itself never moves cash: the
    owner meets a call (a transfer from Treasury, a deposit, a smaller short).  ``cure_only`` (an UNMET call): resolve its attention once
    the condition is cured."""
    conn = ctx.conn
    c = _call_row(conn, call_id)
    acct = _account_row_dict(conn, c["account_id"]) or {}
    if cure_only:
        if c["status"] != "UNMET" or c["cured_ts"]:
            return {"status": "DONE", "note": f"{call_id} is {c['status']}", "posted": False}
        cured, info = _call_cured(ctx, services, c, acct)
        if cured:
            conn.execute("UPDATE margin_calls SET cured_ts=? WHERE call_id=?", (ctx.ts, call_id))
            _resolve_call_attention(ctx, call_id)
            ctx.event("MARGIN_CALL_CURED", account_id=c["account_id"], ref_type="margin_call", ref_id=call_id)
            return {"status": "DONE", "note": f"{call_id} unmet at its due time, covered since", "posted": False}
        return {"status": "SKIPPED", "quiet": True, "note": f"{call_id} unmet; still not covered ({info})", "posted": False}
    if c["status"] != "OPEN":
        return {"status": "DONE", "note": f"{call_id} is already {c['status']}", "posted": False}
    if acct.get("status") != "OPEN":
        return {"status": "SKIPPED", "quiet": True, "note": f"{c['account_id']} is not open", "posted": False}
    transfers = json.loads(c["transfers_json"] or "[]")
    cured, info = _call_cured(ctx, services, c, acct)
    if cured:
        seen = {t.get("transfer_id") for t in transfers}
        more = [t for t in _transfers_into(conn, c["account_id"], int(c["issued_ts"])) if t["transfer_id"] not in seen]
        funded = sum(t["amount_c"] for t in more)
        conn.execute("UPDATE margin_calls SET status='MET', met_ts=?, transfers_json=?, funded_c=funded_c+? WHERE call_id=?",
                     (ctx.ts, json.dumps(transfers + more), funded, call_id))
        _resolve_call_attention(ctx, call_id)
        ctx.event("MARGIN_CALL_MET", account_id=c["account_id"], ref_type="margin_call", ref_id=call_id, funded_c=funded,
                  transfers=[t["transfer_id"] for t in more])
        return {"status": "DONE", "note": f"{call_id} met" + (f" (cash moved in: {fmt_money(funded)})" if funded else ""), "posted": False}
    if ctx.ts >= int(c["due_ts"]):
        conn.execute("UPDATE margin_calls SET status='UNMET', unmet_ts=? WHERE call_id=?", (ctx.ts, call_id))
        _call_attention(ctx, call_id, c["account_id"], c["kind"], c["counterparty"], int(c["amount_c"]), int(c["due_ts"]), c["note"] or "", unmet=True)
        ctx.event("MARGIN_CALL_UNMET", account_id=c["account_id"], ref_type="margin_call", ref_id=call_id)
        return {"status": "DONE", "note": f"{call_id} UNMET: due {ny_of(int(c['due_ts'])).strftime('%Y-%m-%d %H:%M ET')} passed", "posted": False}
    return {"status": "SKIPPED", "quiet": True, "note": f"{call_id} open: {info}", "posted": False}


class MarginCallJob(_JobBase):
    """MARGIN_CALL (module docstring PART B, MARGIN CALLS): issue keys ``{account}:{date}`` per US Exchange / prime-broker account and NYSE
    business day from 17:20 ET (never for a day whose 17:20 came before this release first bound: new jobs start forward), and re-check
    keys ``{account}:call:{call_id}`` (OPEN calls) / ``{account}:cure:{call_id}`` (UNMET calls whose attention is still open)."""
    name = "MARGIN_CALL"
    kind = "lifecycle.margin_call"
    when = "17:20 ET on NYSE business days per US Exchange / prime-broker account; open calls re-checked until met or unmet"
    module = "collateral"

    def due(self, conn, clock, cal, sessions) -> List[str]:
        if not phase2_ready(conn):
            return []
        start = _call_start_ts(conn)
        keys: List[str] = []
        if start is not None:
            now = int(clock.epoch())
            dates = call_dates(cal, now, start)
            if dates:
                from .lifecycle_jobs import open_accounts
                for a in open_accounts(conn):
                    if _role_of(a) not in CALL_ROLES:
                        continue
                    for d in dates:
                        if int(a["opened_ts"] or 0) > epoch_of(d, MARGIN_CALL_ET):
                            continue
                        keys.append(f"{a['account_id']}:{d.isoformat()}")
        for r in conn.execute("SELECT call_id, account_id FROM margin_calls WHERE status='OPEN' ORDER BY call_id").fetchall():
            keys.append(f"{r['account_id']}:call:{r['call_id']}")
        for r in conn.execute("SELECT m.call_id, m.account_id FROM margin_calls m WHERE m.status='UNMET' AND m.cured_ts IS NULL ORDER BY m.call_id").fetchall():
            keys.append(f"{r['account_id']}:cure:{r['call_id']}")
        return self.filter_done(conn, keys)

    def series_of(self, key: str) -> Optional[str]:
        parts = key.rsplit(":", 2)
        if len(parts) == 3 and parts[1] in ("call", "cure"):
            return None
        return key.rsplit(":", 1)[0]

    def run(self, ctx: TxContext, key: str, services) -> dict:
        parts = key.rsplit(":", 2)
        if len(parts) == 3 and parts[1] in ("call", "cure"):
            return check_call(ctx, services, parts[2], cure_only=parts[1] == "cure")
        aid, ds = key.rsplit(":", 1)
        return issue_calls(ctx, services, aid, ds)


def _call_view(conn, r, names: Dict[str, str]) -> dict:
    d = dict(r)
    try:
        d["transfers"] = json.loads(d.pop("transfers_json") or "[]")
    except ValueError:
        d["transfers"] = []
    try:
        d["detail"] = json.loads(d.pop("detail_json") or "{}")
    except ValueError:
        d["detail"] = {}
    d["kind_label"] = CALL_KIND_LABEL.get(d["kind"], d["kind"])
    d["account_name"] = names.get(d["account_id"], d["account_id"])
    d["due"] = ny_of(int(d["due_ts"])).strftime("%Y-%m-%d %H:%M ET")
    d["issued"] = ny_of(int(d["issued_ts"])).strftime("%Y-%m-%d %H:%M ET")
    d["basis"] = (d["detail"] or {}).get("basis")
    d["status_label"] = {"OPEN": "open - not funded yet", "MET": "met", "UNMET": "unmet at its due time" + (" (covered since)" if d.get("cured_ts") else "")}.get(d["status"], d["status"])
    d.pop("idem_key", None)
    return d


def margin_calls_rows(services, scope, from_=None, to=None, status: Optional[str] = None, limit: int = 500) -> List[dict]:
    """C16: the scope's margin calls (business date between ``from_`` and ``to`` when given; newest first): issued, counterparty, amount,
    due, MET / OPEN / UNMET, the funding transfers and the basis - P3's Activity "margin calls" type and the Cash & Collateral / Accounts
    lists read it."""
    with _reader_of(services) as conn:
        if not _table_exists(conn, "margin_calls"):
            return []
        lid, accts = _scope_accounts(conn, scope)
        ids = [a["account_id"] for a in accts]
        if not ids:
            return []
        names = {a["account_id"]: a["name"] for a in accts}
        q = ",".join("?" for _ in ids)
        sql, p = f"SELECT * FROM margin_calls WHERE account_id IN ({q})", list(ids)
        if from_:
            sql += " AND business_date >= ?"
            p.append(_iso(from_))
        if to:
            sql += " AND business_date <= ?"
            p.append(_iso(to))
        if status:
            sql += " AND status=?"
            p.append(str(status).upper())
        rows = conn.execute(sql + " ORDER BY issued_ts DESC, call_id DESC LIMIT ?", p + [int(limit)]).fetchall()
        return [_call_view(conn, r, names) for r in rows]


# ---- C16: the summaries ----------------------------------------------------------------------------------------------------------
def _futures_im(conn, master, account_id: str) -> Tuple[int, int, List[dict]]:
    """(required, posted, rows) of the account's futures initial margin: |qty| x the per-contract rate (the owner's override, else the
    static table: ``accounting.futures_rates_c``) vs the 1300 balance per contract."""
    from .accounting import futures_rates_c
    req = posted = 0
    rows = []
    for p in conn.execute("SELECT p.instrument_id, p.qty FROM v_positions p JOIN instruments i ON i.instrument_id=p.instrument_id WHERE p.account_id=?"
                          " AND i.kind='FUTURE' ORDER BY p.instrument_id", (account_id,)).fetchall():
        inst = _inst(conn, master, p["instrument_id"])
        try:
            im_per = int(futures_rates_c(conn, inst.obj if inst is not None and inst.obj is not None else inst)[0]) if inst is not None else 0
        except Exception:   # noqa: BLE001 - a contract without a table row: the posted figure stands alone
            im_per = 0
        r = abs(int(p["qty"])) * im_per
        po = balance(conn, account_id, "1300", "USD", p["instrument_id"])
        req += r
        posted += po
        rows.append({"instrument_id": p["instrument_id"], "symbol": inst.symbol if inst else p["instrument_id"], "qty": int(p["qty"]), "im_per_contract_c": im_per,
                     "required_c": r, "posted_c": po})
    extra = balance(conn, account_id, "1300", "USD") - posted          # IM still posted on a contract that is flat (released at its next fill)
    return req, posted + extra, rows


def _otc_im(conn, account_id: str) -> Tuple[int, int]:
    if not _table_exists(conn, "otc_contracts"):
        return 0, 0
    req = int(conn.execute("SELECT COALESCE(SUM(im_c), 0) FROM otc_contracts WHERE account_id=? AND status='OPEN'", (account_id,)).fetchone()[0] or 0)
    return req, balance(conn, account_id, "1330", "USD")


def _pledge_rows(services, conn, account_ids: List[str], snapshot, status: str = "PLEDGED") -> List[dict]:
    if not account_ids or not _table_exists(conn, "collateral_pledges"):
        return []
    q = ",".join("?" for _ in account_ids)
    on = _today(services)
    rows = conn.execute(f"SELECT * FROM collateral_pledges WHERE (account_id IN ({q}) OR pledged_to IN ({q}))" + (" AND status=?" if status else "")
                        + " ORDER BY pledge_id", list(account_ids) * 2 + ([status] if status else [])).fetchall()
    return [_pledge_view(services, conn, r, snapshot, on) for r in rows]


def _repo_pledged_mv(services, conn, account_ids: List[str], snapshot) -> int:
    """Market value of the Treasuries pledged in the scope's open repos (``repos`` side REPO), when the repo tables exist."""
    if not account_ids or not _table_exists(conn, "repos"):
        return 0
    cols = {r[1] for r in conn.execute("PRAGMA table_info(repos)").fetchall()}
    if "collateral_account" not in cols:
        return 0
    q = ",".join("?" for _ in account_ids)
    total = 0
    on = _today(services)
    for r in conn.execute(f"SELECT instrument_id, face, collateral_account FROM repos WHERE side='REPO' AND status='OPEN' AND collateral_account IN ({q})",
                          account_ids).fetchall():
        inst = _inst(conn, getattr(services, "instruments", None), r["instrument_id"])
        if inst is None:
            continue
        v = _bond_value(services, conn, inst, int(r["face"]), snapshot, on)
        total += int(v["value_c"] or 0)
    return total


def _lent_mv(services, conn, account_ids: List[str], snapshot) -> Tuple[int, List[dict]]:
    try:
        from .lending_accruals import seclend_tables_ready, sl_outstanding
    except Exception:   # noqa: BLE001
        return 0, []
    if not account_ids or not seclend_tables_ready(conn):
        return 0, []
    q = ",".join("?" for _ in account_ids)
    total, rows = 0, []
    for r in conn.execute(f"SELECT * FROM sec_loans WHERE status='OPEN' AND account_id IN ({q}) ORDER BY loan_id", account_ids).fetchall():
        inst = _inst(conn, getattr(services, "instruments", None), r["instrument_id"])
        qty = int(sl_outstanding(r))
        price, basis, _q = position_price(conn, snapshot, r["account_id"], r["instrument_id"], qty, 0, inst)
        mv = None if price is None or inst is None else inst.value_c(price, qty)
        total += int(mv or 0)
        rows.append({"loan_id": r["loan_id"], "account_id": r["account_id"], "agent": r["agent"], "instrument_id": r["instrument_id"], "qty": qty, "mv_c": mv,
                     "collateral_c": int(r["collateral_c"] or 0), "price_basis": basis})
    return total, rows


def _account_margin(services, conn, a: dict, snapshot, master) -> dict:
    """One account's margin components (required vs posted / eligible), collateral and restricted figures."""
    aid, role = a["account_id"], _role_of(a)
    comps: List[dict] = []
    fut_req, fut_posted, fut_rows = _futures_im(conn, master, aid)
    if fut_req or fut_posted:
        comps.append({"key": "futures_im", "label": "futures initial margin", "counterparty": FCM_NAME, "required_c": fut_req, "posted_c": fut_posted,
                      "excess_c": fut_posted - fut_req, "basis": "contracts x initial margin per contract (Settings > Margins & limits, else the static table)",
                      "rows": fut_rows})
    opt_req = _options_reserved_c(conn, aid)
    cash = free_cash_parts(conn, aid, "USD")
    if opt_req:
        cover = max(0, min(opt_req, cash["settled_c"] + cash["receivable_c"] - cash["payable_c"] - cash["restricted_c"]))
        comps.append({"key": "options", "label": "short-option margin (reserved from cash)", "counterparty": "OCC via the clearing broker", "required_c": opt_req,
                      "posted_c": cover, "excess_c": cover - opt_req, "basis": "the short-option requirement is reserved from the account's own cash; nothing moves"})
    if role == "PB":
        im_req, im_posted = _otc_im(conn, aid)
        if im_req or im_posted:
            comps.append({"key": "otc_im", "label": "OTC initial margin (CSA)", "counterparty": f"{_broker_name(a)} (CSA)", "required_c": im_req, "posted_c": im_posted,
                          "excess_c": im_posted - im_req, "basis": "the open contracts' initial margin under the prime broker's schedule (assumed terms)"})
    regt = None
    if role in REGT_ROLES:
        regt = regt_view(conn, aid, snapshot, master, a)
        if regt["requirement_c"] or regt["short_mv_c"]:
            comps.append({"key": "regt", "label": "Reg T (physical shorts)", "counterparty": _regt_counterparty(conn, a)[0], "required_c": regt["requirement_c"],
                          "posted_c": regt["eligible_c"], "excess_c": regt["excess_c"],
                          "basis": f"{regt['terms']['initial_text']} of the short value vs the longs' loan value + free cash (eligible collateral, not cash posted)"})
    rp = restricted_parts(conn, aid, "USD") if schema_ready(conn) else {"restricted_c": 0, "vm_received_c": 0, "vm_posted_c": 0, "csa_net_c": 0, "seclend_cash_c": 0}
    out = {"account_id": aid, "name": a["name"], "role": role, "custodian": custodian_of(a), "components": comps,
           "margin_required_c": sum(c["required_c"] for c in comps), "margin_posted_c": sum(c["posted_c"] for c in comps),
           "margin_excess_c": sum(c["posted_c"] for c in comps) - sum(c["required_c"] for c in comps),
           "restricted_c": int(rp["restricted_c"]), "restricted": rp, "vm_posted_c": balance(conn, aid, "1320", "USD"), "vm_received_c": balance(conn, aid, "2330", "USD"),
           "borrow_collateral_c": balance(conn, aid, "1400", "USD"), "short_proceeds_c": balance(conn, aid, "2600", "USD"),
           "seclend_cash_received_c": max(0, balance(conn, aid, "2520", "USD")), "settled_c": cash["settled_c"], "regt": regt,
           "margin_reserved_c": int(regt["margin_cash_c"]) if regt is not None and _has_physical_short(conn, aid) else 0}
    return out


def summary(services, scope, snapshot=None) -> dict:
    """C16: ``{margin_required_c, margin_posted_c, margin_excess_c, collateral_posted_c, collateral_received_c, restricted_c, encumbered_c,
    open_calls, failed_settlements, by_counterparty[]}`` of the scope (an account, ``{lifetime}:ALL``, a lifetime or a book), plus the
    detail its consumers show: ``by_account`` (margin components with required vs posted / eligible, restricted, Reg T), ``regt`` (the
    Reg T rows), ``components`` and ``definitions``.  Margin "posted" counts cash posted to the FCM / the dealer, the short-option
    reservation covered by the account's cash and the Reg T eligible collateral (the longs' loan value + free cash); collateral counts
    variation margin posted / received, the stock lenders' cash collateral, securities-lending cash collateral received and CSA-pledged
    securities at their eligible value; encumbered = CSA-pledged and repo-pledged Treasuries and shares out on loan, at market."""
    master = getattr(services, "instruments", None)
    with _reader_of(services) as conn:
        lid, accts = _scope_accounts(conn, scope)
        accts = [a for a in accts if a["status"] == "OPEN"]
        ids = [a["account_id"] for a in accts]
        snap = _snapshot_of(services, snapshot, ids=_scope_ids(conn, ids))
        rows = [_account_margin(services, conn, a, snap, master) for a in accts]
        pledges = _pledge_rows(services, conn, ids, snap)
        pledged_elig = sum(int(p["eligible_c"] or 0) for p in pledges if p["account_id"] in ids or p["pledged_to"] in ids)
        pledged_mv = sum(int(p["market_value_c"] or 0) for p in pledges if p["account_id"] in ids)
        repo_mv = _repo_pledged_mv(services, conn, ids, snap)
        lent_mv, lent_rows = _lent_mv(services, conn, ids, snap)
        open_calls = unmet = 0
        open_calls_c = 0
        if _table_exists(conn, "margin_calls") and ids:
            q = ",".join("?" for _ in ids)
            r = conn.execute(f"SELECT COUNT(*), COALESCE(SUM(amount_c), 0) FROM margin_calls WHERE status='OPEN' AND account_id IN ({q})", ids).fetchone()
            open_calls, open_calls_c = int(r[0] or 0), int(r[1] or 0)
            unmet = int(conn.execute(f"SELECT COUNT(*) FROM margin_calls WHERE status='UNMET' AND cured_ts IS NULL AND account_id IN ({q})", ids).fetchone()[0] or 0)
        failed = 0
        if has_fail_columns(conn) and ids:
            q = ",".join("?" for _ in ids)
            failed = int(conn.execute(f"SELECT COUNT(*) FROM settlements WHERE status='PENDING' AND fail_state='FAILED' AND account_id IN ({q})", ids).fetchone()[0] or 0)
        cps: Dict[str, dict] = {}

        def cp(name: str, typ: str) -> dict:
            return cps.setdefault(f"{typ}:{name}", {"counterparty": name, "type": typ, "accounts": [], "cash_posted_c": 0, "securities_posted_c": 0,
                                                    "cash_received_c": 0, "im_posted_c": 0, "im_required_c": 0, "vm_posted_c": 0, "vm_received_c": 0,
                                                    "net_vm_c": 0, "requirement_c": 0, "eligible_c": 0, "exposure_c": 0, "notes": []})
        for a, r in zip(accts, rows):
            aid = a["account_id"]
            for c in r["components"]:
                if c["key"] == "futures_im":
                    g = cp(FCM_NAME, "FCM")
                    g["im_posted_c"] += c["posted_c"]
                    g["im_required_c"] += c["required_c"]
                    g["cash_posted_c"] += c["posted_c"]
                    g["requirement_c"] += c["required_c"]
                    g["eligible_c"] += c["posted_c"]
                    g["accounts"].append(aid)
            if r["role"] == "PB" and (r["vm_posted_c"] or r["vm_received_c"] or any(c["key"] == "otc_im" for c in r["components"]) or
                                      any(p["pledged_to"] == aid for p in pledges)):
                g = cp(f"{_broker_name(a)}", "CSA")
                im_req, im_posted = _otc_im(conn, aid)
                sec = sum(int(p["eligible_c"] or 0) for p in pledges if p["pledged_to"] == aid)
                g.update(account_id=aid, reuse_allowed=reuse_allowed(conn, aid))
                g["accounts"].append(aid)
                g["im_posted_c"] += im_posted
                g["im_required_c"] += im_req
                g["vm_posted_c"] += r["vm_posted_c"]
                g["vm_received_c"] += r["vm_received_c"]
                g["net_vm_c"] += r["vm_received_c"] - r["vm_posted_c"]
                g["cash_posted_c"] += im_posted + r["vm_posted_c"]
                g["cash_received_c"] += r["vm_received_c"]
                g["securities_posted_c"] += sec
                g["requirement_c"] += im_req
                g["eligible_c"] += im_posted + sec
                g["notes"].append("one CSA per prime broker: variation margin nets across its contracts; received VM net of posted is restricted")
        if _table_exists(conn, "borrows") and ids:
            q = ",".join("?" for _ in ids)
            try:
                from .lending import lender_label
            except Exception:   # noqa: BLE001
                def lender_label(x):
                    return x
            for b in conn.execute(f"SELECT * FROM borrows WHERE status='OPEN' AND account_id IN ({q}) ORDER BY borrow_id", ids).fetchall():
                g = cp(lender_label(b["lender"]), "STOCK_LENDER")
                coll = int(b["collateral_c"] or 0)
                g["cash_posted_c"] += coll
                g["eligible_c"] += coll
                inst = _inst(conn, master, b["instrument_id"])
                qty = int(b["qty"]) - int(b["qty_returned"] or 0) if "qty_returned" in b.keys() else int(b["qty"])
                price, _basis, _q = position_price(conn, snap, b["account_id"], b["instrument_id"], -qty, 0, inst)
                mv = None if price is None or inst is None else inst.value_c(price, qty)
                g["exposure_c"] += int(mv or 0)
                g["requirement_c"] += 0 if mv is None else to_c(from_c(mv) * SHORT_COLLATERAL_PCT)
                if b["account_id"] not in g["accounts"]:
                    g["accounts"].append(b["account_id"])
        for lr in lent_rows:
            g = cp(str(lr["agent"] or "agent lender"), "AGENT_LENDER")
            g["cash_received_c"] += int(lr["collateral_c"])
            g["exposure_c"] += int(lr["mv_c"] or 0)
            if lr["account_id"] not in g["accounts"]:
                g["accounts"].append(lr["account_id"])
        tot = lambda k: sum(int(r[k] or 0) for r in rows)   # noqa: E731
        out = {"scope": str(scope), "lifetime_id": lid, "as_of": _now_ts(services),
               "margin_required_c": tot("margin_required_c"), "margin_posted_c": tot("margin_posted_c"), "margin_excess_c": tot("margin_excess_c"),
               "collateral_posted_c": tot("vm_posted_c") + tot("borrow_collateral_c") + pledged_elig,
               "collateral_received_c": tot("vm_received_c") + tot("seclend_cash_received_c"),
               "restricted_c": tot("restricted_c"), "margin_reserved_c": tot("margin_reserved_c"),
               "encumbered_c": pledged_mv + repo_mv + lent_mv, "encumbered_parts": {"csa_pledged_c": pledged_mv, "repo_pledged_c": repo_mv, "lent_c": lent_mv},
               "open_calls": open_calls, "open_calls_c": open_calls_c, "unmet_calls": unmet, "failed_settlements": failed,
               "by_counterparty": sorted(cps.values(), key=lambda g: (g["type"], g["counterparty"])),
               "by_account": rows, "regt": [r["regt"] for r in rows if r["regt"] is not None],
               "pledges": len(pledges),
               "definitions": {"margin": "required vs posted / eligible: futures initial margin at the FCM, the short-option reservation covered by own cash, OTC"
                                         " initial margin under the CSA and Reg T (eligible collateral = the longs' loan value + free cash)",
                               "collateral": "posted: variation margin posted, the stock lenders' cash collateral, CSA-pledged securities at their eligible value;"
                                             " received: variation margin received and cash collateral for shares lent",
                               "restricted": RESTRICTED_TEXT, "regt": REGT_TEXT, "calls": CALLS_TEXT, "pledges": PLEDGE_TEXT,
                               "encumbered": "CSA-pledged and repo-pledged Treasuries and shares out on loan, at market"}}
    return out


def position_requirements(services, account_id: str) -> Dict[str, dict]:
    """C16: ``{instrument_id: {margin_c, collateral_c, basis}}`` of the account's positions (P3's position rows): a physical short -> its
    Reg T part (initial % of its value) and the lender's cash collateral (1400); a long US equity -> no margin, collateral 0 (it counts
    toward the Reg T eligible collateral at its loan value, said in the basis); a future -> initial margin required vs posted (1300); a
    written option -> its share of the account's short-option reservation when known; an OTC contract -> its initial margin and the
    variation margin net posted (1320 - 2330); a Treasury -> the CSA-pledged eligible value."""
    master = getattr(services, "instruments", None)
    out: Dict[str, dict] = {}
    with _reader_of(services) as conn:
        a = _account_row_dict(conn, account_id)
        if a is None:
            raise RejectError("ACCOUNT_UNKNOWN", f"account {account_id} does not exist")
        snap = _snapshot_of(services, ids=_scope_ids(conn, [account_id]))
        regt = regt_view(conn, account_id, snap, master, a) if _role_of(a) in REGT_ROLES else None
        rpos = {p["instrument_id"]: p for p in (regt or {}).get("positions", [])}
        from .accounting import futures_rates_c
        opt_detail = {}
        if _table_exists(conn, "margin_state"):
            ms = conn.execute("SELECT options_detail_json FROM margin_state WHERE account_id=?", (account_id,)).fetchone()
            try:
                opt_detail = json.loads(ms["options_detail_json"] or "{}") if ms is not None else {}
            except ValueError:
                opt_detail = {}
        per_option = _option_margin_by_instrument(opt_detail)
        for p in conn.execute("SELECT instrument_id, qty, cost_c FROM v_positions WHERE account_id=? ORDER BY instrument_id", (account_id,)).fetchall():
            iid, qty = p["instrument_id"], int(p["qty"])
            inst = _inst(conn, master, iid)
            kind = inst.kind if inst else ""
            if kind in REGT_KINDS and iid in rpos:
                r = rpos[iid]
                if qty < 0:
                    coll = balance(conn, account_id, "1400", "USD", iid)
                    out[iid] = {"margin_c": int(r["requirement_c"]), "collateral_c": coll,
                                "basis": f"Reg T {regt['terms']['initial_text']} of the short value {fmt_money(int(r['mv_c'] or 0))} ({r['price_basis']}); the lender holds"
                                         f" {fmt_money(coll)} of cash collateral (102 %, marked daily)"}
                else:
                    out[iid] = {"margin_c": 0, "collateral_c": 0,
                                "basis": f"fully paid: counts toward the Reg T eligible collateral at {regt['terms']['long_loan_value_text']} (loan value"
                                         f" {fmt_money(int(r['loan_value_c']))})" + (f"; {r['lent_qty']:,} out on loan" if r.get("lent_qty") else "")}
            elif kind == "FUTURE":
                try:
                    im_per = int(futures_rates_c(conn, inst.obj if inst.obj is not None else inst)[0])
                except Exception:   # noqa: BLE001
                    im_per = 0
                posted = balance(conn, account_id, "1300", "USD", iid)
                out[iid] = {"margin_c": abs(qty) * im_per, "collateral_c": posted,
                            "basis": f"initial margin {abs(qty):,} x {fmt_money(im_per)} (assumed table / your override); posted {fmt_money(posted)} at the FCM;"
                                     " variation margin settles daily in cash"}
            elif kind == "OPTION":
                if qty > 0:
                    out[iid] = {"margin_c": 0, "collateral_c": 0, "basis": "a bought option is paid in full: no margin"}
                else:
                    share = per_option.get(iid)
                    out[iid] = {"margin_c": share, "collateral_c": 0,
                                "basis": ("its part of the account's short-option requirement (reserved from cash; spreads offset)" if share is not None
                                          else f"the short-option requirement is computed per account (spreads offset): {fmt_money(_options_reserved_c(conn, account_id))}"
                                               " reserved in total")}
            elif kind == "BOND":
                pledged = csa_pledged_face(conn, account_id, iid)
                if pledged:
                    rows = [x for x in _pledge_rows(services, conn, [account_id], snap) if x["instrument_id"] == iid and x["account_id"] == account_id]
                    elig = sum(int(x["eligible_c"] or 0) for x in rows)
                    out[iid] = {"margin_c": 0, "collateral_c": elig, "basis": f"{pledged:,} face pledged under a CSA (eligible value {fmt_money(elig)} after the haircut)"}
                else:
                    out[iid] = {"margin_c": 0, "collateral_c": 0, "basis": "held unencumbered" if not _repo_pledged(conn, account_id, iid) else "pledged in a repo"}
            elif kind not in ("SWAP",):
                out[iid] = {"margin_c": 0, "collateral_c": 0, "basis": "no margin requirement in this simulator"}
        if _table_exists(conn, "otc_contracts"):
            for c in conn.execute("SELECT contract_id, kind, im_c, margin_mode FROM otc_contracts WHERE account_id=? AND status='OPEN' ORDER BY contract_id",
                                  (account_id,)).fetchall():
                cid = c["contract_id"]
                vm_net = balance(conn, account_id, "1320", "USD", cid) - balance(conn, account_id, "2330", "USD", cid)
                out[cid] = {"margin_c": int(c["im_c"] or 0), "collateral_c": vm_net,
                            "basis": f"initial margin {fmt_money(int(c['im_c'] or 0))} (the prime broker's schedule); variation margin "
                                     + (f"posted {fmt_money(vm_net)}" if vm_net > 0 else (f"received {fmt_money(-vm_net)}" if vm_net < 0 else "0.00"))
                                     + (" (collateral, not P&L)" if c["margin_mode"] == "CTM" else " (settled to market)")}
    return out


def _repo_pledged(conn, account_id: str, iid: str) -> bool:
    try:
        from . import repo as _repo
        return int(_repo.pledged_face(conn, account_id, iid) or 0) > csa_pledged_face(conn, account_id, iid)
    except Exception:   # noqa: BLE001
        return False


def _option_margin_by_instrument(detail: dict) -> Dict[str, int]:
    """Best effort: the per-instrument requirement the options module recorded in ``margin_state.options_detail_json`` (its groups list the
    shorts with their requirement); {} when the detail does not carry it."""
    out: Dict[str, int] = {}
    for g in (detail or {}).get("groups") or []:
        for s in (g or {}).get("shorts") or (g or {}).get("legs") or []:
            iid = (s or {}).get("instrument_id")
            req = (s or {}).get("requirement_c")
            if iid and isinstance(req, int):
                out[iid] = out.get(iid, 0) + int(req)
    return out


def pledges_view(services, scope) -> dict:
    """C16 / C6 probe: the scope's CSA pledges (account, instrument, face, haircut, eligible value, pledged to, status) - PLEDGED first, then
    the released ones - with the totals, the eligible Treasuries that could be pledged and the CSA terms of each prime broker."""
    with _reader_of(services) as conn:
        lid, accts = _scope_accounts(conn, scope)
        ids = [a["account_id"] for a in accts]
        snap = _snapshot_of(services, ids=_scope_ids(conn, ids))
        rows = _pledge_rows(services, conn, ids, snap, status="")
        pbs = [a for a in accts if a["kind"] == "PB" and a["status"] == "OPEN"]
        terms = []
        for pb in pbs:
            try:
                from .prime import read_schedule
                s = read_schedule(conn, pb["account_id"]).to_dict()
            except Exception:   # noqa: BLE001
                s = {}
            terms.append({"account_id": pb["account_id"], "name": pb["name"], "securities": s.get("csa_securities"), "bill_haircut": s.get("csa_bill_haircut"),
                          "bond_haircut": s.get("csa_bond_haircut"), "reuse_allowed": reuse_allowed(conn, pb["account_id"])})
        eligible_holdings = []
        master = getattr(services, "instruments", None)
        if pbs:
            from . import repo as _repo
            for a in accts:
                if a["status"] != "OPEN":
                    continue
                for p in conn.execute("SELECT p.instrument_id, p.qty FROM v_positions p JOIN instruments i ON i.instrument_id=p.instrument_id WHERE p.account_id=?"
                                      " AND i.kind='BOND' AND p.qty > 0 ORDER BY p.instrument_id", (a["account_id"],)).fetchall():
                    inst = _inst(conn, master, p["instrument_id"])
                    t = _bond_type(inst)
                    if t not in PLEDGE_TYPES or inst.currency != "USD":
                        continue
                    held, unsettled, _due = _repo.settled_face(conn, a["account_id"], p["instrument_id"])
                    pledged = int(_repo.pledged_face(conn, a["account_id"], p["instrument_id"]) or 0)
                    eligible_holdings.append({"account_id": a["account_id"], "account_name": a["name"], "instrument_id": p["instrument_id"], "symbol": inst.symbol,
                                              "name": inst.name, "type": t, "face": held, "unsettled": unsettled, "pledged": pledged,
                                              "free": max(0, held - unsettled - pledged)})
    live = [r for r in rows if r["status"] == "PLEDGED"]
    return {"lifetime_id": lid, "rows": sorted(rows, key=lambda r: (r["status"] != "PLEDGED", r["pledge_id"])),
            "pledged_face": sum(r["face"] for r in live), "market_value_c": sum(int(r["market_value_c"] or 0) for r in live),
            "eligible_c": sum(int(r["eligible_c"] or 0) for r in live), "terms": terms, "eligible_holdings": eligible_holdings,
            "definition": PLEDGE_TEXT, "note": "releasing cash initial margin against pledged securities is not in this version"}


def treasury_securities(services, scope) -> dict:
    """PORTFOLIO-09: the US Treasury bills held in the scope's accounts as cash equivalents: face, market value at the bond mark, maturity,
    days to maturity, cost and the purchase discount accreted straight-line to today (display only), pledged face."""
    master = getattr(services, "instruments", None)
    today = _today(services)
    rows = []
    with _reader_of(services) as conn:
        lid, accts = _scope_accounts(conn, scope)
        snap = _snapshot_of(services, ids=_scope_ids(conn, [a["account_id"] for a in accts]))
        for a in accts:
            if a["status"] != "OPEN":
                continue
            for p in conn.execute("SELECT p.instrument_id, p.qty, p.cost_c FROM v_positions p JOIN instruments i ON i.instrument_id=p.instrument_id"
                                  " WHERE p.account_id=? AND i.kind='BOND' AND p.qty > 0 ORDER BY p.instrument_id", (a["account_id"],)).fetchall():
                inst = _inst(conn, master, p["instrument_id"])
                if _bond_type(inst) != "BILL":
                    continue
                face, cost = int(p["qty"]), int(p["cost_c"] or 0)
                v = _bond_value(services, conn, inst, face, snap, today)
                mat = (inst.spec or {}).get("maturity") or inst.expiry
                try:
                    mat_d = date.fromisoformat(str(mat)[:10]) if mat else None
                except ValueError:
                    mat_d = None
                first = conn.execute("SELECT MIN(trade_date) d FROM fills WHERE account_id=? AND instrument_id=? AND side='BUY'", (a["account_id"], p["instrument_id"])).fetchone()
                start = date.fromisoformat(first["d"]) if first is not None and first["d"] else None
                par_c = inst.value_c(Decimal(100), face)
                accreted = None
                if mat_d and start and mat_d > start:
                    days = (min(today, mat_d) - start).days
                    accreted = to_c(from_c(par_c - cost) * Decimal(max(0, days)) / Decimal((mat_d - start).days))
                rows.append({"account_id": a["account_id"], "account_name": a["name"], "instrument_id": p["instrument_id"], "symbol": inst.symbol, "name": inst.name,
                             "face": face, "cost_c": cost, "par_c": par_c, "market_value_c": v["value_c"], "price": v["dirty"], "price_basis": v["basis"],
                             "price_note": v["note"], "maturity": None if mat_d is None else mat_d.isoformat(),
                             "days_to_maturity": None if mat_d is None else (mat_d - today).days, "accreted_discount_c": accreted,
                             "pledged_face": csa_pledged_face(conn, a["account_id"], p["instrument_id"])})
    total = sum(int(r["market_value_c"] or 0) for r in rows)
    return {"lifetime_id": lid, "rows": rows, "market_value_c": total, "face": sum(r["face"] for r in rows), "definition": TBILL_TEXT,
            "line": ("Treasury securities: none" if not rows else f"Treasury securities (cash equivalents): {len(rows)} bill position(s), face "
                     f"{sum(r['face'] for r in rows):,}, market value {fmt_money(total)}")}


# ---- the Accounts page and the Cash & Collateral page ---------------------------------------------------------------------------
INCOME_TYPES = [
    ("cash_interest", "Treasury cash interest", ("4300",), "TREASURY", 1),
    ("pb_credit", "PB credit interest", ("4300",), "PB", 1),
    ("rebates", "stock-borrow rebates earned", ("4350",), None, 1),
    ("borrow_fees", "stock-borrow fees", ("5300",), None, -1),
    ("seclend_fees", "securities-lending fees earned", ("4360",), None, 1),
    ("seclend_rebates", "rebates paid on collateral received", ("5350",), None, -1),
    ("pb_financing", "PB financing interest", ("5100",), None, -1),
    ("loan_interest", "currency-loan interest", ("5150",), None, -1),
    ("repo_interest", "repo interest paid", ("5500",), None, -1),
    ("reverse_repo", "reverse-repo interest earned", ("4500",), None, 1),
    ("trs_financing", "TRS financing leg", ("4451",), None, -1),
    ("swap_coupons", "swap coupons and CDS premium", ("4453",), None, -1),
    ("div_equivalents", "dividend equivalents (TRS)", ("4452",), None, 1),
    ("pil", "payments in lieu", ("5400",), None, -1),
    ("buy_in", "buy-in penalties", ("5700",), None, -1),
]


def income_by_type(conn, accts: List[dict], today: date) -> dict:
    """Interest and financing of the accounts by type and period (business dates): today, month to date, since inception; income positive,
    costs negative (natural income minus natural expense)."""
    ids = [a["account_id"] for a in accts]
    roles = {a["account_id"]: _role_of(a) for a in accts}
    month0 = today.replace(day=1).isoformat()
    out = []
    if not ids:
        return {"rows": [], "periods": ["today", "mtd", "itd"]}
    q = ",".join("?" for _ in ids)
    for key, label, gls, role, sign in INCOME_TYPES:
        g = ",".join("?" for _ in gls)
        acc = [i for i in ids if role is None or roles[i] == role or (role == "PB" and roles[i] == "PB")]
        if not acc:
            continue
        qa = ",".join("?" for _ in acc)
        r = conn.execute(f"SELECT COALESCE(SUM(l.amount_c), 0) itd, COALESCE(SUM(CASE WHEN e.business_date >= ? THEN l.amount_c END), 0) mtd,"
                         f" COALESCE(SUM(CASE WHEN e.business_date = ? THEN l.amount_c END), 0) td FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id"
                         f" WHERE l.account_id IN ({qa}) AND l.gl IN ({g})", [month0, today.isoformat(), *acc, *gls]).fetchone()
        nat = lambda x: -int(x or 0)   # noqa: E731 - income (credit-natural) positive, expense (debit-natural) negative
        row = {"key": key, "label": label, "gls": list(gls), "today_c": nat(r["td"]), "mtd_c": nat(r["mtd"]), "itd_c": nat(r["itd"])}
        if any(row[k] for k in ("today_c", "mtd_c", "itd_c")) or key in ("cash_interest", "rebates", "borrow_fees"):
            out.append(row)
    return {"rows": out, "periods": ["today", "mtd", "itd"], "today": today.isoformat(), "month_start": month0,
            "note": "by business date; income positive, costs negative; accrued nightly, paid monthly (the payment moves no income)"}


def short_collateral_rows(services, conn, accts: List[dict], snapshot) -> dict:
    """Short proceeds (2600) vs the cash collateral at the lender (1400) per short position and in total, with the fund's own cash between
    them (1400 - 2600: the 2 % excess and the daily marks), the lenders and the short value at the view's marks."""
    master = getattr(services, "instruments", None)
    rows = []
    try:
        from .lending import lender_label
    except Exception:   # noqa: BLE001
        def lender_label(x):
            return x
    for a in accts:
        aid = a["account_id"]
        for p in conn.execute("SELECT DISTINCT instrument_id FROM gl_balances WHERE account_id=? AND gl IN ('2600','1400') AND balance_c <> 0 ORDER BY instrument_id",
                              (aid,)).fetchall():
            iid = p["instrument_id"]
            proceeds = balance(conn, aid, "2600", "USD", iid)
            coll = balance(conn, aid, "1400", "USD", iid)
            pos = conn.execute("SELECT qty, cost_c FROM v_positions WHERE account_id=? AND instrument_id=?", (aid, iid)).fetchone()
            qty = int(pos["qty"]) if pos is not None else 0
            inst = _inst(conn, master, iid)
            price, basis, _q = position_price(conn, snapshot, aid, iid, qty, int(pos["cost_c"] or 0) if pos is not None else 0, inst)
            mv = None if price is None or inst is None or not qty else inst.value_c(price, abs(qty))
            lenders = sorted({lender_label(b["lender"]) for b in conn.execute("SELECT lender FROM borrows WHERE account_id=? AND instrument_id=? AND status='OPEN'",
                                                                                   (aid, iid)).fetchall()})
            bids = [b["borrow_id"] for b in conn.execute("SELECT borrow_id FROM borrows WHERE account_id=? AND instrument_id=? AND status='OPEN' ORDER BY borrow_id",
                                                         (aid, iid)).fetchall()]
            rows.append({"account_id": aid, "account_name": a["name"], "instrument_id": iid, "symbol": inst.symbol if inst else iid, "qty": qty, "proceeds_c": proceeds,
                         "collateral_c": coll, "fund_cash_c": coll - proceeds, "short_mv_c": mv, "price": None if price is None else str(price), "price_basis": basis,
                         "lenders": lenders, "borrows": bids, "required_c": None if mv is None else to_c(from_c(mv) * SHORT_COLLATERAL_PCT)})
    tp, tc = sum(r["proceeds_c"] for r in rows), sum(r["collateral_c"] for r in rows)
    return {"rows": rows, "proceeds_c": tp, "collateral_c": tc, "fund_cash_c": tc - tp,
            "note": "the lender holds 102 % cash collateral, marked daily; the short sale's own proceeds fund most of it and the fund's cash the rest"}


def _ladder(conn, ids: List[str], today: date, cal) -> List[dict]:
    if not ids:
        return []
    q = ",".join("?" for _ in ids)
    out = []
    for s in conn.execute(f"SELECT * FROM settlements WHERE status='PENDING' AND account_id IN ({q}) ORDER BY due_date, settle_id LIMIT 300", ids).fetchall():
        v = settlement_view(conn, s)
        out.append({k: v.get(k) for k in ("settle_id", "account_id", "fill_id", "fx_deal_id", "leg", "currency", "amount_c", "due_date", "stage", "label", "chip",
                                          "fail_state", "fail_kind", "fail_reason", "instrument_id", "fill_side", "fill_qty", "ref_type", "ref_id")})
    return out


def _cash_rows(services, conn, accts: List[dict], snapshot) -> List[dict]:
    acc = getattr(services, "accounting", None)
    out = []
    for a in accts:
        if a["status"] != "OPEN":
            continue
        views = {}
        try:
            views = acc.cash(a["account_id"], snapshot=snapshot) if acc is not None else {}
        except Exception:   # noqa: BLE001 - one account's figures failing never hides the others
            views = {}
        for ccy, v in sorted(views.items()):
            d = v.to_dict() if hasattr(v, "to_dict") else dict(v)
            adj = {x["key"]: int(x["amount_c"]) for x in (d.get("adjustments") or [])}
            if ccy != "USD" and not any(int(d.get(k) or 0) for k in ("settled_c", "receivable_c", "payable_c")):
                continue
            out.append({"account_id": a["account_id"], "account_name": a["name"], "role": _role_of(a), "currency": ccy, "settled_c": int(d.get("settled_c") or 0),
                        "receivable_c": int(d.get("receivable_c") or 0), "payable_c": int(d.get("payable_c") or 0), "reserved_c": int(d.get("reserved_c") or 0),
                        "restricted_c": adj.get(ADJUSTER_KEY, 0), "margin_reserved_c": adj.get(MARGIN_ADJUSTER_KEY, 0),
                        "other_adjustments_c": sum(v2 for k2, v2 in adj.items() if k2 not in (ADJUSTER_KEY, MARGIN_ADJUSTER_KEY)),
                        "available_c": int(d.get("available_c") or 0), "overdrawn_c": int(d.get("overdrawn_c") or 0), "needs_c": int(d.get("needs_c") or 0),
                        "sweep_covers_c": int(d.get("sweep_covers_c") or 0), "unfunded_c": int(d.get("unfunded_c") or 0),
                        "held_at_lender_c": int(d.get("held_at_lender_c") or 0), "margin_c": int(d.get("margin_c") or 0), "otc_margin_c": int(d.get("otc_margin_c") or 0)})
    return out


def cash_collateral_view(services, scope) -> dict:
    """The Cash & Collateral page (UI-10, PORTFOLIO-16) in one read: cash by account and currency with the available / restricted / margin /
    reserved split, the settlement ladder, short proceeds vs the lenders' collateral, interest and financing by type and period,
    collateral by counterparty, margin per account with the Reg T terms, margin calls, pledges, Treasury bills and the assumptions."""
    cal = getattr(services, "cal", None)
    today = _today(services)
    with _reader_of(services) as conn:
        lid, accts = _scope_accounts(conn, scope)
        accts = [a for a in accts if a["status"] == "OPEN"]
        ids = [a["account_id"] for a in accts]
        snap = _snapshot_of(services, ids=_scope_ids(conn, ids))
        summ = summary(services, scope, snapshot=snap)
        cash = _cash_rows(services, conn, accts, snap)
        ladder = _ladder(conn, ids, today, cal)
        shorts = short_collateral_rows(services, conn, accts, snap)
        income = income_by_type(conn, accts, today)
        terms = []
        for a in accts:
            role = _role_of(a)
            if role in REGT_ROLES:
                t = margin_terms(conn, a["account_id"])
                row = {"account_id": a["account_id"], "name": a["name"], "role": role, "regt": _terms_json(t)}
                if role == "PB":
                    row["reuse_allowed"] = reuse_allowed(conn, a["account_id"])
                terms.append(row)
        fut_im = sum(balance(conn, i, "1300", "USD") for i in ids)
    calls = margin_calls_rows(services, scope, limit=100)
    pledges = pledges_view(services, scope)
    bills = treasury_securities(services, scope)
    return {"lifetime_id": lid, "as_of": _now_ts(services), "accounting_day": today.isoformat(), "cash": cash, "ladder": ladder, "shorts": shorts, "income": income,
            "futures_im_c": fut_im, "summary": summ, "calls": calls, "pledges": pledges, "treasury_securities": bills, "terms": terms,
            "definitions": dict(summ["definitions"], tbills=TBILL_TEXT)}


def accounts_view(services, scope) -> dict:
    """The Accounts page (UI-09, PORTFOLIO-18): every account of the book with NAV, cash, available, reserved, restricted, margin, working
    orders, settling, attention and custodian (the /api/accounts row of each, plus this module's figures), the custody statement and the
    open calls."""
    with _reader_of(services) as conn:
        lid, accts = _scope_accounts(conn, scope)
    base: Dict[str, dict] = {}
    try:
        from .api_views import account_rows
        base = {r["account_id"]: r for r in account_rows(services, lid)}
    except Exception:   # noqa: BLE001 - the accounts table alone (NAV etc. unavailable, said so)
        base = {}
    summ = summary(services, f"{lid}:ALL")
    margin = {r["account_id"]: r for r in summ["by_account"]}
    # the calls that still need the owner: OPEN ones and UNMET ones whose condition is not cured yet
    calls = [c for c in margin_calls_rows(services, f"{lid}:ALL", limit=200) if c["status"] == "OPEN" or (c["status"] == "UNMET" and not c.get("cured_ts"))]
    rows = []
    for a in accts:
        b = dict(base.get(a["account_id"]) or {k: a[k] for k in ("account_id", "name", "kind", "role", "status", "broker", "sort_order")})
        m = margin.get(a["account_id"]) or {}
        b.pop("settings", None)
        b.update(custodian=custodian_of(a), restricted_c=int(m.get("restricted_c") or 0), margin_required_c=int(m.get("margin_required_c") or 0),
                 margin_posted_c=int(m.get("margin_posted_c") or 0), margin_excess_c=int(m.get("margin_excess_c") or 0),
                 margin_reserved_c=int(m.get("margin_reserved_c") or 0), settled_usd_c=m.get("settled_c"),
                 regt_excess_c=None if not m.get("regt") or not m["regt"].get("requirement_c") else int(m["regt"]["excess_c"]),
                 open_calls=sum(1 for c in calls if c["account_id"] == a["account_id"]), nav_known=a["account_id"] in base,
                 kind_label={"TREASURY": "Treasury", "PB": "prime broker"}.get(_role_of(a), _role_of(a).replace("_", " ").lower()))
        rows.append(b)
    return {"lifetime_id": lid, "rows": rows, "custody": custody_statement(services, f"{lid}:ALL"), "open_calls": calls, "as_of": _now_ts(services),
            "note": "custodians and Reg T terms are the simulator's assumptions; prime-broker accounts are opened only through the onboarding flow"}


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
    bus.register("margin.terms", h_margin_terms, light=True)
    bus.register("collateral.pledge", h_pledge)
    bus.register("collateral.release", h_release)


def _mark_start(db, clock) -> Optional[int]:
    """Record, once per database, when this release first bound (``collateral_state`` START_KEY = the clock's epoch): the MARGIN_CALL job
    never issues a call for a business day whose 17:20 ET came before it (15.9 item 8)."""
    if clock is None:
        return None
    with db.writer() as tx:
        tx.conn.execute("INSERT OR IGNORE INTO collateral_state(key, value, ts) VALUES (?,?,?)", (START_KEY, str(int(clock.epoch())), int(clock.epoch())))
        r = tx.conn.execute("SELECT value FROM collateral_state WHERE key=?", (START_KEY,)).fetchone()
    return int(r["value"]) if r is not None else None


MARGIN_CALL_JOB = MarginCallJob()


def bind(rt) -> None:
    """The schema (C15 columns, the CSA terms; 1.0.6: margin terms, margin calls, CSA pledges, the job's start marker - all additive and
    idempotent), the two C9b adjusters (restricted collateral; the Reg T margin requirement) and the MARGIN_CALL job (C13
    ``lifecycle_eod.add_product_job``, in front of SWEEP_OUT so the reserved margin cash is never swept)."""
    db = getattr(rt, "db", None)
    if db is not None and callable(getattr(db, "ensure_schema_ext", None)):
        ensure_schema(db)
        if callable(getattr(db, "writer", None)):
            rt.margin_call_start_ts = _mark_start(db, getattr(rt, "clock", None))
    rt.collateral_adjuster = _register_adjuster(rt)
    rt.margin_adjuster = _register_margin_adjuster()
    try:
        from .lifecycle_eod import add_product_job
    except ImportError:   # pragma: no cover - a build without the C13 hook
        return
    add_product_job(MARGIN_CALL_JOB, before="SWEEP_OUT")


def _reader(rt):
    from .marks import shared_reader
    return shared_reader(rt.db)


def register_routes(r) -> None:
    """Settlements: GET /api/settlements/{id}; POST .../fail {reason}; POST .../resolve {action, amount | cost}.  Restricted cash: GET
    /api/collateral/restricted?account_id=&currency=.  1.0.6: GET /api/collateral/summary?book=|scope=, /api/collateral/regt?account_id=,
    /api/collateral/requirements?account_id=, /api/collateral/accounts?book=, /api/collateral/custody?book=, /api/collateral/cash?book=,
    /api/collateral/pledges?book=, /api/collateral/treasury_securities?book=, /api/margin/calls?book=|scope=&from=&to=&status=; POST
    /api/margin/terms, /api/collateral/csa, /api/collateral/pledges, /api/collateral/pledges/{id}/release."""
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

    def scope_of(req) -> str:
        s = req.q("scope") or req.q("account_id")
        if s and s != "ALL":
            return s
        return f"{req.lifetime()}:ALL"

    def guarded(fn):
        def run(req):
            try:
                return fn(req)
            except RejectError as e:
                raise ApiError(int(getattr(e, "http", 400) or 400), e.code, e.message, e.details)
        return run

    def account_of(req) -> str:
        aid = req.q("account_id") or ""
        with _reader(req.rt) as conn:
            if account_row(conn, aid) is None:
                raise NotFound(f"account {aid} does not exist")
        return aid

    def regt_get(req):
        aid = account_of(req)
        with _reader(req.rt) as conn:
            snap = _snapshot_of(req.rt, ids=_scope_ids(conn, [aid]))
            return regt_view(conn, aid, snap, getattr(req.rt, "instruments", None))

    def calls_get(req):
        return {"rows": margin_calls_rows(req.rt, scope_of(req), req.q("from"), req.q("to"), req.q("status"), req.q_int("limit", 200, 1, 1000)),
                "definition": CALLS_TEXT}

    def terms_post(req):
        return run_command(req, "margin.terms", req.payload("account_id", "initial_pct", "maintenance_pct", "long_loan_value_pct"))

    def csa_post(req):
        return run_command(req, "collateral.csa_terms", req.payload("account_id", "reuse_allowed"))

    def pledge_post(req):
        return run_command(req, "collateral.pledge", req.payload("account_id", "instrument_id", "face", "pledged_to", "contract_id", "note"))

    def release_post(req):
        return run_command(req, "collateral.release", {"pledge_id": req.params[0]})

    r.add("GET", r"/api/settlements/([^/]+)", settlement_get)
    r.add("POST", r"/api/settlements/([^/]+)/fail", settlement_fail)
    r.add("POST", r"/api/settlements/([^/]+)/resolve", settlement_resolve)
    r.add("GET", r"/api/collateral/restricted", restricted_get)
    r.add("GET", r"/api/collateral/summary", guarded(lambda req: summary(req.rt, scope_of(req))))
    r.add("GET", r"/api/collateral/regt", guarded(regt_get))
    r.add("GET", r"/api/collateral/requirements", guarded(lambda req: {"account_id": account_of(req), "rows": position_requirements(req.rt, account_of(req))}))
    r.add("GET", r"/api/collateral/accounts", guarded(lambda req: accounts_view(req.rt, scope_of(req))))
    r.add("GET", r"/api/collateral/custody", guarded(lambda req: custody_statement(req.rt, scope_of(req))))
    r.add("GET", r"/api/collateral/cash", guarded(lambda req: cash_collateral_view(req.rt, scope_of(req))))
    r.add("GET", r"/api/collateral/pledges", guarded(lambda req: pledges_view(req.rt, scope_of(req))))
    r.add("POST", r"/api/collateral/pledges", pledge_post)
    r.add("POST", r"/api/collateral/pledges/([^/]+)/release", release_post)
    r.add("GET", r"/api/collateral/treasury_securities", guarded(lambda req: treasury_securities(req.rt, scope_of(req))))
    r.add("POST", r"/api/collateral/csa", csa_post)
    r.add("GET", r"/api/margin/calls", guarded(calls_get))
    r.add("POST", r"/api/margin/terms", terms_post)


__all__ = ["SETTLEMENT_COLUMNS", "CSA_TERMS_TABLE", "SCHEMA", "ADJUSTER_KEY", "ADJUSTER_LABEL", "RESTRICTED_TEXT", "ForbiddenError", "has_fail_columns",
           "schema_ready", "bd_balance_c", "gl_c", "settled_at_close_c", "reuse_allowed", "restricted_parts", "restricted_c", "restricted_adjuster",
           "settlement_state", "settlement_view", "mark_failed", "mark_resolved", "group_entry_ids", "restate_accruals", "h_settlement_fail",
           "h_settlement_resolve", "h_asof_sweep", "h_asof_transfer", "h_reaccrue", "h_csa_terms", "ensure_schema", "register", "bind", "register_routes",
           # 1.0.6 (Part B)
           "REGT_DEFAULTS", "REGT_TEXT", "MARGIN_ADJUSTER_KEY", "MARGIN_ADJUSTER_LABEL", "CALL_KINDS", "CALL_KIND_LABEL", "CALLS_TEXT", "PLEDGE_TEXT",
           "TBILL_TEXT", "phase2_ready", "position_price", "margin_terms", "free_cash_parts", "regt_view", "margin_adjuster", "h_margin_terms", "csa_haircut",
           "csa_pledged_face", "h_pledge", "h_release", "custodian_of", "custody_statement", "call_dates", "issue_calls", "check_call", "MarginCallJob",
           "MARGIN_CALL_JOB", "margin_calls_rows", "summary", "position_requirements", "pledges_view", "treasury_securities", "income_by_type",
           "short_collateral_rows", "cash_collateral_view", "accounts_view"]
