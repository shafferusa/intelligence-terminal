"""The ledger: chart of accounts, entries, posting, balances, FIFO lots (TERMINAL_DESIGN.md §2.7, §5).

Conventions
- Double entry, integer cents.  Storage sign: ``amount_c`` is debit-positive for every account kind.  The
  natural-sign balance for display is ``+balance`` for ASSET/EXPENSE and ``-balance`` for LIABILITY/EQUITY/INCOME.
- An entry balances per currency (Σ amount_c = 0 for each currency) AND in base: lines are grouped by
  (currency, fx_rate); USD lines carry base = amount; every other group goes through ``units.base_with_plug``
  so the entry sums to zero in base by construction (§5.3).
- The ledger is at cost: realised P&L, fees, accruals, margin, collateral and settlements are posted; market
  values, unrealised P&L, FX result and NAV are computed by ``accounting.py`` and never posted.
- ``idem_key`` is unique: the same economic event can never post twice (``DuplicatePosting``).
- Lots are relieved FIFO (oldest ``open_ts``, then ``lot_id``); proceeds are allocated pro rata by quantity with
  the residual cent on the last relief; a partial relief takes the pro-rata share of the lot's remaining cost so
  the lot's cost reaches exactly zero with its last share (sentence FIFO, COST_BASIS).
- ``post`` refuses CLOSED accounts, so an archived lifetime is read-only by construction (L23).
- C11 (FINSIM1_PLAN, P4): an entry's business date is ``entry.business_date``, else ``ctx.asof_date`` (an as-of booking or a
  lifecycle job of a past business date sets it on its command context: every entry it posts without its own date - a funding
  sweep included - then carries that date), else the accounting day of ``ctx.now`` (sentence ACCT_DAY).
- The OTC GLs (C11, PORTFOLIO-08): 4450 stays the realised "swap results" GL (return leg, STM settlement, termination,
  maturity); 4451 swap financing (TRS funding leg) and 4453 swap coupons / CDS premium are FINANCING (kind EXPENSE: natural
  sign = a cost, so a financing or coupon RECEIVED is a negative cost); 4452 dividend equivalents is INCOME; 1335 / 2345 are
  the per-contract cost basis of an OTC contract - an upfront or option premium paid (asset) / received (liability) at the
  booking, released into 4450 when the contract terminates, matures or expires.  ``OTC_COST_GLS`` are kept out of
  ``COST_LONG_GLS`` / ``COST_SHORT_GLS`` (L9 ties those to open lots) and out of the NAV asset / liability sets (the contract's
  market value replaces its cost there, C9c).
- ``ensure_chart_rows(conn, codes=None)`` inserts the ``chart`` rows of every ``CHART`` code (or the given ones), INSERT OR
  IGNORE, so a GL a release adds can post on a database an older release created (``journal_lines.gl`` is a foreign key).
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from .clock import accounting_day
from .units import base_with_plug, qprice, to_c

_CCY_RE = re.compile(r"^[A-Z]{3}$")


@dataclass(frozen=True)
class GlSpec:
    code: str
    name: str
    kind: str              # ASSET | LIABILITY | EQUITY | INCOME | EXPENSE
    per_instrument: bool
    bucket: str


def _g(code, name, kind, per_instrument, bucket):
    return code, GlSpec(code, name, kind, bool(per_instrument), bucket)


CHART: Dict[str, GlSpec] = dict([
    _g("1010", "Cash — settled", "ASSET", 0, "cash_settled"),
    _g("1100", "Investments — equities/ETFs at cost", "ASSET", 1, "positions_cost"),
    _g("1110", "Investments — bonds at cost", "ASSET", 1, "positions_cost"),
    _g("1120", "Investments — private company shares at cost", "ASSET", 1, "positions_cost"),
    _g("1130", "Private credit loans at amortised cost", "ASSET", 1, "positions_cost"),
    _g("1200", "Receivable — unsettled sales", "ASSET", 0, "cash_pending_in"),
    _g("1210", "Dividends receivable", "ASSET", 1, "accrued_income"),
    _g("1220", "Accrued interest receivable — bonds and private credit", "ASSET", 1, "accrued_income"),
    _g("1225", "Accrued swap receivable", "ASSET", 1, "accrued_income"),
    _g("1230", "Accrued interest receivable — cash", "ASSET", 0, "accrued_income"),
    _g("1250", "FX settlement receivable", "ASSET", 0, "cash_pending_in"),
    _g("1300", "Margin deposit — futures initial margin", "ASSET", 1, "margin"),
    _g("1310", "Margin deposit — listed options", "ASSET", 0, "margin"),
    _g("1320", "Variation margin posted — OTC (PB)", "ASSET", 1, "margin"),
    _g("1330", "Initial margin — OTC (PB)", "ASSET", 1, "margin"),
    _g("1335", "OTC contract cost — upfront / premium paid", "ASSET", 1, "otc_cost"),
    _g("1360", "Cross-currency notional receivable", "ASSET", 1, "financing_asset"),
    _g("1400", "Cash collateral posted — stock borrow", "ASSET", 1, "collateral"),
    _g("1410", "Accrued rebate receivable", "ASSET", 1, "accrued_income"),
    _g("1420", "Accrued securities-lending fee receivable", "ASSET", 1, "accrued_income"),
    _g("1500", "Reverse repo receivable", "ASSET", 0, "financing_asset"),
    _g("1700", "Options purchased at cost", "ASSET", 1, "positions_cost"),
    _g("2100", "Payable — unsettled purchases", "LIABILITY", 0, "cash_pending_out"),
    _g("2300", "Accrued interest payable — PB financing", "LIABILITY", 0, "accrued_expense"),
    _g("2310", "Accrued borrow fees payable", "LIABILITY", 1, "accrued_expense"),
    _g("2320", "Payments in lieu payable", "LIABILITY", 1, "accrued_expense"),
    _g("2330", "Variation margin received — OTC (PB)", "LIABILITY", 1, "margin_liability"),
    _g("2340", "Accrued swap payable", "LIABILITY", 1, "accrued_expense"),
    _g("2345", "OTC contract cost — upfront / premium received", "LIABILITY", 1, "otc_cost"),
    _g("2350", "FX settlement payable", "LIABILITY", 0, "cash_pending_out"),
    _g("2360", "Cross-currency notional payable", "LIABILITY", 1, "financing"),
    _g("2420", "Accrued rebate payable — securities lent", "LIABILITY", 1, "accrued_expense"),
    _g("2500", "Repo borrowing", "LIABILITY", 0, "financing"),
    _g("2520", "Cash collateral received — securities lent", "LIABILITY", 1, "collateral_received"),
    _g("2600", "Securities sold short (at proceeds)", "LIABILITY", 1, "positions_short"),
    _g("2700", "Prime-broker financing", "LIABILITY", 0, "financing"),
    _g("2750", "Currency loans — principal", "LIABILITY", 0, "financing"),
    _g("2755", "Accrued interest payable — currency loans", "LIABILITY", 0, "accrued_expense"),
    _g("2900", "Options written (at premium received)", "LIABILITY", 1, "positions_short"),
    _g("3000", "Contributed capital", "EQUITY", 0, "capital"),
    _g("3100", "Treasury advances (internal funding)", "EQUITY", 0, "transfers"),
    _g("3900", "FX conversion clearing", "EQUITY", 0, "fx_clearing"),
    _g("4000", "Realised P&L — securities and options", "INCOME", 1, "realised"),
    _g("4200", "Dividend income", "INCOME", 1, "income"),
    _g("4300", "Interest income — cash and coupons", "INCOME", 1, "income"),
    _g("4320", "Fee income — private credit", "INCOME", 1, "income"),
    _g("4350", "Rebate income — cash collateral", "INCOME", 1, "income"),
    _g("4360", "Securities-lending fee income", "INCOME", 1, "income"),
    _g("4400", "Futures variation margin", "INCOME", 1, "realised"),
    _g("4450", "Swap results — resets, net coupons, STM settlements", "INCOME", 1, "realised"),
    _g("4451", "Swap financing leg (TRS funding)", "EXPENSE", 1, "financing_cost"),
    _g("4452", "Dividend equivalents (TRS)", "INCOME", 1, "income"),
    _g("4453", "Swap coupons and CDS premium", "EXPENSE", 1, "financing_cost"),
    _g("4500", "Reverse repo interest", "INCOME", 0, "income"),
    _g("5000", "Commissions and fees", "EXPENSE", 1, "fees"),
    _g("5100", "Interest expense — PB financing", "EXPENSE", 0, "financing_cost"),
    _g("5150", "Interest expense — currency loans", "EXPENSE", 0, "financing_cost"),
    _g("5300", "Securities borrow fees", "EXPENSE", 1, "financing_cost"),
    _g("5350", "Rebate paid on cash collateral received", "EXPENSE", 1, "financing_cost"),
    _g("5400", "Payments in lieu", "EXPENSE", 1, "financing_cost"),
    _g("5500", "Interest expense — repo", "EXPENSE", 0, "financing_cost"),
    _g("5700", "Buy-in penalties", "EXPENSE", 1, "financing_cost"),
])

NATURAL_SIGN = {"ASSET": 1, "EXPENSE": 1, "LIABILITY": -1, "EQUITY": -1, "INCOME": -1}
# Codes whose balances the accounting read models treat as "cost replaced by market value" (§6.3)
COST_LONG_GLS = ("1100", "1110", "1120", "1700")
COST_SHORT_GLS = ("2600", "2900")
REALISED_GLS = ("4000", "4400", "4450")
INCOME_GLS = ("4200", "4300", "4320", "4350", "4360", "4500", "4452")
FINANCING_GLS = ("5100", "5150", "5300", "5350", "5400", "5500", "5700", "4451", "4453")
FEE_GLS = ("5000",)
# C11 (FINSIM1_PLAN): the OTC contract cost GLs - an upfront / OTC option premium paid (1335) or received (2345) at the booking, per
# contract; released into 4450 at termination, maturity or expiry.  NOT in COST_LONG_GLS / COST_SHORT_GLS (L9 ties those to open lots)
# and not in the NAV asset / liability sets: the contract's market value replaces its cost there (C9c), so A - L = NAV holds.
OTC_COST_GLS = ("1335", "2345")
OTC_COST_PAID_GL, OTC_COST_RECEIVED_GL = OTC_COST_GLS
SWAP_RESULT_GL = "4450"          # realised: return leg, STM settlement, termination / maturity value, released cost basis
SWAP_FINANCING_GL = "4451"       # financing: the TRS funding leg (SOFR +/- spread)
SWAP_DIVIDEND_GL = "4452"        # income: dividend equivalents of a TRS
SWAP_COUPON_GL = "4453"          # financing: IRS / XCCY coupon accruals and the CDS index premium


def ensure_chart_rows(conn: sqlite3.Connection, codes=None) -> int:
    """C11: INSERT OR IGNORE the ``chart`` row of every ``CHART`` code (or of ``codes``) on ``conn`` (the caller's transaction, when it
    holds one); returns the rows added.  An existing row is never altered.  ``Database.open`` syncs the whole chart on every open; a
    product module that posts a newer GL calls this from its ``bind`` too (``swaps.bind``), so a database an older release created can
    take the posting (``journal_lines.gl`` is a foreign key to ``chart``)."""
    n = 0
    for code in (CHART if codes is None else codes):
        spec = CHART.get(str(code))
        if spec is None:
            continue
        cur = conn.execute("INSERT OR IGNORE INTO chart(gl, name, kind, per_instrument, bucket) VALUES (?,?,?,?,?)",
                           (spec.code, spec.name, spec.kind, 1 if spec.per_instrument else 0, spec.bucket))
        n += int(cur.rowcount or 0)
    return n


def asof_business_date(ctx) -> Optional[str]:
    """``ctx.asof_date`` as an ISO date (None when the command context carries none): the business date of an as-of booking or of a
    lifecycle job's past date (C11)."""
    d = getattr(ctx, "asof_date", None)
    if d is None or d == "":
        return None
    return d.isoformat() if hasattr(d, "isoformat") else str(d)[:10]


def natural(gl: str, balance_c: int) -> int:
    """Storage (debit-positive) balance -> natural-sign balance for display."""
    return NATURAL_SIGN[CHART[gl].kind] * int(balance_c)


class LedgerError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message or code


class DuplicatePosting(LedgerError):
    """idem_key reuse -> 409 DUPLICATE_POSTING."""

    def __init__(self, idem_key: str):
        super().__init__("DUPLICATE_POSTING", f"journal idem_key already posted: {idem_key}")
        self.idem_key = idem_key


@dataclass(frozen=True)
class Line:
    account_id: str
    gl: str
    currency: str
    amount_c: int                       # signed: debit +, credit −
    instrument_id: Optional[str] = None
    lot_id: Optional[int] = None
    memo: str = ""
    counterparty: Optional[str] = None
    fx_rate: Optional[Decimal] = None   # None -> the command's FxTable rate; FX-deal handlers set the deal rate


@dataclass
class Entry:
    kind: str
    memo: str
    lines: List[Line]
    idem_key: str
    ref_type: Optional[str] = None
    ref_id: Optional[str] = None
    business_date: Optional[str] = None


def dr(account_id: str, gl: str, currency: str, amount_c: int, **kw) -> Line:
    """A debit of ``amount_c`` (a negative amount flips to a credit — the old helper semantics)."""
    return Line(account_id, gl, currency, int(amount_c), **kw)


def cr(account_id: str, gl: str, currency: str, amount_c: int, **kw) -> Line:
    """A credit of ``amount_c`` (stored negative)."""
    return Line(account_id, gl, currency, -int(amount_c), **kw)


def _account_status(conn: sqlite3.Connection, account_id: str, cache: dict) -> Optional[str]:
    if account_id not in cache:
        row = conn.execute("SELECT status FROM accounts WHERE account_id=?", (account_id,)).fetchone()
        cache[account_id] = row["status"] if row else None
    return cache[account_id]


def validate(ctx, entry: Entry) -> List[Tuple[Line, int, Decimal, str]]:
    """§5.2 validation.  Returns ``[(line, base_c, fx_rate, fx_source)]`` in line order or raises ``LedgerError``."""
    if not entry.lines:
        raise LedgerError("EMPTY_ENTRY", "an entry needs at least one line")
    if not entry.idem_key:
        raise LedgerError("IDEM_KEY_REQUIRED", "every entry names its idem_key")
    status_cache: dict = {}
    per_ccy: Dict[str, int] = {}
    for ln in entry.lines:
        if not isinstance(ln.amount_c, int) or isinstance(ln.amount_c, bool) or ln.amount_c == 0:
            raise LedgerError("ZERO_AMOUNT", f"line {ln.gl} {ln.account_id}: amount must be a non-zero int of cents")
        spec = CHART.get(ln.gl)
        if spec is None:
            raise LedgerError("UNKNOWN_GL", f"gl {ln.gl} is not in the chart")
        if not _CCY_RE.match(ln.currency or ""):
            raise LedgerError("BAD_CURRENCY", f"currency {ln.currency!r} must be three upper-case letters")
        st = _account_status(ctx.conn, ln.account_id, status_cache)
        if st is None:
            raise LedgerError("ACCOUNT_UNKNOWN", f"account {ln.account_id} does not exist")
        if st != "OPEN":
            raise LedgerError("ACCOUNT_CLOSED", f"account {ln.account_id} is CLOSED (its lifetime is archived, read-only)")
        if spec.per_instrument and not ln.instrument_id:
            raise LedgerError("INSTRUMENT_REQUIRED", f"gl {ln.gl} ({spec.name}) is per instrument; the line needs instrument_id")
        per_ccy[ln.currency] = per_ccy.get(ln.currency, 0) + ln.amount_c
    bad = {c: s for c, s in per_ccy.items() if s != 0}
    if bad:
        raise LedgerError("UNBALANCED", "entry does not balance per currency: " + ", ".join(f"{c} {s:+d}" for c, s in sorted(bad.items())))
    # base conversion per (currency, fx_rate) group with the §5.3 plug
    groups: Dict[Tuple[str, str], List[int]] = {}
    rate_of: Dict[Tuple[str, str], Tuple[Decimal, str]] = {}
    keys: List[Tuple[str, str]] = []
    for i, ln in enumerate(entry.lines):
        if ln.currency == "USD":
            key = ("USD", "1")
            rate_of[key] = (Decimal(1), "const")
        else:
            if ln.fx_rate is not None:
                rate, source = qprice(ln.fx_rate), "deal"
            else:
                rate, source = ctx.fx.rate(ln.currency), ctx.fx.source(ln.currency)
            key = (ln.currency, str(rate))
            rate_of[key] = (rate, source)
        groups.setdefault(key, []).append(i)
        keys.append(key)
    bases = [0] * len(entry.lines)
    for key, idxs in groups.items():
        rate, _src = rate_of[key]
        amounts = [entry.lines[i].amount_c for i in idxs]
        if key[0] == "USD":
            converted = amounts
        else:
            if sum(amounts) != 0:
                raise LedgerError("UNBALANCED_GROUP", f"{key[0]} lines at rate {key[1]} do not balance ({sum(amounts):+d})")
            converted = base_with_plug(amounts, rate)
        for i, b in zip(idxs, converted):
            bases[i] = b
    if sum(bases) != 0:
        raise LedgerError("UNBALANCED_BASE", f"entry base sum {sum(bases):+d} (plug rule violated)")
    return [(ln, bases[i], rate_of[keys[i]][0], rate_of[keys[i]][1]) for i, ln in enumerate(entry.lines)]


def post(ctx, entry: Entry) -> int:
    """Validate (§5.2/5.3), insert the entry and its lines, update ``gl_balances`` (the only projection) and record
    the touched keys on the context.  Returns ``entry_id``.  Raises ``LedgerError`` / ``DuplicatePosting``."""
    rows = validate(ctx, entry)
    conn = ctx.conn
    ts = int(ctx.now.timestamp())
    business_date = entry.business_date or asof_business_date(ctx) or accounting_day(ctx.now).isoformat()     # C11
    try:
        cur = conn.execute(
            "INSERT INTO journal_entries(command_id, event_id, ts, business_date, kind, memo, ref_type, ref_id, idem_key) VALUES (?,?,?,?,?,?,?,?,?)",
            (ctx.command_id, None, ts, business_date, entry.kind, entry.memo, entry.ref_type, entry.ref_id, entry.idem_key))
    except sqlite3.IntegrityError as e:
        if "idem_key" in str(e) or "UNIQUE" in str(e):
            raise DuplicatePosting(entry.idem_key) from e
        raise
    entry_id = int(cur.lastrowid)
    for n, (ln, base_c, rate, source) in enumerate(rows, start=1):
        conn.execute(
            "INSERT INTO journal_lines(entry_id, line_no, account_id, gl, currency, amount_c, base_c, fx_rate, fx_source, instrument_id, lot_id, counterparty, memo)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (entry_id, n, ln.account_id, ln.gl, ln.currency, ln.amount_c, base_c, str(rate), source, ln.instrument_id, ln.lot_id,
             ln.counterparty, ln.memo or None))
        inst = ln.instrument_id if CHART[ln.gl].per_instrument else ""
        conn.execute(
            "INSERT INTO gl_balances(account_id, gl, currency, instrument_id, balance_c, base_c) VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(account_id, gl, currency, instrument_id) DO UPDATE SET balance_c = balance_c + excluded.balance_c, base_c = base_c + excluded.base_c",
            (ln.account_id, ln.gl, ln.currency, inst, ln.amount_c, base_c))
        ctx.touched_accounts.add(ln.account_id)
        if ln.instrument_id:
            ctx.touched_instruments.add((ln.account_id, ln.instrument_id))
    ctx.touched_entries.append(entry_id)
    return entry_id


def _bal(conn: sqlite3.Connection, column: str, account_id: str, gl: str, currency: Optional[str], instrument_id: Optional[str]) -> int:
    sql = f"SELECT COALESCE(SUM({column}), 0) FROM gl_balances WHERE account_id=? AND gl=?"
    params: list = [account_id, gl]
    if currency is not None:
        sql += " AND currency=?"
        params.append(currency)
    if instrument_id is not None:
        sql += " AND instrument_id=?"
        params.append(instrument_id)
    row = conn.execute(sql, params).fetchone()
    return int(row[0] or 0)


def balance(conn: sqlite3.Connection, account_id: str, gl: str, currency: Optional[str] = None, instrument_id: Optional[str] = None) -> int:
    """Natural-sign balance in cents (summed over currencies / instruments when those are None)."""
    return natural(gl, _bal(conn, "balance_c", account_id, gl, currency, instrument_id))


def base_balance(conn: sqlite3.Connection, account_id: str, gl: str, currency: Optional[str] = None, instrument_id: Optional[str] = None) -> int:
    """Natural-sign balance in USD base cents (at posting rates)."""
    return natural(gl, _bal(conn, "base_c", account_id, gl, currency, instrument_id))


def balances_by(conn: sqlite3.Connection, account_id: str, gl_prefix: str = "") -> Dict[Tuple[str, str, str], int]:
    """``{(gl, currency, instrument_id): natural cents}`` for one account (non-zero rows only)."""
    rows = conn.execute("SELECT gl, currency, instrument_id, balance_c FROM gl_balances WHERE account_id=? AND gl LIKE ? AND balance_c <> 0",
                        (account_id, gl_prefix + "%")).fetchall()
    return {(r["gl"], r["currency"], r["instrument_id"]): natural(r["gl"], r["balance_c"]) for r in rows}


@dataclass
class Relief:
    lot_id: int
    qty: int
    cost_relieved_c: int
    proceeds_c: int
    realised_c: int


def open_lot(tx, account_id: str, instrument_id: str, side: str, fill_id: str, qty: int, unit_cost: Decimal, cost_c: int, currency: str,
             lock_until: Optional[str] = None) -> int:
    """Insert an open lot (LONG cost / SHORT proceeds, gross, instrument currency).  Returns ``lot_id``."""
    if side not in ("LONG", "SHORT"):
        raise LedgerError("BAD_SIDE", f"lot side must be LONG or SHORT, got {side!r}")
    if int(qty) <= 0:
        raise LedgerError("BAD_QTY", "lot quantity must be positive")
    conn = tx.conn
    cur = conn.execute(
        "INSERT INTO lots(account_id, instrument_id, side, open_fill_id, open_ts, qty_open, qty_remaining, unit_cost, cost_c, currency, lock_until)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (account_id, instrument_id, side, fill_id, int(tx.now.timestamp()), int(qty), int(qty), str(qprice(unit_cost)), int(cost_c), currency, lock_until))
    return int(cur.lastrowid)


def open_lots(conn: sqlite3.Connection, account_id: str, instrument_id: str, side: Optional[str] = None) -> List[sqlite3.Row]:
    sql = "SELECT * FROM lots WHERE account_id=? AND instrument_id=? AND qty_remaining > 0"
    params: list = [account_id, instrument_id]
    if side:
        sql += " AND side=?"
        params.append(side)
    sql += " ORDER BY open_ts, lot_id"
    return conn.execute(sql, params).fetchall()


def relieve_fifo(tx, account_id: str, instrument_id: str, side: str, qty: int, close_fill_id: str, proceeds_c_total: int) -> List[Relief]:
    """Relieve ``qty`` from the open lots of ``side`` oldest first.  ``proceeds_c_total`` (the closing gross amount)
    is allocated pro rata by quantity with the residual cent on the last relief; each relief takes the pro-rata
    share of the lot's remaining cost (the last share of a lot takes its whole remaining cost).
    realised = proceeds − cost for LONG lots; cost(=proceeds at opening) − closing amount for SHORT lots."""
    qty = int(qty)
    if qty <= 0:
        raise LedgerError("BAD_QTY", "relief quantity must be positive")
    conn = tx.conn
    lots = open_lots(conn, account_id, instrument_id, side)
    available = sum(int(r["qty_remaining"]) for r in lots)
    if available < qty:
        raise LedgerError("INSUFFICIENT_LOTS", f"{account_id} {instrument_id}: {side} lots hold {available}, asked to relieve {qty}")
    plan: List[Tuple[sqlite3.Row, int]] = []
    left = qty
    for r in lots:
        if left == 0:
            break
        take = min(left, int(r["qty_remaining"]))
        plan.append((r, take))
        left -= take
    reliefs: List[Relief] = []
    allocated = 0
    for i, (r, take) in enumerate(plan):
        if i == len(plan) - 1:
            proceeds = int(proceeds_c_total) - allocated
        else:
            proceeds = to_c(Decimal(int(proceeds_c_total)) / Decimal(100) * Decimal(take) / Decimal(qty))
        allocated += proceeds
        remaining = int(r["qty_remaining"])
        if take == remaining:
            cost = int(r["cost_c"])
        else:
            cost = to_c(Decimal(int(r["cost_c"])) / Decimal(100) * Decimal(take) / Decimal(remaining))
        realised = proceeds - cost if side == "LONG" else cost - proceeds
        conn.execute("UPDATE lots SET qty_remaining = qty_remaining - ?, cost_c = cost_c - ? WHERE lot_id=?", (take, cost, int(r["lot_id"])))
        conn.execute("INSERT INTO lot_relief(close_fill_id, lot_id, qty, cost_relieved_c, proceeds_c, realised_c) VALUES (?,?,?,?,?,?)",
                     (close_fill_id, int(r["lot_id"]), take, cost, proceeds, realised))
        reliefs.append(Relief(int(r["lot_id"]), take, cost, proceeds, realised))
    return reliefs


def position(conn: sqlite3.Connection, account_id: str, instrument_id: str) -> Tuple[int, int]:
    """``(signed qty, cost_c)`` from ``v_positions`` (0, 0 when flat)."""
    row = conn.execute("SELECT qty, cost_c FROM v_positions WHERE account_id=? AND instrument_id=?", (account_id, instrument_id)).fetchone()
    if row is None:
        return 0, 0
    return int(row["qty"] or 0), int(row["cost_c"] or 0)
