"""Lifecycle jobs, part 1: the Job protocol, the §11.3 accrual helper and the money jobs SETTLE_DUE, ACCRUE_DAILY,
PAY_MONTHLY, PB_MONTHLY_MIN and SWEEP_OUT (TERMINAL_DESIGN.md §2.12 lifecycle.py, §5.4, §11.1–11.3, §11.5, §11.6,
§12.2, §12.4, §19.11, §19.23; TERMINAL_WORKPLAN.md WP-08).  ``lifecycle_eod.py`` holds the end-of-day, product and
housekeeping jobs plus ``REGISTRY``; ``lifecycle.py`` the scheduler, the bus handlers and ``run_now``.

Conventions stated here (each is a §1.3 sentence or a rule the tests pin)
- JOB PROTOCOL (§11.1): a job has a ``name`` (the ``jobs_done.job`` value), a command ``kind`` (its own ``lifecycle.<x>``
  handler or a product handler such as ``futures.vm``), ``due(conn, clock, cal, sessions) -> list[str]`` deriving the
  keys that should run now FROM STATE (dates since the last accrual, settlements with past due dates, positions with
  expired instruments) and ``run(ctx, key, services) -> dict`` doing one key's work inside the command's transaction.
  Keys are strings; an account id contains a colon, so every key puts the account FIRST and the fixed-width parts
  (dates, currencies, instrument ids) LAST and is split from the right — never parsed from the left.  ``due()``
  considers only accounts of OPEN lifetimes.  Because keys derive from state, catch-up after downtime is automatic:
  the first tick after a 7-day gap posts exactly 7 accruals per object, in date order, each with its own key.
- JOBS_DONE: every key ends as one ``jobs_done`` row (DONE, or SKIPPED with a note) written in the same transaction as
  the work.  A SKIPPED key is a pending outcome (a missing mark or rate, an unfunded settlement) that comes back as
  due and is retried; a job may declare ``retry_skipped = False`` when a SKIPPED row is terminal for its key
  (SWEEP_OUT "nothing idle" for that date).  Nothing is ever posted in place of missing data.
- ACCRUAL (§11.3, cumulative rounding): ``accrue_segment(ctx, kind, ref, base_c, rate, basis, night)`` keeps one
  ``accrual_state`` row per (kind, ref) holding the segment (``segment_start``, ``base_c``, ``rate``, ``day_basis``,
  ``accrued_through``, ``cum_c``); for night D, ``n = D − segment_start + 1``, ``cum_new = to_c(base × rate × n / basis)``
  and the posted amount is ``cum_new − cum_c``; a changed base, rate or basis restarts the segment at D with ``cum 0``;
  a night already accrued posts 0; a night out of date order raises ACCRUAL_GAP (never lumped).  The sum of the nightly
  postings therefore always equals ``to_c(base × rate × n / basis)`` for the segment — no drift.  ``lending_accruals``
  carries the same function for the borrow kinds; ``credit`` consumes this one through ``services.lifecycle``.
- NIGHTS: every accrual kind uses calendar days (weekends included); night D is due from 00:30 ET of D+1 (from 00:30
  of today every night D < today is due).  The base for night D is the balance at the close of accounting day D,
  read from the ledger's ``business_date`` (entries of D after 17:00 ET belong to D+1); a posting made late (a catch-up
  settlement) carries the business date of the day it was posted, so the bases of the downtime nights are the
  balances as the ledger stood — disclosed in every memo, never restated.
- RATE_LAG: SOFR_D is the latest published observation on or before D (``RateStore.as_of_le``) and must be at most 10
  days old; none → the night is SKIPPED with a WARN attention and retried (never defaulted).  Rates in signatures are
  decimal fractions (``0.0337``); FRED's SOFR print is the published PERCENT (``3.87``), as the RateStore carries it.
- CASH_INT: idle Treasury USD earns ``max(0, SOFR − 0.50 %)`` ACT/360 nightly on the settled USD at the close of D
  (``Dr 1230 / Cr 4300``, idem ``accr:cashint:{lifetime}:USD:{D}`` — the design's ``accr:cashint:USD:{D}`` cannot be
  unique across three books); PB credit interest is ``max(0, SOFR − credit_interest_spread)`` on a PB account's
  positive settled USD (``accr:pbcredit:{account}:{D}``); PB financing interest is ``SOFR + financing_spread`` on
  ``balance(2700)`` (``Dr 5100 / Cr 2300``, ``accr:pb:{account}:{D}``).  4300 is per instrument in the chart, so the
  cash-interest lines carry the synthetic instrument id ``CASH:USD`` (never a position; disclosed).
- BORROW ORDER: a borrow's night D accrues only after the collateral mark of the last trading day on or before D
  (``borrows.last_mark_date``), so a catch-up interleaves marks and nights exactly as real time would (mark Fri,
  nights Fri/Sat/Sun, mark Mon, night Mon) and the rebate base of each night is the collateral that stood that night.
- SETTLE (§11.2 / §5.4): a receivable ``Dr 1010 / Cr 1200|1250``; a payable ``Dr 2100|2350 / Cr 1010`` after
  ``execution.fund`` (own settled cash → one Treasury sweep-in of exactly the shortfall, linked to the settlement →
  PB financing); a payable that cannot be funded stays PENDING with one open ERROR attention SETTLEMENT_UNFUNDED and
  is retried (the §11.2 row; the overdraft rule of §11.6 is for calls, not settlements).  FX legs settle through
  ``fx.settle_deal`` (both legs of a deal).  Private trades carry calendar-day due dates on their rows, so the same
  job settles them on any day.  Idem ``settle:{settle_id}``.
- PAY_MONTHLY (00:45 on the 1st, key ``{account}:{yyyy-mm}``): settles the accrued balances as they stand when it runs —
  borrow fees and rebates per instrument through ``lending.settle_accrued`` (``pay:bfee:{borrow}:{yyyy-mm}``), PB
  financing interest ``Dr 2300 / Cr 1010`` (``pay:pb:{account}:{yyyy-mm}``), cash / PB credit interest ``Dr 1010 /
  Cr 1230`` (``pay:cashint:{account}:{yyyy-mm}``); the memo names the nights covered.  PB_MONTHLY_MIN charges
  ``max(0, monthly_minimum_c − fees charged in the month)`` to 5000 (``pay:pbmin:{account}:{yyyy-mm}``, instrument id
  ``FEE:PB_MIN``).  Loan interest is paid at repayment, not monthly.
- SWEEP (§11.2 / §11.5): once per calendar day at the ``sweep_out_time_et`` setting (17:30), per account with
  ``sweep_out = 1`` and per currency: ``excess = settled − payables pending − reserved by working orders − futures
  maintenance shortfall − buffer`` (the USD buffer converted at the snapshot rate for a foreign currency); ``excess > 0``
  returns to Treasury as one SWEEP_OUT transfer; otherwise the key is SKIPPED "nothing idle" for that date; a currency is
  never swept negative; Treasury is never swept.
- Money is integer cents / Decimal; nothing here is a float; failures raise and are recorded by the scheduler.
"""
from __future__ import annotations

import importlib
import sqlite3
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from . import tables
from .books import account_row, open_lifetimes
from .clock import accounting_day, epoch_of, ny_of
from .commands import ConflictError, RejectError, TxContext
from .db import get_setting
from .ledger import Entry, Line, balance, cr, dr, natural
from .units import fmt_money, from_c, qprice, to_c

ACTOR = "lifecycle"
SOURCE = "lifecycle"
RATE_LAG_MAX_DAYS = 10
CASH_INSTRUMENT = "CASH:USD"          # the per-instrument 4300 line of cash interest (never a position)
PB_MIN_INSTRUMENT = "FEE:PB_MIN"      # the per-instrument 5000 line of a PB monthly minimum
ACCRUE_FROM_ET = "00:30"
PAY_MONTHLY_ET = "00:45"
CLOSED_BORROW_LOOKBACK_S = 30 * 86400
SWEEP_LOOKBACK_DAYS = 14

# accrual kinds whose line functions live in later packages: kind -> (services attribute, table, id column, contract kinds)
ACCRUAL_PRODUCT: Dict[str, Tuple[str, str, str, Tuple[str, ...]]] = {
    "swaprcv": ("swaps", "otc_contracts", "contract_id", ("IRS", "XCCY", "CDX")),
    "swappay": ("swaps", "otc_contracts", "contract_id", ("IRS", "XCCY", "CDX")),
    "trsfin": ("swaps_trs", "otc_contracts", "contract_id", ("TRS",)),
    "pccpn": ("private_credit", "private_loans", "loan_id", ()),
    "pcoid": ("private_credit", "private_loans", "loan_id", ()),
}


# ---- small helpers -----------------------------------------------------------------------------------------------------------
def _date(x) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x))


def _pct(rate: Decimal) -> str:
    return f"{(Decimal(rate) * 100).quantize(Decimal('0.0001')).normalize():f} %"


def module(services, attr: str, import_name: Optional[str] = None):
    """The product module bound as ``services.<attr>`` (what the integrator binds), else the importable module
    ``import_name`` when given; None when neither exists (the job is then skipped with a note, never an error)."""
    mod = getattr(services, attr, None) if services is not None else None
    if mod is not None:
        return mod
    if import_name:
        try:
            return importlib.import_module(import_name)
        except Exception:   # noqa: BLE001 — an optional module that does not import is "not built"
            return None
    return None


def job_row(conn: sqlite3.Connection, job: str, key: str):
    return conn.execute("SELECT job, key, run_ts, command_id, status, note FROM jobs_done WHERE job=? AND key=?", (job, key)).fetchone()


def write_job_done(ctx: TxContext, job: str, key: str, status: str, note: str) -> None:
    ctx.conn.execute("INSERT INTO jobs_done(job, key, run_ts, command_id, status, note) VALUES (?,?,?,?,?,?)"
                     " ON CONFLICT(job, key) DO UPDATE SET run_ts=excluded.run_ts, command_id=excluded.command_id, status=excluded.status, note=excluded.note",
                     (job, key, ctx.ts, ctx.command_id, status, (note or "")[:500]))


def open_accounts(conn: sqlite3.Connection, kinds: Optional[Tuple[str, ...]] = None, lifetime_ids: Optional[List[str]] = None) -> List[sqlite3.Row]:
    """OPEN accounts of OPEN lifetimes (optionally of the given lifetimes / kinds), in book, lifetime and sort order."""
    sql = ("SELECT a.* FROM accounts a JOIN lifetimes l ON l.lifetime_id=a.lifetime_id JOIN books b ON b.book_code=l.book_code"
           " WHERE a.status='OPEN' AND l.status='OPEN'")
    params: list = []
    if lifetime_ids is not None:
        if not lifetime_ids:
            return []
        sql += f" AND a.lifetime_id IN ({','.join('?' for _ in lifetime_ids)})"
        params += list(lifetime_ids)
    sql += " ORDER BY b.sort_order, l.seq, a.sort_order, a.account_id"
    rows = conn.execute(sql, params).fetchall()
    return [r for r in rows if kinds is None or r["kind"] in kinds]


def balance_at_close(conn: sqlite3.Connection, account_id: str, gl: str, ccy: str, d: date) -> int:
    """Natural-sign balance of (account, gl, ccy) at the close of accounting day ``d``: the lines of entries whose
    ``business_date <= d`` (the ledger's own day attribution; a late posting counts from its posting day)."""
    row = conn.execute("SELECT COALESCE(SUM(l.amount_c), 0) FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id"
                       " WHERE l.account_id=? AND l.gl=? AND l.currency=? AND e.business_date<=?", (account_id, gl, ccy, d.isoformat())).fetchone()
    return natural(gl, int(row[0] or 0))


def sofr_for_night(services, d: date) -> Tuple[date, Decimal]:
    """``(observation date, percent)`` for night ``d`` (RATE_LAG, ≤ 10 days old); raises RejectError otherwise."""
    rates = getattr(services, "rates", None)
    if rates is None or not hasattr(rates, "as_of_le"):
        raise RejectError("RATE_STORE_MISSING", "no rate store is bound (services.rates): SOFR is unknown and is never defaulted")
    got = rates.as_of_le("SOFR", d)
    if got is None or (d - got[0]).days > RATE_LAG_MAX_DAYS:
        raise RejectError("RATE_UNAVAILABLE", f"no SOFR observation within {RATE_LAG_MAX_DAYS} days on or before {d.isoformat()}: the night waits for FRED"
                          " (RATE_LAG; never defaulted)", {"series": "SOFR", "night": d.isoformat()})
    return got[0], qprice(got[1])


def last_trading_day(sessions, group: str, d: date) -> Optional[date]:
    """The last trading day of ``group`` on or before ``d`` (within 10 days), else None."""
    x = d
    for _ in range(10):
        try:
            if sessions.is_trading_day(group, x):
                return x
        except KeyError:
            return None
        x -= timedelta(days=1)
    return None


def trading_days(sessions, group: str, start: date, end: date) -> List[date]:
    out: List[date] = []
    d = start
    while d <= end:
        try:
            if sessions.is_trading_day(group, d):
                out.append(d)
        except KeyError:
            return out
        d += timedelta(days=1)
    return out


def nights_between(start: date, through: date) -> List[date]:
    out: List[date] = []
    d = start
    while d <= through:
        out.append(d)
        d += timedelta(days=1)
    return out


def accrual_state_row(conn: sqlite3.Connection, kind: str, ref: str):
    return conn.execute("SELECT * FROM accrual_state WHERE kind=? AND ref_id=?", (kind, ref)).fetchone()


def nights_from_state(conn: sqlite3.Connection, kind: str, ref: str, first: date, through: date) -> List[date]:
    """The nights of ``(kind, ref)`` still to accrue: from ``first`` (or the night after ``accrued_through``) to ``through``."""
    st = accrual_state_row(conn, kind, ref)
    start = first
    if st is not None and st["accrued_through"]:
        start = max(first, _date(st["accrued_through"]) + timedelta(days=1))
    return nights_between(start, through)


# ---- §11.3 ---------------------------------------------------------------------------------------------------------------------
def accrue_segment(ctx: TxContext, kind: str, ref: str, base_c: int, rate, basis: int, night) -> int:
    """§11.3 with persistence (module docstring ACCRUAL).  Returns the cents to post for ``night`` (0 when the night is
    already accrued); the caller posts the entry under its own idem key.  ``night`` is a date or ISO string."""
    night_d = _date(night)
    night_s = night_d.isoformat()
    base_c, basis = int(base_c), int(basis)
    if basis <= 0:
        raise RejectError("BASIS_INVALID", f"day basis must be positive, got {basis}")
    rate_q = qprice(rate)
    conn = ctx.conn
    row = accrual_state_row(conn, kind, ref)
    if row is not None and row["accrued_through"]:
        if row["accrued_through"] >= night_s:
            return 0
        nxt = (_date(row["accrued_through"]) + timedelta(days=1)).isoformat()
        if night_s != nxt:
            raise RejectError("ACCRUAL_GAP", f"{kind} {ref}: night {night_s} requested but {nxt} is the next unaccrued night (accruals post in date order, never lumped)")
    same = (row is not None and row["accrued_through"] and int(row["base_c"]) == base_c and str(row["rate"]) == str(rate_q) and int(row["day_basis"]) == basis)
    if same:
        start, cum_prev = _date(row["segment_start"]), int(row["cum_c"])
    else:
        start, cum_prev = night_d, 0
    n = (night_d - start).days + 1
    cum_new = to_c(from_c(base_c) * rate_q * Decimal(n) / Decimal(basis))
    conn.execute("INSERT INTO accrual_state(kind, ref_id, segment_start, base_c, rate, day_basis, accrued_through, cum_c) VALUES (?,?,?,?,?,?,?,?)"
                 " ON CONFLICT(kind, ref_id) DO UPDATE SET segment_start=excluded.segment_start, base_c=excluded.base_c, rate=excluded.rate,"
                 " day_basis=excluded.day_basis, accrued_through=excluded.accrued_through, cum_c=excluded.cum_c",
                 (kind, ref, start.isoformat(), base_c, str(rate_q), basis, night_s, cum_new))
    return cum_new - cum_prev


# ---- the Job protocol ----------------------------------------------------------------------------------------------------------
class Job:
    """Module docstring JOB PROTOCOL.  Subclasses set ``name``, ``kind``, ``when`` and implement ``due`` / ``run``;
    ``module`` names the ``services`` attribute a product job needs (None for a core job); ``phase`` orders the tick
    (1 = state jobs, iterated until stable; 2 = SNAPSHOT; 3 = expiry / housekeeping / integrity); ``batch`` jobs run
    scheduler-side through ``run_batch`` (marks, integrity) instead of one command per key."""
    name: str = ""
    kind: str = ""
    when: str = ""
    module: Optional[str] = None
    phase: int = 1
    light: bool = False
    batch: bool = False
    retry_skipped: bool = True
    writes_jobs_done: bool = False       # True for product handlers that write their own jobs_done row

    def __init__(self) -> None:
        self.services = None
        self.scheduler = None
        self.notes: Dict[str, str] = {}

    # the protocol
    def due(self, conn: sqlite3.Connection, clock, cal, sessions) -> List[str]:
        return []

    def run(self, ctx: TxContext, key: str, services) -> dict:
        raise NotImplementedError(f"{self.name} has no run()")

    # optional hooks
    def payload(self, key: str) -> dict:
        return {"key": key}

    def series_of(self, key: str) -> Optional[str]:
        """The object a key belongs to (its earlier keys must complete first); None = independent keys."""
        return None

    def before(self, services, keys: List[str]) -> bool:
        """Scheduler-side, outside any transaction, when keys are due; True asks for ``due()`` to be recomputed."""
        return False

    def after_commit(self, services, key: str, res) -> None:
        return None

    def retry_payload(self, services, key: str, res) -> Optional[dict]:
        """A second attempt in the same pass with a richer payload (futures: the daily-bar close), else None."""
        return None

    def run_batch(self, services, keys: List[str], scheduler) -> dict:
        raise NotImplementedError

    def available(self, services) -> Tuple[bool, str]:
        """(available, note): a product job needs its module bound as ``services.<module>`` and its command registered."""
        if self.module is None:
            return True, ""
        mod = getattr(services, self.module, None)
        if mod is None:
            return False, f"skipped: product module services.{self.module} is not built / bound"
        bus = getattr(services, "bus", None)
        if bus is not None and not self.kind.startswith("lifecycle.") and self.kind not in bus.kinds():
            return False, f"skipped: command {self.kind} is not registered"
        return True, ""

    def filter_done(self, conn: sqlite3.Connection, keys: List[str]) -> List[str]:
        out: List[str] = []
        for k in keys:
            r = job_row(conn, self.name, k)
            if r is None:
                out.append(k)
            elif r["status"] != "DONE" and self.retry_skipped:
                out.append(k)
        return out


# ---- SETTLE_DUE -----------------------------------------------------------------------------------------------------------------
class SettleDue(Job):
    name = "SETTLE_DUE"
    kind = "lifecycle.settle"
    when = "from 00:30 ET on the due date (every tick)"

    def due(self, conn, clock, cal, sessions) -> List[str]:
        now = clock.epoch()
        today = clock.today()
        cutoff = today if now >= epoch_of(today, ACCRUE_FROM_ET) else today - timedelta(days=1)
        rows = conn.execute("SELECT s.settle_id FROM settlements s JOIN accounts a ON a.account_id=s.account_id JOIN lifetimes l ON l.lifetime_id=a.lifetime_id"
                            " WHERE s.status='PENDING' AND s.due_date<=? AND a.status='OPEN' AND l.status='OPEN'"
                            " ORDER BY CASE WHEN s.amount_c > 0 THEN 0 ELSE 1 END, s.due_date, s.settle_id", (cutoff.isoformat(),)).fetchall()
        return [r["settle_id"] for r in rows]

    def payload(self, key: str) -> dict:
        return {"key": key, "settle_id": key}

    def run(self, ctx: TxContext, key: str, services) -> dict:
        from . import execution
        conn = ctx.conn
        s = conn.execute("SELECT * FROM settlements WHERE settle_id=?", (key,)).fetchone()
        if s is None:
            raise RejectError("NOT_FOUND", f"settlement {key} does not exist")
        if s["status"] != "PENDING":
            return {"status": "DONE", "note": "already settled", "posted": False}
        aid, ccy, amt, gl, due = s["account_id"], s["currency"], int(s["amount_c"]), s["gl"], s["due_date"]
        if s["fx_deal_id"]:
            fxm = module(services, "fx", "finsim.terminal.fx")
            if fxm is None or not hasattr(fxm, "settle_deal"):
                raise RejectError("NOT_IN_THIS_BUILD", "FX settlement needs the fx module (WP-05), which is not loaded")
            out = fxm.settle_deal(ctx, str(s["fx_deal_id"]), services=services)
            if any(u.get("settle_id") == key for u in out.get("unfunded") or []):
                u = next(x for x in out["unfunded"] if x.get("settle_id") == key)
                return {"status": "SKIPPED", "code": "SETTLEMENT_UNFUNDED", "surfaced": True, "deal": out,
                        "note": f"SETTLEMENT_UNFUNDED: {ccy} {fmt_money(int(u.get('amount_c') or -amt))} for FX deal {s['fx_deal_id']} could not be funded; retried"}
            return {"status": "DONE", "note": f"FX deal {s['fx_deal_id']} leg {s['leg']} {ccy} {fmt_money(abs(amt))}", "deal": out, "posted": True}
        acct = account_row(conn, aid)
        if acct is None or acct["status"] != "OPEN":
            raise RejectError("ACCOUNT_CLOSED", f"account {aid} is not open")
        fill = conn.execute("SELECT instrument_id, side, qty, price FROM fills WHERE fill_id=?", (s["fill_id"],)).fetchone() if s["fill_id"] else None
        iid = fill["instrument_id"] if fill else None
        funding = None
        if amt > 0:
            lines = [dr(aid, "1010", ccy, amt, memo=f"settlement {key} received"), cr(aid, gl, ccy, amt, memo="receivable settled")]
            what = f"receive {ccy} {fmt_money(amt)}"
        else:
            need = -amt
            try:
                funding = execution.fund(ctx, aid, ccy, need, "settlement", key, _date(due), immediate=True, services=services)
            except ConflictError as e:
                if e.code != "INSUFFICIENT_FUNDS":
                    raise
                open_row = conn.execute("SELECT id FROM attention WHERE code='SETTLEMENT_UNFUNDED' AND ref_type='settlement' AND ref_id=? AND resolved_ts IS NULL", (key,)).fetchone()
                if open_row is None:
                    ctx.attention("ERROR", SOURCE, "SETTLEMENT_UNFUNDED",
                                  f"{aid}: {ccy} {fmt_money(need)} due {due} for settlement {key} cannot be funded ({e.message}); it stays pending and is retried",
                                  dict(e.details, account_id=aid, ref_type="settlement", ref_id=key, auto_resolve=1))
                return {"status": "SKIPPED", "code": "SETTLEMENT_UNFUNDED", "surfaced": True, "details": e.details,
                        "note": f"SETTLEMENT_UNFUNDED: {ccy} {fmt_money(need)} due {due} could not be funded (own {fmt_money(int(e.details.get('own_available_c', 0)))},"
                                f" Treasury {fmt_money(int(e.details.get('treasury_available_c', 0)))}); retried"}
            lines = [dr(aid, gl, ccy, need, memo="payable settled"), cr(aid, "1010", ccy, need, memo=f"settlement {key} paid")]
            what = f"pay {ccy} {fmt_money(need)}"
        desc = f" ({fill['side']} {int(fill['qty']):,} {iid} @ {fill['price']})" if fill else ""
        memo = f"settlement {key} " + (f"of fill {s['fill_id']}{desc}" if s["fill_id"] else f"({str(s['leg'] or 'cash').lower()})") + f": {what}, due {due}"
        entry_id = ctx.post(Entry("SETTLE", memo, lines, f"settle:{key}", ref_type="settlement", ref_id=key))
        conn.execute("UPDATE settlements SET status='SETTLED', settled_ts=?, entry_id=? WHERE settle_id=?", (ctx.ts, entry_id, key))
        conn.execute("UPDATE attention SET resolved_ts=? WHERE code='SETTLEMENT_UNFUNDED' AND ref_type='settlement' AND ref_id=? AND resolved_ts IS NULL", (ctx.ts, key))
        execution.auto_resolve_shortfall(ctx, aid, ccy)
        if iid:
            ctx.touched_instruments.add((aid, iid))
        ctx.event("SETTLED", account_id=aid, instrument_id=iid, ref_type="settlement", ref_id=key, fill_id=s["fill_id"], currency=ccy, amount_c=amt, entry_id=entry_id,
                  due_date=due, funding=funding.to_dict() if funding else None)
        return {"status": "DONE", "note": f"{what} (due {due})", "entry_id": entry_id, "amount_c": amt, "currency": ccy, "posted": True,
                "funding": funding.to_dict() if funding else None}


# ---- ACCRUE_DAILY ------------------------------------------------------------------------------------------------------------------
class AccrueDaily(Job):
    name = "ACCRUE_DAILY"
    kind = "lifecycle.accrue"
    when = "from 00:30 ET for every calendar night D < today not yet accrued per object"

    @staticmethod
    def through_night(clock) -> date:
        today = clock.today()
        return today - timedelta(days=1) if clock.epoch() >= epoch_of(today, ACCRUE_FROM_ET) else today - timedelta(days=2)

    def due(self, conn, clock, cal, sessions) -> List[str]:
        lids = open_lifetimes(conn)
        if not lids:
            return []
        through = self.through_night(clock)
        keys: List[str] = []
        keys += self._borrow_keys(conn, sessions, lids, through, clock.epoch())
        keys += self._loan_keys(conn, lids, through)
        keys += self._pb_keys(conn, lids, through)
        keys += self._cash_keys(conn, lids, through)
        keys += self._product_keys(conn, lids, through)
        return self.filter_done(conn, keys)

    def series_of(self, key: str) -> Optional[str]:
        return key.rsplit(":", 1)[0]

    @staticmethod
    def parse(key: str) -> Tuple[str, str, date]:
        kind, rest = key.split(":", 1)
        ref, d = rest.rsplit(":", 1)
        return kind, ref, date.fromisoformat(d)

    def payload(self, key: str) -> dict:
        kind, ref, d = self.parse(key)
        return {"key": key, "kind": kind, "ref": ref, "night": d.isoformat()}

    # -- keys per kind
    def _borrow_keys(self, conn, sessions, lids, through: date, now: int) -> List[str]:
        lending = module(self.services, "lending", "finsim.terminal.lending")
        if lending is None or not hasattr(lending, "nights_due"):
            self.notes["bfee"] = "skipped: lending module not built"
            return []
        q = ",".join("?" for _ in lids)
        rows = conn.execute(f"SELECT b.* FROM borrows b JOIN accounts a ON a.account_id=b.account_id WHERE a.lifetime_id IN ({q}) AND a.status='OPEN'"
                            " AND (b.status='OPEN' OR (b.status='CLOSED' AND b.closed_ts IS NOT NULL AND b.closed_ts > ?)) ORDER BY b.borrow_id",
                            list(lids) + [now - CLOSED_BORROW_LOOKBACK_S]).fetchall()
        master = getattr(self.services, "instruments", None)
        out: List[str] = []
        for b in rows:
            inst = master.get(b["instrument_id"], conn) if master is not None else None
            group = inst.session_group if inst is not None else "US_EQUITY"
            for d in lending.nights_due(conn, b, through):
                if b["status"] == "OPEN":
                    t = last_trading_day(sessions, group, _date(d))
                    if t is None or not b["last_mark_date"] or b["last_mark_date"] < t.isoformat():
                        break                                   # BORROW ORDER: the collateral mark of T(D) comes first
                out.append(f"bfee:{b['borrow_id']}:{d}")
        return out

    def _loan_keys(self, conn, lids, through: date) -> List[str]:
        credit = module(self.services, "credit", "finsim.terminal.credit")
        if credit is None or not hasattr(credit, "nights_due"):
            self.notes["loan"] = "skipped: credit module not built"
            return []
        q = ",".join("?" for _ in lids)
        rows = conn.execute(f"SELECT l.* FROM loans l JOIN accounts a ON a.account_id=l.account_id WHERE l.kind='CASH_LOAN' AND l.status='OPEN'"
                            f" AND a.lifetime_id IN ({q}) AND a.status='OPEN' ORDER BY l.loan_id", list(lids)).fetchall()
        out: List[str] = []
        for loan in rows:
            for d in credit.nights_due(conn, loan, through + timedelta(days=1)):
                out.append(f"loan:{loan['loan_id']}:{_date(d).isoformat()}")
        return out

    def _pb_keys(self, conn, lids, through: date) -> List[str]:
        out: List[str] = []
        for acct in open_accounts(conn, ("PB",), lids):
            aid = acct["account_id"]
            first_credit = accounting_day(ny_of(int(acct["opened_ts"])))
            for d in nights_from_state(conn, "pbcredit", aid, first_credit, through):
                out.append(f"pbcredit:{aid}:{d.isoformat()}")
            loan = conn.execute("SELECT MIN(opened_date) d FROM loans WHERE account_id=? AND kind='PB_MARGIN'", (aid,)).fetchone()
            if loan is not None and loan["d"]:
                for d in nights_from_state(conn, "pb", aid, _date(loan["d"]), through):
                    out.append(f"pb:{aid}:{d.isoformat()}")
        return out

    def _cash_keys(self, conn, lids, through: date) -> List[str]:
        out: List[str] = []
        for lid in lids:
            lrow = conn.execute("SELECT started_ts FROM lifetimes WHERE lifetime_id=?", (lid,)).fetchone()
            tre = f"{lid}:TREASURY"
            if lrow is None or account_row(conn, tre) is None:
                continue
            first = accounting_day(ny_of(int(lrow["started_ts"])))
            for d in nights_from_state(conn, "cashint", tre, first, through):
                out.append(f"cashint:{lid}:{d.isoformat()}")
        return out

    def _product_keys(self, conn, lids, through: date) -> List[str]:
        out: List[str] = []
        q = ",".join("?" for _ in lids)
        for kind, (attr, table, idcol, contract_kinds) in ACCRUAL_PRODUCT.items():
            mod = getattr(self.services, attr, None)
            if mod is None or not hasattr(mod, "accrue_night"):
                self.notes[kind] = f"skipped: services.{attr}.accrue_night is not built"
                continue
            self.notes.pop(kind, None)
            sql = f"SELECT t.{idcol} AS ref, t.start_date FROM {table} t JOIN accounts a ON a.account_id=t.account_id WHERE t.status='OPEN' AND a.lifetime_id IN ({q})"
            params: list = list(lids)
            if contract_kinds:
                sql += f" AND t.kind IN ({','.join('?' for _ in contract_kinds)})"
                params += list(contract_kinds)
            for r in conn.execute(sql + " ORDER BY ref", params).fetchall():
                for d in nights_from_state(conn, kind, r["ref"], _date(r["start_date"]), through):
                    out.append(f"{kind}:{r['ref']}:{d.isoformat()}")
        return out

    # -- one key
    def run(self, ctx: TxContext, key: str, services) -> dict:
        kind, ref, night = self.parse(key)
        if kind == "bfee":
            lending = module(services, "lending", "finsim.terminal.lending")
            if lending is None:
                raise RejectError("NOT_IN_THIS_BUILD", "borrow accruals need the lending module (WP-06)")
            b = ctx.conn.execute("SELECT * FROM borrows WHERE borrow_id=?", (ref,)).fetchone()
            if b is None:
                raise RejectError("NOT_FOUND", f"borrow {ref} does not exist")
            out = lending.accrue_night(ctx, services, b, night.isoformat())
            fee, reb = int(out.get("fee_c") or 0), int(out.get("rebate_c") or 0)
            return dict(out, status="DONE", posted_c=fee + reb, note=f"fee {fmt_money(fee)}, rebate {fmt_money(reb)} on {b['instrument_id']} night of {night.isoformat()}")
        if kind == "loan":
            credit = module(services, "credit", "finsim.terminal.credit")
            if credit is None:
                raise RejectError("NOT_IN_THIS_BUILD", "loan accruals need the credit module (WP-05)")
            loan = credit.loan_row(ctx.conn, ref)
            if loan is None:
                raise RejectError("NOT_FOUND", f"loan {ref} does not exist")
            if loan["kind"] != "CASH_LOAN" or loan["status"] != "OPEN":
                return {"status": "DONE", "note": f"{ref} is not an open cash loan ({loan['kind']} {loan['status']})", "posted": False}
            out = credit.post_loan_accrual(ctx, services, loan, night)
            amt = int(out.get("amount_c") or 0)
            return dict(out, status="DONE", posted_c=amt, note=f"{loan['currency']} {fmt_money(amt)} at {out.get('rate')} night of {night.isoformat()}")
        if kind in ("pb", "pbcredit"):
            acct = account_row(ctx.conn, ref)
            if acct is None or acct["status"] != "OPEN":
                raise RejectError("ACCOUNT_CLOSED", f"account {ref} is not open")
            return self._pb_night(ctx, services, acct, kind, night)
        if kind == "cashint":
            return self._cash_night(ctx, services, ref, night)
        if kind in ACCRUAL_PRODUCT:
            mod = getattr(services, ACCRUAL_PRODUCT[kind][0], None)
            if mod is None or not hasattr(mod, "accrue_night"):
                raise RejectError("NOT_IN_THIS_BUILD", f"{kind} accruals need services.{ACCRUAL_PRODUCT[kind][0]}.accrue_night")
            out = mod.accrue_night(ctx, services, kind, ref, night.isoformat()) or {}
            out.setdefault("status", "DONE")
            return out
        raise RejectError("KIND_UNKNOWN", f"accrual kind {kind!r} is not known")

    def _cash_night(self, ctx: TxContext, services, lid: str, night: date) -> dict:
        tre = f"{lid}:TREASURY"
        acct = account_row(ctx.conn, tre)
        if acct is None or acct["status"] != "OPEN":
            raise RejectError("ACCOUNT_CLOSED", f"{tre} is not open")
        base = max(0, balance_at_close(ctx.conn, tre, "1010", "USD", night))
        obs_date, sofr = sofr_for_night(services, night)
        spec = tables.RATES["treasury_cash"]
        rate = qprice(max(Decimal(spec["floor"]), Decimal(sofr) / Decimal(100) + Decimal(spec["spread"])))
        amount = accrue_segment(ctx, "cashint", tre, base, rate, int(spec["basis"]), night)
        d = night.isoformat()
        memo = (f"Treasury cash interest {lid}: SOFR {sofr} % ({obs_date.isoformat()}) - 0.50 % = {_pct(rate)}, ACT/360 on {fmt_money(base)}"
                f" (settled USD at the close of {d}), night of {d} (ACCRUAL, RATE_LAG, CASH_INT)")
        entry_id = None
        if amount > 0:
            entry_id = ctx.post(Entry("ACCRUAL", memo, [dr(tre, "1230", "USD", amount, memo="accrued cash interest receivable"),
                                                       cr(tre, "4300", "USD", amount, instrument_id=CASH_INSTRUMENT, memo=f"cash interest night of {d}")],
                                      f"accr:cashint:{lid}:USD:{d}", ref_type="treasury", ref_id=tre))
        ctx.touched_accounts.add(tre)
        ctx.event("ACCRUED", account_id=tre, ref_type="treasury", ref_id=lid, kind="cashint", night=d, amount_c=amount, base_c=base, rate=str(rate), sofr=str(sofr),
                  obs_date=obs_date.isoformat(), entry_id=entry_id)
        return {"status": "DONE", "note": f"{fmt_money(amount)} on {fmt_money(base)} at {_pct(rate)} night of {d}", "amount_c": amount, "posted_c": amount, "base_c": base,
                "rate": str(rate), "sofr": str(sofr), "obs_date": obs_date.isoformat(), "entry_id": entry_id}

    def _pb_night(self, ctx: TxContext, services, acct, kind: str, night: date) -> dict:
        credit = module(services, "credit", "finsim.terminal.credit")
        aid = acct["account_id"]
        sched = credit.pb_schedule(services, acct) if credit is not None and hasattr(credit, "pb_schedule") else dict(tables.PB_SCHEDULES.get(acct["broker"] or "", {}))
        obs_date, sofr = sofr_for_night(services, night)
        d = night.isoformat()
        prime = getattr(services, "prime", None)
        if kind == "pb":
            base = max(0, balance_at_close(ctx.conn, aid, "2700", "USD", night))
            spread = Decimal(str(sched.get("financing_spread") if sched.get("financing_spread") is not None else "0"))
            rate = qprice(Decimal(sofr) / Decimal(100) + spread)
            idem, dr_gl, cr_gl, inst = f"accr:pb:{aid}:{d}", "5100", "2300", None
            memo = (f"PB financing interest {aid}: SOFR {sofr} % ({obs_date.isoformat()}) + {_pct(spread)} = {_pct(rate)}, ACT/360 on {fmt_money(base)}"
                    f" (financing balance at the close of {d}), night of {d} (ACCRUAL, RATE_LAG)")
            hook = getattr(prime, "accrual_lines_pb", None)
        else:
            base = max(0, balance_at_close(ctx.conn, aid, "1010", "USD", night))
            spread = Decimal(str(sched.get("credit_interest_spread") if sched.get("credit_interest_spread") is not None else tables.RATES["pb_credit"]["spread"] * -1))
            rate = qprice(max(Decimal(0), Decimal(sofr) / Decimal(100) - spread))
            idem, dr_gl, cr_gl, inst = f"accr:pbcredit:{aid}:{d}", "1230", "4300", CASH_INSTRUMENT
            memo = (f"PB credit interest {aid}: max(0, SOFR {sofr} % ({obs_date.isoformat()}) - {_pct(spread)}) = {_pct(rate)}, ACT/360 on {fmt_money(base)}"
                    f" (settled USD at the close of {d}), night of {d} (ACCRUAL, RATE_LAG)")
            hook = getattr(prime, "accrual_lines_pb_credit", None)
        if hook is not None:
            lines = list(hook(ctx, dict(acct), d, (obs_date, sofr)) or [])      # WP-07 owns the lines; it maintains accrual_state itself
            amount = int(lines[0].amount_c) if lines else 0
        else:
            amount = accrue_segment(ctx, kind, aid, base, rate, 360, night)
            lines = [dr(aid, dr_gl, "USD", amount, instrument_id=inst if dr_gl == "4300" else None, memo=memo[:80]),
                     cr(aid, cr_gl, "USD", amount, instrument_id=inst if cr_gl == "4300" else None, memo=f"night of {d}")] if amount > 0 else []
        entry_id = ctx.post(Entry("ACCRUAL", memo, lines, idem, ref_type="account", ref_id=aid)) if lines else None
        ctx.touched_accounts.add(aid)
        ctx.event("ACCRUED", account_id=aid, ref_type="account", ref_id=aid, kind=kind, night=d, amount_c=amount, base_c=base, rate=str(rate), sofr=str(sofr),
                  obs_date=obs_date.isoformat(), entry_id=entry_id)
        return {"status": "DONE", "note": f"{fmt_money(amount)} on {fmt_money(base)} at {_pct(rate)} night of {d}", "amount_c": amount, "posted_c": amount, "base_c": base,
                "rate": str(rate), "entry_id": entry_id}


# ---- PAY_MONTHLY / PB_MONTHLY_MIN --------------------------------------------------------------------------------------------------
def _previous_month(today: date) -> Optional[Tuple[str, date, date]]:
    first = today.replace(day=1)
    last_prev = first - timedelta(days=1)
    return last_prev.strftime("%Y-%m"), last_prev.replace(day=1), last_prev


def monthly_key_due(clock) -> Optional[Tuple[str, date, date]]:
    """``(yyyy-mm, first, last)`` of the month to settle: the previous month, from 00:45 ET on the 1st of this one."""
    today = clock.today()
    if clock.epoch() < epoch_of(today.replace(day=1), PAY_MONTHLY_ET):
        return None
    return _previous_month(today)


class PayMonthly(Job):
    name = "PAY_MONTHLY"
    kind = "lifecycle.pay_monthly"
    when = "00:45 ET on the 1st of the month, per account with accrued balances"

    def due(self, conn, clock, cal, sessions) -> List[str]:
        m = monthly_key_due(clock)
        if m is None:
            return []
        ym, _first, last = m
        keys: List[str] = []
        for acct in open_accounts(conn):
            aid = acct["account_id"]
            has = conn.execute("SELECT 1 FROM gl_balances WHERE account_id=? AND gl IN ('2310','1410','2300','1230') AND balance_c<>0 LIMIT 1", (aid,)).fetchone()
            # the month's key exists only when something was accrued in or before that month (a lifetime that started
            # this month has nothing to settle for the previous one)
            accrued = conn.execute("SELECT 1 FROM journal_entries e JOIN journal_lines l ON l.entry_id=e.entry_id WHERE l.account_id=? AND e.kind='ACCRUAL'"
                                   " AND e.business_date<=? LIMIT 1", (aid, last.isoformat())).fetchone()
            if has is not None and accrued is not None:
                keys.append(f"{aid}:{ym}")
        return self.filter_done(conn, keys)

    def payload(self, key: str) -> dict:
        aid, ym = key.rsplit(":", 1)
        return {"key": key, "account_id": aid, "month": ym}

    def run(self, ctx: TxContext, key: str, services) -> dict:
        from . import execution
        aid, ym = key.rsplit(":", 1)
        conn = ctx.conn
        acct = account_row(conn, aid)
        if acct is None or acct["status"] != "OPEN":
            raise RejectError("ACCOUNT_CLOSED", f"account {aid} is not open")
        paid: List[dict] = []
        # borrow fees (2310) and rebates (1410), per instrument, through the lending module's settlement entry
        lending = module(services, "lending", "finsim.terminal.lending")
        rows = conn.execute("SELECT instrument_id, currency, SUM(CASE WHEN gl='2310' THEN -balance_c ELSE 0 END) fee, SUM(CASE WHEN gl='1410' THEN balance_c ELSE 0 END) reb"
                            " FROM gl_balances WHERE account_id=? AND gl IN ('2310','1410') GROUP BY instrument_id, currency HAVING fee<>0 OR reb<>0", (aid,)).fetchall()
        for r in rows:
            iid, ccy, fee, reb = r["instrument_id"], r["currency"], int(r["fee"]), int(r["reb"])
            b = conn.execute("SELECT * FROM borrows WHERE account_id=? AND instrument_id=? ORDER BY CASE status WHEN 'OPEN' THEN 0 ELSE 1 END, borrow_id LIMIT 1", (aid, iid)).fetchone()
            through = conn.execute("SELECT MAX(accrued_through) t FROM accrual_state WHERE kind IN ('bfee','rebate') AND ref_id IN (SELECT borrow_id FROM borrows WHERE account_id=? AND instrument_id=?)",
                                   (aid, iid)).fetchone()
            if b is not None and lending is not None and hasattr(lending, "settle_accrued"):
                entry_id = lending.settle_accrued(ctx, services, b, max(fee, 0), max(reb, 0), ym)
            else:
                lines: List[Line] = []
                if fee > 0:
                    if fee > max(reb, 0):
                        execution.fund(ctx, aid, ccy, fee - max(reb, 0), "pay", f"bfee:{iid}:{ym}", None, immediate=True, overdraft=True, services=services)
                    lines += [dr(aid, "2310", ccy, fee, instrument_id=iid, memo="accrued borrow fees paid"), cr(aid, "1010", ccy, fee, memo="borrow fees paid")]
                if reb > 0:
                    lines += [dr(aid, "1010", ccy, reb, memo="rebate received"), cr(aid, "1410", ccy, reb, instrument_id=iid, memo="accrued rebate received")]
                entry_id = ctx.post(Entry("PAY", f"monthly settlement of borrow fees {fmt_money(fee)} and rebates {fmt_money(reb)} on {iid} for {ym}", lines,
                                          f"pay:bfee:{aid}:{iid}:{ym}", ref_type="account", ref_id=aid)) if lines else None
            paid.append({"what": "borrow", "instrument_id": iid, "fee_c": fee, "rebate_c": reb, "entry_id": entry_id, "accrued_through": through["t"] if through else None})
        # PB financing interest 2300 -> 1010
        pb_int = balance(conn, aid, "2300", "USD")
        if pb_int > 0:
            funding = execution.fund(ctx, aid, "USD", pb_int, "pay", f"pb:{ym}", None, immediate=True, overdraft=True, services=services)
            st = accrual_state_row(conn, "pb", aid)
            entry_id = ctx.post(Entry("PAY", f"PB financing interest paid for {ym}: USD {fmt_money(pb_int)} (accrued through {st['accrued_through'] if st else 'n/a'})",
                                      [dr(aid, "2300", "USD", pb_int, memo="accrued financing interest paid"), cr(aid, "1010", "USD", pb_int, memo=f"PB interest {ym}")],
                                      f"pay:pb:{aid}:{ym}", ref_type="account", ref_id=aid))
            paid.append({"what": "pb_interest", "amount_c": pb_int, "entry_id": entry_id, "funding": funding.to_dict()})
        # cash interest / PB credit interest 1230 -> 1010
        cash_int = balance(conn, aid, "1230", "USD")
        if cash_int > 0:
            kind = "cashint" if acct["kind"] == "TREASURY" else "pbcredit"
            st = accrual_state_row(conn, kind, aid)
            entry_id = ctx.post(Entry("PAY", f"{'Treasury cash' if kind == 'cashint' else 'PB credit'} interest received for {ym}: USD {fmt_money(cash_int)}"
                                      f" (accrued through {st['accrued_through'] if st else 'n/a'})",
                                      [dr(aid, "1010", "USD", cash_int, memo=f"interest received {ym}"), cr(aid, "1230", "USD", cash_int, memo="accrued interest settled")],
                                      f"pay:cashint:{aid}:{ym}", ref_type="account", ref_id=aid))
            paid.append({"what": kind, "amount_c": cash_int, "entry_id": entry_id})
        ctx.touched_accounts.add(aid)
        if paid:
            ctx.event("PAID", account_id=aid, ref_type="pay", ref_id=key, month=ym, items=paid)
        note = ", ".join(f"{p['what']} {fmt_money(int(p.get('amount_c', p.get('fee_c', 0)) or 0))}" for p in paid) or "nothing accrued"
        return {"status": "DONE", "note": note, "paid": paid, "posted": bool(paid)}


class PbMonthlyMin(Job):
    name = "PB_MONTHLY_MIN"
    kind = "lifecycle.pb_monthly_min"
    when = "with PAY_MONTHLY (00:45 ET on the 1st), PB accounts with a monthly minimum"

    def _minimum(self, services, acct) -> int:
        credit = module(services, "credit", "finsim.terminal.credit")
        sched = credit.pb_schedule(services, acct) if credit is not None and hasattr(credit, "pb_schedule") else dict(tables.PB_SCHEDULES.get(acct["broker"] or "", {}))
        try:
            return int(sched.get("monthly_minimum_c") or 0)
        except (TypeError, ValueError):
            return 0

    def due(self, conn, clock, cal, sessions) -> List[str]:
        m = monthly_key_due(clock)
        if m is None:
            return []
        ym, _first, last = m
        month_end_ts = epoch_of(last, "23:59")
        keys = [f"{a['account_id']}:{ym}" for a in open_accounts(conn, ("PB",)) if int(a["opened_ts"]) <= month_end_ts and self._minimum(self.services, a) > 0]
        return self.filter_done(conn, keys)

    def payload(self, key: str) -> dict:
        aid, ym = key.rsplit(":", 1)
        return {"key": key, "account_id": aid, "month": ym}

    def run(self, ctx: TxContext, key: str, services) -> dict:
        from . import execution
        aid, ym = key.rsplit(":", 1)
        acct = account_row(ctx.conn, aid)
        if acct is None or acct["status"] != "OPEN":
            raise RejectError("ACCOUNT_CLOSED", f"account {aid} is not open")
        first = date.fromisoformat(ym + "-01")
        last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        # LC-4: the month's commissions only - the previous month's minimum charge (pay:pbmin:..., posted on the 1st) is not a fee
        # of this month (prime.fees_charged_c excludes it the same way)
        row = ctx.conn.execute("SELECT COALESCE(SUM(l.amount_c),0) FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id"
                               " WHERE l.account_id=? AND l.gl='5000' AND e.business_date BETWEEN ? AND ? AND e.idem_key NOT LIKE 'pay:pbmin:%'",
                               (aid, first.isoformat(), last.isoformat())).fetchone()
        fees = int(row[0] or 0)
        minimum = self._minimum(services, acct)
        hook = getattr(getattr(services, "prime", None), "monthly_minimum_line", None)
        if hook is not None:
            lines = list(hook(ctx, dict(acct), ym, fees) or [])
            shortfall = int(lines[0].amount_c) if lines else 0
        else:
            shortfall = max(0, minimum - fees)
            lines = [dr(aid, "5000", "USD", shortfall, instrument_id=PB_MIN_INSTRUMENT, memo=f"PB monthly minimum shortfall {ym}"),
                     cr(aid, "1010", "USD", shortfall, memo="monthly minimum charged")] if shortfall > 0 else []
        if not lines:
            return {"status": "DONE", "note": f"minimum met: fees {fmt_money(fees)} >= {fmt_money(minimum)} in {ym}", "fees_c": fees, "minimum_c": minimum, "posted": False}
        funding = execution.fund(ctx, aid, "USD", shortfall, "pay", f"pbmin:{ym}", None, immediate=True, overdraft=True, services=services)
        entry_id = ctx.post(Entry("FEE", f"PB monthly minimum {ym}: {fmt_money(minimum)} minus fees charged {fmt_money(fees)} = {fmt_money(shortfall)}", lines,
                                  f"pay:pbmin:{aid}:{ym}", ref_type="account", ref_id=aid))
        ctx.event("PAID", account_id=aid, ref_type="pay", ref_id=key, month=ym, minimum_c=minimum, fees_c=fees, shortfall_c=shortfall, entry_id=entry_id)
        return {"status": "DONE", "note": f"shortfall {fmt_money(shortfall)} (minimum {fmt_money(minimum)}, fees {fmt_money(fees)})", "fees_c": fees, "minimum_c": minimum,
                "shortfall_c": shortfall, "entry_id": entry_id, "funding": funding.to_dict(), "posted": True}


# ---- SWEEP_OUT --------------------------------------------------------------------------------------------------------------------
class SweepOut(Job):
    name = "SWEEP_OUT"
    kind = "lifecycle.sweep_out"
    when = "once per calendar day at the sweep_out_time_et setting (17:30), per account with sweep_out = 1 and per currency"
    retry_skipped = False

    def due(self, conn, clock, cal, sessions) -> List[str]:
        """LC-2: a key ``{account}:{ccy}:{date}`` exists only for a date whose sweep time (17:30) has passed AND at which the
        account HELD settled cash in that currency (the ledger's 1010 balance from the entries posted by then): a past day
        on which the account was empty never gets a key, so cash that arrives later (a mid-session transfer, a sale that
        settles at 00:30) is never swept back under an earlier date - it waits for its own day's 17:30.  After genuine
        downtime the days the account held cash are caught up (and run on today's balance, as before)."""
        now = clock.epoch()
        today = clock.today()
        hhmm = str(get_setting(conn, "sweep_out_time_et", "17:30") or "17:30")
        last = today if now >= epoch_of(today, hhmm) else today - timedelta(days=1)
        keys: List[str] = []
        for acct in open_accounts(conn):
            if acct["kind"] == "TREASURY" or not int(acct["sweep_out"] or 0):
                continue
            aid = acct["account_id"]
            opened = ny_of(int(acct["opened_ts"])).date()
            start = max(opened, today - timedelta(days=SWEEP_LOOKBACK_DAYS))
            if start > last:
                continue
            days = []
            d = start
            while d <= last:
                days.append(d)
                d += timedelta(days=1)
            cut0 = epoch_of(days[0], hhmm)
            held: Dict[str, int] = {r["currency"]: int(r["n"] or 0) for r in conn.execute(
                "SELECT l.currency, SUM(l.amount_c) n FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id WHERE l.account_id=? AND l.gl='1010'"
                " AND e.ts <= ? GROUP BY l.currency", (aid, cut0)).fetchall()}
            later = conn.execute("SELECT l.currency, l.amount_c, e.ts FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id WHERE l.account_id=?"
                                 " AND l.gl='1010' AND e.ts > ? AND e.ts <= ? ORDER BY e.ts", (aid, cut0, epoch_of(days[-1], hhmm))).fetchall()
            i = 0
            for d in days:
                cut = epoch_of(d, hhmm)
                while i < len(later) and int(later[i]["ts"]) <= cut:
                    held[later[i]["currency"]] = held.get(later[i]["currency"], 0) + int(later[i]["amount_c"])
                    i += 1
                for ccy in sorted(c for c, n in held.items() if n > 0):
                    keys.append(f"{aid}:{ccy}:{d.isoformat()}")
        return self.filter_done(conn, keys)

    def payload(self, key: str) -> dict:
        aid, ccy, d = key.rsplit(":", 2)
        return {"key": key, "account_id": aid, "currency": ccy, "date": d}

    def run(self, ctx: TxContext, key: str, services) -> dict:
        from . import accounting as _acc, treasury as _treasury
        aid, ccy, d = key.rsplit(":", 2)
        conn = ctx.conn
        acct = account_row(conn, aid)
        if acct is None or acct["status"] != "OPEN" or acct["kind"] == "TREASURY":
            raise RejectError("ACCOUNT_INVALID", f"{aid} is not an open trading account")
        if not int(acct["sweep_out"] or 0):
            return {"status": "SKIPPED", "quiet": True, "note": "sweep-out is switched off for this account", "posted": False}
        settled = balance(conn, aid, "1010", ccy)
        payables = balance(conn, aid, "2100", ccy) + balance(conn, aid, "2350", ccy)
        reserved = _acc.reserved_c(conn, aid, ccy, ctx.marks, getattr(services, "instruments", None))
        maint = 0
        if ccy == "USD":
            master = getattr(services, "instruments", None)
            for r in conn.execute("SELECT v.instrument_id, v.qty FROM v_positions v JOIN instruments i ON i.instrument_id=v.instrument_id WHERE v.account_id=? AND i.kind='FUTURE'", (aid,)).fetchall():
                inst = master.get(r["instrument_id"], conn) if master is not None else None
                mm = int((inst.spec or {}).get("mm_c") or 0) if inst is not None else 0
                maint += max(0, mm * abs(int(r["qty"])) - balance(conn, aid, "1300", "USD", r["instrument_id"]))
        buffer_c = int(acct["sweep_buffer_c"] or 0)
        if ccy != "USD" and buffer_c:
            fx = ctx.fx.get(ccy)
            if fx is None:
                return {"status": "SKIPPED", "quiet": True, "note": f"no USD rate for {ccy} in the mark snapshot: the buffer cannot be converted, nothing swept", "posted": False}
            buffer_c = to_c(from_c(buffer_c) / fx[0])
        excess = settled - payables - reserved - maint - buffer_c
        formula = {"settled_c": settled, "payables_c": payables, "reserved_c": reserved, "futures_maintenance_shortfall_c": maint, "buffer_c": buffer_c, "excess_c": excess,
                   "text": "excess = settled - payables pending - reserved - futures maintenance shortfall - buffer (SWEEP)"}
        if excess <= 0:
            return {"status": "SKIPPED", "quiet": True, "note": f"nothing idle: {fmt_money(settled)} - {fmt_money(payables)} - {fmt_money(reserved)} - {fmt_money(maint)}"
                                                                 f" - {fmt_money(buffer_c)} = {fmt_money(excess)} {ccy}", "formula": formula, "posted": False}
        tid = _treasury.sweep_out(ctx, aid, ccy, excess, f"idle {ccy} cash above the account's needs on {d}")
        return {"status": "DONE", "note": f"swept {ccy} {fmt_money(excess)} to Treasury ({tid})", "transfer_id": tid, "amount_c": excess, "currency": ccy, "formula": formula,
                "posted": True}


__all__ = ["Job", "SettleDue", "AccrueDaily", "PayMonthly", "PbMonthlyMin", "SweepOut", "accrue_segment", "module", "job_row", "write_job_done", "open_accounts",
           "balance_at_close", "sofr_for_night", "last_trading_day", "trading_days", "nights_between", "nights_from_state", "accrual_state_row", "monthly_key_due",
           "ACTOR", "SOURCE", "CASH_INSTRUMENT", "PB_MIN_INSTRUMENT", "RATE_LAG_MAX_DAYS", "ACCRUAL_PRODUCT"]
