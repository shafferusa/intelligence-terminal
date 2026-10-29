"""Accounting read models and integrity (TERMINAL_DESIGN.md §2.9, §6).  ONE module computes every displayed figure.

Conventions
- Everything reads ``gl_balances`` (proven equal to ``journal_lines`` by L3), ``v_positions``, ``lots``, ``settlements``,
  ``orders``, ``borrows``, ``loans``, ``margin_state``, ``otc_contracts``, ``private_loans`` and ONE mark snapshot.
  Nothing here writes, except the whole-DB ``integrity()`` runs that record ``meta.integrity_json`` and the hold state.
- Scopes (§6.0): an account id (``P1:US_EXCHANGE``), ``"{lifetime}:ALL"`` (one lifetime, current or archived),
  ``"ALL"`` (the selected book's open lifetime — ``selected_book``; the only open lifetime when there is one) and
  ``"ALL_BOOKS"`` (every OPEN lifetime).  An archived (CLOSED) lifetime's mark-dependent figures come from the
  snapshot stored at its reset, never a re-mark.
- Per account and per currency c: S_c = Σ ASSET/LIABILITY balances at cost (storage sign), B_c = their base at
  posting rates, U_c = Σ positions (mv − cost) incl. futures open trade equity, swap marks and private-loan marks.
  NAV = Σ_c fx_c(S_c + U_c); unrealised = Σ_c [fx_c(S_c + U_c) − fx_c(S_c)]; fx_result = Σ_{c≠USD} [fx_c(S_c) − B_c]
  (sentence FX_RESULT).  Since every entry balances in base, NAV = capital + transfers + realised + income −
  financing − fees + unrealised + fx_result holds to the cent, and the P&L explain has a zero residual by construction.
- The accounting day closes at 17:00 ET (sentence ACCT_DAY); a snapshot's ``snap_date`` is the New York date whose
  close it represents; "today" compares with the last snapshot dated before the current accounting day.
- Day P&L of a position = Δ(mv − cost) + Δ(realised + income − financing − fees) against that snapshot.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import config, tables
from .accounting_checks import Problem, ledger_checks
from .books import open_lifetime, open_lifetimes, split_account_id
from .clock import accounting_day, ny_of
from .commands import Touched
from .db import dumps, futures_margin_override, get_meta, get_setting, set_meta
from .instruments import EQUITY_KINDS, row_to_instrument
from . import ledger as _ledger
from .ledger import CHART, FEE_GLS, FINANCING_GLS, INCOME_GLS, NATURAL_SIGN, REALISED_GLS, natural
from .marks import shared_reader
from .quotes import MarkView, QUALITY_RANK, unavailable
from .units import fmt_money, from_c, fx_c, mul_c, qprice, to_c

ACCOUNT_ID_RE = re.compile(config.ACCOUNT_ID_RE)
NAV_ASSET_GLS = ("1010", "1200", "1210", "1220", "1225", "1230", "1250", "1300", "1310", "1320", "1330", "1360", "1400", "1410", "1420", "1500")
NAV_LIAB_GLS = ("2100", "2300", "2310", "2320", "2330", "2340", "2350", "2360", "2420", "2500", "2520", "2700", "2750", "2755")
ENCUMBRANCE_GLS = ("1400", "1300", "1310", "1320", "1330")
#: C9c / C11: the OTC contract cost GLs (premium / upfront paid, received) - per contract, OUT of NAV_ASSET_GLS / NAV_LIAB_GLS (the
#: contract's MV replaces its cost there) and out of the listed cost GLs L9 ties to lots.  ``ledger.OTC_COST_GLS`` when P4's ledger has it.
OTC_COST_GLS: Tuple[str, str] = tuple(getattr(_ledger, "OTC_COST_GLS", ("1335", "2345")))[:2]   # type: ignore[assignment]
#: PORTFOLIO-08 / C11: the P&L explain buckets.  The swap financing leg (4451) and swap coupons / CDS premium (4453) are FINANCING,
#: dividend equivalents (4452) are INCOME, whatever bucket list the ledger module carries; 4450 stays realised (return leg, STM,
#: termination, maturity).  Signs come from the storage balance, so a GL booked on either side reads right: financing and fees are
#: costs (debit positive), realised and income are gains (credit positive) - for the existing codes this equals ``natural``.
PL_FINANCING_GLS: Tuple[str, ...] = tuple(dict.fromkeys(tuple(FINANCING_GLS) + ("4451", "4453")))
PL_INCOME_GLS: Tuple[str, ...] = tuple(dict.fromkeys(tuple(g for g in INCOME_GLS if g not in PL_FINANCING_GLS) + ("4452",)))
PL_REALISED_GLS: Tuple[str, ...] = tuple(g for g in REALISED_GLS if g not in PL_FINANCING_GLS and g not in PL_INCOME_GLS)
PL_FEE_GLS: Tuple[str, ...] = tuple(FEE_GLS)


def pl_bucket(gl: str) -> Optional[str]:
    """``realised | income | financing | fees`` for a P&L GL (PORTFOLIO-08), else None."""
    if gl in PL_FINANCING_GLS:
        return "financing"
    if gl in PL_INCOME_GLS:
        return "income"
    if gl in PL_REALISED_GLS:
        return "realised"
    if gl in PL_FEE_GLS:
        return "fees"
    return None


def pl_amount(gl: str, natural_c: int) -> int:
    """A P&L GL's natural balance as its bucket counts it: realised / income credit-positive, financing / fees debit-positive (a
    cost), from the STORAGE sign - so a financing GL of kind INCOME (4451, 4453) reads as a cost when debited."""
    spec = CHART.get(gl)
    storage = NATURAL_SIGN[spec.kind] * int(natural_c) if spec is not None else int(natural_c)
    return storage if pl_bucket(gl) in ("financing", "fees") else -storage


def otc_cost_c(gl: Dict[str, Tuple[int, int]], base: bool = False) -> int:
    """C9c: one OTC contract's cost basis = natural(1335) - natural(2345) (native cents; ``base`` -> USD base): premium / upfront
    paid is a positive cost, received a negative one.  ``gl`` = the contract's ``{gl: (natural native, natural base)}``.  Worked
    (P3 money test d): a CDX upfront RECEIVED of 48,492.57 -> cost -48,492.57; with the mark -49,176.13 the unrealised is
    -49,176.13 - (-48,492.57) = -683.56, the cost of dealing, and realised stays 0.00."""
    i = 1 if base else 0
    return int(gl.get(OTC_COST_GLS[0], (0, 0))[i]) - int(gl.get(OTC_COST_GLS[1], (0, 0))[i])
CLASS_OF_KIND = {"EQUITY": "securities", "ETF": "securities", "ADR": "securities", "REIT": "securities", "PREFERRED": "securities", "BOND": "securities",
                 "OPTION": "listed options", "FUTURE": "futures", "FX": "FX", "PRIVATE": "private / illiquid", "SWAP": "OTC derivatives",
                 "PRIVATE_LOAN": "private credit"}
#: PORTFOLIO-04: the finer asset class of a position row (the class above is the grouping the Portfolio has always used)
ASSET_CLASS_OF_KIND = {"EQUITY": "Equity", "ADR": "Equity", "REIT": "Equity", "PREFERRED": "Equity", "ETF": "ETF", "BOND": "Bond", "OPTION": "Option",
                       "FUTURE": "Future", "FX": "FX", "FX_FORWARD": "FX forward", "PRIVATE": "Private equity", "PRIVATE_LOAN": "Private credit"}
OTC_ASSET_CLASS = {"TRS": "OTC TRS", "IRS": "OTC IRS", "CDX": "OTC CDX", "XCCY": "OTC cross-currency swap", "EQ_OPTION": "OTC option", "FX_OPTION": "OTC option",
                   "EQ_FORWARD": "OTC forward", "FX_FORWARD": "OTC forward"}
#: PORTFOLIO-04: who stands on the other side of a listed position (simulated executions; the clearing house is named, never a real fill)
CLEARING_OF_KIND = {"OPTION": "exchange-cleared (OCC)", "FUTURE": "exchange-cleared (CME)", "EQUITY": "exchange-cleared (NSCC)", "ADR": "exchange-cleared (NSCC)",
                    "REIT": "exchange-cleared (NSCC)", "PREFERRED": "exchange-cleared (NSCC)", "ETF": "exchange-cleared (NSCC)", "BOND": "dealer market (simulated dealer)",
                    "PRIVATE": "negotiated private transaction", "FX": "FX dealer (simulated)", "FX_FORWARD": "FX dealer (simulated)"}
SECTORS_TABLE = "instrument_sectors"
UNCLASSIFIED = "unclassified"


def asset_class_of(kind: Optional[str], extra: Optional[dict] = None) -> str:
    """PORTFOLIO-04: Equity / ETF / Bond / Option / Future / FX / FX forward / OTC TRS / OTC IRS / OTC CDX / OTC option / OTC forward /
    Private equity / Private credit."""
    k = str(kind or "")
    if k == "SWAP":
        return OTC_ASSET_CLASS.get(str((extra or {}).get("kind") or ""), "OTC")
    return ASSET_CLASS_OF_KIND.get(k, k.replace("_", " ").title() if k else "Other")


def counterparty_of(kind: Optional[str], acct: Optional[dict], extra: Optional[dict] = None, borrow: Optional[dict] = None, qty: int = 0) -> str:
    """PORTFOLIO-04: the counterparty of a position row - a short's stock-borrow lender (worded through ``lender_label``: "street lender
    (assumed terms)"), an OTC contract's dealer (the prime broker, bilateral under the ISDA / CSA), a private loan's borrower, else the
    clearing of a listed instrument ("exchange-cleared (OCC)" for options, "(CME)" for futures, "(NSCC)" for stocks and ETFs)."""
    k = str(kind or "")
    ex = extra or {}
    if borrow and borrow.get("lender") and qty < 0:
        return lender_label(borrow.get("lender"))
    if k == "SWAP":
        broker = str(ex.get("broker") or (acct or {}).get("broker") or "")
        name = config.PRIME_BROKERS.get(broker, broker) if broker else ((acct or {}).get("name") or "dealer")
        return f"{name} (bilateral OTC, ISDA / CSA)"
    if k == "PRIVATE_LOAN":
        return str(ex.get("borrower") or "private borrower")
    if ex.get("desk_forward"):
        return "FX dealer (simulated)"
    return CLEARING_OF_KIND.get(k, "exchange-cleared")


def owner_sectors(conn: sqlite3.Connection) -> Dict[str, str]:
    """PORTFOLIO-19: the owner's sector classifications (``instrument.classify``; table made by ``risk.bind``); {} without the table."""
    try:
        return {r[0]: r[1] for r in conn.execute(f"SELECT instrument_id, sector FROM {SECTORS_TABLE}").fetchall() if r[1]}
    except sqlite3.Error:
        return {}


def sector_of(inst, owner: Optional[Dict[str, str]] = None) -> Tuple[Optional[str], Optional[str]]:
    """``(sector, source)`` of an instrument: the owner's classification ("owner"), else the instrument master's (``spec.sector``,
    "master"), else ``(None, None)`` - shown as "unclassified"."""
    if inst is None:
        return None, None
    iid = getattr(inst, "instrument_id", None)
    if owner and iid in owner:
        return owner[iid], "owner"
    sec = (getattr(inst, "spec", None) or {}).get("sector")
    return (str(sec), "master") if sec else (None, None)


BUY_SIDES = ("BUY", "BUY_TO_COVER", "BTO", "BTC")
WORKING = ("WORKING", "PARTIAL", "WAITING_PARENT")
PERIODS = ("TODAY", "MTD", "YTD", "ITD")


class ScopeError(KeyError):
    """Unknown scope / account (HTTP 404)."""


# ---- views ------------------------------------------------------------------------------------------------------------
def _d(obj) -> dict:
    out = {}
    for k, v in obj.__dict__.items():
        if isinstance(v, Decimal):
            out[k] = str(v)
        elif hasattr(v, "to_dict"):
            out[k] = v.to_dict()
        elif isinstance(v, list):
            out[k] = [x.to_dict() if hasattr(x, "to_dict") else x for x in v]
        else:
            out[k] = v
    return out


# The liquidity definition (§6.0 / §6.1 as settled on 2026-10-21 with directive amendment -7; module function
# ``available_c``).  Shown verbatim as the hover text of every "available" figure.
AVAILABLE_TEXT = ("available = max(0, settled cash + receivables due by the settlement date - payables - cash reserved by working orders): "
                  "the account's own spendable cash, never shown below zero. What working orders and payables need beyond it is shown "
                  "apart as 'reserved (Treasury sweep covers N)': exactly that shortfall moves from Treasury when the order fills or the "
                  "payable settles (directive amendment -7); a part Treasury cannot cover is 'unfunded'; settled cash below zero is "
                  "'overdrawn' (an open funding shortfall, s11.6). Buying power = available + the Treasury available and PB headroom "
                  "left after what this account's working orders and payables already need beyond its own cash: max(0, own spendable "
                  "+ Treasury available + PB headroom); a new order beyond it is never accepted as working (directive amendment -8): it queues, "
                  "waiting for funds and reserving nothing, and executes automatically when cash frees up.")

# What a NEW order can draw (``buying_power_split``; ``BuyingPower.formula``; the ticket preview's fundability).
BUYING_POWER_TEXT = ("buying power = max(0, own spendable cash + Treasury available + PB headroom) = available + Treasury left + PB left: "
                     "what this account's working orders, payables and any overdraft need beyond its own cash ('committed') comes off "
                     "Treasury available first and PB headroom second, exactly as their fills will sweep it; a new order whose shortfall "
                     "exceeds Treasury left + PB left is never accepted as working (directive amendment -8): it queues SUSPENDED, waiting "
                     "for funds and reserving nothing, and executes automatically when cash frees up, so a working order is never "
                     "suspended for cash this account had already promised")


@dataclass
class CashView:
    """One account, one currency.  ``available_c`` is the floored figure of ``available_c()``; ``spendable_c`` the signed
    one; ``needs_c`` / ``sweep_covers_c`` / ``unfunded_c`` / ``overdrawn_c`` split what lies beyond own cash
    (``liquidity_split``; sentence ``definition``)."""
    account_id: str
    currency: str
    settled_c: int
    receivable_c: int
    payable_c: int
    pending_c: int
    held_at_lender_c: int
    margin_c: int
    reserved_c: int
    available_c: int
    accrued_c: int = 0
    otc_margin_c: int = 0
    spendable_c: int = 0
    needs_c: int = 0
    sweep_covers_c: int = 0
    unfunded_c: int = 0
    overdrawn_c: int = 0
    options_required_c: int = 0        # SR-13: the short-option margin requirement reserved in this currency (inside reserved_c)
    orders_reserved_c: int = 0         # what working orders reserve (reserved_c minus the option margin)
    adjustments: List[dict] = field(default_factory=list)   # C9b: [{key, label, amount_c}] - settled cash that may not be spent (restricted collateral ...)
    adjustments_total_c: int = 0       # C9b: their total; available / spendable / the split are computed on settled_c - adjustments_total_c
    definition: str = AVAILABLE_TEXT
    to_dict = _d


@dataclass
class AvailableView:
    """§6.0 / §6.1 liquidity of one account in one currency (``Accounting.available_view``; ``liquidity_split``).  ``settled_c``
    is the ledger's settled cash (GL 1010); ``adjustments_total_c`` what the C9b cash adjusters restrict of it - the split is
    computed on ``settled_c - adjustments_total_c``."""
    account_id: str
    currency: str
    settle_date: str
    settled_c: int
    receivable_due_c: int
    payable_c: int
    reserved_c: int
    spendable_c: int
    available_c: int
    needs_c: int
    treasury_available_c: int
    sweep_covers_c: int
    unfunded_c: int
    overdrawn_c: int
    adjustments_total_c: int = 0
    definition: str = AVAILABLE_TEXT
    to_dict = _d


@dataclass
class BuyingPower:
    """What a NEW order / trade of one account can draw in one currency (``buying_power_split``; ``BUYING_POWER_TEXT``).

    ``available_c`` is the account's own available cash floored at zero (``AVAILABLE_TEXT``, the figure the sidebar shows);
    ``committed_c`` what its working orders, payables and any overdraft need beyond its own cash (they sweep it at their
    fills); ``treasury_available_c`` / ``pb_headroom_c`` what Treasury / the PB facility still have for a NEW order once
    ``committed_c`` is set aside (``treasury_gross_c`` / ``pb_headroom_gross_c`` before it: the Treasury page's available
    and the §12.4 headroom); ``total_c`` = available + Treasury left + PB left.  ``orders_preview`` reads exactly
    ``available_c`` / ``treasury_available_c`` / ``pb_headroom_c``: its funding block shows the floored available, the
    sweep-in is the order's own shortfall (what its fill will move) and it is fundable iff ``need − spendable ≤ Treasury +
    PB`` — the rule of the signed figure before the 2026-10-21 floor."""
    account_id: str
    currency: str
    available_c: int
    treasury_available_c: int
    pb_headroom_c: int
    total_c: int
    settle_date: Optional[str]
    committed_c: int = 0
    treasury_gross_c: int = 0
    pb_headroom_gross_c: int = 0
    immediate_c: int = 0             # USX-17: own cash an IMMEDIATE posting may use (futures / OTC initial margin): immediate_available_c
    formula: str = BUYING_POWER_TEXT
    to_dict = _d


@dataclass
class LotView:
    lot_id: int
    side: str
    open_date: str
    open_ts: int
    qty_open: int
    qty_remaining: int
    unit_cost: Decimal
    cost_c: int
    open_fill_id: str
    lock_until: Optional[str]
    currency: str
    to_dict = _d


@dataclass
class PositionView:
    account_id: str
    instrument_id: str
    symbol: str
    name: str
    kind: str
    klass: str
    currency: str
    qty: int
    cost_c: int
    avg_px: Optional[Decimal]
    mark: MarkView
    mv_c: Optional[int]
    mv_base_c: Optional[int]
    notional_c: Optional[int]
    unrealised_c: Optional[int]
    unrealised_base_c: Optional[int]
    realised_itd_c: int
    income_itd_c: int
    financing_itd_c: int
    fees_itd_c: int
    income_pending_c: int
    day_pnl_c: Optional[int]
    weight: Optional[Decimal]
    days_held: Optional[int]
    settle_px: Optional[Decimal]
    borrow: Optional[dict]
    lots: List[LotView] = field(default_factory=list)
    extra: dict = field(default_factory=dict)
    asset_class: str = ""                 # PORTFOLIO-04: Equity / ETF / Bond / Option / Future / FX / OTC TRS / IRS / CDX / option / forward ...
    counterparty: Optional[str] = None    # PORTFOLIO-04: a short's borrow lender (worded), an OTC contract's dealer, a listed name's clearing
    total_pnl_c: Optional[int] = None     # PORTFOLIO-04: unrealised + realised + income + financing + fees to date (base; None while unpriced)
    sector: Optional[str] = None          # PORTFOLIO-19: the owner's classification, else the instrument master's; None = unclassified
    sector_source: Optional[str] = None   # "owner" | "master" | None
    to_dict = _d


@dataclass
class ClosedPositionView:
    """PORTFOLIO-03: one closed position - a listed round trip (from a flat quantity back to flat, fills in time order; a fill that
    crosses zero closes one and opens the next), a terminated / matured OTC contract or a returned stock borrow.  Money in base
    cents; ``fees_c`` / ``financing_c`` are positive costs; ``total_pnl_c`` = realised + income - financing - fees."""
    account_id: str
    instrument_id: str
    symbol: str
    name: str
    kind: str
    asset_class: str
    side: str
    currency: str
    qty: int
    open_date: str
    close_date: str
    open_ts: int
    close_ts: int
    avg_open_px: Optional[Decimal]
    avg_close_px: Optional[Decimal]
    realised_c: int
    fees_c: int
    income_c: int
    financing_c: int
    total_pnl_c: int
    days_held: int
    open_fill_ids: List[str]
    closing_fill_ids: List[str]
    how: str
    counterparty: Optional[str]
    source: str
    note: str = ""
    to_dict = _d


@dataclass
class NavView:
    scope: str
    nav_c: int
    assets_c: int
    liabilities_c: int
    quality: str
    partial: bool
    missing: List[str]
    as_of: int
    cash_c: int
    pending_c: int
    mv_long_c: int
    mv_short_c: int
    unrealised_c: int
    fx_result_c: int
    capital_c: int
    transfers_c: int
    realised_c: int
    income_c: int
    financing_c: int
    fees_c: int
    retained_c: int
    by_account: Dict[str, int]
    lifetime_ids: List[str]
    archived: bool = False
    fx_missing: List[str] = field(default_factory=list)
    note: str = ""
    to_dict = _d


@dataclass
class BalanceSheet:
    scope: str
    rows: List[dict]
    assets_c: int
    liabilities_c: int
    nav_c: int
    rounding_c: int
    memo: dict
    quality: str
    partial: bool
    as_of: int
    to_dict = _d


@dataclass
class PnlView:
    scope: str
    period: str
    nav_c: int
    nav0_c: int
    flows_c: int
    pnl_c: int
    explained_c: int
    residual_c: int
    components: dict
    baseline: dict
    degraded: bool
    quality: str
    partial: bool
    to_dict = _d


@dataclass
class TreasuryView:
    lifetime_id: str
    account_id: str
    cash_by_ccy: Dict[str, int]
    accrued_c: int
    capital_c: int
    capital_by_ccy: Dict[str, int]
    advances_c: int
    advances_by_account: Dict[str, int]
    retained_c: int
    available_c: int
    reserve_c: int
    nav_c: int
    encumbrance_c: int
    identity_ok: bool
    formulas: dict
    to_dict = _d


@dataclass
class TxnRow:
    entry_id: int
    ts: int
    ts_ny: str
    business_date: str
    kind: str
    memo: str
    ref_type: Optional[str]
    ref_id: Optional[str]
    command_id: int
    request_id: Optional[str]
    instrument_id: Optional[str]
    qty: Optional[int]
    price: Optional[str]
    gross_c: Optional[int]
    fee_c: Optional[int]
    origin: Optional[str]
    net_cash: Dict[str, int]
    accounts: List[str]
    counterparty: Optional[str]
    lines: List[dict]
    to_dict = _d


@dataclass
class ObligationsView:
    account_id: str
    settlements: List[dict]
    pending_in: Dict[str, int]
    pending_out: Dict[str, int]
    next_due: Optional[str]
    borrows: List[dict]
    loans: List[dict]
    margin: dict
    collateral_c: int
    otc: List[dict]
    to_dict = _d


@dataclass
class LiquidityView:
    scope: str
    excess_liquidity_c: int
    encumbrance_c: int
    treasury_available_c: int
    accounts_available_c: int
    maintenance_shortfall_c: int
    formulas: dict
    detail: dict
    to_dict = _d


@dataclass
class IntegrityReport:
    ok: bool
    hold: bool
    checked_at: int
    mode: str
    lifetime_id: Optional[str]
    problems: List[Problem]
    ms: int
    holds: Dict[str, bool] = field(default_factory=dict)
    warnings: int = 0
    checks: List[dict] = field(default_factory=list)     # BM-26: one row per check run {code, name, ok, count, errors, warnings, message}

    @property
    def failing(self) -> List[str]:
        return sorted({p.id for p in self.problems if p.severity == "ERROR"})

    def to_dict(self) -> dict:
        return {"ok": self.ok, "hold": self.hold, "checked_at": self.checked_at, "mode": self.mode, "lifetime_id": self.lifetime_id,
                "problems": [p.to_dict() for p in self.problems], "ms": self.ms, "holds": dict(self.holds), "failing": self.failing,
                "checks": [dict(c) for c in self.checks]}


@dataclass
class Scope:
    key: str
    kind: str                      # ACCOUNT | LIFETIME | ALL_BOOKS
    accounts: List[dict]
    lifetime_ids: List[str]
    archived: bool
    snapshot_key: Optional[str]


# ---- module-level helpers (also used by handlers inside a transaction) ----------------------------------------------
def _instrument(conn: sqlite3.Connection, master, iid: str, cache: dict):
    if iid in cache:
        return cache[iid]
    inst = None
    if master is not None:
        inst = master.get(iid, conn)
    if inst is None:
        row = conn.execute("SELECT * FROM instruments WHERE instrument_id=?", (iid,)).fetchone()
        inst = row_to_instrument(row) if row else None
    cache[iid] = inst
    return inst


def _mark_price(snapshot, iid: str) -> Tuple[Optional[Decimal], MarkView]:
    if snapshot is None:
        return None, unavailable(iid, "no mark snapshot")
    view = snapshot.mark(iid)
    if view is None or view.mark is None or view.quality == "unavailable":
        return None, view or unavailable(iid)
    return view.mark, view


def futures_rates_c(conn: sqlite3.Connection, inst) -> Tuple[int, int]:
    """``(initial, maintenance)`` margin per contract of a future for NEW requirements (SR-11): the owner's Settings > Margins &
    limits override of its root (``db.futures_margin_override``), else the static contract table on the row
    (``spec.im_c`` / ``spec.mm_c``, maintenance 90 % of initial when the table has none; decisions s7.4)."""
    spec = inst.spec or {}
    ov = futures_margin_override(conn, str(spec.get("root") or ""))
    if ov is not None:
        return int(ov[0]), int(ov[1])
    im = int(spec.get("im_c") or 0)
    mm = int(spec.get("mm_c") or 0) or (im * 9 // 10)
    return im, mm


def reserved_c(conn: sqlite3.Connection, account_id: str, ccy: str, snapshot, master=None) -> int:
    """§6.1: cash reserved by WORKING / PARTIAL / WAITING_PARENT orders of the account in ``ccy``.  BUY sides reserve
    remaining × ref_px × multiplier × scale + est. fee (ref_px = limit for LIMIT/STOP_LIMIT, else last × 1.02);
    SELL_SHORT reserves the cash its fill will take (SHORT_COLLATERAL, hotfix 1.0.3.1): the 102 % collateral
    1.02 × remaining × last less the sale's own proceeds remaining × last − est. fee = the 2 % excess + fee.  A SUSPENDED
    order reserves nothing (FUNDS_WAIT: it waits for cash and blocks no other order), nor do the WAITING_PARENT hedge legs
    of a SUSPENDED primary (they are counted again with the primary when ``orders.plan_funds_resume`` resumes it).  The
    account's short-option margin requirement
    (``margin_state``, USD) is reserved too (SR-13, decision s7.6: a reservation, no cash movement).  FUTURES (ORC-3) reserve what their fill
    will take from cash, never the notional: ``max(0, fee + IM × (|P + q| − |P|))`` with the position P running over the
    account's working orders of that contract in order (a BUY covering a short releases margin and reserves nothing; a
    SELL that opens a short reserves its initial margin).  Worked: short 1 ESZ26, a working BUY 1 -> |0| − |−1| < 0 ->
    0.00 reserved (the old rule reserved 1 × 7,750 × 50 = 387,500.00); flat, BUY 2 -> 2.25 × 2 + 17,500.00 × 2 = 35,004.50."""
    cache: dict = {}
    total = 0
    rows = conn.execute("SELECT o.order_id, o.account_id, o.instrument_id, o.side, o.qty, o.filled_qty, o.order_type, o.limit_px FROM orders o"
                        " LEFT JOIN orders p ON p.order_id = o.parent_order_id WHERE o.account_id=?"
                        " AND (o.status IN ('WORKING','PARTIAL') OR (o.status='WAITING_PARENT' AND (p.status IS NULL OR p.status <> 'SUSPENDED')))"
                        " ORDER BY o.created_ts, o.order_id", (account_id,)).fetchall()
    fut_pos: Dict[str, int] = {}
    if ccy == "USD":
        # SR-13 (decision s7.6): the short-option margin requirement is a reservation on the account's USD cash
        ms = conn.execute("SELECT options_required_c FROM margin_state WHERE account_id=?", (account_id,)).fetchone()
        total += max(0, int(ms["options_required_c"] or 0)) if ms is not None else 0
    for r in rows:
        inst = _instrument(conn, master, r["instrument_id"], cache)
        if inst is None or inst.currency != ccy:
            continue
        total += reserved_for_order(conn, r, snapshot, master, fut_pos=fut_pos, cache=cache)
    return total


def reserved_for_order(conn: sqlite3.Connection, order_row, snapshot, master=None, *, fut_pos: Optional[Dict[str, int]] = None,
                       cache: Optional[dict] = None) -> int:
    """C9a (FINSIM1_PLAN): the cash ONE working order reserves, in its instrument's currency - the per-row body of ``reserved_c``
    (whose totals are this function summed over the account's WORKING / PARTIAL / WAITING_PARENT orders plus the SR-13 option
    margin, which is an account figure, not an order's).  ``order_row`` is an ``orders`` row (or a dict) with at least
    ``account_id, instrument_id, side, qty, filled_qty, order_type, limit_px``; the order's status is NOT checked here (the
    caller decides which orders reserve).  A FUTURE reserves against the position running over the account's earlier working
    orders of that contract: pass the same ``fut_pos`` dict for every order of one account in ``created_ts`` order (``reserved_c``
    does); without it the position is the account's current one (``v_positions``) and the dict is not kept.  Worked: a LIMIT BUY
    100 AAPL @ 300.00 reserves 100 x 300.00 + fee 1.00 = 30,001.00; a MARKET BUY 100 with last 339.50 reserves 100 x 346.29 + 1.00
    = 34,630.00; an order with nothing left to fill reserves 0.00."""
    cache = {} if cache is None else cache
    inst = _instrument(conn, master, order_row["instrument_id"], cache)
    if inst is None:
        return 0
    remaining = int(order_row["qty"]) - int(order_row["filled_qty"] or 0)
    if remaining <= 0:
        return 0
    side = order_row["side"]
    if inst.kind == "FUTURE":
        iid = inst.instrument_id
        pos = fut_pos if fut_pos is not None else {}
        if iid not in pos:
            pr = conn.execute("SELECT qty FROM v_positions WHERE account_id=? AND instrument_id=?", (order_row["account_id"], iid)).fetchone()
            pos[iid] = int(pr["qty"] or 0) if pr is not None else 0
        p = pos[iid]
        q = remaining if side in BUY_SIDES else -remaining
        im, _mm = futures_rates_c(conn, inst)
        fee, _ = tables.fee_c(inst.kind, remaining)
        pos[iid] = p + q
        return max(0, int(fee) + im * (abs(p + q) - abs(p)))
    last, _view = _mark_price(snapshot, inst.instrument_id)
    if side in BUY_SIDES:
        if order_row["order_type"] in ("LIMIT", "STOP_LIMIT") and order_row["limit_px"]:
            ref = Decimal(order_row["limit_px"])
        elif last is not None:
            ref = last * Decimal("1.02")
        else:
            return 0
        fee, _ = tables.fee_c(inst.kind, remaining)
        return mul_c(remaining, ref, inst.multiplier, inst.price_scale) + fee
    if side == "SELL_SHORT" and last is not None:
        coll = mul_c(remaining, last * config.COLLATERAL_PCT, inst.multiplier, inst.price_scale)
        fee, _ = tables.fee_c(inst.kind, remaining)
        return coll - max(0, min(mul_c(remaining, last, inst.multiplier, inst.price_scale) - fee, coll))
    return 0


# ---- C9b: cash adjusters (FINSIM1_PLAN) --------------------------------------------------------------------------------
#: key -> (label, fn(conn, account_id, ccy, snapshot, master) -> cents).  Module-global (like api's refusal recorders): a module's
#: ``bind(rt)`` registers its adjuster once; a repeated registration of the same key replaces it (the latest definition wins).
_CASH_ADJUSTERS: Dict[str, Tuple[str, Callable]] = {}


def add_cash_adjuster(key: str, label: str, fn: Callable) -> None:
    """C9b: register ``fn(conn, account_id, ccy, snapshot, master) -> cents``, cash of the account that sits in its settled
    balance (GL 1010) but may not be spent - e.g. P6's "restricted (collateral received)" (D9 / C21).  Every registered
    adjuster is subtracted from settled cash inside ``_available_parts``, so ``available_c``, ``spendable_c``,
    ``immediate_available_c`` and buying power exclude it; ``CashView.adjustments`` lists them ``[{key, label, amount_c}]`` and
    ``adjustments_c`` gives SWEEP_OUT their total.  An adjuster only ever RESTRICTS cash: a negative answer counts as 0.  An
    adjuster that raises is a defect and the exception propagates (a figure that cannot be computed is never guessed as 0)."""
    if not key or not callable(fn):
        raise ValueError("a cash adjuster needs a key and a callable")
    _CASH_ADJUSTERS[str(key)] = (str(label or key), fn)


def remove_cash_adjuster(key: str) -> bool:
    """Unregister an adjuster (tests); True when it was registered."""
    return _CASH_ADJUSTERS.pop(str(key), None) is not None


def cash_adjusters() -> List[Tuple[str, str]]:
    """``[(key, label)]`` of the registered adjusters, in registration order."""
    return [(k, v[0]) for k, v in _CASH_ADJUSTERS.items()]


def cash_adjustments(conn: sqlite3.Connection, account_id: str, ccy: str, snapshot, master=None, *, skip=()) -> List[dict]:
    """C9b: ``[{key, label, amount_c}]`` - every registered adjuster's restriction on the account's ``ccy`` cash (0 rows
    included, so a page can say "restricted 0.00"); ``skip`` names adjuster keys to leave out (for example a payment to the
    CSA counterparty that may use that counterparty's collateral: C21 csa_netting)."""
    out = []
    for key, (label, fn) in list(_CASH_ADJUSTERS.items()):
        if key in skip:
            continue
        out.append({"key": key, "label": label, "amount_c": max(0, int(fn(conn, account_id, ccy, snapshot, master) or 0))})
    return out


def adjustments_c(conn: sqlite3.Connection, account_id: str, ccy: str, snapshot, master=None, *, skip=()) -> int:
    """C9b: the total the registered adjusters restrict on the account's ``ccy`` cash (natural cents, >= 0); SWEEP_OUT keeps it
    in the account (restricted cash is never swept)."""
    if not _CASH_ADJUSTERS:
        return 0
    return sum(a["amount_c"] for a in cash_adjustments(conn, account_id, ccy, snapshot, master, skip=skip))


def _available_parts(conn: sqlite3.Connection, account_id: str, ccy: str, settle_date: Optional[date], snapshot, master=None,
                     now: Optional[datetime] = None, *, skip_adjusters=()) -> Tuple[date, int, int, int, int]:
    """``(settle_date, settled, receivables due by settle_date, payables, reserved)`` in natural cents.  ``settled`` is the
    account's USABLE settled cash: the GL 1010 balance less every registered cash adjuster (C9b: restricted collateral and
    the like; ``skip_adjusters`` names keys to leave out)."""
    if settle_date is None:
        if now is None:
            # never the machine's local date: every caller passes the injected clock (Accounting passes clock.now(),
            # handlers pass ctx.now); a missing clock is a programming error that must surface, not a silent wrong day
            raise ValueError("available_c needs settle_date or now (the injected clock)")
        settle_date = accounting_day(now)
    settled = natural("1010", _sum(conn, "SELECT COALESCE(SUM(balance_c),0) FROM gl_balances WHERE account_id=? AND gl='1010' AND currency=?", (account_id, ccy)))
    settled -= adjustments_c(conn, account_id, ccy, snapshot, master, skip=skip_adjusters)
    recv = _sum(conn, "SELECT COALESCE(SUM(amount_c),0) FROM settlements WHERE account_id=? AND currency=? AND status='PENDING' AND amount_c > 0 AND due_date <= ?",
                (account_id, ccy, settle_date.isoformat()))
    pay = natural("2100", _sum(conn, "SELECT COALESCE(SUM(balance_c),0) FROM gl_balances WHERE account_id=? AND gl IN ('2100','2350') AND currency=?", (account_id, ccy)))
    return settle_date, settled, recv, pay, reserved_c(conn, account_id, ccy, snapshot, master)


def spendable_c(conn: sqlite3.Connection, account_id: str, ccy: str, settle_date: Optional[date], snapshot, master=None, now: Optional[datetime] = None, *,
                skip_adjusters=()) -> int:
    """The SIGNED own-cash figure ``settled + Σ receivables due ≤ settle_date − payables − reserved`` (§6.1).  Negative when
    working orders' reservations and payables exceed the account's own cash; shown only split (``liquidity_split``), and
    the fundability of a NEW order counts it (``buying_power`` / ``buying_power_split``: −spendable is ``committed``).
    ``settled`` is usable settled cash (C9b: the registered cash adjusters subtracted)."""
    _d0, settled, recv, pay, reserved = _available_parts(conn, account_id, ccy, settle_date, snapshot, master, now, skip_adjusters=skip_adjusters)
    return settled + recv - pay - reserved


def available_c(conn: sqlite3.Connection, account_id: str, ccy: str, settle_date: Optional[date], snapshot, master=None, now: Optional[datetime] = None, *,
                skip_adjusters=()) -> int:
    """§6.0 / §6.1 available cash: the account's own spendable cash FLOORED AT ZERO (``AVAILABLE_TEXT``; sentence
    AVAILABLE; directive amendment -7)::

        available = max(0, settled + Σ receivables due ≤ settle_date − payables − reserved)

    ``reserved`` is the cash held for WORKING / PARTIAL / WAITING_PARENT orders (``reserved_c``).  What those orders and the
    payables need beyond the account's own cash is NOT negative available: it is the "reserved (Treasury sweep covers N)"
    figure of ``liquidity_split`` — exactly that shortfall moves from Treasury when the order fills or the payable settles
    (``execution.fund``: own available first, then one linked sweep-in).  So a fill sweeps exactly its own shortfall
    against this figure (the filling order's own reservation is released first, ``orders._fill_order``) and the ticket
    preview's funding block (``buying_power().available_c``) shows the same number.  This floored figure is NOT a new
    order's fundability: what the account has already promised beyond its own cash is ``committed`` and comes off the
    Treasury / PB part of ``buying_power`` (``buying_power_split``), so a second order cannot be accepted on cash the first
    one will sweep (the 2026-10-21 regression: accepted, then SUSPENDED at the open).  Settled cash below zero after an
    unfunded call is ``overdrawn`` (an open FUNDING_SHORTFALL, §11.6), never hidden.  ``settle_date`` None -> today's
    accounting day."""
    return max(0, spendable_c(conn, account_id, ccy, settle_date, snapshot, master, now, skip_adjusters=skip_adjusters))


def immediate_available_c(conn: sqlite3.Connection, account_id: str, ccy: str, settle_date: Optional[date], snapshot, master=None,
                          now: Optional[datetime] = None, *, skip_adjusters=()) -> int:
    """What may leave the account's SETTLED cash now (sentence AVAILABLE, the immediate rule; MONEY-6, C12 c)::

        immediate = max(0, min(settled, settled + receivables due by settle_date - payables - reserved))

    The movement leaves 1010 at once, so it can never spend more than the settled balance (L18), and it never spends cash
    the account's payables and working orders already need (net of what settles in by ``settle_date``).  Internal
    transfers (``treasury.h_transfer``), the Treasury's sweep-ins (``treasury_available_c``) and the initial margin a fill
    or a booking posts at once (``execution.fund(commitments=True)``: futures IM, OTC IM) measure against it.
    ``settle_date`` None -> today's accounting day.  Worked (s7, Wed 18:00 = accounting day Thu): settled 0.00, a sale's
    receivable 344,995.00 due Thu -> available 344,995.00 but immediate min(0.00, 344,995.00) = 0.00."""
    _d0, settled, recv, pay, reserved = _available_parts(conn, account_id, ccy, settle_date, snapshot, master, now, skip_adjusters=skip_adjusters)
    return max(0, min(settled, settled + recv - pay - reserved))


def view_settle_date(cal, now: datetime) -> date:
    """USX-10: the settlement date of the account views (Cash & financing, header, sidebar, ``Accounting.cash`` /
    ``available_view`` / ``buying_power`` without an explicit date): the next standard settlement date after today's
    accounting day, ``cal.settlement_date(accounting day, 1)`` (T+1, what a new US order settles at).  Receivables due
    by it offset the payables, so a same-day round trip (BUY then SELL, both settling T+1) leaves available at settled
    cash less the round trip's cost instead of 0.00 with a phantom Treasury sweep; the preview and the fill read the
    same formula at the order's own settle date."""
    d = accounting_day(now)
    if cal is None:
        return d
    try:
        return cal.settlement_date(d, 1)
    except Exception:   # noqa: BLE001 - a calendar without settlement_date keeps the accounting day (never a guess)
        return d


def treasury_available_c(conn: sqlite3.Connection, lifetime_id: str, ccy: str, snapshot, master=None, now: Optional[datetime] = None) -> int:
    """The Treasury cash a sweep-in may move now (BM-01, C12 a): the Treasury's immediate available in that currency minus
    the reserve of the lifetime's book (``books.treasury_reserve_c``, USD only).  Signed: a reserve above the cash gives a
    negative figure (the Treasury page shows it floored); ``execution.fund`` sweeps ``min(shortfall, max(0, it))``, the
    preview / buying power read the same function, so the previewed sweep-in is the executed one."""
    from .books import treasury_reserve_c as _reserve
    tre = f"{lifetime_id}:TREASURY"
    return immediate_available_c(conn, tre, ccy, None, snapshot, master, now) - _reserve(conn, lifetime_id, ccy)


def liquidity_split(settled_c: int, receivable_due_c: int, payable_c: int, reserved_c_: int, treasury_available_c: int) -> Dict[str, int]:
    """The display split of one account / currency (``AVAILABLE_TEXT``), all natural cents::

        spendable    = settled + receivables due − payables − reserved           (signed; the §6.1 formula)
        available    = max(0, spendable)
        overdrawn    = max(0, −settled)                                          (open funding shortfall, §11.6)
        needs        = max(0, −spendable − overdrawn)                            (orders + payables beyond own cash)
        sweep_covers = min(needs, max(0, Treasury available in the currency))    (moves at the fill / settlement)
        unfunded     = needs − sweep_covers                                      (no Treasury cash for it: red)

    Worked: settled 0, one resting BUY reserving 347.29, Treasury 1,000,000.00 -> spendable −347.29, available 0.00,
    needs 347.29, sweep_covers 347.29, unfunded 0.00, overdrawn 0.00."""
    spendable = int(settled_c) + int(receivable_due_c) - int(payable_c) - int(reserved_c_)
    overdrawn = max(0, -int(settled_c))
    needs = max(0, -spendable - overdrawn)
    covers = min(needs, max(0, int(treasury_available_c)))
    return {"spendable_c": spendable, "available_c": max(0, spendable), "needs_c": needs, "sweep_covers_c": covers, "unfunded_c": needs - covers,
            "overdrawn_c": overdrawn}


def buying_power_split(spendable_c: int, treasury_available_c: int, pb_headroom_c: int) -> Dict[str, int]:
    """What a NEW order of one account / currency can draw (``BuyingPower``; ``BUYING_POWER_TEXT``), all natural cents::

        own            = max(0, spendable)                             (available, floored: AVAILABLE_TEXT)
        committed      = max(0, −spendable)                            (working orders + payables + overdraft beyond own cash)
        treasury_gross = Treasury available (the Treasury page; reserve deducted), pb_gross = PB headroom (0 unless financing on)
        from_treasury  = min(committed, max(0, treasury_gross))        (at their fills the commitments sweep Treasury first ...)
        from_pb        = min(committed − from_treasury, max(0, pb_gross))   (... then draw PB financing)
        treasury_left  = max(0, treasury_gross) − from_treasury
        pb_left        = max(0, pb_gross) − from_pb
        total          = own + treasury_left + pb_left  =  max(0, spendable + max(0, treasury_gross) + max(0, pb_gross))

    A new order needing N is fundable iff its own shortfall max(0, N − own) <= treasury_left + pb_left, i.e. iff
    N − spendable <= Treasury + PB: the rule of the signed available before the 2026-10-21 floor (directive amendment -8: a
    trade is refused when it cannot be paid for after the sweeps), while each FILL still sweeps exactly its own shortfall
    against the floored ``available_c``.  Worked (the regression the checker reproduced): own cash 0.00, a resting MARKET BUY
    1,700 AAPL reserving 1,700 x 346.29 + 8.50 = 588,701.50, Treasury 1,000,000.00 -> own 0.00, committed 588,701.50,
    treasury_left 1,000,000.00 − 588,701.50 = 411,298.50 = total; a second BUY needing 441,356.50 is not fundable (441,356.50 >
    411,298.50): it is never accepted WORKING and SUSPENDED at the open - since the FUNDS_WAIT hotfix (1.0.3.1) it is queued
    SUSPENDED at submit, "waiting for funds", reserving nothing, and resumes when cash frees up."""
    spendable = int(spendable_c)
    own = max(0, spendable)
    committed = max(0, -spendable)
    tre = max(0, int(treasury_available_c))
    pb = max(0, int(pb_headroom_c))
    from_tre = min(committed, tre)
    from_pb = min(committed - from_tre, pb)
    return {"available_c": own, "committed_c": committed, "treasury_gross_c": int(treasury_available_c), "treasury_available_c": tre - from_tre,
            "pb_headroom_gross_c": int(pb_headroom_c), "pb_headroom_c": pb - from_pb, "total_c": own + (tre - from_tre) + (pb - from_pb)}


def _iso(d) -> str:
    return d.isoformat() if hasattr(d, "isoformat") else str(d)


def gl_balances_at(conn: sqlite3.Connection, account_id: str, d, gls=None, ccy: Optional[str] = None) -> Dict[Tuple[str, str], int]:
    """Business-date balances (C9h): ``{(gl, currency): natural cents}`` of one account from the journal lines whose ENTRY's
    ``business_date`` <= ``d`` (an entry posted at 19:58 ET on D carries business date D+1 and counts from D+1; an as-of entry
    counts from its own business date).  ``gls`` limits the codes, ``ccy`` the currency.  Never reads ``gl_balances``."""
    sql = ("SELECT l.gl, l.currency, SUM(l.amount_c) AS a FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id"
           " WHERE l.account_id=? AND e.business_date <= ?")
    params: list = [account_id, _iso(d)]
    if gls:
        gls = list(gls)
        sql += f" AND l.gl IN ({','.join('?' for _ in gls)})"
        params.extend(gls)
    if ccy:
        sql += " AND l.currency=?"
        params.append(ccy)
    sql += " GROUP BY l.gl, l.currency"
    return {(r["gl"], r["currency"]): natural(r["gl"], int(r["a"] or 0)) for r in conn.execute(sql, params).fetchall() if r["gl"] in CHART}


def cash_view_at(conn: sqlite3.Connection, account_id: str, ccy: str, d) -> Dict[str, int]:
    """C9h (FINSIM1_PLAN): the account's business-date cash at the close of business date ``d``, natural cents::

        settled_c    = GL 1010                     (journal lines with business_date <= d)
        receivable_c = GL 1200 + 1250              (sales / FX proceeds not settled yet)
        payable_c    = GL 2100 + 2350              (purchases / FX owed, a positive amount)

    The figure an as-of need of date D is funded on (C20), Treasury's as-of sweep check (C15), restricted collateral as of a
    date (C21), the re-accrual base (C17) and the importer's business-date cash table read.  Worked (owner DB, 10-22): the
    transfers entered 19:39-19:43 ET on 10-21 carry business date 10-22, so F1:TREASURY reads settled 25,000,000.00 at the
    close of 10-21 and 1,467,580.80 at the close of 10-22 while its current balance is the later one."""
    b = gl_balances_at(conn, account_id, d, ("1010", "1200", "1250", "2100", "2350"), ccy)
    g = lambda gl: int(b.get((gl, ccy), 0))   # noqa: E731
    return {"settled_c": g("1010"), "receivable_c": g("1200") + g("1250"), "payable_c": g("2100") + g("2350")}


def _sum(conn: sqlite3.Connection, sql: str, params=()) -> int:
    return int(conn.execute(sql, params).fetchone()[0] or 0)


# ---- C9d / C9i: snapshot readers over the restated versions (FINSIM1_PLAN) ---------------------------------------------------
VERSIONS_TABLE = "nav_snapshot_versions"


def has_versions(conn: sqlite3.Connection) -> bool:
    """True when ``nav_snapshot_versions`` exists (created by ``navhistory.bind``; an older file or a runtime without the module
    has none, and every reader below then reads ``nav_snapshots`` exactly as before)."""
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (VERSIONS_TABLE,)).fetchone() is not None


def _version_dict(r) -> dict:
    d = dict(r)
    d["restated"] = True
    d["restated_ts"] = d.get("ts")
    return d


def _original_dict(r) -> dict:
    d = dict(r)
    d["restated"] = False
    d["version"] = 0
    return d


def snapshot_at(conn: sqlite3.Connection, key: str, snap_date) -> Optional[dict]:
    """The row a reader uses for ``key`` (account id or ``{lifetime}:ALL``) on ``snap_date``: the LATEST restated version when
    one exists (``restated`` True, with ``version``, ``reason``, ``batch_id`` and ``restated_ts``), else the original
    ``nav_snapshots`` row (``restated`` False), else None.  A date with only a version row (an account the restatement added)
    counts."""
    ds = _iso(snap_date)
    if has_versions(conn):
        v = conn.execute(f"SELECT * FROM {VERSIONS_TABLE} WHERE account_id=? AND snap_date=? ORDER BY version DESC LIMIT 1", (key, ds)).fetchone()
        if v is not None:
            return _version_dict(v)
    r = conn.execute("SELECT * FROM nav_snapshots WHERE account_id=? AND snap_date=?", (key, ds)).fetchone()
    return _original_dict(r) if r is not None else None


def original_at(conn: sqlite3.Connection, key: str, snap_date) -> Optional[dict]:
    """The ORIGINAL ``nav_snapshots`` row (never a version) - the toggle's other side and the restatement's starting point."""
    r = conn.execute("SELECT * FROM nav_snapshots WHERE account_id=? AND snap_date=?", (key, _iso(snap_date))).fetchone()
    return _original_dict(r) if r is not None else None


def snapshot_chain(conn: sqlite3.Connection, key: str, from_date=None, to_date=None) -> List[dict]:
    """C9i: the latest-version chain of ``key`` in date order (per date the latest version, else the original), optionally
    limited to ``from_date`` .. ``to_date`` (inclusive).  Performance, History, M4 / M5 and the baselines read this."""
    where, params = "account_id=?", [key]
    if from_date is not None:
        where += " AND snap_date >= ?"
        params.append(_iso(from_date))
    if to_date is not None:
        where += " AND snap_date <= ?"
        params.append(_iso(to_date))
    rows = {r["snap_date"]: _original_dict(r) for r in conn.execute(f"SELECT * FROM nav_snapshots WHERE {where}", params).fetchall()}
    if has_versions(conn):
        for v in conn.execute(f"SELECT * FROM {VERSIONS_TABLE} WHERE {where} ORDER BY snap_date, version", params).fetchall():
            rows[v["snap_date"]] = _version_dict(v)              # ascending versions: the last one per date wins
    return [rows[d] for d in sorted(rows)]


def lender_label(name) -> str:
    """C16 (P6's ``lending.lender_label``, imported lazily): a stored lender name as shown - "street lender (modelled)" reads "street lender
    (assumed terms)"; the stored rows are never rewritten.  The same rule applies here when the lending module is absent."""
    try:
        from . import lending as _lending
        fn = getattr(_lending, "lender_label", None)
        if callable(fn):
            return fn(name)
    except Exception:   # noqa: BLE001 - the same rule below
        pass
    s = "" if name is None else str(name)
    for old in ("modelled)", "modeled)"):
        if s.endswith(old):
            return s[: -len(old)] + "assumed terms)"
    return s


def _rget(row, key: str, default=None):
    """``row[key]`` for a dict or a ``sqlite3.Row`` (None-safe; a missing key gives ``default``)."""
    if row is None:
        return default
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


def restated_label(row) -> Optional[dict]:
    """A reader's label of a restated snapshot row (C9d: "restated on <date> (batch X)"), None for an original:
    ``{snap_date, on, batch_id, version, reason, text}``."""
    if not _rget(row, "restated"):
        return None
    on = ny_of(int(_rget(row, "restated_ts") or _rget(row, "ts") or 0)).date().isoformat()
    batch = _rget(row, "batch_id")
    text = f"restated on {on}" + (f" (batch {batch})" if batch else "")
    return {"snap_date": _rget(row, "snap_date"), "on": on, "batch_id": batch, "version": _rget(row, "version"), "reason": _rget(row, "reason"), "text": text}


def _last_snapshot(conn: sqlite3.Connection, key: str, before: Optional[str] = None):
    """The latest snapshot row of ``key`` (dated before ``before`` when given), reading the latest restated version of that date
    when one exists (C9d readers: ``_last_snapshot``, the TODAY / MTD / YTD baselines, the archived NAV and the day P&L base)."""
    if not has_versions(conn):
        if before:
            return conn.execute("SELECT * FROM nav_snapshots WHERE account_id=? AND snap_date < ? ORDER BY snap_date DESC LIMIT 1", (key, before)).fetchone()
        return conn.execute("SELECT * FROM nav_snapshots WHERE account_id=? ORDER BY snap_date DESC LIMIT 1", (key,)).fetchone()
    cond, params = ("AND snap_date < ?", [key, before, key, before]) if before else ("", [key, key])
    r = conn.execute(f"SELECT MAX(d) FROM (SELECT MAX(snap_date) d FROM nav_snapshots WHERE account_id=? {cond}"
                     f" UNION ALL SELECT MAX(snap_date) d FROM {VERSIONS_TABLE} WHERE account_id=? {cond})", params).fetchone()
    if r is None or r[0] is None:
        return None
    return snapshot_at(conn, key, r[0])


# ---- PORTFOLIO-14: dated marks of a snapshot row -------------------------------------------------------------------------------
DATED_KINDS = EQUITY_KINDS + ("OPTION", "FUTURE")


def dated_mark_row(conn: sqlite3.Connection, iid: str, kind: str, d) -> Optional[sqlite3.Row]:
    """The stored end-of-day mark of ``iid`` for date ``d``: the ``marks`` row of kind SETTLE (futures) or CLOSE (everything else)
    dated ``d``, else the latest one before it (a weekend or holiday carries the previous trading day's mark - no re-mark)."""
    return conn.execute("SELECT * FROM marks WHERE instrument_id=? AND kind=? AND mark_date <= ? ORDER BY mark_date DESC LIMIT 1",
                        (iid, "SETTLE" if kind == "FUTURE" else "CLOSE", _iso(d))).fetchone()


def dated_mark_view(conn: sqlite3.Connection, inst, d, sessions=None) -> Optional[MarkView]:
    """PORTFOLIO-14: the dated mark of a position for date ``d`` as a MarkView: equities / ETFs at the recorded CLOSE, futures at the
    SETTLE, options at the recorded CLOSE (the Cboe official close where it was recorded, else the last trade; its stored quality
    says so); a weekend or holiday carries the previous trading day's stored mark (no re-mark).  The row's ``mark_date`` is kept in
    ``inputs``.  None when nothing is stored, or when the close of the instrument's last trading day on or before ``d`` was never
    recorded (an older close is not that day's mark: the caller falls back, labelled)."""
    row = dated_mark_row(conn, inst.instrument_id, inst.kind, d)
    if row is None:
        return None
    ds = _iso(d)
    quality = str(row["quality"] or "close")
    note = f"recorded {row['kind']} of {row['mark_date']}" + (f" (carried to {ds})" if row["mark_date"] != ds else "")
    if sessions is not None and row["mark_date"] != ds:
        try:
            from .lifecycle_jobs import last_trading_day
            grp = "US_EQUITY" if inst.kind == "FUTURE" else inst.session_group
            expected = last_trading_day(sessions, grp, date.fromisoformat(ds))
        except Exception:   # noqa: BLE001 - without a calendar the stored mark is carried as it is
            expected = None
        if expected is not None and row["mark_date"] < expected.isoformat():
            # the close of a trading day on or before d was never recorded: an older close is not that day's mark (only a weekend or a
            # holiday carries one); the caller falls back - the evening snapshot to the live mark, labelled; a restatement to the original's
            return None
    return MarkView(inst.instrument_id, Decimal(str(row["price"])), quality, str(row["source"] or ""), str(row["method"] or row["kind"]).lower(),
                    row["quote_time"], row["fetched_at"], f"{row['kind'].lower()} of {row['mark_date']}",
                    {"mark_date": row["mark_date"], "mark_kind": row["kind"], "note": row["note"]}, False, note)


def mark_labels(kind: str, view: Optional[MarkView]) -> dict:
    """``{mark_kind, mark_source, mark_date}`` of a position's mark for ``detail_json`` (PORTFOLIO-14): CLOSE / SETTLE for a stored
    end-of-day mark (with its date), OVERLAY for an OTC contract (the mark at the last variation margin, or the C13 model mark),
    LIVE for the snapshot's own quote or model."""
    if view is None:
        return {"mark_kind": None, "mark_source": None, "mark_date": None}
    inputs = view.inputs if isinstance(view.inputs, dict) else {}
    if inputs.get("mark_kind"):
        return {"mark_kind": inputs.get("mark_kind"), "mark_source": view.source or None, "mark_date": inputs.get("mark_date")}
    if kind in ("SWAP", "FX_FORWARD"):
        return {"mark_kind": "OVERLAY", "mark_source": view.source or None, "mark_date": None}
    return {"mark_kind": "LIVE" if view.mark is not None else None, "mark_source": view.source or None, "mark_date": None}


class DatedMarks:
    """PORTFOLIO-14: the mark set of the snapshot row of date ``d`` - every listed position (equity / ETF / option / future) at its
    stored mark of ``d`` (``dated_mark_view``), every other mark from ``base`` (the live snapshot: OTC overlays and models,
    private and bond models, FX).  A listed name with no stored mark at all falls back to ``base`` and says so (quality stale,
    never a silent live price).  Everything else a reader asks (``as_of``, ``quote``, ``chain``, ``session``) is ``base``'s."""

    def __init__(self, conn: sqlite3.Connection, master, d, base, sessions=None):
        self._conn, self._master, self._d, self._base, self._sessions = conn, master, d, base, sessions
        self._cache: Dict[str, MarkView] = {}

    _OWN = frozenset(("_conn", "_master", "_d", "_base", "_sessions", "_cache"))

    def __getattr__(self, name):
        if name in DatedMarks._OWN:            # not set yet (never recurse into the base for this wrapper's own state)
            raise AttributeError(name)
        return getattr(self._base, name)

    def as_of(self) -> int:
        return self._base.as_of() if self._base is not None else 0

    def fx(self, ccy: str):
        return self._base.fx(ccy)

    def mark(self, iid: str) -> MarkView:
        if iid in self._cache:
            return self._cache[iid]
        inst = _instrument(self._conn, self._master, iid, {})
        view = None
        if inst is not None and inst.kind in DATED_KINDS:
            view = dated_mark_view(self._conn, inst, self._d, self._sessions)
            if view is None:
                live = self._base.mark(iid) if self._base is not None else None
                if live is not None and live.mark is not None and live.quality != "unavailable":
                    view = MarkView(iid, live.mark, "stale", live.source, live.method, live.quote_time, live.fetched_at, live.delay_label, live.inputs, False,
                                    f"no {'SETTLE' if inst.kind == 'FUTURE' else 'CLOSE'} recorded for {_iso(self._d)}: the live mark of the snapshot time")
                else:
                    view = live or unavailable(iid, f"no recorded close for {_iso(self._d)}")
        if view is None:
            view = self._base.mark(iid) if self._base is not None else unavailable(iid, "no mark snapshot")
        self._cache[iid] = view
        return view


@dataclass
class _Calc:
    account_id: str
    S: Dict[str, int] = field(default_factory=dict)
    B: Dict[str, int] = field(default_factory=dict)
    U: Dict[str, int] = field(default_factory=dict)
    positions: List[PositionView] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    fx_missing: List[str] = field(default_factory=list)
    quality: str = "delayed"
    gl: Dict[Tuple[str, str], int] = field(default_factory=dict)         # (gl, ccy) -> natural native balance
    capital_c: int = 0
    transfers_c: int = 0
    realised_c: int = 0
    income_c: int = 0
    financing_c: int = 0
    fees_c: int = 0
    nav_c: int = 0
    unrealised_c: int = 0
    fx_result_c: int = 0
    assets_native: Dict[str, int] = field(default_factory=dict)
    liab_native: Dict[str, int] = field(default_factory=dict)
    mv_long_c: int = 0
    mv_short_c: int = 0
    cash_c: int = 0
    pending_c: int = 0
    swaps_pos_c: int = 0
    swaps_neg_c: int = 0
    futures_ote_c: int = 0
    cost_long_c: int = 0             # MONEY-5: unpriced long positions carried at cost (base)
    cost_short_c: int = 0            # MONEY-5: unpriced short positions carried at their proceeds (base)
    rates: Dict[str, Optional[Decimal]] = field(default_factory=dict)

    @property
    def partial(self) -> bool:
        return bool(self.missing or self.fx_missing)


class Accounting:
    def __init__(self, db, instruments, marks, clock, cal, sessions):
        self.db = db
        self.instruments = instruments
        self.marks = marks
        self.clock = clock
        self.cal = cal
        self.sessions = sessions
        self.services = None
        self.selected_book: Optional[str] = None
        self.pb_headroom_fn = None     # WP-07 hook: fn(conn, account_row: dict, snapshot, accounting) -> int

    # ---- scopes -----------------------------------------------------------------------------------------------------
    def _snapshot(self, snapshot=None):
        return snapshot if snapshot is not None else self.marks.snapshot()

    def _scope(self, conn: sqlite3.Connection, scope: str) -> Scope:
        scope = str(scope or "")
        if scope == "ALL_BOOKS":
            lids = open_lifetimes(conn)
            return Scope(scope, "ALL_BOOKS", self._accounts_of(conn, lids), lids, False, None)
        if scope == "ALL":
            lid = None
            if self.selected_book:
                row = open_lifetime(conn, self.selected_book)
                lid = row["lifetime_id"] if row else None
            if lid is None:
                lids = open_lifetimes(conn)
                if not lids:
                    raise ScopeError("no open lifetime")
                lid = lids[0]
            return Scope(scope, "LIFETIME", self._accounts_of(conn, [lid]), [lid], False, f"{lid}:ALL")
        if scope.endswith(":ALL"):
            lid = scope[:-4]
            row = conn.execute("SELECT status FROM lifetimes WHERE lifetime_id=?", (lid,)).fetchone()
            if row is None:
                raise ScopeError(f"unknown lifetime {lid}")
            return Scope(scope, "LIFETIME", self._accounts_of(conn, [lid]), [lid], row["status"] == "CLOSED", f"{lid}:ALL")
        if ACCOUNT_ID_RE.match(scope):
            row = conn.execute("SELECT a.*, l.status AS lstatus FROM accounts a JOIN lifetimes l ON l.lifetime_id=a.lifetime_id WHERE a.account_id=?", (scope,)).fetchone()
            if row is None:
                raise ScopeError(f"unknown account {scope}")
            acct = dict(row)
            return Scope(scope, "ACCOUNT", [acct], [acct["lifetime_id"]], acct["lstatus"] == "CLOSED", scope)
        raise ScopeError(f"unknown scope {scope!r}")

    @staticmethod
    def _accounts_of(conn: sqlite3.Connection, lids: List[str]) -> List[dict]:
        if not lids:
            return []
        q = ",".join("?" for _ in lids)
        return [dict(r) for r in conn.execute(f"SELECT * FROM accounts WHERE lifetime_id IN ({q}) ORDER BY lifetime_id, sort_order, account_id", lids).fetchall()]

    def _conn(self):
        """The reader of every view here: the writer inside this thread's own transaction, else the connection this thread
        already reads on, else ONE pooled reader declared as this thread's reading connection (``marks.shared_reader``).
        So a view called inside another one's read (integrity's mark checks -> pnl / balance_sheet / treasury) and the
        PRIVATE / private-credit / SWAP marks computed inside positions reuse it: never a second pooled reader (the pool
        has four; the 2026-10-21 BUSY defect)."""
        return shared_reader(self.db)

    # ---- cash -------------------------------------------------------------------------------------------------------
    def view_date(self) -> date:
        """USX-10: the settlement date of the account views (``view_settle_date``: T+1 after today's accounting day)."""
        return view_settle_date(self.cal, self.clock.now())

    def cash(self, account_id: str, snapshot=None) -> Dict[str, CashView]:
        """Per currency: settled / pending / held / margin / reserved and ``available`` (floored; ``available_c``) with the
        split of what lies beyond own cash (``liquidity_split``) against Treasury's available cash in that currency.
        Receivables count when due by the view's settlement date (``view_settle_date``, USX-10), the date a new order
        settles at, so a same-day round trip nets its sale against its purchase."""
        snap = self._snapshot(snapshot)
        with self._conn() as conn:
            acct = self._scope(conn, account_id).accounts[0]
            ccys = {r[0] for r in conn.execute("SELECT DISTINCT currency FROM gl_balances WHERE account_id=?", (account_id,)).fetchall()} | {"USD"}
            out: Dict[str, CashView] = {}
            today = self.view_date()
            for ccy in sorted(ccys):
                b = {gl: natural(gl, _sum(conn, "SELECT COALESCE(SUM(balance_c),0) FROM gl_balances WHERE account_id=? AND gl=? AND currency=?", (account_id, gl, ccy)))
                     for gl in ("1010", "1200", "1250", "2100", "2350", "1400", "1300", "1310", "1320", "1330", "1230", "1210", "1220", "1225", "1410")}
                reserved = reserved_c(conn, account_id, ccy, snap, self.instruments)
                recv_due = _sum(conn, "SELECT COALESCE(SUM(amount_c),0) FROM settlements WHERE account_id=? AND currency=? AND status='PENDING' AND amount_c > 0 AND due_date <= ?",
                                (account_id, ccy, today.isoformat()))
                receivable = b["1200"] + b["1250"]
                payable = b["2100"] + b["2350"]
                tre = 0 if acct["kind"] == "TREASURY" else self.treasury_available_c(conn, acct["lifetime_id"], ccy, snap)
                adj = cash_adjustments(conn, account_id, ccy, snap, self.instruments)          # C9b: restricted cash and the like
                adj_c = sum(a["amount_c"] for a in adj)
                s = liquidity_split(b["1010"] - adj_c, recv_due, payable, reserved, tre)
                opt_req = 0
                if ccy == "USD":
                    ms = conn.execute("SELECT options_required_c FROM margin_state WHERE account_id=?", (account_id,)).fetchone()
                    opt_req = max(0, int(ms["options_required_c"] or 0)) if ms is not None else 0
                out[ccy] = CashView(account_id, ccy, b["1010"], receivable, payable, receivable - payable, b["1400"], b["1300"] + b["1310"], reserved,
                                    s["available_c"], b["1230"] + b["1210"] + b["1220"] + b["1225"] + b["1410"], b["1320"] + b["1330"],
                                    s["spendable_c"], s["needs_c"], s["sweep_covers_c"], s["unfunded_c"], s["overdrawn_c"], opt_req, reserved - opt_req,
                                    adj, adj_c)
            return out

    def available(self, account_id: str, currency: str, settle_date: Optional[date] = None, snapshot=None) -> int:
        """``available_c`` (floored at zero; ``AVAILABLE_TEXT``) at ``settle_date`` (None: the view's date, USX-10)."""
        snap = self._snapshot(snapshot)
        with self._conn() as conn:
            return available_c(conn, account_id, currency, settle_date or self.view_date(), snap, self.instruments, self.clock.now())

    def available_view(self, account_id: str, currency: str = "USD", settle_date: Optional[date] = None, snapshot=None) -> AvailableView:
        """The liquidity line of one account (sidebar / header): available floored at zero, reserved, what the Treasury
        sweep covers beyond own cash, unfunded and overdrawn (``liquidity_split``; ``AVAILABLE_TEXT``)."""
        snap = self._snapshot(snapshot)
        with self._conn() as conn:
            acct = self._scope(conn, account_id).accounts[0]
            d, settled, recv, pay, reserved = _available_parts(conn, account_id, currency, settle_date or self.view_date(), snap, self.instruments, self.clock.now())
            adj = adjustments_c(conn, account_id, currency, snap, self.instruments)        # C9b: _available_parts' settled is net of it
            tre = 0 if acct["kind"] == "TREASURY" else self.treasury_available_c(conn, acct["lifetime_id"], currency, snap)
            s = liquidity_split(settled, recv, pay, reserved, tre)
            return AvailableView(account_id, currency, d.isoformat(), settled + adj, recv, pay, reserved, s["spendable_c"], s["available_c"], s["needs_c"], tre,
                                 s["sweep_covers_c"], s["unfunded_c"], s["overdrawn_c"], adj)

    def treasury_available_c(self, conn: sqlite3.Connection, lifetime_id: str, currency: str, snap) -> int:
        """``treasury_available_c`` (module function): Treasury immediate available − the book's reserve (BM-01)."""
        return treasury_available_c(conn, lifetime_id, currency, snap, self.instruments, self.clock.now())

    def pb_headroom_c(self, conn: sqlite3.Connection, acct: dict, snap) -> int:
        """§6.1 / §12.4: 50 % of the PB account's long market value − balance(2700) − OTC IM/VM posted, capped by
        pb_limit_c (0 = no cap); 0 unless ``pb_financing`` is on.  WP-07 may replace it through ``pb_headroom_fn``."""
        if acct.get("kind") != "PB" or not int(acct.get("pb_financing") or 0):
            return 0
        if self.pb_headroom_fn is not None:
            return int(self.pb_headroom_fn(conn, acct, snap, self))
        calc = self._account_calc(conn, acct, snap)
        long_mv = sum(p.mv_base_c for p in calc.positions if p.mv_base_c and p.mv_base_c > 0)
        financed = natural("2700", _sum(conn, "SELECT COALESCE(SUM(balance_c),0) FROM gl_balances WHERE account_id=? AND gl='2700' AND currency='USD'", (acct["account_id"],)))
        otc = sum(natural(gl, _sum(conn, "SELECT COALESCE(SUM(balance_c),0) FROM gl_balances WHERE account_id=? AND gl=? AND currency='USD'", (acct["account_id"], gl)))
                  for gl in ("1320", "1330"))
        head = to_c(from_c(long_mv) * tables.PB_HEADROOM_PCT) - financed - otc
        limit = int(acct.get("pb_limit_c") or 0)
        if limit > 0:
            head = min(head, limit - financed)
        return max(0, head)

    def buying_power(self, account_id: str, currency: str, settle_date: Optional[date] = None, snapshot=None) -> BuyingPower:
        """What a NEW order of the account can draw at ``settle_date`` (``buying_power_split``; ``BUYING_POWER_TEXT``):
        ``available_c`` = own available floored at zero (= ``available_c()``, the figure ``execution.fund`` sweeps against
        at the fill, so the previewed sweep-in is the executed one); ``treasury_available_c`` / ``pb_headroom_c`` = Treasury
        available / PB headroom minus what the account's working orders, payables and overdraft already need beyond its own
        cash (``committed_c``).  The ticket preview (``orders_preview``) and the submit check read these three fields, so a
        new order whose own shortfall exceeds what the account's earlier commitments leave of Treasury + PB is not accepted
        as working: it queues SUSPENDED, waiting for funds (FUNDS_WAIT, ``orders.plan_funds_resume``)."""
        snap = self._snapshot(snapshot)
        settle_date = settle_date or self.view_date()          # USX-10: the view's date when the caller names none (header / Cash page)
        with self._conn() as conn:
            sc = self._scope(conn, account_id)
            acct = sc.accounts[0]
            spend = spendable_c(conn, account_id, currency, settle_date, snap, self.instruments, self.clock.now())
            tre = 0 if acct["kind"] == "TREASURY" else self.treasury_available_c(conn, acct["lifetime_id"], currency, snap)
            pb = self.pb_headroom_c(conn, acct, snap) if currency == "USD" else 0
            s = buying_power_split(spend, tre, pb)
            imm = immediate_available_c(conn, account_id, currency, settle_date, snap, self.instruments, self.clock.now())
            return BuyingPower(account_id, currency, s["available_c"], s["treasury_available_c"], s["pb_headroom_c"], s["total_c"],
                               settle_date.isoformat() if settle_date else None, s["committed_c"], s["treasury_gross_c"], s["pb_headroom_gross_c"], imm)

    # ---- positions ------------------------------------------------------------------------------------------------------
    def _gl_by_instrument(self, conn: sqlite3.Connection, aid: str) -> Dict[str, Dict[str, Tuple[int, int]]]:
        out: Dict[str, Dict[str, Tuple[int, int]]] = {}
        for r in conn.execute("SELECT gl, instrument_id, SUM(balance_c) b, SUM(base_c) bb FROM gl_balances WHERE account_id=? AND instrument_id<>'' GROUP BY gl, instrument_id", (aid,)).fetchall():
            out.setdefault(r["instrument_id"], {})[r["gl"]] = (natural(r["gl"], r["b"]), natural(r["gl"], r["bb"]))
        return out

    @staticmethod
    def _pl_cum(gl: Dict[str, Tuple[int, int]]) -> Tuple[int, int, int, int]:
        """(realised, income, financing, fees) per instrument in base (natural; financing and fees as positive costs; PORTFOLIO-08:
        the swap financing / coupon GLs 4451 / 4453 are financing, dividend equivalents 4452 income - ``pl_bucket``)."""
        realised = sum(pl_amount(g, gl[g][1]) for g in PL_REALISED_GLS if g in gl)
        income = sum(pl_amount(g, gl[g][1]) for g in PL_INCOME_GLS if g in gl)
        financing = sum(pl_amount(g, gl[g][1]) for g in PL_FINANCING_GLS if g in gl)
        fees = sum(pl_amount(g, gl[g][1]) for g in PL_FEE_GLS if g in gl)
        return realised, income, financing, fees

    def _positions_of(self, conn: sqlite3.Connection, acct: dict, snap, archived_detail: Optional[dict], day_base: Optional[dict],
                      rate_of=None, nav_for_weight: Optional[int] = None) -> List[PositionView]:
        aid = acct["account_id"]
        cache: dict = {}
        if rate_of is None:
            rate_of = lambda ccy: self._fx_rate(snap, ccy)   # noqa: E731
        glmap = self._gl_by_instrument(conn, aid)
        today = self.clock.today()
        out: List[PositionView] = []
        settle_px: Dict[str, Decimal] = {}
        fut_last: Dict[str, Any] = {}
        for r in conn.execute("SELECT instrument_id, settle_px, settle_date, qty_after, quote_json FROM futures_settle WHERE account_id=? ORDER BY settle_date", (aid,)).fetchall():
            settle_px[r["instrument_id"]] = Decimal(r["settle_px"])
            fut_last[r["instrument_id"]] = r
        borrows: Dict[str, dict] = {}
        for r in conn.execute("SELECT borrow_id, instrument_id, qty, qty_returned, fee_rate, rebate_rate, tier, collateral_c, lender FROM borrows WHERE account_id=? AND status='OPEN'", (aid,)).fetchall():
            # C16 / 15.7 item 8: the lender as shown ("street lender (assumed terms)"); the stored row is never rewritten
            b = borrows.setdefault(r["instrument_id"], {"borrow_ids": [], "qty": 0, "collateral_c": 0, "fee_rate": r["fee_rate"], "rebate_rate": r["rebate_rate"], "tier": r["tier"],
                                                        "lender": lender_label(r["lender"])})
            b["borrow_ids"].append(r["borrow_id"])
            b["qty"] += int(r["qty"]) - int(r["qty_returned"])
            b["collateral_c"] += int(r["collateral_c"])
        prev_pos = (day_base or {}).get("positions", {}) if day_base else {}
        osec = owner_sectors(conn)

        def finish(iid, inst, kind, qty, cost_c, mark_view, mv_c, notional_c, unreal_c, ccy, lots, settle, extra, klass, symbol=None, name=None, avg_px=None):
            gl = glmap.get(iid, {})
            realised, income, financing, fees = self._pl_cum(gl)
            rate = rate_of(ccy)
            mv_base = None if mv_c is None or rate is None else fx_c(mv_c, rate)
            unreal_base = None if unreal_c is None or rate is None else fx_c(unreal_c, rate)
            pending = gl.get("1210", (0, 0))[0] - gl.get("2320", (0, 0))[0]
            day = None
            if unreal_c is not None:
                pp = prev_pos.get(iid) or {}
                prev_unreal = int(pp.get("unrealised_c", 0) or 0)
                prev_pl = int(pp.get("pl_cum_c", 0) or 0)
                pl_now = realised + income - financing - fees
                day = (unreal_base if unreal_base is not None else 0) - prev_unreal + (pl_now - prev_pl)
            avg = None
            if kind == "FUTURE" and lots:
                tot = sum(l.qty_remaining for l in lots)
                avg = qprice((sum(l.unit_cost * l.qty_remaining for l in lots) / Decimal(tot)).quantize(Decimal("0.0001"))) if tot else None
            elif qty and inst is not None and kind not in ("SWAP", "PRIVATE_LOAN"):
                denom = Decimal(abs(qty)) * inst.multiplier * inst.price_scale
                avg = qprice((Decimal(abs(cost_c)) / Decimal(100) / denom).quantize(Decimal("0.0001"))) if denom else None
            if avg_px is not None:
                avg = avg_px
            weight = None
            if nav_for_weight and nav_for_weight > 0 and mv_base is not None:      # V104R1-03: no share of a NAV at or below zero
                weight = (Decimal(mv_base) / Decimal(nav_for_weight)).quantize(Decimal("0.0001"))
            days = None
            if lots:
                days = (today - date.fromisoformat(min(l.open_date for l in lots))).days
            elif kind == "SWAP" and (extra or {}).get("start_date"):
                try:
                    # OTC-18: a contract that starts later (a forward or swap dealt for the next FX trade date, a cross-currency swap starting on
                    # its spot date) has been held 0 days, never a negative count
                    days = max(0, (today - date.fromisoformat(str(extra["start_date"])[:10])).days)
                except ValueError:
                    days = None
            # PORTFOLIO-04: total P&L to date = unrealised + realised + income - financing - fees (base); None while the row has no price
            total = None if unreal_base is None else int(unreal_base) + realised + income - financing - fees
            if kind == "SWAP" and str((extra or {}).get("kind") or "") == "TRS":
                # PORTFOLIO-19: a TRS carries its underlying's sector (its exposure is the underlying's)
                sp = (inst.spec or {}) if inst is not None else {}
                und = ((inst.underlying_id if inst is not None else None) or sp.get("underlying_id") or sp.get("underlying"))
                sec, src = sector_of(_instrument(conn, self.instruments, und, cache), osec) if und else (None, None)
            elif kind in ("SWAP", "FX_FORWARD", "PRIVATE_LOAN"):
                sec, src = None, None
            else:
                sec, src = sector_of(inst, osec)
                if sec is None and kind == "OPTION" and inst is not None and inst.underlying_id:
                    # a listed option without its own classification carries its underlying's sector (ORCL calls: Information Technology)
                    sec, src = sector_of(_instrument(conn, self.instruments, inst.underlying_id, cache), osec)
            out.append(PositionView(aid, iid, symbol or (inst.symbol if inst else iid), name or (inst.name if inst else iid), kind, klass, ccy, qty, cost_c, avg, mark_view, mv_c, mv_base,
                                    notional_c, unreal_c, unreal_base, realised, income, -financing, -fees, pending, day, weight, days, settle,
                                    borrows.get(iid), lots, extra, asset_class_of(kind, extra), counterparty_of(kind, acct, extra, borrows.get(iid), qty), total,
                                    sec, src))

        emitted: set = set()
        for r in conn.execute("SELECT instrument_id, qty, cost_c, currency FROM v_positions WHERE account_id=? ORDER BY instrument_id", (aid,)).fetchall():
            iid, qty, cost_c = r["instrument_id"], int(r["qty"] or 0), int(r["cost_c"] or 0)
            if qty == 0 and cost_c == 0:
                continue
            emitted.add(iid)
            inst = _instrument(conn, self.instruments, iid, cache)
            kind = inst.kind if inst else "EQUITY"
            ccy = inst.currency if inst else (r["currency"] or "USD")
            lots = [LotView(int(l["lot_id"]), l["side"], ny_of(int(l["open_ts"])).date().isoformat(), int(l["open_ts"]), int(l["qty_open"]), int(l["qty_remaining"]),
                            Decimal(l["unit_cost"]), int(l["cost_c"]), l["open_fill_id"], l["lock_until"], l["currency"])
                    for l in conn.execute("SELECT * FROM lots WHERE account_id=? AND instrument_id=? AND qty_remaining > 0 ORDER BY open_ts, lot_id", (aid, iid)).fetchall()]
            if archived_detail is not None:
                pd = (archived_detail.get("positions") or {}).get(iid) or {}
                mark = None if pd.get("mark") is None else Decimal(str(pd["mark"]))
                view = MarkView(iid, mark, pd.get("quality") or ("unavailable" if mark is None else "close"), "stored snapshot (archived lifetime)", "snapshot",
                                pd.get("as_of"), None, "frozen at reset", None, False, "frozen at reset")
            else:
                mark, view = _mark_price(snap, iid)
            mult = inst.multiplier if inst else Decimal(1)
            scale = inst.price_scale if inst else Decimal(1)
            extra: dict = {}
            settle = settle_px.get(iid)
            if kind == "FUTURE":
                ref = settle if settle is not None else (lots[0].unit_cost if lots else None)
                mv_c = 0
                notional = None if mark is None else mul_c(qty, mark, mult, scale)
                unreal = None if (mark is None or ref is None) else mul_c(qty, mark - ref, mult, scale)
                extra = {"open_trade_equity_c": unreal, "reference_px": None if ref is None else str(ref), "im_c": int((inst.spec or {}).get("im_c") or 0) * abs(qty) if inst else None}
            else:
                mv_c = None if mark is None else mul_c(qty, mark, mult, scale)
                notional = mv_c
                unreal = None if mv_c is None else mv_c - cost_c
            if kind == "PRIVATE" and lots:
                extra["lock_until"] = max((l.lock_until or "") for l in lots) or None
            if kind == "BOND":
                extra["accrued_c"] = glmap.get(iid, {}).get("1220", (0, 0))[0]      # accrued interest receivable of this bond (its currency)
            if kind not in ("FUTURE", "SWAP", "PRIVATE_LOAN") and mark is None and qty:
                # MONEY-5: an unpriced position stays in NAV AT COST (the cost row of the balance sheet; a short at its proceeds),
                # never dropped and never valued at a guess: unrealised is 0 against the cost and the row says why
                extra["at_cost"] = True
                extra["at_cost_note"] = "no price: carried at cost in NAV"
            finish(iid, inst, kind, qty, cost_c, view, mv_c, notional, unreal, ccy, lots, settle, extra, CLASS_OF_KIND.get(kind, "securities"))

        # MONEY-3: a future closed flat since its last settle carries the closed contracts' P&L (futures_settle unposted_c of the
        # latest reference row, a 'seed' the day's VM has not settled) until FUTURES_VM posts it to 4400 - a zero-quantity row,
        # so NAV, P&L today and the snapshot include the closing trade before the evening variation margin
        for iid, r in sorted(fut_last.items()):
            if iid in emitted or int(r["qty_after"] or 0) != 0:
                continue
            try:
                info = json.loads(r["quote_json"] or "{}")
            except ValueError:
                info = {}
            if not isinstance(info, dict) or info.get("kind") == "settle":
                continue
            pending = int(info.get("unposted_c") or 0)
            if not pending:
                continue
            inst = _instrument(conn, self.instruments, iid, cache)
            ccy = inst.currency if inst else "USD"
            if archived_detail is not None:
                mark_v, view = None, MarkView(iid, None, "close", "stored snapshot (archived lifetime)", "snapshot", None, None, "frozen at reset", None, False, "frozen at reset")
            else:
                mark_v, view = _mark_price(snap, iid)
            extra = {"open_trade_equity_c": pending, "reference_px": str(r["settle_px"]), "im_c": 0, "pending_vm_c": pending, "pending_vm_date": r["settle_date"],
                     "note": f"closed flat since the last settle: {fmt_money(pending)} is posted by the {r['settle_date']} variation margin"}
            finish(iid, inst, "FUTURE", 0, 0, view, 0, 0, pending, ccy, [], Decimal(r["settle_px"]), extra, CLASS_OF_KIND["FUTURE"])

        swaps_mod = getattr(self.services, "swaps", None) if self.services else None
        pos_fields = getattr(swaps_mod, "position_fields", None) if swaps_mod is not None else None
        for r in conn.execute("SELECT * FROM otc_contracts WHERE account_id=? AND status='OPEN' ORDER BY contract_id", (aid,)).fetchall():
            cid = r["contract_id"]
            inst = _instrument(conn, self.instruments, cid, cache)
            gl = glmap.get(cid, {})
            # C9c: the contract's cost basis = its GL 1335 (premium / upfront paid) less its GL 2345 (premium / upfront received); both stay
            # out of the NAV asset / liability sets, so assets / liabilities carry the contract at MV and NAV moves by MV - cost only
            cost_c = otc_cost_c(gl)
            mark_c = int(r["mark_c"] or 0)
            if archived_detail is not None:
                pd = (archived_detail.get("positions") or {}).get(cid) or {}
                mark_c = int(pd.get("mv_c", mark_c) or 0)
            live = snap.mark(cid) if (snap is not None and archived_detail is None) else None
            # the row's mark is the overlay NAV carries (the mark at the last variation margin; 0 after an STM settlement), so the
            # row's mark and market value agree; the live model mark is beside it in extra.model_mark (WP-15: the two values labelled)
            # the model cannot be re-run now (no curve / no underlying print: the swaps module answers a stale view) -> the overlay is
            # labelled stale too, so NAV's quality says so; "unavailable" (no module, a fixed test mark set) carries no such information
            overlay_q = "stale" if (live is not None and live.quality == "stale") else "computed"
            view = MarkView(cid, from_c(mark_c), overlay_q, "swap overlay (the mark at the last variation margin)", "swap_overlay", r["mark_ts"], None, "model",
                            {"overlay_c": mark_c, "margin_mode": r["margin_mode"], "model_mark": None if live is None or live.mark is None else str(live.mark),
                             "model_quality": None if live is None else live.quality}, False,
                            "NAV carries the mark at the last variation margin (0 after an STM settlement); the live model mark is in model_mark")
            try:
                terms = json.loads(r["terms_json"] or "{}")
            except ValueError:
                terms = {}
            sign = -1 if terms.get("pay_fixed") or terms.get("receiver") is False else 1
            dv01 = None
            swaps_mod = getattr(self.services, "swaps", None) if self.services else None
            if swaps_mod is not None and hasattr(swaps_mod, "dv01_c"):
                try:
                    dv01 = int(swaps_mod.dv01_c(self.services, r))
                except Exception:   # noqa: BLE001 — a missing curve leaves DV01 unknown, never a fake number
                    dv01 = None
            extra = {"kind": r["kind"], "broker": r["broker"], "notional_c": int(r["notional_c"]), "vm_balance_c": int(r["vm_balance_c"] or 0), "im_c": int(r["im_c"] or 0),
                     "dv01_c": dv01, "accrued_c": gl.get("1225", (0, 0))[0] - gl.get("2340", (0, 0))[0], "margin_mode": r["margin_mode"], "maturity_date": r["maturity_date"],
                     "start_date": r["start_date"], "last_reset_date": r["last_reset_date"],
                     "model_mark": None if live is None else live.to_dict()}
            # APIUI-07: the quantity of a swap is its signed notional in whole units of its currency (10,000,000.00 -> 10,000,000),
            # like a private loan's principal; notional_c stays exact in extra / notional
            extra["qty_unit"] = r["currency"]
            # OTC-R1-03: a short readable label for the tables ('CDX.NA.IG S47 5Y buy protection', 'TRS ORCL (pay)', 'IRS pay fixed 4.80 % to
            # 2029-10-29') - the instrument name leads with a generic phrase a narrow column cuts before the index / notional
            try:
                from .risk import contract_label
                extra["label"] = contract_label(inst, extra)
            except Exception:   # noqa: BLE001 - the label is a convenience; the name stays
                extra["label"] = None
            mv_c = mark_c
            fields = None
            if archived_detail is None and callable(pos_fields):
                # C13 (P4): the intraday MV when the live model mark is fresh ("model, intraday"), else the last-VM overlay; a hook that
                # answers None (the stub) or no MV keeps the overlay above
                try:
                    fields = pos_fields(self.services, dict(r), snap, self.clock.epoch())
                except Exception as e:   # noqa: BLE001 - the overlay stays (labelled) and the reason is kept on the row
                    fields = None
                    extra["position_fields_error"] = f"{type(e).__name__}: {e}"
            if isinstance(fields, dict) and fields.get("mv_c") is not None:
                mv_c = int(fields["mv_c"])
                label = str(fields.get("mark_label") or "model")
                view = MarkView(cid, from_c(mv_c), str(fields.get("quality") or "computed"), label, "swap_model", fields.get("as_of"), None, "model",
                                {"overlay_c": mark_c, "margin_mode": r["margin_mode"]}, False, label)
                extra["position_fields"] = {k: v for k, v in fields.items() if k not in ("mv_c", "unrealised_c")}
                for k in ("units", "unit_label", "entry", "current"):
                    if fields.get(k) is not None:
                        extra[k] = fields[k]
            extra["cost_c"] = cost_c
            extra["cost_basis"] = "OTC contract cost: premium / upfront paid (GL 1335) less received (GL 2345)"
            finish(cid, inst, "SWAP", sign * (int(r["notional_c"]) // 100), cost_c, view, mv_c, int(r["notional_c"]), mv_c - cost_c, r["currency"], [], None, extra,
                   "OTC derivatives")

        for r in conn.execute("SELECT * FROM private_loans WHERE account_id=? AND status='OPEN' ORDER BY loan_id", (aid,)).fetchall():
            iid = r["instrument_id"]
            inst = _instrument(conn, self.instruments, iid, cache)
            gl = glmap.get(iid, {})
            cost_c = gl.get("1130", (0, 0))[0]
            mark_c = None
            view = None
            if archived_detail is not None:
                pd = (archived_detail.get("positions") or {}).get(iid) or {}
                mark_c = None if pd.get("mv_c") is None else int(pd["mv_c"])
                view = MarkView(iid, None if mark_c is None else from_c(mark_c), pd.get("quality") or "computed", "stored snapshot (archived lifetime)", "snapshot", pd.get("as_of"), None,
                                "frozen at reset", None, False, "frozen at reset")
            else:
                v = snap.mark(iid) if snap is not None else None
                if v is not None and v.mark is not None and v.quality != "unavailable":
                    view, mark_c = v, to_c(v.mark)
                else:
                    pc = getattr(self.services, "private_credit", None) if self.services else None
                    if pc is not None and hasattr(pc, "pc_mark"):
                        try:
                            mark_c, inputs = pc.pc_mark(self.services, r)
                            view = MarkView(iid, from_c(int(mark_c)), "computed", "pc_model", "pc_model", self.clock.epoch(), None, "model", inputs, False, "amortised cost adjusted for the HY spread")
                        except Exception:   # noqa: BLE001
                            mark_c, view = None, unavailable(iid, "private-credit mark unavailable")
                    else:
                        view = unavailable(iid, "private credit marks: not yet built")
            unreal = None if mark_c is None else int(mark_c) - cost_c
            extra = {"principal_c": int(r["principal_c"]), "borrower": r["borrower"], "accrued_c": gl.get("1220", (0, 0))[0], "next_payment_date": r["next_payment_date"],
                     "maturity_date": r["maturity_date"], "index": r["index_name"], "spread": r["spread"], "issue_price": r["issue_price"]}
            finish(iid, inst, "PRIVATE_LOAN", int(r["principal_c"]) // 100, cost_c, view, mark_c, mark_c, unreal, r["currency"], [], None, extra, "private credit")
        fxm = getattr(self.services, "fx", None) if self.services else None
        if archived_detail is None and fxm is not None and hasattr(fxm, "forward_overlays"):
            for o in fxm.forward_overlays(self.services, conn, aid, snap):
                if o.get("mtm_c") is None:
                    continue
                mtm = int(o["mtm_c"])
                # V104R2-BW-09: the mark's time is its spot input's (a stale EURUSD shows its own age), the snapshot's only without one
                fin = ((o.get("inputs") or {}).get("forward") or {}) if isinstance(o.get("inputs"), dict) else {}
                spot = fin.get("spot") if isinstance(fin, dict) else None
                qt = None
                if isinstance(spot, dict):
                    for k in ("quote_time", "as_of", "fetched_at"):
                        if isinstance(spot.get(k), (int, float)) and not isinstance(spot.get(k), bool) and spot.get(k) > 0:
                            qt = int(spot[k])
                            break
                if qt is None and snap is not None:
                    qt = snap.as_of()
                view = MarkView(o["deal_id"], from_c(mtm), o.get("quality") or "computed", "FX forward (covered interest parity)", "cip",
                                qt, None, "model", o.get("inputs"), False, o.get("note") or "")
                # V104R1-02 / -03: a desk forward is shown by its pair and legs (it has no instrument row), its quantity is the pair's
                # base amount (+ bought, - sold: fx.forward_mtm's sign) and its notional the USD amount it exchanges - the USD leg when
                # it has one, else the base amount at its USD rate (None without a rate: never a guessed notional); the MTM stays the
                # market value (NAV carries the unposted MTM until the value date)
                buy_ccy, sell_ccy = str(o.get("buy_ccy") or ""), str(o.get("sell_ccy") or "")
                buy_c, sell_c = int(o.get("buy_c") or 0), int(o.get("sell_c") or 0)
                pair = str(o.get("pair") or (buy_ccy + sell_ccy))
                base = pair[:3]
                buying_base = buy_ccy == base
                base_c = buy_c if buying_base else sell_c
                sign = 1 if buying_base else -1
                if "USD" in (buy_ccy, sell_ccy):
                    usd_c = buy_c if buy_ccy == "USD" else sell_c
                else:
                    r_base = rate_of(base)
                    usd_c = None if r_base is None else fx_c(base_c, r_base)
                notional = None if usd_c is None else sign * abs(int(usd_c))
                legs = f"buy {buy_ccy} {fmt_money(buy_c)} / sell {sell_ccy} {fmt_money(sell_c)}"
                try:
                    k_px = qprice(Decimal(str(o.get("rate")))) if o.get("rate") not in (None, "") else None
                except Exception:   # noqa: BLE001 - a rate that does not parse leaves the column empty
                    k_px = None
                finish(o["deal_id"], None, "FX_FORWARD", sign * (abs(base_c) // 100), 0, view, mtm, notional, mtm, "USD", [], None,
                       {"pair": pair, "value_date": o.get("value_date"), "buy": [buy_ccy, buy_c], "sell": [sell_ccy, sell_c],
                        "rate": o.get("rate"), "desk_forward": True, "qty_unit": base, "deal_id": o["deal_id"],
                        "notional_basis": ("the USD leg" if "USD" in (buy_ccy, sell_ccy) else f"{base} amount at its USD rate") if notional is not None else f"no USD rate for {base}"},
                       "FX forwards", symbol=f"{pair} fwd", name=f"FX forward {o['deal_id']}: {legs}, value {o.get('value_date')}", avg_px=k_px)
        return out

    @staticmethod
    def _fx_rate(snap, ccy: str) -> Optional[Decimal]:
        if ccy == "USD":
            return Decimal(1)
        if snap is None:
            return None
        v = snap.fx(ccy)
        if v is None or v.mark is None or v.quality == "unavailable":
            return None
        return v.mark

    def _account_calc(self, conn: sqlite3.Connection, acct: dict, snap, archived_detail: Optional[dict] = None, day_base: Optional[dict] = None) -> _Calc:
        aid = acct["account_id"]
        c = _Calc(aid)
        worst = 0
        for r in conn.execute("SELECT gl, currency, balance_c, base_c FROM gl_balances WHERE account_id=?", (aid,)).fetchall():
            gl, ccy, bal, base = r["gl"], r["currency"], int(r["balance_c"]), int(r["base_c"])
            kind = CHART[gl].kind
            c.gl[(gl, ccy)] = c.gl.get((gl, ccy), 0) + natural(gl, bal)
            if kind in ("ASSET", "LIABILITY"):
                c.S[ccy] = c.S.get(ccy, 0) + bal
                c.B[ccy] = c.B.get(ccy, 0) + base
            elif gl == "3000":
                c.capital_c += natural(gl, base)
            elif gl == "3100":
                c.transfers_c += natural(gl, base)
            else:
                bucket = pl_bucket(gl)              # PORTFOLIO-08: 4451 / 4453 financing, 4452 income (storage signs, ``pl_amount``)
                if bucket == "realised":
                    c.realised_c += pl_amount(gl, natural(gl, base))
                elif bucket == "income":
                    c.income_c += pl_amount(gl, natural(gl, base))
                elif bucket == "financing":
                    c.financing_c += pl_amount(gl, natural(gl, base))
                elif bucket == "fees":
                    c.fees_c += pl_amount(gl, natural(gl, base))
        rates: Dict[str, Optional[Decimal]] = {}

        def rate_of(ccy: str) -> Optional[Decimal]:
            if ccy not in rates:
                if archived_detail is not None:
                    fxm = archived_detail.get("fx") or {}
                    rates[ccy] = Decimal(1) if ccy == "USD" else (Decimal(str(fxm[ccy])) if fxm.get(ccy) else None)
                else:
                    rates[ccy] = self._fx_rate(snap, ccy)
            return rates[ccy]
        c.rates = rates
        for (_gl, ccy) in list(c.gl):
            rate_of(ccy)
        c.positions = self._positions_of(conn, acct, snap, archived_detail, day_base, rate_of)
        for p in c.positions:
            if p.unrealised_c is None:
                c.missing.append(p.instrument_id)
                worst = max(worst, QUALITY_RANK["unavailable"])
                # MONEY-5: NAV (= S + U) carries an unpriced position at its cost row (S), so assets / liabilities do too
                if p.kind not in ("FUTURE", "SWAP") and p.cost_c:
                    rate = rate_of(p.currency)
                    if rate is not None:
                        b = fx_c(int(p.cost_c), rate)
                        if b >= 0:
                            c.cost_long_c += b
                        else:
                            c.cost_short_c += -b
                continue
            worst = max(worst, QUALITY_RANK.get(p.mark.quality, 5))
            c.U[p.currency] = c.U.get(p.currency, 0) + int(p.unrealised_c)
            if p.kind == "FUTURE":
                c.futures_ote_c += int(p.unrealised_base_c or 0)
            elif p.kind in ("SWAP", "FX_FORWARD"):           # an overlay at market (a desk FX forward like a swap: unposted MTM)
                if (p.mv_base_c or 0) >= 0:
                    c.swaps_pos_c += int(p.mv_base_c or 0)
                else:
                    c.swaps_neg_c += -int(p.mv_base_c or 0)
            elif p.mv_base_c is not None:
                if p.mv_base_c >= 0:
                    c.mv_long_c += p.mv_base_c
                else:
                    c.mv_short_c += -p.mv_base_c
        c.quality = {v: k for k, v in QUALITY_RANK.items() if k != "live"}.get(worst, "delayed") if c.positions else "delayed"
        for ccy in sorted(set(c.S) | set(c.U)):
            S, U, B = c.S.get(ccy, 0), c.U.get(ccy, 0), c.B.get(ccy, 0)
            rate = rate_of(ccy)
            if rate is None:
                if S or U:
                    c.fx_missing.append(ccy)
                continue
            total = fx_c(S + U, rate)
            at_cost = fx_c(S, rate)
            c.nav_c += total
            c.unrealised_c += total - at_cost
            if ccy != "USD":
                c.fx_result_c += at_cost - B
        for (gl, ccy), v in c.gl.items():
            if gl in NAV_ASSET_GLS:
                c.assets_native[ccy] = c.assets_native.get(ccy, 0) + v
            elif gl in NAV_LIAB_GLS:
                c.liab_native[ccy] = c.liab_native.get(ccy, 0) + v
            rate = rate_of(ccy)
            if gl == "1010":
                c.cash_c += fx_c(v, rate) if rate is not None else 0
            if gl in ("1200", "1250"):
                c.pending_c += fx_c(v, rate) if rate is not None else 0
            if gl in ("2100", "2350"):
                c.pending_c -= fx_c(v, rate) if rate is not None else 0
        return c

    def _calcs(self, conn: sqlite3.Connection, sc: Scope, snap, with_day: bool = False) -> List[_Calc]:
        out = []
        today = accounting_day(self.clock.now()).isoformat()
        for a in sc.accounts:
            archived = None
            if sc.archived:
                row = _last_snapshot(conn, a["account_id"])
                archived = json.loads(row["detail_json"]) if row else {"positions": {}, "fx": {}}
            day_base = None
            if with_day:
                row = _last_snapshot(conn, a["account_id"], today)
                day_base = json.loads(row["detail_json"]) if row else {"positions": {}}
            out.append(self._account_calc(conn, a, snap, archived, day_base))
        return out

    def positions(self, scope: str, snapshot=None) -> List[PositionView]:
        """Every open position of the scope (account id, ``{lifetime}:ALL``, ``ALL`` or ``ALL_BOOKS``) incl. swaps and
        private loans; marks from the snapshot (archived lifetimes: the stored snapshot); ``weight`` of the account's NAV."""
        snap = self._snapshot(snapshot)
        with self._conn() as conn:
            sc = self._scope(conn, scope)
            out: List[PositionView] = []
            for calc in self._calcs(conn, sc, snap, with_day=True):
                for p in calc.positions:
                    # V104R1-03: a share of the account's NAV only when that NAV is positive (a share of a negative NAV reads backwards)
                    p.weight = (Decimal(p.mv_base_c) / Decimal(calc.nav_c)).quantize(Decimal("0.0001")) if (calc.nav_c > 0 and p.mv_base_c is not None) else None
                    out.append(p)
            return out

    def position(self, account_id: str, instrument_id: str, snapshot=None) -> Optional[PositionView]:
        for p in self.positions(account_id, snapshot):
            if p.instrument_id == instrument_id:
                return p
        return None

    def lots(self, account_id: str, instrument_id: str) -> List[LotView]:
        with self._conn() as conn:
            return [LotView(int(l["lot_id"]), l["side"], ny_of(int(l["open_ts"])).date().isoformat(), int(l["open_ts"]), int(l["qty_open"]), int(l["qty_remaining"]),
                            Decimal(l["unit_cost"]), int(l["cost_c"]), l["open_fill_id"], l["lock_until"], l["currency"])
                    for l in conn.execute("SELECT * FROM lots WHERE account_id=? AND instrument_id=? AND qty_remaining > 0 ORDER BY open_ts, lot_id", (account_id, instrument_id)).fetchall()]

    # ---- closed positions (PORTFOLIO-03) ---------------------------------------------------------------------------------
    CLOSE_HOW = {"EXPIRY": "expired", "EXERCISE": "exercised", "ASSIGNMENT": "assigned", "FUTURES_FINAL": "final settlement", "MATURITY": "matured",
                 "NEGOTIATED": "sold (negotiated)"}

    def closed_positions(self, scope: str, from_date=None, to_date=None) -> List[ClosedPositionView]:
        """PORTFOLIO-03 (FINSIM1_PLAN P3 task 4): the closed positions of a scope, newest close first, optionally only those closed in
        ``from_date`` .. ``to_date`` (ISO dates, inclusive; the close's New York date):

        - listed round trips from the fills in time order per account and instrument: a position opens when the quantity leaves zero
          and closes when it returns to zero (a fill that crosses zero closes one position and opens the next with the rest).  Per
          row: open / close dates, the quantity opened, the average open and close prices, realised (the realised lines of the closing
          fills' entries - lot relief - and, for a future, its variation margin from the open to the close business date), fees (the
          fee lines of every fill of the position), income and financing (their lines on the instrument dated from the open to the
          close business date), days held, the opening and closing fill ids and how it closed (sold / covered / expired / exercised /
          assigned / final settlement / matured);
        - OTC contracts TERMINATED or MATURED: every line of the contract (realised 4450 incl. the released cost basis, financing
          4451 / 4453, income 4452, fees);
        - stock borrows returned: the borrow fees and rebates of the borrow's dates (the financing of the short listed above; not
          added again in a total).
        Money in base cents; financing and fees are positive costs; total = realised + income - financing - fees.  Read only."""
        fd = None if from_date in (None, "") else str(from_date)[:10]
        td = None if to_date in (None, "") else str(to_date)[:10]
        with self._conn() as conn:
            sc = self._scope(conn, scope)
            ids = [a["account_id"] for a in sc.accounts]
            accts = {a["account_id"]: a for a in sc.accounts}
            if not ids:
                return []
            q = ",".join("?" for _ in ids)
            cache: dict = {}
            pl_gls = tuple(PL_REALISED_GLS) + tuple(PL_INCOME_GLS) + tuple(PL_FINANCING_GLS) + tuple(PL_FEE_GLS)
            gq = ",".join("?" for _ in pl_gls)
            lines: Dict[Tuple[str, str], List[tuple]] = {}
            for r in conn.execute(f"SELECT l.account_id, l.instrument_id, l.gl, l.base_c, e.entry_id, e.business_date FROM journal_lines l"
                                  f" JOIN journal_entries e ON e.entry_id=l.entry_id WHERE l.account_id IN ({q}) AND COALESCE(l.instrument_id, '') <> ''"
                                  f" AND l.gl IN ({gq})", ids + list(pl_gls)).fetchall():
                bucket = pl_bucket(r["gl"])
                if bucket is None:
                    continue
                lines.setdefault((r["account_id"], r["instrument_id"]), []).append(
                    (int(r["entry_id"]), str(r["business_date"]), bucket, pl_amount(r["gl"], natural(r["gl"], int(r["base_c"] or 0)))))
            fills = [dict(r) for r in conn.execute(f"SELECT f.fill_id, f.order_id, f.account_id, f.instrument_id, f.side, f.qty, f.price, f.fee_c, f.ts, f.trade_date,"
                                                   f" f.origin, f.entry_id, e.business_date FROM fills f JOIN journal_entries e ON e.entry_id=f.entry_id"
                                                   f" WHERE f.account_id IN ({q}) ORDER BY f.ts, f.fill_id", ids).fetchall()]
            borrows = [dict(r) for r in conn.execute(f"SELECT * FROM borrows WHERE account_id IN ({q}) ORDER BY opened_ts, borrow_id", ids).fetchall()]
            try:
                otc_rows = [dict(r) for r in conn.execute(f"SELECT * FROM otc_contracts WHERE account_id IN ({q}) AND status IN ('TERMINATED','MATURED')"
                                                          " ORDER BY contract_id", ids).fetchall()]
            except sqlite3.Error:
                otc_rows = []
            out: List[ClosedPositionView] = []

            def sums(key, pick) -> Dict[str, int]:
                s = {"realised": 0, "income": 0, "financing": 0, "fees": 0}
                for eid, bd, bucket, amt in lines.get(key, []):
                    if pick(eid, bd, bucket):
                        s[bucket] += amt
                return s

            def ny_date(ts) -> str:
                return ny_of(int(ts)).date().isoformat()

            # ---- listed round trips
            run: Dict[Tuple[str, str], int] = {}
            episodes: Dict[Tuple[str, str], dict] = {}
            done: List[Tuple[Tuple[str, str], dict]] = []
            for f in fills:
                key = (f["account_id"], f["instrument_id"])
                signed = int(f["qty"]) if f["side"] == "BUY" else -int(f["qty"])
                pos = run.get(key, 0)
                left = abs(signed)
                if pos != 0 and (pos > 0) != (signed > 0):
                    ep = episodes[key]
                    take = min(left, abs(pos))
                    ep["closes"].append((take, Decimal(str(f["price"])), f))
                    left -= take
                    pos += take if signed > 0 else -take
                    if pos == 0:
                        ep["close_ts"] = int(f["ts"])
                        done.append((key, ep))
                        episodes.pop(key, None)
                if left > 0:
                    if pos == 0:
                        episodes[key] = {"side": "LONG" if signed > 0 else "SHORT", "open_ts": int(f["ts"]), "opens": [], "closes": []}
                    episodes[key]["opens"].append((left, Decimal(str(f["price"])), f))
                    pos += left if signed > 0 else -left
                run[key] = pos
            for key, ep in done:
                aid, iid = key
                inst = _instrument(conn, self.instruments, iid, cache)
                kind = inst.kind if inst is not None else "EQUITY"
                fill_entries = {int(x[2]["entry_id"]) for x in ep["opens"] + ep["closes"]}
                close_entries = {int(x[2]["entry_id"]) for x in ep["closes"]}
                open_bd = min(str(x[2]["business_date"]) for x in ep["opens"])
                close_bd = max(str(x[2]["business_date"]) for x in ep["closes"])
                if kind == "FUTURE":
                    s = sums(key, lambda eid, bd, b: (b == "fees" and eid in fill_entries) or (b != "fees" and open_bd <= bd <= close_bd))
                else:
                    s = sums(key, lambda eid, bd, b: (b == "realised" and eid in close_entries) or (b == "fees" and eid in fill_entries)
                             or (b in ("income", "financing") and open_bd <= bd <= close_bd))
                qty_open = sum(x[0] for x in ep["opens"])
                qty_close = sum(x[0] for x in ep["closes"])
                avg_open = qprice((sum(Decimal(x[0]) * x[1] for x in ep["opens"]) / Decimal(qty_open)).quantize(Decimal("0.0001"))) if qty_open else None
                avg_close = qprice((sum(Decimal(x[0]) * x[1] for x in ep["closes"]) / Decimal(qty_close)).quantize(Decimal("0.0001"))) if qty_close else None
                hows = []
                for x in ep["closes"]:
                    o = str(x[2]["origin"] or "ORDER")
                    w = self.CLOSE_HOW.get(o) or ("sold" if ep["side"] == "LONG" else "covered")
                    if w not in hows:
                        hows.append(w)
                od, cd = ny_date(ep["open_ts"]), ny_date(ep["close_ts"])
                if (fd and cd < fd) or (td and cd > td):
                    continue
                cp = CLEARING_OF_KIND.get(kind, "exchange-cleared")
                if ep["side"] == "SHORT":
                    b = next((b for b in borrows if b["account_id"] == aid and b["instrument_id"] == iid and ep["open_ts"] - 86400 <= int(b["opened_ts"]) <= ep["close_ts"]), None)
                    if b is not None:
                        cp = f"stock borrow {b['borrow_id']}: {lender_label(b.get('lender'))}"
                total = s["realised"] + s["income"] - s["financing"] - s["fees"]
                out.append(ClosedPositionView(aid, iid, inst.symbol if inst else iid, inst.name if inst else iid, kind, asset_class_of(kind), ep["side"],
                                              inst.currency if inst else "USD", qty_open, od, cd, ep["open_ts"], ep["close_ts"], avg_open, avg_close,
                                              s["realised"], s["fees"], s["income"], s["financing"], total, (date.fromisoformat(cd) - date.fromisoformat(od)).days,
                                              [x[2]["fill_id"] for x in ep["opens"]], [x[2]["fill_id"] for x in ep["closes"]], " / ".join(hows), cp, "fills",
                                              "futures: realised is the variation margin from the open to the close" if kind == "FUTURE" else ""))
            # ---- OTC contracts terminated / matured
            for c in otc_rows:
                cid = c["contract_id"]
                key = (c["account_id"], cid)
                s = sums(key, lambda eid, bd, b: True)
                try:
                    terms = json.loads(c.get("terms_json") or "{}")
                except ValueError:
                    terms = {}
                od = str(c.get("trade_date") or c.get("start_date") or "")[:10]
                cd = ny_date(c["closed_ts"]) if c.get("closed_ts") else str(c.get("maturity_date") or "")[:10]
                if (fd and cd < fd) or (td and cd > td):
                    continue
                inst = _instrument(conn, self.instruments, cid, cache)
                extra = {"kind": c["kind"], "broker": c.get("broker"), "maturity_date": c.get("maturity_date")}
                try:
                    from .risk import contract_label
                    label = contract_label(inst, extra)
                except Exception:   # noqa: BLE001 - the label is a convenience
                    label = cid
                units = terms.get("units")
                qty = int(units) if units not in (None, "") else int(c["notional_c"]) // 100
                side = ("pay" if terms.get("pay_fixed") or terms.get("receiver") is False else "receive") if c["kind"] in ("IRS", "TRS") else str(terms.get("side") or "")
                try:
                    days = (date.fromisoformat(cd) - date.fromisoformat(od)).days if od and cd else 0
                except ValueError:
                    days = 0
                total = s["realised"] + s["income"] - s["financing"] - s["fees"]
                out.append(ClosedPositionView(c["account_id"], cid, cid, label, "SWAP", asset_class_of("SWAP", extra), side, c["currency"], qty, od, cd,
                                              0, int(c.get("closed_ts") or 0), None, None, s["realised"], s["fees"], s["income"], s["financing"], total, days, [], [],
                                              str(c["status"]).lower(), counterparty_of("SWAP", accts.get(c["account_id"]), extra), "otc",
                                              "realised includes the released contract cost basis (premium / upfront)"))
            # ---- stock borrows returned
            for b in borrows:
                if b.get("status") != "CLOSED":
                    continue
                key = (b["account_id"], b["instrument_id"])
                od = str(b.get("opened_date") or ny_date(b["opened_ts"]))[:10]
                cd = ny_date(b["closed_ts"]) if b.get("closed_ts") else od
                if (fd and cd < fd) or (td and cd > td):
                    continue
                s = {"income": 0, "financing": 0}
                for eid, bd, bucket, amt in lines.get(key, []):
                    if od <= bd <= cd and bucket in ("income", "financing"):
                        s[bucket] += amt
                inst = _instrument(conn, self.instruments, b["instrument_id"], cache)
                out.append(ClosedPositionView(b["account_id"], b["instrument_id"], inst.symbol if inst else b["instrument_id"],
                                              f"stock borrow {b['borrow_id']}", "BORROW", "Financing (stock borrow)", "BORROWED", b.get("currency") or "USD",
                                              int(b["qty"]), od, cd, int(b["opened_ts"]), int(b.get("closed_ts") or 0), None, None, 0, 0, s["income"], s["financing"],
                                              s["income"] - s["financing"], (date.fromisoformat(cd) - date.fromisoformat(od)).days, [], [], "returned",
                                              lender_label(b.get("lender")), "borrow",
                                              "the financing of the short sale listed with it: its fees and rebates are already in that position's financing"))
            out.sort(key=lambda r: (-(r.close_ts or 0), r.close_date, r.account_id, r.instrument_id), reverse=False)
            return out

    def closed_lots(self, account_id: str, instrument_id: str) -> Tuple[List[dict], List[dict]]:
        """PORTFOLIO-03 / -05: ``(closed lots, reliefs)`` of one position - the lots with nothing remaining and every lot_relief row of the
        position's lots (closing fill, quantity, cost relieved, proceeds, realised), money in the instrument's currency."""
        with self._conn() as conn:
            lots = [dict(r) for r in conn.execute("SELECT * FROM lots WHERE account_id=? AND instrument_id=? AND qty_remaining=0 ORDER BY open_ts, lot_id",
                                                  (account_id, instrument_id)).fetchall()]
            reliefs = [dict(r) for r in conn.execute("SELECT r.*, f.ts, f.trade_date, f.price, f.side FROM lot_relief r JOIN lots l ON l.lot_id=r.lot_id"
                                                     " JOIN fills f ON f.fill_id=r.close_fill_id WHERE l.account_id=? AND l.instrument_id=? ORDER BY f.ts, r.id",
                                                     (account_id, instrument_id)).fetchall()]
            for l in lots:
                l["open_date"] = ny_of(int(l["open_ts"])).date().isoformat()
            return lots, reliefs

    # ---- NAV / balance sheet ------------------------------------------------------------------------------------------
    def _nav_from_calcs(self, sc: Scope, calcs: List[_Calc], as_of: int) -> NavView:
        worst = max((QUALITY_RANK.get(c.quality, 0) for c in calcs), default=0)
        quality = {v: k for k, v in QUALITY_RANK.items() if k != "live"}.get(worst, "delayed")
        missing = [m for c in calcs for m in c.missing]
        fxm = sorted({m for c in calcs for m in c.fx_missing})
        if missing or fxm:
            quality = "unavailable"
        nav = sum(c.nav_c for c in calcs)
        capital = sum(c.capital_c for c in calcs)
        transfers = sum(c.transfers_c for c in calcs)
        realised = sum(c.realised_c for c in calcs)
        income = sum(c.income_c for c in calcs)
        financing = sum(c.financing_c for c in calcs)
        fees = sum(c.fees_c for c in calcs)
        assets, liabs = self._bs_totals(calcs)
        return NavView(sc.key, nav, assets, liabs, quality, bool(missing or fxm), missing, as_of, sum(c.cash_c for c in calcs), sum(c.pending_c for c in calcs),
                       sum(c.mv_long_c for c in calcs), sum(c.mv_short_c for c in calcs), sum(c.unrealised_c for c in calcs), sum(c.fx_result_c for c in calcs),
                       capital, transfers, realised, income, financing, fees, nav - capital - transfers, {c.account_id: c.nav_c for c in calcs}, list(sc.lifetime_ids),
                       sc.archived, fxm, ("%d position(s) at cost: no price" % len(missing)) if missing else "")

    @staticmethod
    def _bs_totals(calcs: List[_Calc]) -> Tuple[int, int]:
        """§6.3 assets / liabilities in base at the rates each calc used: cash, receivables, accrued income, margin and
        collateral (NAV asset codes) + long positions at market + futures OTE (positive) + swaps (positive);
        payables, accrued expenses, financing (NAV liability codes) + shorts at market + OTE (negative) + swaps (negative).
        An unpriced position is carried AT COST like NAV carries it (MONEY-5: a long's cost row in assets, a short's proceeds
        in liabilities), so ``nav_c == assets_c - liabilities_c`` (to FX rounding) whatever is priced."""
        assets = liabs = 0
        for c in calcs:
            for ccy, v in c.assets_native.items():
                rate = c.rates.get(ccy)
                if rate is not None:
                    assets += fx_c(v, rate)
            for ccy, v in c.liab_native.items():
                rate = c.rates.get(ccy)
                if rate is not None:
                    liabs += fx_c(v, rate)
            assets += c.mv_long_c + max(0, c.futures_ote_c) + c.swaps_pos_c + c.cost_long_c
            liabs += c.mv_short_c + max(0, -c.futures_ote_c) + c.swaps_neg_c + c.cost_short_c
        return assets, liabs

    def nav(self, scope: str, snapshot=None) -> NavView:
        snap = self._snapshot(snapshot)
        with self._conn() as conn:
            sc = self._scope(conn, scope)
            if sc.archived:
                return self._nav_archived(conn, sc, snap)
            calcs = self._calcs(conn, sc, snap)
            return self._nav_from_calcs(sc, calcs, snap.as_of())

    def _nav_archived(self, conn: sqlite3.Connection, sc: Scope, snap) -> NavView:
        key = sc.snapshot_key
        row = _last_snapshot(conn, key) if key else None
        if row is None:
            calcs = self._calcs(conn, sc, snap)
            view = self._nav_from_calcs(sc, calcs, snap.as_of())
            view.note = "archived lifetime without a stored snapshot: figures at the stored marks (none) — partial"
            view.partial = True
            return view
        calcs = self._calcs(conn, sc, snap)
        view = self._nav_from_calcs(sc, calcs, int(row["ts"]))
        view.nav_c = int(row["nav_c"])
        view.unrealised_c = int(row["unrealised_c"])
        view.fx_result_c = int(row["fx_result_c"])
        view.quality = row["quality"]
        view.partial = bool(row["partial"])
        view.retained_c = view.nav_c - view.capital_c - view.transfers_c
        view.note = f"frozen at reset ({row['snap_date']})"
        return view

    def balance_sheet(self, scope: str, snapshot=None) -> BalanceSheet:
        snap = self._snapshot(snapshot)
        with self._conn() as conn:
            sc = self._scope(conn, scope)
            calcs = self._calcs(conn, sc, snap)
            nav = self._nav_from_calcs(sc, calcs, snap.as_of()) if not sc.archived else self._nav_archived(conn, sc, snap)
            rows: List[dict] = []

            def add(section, label, key, native: Dict[str, int], base_c: int, sub=False):
                if any(native.values()) or base_c:
                    rows.append({"section": section, "label": label, "key": key, "native": {k: v for k, v in native.items() if v}, "base_c": base_c, "sub": sub})

            def gl_rows(section, label, gls, sign=1):
                native: Dict[str, int] = {}
                base = 0
                for c in calcs:
                    for (gl, ccy), v in c.gl.items():
                        if gl in gls and v:
                            native[ccy] = native.get(ccy, 0) + v
                            rate = c.rates.get(ccy)
                            base += fx_c(v, rate) if rate is not None else 0
                add(section, label, ",".join(gls), native, base)
                return base

            gl_rows("assets", "Cash — settled", ("1010",))
            gl_rows("assets", "Receivables (sales, FX)", ("1200", "1250"))
            gl_rows("assets", "Accrued income", ("1210", "1220", "1225", "1230", "1410", "1420"))
            cost_long = gl_rows("assets", "Securities and options at cost (long)", ("1100", "1110", "1120", "1700"))
            add("assets", "Unrealised on long positions", "unrealised_long", {}, sum(p.unrealised_base_c or 0 for c in calcs for p in c.positions if p.kind not in ("FUTURE", "SWAP", "FX_FORWARD", "PRIVATE_LOAN") and p.qty > 0), True)
            gl_rows("assets", "Private credit at amortised cost", ("1130",))
            add("assets", "Private credit mark adjustment", "unrealised_pc", {}, sum(p.unrealised_base_c or 0 for c in calcs for p in c.positions if p.kind == "PRIVATE_LOAN"), True)
            gl_rows("assets", "Futures initial margin", ("1300",))
            # SR-13: option margin is a reservation (decision s7.6): 1310 holds nothing after the start-up migration, so no row shows; an
            # archived lifetime reset before it keeps its historical deposit row
            gl_rows("assets", "Option margin deposit (before decision s7.6)", ("1310",))
            add("assets", "Futures open trade equity (positive)", "ote_pos", {}, sum(max(0, c.futures_ote_c) for c in calcs))
            gl_rows("assets", "OTC margin posted (IM, VM)", ("1320", "1330"))
            add("assets", "OTC swaps and FX forwards at market (positive)", "swaps_pos", {}, sum(c.swaps_pos_c for c in calcs))
            gl_rows("assets", "Cross-currency notional receivable", ("1360",))
            gl_rows("assets", "Cash collateral posted (held at lender)", ("1400",))
            gl_rows("assets", "Reverse repo", ("1500",))
            gl_rows("liabilities", "Payables (purchases, FX)", ("2100", "2350"))
            gl_rows("liabilities", "Securities and options sold short at proceeds", ("2600", "2900"))
            add("liabilities", "Unrealised on short positions (mark − proceeds)", "unrealised_short", {}, -sum(p.unrealised_base_c or 0 for c in calcs for p in c.positions if p.kind not in ("FUTURE", "SWAP", "FX_FORWARD", "PRIVATE_LOAN") and p.qty < 0), True)
            gl_rows("liabilities", "Accrued expenses", ("2300", "2310", "2320", "2340", "2420", "2755"))
            add("liabilities", "Futures open trade equity (negative)", "ote_neg", {}, sum(max(0, -c.futures_ote_c) for c in calcs))
            gl_rows("liabilities", "OTC variation margin received", ("2330",))
            add("liabilities", "OTC swaps and FX forwards at market (negative)", "swaps_neg", {}, sum(c.swaps_neg_c for c in calcs))
            gl_rows("liabilities", "Cross-currency notional payable", ("2360",))
            gl_rows("liabilities", "Repo", ("2500",))
            gl_rows("liabilities", "Cash collateral received (shares lent)", ("2520",))
            gl_rows("liabilities", "Prime-broker financing", ("2700",))
            gl_rows("liabilities", "Currency loans", ("2750",))
            assets = sum(r["base_c"] for r in rows if r["section"] == "assets")
            liabs = sum(r["base_c"] for r in rows if r["section"] == "liabilities")
            memo = {"capital_c": nav.capital_c, "advances_c": nav.transfers_c, "fx_clearing_base_c": 0, "retained_c": nav.retained_c,
                    "identity": "NAV = capital + advances + retained", "fx_result_c": nav.fx_result_c, "unrealised_c": nav.unrealised_c}
            return BalanceSheet(sc.key, rows, assets, liabs, nav.nav_c, nav.nav_c - (assets - liabs), memo, nav.quality, nav.partial, nav.as_of)

    # ---- P&L ------------------------------------------------------------------------------------------------------------
    def _components(self, conn: sqlite3.Connection, sc: Scope, snap) -> dict:
        if sc.archived:
            nav = self._nav_archived(conn, sc, snap)
        else:
            nav = self._nav_from_calcs(sc, self._calcs(conn, sc, snap), snap.as_of())
        return {"nav_c": nav.nav_c, "unrealised_c": nav.unrealised_c, "fx_result_c": nav.fx_result_c, "realised_c": nav.realised_c, "income_c": nav.income_c,
                "financing_c": nav.financing_c, "fees_c": nav.fees_c, "capital_c": nav.capital_c, "transfers_c": nav.transfers_c, "quality": nav.quality,
                "partial": nav.partial, "missing": nav.missing, "settled_c": nav.cash_c, "pending_c": nav.pending_c, "mv_long_c": nav.mv_long_c, "mv_short_c": nav.mv_short_c}

    def _baseline_rows(self, conn: sqlite3.Connection, sc: Scope, period: str):
        """The baseline snapshot rows (one per snapshot key of the scope) for the period, or None."""
        acct_day = accounting_day(self.clock.now())
        if period == "ITD":
            return None
        if period == "TODAY":
            before = acct_day.isoformat()
        elif period == "MTD":
            before = acct_day.replace(day=1).isoformat()
        elif period == "YTD":
            before = date(acct_day.year, 1, 1).isoformat()
        else:
            raise ValueError(f"period must be one of {PERIODS}")
        keys = [sc.snapshot_key] if sc.snapshot_key else [f"{lid}:ALL" for lid in sc.lifetime_ids]
        rows = [_last_snapshot(conn, k, before) for k in keys]
        if not keys or any(r is None for r in rows):
            return None
        return rows

    def pnl(self, scope: str, period: str = "TODAY", snapshot=None) -> PnlView:
        """§6.5: ``pnl = NAV_t − NAV_0 − flows``; ``explained = Δrealised + Δincome − Δfinancing − Δfees + Δunrealised + Δfx``;
        residual 0 by construction.  A missing baseline -> ``degraded=True`` and ``baseline.inception`` (never invented)."""
        period = (period or "TODAY").upper()
        if period not in PERIODS:
            raise ValueError(f"period must be one of {PERIODS}")
        snap = self._snapshot(snapshot)
        with self._conn() as conn:
            sc = self._scope(conn, scope)
            now = self._components(conn, sc, snap)
            return self._pnl_from(conn, sc, period, now)

    def pnl_periods(self, scope: str, periods=PERIODS, snapshot=None) -> Dict[str, PnlView]:
        """The P&L of several periods on ONE snapshot (the Dashboard's TODAY / MTD / YTD / ITD, FINSIM1 P1b-R1): the scope's current
        figures (``_components``: every position's calc) are computed once and each period is measured against its own baseline -
        exactly what ``pnl`` returns for that period, since both go through ``_pnl_from``.  Keyed by the periods as given; ValueError
        for a period not in ``PERIODS``, as ``pnl``."""
        wanted = [(p, (p or "TODAY").upper()) for p in periods]
        for _p, up in wanted:
            if up not in PERIODS:
                raise ValueError(f"period must be one of {PERIODS}")
        snap = self._snapshot(snapshot)
        with self._conn() as conn:
            sc = self._scope(conn, scope)
            now = self._components(conn, sc, snap)
            return {p: self._pnl_from(conn, sc, up, now) for p, up in wanted}

    def _pnl_from(self, conn: sqlite3.Connection, sc: Scope, period: str, now: dict) -> PnlView:
        """One period's ``PnlView`` (§6.5, ``pnl``) from the scope's current figures ``now`` (``_components``) and the period's baseline
        snapshot rows (the latest restated version where one exists)."""
        rows = self._baseline_rows(conn, sc, period)
        if rows:
            b = {k: sum(int(r[k]) for r in rows) for k in ("nav_c", "unrealised_c", "fx_result_c", "realised_cum_c", "income_cum_c", "financing_cum_c", "fees_cum_c", "capital_cum_c", "transfers_cum_c")}
            baseline = {"inception": False, "snap_date": min(r["snap_date"] for r in rows), "ts": min(int(r["ts"]) for r in rows), "nav_c": b["nav_c"],
                        "restated": restated_label(next((r for r in rows if _rget(r, "restated")), None))}
            degraded = False
        else:
            b = {k: 0 for k in ("nav_c", "unrealised_c", "fx_result_c", "realised_cum_c", "income_cum_c", "financing_cum_c", "fees_cum_c", "capital_cum_c", "transfers_cum_c")}
            baseline = {"inception": True, "snap_date": None, "ts": None, "nav_c": 0}
            degraded = period != "ITD"
        flows = (now["capital_c"] - b["capital_cum_c"]) + (now["transfers_c"] - b["transfers_cum_c"])
        comps = {"realised_c": now["realised_c"] - b["realised_cum_c"], "income_c": now["income_c"] - b["income_cum_c"],
                 "financing_c": now["financing_c"] - b["financing_cum_c"], "fees_c": now["fees_c"] - b["fees_cum_c"],
                 "unrealised_c": now["unrealised_c"] - b["unrealised_c"], "fx_result_c": now["fx_result_c"] - b["fx_result_c"]}
        explained = comps["realised_c"] + comps["income_c"] - comps["financing_c"] - comps["fees_c"] + comps["unrealised_c"] + comps["fx_result_c"]
        pnl = now["nav_c"] - b["nav_c"] - flows
        return PnlView(sc.key, period, now["nav_c"], b["nav_c"], flows, pnl, explained, pnl - explained, comps, baseline, degraded, now["quality"], now["partial"])

    def flows(self, scope: str, from_ts: int, to_ts: int) -> int:
        """Capital deposits − withdrawals (3000) ± internal transfers into the scope (3100) with entry ts in (from, to], base."""
        with self._conn() as conn:
            sc = self._scope(conn, scope)
            ids = [a["account_id"] for a in sc.accounts]
            if not ids:
                return 0
            q = ",".join("?" for _ in ids)
            total = 0
            for r in conn.execute(f"SELECT l.gl, SUM(l.base_c) b FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id"
                                  f" WHERE l.account_id IN ({q}) AND l.gl IN ('3000','3100') AND e.ts > ? AND e.ts <= ? GROUP BY l.gl", ids + [int(from_ts), int(to_ts)]).fetchall():
                total += natural(r["gl"], int(r["b"] or 0))
            return total

    def snapshot_row(self, scope: str, snap_date: date, snapshot=None, dated: bool = False) -> dict:
        """Every ``nav_snapshots`` field for ``scope`` at ``snap_date`` (the New York date whose close it represents),
        incl. ``flows_day_c`` / ``pnl_day_c`` against the previous snapshot (M4; the latest restated version when one exists,
        C9i) and the ``detail_json`` the day P&L and archived views read.  The SNAPSHOT job (WP-08) inserts it with
        ``dated=True`` (PORTFOLIO-14: listed positions at their stored CLOSE / SETTLE of ``snap_date``, ``DatedMarks``); a
        lifetime reset's final row and the tests' helper value at the given snapshot.  ``detail_json`` records each position's
        mark kind, source and the date of the mark it used."""
        snap = self._snapshot(snapshot)
        with self._conn() as conn:
            sc = self._scope(conn, scope)
            key = sc.snapshot_key or scope
            marks = DatedMarks(conn, self.instruments, snap_date, snap, self.sessions) if (dated and not sc.archived) else snap
            calcs = self._calcs(conn, sc, marks)
            nav = self._nav_from_calcs(sc, calcs, snap.as_of())
            prev = _last_snapshot(conn, key, snap_date.isoformat())
            if prev is not None:
                flows_day = (nav.capital_c - int(prev["capital_cum_c"])) + (nav.transfers_c - int(prev["transfers_cum_c"]))
                pnl_day = nav.nav_c - int(prev["nav_c"]) - flows_day
            else:
                flows_day = nav.capital_c + nav.transfers_c
                pnl_day = nav.nav_c - flows_day
            positions = {}
            fx = {}
            for c in calcs:
                for p in c.positions:
                    realised, income, financing, fees = p.realised_itd_c, p.income_itd_c, -p.financing_itd_c, -p.fees_itd_c
                    positions[p.instrument_id] = {"account_id": p.account_id, "qty": p.qty, "cost_c": p.cost_c, "mv_c": p.mv_c, "mv_base_c": p.mv_base_c,
                                                  "unrealised_c": p.unrealised_base_c or 0, "pl_cum_c": realised + income - financing - fees,
                                                  "mark": None if p.mark.mark is None else str(p.mark.mark), "quality": p.mark.quality, "as_of": p.mark.quote_time,
                                                  **mark_labels(p.kind, p.mark)}
                for ccy in set(c.S) | set(c.U):
                    rate = self._fx_rate(snap, ccy)
                    if rate is not None:
                        fx[ccy] = str(rate)
            return {"account_id": key, "snap_date": snap_date.isoformat(), "ts": self.clock.epoch(), "nav_c": nav.nav_c, "settled_c": nav.cash_c, "pending_c": nav.pending_c,
                    "mv_long_c": nav.mv_long_c, "mv_short_c": nav.mv_short_c, "unrealised_c": nav.unrealised_c, "fx_result_c": nav.fx_result_c,
                    "realised_cum_c": nav.realised_c, "income_cum_c": nav.income_c, "financing_cum_c": nav.financing_c, "fees_cum_c": nav.fees_c,
                    "capital_cum_c": nav.capital_c, "transfers_cum_c": nav.transfers_c, "flows_day_c": flows_day, "pnl_day_c": pnl_day, "quality": nav.quality,
                    "partial": 1 if nav.partial else 0, "detail_json": dumps({"positions": positions, "fx": fx, "missing": nav.missing, "by_account": nav.by_account})}

    def nav_as_of(self, scope: str, d, snapshot=None, live_fallback: bool = True) -> dict:
        """C9d (FINSIM1_PLAN, PORTFOLIO-01): the ``nav_snapshots`` row of ``scope`` AS OF BUSINESS DATE ``d`` - positions and cash
        alike from the journal entries whose business_date <= d (a fill at 19:58 ET on D counts from D+1, its business date;
        never the ledger as it stands), listed positions at the dated marks of d (CLOSE / SETTLE / the option close; a weekend or
        holiday carries the previous trading day's), OTC contracts at their VM mark of d (``navhistory`` module docstring).  A
        listed name with no stored mark at all takes the live mark, labelled stale (``live_fallback``; else it is carried at
        cost and the row is partial).  ``flows_day`` / ``pnl_day`` chain to the latest row before d (C9i).  Nothing is written:
        the SNAPSHOT job inserts it for a late (catch-up) date and ``nav.restate`` stores it as a version with its split."""
        from . import navhistory
        snap = self._snapshot(snapshot) if live_fallback else None
        with self._conn() as conn:
            sc = self._scope(conn, scope)
            key = sc.snapshot_key or scope
            return navhistory.row_as_of(conn, self, key, [a["account_id"] for a in sc.accounts], d, live=snap)

    @staticmethod
    def insert_snapshot(conn: sqlite3.Connection, row: dict) -> None:
        cols = ("account_id", "snap_date", "ts", "nav_c", "settled_c", "pending_c", "mv_long_c", "mv_short_c", "unrealised_c", "fx_result_c", "realised_cum_c",
                "income_cum_c", "financing_cum_c", "fees_cum_c", "capital_cum_c", "transfers_cum_c", "flows_day_c", "pnl_day_c", "quality", "partial", "detail_json")
        conn.execute(f"INSERT OR REPLACE INTO nav_snapshots({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})", [row[c] for c in cols])

    # ---- treasury / transactions / obligations / liquidity ---------------------------------------------------------------
    def treasury(self, lifetime_id: Optional[str] = None, snapshot=None) -> TreasuryView:
        snap = self._snapshot(snapshot)
        with self._conn() as conn:
            if lifetime_id is None:
                lifetime_id = self._scope(conn, "ALL").lifetime_ids[0]
            tre = f"{lifetime_id}:TREASURY"
            sc = self._scope(conn, tre)
            calc = self._account_calc(conn, sc.accounts[0], snap)
            cash = {ccy: v for (gl, ccy), v in calc.gl.items() if gl == "1010"}
            capital_by = {ccy: v for (gl, ccy), v in calc.gl.items() if gl == "3000"}
            accrued = sum(v for (gl, ccy), v in calc.gl.items() if gl == "1230")
            advances = -calc.transfers_c
            by_acct = {}
            for r in conn.execute("SELECT g.account_id, SUM(g.base_c) b FROM gl_balances g JOIN accounts a ON a.account_id=g.account_id WHERE a.lifetime_id=? AND g.gl='3100' AND g.account_id<>? GROUP BY g.account_id",
                                  (lifetime_id, tre)).fetchall():
                by_acct[r["account_id"]] = natural("3100", int(r["b"] or 0))
            from .books import treasury_reserve_c as _reserve
            reserve = _reserve(conn, lifetime_id, "USD")                         # BM-01: the reserve of this lifetime's book
            avail = treasury_available_c(conn, lifetime_id, "USD", snap, self.instruments, self.clock.now())
            enc = sum(v for (gl, ccy), v in calc.gl.items() if gl in ENCUMBRANCE_GLS)
            retained = calc.nav_c - calc.capital_c + advances
            return TreasuryView(lifetime_id, tre, cash, accrued, calc.capital_c, capital_by, advances, by_acct, retained, avail, reserve, calc.nav_c, enc,
                                calc.nav_c == calc.capital_c + retained - advances,
                                {"treasury": tables.CONVENTIONS["TREASURY"], "available": "Treasury settled USD − reserve", "retained": "Treasury NAV − capital + advances",
                                 "identity": "Treasury NAV = capital + retained − advances"})

    def transactions(self, scope: str, *, from_date=None, to_date=None, kinds=None, instrument_id=None, limit: int = 500, offset: int = 0) -> List[TxnRow]:
        with self._conn() as conn:
            sc = self._scope(conn, scope)
            ids = [a["account_id"] for a in sc.accounts]
            if not ids:
                return []
            q = ",".join("?" for _ in ids)
            where = [f"e.entry_id IN (SELECT DISTINCT entry_id FROM journal_lines WHERE account_id IN ({q}))"]
            params: list = list(ids)
            if from_date:
                where.append("e.business_date >= ?")
                params.append(str(from_date))
            if to_date:
                where.append("e.business_date <= ?")
                params.append(str(to_date))
            if kinds:
                ks = list(kinds)
                where.append(f"e.kind IN ({','.join('?' for _ in ks)})")
                params.extend(ks)
            if instrument_id:
                where.append("e.entry_id IN (SELECT DISTINCT entry_id FROM journal_lines WHERE instrument_id=?)")
                params.append(instrument_id)
            sql = ("SELECT e.*, c.request_id FROM journal_entries e LEFT JOIN commands c ON c.id=e.command_id WHERE " + " AND ".join(where) +
                   " ORDER BY e.entry_id DESC LIMIT ? OFFSET ?")
            params.extend([int(limit), int(offset)])
            entries = conn.execute(sql, params).fetchall()
            out: List[TxnRow] = []
            id_set = set(ids)
            for e in entries:
                lines = [dict(l) for l in conn.execute("SELECT line_no, account_id, gl, currency, amount_c, base_c, fx_rate, fx_source, instrument_id, lot_id, counterparty, memo FROM journal_lines WHERE entry_id=? ORDER BY line_no", (e["entry_id"],)).fetchall()]
                net: Dict[str, int] = {}
                for l in lines:
                    l["gl_name"] = CHART[l["gl"]].name if l["gl"] in CHART else l["gl"]
                    if l["gl"] == "1010" and l["account_id"] in id_set:
                        net[l["currency"]] = net.get(l["currency"], 0) + int(l["amount_c"])
                fill = None
                if e["ref_type"] == "fill" and e["ref_id"]:
                    fill = conn.execute("SELECT instrument_id, qty, price, gross_c, fee_c, origin, side FROM fills WHERE fill_id=?", (e["ref_id"],)).fetchone()
                inst = fill["instrument_id"] if fill else next((l["instrument_id"] for l in lines if l["instrument_id"]), None)
                qty = (int(fill["qty"]) if fill["side"] == "BUY" else -int(fill["qty"])) if fill else None
                out.append(TxnRow(int(e["entry_id"]), int(e["ts"]), ny_of(int(e["ts"])).strftime("%Y-%m-%d %H:%M:%S"), e["business_date"], e["kind"], e["memo"], e["ref_type"], e["ref_id"],
                                  int(e["command_id"]), e["request_id"], inst, qty, fill["price"] if fill else None, int(fill["gross_c"]) if fill else None,
                                  int(fill["fee_c"]) if fill else None, fill["origin"] if fill else None, net, sorted({l["account_id"] for l in lines} & id_set),
                                  next((l["counterparty"] for l in lines if l["counterparty"]), None), lines))
            return out

    def obligations(self, account_id: str) -> ObligationsView:
        with self._conn() as conn:
            self._scope(conn, account_id)
            # C15 (P6-R1): a FAILED settlement is still PENDING; carry its fail columns (added by collateral.bind) when they exist, so the
            # account Cash & financing ladder reads "failed (simulated)" / "failed - retried" with the reason instead of "pending"
            scols = {r[1] for r in conn.execute("PRAGMA table_info(settlements)").fetchall()}
            extra = "".join(f", {c}" for c in ("fail_state", "fail_kind", "fail_reason") if c in scols)
            sett = [dict(r) for r in conn.execute(f"SELECT settle_id, fill_id, fx_deal_id, leg, currency, amount_c, gl, due_date{extra} FROM settlements WHERE account_id=? AND status='PENDING' ORDER BY due_date, settle_id", (account_id,)).fetchall()]
            pin: Dict[str, int] = {}
            pout: Dict[str, int] = {}
            for s in sett:
                if int(s["amount_c"]) > 0:
                    pin[s["currency"]] = pin.get(s["currency"], 0) + int(s["amount_c"])
                else:
                    pout[s["currency"]] = pout.get(s["currency"], 0) - int(s["amount_c"])
            borrows = [dict(r) for r in conn.execute("SELECT borrow_id, instrument_id, qty, qty_returned, fee_rate, rebate_rate, tier, lender, collateral_c, opened_date, reason, last_mark_date, recall_notice FROM borrows WHERE account_id=? AND status='OPEN'", (account_id,)).fetchall()]
            for b in borrows:                      # C16 / 15.7 item 8: the account Cash & financing borrows table shows the lender worded, the row stays as stored
                b["lender_stored"] = b.get("lender")
                b["lender"] = lender_label(b.get("lender"))
            loans = [dict(r) for r in conn.execute("SELECT loan_id, kind, currency, principal_c, index_name, spread, day_basis, opened_date, maturity_date, lender FROM loans WHERE account_id=? AND status='OPEN'", (account_id,)).fetchall()]
            b1300 = natural("1300", _sum(conn, "SELECT COALESCE(SUM(balance_c),0) FROM gl_balances WHERE account_id=? AND gl='1300'", (account_id,)))
            b1310 = natural("1310", _sum(conn, "SELECT COALESCE(SUM(balance_c),0) FROM gl_balances WHERE account_id=? AND gl='1310'", (account_id,)))
            ms = conn.execute("SELECT options_required_c, as_of_ts FROM margin_state WHERE account_id=?", (account_id,)).fetchone()
            fut_req = 0
            cache: dict = {}
            for r in conn.execute("SELECT v.instrument_id, v.qty FROM v_positions v JOIN instruments i ON i.instrument_id=v.instrument_id WHERE v.account_id=? AND i.kind='FUTURE'", (account_id,)).fetchall():
                inst = _instrument(conn, self.instruments, r["instrument_id"], cache)
                fut_req += futures_rates_c(conn, inst)[0] * abs(int(r["qty"])) if inst else 0
            margin = {"futures_required_c": fut_req, "futures_posted_c": b1300, "options_required_c": int(ms["options_required_c"]) if ms else 0, "options_posted_c": b1310,
                      # SR-13: the option requirement is reserved on the account's cash, never moved (1310 stays 0 after the migration)
                      "options_reserved_c": int(ms["options_required_c"]) if ms else 0, "options_margin_mode": "reserved (decision s7.6: no cash movement)",
                      "otc_im_c": natural("1330", _sum(conn, "SELECT COALESCE(SUM(balance_c),0) FROM gl_balances WHERE account_id=? AND gl='1330'", (account_id,))),
                      "otc_vm_posted_c": natural("1320", _sum(conn, "SELECT COALESCE(SUM(balance_c),0) FROM gl_balances WHERE account_id=? AND gl='1320'", (account_id,))),
                      "otc_vm_received_c": natural("2330", _sum(conn, "SELECT COALESCE(SUM(balance_c),0) FROM gl_balances WHERE account_id=? AND gl='2330'", (account_id,)))}
            coll = natural("1400", _sum(conn, "SELECT COALESCE(SUM(balance_c),0) FROM gl_balances WHERE account_id=? AND gl='1400'", (account_id,)))
            otc = [dict(r) for r in conn.execute("SELECT contract_id, kind, broker, notional_c, currency, maturity_date, margin_mode, im_c, mark_c, vm_balance_c, last_reset_date FROM otc_contracts WHERE account_id=? AND status='OPEN'", (account_id,)).fetchall()]
            return ObligationsView(account_id, sett, pin, pout, sett[0]["due_date"] if sett else None, borrows, loans, margin, coll, otc)

    def liquidity(self, scope: str, snapshot=None) -> LiquidityView:
        """§6.0: excess liquidity = Treasury available + Σ accounts max(0, available USD) − futures maintenance shortfalls;
        encumbrance = Σ base of 1400 + 1300 + 1310 + 1320 + 1330 (sentences EXCESS_LIQ, ENCUMBRANCE)."""
        snap = self._snapshot(snapshot)
        with self._conn() as conn:
            sc = self._scope(conn, scope)
            tre_avail = acct_avail = shortfall = enc = 0
            detail: Dict[str, Any] = {"accounts": {}, "treasury": {}, "maintenance": {}}
            cache: dict = {}
            for lid in sc.lifetime_ids:
                t = self.treasury_available_c(conn, lid, "USD", snap)
                tre_avail += t
                detail["treasury"][lid] = t
            for a in sc.accounts:
                aid = a["account_id"]
                if a["kind"] != "TREASURY":
                    v = max(0, available_c(conn, aid, "USD", self.view_date(), snap, self.instruments, self.clock.now()))
                    acct_avail += v
                    detail["accounts"][aid] = v
                for r in conn.execute("SELECT v.instrument_id, v.qty FROM v_positions v JOIN instruments i ON i.instrument_id=v.instrument_id WHERE v.account_id=? AND i.kind='FUTURE'", (aid,)).fetchall():
                    inst = _instrument(conn, self.instruments, r["instrument_id"], cache)
                    mm = futures_rates_c(conn, inst)[1] if inst else 0          # SR-11: the Settings override's maintenance, else the table
                    posted = natural("1300", _sum(conn, "SELECT COALESCE(SUM(balance_c),0) FROM gl_balances WHERE account_id=? AND gl='1300' AND instrument_id=?", (aid, r["instrument_id"])))
                    s = max(0, mm * abs(int(r["qty"])) - posted)
                    if s:
                        detail["maintenance"][f"{aid} {r['instrument_id']}"] = s
                    shortfall += s
                enc += sum(natural(gl, _sum(conn, "SELECT COALESCE(SUM(base_c),0) FROM gl_balances WHERE account_id=? AND gl=?", (aid, gl))) for gl in ENCUMBRANCE_GLS)
            return LiquidityView(sc.key, tre_avail + acct_avail - shortfall, enc, tre_avail, acct_avail, shortfall,
                                 {"excess_liquidity": tables.CONVENTIONS["EXCESS_LIQ"], "encumbrance": tables.CONVENTIONS["ENCUMBRANCE"]}, detail)

    # ---- integrity -----------------------------------------------------------------------------------------------------
    def integrity(self, mode: str = "full", touched: Optional[Touched] = None, snapshot=None, lifetime_id: Optional[str] = None) -> IntegrityReport:
        """``scoped`` (ledger-level on the touched keys, inside a command), ``ledger`` (whole DB, every OPEN lifetime or
        the given one), ``full`` (+ the mark-dependent WARN set M1–M5).  Whole-DB runs record ``meta.integrity_json`` and
        the per-lifetime INTEGRITY_HOLD (an L failure holds new submissions of that lifetime only)."""
        t0 = _time.monotonic()
        today = self.clock.today()
        owned = self.db.writer_owned_by_current_thread()
        runs: List[dict] = []
        if mode == "scoped":
            with self._conn() as conn:
                problems = ledger_checks(conn, today, self.cal, accounts=set(touched.accounts) if touched else set(), entries=list(touched.entries) if touched else [], scoped=True,
                                         runs=runs)
            return IntegrityReport(not any(p.severity == "ERROR" for p in problems), False, self.clock.epoch(), mode, lifetime_id, problems, int((_time.monotonic() - t0) * 1000),
                                   checks=runs)
        with self._conn() as conn:
            if lifetime_id:
                row = conn.execute("SELECT status FROM lifetimes WHERE lifetime_id=?", (lifetime_id,)).fetchone()
                if row is None:
                    raise ScopeError(f"unknown lifetime {lifetime_id}")
                lids, closed = ([lifetime_id], []) if row["status"] == "OPEN" else ([], [lifetime_id])
            else:
                lids = open_lifetimes(conn)
                closed = [r["lifetime_id"] for r in conn.execute("SELECT lifetime_id FROM lifetimes WHERE status='CLOSED'").fetchall()]
            problems = ledger_checks(conn, today, self.cal, lifetimes=lids + closed, closed_lifetimes=closed, runs=runs)
            if mode == "full":
                marks = self._mark_checks(conn, lids, snapshot)
                problems.extend(marks)
                for code, name in (("M1", "the P&L explain ties (no residual)"), ("M2", "assets - liabilities = NAV"), ("M3", "the Treasury identity holds"),
                                   ("M4", "the daily snapshots chain without a break"), ("M5", "a snapshot every calendar day"), ("M0", "the mark-dependent checks ran")):
                    got = [p for p in marks if p.id == code]
                    if got or code != "M0":
                        runs.append({"code": code, "name": name, "ok": not any(p.severity == "ERROR" for p in got), "count": len(got),
                                     "errors": sum(1 for p in got if p.severity == "ERROR"), "warnings": sum(1 for p in got if p.severity == "WARN"),
                                     "message": "ok" if not got else "; ".join(f"{p.scope}: {p.detail}" for p in got[:3]) + (f" (+{len(got) - 3} more)" if len(got) > 3 else "")})
        holds = {lid: any(p.severity == "ERROR" and (p.lifetime_id in (lid, None)) for p in problems) for lid in lids}
        for lid in closed:
            holds[lid] = any(p.severity == "ERROR" and p.lifetime_id == lid for p in problems)
        ok = not any(p.severity == "ERROR" for p in problems)
        report = IntegrityReport(ok, any(holds.values()), self.clock.epoch(), mode, lifetime_id, problems, int((_time.monotonic() - t0) * 1000), holds,
                                 sum(1 for p in problems if p.severity == "WARN"), runs)
        self._record(report, owned)
        return report

    def _mark_checks(self, conn: sqlite3.Connection, lids: List[str], snapshot) -> List[Problem]:
        snap = self._snapshot(snapshot)
        out: List[Problem] = []
        try:
            scopes = []
            for lid in lids:
                scopes.append(f"{lid}:ALL")
                scopes.extend(a["account_id"] for a in self._accounts_of(conn, [lid]))
            for key in scopes:
                sc = self._scope(conn, key)
                comp = self._components(conn, sc, snap)
                lid = sc.lifetime_ids[0] if sc.lifetime_ids else None
                if comp["partial"]:
                    out.append(Problem("M1", "WARN", key, 0, None, "skipped: NAV is partial (unpriced positions or missing FX)", lid))
                else:
                    for period in ("TODAY", "ITD"):
                        v = self.pnl(key, period, snap)
                        if v.residual_c != 0:
                            out.append(Problem("M1", "WARN", f"{key} {period}", 0, v.residual_c, "P&L explain residual", lid))
                bs = self.balance_sheet(key, snap)
                foreign = len({ccy for a in sc.accounts for (gl, ccy) in self._account_calc(conn, a, snap).gl if ccy != "USD"})
                if abs(bs.rounding_c) > foreign:
                    out.append(Problem("M2", "WARN", key, bs.nav_c, bs.assets_c - bs.liabilities_c, "assets − liabilities ≠ NAV beyond FX rounding", lid))
            for lid in lids:
                t = self.treasury(lid, snap)
                if not t.identity_ok:
                    out.append(Problem("M3", "WARN", f"{lid}:TREASURY", t.capital_c + t.retained_c - t.advances_c, t.nav_c, "Treasury identity", lid))
                adv_sum = sum(t.advances_by_account.values())
                if adv_sum != t.advances_c:
                    out.append(Problem("M3", "WARN", f"{lid}:TREASURY", t.advances_c, adv_sum, "Σ accounts' 3100 ≠ Treasury advances", lid))
                rows = snapshot_chain(conn, f"{lid}:ALL")          # C9i: the latest-version chain (a restated row chains to the next live one)
                prev = None
                for r in rows:
                    if prev is not None:
                        if int(r["nav_c"]) - int(prev["nav_c"]) - int(r["flows_day_c"]) != int(r["pnl_day_c"]):
                            out.append(Problem("M4", "WARN", f"{lid}:ALL {r['snap_date']}", int(r["pnl_day_c"]), int(r["nav_c"]) - int(prev["nav_c"]) - int(r["flows_day_c"]), "snapshot continuity", lid))
                        d0, d1 = date.fromisoformat(prev["snap_date"]), date.fromisoformat(r["snap_date"])
                        if (d1 - d0).days > 1:
                            out.append(Problem("M5", "WARN", f"{lid}:ALL", "a snapshot every calendar day", f"gap {d0} -> {d1}", "snapshot chain gap", lid))
                    prev = r
        except Exception as e:   # noqa: BLE001 — a crashing mark check is reported, never hidden
            out.append(Problem("M0", "WARN", "mark checks", None, None, f"mark-dependent checks raised {type(e).__name__}: {e}"))
        return out

    def _record(self, report: IntegrityReport, owned: bool) -> None:
        payload = dumps(report.to_dict())
        hold_json = dumps({lid: {"since": report.checked_at, "failing": sorted({p.id for p in report.problems if p.severity == 'ERROR' and p.lifetime_id in (lid, None)})}
                           for lid, h in report.holds.items() if h})

        def write(conn: sqlite3.Connection) -> None:
            hold = hold_json
            if report.lifetime_id:                     # a run limited to one lifetime re-decides that lifetime's hold only
                try:
                    prev = json.loads(get_meta(conn, "integrity_hold_json", "{}") or "{}")
                except ValueError:
                    prev = {}
                prev = {k: v for k, v in (prev or {}).items() if k not in report.holds}
                prev.update(json.loads(hold_json))
                hold = dumps(prev)
            else:
                set_meta(conn, "integrity_json", payload)
                set_meta(conn, "integrity_ts", str(report.checked_at))
            set_meta(conn, "integrity_hold_json", hold)
            for lid, held in report.holds.items():
                open_row = conn.execute("SELECT id FROM attention WHERE source='integrity' AND code='INTEGRITY' AND ref_type='lifetime' AND ref_id=? AND resolved_ts IS NULL", (lid,)).fetchone()
                if held and open_row is None:
                    failing = sorted({p.id for p in report.problems if p.severity == "ERROR" and p.lifetime_id in (lid, None)})
                    conn.execute("INSERT INTO attention(ts, severity, source, code, message, details_json, account_id, ref_type, ref_id, resolved_ts, auto_resolve) VALUES (?,?,?,?,?,?,?,?,?,NULL,1)",
                                 (report.checked_at, "ERROR", "integrity", "INTEGRITY", f"{lid}: ledger integrity failed ({', '.join(failing)}); new orders are held until the next clean run",
                                  dumps({"lifetime_id": lid, "failing": failing, "mode": report.mode}), None, "lifetime", lid))
                elif not held and open_row is not None:
                    conn.execute("UPDATE attention SET resolved_ts=? WHERE id=?", (report.checked_at, int(open_row["id"])))
        try:
            if owned:
                write(self.db.writer_conn())
            else:
                with self.db.writer(timeout=5.0) as tx:
                    write(tx.conn)
        except Exception as e:   # noqa: BLE001 — the report is still returned; the failure to persist it is logged by the caller via the note
            report.problems.append(Problem("M0", "WARN", "meta", None, None, f"could not record the report: {e}"))

    def hold_state(self) -> Dict[str, dict]:
        with self._conn() as conn:
            try:
                return json.loads(get_meta(conn, "integrity_hold_json", "{}") or "{}")
            except ValueError:
                return {}

    def is_held(self, lifetime_id: str) -> bool:
        return lifetime_id in self.hold_state()

    def last_report(self) -> Optional[dict]:
        with self._conn() as conn:
            raw = get_meta(conn, "integrity_json")
            try:
                return json.loads(raw) if raw else None
            except ValueError:
                return None


def integrity_scoped(ctx) -> IntegrityReport:
    """The CommandBus hook (§4 step 7): the ledger-level set on the keys this command touched, on the open transaction."""
    t0 = _time.monotonic()
    touched = ctx.touched
    problems = ledger_checks(ctx.conn, accounting_day(ctx.now), ctx.cal, accounts=set(touched.accounts), entries=list(touched.entries), scoped=True)
    return IntegrityReport(not any(p.severity == "ERROR" for p in problems), False, ctx.ts, "scoped", None, problems, int((_time.monotonic() - t0) * 1000))
